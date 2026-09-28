"""Stage B1 tests: contradiction classification + explicit records.

Pure functions, no infra. Proves the spec cases: architect -> photographer
supersedes with history, repeats confirm, weak inferences hold, corrections
supersede with reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from relationship_v2.domain.memory import MemoryFact, MemoryFactStatus
from relationship_v2.services.contradiction_resolver import (
    ConflictKind,
    ConflictResolution,
    resolve_conflict,
)
from relationship_v2.services.memory_extraction import MemoryCandidate


def _fact(value: str, key: str = "job") -> MemoryFact:
    now = datetime.now(UTC)
    return MemoryFact(
        id=uuid4(),
        creator_id=1,
        user_id=2,
        relationship_id=uuid4(),
        category="occupation",
        memory_key=key,
        value=value,
        status=MemoryFactStatus.CURRENT,
        confidence=0.9,
        effective_from=now,
        provenance="test",
        created_at=now,
        updated_at=now,
    )


def _candidate(value: str, key: str = "job", explicit: bool = True) -> MemoryCandidate:
    return MemoryCandidate(
        kind="fact",
        category="occupation",
        memory_key=key,
        value=value,
        confidence=0.85 if explicit else 0.55,
        explicitly_stated=explicit,
        extractor_version="test",
        source_event_id="ev1",
        provenance="test",
    )


def test_no_conflict_new_key() -> None:
    r = resolve_conflict(_candidate("photographer"), [])
    assert r.kind == ConflictKind.NO_CONFLICT
    assert r.resolution == ConflictResolution.ACCEPT
    assert r.previous_value is None


def test_reinforcement_same_value() -> None:
    r = resolve_conflict(_candidate("architect"), [_fact("architect")])
    assert r.kind == ConflictKind.REINFORCEMENT
    assert r.resolution == ConflictResolution.CONFIRM
    assert r.supersedes_id is None


def test_direct_contradiction_supersedes() -> None:
    old = _fact("architect")
    r = resolve_conflict(_candidate("photographer"), [old])
    assert r.kind == ConflictKind.DIRECT_CONTRADICTION
    assert r.resolution == ConflictResolution.SUPERSEDE
    assert r.supersedes_id == str(old.id)
    assert r.previous_value == "architect"


def test_weak_inference_holds_current() -> None:
    old = _fact("architect")
    r = resolve_conflict(_candidate("photographer?", explicit=False), [old])
    assert r.kind == ConflictKind.DIRECT_CONTRADICTION
    assert r.resolution == ConflictResolution.HOLD
    assert r.previous_value == "architect"


def test_correction_supersedes_with_reason() -> None:
    old = _fact("photographer")
    r = resolve_conflict(_candidate("architect"), [old], is_correction=True)
    assert r.kind == ConflictKind.CORRECTION
    assert r.resolution == ConflictResolution.SUPERSEDE
    assert r.is_correction is True
    assert "correction" in r.reason


def test_case_insensitive_match_is_reinforcement() -> None:
    r = resolve_conflict(_candidate("  Architect "), [_fact("architect")])
    assert r.kind == ConflictKind.REINFORCEMENT


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "contradiction_resolver.py"
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
