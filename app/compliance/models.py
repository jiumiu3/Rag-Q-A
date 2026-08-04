from decimal import Decimal
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
