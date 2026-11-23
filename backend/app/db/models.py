from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin


class Review(Base, IdMixin, TimestampMixin):
    """One end-to-end review run over one codebase."""

    __tablename__ = "reviews"

    # --- source ---
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    branch: Mapped[str | None] = mapped_column(String(255))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    workspace_path: Mapped[str | None] = mapped_column(Text)

    # --- lifecycle ---
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    phase: Mapped[str] = mapped_column(String(40), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- agent outputs (each is one turn's product) ---
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    structure: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    scores: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)
    recommendations: Mapped[list[Any] | None] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # --- config snapshot ---
    options: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # --- public sharing ---
    # A founder needs to hand this report to an investor or a customer, so the
    # token is opaque, revocable, and gates a findings-only view (never source).
    share_token: Mapped[str | None] = mapped_column(String(43), unique=True, index=True)
    share_enabled: Mapped[bool] = mapped_column(default=False)

    files: Mapped[list[FileReport]] = relationship(
        back_populates="review", cascade="all, delete-orphan", lazy="selectin"
    )
    findings: Mapped[list[Finding]] = relationship(
        back_populates="review", cascade="all, delete-orphan", lazy="selectin"
    )
    events: Mapped[list[ReviewEvent]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )


class FileReport(Base, IdMixin, TimestampMixin):
    """Per-file verdict produced by the FileReviewer agent."""

    __tablename__ = "file_reports"
    __table_args__ = (
        UniqueConstraint("review_id", "path", name="uq_file_reports_review_path"),
        Index("ix_file_reports_review_score", "review_id", "score"),
    )

    review_id: Mapped[str] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(40))
    loc: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(20), default="pending")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    skip_reason: Mapped[str | None] = mapped_column(Text)

    score: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    strengths: Mapped[list[Any] | None] = mapped_column(JSON)
    dimension_scores: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    complexity: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    model_used: Mapped[str | None] = mapped_column(String(80))
    cache_hit: Mapped[bool] = mapped_column(default=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    review: Mapped[Review] = relationship(back_populates="files")
    findings: Mapped[list[Finding]] = relationship(
        back_populates="file_report", cascade="all, delete-orphan", lazy="selectin"
    )


class Finding(Base, IdMixin, TimestampMixin):
    """A single actionable issue. Either file-scoped or cross-cutting."""

    __tablename__ = "findings"
    __table_args__ = (
        Index("ix_findings_review_severity", "review_id", "severity"),
    )

    review_id: Mapped[str] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), index=True
    )
    file_report_id: Mapped[str | None] = mapped_column(
        ForeignKey("file_reports.id", ondelete="CASCADE"), index=True
    )

    path: Mapped[str | None] = mapped_column(Text)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)

    severity: Mapped[str] = mapped_column(String(16), default="medium", index=True)
    category: Mapped[str] = mapped_column(String(40), default="maintainability")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)
    suggested_patch: Mapped[str | None] = mapped_column(Text)
    code_snippet: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)

    # Critic agent verdict
    verified: Mapped[bool] = mapped_column(default=False)
    critic_note: Mapped[str | None] = mapped_column(Text)
    is_cross_cutting: Mapped[bool] = mapped_column(default=False)

    # Proof engine verdict. `proven` means we demonstrated it, not that a
    # model believed it. Refuted findings are never persisted at all.
    proof_status: Mapped[str] = mapped_column(
        String(16), default="not_attempted", index=True
    )
    proof_kind: Mapped[str | None] = mapped_column(String(64))
    proof_summary: Mapped[str | None] = mapped_column(Text)
    proof_evidence: Mapped[str | None] = mapped_column(Text)
    proof_script: Mapped[str | None] = mapped_column(Text)
    proof_output: Mapped[str | None] = mapped_column(Text)
    proof_executed: Mapped[bool] = mapped_column(default=False)
    proof_duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    review: Mapped[Review] = relationship(back_populates="findings")
    file_report: Mapped[FileReport | None] = relationship(back_populates="findings")


class ReviewEvent(Base, IdMixin):
    """Append-only agent trace. Backs SSE streaming + replay on reconnect."""

    __tablename__ = "review_events"
    __table_args__ = (
        UniqueConstraint("review_id", "seq", name="uq_review_events_seq"),
    )

    review_id: Mapped[str] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    agent: Mapped[str | None] = mapped_column(String(48))
    message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    review: Mapped[Review] = relationship(back_populates="events")


class LLMCallLog(Base, IdMixin):
    """Every Groq call — powers cost/rate dashboards and debugging."""

    __tablename__ = "llm_calls"

    review_id: Mapped[str | None] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), index=True
    )
    agent: Mapped[str] = mapped_column(String(48))
    model: Mapped[str] = mapped_column(String(80))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    cache_hit: Mapped[bool] = mapped_column(default=False)
    ok: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
