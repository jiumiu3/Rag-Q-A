#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from app.api.qa import get_qa_service


def main() -> int:
    parser = argparse.ArgumentParser(description="复现 M5 路由、状态和引用评测")
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    rows = [
        json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line
    ]
    service = get_qa_service()
    passed = 0
    failures: list[dict[str, str]] = []
    for row in rows:
        answer, _ = service.ask(row["question"])
        valid = answer.intent == row["expected_intent"] and answer.status == row["expected_status"]
        valid = valid and (answer.status != "SUFFICIENT" or bool(answer.citations))
        passed += int(valid)
        if not valid:
            failures.append(
                {"question": row["question"], "actual": f"{answer.intent}/{answer.status}"}
            )
    print(
        json.dumps(
            {
                "count": len(rows),
                "passed": passed,
                "accuracy": passed / (len(rows) or 1),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
