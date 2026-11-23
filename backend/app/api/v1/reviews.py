from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.api.deps import db_session
from app.core.errors import CodeReviewError
from app.core.event_bus import bus
from app.core.logging import get_logger
from app.db.base import new_id
from app.db.models import FileReport, Finding, Review, ReviewEvent
from app.db.session import session_scope
from app.ingestion.source import use_zip
from app.schemas.agent import SEVERITY_ORDER
from app.schemas.api import (
    CreateReviewRequest,
    EventRead,
    FindingRead,
    ReviewDetail,
    ReviewOptions,
    ReviewSummary,
)
from app.workers.queue import enqueue
from app.workers.runner import request_cancel

log = get_logger(__name__)
router = APIRouter(prefix="/reviews", tags=["reviews"])

Session = Annotated[AsyncSession, Depends(db_session)]

_MAX_UPLOAD_BYTES = 200 * 1024 * 1024
_TERMINAL = {"completed", "failed", "cancelled"}


# ------------------------------------------------------------------- create


@router.post("", response_model=ReviewDetail, status_code=201)
async def create_review(payload: CreateReviewRequest, db: Session) -> ReviewDetail:
    """Start a review of a git URL or a local directory."""
    if payload.source_type == "zip":
        raise HTTPException(
            status_code=400,
            detail="Use POST /reviews/upload for ZIP archives.",
        )

    review = Review(
        id=new_id(),
        source_type=payload.source_type,
        source_ref=payload.source_ref.strip(),
        display_name=_derive_name(payload.source_ref),
        branch=payload.branch,
        status="queued",
        phase="queued",
        options=payload.options.model_dump(),
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)

    # The PAT is passed to the job and never written to the database.
    await enqueue(review.id, payload.access_token)
    return _to_detail(review, [], [])


@router.post("/upload", response_model=ReviewDetail, status_code=201)
async def upload_review(
    db: Session,
    file: Annotated[UploadFile, File(description="Zipped project")],
    options: Annotated[str | None, Form()] = None,
) -> ReviewDetail:
    """Start a review of an uploaded ZIP archive."""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip archives are accepted.")

    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Archive exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        )

    try:
        parsed = ReviewOptions.model_validate_json(options) if options else ReviewOptions()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid options: {exc}") from exc

    # Extract now so the worker has a real directory to walk, whichever
    # process picks the job up.
    try:
        workspace = await use_zip(data, file.filename or "upload.zip")
    except CodeReviewError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    review = Review(
        id=new_id(),
        source_type="zip",
        source_ref=file.filename or "upload.zip",
        display_name=workspace.display_name,
        status="queued",
        phase="queued",
        workspace_path=str(workspace.root),
        options=parsed.model_dump(),
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)

    await enqueue(review.id, None)
    return _to_detail(review, [], [])


# --------------------------------------------------------------------- read


@router.get("", response_model=list[ReviewSummary])
async def list_reviews(
    db: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: str | None = None,
) -> list[ReviewSummary]:
    stmt = select(Review).order_by(Review.created_at.desc()).limit(limit).offset(offset)
    if status:
        stmt = stmt.where(Review.status == status)
    rows = (await db.execute(stmt)).scalars().all()

    if not rows:
        return []

    ids = [r.id for r in rows]
    counts = await db.execute(
        select(Finding.review_id, Finding.severity, func.count())
        .where(Finding.review_id.in_(ids))
        .group_by(Finding.review_id, Finding.severity)
    )
    totals: dict[str, int] = {}
    criticals: dict[str, int] = {}
    for review_id, severity, count in counts.all():
        totals[review_id] = totals.get(review_id, 0) + count
        if severity == "critical":
            criticals[review_id] = criticals.get(review_id, 0) + count

    return [
        ReviewSummary(
            id=r.id,
            display_name=r.display_name,
            source_type=r.source_type,
            source_ref=r.source_ref,
            status=r.status,
            phase=r.phase,
            progress=r.progress,
            created_at=r.created_at,
            finished_at=r.finished_at,
            overall_score=(r.scores or {}).get("overall"),
            grade=(r.scores or {}).get("grade"),
            findings_count=totals.get(r.id, 0),
            critical_count=criticals.get(r.id, 0),
        )
        for r in rows
    ]


@router.get("/{review_id}", response_model=ReviewDetail)
async def get_review(review_id: str, db: Session) -> ReviewDetail:
    review = await _load_full(db, review_id)
    files = sorted(review.files, key=lambda f: (f.score if f.score is not None else 101))
    cross = [f for f in review.findings if f.is_cross_cutting]
    return _to_detail(review, files, cross)


@router.get("/{review_id}/findings", response_model=list[FindingRead])
async def list_findings(
    review_id: str,
    db: Session,
    severity: str | None = None,
    category: str | None = None,
) -> list[FindingRead]:
    stmt = select(Finding).where(Finding.review_id == review_id)
    if severity:
        stmt = stmt.where(Finding.severity == severity)
    if category:
        stmt = stmt.where(Finding.category == category)

    rows = (await db.execute(stmt)).scalars().all()
    rows.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 5), f.path or ""))
    return [FindingRead.model_validate(f) for f in rows]


@router.get("/{review_id}/events", response_model=list[EventRead])
async def list_events(
    review_id: str,
    db: Session,
    after: Annotated[int, Query(ge=0)] = 0,
) -> list[EventRead]:
    rows = (
        await db.execute(
            select(ReviewEvent)
            .where(ReviewEvent.review_id == review_id, ReviewEvent.seq > after)
            .order_by(ReviewEvent.seq)
        )
    ).scalars().all()
    return [EventRead.model_validate(e) for e in rows]


# ---------------------------------------------------------------- streaming


@router.get("/{review_id}/stream")
async def stream_review(review_id: str, request: Request, db: Session):
    """Server-Sent Events: replay what was missed, then stream live.

    Subscribing before the replay query closes the gap where an event lands
    between the two — duplicates are cheap (the client de-dupes on seq),
    dropped events are not.
    """
    review = await db.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")

    last_id = request.headers.get("last-event-id")
    try:
        after = int(last_id) if last_id else 0
    except ValueError:
        after = 0

    subscription = bus.subscribe(review_id)
    finished = review.status in _TERMINAL
    final_status = review.status

    async def generator():
        seen: set[int] = set()
        try:
            # A fresh session: the request-scoped one is torn down before a
            # streaming response body is consumed.
            async with session_scope() as stream_db:
                backlog = (
                    await stream_db.execute(
                        select(ReviewEvent)
                        .where(ReviewEvent.review_id == review_id, ReviewEvent.seq > after)
                        .order_by(ReviewEvent.seq)
                    )
                ).scalars().all()

            for event in backlog:
                seen.add(event.seq)
                yield {
                    "id": str(event.seq),
                    "event": event.type,
                    "data": json.dumps({
                        "seq": event.seq,
                        "type": event.type,
                        "agent": event.agent,
                        "message": event.message,
                        "payload": event.payload or {},
                        "created_at": event.created_at.isoformat(),
                    }),
                }

            if finished:
                yield {"event": "done", "data": json.dumps({"status": final_status})}
                return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        subscription.__anext__(), timeout=15.0
                    )
                except TimeoutError:
                    # Keep-alive: proxies drop idle SSE connections.
                    yield {"event": "ping", "data": "{}"}
                    continue
                except StopAsyncIteration:
                    break

                seq = event.get("seq")
                if seq in seen:
                    continue
                if isinstance(seq, int):
                    seen.add(seq)

                yield {
                    "id": str(seq),
                    "event": event.get("type", "message"),
                    "data": json.dumps(event, default=str),
                }

                if event.get("type") in (
                    "review_completed", "review_failed", "review_cancelled"
                ):
                    yield {"event": "done", "data": json.dumps({"status": event["type"]})}
                    break
        finally:
            await subscription.aclose()

    return EventSourceResponse(generator(), ping=15)


# -------------------------------------------------------------------- write


@router.post("/{review_id}/cancel", status_code=202)
async def cancel_review(review_id: str, db: Session) -> dict[str, Any]:
    review = await db.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.status in _TERMINAL:
        return {"status": review.status, "cancelled": False}

    review.status = "cancelled"
    review.phase = "cancelled"
    review.finished_at = datetime.now(UTC)
    review.error = "Cancelled by user."
    await db.commit()

    # Immediate stop if this process owns the task; otherwise the worker
    # notices at the next phase boundary.
    in_process = request_cancel(review_id)
    return {"status": "cancelled", "cancelled": True, "immediate": in_process}


@router.delete("/{review_id}", status_code=204, response_class=Response)
async def delete_review(review_id: str, db: Session) -> Response:
    review = await db.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    request_cancel(review_id)
    await db.execute(delete(Review).where(Review.id == review_id))
    await db.commit()
    return Response(status_code=204)


@router.get("/{review_id}/report.md", response_class=PlainTextResponse)
async def export_markdown(review_id: str, db: Session) -> PlainTextResponse:
    """Download the whole review as a Markdown document."""
    from app.api.v1.report import render_markdown

    review = await _load_full(db, review_id)
    text = render_markdown(review)
    filename = f"code-review-{review.display_name}-{review.id[:8]}.md"
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ------------------------------------------------------------------ helpers


async def _load_full(db: AsyncSession, review_id: str) -> Review:
    review = (
        await db.execute(
            select(Review)
            .where(Review.id == review_id)
            .options(
                selectinload(Review.files).selectinload(FileReport.findings),
                selectinload(Review.findings),
            )
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return review


def _to_detail(
    review: Review, files: list[FileReport], cross: list[Finding]
) -> ReviewDetail:
    return ReviewDetail(
        id=review.id,
        display_name=review.display_name,
        source_type=review.source_type,
        source_ref=review.source_ref,
        branch=review.branch,
        commit_sha=review.commit_sha,
        status=review.status,
        phase=review.phase,
        progress=review.progress,
        error=review.error,
        created_at=review.created_at,
        started_at=review.started_at,
        finished_at=review.finished_at,
        profile=review.profile,
        structure=review.structure,
        plan=review.plan,
        scores=review.scores,
        summary=review.summary,
        recommendations=review.recommendations,
        metrics=review.metrics,
        token_usage=review.token_usage,
        options=review.options,
        files=[
            _file_to_read(f) for f in files
        ],
        cross_cutting_findings=[FindingRead.model_validate(f) for f in cross],
    )


def _file_to_read(f: FileReport):
    from app.schemas.api import FileReportRead

    findings = sorted(
        f.findings, key=lambda x: (SEVERITY_ORDER.get(x.severity, 5), x.line_start or 0)
    )
    return FileReportRead(
        id=f.id,
        path=f.path,
        language=f.language,
        loc=f.loc,
        status=f.status,
        score=f.score,
        summary=f.summary,
        role=f.role,
        strengths=f.strengths,
        dimension_scores=f.dimension_scores,
        complexity=f.complexity,
        skip_reason=f.skip_reason,
        cache_hit=f.cache_hit,
        findings=[FindingRead.model_validate(x) for x in findings],
    )


def _derive_name(source_ref: str) -> str:
    ref = source_ref.strip().rstrip("/")
    if not ref:
        return "project"
    return ref.split("/")[-1].removesuffix(".git") or "project"
