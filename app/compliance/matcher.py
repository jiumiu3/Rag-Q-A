from app.compliance.models import ExecutableRule, RuleMatch
from app.domain.models import CheckItem


class RuleMatcher:
    def match(self, item: CheckItem, rules: list[ExecutableRule]) -> RuleMatch:
        candidates = [
            rule
            for rule in rules
            if rule.subject.casefold() == item.object.casefold()
            and rule.attribute.casefold() == item.attribute.casefold()
        ]
        if not candidates:
            return RuleMatch(check_item_id=item.item_id, status="not_found")
        applicable, missing = [], []
        values = item.model_dump()
        for rule in candidates:
            absent = [
                condition.field
                for condition in rule.conditions
                if values.get(condition.field) is None
                and item.additional_fields.get(condition.field) is None
            ]
            if absent:
                missing.extend(absent)
            else:
                applicable.append(rule)
        if not applicable:
            return RuleMatch(
                check_item_id=item.item_id,
                matched_rule_ids=[r.rule_id for r in candidates],
                missing_conditions=list(dict.fromkeys(missing)),
                status="missing_condition",
            )
        signatures = {
            rule.model_dump_json(exclude={"rule_id", "evidence_ids"}) for rule in applicable
        }
        if len(applicable) > 1:
            status = "conflict" if len(signatures) > 1 else "ambiguous"
            return RuleMatch(
                check_item_id=item.item_id,
                matched_rule_ids=[r.rule_id for r in applicable],
                conflicts=["多个已确认规则同时命中"] if status == "conflict" else [],
                status=status,
            )
        return RuleMatch(
            check_item_id=item.item_id,
            matched_rule_ids=[applicable[0].rule_id],
            match_score=1.0,
            status="matched",
        )
