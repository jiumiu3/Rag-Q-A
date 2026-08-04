from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.compliance.models import ExecutableRule
from app.domain.models import (
    CheckItem,
    ClarificationRequest,
    ComplianceResult,
    Evidence,
    IntentType,
    StrictModel,
    TraceEvent,
)
from app.qa.models import AnswerDraft


class WorkflowStatus(StrEnum):
    NEW = "NEW"
    RUNNING = "RUNNING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    WAITING_CLARIFICATION = "WAITING_CLARIFICATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SAFE_STOPPED = "SAFE_STOPPED"


class PendingAction(StrEnum):
    CONFIRM_CHECK_ITEMS = "CONFIRM_CHECK_ITEMS"
    ANSWER_CLARIFICATION = "ANSWER_CLARIFICATION"


class WorkflowMessage(StrictModel):
    role: str
    content: str
    created_at: datetime


class AgentState(StrictModel):
    session_id: str
    request_id: str
    status: WorkflowStatus = WorkflowStatus.NEW
    current_node: str = "normalize_input"
    input_text: str = ""
    normalized_input: str = ""
    intent: IntentType | None = None
    scenario: dict[str, str] = Field(default_factory=dict)
    check_items: list[CheckItem] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    rules: list[ExecutableRule] = Field(default_factory=list)
    compliance_results: list[ComplianceResult] = Field(default_factory=list)
    answer: AnswerDraft | None = None
    clarification: ClarificationRequest | None = None
    pending_action: PendingAction | None = None
    report_markdown: str | None = None
    messages: list[WorkflowMessage] = Field(default_factory=list)
    workflow_trace: list[TraceEvent] = Field(default_factory=list)
    clarification_count: int = Field(default=0, ge=0)
    retrieval_retry_count: int = Field(default=0, ge=0)
    validation_retry_count: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SessionCreateRequest(StrictModel):
    mode: str = "review"


class SessionMessageRequest(StrictModel):
    content: str = Field(min_length=1, max_length=20000)
    idempotency_key: str = Field(min_length=8, max_length=100)


class WorkflowConfirmRequest(StrictModel):
    items: list[CheckItem]
    rules: list[ExecutableRule] = Field(default_factory=list)


class WorkflowClarificationRequest(StrictModel):
    answers: dict[str, str | int | float | bool]
