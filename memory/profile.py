import json
from typing import Any

from groq import AsyncGroq

from core.config import get_settings
from db.postgres import get_user_profile, update_user_profile
from memory.retrieval import get_embedding

_settings = get_settings()
_client = AsyncGroq(api_key=_settings.openai_api_key)

PROFILE_SCHEMA: dict[str, Any] = {
    "name": None,
    "age": None,
    "location": None,
    "occupation": None,
    "relationship_status": None,
    "interests": [],
    "mentioned_topics": [],
    "communication_style": None,
    "emotional_state_recent": None,
    "important_dates": {},
    "preferences": [],
    "topics_to_avoid": [],
    "purchase_signals": [],
}

PROFILE_EXTRACTION_SYSTEM = """Extract facts about the fan from this conversation.
Return a JSON object. Only include fields where you found clear information.
Do not guess or infer. If nothing relevant, return {}.

Schema:
{
  "name": string or null,
  "age": string or null,
  "location": string or null,
  "occupation": string or null,
  "relationship_status": string or null,
  "interests": [list of strings],
  "communication_style": "casual" | "formal" | "playful" | null,
  "emotional_state_recent": string or null,
  "important_dates": {key: value},
  "topics_to_avoid": [list of strings],
  "purchase_signals": [list of strings]
}"""


async def extract_profile_facts(
    conversation_text: str,
) -> dict[str, Any]:
    response = await _client.chat.completions.create(
        model=_settings.cheap_model,
        messages=[
            {"role": "system", "content": PROFILE_EXTRACTION_SYSTEM},
            {"role": "user", "content": conversation_text},
        ],
        response_format={"type": "json_object"},
        max_tokens=400,
    )

    try:
        return json.loads(response.choices[0].message.content)
    except (json.JSONDecodeError, ValueError):
        return {}


def merge_profiles(
    existing: dict[str, Any],
    new: dict[str, Any],
) -> dict[str, Any]:
    merged: dict[str, Any] = {**PROFILE_SCHEMA, **existing}

    for key, value in new.items():
        if value is None:
            continue
        if isinstance(value, list):
            existing_list = merged.get(key, []) or []
            combined = list(set(existing_list + value))
            merged[key] = combined
        elif isinstance(value, dict):
            existing_dict = merged.get(key, {}) or {}
            merged[key] = {**existing_dict, **value}
        else:
            merged[key] = value

    return merged


async def extract_and_update_profile(user_id: int, recent_messages: list[dict[str, Any]]) -> None:
    current_profile = await get_user_profile(user_id)

    conversation_text = "\n".join(
        f"{'Fan' if m['direction'] == 'inbound' else 'You'}: {m['content']}"
        for m in recent_messages[-10:]
    )

    extracted = await extract_profile_facts(conversation_text)
    merged = merge_profiles(current_profile, extracted)
    await update_user_profile(user_id, merged)

    if extracted.get("name"):
        profile_text = json.dumps(merged)
        embedding = await get_embedding(profile_text)
        from db.postgres import upsert_user_embedding

        await upsert_user_embedding(user_id, embedding)
