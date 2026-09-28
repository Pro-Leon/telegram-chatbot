"""Crooked-reply fixes: flush human-touch gate, prompt-echo validator,
word-boundaried tone/mode vocabularies.

Covers the Luna incident findings: flagged drafts auto-sent by flush,
schema text sent verbatim, substring misfires (photo->flirty,
topic->TEASE, send->TEASE).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ── Flush human-touch gate: pure predicate ──────────────────────────────


def test_predicate_untouched_pending_is_not_approved():
    from workers.send_worker import is_queue_item_human_approved as gate

    assert gate({"status": "pending", "edited": False, "resolved_by": None}) is False
    assert gate({"status": "pending", "edited": False, "resolved_by": ""}) is False
    assert gate({"status": "pending"}) is False


def test_predicate_edited_or_resolved_is_approved():
    from workers.send_worker import is_queue_item_human_approved as gate

    assert gate({"status": "pending", "edited": True}) is True
    assert gate({"status": "pending", "edited": False, "resolved_by": "admin"}) is True
    assert gate({"status": "pending", "resolved_by": "op_7"}) is True


def test_predicate_never_raises():
    from workers.send_worker import is_queue_item_human_approved as gate

    for bad in (None, [], "x", 123, {"edited": 0}, {"resolved_by": ""}):
        assert gate(bad) is False


# ── Flush integration (mocked DAO/redis) ────────────────────────────────


def _pool_with_creators(*ids):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[{"creator_id": i} for i in ids])
    pool = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _pending_row(**overrides):
    row = {
        "id": 9001,
        "user_id": 4242,
        "creator_id": 1,
        "status": "pending",
        "draft_content": "Hello there",
        "confidence_score": 0.9,
        "edited": False,
        "resolved_by": None,
        "assigned_to": None,
        "generation_id": None,
    }
    row.update(overrides)
    return row


def _flush_patches(pending_rows):
    from workers import send_worker as sw

    pool = _pool_with_creators(1)
    return [
        patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=pool),
        patch.object(
            sw, "get_pending_queue_items", new_callable=AsyncMock, return_value=list(pending_rows)
        ),
        patch(
            "db.postgres.get_queue_item_for_send",
            new_callable=AsyncMock,
            side_effect=lambda qid, cid: next(
                (dict(r) for r in pending_rows if r["id"] == qid), None
            ),
        ),
        patch.object(sw, "resolve_queue_item", new_callable=AsyncMock, return_value=True),
        patch.object(sw, "enqueue_send", new_callable=AsyncMock, return_value="mid"),
        patch("core.audit.record_audit_event", new_callable=AsyncMock, return_value=None),
    ]


@pytest.mark.asyncio
async def test_flush_skips_untouched_pending():
    from workers import send_worker as sw

    patches = _flush_patches([_pending_row()])
    for p in patches:
        p.start()
    try:
        sent = await sw.flush_queue()
        assert sent == 0
        sw.enqueue_send.assert_not_called()
        # Still pending: never resolved to approved.
        approved_calls = [
            c for c in sw.resolve_queue_item.call_args_list if c.args[1] == "approved"
        ]
        assert approved_calls == []
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_flush_sends_edited_row():
    from workers import send_worker as sw

    patches = _flush_patches([_pending_row(edited=True)])
    for p in patches:
        p.start()
    try:
        sent = await sw.flush_queue()
        assert sent == 1
        sw.enqueue_send.assert_called_once()
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_flush_sends_resolved_row():
    from workers import send_worker as sw

    patches = _flush_patches([_pending_row(resolved_by="admin")])
    for p in patches:
        p.start()
    try:
        sent = await sw.flush_queue()
        assert sent == 1
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_flush_still_rejects_empty():
    from workers import send_worker as sw

    patches = _flush_patches([_pending_row(draft_content="")])
    for p in patches:
        p.start()
    try:
        sent = await sw.flush_queue()
        assert sent == 0
        rejected = [c for c in sw.resolve_queue_item.call_args_list if c.args[1] == "rejected"]
        assert len(rejected) == 1
    finally:
        for p in patches:
            p.stop()


# ── Prompt-echo validator ───────────────────────────────────────────────


def test_echo_schema_text_detected():
    from core.one_call import detect_prompt_echo as echo

    assert echo("Your conversational response to the fan") is True
    assert echo("  YOUR CONVERSATIONAL RESPONSE TO THE FAN. ") is True
    assert echo("<<REPLY>>") is True
    assert echo("  <<reply>>. ") is True


def test_echo_normal_replies_clean():
    from core.one_call import detect_prompt_echo as echo

    assert echo("Hey Luna, red is a great color!") is False
    assert echo("A fan once asked me that, funny story") is False
    assert echo("") is False
    assert echo(None) is False
    assert echo(123) is False


def test_echo_forces_review_in_validation():
    from core.one_call import validate_one_call_response
    import json as _json

    raw = _json.dumps(
        {
            "reply": "Your conversational response to the fan",
            "commerce_signals": {
                "purchase_intent": 0.0,
                "content_interest": 0.0,
                "relationship_engagement": 0.5,
                "price_interest": 0.0,
                "explicit_purchase_request": False,
                "explicit_content_request": False,
                "requested_price": None,
                "declined_recent_offer": False,
                "negative_sentiment": 0.0,
                "confidence": 0.9,
                "evidence": [],
                "model_uncertainty": 0.1,
                "primary_intent": "casual_chat",
                "intent_tags": [],
                "negative_intent_tags": [],
                "fan_asks_question": False,
            },
            "confidence": 0.95,
            "needs_handoff": False,
        }
    )
    res = validate_one_call_response(raw)
    assert "prompt_echo" in res.quality_flags
    assert res.quality_score < 0.3
    assert res.needs_handoff is True
    assert res.advisory_handoff is False  # deterministic, corroborated


# ── Word boundaries ─────────────────────────────────────────────────────


def test_tone_photography_not_flirty_hot_still_is():
    from core.conversation_state import derive_conversation_state as dcs

    photo = dcs(
        [{"direction": "inbound", "content": "I love photography!"}],
        user={"message_count": 10},
    )
    assert photo.tone != "flirty"
    hot = dcs(
        [{"direction": "inbound", "content": "you are so hot"}],
        user={"message_count": 10},
    )
    assert hot.tone == "flirty"


def test_response_mode_topic_and_send_not_tease():
    from core.conversation_state import derive_conversation_state as dcs
    from core.response_mode import ResponseMode, plan_response_mode as prm

    for text, expected in (
        ("What is your favorite topic?", ResponseMode.ANSWER),
        ("Could you send that link again?", ResponseMode.ANSWER),
    ):
        cs = dcs([{"direction": "inbound", "content": text}], user={"message_count": 10})
        assert prm(text, cs) == expected, text


def test_response_mode_sexual_pressure_still_tease():
    from core.conversation_state import derive_conversation_state as dcs
    from core.response_mode import ResponseMode, plan_response_mode as prm

    for text in ("mind sharing a pic of you?", "Can I see a picture?"):
        cs = dcs([{"direction": "inbound", "content": text}], user={"message_count": 10})
        assert prm(text, cs) == ResponseMode.TEASE, text
