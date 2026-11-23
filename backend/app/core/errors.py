from __future__ import annotations


class CodeReviewError(Exception):
    """Base class for all domain errors."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class IngestionError(CodeReviewError):
    status_code = 400
    code = "ingestion_failed"


class RepositoryTooLargeError(IngestionError):
    code = "repository_too_large"


class UnsupportedSourceError(IngestionError):
    code = "unsupported_source"


class LLMError(CodeReviewError):
    status_code = 502
    code = "llm_error"


class LLMRateLimitError(LLMError):
    status_code = 429
    code = "llm_rate_limited"


class BudgetExhaustedError(LLMError):
    status_code = 429
    code = "daily_budget_exhausted"


class NotFoundError(CodeReviewError):
    status_code = 404
    code = "not_found"
