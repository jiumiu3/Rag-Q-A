from app.compliance.judgement_models import ComplianceJudgement
from app.domain.models import (
    CheckItem,
    ComplianceResult,
    ComplianceStatus,
    Evidence,
    RequirementLevel,
)


class ComplianceJudgementValidator:
    """只验证模型引用的证据归属与条款真实性，不改写模型的业务结论。"""

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
        conclusive = judgement.status in {
            ComplianceStatus.COMPLIANT,
            ComplianceStatus.NON_COMPLIANT,
        }
        if conclusive and not bound:
            errors.append("合规或不合规结论必须绑定证据")
        clause_ids = {row.citation.clause_id for row in bound if row.citation.clause_id}
        table_ids = {row.citation.table_id for row in bound if row.citation.table_id}
        if not set(judgement.cited_clause_ids).issubset(clause_ids):
            errors.append("模型引用的条款号与绑定 Evidence 不一致")
        if not set(judgement.cited_table_ids).issubset(table_ids):
            errors.append("模型引用的表号与绑定 Evidence 不一致")
        if errors:
            return self._degraded(
                item, judgement, ComplianceStatus.MANUAL_REVIEW_REQUIRED, errors
            )
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
