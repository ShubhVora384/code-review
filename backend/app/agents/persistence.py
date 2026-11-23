"""Write agent output to the database."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete

from app.core.logging import get_logger
from app.db.base import new_id
from app.db.models import FileReport, Finding, Review
from app.db.session import session_scope

log = get_logger(__name__)

_FINDING_FIELDS = (
    "severity", "category", "title", "description", "impact", "suggestion",
    "suggested_patch", "code_snippet", "confidence", "line_start", "line_end",
    "proof_status", "proof_kind", "proof_summary", "proof_evidence",
    "proof_script", "proof_output", "proof_executed", "proof_duration_ms",
)


async def update_review(review_id: str, **fields: Any) -> None:
    async with session_scope() as db:
        review = await db.get(Review, review_id)
        if review is None:
            log.warning("persist.review_missing", review_id=review_id)
            return
        for key, value in fields.items():
            setattr(review, key, value)


async def save_results(
    review_id: str,
    *,
    file_rows: list[dict[str, Any]],
    kept_findings: list[dict[str, Any]],
) -> None:
    """Persist per-file reports and the findings that survived the critic.

    Findings are matched back to their file by the `_id` assigned before
    critique, so a rejected finding simply never gets written.
    """
    surviving = {f["_id"]: f for f in kept_findings if "_id" in f}

    async with session_scope() as db:
        # Idempotent: a re-run of this review replaces its previous output.
        await db.execute(delete(Finding).where(Finding.review_id == review_id))
        await db.execute(delete(FileReport).where(FileReport.review_id == review_id))
        await db.flush()

        for row in file_rows:
            report = FileReport(
                id=new_id(),
                review_id=review_id,
                path=row["path"],
                language=row.get("language"),
                loc=int(row.get("loc") or 0),
                size_bytes=int(row.get("size_bytes") or 0),
                status=row.get("status", "reviewed"),
                priority=int(row.get("priority") or 0),
                skip_reason=row.get("skip_reason"),
                score=row.get("score"),
                summary=row.get("summary"),
                role=row.get("role"),
                strengths=row.get("strengths") or [],
                dimension_scores=row.get("dimension_scores") or {},
                complexity=_trim_metrics(row.get("complexity") or {}),
                model_used=row.get("model_used"),
                cache_hit=bool(row.get("cache_hit")),
                content_hash=row.get("content_hash"),
            )
            db.add(report)
            await db.flush()

            for raw in row.get("findings", []):
                if raw.get("_id") not in surviving:
                    continue
                merged = surviving[raw["_id"]]
                db.add(
                    Finding(
                        id=new_id(),
                        review_id=review_id,
                        file_report_id=report.id,
                        path=row["path"],
                        verified=bool(merged.get("verified")),
                        critic_note=merged.get("critic_note"),
                        is_cross_cutting=False,
                        **_finding_kwargs(merged),
                    )
                )

        for raw in kept_findings:
            if not raw.get("is_cross_cutting"):
                continue
            db.add(
                Finding(
                    id=new_id(),
                    review_id=review_id,
                    file_report_id=None,
                    path=raw.get("path"),
                    verified=bool(raw.get("verified")),
                    critic_note=raw.get("critic_note"),
                    is_cross_cutting=True,
                    **_finding_kwargs(raw),
                )
            )


async def finalize(
    review_id: str,
    *,
    status: str,
    scores: dict[str, Any] | None = None,
    summary: str | None = None,
    recommendations: list[Any] | None = None,
    metrics: dict[str, Any] | None = None,
    token_usage: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    async with session_scope() as db:
        review = await db.get(Review, review_id)
        if review is None:
            return
        review.status = status
        review.phase = "completed" if status == "completed" else status
        review.progress = 1.0
        review.finished_at = datetime.now(UTC)
        if scores is not None:
            review.scores = scores
        if summary is not None:
            review.summary = summary
        if recommendations is not None:
            review.recommendations = recommendations
        if metrics is not None:
            review.metrics = metrics
        if token_usage is not None:
            review.token_usage = token_usage
        if error is not None:
            review.error = error


def _finding_kwargs(raw: dict[str, Any]) -> dict[str, Any]:
    out = {k: raw.get(k) for k in _FINDING_FIELDS}
    out["severity"] = out.get("severity") or "medium"
    out["category"] = out.get("category") or "maintainability"
    out["title"] = (out.get("title") or "Untitled finding")[:500]
    out["description"] = out.get("description") or ""
    out["confidence"] = float(out.get("confidence") or 0.7)
    out["proof_status"] = out.get("proof_status") or "not_attempted"
    out["proof_executed"] = bool(out.get("proof_executed"))
    out["proof_duration_ms"] = int(out.get("proof_duration_ms") or 0)
    return out


def _trim_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Keep the metric blob small enough to ship in an API response."""
    drop = {"imports", "undocumented_public"}
    return {k: v for k, v in metrics.items() if k not in drop}
