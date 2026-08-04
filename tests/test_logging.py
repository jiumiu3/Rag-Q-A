import json
import logging

from app.core.logging import JsonFormatter


def test_json_formatter_redacts_nested_secrets() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "完成", (), None)
    record.request_id = "req-1"
    record.payload = {"api_key": "danger", "nested": {"password": "danger", "value": 3}}
    payload = json.loads(JsonFormatter().format(record))
    assert payload["request_id"] == "req-1"
    assert payload["payload"]["api_key"] == "***REDACTED***"
    assert payload["payload"]["nested"]["password"] == "***REDACTED***"
    assert "danger" not in json.dumps(payload)
