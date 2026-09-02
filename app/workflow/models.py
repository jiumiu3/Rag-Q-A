from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.compliance.judgement_models import ComplianceJudgement
from app.compliance.models import CandidateRule, ExecutableRule
from app.domain.models import (
    CheckItem,
    ClarificationRequest,
    ComplianceResult,
    Evidence,
    IntentType,
    RetrievalPlan,
    StrictModel,
    TraceEvent,
)
from app.memory.models import FactValue, MemoryActionResult, ProjectSummary
from app.qa.models import AnswerDraft
from app.retrieval.models import (
    EvidenceAssessment,
    QuestionAnalysis,
    RetrievalAttempt,
    RetrievalGoal,
    RetrievalToolTrace,
)


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
    REVIEW_CANDIDATE_RULES = "REVIEW_CANDIDATE_RULES"
    CONFIRM_MEMORY = "CONFIRM_MEMORY"


class WorkflowMessage(StrictModel):
    role: str
    content: str
    created_at: datetime


class AgentState(StrictModel):
    session_id: str
    request_id: str
    user_id: str = "legacy_local"
    project_id: str | None = None
    active_review_run_id: str | None = None
    active_check_item_id: str | None = None
    pending_question_id: str | None = None
    workflow_stage: str = "new"
    pending_hypothesis: dict[str, FactValue] | None = None
    memory_actions: list[MemoryActionResult] = Field(default_factory=list)
    project_summary: ProjectSummary | None = None
    status: WorkflowStatus = WorkflowStatus.NEW
    current_node: str = "normalize_input"
    input_text: str = ""
    normalized_input: str = ""
    intent: IntentType | None = None
    scenario: dict[str, str] = Field(default_factory=dict)
    check_items: list[CheckItem] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_by_check_item: dict[str, list[str]] = Field(default_factory=dict)
    original_query: str = ""
    current_query: str = ""
    query_history: list[str] = Field(default_factory=list)
    retrieval_plan: RetrievalPlan | None = None
    retrieval_attempts: list[RetrievalAttempt] = Field(default_factory=list)
    evidence_assessments: list[EvidenceAssessment] = Field(default_factory=list)
    question_analysis: QuestionAnalysis | None = None
    retrieval_goals: list[RetrievalGoal] = Field(default_factory=list)
    retrieval_step_count: int = Field(default=0, ge=0)
    evidence_coverage: list[EvidenceAssessment] = Field(default_factory=list)
    retrieval_tool_calls: list[RetrievalToolTrace] = Field(default_factory=list)
    rules: list[ExecutableRule] = Field(default_factory=list)
    candidate_rules: list[CandidateRule] = Field(default_factory=list)
    compliance_results: list[ComplianceResult] = Field(default_factory=list)
    compliance_judgements: list[ComplianceJudgement] = Field(default_factory=list)
    compliance_summary: str | None = None
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
    project_id: str | None = None


class SessionMessageRequest(StrictModel):
    content: str = Field(min_length=1, max_length=20000)
    idempotency_key: str = Field(min_length=8, max_length=100)


class WorkflowConfirmRequest(StrictModel):
    items: list[CheckItem]
    rules: list[ExecutableRule] = Field(default_factory=list)


class WorkflowClarificationRequest(StrictModel):
    answers: dict[str, str | int | float | bool]


class MemoryConfirmRequest(StrictModel):
    approved: bool
