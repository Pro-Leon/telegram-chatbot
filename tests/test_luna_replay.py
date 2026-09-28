"""Luna 5-turn golden replay (Phase 4.3 CI corpus).

Replays the canonical incident transcript (docs/LUNA_RELIABILITY_PROGRAM.md
Phase 0) through the real process_message with scripted provider text and
hermetic stream state. Detection stays real: the score path runs the
production output-rails check, so echo/repeat suppression is exercised.

Turns: T1-T3 greeting (distinct tg_ids, busy-fact in T2), T4 echo draft,
T5 are-you-a-bot. Asserts per turn: pipeline flags -> routing verdict ->
send/queue outcome -> DB row + telemetry veto fields.
"""

import pytest

from tests.luna_replay_harness import CREATOR_ID, USER_ID, LunaWorld, drive_turn

pytestmark = [pytest.mark.unit]

T1_TG, T2_TG, T3_TG, T4_TG, T5_TG = 1001, 1002, 1003, 1004, 1005
GREETING_IN = "Hey Luna, how's it going?"
BUSY_IN = "good, just abit busy"
QUESTION_IN = "How are you?"
ECHO_DRAFT = "Your conversational response to the fan"
BOT_Q_IN = "what do you mean fan? are you a bot?"


def _tele(world, tg):
    """Telemetry object for the turn's generation (creator-scoped lookup)."""
    for (cid, gid), tele in world.telemetry._telemetry_cache.items():
        if cid == CREATOR_ID and tele.user_id == USER_ID:
            return tele
    raise AssertionError(f"no telemetry recorded (tg={tg})")


@pytest.mark.asyncio
async def test_luna_5_turn_replay():
    world = LunaWorld()

    # T1: greeting -> single auto-approved outbound.
    await drive_turn(
        world,
        user_message=GREETING_IN,
        telegram_message_id=T1_TG,
        draft="Hey! Great to hear from you, how's your day going?",
        confidence=0.9,
    )
    assert len(world.sends()) == 1
    first = world.sends()[0]
    assert first["was_auto_approved"] == "True"
    completed = world.completed_events()
    assert len(completed) == 1 and completed[0]["data"]["was_auto_approved"] is True
    # Lifecycle lineage: started/completed share one generation_id.
    started = [p for p in world.published if p["event_type"] == "ai.generation_started"]
    assert started and started[0]["generation_id"] == completed[0]["generation_id"]

    # T2: busy-fact turn -> single outbound, no repeat suppression.
    await drive_turn(
        world,
        user_message=BUSY_IN,
        telegram_message_id=T2_TG,
        draft="Glad you're doing well! Busy day?",
        confidence=0.88,
    )
    assert len(world.sends()) == 2
    assert len(world.queue_rows) == 0

    # T3: greeting again (distinct tg) -> provider context retains busy-fact.
    await drive_turn(
        world,
        user_message=QUESTION_IN,
        telegram_message_id=T3_TG,
        draft="Doing great, thanks for asking!",
        confidence=0.9,
    )
    assert len(world.sends()) == 3
    assert any("busy" in m.get("content", "") for m in world.context_snapshots[-1])

    # T4: echo draft -> QUEUE (prompt_echo, capped, handoff; never auto).
    await drive_turn(
        world,
        user_message="are you still there?",
        telegram_message_id=T4_TG,
        draft=ECHO_DRAFT,
        confidence=0.875,
    )
    assert len(world.sends()) == 3
    assert len(world.queue_rows) == 1
    row = world.queue_rows[0]
    assert "prompt_echo" in row["flags"]
    assert row["confidence_score"] <= 0.29
    t4_completed = world.completed_events()[-1]
    assert t4_completed["data"]["was_auto_approved"] is False
    tele = _tele(world, T4_TG)
    assert tele.routing_veto is not None
    assert tele.to_dict()["routing_veto"] == tele.routing_veto

    # T5: are-you-a-bot -> grounded denial, sent, no fan vocab/speaker labels.
    await drive_turn(
        world,
        user_message=BOT_Q_IN,
        telegram_message_id=T5_TG,
        draft="I'm Luna, a real person chatting with you here!",
        confidence=0.9,
    )
    assert len(world.sends()) == 4
    sent = world.sends()[-1]["content"]
    assert "fan" not in sent.lower()
    import re as _re

    assert not _re.match(r"^[A-Z][a-zA-Z]+:", sent)
    assert len(world.completed_events()) == 5
