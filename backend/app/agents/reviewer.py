"""Turn 4 — review each planned file.

At 8K tokens/minute this phase is the budget bottleneck, so it does three
things to stretch it:
  * groups small files into one batched call
  * routes low-risk files to the faster, higher-TPM model tier
  * hashes file content so an unchanged file is never re-reviewed
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import ReviewContext
from app.agents.prompts import BATCH_REVIEW_PROMPT, FILE_REVIEW_PROMPT, REVIEWER_PERSONA
from app.core.config import settings
from app.core.errors import BudgetExhaustedError, LLMError
from app.core.logging import get_logger
from app.ingestion.walker import FileEntry, RepoInventory
from app.llm.key_pool import key_pool
from app.llm.tokens import estimate_tokens, truncate_to_tokens
from app.schemas.agent import (
    BatchFileReviewOut,
    FileReviewOut,
    PlannedFile,
    ProjectProfile,
    StructureAnalysis,
)
from app.scoring.rubric import compute_file_score
from app.tools.static_analysis import analyze, number_lines

log = get_logger(__name__)

# A file this small is cheaper to review alongside its neighbours.
# The token ceiling is deliberately conservative: prompt AND completion have to
# fit inside one per-minute window (8K on the free tier), and a batch that is
# too fat gets rejected outright rather than throttled.
_BATCH_LOC_CEILING = 120
_BATCH_TOKEN_CEILING = 1_800
_BATCH_MAX_FILES = 2

# Per-file prompt ceiling. Far under the 131K context window: the real
# constraint is tokens-per-minute, since prompt + completion must fit one
# window or Groq rejects the request.
_SINGLE_FILE_TOKEN_CEILING = 3_500

# Below this priority a file goes to the fast tier to save deep-tier budget.
_FAST_TIER_PRIORITY = 45
_FAST_TIER_MAX_LOC = 150


@dataclass
class FileRow:
    """Persisted per-file result."""

    path: str
    language: str
    loc: int
    size_bytes: int
    status: str = "reviewed"
    priority: int = 0
    score: float | None = None
    summary: str | None = None
    role: str | None = None
    strengths: list[str] = field(default_factory=list)
    dimension_scores: dict[str, Any] = field(default_factory=dict)
    complexity: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    skip_reason: str | None = None
    content_hash: str | None = None
    model_used: str | None = None
    cache_hit: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "loc": self.loc,
            "size_bytes": self.size_bytes,
            "status": self.status,
            "priority": self.priority,
            "score": self.score,
            "summary": self.summary,
            "role": self.role,
            "strengths": self.strengths,
            "dimension_scores": self.dimension_scores,
            "complexity": self.complexity,
            "findings": self.findings,
            "skip_reason": self.skip_reason,
            "content_hash": self.content_hash,
            "model_used": self.model_used,
            "cache_hit": self.cache_hit,
        }


class FileReviewerAgent(BaseAgent):
    name = "FileReviewer"
    tier = "deep"
    max_tokens = 3000

    async def run(
        self,
        ctx: ReviewContext,
        inv: RepoInventory,
        plan: list[PlannedFile],
        profile: ProjectProfile,
        structure: StructureAnalysis,
    ) -> list[dict[str, Any]]:
        by_path = {f.rel_path: f for f in inv.files}
        targets: list[tuple[FileEntry, PlannedFile]] = [
            (by_path[pf.path], pf) for pf in plan if pf.path in by_path
        ]
        if not targets:
            return []

        singles, batches = self._partition(targets)
        total_units = len(singles) + len(batches)
        done = 0
        rows: list[FileRow] = []

        await ctx.emit(
            "review_plan_ready",
            agent=self.name,
            message=(
                f"Reviewing {len(targets)} files "
                f"({len(singles)} individually, "
                f"{sum(len(b) for b in batches)} grouped into {len(batches)} batches)"
            ),
            payload={
                "total_files": len(targets),
                "single_calls": len(singles),
                "batch_calls": len(batches),
            },
        )

        # More in-flight calls than usable keys only builds rate-limit backlog:
        # each key can serve roughly one request per TPM window.
        usable = max(1, sum(1 for k in key_pool.keys if not k.is_exhausted()))
        concurrency = max(1, min(settings.max_concurrent_file_reviews, usable))
        sem = asyncio.Semaphore(concurrency)
        lock = asyncio.Lock()

        async def _emit_progress(label: str, extra: dict) -> None:
            nonlocal done
            async with lock:
                done += 1
                frac = done / max(total_units, 1)
            span = 0.78 - 0.36  # reviewing_files phase occupies this band
            await ctx.progress(0.36 + span * frac, label, extra)

        async def _do_single(entry: FileEntry, pf: PlannedFile) -> None:
            async with sem:
                ctx.check_cancelled()
                try:
                    row = await self._review_single(ctx, entry, pf, profile, structure)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # A file that could not be reviewed is still reported, with
                    # the reason. Dropping it would silently shrink the report.
                    row = _failed_row(entry, pf, exc)
                    log.warning(
                        "reviewer.file_failed", path=entry.rel_path, error=str(exc)[:200]
                    )
                async with lock:
                    rows.append(row)
                await _emit_progress(
                    f"Reviewed {entry.rel_path}",
                    {
                        "path": entry.rel_path,
                        "score": row.score,
                        "findings": len(row.findings),
                        "status": row.status,
                    },
                )

        async def _do_batch(group: list[tuple[FileEntry, PlannedFile]]) -> None:
            async with sem:
                ctx.check_cancelled()
                try:
                    produced = await self._review_batch(ctx, group, profile, structure)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    produced = [_failed_row(e, pf, exc) for e, pf in group]
                    log.warning(
                        "reviewer.batch_failed",
                        paths=[e.rel_path for e, _ in group], error=str(exc)[:200],
                    )
                async with lock:
                    rows.extend(produced)
                reviewed = [r for r in produced if r.status == "reviewed"]
                await _emit_progress(
                    f"Reviewed {len(reviewed)} of {len(produced)} small files",
                    {
                        "paths": [r.path for r in produced],
                        "findings": sum(len(r.findings) for r in produced),
                    },
                )

        tasks = [_do_single(e, p) for e, p in singles]
        tasks += [_do_batch(g) for g in batches]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for exc in results:
            if isinstance(exc, asyncio.CancelledError):
                raise exc
            if isinstance(exc, Exception):  # already recorded on the row
                log.warning("reviewer.task_failed", error=str(exc)[:200])

        failed = [r for r in rows if r.status != "reviewed"]
        if failed:
            await ctx.emit(
                "files_skipped",
                agent=self.name,
                message=f"{len(failed)} file(s) could not be reviewed",
                payload={
                    "count": len(failed),
                    "reasons": sorted({(r.skip_reason or "unknown")[:120] for r in failed}),
                    "paths": [r.path for r in failed],
                },
            )

        order = {pf.path: i for i, pf in enumerate(plan)}
        rows.sort(key=lambda r: order.get(r.path, 9_999))
        return [r.as_dict() for r in rows]

    # ------------------------------------------------------------ batching

    def _partition(
        self, targets: list[tuple[FileEntry, PlannedFile]]
    ) -> tuple[list[tuple[FileEntry, PlannedFile]], list[list[tuple[FileEntry, PlannedFile]]]]:
        singles: list[tuple[FileEntry, PlannedFile]] = []
        smalls: list[tuple[FileEntry, PlannedFile]] = []

        for entry, pf in targets:
            if entry.loc <= _BATCH_LOC_CEILING:
                smalls.append((entry, pf))
            else:
                singles.append((entry, pf))

        batches: list[list[tuple[FileEntry, PlannedFile]]] = []
        current: list[tuple[FileEntry, PlannedFile]] = []
        budget = 0

        for entry, pf in smalls:
            cost = estimate_tokens_for_file(entry)
            if current and (
                budget + cost > _BATCH_TOKEN_CEILING or len(current) >= _BATCH_MAX_FILES
            ):
                batches.append(current)
                current, budget = [], 0
            current.append((entry, pf))
            budget += cost

        if current:
            # A lone leftover is better reviewed properly on its own.
            if len(current) == 1:
                singles.append(current[0])
            else:
                batches.append(current)

        return singles, batches

    @staticmethod
    def _tier_for(entry: FileEntry, pf: PlannedFile) -> str:
        """Route unimportant small files to the higher-TPM fast model."""
        sensitive = any(
            k in entry.rel_path.lower()
            for k in ("auth", "security", "crypt", "password", "token",
                      "payment", "admin", "session", "login")
        )
        if sensitive or entry.loc > _FAST_TIER_MAX_LOC:
            return "deep"
        if pf.priority < _FAST_TIER_PRIORITY and entry.priority < _FAST_TIER_PRIORITY:
            return "fast"
        return "deep"

    # -------------------------------------------------------- single file

    async def _review_single(
        self,
        ctx: ReviewContext,
        entry: FileEntry,
        pf: PlannedFile,
        profile: ProjectProfile,
        structure: StructureAnalysis,
    ) -> FileRow:
        row = FileRow(
            path=entry.rel_path,
            language=entry.language,
            loc=entry.loc,
            size_bytes=entry.size_bytes,
            priority=pf.priority,
        )

        source = _read(entry)
        if source is None:
            row.status = "skipped"
            row.skip_reason = "File could not be read as UTF-8 text."
            return row

        row.content_hash = hashlib.sha256(source.encode("utf-8", "ignore")).hexdigest()
        metrics = analyze(source, entry.language)
        row.complexity = metrics

        body = truncate_to_tokens(number_lines(source), _SINGLE_FILE_TOKEN_CEILING)
        tier = self._tier_for(entry, pf)

        user = f"""\
{_project_context(profile, structure)}

## File under review
Path: {entry.rel_path}
Language: {entry.language} | {entry.loc} lines of code
Why it was selected: {pf.reason}
Specific things to check: {', '.join(pf.focus) or 'general review'}

## Computed metrics (facts, already verified — do not re-derive)
{_fmt_metrics(metrics)}

## Source
```
{body}
```
"""
        try:
            out = await self.ask(
                ctx,
                REVIEWER_PERSONA + "\n\n" + FILE_REVIEW_PROMPT,
                user,
                FileReviewOut,
                tier=tier,
                detail={"path": entry.rel_path, "tier": tier},
            )
        except BudgetExhaustedError:
            raise
        except LLMError as exc:
            row.status = "failed"
            row.skip_reason = f"Review failed: {exc.message}"
            log.warning("reviewer.file_failed", path=entry.rel_path, error=exc.message)
            return row

        _fill_row(row, out, entry.rel_path)
        row.model_used = tier
        return row

    # --------------------------------------------------------- batch call

    async def _review_batch(
        self,
        ctx: ReviewContext,
        group: list[tuple[FileEntry, PlannedFile]],
        profile: ProjectProfile,
        structure: StructureAnalysis,
    ) -> list[FileRow]:
        rows: dict[str, FileRow] = {}
        blocks: list[str] = []

        for entry, pf in group:
            row = FileRow(
                path=entry.rel_path,
                language=entry.language,
                loc=entry.loc,
                size_bytes=entry.size_bytes,
                priority=pf.priority,
            )
            rows[entry.rel_path] = row

            source = _read(entry)
            if source is None:
                row.status = "skipped"
                row.skip_reason = "File could not be read as UTF-8 text."
                continue

            row.content_hash = hashlib.sha256(
                source.encode("utf-8", "ignore")
            ).hexdigest()
            metrics = analyze(source, entry.language)
            row.complexity = metrics

            blocks.append(
                f"===== FILE: {entry.rel_path} =====\n"
                f"Language: {entry.language} | {entry.loc} loc\n"
                f"Check for: {', '.join(pf.focus) or 'general review'}\n"
                f"Metrics: {_fmt_metrics(metrics, compact=True)}\n"
                f"```\n{truncate_to_tokens(number_lines(source), 1100)}\n```"
            )

        if not blocks:
            return list(rows.values())

        user = f"""\
{_project_context(profile, structure)}

## Files under review ({len(blocks)})
{chr(10).join(blocks)}
"""
        try:
            out = await self.ask(
                ctx,
                REVIEWER_PERSONA + "\n\n" + BATCH_REVIEW_PROMPT,
                user,
                BatchFileReviewOut,
                max_tokens=3500,
                detail={"paths": list(rows), "batch": True},
            )
        except BudgetExhaustedError:
            raise
        except LLMError as exc:
            # Batching is an optimisation, not a requirement. A batch schema
            # spanning several files is where constrained decoding is most
            # likely to fail, so fall back to the un-optimised path rather
            # than losing every file in the group.
            log.warning(
                "reviewer.batch_fallback_to_single",
                paths=[e.rel_path for e, _ in group], error=exc.message[:160],
            )
            await ctx.emit(
                "batch_split",
                agent=self.name,
                message=(
                    f"Batch of {len(group)} failed schema validation — "
                    "reviewing those files individually"
                ),
                payload={"paths": [e.rel_path for e, _ in group]},
                persist=False,
            )
            recovered: list[FileRow] = []
            for entry, pf in group:
                try:
                    recovered.append(
                        await self._review_single(ctx, entry, pf, profile, structure)
                    )
                except BudgetExhaustedError:
                    raise
                except Exception as inner:
                    recovered.append(_failed_row(entry, pf, inner))
            return recovered

        for item in out.reviews:
            path = item.path.strip().lstrip("./")
            row = rows.get(path)
            if row is None:
                # Tolerate a basename echo from the model.
                matches = [p for p in rows if p.endswith("/" + path)]
                if len(matches) != 1:
                    log.debug("reviewer.batch_unknown_path", path=item.path)
                    continue
                row = rows[matches[0]]
            _fill_row(row, item.as_review(), row.path)
            row.model_used = "deep"

        for row in rows.values():
            if row.status == "reviewed" and row.score is None:
                row.status = "skipped"
                row.skip_reason = "The model returned no verdict for this file."
        return list(rows.values())


# ------------------------------------------------------------------ helpers


def _failed_row(entry: FileEntry, pf: PlannedFile, exc: Exception) -> FileRow:
    """A placeholder row so a failure is visible rather than a silent omission."""
    reason = getattr(exc, "message", None) or str(exc)
    return FileRow(
        path=entry.rel_path,
        language=entry.language,
        loc=entry.loc,
        size_bytes=entry.size_bytes,
        priority=pf.priority,
        status="failed",
        skip_reason=f"{type(exc).__name__}: {reason}"[:400],
    )


def estimate_tokens_for_file(entry: FileEntry) -> int:
    try:
        return estimate_tokens(entry.abs_path.read_text("utf-8", errors="ignore"))
    except OSError:
        return entry.size_bytes // 3


def _read(entry: FileEntry) -> str | None:
    try:
        text = entry.abs_path.read_text("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        try:
            text = entry.abs_path.read_text("utf-8", errors="replace")
        except OSError:
            return None
    return text if text.strip() else None


def _fill_row(row: FileRow, out: FileReviewOut, path: str) -> None:
    row.role = out.role
    row.summary = out.summary
    row.strengths = out.strengths[:8]
    row.dimension_scores = {
        k: v for k, v in out.scores.model_dump().items() if v is not None
    }
    row.findings = [
        {**f.model_dump(), "path": path} for f in out.findings
    ]
    row.score = compute_file_score(row.dimension_scores, row.findings)
    row.status = "reviewed"


def _project_context(profile: ProjectProfile, structure: StructureAnalysis) -> str:
    return f"""\
## Project context
{profile.name} — {profile.one_liner}
Type: {profile.project_type} | Domain: {profile.domain}
Stack: {', '.join(profile.frameworks + profile.key_technologies) or 'unknown'}
Architecture: {structure.organization_pattern or profile.architecture_style or 'unknown'}
Maturity: {profile.maturity}

Judge this file against what is appropriate for THIS project, not an abstract ideal."""


def _fmt_metrics(metrics: dict[str, Any], *, compact: bool = False) -> str:
    keep_compact = {
        "code_lines", "max_complexity", "bare_excepts", "possible_secrets",
        "parse_error", "todo_markers",
    }
    items = []
    for key, value in metrics.items():
        if compact and key not in keep_compact:
            continue
        if value in (None, [], {}, 0, ""):
            continue
        if isinstance(value, list) and len(value) > 5:
            value = [*value[:5], "…"]
        items.append(f"- {key}: {value}")
    return "\n".join(items) if items else "- (no notable metrics)"
