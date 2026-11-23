"""Rotating pool of Groq API keys.

Groq enforces its free-tier limits per key, so N keys give N times the
throughput -- but only if requests are spread across them and a key that
returns 429 is taken out of rotation instead of being hammered.

The pool provides:

* **Rotation** -- round-robin by default, so no single key absorbs every
  request. `random` and `least_used` strategies are also available.
* **Availability filtering** -- a key that 429s cools down for the interval the
  API asks for (or a default), and a key whose daily cap is spent leaves
  rotation until 00:00 UTC.
* **Immediate failover** -- selection prefers a key that can serve *right now*,
  so a 429 on one key costs a retry, not a wait.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.config import settings
from app.core.errors import BudgetExhaustedError, LLMError
from app.core.logging import get_logger
from app.llm.rate_limiter import governor

log = get_logger(__name__)

# Bounds on how long a 429'd key sits out.
_MIN_COOLDOWN = 2.0
_MAX_COOLDOWN = 120.0
# How long the pool will wait for *some* key to free up before giving up.
_MAX_POOL_WAIT = 300.0


@dataclass
class ApiKey:
    """One Groq credential plus its live health state."""

    id: str
    secret: str = field(repr=False)
    cooldown_until: float = 0.0
    exhausted_day: str | None = None
    consecutive_429: int = 0
    rotations: int = 0
    calls: int = 0
    tokens: int = 0
    _client: object | None = field(default=None, repr=False)

    @property
    def client(self):
        if self._client is None:
            from groq import AsyncGroq

            # max_retries=0: retries are the pool's job, across keys.
            self._client = AsyncGroq(api_key=self.secret, max_retries=0)
        return self._client

    def is_cooling(self, now: float | None = None) -> bool:
        return (now or time.monotonic()) < self.cooldown_until

    def is_exhausted(self) -> bool:
        return self.exhausted_day == datetime.now(UTC).strftime("%Y%m%d")

    @property
    def state(self) -> str:
        if self.is_exhausted():
            return "exhausted"
        if self.is_cooling():
            return "cooling"
        return "available"

    def cooldown_remaining(self) -> float:
        return max(0.0, round(self.cooldown_until - time.monotonic(), 1))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


class AllKeysBusyError(LLMError):
    code = "all_keys_busy"
    status_code = 429


class KeyPool:
    """Selects which key serves the next request."""

    def __init__(self) -> None:
        self._keys: list[ApiKey] = []
        self._cursor = 0
        self._lock = asyncio.Lock()
        self._loaded = False

    # ------------------------------------------------------------- lifecycle

    def load(self) -> None:
        """Build the pool from configuration. Idempotent."""
        if self._loaded:
            return
        secrets = settings.groq_key_list
        self._keys = [
            ApiKey(id=self._fingerprint(secret, i), secret=secret)
            for i, secret in enumerate(secrets)
        ]
        self._loaded = True
        if self._keys:
            log.info("keypool.loaded", count=len(self._keys),
                     strategy=settings.groq_key_strategy,
                     keys=[k.id for k in self._keys])
        else:
            log.warning("keypool.empty", hint="Set GROQ_API_KEY in .env")

    @staticmethod
    def _fingerprint(secret: str, index: int) -> str:
        """Stable, non-reversible label. Never log or return the secret."""
        digest = hashlib.sha256(secret.encode()).hexdigest()[:6]
        return f"key{index + 1}:{digest}"

    @property
    def keys(self) -> list[ApiKey]:
        self.load()
        return self._keys

    @property
    def size(self) -> int:
        return len(self.keys)

    def scopes(self) -> list[str]:
        return [k.id for k in self.keys]

    # -------------------------------------------------------------- ordering

    def _ordered(self, exclude: set[str]) -> list[ApiKey]:
        """Candidate keys in the order the strategy wants them tried."""
        pool = [k for k in self.keys if k.id not in exclude and not k.is_exhausted()]
        if not pool:
            return []

        strategy = settings.groq_key_strategy
        if strategy == "random":
            return random.sample(pool, len(pool))
        if strategy == "least_used":
            return sorted(pool, key=lambda k: (k.tokens, k.calls))

        # round_robin: rotate the list so a different key leads each time.
        offset = self._cursor % len(pool)
        self._cursor += 1
        return pool[offset:] + pool[:offset]

    # ------------------------------------------------------------- selection

    async def select(
        self, tier: str, estimated_tokens: int, *, exclude: set[str] | None = None
    ) -> ApiKey:
        """Reserve budget on the best available key and return it.

        Prefers a key that can serve immediately. If every key is rate limited
        it waits for whichever frees up soonest. Keys that have spent their
        daily cap are dropped from rotation for the rest of the UTC day.
        """
        self.load()
        if not self._keys:
            raise LLMError(
                "No Groq API key configured. Add GROQ_API_KEY (and optionally "
                "GROQ_API_KEY_2 / GROQ_API_KEY_3) to your .env — free keys at "
                "https://console.groq.com/keys"
            )

        exclude = exclude or set()
        waited = 0.0

        while True:
            async with self._lock:
                candidates = self._ordered(exclude)

            if not candidates:
                raise self._nothing_available_error(tier, exclude)

            now = time.monotonic()
            soonest = _MAX_POOL_WAIT
            exhausted_now: list[ApiKey] = []

            for key in candidates:
                if key.is_cooling(now):
                    soonest = min(soonest, key.cooldown_until - now)
                    continue
                try:
                    wait = await governor.try_acquire(key.id, tier, estimated_tokens)
                except BudgetExhaustedError as exc:
                    # This key is spent for the day; keep the others.
                    key.exhausted_day = datetime.now(UTC).strftime("%Y%m%d")
                    exhausted_now.append(key)
                    log.warning("keypool.key_exhausted", key=key.id, reason=str(exc))
                    continue

                if wait <= 0:
                    key.consecutive_429 = 0
                    key.calls += 1
                    return key
                soonest = min(soonest, wait)

            if exhausted_now and len(exhausted_now) < len(candidates):
                continue  # re-evaluate immediately with the remaining keys

            if all(k.is_exhausted() for k in self.keys):
                raise BudgetExhaustedError(
                    f"All {len(self.keys)} Groq key(s) have spent their daily "
                    "free-tier budget. Resets at 00:00 UTC."
                )

            sleep_for = max(0.25, min(soonest, 5.0))
            if waited + sleep_for > _MAX_POOL_WAIT:
                raise AllKeysBusyError(
                    f"All {len(self.keys)} Groq key(s) have been rate limited for "
                    f"over {_MAX_POOL_WAIT:.0f}s. Add another key or lower "
                    "MAX_CONCURRENT_FILE_REVIEWS."
                )
            await asyncio.sleep(sleep_for)
            waited += sleep_for

    def _nothing_available_error(self, tier: str, exclude: set[str]) -> Exception:
        if exclude and len(exclude) >= len(self.keys):
            return AllKeysBusyError(
                f"Every Groq key was rate limited on this request ({tier} tier). "
                "Retry shortly, or add another key to the pool."
            )
        return BudgetExhaustedError(
            "All Groq keys have spent their daily free-tier budget. "
            "Resets at 00:00 UTC."
        )

    # -------------------------------------------------------------- feedback

    def penalize(self, key: ApiKey, retry_after: float | None) -> float:
        """Take a 429'd key out of rotation. Returns the cooldown applied."""
        key.consecutive_429 += 1
        base = retry_after if retry_after else settings.groq_key_cooldown_sec
        # Back off further if this key keeps failing.
        cooldown = min(_MAX_COOLDOWN, max(_MIN_COOLDOWN, base * key.consecutive_429))
        key.cooldown_until = time.monotonic() + cooldown
        key.rotations += 1
        log.warning("keypool.cooldown", key=key.id, seconds=round(cooldown, 1),
                    consecutive=key.consecutive_429)
        return cooldown

    def mark_exhausted(self, key: ApiKey) -> None:
        key.exhausted_day = datetime.now(UTC).strftime("%Y%m%d")

    def record_success(self, key: ApiKey, tokens: int) -> None:
        key.consecutive_429 = 0
        key.tokens += tokens

    async def settle(self, key: ApiKey, tier: str, estimated: int, actual: int) -> None:
        await governor.settle(key.id, tier, estimated, actual)

    # -------------------------------------------------------------- reporting

    async def snapshot(self) -> dict:
        self.load()
        rows = []
        for key in self._keys:
            deep_req, deep_tok = await governor.usage_today(key.id, "deep")
            fast_req, fast_tok = await governor.usage_today(key.id, "fast")
            rows.append({
                "id": key.id,
                "state": key.state,
                "cooldown_remaining_s": key.cooldown_remaining(),
                "calls": key.calls,
                "tokens": key.tokens,
                "rotations": key.rotations,
                "deep_requests_today": deep_req,
                "deep_tokens_today": deep_tok,
                "fast_requests_today": fast_req,
                "fast_tokens_today": fast_tok,
            })
        return {
            "total": len(self._keys),
            "available": sum(1 for k in self._keys if k.state == "available"),
            "cooling": sum(1 for k in self._keys if k.state == "cooling"),
            "exhausted": sum(1 for k in self._keys if k.state == "exhausted"),
            "strategy": settings.groq_key_strategy,
            "keys": rows,
        }

    async def aclose(self) -> None:
        for key in self._keys:
            await key.aclose()

    def reset(self) -> None:
        """Drop all state. Used by tests."""
        self._keys = []
        self._cursor = 0
        self._loaded = False


key_pool = KeyPool()
