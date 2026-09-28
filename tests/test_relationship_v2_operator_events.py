"""Stage E1 tests: operator outcomes map to non-mutating events; only the
confirmed send builds ResponseSent, with references (never raw content).
"""

from __future__ import annotations

import pytest

from relationship_v2.domain.event import V2EventType, is_relationship_mutating
from relationship_v2.integration.operator_events import (
    build_response_sent,
    classify_action,
)


def test_rejected_discards() -> None:
    d = classify_action("rejected")
    assert d.event_type is None and d.mutating is False


def test_approval_is_not_send() -> None:
    d = classify_action("approved")
    assert d.event_type == V2EventType.RESPONSE_APPROVED
    assert d.mutating is False
    assert not is_relationship_mutating(d.event_type)


def test_edited_records_without_mutating() -> None:
    d = classify_action("approved", was_edited=True)
    assert d.event_type == V2EventType.RESPONSE_EDITED
    assert d.mutating is False
    assert not is_relationship_mutating(d.event_type)


def test_failed_no_mutation() -> None:
    d = classify_action("failed")
    assert d.event_type == V2EventType.RESPONSE_FAILED
    assert d.mutating is False


def test_unknown_no_event() -> None:
    d = classify_action("mystery")
    assert d.event_type is None and d.mutating is False


def test_sent_builder_mutating_with_refs_only() -> None:
    ev = build_response_sent(1, 2, "msg-9", "gen-9", "send_worker", True, queue_id=41)
    assert ev.event_type == V2EventType.RESPONSE_SENT
    assert is_relationship_mutating(ev.event_type) is True
    assert ev.idempotency_key == "sent-msg-9"
    assert ev.payload["message_id"] == "msg-9"
    assert ev.payload["was_edited"] is True
    assert ev.payload["queue_id"] == 41
    assert "content" not in ev.payload and "text" not in ev.payload
    assert ev.generation_id == "gen-9"


def test_sent_builder_fail_closed() -> None:
    with pytest.raises(ValueError):
        build_response_sent(0, 2, "m", "g", "s")
    with pytest.raises(ValueError):
        build_response_sent(1, 2, "", "g", "s")


def test_no_protected_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "integration"
        / "operator_events.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert not any(
        m.startswith(("chatbotv2.", "workers.", "core.routing")) for m in mods
    )
