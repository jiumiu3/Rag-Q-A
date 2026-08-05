import json
from pathlib import Path


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_realistic_retrieval_dataset_is_small_stratified_and_reviewable() -> None:
    rows = _jsonl(Path("evaluation/datasets/retrieval_realistic_small.jsonl"))
    reviews = _jsonl(Path("evaluation/annotations/retrieval_realistic_review.jsonl"))
    assert len(rows) == 16
    assert {row["query_type"] for row in rows} == {"exact", "semantic", "multi_goal"}
    assert len({row["case_id"] for row in rows}) == len(rows)
    assert {row["case_id"] for row in rows} == {row["case_id"] for row in reviews}
    assert all(row["expected_unit_ids"] for row in rows)
    assert all(row["retrieval_goals"] for row in rows)


def test_realistic_retrieval_labels_are_not_falsely_marked_approved() -> None:
    reviews = _jsonl(Path("evaluation/annotations/retrieval_realistic_review.jsonl"))
    assert all(row["status"] == "pending" for row in reviews)
