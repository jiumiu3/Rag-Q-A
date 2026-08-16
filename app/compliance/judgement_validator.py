import json
import re
from decimal import Decimal, InvalidOperation

from app.compliance.judgement_models import ComplianceJudgement
from app.compliance.units import UnitConversionError, UnitService
from app.domain.models import (
    CheckItem,
    ComparisonStep,
    ComplianceResult,
    ComplianceStatus,
    Evidence,
    EvidenceStatus,
    RequirementLevel,
)

NUMBER_RE = re.compile(r"(?<![\w.])[-+]?(?:\d+(?:\.\d+)?|\.\d+)")


class ComplianceJudgementValidator:
    """校验证据归属，并对可结构化数值比较重新计算。"""

    def __init__(self, units: UnitService | None = None) -> None:
        self.units = units or UnitService()

    def validate(
        self,
        item: CheckItem,
        judgement: ComplianceJudgement,
        evidence: list[Evidence],
        allowed_evidence_ids: list[str],
    ) -> ComplianceResult:
        by_id = {row.evidence_id: row for row in evidence}
        allowed = set(allowed_evidence_ids)
        errors: list[str] = []
        if judgement.check_item_id != item.item_id:
            errors.append("模型返回的 check_item_id 与当前检查项不一致")
        if not set(judgement.evidence_ids).issubset(allowed):
            errors.append("模型引用了未绑定到当前检查项的 Evidence")
        bound = [by_id[key] for key in judgement.evidence_ids if key in allowed and key in by_id]
        available = [by_id[key] for key in allowed if key in by_id]
        conclusive = judgement.status in {
            ComplianceStatus.COMPLIANT,
            ComplianceStatus.NON_COMPLIANT,
        }
        if conclusive and not bound:
            errors.append("合规或不合规结论必须绑定证据")
        if any(row.support_type == EvidenceStatus.CONFLICTING for row in available):
            return self._degraded(item, judgement, ComplianceStatus.CONFLICT, ["绑定证据存在冲突"])
        if any(row.support_type == EvidenceStatus.PARTIAL for row in bound):
            return self._degraded(
                item, judgement, ComplianceStatus.MANUAL_REVIEW_REQUIRED, ["绑定证据未完整结构化"]
            )
        if judgement.actual is not None and item.value is not None:
            if self._normalized(judgement.actual) != self._normalized(str(item.value)):
                errors.append("actual 与 CheckItem.value 不一致")
        source_text = " ".join(row.content for row in bound)
        clause_ids = {row.citation.clause_id for row in bound if row.citation.clause_id}
        table_ids = {row.citation.table_id for row in bound if row.citation.table_id}
        if not set(judgement.cited_clause_ids).issubset(clause_ids):
            errors.append("模型引用的条款号与绑定 Evidence 不一致")
        if not set(judgement.cited_table_ids).issubset(table_ids):
            errors.append("模型引用的表号与绑定 Evidence 不一致")
        if judgement.required:
            for number in NUMBER_RE.findall(judgement.required):
                if number not in source_text:
                    errors.append(f"要求数值未出现在绑定证据中：{number}")
        if errors:
            return self._degraded(
                item, judgement, ComplianceStatus.MANUAL_REVIEW_REQUIRED, errors
            )
        deterministic = self._numeric_result(item, judgement)
        if deterministic is not None:
            return deterministic
        deterministic = self._non_numeric_result(item, judgement)
        if deterministic is not None:
            return deterministic
        return ComplianceResult(
            item_id=item.item_id,
            status=judgement.status,
            reasoning=judgement.reasoning,
            actual=judgement.actual or (None if item.value is None else str(item.value)),
            required=judgement.required,
            comparison_trace=judgement.comparison_trace,
            evidence_ids=judgement.evidence_ids,
            advisory=judgement.requirement_level
            in {RequirementLevel.SHOULD, RequirementLevel.SHOULD_NOT},
            limitations=judgement.limitations,
        )

    def _non_numeric_result(
        self, item: CheckItem, judgement: ComplianceJudgement
    ) -> ComplianceResult | None:
        if item.value is None or judgement.required is None or judgement.operator is None:
            return None
        actual = item.value
        required_values: list[object]
        if judgement.operator in {"in", "not_in"}:
            try:
                decoded = json.loads(judgement.required)
            except json.JSONDecodeError:
                decoded = [part.strip() for part in re.split(r"[,，;；]", judgement.required)]
            required_values = decoded if isinstance(decoded, list) else [decoded]
            normalized = {self._normalized(str(value)) for value in required_values}
            contained = self._normalized(str(actual)) in normalized
            passed = contained if judgement.operator == "in" else not contained
        elif isinstance(actual, bool) and judgement.operator in {"==", "!="}:
            expected_text = self._normalized(judgement.required)
            if expected_text not in {"true", "false", "是", "否", "1", "0"}:
                return None
            expected = expected_text in {"true", "是", "1"}
            passed = actual == expected if judgement.operator == "==" else actual != expected
            required_values = [expected]
        else:
            return None
        status = ComplianceStatus.COMPLIANT if passed else ComplianceStatus.NON_COMPLIANT
        return ComplianceResult(
            item_id=item.item_id,
            status=status,
            reasoning=judgement.reasoning,
            actual=str(actual),
            required=f"{judgement.operator} {judgement.required}",
            comparison_trace=[
                ComparisonStep(
                    description="代码重新执行枚举或布尔比较",
                    actual=str(actual),
                    required=str(required_values),
                    passed=passed,
                )
            ],
            evidence_ids=judgement.evidence_ids,
            advisory=judgement.requirement_level
            in {RequirementLevel.SHOULD, RequirementLevel.SHOULD_NOT},
            limitations=judgement.limitations,
        )

    def _numeric_result(
        self, item: CheckItem, judgement: ComplianceJudgement
    ) -> ComplianceResult | None:
        if item.value is None or not judgement.required or not judgement.operator:
            return None
        actual_match = NUMBER_RE.search(str(item.value))
        required_match = NUMBER_RE.search(judgement.required)
        if not actual_match or not required_match or judgement.operator in {"in", "not_in"}:
            return None
        try:
            actual = Decimal(actual_match.group())
            required = Decimal(required_match.group())
            if judgement.required_unit:
                if not item.unit:
                    return self._degraded(
                        item,
                        judgement,
                        ComplianceStatus.INSUFFICIENT_INFORMATION,
                        ["检查项缺少数值单位"],
                    )
                actual = self.units.convert(actual, item.unit, judgement.required_unit)
        except (InvalidOperation, UnitConversionError) as exc:
            return self._degraded(
                item, judgement, ComplianceStatus.INSUFFICIENT_INFORMATION, [str(exc)]
            )
        operations = {
            ">": actual > required,
            ">=": actual >= required,
            "<": actual < required,
            "<=": actual <= required,
            "==": actual == required,
            "!=": actual != required,
        }
        passed = operations[judgement.operator]
        status = ComplianceStatus.COMPLIANT if passed else ComplianceStatus.NON_COMPLIANT
        advisory = judgement.requirement_level in {
            RequirementLevel.SHOULD,
            RequirementLevel.SHOULD_NOT,
        }
        return ComplianceResult(
            item_id=item.item_id,
            status=status,
            reasoning=judgement.reasoning,
            actual=f"{actual} {judgement.required_unit or item.unit or ''}".strip(),
            required=(
                f"{judgement.operator} {required} "
                f"{judgement.required_unit or item.unit or ''}"
            ).strip(),
            comparison_trace=[
                ComparisonStep(
                    description="代码重新执行数值与单位比较",
                    actual=str(actual),
                    required=f"{judgement.operator} {required}",
                    passed=passed,
                )
            ],
            evidence_ids=judgement.evidence_ids,
            advisory=advisory,
            limitations=judgement.limitations,
        )

    @staticmethod
    def _normalized(value: str) -> str:
        return re.sub(r"\s+", "", value).casefold()

    @staticmethod
    def _degraded(
        item: CheckItem,
        judgement: ComplianceJudgement,
        status: ComplianceStatus,
        limitations: list[str],
    ) -> ComplianceResult:
        return ComplianceResult(
            item_id=item.item_id,
            status=status,
            reasoning=judgement.reasoning,
            actual=None if item.value is None else str(item.value),
            required=judgement.required,
            evidence_ids=judgement.evidence_ids,
            advisory=judgement.requirement_level
            in {RequirementLevel.SHOULD, RequirementLevel.SHOULD_NOT},
            limitations=[*judgement.limitations, *limitations],
        )
