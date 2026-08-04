#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from app.api.qa import get_qa_service


def main() -> int:
    parser = argparse.ArgumentParser(description="复现 M4 Recall@K 与 MRR 评测")
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    rows = [
        json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line
    ]
    retrieval = get_qa_service().retrieval
    recall_1 = recall_5 = reciprocal_rank = 0.0
    failures: list[dict[str, object]] = []
    for row in rows:
        result = retrieval.retrieve(row["question"], 5)
        ids = [item.unit_id for item in result.evidence if item.context_reason is None]
        expected = set(row["expected_unit_ids"])
        recall_1 += float(bool(expected.intersection(ids[:1])))
        recall_5 += float(bool(expected.intersection(ids[:5])))
        rank = next((index for index, key in enumerate(ids, 1) if key in expected), None)
        reciprocal_rank += 1 / rank if rank else 0
        if not rank:
            failures.append({"question": row["question"], "returned": ids})
    total = len(rows) or 1
    print(
        json.dumps(
            {
                "count": len(rows),
                "recall_at_1": recall_1 / total,
                "recall_at_5": recall_5 / total,
                "mrr": reciprocal_rank / total,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
