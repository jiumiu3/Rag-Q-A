import time

from app.domain.models import Evidence, RetrievalPlan
from app.retrieval.models import RetrievalResult, RetrievalToolTrace
from app.retrieval.service import RetrievalService


class KnowledgeSearchTool:
    """执行受工作流控制的检索，并返回结构化审计记录。"""

    def __init__(self, retrieval: RetrievalService) -> None:
        self.retrieval = retrieval

    def execute(
        self, plan: RetrievalPlan, known_evidence_ids: set[str]
    ) -> tuple[RetrievalResult, RetrievalToolTrace]:
        started = time.perf_counter()
        result = self.retrieval.execute(plan)
        direct = [item for item in result.evidence if item.context_reason is None]
        trace = RetrievalToolTrace(
            tool_name="knowledge_search",
            query=plan.query,
            retrievers=plan.retrievers,
            filters=plan.filters.model_dump(mode="json"),
            result_count=len(direct),
            new_evidence_count=sum(
                item.evidence_id not in known_evidence_ids for item in result.evidence
            ),
            duration_ms=(time.perf_counter() - started) * 1000,
            attempt=plan.attempt,
            reason_code=plan.rewrite_reason or "initial_search",
        )
        return result, trace


class EvidenceContextTool:
    """只允许从已检索 Evidence 中按 ID 读取父条款或相邻上下文。"""

    def execute(
        self, evidence_id: str, evidence: list[Evidence], attempt: int
    ) -> tuple[list[Evidence], RetrievalToolTrace]:
        started = time.perf_counter()
        valid_ids = {item.evidence_id for item in evidence}
        if evidence_id not in valid_ids:
            raise ValueError("EvidenceContextTool 收到非法 evidence_id")
        context = [
            item
            for item in evidence
            if item.evidence_id == evidence_id or item.context_reason == "parent_or_neighbor"
        ]
        return context, RetrievalToolTrace(
            tool_name="evidence_context",
            query=evidence_id,
            result_count=len(context),
            new_evidence_count=0,
            duration_ms=(time.perf_counter() - started) * 1000,
            attempt=attempt,
            reason_code="requested_evidence_context",
        )
