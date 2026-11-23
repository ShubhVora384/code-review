"""Render a completed review as a portable Markdown document."""

from __future__ import annotations

from typing import Any

from app.db.models import Review
from app.schemas.agent import SEVERITY_ORDER

_SEVERITY_ICON = {
    "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪",
}


def render_markdown(review: Review) -> str:
    scores: dict[str, Any] = review.scores or {}
    metrics: dict[str, Any] = review.metrics or {}
    profile: dict[str, Any] = review.profile or {}
    structure: dict[str, Any] = review.structure or {}
    narrative: dict[str, Any] = scores.get("narrative") or {}

    out: list[str] = [
        f"# Code Review — {review.display_name}",
        "",
        f"> {profile.get('one_liner') or 'Automated agentic code review'}",
        "",
        f"- **Source**: `{review.source_ref}` ({review.source_type})",
    ]
    if review.branch:
        out.append(f"- **Branch**: `{review.branch}`")
    if review.commit_sha:
        out.append(f"- **Commit**: `{review.commit_sha[:12]}`")
    out += [
        f"- **Status**: {review.status}",
        f"- **Reviewed**: {review.finished_at or review.created_at:%Y-%m-%d %H:%M UTC}",
        "",
    ]

    # ---- the answer the reader actually came for ----
    launch = scores.get("launch_verdict") or {}
    if launch:
        icon = {
            "do_not_launch": "🛑", "fix_first": "⚠️",
            "launch_with_care": "✅", "ready": "✅",
        }.get(launch.get("decision", ""), "")
        out += [
            f"## {icon} {launch.get('headline', '')}",
            "",
            launch.get("reason", ""),
            "",
        ]

    proven = [f for f in review.findings if f.proof_status == "proven"]
    if proven:
        out += [
            f"## Demonstrated vulnerabilities ({len(proven)})",
            "",
            "Each of these was **reproduced against your code**, not merely "
            "suspected. The evidence is what the check actually observed.",
            "",
        ]
        for f in sorted(proven, key=lambda x: SEVERITY_ORDER.get(x.severity, 5)):
            out += _render_proven(f)

    # ---- scorecard ----
    if scores:
        out += [
            "## Scorecard",
            "",
            f"### Overall: {scores.get('overall', 'n/a')}/100 "
            f"(**{scores.get('grade', '?')}**) — {scores.get('verdict', '')}",
            "",
            "| Dimension | Score | Weight |",
            "|---|---:|---:|",
        ]
        weights = scores.get("weights", {})
        for dim, value in (scores.get("dimensions") or {}).items():
            out.append(
                f"| {dim.title()} | {value} | {weights.get(dim, 0) * 100:.0f}% |"
            )
        out.append("")

        counts = scores.get("severity_counts") or {}
        if any(counts.values()):
            out += [
                "**Findings by severity**: "
                + " · ".join(
                    f"{_SEVERITY_ICON.get(k, '')} {k} {v}"
                    for k, v in counts.items() if v
                ),
                "",
            ]

    # ---- summary ----
    if review.summary:
        out += ["## Executive summary", "", review.summary, ""]
    if narrative.get("what_the_project_does"):
        out += ["## What this project does", "", narrative["what_the_project_does"], ""]
    if narrative.get("overall_verdict"):
        out += ["## Verdict", "", narrative["overall_verdict"], ""]

    if narrative.get("top_strengths"):
        out += ["## Strengths", ""]
        out += [f"- {s}" for s in narrative["top_strengths"]]
        out.append("")
    if narrative.get("top_risks"):
        out += ["## Key risks", ""]
        out += [f"- {r}" for r in narrative["top_risks"]]
        out.append("")

    # ---- project + structure ----
    if profile:
        out += [
            "## Project profile",
            "",
            f"- **Type**: {profile.get('project_type', 'unknown')}",
            f"- **Domain**: {profile.get('domain', 'unknown')}",
            f"- **Primary language**: {profile.get('primary_language', 'unknown')}",
            f"- **Frameworks**: {', '.join(profile.get('frameworks') or []) or 'none detected'}",
            f"- **Architecture**: {profile.get('architecture_style') or 'unspecified'}",
            f"- **Maturity**: {profile.get('maturity', 'unknown')}",
            "",
            profile.get("purpose", ""),
            "",
        ]

    if structure:
        out += ["## Structure analysis", "",
                f"**Organisation**: {structure.get('organization_pattern', 'n/a')}", ""]
        for label, key in (
            ("Strengths", "strengths"),
            ("Concerns", "concerns"),
            ("Missing pieces", "missing_pieces"),
        ):
            items = structure.get(key) or []
            if items:
                out += [f"**{label}**", ""]
                out += [f"- {i}" for i in items]
                out.append("")

    # ---- recommendations ----
    recs = review.recommendations or []
    quick = [r for r in recs if r.get("horizon") == "quick_win"]
    strategic = [r for r in recs if r.get("horizon") == "strategic"]

    for title, group in (("Quick wins", quick), ("Strategic recommendations", strategic)):
        if not group:
            continue
        out += [f"## {title}", ""]
        for r in group:
            out += [
                f"### {r.get('title')}",
                "",
                f"- **Impact**: {r.get('impact')} | **Effort**: {r.get('effort')} "
                f"| **Category**: {r.get('category')}",
            ]
            if r.get("related_paths"):
                out.append(
                    "- **Files**: " + ", ".join(f"`{p}`" for p in r["related_paths"])
                )
            out += ["", r.get("detail", ""), ""]

    # ---- cross-cutting ----
    cross = [f for f in review.findings if f.is_cross_cutting]
    if cross:
        out += ["## Systemic findings", ""]
        for f in sorted(cross, key=lambda x: SEVERITY_ORDER.get(x.severity, 5)):
            out += _render_finding(f, heading="###")

    # ---- per-file ----
    files = sorted(
        review.files, key=lambda f: (f.score if f.score is not None else 101)
    )
    if files:
        out += ["## File reviews", "",
                "| File | Score | Findings | Language |", "|---|---:|---:|---|"]
        for f in files:
            out.append(
                f"| `{f.path}` | {f.score if f.score is not None else '—'} "
                f"| {len(f.findings)} | {f.language or '—'} |"
            )
        out.append("")

        for f in files:
            if f.status != "reviewed":
                continue
            out += [f"### `{f.path}`", ""]
            if f.role:
                out += [f"**Role**: {f.role}", ""]
            out += [
                f"**Score**: {f.score}/100" + (
                    "  ·  " + " · ".join(
                        f"{k}: {v}" for k, v in (f.dimension_scores or {}).items()
                    ) if f.dimension_scores else ""
                ),
                "",
            ]
            if f.summary:
                out += [f.summary, ""]
            if f.strengths:
                out += ["**Strengths**", ""] + [f"- {s}" for s in f.strengths] + [""]
            for finding in sorted(
                f.findings, key=lambda x: SEVERITY_ORDER.get(x.severity, 5)
            ):
                out += _render_finding(finding, heading="####")

    # ---- run metadata ----
    usage = review.token_usage or {}
    out += [
        "---",
        "",
        "## Run metadata",
        "",
        f"- Files indexed: {metrics.get('files_indexed', '—')}",
        f"- Files reviewed: {metrics.get('files_reviewed', '—')}",
        f"- Lines of code: {metrics.get('total_loc', 0):,}",
        f"- Test-file ratio: {(metrics.get('test_ratio') or 0) * 100:.0f}%",
        f"- LLM calls: {usage.get('calls', 0)} "
        f"({usage.get('cache_hits', 0)} served from cache)",
        f"- Tokens consumed: {usage.get('total_tokens', 0):,}",
        "",
        "_Generated by the AI Code Review System._",
    ]
    return "\n".join(out)


def _render_proven(f: Any) -> list[str]:
    """A demonstrated finding leads with the evidence, not the theory."""
    icon = _SEVERITY_ICON.get(f.severity, "")
    where = f"`{f.path}`" if f.path else "system-wide"
    if f.line_start:
        where += f" line {f.line_start}"

    block = [
        f"### {icon} {f.title}",
        "",
        f"**{f.severity.upper()}** · {where}"
        + ("  ·  reproduced by running it" if f.proof_executed else "  ·  traced in the code"),
        "",
    ]
    if f.proof_evidence:
        block += ["> **What we observed**", "> ", f"> {f.proof_evidence}", ""]
    if f.description:
        block += [f.description, ""]
    if f.impact:
        block += [f"**Why it matters**: {f.impact}", ""]
    if f.suggestion:
        block += [f"**How to fix it**: {f.suggestion}", ""]
    if f.suggested_patch:
        block += ["```", f.suggested_patch.strip(), "```", ""]
    return block


def _render_finding(f: Any, *, heading: str) -> list[str]:
    icon = _SEVERITY_ICON.get(f.severity, "")
    location = f"`{f.path}`" if f.path else "system-wide"
    if f.line_start:
        location += f" line {f.line_start}"
        if f.line_end and f.line_end != f.line_start:
            location += f"–{f.line_end}"

    proof_note = {
        "proven": "  ·  ✓ demonstrated",
        "refuted": "",  # never persisted
        "inconclusive": "  ·  could not be demonstrated automatically",
        "not_attempted": "",
    }.get(getattr(f, "proof_status", "not_attempted"), "")

    block = [
        f"{heading} {icon} {f.title}",
        "",
        f"**{f.severity.upper()}** · {f.category} · {location} "
        f"· confidence {f.confidence:.0%}"
        + ("  ✓ verified" if f.verified else "")
        + proof_note,
        "",
        f.description,
        "",
    ]
    if getattr(f, "proof_evidence", None):
        block += [f"**Evidence**: {f.proof_evidence}", ""]
    if f.code_snippet:
        block += ["```", f.code_snippet.strip(), "```", ""]
    if f.impact:
        block += [f"**Impact**: {f.impact}", ""]
    if f.suggestion:
        block += [f"**Suggested fix**: {f.suggestion}", ""]
    if f.suggested_patch:
        block += ["**Proposed change**", "", "```", f.suggested_patch.strip(), "```", ""]
    return block
