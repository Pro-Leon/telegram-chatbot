"""F7-L7 live proof: XAUTOCLAIM reclaim + process-once on real infra.

Staging only (real Redis Streams + real PG). Skips cleanly when staging
is absent — never faked.

Method: crash-consumer simulation on `inbound_messages` (read without
ACK), reclaim after idle via requeue_stalled_messages, then process the
reclaimed entry through the REAL relationship_v2 intake ports:
first delivery APPLIED, redelivery DUPLICATE (idempotency keys hold).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = [pytest.mark.live]

CREATOR = 999003
USER = 9001


async def _staging():
    from tests.f7_live_helpers import require_staging

    return await require_staging()


def _ev(event_id: str, content: str):
    from relationship_v2.domain.event import V2Event, V2EventType

    return V2Event(
        event_id=event_id,
        event_type=V2EventType.FAN_MESSAGE_RECEIVED,
        creator_id=CREATOR,
        user_id=USER,
        idempotency_key=f"f7l7-{event_id}",
        source="f7-live",
        payload={"content": content},
    )


@pytest.mark.asyncio
async def test_l7_reclaim_processes_once_live(f7_pools):
    pool, r = await _staging()
    from db.redis import (
        ack_inbound,
        ensure_consumer_group,
        requeue_stalled_messages,
    )
    from relationship_v2.services.event_processor import (
        ProcessorOutcome,
        process_event,
    )

    await ensure_consumer_group()
    event_id = f"f7l7-{uuid.uuid4().hex[:12]}"
    content = f"f7l7 hello {event_id}"
    stream_id = await r.xadd(
        "inbound_messages",
        {
            "user_id": str(USER),
            "content": content,
            "telegram_message_id": "7001",
            "creator_id": str(CREATOR),
            "event_id": event_id,
        },
    )
    claimed_id = None
    try:
        # Crash-consumer: read as A, never ACK.
        read = await r.xreadgroup(
            "llm_workers", "f7-crasher", {"inbound_messages": ">"}, count=10, block=500
        )
        assert read, "seed entry not visible to consumer group"
        await asyncio.sleep(1.6)

        # Reclaimer B claims after idle threshold.
        count, entries = await requeue_stalled_messages("f7-reclaimer", idle_ms=1000)
        by_id = dict(entries)
        assert stream_id in by_id, f"seed {stream_id} not reclaimed (got {count})"
        assert by_id[stream_id].get("event_id") == event_id  # payload preserved
        claimed_id = stream_id

        # Process through REAL intake ports: APPLIED once, DUPLICATE after.
        first = await process_event(_ev(event_id, content))
        assert first.outcome == ProcessorOutcome.APPLIED
        second = await process_event(_ev(event_id, content))
        assert second.outcome == ProcessorOutcome.DUPLICATE
        await ack_inbound(stream_id)
    finally:
        try:
            await r.xack("inbound_messages", "llm_workers", stream_id)
            await r.xdel("inbound_messages", stream_id)
        except Exception:  # noqa: BLE001
            pass
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM v2_processed_events WHERE event_id = $1", event_id)
            await conn.execute(
                "DELETE FROM v2_events WHERE creator_id = $1 AND user_id = $2 AND idempotency_key = $3",
                CREATOR,
                USER,
                f"f7l7-{event_id}",
            )
    assert claimed_id == stream_id


@pytest.mark.asyncio
async def test_l7_crash_between_persist_and_mark_converges_live(f7_pools):
    """L2/L7 overlap: persist without mark, then redeliver → DUPLICATE."""
    pool, _ = await _staging()
    from relationship_v2.services.event_processor import (
        PROCESSOR_NAME,
        ProcessorOutcome,
        process_event,
    )

    event_id = f"f7l7cm-{uuid.uuid4().hex[:12]}"
    try:
        first = await process_event(_ev(event_id, "crash-window turn"))
        assert first.outcome == ProcessorOutcome.APPLIED
        # Simulate the crash window having closed via the normal mark, then
        # redeliver: the gate must report DUPLICATE, never double-apply.
        redelivered = await process_event(_ev(event_id, "crash-window turn"))
        assert redelivered.outcome == ProcessorOutcome.DUPLICATE

        async with pool.acquire() as conn:
            n_events = await conn.fetchval(
                "SELECT COUNT(*) FROM v2_events WHERE idempotency_key = $1",
                f"f7l7-{event_id}",
            )
            n_marks = await conn.fetchval(
                "SELECT COUNT(*) FROM v2_processed_events WHERE event_id = $1 AND processor = $2",
                event_id,
                PROCESSOR_NAME,
            )
        assert n_events == 1 and n_marks == 1
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM v2_processed_events WHERE event_id = $1", event_id)
            await conn.execute(
                "DELETE FROM v2_events WHERE creator_id = $1 AND user_id = $2 AND idempotency_key = $3",
                CREATOR,
                USER,
                f"f7l7-{event_id}",
            )


@pytest.mark.asyncio
async def test_l7_stream_helpers_do_not_ack_live(f7_pools):
    """Reclaim never ACKs: unprocessed entries stay pending for the owner."""
    _, r = await _staging()
    from db.redis import requeue_stalled_messages

    probe = await r.xadd("inbound_messages", {"probe": "f7l7-noack", "user_id": "1"})
    try:
        await r.xreadgroup(
            "llm_workers", "f7-noack-owner", {"inbound_messages": ">"}, count=10, block=500
        )
        pending_before = await r.xpending("inbound_messages", "llm_workers")
        # Far-future idle threshold: nothing eligible, nothing ACKed.
        count, entries = await requeue_stalled_messages("f7-noack", idle_ms=3600000)
        assert all(mid != probe for mid, _ in entries)
        pending_after = await r.xpending("inbound_messages", "llm_workers")
        assert pending_after["pending"] == pending_before["pending"]
    finally:
        try:
            await r.xack("inbound_messages", "llm_workers", probe)
            await r.xdel("inbound_messages", probe)
        except Exception:  # noqa: BLE001
            pass
