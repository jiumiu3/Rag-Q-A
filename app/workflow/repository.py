import sqlite3
from pathlib import Path

from app.core.exceptions import SessionNotFoundError
from app.workflow.models import AgentState


class SQLiteWorkflowRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.executescript(
                """CREATE TABLE IF NOT EXISTS workflow_sessions (
                session_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                user_id TEXT, project_id TEXT, workflow_stage TEXT);
                CREATE TABLE IF NOT EXISTS workflow_idempotency (
                session_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                state_json TEXT NOT NULL, PRIMARY KEY(session_id,idempotency_key));"""
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_sessions)")}
            for name in ("user_id", "project_id", "workflow_stage"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE workflow_sessions ADD COLUMN {name} TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_sessions_owner_project "
                "ON workflow_sessions(user_id,project_id,updated_at DESC)"
            )

    def save(self, state: AgentState) -> None:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO workflow_sessions "
                "(session_id,state_json,updated_at,user_id,project_id,workflow_stage) "
                "VALUES(?,?,?,?,?,?)",
                (
                    state.session_id,
                    state.model_dump_json(),
                    state.updated_at.isoformat(),
                    state.user_id,
                    state.project_id,
                    state.workflow_stage,
                ),
            )

    def load(self, session_id: str, user_id: str | None = None) -> AgentState:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            condition = " AND user_id=?" if user_id is not None else ""
            parameters = (session_id, user_id) if user_id is not None else (session_id,)
            row = connection.execute(
                "SELECT state_json FROM workflow_sessions WHERE session_id=?" + condition,
                parameters,
            ).fetchone()
        if not row:
            raise SessionNotFoundError("工作流会话不存在", details={"session_id": session_id})
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

    def list_sessions(self, user_id: str | None = None, project_id: str | None = None) -> list[str]:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            conditions: list[str] = []
            parameters: list[str] = []
            if user_id is not None:
                conditions.append("user_id=?")
                parameters.append(user_id)
            if project_id is not None:
                conditions.append("project_id=?")
                parameters.append(project_id)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            return [
                row[0]
                for row in connection.execute(
                    "SELECT session_id FROM workflow_sessions"
                    + where
                    + " ORDER BY updated_at DESC",
                    parameters,
                )
            ]
