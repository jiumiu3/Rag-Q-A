from pydantic import Field, model_validator

from app.domain.models import EvidenceStatus, IntentType, SourceCitation, StrictModel


class AnswerClaim(StrictModel):
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class RAGGeneratedAnswer(StrictModel):
    """模型只生成结论与 Evidence 绑定，引用详情由代码补全。"""

    conclusion: str = Field(min_length=1)
    claims: list[AnswerClaim] = Field(min_length=1, max_length=5)
    limitations: list[str] = Field(default_factory=list)


class RetrievalToolCall(StrictModel):
    tool_name: str = "knowledge_search"
    query: str
    top_k: int = Field(ge=1, le=10)
    result_count: int = Field(ge=0)


class AnswerDraft(StrictModel):
    intent: IntentType
    status: EvidenceStatus
    conclusion: str
    applicable_conditions: list[str] = Field(default_factory=list)
    claims: list[AnswerClaim] = Field(default_factory=list)
    citations: list[SourceCitation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    tool_calls: list[RetrievalToolCall] = Field(default_factory=list)
    answer_text: str = ""

    @model_validator(mode="after")
    def require_claim_citations(self) -> "AnswerDraft":
        if self.status == EvidenceStatus.SUFFICIENT and not self.claims:
            raise ValueError("充分证据回答必须包含至少一个 claim")
        # API 与前端共用同一展示文本，保证每次回答末尾都有真实来源或无来源声明。
        body = [self.conclusion]
        body.extend(f"{index}. {claim.text}" for index, claim in enumerate(self.claims, 1))
        if self.limitations:
            body.append("限制：" + "；".join(self.limitations))
        body.append("证据来源：")
        if self.citations:
            for index, citation in enumerate(self.citations, 1):
                locator = citation.clause_id or citation.table_id or "未标号内容"
                body.append(
                    f"[{index}] {citation.standard_code}，{locator}，第 {citation.page_number} 页"
                )
        else:
            body.append("无可用规范证据。")
        object.__setattr__(self, "answer_text", "\n".join(body))
        return self


class QARequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


class QAResponse(StrictModel):
    answer: AnswerDraft
    trace: dict[str, object]
