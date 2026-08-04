from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    timestamp: datetime


class VersionResponse(BaseModel):
    name: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """不依赖 OCR、模型和数据库的存活检查。"""
    return HealthResponse(timestamp=datetime.now(UTC))


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    return VersionResponse(name="oil-gas-compliance-agent", version=__version__)
