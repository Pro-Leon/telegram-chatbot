"""Stage C3 tests: intimate domain, classifier, DDL, continuity rules.

No live PostgreSQL: repository SQL verified statically. Proves: comfort
abstracts never echo content, boundaries are critical, history is not
permission (no permit field exists), terminal states hold.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from relationship_v2.domain.intimacy import (
    IntimateKind,
    IntimateMemory,
    IntimateStatus,
    is_valid_intimate_transition,
)
from relationship_v2.persistence.owners import TABLE_OWNERS
from relationship_v2.services.intimate_history import plan_intimate
from relationship_v2.services.memory_extraction import extract_candidates

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = REPO_ROOT / "relationship_v2" / "persistence" / "schema.sql"
MIGRATION = (
    REPO_ROOT / "relationship_v2" / "persistence" / "migrations" / "006_intimate_history.sql"
)


def _mem(kind: IntimateKind = IntimateKind.COMFORT) -> IntimateMemory:
    now = datetime.now(UTC)
    return IntimateMemory(
        id=uuid4(),
        creator_id=1,
        user_id=2,
        relationship_id=uuid4(),
        kind=kind,
        signal="responsive to playful tone (abstract; no verbatim stored)",
        confidence=0.55,
        provenance="test",
        created_at=now,
        updated_at=now,
    )


def test_terminal_transitions_hold() -> None:
    assert is_valid_intimate_transition(IntimateStatus.CURRENT, IntimateStatus.SUPERSEDED)
    assert not is_valid_intimate_transition(IntimateStatus.SUPERSEDED, IntimateStatus.CURRENT)
    assert not is_valid_intimate_transition(IntimateStatus.ARCHIVED, IntimateStatus.CURRENT)


def test_history_is_not_permission() -> None:
    m = _mem()
    assert not hasattr(m, "permitted")
    assert not hasattr(m, "consent")
    assert m.status == IntimateStatus.CURRENT


def test_comfort_plan_abstract_never_verbatim() -> None:
    raw = "I'm so horny right now, I want you naked"
    cands = extract_candidates(raw, source_event_id="ev-1", provenance="test")
    plans = [p for c in cands if (p := plan_intimate(c)) is not None]
    assert plans, "intimate turn must yield a comfort plan"
    for p in plans:
        assert p.kind == IntimateKind.COMFORT
        assert raw.lower() not in p.signal.lower()
        assert "naked" not in p.signal.lower() and "horny" not in p.signal.lower()


def test_boundary_plan_critical() -> None:
    cands = extract_candidates(
        "don't talk about my ex anymore", source_event_id="ev-2", provenance="test"
    )
    plans = [p for c in cands if (p := plan_intimate(c)) is not None]
    assert plans
    assert plans[0].kind == IntimateKind.BOUNDARY
    assert plans[0].importance == "critical"
    assert plans[0].confidence >= 0.9


def test_ordinary_fact_yields_no_plan() -> None:
    cands = extract_candidates("I work as a photographer.", "ev-3", "test")
    assert [p for c in cands if (p := plan_intimate(c)) is not None] == []


def test_signal_bounded() -> None:
    with pytest.raises(ValidationError):
        IntimateMemory(
            **{**_mem().model_dump(), "signal": "x" * 281},
        )


def test_migration_ddl() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS v2_intimate_history" in sql
    assert "CHECK (kind IN ('comfort','preference','boundary','turnoff','milestone'))" in sql
    assert "uq_v2_intimate_current" in sql
    assert "DROP TABLE IF EXISTS v2_intimate_history;" in sql
    for raw_col in ("raw_text", "raw_content", "verbatim", "message_text", "content TEXT"):
        assert raw_col not in sql.lower()
    assert TABLE_OWNERS["v2_intimate_history"].endswith(":record_intimate_signal")
    assert "CREATE TABLE IF NOT EXISTS v2_intimate_history" in SCHEMA.read_text(
        encoding="utf-8"
    )


def test_repository_idempotent_shape() -> None:
    src = (REPO_ROOT / "relationship_v2" / "persistence" / "repository.py").read_text(
        encoding="utf-8"
    )
    assert "def record_intimate_signal" in src
    assert "def list_intimate_history" in src
    assert "ON CONFLICT (relationship_id, kind, signal) WHERE status = 'current'" in src


def test_no_forbidden_imports() -> None:
    import ast

    for rel in (
        "relationship_v2/domain/intimacy.py",
        "relationship_v2/services/intimate_history.py",
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
