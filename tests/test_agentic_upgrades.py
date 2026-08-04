from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.compliance.candidates import DeterministicCandidateExtractor
from app.compliance.matcher import RuleMatcher
from app.compliance.models import CandidateRuleStatus
from app.compliance.repository import SQLiteRuleRepository
from app.domain.models import (
    CharacterSpan,
    CheckItem,
    Evidence,
    EvidenceStatus,
    SourceCitation,
    SourceSpan,
)
from app.retrieval.planner import AdaptiveRetrievalPlanner


def evidence(text: str = "在无人值守站场，控制系统UPS供电时间不应低于2 h。") -> Evidence:
    span = SourceSpan(document_id="doc_test", page_number=12)
    return Evidence(
        evidence_id="evidence_test",
        unit_id="unit_test",
        content=text,
        citation=SourceCitation(
            standard_code="Q/GGW 02005.1-2022",
            file_name="test.pdf",
            clause_id="13.5.1",
            page_number=12,
            quote=text,
            source_span=span,
        ),
        retrieval_sources=["exact"],
        support_type=EvidenceStatus.SUFFICIENT,
    )


def item(**changes: object) -> CheckItem:
    payload: dict[str, object] = {
        "item_id": "item_1",
        "object": "UPS",
        "attribute": "供电时间",
        "value": Decimal("2"),
        "unit": "h",
        "source_text": "UPS供电时间2h",
        "span": CharacterSpan(start=0, end=9),
        "additional_fields": {"station_mode": "无人值守站场"},
    }
    payload.update(changes)
    return CheckItem.model_validate(payload)


def test_retry_rewrites_with_synonyms_and_decomposes() -> None:
    planner = AdaptiveRetrievalPlanner()
    original = "控制室照度是否符合，UPS供电时间是否足够？"
    first = planner.create(original, [], 0, None, [])
    retry = planner.create(original, [], 1, None, first.subqueries)
    assert len(first.subqueries) == 2
    assert retry.subqueries != first.subqueries
    assert "不间断电源" in " ".join(retry.subqueries)


def test_exact_identifier_is_preserved_without_semantic_fallback() -> None:
    plan = AdaptiveRetrievalPlanner().create(
        "Q/GGW 02005.1-2022 第99.99.99条是什么？", [], 1, None, []
    )
    assert plan.retrievers == ["exact"]
    assert "99.99.99" in plan.query


def test_candidate_confirmation_persists_and_pending_cannot_execute(tmp_path: Path) -> None:
    repository = SQLiteRuleRepository(tmp_path / "rules.db")
    candidate = repository.save_candidate(
        DeterministicCandidateExtractor().extract([evidence()])[0]
    )
    assert candidate.operator == ">="
    assert candidate.expected_value == Decimal("2")
    assert candidate.conditions
    with pytest.raises(ValueError, match="confirmed"):
        candidate.to_executable()
    confirmed = repository.review(
        candidate.candidate_rule_id,
        CandidateRuleStatus.CONFIRMED,
        "reviewer",
        "确认并规范对象名",
        {"subject": "UPS"},
    )
    assert confirmed.reviewed_at
    assert confirmed.version == 2
    reopened = SQLiteRuleRepository(tmp_path / "rules.db")
    rules = reopened.list_rules()
    assert len(rules) == 1
    assert rules[0].evidence_ids == ["evidence_test"]
    loaded = reopened.get_candidate(candidate.candidate_rule_id)
    assert loaded is not None
    assert loaded.created_at <= datetime.now(UTC)


def test_rule_match_is_order_independent_and_reports_missing_condition(tmp_path: Path) -> None:
    repository = SQLiteRuleRepository(tmp_path / "rules.db")
    candidate = repository.save_candidate(
        DeterministicCandidateExtractor().extract([evidence()])[0]
    )
    repository.review(candidate.candidate_rule_id, CandidateRuleStatus.CONFIRMED, "reviewer")
    rule = repository.list_rules()[0]
    assert RuleMatcher().match(item(), [rule]).status == "matched"
    assert RuleMatcher().match(item(additional_fields={}), [rule]).status == "missing_condition"
