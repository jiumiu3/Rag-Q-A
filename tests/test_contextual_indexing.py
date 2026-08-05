import json
from collections.abc import Sequence
from pathlib import Path

from app.domain.models import KnowledgeUnit, ParseStatus, SourceSpan, UnitType
from app.knowledge.builder import IndexBuilder
from app.knowledge.contextual import ContextualTextBuilder
from app.knowledge.indexes import BM25Index, VectorIndex, tokenize
from app.knowledge.models import StoredUnit
from app.knowledge.repository import SQLiteKnowledgeRepository
from app.retrieval.service import RetrievalService


def _stored(
    unit_id: str,
    unit_type: UnitType,
    content: str,
    *,
    title: str | None = None,
    clause_number: str | None = None,
    parent_id: str | None = None,
    table_id: str | None = None,
    table_number: str | None = None,
) -> StoredUnit:
    return StoredUnit(
        unit=KnowledgeUnit(
            unit_id=unit_id,
            unit_type=unit_type,
            title=title,
            content=content,
            clause_id=unit_id if unit_type == UnitType.CLAUSE else None,
            parent_id=parent_id,
            chapter_path=[parent_id, unit_id] if parent_id else [unit_id],
            table_id=table_id,
            source_spans=[SourceSpan(document_id="doc_1", page_number=8)],
        ),
        document_id="doc_1",
        standard_code="Q/GGW 02005.2-2022",
        file_name="Q-GGW 02005.2-2022.pdf",
        clause_number=clause_number,
        table_number=table_number,
        parse_status=ParseStatus.PARSED,
    )


def test_clause_prefix_resolves_readable_path_and_parent() -> None:
    parent = _stored("parent", UnitType.CLAUSE, "8.3.4 供电", title="供电", clause_number="8.3.4")
    child = _stored(
        "child",
        UnitType.CLAUSE,
        "8.3.4.4 UPS持续供电时间不应低于2 h。",
        title="UPS持续供电时间",
        clause_number="8.3.4.4",
        parent_id="parent",
    )
    enriched = ContextualTextBuilder().build([parent, child], {})[1]
    assert "[标准：Q/GGW 02005.2-2022]" in enriched.context_prefix
    assert "[位置：8.3.4 供电]" in enriched.context_prefix
    assert "[父条款：8.3.4 供电]" in enriched.context_prefix
    assert "parent" not in enriched.context_prefix
    assert enriched.retrieval_text.endswith(child.unit.content)


def test_table_row_pairs_headers_without_changing_source_text() -> None:
    table = _stored(
        "table", UnitType.TABLE, "表 6 UPS参数", title="UPS参数", table_id="table", table_number="6"
    )
    row = _stored(
        "row",
        UnitType.TABLE_ROW,
        "无人值守 | 2 | h",
        title="UPS参数",
        table_id="table",
        table_number="6",
    )
    tables = {
        "table": {
            "table_number": "6",
            "title": "UPS参数",
            "headers": ["站场运行方式", "最低持续供电时间", "单位"],
        }
    }
    enriched = ContextualTextBuilder().build([table, row], tables)[1]
    assert "站场运行方式：无人值守" in enriched.retrieval_text
    assert "最低持续供电时间：2" in enriched.retrieval_text
    assert enriched.unit.content == "无人值守 | 2 | h"


def test_mismatched_table_row_is_not_guessed() -> None:
    row = _stored("row", UnitType.TABLE_ROW, "无人值守 | 2", table_id="table", table_number="6")
    tables = {"table": {"headers": ["方式", "时间", "单位"]}}
    enriched = ContextualTextBuilder().build([row], tables)[0]
    assert enriched.parse_status == ParseStatus.NEEDS_MANUAL_ANNOTATION
    assert "列映射：NEEDS_MANUAL_ANNOTATION" in enriched.retrieval_text
    assert "方式：无人值守" not in enriched.retrieval_text


class CapturingEmbedding:
    model_id = "capture-v1"
    configuration_id = "capture-v1"

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts = list(texts)
        return [[1.0, 0.0] for _ in texts]


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def test_builder_indexes_retrieval_text_and_versions_context_config(tmp_path: Path) -> None:
    processed = tmp_path / "processed" / "doc_1"
    ocr = tmp_path / "ocr" / "doc_1"
    index = tmp_path / "index"
    ocr.mkdir(parents=True)
    (ocr / "manifest.json").write_text(
        json.dumps(
            {
                "source_path": "Q-GGW 02005.2-2022.pdf",
                "source_sha256": "a" * 64,
                "source_page_count": 1,
            }
        )
    )
    span = SourceSpan(document_id="doc_1", page_number=1).model_dump(mode="json")
    unit = KnowledgeUnit(
        unit_id="clause_1",
        unit_type=UnitType.CLAUSE,
        title="供电",
        content="8.3.4 UPS要求。",
        clause_id="clause_1",
        chapter_path=["clause_1"],
        source_spans=[SourceSpan.model_validate(span)],
    )
    _write_jsonl(
        processed / "clauses.jsonl",
        [
            {
                "clause_id": "clause_1",
                "clause_number": "8.3.4",
                "title": "供电",
                "page_start": 1,
            }
        ],
    )
    _write_jsonl(processed / "tables.jsonl", [])
    _write_jsonl(processed / "terms.jsonl", [])
    _write_jsonl(processed / "knowledge_units.jsonl", [unit.model_dump(mode="json")])
    embedding = CapturingEmbedding()
    repository = SQLiteKnowledgeRepository(tmp_path / "knowledge.db")
    first = IndexBuilder(repository, index, embedding, contextual_version="v1").build(
        tmp_path / "processed", tmp_path / "ocr"
    )
    stored = repository.get_unit("clause_1")
    assert stored is not None
    assert embedding.texts == [stored.retrieval_text]
    bm25 = BM25Index.load(index / "bm25.json")
    assert bm25.documents["clause_1"] == tokenize(stored.retrieval_text)
    assert stored.unit.content == "8.3.4 UPS要求。"
    retrieval = RetrievalService(repository, bm25, VectorIndex.load(index), CapturingEmbedding())
    evidence = retrieval.retrieve("第8.3.4条", 1).evidence[0]
    assert evidence.content == stored.unit.content
    assert evidence.citation.quote == stored.unit.content
    assert "[标准：" not in evidence.citation.quote
    second_builder = IndexBuilder(repository, index, embedding, contextual_version="v2")
    assert second_builder.contextual_manifest_error(
        json.loads((index / "manifest.json").read_text())
    )
    second = second_builder.build(tmp_path / "processed", tmp_path / "ocr")
    assert first.version.version_id != second.version.version_id


def test_old_manifest_is_rejected(tmp_path: Path) -> None:
    builder = IndexBuilder(SQLiteKnowledgeRepository(tmp_path / "db.sqlite"), tmp_path / "index")
    error = builder.contextual_manifest_error({"version_id": "old"})
    assert error is not None and "旧索引" in error
