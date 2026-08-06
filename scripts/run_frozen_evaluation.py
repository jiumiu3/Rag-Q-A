#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from app.evaluation.generated import run_frozen, write_report

parser = argparse.ArgumentParser()
parser.add_argument("--evaluation-dir", type=Path, default=Path("evaluation"))
parser.add_argument("--output-dir", type=Path, default=Path("evaluation/reports/latest/generated"))
args = parser.parse_args()
report = run_frozen(args.evaluation_dir)
write_report(report, args.output_dir)
print(
    json.dumps(
        {"output": str(args.output_dir), "failures": len(report["failures"])}, ensure_ascii=False
    )
)
