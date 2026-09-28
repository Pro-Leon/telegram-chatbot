"""Phase 4 unit tests: conversation lifecycle, turns, resumption.

No external infra required. Covers CONVERSATION_ENGINE.md behaviors +
STATE_MACHINES.md conversation guards + API_CONTRACTS load/apply semantics.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from relationship_v2.domain.conversation import ConversationLifecycle
from relationship_v2.domain.inbound import InboundDecision, InboundEvent
from relationship_v2.services.conversation_engine import (
    TurnOutcome,
    apply_turn,
    classify_inbound,
    derive_topic,
    load_conversation_state,
    resume_context,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _event(key="k1") -> InboundEvent:
    return InboundEvent(
        creator_id=1,
        user_id=2,
        idempotency_key=key,
        inbound_event_id="evt-1",
        text_hash="abc",
        text_length=12,
    )


def test_fresh_inbound_accepted_duplicate_deduped() -> None:
    assert classify_inbound(_event("k1"), set()).decision == InboundDecision.ACCEPT
    assert classify_inbound(_event("k1"), {"k1"}).decision == InboundDecision.DUPLICATE


def test_bad_scope_rejected_fail_closed() -> None:
    with pytest.raises(ValidationError):
        InboundEvent(
            creator_id=0,
            user_id=2,
            idempotency_key="k",
            inbound_event_id="e",
            text_hash="h",
            text_length=1,
        )


def test_closed_cannot_reactivate_without_resume() -> None:
    r = apply_turn(ConversationLifecycle.CLOSED, "advance", 1, 1)
    assert r.outcome == TurnOutcome.REJECTED
    assert r.lifecycle == ConversationLifecycle.CLOSED


def test_failed_is_recovery_state() -> None:
    r = apply_turn(ConversationLifecycle.ACTIVE, "fail", 3, 3)
    assert r.outcome == TurnOutcome.FAILED
    assert r.lifecycle == ConversationLifecycle.FAILED
    back = apply_turn(ConversationLifecycle.FAILED, "recover", 3, 3)
    assert back.outcome == TurnOutcome.ADVANCED
    assert back.lifecycle == ConversationLifecycle.ACTIVE


def test_stale_version_suppressed() -> None:
    r = apply_turn(ConversationLifecycle.ACTIVE, "pause", 2, 3)
    assert r.outcome == TurnOutcome.STALE
    assert r.lifecycle == ConversationLifecycle.ACTIVE


def test_short_message_never_closes_thread() -> None:
    assert derive_topic("football", False, None) == "football"
    assert derive_topic("football", False, "random") == "football"
    assert derive_topic("football", True, "work") == "work"


def test_paused_resumption_restores_state() -> None:
    state = load_conversation_state(
        uuid4(), ConversationLifecycle.PAUSED, "football", 4, datetime.now(UTC)
    )
    assert state.lifecycle == ConversationLifecycle.PAUSED
    assert state.version == 4
    summary = resume_context(state.topic, state.lifecycle, 5, state.as_of)
    assert "football" in summary
    assert "5d" in summary
    r = apply_turn(ConversationLifecycle.PAUSED, "resume", 4, 4)
    assert r.lifecycle == ConversationLifecycle.RESUMED


def test_failed_turn_retryable_with_same_signal() -> None:
    first = apply_turn(ConversationLifecycle.ACTIVE, "fail", 1, 1)
    retry = apply_turn(ConversationLifecycle.ACTIVE, "fail", 1, 1)
    assert first.outcome == retry.outcome == TurnOutcome.FAILED


def test_phase4_no_v1_or_commerce_leakage() -> None:
    import ast

    pkg = REPO_ROOT / "relationship_v2" / "services"
    target = pkg / "conversation_engine.py"
    tree = ast.parse(target.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for mod in mods:
            if mod.startswith("relationship_v2") or mod in ("db.postgres", "db"):
                continue
            assert not (mod == "commerce" or mod.startswith("commerce.")), mod
            assert "context_engine" not in mod, mod
            assert "integrations.dropfans" not in mod, mod
            assert "core.one_call" not in mod, mod
            assert "core.routing" not in mod, mod
