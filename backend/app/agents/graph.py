"""LangGraph pipeline.

    ingest → profile_project → analyze_structure → plan_review → review_files
        ├→ cross_cutting → critique → verify_findings ─┐
        └──────────────────────────────────────────────┴→ synthesize → END

Node names deliberately differ from state keys ("profile_project" vs the
"profile" key) -- LangGraph rejects a node that shadows a state field.

Each node is one agent turn. Nodes return partial state; LangGraph merges it.
Conditional edges skip work that cannot produce anything useful (no reviewable
files, critic disabled, nothing to critique).
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.context import ReviewContext
from app.agents.critic import CriticAgent
from app.agents.crosscut import CrossCuttingAgent
from app.agents.persistence import update_review
from app.agents.planner import PlannerAgent
from app.agents.profiler import ProfilerAgent
from app.agents.reviewer import FileReviewerAgent
from app.agents.structure import StructureAgent
from app.agents.synthesizer import SynthesizerAgent
from app.core.logging import get_logger
from app.ingestion.source import Workspace
from app.ingestion.walker import RepoInventory, walk_repository
from app.proof.agent import ProofAgent
from app.proof.models import ProofResult, ProofStatus
from app.schemas.agent import (
    CrossCuttingOut,
    ProjectProfile,
    ReviewPlan,
    StructureAnalysis,
    SynthesisOut,
)
from app.scoring.rubric import aggregate_repo_score

log = get_logger(__name__)


class ReviewState(TypedDict, total=False):
    ctx: ReviewContext
    workspace: Workspace
    inventory: RepoInventory
    profile: ProjectProfile
    structure: StructureAnalysis
    plan: ReviewPlan
    file_rows: list[dict[str, Any]]
    cross: CrossCuttingOut
    all_findings: list[dict[str, Any]]
    kept_findings: list[dict[str, Any]]
    proofs: dict[str, ProofResult]
    synthesis: SynthesisOut
    scores: dict[str, Any]
    metrics: dict[str, Any]


_profiler = ProfilerAgent()
_structure = StructureAgent()
_planner = PlannerAgent()
_reviewer = FileReviewerAgent()
_crosscut = CrossCuttingAgent()
_critic = CriticAgent()
_synthesizer = SynthesizerAgent()
_prover = ProofAgent()


# --------------------------------------------------------------------- nodes


async def node_ingest(state: ReviewState) -> dict[str, Any]:
    ctx, ws = state["ctx"], state["workspace"]
    await ctx.set_phase("ingesting")

    inv = walk_repository(ws.root)

    await ctx.emit(
        "inventory_ready",
        agent="Ingestor",
        message=(
            f"Indexed {len(inv.files)} files "
            f"({inv.total_loc:,} lines), skipped {inv.skipped}"
        ),
        payload={
            "files": len(inv.files),
            "skipped": inv.skipped,
            "total_loc": inv.total_loc,
            "languages": dict(list(inv.language_breakdown.items())[:10]),
            "truncated": inv.truncated,
            "primary_language": inv.primary_language,
        },
    )
    return {"inventory": inv}


async def node_profile(state: ReviewState) -> dict[str, Any]:
    ctx, inv = state["ctx"], state["inventory"]
    await ctx.set_phase("profiling")

    profile = await _profiler.run(ctx, inv)
    await update_review(ctx.review_id, profile=profile.model_dump())
    await ctx.emit(
        "profile_ready",
        agent=_profiler.name,
        message=profile.one_liner,
        payload=profile.model_dump(),
    )
    return {"profile": profile}


async def node_structure(state: ReviewState) -> dict[str, Any]:
    ctx = state["ctx"]
    await ctx.set_phase("structure")

    structure = await _structure.run(ctx, state["inventory"], state["profile"])
    await update_review(ctx.review_id, structure=structure.model_dump())
    await ctx.emit(
        "structure_ready",
        agent=_structure.name,
        message=(
            f"Organised {structure.organization_pattern}; "
            f"{len(structure.concerns)} concern(s), "
            f"{len(structure.missing_pieces)} gap(s)"
        ),
        payload=structure.model_dump(),
    )
    return {"structure": structure}


async def node_plan(state: ReviewState) -> dict[str, Any]:
    ctx = state["ctx"]
    await ctx.set_phase("planning")

    max_files = int(ctx.options.get("max_files_to_review", 40))
    plan = await _planner.run(
        ctx, state["inventory"], state["profile"], state["structure"], max_files
    )
    await update_review(ctx.review_id, plan=plan.model_dump())
    await ctx.emit(
        "plan_ready",
        agent=_planner.name,
        message=f"Selected {len(plan.files)} files to review",
        payload=plan.model_dump(),
    )
    return {"plan": plan}


async def node_review(state: ReviewState) -> dict[str, Any]:
    ctx = state["ctx"]
    await ctx.set_phase("reviewing_files")

    rows = await _reviewer.run(
        ctx, state["inventory"], state["plan"].files, state["profile"], state["structure"]
    )

    # Stable ids let the critic reject individual findings by reference.
    findings: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        for j, finding in enumerate(row.get("findings", [])):
            finding["_id"] = f"f{i}-{j}"
            finding["is_cross_cutting"] = False
            findings.append(finding)

    reviewed = sum(1 for r in rows if r.get("status") == "reviewed")
    await ctx.emit(
        "files_reviewed",
        agent=_reviewer.name,
        message=f"Reviewed {reviewed} files, raised {len(findings)} candidate findings",
        payload={"reviewed": reviewed, "attempted": len(rows), "findings": len(findings)},
    )
    return {"file_rows": rows, "all_findings": findings}


async def node_crosscut(state: ReviewState) -> dict[str, Any]:
    ctx = state["ctx"]
    await ctx.set_phase("cross_cutting")

    cross = await _crosscut.run(
        ctx, state["file_rows"], state["profile"], state["structure"]
    )

    findings = list(state.get("all_findings", []))
    for k, finding in enumerate(cross.findings):
        raw = finding.model_dump()
        raw["_id"] = f"x-{k}"
        raw["is_cross_cutting"] = True
        raw["path"] = None
        findings.append(raw)

    await ctx.emit(
        "cross_cutting_ready",
        agent=_crosscut.name,
        message=f"Identified {len(cross.findings)} systemic issue(s)",
        payload={
            "count": len(cross.findings),
            "systemic_risks": cross.systemic_risks,
            "architecture_notes": cross.architecture_notes,
        },
    )
    return {"cross": cross, "all_findings": findings}


async def node_critique(state: ReviewState) -> dict[str, Any]:
    ctx = state["ctx"]
    await ctx.set_phase("critique")

    kept = await _critic.run(ctx, state.get("all_findings", []))
    return {"kept_findings": kept}


async def node_verify(state: ReviewState) -> dict[str, Any]:
    """Try to demonstrate what survived the critic.

    Runs after critique so sandbox time is only spent on findings that already
    withstood scrutiny. A finding the proof *disproves* is dropped outright —
    this is the step that removes noise rather than adding more.
    """
    ctx = state["ctx"]
    await ctx.set_phase("verifying")

    findings = state.get("kept_findings", state.get("all_findings", []))
    workspace: Workspace = state["workspace"]

    proofs = await _prover.verify_all(ctx, findings, workspace.root)

    surviving: list[dict[str, Any]] = []
    for finding in findings:
        proof = proofs.get(finding.get("_id"))
        if proof is None:
            finding.setdefault("proof_status", str(ProofStatus.NOT_ATTEMPTED))
            surviving.append(finding)
            continue
        if proof.is_refuted:
            log.info(
                "verify.dropped", title=finding.get("title"), reason=proof.summary
            )
            continue
        finding.update(proof.as_dict())
        if proof.is_proven:
            # A demonstrated finding is no longer a matter of confidence.
            finding["confidence"] = 1.0
            finding["verified"] = True
        surviving.append(finding)

    return {"kept_findings": surviving, "proofs": proofs}


async def node_synthesize(state: ReviewState) -> dict[str, Any]:
    ctx = state["ctx"]
    await ctx.set_phase("synthesizing")

    inv: RepoInventory = state["inventory"]
    rows = state.get("file_rows", [])
    kept = state.get("kept_findings", state.get("all_findings", []))
    cross = state.get("cross") or CrossCuttingOut()
    profile = state["profile"]
    structure = state["structure"]

    # Keep only the surviving findings on each row before scoring.
    kept_ids = {f.get("_id") for f in kept}
    for row in rows:
        row["findings"] = [f for f in row.get("findings", []) if f.get("_id") in kept_ids]

    reviewed = [r for r in rows if r.get("status") == "reviewed"]
    scored = [r["score"] for r in reviewed if r.get("score") is not None]
    worst = sorted(
        (r for r in reviewed if r.get("score") is not None),
        key=lambda r: r["score"],
    )[:5]

    metrics: dict[str, Any] = {
        "files_indexed": len(inv.files),
        "files_reviewed": len(reviewed),
        "files_planned": len(state["plan"].files),
        "total_loc": inv.total_loc,
        "test_ratio": round(inv.test_ratio, 3),
        "languages": dict(list(inv.language_breakdown.items())[:10]),
        "mean_file_score": round(sum(scored) / len(scored), 1) if scored else None,
        "worst_files": [r["path"] for r in worst],
        "total_findings": len(kept),
        "severity_counts": _severity_counts(kept),
        "inventory_truncated": inv.truncated,
    }

    synthesis = await _synthesizer.run(
        ctx, profile, structure, rows, kept, cross, metrics
    )

    proven = [f for f in kept if f.get("proof_status") == str(ProofStatus.PROVEN)]
    metrics["proven_findings"] = len(proven)
    metrics["proof_attempted"] = sum(
        1 for f in kept if f.get("proof_status") not in (None, str(ProofStatus.NOT_ATTEMPTED))
    )

    cross_kept = [f for f in kept if f.get("is_cross_cutting")]
    scores = aggregate_repo_score(
        rows,
        cross_kept,
        synthesis_scores={
            k: v for k, v in synthesis.scores.model_dump().items() if v is not None
        },
        architecture_score=synthesis.architecture_score,
        documentation_score=synthesis.documentation_score,
        test_ratio=inv.test_ratio,
    )

    return {
        "synthesis": synthesis,
        "scores": scores,
        "metrics": metrics,
        "file_rows": rows,
        "kept_findings": kept,
    }


# ------------------------------------------------------------------- routing


def route_after_review(state: ReviewState) -> str:
    """Skip systemic analysis when there is nothing to analyse."""
    rows = state.get("file_rows", [])
    if not any(r.get("status") == "reviewed" for r in rows):
        return "synthesize"
    return "cross_cutting"


def route_after_crosscut(state: ReviewState) -> str:
    ctx = state["ctx"]
    if not ctx.options.get("enable_critic", True):
        return "synthesize"
    if not state.get("all_findings"):
        return "synthesize"
    return "critique"


def _severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        key = str(f.get("severity", "medium"))
        counts[key] = counts.get(key, 0) + 1
    return counts


# --------------------------------------------------------------------- build


def build_graph():
    builder = StateGraph(ReviewState)

    builder.add_node("ingest", node_ingest)
    builder.add_node("profile_project", node_profile)
    builder.add_node("analyze_structure", node_structure)
    builder.add_node("plan_review", node_plan)
    builder.add_node("review_files", node_review)
    builder.add_node("cross_cutting", node_crosscut)
    builder.add_node("critique", node_critique)
    builder.add_node("verify_findings", node_verify)
    builder.add_node("synthesize", node_synthesize)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "profile_project")
    builder.add_edge("profile_project", "analyze_structure")
    builder.add_edge("analyze_structure", "plan_review")
    builder.add_edge("plan_review", "review_files")

    builder.add_conditional_edges(
        "review_files",
        route_after_review,
        {"cross_cutting": "cross_cutting", "synthesize": "synthesize"},
    )
    builder.add_conditional_edges(
        "cross_cutting",
        route_after_crosscut,
        {"critique": "critique", "synthesize": "synthesize"},
    )
    builder.add_edge("critique", "verify_findings")
    builder.add_edge("verify_findings", "synthesize")
    builder.add_edge("synthesize", END)

    return builder.compile()


review_graph = build_graph()
