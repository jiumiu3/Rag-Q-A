import hashlib
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.clarification.models import SessionState
from app.core.exceptions import AgentStateError


class SQLiteSessionRepository:
    """恢复令牌只保存 SHA256，数据库泄露时不能直接恢复会话。"""

    def __init__(self, path: Path, ttl_minutes: int = 60) -> None:
        self.path = path
        self.ttl_minutes = ttl_minutes

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS clarification_sessions (
                session_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
                state_json TEXT NOT NULL, expires_at TEXT NOT NULL)"""
            )

    def create(self, state: SessionState) -> tuple[SessionState, str]:
        self.initialize()
        token = secrets.token_urlsafe(32)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO clarification_sessions VALUES(?,?,?,?)",
                (
                    state.session_id,
                    self._hash(token),
                    state.model_dump_json(),
                    state.expires_at.isoformat(),
                ),
            )
        return state, token

    def save(self, state: SessionState, token: str) -> None:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            updated = connection.execute(
                "UPDATE clarification_sessions SET state_json=?,expires_at=? "
                "WHERE session_id=? AND token_hash=?",
                (
                    state.model_dump_json(),
                    state.expires_at.isoformat(),
                    state.session_id,
                    self._hash(token),
                ),
            ).rowcount
        if not updated:
            raise AgentStateError("session_id 或 resume_token 无效")

    def load(self, session_id: str, token: str) -> SessionState:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT state_json,expires_at,token_hash FROM clarification_sessions "
                "WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if not row or not secrets.compare_digest(row[2], self._hash(token)):
            raise AgentStateError("session_id 或 resume_token 无效")
        if datetime.fromisoformat(row[1]) <= datetime.now(UTC):
            raise AgentStateError("追问会话已过期")
        return SessionState.model_validate_json(row[0])

    def new_expiry(self) -> datetime:
        return datetime.now(UTC) + timedelta(minutes=self.ttl_minutes)

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
