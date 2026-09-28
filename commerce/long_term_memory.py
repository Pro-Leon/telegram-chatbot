"""Long-Term Conversational Memory — deterministic, creator-scoped, confidence-aware (Phase 15)."""
from __future__ import annotations
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("commerce.long_term_memory")

# Memory types
FACT = "fact"
PREFERENCE = "preference"
DISLIKE = "dislike"
PLAN = "plan"
COMMITMENT = "commitment"
OPEN_LOOP = "open_loop"
TOPIC = "topic"
RELATIONSHIP_EVENT = "relationship_event"
PURCHASE_EVENT = "purchase_event"
COMMERCIAL_EVENT = "commercial_event"

# Provenance
EXPLICIT = 1.0
STRONG_INFERENCE = 0.8
WEAK_INFERENCE = 0.5
SYSTEM_EVENT = 0.9

# Decay policies (days)
DECAY_SLOW = 90  # hard fact, purchase
DECAY_MEDIUM = 30  # preference
DECAY_FAST = 7  # topic, plan, open_loop
DECAY_SHORT = 3  # temporary mood

def _now() -> datetime:
    return datetime.now(timezone.utc)

def create_memory_item(
    *,
    creator_id: int,
    user_id: int,
    memory_type: str,
    subject: str,
    value: str,
    confidence: float,
    source: str,
    importance: float = 0.5,
) -> dict[str, Any]:
    return {
        "memory_id": f"{creator_id}:{user_id}:{subject}:{value[:20]}",
        "creator_id": creator_id,
        "user_id": user_id,
        "memory_type": memory_type,
        "subject": subject,
        "value": value,
        "confidence": confidence,
        "source": source,
        "first_seen": _now().isoformat(),
        "last_seen": _now().isoformat(),
        "observation_count": 1,
        "importance": importance,
        "expires_at": None,
    }

async def get_long_term_memory(creator_id: int, user_id: int, profile: dict | None = None) -> list[dict[str, Any]]:
    """Get all long-term memories for creator+user.

    If *profile* is provided it is used directly instead of re-fetching
    from the database — generation-local reuse to avoid redundant PG round-trips.
    """
    try:
        if profile is None:
            from db.postgres import get_user_profile
            profile = await get_user_profile(user_id)
        by_creator = profile.get("long_term_memory_by_creator", {})
        return list(by_creator.get(str(creator_id), []) or by_creator.get(creator_id, []) or [])
    except Exception:
        return []

async def add_memory_item(creator_id: int, user_id: int, item: dict[str, Any]) -> None:
    """Add or update a memory item, handling conflicts deterministically.

    M4 D6: the read-modify-write runs under ``SELECT ... FOR UPDATE`` so a
    concurrent writer for another creator's namespace cannot be clobbered.
    Only this creator's key is touched.
    """
    # Leak hardening: memory values render into prompts verbatim —
    # instruction-like content is dropped instead of persisting.
    try:
        from core.text_sanitize import sanitize_stored_text as _sanitize

        if isinstance(item, dict):
            cleaned = _sanitize(item.get("value", ""))
            if not cleaned:
                logger.debug("add_memory_item dropped unsafe value (fail-closed)")
                return
            item = {**item, "value": cleaned}
    except Exception:
        logger.debug("add_memory_item sanitize failed (fail-open)", exc_info=True)
    try:
        from db.postgres import mutate_user_profile_atomically

        def _mutate(facts: dict[str, Any]) -> bool:
            by_creator = facts.get("long_term_memory_by_creator", {})
            # Ensure str keys
            key = str(creator_id)
            existing = by_creator.get(key, [])
            # Check for conflict: same subject
            found_idx = None
            for idx, mem in enumerate(existing):
                if mem.get("subject") == item["subject"] and mem.get("memory_type") == item["memory_type"]:
                    found_idx = idx
                    break
            if found_idx is not None:
                old = existing[found_idx]
                # Conflict resolution: explicit > strong > weak, newer > older, repeated > isolated
                if item["confidence"] > old["confidence"]:
                    # New explicit overrides old weak
                    existing[found_idx] = item
                elif item["confidence"] == old["confidence"]:
                    # Newer wins, increment count
                    if item["confidence"] == EXPLICIT:
                        existing[found_idx] = item
                    else:
                        # For non-explicit, keep old but update last_seen and count
                        old["last_seen"] = item["last_seen"]
                        old["observation_count"] = old.get("observation_count", 1) + 1
                        old["confidence"] = min(1.0, old["confidence"] + 0.05)
                else:
                    # Old explicit beats new weak, keep old but update last_seen
                    old["last_seen"] = item["last_seen"]
            else:
                existing.append(item)
                # Bound to 20 items, keep most recent
                if len(existing) > 20:
                    existing = sorted(existing, key=lambda x: x["last_seen"], reverse=True)[:20]
            by_creator[key] = existing
            facts["long_term_memory_by_creator"] = by_creator
            return True

        await mutate_user_profile_atomically(user_id, _mutate)
    except Exception:
        logger.warning("add_memory_item failed", exc_info=True)

def is_memory_expired(item: dict[str, Any], now: datetime | None = None) -> bool:
    """Check if memory has expired based on type and decay."""
    now = now or _now()
    # Check for RESOLVED/EXPIRED status first
    if item.get("status") in ("RESOLVED", "EXPIRED", "CANCELLED"):
        return True
    try:
        last_seen = datetime.fromisoformat(item["last_seen"].replace("Z", "+00:00"))
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        days = (now - last_seen).total_seconds() / 86400
        decay_days = {
            FACT: DECAY_SLOW,
            PREFERENCE: DECAY_MEDIUM,
            DISLIKE: DECAY_MEDIUM,
            PLAN: DECAY_FAST,
            COMMITMENT: DECAY_FAST,
            OPEN_LOOP: DECAY_FAST,
            TOPIC: DECAY_FAST,
            PURCHASE_EVENT: DECAY_SLOW,
            COMMERCIAL_EVENT: DECAY_SLOW,
        }.get(item.get("memory_type", PREFERENCE), DECAY_MEDIUM)
        # Exponential decay: confidence * exp(-days/decay_days)
        import math
        decayed = item.get("confidence", 0.5) * math.exp(-days / decay_days)
        return decayed < 0.2
    except Exception:
        return False


async def resolve_open_loop(creator_id: int, user_id: int, current_message: str) -> bool:
    """Mark relevant OPEN_LOOP as RESOLVED if current message indicates completion.

    Checks if current_message contains `went great`/`went well`/`interview` and
    closes the matching OPEN_LOOP (e.g., interview Friday). Returns True if resolved.
    """
    try:
        low = current_message.lower()
        # Simple heuristic: if message indicates completion of a previous open loop
        if any(phrase in low for phrase in ["went great", "went well", "went good", "interview", "exam", "trip"]):
            memories = await get_long_term_memory(creator_id, user_id)
            for mem in memories:
                if mem.get("memory_type") == OPEN_LOOP and mem.get("status", "OPEN") == "OPEN":
                    # Check if subject matches current message tokens
                    import re
                    subj_tokens = set(re.compile(r"[a-z0-9]+").findall(mem.get("subject", "").lower()))
                    msg_tokens = set(re.compile(r"[a-z0-9]+").findall(low))
                    if subj_tokens & msg_tokens:
                        mem["status"] = "RESOLVED"
                        mem["last_seen"] = _now().isoformat()
                        # Persist updated memory (M4 D6: under row lock so a
                        # concurrent creator-namespaced write is preserved).
                        from db.postgres import mutate_user_profile_atomically

                        def _resolve(facts: dict[str, Any]) -> bool:
                            by_creator = facts.get("long_term_memory_by_creator", {})
                            key = str(creator_id)
                            # Update the specific memory in the list
                            for idx, m in enumerate(by_creator.get(key, [])):
                                if m.get("memory_id") == mem.get("memory_id"):
                                    by_creator[key][idx] = mem
                                    break
                            facts["long_term_memory_by_creator"] = by_creator
                            return True

                        await mutate_user_profile_atomically(user_id, _resolve)
                        return True
    except Exception:
        pass
    return False

async def retrieve_relevant_memories(
    creator_id: int,
    user_id: int,
    current_topic: str | None = None,
    open_threads: tuple[str, ...] = (),
    limit: int = 5,
    profile: dict | None = None,
) -> list[dict[str, Any]]:
    """Retrieve only relevant memories for current turn, relevance-ranked.

    If *profile* is provided it is passed to get_long_term_memory to avoid
    a redundant PG round-trip.
    """
    try:
        memories = await get_long_term_memory(creator_id, user_id, profile=profile)
        if not memories:
            return []
        # Filter expired
        now = _now()
        memories = [m for m in memories if not is_memory_expired(m, now)]
        # Score relevance: current_topic + open_threads tokens overlap with subject/value
        import re
        def tokens(s: str) -> set[str]:
            return set(re.compile(r"[a-z0-9]+").findall(s.lower()))
        current_tokens = set()
        if current_topic:
            current_tokens.update(tokens(current_topic))
        for t in open_threads[:3]:
            current_tokens.update(tokens(t))
        scored = []
        for m in memories:
            subj_tokens = tokens(m.get("subject", "") + " " + m.get("value", ""))
            overlap = len(subj_tokens & current_tokens) if current_tokens else 0
            # Base relevance: overlap + confidence + recency
            try:
                last_seen = datetime.fromisoformat(m["last_seen"].replace("Z", "+00:00"))
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=timezone.utc)
                days = (now - last_seen).total_seconds() / 86400
                recency = max(0, 1 - days/30)
            except Exception:
                recency = 0.5
            score = overlap * 0.5 + m.get("confidence", 0.5) * 0.3 + recency * 0.2 + m.get("importance", 0.5) * 0.1
            # Boost for open loops and commitments when relevant
            if m.get("memory_type") in (OPEN_LOOP, COMMITMENT) and overlap > 0:
                score += 0.3
            scored.append((m, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        # Only return those with some relevance or high importance
        result = [m for m, s in scored[:limit] if s > 0.2]
        return result
    except Exception:
        return []

def extract_explicit_memories(text: str, creator_id: int, user_id: int) -> list[dict[str, Any]]:
    """Deterministic extraction of explicit memories from text (no LLM)."""
    import re
    memories = []
    low = text.lower()
    # Explicit color preference
    m = re.search(r"(?:my favorite color is|i love (?:the color )?|i really like) (red|black|blue|green|pink|white|purple)", low)
    if m:
        color = m.group(1)
        memories.append(create_memory_item(creator_id=creator_id, user_id=user_id, memory_type=PREFERENCE, subject="favorite_color", value=color, confidence=EXPLICIT, source="explicit", importance=0.7))
    # Explicit dislike
    m2 = re.search(r"(?:i (?:don.t|do not) like|i hate) (?:being called )?(\w+)", low)
    if m2:
        dislike = m2.group(1)
        if dislike not in ["red", "black"]:  # avoid overlapping with preference
            memories.append(create_memory_item(creator_id=creator_id, user_id=user_id, memory_type=DISLIKE, subject="dislike", value=dislike, confidence=EXPLICIT, source="explicit", importance=0.6))
    # Plan/commitment: "I told you I was going to X", "I have a big exam Friday", "I will come back"
    if any(phrase in low for phrase in ["going to miami", "exam friday", "interview friday", "come back", "after payday"]):
        # Determine type
        if "interview" in low or "exam" in low:
            memories.append(create_memory_item(creator_id=creator_id, user_id=user_id, memory_type=OPEN_LOOP, subject="interview" if "interview" in low else "exam", value=text[:50], confidence=EXPLICIT, source="explicit", importance=0.8))
        elif "miami" in low:
            memories.append(create_memory_item(creator_id=creator_id, user_id=user_id, memory_type=PLAN, subject="trip", value="miami", confidence=EXPLICIT, source="explicit", importance=0.7))
        elif "come back" in low or "after payday" in low:
            memories.append(create_memory_item(creator_id=creator_id, user_id=user_id, memory_type=COMMITMENT, subject="return", value=text[:50], confidence=EXPLICIT, source="explicit", importance=0.6))
    # Open loop: movie, etc.
    if "movie" in low and "watching" in low:
        memories.append(create_memory_item(creator_id=creator_id, user_id=user_id, memory_type=TOPIC, subject="movie", value=text[:50], confidence=WEAK_INFERENCE, source="weak", importance=0.4))
    return memories
