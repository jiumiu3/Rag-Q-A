from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field

from app.clarification.models import (
    ClarificationAnswerRequest,
    ClarificationStartRequest,
    ClarificationStartResponse,
    SessionState,
)
from app.clarification.repository import SQLiteSessionRepository
from app.clarification.session import ClarificationSessionService
from app.compliance.evaluator import RuleEvaluator
from app.compliance.models import ExecutableRule
from app.core.config import get_settings
from app.design.models import ConfirmRequest, ConfirmResponse, DesignPreview, PreviewRequest
from app.design.parser import DesignDescriptionParser
from app.domain.models import CheckItem, ComplianceResult, StrictModel

router = APIRouter(tags=["review"])


class EvaluateItem(StrictModel):
    item: CheckItem
    rule: ExecutableRule


class EvaluateRequest(StrictModel):
    evaluations: list[EvaluateItem] = Field(min_length=1)


class EvaluateResponse(StrictModel):
    results: list[ComplianceResult]


@lru_cache
def get_design_parser() -> DesignDescriptionParser:
    return DesignDescriptionParser()


@lru_cache
def get_session_service() -> ClarificationSessionService:
    settings = get_settings()
    repository = SQLiteSessionRepository(settings.storage.session_sqlite_path)
    return ClarificationSessionService(repository, settings.agent.max_clarifications)


@lru_cache
def get_rule_evaluator() -> RuleEvaluator:
    return RuleEvaluator()


async def provide_design_parser() -> DesignDescriptionParser:
    return get_design_parser()


async def provide_session_service() -> ClarificationSessionService:
    return get_session_service()


async def provide_rule_evaluator() -> RuleEvaluator:
    return get_rule_evaluator()


@router.post("/design/preview", response_model=DesignPreview)
async def preview(
    request: PreviewRequest,
    parser: Annotated[DesignDescriptionParser, Depends(provide_design_parser)],
) -> DesignPreview:
    return parser.parse(request.description, auto_confirm=request.auto_confirm)


@router.post("/design/confirm", response_model=ConfirmResponse)
async def confirm(
    request: ConfirmRequest,
    parser: Annotated[DesignDescriptionParser, Depends(provide_design_parser)],
) -> ConfirmResponse:
    return ConfirmResponse(items=parser.apply_patches(request.items, request.patches))


@router.post("/clarifications", response_model=ClarificationStartResponse)
async def start_clarification(
    request: ClarificationStartRequest,
    service: Annotated[ClarificationSessionService, Depends(provide_session_service)],
) -> ClarificationStartResponse:
    session, _token = service.start(request.items)
    return ClarificationStartResponse(session=session)


@router.post("/clarifications/resume", response_model=SessionState)
async def resume_clarification(
    request: ClarificationAnswerRequest,
    service: Annotated[ClarificationSessionService, Depends(provide_session_service)],
) -> SessionState:
    return service.resume(request.session_id, request.resume_token, request.answers)


@router.post("/compliance/evaluate", response_model=EvaluateResponse)
async def evaluate(
    request: EvaluateRequest,
    evaluator: Annotated[RuleEvaluator, Depends(provide_rule_evaluator)],
) -> EvaluateResponse:
    return EvaluateResponse(
        results=[evaluator.evaluate(entry.item, entry.rule) for entry in request.evaluations]
    )
