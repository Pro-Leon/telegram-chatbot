"""F7-L3 live proof: duplicate-send prevention on real infra.

Staging only. Skips cleanly when staging is absent — never faked.

Two layers:
1. Vault reservation gate (transport-independent): double reserve of the
   same media converges to one reservation id; the duplicate gets None
   (no second send). Proven live here.
2. Full transport path (double-deliver one send-stream entry → one
   Telegram send): requires chatbotv2.main importable. Guarded — skips
   with the blocking reason when the transport is unavailable.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.live]

CREATOR = 999003
USER = 9001


async def _staging_pg():
    from tests.f7_live_helpers import require_staging

    pool, _ = await require_staging()
    return pool


@pytest.mark.asyncio
async def test_l3_reservation_gate_single_send_live(f7_pools):
    """Two reserves, one id: the duplicate is suppressed pre-transport."""
    pool = await _staging_pg()
    from db import vault as vdb

    media_id = 700000 + (uuid.uuid4().int % 8999)
    first = await vdb.reserve_delivery(CREATOR, USER, media_id, 888001)
    assert isinstance(first, int)
    try:
        duplicate = await vdb.reserve_delivery(CREATOR, USER, media_id, 888001)
        assert duplicate is None
        assert await vdb.has_user_received_media(CREATOR, USER, media_id) is False
        assert (
            await vdb.finalize_delivery(first, telegram_message_id=4242, creator_id=CREATOR) is True
        )
        assert await vdb.has_user_received_media(CREATOR, USER, media_id) is True
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM vault_media_deliveries WHERE creator_id = $1 AND user_id = $2",
                CREATOR,
                USER,
            )


@pytest.mark.asyncio
async def test_l3_transport_double_deliver_single_send_live(f7_pools):
    """Double-deliver one send entry on live Redis → one Telegram send."""
    from unittest.mock import AsyncMock, MagicMock

    from tests.f7_live_helpers import require_staging

    pool, r = await require_staging()
    from chatbotv2.main import _handle_send_entry

    tag = uuid.uuid4().hex[:12]
    dedup = f"f7l3-{tag}"
    data = {
        "entity": str(USER),
        "content": f"f7l3 hello {tag}",
        "draft_content": f"f7l3 hello {tag}",
        "was_edited": "",
        "was_auto_approved": "",
        "confidence_score": "0",
        "operator_id": "",
        "save_to_db": "",
        "media_type": "",
        "media_path": "",
        "creator_id": str(CREATOR),
        "dedup_id": dedup,
        "generation_id": f"gen-{tag}",
    }
    dlq_before = await r.xlen("dead_letter_queue")
    client = AsyncMock()
    sent = MagicMock()
    sent.id = 424242
    client.send_message = AsyncMock(return_value=sent)
    client.get_input_entity = AsyncMock(return_value=object())
    stream_id = await r.xadd("send_messages", {k: str(v) for k, v in data.items()})
    try:
        await _handle_send_entry(client, stream_id, dict(data))
        await _handle_send_entry(client, stream_id, dict(data))
        assert client.send_message.call_count == 1
        value = await r.get(f"send_dedup:{CREATOR}:{dedup}")
        assert value == "1"
        assert await r.xlen("dead_letter_queue") == dlq_before
    finally:
        await r.delete(f"send_dedup:{CREATOR}:{dedup}")
        await r.delete(f"send_random_id:{CREATOR}:{dedup}")
        try:
            await r.xack("send_messages", "send_workers", stream_id)
            await r.xdel("send_messages", stream_id)
        except Exception:  # noqa: BLE001
            pass
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM messages WHERE creator_id = $1 AND dedup_id = $2",
                CREATOR,
                dedup,
            )
