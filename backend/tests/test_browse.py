"""The browse endpoint exposes filesystem structure, so containment is the
property that actually matters here."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A root containing a project, a noise dir, and a hidden dir."""
    from app.core import config

    root = tmp_path / "workspace"
    (root / "myproject" / "src").mkdir(parents=True)
    (root / "myproject" / "package.json").write_text("{}")
    (root / "myproject" / ".git").mkdir()
    (root / "myproject" / "node_modules").mkdir()
    (root / "plain-folder").mkdir()
    (root / ".hidden").mkdir()
    (root / "notes.txt").write_text("a file, not a directory")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secrets").mkdir()

    monkeypatch.setattr(config.settings, "browse_roots", str(root))
    return {"root": root, "outside": outside}


async def test_no_path_returns_the_roots(client, sandbox):
    res = await client.get("/api/v1/browse")
    assert res.status_code == 200
    body = res.json()
    assert body["path"] is None
    assert body["roots"] == [str(sandbox["root"])]


async def test_lists_only_directories(client, sandbox):
    res = await client.get("/api/v1/browse", params={"path": str(sandbox["root"])})
    names = {e["name"] for e in res.json()["entries"]}
    assert "myproject" in names
    assert "plain-folder" in names
    assert "notes.txt" not in names, "files must never be listed"


async def test_noise_and_hidden_directories_are_filtered(client, sandbox):
    res = await client.get(
        "/api/v1/browse", params={"path": str(sandbox["root"] / "myproject")}
    )
    names = {e["name"] for e in res.json()["entries"]}
    assert "src" in names
    assert "node_modules" not in names
    assert ".git" not in names


async def test_hidden_shown_on_request(client, sandbox):
    res = await client.get(
        "/api/v1/browse",
        params={"path": str(sandbox["root"]), "show_hidden": "true"},
    )
    assert ".hidden" in {e["name"] for e in res.json()["entries"]}


async def test_projects_and_repos_are_flagged(client, sandbox):
    res = await client.get("/api/v1/browse", params={"path": str(sandbox["root"])})
    entry = next(e for e in res.json()["entries"] if e["name"] == "myproject")
    assert entry["is_git_repo"] is True
    assert entry["is_project"] is True

    plain = next(e for e in res.json()["entries"] if e["name"] == "plain-folder")
    assert plain["is_git_repo"] is False


async def test_cannot_escape_the_root(client, sandbox):
    res = await client.get("/api/v1/browse", params={"path": str(sandbox["outside"])})
    assert res.status_code == 403


async def test_dot_dot_traversal_is_blocked(client, sandbox):
    escape = str(sandbox["root"] / ".." / "outside")
    res = await client.get("/api/v1/browse", params={"path": escape})
    assert res.status_code == 403


async def test_absolute_system_paths_are_blocked(client, sandbox):
    for target in ("/etc", "/", "/root"):
        res = await client.get("/api/v1/browse", params={"path": target})
        assert res.status_code in (403, 404), f"{target} must not be browsable"


async def test_symlink_out_of_the_root_is_rejected(client, sandbox, tmp_path):
    link = sandbox["root"] / "escape-link"
    link.symlink_to(sandbox["outside"], target_is_directory=True)

    # Following the link resolves outside the root, so it must be refused.
    res = await client.get("/api/v1/browse", params={"path": str(link)})
    assert res.status_code == 403

    # And it must not even be offered in the listing.
    listing = await client.get("/api/v1/browse", params={"path": str(sandbox["root"])})
    assert "escape-link" not in {e["name"] for e in listing.json()["entries"]}


async def test_missing_path_is_404(client, sandbox):
    res = await client.get(
        "/api/v1/browse", params={"path": str(sandbox["root"] / "nope")}
    )
    assert res.status_code == 404


async def test_parent_stops_at_the_root(client, sandbox):
    at_root = await client.get("/api/v1/browse", params={"path": str(sandbox["root"])})
    assert at_root.json()["parent"] is None, "must not offer to go above the root"

    nested = await client.get(
        "/api/v1/browse", params={"path": str(sandbox["root"] / "myproject")}
    )
    assert nested.json()["parent"] == str(sandbox["root"])
