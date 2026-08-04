import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from app.domain.models import KnowledgeUnit
from app.knowledge.indexes import (
    BM25Index,
    EmbeddingClient,
    HashEmbeddingClient,
    VectorIndex,
    tokenize,
)
from app.knowledge.models import BuildReport, IndexVersion, StoredUnit
from app.knowledge.repository import SQLiteKnowledgeRepository

STANDARD_PATTERN = re.compile(r"Q[-/]?GGW\s*02005[.．](\d)", re.I)


def _jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def document_metadata(processed_dir: Path, ocr_dir: Path) -> dict[str, object]:
    manifest = json.loads((ocr_dir / "manifest.json").read_text(encoding="utf-8"))
    source_path = str(manifest["source_path"])
    file_name = Path(source_path).name
    match = STANDARD_PATTERN.search(file_name.replace("-", "/"))
    if not match:
        raise ValueError(f"无法从文件名识别标准编号：{file_name}")
    return {
        "document_id": processed_dir.name,
        "file_name": file_name,
        "standard_code": f"Q/GGW 02005.{match.group(1)}-2022",
        "source_hash": manifest["source_sha256"],
        "page_count": manifest["source_page_count"],
    }


class IndexBuilder:
    def __init__(
        self,
        repository: SQLiteKnowledgeRepository,
        index_dir: Path,
        embedding: EmbeddingClient | None = None,
    ) -> None:
        self.repository = repository
        self.index_dir = index_dir
        self.embedding = embedding or HashEmbeddingClient()

    def build(
        self, processed_root: Path, ocr_root: Path, document_ids: list[str] | None = None
    ) -> BuildReport:
        started = time.perf_counter()
        failures: list[str] = []
        directories = sorted(item for item in processed_root.iterdir() if item.is_dir())
        if document_ids:
            directories = [item for item in directories if item.name in document_ids]
        else:
            # 全量模式移除源目录中已不存在的旧文档；增量模式只替换指定文档。
            source_ids = {item.name for item in directories}
            for stale_id in set(self.repository.list_document_ids()) - source_ids:
                self.repository.delete_document(stale_id)
        for directory in directories:
            try:
                metadata = document_metadata(directory, ocr_root / directory.name)
                clauses = {item["clause_id"]: item for item in _jsonl(directory / "clauses.jsonl")}
                tables = {item["table_id"]: item for item in _jsonl(directory / "tables.jsonl")}
                units: list[StoredUnit] = []
                for payload in _jsonl(directory / "knowledge_units.jsonl"):
                    unit = KnowledgeUnit.model_validate(payload)
                    clause = clauses.get(unit.clause_id or "", {})
                    table = tables.get(unit.table_id or "", {})
                    units.append(
                        StoredUnit(
                            unit=unit,
                            document_id=directory.name,
                            standard_code=str(metadata["standard_code"]),
                            file_name=str(metadata["file_name"]),
                            clause_number=clause.get("clause_number"),
                            table_number=table.get("table_number"),
                            parse_status=table.get("parse_status"),
                        )
                    )
                self.repository.replace_document(metadata, units)
                self.repository.import_tables_and_terms(
                    directory.name, list(tables.values()), _jsonl(directory / "terms.jsonl")
                )
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                failures.append(f"{directory.name}: {exc}")
        all_units = self.repository.list_units()
        documents = {
            unit.unit.unit_id: tokenize(f"{unit.unit.title or ''} {unit.unit.content}")
            for unit in all_units
        }
        BM25Index(documents).save(self.index_dir / "bm25.json")
        texts = [f"{unit.unit.title or ''} {unit.unit.content}" for unit in all_units]
        vectors = self.embedding.embed(texts)
        VectorIndex([unit.unit.unit_id for unit in all_units], vectors).save(self.index_dir)
        source_hash = hashlib.sha256(
            "".join(sorted(unit.unit.unit_id for unit in all_units)).encode()
        ).hexdigest()
        embedding_config = getattr(self.embedding, "configuration_id", self.embedding.model_id)
        config_hash = hashlib.sha256(
            f"bm25:k1=1.5,b=0.75;embedding={embedding_config}".encode()
        ).hexdigest()
        version_id_hash = hashlib.sha256(f"{source_hash}:{config_hash}".encode()).hexdigest()
        version = IndexVersion(
            version_id=f"index_{version_id_hash[:20]}",
            source_hash=source_hash,
            config_hash=config_hash,
            embedding_model_id=self.embedding.model_id,
            build_time=datetime.now(UTC),
            unit_count=len(all_units),
            bm25_count=len(documents),
            vector_count=len(vectors),
        )
        self.repository.save_index_version(version)
        (self.index_dir / "manifest.json").write_text(
            version.model_dump_json(indent=2), encoding="utf-8"
        )
        return BuildReport(
            version=version,
            document_count=len(directories) - len(failures),
            failures=failures,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    def check_consistency(self) -> dict[str, int | bool]:
        unit_count = len(self.repository.list_units())
        bm25_count = len(BM25Index.load(self.index_dir / "bm25.json").documents)
        vector = VectorIndex.load(self.index_dir)
        vector_count = len(vector.ids)
        return {
            "unit_count": unit_count,
            "bm25_count": bm25_count,
            "vector_count": vector_count,
            "consistent": unit_count == bm25_count == vector_count == len(vector.vectors),
        }
