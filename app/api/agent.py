import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from app.api.qa import get_qa_service
from app.compliance.repository import SQLiteRuleRepository
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.workflow.graph import WorkflowRunner, graph_mermaid
from app.workflow.models import (
    AgentState,
    SessionCreateRequest,
    SessionMessageRequest,
    WorkflowClarificationRequest,
    WorkflowConfirmRequest,
)
from app.workflow.nodes import WorkflowNodes
from app.workflow.repository import SQLiteWorkflowRepository
from app.workflow.service import WorkflowService

router = APIRouter(tags=["agent"])
PAGE_ID = re.compile(r"^doc_[a-f0-9]{20}$")


class ApiResourceError(AppError):
    code = "RESOURCE_NOT_FOUND"


@lru_cache
def get_workflow_service() -> WorkflowService:
    settings = get_settings()
    qa = get_qa_service()
    nodes = WorkflowNodes(
        qa,
        qa.retrieval,
        SQLiteRuleRepository(settings.storage.session_sqlite_path),
    )
    repository = SQLiteWorkflowRepository(settings.storage.session_sqlite_path)
    return WorkflowService(repository, WorkflowRunner(nodes))


async def provide_workflow_service() -> WorkflowService:
    return get_workflow_service()


@router.post("/sessions", response_model=AgentState)
async def create_session(
    _request: SessionCreateRequest,
    service: Annotated[WorkflowService, Depends(provide_workflow_service)],
) -> AgentState:
    return service.create_session()


@router.post("/sessions/{session_id}/messages", response_model=AgentState)
async def send_message(
    session_id: str,
    request: SessionMessageRequest,
    service: Annotated[WorkflowService, Depends(provide_workflow_service)],
) -> AgentState:
    return service.send_message(session_id, request.content, request.idempotency_key)


@router.post("/sessions/{session_id}/confirm", response_model=AgentState)
async def confirm_items(
    session_id: str,
    request: WorkflowConfirmRequest,
    service: Annotated[WorkflowService, Depends(provide_workflow_service)],
) -> AgentState:
    return service.confirm(session_id, request)


@router.post("/sessions/{session_id}/clarify", response_model=AgentState)
async def clarify(
    session_id: str,
    request: WorkflowClarificationRequest,
    service: Annotated[WorkflowService, Depends(provide_workflow_service)],
) -> AgentState:
    return service.clarify(session_id, request.answers)


@router.get("/sessions/{session_id}", response_model=AgentState)
async def get_session(
    session_id: str,
    service: Annotated[WorkflowService, Depends(provide_workflow_service)],
) -> AgentState:
    return service.repository.load(session_id)


@router.post("/sessions/{session_id}/replay", response_model=AgentState)
async def replay(
    session_id: str,
    service: Annotated[WorkflowService, Depends(provide_workflow_service)],
    start_node: str | None = Query(default=None),
) -> AgentState:
    return service.replay(session_id, start_node)


@router.get("/sessions/{session_id}/report")
async def export_report(
    session_id: str,
    service: Annotated[WorkflowService, Depends(provide_workflow_service)],
    format: Literal["markdown", "json"] = Query(default="markdown"),
) -> PlainTextResponse:
    state = service.repository.load(session_id)
    if format == "json":
        return PlainTextResponse(state.model_dump_json(indent=2), media_type="application/json")
    return PlainTextResponse(state.report_markdown or "报告尚未生成", media_type="text/markdown")


@router.get("/knowledge/documents")
async def knowledge_documents() -> list[dict[str, object]]:
    path = get_settings().storage.sqlite_path
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT document_id,file_name,standard_code,source_hash,page_count "
            "FROM documents ORDER BY standard_code"
        )
        return [dict(row) for row in rows]


@router.get("/knowledge/index-versions")
async def index_versions() -> list[dict[str, object]]:
    path = get_settings().storage.sqlite_path
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM index_versions ORDER BY build_time DESC")
        return [dict(row) for row in rows]


@router.get("/knowledge/status")
async def knowledge_status() -> dict[str, object]:
    settings = get_settings()
    manifest = settings.storage.index_dir / "manifest.json"
    reports = []
    for path in sorted(settings.storage.ocr_dir.glob("*/quality_report.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        reports.append(
            {
                "document_id": payload.get("document_id"),
                "failed_pages": payload.get("failed_pages", []),
                "low_confidence_pages": payload.get("low_confidence_pages", []),
            }
        )
    return {
        "index_ready": manifest.exists(),
        "index_manifest": json.loads(manifest.read_text()) if manifest.exists() else None,
        "ocr_reports": reports,
        "rebuild_running": False,
    }


@router.get("/pages/{document_id}/{page_number}")
async def source_page(document_id: str, page_number: int) -> FileResponse:
    settings = get_settings()
    if not PAGE_ID.fullmatch(document_id) or page_number < 1:
        raise ApiResourceError("非法页面定位参数")
    manifest_path = settings.storage.ocr_dir / document_id / "manifest.json"
    if not manifest_path.exists():
        raise ApiResourceError("文档不存在")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    page = next((item for item in manifest["pages"] if item["page_number"] == page_number), None)
    if not page:
        raise ApiResourceError("页码不存在")
    candidate = settings.storage.pages_dir / document_id / Path(page["image_path"]).name
    root = settings.storage.pages_dir.resolve()
    resolved = candidate.resolve()
    if root not in resolved.parents or not resolved.exists():
        raise ApiResourceError("页面文件不存在")
    return FileResponse(resolved, media_type="image/png")


@router.get("/workflow/graph", response_class=PlainTextResponse)
async def workflow_graph() -> str:
    return graph_mermaid()


@router.get("/demo", response_class=HTMLResponse, include_in_schema=False)
async def demo() -> HTMLResponse:
    html_path = Path(__file__).resolve().parents[1] / "web" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))
