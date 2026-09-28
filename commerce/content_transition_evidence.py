"""Phase 8 current-turn content-transition evidence (deterministic, conservative).

Connects the current inbound turn to the Phase 8 content-transition
selector (``commerce/content_transition.py``).

CORE PRINCIPLE (enforced here):

* Phase 8 extracts evidence from the current turn + existing
  deterministic context only.
* The selector decides the transition.
* The LLM NEVER creates content-transition evidence. This module accepts
  no LLM signals parameter at all (structural neutrality): high
  ``content_interest`` / ``explicit_content_request`` scores with neutral
  current-turn text yield no evidence by construction.
* Historical relationship / intimacy / desire / temperature state NEVER
  creates evidence here. Only the current message text plus already-
  derived turn context (``current_topic`` / ``open_threads`` / existing
  LTM open-loop subjects / prior-context linkage supplied by the caller)
  can set a positive flag.
* Existing commerce / free-photo authority is never reproduced here:
  this module classifies the turn; it never selects media, prices,
  eligibility, or delivery.

This module is deliberately small and deterministic:

* :func:`extract_content_transition_evidence` is a pure function (no
  Redis, no Postgres, no LLM provider, no worker runtime, no clock).
  Importing this module has no side effects. The only cross-module
  imports are the pure deterministic verifiers in
  ``commerce.purchase_intent`` (regex allowlists, no I/O).
* Exactly one rule family assigns each
  :class:`ContentTransitionEvidence` field. No numeric "interest score",
  no aggregation.
* One inbound message yields exactly one
  :class:`ContentTransitionEvidence` object.
* Raw message text, spans, excerpts, and evidence strings are never
  persisted here. Only bounded booleans flow to the selector.

Lexical discipline (mirrors the Phase 6/7 philosophy):

* Every pattern uses word boundaries (``\\b``); substring matches are
  impossible by construction.
* A positive flag requires an explicit content construction (content
  noun + request/curiosity/access framing), never a bare keyword such
  as "pic", "no", or "stop" alone.
* ``current_disinterest`` is content-scoped only (``that`` / ``those`` /
  content nouns adjacent to a disinterest verb). Ordinary topic changes,
  bare "no", bare "stop", and unrelated negative sentiment never set it
  (Phase 7 owns general boundary semantics; nothing here duplicates it).
* ``ConversationContract.maintain_topic`` is NEVER read here (it is
  vacuously true whenever ``current_topic`` exists). Topic linkage uses
  the narrow content-token overlap rule below.
* The dead ``commerce.open_loop`` import path is NEVER used here.
  Open-loop subjects arrive as plain strings computed by the caller
  through the real ``commerce.long_term_memory`` implementation.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("commerce.content_transition_evidence")

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

#: Bound pathological input (mirrors commerce.purchase_intent).
_MAX_LEN = 500

#: Content nouns that anchor every positive rule. Generic words such as
#: "set" or "drop" only count together with the required framing of the
#: rule that uses them (request verb, interrogative, demonstrative +
#: established thread), never alone.
_CONTENT_NOUNS = (
    r"pictures?|photos?|pics?|images?|selfies?|videos?|clips?|"
    r"sets?|bundles?|content|exclusives?|ppv|vault|scenes?|"
    r"shoots?|gallery|galleries|albums?|collections?|customs?"
)

_CONTENT_RE = re.compile(rf"\b(?:{_CONTENT_NOUNS})\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    if not isinstance(text, str):
        return ""
    t = text.strip()[:_MAX_LEN]
    t = t.replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")
    return t.lower()


def _negated_before(text_lower: str, match_start: int, window: int = 30) -> bool:
    """True when a negation term shortly precedes the match (negated ask)."""
    snippet = text_lower[max(0, match_start - window) : match_start]
    return bool(
        re.search(
            r"\b(don't|dont|do not|doesn't|doesnt|never|not|no longer)\b",
            snippet,
        )
    )


# ---------------------------------------------------------------------------
# Explicit content request (direct ask for delivery/viewing of content)
# ---------------------------------------------------------------------------
# Narrow allowlist in the style of commerce/purchase_intent.py. Each
# pattern needs a request verb (+ optional determiner) and a content
# noun, or a first-person want/let-me construction with one.

_EXPLICIT_REQUEST_RES: tuple[re.Pattern[str], ...] = (
    # "send me that picture", "show me your videos", "send pics"
    re.compile(
        rf"\b(send|show|give|drop|share)\s+me\b"
        rf"(?:\s+(?:that|this|the|those|your|a|some|any|more|that\s+kind\s+of))?"
        rf"\s+(?:{_CONTENT_NOUNS})\b",
        re.IGNORECASE,
    ),
    # "can I see that set?", "can I buy that video?", "can I get those pics?"
    re.compile(
        r"\bcan\s+i\s+(see|have|get|buy|unlock|view|watch)\b"
        r"(?:\s+(?:that|this|the|those|it|them|your|some|any|more|a))?"
        rf"(?:\s+(?:{_CONTENT_NOUNS})\b|\s+(?:that|this|those|it|them)\b)",
        re.IGNORECASE,
    ),
    # "can you send me ...", "can you show me ..."
    re.compile(
        r"\bcan\s+you\s+(send|show|give|drop|share|post)\b.{0,20}?"
        rf"(?:me\b.{{0,20}}?(?:{_CONTENT_NOUNS})\b|(?:{_CONTENT_NOUNS})\b)",
        re.IGNORECASE,
    ),
    # "I want that set", "I want to see your exclusive", "I wanna see pics"
    re.compile(
        rf"\bi\s+(?:want|wanna)\s+(?:to\s+(?:see|have|get|buy|unlock|view|watch)\b"
        rf".{{0,20}}?(?:{_CONTENT_NOUNS}|that|this|those|it|them)\b"
        rf"|(?:that|this|the|those)\s+(?:{_CONTENT_NOUNS})\b)",
        re.IGNORECASE,
    ),
    # "let me see those pics", "let me get that video"
    re.compile(
        r"\blet\s+me\s+(see|have|get|watch|view|buy|unlock)\b"
        r"(?:\s+(?:that|this|the|those|it|them|your|some))?"
        rf"(?:\s+(?:{_CONTENT_NOUNS})\b|\s+(?:that|this|those|it|them)\b)",
        re.IGNORECASE,
    ),
)

# ---------------------------------------------------------------------------
# Content curiosity (question about kinds/availability of content)
# ---------------------------------------------------------------------------
# Every pattern is interrogative by construction ("what kind of ... do
# you ...", "do you ..."), so declarative praise ("you make good
# content") can never match.

_CURIOSITY_RES: tuple[re.Pattern[str], ...] = (
    # "what kind of pictures do you take?"
    re.compile(
        rf"\bwhat\s+kind\s+of\s+(?:{_CONTENT_NOUNS}|stuff|things|surprises)\b"
        r"\s+do\s+you\s+(take|make|post|sell|have|share|offer|do)\b",
        re.IGNORECASE,
    ),
    # "what pictures do you have?", "what content do you make?"
    re.compile(
        rf"\bwhat\s+(?:{_CONTENT_NOUNS}|stuff)\b"
        r"\s+do\s+you\s+(have|make|post|sell|share|offer|do)\b",
        re.IGNORECASE,
    ),
    # "do you make videos?", "do you sell exclusives?", "do you have any ppv?"
    re.compile(
        rf"\bdo\s+you\s+(make|sell|post|share|take|have|offer|do)\b"
        rf"(?:\s+(?:any|a|some|those|that|your))?\s+(?:{_CONTENT_NOUNS})\b",
        re.IGNORECASE,
    ),
    # "what do you post?", "what do you sell?"
    re.compile(
        r"\bwhat\s+do\s+you\s+(post|sell|share|offer|make)\b",
        re.IGNORECASE,
    ),
    # "what's on your page / exclusive page / vault / fan site?"
    re.compile(
        r"\bwhat(?:'s| is)\s+on\s+your\s+(page|exclusive(?:\s+page)?|vault|fan\s+site|site)\b",
        re.IGNORECASE,
    ),
)

# ---------------------------------------------------------------------------
# Content/access question (how to unlock / access / find content)
# ---------------------------------------------------------------------------
# Price phrasing itself is covered by the deterministic price verifier;
# these patterns cover non-price access mechanics.

_ACCESS_RES: tuple[re.Pattern[str], ...] = (
    # "how do I unlock that?", "how can I see the set?"
    re.compile(
        r"\bhow\s+(?:do|can)\s+i\s+(unlock|access|get|see|view|watch|buy|find|open)\b",
        re.IGNORECASE,
    ),
    # "where can I buy that?", "where do I unlock ...?"
    re.compile(
        r"\bwhere\s+(?:can|do)\s+i\s+(buy|unlock|find|get|access|see|view)\b",
        re.IGNORECASE,
    ),
    # "do I have to pay to see that?"
    re.compile(
        r"\bdo\s+i\s+(?:have\s+to|need\s+to|got\s+to|gotta)\s+pay\b.{0,20}?"
        r"(?:to\s+(?:see|get|view|watch|unlock|access|buy)|for\s+(?:that|this|those|it|them))?",
        re.IGNORECASE,
    ),
)

# ---------------------------------------------------------------------------
# Current-turn content disinterest (content-scoped only)
# ---------------------------------------------------------------------------
# Conservative by construction: every pattern needs a disinterest verb
# adjacent to a content anchor (demonstrative or content noun). Bare
# "no", bare "stop", ordinary topic changes ("let's talk about
# something else"), and unrelated negative sentiment never match.

_DISINTEREST_RES: tuple[re.Pattern[str], ...] = (
    # "not interested in that anymore", "not interested in content".
    # The anchor is required: ordinary topic preferences ("not
    # interested in football") are not content disinterest.
    re.compile(
        r"\bnot\s+interested\s+in\b"
        rf"\s+(?:that|those|it|them|this|these|more|any\s+of\s+that|{_CONTENT_NOUNS}|that\s+stuff|that\s+kind\s+of\s+(?:stuff|content|thing))\b"
        r"(?:\s+anymore)?",
        re.IGNORECASE,
    ),
    # Bare "not interested" at end of message (nothing specified).
    re.compile(
        r"\bnot\s+interested\b\s*[.!]*$",
        re.IGNORECASE,
    ),
    # "don't want that", "don't want those pics", "don't want any of that"
    re.compile(
        r"\b(?:don't|dont|do\s+not)\s+want\b"
        rf"\s+(?:that|those|it|them|this|these|any\s+of\s+that|more\s+of\s+that|that\s+stuff|(?:{_CONTENT_NOUNS})\b.{0, 10}?)",
        re.IGNORECASE,
    ),
    # "no thanks to that", "no thanks for those", "no thanks, not interested"
    re.compile(
        r"\bno\s+thanks\b.{0,30}?"
        r"(?:that|those|it|them|this|not\s+interested|pass\b)",
        re.IGNORECASE,
    ),
    # "not looking for that", "not looking for content"
    re.compile(
        r"\bnot\s+looking\s+for\b"
        rf"\s+(?:that|those|it|them|this|(?:{_CONTENT_NOUNS})\b|that\s+stuff)\b",
        re.IGNORECASE,
    ),
    # "leave that alone", "leave it alone"
    re.compile(
        r"\b(?:leave|drop)\s+(that|it|those|them)\s+alone\b",
        re.IGNORECASE,
    ),
    # "I'm done with that", "I'm done with those pics"
    re.compile(
        r"\b(?:i'm|i\s+am|im)\s+done\s+with\b"
        rf"\s+(?:that|those|it|them|this|(?:{_CONTENT_NOUNS})\b)",
        re.IGNORECASE,
    ),
    # "don't send me that / those pics / anything like that"
    re.compile(
        r"\b(?:don't|dont|do\s+not|never)\s+send\s+me\b"
        r"\s+(?:that|those|those\s+pics|any\s+pics|anything\s+like\s+that|that\s+stuff)\b",
        re.IGNORECASE,
    ),
    # "stop sending me that / those pics" (content-scoped stop only)
    re.compile(
        rf"\bstop\s+(?:sending|showing|offering|pushing)\s+(?:me\s+)?(?:that|those|it|them|(?:{_CONTENT_NOUNS})\b)",
        re.IGNORECASE,
    ),
)

# ---------------------------------------------------------------------------
# Thread-continuation reference phrasing ("that set you mentioned")
# ---------------------------------------------------------------------------

_REFERENCE_RE = re.compile(
    rf"\b(?:that|this|the|those|same|last|other)\s+(?:{_CONTENT_NOUNS}|one)\b"
    r".{0,20}?\byou\s+(mentioned|showed|sent|posted|told|shared|made|took|promised)\b",
    re.IGNORECASE,
)

_DEMONSTRATIVE_CONTENT_RE = re.compile(
    rf"\b(?:that|those)\s+(?:{_CONTENT_NOUNS})\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Evidence object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContentTransitionEvidence:
    """One turn of deterministic content-interest evidence (immutable)."""

    explicit_request: bool = False
    curiosity: bool = False
    access_question: bool = False
    thread_continuation: bool = False
    purchase_intent: bool = False
    current_disinterest: bool = False

    def has_interest(self) -> bool:
        """True when any current content-interest/request/continuity flag holds."""
        try:
            return bool(
                self.explicit_request
                or self.curiosity
                or self.access_question
                or self.thread_continuation
                or self.purchase_intent
            )
        except Exception:
            return False


_NEUTRAL_EVIDENCE = ContentTransitionEvidence()


def _state_texts(conversation_state: Any | None) -> tuple[str | None, tuple[str, ...]]:
    """Read current_topic / open_threads tolerantly (dict or object)."""
    topic: str | None = None
    threads: tuple[str, ...] = ()
    try:
        if conversation_state is None:
            return None, ()
        if isinstance(conversation_state, dict):
            raw_topic = conversation_state.get("current_topic")
            raw_threads = conversation_state.get("open_threads", ())
        else:
            raw_topic = getattr(conversation_state, "current_topic", None)
            raw_threads = getattr(conversation_state, "open_threads", ())
        if isinstance(raw_topic, str) and raw_topic.strip():
            topic = raw_topic.strip()
        if isinstance(raw_threads, (list, tuple)):
            threads = tuple(t for t in raw_threads if isinstance(t, str) and t.strip())
    except Exception:
        return None, ()
    return topic, threads


def _is_content_thread(text: str) -> bool:
    """True when a topic/thread string carries a content token."""
    try:
        return bool(_CONTENT_RE.search(text or ""))
    except Exception:
        return False


def _loop_subjects(open_loop_subjects: Any | None) -> tuple[str, ...]:
    try:
        if open_loop_subjects is None:
            return ()
        if isinstance(open_loop_subjects, str):
            items: Sequence[Any] = (open_loop_subjects,)
        else:
            items = tuple(open_loop_subjects)  # type: ignore[arg-type]
        return tuple(s for s in items if isinstance(s, str) and s.strip())
    except Exception:
        return ()


def _has_thread_continuation(
    *,
    text_lower: str,
    topic: str | None,
    threads: tuple[str, ...],
    loop_subjects: tuple[str, ...],
) -> bool:
    """Narrow content-thread linkage (never free-text continuity).

    Requires a content token in the CURRENT message plus one trustworthy
    anchor: a content-bearing ``current_topic`` / ``open_thread`` / LTM
    open-loop subject, or an explicit "that <content> you mentioned"
    reference, or a demonstrative content reference ("that set") with a
    content thread present.
    """
    try:
        if not _CONTENT_RE.search(text_lower):
            return False
        thread_present = False
        if topic is not None and _is_content_thread(topic):
            thread_present = True
        if not thread_present:
            for t in threads:
                if _is_content_thread(t):
                    thread_present = True
                    break
        loop_hit = False
        if not thread_present:
            for subj in loop_subjects:
                if _is_content_thread(subj):
                    loop_hit = True
                    break
        if thread_present:
            # Content token now + established content thread, or an
            # explicit reference to previously discussed content.
            if _REFERENCE_RE.search(text_lower) or _DEMONSTRATIVE_CONTENT_RE.search(text_lower):
                return True
            # "tell me more about ..." / "what about ..." with a content
            # noun while a content thread is open is continuation.
            if re.search(r"\b(tell\s+me\s+more|more\s+about|what\s+about)\b", text_lower):
                return True
            # Bare content question while a content thread is open still
            # counts as continuity (the thread is the anchor), but only
            # when the message is short and content-focused to avoid
            # ordinary conversation inheriting a stale thread.
            return len(text_lower) <= 140
        if loop_hit:
            # LTM open-loop content subject exists; require an explicit
            # reference so unrelated chatter cannot inherit the loop.
            return bool(
                _REFERENCE_RE.search(text_lower) or _DEMONSTRATIVE_CONTENT_RE.search(text_lower)
            )
        return False
    except Exception:
        return False


def extract_content_transition_evidence(
    user_message: str,
    conversation_state: Any | None = None,
    open_loop_subjects: Sequence[str] | None = None,
    has_prior_context_reference: bool = False,
) -> ContentTransitionEvidence:
    """Extract one turn of content-transition evidence (pure, never raises).

    Args:
        user_message: raw current inbound text (authoritative).
        conversation_state: existing derived state (dict or object) with
            ``current_topic`` / ``open_threads``. ``maintain_topic`` is
            deliberately never read.
        open_loop_subjects: plain subject strings from the existing LTM
            open-loop / commitment retrieval (caller-supplied, read-only).
        has_prior_context_reference: caller-computed current-to-prior
            linkage from the existing relationship-context selection
            (advisory corroboration for continuation only, never a sole
            authority).

    Returns:
        Exactly one :class:`ContentTransitionEvidence`. Unusable input
        yields neutral evidence (fail-closed: no transition guidance).
        LLM signals are never consulted (structural neutrality).
    """
    try:
        norm = _norm(user_message)
        if not norm or len(norm.strip()) < 2:
            return _NEUTRAL_EVIDENCE

        # Current disinterest is evaluated first so callers can suppress
        # even when curiosity/request phrasing co-occurs.
        disinterest = False
        try:
            for pat in _DISINTEREST_RES:
                if pat.search(norm):
                    disinterest = True
                    break
        except Exception:
            disinterest = False

        explicit = False
        try:
            for pat in _EXPLICIT_REQUEST_RES:
                m = pat.search(norm)
                if m and not _negated_before(norm, m.start()):
                    explicit = True
                    break
        except Exception:
            explicit = False

        curiosity = False
        try:
            for pat in _CURIOSITY_RES:
                if pat.search(norm):
                    curiosity = True
                    break
        except Exception:
            curiosity = False

        access = False
        try:
            for pat in _ACCESS_RES:
                if pat.search(norm):
                    access = True
                    break
            if not access:
                try:
                    from commerce.purchase_intent import is_price_inquiry as _is_price

                    if _is_price(user_message):
                        access = True
                except Exception:
                    pass
        except Exception:
            access = False

        purchase = False
        try:
            from commerce.purchase_intent import (
                is_explicit_purchase_request as _is_purchase,
            )

            purchase = bool(_is_purchase(user_message))
        except Exception:
            purchase = False

        topic, threads = _state_texts(conversation_state)
        subjects = _loop_subjects(open_loop_subjects)
        continuation = _has_thread_continuation(
            text_lower=norm, topic=topic, threads=threads, loop_subjects=subjects
        )
        # Prior-context linkage corroborates continuation only when a
        # content noun is present now (bands/threads alone never suffice;
        # this flag alone never creates continuation).
        try:
            if (
                not continuation
                and bool(has_prior_context_reference) is True
                and bool(_CONTENT_RE.search(norm))
                and (topic is not None or len(threads) > 0 or len(subjects) > 0)
            ):
                continuation = True
        except Exception:
            pass

        return ContentTransitionEvidence(
            explicit_request=bool(explicit),
            curiosity=bool(curiosity),
            access_question=bool(access),
            thread_continuation=bool(continuation),
            purchase_intent=bool(purchase),
            current_disinterest=bool(disinterest),
        )
    except Exception:
        logger.debug("content transition evidence failed (fail-closed neutral)", exc_info=True)
        return _NEUTRAL_EVIDENCE


__all__ = [
    "ContentTransitionEvidence",
    "extract_content_transition_evidence",
]
