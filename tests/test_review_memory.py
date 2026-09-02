from decimal import Decimal
from pathlib import Path

from app.domain.models import (
    CharacterSpan,
    CheckItem,
    CheckItemStatus,
    ComplianceResult,
    ComplianceStatus,
)
from app.memory.repository import SQLiteMemoryRepository


def check(item_id: str, attribute: str, value: int, unit: str) -> CheckItem:
    return CheckItem(
        item_id=item_id,
        object="控制室" if attribute == "照度" else "UPS",
        attribute=attribute,
        relation="=",
        value=Decimal(value),
        unit=unit,
        source_text=f"{attribute}{value}{unit}",
        span=CharacterSpan(start=0, end=5),
        status=CheckItemStatus.READY,
    )


def result(item_id: str) -> ComplianceResult:
    return ComplianceResult(
        item_id=item_id,
        status=ComplianceStatus.COMPLIANT,
        reasoning="符合要求",
        evidence_ids=[f"evidence_{item_id}"],
    )


def test_review_dependencies_invalidate_only_affected_item(tmp_path: Path) -> None:
    repo = SQLiteMemoryRepository(tmp_path / "sessions.db")
    project = repo.create_project("user_a", "A站", None)
    ups = repo.create_fact("user_a", project.project_id, "ups_duration", 2, "h", "review-fact-0001")
    repo.create_fact("user_a", project.project_id, "illumination", 500, "lx", "review-fact-0002")
    items = [
        check("check_ups", "持续供电时间", 2, "h"),
        check("check_lux", "照度", 500, "lx"),
    ]
    repo.save_reviews(
        "user_a",
        project.project_id,
        "session_x",
        "设计描述",
        items,
        [result("check_ups"), result("check_lux")],
        {"check_ups": ["evidence_up"], "check_lux": ["evidence_lux"]},
    )

    assert {item.status for item in repo.list_reviews("user_a", project.project_id)} == {"valid"}
    repo.update_fact("user_a", project.project_id, ups.fact_id, 1, "h", 1, "review-update-0001")
    by_key = {item.semantic_key: item for item in repo.list_reviews("user_a", project.project_id)}
    assert by_key["ups|持续供电时间|"].status == "stale"
    assert by_key["控制室|照度|"].status == "valid"
    stale = repo.stale_check_items("user_a", project.project_id)
    assert [item.item_id for item in stale] == ["check_ups"]


def test_new_review_supersedes_stale_version(tmp_path: Path) -> None:
    repo = SQLiteMemoryRepository(tmp_path / "sessions.db")
    project = repo.create_project("user_a", "A站", None)
    fact = repo.create_fact(
        "user_a", project.project_id, "ups_duration", 2, "h", "version-fact-0001"
    )
    item = check("check_ups", "持续供电时间", 2, "h")
    repo.save_reviews(
        "user_a",
        project.project_id,
        "session_x",
        "原设计",
        [item],
        [result(item.item_id)],
        {item.item_id: ["evidence_1"]},
    )
    repo.update_fact("user_a", project.project_id, fact.fact_id, 1, "h", 1, "version-update-0001")
    updated = item.model_copy(update={"value": Decimal(1)})
    repo.save_reviews(
        "user_a",
        project.project_id,
        "session_x",
        "修改设计",
        [updated],
        [result(item.item_id)],
        {item.item_id: ["evidence_2"]},
        run_type="incremental",
    )
    reviews = repo.list_reviews("user_a", project.project_id)
    assert sorted((row.review_version, row.status) for row in reviews) == [
        (1, "superseded"),
        (2, "valid"),
    ]
