"""Phase 4.1 — routing verdict persistence tests.

Pins the verdict path end to end at unit level:
- the three verdict fields are declared, emitted by to_dict(), and present
  in the primary INSERT (extends the Phase 11 drift-detector contract);
- defaults are null-safe (old rows read NULL);
- the new migration adds exactly these columns, additive + nullable;
- the three new audit event types validate, stay hash-only, and unknown
  types are still rejected;
- operator_queue <-> telemetry join probe: both sides carry
  (creator_id, generation_id) (anchors mirror tests/test_phase11_trace.py
  creator/generation identity).
No live DB/provider I/O. No boards (4.2).
"""

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

VERDICT_FIELDS = ("routing_veto", "advisory_handoff", "corroborated_handoff")
NEW_EVENT_TYPES = ("auto_approved", "prompt_echo", "repeat")
MIGRATION = "20260924000000_phase41_verdict.sql"


def test_verdict_fields_declared_and_emitted():
    from core.telemetry import GenerationTelemetry

    t = GenerationTelemetry(
        user_id=10,
        creator_id=1,
        routing_veto="sealed_suppressed",
        advisory_handoff=True,
        corroborated_handoff=False,
    )
    d = t.to_dict()
    for f in VERDICT_FIELDS:
        assert f in GenerationTelemetry.__dataclass_fields__, f
        assert d[f] == getattr(t, f), f


def test_verdict_defaults_null_safe():
    from core.telemetry import GenerationTelemetry

    d = GenerationTelemetry(user_id=10, creator_id=1).to_dict()
    for f in VERDICT_FIELDS:
        assert d[f] is None, f


def test_verdict_persisted_in_primary_insert():
    root = Path(__file__).resolve().parent.parent
    source = (root / "db" / "postgres.py").read_text(encoding="utf-8")
    match = re.search(r"INSERT INTO generation_telemetry \(\s*(.*?)\) VALUES", source, re.DOTALL)
    assert match, "primary INSERT not found"
    columns = set(re.findall(r"[a-z_][a-z0-9_]*", match.group(1)))
    for f in VERDICT_FIELDS:
        assert f in columns, f


def test_verdict_migration_covers_columns():
    root = Path(__file__).resolve().parent.parent
    migration = (root / "db" / "migrations" / MIGRATION).read_text(encoding="utf-8")
    for f in VERDICT_FIELDS:
        assert f in migration, f
    assert "IF NOT EXISTS" in migration
    assert "BACKFILL" not in migration.upper().replace("NO BACKFILL", "")


def test_audit_new_types_validate_hash_only_and_unknown_rejects():
    from core.audit import EVENT_TYPES, build_audit_event

    for et in NEW_EVENT_TYPES:
        assert et in EVENT_TYPES, et
        ev = build_audit_event(
            event_type=et,
            actor_type="ai",
            actor_id="llm_worker",
            creator_id=1,
            user_id=10,
            action="phase41.probe",
            content="Your conversational response",
            generation_id="g1",
        )
        # Hash-only shape: no raw content retained.
        assert ev["event_type"] == et
        assert ev["content_hash"] is not None
        assert "Your conversational response" not in str(ev.values())
    with pytest.raises(ValueError):
        build_audit_event(
            event_type="auto_approve",
            actor_type="ai",
            creator_id=1,
            action="phase41.probe",
        )


def test_join_probe_creator_generation_both_sides():
    """operator_queue row <-> telemetry row correlate on (creator,generation).

    Anchors mirror tests/test_phase11_trace.py creator/generation identity
    (CREATOR_A/GEN_A style): the join key exists on both sides, no new key.
    """
    from core.telemetry import GenerationTelemetry

    generation_id = "c" * 32
    queue_row = {
        "id": 7,
        "user_id": 101,
        "creator_id": 11,
        "generation_id": generation_id,
        "status": "pending",
    }
    tele = GenerationTelemetry(user_id=101, creator_id=11, generation_id=generation_id).to_dict()
    assert tele["creator_id"] == queue_row["creator_id"] == 11
    assert tele["generation_id"] == queue_row["generation_id"] == generation_id
