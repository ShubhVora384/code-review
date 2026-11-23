"""Per-review execution context: event emission, budget tracking, persistence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.event_bus import bus
from app.core.logging import get_logger
from app.db.base import new_id
from app.db.models import LLMCallLog, Review, ReviewEvent
from app.db.session import session_scope

log = get_logger(__name__)

# Ordered pipeline phases -> fraction of the run complete when each finishes.
PHASE_PROGRESS: dict[str, float] = {
    "queued": 0.0,
    "ingesting": 0.06,
    "profiling": 0.16,
    "structure": 0.28,
    "planning": 0.36,
    "reviewing_files": 0.78,
    "cross_cutting": 0.86,
    "critique": 0.88,
    "verifying": 0.94,
    "synthesizing": 0.98,
    "completed": 1.0,
    "failed": 1.0,
}

PHASE_LABEL: dict[str, str] = {
    "ingesting": "Fetching and indexing the codebase",
    "profiling": "Working out what this project is",
    "structure": "Analysing the folder structure",
    "planning": "Planning the review order",
    "reviewing_files": "Reviewing files",
    "cross_cutting": "Looking for systemic issues",
    "critique": "Challenging the findings",
    "verifying": "Proving the findings by running them",
    "synthesizing": "Writing the final report",
}


@dataclass
class TokenLedger:
    prompt: int = 0
    completion: int = 0
    total: int = 0
    calls: int = 0
    cache_hits: int = 0
    key_rotations: int = 0
    by_agent: dict[str, int] = field(default_factory=dict)
    by_key: dict[str, int] = field(default_factory=dict)

    def add(
        self,
        agent: str,
        prompt: int,
        completion: int,
        total: int,
        cached: bool,
        key_id: str | None = None,
        rotations: int = 0,
    ) -> None:
        self.calls += 1
        self.key_rotations += rotations
        if cached:
            self.cache_hits += 1
            return
        self.prompt += prompt
        self.completion += completion
        self.total += total
        self.by_agent[agent] = self.by_agent.get(agent, 0) + total
        if key_id:
            self.by_key[key_id] = self.by_key.get(key_id, 0) + total

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt,
            "completion_tokens": self.completion,
            "total_tokens": self.total,
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "key_rotations": self.key_rotations,
            "by_agent": dict(sorted(self.by_agent.items(), key=lambda kv: -kv[1])),
            "by_key": dict(sorted(self.by_key.items(), key=lambda kv: -kv[1])),
        }


class ReviewContext:
    """Everything an agent needs that is not the codebase itself."""

    def __init__(self, review_id: str, options: dict[str, Any]) -> None:
        self.review_id = review_id
        self.options = options
        self.ledger = TokenLedger()
        self.cancelled = asyncio.Event()
        self._seq = 0
        self._seq_lock = asyncio.Lock()

    # ------------------------------------------------------------- events

    async def emit(
        self,
        event_type: str,
        *,
        agent: str | None = None,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> None:
        """Record one step of the agent trace and push it to SSE subscribers."""
        async with self._seq_lock:
            self._seq += 1
            seq = self._seq

        now = datetime.now(UTC)
        event = {
            "seq": seq,
            "type": event_type,
            "agent": agent,
            "message": message,
            "payload": payload or {},
            "created_at": now.isoformat(),
            "review_id": self.review_id,
        }

        if persist:
            try:
                async with session_scope() as db:
                    db.add(
                        ReviewEvent(
                            id=new_id(),
                            review_id=self.review_id,
                            seq=seq,
                            type=event_type,
                            agent=agent,
                            message=message,
                            payload=payload or {},
                            created_at=now,
                        )
                    )
            except Exception:
                log.warning("event.persist_failed", type=event_type, exc_info=True)

        await bus.publish(self.review_id, event)

    async def set_phase(self, phase: str, message: str | None = None) -> None:
        # Phase boundaries are the cheapest safe place to honour a cancel
        # request that arrived from another process.
        if await self.is_cancelled_in_db():
            self.cancelled.set()
        self.check_cancelled()

        progress = PHASE_PROGRESS.get(phase, 0.0)
        async with session_scope() as db:
            review = await db.get(Review, self.review_id)
            if review:
                review.phase = phase
                review.progress = progress
                if phase not in ("completed", "failed"):
                    review.status = "running"
        await self.emit(
            "phase",
            message=message or PHASE_LABEL.get(phase, phase),
            payload={"phase": phase, "progress": progress},
        )

    async def progress(self, value: float, message: str, payload: dict | None = None) -> None:
        """Fine-grained progress inside a long phase (per-file reviews)."""
        async with session_scope() as db:
            review = await db.get(Review, self.review_id)
            if review:
                review.progress = max(review.progress, value)
        await self.emit(
            "progress",
            message=message,
            payload={"progress": value, **(payload or {})},
            persist=False,
        )

    # -------------------------------------------------------- llm usage

    async def record_usage(
        self,
        agent: str,
        review_id: str | None,
        result: Any,
        ok: bool,
        error: str | None,
    ) -> None:
        """Sink wired into GroqLLM so every call lands in the ledger + DB."""
        if result is not None:
            self.ledger.add(
                agent,
                result.prompt_tokens,
                result.completion_tokens,
                result.total_tokens,
                result.cache_hit,
                key_id=getattr(result, "key_id", None),
                rotations=getattr(result, "key_rotations", 0),
            )
        try:
            async with session_scope() as db:
                db.add(
                    LLMCallLog(
                        id=new_id(),
                        review_id=self.review_id,
                        agent=agent,
                        model=getattr(result, "model", "unknown") if result else "unknown",
                        prompt_tokens=getattr(result, "prompt_tokens", 0) if result else 0,
                        completion_tokens=getattr(result, "completion_tokens", 0) if result else 0,
                        total_tokens=getattr(result, "total_tokens", 0) if result else 0,
                        latency_ms=getattr(result, "latency_ms", 0) if result else 0,
                        attempts=getattr(result, "attempts", 1) if result else 1,
                        cache_hit=getattr(result, "cache_hit", False) if result else False,
                        ok=ok,
                        error=error,
                        created_at=datetime.now(UTC),
                    )
                )
        except Exception:
            log.debug("llm_call_log.persist_failed", exc_info=True)

    async def record_rotation(
        self, from_key: str, to_key: str | None, cooldown: float, agent: str
    ) -> None:
        """Sink wired into GroqLLM so key failover is visible in the trace."""
        await self.emit(
            "key_rotated",
            agent=agent,
            message=(
                f"Rate limit on {from_key} — "
                + (f"switched to {to_key}" if to_key else "waiting for a key to cool down")
            ),
            payload={
                "from_key": from_key,
                "to_key": to_key,
                "cooldown_s": round(cooldown, 1),
            },
        )

    # ------------------------------------------------------------ control

    def check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise asyncio.CancelledError("Review cancelled by user")

    async def is_cancelled_in_db(self) -> bool:
        async with session_scope() as db:
            row = await db.execute(
                select(Review.status).where(Review.id == self.review_id)
            )
            return row.scalar_one_or_none() == "cancelled"
