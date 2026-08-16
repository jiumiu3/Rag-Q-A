from decimal import Decimal

from app.design.models import ExtractedCheckItem, ExtractedDesign, ItemPatch
from app.design.parser import DesignDescriptionParser, LLMDesignDescriptionParser
from app.domain.models import CheckItemStatus


def test_parser_splits_multiple_attributes_and_preserves_spans() -> None:
    text = "新建输气站现场仪表电缆间距为300mm，控制室照度为500lx。"
    preview = DesignDescriptionParser().parse(text)
    assert {item.attribute for item in preview.items} == {"间距", "照度"}
    assert {item.value for item in preview.items} == {Decimal("300"), Decimal("500")}
    for item in preview.items:
        assert text[item.span.start : item.span.end] == item.source_text


def test_parser_marks_inferred_pronoun_object() -> None:
    preview = DesignDescriptionParser().parse("压力变送器安装在现场；其设计压力为1.6MPa。")
    pressure = next(item for item in preview.items if item.attribute == "压力")
    assert pressure.object == "压力变送器"
    assert "object" in pressure.inferred_fields
    assert pressure.uncertainties


def test_parser_marks_missing_unit() -> None:
    preview = DesignDescriptionParser().parse("仪表电缆间距为300。")
    assert "数值缺少单位" in preview.items[0].uncertainties


def test_confirmation_applies_patch_and_delete() -> None:
    parser = DesignDescriptionParser()
    preview = parser.parse("控制室照度为300lx；仪表电缆间距为200mm。")
    first, second = preview.items
    confirmed = parser.apply_patches(
        preview.items,
        [
            ItemPatch(item_id=first.item_id, value=500),
            ItemPatch(item_id=second.item_id, delete=True),
        ],
    )
    assert len(confirmed) == 1
    assert confirmed[0].value == 500
    assert confirmed[0].status == CheckItemStatus.CONFIRMED
    assert confirmed[0].user_corrected


def test_auto_confirm_only_readies_unambiguous_items() -> None:
    preview = DesignDescriptionParser().parse("控制室照度为500lx。", auto_confirm=True)
    assert preview.items[0].status == CheckItemStatus.READY


class FakeExtractionClient:
    def complete(self, _prompt: str, model: object) -> ExtractedDesign:
        assert model is ExtractedDesign
        return ExtractedDesign(
            items=[
                ExtractedCheckItem(
                    object="仪表管道",
                    attribute="净距",
                    value=50,
                    unit="mm",
                    source_text="仪表管道净距为50 mm",
                )
            ]
        )


def test_llm_parser_uses_lightweight_schema_and_builds_trace_fields() -> None:
    text = "请进行合规审查：仪表管道净距为50 mm。"
    preview = LLMDesignDescriptionParser(FakeExtractionClient()).parse(text)  # type: ignore[arg-type]
    item = preview.items[0]
    assert preview.description == text
    assert text[item.span.start : item.span.end] == item.source_text
    assert item.item_id.startswith("check_")
    assert item.explicit_fields == {"object", "attribute", "value", "unit", "source_text"}
