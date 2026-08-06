#!/usr/bin/env python3
"""仅使用 dev 集分析语义候选并选择 RRF 权重。"""

import argparse
import json
import time
from pathlib import Path
from typing import Any

from app.api.qa import get_qa_service
from app.knowledge.indexes import allowed_unit_ids

WEIGHT_GRID = (
    (1.0, 0.25),
    (1.0, 0.5),
    (1.0, 0.75),
    (1.0, 1.0),
    (1.25, 0.75),
    (1.5, 0.5),
    (2.0, 0.5),
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def metrics(rows: list[dict[str, Any]], key: str) -> dict[str, float | int]:
    top1 = top5 = full = 0
    rr = 0.0
    for row in rows:
        ids = row[key]
        expected = set(row["expected_unit_ids"])
        ranks = [index for index, unit_id in enumerate(ids[:5], 1) if unit_id in expected]
        top1 += int(bool(expected.intersection(ids[:1])))
        top5 += int(bool(ranks))
        full += int(expected.issubset(set(ids[:5])))
        rr += 1 / min(ranks) if ranks else 0
    count = len(rows)
    return {
        "cases": count,
        "recall_at_1": top1 / count,
        "recall_at_5": top5 / count,
        "mrr": rr / count,
        "full_coverage_at_5": full / count,
    }


def fuse(bm25: list[str], vector: list[str], bm25_weight: float, vector_weight: float) -> list[str]:
    scores: dict[str, float] = {}
    for weight, ranking in ((bm25_weight, bm25), (vector_weight, vector)):
        for rank, unit_id in enumerate(ranking, 1):
            scores[unit_id] = scores.get(unit_id, 0.0) + weight / (60 + rank)
    return [unit_id for unit_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/dev/retrieval.jsonl"))
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/reports/latest/fusion_dev.json")
    )
    args = parser.parse_args()
    service = get_qa_service().retrieval
    source = [
        row
        for row in read_jsonl(args.dataset)
        if row["status"] == "approved" and not row["should_refuse"]
    ]
    cached: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index, row in enumerate(source, 1):
        analysis, plan = service.analyzer.plan(row["query"], 20)
        units = service.repository.list_units(plan.filters)
        allowed = allowed_unit_ids(units, plan.filters)
        bm25 = [hit.knowledge_unit_id for hit in service.bm25.search(row["query"], allowed, 80)]
        query_vector = service.embedding.embed([row["query"]])[0]
        vector = [hit.knowledge_unit_id for hit in service.vector.search(query_vector, allowed, 80)]
        cached.append({**row, "bm25": bm25, "vector": vector})
        elapsed = time.perf_counter() - started
        eta = elapsed / index * (len(source) - index)
        print(
            f"\rdev candidates {index}/{len(source)} elapsed={elapsed:.1f}s eta={eta:.1f}s",
            end="",
            flush=True,
        )
    print()
    candidates = []
    for bm25_weight, vector_weight in WEIGHT_GRID:
        key = f"hybrid_{bm25_weight}_{vector_weight}"
        for row in cached:
            row[key] = fuse(row["bm25"], row["vector"], bm25_weight, vector_weight)
        score = metrics(cached, key)
        candidates.append({"bm25_weight": bm25_weight, "vector_weight": vector_weight, **score})
    candidates.sort(
        key=lambda item: (
            -float(item["recall_at_5"]),
            -float(item["full_coverage_at_5"]),
            -float(item["mrr"]),
        )
    )
    failures = []
    for row in cached:
        expected = set(row["expected_unit_ids"])
        for route in ("bm25", "vector"):
            ranking = row[route]
            if not expected.intersection(ranking[:5]):
                failures.append(
                    {
                        "case_id": row["case_id"],
                        "query_type": row["query_type"],
                        "route": route,
                        "expected_ranks": [
                            ranking.index(unit_id) + 1 for unit_id in expected if unit_id in ranking
                        ],
                        "top5": ranking[:5],
                    }
                )
    report = {
        "dataset": str(args.dataset),
        "selection_policy": "recall_at_5, full_coverage_at_5, mrr；仅使用 dev",
        "bm25": metrics(cached, "bm25"),
        "vector": metrics(cached, "vector"),
        "weight_candidates": candidates,
        "selected": candidates[0],
        "semantic_candidate_failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["selected"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
