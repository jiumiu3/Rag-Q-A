from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.domain.models import BoundingBox, StrictModel


class OCRBlockType(StrEnum):
    TEXT = "TEXT"
    HEADER_CANDIDATE = "HEADER_CANDIDATE"
    FOOTER_CANDIDATE = "FOOTER_CANDIDATE"
    WATERMARK_CANDIDATE = "WATERMARK_CANDIDATE"


class OCRBlock(StrictModel):
    block_id: str
    bbox: BoundingBox
    text: str
    confidence: float = Field(ge=0, le=1)
    block_type: OCRBlockType = OCRBlockType.TEXT
    reading_order: int = Field(ge=0)


class PageOCR(StrictModel):
    page_id: str
    document_id: str
    page_number: int = Field(ge=1)
    width_px: int = Field(ge=1)
    height_px: int = Field(ge=1)
    dpi: int = Field(ge=72)
    image_path: str
    blocks: list[OCRBlock] = Field(default_factory=list)
    raw_text: str = ""
    cleaned_text: str = ""
    engine_name: str
    engine_version: str
    duration_ms: float = Field(ge=0)
    error: str | None = None


class PageManifestEntry(StrictModel):
    page_number: int = Field(ge=1)
    page_id: str
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    rotation: int
    image_path: str
    status: str


class IngestionManifest(StrictModel):
    document_id: str
    source_path: str
    source_sha256: str
    source_page_count: int = Field(ge=1)
    page_count: int = Field(ge=1)
    is_complete: bool
    dpi: int
    engine_name: str
    engine_version: str
    config_hash: str
    generated_at: datetime
    pages: list[PageManifestEntry]


class OCRQualityReport(StrictModel):
    document_id: str
    page_count: int
    successful_pages: int
    failed_pages: list[int]
    empty_pages: list[int]
    low_confidence_pages: list[int]
    suspicious_character_pages: list[int]
    total_blocks: int
    total_characters: int
    average_confidence: float
    duration_ms: float
    cache_hit: bool = False


class IngestionResult(StrictModel):
    manifest_path: str
    raw_ocr_path: str
    cleaned_ocr_path: str
    quality_report_path: str
    report: OCRQualityReport
