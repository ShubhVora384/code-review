"""Turn 7 — the report a human actually reads."""

from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import ReviewContext
from app.agents.prompts import SYNTHESIS_PROMPT
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.llm.tokens import truncate_to_tokens
from app.schemas.agent import (
    SEVERITY_ORDER,
    CrossCuttingOut,
    ProjectProfile,
    StructureAnalysis,
    SynthesisOut,
)

log = get_logger(__name__)

_EVIDENCE_BUDGET = 3_000


class SynthesizerAgent(BaseAgent):
    name = "Synthesizer"
    tier = "deep"
    temperature = 0.25
    max_tokens = 3200

    async def run(
        self,
        ctx: ReviewContext,
        profile: ProjectProfile,
        structure: StructureAnalysis,
        file_rows: list[dict[str, Any]],
        findings: list[dict[str, Any]],
        cross: CrossCuttingOut,
        metrics: dict[str, Any],
    ) -> SynthesisOut:
        evidence = truncate_to_tokens(
            self._evidence(file_rows, findings), _EVIDENCE_BUDGET
        )

        user = f"""\
## Project profile
Name: {profile.name}
One-liner: {profile.one_liner}
Purpose: {profile.purpose}
Type: {profile.project_type} | Domain: {profile.domain}
Stack: {', '.join(profile.frameworks + profile.key_technologies) or 'unknown'}
Maturity: {profile.maturity}

## Structure
Organisation: {structure.organization_pattern}
Strengths: {'; '.join(structure.strengths[:6]) or 'none noted'}
Concerns: {'; '.join(structure.concerns[:6]) or 'none noted'}
Missing: {'; '.join(structure.missing_pieces[:8]) or 'none noted'}

## Systemic analysis
{cross.architecture_notes or '(none)'}
Systemic risks: {'; '.join(cross.systemic_risks[:8]) or 'none identified'}

## Computed metrics
- Files reviewed: {metrics.get('files_reviewed')} of {metrics.get('files_indexed')} indexed
- Lines of code: {metrics.get('total_loc'):,}
- Test-file ratio: {metrics.get('test_ratio', 0):.0%}
- Verified findings: {metrics.get('total_findings')}
- By severity: {metrics.get('severity_counts')}
- Mean file score: {metrics.get('mean_file_score')}
- Lowest-scoring files: {', '.join(metrics.get('worst_files', [])) or 'n/a'}

## Evidence
{evidence}

Write the report. Ground every claim in the evidence above.
"""
        try:
            return await self.ask(ctx, SYNTHESIS_PROMPT, user, SynthesisOut)
        except LLMError as exc:
            log.warning("synthesis.failed", error=exc.message)
            return self._fallback(profile, metrics, exc.message)

    @staticmethod
    def _evidence(
        file_rows: list[dict[str, Any]], findings: list[dict[str, Any]]
    ) -> str:
        ranked = sorted(
            findings, key=lambda f: SEVERITY_ORDER.get(str(f.get("severity")), 5)
        )[:35]

        finding_lines = "\n".join(
            f"- [{f.get('severity')}/{f.get('category')}] "
            f"{f.get('path') or 'system-wide'}"
            f"{':' + str(f['line_start']) if f.get('line_start') else ''} — "
            f"{f.get('title')} :: {str(f.get('suggestion') or '')[:180]}"
            for f in ranked
        ) or "- (no findings survived verification)"

        reviewed = [r for r in file_rows if r.get("status") == "reviewed"]
        file_lines = "\n".join(
            f"- {r['path']} (score {r.get('score')}): {str(r.get('summary') or '')[:200]}"
            for r in sorted(reviewed, key=lambda r: (r.get("score") or 100))[:25]
        ) or "- (no files reviewed)"

        strengths = {s for r in reviewed for s in (r.get("strengths") or [])}

        return f"""### Verified findings, most severe first
{finding_lines}

### File verdicts, lowest score first
{file_lines}

### Strengths observed across files
{chr(10).join(f'- {s}' for s in list(strengths)[:15]) or '- (none recorded)'}"""

    @staticmethod
    def _fallback(
        profile: ProjectProfile, metrics: dict[str, Any], error: str
    ) -> SynthesisOut:
        """A degraded but honest report if the final call fails."""
        return SynthesisOut(
            executive_summary=(
                f"{profile.name} was reviewed across "
                f"{metrics.get('files_reviewed', 0)} files and "
                f"{metrics.get('total_findings', 0)} findings were verified. "
                f"The narrative synthesis step could not complete ({error}), so "
                "the per-file results and scorecard below are the authoritative "
                "output for this run."
            ),
            what_the_project_does=profile.purpose or profile.one_liner,
            overall_verdict=(
                "Automated synthesis was unavailable. Review the per-file "
                "findings directly — they completed successfully."
            ),
        )
