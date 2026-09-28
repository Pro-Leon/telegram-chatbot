"""Stage D2a tests: read-layer guards, bounds, and SQL shape.

No live PostgreSQL: scope/limit guards execute before any pool access;
SQL text verified statically. Live read verification belongs to Stage F.
"""

from __future__ import annotations

import pathlib

import pytest

from relationship_v2.persistence import repository

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = (REPO_ROOT / "relationship_v2" / "persistence" / "repository.py").read_text(
    encoding="utf-8"
)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_scope_guards_fire_before_pool() -> None:
    with pytest.raises(ValueError):
        _run(repository.list_current_facts(0, 2))
    with pytest.raises(ValueError):
        _run(repository.list_recent_episodes(1, 0))
    with pytest.raises(ValueError):
        _run(repository.list_signals(1, -3))
    with pytest.raises(ValueError):
        _run(repository.count_events(1, 2, []))


def test_limit_bounds() -> None:
    with pytest.raises(ValueError):
        _run(repository.list_current_facts(1, 2, limit=0))
    with pytest.raises(ValueError):
        _run(repository.list_current_facts(1, 2, limit=201))
    with pytest.raises(ValueError):
        _run(repository.list_recent_episodes(1, 2, limit=101))
    with pytest.raises(ValueError):
        _run(repository.list_signals(1, 2, limit=500))


def test_reads_are_select_only() -> None:
    import ast

    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in (
            "list_current_facts",
            "list_recent_episodes",
            "list_signals",
            "count_events",
            "list_active_loops",
            "list_intimate_history",
        ):
            body = ast.unparse(node)
            assert "INSERT" not in body
            assert "UPDATE" not in body
            assert "DELETE" not in body


def test_current_only_and_ordering() -> None:
    assert "AND status = 'current'" in SRC
    assert "ORDER BY updated_at DESC" in SRC
    assert "ORDER BY created_at DESC" in SRC
    assert "ORDER BY last_observed_at DESC" in SRC
    assert "event_type = ANY($3)" in SRC
    assert "LIMIT $4" in SRC or "LIMIT $3" in SRC


def test_relationship_scoping_optional() -> None:
    assert "relationship_id is not None" in SRC


def test_exports_wired() -> None:
    from relationship_v2.persistence import __all__ as pkg_all

    for name in (
        "list_current_facts",
        "list_recent_episodes",
        "list_signals",
        "count_events",
    ):
        assert hasattr(repository, name), name
        assert name in pkg_all, name
