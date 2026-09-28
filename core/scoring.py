import json
import logging
from typing import Any

from core.llm_provider import get_llm_provider  # noqa: F401 — patched by tests as core.scoring.get_llm_provider

logger = logging.getLogger("scoring")

HARD_FLAGS = [
    "price_mention",
    "personal_info_request",
    "distress_signal",
    "explicit_request",
    "refund_complaint",
    "legal_mention",
    "competitor_mention",
    "photo_promise",
    "persona_identity_violation",
    "persona_question_policy_violation",
    "persona_voice_severe",
]

FLAG_KEYWORDS: dict[str, list[str]] = {
    "price_mention": [
        "price",
        "cost",
        "pay",
        "payment",
        "$",
        "$",
        "subscribe",
        "tip",
        "ppv",
        "buy",
        "purchase",
    ],
    "personal_info_request": [
        "phone",
        "instagram",
        "snapchat",
        "whatsapp",
        "email",
        "address",
        "contact",
    ],
    "distress_signal": [
        "depressed",
        "suicide",
        "hurt myself",
        "hate myself",
        "lonely",
        "nobody cares",
        "can't go on",
    ],
    "legal_mention": ["lawsuit", "lawyer", "report", "illegal", "police", "attorney", "complaint"],
    "photo_promise": [
        "send a pic", "send you a pic", "share a pic", "sharing a pic",
        "send a photo", "send you a photo", "share a photo",
        "send a selfie", "send you a selfie",
        "here's a pic", "here is a pic", "i can send",
    ],
}

SCORING_SYSTEM_PROMPT = """Score this chat response on these criteria.
IMPORTANT: Return a JSON object with the following fields:
{
  "contextually_aware": 0-10,
  "natural_tone": 0-10,
  "appropriate_length": 0-10,
  "not_repetitive": 0-10,
  "flags": []
}
flags can include: "off_topic", "too_formal", "too_generic",
"breaks_persona", "awkward_phrasing", "repetitive"

Only return valid JSON. Do not include any other text."""


async def score_draft(
    draft: str,
    user_message: str,
    context: list[dict[str, Any]],
    *,
    is_authorized_commerce: bool = False,
    authorized_price_minor: int | None = None,
    authorized_url: str | None = None,
) -> tuple[float, list[str]]:
    flags: list[str] = []

    user_lower = user_message.lower()
    draft_lower = draft.lower()

    for flag, keywords in FLAG_KEYWORDS.items():
        # P0-03 FIX: authorized commerce price is not a hallucinated price
        # When the deterministic commerce engine has authorized a product/price/URL,
        # a price mention that matches the authorized price must NOT be treated
        # as a hard-flag. Unauthorized price mentions remain blocked.
        if flag == "price_mention" and is_authorized_commerce:
            # If authorized, verify the draft price matches authorized price (if provided)
            # If no authorized price provided, treat as authorized (commerce response is trusted)
            if authorized_price_minor is not None:
                # Extract dollar amounts from draft and compare to authorized price
                # Authorized price in minor units (cents) -> dollars
                import re
                _price_re = re.compile(r"\$?\s*(\d+(?:\.\d{1,2})?)")
                _authorized_dollars = authorized_price_minor / 100.0
                # Find all price-like numbers in draft
                _found_prices: list[float] = []
                for m in _price_re.finditer(draft):
                    try:
                        _found_prices.append(float(m.group(1)))
                    except Exception:
                        continue
                # If draft contains a price, check if any matches authorized within tolerance
                if _found_prices:
                    # Allow if at least one price matches authorized (tolerance 0.005 as in deepseek_response)
                    _matches = any(abs(p - _authorized_dollars) <= 0.005 for p in _found_prices)
                    # If draft mentions price but none match authorized, treat as unauthorized -> keep flag
                    if _matches:
                        continue
                    # If draft mentions $ but no numeric match, still check keyword $ presence
                    # Unauthorized price -> keep flag (do not skip)
                else:
                    # Draft has keyword price/tip etc but no numeric price found; still may be generic mention
                    # For authorized commerce, generic price words without specific amount are allowed
                    # Check if draft contains authorized price string
                    if f"${_authorized_dollars:.2f}" in draft or f"${int(_authorized_dollars)}" in draft:
                        continue
                    # If no price amount at all, skip flag for authorized commerce
                    if "$" not in draft and "price" not in draft_lower:
                        continue
                    # Otherwise, if draft says "price" but authorized, allow (commerce response may say "price")
                    continue
            else:
                # No authorized price to compare, but draft is from authorized commerce engine -> allow
                continue
        if any(k in user_lower or k in draft_lower for k in keywords):
            flags.append(flag)

    scoring_failed = False
    try:
        # llama.cpp sole provider: generic JSON (no OneCall schema opt-in).
        provider = get_llm_provider()
        response_text = await provider.generate(
            system_instruction=SCORING_SYSTEM_PROMPT,
            user_content=f"User said: {user_message}\n\nDraft response: {draft}",
            response_mime_type="application/json",
            max_output_tokens=512,
            temperature=0.2,
        )
        scores = json.loads(response_text)
    except Exception:
        logger.warning("scoring: LLM scoring failed (all providers), using fail-closed defaults", exc_info=True)
        scores = {}
        scoring_failed = True

    llm_flags = scores.get("flags", [])
    flags.extend(llm_flags)

    if scoring_failed:
        # Fail-closed: scoring failure must never produce an approval-eligible score.
        # Return 0.0 so the draft is routed to operator queue, not auto-approved.
        composite = 0.0
    else:
        score_values = [
            scores.get("contextually_aware", 5),
            scores.get("natural_tone", 5),
            scores.get("appropriate_length", 5),
            scores.get("not_repetitive", 5),
        ]
        composite = sum(score_values) / (len(score_values) * 10)

    if any(f in flags for f in HARD_FLAGS):
        composite = min(composite, 0.1)

    return float(composite), flags
