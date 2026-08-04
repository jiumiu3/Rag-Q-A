import json
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.core.exceptions import RetrievalError
from app.core.model_client import CompatibleEmbeddingClient, CompatibleJSONClient
from app.knowledge.indexes import BM25Index, EmbeddingClient, HashEmbeddingClient, VectorIndex
from app.knowledge.repository import SQLiteKnowledgeRepository
from app.qa.models import QARequest, QAResponse
from app.qa.service import QAService, RAGAgentAnswerer
from app.retrieval.service import RetrievalService

router = APIRouter(tags=["qa"])


@lru_cache
def get_qa_service() -> QAService:
    settings = get_settings()
    index_dir = settings.storage.index_dir
    required = [
        settings.storage.sqlite_path,
        index_dir / "bm25.json",
        index_dir / "vectors.json",
        index_dir / "manifest.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RetrievalError("M3 索引尚未构建", details={"missing": missing})
    repository = SQLiteKnowledgeRepository(settings.storage.sqlite_path)
    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    indexed_model = str(manifest["embedding_model_id"])
    embedding: EmbeddingClient
    if indexed_model.startswith("local-hash-v1-"):
        embedding = HashEmbeddingClient(int(indexed_model.rsplit("-", 1)[-1]))
    elif settings.model.embedding_model == indexed_model:
        embedding = CompatibleEmbeddingClient(settings.model)
    else:
        raise RetrievalError(
            "索引 Embedding 模型与当前配置不一致",
            details={"indexed_model": indexed_model, "configured": settings.model.embedding_model},
        )
    retrieval = RetrievalService(
        repository,
        BM25Index.load(index_dir / "bm25.json"),
        VectorIndex.load(index_dir),
        embedding,
    )
    rag_answerer = (
        RAGAgentAnswerer(CompatibleJSONClient(settings.model), retrieval)
        if settings.agent.rag_llm_enabled
        else None
    )
    return QAService(retrieval, rag_answerer)


@router.post("/qa", response_model=QAResponse)
async def qa(
    request: QARequest, service: Annotated[QAService, Depends(get_qa_service)]
) -> QAResponse:
    answer, result = service.ask(request.question, request.top_k)
    return QAResponse(answer=answer, trace=result.trace.model_dump(mode="json"))
