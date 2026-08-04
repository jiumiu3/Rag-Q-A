import hashlib
import html
import json
import random
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from app.api.qa import get_qa_service
from app.compliance.evaluator import RuleEvaluator
from app.compliance.models import ExecutableRule
from app.core.config import get_settings
from app.design.parser import DesignDescriptionParser
from app.domain.models import CheckItem
from app.evaluation.models import (
    EvaluationFailure,
    EvaluationReport,
    Metric,
    SuiteResult,
    VersionBinding,
)

SEED = 42
EVALUATOR_VERSION = "m11-v1"
ERROR_TAXONOMY = (
    "ocr",
    "structure",
    "retrieval",
    "rule_extraction",
    "unit",
    "table",
    "citation",
    "routing",
    "item_extraction",
    "binding",
    "parameter",
    "qa_status",
    "numeric",
    "agent_tool_call",
    "agent_generation",
)
RULE_ADAPTER: TypeAdapter[ExecutableRule] = TypeAdapter(ExecutableRule)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rate(name: str, numerator: int | float, denominator: int, scope: str) -> Metric:
    return Metric(
        name=name,
        value=float(numerator / denominator) if denominator else None,
        numerator=numerator,
        denominator=denominator,
        status="MEASURED" if denominator else "NOT_EVALUATED",
        scope=scope,
    )


def evaluate_retrieval(path: Path) -> SuiteResult:
    service = get_qa_service().retrieval
    rows = _jsonl(path)
    top1 = top5 = 0
    rr = 0.0
    failures: list[EvaluationFailure] = []
    for row in rows:
        result = service.retrieve(row["question"], 5)
        ids = [item.unit_id for item in result.evidence if item.context_reason is None]
        expected = set(row["expected_unit_ids"])
        top1 += int(bool(expected.intersection(ids[:1])))
        top5 += int(bool(expected.intersection(ids[:5])))
        rank = next((index for index, key in enumerate(ids, 1) if key in expected), None)
        rr += 1 / rank if rank else 0
        if not rank:
            failures.append(
                EvaluationFailure(
                    suite="retrieval",
                    case_id=row["case_id"],
                    category="retrieval",
                    expected=str(sorted(expected)),
                    actual=str(ids[:5]),
                    detail="正确条款未进入 Top-5",
                )
            )
    count = len(rows)
    return SuiteResult(
        name="retrieval",
        dataset_path=str(path),
        dataset_hash=_sha(path),
        case_count=count,
        metrics=[
            _rate("recall_at_1", top1, count, "50条真实索引精确条款号基准"),
            _rate("recall_at_5", top5, count, "50条真实索引精确条款号基准"),
            _rate("mrr", rr, count, "50条真实索引精确条款号基准"),
        ],
        failures=failures,
        limitations=["标签由 SQLite 精确条款键分层派生，不代表同义改写或开放语义检索性能。"],
    )


def evaluate_qa(path: Path) -> SuiteResult:
    service = get_qa_service()
    rows = _jsonl(path)
    route = status = citations = numeric = refusal = refusal_total = evidence_supported = 0
    tool_calls = generation_success = 0
    failures: list[EvaluationFailure] = []
    for row in rows:
        answer, result = service.ask(row["question"])
        route_ok = answer.intent == row["expected_intent"]
        status_ok = answer.status == row["expected_status"]
        expected_ids = set(row.get("expected_unit_ids", []))
        actual_ids = {item.unit_id for item in result.evidence if item.context_reason is None}
        evidence_ok = not expected_ids or bool(expected_ids.intersection(actual_ids))
        citation_ok = (
            answer.status != "SUFFICIENT"
            or bool(answer.citations)
            and all(citation.quote for citation in answer.citations)
        )
        numbers_ok = not service.validator.validate(answer, result.evidence)
        route += int(route_ok)
        status += int(status_ok)
        citations += int(citation_ok)
        numeric += int(numbers_ok)
        evidence_supported += int(status_ok and evidence_ok and citation_ok)
        tool_calls += int(bool(answer.tool_calls))
        generation_ok = not any("已降级为抽取式回答" in item for item in answer.limitations)
        generation_success += int(generation_ok)
        if row["expected_status"] in {"NOT_FOUND", "PARTIAL", "EXTERNAL_STANDARD_REQUIRED"}:
            refusal_total += 1
            refusal += int(status_ok and not answer.claims)
        checks = {
            "routing": route_ok,
            "qa_status": status_ok,
            "citation": citation_ok,
            "numeric": numbers_ok,
            "retrieval": evidence_ok,
            "agent_tool_call": bool(answer.tool_calls),
            "agent_generation": generation_ok,
        }
        for category, passed in checks.items():
            if not passed:
                failures.append(
                    EvaluationFailure(
                        suite="qa",
                        case_id=row["case_id"],
                        category=category,
                        expected=f"{row['expected_intent']}/{row['expected_status']}",
                        actual=f"{answer.intent}/{answer.status}",
                        detail=(
                            "；".join(answer.limitations)
                            if category in {"agent_tool_call", "agent_generation"}
                            and answer.limitations
                            else f"{category} 校验失败"
                        ),
                    )
                )
    count = len(rows)
    metrics = [
        _rate("route_accuracy", route, count, "30条条款/表格查询"),
        _rate("evidence_status_accuracy", status, count, "30条可程序验证状态"),
        _rate("citation_validity", citations, count, "引用存在且来自 Evidence"),
        _rate("numeric_consistency", numeric, count, "回答数值不得脱离绑定 Evidence"),
        _rate("refusal_accuracy", refusal, refusal_total, "10条错误条款/复杂表格/错误表号"),
        _rate(
            "evidence_supported_answer_accuracy",
            evidence_supported,
            count,
            "状态、期望证据与引用联合校验",
        ),
        _rate(
            "agent_tool_call_rate",
            tool_calls,
            count,
            "回答对象包含经代码执行的 knowledge_search 调用审计",
        ),
        _rate(
            "agent_generation_success_rate",
            generation_success,
            count,
            "模型基于工具证据生成结构化回答且未降级为抽取式回答",
        ),
        Metric(
            name="free_text_semantic_correctness",
            denominator=0,
            status="NOT_EVALUATED",
            scope="没有领域专家逐条语义评分，不报告自由文本正确率",
        ),
    ]
    return SuiteResult(
        name="qa",
        dataset_path=str(path),
        dataset_hash=_sha(path),
        case_count=count,
        metrics=metrics,
        failures=failures,
        limitations=["20条正例由精确条款键派生；自由文本语义正确性尚需领域专家评分。"],
    )


def _multiset_recall(expected: list[str], actual: list[str]) -> tuple[int, int]:
    expected_counts = Counter(expected)
    actual_counts = Counter(actual)
    return sum(min(count, actual_counts[key]) for key, count in expected_counts.items()), len(
        expected
    )


def evaluate_design(path: Path, detail_path: Path) -> SuiteResult:
    parser = DesignDescriptionParser()
    rows = _jsonl(path)
    detail_rows = _jsonl(detail_path)
    attr_hits = attr_total = exact = 0
    object_hits = parameter_hits = unit_hits = detail_total = 0
    failures: list[EvaluationFailure] = []
    for index, row in enumerate(rows, 1):
        result = parser.parse(row["description"])
        actual = [item.attribute for item in result.items]
        hits, total = _multiset_recall(row["expected_attributes"], actual)
        attr_hits += hits
        attr_total += total
        exact += int(actual == row["expected_attributes"])
        if hits != total:
            failures.append(
                EvaluationFailure(
                    suite="design",
                    case_id=f"design-{index:03d}",
                    category="item_extraction",
                    expected=str(row["expected_attributes"]),
                    actual=str(actual),
                    detail="原子属性漏抽或错抽",
                )
            )
    for row in detail_rows:
        actual_items = parser.parse(row["description"]).items
        detail_total += len(row["expected_items"])
        for item_index, expected in enumerate(row["expected_items"]):
            actual_item = actual_items[item_index] if item_index < len(actual_items) else None
            if actual_item is None:
                for category in ("binding", "parameter", "unit"):
                    failures.append(
                        EvaluationFailure(
                            suite="design",
                            case_id=row["case_id"],
                            category=category,
                            expected=str(
                                expected.get(category if category != "binding" else "object")
                            ),
                            actual="MISSING_ITEM",
                            detail=f"{expected['attribute']} 未生成，无法校验 {category}",
                        )
                    )
                continue
            object_ok = actual_item.object == expected["object"]
            value_ok = str(actual_item.value) == str(expected["value"])
            unit_ok = actual_item.unit == expected["unit"]
            object_hits += int(object_ok)
            parameter_hits += int(value_ok)
            unit_hits += int(unit_ok)
            for category, passed, expected_value, actual_value in (
                ("binding", object_ok, expected["object"], actual_item.object),
                ("parameter", value_ok, expected["value"], actual_item.value),
                ("unit", unit_ok, expected["unit"], actual_item.unit),
            ):
                if not passed:
                    failures.append(
                        EvaluationFailure(
                            suite="design",
                            case_id=row["case_id"],
                            category=category,
                            expected=str(expected_value),
                            actual=str(actual_value),
                            detail=f"{expected['attribute']} 的 {category} 不一致",
                        )
                    )
    metrics = [
        _rate("item_recall", attr_hits, attr_total, "30段、每段2-3项的属性多重集召回"),
        _rate("item_sequence_exact_match", exact, len(rows), "30段属性顺序完全匹配"),
        _rate("object_binding_accuracy", object_hits, detail_total, "12段24项细粒度人工夹具"),
        _rate("parameter_accuracy", parameter_hits, detail_total, "12段24项细粒度人工夹具"),
        _rate("unit_accuracy", unit_hits, detail_total, "12段24项细粒度人工夹具"),
    ]
    return SuiteResult(
        name="design",
        dataset_path=f"{path};{detail_path}",
        dataset_hash=hashlib.sha256((_sha(path) + _sha(detail_path)).encode()).hexdigest(),
        case_count=len(rows),
        metrics=metrics,
        failures=failures,
        limitations=["细粒度夹具由项目实现阶段人工编写，标注人字段仍待领域负责人签字确认。"],
    )


def evaluate_compliance(path: Path) -> SuiteResult:
    rows = _jsonl(path)
    evaluator = RuleEvaluator()
    status_hits = advisory_hits = trace_hits = missing_hits = missing_total = 0
    advisory_total = 0
    failures: list[EvaluationFailure] = []
    for row in rows:
        item = CheckItem.model_validate(row["item"])
        rule = RULE_ADAPTER.validate_python(row["rule"])
        result = evaluator.evaluate(item, rule)
        status_ok = result.status == row["expected_status"]
        status_hits += int(status_ok)
        trace_ok = bool(result.comparison_trace) or result.status in {
            "INSUFFICIENT_INFORMATION",
            "MANUAL_REVIEW_REQUIRED",
            "NOT_SPECIFIED",
        }
        trace_hits += int(trace_ok)
        if "expected_advisory" in row:
            advisory_total += 1
            advisory_hits += int(result.advisory == row["expected_advisory"])
        if row["expected_status"] == "INSUFFICIENT_INFORMATION":
            missing_total += 1
            missing_hits += int(result.status == "INSUFFICIENT_INFORMATION")
        if not status_ok:
            failures.append(
                EvaluationFailure(
                    suite="compliance",
                    case_id=row["case_id"],
                    category=row["error_category"],
                    expected=row["expected_status"],
                    actual=str(result.status),
                    detail="确定性状态不一致",
                )
            )
    count = len(rows)
    return SuiteResult(
        name="compliance",
        dataset_path=str(path),
        dataset_hash=_sha(path),
        case_count=count,
        metrics=[
            _rate(
                "deterministic_status_accuracy",
                status_hits,
                count,
                "20条人工规则夹具的确定性执行结果",
            ),
            _rate(
                "trace_or_limitation_coverage",
                trace_hits,
                count,
                "每项必须有比较 trace 或安全降级限制",
            ),
            _rate(
                "advisory_strength_accuracy",
                advisory_hits,
                advisory_total,
                "带期望 advisory 标签的规则",
            ),
            _rate(
                "missing_condition_safety_accuracy",
                missing_hits,
                missing_total,
                "缺少必要输入时必须安全降级，不得猜测结论",
            ),
            Metric(
                name="llm_rule_extraction_accuracy",
                denominator=0,
                status="NOT_EVALUATED",
                scope="未建立真实条款到规则的领域专家黄金集",
            ),
            Metric(
                name="production_end_to_end_compliance_accuracy",
                denominator=0,
                status="NOT_EVALUATED",
                scope="尚无人工确认生产规则库，不能报告端到端准确率",
            ),
        ],
        failures=failures,
        limitations=["规则均为人工夹具，只测执行器；不代表真实条款规则抽取或工程审查准确率。"],
    )


def version_binding(dataset_paths: list[Path]) -> VersionBinding:
    settings = get_settings()
    manifest_path = settings.storage.index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    code_files = sorted(Path("app").rglob("*.py")) + sorted(Path("scripts").glob("*.py"))
    code_hash = hashlib.sha256("".join(_sha(path) for path in code_files).encode()).hexdigest()
    prompt_files = [
        Path("app/core/model_client.py"),
        Path("app/compliance/extractor.py"),
        Path("app/evaluation/runner.py"),
    ]
    model_config = {
        "provider": settings.model.provider,
        "base_url": settings.model.base_url,
        "chat_model": settings.model.chat_model,
        "embedding_model": manifest["embedding_model_id"],
        "embedding_batch_size": settings.model.embedding_batch_size,
        "embedding_max_chars": settings.model.embedding_max_chars,
        "timeout": settings.model.timeout,
        "request_retries": settings.model.request_retries,
        "rag_llm_enabled": settings.agent.rag_llm_enabled,
    }
    dataset_manifest = json.loads(
        dataset_paths[-1].with_name("MANIFEST.json").read_text(encoding="utf-8")
    )
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = "UNAVAILABLE_NOT_A_GIT_WORKTREE"
    return VersionBinding(
        generated_at=datetime.now(UTC),
        random_seed=SEED,
        code_commit=commit,
        code_hash=code_hash,
        index_version=manifest["version_id"],
        index_source_hash=manifest["source_hash"],
        index_config_hash=_sha(manifest_path),
        embedding_model_id=manifest["embedding_model_id"],
        chat_model_id=settings.model.chat_model or "NOT_CONFIGURED",
        model_config_hash=hashlib.sha256(
            json.dumps(model_config, sort_keys=True).encode()
        ).hexdigest(),
        prompt_and_evaluator_hash=hashlib.sha256(
            "".join(_sha(path) for path in prompt_files).encode()
        ).hexdigest(),
        dataset_version=dataset_manifest["dataset_version"],
        dataset_hashes={
            **{str(path): _sha(path) for path in dataset_paths},
            str(dataset_paths[-1].with_name("MANIFEST.json")): _sha(
                dataset_paths[-1].with_name("MANIFEST.json")
            ),
        },
        evaluator_version=EVALUATOR_VERSION,
    )


def run_all(root: Path = Path("evaluation")) -> EvaluationReport:
    random.seed(SEED)
    paths = [
        root / "retrieval_gold.jsonl",
        root / "qa_gold.jsonl",
        root / "design_gold.jsonl",
        root / "design_detail_gold.jsonl",
        root / "compliance_gold.jsonl",
    ]
    suites = [
        evaluate_retrieval(paths[0]),
        evaluate_qa(paths[1]),
        evaluate_design(paths[2], paths[3]),
        evaluate_compliance(paths[4]),
    ]
    errors = Counter({category: 0 for category in ERROR_TAXONOMY})
    errors.update(failure.category for suite in suites for failure in suite.failures)
    return EvaluationReport(
        versions=version_binding(paths),
        suites=suites,
        error_summary=dict(sorted(errors.items())),
        known_data_quality={
            "ocr_suspicious_pages": 8,
            "manual_review_tables": 27,
            "production_rules_confirmed": 0,
            "note": "来自 M1/M2 真实质量报告与当前规则库状态",
        },
        claims_boundary=[
            "检索指标仅适用于精确条款号基准，不能外推为开放语义检索准确率。",
            "问答未进行领域专家自由文本语义评分。",
            "拆解细粒度夹具尚待领域负责人签字确认。",
            "合规仅测人工规则夹具执行器，未测真实规则抽取和生产端到端准确率。",
            "没有优化前同口径基线，因此不报告性能提升百分比。",
        ],
    )


def render_markdown(report: EvaluationReport) -> str:
    lines = [
        "# M11 可复现评测报告",
        "",
        f"生成时间：{report.versions.generated_at.isoformat()}",
        f"代码哈希：`{report.versions.code_hash}`",
        f"索引版本：`{report.versions.index_version}`",
        f"索引源哈希：`{report.versions.index_source_hash}`",
        f"数据集版本：`{report.versions.dataset_version}`",
        f"模型配置哈希：`{report.versions.model_config_hash}`（不包含 API Key）",
        f"提示词与评测器哈希：`{report.versions.prompt_and_evaluator_hash}`",
        "",
        "## 指标",
    ]
    for suite in report.suites:
        lines.extend(
            [
                "",
                f"### {suite.name}",
                f"数据量：{suite.case_count}",
                "",
                "| 指标 | 值 | 分母 | 状态 | 适用范围 |",
                "|---|---:|---:|---|---|",
            ]
        )
        for metric in suite.metrics:
            value = f"{metric.value:.4f}" if metric.value is not None else "N/A"
            lines.append(
                f"| {metric.name} | {value} | {metric.denominator} | "
                f"{metric.status} | {metric.scope} |"
            )
        lines.extend(["", *[f"- 限制：{item}" for item in suite.limitations]])
    lines.extend(["", "## 误差分类", ""])
    if report.error_summary:
        lines.extend(f"- {key}: {value}" for key, value in report.error_summary.items())
    else:
        lines.append("- 当前评测夹具无失败；这不表示未覆盖场景不存在误差。")
    failures = [failure for suite in report.suites for failure in suite.failures]
    lines.extend(
        [
            "",
            "### 逐案例误差",
            "",
            "| 套件 | 案例 | 分类 | 期望 | 实际 | 说明 |",
            "|---|---|---|---|---|---|",
        ]
    )
    if failures:
        for failure in failures:
            lines.append(
                f"| {failure.suite} | {failure.case_id} | {failure.category} | "
                f"{failure.expected} | {failure.actual} | {failure.detail} |"
            )
    else:
        lines.append("| - | - | - | - | - | 当前固定夹具无失败 |")
    lines.extend(["", "## 已知数据质量", ""])
    lines.extend(f"- {key}: {value}" for key, value in report.known_data_quality.items())
    lines.extend(["", "## 声明边界", ""] + [f"- {item}" for item in report.claims_boundary])
    return "\n".join(lines) + "\n"


def write_report(report: EvaluationReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump_json(indent=2)
    markdown = render_markdown(report)
    (output_dir / "metrics.json").write_text(payload, encoding="utf-8")
    (output_dir / "report.md").write_text(markdown, encoding="utf-8")
    (output_dir / "report.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>M11评测</title>"
        "<style>body{font:14px sans-serif;max-width:1100px;margin:30px auto}"
        "pre{white-space:pre-wrap}</style>"
        f"<pre>{html.escape(markdown)}</pre>",
        encoding="utf-8",
    )
