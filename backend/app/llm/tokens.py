from __future__ import annotations

import re

# Groq does not expose a public tokenizer endpoint, so we estimate.
# Empirically, source code tokenizes denser than prose on BPE vocabularies:
# prose ~4.0 chars/token, code ~3.2 (punctuation, indentation, identifiers).
# We deliberately bias LOW on chars-per-token (= estimate HIGH on tokens) so
# the rate governor reserves more than it needs rather than less.
_CHARS_PER_TOKEN_CODE = 3.0
_CHARS_PER_TOKEN_PROSE = 3.8

_CODEY = re.compile(r"[{}()\[\];=<>+\-*/&|!#@$]")


def estimate_tokens(text: str) -> int:
    """Conservative (over-)estimate of the token count for `text`."""
    if not text:
        return 0
    sample = text[:4000]
    density = len(_CODEY.findall(sample)) / max(len(sample), 1)
    cpt = _CHARS_PER_TOKEN_CODE if density > 0.03 else _CHARS_PER_TOKEN_PROSE
    return int(len(text) / cpt) + 8


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate for a chat payload, including per-message framing overhead."""
    total = 0
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, list):  # multimodal blocks
            content = "".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        total += estimate_tokens(str(content)) + 4
    return total + 3


def truncate_to_tokens(text: str, max_tokens: int, *, marker: str = "\n… [truncated] …\n") -> str:
    """Trim text so its estimate fits `max_tokens`, keeping head and tail.

    Head+tail beats head-only for code: the tail usually holds exports,
    main guards, and route registrations that define the file's contract.
    """
    if estimate_tokens(text) <= max_tokens:
        return text
    budget_chars = int(max_tokens * _CHARS_PER_TOKEN_CODE) - len(marker)
    if budget_chars <= 0:
        return marker
    head = int(budget_chars * 0.65)
    tail = budget_chars - head
    return text[:head] + marker + text[-tail:]
