"""Stage C1 tests: open-loop domain, lifecycle, due derivation, DDL.

No live PostgreSQL: repository SQL shape verified statically; pure
transitions + DDL + ownership verified directly.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from relationship_v2.domain.open_loop import (
    OpenLoop,
    OpenLoopStatus,
    is_valid_open_loop_transition,
)
from relationship_v2.persistence.owners import TABLE_OWNERS
from relationship_v2.services.open_loops import LoopOutcome, is_due, transition_loop

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = REPO_ROOT / "relationship_v2" / "persistence" / "schema.sql"
MIGRATION = REPO_ROOT / "relationship_v2" / "persistence" / "migrations" / "005_open_loops.sql"


def _loop(status: OpenLoopStatus = OpenLoopStatus.OPEN) -> OpenLoop:
    now = datetime.now(UTC)
    return OpenLoop(
        id=uuid4(),
        creator_id=1,
        user_id=2,
        relationship_id=uuid4(),
        description="daughter soccer game Saturday",
        status=status,
        provenance="test",
        created_at=now,
        updated_at=now,
    )


def test_transitions_guarded() -> None:
    assert is_valid_open_loop_transition(OpenLoopStatus.OPEN, OpenLoopStatus.REFERENCED)
    assert is_valid_open_loop_transition(OpenLoopStatus.DUE, OpenLoopStatus.RESOLVED)
    assert is_valid_open_loop_transition(OpenLoopStatus.REFERENCED, OpenLoopStatus.DUE)
    assert not is_valid_open_loop_transition(OpenLoopStatus.RESOLVED, OpenLoopStatus.OPEN)
    assert not is_valid_open_loop_transition(OpenLoopStatus.DISMISSED, OpenLoopStatus.DUE)
    assert not is_valid_open_loop_transition(OpenLoopStatus.EXPIRED, OpenLoopStatus.REFERENCED)


def test_service_signals() -> None:
    r = transition_loop(OpenLoopStatus.OPEN, "reference")
    assert r.outcome == LoopOutcome.ADVANCED and r.status == OpenLoopStatus.REFERENCED
    r = transition_loop(OpenLoopStatus.RESOLVED, "reference")
    assert r.outcome == LoopOutcome.REJECTED and r.status == OpenLoopStatus.RESOLVED
    r = transition_loop(OpenLoopStatus.OPEN, "bogus")
    assert r.outcome == LoopOutcome.REJECTED


def test_due_derivation() -> None:
    now = datetime.now(UTC)
    assert is_due(OpenLoopStatus.OPEN, now - timedelta(days=1), now) is True
    assert is_due(OpenLoopStatus.OPEN, now + timedelta(days=1), now) is False
    assert is_due(OpenLoopStatus.OPEN, None, now) is False
    assert is_due(OpenLoopStatus.RESOLVED, now - timedelta(days=1), now) is False
    assert is_due(OpenLoopStatus.REFERENCED, now - timedelta(hours=1), now) is True


def test_loop_description_bounded() -> None:
    import pytest
    from pydantic import ValidationError

    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        OpenLoop(
            id=uuid4(),
            creator_id=1,
            user_id=2,
            relationship_id=uuid4(),
            description="",
            provenance="test",
            created_at=now,
            updated_at=now,
        )
    assert _loop().follow_up_attempts == 0


def test_migration_ddl() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS v2_open_loops" in sql
    assert "CHECK (status IN ('open','due','referenced','resolved','expired','dismissed'))" in sql
    assert "follow_up_attempts INTEGER NOT NULL DEFAULT 0" in sql
    assert "idx_v2_loops_scope" in sql
    assert "DROP TABLE IF EXISTS v2_open_loops;" in sql
    assert TABLE_OWNERS["v2_open_loops"].endswith(":create_open_loop")
    schema = SCHEMA.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS v2_open_loops" in schema


def test_repository_guards_terminal_in_sql() -> None:
    src = (REPO_ROOT / "relationship_v2" / "persistence" / "repository.py").read_text(
        encoding="utf-8"
    )
    for fn in (
        "def create_open_loop",
        "def mark_loop_referenced",
        "def resolve_open_loop",
        "def close_open_loop",
        "def list_active_loops",
    ):
        assert fn in src
    assert "AND status IN ('open','due','referenced')" in src


def test_no_forbidden_imports() -> None:
    import ast

    for rel in (
        "relationship_v2/domain/open_loop.py",
        "relationship_v2/services/open_loops.py",
    ):
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
        assert "relationship_v2.persistence.repository" not in mods
