import hashlib
import json
from enum import StrEnum
from typing import Any


class ResourceType(StrEnum):
    DOCUMENT = "doc"
    PAGE = "page"
    CHUNK = "chunk"
    TABLE = "table"
    EVIDENCE = "evidence"
    CHECK_ITEM = "check"
    RULE = "rule"


def stable_id(resource_type: ResourceType, *parts: Any, length: int = 20) -> str:
    """根据规范化输入生成跨进程稳定 ID，避免依赖 Python 的随机 hash。"""
    if not parts:
        raise ValueError("生成稳定 ID 至少需要一个输入字段")
    payload = json.dumps(
        parts, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]
    return f"{resource_type.value}_{digest}"


def document_id(sha256: str) -> str:
    return stable_id(ResourceType.DOCUMENT, sha256.lower())


def page_id(document_id_value: str, page_number: int) -> str:
    return stable_id(ResourceType.PAGE, document_id_value, page_number)


def chunk_id(document_id_value: str, locator: str, content: str) -> str:
    return stable_id(ResourceType.CHUNK, document_id_value, locator, content)


def table_id(document_id_value: str, table_number: str, page_number: int) -> str:
    return stable_id(ResourceType.TABLE, document_id_value, table_number, page_number)


def evidence_id(unit_id: str, query: str) -> str:
    return stable_id(ResourceType.EVIDENCE, unit_id, query)


def check_item_id(source_text: str, span: tuple[int, int]) -> str:
    return stable_id(ResourceType.CHECK_ITEM, source_text, span)
