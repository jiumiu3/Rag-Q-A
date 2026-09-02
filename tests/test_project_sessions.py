import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import httpx

from app.api.agent import provide_workflow_service
from app.api.projects import provide_project_memory_service
from app.main import app
from app.memory.repository import SQLiteMemoryRepository
from app.memory.service import ProjectMemoryService
from app.workflow.graph import WorkflowRunner
from app.workflow.nodes import WorkflowNodes
from app.workflow.repository import SQLiteWorkflowRepository
from app.workflow.service import WorkflowService
from tests.test_m9_workflow import FakeQA


def services(tmp_path: Path) -> tuple[WorkflowService, ProjectMemoryService]:
    path = tmp_path / "sessions.db"
    memory = SQLiteMemoryRepository(path)
    qa = FakeQA()
    workflow = WorkflowService(
        SQLiteWorkflowRepository(path),
        WorkflowRunner(WorkflowNodes(qa, qa.retrieval)),  # type: ignore[arg-type]
        memory,
    )
    return workflow, ProjectMemoryService(memory)


async def request(
    method: str, path: str, payload: dict[str, Any] | None, user_id: str
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=payload, headers={"X-User-Id": user_id})


def test_session_binds_owned_project_and_rejects_cross_user(tmp_path: Path) -> None:
    workflow, projects = services(tmp_path)
    project = projects.repository.create_project("user_a", "A站", None)

    async def workflow_override() -> WorkflowService:
        return workflow

    async def project_override() -> ProjectMemoryService:
        return projects

    app.dependency_overrides[provide_workflow_service] = workflow_override
    app.dependency_overrides[provide_project_memory_service] = project_override
    try:
        created = asyncio.run(
            request(
                "POST",
                "/api/v1/sessions",
                {"mode": "review", "project_id": project.project_id},
                "user_a",
            )
        )
        assert created.status_code == 200
        assert created.json()["project_id"] == project.project_id
        session_id = created.json()["session_id"]
        hidden = asyncio.run(request("GET", f"/api/v1/sessions/{session_id}", None, "user_b"))
        assert hidden.status_code == 404
        visible = asyncio.run(request("GET", f"/api/v1/sessions/{session_id}", None, "user_a"))
        assert visible.status_code == 200
        assert visible.json()["user_id"] == "user_a"
    finally:
        app.dependency_overrides.clear()


def test_session_repository_migrates_legacy_table(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE workflow_sessions ("
            "session_id TEXT PRIMARY KEY,state_json TEXT NOT NULL,updated_at TEXT NOT NULL)"
        )
    repository = SQLiteWorkflowRepository(path)
    repository.initialize()
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_sessions)")}
    assert {"user_id", "project_id", "workflow_stage"}.issubset(columns)
