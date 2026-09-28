"""Phase 87 canonical stage events (fail-open, creator-safe).

Provides typed helpers for publishing canonical observability events via the
existing Redis Pub/Sub event_bus. All helpers are best-effort: failures are
logged, never propagated, and never affect business authority or response
selection.

Event naming (canonical snake_case as per §16):
- state_ready
- retrieval_ready
- context_ready
- one_call_start
- one_call_success
- one_call_failed
- authority_decision
- handoff
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("phase87_events")


async def _safe_publish(event_type: str, data: dict[str, Any], *, user_id: int | None, creator_id: int | None, generation_id: str | None, scope: str = "user") -> None:
    """Best-effort publish; never raises."""
    try:
        from core.event_bus import publish_event
        await publish_event(event_type, data, user_id=user_id, dialog_id=user_id, generation_id=generation_id, creator_id=creator_id, scope=scope)
    except Exception:  # noqa: BLE001 — fail-open
        logger.debug("phase87 event %s publish failed (fail-open)", event_type, exc_info=True)


async def emit_state_ready(*, user_id: int, creator_id: int | None, generation_id: str, runtime_mode: str, success: bool, acquisition_ms: int, degraded: bool) -> None:
    await _safe_publish("state_ready", {"stage": "STATE_READY", "success": success, "acquisition_ms": acquisition_ms, "degraded": degraded, "runtime_mode": runtime_mode}, user_id=user_id, creator_id=creator_id, generation_id=generation_id)


async def emit_retrieval_ready(*, user_id: int, creator_id: int | None, generation_id: str, runtime_mode: str, candidate_count: int, lexical_candidate_count: int, semantic_candidate_count: int, merged_candidate_count: int, retrieval_latency_ms: float | None, embedding_latency_ms: float | None, lexical_threshold: int, semantic_threshold: float, degraded: bool) -> None:
    await _safe_publish("retrieval_ready", {
        "candidate_count": candidate_count,
        "lexical_candidate_count": lexical_candidate_count,
        "semantic_candidate_count": semantic_candidate_count,
        "merged_candidate_count": merged_candidate_count,
        "retrieval_latency_ms": retrieval_latency_ms,
        "embedding_latency_ms": embedding_latency_ms,
        "lexical_threshold": lexical_threshold,
        "semantic_threshold": semantic_threshold,
        "degraded": degraded,
        "runtime_mode": runtime_mode,
    }, user_id=user_id, creator_id=creator_id, generation_id=generation_id)


async def emit_context_ready(*, user_id: int, creator_id: int | None, generation_id: str, runtime_mode: str, candidate_count: int, selected_count: int, conflict_dropped_count: int, lexical_dedup_count: int, total_deduplication_count: int, total_tokens: int, category_tokens: dict, degradation_level: int, truncation_count: int, budget_violations: int, gather_ms: float, ranking_ms: float, dedup_ms: float, budget_ms: float, render_ms: float, total_context_ms: float) -> None:
    await _safe_publish("context_ready", {
        "candidate_count": candidate_count,
        "selected_count": selected_count,
        "conflict_dropped_count": conflict_dropped_count,
        "lexical_dedup_count": lexical_dedup_count,
        "total_deduplication_count": total_deduplication_count,
        "total_tokens": total_tokens,
        "category_tokens": {str(k): v for k, v in (category_tokens or {}).items()},
        "degradation_level": degradation_level,
        "truncation_count": truncation_count,
        "budget_violations": budget_violations,
        "gather_ms": gather_ms,
        "ranking_ms": ranking_ms,
        "dedup_ms": dedup_ms,
        "budget_ms": budget_ms,
        "render_ms": render_ms,
        "total_context_ms": total_context_ms,
        "runtime_mode": runtime_mode,
        # budget constants for verification
        "budget_limit": 2600,
        "header_reserve": 60,
        "effective_budget": 2540,
    }, user_id=user_id, creator_id=creator_id, generation_id=generation_id)


async def emit_one_call_start(*, user_id: int, creator_id: int | None, generation_id: str, runtime_mode: str, provider_name: str, model_name: str, call_index: int, generation_kind: str) -> None:
    await _safe_publish("one_call_start", {
        "provider_name": provider_name,
        "model_name": model_name,
        "call_index": call_index,
        "generation_kind": generation_kind,
        "runtime_mode": runtime_mode,
        "timestamp_ms": int(time.time() * 1000),
    }, user_id=user_id, creator_id=creator_id, generation_id=generation_id)


async def emit_one_call_success(*, user_id: int, creator_id: int | None, generation_id: str, runtime_mode: str, provider_name: str, model_name: str, call_index: int, generation_kind: str, input_tokens: int | None, output_tokens: int | None, latency_ms: int | None, validation_status: str) -> None:
    await _safe_publish("one_call_success", {
        "provider_name": provider_name,
        "model_name": model_name,
        "call_index": call_index,
        "generation_kind": generation_kind,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "validation_status": validation_status,
        "runtime_mode": runtime_mode,
    }, user_id=user_id, creator_id=creator_id, generation_id=generation_id)


async def emit_one_call_failed(*, user_id: int, creator_id: int | None, generation_id: str, runtime_mode: str, provider_name: str, model_name: str, call_index: int, generation_kind: str, latency_ms: int | None, failure_reason: str, error_class: str | None = None) -> None:
    await _safe_publish("one_call_failed", {
        "provider_name": provider_name,
        "model_name": model_name,
        "call_index": call_index,
        "generation_kind": generation_kind,
        "latency_ms": latency_ms,
        "failure_reason": failure_reason,
        "error_class": error_class,
        "runtime_mode": runtime_mode,
    }, user_id=user_id, creator_id=creator_id, generation_id=generation_id)


async def emit_authority_decision(*, user_id: int, creator_id: int | None, generation_id: str, runtime_mode: str, action: str | None, status: str | None, product_id: int | None, price_authority: str | None, price_minor: int | None, currency: str | None, eligibility_result: str | None, execution_result: str | None) -> None:
    await _safe_publish("authority_decision", {
        "action": action,
        "status": status,
        "product_id": product_id,
        "price_authority": price_authority,
        "price_minor": price_minor,
        "currency": currency,
        "eligibility_result": eligibility_result,
        "execution_result": execution_result,
        "runtime_mode": runtime_mode,
    }, user_id=user_id, creator_id=creator_id, generation_id=generation_id)


async def emit_handoff(*, user_id: int, creator_id: int | None, generation_id: str, runtime_mode: str, reason: str, queue_id: int | None = None) -> None:
    await _safe_publish("handoff", {
        "reason": reason,
        "queue_id": queue_id,
        "runtime_mode": runtime_mode,
        "timestamp_ms": int(time.time() * 1000),
    }, user_id=user_id, creator_id=creator_id, generation_id=generation_id)


async def emit_sale_recorded(
    *,
    creator_id: int,
    user_id: int,
    offer_id: int,
    product_id: int | None,
    transaction_id: str,
    price_minor: int | None = None,
    currency: str | None = None,
    revenue_minor: int | None = None,
    first_sale: bool = False,
    occurred_at: str | None = None,
    generation_id: str | None = None,
) -> None:
    """P2.1: emit commerce.sale_recorded best-effort, never blocks purchase commit."""
    await _safe_publish(
        "commerce.sale_recorded",
        {
            "offer_id": offer_id,
            "creator_id": creator_id,
            "user_id": user_id,
            "product_id": product_id,
            "transaction_id": transaction_id,
            "price_minor": price_minor,
            "currency": currency,
            "revenue_minor": revenue_minor,
            "first_sale": first_sale,
            "occurred_at": occurred_at,
        },
        user_id=user_id,
        creator_id=creator_id,
        generation_id=generation_id,
        scope="user",
    )