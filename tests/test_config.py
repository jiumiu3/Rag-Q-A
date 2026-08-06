from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_nested_environment_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_OCR__DPI", "240")
    monkeypatch.setenv("APP_STORAGE__INDEX_DIR", "tmp/index")
    monkeypatch.setenv("APP_AGENT__RAG_LLM_ENABLED", "true")
    monkeypatch.setenv("APP_RETRIEVAL__CONTEXTUAL_VERSION", "v2")
    settings = Settings(_env_file=None)
    assert settings.ocr.dpi == 240
    assert settings.storage.index_dir == Path("tmp/index")
    assert settings.agent.rag_llm_enabled is True
    assert settings.retrieval.contextual_enabled is True
    assert settings.retrieval.contextual_strategy == "deterministic"
    assert settings.retrieval.contextual_version == "v2"


def test_invalid_config_has_clear_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_OCR__DPI", "10")
    with pytest.raises(ValidationError, match="greater than or equal to 72"):
        Settings(_env_file=None)


def test_plain_llm_api_key_is_bound_as_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    settings = Settings(_env_file=None)
    assert settings.model.api_key is not None
    assert settings.model.api_key.get_secret_value() == "test-secret"
    assert settings.model.embedding_api_key is not None
    assert settings.model.embedding_api_key.get_secret_value() == "test-secret"
    assert settings.model.chat_api_key is not None
    assert settings.model.chat_api_key.get_secret_value() == "deepseek-secret"
    assert "test-secret" not in repr(settings)
    assert "deepseek-secret" not in repr(settings)
