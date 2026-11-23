from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import CodeReviewError
from app.core.event_bus import bus
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_db, init_db
from app.llm.cache import cache
from app.llm.client import llm
from app.llm.key_pool import key_pool
from app.llm.rate_limiter import governor
from app.workers import queue

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    await init_db()

    mode = "worker process" if settings.has_redis else "in-process (no Redis)"
    key_pool.load()
    if not settings.groq_ready:
        log.warning("startup.no_groq_key", hint="Set GROQ_API_KEY in .env")
    elif key_pool.size == 1:
        log.info(
            "startup.single_key",
            hint="Add GROQ_API_KEY_2 / _3 to triple the free-tier throughput",
        )
    log.info(
        "startup.ready",
        version=__version__,
        jobs=mode,
        api_keys=key_pool.size,
        key_strategy=settings.groq_key_strategy,
        deep_model=settings.groq_model_deep,
        fast_model=settings.groq_model_fast,
    )

    yield

    await llm.close()
    await bus.close()
    await cache.close()
    await governor.close()
    await queue.close()
    await dispose_db()
    log.info("shutdown.complete")


app = FastAPI(
    title="AI Code Review System",
    description=(
        "Agentic code review. A LangGraph pipeline profiles the project, maps "
        "its structure, plans a review, reviews each file, hunts systemic "
        "issues, challenges its own findings, then scores and reports."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(CodeReviewError)
async def domain_error_handler(_: Request, exc: CodeReviewError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "detail": exc.detail},
    )


@app.get("/", tags=["system"])
async def root() -> dict:
    return {
        "name": "AI Code Review System",
        "version": __version__,
        "docs": "/docs",
        "api": "/api/v1",
    }


app.include_router(api_router)
