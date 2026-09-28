"""Next Best Action — deterministic classification over existing state (Phase 14)."""
from __future__ import annotations
import enum

class NextBestAction(str, enum.Enum):
    RELATIONSHIP_BUILD = "relationship_build"
    EXPLORE_INTEREST = "explore_interest"
    DEEPEN_DESIRE = "deepen_desire"
    QUALIFY = "qualify"
    PRESENT_OFFER = "present_offer"
    HANDLE_OBJECTION = "handle_objection"
    AFTERCARE = "aftercare"
    REENGAGE = "reengage"
    REPEAT_PURCHASE = "repeat_purchase"
    ANSWER_ONLY = "answer_only"
    HANDOFF = "handoff"

def derive_next_best_action(desire: str, temperature: str, sales_window: str, offer_readiness: str, has_active_offer: bool, aftercare_status: str, objection_type: str | None = None) -> NextBestAction:
    if aftercare_status in ("pending", "sent"):
        return NextBestAction.AFTERCARE
    if has_active_offer:
        return NextBestAction.HANDLE_OBJECTION
    if desire == "offer_ready" and offer_readiness == "ready" and sales_window == "open":
        return NextBestAction.PRESENT_OFFER
    if desire in ("qualification",):
        return NextBestAction.QUALIFY
    if desire in ("desire",):
        return NextBestAction.DEEPEN_DESIRE
    if desire in ("interest",):
        return NextBestAction.EXPLORE_INTEREST
    if sales_window == "cooldown":
        return NextBestAction.RELATIONSHIP_BUILD
    if sales_window == "aftercare":
        return NextBestAction.AFTERCARE
    return NextBestAction.RELATIONSHIP_BUILD
