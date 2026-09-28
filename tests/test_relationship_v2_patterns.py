"""Stage C2 tests: pattern strength from evidence counts + decay.

Pure decisions, no infra. Proves: single events never rule, dominance
learns both leanings, contradiction contests, stale evidence decays.
"""

from __future__ import annotations

import pytest

from relationship_v2.services.pattern_learning import (
    PatternLeaning,
    PatternStatus,
    assess_pattern,
    decayed_evidence,
)


def test_single_observation_never_rules() -> None:
    a = assess_pattern("teasing", "topic_affinity", 1, 0, 0.78)
    assert a.status == PatternStatus.WEAK
    assert a.total == 1


def test_spec_example_learns_positive() -> None:
    a = assess_pattern("teasing_about_work", "topic_affinity", 8, 2, 0.78)
    assert a.status == PatternStatus.LEARNED
    assert a.leaning == PatternLeaning.POSITIVE


def test_repeated_ignores_learn_negative() -> None:
    a = assess_pattern("generic_compliments", "topic_affinity", 1, 5, 0.7)
    assert a.status == PatternStatus.LEARNED
    assert a.leaning == PatternLeaning.NEGATIVE


def test_contradictory_evidence_contests() -> None:
    a = assess_pattern("teasing", "topic_affinity", 3, 3, 0.8)
    assert a.status == PatternStatus.CONTESTED
    assert a.adjusted_confidence < 0.8


def test_two_observations_emerging() -> None:
    a = assess_pattern("music", "topic_affinity", 2, 0, 0.6)
    assert a.status == PatternStatus.EMERGING


def test_zero_evidence_weak_neutral() -> None:
    a = assess_pattern("x", "y", 0, 0, 0.5)
    assert a.status == PatternStatus.WEAK
    assert a.leaning == PatternLeaning.NEUTRAL


def test_decay_halves_at_half_life() -> None:
    assert decayed_evidence(8, 30.0) == pytest.approx(4.0)
    assert decayed_evidence(8, 0.0) == pytest.approx(8.0)
    assert decayed_evidence(8, 60.0) == pytest.approx(2.0)


def test_decay_fail_closed() -> None:
    with pytest.raises(ValueError):
        decayed_evidence(-1, 5.0)
    with pytest.raises(ValueError):
        decayed_evidence(3, -1.0)
    with pytest.raises(ValueError):
        assess_pattern("t", "b", 1, 0, 1.5)
    with pytest.raises(ValueError):
        assess_pattern("", "b", 1, 0, 0.5)


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "pattern_learning.py"
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
