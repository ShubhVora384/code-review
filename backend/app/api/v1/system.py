from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.core.config import settings
from app.db.session import get_engine
from app.llm.key_pool import key_pool
from app.llm.rate_limiter import governor
from app.schemas.api import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    db_status = "ok"
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {type(exc).__name__}"

    redis_status = "disabled (in-process fallback)"
    if settings.has_redis:
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(settings.redis_url)
            await client.ping()
            await client.aclose()
            redis_status = "ok"
        except Exception as exc:
            redis_status = f"error: {type(exc).__name__}"

    return HealthResponse(
        status="ok" if db_status == "ok" else "degraded",
        version=__version__,
        database=f"{'postgres' if settings.is_postgres else 'sqlite'} ({db_status})",
        redis=redis_status,
        groq_configured=settings.groq_ready,
        models={"deep": settings.groq_model_deep, "fast": settings.groq_model_fast},
        budget=await governor.snapshot(key_pool.scopes()),
        keys=await key_pool.snapshot(),
    )


@router.get("/budget")
async def budget() -> dict:
    """Live free-tier consumption across the whole key pool."""
    return await governor.snapshot(key_pool.scopes())


@router.get("/keys")
async def keys() -> dict:
    """Per-key health: which are available, cooling, or spent for the day.

    Only non-reversible fingerprints are returned — never the secrets.
    """
    return await key_pool.snapshot()
