#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from app.evaluation.runner import run_all, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 M11 四类评测并生成 JSON/Markdown/HTML")
    parser.add_argument("--evaluation-dir", type=Path, default=Path("evaluation"))
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/reports/latest"))
    args = parser.parse_args()
    report = run_all(args.evaluation_dir)
    write_report(report, args.output_dir)
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "suites": {suite.name: suite.case_count for suite in report.suites},
                "failures": sum(len(suite.failures) for suite in report.suites),
                "code_hash": report.versions.code_hash,
                "index_version": report.versions.index_version,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
