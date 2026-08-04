import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Protocol

from app.core.ids import ResourceType, stable_id
from app.domain.models import (
    Evidence,
    EvidenceStatus,
    IntentType,
    RetrievalFilters,
    RetrievalPlan,
    SourceCitation,
    UnitType,
)
from app.knowledge.indexes import (
    BM25Index,
    EmbeddingClient,
    HashEmbeddingClient,
    VectorIndex,
    allowed_unit_ids,
)
from app.knowledge.models import SearchHit, StoredUnit
from app.knowledge.repository import SQLiteKnowledgeRepository
from app.retrieval.models import QueryAnalysis, RetrievalResult, RetrievalTrace

STANDARD_RE = re.compile(r"Q\s*[/.-]?\s*GGW\s*02005[.．](\d)(?:[-—]\d{4})?", re.I)
CLAUSE_RE = re.compile(r"(?:条款|第)\s*(\d+(?:\.\d+){0,5})\s*条?|\b(\d+(?:\.\d+){2,5})\s*条?")
TABLE_RE = re.compile(r"(?:表|table)\s*([A-Z]?\.?\d+(?:[.-]\d+)*)", re.I)
NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*(?:%|MPa|kPa|℃|mm|m|s|h)?", re.I)


class Reranker(Protocol):
    def rerank(self, query: str, units: Sequence[StoredUnit]) -> list[tuple[str, float]]: ...


class QueryAnalyzer:
    def analyze(self, query: str) -> QueryAnalysis:
        standards = [f"Q/GGW 02005.{part}-2022" for part in STANDARD_RE.findall(query)]
        clauses = [left or right for left, right in CLAUSE_RE.findall(query)]
        tables = TABLE_RE.findall(query)
        if tables:
            intent = IntentType.TABLE_LOOKUP
        elif clauses:
            intent = IntentType.CLAUSE_LOOKUP
        else:
            intent = IntentType.KNOWLEDGE_QA
        return QueryAnalysis(
            intent=intent,
            standard_codes=list(dict.fromkeys(standards)),
            clause_numbers=list(dict.fromkeys(clauses)),
            table_numbers=list(dict.fromkeys(tables)),
            numbers=NUMBER_RE.findall(query),
        )

    def plan(self, query: str, top_k: int = 5) -> tuple[QueryAnalysis, RetrievalPlan]:
        analysis = self.analyze(query)
        exact = analysis.clause_numbers + analysis.table_numbers
        # 明确编号是确定性查询：命中则直接返回，未命中则安全拒答，禁止用相似内容冒充。
        retrievers: list[str] = ["exact"] if exact else ["bm25", "vector"]
        unit_types: list[UnitType] = []
        if analysis.intent == IntentType.CLAUSE_LOOKUP:
            unit_types = [UnitType.CLAUSE]
        elif analysis.intent == IntentType.TABLE_LOOKUP:
            unit_types = [UnitType.TABLE, UnitType.TABLE_ROW]
        filters = RetrievalFilters(
            standard_codes=analysis.standard_codes,
            unit_types=unit_types,
        )
        return analysis, RetrievalPlan(
            intent=analysis.intent,
            query=query,
            exact_keys=exact,
            filters=filters,
            retrievers=retrievers,
            top_k=top_k,
            expansion_policy="parent_and_neighbors",
        )


class RetrievalService:
    def __init__(
        self,
        repository: SQLiteKnowledgeRepository,
        bm25: BM25Index,
        vector: VectorIndex,
        embedding: EmbeddingClient | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.repository = repository
        self.bm25 = bm25
        self.vector = vector
        self.embedding = embedding or HashEmbeddingClient()
        self.reranker = reranker
        self.analyzer = QueryAnalyzer()

    def retrieve(self, query: str, top_k: int = 5) -> RetrievalResult:
        analysis, plan = self.analyzer.plan(query, top_k)
        all_units = self.repository.list_units(plan.filters)
        allowed = allowed_unit_ids(all_units, plan.filters)
        routes: dict[str, list[SearchHit]] = {}
        if "exact" in plan.retrievers:
            routes["exact"] = self.repository.exact_search(plan.exact_keys, plan.filters, top_k * 3)
        if "bm25" in plan.retrievers:
            routes["bm25"] = self.bm25.search(query, allowed, top_k * 4)
        if "vector" in plan.retrievers:
            routes["vector"] = self.vector.search(
                self.embedding.embed([query])[0], allowed, top_k * 4
            )
        fused, source_scores = self._rrf(routes)
        if routes.get("exact"):
            # 明确编号查询以精确命中为首要结果，语义路线只负责补充相关上下文。
            exact_order = {
                hit.knowledge_unit_id: index for index, hit in enumerate(routes["exact"])
            }
            fused.sort(
                key=lambda item: (
                    item[0] not in exact_order,
                    exact_order.get(item[0], 0),
                    -item[1],
                    item[0],
                )
            )
        candidate_ids = [key for key, _ in fused[: top_k * 2]]
        units = self.repository.get_units(candidate_ids)
        if self.reranker:
            reranked = self.reranker.rerank(query, units)
            order = {key: score for key, score in reranked}
            units.sort(key=lambda item: -order.get(item.unit.unit_id, 0))
        units = self._expand(units[:top_k], all_units)
        evidence = [
            self._evidence(item, source_scores.get(item.unit.unit_id, {}), analysis)
            for item in units
        ]
        warnings: list[str] = []
        if analysis.intent == IntentType.TABLE_LOOKUP and any(
            item.support_type == EvidenceStatus.PARTIAL for item in evidence
        ):
            warnings.append("表格尚未完成行列结构化，禁止自动参数查值，需人工复核。")
        trace = RetrievalTrace(
            plan=plan,
            query_analysis=analysis,
            candidates={
                route: [hit.knowledge_unit_id for hit in hits] for route, hits in routes.items()
            },
            fused_scores={key: score for key, score in fused},
            warnings=warnings,
        )
        return RetrievalResult(evidence=evidence, trace=trace)

    @staticmethod
    def _rrf(
        routes: dict[str, list[SearchHit]], k: int = 60
    ) -> tuple[list[tuple[str, float]], dict[str, dict[str, float]]]:
        fused: defaultdict[str, float] = defaultdict(float)
        scores: defaultdict[str, dict[str, float]] = defaultdict(dict)
        weights = {"exact": 2.0, "bm25": 1.0, "vector": 1.0}
        for route, hits in routes.items():
            for rank, hit in enumerate(hits, 1):
                fused[hit.knowledge_unit_id] += weights.get(route, 1.0) / (k + rank)
                scores[hit.knowledge_unit_id][route] = hit.score
        return sorted(fused.items(), key=lambda item: (-item[1], item[0])), dict(scores)

    @staticmethod
    def _expand(selected: list[StoredUnit], all_units: list[StoredUnit]) -> list[StoredUnit]:
        by_id = {item.unit.unit_id: item for item in all_units}
        positions = {item.unit.unit_id: index for index, item in enumerate(all_units)}
        output = list(selected)
        seen = {item.unit.unit_id for item in output}
        for item in list(selected):
            related: list[StoredUnit] = []
            if item.unit.parent_id and item.unit.parent_id in by_id:
                related.append(by_id[item.unit.parent_id])
            position = positions.get(item.unit.unit_id)
            if position is not None:
                for neighbor in all_units[max(0, position - 1) : position + 2]:
                    if neighbor.document_id == item.document_id:
                        related.append(neighbor)
            for context in related:
                if context.unit.unit_id not in seen:
                    output.append(context)
                    seen.add(context.unit.unit_id)
        return output

    @staticmethod
    def _evidence(
        stored: StoredUnit, scores: dict[str, float], analysis: QueryAnalysis
    ) -> Evidence:
        unit = stored.unit
        span = unit.source_spans[0]
        partial_table = (
            unit.unit_type in {UnitType.TABLE, UnitType.TABLE_ROW}
            and stored.parse_status == "NEEDS_MANUAL_ANNOTATION"
        )
        quote = unit.content[:500]
        citation = SourceCitation(
            standard_code=stored.standard_code,
            file_name=stored.file_name,
            clause_id=stored.clause_number,
            table_id=stored.table_number,
            chapter_path=unit.chapter_path,
            page_number=span.page_number,
            quote=quote,
            source_span=span,
        )
        sources = list(scores) or ["context_expansion"]
        return Evidence(
            evidence_id=stable_id(ResourceType.EVIDENCE, unit.unit_id, analysis.intent),
            unit_id=unit.unit_id,
            content=unit.content,
            citation=citation,
            retrieval_sources=sources,
            scores=scores,
            support_type=EvidenceStatus.PARTIAL if partial_table else EvidenceStatus.SUFFICIENT,
            context_reason="parent_or_neighbor" if not scores else None,
        )
