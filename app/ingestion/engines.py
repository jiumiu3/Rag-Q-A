from importlib import metadata
from pathlib import Path
from typing import Any

from app.core.exceptions import IngestionError
from app.core.ids import ResourceType, stable_id
from app.domain.models import BoundingBox
from app.ingestion.models import OCRBlock, OCRBlockType


class RapidOCREngine:
    """RapidOCR 中文适配器；模型只在首次实际识别时懒加载。"""

    name = "rapidocr"

    def __init__(self) -> None:
        self._engine: Any | None = None

    @property
    def version(self) -> str:
        try:
            return metadata.version("rapidocr")
        except metadata.PackageNotFoundError:
            return "not-installed"

    def _load(self) -> Any:
        if self._engine is None:
            try:
                from rapidocr import RapidOCR
            except ImportError as exc:
                raise IngestionError(
                    "RapidOCR 未安装，请先统一安装项目 OCR 依赖：pip install -e '.[ocr]'"
                ) from exc
            self._engine = RapidOCR()
        return self._engine

    def prepare(self) -> None:
        """在生成页面图片前完成依赖检查和模型初始化，失败时快速终止。"""
        self._load()

    def recognize(self, image_path: Path, *, width: int, height: int) -> list[OCRBlock]:
        try:
            result = self._load()(str(image_path))
            rows = self._normalize_result(result)
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError(
                "RapidOCR 识别失败",
                details={"image_path": str(image_path), "reason": str(exc)},
            ) from exc

        blocks: list[OCRBlock] = []
        for order, (box, text, score) in enumerate(rows):
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            bbox = BoundingBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))
            block_type = self._classify_block(bbox, width, height, float(score))
            blocks.append(
                OCRBlock(
                    block_id=stable_id(ResourceType.CHUNK, str(image_path), order, text),
                    bbox=bbox,
                    text=str(text).strip(),
                    confidence=max(0.0, min(1.0, float(score))),
                    block_type=block_type,
                    reading_order=order,
                )
            )
        return [block for block in blocks if block.text]

    @staticmethod
    def _normalize_result(result: Any) -> list[tuple[Any, str, float]]:
        # RapidOCR 3.x 返回带 boxes/txts/scores 的对象；兼容旧版三元组结构。
        if result is None:
            return []
        if all(hasattr(result, name) for name in ("boxes", "txts", "scores")):
            # NumPy 数组不支持直接做布尔判断，必须显式判断 None。
            boxes = result.boxes if result.boxes is not None else []
            texts = result.txts if result.txts is not None else []
            scores = result.scores if result.scores is not None else []
            return list(zip(boxes, texts, scores, strict=False))
        payload = result[0] if isinstance(result, tuple) else result
        if payload is None:
            return []
        return [(row[0], str(row[1]), float(row[2])) for row in payload]

    @staticmethod
    def _classify_block(
        bbox: BoundingBox, width: int, height: int, confidence: float
    ) -> OCRBlockType:
        center_y = (bbox.y0 + bbox.y1) / 2
        if center_y <= height * 0.08:
            return OCRBlockType.HEADER_CANDIDATE
        if center_y >= height * 0.92:
            return OCRBlockType.FOOTER_CANDIDATE
        if confidence < 0.5 and height * 0.25 < center_y < height * 0.75:
            return OCRBlockType.WATERMARK_CANDIDATE
        return OCRBlockType.TEXT


def create_ocr_engine(name: str) -> RapidOCREngine:
    normalized = name.strip().lower()
    if normalized == "rapidocr":
        return RapidOCREngine()
    raise IngestionError(f"不支持的 OCR 引擎：{name}", details={"supported_engines": ["rapidocr"]})
