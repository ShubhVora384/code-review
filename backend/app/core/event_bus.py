"""Pub/sub fan-out for live agent traces.

With Redis the worker and API can be separate processes and SSE still works.
Without it, both run in-process and subscribers get an asyncio.Queue each.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_QUEUE_MAX = 512
_CHANNEL = "review.events:{}"


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._redis = None
        self._lock = asyncio.Lock()

    async def _get_redis(self):
        if not settings.has_redis:
            return None
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                settings.redis_url, encoding="utf-8", decode_responses=True
            )
        return self._redis

    async def publish(self, review_id: str, event: dict) -> None:
        payload = json.dumps(event, default=str)

        r = await self._get_redis()
        if r is not None:
            with suppress(Exception):
                await r.publish(_CHANNEL.format(review_id), payload)

        # Always deliver in-process too: single-process mode has no Redis, and
        # in split mode the worker's own subscribers (if any) still get it.
        async with self._lock:
            queues = list(self._subs.get(review_id, ()))
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("bus.subscriber_slow", review_id=review_id)

    async def subscribe(self, review_id: str) -> AsyncIterator[dict]:
        """Yield events for one review until the consumer stops iterating."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)

        async with self._lock:
            self._subs.setdefault(review_id, set()).add(queue)

        pubsub = None
        pump: asyncio.Task | None = None
        r = await self._get_redis()

        if r is not None:
            pubsub = r.pubsub()
            await pubsub.subscribe(_CHANNEL.format(review_id))

            async def _pump() -> None:
                try:
                    async for msg in pubsub.listen():
                        if msg.get("type") != "message":
                            continue
                        with suppress(json.JSONDecodeError, asyncio.QueueFull):
                            queue.put_nowait(json.loads(msg["data"]))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.warning("bus.pump_failed", review_id=review_id, exc_info=True)

            pump = asyncio.create_task(_pump())

        try:
            while True:
                yield await queue.get()
        finally:
            if pump is not None:
                pump.cancel()
                with suppress(asyncio.CancelledError):
                    await pump
            if pubsub is not None:
                with suppress(Exception):
                    await pubsub.unsubscribe(_CHANNEL.format(review_id))
                    await pubsub.aclose()
            async with self._lock:
                subs = self._subs.get(review_id)
                if subs:
                    subs.discard(queue)
                    if not subs:
                        self._subs.pop(review_id, None)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


bus = EventBus()
