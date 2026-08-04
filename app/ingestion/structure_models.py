from enum import StrEnum

from pydantic import Field, model_validator

from app.domain.models import KnowledgeUnit, ParseStatus, SourceSpan, StrictModel


class Clause(StrictModel):
    clause_id: str
    document_id: str
    clause_number: str
    title: str | None = None
    content: str
    parent_id: str | None = None
    children_ids: list[str] = Field(default_factory=list)
    chapter_path: list[str] = Field(default_factory=list)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    source_spans: list[SourceSpan] = Field(min_length=1)


class TableRow(StrictModel):
    row_id: str
    table_id: str
    row_index: int = Field(ge=0)
    cells: list[str]
    page_number: int = Field(ge=1)
    source_spans: list[SourceSpan] = Field(default_factory=list)


class StructuredTable(StrictModel):
    table_id: str
    document_id: str
    table_number: str
    title: str
    headers: list[str] = Field(default_factory=list)
    rows: list[TableRow] = Field(default_factory=list)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    image_paths: list[str] = Field(default_factory=list)
    parse_status: ParseStatus
    source_spans: list[SourceSpan] = Field(default_factory=list)


class Term(StrictModel):
    term_id: str
    document_id: str
    name: str
    english_name: str | None = None
    definition: str
    clause_id: str | None = None
    source_spans: list[SourceSpan] = Field(min_length=1)


class FigureNote(StrictModel):
    figure_id: str
    document_id: str
    figure_number: str
    title: str
    page_number: int = Field(ge=1)
    image_path: str
    needs_manual_annotation: bool = True
    source_spans: list[SourceSpan] = Field(default_factory=list)


class CorrectionAction(StrEnum):
    REPLACE_TEXT = "replace_text"
    DROP_BLOCK = "drop_block"
    EXCLUDE_PAGE = "exclude_page"


class Correction(StrictModel):
    correction_id: str
    action: CorrectionAction
    document_id: str | None = None
    page_number: int = Field(ge=1)
    block_id: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    reason: str

    @model_validator(mode="after")
    def validate_action_fields(self) -> "Correction":
        if self.action in {CorrectionAction.REPLACE_TEXT, CorrectionAction.DROP_BLOCK}:
            if not self.block_id:
                raise ValueError(f"{self.action} 必须提供 block_id")
        if self.action == CorrectionAction.REPLACE_TEXT and self.new_text is None:
            raise ValueError("replace_text 必须提供 new_text")
        return self


class CorrectionSet(StrictModel):
    corrections: list[Correction] = Field(default_factory=list)


class AppliedCorrection(StrictModel):
    correction_id: str
    applied: bool
    message: str


class ParsingQualityReport(StrictModel):
    document_id: str
    page_count: int
    excluded_directory_pages: list[int] = Field(default_factory=list)
    removed_repeated_block_ids: list[str] = Field(default_factory=list)
    clause_count: int = 0
    table_count: int = 0
    term_count: int = 0
    unparsed_term_clause_ids: list[str] = Field(default_factory=list)
    figure_count: int = 0
    knowledge_unit_count: int = 0
    orphan_clause_ids: list[str] = Field(default_factory=list)
    numbering_warnings: list[str] = Field(default_factory=list)
    empty_title_clause_ids: list[str] = Field(default_factory=list)
    inconsistent_table_ids: list[str] = Field(default_factory=list)
    manual_review_table_ids: list[str] = Field(default_factory=list)
    applied_corrections: list[AppliedCorrection] = Field(default_factory=list)


class StructureResult(StrictModel):
    document_id: str
    clauses: list[Clause]
    tables: list[StructuredTable]
    terms: list[Term]
    figures: list[FigureNote]
    knowledge_units: list[KnowledgeUnit]
    report: ParsingQualityReport
