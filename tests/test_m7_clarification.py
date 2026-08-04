from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.clarification.repository import SQLiteSessionRepository
from app.clarification.service import CompletenessChecker
from app.clarification.session import ClarificationSessionService
from app.core.exceptions import AgentStateError
from app.domain.models import CharacterSpan, CheckItem, CheckItemStatus


def item(**changes: object) -> CheckItem:
    base: dict[str, object] = {
        "item_id": "check_1",
        "object": "仪表电缆",
        "attribute": "间距",
        "value": Decimal("300"),
        "unit": None,
        "location": None,
        "condition": None,
        "source_text": "仪表电缆间距为300",
        "span": CharacterSpan(start=0, end=10),
    }
    base.update(changes)
    return CheckItem.model_validate(base)


def test_checker_merges_shared_missing_fields() -> None:
    first = item()
    second = item(item_id="check_2", object="动力电缆")
    result = CompletenessChecker().check([first, second])
    unit = next(field for field in result.missing_fields if field.field_name == "unit")
    assert unit.related_item_ids == ["check_1", "check_2"]
    assert all(
        entry.status == CheckItemStatus.NEEDS_CLARIFICATION for entry in result.incomplete_items
    )


def test_session_partial_answer_and_resume(tmp_path: Path) -> None:
    service = ClarificationSessionService(SQLiteSessionRepository(tmp_path / "sessions.db"), 2)
    state, token = service.start([item()])
    assert state.pending_request
    resumed = service.resume(state.session_id, token, {"unit": "mm"})
    assert resumed.clarification_round == 1
    assert resumed.pending_request
    assert resumed.items[0].unit == "mm"
    assert any(change["field"] == "unit" for change in resumed.changes)


def test_session_only_updates_related_item(tmp_path: Path) -> None:
    service = ClarificationSessionService(SQLiteSessionRepository(tmp_path / "sessions.db"), 2)
    ready = item(item_id="ready", unit="mm", location="现场", condition="平行敷设")
    state, token = service.start([item(), ready])
    resumed = service.resume(state.session_id, token, {"unit": "m"})
    values = {entry.item_id: entry.unit for entry in resumed.items}
    assert values["ready"] == "mm"
    assert values["check_1"] == "m"


def test_invalid_token_is_rejected(tmp_path: Path) -> None:
    service = ClarificationSessionService(SQLiteSessionRepository(tmp_path / "sessions.db"), 2)
    state, _ = service.start([item()])
    with pytest.raises(AgentStateError, match="无效"):
        service.resume(state.session_id, "wrong", {})


def test_round_limit_stops_clarification(tmp_path: Path) -> None:
    service = ClarificationSessionService(SQLiteSessionRepository(tmp_path / "sessions.db"), 1)
    state, token = service.start([item()])
    resumed = service.resume(state.session_id, token, {"unit": "mm"})
    assert resumed.pending_request is None
    assert "INSUFFICIENT_INFORMATION" in resumed.items[0].uncertainties


def test_expired_session_is_rejected(tmp_path: Path) -> None:
    repository = SQLiteSessionRepository(tmp_path / "sessions.db", ttl_minutes=-1)
    service = ClarificationSessionService(repository, 2)
    state, token = service.start([item()])
    assert state.expires_at < datetime.now(UTC)
    with pytest.raises(AgentStateError, match="过期"):
        service.resume(state.session_id, token, {})
