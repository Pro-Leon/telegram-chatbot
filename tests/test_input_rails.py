"""Phase 2.1 input rails tests (mocked event/redis, no DB)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatbotv2.handlers import normalize_inbound_text


def test_normalize_empty_whitespace_nontext_drop():
    assert normalize_inbound_text("") == (None, False)
    assert normalize_inbound_text("   \n  ") == (None, False)
    assert normalize_inbound_text(None) == (None, False)
    assert normalize_inbound_text(123) == (None, False)
    assert normalize_inbound_text(["hi"]) == (None, False)


def test_normalize_clean_passthrough():
    assert normalize_inbound_text("  hello there  ") == ("hello there", False)
    assert normalize_inbound_text("x" * 2000) == ("x" * 2000, False)


def test_normalize_truncates_over_long():
    text, truncated = normalize_inbound_text("y" * 2001)
    assert truncated is True
    assert len(text) == 2000


def _fake_event(message, msg_id=5):
    from telethon.tl.types import User

    event = MagicMock()
    event.sender_id = 7
    event.message = MagicMock()
    event.message.message = message
    event.message.id = msg_id
    sender = MagicMock(spec=User)
    sender.username = "u"
    sender.first_name = "A"
    sender.bot = False
    event.get_sender = AsyncMock(return_value=sender)
    return event


@pytest.mark.asyncio
async def test_handler_drops_empty_and_nontext():
    from chatbotv2 import handlers as _h

    for bad in ["", "   ", None, 123]:
        upsert = AsyncMock()
        with (
            patch.object(_h, "check_rate_limit", new=AsyncMock(return_value=True)),
            patch.object(_h, "upsert_user", upsert),
        ):
            await _h.handle_incoming_message(_fake_event(bad))
        upsert.assert_not_called()


@pytest.mark.asyncio
async def test_handler_truncates_over_long():
    from chatbotv2 import handlers as _h
    from commerce.single_creator import SingleCreatorStatus

    creator = MagicMock()
    creator.status = SingleCreatorStatus.READY
    creator.creator_id = 42
    saved = {}

    async def fake_save(**kwargs):
        saved.update(kwargs)
        return 1

    with (
        patch.object(_h, "check_rate_limit", new=AsyncMock(return_value=True)),
        patch.object(_h, "upsert_user", new=AsyncMock()),
        patch(
            "commerce.single_creator.resolve_single_application_creator",
            new=AsyncMock(return_value=creator),
        ),
        patch.object(_h, "save_inbound_message", side_effect=fake_save),
        patch("core.event_bus.publish_event", new=AsyncMock(return_value="e")),
        patch.object(_h, "debounce_enqueue", new=AsyncMock(return_value="")),
        patch.object(_h, "get_client", new=AsyncMock()),
    ):
        await _h.handle_incoming_message(_fake_event("z" * 2001))
    assert len(saved.get("content", "")) == 2000


class _FakeRedis:
    def __init__(self, fail_on_set=False):
        self.store = {}
        self.xadds = []
        self.fail_on_set = fail_on_set

    async def set(self, key, value, nx=False, ex=None):
        if self.fail_on_set:
            raise RuntimeError("redis down")
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

    async def xadd(self, stream, data, id="*"):
        self.xadds.append((stream, dict(data)))
        return "1-0"


def _inbound(content, tg):
    return {
        "user_id": "7",
        "content": content,
        "telegram_message_id": str(tg),
        "username": "u",
        "first_name": "A",
        "persona": "p",
        "generation_id": f"gid-{tg}",
        "creator_id": "42",
    }


@pytest.mark.asyncio
async def test_enqueue_duplicate_content_new_tgid_suppressed():
    from db import redis as _r

    fake = _FakeRedis()
    with patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)):
        first = await _r.enqueue_inbound(_inbound("same hello", 11))
        second = await _r.enqueue_inbound(_inbound("same hello", 12))
    assert not str(first).startswith("duplicate:")
    assert str(second).startswith("duplicate:")
    assert len(fake.xadds) == 1


@pytest.mark.asyncio
async def test_enqueue_same_tgid_suppressed_and_clean_passes():
    from db import redis as _r

    fake = _FakeRedis()
    with patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)):
        await _r.enqueue_inbound(_inbound("hi", 21))
        dup = await _r.enqueue_inbound(_inbound("hi", 21))
        ok = await _r.enqueue_inbound(_inbound("different text here", 22))
    assert str(dup).startswith("duplicate:")
    assert not str(ok).startswith("duplicate:")
    assert len(fake.xadds) == 2


@pytest.mark.asyncio
async def test_enqueue_redis_error_fail_open_xadd():
    from db import redis as _r

    fake = _FakeRedis(fail_on_set=True)
    with patch.object(_r, "get_redis", new=AsyncMock(return_value=fake)):
        mid = await _r.enqueue_inbound(_inbound("hello", 31))
    assert not str(mid).startswith("duplicate:")
    assert len(fake.xadds) == 1


@pytest.mark.asyncio
async def test_wait_and_process_enqueue_raise_no_escape():
    from chatbotv2 import handlers as _h

    debounced = [
        {
            "user_id": "7",
            "content": "hi",
            "telegram_message_id": "9",
            "username": "u",
            "first_name": "A",
            "generation_id": "gid-9",
        }
    ]
    with (
        patch("asyncio.sleep", new=AsyncMock()),
        patch.object(_h, "debounce_consume", new=AsyncMock(return_value=("ok", debounced))),
        patch.object(_h, "get_cached_user_persona", new=AsyncMock(return_value="p")),
        patch.object(_h, "enqueue_inbound", new=AsyncMock(side_effect=RuntimeError("XADD down"))),
    ):
        # Must not raise: bare create_task in production.
        await _h._wait_and_process(7, "u", "A", creator_id=42, fence="tok")
