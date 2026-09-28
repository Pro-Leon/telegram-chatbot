"""Phase 101 — Deterministic readiness evaluation (FAIL-CLOSED).

Readiness is derived from authoritative warming/desire/sales-window state,
not from LLM declarations like "user is ready". LLM advisory is ignored
for authority. Creator-scoped via caller-supplied authoritative values.
No DB write, no PPV, no quota, idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass

from commerce.offer_readiness import OfferReadiness, evaluate_offer_readiness
from commerce.warming import WarmingState


class ReadinessLevel(str):
    NOT_READY = "not_ready"
    BUILD_WARMING = "build_warming"
    READY = "ready"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class ReadinessState:
    level: str
    available: bool
    degraded_reason: str | None = None
    offer_readiness: str | None = None


def evaluate_readiness(
    *,
    warming: WarmingState | None = None,
    desire_stage: str = "relationship",
    temperature: str = "cold",
    purchase_intent: float = 0.0,
    has_active_offer: bool = False,
    aftercare_active: bool = False,
    is_on_cooldown: bool = False,
    has_relevant_product: bool = True,
    not_purchased: bool = True,
    creator_id: int | None = None,
    desire_evidence: tuple[str, ...] | list[str] | None = None,
) -> ReadinessState:
    """Pure deterministic readiness. Fail-closed on degraded.

    Acyclic: readiness is determinable from warming/desire/temperature and
    authoritative prerequisites BEFORE sales-window is evaluated. Sales-window
    then consumes the resulting readiness (readiness gates window, not vice
    versa). No LLM, no sales_window input, no circular dependency.

    Degraded conditions (missing creator, aftercare, cooldown, active offer,
    no relevant product) → NOT_READY/DEGRADED, never READY.
    """
    if creator_id is None or creator_id <= 0:
        return ReadinessState(level=ReadinessLevel.DEGRADED, available=False, degraded_reason="missing_creator", offer_readiness=OfferReadiness.NOT_READY.value)

    if aftercare_active:
        return ReadinessState(level=ReadinessLevel.DEGRADED, available=False, degraded_reason="aftercare_active", offer_readiness=OfferReadiness.NOT_READY.value)

    if is_on_cooldown:
        return ReadinessState(level=ReadinessLevel.DEGRADED, available=False, degraded_reason="on_cooldown", offer_readiness=OfferReadiness.NOT_READY.value)

    if has_active_offer:
        return ReadinessState(level=ReadinessLevel.DEGRADED, available=False, degraded_reason="active_offer", offer_readiness=OfferReadiness.NOT_READY.value)

    if not has_relevant_product or not not_purchased:
        return ReadinessState(level=ReadinessLevel.DEGRADED, available=False, degraded_reason="no_relevant_product_or_purchased", offer_readiness=OfferReadiness.NOT_READY.value)

    # Warming degraded → not ready (but not error)
    if warming is not None and not warming.available:
        if warming.level in ("not_available", "cooldown", "aftercare"):
            return ReadinessState(level=ReadinessLevel.DEGRADED, available=False, degraded_reason=warming.degraded_reason or "warming_not_available", offer_readiness=OfferReadiness.NOT_READY.value)
        if warming.level == "cold":
            return ReadinessState(level=ReadinessLevel.BUILD_WARMING, available=False, degraded_reason="warming_cold", offer_readiness=OfferReadiness.BUILD_DESIRE.value)

    # Delegate to existing offer_readiness for fine-grained commerce readiness.
    # This reuses proven logic without duplication and does NOT require
    # sales_window. Sales-window will later consume this readiness.
    offer_r = evaluate_offer_readiness(
        desire_stage=desire_stage,
        temperature=temperature,
        purchase_intent=purchase_intent,
        has_active_offer=has_active_offer,
        is_on_cooldown=is_on_cooldown,
        aftercare_active=aftercare_active,
        has_relevant_product=has_relevant_product,
        not_purchased=not_purchased,
        desire_evidence=desire_evidence,
    )

    if offer_r == OfferReadiness.READY:
        return ReadinessState(level=ReadinessLevel.READY, available=True, offer_readiness=offer_r.value)

    if offer_r == OfferReadiness.TEST_INTEREST:
        return ReadinessState(level=ReadinessLevel.BUILD_WARMING, available=False, offer_readiness=offer_r.value)

    return ReadinessState(level=ReadinessLevel.NOT_READY, available=False, offer_readiness=offer_r.value)
