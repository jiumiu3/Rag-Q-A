from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelSettings(BaseModel):
    provider: str = "compatible"
    base_url: str | None = None
    api_key_env: str = "LLM_API_KEY"
    api_key: SecretStr | None = None
    chat_model: str | None = None
    embedding_model: str | None = None
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    embedding_max_chars: int = Field(default=6000, ge=256, le=50000)
    timeout: float = Field(default=60.0, gt=0)
    request_retries: int = Field(default=3, ge=1, le=5)


class OCRSettings(BaseModel):
    engine: str = "rapidocr"
    language: str = "ch"
    dpi: int = Field(default=300, ge=72, le=600)
    confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    table_enabled: bool = True
    min_text_chars: int = Field(default=10, ge=0)
    image_format: Literal["png", "jpg"] = "png"


class ChunkSettings(BaseModel):
    max_clause_chars: int = Field(default=2000, ge=100)
    parent_context_depth: int = Field(default=2, ge=0, le=10)
    table_row_indexing: bool = True


class RetrievalSettings(BaseModel):
    bm25_top_k: int = Field(default=20, ge=1)
    vector_top_k: int = Field(default=20, ge=1)
    rerank_top_k: int = Field(default=5, ge=1)
    score_threshold: float = Field(default=0.2, ge=0, le=1)


class AgentSettings(BaseModel):
    rag_llm_enabled: bool = False
    max_clarifications: int = Field(default=2, ge=0, le=10)
    max_retrieval_retries: int = Field(default=2, ge=0, le=10)
    max_check_items: int = Field(default=20, ge=1, le=100)


class StorageSettings(BaseModel):
    sqlite_path: Path = Path("data/knowledge.db")
    session_sqlite_path: Path = Path("data/sessions.db")
    index_dir: Path = Path("data/indexes")
    raw_data_dir: Path = Path("data/raw")
    pages_dir: Path = Path("data/pages")
    ocr_dir: Path = Path("data/ocr")
    processed_dir: Path = Path("data/processed")


class Settings(BaseSettings):
    """应用配置；业务模块应通过依赖注入接收实例，不直接读取环境变量。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="APP_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "油气管网规范审查 Agent"
    env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_API_KEY", "APP_LLM_API_KEY"),
        exclude=True,
    )
    model: ModelSettings = Field(default_factory=ModelSettings)
    ocr: OCRSettings = Field(default_factory=OCRSettings)
    chunk: ChunkSettings = Field(default_factory=ChunkSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)

    @model_validator(mode="after")
    def bind_model_api_key(self) -> "Settings":
        """兼容 .env 的 LLM_API_KEY，同时只在内存中以 SecretStr 保存。"""
        if self.llm_api_key and not self.model.api_key:
            self.model = self.model.model_copy(update={"api_key": self.llm_api_key})
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
