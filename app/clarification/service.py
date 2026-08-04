from collections import defaultdict
from collections.abc import Sequence

from app.clarification.models import CompletenessResult, RuleTemplate
from app.domain.models import (
    CheckItem,
    CheckItemStatus,
    ClarificationRequest,
    Evidence,
    MissingField,
)

DEFAULT_TEMPLATES = (
    RuleTemplate(
        template_id="cable_spacing",
        attributes={"间距", "净距"},
        required_fields=["object", "value", "unit", "location", "condition"],
        reasons={
            "object": "需明确两类电缆或设备对象",
            "value": "需提供实际间距",
            "unit": "数值比较必须有单位",
            "location": "敷设位置影响适用条款",
            "condition": "需明确平行、交叉或危险区等条件",
        },
        examples={"unit": ["mm", "m"], "condition": ["平行敷设", "交叉敷设"]},
    ),
    RuleTemplate(
        template_id="illuminance",
        attributes={"照度"},
        required_fields=["value", "unit", "location"],
        reasons={
            "value": "需提供实际照度",
            "unit": "照度单位应为 lx",
            "location": "不同房间要求不同",
        },
        examples={"unit": ["lx"], "location": ["控制室", "机柜间"]},
    ),
    RuleTemplate(
        template_id="power_duration",
        attributes={"供电时间"},
        required_fields=["object", "value", "unit", "condition"],
        reasons={
            "object": "需明确供电对象",
            "value": "需提供持续时间",
            "unit": "持续时间需要 min 或 h",
            "condition": "需明确正常或事故工况",
        },
    ),
    RuleTemplate(
        template_id="grounding_area",
        attributes={"接地截面积"},
        required_fields=["object", "value", "unit", "material"],
        reasons={
            "object": "需明确接地导体",
            "value": "需提供截面积",
            "unit": "截面积应带 mm²",
            "material": "导体材质影响要求",
        },
        examples={"material": ["铜", "镀锌钢"]},
    ),
    RuleTemplate(
        template_id="numeric",
        attributes={"压力", "温度", "电压"},
        required_fields=["object", "value", "unit"],
        reasons={"object": "需明确设备对象", "value": "需提供实际值", "unit": "数值比较必须有单位"},
    ),
)


class CompletenessChecker:
    def __init__(self, templates: Sequence[RuleTemplate] = DEFAULT_TEMPLATES) -> None:
        self.templates = templates

    def check(
        self, items: Sequence[CheckItem], evidence: Sequence[Evidence] = ()
    ) -> CompletenessResult:
        missing_by_field: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
        reasons: dict[tuple[str, str], tuple[str, list[str]]] = {}
        ready: list[CheckItem] = []
        incomplete: list[CheckItem] = []
        evidence_fields = self._evidence_required_fields(evidence)
        for item in items:
            template = next(
                (entry for entry in self.templates if item.attribute in entry.attributes), None
            )
            required = list(template.required_fields if template else ["object", "value"])
            required.extend(field for field in evidence_fields if field not in required)
            item_missing = []
            for field in required:
                value = self._field_value(item, field)
                if value is None or value == "" or field in item.inferred_fields:
                    key = (field, template.template_id if template else "evidence")
                    missing_by_field[key].append(item.item_id)
                    reason = (
                        template.reasons.get(field, "检索证据表明该字段是规则判断前提")
                        if template
                        else "规则判断需要该字段"
                    )
                    examples = template.examples.get(field, []) if template else []
                    reasons[key] = (reason, examples)
                    item_missing.append(field)
            if item_missing:
                incomplete.append(
                    item.model_copy(update={"status": CheckItemStatus.NEEDS_CLARIFICATION})
                )
            else:
                ready.append(item.model_copy(update={"status": CheckItemStatus.READY}))
        missing = [
            MissingField(
                field_name=field,
                reason=reasons[key][0],
                expected_type="string_or_number",
                examples=reasons[key][1],
                related_item_ids=list(dict.fromkeys(ids)),
            )
            for key, ids in missing_by_field.items()
            for field in [key[0]]
        ]
        return CompletenessResult(
            ready_items=ready, incomplete_items=incomplete, missing_fields=missing
        )

    @staticmethod
    def _field_value(item: CheckItem, field: str) -> object | None:
        if hasattr(item, field):
            value: object = getattr(item, field)
            return value
        return item.additional_fields.get(field)

    @staticmethod
    def _evidence_required_fields(evidence: Sequence[Evidence]) -> set[str]:
        fields: set[str] = set()
        keywords = {
            "材质": "material",
            "区域": "condition",
            "位置": "location",
            "工况": "condition",
        }
        for item in evidence:
            for keyword, field in keywords.items():
                if keyword in item.content:
                    fields.add(field)
        return fields


class ClarificationPlanner:
    def plan(
        self, missing: Sequence[MissingField], resume_token: str
    ) -> ClarificationRequest | None:
        if not missing:
            return None
        labels = "、".join(dict.fromkeys(field.field_name for field in missing))
        related = sorted({item for field in missing for item in field.related_item_ids})
        question = f"为了判断检查项 {', '.join(related)}，请补充：{labels}。"
        return ClarificationRequest(
            question=question, requested_fields=list(missing), resume_token=resume_token
        )
