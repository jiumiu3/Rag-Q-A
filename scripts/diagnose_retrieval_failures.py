#!/usr/bin/env python3
"""按检索优化计划 P0 生成冻结测试集逐案例失败诊断报告。"""

import argparse
import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.api.qa import get_qa_service
from app.evaluation.generated import EvaluationEmbeddingCache
from app.knowledge.indexes import allowed_unit_ids


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 数据集，跳过空行。"""
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def percentile(values: list[float], ratio: float) -> float:
    """按 Python 内置统计模块的常见方式取 p95。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(math.ceil(len(ordered) * ratio) - 1, len(ordered) - 1)]


def fuse(
    bm25: list[str],
    vector: list[str],
    rrf_k: int,
    weights: dict[str, float],
) -> list[str]:
    """使用与 RetrievalService._rrf 相同的 RRF 融合规则。"""
    scores: dict[str, float] = {}
    for route, ranking, weight in (
        ("bm25", bm25, weights.get("bm25", 1.0)),
        ("vector", vector, weights.get("vector", 1.0)),
    ):
        for rank, unit_id in enumerate(ranking, 1):
            scores[unit_id] = scores.get(unit_id, 0.0) + weight / (rrf_k + rank)
    return [unit_id for unit_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def ranks_for(ids: list[str], expected: set[str]) -> dict[str, int]:
    """返回每个黄金单元在候选列表中的 1-based rank。"""
    return {unit_id: index for index, unit_id in enumerate(ids, 1) if unit_id in expected}


def metrics_for(
    rows: list[dict[str, Any]], key: str
) -> dict[str, Any]:
    """对同一候选键计算 Recall@1、Recall@5、MRR、nDCG@5 和完整覆盖。"""
    top1 = top5 = full = 0
    rr = ndcg = 0.0
    for row in rows:
        ids = row[key][:5]
        expected = set(row["gold_unit_ids"])
        ranks = [rank for rank, unit_id in enumerate(ids, 1) if unit_id in expected]
        top1 += int(bool(expected.intersection(ids[:1])))
        top5 += int(bool(ranks))
        full += int(expected.issubset(set(ids)))
        rr += 1 / min(ranks) if ranks else 0.0
        ideal = sum(1 / math.log2(index + 1) for index in range(1, min(5, len(expected)) + 1))
        ndcg += sum(1 / math.log2(rank + 1) for rank in ranks) / ideal if ideal else 0.0
    count = len(rows) or 1
    return {
        "recall_at_1": top1 / count,
        "recall_at_5": top5 / count,
        "mrr": rr / count,
        "ndcg_at_5": ndcg / count,
        "full_coverage_at_5": full / count,
    }


def classify(
    expected: set[str],
    bm25: list[str],
    vector: list[str],
    hybrid: list[str],
    final_top5: list[str],
) -> str:
    """按 P0 定义的 A-F 分类失败原因。"""
    bm25_set = set(bm25)
    vector_set = set(vector)
    hybrid_set = set(hybrid)
    final_set = set(final_top5)
    if expected.issubset(bm25_set) and expected.issubset(vector_set):
        if expected.issubset(hybrid_set):
            return "A" if not expected.issubset(final_set) else "OK"
        return "D"
    if expected.issubset(bm25_set):
        return "C"
    if expected.issubset(vector_set):
        return "B"
    return "E"


def main() -> int:
    parser = argparse.ArgumentParser(description="P0 检索失败诊断")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evaluation/frozen_test/retrieval.jsonl"),
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("evaluation/reports/latest/query_embedding_cache.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("evaluation/reports/latest"),
    )
    args = parser.parse_args()

    rows = read_jsonl(args.dataset)
    service = get_qa_service().retrieval
    service.embedding = EvaluationEmbeddingCache(service.embedding, args.cache)

    # 所有真实知识单元 ID，用来识别明显指向不存在单元的标签问题。
    repository_ids = {unit.unit.unit_id for unit in service.repository.list_units()}
    evaluated = [
        row for row in rows if row["status"] == "approved" and not row["should_refuse"]
    ]
    unanswerable_count = sum(bool(row["should_refuse"]) for row in rows)
    details: list[dict[str, Any]] = []

    for row in evaluated:
        expected = set(row["expected_unit_ids"])
        analysis, base_plan = service.analyzer.plan(row["query"], 5)
        # P0 只诊断 BM25/Vector/Hybrid，不混入 Exact 路由。
        plan = base_plan.model_copy(
            update={
                "retrievers": ["bm25", "vector"],
                "exact_keys": [],
                "expansion_policy": None,
            }
        )
        units = service.repository.list_units(plan.filters)
        allowed = allowed_unit_ids(units, plan.filters)

        started = time.perf_counter()
        bm25_hits = service.bm25.search(row["query"], allowed, 20)
        bm25_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        query_vector = service.embedding.embed([row["query"]])[0]
        vector_hits = service.vector.search(query_vector, allowed, 20)
        vector_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        fused_ids = fuse(
            [hit.knowledge_unit_id for hit in bm25_hits],
            [hit.knowledge_unit_id for hit in vector_hits],
            service.rrf_k,
            service.rrf_weights,
        )
        fuse_ms = (time.perf_counter() - started) * 1000

        bm25_ids = [hit.knowledge_unit_id for hit in bm25_hits]
        vector_ids = [hit.knowledge_unit_id for hit in vector_hits]
        hybrid_ids = fused_ids[:10]
        final_top5 = fused_ids[:5]
        gold_ranks_bm25 = ranks_for(bm25_ids, expected)
        gold_ranks_vector = ranks_for(vector_ids, expected)
        gold_ranks_hybrid = ranks_for(hybrid_ids, expected)

        if not expected.issubset(repository_ids):
            category = "F"
        else:
            category = classify(
                expected,
                bm25_ids,
                vector_ids,
                hybrid_ids,
                final_top5,
            )

        details.append(
            {
                "case_id": row["case_id"],
                "query": row["query"],
                "query_type": row["query_type"],
                "difficulty": row["difficulty"],
                "gold_unit_ids": sorted(expected),
                "bm25_gold_ranks": gold_ranks_bm25,
                "vector_gold_ranks": gold_ranks_vector,
                "hybrid_gold_ranks": gold_ranks_hybrid,
                "bm25_top20": bm25_ids,
                "vector_top20": vector_ids,
                "hybrid_top10": hybrid_ids,
                "hybrid_top5": final_top5,
                "classification": category,
                "latency_ms": {
                    "bm25": bm25_ms,
                    "vector": vector_ms,
                    "hybrid": bm25_ms + vector_ms + fuse_ms,
                },
            }
        )

    for row in details:
        row["bm25_top5"] = row["bm25_top20"][:5]
        row["vector_top5"] = row["vector_top20"][:5]

    metrics = {
        "bm25": metrics_for(details, "bm25_top5"),
        "vector": metrics_for(details, "vector_top5"),
        "hybrid": metrics_for(details, "hybrid_top5"),
    }
    for route in ("bm25", "vector", "hybrid"):
        values = [row["latency_ms"][route] for row in details]
        metrics[route]["latency_p50_ms"] = percentile(values, 0.5)
        metrics[route]["latency_p95_ms"] = percentile(values, 0.95)

    failure_counts = Counter(row["classification"] for row in details)
    report = {
        "dataset": str(args.dataset),
        "evaluated_cases": len(details),
        "excluded_unanswerable_cases": unanswerable_count,
        "metrics": metrics,
        "failure_counts": dict(sorted(failure_counts.items())),
        "cases": details,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "retrieval_failure_analysis.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(report, args.output_dir / "retrieval_failure_analysis.md")
    print(json.dumps(report["failure_counts"], ensure_ascii=False))
    print(f"wrote {json_path}")
    return 0


def write_markdown(report: dict[str, Any], path: Path) -> None:
    """把诊断结果转成便于人工阅读的 Markdown。"""
    lines = [
        "# 检索失败诊断",
        "",
        f"- 数据集：`{report['dataset']}`",
        f"- 评测案例：{report['evaluated_cases']}",
        f"- 排除不可回答案例：{report['excluded_unanswerable_cases']}",
        "",
        "## 路由指标",
        "",
        "| 路由 | Recall@1 | Recall@5 | MRR | nDCG@5 | P50 | P95 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for route in ("bm25", "vector", "hybrid"):
        metric = report["metrics"][route]
        lines.append(
            f"| {route} | {metric['recall_at_1']:.4f} | {metric['recall_at_5']:.4f} "
            f"| {metric['mrr']:.4f} | {metric['ndcg_at_5']:.4f} "
            f"| {metric['latency_p50_ms']:.1f}ms | {metric['latency_p95_ms']:.1f}ms |"
        )
    lines.extend(["", "## 失败分类", "", "| 分类 | 数量 |", "|---|---:|"])
    labels = {
        "A": "排序问题",
        "B": "词法检索问题",
        "C": "语义检索问题",
        "D": "融合问题",
        "E": "真正召回问题",
        "F": "标签问题",
        "OK": "正常",
    }
    for category, count in report["failure_counts"].items():
        lines.append(f"| {category} | {count} |")
    lines.extend(["", "## 逐案例诊断", ""])
    for row in report["cases"]:
        lines.append(f"### {row['case_id']} · {row['query_type']} · {row['classification']}")
        lines.append(f"- Query：`{row['query']}`")
        lines.append(f"- Gold：`{row['gold_unit_ids']}`")
        lines.append(f"- BM25 gold rank：`{row['bm25_gold_ranks']}`")
        lines.append(f"- Vector gold rank：`{row['vector_gold_ranks']}`")
        lines.append(f"- Hybrid gold rank：`{row['hybrid_gold_ranks']}`")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
