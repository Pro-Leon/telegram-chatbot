"""M6 — operator/content-integrity tests (edited sends, identity, WS filtering).

Hermetic: no live services. Real route/worker functions run against minimal
fakes for get_pool / enqueue / events / resolver so creator predicates, the
edit lifecycle, dedup/generation identity, and WS filtering are exercised.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeConn:
    def __init__(self, store: dict, log: list):
        self._s = store
        self._log = log

    async def fetchrow(self, sql: str, *params):
        self._log.append(("fetchrow", sql, params))
        if "FROM operator_queue" in sql:
            row = self._s["queue"].get(params[0])
            if row is None:
                return None
            if "q.creator_id = $2" in sql and int(row.get("creator_id") or -1) != int(params[1]):
                return None
            return copy.deepcopy(row)
        if "FROM messages m JOIN users" in sql:
            uid, cid = params[0], params[1]
            cands = [m for m in self._s["messages"]
                     if m["user_id"] == uid and m["creator_id"] == cid and m["direction"] == "inbound"]
            if not cands:
                return None
            m = cands[0]
            u = self._s["users"].get(uid, {})
            return {"user_id": uid, "content": m["content"],
                    "telegram_message_id": m.get("telegram_message_id"),
                    "username": u.get("username"), "first_name": u.get("first_name")}
        return None

    async def fetch(self, sql: str, *params):
        self._log.append(("fetch", sql, params))
        return []

    async def execute(self, sql: str, *params):
        self._log.append(("execute", sql, params))
        if "UPDATE operator_queue SET" in sql:
            status, final_content = params[0], params[1]
            if "AND creator_id" in sql:
                # scoped branch: (status, final, operator_id, resolved_by, id, creator)
                qid, cid = params[4], params[5]
                row = self._s["queue"].get(qid)
                if row is None or int(row.get("creator_id") or -1) != int(cid) or row.get("status") != "pending":
                    return "UPDATE 0"
                if self._s.get("fail_next_approve"):
                    self._s["fail_next_approve"] = False
                    return "UPDATE 0"
                row["status"] = status
                if final_content is not None:
                    if final_content != row.get("draft_content"):
                        row["edited"] = True
                    row["draft_content"] = final_content
                    row["final_content"] = final_content
                if len(params) > 3 and params[3] is not None:
                    row["resolved_by"] = params[3]
                return "UPDATE 1"
            return "UPDATE 1"
        return "OK"


class FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class FakePool:
    def __init__(self, store: dict):
        self.store = store
        self.log: list = []
        self._conn = FakeConn(store, self.log)

    def acquire(self):
        return FakeAcquire(self._conn)


def _queue_store() -> dict:
    return {
        "queue": {
            10: {"id": 10, "user_id": 777, "creator_id": 1, "status": "pending",
                 "draft_content": "original draft", "final_content": None,
                 "edited": False, "confidence_score": 0.4, "flags": ["tone"],
                 "generation_id": "abc123abc123abc123abc123abc123ab",
                 "assigned_to": None, "resolved_by": None},
        },
        "messages": [],
        "users": {777: {"username": "fan", "first_name": "Alex"}},
        "fail_next_approve": False,
    }


def _ready_ctx(creator_id: int | None):
    from unittest.mock import MagicMock

    from commerce.single_creator import SingleCreatorStatus

    ctx = MagicMock()
    if creator_id is None:
        ctx.status = SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE
        ctx.creator_id = None
    else:
        ctx.status = SingleCreatorStatus.READY
        ctx.creator_id = creator_id
    return ctx


# ---------------------------------------------------------------------------
# M6-01/M6-02: edit lifecycle on the suggestion-send path
# ---------------------------------------------------------------------------


class TestSuggestionEditIntegrity:
    @pytest.mark.asyncio
    async def test_edited_content_reaches_payload_and_persists(self):
        import chatbotv2.dashboard.routes.queue as routes

        store = _queue_store()
        pool = FakePool(store)
        enqueued: dict = {}
        events: list = []

        async def _fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None):
            enqueued.update(payload=payload, dedup_id=dedup_id,
                            generation_id=generation_id, creator_id=creator_id)
            return "stream-1"

        async def _fake_publish(event_type, data, **kw):
            events.append((event_type, data, kw))

        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch("commerce.single_creator.resolve_single_application_creator",
                  new=AsyncMock(return_value=_ready_ctx(1))),
            patch.object(routes, "enqueue_send", side_effect=_fake_enqueue),
            patch.object(routes, "publish_event", side_effect=_fake_publish),
        ):
            resp = await routes.api_send_suggestion(10, content="fixed text", auth={"username": "op1"})
        import json as _json

        body = _json.loads(resp.body.decode())
        assert body == {"ok": True, "edited": True}
        # intent == bytes at every stage
        assert enqueued["payload"]["content"] == "fixed text"
        assert enqueued["payload"]["draft_content"] == "fixed text"
        assert enqueued["payload"]["was_edited"] is True
        assert enqueued["payload"]["was_auto_approved"] is False
        # correlation preserved, dedup bound to edited bytes
        assert enqueued["generation_id"] == "abc123abc123abc123abc123abc123ab"
        want_dedup = f"queue_item:10:{hashlib.sha256(b'fixed text').hexdigest()[:16]}"
        assert enqueued["dedup_id"] == want_dedup
        # persisted row matches
        row = store["queue"][10]
        assert row["status"] == "approved"
        assert row["draft_content"] == "fixed text" and row["final_content"] == "fixed text"
        assert row["edited"] is True and row["resolved_by"] == "op1"
        # audit evidence emitted
        assert ("operator_queue.updated",) == (events[0][0],)
        assert events[0][1]["edited"] is True
        assert events[0][1]["action"] == "approved"
        assert events[0][1]["resolved_by"] == "op1"

    @pytest.mark.asyncio
    async def test_unedited_send_preserves_legacy_identity(self):
        import chatbotv2.dashboard.routes.queue as routes

        store = _queue_store()
        pool = FakePool(store)
        enqueued: dict = {}

        async def _fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None):
            enqueued.update(payload=payload, dedup_id=dedup_id)
            return "stream-1"

        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch("commerce.single_creator.resolve_single_application_creator",
                  new=AsyncMock(return_value=_ready_ctx(1))),
            patch.object(routes, "enqueue_send", side_effect=_fake_enqueue),
            patch.object(routes, "publish_event", new=AsyncMock()),
        ):
            resp = await routes.api_send_suggestion(10, auth={"username": "op1"})
        import json as _json

        assert _json.loads(resp.body.decode()) == {"ok": True, "edited": False}
        assert enqueued["payload"]["content"] == "original draft"
        assert enqueued["payload"]["was_edited"] is False
        assert enqueued["dedup_id"] == "queue_item:10"

    @pytest.mark.asyncio
    async def test_empty_edit_rejected_and_identity_edit_is_noop(self):
        import chatbotv2.dashboard.routes.queue as routes
        from fastapi import HTTPException

        store = _queue_store()
        pool = FakePool(store)
        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch("commerce.single_creator.resolve_single_application_creator",
                  new=AsyncMock(return_value=_ready_ctx(1))),
            patch.object(routes, "enqueue_send", new=AsyncMock()) as mock_enq,
            patch.object(routes, "publish_event", new=AsyncMock()),
        ):
            with pytest.raises(HTTPException) as ei:
                await routes.api_send_suggestion(10, content="   ", auth={})
            assert ei.value.status_code == 422
            mock_enq.assert_not_called()
            assert store["queue"][10]["status"] == "pending"
            # identical content is not an edit: legacy path
            resp = await routes.api_send_suggestion(10, content="original draft", auth={})
        import json as _json

        assert _json.loads(resp.body.decode())["edited"] is False

    @pytest.mark.asyncio
    async def test_lost_race_after_enqueue_still_409(self):
        """Enqueue succeeded but approve lost the race: 409, no event."""
        import chatbotv2.dashboard.routes.queue as routes
        from fastapi import HTTPException

        store = _queue_store()
        store["fail_next_approve"] = True  # second resolve (approve) loses
        pool = FakePool(store)
        events: list = []

        async def _fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None):
            return "stream-1"

        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch("commerce.single_creator.resolve_single_application_creator",
                  new=AsyncMock(return_value=_ready_ctx(1))),
            patch.object(routes, "enqueue_send", side_effect=_fake_enqueue),
            patch.object(routes, "publish_event", side_effect=lambda *a, **k: events.append(a)),
        ):
            with pytest.raises(HTTPException) as ei:
                await routes.api_send_suggestion(10, auth={})
            assert ei.value.status_code == 409
            assert events == []


# ---------------------------------------------------------------------------
# M6-04: closed resolve states + creator/mutation negatives
# ---------------------------------------------------------------------------


class TestQueueMutationBoundaries:
    @pytest.mark.asyncio
    async def test_resolve_status_allowlist(self):
        import chatbotv2.dashboard.routes.queue as routes
        from fastapi import HTTPException

        store = _queue_store()
        pool = FakePool(store)
        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch("commerce.single_creator.resolve_single_application_creator",
                  new=AsyncMock(return_value=_ready_ctx(1))),
            patch.object(routes, "publish_event", new=AsyncMock()),
        ):
            with pytest.raises(HTTPException) as ei:
                await routes.api_resolve_queue(10, status="weird", content=None, auth={})
            assert ei.value.status_code == 422
            assert store["queue"][10]["status"] == "pending"
            for ok_status in ("pending", "approved", "rejected", "failed"):
                store["queue"][10]["status"] = "pending"
                resp = await routes.api_resolve_queue(10, status=ok_status, content=None, auth={})
                assert resp.status_code == 200
                assert store["queue"][10]["status"] == ok_status

    @pytest.mark.asyncio
    async def test_foreign_creator_cannot_read_or_mutate(self):
        import chatbotv2.dashboard.routes.queue as routes
        from fastapi import HTTPException

        store = _queue_store()
        pool = FakePool(store)
        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch("commerce.single_creator.resolve_single_application_creator",
                  new=AsyncMock(return_value=_ready_ctx(2))),
            patch.object(routes, "enqueue_send", new=AsyncMock()),
            patch.object(routes, "publish_event", new=AsyncMock()),
        ):
            with pytest.raises(HTTPException) as ei:
                await routes.api_resolve_queue(10, status="approved", content=None, auth={})
            assert ei.value.status_code == 404
            with pytest.raises(HTTPException) as ei:
                await routes.api_send_suggestion(10, auth={})
            assert ei.value.status_code == 404
            assert store["queue"][10]["status"] == "pending"

    def test_mutation_endpoints_take_no_recipient_or_creator(self):
        import chatbotv2.dashboard.routes.queue as routes

        for fn in (routes.api_resolve_queue, routes.api_send_suggestion):
            params = set(inspect.signature(fn).parameters)
            assert "user_id" not in params, fn.__name__
            assert "creator_id" not in params, fn.__name__
            assert "dialog_id" not in params, fn.__name__


# ---------------------------------------------------------------------------
# Flush path: was_edited parity + auto-approval honesty
# ---------------------------------------------------------------------------


class TestFlushEditedParity:
    @pytest.mark.asyncio
    async def test_process_approved_message_edited_forces_manual(self):
        from workers.send_worker import process_approved_message

        captured: dict = {}

        async def _fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None):
            captured.update(payload=payload, dedup_id=dedup_id)
            return "stream-9"

        with patch("workers.send_worker.enqueue_send", side_effect=_fake_enqueue):
            # high stored confidence but edited bytes: never auto-approved
            res = await process_approved_message(
                user_id=777, content="edited", confidence_score=0.95,
                queue_id=10, creator_id=1, was_edited=True)
            assert res["ok"] is True
            assert captured["payload"]["was_edited"] is True
            assert captured["payload"]["was_auto_approved"] is False
            # M7 (B3): edited flush bytes carry the content-bound dedup suffix
            # consistent with the dashboard send path (was: queue_item:{id}).
            import hashlib as _hashlib

            assert captured["dedup_id"] == (
                "queue_item:10:" + _hashlib.sha256(b"edited").hexdigest()[:16]
            )
            assert res["dedup_id"] == captured["dedup_id"]
            # unedited high-confidence keeps legacy derivation
            res = await process_approved_message(
                user_id=777, content="draft", confidence_score=0.95,
                queue_id=10, creator_id=1, was_edited=False)
            assert res == {"ok": True, "dedup_id": "queue_item:10"}
            assert captured["dedup_id"] == "queue_item:10"
            assert captured["payload"]["was_auto_approved"] is True
            assert captured["payload"]["was_edited"] is False

    @pytest.mark.asyncio
    async def test_flush_forwards_row_edited_flag(self):
        import workers.send_worker as worker

        items = [
            {"id": 11, "user_id": 777, "status": "pending", "draft_content": "fixed",
             "confidence_score": 0.9, "assigned_to": None, "creator_id": 1,
             "generation_id": None, "edited": True},
        ]
        seen: dict = {}

        async def _fake_process(**kwargs):
            seen.update(kwargs)
            return {"ok": True}

        with (
            patch.object(worker, "get_pending_queue_items", new=AsyncMock(return_value=items)),
            patch.object(worker, "process_approved_message", side_effect=_fake_process),
            patch.object(worker, "resolve_queue_item", new=AsyncMock(return_value=True)),
            patch("core.event_bus.publish_event", new=AsyncMock()),
            patch("db.postgres.get_pool", new=AsyncMock()),
            # M7 (B3): flush re-reads the authoritative row before enqueue.
            patch("db.postgres.get_queue_item_for_send", new=AsyncMock(return_value=dict(items[0]))),
        ):
            # flush enumerates active creators from the DB
            import db.postgres as pg

            class _Row(dict):
                pass

            async def _fake_fetch(sql, *a):
                return [{"creator_id": 1}]

            fake_conn = AsyncMock()
            fake_conn.fetch = _fake_fetch

            class _Acq:
                async def __aenter__(self):
                    return fake_conn

                async def __aexit__(self, *a):
                    return False

            fake_pool = AsyncMock()
            fake_pool.acquire = lambda: _Acq()
            with patch.object(pg, "get_pool", new=AsyncMock(return_value=fake_pool)):
                sent = await worker.flush_queue(max_items=10)
        assert sent == 1
        assert seen["was_edited"] is True
        assert seen["content"] == "fixed"


# ---------------------------------------------------------------------------
# Vault media: creator preference + caption-bound identity
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return dict(self._body)


class TestVaultMediaIntegrity:
    @pytest.mark.asyncio
    async def test_creator_prefers_active_integration(self):
        import chatbotv2.dashboard.routes.vault as routes

        with (
            patch("db.fangate.list_active_creator_ids", new=AsyncMock(return_value=[2])),
            patch("db.fangate.get_any_creator_id_with_integration", new=AsyncMock(return_value=1)),
        ):
            assert await routes._require_creator_id({}) == 2

    @pytest.mark.asyncio
    async def test_creator_falls_back_to_legacy_lookup(self):
        import chatbotv2.dashboard.routes.vault as routes

        with (
            patch("db.fangate.list_active_creator_ids", new=AsyncMock(return_value=[])),
            patch("db.fangate.get_any_creator_id_with_integration", new=AsyncMock(return_value=1)),
        ):
            assert await routes._require_creator_id({}) == 1

    @pytest.mark.asyncio
    async def test_creator_missing_fails_closed(self):
        import chatbotv2.dashboard.routes.vault as routes

        with (
            patch("db.fangate.list_active_creator_ids", new=AsyncMock(return_value=[])),
            patch("db.fangate.get_any_creator_id_with_integration", new=AsyncMock(return_value=None)),
        ):
            with pytest.raises(ValueError):
                await routes._require_creator_id({})

    @pytest.mark.asyncio
    async def test_caption_bound_dedup_and_generation(self):
        import chatbotv2.dashboard.routes.vault as routes

        def _body(caption):
            return {"user_id": 777, "media_type": "photo",
                    "media_path": "https://cdn/x.jpg", "fangate_media_id": 55,
                    "caption": caption}

        seen: list = []

        async def _fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None):
            seen.append((dict(payload), dedup_id, generation_id, creator_id))
            return "stream-1"

        with (
            patch.object(routes, "_require_creator_id", new=AsyncMock(return_value=1)),
            patch.object(routes, "enqueue_send", side_effect=_fake_enqueue),
        ):
            await routes.send_vault_media(_FakeRequest(_body("hello")), auth={})
            await routes.send_vault_media(_FakeRequest(_body("hello world")), auth={})
        (p1, d1, g1, c1), (p2, d2, g2, c2) = seen
        assert d1 != d2  # caption edit => distinct delivery identity
        assert p1["content"] == "hello" and p2["content"] == "hello world"
        assert str(g1).startswith("manual:") and str(g2).startswith("manual:")
        assert g1 != g2
        assert c1 == 1 and p1["creator_id"] == "1"
        # identical retry still dedups
        seen.clear()
        with (
            patch.object(routes, "_require_creator_id", new=AsyncMock(return_value=1)),
            patch.object(routes, "enqueue_send", side_effect=_fake_enqueue),
        ):
            await routes.send_vault_media(_FakeRequest(_body("hello")), auth={})
        assert seen[0][1] == d1


# ---------------------------------------------------------------------------
# AI-reply identity honesty
# ---------------------------------------------------------------------------


class TestAiReplyIdentity:
    def _msg_store(self, tg):
        store = _queue_store()
        store["messages"].append(
            {"user_id": 777, "creator_id": 1, "direction": "inbound",
             "content": "hey", "telegram_message_id": tg})
        return store

    @pytest.mark.asyncio
    async def test_null_telegram_id_gets_synthetic_identity(self):
        import chatbotv2.dashboard.routes.messages as routes

        pool = FakePool(self._msg_store(None))
        captured: dict = {}

        async def _fake_enqueue_inbound(payload):
            captured.update(payload)
            return "stream-1"

        with (
            patch.object(routes, "get_pool", new=AsyncMock(return_value=pool)),
            patch("commerce.single_creator.resolve_single_application_creator",
                  new=AsyncMock(return_value=_ready_ctx(1))),
            patch.object(routes, "get_cached_user_persona", new=AsyncMock(return_value=None)),
            patch.object(routes, "get_user_persona", new=AsyncMock(return_value="p")),
            patch("db.redis.cache_user_persona", new=AsyncMock()),
            patch.object(routes, "get_cached_default_persona", new=AsyncMock(return_value="d")),
            patch.object(routes, "enqueue_inbound", side_effect=_fake_enqueue_inbound),
        ):
            resp = await routes.api_dialog_ai_reply(777, auth={})
            assert resp.status_code == 200
        assert str(captured["generation_id"]).startswith("manual:")
        assert "ai-reply" in str(captured["generation_id"])

    @pytest.mark.asyncio
    async def test_real_telegram_id_keeps_canonical_md5(self):
        import chatbotv2.dashboard.routes.messages as routes
        from core.generation import is_telegram_generation_id

        pool = FakePool(self._msg_store(41))
        captured: dict = {}

        async def _fake_enqueue_inbound(payload):
            captured.update(payload)
            return "stream-1"

        with (
            patch.object(routes, "get_pool", new=AsyncMock(return_value=pool)),
            patch("commerce.single_creator.resolve_single_application_creator",
                  new=AsyncMock(return_value=_ready_ctx(1))),
            patch.object(routes, "get_cached_user_persona", new=AsyncMock(return_value=None)),
            patch.object(routes, "get_user_persona", new=AsyncMock(return_value="p")),
            patch("db.redis.cache_user_persona", new=AsyncMock()),
            patch.object(routes, "get_cached_default_persona", new=AsyncMock(return_value="d")),
            patch.object(routes, "enqueue_inbound", side_effect=_fake_enqueue_inbound),
        ):
            await routes.api_dialog_ai_reply(777, auth={})
        assert is_telegram_generation_id(captured["generation_id"])

    @pytest.mark.asyncio
    async def test_repeat_click_reports_suppression(self):
        import chatbotv2.dashboard.routes.messages as routes
        import json as _json

        pool = FakePool(self._msg_store(41))
        with (
            patch.object(routes, "get_pool", new=AsyncMock(return_value=pool)),
            patch("commerce.single_creator.resolve_single_application_creator",
                  new=AsyncMock(return_value=_ready_ctx(1))),
            patch.object(routes, "get_cached_user_persona", new=AsyncMock(return_value=None)),
            patch.object(routes, "get_user_persona", new=AsyncMock(return_value="p")),
            patch("db.redis.cache_user_persona", new=AsyncMock()),
            patch.object(routes, "get_cached_default_persona", new=AsyncMock(return_value="d")),
            patch.object(routes, "enqueue_inbound", new=AsyncMock(return_value="duplicate:41")),
        ):
            resp = await routes.api_dialog_ai_reply(777, auth={})
        body = _json.loads(resp.body.decode())
        assert body["ok"] is True and body["deduped"] is True


# ---------------------------------------------------------------------------
# WebSocket creator filtering (fail closed on empty filter)
# ---------------------------------------------------------------------------


class _FakeWS:
    def __init__(self):
        self.sent: list = []

    async def accept(self):
        return None

    async def send_text(self, data):
        self.sent.append(data)


class TestWebSocketFiltering:
    @pytest.mark.asyncio
    async def test_empty_filter_gets_no_creator_events(self):
        from chatbotv2.dashboard.ws_manager import ConnectionManager

        mgr = ConnectionManager()
        ws_a, ws_empty, ws_b = _FakeWS(), _FakeWS(), _FakeWS()
        await mgr.connect(ws_a, is_global=True, creator_ids={1})
        await mgr.connect(ws_empty, is_global=True, creator_ids=set())
        await mgr.connect(ws_b, is_global=True, creator_ids={2})
        await mgr.broadcast({"event_type": "message.sent"}, dialog_id=None, creator_id=1)
        assert len(ws_a.sent) == 1
        assert ws_empty.sent == []
        assert ws_b.sent == []
        # truly global events still reach global connections
        await mgr.broadcast({"event_type": "health"}, dialog_id=None, creator_id=None)
        assert len(ws_a.sent) == 2
        assert len(ws_empty.sent) == 1
        assert len(ws_b.sent) == 1

    @pytest.mark.asyncio
    async def test_scoped_mismatch_excluded(self):
        from chatbotv2.dashboard.ws_manager import ConnectionManager

        mgr = ConnectionManager()
        ws_a, ws_b = _FakeWS(), _FakeWS()
        await mgr.connect(ws_a, is_global=False, creator_ids={1})
        await mgr.connect(ws_b, is_global=False, creator_ids={2})
        await mgr.broadcast({"event_type": "message.sent"}, dialog_id=None, creator_id=2)
        assert ws_a.sent == []
        assert len(ws_b.sent) == 1


# ---------------------------------------------------------------------------
# Commerce quarantine contract + manual-send boundaries
# ---------------------------------------------------------------------------


class TestCommerceQuarantine:
    def test_no_operator_execution_edge(self):
        import pathlib

        markers = (
            "execute_sealed_offer", "execute_ppv", "seal_ranked_candidate",
            "orchestrate_commerce", "create_offer_serialized",
            "resolve_and_run_commerce", "run_commerce_pipeline",
            "attribute_purchase_from_webhook",
        )
        roots = [
            pathlib.Path("chatbotv2/dashboard/routes/messages.py"),
            pathlib.Path("chatbotv2/dashboard/routes/queue.py"),
            pathlib.Path("chatbotv2/dashboard/routes/dialogs.py"),
            pathlib.Path("chatbotv2/dashboard/routes/followups.py"),
            pathlib.Path("chatbotv2/dashboard/routes/vault.py"),
            pathlib.Path("workers/send_worker.py"),
            pathlib.Path("workers/scheduler_worker.py"),
        ]
        for path in roots:
            src = path.read_text(encoding="utf-8")
            for marker in markers:
                assert marker not in src, f"{path}: {marker}"

    def test_fangate_offer_endpoints_stay_quarantined(self):
        import pathlib

        src = pathlib.Path("chatbotv2/dashboard/routes/fangate.py").read_text(encoding="utf-8")
        for name in ("api_fangate_create_offer",):
            assert name in src
        assert src.count("501") >= 3


class TestManualSendBoundaries:
    @pytest.mark.asyncio
    async def test_manual_send_fail_closed_and_permitted(self):
        import chatbotv2.dashboard.routes.messages as routes
        import json as _json
        from fastapi import HTTPException

        from chatbotv2.dashboard.schemas import SendMessageRequest

        captured: dict = {}

        async def _fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None):
            captured.update(payload=payload, dedup_id=dedup_id, creator_id=creator_id)
            return "stream-1"

        req = SendMessageRequest(user_id=777, content="hello there")
        with patch.object(routes, "enqueue_send", side_effect=_fake_enqueue):
            with patch("commerce.single_creator.resolve_single_application_creator",
                      new=AsyncMock(return_value=_ready_ctx(None))):
                with pytest.raises(HTTPException) as ei:
                    await routes.api_send_message(req, auth={})
                assert ei.value.status_code == 404
            with patch("commerce.single_creator.resolve_single_application_creator",
                      new=AsyncMock(return_value=_ready_ctx(1))):
                resp = await routes.api_send_message(req, auth={})
        assert _json.loads(resp.body.decode()) == {"ok": True}
        assert captured["payload"]["entity"] == "777"
        assert captured["payload"]["creator_id"] == "1"
        assert captured["payload"]["was_auto_approved"] is False
        assert str(captured["payload"]["generation_id"]).startswith("manual:")