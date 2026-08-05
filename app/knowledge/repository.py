import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from app.domain.models import KnowledgeUnit, RetrievalFilters
from app.knowledge.models import IndexVersion, SearchHit, StoredUnit

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS documents (
 document_id TEXT PRIMARY KEY, file_name TEXT NOT NULL, standard_code TEXT NOT NULL,
 source_hash TEXT NOT NULL, page_count INTEGER NOT NULL, metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_units (
 knowledge_unit_id TEXT PRIMARY KEY,
 document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
 unit_type TEXT NOT NULL, title TEXT, content TEXT NOT NULL, clause_id TEXT, clause_number TEXT,
 parent_id TEXT, table_id TEXT, table_number TEXT, parse_status TEXT, unit_json TEXT NOT NULL,
 context_prefix TEXT NOT NULL DEFAULT '', retrieval_text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_unit_exact
 ON knowledge_units(document_id, clause_number, table_number, unit_type);
CREATE TABLE IF NOT EXISTS source_spans (
 knowledge_unit_id TEXT NOT NULL REFERENCES knowledge_units(knowledge_unit_id) ON DELETE CASCADE,
 span_index INTEGER NOT NULL, page_number INTEGER NOT NULL, span_json TEXT NOT NULL,
 PRIMARY KEY (knowledge_unit_id, span_index)
);
CREATE TABLE IF NOT EXISTS tables (
 table_id TEXT PRIMARY KEY,
 document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
 table_number TEXT NOT NULL, title TEXT NOT NULL,
 parse_status TEXT NOT NULL, table_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS table_rows (
 row_id TEXT PRIMARY KEY, table_id TEXT NOT NULL REFERENCES tables(table_id) ON DELETE CASCADE,
 row_index INTEGER NOT NULL, row_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terms (
 term_id TEXT PRIMARY KEY,
 document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
 name TEXT NOT NULL, english_name TEXT, definition TEXT NOT NULL, term_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS index_versions (
 version_id TEXT PRIMARY KEY, source_hash TEXT NOT NULL, config_hash TEXT NOT NULL,
 embedding_model_id TEXT NOT NULL, build_time TEXT NOT NULL, unit_count INTEGER NOT NULL,
 bm25_count INTEGER NOT NULL, vector_count INTEGER NOT NULL
);
"""


class SQLiteKnowledgeRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        # SQLite 的外键开关是连接级设置，每次连接都必须启用才能执行 ON DELETE CASCADE。
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(knowledge_units)")}
            if "context_prefix" not in columns:
                connection.execute(
                    "ALTER TABLE knowledge_units ADD COLUMN context_prefix TEXT NOT NULL DEFAULT ''"
                )
            if "retrieval_text" not in columns:
                connection.execute(
                    "ALTER TABLE knowledge_units ADD COLUMN retrieval_text TEXT NOT NULL DEFAULT ''"
                )

    def replace_document(self, document: dict[str, object], units: Sequence[StoredUnit]) -> None:
        self.initialize()
        document_id = str(document["document_id"])
        with self._connection() as connection:
            connection.execute("DELETE FROM documents WHERE document_id=?", (document_id,))
            connection.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?)",
                (
                    document_id,
                    document["file_name"],
                    document["standard_code"],
                    document["source_hash"],
                    document["page_count"],
                    json.dumps(document, ensure_ascii=False),
                ),
            )
            for stored in units:
                unit = stored.unit
                connection.execute(
                    "INSERT INTO knowledge_units "
                    "(knowledge_unit_id,document_id,unit_type,title,content,clause_id,"
                    "clause_number,parent_id,table_id,table_number,parse_status,unit_json,"
                    "context_prefix,retrieval_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        unit.unit_id,
                        document_id,
                        unit.unit_type,
                        unit.title,
                        unit.content,
                        unit.clause_id,
                        stored.clause_number,
                        unit.parent_id,
                        unit.table_id,
                        stored.table_number,
                        stored.parse_status,
                        unit.model_dump_json(),
                        stored.context_prefix,
                        stored.retrieval_text,
                    ),
                )
                for index, span in enumerate(unit.source_spans):
                    connection.execute(
                        "INSERT INTO source_spans VALUES(?,?,?,?)",
                        (unit.unit_id, index, span.page_number, span.model_dump_json()),
                    )

    def import_tables_and_terms(
        self,
        document_id: str,
        tables: Sequence[dict[str, object]],
        terms: Sequence[dict[str, object]],
    ) -> None:
        with self._connection() as connection:
            for table in tables:
                connection.execute(
                    "INSERT INTO tables VALUES(?,?,?,?,?,?)",
                    (
                        table["table_id"],
                        document_id,
                        table["table_number"],
                        table["title"],
                        table["parse_status"],
                        json.dumps(table, ensure_ascii=False),
                    ),
                )
                rows = cast(list[dict[str, object]], table.get("rows", []))
                for row in rows:
                    assert isinstance(row, dict)
                    connection.execute(
                        "INSERT INTO table_rows VALUES(?,?,?,?)",
                        (
                            row["row_id"],
                            table["table_id"],
                            row["row_index"],
                            json.dumps(row, ensure_ascii=False),
                        ),
                    )
            for term in terms:
                connection.execute(
                    "INSERT INTO terms VALUES(?,?,?,?,?,?)",
                    (
                        term["term_id"],
                        document_id,
                        term["name"],
                        term.get("english_name"),
                        term["definition"],
                        json.dumps(term, ensure_ascii=False),
                    ),
                )

    def delete_document(self, document_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM documents WHERE document_id=?", (document_id,))

    def list_document_ids(self) -> list[str]:
        self.initialize()
        with self._connection() as connection:
            rows = connection.execute("SELECT document_id FROM documents ORDER BY document_id")
            return [row[0] for row in rows]

    @staticmethod
    def _stored(row: sqlite3.Row) -> StoredUnit:
        return StoredUnit(
            unit=KnowledgeUnit.model_validate_json(row["unit_json"]),
            document_id=row["document_id"],
            standard_code=row["standard_code"],
            file_name=row["file_name"],
            clause_number=row["clause_number"],
            table_number=row["table_number"],
            parse_status=row["parse_status"],
            context_prefix=row["context_prefix"],
            retrieval_text=row["retrieval_text"],
        )

    def get_unit(self, unit_id: str) -> StoredUnit | None:
        units = self.get_units([unit_id])
        return units[0] if units else None

    def get_units(self, unit_ids: Sequence[str]) -> list[StoredUnit]:
        if not unit_ids:
            return []
        marks = ",".join("?" for _ in unit_ids)
        query = (
            "SELECT u.*,d.standard_code,d.file_name FROM knowledge_units u "
            f"JOIN documents d USING(document_id) WHERE knowledge_unit_id IN ({marks})"
        )
        with self._connection() as connection:
            rows = connection.execute(query, tuple(unit_ids)).fetchall()
        mapped = {row["knowledge_unit_id"]: self._stored(row) for row in rows}
        return [mapped[item] for item in unit_ids if item in mapped]

    def list_units(self, filters: RetrievalFilters | None = None) -> list[StoredUnit]:
        filters = filters or RetrievalFilters()
        where, params = self._filter_sql(filters)
        query = (
            "SELECT u.*,d.standard_code,d.file_name FROM knowledge_units u "
            "JOIN documents d USING(document_id)" + where
        )
        with self._connection() as connection:
            return [self._stored(row) for row in connection.execute(query, params)]

    @staticmethod
    def _filter_sql(filters: RetrievalFilters) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if filters.standard_codes:
            marks = ",".join("?" for _ in filters.standard_codes)
            clauses.append(f"d.standard_code IN ({marks})")
            params.extend(filters.standard_codes)
        if filters.unit_types:
            marks = ",".join("?" for _ in filters.unit_types)
            clauses.append(f"u.unit_type IN ({marks})")
            params.extend(filters.unit_types)
        if filters.page_numbers:
            marks = ",".join("?" for _ in filters.page_numbers)
            clauses.append(
                "EXISTS(SELECT 1 FROM source_spans s "
                "WHERE s.knowledge_unit_id=u.knowledge_unit_id "
                f"AND s.page_number IN ({marks}))"
            )
            params.extend(filters.page_numbers)
        return ((" WHERE " + " AND ".join(clauses)) if clauses else "", params)

    def exact_search(
        self, keys: Sequence[str], filters: RetrievalFilters, limit: int
    ) -> list[SearchHit]:
        if not keys:
            return []
        where, params = self._filter_sql(filters)
        prefix = " AND " if where else " WHERE "
        key_match = (
            "lower(coalesce(u.clause_number,''))=lower(?) OR "
            "lower(coalesce(u.table_number,''))=lower(?) OR "
            "lower(d.standard_code)=lower(?) OR lower(coalesce(u.title,''))=lower(?)"
        )
        key_sql = " OR ".join(key_match for _ in keys)
        for key in keys:
            params.extend([key, key, key, key])
        query = (
            "SELECT u.knowledge_unit_id FROM knowledge_units u "
            "JOIN documents d USING(document_id)" + where + prefix + f"({key_sql}) LIMIT ?"
        )
        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [SearchHit(knowledge_unit_id=row[0], score=1.0, source="exact") for row in rows]

    def save_index_version(self, version: IndexVersion) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO index_versions VALUES(?,?,?,?,?,?,?,?)",
                (
                    version.version_id,
                    version.source_hash,
                    version.config_hash,
                    version.embedding_model_id,
                    version.build_time.isoformat(),
                    version.unit_count,
                    version.bm25_count,
                    version.vector_count,
                ),
            )
