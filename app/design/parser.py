import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from app.core.ids import check_item_id
from app.core.model_client import CompatibleJSONClient
from app.design.models import DesignContext, DesignPreview, ItemPatch
from app.domain.models import CharacterSpan, CheckItem, CheckItemStatus

SENTENCE_RE = re.compile(r"[^。；;！？!?\n]+[。；;！？!?]?|[^。；;！？!?\n]+$")
NUMBER_RE = re.compile(
    r"(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>mm²|mm2|MPa|kPa|bar|min|km|mm|kV|°C|℃|lx|m|h|s|V|A|%)?",
    re.I,
)
LOCATION_RE = re.compile(r"(?:位于|安装在|设置在|敷设于)([^，。；,]{1,30})")
CONDITION_RE = re.compile(r"(?:当|在)([^，。；,]{2,40})(?:时|情况下)")

ATTRIBUTE_ALIASES: dict[str, tuple[str, ...]] = {
    "间距": ("间距", "净距", "距离"),
    "照度": ("照度",),
    "供电时间": ("供电时间", "备用时间", "持续供电"),
    "接地截面积": ("接地截面积", "截面积", "接地线面积"),
    "压力": ("设计压力", "工作压力", "压力"),
    "温度": ("设计温度", "工作温度", "温度"),
    "电压": ("电压",),
    "防护等级": ("防护等级", "IP等级"),
    "防爆等级": ("防爆等级",),
    "安装方式": ("安装方式", "安装"),
    "设置状态": ("应设置", "设置", "配置", "采用", "安装"),
}
OBJECTS = (
    "可燃气体探测器",
    "火灾探测器",
    "流量计",
    "压力变送器",
    "温度变送器",
    "控制阀",
    "仪表电缆",
    "信号电缆",
    "动力电缆",
    "接地线",
    "UPS",
    "控制室",
    "机柜间",
    "分析仪",
    "仪表",
    "电缆",
    "设备",
)
PROJECT_TYPES = ("新建", "改建", "扩建")
STATION_TYPES = ("输气站", "输油站", "油库", "LNG接收站", "储气库", "阀室", "站场")
AREAS = ("爆炸危险区域", "危险区", "非危险区", "控制室", "机柜间", "现场")


class StructuredDesignParser(Protocol):
    def parse(self, description: str) -> DesignPreview: ...


class DesignDescriptionParser:
    """可复现的首版解析器；后续模型适配器必须返回相同 Pydantic 模型。"""

    def parse(self, description: str, *, auto_confirm: bool = False) -> DesignPreview:
        context = DesignContext(
            project_type=self._first(description, PROJECT_TYPES),
            station_type=self._first(description, STATION_TYPES),
            area=self._first(description, AREAS),
        )
        items: list[CheckItem] = []
        last_object: str | None = None
        for match in SENTENCE_RE.finditer(description):
            sentence = match.group().strip()
            if not sentence:
                continue
            explicit_object = self._first(sentence, OBJECTS)
            if explicit_object:
                last_object = explicit_object
            attributes = self._attributes(sentence)
            for attribute, position in attributes:
                nearest_object = self._nearest_object(sentence, position)
                # “A 与 B 间距”是关系属性，保留两个对象，避免只绑定到离属性最近的 B。
                if attribute == "间距":
                    nearest_object = self._relation_object(sentence, position) or nearest_object
                item = self._item(
                    description,
                    sentence,
                    match.start(),
                    nearest_object or last_object,
                    nearest_object,
                    attribute,
                    position,
                    context,
                    auto_confirm,
                )
                items.append(item)
        items = self._deduplicate(items)
        warnings: list[str] = []
        if not items:
            warnings.append("未识别到可独立检查的对象属性，请人工补充检查项。")
        return DesignPreview(
            description=description,
            context=context,
            items=items,
            requires_confirmation=not auto_confirm,
            warnings=warnings,
        )

    @staticmethod
    def _first(text: str, values: Sequence[str]) -> str | None:
        return next((value for value in values if value.casefold() in text.casefold()), None)

    @staticmethod
    def _attributes(sentence: str) -> list[tuple[str, int]]:
        found: list[tuple[str, int]] = []
        for canonical, aliases in ATTRIBUTE_ALIASES.items():
            positions: list[int] = []
            occupied: list[range] = []
            for alias in sorted(aliases, key=len, reverse=True):
                for match in re.finditer(re.escape(alias), sentence):
                    current = range(match.start(), match.end())
                    if not any(set(current).intersection(previous) for previous in occupied):
                        positions.append(match.start())
                        occupied.append(current)
            if canonical == "压力" and not any(key in sentence for key in ("设计压力", "工作压力")):
                positions = [
                    position
                    for position in positions
                    if not re.match(r"(?:变送器|表|计)", sentence[position + 2 :])
                ]
            if positions:
                found.extend((canonical, position) for position in positions)
        found.sort(key=lambda item: item[1])
        # “安装”只在没有更具体属性时作为设置状态，避免同一句重复生成同义检查项。
        generic = {"安装方式", "设置状态"}
        if any(item[0] not in generic for item in found):
            found = [item for item in found if item[0] not in generic]
        elif found:
            setting = next((item for item in found if item[0] == "设置状态"), found[0])
            found = [setting]
        return found

    def _item(
        self,
        description: str,
        sentence: str,
        start: int,
        object_name: str | None,
        explicit_object: str | None,
        attribute: str,
        attribute_position: int,
        context: DesignContext,
        auto_confirm: bool,
    ) -> CheckItem:
        numeric = self._nearest_number(sentence, attribute_position)
        location_match = LOCATION_RE.search(sentence)
        condition_match = CONDITION_RE.search(sentence)
        explicit = {"attribute", "source_text"}
        inferred: set[str] = set()
        uncertainties: list[str] = []
        if explicit_object:
            explicit.add("object")
        elif object_name:
            inferred.add("object")
            uncertainties.append("对象由上文指代推断，需要用户确认")
        else:
            object_name = "未识别对象"
            uncertainties.append("缺少明确对象")
        value: Decimal | bool | str | None = None
        unit: str | None = None
        ip_match = re.search(r"IP\s*\d+[A-Z]?", sentence, re.I)
        explosion_match = re.search(r"Ex\s+[^，。；,]+", sentence, re.I)
        # IP/Ex 等级是枚举字符串，不应被通用数字解析器退化为 65、4 等数字。
        if attribute == "防护等级" and ip_match:
            value = re.sub(r"\s+", "", ip_match.group())
            explicit.add("value")
        elif attribute == "防爆等级" and explosion_match:
            value = explosion_match.group().strip()
            explicit.add("value")
        elif numeric:
            value = Decimal(numeric.group("value"))
            unit = numeric.group("unit")
            explicit.add("value")
            if unit:
                explicit.add("unit")
            else:
                uncertainties.append("数值缺少单位")
        elif re.search(r"(?:不|未|不得|禁止)(?:设置|安装|采用|配置)", sentence):
            value = False
            explicit.add("value")
        elif re.search(r"(?:设置|安装|采用|配置)", sentence):
            value = True
            explicit.add("value")
        if location_match:
            explicit.add("location")
        if condition_match:
            explicit.add("condition")
        elif context.area:
            inferred.add("condition")
        span = CharacterSpan(start=start, end=start + len(sentence))
        status = (
            CheckItemStatus.READY if auto_confirm and not uncertainties else CheckItemStatus.DRAFT
        )
        return CheckItem(
            item_id=check_item_id(f"{description}\nattribute={attribute}", (span.start, span.end)),
            object=object_name,
            attribute=attribute,
            value=value,
            unit=unit,
            location=location_match.group(1) if location_match else None,
            condition=condition_match.group(1) if condition_match else context.area,
            source_text=sentence,
            span=span,
            explicit_fields=explicit,
            inferred_fields=inferred,
            uncertainties=uncertainties,
            status=status,
        )

    @staticmethod
    def _nearest_number(sentence: str, position: int) -> re.Match[str] | None:
        matches = list(NUMBER_RE.finditer(sentence))
        following = [item for item in matches if item.start() >= position]
        return following[0] if following else (matches[-1] if matches else None)

    @staticmethod
    def _nearest_object(sentence: str, position: int) -> str | None:
        raw = [
            (match.start(), match.end(), value)
            for value in OBJECTS
            for match in re.finditer(re.escape(value), sentence, re.I)
        ]
        occurrences = [
            item
            for item in raw
            if not any(
                other[0] <= item[0]
                and other[1] >= item[1]
                and (other[1] - other[0]) > (item[1] - item[0])
                for other in raw
            )
        ]
        preceding = [item for item in occurrences if item[0] <= position]
        if preceding:
            return max(preceding, key=lambda item: item[0])[2]
        return min(occurrences, key=lambda item: item[0])[2] if occurrences else None

    @staticmethod
    def _relation_object(sentence: str, position: int) -> str | None:
        """提取关系属性前最近的两个完整对象，如“仪表电缆与动力电缆”。"""
        raw = [
            (match.start(), match.end(), value)
            for value in OBJECTS
            for match in re.finditer(re.escape(value), sentence[:position], re.I)
        ]
        occurrences = [
            item
            for item in raw
            if not any(
                other[0] <= item[0]
                and other[1] >= item[1]
                and (other[1] - other[0]) > (item[1] - item[0])
                for other in raw
            )
        ]
        ordered = sorted(occurrences, key=lambda item: item[0])
        unique: list[str] = []
        for _, _, value in ordered:
            if not unique or unique[-1] != value:
                unique.append(value)
        return "与".join(unique[-2:]) if len(unique) >= 2 else None

    @staticmethod
    def _deduplicate(items: list[CheckItem]) -> list[CheckItem]:
        output: list[CheckItem] = []
        seen: set[tuple[str, str, str | None, str | int | Decimal | bool | None, str | None]] = (
            set()
        )
        for item in items:
            key = (item.object, item.attribute, item.location, item.value, item.unit)
            if key not in seen:
                output.append(item)
                seen.add(key)
        return output

    @staticmethod
    def apply_patches(items: list[CheckItem], patches: list[ItemPatch]) -> list[CheckItem]:
        by_id = {item.item_id: item for item in items}
        for patch in patches:
            if patch.delete:
                by_id.pop(patch.item_id, None)
                continue
            item = by_id.get(patch.item_id)
            if not item:
                continue
            changes = patch.model_dump(exclude={"item_id", "delete"}, exclude_none=True)
            changes["user_corrected"] = bool(changes)
            changes["status"] = CheckItemStatus.CONFIRMED
            by_id[patch.item_id] = item.model_copy(update=changes)
        return [
            item.model_copy(update={"status": CheckItemStatus.CONFIRMED}) for item in by_id.values()
        ]


class LLMDesignDescriptionParser:
    """可选模型解析器；返回后再次校验字符区间与原文映射。"""

    def __init__(self, client: CompatibleJSONClient) -> None:
        self.client = client

    def parse(self, description: str) -> DesignPreview:
        preview = self.client.complete(
            "将设计描述拆成原子检查项。每项只能有一个主要属性，区分 explicit_fields 与 "
            f"inferred_fields，并精确给出 source_text 和字符 span。设计描述：\n{description}",
            DesignPreview,
        )
        if preview.description != description:
            raise ValueError("模型返回的 description 与输入不一致")
        for item in preview.items:
            if description[item.span.start : item.span.end] != item.source_text:
                raise ValueError(f"检查项 {item.item_id} 的字符区间无法回溯原文")
        return preview
