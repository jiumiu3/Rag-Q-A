import asyncio
from typing import Any

import httpx

from app.main import app


async def post(path: str, payload: dict[str, Any]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=payload)


def test_design_preview_api() -> None:
    response = asyncio.run(
        post(
            "/api/v1/design/preview",
            {"description": "新建输气站控制室照度为500lx。", "auto_confirm": False},
        )
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["attribute"] == "照度"


def test_compliance_evaluate_api() -> None:
    response = asyncio.run(
        post(
            "/api/v1/compliance/evaluate",
            {
                "evaluations": [
                    {
                        "item": {
                            "item_id": "check_1",
                            "object": "仪表电缆",
                            "attribute": "间距",
                            "value": "0.3",
                            "unit": "m",
                            "source_text": "间距0.3m",
                            "span": {"start": 0, "end": 6},
                        },
                        "rule": {
                            "rule_type": "NUMERIC_THRESHOLD",
                            "rule_id": "rule_1",
                            "subject": "仪表电缆",
                            "attribute": "间距",
                            "operator": ">=",
                            "threshold": "300",
                            "unit": "mm",
                            "requirement_level": "SHALL",
                            "evidence_ids": ["evidence_1"],
                        },
                    }
                ]
            },
        )
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "COMPLIANT"


def test_invalid_rule_is_rejected_by_api_schema() -> None:
    response = asyncio.run(
        post(
            "/api/v1/compliance/evaluate",
            {
                "evaluations": [
                    {
                        "item": {
                            "item_id": "check_1",
                            "object": "电缆",
                            "attribute": "间距",
                            "source_text": "测试",
                            "span": {"start": 0, "end": 2},
                        },
                        "rule": {"rule_type": "NUMERIC_THRESHOLD"},
                    }
                ]
            },
        )
    )
    assert response.status_code == 422
