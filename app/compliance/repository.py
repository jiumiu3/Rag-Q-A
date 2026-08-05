import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.compliance.models import CandidateRule, CandidateRuleStatus, ExecutableRule

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_rules (
 candidate_rule_id TEXT PRIMARY KEY, status TEXT NOT NULL, version INTEGER NOT NULL,
 standard_code TEXT NOT NULL, clause_id TEXT, table_id TEXT, subject TEXT NOT NULL,
 attribute TEXT NOT NULL, candidate_json TEXT NOT NULL, original_json TEXT NOT NULL,
 created_at TEXT NOT NULL, reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidate_lookup
 ON candidate_rules(status, subject, attribute, standard_code, clause_id);
CREATE TABLE IF NOT EXISTS candidate_rule_reviews (
 review_id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_rule_id TEXT NOT NULL,
 version INTEGER NOT NULL, action TEXT NOT NULL, reviewed_by TEXT NOT NULL,
 review_comment TEXT, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
 reviewed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS executable_rules (
 rule_id TEXT PRIMARY KEY, candidate_rule_id TEXT NOT NULL, rule_json TEXT NOT NULL,
 subject TEXT NOT NULL, attribute TEXT NOT NULL, standard_code TEXT NOT NULL,
 clause_id TEXT, version INTEGER NOT NULL, confirmed_at TEXT NOT NULL
);
"""


class SQLiteRuleRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(SCHEMA)

    def save_candidate(self, candidate: CandidateRule) -> CandidateRule:
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT candidate_json FROM candidate_rules WHERE candidate_rule_id=?",
                (candidate.candidate_rule_id,),
            ).fetchone()
            if existing:
                return CandidateRule.model_validate_json(existing[0])
            payload = candidate.model_dump_json()
            connection.execute(
                "INSERT INTO candidate_rules VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    candidate.candidate_rule_id,
                    candidate.status,
                    candidate.version,
                    candidate.standard_code,
                    candidate.clause_id,
                    candidate.table_id,
                    candidate.subject,
                    candidate.attribute,
                    payload,
                    payload,
                    candidate.created_at.isoformat(),
                    None,
                ),
            )
        return candidate

    def get_candidate(self, candidate_rule_id: str) -> CandidateRule | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT candidate_json FROM candidate_rules WHERE candidate_rule_id=?",
                (candidate_rule_id,),
            ).fetchone()
        return CandidateRule.model_validate_json(row[0]) if row else None

    def list_candidates(self, status: CandidateRuleStatus | None = None) -> list[CandidateRule]:
        query = "SELECT candidate_json FROM candidate_rules"
        args: tuple[object, ...] = ()
        if status:
            query, args = query + " WHERE status=?", (status,)
        with self._connection() as connection:
            rows = connection.execute(query + " ORDER BY created_at DESC", args).fetchall()
        return [CandidateRule.model_validate_json(row[0]) for row in rows]

    def review(
        self,
        candidate_rule_id: str,
        action: CandidateRuleStatus,
        reviewed_by: str,
        comment: str | None = None,
        changes: dict[str, object] | None = None,
    ) -> CandidateRule:
        current = self.get_candidate(candidate_rule_id)
        if not current:
            raise KeyError(candidate_rule_id)
        if current.status != CandidateRuleStatus.PENDING_REVIEW:
            raise ValueError("只有 pending_review 候选规则可以审核")
        now = datetime.now(UTC)
        allowed = set(CandidateRule.model_fields) - {
            "candidate_rule_id",
            "status",
            "version",
            "created_at",
            "reviewed_at",
            "reviewed_by",
            "review_comment",
            "evidence_ids",
            "standard_code",
            "clause_id",
            "table_id",
            "source_text",
        }
        safe_changes = {key: value for key, value in (changes or {}).items() if key in allowed}
        updated = current.model_copy(
            update={
                **safe_changes,
                "status": action,
                "version": current.version + 1,
                "reviewed_at": now,
                "reviewed_by": reviewed_by,
                "review_comment": comment,
            }
        )
        updated = CandidateRule.model_validate(updated.model_dump())
        executable = updated.to_executable() if action == CandidateRuleStatus.CONFIRMED else None
        with self._connection() as connection:
            connection.execute(
                "UPDATE candidate_rules SET status=?,version=?,candidate_json=?,reviewed_at=? "
                "WHERE candidate_rule_id=?",
                (
                    updated.status,
                    updated.version,
                    updated.model_dump_json(),
                    now.isoformat(),
                    candidate_rule_id,
                ),
            )
            connection.execute(
                "INSERT INTO candidate_rule_reviews("
                "candidate_rule_id,version,action,reviewed_by,review_comment,before_json,"
                "after_json,reviewed_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    candidate_rule_id,
                    updated.version,
                    action,
                    reviewed_by,
                    comment,
                    current.model_dump_json(),
                    updated.model_dump_json(),
                    now.isoformat(),
                ),
            )
            if executable:
                connection.execute(
                    "INSERT OR REPLACE INTO executable_rules VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        executable.rule_id,
                        candidate_rule_id,
                        executable.model_dump_json(),
                        updated.subject,
                        updated.attribute,
                        updated.standard_code,
                        updated.clause_id,
                        updated.version,
                        now.isoformat(),
                    ),
                )
        return updated

    def list_rules(
        self, subject: str | None = None, attribute: str | None = None
    ) -> list[ExecutableRule]:
        clauses, args = [], []
        if subject:
            clauses.append("subject=?")
            args.append(subject)
        if attribute:
            clauses.append("attribute=?")
            args.append(attribute)
        query = "SELECT rule_json FROM executable_rules" + (
            (" WHERE " + " AND ".join(clauses)) if clauses else ""
        )
        with self._connection() as connection:
            rows = connection.execute(query, args).fetchall()
        from pydantic import TypeAdapter

        adapter: TypeAdapter[ExecutableRule] = TypeAdapter(ExecutableRule)
        return [adapter.validate_json(row[0]) for row in rows]

    def get_rule(self, rule_id: str) -> ExecutableRule | None:
        from pydantic import TypeAdapter

        with self._connection() as connection:
            row = connection.execute(
                "SELECT rule_json FROM executable_rules WHERE rule_id=?", (rule_id,)
            ).fetchone()
        return TypeAdapter(ExecutableRule).validate_json(row[0]) if row else None
