"""Phase 6 current-turn intimacy evidence (deterministic, conservative).

Connects current-turn conversational observations to the Phase 6
descriptive intimacy trajectory domain
(``commerce/intimacy_trajectory.py``).

CORE PRINCIPLE (enforced here):

* Phase 6 extracts evidence.
* The intimacy trajectory decides durable state.
* The LLM observes (advisory only, corroboration counter at most).
* Commerce signals never gain authority over intimacy state.
* Historical intimacy never manufactures current-turn evidence.

This module is deliberately small and deterministic:

* :func:`extract_intimacy_evidence` is a pure function (no Redis, no
  Postgres, no LLM provider, no worker runtime, no commerce imports).
  Importing this module has no side effects.
* Exactly one rule assigns each ``IntimacyTurnEvidence`` field. No
  second intimacy score, no numeric "intimacy strength" aggregation.
* One processed turn yields exactly one ``IntimacyTurnEvidence``
  object (deduplication owned here; per-dimension booleans mean repeated
  lexical hits in one turn count once).
* The existing ``tone`` heuristic (``core/conversation_state.py``) may
  corroborate playfulness only; it is never the sole authority for any
  signal, and never for ``sexual_conversation_signal``.
* Raw message text, spans, excerpts, and evidence strings are never
  persisted here. Only bounded booleans flow into the trajectory.

Lexical discipline (see section 11 of the implementation prompt):

* Every pattern uses word boundaries (``\\b``); substring matches such
  as ``photo -> hot`` are impossible by construction (and pinned by
  tests).
* Categories are narrowly scoped and independent: romantic / playful /
  emotional / sexual-strong / sexual-soft.
* ``sexual_conversation_signal`` requires either one unnegated
  strong-pattern hit or at least two *distinct* unnegated soft-pattern
  tokens in the same message. A single ambiguous word (``sexy`` alone,
  ``horny`` alone) never sets it, and a negated proposition
  (``don't kiss me``) never sets it -- and the known false-positive
  words ``hot``, ``beautiful``, ``gorgeous``, ``babe``, ``photo``,
  ``pic`` are explicitly excluded and pinned by tests.
* False-positive characterization per category lives in the pattern
  comments below and is pinned by tests.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from commerce.intimacy_trajectory import (
    INTIMACY_TRAJECTORY_KEY,
    IntimacyAnchors,
    IntimacyTurnEvidence,
    accumulate_intimacy_turn,
    get_intimacy_anchors,
    intimacy_anchors_from_dict,
    intimacy_anchors_to_dict,
    neutral_intimacy_anchors,
)

logger = logging.getLogger("commerce.intimacy_evidence")

# ---------------------------------------------------------------------------
# Bounded retention for the generation-keyed processed marker list.
# ---------------------------------------------------------------------------

#: Same bound as Phase 2 (existing LTM per-creator bound of 20).
INTIMACY_PROCESSED_IDS_MAX = 20

#: Field name for the bounded processed-marker list inside this creator's
#: intimacy block. Stored alongside (never inside) the anchor payload
#: keys so trajectory readers keep working byte-for-byte.
INTIMACY_PROCESSED_IDS_FIELD = "processed_generation_ids"

# ---------------------------------------------------------------------------
# Lexical evidence (word-boundaried, narrow, documented)
# ---------------------------------------------------------------------------
# FP = known false-positive shape. All patterns require ``\\b`` on both
# ends where applicable so substrings ("photo"->"hot") cannot match.

#: Romantic: explicit missing/thinking-of-you language or the word
#: itself. FP: "romantic comedy / romantic movie" (accepted: LOW band
#: still requires a second corroborated observation).
_ROMANTIC_RE = re.compile(
    r"\b(miss you|missing you|thinking of you|thought of you|romantic|fall for you|falling for you)\b",
    re.IGNORECASE,
)

#: Playful: teasing/flirting laughter vocabulary. FP: nervous or
#: dismissive laughter ("haha" alone), friendly banter. Single hit sets
#: the turn signal; durability still needs repetition.
_PLAYFUL_RE = re.compile(
    r"\b(tease|teasing|teased|playful|flirt|flirting|flirted|flirty|wink|cheeky|haha|lol)\b",
    re.IGNORECASE,
)

#: Emotional: trust/safety/vulnerability disclosure. FP: platonic
#: gratitude ("grateful for you" to a friend). Descriptive only.
_EMOTIONAL_RE = re.compile(
    r"\b(trust you|feel safe|feel safe with you|open up|opening up|vulnerable|means a lot|means so much|grateful for you|there for me|comfort me)\b",
    re.IGNORECASE,
)

#: Sexual-conversation (strong): unambiguous sexual-conversational
#: propositions. A single unnegated strong hit sets the turn signal;
#: a negated proposition ("don't kiss me") never does (see negation
#: guard below). FP: quoted song lyrics / jokes (accepted: durability
#: needs a second turn).
_SEXUAL_STRONG_RE = re.compile(
    r"\b(kiss me|make love|turn me on|turned on|turns me on|in bed with|undress|touch me|take off your|take my clothes off)\b",
    re.IGNORECASE,
)

#: Sexual-conversation (soft): desire adjectives. One alone NEVER sets
#: the signal (too ambiguous in isolation); two *distinct* soft tokens
#: in the same message corroborate each other.
_SEXUAL_SOFT_TOKENS = ("sexy", "horny", "naughty", "aroused", "arousal")
_SEXUAL_SOFT_RES = tuple(
    re.compile(r"\b" + re.escape(token) + r"\b", re.IGNORECASE) for token in _SEXUAL_SOFT_TOKENS
)

#: Explicitly excluded: known false-positive words from the Phase 6
#: prompt (audit section 11). These NEVER contribute to any intimacy signal,
#: even in combination. Pinned by tests.
_EXCLUDED_TOKENS = frozenset({"hot", "beautiful", "gorgeous", "babe", "photo", "pic", "picture"})

#: Combined intimate vocabulary for history/topic scanning (continuity
#: and current-topic flags only -- never durable promotion by itself).
#: Soft singles count here because continuity asks "was intimacy aired
#: before", not "was it corroborated".
_COMBINED_PATTERNS = (
    _ROMANTIC_RE,
    _PLAYFUL_RE,
    _EMOTIONAL_RE,
    _SEXUAL_STRONG_RE,
    *_SEXUAL_SOFT_RES,
)


#: Minimum shared substantive tokens between the current message and
#: prior intimate texts for ``prior_intimate_context_reference``.
#: Mirrors the Phase 5 overlap philosophy (at least one shared
#: substantive token), scoped to intimate context: co-occurrence of
#: unrelated intimacy categories ("I miss you" history + "lol funny"
#: now) is NOT a reference.
INTIMATE_REFERENCE_MIN_OVERLAP = 1

#: Stopwords excluded from reference-overlap counting. Small closed
#: set: pronouns, articles, copulas, and fillers that would otherwise
#: link unrelated turns ("see you later" sharing only "you").
_INTIMACY_STOPWORDS = frozenset(
    {
        "i",
        "me",
        "my",
        "you",
        "your",
        "yours",
        "we",
        "us",
        "our",
        "to",
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "in",
        "on",
        "at",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "that",
        "this",
        "these",
        "those",
        "so",
        "too",
        "just",
        "very",
        "really",
        "quite",
        "still",
        "also",
        "with",
        "for",
        "as",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "will",
        "would",
        "can",
        "could",
        "should",
        "not",
        "no",
        "never",
        "ve",
        "re",
        "ll",
        "d",
        "s",
        "t",
        "m",
    }
)

#: Bound on prior intimate texts retained for overlap counting.
_MAX_PRIOR_TEXTS = 8


def _substantive_tokens(text: str) -> set[str]:
    """Normalized lexical tokens minus stopwords (pure, never raises)."""
    try:
        return set(
            token
            for token in re.findall(r"[a-z0-9]+", text.lower())
            if token and token not in _INTIMACY_STOPWORDS
        )
    except Exception:
        return set()


def _reference_overlap(current: str, prior_texts: Sequence[str]) -> int:
    """Shared substantive tokens between current and prior texts."""
    try:
        current_tokens = _substantive_tokens(current)
        if not current_tokens:
            return 0
        shared: set[str] = set()
        for prior in prior_texts:
            if not isinstance(prior, str) or not prior:
                continue
            shared |= current_tokens & _substantive_tokens(prior)
            if len(shared) >= INTIMATE_REFERENCE_MIN_OVERLAP:
                break
        return len(shared)
    except Exception:
        return 0


def _message_text(item: Any) -> str:
    """Tolerantly read one history item's text (pure, never raises)."""
    try:
        if isinstance(item, Mapping):
            for key in ("content", "text", "message"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return ""
        if isinstance(item, str):
            return item
        return ""
    except Exception:
        return ""


def _has_hit(pattern: re.Pattern[str], text: str) -> bool:
    try:
        if not isinstance(text, str) or not text:
            return False
        return pattern.search(text) is not None
    except Exception:
        return False


def _any_hit(text: str) -> bool:
    """True when any intimate pattern hits (continuity/topic use)."""
    try:
        return any(_has_hit(pattern, text) for pattern in _COMBINED_PATTERNS)
    except Exception:
        return False


#: Negation guard (Defect A remediation): a negated sexual proposition
#: ("don't kiss me", "never touch me") must NOT count as positive
#: sexual-conversation evidence. The guard is deliberately narrow, not
#: a general semantic parser:
#:
#: * it operates per matched proposition/token (never a global turn
#:   veto -- an unrelated negation elsewhere in the turn changes
#:   nothing);
#: * it looks at the governing clause segment immediately before
#:   the match: punctuation always bounds governance and contrast
#:   conjunctions (such as "but") bound it too, so "I am not tired,
#:   kiss me" and "not sexy but horny" still count; a short emphatic
#:   insert looks through one punctuation boundary only ("never, ever
#:   kiss me" does not count), never through a contrast word;
#: * it applies to the sexual-conversation path only (strong matches
#:   and soft tokens). Other dimensions are unchanged.
#: Known limitation: distant, hedged, or forward negation ("I don't
#: think you should kiss me", "kiss me -- not!") is not resolved and
#: still counts; durability still requires a second corroborated turn.
_NEGATION_TERMS = frozenset(
    {
        "don't",
        "dont",
        "not",
        "never",
        "no",
        "cannot",
        "neither",
        "nor",
        "can't",
        "cant",
        "won't",
        "wont",
        "wouldn't",
        "wouldnt",
        "shouldn't",
        "shouldnt",
        "couldn't",
        "couldnt",
        "didn't",
        "didnt",
        "doesn't",
        "doesnt",
        "isn't",
        "isnt",
        "aren't",
        "arent",
        "wasn't",
        "wasnt",
        "weren't",
        "werent",
        "haven't",
        "havent",
        "hasn't",
        "hasnt",
        "hadn't",
        "hadnt",
    }
)

#: Word tokenizer that preserves apostrophes so contractions survive
#: ("don't" stays one token instead of "don"+"t").
_NEG_WORD_RE = re.compile(r"[a-z0-9']+")

#: Clause-boundary split for the negation window. Punctuation always
#: bounds governance; contrast conjunctions bound it too ("not sexy
#: but horny" must not let "not" govern "horny"). The two boundary
#: kinds are kept distinct (see ``_is_negated``): look-through crosses
#: punctuation only, never a contrast word.
_NEG_PUNCT_SPLIT_RE = re.compile(r"[,.;!?]")
_NEG_CONTRAST_SPLIT_RE = re.compile(r"\b(?:but|however|although|though|yet)\b", re.IGNORECASE)

#: A short trailing insert ("never, ever kiss me") does not break
#: governance: when the trailing segment is non-empty but tiny, one
#: preceding segment is examined as well.
_NEG_LOOKTHROUGH_MAX_TOKENS = 2

#: Characters examined immediately before a match.
_NEG_WINDOW_CHARS = 24

#: Word-tokens examined in the governing clause segment.
_NEG_WINDOW_TOKENS = 3


def _segment_has_negation(segment: str) -> bool:
    """True when the segment's last tokens contain a negation term."""
    try:
        normalized = segment.lower().replace(chr(0x2019), chr(0x27)).replace("`", chr(0x27))
        tokens = _NEG_WORD_RE.findall(normalized)[-_NEG_WINDOW_TOKENS:]
        return any(token in _NEGATION_TERMS for token in tokens)
    except Exception:
        return False


def _is_negated(text: str, match_start: int) -> bool:
    """True when a negation term governs the match at ``match_start``.

    Pure, bounded, never raises. Only the governing clause segment of
    the preceding window is examined, and only its last three
    word-tokens, so unrelated negations elsewhere in the turn cannot
    suppress a genuine proposition. A short emphatic insert ("never,
    ever kiss me") looks through one punctuation boundary; a full
    clause ("I am not tired, kiss me") blocks governance, and
    governance never crosses a contrast word ("not sexy but horny"
    leaves "horny" ungoverned).
    """
    try:
        if not isinstance(text, str) or not text or match_start <= 0:
            return False
        window = text[max(0, match_start - _NEG_WINDOW_CHARS) : match_start]
        punct_segments = _NEG_PUNCT_SPLIT_RE.split(window)
        trailing_punct = punct_segments[-1] if punct_segments else ""
        # Contrast words always bound governance within the trailing
        # punctuation segment.
        governing = _NEG_CONTRAST_SPLIT_RE.split(trailing_punct)[-1]
        if _segment_has_negation(governing):
            return True
        # Look-through for short emphatic inserts only: when no
        # contrast word intervenes (governing == trailing segment) and
        # it is a non-empty fragment of at most two tokens (e.g.
        # " ever "), one preceding punctuation segment is examined.
        if governing != trailing_punct:
            return False
        try:
            trailing_tokens = _NEG_WORD_RE.findall(governing.lower())
        except Exception:
            trailing_tokens = []
        if (
            trailing_tokens
            and len(trailing_tokens) <= _NEG_LOOKTHROUGH_MAX_TOKENS
            and len(punct_segments) >= 2
            and _segment_has_negation(punct_segments[-2])
        ):
            return True
        return False
    except Exception:
        return False


def _strong_match_unnegated(text: str) -> bool:
    """True when at least one strong-pattern match is not negated."""
    try:
        if not isinstance(text, str) or not text:
            return False
        for match in _SEXUAL_STRONG_RE.finditer(text):
            if not _is_negated(text, match.start()):
                return True
        return False
    except Exception:
        return False


def _soft_token_hits(text: str) -> set[str]:
    """Distinct unnegated soft-token hits (corroboration counting)."""
    found: set[str] = set()
    try:
        if not isinstance(text, str) or not text:
            return found
        for token, pattern in zip(_SEXUAL_SOFT_TOKENS, _SEXUAL_SOFT_RES):
            try:
                for match in pattern.finditer(text):
                    if not _is_negated(text, match.start()):
                        found.add(token)
                        break
            except Exception:
                continue
    except Exception:
        pass
    return found


def _field(source: Any, name: str, default: Any = None) -> Any:
    if source is None:
        return default
    try:
        if isinstance(source, Mapping):
            return source.get(name, default)
        return getattr(source, name, default)
    except Exception:
        return default


def _tokens(text: Any) -> set[str]:
    try:
        if not isinstance(text, str) or not text:
            return set()
        return set(re.findall(r"[a-z0-9]+", text.lower()))
    except Exception:
        return set()


def _valid_llm_hint(value: Any) -> float | None:
    """Validate an advisory LLM float (pure, strict).

    Returns the float only when it is a real finite number in [0, 1].
    Booleans, strings, NaN/Inf, and out-of-range values yield None.
    Passed through to the trajectory, which applies the high-threshold
    + substantive-signal rule (corroboration counter only, never band
    promotion).
    """
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    if numeric < 0.0 or numeric > 1.0:
        return None
    return numeric


def _read_llm_advisory(llm_signals: Any) -> tuple[float | None, bool]:
    """Read (content_interest, explicit_content_request) advisories.

    Accepts a validated ``CommerceSignals``-shaped object or a plain
    mapping; anything else yields absent. Both values are advisory
    only -- see module docstring.
    """
    try:
        if llm_signals is None:
            return None, False
        interest = _valid_llm_hint(_field(llm_signals, "content_interest", None))
        explicit = _field(llm_signals, "explicit_content_request", False)
        return interest, bool(explicit is True)
    except Exception:
        return None, False


def _tone_hint(conversation_state: Any) -> str | None:
    """Read the existing transient tone (weak supporting input only)."""
    try:
        tone = _field(conversation_state, "tone", None)
        if isinstance(tone, str) and tone.strip():
            return tone.strip().lower()
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Current-turn extraction (pure; exactly one rule per evidence field)
# ---------------------------------------------------------------------------


#: Sentinel distinguishing "caller did not pass an LLM override" from
#: "caller explicitly cleared it". Overrides win over ``llm_signals``.
_UNSET: Any = object()


def extract_intimacy_evidence(
    *,
    user_message: Any,
    history: Sequence[Mapping[str, Any]] | None = None,
    conversation_state: Any | None = None,
    assistant_texts: Sequence[str] | None = None,
    assistant_text: str | None = None,
    llm_signals: Any | None = None,
    llm_content_interest: Any = _UNSET,
    llm_explicit_content: Any | None = None,
    now: datetime | None = None,
) -> IntimacyTurnEvidence:
    """Normalize current-turn intimacy evidence (pure, deterministic).

    * ``user_message`` is the current raw fan turn (tokens only; never
      persisted).
    * ``history`` is already-available processed-turn history
      (direction/content dicts); only presence of intimate vocabulary
      is read, never raw text out.
    * ``conversation_state`` supplies current_topic/open_threads for
      the current-intimate-topic flag (tolerant dict/object reads).
    * ``assistant_texts`` / ``assistant_text`` are recent assistant
      outputs for the continuation flag (already in memory).
    * ``llm_signals`` (CommerceSignals-shaped) or the explicit
      ``llm_content_interest`` / ``llm_explicit_content`` overrides
      supply advisory-only corroboration inputs. Explicit overrides
      win when passed (including an explicit None, which clears).

    Historical trajectory alone never sets any signal: every True
    requires current-turn lexical or state evidence. Never raises.
    """
    try:
        _ = _coerce_now(now)
        text = user_message if isinstance(user_message, str) else ""
        low_text = text  # patterns are case-insensitive; no lower copy kept.

        # -- Dimension signals from the current user message only --
        romantic = _has_hit(_ROMANTIC_RE, low_text)
        # Playful may be corroborated by the transient tone heuristic,
        # but tone alone never creates it: lexical hit required.
        playful = _has_hit(_PLAYFUL_RE, low_text)
        emotional = _has_hit(_EMOTIONAL_RE, low_text)
        sexual_strong = _strong_match_unnegated(low_text)
        soft_hits = _soft_token_hits(low_text)
        sexual = bool(sexual_strong or len(soft_hits) >= 2)

        # -- Prior intimate context: intimate vocabulary in history --
        # Only presence is read here; linkage to the current message is
        # decided below via substantive token overlap (co-occurrence of
        # unrelated intimacy categories is NOT a reference).
        prior = False
        prior_texts: list[str] = []
        try:
            items = list(history) if isinstance(history, (list, tuple)) else []
        except Exception:
            items = []
        try:
            for item in items:
                text_item = _message_text(item)
                if text_item and _any_hit(text_item):
                    prior = True
                    if len(prior_texts) < _MAX_PRIOR_TEXTS:
                        prior_texts.append(text_item)
                    else:
                        break
        except Exception:
            prior = False
            prior_texts = []

        # -- Current intimate topic from conversation state --
        current_topic_flag = False
        try:
            topic = _field(conversation_state, "current_topic", None)
            threads = _field(conversation_state, "open_threads", ()) or ()
            candidates: list[str] = []
            if isinstance(topic, str) and topic.strip():
                candidates.append(topic)
            if isinstance(threads, (list, tuple)):
                candidates.extend(t for t in threads if isinstance(t, str) and t.strip())
            for candidate in candidates:
                if _any_hit(candidate):
                    current_topic_flag = True
                    break
        except Exception:
            current_topic_flag = False

        # -- Continuity: current intimate signal AND prior intimate airing --
        # (co-occurrence across turns; the durable counter only).
        current_any = bool(romantic or playful or emotional or sexual)
        continuity = bool(current_any and prior)
        # -- Reference: linkage, not co-occurrence. The current message
        # must share substantive normalized tokens with the prior
        # intimate texts (Phase 5 overlap philosophy, intimate-scoped).
        # "I miss you" history + "lol funny" now shares nothing
        # substantive and is NOT a reference; "I miss you too" shares
        # "miss" and is one.
        reference = bool(
            current_any
            and prior
            and _reference_overlap(text, prior_texts) >= INTIMATE_REFERENCE_MIN_OVERLAP
        )

        # -- Initiation / continuation --
        user_initiated = bool(current_any and text.strip())
        assistant_continuation = False
        try:
            assistant_bits: list[str] = []
            if isinstance(assistant_text, str) and assistant_text.strip():
                assistant_bits.append(assistant_text)
            if isinstance(assistant_texts, (list, tuple)):
                assistant_bits.extend(
                    t for t in assistant_texts if isinstance(t, str) and t.strip()
                )
            for bit in assistant_bits:
                if _any_hit(bit):
                    assistant_continuation = True
                    break
        except Exception:
            assistant_continuation = False

        # -- LLM advisory (corroboration only) --
        interest, explicit = _read_llm_advisory(llm_signals)
        if llm_content_interest is not _UNSET:
            interest = _valid_llm_hint(llm_content_interest)
        # An explicit boolean override wins; None means "not passed".
        if isinstance(llm_explicit_content, bool):
            explicit = llm_explicit_content

        return IntimacyTurnEvidence(
            romantic_signal=romantic,
            playful_signal=playful,
            emotional_signal=emotional,
            sexual_conversation_signal=sexual,
            intimate_continuity_signal=continuity,
            user_initiated_intimacy=user_initiated,
            assistant_intimacy_continuation=assistant_continuation,
            current_intimate_topic=current_topic_flag,
            prior_intimate_context_reference=reference,
            llm_content_interest=interest,
            llm_explicit_content=explicit,
        )
    except Exception:
        logger.debug("intimacy evidence extraction failed (fail-open neutral)", exc_info=True)
        return IntimacyTurnEvidence()


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now


# ---------------------------------------------------------------------------
# Idempotent accumulation (fail-open; exactly once per generation_id)
# ---------------------------------------------------------------------------


def _read_processed_ids(block: Any) -> list[str]:
    """Tolerantly read the bounded processed-marker list (pure)."""
    try:
        if not isinstance(block, dict):
            return []
        raw = block.get(INTIMACY_PROCESSED_IDS_FIELD, [])
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                out.append(entry.strip()[:128])
        return out
    except Exception:
        return []


async def accumulate_intimacy_turn_idempotent(
    *,
    user_id: int,
    creator_id: int,
    generation_id: str,
    evidence: IntimacyTurnEvidence,
    now: datetime | None = None,
    source: str = "intimacy_evidence",
    max_processed_ids: int = INTIMACY_PROCESSED_IDS_MAX,
) -> tuple[IntimacyAnchors | None, bool]:
    """Accumulate one turn exactly once per generation_id (fail-open).

    The marker check and the intimacy anchor mutation occur atomically
    inside the existing row-locked ``mutate_user_profile_atomically``
    closure, in the intimacy namespace
    (``intimacy_trajectory_by_creator``) -- separate from the
    relationship namespace. No separate Redis marker, no check-then-
    write outside the mutation, no unbounded list.

    * First ``generation_id``: accumulates via
      :func:`accumulate_intimacy_turn`, records the id, prunes to the
      bounded retention, persists only this creator's block.
    * Repeated ``generation_id`` (worker redelivery/retry): does not
      accumulate, returns existing state.
    * The marker is creator-scoped (inside this creator's intimacy
      block) and user-scoped (inside this user's row).

    Never raises: any failure returns ``(None, False)`` so
    conversation processing continues.
    """
    try:
        moment = _coerce_now(now)
        bounded_source = str(source)[:64] if source else "intimacy_evidence"
        gen = generation_id.strip() if isinstance(generation_id, str) else ""
        if not gen:
            logger.warning("intimacy idempotent accumulate skipped: empty generation_id")
            return None, False
        gen = gen[:128]
        try:
            uid = int(user_id)
            cid = int(creator_id)
        except Exception:
            logger.warning("intimacy idempotent accumulate skipped: bad ids")
            return None, False
        if not isinstance(evidence, IntimacyTurnEvidence):
            logger.warning("intimacy idempotent accumulate skipped: bad evidence")
            return None, False
        try:
            cap = int(max_processed_ids)
        except Exception:
            cap = INTIMACY_PROCESSED_IDS_MAX
        cap = max(1, min(cap, 100))

        from db.postgres import mutate_user_profile_atomically

        result: dict[str, Any] = {"anchors": None, "did": False}

        def _mutate(facts: dict[str, Any]) -> bool:
            try:
                by_creator = facts.get(INTIMACY_TRAJECTORY_KEY, {})
                if not isinstance(by_creator, dict):
                    by_creator = {}
                block = by_creator.get(str(cid), {}) or by_creator.get(cid, {})
                if not isinstance(block, dict):
                    block = {}
                processed = _read_processed_ids(block)
                if gen in processed:
                    result["anchors"] = intimacy_anchors_from_dict(block, moment)
                    result["did"] = False
                    return False
                base = (
                    intimacy_anchors_from_dict(block, moment)
                    if block
                    else neutral_intimacy_anchors(moment)
                )
                # Preserve cross-check: trajectory loader on the same block.
                try:
                    _ = get_intimacy_anchors({INTIMACY_TRAJECTORY_KEY: {str(cid): block}}, cid)
                except Exception:
                    pass
                updated = accumulate_intimacy_turn(base, evidence, moment, source=bounded_source)
                processed.append(gen)
                if len(processed) > cap:
                    processed = processed[-cap:]
                new_block = intimacy_anchors_to_dict(updated)
                new_block[INTIMACY_PROCESSED_IDS_FIELD] = list(processed)
                by_creator[str(cid)] = new_block
                facts[INTIMACY_TRAJECTORY_KEY] = by_creator
                result["anchors"] = updated
                result["did"] = True
                return True
            except Exception:
                logger.warning("intimacy idempotent mutate failed (fail-open)", exc_info=True)
                result["anchors"] = None
                result["did"] = False
                return False

        persisted = await mutate_user_profile_atomically(uid, _mutate)
        if result["did"] and not persisted:
            # Mutation asked to write but persistence failed: report
            # failure so the caller treats the snapshot as unavailable.
            # The next redelivery will retry under the same generation_id.
            return result["anchors"], False
        return result["anchors"], bool(result["did"])
    except Exception:
        logger.warning("accumulate_intimacy_turn_idempotent failed (fail-open)", exc_info=True)
        return None, False


def snapshot_for_accumulated(
    anchors: IntimacyAnchors | None,
    turn_evidence: IntimacyTurnEvidence | None = None,
    now: datetime | None = None,
) -> Any:
    """Derive the read-only snapshot for accumulated anchors (pure).

    Thin wrapper over :func:`derive_intimacy_snapshot` so worker call
    sites share one import surface. Never raises.
    """
    try:
        from commerce.intimacy_trajectory import derive_intimacy_snapshot

        return derive_intimacy_snapshot(anchors, turn_evidence, now)
    except Exception:
        logger.debug("intimacy snapshot derivation failed (fail-open)", exc_info=True)
        try:
            from commerce.intimacy_trajectory import neutral_intimacy_snapshot

            return neutral_intimacy_snapshot(now)
        except Exception:
            return None


__all__ = [
    "INTIMACY_PROCESSED_IDS_FIELD",
    "INTIMACY_PROCESSED_IDS_MAX",
    "accumulate_intimacy_turn_idempotent",
    "extract_intimacy_evidence",
    "snapshot_for_accumulated",
]
