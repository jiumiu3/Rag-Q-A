from pathlib import Path

from app.domain.models import ClarificationRequest, MissingField
from app.memory.repository import SQLiteMemoryRepository
from app.workflow.graph import WorkflowRunner
from app.workflow.models import PendingAction, WorkflowStatus
from app.workflow.nodes import WorkflowNodes
from app.workflow.repository import SQLiteWorkflowRepository
from app.workflow.service import WorkflowService
from tests.test_m9_workflow import FakeQA
from tests.test_review_memory import check, result


def workflow(tmp_path: Path) -> tuple[WorkflowService, SQLiteMemoryRepository, str]:
    path = tmp_path / "sessions.db"
    memory = SQLiteMemoryRepository(path)
    project = memory.create_project("user_a", "A站", None)
    qa = FakeQA()
    service = WorkflowService(
        SQLiteWorkflowRepository(path),
        WorkflowRunner(WorkflowNodes(qa, qa.retrieval)),  # type: ignore[arg-type]
        memory,
    )
    return service, memory, project.project_id


def test_new_session_restores_project_summary_and_pending_question(tmp_path: Path) -> None:
    service, memory, project_id = workflow(tmp_path)
    memory.create_fact("user_a", project_id, "ups_duration", 2, "h", "resume-fact-0001")
    clarification = ClarificationRequest(
        question="UPS 给哪些设备供电？",
        requested_fields=[
            MissingField(
                field_name="ups_target_equipment",
                reason="规范适用条件需要",
                expected_type="string",
                related_item_ids=["check_ups"],
            )
        ],
        resume_token="resume_token_12345678",
    )
    memory.save_pending_question("user_a", project_id, "session_old", clarification)

    resumed = service.create_session("user_a", project_id)
    assert resumed.project_summary is not None
    assert resumed.project_summary.active_fact_count == 1
    assert resumed.project_summary.pending_question_count == 1
    assert resumed.status == WorkflowStatus.WAITING_CLARIFICATION
    assert resumed.pending_action == PendingAction.ANSWER_CLARIFICATION
    assert resumed.clarification == clarification


def test_review_explanation_uses_memory_without_extraction_model(tmp_path: Path) -> None:
    service, memory, project_id = workflow(tmp_path)
    memory.create_fact("user_a", project_id, "ups_duration", 2, "h", "explain-fact-0001")
    item = check("check_ups", "持续供电时间", 2, "h")
    memory.save_reviews(
        "user_a",
        project_id,
        "session_old",
        "原设计",
        [item],
        [result(item.item_id)],
        {item.item_id: ["evidence_1"]},
    )
    session = service.create_session("user_a", project_id)
    explained = service.send_message(
        session.session_id, "为什么判断UPS合规？", "explain-review-0001", "user_a"
    )
    assert explained.status == WorkflowStatus.COMPLETED
    assert explained.workflow_stage == "review_explanation"
    assert "evidence_1" in (explained.report_markdown or "")
    assert not explained.errors
