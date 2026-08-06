from datetime import datetime

from pydantic import Field

from app.domain.models import KnowledgeUnit, StrictModel


class StoredUnit(StrictModel):
    unit: KnowledgeUnit
    document_id: str
    standard_code: str
    file_name: str
    clause_number: str | None = None
    table_number: str | None = None
    parse_status: str | None = None
    context_prefix: str = ""
    retrieval_text: str = ""


class SearchHit(StrictModel):
    knowledge_unit_id: str
    score: float
    source: str


class IndexVersion(StrictModel):
    version_id: str
    source_hash: str
    config_hash: str
    embedding_model_id: str
    embedding_configuration_hash: str
    embedding_dimensions: int = Field(gt=0)
    build_time: datetime
    unit_count: int = Field(ge=0)
    bm25_count: int = Field(ge=0)
    vector_count: int = Field(ge=0)
    contextual_enabled: bool = True
    contextual_strategy: str = "deterministic"
    contextual_strategy_version: str = "v1"
    contextual_config_hash: str = ""


class BuildReport(StrictModel):
    version: IndexVersion
    document_count: int
    failures: list[str] = Field(default_factory=list)
    duration_ms: float = Field(ge=0)
