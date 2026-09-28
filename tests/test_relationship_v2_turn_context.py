"""Stage D2b tests: turn-context composition with fake ports.

No live DB/commerce: all ports faked. Proves: sections compose from
stores, commerce failure degrades (stale section, context still built),
scope fail-closed, budget respected, determinism.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from relationship_v2.services.turn_context import assemble_turn_context


def _rel(**kw) -> dict:
    base = {
        "id": uuid4(),
        "lifecycle": "warming",
        "version": 2,
        "first_interaction_at": datetime(2026, 1, 1, tzinfo=UTC),
        "last_interaction_at": datetime(2026, 9, 20, tzinfo=UTC),
    }
    base.update(kw)
    return base


def _ports(**over):
    async def _rel_fn(c, u, p):
        return _rel()

    async def _zero(*a, **k):
        return 0

    async def _empty(*a, **k):
        return []

    ports = {
        "get_or_create": _rel_fn,
        "count_events": _zero,
        "count_facts": _zero,
        "count_episodes": _zero,
        "list_facts": _empty,
        "list_episodes": _empty,
        "list_signals": _empty,
        "list_loops": _empty,
        "list_intimate": _empty,
    }
    ports.update(over)
    return ports


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_happy_path_composes() -> None:
    async def _facts(c, u, **k):
        return [
            {
                "id": uuid4(),
                "memory_key": "job",
                "value": "photographer",
                "confidence": 0.9,
                "importance": "high",
            }
        ]

    async def _loops(c, u, **k):
        return [
            {
                "id": uuid4(),
                "description": "daughter soccer game",
                "status": "open",
                "priority": "high",
                "expected_at": None,
            }
        ]

    async def _elig(c, u):
        return {"eligible": True, "reason": "ok"}

    async def _opp(c, u):
        return {"active_offer": False, "opportunities": [], "cooldowns": {}}

    async def _purch(c, u):
        return {
            "status": "none",
            "count": 0,
            "last_at": None,
            "owned_refs": [],
            "aftercare": "none",
        }

    ctx = _run(
        assemble_turn_context(
            1,
            2,
            "gen-1",
            now=datetime(2026, 9, 27, tzinfo=UTC),
            **_ports(
                list_facts=_facts,
                list_loops=_loops,
                eligibility_port=_elig,
                opportunity_port=_opp,
                purchase_port=_purch,
            ),
        )
    )
    assert ctx.snapshot.lifecycle.value == "warming"
    assert ctx.commerce is not None and ctx.commerce_unavailable is False
    assert ctx.recall_refs and ctx.recall_refs[0].startswith("loop:")
    assert ctx.assembled.total_chars <= 6000
    names = [s.name for s in ctx.assembled.sections]
    assert "relationship_state" in names and "commerce_context" in names


def test_commerce_failure_degrades_not_fails() -> None:
    async def _boom(c, u):
        raise ConnectionError("commerce down")

    ctx = _run(
        assemble_turn_context(
            1,
            2,
            "gen-2",
            now=datetime(2026, 9, 27, tzinfo=UTC),
            **_ports(
                eligibility_port=_boom, opportunity_port=_boom, purchase_port=_boom
            ),
        )
    )
    assert ctx.commerce is None and ctx.commerce_unavailable is True
    assert "commerce_context" in ctx.assembled.stale_sections
    assert ctx.snapshot.lifecycle.value == "warming"


def test_scope_fail_closed() -> None:
    with pytest.raises(ValueError):
        _run(assemble_turn_context(0, 2, "gen-x", **_ports()))
    with pytest.raises(ValueError):
        _run(assemble_turn_context(1, 2, "", **_ports()))


def test_deterministic() -> None:
    kw = {"now": datetime(2026, 9, 27, tzinfo=UTC)}
    a = _run(assemble_turn_context(1, 2, "gen-d", **_ports(), **kw))
    b = _run(assemble_turn_context(1, 2, "gen-d", **_ports(), **kw))
    assert a.assembled.total_chars == b.assembled.total_chars
    assert [s.lines for s in a.assembled.sections] == [s.lines for s in b.assembled.sections]


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "turn_context.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert not any("ws_manager" in m or "event_subscriber" in m for m in mods)
    assert not any(m.startswith(("chatbotv2.", "workers.")) for m in mods)
    # Repository binds lazily inside default ports only (fake-injectable).
    tree_top = ast.parse(path.read_text(encoding="utf-8"))
    top_level = set()
    for node in tree_top.body:
        if isinstance(node, ast.Import):
            top_level.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module)
    assert "relationship_v2.persistence.repository" not in top_level
