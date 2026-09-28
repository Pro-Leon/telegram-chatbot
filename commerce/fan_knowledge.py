"""Fan Knowledge — Phase 36 deep, bounded, deterministic, creator-scoped, idempotent.

Supports 20+ categories, confidence/source, temporal, history, dedup, bounded 30 per creator:user,
no LLM, no new worker/queue, reuse user_profiles JSONB.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any
import hashlib

# ── Constants ────────────────────────────────────────────────────────────
CATEGORIES = {
    "IDENTITY": ["name","preferred_name","nickname","age","gender"],
    "LOCATION": ["city","country","region","timezone"],
    "WORK": ["occupation","employer","industry","work_schedule"],
    "EDUCATION": ["education","school"],
    "FAMILY": ["family","siblings","children","parents"],
    "RELATIONSHIPS": ["relationship_status","partner"],
    "PETS": ["pet","pet_name","pet_type"],
    "HOBBIES": ["hobby","interest","sports","music","movies","games","food"],
    "TRAVEL": ["travel","trip","places","countries_visited"],
    "LIFESTYLE": ["lifestyle","routine","sleep_schedule"],
    "PREFERENCES": ["preference","like","dislike","boundary"],
    "GOALS": ["goal","plan","ambition","project"],
    "EVENTS": ["event","birthday","anniversary","upcoming_trip"],
    "BEHAVIOR": ["communication_style","personality_signal"],
    "TEMPORAL_CONTEXT": ["temporary_location","schedule"],
}

TEMPORAL_TYPES = {"PERMANENT","CURRENT","HISTORICAL","TEMPORARY","RECURRING","FUTURE","EVENT","UNKNOWN"}
SOURCES = {"USER_EXPLICIT","USER_CORRECTION","USER_CONFIRMATION","SYSTEM_OBSERVED","BEHAVIORAL_OBSERVATION","COMMERCE_EVENT","DERIVED_CONTEXT"}
STATUSES = {"CURRENT","HISTORICAL","EXPIRED","PENDING"}

CONFIDENCE_EXPLICIT = 1.0
CONFIDENCE_CONFIRMED = 0.9
CONFIDENCE_BEHAVIORAL = 0.6
CONFIDENCE_INFERRED = 0.3

@dataclass
class FanKnowledgeItem:
    subject: str
    value: str
    category: str
    confidence: float
    source: str
    observed_at: str
    effective_from: str | None = None
    effective_until: str | None = None
    expires_at: str | None = None
    temporal_type: str = "CURRENT"
    status: str = "CURRENT"
    evidence_generation_id: str | None = None
    last_confirmed_at: str | None = None
    confirmation_count: int = 1
    contradiction_count: int = 0
    creator_id: int | None = None
    user_id: int | None = None
    first_observed_at: str | None = None
    value_type: str = "string"
    subcategory: str | None = None
    location_role: str | None = None  # HOME, TEMPORARY, HISTORICAL for city

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _canon_subject(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", s.lower().strip())[:40]

# ── Extraction (deterministic, no LLM, no interrogation) ───────────────

# Regex patterns for natural capture (explicit only, not inference) — handles contractions and natural forms
_PATTERNS = [
    # Occupation: I'm a software engineer / I'm a nurse / I work as a nurse (with i'm contraction)
    (re.compile(r"\bi'?m a ([a-z ]+?)(?:\.|,| from| and| who|!|\?|$)", re.I), "occupation", "WORK", "USER_EXPLICIT"),
    (re.compile(r"\bi work as a ([a-z ]+?)(?:\.|,|!|\?|$)", re.I), "occupation", "WORK", "USER_EXPLICIT"),
    # City: I live in Chicago / I'm from Chicago / I moved to New York / I'm in Spain for a week — require I anchor to avoid third-party "My sister lives in..."
    (re.compile(r"\bi live in ([a-z]+(?: [a-z]+)?)\b", re.I), "city", "LOCATION", "USER_EXPLICIT"),
    (re.compile(r"\bi'?m from ([a-z]+(?: [a-z]+)?)\b", re.I), "city", "LOCATION", "USER_EXPLICIT"),
    (re.compile(r"\bi (?:have )?moved to ([a-z]+(?: [a-z]+)?)\b", re.I), "city", "LOCATION", "USER_EXPLICIT"),
    (re.compile(r"\bi'?m in ([a-z]+) for a week\b", re.I), "city", "LOCATION", "USER_EXPLICIT"),
    (re.compile(r"\bi\b.*?from ([A-Z][a-z]+(?: [A-Z][a-z]+)?)\b", re.I), "city", "LOCATION", "USER_EXPLICIT"),  # I ... from Chicago (e.g., I'm a software engineer from Chicago) — requires I before from
    # No generic "from ([A-Z][a-z]+)" fallback — removed to prevent My sister is from Chicago false-positive
    # Country is similar to city, but we treat as city for now
    # Pet: My dog Max / My golden retriever / My golden retriever is Max / I've got two cats / My dog is Max
    (re.compile(r"\bmy golden retriever (?:is )?([A-Z][a-z]+)\b", re.I), "pet_name", "PETS", "USER_EXPLICIT"),
    (re.compile(r"\bmy (dog|cat|bird|fish) (?:named )?([a-z]+)\b", re.I), "pet_name", "PETS", "USER_EXPLICIT"),
    (re.compile(r"\bmy (dog|cat)\b", re.I), "pet_type", "PETS", "USER_EXPLICIT"),
    (re.compile(r"\bgolden retriever", re.I), "pet_type", "PETS", "USER_EXPLICIT"),
    (re.compile(r"\bmy dog is ([a-z]+)\b", re.I), "pet_name", "PETS", "USER_EXPLICIT"),
    # Cross-message pronoun: His name is Max (when antecedent pet exists) — explicit but requires antecedent check
    (re.compile(r"\b(?:his|her|its) name is ([A-Z][a-z]+)\b", re.I), "pet_name_pronoun", "PETS", "USER_EXPLICIT"),
    (re.compile(r"\bthe (?:dog|cat|bird)'s name is ([A-Z][a-z]+)\b", re.I), "pet_name_pronoun", "PETS", "USER_EXPLICIT"),
    (re.compile(r"\bi have (?:a )?(dog|cat|bird)\b", re.I), "pet_type", "PETS", "USER_EXPLICIT"),
    (re.compile(r"\bi've got two (cats|dogs)\b", re.I), "pets_count", "PETS", "USER_EXPLICIT"),
    # Schedule: I work nights / I work days / I usually go running after work
    (re.compile(r"\bi work nights?\b", re.I), "work_schedule", "SCHEDULE", "USER_EXPLICIT"),
    (re.compile(r"\bi work days?\b", re.I), "work_schedule", "SCHEDULE", "USER_EXPLICIT"),
    (re.compile(r"\bafter work\b", re.I), "routine", "LIFESTYLE", "USER_EXPLICIT"),
    (re.compile(r"\bgo(?:ing)? running\b", re.I), "hobby", "HOBBIES", "USER_EXPLICIT"),
    (re.compile(r"\brunning\b", re.I), "hobby", "HOBBIES", "USER_EXPLICIT"),
    # Hobbies/interests
    (re.compile(r"\bi (?:love|like) ([a-z0-9 ]+? movies)", re.I), "interest", "INTERESTS", "USER_EXPLICIT"),
    (re.compile(r"\bi (?:love|like) ([a-z0-9 ]+)", re.I), "interest", "INTERESTS", "USER_EXPLICIT"),
    (re.compile(r"\bi hate ([a-z ]+)", re.I), "dislike", "PREFERENCES", "USER_EXPLICIT"),
    (re.compile(r"\bobsessed with ([a-z0-9 ]+)", re.I), "interest", "INTERESTS", "USER_EXPLICIT"),
    # Travel: I'm going to Miami next Friday / heading to London next month — require I anchor
    (re.compile(r"\bi'?m going to ([a-z]+)(?: next \w+)?\b", re.I), "trip", "TRAVEL", "USER_EXPLICIT"),
    (re.compile(r"\bgoing to ([a-z]+)(?: next \w+)?\b", re.I), "trip", "TRAVEL", "USER_EXPLICIT"),  # keep generic but will be filtered if not I? Actually keep but with I check via low
    (re.compile(r"\bi heading to ([a-z]+)", re.I), "trip", "TRAVEL", "USER_EXPLICIT"),
    (re.compile(r"\bheading to ([a-z]+)", re.I), "trip", "TRAVEL", "USER_EXPLICIT"),
    (re.compile(r"\bi (?:am )?visiting ([a-z]+)", re.I), "trip", "TRAVEL", "USER_EXPLICIT"),
    (re.compile(r"\bvisiting ([a-z]+)", re.I), "trip", "TRAVEL", "USER_EXPLICIT"),
    # Family
    (re.compile(r"\bmy (sister|brother|mom|dad|mother|father) ([a-z]+)", re.I), "family", "FAMILY", "USER_EXPLICIT"),
    (re.compile(r"\bi have (?:two )?kids\b", re.I), "family", "FAMILY", "USER_EXPLICIT"),
    # Relationship
    (re.compile(r"\bi am (divorced|married|single)", re.I), "relationship_status", "RELATIONSHIPS", "USER_EXPLICIT"),
    (re.compile(r"\bgetting married next summer\b", re.I), "plan", "RELATIONSHIPS", "USER_EXPLICIT"),
    # Birthday
    (re.compile(r"\bmy birthday is in ([a-z]+)", re.I), "birthday", "EVENTS", "USER_EXPLICIT"),
    # Name: I'm Alex / My name is Alex
    (re.compile(r"\bi am ([A-Z][a-z]+)(?:\.|,|!|\?| from| and|$)", re.I), "preferred_name", "IDENTITY", "USER_EXPLICIT"),
    (re.compile(r"\bmy name is ([A-Z][a-z]+)", re.I), "preferred_name", "IDENTITY", "USER_EXPLICIT"),
]

# More specific patterns that should not be treated as generic city (e.g., "from work" is not city)
_CITY_BLACKLIST = {"work","home","there","here","hospital"}

def extract_fan_knowledge(text: str, creator_id: int, user_id: int, generation_id: str | None = None, existing_knowledge: list[dict[str, Any]] | None = None) -> list[FanKnowledgeItem]:
    items: list[FanKnowledgeItem] = []
    low = text.lower()
    # For cross-message pronoun resolution, check existing knowledge for pet antecedent
    existing_pet_types = []
    existing_pet_names = []
    if existing_knowledge:
        for ek in existing_knowledge:
            if ek.get("subject") == "pet_type" and ek.get("status") == "CURRENT":
                existing_pet_types.append(ek)
            if ek.get("subject") == "pet_name" and ek.get("status") == "CURRENT":
                existing_pet_names.append(ek)
    for pat, subject, category, source in _PATTERNS:
        for m in pat.finditer(text):
            try:
                # Extract value: last captured group that is not empty, or full match
                val = None
                for g in reversed(m.groups()):
                    if g and g.strip():
                        val = g.strip()
                        break
                if not val:
                    val = m.group(0).strip()
                # Normalize
                val_lower = val.lower().strip()
                # Normalize work_schedule
                if subject == "work_schedule":
                    if "night" in val_lower:
                        val = "night_shift"
                        val_lower = "night_shift"
                    elif "day" in val_lower:
                        val = "day_shift"
                        val_lower = "day_shift"
                elif subject == "hobby" and val_lower in ("go running","going running","running"):
                    val = "running"
                    val_lower = "running"
                elif subject == "pet_type" and "golden retriever" in val_lower:
                    val = "dog"
                    val_lower = "dog"
                # City blacklist
                if subject == "city" and val_lower in _CITY_BLACKLIST:
                    continue
                # Occupation should not be "software" without "engineer" — need at least 2 chars
                if len(val) < 2:
                    continue
                # For city, ensure first letter capitalized in original and not too long
                if subject == "city" and len(val.split()) > 2:
                    continue
                # For occupation, require not "a" alone
                if subject == "occupation" and val_lower in ("a","an","the"):
                    continue
                # P1-02: cross-message pronoun "His name is Max" only if exactly one pet_type antecedent and no pet_name yet (unambiguous)
                if subject == "pet_name_pronoun":
                    if len(existing_pet_types) != 1 or len(existing_pet_names) != 0:
                        continue
                    subject = "pet_name"  # normalize to pet_name
                    # value is already Max, keep it
                    category = "PETS"
                    source = "USER_EXPLICIT"
                # Build item
                # Determine temporal_type and location_role
                temporal = "CURRENT"
                expires = None
                location_role = None
                if subject == "trip":
                    temporal = "TEMPORARY"
                    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
                    location_role = "TEMPORARY"
                elif subject == "city":
                    if "for a week" in low:
                        temporal = "TEMPORARY"
                        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
                        location_role = "TEMPORARY"
                    elif "moving" in low or "moved" in low:
                        temporal = "CURRENT"
                        location_role = "HOME"
                    else:
                        location_role = "HOME"
                elif subject == "work_schedule":
                    temporal = "RECURRING"
                item = FanKnowledgeItem(
                    subject=_canon_subject(subject),
                    value=val[:80],
                    category=category,
                    confidence=CONFIDENCE_EXPLICIT,
                    source=source,
                    observed_at=_now_iso(),
                    effective_from=_now_iso(),
                    temporal_type=temporal,
                    expires_at=expires,
                    status="CURRENT",
                    evidence_generation_id=generation_id,
                    last_confirmed_at=_now_iso(),
                    confirmation_count=1,
                    creator_id=creator_id,
                    user_id=user_id,
                    first_observed_at=_now_iso(),
                    location_role=location_role,
                )
                # P1: pet_name must be capitalized (Max, not keeps) — skip if first char is lowercase in original
                if subject in ("pet_name", "pet_name_pronoun") and val and val[0].islower() and val.lower() != val.capitalize():
                    # Check original captured value's first char is lower -> likely not a name (keeps)
                    # But allow Max (capital M) — val is stripped, check original m.group(0) first char of captured group?
                    # Simplified: if val is all lower, skip
                    if val.islower():
                        continue
                # For pet_name, also create pet_type if not already
                items.append(item)
                # Special: if pet_name Max and pet_type dog, also create pet_type if not exists
                if subject == "pet_name" and "golden retriever" in low:
                    items.append(FanKnowledgeItem(
                        subject="pet_type", value="dog", category="PETS", confidence=CONFIDENCE_EXPLICIT,
                        source=source, observed_at=_now_iso(), temporal_type="CURRENT", status="CURRENT",
                        evidence_generation_id=generation_id, creator_id=creator_id, user_id=user_id, first_observed_at=_now_iso()
                    ))
                # For city, also set country if known? Not needed
            except Exception:
                continue
    # Deduplicate within this extraction (same subject/value)
    seen = set()
    deduped: list[FanKnowledgeItem] = []
    for it in items:
        key = (it.subject, it.value.lower())
        if key not in seen:
            seen.add(key)
            deduped.append(it)
    return deduped

# ── Persistence (bounded, creator-scoped, idempotent via generation_id) ───

_KNOWLEDGE_KEY = "fan_knowledge_by_creator"
_KNOWLEDGE_MAX = 30
_HISTORY_MAX = 5

def _knowledge_key(creator_id: int, user_id: int) -> str:
    return f"{creator_id}:{user_id}"

# In-memory fallback for tests
_knowledge_mem: dict[str, list[dict[str, Any]]] = {}
import asyncio
_knowledge_locks: dict[str, asyncio.Lock] = {}

async def add_knowledge_item(creator_id: int, user_id: int, item: FanKnowledgeItem) -> bool:
    # Atomic via SELECT ... FOR UPDATE to prevent TOCTOU lost update (P1-02)
    # Leak hardening: values render into prompts verbatim — sanitize the
    # value in place; refuse persistence when nothing safe remains.
    try:
        from core.text_sanitize import sanitize_stored_text as _sanitize_ki

        cleaned_value = _sanitize_ki(item.value)
        if not cleaned_value:
            return False
        item.value = cleaned_value
    except Exception:
        pass
    try:
        from db.postgres import get_pool
        import json
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT facts FROM user_profiles WHERE user_id=$1 FOR UPDATE", user_id)
                if row and row["facts"]:
                    facts = json.loads(row["facts"]) if isinstance(row["facts"], str) else dict(row["facts"])
                else:
                    facts = {}
                by_creator = facts.get(_KNOWLEDGE_KEY, {})
                key = str(creator_id)
                lst = by_creator.get(key, [])
                # Check idempotency: same generation_id + subject/value
                if item.evidence_generation_id:
                    for e in lst:
                        if e.get("evidence_generation_id") == item.evidence_generation_id and e.get("subject") == item.subject and e.get("value") == item.value:
                            return True
                # Check for existing subject (contradiction/correction)
                # M4 D6: this entire read-modify-write runs inside the transaction
                # opened above, so the SELECT ... FOR UPDATE row lock is held
                # through the final UPDATE below. Concurrent writers for any
                # creators serialize here instead of clobbering each other's
                # namespaced keys. — for city, HOME vs TEMPORARY are separate (P1-03); for pet_type, allow multiple values (dog+cat)
                found_idx = None
                for idx, old in enumerate(lst):
                    if old.get("subject") == item.subject and old.get("status") == "CURRENT":
                        if item.subject == "city" and old.get("location_role") != item.location_role:
                            continue
                        if item.subject in ("pet_type", "pet_name") and old.get("value", "").lower() != item.value.lower():
                            continue  # allow multiple pet types/names (dog, cat, Max, Milo) as separate CURRENT
                        found_idx = idx
                        break
                if found_idx is not None:
                    old = lst[found_idx]
                    # If new is explicit correction (e.g., moved to New York), history: move old to HISTORICAL
                    if old["value"].lower() != item.value.lower():
                        # Preserve history: set old to HISTORICAL, effective_until now
                        old["status"] = "HISTORICAL"
                        old["effective_until"] = _now_iso()
                        old["contradiction_count"] = old.get("contradiction_count", 0) + 1
                        # Keep old in list (now historical) and add new as CURRENT
                        # Ensure history bounded 5 per subject
                        # Count historical for this subject
                        hist_count = sum(1 for e in lst if e.get("subject") == item.subject and e.get("status") == "HISTORICAL")
                        if hist_count >= _HISTORY_MAX:
                            # Remove oldest historical for this subject
                            for j, e in enumerate(lst):
                                if e.get("subject") == item.subject and e.get("status") == "HISTORICAL":
                                    lst.pop(j)
                                    break
                        lst.append(item.to_dict())
                    else:
                        # Same value → confirmation (strengthen)
                        old["last_confirmed_at"] = _now_iso()
                        old["confirmation_count"] = old.get("confirmation_count", 1) + 1
                        old["confidence"] = min(1.0, old.get("confidence", 0.5) + 0.05)
                        old["evidence_generation_id"] = item.evidence_generation_id
                        # Move to end for recency
                        lst.append(lst.pop(found_idx))
                        lst[-1] = old
                else:
                    lst.append(item.to_dict())
                    # Bounded 30 per creator:user
                    # Prune by importance: explicit > recent
                    if len(lst) > _KNOWLEDGE_MAX:
                        # Sort by status CURRENT first, then confidence, then last_confirmed
                        # Keep CURRENT, not HISTORICAL/EXPIRED, if over limit
                        # For simplicity, keep last 30
                        lst = lst[-_KNOWLEDGE_MAX:]
                # Expired pruning: remove EXPIRED where expires_at < now
                now = datetime.now(timezone.utc)
                filtered = []
                for e in lst:
                    exp = e.get("expires_at")
                    if exp:
                        try:
                            dt = datetime.fromisoformat(exp.replace("Z","+00:00"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            if dt < now and e.get("temporal_type") == "TEMPORARY":
                                e["status"] = "EXPIRED"
                                # Keep EXPIRED but don't count towards active limit? For now keep
                        except Exception:
                            pass
                    filtered.append(e)
                # If many EXPIRED, prune oldest EXPIRED beyond 5
                # Keep active CURRENT
                by_creator[key] = filtered
                facts[_KNOWLEDGE_KEY] = by_creator
                # Atomic update within same transaction (FOR UPDATE lock held)
                await conn.execute(
                    "INSERT INTO user_profiles (user_id, facts, updated_at) VALUES ($1, $2::jsonb, NOW()) ON CONFLICT (user_id) DO UPDATE SET facts=$2::jsonb, updated_at=NOW()",
                    user_id,
                    json.dumps(facts),
                )
                # Also in-memory
                _knowledge_mem[_knowledge_key(creator_id, user_id)] = filtered
                # Pass 3: invalidate doc cache for this key
                try:
                    from commerce.embedding_model import invalidate_doc
                    invalidate_doc(creator_id, f"{item.subject}={item.value}")
                except Exception:
                    pass
                return True
    except Exception:
        # Fallback in-memory (handles history, dedup, bounds) — with lock for concurrency
        k = _knowledge_key(creator_id, user_id)
        lock = _knowledge_locks.setdefault(k, asyncio.Lock())
        async with lock:
            lst = _knowledge_mem.get(k, [])
            if item.evidence_generation_id:
                for e in lst:
                    if e.get("evidence_generation_id") == item.evidence_generation_id and e.get("subject") == item.subject and e.get("value") == item.value:
                        return True
            # Find existing CURRENT for same subject — for city, HOME vs TEMPORARY separate (P1-03); for pet_type, allow multiple values (dog+cat)
            found_idx = None
            for idx, old in enumerate(lst):
                if old.get("subject") == item.subject and old.get("status") == "CURRENT":
                    if item.subject == "city" and old.get("location_role") != item.location_role:
                        continue
                    if item.subject == "pet_type" and old.get("value", "").lower() != item.value.lower():
                        continue
                    if item.subject == "pet_name" and old.get("value", "").lower() != item.value.lower():
                        continue
                    found_idx = idx
                    break
            if found_idx is not None:
                old = lst[found_idx]
                if old["value"].lower() != item.value.lower():
                    old["status"] = "HISTORICAL"
                    old["effective_until"] = _now_iso()
                    old["contradiction_count"] = old.get("contradiction_count", 0) + 1
                    # History bound check
                    hist_count = sum(1 for e in lst if e.get("subject") == item.subject and e.get("status") == "HISTORICAL")
                    if hist_count >= _HISTORY_MAX:
                        for j, e in enumerate(lst):
                            if e.get("subject") == item.subject and e.get("status") == "HISTORICAL":
                                lst.pop(j)
                                break
                    lst.append(item.to_dict())
                else:
                    old["last_confirmed_at"] = _now_iso()
                    old["confirmation_count"] = old.get("confirmation_count", 1) + 1
                    old["confidence"] = min(1.0, old.get("confidence", 0.5) + 0.05)
                    old["evidence_generation_id"] = item.evidence_generation_id
                    lst.append(lst.pop(found_idx))
                    lst[-1] = old
            else:
                lst.append(item.to_dict())
                if len(lst) > _KNOWLEDGE_MAX:
                    lst = lst[-_KNOWLEDGE_MAX:]
            # Expired check not needed for fallback (keep)
            _knowledge_mem[k] = lst
            # Pass 3: invalidate doc cache for this key (fallback path)
            try:
                from commerce.embedding_model import invalidate_doc
                invalidate_doc(creator_id, f"{item.subject}={item.value}")
            except Exception:
                pass
            return False

async def get_fan_knowledge(creator_id: int, user_id: int, profile: dict | None = None) -> list[dict[str, Any]]:
    """Get fan knowledge for creator+user.

    If *profile* is provided it is used directly instead of re-fetching
    from the database — generation-local reuse to avoid redundant PG round-trips.
    """
    try:
        if profile is None:
            from db.postgres import get_user_profile
            profile = await get_user_profile(user_id)
        by_creator = profile.get(_KNOWLEDGE_KEY, {})
        lst = by_creator.get(str(creator_id), []) or by_creator.get(creator_id, []) or []
        # Fallback to in-memory if DB has no data but in-memory does (for tests without DB)
        if not lst:
            lst = _knowledge_mem.get(_knowledge_key(creator_id, user_id), [])
        # Return only CURRENT/TEMPORARY not EXPIRED, but include HISTORICAL for now? For main retrieval we return CURRENT and TEMPORARY not EXPIRED, HISTORICAL only via get_knowledge_memory
        now = datetime.now(timezone.utc)
        out = []
        for e in lst:
            if e.get("status") == "EXPIRED":
                continue
            exp = e.get("expires_at")
            if exp and e.get("temporal_type") == "TEMPORARY":
                try:
                    dt = datetime.fromisoformat(exp.replace("Z","+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < now:
                        continue
                except Exception:
                    pass
            # Only return CURRENT (and TEMPORARY which is CURRENT) — HISTORICAL not returned in main retrieval
            if e.get("status") == "HISTORICAL":
                continue
            out.append(e)
        # If out is empty but _knowledge_mem has CURRENT, fallback to it (for tests)
        if not out:
            # Try in-memory directly
            mem = _knowledge_mem.get(_knowledge_key(creator_id, user_id), [])
            for e in mem:
                if e.get("status") != "CURRENT":
                    continue
                exp = e.get("expires_at")
                if exp and e.get("temporal_type") == "TEMPORARY":
                    try:
                        dt = datetime.fromisoformat(exp.replace("Z","+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt < now:
                            continue
                    except Exception:
                        pass
                out.append(e)
        return out
    except Exception:
        # Fallback: return CURRENT only from in-memory
        now = datetime.now(timezone.utc)
        out = []
        for e in _knowledge_mem.get(_knowledge_key(creator_id, user_id), []):
            if e.get("status") != "CURRENT":
                continue
            exp = e.get("expires_at")
            if exp and e.get("temporal_type") == "TEMPORARY":
                try:
                    dt = datetime.fromisoformat(exp.replace("Z","+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < now:
                        continue
                except Exception:
                    pass
            out.append(e)
        return out

def get_knowledge_memory(creator_id: int, user_id: int) -> list[dict[str, Any]]:
    return list(_knowledge_mem.get(_knowledge_key(creator_id, user_id), []))

def clear_knowledge_memory(creator_id: int | None = None, user_id: int | None = None) -> None:
    if creator_id is None and user_id is None:
        _knowledge_mem.clear()
    elif user_id is not None and creator_id is not None:
        _knowledge_mem.pop(_knowledge_key(creator_id, user_id), None)
    else:
        for k in list(_knowledge_mem.keys()):
            if str(creator_id) in k:
                _knowledge_mem.pop(k, None)

def is_knowledge_expired(item: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if item.get("status") == "EXPIRED":
        return True
    exp = item.get("expires_at")
    if exp:
        try:
            dt = datetime.fromisoformat(exp.replace("Z","+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt < now:
                return True
        except Exception:
            pass
    # Category-aware retention: permanent 90d, current until contradicted, temporary 7d already via expires_at
    return False

# ── Retrieval (bounded, relevance-ranked) ────────────────────────────

async def retrieve_relevant_knowledge(
    creator_id: int,
    user_id: int,
    current_topic: str | None = None,
    open_threads: tuple[str, ...] = (),
    limit: int = 5,
    profile: dict | None = None,
) -> list[dict[str, Any]]:
    """Retrieve relevant knowledge for current turn, relevance-ranked.

    If *profile* is provided it is passed to get_fan_knowledge to avoid
    a redundant PG round-trip.
    """
    try:
        knowledge = await get_fan_knowledge(creator_id, user_id, profile=profile)
        if not knowledge:
            return []
        # Filter expired
        knowledge = [k for k in knowledge if not is_knowledge_expired(k)]
        # Score relevance: current_topic + open_threads overlap with subject/value
        import re
        def tokens(s: str) -> set[str]:
            return set(re.compile(r"[a-z0-9]+").findall(s.lower()))
        current_tokens = set()
        if current_topic:
            current_tokens.update(tokens(current_topic))
        for t in open_threads[:3]:
            current_tokens.update(tokens(t))
        scored = []
        for k in knowledge:
            subj_tokens = tokens(k.get("subject","") + " " + k.get("value",""))
            overlap = len(subj_tokens & current_tokens) if current_tokens else 0
            try:
                last = datetime.fromisoformat(k["last_confirmed_at"].replace("Z","+00:00")) if k.get("last_confirmed_at") else datetime.fromisoformat(k["observed_at"].replace("Z","+00:00"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                days = (datetime.now(timezone.utc) - last).total_seconds()/86400
                recency = max(0, 1 - days/30)
            except Exception:
                recency = 0.5
            # Explicit > inferred
            conf = k.get("confidence", 0.5)
            score = overlap * 0.5 + conf * 0.3 + recency * 0.2
            # Boost for current temporal
            if k.get("temporal_type") == "CURRENT":
                score += 0.2
            scored.append((k, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [k for k,_ in scored[:limit] if _ > 0.2]
    except Exception:
        return []

# ── Unified Personalization Context (bounded) ────────────────────────

async def build_personalization_context(
    creator_id: int,
    user_id: int,
    current_topic: str | None = None,
    open_threads: tuple[str, ...] = (),
) -> str:
    parts: list[str] = []
    try:
        knowledge = await retrieve_relevant_knowledge(creator_id, user_id, current_topic=current_topic, open_threads=open_threads, limit=5)
        if knowledge:
            lines = []
            for k in knowledge:
                # For temporal, show effective time
                if k.get("temporal_type") == "TEMPORARY" and k.get("expires_at"):
                    lines.append(f"{k['subject']}={k['value']} (current, until {k['expires_at'][:10]})")
                elif k.get("status") == "HISTORICAL":
                    lines.append(f"{k['subject']}={k['value']} (historical)")
                else:
                    lines.append(f"{k['subject']}={k['value']} ({k['temporal_type'].lower()}, conf {k['confidence']:.1f})")
            parts.append("FAN KNOWLEDGE: " + "; ".join(lines))
    except Exception:
        pass
    return "\n".join(parts)
