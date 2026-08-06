import re

from app.clarification.service import ClarificationPlanner, CompletenessChecker
from app.compliance.candidates import DeterministicCandidateExtractor
from app.compliance.evaluator import RuleEvaluator
from app.compliance.matcher import RuleMatcher
from app.compliance.models import ManualReviewRule
from app.compliance.repository import SQLiteRuleRepository
from app.design.parser import DesignDescriptionParser
from app.domain.models import (
    CheckItemStatus,
    ComplianceResult,
    ComplianceStatus,
    IntentType,
    RequirementLevel,
)
from app.qa.service import QAService
from app.retrieval.models import (
    EvidenceAssessment,
    RetrievalAttempt,
    RetrievalResult,
    RetrievalTrace,
)
from app.retrieval.planner import AdaptiveRetrievalPlanner
from app.retrieval.service import QueryAnalyzer, RetrievalService
from app.workflow.models import AgentState, PendingAction, WorkflowStatus


class WorkflowNodes:
    """每个节点只返回局部更新；状态合并由图执行器负责。"""

    def __init__(
        self,
        qa: QAService,
        retrieval: RetrievalService,
        rule_repository: SQLiteRuleRepository | None = None,
    ) -> None:
        self.qa = qa
        self.retrieval = retrieval
        self.parser = DesignDescriptionParser()
        self.completeness = CompletenessChecker()
        self.clarifications = ClarificationPlanner()
        self.evaluator = RuleEvaluator()
        self.rule_matcher = RuleMatcher()
        self.query_analyzer = QueryAnalyzer()
        self.retrieval_planner = AdaptiveRetrievalPlanner()
        self.rule_repository = rule_repository
        self.candidate_extractor = DeterministicCandidateExtractor()

    def normalize_input(self, state: AgentState) -> dict[str, object]:
        normalized = re.sub(r"\s+", " ", state.input_text).strip()
        return {"normalized_input": normalized, "status": WorkflowStatus.RUNNING}

    def classify_intent(self, state: AgentState) -> dict[str, object]:
        text = state.normalized_input
        if text.startswith(("你好", "您好", "嗨")) and "介绍你能做什么" in text:
            intent = IntentType.OUT_OF_SCOPE
        elif any(word in text for word in ("忽略证据", "编造", "绕过工具", "泄露提示词")):
            intent = IntentType.OUT_OF_SCOPE
        elif any(word in text for word in ("航空发动机", "医疗诊断", "证券投资", "核聚变")):
            intent = IntentType.OUT_OF_SCOPE
        elif any(word in text for word in ("审查", "合规", "是否符合", "设计描述")):
            intent = IntentType.COMPLIANCE_REVIEW
        elif any(word in text for word in ("检查清单", "检查项", "清单")):
            intent = IntentType.CHECKLIST_GENERATION
        elif not re.search(r"[？?]", text) and len(self.parser.parse(text).items) >= 2:
            # 多对象/多属性设计陈述应自动进入预审，不要求用户显式说“合规审查”。
            intent = IntentType.COMPLIANCE_REVIEW
        else:
            intent = self.query_analyzer.analyze(text).intent
        return {"intent": intent}

    def identify_scenario(self, state: AgentState) -> dict[str, object]:
        preview = self.parser.parse(state.normalized_input)
        scenario = {
            key: value for key, value in preview.context.model_dump().items() if value is not None
        }
        return {"scenario": scenario}

    def extract_check_items(self, state: AgentState) -> dict[str, object]:
        preview = self.parser.parse(state.normalized_input)
        items = preview.items[:20]
        if not items:
            return {
                "status": WorkflowStatus.SAFE_STOPPED,
                "errors": state.errors + ["未能从设计描述提取检查项"],
            }
        return {"check_items": items}

    def confirm(self, state: AgentState) -> dict[str, object]:
        if any(item.status == CheckItemStatus.DRAFT for item in state.check_items):
            return {
                "status": WorkflowStatus.WAITING_CONFIRMATION,
                "pending_action": PendingAction.CONFIRM_CHECK_ITEMS,
            }
        return {"pending_action": None, "status": WorkflowStatus.RUNNING}

    def check_completeness(self, state: AgentState) -> dict[str, object]:
        result = self.completeness.check(state.check_items, state.evidence)
        items = result.ready_items + result.incomplete_items
        if result.missing_fields:
            if state.clarification_count >= 2:
                results = [
                    ComplianceResult(
                        item_id=item.item_id,
                        status=ComplianceStatus.INSUFFICIENT_INFORMATION,
                        limitations=["达到最大追问轮次，仍缺少判断条件"],
                    )
                    for item in result.incomplete_items
                ]
                return {
                    "check_items": items,
                    "compliance_results": results,
                    "status": WorkflowStatus.SAFE_STOPPED,
                    "pending_action": None,
                }
            request = self.clarifications.plan(result.missing_fields, state.session_id)
            return {
                "check_items": items,
                "clarification": request,
                "status": WorkflowStatus.WAITING_CLARIFICATION,
                "pending_action": PendingAction.ANSWER_CLARIFICATION,
            }
        return {
            "check_items": items,
            "clarification": None,
            "pending_action": None,
            "status": WorkflowStatus.RUNNING,
        }

    def plan_retrieval(self, state: AgentState) -> dict[str, object]:
        original = state.original_query or state.normalized_input
        previous = state.evidence_assessments[-1] if state.evidence_assessments else None
        plan = self.retrieval_planner.create(
            original,
            state.check_items,
            state.retrieval_retry_count,
            previous,
            state.query_history,
        )
        return {
            "original_query": original,
            "current_query": plan.query,
            "retrieval_plan": plan,
        }

    def analyze_question(self, state: AgentState) -> dict[str, object]:
        if not hasattr(self.qa, "agentic_planner"):
            answer, result = self.qa.ask(state.normalized_input)
            return {"answer": answer, "evidence": result.evidence}
        analysis = self.qa.agentic_planner.analyze_question(state.normalized_input)
        return {
            "question_analysis": analysis,
            "retrieval_goals": analysis.goals,
            "original_query": state.normalized_input,
        }

    def plan_qa_retrieval(self, state: AgentState) -> dict[str, object]:
        if state.question_analysis is None:
            raise ValueError("plan_qa_retrieval 缺少 QuestionAnalysis")
        previous = state.evidence_assessments[-1] if state.evidence_assessments else None
        plan = self.qa.agentic_planner.create(
            state.original_query,
            state.question_analysis,
            state.retrieval_step_count,
            previous,
            state.query_history,
        )
        return {"current_query": plan.query, "retrieval_plan": plan}

    def retrieve(self, state: AgentState) -> dict[str, object]:
        if state.retrieval_plan is None:
            raise ValueError("retrieve 缺少 RetrievalPlan")
        evidence = list(state.evidence)
        seen = {item.evidence_id for item in evidence}
        new_count = 0
        tool_calls = list(state.retrieval_tool_calls)
        queries = state.retrieval_plan.subqueries or [state.retrieval_plan.query]
        for query in queries:
            plan = state.retrieval_plan.model_copy(update={"query": query})
            if hasattr(self.qa, "search_tool"):
                result, tool_trace = self.qa.search_tool.execute(plan, seen)
                tool_calls.append(tool_trace)
            else:
                result = (
                    self.retrieval.execute(plan)
                    if hasattr(self.retrieval, "execute")
                    else self.retrieval.retrieve(query, plan.top_k)
                )
            for item in result.evidence:
                if item.evidence_id not in seen:
                    evidence.append(item)
                    seen.add(item.evidence_id)
                    new_count += 1
        attempt = RetrievalAttempt(
            attempt=state.retrieval_plan.attempt,
            original_query=state.original_query or state.normalized_input,
            queries=queries,
            routes=state.retrieval_plan.retrievers,
            top_k=state.retrieval_plan.top_k,
            filters=state.retrieval_plan.filters.model_dump(mode="json"),
            rewrite_reason=state.retrieval_plan.rewrite_reason,
            result_count=len(evidence),
            new_evidence_count=new_count,
        )
        return {
            "evidence": evidence,
            "query_history": state.query_history + queries,
            "retrieval_attempts": state.retrieval_attempts + [attempt],
            "retrieval_tool_calls": tool_calls,
        }

    def evaluate_qa_evidence(self, state: AgentState) -> dict[str, object]:
        if state.question_analysis is None or state.retrieval_plan is None:
            raise ValueError("evaluate_qa_evidence 缺少分析或检索计划")
        latest = state.retrieval_attempts[-1]
        valid_ids = {item.evidence_id for item in state.evidence}
        assessment = self.qa.evidence_evaluator.evaluate(
            state.question_analysis,
            state.retrieval_plan,
            state.evidence,
            valid_ids,
            latest.new_evidence_count,
        )
        updates: dict[str, object] = {
            "evidence_assessments": state.evidence_assessments + [assessment],
            "evidence_coverage": state.evidence_coverage + [assessment],
            "retrieval_step_count": state.retrieval_step_count + 1,
            "retrieval_goals": [
                goal.model_copy(
                    update={
                        "status": coverage.status,
                        "supporting_evidence_ids": coverage.supporting_evidence_ids,
                    }
                )
                for goal, coverage in zip(
                    state.question_analysis.goals, assessment.goal_assessments, strict=True
                )
            ],
        }
        if not assessment.is_sufficient and assessment.next_action in {
            "manual_review",
            "safe_stop",
        }:
            updates.update(
                status=WorkflowStatus.SAFE_STOPPED,
                errors=state.errors + [assessment.failure_reason or "证据不足"],
            )
        return updates

    def generate_qa_answer(self, state: AgentState) -> dict[str, object]:
        if state.retrieval_plan is None or state.question_analysis is None:
            raise ValueError("generate_qa_answer 缺少检索上下文")
        result = RetrievalResult(
            evidence=state.evidence,
            trace=RetrievalTrace(
                plan=state.retrieval_plan,
                query_analysis=self.query_analyzer.analyze(state.original_query),
            ),
        )
        answer = self.qa.answer_generator.generate(
            state.original_query,
            state.evidence,
            state.retrieval_goals,
            state.retrieval_tool_calls,
            result,
        )
        return {"answer": answer}

    def verify_qa_answer(self, state: AgentState) -> dict[str, object]:
        if state.answer is None:
            return {
                "status": WorkflowStatus.SAFE_STOPPED,
                "errors": state.errors + ["未生成问答结果"],
            }
        errors = self.qa.validator.validate(state.answer, state.evidence)
        if errors:
            return {"status": WorkflowStatus.SAFE_STOPPED, "errors": state.errors + errors}
        return {}

    def evaluate_evidence(self, state: AgentState) -> dict[str, object]:
        plan = state.retrieval_plan
        latest = state.retrieval_attempts[-1]
        direct = [item for item in state.evidence if item.context_reason is None]
        exact_missing = bool(
            plan
            and plan.exact_keys
            and not any("exact" in item.retrieval_sources for item in direct)
        )
        partial_table = bool(direct) and all(item.support_type != "SUFFICIENT" for item in direct)
        unsupported: list[str] = []
        missing: list[str] = []
        for check in state.check_items:
            terms = [check.object.casefold(), check.attribute.casefold()]
            if not any(all(term in item.content.casefold() for term in terms) for item in direct):
                unsupported.append(check.item_id)
                missing.append(f"{check.object}/{check.attribute}")
        sufficient = bool(direct) and not exact_missing and not partial_table and not unsupported
        if sufficient:
            assessment = EvidenceAssessment(is_sufficient=True, next_action="accept")
        elif exact_missing:
            assessment = EvidenceAssessment(
                is_sufficient=False,
                failure_reason="目标条款或表号精确检索未命中",
                missing_aspects=plan.exact_keys if plan else [],
                next_action="safe_stop",
            )
        elif partial_table:
            assessment = EvidenceAssessment(
                is_sufficient=False,
                failure_reason="表格未可靠结构化",
                missing_aspects=missing,
                unsupported_check_item_ids=unsupported,
                next_action="manual_review",
            )
        else:
            assessment = EvidenceAssessment(
                is_sufficient=False,
                failure_reason="检索结果未覆盖全部检查项" if direct else "未检索到直接证据",
                missing_aspects=missing,
                unsupported_check_item_ids=unsupported,
                suggested_query_terms=missing,
                next_action="expand_query" if state.retrieval_retry_count < 2 else "safe_stop",
            )
        attempts = state.retrieval_attempts[:-1] + [
            latest.model_copy(update={"assessment": assessment})
        ]
        assessments = state.evidence_assessments + [assessment]
        base: dict[str, object] = {
            "retrieval_attempts": attempts,
            "evidence_assessments": assessments,
        }
        if assessment.is_sufficient:
            return base
        if assessment.next_action == "manual_review":
            return {
                **base,
                "status": WorkflowStatus.SAFE_STOPPED,
                "errors": state.errors + [assessment.failure_reason or "需人工复核"],
            }
        if (
            assessment.next_action != "safe_stop"
            and state.retrieval_retry_count < 2
            and latest.new_evidence_count > 0
            or (
                assessment.next_action != "safe_stop"
                and state.retrieval_retry_count < 2
                and not state.retrieval_attempts[:-1]
            )
        ):
            return {**base, "retrieval_retry_count": state.retrieval_retry_count + 1}
        return {
            **base,
            "status": WorkflowStatus.SAFE_STOPPED,
            "retrieval_retry_count": min(2, state.retrieval_retry_count + 1),
            "errors": state.errors
            + [
                (assessment.failure_reason or "证据不足")
                + (
                    "；达到检索重试上限"
                    if state.retrieval_retry_count >= 2
                    else "；重试未新增 Evidence，检索重试上限已消耗"
                )
            ],
        }

    def extract_rule(self, state: AgentState) -> dict[str, object]:
        if state.rules:
            return {}
        evidence_ids = [item.evidence_id for item in state.evidence]
        if not evidence_ids:
            return {}
        if self.rule_repository:
            confirmed = [
                rule
                for item in state.check_items
                for rule in self.rule_repository.list_rules(item.object, item.attribute)
            ]
            if confirmed:
                return {"rules": list({rule.rule_id: rule for rule in confirmed}.values())}
            candidates = [
                self.rule_repository.save_candidate(candidate)
                for candidate in self.candidate_extractor.extract(state.evidence)
            ]
            return {
                "candidate_rules": candidates,
                "status": WorkflowStatus.WAITING_CONFIRMATION,
                "pending_action": PendingAction.REVIEW_CANDIDATE_RULES,
            }
        # 未经人工确认或结构化模型校验的证据不能自动变成确定性规则。
        rules = [
            ManualReviewRule(
                rule_id=f"manual_{item.item_id}",
                subject=item.object,
                attribute=item.attribute,
                reason="尚未建立并人工确认对应生产规则",
                requirement_level=RequirementLevel.SHALL,
                evidence_ids=evidence_ids[:3],
            )
            for item in state.check_items
        ]
        return {"rules": rules}

    def evaluate_compliance(self, state: AgentState) -> dict[str, object]:
        by_id = {rule.rule_id: rule for rule in state.rules}
        results = []
        for item in state.check_items:
            match = self.rule_matcher.match(item, state.rules)
            if match.status == "matched":
                results.append(self.evaluator.evaluate(item, by_id[match.matched_rule_ids[0]]))
            else:
                status = (
                    ComplianceStatus.INSUFFICIENT_INFORMATION
                    if match.status == "missing_condition"
                    else ComplianceStatus.MANUAL_REVIEW_REQUIRED
                )
                results.append(
                    ComplianceResult(
                        item_id=item.item_id,
                        status=status,
                        evidence_ids=list(
                            dict.fromkeys(
                                evidence_id
                                for rule_id in match.matched_rule_ids
                                for evidence_id in by_id[rule_id].evidence_ids
                            )
                        ),
                        limitations=[
                            f"规则匹配状态：{match.status}"
                            + (
                                f"；缺少条件：{','.join(match.missing_conditions)}"
                                if match.missing_conditions
                                else ""
                            )
                        ],
                    )
                )
        return {"compliance_results": results}

    def verify_claims(self, state: AgentState) -> dict[str, object]:
        evidence_ids = {item.evidence_id for item in state.evidence}
        invalid = [
            result.item_id
            for result in state.compliance_results
            if not set(result.evidence_ids).issubset(evidence_ids)
        ]
        if invalid and state.validation_retry_count < 1:
            return {"validation_retry_count": state.validation_retry_count + 1}
        if invalid:
            return {
                "status": WorkflowStatus.SAFE_STOPPED,
                "errors": state.errors + [f"结果引用验证失败：{invalid}"],
            }
        return {}

    def generate_report(self, state: AgentState) -> dict[str, object]:
        lines = ["# 规范预审报告", "", f"会话：`{state.session_id}`", "", "## 结果"]
        if state.answer:
            lines.extend(["", state.answer.answer_text])
        for item, result in zip(state.check_items, state.compliance_results, strict=False):
            lines.extend(
                [
                    "",
                    f"### {item.object} / {item.attribute}",
                    f"- 状态：{result.status}",
                    f"- 实际值：{result.actual or item.value or '未提供'} {item.unit or ''}",
                    f"- 规则要求：{result.required or '未形成确定性规则'}",
                    f"- 限制：{'；'.join(result.limitations) or '无'}",
                ]
            )
        # 问答的 answer_text 已在末尾附来源，避免报告重复；其他任务也必须以来源结尾。
        if state.evidence and not state.answer:
            lines.extend(["", "## 证据来源"])
            for evidence in state.evidence[:10]:
                citation = evidence.citation
                lines.append(
                    f"- {citation.standard_code}，条款 {citation.clause_id or '-'}，"
                    f"第 {citation.page_number} 页：{citation.quote[:120]}"
                )
        elif not state.answer:
            lines.extend(["", "## 证据来源", "", "无可用规范证据。"])
        return {
            "report_markdown": "\n".join(lines),
            "status": WorkflowStatus.COMPLETED,
            "pending_action": None,
        }

    def answer_query(self, state: AgentState) -> dict[str, object]:
        answer, result = self.qa.ask(state.normalized_input)
        return {"answer": answer, "evidence": result.evidence}

    def generate_checklist(self, state: AgentState) -> dict[str, object]:
        preview = self.parser.parse(state.normalized_input, auto_confirm=True)
        return {"check_items": preview.items}

    def out_of_scope(self, state: AgentState) -> dict[str, object]:
        return {
            "status": WorkflowStatus.SAFE_STOPPED,
            "errors": state.errors + ["请求超出当前三份油气管网规范范围"],
        }
