"""Structured contracts every agent must fill.

These double as the JSON Schema handed to Groq, so field descriptions are
prompt engineering — keep them short, imperative and unambiguous.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AgentModel(BaseModel):
    """Base for every agent contract.

    Models routinely omit an optional field, or send an explicit ``null``
    where a list is expected. Both are harmless — the field has a default —
    but Pydantic rejects the second. Dropping null-valued keys before
    validation makes the whole pipeline tolerant of that, which matters
    because a single missing key would otherwise fail an entire review.
    """

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls_with_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        cleaned = {}
        for key, value in data.items():
            if value is None:
                field = cls.model_fields.get(key)
                # Keep the null only where the field genuinely allows it.
                if field is not None and not field.is_required():
                    continue
            cleaned[key] = value
        return cleaned


Severity = Literal["critical", "high", "medium", "low", "info"]
Category = Literal[
    "correctness",
    "security",
    "performance",
    "maintainability",
    "readability",
    "testing",
    "architecture",
    "documentation",
    "dependency",
    "style",
]

SEVERITY_WEIGHT: dict[str, float] = {
    "critical": 25.0, "high": 12.0, "medium": 5.0, "low": 1.5, "info": 0.0,
}
SEVERITY_ORDER: dict[str, int] = {
    "critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4,
}


# ---------------------------------------------------------------- turn 1


class ProjectProfile(AgentModel):
    """What this project actually is — produced before any code is reviewed."""

    name: str = Field(description="Project name")
    one_liner: str = Field(description="One sentence on what this project does")
    purpose: str = Field(description="2-4 sentences on the problem it solves")
    project_type: str = Field(
        description="e.g. web API, CLI tool, library, mobile app, data pipeline"
    )
    domain: str = Field(description="Business/technical domain")
    primary_language: str = ""
    frameworks: list[str] = Field(default_factory=list)
    key_technologies: list[str] = Field(default_factory=list)
    architecture_style: str = Field(
        default="", description="e.g. layered, MVC, microservice, monolith, event-driven"
    )
    entry_points: list[str] = Field(
        default_factory=list, description="Files that start the app"
    )
    maturity: Literal["prototype", "early", "developing", "mature", "unknown"] = "unknown"
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


# ---------------------------------------------------------------- turn 2


class ModuleNote(AgentModel):
    name: str
    path: str
    responsibility: str
    concerns: list[str] = Field(default_factory=list)


class StructureAnalysis(AgentModel):
    """How the codebase is organised, and whether that organisation holds up."""

    organization_pattern: str = Field(
        description="How the tree is arranged, e.g. by-layer, by-feature, flat"
    )
    layers: list[str] = Field(default_factory=list)
    modules: list[ModuleNote] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    coupling_assessment: str = ""
    missing_pieces: list[str] = Field(
        default_factory=list,
        description="Expected-but-absent things: tests, CI, docs, error handling",
    )
    hotspot_paths: list[str] = Field(
        default_factory=list, description="Paths most deserving deep review"
    )


# ---------------------------------------------------------------- turn 3


class PlannedFile(AgentModel):
    path: str
    reason: str = Field(description="Why this file matters to the review")
    focus: list[str] = Field(
        default_factory=list, description="Specific things to check in this file"
    )
    priority: int = Field(default=50, ge=0, le=100)


class ReviewPlan(AgentModel):
    strategy: str = Field(description="1-3 sentences on how this review is sequenced")
    files: list[PlannedFile] = Field(default_factory=list)
    global_concerns: list[str] = Field(
        default_factory=list, description="Cross-file things to watch for"
    )


# ---------------------------------------------------------------- turn 4


class FindingOut(AgentModel):
    title: str = Field(description="Short, specific problem statement")
    severity: Severity = "medium"
    category: Category = "maintainability"
    description: str = Field(description="What is wrong and why, concretely")
    impact: str = Field(default="", description="What breaks if left unfixed")
    suggestion: str = Field(default="", description="Concrete fix, actionable")
    suggested_patch: str | None = Field(
        default=None, description="Corrected code snippet, if short and unambiguous"
    )
    line_start: int | None = None
    line_end: int | None = None
    code_snippet: str | None = Field(
        default=None, description="The offending lines, verbatim and short"
    )
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("line_start", "line_end", mode="before")
    @classmethod
    def _coerce_line(cls, v):
        """Models sometimes emit '42', '42-50' or 0. Normalise to int|None."""
        if v in (None, "", 0, "0"):
            return None
        if isinstance(v, str):
            digits = "".join(ch for ch in v.split("-")[0] if ch.isdigit())
            return int(digits) if digits else None
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None


class DimensionScores(AgentModel):
    """0-100 per dimension. Absent dimensions are treated as not-applicable."""

    correctness: float | None = Field(default=None, ge=0, le=100)
    security: float | None = Field(default=None, ge=0, le=100)
    performance: float | None = Field(default=None, ge=0, le=100)
    maintainability: float | None = Field(default=None, ge=0, le=100)
    readability: float | None = Field(default=None, ge=0, le=100)
    testing: float | None = Field(default=None, ge=0, le=100)


class FileReviewOut(AgentModel):
    role: str = Field(description="What this file is responsible for")
    summary: str = Field(description="2-4 sentence verdict on the file")
    strengths: list[str] = Field(default_factory=list)
    findings: list[FindingOut] = Field(default_factory=list)
    scores: DimensionScores = Field(default_factory=DimensionScores)


class BatchFileReviewItem(AgentModel):
    """One file's verdict inside a batched review."""

    path: str = Field(description="Exact file path from the FILE marker")
    role: str = Field(description="What this file is responsible for")
    summary: str = Field(description="2-4 sentence verdict on the file")
    strengths: list[str] = Field(default_factory=list)
    findings: list[FindingOut] = Field(default_factory=list)
    scores: DimensionScores = Field(default_factory=DimensionScores)

    def as_review(self) -> FileReviewOut:
        return FileReviewOut(
            role=self.role, summary=self.summary,
            strengths=self.strengths, findings=self.findings, scores=self.scores,
        )


class BatchFileReviewOut(AgentModel):
    """Several small files reviewed in one call — the TPM-efficient path.

    A list rather than a path-keyed dict: strict constrained decoding cannot
    express arbitrary object keys, and without strict mode these reasoning
    models fail schema validation outright.
    """

    reviews: list[BatchFileReviewItem] = Field(
        description="One entry per file, in the order the files were given"
    )


# ---------------------------------------------------------------- turn 5


class CrossCuttingOut(AgentModel):
    """Problems that no single file review can see."""

    findings: list[FindingOut] = Field(default_factory=list)
    architecture_notes: str = ""
    systemic_risks: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- turn 6


class CriticVerdict(AgentModel):
    finding_id: str
    keep: bool = Field(description="False if the finding is wrong, vague or duplicated")
    reason: str = ""
    adjusted_severity: Severity | None = None


class CritiqueOut(AgentModel):
    verdicts: list[CriticVerdict] = Field(default_factory=list)
    overall_note: str = ""


# ---------------------------------------------------------------- turn 7


class Recommendation(AgentModel):
    title: str
    detail: str
    effort: Literal["trivial", "small", "medium", "large"] = "medium"
    impact: Literal["low", "medium", "high", "critical"] = "medium"
    category: Category = "maintainability"
    related_paths: list[str] = Field(default_factory=list)


class SynthesisOut(AgentModel):
    """The final report the user actually reads."""

    executive_summary: str = Field(description="4-8 sentences for a tech lead")
    what_the_project_does: str = ""
    overall_verdict: str = Field(description="One paragraph: ship it, or not, and why")
    scores: DimensionScores = Field(default_factory=DimensionScores)
    architecture_score: float | None = Field(default=None, ge=0, le=100)
    documentation_score: float | None = Field(default=None, ge=0, le=100)
    top_strengths: list[str] = Field(default_factory=list)
    top_risks: list[str] = Field(default_factory=list)
    quick_wins: list[Recommendation] = Field(default_factory=list)
    strategic_recommendations: list[Recommendation] = Field(default_factory=list)
