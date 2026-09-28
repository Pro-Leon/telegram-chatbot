"""Phase 75D: Commerce signal integration for one-call prompt.

Provides commerce context to Qwen2.5 so it can output accurate
commerce signals alongside the reply. The signals remain advisory
— no field directly authorizes price, payment, or access.

All commerce authority remains in deterministic engine:
- commerce/decision.py (decide_commerce_action)
- commerce/execution.py (execute_ppv)
- commerce/signals.py (signals_to_context)
"""

import logging
from typing import Any

logger = logging.getLogger("commerce_prompt")


def build_commerce_signal_hints(
    commerce_text: str,
    relationship_state: str | None = None,
    recent_offer_count: int = 0,
    recent_purchase_count: int = 0,
    has_active_offer: bool = False,
    has_relevant_product: bool = True,
) -> str:
    """Build compact commerce context for one-call prompt.

    Provides Qwen with commerce-relevant facts so it can output
    accurate advisory signals. Does NOT authorize any action.

    Args:
        commerce_text: Rendered commerce context from build_llm_context
        relationship_state: Current relationship state
        recent_offer_count: Number of recent offers
        recent_purchase_count: Number of recent purchases
        has_active_offer: Whether fan has an active offer
        has_relevant_product: Whether relevant product exists

    Returns:
        Compact commerce context string for prompt injection
    """
    hints: list[str] = []

    # Extract key facts from commerce text
    if commerce_text:
        key_facts = _extract_key_commerce_facts(commerce_text)
        if key_facts:
            hints.append("COMMERCE FACTS: " + "; ".join(key_facts[:3]))

    # Relationship context
    if relationship_state:
        hints.append(f"RELATIONSHIP: {relationship_state}")

    # Offer/purchase context
    if has_active_offer:
        hints.append("ACTIVE OFFER: yes")
    if recent_offer_count > 0:
        hints.append(f"RECENT OFFERS: {recent_offer_count}")
    if recent_purchase_count > 0:
        hints.append(f"RECENT PURCHASES: {recent_purchase_count}")
    if has_relevant_product:
        hints.append("RELEVANT PRODUCT: yes")

    return "\n".join(hints) if hints else ""


def _extract_key_commerce_facts(commerce_text: str) -> list[str]:
    """Extract key facts from commerce text."""
    facts: list[str] = []
    keywords = (
        "purchase", "tip", "revenue", "offer", "cooldown",
        "eligible", "status", "balance", "aftercare",
    )

    for line in commerce_text.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("[") and not line.startswith("─"):
            if any(kw in line.lower() for kw in keywords):
                facts.append(line)

    return facts


def build_commerce_signal_schema() -> dict[str, Any]:
    """Build the commerce signal schema for one-call output.

    Returns the schema definition for commerce_signals field
    in the one-call JSON output.
    """
    return {
        "purchase_intent": "float 0.0-1.0",
        "content_interest": "float 0.0-1.0",
        "relationship_engagement": "float 0.0-1.0",
        "price_interest": "float 0.0-1.0",
        "explicit_purchase_request": "boolean",
        "explicit_content_request": "boolean",
        "requested_price": "null or positive number",
        "declined_recent_offer": "boolean",
        "asks_for_free_content": "boolean",
        "negative_sentiment": "float 0.0-1.0",
        "confidence": "float 0.0-1.0",
        "evidence": "list of up to 5 short strings (no payment data)",
        "model_uncertainty": "float 0.0-1.0",
        "primary_intent": "one of: casual_chat, greeting, relationship_building, personal_disclosure, content_curiosity, content_request, price_inquiry, purchase_intent, repeat_purchase_intent, post_purchase, aftercare, tip_interest, complaint, custom_request, negotiation, hesitation, rejection, uncertain, reassurance, appreciation, operator_request, other",
        "intent_tags": "list of up to 5 intent tags",
        "negative_intent_tags": "list: hesitation, rejection, complaint",
        "fan_asks_question": "boolean",
    }


COMMERCE_SIGNAL_INSTRUCTIONS = """
COMMERCE SIGNALS:
You must output commerce_signals as a JSON object. These are YOUR OBSERVATIONS
about the fan's intent. Be honest and conservative.

Key rules:
1. "purchase_intent" = how likely the fan wants to buy (0.0 = not at all, 1.0 = ready to buy)
2. "content_interest" = how interested in content (0.0-1.0)
3. "relationship_engagement" = how engaged in conversation (0.0-1.0)
4. "price_interest" = how much asking about price (0.0-1.0)
5. "explicit_purchase_request" = fan explicitly said they want to buy
6. "explicit_content_request" = fan explicitly asked for content
7. "requested_price" = price fan mentioned (null if none)
8. "negative_sentiment" = how negative the fan is (0.0-1.0)
9. "confidence" = your confidence in these signals (0.0-1.0)
10. "evidence" = short quotes supporting your assessment (max 5, no payment data)

NEVER invent signals. If unsure, use 0.0-0.3 range.
NEVER include payment data (card numbers, CVV, etc.) in evidence.
"""
