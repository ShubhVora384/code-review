"""Server-side directory browser for the 'Local folder' picker.

The browser cannot hand JavaScript a real filesystem path, and the reviewer
needs one — so the API exposes a read-only directory listing that the UI walks.

This endpoint exposes filesystem structure, so it is deliberately narrow:

* only directory **names** are returned, never file contents
* every resolved path must sit inside a configured root (default: `$HOME`)
* resolution happens before the containment check, so `..` and symlinks
  pointing outside the roots are rejected rather than followed
* hidden directories are omitted unless explicitly requested
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/browse", tags=["browse"])

# Directories that are never worth offering as a review target.
_NOISE = {
    "node_modules", "__pycache__", ".git", ".venv", "venv", "dist", "build",
    ".next", ".nuxt", "target", ".gradle", ".idea", ".vscode", ".cache",
    "site-packages", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
}

# Files that mark a directory as a real project worth reviewing.
_PROJECT_MARKERS = (
    "package.json", "pyproject.toml", "requirements.txt", "setup.py",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "composer.json",
    "Gemfile", "mix.exs", "pubspec.yaml", "CMakeLists.txt",
)


def _allowed_roots() -> list[Path]:
    roots: list[Path] = []
    for raw in settings.browse_root_list:
        try:
            resolved = Path(raw).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved.is_dir():
            roots.append(resolved)
    return roots


def _resolve_within_roots(raw: str) -> Path:
    """Resolve `raw` and prove it sits inside an allowed root."""
    roots = _allowed_roots()
    if not roots:
        raise HTTPException(
            status_code=503,
            detail="No browsable roots are configured. Set BROWSE_ROOTS in .env.",
        )

    try:
        # strict=True so a non-existent path cannot be probed for existence
        # anywhere outside the roots.
        target = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail=f"Path not found: {raw}") from exc

    for root in roots:
        if target == root or root in target.parents:
            return target

    log.warning("browse.escape_attempt", requested=raw)
    raise HTTPException(
        status_code=403,
        detail="That path is outside the browsable roots configured for this server.",
    )


def _describe(path: Path) -> dict:
    """Cheap metadata so the picker can highlight real projects."""
    is_repo = False
    is_project = False
    child_dirs = 0
    try:
        with __import__("os").scandir(path) as entries:
            for entry in entries:
                name = entry.name
                if name == ".git":
                    is_repo = True
                elif name in _PROJECT_MARKERS:
                    is_project = True
                if (
                    entry.is_dir(follow_symlinks=False)
                    and not name.startswith(".")
                    and name not in _NOISE
                ):
                    child_dirs += 1
    except (PermissionError, OSError):
        return {"readable": False, "is_git_repo": False,
                "is_project": False, "child_dirs": 0}

    return {
        "readable": True,
        "is_git_repo": is_repo,
        "is_project": is_project or is_repo,
        "child_dirs": child_dirs,
    }


@router.get("")
async def browse(
    path: Annotated[str, Query(description="Absolute directory path")] = "",
    show_hidden: bool = False,
) -> dict:
    """List the sub-directories of `path`.

    With no `path`, returns the configured roots as the starting point.
    """
    roots = _allowed_roots()
    if not roots:
        raise HTTPException(
            status_code=503,
            detail="No browsable roots are configured. Set BROWSE_ROOTS in .env.",
        )

    if not path.strip():
        return {
            "path": None,
            "parent": None,
            "roots": [str(r) for r in roots],
            "entries": [
                {"name": r.name or str(r), "path": str(r), **_describe(r)}
                for r in roots
            ],
        }

    target = _resolve_within_roots(path)

    entries: list[dict] = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.is_symlink():
                continue
            if child.name in _NOISE:
                continue
            if child.name.startswith(".") and not show_hidden:
                continue
            entries.append({"name": child.name, "path": str(child), **_describe(child)})
    except PermissionError as exc:
        raise HTTPException(
            status_code=403, detail=f"Permission denied reading {target}"
        ) from exc

    # Only offer "up" while it stays inside a root.
    parent = None
    if not any(target == r for r in roots):
        candidate = target.parent
        if any(candidate == r or r in candidate.parents for r in roots):
            parent = str(candidate)

    return {
        "path": str(target),
        "parent": parent,
        "roots": [str(r) for r in roots],
        "entries": entries,
        **_describe(target),
    }
