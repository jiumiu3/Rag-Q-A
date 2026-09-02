class AppError(Exception):
    """可安全映射为统一 API 错误的基础异常。"""

    code = "APP_ERROR"
    status_code = 400

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class IngestionError(AppError):
    code = "INGESTION_ERROR"


class RetrievalError(AppError):
    code = "RETRIEVAL_ERROR"


class EvidenceError(AppError):
    code = "EVIDENCE_ERROR"


class ComplianceError(AppError):
    code = "COMPLIANCE_ERROR"


class AgentStateError(AppError):
    code = "AGENT_STATE_ERROR"


class IdentityError(AppError):
    code = "MISSING_USER_ID"


class ProjectNotFoundError(AppError):
    code = "PROJECT_NOT_FOUND"
    status_code = 404


class SessionNotFoundError(AppError):
    code = "SESSION_NOT_FOUND"
    status_code = 404


class FactNotFoundError(AppError):
    code = "FACT_NOT_FOUND"
    status_code = 404


class FactVersionConflictError(AppError):
    code = "FACT_VERSION_CONFLICT"
    status_code = 409
