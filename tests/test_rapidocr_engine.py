from collections.abc import Iterator
from typing import Any

from app.ingestion.engines import RapidOCREngine


class AmbiguousArray:
    """模拟 NumPy 数组：参与布尔判断时抛出真值不明确错误。"""

    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def __bool__(self) -> bool:
        raise ValueError("The truth value of an array is ambiguous")

    def __iter__(self) -> Iterator[Any]:
        return iter(self.values)


class RapidOCR3Output:
    boxes: Any = AmbiguousArray([[[0, 0], [10, 0], [10, 10], [0, 10]]])
    txts: Any = ("测试条款",)
    scores: Any = (0.98,)


def test_normalize_rapidocr_3_output_does_not_boolean_check_numpy_array() -> None:
    rows = RapidOCREngine._normalize_result(RapidOCR3Output())

    assert len(rows) == 1
    assert rows[0][1:] == ("测试条款", 0.98)


def test_normalize_empty_rapidocr_3_output() -> None:
    output = RapidOCR3Output()
    output.boxes = None
    output.txts = None
    output.scores = None

    assert RapidOCREngine._normalize_result(output) == []
