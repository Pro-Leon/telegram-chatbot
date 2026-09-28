"""M7 — reliability / auditability / integrity tests.

Hermetic: no live services. Real route/worker/contract functions run against
minimal fakes for get_pool / enqueue / events / resolver so the M7 audit
contract, actor propagation, queue flush/edit race narrowing, scheduler
lifecycle, vault guards, lab boundary, and creator-scope hardening are
exercised without Postgres, Redis, or Telegram.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# B1 — audit event contract
# ---------------------------------------------------------------------------


class TestAuditContract:
    def test_envelope_requires_known_event_type(self):
        from core.audit import build_audit_event

        with pytest.raises(ValueError):
            build_audit_event(
                event_type="teleport",
                actor_type="human",
                actor_id="op",
                creator_id=1,
                action="test",
            )

    def test_envelope_rejects_unknown_actor_type(self):
        from core.audit import build_audit_event

        with pytest.raises(ValueError):
            build_audit_event(
                event_type="enqueue",
                actor_type="superuser",
                actor_id="x",
                creator_id=1,
                action="test",
            )

    def test_envelope_requires_creator_and_action(self):
        from core.audit import build_audit_event

        with pytest.raises(ValueError):
            build_audit_event(
                event_type="enqueue", actor_type="human", actor_id="op",
                creator_id=None, action="test",
            )
        with pytest.raises(ValueError):
            build_audit_event(
                event_type="enqueue", actor_type="human", actor_id="op",
                creator_id=1, action="  ",
            )

    def test_content_hashed_never_stored_raw(self):
        from core.audit import build_audit_event

        ev = build_audit_event(
            event_type="enqueue",
            actor_type="human",
            actor_id="op",
            creator_id=3,
            user_id=9,
            action="POST /api/send-message",
            content="hello secret",
            content_original="old",
            generation_id="manual:x",
            dedup_id="d1",
            queue_id=4,
        )
        assert ev["content_hash"] == hashlib.sha256(b"hello secret").hexdigest()
        assert ev["content_hash_original"] == hashlib.sha256(b"old").hexdigest()
        assert "hello secret" not in str(ev.values())
        for banned in ("content", "caption", "media_path"):
            assert banned not in ev

    def test_correlation_and_lifecycle_fields(self):
        from core.audit import build_audit_event

        ev = build_audit_event(
            event_type="approve",
            actor_type="worker",
            actor_id="send_1",
            creator_id=1,
            action="send_worker.flush_queue",
            generation_id="g",
            dedup_id="queue_item:8",
            queue_id=8,
            schedule_id=None,
            state_before="pending",
            state_after="approved",
            result="approved",
            attempt=2,
            lease_token="tok",
            telegram_message_id=123,
        )
        assert ev["queue_id"] == 8
        assert ev["lease_token"] == "tok"
        assert ev["attempt"] == 2
        assert ev["telegram_message_id"] == 123
        assert ev["audit_id"]

    def test_short_hash_matches_m6_dedup_convention(self):
        from core.audit import short_hash

        assert short_hash("abc") == hashlib.sha256(b"abc").hexdigest()[:16]

    @pytest.mark.asyncio
    async def test_record_audit_failure_isolation(self):
        """Persistence failure returns False and never raises."""
        from core import audit as audit_mod

        ev = audit_mod.build_audit_event(
            event_type="enqueue", actor_type="human", actor_id="op",
            creator_id=1, action="test", content="x",
        )
        with patch(
            "db.postgres.get_pool", new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ):
            assert await audit_mod.record_audit(ev) is False

    @pytest.mark.asyncio
    async def test_record_audit_event_construction_failure_never_raises(self):
        from core.audit import record_audit_event

        assert (
            await record_audit_event(
                event_type="nope", actor_type="human", creator_id=1, action="t"
            )
            is False
        )


# ---------------------------------------------------------------------------
# B5 — actor identity
# ---------------------------------------------------------------------------


class TestActorIdentity:
    def test_actor_from_auth_human(self):
        from core.audit import actor_from_auth

        assert actor_from_auth({"username": "op1"}) == {
            "actor_type": "human", "actor_id": "op1",
        }

    def test_actor_from_auth_missing_is_explicit_unknown(self):
        from core.audit import actor_from_auth

        assert actor_from_auth({})["actor_type"] == "unknown"
        assert actor_from_auth(None)["actor_type"] == "unknown"

    def test_system_scheduler_worker_ai_actors(self):
        from core.audit import (
            actor_ai,
            actor_scheduler,
            actor_system,
            actor_worker,
        )

        assert actor_worker("w1")["actor_type"] == "worker"
        assert actor_scheduler("s1")["actor_type"] == "scheduler"
        assert actor_ai()["actor_type"] == "ai"
        assert actor_system("x")["actor_type"] == "system"

    def test_strip_spoofed_actor(self):
        from core.audit import strip_spoofed_actor

        payload = {"entity": "5", "actor_type": "human", "actor_id": "root"}
        cleaned, spoofed = strip_spoofed_actor(dict(payload))
        assert spoofed is True
        assert "actor_type" not in cleaned
        assert cleaned["entity"] == "5"
        _, clean = strip_spoofed_actor({"entity": "5"})
        assert clean is False

    def test_worker_cannot_mint_human_identity(self):
        """Consumer flow: strip inbound actor keys, overwrite with trusted actor."""
        from core.audit import actor_worker, strip_spoofed_actor, validate_actor

        inbound = {"entity": "5", "actor_type": "human", "actor_id": "admin"}
        _cleaned, spoofed = strip_spoofed_actor(dict(inbound))
        assert spoofed is True
        trusted = actor_worker("send_1")
        assert validate_actor(trusted["actor_type"], trusted["actor_id"]) == trusted
        assert trusted["actor_type"] != "human"


# ---------------------------------------------------------------------------
# Helpers: fake DB pool / resolver
# ---------------------------------------------------------------------------


def _ready_ctx(creator_id=42):
    return SimpleNamespace(status=MagicMock(), creator_id=creator_id)


def _patch_ready_creator(monkeypatch=None, creator_id=42):
    from commerce import single_creator as sc

    ctx = SimpleNamespace(status=sc.SingleCreatorStatus.READY, creator_id=creator_id)
    return patch.object(sc, "resolve_single_application_creator", new=AsyncMock(return_value=ctx))


class FakeConn:
    """Minimal asyncpg-like fake honoring creator predicates + audit table."""

    def __init__(self, store: dict, log: list):
        self._s = store
        self._log = log

    async def fetchrow(self, sql: str, *params):
        self._log.append(("fetchrow", sql, params))
        if "FROM operator_audit_events" in sql or "INTO operator_audit_events" in sql:
            return None
        if "FROM operator_queue" in sql:
            row = self._s["queue"].get(params[0])
            if row is None:
                return None
            if "q.creator_id = $2" in sql and int(row.get("creator_id") or -1) != int(params[1]):
                return None
            if "status = 'pending'" in sql and row.get("status") != "pending":
                return None
            return dict(row)
        return None

    async def fetch(self, sql: str, *params):
        self._log.append(("fetch", sql, params))
        return []

    async def fetchval(self, sql: str, *params):
        self._log.append(("fetchval", sql, params))
        if "FROM scheduled_messages" in sql and "SELECT status" in sql:
            return self._s.get("sched_status", "processing")
        return None

    async def execute(self, sql: str, *params):
        self._log.append(("execute", sql, params))
        if "INTO operator_audit_events" in sql:
            self._s.setdefault("audits", []).append({"sql": sql, "params": params})
            return "INSERT 0 1"
        if "UPDATE operator_queue SET" in sql:
            return "UPDATE 1"
        if "UPDATE scheduled_messages" in sql:
            wanted = "completed" in sql or "failed" in sql
            if self._s.get("mark_ok", True) and wanted:
                return "UPDATE 1"
            return "UPDATE 0"
        return "OK"


class FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, store, log):
        self._conn = FakeConn(store, log)

    def acquire(self):
        return FakeAcquire(self._conn)


# ---------------------------------------------------------------------------
# B3 — flush/edit race
# ---------------------------------------------------------------------------


def _queue_row(**over):
    row = {
        "id": 7, "user_id": 11, "creator_id": 42, "status": "pending",
        "draft_content": "hello", "confidence_score": 0.9, "edited": False,
        "assigned_to": None, "resolved_by": "op1", "generation_id": None,
    }
    row.update(over)
    return row


class TestFlushRace:
    @pytest.mark.asyncio
    async def test_fresh_match_enqueues_authoritative_bytes(self):
        """Snapshot == re-read → single enqueue of authoritative bytes."""
        import workers.send_worker as sw

        row = _queue_row()
        store = {"queue": {7: dict(row)}, "audits": []}
        fake_pool = FakePool(store, [])
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=fake_pool)), \
             patch("workers.send_worker.get_pending_queue_items", new=AsyncMock(return_value=[dict(row)])), \
             patch.object(sw, "enqueue_send", new=AsyncMock(return_value="s1")) as mock_enq, \
             patch("core.event_bus.publish_event", new=AsyncMock(return_value="e1")):
            orig_fetch = FakeConn.fetch

            async def _fetch_with_creators(self, sql, *params):
                if "FROM creator_integrations" in sql:
                    return [{"creator_id": 42}]
                return await orig_fetch(self, sql, *params)

            with patch.object(FakeConn, "fetch", _fetch_with_creators):
                sent = await sw.flush_queue(max_items=50)
        assert sent == 1
        mock_enq.assert_awaited_once()
        kinds = [a["params"][1] for a in store["audits"]]
        assert "enqueue" in kinds and "approve" in kinds

    @pytest.mark.asyncio
    async def test_stale_snapshot_suppressed_no_enqueue(self):
        """Snapshot hello vs fresh edited bytes → stale_suppressed, no XADD."""
        import workers.send_worker as sw

        snapshot = _queue_row()  # draft "hello", edited False
        fresh = _queue_row(draft_content="hello EDITED", edited=True, final_content="hello EDITED")
        store = {"queue": {7: fresh}, "audits": []}
        log: list = []
        fake_pool = FakePool(store, log)
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=fake_pool)), \
             patch("workers.send_worker.get_pending_queue_items", new=AsyncMock(return_value=[snapshot])), \
             patch.object(sw, "enqueue_send", new=AsyncMock()) as mock_enq, \
             patch("db.postgres.get_pool", new=AsyncMock(return_value=fake_pool)):
            # creator enumeration query runs on db.postgres pool too; make the
            # fetch for creator_integrations return our creator via FakeConn? The
            # flush reads creator ids with conn.fetch — extend FakeConn inline.
            orig_fetch = FakeConn.fetch

            async def _fetch_with_creators(self, sql, *params):
                if "FROM creator_integrations" in sql:
                    return [{"creator_id": 42}]
                return await orig_fetch(self, sql, *params)

            with patch.object(FakeConn, "fetch", _fetch_with_creators):
                sent = await sw.flush_queue(max_items=50)
        assert sent == 0
        mock_enq.assert_not_awaited()
        audit_types = [a["params"][1] for a in store["audits"]]
        assert "stale_suppressed" in audit_types

    @pytest.mark.asyncio
    async def test_resolved_elsewhere_skips_without_enqueue(self):
        import workers.send_worker as sw

        snapshot = _queue_row()
        store = {"queue": {}, "audits": []}  # fresh re-read finds nothing
        log: list = []
        fake_pool = FakePool(store, log)
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=fake_pool)), \
             patch("workers.send_worker.get_pending_queue_items", new=AsyncMock(return_value=[snapshot])), \
             patch.object(sw, "enqueue_send", new=AsyncMock()) as mock_enq:
            orig_fetch = FakeConn.fetch

            async def _fetch_with_creators(self, sql, *params):
                if "FROM creator_integrations" in sql:
                    return [{"creator_id": 42}]
                return await orig_fetch(self, sql, *params)

            with patch.object(FakeConn, "fetch", _fetch_with_creators):
                sent = await sw.flush_queue(max_items=50)
        assert sent == 0
        mock_enq.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_edited_flush_dedup_is_content_bound(self):
        from workers import send_worker as sw

        captured = {}

        async def _fake_enq(payload, dedup_id=None, generation_id=None, creator_id=None):
            captured["dedup_id"] = dedup_id
            captured["payload"] = payload
            return "x"

        with patch.object(sw, "enqueue_send", new=_fake_enq):
            res = await sw.process_approved_message(
                user_id=11, content="edited bytes", confidence_score=0.99,
                queue_id=9, creator_id=42, was_edited=True,
                actor_type="human", actor_id="op1",
            )
        assert res and res["ok"]
        expect = "queue_item:9:" + hashlib.sha256(b"edited bytes").hexdigest()[:16]
        assert captured["dedup_id"] == expect
        assert captured["payload"]["was_auto_approved"] is False
        assert captured["payload"]["actor_type"] == "human"

    @pytest.mark.asyncio
    async def test_unedited_flush_keeps_legacy_dedup(self):
        from workers import send_worker as sw

        captured = {}

        async def _fake_enq(payload, dedup_id=None, generation_id=None, creator_id=None):
            captured["dedup_id"] = dedup_id
            captured["payload"] = payload
            return "x"

        with patch.object(sw, "enqueue_send", new=_fake_enq):
            res = await sw.process_approved_message(
                user_id=11, content="plain", confidence_score=0.1,
                queue_id=9, creator_id=42, was_edited=False,
            )
        assert res and res["ok"]
        assert captured["dedup_id"] == "queue_item:9"

    @pytest.mark.asyncio
    async def test_identical_retry_shares_identity(self):
        from workers import send_worker as sw

        seen = []

        async def _fake_enq(payload, dedup_id=None, generation_id=None, creator_id=None):
            seen.append(dedup_id)
            return "x"

        with patch.object(sw, "enqueue_send", new=_fake_enq):
            for _ in range(2):
                await sw.process_approved_message(
                    user_id=11, content="same edit", confidence_score=0.5,
                    queue_id=9, creator_id=42, was_edited=True,
                )
        assert seen[0] == seen[1]

    @pytest.mark.asyncio
    async def test_divergent_content_not_suppressed(self):
        from workers import send_worker as sw

        seen = []

        async def _fake_enq(payload, dedup_id=None, generation_id=None, creator_id=None):
            seen.append(dedup_id)
            return "x"

        with patch.object(sw, "enqueue_send", new=_fake_enq):
            await sw.process_approved_message(
                user_id=11, content="v1", queue_id=9, creator_id=42, was_edited=True)
            await sw.process_approved_message(
                user_id=11, content="v2", queue_id=9, creator_id=42, was_edited=True)
        assert seen[0] != seen[1]


# ---------------------------------------------------------------------------
# B4 — scheduler lifecycle
# ---------------------------------------------------------------------------


def _sched_row(**over):
    row = {
        "id": 21, "user_id": 11, "creator_id": 42, "status": "processing",
        "content": "hi", "media_type": "", "media_path": "", "dedup_key": "k1",
        "reason": "", "generation_id": None, "attempts": 1, "claimed_by": "sched_1",
    }
    row.update(over)
    return row


class TestSchedulerLifecycle:
    @pytest.mark.asyncio
    async def test_cancel_before_claim_skips_send(self):
        import workers.scheduler_worker as sch

        store = {"sched_status": "processing", "audits": []}
        log: list = []
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=FakePool(store, log))), \
             patch.object(sch, "claim_due_messages", new=AsyncMock(return_value=[{"id": 21}])), \
             patch.object(sch, "_fetch_message", new=AsyncMock(return_value=_sched_row(status="cancelled"))), \
             patch.object(sch, "enqueue_send", new=AsyncMock()) as mock_enq:
            n = await sch.process_due_messages("sched_1")
        assert n == 0
        mock_enq.assert_not_awaited()
        assert any(a["params"][1] == "cancel" for a in store["audits"])

    @pytest.mark.asyncio
    async def test_ineligible_recipient_suppressed_not_completed_silently(self):
        import workers.scheduler_worker as sch

        store = {"sched_status": "processing", "audits": []}
        log: list = []
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=FakePool(store, log))), \
             patch.object(sch, "claim_due_messages", new=AsyncMock(return_value=[{"id": 21}])), \
             patch.object(sch, "_fetch_message", new=AsyncMock(return_value=_sched_row())), \
             patch.object(sch, "_check_user_eligible", new=AsyncMock(return_value=False)), \
             patch.object(sch, "enqueue_send", new=AsyncMock()) as mock_enq:
            n = await sch.process_due_messages("sched_1")
        assert n == 0
        mock_enq.assert_not_awaited()
        assert any(a["params"][1] == "suppress" for a in store["audits"])
        assert any("suppressed:recipient_ineligible" in str(entry) for entry in log)

    @pytest.mark.asyncio
    async def test_cancel_race_after_enqueue_is_explicit(self):
        import workers.scheduler_worker as sch

        store = {"sched_status": "cancelled", "mark_ok": False, "audits": []}
        log: list = []
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=FakePool(store, log))), \
             patch.object(sch, "claim_due_messages", new=AsyncMock(return_value=[{"id": 21}])), \
             patch.object(sch, "_fetch_message", new=AsyncMock(return_value=_sched_row())), \
             patch.object(sch, "_check_user_eligible", new=AsyncMock(return_value=True)), \
             patch.object(sch, "enqueue_send", new=AsyncMock(return_value="x")):
            n = await sch.process_due_messages("sched_1")
        # send happened; race audited; counted once; no duplicate enqueue
        assert n == 1
        assert any(a["params"][1] == "cancel_raced" for a in store["audits"])

    @pytest.mark.asyncio
    async def test_duplicate_claim_ids_share_stable_dedup(self):
        import workers.scheduler_worker as sch

        m = _sched_row()
        assert sch._make_dedup_id(m) == sch._make_dedup_id(dict(m))

    @pytest.mark.asyncio
    async def test_mark_suppressed_uses_completed_plus_reason(self):
        import inspect

        import db.postgres as pg

        src = inspect.getsource(pg.mark_scheduled_suppressed)
        assert "completed" in src
        assert "suppressed:" in src
        assert "status = 'processing'" in src


# ---------------------------------------------------------------------------
# B2 — direct-send audit coverage
# ---------------------------------------------------------------------------


class TestDirectSendAudit:
    @pytest.mark.asyncio
    async def test_send_message_emits_intent_and_enqueue(self):
        from chatbotv2.dashboard.routes import messages as msg_routes
        from chatbotv2.dashboard.schemas import SendMessageRequest

        audits: list = []

        async def _fake_audit(**kwargs):
            audits.append(kwargs)
            return True

        enqueued: list = []

        async def _fake_enq(payload, dedup_id=None, generation_id=None, creator_id=None):
            enqueued.append((payload, dedup_id))
            return "x"

        with _patch_ready_creator(creator_id=42), \
             patch("core.audit.record_audit_event", new=_fake_audit), \
             patch.object(msg_routes, "enqueue_send", new=_fake_enq):
            resp = await msg_routes.api_send_message(
                SendMessageRequest(user_id=11, content="hi"), {"username": "op1"}
            )
        assert resp.status_code == 200
        kinds = [a["event_type"] for a in audits]
        assert "intent" in kinds and "enqueue" in kinds
        assert enqueued[0][0]["actor_type"] == "human"
        assert enqueued[0][0]["actor_id"] == "op1"
        assert audits[0]["creator_id"] == 42

    @pytest.mark.asyncio
    async def test_send_message_failure_audited(self):
        from chatbotv2.dashboard.routes import messages as msg_routes
        from chatbotv2.dashboard.schemas import SendMessageRequest

        audits: list = []

        async def _fake_audit(**kwargs):
            audits.append(kwargs)
            return True

        async def _boom(*a, **k):
            raise RuntimeError("redis down")

        with _patch_ready_creator(creator_id=42), \
             patch("core.audit.record_audit_event", new=_fake_audit), \
             patch.object(msg_routes, "enqueue_send", new=_boom):
            from fastapi import HTTPException

            with pytest.raises(HTTPException):
                await msg_routes.api_send_message(
                    SendMessageRequest(user_id=11, content="hi"), {"username": "op1"}
                )
        assert any(a["event_type"] == "failure" for a in audits)


# ---------------------------------------------------------------------------
# B8 — vault guards
# ---------------------------------------------------------------------------


class TestVaultGuards:
    def _req(self, body: dict):
        req = MagicMock()
        req.json = AsyncMock(return_value=body)
        return req

    @pytest.mark.asyncio
    async def test_verified_asset_enqueues_with_snapshot(self):
        from chatbotv2.dashboard.routes import vault as vault_routes

        item = SimpleNamespace(media_id=5, product_id=9, media_type="photo")
        audits: list = []
        payloads: list = []

        async def _fake_audit(**kwargs):
            audits.append(kwargs)
            return True

        async def _fake_enq(payload, dedup_id=None, generation_id=None, creator_id=None):
            payloads.append(payload)
            return "x"

        with patch.object(vault_routes, "_require_creator_id", new=AsyncMock(return_value=42)), \
             patch.object(vault_routes.vault_svc, "get_media", new=AsyncMock(return_value=item)), \
             patch("core.audit.record_audit_event", new=_fake_audit), \
             patch.object(vault_routes, "enqueue_send", new=_fake_enq):
            resp = await vault_routes.send_vault_media(
                self._req({"user_id": 11, "media_type": "photo",
                           "media_path": "https://x/y.jpg", "fangate_media_id": 5,
                           "product_id": 9, "caption": "cap"}),
                {"username": "op1"},
            )
        assert resp == {"ok": True, "queued": True}
        assert payloads[0]["asset_hash"]
        assert payloads[0]["asset_verified"] == "verified"
        assert payloads[0]["actor_type"] == "human"

    @pytest.mark.asyncio
    async def test_product_mismatch_rejected(self):
        from chatbotv2.dashboard.routes import vault as vault_routes

        item = SimpleNamespace(media_id=5, product_id=9, media_type="photo")
        with patch.object(vault_routes, "_require_creator_id", new=AsyncMock(return_value=42)), \
             patch.object(vault_routes.vault_svc, "get_media", new=AsyncMock(return_value=item)), \
             patch("core.audit.record_audit_event", new=AsyncMock(return_value=True)), \
             patch.object(vault_routes, "enqueue_send", new=AsyncMock()):
            resp = await vault_routes.send_vault_media(
                self._req({"user_id": 11, "media_type": "photo",
                           "media_path": "https://x/y.jpg", "fangate_media_id": 5,
                           "product_id": 10, "caption": ""}),
                {"username": "op1"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_mirror_miss_still_enqueues_with_unverified_audit(self):
        from chatbotv2.dashboard.routes import vault as vault_routes

        audits: list = []

        async def _fake_audit(**kwargs):
            audits.append(kwargs)
            return True

        with patch.object(vault_routes, "_require_creator_id", new=AsyncMock(return_value=42)), \
             patch.object(vault_routes.vault_svc, "get_media", new=AsyncMock(return_value=None)), \
             patch("core.audit.record_audit_event", new=_fake_audit), \
             patch.object(vault_routes, "enqueue_send", new=AsyncMock(return_value="x")):
            resp = await vault_routes.send_vault_media(
                self._req({"user_id": 11, "media_type": "photo",
                           "media_path": "https://x/y.jpg", "fangate_media_id": 5,
                           "caption": ""}),
                {"username": "op1"},
            )
        assert resp == {"ok": True, "queued": True}
        assert any("mirror-miss" in str(a.get("result")) for a in audits)

    @pytest.mark.asyncio
    async def test_finalize_release_scoped_sql(self):
        import inspect

        import db.vault as vdb

        assert "creator_id = $3" in inspect.getsource(vdb.finalize_delivery)
        assert "creator_id = $2" in inspect.getsource(vdb.release_delivery)


# ---------------------------------------------------------------------------
# B7 — lab boundary
# ---------------------------------------------------------------------------


class TestLabBoundary:
    @pytest.mark.asyncio
    async def test_foreign_persona_rejected(self):
        from chatbotv2.dashboard.routes import lab as lab_routes

        store_rows = {"persona": None}  # lookup finds nothing (foreign/missing)

        class _Conn:
            async def fetchrow(self, sql, *params):
                assert "creator_id" in sql  # scoped predicate present
                return store_rows["persona"]

        class _Acq:
            async def __aenter__(self):
                return _Conn()

            async def __aexit__(self, *exc):
                return False

        class _Pool:
            def acquire(self):
                return _Acq()

        with _patch_ready_creator(creator_id=42), \
             patch("db.postgres.get_pool", new=AsyncMock(return_value=_Pool())):
            resp = await lab_routes.api_lab_execute(
                {"creator_id": 42, "persona_id": 777, "fan_message": "hi"},
                {"username": "op1"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_creator_mismatch_rejected(self):
        from chatbotv2.dashboard.routes import lab as lab_routes

        with _patch_ready_creator(creator_id=42):
            resp = await lab_routes.api_lab_execute(
                {"creator_id": 43, "fan_message": "hi"}, {"username": "op1"}
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# B6 — creator-scope hardening
# ---------------------------------------------------------------------------


class TestCreatorScope:
    def test_pages_queue_uses_creator(self):
        import inspect

        from chatbotv2.dashboard.routes import pages as pages_routes

        src = inspect.getsource(pages_routes.dashboard_queue)
        assert "creator_id" in src

    def test_pages_chats_scoped(self):
        import inspect

        from chatbotv2.dashboard.routes import pages as pages_routes

        src = inspect.getsource(pages_routes.dashboard_chats)
        assert src.count("creator_id") >= 3

    def test_ai_intel_scoped(self):
        import inspect

        from chatbotv2.dashboard.routes import ai_intel as ai_routes

        src = inspect.getsource(ai_routes.api_dialog_ai_intel)
        assert "creator_id = $2" in src

    def test_dialogs_unsegmented_scoped(self):
        import inspect

        from chatbotv2.dashboard.routes import dialogs as dialogs_routes

        src = inspect.getsource(dialogs_routes.api_dialogs)
        assert "creator_id" in src

    def test_segments_message_queue_scoped_multirule(self):
        from segments.evaluator import compile_rule
        from segments.models import FieldRule, RuleGroup

        rule = RuleGroup(operator="AND", children=[
            FieldRule(field="inbound_count", operator=">=", value=5),
            FieldRule(field="queue_count", operator=">=", value=1),
            FieldRule(field="has_pending_queue", operator="=", value=True),
        ])
        where, params = compile_rule(7, rule)
        import re

        for n in re.findall(r"creator_id = \$(\d+)", where):
            assert params[int(n) - 1] == 7

    def test_search_messages_accepts_creator(self):
        import inspect

        import db.postgres as pg

        assert "creator_id" in inspect.signature(pg.search_messages).parameters

    def test_legacy_queue_branches_deprecated(self):
        import inspect

        import db.postgres as pg

        assert "deprecated" in inspect.getsource(pg.get_queue_item).lower()
        assert "deprecated" in inspect.getsource(pg.resolve_queue_item).lower()
