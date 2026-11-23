"""Deterministic scoring.

The model supplies per-dimension judgements; this module turns them into a
number. Keeping the arithmetic out of the LLM makes scores reproducible and
stops one dramatic file from dominating the repository grade.
"""

from __future__ import annotations

import math
from typing import Any

from app.schemas.agent import SEVERITY_WEIGHT

# Repository-level weighting. Correctness and security dominate.
DIMENSION_WEIGHTS: dict[str, float] = {
    "correctness": 0.26,
    "security": 0.22,
    "maintainability": 0.16,
    "architecture": 0.12,
    "testing": 0.10,
    "readability": 0.07,
    "performance": 0.05,
    "documentation": 0.02,
}

GRADE_BANDS: list[tuple[float, str]] = [
    (90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F"),
]

# A file can only lose so much to sheer finding count.
_MAX_FINDING_PENALTY = 45.0


def grade_for(score: float) -> str:
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


def verdict_for(score: float) -> str:
    if score >= 90:
        return "Production ready"
    if score >= 80:
        return "Solid, minor work needed"
    if score >= 70:
        return "Workable, notable gaps"
    if score >= 60:
        return "Needs significant work"
    return "Major rework required"


# How much a finding counts toward the score, by how well we can stand behind
# it. A demonstrated flaw carries full weight; one we could not exercise is
# still worth reporting but should not dominate the grade.
PROOF_WEIGHT: dict[str, float] = {
    "proven": 1.3,
    "inconclusive": 0.75,
    # Not discounted heavily: these already survived the critic, they simply
    # have no automated experiment yet.
    "not_attempted": 0.85,
    "refuted": 0.0,
}

# A demonstrated flaw is a fact, not a judgement, so it caps the grade
# regardless of how well the rest of the codebase scores. Without this a
# report can say "do not launch" next to a C, which reads as noise.
PROVEN_SEVERITY_CEILING: dict[str, float] = {"critical": 45.0, "high": 62.0}


def finding_penalty(findings: list[dict[str, Any]]) -> float:
    """Weighted, confidence- and proof-scaled, diminishing-returns penalty.

    Ten medium issues should not sink a file as hard as ten criticals, and the
    eleventh medium should hurt less than the first — hence the damping. A
    finding we actually demonstrated counts for twice one we merely suspect.
    """
    if not findings:
        return 0.0
    raw = 0.0
    for f in findings:
        weight = SEVERITY_WEIGHT.get(str(f.get("severity", "medium")), 5.0)
        confidence = float(f.get("confidence") or 0.7)
        proof = PROOF_WEIGHT.get(str(f.get("proof_status") or "not_attempted"), 0.5)
        raw += weight * max(0.3, min(confidence, 1.0)) * proof
    damped = _MAX_FINDING_PENALTY * (1 - math.exp(-raw / 30.0))
    return round(min(damped, _MAX_FINDING_PENALTY), 2)


def compute_file_score(
    dimension_scores: dict[str, Any] | None,
    findings: list[dict[str, Any]],
) -> float:
    """Blend the model's dimension scores with the severity of what it found."""
    dims = {
        k: float(v)
        for k, v in (dimension_scores or {}).items()
        if v is not None and isinstance(v, (int, float))
    }
    if dims:
        total_w = sum(DIMENSION_WEIGHTS.get(k, 0.05) for k in dims)
        base = sum(v * DIMENSION_WEIGHTS.get(k, 0.05) for k, v in dims.items()) / total_w
    else:
        base = 75.0

    # The model's own scores already reflect some of the findings, so apply
    # only part of the penalty again — enough to separate clean from messy.
    score = base - finding_penalty(findings) * 0.6
    return round(max(0.0, min(100.0, score)), 1)


def _loc_weighted_mean(pairs: list[tuple[float, int]]) -> float | None:
    """Weight by sqrt(loc): big files matter more, but not linearly more."""
    usable = [(s, max(w, 1)) for s, w in pairs if s is not None]
    if not usable:
        return None
    num = sum(s * math.sqrt(w) for s, w in usable)
    den = sum(math.sqrt(w) for _, w in usable)
    return round(num / den, 1) if den else None


def aggregate_repo_score(
    file_rows: list[dict[str, Any]],
    cross_cutting: list[dict[str, Any]],
    *,
    synthesis_scores: dict[str, Any] | None = None,
    architecture_score: float | None = None,
    documentation_score: float | None = None,
    test_ratio: float = 0.0,
) -> dict[str, Any]:
    """Roll per-file results plus systemic findings into the final scorecard."""
    dims: dict[str, float | None] = {}

    for dim in ("correctness", "security", "performance", "maintainability",
                "readability", "testing"):
        pairs = [
            (
                (r.get("dimension_scores") or {}).get(dim),
                int(r.get("loc") or 1),
            )
            for r in file_rows
            if r.get("dimension_scores")
        ]
        pairs = [(float(s), w) for s, w in pairs if isinstance(s, (int, float))]
        dims[dim] = _loc_weighted_mean(pairs)

    # Fall back to the synthesiser's own read where per-file data is thin.
    for dim, value in (synthesis_scores or {}).items():
        if dims.get(dim) is None and isinstance(value, (int, float)):
            dims[dim] = float(value)

    dims["architecture"] = architecture_score
    dims["documentation"] = documentation_score

    # No tests found anywhere is a hard fact, not a judgement call.
    if not test_ratio:
        dims["testing"] = min(dims.get("testing") or 30.0, 30.0)

    present = {k: v for k, v in dims.items() if v is not None}
    if present:
        total_w = sum(DIMENSION_WEIGHTS.get(k, 0.05) for k in present)
        overall = sum(v * DIMENSION_WEIGHTS.get(k, 0.05) for k, v in present.items()) / total_w
    else:
        overall = 70.0

    # Systemic issues hit the whole repository, not one file.
    overall -= finding_penalty(cross_cutting) * 0.5
    overall = round(max(0.0, min(100.0, overall)), 1)

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for row in file_rows:
        for f in row.get("findings", []):
            counts[str(f.get("severity", "medium"))] = counts.get(
                str(f.get("severity", "medium")), 0
            ) + 1
    for f in cross_cutting:
        counts[str(f.get("severity", "medium"))] = counts.get(
            str(f.get("severity", "medium")), 0
        ) + 1

    every = _all_findings(file_rows, cross_cutting)
    proven_by_severity = [
        str(f.get("severity")) for f in every
        if str(f.get("proof_status")) == "proven"
    ]
    proven = len(proven_by_severity)
    proven_critical = sum(
        1 for s in proven_by_severity if s in ("critical", "high")
    )

    # Demonstrated flaws set a hard ceiling on the grade.
    for severity, ceiling in PROVEN_SEVERITY_CEILING.items():
        if severity in proven_by_severity:
            overall = min(overall, ceiling)
            break

    return {
        "overall": overall,
        "grade": grade_for(overall),
        "verdict": verdict_for(overall),
        "launch_verdict": launch_verdict(proven_critical, counts),
        "dimensions": {k: v for k, v in dims.items() if v is not None},
        "weights": DIMENSION_WEIGHTS,
        "severity_counts": counts,
        "total_findings": sum(counts.values()),
        "proven_findings": proven,
        "proven_critical": proven_critical,
    }


def _all_findings(
    file_rows: list[dict[str, Any]], cross_cutting: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [f for row in file_rows for f in row.get("findings", [])] + list(cross_cutting)


def launch_verdict(proven_critical: int, counts: dict[str, int]) -> dict[str, Any]:
    """The one-line answer a founder is actually asking for.

    Driven by what was *demonstrated*, not by what was merely suspected —
    telling someone not to launch should require evidence.
    """
    if proven_critical:
        noun = "vulnerability was" if proven_critical == 1 else "vulnerabilities were"
        return {
            "decision": "do_not_launch",
            "headline": "Do not launch yet",
            "reason": (
                f"{proven_critical} serious {noun} demonstrated against your "
                "code, not just suspected."
            ),
        }
    if counts.get("critical") or counts.get("high"):
        return {
            "decision": "fix_first",
            "headline": "Fix these before launching",
            "reason": (
                "Serious issues were identified but could not be automatically "
                "demonstrated. Have someone confirm them by hand."
            ),
        }
    if counts.get("medium"):
        return {
            "decision": "launch_with_care",
            "headline": "Safe to launch, with follow-ups",
            "reason": "Nothing critical surfaced. The medium items are worth planning in.",
        }
    return {
        "decision": "ready",
        "headline": "Nothing serious found",
        "reason": "No critical or high-severity issues surfaced in the reviewed files.",
    }
