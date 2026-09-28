import json
from typing import Any

from core.llm_provider import get_llm_provider
from db.postgres import mutate_user_profile_atomically

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
Return a JSON object with "facts" containing extracted fields, and a top-level
"confidence" object mapping each field to "explicit", "inferred", or "temporary".

Rules:
- "explicit" = fan stated it directly (e.g. "I love horror movies")
- "inferred" = you deduced it from context (e.g. fan repeatedly discusses horror)
- "temporary" = time-bound (e.g. "watching a game tonight")
- Do NOT guess or infer vague impressions. Only extract clear information.
- If nothing relevant, return {"facts": {}, "confidence": {}}

Schema:
{
  "facts": {
    "name": string or null,
    "age": string or null,
    "location": string or null,
    "occupation": string or null,
    "relationship_status": string or null,
    "interests": [list of strings],
    "mentioned_topics": [list of strings],
    "preferences": [list of strings],
    "communication_style": "casual" | "formal" | "playful" | null,
    "emotional_state_recent": string or null,
    "important_dates": {key: value},
    "topics_to_avoid": [list of strings],
    "purchase_signals": [list of strings]
  },
  "confidence": {
    "<field_name>": "explicit" | "inferred" | "temporary"
  }
}"""

# Maximum items per list field in the profile.
_PROFILE_LIST_CAP: int = 15


async def extract_profile_facts(
    conversation_text: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Extract profile facts and confidence from conversation text.

    Returns (facts, confidence_map) where confidence_map maps field names
    to one of "explicit", "inferred", "temporary".
    """
    provider = get_llm_provider()

    try:
        response_text = await provider.generate(
            system_instruction=PROFILE_EXTRACTION_SYSTEM,
            user_content=conversation_text,
            response_mime_type="application/json",
            max_output_tokens=400,
        )
    except Exception:
        return {}, {}

    try:
        parsed = json.loads(response_text)
        if isinstance(parsed, dict) and "facts" in parsed:
            return parsed["facts"], parsed.get("confidence", {})
        # Backward-compatible: if LLM returns flat dict without wrapper
        return parsed, {}
    except (json.JSONDecodeError, ValueError):
        return {}, {}


def merge_profiles(
    existing: dict[str, Any],
    new: dict[str, Any],
    confidence_map: dict[str, str] | None = None,
    *,
    additive_only: bool = False,
) -> dict[str, Any]:
    """Merge new profile facts into existing profile.

    - Lists are unioned and capped at _PROFILE_LIST_CAP.
    - Dicts are shallow-merged (new keys win), unless ``additive_only``.
    - Scalars overwrite (latest value wins), unless ``additive_only``.
    - Confidence map is stored under _confidence key (only for applied keys
      in ``additive_only`` mode).

    M5: ``additive_only`` is the stale-source mode — an older computation may
    still contribute genuinely new items/keys, but must never replace newer
    scalar values or existing dict entries.
    """
    merged: dict[str, Any] = {**PROFILE_SCHEMA, **existing}
    applied: set[str] = set()

    # Leak hardening: stored values render back into generation prompts
    # verbatim, so instruction-like or degenerate content is dropped here
    # instead of persisting as trusted context.
    try:
        from core.text_sanitize import sanitize_stored_text as _sanitize
    except Exception:
        _sanitize = None  # type: ignore[assignment]

    def _clean(value: Any) -> Any:
        try:
            if isinstance(value, list):
                cleaned = []
                for item in value:
                    if isinstance(item, str):
                        text = _sanitize(item) if _sanitize else item.strip()
                        if text:
                            cleaned.append(text)
                    elif item is not None:
                        cleaned.append(item)
                return cleaned
            if isinstance(value, dict):
                return {
                    k: (_sanitize(v) if isinstance(v, str) and _sanitize else v)
                    for k, v in value.items()
                }
            if isinstance(value, str):
                return _sanitize(value) if _sanitize else value.strip()
            return value
        except Exception:
            return value

    for key, value in new.items():
        if value is None:
            continue
        value = _clean(value)
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, list):
            existing_list = merged.get(key, []) or []
            # Deduplicate while preserving order, then cap
            seen: set[str] = set()
            deduped: list[str] = []
            for item in existing_list + value:
                item_str = str(item)
                if item_str not in seen:
                    seen.add(item_str)
                    deduped.append(item)
            if (not additive_only) or len(deduped) > len(existing_list):
                merged[key] = deduped[:_PROFILE_LIST_CAP]
                applied.add(key)
        elif isinstance(value, dict):
            existing_dict = merged.get(key, {}) or {}
            if additive_only:
                # Only add absent keys; never overwrite existing entries.
                added = False
                for dict_key, dict_value in value.items():
                    if dict_key not in existing_dict:
                        existing_dict[dict_key] = dict_value
                        added = True
                merged[key] = existing_dict
                if added:
                    applied.add(key)
            else:
                merged[key] = {**existing_dict, **value}
                applied.add(key)
        else:
            if additive_only:
                # Stale source must not replace a newer scalar.
                continue
            merged[key] = value
            applied.add(key)

    # Store confidence metadata
    if confidence_map:
        existing_conf = merged.get("_confidence", {}) or {}
        if additive_only:
            for conf_key in applied:
                if conf_key in confidence_map:
                    existing_conf[conf_key] = confidence_map[conf_key]
            merged["_confidence"] = existing_conf
        else:
            merged["_confidence"] = {**existing_conf, **confidence_map}

    return merged


# M5: per-creator source high-water for flat profile replacements, stored
# inside facts (no migration). The key ends with "_by_creator" so the legacy
# prompt renderer skips it, and it is namespaced per creator so creator A's
# writes can never block creator B's. Value is the max messages.id over the
# extraction window that produced the committed scalars.
_PROFILE_SOURCE_KEY = "_profile_source_by_creator"


def _coerce_source_order(value: Any) -> int | None:
    """Normalize a source order to int, or None when unknown/invalid."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


async def extract_and_update_profile(
    user_id: int,
    recent_messages: list[dict[str, Any]],
    source_order: int | None = None,
    creator_id: int | None = None,
) -> None:
    """Extract flat profile facts from recent messages and persist them.

    M5 freshness: when both ``source_order`` (max ``messages.id`` over the
    extraction window) and ``creator_id`` are provided, scalar replacement is
    applied only if this source is at least as new as the source that
    produced the currently stored scalars (compare-and-set inside the atomic
    mutation). An older source still contributes additive merges (new list
    items / new dict keys) but can never overwrite newer scalars, and never
    advances the high-water. When either is None, legacy behavior applies
    (full merge, no high-water tracking) for backward compatibility.
    """
    conversation_text = "\n".join(
        f"{'Fan' if m['direction'] == 'inbound' else 'You'}: {m['content']}"
        for m in recent_messages[-10:]
    )

    extracted, confidence_map = await extract_profile_facts(conversation_text)
    if not extracted:
        return

    order = _coerce_source_order(source_order)
    gated = order is not None and creator_id is not None
    creator_key = str(creator_id) if creator_id is not None else None

    # M4 D6: merge under SELECT ... FOR UPDATE so this whole-facts write
    # cannot clobber a concurrent creator-namespaced write (e.g. another
    # creator's fan_knowledge_by_creator entry for the same user row).
    # Extraction I/O stays outside the row lock; only the merge persists.
    # M5: the freshness comparison and high-water update execute inside the
    # same locked mutation (no await between check and write).
    def _merge(facts: dict[str, Any]) -> bool:
        if not gated or creator_key is None:
            merged = merge_profiles(dict(facts), extracted, confidence_map)
            facts.clear()
            facts.update(merged)
            return True
        high_waters = facts.get(_PROFILE_SOURCE_KEY, {})
        if not isinstance(high_waters, dict):
            high_waters = {}
        stored = _coerce_source_order(high_waters.get(creator_key))
        if stored is not None and order < stored:
            # Stale source: additive-only merge, high-water untouched.
            merged = merge_profiles(
                dict(facts), extracted, confidence_map, additive_only=True
            )
            facts.clear()
            facts.update(merged)
            return True
        merged = merge_profiles(dict(facts), extracted, confidence_map)
        facts.clear()
        facts.update(merged)
        high_waters[creator_key] = order
        facts[_PROFILE_SOURCE_KEY] = high_waters
        return True

    await mutate_user_profile_atomically(user_id, _merge)

    # NOTE: provider-based profile embedding write removed by architecture
    # decision. llama.cpp provides no embeddings endpoint (returns 501), the
    # profile embedding column is not read by the active generation path, and
    # active semantic retrieval uses local MiniLM. Facts extraction +
    # persistence above is the complete profile contract. No embedding
    # replacement is introduced here and no DB migration is performed.
