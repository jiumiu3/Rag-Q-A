import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from app.domain.models import RetrievalFilters
from app.knowledge.models import SearchHit, StoredUnit

TOKEN_PATTERN = re.compile(r"[a-z]+(?:[./-][a-z0-9]+)*|\d+(?:\.\d+)*|[\u4e00-\u9fff]", re.I)


class EmbeddingClient(Protocol):
    @property
    def model_id(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def tokenize(text: str) -> list[str]:
    """中文首版按单字并保留英文/条款号词元，避免依赖外部分词词典。"""
    return TOKEN_PATTERN.findall(text.casefold().replace("—", "-").replace("－", "-"))


class HashEmbeddingClient:
    """离线确定性后备向量；生产环境可替换为实现同一协议的嵌入 API。"""

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    @property
    def model_id(self) -> str:
        return f"local-hash-v1-{self.dimensions}"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        output: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in tokenize(text):
                digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
                index = int.from_bytes(digest, "big") % self.dimensions
                vector[index] += -1.0 if digest[0] & 1 else 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            output.append([value / norm for value in vector])
        return output


class BM25Index:
    def __init__(self, documents: dict[str, list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.documents = documents
        self.k1 = k1
        self.b = b

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"documents": self.documents}, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(payload["documents"])

    def search(self, query: str, allowed_ids: set[str], limit: int) -> list[SearchHit]:
        query_tokens = tokenize(query)
        docs = {key: value for key, value in self.documents.items() if key in allowed_ids}
        if not query_tokens or not docs:
            return []
        counts = {key: Counter(tokens) for key, tokens in docs.items()}
        avg_length = sum(len(tokens) for tokens in docs.values()) / len(docs)
        frequencies = Counter(
            token for token in set(query_tokens) for tokens in docs.values() if token in tokens
        )
        scored: list[tuple[str, float]] = []
        for unit_id, terms in counts.items():
            score = 0.0
            for token in query_tokens:
                frequency = terms[token]
                if not frequency:
                    continue
                idf = math.log(
                    1 + (len(docs) - frequencies[token] + 0.5) / (frequencies[token] + 0.5)
                )
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * sum(terms.values()) / avg_length
                )
                score += idf * frequency * (self.k1 + 1) / denominator
            if score > 0:
                scored.append((unit_id, score))
        scored.sort(key=lambda item: (-item[1], item[0]))
        maximum = scored[0][1] if scored else 1.0
        return [
            SearchHit(knowledge_unit_id=key, score=score / maximum, source="bm25")
            for key, score in scored[:limit]
        ]


class VectorIndex:
    """持久化统一 ID 映射；安装 faiss-cpu 后自动使用 IndexFlatIP。"""

    def __init__(self, ids: list[str], vectors: list[list[float]]) -> None:
        self.ids = ids
        self.vectors = vectors

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "vector_mapping.json").write_text(json.dumps(self.ids), encoding="utf-8")
        (directory / "vectors.json").write_text(json.dumps(self.vectors), encoding="utf-8")
        try:
            import faiss  # type: ignore[import-not-found]
            import numpy as np

            matrix: np.ndarray = np.asarray(self.vectors, dtype="float32")
            index = faiss.IndexFlatIP(matrix.shape[1])
            index.add(matrix)
            faiss.write_index(index, str(directory / "vectors.faiss"))
        except ImportError:
            # 未安装可选依赖时保留精确余弦后备，构建和测试仍可离线运行。
            pass

    @classmethod
    def load(cls, directory: Path) -> "VectorIndex":
        return cls(
            json.loads((directory / "vector_mapping.json").read_text()),
            json.loads((directory / "vectors.json").read_text()),
        )

    def search(
        self, query_vector: list[float], allowed_ids: set[str], limit: int
    ) -> list[SearchHit]:
        scores = [
            (unit_id, sum(a * b for a, b in zip(query_vector, vector, strict=True)))
            for unit_id, vector in zip(self.ids, self.vectors, strict=True)
            if unit_id in allowed_ids
        ]
        scores.sort(key=lambda item: (-item[1], item[0]))
        return [
            SearchHit(knowledge_unit_id=key, score=max(0.0, score), source="vector")
            for key, score in scores[:limit]
            if score > 0
        ]


def allowed_unit_ids(units: Sequence[StoredUnit], filters: RetrievalFilters) -> set[str]:
    allowed: set[str] = set()
    for stored in units:
        pages = {span.page_number for span in stored.unit.source_spans}
        if filters.standard_codes and stored.standard_code not in filters.standard_codes:
            continue
        if filters.unit_types and stored.unit.unit_type not in filters.unit_types:
            continue
        if filters.page_numbers and not pages.intersection(filters.page_numbers):
            continue
        allowed.add(stored.unit.unit_id)
    return allowed
