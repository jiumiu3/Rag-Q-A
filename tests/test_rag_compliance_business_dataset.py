import json
from pathlib import Path

DATASET_PATH = Path("evaluation/datasets/rag_compliance_business_small.jsonl")


def _load_cases() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in DATASET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_business_dataset_has_ten_traceable_unique_cases() -> None:
    cases = _load_cases()
    assert len(cases) == 10
    assert len({case["case_id"] for case in cases}) == 10
    assert {case["dataset_version"] for case in cases} == {
        "rag-compliance-business-v0.1.0"
    }

    for case in cases:
        retrieval = case["gold_retrieval"]
        provenance = case["provenance"]
        assert retrieval["clause_id"]
        assert retrieval["quote"]
        assert retrieval["expected_unit_ids"]
        assert retrieval["reference_evidence_ids"]
        assert provenance["pdf_file"].endswith(".pdf")
        assert provenance["source_image"].endswith(".png")
        assert provenance["extraction_method"] == "project_ocr_with_pdf_page_trace"


def test_business_dataset_covers_judgement_and_rule_diversity() -> None:
    cases = _load_cases()
    statuses = {case["gold_judgement"]["status"] for case in cases}
    standards = {case["gold_retrieval"]["standard_code"] for case in cases}
    comparison_types = {
        case["gold_judgement"]["comparison"]["type"] for case in cases
    }
    all_tags = {tag for case in cases for tag in case["tags"]}

    assert statuses == {
        "COMPLIANT",
        "NON_COMPLIANT",
        "INSUFFICIENT_INFORMATION",
        "MANUAL_REVIEW_REQUIRED",
    }
    assert standards == {
        "Q/GGW 02005.1—2022",
        "Q/GGW 02005.2—2022",
        "Q/GGW 02005.3—2022",
    }
    assert {
        "numeric_threshold",
        "unit_conversion_threshold",
        "boolean_prohibition",
        "external_standard_dependency",
        "numeric_range",
        "compound_threshold_and_action",
    }.issubset(comparison_types)
    assert {"boundary_value", "prohibition", "manual_review", "insufficient_information"}.issubset(
        all_tags
    )


def test_business_dataset_missing_information_matches_safe_degradation() -> None:
    cases = _load_cases()
    for case in cases:
        judgement = case["gold_judgement"]
        if judgement["status"] in {
            "INSUFFICIENT_INFORMATION",
            "MANUAL_REVIEW_REQUIRED",
        }:
            assert judgement["missing_information"]
        else:
            assert judgement["missing_information"] == []
