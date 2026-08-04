import json
import urllib.error
import urllib.request
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from app.core.config import ModelSettings
from app.core.model_client import CompatibleEmbeddingClient, CompatibleJSONClient, ModelClientError


class DemoResponse(BaseModel):
    value: int


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class FakeHTTPErrorResponse(urllib.error.HTTPError):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("https://example.invalid", 400, "bad request", {}, None)
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_compatible_client_validates_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_open(_request: urllib.request.Request, timeout: float) -> FakeHTTPResponse:
        assert timeout == 5
        return FakeHTTPResponse({"choices": [{"message": {"content": '{"value": 3}'}}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    client = CompatibleJSONClient(
        ModelSettings(
            base_url="https://example.invalid/v1",
            chat_model="test",
            api_key=SecretStr("secret"),
            timeout=5,
        )
    )
    assert client.complete("demo", DemoResponse).value == 3


def test_compatible_client_rejects_invalid_model_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeHTTPResponse(
            {"choices": [{"message": {"content": '{"value": "wrong"}'}}]}
        ),
    )
    client = CompatibleJSONClient(
        ModelSettings(
            base_url="https://example.invalid/v1",
            chat_model="test",
            api_key=SecretStr("secret"),
        )
    )
    with pytest.raises(ModelClientError, match="结构化校验"):
        client.complete("demo", DemoResponse)


def test_embedding_client_batches_and_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_open(request: urllib.request.Request, timeout: float) -> FakeHTTPResponse:
        assert request.full_url.endswith("/embeddings")
        assert timeout == 5
        return FakeHTTPResponse({"data": [{"index": 0, "embedding": [3.0, 4.0]}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    client = CompatibleEmbeddingClient(
        ModelSettings(
            base_url="https://example.invalid/v1",
            embedding_model="embedding-test",
            api_key=SecretStr("secret"),
            timeout=5,
        )
    )
    assert client.embed(["测试"])[0] == pytest.approx([0.6, 0.8])


def test_embedding_client_truncates_only_api_input(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    def fake_open(request: urllib.request.Request, **_kwargs: Any) -> FakeHTTPResponse:
        payload = json.loads(request.data or b"{}")
        sent.extend(payload["input"])
        return FakeHTTPResponse({"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    client = CompatibleEmbeddingClient(
        ModelSettings(
            base_url="https://example.invalid/v1",
            embedding_model="embedding-test",
            embedding_max_chars=256,
            api_key=SecretStr("secret"),
        )
    )
    client.embed(["长" * 300])
    assert len(sent[0]) == 256


def test_http_error_keeps_safe_provider_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FakeHTTPErrorResponse(
                {"error": {"type": "invalid_request", "message": "tool_choice 不受支持"}}
            )
        ),
    )
    client = CompatibleJSONClient(
        ModelSettings(
            base_url="https://example.invalid/v1",
            chat_model="test",
            api_key=SecretStr("secret"),
        )
    )
    with pytest.raises(ModelClientError) as captured:
        client.chat([{"role": "user", "content": "测试"}])
    assert captured.value.details["error_type"] == "invalid_request"
    assert captured.value.details["error_message"] == "tool_choice 不受支持"


def test_client_retries_transient_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def flaky_open(*_args: Any, **_kwargs: Any) -> FakeHTTPResponse:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise urllib.error.URLError("temporary")
        return FakeHTTPResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(urllib.request, "urlopen", flaky_open)
    monkeypatch.setattr("app.core.model_client.time.sleep", lambda _seconds: None)
    client = CompatibleJSONClient(
        ModelSettings(
            base_url="https://example.invalid/v1",
            chat_model="test",
            api_key=SecretStr("secret"),
            request_retries=3,
        )
    )
    assert client.chat([{"role": "user", "content": "测试"}])["content"] == "ok"
    assert attempts == 3
