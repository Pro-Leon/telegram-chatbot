"""M2 — Atomic Debounce / Latest-Only Processing tests.

Hermetic, deterministic, no live Redis/Postgres/Telegram/LLM.
Proves the concurrency properties directly with an in-memory FakeRedis
that emulates the M2 Lua scripts atomically (as real Redis does).

Conventions under test (db/redis.py):
  * owner election: SET lock NX EX window  -> fence token minted by winner
  * timer consume:  Lua(check owner==fence, LRANGE, DEL list/owner/lock)
  * recovery claim: Lua(check lock absent, LRANGE, DEL list/owner)
  * ordering: RPUSH order; latest = consumed[-1]; generation_id verbatim.

For collapse tests, distinct telegram_message_ids are always used so a
passing test can never rely on downstream inbound_dedup masking.
"""

import asyncio
import inspect
import json
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# FakeRedis with atomic Lua emulation
# ---------------------------------------------------------------------------
class FakeRedis:
    def __init__(self):
        self.store = {}
        self.lists = {}
        self.streams = {"inbound_messages": []}
        self.xadd_id = 0

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def exists(self, key):
        if key in self.store:
            return 1
        if key in self.lists and self.lists[key]:
            return 1
        return 0

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    async def delete(self, *keys):
        cnt = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                cnt += 1
            if k in self.lists:
                del self.lists[k]
                cnt += 1
        return cnt

    async def rpush(self, key, val):
        self.lists.setdefault(key, []).append(val)
        return len(self.lists[key])

    async def lrange(self, key, start, end):
        lst = self.lists.get(key, [])
        if end == -1:
            return lst[start:]
        return lst[start : end + 1]

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def expire(self, key, ttl):
        return True

    async def xadd(self, stream, data, id="*"):
        self.xadd_id += 1
        mid = f"{self.xadd_id}-0"
        self.streams.setdefault(stream, []).append((mid, dict(data)))
        return mid

    async def scan_iter(self, match=None, count=None):
        for k in list(self.lists):
            if k.endswith(":messages"):
                yield k

    async def eval(self, script, numkeys, *args):
        keys = [args[0]] if numkeys >= 1 else []
        keys += list(args[1:numkeys])
        argv = list(args[numkeys:])
        # M2 timer consume (checked before generic GET/DEL matching).
        if "'STALE'" in script:
            owner, lst, lock = keys[0], keys[1], keys[2]
            if self.store.get(owner) != (argv[0] if argv else None):
                return ["STALE", ""]
            msgs = list(self.lists.get(lst, []))
            self.lists.pop(lst, None)
            self.store.pop(owner, None)
            self.store.pop(lock, None)
            if not msgs:
                return ["EMPTY", ""]
            return ["OK", json.dumps(msgs)]
        # M2 recovery claim.
        if "'BUSY'" in script:
            lock, owner, lst = keys[0], keys[1], keys[2]
            if lock in self.store:
                return ["BUSY", ""]
            msgs = list(self.lists.get(lst, []))
            if not msgs:
                return ["EMPTY", ""]
            self.lists.pop(lst, None)
            self.store.pop(owner, None)
            return ["OK", json.dumps(msgs)]
        # Legacy drain (bare cjson return, no status tuple).
        if "return cjson.encode(msgs)" in script:
            lst = keys[0]
            msgs = list(self.lists.get(lst, []))
            for k in keys:
                self.lists.pop(k, None)
                self.store.pop(k, None)
            return json.dumps(msgs)
        # P1.8 inbound confirm: GET==token -> SETEX 1
        if "SETEX" in script and "GET" in script:
            key, expected = keys[0], argv[0]
            if self.store.get(key) == expected:
                self.store[key] = "1"
                return 1
            return 0
        # Release: GET==token -> DEL
        if "DEL" in script and "GET" in script:
            key, expected = keys[0], argv[0]
            if self.store.get(key) == expected:
                del self.store[key]
                return 1
            return 0
        return 0


def _msg(uid, cid, tg, content, gid):
    return {
        "user_id": str(uid),
        "content": content,
        "telegram_message_id": str(tg),
        "username": "u",
        "first_name": "f",
        "generation_id": gid,
        "creator_id": str(cid),
    }


def _patch_redis(fake):
    return patch("db.redis.get_redis", new=AsyncMock(return_value=fake))


# ---------------------------------------------------------------------------
# T1 — Concurrent enqueue: exactly one owner, all buffered, creator-isolated
# ---------------------------------------------------------------------------
async def test_T1_concurrent_enqueue_single_owner():
    from db.redis import debounce_enqueue

    fake = FakeRedis()
    with _patch_redis(fake):
        results = await asyncio.gather(
            *[
                debounce_enqueue(
                    user_id=100,
                    content=f"m{i}",
                    message_data=_msg(100, 1, 1000 + i, f"m{i}", f"gid{i}"),
                    window_seconds=3,
                    creator_id=1,
                )
                for i in range(10)
            ]
        )
    owners = [r for r in results if r]
    assert len(owners) == 1, f"expected exactly one owner, got {results!r}"
    assert len(set(owners)) == 1
    buffered = fake.lists.get("debounce:creator:1:user:100:messages", [])
    assert len(buffered) == 10, "all valid messages must be buffered"
    tgs = sorted(json.loads(m)["telegram_message_id"] for m in buffered)
    assert tgs == [str(1000 + i) for i in range(10)]


async def test_T1_creator_isolation_under_concurrency():
    from db.redis import debounce_consume, debounce_enqueue

    fake = FakeRedis()
    with _patch_redis(fake):
        ra, rb = await asyncio.gather(
            debounce_enqueue(100, "a", _msg(100, 1, 11, "a", "ga"), creator_id=1),
            debounce_enqueue(100, "b", _msg(100, 2, 11, "b", "gb"), creator_id=2),
        )
    assert ra and rb and ra != rb
    assert "debounce:creator:1:user:100:messages" in fake.lists
    assert "debounce:creator:2:user:100:messages" in fake.lists
    with _patch_redis(fake):
        sa, ma = await debounce_consume(100, 1, ra)
        sb, mb = await debounce_consume(100, 2, rb)
    assert (sa, sb) == ("ok", "ok")
    assert ma[-1]["content"] == "a" and mb[-1]["content"] == "b"


# ---------------------------------------------------------------------------
# T2 — m1/m2/m3 latest-only, gid verbatim
# ---------------------------------------------------------------------------
async def test_T2_latest_only_m1_m2_m3():
    from core.generation import telegram_generation_id
    from db.redis import debounce_consume, debounce_enqueue

    fake = FakeRedis()
    gids = [telegram_generation_id(200, f"m{i}", 2000 + i) for i in range(1, 4)]
    with _patch_redis(fake):
        fences = [
            await debounce_enqueue(
                200, f"m{i}", _msg(200, 1, 2000 + i, f"m{i}", gids[i - 1]), creator_id=1
            )
            for i in (1, 2, 3)
        ]
        assert sum(bool(f) for f in fences) == 1
        owner = next(f for f in fences if f)
        status, consumed = await debounce_consume(200, 1, owner)
    assert status == "ok"
    assert [m["content"] for m in consumed] == ["m1", "m2", "m3"]
    assert consumed[-1]["generation_id"] == gids[2]
    # Buffer fully cleared; nothing left behind.
    assert "debounce:creator:1:user:200:messages" not in fake.lists


# ---------------------------------------------------------------------------
# T3 — Two overlapping timers: late T1 is stale, cannot touch N+1
# ---------------------------------------------------------------------------
async def test_T3_overlapping_timers_stale_abdicates():
    from db.redis import debounce_consume, debounce_enqueue

    fake = FakeRedis()
    with _patch_redis(fake):
        fence_a = await debounce_enqueue(300, "m1", _msg(300, 1, 301, "m1", "g1"), creator_id=1)
        assert fence_a
        # Window N expires (lock TTL lapses) before T1 wakes.
        await fake.delete("debounce:creator:1:user:300:lock")
        # Successor window N+1 elects a new owner with a different fence.
        fence_b = await debounce_enqueue(300, "m4", _msg(300, 1, 304, "m4", "g4"), creator_id=1)
        assert fence_b and fence_b != fence_a
        # Late T1 wakes: must be stale, buffer untouched.
        status_a, msgs_a = await debounce_consume(300, 1, fence_a)
        assert status_a == "stale"
        assert msgs_a == []
        assert len(fake.lists["debounce:creator:1:user:300:messages"]) == 2
        # Valid owner consumes the successor window (both buffered messages).
        status_b, msgs_b = await debounce_consume(300, 1, fence_b)
        assert status_b == "ok"
        assert [m["content"] for m in msgs_b] == ["m1", "m4"]


# ---------------------------------------------------------------------------
# T4 — Timer vs recovery concurrently: exactly one logical enqueue
# ---------------------------------------------------------------------------
class RendezvousFakeRedis(FakeRedis):
    """Forces timer-consume and recovery-claim evals to overlap deterministically."""

    def __init__(self):
        super().__init__()
        self.entered = 0
        self.release = asyncio.Event()

    async def eval(self, script, numkeys, *args):
        if "'STALE'" in script or "'BUSY'" in script:
            self.entered += 1
            if self.entered >= 2:
                self.release.set()
            await asyncio.wait_for(self.release.wait(), timeout=5)
        return await super().eval(script, numkeys, *args)


async def test_T4_timer_vs_recovery_mutual_exclusion():
    from db.redis import debounce_consume, debounce_enqueue, requeue_stalled_debounce

    fake = RendezvousFakeRedis()
    with _patch_redis(fake):
        fence = await debounce_enqueue(400, "m1", _msg(400, 1, 401, "m1", "g1"), creator_id=1)
        await debounce_enqueue(400, "m2", _msg(400, 1, 402, "m2", "g2"), creator_id=1)
        # Lock lapses while the timer is still delayed.
        await fake.delete("debounce:creator:1:user:400:lock")
        (status, consumed), recovered = await asyncio.gather(
            debounce_consume(400, 1, fence),
            requeue_stalled_debounce(window_seconds=3, max_keys=10),
        )
    # Exactly one side wins the window (distinct tgIds: no dedup masking).
    assert (status == "ok") != (recovered == 1), (status, consumed, recovered)
    if status == "ok":
        assert [m["telegram_message_id"] for m in consumed] == ["401", "402"]
        latest = consumed[-1]
    else:
        assert recovered == 1
        (mid, data), = fake.streams["inbound_messages"]
        latest = data
        assert data["telegram_message_id"] == "402"
    # Winner's latest enqueued exactly once downstream.
    assert latest["content"] == "m2" and latest["generation_id"] == "g2"


# ---------------------------------------------------------------------------
# T5 — Boundary RPUSH: deterministic window assignment, never lost
# ---------------------------------------------------------------------------
async def test_T5a_rpush_before_consume_joins_window():
    from db.redis import debounce_consume, debounce_enqueue

    fake = FakeRedis()
    with _patch_redis(fake):
        fence = await debounce_enqueue(500, "m1", _msg(500, 1, 501, "m1", "g1"), creator_id=1)
        await debounce_enqueue(500, "m2", _msg(500, 1, 502, "m2", "g2"), creator_id=1)
        status, consumed = await debounce_consume(500, 1, fence)
    assert status == "ok"
    assert [m["telegram_message_id"] for m in consumed] == ["501", "502"]


async def test_T5b_rpush_after_consume_opens_successor_window():
    from db.redis import debounce_consume, debounce_enqueue

    fake = FakeRedis()
    with _patch_redis(fake):
        fence_a = await debounce_enqueue(501, "m1", _msg(501, 1, 511, "m1", "g1"), creator_id=1)
        status, consumed = await debounce_consume(501, 1, fence_a)
        assert [m["telegram_message_id"] for m in consumed] == ["511"]
        # Arrival after consumption elects a successor window (lock was cleared).
        fence_b = await debounce_enqueue(501, "m2", _msg(501, 1, 512, "m2", "g2"), creator_id=1)
        assert fence_b and fence_b != fence_a
        status_b, consumed_b = await debounce_consume(501, 1, fence_b)
    assert status_b == "ok"
    assert [m["telegram_message_id"] for m in consumed_b] == ["512"]


async def test_T5c_concurrent_boundary_exactly_once():
    from db.redis import debounce_consume, debounce_enqueue

    fake = FakeRedis()
    with _patch_redis(fake):
        fence_a = await debounce_enqueue(502, "m1", _msg(502, 1, 521, "m1", "g1"), creator_id=1)
        await debounce_enqueue(502, "m2", _msg(502, 1, 522, "m2", "g2"), creator_id=1)
        (status_a, consumed_a), fence_b = await asyncio.gather(
            debounce_consume(502, 1, fence_a),
            debounce_enqueue(502, "m3", _msg(502, 1, 523, "m3", "g3"), creator_id=1),
        )
        seen = [m["telegram_message_id"] for m in consumed_a]
        if fence_b:
            # m3 opened a successor window: drain it too.
            status_b, consumed_b = await debounce_consume(502, 1, fence_b)
            assert status_b == "ok"
            seen += [m["telegram_message_id"] for m in consumed_b]
        else:
            # m3 joined the consumed window.
            pass
    assert sorted(seen) == ["521", "522", "523"], seen
    assert len(seen) == len(set(seen)), "each message consumed exactly once"


# ---------------------------------------------------------------------------
# T6 — Crash points: everything remains recoverable + latest-only
# ---------------------------------------------------------------------------
async def test_T6a_lock_without_buffer_recovers_via_reconcile():
    """Crash after SET lock but before RPUSH/timer: DB reconcile owns it."""
    from db.redis import reconcile_inbound_gaps, requeue_stalled_debounce

    fake = FakeRedis()
    # Newest-first, mirroring ORDER BY created_at DESC.
    rows = [
        {"user_id": 600, "creator_id": 1, "content": "m2", "telegram_message_id": 602, "created_at": None},
        {"user_id": 600, "creator_id": 1, "content": "m1", "telegram_message_id": 601, "created_at": None},
    ]

    class FakeConn:
        async def fetch(self, *a, **kw):
            return rows

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakePool:
        def acquire(self):
            return FakeConn()

    with _patch_redis(fake):
        # Crashed owner: lock present, no owner key, no list, no timer.
        await fake.set("debounce:creator:1:user:600:lock", "w:dead", nx=True, ex=3)
        assert await requeue_stalled_debounce(window_seconds=3) == 0  # active lock: skip
        await fake.delete("debounce:creator:1:user:600:lock")  # TTL lapses
        assert await requeue_stalled_debounce(window_seconds=3) == 0  # empty: nothing
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=FakePool())):
            assert await reconcile_inbound_gaps(lookback_seconds=300, limit=10) == 1
    assert len(fake.streams["inbound_messages"]) == 1
    assert fake.streams["inbound_messages"][0][1]["telegram_message_id"] == "602"


async def test_T6b_buffer_without_timer_recovers_latest_only():
    """Crash after RPUSH but before timer creation: recovery owns it."""
    from db.redis import debounce_enqueue, requeue_stalled_debounce

    fake = FakeRedis()
    with _patch_redis(fake):
        fence = await debounce_enqueue(601, "m1", _msg(601, 1, 611, "m1", "g1"), creator_id=1)
        assert fence
        await debounce_enqueue(601, "m2", _msg(601, 1, 612, "m2", "g2"), creator_id=1)
        # Process dies here: no timer ever runs. Lock lapses.
        await fake.delete("debounce:creator:1:user:601:lock")
        assert await requeue_stalled_debounce(window_seconds=3) == 1
    assert len(fake.streams["inbound_messages"]) == 1
    assert fake.streams["inbound_messages"][0][1]["telegram_message_id"] == "612"


async def test_T6c_pop_then_crash_before_enqueue_recovers_same_latest():
    """Crash between atomic pop and XADD: reconcile re-enqueues the same latest."""
    from db.redis import debounce_consume, debounce_enqueue, reconcile_inbound_gaps

    fake = FakeRedis()
    with _patch_redis(fake):
        fence = await debounce_enqueue(602, "m1", _msg(602, 1, 621, "m1", "g1"), creator_id=1)
        await debounce_enqueue(602, "m2", _msg(602, 1, 622, "m2", "g2"), creator_id=1)
        status, consumed = await debounce_consume(602, 1, fence)
        assert status == "ok" and consumed[-1]["telegram_message_id"] == "622"
        # Crash: stream never written. DB still holds both rows (newest-first).
        rows = [
            {"user_id": 602, "creator_id": 1, "content": "m2", "telegram_message_id": 622, "created_at": None},
            {"user_id": 602, "creator_id": 1, "content": "m1", "telegram_message_id": 621, "created_at": None},
        ]

        class FakeConn:
            async def fetch(self, *a, **kw):
                return rows

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class FakePool:
            def acquire(self):
                return FakeConn()

        with patch("db.postgres.get_pool", new=AsyncMock(return_value=FakePool())):
            assert await reconcile_inbound_gaps(lookback_seconds=300, limit=10) == 1
    assert len(fake.streams["inbound_messages"]) == 1
    assert fake.streams["inbound_messages"][0][1]["telegram_message_id"] == "622"


# ---------------------------------------------------------------------------
# T7 — Recovery latest-only: stranded m1/m2/m3 -> one job (m3)
# ---------------------------------------------------------------------------
async def test_T7_recovery_single_latest_job():
    from db.redis import requeue_stalled_debounce

    fake = FakeRedis()
    key = "debounce:creator:1:user:700:messages"
    fake.lists[key] = [json.dumps(_msg(700, 1, 700 + i, f"m{i}", f"g{i}")) for i in (1, 2, 3)]
    with _patch_redis(fake):
        assert await requeue_stalled_debounce(window_seconds=3, max_keys=10) == 1
    assert len(fake.streams["inbound_messages"]) == 1
    assert fake.streams["inbound_messages"][0][1]["telegram_message_id"] == "703"
    assert fake.streams["inbound_messages"][0][1]["generation_id"] == "g3"
    assert key not in fake.lists


# ---------------------------------------------------------------------------
# T8 — Redis failure: no false success, buffer stays recoverable
# ---------------------------------------------------------------------------
async def test_T8_append_failure_reports_no_ownership():
    from db.redis import debounce_enqueue

    class FailRpush(FakeRedis):
        async def rpush(self, key, val):
            raise ConnectionError("redis down")

    fake = FailRpush()
    with _patch_redis(fake):
        with pytest.raises(ConnectionError):
            await debounce_enqueue(800, "m1", _msg(800, 1, 801, "m1", "g1"), creator_id=1)
    # Fenced lock release ran: no orphaned election lock left behind.
    assert "debounce:creator:1:user:800:lock" not in fake.store


async def test_T8_consume_failure_leaves_buffer():
    from db.redis import DebounceConsumeError, debounce_consume

    class FailEval(FakeRedis):
        async def eval(self, script, numkeys, *args):
            raise ConnectionError("redis down")

    fake = FailEval()
    fake.store["debounce:creator:1:user:801:owner"] = "w:abc"
    fake.lists["debounce:creator:1:user:801:messages"] = [json.dumps(_msg(801, 1, 802, "m", "g"))]
    with _patch_redis(fake):
        with pytest.raises(DebounceConsumeError):
            await debounce_consume(801, 1, "w:abc")
    assert len(fake.lists["debounce:creator:1:user:801:messages"]) == 1


# ---------------------------------------------------------------------------
# T9 — Duplicate Telegram update: persistence/dedup intact, collapse holds
# ---------------------------------------------------------------------------
async def test_T9_duplicate_tgid_collapse():
    from db.redis import debounce_consume, debounce_enqueue, enqueue_inbound

    fake = FakeRedis()
    with _patch_redis(fake):
        owner = await debounce_enqueue(900, "m1", _msg(900, 1, 901, "m1", "g1"), creator_id=1)
        # Redelivery before consumption: buffered (no dedup marker yet), harmless.
        assert await debounce_enqueue(900, "m1", _msg(900, 1, 901, "m1", "g1"), creator_id=1) == ""
        # Distinct second message still collapses to latest.
        assert await debounce_enqueue(900, "m2", _msg(900, 1, 902, "m2", "g2"), creator_id=1) == ""
        status, consumed = await debounce_consume(900, 1, owner)
        assert status == "ok" and consumed[-1]["telegram_message_id"] == "902"
        mid = await enqueue_inbound({**consumed[-1], "persona": "p"})
        assert not str(mid).startswith("duplicate:")
        # Redelivery after enqueue is suppressed at ingress (existing dedup).
        assert await debounce_enqueue(900, "m2", _msg(900, 1, 902, "m2", "g2"), creator_id=1) == ""
        assert "debounce:creator:1:user:900:messages" not in fake.lists


async def test_T9_handler_persists_before_debounce():
    """I1: save_inbound_message must precede debounce_enqueue in the handler."""
    import chatbotv2.handlers as handlers

    src = inspect.getsource(handlers.handle_incoming_message)
    assert src.index("save_inbound_message(") < src.index("debounce_enqueue(")


# ---------------------------------------------------------------------------
# T10 — Creator isolation (covered in T1) + independent latest
# ---------------------------------------------------------------------------
async def test_T10_independent_latest_per_creator():
    from db.redis import debounce_consume, debounce_enqueue

    fake = FakeRedis()
    with _patch_redis(fake):
        fa = await debounce_enqueue(1000, "A1", _msg(1000, 1, 1, "A1", "ga1"), creator_id=1)
        fb = await debounce_enqueue(1000, "B1", _msg(1000, 2, 2, "B1", "gb1"), creator_id=2)
        await debounce_enqueue(1000, "A2", _msg(1000, 1, 3, "A2", "ga2"), creator_id=1)
        await debounce_enqueue(1000, "B2", _msg(1000, 2, 4, "B2", "gb2"), creator_id=2)
        sa, ma = await debounce_consume(1000, 1, fa)
        sb, mb = await debounce_consume(1000, 2, fb)
    assert sa == sb == "ok"
    assert [m["content"] for m in ma] == ["A1", "A2"]
    assert [m["content"] for m in mb] == ["B1", "B2"]


# ---------------------------------------------------------------------------
# T11 — Delayed event loop: old timer cannot steal successor messages
# ---------------------------------------------------------------------------
async def test_T11_delayed_timer_abdicates_at_handler_level():
    import chatbotv2.handlers as handlers
    from core.generation import telegram_generation_id

    fake = FakeRedis()
    g1 = telegram_generation_id(1100, "m1", 1101)
    g2 = telegram_generation_id(1100, "m2", 1102)
    with patch("db.redis.get_redis", new=AsyncMock(return_value=fake)):
        from db.redis import debounce_enqueue

        fence_a = await debounce_enqueue(1100, "m1", _msg(1100, 1, 1101, "m1", g1), creator_id=1)
        await fake.delete("debounce:creator:1:user:1100:lock")  # window lapses (loop stall)
        fence_b = await debounce_enqueue(1100, "m2", _msg(1100, 1, 1102, "m2", g2), creator_id=1)
        assert fence_b != fence_a
        with (
            patch.object(handlers.asyncio, "sleep", new=AsyncMock()),
            patch.object(handlers, "enqueue_inbound", new=AsyncMock()) as mock_enq,
            patch.object(handlers, "get_cached_user_persona", new=AsyncMock(return_value="p")),
        ):
            await handlers._wait_and_process(1100, "u", "F", creator_id=1, fence=fence_a)
            mock_enq.assert_not_called()  # stale timer enqueues nothing
            await handlers._wait_and_process(1100, "u", "F", creator_id=1, fence=fence_b)
            mock_enq.assert_called_once()
            payload = mock_enq.call_args[0][0]
            # Phase 2.3: merged window [m1, m2] carries first-message identity.
            assert payload["content"] == "m1\nm2"
            assert payload["telegram_message_id"] == "1101"
            assert payload["generation_id"] == g1


# ---------------------------------------------------------------------------
# T12 — Generation identity forwarded verbatim
# ---------------------------------------------------------------------------
async def test_T12_generation_id_verbatim_end_to_end():
    import chatbotv2.handlers as handlers
    from core.generation import telegram_generation_id
    from db.redis import debounce_enqueue

    fake = FakeRedis()
    with patch("db.redis.get_redis", new=AsyncMock(return_value=fake)):
        fences = [
            await debounce_enqueue(
                1200,
                f"m{i}",
                _msg(1200, 1, 1200 + i, f"m{i}", telegram_generation_id(1200, f"m{i}", 1200 + i)),
                creator_id=1,
            )
            for i in (1, 2, 3)
        ]
        owner = next(f for f in fences if f)
        with (
            patch.object(handlers.asyncio, "sleep", new=AsyncMock()),
            patch.object(handlers, "enqueue_inbound", new=AsyncMock(return_value="9-0")) as mock_enq,
            patch.object(handlers, "get_cached_user_persona", new=AsyncMock(return_value="p")),
        ):
            await handlers._wait_and_process(1200, "u", "F", creator_id=1, fence=owner)
    mock_enq.assert_called_once()
    payload = mock_enq.call_args[0][0]
    # Phase 2.3: full-window merge with first-message (owner) identity.
    assert payload["content"] == "m1\nm2\nm3"
    assert payload["generation_id"] == telegram_generation_id(1200, "m1", 1201)
    assert payload["creator_id"] == "1"
    assert payload["telegram_message_id"] == "1201"
