"""Telemetry module for AI-native canary phase 2.

Structured telemetry for both legacy and agent paths.
Observational only — never mutates commerce state.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("telemetry")


@dataclass
class GenerationTelemetry:
    """Structured telemetry for a single generation."""

    generation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: int = 0
    creator_id: int | None = None
    runtime_mode: str = "legacy"  # 'legacy' or 'agent'
    provider_name: str = ""
    model_name: str = ""
    context_build_ms: int = 0
    generation_latency_ms: int = 0
    scoring_latency_ms: int = 0
    scoring_score: float = 0.0
    scoring_flags: list[str] = field(default_factory=list)
    tool_calls_count: int = 0
    tool_names: list[str] = field(default_factory=list)
    total_e2e_latency_ms: int = 0
    routing_decision: str = ""  # 'auto_approved', 'operator_queued', 'commerce_response'
    success: bool = True
    failure_type: str | None = None
    worker_id: str | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    # Provider-specific timing
    provider_request_start: float | None = None
    provider_request_end: float | None = None
    provider_latency_ms: int = 0
    provider_error: str | None = None

    # Token tracking (when available)
    input_token_count: int | None = None
    output_token_count: int | None = None

    # Sales intelligence (P1 telemetry extension — compact, no raw content)
    commercial_objective: str | None = None
    commerce_action: str | None = None
    sales_pressure: str | None = None
    product_selected: int | None = None
    offer_presented: bool = False
    tip_presented: bool = False
    objection_type: str | None = None
    purchase_state: str | None = None

    # Phase 17: Conversation intelligence + memory
    desire_stage: str | None = None
    temperature: str | None = None
    sales_window: str | None = None
    offer_readiness: str | None = None
    next_best_action: str | None = None
    conversation_objective: str | None = None
    objective_reason: str | None = None
    response_mode: str | None = None
    question_policy: str | None = None
    memory_retrieved_count: int = 0
    memory_written_count: int = 0
    open_loop_count: int = 0
    commitment_count: int = 0

    # Phase 20: Adaptive optimization (compact, no PII, deterministic)
    strategy_selected: str | None = None
    strategy_source: str | None = None
    strategy_mode: str | None = None  # explore/exploit/safe_default
    strategy_confidence: float | None = None
    strategy_evidence_count: int | None = None
    strategy_exploration: bool = False
    outcome: str | None = None
    outcome_strength: float | None = None
    experiment_id: str | None = None
    experiment_variant: str | None = None  # CONTROL / EXPERIMENT
    attribution_type: str | None = None  # direct / assisted / organic / unknown
    fatigue_score: float | None = None

    # Phase 21: Enterprise operations (compact, deterministic, generation-scoped, no secrets)
    operation_allowed: bool | None = None
    operation_block_reason: str | None = None
    pressure_score: float | None = None
    risk_state: str | None = None
    handoff_required: bool | None = None
    failure_class: str | None = None
    decision_trace: str | None = None
    lifecycle_state: str | None = None

    # Phase 25: Revenue/Relationship/Funnel (compact, PII-free, bounded)
    funnel_state: str | None = None
    funnel_transition: str | None = None
    relationship_health: float | None = None
    commercial_intent: float | None = None
    conversion_window: str | None = None
    # attribution_type already exists above, reuse
    baseline_state: str | None = None
    optimization_state: str | None = None

    # Phase 43D: Persona Behavioral Fidelity (compact, no PII, deterministic)
    persona_version: int | None = None
    emotional_state: str | None = None
    behavior_confidence: str | None = None
    conversation_mode: str | None = None
    persona_voice_valid: bool | None = None
    persona_voice_severe: bool | None = None
    voice_score: float | None = None
    naturalness_score: float | None = None
    persona_question_compliance: bool | None = None
    persona_fact_violation: bool | None = None

    # Phase 89: Persona Studio + conversational grounding (compact, no raw content)
    persona_id: int | None = None
    participant_grounding_enabled: bool | None = None
    conversation_contract_present: bool | None = None
    speaker_correct: bool | None = None
    question_answered: bool | None = None
    topic_continuous: bool | None = None
    conversational_validation_flags: list[str] = field(default_factory=list)
    # Phase 89R: hardened roleplay telemetry (additive, no PII break)
    roleplay_enabled: bool | None = None
    character_name: str | None = None
    # player_name omitted from telemetry by default for privacy; store only if non-sensitive or hashed
    # we keep field but llm_worker will populate with anonymized identifier if privacy requires
    player_name: str | None = None
    player_name_hash: str | None = None
    roleplay_contract_present: bool | None = None
    character_correct: bool | None = None
    player_agency_preserved: bool | None = None
    out_of_character: bool | None = None
    roleplay_validation_flags: list[str] = field(default_factory=list)

    # Phase 44C: Context size telemetry (character-based, token estimate via chars/4)
    persona_context_chars: int | None = None
    generation_context_chars: int | None = None
    generation_context_tokens_estimate: int | None = None
    persona_context_compact_chars: int | None = None

    # Phase 50: Local intelligence telemetry (per-message, no PII)
    unified_intelligence_ms: float | None = None
    rapidfuzz_ms: float | None = None
    embedding_ms: float | None = None
    similarity_ms: float | None = None
    unified_intelligence_confidence: float | None = None
    unified_intelligence_abstained: bool | None = None

    # Phase 73: Context Engine observational telemetry (per-message, no PII)
    context_engine_enabled: bool = False
    context_engine_ms: float | None = None
    context_engine_gather_ms: float | None = None
    context_engine_score_ms: float | None = None
    context_engine_dedup_ms: float | None = None
    context_engine_budget_ms: float | None = None
    context_engine_render_ms: float | None = None
    context_engine_candidates: int | None = None
    context_engine_selected: int | None = None
    context_engine_dropped: int | None = None
    context_engine_tokens: int | None = None
    context_engine_chars: int | None = None
    context_engine_failed: bool = False

    # Phase 87: Canonical generation counters (creator-safe, per-turn)
    one_call_count: int = 0
    total_llm_calls: int = 0
    ppv_second_generation_count: int = 0
    legacy_generation_count: int = 0
    shadow_generation_count: int = 0
    agent_canary_generation_count: int = 0

    # Phase 87: Context Engine detailed observability (no raw content)
    retrieval_latency_ms: int | None = None
    lexical_candidate_count: int | None = None
    semantic_candidate_count: int | None = None
    merged_candidate_count: int | None = None
    embedding_latency_ms: int | None = None
    lexical_latency_ms: int | None = None
    ranking_latency_ms: int | None = None
    conflict_dropped_count: int | None = None
    lexical_dedup_removed_count: int | None = None
    total_deduplication_count: int | None = None
    context_tokens: int | None = None
    context_category_tokens: dict | None = None  # JSON-serializable per-category token map
    context_degradation_level: int | None = None
    context_truncation_count: int | None = None
    context_budget_limit: int | None = None  # 2600
    context_header_reserve: int | None = None  # 60
    context_effective_budget: int | None = None  # 2540
    context_budget_violation_count: int | None = None

    # Phase 87: PPV second-generation provider identity
    ppv_provider_name: str | None = None
    ppv_model_name: str | None = None
    ppv_input_tokens: int | None = None
    ppv_output_tokens: int | None = None
    ppv_latency_ms: int | None = None

    # Phase 87: Outcome taxonomy
    validation_outcome: str | None = (
        None  # provider_exception | malformed_json | pydantic_failure | safety_failure | quality_failure | success ...
    )
    authority_decision: str | None = None  # NO_COMMERCE | DENIED | OFFER_PPV | EXECUTED etc
    commerce_status: str | None = None  # executed | already_executed | denied | no_product etc
    handoff_reason: str | None = None
    delivery_status: str | None = None  # sent | handoff | failed | duplicate_suppressed

    # Phase 4.1: routing verdict persistence (observational, already-computed).
    # Assigned per turn by workers/llm_worker (routing_veto = routing reason,
    # advisory/corroborated handoff flags); NULL means "not recorded".
    routing_veto: str | None = None
    advisory_handoff: bool | None = None
    corroborated_handoff: bool | None = None

    # Phase 87: Duplicate / Redis observability counters (per-turn increment)
    duplicate_send_suppressed_count: int = 0
    already_executed_count: int = 0
    inbound_redelivery_count: int = 0
    # Legacy thresholds for retrieval observability (constants, not per-turn content)
    lexical_threshold: int | None = None  # 80
    semantic_threshold: float | None = None  # 0.30
    retrieval_degraded: bool | None = None

    # Phase 101: warming/readiness (deterministic, observational, already-computed)
    warming_level: str | None = None
    warming_score: float | None = None
    warming_available: bool | None = None
    warming_ceiling: float | None = None
    readiness_level: str | None = None
    readiness_available: bool | None = None
    # Phase 102: product/menu (authoritative, observational)
    menu_items: int | None = None
    menu_context_chars: int | None = None
    # Phase 103: free-photo (observational, reservation correlation)
    free_photo_outcome: str | None = None
    free_photo_vault_item: str | None = None
    free_photo_llm_flag: bool | None = None
    free_photo_delivery: str | None = None
    free_photo_telegram_id: int | None = None
    free_photo_reservation_id: int | None = None
    # Phase 9: commerce context vs authority (bounded, categorical, no raw text).
    # `commerce_context` names the contextual evidence label (reason code);
    # `commerce_authorization_basis` names the deterministic authorization basis
    # (NONE when no deterministic corroboration). Telemetry only, never an
    # authority input.
    commerce_context: str | None = None
    commerce_authorization_basis: str | None = None
    commerce_user_initiated: bool | None = None
    commerce_warmth_without_evidence: bool | None = None
    # Phase 10: Learning & Optimization attribution (bounded categorical, no PII).
    # config_version = deterministic version that produced the decision
    # (LEGACY value "unversioned-legacy" for pre-Phase-10 rows, "unknown"
    # when lookup fails). strategy_version is distinct from experiment ID,
    # variant ID, config version, and ranking policy version. Relationship
    # vs commerce outcomes are separate namespaces (never a single reward).
    config_version: str | None = None
    strategy_version: str | None = None
    ranking_policy_version: str | None = None
    relationship_outcome: str | None = None
    commerce_outcome: str | None = None
    attribution_status: str | None = None
    maturity_policy_version: str | None = None
    maturity_state: str | None = None
    generation_creator_scope: int | None = None
    calibration_available: bool | None = None

    # Pass 0: OneCall stabilization baseline instrumentation (fail-open).
    # Every field defaults to None/0 so workers can assign directly without
    # try/catch; unset means "not measured on this turn", never zero-data.
    authoritative_assembly_ms: int | None = None
    compaction_ms: int | None = None
    serialization_ms: int | None = None
    provider_ms: int | None = None
    prompt_ms: int | None = None
    predicted_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    validation_ms: int | None = None
    routing_ms: int | None = None
    total_ms: int | None = None
    semantic_invoked: bool | None = None
    semantic_required: bool | None = None
    semantic_hits: int | None = None
    ce_tokens: int | None = None
    retrieval_metrics: dict | None = None  # JSON-serializable retrieval side-channel copy

    def complete(self, success: bool = True, failure_type: str | None = None) -> None:
        """Mark telemetry as complete."""
        self.completed_at = time.time()
        self.success = success
        self.failure_type = failure_type
        self.total_e2e_latency_ms = int((self.completed_at - self.started_at) * 1000)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "generation_id": self.generation_id,
            "user_id": self.user_id,
            "creator_id": self.creator_id,
            "runtime_mode": self.runtime_mode,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "context_build_ms": self.context_build_ms,
            "generation_latency_ms": self.generation_latency_ms,
            "scoring_latency_ms": self.scoring_latency_ms,
            "scoring_score": self.scoring_score,
            "scoring_flags": self.scoring_flags,
            "tool_calls_count": self.tool_calls_count,
            "tool_names": self.tool_names,
            "total_e2e_latency_ms": self.total_e2e_latency_ms,
            "routing_decision": self.routing_decision,
            "success": self.success,
            "failure_type": self.failure_type,
            "worker_id": self.worker_id,
            "provider_latency_ms": self.provider_latency_ms,
            "provider_error": self.provider_error,
            "input_token_count": self.input_token_count,
            "output_token_count": self.output_token_count,
            # Phase 87 extended — new schema (persisted via additive migration)
            "one_call_count": self.one_call_count,
            "total_llm_calls": self.total_llm_calls,
            "ppv_second_generation_count": self.ppv_second_generation_count,
            "legacy_generation_count": self.legacy_generation_count,
            "shadow_generation_count": self.shadow_generation_count,
            "agent_canary_generation_count": self.agent_canary_generation_count,
            "retrieval_latency_ms": self.retrieval_latency_ms,
            "lexical_candidate_count": self.lexical_candidate_count,
            "semantic_candidate_count": self.semantic_candidate_count,
            "merged_candidate_count": self.merged_candidate_count,
            "embedding_latency_ms": self.embedding_latency_ms,
            "lexical_latency_ms": self.lexical_latency_ms,
            "ranking_latency_ms": self.ranking_latency_ms,
            "conflict_dropped_count": self.conflict_dropped_count,
            "lexical_dedup_removed_count": self.lexical_dedup_removed_count,
            "total_deduplication_count": self.total_deduplication_count,
            "context_tokens": self.context_tokens,
            "context_category_tokens": self.context_category_tokens,
            "context_degradation_level": self.context_degradation_level,
            "context_truncation_count": self.context_truncation_count,
            "context_budget_limit": self.context_budget_limit,
            "context_header_reserve": self.context_header_reserve,
            "context_effective_budget": self.context_effective_budget,
            "context_budget_violation_count": self.context_budget_violation_count,
            "ppv_provider_name": self.ppv_provider_name,
            "ppv_model_name": self.ppv_model_name,
            "ppv_input_tokens": self.ppv_input_tokens,
            "ppv_output_tokens": self.ppv_output_tokens,
            "ppv_latency_ms": self.ppv_latency_ms,
            "validation_outcome": self.validation_outcome,
            "authority_decision": self.authority_decision,
            "commerce_status": self.commerce_status,
            "handoff_reason": self.handoff_reason,
            "delivery_status": self.delivery_status,
            # Phase 4.1: routing verdict (None when not recorded on this turn)
            "routing_veto": self.routing_veto,
            "advisory_handoff": self.advisory_handoff,
            "corroborated_handoff": self.corroborated_handoff,
            "duplicate_send_suppressed_count": self.duplicate_send_suppressed_count,
            "already_executed_count": self.already_executed_count,
            "inbound_redelivery_count": self.inbound_redelivery_count,
            "lexical_threshold": self.lexical_threshold,
            "semantic_threshold": self.semantic_threshold,
            "retrieval_degraded": self.retrieval_degraded,
            "commercial_objective": self.commercial_objective,
            "commerce_action": self.commerce_action,
            "sales_pressure": self.sales_pressure,
            "product_selected": self.product_selected,
            "offer_presented": self.offer_presented,
            "tip_presented": self.tip_presented,
            "objection_type": self.objection_type,
            "purchase_state": self.purchase_state,
            "desire_stage": self.desire_stage,
            "temperature": self.temperature,
            "sales_window": self.sales_window,
            "offer_readiness": self.offer_readiness,
            "next_best_action": self.next_best_action,
            "conversation_objective": self.conversation_objective,
            "objective_reason": self.objective_reason,
            "response_mode": self.response_mode,
            "question_policy": self.question_policy,
            "memory_retrieved_count": self.memory_retrieved_count,
            "memory_written_count": self.memory_written_count,
            "open_loop_count": self.open_loop_count,
            "commitment_count": self.commitment_count,
            "strategy_selected": self.strategy_selected,
            "strategy_source": self.strategy_source,
            "strategy_mode": self.strategy_mode,
            "strategy_confidence": self.strategy_confidence,
            "strategy_evidence_count": self.strategy_evidence_count,
            "strategy_exploration": self.strategy_exploration,
            "outcome": self.outcome,
            "outcome_strength": self.outcome_strength,
            "experiment_id": self.experiment_id,
            "experiment_variant": self.experiment_variant,
            "attribution_type": self.attribution_type,
            "fatigue_score": self.fatigue_score,
            "operation_allowed": self.operation_allowed,
            "operation_block_reason": self.operation_block_reason,
            "pressure_score": self.pressure_score,
            "risk_state": self.risk_state,
            "handoff_required": self.handoff_required,
            "failure_class": self.failure_class,
            "decision_trace": self.decision_trace,
            "lifecycle_state": self.lifecycle_state,
            "funnel_state": self.funnel_state,
            "funnel_transition": self.funnel_transition,
            "relationship_health": self.relationship_health,
            "commercial_intent": self.commercial_intent,
            "conversion_window": self.conversion_window,
            "baseline_state": self.baseline_state,
            "optimization_state": self.optimization_state,
            "persona_version": self.persona_version,
            "emotional_state": self.emotional_state,
            "behavior_confidence": self.behavior_confidence,
            "conversation_mode": self.conversation_mode,
            "persona_voice_valid": self.persona_voice_valid,
            "persona_voice_severe": self.persona_voice_severe,
            "voice_score": self.voice_score,
            "naturalness_score": self.naturalness_score,
            "persona_question_compliance": self.persona_question_compliance,
            "persona_fact_violation": self.persona_fact_violation,
            "persona_id": self.persona_id,
            "participant_grounding_enabled": self.participant_grounding_enabled,
            "conversation_contract_present": self.conversation_contract_present,
            "speaker_correct": self.speaker_correct,
            "question_answered": self.question_answered,
            "topic_continuous": self.topic_continuous,
            "conversational_validation_flags": self.conversational_validation_flags,
            "roleplay_enabled": self.roleplay_enabled,
            "character_name": self.character_name,
            "player_name": self.player_name,
            "player_name_hash": self.player_name_hash,
            "roleplay_contract_present": self.roleplay_contract_present,
            "character_correct": self.character_correct,
            "player_agency_preserved": self.player_agency_preserved,
            "out_of_character": self.out_of_character,
            "roleplay_validation_flags": self.roleplay_validation_flags,
            "persona_context_chars": self.persona_context_chars,
            "generation_context_chars": self.generation_context_chars,
            "generation_context_tokens_estimate": self.generation_context_tokens_estimate,
            "persona_context_compact_chars": self.persona_context_compact_chars,
            "unified_intelligence_ms": self.unified_intelligence_ms,
            "rapidfuzz_ms": self.rapidfuzz_ms,
            "embedding_ms": self.embedding_ms,
            "similarity_ms": self.similarity_ms,
            "unified_intelligence_confidence": self.unified_intelligence_confidence,
            "unified_intelligence_abstained": self.unified_intelligence_abstained,
            "context_engine_enabled": self.context_engine_enabled,
            "context_engine_ms": self.context_engine_ms,
            "context_engine_gather_ms": self.context_engine_gather_ms,
            "context_engine_score_ms": self.context_engine_score_ms,
            "context_engine_dedup_ms": self.context_engine_dedup_ms,
            "context_engine_budget_ms": self.context_engine_budget_ms,
            "context_engine_render_ms": self.context_engine_render_ms,
            "context_engine_candidates": self.context_engine_candidates,
            "context_engine_selected": self.context_engine_selected,
            "context_engine_dropped": self.context_engine_dropped,
            "context_engine_tokens": self.context_engine_tokens,
            "context_engine_chars": self.context_engine_chars,
            "context_engine_failed": self.context_engine_failed,
            # Backward compat: expose new retrieval/context/outcome fields for existing dashboards
            "context_engine_failed_flag": self.context_engine_failed,
            # Phase 101-103 observability (already-computed, never authoritative)
            "warming_level": self.warming_level,
            "warming_score": self.warming_score,
            "warming_available": self.warming_available,
            "warming_ceiling": self.warming_ceiling,
            "readiness_level": self.readiness_level,
            "readiness_available": self.readiness_available,
            "menu_items": self.menu_items,
            "menu_context_chars": self.menu_context_chars,
            "free_photo_outcome": self.free_photo_outcome,
            "free_photo_vault_item": self.free_photo_vault_item,
            "free_photo_llm_flag": self.free_photo_llm_flag,
            "free_photo_delivery": self.free_photo_delivery,
            "free_photo_telegram_id": self.free_photo_telegram_id,
            "free_photo_reservation_id": self.free_photo_reservation_id,
            # Phase 9: context vs authority (bounded, telemetry only)
            "commerce_context": self.commerce_context,
            "commerce_authorization_basis": self.commerce_authorization_basis,
            "commerce_user_initiated": self.commerce_user_initiated,
            "commerce_warmth_without_evidence": self.commerce_warmth_without_evidence,
            # Phase 10: versioned attribution + separated outcome namespaces
            "config_version": self.config_version,
            "strategy_version": self.strategy_version,
            "ranking_policy_version": self.ranking_policy_version,
            "relationship_outcome": self.relationship_outcome,
            "commerce_outcome": self.commerce_outcome,
            "attribution_status": self.attribution_status,
            "maturity_policy_version": self.maturity_policy_version,
            "maturity_state": self.maturity_state,
            "generation_creator_scope": self.generation_creator_scope,
            "calibration_available": self.calibration_available,
            # Pass 0: OneCall stabilization baseline (fail-open, None when unmeasured)
            "authoritative_assembly_ms": self.authoritative_assembly_ms,
            "compaction_ms": self.compaction_ms,
            "serialization_ms": self.serialization_ms,
            "provider_ms": self.provider_ms,
            "prompt_ms": self.prompt_ms,
            "predicted_ms": self.predicted_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "validation_ms": self.validation_ms,
            "routing_ms": self.routing_ms,
            "total_ms": self.total_ms,
            "semantic_invoked": self.semantic_invoked,
            "semantic_required": self.semantic_required,
            "semantic_hits": self.semantic_hits,
            "ce_tokens": self.ce_tokens,
            "retrieval_metrics": self.retrieval_metrics,
        }


class TelemetryCollector:
    """Collects and stores telemetry data.

    Observational only — never mutates commerce state.
    Best-effort — failures are logged, never propagate.
    """

    def __init__(self):
        # Composite key (creator_id, generation_id) for isolation; legacy fallback via generation_id alone
        self._telemetry_cache: dict[tuple[int | None, str], GenerationTelemetry] = {}

    def _cache_key(self, generation_id: str, creator_id: int | None) -> tuple[int | None, str]:
        return (creator_id, generation_id)

    def start_generation(
        self,
        user_id: int,
        creator_id: int | None = None,
        runtime_mode: str = "legacy",
        worker_id: str | None = None,
        generation_id: str | None = None,
    ) -> GenerationTelemetry:
        """Start tracking a new generation."""
        telemetry = GenerationTelemetry(
            user_id=user_id,
            creator_id=creator_id,
            runtime_mode=runtime_mode,
            worker_id=worker_id,
        )
        # Allow caller to specify generation_id (for correlation with event bus)
        if generation_id:
            telemetry.generation_id = generation_id
        # Creator-scoped key: (creator_id, generation_id) — canonical generation_id remains MD5(user:msg:tgId) unchanged
        self._telemetry_cache[self._cache_key(telemetry.generation_id, telemetry.creator_id)] = (
            telemetry
        )
        return telemetry

    def get(self, generation_id: str, creator_id: int | None = None) -> GenerationTelemetry | None:
        """Get telemetry by generation_id. Creator-scoped when provided, else fallback to any.

        Phases 1-4: the no-creator fallback is retained for backward-compatible
        callers, but it refuses ambiguous matches: when entries for multiple
        creators share the same generation_id (possible because the
        Telegram-derived MD5 excludes creator_id), None is returned instead
        of another creator's telemetry to prevent cross-creator collisions.
        """
        if creator_id is not None:
            return self._telemetry_cache.get(self._cache_key(generation_id, creator_id))
        # Fallback: find any entry with this generation_id (backward compat for callers without creator_id)
        _match = None
        for (cid, gid), val in self._telemetry_cache.items():
            if gid == generation_id:
                if _match is not None:
                    return None
                _match = val
        return _match

    async def record(self, telemetry: GenerationTelemetry) -> None:
        """Record telemetry to database.

        Best-effort — failures are logged, never propagate.
        """
        try:
            from db.postgres import insert_generation_telemetry

            await insert_generation_telemetry(telemetry.to_dict())
        except Exception as e:
            logger.warning(f"Failed to record telemetry: {e}")

        # Cleanup cache — creator-scoped
        self._telemetry_cache.pop(
            self._cache_key(telemetry.generation_id, telemetry.creator_id), None
        )

    def record_sync(self, telemetry: GenerationTelemetry) -> None:
        """Record telemetry synchronously (for fire-and-forget).

        Best-effort — failures are logged, never propagate.
        """
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule for later
                loop.create_task(self.record(telemetry))
            else:
                loop.run_until_complete(self.record(telemetry))
        except Exception as e:
            logger.warning(f"Failed to record telemetry (sync): {e}")

        # Cleanup cache
        self._telemetry_cache.pop(
            self._cache_key(telemetry.generation_id, telemetry.creator_id), None
        )


# Global collector
_collector = TelemetryCollector()


def get_telemetry_collector() -> TelemetryCollector:
    """Get the global telemetry collector."""
    return _collector
