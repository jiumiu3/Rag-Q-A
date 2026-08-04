import pytest
from pydantic import ValidationError

from app.domain.models import BoundingBox, DocumentMeta, DocumentStatus, SourceSpan


def test_document_model_round_trip() -> None:
    document = DocumentMeta(
        document_id="doc_1",
        file_name="sample.pdf",
        standard_code="Q/GGW 02005.1—2022",
        part="1",
        year=2022,
        sha256="a" * 64,
        page_count=10,
        status=DocumentStatus.READY,
    )
    assert DocumentMeta.model_validate_json(document.model_dump_json()) == document


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SourceSpan(document_id="doc_1", page_number=1, unknown=True)  # type: ignore[call-arg]


def test_bbox_rejects_reversed_coordinates() -> None:
    with pytest.raises(ValidationError, match="右下坐标"):
        BoundingBox(x0=2, y0=0, x1=1, y1=3)
