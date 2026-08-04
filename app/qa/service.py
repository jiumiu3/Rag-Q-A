import json
import re
from typing import Any

from pydantic import ValidationError

from app.core.model_client import CompatibleJSONClient, ModelClientError
from app.domain.models import Evidence, EvidenceStatus, IntentType, SourceCitation
from app.qa.models import AnswerClaim, AnswerDraft, RAGGeneratedAnswer, RetrievalToolCall
from app.retrieval.models import RetrievalResult
from app.retrieval.service import QueryAnalyzer, RetrievalService

NUMERIC_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*(?:%|MPa|kPa|℃|mm|m|s|h)?", re.I)
REFERENCE_RE = re.compile(
    r"Q\s*[/.-]?\s*GGW\s*\d+(?:\.\d+)?(?:\s*[-—]\s*\d{4})?"
    r"|第\s*\d+(?:\.\d+)*\s*(?:条|页)"
    r"|\[\d+\]",
    re.I,
)


class CitationValidator:
    """验证引用字段、claim 绑定和数值均来自当前 Evidence。"""

    def validate(self, draft: AnswerDraft, evidence: list[Evidence]) -> list[str]:
        errors: list[str] = []
        by_id = {item.evidence_id: item for item in evidence}
        for claim in draft.claims:
            bound = [by_id[key] for key in claim.evidence_ids if key in by_id]
            if len(bound) != len(claim.evidence_ids):
                errors.append("claim 引用了不存在的 evidence_id")
                continue
            source_text = " ".join(item.content for item in bound)
            reference_spans = [
                range(match.start(), match.end()) for match in REFERENCE_RE.finditer(claim.text)
            ]
            for match in NUMERIC_RE.finditer(claim.text):
                if any(match.start() in span for span in reference_spans):
                    continue
                number = match.group()
                if number not in source_text:
                    errors.append(f"回答数值未出现在绑定证据中：{number}")
        valid_citations = {
            (
                item.citation.standard_code,
                item.citation.clause_id,
                item.citation.table_id,
                item.citation.page_number,
                item.citation.quote,
            )
            for item in evidence
        }
        for citation in draft.citations:
            key = (
                citation.standard_code,
                citation.clause_id,
                citation.table_id,
                citation.page_number,
                citation.quote,
            )
            if key not in valid_citations:
                errors.append("引用字段或摘录不属于当前 Evidence")
        return errors


class QueryRouter:
    def __init__(self) -> None:
        self.analyzer = QueryAnalyzer()

    def route(self, question: str) -> IntentType:
        return self.analyzer.analyze(question).intent


class EvidenceAnswerer:
    """首版采用可校验的抽取式回答，不使用模型常识补写规范要求。"""

    def answer(self, _question: str, result: RetrievalResult) -> AnswerDraft:
        intent = result.trace.query_analysis.intent
        if not result.evidence:
            return self._degraded(
                intent, EvidenceStatus.NOT_FOUND, "未检索到可支持回答的规范证据。"
            )
        if intent == IntentType.TABLE_LOOKUP:
            table_evidence = [item for item in result.evidence if item.citation.table_id]
            requested_tables = set(result.trace.query_analysis.table_numbers)
            exact_tables = [
                item for item in table_evidence if item.citation.table_id in requested_tables
            ]
            if exact_tables:
                table_evidence = exact_tables
            elif requested_tables:
                return self._degraded(
                    intent,
                    EvidenceStatus.NOT_FOUND,
                    "未找到与请求表号精确匹配的表格证据。",
                )
            if not table_evidence or any(
                item.support_type == EvidenceStatus.PARTIAL for item in table_evidence
            ):
                return self._degraded(
                    intent,
                    EvidenceStatus.PARTIAL,
                    "已定位到相关表格，但该表尚未完成可靠的行列结构化，不能自动返回参数值。",
                    ["请根据引用页图像人工复核；后续可接入多模态表格解析。"],
                    [item.citation for item in table_evidence],
                )
        primary = [item for item in result.evidence if item.context_reason is None]
        if intent == IntentType.CLAUSE_LOOKUP:
            requested_clauses = set(result.trace.query_analysis.clause_numbers)
            exact_clauses = [
                item for item in primary if item.citation.clause_id in requested_clauses
            ]
            if exact_clauses:
                primary = exact_clauses
            elif requested_clauses:
                return self._degraded(
                    intent,
                    EvidenceStatus.NOT_FOUND,
                    "未找到与请求条款号精确匹配的规范证据。",
                )
        if not primary:
            return self._degraded(
                intent, EvidenceStatus.NOT_FOUND, "仅找到上下文，未找到直接证据。"
            )
        # 每条结论直接摘取证据原文，保证引用验证和数值一致性可以确定执行。
        claims = [
            AnswerClaim(text=item.citation.quote, evidence_ids=[item.evidence_id])
            for item in primary[:3]
        ]
        return AnswerDraft(
            intent=intent,
            status=EvidenceStatus.SUFFICIENT,
            conclusion="根据检索到的规范原文，可得到以下内容：",
            claims=claims,
            citations=[item.citation for item in primary[:3]],
            limitations=["当前为抽取式回答；结论不构成完整设计合规判定。"],
        )

    @staticmethod
    def _degraded(
        intent: IntentType,
        status: EvidenceStatus,
        conclusion: str,
        limitations: list[str] | None = None,
        citations: list[SourceCitation] | None = None,
        tool_calls: list[RetrievalToolCall] | None = None,
    ) -> AnswerDraft:
        return AnswerDraft(
            intent=intent,
            status=status,
            conclusion=conclusion,
            limitations=limitations or ["证据不足时系统不会使用模型常识生成规范结论。"],
            citations=citations or [],
            tool_calls=tool_calls or [],
        )


class RAGAgentAnswerer:
    """让聊天模型调用知识检索工具，再严格依据返回 Evidence 组织回答。"""

    TOOL = {
        "type": "function",
        "function": {
            "name": "knowledge_search",
            "description": "检索油气管网仪表与自动控制规范，返回可引用的条款或表格证据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "保留标准号和条款号的检索问题"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }

    def __init__(self, client: CompatibleJSONClient, retrieval: RetrievalService) -> None:
        self.client = client
        self.retrieval = retrieval

    def answer(self, question: str, top_k: int) -> tuple[AnswerDraft, RetrievalResult]:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是油气管网规范问答 Agent。回答前必须调用 knowledge_search。"
                    "检索问题必须保留用户提供的标准号、条款号和表号。"
                    "不得使用模型记忆补充规范要求。"
                ),
            },
            {"role": "user", "content": question},
        ]
        tool_message = self.client.chat(
            messages,
            tools=[self.TOOL],
            # 当前兼容服务的 thinking mode 只接受 auto；下方仍会硬校验必须产生工具调用。
            tool_choice="auto",
        )
        calls = tool_message.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            raise ModelClientError("模型没有按要求调用 knowledge_search")
        call = calls[0]
        try:
            arguments = json.loads(call["function"]["arguments"])
            query = str(arguments["query"]).strip()
            requested_top_k = min(max(int(arguments.get("top_k", top_k)), 1), 10)
            call_id = str(call["id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelClientError("knowledge_search 工具参数无效") from exc
        if not query:
            raise ModelClientError("knowledge_search 查询不能为空")
        result = self.retrieval.retrieve(query, requested_top_k)
        primary = [item for item in result.evidence if item.context_reason is None][:10]
        invocation = RetrievalToolCall(
            query=query,
            top_k=requested_top_k,
            result_count=len(primary),
        )
        base = EvidenceAnswerer().answer(question, result)
        # 表格未结构化、精确编号未命中等情况禁止交给模型补写。
        if base.status != EvidenceStatus.SUFFICIENT:
            return (
                AnswerDraft.model_validate(
                    {**base.model_dump(exclude={"answer_text"}), "tool_calls": [invocation]}
                ),
                result,
            )
        evidence_payload = [
            {
                "evidence_id": item.evidence_id,
                "content": item.content,
                "standard_code": item.citation.standard_code,
                "clause_id": item.citation.clause_id,
                "table_id": item.citation.table_id,
                "page_number": item.citation.page_number,
            }
            for item in primary
        ]
        messages.extend(
            [
                tool_message,
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": "knowledge_search",
                    "content": json.dumps(evidence_payload, ensure_ascii=False),
                },
                {
                    "role": "system",
                    "content": (
                        "仅依据工具证据组织简体中文回答。每条 claim 必须绑定一个或多个真实 "
                        "evidence_id；不得编造编号、数值或来源。输出符合此 JSON Schema："
                        + json.dumps(RAGGeneratedAnswer.model_json_schema(), ensure_ascii=False)
                    ),
                },
            ]
        )
        try:
            final_message = self.client.chat(
                messages,
                tools=[self.TOOL],
                tool_choice="none",
                response_format={"type": "json_object"},
            )
            generated = RAGGeneratedAnswer.model_validate_json(final_message["content"])
            by_id = {item.evidence_id: item for item in primary}
            used_ids: list[str] = []
            for claim in generated.claims:
                if not set(claim.evidence_ids).issubset(by_id):
                    raise ModelClientError("RAG Agent 引用了工具范围之外的 Evidence")
                used_ids.extend(claim.evidence_ids)
        except (ModelClientError, KeyError, TypeError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                error_type = str(exc.errors()[0].get("type", "validation_error"))
                reason = f"结构化校验失败（{error_type}）"
            elif isinstance(exc, ModelClientError):
                reason = exc.message
            else:
                reason = f"结构化响应缺少字段（{type(exc).__name__}）"
            payload = base.model_dump(exclude={"answer_text"})
            payload["tool_calls"] = [invocation]
            payload["limitations"] = base.limitations + [
                f"模型已调用检索工具，但回答生成失败，已降级为抽取式回答：{reason}"
            ]
            return AnswerDraft.model_validate(payload), result
        citations = [by_id[key].citation for key in dict.fromkeys(used_ids)]
        return (
            AnswerDraft(
                intent=QueryRouter().route(question),
                status=EvidenceStatus.SUFFICIENT,
                conclusion=generated.conclusion,
                claims=generated.claims,
                citations=citations,
                limitations=generated.limitations
                + ["回答由 RAG Agent 基于 knowledge_search 返回证据生成。"],
                tool_calls=[invocation],
            ),
            result,
        )


class QAService:
    def __init__(
        self, retrieval: RetrievalService, rag_answerer: RAGAgentAnswerer | None = None
    ) -> None:
        self.retrieval = retrieval
        self.answerer = EvidenceAnswerer()
        self.rag_answerer = rag_answerer
        self.validator = CitationValidator()

    def ask(self, question: str, top_k: int = 5) -> tuple[AnswerDraft, RetrievalResult]:
        fallback_reason: str | None = None
        if self.rag_answerer:
            try:
                draft, result = self.rag_answerer.answer(question, top_k)
            except ModelClientError as exc:
                fallback_reason = exc.message
                result = self.retrieval.retrieve(question, top_k)
                draft = self.answerer.answer(question, result)
        else:
            result = self.retrieval.retrieve(question, top_k)
            draft = self.answerer.answer(question, result)
        if fallback_reason:
            draft.limitations.append(f"模型工具调用失败，已降级为抽取式回答：{fallback_reason}")
            draft = AnswerDraft.model_validate(draft.model_dump(exclude={"answer_text"}))
        errors = self.validator.validate(draft, result.evidence)
        if errors:
            draft = EvidenceAnswerer._degraded(
                result.trace.query_analysis.intent,
                EvidenceStatus.PARTIAL,
                "候选答案未通过引用一致性校验，已阻止返回未经验证的内容。",
                errors,
                citations=draft.citations,
                tool_calls=draft.tool_calls,
            )
        return draft, result
