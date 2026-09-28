"""Sunny self-knowledge — authoritative creator-side persona facts (C.1-F Phase 5).

Facts come from an explicit authoritative source: the `personas` instruction
itself (parsed for allowed self-facts) + a small hard-coded safe list.
No external events are invented. Commerce is never invented.

If a fact is not in the authoritative source, it is not emitted.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Authoritative safe self-facts for Sunny — curated, not invented per fan.
# These are generic interests Sunny enjoys talking about, not real-world
# events or location claims. They give "what are you upto?" a grounded
# answer without fabricating a specific activity.
# ---------------------------------------------------------------------------

_SUNNY_SELF_FACTS = (
    "enjoys cozy movie nights, trying new cafes, late-night chats, music, and playful teasing",
    "loves getting to know people — asking about their day, work, and what makes them smile",
    "keeps things light and warm, a little flirty when the vibe is right",
)

# quick mapping from persona.name -> self-facts
PERSONA_SELF_FACTS: dict[str, tuple[str, ...]] = {
    "sunny skye": _SUNNY_SELF_FACTS,
    "sunny - sales": _SUNNY_SELF_FACTS,
    "sunny": _SUNNY_SELF_FACTS,
}


def get_persona_self_facts(persona_name: str | None) -> tuple[str, ...]:
    if not persona_name:
        # Legacy fallback for direct callers / tests — but render path guards against universal injection
        return _SUNNY_SELF_FACTS
    key = persona_name.strip().lower()
    # Return sunny only for known sunny aliases; otherwise empty (no leak for Mia etc)
    return PERSONA_SELF_FACTS.get(key, ())


def render_persona_self_block(persona_name: str | None) -> str:
    # Universal injection guard: when persona_name is None/empty, do NOT emit ABOUT SUNNY
    # This prevents cross-creator contamination; Sunny is emitted only when persona is explicitly Sunny
    if not persona_name:
        return ""
    facts = get_persona_self_facts(persona_name)
    if not facts:
        return ""
    return "ABOUT SUNNY: " + "; ".join(facts)
