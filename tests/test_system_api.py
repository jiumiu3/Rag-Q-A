import asyncio

import httpx

from app.main import app


async def request(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


def test_health_endpoint_has_no_external_dependency() -> None:
    response = asyncio.run(request("/api/v1/health"))
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_version_endpoint() -> None:
    response = asyncio.run(request("/api/v1/version"))
    assert response.status_code == 200
    assert response.json() == {"name": "oil-gas-compliance-agent", "version": "0.1.0"}
