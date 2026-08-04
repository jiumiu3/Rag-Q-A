import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import fitz

from app.core.config import Settings
from app.core.exceptions import IngestionError
from app.core.ids import document_id, page_id
from app.domain.models import DocumentMeta, DocumentStatus
from app.ingestion.interfaces import OCREngine
from app.ingestion.models import (
    IngestionManifest,
    IngestionResult,
    OCRQualityReport,
    PageManifestEntry,
    PageOCR,
)
from app.ingestion.renderer import PdfPageRenderer

SUSPICIOUS_PATTERN = re.compile(r"[�□]{2,}|[^\x00-\x7f\u3000-\u9fff]{5,}")


class IngestionPipeline:
    """M1 离线流程：元数据、渲染、OCR、幂等缓存和质量报告。"""

    def __init__(self, settings: Settings, engine: OCREngine) -> None:
        self.settings = settings
        self.engine = engine
        self.renderer = PdfPageRenderer(settings.ocr.dpi, settings.ocr.image_format)

    def ingest(
        self, pdf_path: Path, *, force: bool = False, max_pages: int | None = None
    ) -> IngestionResult:
        started = time.perf_counter()
        if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
            raise IngestionError(f"PDF 文件不存在或格式不正确：{pdf_path}")

        sha256 = self._file_sha256(pdf_path)
        doc_id = document_id(sha256)
        output_dir = self.settings.storage.ocr_dir / doc_id
        manifest_path = output_dir / "manifest.json"
        raw_path = output_dir / "pages.raw.jsonl"
        cleaned_path = output_dir / "pages.cleaned.jsonl"
        report_path = output_dir / "quality_report.json"
        config_hash = self._config_hash()
        source_page_count = self._pdf_page_count(pdf_path)

        if (
            not force
            and max_pages is None
            and self._cache_valid(manifest_path, config_hash, sha256, source_page_count)
        ):
            report = OCRQualityReport.model_validate_json(report_path.read_text(encoding="utf-8"))
            report.cache_hit = True
            return IngestionResult(
                manifest_path=str(manifest_path),
                raw_ocr_path=str(raw_path),
                cleaned_ocr_path=str(cleaned_path),
                quality_report_path=str(report_path),
                report=report,
            )

        # 提前验证 OCR 运行时，避免依赖缺失时产生整本文档的无用页面图片。
        self.engine.prepare()
        output_dir.mkdir(parents=True, exist_ok=True)
        page_records: list[PageOCR] = []
        manifest_pages: list[PageManifestEntry] = []
        try:
            with fitz.open(pdf_path) as document:
                limit = min(source_page_count, max_pages) if max_pages else source_page_count
                for index in range(limit):
                    record, entry = self._process_page(document[index], doc_id)
                    page_records.append(record)
                    manifest_pages.append(entry)
        except (fitz.FileDataError, fitz.EmptyFileError, OSError) as exc:
            raise IngestionError(f"无法读取 PDF：{pdf_path}", details={"reason": str(exc)}) from exc

        manifest = IngestionManifest(
            document_id=doc_id,
            source_path=str(pdf_path),
            source_sha256=sha256,
            source_page_count=source_page_count,
            page_count=len(page_records),
            is_complete=len(page_records) == source_page_count,
            dpi=self.settings.ocr.dpi,
            engine_name=self.engine.name,
            engine_version=self.engine.version,
            config_hash=config_hash,
            generated_at=datetime.now(UTC),
            pages=manifest_pages,
        )
        report = self._quality_report(doc_id, page_records, started)
        self._write_outputs(
            manifest, page_records, report, manifest_path, raw_path, cleaned_path, report_path
        )
        return IngestionResult(
            manifest_path=str(manifest_path),
            raw_ocr_path=str(raw_path),
            cleaned_ocr_path=str(cleaned_path),
            quality_report_path=str(report_path),
            report=report,
        )

    def inspect_document(self, pdf_path: Path) -> DocumentMeta:
        sha256 = self._file_sha256(pdf_path)
        match = re.search(r"(Q[-/]GGW\s+02005\.(\d+))[—-](\d{4})", pdf_path.stem, re.IGNORECASE)
        with fitz.open(pdf_path) as document:
            return DocumentMeta(
                document_id=document_id(sha256),
                file_name=pdf_path.name,
                standard_code=match.group(1).replace("-", "/") + f"—{match.group(3)}"
                if match
                else pdf_path.stem,
                part=match.group(2) if match else None,
                year=int(match.group(3)) if match else None,
                sha256=sha256,
                page_count=len(document),
                status=DocumentStatus.PENDING,
            )

    def _process_page(self, page: fitz.Page, doc_id: str) -> tuple[PageOCR, PageManifestEntry]:
        started = time.perf_counter()
        number = page.number + 1
        current_page_id = page_id(doc_id, number)
        image_path = (
            self.settings.storage.pages_dir
            / doc_id
            / f"{current_page_id}.{self.settings.ocr.image_format}"
        )
        width, height = self.renderer.render_page(page, image_path)
        error: str | None = None
        try:
            blocks = self.engine.recognize(image_path, width=width, height=height)
        except Exception as exc:
            blocks = []
            error = self._error_message(exc)
        raw_text = "\n".join(block.text for block in blocks)
        cleaned_text = "\n".join(
            block.text
            for block in blocks
            if block.confidence >= self.settings.ocr.confidence_threshold
        )
        record = PageOCR(
            page_id=current_page_id,
            document_id=doc_id,
            page_number=number,
            width_px=width,
            height_px=height,
            dpi=self.settings.ocr.dpi,
            image_path=str(image_path),
            blocks=blocks,
            raw_text=raw_text,
            cleaned_text=cleaned_text,
            engine_name=self.engine.name,
            engine_version=self.engine.version,
            duration_ms=(time.perf_counter() - started) * 1000,
            error=error,
        )
        entry = PageManifestEntry(
            page_number=number,
            page_id=current_page_id,
            width_pt=page.rect.width,
            height_pt=page.rect.height,
            rotation=page.rotation,
            image_path=str(image_path),
            status="failed" if error else "success",
        )
        return record, entry

    @staticmethod
    def _error_message(exc: Exception) -> str:
        """保留离线处理根因，避免质量报告只出现无法排查的通用错误。"""
        if isinstance(exc, IngestionError):
            reason = exc.details.get("reason")
            return f"{exc.message}: {reason}" if reason else exc.message
        return f"{type(exc).__name__}: {exc}"

    def _quality_report(
        self, doc_id: str, records: list[PageOCR], started: float
    ) -> OCRQualityReport:
        confidences = [block.confidence for record in records for block in record.blocks]
        failed = [record.page_number for record in records if record.error]
        empty = [
            record.page_number
            for record in records
            if not record.error and len(record.cleaned_text) < self.settings.ocr.min_text_chars
        ]
        low_confidence = [
            record.page_number
            for record in records
            if record.blocks
            and sum(block.confidence for block in record.blocks) / len(record.blocks)
            < self.settings.ocr.confidence_threshold
        ]
        suspicious = [
            record.page_number for record in records if SUSPICIOUS_PATTERN.search(record.raw_text)
        ]
        return OCRQualityReport(
            document_id=doc_id,
            page_count=len(records),
            successful_pages=len(records) - len(failed),
            failed_pages=failed,
            empty_pages=empty,
            low_confidence_pages=low_confidence,
            suspicious_character_pages=suspicious,
            total_blocks=sum(len(record.blocks) for record in records),
            total_characters=sum(len(record.cleaned_text) for record in records),
            average_confidence=sum(confidences) / len(confidences) if confidences else 0,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    @staticmethod
    def _write_outputs(
        manifest: IngestionManifest,
        records: list[PageOCR],
        report: OCRQualityReport,
        manifest_path: Path,
        raw_path: Path,
        cleaned_path: Path,
        report_path: Path,
    ) -> None:
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        raw_path.write_text(
            "".join(record.model_dump_json() + "\n" for record in records), encoding="utf-8"
        )
        cleaned_path.write_text(
            "".join(
                json.dumps(
                    {
                        "page_id": record.page_id,
                        "document_id": record.document_id,
                        "page_number": record.page_number,
                        "cleaned_text": record.cleaned_text,
                        "source_raw_file": str(raw_path),
                        "error": record.error,
                    },
                    ensure_ascii=False,
                )
                + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    def _config_hash(self) -> str:
        payload = {
            "dpi": self.settings.ocr.dpi,
            "image_format": self.settings.ocr.image_format,
            "confidence_threshold": self.settings.ocr.confidence_threshold,
            "engine": self.engine.name,
            "engine_version": self.engine.version,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _cache_valid(
        manifest_path: Path, config_hash: str, sha256: str, source_page_count: int
    ) -> bool:
        if not manifest_path.is_file():
            return False
        try:
            manifest = IngestionManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (ValueError, OSError):
            return False
        output_dir = manifest_path.parent
        return (
            manifest.source_sha256 == sha256
            and manifest.config_hash == config_hash
            and manifest.is_complete
            and manifest.source_page_count == source_page_count
            and manifest.page_count == source_page_count
            and len(manifest.pages) == source_page_count
            and (output_dir / "pages.raw.jsonl").is_file()
            and (output_dir / "pages.cleaned.jsonl").is_file()
            and (output_dir / "quality_report.json").is_file()
        )

    @staticmethod
    def _pdf_page_count(path: Path) -> int:
        try:
            with fitz.open(path) as document:
                if len(document) < 1:
                    raise IngestionError(f"PDF 不包含有效页面：{path}")
                return len(document)
        except (fitz.FileDataError, fitz.EmptyFileError, OSError) as exc:
            raise IngestionError(f"无法读取 PDF：{path}", details={"reason": str(exc)}) from exc

    @staticmethod
    def _file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(chunk_size), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise IngestionError(f"无法读取文件：{path}", details={"reason": str(exc)}) from exc
        return digest.hexdigest()
