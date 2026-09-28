"""Desire ladder — deterministic conversational-commercial state machine (Phase 2.2).

Not a commerce decision. The LLM never sets purchase state; this maps
observable fan + conversation evidence to a stage that tells the LLM *what
conversational job* to do. Purchase/aftercare transitions remain via the
deterministic offer/transaction tables.

Stages 0-8 correspond to the sales journey, but persistence is transient
(derived per turn) plus long-term via funnel/offers/purchases.

No new tables. All inputs are existing derived state or 1 DB read.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone


class DesireStage(str, enum.Enum):
    RELATIONSHIP = "relationship"  # 0
    CURIOSITY = "curiosity"  # 1
    INTEREST = "interest"  # 2
    DESIRE = "desire"  # 3
    QUALIFICATION = "qualification"  # 4
    OFFER_READY = "offer_ready"  # 5 — strong buying window, offer permitted
    PURCHASE = "purchase"  # 6 — offer presented, awaiting outcome
    AFTERCARE = "aftercare"  # 7
    REPEAT = "repeat"  # 8 — ready again after aftercare/engagement


@dataclass(frozen=True)
class DesireState:
    stage: DesireStage
    confidence: float  # 0-1 how strongly this stage is held
    evidence: tuple[str, ...]  # e.g. ("asked about red dress", "price q")
    last_transition_at: str | None = None


# Decay: each unrelated hour or topic change reduces confidence.
# Deterministic: decay factor applied per 24h window, not wall-clock cron.
DECAY_PER_24H = 0.70  # temperature ×0.7 per 24h
DECAY_TOPIC_CHANGE = 0.70


def _norm_topic(s: str) -> str:
    return s.strip().lower()


def derive_desire_stage(
    *,
    relationship_state: str = "cold",
    primary_intent: str | None = None,
    intent_tags: list[str] | None = None,
    purchase_intent: float = 0.0,
    price_interest: float = 0.0,
    explicit_content_request: bool = False,
    explicit_purchase_request: bool = False,
    asked_for_free_content: bool = False,
    has_active_offer: bool = False,
    aftercare_status: str = "none",
    has_purchased: bool = False,
    hours_since_last_offer: float | None = None,
    hours_since_last_purchase: float | None = None,
    consecutive_rejections: int = 0,
    fan_asks_question: bool = False,
    commercial_paused: bool = False,
    current_topic: str | None = None,
    open_threads: tuple[str, ...] = (),
) -> DesireState:
    """Determine desire stage from observable evidence.

    Deterministic, bounded, no LLM, no clock. Uses existing
    relationship_state + intent + offer/purchase state.
    """
    intent_tags = intent_tags or []
    ev: list[str] = []
    confidence = 0.5

    # PURCHASE / AFTERCARE are purchase-state driven, highest priority
    if has_purchased or aftercare_status in ("pending", "sent"):
        if aftercare_status in ("pending", "sent"):
            return DesireState(DesireStage.AFTERCARE, 0.85, tuple(ev or ["recent purchase"]), None)
        return DesireState(DesireStage.PURCHASE, 0.85, tuple(ev or ["purchased"]), None)

    # Explicit strong commercial intent → OFFER_READY or QUALIFICATION
    if explicit_purchase_request or explicit_content_request:
        if explicit_purchase_request:
            return DesireState(DesireStage.OFFER_READY, 0.95, ("explicit purchase request",), None)
        return DesireState(DesireStage.QUALIFICATION, 0.85, ("explicit content request",), None)

    if asked_for_free_content:
        return DesireState(DesireStage.INTEREST, 0.55, ("interest:free-content",), None)

    # Cooldown / rejection suppresses to RELATIONSHIP even if desire was present
    if commercial_paused or consecutive_rejections >= 3:
        return DesireState(DesireStage.RELATIONSHIP, 0.40, ("cooldown",), None)
    if has_active_offer and (hours_since_last_offer or 0) < 24:
        # Recent offer still pending — not ready again
        return DesireState(DesireStage.PURCHASE, 0.50, ("recent offer pending",), None)

    # Hot purchase intent → OFFER_READY (evidence required)
    if purchase_intent >= 0.80 and primary_intent in (
        "purchase_intent",
        "price_inquiry",
        "content_request",
    ):
        return DesireState(
            DesireStage.OFFER_READY,
            purchase_intent,
            (f"purchase_intent {purchase_intent:.2f}",),
            None,
        )

    # Content desire → DESIRE / QUALIFICATION
    commercial_intents = set(intent_tags) & {
        "content_curiosity",
        "content_request",
        "price_inquiry",
        "purchase_intent",
        "tip_interest",
    }
    if primary_intent == "content_request" or "content_request" in intent_tags:
        return DesireState(DesireStage.QUALIFICATION, 0.75, ("content request",), None)
    if purchase_intent >= 0.55 or price_interest >= 0.55 or commercial_intents:
        return DesireState(
            DesireStage.DESIRE, max(purchase_intent, price_interest), ("desire signals",), None
        )
    if primary_intent in ("content_curiosity", "tip_interest") or content_interest_estimate(
        intent_tags, primary_intent
    ):
        return DesireState(DesireStage.CURIOSITY, 0.60, ("curiosity",), None)

    # Engagement without curiosity → INTEREST if relationship warm
    if relationship_state.lower() in ("warm", "engaged", "buying_signal", "warm", "engaged"):
        return DesireState(DesireStage.INTEREST, 0.55, ("interest:warmth",), None)
    if purchase_intent >= 0.30:
        return DesireState(DesireStage.INTEREST, 0.55, ("interest:buying-signal",), None)

    # Default — relationship
    return DesireState(DesireStage.RELATIONSHIP, 0.45, ("rapport",), None)


def content_interest_estimate(intent_tags: list[str], primary: str | None) -> bool:
    return primary == "content_curiosity" or "content_curiosity" in intent_tags


def decay_desire(confidence: float, hours_elapsed: float, topic_changed: bool) -> float:
    """Deterministic decay: ×0.7 per 24h + ×0.7 on topic change."""
    factor = 1.0
    if hours_elapsed >= 24:
        factor *= DECAY_PER_24H * (hours_elapsed / 24)
        factor = max(0.3, min(1.0, factor))
    else:
        factor = max(0.7, factor)
    if topic_changed:
        factor *= DECAY_TOPIC_CHANGE
    return max(0.15, min(0.95, confidence * factor))
