"""Shared mocked turn-driver for the Luna golden + red-team replay corpus.

Drives the real ``workers.llm_worker.process_message`` per inbound turn with
scripted provider text. Real production seams under test: output-rails
detection (score path), routing decision, turn-send gate, send-stream XADD,
operator-queue capture, generation lifecycle events, telemetry veto fields.

I/O boundaries mocked: LLM text (scripted per turn), PG pool (fail-open),
provider context builder (accumulating harness history), telemetry sink
(capturing real collector). Shared FakeRedis gives the real turn gate and
the real enqueue_send hermetic stream state across turns.
"""

from __future__ import annotations

import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

CREATOR_ID = 1
USER_ID = 8151382101


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.xadds = []

    async def set(self, key, value, nx=False, ex=None):
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


class LunaWorld:
    """Per-corpus hermetic world: stream state, queue rows, events, telemetry."""

    def __init__(self):
        self.redis = FakeRedis()
        self.queue_rows = []
        self.published = []
        self.context_snapshots = []
        self.history = []
        self.enqueues = []
        from core.telemetry import TelemetryCollector

        self.telemetry = TelemetryCollector()

    def sends(self, stream="send_messages"):
        return [d for s, d in self.redis.xadds if s == stream]

    def queue_for_tg(self, tg_id):
        return [r for r in self.queue_rows if r.get("telegram_message_id") == tg_id]

    def completed_events(self):
        return [p for p in self.published if p["event_type"] == "ai.generation_completed"]


async def drive_turn(world, *, user_message, telegram_message_id, draft, confidence):
    """Drive one real process_message turn with scripted provider output.

    Detection stays real: the score path runs the production output-rails
    check over the scripted draft (capped by its verdict), so echo/repeat
    suppression is exercised, not assumed.
    """
    from commerce.single_creator import SingleCreatorStatus
    from workers import llm_worker as lw

    async def fake_generate(context_messages, user_message=None, model=None):
        world.context_snapshots.append(list(context_messages or []))
        return draft

    async def fake_score(draft_text, user_msg, context):
        from core.output_rails import check as rails_check

        recent = [d.get("content", "") for d in world.sends()]
        _verdict, flags, cap = rails_check(draft_text, recent_outbound=recent)
        score = min(confidence, cap) if cap else confidence
        return score, list(flags)

    async def fake_build_context(*args, **kwargs):
        world.history.append({"role": "user", "content": user_message})
        return list(world.history)

    async def fake_add_queue(**kwargs):
        row = dict(kwargs)
        row["telegram_message_id"] = telegram_message_id
        row["id"] = 1000 + len(world.queue_rows)
        world.queue_rows.append(row)
        return row["id"]

    async def fake_enqueue(payload, dedup_id=None, generation_id=None, creator_id=None):
        from db.redis import enqueue_send as real_enqueue

        result = await real_enqueue(
            payload, dedup_id=dedup_id, generation_id=generation_id, creator_id=creator_id
        )
        world.enqueues.append(
            {"payload": dict(payload), "dedup_id": dedup_id, "generation_id": generation_id}
        )
        return result

    async def fake_publish(event_type, data, **kwargs):
        world.published.append({"event_type": event_type, "data": data, **kwargs})
        return "evt-1"

    async def fake_publish_batch(events):
        for ev in events:
            world.published.append(
                {
                    "event_type": ev["event"],
                    "data": ev["data"],
                    **{k: v for k, v in ev.items() if k not in ("event", "data")},
                }
            )
        return ["evt-b"]

    async def fake_evaluate(*args, **kwargs):
        return None

    async def fake_commerce_draft(*args, **kwargs):
        return None

    mock_creator = MagicMock()
    mock_creator.status = SingleCreatorStatus.READY
    mock_creator.creator_id = CREATOR_ID

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch(
                "db.dropfans.get_dropfans_integration",
                new=AsyncMock(return_value={"status": "active"}),
            )
        )
        stack.enter_context(patch.object(lw, "acquire_user_lock", new=AsyncMock(return_value=True)))
        stack.enter_context(patch.object(lw, "upsert_user", new=AsyncMock()))
        stack.enter_context(
            patch.object(lw, "is_user_auto_reply_excluded", new=AsyncMock(return_value=False))
        )
        stack.enter_context(
            patch(
                "memory.creator_persona.get_structured_persona_async",
                new=AsyncMock(return_value=None),
                create=True,
            )
        )
        stack.enter_context(patch.object(lw, "build_qwen3_context", new=fake_build_context))
        stack.enter_context(
            patch(
                "core.conversation_state.derive_conversation_state", new=MagicMock(return_value={})
            )
        )
        stack.enter_context(
            patch(
                "db.postgres.get_pool",
                new=AsyncMock(side_effect=RuntimeError("no db in unit test")),
                create=True,
            )
        )
        stack.enter_context(
            patch("commerce.opportunity_engine.evaluate_opportunity", new=fake_evaluate)
        )
        stack.enter_context(
            patch.object(lw, "_try_commerce_draft", new=fake_commerce_draft, create=True)
        )
        stack.enter_context(
            patch(
                "commerce.dynamic_copy.generate_sealed_lead_in",
                new=AsyncMock(return_value=MagicMock(status="FAILED", text=None)),
            )
        )
        stack.enter_context(
            patch(
                "commerce.deepseek.extract_commerce_signals",
                new=AsyncMock(return_value=None),
                create=True,
            )
        )
        stack.enter_context(patch.object(lw, "generate_draft", new=fake_generate))
        stack.enter_context(patch.object(lw, "score_draft", new=fake_score))
        stack.enter_context(
            patch.object(lw, "is_auto_reply_enabled", new=AsyncMock(return_value=True))
        )
        stack.enter_context(patch.object(lw, "add_to_operator_queue", new=fake_add_queue))
        stack.enter_context(patch.object(lw, "enqueue_send", new=fake_enqueue, create=True))
        stack.enter_context(patch.object(lw, "post_process", new=AsyncMock()))
        stack.enter_context(patch.object(lw, "notify_operators", new=AsyncMock(), create=True))
        stack.enter_context(patch("core.event_bus.publish_event", new=fake_publish))
        stack.enter_context(patch("core.event_bus.publish_events_batch", new=fake_publish_batch))
        try:
            stack.enter_context(patch.object(lw._settings, "llm_path", "legacy"))
            stack.enter_context(patch.object(lw._settings, "llm_tools_enabled", False))
        except Exception:
            logging.getLogger("tests.luna_replay_harness").debug(
                "settings override skipped", exc_info=True
            )
        stack.enter_context(patch("db.redis.get_redis", new=AsyncMock(return_value=world.redis)))
        stack.enter_context(
            patch(
                "core.telemetry.get_telemetry_collector",
                new=MagicMock(return_value=world.telemetry),
            )
        )
        try:
            stack.enter_context(
                patch(
                    "agent.canary.should_use_agent", new=MagicMock(return_value=False), create=True
                )
            )
        except Exception:
            logging.getLogger("tests.luna_replay_harness").debug(
                "canary patch skipped", exc_info=True
            )
        await lw.process_message(
            user_id=USER_ID,
            user_message=user_message,
            telegram_message_id=telegram_message_id,
            username="luna_fan",
            first_name="Fan",
            persona="p",
            creator_id=CREATOR_ID,
        )
    return world
