import pytest

from app.domain.models import (
    Evidence,
    EvidenceStatus,
    IntentType,
    RetrievalFilters,
    RetrievalPlan,
    SourceCitation,
    SourceSpan,
)
from app.retrieval.agentic_planner import AgenticRetrievalPlanner
from app.retrieval.evaluator import QAEvidenceEvaluator
from app.retrieval.models import RetrievalGoalStatus
from app.retrieval.tools import EvidenceContextTool


def test_explicit_clause_uses_exact_only() -> None:
    planner = AgenticRetrievalPlanner()
    question = "Q/GGW 02005.1-2022 第8.3.4.4条有什么要求？"
    analysis = planner.analyze_question(question)
    plan = planner.create(question, analysis, 0, None, [])
    assert plan.retrievers == ["exact"]
    assert plan.exact_keys == ["8.3.4.4"]


def test_numeric_question_uses_bm25_initial_route() -> None:
    planner = AgenticRetrievalPlanner()
    question = "设计压力为1.6 MPa时有什么要求？"
    analysis = planner.analyze_question(question)
    plan = planner.create(question, analysis, 0, None, [])
    assert plan.retrievers == ["bm25"]


def test_semantic_question_keeps_hybrid_initial_route() -> None:
    planner = AgenticRetrievalPlanner()
    question = "UPS持续供电时间有什么要求？"
    analysis = planner.analyze_question(question)
    plan = planner.create(question, analysis, 0, None, [])
    assert plan.retrievers == ["bm25", "vector"]


def test_complex_question_has_multiple_goals_and_retry_changes_queries() -> None:
    planner = AgenticRetrievalPlanner()
    question = "控制室照度有什么要求；UPS供电时间有什么要求？"
    analysis = planner.analyze_question(question)
    first = planner.create(question, analysis, 0, None, [])
    assert analysis.requires_multi_step
    assert len(analysis.goals) == 2
    retry = planner.create(question, analysis, 1, None, first.subqueries)
    assert retry.subqueries != first.subqueries


def test_exact_miss_stops_without_semantic_fallback() -> None:
    planner = AgenticRetrievalPlanner()
    question = "第99.99.99条是什么？"
    analysis = planner.analyze_question(question)
    plan = planner.create(question, analysis, 0, None, [])
    assessment = QAEvidenceEvaluator().evaluate(analysis, plan, [], set(), 0)
    assert assessment.next_action == "safe_stop"
    assert plan.retrievers == ["exact"]
    assert not assessment.is_sufficient


def _evidence(
    evidence_id: str = "evidence_1", status: EvidenceStatus = EvidenceStatus.SUFFICIENT
) -> Evidence:
    text = "UPS持续供电时间不应低于2 h。"
    return Evidence(
        evidence_id=evidence_id,
        unit_id="unit_1",
        content=text,
        citation=SourceCitation(
            standard_code="Q/GGW 02005.1-2022",
            file_name="test.pdf",
            clause_id="5.2.1",
            page_number=3,
            quote=text,
            source_span=SourceSpan(document_id="doc_1", page_number=3),
        ),
        retrieval_sources=["bm25"],
        support_type=status,
    )


def _open_plan(attempt: int = 0) -> RetrievalPlan:
    return RetrievalPlan(
        intent=IntentType.KNOWLEDGE_QA,
        query="UPS持续供电时间",
        retrievers=["bm25"],
        filters=RetrievalFilters(),
        attempt=attempt,
    )


def test_no_new_evidence_and_max_attempt_stop() -> None:
    planner = AgenticRetrievalPlanner()
    analysis = planner.analyze_question("UPS持续供电时间有什么要求？")
    no_new = QAEvidenceEvaluator().evaluate(analysis, _open_plan(), [], set(), 0)
    assert no_new.next_action == "safe_stop"
    maximum = QAEvidenceEvaluator().evaluate(analysis, _open_plan(2), [], set(), 1)
    assert maximum.next_action == "safe_stop"
    assert maximum.failure_reason == "达到最大检索轮数"


def test_invalid_evidence_id_is_not_accepted_and_context_rejects_it() -> None:
    planner = AgenticRetrievalPlanner()
    analysis = planner.analyze_question("UPS持续供电时间有什么要求？")
    assessment = QAEvidenceEvaluator().evaluate(analysis, _open_plan(), [_evidence()], set(), 1)
    assert not assessment.is_sufficient
    with pytest.raises(ValueError, match="非法 evidence_id"):
        EvidenceContextTool().execute("invented", [_evidence()], 0)


def test_conflict_requires_manual_review_and_sufficient_evidence_is_accepted() -> None:
    planner = AgenticRetrievalPlanner()
    analysis = planner.analyze_question("UPS持续供电时间有什么要求？")
    conflict = _evidence(status=EvidenceStatus.CONFLICTING)
    assessment = QAEvidenceEvaluator().evaluate(
        analysis, _open_plan(), [conflict], {conflict.evidence_id}, 1
    )
    assert assessment.next_action == "manual_review"
    assert assessment.goal_assessments[0].status == RetrievalGoalStatus.CONFLICT
    supported = _evidence()
    accepted = QAEvidenceEvaluator().evaluate(
        analysis, _open_plan(), [supported], {supported.evidence_id}, 1
    )
    assert accepted.is_sufficient
    assert accepted.next_action == "accept"


def test_multi_goal_does_not_bind_unrelated_evidence_to_every_goal() -> None:
    planner = AgenticRetrievalPlanner()
    analysis = planner.analyze_question("控制室照度有什么要求；UPS持续供电时间有什么要求？")
    ups = _evidence()
    assessment = QAEvidenceEvaluator().evaluate(
        analysis,
        _open_plan(),
        [ups],
        {ups.evidence_id},
        1,
    )
    by_description = {
        goal.description: coverage
        for goal, coverage in zip(analysis.goals, assessment.goal_assessments, strict=True)
    }
    lighting = next(
        coverage for description, coverage in by_description.items() if "照度" in description
    )
    power = next(
        coverage for description, coverage in by_description.items() if "UPS" in description
    )
    assert lighting.status == RetrievalGoalStatus.UNSUPPORTED
    assert lighting.supporting_evidence_ids == []
    assert power.status == RetrievalGoalStatus.SUPPORTED
    assert power.supporting_evidence_ids == [ups.evidence_id]
    assert not assessment.is_sufficient
    assert assessment.next_action == "expand_query"
