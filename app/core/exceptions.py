class AppError(Exception):
    """可安全映射为统一 API 错误的基础异常。"""

    code = "APP_ERROR"

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
