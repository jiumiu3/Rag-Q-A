from app.domain.models import IntentType
from app.workflow.models import AgentState, PendingAction, WorkflowStatus


def route_intent(state: AgentState) -> str:
    routes = {
        IntentType.KNOWLEDGE_QA: "analyze_question",
        IntentType.CLAUSE_LOOKUP: "analyze_question",
        IntentType.TABLE_LOOKUP: "analyze_question",
        IntentType.CHECKLIST_GENERATION: "identify_scenario",
        IntentType.COMPLIANCE_REVIEW: "identify_scenario",
        IntentType.OUT_OF_SCOPE: "out_of_scope",
    }
    return routes[state.intent] if state.intent is not None else "out_of_scope"


def route_after_identification(state: AgentState) -> str:
    return (
        "generate_checklist"
        if state.intent == IntentType.CHECKLIST_GENERATION
        else "extract_check_items"
    )


def route_confirmation(state: AgentState) -> str:
    return "pause" if state.status == WorkflowStatus.WAITING_CONFIRMATION else "check_completeness"


def route_completeness(state: AgentState) -> str:
    if state.status in {WorkflowStatus.WAITING_CLARIFICATION, WorkflowStatus.SAFE_STOPPED}:
        return "pause"
    return "plan_retrieval"


def route_evidence(state: AgentState) -> str:
    if state.status == WorkflowStatus.SAFE_STOPPED:
        return "pause"
    if state.evidence_assessments and state.evidence_assessments[-1].is_sufficient:
        return "judge_compliance"
    return "plan_retrieval"


def route_verification(state: AgentState) -> str:
    if state.status == WorkflowStatus.SAFE_STOPPED:
        return "pause"
    return "evaluate_compliance" if state.validation_retry_count else "generate_report"


def route_after_rule_extraction(state: AgentState) -> str:
    return (
        "pause"
        if state.pending_action == PendingAction.REVIEW_CANDIDATE_RULES
        else "evaluate_compliance"
    )


def route_qa_evidence(state: AgentState) -> str:
    if not state.evidence_assessments:
        return "pause"
    assessment = state.evidence_assessments[-1]
    if assessment.is_sufficient:
        return "generate_qa_answer"
    if assessment.next_action in {"manual_review", "safe_stop"}:
        return "pause"
    return "plan_qa_retrieval"


def route_qa_verification(state: AgentState) -> str:
    return "pause" if state.status == WorkflowStatus.SAFE_STOPPED else "generate_report"


def route_after_qa_analysis(state: AgentState) -> str:
    return "generate_report" if state.answer is not None else "plan_qa_retrieval"
