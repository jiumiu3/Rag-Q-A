from app.domain.models import IntentType
from app.workflow.models import AgentState, WorkflowStatus


def route_intent(state: AgentState) -> str:
    routes = {
        IntentType.KNOWLEDGE_QA: "answer_query",
        IntentType.CLAUSE_LOOKUP: "answer_query",
        IntentType.TABLE_LOOKUP: "answer_query",
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
    return "retrieve" if not state.evidence and state.retrieval_retry_count else "extract_rule"


def route_verification(state: AgentState) -> str:
    if state.status == WorkflowStatus.SAFE_STOPPED:
        return "pause"
    return "evaluate_compliance" if state.validation_retry_count else "generate_report"
