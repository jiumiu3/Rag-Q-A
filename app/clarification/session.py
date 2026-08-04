import secrets
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from app.clarification.models import CompletenessResult, SessionState
from app.clarification.repository import SQLiteSessionRepository
from app.clarification.service import ClarificationPlanner, CompletenessChecker
from app.domain.models import CheckItem, CheckItemStatus, ComplianceStatus


class ClarificationSessionService:
    def __init__(self, repository: SQLiteSessionRepository, max_rounds: int = 2) -> None:
        self.repository = repository
        self.max_rounds = max_rounds
        self.checker = CompletenessChecker()
        self.planner = ClarificationPlanner()

    def start(self, items: list[CheckItem]) -> tuple[SessionState, str]:
        now = datetime.now(UTC)
        state = SessionState(
            session_id=f"session_{secrets.token_hex(10)}",
            items=items,
            max_rounds=self.max_rounds,
            created_at=now,
            updated_at=now,
            expires_at=self.repository.new_expiry(),
        )
        state, token = self.repository.create(state)
        checked = self.checker.check(state.items)
        state = self._with_check(state, checked, token)
        self.repository.save(state, token)
        return state, token

    def resume(
        self, session_id: str, token: str, answers: dict[str, str | int | float | bool]
    ) -> SessionState:
        state = self.repository.load(session_id, token)
        pending = state.pending_request
        if not pending:
            return state
        changes = list(state.changes)
        items = {item.item_id: item for item in state.items}
        for missing in pending.requested_fields:
            if missing.field_name not in answers:
                continue
            value = answers[missing.field_name]
            for item_id in missing.related_item_ids:
                item = items.get(item_id)
                if not item:
                    continue
                old = self._value(item, missing.field_name)
                items[item_id] = self._update(item, missing.field_name, value)
                changes.append(
                    {
                        "item_id": item_id,
                        "field": missing.field_name,
                        "old": str(old),
                        "new": str(value),
                        "source": "user",
                    }
                )
        checked = self.checker.check(list(items.values()))
        rounds = state.clarification_round + 1
        state = state.model_copy(
            update={
                "items": checked.ready_items + checked.incomplete_items,
                "clarification_round": rounds,
                "changes": changes,
                "updated_at": datetime.now(UTC),
                "expires_at": self.repository.new_expiry(),
            }
        )
        if checked.missing_fields and rounds >= state.max_rounds:
            state = state.model_copy(
                update={
                    "pending_request": None,
                    "items": [
                        item.model_copy(
                            update={
                                "status": CheckItemStatus.NEEDS_CLARIFICATION,
                                "uncertainties": item.uncertainties
                                + [ComplianceStatus.INSUFFICIENT_INFORMATION],
                            }
                        )
                        for item in state.items
                    ],
                }
            )
        else:
            state = self._with_check(state, checked, token)
        self.repository.save(state, token)
        return state

    def _with_check(
        self, state: SessionState, checked: CompletenessResult, token: str
    ) -> SessionState:
        request = self.planner.plan(checked.missing_fields, token)
        return state.model_copy(
            update={
                "items": checked.ready_items + checked.incomplete_items,
                "pending_request": request,
            }
        )

    @staticmethod
    def _value(item: CheckItem, field: str) -> object | None:
        return getattr(item, field) if hasattr(item, field) else item.additional_fields.get(field)

    @staticmethod
    def _update(item: CheckItem, field: str, value: str | int | float | bool) -> CheckItem:
        if field in {"value"}:
            try:
                converted: object = Decimal(str(value))
            except InvalidOperation:
                converted = value
            return item.model_copy(update={field: converted, "user_corrected": True})
        if hasattr(item, field):
            return item.model_copy(update={field: value, "user_corrected": True})
        additional = dict(item.additional_fields)
        additional[field] = value if not isinstance(value, float) else Decimal(str(value))
        return item.model_copy(update={"additional_fields": additional, "user_corrected": True})
