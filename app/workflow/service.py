import secrets
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from app.domain.models import CheckItem, CheckItemStatus
from app.workflow.graph import WorkflowRunner
from app.workflow.models import (
    AgentState,
    WorkflowConfirmRequest,
    WorkflowMessage,
    WorkflowStatus,
)
from app.workflow.repository import SQLiteWorkflowRepository


class WorkflowService:
    def __init__(self, repository: SQLiteWorkflowRepository, runner: WorkflowRunner) -> None:
        self.repository = repository
        self.runner = runner

    def create_session(self) -> AgentState:
        now = datetime.now(UTC)
        state = AgentState(
            session_id=f"session_{secrets.token_hex(10)}",
            request_id=f"request_{secrets.token_hex(10)}",
            created_at=now,
            updated_at=now,
        )
        self.repository.save(state)
        return state

    def send_message(self, session_id: str, content: str, idempotency_key: str) -> AgentState:
        cached = self.repository.get_idempotent(session_id, idempotency_key)
        if cached:
            return cached
        previous = self.repository.load(session_id)
        now = datetime.now(UTC)
        state = previous.model_copy(
            update={
                "request_id": f"request_{secrets.token_hex(10)}",
                "status": WorkflowStatus.NEW,
                "current_node": "normalize_input",
                "input_text": content,
                "normalized_input": "",
                "intent": None,
                "check_items": [],
                "evidence": [],
                "rules": [],
                "candidate_rules": [],
                "compliance_results": [],
                "answer": None,
                "clarification": None,
                "pending_action": None,
                "report_markdown": None,
                "errors": [],
                "retrieval_retry_count": 0,
                "original_query": "",
                "current_query": "",
                "query_history": [],
                "retrieval_plan": None,
                "retrieval_attempts": [],
                "evidence_assessments": [],
                "question_analysis": None,
                "retrieval_goals": [],
                "retrieval_step_count": 0,
                "evidence_coverage": [],
                "retrieval_tool_calls": [],
                "validation_retry_count": 0,
                "messages": previous.messages
                + [WorkflowMessage(role="user", content=content, created_at=now)],
                "updated_at": now,
            }
        )
        state = self.runner.run(state)
        self.repository.save(state)
        self.repository.save_idempotent(session_id, idempotency_key, state)
        return state

    def confirm(self, session_id: str, request: WorkflowConfirmRequest) -> AgentState:
        state = self.repository.load(session_id)
        items = [
            item.model_copy(update={"status": CheckItemStatus.CONFIRMED}) for item in request.items
        ]
        state = state.model_copy(
            update={
                "check_items": items,
                "rules": request.rules,
                "status": WorkflowStatus.RUNNING,
                "pending_action": None,
            }
        )
        state = self.runner.run(state, "check_completeness")
        self.repository.save(state)
        return state

    def clarify(self, session_id: str, answers: dict[str, str | int | float | bool]) -> AgentState:
        state = self.repository.load(session_id)
        if not state.clarification:
            return state
        items = {item.item_id: item for item in state.check_items}
        for missing in state.clarification.requested_fields:
            if missing.field_name not in answers:
                continue
            for item_id in missing.related_item_ids:
                if item_id in items:
                    items[item_id] = self._update(
                        items[item_id], missing.field_name, answers[missing.field_name]
                    )
        state = state.model_copy(
            update={
                "check_items": list(items.values()),
                "clarification": None,
                "clarification_count": state.clarification_count + 1,
                "status": WorkflowStatus.RUNNING,
                "pending_action": None,
            }
        )
        state = self.runner.run(state, "check_completeness")
        self.repository.save(state)
        return state

    def replay(self, session_id: str, start_node: str | None = None) -> AgentState:
        state = self.repository.load(session_id)
        replayed = self.runner.run(state, start_node or state.current_node)
        self.repository.save(replayed)
        return replayed

    @staticmethod
    def _update(item: CheckItem, field: str, value: str | int | float | bool) -> CheckItem:
        if field == "value":
            try:
                converted: object = Decimal(str(value))
            except InvalidOperation:
                converted = value
            return item.model_copy(update={"value": converted, "user_corrected": True})
        if field in {"object", "unit", "location", "condition", "relation"}:
            return item.model_copy(update={field: str(value), "user_corrected": True})
        additional = dict(item.additional_fields)
        additional[field] = value if not isinstance(value, float) else Decimal(str(value))
        return item.model_copy(update={"additional_fields": additional, "user_corrected": True})
