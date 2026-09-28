"""Phase 2 current-turn relationship evidence normalization (AUDIT → IMPLEMENT).

Connects current-turn behavioral evidence to the Phase 1 deterministic
relationship trajectory domain (``commerce/relationship_trajectory.py``).

CORE PRINCIPLE (enforced here):

* Phase 2 extracts evidence.
* Phase 1 decides relationship state.
* The LLM observes (advisory only).
* The deterministic relationship domain remains authoritative.

This module is deliberately small and deterministic:

* :func:`extract_turn_evidence` is a pure function (no Redis, no Postgres,
  no LLM provider, no worker runtime). Importing this module has no side
  effects and requires none of that infrastructure.
* Exactly one rule assigns each ``RelationshipTurnEvidence`` field. No
  additional relationship evidence fields, no second relationship score, no
  engagement score, no numeric "relationship strength" aggregation.
* One inbound message yields exactly one ``RelationshipTurnEvidence``
  object (deduplication owned here).
* LLM signals are advisory only and only ever read from validated
  ``CommerceSignals`` (never raw ``OneCallReply``, never raw evidence
  strings, never content-interest signals).
* ``ConversationContract.maintain_topic`` is NEVER read here (it is
  vacuously true whenever ``current_topic`` exists). Topic continuation
  uses the narrow provisional overlap rule below.
* The dead ``commerce.open_loop`` import path is NEVER used here. Open-loop
  inputs arrive as plain booleans computed by the caller through the real
  ``commerce.long_term_memory`` implementation.
* Raw message text, spans, excerpts, and evidence strings are never
  persisted here. Only bounded booleans flow into Phase 1.

Provisional thresholds introduced here (all deterministic, named,
documented, boundary-tested):

* :data:`RELATIONSHIP_TOPIC_MIN_OVERLAP` — minimum shared topic-token
  overlap for ``user_continued_topic`` (provisional).
* :data:`RELATIONSHIP_RETRIEVAL_MIN_OVERLAP` — minimum shared token
  overlap against existing retrieval output for
  ``user_referenced_previous_context`` (provisional; distinct from the
  commercial ``0.2`` relevance prefilter which is NOT reused).
* :data:`RELATIONSHIP_PROCESSED_IDS_MAX` — bounded retention for the
  generation-keyed idempotency marker (chosen from the existing LTM
  per-creator bound of 20 items; provisional).
"""

from __future__ import annotations

import logging
import math
import re
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from commerce.relationship_trajectory import (
    RELATIONSHIP_TRAJECTORY_KEY,
    RelationshipAnchors,
    RelationshipTurnEvidence,
    accumulate_turn,
    anchors_from_dict,
    anchors_to_dict,
    derive_relationship_snapshot,
    get_relationship_anchors,
    neutral_anchors,
)

logger = logging.getLogger("commerce.relationship_evidence")

# ---------------------------------------------------------------------------
# Provisional thresholds (explicit, deterministic, boundary-tested)
# ---------------------------------------------------------------------------

#: Minimum shared topic-token overlap for ``user_continued_topic``.
#: Provisional: a single shared substantive token that is present both in
#: the current message and in prior history (via ConversationState
#: topic/open-thread data) counts as continuation. Requires prior presence
#: so a single message cannot continue a topic it just created.
RELATIONSHIP_TOPIC_MIN_OVERLAP = 1

#: Minimum shared token overlap against existing retrieval output for
#: ``user_referenced_previous_context``. Provisional and deliberately
#: distinct from the commercial relevance prefilter (``0.2`` score) used by
#: ``retrieve_relevant_memories`` / ``retrieve_relevant_knowledge``: those
#: functions prefilter at ``0.2`` (nearly everything passes); the
#: relationship layer additionally requires at least one shared
#: substantive token between the current message and a retrieved
#: subject/value. If reliable retrieval evidence is unavailable, false.
RELATIONSHIP_RETRIEVAL_MIN_OVERLAP = 1

#: Bounded retention for the generation-keyed processed marker list kept
#: inside the existing relationship namespace. Chosen from the existing
#: LTM per-creator bound (20 items per creator:user in
#: ``commerce/long_term_memory.py``); provisional. Only the most recent N
#: generation_ids are retained; older entries are pruned deterministically.
RELATIONSHIP_PROCESSED_IDS_MAX = 20

#: Field name for the bounded processed-marker list inside the existing
#: creator block. Stored alongside (never inside) the Phase 1 anchor
#: payload keys so Phase 1 readers (which ignore unknown fields) keep
#: working byte-for-byte.
PROCESSED_GENERATION_IDS_FIELD = "processed_generation_ids"

# ---------------------------------------------------------------------------
# Canonical question detector (ONE detector feeds relationship evidence)
# ---------------------------------------------------------------------------
# Characterization of the three divergent implementations found in tree:
#
# * core/conversation_state._is_question: trailing "?" (regex ``\?\s*$``)
#   OR lowercased startswith one of 11 prefixes: what/how/when/where/who/
#   why/can you/could you/do you/are you/have you.
# * core/conversation_contract._is_question: trailing "?" OR regex match of
#   18 prefixes (adds will you/would you/did you/is/are/who's/what's). This
#   is the WIDEST on bare "?-less" declaratives ("are we done", "is it
#   late", "will you come" all True only here).
# * core/response_mode._is_question: endswith "?" OR 10 prefixes (lacks
#   "could you ").
#
# Per the Phase 2 mandate the conversation_state implementation is the
# canonical detector (conservative: fewer bare-declarative positives;
# prefers false over speculative true). CommerceSignals.fan_asks_question
# may corroborate but NEVER substitutes and NEVER creates a second vote.
# One message = one boolean.

_QUESTION_RE = re.compile(r"\?\s*$")
_CANONICAL_QUESTION_PREFIXES = (
    "what ",
    "how ",
    "when ",
    "where ",
    "who ",
    "why ",
    "can you ",
    "could you ",
    "do you ",
    "are you ",
    "have you ",
)


def is_canonical_question(text: Any) -> bool:
    """Canonical deterministic question detector (mirrors conversation_state).

    Pure, deterministic, no I/O. Exactly the ``core/conversation_state``
    semantics: trailing ``?`` (allowing trailing whitespace) or a
    lowercased interrogative prefix. Non-string input is False.
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if _QUESTION_RE.search(stripped):
        return True
    return stripped.lower().startswith(_CANONICAL_QUESTION_PREFIXES)


# ---------------------------------------------------------------------------
# Token helpers (deterministic, no embeddings, no semantic similarity)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Minimal stopword set so single-token overlap cannot be satisfied by
#: pure function words. Bounded and documented; not a semantic model.
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "have",
        "from",
        "they",
        "them",
        "then",
        "than",
        "when",
        "what",
        "where",
        "which",
        "would",
        "could",
        "should",
        "about",
        "into",
        "your",
        "yours",
        "are",
        "was",
        "were",
        "been",
        "being",
        "will",
        "just",
        "like",
        "more",
        "very",
        "much",
        "such",
        "only",
        "also",
        "how",
        "why",
        "who",
        "you",
        "yourself",
    }
)


def _tokenize(text: Any) -> set[str]:
    """Lowercase alphanumeric tokens, len>=3, minus stopwords (pure)."""
    if not isinstance(text, str) or not text:
        return set()
    return {
        tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) >= 3 and tok not in _STOPWORDS
    }


def _get_state_field(state: Any, name: str, default: Any = None) -> Any:
    """Read a ConversationState field from object OR dict (pure)."""
    if state is None:
        return default
    if isinstance(state, Mapping):
        return state.get(name, default)
    return getattr(state, name, default)


def _message_text(message: Any) -> str:
    if isinstance(message, Mapping):
        content = message.get("content", "")
        return content if isinstance(content, str) else ""
    return ""


def _message_is_user(message: Any) -> bool:
    if not isinstance(message, Mapping):
        return False
    role = message.get("direction", message.get("role", ""))
    return role in ("inbound", "user")


def _message_is_assistant(message: Any) -> bool:
    if not isinstance(message, Mapping):
        return False
    role = message.get("direction", message.get("role", ""))
    return role in ("outbound", "assistant", "model")


# ---------------------------------------------------------------------------
# Narrow typed inputs
# ---------------------------------------------------------------------------


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now


def _valid_llm_engagement(value: Any) -> float | None:
    """Validate the advisory LLM engagement float (pure, strict).

    Returns the float only when it is a real finite number in [0, 1].
    Booleans, strings, NaN/Inf, and out-of-range values yield None (absent).
    The value is passed through to Phase 1, which applies the exact Phase 1
    rule (high threshold + substantive behavioral signal required,
    informational corroboration counter only, never band promotion).
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


# ---------------------------------------------------------------------------
# Core extractor (pure function — ONE rule per evidence field)
# ---------------------------------------------------------------------------


def extract_turn_evidence(
    *,
    user_message: Any,
    history: Sequence[Mapping[str, Any]] | None = None,
    conversation_state: Any | None = None,
    contract: Any | None = None,  # accepted for signature stability; maintain_topic NEVER read
    lifecycle: str | None = None,
    ltm_explicit_hit: bool = False,
    fan_knowledge_explicit_hit: bool = False,
    open_loop_continued: bool = False,
    open_loop_resolved: bool = False,
    retrieved_memories: Sequence[Mapping[str, Any]] | None = None,
    retrieved_knowledge: Sequence[Mapping[str, Any]] | None = None,
    assistant_text: str | None = None,
    assistant_valid: bool = False,
    conversation_mode: str | None = None,
    llm_signals: Any | None = None,
    now: datetime | None = None,
) -> RelationshipTurnEvidence:
    """Normalize current-turn behavioral evidence (pure, deterministic).

    Exactly one rule assigns each ``RelationshipTurnEvidence`` field; see
    module docstring and ``docs/RELATIONSHIP_TRAJECTORY_PHASE2.md``. The
    ``contract`` argument exists so callers can pass the derived contract
    without the extractor depending on it: ``maintain_topic`` is never
    read (it is vacuously true whenever ``current_topic`` exists).

    ``llm_signals`` may only be validated ``CommerceSignals`` (the
    ``signals`` attribute of a validated ``OneCallResult``). Raw
    ``OneCallReply``, invalid payloads, and content-interest signals are
    never consumed; malformed input degrades to absent (None).
    """
    _ = _coerce_now(now)  # single-now threading: extraction is timeless;
    # the timestamp flows to accumulation/snapshot/persistence, not booleans.
    _ = contract  # intentionally unread: maintain_topic must not feed evidence.

    history_list = list(history) if history is not None else []
    text = user_message if isinstance(user_message, str) else ""

    # user_sent_message: transport/processing fact for the processed inbound
    # turn. Never derived from an LLM signal.
    user_sent = True

    # user_asked_question: ONE canonical deterministic detector. The LLM
    # fan_asks_question flag may corroborate but is NOT an independent vote
    # and can never substitute: the boolean equals the deterministic result.
    user_asked = is_canonical_question(text)

    # user_answered_question: existing ConversationState representation.
    # Preserve existing semantics: True only when a previous creator
    # question exists AND the state marks it answered (the current inbound
    # turn sits after it). No new answer detector is created here.
    last_q = _get_state_field(conversation_state, "last_question", None)
    last_q_answered = _get_state_field(conversation_state, "last_question_answered", False)
    user_answered = last_q is not None and last_q_answered is True

    # user_shared_information: deterministic explicit behavioral evidence
    # only, OR semantics. Either narrow explicit store hit yields one
    # boolean. Inferred/temporary profile LLM deltas are never consulted
    # (no such input exists on this function), and no raw text/spans are
    # persisted (booleans only).
    user_shared = bool(ltm_explicit_hit) or bool(fan_knowledge_explicit_hit)

    # user_continued_topic: narrow provisional overlap rule over existing
    # ConversationState topic/open-thread data. NEVER maintain_topic.
    # Requires the overlapping token to appear in BOTH the current message
    # and prior history (excluding the current turn), so a single message
    # cannot continue a topic it just created. Prefers false when the
    # representation cannot safely distinguish continuation. No semantic
    # similarity.
    user_continued = _continued_topic(text, history_list, conversation_state)

    # user_referenced_previous_context: explicit relationship-specific
    # threshold over existing retrieval output. Requires at least
    # RELATIONSHIP_RETRIEVAL_MIN_OVERLAP shared substantive tokens between
    # the current message and a retrieved subject/value. Deterministic,
    # named, provisional, boundary-tested. Never reuses the commercial 0.2
    # score filter; never introduces embeddings or a new retrieval
    # subsystem. Absent retrieval evidence yields False.
    user_referenced = _referenced_previous_context(text, retrieved_memories, retrieved_knowledge)

    # assistant_asked_question: actual outbound text only, via the same
    # canonical detector (consistent with the outbound history
    # representation). LLM intent is never consulted. Failed
    # generation/send paths (assistant_valid False, empty text) are False.
    assistant_asked = bool(assistant_valid) and is_canonical_question(assistant_text or "")

    # assistant_shared_information: provisional planner-derived proxy ONLY:
    # existing conversation_mode == "share" when the mode is available at
    # the extraction point. This is NOT a direct observation of assistant
    # content and is documented as such. No assistant-content classifier is
    # created. Unavailable mode yields False.
    assistant_shared = (
        isinstance(conversation_mode, str) and conversation_mode.strip().lower() == "share"
    )

    # session_returned: ONLY the existing ConversationLifecycle.RETURNING
    # definition. The explicit lifecycle argument is authoritative when
    # provided; otherwise the state's lifecycle field is read. No new
    # return timeout is created; LTM COMMITMENT "return" memories are never
    # consulted (no such input exists here).
    effective_lifecycle = lifecycle
    if effective_lifecycle is None:
        state_lifecycle = _get_state_field(conversation_state, "lifecycle", None)
        effective_lifecycle = state_lifecycle if isinstance(state_lifecycle, str) else None
    session_returned = (
        isinstance(effective_lifecycle, str) and effective_lifecycle.strip().lower() == "returning"
    )

    # open_loop_continued / open_loop_resolved: booleans computed by the
    # caller through the real long_term_memory implementation. The dead
    # commerce.open_loop path is never imported or called here. Absent
    # loops yield False. No new semantic open-loop subsystem.
    loop_continued = bool(open_loop_continued)
    loop_resolved = bool(open_loop_resolved)

    # LLM boundary: only validated CommerceSignals. relationship_engagement
    # passes through as an advisory float for Phase 1's exact corroboration
    # rule; fan_asks_question cannot create a second vote (boolean already
    # fixed above); primary_intent/intent tags never create evidence (not
    # read); content signals and raw evidence strings never enter (not
    # read).
    llm_engagement: float | None = None
    try:
        signals = llm_signals
        if signals is not None and type(signals).__name__ == "CommerceSignals":
            llm_engagement = _valid_llm_engagement(
                getattr(signals, "relationship_engagement", None)
            )
        else:
            llm_engagement = None
    except Exception:
        llm_engagement = None

    return RelationshipTurnEvidence(
        user_sent_message=user_sent,
        user_asked_question=user_asked,
        user_answered_question=user_answered,
        user_shared_information=user_shared,
        user_continued_topic=user_continued,
        user_referenced_previous_context=user_referenced,
        assistant_asked_question=assistant_asked,
        assistant_shared_information=assistant_shared,
        session_returned=session_returned,
        open_loop_continued=loop_continued,
        open_loop_resolved=loop_resolved,
        llm_relationship_engagement=llm_engagement,
    )


def _continued_topic(
    user_message: str,
    history: list[Mapping[str, Any]],
    conversation_state: Any | None,
) -> bool:
    """Narrow provisional overlap rule (pure). Never uses maintain_topic."""
    try:
        current_tokens = _tokenize(user_message)
        if not current_tokens:
            return False
        # Prior history excludes the current turn: callers pass DB history
        # without the current message, OR with it appended last. Handle
        # both: drop a trailing entry whose text equals the current message
        # when history is non-empty (avoids self-continuation).
        prior = history
        if prior:
            last_text = _message_text(prior[-1])
            if (
                isinstance(user_message, str)
                and last_text.strip() == user_message.strip()
                and _message_is_user(prior[-1])
            ):
                prior = prior[:-1]
        if not prior:
            return False
        prior_tokens: set[str] = set()
        for message in prior:
            # Only real conversation turns count as prior presence;
            # system/persona blocks must never satisfy continuation.
            if not (_message_is_user(message) or _message_is_assistant(message)):
                continue
            prior_tokens.update(_tokenize(_message_text(message)))
        if not prior_tokens:
            return False
        # State topic/open-thread vocabulary (existing representation).
        state_terms: set[str] = set()
        for field_name in ("current_topic", "recent_topics", "open_threads"):
            value = _get_state_field(conversation_state, field_name, None)
            if value is None:
                continue
            if isinstance(value, str):
                state_terms.update(_tokenize(value))
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, str):
                        state_terms.update(_tokenize(item))
        if not state_terms:
            return False
        # Continuation requires a shared substantive token present in the
        # current message, the state vocabulary, AND prior history text.
        shared = current_tokens & state_terms & prior_tokens
        return len(shared) >= RELATIONSHIP_TOPIC_MIN_OVERLAP
    except Exception:
        return False


def _referenced_previous_context(
    user_message: str,
    retrieved_memories: Sequence[Mapping[str, Any]] | None,
    retrieved_knowledge: Sequence[Mapping[str, Any]] | None,
) -> bool:
    """Relationship-specific threshold over existing retrieval output (pure).

    Scores each retrieved item by substantive token overlap between the
    current message and the item's subject/value. True when the best
    overlap meets RELATIONSHIP_RETRIEVAL_MIN_OVERLAP. No embeddings, no
    new retrieval subsystem, no commercial-threshold reuse.
    """
    try:
        current_tokens = _tokenize(user_message)
        if not current_tokens:
            return False
        candidates: list[Mapping[str, Any]] = []
        if retrieved_memories:
            candidates.extend([m for m in retrieved_memories if isinstance(m, Mapping)])
        if retrieved_knowledge:
            candidates.extend([k for k in retrieved_knowledge if isinstance(k, Mapping)])
        if not candidates:
            return False
        best = 0
        for item in candidates:
            try:
                subject = item.get("subject", "")
                value = item.get("value", "")
                item_tokens = _tokenize(subject) | _tokenize(value)
                overlap = len(current_tokens & item_tokens)
                if overlap > best:
                    best = overlap
            except Exception:
                continue
        return best >= RELATIONSHIP_RETRIEVAL_MIN_OVERLAP
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Narrow deterministic helpers for the worker (pure parts of disclosure)
# ---------------------------------------------------------------------------


def has_explicit_ltm_hit(text: Any, creator_id: int, user_id: int) -> bool:
    """True when a narrow explicit LTM extractor fires this turn (pure I/O-free).

    Only ``EXPLICIT`` (1.0) hits count. Weak-inference items (e.g. the
    movie-watching TOPIC at 0.5) never count. Never consults post-hoc
    profile LLM deltas. Never raises: failure yields False.
    """
    try:
        if not isinstance(text, str) or not text.strip():
            return False
        from commerce.long_term_memory import EXPLICIT, extract_explicit_memories

        items = extract_explicit_memories(text, int(creator_id), int(user_id))
        for item in items or []:
            try:
                if isinstance(item, Mapping) and float(item.get("confidence", 0.0)) == float(
                    EXPLICIT
                ):
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def has_explicit_fan_knowledge_hit(
    text: Any,
    creator_id: int,
    user_id: int,
    existing_knowledge: Sequence[Mapping[str, Any]] | None = None,
) -> bool:
    """True when an explicit fan-knowledge pattern matches this turn (pure).

    Only ``USER_EXPLICIT`` items at explicit confidence count; inferred or
    temporary profile information is never treated as disclosure (this
    helper never reads the profile LLM confidence map at all). Existing
    false-positive guards upstream (I-anchored city, city blacklist,
    capitalized pet names, pronoun antecedent checks) are preserved by
    calling the real extractor. Never raises: failure yields False.
    """
    try:
        if not isinstance(text, str) or not text.strip():
            return False
        from commerce.fan_knowledge import (
            CONFIDENCE_EXPLICIT,
            extract_fan_knowledge,
        )

        items = extract_fan_knowledge(
            text,
            int(creator_id),
            int(user_id),
            existing_knowledge=list(existing_knowledge) if existing_knowledge else None,
        )
        for item in items or []:
            try:
                as_dict = item.to_dict() if hasattr(item, "to_dict") else item
                if not isinstance(as_dict, Mapping):
                    continue
                if as_dict.get("source") != "USER_EXPLICIT":
                    continue
                if float(as_dict.get("confidence", 0.0)) < float(CONFIDENCE_EXPLICIT):
                    continue
                return True
            except Exception:
                continue
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Idempotent runtime accumulation (generation-keyed, atomic, bounded)
# ---------------------------------------------------------------------------


def _read_processed_ids(block: Any) -> list[str]:
    """Tolerantly read the bounded processed-marker list (pure)."""
    try:
        if not isinstance(block, dict):
            return []
        raw = block.get(PROCESSED_GENERATION_IDS_FIELD, [])
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                out.append(entry.strip()[:128])
        return out
    except Exception:
        return []


async def accumulate_relationship_turn_idempotent(
    *,
    user_id: int,
    creator_id: int,
    generation_id: str,
    evidence: RelationshipTurnEvidence,
    now: datetime | None = None,
    source: str = "relationship_evidence",
    max_processed_ids: int = RELATIONSHIP_PROCESSED_IDS_MAX,
) -> tuple[RelationshipAnchors | None, bool]:
    """Accumulate one turn exactly once per generation_id (fail-open).

    The marker check and the relationship anchor mutation occur atomically
    inside the existing row-locked ``mutate_user_profile_atomically``
    closure, in the existing relationship namespace
    (``relationship_trajectory_by_creator``). No separate Redis marker, no
    transitions-as-idempotency, no check-then-write outside the mutation,
    no unbounded list.

    * First ``generation_id``: accumulates via Phase 1
      :func:`accumulate_turn`, records the id, prunes to the bounded
      retention, persists only this creator's block (unrelated namespaces
      and other creators preserved).
    * Repeated ``generation_id`` (worker redelivery/retry): does not
      accumulate, does not increment counters, returns existing state.
    * The marker is creator-scoped (inside this creator's block) and
      user-scoped (inside this user's row): it survives worker retries via
      Postgres and never leaks across users or creators.

    Never raises: any failure (bad input, anchor load failure, persistence
    failure) returns ``(None, False)`` so conversation processing continues.
    """
    try:
        moment = _coerce_now(now)
        bounded_source = str(source)[:64] if source else "relationship_evidence"
        gen = generation_id.strip() if isinstance(generation_id, str) else ""
        if not gen:
            logger.warning("relationship idempotent accumulate skipped: empty generation_id")
            return None, False
        gen = gen[:128]
        try:
            uid = int(user_id)
            cid = int(creator_id)
        except Exception:
            logger.warning("relationship idempotent accumulate skipped: bad ids")
            return None, False
        if not isinstance(evidence, RelationshipTurnEvidence):
            logger.warning("relationship idempotent accumulate skipped: bad evidence")
            return None, False
        try:
            cap = int(max_processed_ids)
        except Exception:
            cap = RELATIONSHIP_PROCESSED_IDS_MAX
        cap = max(1, min(cap, 100))

        from db.postgres import mutate_user_profile_atomically

        result: dict[str, Any] = {"anchors": None, "did": False}

        def _mutate(facts: dict[str, Any]) -> bool:
            try:
                by_creator = facts.get(RELATIONSHIP_TRAJECTORY_KEY, {})
                if not isinstance(by_creator, dict):
                    by_creator = {}
                # Tolerate int keys in hand-built profiles (same fallback
                # order as Phase 1 / LTM / fan knowledge).
                block = by_creator.get(str(cid), {}) or by_creator.get(cid, {})
                if not isinstance(block, dict):
                    block = {}
                processed = _read_processed_ids(block)
                if gen in processed:
                    result["anchors"] = anchors_from_dict(block, moment)
                    result["did"] = False
                    return False
                base = anchors_from_dict(block, moment) if block else neutral_anchors(moment)
                # Preserve cross-check: Phase 1 loader on the same block.
                try:
                    _ = get_relationship_anchors(
                        {RELATIONSHIP_TRAJECTORY_KEY: {str(cid): block}}, cid
                    )
                except Exception:
                    pass
                updated = accumulate_turn(base, evidence, moment, source=bounded_source)
                processed.append(gen)
                if len(processed) > cap:
                    processed = processed[-cap:]
                new_block = anchors_to_dict(updated)
                new_block[PROCESSED_GENERATION_IDS_FIELD] = list(processed)
                by_creator[str(cid)] = new_block
                facts[RELATIONSHIP_TRAJECTORY_KEY] = by_creator
                result["anchors"] = updated
                result["did"] = True
                return True
            except Exception:
                logger.warning("relationship idempotent mutate failed (fail-open)", exc_info=True)
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
        logger.warning("accumulate_relationship_turn_idempotent failed (fail-open)", exc_info=True)
        return None, False


def snapshot_for_accumulated(
    anchors: RelationshipAnchors | None,
    evidence: RelationshipTurnEvidence | None = None,
    now: datetime | None = None,
) -> Any:
    """Derive the deterministic snapshot view (pure, fail-open)."""
    try:
        return derive_relationship_snapshot(anchors, evidence, _coerce_now(now))
    except Exception:
        logger.warning("snapshot derivation failed (fail-open)", exc_info=True)
        return None


__all__ = [
    "PROCESSED_GENERATION_IDS_FIELD",
    "RELATIONSHIP_PROCESSED_IDS_MAX",
    "RELATIONSHIP_RETRIEVAL_MIN_OVERLAP",
    "RELATIONSHIP_TOPIC_MIN_OVERLAP",
    "accumulate_relationship_turn_idempotent",
    "extract_turn_evidence",
    "has_explicit_fan_knowledge_hit",
    "has_explicit_ltm_hit",
    "is_canonical_question",
    "snapshot_for_accumulated",
]
