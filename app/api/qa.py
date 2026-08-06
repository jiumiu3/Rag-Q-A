import hashlib
import json
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.core.exceptions import RetrievalError
from app.core.model_client import CompatibleEmbeddingClient, CompatibleJSONClient
from app.knowledge.builder import IndexBuilder
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
    manifest_checker = IndexBuilder(
        repository,
        index_dir,
        contextual_enabled=settings.retrieval.contextual_enabled,
        contextual_strategy=settings.retrieval.contextual_strategy,
        contextual_version=settings.retrieval.contextual_version,
    )
    contextual_error = manifest_checker.contextual_manifest_error(manifest)
    if contextual_error:
        raise RetrievalError(contextual_error)
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
    indexed_config_hash = manifest.get("embedding_configuration_hash")
    indexed_dimensions = manifest.get("embedding_dimensions")
    if not indexed_config_hash or not isinstance(indexed_dimensions, int):
        raise RetrievalError("向量索引缺少完整版本绑定，请重新执行 build_index.py")
    current_config = getattr(embedding, "configuration_id", embedding.model_id)
    current_config_hash = hashlib.sha256(current_config.encode()).hexdigest()
    if indexed_config_hash != current_config_hash:
        raise RetrievalError(
            "索引 Embedding 配置与当前配置不一致",
            details={
                "indexed_configuration_hash": indexed_config_hash,
                "configured_configuration_hash": current_config_hash,
            },
        )
    vector = VectorIndex.load(index_dir)
    actual_dimensions = {len(item) for item in vector.vectors}
    if actual_dimensions != {indexed_dimensions}:
        raise RetrievalError(
            "向量索引维度与 manifest 不一致",
            details={"manifest": indexed_dimensions, "actual": sorted(actual_dimensions)},
        )
    retrieval = RetrievalService(
        repository,
        BM25Index.load(index_dir / "bm25.json"),
        vector,
        embedding,
        rrf_k=settings.retrieval.rrf_k,
        rrf_weights={
            "exact": settings.retrieval.exact_weight,
            "bm25": settings.retrieval.bm25_weight,
            "vector": settings.retrieval.vector_weight,
        },
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
