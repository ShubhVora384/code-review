"""Turn 6 — challenge every finding before it reaches the user.

False positives are the fastest way to make a review tool untrusted, so each
finding is re-examined against its own quoted evidence.
"""

from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import ReviewContext
from app.agents.prompts import CRITIC_PROMPT
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.schemas.agent import CritiqueOut

log = get_logger(__name__)

# Keep each critique call small — at 8K TPM a huge batch stalls the pipeline.
_BATCH_SIZE = 8


class CriticAgent(BaseAgent):
    name = "Critic"
    tier = "deep"
    temperature = 0.0
    max_tokens = 2800

    async def run(
        self, ctx: ReviewContext, findings: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return the findings that survive, with severities adjusted."""
        if not findings:
            return []

        # Only re-litigate claims that carry weight or look shaky.
        candidates = [
            f for f in findings
            if f.get("severity") in ("critical", "high", "medium")
            or float(f.get("confidence") or 0.7) < 0.6
        ]
        passthrough = [f for f in findings if f not in candidates]

        if not candidates:
            return findings

        verdicts: dict[str, Any] = {}
        for start in range(0, len(candidates), _BATCH_SIZE):
            chunk = candidates[start : start + _BATCH_SIZE]
            ctx.check_cancelled()
            verdicts |= await self._critique_chunk(ctx, chunk)

        kept: list[dict[str, Any]] = []
        rejected = 0

        for finding in candidates:
            verdict = verdicts.get(finding["_id"])
            if verdict is None:
                finding["verified"] = False  # never critiqued; show unverified
                kept.append(finding)
                continue
            if not verdict.keep:
                rejected += 1
                continue
            finding["verified"] = True
            finding["critic_note"] = verdict.reason or None
            if verdict.adjusted_severity:
                finding["severity"] = verdict.adjusted_severity
            kept.append(finding)

        await ctx.emit(
            "critique_complete",
            agent=self.name,
            message=(
                f"Verified {len(kept)} findings, discarded {rejected} as "
                "unsupported or duplicated"
            ),
            payload={
                "kept": len(kept),
                "rejected": rejected,
                "not_critiqued": len(passthrough),
            },
        )
        return kept + passthrough

    async def _critique_chunk(
        self, ctx: ReviewContext, chunk: list[dict[str, Any]]
    ) -> dict[str, Any]:
        listing = "\n\n".join(
            f"""### id: {f['_id']}
File: {f.get('path') or '(cross-cutting)'}  lines {f.get('line_start')}-{f.get('line_end')}
Severity claimed: {f.get('severity')} | Category: {f.get('category')}
Title: {f.get('title')}
Description: {str(f.get('description'))[:600]}
Evidence quoted by the reviewer:
{str(f.get('code_snippet') or '(no code was quoted)')[:500]}"""
            for f in chunk
        )

        user = f"""\
Assess these {len(chunk)} findings. Return exactly one verdict per id.

{listing}
"""
        try:
            out = await self.ask(
                ctx, CRITIC_PROMPT, user, CritiqueOut,
                detail={"batch_size": len(chunk)},
            )
        except LLMError as exc:
            log.warning("critic.chunk_failed", error=exc.message)
            return {}

        return {v.finding_id: v for v in out.verdicts}
