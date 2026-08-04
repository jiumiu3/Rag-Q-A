from pathlib import Path

import fitz

from app.core.config import OCRSettings, Settings, StorageSettings
from app.domain.models import BoundingBox
from app.ingestion.models import OCRBlock
from app.ingestion.pipeline import IngestionPipeline


class FakeOCREngine:
    name = "fake"
    version = "1.0"

    def __init__(self, *, fail_page: bool = False) -> None:
        self.calls = 0
        self.fail_page = fail_page

    def prepare(self) -> None:
        return None

    def recognize(self, image_path: Path, *, width: int, height: int) -> list[OCRBlock]:
        self.calls += 1
        if self.fail_page:
            raise RuntimeError("模拟 OCR 失败")
        return [
            OCRBlock(
                block_id=f"block-{self.calls}",
                bbox=BoundingBox(x0=1, y0=1, x1=width - 1, y1=min(50, height - 1)),
                text="11.6.1 测试条款",
                confidence=0.98,
                reading_order=0,
            )
        ]


def make_pdf(path: Path, pages: int = 2) -> None:
    document = fitz.open()
    for _ in range(pages):
        document.new_page(width=200, height=300)
    document.save(path)
    document.close()


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        ocr=OCRSettings(dpi=72, min_text_chars=3),
        storage=StorageSettings(
            raw_data_dir=tmp_path / "raw",
            pages_dir=tmp_path / "pages",
            ocr_dir=tmp_path / "ocr",
            processed_dir=tmp_path / "processed",
            index_dir=tmp_path / "indexes",
            sqlite_path=tmp_path / "knowledge.db",
        ),
    )


def test_pipeline_outputs_traceable_jsonl_and_report(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Q-GGW 02005.1—2022 测试.pdf"
    make_pdf(pdf_path)
    engine = FakeOCREngine()
    result = IngestionPipeline(make_settings(tmp_path), engine).ingest(pdf_path)

    assert result.report.page_count == 2
    assert result.report.successful_pages == 2
    assert result.report.total_characters > 0
    assert Path(result.raw_ocr_path).read_text(encoding="utf-8").count("\n") == 2
    assert "source_raw_file" in Path(result.cleaned_ocr_path).read_text(encoding="utf-8")
    assert len(list((tmp_path / "pages").rglob("*.png"))) == 2


def test_pipeline_reuses_valid_cache(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    make_pdf(pdf_path, pages=1)
    engine = FakeOCREngine()
    pipeline = IngestionPipeline(make_settings(tmp_path), engine)

    first = pipeline.ingest(pdf_path)
    second = pipeline.ingest(pdf_path)

    assert first.report.cache_hit is False
    assert second.report.cache_hit is True
    assert engine.calls == 1


def test_partial_smoke_result_is_never_treated_as_complete_cache(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    make_pdf(pdf_path, pages=2)
    engine = FakeOCREngine()
    pipeline = IngestionPipeline(make_settings(tmp_path), engine)

    partial = pipeline.ingest(pdf_path, max_pages=1)
    complete = pipeline.ingest(pdf_path)

    assert partial.report.page_count == 1
    assert complete.report.page_count == 2
    assert complete.report.cache_hit is False
    assert engine.calls == 3


def test_ocr_failure_is_recorded_instead_of_silently_dropped(tmp_path: Path) -> None:
    pdf_path = tmp_path / "failed.pdf"
    make_pdf(pdf_path, pages=1)
    result = IngestionPipeline(make_settings(tmp_path), FakeOCREngine(fail_page=True)).ingest(
        pdf_path
    )

    assert result.report.failed_pages == [1]
    assert result.report.successful_pages == 0
    assert "模拟 OCR 失败" in Path(result.raw_ocr_path).read_text(encoding="utf-8")


def test_document_inspection_extracts_standard_metadata(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Q-GGW 02005.2—2022 测试.pdf"
    make_pdf(pdf_path, pages=3)
    pipeline = IngestionPipeline(make_settings(tmp_path), FakeOCREngine())

    metadata = pipeline.inspect_document(pdf_path)

    assert metadata.standard_code == "Q/GGW 02005.2—2022"
    assert metadata.part == "2"
    assert metadata.year == 2022
    assert metadata.page_count == 3
