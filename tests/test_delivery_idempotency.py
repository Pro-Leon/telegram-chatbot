"""Phase 3.1 delivery idempotency tests (mocked redis/client, no DB/wire)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeRedis:
    def __init__(self, fail_on_set=False):
        self.store = {}
        self.xadds = []
        self.xacks = []
        self.fail_on_set = fail_on_set
        self.claims = []
        self.pending_map = {}

    async def set(self, key, value, nx=False, ex=None):
        if self.fail_on_set:
            raise RuntimeError("redis down")
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    async def delete(self, key):
        return self.store.pop(key, None) is not None

    async def eval(self, *args):
        return 1

    async def xadd(self, stream, data, id="*"):
        self.xadds.append((stream, dict(data)))
        return "9-0"

    async def xack(self, *args):
        self.xacks.append(args)
        return 1

    async def xautoclaim(self, **kwargs):
        return [None, list(self.claims)]

    async def xpending_range(self, *args):
        # (name, group, lo, hi, count) — map ids to configured counts.
        ids = [mid for mid, _ in self.claims]
        out = []
        for mid in ids:
            out.append(
                {
                    "message_id": mid,
                    "consumer": "c",
                    "time_since_delivered": 1,
                    "times_delivered": self.pending_map.get(mid, 1),
                }
            )
        return out


def _inbound(content, tg):
    return {
        "user_id": "7",
        "content": content,
        "telegram_message_id": str(tg),
        "username": "u",
        "first_name": "A",
        "persona": "p",
        "generation_id": f"gid-{tg}",
        "creator_id": "42",
    }


@pytest.mark.asyncio
async def test_duplicate_tgid_suppressed():
    from db import redis as _r

    fake = _FakeRedis()
    with patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)):
        first = await _r.enqueue_inbound(_inbound("hello", 11))
        second = await _r.enqueue_inbound(_inbound("hello", 11))
    assert not str(first).startswith("duplicate:")
    assert str(second).startswith("duplicate:")
    assert len(fake.xadds) == 1


@pytest.mark.asyncio
async def test_duplicate_content_new_tgid_suppressed():
    from db import redis as _r

    fake = _FakeRedis()
    with patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)):
        await _r.enqueue_inbound(_inbound("same words", 21))
        dup = await _r.enqueue_inbound(_inbound("same words", 22))
    assert str(dup).startswith("duplicate:")
    assert len(fake.xadds) == 1


@pytest.mark.asyncio
async def test_content_window_expiry_reallows():
    from db import redis as _r

    fake = _FakeRedis()
    with patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)):
        await _r.enqueue_inbound(_inbound("repeat later", 31))
        for key in [k for k in fake.store if ":content:" in k]:
            del fake.store[key]
        ok = await _r.enqueue_inbound(_inbound("repeat later", 32))
    assert not str(ok).startswith("duplicate:")
    assert len(fake.xadds) == 2


@pytest.mark.asyncio
async def test_reclaim_fifth_delivery_dlq_ack():
    from db import redis as _r

    fake = _FakeRedis()
    fake.claims = [("5-0", {"content": "poison"}), ("6-0", {"content": "fresh"})]
    fake.pending_map = {"5-0": 6, "6-0": 1}
    with patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)):
        n, entries = await _r.requeue_stalled_send_messages("c")
    assert n == 1
    assert [mid for mid, _ in entries] == ["6-0"]
    assert any(d.get("reason") == "max_redeliveries_exceeded" for _, d in fake.xadds)
    assert fake.xacks, "poison entry must be ACKed after DLQ"


@pytest.mark.asyncio
async def test_reclaim_below_limit_returned():
    from db import redis as _r

    fake = _FakeRedis()
    fake.claims = [("7-0", {"content": "ok"})]
    fake.pending_map = {"7-0": 2}
    with patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)):
        n, entries = await _r.requeue_stalled_send_messages("c")
    assert n == 1 and entries[0][0] == "7-0"
    assert not [d for _, d in fake.xadds if d.get("reason") == "max_redeliveries_exceeded"]


def _send_data(**over):
    d = {
        "entity": "123",
        "content": "hello",
        "draft_content": "hello",
        "was_edited": "false",
        "was_auto_approved": "true",
        "confidence_score": "0.9",
        "operator_id": "",
        "save_to_db": "true",
        "creator_id": "42",
        "dedup_id": "d1",
        "generation_id": "g1",
    }
    d.update(over)
    return d


def _send_harness(monkeypatch_send=None, save_side_effect=None, dedup_value=None):
    import chatbotv2.main as _m

    client = MagicMock()
    client.get_input_entity = AsyncMock(return_value=object())
    sent_msg = MagicMock()
    sent_msg.id = 999
    client.send_message = AsyncMock(return_value=sent_msg)
    calls = {"ack": [], "repair": [], "save": []}

    async def fake_save(**kwargs):
        calls["save"].append(kwargs)
        if save_side_effect is not None:
            raise save_side_effect

    async def fake_repair(dedup_id, **kwargs):
        calls["repair"].append((dedup_id, kwargs))
        return True

    async def fake_ack(*args):
        calls["ack"].append(args)

    patches = [
        patch.object(_m, "get_send_dedup_value", new=AsyncMock(return_value=dedup_value)),
        patch.object(_m, "get_send_rate_limit_wait", new=AsyncMock(return_value=0)),
        patch.object(_m, "check_send_rate_limit", new=AsyncMock(return_value=True)),
        patch.object(_m, "try_reserve_send_dedup", new=AsyncMock(return_value="reserved:tok")),
        patch.object(_m, "get_or_create_send_random_id", new=AsyncMock(return_value="rid")),
        patch.object(_m, "confirm_send_dedup", new=AsyncMock(return_value=True)),
        patch.object(_m, "mark_send_dedup", new=AsyncMock(return_value=None)),
        patch.object(_m, "save_outbound_after_send", side_effect=fake_save),
        patch.object(_m, "record_send_repair_needed", side_effect=fake_repair),
        patch.object(_m, "ack_send", side_effect=fake_ack),
        patch.object(_m, "publish_event", new=AsyncMock(return_value="e")),
    ]
    for p in patches:
        p.start()

    async def stop():
        for p in patches:
            p.stop()

    return client, calls, stop


@pytest.mark.asyncio
async def test_crash_accept_to_save_acked_repair():
    # H4 Batch 1 delivery truth supersedes the Phase 3.1 save-before-ack
    # order: ACK precedes persistence; a save failure routes to the
    # post-send repair path (confirm/finalize/DLQ), never send_failed.
    import chatbotv2.main as _m

    client, calls, stop = _send_harness(save_side_effect=RuntimeError("db down"))
    with patch("core.entity_blacklist.is_blacklisted", new=AsyncMock(return_value=False)):
        with patch.object(_m, "move_send_to_dlq", new=AsyncMock(return_value=True)) as mock_dlq:
            await _m._process_send_entry_inner(client, "8-0", _send_data())
    await stop()
    assert len(calls["save"]) == 1
    assert len(calls["ack"]) == 1
    mock_dlq.assert_called_once()
    assert mock_dlq.call_args[0][1] == "post_send_persistence_failed"


@pytest.mark.asyncio
async def test_reclaim_same_dedup_no_double_wire():
    import chatbotv2.main as _m

    client, calls, stop = _send_harness(dedup_value="1")
    with patch("core.entity_blacklist.is_blacklisted", new=AsyncMock(return_value=False)):
        await _m._process_send_entry_inner(client, "8-0", _send_data())
    await stop()
    client.send_message.assert_not_called()
    assert len(calls["ack"]) == 1
    assert calls["save"] == []


@pytest.mark.asyncio
async def test_save_failure_never_send_failed():
    # H4 Batch 1: save failure after proven acceptance is post-send —
    # DLQ with the stable reason, never send_failed, never a resend.
    import chatbotv2.main as _m

    client, calls, stop = _send_harness(save_side_effect=RuntimeError("db down"))
    with patch("core.entity_blacklist.is_blacklisted", new=AsyncMock(return_value=False)):
        with patch.object(_m, "move_send_to_dlq", new=AsyncMock(return_value=True)) as mock_dlq:
            with patch.object(_m, "publish_event", new=AsyncMock()) as mock_pub:
                await _m._process_send_entry_inner(client, "8-1", _send_data())
    await stop()
    mock_dlq.assert_called_once()
    assert mock_dlq.call_args[0][1] == "post_send_persistence_failed"
    for call in mock_pub.call_args_list:
        assert call[0][0] != "message.send_failed"
