"""Phase 5 unit tests: budgets, prioritization, freshness, conflicts, grounding.

No external infra required. Covers CONTEXT_ASSEMBLY.md rules.
"""

from __future__ import annotations

import pathlib

import pytest

from relationship_v2.services.context_assembly import (
    COMPOSITION_ORDER,
    TOTAL_BUDGET_CHARS,
    assemble,
    resolve_conflicts,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _inputs(**overrides: list[str]) -> dict[str, list[str]]:
    base: dict[str, list[str]] = {
        "system": ["sys"],
        "creator": ["creator: Sunny (from system config)"],
        "fan_identity": ["fan 2"],
        "relationship_state": ["established, familiar"],
        "long_term_memory": ["job=photographer"],
        "episodic_memory": ["episode: soccer game"],
        "recent_conversation": ["hey", "hi there"],
        "conversation_state": ["active on football"],
        "engagement_signals": ["teasing positive x4"],
        "escalation_state": ["relationship stage"],
        "commerce_context": ["no eligible offers"],
        "response_constraints": ["no invented prices"],
    }
    base.update(overrides)
    return base


def test_composition_order_fixed() -> None:
    ctx = assemble("g1", 1, 2, _inputs())
    assert [s.name for s in ctx.sections] == list(COMPOSITION_ORDER)
    assert ctx.total_chars <= TOTAL_BUDGET_CHARS


def test_constraints_survive_overflow() -> None:
    big = ["x" * 500 for _ in range(30)]
    ctx = assemble("g2", 1, 2, _inputs(long_term_memory=big, system=big))
    names = {s.name: s for s in ctx.sections}
    assert names["response_constraints"].lines == ["no invented prices"]
    assert names["commerce_context"].lines == ["no eligible offers"]
    assert ctx.total_chars <= TOTAL_BUDGET_CHARS
    assert any(s.truncated for s in ctx.sections)


def test_stale_sections_excluded_never_current() -> None:
    ctx = assemble("g3", 1, 2, _inputs(), stale={"relationship_state"})
    assert "relationship_state" in ctx.stale_sections
    names = {s.name: s for s in ctx.sections}
    assert names["relationship_state"].lines == []


def test_commerce_beats_memory_contradiction() -> None:
    resolved, log = resolve_conflicts(
        ["price=$20 special deal"],
        ["price=$35 confirmed"],
        [],
        set(),
        set(),
    )
    assert all("20" not in line for line in resolved)
    assert any(r.rule == "commerce_beats_memory" for r in log)


def test_scope_fail_closed() -> None:
    with pytest.raises(ValueError):
        assemble("g4", 0, 2, _inputs())


def test_deterministic_same_inputs_same_output() -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    a = assemble("g5", 1, 2, _inputs(), now=now)
    b = assemble("g5", 1, 2, _inputs(), now=now)
    assert a == b


def test_phase5_no_v1_or_commerce_leakage() -> None:
    import ast

    target = REPO_ROOT / "relationship_v2" / "services" / "context_assembly.py"
    tree = ast.parse(target.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for mod in mods:
            if mod.startswith("relationship_v2"):
                continue
            assert not (mod == "commerce" or mod.startswith("commerce.")), mod
            assert "context_engine" not in mod, mod
            assert "integrations.dropfans" not in mod, mod
