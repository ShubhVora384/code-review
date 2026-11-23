"""Turn 3 — decide which files to review, in what order, looking for what."""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.agents.context import ReviewContext
from app.agents.prompts import PLANNER_PROMPT
from app.core.logging import get_logger
from app.ingestion.walker import FileEntry, RepoInventory
from app.schemas.agent import PlannedFile, ProjectProfile, ReviewPlan, StructureAnalysis

log = get_logger(__name__)

# How many candidates to show the planner. More context costs tokens; beyond
# a few hundred the heuristic ordering is already doing the real work.
_MAX_CANDIDATES = 90


class PlannerAgent(BaseAgent):
    name = "ReviewPlanner"
    tier = "deep"
    max_tokens = 3000

    async def run(
        self,
        ctx: ReviewContext,
        inv: RepoInventory,
        profile: ProjectProfile,
        structure: StructureAnalysis,
        max_files: int,
    ) -> ReviewPlan:
        candidates = self._candidates(inv, structure, ctx)
        if not candidates:
            return ReviewPlan(strategy="No reviewable source files found.", files=[])

        listing = "\n".join(
            f"- {f.rel_path} | {f.language} | {f.loc} loc"
            f"{' | TEST' if f.is_test else ''}"
            f"{' | MANIFEST' if f.is_manifest else ''}"
            for f in candidates
        )

        focus = ctx.options.get("focus_areas") or []
        user = f"""\
## Project
{profile.name} — {profile.one_liner}
Type: {profile.project_type} | Stack: {', '.join(profile.frameworks) or 'n/a'}

## Structure analysis
Organisation: {structure.organization_pattern}
Concerns: {'; '.join(structure.concerns[:6]) or 'none noted'}
Missing: {'; '.join(structure.missing_pieces[:6]) or 'none noted'}
Flagged hotspots: {', '.join(structure.hotspot_paths[:12]) or 'none'}

## User focus areas
{', '.join(focus) if focus else 'none specified — use your own judgement'}

## Candidate files ({len(candidates)} shown, ordered by heuristic priority)
{listing}

Select at most {max_files} files.
"""
        plan = await self.ask(
            ctx,
            PLANNER_PROMPT.format(max_files=max_files),
            user,
            ReviewPlan,
        )

        plan.files = self._reconcile(plan.files, candidates, max_files)
        return plan

    def _candidates(
        self, inv: RepoInventory, structure: StructureAnalysis, ctx: ReviewContext
    ) -> list[FileEntry]:
        include_tests = ctx.options.get("include_tests", True)
        files = [
            f for f in inv.reviewable_files if include_tests or not f.is_test
        ]

        # Promote anything the structure agent called a hotspot.
        hot = set(structure.hotspot_paths)
        for f in files:
            if f.rel_path in hot or any(
                f.rel_path.startswith(h.rstrip("/") + "/") for h in hot
            ):
                f.priority += 45

        files.sort(key=lambda f: (-f.priority, f.rel_path))
        return files[:_MAX_CANDIDATES]

    @staticmethod
    def _reconcile(
        planned: list[PlannedFile], candidates: list[FileEntry], max_files: int
    ) -> list[PlannedFile]:
        """Drop hallucinated paths, de-duplicate, and backfill from heuristics."""
        by_path = {f.rel_path: f for f in candidates}
        seen: set[str] = set()
        kept: list[PlannedFile] = []

        for pf in planned:
            path = pf.path.strip().lstrip("./")
            if path not in by_path:
                # Tolerate a basename or suffix match before discarding.
                matches = [p for p in by_path if p.endswith("/" + path) or p == path]
                if len(matches) != 1:
                    log.debug("planner.unknown_path", path=pf.path)
                    continue
                path = matches[0]
            if path in seen:
                continue
            seen.add(path)
            pf.path = path
            kept.append(pf)
            if len(kept) >= max_files:
                break

        if len(kept) < max_files:
            for f in candidates:
                if len(kept) >= max_files:
                    break
                if f.rel_path in seen:
                    continue
                seen.add(f.rel_path)
                kept.append(
                    PlannedFile(
                        path=f.rel_path,
                        reason="Included by priority heuristic to fill the review budget.",
                        focus=["correctness", "error handling", "security"],
                        priority=max(0, min(100, f.priority)),
                    )
                )
        return kept
