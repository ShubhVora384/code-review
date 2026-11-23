"""Job dispatch.

With Redis, jobs go on a list and a separate worker process pops them, which
is what docker-compose runs. Without Redis, the API runs the job in-process as
a background task so the whole system still works with one `uvicorn` command.
"""

from __future__ import annotations

import asyncio
import json

from app.core.config import settings
from app.core.logging import get_logger
from app.workers.runner import register, run_review

log = get_logger(__name__)

QUEUE_KEY = "codereview:jobs"
_redis = None


async def _get_redis():
    global _redis
    if not settings.has_redis:
        return None
    if _redis is None:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True
        )
    return _redis


async def enqueue(review_id: str, access_token: str | None = None) -> str:
    """Schedule a review. Returns the execution mode actually used."""
    r = await _get_redis()
    if r is not None:
        await r.rpush(
            QUEUE_KEY,
            json.dumps({"review_id": review_id, "access_token": access_token}),
        )
        log.info("queue.enqueued", review_id=review_id)
        return "queued"

    task = asyncio.create_task(
        run_review(review_id, access_token), name=f"review:{review_id}"
    )
    register(review_id, task)
    log.info("queue.inprocess", review_id=review_id)
    return "in_process"


async def worker_loop(concurrency: int = 1) -> None:
    """Blocking pop loop for the dedicated worker process."""
    r = await _get_redis()
    if r is None:
        raise RuntimeError(
            "The worker process requires REDIS_URL. Without Redis the API "
            "runs reviews in-process and no worker is needed."
        )

    sem = asyncio.Semaphore(concurrency)
    log.info("worker.started", concurrency=concurrency)
    tasks: set[asyncio.Task] = set()

    async def _handle(payload: dict) -> None:
        async with sem:
            review_id = payload["review_id"]
            try:
                await run_review(review_id, payload.get("access_token"))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("worker.job_failed", review_id=review_id)

    try:
        while True:
            item = await r.blpop(QUEUE_KEY, timeout=5)
            if item is None:
                continue
            try:
                payload = json.loads(item[1])
            except json.JSONDecodeError:
                log.warning("worker.bad_payload", raw=item[1][:200])
                continue

            task = asyncio.create_task(_handle(payload))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    except asyncio.CancelledError:
        log.info("worker.stopping", inflight=len(tasks))
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def close() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
