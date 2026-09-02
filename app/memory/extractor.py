import json
from typing import Protocol

from app.memory.models import MemoryCandidateList, ProjectFact


class StructuredCompletionClient(Protocol):
    def complete(
        self, prompt: str, response_model: type[MemoryCandidateList]
    ) -> MemoryCandidateList: ...


class MemoryCandidateExtractor:
    """用模型提取候选事实，但不让模型决定数据库状态。"""

    def __init__(self, client: StructuredCompletionClient) -> None:
        self.client = client

    def extract(self, message: str, current_facts: list[ProjectFact]) -> MemoryCandidateList:
        facts = [
            {"fact_key": item.fact_key, "value": item.value, "unit": item.unit}
            for item in current_facts
        ]
        prompt = (
            "从用户消息中提取工程项目事实候选。只提取消息明确包含的事实，不提取规范要求、"
            "助手结论或常识。若用户明确改变已有值，action=update；新事实 action=create；"
            "没有项目事实时返回空列表。疑问式‘如果’属于 hypothetical；‘应该/大概/可能’"
            "属于 uncertain 并要求确认。fact_key 使用简短 snake_case。"
            "不得把当前事实复制成新候选。\n\n"
            + json.dumps({"current_facts": facts, "user_message": message}, ensure_ascii=False)
        )
        return self.client.complete(prompt, MemoryCandidateList)
