from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DocumentStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class UnitType(StrEnum):
    CLAUSE = "CLAUSE"
    TABLE = "TABLE"
    TABLE_ROW = "TABLE_ROW"
    TERM = "TERM"
    FIGURE_NOTE = "FIGURE_NOTE"
    APPENDIX = "APPENDIX"


class ParseStatus(StrEnum):
    PARSED = "PARSED"
    PARTIAL = "PARTIAL"
    NEEDS_MANUAL_ANNOTATION = "NEEDS_MANUAL_ANNOTATION"
    FAILED = "FAILED"


class IntentType(StrEnum):
    KNOWLEDGE_QA = "KNOWLEDGE_QA"
    CLAUSE_LOOKUP = "CLAUSE_LOOKUP"
    TABLE_LOOKUP = "TABLE_LOOKUP"
    CHECKLIST_GENERATION = "CHECKLIST_GENERATION"
    COMPLIANCE_REVIEW = "COMPLIANCE_REVIEW"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class EvidenceStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    CONFLICTING = "CONFLICTING"
    NOT_FOUND = "NOT_FOUND"
    EXTERNAL_STANDARD_REQUIRED = "EXTERNAL_STANDARD_REQUIRED"


class CheckItemStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    READY = "READY"
    EVALUATED = "EVALUATED"


class ComplianceStatus(StrEnum):
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"
    NOT_SPECIFIED = "NOT_SPECIFIED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class RequirementLevel(StrEnum):
    SHALL = "SHALL"
    SHALL_NOT = "SHALL_NOT"
    SHOULD = "SHOULD"
    SHOULD_NOT = "SHOULD_NOT"
    MAY = "MAY"


class RuleType(StrEnum):
    NUMERIC_THRESHOLD = "NUMERIC_THRESHOLD"
    NUMERIC_RANGE = "NUMERIC_RANGE"
    ENUM = "ENUM"
    BOOLEAN_REQUIREMENT = "BOOLEAN_REQUIREMENT"
    LOOKUP_TABLE = "LOOKUP_TABLE"
    COMPOSITE = "COMPOSITE"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class BoundingBox(StrictModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def validate_order(self) -> "BoundingBox":
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("bbox 的右下坐标不得小于左上坐标")
        return self


class DocumentMeta(StrictModel):
    document_id: str
    file_name: str
    standard_code: str
    part: str | None = None
    year: int | None = Field(default=None, ge=1900, le=2200)
    publish_date: date | None = None
    effective_date: date | None = None
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    page_count: int = Field(ge=1)
    status: DocumentStatus = DocumentStatus.PENDING


class SourceSpan(StrictModel):
    document_id: str
    page_number: int = Field(ge=1)
    bbox: BoundingBox | None = None
    image_path: str | None = None
    ocr_block_ids: list[str] = Field(default_factory=list)


class KnowledgeUnit(StrictModel):
    unit_id: str
    unit_type: UnitType
    title: str | None = None
    content: str = Field(min_length=1)
    clause_id: str | None = None
    parent_id: str | None = None
    chapter_path: list[str] = Field(default_factory=list)
    table_id: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    source_spans: list[SourceSpan] = Field(min_length=1)


class TableRecord(StrictModel):
    table_id: str
    title: str
    headers: list[str]
    rows: list[list[str]]
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    image_paths: list[str] = Field(default_factory=list)
    parse_status: ParseStatus

    @model_validator(mode="after")
    def validate_pages(self) -> "TableRecord":
        if self.page_end < self.page_start:
            raise ValueError("page_end 不得早于 page_start")
        return self


class RetrievalFilters(StrictModel):
    standard_codes: list[str] = Field(default_factory=list)
    unit_types: list[UnitType] = Field(default_factory=list)
    page_numbers: list[int] = Field(default_factory=list)


class RetrievalPlan(StrictModel):
    intent: IntentType
    query: str
    exact_keys: list[str] = Field(default_factory=list)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    retrievers: list[Literal["exact", "bm25", "vector"]] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=100)
    expansion_policy: str | None = None


class SourceCitation(StrictModel):
    standard_code: str
    file_name: str
    clause_id: str | None = None
    table_id: str | None = None
    chapter_path: list[str] = Field(default_factory=list)
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1)
    source_span: SourceSpan


class Evidence(StrictModel):
    evidence_id: str
    unit_id: str
    content: str
    citation: SourceCitation
    retrieval_sources: list[str]
    scores: dict[str, float] = Field(default_factory=dict)
    support_type: EvidenceStatus
    context_reason: str | None = None


class CharacterSpan(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> "CharacterSpan":
        if self.end <= self.start:
            raise ValueError("字符区间 end 必须大于 start")
        return self


class CheckItem(StrictModel):
    item_id: str
    object: str
    attribute: str
    value: str | int | Decimal | bool | None = None
    unit: str | None = None
    location: str | None = None
    condition: str | None = None
    relation: str | None = None
    additional_fields: dict[str, str | int | Decimal | bool] = Field(default_factory=dict)
    source_text: str
    span: CharacterSpan
    explicit_fields: set[str] = Field(default_factory=set)
    inferred_fields: set[str] = Field(default_factory=set)
    uncertainties: list[str] = Field(default_factory=list)
    status: CheckItemStatus = CheckItemStatus.DRAFT
    user_corrected: bool = False


class MissingField(StrictModel):
    field_name: str
    reason: str
    expected_type: str
    examples: list[str] = Field(default_factory=list)
    related_item_ids: list[str] = Field(min_length=1)


class ClarificationRequest(StrictModel):
    question: str
    requested_fields: list[MissingField] = Field(min_length=1)
    resume_token: str


class RuleCondition(StrictModel):
    field: str
    operator: str
    value: str | int | Decimal | bool
    unit: str | None = None


class NormalizedRule(StrictModel):
    rule_id: str
    rule_type: RuleType
    subject: str
    attribute: str
    operator: str | None = None
    threshold: Decimal | None = None
    range: tuple[Decimal, Decimal] | None = None
    lookup: dict[str, str | Decimal | int | bool] | None = None
    unit: str | None = None
    conditions: list[RuleCondition] = Field(default_factory=list)
    requirement_level: RequirementLevel
    evidence_ids: list[str] = Field(min_length=1)


class ComparisonStep(StrictModel):
    description: str
    actual: str | None = None
    required: str | None = None
    passed: bool | None = None
    children: list["ComparisonStep"] = Field(default_factory=list)


class ComplianceResult(StrictModel):
    item_id: str
    status: ComplianceStatus
    actual: str | None = None
    required: str | None = None
    comparison_trace: list[ComparisonStep] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    advisory: bool = False
    limitations: list[str] = Field(default_factory=list)


class TraceEvent(StrictModel):
    timestamp: datetime
    request_id: str
    session_id: str | None = None
    module: str
    duration_ms: float = Field(ge=0)
    status: Literal["success", "error", "skipped"]
    summary: str | None = None


class ErrorRecord(StrictModel):
    code: str
    message: str
    module: str
    timestamp: datetime
    retryable: bool = False
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


JsonValue = Annotated[Any, Field(description="仅供最终响应载荷；模块间业务数据应使用具体模型")]
