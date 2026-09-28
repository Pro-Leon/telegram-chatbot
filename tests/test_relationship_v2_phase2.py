"""Phase 2 unit tests: relationship context derivation + snapshot rules.

No external infra required.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from relationship_v2.domain.context import RelationshipEvidence
from relationship_v2.domain.relationship import RelationshipLifecycle
from relationship_v2.persistence.owners import TABLE_OWNERS
from relationship_v2.services.relationship_context import (
    derive_lifecycle,
    is_snapshot_stale,
    load_relationship_context,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = REPO_ROOT / "relationship_v2" / "persistence" / "schema.sql"
MIGRATION_002 = (
    REPO_ROOT / "relationship_v2" / "persistence" / "migrations" / "002_phase2_snapshots.sql"
)


def _now():
    return datetime.now(UTC)


def test_new_fan_stays_new_without_evidence() -> None:
    ev = RelationshipEvidence()
    assert derive_lifecycle(RelationshipLifecycle.NEW, ev) == RelationshipLifecycle.NEW


def test_progression_requires_evidence_no_skipping() -> None:
    # Strong evidence from NEW steps one level (no new -> deep jump).
    ev = RelationshipEvidence(interaction_count=100, milestone_count=10)
    assert derive_lifecycle(RelationshipLifecycle.NEW, ev) == RelationshipLifecycle.WARMING
    # Warming with same evidence steps to established, not deep.
    assert derive_lifecycle(RelationshipLifecycle.WARMING, ev) == RelationshipLifecycle.ESTABLISHED


def test_long_absence_parks_as_dormant() -> None:
    ev = RelationshipEvidence(interaction_count=60, milestone_count=6, days_since_last=45)
    assert derive_lifecycle(RelationshipLifecycle.DEEP, ev) == RelationshipLifecycle.DORMANT


def test_return_after_dormancy_reactivates() -> None:
    ev = RelationshipEvidence(interaction_count=60, milestone_count=6, days_since_last=2)
    assert derive_lifecycle(RelationshipLifecycle.DORMANT, ev) == RelationshipLifecycle.REACTIVATED


def test_snapshot_immutable_and_scoped() -> None:
    snap = load_relationship_context(
        relationship_id=uuid4(),
        creator_id=1,
        user_id=2,
        current_lifecycle=RelationshipLifecycle.NEW,
        version=1,
        evidence=RelationshipEvidence(interaction_count=6),
        provenance="test",
        now=_now(),
    )
    assert snap.lifecycle == RelationshipLifecycle.WARMING
    assert snap.familiarity == "familiar"
    assert snap.buyer_class == "none"
    with pytest.raises(ValidationError):
        snap.lifecycle = RelationshipLifecycle.DEEP  # type: ignore[misc]
    with pytest.raises(ValueError):
        load_relationship_context(
            relationship_id=uuid4(),
            creator_id=0,
            user_id=2,
            current_lifecycle=RelationshipLifecycle.NEW,
            version=1,
            evidence=RelationshipEvidence(),
            provenance="test",
        )


def test_reentry_context_only_after_absence() -> None:
    fresh = load_relationship_context(
        relationship_id=uuid4(),
        creator_id=1,
        user_id=2,
        current_lifecycle=RelationshipLifecycle.ESTABLISHED,
        version=3,
        evidence=RelationshipEvidence(interaction_count=25, milestone_count=3, days_since_last=1),
        provenance="test",
        now=_now(),
    )
    assert fresh.reentry is None
    away = load_relationship_context(
        relationship_id=uuid4(),
        creator_id=1,
        user_id=2,
        current_lifecycle=RelationshipLifecycle.ESTABLISHED,
        version=3,
        evidence=RelationshipEvidence(interaction_count=25, milestone_count=3, days_since_last=40),
        provenance="test",
        now=_now(),
    )
    assert away.lifecycle == RelationshipLifecycle.DORMANT
    assert away.reentry is not None


def test_stale_snapshots_must_refetch() -> None:
    from datetime import timedelta

    old = load_relationship_context(
        relationship_id=uuid4(),
        creator_id=1,
        user_id=2,
        current_lifecycle=RelationshipLifecycle.NEW,
        version=1,
        evidence=RelationshipEvidence(),
        provenance="test",
        now=_now() - timedelta(seconds=3600),
    )
    assert is_snapshot_stale(old) is True


def test_purchase_refs_are_references_only() -> None:
    snap = load_relationship_context(
        relationship_id=uuid4(),
        creator_id=1,
        user_id=2,
        current_lifecycle=RelationshipLifecycle.ESTABLISHED,
        version=3,
        evidence=RelationshipEvidence(
            interaction_count=25, milestone_count=3, purchase_ref_count=3
        ),
        provenance="test",
        now=_now(),
    )
    assert snap.buyer_class == "repeat"


def test_snapshot_table_owned_and_additive() -> None:
    assert (
        TABLE_OWNERS["v2_relationship_snapshots"]
        == "relationship_v2.services.relationship_context:persist_snapshot"
    )
    sql = SCHEMA.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS v2_relationship_snapshots" in sql
    assert MIGRATION_002.exists()
    mig = MIGRATION_002.read_text(encoding="utf-8")
    assert "v2_relationship_snapshots" in mig


def test_phase2_no_v1_or_commerce_leakage() -> None:
    import ast

    pkg = REPO_ROOT / "relationship_v2" / "services"
    forbidden_prefixes = (
        "commerce",
        "context_engine",
        "integrations.dropfans",
        "integrations.fangate",
    )
    forbidden_modules = {"core.one_call", "core.routing"}
    for f in pkg.rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods.add(a.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        for mod in mods:
            # db.postgres shared infra is allowed; relative + stdlib allowed.
            if mod in ("db.postgres", "db", "") or mod.startswith("relationship_v2"):
                continue
            assert mod not in forbidden_modules, f"{f.name}: {mod}"
            for prefix in forbidden_prefixes:
                assert not (mod == prefix or mod.startswith(prefix + ".")), f"{f.name}: {mod}"
