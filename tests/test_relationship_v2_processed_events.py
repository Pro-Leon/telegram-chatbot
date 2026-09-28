"""Stage A2 tests: processed-event idempotency DDL + domain + owners.

No live PostgreSQL required: verifies versioned migration SQL, cumulative
schema, ownership map, domain validation, and repository SQL shape.
"""

from __future__ import annotations

import pathlib
from uuid import uuid4

import pytest
from pydantic import ValidationError

from relationship_v2.domain.event import ProcessedEvent
from relationship_v2.persistence.owners import TABLE_OWNERS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = REPO_ROOT / "relationship_v2" / "persistence" / "schema.sql"
MIGRATION = REPO_ROOT / "relationship_v2" / "persistence" / "migrations" / "004_processed_events.sql"


def test_migration_ddl() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS v2_processed_events" in sql
    assert "CONSTRAINT uq_v2_processed_event UNIQUE (event_id, processor)" in sql
    assert "idx_v2_processed_event" in sql
    assert "DROP TABLE IF EXISTS v2_processed_events;" in sql
    for legacy in ("users", "messages", "operator_queue"):
        assert f"ALTER TABLE {legacy}" not in sql
        assert f"CREATE TABLE IF NOT EXISTS {legacy}" not in sql


def test_schema_cumulative() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS v2_processed_events" in sql
    assert "CONSTRAINT uq_v2_processed_event UNIQUE (event_id, processor)" in sql
    # No duplicate representation of pattern storage without reason:
    # engagement signals remain the single evidence-count store.
    assert "CREATE TABLE IF NOT EXISTS v2_patterns" not in sql


def test_owners_single_owner() -> None:
    assert (
        TABLE_OWNERS["v2_processed_events"]
        == "relationship_v2.persistence.repository:mark_event_processed"
    )
    assert len(TABLE_OWNERS) == 12


def test_processed_event_domain_fail_closed() -> None:
    from datetime import UTC, datetime

    with pytest.raises(ValidationError):
        ProcessedEvent(
            id=uuid4(),
            event_id="",
            processor="p",
            creator_id=1,
            user_id=2,
            processed_at=datetime.now(UTC),
        )
    ev = ProcessedEvent(
        id=uuid4(),
        event_id="e1",
        processor="event_processor",
        creator_id=1,
        user_id=2,
        processed_at=datetime.now(UTC),
    )
    assert ev.event_id == "e1"


def test_repository_sql_is_idempotent() -> None:
    import ast

    path = REPO_ROOT / "relationship_v2" / "persistence" / "repository.py"
    src = path.read_text(encoding="utf-8")
    assert "def mark_event_processed" in src
    assert "def is_event_processed" in src
    assert "ON CONFLICT (event_id, processor) DO NOTHING" in src
    tree = ast.parse(src)
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
