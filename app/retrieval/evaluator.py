import re

from app.domain.models import Evidence, EvidenceStatus, RetrievalPlan
from app.retrieval.models import (
    EvidenceAssessment,
    GoalEvidenceAssessment,
    QuestionAnalysis,
    RetrievalGoalStatus,
)


class QAEvidenceEvaluator:
    """对每个检索目标执行确定性覆盖和安全边界检查。"""

    def evaluate(
        self,
        analysis: QuestionAnalysis,
        plan: RetrievalPlan,
        evidence: list[Evidence],
        valid_evidence_ids: set[str],
        new_evidence_count: int,
    ) -> EvidenceAssessment:
        direct = [item for item in evidence if item.context_reason is None]
        goal_results: list[GoalEvidenceAssessment] = []
        for goal in analysis.goals:
            identifiers = goal.required_identifiers
            if identifiers:
                candidates = [
                    item
                    for item in direct
                    if (
                        item.citation.clause_id in identifiers
                        or item.citation.table_id in identifiers
                    )
                    and "exact" in item.retrieval_sources
                ]
            else:
                candidates = self._candidates(goal.description, direct)
            legal = [item for item in candidates if item.evidence_id in valid_evidence_ids]
            partial = [item for item in legal if item.support_type == EvidenceStatus.PARTIAL]
            conflicts = [item for item in legal if item.support_type == EvidenceStatus.CONFLICTING]
            supported = [
                item
                for item in legal
                if item.support_type == EvidenceStatus.SUFFICIENT and item not in conflicts
            ]
            if conflicts:
                status = RetrievalGoalStatus.CONFLICT
            elif supported:
                status = RetrievalGoalStatus.SUPPORTED
            elif partial:
                status = RetrievalGoalStatus.PARTIAL
            else:
                status = RetrievalGoalStatus.UNSUPPORTED
            goal_results.append(
                GoalEvidenceAssessment(
                    goal_id=goal.goal_id,
                    status=status,
                    supporting_evidence_ids=[item.evidence_id for item in supported],
                    missing_aspects=[] if supported else [goal.description],
                    conflict_evidence_ids=[item.evidence_id for item in conflicts],
                )
            )
        statuses = {item.status for item in goal_results}
        sufficient = bool(goal_results) and statuses == {RetrievalGoalStatus.SUPPORTED}
        exact_missing = bool(plan.exact_keys) and not sufficient
        has_conflict = RetrievalGoalStatus.CONFLICT in statuses
        has_partial = RetrievalGoalStatus.PARTIAL in statuses
        missing = [aspect for item in goal_results for aspect in item.missing_aspects]
        if sufficient:
            action = "accept"
            reason = None
        elif has_conflict:
            action = "manual_review"
            reason = "检索到相互冲突的证据"
        elif exact_missing:
            action = "safe_stop"
            reason = "目标条款或表号精确检索未命中"
        elif has_partial:
            action = "manual_review"
            reason = "表格未可靠结构化"
        elif new_evidence_count == 0:
            action = "safe_stop"
            reason = "本轮未产生新增 Evidence"
        elif plan.attempt + 1 >= 3:
            action = "safe_stop"
            reason = "达到最大检索轮数"
        else:
            action = "expand_query"
            reason = "检索结果未覆盖全部目标"
        return EvidenceAssessment(
            is_sufficient=sufficient,
            failure_reason=reason,
            missing_aspects=missing,
            suggested_query_terms=missing,
            goal_assessments=goal_results,
            next_action=action,
        )

    @staticmethod
    def _candidates(description: str, evidence: list[Evidence]) -> list[Evidence]:
        ascii_terms, chinese_bigrams = QAEvidenceEvaluator._goal_signals(description)
        if not ascii_terms and not chinese_bigrams:
            return []
        minimum_chinese_overlap = min(2, len(chinese_bigrams))
        matches: list[Evidence] = []
        for item in evidence:
            content = item.content.casefold()
            ascii_match = any(term in content for term in ascii_terms)
            chinese_overlap = sum(term in content for term in chinese_bigrams)
            if ascii_match or chinese_overlap >= minimum_chinese_overlap:
                matches.append(item)
        return matches

    @staticmethod
    def _goal_signals(description: str) -> tuple[set[str], set[str]]:
        normalized = description.casefold()
        ascii_terms = {
            term for term in re.findall(r"[a-z][a-z0-9./-]*", normalized) if len(term) >= 2
        }
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]+", normalized))
        for phrase in (
            "有什么要求",
            "有哪些要求",
            "什么要求",
            "如何规定",
            "怎么规定",
            "是否符合",
            "要求",
            "规定",
            "是否",
            "什么",
            "如何",
            "怎么",
        ):
            chinese = chinese.replace(phrase, "")
        if len(chinese) < 2:
            return ascii_terms, set()
        if len(chinese) == 2:
            return ascii_terms, {chinese}
        return ascii_terms, {chinese[index : index + 2] for index in range(len(chinese) - 1)}
