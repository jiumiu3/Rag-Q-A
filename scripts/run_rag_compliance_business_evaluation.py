#!/usr/bin/env python3
"""通过真实会话工作流运行小规模RAG合规业务测试集。"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.api.agent import get_workflow_service
from app.core.config import get_settings
from app.workflow.models import WorkflowConfirmRequest, WorkflowStatus


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    metrics = report["metrics"]
    lines = [
        "# RAG合规真实工作流业务测试报告",
        "",
        f"- 案例数：{metrics['total_cases']}",
        f"- 完整工作流完成数：{metrics['completed_cases']}",
        f"- Retrieval Hit@5：{metrics['retrieval_hit_at_5']:.2%}",
        f"- 最终状态准确率：{metrics['status_accuracy']:.2%}",
        f"- False compliant：{metrics['false_compliant_count']}",
        f"- 错误或中断：{metrics['error_count']}",
        "",
        "| 案例 | 提取项数 | 工作流状态 | 召回 | 金标 | 结果 | 正确 |",
        "|---|---:|---|---:|---|---|---:|",
    ]
    for row in report["cases"]:
        lines.append(
            f"| {row['case_id']} | {len(row['extracted_check_items'])} | "
            f"{row['workflow_status']} | {'是' if row['retrieval_hit_at_5'] else '否'} | "
            f"{row['gold_status']} | {row.get('predicted_status') or '无'} | "
            f"{'是' if row['status_correct'] else '否'} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(dataset_path: Path, output_dir: Path, case_id: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.agent.rag_llm_enabled:
        raise RuntimeError("agent.rag_llm_enabled=false，真实模型评测被禁用")
    service = get_workflow_service()
    cases = _load_jsonl(dataset_path)
    if case_id:
        cases = [case for case in cases if case["case_id"] == case_id]
        if not cases:
            raise ValueError(f"数据集中不存在案例：{case_id}")
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        started = time.perf_counter()
        expected_ids = set(case["gold_retrieval"]["expected_unit_ids"])
        row: dict[str, Any] = {
            "case_id": case["case_id"],
            "gold_status": case["gold_judgement"]["status"],
            "expected_clause_id": case["gold_retrieval"]["clause_id"],
            "extracted_check_items": [],
            "retrieved_unit_ids": [],
            "retrieval_hit_at_5": False,
            "predicted_status": None,
            "status_correct": False,
            "error": None,
        }
        try:
            created = service.create_session()
            state = service.send_message(
                created.session_id,
                "请进行合规审查：" + case["design_description"],
                f"business-eval-{case['case_id']}-{created.session_id}",
            )
            row["extracted_check_items"] = [
                item.model_dump(mode="json") for item in state.check_items
            ]
            if state.status == WorkflowStatus.WAITING_CONFIRMATION:
                # 只确认模型真实提取结果，不使用金标修改或替换检查项。
                state = service.confirm(
                    created.session_id,
                    WorkflowConfirmRequest(items=state.check_items),
                )
            direct = [item for item in state.evidence if item.context_reason is None]
            units = [item.unit_id for item in direct]
            row.update(
                workflow_status=state.status.value,
                current_node=state.current_node,
                retrieved_unit_ids=units,
                retrieval_hit_at_5=bool(expected_ids.intersection(units)),
                retrieval_attempts=[
                    attempt.model_dump(mode="json") for attempt in state.retrieval_attempts
                ],
                evidence_by_check_item=state.evidence_by_check_item,
                judgements=[
                    judgement.model_dump(mode="json")
                    for judgement in state.compliance_judgements
                ],
                compliance_results=[
                    result.model_dump(mode="json") for result in state.compliance_results
                ],
                errors=state.errors,
                report_markdown=state.report_markdown,
                workflow_trace=[trace.model_dump(mode="json") for trace in state.workflow_trace],
            )
            if state.compliance_results:
                predicted = next(
                    (
                        status
                        for status in (
                            "NON_COMPLIANT",
                            "CONFLICT",
                            "MANUAL_REVIEW_REQUIRED",
                            "INSUFFICIENT_INFORMATION",
                            "COMPLIANT",
                        )
                        if status in {result.status.value for result in state.compliance_results}
                    ),
                    None,
                )
                row["predicted_status"] = predicted
                row["status_correct"] = predicted == row["gold_status"]
            if state.status != WorkflowStatus.COMPLETED:
                row["error"] = f"工作流未完成：{state.status.value}/{state.current_node}"
        except Exception as exc:  # 单条异常不能中断整批业务测试。
            row.setdefault("workflow_status", "ERROR")
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["latency_seconds"] = round(time.perf_counter() - started, 3)
        rows.append(row)
        print(
            f"[{index}/{len(cases)}] {row['case_id']} workflow={row['workflow_status']} "
            f"items={len(row['extracted_check_items'])} hit={row['retrieval_hit_at_5']} "
            f"gold={row['gold_status']} predicted={row['predicted_status']}",
            flush=True,
        )
    completed = [row for row in rows if row.get("workflow_status") == "COMPLETED"]
    false_compliant = [
        row
        for row in rows
        if row["predicted_status"] == "COMPLIANT" and row["gold_status"] != "COMPLIANT"
    ]
    metrics = {
        "total_cases": len(rows),
        "completed_cases": len(completed),
        "retrieval_hit_at_5": sum(row["retrieval_hit_at_5"] for row in rows) / len(rows),
        "status_accuracy": sum(row["status_correct"] for row in rows) / len(rows),
        "false_compliant_count": len(false_compliant),
        "error_count": sum(bool(row["error"]) for row in rows),
        "total_latency_seconds": round(sum(row["latency_seconds"] for row in rows), 3),
    }
    report = {
        "evaluation_version": "rag-compliance-full-workflow-v0.2.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_path": str(dataset_path),
        "chat_model": settings.model.chat_model,
        "embedding_model": settings.model.embedding_model,
        "metrics": metrics,
        "cases": rows,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_markdown(report, output_dir / "report.md")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evaluation/datasets/rag_compliance_business_small.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("evaluation/reports/latest/rag_compliance_business_full_workflow"),
    )
    parser.add_argument("--case-id")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.dataset, args.output_dir, args.case_id)["metrics"],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
