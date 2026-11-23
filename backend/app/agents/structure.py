"""Turn 2 — how is the codebase organised?"""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.agents.context import ReviewContext
from app.agents.prompts import STRUCTURE_PROMPT
from app.ingestion.walker import RepoInventory, directory_summary, render_tree
from app.llm.tokens import truncate_to_tokens
from app.schemas.agent import ProjectProfile, StructureAnalysis

_TREE_BUDGET = 2_500


class StructureAgent(BaseAgent):
    name = "StructureAnalyst"
    tier = "deep"
    max_tokens = 2800

    async def run(
        self, ctx: ReviewContext, inv: RepoInventory, profile: ProjectProfile
    ) -> StructureAnalysis:
        tree = truncate_to_tokens(render_tree(inv, max_entries=420), _TREE_BUDGET)
        dirs = directory_summary(inv)

        largest = sorted(inv.reviewable_files, key=lambda f: -f.loc)[:15]
        entry_hits = [
            f.rel_path for f in inv.files
            if any(
                f.rel_path.lower().endswith(s)
                for s in ("main.py", "app.py", "__main__.py", "index.js", "index.ts",
                          "main.go", "main.rs", "server.js", "server.ts", "main.java")
            )
        ][:10]

        user = f"""\
## Project profile (established in the previous step)
- Name: {profile.name}
- Type: {profile.project_type}
- Purpose: {profile.one_liner}
- Stack: {', '.join(profile.frameworks + profile.key_technologies) or 'unknown'}
- Primary language: {profile.primary_language}
- Maturity signal: {profile.maturity}

## Repository shape
Files indexed: {len(inv.files)} | Lines of code: {inv.total_loc:,} | \
Test-file ratio: {inv.test_ratio:.0%}

## Directory rollup
{chr(10).join(
    f"- {d['path']}/ — {d['files']} file(s), {d['loc']:,} loc ({', '.join(d['languages'])})"
    for d in dirs[:25]
) or '(flat repository)'}

## Largest source files
{chr(10).join(f"- {f.rel_path} ({f.loc} loc)" for f in largest) or '(none)'}

## Detected entry points
{chr(10).join(f"- {p}" for p in entry_hits) or '(none detected by filename)'}

## Full tree
{tree}
"""
        analysis = await self.ask(ctx, STRUCTURE_PROMPT, user, StructureAnalysis)

        # Keep only hotspots that name a path we actually have.
        known = {f.rel_path for f in inv.files}
        analysis.hotspot_paths = [
            p for p in analysis.hotspot_paths
            if p in known or any(k.startswith(p.rstrip("/") + "/") for k in known)
        ]
        return analysis
