"""Phase 101 — Deterministic warming layer (CEILING, not target).

Warming is a bounded readiness signal for relationship-building, derived
solely from authoritative application state. No LLM, no second context,
no DB write, no PPV, no free-photo quota. Repeated evaluation on same
state is idempotent.

Frozen invariants (Phases 96D-100 remain):
 - warming is a ceiling/readiness signal, not an obligation to send
 - zero warming activity in a day is valid
 - LLM advisory only
 - creator-scoped
 - deterministic, no clock beyond already-derived hours thresholds
"""

from __future__ import annotations

from dataclasses import dataclass


class WarmingLevel(str):
    NOT_AVAILABLE = "not_available"
    COLD = "cold"
    WARM = "warm"
    HOT = "hot"
    COOLDOWN = "cooldown"
    AFTERCARE = "aftercare"


@dataclass(frozen=True)
class WarmingState:
    level: str  # NOT_AVAILABLE / COLD / WARM / HOT / COOLDOWN / AFTERCARE
    score: float  # 0.0 .. 1.0
    available: bool  # False when degraded (missing creator, cooldown, aftercare)
    ceiling: float  # 0..1, never interpreted as target count
    degraded_reason: str | None = None


def derive_warming_state(
    *,
    creator_id: int | None = None,
    relationship_state: str = "cold",
    desire_stage: str = "relationship",
    purchase_intent: float = 0.0,
    recent_offer_count: int = 0,
    consecutive_rejections: int = 0,
    hours_since_last_offer: float | None = None,
    hours_since_last_purchase: float | None = None,
    aftercare_status: str = "none",
    commercial_paused: bool = False,
    has_active_offer: bool = False,
) -> WarmingState:
    """Pure deterministic warming. No LLM, no DB, no clock.

    Creator-scoped via caller-supplied authoritative values (already
    creator-scoped). If creator_id is None, warming is NOT_AVAILABLE
    (fail-closed). Aftercare/cooldown/paused degrade to not_available.
    """
    # Degraded: missing creator or required state
    if creator_id is None or creator_id <= 0:
        return WarmingState(level=WarmingLevel.NOT_AVAILABLE, score=0.0, available=False, ceiling=0.0, degraded_reason="missing_creator")

    if aftercare_status in ("pending", "sent"):
        return WarmingState(level=WarmingLevel.AFTERCARE, score=0.15, available=False, ceiling=0.0, degraded_reason="aftercare_active")

    if commercial_paused or consecutive_rejections >= 3:
        return WarmingState(level=WarmingLevel.COOLDOWN, score=0.20, available=False, ceiling=0.0, degraded_reason="commercial_paused")

    if has_active_offer:
        return WarmingState(level=WarmingLevel.COOLDOWN, score=0.25, available=False, ceiling=0.0, degraded_reason="active_offer")

    # Derive base from relationship + desire (authoritative)
    rel_map = {
        "cold": 0.20,
        "new": 0.30,
        "engaged": 0.55,
        "warm": 0.65,
        "buying_signal": 0.75,
        "purchased": 0.60,
        "repeat_buyer": 0.70,
        "vip": 0.80,
        "cooling_down": 0.30,
        "do_not_push": 0.10,
        "operator_required": 0.10,
    }
    rel = rel_map.get(relationship_state.lower(), 0.35) if relationship_state else 0.35

    desire_boost = {
        "relationship": 0.00,
        "curiosity": 0.15,
        "interest": 0.25,
        "desire": 0.40,
        "qualification": 0.50,
        "offer_ready": 0.55,
        "purchase": 0.10,
        "aftercare": 0.00,
        "repeat": 0.20,
    }.get(desire_stage.lower(), 0.0)

    # Purchase intent is advisory but bounded; not authoritative for warming
    pi = max(0.0, min(1.0, purchase_intent or 0.0)) * 0.15

    # Fatigue from recent offers (already-derived hours thresholds)
    fatigue = 0.0
    if recent_offer_count >= 1:
        fatigue += 0.12
    if recent_offer_count >= 2:
        fatigue += 0.18
    if hours_since_last_offer is not None and hours_since_last_offer < 24:
        fatigue += 0.12
    if hours_since_last_purchase is not None and hours_since_last_purchase < 6:
        fatigue += 0.20

    fatigue = min(0.50, fatigue)

    raw = rel * 0.45 + desire_boost * 0.55 + pi - fatigue * 0.70
    score = max(0.0, min(1.0, raw + 0.20))
    # Ceiling is derived from score but never a target count
    ceiling = round(score, 3)

    if score >= 0.60:
        level = WarmingLevel.HOT
    elif score >= 0.35:
        level = WarmingLevel.WARM
    else:
        level = WarmingLevel.COLD

    # Not available if no warming signal and cold
    available = level in (WarmingLevel.WARM, WarmingLevel.HOT)
    degraded = None if available else ("warming_not_available" if level == WarmingLevel.COLD else None)

    return WarmingState(level=level, score=round(score, 3), available=available, ceiling=ceiling, degraded_reason=degraded)
