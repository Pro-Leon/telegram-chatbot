"""Stage F9a tests: inbound orchestration from shipped primitives.

No live Redis/DB/LLM: guard, event ports, and turn-context port faked.
Proves: fresh text plans with strategy, redelivery duplicates, contention
defers, blanks reject, scope fail-closed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from relationship_v2.domain.context import RelationshipContextSnapshot
from relationship_v2.domain.relationship import RelationshipLifecycle
from relationship_v2.services.context_assembly import assemble
from relationship_v2.services.turn_context import TurnContext
from relationship_v2.services.turn_orchestrator import InboundOutcome, run_inbound_turn

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _ctx() -> TurnContext:
    snap = RelationshipContextSnapshot(
        relationship_id=uuid4(), creator_id=1, user_id=2,
        lifecycle=RelationshipLifecycle.NEW, familiarity="stranger",
        comfort="low", version=1, as_of=NOW, evidence_hash="x", provenance="t",
    )
    return TurnContext(
        generation_id="gen-1", creator_id=1, user_id=2,
        relationship_id=snap.relationship_id, snapshot=snap,
        commerce=None, commerce_unavailable=True, recall_refs=["loop:x"],
        assembled=assemble("gen-1", 1, 2, {}, now=NOW), provenance="t",
    )


class _Store:
    def __init__(self) -> None:
        self.processed: set[str] = set()

    async def is_processed(self, event_id: str, processor: str) -> bool:
        return event_id in self.processed

    async def get_or_create(self, c: int, u: int, p: str) -> dict:
        return {"id": uuid4()}

    async def create(self, **kw) -> dict:
        return {"id": uuid4()}

    async def mark(self, **kw) -> dict:
        self.processed.add(kw["event_id"])
        return {"id": uuid4()}


def _ports(store: _Store, held: bool = True) -> dict:
    async def _acquire(key: str, ttl: int) -> bool:
        return held

    async def _release(key: str) -> None:
        return None

    async def _ctx_port(*a, **k) -> TurnContext:
        return _ctx()

    return {
        "turn_context": _ctx_port,
        "is_processed": store.is_processed,
        "get_or_create": store.get_or_create,
        "create_event": store.create,
        "mark_processed": store.mark,
        "acquire": _acquire,
        "release": _release,
    }


def test_applied_plans_with_strategy() -> None:
    r = _run(
        run_inbound_turn(1, 2, "hey, just got home", "msg-1", "gen-1", now=NOW,
                         **_ports(_Store()))
    )
    assert r.outcome == InboundOutcome.APPLIED
    assert r.plan is not None and r.strategy is not None
    assert r.recall_refs == ["loop:x"]
    assert r.plan.provenance == "relationship_v2.services.turn_orchestrator"


def test_duplicate_redelivery() -> None:
    store = _Store()
    first = _run(
        run_inbound_turn(1, 2, "hey", "msg-2", "gen-2", now=NOW, **_ports(store))
    )
    second = _run(
        run_inbound_turn(1, 2, "hey", "msg-2", "gen-9", now=NOW, **_ports(store))
    )
    assert first.outcome == InboundOutcome.APPLIED
    assert second.outcome == InboundOutcome.DUPLICATE
    assert second.plan is None


def test_contended_defers() -> None:
    r = _run(
        run_inbound_turn(1, 2, "hey", "msg-3", "gen-3", now=NOW,
                         **_ports(_Store(), held=False))
    )
    assert r.outcome == InboundOutcome.DEFERRED
    assert r.plan is None


def test_blank_rejects() -> None:
    r = _run(
        run_inbound_turn(1, 2, "   ", "msg-4", "gen-4", now=NOW, **_ports(_Store()))
    )
    assert r.outcome == InboundOutcome.REJECTED


def test_scope_fail_closed() -> None:
    with pytest.raises(ValueError):
        _run(run_inbound_turn(0, 2, "hey", "m", "g", **_ports(_Store())))


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "turn_orchestrator.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert "relationship_v2.persistence.repository" not in mods
    assert not any(m.startswith(("chatbotv2.", "workers.")) for m in mods)
