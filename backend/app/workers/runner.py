"""Drive one review end to end."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.context import ReviewContext
from app.agents.graph import review_graph
from app.agents.persistence import finalize, save_results, update_review
from app.core.errors import CodeReviewError, IngestionError
from app.core.logging import get_logger
from app.db.models import Review
from app.db.session import session_scope
from app.ingestion.source import Workspace, resolve_source
from app.llm.client import rotation_sink, usage_sink

log = get_logger(__name__)

# In-process handles so the API can cancel a run it started itself.
_running: dict[str, asyncio.Task] = {}


def is_running(review_id: str) -> bool:
    task = _running.get(review_id)
    return task is not None and not task.done()


def request_cancel(review_id: str) -> bool:
    task = _running.get(review_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


def register(review_id: str, task: asyncio.Task) -> None:
    _running[review_id] = task
    task.add_done_callback(lambda _: _running.pop(review_id, None))


async def run_review(review_id: str, access_token: str | None = None) -> None:
    """Resolve the source, run the agent graph, persist everything."""
    async with session_scope() as db:
        review = await db.get(Review, review_id)
        if review is None:
            log.error("runner.review_missing", review_id=review_id)
            return
        options = dict(review.options or {})
        source_type = review.source_type
        source_ref = review.source_ref
        branch = review.branch
        prepared_path = review.workspace_path
        display_name = review.display_name
        review.status = "running"
        review.started_at = datetime.now(UTC)
        review.error = None

    ctx = ReviewContext(review_id, options)
    usage_sink.set(ctx.record_usage)
    rotation_sink.set(ctx.record_rotation)

    workspace: Workspace | None = None
    try:
        await ctx.emit(
            "review_started",
            message="Review started",
            payload={"source_type": source_type, "options": options},
        )
        await ctx.set_phase("ingesting", "Fetching the codebase")

        if source_type == "zip":
            # The archive was extracted at upload time so that whichever
            # process runs the job finds a real directory waiting for it.
            if not prepared_path or not Path(prepared_path).is_dir():
                raise IngestionError(
                    "The uploaded archive is no longer available on disk. "
                    "Upload it again to re-run this review."
                )
            workspace = Workspace(
                root=Path(prepared_path),
                display_name=display_name,
                source_type="zip",
                source_ref=source_ref,
                ephemeral=True,
            )
        else:
            workspace = await resolve_source(
                source_type=source_type,
                source_ref=source_ref,
                branch=branch,
                token=access_token,
            )
        await update_review(
            review_id,
            display_name=workspace.display_name,
            branch=workspace.branch,
            commit_sha=workspace.commit_sha,
            workspace_path=str(workspace.root),
        )
        await ctx.emit(
            "source_ready",
            agent="Ingestor",
            message=f"Loaded {workspace.display_name}",
            payload={
                "display_name": workspace.display_name,
                "branch": workspace.branch,
                "commit": (workspace.commit_sha or "")[:8] or None,
            },
        )

        final_state: dict[str, Any] = await review_graph.ainvoke(
            {"ctx": ctx, "workspace": workspace},
            # One node per agent turn; the cap only guards against a cycle bug.
            {"recursion_limit": 40},
        )

        await save_results(
            review_id,
            file_rows=final_state.get("file_rows", []),
            kept_findings=final_state.get("kept_findings", []),
        )

        synthesis = final_state.get("synthesis")
        scores = final_state.get("scores") or {}
        recommendations = []
        summary = None
        if synthesis is not None:
            summary = synthesis.executive_summary
            recommendations = [
                {**r.model_dump(), "horizon": horizon}
                for horizon, items in (
                    ("quick_win", synthesis.quick_wins),
                    ("strategic", synthesis.strategic_recommendations),
                )
                for r in items
            ]
            scores = {
                **scores,
                "narrative": {
                    "what_the_project_does": synthesis.what_the_project_does,
                    "overall_verdict": synthesis.overall_verdict,
                    "top_strengths": synthesis.top_strengths,
                    "top_risks": synthesis.top_risks,
                },
            }

        await finalize(
            review_id,
            status="completed",
            scores=scores,
            summary=summary,
            recommendations=recommendations,
            metrics=final_state.get("metrics"),
            token_usage=ctx.ledger.as_dict(),
        )
        await ctx.emit(
            "review_completed",
            message="Review complete",
            payload={
                "overall_score": scores.get("overall"),
                "grade": scores.get("grade"),
                "verdict": scores.get("verdict"),
                "findings": scores.get("total_findings"),
                "tokens": ctx.ledger.total,
            },
        )
        log.info(
            "runner.completed",
            review_id=review_id,
            score=scores.get("overall"),
            tokens=ctx.ledger.total,
        )

    except asyncio.CancelledError:
        await finalize(
            review_id,
            status="cancelled",
            error="Cancelled by user.",
            token_usage=ctx.ledger.as_dict(),
        )
        await ctx.emit("review_cancelled", message="Review cancelled")
        log.info("runner.cancelled", review_id=review_id)
        raise

    except CodeReviewError as exc:
        # Keep the provider's own words: the summary says what to change, the
        # detail says what the API actually complained about.
        full = exc.message + (f"\n\n{exc.detail}" if exc.detail else "")
        await finalize(
            review_id,
            status="failed",
            error=full,
            token_usage=ctx.ledger.as_dict(),
        )
        await ctx.emit(
            "review_failed",
            message=exc.message,
            payload={"code": exc.code, "detail": exc.detail},
        )
        log.error(
            "runner.failed",
            review_id=review_id,
            code=exc.code,
            error=exc.message,
            detail=exc.detail,
        )

    except Exception as exc:
        log.exception("runner.crashed", review_id=review_id)
        await finalize(
            review_id,
            status="failed",
            error=f"Unexpected error: {exc}",
            token_usage=ctx.ledger.as_dict(),
        )
        await ctx.emit("review_failed", message=f"Unexpected error: {exc}")

    finally:
        if workspace is not None:
            workspace.cleanup()
