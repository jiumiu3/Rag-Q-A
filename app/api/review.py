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
from app.compliance.candidates import DeterministicCandidateExtractor
from app.compliance.evaluator import RuleEvaluator
from app.compliance.models import (
    CandidateRule,
    CandidateRuleStatus,
    ExecutableRule,
    RuleReviewRequest,
)
from app.compliance.repository import SQLiteRuleRepository
from app.core.config import get_settings
from app.design.models import ConfirmRequest, ConfirmResponse, DesignPreview, PreviewRequest
from app.design.parser import DesignDescriptionParser
from app.domain.models import CheckItem, ComplianceResult, Evidence, StrictModel

router = APIRouter(tags=["review"])


class EvaluateItem(StrictModel):
    item: CheckItem
    rule: ExecutableRule


class EvaluateRequest(StrictModel):
    evaluations: list[EvaluateItem] = Field(min_length=1)


class EvaluateResponse(StrictModel):
    results: list[ComplianceResult]


class CandidateExtractRequest(StrictModel):
    evidence: list[Evidence] = Field(min_length=1)


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


@lru_cache
def get_rule_repository() -> SQLiteRuleRepository:
    return SQLiteRuleRepository(get_settings().storage.session_sqlite_path)


async def provide_rule_repository() -> SQLiteRuleRepository:
    return get_rule_repository()


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


@router.post("/candidate-rules/extract", response_model=list[CandidateRule])
async def extract_candidates(
    request: CandidateExtractRequest,
    repository: Annotated[SQLiteRuleRepository, Depends(provide_rule_repository)],
) -> list[CandidateRule]:
    return [
        repository.save_candidate(item)
        for item in DeterministicCandidateExtractor().extract(request.evidence)
    ]


@router.get("/candidate-rules", response_model=list[CandidateRule])
async def list_candidates(
    repository: Annotated[SQLiteRuleRepository, Depends(provide_rule_repository)],
    status: CandidateRuleStatus | None = None,
) -> list[CandidateRule]:
    return repository.list_candidates(status)


@router.get("/candidate-rules/{candidate_rule_id}", response_model=CandidateRule)
async def get_candidate(
    candidate_rule_id: str,
    repository: Annotated[SQLiteRuleRepository, Depends(provide_rule_repository)],
) -> CandidateRule:
    candidate = repository.get_candidate(candidate_rule_id)
    if not candidate:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="候选规则不存在")
    return candidate


def _review(
    repository: SQLiteRuleRepository,
    candidate_rule_id: str,
    request: RuleReviewRequest,
    status: CandidateRuleStatus,
) -> CandidateRule:
    return repository.review(
        candidate_rule_id, status, request.reviewed_by, request.review_comment, request.changes
    )


@router.post("/candidate-rules/{candidate_rule_id}/confirm", response_model=CandidateRule)
async def confirm_candidate(
    candidate_rule_id: str,
    request: RuleReviewRequest,
    repository: Annotated[SQLiteRuleRepository, Depends(provide_rule_repository)],
) -> CandidateRule:
    return _review(repository, candidate_rule_id, request, CandidateRuleStatus.CONFIRMED)


@router.post("/candidate-rules/{candidate_rule_id}/reject", response_model=CandidateRule)
async def reject_candidate(
    candidate_rule_id: str,
    request: RuleReviewRequest,
    repository: Annotated[SQLiteRuleRepository, Depends(provide_rule_repository)],
) -> CandidateRule:
    return _review(repository, candidate_rule_id, request, CandidateRuleStatus.REJECTED)


@router.post("/candidate-rules/{candidate_rule_id}/revise", response_model=CandidateRule)
async def revise_candidate(
    candidate_rule_id: str,
    request: RuleReviewRequest,
    repository: Annotated[SQLiteRuleRepository, Depends(provide_rule_repository)],
) -> CandidateRule:
    return _review(repository, candidate_rule_id, request, CandidateRuleStatus.CONFIRMED)


@router.get("/rules", response_model=list[ExecutableRule])
async def list_rules(
    repository: Annotated[SQLiteRuleRepository, Depends(provide_rule_repository)],
    subject: str | None = None,
    attribute: str | None = None,
) -> list[ExecutableRule]:
    return repository.list_rules(subject, attribute)


@router.get("/rules/{rule_id}", response_model=ExecutableRule)
async def get_rule(
    rule_id: str, repository: Annotated[SQLiteRuleRepository, Depends(provide_rule_repository)]
) -> ExecutableRule:
    rule = repository.get_rule(rule_id)
    if not rule:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="规则不存在")
    return rule
