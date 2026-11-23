"""Walk a workspace into a structured inventory the agents can reason about."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pathspec

from app.core.config import settings
from app.core.logging import get_logger
from app.ingestion.language import (
    SKIP_DIRS,
    detect_language,
    is_generated,
    is_manifest,
    is_probably_binary,
    is_test_file,
)

log = get_logger(__name__)

# Languages worth spending deep-model tokens on.
_REVIEWABLE = {
    "Python", "JavaScript", "TypeScript", "Java", "Kotlin", "Go", "Rust", "Ruby",
    "PHP", "C", "C++", "C#", "Swift", "Scala", "Shell", "SQL", "Vue", "Svelte",
    "Dart", "Elixir", "Objective-C", "Lua", "R", "Haskell", "Terraform",
}
_CONFIGY = {"JSON", "YAML", "TOML", "INI", "XML", "Dockerfile", "Make", "HCL"}


@dataclass
class FileEntry:
    rel_path: str
    abs_path: Path
    language: str
    size_bytes: int
    loc: int
    is_test: bool = False
    is_manifest: bool = False
    is_generated: bool = False
    priority: int = 0

    @property
    def reviewable(self) -> bool:
        return self.language in _REVIEWABLE and not self.is_generated


@dataclass
class RepoInventory:
    root: Path
    files: list[FileEntry] = field(default_factory=list)
    skipped: int = 0
    truncated: bool = False

    @property
    def total_loc(self) -> int:
        return sum(f.loc for f in self.files)

    @property
    def language_breakdown(self) -> dict[str, dict]:
        agg: dict[str, dict] = defaultdict(lambda: {"files": 0, "loc": 0})
        for f in self.files:
            if f.language == "Unknown":
                continue
            agg[f.language]["files"] += 1
            agg[f.language]["loc"] += f.loc
        return dict(
            sorted(agg.items(), key=lambda kv: kv[1]["loc"], reverse=True)
        )

    @property
    def primary_language(self) -> str:
        code = {k: v for k, v in self.language_breakdown.items() if k in _REVIEWABLE}
        return next(iter(code), "Unknown")

    @property
    def manifests(self) -> list[FileEntry]:
        return [f for f in self.files if f.is_manifest]

    @property
    def reviewable_files(self) -> list[FileEntry]:
        return [f for f in self.files if f.reviewable]

    @property
    def test_ratio(self) -> float:
        rev = self.reviewable_files
        if not rev:
            return 0.0
        return sum(1 for f in rev if f.is_test) / len(rev)


def _load_ignore_spec(root: Path) -> pathspec.PathSpec | None:
    patterns: list[str] = []
    for name in (".gitignore", ".dockerignore"):
        gi = root / name
        if gi.is_file():
            try:
                patterns += gi.read_text("utf-8", errors="ignore").splitlines()
            except OSError:
                continue
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def _count_loc(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _score_priority(entry: FileEntry, root_depth_hint: int) -> int:
    """Higher = reviewed earlier. The planner may override, but this gives a
    sane default ordering when the token budget cannot cover everything."""
    score = 0
    if entry.is_manifest:
        score += 40
    if entry.reviewable:
        score += 30
    if entry.language in _CONFIGY:
        score += 10

    name = entry.rel_path.lower()
    # Entry points and security-sensitive surfaces first.
    for hint, bonus in (
        ("main.", 25), ("app.", 20), ("index.", 18), ("server.", 22),
        ("auth", 30), ("security", 30), ("login", 25), ("password", 25),
        ("crypt", 25), ("token", 20), ("payment", 28), ("admin", 18),
        ("api/", 15), ("route", 15), ("controller", 15), ("handler", 12),
        ("model", 10), ("schema", 10), ("db", 12), ("query", 12),
        ("middleware", 14), ("config", 12), ("util", 5),
    ):
        if hint in name:
            score += bonus

    # Substantial files carry more risk than one-liners.
    if entry.loc > 400:
        score += 15
    elif entry.loc > 120:
        score += 8
    elif entry.loc < 10:
        score -= 15

    if entry.is_test:
        score -= 12
    if entry.is_generated:
        score -= 60

    # Shallow files are usually more architecturally central.
    depth = entry.rel_path.count("/")
    score -= min(depth, 6) * 2
    return score


def walk_repository(root: Path) -> RepoInventory:
    """Build the file inventory, honouring .gitignore and size guardrails."""
    inv = RepoInventory(root=root)
    spec = _load_ignore_spec(root)
    max_bytes = settings.max_file_kb * 1024

    for path in sorted(root.rglob("*")):
        if len(inv.files) >= settings.max_files:
            inv.truncated = True
            break
        if path.is_symlink() or not path.is_file():
            continue

        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue

        if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            inv.skipped += 1
            continue
        if path.name in SKIP_DIRS:
            inv.skipped += 1
            continue
        if spec is not None and spec.match_file(rel):
            inv.skipped += 1
            continue

        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0 or size > max_bytes:
            inv.skipped += 1
            continue
        if is_probably_binary(path):
            inv.skipped += 1
            continue

        entry = FileEntry(
            rel_path=rel,
            abs_path=path,
            language=detect_language(path),
            size_bytes=size,
            loc=_count_loc(path),
            is_test=is_test_file(rel),
            is_manifest=is_manifest(path),
            is_generated=is_generated(rel),
        )
        entry.priority = _score_priority(entry, 0)
        inv.files.append(entry)

    inv.files.sort(key=lambda f: (-f.priority, f.rel_path))
    log.info(
        "ingest.walked",
        files=len(inv.files),
        skipped=inv.skipped,
        loc=inv.total_loc,
        truncated=inv.truncated,
    )
    return inv


def render_tree(inv: RepoInventory, *, max_entries: int = 320) -> str:
    """Compact directory tree for the structure-analysis prompt.

    Collapses over-full directories rather than truncating alphabetically, so
    the model still sees every top-level area of the codebase.
    """
    by_dir: dict[str, list[FileEntry]] = defaultdict(list)
    for f in inv.files:
        by_dir[str(Path(f.rel_path).parent).replace(".", "") or ""].append(f)

    budget_per_dir = max(3, max_entries // max(len(by_dir), 1))
    lines: list[str] = []
    shown = 0

    for directory in sorted(by_dir):
        entries = sorted(by_dir[directory], key=lambda f: f.rel_path)
        depth = directory.count("/") + 1 if directory else 0
        indent = "  " * depth
        if directory:
            lines.append(f"{indent}{Path(directory).name}/")
        for f in entries[:budget_per_dir]:
            if shown >= max_entries:
                break
            lines.append(f"{indent}  {Path(f.rel_path).name}  ({f.loc} loc)")
            shown += 1
        hidden = len(entries) - budget_per_dir
        if hidden > 0:
            lines.append(f"{indent}  … +{hidden} more file(s)")

    if inv.truncated:
        lines.append(f"… inventory capped at {settings.max_files} files")
    return "\n".join(lines)


def directory_summary(inv: RepoInventory) -> list[dict]:
    """Per-top-level-directory rollup — cheap context for the planner."""
    agg: dict[str, dict] = defaultdict(
        lambda: {"files": 0, "loc": 0, "languages": Counter()}
    )
    for f in inv.files:
        top = f.rel_path.split("/")[0] if "/" in f.rel_path else "(root)"
        agg[top]["files"] += 1
        agg[top]["loc"] += f.loc
        agg[top]["languages"][f.language] += 1

    out = []
    for name, data in sorted(agg.items(), key=lambda kv: kv[1]["loc"], reverse=True):
        out.append({
            "path": name,
            "files": data["files"],
            "loc": data["loc"],
            "languages": [lang for lang, _ in data["languages"].most_common(3)],
        })
    return out
