"""Phase 3.2 outbox relay proofs: crash between XADD and resolve → re-drive.

Single wire is proven by stable dedup identities across re-XADDs (consumer
reservation suppresses the duplicate wire — covered by p18/77d suites).
These tests prove each relay window re-drives exactly once more with the
SAME identity and eventually resolves, without was_auto_approved drift.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeRedis:
    def __init__(self):
        self.xadds = []
        self.store = {}

    async def xadd(self, stream, data, id="*"):
        self.xadds.append((stream, dict(data)))
        return "9-0"

    async def set(self, key, value, nx=False, ex=None):
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


def _queue_row(**over):
    row = {
        "id": 1,
        "user_id": 123,
        "creator_id": 42,
        "status": "pending",
        "draft_content": "hello there",
        "edited": True,
        "resolved_by": "op1",
        "confidence_score": 0.9,
        "assigned_to": None,
        "generation_id": None,
    }
    row.update(over)
    return row


class _FakePoolConn:
    def __init__(self, creator_ids):
        self.creator_ids = creator_ids

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch(self, *a, **k):
        return [{"creator_id": c} for c in self.creator_ids]


class _FakePool:
    def __init__(self, creator_ids):
        self.creator_ids = creator_ids

    def acquire(self):
        return _FakePoolConn(self.creator_ids)


@pytest.mark.asyncio
async def test_flush_xadd_resolve_crash_single_wire():
    import workers.send_worker as _sw
    from db import redis as _r

    fake = _FakeRedis()
    row = _queue_row()
    resolve_calls = []

    async def flaky_resolve(queue_id, status, **kwargs):
        resolve_calls.append(status)
        if len(resolve_calls) == 1:
            raise RuntimeError("crash between XADD and resolve")
        return True

    async def fake_pool():
        return _FakePool([42])

    with (
        patch("db.postgres.get_pool", new=AsyncMock(side_effect=fake_pool)),
        patch.object(_sw, "get_pending_queue_items", new=AsyncMock(return_value=[dict(row)])),
        patch("db.postgres.get_queue_item_for_send", new=AsyncMock(return_value=dict(row))),
        patch.object(_sw, "resolve_queue_item", side_effect=flaky_resolve),
        patch("core.audit.record_audit_event", new=AsyncMock(return_value=True)),
        patch("core.event_bus.publish_event", new=AsyncMock(return_value="e")),
        patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)),
    ):
        await _sw.flush_queue(max_items=5)
        await _sw.flush_queue(max_items=5)
    sends = [d for s, d in fake.xadds if s == "send_messages"]
    assert len(sends) == 2
    # Same dedup identity across the crash (M6 content-bound suffix applies
    # to edited rows) → consumer suppresses the duplicate wire.
    assert len({d["dedup_id"] for d in sends}) == 1
    # M6: edited rows never auto-approve — what matters is no drift across retry.
    assert [d["was_auto_approved"] for d in sends] == ["False", "False"]
    assert resolve_calls == ["approved", "approved"]


@pytest.mark.asyncio
async def test_dashboard_xadd_resolve_crash_single_wire():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth
    from commerce.single_creator import SingleCreatorStatus
    from db import redis as _r

    fake = _FakeRedis()
    row = _queue_row()
    creator = MagicMock()
    creator.status = SingleCreatorStatus.READY
    creator.creator_id = 42
    resolve_calls = []

    async def flaky_resolve(queue_id, status, **kwargs):
        resolve_calls.append(status)
        if len(resolve_calls) == 1:
            raise RuntimeError("crash between XADD and resolve")
        return True

    async def drive():
        app.dependency_overrides[require_auth] = lambda: {"username": "op1"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/suggestions/1/send")

    with (
        patch(
            "commerce.single_creator.resolve_single_application_creator",
            new=AsyncMock(return_value=creator),
        ),
        patch(
            "chatbotv2.dashboard.routes.queue.get_queue_item", new=AsyncMock(return_value=dict(row))
        ),
        patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)),
        patch("chatbotv2.dashboard.routes.queue.resolve_queue_item", side_effect=flaky_resolve),
        patch("chatbotv2.dashboard.routes.queue.publish_event", new=AsyncMock()),
        patch("core.audit.record_audit_event", new=AsyncMock(return_value=True)),
    ):
        # First drive crashes between XADD and resolve (unhandled == dropped
        # connection in production); second drive is the operator retry.
        with pytest.raises(RuntimeError, match="crash between XADD and resolve"):
            await drive()
        resp2 = await drive()
        assert resp2.status_code == 200
        app.dependency_overrides.clear()
    sends = [d for s, d in fake.xadds if s == "send_messages"]
    assert len(sends) == 2
    assert len({d["dedup_id"] for d in sends}) == 1


@pytest.mark.asyncio
async def test_dashboard_resolve_race_no_second_wire():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth
    from commerce.single_creator import SingleCreatorStatus
    from db import redis as _r

    fake = _FakeRedis()
    creator = MagicMock()
    creator.status = SingleCreatorStatus.READY
    creator.creator_id = 42
    with (
        patch(
            "commerce.single_creator.resolve_single_application_creator",
            new=AsyncMock(return_value=creator),
        ),
        patch(
            "chatbotv2.dashboard.routes.queue.get_queue_item",
            new=AsyncMock(return_value=_queue_row()),
        ),
        patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)),
        patch(
            "chatbotv2.dashboard.routes.queue.resolve_queue_item", new=AsyncMock(return_value=False)
        ),
        patch("chatbotv2.dashboard.routes.queue.publish_event", new=AsyncMock()),
        patch("core.audit.record_audit_event", new=AsyncMock(return_value=True)),
    ):
        app.dependency_overrides[require_auth] = lambda: {"username": "op1"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/suggestions/1/send")
        app.dependency_overrides.clear()
    assert resp.status_code == 409
    assert len([d for s, d in fake.xadds if s == "send_messages"]) == 1


@pytest.mark.asyncio
async def test_scheduler_xadd_mark_crash_single_wire():
    import workers.scheduler_worker as _sched
    from db import redis as _r

    fake = _FakeRedis()
    full = {
        "id": 5,
        "user_id": 123,
        "creator_id": 42,
        "content": "scheduled hello",
        "dedup_key": "job1",
        "generation_id": "scheduled:job1:5",
        "status": "processing",
        "attempts": 0,
        "claimed_by": "w",
    }
    marks = []

    async def flaky_mark(msg_id):
        marks.append(msg_id)
        if len(marks) == 1:
            raise RuntimeError("crash between XADD and mark")
        return True

    with (
        patch.object(_sched, "claim_due_messages", new=AsyncMock(return_value=[{"id": 5}])),
        patch.object(_sched, "_fetch_message", new=AsyncMock(return_value=dict(full))),
        patch.object(_sched, "_check_user_eligible", new=AsyncMock(return_value=True)),
        patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)),
        patch.object(_sched, "mark_scheduled_enqueued", side_effect=flaky_mark),
        patch.object(_sched, "mark_scheduled_failed", new=AsyncMock(return_value=False)),
        patch("core.audit.record_audit_event", new=AsyncMock(return_value=True)),
    ):
        await _sched.process_due_messages("w")
        await _sched.process_due_messages("w")
    sends = [d for s, d in fake.xadds if s == "send_messages"]
    assert len(sends) == 2
    assert {d["dedup_id"] for d in sends} == {"scheduled:job1:5"}
    assert marks == [5, 5]
