"""Context Engine Worker Integration (Phase 73 + Phase 77).

Narrow, feature-gated, fail-open integration between the Context Engine
and the production LLM worker path.

Design principles:
- OBSERVATIONAL ONLY: Context Engine output is never used for decisions
- FAIL-OPEN: any failure does not affect production processing
- FEATURE-GATED: disabled by default (context_engine_observational=False)
- ISOLATED: all logic in this module; worker gets a single function call
- NO AUTHORITY: Context Engine cannot send, offer, or modify commerce state
- CREATOR-SCOPED: all data is scoped to the current creator/user pair

Phase 77 adds A/B canary observation:
- Canary runs AFTER Context Engine observation
- Canary feeds compact context to Qwen for one generation
- Canary output is compared against authoritative path
- Canary NEVER sends, offers, or mutates state
- Canary failure does not affect production path
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("context_engine.worker")


# ---------------------------------------------------------------------------
# Observation Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextEngineObservation:
    """Result of a Context Engine run.

    Contains telemetry and the rendered context for production use.
    """

    enabled: bool
    pipeline_result: Any | None = None  # ContextPipelineResult or None on failure
    total_ms: float = 0.0
    gather_ms: float = 0.0
    score_ms: float = 0.0
    dedup_ms: float = 0.0
    budget_ms: float = 0.0
    render_ms: float = 0.0
    candidate_count: int = 0
    selected_count: int = 0
    dropped_count: int = 0
    token_count: int = 0
    char_count: int = 0
    rendered_text: str = ""  # Rendered context blocks for production use
    failed: bool = False
    error: str | None = None
    # Phase 87 detailed observability
    conflict_dropped: int = 0
    lexical_dedup_removed: int = 0
    truncation_count: int = 0
    budget_violations: int = 0
    degradation_level: int = 0
    category_tokens: dict | None = None
    retrieval_metrics: dict[str, Any] | None = None
    budget_ms_detailed: float = 0.0
    score_ms_detailed: float = 0.0


# ---------------------------------------------------------------------------
# Core Integration Function
# ---------------------------------------------------------------------------


async def observe_context_engine(
    *,
    user_id: int,
    creator_id: int | None,
    user_message: str,
    generation_id: str | None = None,
    persona_snapshot: dict[str, Any] | None = None,
    enabled: bool = False,
    conversation_state: dict[str, Any] | None = None,
    authoritative_state: Any | None = None,
    telemetry: Any | None = None,
) -> ContextEngineObservation:
    """Run the Context Engine in observational mode against production state.

    This function is the ONLY integration point between the production
    worker and the Context Engine. It must be called exactly once per
    generation, after context build and before LLM #1.

    When enabled=False (default), returns immediately with no work done.

    When enabled=True:
    1. Builds a ContextRequest from authoritative production state
    2. Runs the full Context Engine pipeline
    3. Captures timing and output metrics
    4. Returns ContextEngineObservation (telemetry only)

    FAIL-OPEN: any exception is caught and logged. The production
    worker path is never affected by Context Engine failure.

    Args:
        user_id: Target user (from Telegram event sender)
        creator_id: Creator persona owner (from resolve_single_application_creator)
        user_message: Latest inbound user message
        generation_id: Deterministic generation identifier
        persona_snapshot: Structured persona data (already fetched by worker)
        enabled: Feature flag from settings.context_engine_observational
        conversation_state: Derived conversation state for state relevance scoring
        telemetry: Optional GenerationTelemetry filled in place (Pass 0;
            fail-open, never raises; None = skip).

    Returns:
        ContextEngineObservation with metrics and result (or failure info)
    """
    if not enabled:
        _obs = ContextEngineObservation(enabled=False)
        if telemetry is not None:
            apply_observation_to_telemetry(_obs, telemetry)
        return _obs

    _start = time.monotonic()

    try:
        from context_engine.integration import (
            ContextEngineIntegration,
            ContextRequest,
        )

        # Build request from authoritative production state (Phase 2: snapshot reuse)
        request = ContextRequest(
            creator_id=creator_id,
            user_id=user_id,
            current_message=user_message,
            conversation_state=conversation_state,
            authoritative_state=authoritative_state,
            metadata={
                "generation_id": generation_id or "",
                "source": "production_worker",
            },
        )

        # Run full pipeline
        engine = ContextEngineIntegration()
        pipeline_result = await engine.process(request)

        _total_ms = (time.monotonic() - _start) * 1000

        # Extract metrics from pipeline result
        _rendered = pipeline_result.rendered
        _char_count = 0
        _rendered_text = ""
        # Phase 2: preserve authority-labelled sections for OneCall
        # Previously only memory/temporal/commerce/content were forwarded and
        # authority markers were stripped. Now we forward all rendered blocks
        # with explicit section headers so OneCall can distinguish
        # [CURRENT AUTHORITATIVE STATE] vs [HISTORICAL] vs [RETRIEVED] vs [ADVISORY].
        if _rendered:
            _char_count = (
                len(_rendered.system_prompt)
                + len(_rendered.state_block)
                + len(_rendered.commerce_block)
                + len(_rendered.memory_block)
                + len(_rendered.temporal_block)
                + len(_rendered.content_block)
            )
            # Build rendered text for production use (authority-labelled)
            _blocks: list[str] = []
            if _rendered.system_prompt:
                _blocks.append(f"[CURRENT AUTHORITATIVE STATE - SYSTEM]\n{_rendered.system_prompt}")
            if _rendered.state_block:
                _blocks.append(f"[CURRENT AUTHORITATIVE STATE]\n{_rendered.state_block}")
            if _rendered.commerce_block:
                _blocks.append(f"[CURRENT AUTHORITATIVE STATE - COMMERCE]\n{_rendered.commerce_block}")
            if _rendered.memory_block:
                _blocks.append(f"[RETRIEVED KNOWLEDGE - DERIVED]\n{_rendered.memory_block}")
            if _rendered.temporal_block:
                _blocks.append(f"[RETRIEVED KNOWLEDGE - TEMPORAL]\n{_rendered.temporal_block}")
            if _rendered.content_block:
                _blocks.append(f"[RETRIEVED CONTENT]\n{_rendered.content_block}")
            # Also include conversation turns count in char count but not in rendered_text (handled via OneCall snapshot path)
            _rendered_text = "\n\n".join(_blocks)

        # Phase 87: capture detailed split metrics
        _conflict_dropped = getattr(pipeline_result, "conflict_dropped_count", 0)
        _lexical_dedup = getattr(pipeline_result, "lexical_dedup_removed_count", 0)
        _truncation = getattr(pipeline_result, "truncation_count", 0)
        _violations = len(getattr(pipeline_result, "violations", []) or [])
        _retrieval_metrics = getattr(pipeline_result, "retrieval_metrics", None)
        _score_ms_detailed = getattr(pipeline_result, "score_ms", 0.0)
        _dedup_ms_detailed = getattr(pipeline_result, "dedup_ms", 0.0)
        _budget_ms_detailed = getattr(pipeline_result, "budget_ms", 0.0)
        _render_ms_detailed = getattr(pipeline_result, "render_ms", 0.0)
        observation = ContextEngineObservation(
            enabled=True,
            pipeline_result=pipeline_result,
            total_ms=_total_ms,
            gather_ms=pipeline_result.gather_time_ms,
            score_ms=_score_ms_detailed,
            dedup_ms=_dedup_ms_detailed,
            budget_ms=_budget_ms_detailed,
            render_ms=_render_ms_detailed,
            candidate_count=pipeline_result.candidate_count,
            selected_count=pipeline_result.selected_count,
            dropped_count=pipeline_result.candidate_count - pipeline_result.selected_count,
            token_count=pipeline_result.total_tokens,
            char_count=_char_count,
            rendered_text=_rendered_text,
            failed=False,
            conflict_dropped=_conflict_dropped,
            lexical_dedup_removed=_lexical_dedup,
            truncation_count=_truncation,
            budget_violations=_violations,
            degradation_level=pipeline_result.degradation_level,
            category_tokens=dict(pipeline_result.category_tokens),
            retrieval_metrics=_retrieval_metrics,
            budget_ms_detailed=_budget_ms_detailed,
            score_ms_detailed=_score_ms_detailed,
        )

        logger.debug(
            "context_engine_observational complete: candidates=%d selected=%d "
            "tokens=%d total_ms=%.1f",
            observation.candidate_count,
            observation.selected_count,
            observation.token_count,
            observation.total_ms,
        )

        if telemetry is not None:
            apply_observation_to_telemetry(observation, telemetry)

        return observation

    except Exception as exc:  # noqa: BLE001 — fail-open: Context Engine failure must not affect production
        _total_ms = (time.monotonic() - _start) * 1000
        logger.warning(
            "context_engine_observational failed (fail-open): %s", exc
        )
        _failed = ContextEngineObservation(
            enabled=True,
            total_ms=_total_ms,
            failed=True,
            error=str(exc)[:200],
        )
        if telemetry is not None:
            apply_observation_to_telemetry(_failed, telemetry)
        return _failed


# ---------------------------------------------------------------------------
# Pass 0: Telemetry pass-through (fail-open, no authority)
# ---------------------------------------------------------------------------


def apply_observation_to_telemetry(
    observation: ContextEngineObservation,
    telemetry: Any,
) -> Any:
    """Copy an observation into a GenerationTelemetry in place (Pass 0).

    Populates retrieval_metrics (lexical/semantic counts, degraded,
    thresholds) and the CE/integration totals so the worker's telemetry
    record carries the full Pass 0 baseline without a second read.
    Never raises; returns the telemetry object unchanged on any failure.
    """
    try:
        if telemetry is None or observation is None:
            return telemetry
        try:
            telemetry.context_engine_observed = bool(observation.enabled)
        except Exception:
            pass
        rm = getattr(observation, "retrieval_metrics", None)
        if not isinstance(rm, dict):
            rm = {}
        try:
            telemetry.retrieval_metrics = dict(rm)
        except Exception:
            pass
        try:
            telemetry.lexical_candidate_count = int(rm.get("lexical_candidate_count", 0))
            telemetry.semantic_candidate_count = int(rm.get("semantic_candidate_count", 0))
            telemetry.merged_candidate_count = int(
                rm.get("merged_candidate_count", getattr(observation, "candidate_count", 0) or 0)
            )
            telemetry.lexical_latency_ms = rm.get("lexical_latency_ms")
            telemetry.embedding_latency_ms = rm.get("embedding_latency_ms")
            telemetry.retrieval_latency_ms = rm.get("total_retrieval_ms", getattr(observation, "gather_ms", None))
            telemetry.retrieval_degraded = bool(rm.get("degraded", bool(getattr(observation, "failed", False))))
            telemetry.lexical_threshold = int(rm.get("lexical_threshold", 80))
            telemetry.semantic_threshold = float(rm.get("semantic_threshold", 0.30))
        except Exception:
            pass
        try:
            telemetry.context_engine_enabled = bool(observation.enabled and not observation.failed)
            telemetry.context_engine_ms = float(observation.total_ms or 0.0)
            telemetry.context_engine_gather_ms = float(observation.gather_ms or 0.0)
            telemetry.context_engine_candidates = int(observation.candidate_count or 0)
            telemetry.context_engine_selected = int(observation.selected_count or 0)
            telemetry.context_engine_dropped = int(observation.dropped_count or 0)
            telemetry.context_engine_tokens = int(observation.token_count or 0)
            telemetry.context_engine_chars = int(observation.char_count or 0)
            telemetry.context_engine_failed = bool(observation.failed)
            telemetry.ce_tokens = int(observation.token_count or 0)
            telemetry.semantic_hits = int(rm.get("semantic_candidate_count", 0))
            # Invoked = CE ran and produced retrieval metrics; required mirrors
            # invoked here (the retrieval gate decision itself lives in
            # MemorySource.gather and is recorded via degraded/metrics).
            telemetry.semantic_invoked = bool(observation.enabled and not observation.failed)
            telemetry.semantic_required = bool(observation.enabled and not observation.failed)
        except Exception:
            pass
        return telemetry
    except Exception:
        return telemetry


# ---------------------------------------------------------------------------
# Phase 77: A/B Canary Observation
# ---------------------------------------------------------------------------


async def observe_canary(
    *,
    user_id: int,
    creator_id: int | None,
    user_message: str,
    generation_id: str,
    context_messages: list[dict[str, str]],
    authoritative_snapshot: Any | None = None,
    context_engine_observation: ContextEngineObservation | None = None,
    canary_config: Any | None = None,
) -> Any:
    """Run the A/B canary observation path.

    This function is called AFTER observe_context_engine() and runs the
    Context Engine + Qwen canary path for A/B comparison.

    SAFETY:
    - OBSERVATIONAL ONLY: output is compared but never acted upon
    - FAIL-OPEN: any failure does not affect production processing
    - FEATURE-GATED: disabled by default (context_engine_canary_mode="disabled")
    - NO AUTHORITY: canary cannot send, offer, or modify commerce state

    When mode is "disabled" (default), returns immediately with no work done.

    Args:
        user_id: Target user ID
        creator_id: Creator ID
        user_message: Latest inbound message
        generation_id: Deterministic generation ID
        context_messages: Context from Context Engine (Qwen-compatible format)
        authoritative_snapshot: Snapshot of authoritative path decisions
        context_engine_observation: Existing Context Engine observation
        canary_config: CanaryConfig instance (built from settings if None)

    Returns:
        CanaryObservationRecord with comparison data (or failure info)
    """
    from context_engine.canary_config import CanaryConfig, CanaryMode

    if canary_config is None:
        canary_config = CanaryConfig.from_settings()

    if canary_config.mode == CanaryMode.DISABLED:
        return None

    if not canary_config.should_run(user_id):
        return None

    _start = time.monotonic()

    try:
        from context_engine.canary_observer import CanaryObserver

        observer = CanaryObserver(config=canary_config)

        record = await observer.observe(
            user_id=user_id,
            creator_id=creator_id,
            user_message=user_message,
            generation_id=generation_id,
            context_messages=context_messages,
            authoritative_snapshot=authoritative_snapshot,
            context_engine_observation=context_engine_observation,
        )

        _total_ms = (time.monotonic() - _start) * 1000
        record.total_canary_ms = _total_ms

        logger.debug(
            "canary observation complete: disagreement=%s intent=%s "
            "commerce=%s confidence=%.2f total_ms=%.1f",
            record.disagreement.value,
            record.canary_intent,
            record.canary_commerce_action,
            record.canary_confidence,
            _total_ms,
        )

        return record

    except Exception as exc:  # noqa: BLE001 — fail-open: canary failure must not affect production
        _total_ms = (time.monotonic() - _start) * 1000
        logger.warning(
            "canary observation failed (fail-open): %s", exc
        )
        from context_engine.canary_record import (
            CanaryObservationRecord,
            DisagreementType,
        )

        return CanaryObservationRecord(
            generation_id=generation_id,
            user_id=user_id,
            creator_id=creator_id,
            canary_failed=True,
            canary_error=str(exc)[:200],
            disagreement=DisagreementType.INFRASTRUCTURE_FAILURE,
            total_canary_ms=_total_ms,
        )
