#!/usr/bin/env python3
import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from app.api.qa import get_qa_service
from app.workflow.graph import WorkflowRunner
from app.workflow.models import AgentState
from app.workflow.nodes import WorkflowNodes


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 M11 三类稳定演示案例")
    parser.add_argument("--cases", type=Path, default=Path("evaluation/demo_cases.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/reports/latest/demo_cases.json")
    )
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    qa = get_qa_service()
    runner = WorkflowRunner(WorkflowNodes(qa, qa.retrieval))
    output = []
    for case in cases:
        now = datetime.now(UTC)
        state = runner.run(
            AgentState(
                session_id=f"session_{case['case_id']}",
                request_id=f"request_{case['case_id']}",
                input_text=case["input"],
                created_at=now,
                updated_at=now,
            )
        )
        output.append(
            {
                "case_id": case["case_id"],
                "title": case["title"],
                "status": state.status,
                "expected_terminal": case["expected_terminal"],
                "passed": state.status == case["expected_terminal"],
                "intent": state.intent,
                "check_item_count": len(state.check_items),
                "evidence_count": len(state.evidence),
                "result_statuses": [result.status for result in state.compliance_results],
                "trace_nodes": [event.module for event in state.workflow_trace],
                "limitations": state.errors,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(output, ensure_ascii=False, indent=2)
    args.output.write_text(payload, encoding="utf-8")
    markdown = ["# M11 三类演示案例", ""]
    for case in output:
        markdown.extend(
            [
                f"## {case['title']}",
                "",
                f"- 终态：`{case['status']}`（期望 `{case['expected_terminal']}`）",
                f"- 通过：`{case['passed']}`",
                f"- 意图：`{case['intent']}`",
                f"- 检查项：{case['check_item_count']}，证据：{case['evidence_count']}",
                f"- 工作流：`{' -> '.join(case['trace_nodes'])}`",
                "",
            ]
        )
    args.output.with_suffix(".md").write_text("\n".join(markdown), encoding="utf-8")
    print(payload)
    return 0 if all(case["passed"] for case in output) else 1


if __name__ == "__main__":
    raise SystemExit(main())
