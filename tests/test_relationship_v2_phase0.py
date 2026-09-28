"""Stage A1 tests: V2 flags default off + event envelope reality rule.

No external infra required.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from relationship_v2.domain.event import (
    NON_MUTATING_TYPES,
    V2Event,
    V2EventType,
    is_relationship_mutating,
)


def test_flags_default_off() -> None:
    from core import architecture_router as router
    from core.config import get_settings

    s = get_settings()
    assert s.relationship_v2_enabled is False
    assert s.relationship_v2_read_enabled is False
    assert s.relationship_v2_write_enabled is False
    assert s.relationship_v2_shadow_mode is False
    assert router.is_v2_read_enabled() is False
    assert router.is_v2_write_enabled() is False
    assert router.is_v2_shadow_mode() is False


def test_event_envelope_fail_closed() -> None:
    with pytest.raises(ValidationError):
        V2Event(
            event_id="e1",
            event_type=V2EventType.FAN_MESSAGE_RECEIVED,
            creator_id=0,
            user_id=1,
            idempotency_key="k1",
            source="test",
        )
    ev = V2Event(
        event_id="e1",
        event_type=V2EventType.FAN_MESSAGE_RECEIVED,
        creator_id=1,
        user_id=2,
        idempotency_key="k1",
        source="test",
    )
    assert ev.schema_version == "v2-event-1"


def test_reality_rule_drafts_never_mutate() -> None:
    for t in (
        V2EventType.RESPONSE_GENERATED,
        V2EventType.RESPONSE_REJECTED,
        V2EventType.RESPONSE_EDITED,
        V2EventType.RESPONSE_APPROVED,
    ):
        assert t in NON_MUTATING_TYPES
        assert is_relationship_mutating(t) is False
    assert is_relationship_mutating(V2EventType.RESPONSE_SENT) is True
    assert is_relationship_mutating(V2EventType.FAN_MESSAGE_RECEIVED) is True
    assert is_relationship_mutating(V2EventType.PURCHASE_COMPLETED) is True


def test_no_commerce_or_provider_imports_in_event_module() -> None:
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "relationship_v2" / "domain" / "event.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert "relationship_v2.domain.event" not in mods
