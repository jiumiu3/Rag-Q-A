#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.core.model_client import CompatibleEmbeddingClient
from app.knowledge.builder import IndexBuilder
from app.knowledge.repository import SQLiteKnowledgeRepository


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="构建 M3 SQLite、BM25 和向量索引")
    command.add_argument("--document-id", action="append", help="仅替换指定文档；可重复传入")
    command.add_argument("--check", action="store_true", help="只执行索引一致性检查")
    return command


def main() -> int:
    args = parser().parse_args()
    settings = get_settings()
    repository = SQLiteKnowledgeRepository(settings.storage.sqlite_path)
    embedding = (
        CompatibleEmbeddingClient(settings.model)
        if settings.model.base_url and settings.model.embedding_model
        else None
    )
    builder = IndexBuilder(repository, settings.storage.index_dir, embedding)
    if args.check:
        result = builder.check_consistency()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["consistent"] else 1
    report = builder.build(
        Path(settings.storage.processed_dir), Path(settings.storage.ocr_dir), args.document_id
    )
    output = report.model_dump(mode="json")
    output["consistency"] = builder.check_consistency()
    print(json.dumps(output, ensure_ascii=False))
    return 0 if not report.failures and output["consistency"]["consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
