import json
from typing import Any

from groq import AsyncGroq

from core.config import get_settings

_settings = get_settings()
_client = AsyncGroq(api_key=_settings.openai_api_key)

HARD_FLAGS = [
    "price_mention",
    "personal_info_request",
    "distress_signal",
    "explicit_request",
    "refund_complaint",
    "legal_mention",
    "competitor_mention",
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
) -> tuple[float, list[str]]:
    flags: list[str] = []

    user_lower = user_message.lower()
    draft_lower = draft.lower()

    for flag, keywords in FLAG_KEYWORDS.items():
        if any(k in user_lower or k in draft_lower for k in keywords):
            flags.append(flag)

    response = await _client.chat.completions.create(
        model=_settings.cheap_model,
        messages=[
            {"role": "system", "content": SCORING_SYSTEM_PROMPT},
            {"role": "user", "content": f"User said: {user_message}\n\nDraft response: {draft}"},
        ],
        response_format={"type": "json_object"},
        max_tokens=150,
    )

    try:
        scores = json.loads(response.choices[0].message.content)
    except (json.JSONDecodeError, ValueError):
        scores = {}

    llm_flags = scores.get("flags", [])
    flags.extend(llm_flags)

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
