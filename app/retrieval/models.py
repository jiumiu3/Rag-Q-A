from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.domain.models import Evidence, IntentType, RetrievalPlan, StrictModel


class QueryAnalysis(StrictModel):
    intent: IntentType
    standard_codes: list[str] = Field(default_factory=list)
    clause_numbers: list[str] = Field(default_factory=list)
    table_numbers: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)


class RetrievalGoalStatus(StrEnum):
    PENDING = "PENDING"
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    CONFLICT = "CONFLICT"


class RetrievalGoal(StrictModel):
    goal_id: str
    description: str
    required_identifiers: list[str] = Field(default_factory=list)
    suggested_terms: list[str] = Field(default_factory=list)
    status: RetrievalGoalStatus = RetrievalGoalStatus.PENDING
    supporting_evidence_ids: list[str] = Field(default_factory=list)


class QuestionAnalysis(StrictModel):
    complexity: Literal["simple", "complex"]
    goals: list[RetrievalGoal] = Field(min_length=1, max_length=5)
    exact_identifiers: list[str] = Field(default_factory=list)
    requires_multi_step: bool = False


class GoalEvidenceAssessment(StrictModel):
    goal_id: str
    status: RetrievalGoalStatus
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    missing_aspects: list[str] = Field(default_factory=list)
    conflict_evidence_ids: list[str] = Field(default_factory=list)


class RetrievalToolTrace(StrictModel):
    tool_name: Literal["knowledge_search", "evidence_context"]
    query: str
    retrievers: list[str] = Field(default_factory=list)
    filters: dict[str, list[str] | list[int]] = Field(default_factory=dict)
    result_count: int = Field(ge=0)
    new_evidence_count: int = Field(ge=0)
    duration_ms: float = Field(ge=0)
    attempt: int = Field(ge=0)
    reason_code: str


class RetrievalTrace(StrictModel):
    plan: RetrievalPlan
    query_analysis: QueryAnalysis
    candidates: dict[str, list[str]] = Field(default_factory=dict)
    fused_scores: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class RetrievalResult(StrictModel):
    evidence: list[Evidence]
    trace: RetrievalTrace


class EvidenceAssessment(StrictModel):
    is_sufficient: bool
    failure_reason: str | None = None
    missing_aspects: list[str] = Field(default_factory=list)
    unsupported_check_item_ids: list[str] = Field(default_factory=list)
    suggested_query_terms: list[str] = Field(default_factory=list)
    goal_assessments: list[GoalEvidenceAssessment] = Field(default_factory=list)
    next_action: Literal[
        "accept",
        "rewrite_query",
        "expand_query",
        "decompose_query",
        "change_route",
        "clarify",
        "manual_review",
        "safe_stop",
    ]


class RetrievalAttempt(StrictModel):
    attempt: int = Field(ge=0)
    original_query: str
    queries: list[str] = Field(min_length=1)
    routes: list[str] = Field(min_length=1)
    top_k: int = Field(ge=1)
    filters: dict[str, list[str] | list[int]] = Field(default_factory=dict)
    rewrite_reason: str | None = None
    result_count: int = Field(ge=0)
    new_evidence_count: int = Field(ge=0)
    assessment: EvidenceAssessment | None = None
