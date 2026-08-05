#!/usr/bin/env python3
import argparse
import json
import math
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from app.api.qa import get_qa_service
from app.domain.models import IntentType, RetrievalFilters, RetrievalPlan


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _progress(done: int, total: int, started: float, label: str) -> None:
    elapsed = time.perf_counter() - started
    eta = elapsed / done * (total - done) if done else 0.0
    percent = done / total * 100 if total else 100.0
    print(
        f"\r[{done:>3}/{total:<3} {percent:5.1f}%] {label[:36]:<36} "
        f"elapsed={elapsed:6.1f}s eta={eta:6.1f}s",
        end="" if done < total else "\n",
        file=sys.stderr,
        flush=True,
    )


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(math.ceil(len(ordered) * ratio) - 1, len(ordered) - 1)]


def _summarize(rows: list[dict[str, Any]]) -> dict[str, object]:
    count = len(rows)
    if not count:
        return {"case_count": 0}
    return {
        "case_count": count,
        "recall_at_1": sum(item["hit_at_1"] for item in rows) / count,
        "recall_at_5": sum(item["hit_at_5"] for item in rows) / count,
        "mrr": sum(item["reciprocal_rank"] for item in rows) / count,
        "full_goal_coverage_rate": sum(item["full_goal_coverage"] for item in rows) / count,
        "mean_goal_coverage": sum(item["goal_coverage"] for item in rows) / count,
        "latency_ms_p50": statistics.median(item["duration_ms"] for item in rows),
        "latency_ms_p95": _percentile([item["duration_ms"] for item in rows], 0.95),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="运行小规模分层真实检索评测并显示进度")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evaluation/datasets/retrieval_realistic_small.jsonl"),
    )
    parser.add_argument(
        "--annotations",
        type=Path,
        default=Path("evaluation/annotations/retrieval_realistic_review.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/reports/latest/retrieval_realistic.json"),
    )
    parser.add_argument(
        "--routes",
        nargs="+",
        choices=("bm25", "vector", "hybrid"),
        default=["bm25", "hybrid"],
        help="默认只跑 BM25 和 Hybrid；增加 vector 会增加 Embedding API 调用",
    )
    parser.add_argument("--limit", type=int, help="仅运行前 N 条，用于冒烟测试")
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument(
        "--allow-local-hash",
        action="store_true",
        help="仅供管线冒烟；允许本地哈希向量，但报告不得作为真实 Vector 效果",
    )
    args = parser.parse_args()

    cases = _jsonl(args.dataset)
    if args.limit is not None:
        cases = cases[: max(args.limit, 0)]
    annotations = {item["case_id"]: item for item in _jsonl(args.annotations)}
    service = get_qa_service().retrieval
    embedding_model_id = service.embedding.model_id
    uses_vector = any(route in {"vector", "hybrid"} for route in args.routes)
    if uses_vector and embedding_model_id.startswith("local-hash") and not args.allow_local_hash:
        parser.error(
            "当前索引使用 local-hash Embedding，不能评估真实 Vector/Hybrid 效果；"
            "请配置真实 Embedding 模型并重新执行 build_index.py"
        )
    jobs = [
        (case, "exact" if case["query_type"] == "exact" else route)
        for case in cases
        for route in (["exact"] if case["query_type"] == "exact" else args.routes)
    ]
    started = time.perf_counter()
    details: list[dict[str, Any]] = []
    for done, (case, route) in enumerate(jobs, 1):
        analysis = service.analyzer.analyze(case["question"])
        retrievers: list[Literal["exact", "bm25", "vector"]]
        if route == "exact":
            retrievers = ["exact"]
        elif route == "hybrid":
            retrievers = ["bm25", "vector"]
        elif route == "vector":
            retrievers = ["vector"]
        else:
            retrievers = ["bm25"]
        plan = RetrievalPlan(
            intent=IntentType.CLAUSE_LOOKUP if route == "exact" else IntentType.KNOWLEDGE_QA,
            query=case["question"],
            exact_keys=[] if route != "exact" else analysis.clause_numbers,
            retrievers=retrievers,
            filters=RetrievalFilters(standard_codes=analysis.standard_codes),
            top_k=5,
        )
        call_started = time.perf_counter()
        result = service.execute(plan)
        duration_ms = (time.perf_counter() - call_started) * 1000
        returned = [item.unit_id for item in result.evidence if item.context_reason is None]
        expected = set(case["expected_unit_ids"])
        rank = next(
            (index for index, unit_id in enumerate(returned, 1) if unit_id in expected), None
        )
        goals = case["retrieval_goals"]
        goal_hits = [
            bool(set(goal["relevant_unit_ids"]).intersection(returned[:5])) for goal in goals
        ]
        review = annotations.get(case["case_id"], {"status": "missing"})
        details.append(
            {
                "case_id": case["case_id"],
                "query_type": case["query_type"],
                "difficulty": case["difficulty"],
                "route": route,
                "annotation_status": review["status"],
                "returned_unit_ids": returned[:5],
                "hit_at_1": bool(expected.intersection(returned[:1])),
                "hit_at_5": bool(expected.intersection(returned[:5])),
                "reciprocal_rank": 1 / rank if rank else 0.0,
                "goal_coverage": sum(goal_hits) / len(goal_hits),
                "full_goal_coverage": all(goal_hits),
                "duration_ms": duration_ms,
            }
        )
        if not args.no_progress:
            _progress(done, len(jobs), started, f"{case['case_id']}:{route}")

    by_route: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    by_stratum: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    approved: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for detail in details:
        by_route[detail["route"]].append(detail)
        by_stratum[f"{detail['route']}:{detail['query_type']}"].append(detail)
        if detail["annotation_status"] == "approved":
            approved[detail["route"]].append(detail)
    payload = {
        "status": "MEASURED" if approved else "PROVISIONAL",
        "warning": (
            "当前标签尚未完成领域专家审核；指标只能用于调试，不能宣称真实生产效果。"
            if not approved
            else None
        ),
        "query_count": len(cases),
        "execution_count": len(jobs),
        "embedding_call_upper_bound": sum(route in {"vector", "hybrid"} for _, route in jobs),
        "embedding_model_id": embedding_model_id,
        "uses_real_embedding": not embedding_model_id.startswith("local-hash"),
        "elapsed_seconds": time.perf_counter() - started,
        "metrics_all_candidates": {
            route: _summarize(items) for route, items in sorted(by_route.items())
        },
        "metrics_approved_only": {
            route: _summarize(items) for route, items in sorted(approved.items())
        },
        "metrics_by_stratum": {key: _summarize(items) for key, items in sorted(by_stratum.items())},
        "details": details,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in payload.items() if key != "details"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
