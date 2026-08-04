import json
from pathlib import Path

from app.design.parser import DesignDescriptionParser
from app.evaluation.models import EvaluationReport
from app.evaluation.runner import evaluate_compliance, render_markdown, version_binding
from app.retrieval.service import QueryAnalyzer


def _jsonl_count(path: Path) -> int:
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line])


def test_m11_dataset_sizes_and_demo_types() -> None:
    assert _jsonl_count(Path("evaluation/retrieval_gold.jsonl")) == 50
    assert _jsonl_count(Path("evaluation/qa_gold.jsonl")) == 30
    assert _jsonl_count(Path("evaluation/design_gold.jsonl")) == 30
    assert _jsonl_count(Path("evaluation/design_detail_gold.jsonl")) == 12
    assert _jsonl_count(Path("evaluation/compliance_gold.jsonl")) == 20
    demos = json.loads(Path("evaluation/demo_cases.json").read_text(encoding="utf-8"))
    assert len(demos) == 3
    assert {case["type"] for case in demos} == {"query", "checklist", "full_review"}


def test_root_clause_is_exact_lookup() -> None:
    analysis = QueryAnalyzer().analyze("Q/GGW 02005.1-2022 第1条是什么？")
    assert analysis.intent == "CLAUSE_LOOKUP"
    assert analysis.clause_numbers == ["1"]


def test_design_enum_and_relation_values_are_not_truncated() -> None:
    parser = DesignDescriptionParser()
    result = parser.parse("仪表电缆与动力电缆间距为300mm，设备防护等级IP65，防爆等级Ex d IIB T4。")
    by_attribute = {item.attribute: item for item in result.items}
    assert by_attribute["间距"].object == "仪表电缆与动力电缆"
    assert by_attribute["防护等级"].value == "IP65"
    assert by_attribute["防爆等级"].value == "Ex d IIB T4"


def test_compliance_fixture_is_deterministic_and_has_explicit_na_metrics() -> None:
    result = evaluate_compliance(Path("evaluation/compliance_gold.jsonl"))
    status = next(
        metric for metric in result.metrics if metric.name == "deterministic_status_accuracy"
    )
    assert status.value == 1.0
    not_evaluated = [metric for metric in result.metrics if metric.status == "NOT_EVALUATED"]
    assert not_evaluated
    assert all(metric.value is None and metric.denominator == 0 for metric in not_evaluated)


def test_report_binds_versions_without_api_key() -> None:
    paths = [
        Path("evaluation/retrieval_gold.jsonl"),
        Path("evaluation/qa_gold.jsonl"),
        Path("evaluation/design_gold.jsonl"),
        Path("evaluation/design_detail_gold.jsonl"),
        Path("evaluation/compliance_gold.jsonl"),
    ]
    report = EvaluationReport(
        versions=version_binding(paths),
        suites=[],
        error_summary={},
        known_data_quality={},
        claims_boundary=["没有优化前同口径基线"],
    )
    payload = report.model_dump_json()
    assert "api_key" not in payload.casefold()
    assert report.versions.dataset_version == "m11-datasets-v1"
    assert len(report.versions.dataset_hashes) == 6
    markdown = render_markdown(report)
    assert "没有优化前同口径基线" in markdown
