# -*- coding: utf-8 -*-
"""Phases 1-4 acceptance tests: first-message identity & correlation.

Covers remediation acceptance Tests 1-10 using fakes/mocks only.
No live llama.cpp, Telegram, DropFans, Redis production, or PostgreSQL
production required. All marked unit.
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ready_creator(creator_id):
    from commerce.single_creator import SingleCreatorStatus

    ctx = MagicMock()
    ctx.status = SingleCreatorStatus.READY
    ctx.creator_id = creator_id
    return ctx


class _FakeConn:
    """Minimal asyncpg-connection fake capturing SQL + params."""

    def __init__(self, fetchrow_result=None, execute_result="UPDATE 1"):
        self.statements = []
        self.fetchrow_result = fetchrow_result
        self.execute_result = execute_result

    async def fetchrow(self, sql, *args):
        self.statements.append((sql, args))
        return self.fetchrow_result

    async def fetch(self, sql, *args):
        self.statements.append((sql, args))
        return []

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        return self.execute_result


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


# ---------------------------------------------------------------------------
# Test 1 — canonical generation ID matches production algorithm
# ---------------------------------------------------------------------------

def test_1_canonical_telegram_generation_id():
    from core.generation import (
        ensure_generation_id,
        is_telegram_generation_id,
        is_valid_generation_id,
        synthetic_generation_id,
        telegram_generation_id,
    )

    user_id, content, tg_id = 123, "hello", 456
    expected = hashlib.md5(f"{user_id}:{content}:{tg_id}".encode()).hexdigest()
    gid = telegram_generation_id(user_id, content, tg_id)
    assert gid == expected
    assert len(gid) == 32
    int(gid, 16)
    assert is_telegram_generation_id(gid)
    assert is_valid_generation_id(gid)
    # Existing valid IDs are authoritative, never recomputed.
    assert ensure_generation_id(gid, user_id=999, content="other", telegram_message_id=1) == gid
    # Genuinely absent IDs fall back to deterministic recomputation.
    assert ensure_generation_id(None, user_id=user_id, content=content, telegram_message_id=tg_id) == expected
    assert ensure_generation_id("", user_id=user_id, content=content, telegram_message_id=tg_id) == expected
    assert ensure_generation_id(None) is None
    # Synthetic IDs are namespaced and never MD5-shaped.
    synth = synthetic_generation_id("scheduled:", "key:1")
    assert synth.startswith("scheduled:")
    assert not is_telegram_generation_id(synth)
    assert is_valid_generation_id(synth)
    assert not is_valid_generation_id("")
    assert not is_valid_generation_id(None)
    with pytest.raises(ValueError):
        synthetic_generation_id("nosuffix", "x")


# ---------------------------------------------------------------------------
# Test 2 — generation survives inbound path (persist + debounce + enqueue)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_2a_save_inbound_persists_generation_id():
    import db.postgres as pg
    from core.generation import telegram_generation_id

    conn = _FakeConn(fetchrow_result={"id": 7})
    pool = _FakePool(conn)
    gid = telegram_generation_id(123, "hello", 456)
    with patch.object(pg, "get_pool", new=AsyncMock(return_value=pool)):
        row_id = await pg.save_inbound_message(
            123, "hello", 456, creator_id=42, generation_id=gid,
        )
    assert row_id == 7
    sql, args = conn.statements[0]
    assert "generation_id" in sql
    # Passed through verbatim: (user, creator, generation, content, tg).
    assert args[0] == 123 and args[1] == 42 and args[2] == gid
    assert args[3] == "hello" and args[4] == 456


@pytest.mark.asyncio
async def test_2b_debounce_preserves_generation_id():
    import chatbotv2.handlers as handlers
    from core.generation import telegram_generation_id

    gid = telegram_generation_id(123, "hello", 456)
    captured = {}

    async def _fake_enqueue(payload):
        captured.update(payload)
        return "inbound-1"

    buffered = [{
        "user_id": "123",
        "content": "hello",
        "telegram_message_id": "456",
        "username": "u",
        "first_name": "f",
        "generation_id": gid,
        "creator_id": "42",
    }]
    with (
        patch.object(handlers.asyncio, "sleep", new=AsyncMock()),
        patch.object(handlers, "get_debounced_messages", new=AsyncMock(return_value=buffered)),
        patch.object(handlers, "enqueue_inbound", new=AsyncMock(side_effect=_fake_enqueue)),
        patch.object(handlers, "get_cached_user_persona", new=AsyncMock(return_value="persona")),
        patch.object(handlers, "cache_user_persona", new=AsyncMock()),
        patch.object(handlers, "get_cached_default_persona", new=AsyncMock(return_value=None)),
        patch.object(handlers, "get_default_persona", new=AsyncMock(return_value="")),
        patch.object(handlers, "cache_default_persona", new=AsyncMock()),
    ):
        await handlers._wait_and_process(123, "u", "f", creator_id=42)
    assert captured["generation_id"] == gid
    assert captured["creator_id"] == "42"
    assert captured["content"] == "hello"


# ---------------------------------------------------------------------------
# Test 3 — XAUTOCLAIM/recovery preservation (no recompute when present)
# ---------------------------------------------------------------------------

def test_3a_ensure_preserves_stream_generation_id():
    from core.generation import ensure_generation_id, telegram_generation_id

    gid = telegram_generation_id(123, "hello", 456)
    assert ensure_generation_id(gid, user_id=123, content="hello", telegram_message_id=456) == gid


def test_3b_worker_normal_path_forwards_stream_generation_id():
    import inspect

    import workers.llm_worker as lw

    source = inspect.getsource(lw.run_worker)
    assert '"generation_id": data.get("generation_id") or None' in source


@pytest.mark.asyncio
async def test_3c_enqueue_inbound_preserves_generation_id():
    import db.redis as redis_mod
    from core.generation import telegram_generation_id

    gid = telegram_generation_id(123, "hello", 456)
    captured = {}

    fake_redis = AsyncMock()
    fake_redis.set = AsyncMock(return_value=True)

    async def _fake_xadd(stream, data, id="*"):
        captured.update(data)
        return "stream-1"

    fake_redis.xadd = _fake_xadd
    fake_redis.eval = AsyncMock(return_value=1)
    with patch.object(redis_mod, "get_redis", new=AsyncMock(return_value=fake_redis)):
        msg_id = await redis_mod.enqueue_inbound({
            "user_id": "123",
            "content": "hello",
            "telegram_message_id": "456",
            "generation_id": gid,
            "creator_id": "42",
        })
    assert msg_id == "stream-1"
    assert captured["generation_id"] == gid


# ---------------------------------------------------------------------------
# Shared legacy-path driver for worker routing tests (Tests 4 + 9)
# ---------------------------------------------------------------------------

async def _drive_worker_legacy(*, auto_reply, score, flags, creator_id=42, generation_id=None):
    """Drive process_message via the legacy path with fakes.

    Returns (add_kwargs, batch_events, published, enqueue_calls).
    """
    import workers.llm_worker as lw
    from core.generation import telegram_generation_id

    gid = generation_id or telegram_generation_id(123, "hello", 456)
    add_kwargs = {}
    batch_events = []
    published = []
    enqueue_calls = []

    async def _fake_add(**kwargs):
        add_kwargs.update(kwargs)
        return 99

    async def _fake_batch(events):
        batch_events.extend(events)

    async def _fake_publish(event_type, data, **kwargs):
        published.append({"event_type": event_type, "data": data, **kwargs})

    async def _fake_enqueue(payload=None, **kwargs):
        enqueue_calls.append({"payload": dict(payload or {}), **kwargs})
        return "send-1"

    with (
        patch.object(lw._settings, "llm_path", "legacy"),
        patch.object(lw, "upsert_user", new=AsyncMock()),
        patch.object(lw, "get_recent_messages", new=AsyncMock(return_value=[])),
        patch.object(lw, "build_qwen3_context", new=AsyncMock(return_value=[])),
        patch.object(lw, "is_user_auto_reply_excluded", new=AsyncMock(return_value=False)),
        patch.object(lw, "is_auto_reply_enabled", new=AsyncMock(return_value=auto_reply)),
        patch.object(lw, "add_to_operator_queue", new=AsyncMock(side_effect=_fake_add)),
        patch.object(lw, "acquire_user_lock", new=AsyncMock(return_value=True)),
        patch.object(lw, "generate_draft", new=AsyncMock(return_value="Draft response")),
        patch.object(lw, "score_draft", new=AsyncMock(return_value=(score, flags))),
        patch.object(lw, "extract_and_update_profile", new=AsyncMock()),
        patch.object(lw, "maybe_summarize", new=AsyncMock()),
        patch.object(lw, "post_process", new=AsyncMock()),
        patch.object(lw, "enqueue_send", new=AsyncMock(side_effect=_fake_enqueue)),
        patch("core.event_bus.publish_event", new=AsyncMock(side_effect=_fake_publish)),
        patch("core.event_bus.publish_events_batch", new=AsyncMock(side_effect=_fake_batch)),
        patch("db.redis.get_redis", new=AsyncMock()),
    ):
        await lw.process_message(
            123, "hello", 456, "u", "f", "persona",
            generation_id=gid, creator_id=creator_id,
        )
    return add_kwargs, batch_events, published, enqueue_calls, gid


# ---------------------------------------------------------------------------
# Test 4 — auto_reply_off queues (no crash, creator + generation preserved)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_4_auto_reply_off_queues_with_identity():
    add_kwargs, batch, published, enqueue_calls, gid = await _drive_worker_legacy(
        auto_reply=False, score=0.5, flags=[],
    )
    # No exception, operator queue created with identity.
    assert add_kwargs["creator_id"] == 42
    assert add_kwargs["generation_id"] == gid
    assert add_kwargs["user_id"] == 123
    # No automatic send occurs on this branch.
    assert enqueue_calls == []
    # Suggestion event carries the same correlation.
    suggestion = [e for e in batch if e["event"] == "suggestion.created"]
    assert len(suggestion) == 1
    assert suggestion[0]["creator_id"] == 42
    assert suggestion[0]["generation_id"] == gid
    completed = [e for e in batch if e["event"] == "ai.generation_completed"]
    assert len(completed) == 1
    assert completed[0]["creator_id"] == 42
    assert completed[0]["generation_id"] == gid


# ---------------------------------------------------------------------------
# Test 5 — operator correlation (queue row -> send payload -> outbound)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_5a_process_approved_message_preserves_identity():
    from workers.send_worker import process_approved_message
    from core.generation import telegram_generation_id

    gid = telegram_generation_id(123, "hello", 456)
    captured = {}

    async def _fake_enqueue(payload, **kwargs):
        captured["payload"] = dict(payload)
        captured.update(kwargs)
        return "send-1"

    with patch("workers.send_worker.enqueue_send", new=AsyncMock(side_effect=_fake_enqueue)):
        result = await process_approved_message(
            123, "hello", confidence_score=0.9, operator_id=None,
            queue_id=10, creator_id=42, generation_id=gid,
        )
    # M7 (B3): result carries the dedup identity used for the send.
    assert result["ok"] is True
    assert result["dedup_id"] == "queue_item:10"
    assert captured["payload"]["creator_id"] == "42"
    assert captured["payload"]["generation_id"] == gid
    assert captured["generation_id"] == gid
    assert captured["creator_id"] == 42
    # queue_item:{id} remains the dedup key, not the correlation ID.
    assert captured["dedup_id"] == "queue_item:10"


@pytest.mark.asyncio
async def test_5b_dashboard_send_preserves_row_identity():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth
    from core.generation import telegram_generation_id

    gid = telegram_generation_id(123, "hello", 456)
    row = {
        "id": 10, "user_id": 123, "creator_id": 42, "generation_id": gid,
        "draft_content": "Hello!", "status": "pending",
        "confidence_score": 0.9, "assigned_to": None, "edited": False,
    }
    enqueued = {}

    async def _fake_enqueue(payload, **kwargs):
        enqueued["payload"] = dict(payload)
        enqueued.update(kwargs)
        return "send-1"

    with (
        patch("commerce.single_creator.resolve_single_application_creator",
              new=AsyncMock(return_value=_ready_creator(42))),
        patch("chatbotv2.dashboard.routes.queue.get_queue_item",
              new=AsyncMock(return_value=row)),
        patch("chatbotv2.dashboard.routes.queue.enqueue_send",
              new=AsyncMock(side_effect=_fake_enqueue)),
        patch("chatbotv2.dashboard.routes.queue.resolve_queue_item",
              new=AsyncMock(return_value=True)),
        patch("chatbotv2.dashboard.routes.queue.publish_event", new=AsyncMock()),
    ):
        app.dependency_overrides[require_auth] = lambda: {"username": "op1"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/suggestions/10/send")
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert enqueued["payload"]["creator_id"] == "42"
    assert enqueued["payload"]["generation_id"] == gid
    assert enqueued["generation_id"] == gid


@pytest.mark.asyncio
async def test_5c_save_outbound_after_send_persists_identity():
    import db.postgres as pg
    from core.generation import telegram_generation_id

    conn = _FakeConn()
    pool = _FakePool(conn)
    gid = telegram_generation_id(123, "hello", 456)
    with patch.object(pg, "get_pool", new=AsyncMock(return_value=pool)):
        await pg.save_outbound_after_send(
            123, "hello", "hello", False, True, 0.9, None, 777,
            creator_id=42, generation_id=gid,
        )
    sql, args = conn.statements[0]
    assert "generation_id" in sql
    # (user, creator, generation, content, ...) passed through verbatim.
    assert args[0] == 123 and args[1] == 42 and args[2] == gid


# ---------------------------------------------------------------------------
# Test 6 — creator isolation on queue mutation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_6a_resolve_forwards_creator_and_operator():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    row = {"id": 1, "user_id": 123, "creator_id": 2, "draft_content": "Hi", "status": "pending"}
    get_calls = {}
    resolve_calls = {}

    async def _fake_get(queue_id, creator_id=None):
        get_calls["queue_id"] = queue_id
        get_calls["creator_id"] = creator_id
        return row

    async def _fake_resolve(queue_id, status, **kwargs):
        resolve_calls["queue_id"] = queue_id
        resolve_calls["status"] = status
        resolve_calls.update(kwargs)
        return True

    with (
        patch("commerce.single_creator.resolve_single_application_creator",
              new=AsyncMock(return_value=_ready_creator(2))),
        patch("chatbotv2.dashboard.routes.queue.get_queue_item", new=_fake_get),
        patch("chatbotv2.dashboard.routes.queue.resolve_queue_item", new=_fake_resolve),
        patch("chatbotv2.dashboard.routes.queue.publish_event", new=AsyncMock()),
    ):
        app.dependency_overrides[require_auth] = lambda: {"username": "op2"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/queue/1/resolve", data={"status": "approved"})
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert get_calls == {"queue_id": 1, "creator_id": 2}
    assert resolve_calls["creator_id"] == 2
    assert resolve_calls["resolved_by"] == "op2"


@pytest.mark.asyncio
async def test_6b_cross_creator_resolve_rejected_no_mutation():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    resolve_mock = AsyncMock(return_value=True)
    enqueue_mock = AsyncMock()
    with (
        patch("commerce.single_creator.resolve_single_application_creator",
              new=AsyncMock(return_value=_ready_creator(2))),
        # Scoped read misses: row belongs to creator 1.
        patch("chatbotv2.dashboard.routes.queue.get_queue_item",
              new=AsyncMock(return_value=None)),
        patch("chatbotv2.dashboard.routes.queue.resolve_queue_item", resolve_mock),
        patch("chatbotv2.dashboard.routes.queue.enqueue_send", enqueue_mock),
        patch("chatbotv2.dashboard.routes.queue.publish_event", new=AsyncMock()),
    ):
        app.dependency_overrides[require_auth] = lambda: {"username": "op2"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/suggestions/1/send")
        app.dependency_overrides.clear()
    assert resp.status_code == 404
    resolve_mock.assert_not_called()
    enqueue_mock.assert_not_called()


@pytest.mark.asyncio
async def test_6c_owner_can_resolve_own_item():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    row = {"id": 1, "user_id": 123, "creator_id": 1, "draft_content": "Hi", "status": "pending"}
    with (
        patch("commerce.single_creator.resolve_single_application_creator",
              new=AsyncMock(return_value=_ready_creator(1))),
        patch("chatbotv2.dashboard.routes.queue.get_queue_item",
              new=AsyncMock(return_value=row)),
        patch("chatbotv2.dashboard.routes.queue.resolve_queue_item",
              new=AsyncMock(return_value=True)),
        patch("chatbotv2.dashboard.routes.queue.publish_event", new=AsyncMock()),
    ):
        app.dependency_overrides[require_auth] = lambda: {"username": "op1"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/queue/1/resolve", data={"status": "approved"})
        app.dependency_overrides.clear()
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Test 7 — double resolution guarded by pending predicate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_7a_resolve_is_creator_and_pending_scoped():
    import db.postgres as pg

    conn = _FakeConn(execute_result="UPDATE 1")
    pool = _FakePool(conn)
    with patch.object(pg, "get_pool", new=AsyncMock(return_value=pool)):
        mutated = await pg.resolve_queue_item(1, "approved", creator_id=2, resolved_by="op2")
    assert mutated is True
    sql, args = conn.statements[0]
    assert "creator_id" in sql and "status = 'pending'" in sql
    assert args[-2] == 1 and args[-1] == 2


@pytest.mark.asyncio
async def test_7b_second_resolution_does_not_mutate():
    import db.postgres as pg

    conn = _FakeConn(execute_result="UPDATE 0")
    pool = _FakePool(conn)
    with patch.object(pg, "get_pool", new=AsyncMock(return_value=pool)):
        mutated = await pg.resolve_queue_item(1, "approved", creator_id=2)
    assert mutated is False


# ---------------------------------------------------------------------------
# Test 8 — scheduler correlation uses synthetic namespace
# ---------------------------------------------------------------------------

def test_8a_scheduled_payload_has_synthetic_generation_id():
    from workers.scheduler_worker import _build_send_payload, _make_dedup_id

    msg = {
        "id": 5, "user_id": 123, "creator_id": 42, "content": "follow-up",
        "media_type": "", "media_path": "", "dedup_key": "post_purchase:9",
    }
    payload = _build_send_payload(msg)
    assert payload["generation_id"].startswith("scheduled:")
    assert payload["generation_id"] == "scheduled:post_purchase:9:5"
    assert payload["creator_id"] == "42"
    # Same row always yields the same dedup + generation (retry-safe).
    assert _make_dedup_id(msg) == "scheduled:post_purchase:9:5"
    assert _build_send_payload(dict(msg))["generation_id"] == payload["generation_id"]


@pytest.mark.asyncio
async def test_8b_scheduler_enqueue_carries_synthetic_id():
    import workers.scheduler_worker as sched

    row = {
        "id": 5, "user_id": 123, "creator_id": 42, "content": "follow-up",
        "media_type": "", "media_path": "", "dedup_key": "post_purchase:9",
        "status": "pending", "reason": "",
    }
    enqueued = {}

    async def _fake_enqueue(payload, **kwargs):
        enqueued["payload"] = dict(payload)
        enqueued.update(kwargs)
        return "send-1"

    with (
        patch.object(sched, "claim_due_messages", new=AsyncMock(return_value=[{"id": 5}])),
        patch("db.postgres.get_scheduled_message", new=AsyncMock(return_value=row)),
        patch.object(sched, "_check_user_eligible", new=AsyncMock(return_value=True)),
        patch.object(sched, "enqueue_send", new=AsyncMock(side_effect=_fake_enqueue)),
        patch.object(sched, "mark_scheduled_enqueued", new=AsyncMock()),
        patch.object(sched, "mark_scheduled_failed", new=AsyncMock()),
    ):
        count = await sched.process_due_messages("test-worker")
    assert count == 1
    assert enqueued["payload"]["generation_id"].startswith("scheduled:")
    assert enqueued["generation_id"] == enqueued["payload"]["generation_id"]
    assert "Telegram-derived" not in enqueued["generation_id"]
    assert len(enqueued["generation_id"]) != 32 or ":" in enqueued["generation_id"]


# ---------------------------------------------------------------------------
# Test 9 — user-scoped events carry creator + generation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_9_auto_approved_events_carry_identity():
    add_kwargs, batch, published, enqueue_calls, gid = await _drive_worker_legacy(
        auto_reply=True, score=0.95, flags=[],
    )
    # Auto-send happened with identity in the payload.
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["payload"]["generation_id"] == gid
    assert enqueue_calls[0]["generation_id"] == gid
    assert enqueue_calls[0]["creator_id"] == 42
    # Started event carries identity.
    started = [e for e in published if e["event_type"] == "ai.generation_started"]
    assert len(started) == 1
    assert started[0]["creator_id"] == 42
    assert started[0]["generation_id"] == gid
    assert started[0]["user_id"] == 123
    # Completed batch event carries identity.
    completed = [e for e in batch if e["event"] == "ai.generation_completed"]
    assert len(completed) == 1
    for event in batch:
        assert event["creator_id"] == 42
        assert event["generation_id"] == gid
        assert event["user_id"] == 123


# ---------------------------------------------------------------------------
# Test 10 — no commerce-authority regression (deterministic gates intact)
# ---------------------------------------------------------------------------

def test_10a_operator_send_path_creates_no_commerce_state():
    import inspect

    import workers.send_worker as send_worker

    source = inspect.getsource(send_worker.process_approved_message)
    assert "execute_ppv" not in source
    assert "create_offer" not in source
    assert "generation_id" in source


def test_10b_generation_id_never_authorizes_commerce():
    import inspect

    import commerce.opportunity_execution as execution
    import commerce.opportunity_sealing as sealing

    # Correlation identity lives outside commerce authority: neither sealing
    # nor execution imports the generation helper, so a generation ID can
    # never become price/product/eligibility evidence.
    for module in (sealing, execution):
        assert "core.generation" not in inspect.getsource(module)
    # Sealing still verifies live DropFans state deterministically.
    assert "price" in inspect.getsource(sealing.seal_ranked_candidate)


def test_10c_auto_send_still_gated_on_score_and_flags():
    import inspect

    import workers.llm_worker as lw

    source = inspect.getsource(lw.process_message)
    # Phase 1.4: gate is HARD-tier-only (info flags are score-penalty-only).
    assert "score >= _settings.auto_approve_threshold and not _has_hard_flags" in source


# ---------------------------------------------------------------------------
# Test 11 — remediation follow-ups: canonical ownership on remaining surfaces
# (manual dashboard sends, AI-reply requeue, Lab synthetic IDs, scheduler
# persistence, resolve-event correlation, worker fallback helper).
# ---------------------------------------------------------------------------

def test_11a_llm_worker_fallback_uses_canonical_helper():
    import inspect

    import workers.llm_worker as lw

    source = inspect.getsource(lw.process_message)
    # Recovery fallback must go through the canonical helper (same MD5
    # algorithm), not a duplicated inline hashlib.md5 for generation identity.
    assert "telegram_generation_id(" in source
    assert "from core.generation import ensure_generation_id, telegram_generation_id" in source


def test_11b_lab_turn_uses_namespaced_synthetic_id():
    import inspect

    import chatbotv2.dashboard.routes.lab as lab

    source = inspect.getsource(lab._single_lab_turn)
    assert "manual_generation_id(" in source
    # Must never mint MD5-shaped IDs for synthetic lab turns.
    assert 'hashlib.md5(f"{fan_user_id}' not in source


def test_11c_ai_reply_uses_canonical_helper_and_creator():
    import inspect

    import chatbotv2.dashboard.routes.messages as messages

    source = inspect.getsource(messages.api_dialog_ai_reply)
    assert "telegram_generation_id(" in source
    assert '"creator_id"' in source


@pytest.mark.asyncio
async def test_11d_manual_send_carries_synthetic_identity():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    enqueued = {}

    async def _fake_enqueue(payload, **kwargs):
        enqueued["payload"] = dict(payload)
        enqueued.update(kwargs)
        return "send-1"

    with (
        patch("commerce.single_creator.resolve_single_application_creator",
              new=AsyncMock(return_value=_ready_creator(42))),
        patch("chatbotv2.dashboard.routes.messages.enqueue_send",
              new=AsyncMock(side_effect=_fake_enqueue)),
    ):
        app.dependency_overrides[require_auth] = lambda: {"username": "op1"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/send-message", json={"user_id": 123, "content": "hi"})
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert enqueued["payload"]["creator_id"] == "42"
    assert enqueued["creator_id"] == 42
    assert enqueued["payload"]["generation_id"].startswith("manual:")
    assert enqueued["generation_id"] == enqueued["payload"]["generation_id"]


@pytest.mark.asyncio
async def test_11e_dialog_send_carries_synthetic_identity():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    enqueued = {}

    async def _fake_enqueue(payload, **kwargs):
        enqueued["payload"] = dict(payload)
        enqueued.update(kwargs)
        return "send-1"

    with (
        patch("commerce.single_creator.resolve_single_application_creator",
              new=AsyncMock(return_value=_ready_creator(42))),
        patch("chatbotv2.dashboard.routes.messages.enqueue_send",
              new=AsyncMock(side_effect=_fake_enqueue)),
    ):
        app.dependency_overrides[require_auth] = lambda: {"username": "op1"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dialogs/123/send", data={"content": "hello"})
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert enqueued["payload"]["creator_id"] == "42"
    assert enqueued["payload"]["generation_id"].startswith("manual:")
    assert enqueued["generation_id"] == enqueued["payload"]["generation_id"]


@pytest.mark.asyncio
async def test_11f_ai_reply_requeue_preserves_canonical_generation():
    import chatbotv2.dashboard.routes.messages as messages
    from core.generation import telegram_generation_id

    captured = {}
    row = {
        "user_id": 123, "content": "hello", "telegram_message_id": 456,
        "username": "u", "first_name": "f",
    }
    expected = telegram_generation_id(123, "hello", 456)

    class _FakeConn2:
        async def fetchrow(self, sql, *args):
            return row

    class _FakePool2:
        def acquire(self):
            conn = _FakeConn2()

            class _Ctx:
                async def __aenter__(self):
                    return conn

                async def __aexit__(self, *exc):
                    return None

            return _Ctx()

    async def _fake_enqueue(payload):
        captured.update(payload)
        return "inbound-1"

    with (
        patch("commerce.single_creator.resolve_single_application_creator",
              new=AsyncMock(return_value=_ready_creator(42))),
        patch.object(messages, "get_pool", new=AsyncMock(return_value=_FakePool2())),
        patch.object(messages, "get_cached_user_persona", new=AsyncMock(return_value="persona")),
        patch.object(messages, "enqueue_inbound", new=AsyncMock(side_effect=_fake_enqueue)),
    ):
        await messages.api_dialog_ai_reply(123, auth={"username": "op1"})
    assert captured["generation_id"] == expected
    assert captured["creator_id"] == "42"


@pytest.mark.asyncio
async def test_11g_create_scheduled_message_persists_generation_id():
    import db.postgres as pg

    conn = _FakeConn(fetchrow_result={"id": 9})
    pool = _FakePool(conn)
    with patch.object(pg, "get_pool", new=AsyncMock(return_value=pool)):
        # Asyncpg fakes have no transaction(); stub it for this unit test.
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_tx():
            yield conn

        conn.transaction = _fake_tx
        conn.fetchval = AsyncMock(return_value=None)
        msg_id = await pg.create_scheduled_message(
            123, "2026-01-01", "follow-up", "dedup-1",
            creator_id=42, generation_id="scheduled:dedup-1:9",
        )
    assert msg_id == 9
    # M7 (B1/B4): creation emits a best-effort audit INSERT after the
    # scheduled INSERT; locate the scheduled INSERT explicitly.
    sched_stmts = [(s, a) for s, a in conn.statements if "INSERT INTO scheduled_messages" in s]
    assert sched_stmts, "scheduled INSERT missing"
    sql, args = sched_stmts[-1]
    assert "generation_id" in sql
    assert args[7] == 42 and args[8] == "scheduled:dedup-1:9"


@pytest.mark.asyncio
async def test_11h_get_scheduled_message_selects_generation_id():
    import db.postgres as pg

    row = {"id": 5, "generation_id": "scheduled:post_purchase:9:5", "creator_id": 42}
    conn = _FakeConn(fetchrow_result=row)
    pool = _FakePool(conn)
    with patch.object(pg, "get_pool", new=AsyncMock(return_value=pool)):
        result = await pg.get_scheduled_message(5)
    assert result["generation_id"] == "scheduled:post_purchase:9:5"
    sql, _ = conn.statements[0]
    assert "generation_id" in sql


@pytest.mark.asyncio
async def test_11i_resolve_event_carries_row_generation():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth
    from core.generation import telegram_generation_id

    gid = telegram_generation_id(123, "hello", 456)
    row = {"id": 1, "user_id": 123, "creator_id": 2, "draft_content": "Hi",
           "status": "pending", "generation_id": gid}
    published = []

    async def _fake_publish(event_type, data, **kwargs):
        published.append({"event_type": event_type, "data": data, **kwargs})

    with (
        patch("commerce.single_creator.resolve_single_application_creator",
              new=AsyncMock(return_value=_ready_creator(2))),
        patch("chatbotv2.dashboard.routes.queue.get_queue_item",
              new=AsyncMock(return_value=row)),
        patch("chatbotv2.dashboard.routes.queue.resolve_queue_item",
              new=AsyncMock(return_value=True)),
        patch("chatbotv2.dashboard.routes.queue.publish_event", new=_fake_publish),
    ):
        app.dependency_overrides[require_auth] = lambda: {"username": "op2"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/queue/1/resolve", data={"status": "approved"})
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert published[0]["generation_id"] == gid
    assert published[0]["creator_id"] == 2


# ---------------------------------------------------------------------------
# Phase 5 — routing integrity (H3 invalid-output bar + needs_handoff,
# H6 excluded-user observability). Fakes/mocks only.
# ---------------------------------------------------------------------------

def _routing_settings(**overrides):
    settings = MagicMock(
        llm_path="new",
        user_lock_ttl=60,
        auto_approve_threshold=0.80,
        autonomy_enabled=True,
        context_engine_enabled=False,
        context_engine_observational=False,
        context_engine_sample_rate=0.0,
        llm_provider="llamacpp",
        llama_model="default",
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _one_call_result(*, reply="hello fan", score=0.95, needs_handoff=False,
                     advisory_handoff=False, is_valid=True, quality_flags=None,
                     safety_flags=None, validation_error=None):
    from commerce.signals import CommerceSignals
    from core.one_call import OneCallResult

    return OneCallResult(
        reply=reply,
        signals=CommerceSignals.low_information(),
        confidence=0.9,
        needs_handoff=needs_handoff,
        advisory_handoff=advisory_handoff,
        is_valid=is_valid,
        validation_error=validation_error,
        quality_score=score,
        quality_flags=list(quality_flags or []),
        safety_flags=list(safety_flags or []),
    )


async def _drive_worker_new_path(*, one_call=None, auto_reply=True,
                                 canonical_decision=None, seal_setup=None):
    """Drive process_message on the OneCall path with fakes.

    Returns (queue_kwargs, batch_events, published, enqueue_calls, gid).
    """
    import workers.llm_worker as lw
    from core.generation import telegram_generation_id

    gid = telegram_generation_id(123, "hello", 456)
    queue_kwargs = {}
    batch_events = []
    published = []
    enqueue_calls = []

    async def _fake_add(**kwargs):
        queue_kwargs.update(kwargs)
        return 99

    async def _fake_batch(events):
        batch_events.extend(events)

    async def _fake_publish(event_type, data, **kwargs):
        published.append({"event_type": event_type, "data": data, **kwargs})

    async def _fake_enqueue(payload=None, **kwargs):
        enqueue_calls.append({"payload": dict(payload or {}), **kwargs})
        return "send-1"

    if one_call is None:
        one_call = _one_call_result()

    persona_validation = MagicMock(valid=True, severe=False, validation_status="ok")

    # ExitStack: a single parenthesised with-tuple with this many patches
    # exceeds CPython's static nested-block limit.
    from contextlib import ExitStack

    with ExitStack() as stack:
        for _cm in (
            patch.object(lw, "_settings", _routing_settings()),
            patch.object(lw, "acquire_user_lock", new=AsyncMock(return_value=True)),
            patch.object(lw, "upsert_user", new=AsyncMock()),
            patch.object(lw, "get_recent_messages", new=AsyncMock(return_value=[])),
            patch.object(lw, "build_qwen3_context", new=AsyncMock(return_value=[])),
            patch.object(lw, "is_user_auto_reply_excluded", new=AsyncMock(return_value=False)),
            patch.object(lw, "is_auto_reply_enabled", new=AsyncMock(return_value=auto_reply)),
            patch.object(lw, "add_to_operator_queue", new=AsyncMock(side_effect=_fake_add)),
            patch.object(lw, "enqueue_send", new=AsyncMock(side_effect=_fake_enqueue)),
            patch.object(lw, "extract_and_update_profile", new=AsyncMock()),
            patch.object(lw, "maybe_summarize", new=AsyncMock()),
            patch.object(lw, "post_process", new=AsyncMock()),
            patch.object(lw, "notify_operators", new=AsyncMock()),
            patch.object(lw, "_try_commerce_draft", new=AsyncMock(return_value=None)),
            patch.object(lw, "_get_canonical_commerce_evaluation",
                         new=AsyncMock(return_value=(canonical_decision, None))),
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback",
                  new=AsyncMock(return_value=one_call)),
            patch("commerce.persona_validation.validate_persona_voice",
                  new=MagicMock(return_value=persona_validation)),
            patch("context_engine.worker_integration.observe_context_engine",
                  new=AsyncMock(return_value=None)),
            patch("core.event_bus.publish_event", new=AsyncMock(side_effect=_fake_publish)),
            patch("core.event_bus.publish_events_batch", new=AsyncMock(side_effect=_fake_batch)),
            patch("db.redis.get_redis", new=AsyncMock()),
        ):
            stack.enter_context(_cm)
        if seal_setup is not None:
            for _cm in seal_setup:
                stack.enter_context(_cm)
        await lw.process_message(
            123, "hello", 456, "u", "f", "persona",
            generation_id=gid, creator_id=42,
        )
    return queue_kwargs, batch_events, published, enqueue_calls, gid


def _sealed_suppressed_patches():
    """Patch opportunity/sealing to yield a sealed-but-unhandled execution."""
    candidate = MagicMock()
    candidate.mapped_drop_ids = ("drop_xyz",)
    candidate.definition_id = 1
    opportunity = MagicMock()
    opportunity.has_opportunity = True
    opportunity.selected_candidate = candidate
    opportunity.ranking_result = MagicMock()
    seal = MagicMock()
    seal.status = "SEALED"
    seal.offer = {"id": 7}
    seal.subreason = None
    execution = MagicMock()
    execution.status = "RESERVED"
    execution.offer_id = 7
    return (
        patch("commerce.opportunity_engine.evaluate_opportunity",
              new=AsyncMock(return_value=opportunity)),
        patch("commerce.opportunity_sealing.seal_ranked_candidate",
              new=AsyncMock(return_value=seal)),
        patch("commerce.opportunity_execution.execute_sealed_offer",
              new=AsyncMock(return_value=execution)),
        patch("commerce.opportunity_execution.is_sealed_ppv_handled",
              new=MagicMock(return_value=False)),
    )


# --- Routing boundary unit proofs ------------------------------------------

def test_12a_invalid_output_never_auto_sends():
    from core.routing import RoutingAction, decide_routing

    decision = decide_routing(is_valid=False, score=0.99)
    assert decision.action is RoutingAction.QUEUE
    assert decision.reason == "invalid_output"


def test_12b_score_below_threshold_queues():
    from core.routing import RoutingAction, decide_routing

    decision = decide_routing(is_valid=True, score=0.5)
    assert decision.action is RoutingAction.QUEUE
    assert decision.reason == "below_threshold"
    # Boundary: threshold itself approves.
    assert decide_routing(is_valid=True, score=0.8).action is RoutingAction.AUTO_SEND


def test_12c_blocking_flags_queue():
    from core.routing import RoutingAction, decide_routing

    decision = decide_routing(is_valid=True, score=0.99, has_blocking_flags=True)
    assert decision.action is RoutingAction.QUEUE
    assert decision.reason == "blocking_flags"


def test_12d_safety_block_vetoes_with_reason():
    from core.routing import RoutingAction, decide_routing

    decision = decide_routing(
        is_valid=True, score=0.99, has_blocking_flags=True, safety_block=True,
    )
    assert decision.action is RoutingAction.QUEUE
    assert decision.reason == "safety_block"


def test_12e_bare_advisory_handoff_does_not_veto():
    from core.routing import RoutingAction, decide_routing

    # Bare advisory flag, all deterministic signals clean: must not veto
    # (otherwise the model could deny service at will).
    decision = decide_routing(
        is_valid=True, score=0.95, needs_handoff=True, advisory_handoff=True,
    )
    assert decision.action is RoutingAction.AUTO_SEND
    assert decision.reason == "approved"
    assert decision.advisory_handoff is True
    assert decision.corroborated_handoff is False


def test_12f_deterministic_only_handoff_queues():
    from core.routing import RoutingAction, decide_routing

    decision = decide_routing(
        is_valid=True, score=0.95, needs_handoff=True, advisory_handoff=False,
    )
    assert decision.action is RoutingAction.QUEUE
    assert decision.reason == "corroborated_handoff"
    assert decision.corroborated_handoff is True


def test_12g_commerce_handoff_and_deny_queue():
    from core.routing import RoutingAction, decide_routing

    assert decide_routing(
        is_valid=True, score=0.99, commerce_handoff_required=True,
    ).reason == "commerce_handoff_required"
    assert decide_routing(
        is_valid=True, score=0.99, commerce_handoff_required=True,
    ).action is RoutingAction.QUEUE
    assert decide_routing(
        is_valid=True, score=0.99, commerce_deny=True,
    ).reason == "commerce_deny"
    assert decide_routing(
        is_valid=True, score=0.99, sealed_suppressed=True,
    ).reason == "sealed_suppressed"


def test_12h_veto_precedence_is_fail_closed():
    from core.routing import decide_routing

    # Invalid beats commerce/auto-reply/threshold signals.
    assert decide_routing(
        is_valid=False, score=0.99, commerce_deny=True, auto_reply_on=False,
    ).reason == "invalid_output"
    # Commerce deny beats auto-reply-off and threshold.
    assert decide_routing(
        is_valid=True, score=0.1, commerce_deny=True,
    ).reason == "commerce_deny"
    # Invalid beats excluded too: untrusted output always demands operator
    # eyes (queue != send). Unreachable in the worker anyway — exclusion is
    # checked before any generation runs.
    assert decide_routing(is_valid=False, excluded=True).reason == "invalid_output"
    # Excluded (with otherwise usable output) suppresses: never sends,
    # never queues.
    from core.routing import RoutingAction

    excluded = decide_routing(is_valid=True, excluded=True)
    assert excluded.action is RoutingAction.SUPPRESS
    assert excluded.reason == "excluded"


# --- Validation-layer proofs -------------------------------------------------

def test_12i_malformed_json_is_invalid_with_handoff():
    from core.one_call import validate_one_call_response

    result = validate_one_call_response("{not json")
    assert result.is_valid is False
    assert result.needs_handoff is True
    assert result.advisory_handoff is False
    assert result.reply == ""


def test_12j_schema_invalid_output_is_invalid():
    from core.one_call import validate_one_call_response

    # Missing required reply; extra field forbidden.
    for raw in ('{"confidence": 0.9}', '{"reply": "hi", "bogus": 1}'):
        result = validate_one_call_response(raw)
        assert result.is_valid is False
        assert result.needs_handoff is True
        assert result.advisory_handoff is False


def test_12k_advisory_flag_survives_validation():
    from core.one_call import validate_one_call_response

    result = validate_one_call_response(
        '{"reply": "hello", "confidence": 0.9, "needs_handoff": true}'
    )
    assert result.is_valid is True
    assert result.needs_handoff is True
    assert result.advisory_handoff is True


# --- Worker routing proofs (OneCall path) -------------------------------------

@pytest.mark.asyncio
async def test_12l_invalid_onecall_never_sends():
    queue_kwargs, batch, published, enqueue_calls, gid = await _drive_worker_new_path(
        one_call=_one_call_result(
            reply="", score=0.0, is_valid=False,
            validation_error="Schema validation failed: bad",
        ),
    )
    assert enqueue_calls == []
    assert queue_kwargs["creator_id"] == 42
    assert queue_kwargs["generation_id"] == gid
    assert queue_kwargs["flags"] == ["one_call_invalid_result"]
    completed = [e for e in published if e["event_type"] == "ai.generation_completed"]
    assert completed and completed[0]["generation_id"] == gid
    assert completed[0]["creator_id"] == 42


@pytest.mark.asyncio
async def test_12m_bare_advisory_handoff_sends_and_is_recorded():
    queue_kwargs, batch, published, enqueue_calls, gid = await _drive_worker_new_path(
        one_call=_one_call_result(score=0.95, needs_handoff=True, advisory_handoff=True),
    )
    # Advisory alone must not veto: the clean turn still auto-sends...
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["payload"]["generation_id"] == gid
    assert enqueue_calls[0]["creator_id"] == 42
    # ...but the signal is no longer silently ignored: it is recorded.
    started = [e for e in published if e["event_type"] == "ai.generation_started"]
    assert started and started[0]["generation_id"] == gid
    completed = [e for e in batch if e["event"] == "ai.generation_completed"]
    assert len(completed) == 1
    assert completed[0]["data"]["advisory_handoff"] is True
    assert completed[0]["data"]["routing_reason"] == "approved"
    assert completed[0]["creator_id"] == 42
    assert completed[0]["generation_id"] == gid
    assert queue_kwargs == {}


@pytest.mark.asyncio
async def test_12n_deterministic_handoff_queues_without_send():
    queue_kwargs, batch, published, enqueue_calls, gid = await _drive_worker_new_path(
        one_call=_one_call_result(score=0.95, needs_handoff=True, advisory_handoff=False),
    )
    assert enqueue_calls == []
    assert queue_kwargs["creator_id"] == 42
    assert queue_kwargs["generation_id"] == gid
    completed = [e for e in batch if e["event"] == "ai.generation_completed"]
    assert completed and completed[0]["data"]["routing_reason"] == "corroborated_handoff"


@pytest.mark.asyncio
async def test_12o_low_score_and_flags_queue_on_new_path():
    queue_kwargs, batch, published, enqueue_calls, gid = await _drive_worker_new_path(
        one_call=_one_call_result(score=0.5),
    )
    assert enqueue_calls == []
    assert queue_kwargs["generation_id"] == gid
    completed = [e for e in batch if e["event"] == "ai.generation_completed"]
    assert completed[0]["data"]["routing_reason"] == "below_threshold"

    queue_kwargs, batch, published, enqueue_calls, gid = await _drive_worker_new_path(
        one_call=_one_call_result(score=0.95, quality_flags=["too_generic"]),
    )
    # Phase 1.4: info-tier flags are penalty-only — high score still sends.
    assert len(enqueue_calls) == 1
    completed = [e for e in batch if e["event"] == "ai.generation_completed"]
    assert completed[0]["data"]["routing_reason"] == "approved"

    queue_kwargs, batch, published, enqueue_calls, gid = await _drive_worker_new_path(
        one_call=_one_call_result(score=0.95, quality_flags=["prompt_echo"]),
    )
    # Hard-tier flags still veto at any score.
    assert enqueue_calls == []
    completed = [e for e in batch if e["event"] == "ai.generation_completed"]
    assert completed[0]["data"]["routing_reason"] == "blocking_flags"


@pytest.mark.asyncio
async def test_12p_safety_flags_veto_send():
    queue_kwargs, batch, published, enqueue_calls, gid = await _drive_worker_new_path(
        one_call=_one_call_result(score=0.95, safety_flags=["price_mention"]),
    )
    assert enqueue_calls == []
    completed = [e for e in batch if e["event"] == "ai.generation_completed"]
    assert completed[0]["data"]["routing_reason"] == "safety_block"
    assert completed[0]["creator_id"] == 42
    assert completed[0]["generation_id"] == gid


@pytest.mark.asyncio
async def test_12q_canonical_commerce_handoff_queues():
    from commerce.decision import CommerceDecision
    from commerce.models import CommerceAction

    decision = CommerceDecision(
        action=CommerceAction.OPERATOR_HANDOFF,
        reason_code=__import__("commerce.decision", fromlist=["CommerceReason"]).CommerceReason.OPERATOR_HANDOFF_NEEDED,
        allowed=False,
        confidence=1.0,
    )
    queue_kwargs, batch, published, enqueue_calls, gid = await _drive_worker_new_path(
        one_call=_one_call_result(score=0.95),
        canonical_decision=decision,
    )
    assert enqueue_calls == []
    assert queue_kwargs["creator_id"] == 42
    assert queue_kwargs["generation_id"] == gid
    completed = [e for e in batch if e["event"] == "ai.generation_completed"]
    assert completed[0]["data"]["routing_reason"] == "commerce_handoff_required"


@pytest.mark.asyncio
async def test_12r_sealed_but_unhandled_queues():
    queue_kwargs, batch, published, enqueue_calls, gid = await _drive_worker_new_path(
        one_call=_one_call_result(score=0.95),
        seal_setup=_sealed_suppressed_patches(),
    )
    # A sealed action exists for this turn but never reached the send
    # stream: ordinary auto-send must not race it.
    assert enqueue_calls == []
    assert queue_kwargs["creator_id"] == 42
    assert queue_kwargs["generation_id"] == gid
    completed = [e for e in batch if e["event"] == "ai.generation_completed"]
    assert completed[0]["data"]["routing_reason"] == "sealed_suppressed"


# --- H6 excluded-user observability --------------------------------------------

@pytest.mark.asyncio
async def test_12s_excluded_user_produces_suppressed_outcome():
    import workers.llm_worker as lw
    from core.generation import telegram_generation_id

    gid = telegram_generation_id(123, "hello", 456)
    published = []
    enqueue_calls = []
    queue_calls = []

    async def _fake_publish(event_type, data, **kwargs):
        published.append({"event_type": event_type, "data": data, **kwargs})

    with (
        patch.object(lw, "acquire_user_lock", new=AsyncMock(return_value=True)),
        patch.object(lw, "upsert_user", new=AsyncMock()),
        patch.object(lw, "is_user_auto_reply_excluded", new=AsyncMock(return_value=True)),
        patch.object(lw, "enqueue_send", new=AsyncMock(
            side_effect=lambda *a, **k: enqueue_calls.append((a, k)) or "send-1")),
        patch.object(lw, "add_to_operator_queue", new=AsyncMock(
            side_effect=lambda *a, **k: queue_calls.append((a, k)) or 99)),
        patch("core.event_bus.publish_event", new=AsyncMock(side_effect=_fake_publish)),
        patch("core.event_bus.publish_events_batch", new=AsyncMock()),
        patch("db.redis.get_redis", new=AsyncMock()),
    ):
        await lw.process_message(
            123, "hello", 456, "u", "f", "persona",
            generation_id=gid, creator_id=42,
        )

    # No send, no queue — but the lifecycle is observable now.
    assert enqueue_calls == []
    assert queue_calls == []
    started = [e for e in published if e["event_type"] == "ai.generation_started"]
    completed = [e for e in published if e["event_type"] == "ai.generation_completed"]
    assert len(started) == 1
    assert len(completed) == 1
    assert completed[0]["data"]["suppressed"] is True
    assert completed[0]["data"]["suppression_reason"] == "do_not_auto_reply"
    assert completed[0]["data"]["was_auto_approved"] is False
    assert completed[0]["creator_id"] == 42
    assert completed[0]["generation_id"] == gid
    assert started[0]["creator_id"] == 42
    assert started[0]["generation_id"] == gid


@pytest.mark.asyncio
async def test_12t_legacy_empty_draft_never_sends():
    import workers.llm_worker as lw
    from core.generation import telegram_generation_id

    gid = telegram_generation_id(123, "hello", 456)
    batch_events = []
    enqueue_calls = []

    async def _fake_batch(events):
        batch_events.extend(events)

    async def _fake_enqueue(payload=None, **kwargs):
        enqueue_calls.append({"payload": dict(payload or {}), **kwargs})
        return "send-1"

    with (
        patch.object(lw._settings, "llm_path", "legacy"),
        patch.object(lw, "upsert_user", new=AsyncMock()),
        patch.object(lw, "get_recent_messages", new=AsyncMock(return_value=[])),
        patch.object(lw, "build_qwen3_context", new=AsyncMock(return_value=[])),
        patch.object(lw, "is_user_auto_reply_excluded", new=AsyncMock(return_value=False)),
        patch.object(lw, "is_auto_reply_enabled", new=AsyncMock(return_value=True)),
        patch.object(lw, "add_to_operator_queue", new=AsyncMock(return_value=99)),
        patch.object(lw, "acquire_user_lock", new=AsyncMock(return_value=True)),
        patch.object(lw, "generate_draft", new=AsyncMock(return_value="")),
        patch.object(lw, "score_draft", new=AsyncMock(return_value=(0.9, []))),
        patch.object(lw, "extract_and_update_profile", new=AsyncMock()),
        patch.object(lw, "maybe_summarize", new=AsyncMock()),
        patch.object(lw, "post_process", new=AsyncMock()),
        patch.object(lw, "enqueue_send", new=AsyncMock(side_effect=_fake_enqueue)),
        patch("core.event_bus.publish_event", new=AsyncMock()),
        patch("core.event_bus.publish_events_batch", new=AsyncMock(side_effect=_fake_batch)),
        patch("db.redis.get_redis", new=AsyncMock()),
    ):
        await lw.process_message(
            123, "hello", 456, "u", "f", "persona",
            generation_id=gid, creator_id=42,
        )
    # High score and no flags, but no valid generation: must still queue.
    assert enqueue_calls == []
    completed = [e for e in batch_events if e["event"] == "ai.generation_completed"]
    assert completed and completed[0]["data"]["routing_reason"] == "invalid_output"
    assert completed[0]["creator_id"] == 42
    assert completed[0]["generation_id"] == gid
