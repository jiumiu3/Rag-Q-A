from datetime import datetime

from pydantic import Field

from app.domain.models import CheckItem, ClarificationRequest, MissingField, StrictModel


class RuleTemplate(StrictModel):
    template_id: str
    attributes: set[str]
    required_fields: list[str]
    reasons: dict[str, str]
    examples: dict[str, list[str]] = Field(default_factory=dict)


class CompletenessResult(StrictModel):
    ready_items: list[CheckItem]
    incomplete_items: list[CheckItem]
    missing_fields: list[MissingField]


class SessionState(StrictModel):
    session_id: str
    items: list[CheckItem]
    clarification_round: int = Field(default=0, ge=0)
    max_rounds: int = Field(default=2, ge=0)
    pending_request: ClarificationRequest | None = None
    changes: list[dict[str, str]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class ClarificationStartRequest(StrictModel):
    items: list[CheckItem] = Field(min_length=1)


class ClarificationStartResponse(StrictModel):
    session: SessionState


class ClarificationAnswerRequest(StrictModel):
    session_id: str
    resume_token: str
    answers: dict[str, str | int | float | bool]
