"""Connectivity check: `python -m app.llm.diagnose`

Makes one minimal real call per configured key and prints exactly what Groq
says. Run this first whenever a review fails with a provider error -- it
isolates key/model/config problems from anything in the pipeline.
"""

from __future__ import annotations

import asyncio
import sys

from app.core.config import settings
from app.core.logging import configure_logging
from app.llm.client import describe_api_error
from app.llm.key_pool import key_pool
from app.llm.schema import strict_json_schema
from app.schemas.agent import ProjectProfile

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


async def _probe(key, model: str, mode: str) -> tuple[bool, str]:
    """One small request in `mode`. Returns (ok, description).

    Reasoning models spend completion tokens on their reasoning trace, so the
    budget here is deliberately generous — a tiny one returns empty content and
    looks like a failure when nothing is actually wrong.
    """
    kwargs = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": "Profile this project as JSON: a FastAPI service for "
                       "invoice reconciliation, backed by Postgres.",
        }],
        "max_completion_tokens": 1200,
        "temperature": 0.1,
    }
    if mode == "strict schema":
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": strict_json_schema(ProjectProfile),
        }
    elif mode == "json object":
        kwargs["response_format"] = {"type": "json_object"}

    try:
        resp = await key.client.chat.completions.create(**kwargs)
        content = (resp.choices[0].message.content or "").strip()
        used = getattr(resp.usage, "total_tokens", 0)
        if mode == "strict schema":
            ProjectProfile.model_validate_json(content)
            return True, f"{used} tokens · schema validated"
        if not content:
            return False, f"{used} tokens but empty content — raise max_completion_tokens"
        return True, f"{used} tokens · {content[:44]}"
    except Exception as exc:
        message, code = describe_api_error(exc)
        status = getattr(exc, "status_code", None)
        if status is None:
            return False, f"{type(exc).__name__}: {message[:110]}"
        return False, f"HTTP {status}" + (f" [{code}]" if code else "") + f" — {message[:110]}"


async def main() -> int:
    configure_logging()
    key_pool.load()

    print(f"\n{DIM}Groq configuration check{RESET}")
    print(f"  deep model : {settings.groq_model_deep}")
    print(f"  fast model : {settings.groq_model_fast}")
    print(f"  keys       : {key_pool.size}")
    print(f"  strategy   : {settings.groq_key_strategy}\n")

    if not key_pool.size:
        print(f"{RED}No API keys configured.{RESET} Set GROQ_API_KEY in .env — "
              "free keys at https://console.groq.com/keys\n")
        return 1

    dead_keys, failures = [], 0

    for key in key_pool.keys:
        print(f"{DIM}{key.id}{RESET}")
        key_failed = 0
        for label, model in (
            ("deep", settings.groq_model_deep),
            ("fast", settings.groq_model_fast),
        ):
            # "strict schema" is the mode the agents actually use.
            for mode in ("strict schema", "plain"):
                ok, detail = await _probe(key, model, mode)
                mark = f"{GREEN}ok  {RESET}" if ok else f"{RED}FAIL{RESET}"
                if not ok:
                    failures += 1
                    key_failed += 1
                print(f"   {mark} {label:<5} {mode:<14} {model:<24} {detail}")
        if key_failed == 4:
            dead_keys.append(key.id)
        print()

    usable = len(key_pool.keys) - len(dead_keys)
    if dead_keys:
        print(f"{YELLOW}Unusable key(s): {', '.join(dead_keys)}{RESET}")
        print("  Replace them in .env. The pool drops a rejected key automatically,")
        print(f"  so reviews still run on the remaining {usable}.\n")

    if failures and usable:
        print(f"{YELLOW}{failures} probe(s) failed, but {usable} key(s) work — "
              f"reviews will run.{RESET}\n")
        return 0
    if failures:
        print(f"{RED}No usable keys.{RESET}")
        print("  · 'organization has been restricted' → that Groq account is blocked")
        print("  · 'decommissioned' / 'does not exist' → update GROQ_MODEL_DEEP or")
        print("    GROQ_MODEL_FAST (see https://console.groq.com/docs/models)")
        print("  · 'invalid api key' → re-copy the key from the Groq console\n")
        return 1

    print(f"{GREEN}All probes passed.{RESET} {usable} key(s) usable.\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
