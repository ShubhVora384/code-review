from __future__ import annotations

import time
from typing import TypeVar

from pydantic import BaseModel

from app.agents.context import ReviewContext
from app.core.logging import get_logger
from app.llm.client import llm

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class BaseAgent:
    """Common plumbing: typed LLM calls wrapped in trace events.

    Not abstract -- subclasses define their own ``run()`` signature because
    each turn consumes a different slice of the pipeline state.
    """

    name: str = "agent"
    tier: str = "deep"
    temperature: float = 0.1
    max_tokens: int = 4096

    async def ask(
        self,
        ctx: ReviewContext,
        system: str,
        user: str,
        response_model: type[T],
        *,
        tier: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        use_cache: bool = True,
        detail: dict | None = None,
    ) -> T:
        """One structured call, bracketed by thinking/done trace events."""
        ctx.check_cancelled()
        await ctx.emit(
            "agent_start",
            agent=self.name,
            message=f"{self.name} is working…",
            payload=detail or {},
            persist=False,
        )

        started = time.perf_counter()
        parsed, result = await llm.structured(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_model,
            tier=tier or self.tier,
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=max_tokens or self.max_tokens,
            agent=self.name,
            review_id=ctx.review_id,
            use_cache=use_cache,
        )

        await ctx.emit(
            "agent_done",
            agent=self.name,
            message=f"{self.name} finished",
            payload={
                "tokens": result.total_tokens,
                "model": result.model,
                "cache_hit": result.cache_hit,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            },
            persist=False,
        )
        return parsed
