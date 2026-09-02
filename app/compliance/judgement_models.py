from typing import Literal

from pydantic import Field

from app.domain.models import (
    ComparisonStep,
    ComplianceStatus,
    RequirementLevel,
    StrictModel,
)


class ComplianceJudgement(StrictModel):
    """模型生成的原始判断。

    最终 ComplianceResult 必须经过确定性校验器生成，不直接信任本模型的 status。
    """

    check_item_id: str
    status: ComplianceStatus
    reasoning: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    cited_clause_ids: list[str] = Field(default_factory=list)
    cited_table_ids: list[str] = Field(default_factory=list)
    applied_conditions: list[str] = Field(default_factory=list)
    requirement_level: RequirementLevel | None = None
    actual: str | None = None
    required: str | None = None
    operator: Literal[">", ">=", "<", "<=", "==", "!=", "in", "not_in"] | None = None
    required_unit: str | None = None
    comparison_trace: list[ComparisonStep] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class ComplianceJudgementList(StrictModel):
    overall_summary: str = Field(min_length=1)
    judgements: list[ComplianceJudgement]
