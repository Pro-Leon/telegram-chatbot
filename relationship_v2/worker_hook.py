"""Worker observation hook (Stage D3). Metadata out, control never.

Mirrors the established `context_engine.worker_integration` observe pattern:
flag-gated, fail-open, telemetry-only. The V2 context is built and measured
but never feeds generation, routing, sending, or state while shadow mode
is the posture. Any failure returns None; production flow continues.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("sunny.v2.worker_hook")


class V2TurnObservation(BaseModel):
    enabled: bool = True
    total_chars: int = Field(ge=0)
    section_count: int = Field(ge=0)
    recall_count: int = Field(ge=0)
    commerce_ok: bool = False
    elapsed_ms: float = Field(ge=0.0)
    generation_id: str = Field(min_length=1)

    model_config = {"frozen": True}


AssemblePort = Callable[..., Awaitable[Any]]


async def _default_assemble(**kwargs: Any) -> Any:
    from relationship_v2.services.turn_context import assemble_turn_context

    return await assemble_turn_context(**kwargs)


async def observe_turn_context(
    creator_id: int,
    user_id: int,
    generation_id: str,
    *,
    now: datetime | None = None,
    assemble: AssemblePort | None = None,
) -> V2TurnObservation | None:
    """Build-and-measure V2 context when the read flag is on. Never raises."""
    try:
        from core.architecture_router import is_v2_read_enabled

        if not is_v2_read_enabled():
            return None
        if creator_id <= 0 or user_id <= 0 or not generation_id:
            return None
        start = time.perf_counter()
        ctx = await (assemble or _default_assemble)(
            creator_id,
            user_id,
            generation_id,
            now=now or datetime.now(UTC),
        )
        elapsed = (time.perf_counter() - start) * 1000.0
        return V2TurnObservation(
            total_chars=int(ctx.assembled.total_chars),
            section_count=len(ctx.assembled.sections),
            recall_count=len(ctx.recall_refs),
            commerce_ok=not ctx.commerce_unavailable,
            elapsed_ms=elapsed,
            generation_id=generation_id,
        )
    except Exception:
        logger.debug("v2 turn observation skipped (fail-open)", exc_info=True)
        return None
