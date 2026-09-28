"""Conversation lifecycle + conversational state (C.1-F Phases 1-2).

Lightweight, transient, derived-per-turn from messages/history — no new worker,
no second queue. Persistence is zero or Redis-volatile; correctness does not
depend on durability because identity is re-derived from message history
(which is durable Postgres) every turn.

Responsibilities:
- NEW / ESTABLISHED / RETURNING
- identity_already_established
- last assistant question + was_answered
- open threads / topics extraction
- conversational tone hint
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class ConversationLifecycle(str):
    NEW = "new"
    ESTABLISHED = "established"
    RETURNING = "returning"  # dormant then resumed


# Heuristics for identity: did Sunny already introduce herself in history?
_SUNNY_NAME_PATTERNS = (
    re.compile(r"\bsunny\s*skye\b", re.I),
    re.compile(r"\biam\s+sunny\b", re.I),
    re.compile(r"\bi['’ ]?m\s+sunny\b", re.I),
)


def identity_already_established_from_messages(messages: list[dict]) -> bool:
    """True if any outbound/sunny-authored message already contains an intro."""
    for m in messages:
        direction = m.get("direction") or m.get("role") or ""
        content = m.get("content") or ""
        # check both stored outbound messages and assistant-turn transcripts
        if direction in ("outbound", "assistant"):
            for pat in _SUNNY_NAME_PATTERNS:
                if pat.search(content):
                    return True
    return False


def derive_lifecycle(
    message_count: int,
    last_message_at: datetime | None,
    now: datetime | None = None,
) -> str:
    """Map durable counters to NEW/ESTABLISHED/RETURNING."""
    now = now or datetime.now(timezone.utc)
    if message_count <= 2:
        return ConversationLifecycle.NEW
    # returning = was dormant (>48h) then resumed
    if last_message_at is not None:
        gap_h = (now - last_message_at).total_seconds() / 3600 if last_message_at.tzinfo else 48
        if gap_h >= 48:
            return ConversationLifecycle.RETURNING
    return ConversationLifecycle.ESTABLISHED


# ---------------------------------------------------------------------------
# Conversational state (transient)
# ---------------------------------------------------------------------------

# naive question detection — ends with ? or starts with interrogative
_QUESTION_RE = re.compile(r"\?\s*$")

def _is_question(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if _QUESTION_RE.search(t):
        return True
    low = t.lower()
    return low.startswith(("what ", "how ", "when ", "where ", "who ", "why ", "can you ", "could you ", "do you ", "are you ", "have you "))

def _extract_last_question(messages: list[dict]) -> tuple[str | None, bool]:
    """Return (last_assistant_question_text, was_answered)."""
    last_q = None
    last_q_idx = -1
    for i, m in enumerate(messages):
        role = m.get("direction") or m.get("role") or ""
        content = (m.get("content") or "").strip()
        is_sunny = role in ("outbound", "assistant", "model")
        if is_sunny and _is_question(content):
            last_q = content
            last_q_idx = i
    if last_q is None:
        return None, False
    # answered if any user turn after last_q
    for m in messages[last_q_idx + 1:]:
        role = m.get("direction") or m.get("role") or ""
        if role in ("inbound", "user"):
            return last_q, True
    return last_q, False

def _extract_topics(messages: list[dict], limit: int = 4) -> list[str]:
    keywords = ["work", "netflix", "popcorn", "movie", "saturday", "saturdays", "office", "home", "horny", "naughty", "pic", "picture", "weather", "day"]
    seen: list[str] = []
    for m in messages[-8:]:
        low = (m.get("content") or "").lower()
        for kw in keywords:
            if kw in low and kw not in seen:
                seen.append(kw)
                if len(seen) >= limit:
                    return seen
    # fallback: last user message first words as topic-ish
    return seen

def _open_threads(messages: list[dict]) -> list[str]:
    # open = fan-mentioned topics not yet closed by sunny referencing them
    topics = _extract_topics(messages, limit=6)
    threads: list[str] = []
    for t in topics:
        # naively keep topics mentioned in last 6 but not last 2 sunny answers
        threads.append(t)
    return threads[:3]

# Word-boundaried flirty-tone vocabulary (same words as before; bare
# substring matching tagged "photography" flirty via "hot" in "photo").
_FLIRTY_WORD_RE = re.compile(r"\b(horny|sexy|naughty|nude|kiss|hot|beautiful|gorgeous|babe)\b")


def _derive_tone(messages: list[dict]) -> str:
    last_user = next((m for m in reversed(messages) if (m.get("direction") or m.get("role")) in ("inbound","user")), None)
    if not last_user:
        return "warm"
    low = (last_user.get("content") or "").lower()
    # Word-boundaried: bare substrings misfire ("hot" in "photo",
    # "kiss" inside longer words), tagging innocent turns flirty.
    if _FLIRTY_WORD_RE.search(low):
        return "flirty"
    if any(w in low for w in ["sad","lonely","depressed","upset"]):
        return "supportive"
    if "?" in low:
        return "curious"
    return "warm"


@dataclass(frozen=True)
class ConversationState:
    lifecycle: str
    identity_already_established: bool
    current_topic: str | None
    recent_topics: tuple[str, ...]
    open_threads: tuple[str, ...]
    last_question: str | None
    last_question_answered: bool
    consecutive_questions: int  # computed (for question budget)
    tone: str
    last_user_fact: str | None
    questions_in_last_3: int = 0  # D-02: sliding window count (max 3)


# ---------------------------------------------------------------------------
# Phase 2.4: single lifecycle authority
# ---------------------------------------------------------------------------

#: Message-count thresholds for deterministic funnel progression.
#: Documented, creator-agnostic constants (no I/O, no LLM).
FUNNEL_WARMING_AT_MESSAGES = 5
FUNNEL_ENGAGED_AT_MESSAGES = 20

#: Terminal funnel stages the activity writer never demotes or promotes.
FUNNEL_TERMINAL_STAGES = frozenset({"converted", "vip"})


def derive_funnel_stage(
    current_stage: str | None,
    message_count: int = 0,
) -> str | None:
    """Deterministic new→warming→engaged transition (pure, Phase 2.4).

    Returns the target stage, or None when no transition applies (already
    terminal, already there, or below thresholds). Idempotent: applying the
    result and re-deriving yields None. Jumps new→engaged directly when the
    count already qualifies (single write, still idempotent).
    """
    try:
        cur = (current_stage or "new").strip().lower() or "new"
        try:
            count = int(message_count or 0)
        except (TypeError, ValueError):
            count = 0
        if cur in FUNNEL_TERMINAL_STAGES or cur == "engaged":
            return None
        if count >= FUNNEL_ENGAGED_AT_MESSAGES and cur in ("new", "warming"):
            return "engaged"
        if count >= FUNNEL_WARMING_AT_MESSAGES and cur == "new":
            return "warming"
        return None
    except Exception:
        return None


@dataclass(frozen=True)
class LifecycleState:
    """Single authoritative lifecycle snapshot for one turn (Phase 2.4).

    Unites the three previously divergent derivations: conversation
    lifecycle (NEW/ESTABLISHED/RETURNING), durable funnel stage, and
    deterministic relationship state. ``degraded`` is True when purchase
    context was unknown (explicit unknown, never zeros-as-truth).
    """

    lifecycle: str
    funnel_stage: str
    relationship_state: str
    degraded: bool = False


def derive_lifecycle_state(
    *,
    funnel_stage: str | None = None,
    message_count: int = 0,
    last_message_days_ago: float | None = None,
    purchase_count: int | None = None,
    last_purchase_days_ago: float | None = None,
    has_active_offer: bool | None = None,
    now: datetime | None = None,
) -> LifecycleState:
    """Single lifecycle owner (pure, deterministic, no I/O — Phase 2.4).

    ``purchase_count`` / ``has_active_offer`` accept None meaning UNKNOWN
    (caller has no purchase context); the state is then tagged degraded
    instead of deriving from false zeros.
    """
    try:
        _now = now or datetime.now(timezone.utc)
        try:
            _count = int(message_count or 0)
        except (TypeError, ValueError):
            _count = 0
        _last_at: datetime | None = None
        try:
            if last_message_days_ago is not None and float(last_message_days_ago) >= 0:
                from datetime import timedelta as _td

                _last_at = _now - _td(days=float(last_message_days_ago))
        except (TypeError, ValueError):
            _last_at = None
        lifecycle = derive_lifecycle(_count, _last_at, now=_now)
        funnel = (funnel_stage or "new").strip().lower() or "new"

        degraded = purchase_count is None or has_active_offer is None
        try:
            _pc = 0 if purchase_count is None else int(purchase_count)
        except (TypeError, ValueError):
            _pc = 0
            degraded = True
        _hao = bool(has_active_offer) if has_active_offer is not None else False

        from commerce.relationship import derive_relationship_state as _derive_rel

        rel = _derive_rel(
            funnel_stage=funnel,
            purchase_count=_pc,
            last_purchase_days_ago=last_purchase_days_ago,
            last_message_days_ago=last_message_days_ago,
            message_count=_count,
            has_active_offer=_hao,
        )
        rel_value = rel.value if hasattr(rel, "value") else str(rel)
        return LifecycleState(
            lifecycle=lifecycle,
            funnel_stage=funnel,
            relationship_state=rel_value,
            degraded=degraded,
        )
    except Exception:
        # Fail-open: unknown is safer than a wrong state.
        return LifecycleState(
            lifecycle=ConversationLifecycle.NEW,
            funnel_stage=(funnel_stage or "new"),
            relationship_state="cold",
            degraded=True,
        )


def derive_conversation_state(
    messages: list[dict],
    user: dict | None = None,
    now: datetime | None = None,
) -> ConversationState:
    """Derive lightweight conversational state from recent messages.

    Pure, deterministic, no I/O, no LLM.
    """
    message_count = 0
    last_at: datetime | None = None
    if user is not None:
        message_count = int(user.get("message_count") or len(messages))
        last_at = user.get("last_seen") or user.get("last_message_at")
        if isinstance(last_at, str):
            try:
                last_at = datetime.fromisoformat(last_at)
            except Exception:
                last_at = None

    lifecycle = derive_lifecycle(message_count, last_at, now=now)
    already = identity_already_established_from_messages(messages)
    # if still new but history contains prior sunny intro from earlier session
    # that was trimmed away (limit 20), the assistant count truncation may have
    # cut it — conservative: treat NEW with message_count>10 as established
    if not already and message_count > 8:
        # caller passed 20-limit recent slice; full history says established
        # so assume identity done once we have >8 total messages
        already = True

    topics = _extract_topics(messages)
    current_topic = topics[0] if topics else None
    open_threads = tuple(_open_threads(messages))
    last_q, answered = _extract_last_question(messages)

    # consecutive sunny questions (trailing assistant turns that are questions)
    consecutive = 0
    for m in reversed(messages):
        role = m.get("direction") or m.get("role") or ""
        if role in ("inbound","user"):
            break
        if role in ("outbound","assistant","model"):
            if _is_question(m.get("content") or ""):
                consecutive += 1
            else:
                break

    tone = _derive_tone(messages)

    # last user-revealed fact: crude fallback last inbound
    last_user_fact = None
    for m in reversed(messages):
        role = m.get("direction") or m.get("role") or ""
        if role in ("inbound","user") and len((m.get("content") or "").strip()) > 6:
            last_user_fact = (m.get("content") or "").strip()[:120]
            break

    # D-02: sliding window — questions in last 3 assistant turns
    questions_in_last_3 = 0
    assistant_turns_seen = 0
    for m in reversed(messages):
        role = m.get("direction") or m.get("role") or ""
        if role in ("outbound","assistant","model"):
            if assistant_turns_seen < 3:
                if _is_question(m.get("content") or ""):
                    questions_in_last_3 += 1
            assistant_turns_seen += 1
            if assistant_turns_seen >= 3:
                break

    return ConversationState(
        lifecycle=lifecycle,
        identity_already_established=already,
        current_topic=current_topic,
        recent_topics=tuple(topics[:4]),
        open_threads=open_threads,
        last_question=last_q,
        last_question_answered=answered,
        consecutive_questions=consecutive,
        tone=tone,
        last_user_fact=last_user_fact,
        questions_in_last_3=questions_in_last_3,
    )
