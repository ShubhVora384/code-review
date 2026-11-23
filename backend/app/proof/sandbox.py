"""Isolated execution for proof scripts.

Proof scripts import and call code from the repository under audit. That code
is untrusted by construction — the whole premise is that we do not yet know
what it does. Isolation is therefore not a nicety.

Two backends:

* ``DockerSandbox`` — the only one fit for a hosted deployment. No network,
  capped memory/CPU/PIDs, read-only root filesystem, unprivileged user, all
  capabilities dropped.
* ``SubprocessSandbox`` — a development convenience. It applies rlimits and a
  hard timeout, but it does **not** contain hostile code: same kernel, same
  filesystem, same user. It refuses to run outside development for that reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import stat
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.errors import CodeReviewError
from app.core.logging import get_logger

log = get_logger(__name__)

SCRIPT_NAME = "__proof__.py"
_OUTPUT_LIMIT = 8_000


class SandboxUnavailableError(CodeReviewError):
    code = "sandbox_unavailable"
    status_code = 503


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    backend: str = ""
    files_after: dict[str, str] = field(default_factory=dict)
    """Contents of `read_back` paths once the run finished, for tamper checks."""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        parts = [p for p in (self.stdout.strip(), self.stderr.strip()) if p]
        return "\n".join(parts)[:_OUTPUT_LIMIT]


class Sandbox(Protocol):
    name: str

    async def run(
        self,
        script: str,
        *,
        files: dict[str, str] | None = None,
        timeout: int | None = None,
        network: bool = False,
        read_back: list[str] | None = None,
    ) -> SandboxResult: ...

    async def available(self) -> bool: ...


def _truncate(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    if len(text) > _OUTPUT_LIMIT:
        return text[:_OUTPUT_LIMIT] + "\n… [output truncated]"
    return text


def _collect(root: Path, names: list[str] | None) -> dict[str, str]:
    """Read requested files back so the caller can detect tampering."""
    out: dict[str, str] = {}
    for name in names or []:
        target = root / name
        try:
            out[name] = target.read_text("utf-8", errors="replace")
        except OSError:
            out[name] = ""
    return out


def _materialise(root: Path, script: str, files: dict[str, str] | None) -> None:
    """Write the proof script plus any supporting sources into `root`."""
    for rel, content in (files or {}).items():
        target = (root / rel).resolve()
        if not str(target).startswith(str(root.resolve())):
            raise ValueError(f"Refusing to write outside the sandbox: {rel!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (root / SCRIPT_NAME).write_text(script, encoding="utf-8")


# --------------------------------------------------------------------- docker


class DockerSandbox:
    """Container-isolated execution. The only backend safe for hosting."""

    name = "docker"

    def __init__(self) -> None:
        self._available: bool | None = None

    async def available(self) -> bool:
        if self._available is not None:
            return self._available
        if shutil.which("docker") is None:
            self._available = False
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=15)
            self._available = proc.returncode == 0
        except (TimeoutError, OSError):
            self._available = False
        return self._available

    async def run(
        self,
        script: str,
        *,
        files: dict[str, str] | None = None,
        timeout: int | None = None,
        network: bool = False,
        read_back: list[str] | None = None,
    ) -> SandboxResult:
        timeout = timeout or settings.proof_timeout_sec
        workdir = Path(tempfile.mkdtemp(prefix="proof-"))
        try:
            _materialise(workdir, script, files)
            # The container runs as `nobody`, so it needs to write here.
            os.chmod(workdir, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
            for child in workdir.rglob("*"):
                os.chmod(child, 0o666 if child.is_file() else 0o777)

            cmd = [
                "docker", "run", "--rm",
                "--network", "bridge" if network else "none",
                f"--memory={settings.proof_memory_mb}m",
                f"--memory-swap={settings.proof_memory_mb}m",
                "--cpus=1",
                "--pids-limit=128",
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--user", "65534:65534",
                "-v", f"{workdir}:/work:rw",
                "-w", "/work",
                "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-e", "HOME=/tmp",
                settings.proof_image,
                "python", "-s", "-B", f"/work/{SCRIPT_NAME}",
            ]

            started = time.perf_counter()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout + 10
                )
                timed_out = False
            except TimeoutError:
                proc.kill()
                out, err, timed_out = b"", b"proof timed out", True

            return SandboxResult(
                exit_code=proc.returncode if proc.returncode is not None else -1,
                stdout=_truncate(out),
                stderr=_truncate(err),
                duration_ms=int((time.perf_counter() - started) * 1000),
                timed_out=timed_out,
                backend=self.name,
                files_after=_collect(workdir, read_back),
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


# ----------------------------------------------------------------- subprocess


def _apply_limits(memory_mb: int, cpu_seconds: int):
    """Best-effort rlimits for the child. POSIX only."""

    def _preexec() -> None:  # pragma: no cover - runs in the forked child
        import resource

        nbytes = memory_mb * 1024 * 1024
        for res, limit in (
            (resource.RLIMIT_AS, nbytes),
            (resource.RLIMIT_DATA, nbytes),
            (resource.RLIMIT_CPU, cpu_seconds),
            (resource.RLIMIT_NOFILE, 256),
            (resource.RLIMIT_FSIZE, 16 * 1024 * 1024),
            (resource.RLIMIT_CORE, 0),
        ):
            try:
                resource.setrlimit(res, (limit, limit))
            except (ValueError, OSError):
                continue
        os.setsid()

    return _preexec


class SubprocessSandbox:
    """Development-only execution. Does NOT contain hostile code."""

    name = "subprocess"

    async def available(self) -> bool:
        return os.name == "posix"

    async def run(
        self,
        script: str,
        *,
        files: dict[str, str] | None = None,
        timeout: int | None = None,
        network: bool = False,
        read_back: list[str] | None = None,
    ) -> SandboxResult:
        if settings.app_env != "development":
            raise SandboxUnavailableError(
                "The subprocess sandbox is development-only and cannot isolate "
                "untrusted code. Install Docker and set PROOF_SANDBOX=docker "
                "for any hosted deployment."
            )

        timeout = timeout or settings.proof_timeout_sec
        workdir = Path(tempfile.mkdtemp(prefix="proof-"))
        try:
            _materialise(workdir, script, files)

            argv: list[str] = []
            # unshare gives us a network namespace even without Docker.
            if not network and shutil.which("unshare"):
                argv += ["unshare", "--net", "--map-root-user"]
            argv += [sys.executable, "-s", "-B", SCRIPT_NAME]

            env = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(workdir),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(workdir),
            }

            started = time.perf_counter()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=str(workdir),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    preexec_fn=_apply_limits(settings.proof_memory_mb, timeout),
                )
            except (OSError, PermissionError) as exc:
                # `unshare` needs privileges that are not always granted.
                if argv[0] == "unshare":
                    log.debug("sandbox.unshare_unavailable", error=str(exc))
                    return await self._run_plain(workdir, timeout, started, read_back)
                raise

            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                timed_out = False
            except TimeoutError:
                _kill_group(proc)
                out, err, timed_out = b"", b"proof timed out", True

            return SandboxResult(
                exit_code=proc.returncode if proc.returncode is not None else -1,
                stdout=_truncate(out),
                stderr=_truncate(err),
                duration_ms=int((time.perf_counter() - started) * 1000),
                timed_out=timed_out,
                backend=self.name,
                files_after=_collect(workdir, read_back),
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    async def _run_plain(
        self, workdir: Path, timeout: int, started: float,
        read_back: list[str] | None = None,
    ) -> SandboxResult:
        """Fallback without a network namespace."""
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-s", "-B", SCRIPT_NAME,
            cwd=str(workdir),
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(workdir),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(workdir),
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_apply_limits(settings.proof_memory_mb, timeout),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            timed_out = False
        except TimeoutError:
            _kill_group(proc)
            out, err, timed_out = b"", b"proof timed out", True

        return SandboxResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=_truncate(out),
            stderr=_truncate(err),
            duration_ms=int((time.perf_counter() - started) * 1000),
            timed_out=timed_out,
            backend=self.name,
            files_after=_collect(workdir, read_back),
        )


def _kill_group(proc) -> None:
    """Kill the whole process group: a proof may have spawned children."""
    import signal

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


# ------------------------------------------------------------------ selection

_cached: Sandbox | None = None


async def get_sandbox() -> Sandbox | None:
    """The configured backend, or None when execution is unavailable."""
    global _cached
    if _cached is not None:
        return _cached

    choice = settings.proof_sandbox.lower()
    if choice == "disabled":
        return None

    docker = DockerSandbox()
    subprocess_box = SubprocessSandbox()

    if choice == "docker":
        if not await docker.available():
            raise SandboxUnavailableError(
                "PROOF_SANDBOX=docker but the Docker daemon is not reachable. "
                "Start Docker (and enable WSL integration if applicable)."
            )
        _cached = docker
    elif choice == "subprocess":
        _cached = subprocess_box
    else:  # auto
        if await docker.available():
            _cached = docker
        elif settings.app_env == "development" and await subprocess_box.available():
            log.warning(
                "sandbox.subprocess_fallback",
                hint="Docker unavailable; proofs run with rlimits only. "
                     "Not safe for untrusted repositories — development only.",
            )
            _cached = subprocess_box
        else:
            log.warning("sandbox.unavailable", hint="Executed proofs disabled")
            return None

    log.info("sandbox.selected", backend=_cached.name)
    return _cached


def reset_sandbox_cache() -> None:
    """Used by tests to re-evaluate selection."""
    global _cached
    _cached = None
