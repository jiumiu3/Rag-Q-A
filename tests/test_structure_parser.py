from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.exceptions import IngestionError
from app.domain.models import BoundingBox
from app.ingestion.models import (
    IngestionManifest,
    OCRBlock,
    OCRBlockType,
    PageManifestEntry,
    PageOCR,
)
from app.ingestion.structure_parser import StructureParser, write_structure_result


def block(
    page_number: int,
    order: int,
    text: str,
    *,
    x0: float = 100,
    y0: float | None = None,
    block_type: OCRBlockType = OCRBlockType.TEXT,
) -> OCRBlock:
    y = y0 if y0 is not None else 100 + order * 40
    return OCRBlock(
        block_id=f"p{page_number}-b{order}",
        bbox=BoundingBox(x0=x0, y0=y, x1=x0 + 250, y1=y + 25),
        text=text,
        confidence=0.98,
        block_type=block_type,
        reading_order=order,
    )


def page(page_number: int, blocks: list[OCRBlock]) -> PageOCR:
    text = "\n".join(item.text for item in blocks)
    return PageOCR(
        page_id=f"page-{page_number}",
        document_id="doc-test",
        page_number=page_number,
        width_px=1000,
        height_px=1400,
        dpi=300,
        image_path=f"data/pages/doc-test/page-{page_number}.png",
        blocks=blocks,
        raw_text=text,
        cleaned_text=text,
        engine_name="fake",
        engine_version="1",
        duration_ms=10,
    )


def write_m1_fixture(root: Path, *, complete: bool = True) -> Path:
    pages = [
        page(
            1,
            [
                block(1, 0, "Q/GGW 02005.1—2022", block_type=OCRBlockType.HEADER_CANDIDATE),
                block(1, 1, "目 次"),
                block(1, 2, "1 范围 ........ 1"),
                block(1, 3, "液化天然气接收站管理公司 2025-10-28"),
            ],
        ),
        page(
            2,
            [
                block(2, 0, "Q/GGW 02005.1—2022", block_type=OCRBlockType.HEADER_CANDIDATE),
                block(2, 1, "1 范围"),
                block(2, 2, "本文件规定了仪表及自动控制系统的通用要求。"),
                block(2, 3, "1.1 一般要求"),
                block(2, 4, "条款内容跨页开始"),
                block(2, 5, "液化天然气接收站管理公司 2025-10-28"),
            ],
        ),
        page(
            3,
            [
                block(3, 0, "Q/GGW 02005.1—2022", block_type=OCRBlockType.HEADER_CANDIDATE),
                block(3, 1, "条款内容跨页结束"),
                block(3, 2, "2 规范性引用文件"),
                block(3, 3, "引用文件内容。"),
                block(3, 4, "3 术语和定义"),
                block(3, 5, "3.1 仪表管道 instrumentation piping"),
                block(3, 6, "仪表测量管道、气动和液动信号管道的总称。"),
                block(3, 7, "液化天然气接收站管理公司 2025-10-28"),
            ],
        ),
        page(
            4,
            [
                block(4, 0, "Q/GGW 02005.1—2022", block_type=OCRBlockType.HEADER_CANDIDATE),
                block(4, 1, "4 技术要求"),
                block(4, 2, "表 1 测试参数"),
                block(4, 3, "项目", x0=100, y0=300),
                block(4, 4, "要求", x0=500, y0=300),
                block(4, 5, "照度", x0=100, y0=350),
                block(4, 6, "500 lx", x0=500, y0=350),
                block(4, 7, "液化天然气接收站管理公司 2025-10-28"),
                block(4, 8, "1", x0=300, y0=500),
                block(4, 9, "8.8 不存在父条款的伪编号", x0=300, y0=550),
                block(4, 10, "2022-06-01实施", x0=300, y0=600),
                block(4, 11, "9 伪造根章节标题", x0=300, y0=650),
                block(4, 12, "5) 表格中的中文要求。", x0=190, y0=700),
            ],
        ),
    ]
    manifest = IngestionManifest(
        document_id="doc-test",
        source_path="data/raw/test.pdf",
        source_sha256="a" * 64,
        source_page_count=4,
        page_count=4 if complete else 1,
        is_complete=complete,
        dpi=300,
        engine_name="fake",
        engine_version="1",
        config_hash="b" * 64,
        generated_at=datetime.now(UTC),
        pages=[
            PageManifestEntry(
                page_number=item.page_number,
                page_id=item.page_id,
                width_pt=612,
                height_pt=792,
                rotation=0,
                image_path=item.image_path,
                status="success",
            )
            for item in (pages if complete else pages[:1])
        ],
    )
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    selected_pages = pages if complete else pages[:1]
    (root / "pages.raw.jsonl").write_text(
        "".join(item.model_dump_json() + "\n" for item in selected_pages), encoding="utf-8"
    )
    return root


def test_parser_recovers_hierarchy_cross_page_terms_tables_and_units(tmp_path: Path) -> None:
    ocr_dir = write_m1_fixture(tmp_path / "ocr")
    result = StructureParser().parse_directory(ocr_dir)

    assert result.report.excluded_directory_pages == [1]
    assert result.report.clause_count == 6
    assert {item.clause_number for item in result.clauses} == {"1", "1.1", "2", "3", "3.1", "4"}
    clause_11 = next(item for item in result.clauses if item.clause_number == "1.1")
    clause_1 = next(item for item in result.clauses if item.clause_number == "1")
    assert clause_11.parent_id == clause_1.clause_id
    assert clause_11.page_start == 2
    assert clause_11.page_end == 3
    assert "跨页结束" in clause_11.content
    assert result.terms[0].name == "仪表管道"
    assert result.report.unparsed_term_clause_ids == []
    assert result.tables[0].headers == ["项目", "要求"]
    assert result.tables[0].rows[0].cells == ["照度", "500 lx"]
    assert result.report.knowledge_unit_count >= result.report.clause_count
    assert result.report.removed_repeated_block_ids


def test_parser_rejects_incomplete_ocr_without_writing_processed_data(tmp_path: Path) -> None:
    ocr_dir = write_m1_fixture(tmp_path / "ocr", complete=False)

    with pytest.raises(IngestionError, match="拒绝解析未完成"):
        StructureParser().parse_directory(ocr_dir)


def test_corrections_are_audited_without_mutating_raw_file(tmp_path: Path) -> None:
    ocr_dir = write_m1_fixture(tmp_path / "ocr")
    raw_before = (ocr_dir / "pages.raw.jsonl").read_text(encoding="utf-8")
    corrections = tmp_path / "corrections.yaml"
    corrections.write_text(
        """corrections:
  - correction_id: fix-clause
    action: replace_text
    page_number: 2
    block_id: p2-b3
    old_text: "1.1 一般要求"
    new_text: "1.1 基本要求"
    reason: "人工核对"
""",
        encoding="utf-8",
    )

    result = StructureParser().parse_directory(ocr_dir, corrections_path=corrections)

    assert any(item.title == "基本要求" for item in result.clauses)
    assert result.report.applied_corrections[0].applied is True
    assert (ocr_dir / "pages.raw.jsonl").read_text(encoding="utf-8") == raw_before


def test_structure_outputs_are_written_separately_from_ocr(tmp_path: Path) -> None:
    result = StructureParser().parse_directory(write_m1_fixture(tmp_path / "ocr"))
    output_dir = tmp_path / "processed" / result.document_id

    write_structure_result(result, output_dir)

    assert (output_dir / "clauses.jsonl").is_file()
    assert (output_dir / "knowledge_units.jsonl").is_file()
    assert (output_dir / "parsing_quality_report.json").is_file()
    assert not list(output_dir.glob("*.tmp"))
