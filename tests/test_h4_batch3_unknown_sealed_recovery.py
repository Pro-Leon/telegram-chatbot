"""H4 Batch 3: unknown delivery, sealed delivery, and recovery tests (fakes only).

Covers D1 (sealed timing), D2 (UNKNOWN classification), D3 (flood handling),
D4 (crash-safe DLQ move), D5 (reason-aware replay + repair), D6 (confirm
repair record), and vault-reaper UNKNOWN shielding.

No live Telegram, DropFans, llama.cpp, production Redis, or production
PostgreSQL. All marked unit. A retry after UNKNOWN is at-least-once on the
Telegram wire by Telethon library constraint — asserted as documented
behavior, never as exactly-once.
"""

import asyncio
import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

UNKNOWN_REASON = "unknown_send_result"
POST_SEND_REASON = "post_send_persistence_failed"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeRedis:
    """In-memory Redis fake with real stream-ID monotonicity (like Redis)."""

    def __init__(self):
        self.store = {}
        self.streams = {}
        self.acks = []
        self._seq = 0
        self.fail_xadd = False
        self.fail_xack = False

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    async def delete(self, *keys):
        n = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                n += 1
        return n

    async def scan_iter(self, match=None, count=None):
        import fnmatch

        for key in list(self.store):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    async def xadd(self, stream, fields, id="*"):
        if self.fail_xadd:
            raise RuntimeError("xadd down")
        entries = self.streams.setdefault(stream, [])
        if id == "*":
            self._seq += 1
            nid = f"{1700000000000 + self._seq}-0"
        else:
            if entries and id <= entries[-1][0]:
                from redis.exceptions import ResponseError

                raise ResponseError(
                    "The ID specified in XADD is equal or smaller than the target stream top item"
                )
            nid = id
        entries.append((nid, dict(fields)))
        return nid

    async def xrange(self, stream, min="-", max="+", count=None):
        entries = self.streams.get(stream, [])
        out = [(i, dict(f)) for i, f in entries if min <= i <= max]
        if count is not None:
            out = out[:count]
        return out

    async def xack(self, *args):
        if self.fail_xack:
            raise RuntimeError("xack down")
        self.acks.append(args)
        return 1

    async def xdel(self, stream, *ids):
        entries = self.streams.get(stream, [])
        before = len(entries)
        self.streams[stream] = [(i, f) for i, f in entries if i not in ids]
        return before - len(self.streams[stream])


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return None

        return _Ctx()


class _FakePGOutboundConn:
    """In-memory messages table enforcing UNIQUE (creator_id, dedup_id)
    WHERE dedup_id IS NOT NULL (first row wins, like ON CONFLICT DO NOTHING)."""

    def __init__(self):
        self.rows = []
        self.statements = []

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        if "dedup_id" in sql:
            creator, dedup = args[1], args[3]
            for row in self.rows:
                if row.get("creator_id") == creator and row.get("dedup_id") == dedup:
                    return "INSERT 0 0"
            self.rows.append({
                "user_id": args[0],
                "creator_id": creator,
                "generation_id": args[2],
                "dedup_id": dedup,
                "content": args[4],
                "telegram_message_id": args[10],
            })
            return "INSERT 0 1"
        self.rows.append({"legacy": True})
        return "INSERT 0 1"


def _patch_redis_fake(fake):
    import db.redis as redis_mod

    return patch.object(redis_mod, "get_redis", new=AsyncMock(return_value=fake))


def _text_data(**overrides):
    base = {
        "entity": "12345",
        "content": "hello",
        "draft_content": "hello",
        "was_edited": "",
        "was_auto_approved": "true",
        "confidence_score": "0.9",
        "operator_id": "",
        "save_to_db": "true",
        "media_type": "",
        "media_path": "",
        "dedup_id": "plain:1",
        "creator_id": "42",
        "generation_id": "gid-1",
    }
    base.update(overrides)
    return base


def _ok_client(tg_id=777):
    client = AsyncMock()
    client.get_input_entity = AsyncMock(return_value=MagicMock())
    client.send_message = AsyncMock(return_value=MagicMock(id=tg_id))
    return client


def _drive_stack(data, client, msg_id="m1", overrides=None, vault_overrides=None):
    import db.vault as vault_mod
    from chatbotv2 import main as main_mod

    mocks = {
        "get_send_dedup_value": AsyncMock(return_value=None),
        "try_reserve_send_dedup": AsyncMock(return_value="reserved:tok"),
        "confirm_send_dedup": AsyncMock(return_value=True),
        "mark_send_dedup": AsyncMock(),
        "release_send_dedup": AsyncMock(return_value=True),
        "ack_send": AsyncMock(),
        "save_outbound_after_send": AsyncMock(),
        "publish_event": AsyncMock(return_value="evt-1"),
        "move_send_to_dlq": AsyncMock(return_value=True),
        "enqueue_send": AsyncMock(return_value="x-1"),
        "get_send_rate_limit_wait": AsyncMock(return_value=0),
        "check_send_rate_limit": AsyncMock(return_value=True),
        "get_or_create_send_random_id": AsyncMock(return_value=b"0123456789abcdef"),
        "record_unknown_send_attempt": AsyncMock(return_value=True),
        "record_send_repair_needed": AsyncMock(return_value=True),
        "clear_unknown_send_attempt": AsyncMock(return_value=True),
        "clear_send_repair_needed": AsyncMock(return_value=True),
    }
    mocks.update(overrides or {})
    vault_mocks = {
        "finalize_delivery": AsyncMock(return_value=True),
        "release_delivery": AsyncMock(return_value=True),
        "finalize_dropfans_delivery": AsyncMock(return_value=True),
        "release_dropfans_delivery": AsyncMock(return_value=True),
        "reserve_delivery": AsyncMock(return_value=7),
    }
    vault_mocks.update(vault_overrides or {})
    published, dlq = [], []

    real_publish = mocks["publish_event"]
    real_dlq = mocks["move_send_to_dlq"]

    async def _pub(event_type, *args, **kwargs):
        published.append(event_type)
        if isinstance(real_publish, AsyncMock) and real_publish.side_effect is not None:
            return await real_publish.side_effect(event_type, *args, **kwargs)
        return "evt-1"

    async def _dlq(message_id, reason, **kwargs):
        dlq.append({"message_id": message_id, "reason": reason, **kwargs})
        if isinstance(real_dlq, AsyncMock) and real_dlq.side_effect is not None:
            return await real_dlq.side_effect(message_id, reason, **kwargs)
        return True

    use_pub = not (overrides and "publish_event" in overrides)
    use_dlq = not (overrides and "move_send_to_dlq" in overrides)
    stack = ExitStack()
    for name, mock_obj in mocks.items():
        if (name == "publish_event" and use_pub) or (name == "move_send_to_dlq" and use_dlq):
            continue
        stack.enter_context(patch.object(main_mod, name, mock_obj))
    if use_pub:
        stack.enter_context(patch.object(main_mod, "publish_event", AsyncMock(side_effect=_pub)))
    if use_dlq:
        stack.enter_context(patch.object(main_mod, "move_send_to_dlq", AsyncMock(side_effect=_dlq)))
    stack.enter_context(
        patch("core.entity_blacklist.is_blacklisted", new=AsyncMock(return_value=False))
    )
    for name, mock_obj in vault_mocks.items():
        stack.enter_context(patch.object(vault_mod, name, mock_obj))
    return stack, mocks, vault_mocks, published, dlq


# ---------------------------------------------------------------------------
# Classification unit tests
# ---------------------------------------------------------------------------

def test_classify_refused_vs_unknown():
    from telethon.errors import FloodWaitError, SlowModeWaitError, UserIsBlockedError

    from chatbotv2.main import _classify_transport_error

    assert _classify_transport_error(UserIsBlockedError("b")) == "refused"
    assert _classify_transport_error(ValueError("v")) == "refused"
    assert _classify_transport_error(TypeError("t")) == "refused"
    flood_exc = FloodWaitError(request=None)
    assert _classify_transport_error(flood_exc) == "unknown"
    assert _classify_transport_error(SlowModeWaitError(request=None)) == "unknown"
    assert _classify_transport_error(TimeoutError("t")) == "unknown"
    assert _classify_transport_error(ConnectionResetError("r")) == "unknown"
    assert _classify_transport_error(RuntimeError("?")) == "unknown"
    try:
        raise asyncio.CancelledError()
    except asyncio.CancelledError as e:
        assert _classify_transport_error(e) == "unknown"


# ---------------------------------------------------------------------------
# UNKNOWN send behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_timeout_never_certifies_failure():
    from chatbotv2 import main as main_mod

    client = _ok_client()
    client.send_message = AsyncMock(side_effect=TimeoutError("timed out"))
    stack, mocks, vault_mocks, published, dlq = _drive_stack(_text_data(), client)
    with stack:
        await main_mod._process_send_entry_inner(client, "m1", _text_data())
    assert len(dlq) == 1 and dlq[0]["reason"] == UNKNOWN_REASON
    assert "message.send_failed" not in published
    assert "message.sent" not in published
    assert dlq[0]["payload"]["dedup_id"] == "plain:1"
    assert dlq[0]["payload"]["generation_id"] == "gid-1"
    assert dlq[0]["payload"]["creator_id"] == "42"
    assert "unknown_error" in dlq[0]["payload"]
    mocks["save_outbound_after_send"].assert_not_called()
    # Lease kept (never released), marker never written, vault untouched.
    mocks["release_send_dedup"].assert_not_called()
    mocks["confirm_send_dedup"].assert_not_called()
    mocks["mark_send_dedup"].assert_not_called()
    vault_mocks["release_delivery"].assert_not_called()
    vault_mocks["release_dropfans_delivery"].assert_not_called()
    vault_mocks["finalize_delivery"].assert_not_called()
    # UNKNOWN attempt recorded with identity.
    mocks["record_unknown_send_attempt"].assert_called_once()
    assert mocks["record_unknown_send_attempt"].call_args[0][0] == "plain:1"
    assert mocks["record_unknown_send_attempt"].call_args[1]["creator_id"] == 42
    assert mocks["record_unknown_send_attempt"].call_args[1]["generation_id"] == "gid-1"


@pytest.mark.asyncio
async def test_unknown_connection_reset_keeps_lease_and_vault():
    from chatbotv2 import main as main_mod

    client = _ok_client()
    client.send_message = AsyncMock(side_effect=ConnectionResetError("reset"))
    stack, mocks, vault_mocks, published, dlq = _drive_stack(_text_data(), client)
    with stack:
        await main_mod._process_send_entry_inner(client, "m1", _text_data())
    assert len(dlq) == 1 and dlq[0]["reason"] == UNKNOWN_REASON
    assert "message.send_failed" not in published
    mocks["release_send_dedup"].assert_not_called()
    vault_mocks["release_delivery"].assert_not_called()


@pytest.mark.asyncio
async def test_unknown_send_stage_flood_is_not_send_error():
    from telethon.errors import FloodWaitError

    from chatbotv2 import main as main_mod

    flood_exc = FloodWaitError(request=None)
    flood_exc.seconds = 30
    client = _ok_client()
    client.send_message = AsyncMock(side_effect=flood_exc)
    stack, mocks, _, published, dlq = _drive_stack(_text_data(), client)
    with stack, patch(
        "core.entity_blacklist.blacklist_entity", new=AsyncMock()
    ) as mock_blacklist:
        await main_mod._process_send_entry_inner(client, "m1", _text_data())
    assert len(dlq) == 1 and dlq[0]["reason"] == UNKNOWN_REASON
    assert "message.send_failed" not in published
    mock_blacklist.assert_not_called()
    mocks["mark_send_dedup"].assert_not_called()


@pytest.mark.asyncio
async def test_unknown_cancellation_reraises_and_preserves():
    from chatbotv2 import main as main_mod

    client = _ok_client()
    client.send_message = AsyncMock(side_effect=asyncio.CancelledError())
    stack, mocks, _, published, _dlq = _drive_stack(_text_data(), client)
    with stack, pytest.raises(asyncio.CancelledError):
        await main_mod._process_send_entry_inner(client, "m1", _text_data())
    # Recorded before the exception escaped; nothing certified; entry un-ACKed.
    mocks["record_unknown_send_attempt"].assert_called_once()
    assert "message.send_failed" not in published
    mocks["ack_send"].assert_not_called()
    mocks["release_send_dedup"].assert_not_called()


@pytest.mark.asyncio
async def test_unknown_media_preserves_vault_reservation():
    import tempfile

    from chatbotv2 import main as main_mod

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"x")
        temp_path = f.name
    client = _ok_client()
    data = _text_data(media_type="photo", media_path=temp_path,
                      fangate_media_id="9", dedup_id="media:u1")
    with patch("chatbotv2.main.send_file", new=AsyncMock(side_effect=TimeoutError("t"))):
        stack, mocks, vault_mocks, published, dlq = _drive_stack(data, client)
        with stack:
            await main_mod._process_send_entry_inner(client, "m1", dict(data))
    assert len(dlq) == 1 and dlq[0]["reason"] == UNKNOWN_REASON
    assert "message.send_failed" not in published
    vault_mocks["release_delivery"].assert_not_called()
    vault_mocks["finalize_delivery"].assert_not_called()
    mocks["release_send_dedup"].assert_not_called()
    assert mocks["record_unknown_send_attempt"].call_args[1]["vault_reservation_ids"] == [7]


@pytest.mark.asyncio
async def test_refused_blocked_still_truthful():
    from telethon.errors import UserIsBlockedError

    from chatbotv2 import main as main_mod

    client = _ok_client()
    client.send_message = AsyncMock(side_effect=UserIsBlockedError("Blocked"))
    stack, mocks, _, published, dlq = _drive_stack(_text_data(), client)
    with stack:
        await main_mod._process_send_entry_inner(client, "m1", _text_data())
    assert "message.send_failed" in published
    assert dlq == []
    mocks["record_unknown_send_attempt"].assert_not_called()


# ---------------------------------------------------------------------------
# UNKNOWN + repair stores (real helpers against FakeRedis)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_store_roundtrip_and_scope():
    from db.redis import (
        clear_unknown_send_attempt,
        get_unknown_send_attempt,
        record_unknown_send_attempt,
    )

    fake = _FakeRedis()
    with _patch_redis_fake(fake):
        ok = await record_unknown_send_attempt(
            "d-1", creator_id=42, generation_id="g-9",
            error_type="TimeoutError", error_detail="timed out",
            vault_reservation_ids=[7],
        )
        assert ok is True
        rec = await get_unknown_send_attempt("d-1", creator_id=42)
    assert rec["dedup_id"] == "d-1"
    assert rec["creator_id"] == 42
    assert rec["generation_id"] == "g-9"
    assert rec["classification"] == "unknown"
    assert rec["vault_reservation_ids"] == [7]
    assert rec["attempt_timestamp"] > 0
    assert fake.store.get("send_unknown:42:d-1") is not None
    with _patch_redis_fake(fake):
        assert await get_unknown_send_attempt("d-1", creator_id=43) is None
        assert await clear_unknown_send_attempt("d-1", creator_id=42) is True
        assert await get_unknown_send_attempt("d-1", creator_id=42) is None


@pytest.mark.asyncio
async def test_unknown_store_missing_identity_no_global_key():
    from db.redis import get_unknown_send_attempt, record_unknown_send_attempt

    fake = _FakeRedis()
    with _patch_redis_fake(fake):
        assert await record_unknown_send_attempt(None, creator_id=42) is False
        assert await record_unknown_send_attempt("d", creator_id=None) is False
        assert await get_unknown_send_attempt("d", creator_id=None) is None
    assert fake.store == {}


@pytest.mark.asyncio
async def test_unknown_vault_shield_listing():
    from db.redis import list_unknown_vault_reservation_ids, record_unknown_send_attempt

    fake = _FakeRedis()
    fake.store["send_unknown:1:a"] = "not-json{{"
    with _patch_redis_fake(fake):
        await record_unknown_send_attempt("d-1", creator_id=1, vault_reservation_ids=[7, 9])
        await record_unknown_send_attempt("d-2", creator_id=2, vault_reservation_ids=["bad", 11])
        ids = await list_unknown_vault_reservation_ids(limit=200)
    assert sorted(ids) == [7, 9, 11]


@pytest.mark.asyncio
async def test_repair_store_roundtrip_and_clear():
    from db.redis import (
        clear_send_repair_needed,
        get_send_repair_needed,
        record_send_repair_needed,
    )

    fake = _FakeRedis()
    with _patch_redis_fake(fake):
        assert await record_send_repair_needed(
            "d-r", creator_id=5, generation_id="g", reason="confirm_failed",
            telegram_message_id=123,
        ) is True
        rec = await get_send_repair_needed("d-r", creator_id=5)
        assert rec["reason"] == "confirm_failed"
        assert rec["telegram_message_id"] == 123
        assert await clear_send_repair_needed("d-r", creator_id=5) is True
        assert await get_send_repair_needed("d-r", creator_id=5) is None


# ---------------------------------------------------------------------------
# Flood handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolution_floodwait_sleep_capped_then_requeues():
    from telethon.errors import FloodWaitError

    from chatbotv2 import main as main_mod
    from core.config import get_settings

    cap = max(1, int(get_settings().redis_pending_idle_ms // 1000))
    order = []

    async def _xadd(payload, **kwargs):
        order.append("xadd")
        return "x-2"

    async def _ack(message_id):
        order.append("ack")

    slept = []

    async def _sleep(delay):
        slept.append(delay)

    flood_exc = FloodWaitError(request=None)
    flood_exc.seconds = 3600
    client = _ok_client()
    client.get_input_entity = AsyncMock(side_effect=flood_exc)
    stack, _mocks, _, _, dlq = _drive_stack(
        _text_data(), client,
        overrides={
            "enqueue_send": AsyncMock(side_effect=_xadd),
            "ack_send": AsyncMock(side_effect=_ack),
        },
    )
    with stack, patch("asyncio.sleep", new=_sleep):
        await main_mod._process_send_entry_inner(client, "m1", _text_data())
    assert order == ["xadd", "ack"]
    assert slept == [float(cap)]
    assert dlq == []
    client.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_resolution_slowmode_defers_without_blacklist_or_mark():
    from telethon.errors import SlowModeWaitError

    from chatbotv2 import main as main_mod

    slow_exc = SlowModeWaitError(request=None)
    slow_exc.seconds = 5
    client = _ok_client()
    client.get_input_entity = AsyncMock(side_effect=slow_exc)
    stack, mocks, _, _, dlq = _drive_stack(_text_data(), client)
    with stack, patch(
        "core.entity_blacklist.blacklist_entity", new=AsyncMock()
    ) as mock_blacklist, patch("asyncio.sleep", new=AsyncMock()):
        await main_mod._process_send_entry_inner(client, "m1", _text_data())
    mocks["enqueue_send"].assert_called_once()
    mocks["ack_send"].assert_called_once_with("m1")
    mock_blacklist.assert_not_called()
    mocks["mark_send_dedup"].assert_not_called()
    assert dlq == []
    client.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_resolution_peerflood_defers_without_blacklist():
    from telethon.errors import PeerFloodError

    from chatbotv2 import main as main_mod

    client = _ok_client()
    client.get_input_entity = AsyncMock(side_effect=PeerFloodError(request=None))
    stack, mocks, _, _, dlq = _drive_stack(_text_data(), client)
    with stack, patch(
        "core.entity_blacklist.blacklist_entity", new=AsyncMock()
    ) as mock_blacklist, patch("asyncio.sleep", new=AsyncMock()):
        await main_mod._process_send_entry_inner(client, "m1", _text_data())
    mocks["enqueue_send"].assert_called_once()
    mocks["ack_send"].assert_called_once_with("m1")
    mock_blacklist.assert_not_called()
    assert dlq == []


# ---------------------------------------------------------------------------
# Sealed delivery (D1)
# ---------------------------------------------------------------------------

def _sealed_offer(offer_id=42):
    return {
        "id": offer_id,
        "creator_id": 1,
        "user_id": 10,
        "link": "https://www.dropfans.io/buy/dpfn_X",
        "price_minor": 3000,
        "currency": "USD",
        "vault_item_ids": ["vid-1"],
        "media_count": 2,
        "dropfans_product_id": "dpfn_X",
    }


class _SealResult:
    def __init__(self, status="SEALED", offer=None):
        self.status = status
        self.offer = offer


@pytest.mark.asyncio
async def test_sealed_execute_releases_instead_of_confirming():
    from commerce.opportunity_execution import execute_sealed_offer

    calls = {"reserve": [], "enqueue": [], "confirm": [], "release": [], "get": []}

    async def fake_reserve(dedup_id, creator_id=None, **kw):
        calls["reserve"].append((dedup_id, creator_id))
        return "reserved:tok-seal"

    async def fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None, **kw):
        calls["enqueue"].append((dedup_id, creator_id))
        return "msg-1"

    async def fake_confirm(dedup_id, creator_id=None, token=None, **kw):
        calls["confirm"].append((dedup_id, creator_id, token))
        return True

    async def fake_release(dedup_id, creator_id=None, token=None, **kw):
        calls["release"].append((dedup_id, creator_id, token))
        return True

    with (
        patch("db.redis.try_reserve_send_dedup", new=fake_reserve),
        patch("db.redis.enqueue_send", new=fake_enqueue),
        patch("db.redis.confirm_send_dedup", new=fake_confirm),
        patch("db.redis.release_send_dedup", new=fake_release),
    ):
        res = await execute_sealed_offer(
            _SealResult(status="SEALED", offer=_sealed_offer(42)),
            creator_id=1, user_id=10, generation_id="g-seal",
        )
    assert res.status == "EXECUTED"
    assert res.dedup_id == "sealed:42"
    assert calls["confirm"] == []
    assert calls["release"] == [("sealed:42", 1, "reserved:tok-seal")]
    assert calls["enqueue"] == [("sealed:42", 1)]


@pytest.mark.asyncio
async def test_sealed_worker_invokes_telegram_then_confirms():
    from chatbotv2 import main as main_mod

    data = _text_data(entity="10", dedup_id="sealed:42", generation_id="g-seal")
    client = _ok_client(tg_id=4242)
    stack, mocks, _, published, dlq = _drive_stack(data, client)
    with stack:
        await main_mod._process_send_entry_inner(client, "m-seal", dict(data))
    client.send_message.assert_called_once()
    assert mocks["confirm_send_dedup"].call_count >= 1
    assert mocks["confirm_send_dedup"].call_args[0][0] == "sealed:42"
    assert "message.sent" in published
    assert dlq == []


@pytest.mark.asyncio
async def test_sealed_crash_before_send_remains_recoverable():
    from chatbotv2 import main as main_mod

    data = _text_data(entity="10", dedup_id="sealed:42", generation_id="g-seal")
    client = _ok_client()
    stack, mocks, _, published, dlq = _drive_stack(
        data, client,
        overrides={"get_send_dedup_value": AsyncMock(return_value="reserved:tok-seal")},
    )
    with stack:
        await main_mod._process_send_entry_inner(client, "m-seal", dict(data))
    # In-flight reservation defers: no send, no ACK (pending → reclaimable).
    client.send_message.assert_not_called()
    mocks["ack_send"].assert_not_called()
    assert dlq == []
    assert published == []


@pytest.mark.asyncio
async def test_sealed_replay_undelivered_does_not_suppress():
    from chatbotv2 import main as main_mod

    data = _text_data(entity="10", dedup_id="sealed:42", generation_id="g-seal")
    client = _ok_client(tg_id=111)
    stack, _mocks, _, published, _ = _drive_stack(data, client)
    with stack:
        await main_mod._process_send_entry_inner(client, "replay-1", dict(data))
    client.send_message.assert_called_once()
    assert "message.sent" in published


@pytest.mark.asyncio
async def test_sealed_replay_delivered_does_not_duplicate():
    from chatbotv2 import main as main_mod

    data = _text_data(entity="10", dedup_id="sealed:42", generation_id="g-seal")
    client = _ok_client()
    stack, mocks, _, _, dlq = _drive_stack(
        data, client,
        overrides={"get_send_dedup_value": AsyncMock(return_value="1")},
    )
    with stack:
        await main_mod._process_send_entry_inner(client, "replay-2", dict(data))
    client.send_message.assert_not_called()
    mocks["ack_send"].assert_called_once_with("replay-2")
    assert dlq == []


@pytest.mark.asyncio
async def test_sealed_already_reserved_intact():
    from commerce.opportunity_execution import execute_sealed_offer, is_sealed_ppv_handled

    async def fake_reserve(dedup_id, creator_id=None, **kw):
        return None

    async def fake_get(dedup_id, creator_id=None, **kw):
        return "reserved:other-token"

    async def _explode(*a, **kw):
        raise AssertionError("must not enqueue on duplicate")

    with (
        patch("db.redis.try_reserve_send_dedup", new=fake_reserve),
        patch("db.redis.get_send_dedup_value", new=fake_get),
        patch("db.redis.enqueue_send", new=_explode),
        patch("db.redis.confirm_send_dedup", new=_explode),
        patch("db.redis.release_send_dedup", new=_explode),
    ):
        res = await execute_sealed_offer(
            _SealResult(status="SEALED", offer=_sealed_offer(42)),
            creator_id=1, user_id=10,
        )
    assert res.status == "ALREADY_ENQUEUED"
    assert is_sealed_ppv_handled(res) is False


# ---------------------------------------------------------------------------
# D4: crash-safe DLQ move (real move_send_to_dlq against FakeRedis)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dlq_xadd_failure_leaves_pending_no_ack():
    from db.redis import move_send_to_dlq

    fake = _FakeRedis()
    fake.fail_xadd = True
    with _patch_redis_fake(fake):
        ok = await move_send_to_dlq("m1", "send_error", payload={"a": "b"}, worker_id="w")
    assert ok is False
    assert fake.acks == []
    assert fake.streams.get("dead_letter_queue", []) == []


@pytest.mark.asyncio
async def test_dlq_xack_failure_reports_record_without_certifying_gone():
    from db.redis import move_send_to_dlq

    fake = _FakeRedis()
    fake.fail_xack = True
    with _patch_redis_fake(fake):
        ok = await move_send_to_dlq("m1", "send_error", payload={"a": "b"}, worker_id="w")
    assert ok is False
    assert len(fake.streams.get("dead_letter_queue", [])) == 1
    assert fake.acks == []


@pytest.mark.asyncio
async def test_dlq_move_success_true():
    from db.redis import move_send_to_dlq

    fake = _FakeRedis()
    with _patch_redis_fake(fake):
        ok = await move_send_to_dlq("m1", "send_error", payload={"a": "b"}, worker_id="w")
    assert ok is True
    assert len(fake.acks) == 1


@pytest.mark.asyncio
async def test_dlq_move_never_raises():
    from db.redis import move_send_to_dlq

    with patch("db.redis.get_redis", new=AsyncMock(side_effect=RuntimeError("redis down"))):
        assert await move_send_to_dlq("m1", "send_error") is False


@pytest.mark.asyncio
async def test_presend_dlq_failure_defers_send_failed():
    from chatbotv2 import main as main_mod

    client = _ok_client()
    client.send_message = AsyncMock(side_effect=ValueError("bad entity"))
    stack, mocks, _, published, _dlq = _drive_stack(
        _text_data(), client,
        overrides={"move_send_to_dlq": AsyncMock(return_value=False)},
    )
    with stack:
        await main_mod._process_send_entry_inner(client, "m1", _text_data())
    # Entry stays pending for reclaim; failure must not be certified yet.
    assert "message.send_failed" not in published
    mocks["save_outbound_after_send"].assert_not_called()


# ---------------------------------------------------------------------------
# D5: reason-aware replay (real replay_dlq_entry against FakeRedis)
# ---------------------------------------------------------------------------

async def _seed_send_dlq(fake, reason, payload, replay_count="0"):
    import db.redis as redis_mod

    with _patch_redis_fake(fake):
        eid = await fake.xadd(
            redis_mod.DLQ_STREAM,
            {
                "message_id": "orig-1",
                "reason": reason,
                "stream": "send",
                "failure_timestamp": "1700000000",
                "replay_count": replay_count,
                "payload": json.dumps(payload),
            },
        )
    return eid


def _send_payload(**over):
    base = {
        "entity": "111",
        "content": "hi",
        "draft_content": "hi",
        "was_edited": "",
        "was_auto_approved": "true",
        "confidence_score": "0.9",
        "operator_id": "",
        "save_to_db": "true",
        "dedup_id": "replay-d",
        "creator_id": "42",
        "generation_id": "gid-r",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_replay_resend_eligible_preserves_identity():
    import db.redis as redis_mod

    fake = _FakeRedis()
    eid = await _seed_send_dlq(fake, "send_error", _send_payload())
    with _patch_redis_fake(fake):
        res = await redis_mod.replay_dlq_entry(eid)
    assert res["success"] is True
    assert res["new_message_id"] is not None
    assert res["replay_count"] == 1
    sent = fake.streams.get(redis_mod.SEND_STREAM, [])
    assert len(sent) == 1
    fields = sent[0][1]
    assert fields["dedup_id"] == "replay-d"
    assert fields["generation_id"] == "gid-r"
    assert fields["creator_id"] == "42"


@pytest.mark.asyncio
async def test_replay_post_send_is_repair_only():
    import db.postgres as pg_mod
    import db.redis as redis_mod
    import db.vault as vault_mod

    pg_conn = _FakePGOutboundConn()
    finalize_calls, mark_calls = [], []

    async def _fin(rid, telegram_message_id=None):
        finalize_calls.append((rid, telegram_message_id))
        return True

    async def _mark(dedup_id, ttl=86400, creator_id=None):
        mark_calls.append((dedup_id, creator_id))

    payload = _send_payload()
    payload["post_send_telegram_message_id"] = 555
    payload["post_send_delivery_reservation_id"] = 7
    fake = _FakeRedis()
    eid = await _seed_send_dlq(fake, POST_SEND_REASON, payload)
    with (
        _patch_redis_fake(fake),
        patch.object(pg_mod, "get_pool", new=AsyncMock(return_value=_FakePool(pg_conn))),
        patch.object(vault_mod, "finalize_delivery", new=AsyncMock(side_effect=_fin)),
        patch.object(redis_mod, "mark_send_dedup", new=AsyncMock(side_effect=_mark)),
    ):
        res = await redis_mod.replay_dlq_entry(eid)
    assert res["success"] is True
    assert res["new_message_id"] is None
    assert res["repaired"] is not None
    # No Telegram send: nothing re-enqueued to the send stream.
    assert fake.streams.get(redis_mod.SEND_STREAM, []) == []
    # Repair evidence applied: one idempotent row, marker, finalize.
    assert len(pg_conn.rows) == 1
    assert pg_conn.rows[0]["telegram_message_id"] == 555
    assert pg_conn.rows[0]["dedup_id"] == "replay-d"
    assert mark_calls == [("replay-d", 42)]
    assert finalize_calls == [(7, 555)]


@pytest.mark.asyncio
async def test_replay_post_send_never_creates_second_row():
    import db.postgres as pg_mod
    import db.redis as redis_mod

    pg_conn = _FakePGOutboundConn()
    fake = _FakeRedis()
    eid = await _seed_send_dlq(
        fake, POST_SEND_REASON,
        _send_payload(post_send_telegram_message_id=555),
    )
    with (
        _patch_redis_fake(fake),
        patch.object(pg_mod, "get_pool", new=AsyncMock(return_value=_FakePool(pg_conn))),
    ):
        first = await redis_mod.replay_dlq_entry(eid)
        # Second repair targets the rotated record id.
        rotated = next(i for i, _ in fake.streams[redis_mod.DLQ_STREAM] if i != eid)
        second = await redis_mod.replay_dlq_entry(rotated, max_replay_attempts=5)
    assert first["success"] is True and second["success"] is True
    assert len(pg_conn.rows) == 1


@pytest.mark.asyncio
async def test_replay_unknown_refused_by_default():
    import db.redis as redis_mod

    fake = _FakeRedis()
    eid = await _seed_send_dlq(fake, UNKNOWN_REASON, _send_payload())
    with _patch_redis_fake(fake):
        res = await redis_mod.replay_dlq_entry(eid)
    assert res["success"] is False
    assert res["error"] == "unknown_result_requires_operator_decision"
    assert res["new_message_id"] is None
    assert res["replay_count"] == 0
    assert fake.streams.get(redis_mod.SEND_STREAM, []) == []
    # Refusal does not consume a replay attempt.
    entry = (await fake.xrange(redis_mod.DLQ_STREAM, min=eid, max=eid))[0][1]
    assert entry["replay_count"] == "0"


@pytest.mark.asyncio
async def test_replay_unknown_force_resend_is_explicit():
    import db.redis as redis_mod

    fake = _FakeRedis()
    eid = await _seed_send_dlq(fake, UNKNOWN_REASON, _send_payload())
    with _patch_redis_fake(fake):
        res = await redis_mod.replay_dlq_entry(eid, force_resend=True)
    assert res["success"] is True
    assert res["new_message_id"] is not None
    assert fake.streams.get(redis_mod.SEND_STREAM, []) != []


@pytest.mark.asyncio
async def test_replay_count_bounded_and_atomic():
    import db.redis as redis_mod

    fake = _FakeRedis()
    eid = await _seed_send_dlq(fake, "send_error", _send_payload(), replay_count="2")
    with _patch_redis_fake(fake):
        ok = await redis_mod.replay_dlq_entry(eid, max_replay_attempts=3)
        assert ok["success"] is True and ok["replay_count"] == 3
        # Non-tail rotation keeps the bound enforceable: replay the rotated id.
        rotated = [i for i, _ in fake.streams[redis_mod.DLQ_STREAM]][-1]
        refused = await redis_mod.replay_dlq_entry(rotated, max_replay_attempts=3)
        assert refused["success"] is False
        assert refused["error"] == "max_replay_attempts_reached"


@pytest.mark.asyncio
async def test_replay_send_missing_creator_returns_error_not_raise():
    import db.redis as redis_mod

    fake = _FakeRedis()
    payload = _send_payload()
    del payload["creator_id"]
    eid = await _seed_send_dlq(fake, "send_error", payload)
    with _patch_redis_fake(fake):
        res = await redis_mod.replay_dlq_entry(eid)
    assert res["success"] is False
    assert "creator_id" in res["error"]


# ---------------------------------------------------------------------------
# D6: confirm-failure repair record
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirm_failure_records_repair_without_dlq_or_resend():
    from chatbotv2 import main as main_mod

    stack, mocks, vault_mocks, published, dlq = _drive_stack(
        _text_data(),
        _ok_client(),
        overrides={"confirm_send_dedup": AsyncMock(side_effect=RuntimeError("redis down"))},
    )
    with stack:
        await main_mod._process_send_entry_inner(_ok_client(), "m1", _text_data())
    assert "message.sent" in published
    assert "message.send_failed" not in published
    assert dlq == []
    mocks["record_send_repair_needed"].assert_called_once()
    assert mocks["record_send_repair_needed"].call_args[0][0] == "plain:1"
    assert mocks["record_send_repair_needed"].call_args[1]["reason"] == "confirm_failed"
    assert mocks["record_send_repair_needed"].call_args[1]["telegram_message_id"] == 777
    vault_mocks["release_delivery"].assert_not_called()
    mocks["save_outbound_after_send"].assert_called_once()


@pytest.mark.asyncio
async def test_repair_never_clobbers_inflight_lease():
    import db.redis as redis_mod

    fake = _FakeRedis()
    fake.store["send_dedup:42:d-r"] = "reserved:other-owner"
    mark_calls = []
    pg_conn = _FakePGOutboundConn()
    import db.postgres as pg_mod

    with (
        _patch_redis_fake(fake),
        patch.object(redis_mod, "mark_send_dedup", new=AsyncMock(side_effect=lambda *a, **k: mark_calls.append(a) or None)),
        patch.object(pg_mod, "get_pool", new=AsyncMock(return_value=_FakePool(pg_conn))),
    ):
        res = await redis_mod.repair_post_send_delivery(
            dedup_id="d-r", creator_id=42, user_id=111, content="hi",
            telegram_message_id=999,
        )
    assert mark_calls == []
    assert res["steps"]["marker"] == "deferred_inflight"
    assert res["steps"]["saved"] is True
    assert fake.store["send_dedup:42:d-r"] == "reserved:other-owner"


@pytest.mark.asyncio
async def test_success_clears_unknown_but_preserves_fresh_repair():
    from chatbotv2 import main as main_mod

    stack, mocks, _, published, _ = _drive_stack(
        _text_data(),
        _ok_client(),
        overrides={"confirm_send_dedup": AsyncMock(side_effect=RuntimeError("down"))},
    )
    with stack:
        await main_mod._process_send_entry_inner(_ok_client(), "m1", _text_data())
    assert "message.sent" in published
    mocks["clear_unknown_send_attempt"].assert_called_once_with("plain:1", creator_id=42)
    # Repair was recorded in this same invocation and the marker is still
    # unconfirmed — the success path must not wipe it.
    mocks["clear_send_repair_needed"].assert_not_called()


@pytest.mark.asyncio
async def test_success_clears_stale_repair_when_marker_confirmed():
    from chatbotv2 import main as main_mod

    stack, mocks, _, published, _ = _drive_stack(_text_data(), _ok_client())
    with stack:
        await main_mod._process_send_entry_inner(_ok_client(), "m1", _text_data())
    assert "message.sent" in published
    mocks["clear_unknown_send_attempt"].assert_called_once_with("plain:1", creator_id=42)
    mocks["clear_send_repair_needed"].assert_called_once_with("plain:1", creator_id=42)


# ---------------------------------------------------------------------------
# Vault reaper shielding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reaper_skip_ids_reach_sql():
    statements = []

    class _Conn:
        async def fetch(self, sql, *args):
            statements.append((sql, args))
            return []

    import db.vault as vault_mod

    with patch.object(vault_mod, "get_pool", new=AsyncMock(return_value=_FakePool(_Conn()))):
        released = await vault_mod.release_stale_reservations(5, skip_ids=[7, 9])
    assert released == []
    sql, args = statements[0]
    assert "NOT (id = ANY" in sql
    assert args[0] == 5 and args[2] == [7, 9]


@pytest.mark.asyncio
async def test_stream_loop_shields_unknown_reservations():
    from chatbotv2 import main as main_mod

    release_calls = {}

    async def _fake_release(max_age_minutes=5, batch_size=50, skip_ids=()):
        release_calls["skip_ids"] = list(skip_ids)
        return []

    data = {"entity": "1", "content": "x"}  # no creator_id → fast fail-closed DLQ
    with (
        patch.object(main_mod, "is_shutting_down", side_effect=[False, True]),
        patch.object(main_mod, "requeue_stalled_send_messages", new=AsyncMock(return_value=(0, []))),
        patch.object(
            main_mod, "read_send_messages",
            new=AsyncMock(return_value=[("send_messages", [("m1", data)])]),
        ),
        patch.object(main_mod, "move_send_to_dlq", new=AsyncMock(return_value=True)),
        patch.object(
            main_mod, "list_unknown_vault_reservation_ids",
            new=AsyncMock(return_value=[7, 9]),
        ),
        patch.object(main_mod, "release_stale_reservations", new=_fake_release),
    ):
        await main_mod._process_send_stream(AsyncMock())
    assert release_calls["skip_ids"] == [7, 9]
