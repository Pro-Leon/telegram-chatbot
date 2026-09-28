"""M3 — Current-message exactly-once in model-visible context.

Hermetic, deterministic, no live Postgres/Redis/Telegram/LLM.
Asserts the model-visible message lists (the objects passed toward
OneCall / provider.generate / provider.generate_with_history), not
intermediate structures.

Identity note: history projections carry (role/direction, content) only,
so tests establish message identity by construction — each simulated DB
row is a distinct logical telegram (distinct telegram_message_id in the
fixture comments), mirroring the canonical save-before-retrieval flow.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _user(content):
    return {"role": "user", "content": content}


def _assistant(content):
    return {"role": "assistant", "content": content}


def _system(content="sys"):
    return {"role": "system", "content": content}


def _user_turns(messages):
    return [m for m in messages if m.get("role") == "user"]


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------
class TestHelper:
    def test_tail_match_skips(self):
        from memory.context import current_message_in_history

        assert current_message_in_history([_user("old"), _user("hello")], "hello") is True

    def test_absent_appends(self):
        from memory.context import current_message_in_history

        assert current_message_in_history([_user("old")], "new") is False

    def test_empty_history_appends(self):
        from memory.context import current_message_in_history

        assert current_message_in_history([], "hello") is False

    def test_stale_non_tail_match_skips(self):
        from memory.context import current_message_in_history

        assert current_message_in_history([_user("old"), _user("new")], "old") is True

    def test_assistant_echo_does_not_suppress(self):
        from memory.context import current_message_in_history

        # Only assistant turns carry the text: current is genuinely absent.
        assert current_message_in_history([_assistant("hello")], "hello") is False

    def test_direction_keyed_turns(self):
        from memory.context import current_message_in_history

        raw = [
            {"direction": "inbound", "content": "old"},
            {"direction": "outbound", "content": "reply"},
        ]
        assert current_message_in_history(raw, "old") is True
        assert current_message_in_history(raw, "reply") is False

    def test_label_stripping(self):
        from memory.context import current_message_in_history

        assert (
            current_message_in_history([_user("Alex: hello")], "hello", speaker_labels=("Alex",))
            is True
        )
        # Without labels the prefixed turn is a different string.
        assert current_message_in_history([_user("Alex: hello")], "hello") is False

    def test_blank_current_appends(self):
        from memory.context import current_message_in_history

        assert current_message_in_history([_user("")], "") is False
        assert current_message_in_history([], "") is False

    def test_pure_no_side_effects(self):
        from memory.context import current_message_in_history

        history = [_user("a")]
        assert current_message_in_history(history, "b") is False
        assert history == [_user("a")]


# ---------------------------------------------------------------------------
# generate_draft boundary (model-visible via generate_with_history)
# ---------------------------------------------------------------------------
def _capturing_provider():
    captured = {}

    async def fake_gwh(*, system_instruction="", messages=None, **kwargs):
        captured["system"] = system_instruction
        captured["messages"] = list(messages or [])
        return "draft"

    provider = AsyncMock()
    provider.generate_with_history = fake_gwh
    return provider, captured


async def test_A_legacy_draft_persisted_current_once():
    """history=[..., current] + generate_draft(history, current) → 1 user turn."""
    from workers.llm_worker import generate_draft

    provider, captured = _capturing_provider()
    context = [_system(), _user("earlier"), _assistant("reply"), _user("hello")]
    with patch("workers.llm_worker.get_llm_provider", return_value=provider):
        await generate_draft(context, "hello")
    users = _user_turns(captured["messages"])
    assert [m["content"] for m in users] == ["earlier", "hello"]


async def test_B_legacy_draft_current_absent_appends():
    from workers.llm_worker import generate_draft

    provider, captured = _capturing_provider()
    context = [_system(), _user("old")]
    with patch("workers.llm_worker.get_llm_provider", return_value=provider):
        await generate_draft(context, "new")
    users = _user_turns(captured["messages"])
    assert [m["content"] for m in users] == ["old", "new"]


async def test_C_legacy_draft_stale_current_once():
    """history=[current_old, newer], current=current_old → no second copy."""
    from workers.llm_worker import generate_draft

    provider, captured = _capturing_provider()
    context = [_system(), _user("old"), _user("new")]
    with patch("workers.llm_worker.get_llm_provider", return_value=provider):
        await generate_draft(context, "old")
    users = _user_turns(captured["messages"])
    assert [m["content"] for m in users] == ["old", "new"]


async def test_D_identical_text_distinct_messages_preserved():
    """m1='hi' (tg 101) and m2='hi' (tg 102): both rows visible, both kept."""
    from workers.llm_worker import generate_draft

    provider, captured = _capturing_provider()
    # Canonical post-persistence history: m1 row + m2's own row at tail.
    context = [_system(), _user("hi"), _user("hi")]
    with patch("workers.llm_worker.get_llm_provider", return_value=provider):
        await generate_draft(context, "hi")
    users = _user_turns(captured["messages"])
    assert [m["content"] for m in users] == ["hi", "hi"]


async def test_E_legacy_draft_first_message():
    from workers.llm_worker import generate_draft

    provider, captured = _capturing_provider()
    with patch("workers.llm_worker.get_llm_provider", return_value=provider):
        await generate_draft([_system()], "hello")
    users = _user_turns(captured["messages"])
    assert [m["content"] for m in users] == ["hello"]


# ---------------------------------------------------------------------------
# OneCall snapshot builder boundary (this list IS the model-visible input:
# one_call_generation sends user_content=json.dumps(messages))
# ---------------------------------------------------------------------------
def _auth_state(current, first_name="Alex"):
    return SimpleNamespace(
        current_message=current,
        user={"first_name": first_name},
        profile={},
        persona="",
        persona_name=None,
        participants=None,
        conversation_contract=None,
    )


def _pipeline(msgs):
    return SimpleNamespace(messages=list(msgs), snapshot=None)


async def test_F_onecall_normal_latest_once():
    from core.context_compact import build_one_call_from_snapshot

    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Alex: earlier"},
        {"role": "assistant", "content": "Sunny Skye: hey"},
        {"role": "user", "content": "Alex: hello"},
    ]
    messages = build_one_call_from_snapshot(
        snapshot=None,
        authoritative_state=_auth_state("hello"),
        pipeline_result=_pipeline(history),
    )
    users = _user_turns(messages)
    assert sum(m["content"].count("hello") for m in users) == 1
    assert not any("[PLAYER MESSAGE]" in m["content"] for m in users)


async def test_G_onecall_stale_once():
    """history=[current_old, newer], current=current_old → exactly one copy."""
    from core.context_compact import build_one_call_from_snapshot

    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Alex: old"},
        {"role": "user", "content": "Alex: new"},
    ]
    messages = build_one_call_from_snapshot(
        snapshot=None,
        authoritative_state=_auth_state("old"),
        pipeline_result=_pipeline(history),
    )
    users = _user_turns(messages)
    assert [m["content"] for m in users] == ["Alex: old", "Alex: new"]
    assert not any("[PLAYER MESSAGE]" in m["content"] for m in users)


async def test_E_onecall_first_message():
    from core.context_compact import build_one_call_from_snapshot

    auth = SimpleNamespace(
        current_message="hello",
        user={},
        profile={},
        persona="You are Sunny",
        persona_name="Sunny",
        participants=None,
        conversation_contract=None,
        conversation_state=None,
        recent_messages=(),
        commerce_context_text="",
        summary=None,
        summary_age_days=None,
    )
    messages = build_one_call_from_snapshot(snapshot=None, authoritative_state=auth, pipeline_result=None)
    users = _user_turns(messages)
    assert len(users) == 1
    assert "hello" in users[0]["content"]


async def test_H_m2_collapse_snapshot():
    """Persisted m1/m2/m3, current=m3 → each exactly once, no collapse."""
    from core.context_compact import build_one_call_from_snapshot

    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Alex: m1"},
        {"role": "user", "content": "Alex: m2"},
        {"role": "user", "content": "Alex: m3"},
    ]
    messages = build_one_call_from_snapshot(
        snapshot=None,
        authoritative_state=_auth_state("m3"),
        pipeline_result=_pipeline(history),
    )
    users = _user_turns(messages)
    assert [m["content"] for m in users] == ["Alex: m1", "Alex: m2", "Alex: m3"]


async def test_H_m2_collapse_legacy_draft():
    from workers.llm_worker import generate_draft

    provider, captured = _capturing_provider()
    context = [_system(), _user("m1"), _user("m2"), _user("m3")]
    with patch("workers.llm_worker.get_llm_provider", return_value=provider):
        await generate_draft(context, "m3")
    users = _user_turns(captured["messages"])
    assert [m["content"] for m in users] == ["m1", "m2", "m3"]


# ---------------------------------------------------------------------------
# Agent loop boundary
# ---------------------------------------------------------------------------
def test_E_agent_first_message():
    from agent.loop import AgentLoop
    from agent.state import AgentState

    state = AgentState.create(creator_id=1, user_id=100, current_message="hello", history=())
    loop = AgentLoop(state=state, provider=AsyncMock())
    context = loop._build_context()
    users = _user_turns(context)
    assert [m["content"] for m in users] == ["hello"]


def test_agent_history_tail_once():
    from agent.loop import AgentLoop
    from agent.state import AgentState

    state = AgentState.create(
        creator_id=1,
        user_id=100,
        current_message="hello",
        history=({"role": "user", "content": "earlier"}, {"role": "user", "content": "hello"}),
    )
    loop = AgentLoop(state=state, provider=AsyncMock())
    context = loop._build_context()
    users = _user_turns(context)
    assert [m["content"] for m in users] == ["earlier", "hello"]


def test_agent_stale_once():
    from agent.loop import AgentLoop
    from agent.state import AgentState

    state = AgentState.create(
        creator_id=1,
        user_id=100,
        current_message="old",
        history=({"role": "user", "content": "old"}, {"role": "user", "content": "new"}),
    )
    loop = AgentLoop(state=state, provider=AsyncMock())
    context = loop._build_context()
    users = _user_turns(context)
    assert [m["content"] for m in users] == ["old", "new"]


# ---------------------------------------------------------------------------
# Creator isolation: same user/content across creators are independent
# ---------------------------------------------------------------------------
async def test_I_creator_isolation():
    from memory.context import current_message_in_history
    from workers.llm_worker import generate_draft

    # Pure helper carries no cross-call state.
    assert current_message_in_history([_user("hi")], "hi") is True
    assert current_message_in_history([_user("other")], "hi") is False

    # Same user/content built independently per creator context.
    for _creator in (1, 2):
        provider, captured = _capturing_provider()
        context = [_system(), _user("hi")]
        with patch("workers.llm_worker.get_llm_provider", return_value=provider):
            await generate_draft(context, "hi")
        assert [m["content"] for m in _user_turns(captured["messages"])] == ["hi"]
