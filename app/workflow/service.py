import json
import secrets
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from app.domain.models import CheckItem, CheckItemStatus, IntentType
from app.memory.extractor import MemoryCandidateExtractor
from app.memory.models import (
    CandidateStatus,
    ExpressionMode,
    FactSourceType,
    FactValue,
    MemoryAction,
    MemoryActionResult,
)
from app.memory.repository import SQLiteMemoryRepository
from app.workflow.graph import WorkflowRunner
from app.workflow.models import (
    AgentState,
    PendingAction,
    WorkflowConfirmRequest,
    WorkflowMessage,
    WorkflowStatus,
)
from app.workflow.repository import SQLiteWorkflowRepository


class WorkflowService:
    def __init__(
        self,
        repository: SQLiteWorkflowRepository,
        runner: WorkflowRunner,
        memory_repository: SQLiteMemoryRepository | None = None,
        memory_extractor: MemoryCandidateExtractor | None = None,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.memory_repository = memory_repository
        self.memory_extractor = memory_extractor

    def create_session(
        self, user_id: str = "legacy_local", project_id: str | None = None
    ) -> AgentState:
        if project_id is not None:
            if self.memory_repository is None:
                raise ValueError("绑定项目需要 Project Memory Repository")
            self.memory_repository.get_project(user_id, project_id)
        now = datetime.now(UTC)
        state = AgentState(
            session_id=f"session_{secrets.token_hex(10)}",
            request_id=f"request_{secrets.token_hex(10)}",
            user_id=user_id,
            project_id=project_id,
            workflow_stage="project_resolved" if project_id else "new",
            project_summary=(
                self.memory_repository.project_summary(user_id, project_id)
                if project_id and self.memory_repository
                else None
            ),
            created_at=now,
            updated_at=now,
        )
        if project_id and self.memory_repository:
            pending = self.memory_repository.pending_question(user_id, project_id)
            if pending:
                state = state.model_copy(
                    update={
                        "pending_question_id": pending[0],
                        "clarification": pending[1],
                        "status": WorkflowStatus.WAITING_CLARIFICATION,
                        "pending_action": PendingAction.ANSWER_CLARIFICATION,
                        "workflow_stage": "restored_pending_question",
                    }
                )
        self.repository.save(state)
        return state

    def send_message(
        self,
        session_id: str,
        content: str,
        idempotency_key: str,
        user_id: str | None = None,
    ) -> AgentState:
        previous = self.repository.load(session_id, user_id)
        cached = self.repository.get_idempotent(session_id, idempotency_key)
        if cached:
            return cached
        now = datetime.now(UTC)
        state = previous.model_copy(
            update={
                "request_id": f"request_{secrets.token_hex(10)}",
                "status": WorkflowStatus.NEW,
                "current_node": "normalize_input",
                "input_text": content,
                "normalized_input": "",
                "intent": None,
                "check_items": [],
                "evidence": [],
                "evidence_by_check_item": {},
                "rules": [],
                "candidate_rules": [],
                "compliance_results": [],
                "compliance_judgements": [],
                "compliance_summary": None,
                "answer": None,
                "clarification": None,
                "pending_action": None,
                "report_markdown": None,
                "errors": [],
                "retrieval_retry_count": 0,
                "original_query": "",
                "current_query": "",
                "query_history": [],
                "retrieval_plan": None,
                "retrieval_attempts": [],
                "evidence_assessments": [],
                "question_analysis": None,
                "retrieval_goals": [],
                "retrieval_step_count": 0,
                "evidence_coverage": [],
                "retrieval_tool_calls": [],
                "validation_retry_count": 0,
                "workflow_stage": "processing_message",
                "memory_actions": [],
                "messages": previous.messages
                + [WorkflowMessage(role="user", content=content, created_at=now)],
                "updated_at": now,
            }
        )
        if state.project_id and self._is_review_explanation(content):
            state = self._explain_review(state, content)
            self.repository.save(state)
            self.repository.save_idempotent(session_id, idempotency_key, state)
            return state
        if state.project_id is not None:
            state = self._extract_project_memory(state, content, idempotency_key)
            if state.status in {WorkflowStatus.SAFE_STOPPED, WorkflowStatus.WAITING_CLARIFICATION}:
                state = self._refresh_project_summary(state)
                self.repository.save(state)
                self.repository.save_idempotent(session_id, idempotency_key, state)
                return state
        start_node = "plan_retrieval" if state.workflow_stage == "incremental_review" else None
        state = self.runner.run(state, start_node)
        state = self._persist_reviews(state)
        state = self._sync_pending_question(state)
        state = self._refresh_project_summary(state)
        self.repository.save(state)
        self.repository.save_idempotent(session_id, idempotency_key, state)
        return state

    def _persist_reviews(self, state: AgentState) -> AgentState:
        if (
            self.memory_repository is None
            or state.project_id is None
            or not state.compliance_results
            or state.workflow_stage == "hypothetical_review"
        ):
            return state
        run_type = "incremental" if state.workflow_stage == "incremental_review" else "full"
        run_id = self.memory_repository.save_reviews(
            state.user_id,
            state.project_id,
            state.session_id,
            state.normalized_input,
            list(state.check_items),
            list(state.compliance_results),
            state.evidence_by_check_item,
            run_type=run_type,
        )
        return state.model_copy(
            update={"active_review_run_id": run_id, "workflow_stage": "review_persisted"}
        )

    def _sync_pending_question(self, state: AgentState) -> AgentState:
        if (
            self.memory_repository is None
            or state.project_id is None
            or state.clarification is None
            or state.pending_action != PendingAction.ANSWER_CLARIFICATION
        ):
            return state
        question_id = self.memory_repository.save_pending_question(
            state.user_id, state.project_id, state.session_id, state.clarification
        )
        return state.model_copy(update={"pending_question_id": question_id})

    def _refresh_project_summary(self, state: AgentState) -> AgentState:
        if self.memory_repository is None or state.project_id is None:
            return state
        return state.model_copy(
            update={
                "project_summary": self.memory_repository.project_summary(
                    state.user_id, state.project_id
                )
            }
        )

    def _explain_review(self, state: AgentState, content: str) -> AgentState:
        assert self.memory_repository is not None and state.project_id is not None
        reviews = self.memory_repository.list_reviews(state.user_id, state.project_id)
        selected = next(
            (
                row
                for row in reviews
                if any(part and part in content.lower() for part in row.semantic_key.split("|"))
            ),
            reviews[0] if reviews else None,
        )
        if selected is None:
            report = "当前项目还没有可解释的历史审查记录。"
        else:
            report = "\n".join(
                [
                    "# 历史审查解释",
                    "",
                    f"- 检查项：{selected.semantic_key}",
                    f"- 当前状态：{selected.status}",
                    f"- 判断：{selected.judgement}",
                    f"- 实际值：{selected.actual or '未记录'}",
                    f"- 规范要求：{selected.required or '未记录'}",
                    f"- 判断理由：{selected.reason or '未记录'}",
                    f"- Evidence：{', '.join(selected.evidence_ids) or '无'}",
                ]
            )
        return state.model_copy(
            update={
                "report_markdown": report,
                "status": WorkflowStatus.COMPLETED,
                "workflow_stage": "review_explanation",
            }
        )

    @staticmethod
    def _is_review_explanation(content: str) -> bool:
        return "为什么" in content and any(word in content for word in ("判断", "合规", "不合规"))

    def _extract_project_memory(
        self, state: AgentState, content: str, idempotency_key: str
    ) -> AgentState:
        if self.memory_repository is None or self.memory_extractor is None:
            return state.model_copy(
                update={
                    "status": WorkflowStatus.SAFE_STOPPED,
                    "workflow_stage": "memory_extraction_unavailable",
                    "errors": state.errors
                    + ["MEMORY_EXTRACTION_UNAVAILABLE：项目消息需要配置聊天模型"],
                }
            )
        assert state.project_id is not None
        source_message_id = f"message_{idempotency_key}"
        try:
            extracted = self.memory_extractor.extract(
                content,
                self.memory_repository.list_facts(state.user_id, state.project_id),
            )
        except Exception as exc:
            self.memory_repository.save_candidate(
                state.user_id,
                state.project_id,
                state.session_id,
                source_message_id,
                content,
                action=None,
                fact_key=None,
                value=None,
                unit=None,
                confidence=None,
                expression_mode=None,
                status=CandidateStatus.FAILED,
                failure_reason=str(exc)[:500],
            )
            return state.model_copy(
                update={
                    "status": WorkflowStatus.SAFE_STOPPED,
                    "workflow_stage": "memory_extraction_failed",
                    "errors": state.errors + ["MEMORY_EXTRACTION_UNAVAILABLE：事实候选提取失败"],
                }
            )

        results: list[MemoryActionResult] = []
        requires_confirmation = False
        hypothesis: dict[str, FactValue] | None = None
        hypothesis_items: list[CheckItem] = []
        for index, candidate in enumerate(extracted.candidates):
            pending = (
                candidate.expression_mode != ExpressionMode.EXPLICIT
                or candidate.requires_confirmation
                or candidate.confidence < 0.9
                or candidate.fact_key is None
                or candidate.value is None
            )
            status = CandidateStatus.PENDING if pending else CandidateStatus.APPLIED
            fact_id: str | None = None
            if not pending and candidate.action != MemoryAction.NOOP:
                assert candidate.fact_key is not None
                assert candidate.value is not None
                current = {
                    item.fact_key: item
                    for item in self.memory_repository.list_facts(state.user_id, state.project_id)
                }.get(candidate.fact_key)
                key = f"{idempotency_key}-{index:02d}"
                if current is None:
                    fact = self.memory_repository.create_fact(
                        state.user_id,
                        state.project_id,
                        candidate.fact_key,
                        candidate.value,
                        candidate.unit,
                        key,
                        source_type=FactSourceType.MODEL_EXTRACTED,
                        source_message_id=source_message_id,
                        confidence=candidate.confidence,
                    )
                else:
                    fact = self.memory_repository.update_fact(
                        state.user_id,
                        state.project_id,
                        current.fact_id,
                        candidate.value,
                        candidate.unit,
                        current.version,
                        key,
                        source_type=FactSourceType.MODEL_EXTRACTED,
                        source_message_id=source_message_id,
                        confidence=candidate.confidence,
                    )
                fact_id = fact.fact_id
            elif candidate.action == MemoryAction.NOOP:
                status = CandidateStatus.REJECTED
            if (
                candidate.expression_mode == ExpressionMode.HYPOTHETICAL
                and candidate.fact_key
                and candidate.value is not None
            ):
                current = {
                    row.fact_key: row
                    for row in self.memory_repository.list_facts(state.user_id, state.project_id)
                }.get(candidate.fact_key)
                if current:
                    hypothesis = {
                        "fact_key": candidate.fact_key,
                        "value": candidate.value,
                        "unit": candidate.unit or "",
                        "base_fact_id": current.fact_id,
                    }
                    hypothesis_items.extend(
                        item.model_copy(update={"value": candidate.value, "unit": candidate.unit})
                        for item in self.memory_repository.check_items_for_fact(
                            state.user_id, state.project_id, current.fact_id
                        )
                    )
            candidate_id = self.memory_repository.save_candidate(
                state.user_id,
                state.project_id,
                state.session_id,
                source_message_id,
                content,
                action=candidate.action,
                fact_key=candidate.fact_key,
                value=candidate.value,
                unit=candidate.unit,
                confidence=candidate.confidence,
                expression_mode=candidate.expression_mode,
                status=status,
                failure_reason=None,
                fact_id=fact_id,
            )
            results.append(
                MemoryActionResult(
                    candidate_id=candidate_id,
                    action=candidate.action,
                    status=status,
                    fact_key=candidate.fact_key,
                    fact_id=fact_id,
                    reason=candidate.reason,
                )
            )
            requires_confirmation = requires_confirmation or (
                status == CandidateStatus.PENDING
                and candidate.expression_mode != ExpressionMode.HYPOTHETICAL
            )
        if requires_confirmation:
            return state.model_copy(
                update={
                    "memory_actions": results,
                    "status": WorkflowStatus.WAITING_CLARIFICATION,
                    "pending_action": PendingAction.CONFIRM_MEMORY,
                    "workflow_stage": "waiting_memory_confirmation",
                }
            )
        if hypothesis and hypothesis_items:
            return state.model_copy(
                update={
                    "memory_actions": results,
                    "pending_hypothesis": hypothesis,
                    "check_items": hypothesis_items,
                    "intent": IntentType.COMPLIANCE_REVIEW,
                    "status": WorkflowStatus.RUNNING,
                    "workflow_stage": "hypothetical_review",
                    "current_node": "plan_retrieval",
                }
            )
        stale_items = self.memory_repository.stale_check_items(state.user_id, state.project_id)
        if stale_items:
            return state.model_copy(
                update={
                    "memory_actions": results,
                    "check_items": stale_items,
                    "intent": IntentType.COMPLIANCE_REVIEW,
                    "status": WorkflowStatus.RUNNING,
                    "workflow_stage": "incremental_review",
                    "current_node": "plan_retrieval",
                }
            )
        return state.model_copy(
            update={"memory_actions": results, "workflow_stage": "memory_resolved"}
        )

    def confirm(
        self, session_id: str, request: WorkflowConfirmRequest, user_id: str | None = None
    ) -> AgentState:
        state = self.repository.load(session_id, user_id)
        items = [
            item.model_copy(update={"status": CheckItemStatus.CONFIRMED}) for item in request.items
        ]
        state = state.model_copy(
            update={
                "check_items": items,
                "rules": request.rules,
                "status": WorkflowStatus.RUNNING,
                "pending_action": None,
            }
        )
        state = self.runner.run(state, "check_completeness")
        state = self._persist_reviews(state)
        state = self._sync_pending_question(state)
        state = self._refresh_project_summary(state)
        self.repository.save(state)
        return state

    def confirm_memory(
        self, session_id: str, approved: bool, user_id: str | None = None
    ) -> AgentState:
        state = self.repository.load(session_id, user_id)
        if (
            self.memory_repository is None
            or state.project_id is None
            or state.pending_action != PendingAction.CONFIRM_MEMORY
        ):
            return state
        candidates = self.memory_repository.pending_candidates(
            state.user_id, state.project_id, state.session_id
        )
        for candidate in candidates:
            candidate_id = str(candidate["candidate_id"])
            if not approved:
                self.memory_repository.resolve_candidate(
                    state.user_id, state.project_id, candidate_id, CandidateStatus.REJECTED, None
                )
                continue
            fact_key = candidate["fact_key"]
            value_json = candidate["candidate_value_json"]
            if not isinstance(fact_key, str) or not isinstance(value_json, str):
                continue
            value = json.loads(value_json)
            raw_unit = candidate.get("unit")
            unit = raw_unit if isinstance(raw_unit, str) else None
            active = {
                row.fact_key: row
                for row in self.memory_repository.list_facts(state.user_id, state.project_id)
            }.get(fact_key)
            key = f"confirm-{candidate_id}"
            if active:
                fact = self.memory_repository.update_fact(
                    state.user_id,
                    state.project_id,
                    active.fact_id,
                    value,
                    unit,
                    active.version,
                    key,
                    source_type=FactSourceType.USER_CONFIRMED,
                )
            else:
                fact = self.memory_repository.create_fact(
                    state.user_id,
                    state.project_id,
                    fact_key,
                    value,
                    unit,
                    key,
                    source_type=FactSourceType.USER_CONFIRMED,
                )
            self.memory_repository.resolve_candidate(
                state.user_id,
                state.project_id,
                candidate_id,
                CandidateStatus.APPLIED,
                fact.fact_id,
            )
        if not approved:
            stopped = state.model_copy(
                update={
                    "status": WorkflowStatus.SAFE_STOPPED,
                    "pending_action": None,
                    "workflow_stage": "memory_rejected",
                }
            )
            stopped = self._refresh_project_summary(stopped)
            self.repository.save(stopped)
            return stopped
        resumed = state.model_copy(
            update={
                "status": WorkflowStatus.RUNNING,
                "pending_action": None,
                "workflow_stage": "memory_confirmed",
            }
        )
        resumed = self.runner.run(resumed, "normalize_input")
        resumed = self._persist_reviews(resumed)
        resumed = self._sync_pending_question(resumed)
        resumed = self._refresh_project_summary(resumed)
        self.repository.save(resumed)
        return resumed

    def clarify(
        self,
        session_id: str,
        answers: dict[str, str | int | float | bool],
        user_id: str | None = None,
    ) -> AgentState:
        state = self.repository.load(session_id, user_id)
        if not state.clarification:
            return state
        if self.memory_repository and state.project_id:
            current = {
                row.fact_key: row
                for row in self.memory_repository.list_facts(state.user_id, state.project_id)
            }
            for field, value in answers.items():
                key = f"clarify-{state.session_id}-{state.clarification_count}-{field}"
                existing = current.get(field)
                if existing:
                    self.memory_repository.update_fact(
                        state.user_id,
                        state.project_id,
                        existing.fact_id,
                        value,
                        None,
                        existing.version,
                        key,
                        source_type=FactSourceType.USER_CONFIRMED,
                    )
                else:
                    self.memory_repository.create_fact(
                        state.user_id,
                        state.project_id,
                        field,
                        value,
                        None,
                        key,
                        source_type=FactSourceType.USER_CONFIRMED,
                    )
            if state.pending_question_id:
                self.memory_repository.resolve_pending_question(
                    state.user_id, state.project_id, state.pending_question_id
                )
        items = {item.item_id: item for item in state.check_items}
        for missing in state.clarification.requested_fields:
            if missing.field_name not in answers:
                continue
            for item_id in missing.related_item_ids:
                if item_id in items:
                    items[item_id] = self._update(
                        items[item_id], missing.field_name, answers[missing.field_name]
                    )
        state = state.model_copy(
            update={
                "check_items": list(items.values()),
                "clarification": None,
                "clarification_count": state.clarification_count + 1,
                "status": WorkflowStatus.RUNNING,
                "pending_action": None,
                "pending_question_id": None,
            }
        )
        state = self.runner.run(state, "check_completeness")
        state = self._persist_reviews(state)
        state = self._sync_pending_question(state)
        state = self._refresh_project_summary(state)
        self.repository.save(state)
        return state

    def replay(
        self, session_id: str, start_node: str | None = None, user_id: str | None = None
    ) -> AgentState:
        state = self.repository.load(session_id, user_id)
        replayed = self.runner.run(state, start_node or state.current_node)
        self.repository.save(replayed)
        return replayed

    @staticmethod
    def _update(item: CheckItem, field: str, value: str | int | float | bool) -> CheckItem:
        if field == "value":
            try:
                converted: object = Decimal(str(value))
            except InvalidOperation:
                converted = value
            return item.model_copy(update={"value": converted, "user_corrected": True})
        if field in {"object", "unit", "location", "condition", "relation"}:
            return item.model_copy(update={field: str(value), "user_corrected": True})
        additional = dict(item.additional_fields)
        additional[field] = value if not isinstance(value, float) else Decimal(str(value))
        return item.model_copy(update={"additional_fields": additional, "user_corrected": True})
