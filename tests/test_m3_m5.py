import json
from pathlib import Path
from typing import Any, cast

from app.domain.models import (
    BoundingBox,
    EvidenceStatus,
    IntentType,
    KnowledgeUnit,
    SourceSpan,
    UnitType,
)
from app.knowledge.indexes import BM25Index, HashEmbeddingClient, VectorIndex, tokenize
from app.knowledge.models import StoredUnit
from app.knowledge.repository import SQLiteKnowledgeRepository
from app.qa.models import AnswerClaim, AnswerDraft
from app.qa.service import CitationValidator, QAService, RAGAgentAnswerer
from app.retrieval.service import QueryAnalyzer, RetrievalService


def stored_unit(
    unit_id: str,
    content: str,
    clause: str | None = None,
    table: str | None = None,
    status: str | None = None,
) -> StoredUnit:
    unit_type = UnitType.TABLE if table else UnitType.CLAUSE
    return StoredUnit(
        unit=KnowledgeUnit(
            unit_id=unit_id,
            unit_type=unit_type,
            content=content,
            clause_id=unit_id if clause else None,
            table_id=unit_id if table else None,
            source_spans=[
                SourceSpan(
                    document_id="doc_test",
                    page_number=3,
                    bbox=BoundingBox(x0=0, y0=0, x1=1, y1=1),
                )
            ],
        ),
        document_id="doc_test",
        standard_code="Q/GGW 02005.1-2022",
        file_name="test.pdf",
        clause_number=clause,
        table_number=table,
        parse_status=status,
    )


def services(tmp_path: Path) -> tuple[SQLiteKnowledgeRepository, RetrievalService, QAService]:
    repository = SQLiteKnowledgeRepository(tmp_path / "knowledge.db")
    units = [
        stored_unit("u1", "5.2.1 仪表应可靠接地。", clause="5.2.1"),
        stored_unit(
            "u2",
            "表 6 压力参数候选",
            table="6",
            status="NEEDS_MANUAL_ANNOTATION",
        ),
    ]
    repository.replace_document(
        {
            "document_id": "doc_test",
            "file_name": "test.pdf",
            "standard_code": "Q/GGW 02005.1-2022",
            "source_hash": "a" * 64,
            "page_count": 3,
        },
        units,
    )
    documents = {item.unit.unit_id: list(item.unit.content) for item in units}
    bm25 = BM25Index(documents)
    embedding = HashEmbeddingClient(32)
    vector = VectorIndex(
        [item.unit.unit_id for item in units],
        embedding.embed([item.unit.content for item in units]),
    )
    retrieval = RetrievalService(repository, bm25, vector, embedding)
    return repository, retrieval, QAService(retrieval)


def test_repository_exact_clause_and_replacement(tmp_path: Path) -> None:
    repository, _, _ = services(tmp_path)
    analysis, plan = QueryAnalyzer().plan("Q/GGW 02005.1 第5.2.1条")
    hits = repository.exact_search(analysis.clause_numbers, plan.filters, 5)
    assert [hit.knowledge_unit_id for hit in hits] == ["u1"]
    assert repository.list_document_ids() == ["doc_test"]


def test_repository_can_replace_same_document_without_duplicate_ids(tmp_path: Path) -> None:
    repository, _, _ = services(tmp_path)
    unit = stored_unit("u1", "5.2.1 更新后的接地要求。", clause="5.2.1")
    repository.replace_document(
        {
            "document_id": "doc_test",
            "file_name": "test.pdf",
            "standard_code": "Q/GGW 02005.1-2022",
            "source_hash": "b" * 64,
            "page_count": 3,
        },
        [unit],
    )
    assert len(repository.list_units()) == 1
    assert repository.get_unit("u1").unit.content == "5.2.1 更新后的接地要求。"  # type: ignore[union-attr]


def test_indexes_roundtrip_and_id_mapping(tmp_path: Path) -> None:
    _, retrieval, _ = services(tmp_path)
    retrieval.bm25.save(tmp_path / "bm25.json")
    retrieval.vector.save(tmp_path / "vector")
    assert set(BM25Index.load(tmp_path / "bm25.json").documents) == {"u1", "u2"}
    assert VectorIndex.load(tmp_path / "vector").ids == ["u1", "u2"]


def test_bm25_chinese_bigrams_prioritize_engineering_phrase() -> None:
    documents = {
        "target": tokenize("自力式调压器的调节精度应优于规定阈值"),
        "generic": tokenize("工程设计应满足规范要求并进行说明"),
        "other": tokenize("压力变送器应满足工程设计规范要求"),
    }
    hits = BM25Index(documents).search("自力式调压器调节精度要求", set(documents), 3)
    assert hits[0].knowledge_unit_id == "target"


def test_clause_qa_only_returns_exact_clause(tmp_path: Path) -> None:
    _, _, qa = services(tmp_path)
    answer, _ = qa.ask("Q/GGW 02005.1-2022 第5.2.1条是什么？")
    assert answer.status == EvidenceStatus.SUFFICIENT
    assert len(answer.claims) == 1
    assert answer.citations[0].clause_id == "5.2.1"
    assert answer.answer_text.endswith("Q/GGW 02005.1-2022，5.2.1，第 3 页")
    assert "证据来源：" in answer.answer_text


def test_unparsed_table_safely_degrades(tmp_path: Path) -> None:
    _, _, qa = services(tmp_path)
    answer, result = qa.ask("表 6 中的压力参数是多少？")
    assert answer.status == EvidenceStatus.PARTIAL
    assert not answer.claims
    assert answer.citations[0].table_id == "6"
    assert answer.answer_text.rstrip().endswith("Q/GGW 02005.1-2022，6，第 3 页")
    assert result.trace.warnings


def test_answer_without_evidence_explicitly_states_no_source(tmp_path: Path) -> None:
    _, _, qa = services(tmp_path)
    answer, _ = qa.ask("第99.99.99条是什么？")
    assert answer.answer_text.endswith("证据来源：\n无可用规范证据。")


def test_vector_payload_is_json_serializable(tmp_path: Path) -> None:
    _, retrieval, _ = services(tmp_path)
    assert json.dumps(retrieval.vector.vectors)


def test_numeric_parameter_is_not_misrouted_as_clause() -> None:
    analysis = QueryAnalyzer().analyze("设计压力为 1.6 MPa 时有什么要求？")
    assert not analysis.clause_numbers
    assert analysis.route_type == "numeric"


def test_dynamic_route_uses_bm25_for_numeric_parameter() -> None:
    _, plan = QueryAnalyzer().plan("设计压力为 1.6 MPa 时有什么要求？")
    assert plan.retrievers == ["bm25"]


def test_context_limited_clause_is_not_treated_as_exact_lookup() -> None:
    analysis, plan = QueryAnalyzer().plan(
        "请说明孔板流量计的具体要求，限定在Q/GGW 02005.3-2022第5.3.5.7条上下文内回答。"
    )
    assert analysis.route_type == "keyword"
    assert plan.retrievers == ["bm25"]


def test_multi_goal_query_populates_subqueries() -> None:
    analysis, plan = QueryAnalyzer().plan(
        "控制室照度有什么要求；UPS供电时间有什么要求？"
    )
    assert analysis.route_type == "multi_goal"
    assert plan.retrievers == ["bm25", "vector"]
    assert len(plan.subqueries) == 2


def test_clause_and_table_intents_apply_distinct_unit_filters() -> None:
    analyzer = QueryAnalyzer()
    _, clause_plan = analyzer.plan("第1条是什么？")
    _, table_plan = analyzer.plan("表1是什么？")
    assert clause_plan.filters.unit_types == [UnitType.CLAUSE]
    assert table_plan.filters.unit_types == [UnitType.TABLE, UnitType.TABLE_ROW]
    assert clause_plan.retrievers == ["exact"]
    assert table_plan.retrievers == ["exact"]


def test_standard_number_and_year_are_not_engineering_numeric_claims(tmp_path: Path) -> None:
    _, retrieval, _ = services(tmp_path)
    result = retrieval.retrieve("第5.2.1条", 5)
    evidence = result.evidence[0]
    draft = AnswerDraft(
        intent=IntentType.CLAUSE_LOOKUP,
        status=EvidenceStatus.SUFFICIENT,
        conclusion="结论",
        claims=[
            AnswerClaim(
                text="Q/GGW 02005.1-2022 第5.2.1条要求仪表应可靠接地。",
                evidence_ids=[evidence.evidence_id],
            )
        ],
        citations=[evidence.citation],
    )
    assert CitationValidator().validate(draft, result.evidence) == []


class FakeToolCallingClient:
    def chat(
        self,
        messages: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        tool_result = next((item for item in messages if item["role"] == "tool"), None)
        if tool_result is None:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_test",
                        "type": "function",
                        "function": {
                            "name": "knowledge_search",
                            "arguments": json.dumps(
                                {"query": "Q/GGW 02005.1-2022 第5.2.1条", "top_k": 5}
                            ),
                        },
                    }
                ],
            }
        evidence = json.loads(tool_result["content"])
        return {
            "role": "assistant",
            "content": json.dumps(
                {
                    "conclusion": "规范要求仪表可靠接地。",
                    "claims": [
                        {
                            "text": "仪表应可靠接地。",
                            "evidence_ids": [evidence[0]["evidence_id"]],
                        }
                    ],
                    "limitations": [],
                },
                ensure_ascii=False,
            ),
        }


class FakeInvalidFinalClient(FakeToolCallingClient):
    def chat(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        if any(item["role"] == "tool" for item in messages):
            return {"role": "assistant", "content": '{"unexpected": true}'}
        return super().chat(messages, **kwargs)


def test_rag_agent_calls_search_tool_and_binds_source(tmp_path: Path) -> None:
    _, retrieval, _ = services(tmp_path)
    client = cast(Any, FakeToolCallingClient())
    qa = QAService(retrieval, RAGAgentAnswerer(client, retrieval))
    answer, _ = qa.ask("Q/GGW 02005.1-2022 第5.2.1条是什么？")
    assert answer.conclusion == "规范要求仪表可靠接地。"
    assert answer.tool_calls[0].tool_name == "knowledge_search"
    assert answer.tool_calls[0].result_count == 1
    assert answer.citations[0].clause_id == "5.2.1"
    assert answer.answer_text.endswith("Q/GGW 02005.1-2022，5.2.1，第 3 页")


def test_rag_structured_failure_preserves_tool_audit(tmp_path: Path) -> None:
    _, retrieval, _ = services(tmp_path)
    client = cast(Any, FakeInvalidFinalClient())
    qa = QAService(retrieval, RAGAgentAnswerer(client, retrieval))
    answer, _ = qa.ask("Q/GGW 02005.1-2022 第5.2.1条是什么？")
    assert answer.status == EvidenceStatus.SUFFICIENT
    assert answer.tool_calls[0].tool_name == "knowledge_search"
    assert "结构化校验失败" in "；".join(answer.limitations)
