"""Free-tier governor, scoped per API key.

Groq's limits are enforced per key, so every bucket is namespaced by a key id.
With three keys in rotation the effective ceiling is three times a single
key's, provided each key's own budget is tracked separately -- which is what
`scope` does here.

For `openai/gpt-oss-120b` the free plan allows 30 RPM / 8K TPM / 1K RPD /
200K TPD **per key**. Tokens-per-minute is the binding constraint for code
review, so the governor blocks before dispatch instead of reacting to 429s.
Two windows are enforced per (key, tier):

  * a refilling token bucket for the per-minute limits (RPM, TPM)
  * a hard UTC-day counter for the per-day limits (RPD, TPD)

Backed by Redis when configured (so API and worker share one budget), and by
in-process state otherwise.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import settings
from app.core.errors import BudgetExhaustedError
from app.core.logging import get_logger

log = get_logger(__name__)

# Never sleep longer than this in one go; lets cancellation land promptly.
_MAX_SLEEP_SLICE = 5.0
# Refuse to wait longer than this for capacity on a single key.
_MAX_TOTAL_WAIT = 300.0


@dataclass(frozen=True)
class TierLimits:
    name: str
    rpm: int
    tpm: int
    rpd: int
    tpd: int


def limits_for(tier: str) -> TierLimits:
    if tier == "fast":
        return TierLimits(
            "fast",
            settings.groq_fast_rpm,
            settings.groq_fast_tpm,
            settings.groq_fast_rpd,
            settings.groq_fast_tpd,
        )
    return TierLimits(
        "deep",
        settings.groq_deep_rpm,
        settings.groq_deep_tpm,
        settings.groq_deep_rpd,
        settings.groq_deep_tpd,
    )


def _utc_day() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


# --------------------------------------------------------------------------
# Lua: atomic multi-bucket reserve. Returns 0 on success, else seconds to wait.
# --------------------------------------------------------------------------
_RESERVE_LUA = """
local req_key, tok_key = KEYS[1], KEYS[2]
local rpm, tpm = tonumber(ARGV[1]), tonumber(ARGV[2])
local now, want = tonumber(ARGV[3]), tonumber(ARGV[4])

local function level(key, cap, rate)
  local st = redis.call('HMGET', key, 'v', 't')
  local v = tonumber(st[1])
  local t = tonumber(st[2])
  if v == nil then return cap, now end
  local refilled = math.min(cap, v + (now - t) * rate)
  return refilled, now
end

local rreq = level(req_key, rpm, rpm / 60.0)
local rtok = level(tok_key, tpm, tpm / 60.0)

if rreq < 1 then
  return tostring((1 - rreq) / (rpm / 60.0))
end
if rtok < want then
  return tostring((want - rtok) / (tpm / 60.0))
end

redis.call('HMSET', req_key, 'v', rreq - 1, 't', now)
redis.call('EXPIRE', req_key, 300)
redis.call('HMSET', tok_key, 'v', rtok - want, 't', now)
redis.call('EXPIRE', tok_key, 300)
return "0"
"""


class _LocalBucket:
    __slots__ = ("capacity", "rate", "stamp", "value")

    def __init__(self, capacity: float, rate: float) -> None:
        self.capacity = capacity
        self.rate = rate
        self.value = capacity
        self.stamp = time.monotonic()

    def _refill(self, now: float) -> None:
        self.value = min(self.capacity, self.value + (now - self.stamp) * self.rate)
        self.stamp = now

    def try_take(self, amount: float) -> float:
        """Take `amount`; return 0.0 on success or seconds to wait."""
        now = time.monotonic()
        self._refill(now)
        if self.value < amount:
            return (amount - self.value) / self.rate if self.rate > 0 else _MAX_TOTAL_WAIT
        self.value -= amount
        return 0.0

    def give_back(self, amount: float) -> None:
        self.value = min(self.capacity, self.value + amount)


class RateGovernor:
    """Per-key, per-tier budget enforcer shared by every agent and process."""

    def __init__(self) -> None:
        self._redis = None
        self._script = None
        self._lock = asyncio.Lock()
        self._local: dict[str, tuple[_LocalBucket, _LocalBucket]] = {}
        self._local_daily: dict[str, dict] = {}

    @staticmethod
    def _slot(scope: str, tier: str) -> str:
        return f"{scope}:{tier}"

    async def _get_redis(self):
        if not settings.has_redis:
            return None
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                settings.redis_url, encoding="utf-8", decode_responses=True
            )
            self._script = self._redis.register_script(_RESERVE_LUA)
        return self._redis

    # -- daily caps ---------------------------------------------------------

    async def _charge_daily(self, scope: str, tier: str, want: int) -> None:
        """Debit the per-day counters. Called only once a call really goes out."""
        slot = self._slot(scope, tier)
        day = _utc_day()

        r = await self._get_redis()
        if r is not None:
            pipe = r.pipeline()
            pipe.incr(f"crg:{slot}:rpd:{day}", 1)
            pipe.expire(f"crg:{slot}:rpd:{day}", 172800)
            pipe.incrby(f"crg:{slot}:tpd:{day}", want)
            pipe.expire(f"crg:{slot}:tpd:{day}", 172800)
            await pipe.execute()
            return

        st = self._local_daily.get(slot)
        if st is None or st["day"] != day:
            st = {"day": day, "req": 0, "tok": 0}
            self._local_daily[slot] = st
        st["req"] += 1
        st["tok"] += want

    async def check_daily(self, scope: str, tier: str, want: int) -> None:
        """Raise if this key has no daily budget left. Read-only.

        Deliberately does not increment: selection probes several keys per
        dispatch and retries while waiting for capacity, so charging here
        would burn the daily quota on calls that never happen.
        """
        lim = limits_for(tier)
        used_req, used_tok = await self.usage_today(scope, tier)

        if used_req >= lim.rpd:
            raise BudgetExhaustedError(
                f"Key '{scope}' hit its daily request cap for the '{tier}' tier "
                f"({lim.rpd}/day on Groq's free plan). Resets at 00:00 UTC."
            )
        if used_tok + want > lim.tpd:
            raise BudgetExhaustedError(
                f"Key '{scope}' hit its daily token cap for the '{tier}' tier "
                f"({lim.tpd:,}/day on Groq's free plan). Resets at 00:00 UTC."
            )

    # -- per-minute caps ----------------------------------------------------

    async def try_acquire(self, scope: str, tier: str, estimated_tokens: int) -> float:
        """Non-blocking reserve. Returns 0.0 if granted, else seconds to wait.

        Raises BudgetExhaustedError if this key's daily cap is spent — the pool
        uses that to drop the key from rotation rather than to fail the run.
        """
        lim = limits_for(tier)
        want = max(1, min(estimated_tokens, lim.tpm))
        await self.check_daily(scope, tier, want)

        wait = await self._try_reserve(scope, tier, want)
        if wait <= 0:
            # Only a granted reservation costs daily budget.
            await self._charge_daily(scope, tier, want)
        return wait

    async def _try_reserve(self, scope: str, tier: str, want: int) -> float:
        lim = limits_for(tier)
        slot = self._slot(scope, tier)

        r = await self._get_redis()
        if r is not None:
            wait = await self._script(
                keys=[f"crg:{slot}:req", f"crg:{slot}:tok"],
                args=[lim.rpm, lim.tpm, time.time(), want],
            )
            return float(wait)

        async with self._lock:
            if slot not in self._local:
                self._local[slot] = (
                    _LocalBucket(lim.rpm, lim.rpm / 60.0),
                    _LocalBucket(lim.tpm, lim.tpm / 60.0),
                )
            req_b, tok_b = self._local[slot]
            wait = req_b.try_take(1)
            if wait > 0:
                return wait
            wait = tok_b.try_take(want)
            if wait > 0:
                req_b.give_back(1)  # don't burn a request slot we didn't use
                return wait
            return 0.0

    async def acquire(self, scope: str, tier: str, estimated_tokens: int) -> float:
        """Block until this key may spend `estimated_tokens`. Returns seconds waited."""
        lim = limits_for(tier)
        want = max(1, min(estimated_tokens, lim.tpm))
        if estimated_tokens > lim.tpm:
            log.warning(
                "governor.request_exceeds_tpm",
                scope=scope, tier=tier, estimated=estimated_tokens, tpm=lim.tpm,
            )

        await self.check_daily(scope, tier, want)

        waited = 0.0
        while True:
            wait = await self._try_reserve(scope, tier, want)
            if wait <= 0:
                await self._charge_daily(scope, tier, want)
                if waited > 1.0:
                    log.info("governor.released", scope=scope, tier=tier,
                             waited_s=round(waited, 1))
                return waited
            if waited + wait > _MAX_TOTAL_WAIT:
                raise BudgetExhaustedError(
                    f"Rate limit backlog exceeds {_MAX_TOTAL_WAIT:.0f}s on key "
                    f"'{scope}' ({tier} tier). Add another key or reduce concurrency."
                )
            slice_s = min(wait + 0.05, _MAX_SLEEP_SLICE)
            await asyncio.sleep(slice_s)
            waited += slice_s

    async def settle(self, scope: str, tier: str, estimated: int, actual: int) -> None:
        """Reconcile the reservation against real usage from the API response."""
        delta = actual - estimated
        if delta == 0:
            return
        slot = self._slot(scope, tier)

        r = await self._get_redis()
        if r is not None:
            await r.hincrbyfloat(f"crg:{slot}:tok", "v", float(-delta))
            await r.incrby(f"crg:{slot}:tpd:{_utc_day()}", delta)
            return

        async with self._lock:
            if slot in self._local:
                _, tok_b = self._local[slot]
                if delta > 0:
                    tok_b.try_take(delta)
                else:
                    tok_b.give_back(-delta)
            st = self._local_daily.get(slot)
            if st:
                st["tok"] += delta

    async def usage_today(self, scope: str, tier: str) -> tuple[int, int]:
        """(requests, tokens) charged to this key today."""
        slot = self._slot(scope, tier)
        day = _utc_day()
        r = await self._get_redis()
        if r is not None:
            req = int(await r.get(f"crg:{slot}:rpd:{day}") or 0)
            tok = int(await r.get(f"crg:{slot}:tpd:{day}") or 0)
            return req, tok
        st = self._local_daily.get(slot, {})
        if st.get("day") != day:
            return 0, 0
        return st.get("req", 0), st.get("tok", 0)

    async def snapshot(self, scopes: list[str]) -> dict:
        """Budget rolled up across every key, for /health and the dashboard."""
        out: dict = {
            "backend": "redis" if settings.has_redis else "in-process",
            "keys_in_pool": len(scopes),
        }
        for tier in ("deep", "fast"):
            lim = limits_for(tier)
            used_req = used_tok = 0
            for scope in scopes:
                req, tok = await self.usage_today(scope, tier)
                used_req += req
                used_tok += tok
            pool_rpd = lim.rpd * max(len(scopes), 1)
            pool_tpd = lim.tpd * max(len(scopes), 1)
            out[tier] = {
                "rpm_per_key": lim.rpm,
                "tpm_per_key": lim.tpm,
                "requests_today": used_req,
                "requests_per_day": pool_rpd,
                "tokens_today": used_tok,
                "tokens_per_day": pool_tpd,
                "day_pct": round(100 * used_tok / pool_tpd, 1) if pool_tpd else 0.0,
            }
        return out

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


governor = RateGovernor()
