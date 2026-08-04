from pydantic import Field

from app.domain.models import Evidence, IntentType, RetrievalPlan, StrictModel


class QueryAnalysis(StrictModel):
    intent: IntentType
    standard_codes: list[str] = Field(default_factory=list)
    clause_numbers: list[str] = Field(default_factory=list)
    table_numbers: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)


class RetrievalTrace(StrictModel):
    plan: RetrievalPlan
    query_analysis: QueryAnalysis
    candidates: dict[str, list[str]] = Field(default_factory=dict)
    fused_scores: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class RetrievalResult(StrictModel):
    evidence: list[Evidence]
    trace: RetrievalTrace
