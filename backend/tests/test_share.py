"""A share link is handed to people who have no right to the codebase, so what
it does *not* contain matters more than what it does."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.base import new_id
from app.db.models import FileReport, Finding, Review
from app.db.session import init_db, session_scope
from app.main import app

SECRET_SNIPPET = 'SECRET_KEY = "sk_' 'live_4eC39HqLyjWDarjtT1zdp7dc"'
PROOF_SCRIPT = "import target_module\nprint('PROOF_VERDICT: VULNERABLE')"


@pytest.fixture(autouse=True)
async def _db():
    await init_db()
    yield


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed(status: str = "completed") -> str:
    rid = new_id()
    async with session_scope() as db:
        db.add(Review(
            id=rid, source_type="local", source_ref="/home/someone/private-app",
            display_name="private-app", status=status, phase=status, progress=1.0,
            summary="One demonstrated vulnerability.",
            scores={
                "overall": 41.0, "grade": "F", "verdict": "Major rework required",
                "launch_verdict": {
                    "decision": "do_not_launch",
                    "headline": "Do not launch yet",
                    "reason": "1 serious vulnerability was demonstrated.",
                },
                "dimensions": {"security": 20.0},
                "severity_counts": {"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0},
                "proven_findings": 1,
            },
            metrics={"files_indexed": 12, "files_reviewed": 6, "total_loc": 900},
        ))
        await db.flush()
        report = FileReport(id=new_id(), review_id=rid, path="app/auth.py",
                            language="Python", loc=40, status="reviewed", score=10.0)
        db.add(report)
        await db.flush()
        db.add(Finding(
            id=new_id(), review_id=rid, file_report_id=report.id, path="app/auth.py",
            severity="critical", category="security", title="Hard-coded secret key",
            description="A live Stripe key is committed.", confidence=1.0,
            verified=True, code_snippet=SECRET_SNIPPET,
            proof_status="proven", proof_kind="secret_shape:stripe_live",
            proof_evidence="app/auth.py:5 contains a live Stripe key format.",
            proof_script=PROOF_SCRIPT, proof_output="PROOF_VERDICT: VULNERABLE",
            proof_executed=False,
        ))
    return rid


async def test_sharing_is_off_until_asked_for(client):
    rid = await _seed()
    detail = (await client.get(f"/api/v1/reviews/{rid}")).json()
    assert detail.get("share_token") in (None, "")


async def test_enable_then_read_the_public_report(client):
    rid = await _seed()
    res = await client.post(f"/api/v1/reviews/{rid}/share")
    assert res.status_code == 200
    token = res.json()["share_token"]
    assert len(token) >= 32, "the token must not be guessable"

    public = await client.get(f"/api/v1/share/{token}")
    assert public.status_code == 200
    body = public.json()
    assert body["display_name"] == "private-app"
    assert body["launch_verdict"]["decision"] == "do_not_launch"
    assert len(body["proven_findings"]) == 1
    assert body["proven_findings"][0]["proof_evidence"]


async def test_public_report_never_exposes_source_or_proof_scripts(client):
    rid = await _seed()
    token = (await client.post(f"/api/v1/reviews/{rid}/share")).json()["share_token"]
    raw = (await client.get(f"/api/v1/share/{token}")).text

    assert SECRET_SNIPPET not in raw, "code snippets must not reach a public link"
    assert "sk_live_" not in raw, "the secret itself must never be echoed"
    assert PROOF_SCRIPT not in raw, "proof scripts quote the source"
    assert "proof_script" not in raw
    assert "/home/someone/private-app" not in raw, "server paths must stay private"


async def test_revoking_kills_the_link(client):
    rid = await _seed()
    token = (await client.post(f"/api/v1/reviews/{rid}/share")).json()["share_token"]
    assert (await client.get(f"/api/v1/share/{token}")).status_code == 200

    assert (await client.delete(f"/api/v1/reviews/{rid}/share")).status_code == 200
    assert (await client.get(f"/api/v1/share/{token}")).status_code == 404


async def test_a_revoked_link_is_never_revived(client):
    """Re-enabling issues a fresh token; the old one must stay dead."""
    rid = await _seed()
    first = (await client.post(f"/api/v1/reviews/{rid}/share")).json()["share_token"]
    await client.delete(f"/api/v1/reviews/{rid}/share")
    second = (await client.post(f"/api/v1/reviews/{rid}/share")).json()["share_token"]

    assert first != second
    assert (await client.get(f"/api/v1/share/{first}")).status_code == 404
    assert (await client.get(f"/api/v1/share/{second}")).status_code == 200


async def test_unknown_token_is_404(client):
    assert (await client.get("/api/v1/share/not-a-real-token")).status_code == 404


async def test_incomplete_reviews_cannot_be_shared(client):
    rid = await _seed(status="running")
    res = await client.post(f"/api/v1/reviews/{rid}/share")
    assert res.status_code == 409


async def test_sharing_a_missing_review_is_404(client):
    assert (await client.post("/api/v1/reviews/deadbeef/share")).status_code == 404
