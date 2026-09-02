import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.core.exceptions import (
    FactNotFoundError,
    FactVersionConflictError,
    ProjectNotFoundError,
)
from app.domain.models import CheckItem, ClarificationRequest, ComplianceResult
from app.memory.models import (
    FactSourceType,
    FactStatus,
    FactValue,
    Project,
    ProjectFact,
    ProjectStatus,
    ProjectSummary,
    ReviewRecord,
    ReviewStatus,
    infer_value_type,
)

SCHEMA_VERSION = 4


class SQLiteMemoryRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"数据库版本 {version} 高于程序支持版本 {SCHEMA_VERSION}")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    project_type TEXT,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, name)
                );
                CREATE INDEX IF NOT EXISTS idx_projects_owner
                    ON projects(user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS project_facts (
                    fact_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    fact_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    value_type TEXT NOT NULL,
                    unit TEXT,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    source_type TEXT NOT NULL,
                    source_message_id TEXT,
                    superseded_by TEXT REFERENCES project_facts(fact_id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, fact_key, version)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_project_facts_one_active
                    ON project_facts(user_id, project_id, fact_key)
                    WHERE status = 'active';
                CREATE INDEX IF NOT EXISTS idx_project_facts_history
                    ON project_facts(user_id, project_id, fact_key, version DESC);
                CREATE TABLE IF NOT EXISTS memory_idempotency (
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    fact_id TEXT NOT NULL REFERENCES project_facts(fact_id),
                    PRIMARY KEY(user_id, project_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS memory_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    session_id TEXT NOT NULL,
                    source_message_id TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    action TEXT,
                    fact_key TEXT,
                    candidate_value_json TEXT,
                    unit TEXT,
                    confidence REAL,
                    expression_mode TEXT,
                    status TEXT NOT NULL,
                    failure_reason TEXT,
                    fact_id TEXT REFERENCES project_facts(fact_id),
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memory_candidates_scope
                    ON memory_candidates(user_id,project_id,status,created_at DESC);
                CREATE TABLE IF NOT EXISTS project_changes (
                    change_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    fact_key TEXT NOT NULL,
                    old_fact_id TEXT REFERENCES project_facts(fact_id),
                    new_fact_id TEXT NOT NULL REFERENCES project_facts(fact_id),
                    change_type TEXT NOT NULL,
                    source_message_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_project_changes_scope
                    ON project_changes(user_id,project_id,created_at DESC);
                CREATE TABLE IF NOT EXISTS review_runs (
                    review_run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    session_id TEXT NOT NULL, run_type TEXT NOT NULL,
                    design_description TEXT NOT NULL, status TEXT NOT NULL,
                    started_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS project_check_items (
                    check_item_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    review_run_id TEXT NOT NULL REFERENCES review_runs(review_run_id),
                    semantic_key TEXT NOT NULL, check_item_json TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(check_item_id,review_run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_check_items_status
                    ON project_check_items(project_id,status,updated_at DESC);
                CREATE TABLE IF NOT EXISTS review_records (
                    review_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    review_run_id TEXT NOT NULL REFERENCES review_runs(review_run_id),
                    check_item_id TEXT NOT NULL, semantic_key TEXT NOT NULL,
                    judgement TEXT NOT NULL, reason TEXT, result_json TEXT NOT NULL,
                    status TEXT NOT NULL, review_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL, invalidated_at TEXT,
                    invalidated_by_change_id TEXT, superseded_by_review_id TEXT,
                    UNIQUE(project_id,semantic_key,review_version)
                );
                CREATE INDEX IF NOT EXISTS idx_review_records_scope
                    ON review_records(user_id,project_id,status,created_at DESC);
                CREATE TABLE IF NOT EXISTS review_fact_dependencies (
                    review_id TEXT NOT NULL REFERENCES review_records(review_id),
                    fact_id TEXT NOT NULL REFERENCES project_facts(fact_id),
                    fact_key TEXT NOT NULL, PRIMARY KEY(review_id,fact_id)
                );
                CREATE INDEX IF NOT EXISTS idx_review_fact_dependency
                    ON review_fact_dependencies(fact_id);
                CREATE TABLE IF NOT EXISTS review_evidence_dependencies (
                    review_id TEXT NOT NULL REFERENCES review_records(review_id),
                    evidence_id TEXT NOT NULL, PRIMARY KEY(review_id,evidence_id)
                );
                CREATE TABLE IF NOT EXISTS pending_questions (
                    question_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    session_id TEXT NOT NULL, clarification_json TEXT NOT NULL,
                    status TEXT NOT NULL, asked_at TEXT NOT NULL, answered_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_pending_questions_scope
                    ON pending_questions(user_id,project_id,status,asked_at DESC);
                """
            )
            if version < SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def create_project(self, user_id: str, name: str, project_type: str | None) -> Project:
        self.initialize()
        now = datetime.now(UTC)
        project = Project(
            project_id=f"project_{secrets.token_hex(10)}",
            user_id=user_id,
            name=name,
            project_type=project_type,
            status=ProjectStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        with self._connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)",
                    (
                        project.project_id,
                        project.user_id,
                        project.name,
                        project.project_type,
                        project.status,
                        project.version,
                        project.created_at.isoformat(),
                        project.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise FactVersionConflictError("同一用户下已存在同名项目") from exc
        return project

    def list_projects(self, user_id: str) -> list[Project]:
        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC,project_id",
                (user_id,),
            ).fetchall()
        return [self._project(row) for row in rows]

    def get_project(self, user_id: str, project_id: str) -> Project:
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE user_id=? AND project_id=?",
                (user_id, project_id),
            ).fetchone()
        if row is None:
            raise ProjectNotFoundError("项目不存在")
        return self._project(row)

    def create_fact(
        self,
        user_id: str,
        project_id: str,
        fact_key: str,
        value: FactValue,
        unit: str | None,
        idempotency_key: str,
        *,
        source_type: FactSourceType = FactSourceType.API,
        source_message_id: str | None = None,
        confidence: float = 1.0,
    ) -> ProjectFact:
        self.initialize()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_project(connection, user_id, project_id)
            cached = self._idempotent(connection, user_id, project_id, idempotency_key)
            if cached is not None:
                return cached
            active = connection.execute(
                "SELECT fact_id FROM project_facts WHERE user_id=? AND project_id=? "
                "AND fact_key=? AND status='active'",
                (user_id, project_id, fact_key),
            ).fetchone()
            if active is not None:
                raise FactVersionConflictError(
                    "事实已存在，请使用更新接口",
                    details={"fact_id": active["fact_id"]},
                )
            fact = self._new_fact(
                user_id,
                project_id,
                fact_key,
                value,
                unit,
                1,
                source_type,
                source_message_id,
                confidence,
            )
            self._insert_fact(connection, fact)
            self._save_idempotent(connection, fact, idempotency_key)
            self._insert_change(connection, fact, None, "create")
            return fact

    def update_fact(
        self,
        user_id: str,
        project_id: str,
        fact_id: str,
        value: FactValue,
        unit: str | None,
        expected_version: int,
        idempotency_key: str,
        *,
        source_type: FactSourceType = FactSourceType.API,
        source_message_id: str | None = None,
        confidence: float = 1.0,
    ) -> ProjectFact:
        self.initialize()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_project(connection, user_id, project_id)
            cached = self._idempotent(connection, user_id, project_id, idempotency_key)
            if cached is not None:
                return cached
            row = connection.execute(
                "SELECT * FROM project_facts WHERE user_id=? AND project_id=? AND fact_id=?",
                (user_id, project_id, fact_id),
            ).fetchone()
            if row is None:
                raise FactNotFoundError("项目事实不存在")
            old = self._fact(row)
            if old.status != FactStatus.ACTIVE or old.version != expected_version:
                current = connection.execute(
                    "SELECT fact_id,version FROM project_facts WHERE user_id=? AND project_id=? "
                    "AND fact_key=? AND status='active'",
                    (user_id, project_id, old.fact_key),
                ).fetchone()
                raise FactVersionConflictError(
                    "项目事实版本已变化",
                    details=dict(current) if current else {},
                )
            new_fact = self._new_fact(
                user_id,
                project_id,
                old.fact_key,
                value,
                unit,
                old.version + 1,
                source_type,
                source_message_id,
                confidence,
            )
            # 先以非 active 状态插入，既满足 superseded_by 外键，又不违反 active 唯一索引。
            self._insert_fact(
                connection, new_fact.model_copy(update={"status": FactStatus.UNCERTAIN})
            )
            connection.execute(
                "UPDATE project_facts SET status='superseded',superseded_by=?,updated_at=? "
                "WHERE fact_id=? AND status='active'",
                (new_fact.fact_id, new_fact.updated_at.isoformat(), old.fact_id),
            )
            connection.execute(
                "UPDATE project_facts SET status='active' WHERE fact_id=?", (new_fact.fact_id,)
            )
            self._save_idempotent(connection, new_fact, idempotency_key)
            change_id = self._insert_change(connection, new_fact, old.fact_id, "update")
            self._invalidate_reviews(connection, old.fact_id, change_id, new_fact.updated_at)
            return new_fact

    def save_candidate(
        self,
        user_id: str,
        project_id: str,
        session_id: str,
        source_message_id: str,
        raw_text: str,
        *,
        action: str | None,
        fact_key: str | None,
        value: FactValue | None,
        unit: str | None,
        confidence: float | None,
        expression_mode: str | None,
        status: str,
        failure_reason: str | None = None,
        fact_id: str | None = None,
    ) -> str:
        self.initialize()
        candidate_id = f"candidate_{secrets.token_hex(10)}"
        now = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            self._require_project(connection, user_id, project_id)
            connection.execute(
                "INSERT INTO memory_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    user_id,
                    project_id,
                    session_id,
                    source_message_id,
                    raw_text,
                    action,
                    fact_key,
                    None if value is None else json.dumps(value, ensure_ascii=False),
                    unit,
                    confidence,
                    expression_mode,
                    status,
                    failure_reason,
                    fact_id,
                    now,
                    now if status in {"applied", "rejected"} else None,
                ),
            )
        return candidate_id

    def list_changes(self, user_id: str, project_id: str) -> list[dict[str, object]]:
        self.initialize()
        with self._connection() as connection:
            self._require_project(connection, user_id, project_id)
            rows = connection.execute(
                "SELECT * FROM project_changes WHERE user_id=? AND project_id=? "
                "ORDER BY created_at DESC,change_id",
                (user_id, project_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_candidates(
        self, user_id: str, project_id: str, session_id: str
    ) -> list[dict[str, object]]:
        self.initialize()
        with self._connection() as connection:
            self._require_project(connection, user_id, project_id)
            rows = connection.execute(
                "SELECT * FROM memory_candidates WHERE user_id=? AND project_id=? "
                "AND session_id=? AND status='pending' ORDER BY created_at,candidate_id",
                (user_id, project_id, session_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def resolve_candidate(
        self,
        user_id: str,
        project_id: str,
        candidate_id: str,
        status: str,
        fact_id: str | None,
    ) -> None:
        self.initialize()
        with self._connection() as connection:
            changed = connection.execute(
                "UPDATE memory_candidates SET status=?,fact_id=?,resolved_at=? "
                "WHERE candidate_id=? AND user_id=? AND project_id=? AND status='pending'",
                (status, fact_id, datetime.now(UTC).isoformat(), candidate_id, user_id, project_id),
            ).rowcount
            if not changed:
                raise ProjectNotFoundError("待确认记忆候选不存在")

    def save_reviews(
        self,
        user_id: str,
        project_id: str,
        session_id: str,
        design_description: str,
        check_items: list[CheckItem],
        results: list[ComplianceResult],
        evidence_by_item: dict[str, list[str]],
        *,
        run_type: str = "full",
    ) -> str:
        """保存已通过 Evidence 校验的审查结果和精确依赖。"""

        self.initialize()
        run_id = f"review_run_{secrets.token_hex(10)}"
        now = datetime.now(UTC)
        active_facts = self.list_facts(user_id, project_id)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_project(connection, user_id, project_id)
            connection.execute(
                "INSERT INTO review_runs VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    user_id,
                    project_id,
                    session_id,
                    run_type,
                    design_description,
                    "completed",
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            result_by_id = {row.item_id: row for row in results}
            for item in check_items:
                item_id = item.item_id
                result = result_by_id.get(item_id)
                if result is None:
                    continue
                semantic_key = self._semantic_key(item)
                connection.execute(
                    "INSERT INTO project_check_items VALUES(?,?,?,?,?,?,?,?)",
                    (
                        item_id,
                        project_id,
                        run_id,
                        semantic_key,
                        item.model_dump_json(),
                        "completed",
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                previous = connection.execute(
                    "SELECT review_id,review_version FROM review_records "
                    "WHERE user_id=? AND project_id=? AND semantic_key=? "
                    "ORDER BY review_version DESC LIMIT 1",
                    (user_id, project_id, semantic_key),
                ).fetchone()
                version = int(previous["review_version"]) + 1 if previous else 1
                review_id = f"review_{secrets.token_hex(10)}"
                connection.execute(
                    "INSERT INTO review_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        review_id,
                        user_id,
                        project_id,
                        run_id,
                        item_id,
                        semantic_key,
                        str(result.status),
                        result.reasoning,
                        result.model_dump_json(),
                        ReviewStatus.VALID,
                        version,
                        now.isoformat(),
                        None,
                        None,
                        None,
                    ),
                )
                if previous:
                    connection.execute(
                        "UPDATE review_records SET status='superseded',"
                        "superseded_by_review_id=? WHERE review_id=?",
                        (review_id, previous["review_id"]),
                    )
                for fact in self._facts_for_item(item, active_facts):
                    connection.execute(
                        "INSERT INTO review_fact_dependencies VALUES(?,?,?)",
                        (review_id, fact.fact_id, fact.fact_key),
                    )
                for evidence_id in evidence_by_item.get(item_id, []):
                    connection.execute(
                        "INSERT OR IGNORE INTO review_evidence_dependencies VALUES(?,?)",
                        (review_id, evidence_id),
                    )
        return run_id

    def list_reviews(self, user_id: str, project_id: str) -> list[ReviewRecord]:
        self.initialize()
        with self._connection() as connection:
            self._require_project(connection, user_id, project_id)
            rows = connection.execute(
                "SELECT r.*,GROUP_CONCAT(DISTINCT f.fact_id) fact_ids,"
                "GROUP_CONCAT(DISTINCT e.evidence_id) evidence_ids FROM review_records r "
                "LEFT JOIN review_fact_dependencies f ON f.review_id=r.review_id "
                "LEFT JOIN review_evidence_dependencies e ON e.review_id=r.review_id "
                "WHERE r.user_id=? AND r.project_id=? GROUP BY r.review_id "
                "ORDER BY r.created_at DESC,r.review_version DESC",
                (user_id, project_id),
            ).fetchall()
        return [self._review(row) for row in rows]

    def get_review(self, user_id: str, project_id: str, review_id: str) -> ReviewRecord:
        selected = next(
            (row for row in self.list_reviews(user_id, project_id) if row.review_id == review_id),
            None,
        )
        if selected is None:
            raise ProjectNotFoundError("审查记录不存在")
        return selected

    def stale_check_items(self, user_id: str, project_id: str) -> list[CheckItem]:
        self.initialize()
        with self._connection() as connection:
            self._require_project(connection, user_id, project_id)
            rows = connection.execute(
                "SELECT pci.check_item_json FROM project_check_items pci "
                "JOIN review_records r ON r.review_run_id=pci.review_run_id "
                "AND r.check_item_id=pci.check_item_id WHERE r.user_id=? "
                "AND r.project_id=? AND r.status='stale' ORDER BY r.invalidated_at DESC",
                (user_id, project_id),
            ).fetchall()
        unique: dict[str, CheckItem] = {}
        for row in rows:
            item = CheckItem.model_validate_json(row["check_item_json"])
            unique[self._semantic_key(item)] = item
        return list(unique.values())

    def check_items_for_fact(self, user_id: str, project_id: str, fact_id: str) -> list[CheckItem]:
        self.initialize()
        with self._connection() as connection:
            self._require_project(connection, user_id, project_id)
            rows = connection.execute(
                "SELECT pci.check_item_json FROM review_fact_dependencies d "
                "JOIN review_records r ON r.review_id=d.review_id "
                "JOIN project_check_items pci ON pci.review_run_id=r.review_run_id "
                "AND pci.check_item_id=r.check_item_id WHERE d.fact_id=? AND r.user_id=? "
                "AND r.project_id=? AND r.status='valid'",
                (fact_id, user_id, project_id),
            ).fetchall()
        return [CheckItem.model_validate_json(row["check_item_json"]) for row in rows]

    def save_pending_question(
        self,
        user_id: str,
        project_id: str,
        session_id: str,
        clarification: ClarificationRequest,
    ) -> str:
        self.initialize()
        with self._connection() as connection:
            self._require_project(connection, user_id, project_id)
            existing = connection.execute(
                "SELECT question_id FROM pending_questions WHERE user_id=? AND project_id=? "
                "AND status='pending' ORDER BY asked_at DESC LIMIT 1",
                (user_id, project_id),
            ).fetchone()
            if existing:
                return str(existing["question_id"])
            question_id = f"question_{secrets.token_hex(10)}"
            connection.execute(
                "INSERT INTO pending_questions VALUES(?,?,?,?,?,?,?,?)",
                (
                    question_id,
                    user_id,
                    project_id,
                    session_id,
                    clarification.model_dump_json(),
                    "pending",
                    datetime.now(UTC).isoformat(),
                    None,
                ),
            )
        return question_id

    def pending_question(
        self, user_id: str, project_id: str
    ) -> tuple[str, ClarificationRequest] | None:
        self.initialize()
        with self._connection() as connection:
            self._require_project(connection, user_id, project_id)
            row = connection.execute(
                "SELECT question_id,clarification_json FROM pending_questions "
                "WHERE user_id=? AND project_id=? AND status='pending' "
                "ORDER BY asked_at DESC LIMIT 1",
                (user_id, project_id),
            ).fetchone()
        if row is None:
            return None
        return str(row["question_id"]), ClarificationRequest.model_validate_json(
            row["clarification_json"]
        )

    def resolve_pending_question(self, user_id: str, project_id: str, question_id: str) -> None:
        self.initialize()
        with self._connection() as connection:
            changed = connection.execute(
                "UPDATE pending_questions SET status='answered',answered_at=? "
                "WHERE question_id=? AND user_id=? AND project_id=? AND status='pending'",
                (datetime.now(UTC).isoformat(), question_id, user_id, project_id),
            ).rowcount
            if not changed:
                raise ProjectNotFoundError("待确认问题不存在")

    def project_summary(self, user_id: str, project_id: str) -> ProjectSummary:
        self.initialize()
        with self._connection() as connection:
            self._require_project(connection, user_id, project_id)
            fact_count = connection.execute(
                "SELECT COUNT(*) FROM project_facts WHERE user_id=? AND project_id=? "
                "AND status='active'",
                (user_id, project_id),
            ).fetchone()[0]
            review_counts = dict(
                connection.execute(
                    "SELECT status,COUNT(*) FROM review_records WHERE user_id=? AND project_id=? "
                    "GROUP BY status",
                    (user_id, project_id),
                ).fetchall()
            )
            pending_count = connection.execute(
                "SELECT COUNT(*) FROM pending_questions WHERE user_id=? AND project_id=? "
                "AND status='pending'",
                (user_id, project_id),
            ).fetchone()[0]
        return ProjectSummary(
            project_id=project_id,
            active_fact_count=fact_count,
            valid_review_count=review_counts.get("valid", 0),
            stale_review_count=review_counts.get("stale", 0),
            pending_question_count=pending_count,
        )

    def list_facts(
        self, user_id: str, project_id: str, *, include_history: bool = False
    ) -> list[ProjectFact]:
        self.initialize()
        with self._connection() as connection:
            self._require_project(connection, user_id, project_id)
            condition = "" if include_history else " AND status='active'"
            rows = connection.execute(
                "SELECT * FROM project_facts WHERE user_id=? AND project_id=?"
                + condition
                + " ORDER BY fact_key,version DESC",
                (user_id, project_id),
            ).fetchall()
        return [self._fact(row) for row in rows]

    @staticmethod
    def _require_project(connection: sqlite3.Connection, user_id: str, project_id: str) -> None:
        row = connection.execute(
            "SELECT 1 FROM projects WHERE user_id=? AND project_id=?", (user_id, project_id)
        ).fetchone()
        if row is None:
            raise ProjectNotFoundError("项目不存在")

    @staticmethod
    def _new_fact(
        user_id: str,
        project_id: str,
        fact_key: str,
        value: FactValue,
        unit: str | None,
        version: int,
        source_type: FactSourceType,
        source_message_id: str | None,
        confidence: float,
    ) -> ProjectFact:
        now = datetime.now(UTC)
        return ProjectFact(
            fact_id=f"fact_{secrets.token_hex(10)}",
            user_id=user_id,
            project_id=project_id,
            fact_key=fact_key,
            value=value,
            value_type=infer_value_type(value),
            unit=unit,
            status=FactStatus.ACTIVE,
            version=version,
            confidence=confidence,
            source_type=source_type,
            source_message_id=source_message_id,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _insert_fact(connection: sqlite3.Connection, fact: ProjectFact) -> None:
        connection.execute(
            "INSERT INTO project_facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fact.fact_id,
                fact.user_id,
                fact.project_id,
                fact.fact_key,
                json.dumps(fact.value, ensure_ascii=False),
                fact.value_type,
                fact.unit,
                fact.status,
                fact.version,
                fact.confidence,
                fact.source_type,
                fact.source_message_id,
                fact.superseded_by,
                fact.created_at.isoformat(),
                fact.updated_at.isoformat(),
            ),
        )

    @staticmethod
    def _save_idempotent(
        connection: sqlite3.Connection, fact: ProjectFact, idempotency_key: str
    ) -> None:
        connection.execute(
            "INSERT INTO memory_idempotency VALUES(?,?,?,?)",
            (fact.user_id, fact.project_id, idempotency_key, fact.fact_id),
        )

    @staticmethod
    def _insert_change(
        connection: sqlite3.Connection,
        fact: ProjectFact,
        old_fact_id: str | None,
        change_type: str,
    ) -> str:
        change_id = f"change_{secrets.token_hex(10)}"
        connection.execute(
            "INSERT INTO project_changes VALUES(?,?,?,?,?,?,?,?,?)",
            (
                change_id,
                fact.user_id,
                fact.project_id,
                fact.fact_key,
                old_fact_id,
                fact.fact_id,
                change_type,
                fact.source_message_id,
                fact.created_at.isoformat(),
            ),
        )
        return change_id

    @staticmethod
    def _invalidate_reviews(
        connection: sqlite3.Connection,
        old_fact_id: str,
        change_id: str,
        invalidated_at: datetime,
    ) -> None:
        connection.execute(
            "UPDATE review_records SET status='stale',invalidated_at=?,"
            "invalidated_by_change_id=? WHERE status='valid' AND review_id IN "
            "(SELECT review_id FROM review_fact_dependencies WHERE fact_id=?)",
            (invalidated_at.isoformat(), change_id, old_fact_id),
        )

    @staticmethod
    def _semantic_key(item: CheckItem) -> str:
        return "|".join(
            str(getattr(item, name, "") or "").strip().lower()
            for name in ("object", "attribute", "location")
        )

    @staticmethod
    def _facts_for_item(item: CheckItem, facts: list[ProjectFact]) -> list[ProjectFact]:
        text = " ".join(
            str(getattr(item, name, "") or "") for name in ("object", "attribute", "source_text")
        ).lower()
        item_value = getattr(item, "value", None)
        item_unit = str(getattr(item, "unit", "") or "").lower()
        aliases = {
            "ups_duration": ("ups", "供电时间", "持续供电"),
            "illumination": ("照度", "illumination"),
            "pipe_diameter": ("管径", "diameter"),
            "pressure": ("压力", "pressure"),
            "temperature": ("温度", "temperature"),
        }
        selected = []
        for fact in facts:
            value_matches = item_value is not None and str(fact.value) == str(item_value)
            unit_matches = not fact.unit or not item_unit or fact.unit.lower() == item_unit
            fact_aliases = aliases.get(fact.fact_key, (fact.fact_key,))
            key_matches = any(alias in text for alias in fact_aliases)
            if (value_matches and unit_matches) or key_matches:
                selected.append(fact)
        return selected

    @staticmethod
    def _review(row: sqlite3.Row) -> ReviewRecord:
        result = json.loads(row["result_json"])
        return ReviewRecord(
            review_id=row["review_id"],
            project_id=row["project_id"],
            review_run_id=row["review_run_id"],
            check_item_id=row["check_item_id"],
            semantic_key=row["semantic_key"],
            judgement=row["judgement"],
            reason=row["reason"],
            status=row["status"],
            review_version=row["review_version"],
            actual=result.get("actual"),
            required=result.get("required"),
            evidence_ids=row["evidence_ids"].split(",") if row["evidence_ids"] else [],
            fact_ids=row["fact_ids"].split(",") if row["fact_ids"] else [],
            created_at=row["created_at"],
            invalidated_at=row["invalidated_at"],
            invalidated_by_change_id=row["invalidated_by_change_id"],
        )

    @classmethod
    def _idempotent(
        cls, connection: sqlite3.Connection, user_id: str, project_id: str, key: str
    ) -> ProjectFact | None:
        row = connection.execute(
            "SELECT f.* FROM memory_idempotency i JOIN project_facts f ON f.fact_id=i.fact_id "
            "WHERE i.user_id=? AND i.project_id=? AND i.idempotency_key=?",
            (user_id, project_id, key),
        ).fetchone()
        return cls._fact(row) if row else None

    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        return Project(
            project_id=row["project_id"],
            user_id=row["user_id"],
            name=row["name"],
            project_type=row["project_type"],
            status=row["status"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _fact(row: sqlite3.Row) -> ProjectFact:
        return ProjectFact(
            fact_id=row["fact_id"],
            user_id=row["user_id"],
            project_id=row["project_id"],
            fact_key=row["fact_key"],
            value=json.loads(row["value_json"]),
            value_type=row["value_type"],
            unit=row["unit"],
            status=row["status"],
            version=row["version"],
            confidence=row["confidence"],
            source_type=row["source_type"],
            source_message_id=row["source_message_id"],
            superseded_by=row["superseded_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
