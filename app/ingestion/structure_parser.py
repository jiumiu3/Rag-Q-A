import json
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.core.exceptions import IngestionError
from app.core.ids import ResourceType, stable_id
from app.domain.models import KnowledgeUnit, ParseStatus, SourceSpan, StrictModel, UnitType
from app.ingestion.corrections import apply_corrections, load_corrections
from app.ingestion.models import IngestionManifest, OCRBlock, OCRBlockType, PageOCR
from app.ingestion.structure_models import (
    AppliedCorrection,
    Clause,
    FigureNote,
    ParsingQualityReport,
    StructuredTable,
    StructureResult,
    TableRow,
    Term,
)

CLAUSE_PATTERN = re.compile(r"^(\d+(?:\.\d+){0,5})(?:\s+|　*)(.*)$")
TABLE_PATTERN = re.compile(
    r"^表\s*([A-Z]?\d+(?:[.\-]\d+)*)\s*(.*?)(?:[（(]\s*续\s*[）)])?$", re.IGNORECASE
)
FIGURE_PATTERN = re.compile(r"^图\s*([A-Z]?\d+(?:[.\-]\d+)*)\s*(.*)$", re.IGNORECASE)
TERM_LINE_PATTERN = re.compile(r"^(.+?)([A-Za-z][A-Za-z0-9 /()\-]{2,80})$")
WATERMARK_PATTERN = re.compile(r"(?:管理公司|接收站管理|\d{4}[-—]\d{1,2}[-—]\d{1,2}|\d{7,})")


class StructureParser:
    """从完整 PageOCR 恢复条款、术语、表格、图注和可检索知识单元。"""

    def parse_directory(
        self, ocr_dir: Path, *, corrections_path: Path | None = None
    ) -> StructureResult:
        manifest_path = ocr_dir / "manifest.json"
        raw_path = ocr_dir / "pages.raw.jsonl"
        if not manifest_path.is_file() or not raw_path.is_file():
            raise IngestionError(f"M1 输出不完整：{ocr_dir}")
        try:
            manifest = IngestionManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            pages = [
                PageOCR.model_validate_json(line)
                for line in raw_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, ValueError) as exc:
            raise IngestionError(
                f"无法读取 M1 输出：{ocr_dir}", details={"reason": str(exc)}
            ) from exc

        self._validate_complete(manifest, pages)
        corrections = load_corrections(corrections_path)
        pages, audits = apply_corrections(pages, corrections, manifest.document_id)
        filtered_pages, removed_ids = self._remove_repeated_noise(pages)
        directory_pages = self._directory_pages(filtered_pages)
        content_pages = [page for page in filtered_pages if page.page_number not in directory_pages]

        clauses = self._parse_clauses(content_pages, manifest.document_id)
        tables = self._parse_tables(content_pages, manifest.document_id)
        figures = self._parse_figures(content_pages, manifest.document_id)
        terms = self._parse_terms(clauses, manifest.document_id)
        units = self._knowledge_units(clauses, tables, terms, figures)
        report = self._quality_report(
            manifest.document_id,
            pages,
            clauses,
            tables,
            terms,
            figures,
            units,
            directory_pages,
            removed_ids,
            audits,
        )
        return StructureResult(
            document_id=manifest.document_id,
            clauses=clauses,
            tables=tables,
            terms=terms,
            figures=figures,
            knowledge_units=units,
            report=report,
        )

    @staticmethod
    def _validate_complete(manifest: IngestionManifest, pages: list[PageOCR]) -> None:
        if not manifest.is_complete:
            raise IngestionError(
                "拒绝解析未完成的 OCR 文档",
                details={
                    "document_id": manifest.document_id,
                    "processed_pages": manifest.page_count,
                    "source_pages": manifest.source_page_count,
                },
            )
        if manifest.page_count != manifest.source_page_count or len(pages) != manifest.page_count:
            raise IngestionError(
                "OCR 清单与 JSONL 页数不一致",
                details={"manifest_pages": manifest.page_count, "jsonl_pages": len(pages)},
            )
        page_numbers = [page.page_number for page in pages]
        if page_numbers != list(range(1, manifest.source_page_count + 1)):
            raise IngestionError("OCR JSONL 页码不连续或顺序错误")

    def _remove_repeated_noise(self, pages: list[PageOCR]) -> tuple[list[PageOCR], list[str]]:
        normalized_by_block: dict[str, str] = {}
        frequency: Counter[str] = Counter()
        for page in pages:
            seen: set[str] = set()
            for block in page.blocks:
                normalized = self._normalize_noise(block.text)
                normalized_by_block[block.block_id] = normalized
                if normalized and normalized not in seen:
                    frequency[normalized] += 1
                    seen.add(normalized)

        repeat_threshold = max(3, int(len(pages) * 0.3))
        cleaned = [page.model_copy(deep=True) for page in pages]
        removed: list[str] = []
        for page in cleaned:
            kept: list[OCRBlock] = []
            for block in page.blocks:
                normalized = normalized_by_block.get(block.block_id, "")
                repeated = bool(normalized) and frequency[normalized] >= repeat_threshold
                positional_noise = block.block_type in {
                    OCRBlockType.HEADER_CANDIDATE,
                    OCRBlockType.FOOTER_CANDIDATE,
                    OCRBlockType.WATERMARK_CANDIDATE,
                }
                watermark = bool(WATERMARK_PATTERN.search(block.text))
                unusually_tall = (block.bbox.y1 - block.bbox.y0) > page.height_px * 0.04
                geometric_watermark = (
                    unusually_tall
                    and (block.bbox.x1 - block.bbox.x0) > page.width_px * 0.2
                    and block.bbox.x0 > page.width_px * 0.2
                )
                if (
                    positional_noise
                    or geometric_watermark
                    or (watermark and unusually_tall)
                    or (repeated and watermark)
                ):
                    removed.append(block.block_id)
                else:
                    kept.append(block)
            page.blocks = kept
            page.cleaned_text = "\n".join(block.text for block in kept)
        return cleaned, removed

    @staticmethod
    def _normalize_noise(text: str) -> str:
        return re.sub(r"[\s\W_]+", "", text).lower()

    @staticmethod
    def _directory_pages(pages: list[PageOCR]) -> list[int]:
        excluded: list[int] = []
        early_limit = max(5, int(len(pages) * 0.2))
        in_directory = False
        for page in pages:
            if page.page_number > early_limit:
                break
            text = page.cleaned_text
            compact = re.sub(r"\s+", "", text)
            if "目次" in compact or "目录" in compact:
                in_directory = True
            numbered_lines = sum(
                1 for line in text.splitlines() if re.search(r"\.{2,}\s*\d+$|\s\d+$", line.strip())
            )
            if in_directory and (numbered_lines >= 2 or "目次" in compact or "目录" in compact):
                excluded.append(page.page_number)
            elif in_directory and excluded:
                in_directory = False
        return excluded

    def _parse_clauses(self, pages: list[PageOCR], document_id: str) -> list[Clause]:
        clauses: list[Clause] = []
        current: dict[str, object] | None = None
        stack: dict[int, str] = {}
        accepted_numbers: set[str] = set()

        for page in pages:
            ordered_blocks = sorted(page.blocks, key=lambda item: item.reading_order)
            for index, block in enumerate(ordered_blocks):
                match = self._clause_match(block.text)
                next_block = ordered_blocks[index + 1] if index + 1 < len(ordered_blocks) else None
                if match and self._is_clause_candidate(
                    match, block, next_block, page.width_px, accepted_numbers
                ):
                    if current:
                        clauses.append(self._finish_clause(current))
                    number, remainder = match
                    level = number.count(".") + 1
                    parent_id = stack.get(level - 1)
                    clause_id = stable_id(ResourceType.CHUNK, document_id, "clause", number)
                    for key in [key for key in stack if key >= level]:
                        stack.pop(key)
                    stack[level] = clause_id
                    accepted_numbers.add(number)
                    chapter_path = [stack[index] for index in sorted(stack) if index <= level]
                    current = {
                        "clause_id": clause_id,
                        "document_id": document_id,
                        "clause_number": number,
                        "title": self._title_candidate(remainder),
                        "texts": [remainder] if remainder else [],
                        "parent_id": parent_id,
                        "chapter_path": chapter_path,
                        "page_start": page.page_number,
                        "page_end": page.page_number,
                        "spans": [self._span(page, block)],
                    }
                elif current:
                    texts = current["texts"]
                    spans = current["spans"]
                    assert isinstance(texts, list) and isinstance(spans, list)
                    texts.append(block.text)
                    spans.append(self._span(page, block))
                    current["page_end"] = page.page_number
        if current:
            clauses.append(self._finish_clause(current))

        clause_map = {clause.clause_id: clause for clause in clauses}
        for clause in clauses:
            if clause.parent_id and clause.parent_id in clause_map:
                clause_map[clause.parent_id].children_ids.append(clause.clause_id)
        return clauses

    @staticmethod
    def _is_clause_candidate(
        match: tuple[str, str],
        block: OCRBlock,
        next_block: OCRBlock | None,
        page_width: int,
        accepted_numbers: set[str],
    ) -> bool:
        number, remainder = match
        if number in accepted_numbers:
            return False
        if block.block_type != OCRBlockType.TEXT or block.bbox.x0 > page_width * 0.2:
            return False

        parts = number.split(".")
        if len(parts) == 1:
            # 根章节必须同时携带中文标题；排除页码、图例序号和表格行号。
            accepted_roots = [int(item) for item in accepted_numbers if "." not in item]
            expected_root = max(accepted_roots, default=0) + 1
            short_heading = len(remainder) <= 60 and not re.search(r"[：:；;。!?）)]", remainder)
            return (
                block.bbox.x0 <= page_width * 0.16
                and int(number) == expected_root
                and short_heading
                and bool(re.search(r"[\u4e00-\u9fff]{2,}", remainder))
            )

        parent_number = ".".join(parts[:-1])
        if parent_number not in accepted_numbers:
            return False
        if remainder:
            return bool(re.search(r"[\u4e00-\u9fffA-Za-z]", remainder))

        # 第 3 章是连续术语定义；标题可能跨页或被水印遮挡，但编号本身仍可信。
        if parent_number == "3" and len(parts) == 2:
            accepted_terms = [
                int(item.split(".")[1])
                for item in accepted_numbers
                if item.startswith("3.") and item.count(".") == 1
            ]
            return int(parts[1]) == max(accepted_terms, default=0) + 1

        # 纯层级编号主要出现在术语区：下一块应是紧邻的“中文名 + 英文名”。
        if next_block is None:
            return False
        close_vertically = next_block.bbox.y0 <= block.bbox.y1 + 180
        bilingual_term = bool(
            re.search(r"[\u4e00-\u9fff]{2,}", next_block.text)
            and re.search(r"[A-Za-z]{3,}", next_block.text)
        )
        return len(parts) == 2 and close_vertically and bilingual_term

    @staticmethod
    def _clause_match(text: str) -> tuple[str, str] | None:
        match = CLAUSE_PATTERN.match(text.strip())
        if not match:
            return None
        number = match.group(1)
        parts = number.split(".")
        if any(part.startswith("0") and len(part) > 1 for part in parts):
            return None
        if int(parts[0]) < 1 or int(parts[0]) > 99:
            return None
        return number, match.group(2).strip()

    @staticmethod
    def _title_candidate(text: str) -> str | None:
        if text and len(text) <= 60 and not re.search(r"[。；;]", text):
            return text
        return None

    @staticmethod
    def _finish_clause(data: dict[str, Any]) -> Clause:
        texts = data.pop("texts")
        spans = data.pop("spans")
        assert isinstance(texts, list) and isinstance(spans, list)
        return Clause(
            content="\n".join(str(text) for text in texts).strip(), source_spans=spans, **data
        )

    def _parse_tables(self, pages: list[PageOCR], document_id: str) -> list[StructuredTable]:
        tables: dict[str, StructuredTable] = {}
        for page in pages:
            blocks = sorted(page.blocks, key=lambda item: item.reading_order)
            for index, block in enumerate(blocks):
                match = TABLE_PATTERN.match(block.text.strip())
                if not match:
                    continue
                number, title = match.group(1), match.group(2).strip()
                table_id = stable_id(ResourceType.TABLE, document_id, number)
                candidate_blocks: list[OCRBlock] = []
                for following in blocks[index + 1 :]:
                    if TABLE_PATTERN.match(following.text.strip()) or FIGURE_PATTERN.match(
                        following.text.strip()
                    ):
                        break
                    if self._clause_match(following.text):
                        break
                    candidate_blocks.append(following)
                row_groups = self._cluster_rows(candidate_blocks)
                parsed_rows = [
                    TableRow(
                        row_id=stable_id(ResourceType.CHUNK, table_id, page.page_number, row_index),
                        table_id=table_id,
                        row_index=row_index,
                        cells=[cell.text for cell in row],
                        page_number=page.page_number,
                        source_spans=[self._span(page, cell) for cell in row],
                    )
                    for row_index, row in enumerate(row_groups)
                ]
                existing = tables.get(table_id)
                if existing:
                    offset = len(existing.rows)
                    for row in parsed_rows:
                        row.row_index += offset
                    existing.rows.extend(parsed_rows)
                    existing.page_end = page.page_number
                    existing.image_paths.append(page.image_path)
                    existing.source_spans.append(self._span(page, block))
                    if not existing.title and title:
                        existing.title = title
                else:
                    headers = parsed_rows[0].cells if parsed_rows else []
                    tables[table_id] = StructuredTable(
                        table_id=table_id,
                        document_id=document_id,
                        table_number=number,
                        title=title or f"表 {number}",
                        headers=headers,
                        rows=parsed_rows[1:] if parsed_rows else [],
                        page_start=page.page_number,
                        page_end=page.page_number,
                        image_paths=[page.image_path],
                        parse_status=(
                            ParseStatus.PARSED
                            if len(parsed_rows) >= 2
                            else ParseStatus.NEEDS_MANUAL_ANNOTATION
                        ),
                        source_spans=[self._span(page, block)],
                    )
        parsed_tables = list(tables.values())
        for table in parsed_tables:
            consistent = bool(table.headers) and len(table.headers) >= 2 and bool(table.rows)
            consistent = consistent and all(
                len(row.cells) == len(table.headers) for row in table.rows
            )
            table.parse_status = (
                ParseStatus.PARSED if consistent else ParseStatus.NEEDS_MANUAL_ANNOTATION
            )
        return parsed_tables

    @staticmethod
    def _cluster_rows(blocks: list[OCRBlock], tolerance: float = 18.0) -> list[list[OCRBlock]]:
        rows: list[list[OCRBlock]] = []
        for block in sorted(
            blocks, key=lambda item: ((item.bbox.y0 + item.bbox.y1) / 2, item.bbox.x0)
        ):
            center = (block.bbox.y0 + block.bbox.y1) / 2
            target = next(
                (
                    row
                    for row in rows
                    if abs(
                        center - sum((item.bbox.y0 + item.bbox.y1) / 2 for item in row) / len(row)
                    )
                    <= tolerance
                ),
                None,
            )
            if target is None:
                rows.append([block])
            else:
                target.append(block)
        return [sorted(row, key=lambda item: item.bbox.x0) for row in rows]

    def _parse_figures(self, pages: list[PageOCR], document_id: str) -> list[FigureNote]:
        figures: list[FigureNote] = []
        for page in pages:
            for block in page.blocks:
                match = FIGURE_PATTERN.match(block.text.strip())
                if match:
                    number, title = match.group(1), match.group(2).strip()
                    figures.append(
                        FigureNote(
                            figure_id=stable_id(ResourceType.CHUNK, document_id, "figure", number),
                            document_id=document_id,
                            figure_number=number,
                            title=title or f"图 {number}",
                            page_number=page.page_number,
                            image_path=page.image_path,
                            source_spans=[self._span(page, block)],
                        )
                    )
        return figures

    @staticmethod
    def _parse_terms(clauses: list[Clause], document_id: str) -> list[Term]:
        terms: list[Term] = []
        for clause in clauses:
            if not clause.clause_number.startswith("3."):
                continue
            lines = [line.strip() for line in clause.content.splitlines() if line.strip()]
            title_index = -1
            title_match: re.Match[str] | None = None
            for index, line in enumerate(lines):
                match = TERM_LINE_PATTERN.match(line)
                if match and re.search(r"[\u4e00-\u9fff]{2,}", match.group(1)):
                    title_index = index
                    title_match = match
                    break
            if title_match is None:
                continue
            name, english_name = title_match.groups()
            definition = "\n".join(lines[title_index + 1 :]).strip()
            if not definition:
                continue
            terms.append(
                Term(
                    term_id=stable_id(ResourceType.CHUNK, document_id, "term", name),
                    document_id=document_id,
                    name=name.strip(),
                    english_name=english_name.strip(),
                    definition=definition.strip(),
                    clause_id=clause.clause_id,
                    source_spans=clause.source_spans,
                )
            )
        return terms

    def _knowledge_units(
        self,
        clauses: list[Clause],
        tables: list[StructuredTable],
        terms: list[Term],
        figures: list[FigureNote],
    ) -> list[KnowledgeUnit]:
        units: list[KnowledgeUnit] = []
        for clause in clauses:
            units.append(
                KnowledgeUnit(
                    unit_id=clause.clause_id,
                    unit_type=UnitType.CLAUSE,
                    title=clause.title,
                    content=f"{clause.clause_number} {clause.content}".strip(),
                    clause_id=clause.clause_id,
                    parent_id=clause.parent_id,
                    chapter_path=clause.chapter_path,
                    source_spans=clause.source_spans,
                )
            )
        for table in tables:
            units.append(
                KnowledgeUnit(
                    unit_id=table.table_id,
                    unit_type=UnitType.TABLE,
                    title=table.title,
                    content=self._table_content(table),
                    table_id=table.table_id,
                    source_spans=table.source_spans,
                )
            )
            for row in table.rows:
                units.append(
                    KnowledgeUnit(
                        unit_id=row.row_id,
                        unit_type=UnitType.TABLE_ROW,
                        title=table.title,
                        content=" | ".join(row.cells),
                        table_id=table.table_id,
                        metadata={"row_index": row.row_index},
                        source_spans=row.source_spans or table.source_spans,
                    )
                )
        for term in terms:
            units.append(
                KnowledgeUnit(
                    unit_id=term.term_id,
                    unit_type=UnitType.TERM,
                    title=term.name,
                    content=f"{term.name} {term.english_name or ''}\n{term.definition}".strip(),
                    clause_id=term.clause_id,
                    source_spans=term.source_spans,
                )
            )
        for figure in figures:
            units.append(
                KnowledgeUnit(
                    unit_id=figure.figure_id,
                    unit_type=UnitType.FIGURE_NOTE,
                    title=figure.title,
                    content=f"图 {figure.figure_number} {figure.title}",
                    metadata={"needs_manual_annotation": figure.needs_manual_annotation},
                    source_spans=figure.source_spans,
                )
            )
        return units

    @staticmethod
    def _table_content(table: StructuredTable) -> str:
        lines = [f"表 {table.table_number} {table.title}"]
        if table.headers:
            lines.append(" | ".join(table.headers))
        lines.extend(" | ".join(row.cells) for row in table.rows)
        return "\n".join(lines)

    @staticmethod
    def _span(page: PageOCR, block: OCRBlock) -> SourceSpan:
        return SourceSpan(
            document_id=page.document_id,
            page_number=page.page_number,
            bbox=block.bbox,
            image_path=page.image_path,
            ocr_block_ids=[block.block_id],
        )

    @staticmethod
    def _quality_report(
        document_id: str,
        pages: list[PageOCR],
        clauses: list[Clause],
        tables: list[StructuredTable],
        terms: list[Term],
        figures: list[FigureNote],
        units: list[KnowledgeUnit],
        directory_pages: list[int],
        removed_ids: list[str],
        audits: list[AppliedCorrection],
    ) -> ParsingQualityReport:
        clause_ids = {clause.clause_id for clause in clauses}
        orphans = [
            clause.clause_id
            for clause in clauses
            if clause.parent_id and clause.parent_id not in clause_ids
        ]
        warnings: list[str] = []
        roots = [clause for clause in clauses if "." not in clause.clause_number]
        for previous, current in zip(roots, roots[1:], strict=False):
            if int(current.clause_number) > int(previous.clause_number) + 1:
                warnings.append(
                    f"根章节编号从 {previous.clause_number} 跳到 {current.clause_number}"
                )
        inconsistent = [
            table.table_id
            for table in tables
            if table.headers and any(len(row.cells) != len(table.headers) for row in table.rows)
        ]
        parsed_term_clause_ids = {term.clause_id for term in terms}
        term_clause_ids = {
            clause.clause_id for clause in clauses if clause.clause_number.startswith("3.")
        }
        return ParsingQualityReport(
            document_id=document_id,
            page_count=len(pages),
            excluded_directory_pages=directory_pages,
            removed_repeated_block_ids=removed_ids,
            clause_count=len(clauses),
            table_count=len(tables),
            term_count=len(terms),
            unparsed_term_clause_ids=sorted(term_clause_ids - parsed_term_clause_ids),
            figure_count=len(figures),
            knowledge_unit_count=len(units),
            orphan_clause_ids=orphans,
            numbering_warnings=warnings,
            empty_title_clause_ids=[clause.clause_id for clause in clauses if not clause.title],
            inconsistent_table_ids=inconsistent,
            manual_review_table_ids=[
                table.table_id
                for table in tables
                if table.parse_status == ParseStatus.NEEDS_MANUAL_ANNOTATION
            ],
            applied_corrections=audits,
        )


def write_structure_result(result: StructureResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads: list[tuple[str, Sequence[StrictModel]]] = [
        ("clauses.jsonl", result.clauses),
        ("tables.jsonl", result.tables),
        ("terms.jsonl", result.terms),
        ("figures.jsonl", result.figures),
        ("knowledge_units.jsonl", result.knowledge_units),
    ]
    for file_name, records in payloads:
        target = output_dir / file_name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            "".join(record.model_dump_json() + "\n" for record in records), encoding="utf-8"
        )
        temporary.replace(target)
    report_path = output_dir / "parsing_quality_report.json"
    temporary_report = report_path.with_suffix(".json.tmp")
    temporary_report.write_text(result.report.model_dump_json(indent=2), encoding="utf-8")
    temporary_report.replace(report_path)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "document_id": result.document_id,
                "clauses": len(result.clauses),
                "tables": len(result.tables),
                "terms": len(result.terms),
                "figures": len(result.figures),
                "knowledge_units": len(result.knowledge_units),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
