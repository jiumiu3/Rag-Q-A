import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import ModelSettings
from app.core.exceptions import AppError

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class ModelClientError(AppError):
    code = "MODEL_CLIENT_ERROR"


class CompatibleJSONClient:
    """最小 OpenAI-compatible JSON 客户端；只接受经过 Pydantic 校验的输出。"""

    def __init__(
        self, settings: ModelSettings, purpose: Literal["chat", "embedding"] = "chat"
    ) -> None:
        self.settings = settings
        self.purpose = purpose

    @property
    def base_url(self) -> str | None:
        return (
            self.settings.resolved_chat_base_url
            if self.purpose == "chat"
            else self.settings.resolved_embedding_base_url
        )

    @property
    def api_key_env(self) -> str:
        return (
            self.settings.resolved_chat_api_key_env
            if self.purpose == "chat"
            else self.settings.resolved_embedding_api_key_env
        )

    def _api_key(self) -> str:
        endpoint_key = (
            self.settings.chat_api_key
            if self.purpose == "chat"
            else self.settings.embedding_api_key
        )
        key = (
            endpoint_key.get_secret_value()
            if endpoint_key
            else os.getenv(self.api_key_env)
            or (self.settings.api_key.get_secret_value() if self.settings.api_key else None)
        )
        if not key:
            raise ModelClientError(
                f"环境变量 {self.api_key_env} 中没有可用密钥",
                details={"hint": "Docker 使用 env_file；本地运行前需导出 .env"},
            )
        return key

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """调用 OpenAI-compatible chat/completions，供受控工具循环复用。"""
        if not self.base_url or not self.settings.chat_model:
            raise ModelClientError("未配置模型 BASE_URL 或 CHAT_MODEL")
        payload: dict[str, Any] = {
            "model": self.settings.chat_model,
            "messages": messages,
            "temperature": 0,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if response_format:
            payload["response_format"] = response_format
        body = self._post("chat/completions", payload)
        try:
            return dict(body["choices"][0]["message"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelClientError("模型响应缺少 message", details={"error": str(exc)}) from exc

    def complete(self, prompt: str, response_model: type[ResponseModel]) -> ResponseModel:
        if not self.base_url or not self.settings.chat_model:
            raise ModelClientError("未配置模型 BASE_URL 或 CHAT_MODEL")
        schema = response_model.model_json_schema()
        schema_text = json.dumps(schema, ensure_ascii=False)
        try:
            message = self.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "只根据输入生成 JSON，不补充输入中不存在的事实。"
                            "输出必须符合给定 JSON Schema。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"JSON Schema:\n{schema_text}\n\n任务:\n{prompt}",
                    },
                ],
                response_format={"type": "json_object"},
            )
            return response_model.model_validate_json(message["content"])
        except (KeyError, json.JSONDecodeError, ValidationError) as exc:
            raise ModelClientError("模型输出未通过结构化校验", details={"error": str(exc)}) from exc

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise ModelClientError("未配置模型 BASE_URL")
        url = f"{self.base_url.rstrip('/')}/{endpoint}"
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }
        retries = self.settings.request_retries
        for attempt in range(retries):
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                    return dict(json.loads(response.read()))
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                if retryable and attempt + 1 < retries:
                    exc.close()
                    time.sleep(attempt + 1)
                    continue
                details: dict[str, object] = {"http_status": exc.code}
                try:
                    error_body = json.loads(exc.read())
                    error = error_body.get("error", error_body)
                    if isinstance(error, dict):
                        details["error_type"] = str(error.get("type") or error.get("code") or "")
                        details["error_message"] = str(error.get("message") or "")[:500]
                except (json.JSONDecodeError, OSError, AttributeError):
                    pass
                # 禁止底层 urllib Request（含 Authorization 请求头）进入上层 traceback。
                raise ModelClientError("模型 API 返回错误", details=details) from None
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt + 1 < retries:
                    time.sleep(attempt + 1)
                    continue
                raise ModelClientError(
                    "模型 API 连接失败", details={"error": str(exc), "attempts": retries}
                ) from None
            except (json.JSONDecodeError, TypeError) as exc:
                raise ModelClientError(
                    "模型 API 返回非 JSON 内容", details={"error": str(exc)}
                ) from None
        raise ModelClientError("模型 API 请求重试耗尽")


class CompatibleEmbeddingClient:
    """OpenAI-compatible Embeddings 客户端，用于建库和查询使用同一模型。"""

    def __init__(self, settings: ModelSettings, show_progress: bool = False) -> None:
        if not settings.embedding_model:
            raise ModelClientError("未配置 EMBEDDING_MODEL")
        self.settings = settings
        self.client = CompatibleJSONClient(settings, purpose="embedding")
        self.show_progress = show_progress

    @property
    def model_id(self) -> str:
        return self.settings.embedding_model or "NOT_CONFIGURED"

    @property
    def configuration_id(self) -> str:
        endpoint = self.settings.resolved_embedding_base_url or "NOT_CONFIGURED"
        endpoint_hash = hashlib.sha256(endpoint.encode()).hexdigest()
        return (
            f"{self.model_id}:batch={self.settings.embedding_batch_size}:"
            f"max_chars={self.settings.embedding_max_chars}:endpoint={endpoint_hash}"
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # 控制单次请求体大小，避免 1092 个知识单元一次提交超出服务限制。
        vectors: list[list[float]] = []
        size = self.settings.embedding_batch_size
        total_batches = math.ceil(len(texts) / size)
        started = time.perf_counter()
        for start in range(0, len(texts), size):
            # 只截断向量编码输入，SQLite 原文和最终引用仍保持完整。
            batch = [
                text[: self.settings.embedding_max_chars] for text in texts[start : start + size]
            ]
            body = self.client._post(
                "embeddings", {"model": self.model_id, "input": batch, "encoding_format": "float"}
            )
            try:
                rows = sorted(body["data"], key=lambda item: item["index"])
                for item in rows:
                    vector = list(map(float, item["embedding"]))
                    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
                    vectors.append([value / norm for value in vector])
            except (KeyError, TypeError, ValueError) as exc:
                raise ModelClientError(
                    "Embedding API 输出格式无效", details={"error": str(exc)}
                ) from exc
            if self.show_progress:
                completed = start // size + 1
                elapsed = time.perf_counter() - started
                eta = elapsed / completed * (total_batches - completed)
                print(
                    f"\rEmbedding [{completed}/{total_batches}] "
                    f"elapsed={elapsed:.1f}s eta={eta:.1f}s",
                    end="" if completed < total_batches else "\n",
                    file=sys.stderr,
                    flush=True,
                )
        if len(vectors) != len(texts) or len({len(vector) for vector in vectors}) != 1:
            raise ModelClientError("Embedding API 返回数量或维度不一致")
        return vectors
