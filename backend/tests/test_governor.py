import asyncio
import time

import pytest

from app.core.errors import BudgetExhaustedError
from app.llm.rate_limiter import RateGovernor, _LocalBucket

KEY = "key1:testaa"


def test_bucket_grants_then_throttles():
    bucket = _LocalBucket(capacity=100, rate=10)
    assert bucket.try_take(60) == 0.0
    assert bucket.try_take(30) == 0.0
    wait = bucket.try_take(50)
    assert wait > 0, "should report a wait once drained"
    # 100 capacity - 60 - 30 = 10 left; a 50 ask is 40 short at 10/sec = 4s
    assert wait == pytest.approx(4.0, abs=0.2)


def test_bucket_refills_over_time():
    bucket = _LocalBucket(capacity=100, rate=1000)
    bucket.try_take(100)
    time.sleep(0.05)
    assert bucket.try_take(10) == 0.0


def test_giving_back_never_exceeds_capacity():
    bucket = _LocalBucket(capacity=50, rate=1)
    bucket.give_back(500)
    assert bucket.value == 50


async def test_acquire_is_immediate_when_budget_is_free():
    gov = RateGovernor()
    waited = await gov.acquire(KEY, "deep", 100)
    assert waited == 0.0


async def test_acquire_blocks_once_tpm_is_exhausted(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "groq_deep_tpm", 600)   # 10 tokens/sec
    monkeypatch.setattr(config.settings, "groq_deep_rpm", 600)

    gov = RateGovernor()
    await gov.acquire(KEY, "deep", 600)          # drains the minute bucket
    started = time.monotonic()
    await gov.acquire(KEY, "deep", 20)           # needs ~2s of refill
    assert time.monotonic() - started > 1.0


async def test_request_slot_is_returned_when_tokens_are_short(monkeypatch):
    """A request denied for tokens must not silently burn an RPM slot."""
    from app.core import config
    monkeypatch.setattr(config.settings, "groq_deep_tpm", 100)
    monkeypatch.setattr(config.settings, "groq_deep_rpm", 2)

    gov = RateGovernor()
    await gov._try_reserve(KEY, "deep", 100)        # drains tokens, uses 1 request
    wait = await gov._try_reserve(KEY, "deep", 100) # denied on tokens
    assert wait > 0

    req_bucket, _ = gov._local[f"{KEY}:deep"]
    assert req_bucket.value >= 0.9, "the refused request must not consume a slot"


async def test_daily_token_cap_raises(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "groq_deep_tpd", 500)
    gov = RateGovernor()
    with pytest.raises(BudgetExhaustedError, match="daily token cap"):
        for _ in range(10):
            await gov.acquire(KEY, "deep", 100)


async def test_daily_request_cap_raises(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "groq_deep_rpd", 3)
    gov = RateGovernor()
    with pytest.raises(BudgetExhaustedError, match="daily request cap"):
        for _ in range(5):
            await gov.acquire(KEY, "deep", 1)


async def test_settle_reconciles_an_underestimate(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "groq_deep_tpm", 1000)
    gov = RateGovernor()
    await gov.acquire(KEY, "deep", 100)
    _, tok = gov._local[f"{KEY}:deep"]
    before = tok.value
    await gov.settle(KEY, "deep", estimated=100, actual=300)
    assert tok.value < before, "extra real usage must be deducted"


async def test_snapshot_shape():
    gov = RateGovernor()
    await gov.acquire(KEY, "fast", 50)
    snap = await gov.snapshot([KEY])
    assert snap["backend"] == "in-process"
    assert snap["fast"]["tokens_today"] >= 50
    assert 0 <= snap["deep"]["day_pct"] <= 100


async def test_concurrent_acquires_are_serialised(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "groq_deep_tpm", 6000)
    gov = RateGovernor()
    await asyncio.gather(*(gov.acquire(KEY, "deep", 500) for _ in range(8)))
    _, tok = gov._local[f"{KEY}:deep"]
    assert tok.value <= 2100, "every concurrent call must debit the shared bucket"


async def test_refused_probes_do_not_burn_daily_quota(monkeypatch):
    """Key selection probes several keys per dispatch and retries while
    waiting. Charging the daily counter on a probe rather than on an actual
    call exhausts the day's budget after a handful of real requests."""
    from app.core import config

    monkeypatch.setattr(config.settings, "groq_deep_tpm", 1000)
    monkeypatch.setattr(config.settings, "groq_deep_tpd", 500_000)
    gov = RateGovernor()

    granted = await gov.try_acquire(KEY, "deep", 900)
    assert granted == 0.0

    for _ in range(40):
        assert await gov.try_acquire(KEY, "deep", 900) > 0, "bucket should be empty"

    requests, tokens = await gov.usage_today(KEY, "deep")
    assert requests == 1, f"only the granted call should be charged, got {requests}"
    assert tokens == 900


async def test_blocking_acquire_charges_exactly_once(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "groq_deep_tpm", 60_000)
    gov = RateGovernor()
    for _ in range(3):
        await gov.acquire(KEY, "deep", 100)

    requests, tokens = await gov.usage_today(KEY, "deep")
    assert requests == 3
    assert tokens == 300
