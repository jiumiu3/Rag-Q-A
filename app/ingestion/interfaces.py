from pathlib import Path
from typing import Protocol

from app.ingestion.models import OCRBlock


class OCREngine(Protocol):
    """OCR 引擎统一接口；在线 Agent 不得直接依赖具体实现。"""

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def prepare(self) -> None: ...

    def recognize(self, image_path: Path, *, width: int, height: int) -> list[OCRBlock]: ...
