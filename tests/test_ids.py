import pytest

from app.core.ids import ResourceType, page_id, stable_id


def test_stable_id_is_deterministic_and_namespaced() -> None:
    assert page_id("doc_123", 1) == page_id("doc_123", 1)
    assert page_id("doc_123", 1).startswith("page_")
    assert page_id("doc_123", 1) != stable_id(ResourceType.CHUNK, "doc_123", 1)


def test_stable_id_requires_input() -> None:
    with pytest.raises(ValueError, match="至少需要一个输入字段"):
        stable_id(ResourceType.DOCUMENT)
