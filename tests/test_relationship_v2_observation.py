"""Stage B2 tests: LLM proposals gated, never direct writes.

Pure functions, no providers. Proves: explicit new facts promote, repeats
hold, weak contradictions hold, creator/commerce claims reject, patterns
gate on confidence, malformed items reject without killing the batch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from relationship_v2.domain.memory import MemoryFact, MemoryFactStatus
from relationship_v2.services.llm_observation import (
    MemoryObservation,
    ObservationBatch,
    ObservationDisposition,
    PatternObservation,
    review_batch,
)


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


def _batch(
    memories: list[MemoryObservation] | None = None,
    patterns: list[PatternObservation] | None = None,
) -> ObservationBatch:
    return ObservationBatch(
        generation_id="gen-1",
        creator_id=1,
        user_id=2,
        source_event_id="ev-1",
        provenance="llm-test",
        memories=memories or [],
        patterns=patterns or [],
    )


def _mem(key: str, value: str, conf: float = 0.86, explicit: bool = True) -> MemoryObservation:
    return MemoryObservation(
        category="occupation",
        memory_key=key,
        value=value,
        confidence=conf,
        explicitly_stated=explicit,
    )


def test_explicit_new_fact_promotes_current() -> None:
    rep = review_batch(_batch(memories=[_mem("job", "photographer")]), [])
    assert rep.promoted == 1 and rep.rejected == 0
    assert rep.results[0].disposition == ObservationDisposition.PROMOTE_CURRENT


def test_repeat_confirms_hold_no_row() -> None:
    rep = review_batch(_batch(memories=[_mem("job", "architect")]), [_fact("architect")])
    assert rep.results[0].disposition == ObservationDisposition.HOLD
    assert rep.promoted == 0


def test_weak_contradiction_held() -> None:
    rep = review_batch(
        _batch(memories=[_mem("job", "photographer?", conf=0.55, explicit=False)]),
        [_fact("architect")],
    )
    assert rep.results[0].disposition == ObservationDisposition.HOLD


def test_explicit_contradiction_promotes_with_record() -> None:
    rep = review_batch(_batch(memories=[_mem("job", "photographer")]), [_fact("architect")])
    r = rep.results[0]
    assert r.disposition == ObservationDisposition.PROMOTE_CURRENT
    assert r.conflict is not None
    assert r.conflict.previous_value == "architect"


def test_creator_identity_rejected() -> None:
    obs = MemoryObservation(
        category="creator",
        memory_key="creator_name",
        value="Sunny",
        confidence=0.99,
        explicitly_stated=True,
    )
    rep = review_batch(_batch(memories=[obs]), [])
    assert rep.results[0].disposition == ObservationDisposition.REJECT
    assert rep.rejected == 1


def test_commerce_claim_rejected() -> None:
    obs = MemoryObservation(
        category="commerce",
        memory_key="price",
        value="$20",
        confidence=0.99,
        explicitly_stated=True,
    )
    rep = review_batch(_batch(memories=[obs]), [])
    assert rep.results[0].disposition == ObservationDisposition.REJECT


def test_low_confidence_important_rejected() -> None:
    obs = MemoryObservation(
        category="occupation",
        memory_key="job",
        value="maybe astronaut",
        confidence=0.4,
        importance="high",
    )
    rep = review_batch(_batch(memories=[obs]), [])
    assert rep.results[0].disposition == ObservationDisposition.REJECT


def test_pattern_confidence_gate() -> None:
    weak = PatternObservation(pattern="likes_teasing", description="x", confidence=0.5)
    strong = PatternObservation(pattern="likes_teasing", description="x", confidence=0.73)
    rep = review_batch(_batch(patterns=[weak, strong]), [])
    assert rep.results[0].disposition == ObservationDisposition.REJECT
    assert rep.results[1].disposition == ObservationDisposition.HOLD


def test_batch_scope_fail_closed() -> None:
    with pytest.raises(ValidationError):
        ObservationBatch(
            generation_id="g",
            creator_id=0,
            user_id=2,
            source_event_id="e",
            provenance="p",
        )


def test_no_provider_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "llm_observation.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any("llama" in m or "groq" in m or "openai" in m for m in mods)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert "relationship_v2.persistence.repository" not in mods
