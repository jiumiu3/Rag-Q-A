from app.memory.models import (
    FactCreateRequest,
    FactUpdateRequest,
    Project,
    ProjectCreateRequest,
    ProjectFact,
)
from app.memory.repository import SQLiteMemoryRepository


class ProjectMemoryService:
    def __init__(self, repository: SQLiteMemoryRepository) -> None:
        self.repository = repository

    def create_project(self, user_id: str, request: ProjectCreateRequest) -> Project:
        return self.repository.create_project(user_id, request.name, request.project_type)

    def create_fact(self, user_id: str, project_id: str, request: FactCreateRequest) -> ProjectFact:
        return self.repository.create_fact(
            user_id,
            project_id,
            request.fact_key,
            request.value,
            request.unit,
            request.idempotency_key,
        )

    def update_fact(
        self, user_id: str, project_id: str, fact_id: str, request: FactUpdateRequest
    ) -> ProjectFact:
        return self.repository.update_fact(
            user_id,
            project_id,
            fact_id,
            request.value,
            request.unit,
            request.expected_version,
            request.idempotency_key,
        )
