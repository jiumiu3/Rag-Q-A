from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.domain.models import RequirementLevel, RuleCondition, StrictModel


class RuleBase(StrictModel):
    rule_id: str
    subject: str
    attribute: str
    conditions: list[RuleCondition] = Field(default_factory=list)
    requirement_level: RequirementLevel
    evidence_ids: list[str] = Field(min_length=1)
    external_standard_required: bool = False


class NumericThresholdRule(RuleBase):
    rule_type: Literal["NUMERIC_THRESHOLD"] = "NUMERIC_THRESHOLD"
    operator: Literal[">", ">=", "<", "<=", "==", "!="]
    threshold: Decimal
    unit: str


class NumericRangeRule(RuleBase):
    rule_type: Literal["NUMERIC_RANGE"] = "NUMERIC_RANGE"
    minimum: Decimal
    maximum: Decimal
    unit: str
    include_minimum: bool = True
    include_maximum: bool = True

    @model_validator(mode="after")
    def validate_range(self) -> "NumericRangeRule":
        if self.maximum < self.minimum:
            raise ValueError("maximum 不得小于 minimum")
        return self


class EnumRule(RuleBase):
    rule_type: Literal["ENUM"] = "ENUM"
    allowed_values: list[str] = Field(min_length=1)
    forbidden: bool = False


class BooleanRequirementRule(RuleBase):
    rule_type: Literal["BOOLEAN_REQUIREMENT"] = "BOOLEAN_REQUIREMENT"
    expected: bool


class LookupTableRule(RuleBase):
    rule_type: Literal["LOOKUP_TABLE"] = "LOOKUP_TABLE"
    table_id: str
    input_fields: list[str] = Field(min_length=1)
    rows: list[dict[str, str | int | Decimal | bool]] = Field(default_factory=list)
    output_field: str
    parse_status: str


class CompositeRule(RuleBase):
    rule_type: Literal["COMPOSITE"] = "COMPOSITE"
    combinator: Literal["AND", "OR"]
    rules: list["ExecutableRule"] = Field(min_length=1)


class ManualReviewRule(RuleBase):
    rule_type: Literal["MANUAL_REVIEW"] = "MANUAL_REVIEW"
    reason: str


ExecutableRule = Annotated[
    NumericThresholdRule
    | NumericRangeRule
    | EnumRule
    | BooleanRequirementRule
    | LookupTableRule
    | CompositeRule
    | ManualReviewRule,
    Field(discriminator="rule_type"),
]


class RuleExtractionResult(StrictModel):
    rules: list[ExecutableRule]
    failures: list[str] = Field(default_factory=list)


class CandidateRuleStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class CandidateRule(StrictModel):
    candidate_rule_id: str
    rule_type: Literal[
        "NUMERIC_THRESHOLD",
        "NUMERIC_RANGE",
        "ENUM",
        "BOOLEAN_REQUIREMENT",
        "LOOKUP_TABLE",
        "COMPOSITE",
        "MANUAL_REVIEW",
    ]
    subject: str
    attribute: str
    operator: str | None = None
    expected_value: Decimal | str | None = None
    lower_bound: Decimal | None = None
    upper_bound: Decimal | None = None
    unit: str | None = None
    allowed_values: list[str] = Field(default_factory=list)
    expected_boolean: bool | None = None
    conditions: list[RuleCondition] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    requirement_level: RequirementLevel
    evidence_ids: list[str] = Field(min_length=1)
    standard_code: str
    clause_id: str | None = None
    table_id: str | None = None
    page_number: int = Field(ge=1)
    source_text: str
    confidence: float = Field(ge=0, le=1)
    uncertainties: list[str] = Field(default_factory=list)
    extraction_method: Literal["deterministic", "llm", "manual_fallback"]
    status: CandidateRuleStatus = CandidateRuleStatus.PENDING_REVIEW
    version: int = Field(default=1, ge=1)
    created_at: datetime
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    review_comment: str | None = None

    def to_executable(self) -> ExecutableRule:
        if self.status != CandidateRuleStatus.CONFIRMED:
            raise ValueError("只有 confirmed 候选规则可以转换为正式规则")
        common = dict(
            rule_id=self.candidate_rule_id.replace("candidate_", "rule_", 1),
            subject=self.subject,
            attribute=self.attribute,
            conditions=self.conditions,
            requirement_level=self.requirement_level,
            evidence_ids=self.evidence_ids,
        )
        if self.rule_type == "NUMERIC_THRESHOLD" and self.expected_value is not None and self.unit:
            return NumericThresholdRule(
                **common, operator=self.operator, threshold=self.expected_value, unit=self.unit
            )
        if (
            self.rule_type == "NUMERIC_RANGE"
            and self.lower_bound is not None
            and self.upper_bound is not None
            and self.unit
        ):
            return NumericRangeRule(
                **common, minimum=self.lower_bound, maximum=self.upper_bound, unit=self.unit
            )
        if self.rule_type == "ENUM" and self.allowed_values:
            return EnumRule(**common, allowed_values=self.allowed_values)
        if self.rule_type == "BOOLEAN_REQUIREMENT" and self.expected_boolean is not None:
            return BooleanRequirementRule(**common, expected=self.expected_boolean)
        return ManualReviewRule(**common, reason="候选规则包含歧义、查表或复合逻辑，需人工执行")


class RuleReviewRequest(StrictModel):
    reviewed_by: str = Field(min_length=1)
    review_comment: str | None = None
    changes: dict[str, object] = Field(default_factory=dict)


class RuleMatch(StrictModel):
    check_item_id: str
    matched_rule_ids: list[str] = Field(default_factory=list)
    match_score: float | None = None
    missing_conditions: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    status: Literal["matched", "ambiguous", "missing_condition", "not_found", "conflict"]
