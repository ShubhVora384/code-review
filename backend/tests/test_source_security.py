import io
import zipfile
from pathlib import Path

import pytest

from app.core.errors import IngestionError, RepositoryTooLargeError, UnsupportedSourceError
from app.ingestion.source import _sanitize_git_url, resolve_source, use_local_path, use_zip


def test_rejects_non_http_schemes():
    for url in ("ssh://git@github.com/a/b.git", "file:///etc/passwd", "git://x/y"):
        with pytest.raises(UnsupportedSourceError):
            _sanitize_git_url(url, None)


def test_rejects_malformed_host():
    with pytest.raises(UnsupportedSourceError):
        _sanitize_git_url("https:///no-host/repo.git", None)


def test_token_is_injected_but_kept_out_of_the_display_url():
    url, display = _sanitize_git_url("https://github.com/o/r.git", "ghp_secret123")
    assert "ghp_secret123@github.com" in url
    assert "ghp_secret123" not in display
    assert display == "https://github.com/o/r.git"


def test_existing_credentials_are_stripped_from_display():
    _, display = _sanitize_git_url("https://user:pw@github.com/o/r.git", None)
    assert "user:pw" not in display


async def test_unknown_source_type_rejected():
    with pytest.raises(UnsupportedSourceError):
        await resolve_source(source_type="ftp", source_ref="x")


async def test_local_path_must_exist():
    with pytest.raises(IngestionError):
        await use_local_path("/definitely/not/a/real/path/xyz")


async def test_local_path_must_be_a_directory(tmp_path: Path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(IngestionError):
        await use_local_path(str(f))


async def test_local_workspace_is_not_deleted_on_cleanup(sample_project: Path):
    ws = await use_local_path(str(sample_project))
    assert ws.ephemeral is False
    ws.cleanup()
    assert sample_project.exists(), "cleanup must never delete a user's own directory"


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return buf.getvalue()


async def test_zip_slip_is_blocked():
    payload = _zip_bytes({"../../evil.py": "pwned"})
    with pytest.raises(IngestionError):
        await use_zip(payload, "evil.zip")


async def test_absolute_path_in_archive_is_blocked():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("/etc/cron.d/evil")
        zf.writestr(info, "pwned")
    with pytest.raises(IngestionError):
        await use_zip(buf.getvalue(), "evil.zip")


async def test_bad_archive_rejected():
    with pytest.raises(IngestionError):
        await use_zip(b"this is not a zip file", "broken.zip")


async def test_valid_zip_extracts_and_collapses_wrapper_dir():
    payload = _zip_bytes({
        "myproject/src/app.py": "print(1)\n",
        "myproject/README.md": "# hi\n",
    })
    ws = await use_zip(payload, "myproject.zip")
    try:
        assert (ws.root / "src" / "app.py").exists(), "single wrapper dir should collapse"
        assert ws.source_type == "zip"
        assert ws.ephemeral is True
    finally:
        ws.cleanup()
    assert not ws.root.exists()


async def test_zip_bomb_guard(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "max_repo_mb", 1)
    payload = _zip_bytes({f"f{i}.txt": "A" * 200_000 for i in range(10)})
    with pytest.raises(RepositoryTooLargeError):
        await use_zip(payload, "bomb.zip")
