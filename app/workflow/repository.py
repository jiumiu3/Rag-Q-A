import sqlite3
from pathlib import Path

from app.core.exceptions import AgentStateError
from app.workflow.models import AgentState


class SQLiteWorkflowRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.executescript(
                """CREATE TABLE IF NOT EXISTS workflow_sessions (
                session_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_idempotency (
                session_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                state_json TEXT NOT NULL, PRIMARY KEY(session_id,idempotency_key));"""
            )

    def save(self, state: AgentState) -> None:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO workflow_sessions VALUES(?,?,?)",
                (state.session_id, state.model_dump_json(), state.updated_at.isoformat()),
            )

    def load(self, session_id: str) -> AgentState:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT state_json FROM workflow_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        if not row:
            raise AgentStateError("工作流会话不存在", details={"session_id": session_id})
        return AgentState.model_validate_json(row[0])

    def get_idempotent(self, session_id: str, key: str) -> AgentState | None:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT state_json FROM workflow_idempotency "
                "WHERE session_id=? AND idempotency_key=?",
                (session_id, key),
            ).fetchone()
        return AgentState.model_validate_json(row[0]) if row else None

    def save_idempotent(self, session_id: str, key: str, state: AgentState) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO workflow_idempotency VALUES(?,?,?)",
                (session_id, key, state.model_dump_json()),
            )

    def list_sessions(self) -> list[str]:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT session_id FROM workflow_sessions ORDER BY updated_at DESC"
                )
            ]
