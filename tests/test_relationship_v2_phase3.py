"""Phase 3 unit tests: extraction / validation / retrieval.

No external infra required. Covers 05_MEMORY_SPEC quality gates 1-10
(applicable deterministic subset) + MEMORY_ARCHITECTURE lifecycle.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from relationship_v2.domain.memory import (
    MemoryEpisode,
    MemoryEpisodeType,
    MemoryFact,
    MemoryFactStatus,
    MemoryImportance,
)
from relationship_v2.persistence.owners import TABLE_OWNERS
from relationship_v2.services.memory_extraction import (
    CandidateKind,
    MemoryCandidate,
    extract_candidates,
)
from relationship_v2.services.memory_retrieval import (
    RetrievalBudget,
    retrieve_tiered,
    score_fact,
)
from relationship_v2.services.memory_validation import (
    ValidationCode,
    is_valid_memory_status_transition,
    validate_fact_candidate,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = REPO_ROOT / "relationship_v2" / "persistence" / "schema.sql"
MIGRATION_003 = (
    REPO_ROOT / "relationship_v2" / "persistence" / "migrations" / "003_phase3_signals.sql"
)


def _now():
    return datetime.now(UTC)


def _fact(key="job", value="photographer", status=MemoryFactStatus.CURRENT):
    now = _now()
    return MemoryFact(
        id=uuid4(),
        creator_id=1,
        user_id=2,
        relationship_id=uuid4(),
        category="occupation",
        memory_key=key,
        value=value,
        status=status,
        importance=MemoryImportance.HIGH,
        confidence=0.9,
        effective_from=now - timedelta(days=3),
        provenance="test",
        created_at=now,
        updated_at=now,
    )


def test_explicit_fact_extracted_with_confidence() -> None:
    cands = extract_candidates("I work as a photographer", "evt1", "test")
    facts = [c for c in cands if c.kind == CandidateKind.FACT]
    assert facts, "explicit occupation must extract"
    assert facts[0].confidence >= 0.6
    assert facts[0].explicitly_stated is True


def test_changed_fact_supersedes_history_preserved() -> None:
    old = _fact(value="architect")
    cand = MemoryCandidate(
        kind=CandidateKind.FACT,
        category="occupation",
        memory_key="job",
        value="photographer",
        confidence=0.9,
        importance="high",
        explicitly_stated=True,
        extractor_version="v",
        source_event_id="e2",
        provenance="test",
    )
    verdict = validate_fact_candidate(cand, [old])
    assert verdict.code == ValidationCode.SUPERSEDE_CONTRADICTION
    assert verdict.promote is True
    assert verdict.supersedes_id == str(old.id)


def test_repeated_fact_confirms_without_new_row() -> None:
    old = _fact(value="photographer")
    cand = MemoryCandidate(
        kind=CandidateKind.FACT,
        category="occupation",
        memory_key="job",
        value="Photographer ",
        confidence=0.9,
        importance="high",
        explicitly_stated=True,
        extractor_version="v",
        source_event_id="e3",
        provenance="test",
    )
    assert validate_fact_candidate(cand, [old]).code == ValidationCode.CONFIRM_EXISTING


def test_weak_inference_never_promoted_to_important() -> None:
    cand = MemoryCandidate(
        kind=CandidateKind.FACT,
        category="occupation",
        memory_key="job",
        value="maybe architect",
        confidence=0.4,
        importance="high",
        explicitly_stated=False,
        extractor_version="v",
        source_event_id="e4",
        provenance="test",
    )
    v = validate_fact_candidate(cand, [])
    assert v.code == ValidationCode.REJECT_LOW_CONFIDENCE
    assert v.promote is False


def test_creator_identity_and_commerce_rejected() -> None:
    for key, code in (
        ("creator_name", ValidationCode.REJECT_CREATOR_IDENTITY),
        ("price", ValidationCode.REJECT_COMMERCE_TRUTH),
    ):
        cand = MemoryCandidate(
            kind=CandidateKind.FACT,
            category="x",
            memory_key=key,
            value="y",
            confidence=0.95,
            explicitly_stated=True,
            extractor_version="v",
            source_event_id="e5",
            provenance="test",
        )
        assert validate_fact_candidate(cand, []).code == code


def test_intimate_content_stored_abstract_not_verbatim() -> None:
    cands = extract_candidates("I'm exhausted but kinda horny tonight", "e6", "test")
    for c in cands:
        if c.kind == CandidateKind.SIGNAL and c.category == "intimate_signal":
            assert "horny" not in c.value.lower()
            return
    # Acceptable: no intimate signal emitted either; never verbatim fact.
    assert all("horny" not in c.value.lower() or c.kind == CandidateKind.EPISODE for c in cands)


def test_memory_lifecycle_guards() -> None:
    assert is_valid_memory_status_transition(MemoryFactStatus.CANDIDATE, MemoryFactStatus.VALIDATED)
    # Invalid: candidate -> superseded skips validation.
    assert not is_valid_memory_status_transition(
        MemoryFactStatus.CANDIDATE, MemoryFactStatus.SUPERSEDED
    )


def test_retrieval_prefers_current_and_relevant() -> None:
    now = _now()
    current = _fact(value="photographer")
    old = MemoryFact(
        id=uuid4(),
        creator_id=1,
        user_id=2,
        relationship_id=uuid4(),
        category="occupation",
        memory_key="job_old",
        value="architect",
        status=MemoryFactStatus.SUPERSEDED,
        importance=MemoryImportance.NORMAL,
        confidence=0.7,
        effective_from=now - timedelta(days=100),
        effective_to=now - timedelta(days=90),
        provenance="test",
        created_at=now,
        updated_at=now,
    )
    assert score_fact(current, "what is your job photographer") > score_fact(
        old, "what is your job photographer"
    )


def test_proactive_callback_without_topic_overlap() -> None:
    now = _now()
    important_old = MemoryFact(
        id=uuid4(),
        creator_id=1,
        user_id=2,
        relationship_id=uuid4(),
        category="life_event",
        memory_key="daughter_game",
        value="daughter soccer game Saturday",
        status=MemoryFactStatus.CURRENT,
        importance=MemoryImportance.HIGH,
        confidence=0.9,
        effective_from=now - timedelta(days=14),
        provenance="test",
        created_at=now,
        updated_at=now,
    )
    episode = MemoryEpisode(
        id=uuid4(),
        creator_id=1,
        user_id=2,
        relationship_id=uuid4(),
        episode_type=MemoryEpisodeType.IMPORTANT_EVENT,
        summary="recent small talk",
        salience=0.2,
        provenance="test",
        created_at=now,
    )
    res = retrieve_tiered(
        [important_old],
        [episode],
        "hey",
        RetrievalBudget(tier1_max=1, tier2_max=1, tier3_max=3),
        now,
    )
    selected = set(res.tier1_ids + res.tier2_ids + res.tier3_ids)
    assert str(important_old.id) in selected


def test_retrieval_budget_bounded() -> None:
    facts = [_fact(key=f"k{i}", value=f"v{i}") for i in range(20)]
    res = retrieve_tiered(facts, [], "job", RetrievalBudget(tier1_max=2, tier2_max=2, tier3_max=1))
    assert len(res.tier1_ids) <= 2
    assert len(res.tier2_ids) <= 2
    assert len(res.tier3_ids) <= 1
    assert res.dropped >= 0


def test_signals_table_owned_and_scoped() -> None:
    assert (
        TABLE_OWNERS["v2_engagement_signals"]
        == "relationship_v2.persistence.repository:record_engagement_signal"
    )
    sql = SCHEMA.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS v2_engagement_signals" in sql
    assert "uq_v2_signals_topic" in sql
    assert MIGRATION_003.exists()


def test_phase3_no_v1_or_commerce_leakage() -> None:
    import ast

    for sub in ("services", "domain"):
        pkg = REPO_ROOT / "relationship_v2" / sub
        for f in pkg.rglob("*.py"):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                for mod in mods:
                    if mod.startswith("relationship_v2") or mod in (
                        "db.postgres",
                        "db",
                    ):
                        continue
                    assert not (mod == "commerce" or mod.startswith("commerce.")), f
                    assert "context_engine" not in mod, f
                    assert "integrations.dropfans" not in mod, f
                    assert "integrations.fangate" not in mod, f
