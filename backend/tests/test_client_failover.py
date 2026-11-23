"""429 failover across the key pool, exercised through GroqLLM._dispatch."""

import httpx
import pytest
from groq import APIStatusError, RateLimitError

from app.core.errors import LLMRateLimitError
from app.llm.client import GroqLLM, rotation_sink
from app.llm.key_pool import KeyPool
from app.llm.rate_limiter import RateGovernor

K1 = "gsk_" + "a" * 48
K2 = "gsk_" + "b" * 48
K3 = "gsk_" + "c" * 48


def _rate_limit_error(retry_after: str | None = None) -> RateLimitError:
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(
        429,
        headers=headers,
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )
    return RateLimitError("rate limit reached", response=response, body=None)


def _status_error(code: int) -> APIStatusError:
    response = httpx.Response(
        code, request=httpx.Request("POST", "https://api.groq.com/v1/chat")
    )
    return APIStatusError(f"http {code}", response=response, body=None)


class _Usage:
    prompt_tokens = 100
    completion_tokens = 50
    total_tokens = 150


class _Resp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]
        self.usage = _Usage()


class _FakeClient:
    """Per-key fake. `behaviour` is an exception to raise, or None to succeed."""

    def __init__(self, label, behaviour=None):
        self.label = label
        self.behaviour = behaviour
        self.calls = 0
        outer = self

        class _Completions:
            async def create(self, **_kw):
                outer.calls += 1
                if outer.behaviour is not None:
                    raise outer.behaviour
                return _Resp(f'{{"served_by": "{outer.label}"}}')

        self.chat = type("Chat", (), {"completions": _Completions()})()

    async def close(self):
        pass


@pytest.fixture
def wired(monkeypatch):
    """Three keys, a private governor, and a fake client bound to each key."""
    from app.core import config
    from app.llm import client as client_mod
    from app.llm import key_pool as kp_mod

    monkeypatch.setattr(config.settings, "groq_api_key", K1)
    monkeypatch.setattr(config.settings, "groq_api_key_2", K2)
    monkeypatch.setattr(config.settings, "groq_api_key_3", K3)
    monkeypatch.setattr(config.settings, "groq_api_keys", "")
    monkeypatch.setattr(config.settings, "groq_key_strategy", "round_robin")

    governor = RateGovernor()
    monkeypatch.setattr(kp_mod, "governor", governor)

    pool = KeyPool()
    pool.load()
    monkeypatch.setattr(client_mod, "key_pool", pool)

    llm = GroqLLM()

    def bind(behaviours):
        """behaviours: list aligned with the three keys."""
        clients = []
        for key, behaviour in zip(pool.keys, behaviours, strict=True):
            fake = _FakeClient(key.id, behaviour)
            key._client = fake
            clients.append(fake)
        return clients

    return llm, pool, bind


async def _call(llm, **kw):
    return await llm._dispatch(
        [{"role": "user", "content": "hello"}],
        tier="deep", temperature=0.0, max_tokens=100, json_mode=False,
        agent="test", review_id=None, use_cache=False, schema_name=None,
        **kw,
    )


async def test_429_on_first_key_fails_over_to_another(wired):
    llm, pool, bind = wired
    clients = bind([_rate_limit_error("5"), None, None])

    result = await _call(llm)

    assert result.key_rotations == 1, "one rotation should have occurred"
    assert result.key_id != pool.keys[0].id, "must not be served by the 429'd key"
    assert clients[0].calls == 1, "the failing key was tried exactly once"
    assert '"served_by"' in result.content


async def test_the_429_key_is_cooled_down(wired):
    llm, pool, bind = wired
    bind([_rate_limit_error("9"), None, None])

    await _call(llm)

    failed = pool.keys[0]
    assert failed.state == "cooling"
    assert failed.cooldown_remaining() > 5
    assert failed.rotations == 1


async def test_cooled_key_is_avoided_by_later_calls(wired):
    llm, _, bind = wired
    clients = bind([_rate_limit_error("30"), None, None])

    await _call(llm)
    before = clients[0].calls
    for _ in range(4):
        await _call(llm)

    assert clients[0].calls == before, "a cooling key must not be retried"


async def test_two_bad_keys_still_succeed_on_the_third(wired):
    """Round-robin may skip a dead key entirely; what matters is that the
    request lands on the healthy one and no dead key is retried."""
    llm, pool, bind = wired
    clients = bind([_rate_limit_error("5"), _rate_limit_error("5"), None])

    result = await _call(llm)

    assert result.key_id == pool.keys[2].id
    assert result.key_rotations >= 1
    assert clients[2].calls == 1
    assert all(c.calls <= 1 for c in clients[:2]), "a 429'd key must not be retried"


async def test_all_keys_rate_limited_raises_after_exhausting_them(wired):
    llm, _, bind = wired
    bind([_rate_limit_error("1"), _rate_limit_error("1"), _rate_limit_error("1")])

    with pytest.raises(LLMRateLimitError, match="rate limited"):
        await _call(llm)


async def test_rotation_is_reported_to_the_trace(wired):
    llm, pool, bind = wired
    bind([_rate_limit_error("5"), None, None])

    seen = []

    async def sink(from_key, to_key, cooldown, agent):
        seen.append({"from": from_key, "to": to_key, "cooldown": cooldown, "agent": agent})

    token = rotation_sink.set(sink)
    try:
        await _call(llm)
    finally:
        rotation_sink.reset(token)

    assert len(seen) == 1
    assert seen[0]["from"] == pool.keys[0].id
    assert seen[0]["to"] is not None
    assert seen[0]["agent"] == "test"


async def test_unauthorized_key_is_dropped_and_another_serves(wired):
    """A revoked key should never be retried; the request still succeeds."""
    llm, pool, bind = wired
    clients = bind([_status_error(401), None, None])

    result = await _call(llm)

    assert pool.keys[0].state == "exhausted"
    assert result.key_id != pool.keys[0].id
    assert clients[0].calls == 1


async def test_successful_call_records_usage_against_its_key(wired):
    llm, pool, bind = wired
    bind([None, None, None])

    result = await _call(llm)
    served = next(k for k in pool.keys if k.id == result.key_id)

    assert served.tokens == 150
    assert served.calls == 1
    assert result.key_rotations == 0


async def test_load_spreads_over_the_pool(wired):
    llm, _, bind = wired
    clients = bind([None, None, None])

    for _ in range(9):
        await _call(llm)

    assert all(c.calls == 3 for c in clients), \
        f"expected even spread, got {[c.calls for c in clients]}"
