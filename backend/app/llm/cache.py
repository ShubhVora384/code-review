from __future__ import annotations

import hashlib
import json
from collections import OrderedDict

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_TTL_SECONDS = 7 * 24 * 3600
_LOCAL_MAX = 512


def make_key(model: str, messages: list[dict], schema_name: str | None) -> str:
    """Stable content hash. Identical file + identical prompt = free re-review."""
    blob = json.dumps(
        {"m": model, "s": schema_name, "x": messages},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "llmc:" + hashlib.sha256(blob.encode()).hexdigest()


class ResponseCache:
    def __init__(self) -> None:
        self._redis = None
        self._local: OrderedDict[str, str] = OrderedDict()

    async def _get_redis(self):
        if not settings.has_redis:
            return None
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                settings.redis_url, encoding="utf-8", decode_responses=True
            )
        return self._redis

    async def get(self, key: str) -> str | None:
        r = await self._get_redis()
        if r is not None:
            return await r.get(key)
        val = self._local.get(key)
        if val is not None:
            self._local.move_to_end(key)
        return val

    async def set(self, key: str, value: str) -> None:
        r = await self._get_redis()
        if r is not None:
            await r.set(key, value, ex=_TTL_SECONDS)
            return
        self._local[key] = value
        self._local.move_to_end(key)
        while len(self._local) > _LOCAL_MAX:
            self._local.popitem(last=False)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


cache = ResponseCache()
