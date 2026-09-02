from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.identity import current_user_id
from app.core.config import get_settings
from app.memory.models import (
    FactCreateRequest,
    FactListResponse,
    FactUpdateRequest,
    Project,
    ProjectCreateRequest,
    ProjectFact,
    ProjectListResponse,
    ReviewListResponse,
    ReviewRecord,
)
from app.memory.repository import SQLiteMemoryRepository
from app.memory.service import ProjectMemoryService

router = APIRouter(prefix="/projects", tags=["projects"])


@lru_cache
def get_project_memory_service() -> ProjectMemoryService:
    repository = SQLiteMemoryRepository(get_settings().storage.session_sqlite_path)
    repository.initialize()
    return ProjectMemoryService(repository)


async def provide_project_memory_service() -> ProjectMemoryService:
    return get_project_memory_service()


@router.post("", response_model=Project)
async def create_project(
    request: ProjectCreateRequest,
    user_id: Annotated[str, Depends(current_user_id)],
    service: Annotated[ProjectMemoryService, Depends(provide_project_memory_service)],
) -> Project:
    return service.create_project(user_id, request)


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    user_id: Annotated[str, Depends(current_user_id)],
    service: Annotated[ProjectMemoryService, Depends(provide_project_memory_service)],
) -> ProjectListResponse:
    return ProjectListResponse(items=service.repository.list_projects(user_id))


@router.get("/{project_id}", response_model=Project)
async def get_project(
    project_id: str,
    user_id: Annotated[str, Depends(current_user_id)],
    service: Annotated[ProjectMemoryService, Depends(provide_project_memory_service)],
) -> Project:
    return service.repository.get_project(user_id, project_id)


@router.get("/{project_id}/facts", response_model=FactListResponse)
async def list_facts(
    project_id: str,
    user_id: Annotated[str, Depends(current_user_id)],
    service: Annotated[ProjectMemoryService, Depends(provide_project_memory_service)],
) -> FactListResponse:
    return FactListResponse(items=service.repository.list_facts(user_id, project_id))


@router.get("/{project_id}/facts/history", response_model=FactListResponse)
async def fact_history(
    project_id: str,
    user_id: Annotated[str, Depends(current_user_id)],
    service: Annotated[ProjectMemoryService, Depends(provide_project_memory_service)],
) -> FactListResponse:
    return FactListResponse(
        items=service.repository.list_facts(user_id, project_id, include_history=True)
    )


@router.post("/{project_id}/facts", response_model=ProjectFact)
async def create_fact(
    project_id: str,
    request: FactCreateRequest,
    user_id: Annotated[str, Depends(current_user_id)],
    service: Annotated[ProjectMemoryService, Depends(provide_project_memory_service)],
) -> ProjectFact:
    return service.create_fact(user_id, project_id, request)


@router.patch("/{project_id}/facts/{fact_id}", response_model=ProjectFact)
async def update_fact(
    project_id: str,
    fact_id: str,
    request: FactUpdateRequest,
    user_id: Annotated[str, Depends(current_user_id)],
    service: Annotated[ProjectMemoryService, Depends(provide_project_memory_service)],
) -> ProjectFact:
    return service.update_fact(user_id, project_id, fact_id, request)


@router.get("/{project_id}/changes")
async def list_changes(
    project_id: str,
    user_id: Annotated[str, Depends(current_user_id)],
    service: Annotated[ProjectMemoryService, Depends(provide_project_memory_service)],
) -> list[dict[str, object]]:
    return service.repository.list_changes(user_id, project_id)


@router.get("/{project_id}/reviews", response_model=ReviewListResponse)
async def list_reviews(
    project_id: str,
    user_id: Annotated[str, Depends(current_user_id)],
    service: Annotated[ProjectMemoryService, Depends(provide_project_memory_service)],
) -> ReviewListResponse:
    return ReviewListResponse(items=service.repository.list_reviews(user_id, project_id))


@router.get("/{project_id}/reviews/{review_id}", response_model=ReviewRecord)
async def get_review(
    project_id: str,
    review_id: str,
    user_id: Annotated[str, Depends(current_user_id)],
    service: Annotated[ProjectMemoryService, Depends(provide_project_memory_service)],
) -> ReviewRecord:
    return service.repository.get_review(user_id, project_id, review_id)
