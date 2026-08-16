import re

from app.core.ids import ResourceType, stable_id
from app.core.model_client import CompatibleJSONClient, ModelClientError
from app.domain.models import CheckItem, RetrievalFilters, RetrievalPlan, UnitType
from app.retrieval.models import (
    EvidenceAssessment,
    QuestionAnalysis,
    RetrievalGoal,
    RetrievalGoalStatus,
)
from app.retrieval.planner import DOMAIN_SYNONYMS
from app.retrieval.service import QueryAnalyzer


class AgenticRetrievalPlanner:
    """为知识查询和合规检查项生成统一、可审计的动态检索计划。"""

    MAX_ATTEMPTS = 3
    MAX_SUBQUERIES = 5

    def __init__(self, client: CompatibleJSONClient | None = None) -> None:
        self.client = client
        self.analyzer = QueryAnalyzer()

    def analyze_question(self, question: str) -> QuestionAnalysis:
        if self.client and hasattr(self.client, "complete"):
            try:
                prompt = (
                    "拆分问题为最多5个可验证检索目标。原样保留标准号、条款号和表号；"
                    "仅输出目标描述、标识符和建议术语，不输出思维链。问题：" + question
                )
                generated = self.client.complete(prompt, QuestionAnalysis)
                return self._enforce_identifiers(question, generated)
            except ModelClientError:
                pass
        return self._deterministic_analysis(question)

    def create(
        self,
        question: str,
        analysis: QuestionAnalysis,
        attempt: int,
        previous: EvidenceAssessment | None,
        history: list[str],
        top_k: int = 5,
    ) -> RetrievalPlan:
        if attempt >= self.MAX_ATTEMPTS:
            raise ValueError("达到最大检索轮数")
        parsed = self.analyzer.analyze(question)
        exact = analysis.exact_identifiers
        missing = {
            item.goal_id
            for item in (previous.goal_assessments if previous else [])
            if item.status not in {RetrievalGoalStatus.SUPPORTED}
        }
        goals = [goal for goal in analysis.goals if not missing or goal.goal_id in missing]
        if exact:
            queries = [question]
            retrievers = ["exact"]
            reason = "explicit_identifier_exact_only"
        else:
            queries = [goal.description for goal in goals]
            if attempt:
                queries = [self._expand(query, attempt) for query in queries]
            if parsed.route_type in {"numeric", "keyword"}:
                # 数值参数和专业关键词查询优先 BM25，避免 Vector 相似语义干扰。
                retrievers = ["bm25"] if attempt != 2 else ["vector", "bm25"]
            else:
                retrievers = ["bm25", "vector"] if attempt != 1 else ["bm25"]
            reason = "initial_goal_search" if attempt == 0 else "missing_goal_retry"
        queries = self._deduplicate(queries[: self.MAX_SUBQUERIES], history, attempt)
        unit_types: list[UnitType] = []
        if parsed.table_numbers:
            unit_types = [UnitType.TABLE, UnitType.TABLE_ROW]
        elif parsed.clause_numbers:
            unit_types = [UnitType.CLAUSE]
        return RetrievalPlan(
            intent=parsed.intent,
            query=queries[0],
            subqueries=queries,
            exact_keys=exact,
            filters=RetrievalFilters(standard_codes=parsed.standard_codes, unit_types=unit_types),
            retrievers=retrievers,
            top_k=top_k,
            expansion_policy="parent_and_neighbors",
            rewrite_reason=reason,
            attempt=attempt,
        )

    def create_for_compliance(
        self,
        original_query: str,
        check_items: list[CheckItem],
        attempt: int,
        previous: EvidenceAssessment | None,
        history: list[str],
        top_k: int = 5,
    ) -> RetrievalPlan:
        """使用与问答相同的动态选路策略，并保持子查询到检查项的一一绑定。"""
        if attempt >= self.MAX_ATTEMPTS:
            raise ValueError("达到最大检索轮数")
        parsed = self.analyzer.analyze(original_query)
        exact = parsed.clause_numbers + parsed.table_numbers
        missing = set(previous.unsupported_check_item_ids if previous else [])
        selected = [item for item in check_items if not missing or item.item_id in missing]
        if exact:
            queries = [original_query]
            item_ids = [""]
            retrievers = ["exact"]
            reason = "explicit_identifier_exact_only"
        else:
            queries = [
                " ".join(filter(None, (item.object, item.attribute, item.condition)))
                for item in selected
            ]
            item_ids = [item.item_id for item in selected]
            if attempt:
                queries = [self._expand(query, attempt) for query in queries]
            if parsed.route_type in {"numeric", "keyword"}:
                retrievers = ["bm25"] if attempt != 2 else ["vector", "bm25"]
            else:
                retrievers = ["bm25", "vector"] if attempt != 1 else ["bm25"]
            reason = "check_item_search" if attempt == 0 else "missing_item_retry"
        queries = self._deduplicate(queries[: self.MAX_SUBQUERIES], history, attempt)
        item_ids = item_ids[: len(queries)]
        return RetrievalPlan(
            intent=parsed.intent,
            query=queries[0],
            subqueries=queries,
            subquery_item_ids=item_ids,
            exact_keys=exact,
            filters=RetrievalFilters(standard_codes=parsed.standard_codes),
            retrievers=retrievers,
            top_k=top_k,
            expansion_policy="parent_and_neighbors",
            rewrite_reason=reason,
            attempt=attempt,
        )

    def _deterministic_analysis(self, question: str) -> QuestionAnalysis:
        parsed = self.analyzer.analyze(question)
        identifiers = parsed.clause_numbers + parsed.table_numbers
        parts = [
            part.strip(" ，。？?；;")
            for part in re.split(
                r"[，,；;]|(?:以及|并且|同时|和(?=.{2,}(?:是否|要求|规定)))", question
            )
            if part.strip(" ，。？?；;")
        ][: self.MAX_SUBQUERIES]
        if identifiers:
            parts = [question]
        goals = [
            RetrievalGoal(
                goal_id=stable_id(ResourceType.CHECK_ITEM, "retrieval_goal", index, part),
                description=part,
                required_identifiers=identifiers,
                suggested_terms=[term for term in DOMAIN_SYNONYMS if term in part],
            )
            for index, part in enumerate(parts or [question])
        ]
        return QuestionAnalysis(
            complexity="complex" if len(goals) > 1 else "simple",
            goals=goals,
            exact_identifiers=identifiers,
            requires_multi_step=len(goals) > 1,
        )

    def _enforce_identifiers(self, question: str, generated: QuestionAnalysis) -> QuestionAnalysis:
        parsed = self.analyzer.analyze(question)
        exact = parsed.clause_numbers + parsed.table_numbers
        if not exact:
            return generated
        goal = generated.goals[0].model_copy(
            update={"description": question, "required_identifiers": exact}
        )
        return QuestionAnalysis(
            complexity="simple", goals=[goal], exact_identifiers=exact, requires_multi_step=False
        )

    @staticmethod
    def _expand(query: str, attempt: int) -> str:
        additions = [
            synonym
            for term, synonyms in DOMAIN_SYNONYMS.items()
            if term.casefold() in query.casefold()
            for synonym in synonyms
        ]
        suffix = " 规范要求" if attempt > 1 else ""
        return " ".join(dict.fromkeys([query, *additions])) + suffix

    @staticmethod
    def _deduplicate(queries: list[str], history: list[str], attempt: int) -> list[str]:
        seen = {re.sub(r"\W+", "", item).casefold() for item in history}
        output: list[str] = []
        for query in queries:
            candidate = query
            normalized = re.sub(r"\W+", "", candidate).casefold()
            if normalized in seen:
                candidate = f"{candidate} {'适用条件' if attempt == 1 else '技术要求'}"
                normalized = re.sub(r"\W+", "", candidate).casefold()
            if normalized not in seen:
                output.append(candidate)
                seen.add(normalized)
        if not output:
            raise ValueError("未能生成与历史不同的查询")
        return output
