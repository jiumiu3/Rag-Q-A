from datetime import UTC, datetime
from pathlib import Path

from app.compliance.judgement_models import ComplianceJudgement, ComplianceJudgementList
from app.domain.models import CheckItem, ComplianceStatus
from app.workflow.graph import WorkflowRunner
from app.workflow.models import AgentState, PendingAction, WorkflowStatus
from app.workflow.nodes import WorkflowNodes
from tests.test_m9_workflow import FakeQA
from tests.test_review_memory import check


class MissingConditionJudge:
    def judge(self, check_items: list[CheckItem], *_args: object) -> ComplianceJudgementList:
        item = check_items[0]
        return ComplianceJudgementList(
            overall_summary="缺少 UPS 供电对象",
            judgements=[
                ComplianceJudgement(
                    check_item_id=item.item_id,
                    status=ComplianceStatus.INSUFFICIENT_INFORMATION,
                    reasoning="规范要求随供电对象变化",
                    missing_fields=["ups_target_equipment"],
                )
            ],
        )


def test_judgement_missing_field_pauses_for_evidence_driven_clarification(
    tmp_path: Path,
) -> None:
    qa = FakeQA()
    nodes = WorkflowNodes(qa, qa.retrieval, compliance_judge=MissingConditionJudge())  # type: ignore[arg-type]
    now = datetime.now(UTC)
    state = AgentState(
        session_id="session_test",
        request_id="request_test",
        check_items=[check("check_ups", "持续供电时间", 2, "h")],
        created_at=now,
        updated_at=now,
    )
    result = WorkflowRunner(nodes).run(state, "judge_compliance")
    assert result.status == WorkflowStatus.WAITING_CLARIFICATION
    assert result.pending_action == PendingAction.ANSWER_CLARIFICATION
    assert result.clarification is not None
    assert result.clarification.requested_fields[0].field_name == "ups_target_equipment"
    assert result.current_node == "pause"
