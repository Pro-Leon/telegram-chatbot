"""Phase 3.3 single-outbound per (creator,user,telegram_message_id) + wire-id fix.

A: two drafts, same inbound tg_id -> one XADD + one queue row (second suppressed).
B: retry same dedup, wire 100 -> 101 -> single row records 101, content first-wins.
C: different tg_id -> two sends allowed.
D: redelivery same tg_id -> suppressed (idempotent).
E: sealed + normal same turn -> sealed wins, normal suppressed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeRedis:
    def __init__(self, fail_on_set=False):
        self.store = {}
        self.xadds = []
        self.fail_on_set = fail_on_set

    async def set(self, key, value, nx=False, ex=None):
        if self.fail_on_set:
            raise RuntimeError("redis down")
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def delete(self, key):
        return self.store.pop(key, None) is not None

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    async def xadd(self, stream, data, id="*"):
        self.xadds.append((stream, dict(data)))
        return "9-0"

    async def eval(self, *args):
        return 1


class _FakeConn:
    """Simulates messages table: first-wins content, latest wire id."""

    def __init__(self, store):
        self.store = store
        self.queries = []

    async def execute(self, query, *args):
        self.queries.append(query)
        # Only model the dedup upsert branch (14 params).
        if len(args) == 14 and "ON CONFLICT" in query:
            (
                _user_id,
                creator_id,
                _generation_id,
                dedup,
                content,
                _draft,
                _edited,
                _auto,
                _score,
                _op,
                tg_id,
                _media_type,
                _media_path,
                _fangate,
            ) = args
            key = (creator_id, dedup)
            is_update = "DO UPDATE SET" in query
            assert "DO NOTHING" not in query, "3.3 must not use DO NOTHING"
            assert "telegram_message_id = EXCLUDED.telegram_message_id" in query
            # UPDATE must not touch first-wins columns.
            if is_update:
                update_clause = query.split("DO UPDATE SET", 1)[1]
                for col in ("content =", "draft_content =", "was_auto_approved ="):
                    assert col not in update_clause, f"first-wins column touched: {col}"
            if key not in self.store:
                self.store[key] = {"content": content, "telegram_message_id": tg_id}
            elif (
                is_update and tg_id is not None and self.store[key]["telegram_message_id"] != tg_id
            ):
                self.store[key]["telegram_message_id"] = tg_id
            return "INSERT 0 1"
        return "INSERT 0 1"


class _FakePoolConn:
    def __init__(self, store):
        self._conn = _FakeConn(store)
        self.store = store

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, store):
        self.store = store
        self.last_conn = None

    def acquire(self):
        self.last_conn = _FakePoolConn(self.store)
        return self.last_conn


def _seal_result(offer):
    sr = MagicMock()
    sr.status = "SEALED"
    sr.offer = offer
    return sr


def _offer(**over):
    base = {
        "id": 42,
        "creator_id": 1,
        "user_id": 10,
        "link": "https://www.dropfans.io/buy/dpfn_A",
        "price_minor": 1999,
        "currency": "USD",
        "vault_item_ids": ["V1"],
        "media_count": 1,
        "dropfans_product_id": "dpfn_A",
        "reason": '{"allow_download": true}',
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_turn_claim_first_wins_second_suppressed():
    from db import redis as _r

    fake = _FakeRedis()
    with patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)):
        assert await _r.try_claim_turn_send(1, 10, 100) is True
        assert await _r.try_claim_turn_send(1, 10, 100) is False
        # Different turn is independent.
        assert await _r.try_claim_turn_send(1, 10, 101) is True
        # Different creator/user independent.
        assert await _r.try_claim_turn_send(2, 10, 100) is True
        assert _r._turn_send_key(1, 10, "100") == "turn_send:1:10:100"


@pytest.mark.asyncio
async def test_turn_claim_fail_open():
    from db import redis as _r

    # Missing identity proceeds (never lost-send).
    assert await _r.try_claim_turn_send(None, 10, 100) is True
    assert await _r.try_claim_turn_send(1, 10, 0) is True
    assert await _r.try_claim_turn_send(1, 10, None) is True
    # Redis outage proceeds.
    fake = _FakeRedis(fail_on_set=True)
    with patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)):
        assert await _r.try_claim_turn_send(1, 10, 100) is True


@pytest.mark.asyncio
async def test_two_drafts_same_tg_one_xadd_one_queue_row():
    """A: greeting + leak drafts for one inbound tg_id -> single send."""
    from db import redis as _r

    fake = _FakeRedis()
    queue_rows = []
    with patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)):
        # Draft 1 (greeting) claims and enqueues.
        assert await _r.try_claim_turn_send(1, 10, 555) is True
        await fake.xadd("send_messages", {"content": "hello!", "dedup_id": "md5-a"})
        queue_rows.append({"tg_id": 555, "draft": "hello!"})
        # Draft 2 (leak variant, same turn) loses the claim -> suppressed.
        assert await _r.try_claim_turn_send(1, 10, 555) is False
    assert len([s for s, _ in fake.xadds if s == "send_messages"]) == 1
    assert len(queue_rows) == 1


@pytest.mark.asyncio
async def test_retry_wire_id_updates_single_row():
    """B: same dedup, wire 100 -> 101 -> row records 101, content first-wins."""
    from db import postgres as _pg

    store = {}
    pool = _FakePool(store)
    with patch.object(_pg, "get_pool", new=AsyncMock(return_value=pool)):
        await _pg.save_outbound_after_send(
            user_id=10,
            content="hello",
            draft_content="hello",
            was_edited=False,
            was_auto_approved=True,
            confidence_score=0.9,
            operator_id=None,
            telegram_message_id=100,
            creator_id=1,
            generation_id="g1",
            dedup_id="md5-a",
        )
        await _pg.save_outbound_after_send(
            user_id=10,
            content="hello-CHANGED",
            draft_content="hello-CHANGED",
            was_edited=False,
            was_auto_approved=True,
            confidence_score=0.9,
            operator_id=None,
            telegram_message_id=101,
            creator_id=1,
            generation_id="g1",
            dedup_id="md5-a",
        )
    assert len(store) == 1
    row = store[(1, "md5-a")]
    assert row["telegram_message_id"] == 101
    assert row["content"] == "hello"


@pytest.mark.asyncio
async def test_different_tg_two_sends_redelivery_suppressed():
    """C: different tg -> two sends. D: redelivery same tg -> suppressed."""
    from db import redis as _r

    fake = _FakeRedis()
    with patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)):
        assert await _r.try_claim_turn_send(1, 10, 200) is True
        await fake.xadd("send_messages", {"content": "turn-1"})
        assert await _r.try_claim_turn_send(1, 10, 201) is True
        await fake.xadd("send_messages", {"content": "turn-2"})
        # Redelivery of turn 1: same key -> suppress.
        assert await _r.try_claim_turn_send(1, 10, 200) is False
    assert len([s for s, _ in fake.xadds if s == "send_messages"]) == 2


@pytest.mark.asyncio
async def test_sealed_wins_normal_suppressed_same_turn():
    """E: sealed claims the turn; normal auto-send for same tg is suppressed."""
    from commerce import opportunity_execution as _exe
    from db import redis as _r

    fake = _FakeRedis()
    enqueues = []

    async def fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None):
        enqueues.append(dedup_id)
        return "9-0"

    async def fake_reserve(dedup_id, creator_id=None):
        return "reserved:tok"

    with (
        patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)),
        patch.object(_exe._redis_mod, "enqueue_send", new=fake_enqueue),
        patch.object(_exe._redis_mod, "try_reserve_send_dedup", new=fake_reserve),
        patch.object(_exe, "_try_dynamic_sealed_content", new=AsyncMock(return_value="")),
    ):
        first = await _exe.execute_sealed_offer(
            _seal_result(_offer()),
            creator_id=1,
            user_id=10,
            generation_id="g1",
            telegram_message_id=777,
        )
        assert first.status == "EXECUTED"
        # Normal path for the same turn now loses the turn claim.
        assert await _r.try_claim_turn_send(1, 10, 777) is False
        # Repeat sealed call for the same turn is also suppressed.
        second = await _exe.execute_sealed_offer(
            _seal_result(_offer()),
            creator_id=1,
            user_id=10,
            generation_id="g1",
            telegram_message_id=777,
        )
        assert second.status == "ALREADY_ENQUEUED"
    assert enqueues == ["sealed:42"]
