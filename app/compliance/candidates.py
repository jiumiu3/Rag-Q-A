import hashlib
import re
from datetime import UTC, datetime
from decimal import Decimal

from app.compliance.models import CandidateRule
from app.domain.models import Evidence, RequirementLevel, RuleCondition

NUMBER = r"(\d+(?:\.\d+)?)\s*(h|s|m|mm|MPa|kPa|lx|℃|%)?"


class DeterministicCandidateExtractor:
    def extract(self, evidence: list[Evidence]) -> list[CandidateRule]:
        return [self._extract_one(item) for item in evidence]

    def _extract_one(self, evidence: Evidence) -> CandidateRule:
        text = evidence.content.strip()
        level = self._level(text)
        rule_type = "MANUAL_REVIEW"
        operator = None
        expected: Decimal | None = None
        unit = None
        lower = upper = None
        allowed: list[str] = []
        expected_boolean = None
        uncertainties: list[str] = []
        range_match = re.search(rf"{NUMBER}\s*(?:至|到|～|~|—|-)+\s*{NUMBER}", text, re.I)
        threshold = re.search(
            rf"(不应低于|不得小于|至少|不小于|应不低于|不得超过|不应超过|不大于|至多)\s*{NUMBER}",
            text,
            re.I,
        )
        enum_match = re.search(r"(?:应采用|应为|可采用)[：:]?([^。；]+?)(?:之一|。|；|$)", text)
        if range_match:
            rule_type = "NUMERIC_RANGE"
            lower, upper = Decimal(range_match.group(1)), Decimal(range_match.group(3))
            unit = range_match.group(2) or range_match.group(4)
        elif threshold:
            rule_type = "NUMERIC_THRESHOLD"
            phrase, value, unit = threshold.group(1), threshold.group(2), threshold.group(3)
            operator = (
                ">=" if phrase in {"不应低于", "不得小于", "至少", "不小于", "应不低于"} else "<="
            )
            expected = Decimal(value)
        elif re.search(r"(?:不得|禁止|不应)(?:设置|安装|采用|配置)", text):
            rule_type, expected_boolean = "BOOLEAN_REQUIREMENT", False
        elif re.search(r"(?:应|必须)(?:设置|安装|采用|配置)", text):
            rule_type, expected_boolean = "BOOLEAN_REQUIREMENT", True
        elif enum_match and any(mark in enum_match.group(1) for mark in ("、", "或")):
            rule_type = "ENUM"
            allowed = [x.strip() for x in re.split(r"、|或", enum_match.group(1)) if x.strip()]
        if re.search(r"按(?:照)?(?:表|附录)", text):
            rule_type = "LOOKUP_TABLE"
            uncertainties.append("查表规则需确认表格已可靠结构化")
        if re.search(r"(?:除|但|特殊情况|例外)", text):
            exceptions = [text]
            uncertainties.append("例外范围需人工确认")
        else:
            exceptions = []
        conditions = self._conditions(text)
        subject, attribute = self._subject_attribute(text)
        if not unit and rule_type in {"NUMERIC_THRESHOLD", "NUMERIC_RANGE"}:
            uncertainties.append("单位缺失")
        if rule_type == "MANUAL_REVIEW":
            uncertainties.append("确定性解析无法形成唯一可执行规则")
        source = evidence.citation
        raw_id = "|".join(
            (
                source.standard_code,
                source.clause_id or source.table_id or "",
                evidence.evidence_id,
                subject,
                attribute,
            )
        )
        return CandidateRule(
            candidate_rule_id="candidate_" + hashlib.sha256(raw_id.encode()).hexdigest()[:20],
            rule_type=rule_type,
            subject=subject,
            attribute=attribute,
            operator=operator,
            expected_value=expected,
            lower_bound=lower,
            upper_bound=upper,
            unit=unit,
            allowed_values=allowed,
            expected_boolean=expected_boolean,
            conditions=conditions,
            exceptions=exceptions,
            requirement_level=level,
            evidence_ids=[evidence.evidence_id],
            standard_code=source.standard_code,
            clause_id=source.clause_id,
            table_id=source.table_id,
            page_number=source.page_number,
            source_text=text,
            confidence=0.9 if rule_type != "MANUAL_REVIEW" and not uncertainties else 0.5,
            uncertainties=uncertainties,
            extraction_method="deterministic",
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _level(text: str) -> RequirementLevel:
        if re.search(r"(?:不得|禁止)", text):
            return RequirementLevel.SHALL_NOT
        if "不应" in text:
            return RequirementLevel.SHOULD_NOT
        if "宜" in text:
            return RequirementLevel.SHOULD
        if "可" in text:
            return RequirementLevel.MAY
        return RequirementLevel.SHALL

    @staticmethod
    def _conditions(text: str) -> list[RuleCondition]:
        match = re.search(r"(?:在|当|对于)([^，。；]+?)(?:时|情况下)?[，,]", text)
        if not match:
            return []
        value = match.group(1).strip()
        field = "station_mode" if "值守" in value else "applicable_condition"
        return [RuleCondition(field=field, operator="==", value=value)]

    @staticmethod
    def _subject_attribute(text: str) -> tuple[str, str]:
        subject = next(
            (x for x in ("UPS", "不间断电源", "控制系统", "电缆", "仪表", "探测器") if x in text),
            "规范对象",
        )
        attribute = next(
            (
                x
                for x in ("持续供电时间", "供电时间", "间距", "照度", "压力", "设置状态")
                if x in text
            ),
            "规范要求",
        )
        return subject, attribute
