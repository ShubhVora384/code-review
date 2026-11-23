import itertools
import time

import pytest

from app.core.errors import BudgetExhaustedError
from app.llm.key_pool import AllKeysBusyError, KeyPool
from app.llm.rate_limiter import RateGovernor

K1 = "gsk_" + "a" * 48
K2 = "gsk_" + "b" * 48
K3 = "gsk_" + "c" * 48


@pytest.fixture
def pool(monkeypatch):
    """A three-key pool with a private governor, so tests never share budget."""
    from app.core import config
    from app.llm import key_pool as kp_mod

    monkeypatch.setattr(config.settings, "groq_api_key", K1)
    monkeypatch.setattr(config.settings, "groq_api_key_2", K2)
    monkeypatch.setattr(config.settings, "groq_api_key_3", K3)
    monkeypatch.setattr(config.settings, "groq_api_keys", "")
    monkeypatch.setattr(config.settings, "groq_key_strategy", "round_robin")
    monkeypatch.setattr(kp_mod, "governor", RateGovernor())

    p = KeyPool()
    p.load()
    return p


def test_pool_loads_every_configured_key(pool):
    assert pool.size == 3
    assert [k.id.split(":")[0] for k in pool.keys] == ["key1", "key2", "key3"]


def test_fingerprints_never_expose_the_secret(pool):
    for key in pool.keys:
        assert K1 not in key.id and K2 not in key.id and K3 not in key.id
        assert len(key.id) < 20


def test_duplicate_and_placeholder_keys_are_dropped(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "groq_api_key", K1)
    monkeypatch.setattr(config.settings, "groq_api_key_2", K1)      # duplicate
    monkeypatch.setattr(config.settings, "groq_api_key_3", "gsk_replace_me")
    monkeypatch.setattr(config.settings, "groq_api_keys", f"{K2}, , {K1}")

    p = KeyPool()
    p.load()
    assert p.size == 2  # K1 once, K2 once


async def test_round_robin_spreads_load(pool):
    picked = [(await pool.select("deep", 10)).id for _ in range(6)]
    # Every key must be used, and no key twice in a row.
    assert len(set(picked)) == 3
    assert all(a != b for a, b in itertools.pairwise(picked))


async def test_random_strategy_uses_all_keys(pool, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "groq_key_strategy", "random")
    picked = {(await pool.select("deep", 10)).id for _ in range(30)}
    assert picked == {k.id for k in pool.keys}


async def test_least_used_prefers_the_quietest_key(pool, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "groq_key_strategy", "least_used")

    pool.keys[0].tokens = 5_000
    pool.keys[1].tokens = 100
    pool.keys[2].tokens = 900

    chosen = await pool.select("deep", 10)
    assert chosen.id == pool.keys[1].id


async def test_cooling_key_is_skipped(pool):
    first = pool.keys[0]
    pool.penalize(first, retry_after=30)
    assert first.state == "cooling"

    picked = {(await pool.select("deep", 10)).id for _ in range(6)}
    assert first.id not in picked


async def test_cooldown_backs_off_on_repeated_429s(pool):
    key = pool.keys[0]
    first = pool.penalize(key, retry_after=None)
    key.cooldown_until = 0.0        # expire it so we can penalize again
    second = pool.penalize(key, retry_after=None)
    assert second > first, "consecutive failures should back off further"


async def test_retry_after_is_honoured(pool):
    key = pool.keys[0]
    cooldown = pool.penalize(key, retry_after=7)
    assert 6.5 <= cooldown <= 7.5
    assert key.cooldown_remaining() > 5


async def test_exhausted_key_leaves_rotation(pool):
    dead = pool.keys[1]
    pool.mark_exhausted(dead)
    assert dead.state == "exhausted"

    picked = {(await pool.select("deep", 10)).id for _ in range(6)}
    assert dead.id not in picked
    assert len(picked) == 2


async def test_all_keys_exhausted_raises(pool):
    for key in pool.keys:
        pool.mark_exhausted(key)
    with pytest.raises(BudgetExhaustedError, match="daily"):
        await pool.select("deep", 10)


async def test_daily_cap_on_one_key_falls_through_to_another(pool, monkeypatch):
    """A key hitting its per-day cap must not fail the request."""
    from app.core import config
    monkeypatch.setattr(config.settings, "groq_deep_rpd", 2)

    used = set()
    for _ in range(6):
        used.add((await pool.select("deep", 10)).id)

    # Each key allows 2 requests/day, so all three get used before anything fails.
    assert len(used) == 3


async def test_excluded_keys_are_not_returned(pool):
    banned = {pool.keys[0].id, pool.keys[1].id}
    chosen = await pool.select("deep", 10, exclude=banned)
    assert chosen.id == pool.keys[2].id


async def test_excluding_everything_reports_all_busy(pool):
    banned = {k.id for k in pool.keys}
    with pytest.raises(AllKeysBusyError):
        await pool.select("deep", 10, exclude=banned)


async def test_tpm_pressure_moves_to_the_next_key(pool, monkeypatch):
    """One key's minute budget being spent must not stall the pool."""
    from app.core import config
    monkeypatch.setattr(config.settings, "groq_deep_tpm", 1000)

    started = time.monotonic()
    picked = [(await pool.select("deep", 900)).id for _ in range(3)]
    elapsed = time.monotonic() - started

    assert len(set(picked)) == 3, "each 900-token call should land on a fresh key"
    assert elapsed < 1.0, "with free keys available there should be no waiting"


async def test_snapshot_reports_state_without_secrets(pool):
    await pool.select("deep", 10)
    pool.penalize(pool.keys[1], retry_after=15)
    pool.mark_exhausted(pool.keys[2])

    snap = await pool.snapshot()
    assert snap["total"] == 3
    assert snap["available"] == 1
    assert snap["cooling"] == 1
    assert snap["exhausted"] == 1
    assert snap["strategy"] == "round_robin"

    blob = repr(snap)
    assert K1 not in blob and K2 not in blob and K3 not in blob


async def test_empty_pool_raises_a_helpful_error(monkeypatch):
    from app.core import config
    from app.core.errors import LLMError

    for attr in ("groq_api_key", "groq_api_key_2", "groq_api_key_3", "groq_api_keys"):
        monkeypatch.setattr(config.settings, attr, "")

    p = KeyPool()
    with pytest.raises(LLMError, match="No Groq API key"):
        await p.select("deep", 10)


def test_record_success_clears_the_failure_streak(pool):
    key = pool.keys[0]
    pool.penalize(key, retry_after=5)
    assert key.consecutive_429 == 1
    pool.record_success(key, tokens=1234)
    assert key.consecutive_429 == 0
    assert key.tokens == 1234
