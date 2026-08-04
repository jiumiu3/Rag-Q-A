import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import IngestionError
from app.ingestion.structure_parser import StructureParser, write_structure_result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从完整 M1 OCR 输出恢复条款、表格与知识单元")
    parser.add_argument("paths", nargs="*", type=Path, help="M1 文档 OCR 目录")
    parser.add_argument("--corrections", type=Path, help="人工修订 YAML；不指定则不应用补丁")
    return parser.parse_args()


def resolve_ocr_path(path: Path) -> Path:
    """兼容 data/ocr、/app/data/ocr 以及常见的 app/data/ocr 写法。"""
    candidates = [path]
    if not path.is_absolute():
        candidates.extend([Path.cwd() / path, Path("/") / path])
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return path


def main() -> int:
    args = parse_args()
    settings = get_settings()
    paths = [resolve_ocr_path(path) for path in args.paths] or sorted(
        path.parent for path in settings.storage.ocr_dir.glob("*/manifest.json")
    )
    if not paths:
        raise SystemExit(f"未找到 M1 OCR 输出：{settings.storage.ocr_dir}")

    parser = StructureParser()
    failures = 0
    for ocr_dir in paths:
        try:
            result = parser.parse_directory(ocr_dir, corrections_path=args.corrections)
            output_dir = settings.storage.processed_dir / result.document_id
            write_structure_result(result, output_dir)
            print(
                json.dumps(
                    {
                        "document_id": result.document_id,
                        "output_dir": str(output_dir),
                        "clauses": len(result.clauses),
                        "tables": len(result.tables),
                        "terms": len(result.terms),
                        "figures": len(result.figures),
                        "knowledge_units": len(result.knowledge_units),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        except IngestionError as exc:
            failures += 1
            print(
                json.dumps(
                    {"ocr_dir": str(ocr_dir), "error": exc.message, "details": exc.details},
                    ensure_ascii=False,
                )
            )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
