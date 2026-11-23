"""Public, read-only report links.

A founder's reason for running an audit is usually to show someone the result —
an investor, a customer, a prospective engineer. That means a URL they can send
without handing over an account.

Two rules make this safe to expose:

* the token is opaque and revocable, and sharing is off until asked for;
* the public view carries findings and evidence only — never file contents,
  never the proof scripts, never the source path on disk.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import db_session
from app.db.models import FileReport, Finding, Review
from app.schemas.agent import SEVERITY_ORDER

router = APIRouter(tags=["share"])

Session = Annotated[AsyncSession, Depends(db_session)]

# 32 bytes of entropy, URL-safe: unguessable, and short enough to paste.
_TOKEN_BYTES = 32


@router.post("/reviews/{review_id}/share")
async def enable_share(review_id: str, db: Session) -> dict[str, Any]:
    """Turn on public sharing and return the link."""
    review = await db.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="Only a completed review can be shared.",
        )

    if not review.share_token:
        review.share_token = secrets.token_urlsafe(_TOKEN_BYTES)
    review.share_enabled = True
    await db.commit()

    return {"share_token": review.share_token, "path": f"/shared/{review.share_token}"}


@router.delete("/reviews/{review_id}/share", status_code=200)
async def revoke_share(review_id: str, db: Session) -> dict[str, Any]:
    """Revoke the link. A new one is issued next time sharing is enabled."""
    review = await db.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")

    review.share_enabled = False
    review.share_token = None  # rotate, so an old link can never be revived
    await db.commit()
    return {"shared": False}


@router.get("/share/{token}")
async def read_shared(token: str, db: Session) -> dict[str, Any]:
    """The public report. No authentication, and no source code."""
    review = (
        await db.execute(
            select(Review)
            .where(Review.share_token == token, Review.share_enabled.is_(True))
            .options(
                selectinload(Review.files).selectinload(FileReport.findings),
                selectinload(Review.findings),
            )
        )
    ).scalar_one_or_none()

    if review is None:
        # Same answer for "never existed" and "revoked" — a shared link should
        # not confirm that a report was once here.
        raise HTTPException(status_code=404, detail="This report is not available.")

    scores = review.scores or {}
    findings = [
        _public_finding(f)
        for f in sorted(
            review.findings, key=lambda x: SEVERITY_ORDER.get(x.severity, 5)
        )
    ]
    proven = [f for f in findings if f["proof_status"] == "proven"]

    return {
        "display_name": review.display_name,
        "created_at": review.created_at,
        "finished_at": review.finished_at,
        "overall_score": scores.get("overall"),
        "grade": scores.get("grade"),
        "verdict": scores.get("verdict"),
        "launch_verdict": scores.get("launch_verdict"),
        "dimensions": scores.get("dimensions"),
        "severity_counts": scores.get("severity_counts"),
        "summary": review.summary,
        "narrative": scores.get("narrative"),
        "recommendations": review.recommendations,
        "proven_findings": proven,
        "other_findings": [f for f in findings if f["proof_status"] != "proven"],
        "metrics": _public_metrics(review.metrics or {}),
    }


def _public_finding(f: Finding) -> dict[str, Any]:
    """Everything a reader needs, and nothing that leaks the codebase.

    `code_snippet` and `proof_script` are deliberately omitted: they quote the
    source, and the person holding a share link may not be entitled to it.
    """
    return {
        "severity": f.severity,
        "category": f.category,
        "title": f.title,
        "description": f.description,
        "impact": f.impact,
        "suggestion": f.suggestion,
        "path": f.path,
        "line_start": f.line_start,
        "proof_status": f.proof_status,
        "proof_summary": f.proof_summary,
        "proof_evidence": f.proof_evidence,
        "proof_executed": f.proof_executed,
        "is_cross_cutting": f.is_cross_cutting,
    }


def _public_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "files_indexed", "files_reviewed", "total_loc", "test_ratio",
        "languages", "proven_findings", "total_findings",
    }
    return {k: v for k, v in metrics.items() if k in keep}
