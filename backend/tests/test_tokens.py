from app.llm.tokens import estimate_messages_tokens, estimate_tokens, truncate_to_tokens


def test_estimate_scales_with_length():
    assert estimate_tokens("") == 0
    short, long = estimate_tokens("a" * 100), estimate_tokens("a" * 1000)
    assert long > short > 0


def test_code_estimates_denser_than_prose():
    code = "def f(x):\n    return {k: v for k, v in x.items() if v > 0}\n" * 5
    prose = "The quick brown fox jumps over the lazy dog every single day. " * 5
    # Same-ish length, but code should cost more tokens per character.
    assert estimate_tokens(code) / len(code) > estimate_tokens(prose) / len(prose)


def test_truncate_keeps_head_and_tail():
    text = "\n".join(f"line{i}" for i in range(5000))
    out = truncate_to_tokens(text, 200)
    assert estimate_tokens(out) <= 260  # allow for the marker
    assert out.startswith("line0")
    assert out.rstrip().endswith("line4999")
    assert "truncated" in out


def test_truncate_is_noop_when_within_budget():
    assert truncate_to_tokens("short", 1000) == "short"


def test_message_estimate_includes_framing():
    msgs = [{"role": "system", "content": "abc"}, {"role": "user", "content": "def"}]
    assert estimate_messages_tokens(msgs) > estimate_tokens("abcdef")
