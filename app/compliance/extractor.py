from typing import Protocol

from app.compliance.models import RuleExtractionResult
from app.core.model_client import CompatibleJSONClient
from app.domain.models import Evidence


class LLMRuleExtractor(Protocol):
    """模型只能生成结构化候选；最终判断始终由 RuleEvaluator 执行。"""

    def extract(self, evidence: list[Evidence]) -> RuleExtractionResult: ...


def validate_extraction(payload: object) -> RuleExtractionResult:
    """抽取失败直接返回验证错误，不构造半成品规则。"""
    return RuleExtractionResult.model_validate(payload)


class CompatibleLLMRuleExtractor:
    """只把 Evidence 转换为规则候选；不执行比较，不产生合规状态。"""

    def __init__(self, client: CompatibleJSONClient) -> None:
        self.client = client

    def extract(self, evidence: list[Evidence]) -> RuleExtractionResult:
        safe_payload = [
            {
                "evidence_id": item.evidence_id,
                "content": item.content,
                "support_type": item.support_type,
                "table_id": item.citation.table_id,
            }
            for item in evidence
        ]
        prompt = (
            "从证据抽取可执行规则，保留应/不得/宜/不宜/可的强度。不得推断缺失数值。"
            "表格证据不是 SUFFICIENT 时只能输出 MANUAL_REVIEW；外部引用设置 "
            f"external_standard_required=true。证据：\n{safe_payload}"
        )
        result = self.client.complete(prompt, RuleExtractionResult)
        allowed_ids = {item.evidence_id for item in evidence}
        for rule in result.rules:
            if not set(rule.evidence_ids).issubset(allowed_ids):
                raise ValueError(f"规则 {rule.rule_id} 引用了输入之外的 Evidence")
        return result
