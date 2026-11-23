"""Turn 5 — systemic issues no single-file review can see."""

from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import ReviewContext
from app.agents.prompts import CROSS_CUTTING_PROMPT, REVIEWER_PERSONA
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.llm.tokens import truncate_to_tokens
from app.schemas.agent import (
    SEVERITY_ORDER,
    CrossCuttingOut,
    ProjectProfile,
    StructureAnalysis,
)

log = get_logger(__name__)

_DIGEST_BUDGET = 3_000


class CrossCuttingAgent(BaseAgent):
    name = "SystemsAuditor"
    tier = "deep"
    max_tokens = 2800

    async def run(
        self,
        ctx: ReviewContext,
        file_rows: list[dict[str, Any]],
        profile: ProjectProfile,
        structure: StructureAnalysis,
    ) -> CrossCuttingOut:
        if not file_rows:
            return CrossCuttingOut()

        digest = truncate_to_tokens(self._digest(file_rows), _DIGEST_BUDGET)

        user = f"""\
## Project
{profile.name} — {profile.one_liner}
Type: {profile.project_type} | Stack: {', '.join(profile.frameworks) or 'unknown'}

## Structure analysis
Organisation: {structure.organization_pattern}
Layers: {', '.join(structure.layers) or 'not clearly layered'}
Concerns raised: {'; '.join(structure.concerns[:8]) or 'none'}
Missing pieces: {'; '.join(structure.missing_pieces[:8]) or 'none'}
Coupling: {structure.coupling_assessment or 'not assessed'}

## Per-file review digest ({len(file_rows)} files)
{digest}

Identify only issues that span multiple files or describe the system as a whole.
"""
        try:
            return await self.ask(
                ctx, REVIEWER_PERSONA + "\n\n" + CROSS_CUTTING_PROMPT, user, CrossCuttingOut
            )
        except LLMError as exc:
            log.warning("crosscut.failed", error=exc.message)
            return CrossCuttingOut(
                architecture_notes=f"Cross-cutting analysis unavailable: {exc.message}"
            )

    @staticmethod
    def _digest(file_rows: list[dict[str, Any]]) -> str:
        """Condense every file review into a few lines each."""
        blocks: list[str] = []
        for row in sorted(file_rows, key=lambda r: (r.get("score") or 100)):
            if row.get("status") != "reviewed":
                continue
            findings = sorted(
                row.get("findings", []),
                key=lambda f: SEVERITY_ORDER.get(str(f.get("severity")), 5),
            )[:4]
            lines = [
                f"### {row['path']} (score {row.get('score')}, {row.get('language')})",
                f"Role: {row.get('role') or 'unknown'}",
                f"Verdict: {(row.get('summary') or '')[:280]}",
            ]
            if findings:
                lines.append("Findings:")
                lines += [
                    f"  - [{f.get('severity')}/{f.get('category')}] {f.get('title')}"
                    for f in findings
                ]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks) if blocks else "(no files were successfully reviewed)"
