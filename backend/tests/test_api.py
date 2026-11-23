
from datetime import UTC

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.base import new_id
from app.db.models import FileReport, Finding, Review
from app.db.session import init_db, session_scope
from app.main import app


@pytest.fixture(autouse=True)
async def _db():
    await init_db()
    yield


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_completed_review() -> str:
    rid = new_id()
    async with session_scope() as db:
        db.add(Review(
            id=rid, source_type="git", source_ref="https://github.com/o/r",
            display_name="r", status="completed", phase="completed", progress=1.0,
            summary="It works but has issues.",
            scores={"overall": 72.5, "grade": "C", "verdict": "Workable",
                    "dimensions": {"security": 60.0}, "weights": {"security": 0.22},
                    "severity_counts": {"critical": 1, "high": 0, "medium": 0,
                                        "low": 0, "info": 0},
                    "total_findings": 1},
            metrics={"files_indexed": 10, "files_reviewed": 3, "total_loc": 500,
                     "test_ratio": 0.2},
            recommendations=[{"title": "Fix auth", "detail": "…", "effort": "small",
                              "impact": "high", "category": "security",
                              "related_paths": ["a.py"], "horizon": "quick_win"}],
        ))
        await db.flush()
        report = FileReport(id=new_id(), review_id=rid, path="a.py", language="Python",
                            loc=100, status="reviewed", score=55.0, summary="Risky.")
        db.add(report)
        await db.flush()
        db.add(Finding(id=new_id(), review_id=rid, file_report_id=report.id, path="a.py",
                       severity="critical", category="security", title="SQL injection",
                       description="Concatenated query.", confidence=0.95, verified=True))
    return rid


async def test_root_and_health(client: AsyncClient):
    assert (await client.get("/")).status_code == 200

    res = await client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in ("ok", "degraded")
    assert body["models"]["deep"]
    assert "deep" in body["budget"]


async def test_openapi_schema_builds(client: AsyncClient):
    res = await client.get("/openapi.json")
    assert res.status_code == 200
    assert "/api/v1/reviews" in res.json()["paths"]


async def test_create_review_validation(client: AsyncClient):
    # Missing source_ref for a git review
    res = await client.post("/api/v1/reviews",
                            json={"source_type": "git", "source_ref": ""})
    assert res.status_code == 422

    # zip must go through the upload endpoint
    res = await client.post("/api/v1/reviews",
                            json={"source_type": "zip", "source_ref": "x"})
    assert res.status_code == 400

    res = await client.post("/api/v1/reviews",
                            json={"source_type": "carrier-pigeon", "source_ref": "x"})
    assert res.status_code == 422


async def test_upload_rejects_non_zip(client: AsyncClient):
    res = await client.post("/api/v1/reviews/upload",
                            files={"file": ("x.tar.gz", b"data", "application/gzip")})
    assert res.status_code == 400


async def test_get_and_list_review(client: AsyncClient):
    rid = await _seed_completed_review()

    res = await client.get(f"/api/v1/reviews/{rid}")
    assert res.status_code == 200
    body = res.json()
    assert body["scores"]["grade"] == "C"
    assert len(body["files"]) == 1
    assert body["files"][0]["findings"][0]["title"] == "SQL injection"

    res = await client.get("/api/v1/reviews")
    rows = res.json()
    row = next(r for r in rows if r["id"] == rid)
    assert row["overall_score"] == 72.5
    assert row["findings_count"] == 1
    assert row["critical_count"] == 1


async def test_missing_review_is_404(client: AsyncClient):
    assert (await client.get("/api/v1/reviews/deadbeef")).status_code == 404
    assert (await client.delete("/api/v1/reviews/deadbeef")).status_code == 404
    assert (await client.post("/api/v1/reviews/deadbeef/cancel")).status_code == 404


async def test_findings_filtering(client: AsyncClient):
    rid = await _seed_completed_review()

    res = await client.get(f"/api/v1/reviews/{rid}/findings?severity=critical")
    assert len(res.json()) == 1

    res = await client.get(f"/api/v1/reviews/{rid}/findings?severity=low")
    assert res.json() == []


async def test_markdown_export(client: AsyncClient):
    rid = await _seed_completed_review()
    res = await client.get(f"/api/v1/reviews/{rid}/report.md")
    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    assert "# Code Review" in res.text
    assert "SQL injection" in res.text


async def test_sse_replays_a_finished_review(client: AsyncClient):
    """A completed review must stream its stored trace then close, not hang."""
    from datetime import datetime

    from app.db.models import ReviewEvent

    rid = await _seed_completed_review()
    async with session_scope() as db:
        for i in (1, 2):
            db.add(ReviewEvent(id=new_id(), review_id=rid, seq=i, type="phase",
                               agent="Tester", message=f"step {i}", payload={"phase": "x"},
                               created_at=datetime.now(UTC)))

    async with client.stream("GET", f"/api/v1/reviews/{rid}/stream") as res:
        assert res.status_code == 200
        chunks = [line async for line in res.aiter_lines()]

    body = "\n".join(chunks)
    assert "step 1" in body and "step 2" in body
    assert "event: done" in body


async def test_delete_review(client: AsyncClient):
    rid = await _seed_completed_review()
    assert (await client.delete(f"/api/v1/reviews/{rid}")).status_code == 204
    assert (await client.get(f"/api/v1/reviews/{rid}")).status_code == 404
