"""Phase 2.3 debounce merge tests (mocked consume, no Redis)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _msg(uid, text, tg, gid):
    return {
        "user_id": str(uid),
        "content": text,
        "telegram_message_id": str(tg),
        "username": "u",
        "first_name": "A",
        "generation_id": gid,
    }


def _gid(uid, text, tg):
    from core.generation import telegram_generation_id

    return telegram_generation_id(uid, text, tg)


def _run(buffer, fence="tok"):
    from chatbotv2 import handlers as _h

    captured = {}

    async def fake_enqueue(payload):
        captured.update(payload)
        return "1-0"

    patches = [
        patch("asyncio.sleep", new=AsyncMock()),
        patch.object(_h, "debounce_consume", new=AsyncMock(return_value=("ok", buffer))),
        patch.object(_h, "get_cached_user_persona", new=AsyncMock(return_value="p")),
        patch.object(_h, "enqueue_inbound", side_effect=fake_enqueue),
    ]
    for p in patches:
        p.start()
    try:
        import asyncio

        asyncio.run(_h._wait_and_process(7, "u", "A", creator_id=42, fence=fence))
    finally:
        for p in patches:
            p.stop()
    return captured


def test_burst_three_merged_in_order_first_identity():
    buf = [
        _msg(7, "Hey Luna!", 11, _gid(7, "Hey Luna!", 11)),
        _msg(7, "good, just abit busy", 12, _gid(7, "good, just abit busy", 12)),
        _msg(7, "How are you?", 13, _gid(7, "How are you?", 13)),
    ]
    out = _run(buf)
    assert out["content"] == "Hey Luna!\ngood, just abit busy\nHow are you?"
    assert out["generation_id"] == _gid(7, "Hey Luna!", 11)
    assert out["telegram_message_id"] == "11"


def test_single_msg_identical_to_latest_only():
    buf = [_msg(7, "hello there", 21, _gid(7, "hello there", 21))]
    out = _run(buf)
    assert out["content"] == "hello there"
    assert out["generation_id"] == _gid(7, "hello there", 21)
    assert out["telegram_message_id"] == "21"


@pytest.mark.asyncio
async def test_burst_audit_saves_per_arrival():
    from telethon.tl.types import User

    from chatbotv2 import handlers as _h
    from commerce.single_creator import SingleCreatorStatus

    creator = MagicMock()
    creator.status = SingleCreatorStatus.READY
    creator.creator_id = 42
    saves = []
    buffered = []

    async def fake_save(**kwargs):
        saves.append(kwargs)
        return len(saves)

    async def fake_debounce(**kwargs):
        buffered.append(kwargs)
        return ""

    def fake_event(text, tg):
        event = MagicMock()
        event.sender_id = 7
        event.message = MagicMock()
        event.message.message = text
        event.message.id = tg
        sender = MagicMock(spec=User)
        sender.username = "u"
        sender.first_name = "A"
        sender.bot = False
        event.get_sender = AsyncMock(return_value=sender)
        return event

    with (
        patch.object(_h, "check_rate_limit", new=AsyncMock(return_value=True)),
        patch.object(_h, "upsert_user", new=AsyncMock()),
        patch(
            "commerce.single_creator.resolve_single_application_creator",
            new=AsyncMock(return_value=creator),
        ),
        patch.object(_h, "save_inbound_message", side_effect=fake_save),
        patch("core.event_bus.publish_event", new=AsyncMock(return_value="e")),
        patch.object(_h, "debounce_enqueue", side_effect=fake_debounce),
        patch.object(_h, "get_client", new=AsyncMock()),
    ):
        for text, tg in [("Hey Luna!", 11), ("good, just abit busy", 12), ("How are you?", 13)]:
            await _h.handle_incoming_message(fake_event(text, tg))
    assert len(saves) == 3
    assert [b["content"] for b in buffered] == ["Hey Luna!", "good, just abit busy", "How are you?"]


@pytest.mark.asyncio
async def test_empty_stale_busy_paths_unchanged():
    from chatbotv2 import handlers as _h

    for status in ["empty", "stale", "busy"]:
        enqueued = AsyncMock(return_value="1-0")
        with (
            patch("asyncio.sleep", new=AsyncMock()),
            patch.object(_h, "debounce_consume", new=AsyncMock(return_value=(status, []))),
            patch.object(_h, "get_cached_user_persona", new=AsyncMock(return_value="p")),
            patch.object(_h, "enqueue_inbound", enqueued),
        ):
            await _h._wait_and_process(7, "u", "A", creator_id=42, fence="tok")
        enqueued.assert_not_called()


@pytest.mark.asyncio
async def test_consume_error_path_unchanged():
    from chatbotv2 import handlers as _h
    from db.redis import DebounceConsumeError

    enqueued = AsyncMock(return_value="1-0")
    with (
        patch("asyncio.sleep", new=AsyncMock()),
        patch.object(_h, "debounce_consume", new=AsyncMock(side_effect=DebounceConsumeError("x"))),
        patch.object(_h, "enqueue_inbound", enqueued),
    ):
        await _h._wait_and_process(7, "u", "A", creator_id=42, fence="tok")
    enqueued.assert_not_called()
