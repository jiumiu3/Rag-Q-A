from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.compliance.judgement_models import ComplianceJudgement, ComplianceJudgementList
from app.compliance.judgement_validator import ComplianceJudgementValidator
from app.compliance.rag_judge import ComplianceJudge, unavailable_judgements
from app.domain.models import (
    CharacterSpan,
    CheckItem,
    ComplianceStatus,
    Evidence,
    EvidenceStatus,
    RequirementLevel,
    SourceCitation,
    SourceSpan,
)
from app.retrieval.planner import AdaptiveRetrievalPlanner
from app.workflow.graph import WorkflowRunner
from app.workflow.models import AgentState, WorkflowStatus
from app.workflow.nodes import WorkflowNodes


def check_item(item_id: str = "check_1", value: Decimal = Decimal("500")) -> CheckItem:
    return CheckItem(
        item_id=item_id,
        object="控制室",
        attribute="照度",
        value=value,
        unit="lx",
        condition="无人值守",
        source_text="无人值守控制室照度为500lx",
        span=CharacterSpan(start=0, end=16),
    )


def evidence(
    evidence_id: str = "evidence_1",
    status: EvidenceStatus = EvidenceStatus.SUFFICIENT,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        unit_id="unit_1",
        content="无人值守控制室照度不得低于 300 lx。",
        citation=SourceCitation(
            standard_code="Q/GGW TEST-2026",
            file_name="test.pdf",
            clause_id="5.1",
            page_number=3,
            quote="照度不得低于 300 lx。",
            source_span=SourceSpan(document_id="doc_1", page_number=3),
        ),
        retrieval_sources=["bm25"],
        support_type=status,
    )


def judgement(**changes: object) -> ComplianceJudgement:
    payload: dict[str, object] = {
        "check_item_id": "check_1",
        "status": ComplianceStatus.NON_COMPLIANT,
        "reasoning": "规范要求不低于300lx",
        "evidence_ids": ["evidence_1"],
        "requirement_level": RequirementLevel.SHALL,
        "actual": "500",
        "required": "300",
        "operator": ">=",
        "required_unit": "lx",
    }
    payload.update(changes)
    return ComplianceJudgement.model_validate(payload)


def test_validator_recomputes_numeric_result_instead_of_trusting_model_status() -> None:
    result = ComplianceJudgementValidator().validate(
        check_item(), judgement(), [evidence()], ["evidence_1"]
    )
    assert result.status == ComplianceStatus.COMPLIANT
    assert result.comparison_trace[0].passed is True
    assert result.actual == "500 lx"


def test_validator_rejects_cross_item_evidence() -> None:
    result = ComplianceJudgementValidator().validate(
        check_item(), judgement(), [evidence()], ["evidence_for_other_item"]
    )
    assert result.status == ComplianceStatus.MANUAL_REVIEW_REQUIRED
    assert any("未绑定到当前检查项" in item for item in result.limitations)


def test_partial_and_conflicting_evidence_safely_degrade() -> None:
    partial = ComplianceJudgementValidator().validate(
        check_item(), judgement(), [evidence(status=EvidenceStatus.PARTIAL)], ["evidence_1"]
    )
    conflict = ComplianceJudgementValidator().validate(
        check_item(),
        judgement(),
        [evidence(status=EvidenceStatus.CONFLICTING)],
        ["evidence_1"],
    )
    assert partial.status == ComplianceStatus.MANUAL_REVIEW_REQUIRED
    assert conflict.status == ComplianceStatus.CONFLICT


def test_planner_preserves_check_item_for_each_subquery_and_excludes_actual_value() -> None:
    items = [check_item("check_1"), check_item("check_2", Decimal("600"))]
    plan = AdaptiveRetrievalPlanner().create("请审查控制室照度", items, 0, None, [])
    assert plan.subquery_item_ids == ["check_1", "check_2"]
    assert all("500" not in query and "600" not in query for query in plan.subqueries)


class FakeClient:
    def __init__(self) -> None:
        self.prompt = ""

    def complete(self, prompt: str, _model: object) -> ComplianceJudgementList:
        self.prompt = prompt
        return ComplianceJudgementList(judgements=[judgement()])


def test_judge_only_sends_evidence_bound_to_current_item() -> None:
    client = FakeClient()
    judge = ComplianceJudge(client)  # type: ignore[arg-type]
    judge.judge(
        [check_item()],
        [evidence(), evidence("evidence_other")],
        {"check_1": ["evidence_1"]},
    )
    assert "evidence_1" in client.prompt
    assert "evidence_other" not in client.prompt
    assert "不可信的引用数据" in client.prompt


def test_judge_limits_model_input_to_top_five_direct_evidence() -> None:
    client = FakeClient()
    rows = [evidence(f"evidence_{index}") for index in range(1, 7)]
    ComplianceJudge(client).judge(  # type: ignore[arg-type]
        [check_item()], rows, {"check_1": [row.evidence_id for row in rows]}
    )
    assert "evidence_5" in client.prompt
    assert "evidence_6" not in client.prompt


def test_missing_model_configuration_never_returns_compliant() -> None:
    result = unavailable_judgements([check_item()])
    assert result[0].status == ComplianceStatus.MANUAL_REVIEW_REQUIRED


def test_validator_recomputes_enum_and_boolean_results() -> None:
    enum_item = check_item().model_copy(update={"value": "IP65", "unit": None})
    enum_result = ComplianceJudgementValidator().validate(
        enum_item,
        judgement(actual="IP65", required='["IP65", "IP66"]', operator="in"),
        [evidence()],
        ["evidence_1"],
    )
    boolean_item = check_item().model_copy(update={"value": True, "unit": None})
    boolean_result = ComplianceJudgementValidator().validate(
        boolean_item,
        judgement(actual="True", required="true", operator="=="),
        [evidence()],
        ["evidence_1"],
    )
    assert enum_result.status == ComplianceStatus.COMPLIANT
    assert boolean_result.status == ComplianceStatus.COMPLIANT


def test_validator_rejects_citation_not_present_in_bound_evidence() -> None:
    result = ComplianceJudgementValidator().validate(
        check_item(),
        judgement(cited_clause_ids=["9.9"]),
        [evidence()],
        ["evidence_1"],
    )
    assert result.status == ComplianceStatus.MANUAL_REVIEW_REQUIRED
    assert any("条款号" in item for item in result.limitations)


class FakeComplianceJudge:
    def judge(self, *_args: object) -> list[ComplianceJudgement]:
        return [judgement(status=ComplianceStatus.NON_COMPLIANT)]


def test_workflow_runs_judge_validate_and_report_chain() -> None:
    qa = SimpleNamespace()
    retrieval = SimpleNamespace()
    nodes = WorkflowNodes(  # type: ignore[arg-type]
        qa, retrieval, compliance_judge=FakeComplianceJudge()  # type: ignore[arg-type]
    )
    now = datetime.now(UTC)
    state = AgentState(
        session_id="session_test",
        request_id="request_test",
        status=WorkflowStatus.RUNNING,
        intent="COMPLIANCE_REVIEW",
        check_items=[check_item()],
        evidence=[evidence()],
        evidence_by_check_item={"check_1": ["evidence_1"]},
        created_at=now,
        updated_at=now,
    )
    result = WorkflowRunner(nodes).run(state, "judge_compliance")
    assert result.status == WorkflowStatus.COMPLETED
    assert result.compliance_results[0].status == ComplianceStatus.COMPLIANT
    assert "总体状态：COMPLIANT" in (result.report_markdown or "")
