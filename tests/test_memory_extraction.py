from pathlib import Path

from app.memory.extractor import MemoryCandidateExtractor
from app.memory.models import (
    ExpressionMode,
    MemoryAction,
    MemoryCandidateDraft,
    MemoryCandidateList,
)
from app.memory.repository import SQLiteMemoryRepository
from app.workflow.graph import WorkflowRunner
from app.workflow.models import PendingAction, WorkflowStatus
from app.workflow.nodes import WorkflowNodes
from app.workflow.repository import SQLiteWorkflowRepository
from app.workflow.service import WorkflowService
from tests.test_m9_workflow import FakeQA
from tests.test_review_memory import check
from tests.test_review_memory import result as compliance_result


class FakeMemoryClient:
    def __init__(self, result: MemoryCandidateList | Exception) -> None:
        self.result = result

    def complete(
        self, _prompt: str, _response_model: type[MemoryCandidateList]
    ) -> MemoryCandidateList:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, list[str]]] = []

    def run(self, state: object, start_node: str | None = None) -> object:
        item_ids = [item.item_id for item in state.check_items]  # type: ignore[attr-defined]
        self.calls.append((start_node, item_ids))
        return state.model_copy(update={"status": WorkflowStatus.SAFE_STOPPED})  # type: ignore[attr-defined,no-any-return]


def service(tmp_path: Path, result: MemoryCandidateList | Exception | None) -> WorkflowService:
    path = tmp_path / "sessions.db"
    memory = SQLiteMemoryRepository(path)
    project = memory.create_project("user_a", "A站", None)
    qa = FakeQA()
    extractor = MemoryCandidateExtractor(FakeMemoryClient(result)) if result is not None else None
    workflow = WorkflowService(
        SQLiteWorkflowRepository(path),
        WorkflowRunner(WorkflowNodes(qa, qa.retrieval)),  # type: ignore[arg-type]
        memory,
        extractor,
    )
    workflow.create_session("user_a", project.project_id)
    return workflow


def test_explicit_candidate_is_applied_before_workflow(tmp_path: Path) -> None:
    result = MemoryCandidateList(
        candidates=[
            MemoryCandidateDraft(
                action=MemoryAction.CREATE,
                fact_key="ups_duration",
                value=2,
                unit="h",
                expression_mode=ExpressionMode.EXPLICIT,
                reason="用户明确给出 UPS 时长",
            )
        ]
    )
    workflow = service(tmp_path, result)
    session_id = workflow.repository.list_sessions("user_a")[0]
    state = workflow.send_message(session_id, "UPS供电时间为2h。", "extract-memory-0001", "user_a")
    facts = workflow.memory_repository.list_facts("user_a", state.project_id or "")  # type: ignore[union-attr]
    assert [(item.fact_key, item.value) for item in facts] == [("ups_duration", 2)]
    assert state.memory_actions[0].status == "applied"


def test_uncertain_candidate_waits_without_overwriting_fact(tmp_path: Path) -> None:
    result = MemoryCandidateList(
        candidates=[
            MemoryCandidateDraft(
                action=MemoryAction.UPDATE,
                fact_key="ups_duration",
                value=1,
                unit="h",
                confidence=0.6,
                expression_mode=ExpressionMode.UNCERTAIN,
                requires_confirmation=True,
                reason="用户表达不确定",
            )
        ]
    )
    workflow = service(tmp_path, result)
    session_id = workflow.repository.list_sessions("user_a")[0]
    project_id = workflow.repository.load(session_id).project_id or ""
    workflow.memory_repository.create_fact(  # type: ignore[union-attr]
        "user_a", project_id, "ups_duration", 2, "h", "seed-fact-0001"
    )
    state = workflow.send_message(session_id, "UPS应该改成1h吧。", "extract-memory-0002", "user_a")
    facts = workflow.memory_repository.list_facts("user_a", project_id)  # type: ignore[union-attr]
    assert facts[0].value == 2
    assert state.pending_action == PendingAction.CONFIRM_MEMORY
    assert state.status == WorkflowStatus.WAITING_CLARIFICATION

    resumed = workflow.confirm_memory(session_id, True, "user_a")
    confirmed = workflow.memory_repository.list_facts("user_a", project_id)  # type: ignore[union-attr]
    assert [(item.value, item.version, item.source_type) for item in confirmed] == [
        (1, 2, "user_confirmed")
    ]
    assert resumed.pending_action != PendingAction.CONFIRM_MEMORY


def test_missing_or_failed_model_safely_stops_without_fact(tmp_path: Path) -> None:
    for index, result in enumerate((None, RuntimeError("model down"))):
        workflow = service(tmp_path / str(index), result)
        session_id = workflow.repository.list_sessions("user_a")[0]
        state = workflow.send_message(
            session_id, "UPS为2h", f"extract-failure-{index:04d}", "user_a"
        )
        assert state.status == WorkflowStatus.SAFE_STOPPED
        assert "MEMORY_EXTRACTION_UNAVAILABLE" in state.errors[-1]
        assert (
            workflow.memory_repository.list_facts(  # type: ignore[union-attr]
                "user_a", state.project_id or ""
            )
            == []
        )


def test_hypothetical_candidate_never_changes_active_fact(tmp_path: Path) -> None:
    result = MemoryCandidateList(
        candidates=[
            MemoryCandidateDraft(
                action=MemoryAction.UPDATE,
                fact_key="ups_duration",
                value=1,
                unit="h",
                expression_mode=ExpressionMode.HYPOTHETICAL,
                requires_confirmation=True,
                reason="假设分析",
            )
        ]
    )
    workflow = service(tmp_path, result)
    session_id = workflow.repository.list_sessions("user_a")[0]
    project_id = workflow.repository.load(session_id).project_id or ""
    fact = workflow.memory_repository.create_fact(  # type: ignore[union-attr]
        "user_a", project_id, "ups_duration", 2, "h", "seed-fact-0002"
    )
    item = check("check_ups", "持续供电时间", 2, "h")
    workflow.memory_repository.save_reviews(  # type: ignore[union-attr]
        "user_a",
        project_id,
        session_id,
        "原设计",
        [item],
        [compliance_result(item.item_id)],
        {item.item_id: ["evidence_1"]},
    )
    state = workflow.send_message(session_id, "如果改成1h呢？", "hypothesis-0001", "user_a")
    current = workflow.memory_repository.list_facts("user_a", project_id)  # type: ignore[union-attr]
    assert [(item.value, item.version) for item in current] == [(2, 1)]
    assert state.pending_hypothesis == {
        "fact_key": "ups_duration",
        "value": 1,
        "unit": "h",
        "base_fact_id": fact.fact_id,
    }


def test_formal_update_executes_only_fact_dependent_check_item(tmp_path: Path) -> None:
    path = tmp_path / "sessions.db"
    memory = SQLiteMemoryRepository(path)
    project = memory.create_project("user_a", "A站", None)
    ups = memory.create_fact(
        "user_a", project.project_id, "ups_duration", 2, "h", "incremental-fact-0001"
    )
    memory.create_fact(
        "user_a", project.project_id, "illumination", 500, "lx", "incremental-fact-0002"
    )
    items = [
        check("check_ups", "持续供电时间", 2, "h"),
        check("check_lux", "照度", 500, "lx"),
    ]
    memory.save_reviews(
        "user_a",
        project.project_id,
        "session_old",
        "原设计",
        items,
        [compliance_result(row.item_id) for row in items],
        {row.item_id: [f"evidence_{row.item_id}"] for row in items},
    )
    candidate = MemoryCandidateList(
        candidates=[
            MemoryCandidateDraft(
                action=MemoryAction.UPDATE,
                fact_key="ups_duration",
                value=1,
                unit="h",
                expression_mode=ExpressionMode.EXPLICIT,
                reason="用户明确修改 UPS 时长",
            )
        ]
    )
    runner = RecordingRunner()
    workflow = WorkflowService(
        SQLiteWorkflowRepository(path),
        runner,  # type: ignore[arg-type]
        memory,
        MemoryCandidateExtractor(FakeMemoryClient(candidate)),
    )
    session = workflow.create_session("user_a", project.project_id)
    workflow.send_message(session.session_id, "UPS改成1h。", "incremental-run-0001", "user_a")

    assert runner.calls == [("plan_retrieval", ["check_ups"])]
    reviews = {row.semantic_key: row for row in memory.list_reviews("user_a", project.project_id)}
    assert reviews["ups|持续供电时间|"].status == "stale"
    assert reviews["控制室|照度|"].status == "valid"
    current_ids = [row.fact_id for row in memory.list_facts("user_a", project.project_id)]
    assert ups.fact_id not in current_ids
