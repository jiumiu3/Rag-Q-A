from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.compliance.evaluator import RuleEvaluator
from app.compliance.models import (
    BooleanRequirementRule,
    CompositeRule,
    EnumRule,
    LookupTableRule,
    ManualReviewRule,
    NumericRangeRule,
    NumericThresholdRule,
)
from app.compliance.units import UnitConversionError, UnitService
from app.domain.models import CharacterSpan, CheckItem, ComplianceStatus, RequirementLevel


def item(value: object, unit: str | None = None, **changes: object) -> CheckItem:
    payload: dict[str, object] = {
        "item_id": "check_1",
        "object": "电缆",
        "attribute": "间距",
        "value": value,
        "unit": unit,
        "source_text": "测试",
        "span": CharacterSpan(start=0, end=2),
    }
    payload.update(changes)
    return CheckItem.model_validate(payload)


def threshold(**changes: object) -> NumericThresholdRule:
    payload: dict[str, object] = {
        "rule_id": "rule_1",
        "subject": "电缆",
        "attribute": "间距",
        "operator": ">=",
        "threshold": Decimal("300"),
        "unit": "mm",
        "requirement_level": RequirementLevel.SHALL,
        "evidence_ids": ["evidence_1"],
    }
    payload.update(changes)
    return NumericThresholdRule.model_validate(payload)


def test_unit_conversion_and_equal_boundary() -> None:
    result = RuleEvaluator().evaluate(item(Decimal("0.3"), "m"), threshold())
    assert result.status == ComplianceStatus.COMPLIANT
    assert result.comparison_trace[0].actual == "300 mm"


def test_incompatible_units_are_insufficient() -> None:
    result = RuleEvaluator().evaluate(item(Decimal("3"), "h"), threshold())
    assert result.status == ComplianceStatus.INSUFFICIENT_INFORMATION
    with pytest.raises(UnitConversionError):
        UnitService().convert(Decimal("1"), "MPa", "mm")


def test_range_exclusive_endpoint() -> None:
    rule = NumericRangeRule(
        rule_id="r",
        subject="压力变送器",
        attribute="压力",
        minimum=Decimal("1"),
        maximum=Decimal("2"),
        unit="MPa",
        include_minimum=False,
        requirement_level="SHALL",
        evidence_ids=["e"],
    )
    result = RuleEvaluator().evaluate(item(Decimal("1"), "MPa", attribute="压力"), rule)
    assert result.status == ComplianceStatus.NON_COMPLIANT


def test_enum_and_advisory_strength() -> None:
    rule = EnumRule(
        rule_id="r",
        subject="仪表",
        attribute="防护等级",
        allowed_values=["IP65"],
        requirement_level="SHOULD",
        evidence_ids=["e"],
    )
    result = RuleEvaluator().evaluate(item("IP65", attribute="防护等级"), rule)
    assert result.status == ComplianceStatus.COMPLIANT
    assert result.advisory


def test_boolean_requirement() -> None:
    rule = BooleanRequirementRule(
        rule_id="r",
        subject="探测器",
        attribute="设置状态",
        expected=True,
        requirement_level="SHALL",
        evidence_ids=["e"],
    )
    assert (
        RuleEvaluator().evaluate(item(True, attribute="设置状态"), rule).status
        == ComplianceStatus.COMPLIANT
    )


def test_unparsed_table_requires_manual_review() -> None:
    rule = LookupTableRule(
        rule_id="r",
        subject="阀门",
        attribute="压力",
        table_id="table_6",
        input_fields=["object"],
        output_field="value",
        parse_status="NEEDS_MANUAL_ANNOTATION",
        requirement_level="SHALL",
        evidence_ids=["e"],
    )
    result = RuleEvaluator().evaluate(item("1.6", object="阀门", attribute="压力"), rule)
    assert result.status == ComplianceStatus.MANUAL_REVIEW_REQUIRED


def test_lookup_requires_unique_row() -> None:
    rule = LookupTableRule(
        rule_id="r",
        subject="阀门",
        attribute="等级",
        table_id="t",
        input_fields=["object"],
        output_field="value",
        parse_status="PARSED",
        rows=[{"object": "阀门", "value": "A"}],
        requirement_level="SHALL",
        evidence_ids=["e"],
    )
    assert (
        RuleEvaluator().evaluate(item("A", object="阀门"), rule).status
        == ComplianceStatus.COMPLIANT
    )


def test_composite_preserves_child_trace() -> None:
    rule = CompositeRule(
        rule_id="c",
        subject="电缆",
        attribute="间距",
        combinator="AND",
        rules=[threshold(), threshold(rule_id="r2", operator="<=", threshold=Decimal("500"))],
        requirement_level="SHALL",
        evidence_ids=["e"],
    )
    result = RuleEvaluator().evaluate(item(Decimal("400"), "mm"), rule)
    assert result.status == ComplianceStatus.COMPLIANT
    assert len(result.comparison_trace[0].children) == 2


def test_manual_and_external_rules_never_compare() -> None:
    manual = ManualReviewRule(
        rule_id="r",
        subject="表格",
        attribute="参数",
        reason="复杂合并单元格",
        requirement_level="SHALL",
        evidence_ids=["e"],
    )
    assert (
        RuleEvaluator().evaluate(item(1), manual).status == ComplianceStatus.MANUAL_REVIEW_REQUIRED
    )
    assert (
        RuleEvaluator().evaluate(item(1), threshold(external_standard_required=True)).status
        == ComplianceStatus.MANUAL_REVIEW_REQUIRED
    )


def test_invalid_range_fails_before_evaluation() -> None:
    with pytest.raises(ValidationError):
        NumericRangeRule(
            rule_id="r",
            subject="x",
            attribute="x",
            minimum=Decimal("2"),
            maximum=Decimal("1"),
            unit="mm",
            requirement_level="SHALL",
            evidence_ids=["e"],
        )
