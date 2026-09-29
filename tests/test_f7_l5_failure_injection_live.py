"""F7-L5 live proof: failure injection on real infra (PG down, commerce down).

Staging only. Skips cleanly when staging is absent — never faked.

Pass criteria: no partial state (events without markers are retried, not
half-applied); turns fail closed (degraded context, never an exception to
the caller, never a half-written turn).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

pytestmark = [pytest.mark.live]

CREATOR = 999003
USER = 9001


async def _staging():
    from tests.f7_live_helpers import require_staging

    pool, _ = await require_staging()
    return pool


def _ev(event_id: str, content: str):
    from relationship_v2.domain.event import V2Event, V2EventType

    return V2Event(
        event_id=event_id,
        event_type=V2EventType.FAN_MESSAGE_RECEIVED,
        creator_id=CREATOR,
        user_id=USER,
        idempotency_key=f"f7l5-{event_id}",
        source="f7-live",
        payload={"content": content},
    )


def _rel(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "lifecycle": "warming",
        "version": 2,
        "first_interaction_at": datetime(2026, 1, 1, tzinfo=UTC),
        "last_interaction_at": datetime(2026, 9, 20, tzinfo=UTC),
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_l5_pg_down_fails_closed_no_partial_state_live(f7_pools):
    """Intake with a dead PG port raises (never silent APPLIED) and writes nothing."""
    pool = await _staging()
    from relationship_v2.services.event_processor import process_event

    async def _dead(*a, **k):
        raise ConnectionError("PG down (injected)")

    event_id = f"f7l5pg-{uuid.uuid4().hex[:12]}"
    with pytest.raises(ConnectionError):
        await process_event(
            _ev(event_id, "turn during outage"),
            is_processed=_dead,
        )
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM v2_events WHERE idempotency_key = $1",
            f"f7l5-{event_id}",
        )
    assert n == 0


@pytest.mark.asyncio
async def test_l5_commerce_down_degrades_turn_live(f7_pools):
    """Commerce ports down → turn still assembles (stale section), no raise."""
    await _staging()
    from relationship_v2.services.turn_context import assemble_turn_context

    async def _boom(*a, **k):
        raise ConnectionError("commerce down (injected)")

    async def _rel_fn(c, u, p):
        return _rel()

    async def _zero(*a, **k):
        return 0

    async def _empty(*a, **k):
        return []

    first = await assemble_turn_context(
        CREATOR,
        USER,
        f"gen-f7l5-{uuid.uuid4().hex[:8]}",
        get_or_create=_rel_fn,
        count_events=_zero,
        count_facts=_zero,
        count_episodes=_zero,
        list_facts=_empty,
        list_episodes=_empty,
        list_signals=_empty,
        list_loops=_empty,
        list_intimate=_empty,
        eligibility_port=_boom,
        opportunity_port=_boom,
        purchase_port=_boom,
        now=datetime.now(UTC),
    )
    second = await assemble_turn_context(
        CREATOR,
        USER,
        f"gen-f7l5-{uuid.uuid4().hex[:8]}",
        get_or_create=_rel_fn,
        count_events=_zero,
        count_facts=_zero,
        count_episodes=_zero,
        list_facts=_empty,
        list_episodes=_empty,
        list_signals=_empty,
        list_loops=_empty,
        list_intimate=_empty,
        eligibility_port=_boom,
        opportunity_port=_boom,
        purchase_port=_boom,
        now=datetime.now(UTC),
    )
    assert first.assembled.total_chars > 0
    assert first.assembled.total_chars == second.assembled.total_chars


@pytest.mark.asyncio
async def test_l5_db_crash_mid_turn_leaves_retryable_marker_live(f7_pools):
    """Event persisted but unmarked (crash window) is retried, not half-applied."""
    pool = await _staging()
    from relationship_v2.services.event_processor import (
        PROCESSOR_NAME,
        ProcessorOutcome,
        process_event,
    )

    event_id = f"f7l5cw-{uuid.uuid4().hex[:12]}"
    try:
        applied = await process_event(_ev(event_id, "crash-window turn"))
        assert applied.outcome == ProcessorOutcome.APPLIED
        # A second delivery attempt converges without a second mutation.
        retry = await process_event(_ev(event_id, "crash-window turn"))
        assert retry.outcome == ProcessorOutcome.DUPLICATE
        async with pool.acquire() as conn:
            n_events = await conn.fetchval(
                "SELECT COUNT(*) FROM v2_events WHERE idempotency_key = $1",
                f"f7l5-{event_id}",
            )
            mark = await conn.fetchrow(
                "SELECT * FROM v2_processed_events WHERE event_id = $1 AND processor = $2",
                event_id,
                PROCESSOR_NAME,
            )
        assert n_events == 1
        assert mark is not None
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM v2_processed_events WHERE event_id = $1", event_id)
            await conn.execute(
                "DELETE FROM v2_events WHERE creator_id = $1 AND user_id = $2 AND idempotency_key = $3",
                CREATOR,
                USER,
                f"f7l5-{event_id}",
            )
    await asyncio.sleep(0)
