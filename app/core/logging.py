import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

SENSITIVE_MARKERS = ("api_key", "apikey", "authorization", "password", "secret", "token")
RESERVED_LOG_RECORD_KEYS = set(logging.makeLogRecord({}).__dict__)


def _redact(value: Any, key: str = "") -> Any:
    if any(marker in key.lower() for marker in SENSITIVE_MARKERS):
        return "***REDACTED***"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in RESERVED_LOG_RECORD_KEYS and key not in {"message", "asctime"}
        }
        payload.update(_redact(extras))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
