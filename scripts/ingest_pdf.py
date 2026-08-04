import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import IngestionError
from app.ingestion.engines import create_ocr_engine
from app.ingestion.pipeline import IngestionPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="渲染 PDF 并执行逐页 OCR")
    parser.add_argument(
        "paths", nargs="*", type=Path, help="PDF 路径；不传时处理 data/raw 下全部 PDF"
    )
    parser.add_argument("--force", action="store_true", help="忽略有效缓存并重新处理")
    parser.add_argument("--max-pages", type=int, help="仅处理前 N 页，用于冒烟测试")
    parser.add_argument(
        "--inspect-only", action="store_true", help="只读取哈希和文档元数据，不渲染/OCR"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()
    paths = args.paths or sorted(settings.storage.raw_data_dir.glob("*.pdf"))
    if not paths:
        raise SystemExit(f"未找到 PDF：{settings.storage.raw_data_dir}")
    pipeline = IngestionPipeline(settings, create_ocr_engine(settings.ocr.engine))
    exit_code = 0
    for path in paths:
        try:
            result = (
                pipeline.inspect_document(path).model_dump(mode="json")
                if args.inspect_only
                else pipeline.ingest(path, force=args.force, max_pages=args.max_pages).model_dump(
                    mode="json"
                )
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except IngestionError as exc:
            exit_code = 1
            print(json.dumps({"file": str(path), "error": exc.message}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
