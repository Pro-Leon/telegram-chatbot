"""Stage C6 tests: absence bands and return plans.

Pure logic, no infra. Proves: 3d/30d anchors, identity always retained
(never cold-start), long absence acknowledges, lapsed continues threads,
no hardcoded fan-facing phrases in guidance.
"""

from __future__ import annotations

import pytest

from relationship_v2.services.absence_service import (
    AbsenceBand,
    classify_absence,
    plan_return,
)


def test_anchors() -> None:
    assert classify_absence(0) == AbsenceBand.ACTIVE
    assert classify_absence(2) == AbsenceBand.ACTIVE
    assert classify_absence(3) == AbsenceBand.LAPSED
    assert classify_absence(29) == AbsenceBand.LAPSED
    assert classify_absence(30) == AbsenceBand.LONG_ABSENCE
    assert classify_absence(90) == AbsenceBand.LONG_ABSENCE
    assert classify_absence(None) == AbsenceBand.ACTIVE


def test_negative_rejected() -> None:
    with pytest.raises(ValueError):
        classify_absence(-1)


def test_long_return_acknowledges_and_retains() -> None:
    p = plan_return(45, has_open_threads=True, provenance="test")
    assert p.band == AbsenceBand.LONG_ABSENCE
    assert p.retain_identity is True
    assert p.acknowledge_return is True
    assert p.continue_thread is True
    assert "unresolved thread" in p.guidance


def test_lapsed_continues_without_acknowledge() -> None:
    p = plan_return(5, has_open_threads=True, provenance="test")
    assert p.band == AbsenceBand.LAPSED
    assert p.acknowledge_return is False
    assert p.continue_thread is True


def test_active_continues() -> None:
    p = plan_return(1, has_open_threads=False, provenance="test")
    assert p.band == AbsenceBand.ACTIVE
    assert p.retain_identity is True


def test_no_hardcoded_fan_phrases() -> None:
    for days, threads in ((45, True), (5, True), (1, False), (60, False)):
        g = plan_return(days, threads, "test").guidance.lower()
        assert "look who" not in g
        assert "😏" not in g
        assert "missed you" not in g


def test_provenance_required() -> None:
    with pytest.raises(ValueError):
        plan_return(5, True, "")


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "absence_service.py"
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
