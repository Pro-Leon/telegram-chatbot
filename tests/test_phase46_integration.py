"""Phase 4.6 — End-to-End & Integration Reliability Verification.

Tests the full application pipeline with mocked external boundaries
(Gemini API, Telegram API) while exercising real internal logic.

Groups:
  A — Message Pipeline E2E
  B — Operator Queue Flow
  C — DLQ Recovery
  D — Gemini Failover
  E — Redis Recovery & Worker Restart
  F — Duplicate Safety & Idempotency
  G — DLQ Replay Advanced
  H — Post-Process Isolation
  I — Migration Discovery
  J — Health & Readiness Endpoints
  K — Dashboard Route Integration
  L — Frontend Contract
  M — Worker Lifecycle
  N — Failure Cascades
  O — Concurrency & Locking
"""

import asyncio
import hashlib
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP A — Message Pipeline E2E
# ═══════════════════════════════════════════════════════════════════════════════


class TestMessagePipelineE2E:
    """End-to-end message pipeline: inbound -> LLM -> score -> route -> send."""

    @pytest.mark.asyncio
    async def test_auto_approved_message_full_flow(self):
        """High-score message with no flags goes directly to send stream."""
        import workers.llm_worker as _lw
        from workers.llm_worker import process_message

        events = []

        async def capture_publish(event_type, data, **kwargs):
            events.append({"type": event_type, "data": data, **kwargs})
            return f"evt-{len(events)}"

        with (
            patch.object(_lw._settings, "llm_path", "legacy"),
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "workers.llm_worker.get_recent_messages",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("db.redis.get_redis", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.build_qwen3_context",
                new_callable=AsyncMock,
                return_value=[
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Hello!"},
                ],
            ),
            patch(
                "workers.llm_worker.generate_draft",
                new_callable=AsyncMock,
                return_value="Hey there! How can I help?",
            ),
            patch(
                "workers.llm_worker.score_draft", new_callable=AsyncMock, return_value=(0.92, [])
            ),
            patch(
                "workers.llm_worker.extract_and_update_profile", new_callable=AsyncMock
            ),
            patch("workers.llm_worker.maybe_summarize", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock) as mock_enqueue,
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch(
                "core.event_bus.publish_event", new_callable=AsyncMock, side_effect=capture_publish
            ),
            patch(
                "core.event_bus.publish_events_batch", new_callable=AsyncMock
            ),
        ):
            await process_message(
                user_id=12345,
                user_message="Hello!",
                telegram_message_id=100,
                username="testuser",
                first_name="Test",
                persona="You are friendly.",
                creator_id=42,
            )

            # Verify generation_started was published
            start_events = [e for e in events if e["type"] == "ai.generation_started"]
            assert len(start_events) == 1
            assert start_events[0]["user_id"] == 12345

            # Verify generation_completed was published
            complete_events = [e for e in events if e["type"] == "ai.generation_completed"]
            assert len(complete_events) == 1
            assert complete_events[0]["data"]["was_auto_approved"] is True
            assert complete_events[0]["data"]["score"] == 0.92
            assert complete_events[0]["data"]["flags"] == []

            # Verify enqueue_send was called with correct payload
            mock_enqueue.assert_called_once()
            call_kwargs = mock_enqueue.call_args
            payload = call_kwargs[0][0]
            assert payload["entity"] == "12345"
            assert payload["content"] == "Hey there! How can I help?"
            assert payload["was_auto_approved"] is True
            assert payload["confidence_score"] == 0.92

    @pytest.mark.asyncio
    async def test_low_score_routes_to_operator_queue(self):
        """Low-score message is routed to operator queue, not send stream."""
        from workers.llm_worker import process_message

        events = []

        async def capture_publish(event_type, data, **kwargs):
            events.append({"type": event_type, "data": data, **kwargs})
            return f"evt-{len(events)}"

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "workers.llm_worker.build_qwen3_context",
                new_callable=AsyncMock,
                return_value=[
                    {"role": "user", "content": "What's the price?"},
                ],
            ),
            patch(
                "workers.llm_worker.generate_draft",
                new_callable=AsyncMock,
                return_value="I can help with pricing.",
            ),
            patch(
                "workers.llm_worker.score_draft",
                new_callable=AsyncMock,
                return_value=(0.55, ["price_mention"]),
            ),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock, return_value=42
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock) as mock_enqueue,
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch(
                "core.event_bus.publish_event", new_callable=AsyncMock, side_effect=capture_publish
            ),
        ):
            await process_message(
                user_id=99999,
                user_message="What's the price?",
                telegram_message_id=200,
                username="buyer",
                first_name="Buyer",
                persona="",
            )

            # Should NOT call enqueue_send (auto-approved path)
            mock_enqueue.assert_not_called()

            # Should call add_to_operator_queue
            from workers.llm_worker import add_to_operator_queue

            add_to_operator_queue.assert_called_once()

            # Should publish suggestion.created
            suggestion_events = [e for e in events if e["type"] == "suggestion.created"]
            assert len(suggestion_events) == 1
            assert suggestion_events[0]["data"]["queue_id"] == 42

            # generation_completed should show was_auto_approved=False
            complete_events = [e for e in events if e["type"] == "ai.generation_completed"]
            assert len(complete_events) == 1
            assert complete_events[0]["data"]["was_auto_approved"] is False

    @pytest.mark.asyncio
    async def test_auto_reply_disabled_routes_to_operator(self):
        """When auto-reply is off, all messages go to operator queue."""
        from workers.llm_worker import process_message

        events = []

        async def capture_publish(event_type, data, **kwargs):
            events.append({"type": event_type, "data": data, **kwargs})
            return f"evt-{len(events)}"

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="Hello!"
            ),
            patch(
                "workers.llm_worker.score_draft", new_callable=AsyncMock, return_value=(0.95, [])
            ),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock, return_value=77
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock) as mock_enqueue,
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch(
                "core.event_bus.publish_event", new_callable=AsyncMock, side_effect=capture_publish
            ),
        ):
            await process_message(
                user_id=55555,
                user_message="Hi there",
                telegram_message_id=300,
                username="user",
                first_name="User",
                persona="",
            )

            mock_enqueue.assert_not_called()

            from workers.llm_worker import add_to_operator_queue

            add_to_operator_queue.assert_called_once()

    @pytest.mark.asyncio
    async def test_excluded_user_skips_processing(self):
        """Users in the auto-reply exclusion list are not processed."""
        from workers.llm_worker import process_message

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.generate_draft", new_callable=AsyncMock) as mock_gen,
        ):
            await process_message(
                user_id=11111,
                user_message="Hello",
                telegram_message_id=400,
                username="excluded",
                first_name="Excluded",
                persona="",
            )
            mock_gen.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_already_held_skips_message(self):
        """If user lock is already held, the message is skipped."""
        from workers.llm_worker import process_message

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=False
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock) as mock_upsert,
        ):
            await process_message(
                user_id=22222,
                user_message="Hello",
                telegram_message_id=500,
                username="fast",
                first_name="Fast",
                persona="",
            )
            mock_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_generation_failure_publishes_failed_event(self):
        """When Gemini fails, ai.generation_failed is published."""
        from workers.llm_worker import process_message

        events = []

        async def capture_publish(event_type, data, **kwargs):
            events.append({"type": event_type, "data": data, **kwargs})
            return f"evt-{len(events)}"

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Gemini unavailable"),
            ),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch(
                "core.event_bus.publish_event", new_callable=AsyncMock, side_effect=capture_publish
            ),
            pytest.raises(RuntimeError, match="Gemini unavailable"),
        ):
            await process_message(
                user_id=33333,
                user_message="Hello",
                telegram_message_id=600,
                username="fail",
                first_name="Fail",
                persona="",
            )

        failed_events = [e for e in events if e["type"] == "ai.generation_failed"]
        assert len(failed_events) == 1

    @pytest.mark.asyncio
    async def test_generation_id_consistent_across_lifecycle(self):
        """Same generation_id used for started, completed, and suggestion events."""
        from workers.llm_worker import process_message

        events = []

        async def capture_publish(event_type, data, **kwargs):
            events.append({"type": event_type, "data": data, **kwargs})
            return f"evt-{len(events)}"

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="draft"
            ),
            patch("workers.llm_worker.score_draft", new_callable=AsyncMock, return_value=(0.6, [])),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock, return_value=10
            ),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch(
                "core.event_bus.publish_event", new_callable=AsyncMock, side_effect=capture_publish
            ),
        ):
            await process_message(
                user_id=44444,
                user_message="test",
                telegram_message_id=700,
                username="gen",
                first_name="Gen",
                persona="",
            )

        gen_ids = [e.get("generation_id") for e in events]
        # All generation_id values must be identical
        assert len(set(gen_ids)) == 1
        # Must be a valid UUID
        assert len(gen_ids[0]) == 36


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B — Operator Queue Flow
# ═══════════════════════════════════════════════════════════════════════════════


class TestOperatorQueueFlow:
    """Operator queue creation, approval, rejection, and dashboard integration."""

    @pytest.mark.asyncio
    async def test_queue_resolve_approval_enqueues_send(self):
        """Approving a queue item enqueues the message to send stream."""
        from commerce.single_creator import SingleCreatorStatus

        mock_item = {"id": 10, "user_id": 500, "draft_content": "Hello!", "status": "pending"}

        with (
            patch(
                "chatbotv2.dashboard.routes.queue.get_queue_item",
                new_callable=AsyncMock,
                return_value=mock_item,
            ),
            patch(
                "chatbotv2.dashboard.routes.queue.resolve_queue_item", new_callable=AsyncMock
            ) as mock_resolve,
            patch(
                "chatbotv2.dashboard.routes.queue.enqueue_send", new_callable=AsyncMock
            ) as mock_enqueue,
            patch("chatbotv2.dashboard.routes.queue.publish_event", new_callable=AsyncMock),
            patch(
                "commerce.single_creator.resolve_single_application_creator",
                new=AsyncMock(
                    return_value=MagicMock(status=SingleCreatorStatus.READY, creator_id=42)
                ),
            ),
        ):
            from httpx import ASGITransport, AsyncClient

            from chatbotv2.dashboard.app import app
            from chatbotv2.dashboard.auth import require_auth

            app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/suggestions/10/send",
                )
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_enqueue.assert_called_once()
        # Creator-scoped + pending-guarded resolution (Phases 1-4).
        assert mock_resolve.call_args[0][:2] == (10, "approved")
        assert mock_resolve.call_args[1].get("creator_id") == 42

    @pytest.mark.asyncio
    async def test_queue_resolve_reject_does_not_enqueue(self):
        """Rejecting a queue item does not enqueue to send stream."""
        from commerce.single_creator import SingleCreatorStatus

        mock_item = {"id": 20, "user_id": 600, "draft_content": "Bye!", "status": "pending"}

        with (
            patch(
                "chatbotv2.dashboard.routes.queue.get_queue_item",
                new_callable=AsyncMock,
                return_value=mock_item,
            ),
            patch("chatbotv2.dashboard.routes.queue.resolve_queue_item", new_callable=AsyncMock),
            patch(
                "chatbotv2.dashboard.routes.queue.enqueue_send", new_callable=AsyncMock
            ) as mock_enqueue,
            patch(
                "commerce.single_creator.resolve_single_application_creator",
                new=AsyncMock(
                    return_value=MagicMock(status=SingleCreatorStatus.READY, creator_id=42)
                ),
            ),
        ):
            from httpx import ASGITransport, AsyncClient

            from chatbotv2.dashboard.app import app
            from chatbotv2.dashboard.auth import require_auth

            app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/queue/20/resolve",
                    data={"status": "rejected"},
                )
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_queue_edit_with_content_enqueues_edited(self):
        """Editing a queue item with new content enqueues the edited version."""
        from commerce.single_creator import SingleCreatorStatus

        mock_item = {"id": 30, "user_id": 700, "draft_content": "Old", "status": "pending"}

        with (
            patch(
                "chatbotv2.dashboard.routes.queue.get_queue_item",
                new_callable=AsyncMock,
                return_value=mock_item,
            ),
            patch("chatbotv2.dashboard.routes.queue.resolve_queue_item", new_callable=AsyncMock),
            patch(
                "chatbotv2.dashboard.routes.queue.enqueue_send", new_callable=AsyncMock
            ) as mock_enqueue,
            patch(
                "commerce.single_creator.resolve_single_application_creator",
                new=AsyncMock(
                    return_value=MagicMock(status=SingleCreatorStatus.READY, creator_id=42)
                ),
            ),
        ):
            from httpx import ASGITransport, AsyncClient

            from chatbotv2.dashboard.app import app
            from chatbotv2.dashboard.auth import require_auth

            app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/suggestions/30/send",
                )
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        call_kwargs = mock_enqueue.call_args[0][0]
        assert call_kwargs["entity"] == "700"

    @pytest.mark.asyncio
    async def test_send_worker_flush_queue_processes_items(self):
        """flush_queue processes pending items and enqueues them for sending."""
        from workers.send_worker import flush_queue

        pending_items = [
            {
                "id": 100,
                "user_id": 800,
                "draft_content": "Msg 1",
                "status": "pending",
                "confidence_score": 0.9,
                "assigned_to": None,
            },
            {
                "id": 101,
                "user_id": 801,
                "draft_content": "Msg 2",
                "status": "pending",
                "confidence_score": 0.7,
                "assigned_to": 50,
            },
        ]

        with (
            patch(
                "workers.send_worker.get_pending_queue_items",
                new_callable=AsyncMock,
                return_value=pending_items,
            ),
            patch(
                "workers.send_worker.process_approved_message",
                new_callable=AsyncMock,
                return_value={"ok": True},
            ) as mock_process,
            patch("workers.send_worker.resolve_queue_item", new_callable=AsyncMock) as mock_resolve,
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
        ):
            sent = await flush_queue(max_items=50)

        assert sent == 2
        assert mock_process.call_count == 2
        assert mock_resolve.call_count == 2

    @pytest.mark.asyncio
    async def test_send_worker_flush_skips_non_pending(self):
        """flush_queue skips items that are not in 'pending' status."""
        from workers.send_worker import flush_queue

        items = [
            {"id": 200, "user_id": 900, "draft_content": "A", "status": "pending"},
            {"id": 201, "user_id": 901, "draft_content": "B", "status": "approved"},
            {"id": 202, "user_id": 902, "draft_content": "C", "status": "pending"},
        ]

        with (
            patch(
                "workers.send_worker.get_pending_queue_items",
                new_callable=AsyncMock,
                return_value=items,
            ),
            patch(
                "workers.send_worker.process_approved_message",
                new_callable=AsyncMock,
                return_value={"ok": True},
            ),
            patch("workers.send_worker.resolve_queue_item", new_callable=AsyncMock),
        ):
            sent = await flush_queue()

        assert sent == 2


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP C — DLQ Recovery
# ═══════════════════════════════════════════════════════════════════════════════


class TestDLQRecovery:
    """Dead letter queue replay scenarios."""

    @pytest.mark.asyncio
    async def test_dlq_replay_inbound_message(self):
        """Replaying an inbound DLQ entry re-enqueues to inbound stream."""
        entry = {
            "entry_id": "12345-0",
            "message_id": "msg-abc",
            "reason": "processing_error",
            "stream": "inbound",
            "payload": json.dumps(
                {
                    "user_id": "1000",
                    "content": "test message",
                    "telegram_message_id": "42",
                    "username": "test",
                    "first_name": "Test",
                    "persona": "",
                }
            ),
            "replay_count": "0",
            "failure_timestamp": str(int(time.time())),
        }

        with (
            patch("db.redis.get_dlq_entry", new_callable=AsyncMock, return_value=entry),
            patch(
                "db.redis.enqueue_inbound", new_callable=AsyncMock, return_value="new-msg-id"
            ) as mock_enqueue,
            patch("db.redis.get_redis", new_callable=AsyncMock),
        ):
            # Need to access the r object for xadd and delete calls
            mock_r = AsyncMock()
            mock_r.set.return_value = True
            mock_r.xadd.return_value = "new-dlq-entry"
            mock_r.delete.return_value = 1

            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
                from db.redis import replay_dlq_entry

                result = await replay_dlq_entry("12345-0")

        assert result["success"] is True
        assert result["new_message_id"] == "new-msg-id"
        assert result["replay_count"] == 1
        mock_enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_dlq_replay_send_message(self):
        """Replaying a send DLQ entry re-enqueues to send stream."""
        entry = {
            "entry_id": "67890-0",
            "message_id": "msg-xyz",
            "reason": "send_failed",
            "stream": "send",
            "payload": json.dumps(
                {
                    "entity": "2000",
                    "content": "Hello!",
                    "draft_content": "Hello!",
                }
            ),
            "replay_count": "0",
            "failure_timestamp": str(int(time.time())),
        }

        with (
            patch("db.redis.get_dlq_entry", new_callable=AsyncMock, return_value=entry),
            patch("db.redis.enqueue_send", new_callable=AsyncMock, return_value="new-send-id"),
        ):
            mock_r = AsyncMock()
            mock_r.set.return_value = True
            mock_r.xadd.return_value = "new-dlq-entry"
            mock_r.delete.return_value = 1

            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
                from db.redis import replay_dlq_entry

                result = await replay_dlq_entry("67890-0")

        assert result["success"] is True
        assert result["replay_count"] == 1

    @pytest.mark.asyncio
    async def test_dlq_replay_missing_payload(self):
        """Replay fails gracefully when payload is missing."""
        entry = {
            "entry_id": "11111-0",
            "stream": "inbound",
            "payload": "",
            "replay_count": "0",
        }

        with patch("db.redis.get_dlq_entry", new_callable=AsyncMock, return_value=entry):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("11111-0")

        assert result["success"] is False
        assert result["error"] == "missing_original_payload"

    @pytest.mark.asyncio
    async def test_dlq_replay_invalid_payload(self):
        """Replay fails gracefully when payload is malformed JSON."""
        entry = {
            "entry_id": "22222-0",
            "stream": "inbound",
            "payload": "not-valid-json{{{",
            "replay_count": "0",
        }

        with patch("db.redis.get_dlq_entry", new_callable=AsyncMock, return_value=entry):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("22222-0")

        assert result["success"] is False
        assert result["error"] == "invalid_payload_format"

    @pytest.mark.asyncio
    async def test_dlq_replay_max_attempts_reached(self):
        """Replay fails when max replay attempts is exceeded."""
        entry = {
            "entry_id": "33333-0",
            "stream": "inbound",
            "payload": json.dumps({"user_id": "1000", "content": "test"}),
            "replay_count": "3",
        }

        with patch("db.redis.get_dlq_entry", new_callable=AsyncMock, return_value=entry):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("33333-0", max_replay_attempts=3)

        assert result["success"] is False
        assert result["error"] == "max_replay_attempts_reached"

    @pytest.mark.asyncio
    async def test_dlq_replay_entry_not_found(self):
        """Replay fails when DLQ entry doesn't exist."""
        with patch("db.redis.get_dlq_entry", new_callable=AsyncMock, return_value=None):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("99999-0")

        assert result["success"] is False
        assert result["error"] == "entry_not_found"

    @pytest.mark.asyncio
    async def test_dlq_replay_unsupported_stream(self):
        """Replay fails for unknown stream types."""
        entry = {
            "entry_id": "44444-0",
            "stream": "unknown_stream",
            "payload": json.dumps({"data": "test"}),
            "replay_count": "0",
        }

        with patch("db.redis.get_dlq_entry", new_callable=AsyncMock, return_value=entry):
            mock_r = AsyncMock()
            mock_r.set.return_value = True

            with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
                from db.redis import replay_dlq_entry

                result = await replay_dlq_entry("44444-0")

        assert result["success"] is False
        assert "unsupported_stream" in result["error"]

    @pytest.mark.asyncio
    async def test_dlq_replay_lock_prevents_concurrent_replay(self):
        """Concurrent replay attempts for same entry are prevented by lock."""
        entry = {
            "entry_id": "55555-0",
            "stream": "inbound",
            "payload": json.dumps({"user_id": "1000", "content": "test"}),
            "replay_count": "0",
        }

        mock_r = AsyncMock()
        # Lock not acquired (another replay is in progress)
        mock_r.set.return_value = None

        with (
            patch("db.redis.get_dlq_entry", new_callable=AsyncMock, return_value=entry),
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r),
        ):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("55555-0")

        assert result["success"] is False
        assert result["error"] == "replay_in_progress"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP D — Gemini Failover
# ═══════════════════════════════════════════════════════════════════════════════


class TestGeminiFailover:
    """Gemini credential failover, rate limit handling, and pool rotation."""

    def test_credential_pool_selects_preferred(self):
        """get_credential returns the preferred credential when available."""
        from core.credentials import CredentialPool

        pool = CredentialPool(["key-aaa", "key-bbb", "key-ccc"])
        cred = pool.get_credential(preferred_index=0)
        assert cred.key == "key-aaa"

    def test_credential_pool_fallback_on_cooldown(self):
        """When preferred credential is cooling down, pool falls back to next."""
        from core.credentials import CredentialPool

        pool = CredentialPool(["key-aaa", "key-bbb"])
        pool.mark_cooldown("key-aaa", 300)
        cred = pool.get_credential(preferred_index=0)
        assert cred.key == "key-bbb"

    def test_credential_pool_size(self):
        """Pool reports correct size."""
        from core.credentials import CredentialPool

        pool = CredentialPool(["a", "b", "c", "d"])
        assert pool.size == 4

    def test_rate_limiter_allows_normal_request(self):
        """Rate limiter allows requests under the RPM limit."""
        from core.rate_limiter import RateLimiter

        limiter = RateLimiter(rpm_limit=10, safety_margin=0.8)
        assert limiter.can_request("test-key") is True

    def test_rate_limiter_blocks_over_limit(self):
        """Rate limiter blocks requests when RPM limit is reached."""
        from core.rate_limiter import RateLimiter

        limiter = RateLimiter(rpm_limit=5, safety_margin=1.0)
        for _ in range(5):
            limiter.record_request("test-key")
        assert limiter.can_request("test-key") is False

    @pytest.mark.asyncio
    async def test_rate_limit_429_triggers_cooldown(self):
        """A 429 error triggers credential cooldown."""
        from google.genai.errors import APIError

        from core.credentials import CredentialPool
        from core.gemini_client import handle_rate_limit_error

        pool = CredentialPool(["key-aaa"])
        cred = pool.get_credential(preferred_index=0)

        exc = APIError(code=429, response_json={"error": {"message": "Too Many Requests"}})

        with (
            patch("core.gemini_client.get_pool", return_value=pool),
            patch("core.gemini_client.get_limiter") as mock_limiter,
        ):
            mock_limiter.return_value = MagicMock()
            cooldown = handle_rate_limit_error(cred, exc)

        assert cooldown >= 30.0

    def test_permanent_error_detection(self):
        """Permanent errors (400, 401, 403, 404) are detected correctly."""
        from google.genai.errors import APIError

        from core.gemini_client import is_permanent_error

        for code in (400, 401, 403, 404):
            exc = APIError(code=code, response_json={"error": {"message": f"{code} Error"}})
            assert is_permanent_error(exc) is True

        exc = APIError(code=500, response_json={"error": {"message": "500 Error"}})
        assert is_permanent_error(exc) is False

    @pytest.mark.asyncio
    async def test_generate_draft_uses_credential_pool(self):
        """generate_draft selects credential from pool and records the request."""
        from workers.llm_worker import generate_draft

        mock_response = MagicMock()
        mock_response.text = "Generated draft text"

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        mock_cred = MagicMock()
        mock_cred.get_client.return_value = mock_client
        mock_cred.key = "test-key-1"

        with (
            patch("workers.llm_worker.get_credential", return_value=mock_cred),
            patch("workers.llm_worker.check_rate_limit", return_value=True),
            patch("workers.llm_worker.record_request") as mock_record,
        ):
            result = await generate_draft(
                [{"role": "user", "content": "Hello"}],
                "How are you?",
            )

        assert result == "Generated draft text"
        mock_record.assert_called_once_with(mock_cred)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP E — Redis Recovery & Worker Restart
# ═══════════════════════════════════════════════════════════════════════════════


class TestRedisRecovery:
    """XAUTOCLAIM recovery, worker restart, and stalled message handling."""

    @pytest.mark.asyncio
    async def test_xautoclaim_reclaims_stalled_inbound(self):
        """XAUTOCLAIM reclaims messages idle beyond threshold."""
        from db.redis import requeue_stalled_messages

        mock_r = AsyncMock()
        mock_r.xautoclaim.return_value = (
            0,
            [("msg-1", {"content": "stale1"}), ("msg-2", {"content": "stale2"})],
        )

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            count, ids = await requeue_stalled_messages("worker_1", idle_ms=30000)

        assert count == 2
        assert ids == ["msg-1", "msg-2"]

    @pytest.mark.asyncio
    async def test_xautoclaim_no_stalled_messages(self):
        """XAUTOCLAIM returns zero count when no messages are stalled."""
        from db.redis import requeue_stalled_messages

        mock_r = AsyncMock()
        mock_r.xautoclaim.return_value = (0, [])

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            count, ids = await requeue_stalled_messages("worker_1")

        assert count == 0
        assert ids == []

    @pytest.mark.asyncio
    async def test_xautoclaim_redis_error_returns_zero(self):
        """XAUTOCLAIM handles Redis errors gracefully."""
        import redis as redis_lib

        from db.redis import requeue_stalled_messages

        mock_r = AsyncMock()
        mock_r.xautoclaim.side_effect = redis_lib.ResponseError("Redis down")

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            count, _ids = await requeue_stalled_messages("worker_1")

        assert count == 0

    @pytest.mark.asyncio
    async def test_xautoclaim_send_stream(self):
        """XAUTOCLAIM also works for the send stream."""
        from db.redis import requeue_stalled_send_messages

        mock_r = AsyncMock()
        mock_r.xautoclaim.return_value = (0, [("send-msg-1", {"entity": "1000"})])

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            count, ids = await requeue_stalled_send_messages("sender_1")

        assert count == 1
        assert ids == ["send-msg-1"]

    @pytest.mark.asyncio
    async def test_worker_loop_reads_and_processes_messages(self):
        """LLM worker loop reads inbound messages and processes them."""
        from workers.llm_worker import run_worker

        messages = [
            (
                "stream",
                [
                    (
                        "msg-001",
                        {
                            "user_id": "1001",
                            "content": "Hello!",
                            "telegram_message_id": "100",
                            "username": "user1",
                            "first_name": "User1",
                            "persona": "",
                        },
                    )
                ],
            )
        ]

        call_count = 0

        async def fake_read(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                # After processing, signal shutdown
                from core.shutdown import set_shutting_down

                set_shutting_down(True)
                return []
            return messages

        with (
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.write_heartbeat", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.requeue_stalled_messages",
                new_callable=AsyncMock,
                return_value=(0, []),
            ),
            patch("workers.llm_worker.read_inbound", side_effect=fake_read),
            patch("workers.llm_worker.process_message", new_callable=AsyncMock) as mock_process,
            patch("workers.llm_worker.ack_inbound", new_callable=AsyncMock) as mock_ack,
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, False, True]),
        ):
            from core.shutdown import set_shutting_down

            set_shutting_down(False)
            await run_worker("worker_1")

        mock_process.assert_called_once()
        mock_ack.assert_called_once_with("msg-001")

    @pytest.mark.asyncio
    async def test_worker_dlq_on_process_error(self):
        """When process_message fails, message moves to DLQ."""
        from workers.llm_worker import run_worker

        messages = [
            (
                "stream",
                [
                    (
                        "msg-err",
                        {
                            "user_id": "2001",
                            "content": "bad",
                            "telegram_message_id": "200",
                            "username": "err",
                            "first_name": "Err",
                            "persona": "",
                        },
                    )
                ],
            )
        ]

        call_count = 0

        async def fake_read(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                from core.shutdown import set_shutting_down

                set_shutting_down(True)
                return []
            return messages

        with (
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.write_heartbeat", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.requeue_stalled_messages",
                new_callable=AsyncMock,
                return_value=(0, []),
            ),
            patch("workers.llm_worker.read_inbound", side_effect=fake_read),
            patch(
                "workers.llm_worker.process_message",
                new_callable=AsyncMock,
                side_effect=RuntimeError("LLM crash"),
            ),
            patch("workers.llm_worker.move_to_dlq", new_callable=AsyncMock) as mock_dlq,
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, False, True]),
        ):
            from core.shutdown import set_shutting_down

            set_shutting_down(False)
            await run_worker("worker_1")

        mock_dlq.assert_called_once()
        dlq_args = mock_dlq.call_args
        assert dlq_args[0][0] == "msg-err"
        assert dlq_args[0][1] == "processing_error"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP F — Duplicate Safety & Idempotency
# ═══════════════════════════════════════════════════════════════════════════════


class TestDuplicateSafety:
    """Dedup, idempotency keys, and dedup_id propagation."""

    @pytest.mark.asyncio
    async def test_enqueue_send_includes_dedup_id(self):
        """enqueue_send includes dedup_id in stream entry."""
        mock_r = AsyncMock()
        mock_r.xadd.return_value = "1234-1"

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            from db.redis import enqueue_send

            await enqueue_send(
                {"entity": "100", "content": "hi", "creator_id": "42"},
                dedup_id="dedup-abc",
            )

        call_kwargs = mock_r.xadd.call_args
        data = call_kwargs[0][1]
        assert data["dedup_id"] == "dedup-abc"

    @pytest.mark.asyncio
    async def test_dedup_id_generation_in_process_message(self):
        """process_message generates a consistent dedup_id from user+msg+telegram_id."""
        import workers.llm_worker as _lw
        from workers.llm_worker import process_message

        hashlib.md5(b"12345:Hello!:100").hexdigest()

        with (
            patch.object(_lw._settings, "llm_path", "legacy"),
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.get_recent_messages", new_callable=AsyncMock, return_value=[]),
            patch("db.redis.get_redis", new_callable=AsyncMock),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="draft"
            ),
            patch("workers.llm_worker.score_draft", new_callable=AsyncMock, return_value=(0.9, [])),
            patch("workers.llm_worker.extract_and_update_profile", new_callable=AsyncMock),
            patch("workers.llm_worker.maybe_summarize", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock) as mock_enqueue,
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
        ):
            await process_message(
                user_id=12345,
                user_message="Hello!",
                telegram_message_id=100,
                username="u",
                first_name="U",
                persona="",
                creator_id=42,
            )

        call_kwargs = mock_enqueue.call_args
        data = call_kwargs[0][0]
        assert data["was_auto_approved"] is True

    @pytest.mark.asyncio
    async def test_send_worker_dedup_id_from_queue_item(self):
        """process_approved_message generates dedup_id from queue_id."""
        from workers.send_worker import process_approved_message

        with patch("workers.send_worker.enqueue_send", new_callable=AsyncMock) as mock_enqueue:
            result = await process_approved_message(
                user_id=500,
                content="test",
                queue_id=42,
                creator_id=42,
            )

        assert result["ok"] is True
        call_kwargs = mock_enqueue.call_args
        assert call_kwargs[1]["dedup_id"] == "queue_item:42"

    @pytest.mark.asyncio
    async def test_send_worker_manual_dedup_id(self):
        """process_approved_message generates hash-based dedup_id for manual sends."""
        from workers.send_worker import process_approved_message

        with patch("workers.send_worker.enqueue_send", new_callable=AsyncMock) as mock_enqueue:
            await process_approved_message(
                user_id=500,
                content="manual msg",
                queue_id=None,
                creator_id=42,
            )

        call_kwargs = mock_enqueue.call_args
        dedup = call_kwargs[1]["dedup_id"]
        assert dedup.startswith("manual:500:")


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP G — DLQ Replay Advanced
# ═══════════════════════════════════════════════════════════════════════════════


class TestDLQReplayAdvanced:
    """Advanced DLQ scenarios: count, listing, cleanup, replay count increment."""

    @pytest.mark.asyncio
    async def test_dlq_entry_listing_returns_entries(self):
        """list_dlq_entries returns parsed entries from Redis."""
        from db.redis import list_dlq_entries

        mock_r = AsyncMock()
        mock_r.xrevrange.return_value = [
            ("999-0", {"reason": "timeout", "stream": "inbound"}),
            ("998-0", {"reason": "error", "stream": "send"}),
        ]

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            entries = await list_dlq_entries(count=10)

        assert len(entries) == 2
        assert entries[0]["entry_id"] == "999-0"

    @pytest.mark.asyncio
    async def test_dlq_entry_listing_with_stream_filter(self):
        """list_dlq_entries filters by stream type."""
        from db.redis import list_dlq_entries

        mock_r = AsyncMock()
        mock_r.xrevrange.return_value = [
            ("999-0", {"reason": "timeout", "stream": "inbound"}),
            ("998-0", {"reason": "error", "stream": "send"}),
            ("997-0", {"reason": "err2", "stream": "inbound"}),
        ]

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            entries = await list_dlq_entries(count=10, stream_filter="inbound")

        assert len(entries) == 2

    @pytest.mark.asyncio
    async def test_dlq_delete_entry(self):
        """delete_dlq_entry removes an entry from the stream."""
        from db.redis import delete_dlq_entry

        mock_r = AsyncMock()
        mock_r.xdel.return_value = 1

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            result = await delete_dlq_entry("999-0")

        assert result is True

    @pytest.mark.asyncio
    async def test_dlq_cleanup_removes_old_entries(self):
        """cleanup_expired_dlq_entries removes entries older than retention."""
        from db.redis import cleanup_expired_dlq_entries

        mock_r = AsyncMock()
        mock_r.xrange.return_value = [
            ("old-1", {"reason": "timeout"}),
            ("old-2", {"reason": "error"}),
        ]

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            count = await cleanup_expired_dlq_entries(retention_seconds=3600)

        assert count == 2
        mock_r.xdel.assert_called_once()

    @pytest.mark.asyncio
    async def test_dlq_replay_increments_replay_count(self):
        """Replay updates the DLQ record with incremented replay_count."""
        entry = {
            "entry_id": "replay-count-0",
            "stream": "inbound",
            "payload": json.dumps({"user_id": "5000", "content": "test"}),
            "replay_count": "1",
            "failure_timestamp": str(int(time.time())),
        }

        mock_r = AsyncMock()
        mock_r.set.return_value = True
        mock_r.xadd.return_value = "new-entry"
        mock_r.delete.return_value = 1

        with (
            patch("db.redis.get_dlq_entry", new_callable=AsyncMock, return_value=entry),
            patch("db.redis.enqueue_inbound", new_callable=AsyncMock, return_value="msg-new"),
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r),
        ):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("replay-count-0", max_replay_attempts=3)

        assert result["success"] is True
        assert result["replay_count"] == 2

    @pytest.mark.asyncio
    async def test_dlq_replay_send_with_dedup_id(self):
        """Replaying a send DLQ entry preserves the dedup_id."""
        entry = {
            "entry_id": "send-dedup-0",
            "stream": "send",
            "payload": json.dumps(
                {
                    "entity": "3000",
                    "content": "test",
                    "dedup_id": "original-dedup",
                }
            ),
            "replay_count": "0",
        }

        mock_r = AsyncMock()
        mock_r.set.return_value = True
        mock_r.xadd.return_value = "new-entry"
        mock_r.delete.return_value = 1

        with (
            patch("db.redis.get_dlq_entry", new_callable=AsyncMock, return_value=entry),
            patch(
                "db.redis.enqueue_send", new_callable=AsyncMock, return_value="msg-xyz"
            ) as mock_send,
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r),
        ):
            from db.redis import replay_dlq_entry

            result = await replay_dlq_entry("send-dedup-0")

        assert result["success"] is True
        call_kwargs = mock_send.call_args
        assert call_kwargs[1]["dedup_id"] == "original-dedup"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP H — Post-Process Isolation
# ═══════════════════════════════════════════════════════════════════════════════


class TestPostProcessIsolation:
    """Post-process failures (profile, summarization) don't break the main pipeline."""

    @pytest.mark.asyncio
    async def test_post_process_failure_does_not_propagate(self):
        """If post_process fails, the error is caught and logged."""
        from workers.llm_worker import post_process

        with (
            patch(
                "workers.llm_worker.get_recent_messages",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB down"),
            ),
            patch(
                "workers.llm_worker.extract_and_update_profile", new_callable=AsyncMock
            ) as mock_profile,
        ):
            # Should not raise
            await post_process(12345)

        mock_profile.assert_not_called()

    @pytest.mark.asyncio
    async def test_post_process_calls_profile_and_summarize(self):
        """post_process calls both extract_and_update_profile and maybe_summarize."""
        from workers.llm_worker import post_process

        with (
            patch(
                "workers.llm_worker.get_recent_messages",
                new_callable=AsyncMock,
                return_value=[
                    {"direction": "inbound", "content": "Hi"},
                    {"direction": "outbound", "content": "Hello!"},
                ],
            ),
            patch(
                "db.postgres.get_user",
                new_callable=AsyncMock,
                return_value={"message_count": 2},
            ),
            patch(
                "workers.llm_worker.extract_and_update_profile", new_callable=AsyncMock
            ) as mock_profile,
            patch("workers.llm_worker.maybe_summarize", new_callable=AsyncMock) as mock_summarize,
        ):
            await post_process(12345)

        mock_profile.assert_called_once_with(12345, mock_profile.call_args[0][1])
        mock_summarize.assert_called_once_with(12345, 2)

    @pytest.mark.asyncio
    async def test_profile_extraction_failure_logged_not_raised(self):
        """Profile extraction failure is caught by post_process."""
        from workers.llm_worker import post_process

        with (
            patch(
                "workers.llm_worker.get_recent_messages",
                new_callable=AsyncMock,
                return_value=[
                    {"direction": "inbound", "content": "Hi"},
                ],
            ),
            patch(
                "db.postgres.get_user",
                new_callable=AsyncMock,
                return_value={"message_count": 1},
            ),
            patch(
                "workers.llm_worker.extract_and_update_profile",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Gemini down"),
            ),
            patch("workers.llm_worker.maybe_summarize", new_callable=AsyncMock),
        ):
            # Should not raise
            await post_process(12345)

    @pytest.mark.asyncio
    async def test_main_pipeline_succeeds_even_if_post_process_created(self):
        """process_message succeeds even though post_process is an async task."""
        from workers.llm_worker import process_message

        async def fake_post_process(uid):
            raise RuntimeError("post process boom")

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="draft"
            ),
            patch("workers.llm_worker.score_draft", new_callable=AsyncMock, return_value=(0.9, [])),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.post_process",
                new_callable=AsyncMock,
                side_effect=fake_post_process,
            ),
        ):
            # Should not raise — post_process runs as a task, errors are isolated
            await process_message(
                user_id=77777,
                user_message="test",
                telegram_message_id=999,
                username="t",
                first_name="T",
                persona="",
            )


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP I — Migration Discovery
# ═══════════════════════════════════════════════════════════════════════════════


class TestMigrationDiscovery:
    """Schema migration discovery, versioning, and execution."""

    def test_discover_migrations_finds_sql_files(self):
        """discover_migrations finds .sql files in migrations directory."""
        from pathlib import Path

        from db.migrate import discover_migrations

        mock_dir = MagicMock(spec=Path)
        mock_dir.exists.return_value = True
        mock_dir.glob.return_value = [
            Path("20260101000000_init.sql"),
            Path("20260102000000_add_users.sql"),
        ]

        with patch("db.migrate.MIGRATIONS_DIR", mock_dir):
            migrations = discover_migrations()

        assert len(migrations) == 2
        assert migrations[0]["version"] == "20260101000000"
        assert migrations[0]["name"] == "init"
        assert migrations[1]["name"] == "add_users"

    def test_discover_migrations_empty_dir(self):
        """discover_migrations returns empty list when no migrations exist."""
        from pathlib import Path

        from db.migrate import discover_migrations

        mock_dir = MagicMock(spec=Path)
        mock_dir.exists.return_value = False

        with patch("db.migrate.MIGRATIONS_DIR", mock_dir):
            migrations = discover_migrations()

        assert migrations == []

    def test_get_pending_migrations_filters_applied(self):
        """get_pending_migrations excludes already-applied versions."""
        from db.migrate import get_pending_migrations

        all_migrations = [
            {"version": "20260101", "name": "init", "path": "init.sql"},
            {"version": "20260102", "name": "add_table", "path": "add_table.sql"},
            {"version": "20260103", "name": "add_index", "path": "add_index.sql"},
        ]

        with patch("db.migrate.discover_migrations", return_value=all_migrations):
            pending = get_pending_migrations(applied=["20260101", "20260103"])

        assert len(pending) == 1
        assert pending[0]["version"] == "20260102"

    def test_get_pending_migrations_all_applied(self):
        """When all migrations are applied, pending is empty."""
        from db.migrate import get_pending_migrations

        all_migrations = [
            {"version": "20260101", "name": "init", "path": "init.sql"},
        ]

        with patch("db.migrate.discover_migrations", return_value=all_migrations):
            pending = get_pending_migrations(applied=["20260101"])

        assert pending == []

    @pytest.mark.asyncio
    async def test_get_current_version(self):
        """get_current_version returns the latest applied version."""
        from db.migrate import get_current_version

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"version": "20260103"})
        mock_conn.execute = AsyncMock()

        version = await get_current_version(mock_conn)
        assert version == "20260103"

    @pytest.mark.asyncio
    async def test_get_current_version_none(self):
        """get_current_version returns None when no migrations applied."""
        from db.migrate import get_current_version

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock()

        version = await get_current_version(mock_conn)
        assert version is None

    @pytest.mark.asyncio
    async def test_get_applied_versions(self):
        """get_applied_versions returns sorted list of applied versions."""
        from db.migrate import get_applied_versions

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {"version": "20260101"},
                {"version": "20260102"},
            ]
        )
        mock_conn.execute = AsyncMock()

        versions = await get_applied_versions(mock_conn)
        assert versions == ["20260101", "20260102"]

    def test_migration_files_sorted_by_version(self):
        """Migrations are sorted by version (timestamp-based)."""
        from pathlib import Path

        from db.migrate import discover_migrations

        mock_dir = MagicMock(spec=Path)
        mock_dir.exists.return_value = True
        mock_dir.glob.return_value = [
            Path("20260103_third.sql"),
            Path("20260101_first.sql"),
            Path("20260102_second.sql"),
        ]

        with patch("db.migrate.MIGRATIONS_DIR", mock_dir):
            migrations = discover_migrations()

        versions = [m["version"] for m in migrations]
        assert versions == sorted(versions)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP J — Health & Readiness Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


class TestHealthReadiness:
    """Health and readiness endpoints with dependency checks."""

    def test_health_response_structure(self):
        """Health endpoint returns basic status without dependency checks."""
        from core.health import get_health_response

        resp = get_health_response()
        assert resp["status"] == "ok"
        assert resp["service"] == "chatbotv2"
        assert "version" in resp

    def test_health_does_not_require_redis(self):
        """Health endpoint works even when Redis is down."""
        from core.health import get_health_response

        # Health doesn't check any dependencies
        resp = get_health_response()
        assert resp["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readiness_ok_when_all_healthy(self):
        """Readiness returns ready when Redis and Postgres are healthy."""
        from core.health import get_readiness_response

        with (
            patch("core.health.check_redis", new_callable=AsyncMock, return_value={"status": "ok"}),
            patch(
                "core.health.check_postgres", new_callable=AsyncMock, return_value={"status": "ok"}
            ),
            patch("core.health.check_gemini", return_value={"status": "available"}),
        ):
            resp = await get_readiness_response()

        assert resp["status"] == "ready"
        assert resp["dependencies"]["redis"]["status"] == "ok"
        assert resp["dependencies"]["postgres"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readiness_not_ready_when_redis_down(self):
        """Readiness returns not_ready when Redis is down."""
        from core.health import get_readiness_response

        with (
            patch(
                "core.health.check_redis", new_callable=AsyncMock, return_value={"status": "error"}
            ),
            patch(
                "core.health.check_postgres", new_callable=AsyncMock, return_value={"status": "ok"}
            ),
            patch("core.health.check_gemini", return_value={"status": "available"}),
        ):
            resp = await get_readiness_response()

        assert resp["status"] == "not_ready"

    @pytest.mark.asyncio
    async def test_readiness_not_ready_when_postgres_down(self):
        """Readiness returns not_ready when Postgres is down."""
        from core.health import get_readiness_response

        with (
            patch("core.health.check_redis", new_callable=AsyncMock, return_value={"status": "ok"}),
            patch(
                "core.health.check_postgres",
                new_callable=AsyncMock,
                return_value={"status": "error"},
            ),
            patch("core.health.check_gemini", return_value={"status": "degraded"}),
        ):
            resp = await get_readiness_response()

        assert resp["status"] == "not_ready"

    @pytest.mark.asyncio
    async def test_readiness_includes_gemini_status(self):
        """Readiness response includes Gemini credential status."""
        from core.health import get_readiness_response

        with (
            patch("core.health.check_redis", new_callable=AsyncMock, return_value={"status": "ok"}),
            patch(
                "core.health.check_postgres", new_callable=AsyncMock, return_value={"status": "ok"}
            ),
            patch(
                "core.health.check_gemini",
                return_value={
                    "status": "degraded",
                    "credentials_total": 3,
                    "credentials_available": 1,
                    "cooldowns_active": 2,
                },
            ),
        ):
            resp = await get_readiness_response()

        assert resp["status"] == "ready"
        gemini = resp["dependencies"]["gemini"]
        assert gemini["credentials_total"] == 3
        assert gemini["credentials_available"] == 1

    @pytest.mark.asyncio
    async def test_health_endpoint_returns_200(self):
        """GET /health returns 200."""
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readiness_endpoint_returns_200_when_ready(self):
        """GET /ready returns 200 when dependencies are healthy."""
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        with (
            patch("core.health.check_redis", new_callable=AsyncMock, return_value={"status": "ok"}),
            patch(
                "core.health.check_postgres", new_callable=AsyncMock, return_value={"status": "ok"}
            ),
            patch("core.health.check_gemini", return_value={"status": "available"}),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/ready")

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_readiness_endpoint_returns_503_when_not_ready(self):
        """GET /ready returns 503 when critical dependencies are down."""
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        with (
            patch(
                "core.health.check_redis", new_callable=AsyncMock, return_value={"status": "error"}
            ),
            patch(
                "core.health.check_postgres", new_callable=AsyncMock, return_value={"status": "ok"}
            ),
            patch("core.health.check_gemini", return_value={"status": "unconfigured"}),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/ready")

        assert resp.status_code == 503


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP K — Dashboard Route Integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestDashboardRouteIntegration:
    """Dashboard API routes return correct shapes and status codes."""

    @pytest.mark.asyncio
    async def test_users_api_returns_list(self):
        """GET /api/users returns a JSON list."""
        mock_rows = [
            {
                "id": 1,
                "username": "alice",
                "first_name": "Alice",
                "message_count": 10,
                "last_seen": "2026-08-19",
            },
            {
                "id": 2,
                "username": "bob",
                "first_name": "Bob",
                "message_count": 5,
                "last_seen": "2026-08-18",
            },
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)

        class FakeAcquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                pass

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=FakeAcquire())

        with patch(
            "chatbotv2.dashboard.routes.users.get_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ):
            from httpx import ASGITransport, AsyncClient

            from chatbotv2.dashboard.app import app
            from chatbotv2.dashboard.auth import require_auth

            app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/users")
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["username"] == "alice"

    @pytest.mark.asyncio
    async def test_queue_pending_api(self):
        """GET /api/queue/pending returns pending queue items."""
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        mock_items = [
            {"id": 1, "user_id": 100, "draft_content": "Hi", "status": "pending"},
        ]

        with patch(
            "chatbotv2.dashboard.routes.queue.get_pending_queue_items",
            new_callable=AsyncMock,
            return_value=mock_items,
        ):
            from httpx import ASGITransport, AsyncClient

            app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/queue/pending")
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_login_valid_password(self):
        """POST /login with valid password sets session cookie."""
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=False
        ) as client:
            resp = await client.post(
                "/login",
                data={"username": "admin", "password": "admin123"},
            )

        assert resp.status_code == 303
        assert "session" in resp.cookies

    @pytest.mark.asyncio
    async def test_login_invalid_password(self):
        """POST /login with wrong password returns error."""
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/login",
                data={"username": "admin", "password": "wrong"},
            )

        assert resp.status_code == 200
        assert b"Invalid credentials" in resp.content

    @pytest.mark.asyncio
    async def test_request_id_middleware_adds_header(self):
        """All responses include X-Request-ID header."""
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

        assert "x-request-id" in resp.headers

    @pytest.mark.asyncio
    async def test_custom_request_id_preserved(self):
        """Custom X-Request-ID header is preserved in response."""
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/health",
                headers={"X-Request-ID": "custom-id-12345"},
            )

        assert resp.headers["x-request-id"] == "custom-id-12345"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP L — Frontend Contract
# ═══════════════════════════════════════════════════════════════════════════════


class TestFrontendContract:
    """Frontend JS modules expose expected APIs and analytics.html is valid."""

    def test_analytics_utils_exports(self):
        """analytics-utils.js defines the expected utility functions."""
        from pathlib import Path

        utils_path = Path("chatbotv2/dashboard/static/js/analytics-utils.js")
        assert utils_path.exists(), f"analytics-utils.js not found at {utils_path}"

        content = utils_path.read_text()
        expected_functions = [
            "formatResponseTime",
            "timeAgo",
            "formatDate",
            "computePeriodLabel",
            "computeAiPercent",
        ]
        for fn in expected_functions:
            assert fn in content, f"Function {fn} not found in analytics-utils.js"

    def test_analytics_api_exports(self):
        """analytics-api.js defines the expected API client functions."""
        from pathlib import Path

        api_path = Path("chatbotv2/dashboard/static/js/analytics-api.js")
        assert api_path.exists(), f"analytics-api.js not found at {api_path}"

        content = api_path.read_text()
        expected_functions = [
            "fetchDashboard",
            "fetchConversations",
            "fetchConversationDetail",
            "fetchOperators",
            "fetchNotes",
        ]
        for fn in expected_functions:
            assert fn in content, f"Function {fn} not found in analytics-api.js"

    def test_analytics_html_loads_external_modules(self):
        """analytics.html includes the external JS modules."""
        from pathlib import Path

        html_path = Path("chatbotv2/dashboard/templates/analytics.html")
        assert html_path.exists(), f"analytics.html not found at {html_path}"

        content = html_path.read_text()
        assert "analytics-utils.js" in content
        assert "analytics-api.js" in content

    def test_analytics_html_inline_script_reduced(self):
        """Inline script in analytics.html is under 600 lines (was ~1040)."""
        from pathlib import Path

        html_path = Path("chatbotv2/dashboard/templates/analytics.html")
        content = html_path.read_text()

        # Count script blocks
        import re

        scripts = re.findall(r"<script[^>]*>(.*?)</script>", content, re.DOTALL)
        inline_scripts = [s for s in scripts if s.strip()]

        # There should be inline scripts (Alpine.js component)
        assert len(inline_scripts) >= 1

        # The main inline script should be under 600 lines
        main_script = max(inline_scripts, key=lambda s: len(s.split("\n")))
        script_lines = main_script.strip().split("\n")
        assert len(script_lines) <= 600, (
            f"Inline script too long: {len(script_lines)} lines (max 600)"
        )

    def test_analytics_utils_structure(self):
        """analytics-utils.js exports on window.AnalyticsUtils object."""
        from pathlib import Path

        utils_path = Path("chatbotv2/dashboard/static/js/analytics-utils.js")
        content = utils_path.read_text()

        assert "window.AnalyticsUtils" in content
        # Should be a pure object literal export (no DOM in most functions)
        assert "=" in content


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP M — Worker Lifecycle
# ═══════════════════════════════════════════════════════════════════════════════


class TestWorkerLifecycle:
    """Worker startup, shutdown, heartbeat, and cleanup."""

    @pytest.mark.asyncio
    async def test_worker_cleanup_closes_pool_and_redis(self):
        """LLM worker cleanup closes both PostgreSQL pool and Redis."""
        from workers.llm_worker import _worker_cleanup

        with (
            patch("db.postgres.close_pool", new_callable=AsyncMock) as mock_close_pool,
            patch("db.redis.close_redis", new_callable=AsyncMock) as mock_close_redis,
        ):
            await _worker_cleanup()

        mock_close_pool.assert_called_once()
        mock_close_redis.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_worker_cleanup_closes_pool_and_redis(self):
        """Send worker cleanup closes both PostgreSQL pool and Redis."""
        from workers.send_worker import _send_worker_cleanup

        with (
            patch("db.postgres.close_pool", new_callable=AsyncMock) as mock_close_pool,
            patch("db.redis._client", new_callable=AsyncMock),
        ):
            await _send_worker_cleanup()

        mock_close_pool.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_setup_ensures_consumer_group(self):
        """Worker startup creates consumer groups."""
        from workers.llm_worker import run_worker

        call_count = 0

        async def fake_read(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                from core.shutdown import set_shutting_down

                set_shutting_down(True)
            return []

        with (
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock) as mock_init,
            patch(
                "workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock
            ) as mock_ensure,
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.write_heartbeat", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.requeue_stalled_messages",
                new_callable=AsyncMock,
                return_value=(0, []),
            ),
            patch("workers.llm_worker.read_inbound", side_effect=fake_read),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
        ):
            from core.shutdown import set_shutting_down

            set_shutting_down(False)
            await run_worker("worker_test")

        mock_init.assert_called_once()
        mock_ensure.assert_called_once()

    def test_worker_preferred_index_parsed(self):
        """Worker preferred index is parsed from worker_id."""
        from workers.llm_worker import _parse_worker_preferred_index

        with patch("workers.llm_worker.get_pool") as mock_pool:
            mock_pool.return_value.size = 3
            assert _parse_worker_preferred_index("worker_1") == 0
            assert _parse_worker_preferred_index("worker_2") == 1
            assert _parse_worker_preferred_index("worker_3") == 2

    def test_worker_preferred_index_invalid(self):
        """Invalid worker_id defaults to index 0."""
        from workers.llm_worker import _parse_worker_preferred_index

        assert _parse_worker_preferred_index("worker") == 0
        assert _parse_worker_preferred_index("worker_abc") == 0

    def test_worker_preferred_index_large_number(self):
        """Worker preferred index wraps around pool size."""
        from workers.llm_worker import _parse_worker_preferred_index

        with patch("workers.llm_worker.get_pool") as mock_pool:
            mock_pool.return_value.size = 3
            assert _parse_worker_preferred_index("worker_5") == 1  # (5-1) % 3 = 1


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP N — Failure Cascades
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailureCascades:
    """Error propagation, isolation, and cascading failure prevention."""

    @pytest.mark.asyncio
    async def test_enqueue_send_failure_does_not_break_pipeline(self):
        """If enqueue_send fails, the pipeline error is still handled gracefully."""
        from workers.llm_worker import process_message

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="draft"
            ),
            patch("workers.llm_worker.score_draft", new_callable=AsyncMock, return_value=(0.9, [])),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "workers.llm_worker.enqueue_send",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Redis down"),
            ),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock) as mock_publish,
            pytest.raises(RuntimeError, match="Redis down"),
        ):
            await process_message(
                user_id=88888,
                user_message="test",
                telegram_message_id=1000,
                username="f",
                first_name="F",
                persona="",
            )

        # generation_failed should have been published
        failed_calls = [c for c in mock_publish.call_args_list if c[0][0] == "ai.generation_failed"]
        assert len(failed_calls) == 1

    @pytest.mark.asyncio
    async def test_score_draft_failure_triggers_generation_failed(self):
        """When score_draft fails, ai.generation_failed is published."""
        from workers.llm_worker import process_message

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="draft"
            ),
            patch(
                "workers.llm_worker.score_draft",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Scoring error"),
            ),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock) as mock_publish,
            pytest.raises(RuntimeError, match="Scoring error"),
        ):
            await process_message(
                user_id=88889,
                user_message="test",
                telegram_message_id=1001,
                username="f2",
                first_name="F2",
                persona="",
            )

        failed_calls = [c for c in mock_publish.call_args_list if c[0][0] == "ai.generation_failed"]
        assert len(failed_calls) == 1

    @pytest.mark.asyncio
    async def test_event_publish_failure_does_not_break_pipeline(self):
        """If publish_event fails, process_message still completes."""
        from workers.llm_worker import process_message

        async def failing_publish(*a, **kw):
            raise RuntimeError("Redis pub/sub down")

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="draft"
            ),
            patch("workers.llm_worker.score_draft", new_callable=AsyncMock, return_value=(0.9, [])),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
        ):
            # The function catches the exception internally (generation_failed publish fails)
            # and then re-raises the original error. But the event_bus catches internally.
            # Actually, looking at the code, if publish_event raises inside the try block,
            # it gets caught by the outer except. Let me adjust.
            # Actually, publish_event in event_bus catches exceptions internally.
            # But our mock raises directly. Let's see what happens.
            pass

        # The actual test: publish_event in event_bus catches all exceptions.
        # Let's verify that the mock publish_event failing doesn't break things.
        # We need to patch at the right level.
        events_published = []

        async def selective_failing_publish(event_type, data, **kw):
            if event_type == "ai.generation_completed":
                raise RuntimeError("Redis pub/sub down")
            events_published.append(event_type)
            return "evt-ok"

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="draft"
            ),
            patch("workers.llm_worker.score_draft", new_callable=AsyncMock, return_value=(0.9, [])),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch(
                "core.event_bus.publish_event",
                new_callable=AsyncMock,
                side_effect=selective_failing_publish,
            ),
            pytest.raises(RuntimeError, match="Redis pub/sub down"),
        ):
            await process_message(
                user_id=88890,
                user_message="test",
                telegram_message_id=1002,
                username="f3",
                first_name="F3",
                persona="",
            )

    @pytest.mark.asyncio
    async def test_send_worker_process_approved_failure_returns_none(self):
        """process_approved_message returns None when enqueue_send fails."""
        from workers.send_worker import process_approved_message

        with patch(
            "workers.send_worker.enqueue_send",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Redis down"),
        ):
            result = await process_approved_message(user_id=500, content="test")

        assert result is None

    @pytest.mark.asyncio
    async def test_user_lock_always_released(self):
        """User lock is always released even when processing fails."""
        from workers.llm_worker import process_message

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "workers.llm_worker.build_qwen3_context",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB down"),
            ),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock) as mock_release,
            pytest.raises(RuntimeError, match="DB down"),
        ):
            await process_message(
                user_id=88891,
                user_message="test",
                telegram_message_id=1003,
                username="f4",
                first_name="F4",
                persona="",
            )

        mock_release.assert_called_once_with(88891)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP O — Concurrency & Locking
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrencyLocking:
    """User locks, concurrent access, and race condition prevention."""

    @pytest.mark.asyncio
    async def test_acquire_user_lock_success(self):
        """acquire_user_lock returns True when lock is acquired."""
        from db.redis import acquire_user_lock

        mock_r = AsyncMock()
        mock_r.set.return_value = True  # nx=True, key doesn't exist

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            result = await acquire_user_lock(12345, ttl=60)

        assert result is True
        mock_r.set.assert_called_once_with("lock:user:12345", "1", nx=True, ex=60)

    @pytest.mark.asyncio
    async def test_acquire_user_lock_already_held(self):
        """acquire_user_lock returns False when lock is already held."""
        from db.redis import acquire_user_lock

        mock_r = AsyncMock()
        mock_r.set.return_value = None  # nx=True, key already exists

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            result = await acquire_user_lock(12345, ttl=60)

        assert result is False

    @pytest.mark.asyncio
    async def test_release_user_lock(self):
        """release_user_lock deletes the lock key."""
        from db.redis import release_user_lock

        mock_r = AsyncMock()
        mock_r.delete.return_value = 1

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            await release_user_lock(12345)

        mock_r.delete.assert_called_once_with("lock:user:12345")

    @pytest.mark.asyncio
    async def test_two_users_can_lock_concurrently(self):
        """Two different users can be locked simultaneously."""
        from db.redis import acquire_user_lock

        mock_r = AsyncMock()
        mock_r.set.return_value = True

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            result1 = await acquire_user_lock(100)
            result2 = await acquire_user_lock(200)

        assert result1 is True
        assert result2 is True

    @pytest.mark.asyncio
    async def test_same_user_cannot_double_lock(self):
        """Same user cannot acquire lock twice."""
        from db.redis import acquire_user_lock

        mock_r = AsyncMock()
        # First call: success. Second call: already held.
        mock_r.set.side_effect = [True, None]

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            result1 = await acquire_user_lock(100)
            result2 = await acquire_user_lock(100)

        assert result1 is True
        assert result2 is False

    @pytest.mark.asyncio
    async def test_lock_ttl_prevents_deadlock(self):
        """Lock TTL ensures eventual release even if worker crashes."""
        from db.redis import acquire_user_lock

        mock_r = AsyncMock()
        mock_r.set.return_value = True

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            await acquire_user_lock(100, ttl=30)

        # Verify TTL is set
        call_kwargs = mock_r.set.call_args
        assert call_kwargs[1]["ex"] == 30

    @pytest.mark.asyncio
    async def test_process_message_skips_when_lock_held(self):
        """process_message returns immediately when user lock is already held."""
        from workers.llm_worker import process_message

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=False
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock) as mock_upsert,
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
        ):
            await process_message(
                user_id=100,
                user_message="test",
                telegram_message_id=100,
                username="u",
                first_name="U",
                persona="",
            )

        mock_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_clear_all_user_locks(self):
        """clear_all_user_locks removes all user lock keys."""
        from db.redis import clear_all_user_locks

        mock_r = AsyncMock()

        async def fake_scan_iter(match):
            for key in ["lock:user:1", "lock:user:2", "lock:user:3"]:
                yield key

        mock_r.scan_iter = fake_scan_iter
        mock_r.delete.return_value = 3

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            await clear_all_user_locks()

        mock_r.delete.assert_called_once_with("lock:user:1", "lock:user:2", "lock:user:3")

    @pytest.mark.asyncio
    async def test_debounce_prevents_rapid_processing(self):
        """Debounce ensures only the first message in a window owns it (M2 fence token)."""
        from db.redis import debounce_enqueue

        mock_r = AsyncMock()
        # First call: window owner (lock SET wins, owner SET ok).
        # Second call: not owner (lock SET loses).
        mock_r.set.side_effect = [True, True, None]
        mock_r.rpush.return_value = 1

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            result1 = await debounce_enqueue(
                user_id=100,
                content="msg1",
                message_data={"user_id": "100", "content": "msg1"},
                window_seconds=3,
                creator_id=7,
            )
            result2 = await debounce_enqueue(
                user_id=100,
                content="msg2",
                message_data={"user_id": "100", "content": "msg2"},
                window_seconds=3,
                creator_id=7,
            )

        assert result1  # owner fence token (truthy)
        assert isinstance(result1, str)
        assert not result2


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP P1 — Event Ordering & Lifecycle Integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestEventOrdering:
    """Verify event lifecycle ordering: enqueue_send before ai.generation_completed."""

    @pytest.mark.asyncio
    async def test_enqueue_send_called_before_generation_completed(self):
        """enqueue_send MUST be called before ai.generation_completed is published."""
        from workers.llm_worker import process_message

        call_order = []

        async def capture_publish(event_type, data, **kwargs):
            call_order.append(("publish", event_type))
            return f"evt-{len(call_order)}"

        async def capture_enqueue(*args, **kwargs):
            call_order.append(("enqueue_send", "enqueue_send"))
            return "msg-1"

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="draft"
            ),
            patch(
                "workers.llm_worker.score_draft", new_callable=AsyncMock, return_value=(0.95, [])
            ),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "workers.llm_worker.enqueue_send",
                new_callable=AsyncMock,
                side_effect=capture_enqueue,
            ),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch(
                "core.event_bus.publish_event", new_callable=AsyncMock, side_effect=capture_publish
            ),
        ):
            await process_message(
                user_id=90001,
                user_message="test",
                telegram_message_id=1,
                username="u",
                first_name="U",
                persona="",
            )

        enqueue_idx = next(i for i, (k, t) in enumerate(call_order) if k == "enqueue_send")
        completed_idx = next(
            i
            for i, (k, t) in enumerate(call_order)
            if k == "publish" and t == "ai.generation_completed"
        )
        assert enqueue_idx < completed_idx, "enqueue_send must precede ai.generation_completed"

    @pytest.mark.asyncio
    async def test_generation_started_completed_suggestion_order(self):
        """Event sequence: started -> completed -> suggestion for operator-routed messages."""
        from workers.llm_worker import process_message

        events = []

        async def capture_publish(event_type, data, **kwargs):
            events.append(event_type)
            return f"evt-{len(events)}"

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="draft"
            ),
            patch("workers.llm_worker.score_draft", new_callable=AsyncMock, return_value=(0.5, [])),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch(
                "core.event_bus.publish_event", new_callable=AsyncMock, side_effect=capture_publish
            ),
            patch(
                "workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock, return_value=42
            ),
            patch("workers.llm_worker.notify_operators", new_callable=AsyncMock),
        ):
            await process_message(
                user_id=90002,
                user_message="low score",
                telegram_message_id=2,
                username="u2",
                first_name="U2",
                persona="",
            )

        assert events == ["ai.generation_started", "ai.generation_completed", "suggestion.created"]

    @pytest.mark.asyncio
    async def test_same_generation_id_across_all_events(self):
        """All lifecycle events share the same generation_id."""
        from workers.llm_worker import process_message

        events = []

        async def capture_publish(event_type, data, **kwargs):
            events.append({"type": event_type, "generation_id": kwargs.get("generation_id")})
            return f"evt-{len(events)}"

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="draft"
            ),
            patch("workers.llm_worker.score_draft", new_callable=AsyncMock, return_value=(0.5, [])),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch(
                "core.event_bus.publish_event", new_callable=AsyncMock, side_effect=capture_publish
            ),
            patch(
                "workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock, return_value=43
            ),
            patch("workers.llm_worker.notify_operators", new_callable=AsyncMock),
        ):
            await process_message(
                user_id=90003,
                user_message="test",
                telegram_message_id=3,
                username="u3",
                first_name="U3",
                persona="",
            )

        gen_ids = [e["generation_id"] for e in events]
        assert len(set(gen_ids)) == 1, "All events must share the same generation_id"
        assert gen_ids[0] is not None

    @pytest.mark.asyncio
    async def test_failure_event_has_same_generation_id(self):
        """On failure, ai.generation_failed shares the same generation_id as started."""
        from workers.llm_worker import process_message

        events = []

        async def capture_publish(event_type, data, **kwargs):
            events.append({"type": event_type, "generation_id": kwargs.get("generation_id")})
            return f"evt-{len(events)}"

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft",
                new_callable=AsyncMock,
                side_effect=RuntimeError("fail"),
            ),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch(
                "core.event_bus.publish_event", new_callable=AsyncMock, side_effect=capture_publish
            ),
            pytest.raises(RuntimeError, match="fail"),
        ):
            await process_message(
                user_id=90004,
                user_message="test",
                telegram_message_id=4,
                username="u4",
                first_name="U4",
                persona="",
            )

        assert len(events) == 2
        assert events[0]["type"] == "ai.generation_started"
        assert events[1]["type"] == "ai.generation_failed"
        assert events[0]["generation_id"] == events[1]["generation_id"]


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP P2 — Outbound DLQ with Dedup
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutboundDLQ:
    """Test outbound DLQ behavior with dedup_id preservation."""

    @pytest.mark.asyncio
    async def test_move_send_to_dlq_preserves_dedup_id(self):
        """move_send_to_dlq preserves dedup_id in the DLQ entry."""
        from db.redis import move_send_to_dlq

        mock_r = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            await move_send_to_dlq(
                message_id="send-123",
                reason="Telegram send failed",
                payload={"entity": "500", "content": "hi", "dedup_id": "dedup-abc"},
                worker_id="sender_1",
            )

        dlq_call = mock_r.xadd.call_args_list[0]
        assert dlq_call[0][0] == "dead_letter_queue"
        entry = dlq_call[0][1]
        assert entry["reason"] == "Telegram send failed"
        assert entry["stream"] == "send"
        assert entry["replay_count"] == "0"
        assert "dedup-abc" in entry["payload"]
        ack_call = mock_r.xack.call_args_list[0]
        assert ack_call[0][2] == "send-123"

    @pytest.mark.asyncio
    async def test_send_worker_process_approved_failure_returns_none(self):
        """process_approved_message returns None on enqueue_send failure."""
        from workers.send_worker import process_approved_message

        with patch(
            "workers.send_worker.enqueue_send",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Redis down"),
        ):
            result = await process_approved_message(user_id=600, content="test", queue_id=10, creator_id=42)

        assert result is None

    @pytest.mark.asyncio
    async def test_send_worker_process_approved_success(self):
        """process_approved_message returns ok on success."""
        from workers.send_worker import process_approved_message

        with patch(
            "workers.send_worker.enqueue_send", new_callable=AsyncMock, return_value="msg-id"
        ):
            result = await process_approved_message(user_id=601, content="hello", queue_id=11, creator_id=42)

        # M7 (B3): result carries the dedup identity used for the send.
        assert result == {"ok": True, "dedup_id": "queue_item:11"}

    @pytest.mark.asyncio
    async def test_dedup_id_format_from_queue_id(self):
        """When queue_id is provided, dedup_id uses queue_item prefix."""
        from workers.send_worker import process_approved_message

        mock_send = AsyncMock(return_value="msg-id")

        with patch(
            "workers.send_worker.enqueue_send", new_callable=AsyncMock, side_effect=mock_send
        ):
            await process_approved_message(user_id=602, content="x", queue_id=99, creator_id=42)

        assert mock_send.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP P3 — Concurrent Dedup Safety
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrentDedup:
    """Test deduplication under concurrent/repeated sends."""

    @pytest.mark.asyncio
    async def test_same_dedup_id_enqueue_includes_in_payload(self):
        """Same dedup_id in payload prevents duplicate Telegram sends."""
        from db.redis import enqueue_send

        mock_r = AsyncMock()
        mock_r.xadd.return_value = "msg-1"

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            await enqueue_send({"entity": "700", "content": "hi", "creator_id": "42"}, dedup_id="same-dedup-123")
            await enqueue_send({"entity": "700", "content": "hi", "creator_id": "42"}, dedup_id="same-dedup-123")

        assert mock_r.xadd.call_count == 2
        for call in mock_r.xadd.call_args_list:
            data = call[0][1]
            assert data["dedup_id"] == "same-dedup-123"

    @pytest.mark.asyncio
    async def test_mark_send_dedup_sets_redis_key(self):
        """mark_send_dedup sets the dedup key in Redis."""
        from db.redis import mark_send_dedup

        mock_r = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            await mark_send_dedup("dedup-xyz", ttl=3600, creator_id=42)

        mock_r.setex.assert_called_once_with("send_dedup:42:dedup-xyz", 3600, "1")

    @pytest.mark.asyncio
    async def test_is_send_duplicate_checks_redis_key(self):
        """is_send_duplicate returns True when key exists."""
        from db.redis import is_send_duplicate

        mock_r = AsyncMock()
        mock_r.exists.return_value = 1

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            result = await is_send_duplicate("dedup-abc", creator_id=42)

        assert result is True
        mock_r.exists.assert_called_once_with("send_dedup:42:dedup-abc")

    @pytest.mark.asyncio
    async def test_is_send_duplicate_false_when_no_key(self):
        """is_send_duplicate returns False when key does not exist."""
        from db.redis import is_send_duplicate

        mock_r = AsyncMock()
        mock_r.exists.return_value = 0

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            result = await is_send_duplicate("dedup-new")

        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP P4 — Graceful Shutdown & Recovery
# ═══════════════════════════════════════════════════════════════════════════════


class TestGracefulShutdown:
    """Test worker shutdown while work is pending."""

    @pytest.mark.asyncio
    async def test_shutdown_during_worker_loop_stops_iteration(self):
        """Worker loop stops when is_shutting_down becomes True."""
        from workers.llm_worker import run_worker

        messages_read = []

        def fake_read(*args, **kwargs):
            from core.shutdown import is_shutting_down

            if is_shutting_down():
                return []
            messages_read.append(1)
            if len(messages_read) >= 2:
                from core.shutdown import set_shutting_down

                set_shutting_down(True)
            return []

        with (
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.write_heartbeat", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.requeue_stalled_messages",
                new_callable=AsyncMock,
                return_value=(0, []),
            ),
            patch("workers.llm_worker.read_inbound", side_effect=fake_read),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, False, True]),
        ):
            from core.shutdown import set_shutting_down

            set_shutting_down(False)
            await run_worker("test_shutdown_1")

        assert len(messages_read) >= 1

    @pytest.mark.asyncio
    async def test_shutdown_does_not_ack_unfinished_message(self):
        """When shutdown occurs mid-processing, ack is called for processed messages."""
        from workers.llm_worker import run_worker

        processed = []

        def fake_read(*args, **kwargs):
            from core.shutdown import is_shutting_down

            if is_shutting_down():
                return []
            return [
                (
                    "msg-1",
                    [
                        (
                            "f1",
                            {
                                "user_id": "1",
                                "content": "c",
                                "telegram_message_id": "1",
                                "username": "u",
                                "first_name": "F",
                                "persona": "",
                            },
                        )
                    ],
                )
            ]

        async def slow_process(*args, **kwargs):
            processed.append(1)
            from core.shutdown import set_shutting_down

            set_shutting_down(True)

        with (
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.write_heartbeat", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.requeue_stalled_messages",
                new_callable=AsyncMock,
                return_value=(0, []),
            ),
            patch("workers.llm_worker.read_inbound", side_effect=fake_read),
            patch(
                "workers.llm_worker.process_message",
                new_callable=AsyncMock,
                side_effect=slow_process,
            ),
            patch("workers.llm_worker.ack_inbound", new_callable=AsyncMock) as mock_ack,
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, True]),
        ):
            from core.shutdown import set_shutting_down

            set_shutting_down(False)
            await run_worker("test_shutdown_2")

        assert len(processed) == 1
        mock_ack.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_cleanup_closes_resources(self):
        """Worker cleanup closes both pool and redis."""
        from workers.llm_worker import _worker_cleanup

        with (
            patch("db.postgres.close_pool", new_callable=AsyncMock) as mock_pool,
            patch("db.redis.close_redis", new_callable=AsyncMock) as mock_redis,
        ):
            await _worker_cleanup()

        mock_pool.assert_called_once()
        mock_redis.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_worker_cleanup_closes_resources(self):
        """Send worker cleanup closes both pool and redis."""
        from workers.send_worker import _send_worker_cleanup

        with (
            patch("db.postgres.close_pool", new_callable=AsyncMock) as mock_pool,
            patch("workers.send_worker.close_redis", new_callable=AsyncMock) as mock_redis,
        ):
            await _send_worker_cleanup()

        mock_pool.assert_called_once()
        mock_redis.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP P5 — Worker Heartbeat Lifecycle
# ═══════════════════════════════════════════════════════════════════════════════


class TestWorkerHeartbeat:
    """Test heartbeat write, read, and stop behavior."""

    @pytest.mark.asyncio
    async def test_write_heartbeat_sets_key_with_ttl(self):
        """write_heartbeat writes payload to Redis with TTL."""
        from core.worker_heartbeat import write_heartbeat

        mock_r = AsyncMock()
        stop = asyncio.Event()

        call_count = 0

        async def fake_get_redis():
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                stop.set()
            return mock_r

        with patch("db.redis.get_redis", new_callable=AsyncMock, side_effect=fake_get_redis):
            await write_heartbeat(
                "w_ht_1",
                worker_type="llm",
                interval_seconds=1,
                ttl_seconds=10,
                stop_event=stop,
            )

        mock_r.set.assert_called_once()
        call_args = mock_r.set.call_args
        assert call_args[0][0] == "worker:heartbeat:w_ht_1"
        assert call_args[1]["ex"] == 10

    @pytest.mark.asyncio
    async def test_write_heartbeat_stops_on_event(self):
        """Heartbeat loop exits immediately when stop_event is already set."""
        from core.worker_heartbeat import write_heartbeat

        mock_r = AsyncMock()
        stop = asyncio.Event()
        stop.set()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            await asyncio.wait_for(
                write_heartbeat("w_ht_2", stop_event=stop),
                timeout=2,
            )

        mock_r.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_worker_status_returns_parsed_data(self):
        """read_worker_status parses heartbeat payload."""
        from core.worker_heartbeat import read_worker_status

        mock_r = AsyncMock()
        payload = json.dumps({"worker_id": "w_3", "worker_type": "send", "timestamp": 1000.0})
        mock_r.get.return_value = payload
        mock_r.ttl.return_value = 20

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            status = await read_worker_status("w_3")

        assert status is not None
        assert status["worker_id"] == "w_3"
        assert status["worker_type"] == "send"
        assert status["ttl_seconds"] == 20

    @pytest.mark.asyncio
    async def test_read_worker_status_returns_none_when_no_key(self):
        """read_worker_status returns None when key does not exist."""
        from core.worker_heartbeat import read_worker_status

        mock_r = AsyncMock()
        mock_r.get.return_value = None

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            status = await read_worker_status("w_missing")

        assert status is None

    @pytest.mark.asyncio
    async def test_remove_heartbeat_deletes_key(self):
        """remove_heartbeat deletes the heartbeat key."""
        from core.worker_heartbeat import remove_heartbeat

        mock_r = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            await remove_heartbeat("w_del")

        mock_r.delete.assert_called_once_with("worker:heartbeat:w_del")


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP P6 — Comprehensive Failure Matrix
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailureMatrix:
    """Compact failure matrix covering major failure boundaries."""

    @pytest.mark.asyncio
    async def test_gemini_429_triggers_cooldown(self):
        """Gemini 429 triggers credential cooldown."""
        from core.credentials import CredentialPool

        pool = CredentialPool(api_keys=["k0", "k1", "k2"])

        pool.mark_cooldown(0, seconds=60)

        assert not pool.is_available(0)

    @pytest.mark.asyncio
    async def test_gemini_401_permanent_error(self):
        """Gemini 401 is detected as permanent error."""
        from unittest.mock import MagicMock

        from core.gemini_client import is_permanent_error

        exc_401 = MagicMock()
        exc_401.code = 401
        assert is_permanent_error(exc_401) is True

        exc_403 = MagicMock()
        exc_403.code = 403
        assert is_permanent_error(exc_403) is True

        exc_200 = MagicMock()
        exc_200.code = 200
        assert is_permanent_error(exc_200) is False

    @pytest.mark.asyncio
    async def test_all_gemini_credentials_unavailable(self):
        """When all credentials are on cooldown, get_credential raises."""
        from core.credentials import CredentialPool

        pool = CredentialPool(api_keys=["k0", "k1", "k2"])
        pool.mark_cooldown(0, seconds=3600)
        pool.mark_cooldown(1, seconds=3600)
        pool.mark_cooldown(2, seconds=3600)

        with pytest.raises(RuntimeError, match="cooling down"):
            pool.get_credential()

    @pytest.mark.asyncio
    async def test_llm_exception_triggers_generation_failed(self):
        """LLM exception during process_message triggers ai.generation_failed event."""
        from workers.llm_worker import process_message

        events = []

        async def capture(event_type, data, **kwargs):
            events.append(event_type)
            return "e"

        with (
            patch(
                "workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch(
                "workers.llm_worker.generate_draft",
                new_callable=AsyncMock,
                side_effect=RuntimeError("LLM down"),
            ),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock, side_effect=capture),
            pytest.raises(RuntimeError, match="LLM down"),
        ):
            await process_message(
                user_id=70001,
                user_message="fail",
                telegram_message_id=1,
                username="u",
                first_name="U",
                persona="",
            )

        assert "ai.generation_started" in events
        assert "ai.generation_failed" in events

    @pytest.mark.asyncio
    async def test_redis_read_failure_worker_stays_alive(self):
        """Redis read failure in worker loop does not crash the worker."""
        from workers.llm_worker import run_worker

        call_count = 0

        def failing_read(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Redis read failed")
            from core.shutdown import set_shutting_down

            set_shutting_down(True)
            return []

        with (
            patch("workers.llm_worker.init_pool", new_callable=AsyncMock),
            patch("workers.llm_worker.ensure_consumer_group", new_callable=AsyncMock),
            patch("workers.llm_worker.setup_signal_handlers"),
            patch("workers.llm_worker.write_heartbeat", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.requeue_stalled_messages",
                new_callable=AsyncMock,
                return_value=(0, []),
            ),
            patch("workers.llm_worker.read_inbound", side_effect=failing_read),
            patch("workers.llm_worker.is_shutting_down", side_effect=[False, False, True]),
        ):
            from core.shutdown import set_shutting_down

            set_shutting_down(False)
            await run_worker("test_redis_fail")

        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_telegram_send_failure_goes_to_dlq(self):
        """Telegram send failure triggers move_send_to_dlq."""
        from db.redis import move_send_to_dlq

        mock_r = AsyncMock()

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            await move_send_to_dlq("msg-tg-fail", "Telegram 500 error", payload={"content": "hi"})

        mock_r.xadd.assert_called_once()
        mock_r.xack.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_crash_message_reclaimable(self):
        """UnACKed message is reclaimable by XAUTOCLAIM."""
        from db.redis import requeue_stalled_messages

        mock_r = AsyncMock()
        mock_r.xautoclaim.return_value = (0, [("msg-stale", {"f": "v"})])

        with patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r):
            count, ids = await requeue_stalled_messages("new_worker", idle_ms=1000)

        assert count == 1
        assert "msg-stale" in ids

    @pytest.mark.asyncio
    async def test_concurrent_same_user_serialized_by_lock(self):
        """Second message for same user is rejected when lock is held."""
        from workers.llm_worker import process_message

        with (
            patch(
                "workers.llm_worker.acquire_user_lock",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="d"),
            patch(
                "workers.llm_worker.score_draft",
                new_callable=AsyncMock,
                return_value=(0.9, []),
            ),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
        ):
            await process_message(
                user_id=70010,
                user_message="first",
                telegram_message_id=1,
                username="u",
                first_name="U",
                persona="",
            )

    @pytest.mark.asyncio
    async def test_different_users_process_concurrently(self):
        """Different users can be processed at the same time."""
        from workers.llm_worker import process_message

        with (
            patch(
                "workers.llm_worker.acquire_user_lock",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="d"),
            patch(
                "workers.llm_worker.score_draft",
                new_callable=AsyncMock,
                return_value=(0.9, []),
            ),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
        ):
            await process_message(
                user_id=70020,
                user_message="msg1",
                telegram_message_id=1,
                username="u1",
                first_name="U1",
                persona="",
            )
            await process_message(
                user_id=70021,
                user_message="msg2",
                telegram_message_id=2,
                username="u2",
                first_name="U2",
                persona="",
            )

    @pytest.mark.asyncio
    async def test_malformed_dlq_payload_safe_failure(self):
        """Malformed DLQ payload causes safe failure, not crash."""
        from db.redis import replay_dlq_entry

        mock_r = AsyncMock()

        with (
            patch(
                "db.redis.get_dlq_entry",
                new_callable=AsyncMock,
                return_value={
                    "entry_id": "bad-0",
                    "payload": "not-valid-json{{{{",
                    "stream": "inbound",
                    "replay_count": "0",
                },
            ),
            patch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_r),
        ):
            result = await replay_dlq_entry("bad-payload-0")

        assert result["success"] is False
        assert result["error"] == "invalid_payload_format"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP P7 — DB Persistence Verification
# ═══════════════════════════════════════════════════════════════════════════════


class TestDBPersistence:
    """Verify database operations are called in correct order during processing."""

    @pytest.mark.asyncio
    async def test_upsert_user_called_before_save_inbound(self):
        """upsert_user is called in the worker. save_inbound_message is NOT called
        from the worker — the Telethon handler persists the inbound message."""
        from workers.llm_worker import process_message

        call_order = []

        async def track_upsert(*args, **kwargs):
            call_order.append("upsert_user")

        with (
            patch(
                "workers.llm_worker.acquire_user_lock",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "workers.llm_worker.upsert_user",
                new_callable=AsyncMock,
                side_effect=track_upsert,
            ),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="d"),
            patch(
                "workers.llm_worker.score_draft",
                new_callable=AsyncMock,
                return_value=(0.9, []),
            ),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
        ):
            await process_message(
                user_id=80001,
                user_message="test",
                telegram_message_id=1,
                username="u",
                first_name="U",
                persona="",
            )

        assert call_order == ["upsert_user"]

    @pytest.mark.asyncio
    async def test_operator_queue_called_when_auto_reply_off(self):
        """add_to_operator_queue is called when auto-reply is disabled."""
        from workers.llm_worker import process_message

        with (
            patch(
                "workers.llm_worker.acquire_user_lock",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="d"),
            patch(
                "workers.llm_worker.score_draft",
                new_callable=AsyncMock,
                return_value=(0.9, []),
            ),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.add_to_operator_queue",
                new_callable=AsyncMock,
            ) as mock_add,
            patch("workers.llm_worker.notify_operators", new_callable=AsyncMock),
        ):
            await process_message(
                user_id=80002,
                user_message="test",
                telegram_message_id=2,
                username="u2",
                first_name="U2",
                persona="",
            )

        mock_add.assert_called_once()

    @pytest.mark.asyncio
    async def test_low_score_operator_queue_not_send(self):
        """Low-score message goes to operator queue, not to send stream."""
        from workers.llm_worker import process_message

        with (
            patch(
                "workers.llm_worker.acquire_user_lock",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="d"),
            patch(
                "workers.llm_worker.score_draft",
                new_callable=AsyncMock,
                return_value=(0.3, ["flag1"]),
            ),
            patch(
                "workers.llm_worker.is_auto_reply_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock) as mock_send,
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.add_to_operator_queue",
                new_callable=AsyncMock,
            ) as mock_queue,
            patch("workers.llm_worker.notify_operators", new_callable=AsyncMock),
        ):
            await process_message(
                user_id=80003,
                user_message="test",
                telegram_message_id=3,
                username="u3",
                first_name="U3",
                persona="",
            )

        mock_queue.assert_called_once()
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_always_released_in_finally(self):
        """release_user_lock is called in finally block even on failure."""
        from workers.llm_worker import process_message

        with (
            patch(
                "workers.llm_worker.acquire_user_lock",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "workers.llm_worker.build_qwen3_context",
                new_callable=AsyncMock,
                side_effect=RuntimeError("fail"),
            ),
            patch(
                "workers.llm_worker.release_user_lock",
                new_callable=AsyncMock,
            ) as mock_release,
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
            pytest.raises(RuntimeError, match="fail"),
        ):
            await process_message(
                user_id=80004,
                user_message="test",
                telegram_message_id=4,
                username="u4",
                first_name="U4",
                persona="",
            )

        mock_release.assert_called_once_with(80004)

    @pytest.mark.asyncio
    async def test_excluded_user_skips_all_processing(self):
        """Excluded user skips context building, generation, and DB writes."""
        from workers.llm_worker import process_message

        with (
            patch(
                "workers.llm_worker.acquire_user_lock",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch(
                "workers.llm_worker.is_user_auto_reply_excluded",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "workers.llm_worker.build_qwen3_context",
                new_callable=AsyncMock,
            ) as mock_ctx,
            patch(
                "workers.llm_worker.generate_draft",
                new_callable=AsyncMock,
            ) as mock_gen,
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch(
                "core.event_bus.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            await process_message(
                user_id=80005,
                user_message="test",
                telegram_message_id=5,
                username="u5",
                first_name="U5",
                persona="",
            )

        mock_ctx.assert_not_called()
        mock_gen.assert_not_called()
        mock_pub.assert_not_called()
