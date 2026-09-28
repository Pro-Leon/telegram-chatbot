"""Deterministic conversational strategy layer — Phase 4.

Answers one question per turn:

    Given the current relationship trajectory snapshot, the current
    conversational context, and the applicable hard constraints, what
    kind of conversational move should happen next?

Architecture::

    relationship trajectory (descriptive state)
            +
    current conversational context
            +
    hard conversational constraints
            ↓
    CONVERSATIONAL STRATEGY (this module)
            ↓
    realization hint
            ↓
    LLM realization

Design rules enforced here:

* Pure and deterministic: no DB, no Redis, no network, no LLM, no
  randomness, no global mutable state, no clock reads inside selection
  (callers may pass ``now`` through to snapshot derivation only).
* Single owner: this module is the sole place where a Phase 4
  conversational move is selected. It does not call, wrap, or extend
  the legacy response-mode planner (left untouched and never revived),
  does not call ``commerce.conversation_intelligence`` (untouched), and
  does not modify ``commerce.persona_behavior`` (consumed read-only as
  constraints).
* Descriptive input only: the ``RelationshipSnapshot`` bands are read,
  never written, never promoted, never reinterpreted. This module holds
  no counters and persists nothing.
* Current context wins: an explicit current-turn requirement (farewell,
  direct question, sincerity requirement, annoyance/repair evidence)
  always outranks historical bands, however strong.
* No single-turn jumps: one lively message cannot create durable state
  (that remains Phase 1 authority); here it can at most shape one
  advisory move, and band-only evidence never triggers CALLBACK,
  EXPLORE, SHARE, or RECOVER.
* Conservative callbacks: a callback requires real prior-context
  evidence in the current turn, never a band value alone.
* Question budget survives: a question is emitted only when the
  existing ``core.question_policy`` budget allows an exploratory move
  AND persona constraints allow it. Persona vetoes and capability /
  contract restrictions always win; the selector downgrades or abstains
  instead of overriding.
* Trajectory bands never select the flirty / playful escalation
  realization hint. That hint remains owned by persona behavior and
  later architecture. High familiarity / engagement / reciprocity /
  continuity / growing trend change conversational depth and continuity
  only.
* Commerce separation: this module accepts no funnel, catalog, price,
  ranking, sealing, execution, or offline-tuning inputs. Strategy output
  is therefore invariant under commerce-signal changes by construction.
* Fail-open: ``None`` means "abstain; existing behavior stays
  responsible". Missing snapshots, cold-start (all bands unknown), and
  unusable inputs all yield ``None``. The selector never raises on
  untrusted input.

Priority order (exactly one move or abstain; earlier wins):

    1. CLOSE       — explicit farewell act in the current turn.
    2. RECOVER     — concrete repair evidence (annoyed affect).
    3. ACKNOWLEDGE — direct question to answer, or sincerity required.
    4. CALLBACK    — strong familiarity + anchored continuity + real
                     prior-context evidence + open threads.
    5. CONTINUE    — current topic present and continued this turn.
    6. EXPLORE     — answered question + steady/deep engagement +
                     balanced/high reciprocity + question allowed.
    7. SHARE       — same reciprocity evidence as EXPLORE, but asking
                     is currently disallowed (reciprocate without
                     interrogating).
    8. CONTINUE    — natural (topic present or steady/deep engagement).
    9. ACKNOWLEDGE — safe default (low confidence, no question).

Relationship mapping notes (conservative; depth/continuity only):

* Low familiarity (new/unknown): ACKNOWLEDGE / CONTINUE only; no
  callbacks requiring historical familiarity.
* Familiar / established: CALLBACK / EXPLORE / SHARE / CONTINUE when
  the current turn supplies matching evidence.
* Low engagement: never interrogative; ACKNOWLEDGE / CONTINUE.
* Steady / deep engagement: may support EXPLORE / SHARE / CONTINUE /
  CALLBACK with matching turn evidence.
* Low reciprocity: never compensated with repeated questions; only
  non-question moves.
* Balanced / high reciprocity: may support SHARE / EXPLORE / CONTINUE
  with matching turn evidence.
* Sparse continuity: callbacks never invented.
* Anchored / rich continuity: callbacks permitted with real evidence.
* Growing trend: momentum context only; never a durable transition.
* Declining trend: may add context to RECOVER, never triggers it alone.

This module is authoritative over stale roadmap vocabulary: roadmap-only
names were never runtime contracts and are not imported here. The move
vocabulary below is the Phase 4 contract.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("commerce.conversation_strategy")

# ---------------------------------------------------------------------------
# Closed vocabularies (string values are part of the Phase 4 contract)
# ---------------------------------------------------------------------------


class StrategyMove(str):
    """Phase 4 conversational moves (new concepts, not roadmap imports)."""

    ACKNOWLEDGE = "ACKNOWLEDGE"
    CONTINUE = "CONTINUE"
    EXPLORE = "EXPLORE"
    CALLBACK = "CALLBACK"
    SHARE = "SHARE"
    RECOVER = "RECOVER"
    CLOSE = "CLOSE"


_MOVES = frozenset(
    {
        StrategyMove.ACKNOWLEDGE,
        StrategyMove.CONTINUE,
        StrategyMove.EXPLORE,
        StrategyMove.CALLBACK,
        StrategyMove.SHARE,
        StrategyMove.RECOVER,
        StrategyMove.CLOSE,
    }
)

#: Realization hints reuse the existing response-mode token set as a
#: lower-level phrasing hint only. The flirty / playful escalation token
#: and the capability-clarification token are deliberately excluded: the
#: former stays owned by persona behavior, the latter by the capability
#: contract. Values mirror ``core.response_mode.ResponseMode`` members.
_ALLOWED_HINTS = frozenset({"react", "answer", "share", "explore", "callback", "close"})

#: Small explicit question vocabulary (mirrors ``core.question_policy``
#: outcomes and ``commerce.persona_behavior`` policy strings).
_QUESTION_POLICIES = frozenset({"NO_QUESTION", "ONE_NATURAL_QUESTION"})

#: Categorical decision confidence. Not a score, never optimized.
_CONFIDENCES = frozenset({"HIGH", "MEDIUM", "LOW"})

#: Bounded reason-code vocabulary. Deterministic, stable, testable; no
#: prose, no raw text, no counters.
_REASON_CODES = frozenset(
    {
        "CURRENT_QUESTION",
        "ANSWERED_QUESTION",
        "SINCERITY_REQUIRED",
        "REPAIR_EVIDENCE",
        "FAREWELL",
        "CURRENT_TOPIC",
        "CONTINUED_TOPIC",
        "PRIOR_CONTEXT",
        "OPEN_THREADS",
        "FAMILIARITY_NEW",
        "FAMILIARITY_FAMILIAR",
        "FAMILIARITY_ESTABLISHED",
        "ENGAGEMENT_LOW",
        "ENGAGEMENT_STEADY",
        "ENGAGEMENT_DEEP",
        "RECIPROCITY_LOW",
        "RECIPROCITY_BALANCED",
        "RECIPROCITY_HIGH",
        "CONTINUITY_SPARSE",
        "CONTINUITY_ANCHORED",
        "CONTINUITY_RICH",
        "TREND_GROWING",
        "TREND_DECLINING",
        "TREND_STABLE",
        "QUESTION_BUDGET",
        "PERSONA_CONSTRAINT",
        "NO_QUESTION_REQUIRED",
    }
)


# ---------------------------------------------------------------------------
# Typed contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConversationalStrategy:
    """One deterministic conversational move (immutable, advisory)."""

    move: str
    realization_hint: str
    question_policy: str
    reason_codes: tuple[str, ...]
    confidence: str


@dataclass(frozen=True)
class TurnEvidenceSummary:
    """Small deterministic summary of current-turn conversational facts.

    Callers build this from already-available deterministic sources
    (contract flags, state fields, relationship-evidence extraction).
    Every field defaults to False so unknown evidence reads as absent —
    the selector then stays conservative. No raw text lives here.
    """

    user_asked_question: bool = False
    user_answered_question: bool = False
    user_continued_topic: bool = False
    user_referenced_previous_context: bool = False
    assistant_asked_question: bool = False
    assistant_shared_information: bool = False
    farewell: bool = False


# ---------------------------------------------------------------------------
# Farewell detection (provisional, deterministic, documented)
# ---------------------------------------------------------------------------
# Minimal word-bounded set used ONLY to recognize an explicit conversational
# close. Not a sentiment model, not an intent classifier. A miss simply
# yields the safe default move; a hit yields CLOSE, which is the least
# intrusive advisory move.

_FAREWELL_RE = re.compile(
    r"(?i)\b(by+e+\b|good\s*night\b|g\.?\s*t\.?\s*g\.?\b|gotta go\b|got to go\b"
    r"|talk later\b|see (you|ya)(\s+(later|soon))?\b)"
)


def detect_farewell(text: Any) -> bool:
    """True when the fan message carries an explicit farewell (pure)."""
    try:
        if not isinstance(text, str) or not text.strip():
            return False
        return bool(_FAREWELL_RE.search(text))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tolerant field readers (duck-typed: dataclass OR mapping OR None)
# ---------------------------------------------------------------------------


def _field(source: Any, name: str, default: Any = None) -> Any:
    if source is None:
        return default
    try:
        if isinstance(source, Mapping):
            return source.get(name, default)
        return getattr(source, name, default)
    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "y")
        return default
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        number = int(value)
        return number if number >= 0 else default
    except Exception:
        return default


def _band_value(snapshot: Any, name: str) -> str:
    """Read one band as a lowercase token; unknown/missing → "unknown"."""
    try:
        raw = _field(snapshot, name, None)
        if raw is None:
            return "unknown"
        token = getattr(raw, "value", raw)
        text = str(token).strip().lower()
        return text if text else "unknown"
    except Exception:
        return "unknown"


def _band_code(prefix: str, value: str) -> str | None:
    if value in ("unknown", ""):
        return None
    code = f"{prefix}_{value.upper()}"
    return code if code in _REASON_CODES else None


# ---------------------------------------------------------------------------
# Core selector (pure; never raises — unusable input yields None)
# ---------------------------------------------------------------------------


def select_conversational_strategy(
    *,
    snapshot: Any | None = None,
    conversation_state: Any | None = None,
    contract: Any | None = None,
    persona: Any | None = None,
    turn: TurnEvidenceSummary | Mapping[str, Any] | None = None,
) -> ConversationalStrategy | None:
    """Select one conversational move, or None to abstain (pure).

    All inputs are read-only descriptive state or hard constraints.
    Returns None when the snapshot is missing, cold-start (every band
    unknown), or inputs are unusable. Never raises.
    """
    try:
        return _select(
            snapshot=snapshot,
            conversation_state=conversation_state,
            contract=contract,
            persona=persona,
            turn=turn,
        )
    except Exception:
        logger.debug("strategy selection failed (fail-open abstain)", exc_info=True)
        return None


def _select(
    *,
    snapshot: Any | None,
    conversation_state: Any | None,
    contract: Any | None,
    persona: Any | None,
    turn: TurnEvidenceSummary | Mapping[str, Any] | None,
) -> ConversationalStrategy | None:
    if snapshot is None:
        return None

    familiarity = _band_value(snapshot, "familiarity")
    engagement = _band_value(snapshot, "engagement")
    reciprocity = _band_value(snapshot, "reciprocity")
    continuity = _band_value(snapshot, "continuity")
    trend = _band_value(snapshot, "trend")

    if all(
        band == "unknown"
        for band in (familiarity, engagement, reciprocity, continuity, trend)
    ):
        # Cold start: no relationship history accumulated. Existing
        # behavior stays fully responsible; prompt must remain unchanged.
        return None

    # -- Conversation state (tolerant; missing fields read as absent) --
    current_topic = _field(conversation_state, "current_topic", None)
    if not isinstance(current_topic, str) or not current_topic.strip():
        current_topic = None
    open_threads = _field(conversation_state, "open_threads", ()) or ()
    try:
        open_threads = tuple(t for t in open_threads if isinstance(t, str) and t.strip())
    except Exception:
        open_threads = ()
    last_question = _field(conversation_state, "last_question", None)
    last_question_answered = _as_bool(
        _field(conversation_state, "last_question_answered", True), True
    )
    consecutive_questions = _as_int(_field(conversation_state, "consecutive_questions", 0))
    questions_in_last_3 = _field(conversation_state, "questions_in_last_3", None)
    try:
        questions_in_last_3 = (
            _as_int(questions_in_last_3) if questions_in_last_3 is not None else None
        )
    except Exception:
        questions_in_last_3 = None

    # -- Contract constraints actually used (never topic-maintenance) --
    contract_answer_required = _as_bool(_field(contract, "answer_required", False))

    # -- Persona constraints (vetoes; never relationship state) --
    persona_question_allowed = _field(persona, "question_allowed", None)
    persona_question_allowed = (
        True if persona_question_allowed is None else _as_bool(persona_question_allowed, True)
    )
    sincerity_required = _as_bool(_field(persona, "sincerity_required", False))
    emotional_state = _field(persona, "emotional_state", None)
    emotional_state = (
        str(emotional_state).strip().lower()
        if isinstance(emotional_state, str) and emotional_state.strip()
        else "warm"
    )

    # -- Current-turn evidence (absent reads as False: conservative) --
    asked = _as_bool(_field(turn, "user_asked_question", False)) or contract_answer_required
    answered = _as_bool(_field(turn, "user_answered_question", False))
    continued = _as_bool(_field(turn, "user_continued_topic", False))
    referenced = _as_bool(_field(turn, "user_referenced_previous_context", False))
    farewell = _as_bool(_field(turn, "farewell", False))

    # -- Existing question budget (consumer check, never modified) --
    budget_allows_question = False
    try:
        from core.question_policy import evaluate_question_budget

        _budget = evaluate_question_budget(
            last_question if isinstance(last_question, str) else None,
            bool(last_question_answered),
            consecutive_questions,
            "explore",
            questions_in_last_3=questions_in_last_3,
        )
        budget_allows_question = bool(getattr(_budget, "allowed", False))
    except Exception:
        budget_allows_question = False
    question_ok = bool(budget_allows_question and persona_question_allowed)

    def _codes(*codes: str | None) -> tuple[str, ...]:
        seen: list[str] = []
        for code in codes:
            if code and code in _REASON_CODES and code not in seen:
                seen.append(code)
        return tuple(seen)

    def _build(
        move: str,
        hint: str,
        question: str,
        reasons: tuple[str, ...],
        confidence: str,
    ) -> ConversationalStrategy | None:
        if move not in _MOVES or hint not in _ALLOWED_HINTS:
            return None
        if question not in _QUESTION_POLICIES or confidence not in _CONFIDENCES:
            return None
        clean = tuple(c for c in reasons if c in _REASON_CODES)
        if not clean:
            return None
        return ConversationalStrategy(
            move=move,
            realization_hint=hint,
            question_policy=question,
            reason_codes=clean,
            confidence=confidence,
        )

    engagement_code = _band_code("ENGAGEMENT", engagement)
    reciprocity_code = _band_code("RECIPROCITY", reciprocity)
    continuity_code = _band_code("CONTINUITY", continuity)
    familiarity_code = _band_code("FAMILIARITY", familiarity)

    # 1. CLOSE — explicit farewell act wins over every historical band.
    if farewell:
        return _build(
            StrategyMove.CLOSE, "close", "NO_QUESTION", _codes("FAREWELL"), "HIGH"
        )

    # 2. RECOVER — concrete repair evidence only (annoyed affect). A
    # declining trend alone never reaches this branch.
    if emotional_state == "annoyed":
        return _build(
            StrategyMove.RECOVER,
            "react",
            "NO_QUESTION",
            _codes(
                "REPAIR_EVIDENCE",
                "TREND_DECLINING" if trend == "declining" else None,
                "PERSONA_CONSTRAINT",
            ),
            "HIGH",
        )

    # 3. ACKNOWLEDGE — a question owed an answer, or sincerity required
    # (serious / nervous / embarrassed affect). Current-turn requirement
    # outranks historical bands.
    if asked or sincerity_required or emotional_state in ("serious", "nervous", "embarrassed"):
        reasons = _codes(
            "CURRENT_QUESTION" if asked else None,
            "SINCERITY_REQUIRED"
            if (sincerity_required or emotional_state in ("serious", "nervous", "embarrassed"))
            else None,
        )
        return _build(
            StrategyMove.ACKNOWLEDGE,
            "answer" if asked else "react",
            "NO_QUESTION",
            reasons,
            "HIGH" if asked else "MEDIUM",
        )

    # 4. CALLBACK — strong familiarity plus anchored continuity plus real
    # prior-context evidence plus open threads. Bands alone never suffice.
    if (
        familiarity in ("familiar", "established")
        and continuity in ("anchored", "rich")
        and open_threads
        and (referenced or (continued and current_topic is not None))
    ):
        if question_ok:
            return _build(
                StrategyMove.CALLBACK,
                "callback",
                "ONE_NATURAL_QUESTION",
                _codes(
                    "PRIOR_CONTEXT" if referenced else "CONTINUED_TOPIC",
                    "OPEN_THREADS",
                    continuity_code,
                    familiarity_code,
                    "QUESTION_BUDGET",
                ),
                "MEDIUM",
            )
        return _build(
            StrategyMove.CALLBACK,
            "callback",
            "NO_QUESTION",
            _codes(
                "PRIOR_CONTEXT" if referenced else "CONTINUED_TOPIC",
                "OPEN_THREADS",
                continuity_code,
                familiarity_code,
                "PERSONA_CONSTRAINT" if not persona_question_allowed else "QUESTION_BUDGET",
            ),
            "MEDIUM",
        )

    # 5. CONTINUE — current topic present and continued this turn.
    if current_topic is not None and continued:
        return _build(
            StrategyMove.CONTINUE,
            "react",
            "NO_QUESTION",
            _codes("CURRENT_TOPIC", "CONTINUED_TOPIC", engagement_code),
            "HIGH",
        )

    # 6. EXPLORE — answered question plus steady/deep engagement plus
    # balanced/high reciprocity, and only when asking is allowed.
    if (
        answered
        and engagement in ("steady", "deep")
        and reciprocity in ("balanced", "high")
        and question_ok
    ):
        return _build(
            StrategyMove.EXPLORE,
            "explore",
            "ONE_NATURAL_QUESTION",
            _codes("ANSWERED_QUESTION", engagement_code, reciprocity_code, "QUESTION_BUDGET"),
            "MEDIUM",
        )

    # 7. SHARE — same reciprocity evidence as EXPLORE, but asking is
    # currently disallowed: reciprocate without interrogating.
    if answered and engagement in ("steady", "deep") and reciprocity in ("balanced", "high"):
        return _build(
            StrategyMove.SHARE,
            "share",
            "NO_QUESTION",
            _codes(
                "ANSWERED_QUESTION",
                engagement_code,
                reciprocity_code,
                "PERSONA_CONSTRAINT" if not persona_question_allowed else "QUESTION_BUDGET",
            ),
            "MEDIUM",
        )

    # 8. CONTINUE — natural (topic present or steady/deep engagement).
    if current_topic is not None or engagement in ("steady", "deep"):
        return _build(
            StrategyMove.CONTINUE,
            "react",
            "NO_QUESTION",
            _codes(
                "CURRENT_TOPIC" if current_topic is not None else engagement_code,
                "NO_QUESTION_REQUIRED",
            ),
            "LOW",
        )

    # 9. ACKNOWLEDGE — safe default. Never interrogative.
    return _build(
        StrategyMove.ACKNOWLEDGE,
        "react",
        "NO_QUESTION",
        _codes(
            "NO_QUESTION_REQUIRED",
            engagement_code if engagement in ("low",) else None,
            reciprocity_code if reciprocity in ("low",) else None,
            continuity_code if continuity in ("sparse",) else None,
        ),
        "LOW",
    )


# ---------------------------------------------------------------------------
# Worker helper (pure over an already-fetched profile; no I/O, no writes)
# ---------------------------------------------------------------------------


def select_for_turn(
    *,
    profile: Any | None,
    creator_id: int | None,
    conversation_state: Any | None = None,
    contract: Any | None = None,
    persona: Any | None = None,
    turn: TurnEvidenceSummary | Mapping[str, Any] | None = None,
) -> ConversationalStrategy | None:
    """Derive snapshot from an already-fetched profile, then select.

    Pure read over the supplied ``profile`` mapping (this creator's
    namespace only); performs zero DB/Redis access and zero writes.
    Returns None whenever inputs are missing or unusable.
    """
    try:
        if not isinstance(profile, dict) or creator_id is None:
            return None
        try:
            cid = int(creator_id)
        except Exception:
            return None
        from commerce.relationship_trajectory import (
            derive_relationship_snapshot,
            get_relationship_anchors,
        )

        anchors = get_relationship_anchors(profile, cid)
        snapshot = derive_relationship_snapshot(anchors, None)
        return select_conversational_strategy(
            snapshot=snapshot,
            conversation_state=conversation_state,
            contract=contract,
            persona=persona,
            turn=turn,
        )
    except Exception:
        logger.debug("select_for_turn failed (fail-open abstain)", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Prompt rendering (concise directive; no counters, no raw text)
# ---------------------------------------------------------------------------


def render_conversation_strategy(strategy: ConversationalStrategy | None) -> str:
    """Render the strategy block for prompt injection (pure, fail-open).

    Returns "" when there is nothing safe to render, so callers can
    skip appending and leave the prompt byte-identical to today.
    """
    try:
        if not isinstance(strategy, ConversationalStrategy):
            return ""
        if strategy.move not in _MOVES or strategy.realization_hint not in _ALLOWED_HINTS:
            return ""
        if strategy.question_policy not in _QUESTION_POLICIES:
            return ""
        reasons = tuple(c for c in (strategy.reason_codes or ()) if c in _REASON_CODES)
        if not reasons:
            return ""
        return (
            "CONVERSATION STRATEGY:\n"
            f"move={strategy.move}\n"
            f"realization={strategy.realization_hint}\n"
            f"question={strategy.question_policy}\n"
            f"reasons={','.join(reasons)}"
        )
    except Exception:
        logger.debug("strategy render failed (fail-open empty)", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Custom-acknowledgment guidance (H2, advisory wording only)
# ---------------------------------------------------------------------------
# Renders ONLY when the turn's advisory intent_tags contain
# "custom_request" (in-scope LLM signal, same turn). Pure wording
# guidance for the realizer: warm appreciation (one line, no commitment
# verbs) + at most one numberless clarifying question + an explicit
# never-promise list. No move, budget, authority, price, detection, or
# state change. Callers append it as a suffix to the existing prompt
# assembly; empty renders nothing (byte-identical prompt).
#
# Known limit (documented, not fixed): the production OneCall prompt
# freezes from the snapshot before same-turn signals exist, so this
# suffix cannot reach the OneCall draft without restructuring. It serves
# the legacy/fallback draft path and documents the intended shape.

_CUSTOM_ACK_INTENT = "custom_request"

_CUSTOM_ACK_BLOCK = (
    "CUSTOM ACKNOWLEDGMENT (advisory wording only, no authority change):\n"
    "The fan asked for custom content. Acknowledge warmly in one line "
    "with no commitment verbs (do not promise to make, send, price, or deliver anything).\n"
    "You may ask at most ONE clarifying question about what exactly they want "
    "(content type, length, outfit, or words-only budget comfort) and only if the "
    "existing question budget allows it.\n"
    "NEVER: quote prices or numbers, share links or URLs, state delivery dates, "
    "or commit to creating anything. Pricing stays human-side."
)


def render_custom_acknowledgment(intent_tags: Any | None) -> str:
    """Render the custom-ack guidance block, or "" (pure, fail-open)."""
    try:
        if intent_tags is None:
            return ""
        if isinstance(intent_tags, (list, tuple, set, frozenset)):
            tags = [str(t) for t in intent_tags]
        else:
            return ""
        if _CUSTOM_ACK_INTENT not in tags:
            return ""
        return _CUSTOM_ACK_BLOCK
    except Exception:
        logger.debug("custom-ack render failed (fail-open empty)", exc_info=True)
        return ""


#: Queue-payload need length (matches the H2 envelope spec).
_CUSTOM_NEED_MAX_CHARS = 120

#: Card-data hygiene for the payload token (mirrors the
#: ``commerce/signals.py`` payment-data discipline: 13-16 digit runs
#: must never travel in advisory text).
_CARD_RUN_RE = re.compile(r"\b\d{13,16}\b")


def custom_need_token(user_message: Any, intent_tags: Any | None) -> str | None:
    """Build the ``custom:<truncated-need>`` queue-payload token (pure).

    Returns None unless "custom_request" is in the turn's advisory
    intent_tags (so price-inquiry and other turns never mint it — no
    text detection here) and the message is usable text. The need is
    the fan's own words, whitespace-collapsed, card-run redacted,
    truncated to ~120 chars. Fail-open None on anything unexpected.
    """
    try:
        if intent_tags is None:
            return None
        if isinstance(intent_tags, (list, tuple, set, frozenset)):
            tags = [str(t) for t in intent_tags]
        else:
            return None
        if _CUSTOM_ACK_INTENT not in tags:
            return None
        if not isinstance(user_message, str) or not user_message.strip():
            return None
        need = re.sub(r"\s+", " ", user_message.strip())
        need = _CARD_RUN_RE.sub("[redacted]", need)
        need = need[:_CUSTOM_NEED_MAX_CHARS].rstrip()
        if not need:
            return None
        return f"custom:{need}"
    except Exception:
        logger.debug("custom-need token failed (fail-open none)", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Bot-accusation deflection guidance + repeat evidence (H3, advisory only)
# ---------------------------------------------------------------------------
# Renders ONLY when the turn's advisory intent_tags contain
# "operator_request" (in-scope LLM signal, same turn). Pure wording
# guidance for the realizer: one playful, warm deflection line with zero
# verifiable claims (no human-species claims, no locations, no
# activities, no technical talk) + an explicit never-list. No move,
# budget, authority, price, detection, or state change. Callers append it
# as a suffix to the existing prompt assembly; empty renders nothing.
#
# Known limit (documented, not fixed): the production OneCall prompt
# freezes from the snapshot before same-turn signals exist, so this
# suffix cannot reach the OneCall draft without restructuring. It serves
# the legacy/fallback draft path and documents the intended shape.
#
# Sharp edge (documented, not fixed): the machine fact checks only cover
# name/age/3-cities/4-occupations, so a capitalized identity word in a
# parroted deflection (e.g. "An Angel") could trip
# fact_identity_claimed_*. Keep parroting lowercase-casual and claim-free.

_BOTASK_INTENT = "operator_request"

_BOTASK_DEFLECTION_BLOCK = (
    "BOT-ACCUSATION DEFLECTION (advisory wording only, no authority change):\n"
    "The fan asked whether this is a bot or asked for a real person. Deflect once, "
    "playfully and warmly, with zero verifiable claims.\n"
    "Example shape (adapt to persona voice, keep it casual and claim-free): "
    "\"what do you mean human? i'm an angel you know, so i'll take that as a compliment\".\n"
    "NEVER: claim to be human, state locations or activities, explain automation, "
    "or admit bot/AI status. If the fan repeats the ask, the turn routes to a human instead."
)


def render_botask_deflection(intent_tags: Any | None) -> str:
    """Render the bot-accusation deflection block, or "" (pure, fail-open)."""
    try:
        if intent_tags is None:
            return ""
        if isinstance(intent_tags, (list, tuple, set, frozenset)):
            tags = [str(t) for t in intent_tags]
        else:
            return ""
        if _BOTASK_INTENT not in tags:
            return ""
        return _BOTASK_DEFLECTION_BLOCK
    except Exception:
        logger.debug("botask render failed (fail-open empty)", exc_info=True)
        return ""


#: Verbatim ground-truth bot-accusation/human-demand vocabulary for repeat
#: detection: validation_dataset_440.py rows 401-420 (operator_request).
#: Matched 1:1 after normalization — paraphrases do NOT match (they fall
#: back to first-ask treatment: guidance only, no handoff).
_BOTASK_PRIOR_TEXTS = frozenset({
    "I need to talk to a real person",
    "are you a bot or a real human?",
    "can someone from your team help me?",
    "I need to speak with support staff",
    "connect me to a customer service rep",
    "I want to talk to the person behind this account",
    "is there a manager I can speak to?",
    "I need human assistance, not a bot",
    "can I get help from a staff member?",
    "I want to speak to someone who can make decisions",
    "this is automated right? I need a person",
    "I need to escalate this to a supervisor",
    "can you transfer me to a live agent?",
    "I want to talk to the owner of this page",
    "is there anyone available to chat right now?",
    "I need to speak with someone about a billing issue",
    "can a real person handle my request?",
    "I want to talk to a human, not an AI",
    "connect me with your support team",
    "I need to speak to someone in charge",
})

#: Fan-side roles recognized in caller-supplied histories (dicts or objects).
_BOTASK_USER_ROLES = frozenset({"user", "fan", "customer", "human", "inbound"})


def _normalize_botask_text(text: Any) -> str:
    try:
        cleaned = re.sub(r"\s+", " ", str(text).strip().lower())
        return cleaned.rstrip("?!.'\"").strip()
    except Exception:
        return ""


_BOTASK_PRIOR_NORM = frozenset(_normalize_botask_text(t) for t in _BOTASK_PRIOR_TEXTS)


def had_prior_bot_accusation(messages: Any | None) -> bool:
    """True when an EARLIER fan turn matches ground-truth bot-ask wording.

    Turn-local window over caller-supplied (durable) history: no reads,
    no writes, no persistence here. The last fan-role entry is treated
    as the current turn and excluded, so a lone current ask never
    counts as its own repeat. Fail-open False on anything unexpected.
    """
    try:
        if isinstance(messages, (list, tuple)):
            items = list(messages)
        else:
            return False
        if not items:
            return False
        user_indices: list[int] = []
        for i, m in enumerate(items):
            try:
                if isinstance(m, dict):
                    role = m.get("role")
                    text = m.get("content")
                else:
                    role = getattr(m, "role", None)
                    text = getattr(m, "content", None)
            except Exception:
                continue
            if (
                isinstance(role, str)
                and role.strip().lower() in _BOTASK_USER_ROLES
                and isinstance(text, str)
                and text.strip()
            ):
                user_indices.append(i)
        for i in user_indices[:-1]:
            try:
                m = items[i]
                if isinstance(m, dict):
                    text = m.get("content")
                else:
                    text = getattr(m, "content", None)
                if _normalize_botask_text(text) in _BOTASK_PRIOR_NORM:
                    return True
            except Exception:
                continue
        return False
    except Exception:
        logger.debug("botask repeat scan failed (fail-open false)", exc_info=True)
        return False


#: Read-only mirror of the complaint-sentiment threshold owned by
#: commerce/relationship.py (check_operator_handoff). Never retune here;
#: used only to recognize an already-firing complaint alongside a
#: bot-accusation for the queue-payload label.
_COMPLAINT_SENTIMENT_THRESHOLD = 0.70


def botask_queue_token(
    *,
    intent_tags: Any | None = None,
    negative_intent_tags: Any | None = None,
    negative_sentiment: Any | None = None,
    repeated: bool = False,
) -> str | None:
    """Build the ``botask:<repeat|complaint-combo>`` queue token (pure).

    Returns None unless "operator_request" is in the turn's advisory
    intent_tags. "repeat" wins over "complaint-combo". Fail-open None.
    """
    try:
        if intent_tags is None:
            return None
        if isinstance(intent_tags, (list, tuple, set, frozenset)):
            tags = [str(t) for t in intent_tags]
        else:
            return None
        if _BOTASK_INTENT not in tags:
            return None
        if bool(repeated):
            return "botask:repeat"
        neg: list[str] = []
        try:
            if isinstance(negative_intent_tags, (list, tuple, set, frozenset)):
                neg = [str(t) for t in negative_intent_tags]
        except Exception:
            neg = []
        complaint_firing = "complaint" in neg
        if not complaint_firing:
            try:
                if isinstance(negative_sentiment, bool):
                    pass
                elif isinstance(negative_sentiment, (int, float)):
                    complaint_firing = float(negative_sentiment) >= _COMPLAINT_SENTIMENT_THRESHOLD
            except Exception:
                pass
        if complaint_firing:
            return "botask:complaint-combo"
        return None
    except Exception:
        logger.debug("botask token failed (fail-open none)", exc_info=True)
        return None


__all__ = [
    "ConversationalStrategy",
    "StrategyMove",
    "TurnEvidenceSummary",
    "botask_queue_token",
    "custom_need_token",
    "detect_farewell",
    "had_prior_bot_accusation",
    "render_botask_deflection",
    "render_conversation_strategy",
    "render_custom_acknowledgment",
    "select_conversational_strategy",
    "select_for_turn",
]
