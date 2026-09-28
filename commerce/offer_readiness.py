"""Offer readiness — deterministic, bounded (Phase 2.2).

Does NOT create offers. Decides if a natural transition toward a relevant
paid content offer is appropriate.

Inputs: desire stage, temperature, purchase intent, content relevance,
recent offer timing, fatigue, existing active offer, purchase history,
AUTONOMY_ENABLED, eligibility. All derived from existing state/DAO.

Outputs: NOT_READY / BUILD_DESIRE / TEST_INTEREST / READY
"""

from __future__ import annotations

import enum


class OfferReadiness(str, enum.Enum):
    NOT_READY = "not_ready"
    BUILD_DESIRE = "build_desire"
    TEST_INTEREST = "test_interest"
    READY = "ready"


def evaluate_offer_readiness(
    desire_stage: str,
    temperature: str,  # cold/warm/hot
    purchase_intent: float = 0.0,
    has_active_offer: bool = False,
    is_on_cooldown: bool = False,
    aftercare_active: bool = False,
    autonomy_enabled: bool = True,
    has_relevant_product: bool = True,
    not_purchased: bool = True,
    *,
    desire_evidence: tuple[str, ...] | list[str] | None = None,
) -> OfferReadiness:
    if not autonomy_enabled:
        return OfferReadiness.NOT_READY
    if aftercare_active:
        return OfferReadiness.NOT_READY
    if has_active_offer:
        return OfferReadiness.NOT_READY
    if not has_relevant_product or not not_purchased:
        return OfferReadiness.NOT_READY
    if is_on_cooldown:
        return OfferReadiness.NOT_READY
    if desire_stage in ("relationship", "curiosity"):
        return OfferReadiness.BUILD_DESIRE
    if desire_stage in ("interest", "desire"):
        # Phase 3 provenance fence: warmth-derived INTEREST carries no
        # commercial evidence → BUILD_DESIRE, never TEST_INTEREST.
        # Buying-signal / free-content / absent behave exactly as today.
        try:
            if desire_stage == "interest" and desire_evidence is not None:
                for _tok in desire_evidence:
                    if isinstance(_tok, str) and _tok.startswith("interest:warmth"):
                        return OfferReadiness.BUILD_DESIRE
        except Exception:
            pass
        if temperature == "cold":
            return OfferReadiness.BUILD_DESIRE
        return OfferReadiness.TEST_INTEREST
    if desire_stage in ("qualification", "offer_ready"):
        if temperature == "hot" and purchase_intent >= 0.55:
            return OfferReadiness.READY
        if purchase_intent >= 0.40:
            return OfferReadiness.TEST_INTEREST
        return OfferReadiness.BUILD_DESIRE
    if temperature == "hot" and purchase_intent >= 0.65:
        return OfferReadiness.READY
    return OfferReadiness.NOT_READY
