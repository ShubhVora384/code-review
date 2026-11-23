"""Groq chat client with structured output, caching and budget enforcement.

Every agent call funnels through `GroqLLM.structured()` or `.text()` so that
rate limiting, retries, caching and usage logging happen in exactly one place.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextvars import ContextVar
from typing import Any, TypeVar

from groq import APIConnectionError, APIStatusError, RateLimitError
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.errors import LLMError, LLMRateLimitError
from app.core.logging import get_logger
from app.llm.cache import cache, make_key
from app.llm.key_pool import AllKeysBusyError, key_pool
from app.llm.rate_limiter import limits_for
from app.llm.schema import strict_json_schema, supports_strict_dicts
from app.llm.tokens import estimate_messages_tokens

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_MAX_ATTEMPTS = 4
_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# Per-review usage sink. A ContextVar (not an attribute) so concurrent
# reviews sharing this singleton each log to their own ledger.
usage_sink: ContextVar = ContextVar("llm_usage_sink", default=None)

# Notified when a 429 forces a switch to another key, so the agent trace
# can show the rotation instead of it happening invisibly.
rotation_sink: ContextVar = ContextVar("llm_rotation_sink", default=None)


class SchemaRejectedError(LLMError):
    """Groq refused the request because of the response schema.

    Recoverable: `structured()` retries with constrained decoding switched
    off and lets Pydantic do the validating instead.
    """

    code = "schema_rejected"


class LLMResult(BaseModel):
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    attempts: int = 1
    cache_hit: bool = False
    key_id: str | None = None
    key_rotations: int = 0


def _extract_json(raw: str) -> str:
    """Pull a JSON object out of a model response that may be fenced or prefixed."""
    text = raw.strip()
    fenced = _JSON_BLOCK.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def _schema_hint(model_cls: type[BaseModel]) -> str:
    schema = model_cls.model_json_schema()
    return json.dumps(schema, indent=2, ensure_ascii=False)


class GroqLLM:
    """Single entry point to Groq for all agents."""

    @staticmethod
    def model_for(tier: str) -> str:
        return settings.groq_model_fast if tier == "fast" else settings.groq_model_deep

    # ------------------------------------------------------------------ core

    async def _dispatch(
        self,
        messages: list[dict],
        *,
        tier: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        agent: str,
        review_id: str | None,
        use_cache: bool,
        schema_name: str | None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResult:
        """Send one chat completion, rotating keys as budgets and 429s dictate.

        Each attempt asks the pool for whichever key can serve soonest, and a
        rate-limited key is excluded from the remaining attempts of *this*
        request as well as cooled down globally. With several keys configured a
        429 therefore costs one immediate retry rather than a stall.
        """
        model = self.model_for(tier)
        cache_key = make_key(model, messages, schema_name) if use_cache else None

        if cache_key:
            hit = await cache.get(cache_key)
            if hit is not None:
                log.debug("llm.cache_hit", agent=agent, model=model)
                return LLMResult(content=hit, model=model, cache_hit=True)

        # A single request cannot exceed the per-minute token limit: Groq
        # counts prompt + completion against TPM and rejects the call outright
        # ("Request too large ... Limit 8000, Requested 8870"). So the
        # completion budget is whatever is left after the prompt, not whatever
        # the agent asked for.
        prompt_tokens = estimate_messages_tokens(messages)
        max_tokens = _fit_completion_budget(prompt_tokens, max_tokens, tier, agent)

        estimated = prompt_tokens + max_tokens
        started = time.perf_counter()
        tried: set[str] = set()
        rotations = 0
        last_err: Exception | None = None

        # One attempt per key, plus a couple of extra passes for transient faults.
        max_attempts = max(_MAX_ATTEMPTS, key_pool.size + 1)

        for attempt in range(1, max_attempts + 1):
            try:
                api_key = await key_pool.select(tier, estimated, exclude=tried)
            except AllKeysBusyError as exc:
                if last_err is not None:
                    break  # surface the underlying API error instead
                raise exc

            log.info(
                "llm.request",
                agent=agent,
                key=api_key.id,
                model=model,
                tier=tier,
                est_tokens=estimated,
                json_mode=json_mode,
                attempt=attempt,
            )

            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    # max_tokens is deprecated on Groq in favour of this.
                    "max_completion_tokens": max_tokens,
                }
                if _is_reasoning_model(model) and settings.groq_reasoning_effort:
                    # Sent via extra_body: `reasoning_effort` post-dates the
                    # pinned SDK, and extra_body passes it through untouched.
                    kwargs["extra_body"] = {
                        "reasoning_effort": settings.groq_reasoning_effort
                    }
                if response_format is not None:
                    kwargs["response_format"] = response_format
                elif json_mode:
                    kwargs["response_format"] = {"type": "json_object"}

                resp = await api_key.client.chat.completions.create(**kwargs)
                content = (resp.choices[0].message.content or "").strip()
                usage = resp.usage
                total = getattr(usage, "total_tokens", 0) or 0

                await key_pool.settle(api_key, tier, estimated, total)
                key_pool.record_success(api_key, total)

                result = LLMResult(
                    content=content,
                    model=model,
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    total_tokens=total,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    attempts=attempt,
                    key_id=api_key.id,
                    key_rotations=rotations,
                )
                log.info(
                    "llm.response",
                    agent=agent,
                    key=api_key.id,
                    tokens=total,
                    ms=result.latency_ms,
                    rotations=rotations,
                )
                if cache_key:
                    await cache.set(cache_key, content)
                await self._log_usage(agent, review_id, result, ok=True)
                return result

            except RateLimitError as exc:
                last_err = exc
                retry_after = _retry_after(exc, default=None)
                cooldown = key_pool.penalize(api_key, retry_after)
                tried.add(api_key.id)
                rotations += 1

                remaining = [
                    k for k in key_pool.keys
                    if k.id not in tried and not k.is_cooling() and not k.is_exhausted()
                ]
                await self._announce_rotation(
                    api_key.id,
                    remaining[0].id if remaining else None,
                    cooldown,
                    agent,
                )
                if remaining:
                    continue  # another key is ready right now — no sleep
                # Nothing else free: wait out the shortest cooldown.
                await asyncio.sleep(min(cooldown, 10.0))
                tried.clear()  # let cooled-down keys back in

            except APIStatusError as exc:
                last_err = exc
                status = exc.status_code
                groq_message, groq_code = describe_api_error(exc)

                if status in (401, 403) or _is_key_fatal(groq_code, groq_message):
                    # This credential will never work (revoked, or the account
                    # is restricted). Drop it and let another key serve.
                    key_pool.mark_exhausted(api_key)
                    tried.add(api_key.id)
                    log.error(
                        "llm.key_rejected",
                        key=api_key.id, status=status, groq_code=groq_code,
                        groq_error=groq_message,
                    )
                    healthy = [
                        k for k in key_pool.keys
                        if k.id not in tried and not k.is_exhausted()
                    ]
                    if healthy:
                        await self._announce_rotation(
                            api_key.id, healthy[0].id, 0.0, agent
                        )
                        continue
                    raise LLMError(
                        f"Every Groq key was rejected by the API. Last reason: "
                        f"{groq_message}",
                        detail=f"key {api_key.id} · HTTP {status}"
                               + (f" · code: {groq_code}" if groq_code else ""),
                    ) from exc

                if status and 400 <= status < 500 and status != 429:
                    log.error(
                        "llm.request_rejected",
                        agent=agent, key=api_key.id, model=model, status=status,
                        groq_code=groq_code, groq_error=groq_message,
                    )
                    if response_format is not None and _is_schema_rejection(
                        groq_code, groq_message
                    ):
                        raise SchemaRejectedError(
                            f"{model} rejected the strict response schema.",
                            detail=groq_message[:600],
                        ) from exc
                    await self._log_usage(
                        agent, review_id, None, ok=False, error=groq_message
                    )
                    raise LLMError(
                        _explain_rejection(status, groq_code, groq_message, model),
                        detail=(
                            f"Groq said: {groq_message}\n"
                            f"HTTP {status}"
                            + (f" · code: {groq_code}" if groq_code else "")
                            + f"\nmodel: {model} · tier: {tier} · agent: {agent}"
                        ),
                    ) from exc

                log.warning(
                    "llm.server_error",
                    agent=agent, key=api_key.id, status=status, groq_error=groq_message,
                )
                await asyncio.sleep(min(15.0, 2.0 * attempt))

            except (TimeoutError, APIConnectionError) as exc:
                last_err = exc
                await asyncio.sleep(min(10.0, 1.5 * attempt))

        await self._log_usage(agent, review_id, None, ok=False, error=str(last_err))
        if isinstance(last_err, RateLimitError):
            raise LLMRateLimitError(
                f"Every configured Groq key ({key_pool.size}) is rate limited. "
                f"The free tier allows {settings.groq_deep_tpm:,} tokens/min per "
                "key — add another key or try again shortly."
            ) from last_err
        raise LLMError(
            f"Groq call failed after {max_attempts} attempts.",
            detail=str(last_err)[:800],
        ) from last_err

    @staticmethod
    async def _announce_rotation(
        from_key: str, to_key: str | None, cooldown: float, agent: str
    ) -> None:
        sink = rotation_sink.get()
        if sink is None:
            return
        try:
            await sink(from_key, to_key, cooldown, agent)
        except Exception:  # a trace event must never break a review
            log.debug("llm.rotation_sink_failed", exc_info=True)

    async def _log_usage(
        self,
        agent: str,
        review_id: str | None,
        result: LLMResult | None,
        *,
        ok: bool,
        error: str | None = None,
    ) -> None:
        sink = usage_sink.get()
        if sink is None:
            return
        try:
            await sink(agent, review_id, result, ok, error)
        except Exception:  # logging must never break a review
            log.warning("llm.usage_sink_failed", agent=agent, exc_info=True)

    # ------------------------------------------------------------- public API

    async def text(
        self,
        messages: list[dict],
        *,
        tier: str = "deep",
        temperature: float = 0.2,
        max_tokens: int = 2048,
        agent: str = "unknown",
        review_id: str | None = None,
        use_cache: bool = True,
    ) -> LLMResult:
        return await self._dispatch(
            messages,
            tier=tier,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
            agent=agent,
            review_id=review_id,
            use_cache=use_cache,
            schema_name=None,
        )

    async def structured(
        self,
        messages: list[dict],
        response_model: type[T],
        *,
        tier: str = "deep",
        temperature: float = 0.1,
        max_tokens: int = 4096,
        agent: str = "unknown",
        review_id: str | None = None,
        use_cache: bool = True,
    ) -> tuple[T, LLMResult]:
        """Call the model and coerce the reply into `response_model`.

        Uses Groq JSON mode plus an explicit schema in the system prompt, then
        one repair round-trip if validation fails. Repair is far cheaper than
        re-running a whole file review, which matters at 8K TPM.
        """
        # Strict json_schema uses constrained decoding, so a reasoning model
        # physically cannot emit output that breaks the schema. Models with an
        # open-ended dict field can't be expressed strictly and fall back to
        # plain JSON-object mode plus the in-prompt schema.
        strict = supports_strict_dicts(response_model)
        response_format: dict[str, Any] | None = None
        if strict:
            response_format = {
                "type": "json_schema",
                "json_schema": strict_json_schema(response_model),
            }

        payload = list(messages)
        payload.insert(
            0,
            {
                "role": "system",
                "content": (
                    "You return ONLY a single valid JSON object — no prose, no "
                    "markdown fences. It must validate against this JSON Schema:\n\n"
                    f"{_schema_hint(response_model)}\n\n"
                    "Every required field must be present. Use null for unknown "
                    "optional values, never invent placeholder text."
                ),
            },
        )

        async def _send(fmt: dict[str, Any] | None, label: str) -> LLMResult:
            return await self._dispatch(
                payload,
                tier=tier,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=True,
                agent=label,
                review_id=review_id,
                use_cache=use_cache,
                schema_name=response_model.__name__,
                response_format=fmt,
            )

        try:
            result = await _send(response_format, agent)
        except SchemaRejectedError as exc:
            # Constrained decoding is the best mode when it works, but these
            # models occasionally omit a field and Groq rejects the whole
            # response. Falling back to schema-guided-but-unenforced output
            # keeps the review alive; the tolerant Pydantic models and the
            # repair round-trip below take over as the validators.
            log.warning(
                "llm.strict_schema_fallback",
                agent=agent, model=response_model.__name__, reason=exc.detail,
            )
            loose = None
            if response_format is not None:
                loose = {
                    "type": "json_schema",
                    "json_schema": {**response_format["json_schema"], "strict": False},
                }
            response_format = loose
            result = await _send(loose, f"{agent}:loose")

        try:
            return response_model.model_validate_json(_extract_json(result.content)), result
        except (ValidationError, json.JSONDecodeError) as exc:
            # Bind before the except block ends -- Python unbinds `exc` on exit.
            validation_error = str(exc)[:1200]
            log.warning("llm.schema_miss", agent=agent, model=response_model.__name__)

        repair = [
            *payload,
            {"role": "assistant", "content": result.content[:4000]},
            {
                "role": "user",
                "content": (
                    "That output failed schema validation with:\n"
                    f"{validation_error}\n\n"
                    "Return the corrected JSON object only."
                ),
            },
        ]
        fixed = await self._dispatch(
            repair,
            tier=tier,
            temperature=0.0,
            max_tokens=max_tokens,
            json_mode=True,
            agent=f"{agent}:repair",
            review_id=review_id,
            use_cache=False,
            schema_name=response_model.__name__,
            response_format=response_format,
        )
        try:
            parsed = response_model.model_validate_json(_extract_json(fixed.content))
        except (ValidationError, json.JSONDecodeError) as exc2:
            raise LLMError(
                f"{agent} could not produce valid {response_model.__name__} JSON.",
                detail=str(exc2)[:800],
            ) from exc2

        fixed.total_tokens += result.total_tokens
        fixed.attempts = result.attempts + fixed.attempts
        return parsed, fixed

    async def close(self) -> None:
        await key_pool.aclose()


def describe_api_error(exc: Exception) -> tuple[str, str | None]:
    """Pull Groq's own complaint out of an SDK exception.

    Groq answers a rejected request with
    ``{"error": {"message": ..., "type": ..., "code": ...}}``. The SDK wraps
    that, and the useful half is easy to lose -- which turns a fixable
    "decommissioned model" into an opaque "400".

    Returns ``(human_message, machine_code)``.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or "").strip()
            code = err.get("code") or err.get("type")
            if message:
                return message, (str(code) if code else None)
        if isinstance(body.get("message"), str):
            return body["message"].strip(), None

    # Fall back to the raw response text, which still beats the SDK's summary.
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payload = response.json()
            err = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"]).strip(), str(err.get("code") or err.get("type") or "") or None
        except Exception:
            text = getattr(response, "text", "") or ""
            if text.strip():
                return text.strip()[:500], None

    return str(exc)[:500], None


def _retry_after(exc: Exception, *, default: float | None) -> float | None:
    """Seconds the API asked us to wait, or `default` when it said nothing."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        raw = resp.headers.get("retry-after") if getattr(resp, "headers", None) else None
        if raw:
            try:
                return min(60.0, float(raw))
            except ValueError:
                pass
    return default


# Leave room for estimation error and Groq's own accounting.
_TPM_SAFETY = 0.88
# Below this a reply is not worth making.
_MIN_COMPLETION = 400


def _fit_completion_budget(
    prompt_tokens: int, requested: int, tier: str, agent: str
) -> int:
    """Shrink the completion budget so prompt + completion fits one TPM window."""
    ceiling = int(limits_for(tier).tpm * _TPM_SAFETY)
    available = ceiling - prompt_tokens

    if available < _MIN_COMPLETION:
        raise LLMError(
            f"The prompt for {agent} is too large to fit the "
            f"{limits_for(tier).tpm:,} tokens/min limit of the '{tier}' tier "
            f"(prompt ≈ {prompt_tokens:,} tokens). Reduce 'Files to review' or "
            "MAX_FILE_KB, or move to a higher tier.",
            detail=f"prompt≈{prompt_tokens} ceiling={ceiling} agent={agent}",
        )

    if available < requested:
        log.info(
            "llm.completion_clamped",
            agent=agent, requested=requested, granted=available,
            prompt_est=prompt_tokens, tpm=limits_for(tier).tpm,
        )
        return available
    return requested


def _is_reasoning_model(model: str) -> bool:
    """Models that emit a reasoning trace and accept `reasoning_effort`."""
    low = model.lower()
    return "gpt-oss" in low or "qwen3" in low or "deepseek-r1" in low


def _is_schema_rejection(code: str | None, message: str) -> bool:
    low = f"{code or ''} {message}".lower()
    return (
        "json_validate_failed" in low
        or "does not match the expected schema" in low
        or "invalid json schema" in low
        or "failed to validate json" in low
    )


# 400s that condemn the credential rather than the request.
_KEY_FATAL_CODES = {
    "organization_restricted", "invalid_api_key", "account_deactivated",
    "authentication_error", "insufficient_quota", "billing_error",
}


def _is_key_fatal(code: str | None, message: str) -> bool:
    low = f"{code or ''} {message}".lower()
    if code and code in _KEY_FATAL_CODES:
        return True
    return any(
        phrase in low
        for phrase in (
            "organization has been restricted", "invalid api key",
            "account has been deactivated", "no longer active",
        )
    )


def _explain_rejection(
    status: int, code: str | None, message: str, model: str
) -> str:
    """Map Groq's complaint onto the thing the operator should actually change."""
    low = f"{code or ''} {message}".lower()

    if "organization has been restricted" in low or code == "organization_restricted":
        return (
            "Groq has restricted the account behind this API key. Create a new "
            "key from a working account, or contact Groq support."
        )
    if "json_validate_failed" in low or "failed to validate json" in low:
        return (
            f"The model '{model}' could not produce schema-valid JSON. This is "
            "typical of reasoning models in plain JSON-object mode; the client "
            "normally uses strict json_schema instead. If this persists, pick a "
            "different model for this tier."
        )
    if "decommission" in low or "does not exist" in low or "model_not_found" in low:
        return (
            f"Groq does not serve the model '{model}'. It has most likely been "
            "decommissioned. Set GROQ_MODEL_DEEP / GROQ_MODEL_FAST in .env to a "
            "current model — see https://console.groq.com/docs/models"
        )
    if "json" in low and ("response_format" in low or "json_object" in low):
        return (
            f"The model '{model}' rejected JSON response-format mode. Switch "
            "GROQ_MODEL_DEEP to a model that supports JSON mode."
        )
    if "must contain the word" in low:
        return (
            "Groq requires the word 'json' in the prompt when JSON mode is on. "
            "This is a bug in the prompt builder, not your configuration."
        )
    if "context" in low and ("length" in low or "window" in low):
        return (
            "The request exceeded the model's context window. Lower "
            "MAX_FILE_KB or 'Files to review'."
        )
    if "max_completion_tokens" in low or "max_tokens" in low:
        return f"Groq rejected the token-limit parameter for '{model}': {message}"
    if status == 413 or "too large" in low:
        return "The request payload was too large for Groq. Reduce the review scope."

    return f"Groq rejected the request ({status}): {message}"


llm = GroqLLM()
