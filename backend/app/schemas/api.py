from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SourceType = Literal["git", "local", "zip"]
ReviewStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


class ReviewOptions(BaseModel):
    max_files_to_review: int = Field(default=40, ge=1, le=400)
    include_tests: bool = True
    deep_security_pass: bool = True
    enable_critic: bool = True
    focus_areas: list[str] = Field(
        default_factory=list,
        description="Optional user steer, e.g. ['security', 'performance']",
    )


class CreateReviewRequest(BaseModel):
    source_type: SourceType
    source_ref: str = Field(
        default="", description="Git URL or local absolute path. Empty for zip."
    )
    branch: str | None = None
    access_token: str | None = Field(
        default=None,
        description="PAT for private repos. Used for the clone, never persisted.",
    )
    options: ReviewOptions = Field(default_factory=ReviewOptions)

    @model_validator(mode="after")
    def _require_ref(self) -> CreateReviewRequest:
        if self.source_type in ("git", "local") and not self.source_ref.strip():
            raise ValueError(f"source_ref is required for source_type={self.source_type}")
        return self


class FindingRead(BaseModel):
    id: str
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    severity: str
    category: str
    title: str
    description: str
    impact: str | None = None
    suggestion: str | None = None
    suggested_patch: str | None = None
    code_snippet: str | None = None
    confidence: float
    verified: bool
    critic_note: str | None = None
    is_cross_cutting: bool

    # Proof engine. `proven` means demonstrated, not merely agreed with.
    proof_status: str = "not_attempted"
    proof_kind: str | None = None
    proof_summary: str | None = None
    proof_evidence: str | None = None
    proof_output: str | None = None
    proof_executed: bool = False
    proof_duration_ms: int = 0

    model_config = {"from_attributes": True}


class FileReportRead(BaseModel):
    id: str
    path: str
    language: str | None = None
    loc: int
    status: str
    score: float | None = None
    summary: str | None = None
    role: str | None = None
    strengths: list[Any] | None = None
    dimension_scores: dict[str, Any] | None = None
    complexity: dict[str, Any] | None = None
    skip_reason: str | None = None
    cache_hit: bool = False
    findings: list[FindingRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ReviewSummary(BaseModel):
    """Lightweight row for the history list."""

    id: str
    display_name: str
    source_type: str
    source_ref: str
    status: str
    phase: str
    progress: float
    created_at: datetime
    finished_at: datetime | None = None
    overall_score: float | None = None
    grade: str | None = None
    findings_count: int = 0
    critical_count: int = 0

    model_config = {"from_attributes": True}


class ReviewDetail(BaseModel):
    id: str
    display_name: str
    source_type: str
    source_ref: str
    branch: str | None = None
    commit_sha: str | None = None
    status: str
    phase: str
    progress: float
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    profile: dict[str, Any] | None = None
    structure: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    scores: dict[str, Any] | None = None
    summary: str | None = None
    recommendations: list[Any] | None = None
    metrics: dict[str, Any] | None = None
    token_usage: dict[str, Any] | None = None
    options: dict[str, Any] | None = None

    files: list[FileReportRead] = Field(default_factory=list)
    cross_cutting_findings: list[FindingRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class EventRead(BaseModel):
    seq: int
    type: str
    agent: str | None = None
    message: str | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    redis: str
    groq_configured: bool
    models: dict[str, str]
    budget: dict[str, Any]
    keys: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: str | None = None
