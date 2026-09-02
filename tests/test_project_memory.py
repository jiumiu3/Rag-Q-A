import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.api.projects import provide_project_memory_service
from app.core.exceptions import FactVersionConflictError, ProjectNotFoundError
from app.main import app
from app.memory.repository import SQLiteMemoryRepository
from app.memory.service import ProjectMemoryService


def repository(tmp_path: Path) -> SQLiteMemoryRepository:
    return SQLiteMemoryRepository(tmp_path / "sessions.db")


def test_project_and_fact_are_isolated_by_user_and_project(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    a = repo.create_project("user_a", "A站", None)
    b = repo.create_project("user_a", "B站", None)
    repo.create_fact("user_a", a.project_id, "ups_duration", 2, "h", "create-a-0001")
    repo.create_fact("user_a", b.project_id, "ups_duration", 4, "h", "create-b-0001")

    facts = repo.list_facts("user_a", a.project_id)
    assert [(item.value, item.unit) for item in facts] == [(2, "h")]
    with pytest.raises(ProjectNotFoundError):
        repo.list_facts("user_b", a.project_id)


def test_fact_update_preserves_history_and_current_uniqueness(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    project = repo.create_project("user_a", "A站", None)
    old = repo.create_fact("user_a", project.project_id, "ups_duration", 2, "h", "create-fact-0001")
    new = repo.update_fact("user_a", project.project_id, old.fact_id, 1, "h", 1, "update-fact-0001")

    current = repo.list_facts("user_a", project.project_id)
    history = repo.list_facts("user_a", project.project_id, include_history=True)
    assert [(item.value, item.version, item.status) for item in current] == [(1, 2, "active")]
    assert [(item.value, item.version, item.status) for item in history] == [
        (1, 2, "active"),
        (2, 1, "superseded"),
    ]
    assert history[1].superseded_by == new.fact_id


def test_fact_write_is_idempotent_and_rejects_stale_version(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    project = repo.create_project("user_a", "A站", None)
    first = repo.create_fact(
        "user_a", project.project_id, "illumination", 500, "lx", "same-key-0001"
    )
    repeated = repo.create_fact(
        "user_a", project.project_id, "illumination", 999, "lx", "same-key-0001"
    )
    assert repeated.fact_id == first.fact_id
    repo.update_fact("user_a", project.project_id, first.fact_id, 350, "lx", 1, "update-key-0001")
    with pytest.raises(FactVersionConflictError):
        repo.update_fact(
            "user_a", project.project_id, first.fact_id, 300, "lx", 1, "update-key-0002"
        )


async def request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    user_id: str | None = "user_a",
) -> httpx.Response:
    headers = {"X-User-Id": user_id} if user_id else {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=payload, headers=headers)


def test_project_api_requires_identity_and_enforces_scope(tmp_path: Path) -> None:
    service = ProjectMemoryService(repository(tmp_path))

    async def override() -> ProjectMemoryService:
        return service

    app.dependency_overrides[provide_project_memory_service] = override
    try:
        missing = asyncio.run(request("GET", "/api/v1/projects", user_id=None))
        assert missing.status_code == 400
        created = asyncio.run(
            request("POST", "/api/v1/projects", {"name": "A站", "project_type": "station"})
        )
        assert created.status_code == 200
        project_id = created.json()["project_id"]
        hidden = asyncio.run(request("GET", f"/api/v1/projects/{project_id}", user_id="user_b"))
        assert hidden.status_code == 404
        fact = asyncio.run(
            request(
                "POST",
                f"/api/v1/projects/{project_id}/facts",
                {
                    "fact_key": "ups_duration",
                    "value": 2,
                    "unit": "h",
                    "idempotency_key": "api-fact-0001",
                },
            )
        )
        assert fact.status_code == 200
        assert fact.json()["value"] == 2
    finally:
        app.dependency_overrides.clear()
