"""The agent that tries to demonstrate a finding.

The important property here is *who decides*. The model designs an experiment;
the sandbox runs it; the verdict is read from what the run actually printed and
from the process exit state. The model never gets to declare its own finding
proven.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import ReviewContext
from app.core.config import settings
from app.core.errors import BudgetExhaustedError, LLMError
from app.core.logging import get_logger
from app.llm.client import llm
from app.proof.models import ProofResult, ProofStatus
from app.proof.sandbox import SandboxUnavailableError, get_sandbox
from app.proof.strategies import (
    NOT_VULNERABLE,
    VULNERABLE,
    Target,
    build_proof_prompt,
    interpret,
    recipe_for,
    static_strategy_for,
)

log = get_logger(__name__)

TARGET_MODULE = "target_module.py"

# Severities worth spending sandbox time on.
_WORTH_PROVING = {"critical", "high"}

PROOF_PERSONA = """\
You design a single, minimal experiment that determines whether a reported
vulnerability is real.

You are not asked to agree or disagree with the report. You are asked to build
something that will show the answer either way, and to report honestly what it
shows. A script that concludes NOT_VULNERABLE is a perfectly good result — a
false alarm caught here is worth more than a confirmed bug.

Write plain Python 3.12 using only the standard library. The code under test is
importable as `target_module`. Keep it under 60 lines.
"""

# A proof that rewrites the code it is testing proves nothing.
_TAMPER_PATTERNS = [
    re.compile(r"""open\s*\(\s*["'][^"']*target_module\.py["']\s*,\s*["'][wax]"""),
    re.compile(r"""(?:Path\s*\(\s*)?["'][^"']*target_module\.py["']\s*\)?\s*\.\s*write_text"""),
    re.compile(r"""shutil\.(?:copy|move)\s*\([^)]*target_module"""),
    re.compile(r"""target_module\.\w+\s*=\s*(?:lambda|def\b)"""),
    re.compile(r"""setattr\s*\(\s*target_module"""),
]


def _looks_like_tampering(script: str) -> str | None:
    for pattern in _TAMPER_PATTERNS:
        if pattern.search(script):
            return pattern.pattern
    return None


def _strip_fences(text: str) -> str:
    fenced = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    body = fenced.group(1) if fenced else text
    return body.strip()


class ProofAgent(BaseAgent):
    name = "ProofEngine"
    tier = "deep"
    temperature = 0.0
    max_tokens = 1800

    async def verify_all(
        self,
        ctx: ReviewContext,
        findings: list[dict[str, Any]],
        root: Path,
    ) -> dict[str, ProofResult]:
        """Attempt a proof for each finding worth proving."""
        results: dict[str, ProofResult] = {}
        if not settings.proof_enabled or not findings:
            return results

        candidates = [
            f for f in findings
            if str(f.get("severity", "")).lower() in _WORTH_PROVING and f.get("path")
        ]
        # Most severe first, so a budget cut-off loses the least important ones.
        candidates.sort(key=lambda f: 0 if f.get("severity") == "critical" else 1)
        budget = candidates[: settings.proof_max_per_review]

        skipped = len(candidates) - len(budget)
        await ctx.emit(
            "proof_started",
            agent=self.name,
            message=f"Attempting to prove {len(budget)} finding(s)",
            payload={
                "attempting": len(budget),
                "over_budget": skipped,
                "total_findings": len(findings),
            },
        )

        sandbox = None
        try:
            sandbox = await get_sandbox()
        except SandboxUnavailableError as exc:
            log.warning("proof.sandbox_unavailable", error=exc.message)
            await ctx.emit(
                "proof_sandbox_unavailable",
                agent=self.name,
                message=exc.message,
                payload={"executed_proofs": False},
            )

        semaphore = asyncio.Semaphore(max(1, settings.max_concurrent_file_reviews))

        async def _one(finding: dict[str, Any]) -> None:
            async with semaphore:
                ctx.check_cancelled()
                try:
                    result = await self._verify(ctx, finding, root, sandbox)
                except asyncio.CancelledError:
                    raise
                except BudgetExhaustedError as exc:
                    result = ProofResult.inconclusive(
                        kind="budget", summary=f"Token budget exhausted: {exc.message}"
                    )
                except Exception as exc:
                    log.warning(
                        "proof.failed", title=finding.get("title"), error=str(exc)[:200]
                    )
                    result = ProofResult.inconclusive(
                        kind="error", summary=f"Proof could not run: {exc}"
                    )
                results[finding["_id"]] = result
                await ctx.emit(
                    "proof_result",
                    agent=self.name,
                    message=f"{result.status.upper()}: {finding.get('title', '')[:70]}",
                    payload={
                        "finding_id": finding["_id"],
                        "path": finding.get("path"),
                        "status": str(result.status),
                        "kind": result.kind,
                        "executed": result.executed,
                        "summary": result.summary,
                    },
                    persist=False,
                )

        await asyncio.gather(*(_one(f) for f in budget), return_exceptions=True)

        proven = sum(1 for r in results.values() if r.is_proven)
        refuted = sum(1 for r in results.values() if r.is_refuted)
        await ctx.emit(
            "proof_complete",
            agent=self.name,
            message=(
                f"Proved {proven} finding(s); disproved {refuted}. "
                f"{len(budget) - proven - refuted} could not be exercised."
            ),
            payload={"proven": proven, "refuted": refuted, "attempted": len(budget)},
        )
        return results

    # ------------------------------------------------------------------ one

    async def _verify(
        self,
        ctx: ReviewContext,
        finding: dict[str, Any],
        root: Path,
        sandbox: Any,
    ) -> ProofResult:
        rel_path = str(finding.get("path") or "")
        source_file = (root / rel_path).resolve()
        try:
            source_file.relative_to(root.resolve())
        except ValueError:
            return ProofResult.inconclusive(
                kind="path", summary="Finding points outside the workspace."
            )
        if not source_file.is_file():
            return ProofResult.inconclusive(
                kind="path", summary=f"{rel_path} is no longer present."
            )

        source = source_file.read_text("utf-8", errors="replace")
        target = Target(
            path=rel_path,
            source=source,
            category=str(finding.get("category") or ""),
            severity=str(finding.get("severity") or ""),
            title=str(finding.get("title") or ""),
            description=str(finding.get("description") or ""),
            line_start=finding.get("line_start"),
            code_snippet=finding.get("code_snippet"),
        )

        # Deterministic first: free, and stronger than any generated script.
        static = static_strategy_for(target)
        if static is not None:
            result = static(target)
            if result.status is not ProofStatus.INCONCLUSIVE:
                return result

        if sandbox is None:
            return ProofResult.inconclusive(
                kind="no_sandbox",
                summary="No sandbox available, so this could not be executed.",
            )
        if target.language != "Python":
            return ProofResult.inconclusive(
                kind="language",
                summary="Executed proofs currently support Python only.",
            )

        recipe = recipe_for(target)
        if recipe is None:
            return ProofResult.skipped(
                "This kind of finding has no automated experiment yet."
            )

        return await self._execute(ctx, target, recipe, source, sandbox)

    async def _execute(
        self,
        ctx: ReviewContext,
        target: Target,
        recipe: str,
        source: str,
        sandbox: Any,
    ) -> ProofResult:
        try:
            reply = await llm.text(
                [
                    {"role": "system", "content": PROOF_PERSONA},
                    {"role": "user", "content": build_proof_prompt(target, recipe)},
                ],
                tier=self.tier,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                agent=self.name,
                review_id=ctx.review_id,
                use_cache=True,
            )
        except LLMError as exc:
            return ProofResult.inconclusive(
                kind=recipe, summary=f"Could not design an experiment: {exc.message}"
            )

        script = _strip_fences(reply.content)
        if not script or "import" not in script:
            return ProofResult.inconclusive(
                kind=recipe, summary="The generated experiment was not usable Python."
            )

        tamper = _looks_like_tampering(script)
        if tamper:
            log.warning("proof.tamper_rejected", path=target.path, pattern=tamper)
            return ProofResult.inconclusive(
                kind=recipe,
                summary=(
                    "Rejected: the experiment tried to modify or monkeypatch the "
                    "code under test, which would prove nothing."
                ),
            )

        before = hashlib.sha256(source.encode()).hexdigest()
        run = await sandbox.run(
            script,
            files={TARGET_MODULE: source},
            timeout=settings.proof_timeout_sec,
            network=False,
            read_back=[TARGET_MODULE],
        )

        after_source = run.files_after.get(TARGET_MODULE, source)
        if hashlib.sha256(after_source.encode()).hexdigest() != before:
            log.warning("proof.target_modified", path=target.path)
            return ProofResult.inconclusive(
                kind=recipe,
                summary=(
                    "Rejected: the code under test was modified during the run, "
                    "so the result cannot be trusted."
                ),
                script=script,
                output=run.output,
                executed=True,
                duration_ms=run.duration_ms,
            )

        if run.timed_out:
            return ProofResult.inconclusive(
                kind=recipe,
                summary=f"The experiment timed out after {settings.proof_timeout_sec}s.",
                script=script,
                output=run.output,
                executed=True,
                duration_ms=run.duration_ms,
            )

        verdict, evidence = interpret(run.output, run.exit_code)

        if verdict == VULNERABLE:
            return ProofResult.proven(
                kind=f"executed:{recipe}",
                summary=target.title,
                evidence=evidence or "The experiment reproduced the reported flaw.",
                script=script,
                output=run.output,
                executed=True,
                duration_ms=run.duration_ms,
            )
        if verdict == NOT_VULNERABLE:
            return ProofResult.refuted(
                kind=f"executed:{recipe}",
                summary=evidence or "The experiment ran and the flaw did not occur.",
                script=script,
                output=run.output,
                executed=True,
                duration_ms=run.duration_ms,
            )

        # No verdict marker: the script crashed or misbehaved. That is not
        # evidence either way, so it stays unproven rather than being counted.
        return ProofResult.inconclusive(
            kind=f"executed:{recipe}",
            summary=(
                "The experiment did not report a verdict "
                f"(exit code {run.exit_code})."
            ),
            script=script,
            output=run.output,
            executed=True,
            duration_ms=run.duration_ms,
        )


proof_agent = ProofAgent()
