from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.domain.models import StrictModel

FactValue = str | int | float | bool | list[str] | dict[str, str | int | float | bool]


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class FactStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    UNCERTAIN = "uncertain"
    DELETED = "deleted"


class FactValueType(StrEnum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING_LIST = "string_list"
    OBJECT = "object"


class FactSourceType(StrEnum):
    USER_CONFIRMED = "user_confirmed"
    USER_EXPLICIT = "user_explicit"
    MODEL_EXTRACTED = "model_extracted"
    API = "api"


class MemoryAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    NOOP = "noop"


class ExpressionMode(StrEnum):
    EXPLICIT = "explicit"
    UNCERTAIN = "uncertain"
    HYPOTHETICAL = "hypothetical"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    VALID = "valid"
    STALE = "stale"
    SUPERSEDED = "superseded"
    NEEDS_REVIEW = "needs_review"


class Project(StrictModel):
    project_id: str
    user_id: str
    name: str
    project_type: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime


class ProjectCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    project_type: str | None = Field(default=None, max_length=100)


class ProjectFact(StrictModel):
    fact_id: str
    user_id: str
    project_id: str
    fact_key: str
    value: FactValue
    value_type: FactValueType
    unit: str | None = None
    status: FactStatus
    version: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)
    source_type: FactSourceType
    source_message_id: str | None = None
    superseded_by: str | None = None
    created_at: datetime
    updated_at: datetime


class FactCreateRequest(StrictModel):
    fact_key: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,127}$")
    value: FactValue
    unit: str | None = Field(default=None, max_length=32)
    idempotency_key: str = Field(min_length=8, max_length=100)


class FactUpdateRequest(StrictModel):
    value: FactValue
    unit: str | None = Field(default=None, max_length=32)
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=100)


class ProjectListResponse(StrictModel):
    items: list[Project]


class FactListResponse(StrictModel):
    items: list[ProjectFact]


class MemoryCandidateDraft(StrictModel):
    action: MemoryAction
    fact_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.]{0,127}$")
    value: FactValue | None = None
    unit: str | None = Field(default=None, max_length=32)
    confidence: float = Field(default=1.0, ge=0, le=1)
    expression_mode: ExpressionMode
    requires_confirmation: bool = False
    reason: str = Field(min_length=1, max_length=500)


class MemoryCandidateList(StrictModel):
    candidates: list[MemoryCandidateDraft] = Field(default_factory=list, max_length=20)


class MemoryActionResult(StrictModel):
    candidate_id: str
    action: MemoryAction
    status: CandidateStatus
    fact_key: str | None = None
    fact_id: str | None = None
    reason: str


class ReviewRecord(StrictModel):
    review_id: str
    project_id: str
    review_run_id: str
    check_item_id: str
    semantic_key: str
    judgement: str
    reason: str | None = None
    actual: str | None = None
    required: str | None = None
    status: ReviewStatus
    review_version: int
    evidence_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    invalidated_at: datetime | None = None
    invalidated_by_change_id: str | None = None


class ReviewListResponse(StrictModel):
    items: list[ReviewRecord]


class ProjectSummary(StrictModel):
    project_id: str
    active_fact_count: int = 0
    valid_review_count: int = 0
    stale_review_count: int = 0
    pending_question_count: int = 0


def infer_value_type(value: FactValue) -> FactValueType:
    # bool 是 int 的子类，必须先判断，避免写成 number。
    if isinstance(value, bool):
        return FactValueType.BOOLEAN
    if isinstance(value, (int, float)):
        return FactValueType.NUMBER
    if isinstance(value, list):
        return FactValueType.STRING_LIST
    if isinstance(value, dict):
        return FactValueType.OBJECT
    return FactValueType.STRING
