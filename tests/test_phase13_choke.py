"""Phase 1.3 choke tests: central enqueue_send guard + hold/error semantics."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.output_rails import RailsRefusal


def _fake_redis():
    r = AsyncMock()
    r.xadd = AsyncMock(return_value="1-0")
    return r


def _payload(text):
    return {
        "entity": "123",
        "content": text,
        "draft_content": text,
        "was_edited": False,
        "was_auto_approved": False,
        "confidence_score": 0.9,
        "creator_id": "42",
    }


@pytest.mark.asyncio
async def test_enqueue_refuses_echo_no_xadd():
    from db import redis as _redis

    r = _fake_redis()
    with patch.object(_redis, "get_redis", new=AsyncMock(return_value=r)):
        try:
            await _redis.enqueue_send(
                _payload("Your conversational response to the fan"), creator_id=42
            )
            raised = None
        except RailsRefusal as e:
            raised = e
    assert raised is not None and "prompt_echo" in raised.flags
    r.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_refuses_fan_speaker_markup():
    from db import redis as _redis

    for text, flag in [
        ("Hey fan, great to see you!", "fan_word"),
        # Central choke has no persona names: only generic labels detectable
        # here (name-specific prefixes are enforced at pipeline 1.2).
        ("CHARACTER: Hey there!", "speaker_prefix"),
        ("[PLAYER MESSAGE] hello friend", "markup_echo"),
    ]:
        r = _fake_redis()
        with patch.object(_redis, "get_redis", new=AsyncMock(return_value=r)):
            try:
                await _redis.enqueue_send(_payload(text), creator_id=42)
                raised = None
            except RailsRefusal as e:
                raised = e
        assert raised is not None and flag in raised.flags, text
        r.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_refuses_caption_only():
    from db import redis as _redis

    r = _fake_redis()
    with patch.object(_redis, "get_redis", new=AsyncMock(return_value=r)):
        try:
            await _redis.enqueue_send(
                {"entity": "123", "caption": "Hey fan, see this!", "creator_id": "42"},
                creator_id=42,
            )
            raised = None
        except RailsRefusal as e:
            raised = e
    assert raised is not None and "fan_word" in raised.flags
    r.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_clean_and_edited_retry_send():
    from db import redis as _redis

    r = _fake_redis()
    with patch.object(_redis, "get_redis", new=AsyncMock(return_value=r)):
        sid = await _redis.enqueue_send(
            _payload("Hey Alex! Dance class was great today."), dedup_id="a", creator_id=42
        )
        assert sid == "1-0"
        sid2 = await _redis.enqueue_send(
            _payload("Hey Alex! Edited clean retry."), dedup_id="b", creator_id=42
        )
        assert sid2 == "1-0"
    assert r.xadd.await_count == 2


@pytest.mark.asyncio
async def test_queue_send_rails_refusal_422_item_pending():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth
    from commerce.single_creator import SingleCreatorStatus

    row = {
        "id": 10,
        "user_id": 123,
        "creator_id": 42,
        "draft_content": "Hey fan, great to see you!",
        "status": "pending",
        "confidence_score": 0.9,
        "assigned_to": None,
    }
    creator = MagicMock()
    creator.status = SingleCreatorStatus.READY
    creator.creator_id = 42
    resolve_mock = AsyncMock(return_value=True)
    with (
        patch(
            "commerce.single_creator.resolve_single_application_creator",
            new=AsyncMock(return_value=creator),
        ),
        patch("chatbotv2.dashboard.routes.queue.get_queue_item", new=AsyncMock(return_value=row)),
        patch(
            "chatbotv2.dashboard.routes.queue.enqueue_send",
            new=AsyncMock(side_effect=RailsRefusal(["fan_word"])),
        ),
        patch("chatbotv2.dashboard.routes.queue.resolve_queue_item", resolve_mock),
        patch("chatbotv2.dashboard.routes.queue.publish_event", new=AsyncMock()),
        patch("core.audit.record_audit_event", new=AsyncMock(return_value=True)),
    ):
        app.dependency_overrides[require_auth] = lambda: {"username": "op1"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/suggestions/10/send")
        app.dependency_overrides.clear()
    assert resp.status_code == 422
    assert "fan_word" in resp.text
    resolve_mock.assert_not_called()


@pytest.mark.asyncio
async def test_messages_send_rails_refusal_422_flags():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth
    from commerce.single_creator import SingleCreatorStatus

    creator = MagicMock()
    creator.status = SingleCreatorStatus.READY
    creator.creator_id = 42
    with (
        patch(
            "commerce.single_creator.resolve_single_application_creator",
            new=AsyncMock(return_value=creator),
        ),
        patch(
            "chatbotv2.dashboard.routes.messages.enqueue_send",
            new=AsyncMock(side_effect=RailsRefusal(["prompt_echo"])),
        ),
        patch("core.audit.record_audit_event", new=AsyncMock(return_value=True)),
    ):
        app.dependency_overrides[require_auth] = lambda: {"username": "op1"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/send-message", json={"user_id": 123, "content": "Hey fan!"}
            )
        app.dependency_overrides.clear()
    assert resp.status_code == 422
    assert "prompt_echo" in resp.text


@pytest.mark.asyncio
async def test_vault_send_rails_refusal_422_flags():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    with (
        patch(
            "chatbotv2.dashboard.routes.vault._require_creator_id",
            new=AsyncMock(return_value=42),
        ),
        patch(
            "chatbotv2.dashboard.routes.vault.vault_svc.get_media",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "chatbotv2.dashboard.routes.vault.enqueue_send",
            new=AsyncMock(side_effect=RailsRefusal(["fan_word"])),
        ),
        patch("core.audit.record_audit_event", new=AsyncMock(return_value=True)),
    ):
        app.dependency_overrides[require_auth] = lambda: {"username": "op1"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/vault/send-media",
                json={
                    "user_id": 123,
                    "media_type": "photo",
                    "media_path": "https://example.com/a.jpg",
                    "fangate_media_id": 7,
                    "caption": "Hey fan, look!",
                },
            )
        app.dependency_overrides.clear()
    assert resp.status_code == 422
    assert "fan_word" in resp.text


@pytest.mark.asyncio
async def test_scheduler_rails_hold_not_failed():
    import workers.scheduler_worker as _sched

    statements = []

    class _FakeConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, sql, *args):
            statements.append((sql, args))
            return "UPDATE 1"

    class _FakePool:
        def acquire(self):
            return _FakeConn()

    full = {
        "id": 5,
        "user_id": 123,
        "creator_id": 42,
        "content": "Your conversational response to the fan",
        "dedup_key": "job1",
        "generation_id": "scheduled:job1:5",
        "status": "processing",
        "attempts": 0,
        "claimed_by": "w",
    }
    mock_failed = AsyncMock(return_value=True)
    mock_enqueued = AsyncMock(return_value=True)
    with (
        patch.object(_sched, "claim_due_messages", new=AsyncMock(return_value=[{"id": 5}])),
        patch.object(_sched, "_fetch_message", new=AsyncMock(return_value=full)),
        patch.object(_sched, "_check_user_eligible", new=AsyncMock(return_value=True)),
        patch.object(
            _sched, "enqueue_send", new=AsyncMock(side_effect=RailsRefusal(["prompt_echo"]))
        ),
        patch.object(_sched, "mark_scheduled_failed", mock_failed),
        patch.object(_sched, "mark_scheduled_enqueued", mock_enqueued),
        patch("db.postgres.get_pool", new=AsyncMock(return_value=_FakePool())),
        patch("core.audit.record_audit_event", new=AsyncMock(return_value=True)),
    ):
        n = await _sched.process_due_messages("w")
    assert n == 0
    mock_failed.assert_not_called()
    mock_enqueued.assert_not_called()
    holds = [s for s, _ in statements if "SET status = 'pending'" in s]
    assert holds, statements
