"""Commercial temperature — bounded COLD/WARM/HOT (Phase 2.2).

Combines multiple signals: relationship strength + desire + purchase
intent + content interest − fatigue − rejection − recent offer pressure.

Deterministic, no LLM, no clock, bounded 0..1 then mapped to labels.
Mirrors the conceptual formula in the spec:

    temperature = positive_relationship + desire + purchase_intent
                  + content_interest − fatigue − rejection − recent_offer
"""

from __future__ import annotations

from dataclasses import dataclass


class Temperature(str):
    COLD = "cold"
    WARM = "warm"
    HOT = "hot"


@dataclass(frozen=True)
class CommercialTemperature:
    level: str  # COLD/WARM/HOT
    score: float  # 0..1
    sales_fatigue: str  # none / low / medium / high


def derive_commercial_temperature(
    *,
    relationship_score: float | None = None,
    desire_stage: str = "relationship",
    purchase_intent: float = 0.0,
    content_interest: float = 0.0,
    recent_offer_count: int = 0,
    recent_sales_attempts: int = 0,
    consecutive_rejections: int = 0,
    hours_since_last_offer: float | None = None,
    hours_since_last_purchase: float | None = None,
    aftercare_status: str = "none",
    commercial_paused: bool = False,
) -> CommercialTemperature:
    """Bounded deterministic temperature.

    Weights chosen to require multiple signals for HOT — single generic
    compliment (purchase_intent 0.1) stays COLD, consistent with spec.
    """
    # Base from relationship
    rel = max(0.0, min(1.0, (relationship_score or 0.2)))
    desire_boost = {
        "relationship": 0.0,
        "curiosity": 0.15,
        "interest": 0.25,
        "desire": 0.40,
        "qualification": 0.50,
        "offer_ready": 0.60,
        "purchase": -0.10,
        "aftercare": -0.30,
        "repeat": 0.20,
    }.get(desire_stage, 0.0)

    purchase = max(0.0, min(1.0, purchase_intent or 0.0)) * 0.30
    content = max(0.0, min(1.0, content_interest or 0.0)) * 0.15

    fatigue = 0.0
    if recent_offer_count >= 1:
        fatigue += 0.15
    if recent_offer_count >= 2:
        fatigue += 0.20
    if recent_sales_attempts >= 2:
        fatigue += 0.15
    if consecutive_rejections >= 2:
        fatigue += 0.20
    if consecutive_rejections >= 3:
        fatigue += 0.25
    if hours_since_last_offer is not None and hours_since_last_offer < 24:
        fatigue += 0.15
    if hours_since_last_purchase is not None and hours_since_last_purchase < 6:
        fatigue += 0.30
    if aftercare_status in ("pending", "sent"):
        fatigue += 0.25
    if commercial_paused:
        fatigue += 0.30

    fatigue = min(0.65, fatigue)
    sales_fatigue_label = "high" if fatigue >= 0.40 else ("medium" if fatigue >= 0.20 else ("low" if fatigue > 0 else "none"))

    raw = rel * 0.30 + desire_boost * 0.70 + purchase + content - fatigue * 0.90
    # normalize roughly to 0..1 via tanh-ish clamp
    score = max(0.0, min(1.0, raw + 0.35))

    if score >= 0.65:
        level = Temperature.HOT
    elif score >= 0.35:
        level = Temperature.WARM
    else:
        level = Temperature.COLD

    return CommercialTemperature(level=level, score=round(score, 3), sales_fatigue=sales_fatigue_label)
