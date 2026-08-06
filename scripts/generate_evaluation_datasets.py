#!/usr/bin/env python3
"""生成、校验并按需冻结五类正式评测数据。"""

import argparse
import json
from pathlib import Path

from app.evaluation.generated import generate_all, split_and_freeze, validate_datasets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-dir", type=Path, default=Path("evaluation"))
    parser.add_argument("--db", type=Path, default=Path("data/knowledge.db"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--regenerate-frozen", action="store_true")
    args = parser.parse_args()
    counts = generate_all(args.evaluation_dir, args.db, limit=args.limit)
    validation = validate_datasets(args.evaluation_dir, args.db)
    if not validation["valid"]:
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return 1
    frozen = (
        split_and_freeze(args.evaluation_dir, regenerate_frozen=args.regenerate_frozen)
        if args.freeze
        else None
    )
    print(
        json.dumps(
            {"counts": counts, "validation": validation, "frozen": frozen},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
