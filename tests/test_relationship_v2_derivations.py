"""Stage F6 tests: engagement/reciprocity/intimacy bands + due-sweep scan.

Pure derivations, no infra. Proves bands follow the spec categories,
stale caps apply, balance drives reciprocity, intimate accumulation bands
without permission semantics, sweep finds only actionable overdue loops.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from relationship_v2.services.relationship_derivations import (
    derive_engagement,
    derive_intimacy_trajectory,
    derive_reciprocity,
    find_due_loops,
)


def test_engagement_bands() -> None:
    assert derive_engagement(0, 0, None) == "low"
    assert derive_engagement(60, 35, 2) == "high"
    assert derive_engagement(60, 35, 90) == "moderate"
    assert derive_engagement(8, 2, 5) == "moderate"
    assert derive_engagement(2, 0, 1) == "low"
    with pytest.raises(ValueError):
        derive_engagement(-1, 0, None)


def test_reciprocity_balance() -> None:
    assert derive_reciprocity(0, 0) == "low"
    assert derive_reciprocity(1, 0) == "low"
    assert derive_reciprocity(2, 0) == "moderate"
    assert derive_reciprocity(10, 9) == "high"
    assert derive_reciprocity(10, 1) == "moderate"
    with pytest.raises(ValueError):
        derive_reciprocity(1, -1)


def test_intimacy_trajectory() -> None:
    assert derive_intimacy_trajectory(0, 0, 0) == "none"
    assert derive_intimacy_trajectory(1, 0, 0) == "playful"
    assert derive_intimacy_trajectory(1, 1, 0) == "romantic"
    assert derive_intimacy_trajectory(0, 0, 1) == "intimate"
    assert derive_intimacy_trajectory(2, 2, 2) == "deeply_intimate"
    with pytest.raises(ValueError):
        derive_intimacy_trajectory(0, 0, -1)


def test_due_sweep_scan() -> None:
    now = datetime.now(UTC)
    past = now - timedelta(days=2)
    future = now + timedelta(days=2)
    loops = [
        {"id": "a", "status": "open", "expected_at": past},
        {"id": "b", "status": "open", "expected_at": future},
        {"id": "c", "status": "resolved", "expected_at": past},
        {"id": "d", "status": "bogus", "expected_at": past},
        {"id": "e", "status": "referenced", "expected_at": past},
    ]
    found = find_due_loops(loops, now)
    assert [loop["id"] for loop in found] == ["a", "e"]


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "relationship_derivations.py"
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
