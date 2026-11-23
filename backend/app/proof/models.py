from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ProofStatus(StrEnum):
    """How much we can actually stand behind a finding."""

    PROVEN = "proven"
    """Demonstrated: a deterministic trace or an executed proof showed the flaw."""

    REFUTED = "refuted"
    """The proof ran and the flaw did not occur. The finding is dropped."""

    INCONCLUSIVE = "inconclusive"
    """Could not be exercised — missing dependencies, needs a running app."""

    NOT_ATTEMPTED = "not_attempted"
    """Out of budget, or no strategy covers this category."""


# Only these carry weight in the headline verdict.
TRUSTWORTHY = {ProofStatus.PROVEN}


@dataclass
class ProofResult:
    """The outcome of trying to demonstrate one finding."""

    status: ProofStatus
    kind: str = ""
    """Which strategy produced this — e.g. `command_injection_canary`."""

    summary: str = ""
    """One line a non-engineer can read."""

    evidence: str = ""
    """What actually happened: the canary that appeared, the row that leaked."""

    script: str | None = None
    """The proof source, so a human can re-run it."""

    output: str | None = None
    """Captured stdout/stderr, truncated."""

    duration_ms: int = 0
    executed: bool = False
    """True when a sandbox ran; false for purely static proofs."""

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_proven(self) -> bool:
        return self.status is ProofStatus.PROVEN

    @property
    def is_refuted(self) -> bool:
        return self.status is ProofStatus.REFUTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "proof_status": str(self.status),
            "proof_kind": self.kind or None,
            "proof_summary": self.summary or None,
            "proof_evidence": self.evidence or None,
            "proof_script": (self.script or None),
            "proof_output": (self.output or None),
            "proof_executed": self.executed,
            "proof_duration_ms": self.duration_ms,
        }

    # -- constructors that read well at the call site ----------------------

    @classmethod
    def proven(cls, kind: str, summary: str, evidence: str, **kw: Any) -> ProofResult:
        return cls(ProofStatus.PROVEN, kind=kind, summary=summary, evidence=evidence, **kw)

    @classmethod
    def refuted(cls, kind: str, summary: str, **kw: Any) -> ProofResult:
        return cls(ProofStatus.REFUTED, kind=kind, summary=summary, **kw)

    @classmethod
    def inconclusive(cls, kind: str, summary: str, **kw: Any) -> ProofResult:
        return cls(ProofStatus.INCONCLUSIVE, kind=kind, summary=summary, **kw)

    @classmethod
    def skipped(cls, summary: str = "No proof strategy for this finding.") -> ProofResult:
        return cls(ProofStatus.NOT_ATTEMPTED, kind="none", summary=summary)
