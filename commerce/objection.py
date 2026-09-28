"""Objection Intelligence — deterministic classification (Phase 14)."""
from __future__ import annotations
import enum

class ObjectionType(str, enum.Enum):
    PRICE = "price"
    TIMING = "timing"
    TRUST = "trust"
    CONTENT_VALUE = "content_value"
    CONFUSION = "confusion"
    COMPARISON = "comparison"
    NO_INTEREST = "no_interest"
    ALREADY_PURCHASED = "already_purchased"
    DELIVERY = "delivery"
    TECHNICAL = "technical"
    OTHER = "other"

def classify_objection(text: str) -> ObjectionType:
    low = text.lower()
    if any(w in low for w in ["expensive", "price", "cost", "afford", "broke"]):
        return ObjectionType.PRICE
    if any(w in low for w in ["later", "busy", "not now", "maybe later", "think about"]):
        return ObjectionType.TIMING
    if any(w in low for w in ["trust", "scam", "fake"]):
        return ObjectionType.TRUST
    if any(w in low for w in ["not interested", "no thanks", "nah"]):
        return ObjectionType.NO_INTEREST
    if "already bought" in low or "already purchased" in low:
        return ObjectionType.ALREADY_PURCHASED
    return ObjectionType.OTHER
