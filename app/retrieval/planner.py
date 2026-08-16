import re

from app.domain.models import CheckItem, RetrievalFilters, RetrievalPlan, UnitType
from app.retrieval.models import EvidenceAssessment
from app.retrieval.service import QueryAnalyzer

DOMAIN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "UPS": ("不间断电源", "备用电源"),
    "不间断电源": ("UPS", "备用电源"),
    "供电时间": ("后备时间", "持续供电时间", "续航"),
    "持续供电时间": ("供电时间", "后备时间", "续航"),
    "间距": ("距离", "净距"),
    "照度": ("照明", "光照强度"),
}


class AdaptiveRetrievalPlanner:
    """根据上一轮证据缺口生成可审计且实质不同的检索计划。"""

    def __init__(self) -> None:
        self.analyzer = QueryAnalyzer()

    def create(
        self,
        original_query: str,
        check_items: list[CheckItem],
        attempt: int,
        previous: EvidenceAssessment | None,
        history: list[str],
        top_k: int = 5,
    ) -> RetrievalPlan:
        base_queries = self._item_queries(check_items) or self._decompose(original_query)
        analysis = self.analyzer.analyze(original_query)
        exact_keys = analysis.clause_numbers + analysis.table_numbers
        if exact_keys:
            queries = [original_query]
            query_item_ids = [""]
            routes = ["exact"]
            reason = None if attempt == 0 else "精确标识符未命中；保留标识符并扩展上下文"
            expansion = None if attempt == 0 else "parent_and_neighbors"
        elif attempt == 0:
            queries = base_queries
            query_item_ids = [item.item_id for item in check_items]
            routes = ["bm25", "vector"]
            reason = "复杂问题按检查项拆分" if len(queries) > 1 else None
            expansion = "parent_and_neighbors"
        else:
            queries = [self._expand_terms(item) for item in base_queries]
            query_item_ids = [item.item_id for item in check_items]
            routes = ["bm25"] if attempt == 1 else ["vector", "bm25"]
            reason = (
                f"证据评估失败：{previous.failure_reason}"
                if previous and previous.failure_reason
                else "领域同义词扩展并切换检索路线"
            )
            expansion = "parent_and_neighbors"
        normalized_history = {self._normalize(item) for item in history}
        if history and all(self._normalize(item) in normalized_history for item in queries):
            queries = [f"{item} 规范要求" for item in queries]
            reason = (reason + "；" if reason else "") + "避免与历史查询实质相同"
        unit_types: list[UnitType] = []
        if analysis.table_numbers:
            unit_types = [UnitType.TABLE, UnitType.TABLE_ROW]
        elif analysis.clause_numbers:
            unit_types = [UnitType.CLAUSE]
        return RetrievalPlan(
            intent=analysis.intent,
            query=queries[0],
            subqueries=queries,
            # 空字符串表示精确编号查询同时支持本轮所有检查项。
            subquery_item_ids=query_item_ids,
            exact_keys=exact_keys,
            filters=RetrievalFilters(standard_codes=analysis.standard_codes, unit_types=unit_types),
            retrievers=routes,
            top_k=top_k,
            expansion_policy=expansion,
            rewrite_reason=reason,
            attempt=attempt,
        )

    @staticmethod
    def _item_queries(items: list[CheckItem]) -> list[str]:
        return [
            " ".join(filter(None, (item.object, item.attribute, item.condition))) for item in items
        ]

    @staticmethod
    def _decompose(query: str) -> list[str]:
        parts = [part.strip("，。？?；; ") for part in re.split(r"[，,；;]", query)]
        return [part for part in parts if part] or [query]

    @staticmethod
    def _expand_terms(query: str) -> str:
        additions: list[str] = []
        for term, synonyms in DOMAIN_SYNONYMS.items():
            if term.casefold() in query.casefold():
                additions.extend(synonyms)
        return " ".join(dict.fromkeys([query, *additions]))

    @staticmethod
    def _normalize(query: str) -> str:
        return re.sub(r"\W+", "", query).casefold()
