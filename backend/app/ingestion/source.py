"""Resolve a user-supplied source into a local workspace directory.

Supports: public git URL, private git URL + PAT, local folder path, ZIP upload.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

from app.core.config import settings
from app.core.errors import (
    IngestionError,
    RepositoryTooLargeError,
    UnsupportedSourceError,
)
from app.core.logging import get_logger
from app.db.base import new_id

log = get_logger(__name__)

_ALLOWED_GIT_SCHEMES = {"http", "https"}
_GIT_HOST_RE = re.compile(r"^[a-zA-Z0-9._\-]+(:\d+)?$")
_BRANCH_RE = re.compile(r"^[\w./\-]{1,255}$")


@dataclass
class Workspace:
    """A checked-out codebase on local disk, ready to walk."""

    root: Path
    display_name: str
    source_type: str
    source_ref: str
    branch: str | None = None
    commit_sha: str | None = None
    ephemeral: bool = True

    def cleanup(self) -> None:
        if self.ephemeral and self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)


def _new_workspace_dir() -> Path:
    d = settings.workspace_path / new_id()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dir_size_mb(path: Path) -> float:
    total = 0
    for p in path.rglob("*"):
        if p.is_file() and not p.is_symlink():
            try:
                total += p.stat().st_size
            except OSError:
                continue
    return total / (1024 * 1024)


# ----------------------------------------------------------------- git clone


def _sanitize_git_url(url: str, token: str | None) -> tuple[str, str]:
    """Validate the URL and inject a PAT if provided. Returns (url, safe_display)."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in _ALLOWED_GIT_SCHEMES:
        raise UnsupportedSourceError(
            "Only http(s) git URLs are supported. "
            "SSH clones would need key material on the server."
        )
    if not parsed.netloc or not _GIT_HOST_RE.match(parsed.netloc.split("@")[-1]):
        raise UnsupportedSourceError(f"Invalid git host in URL: {url!r}")

    display = urlunparse(parsed._replace(netloc=parsed.netloc.split("@")[-1]))

    if token:
        # PAT goes in the netloc; it is never persisted or logged.
        netloc = f"{quote(token, safe='')}@{parsed.netloc.split('@')[-1]}"
        url = urlunparse(parsed._replace(netloc=netloc))
    return url, display


async def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo", "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        raise IngestionError(f"Command timed out after {timeout}s: {cmd[0]}") from None
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


def _redact(text: str, token: str | None) -> str:
    return text.replace(token, "***") if token else text


async def clone_git(url: str, branch: str | None, token: str | None) -> Workspace:
    if branch and not _BRANCH_RE.match(branch):
        raise UnsupportedSourceError(f"Invalid branch name: {branch!r}")

    clone_url, display_url = _sanitize_git_url(url, token)
    dest = _new_workspace_dir()
    repo_dir = dest / "repo"

    cmd = ["git", "clone", "--depth", "1", "--single-branch", "--no-tags"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [clone_url, str(repo_dir)]

    code, out = await _run(cmd, timeout=settings.clone_timeout_sec)
    if code != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise IngestionError(
            "git clone failed. Check the URL, branch and (for private repos) the token.",
            detail=_redact(out, token)[-800:],
        )

    size = _dir_size_mb(repo_dir)
    if size > settings.max_repo_mb:
        shutil.rmtree(dest, ignore_errors=True)
        raise RepositoryTooLargeError(
            f"Repository is {size:.0f} MB, over the {settings.max_repo_mb} MB limit."
        )

    _, sha = await _run(["git", "rev-parse", "HEAD"], cwd=repo_dir, timeout=15)
    _, ref = await _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir, timeout=15)

    name = Path(urlparse(display_url).path).name.removesuffix(".git") or "repository"
    log.info("ingest.cloned", repo=name, size_mb=round(size, 1))

    return Workspace(
        root=repo_dir,
        display_name=name,
        source_type="git",
        source_ref=display_url,
        branch=(branch or ref.strip() or None),
        commit_sha=sha.strip()[:40] or None,
        ephemeral=True,
    )


# ---------------------------------------------------------------- local path


async def use_local_path(raw_path: str) -> Workspace:
    path = Path(raw_path).expanduser()
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IngestionError(f"Path not found: {raw_path}") from exc
    if not path.is_dir():
        raise IngestionError(f"Not a directory: {path}")

    size = _dir_size_mb(path)
    if size > settings.max_repo_mb:
        raise RepositoryTooLargeError(
            f"Directory is {size:.0f} MB, over the {settings.max_repo_mb} MB limit."
        )

    commit_sha = branch = None
    if (path / ".git").exists():
        code, sha = await _run(["git", "rev-parse", "HEAD"], cwd=path, timeout=15)
        if code == 0:
            commit_sha = sha.strip()[:40]
        code, ref = await _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path, timeout=15)
        if code == 0:
            branch = ref.strip()

    # ephemeral=False: never delete a directory the user already owned.
    return Workspace(
        root=path,
        display_name=path.name,
        source_type="local",
        source_ref=str(path),
        branch=branch,
        commit_sha=commit_sha,
        ephemeral=False,
    )


# ------------------------------------------------------------------ zip file


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract with zip-slip protection and an uncompressed-size ceiling."""
    limit_bytes = settings.max_repo_mb * 1024 * 1024
    total = 0
    dest_resolved = dest.resolve()

    for info in zf.infolist():
        if info.is_dir():
            continue
        total += info.file_size
        if total > limit_bytes:
            raise RepositoryTooLargeError(
                f"Archive expands past the {settings.max_repo_mb} MB limit."
            )
        target = (dest / info.filename).resolve()
        if not str(target).startswith(str(dest_resolved) + "/") and target != dest_resolved:
            raise IngestionError(f"Unsafe path in archive: {info.filename!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, target.open("wb") as out:
            shutil.copyfileobj(src, out, length=1024 * 64)


async def use_zip(data: bytes, filename: str) -> Workspace:
    dest = _new_workspace_dir()
    extract_dir = dest / "repo"
    extract_dir.mkdir(parents=True, exist_ok=True)
    archive = dest / "upload.zip"
    archive.write_bytes(data)

    def _extract() -> None:
        try:
            with zipfile.ZipFile(archive) as zf:
                _safe_extract(zf, extract_dir)
        except zipfile.BadZipFile as exc:
            raise IngestionError("Uploaded file is not a valid ZIP archive.") from exc

    try:
        await asyncio.to_thread(_extract)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        archive.unlink(missing_ok=True)

    # Collapse a single wrapper directory (the usual GitHub zip shape).
    entries = [p for p in extract_dir.iterdir() if p.name != "__MACOSX"]
    root = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_dir

    name = Path(filename).stem or "uploaded-project"
    log.info("ingest.unzipped", name=name)

    return Workspace(
        root=root,
        display_name=name,
        source_type="zip",
        source_ref=filename,
        ephemeral=True,
    )


# ------------------------------------------------------------------ dispatch


async def resolve_source(
    *,
    source_type: str,
    source_ref: str = "",
    branch: str | None = None,
    token: str | None = None,
    upload: tuple[bytes, str] | None = None,
) -> Workspace:
    if source_type == "git":
        return await clone_git(source_ref, branch, token)
    if source_type == "local":
        return await use_local_path(source_ref)
    if source_type == "zip":
        if upload is None:
            raise IngestionError("No file was uploaded for a zip source.")
        return await use_zip(*upload)
    raise UnsupportedSourceError(f"Unknown source type: {source_type!r}")
