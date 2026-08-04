#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from app.design.parser import DesignDescriptionParser


def main() -> int:
    parser = argparse.ArgumentParser(description="复现 M6 原子属性拆解评测")
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    rows = [
        json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line
    ]
    design_parser = DesignDescriptionParser()
    correct = 0
    failures: list[dict[str, object]] = []
    for row in rows:
        result = design_parser.parse(row["description"])
        actual = [item.attribute for item in result.items]
        expected = row["expected_attributes"]
        if actual == expected:
            correct += 1
        else:
            failures.append(
                {"description": row["description"], "expected": expected, "actual": actual}
            )
    print(
        json.dumps(
            {"count": len(rows), "exact_match": correct / (len(rows) or 1), "failures": failures},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
