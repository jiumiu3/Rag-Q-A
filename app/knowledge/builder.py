import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from app.domain.models import KnowledgeUnit
from app.knowledge.contextual import ContextualTextBuilder
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
        contextual_enabled: bool = True,
        contextual_strategy: str = "deterministic",
        contextual_version: str = "v1",
    ) -> None:
        self.repository = repository
        self.index_dir = index_dir
        self.embedding = embedding or HashEmbeddingClient()
        if contextual_strategy != "deterministic":
            raise ValueError(f"不支持 contextual strategy：{contextual_strategy}")
        self.contextual = ContextualTextBuilder(contextual_version, contextual_enabled)

    def build(
        self, processed_root: Path, ocr_root: Path, document_ids: list[str] | None = None
    ) -> BuildReport:
        started = time.perf_counter()
        failures: list[str] = []
        directories = sorted(item for item in processed_root.iterdir() if item.is_dir())
        manifest_path = self.index_dir / "manifest.json"
        config_changed = True
        if manifest_path.exists():
            try:
                old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                config_changed = self.contextual_manifest_error(old_manifest) is not None
            except (OSError, json.JSONDecodeError):
                config_changed = True
        # 策略变化会影响每个单元的 retrieval_text，不能只更新指定文档。
        if document_ids and config_changed:
            document_ids = None
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
                clauses = {
                    str(item["clause_id"]): item for item in _jsonl(directory / "clauses.jsonl")
                }
                tables = {
                    str(item["table_id"]): item for item in _jsonl(directory / "tables.jsonl")
                }
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
                units = self._attach_table_locations(units, clauses)
                units = self.contextual.build(units, tables)
                self.repository.replace_document(metadata, units)
                self.repository.import_tables_and_terms(
                    directory.name, list(tables.values()), _jsonl(directory / "terms.jsonl")
                )
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                failures.append(f"{directory.name}: {exc}")
        all_units = self.repository.list_units()
        documents = {unit.unit.unit_id: tokenize(unit.retrieval_text) for unit in all_units}
        BM25Index(documents).save(self.index_dir / "bm25.json")
        texts = [unit.retrieval_text for unit in all_units]
        vectors = self.embedding.embed(texts)
        VectorIndex([unit.unit.unit_id for unit in all_units], vectors).save(self.index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        (self.index_dir / "contextual_units.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "unit_id": unit.unit.unit_id,
                        "context_prefix": unit.context_prefix,
                        "retrieval_text_hash": hashlib.sha256(
                            unit.retrieval_text.encode()
                        ).hexdigest(),
                        "strategy": self.contextual.strategy,
                        "strategy_version": self.contextual.version,
                    },
                    ensure_ascii=False,
                )
                + "\n"
                for unit in all_units
            ),
            encoding="utf-8",
        )
        source_hash = hashlib.sha256(
            "".join(
                sorted(
                    f"{unit.unit.unit_id}:"
                    f"{hashlib.sha256(unit.retrieval_text.encode()).hexdigest()}"
                    for unit in all_units
                )
            ).encode()
        ).hexdigest()
        embedding_config = getattr(self.embedding, "configuration_id", self.embedding.model_id)
        embedding_configuration_hash = hashlib.sha256(embedding_config.encode()).hexdigest()
        config_hash = hashlib.sha256(
            (
                f"bm25:k1=1.5,b=0.75;embedding={embedding_config};"
                f"contextual={self.contextual.config_hash}"
            ).encode()
        ).hexdigest()
        version_id_hash = hashlib.sha256(f"{source_hash}:{config_hash}".encode()).hexdigest()
        version = IndexVersion(
            version_id=f"index_{version_id_hash[:20]}",
            source_hash=source_hash,
            config_hash=config_hash,
            embedding_model_id=self.embedding.model_id,
            embedding_configuration_hash=embedding_configuration_hash,
            embedding_dimensions=len(vectors[0]),
            build_time=datetime.now(UTC),
            unit_count=len(all_units),
            bm25_count=len(documents),
            vector_count=len(vectors),
            contextual_enabled=self.contextual.enabled,
            contextual_strategy=self.contextual.strategy,
            contextual_strategy_version=self.contextual.version,
            contextual_config_hash=self.contextual.config_hash,
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

    @staticmethod
    def _attach_table_locations(
        units: list[StoredUnit], clauses: dict[str, dict[str, object]]
    ) -> list[StoredUnit]:
        clause_units = [item for item in units if item.unit.clause_id]
        output: list[StoredUnit] = []
        table_parents: dict[str, StoredUnit] = {}
        for stored in units:
            if stored.unit.unit_type != "TABLE":
                continue
            page = stored.unit.source_spans[0].page_number
            candidates = [
                item
                for item in clause_units
                if IndexBuilder._page_start(
                    clauses.get(item.unit.clause_id or "", {}).get("page_start")
                )
                <= page
            ]
            if candidates:
                table_parents[stored.unit.unit_id] = candidates[-1]
        for stored in units:
            parent = table_parents.get(stored.unit.table_id or "")
            if parent and stored.unit.unit_type in {"TABLE", "TABLE_ROW"}:
                unit = stored.unit.model_copy(
                    update={
                        "parent_id": parent.unit.unit_id,
                        "chapter_path": parent.unit.chapter_path,
                    }
                )
                stored = stored.model_copy(update={"unit": unit})
            output.append(stored)
        return output

    @staticmethod
    def _page_start(value: object) -> int:
        return value if isinstance(value, int) else 0

    def check_consistency(self) -> dict[str, int | bool | str]:
        unit_count = len(self.repository.list_units())
        bm25_count = len(BM25Index.load(self.index_dir / "bm25.json").documents)
        vector = VectorIndex.load(self.index_dir)
        vector_count = len(vector.ids)
        manifest_path = self.index_dir / "manifest.json"
        contextual_count = len(_jsonl(self.index_dir / "contextual_units.jsonl"))
        contextual_valid = False
        contextual_error = "索引 manifest 不存在"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            contextual_error = self.contextual_manifest_error(manifest) or ""
            contextual_valid = not contextual_error
        return {
            "unit_count": unit_count,
            "bm25_count": bm25_count,
            "vector_count": vector_count,
            "contextual_count": contextual_count,
            "contextual_valid": contextual_valid,
            "contextual_error": contextual_error,
            "consistent": (
                unit_count == bm25_count == vector_count == len(vector.vectors) == contextual_count
                and contextual_valid
            ),
        }

    def contextual_manifest_error(self, manifest: dict[str, object]) -> str | None:
        required = {
            "contextual_enabled",
            "contextual_strategy",
            "contextual_strategy_version",
            "contextual_config_hash",
        }
        missing = sorted(required - manifest.keys())
        if missing:
            return f"旧索引缺少上下文 manifest 字段 {missing}，请重新执行 build_index.py"
        expected = {
            "contextual_enabled": self.contextual.enabled,
            "contextual_strategy": self.contextual.strategy,
            "contextual_strategy_version": self.contextual.version,
            "contextual_config_hash": self.contextual.config_hash,
        }
        actual = {key: manifest.get(key) for key in expected}
        if actual != expected:
            return "上下文检索配置与索引 manifest 不一致，请重新执行 build_index.py"
        return None
