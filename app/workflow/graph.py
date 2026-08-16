import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.domain.models import IntentType, TraceEvent
from app.workflow.models import AgentState, WorkflowStatus
from app.workflow.nodes import WorkflowNodes
from app.workflow.routes import (
    route_after_identification,
    route_after_qa_analysis,
    route_after_rule_extraction,
    route_completeness,
    route_confirmation,
    route_evidence,
    route_intent,
    route_qa_evidence,
    route_qa_verification,
    route_verification,
)

Node = Callable[[AgentState], dict[str, object]]


class WorkflowRunner:
    """与 LangGraph 节点语义一致的本地执行器，用于未安装依赖时运行和测试。"""

    def __init__(self, nodes: WorkflowNodes, max_steps: int = 40) -> None:
        self.nodes = nodes
        self.max_steps = max_steps
        self.node_map: dict[str, Node] = {
            name: getattr(nodes, name)
            for name in (
                "normalize_input",
                "classify_intent",
                "identify_scenario",
                "extract_check_items",
                "confirm",
                "check_completeness",
                "plan_retrieval",
                "retrieve",
                "evaluate_evidence",
                "judge_compliance",
                "extract_rule",
                "evaluate_compliance",
                "verify_claims",
                "generate_report",
                "answer_query",
                "analyze_question",
                "plan_qa_retrieval",
                "evaluate_qa_evidence",
                "generate_qa_answer",
                "verify_qa_answer",
                "generate_checklist",
                "out_of_scope",
            )
        }

    def run(self, state: AgentState, start_node: str | None = None) -> AgentState:
        node_name = start_node or state.current_node
        for _step in range(self.max_steps):
            if node_name == "pause":
                return state
            state = self._execute(state, node_name)
            node_name = self._next(node_name, state)
            state = state.model_copy(update={"current_node": node_name})
            if node_name == "end":
                return state
        return state.model_copy(
            update={
                "status": WorkflowStatus.SAFE_STOPPED,
                "errors": state.errors + ["达到工作流最大步数，已安全停止"],
            }
        )

    def _execute(self, state: AgentState, node_name: str) -> AgentState:
        started = time.perf_counter()
        status = "success"
        try:
            update = self.node_map[node_name](state)
        except Exception as exc:
            update = {
                "status": WorkflowStatus.FAILED,
                "errors": state.errors + [f"{node_name}: {exc}"],
            }
            status = "error"
        duration = (time.perf_counter() - started) * 1000
        trace = TraceEvent(
            timestamp=datetime.now(UTC),
            request_id=state.request_id,
            session_id=state.session_id,
            module=node_name,
            duration_ms=duration,
            status=status,
            summary=f"局部更新字段：{','.join(update)}",
        )
        update["workflow_trace"] = state.workflow_trace + [trace]
        update["updated_at"] = datetime.now(UTC)
        return state.model_copy(update=update)

    @staticmethod
    def _next(node: str, state: AgentState) -> str:
        if state.status == WorkflowStatus.FAILED:
            return "end"
        fixed = {
            "normalize_input": "classify_intent",
            "classify_intent": route_intent(state),
            "analyze_question": route_after_qa_analysis(state),
            "plan_qa_retrieval": "retrieve",
            "identify_scenario": route_after_identification(state),
            "extract_check_items": "confirm",
            "confirm": route_confirmation(state),
            "check_completeness": route_completeness(state),
            "plan_retrieval": "retrieve",
            "retrieve": (
                "evaluate_qa_evidence"
                if state.intent
                in {IntentType.KNOWLEDGE_QA, IntentType.CLAUSE_LOOKUP, IntentType.TABLE_LOOKUP}
                else "evaluate_evidence"
            ),
            "evaluate_qa_evidence": route_qa_evidence(state),
            "generate_qa_answer": "verify_qa_answer",
            "verify_qa_answer": route_qa_verification(state),
            "evaluate_evidence": route_evidence(state),
            "judge_compliance": "evaluate_compliance",
            "extract_rule": route_after_rule_extraction(state),
            "evaluate_compliance": "verify_claims",
            "verify_claims": route_verification(state),
            "answer_query": "generate_report",
            "generate_checklist": "generate_report",
            "generate_report": "end",
            "out_of_scope": "end",
        }
        return fixed[node]


def build_langgraph(nodes: WorkflowNodes, checkpoint_path: Path | None = None) -> object:
    """安装 requirements-agent.txt 后构建原生 StateGraph；业务节点保持同一实现。"""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RuntimeError("未安装 langgraph，请安装 requirements-agent.txt") from exc
    graph = StateGraph(AgentState)
    runner = WorkflowRunner(nodes)
    for name, function in runner.node_map.items():
        graph.add_node(name, function)
    graph.set_entry_point("normalize_input")
    graph.add_edge("normalize_input", "classify_intent")
    graph.add_conditional_edges("classify_intent", route_intent)
    graph.add_conditional_edges(
        "analyze_question",
        route_after_qa_analysis,
        {"plan_qa_retrieval": "plan_qa_retrieval", "generate_report": "generate_report"},
    )
    graph.add_edge("plan_qa_retrieval", "retrieve")
    graph.add_conditional_edges("identify_scenario", route_after_identification)
    graph.add_edge("extract_check_items", "confirm")
    graph.add_conditional_edges(
        "confirm", route_confirmation, {"check_completeness": "check_completeness", "pause": END}
    )
    graph.add_conditional_edges(
        "check_completeness", route_completeness, {"plan_retrieval": "plan_retrieval", "pause": END}
    )
    graph.add_edge("plan_retrieval", "retrieve")
    graph.add_conditional_edges(
        "retrieve",
        lambda state: (
            "evaluate_qa_evidence"
            if state.intent
            in {IntentType.KNOWLEDGE_QA, IntentType.CLAUSE_LOOKUP, IntentType.TABLE_LOOKUP}
            else "evaluate_evidence"
        ),
    )
    graph.add_conditional_edges(
        "evaluate_qa_evidence",
        route_qa_evidence,
        {
            "plan_qa_retrieval": "plan_qa_retrieval",
            "generate_qa_answer": "generate_qa_answer",
            "pause": END,
        },
    )
    graph.add_edge("generate_qa_answer", "verify_qa_answer")
    graph.add_conditional_edges(
        "verify_qa_answer",
        route_qa_verification,
        {"generate_report": "generate_report", "pause": END},
    )
    graph.add_conditional_edges(
        "evaluate_evidence",
        route_evidence,
        {
            "plan_retrieval": "plan_retrieval",
            "judge_compliance": "judge_compliance",
            "pause": END,
        },
    )
    graph.add_edge("judge_compliance", "evaluate_compliance")
    graph.add_conditional_edges(
        "extract_rule",
        route_after_rule_extraction,
        {"evaluate_compliance": "evaluate_compliance", "pause": END},
    )
    graph.add_edge("evaluate_compliance", "verify_claims")
    graph.add_conditional_edges(
        "verify_claims",
        route_verification,
        {
            "evaluate_compliance": "evaluate_compliance",
            "generate_report": "generate_report",
            "pause": END,
        },
    )
    graph.add_edge("answer_query", "generate_report")
    graph.add_edge("generate_checklist", "generate_report")
    graph.add_edge("generate_report", END)
    graph.add_edge("out_of_scope", END)
    if checkpoint_path is None:
        return graph.compile()
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    return graph.compile(checkpointer=SqliteSaver(connection))


def graph_mermaid() -> str:
    return """flowchart TD
 input[normalize_input] --> intent[classify_intent]
 intent -->|QA/条款/表格| analyze[analyze_question]
 analyze --> qaplan[plan_qa_retrieval] --> qaret[retrieve]
 qaret --> qaeval{evaluate_qa_evidence}
 qaeval -->|retry| qaplan
 qaeval -->|sufficient| qaanswer[generate_qa_answer --> verify_qa_answer]
 intent -->|清单/预审| scenario[identify_scenario]
 scenario --> items[extract_check_items] --> confirm{confirm}
 confirm -->|等待人工| pause1([pause])
 confirm --> complete{check_completeness}
 complete -->|缺字段| pause2([pause])
 complete --> retrieve[plan_retrieval → retrieve]
 retrieve --> evidence{evaluate_evidence}
 evidence -->|有限重试| retrieve
 evidence --> judge[judge_compliance → evaluate_compliance]
 judge --> verify{verify_claims}
 verify -->|最多1次| rules
 verify --> report[generate_report]"""
