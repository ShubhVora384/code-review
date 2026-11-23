"""Turn 1 — what is this project?"""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.agents.context import ReviewContext
from app.agents.prompts import PROFILER_PROMPT
from app.ingestion.walker import RepoInventory, directory_summary, render_tree
from app.llm.tokens import truncate_to_tokens
from app.schemas.agent import ProjectProfile

# Manifests are the highest-signal bytes in any repo — spend budget here.
_MANIFEST_BUDGET = 3_000
_TREE_BUDGET = 1_800


class ProfilerAgent(BaseAgent):
    name = "ProjectProfiler"
    tier = "deep"
    max_tokens = 2200

    async def run(self, ctx: ReviewContext, inv: RepoInventory) -> ProjectProfile:
        manifest_text = self._read_manifests(inv)
        tree = truncate_to_tokens(render_tree(inv, max_entries=200), _TREE_BUDGET)
        dirs = directory_summary(inv)[:14]
        langs = dict(list(inv.language_breakdown.items())[:12])

        user = f"""\
## Language statistics
{_fmt_langs(langs)}

Total files indexed: {len(inv.files)}   Total lines of code: {inv.total_loc:,}
Test files: {sum(1 for f in inv.reviewable_files if f.is_test)} \
({inv.test_ratio:.0%} of source files)

## Top-level directories
{_fmt_dirs(dirs)}

## File tree
{tree}

## Manifest and documentation files
{manifest_text}
"""
        profile = await self.ask(ctx, PROFILER_PROMPT, user, ProjectProfile)

        if not profile.primary_language or profile.primary_language == "Unknown":
            profile.primary_language = inv.primary_language
        if not profile.name:
            profile.name = inv.root.name
        return profile

    @staticmethod
    def _read_manifests(inv: RepoInventory) -> str:
        """READMEs first, then build manifests, until the budget runs out."""
        def rank(entry) -> tuple[int, int]:
            low = entry.rel_path.lower()
            depth = entry.rel_path.count("/")
            if "readme" in low:
                return (0, depth)
            if low.endswith(("package.json", "pyproject.toml", "cargo.toml",
                             "go.mod", "pom.xml", "composer.json")):
                return (1, depth)
            return (2, depth)

        chunks: list[str] = []
        spent = 0
        for entry in sorted(inv.manifests, key=rank)[:14]:
            try:
                content = entry.abs_path.read_text("utf-8", errors="ignore")
            except OSError:
                continue
            share = min(5_000, max(600, _MANIFEST_BUDGET - spent))
            if share <= 0:
                break
            body = truncate_to_tokens(content, share)
            spent += share
            chunks.append(f"===== {entry.rel_path} =====\n{body}")
            if spent >= _MANIFEST_BUDGET:
                break

        return "\n\n".join(chunks) if chunks else "(no manifest or README files found)"


def _fmt_langs(langs: dict[str, dict]) -> str:
    if not langs:
        return "(none detected)"
    return "\n".join(
        f"- {name}: {d['files']} file(s), {d['loc']:,} loc"
        for name, d in langs.items()
    )


def _fmt_dirs(dirs: list[dict]) -> str:
    if not dirs:
        return "(flat repository)"
    return "\n".join(
        f"- {d['path']}/ — {d['files']} file(s), {d['loc']:,} loc, "
        f"mainly {', '.join(d['languages']) or 'mixed'}"
        for d in dirs
    )
