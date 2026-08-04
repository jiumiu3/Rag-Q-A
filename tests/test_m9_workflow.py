from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.domain.models import EvidenceStatus, IntentType
from app.qa.models import AnswerDraft
from app.workflow.graph import WorkflowRunner, graph_mermaid
from app.workflow.models import AgentState, WorkflowConfirmRequest, WorkflowStatus
from app.workflow.nodes import WorkflowNodes
from app.workflow.repository import SQLiteWorkflowRepository
from app.workflow.service import WorkflowService


class FakeRetrieval:
    def retrieve(self, _query: str, _top_k: int = 5) -> SimpleNamespace:
        return SimpleNamespace(evidence=[])


class FakeQA:
    def __init__(self) -> None:
        self.retrieval = FakeRetrieval()

    def ask(self, _question: str, _top_k: int = 5) -> tuple[AnswerDraft, SimpleNamespace]:
        answer = AnswerDraft(
            intent=IntentType.KNOWLEDGE_QA,
            status=EvidenceStatus.NOT_FOUND,
            conclusion="未检索到证据",
        )
        return answer, SimpleNamespace(evidence=[])


def workflow(tmp_path: Path) -> WorkflowService:
    qa = FakeQA()
    nodes = WorkflowNodes(qa, qa.retrieval)  # type: ignore[arg-type]
    return WorkflowService(
        SQLiteWorkflowRepository(tmp_path / "workflow.db"), WorkflowRunner(nodes)
    )


def state(text: str) -> AgentState:
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_test",
        request_id="request_test",
        input_text=text,
        created_at=now,
        updated_at=now,
    )


def test_query_routes_complete_with_trace() -> None:
    qa = FakeQA()
    runner = WorkflowRunner(WorkflowNodes(qa, qa.retrieval))  # type: ignore[arg-type]
    for query, intent in (
        ("什么是SCADA？", IntentType.KNOWLEDGE_QA),
        ("第5.2.1条是什么？", IntentType.CLAUSE_LOOKUP),
        ("表6是什么？", IntentType.TABLE_LOOKUP),
    ):
        result = runner.run(state(query))
        assert result.intent == intent
        assert result.status == WorkflowStatus.COMPLETED
        assert result.workflow_trace


def test_checklist_route_completes() -> None:
    qa = FakeQA()
    result = WorkflowRunner(WorkflowNodes(qa, qa.retrieval)).run(  # type: ignore[arg-type]
        state("生成控制室照度和UPS供电时间检查清单")
    )
    assert result.intent == IntentType.CHECKLIST_GENERATION
    assert result.status == WorkflowStatus.COMPLETED
    assert result.report_markdown.endswith("## 证据来源\n\n无可用规范证据。")


def test_compliance_review_pauses_for_confirmation(tmp_path: Path) -> None:
    service = workflow(tmp_path)
    created = service.create_session()
    result = service.send_message(
        created.session_id,
        "请审查：压力变送器设计压力为1.6MPa。",
        "idempotency-0001",
    )
    assert result.intent == IntentType.COMPLIANCE_REVIEW
    assert result.status == WorkflowStatus.WAITING_CONFIRMATION
    assert result.check_items


def test_retrieval_retries_are_bounded(tmp_path: Path) -> None:
    service = workflow(tmp_path)
    created = service.create_session()
    paused = service.send_message(
        created.session_id,
        "请审查：压力变送器设计压力为1.6MPa。",
        "idempotency-0002",
    )
    result = service.confirm(
        created.session_id,
        WorkflowConfirmRequest(items=paused.check_items),
    )
    assert result.status == WorkflowStatus.SAFE_STOPPED
    assert result.retrieval_retry_count == 2
    assert "检索重试上限" in result.errors[-1]


def test_idempotent_message_and_session_isolation(tmp_path: Path) -> None:
    service = workflow(tmp_path)
    first = service.create_session()
    second = service.create_session()
    one = service.send_message(first.session_id, "什么是SCADA？", "idempotency-0003")
    repeated = service.send_message(first.session_id, "不同内容", "idempotency-0003")
    assert repeated.model_dump() == one.model_dump()
    assert service.repository.load(second.session_id).messages == []


def test_graph_can_be_printed() -> None:
    diagram = graph_mermaid()
    assert "normalize_input" in diagram
    assert "generate_report" in diagram
