"""Stage C4 tests: relationship reasoning from snapshot + evidence + counts.

Pure composer, no infra. Proves: new fans get minimal answers, long-term
fans read strong, dormant reads fragile with reentry, buyers classified,
unfinished counts loops, boundaries surfaced, provenance required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from relationship_v2.domain.context import RelationshipContextSnapshot, RelationshipEvidence
from relationship_v2.domain.relationship import RelationshipLifecycle
from relationship_v2.services.relationship_reasoning import (
    StoreCounts,
    derive_continuity,
    reason_about,
)


def _ev(**kw) -> RelationshipEvidence:
    base = {"interaction_count": 0}
    base.update(kw)
    return RelationshipEvidence(**base)


def _snap(lifecycle=RelationshipLifecycle.NEW, **kw) -> RelationshipContextSnapshot:
    base = {
        "relationship_id": uuid4(),
        "creator_id": 1,
        "user_id": 2,
        "lifecycle": lifecycle,
        "familiarity": "stranger",
        "comfort": "low",
        "version": 1,
        "as_of": datetime.now(UTC),
        "evidence_hash": "abc",
        "provenance": "test",
    }
    base.update(kw)
    return RelationshipContextSnapshot(**base)


def test_new_fan_minimal_answers() -> None:
    r = reason_about(_snap(), _ev(), StoreCounts(), "test")
    assert r.continuity == "fragile"
    assert r.questions_answered == ["who", "how_long"]


def test_long_term_strong() -> None:
    ev = _ev(
        interaction_count=60,
        meaningful_interaction_count=35,
        milestone_count=6,
        days_since_first=120,
        days_since_last=2,
    )
    counts = StoreCounts(episode_count=10, fact_count=14)
    assert derive_continuity(ev, counts) == "strong"
    r = reason_about(
        _snap(RelationshipLifecycle.DEEP, familiarity="longstanding", comfort="high"),
        ev,
        counts,
        "test",
    )
    assert len(r.questions_answered) == 9
    assert "120" in r.tenure_answer


def test_dormant_fragile_with_reentry() -> None:
    from relationship_v2.services.relationship_context import build_reentry

    ev = _ev(interaction_count=40, milestone_count=3, days_since_last=45)
    snap = _snap(RelationshipLifecycle.DORMANT)
    reentry = build_reentry(RelationshipLifecycle.DORMANT, ev, True)
    snap = snap.model_copy(update={"reentry": reentry, "absence_days": 45})
    assert derive_continuity(ev, StoreCounts()) == "fragile"
    r = reason_about(snap, ev, StoreCounts(), "test")
    assert "45" in r.absence_answer


def test_unfinished_counts_loops_and_facts() -> None:
    counts = StoreCounts(open_loop_count=3, due_loop_count=1, fact_count=7)
    r = reason_about(_snap(), _ev(interaction_count=10), counts, "test")
    assert "3 open threads (1 due)" in r.unfinished_answer
    assert "7 durable facts" in r.unfinished_answer


def test_boundaries_and_buyer_surfaced() -> None:
    snap = _snap(has_boundaries=True, buyer_class="repeat")
    r = reason_about(snap, _ev(interaction_count=25, purchase_ref_count=3), StoreCounts(), "t")
    assert "present" in r.intimate_answer
    assert "repeat" in r.engagement_answer


def test_provenance_required() -> None:
    with pytest.raises(ValueError):
        reason_about(_snap(), _ev(), StoreCounts(), "")


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "relationship_reasoning.py"
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
