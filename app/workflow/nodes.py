import re

from app.clarification.service import ClarificationPlanner, CompletenessChecker
from app.compliance.evaluator import RuleEvaluator
from app.compliance.models import ManualReviewRule
from app.design.parser import DesignDescriptionParser
from app.domain.models import (
    CheckItemStatus,
    ComplianceResult,
    ComplianceStatus,
    IntentType,
    RequirementLevel,
)
from app.qa.service import QAService
from app.retrieval.service import QueryAnalyzer, RetrievalService
from app.workflow.models import AgentState, PendingAction, WorkflowStatus


class WorkflowNodes:
    """每个节点只返回局部更新；状态合并由图执行器负责。"""

    def __init__(self, qa: QAService, retrieval: RetrievalService) -> None:
        self.qa = qa
        self.retrieval = retrieval
        self.parser = DesignDescriptionParser()
        self.completeness = CompletenessChecker()
        self.clarifications = ClarificationPlanner()
        self.evaluator = RuleEvaluator()
        self.query_analyzer = QueryAnalyzer()

    def normalize_input(self, state: AgentState) -> dict[str, object]:
        normalized = re.sub(r"\s+", " ", state.input_text).strip()
        return {"normalized_input": normalized, "status": WorkflowStatus.RUNNING}

    def classify_intent(self, state: AgentState) -> dict[str, object]:
        text = state.normalized_input
        if any(word in text for word in ("审查", "合规", "是否符合", "设计描述")):
            intent = IntentType.COMPLIANCE_REVIEW
        elif any(word in text for word in ("检查清单", "检查项", "清单")):
            intent = IntentType.CHECKLIST_GENERATION
        else:
            intent = self.query_analyzer.analyze(text).intent
        return {"intent": intent}

    def identify_scenario(self, state: AgentState) -> dict[str, object]:
        preview = self.parser.parse(state.normalized_input)
        scenario = {
            key: value for key, value in preview.context.model_dump().items() if value is not None
        }
        return {"scenario": scenario}

    def extract_check_items(self, state: AgentState) -> dict[str, object]:
        preview = self.parser.parse(state.normalized_input)
        items = preview.items[:20]
        if not items:
            return {
                "status": WorkflowStatus.SAFE_STOPPED,
                "errors": state.errors + ["未能从设计描述提取检查项"],
            }
        return {"check_items": items}

    def confirm(self, state: AgentState) -> dict[str, object]:
        if any(item.status == CheckItemStatus.DRAFT for item in state.check_items):
            return {
                "status": WorkflowStatus.WAITING_CONFIRMATION,
                "pending_action": PendingAction.CONFIRM_CHECK_ITEMS,
            }
        return {"pending_action": None, "status": WorkflowStatus.RUNNING}

    def check_completeness(self, state: AgentState) -> dict[str, object]:
        result = self.completeness.check(state.check_items, state.evidence)
        items = result.ready_items + result.incomplete_items
        if result.missing_fields:
            if state.clarification_count >= 2:
                results = [
                    ComplianceResult(
                        item_id=item.item_id,
                        status=ComplianceStatus.INSUFFICIENT_INFORMATION,
                        limitations=["达到最大追问轮次，仍缺少判断条件"],
                    )
                    for item in result.incomplete_items
                ]
                return {
                    "check_items": items,
                    "compliance_results": results,
                    "status": WorkflowStatus.SAFE_STOPPED,
                    "pending_action": None,
                }
            request = self.clarifications.plan(result.missing_fields, state.session_id)
            return {
                "check_items": items,
                "clarification": request,
                "status": WorkflowStatus.WAITING_CLARIFICATION,
                "pending_action": PendingAction.ANSWER_CLARIFICATION,
            }
        return {
            "check_items": items,
            "clarification": None,
            "pending_action": None,
            "status": WorkflowStatus.RUNNING,
        }

    def plan_retrieval(self, _state: AgentState) -> dict[str, object]:
        return {}

    def retrieve(self, state: AgentState) -> dict[str, object]:
        evidence = []
        seen: set[str] = set()
        queries = (
            [f"{item.object} {item.attribute} {item.condition or ''}" for item in state.check_items]
            if state.check_items
            else [state.normalized_input]
        )
        for query in queries:
            for item in self.retrieval.retrieve(query, 3).evidence:
                if item.evidence_id not in seen:
                    evidence.append(item)
                    seen.add(item.evidence_id)
        return {"evidence": evidence}

    def evaluate_evidence(self, state: AgentState) -> dict[str, object]:
        if state.evidence:
            return {}
        if state.retrieval_retry_count < 2:
            return {"retrieval_retry_count": state.retrieval_retry_count + 1}
        return {
            "status": WorkflowStatus.SAFE_STOPPED,
            "errors": state.errors + ["达到检索重试上限，未找到规范证据"],
        }

    def extract_rule(self, state: AgentState) -> dict[str, object]:
        if state.rules:
            return {}
        evidence_ids = [item.evidence_id for item in state.evidence]
        if not evidence_ids:
            return {}
        # 未经人工确认或结构化模型校验的证据不能自动变成确定性规则。
        rules = [
            ManualReviewRule(
                rule_id=f"manual_{item.item_id}",
                subject=item.object,
                attribute=item.attribute,
                reason="尚未建立并人工确认对应生产规则",
                requirement_level=RequirementLevel.SHALL,
                evidence_ids=evidence_ids[:3],
            )
            for item in state.check_items
        ]
        return {"rules": rules}

    def evaluate_compliance(self, state: AgentState) -> dict[str, object]:
        results = [
            self.evaluator.evaluate(item, rule)
            for item, rule in zip(state.check_items, state.rules, strict=False)
        ]
        return {"compliance_results": results}

    def verify_claims(self, state: AgentState) -> dict[str, object]:
        evidence_ids = {item.evidence_id for item in state.evidence}
        invalid = [
            result.item_id
            for result in state.compliance_results
            if not set(result.evidence_ids).issubset(evidence_ids)
        ]
        if invalid and state.validation_retry_count < 1:
            return {"validation_retry_count": state.validation_retry_count + 1}
        if invalid:
            return {
                "status": WorkflowStatus.SAFE_STOPPED,
                "errors": state.errors + [f"结果引用验证失败：{invalid}"],
            }
        return {}

    def generate_report(self, state: AgentState) -> dict[str, object]:
        lines = ["# 规范预审报告", "", f"会话：`{state.session_id}`", "", "## 结果"]
        if state.answer:
            lines.extend(["", state.answer.answer_text])
        for item, result in zip(state.check_items, state.compliance_results, strict=False):
            lines.extend(
                [
                    "",
                    f"### {item.object} / {item.attribute}",
                    f"- 状态：{result.status}",
                    f"- 实际值：{result.actual or item.value or '未提供'} {item.unit or ''}",
                    f"- 规则要求：{result.required or '未形成确定性规则'}",
                    f"- 限制：{'；'.join(result.limitations) or '无'}",
                ]
            )
        # 问答的 answer_text 已在末尾附来源，避免报告重复；其他任务也必须以来源结尾。
        if state.evidence and not state.answer:
            lines.extend(["", "## 证据来源"])
            for evidence in state.evidence[:10]:
                citation = evidence.citation
                lines.append(
                    f"- {citation.standard_code}，条款 {citation.clause_id or '-'}，"
                    f"第 {citation.page_number} 页：{citation.quote[:120]}"
                )
        elif not state.answer:
            lines.extend(["", "## 证据来源", "", "无可用规范证据。"])
        return {
            "report_markdown": "\n".join(lines),
            "status": WorkflowStatus.COMPLETED,
            "pending_action": None,
        }

    def answer_query(self, state: AgentState) -> dict[str, object]:
        answer, result = self.qa.ask(state.normalized_input)
        return {"answer": answer, "evidence": result.evidence}

    def generate_checklist(self, state: AgentState) -> dict[str, object]:
        preview = self.parser.parse(state.normalized_input, auto_confirm=True)
        return {"check_items": preview.items}

    def out_of_scope(self, state: AgentState) -> dict[str, object]:
        return {
            "status": WorkflowStatus.SAFE_STOPPED,
            "errors": state.errors + ["请求超出当前三份油气管网规范范围"],
        }
