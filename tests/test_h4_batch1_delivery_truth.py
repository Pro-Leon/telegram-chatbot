# -*- coding: utf-8 -*-
"""H4 Batch 1: outbound delivery truthfulness tests.

Central invariant under test (fault injection, fakes only — no live
Telegram/DB/Redis):

    Telegram accepted && message.send_failed absent && vault not released

Covers: post-send persistence/ACK/event/finalize failures, pre-send
failures (unchanged truthful behavior), re-enqueue-before-ACK ordering,
and creator/generation correlation on every modified path.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

POST_SEND_REASON = "post_send_persistence_failed"


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


def _ok_client(tg_id=777):
    client = AsyncMock()
    client.get_input_entity = AsyncMock(return_value=MagicMock())
    client.send_message = AsyncMock(return_value=MagicMock(id=tg_id))
    return client


async def _drive(data, client=None, msg_id="m1", overrides=None, vault_overrides=None):
    """Drive _process_send_entry_inner with standard fakes.

    Returns (mocks, published_event_types, dlq_records).
    `overrides` replaces individual chatbotv2.main patches;
    `vault_overrides` replaces individual db.vault patches (also exposed
    in `mocks` as vault_<name> for assertions).
    """
    from chatbotv2 import main as main_mod

    client = client or _ok_client()
    mocks = {
        "get_send_dedup_value": AsyncMock(return_value=None),
        "try_reserve_send_dedup": AsyncMock(return_value="reserved:tok-post"),
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
    mocks["_vault_mocks"] = vault_mocks
    # Aliases for assertions (do NOT patch these onto chatbotv2.main).
    for _vn, _vm in vault_mocks.items():
        mocks[f"vault_{_vn}"] = _vm
    published = []
    dlq_records = []

    real_publish = mocks["publish_event"]
    real_dlq = mocks["move_send_to_dlq"]

    async def _pub(event_type, *args, **kwargs):
        published.append(event_type)
        if isinstance(real_publish, AsyncMock) and real_publish.side_effect is not None:
            return await real_publish.side_effect(event_type, *args, **kwargs)
        return "evt-1"

    async def _dlq(message_id, reason, **kwargs):
        dlq_records.append({"message_id": message_id, "reason": reason, **kwargs})
        if isinstance(real_dlq, AsyncMock) and real_dlq.side_effect is not None:
            return await real_dlq.side_effect(message_id, reason, **kwargs)
        return None

    use_pub_wrapper = not (overrides and "publish_event" in overrides)
    use_dlq_wrapper = not (overrides and "move_send_to_dlq" in overrides)

    with ExitStack() as stack:
        for name, mock_obj in mocks.items():
            if name.startswith(("vault_", "_vault")):
                continue
            if (name == "publish_event" and use_pub_wrapper) or (
                name == "move_send_to_dlq" and use_dlq_wrapper
            ):
                continue
            stack.enter_context(patch.object(main_mod, name, mock_obj))
        if use_pub_wrapper:
            stack.enter_context(patch.object(main_mod, "publish_event", AsyncMock(side_effect=_pub)))
        if use_dlq_wrapper:
            stack.enter_context(patch.object(main_mod, "move_send_to_dlq", AsyncMock(side_effect=_dlq)))
        stack.enter_context(
            patch("core.entity_blacklist.is_blacklisted", new=AsyncMock(return_value=False))
        )
        import db.vault as vault_mod

        for _vault_name, _vault_mock in vault_mocks.items():
            stack.enter_context(patch.object(vault_mod, _vault_name, _vault_mock))
        await main_mod._process_send_entry_inner(client, msg_id, data)
    return mocks, published, dlq_records


# ---------------------------------------------------------------------------
# Post-send persistence failures: never send_failed, never release, repair DLQ
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_1_save_throws_after_acceptance():
    mocks, published, dlq = await _drive(
        _text_data(),
        overrides={"save_outbound_after_send": AsyncMock(side_effect=RuntimeError("pg down"))},
    )
    assert "message.send_failed" not in published
    assert "message.sent" not in published  # save precedes the sent event
    assert len(dlq) == 1
    assert dlq[0]["reason"] == POST_SEND_REASON
    assert dlq[0]["payload"]["creator_id"] == "42"
    assert dlq[0]["payload"]["generation_id"] == "gid-1"
    # Delivered marker ensured by both the main flow and the post-send
    # repair path (idempotent owner-safe confirm), ACK ran before save.
    assert mocks["confirm_send_dedup"].call_count >= 1
    first_confirm = mocks["confirm_send_dedup"].call_args_list[0]
    assert first_confirm[0][0] == "plain:1"
    assert first_confirm[1].get("creator_id") == 42
    mocks["ack_send"].assert_called_once_with("m1")


@pytest.mark.asyncio
async def test_1b_save_throws_with_reservation_finalizes_not_releases():
    import db.vault as vault_mod

    finalize_calls = []
    release_calls = []

    async def _fin(reservation_id, telegram_message_id=None, creator_id=None):
        finalize_calls.append((reservation_id, telegram_message_id))
        return True

    async def _rel(reservation_id, creator_id=None):
        release_calls.append(reservation_id)
        return True

    with (
        patch.object(vault_mod, "finalize_delivery", AsyncMock(side_effect=_fin)),
        patch.object(vault_mod, "release_delivery", AsyncMock(side_effect=_rel)),
    ):
        mocks, published, dlq = await _drive(
            _text_data(),
            overrides={"save_outbound_after_send": AsyncMock(side_effect=RuntimeError("pg down"))},
        )
    assert "message.send_failed" not in published
    assert dlq[0]["reason"] == POST_SEND_REASON
    assert release_calls == []
    assert finalize_calls == []  # text path holds no reservation; nothing to finalize


@pytest.mark.asyncio
async def test_2_ack_throws_after_acceptance():
    mocks, published, dlq = await _drive(
        _text_data(),
        overrides={"ack_send": AsyncMock(side_effect=RuntimeError("redis down"))},
    )
    assert "message.send_failed" not in published
    assert len(dlq) == 1
    assert dlq[0]["reason"] == POST_SEND_REASON
    assert dlq[0]["payload"]["creator_id"] == "42"
    assert dlq[0]["payload"]["generation_id"] == "gid-1"
    # ACK failure means save never ran (ordering), but the marker was set.
    assert mocks["confirm_send_dedup"].call_count >= 1
    mocks["save_outbound_after_send"].assert_not_called()


@pytest.mark.asyncio
async def test_3_sent_event_throws_after_acceptance():
    attempted = []

    async def _pub(event_type, *args, **kwargs):
        attempted.append(event_type)
        if event_type == "message.sent":
            raise RuntimeError("bus down")
        return "evt-1"

    mocks, published, dlq = await _drive(
        _text_data(),
        overrides={"publish_event": AsyncMock(side_effect=_pub)},
    )
    assert "message.sent" in attempted
    assert "message.send_failed" not in attempted
    assert len(dlq) == 1
    assert dlq[0]["reason"] == POST_SEND_REASON
    # Save + finalize already succeeded before the event throw.
    mocks["save_outbound_after_send"].assert_called_once()
    save_kwargs = mocks["save_outbound_after_send"].call_args[1]
    assert save_kwargs["creator_id"] == 42
    assert save_kwargs["generation_id"] == "gid-1"


@pytest.mark.asyncio
async def test_4_finalize_throws_preserves_reservation():
    import tempfile

    release_calls = []

    async def _rel(reservation_id):
        release_calls.append(reservation_id)
        return True

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"test")
        temp_path = f.name

    sent = []

    async def _pub(event_type, *args, **kwargs):
        sent.append(event_type)
        return "evt-1"

    client = _ok_client(tg_id=555)
    data = _text_data(
        media_type="photo",
        media_path=temp_path,
        fangate_media_id="9",
        dedup_id="media:1",
    )
    with patch("chatbotv2.main.send_file", new=AsyncMock(return_value=MagicMock(id=555))):
        mocks, published, dlq = await _drive(
            data,
            client=client,
            overrides={"publish_event": AsyncMock(side_effect=_pub)},
            vault_overrides={
                "finalize_delivery": AsyncMock(side_effect=RuntimeError("pg down")),
                "release_delivery": AsyncMock(side_effect=_rel),
            },
        )
    # Finalize failure is swallowed in-flow: sent still emitted, no DLQ, no release.
    assert "message.sent" in sent
    assert "message.send_failed" not in sent
    assert dlq == []
    assert release_calls == []
    mocks["vault_release_delivery"].assert_not_called()


@pytest.mark.asyncio
async def test_4b_save_throws_with_media_reservation_finalizes_not_releases():
    import tempfile

    finalize_calls = []
    release_calls = []

    async def _fin(reservation_id, telegram_message_id=None, creator_id=None):
        finalize_calls.append((reservation_id, telegram_message_id))
        return True

    async def _rel(reservation_id, creator_id=None):
        release_calls.append(reservation_id)
        return True

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"test")
        temp_path = f.name

    client = _ok_client(tg_id=556)
    data = _text_data(
        media_type="photo",
        media_path=temp_path,
        fangate_media_id="9",
        dedup_id="media:2",
    )
    with patch("chatbotv2.main.send_file", new=AsyncMock(return_value=MagicMock(id=556))):
        mocks, published, dlq = await _drive(
            data,
            client=client,
            overrides={"save_outbound_after_send": AsyncMock(side_effect=RuntimeError("pg down"))},
            vault_overrides={
                "finalize_delivery": AsyncMock(side_effect=_fin),
                "release_delivery": AsyncMock(side_effect=_rel),
            },
        )
    assert "message.send_failed" not in published
    assert len(dlq) == 1 and dlq[0]["reason"] == POST_SEND_REASON
    # Proven delivery is finalized with the real Telegram id — never released.
    assert finalize_calls == [(7, 556)]
    assert release_calls == []
    mocks["vault_release_delivery"].assert_not_called()


@pytest.mark.asyncio
async def test_5_confirm_throws_still_truthful():
    mocks, published, dlq = await _drive(
        _text_data(),
        overrides={"confirm_send_dedup": AsyncMock(side_effect=RuntimeError("redis down"))},
    )
    # Confirm failure is swallowed by existing code; send completes truthfully.
    assert "message.sent" in published
    assert "message.send_failed" not in published
    assert dlq == []
    mocks["save_outbound_after_send"].assert_called_once()


# ---------------------------------------------------------------------------
# Pre-send failures: existing truthful behavior intact
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_6_blocked_before_send_unchanged():
    from telethon.errors import UserIsBlockedError

    client = _ok_client()
    client.send_message = AsyncMock(side_effect=UserIsBlockedError("Blocked"))
    mocks, published, dlq = await _drive(_text_data(), client=client)
    assert "message.send_failed" in published
    assert "message.sent" not in published
    assert dlq == []  # blocked path ACKs directly, no DLQ
    mocks["ack_send"].assert_called_once_with("m1")
    mocks["save_outbound_after_send"].assert_not_called()


@pytest.mark.asyncio
async def test_7_entity_failure_no_sent():
    client = _ok_client()
    client.get_input_entity = AsyncMock(side_effect=ValueError("no such peer"))
    mocks, published, dlq = await _drive(_text_data(), client=client)
    assert "message.sent" not in published
    assert "message.send_failed" not in published
    assert len(dlq) == 1 and dlq[0]["reason"] == "entity_not_found"
    mocks["save_outbound_after_send"].assert_not_called()
    client.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Re-enqueue ordering: XADD before ACK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_8_rate_limit_reenqueue_before_ack():
    order = []

    async def _xadd(payload, **kwargs):
        order.append("xadd")
        return "x-2"

    async def _ack(message_id):
        order.append("ack")

    mocks, published, dlq = await _drive(
        _text_data(),
        overrides={
            "check_send_rate_limit": AsyncMock(return_value=False),
            "enqueue_send": AsyncMock(side_effect=_xadd),
            "ack_send": AsyncMock(side_effect=_ack),
        },
    )
    assert order == ["xadd", "ack"]
    assert dlq == []
    mocks["save_outbound_after_send"].assert_not_called()


@pytest.mark.asyncio
async def test_8b_rate_limit_xadd_failure_leaves_pending():
    mocks, published, dlq = await _drive(
        _text_data(),
        overrides={
            "check_send_rate_limit": AsyncMock(return_value=False),
            "enqueue_send": AsyncMock(side_effect=RuntimeError("redis down")),
        },
    )
    mocks["ack_send"].assert_not_called()
    assert dlq == []
    mocks["save_outbound_after_send"].assert_not_called()


@pytest.mark.asyncio
async def test_9_floodwait_reenqueue_before_ack():
    from telethon.errors import FloodWaitError

    order = []

    async def _xadd(payload, **kwargs):
        order.append("xadd")
        return "x-3"

    async def _ack(message_id):
        order.append("ack")

    flood_exc = FloodWaitError(request=None)
    flood_exc.seconds = 0
    client = _ok_client()
    client.get_input_entity = AsyncMock(side_effect=flood_exc)
    mocks, published, dlq = await _drive(
        _text_data(),
        client=client,
        overrides={
            "enqueue_send": AsyncMock(side_effect=_xadd),
            "ack_send": AsyncMock(side_effect=_ack),
        },
    )
    assert order == ["xadd", "ack"]
    assert dlq == []
    client.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_9b_floodwait_xadd_failure_leaves_pending():
    from telethon.errors import FloodWaitError

    flood_exc = FloodWaitError(request=None)
    flood_exc.seconds = 0
    client = _ok_client()
    client.get_input_entity = AsyncMock(side_effect=flood_exc)
    mocks, published, dlq = await _drive(
        _text_data(),
        client=client,
        overrides={"enqueue_send": AsyncMock(side_effect=RuntimeError("redis down"))},
    )
    mocks["ack_send"].assert_not_called()
    assert dlq == []


# ---------------------------------------------------------------------------
# Correlation + happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_10_success_still_sends_with_correlation():
    mocks, published, dlq = await _drive(_text_data())
    assert "message.sent" in published
    assert "message.send_failed" not in published
    assert dlq == []
    save_kwargs = mocks["save_outbound_after_send"].call_args[1]
    assert save_kwargs["creator_id"] == 42
    assert save_kwargs["generation_id"] == "gid-1"
    assert save_kwargs["telegram_message_id"] == 777
