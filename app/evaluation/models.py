from datetime import datetime
from typing import Literal

from pydantic import Field

from app.domain.models import StrictModel


class Metric(StrictModel):
    name: str
    value: float | None = None
    numerator: int | float | None = None
    denominator: int = Field(ge=0)
    status: Literal["MEASURED", "NOT_EVALUATED"]
    scope: str


class EvaluationFailure(StrictModel):
    suite: str
    case_id: str
    category: str
    expected: str
    actual: str
    detail: str


class SuiteResult(StrictModel):
    name: str
    dataset_path: str
    dataset_hash: str
    case_count: int
    metrics: list[Metric]
    failures: list[EvaluationFailure] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class VersionBinding(StrictModel):
    generated_at: datetime
    random_seed: int
    code_commit: str
    code_hash: str
    index_version: str
    index_source_hash: str
    index_config_hash: str
    embedding_model_id: str
    chat_model_id: str
    model_config_hash: str
    prompt_and_evaluator_hash: str
    dataset_version: str
    dataset_hashes: dict[str, str]
    evaluator_version: str


class EvaluationReport(StrictModel):
    versions: VersionBinding
    suites: list[SuiteResult]
    error_summary: dict[str, int]
    known_data_quality: dict[str, int | str]
    claims_boundary: list[str]
