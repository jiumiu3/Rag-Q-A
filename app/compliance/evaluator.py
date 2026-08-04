import operator
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from app.compliance.models import (
    BooleanRequirementRule,
    CompositeRule,
    EnumRule,
    ExecutableRule,
    LookupTableRule,
    ManualReviewRule,
    NumericRangeRule,
    NumericThresholdRule,
)
from app.compliance.units import UnitConversionError, UnitService
from app.domain.models import (
    CheckItem,
    ComparisonStep,
    ComplianceResult,
    ComplianceStatus,
    RequirementLevel,
    RuleCondition,
)

OPERATORS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


class RuleEvaluator:
    """纯确定性执行器：不调用模型，不接受未校验的自由文本规则。"""

    def __init__(self, units: UnitService | None = None) -> None:
        self.units = units or UnitService()

    def evaluate(self, item: CheckItem, rule: ExecutableRule) -> ComplianceResult:
        if rule.external_standard_required:
            return self._result(
                item, rule, ComplianceStatus.MANUAL_REVIEW_REQUIRED, "规则依赖未上传的外部标准"
            )
        if isinstance(rule, ManualReviewRule):
            return self._result(item, rule, ComplianceStatus.MANUAL_REVIEW_REQUIRED, rule.reason)
        if not self._conditions_match(item, rule.conditions):
            return self._result(
                item, rule, ComplianceStatus.NOT_SPECIFIED, "检查项不满足规则适用条件"
            )
        if isinstance(rule, NumericThresholdRule):
            return self._threshold(item, rule)
        if isinstance(rule, NumericRangeRule):
            return self._range(item, rule)
        if isinstance(rule, EnumRule):
            return self._enum(item, rule)
        if isinstance(rule, BooleanRequirementRule):
            return self._boolean(item, rule)
        if isinstance(rule, LookupTableRule):
            return self._lookup(item, rule)
        if isinstance(rule, CompositeRule):
            return self._composite(item, rule)
        return self._result(item, rule, ComplianceStatus.NOT_SPECIFIED, "不支持的规则类型")

    def _threshold(self, item: CheckItem, rule: NumericThresholdRule) -> ComplianceResult:
        actual = self._numeric(item, rule.unit)
        if isinstance(actual, ComplianceResult):
            return actual.model_copy(update={"evidence_ids": rule.evidence_ids})
        passed = OPERATORS[rule.operator](actual, rule.threshold)
        step = ComparisonStep(
            description="数值阈值比较",
            actual=f"{self._decimal_text(actual)} {rule.unit}",
            required=f"{rule.operator} {self._decimal_text(rule.threshold)} {rule.unit}",
            passed=passed,
        )
        return self._passed(item, rule, passed, [step])

    def _range(self, item: CheckItem, rule: NumericRangeRule) -> ComplianceResult:
        actual = self._numeric(item, rule.unit)
        if isinstance(actual, ComplianceResult):
            return actual.model_copy(update={"evidence_ids": rule.evidence_ids})
        lower = actual >= rule.minimum if rule.include_minimum else actual > rule.minimum
        upper = actual <= rule.maximum if rule.include_maximum else actual < rule.maximum
        step = ComparisonStep(
            description="数值范围比较",
            actual=f"{self._decimal_text(actual)} {rule.unit}",
            required=(
                f"{'[' if rule.include_minimum else '('}{rule.minimum}, {rule.maximum}"
                f"{']' if rule.include_maximum else ')'} {rule.unit}"
            ),
            passed=lower and upper,
        )
        return self._passed(item, rule, lower and upper, [step])

    def _enum(self, item: CheckItem, rule: EnumRule) -> ComplianceResult:
        if item.value is None:
            return self._result(
                item, rule, ComplianceStatus.INSUFFICIENT_INFORMATION, "缺少枚举实际值"
            )
        actual = str(item.value).casefold()
        contains = actual in {value.casefold() for value in rule.allowed_values}
        passed = not contains if rule.forbidden else contains
        step = ComparisonStep(
            description="枚举值比较",
            actual=str(item.value),
            required=str(rule.allowed_values),
            passed=passed,
        )
        return self._passed(item, rule, passed, [step])

    def _boolean(self, item: CheckItem, rule: BooleanRequirementRule) -> ComplianceResult:
        if not isinstance(item.value, bool):
            return self._result(
                item, rule, ComplianceStatus.INSUFFICIENT_INFORMATION, "缺少布尔实际值"
            )
        passed = item.value is rule.expected
        return self._passed(
            item,
            rule,
            passed,
            [
                ComparisonStep(
                    description="布尔要求比较",
                    actual=str(item.value),
                    required=str(rule.expected),
                    passed=passed,
                )
            ],
        )

    def _lookup(self, item: CheckItem, rule: LookupTableRule) -> ComplianceResult:
        if rule.parse_status != "PARSED":
            return self._result(
                item,
                rule,
                ComplianceStatus.MANUAL_REVIEW_REQUIRED,
                "表格未可靠结构化，禁止自动查值",
            )
        values = item.model_dump()
        matches = [
            row
            for row in rule.rows
            if all(
                str(row.get(field)) == str(values.get(field) or item.additional_fields.get(field))
                for field in rule.input_fields
            )
        ]
        if len(matches) != 1:
            return self._result(
                item, rule, ComplianceStatus.MANUAL_REVIEW_REQUIRED, "表格行列无法唯一定位"
            )
        required = matches[0].get(rule.output_field)
        passed = str(item.value) == str(required)
        return self._passed(
            item,
            rule,
            passed,
            [
                ComparisonStep(
                    description="表格唯一行查值",
                    actual=str(item.value),
                    required=str(required),
                    passed=passed,
                )
            ],
        )

    def _composite(self, item: CheckItem, rule: CompositeRule) -> ComplianceResult:
        children = [self.evaluate(item, child) for child in rule.rules]
        if any(child.status == ComplianceStatus.MANUAL_REVIEW_REQUIRED for child in children):
            status = ComplianceStatus.MANUAL_REVIEW_REQUIRED
        elif any(child.status == ComplianceStatus.INSUFFICIENT_INFORMATION for child in children):
            status = ComplianceStatus.INSUFFICIENT_INFORMATION
        else:
            flags = [child.status == ComplianceStatus.COMPLIANT for child in children]
            passed = all(flags) if rule.combinator == "AND" else any(flags)
            status = ComplianceStatus.COMPLIANT if passed else ComplianceStatus.NON_COMPLIANT
        step = ComparisonStep(
            description=f"复合规则 {rule.combinator}",
            passed=status == ComplianceStatus.COMPLIANT,
            children=[step for child in children for step in child.comparison_trace],
        )
        return ComplianceResult(
            item_id=item.item_id,
            status=status,
            comparison_trace=[step],
            evidence_ids=list(
                dict.fromkeys(
                    rule.evidence_ids + [key for child in children for key in child.evidence_ids]
                )
            ),
            advisory=self._advisory(rule.requirement_level),
        )

    def _numeric(self, item: CheckItem, target_unit: str) -> Decimal | ComplianceResult:
        if item.value is None or not item.unit:
            return ComplianceResult(
                item_id=item.item_id,
                status=ComplianceStatus.INSUFFICIENT_INFORMATION,
                limitations=["数值或单位缺失"],
            )
        try:
            value = Decimal(str(item.value))
            return self.units.convert(value, item.unit, target_unit)
        except (InvalidOperation, UnitConversionError) as exc:
            return ComplianceResult(
                item_id=item.item_id,
                status=ComplianceStatus.INSUFFICIENT_INFORMATION,
                limitations=[str(exc)],
            )

    @staticmethod
    def _conditions_match(item: CheckItem, conditions: Sequence[RuleCondition]) -> bool:
        values = item.model_dump()
        for condition in conditions:
            field = condition.field
            actual = values.get(field) or item.additional_fields.get(field)
            expected = condition.value
            operation = condition.operator
            if operation == "==" and str(actual).casefold() != str(expected).casefold():
                return False
            if operation == "!=" and str(actual).casefold() == str(expected).casefold():
                return False
        return True

    def _passed(
        self, item: CheckItem, rule: ExecutableRule, passed: bool, steps: list[ComparisonStep]
    ) -> ComplianceResult:
        # SHALL_NOT/SHOULD_NOT 的否定语义已经编码在 operator/expected/forbidden 中，不在此二次翻转。
        return ComplianceResult(
            item_id=item.item_id,
            status=ComplianceStatus.COMPLIANT if passed else ComplianceStatus.NON_COMPLIANT,
            actual=steps[0].actual,
            required=steps[0].required,
            comparison_trace=steps,
            evidence_ids=rule.evidence_ids,
            advisory=self._advisory(rule.requirement_level),
        )

    @staticmethod
    def _advisory(level: RequirementLevel) -> bool:
        return level in {RequirementLevel.SHOULD, RequirementLevel.SHOULD_NOT, RequirementLevel.MAY}

    @staticmethod
    def _decimal_text(value: Decimal) -> str:
        return format(value, "f")

    def _result(
        self, item: CheckItem, rule: ExecutableRule, status: ComplianceStatus, limitation: str
    ) -> ComplianceResult:
        return ComplianceResult(
            item_id=item.item_id,
            status=status,
            evidence_ids=rule.evidence_ids,
            advisory=self._advisory(rule.requirement_level),
            limitations=[limitation],
        )
