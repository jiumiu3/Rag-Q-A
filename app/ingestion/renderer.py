from pathlib import Path

import fitz

from app.core.exceptions import IngestionError


class PdfPageRenderer:
    """使用 PyMuPDF 将 PDF 页面按固定 DPI 渲染为无损图片。"""

    def __init__(self, dpi: int = 300, image_format: str = "png") -> None:
        self.dpi = dpi
        self.image_format = image_format

    def render_page(self, page: fitz.Page, output_path: Path) -> tuple[int, int]:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            pixmap = page.get_pixmap(dpi=self.dpi, alpha=False)
            pixmap.save(output_path)
            return pixmap.width, pixmap.height
        except (OSError, RuntimeError, ValueError) as exc:
            raise IngestionError(
                f"PDF 第 {page.number + 1} 页渲染失败",
                details={"output_path": str(output_path), "reason": str(exc)},
            ) from exc
