"""Conversational sales objective — maps deterministic commerce outcome to LLM guidance.

The LLM receives language only (relationship, curiosity, tease) — never
product_id, price, URL, or offer authority. This bridges the deterministic
commerce engine to the conversational AI without duplicating decision logic.
"""

from commerce.selection import CommerceSelectionResult, CommerceSelectionStatus

_OBJECTIVE_MAP = {
    "commerce_unavailable": "no_sale",
    "commerce_not_applicable": "relationship",
    "response_generation_failed": "relationship",
    "execution_failed": "relationship",
    "state_unavailable": "no_sale",
    "creator_context_unavailable": "no_sale",
    "product_unavailable": "no_sale",
    "eligibility_unavailable": "no_sale",
    "resolution_failed": "no_sale",
    "empty_response": "relationship",
    "invalid_response": "relationship",
    "unexpected_error": "relationship",
}

def derive_commercial_objective(
    selection: CommerceSelectionResult | None,
    relationship_state: str | None = None,
) -> str:
    """Map selection result to a conversational job.

    Returns one of:
      relationship | explore | build_desire | qualify | recommend |
      present_offer | objection_handling | aftercare | reengage | no_sale
    """
    if selection is None:
        # commerce unavailable → no_sale
        return "no_sale"

    if selection.status == CommerceSelectionStatus.USE_COMMERCE_RESPONSE:
        return "present_offer"

    if selection.status == CommerceSelectionStatus.COMMERCE_UNAVAILABLE:
        return "no_sale"

    # FALLBACK path — conversational should handle naturally
    reason = selection.reason.value if hasattr(selection.reason, 'value') else str(selection.reason)

    base = _OBJECTIVE_MAP.get(reason, "relationship")

    # Refine relationship into build_desire vs qualify vs explore based on relationship
    if base == "relationship" and relationship_state in ("WARM", "BUYING_SIGNAL", "warm", "engaged"):
        return "build_desire"
    if base == "relationship" and relationship_state in ("PURCHASED", "REPEAT_BUYER", "VIP", "vip", "purchased"):
        return "aftercare"

    return base
