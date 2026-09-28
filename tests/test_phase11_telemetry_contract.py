"""Phase 11 Slice C — telemetry contract test (drift detector).

Pins the contract between GenerationTelemetry declared fields, to_dict()
emission, primary INSERT persistence, and migration columns. Any declared
field that is emitted but silently absent from persistence must appear in
INTENTIONALLY_TRANSIENT with a documented reason, otherwise this test
fails. The goal is no accidental silent telemetry loss.
"""

import dataclasses
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

# Declared + emitted but deliberately not persisted. Each entry documents
# why persistence is unnecessary (trace sources the same fact elsewhere,
# or the field is a duplicate alias / privacy-sensitive raw value).
INTENTIONALLY_TRANSIENT = frozenset(
    {
        # Backward-compat duplicate alias of context_engine_failed.
        "context_engine_failed_flag",
        # Raw operator name: privacy — the hash (player_name_hash,
        # persisted) suffices for correlation.
        "player_name",
        # Commerce authority lives in ledger snapshots + CommerceDecision
        # metadata; per-turn commercial floats are advisory fragments.
        "commercial_objective",
        "commerce_action",
        "sales_pressure",
        "product_selected",
        "offer_presented",
        "tip_presented",
        "objection_type",
        "purchase_state",
        # Phase 17 intelligence fragments: descriptive turn context whose
        # durable counterpart lives in trajectory anchors / snapshots.
        "desire_stage",
        "temperature",
        "sales_window",
        "offer_readiness",
        "next_best_action",
        "response_mode",
        "question_policy",
        "memory_retrieved_count",
        "memory_written_count",
        "open_loop_count",
        "commitment_count",
        # Phase 20 adaptive fragments superseded by Phase 10/11 bounded
        # namespaces (strategy_selected/source/mode persisted; experiment
        # sourced from exposure records; outcome split into
        # relationship/commerce namespaces).
        "strategy_confidence",
        "strategy_evidence_count",
        "strategy_exploration",
        "outcome",
        "outcome_strength",
        "attribution_type",
        "fatigue_score",
        "experiment_id",
        "experiment_variant",
        # Phase 21 operation fragments beyond the persisted gate subset
        # (operation_allowed/block_reason/handoff_required/decision_trace
        # persisted; pressure/risk/lifecycle derivable per turn).
        "pressure_score",
        "risk_state",
        "failure_class",
        "lifecycle_state",
        # Phase 25 funnel floats: diagnostic-only, derivable aggregates.
        "funnel_state",
        "funnel_transition",
        "relationship_health",
        "commercial_intent",
        "conversion_window",
        "baseline_state",
        "optimization_state",
        # Persona fidelity fragments: bounded diagnostics, not trace inputs.
        "persona_version",
        "emotional_state",
        "behavior_confidence",
        "conversation_mode",
        "persona_voice_valid",
        "persona_voice_severe",
        "voice_score",
        "naturalness_score",
        "persona_question_compliance",
        "persona_fact_violation",
        # Persona/context-size diagnostics (persona_id persisted).
        "persona_context_chars",
        "generation_context_chars",
        "generation_context_tokens_estimate",
        "persona_context_compact_chars",
        # Phase 9 commerce-context booleans: bounded flags duplicating the
        # persisted authorization-basis string (trace sources the basis).
        "commerce_user_initiated",
        "commerce_warmth_without_evidence",
        # Local-intelligence timing diagnostics.
        "unified_intelligence_ms",
        "rapidfuzz_ms",
        "embedding_ms",
        "similarity_ms",
        "unified_intelligence_confidence",
        "unified_intelligence_abstained",
        # Context-engine timing/candidate diagnostics (degraded flag and
        # retrieval latency/counts persisted).
        "context_engine_enabled",
        "context_engine_ms",
        "context_engine_gather_ms",
        "context_engine_score_ms",
        "context_engine_dedup_ms",
        "context_engine_budget_ms",
        "context_engine_render_ms",
        "context_engine_candidates",
        "context_engine_selected",
        "context_engine_dropped",
        "context_engine_tokens",
        "context_engine_chars",
        "context_engine_failed",
    }
)

# Phase 11 trace columns: declared + emitted + persisted (must stay).
PHASE11_COLUMNS = frozenset(
    {
        "strategy_selected",
        "strategy_source",
        "strategy_mode",
        "conversation_objective",
        "objective_reason",
        "decision_trace",
        "operation_allowed",
        "operation_block_reason",
        "handoff_required",
        "commerce_context",
        "commerce_authorization_basis",
    }
)


def _to_dict_keys() -> set[str]:
    from core.telemetry import GenerationTelemetry

    return set(GenerationTelemetry(user_id=1).to_dict())


def _declared_fields() -> set[str]:
    from core.telemetry import GenerationTelemetry

    return set(GenerationTelemetry.__dataclass_fields__)


def _primary_insert_columns() -> list[str]:
    root = Path(__file__).resolve().parent.parent
    source = (root / "db" / "postgres.py").read_text(encoding="utf-8")
    match = re.search(r"INSERT INTO generation_telemetry \(\s*(.*?)\) VALUES", source, re.S)
    assert match, "primary INSERT not found"
    return re.findall(r"[a-z_][a-z0-9_]*", match.group(1))


def test_to_dict_emits_only_declared_fields():
    """No undeclared worker attribute may leak through to_dict().

    The single documented exception is the backward-compat duplicate
    alias ``context_engine_failed_flag`` (explicitly allowlisted).
    """
    extra = _to_dict_keys() - _declared_fields()
    assert extra <= INTENTIONALLY_TRANSIENT, sorted(extra)


def test_no_silent_telemetry_loss():
    """Every emitted field is persisted or explicitly allowlisted."""
    emitted = _to_dict_keys()
    persisted = set(_primary_insert_columns())
    silent = emitted - persisted - INTENTIONALLY_TRANSIENT
    assert not silent, f"silently dropped telemetry fields: {sorted(silent)}"


def test_transient_allowlist_is_accurate():
    """Allowlisted fields must still exist (no stale entries hiding drift)."""
    emitted = _to_dict_keys()
    stale = INTENTIONALLY_TRANSIENT - emitted
    assert not stale, f"stale allowlist entries: {sorted(stale)}"


def test_phase11_columns_persisted():
    """Phase 11 trace fragments are declared, emitted, and persisted."""
    emitted = _to_dict_keys()
    persisted = set(_primary_insert_columns())
    declared = _declared_fields()
    for column in PHASE11_COLUMNS:
        assert column in declared, column
        assert column in emitted, column
        assert column in persisted, column


def test_phase11_migration_covers_columns():
    """Migration adds every Phase 11 column (additive, nullable)."""
    root = Path(__file__).resolve().parent.parent
    migration = (root / "db" / "migrations" / "20260923000000_phase11_telemetry.sql").read_text(
        encoding="utf-8"
    )
    for column in PHASE11_COLUMNS:
        assert column in migration, column
    assert "IF NOT EXISTS" in migration


def test_primary_placeholders_match_columns():
    """Placeholder count matches the primary column count."""
    root = Path(__file__).resolve().parent.parent
    source = (root / "db" / "postgres.py").read_text(encoding="utf-8")
    match = re.search(
        r"INSERT INTO generation_telemetry \(\s*(.*?)\) VALUES \(\s*(.*?)\)\s*ON CONFLICT",
        source,
        re.S,
    )
    assert match, "primary INSERT block not found"
    columns = re.findall(r"[a-z_][a-z0-9_]*", match.group(1))
    placeholders = re.findall(r"\$\d+", match.group(2))
    assert len(columns) == len(placeholders), (len(columns), len(placeholders))
    assert len(columns) >= 113, len(columns)
