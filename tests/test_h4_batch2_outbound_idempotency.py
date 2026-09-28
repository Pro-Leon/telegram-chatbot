"""H4 Batch 2: durable outbound idempotency tests (fakes only).

Covers: messages.dedup_id persistence idempotency on (creator_id, dedup_id),
creator-scoped stable Telegram random_id storage/reuse, DLQ-replay dedup
continuity, send-path wiring, and H4 Batch 1 regression guards.

No live Telegram, DropFans, llama.cpp, production Redis, or production
PostgreSQL. All marked unit.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

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
    """In-memory messages table enforcing the Batch 2 partial unique index.

    Emulates: UNIQUE (creator_id, dedup_id) WHERE dedup_id IS NOT NULL.
    NULL-dedup rows never conflict. Conflicting retries insert nothing and
    never overwrite the first row.
    """

    def __init__(self):
        self.rows = []
        self.statements = []

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        if "dedup_id" in sql:
            # Batch 2 shape: (user, creator, generation, dedup, content, ...).
            creator, dedup = args[1], args[3]
            for row in self.rows:
                if row["creator_id"] == creator and row["dedup_id"] == dedup:
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
        # Legacy shapes (generation-only or pre-migration): always insert.
        self.rows.append({"legacy": True, "nargs": len(args)})
        return "INSERT 0 1"


class _FakeRedis:
    """Minimal Redis fake: GET + SET (NX/EX) with str responses."""

    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


async def _save(pool_conn, **kwargs):
    import db.postgres as pg_mod

    params = {
        "user_id": 111,
        "content": "hi",
        "draft_content": "hi",
        "was_edited": False,
        "was_auto_approved": True,
        "confidence_score": 0.9,
        "operator_id": None,
        "telegram_message_id": 1001,
        "creator_id": 42,
        "generation_id": "gen-1",
    }
    params.update(kwargs)
    with patch.object(pg_mod, "get_pool", new=AsyncMock(return_value=_FakePool(pool_conn))):
        await pg_mod.save_outbound_after_send(**params)


# ---------------------------------------------------------------------------
# DB: idempotent persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_1_first_save_with_dedup_creates_one_row():
    conn = _FakePGOutboundConn()
    await _save(conn, dedup_id="d-1")
    assert len(conn.rows) == 1
    assert conn.rows[0]["dedup_id"] == "d-1"
    assert conn.rows[0]["creator_id"] == 42
    sql = conn.statements[0][0]
    assert "dedup_id" in sql
    assert "ON CONFLICT" in sql


@pytest.mark.asyncio
async def test_2_repeat_save_same_identity_creates_no_second_row():
    conn = _FakePGOutboundConn()
    await _save(conn, dedup_id="d-1")
    await _save(conn, dedup_id="d-1")
    assert len(conn.rows) == 1


@pytest.mark.asyncio
async def test_3_same_dedup_different_creators_do_not_collide():
    conn = _FakePGOutboundConn()
    await _save(conn, dedup_id="d-1", creator_id=1)
    await _save(conn, dedup_id="d-1", creator_id=2)
    assert len(conn.rows) == 2


@pytest.mark.asyncio
async def test_4_null_dedup_remains_legacy_compatible():
    conn = _FakePGOutboundConn()
    await _save(conn)
    await _save(conn)
    await _save(conn, dedup_id="   ")
    # Legacy rows never conflict with each other or with blank dedup.
    assert len(conn.rows) == 3
    assert all("dedup_id" not in sql for sql, _ in conn.statements)


@pytest.mark.asyncio
async def test_5_conflicting_retry_does_not_overwrite_authoritative_row():
    conn = _FakePGOutboundConn()
    await _save(conn, dedup_id="d-9", telegram_message_id=1001, content="first")
    await _save(conn, dedup_id="d-9", telegram_message_id=9999, content="contradictory retry")
    assert len(conn.rows) == 1
    assert conn.rows[0]["telegram_message_id"] == 1001
    assert conn.rows[0]["content"] == "first"


@pytest.mark.asyncio
async def test_5b_generation_id_not_unique_across_outbounds():
    conn = _FakePGOutboundConn()
    await _save(conn, dedup_id="d-a", generation_id="shared-gen")
    await _save(conn, dedup_id="d-b", generation_id="shared-gen")
    assert len(conn.rows) == 2


# ---------------------------------------------------------------------------
# Random ID: durable creator-scoped stability
# ---------------------------------------------------------------------------

def _patch_fake_redis(fake):
    import db.redis as redis_mod

    return patch.object(redis_mod, "get_redis", new=AsyncMock(return_value=fake))


@pytest.mark.asyncio
async def test_6_first_send_creates_and_stores_random_id():
    from db.redis import get_or_create_send_random_id

    fake = _FakeRedis()
    with _patch_fake_redis(fake):
        rid = await get_or_create_send_random_id("d-1", creator_id=42)
    assert isinstance(rid, bytes) and len(rid) == 16
    assert fake.store.get("send_random_id:42:d-1") == rid.hex()


@pytest.mark.asyncio
async def test_7_retry_same_identity_returns_same_random_id():
    from db.redis import get_or_create_send_random_id

    fake = _FakeRedis()
    with _patch_fake_redis(fake):
        first = await get_or_create_send_random_id("d-1", creator_id=42)
        second = await get_or_create_send_random_id("d-1", creator_id=42)
    assert first == second


@pytest.mark.asyncio
async def test_8_different_dedup_gets_different_random_id():
    from db.redis import get_or_create_send_random_id

    fake = _FakeRedis()
    with _patch_fake_redis(fake):
        rid_a = await get_or_create_send_random_id("d-a", creator_id=42)
        rid_b = await get_or_create_send_random_id("d-b", creator_id=42)
    assert rid_a != rid_b


@pytest.mark.asyncio
async def test_9_different_creator_gets_different_random_id():
    from db.redis import get_or_create_send_random_id

    fake = _FakeRedis()
    with _patch_fake_redis(fake):
        rid_1 = await get_or_create_send_random_id("d-1", creator_id=1)
        rid_2 = await get_or_create_send_random_id("d-1", creator_id=2)
    assert rid_1 != rid_2
    assert set(fake.store) == {"send_random_id:1:d-1", "send_random_id:2:d-1"}


@pytest.mark.asyncio
async def test_9b_missing_identity_returns_none_without_global_key():
    from db.redis import get_or_create_send_random_id

    fake = _FakeRedis()
    with _patch_fake_redis(fake):
        assert await get_or_create_send_random_id(None, creator_id=42) is None
        assert await get_or_create_send_random_id("d-1", creator_id=None) is None
        assert await get_or_create_send_random_id("  ", creator_id=42) is None
    assert fake.store == {}


@pytest.mark.asyncio
async def test_10_dlq_replay_dedup_yields_same_random_id():
    """DLQ replay preserves dedup_id, therefore the same random_id."""
    from db.redis import get_or_create_send_random_id

    original_payload = {"entity": "111", "content": "hi", "dedup_id": "replay-d",
                        "creator_id": "42", "generation_id": "gen-r"}
    # Existing replay contract passes dedup_id through verbatim.
    replayed_dedup = dict(original_payload).get("dedup_id")
    assert replayed_dedup == "replay-d"
    fake = _FakeRedis()
    with _patch_fake_redis(fake):
        before = await get_or_create_send_random_id(original_payload["dedup_id"], creator_id=42)
        after = await get_or_create_send_random_id(replayed_dedup, creator_id=42)
    assert before == after


@pytest.mark.asyncio
async def test_11_many_attempts_retrieve_same_persisted_value():
    from db.redis import get_or_create_send_random_id

    fake = _FakeRedis()
    with _patch_fake_redis(fake):
        seen = {await get_or_create_send_random_id("d-loop", creator_id=7) for _ in range(5)}
    assert len(seen) == 1


# ---------------------------------------------------------------------------
# Send integration: wiring + Batch 1 regression
# ---------------------------------------------------------------------------

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


def _drive_stack(data, client, msg_id="m1", overrides=None, vault_overrides=None,
                 redis_fake=None):
    import db.redis as redis_mod
    import db.vault as vault_mod
    from chatbotv2 import main as main_mod

    mocks = {        "get_send_dedup_value": AsyncMock(return_value=None),
        "try_reserve_send_dedup": AsyncMock(return_value="reserved:tok"),
        "confirm_send_dedup": AsyncMock(return_value=True),
        "mark_send_dedup": AsyncMock(),
        "ack_send": AsyncMock(),
        "save_outbound_after_send": AsyncMock(),
        "publish_event": AsyncMock(return_value="evt-1"),
        "move_send_to_dlq": AsyncMock(),
        "enqueue_send": AsyncMock(return_value="x-1"),
        "get_send_rate_limit_wait": AsyncMock(return_value=0),
        "check_send_rate_limit": AsyncMock(return_value=True),
        # H4 Batch 2/3 hermeticity: never touch live Redis from these tests.
        # test_13 overrides get_or_create_send_random_id with the real helper
        # bound to its FakeRedis to prove end-to-end stability.
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

    async def _pub(event_type, *args, **kwargs):
        published.append(event_type)
        return "evt-1"

    async def _dlq(message_id, reason, **kwargs):
        dlq.append({"message_id": message_id, "reason": reason, **kwargs})

    stack = ExitStack()
    use_pub = not (overrides and "publish_event" in overrides)
    use_dlq = not (overrides and "move_send_to_dlq" in overrides)
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
    if redis_fake is not None:
        # Real helper against fake Redis: proves end-to-end stability.
        stack.enter_context(
            patch.object(redis_mod, "get_redis", new=AsyncMock(return_value=redis_fake))
        )
    return stack, mocks, vault_mocks, published, dlq


def _ok_client(tg_id=777):
    client = AsyncMock()
    client.get_input_entity = AsyncMock(return_value=MagicMock())
    client.send_message = AsyncMock(return_value=MagicMock(id=tg_id))
    return client


@pytest.mark.asyncio
async def test_12_success_persists_dedup_and_sends():
    from chatbotv2 import main as main_mod

    client = _ok_client()
    stack, mocks, _, published, dlq = _drive_stack(_text_data(), client)
    with stack:
        await main_mod._process_send_entry_inner(client, "m1", _text_data())
    assert "message.sent" in published
    assert dlq == []
    save_kwargs = mocks["save_outbound_after_send"].call_args[1]
    assert save_kwargs["dedup_id"] == "plain:1"
    assert save_kwargs["creator_id"] == 42
    assert save_kwargs["generation_id"] == "gid-1"


@pytest.mark.asyncio
async def test_13_media_retry_reuses_same_random_id_end_to_end():
    import tempfile

    from chatbotv2 import main as main_mod

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"x")
        temp_path = f.name
    fake_redis = _FakeRedis()
    seen = []

    async def _send_file(entity, path, caption="", force_document=False, random_id=None):
        seen.append(random_id)
        return MagicMock(id=555)

    async def _real_random_id(dedup_id, creator_id=None, **kw):
        from db import redis as _redis_mod

        return await _redis_mod.get_or_create_send_random_id(dedup_id, creator_id=creator_id)

    data = _text_data(media_type="photo", media_path=temp_path,
                      fangate_media_id="9", dedup_id="media:9")
    for attempt in ("m1", "m2"):
        client = _ok_client(tg_id=555)
        stack, _mocks, _, _, _ = _drive_stack(
            data, client, msg_id=attempt, redis_fake=fake_redis,
            overrides={"get_or_create_send_random_id": AsyncMock(side_effect=_real_random_id)},
        )
        with stack, patch.object(main_mod, "send_file", new=AsyncMock(side_effect=_send_file)):
            await main_mod._process_send_entry_inner(client, attempt, dict(data))
    assert len(seen) == 2
    assert seen[0] is not None and seen[0] == seen[1]
    assert fake_redis.store.get("send_random_id:42:media:9") == seen[0].hex()


@pytest.mark.asyncio
async def test_14_batch1_save_failure_still_never_send_failed():
    from chatbotv2 import main as main_mod

    client = _ok_client()
    stack, _mocks, _, published, dlq = _drive_stack(
        _text_data(), client,
        overrides={"save_outbound_after_send": AsyncMock(side_effect=RuntimeError("pg down"))},
    )
    with stack:
        await main_mod._process_send_entry_inner(client, "m1", _text_data())
    assert "message.send_failed" not in published
    assert len(dlq) == 1 and dlq[0]["reason"] == "post_send_persistence_failed"
    assert dlq[0]["payload"]["dedup_id"] == "plain:1"


@pytest.mark.asyncio
async def test_15_batch1_post_send_failure_no_vault_release():
    import tempfile

    import db.vault as vault_mod
    from chatbotv2 import main as main_mod

    release_calls = []

    async def _rel(reservation_id):
        release_calls.append(reservation_id)
        return True

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"x")
        temp_path = f.name
    client = _ok_client(tg_id=556)
    data = _text_data(media_type="photo", media_path=temp_path,
                      fangate_media_id="9", dedup_id="media:15")
    with patch.object(vault_mod, "release_delivery", AsyncMock(side_effect=_rel)):
        stack, _mocks, _, published, dlq = _drive_stack(
            data, client,
            overrides={"save_outbound_after_send": AsyncMock(side_effect=RuntimeError("pg down"))},
        )
        with stack, patch.object(
            main_mod, "send_file", new=AsyncMock(return_value=MagicMock(id=556))
        ):
            await main_mod._process_send_entry_inner(client, "m1", dict(data))
    assert "message.send_failed" not in published
    assert len(dlq) == 1 and dlq[0]["reason"] == "post_send_persistence_failed"
    assert release_calls == []
