import asyncio
from pathlib import Path
from typing import Any

import httpx

from app.api.agent import provide_workflow_service
from app.main import app
from app.workflow.service import WorkflowService
from tests.test_m9_workflow import workflow


async def request(method: str, path: str, payload: dict[str, Any] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=payload)


def install_service(service: WorkflowService) -> None:
    async def override() -> WorkflowService:
        return service

    app.dependency_overrides[provide_workflow_service] = override


def test_session_message_state_and_report_api(tmp_path: Path) -> None:
    install_service(workflow(tmp_path))
    created = asyncio.run(request("POST", "/api/v1/sessions", {"mode": "review"}))
    assert created.status_code == 200
    session_id = created.json()["session_id"]
    message = asyncio.run(
        request(
            "POST",
            f"/api/v1/sessions/{session_id}/messages",
            {"content": "什么是SCADA？", "idempotency_key": "api-idempotency-1"},
        )
    )
    assert message.status_code == 200
    assert message.json()["status"] == "COMPLETED"
    loaded = asyncio.run(request("GET", f"/api/v1/sessions/{session_id}"))
    assert loaded.json()["session_id"] == session_id
    report = asyncio.run(request("GET", f"/api/v1/sessions/{session_id}/report?format=markdown"))
    assert report.status_code == 200
    assert "规范预审报告" in report.text
    assert report.headers["x-request-id"].startswith("request_")
    app.dependency_overrides.clear()


def test_message_idempotency_api(tmp_path: Path) -> None:
    install_service(workflow(tmp_path))
    created = asyncio.run(request("POST", "/api/v1/sessions", {"mode": "review"})).json()
    path = f"/api/v1/sessions/{created['session_id']}/messages"
    payload = {"content": "第5.2.1条是什么？", "idempotency_key": "api-idempotency-2"}
    first = asyncio.run(request("POST", path, payload)).json()
    second = asyncio.run(request("POST", path, payload)).json()
    assert first == second
    app.dependency_overrides.clear()


def test_demo_and_graph_endpoints() -> None:
    demo = asyncio.run(request("GET", "/api/v1/demo"))
    graph = asyncio.run(request("GET", "/api/v1/workflow/graph"))
    assert demo.status_code == 200
    assert "检查项确认" in demo.text
    assert "淡蓝" not in demo.text
    assert "证据与详情侧栏" in demo.text
    assert "判断结果会列出对应来源条款" in demo.text
    assert "正在继续进行规范检索与合规判断" in demo.text
    assert "classify_intent" in graph.text
