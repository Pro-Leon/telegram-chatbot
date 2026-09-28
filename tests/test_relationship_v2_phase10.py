"""Phase 10 unit tests: shadow observe-only validation.

No DB/Redis/LLM/commerce (pure composition). Covers MIGRATION_AND_CUTOVER.md
shadow rule: never sends, creates offers, or mutates state.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

from relationship_v2.domain.shadow import ShadowInputs
from relationship_v2.services.shadow import compare_with_legacy, observe_turn

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _inputs(**kw) -> ShadowInputs:
    base: dict = {
        "generation_id": "gen-shadow-1",
        "creator_id": 1,
        "user_id": 2,
        "inbound_text": "hey, just got home from work",
        "inbound_text_length": 28,
        "current_relationship_lifecycle": "warming",
        "relationship_version": 2,
        "interaction_count": 8,
        "meaningful_interaction_count": 2,
        "milestone_count": 1,
        "current_escalation_stage": "relationship",
        "responsiveness": 0.7,
        "engagement_evidence": 8,
        "memory_lines": ["job=photographer"],
        "conversation_topic": "work",
    }
    base.update(kw)
    return ShadowInputs(**base)


def test_shadow_observes_without_mutation() -> None:
    now = datetime.now(UTC)
    report = observe_turn(_inputs(), now)
    assert report.generation_id == "gen-shadow-1"
    assert report.assembly_sections >= 1
    assert report.memory_lines_considered == 1
    assert report.observed_at == now


def test_shadow_deterministic() -> None:
    now = datetime.now(UTC)
    assert observe_turn(_inputs(), now) == observe_turn(_inputs(), now)


def test_shadow_scope_fail_closed() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _inputs(creator_id=0)


def test_divergence_notes_without_winner() -> None:
    report = observe_turn(_inputs())
    divs = compare_with_legacy(report, _inputs(legacy_stage="present_offer", legacy_memory_count=9))
    assert any(d.dimension == "stage" for d in divs)
    assert any(d.dimension == "memory_coverage" for d in divs)
    assert all(d.note for d in divs)


def test_no_divergence_when_aligned() -> None:
    report = observe_turn(_inputs())
    divs = compare_with_legacy(
        report,
        _inputs(
            legacy_stage=report.selected_stage,
            legacy_memory_count=report.memory_lines_considered,
        ),
    )
    assert divs == []


def test_phase10_zero_io_no_sends_no_mutations() -> None:
    import ast

    target = REPO_ROOT / "relationship_v2" / "services" / "shadow.py"
    tree = ast.parse(target.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for mod in mods:
            assert mod.split(".")[0] in (
                "relationship_v2",
                "logging",
                "datetime",
                "uuid",
                "__future__",
            ), mod
    text = target.read_text(encoding="utf-8")
    for forbidden in (
        "db.postgres",
        "db.redis",
        "get_pool",
        "get_redis",
        "provider",
        "llamacpp",
        "one_call",
        "xadd",
        "enqueue",
        "send_messages",
        "execute_ppv",
        "create_relationship_event",
        "create_memory_fact",
        "persist_snapshot",
    ):
        assert forbidden not in text, forbidden
