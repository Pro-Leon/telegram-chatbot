"""Phase 7 current-turn boundary evidence (deterministic, conservative).

Connects the current inbound turn to the Phase 7 durable boundary domain
(``commerce/boundary_state.py``).

CORE PRINCIPLE (enforced here):

* Phase 7 extracts evidence from the current turn only.
* The boundary state module decides durable constraints.
* The LLM never creates durable boundary state (corroboration at most,
  never authority).
* Commerce/intimacy/relationship signals never create boundary evidence.

This module is deliberately small and deterministic:

* :func:`extract_boundary_evidence` is a pure function (no Redis, no
  Postgres, no LLM provider, no worker runtime, no commerce imports).
  Importing this module has no side effects.
* Exactly one rule family assigns each ``BoundaryTurnEvidence`` field.
  No numeric "boundary score", no permission/consent inference.
* One processed turn yields exactly one ``BoundaryTurnEvidence`` object.
* Raw message text is never persisted here. Only bounded booleans flow
  into durable state.

Lexical discipline (mirrors the Phase 6 negation philosophy):

* Every pattern uses word boundaries (``\\b``); substring matches are
  impossible by construction (pinned by tests).
* A boundary requires an explicit behavioral-scope construction
  (directive verb + constrained behavior), never a bare keyword such as
  "stop", "don't", "no", or "too much" alone.
* Explicit false-positive guards (``don't forget ...``, quoted
  third-party speech, non-conversational objects such as pizza/music)
  are documented below and pinned by tests.
"""

from __future__ import annotations

import enum
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("commerce.boundary_evidence")


# ---------------------------------------------------------------------------
# Closed vocabularies (string values are part of the Phase 7 contract)
# ---------------------------------------------------------------------------


class BoundaryType(str, enum.Enum):
    """Behavioral-scope constraints (not moral judgment, not permission)."""

    NO_FLIRTING = "NO_FLIRTING"
    NO_SEXUAL_TOPIC = "NO_SEXUAL_TOPIC"
    NO_PET_NAME = "NO_PET_NAME"
    NO_PERSONAL_QUESTION = "NO_PERSONAL_QUESTION"
    CHANGE_TOPIC = "CHANGE_TOPIC"
    STOP_CONVERSATION = "STOP_CONVERSATION"
    DO_NOT_CONTACT = "DO_NOT_CONTACT"


class BoundaryScope(str, enum.Enum):
    """How broadly a boundary applies. Narrow by default."""

    TURN = "TURN"
    TOPIC = "TOPIC"
    MODE = "MODE"
    CONVERSATION = "CONVERSATION"
    CONTACT = "CONTACT"


#: Canonical scope per boundary type. A narrow boundary is never silently
#: broadened: pet-name/flirting/question constraints are MODE/CONVERSATION
#: (manner of speech), sexual/change-topic are TOPIC-scoped, and only an
#: explicit contact request reaches CONTACT scope.
BOUNDARY_SCOPES: dict[str, str] = {
    BoundaryType.NO_FLIRTING.value: BoundaryScope.MODE.value,
    BoundaryType.NO_SEXUAL_TOPIC.value: BoundaryScope.TOPIC.value,
    BoundaryType.NO_PET_NAME.value: BoundaryScope.MODE.value,
    BoundaryType.NO_PERSONAL_QUESTION.value: BoundaryScope.MODE.value,
    BoundaryType.CHANGE_TOPIC.value: BoundaryScope.TOPIC.value,
    BoundaryType.STOP_CONVERSATION.value: BoundaryScope.CONVERSATION.value,
    BoundaryType.DO_NOT_CONTACT.value: BoundaryScope.CONTACT.value,
}

#: Ordered type list (stable rendering / snapshot order).
BOUNDARY_TYPES: tuple[str, ...] = (
    BoundaryType.NO_FLIRTING.value,
    BoundaryType.NO_SEXUAL_TOPIC.value,
    BoundaryType.NO_PET_NAME.value,
    BoundaryType.NO_PERSONAL_QUESTION.value,
    BoundaryType.CHANGE_TOPIC.value,
    BoundaryType.STOP_CONVERSATION.value,
    BoundaryType.DO_NOT_CONTACT.value,
)


# ---------------------------------------------------------------------------
# Evidence object (turn-scoped, bounded booleans only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundaryTurnEvidence:
    """One turn of boundary evidence (immutable, advisory input only).

    ``*_asserted`` fields record that the user established the constraint
    this turn. ``*_relaxed`` fields record an explicit permission to lift
    it (never inferred from positive conversation). ``ambiguous`` marks a
    vague de-escalation signal with no durable effect.
    """

    no_flirting: bool = False
    no_sexual_topic: bool = False
    no_pet_name: bool = False
    no_personal_question: bool = False
    change_topic: bool = False
    stop_conversation: bool = False
    do_not_contact: bool = False

    relaxed_no_flirting: bool = False
    relaxed_no_sexual_topic: bool = False
    relaxed_no_pet_name: bool = False
    relaxed_no_personal_question: bool = False
    relaxed_stop_conversation: bool = False
    relaxed_do_not_contact: bool = False

    #: "right now / for now / ..." qualifier observed alongside a topic
    #: assertion. Only affects expiry of TOPIC-scoped constraints.
    temporary_qualifier: bool = False

    #: Vague signal (bare "stop", "that's too much") with no durable effect.
    ambiguous: bool = False

    def asserted_types(self) -> tuple[str, ...]:
        out: list[str] = []
        for name in BOUNDARY_TYPES:
            if getattr(self, _FIELD_BY_TYPE[name][0]):
                out.append(name)
        return tuple(out)

    def relaxed_types(self) -> tuple[str, ...]:
        out: list[str] = []
        for name in BOUNDARY_TYPES:
            relaxed_field = _FIELD_BY_TYPE[name][1]
            if relaxed_field is not None and getattr(self, relaxed_field):
                out.append(name)
        return tuple(out)

    def has_assertion(self) -> bool:
        return any(getattr(self, f) for f, _ in _FIELD_BY_TYPE.values())

    def has_relaxation(self) -> bool:
        return any(getattr(self, r) for _, r in _FIELD_BY_TYPE.values() if r is not None)

    def is_empty(self) -> bool:
        return (
            not self.has_assertion()
            and not self.has_relaxation()
            and not self.ambiguous
            and not self.temporary_qualifier
        )


#: Maps BoundaryType -> (asserted field, relaxed field or None).
#: CHANGE_TOPIC is temporary by design; expiry (not relaxation) ends it.
_FIELD_BY_TYPE: dict[str, tuple[str, str | None]] = {
    BoundaryType.NO_FLIRTING.value: ("no_flirting", "relaxed_no_flirting"),
    BoundaryType.NO_SEXUAL_TOPIC.value: ("no_sexual_topic", "relaxed_no_sexual_topic"),
    BoundaryType.NO_PET_NAME.value: ("no_pet_name", "relaxed_no_pet_name"),
    BoundaryType.NO_PERSONAL_QUESTION.value: (
        "no_personal_question",
        "relaxed_no_personal_question",
    ),
    BoundaryType.CHANGE_TOPIC.value: ("change_topic", None),
    BoundaryType.STOP_CONVERSATION.value: ("stop_conversation", "relaxed_stop_conversation"),
    BoundaryType.DO_NOT_CONTACT.value: ("do_not_contact", "relaxed_do_not_contact"),
}


# ---------------------------------------------------------------------------
# Lexical rule families (explicit constructions only)
# ---------------------------------------------------------------------------
# FP = known false-positive shape handled by the rule or a guard.
# Every alternative requires \b on both ends where applicable.

# Curly-apostrophe / fancy-quote normalization happens before matching, so
# patterns only need the ASCII forms (don't, can't, I'm).

#: Pet-name objects recognized after call/calling. FP: "call me tomorrow"
#: (no pet name -> no match); "love" excluded (too common: "I love pizza").
_PET_NAMES = r"(?:babe|baby|sweetheart|honey|darling|cutie|sweetie|angel|princess)"

#: Clause splitter (conservative sentence segmentation for the forget-guard
#: and quoted-speech guard).
_CLAUSE_SPLIT_RE = re.compile(r"[.!?;\n]+")

#: Third-party attribution ("he said ...", "my friend told me ...").
_ATTRIBUTION_RE = re.compile(
    r"\b(he|she|they|my (?:friend|mom|dad|sister|brother|ex|boss|mother|father))\b"
    r"[^.!?;\n]{0,40}\b(said|told|says|telling)\b",
    re.IGNORECASE,
)

#: Double-quoted spans (removed when attribution is present).
_QUOTED_RE = re.compile(r'"[^"]{1,200}"|\'[^\']{1,200}\'')

#: "don't forget ..." guard: a clause containing this never contributes a
#: "don't ..." assertion (FP: "don't forget to send the file").
_FORGET_RE = re.compile(r"\bdon'?t forget\b|\bdo not forget\b", re.IGNORECASE)

#: "don't mean ..." guard: a clause containing this never contributes an
#: assertion (FP: "I don't mean stop flirting" denies the meaning rather
#: than establishing a boundary). Narrow: only the denying construction;
#: "I mean stop flirting" still asserts (D3 guard).
_MEAN_RE = re.compile(r"\bdon'?t\s+mean\b|\bdidn'?t\s+mean\b|\bdo\s+not\s+mean\b", re.IGNORECASE)

#: Temporary qualifiers ("right now", "for now", ...). Only TOPIC-scoped
#: constraints honor these (see boundary_state expiry policy).
_TEMPORARY_RE = re.compile(
    r"\b(right now|for now|tonight|at the moment|currently|not today)\b",
    re.IGNORECASE,
)

# -- Assertion families ----------------------------------------------------

#: NO_FLIRTING: directive + flirt stem, or a direct manner complaint.
#: FP: "I'm not flirting" (self-report, no directive -> no match);
#: "stop the music" (no flirt stem -> no match).
_FLIRT_ASSERT_RES = (
    re.compile(r"\bstop\s+flirt\w*\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+flirt\b", re.IGNORECASE),
    re.compile(r"\bdo\s+not\s+flirt\b", re.IGNORECASE),
    re.compile(r"\bnever\s+flirt\b", re.IGNORECASE),
    re.compile(r"\bquit\s+flirt\w*\b", re.IGNORECASE),
    re.compile(r"\bno\s+more\s+flirt\w*\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+talk\s+to\s+me\s+like\s+that\b", re.IGNORECASE),
    re.compile(r"\bstop\s+talking\s+to\s+me\s+like\s+that\b", re.IGNORECASE),
    re.compile(r"\bdo\s+not\s+talk\s+to\s+me\s+like\s+that\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+speak\s+to\s+me\s+like\s+that\b", re.IGNORECASE),
)

#: "too much" only counts with an adjacent flirt/sexual/manner complement
#: (FP: "that's too much information about your day" -> no match; bare
#: "that's too much" -> ambiguous only, never an assertion).
_TOO_MUCH_FLIRT_RE = re.compile(
    r"\btoo\s+much\b[^.!?;\n]{0,40}\b(flirt\w*|sexual|sex\b|babe|"
    r"sweetheart|honey|questions?\b)",
    re.IGNORECASE,
)

#: NO_SEXUAL_TOPIC: explicit sexual-topic object required.
#: FP: "I don't want pizza" (non-sexual object -> no match).
_SEXUAL_ASSERT_RES = (
    re.compile(r"\bdon'?t\s+want\s+to\s+talk\s+about\s+sex\b", re.IGNORECASE),
    re.compile(r"\bdo\s+not\s+want\s+to\s+talk\s+about\s+sex\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+want\s+(?:any\s+)?sexual\s+talk\b", re.IGNORECASE),
    re.compile(r"\bno\s+sex\s+talk\b", re.IGNORECASE),
    re.compile(r"\bstop\s+(?:the\s+)?sex\s+talk\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+talk\s+about\s+sex\b", re.IGNORECASE),
    re.compile(r"\bnever\s+talk\s+about\s+sex\b", re.IGNORECASE),
    re.compile(r"\blet'?s\s+not\s+talk\s+about\s+sex\b", re.IGNORECASE),
)

#: General discomfort maps to manner + sexual-topic de-escalation (both
#: validators admit neutral continuation, so joint mapping is low-cost).
#: "not comfortable" REQUIRES the verb (FP: "comfortable bed" -> no match).
_DISCOMFORT_RES = (
    re.compile(r"\bnot\s+comfortable\s+with\b", re.IGNORECASE),
    re.compile(r"\buncomfortable\s+with\b", re.IGNORECASE),
    re.compile(r"\bi'?m\s+not\s+comfortable\b", re.IGNORECASE),
)

#: NO_PET_NAME: call/calling + pet-name object, or demonstrative "that".
#: FP: "call me tomorrow" (no pet name -> no match).
_PET_ASSERT_RES = (
    re.compile(r"\bdon'?t\s+call\s+me\s+" + _PET_NAMES + r"\b", re.IGNORECASE),
    re.compile(r"\bdo\s+not\s+call\s+me\s+" + _PET_NAMES + r"\b", re.IGNORECASE),
    re.compile(r"\bnever\s+call\s+me\s+" + _PET_NAMES + r"\b", re.IGNORECASE),
    re.compile(r"\bstop\s+calling\s+me\s+" + _PET_NAMES + r"\b", re.IGNORECASE),
    re.compile(r"\bquit\s+calling\s+me\s+" + _PET_NAMES + r"\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+call\s+me\s+that\b", re.IGNORECASE),
    re.compile(r"\bstop\s+calling\s+me\s+that\b", re.IGNORECASE),
    re.compile(r"\bnever\s+call\s+me\s+that\b", re.IGNORECASE),
)

#: NO_PERSONAL_QUESTION: ask-verb + question/personal object.
_NOQ_ASSERT_RES = (
    re.compile(r"\bdon'?t\s+ask\s+me\s+personal\b", re.IGNORECASE),
    re.compile(r"\bdo\s+not\s+ask\s+me\s+personal\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+ask\s+me\s+that\b", re.IGNORECASE),
    re.compile(r"\bstop\s+asking\s+(?:me\s+)?(?:personal|questions?\b|that)\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+ask\s+(?:me\s+)?(?:any\s+)?questions?\b", re.IGNORECASE),
    re.compile(r"\bno\s+more\s+questions?\b", re.IGNORECASE),
    re.compile(r"\bquit\s+asking\s+me\b", re.IGNORECASE),
)

#: CHANGE_TOPIC: explicit topic-move directive (no negation needed).
_CHANGE_ASSERT_RES = (
    re.compile(r"\btalk\s+about\s+something\s+else\b", re.IGNORECASE),
    re.compile(r"\bchange\s+the\s+subject\b", re.IGNORECASE),
    re.compile(r"\bchange\s+topic\b", re.IGNORECASE),
    re.compile(r"\blet'?s\s+(?:talk\s+about\s+)?something\s+else\b", re.IGNORECASE),
    re.compile(r"\bleave\s+that\s+alone\b", re.IGNORECASE),
    re.compile(r"\bleave\s+it\s+alone\b", re.IGNORECASE),
    re.compile(r"\blet'?s\s+move\s+on\b", re.IGNORECASE),
    re.compile(r"\bmoving\s+on\b", re.IGNORECASE),
)

#: STOP_CONVERSATION: end-this-conversation directives. "stop flirting"
#: never reaches this family (no flirt stem here); "stop the music"
#: never matches (complement required). Manner/topic complements
#: ("like that", "about X") never assert STOP: those stay with the
#: narrower manner rule or assert nothing (D1 guard).
_STOP_ASSERT_RES = (
    re.compile(r"\bleave\s+me\s+alone\b", re.IGNORECASE),
    re.compile(r"\bgo\s+away\b", re.IGNORECASE),
    re.compile(r"\bstop\s+talking\s+to\s+me\b(?!\s+(?:like\s+that|about\b))", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+talk\s+to\s+me\b(?!\s+(?:like\s+that|about\b))", re.IGNORECASE),
)

#: DO_NOT_CONTACT: future-contact directives. Requires a contact verb
#: (message/text/contact) or "again/ever" framing.
_CONTACT_ASSERT_RES = (
    re.compile(r"\bdon'?t\s+contact\s+me\b", re.IGNORECASE),
    re.compile(r"\bdo\s+not\s+contact\s+me\b", re.IGNORECASE),
    re.compile(r"\bnever\s+contact\s+me\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+message\s+me\b", re.IGNORECASE),
    re.compile(r"\bdo\s+not\s+message\s+me\b", re.IGNORECASE),
    re.compile(r"\bnever\s+message\s+me\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+text\s+me\b", re.IGNORECASE),
    re.compile(r"\bnever\s+text\s+me\b", re.IGNORECASE),
    re.compile(r"\bstop\s+(?:messaging|texting|contacting)\s+me\b", re.IGNORECASE),
)

# -- Relaxation families (explicit permission only) -------------------------

#: Ordinary positive conversation ("haha", "I missed you", thanks) never
#: matches these. Each requires a permission verb + boundary object.
_PET_RELAX_RES = (
    re.compile(r"\byou\s+can\s+call\s+me\b", re.IGNORECASE),
    re.compile(r"\bcall\s+me\s+" + _PET_NAMES + r"\b", re.IGNORECASE),
    re.compile(r"\bit'?s\s+(?:ok|okay|fine)\s+to\s+call\s+me\b", re.IGNORECASE),
    re.compile(r"\bi\s+don'?t\s+mind\s+.*\bcall", re.IGNORECASE),
    re.compile(r"\bgo\s+ahead.*\bcall\s+me\b", re.IGNORECASE),
)

_FLIRT_RELAX_RES = (
    re.compile(r"\byou\s+can\s+flirt\b", re.IGNORECASE),
    re.compile(r"\bflirt\s+with\s+me\b", re.IGNORECASE),
    re.compile(r"\bit'?s\s+(?:ok|okay|fine)\s+to\s+flirt\b", re.IGNORECASE),
    re.compile(r"\bgo\s+ahead.*\bflirt\b", re.IGNORECASE),
    re.compile(r"\bi\s+don'?t\s+mind\s+.*\bflirt", re.IGNORECASE),
)

_SEXUAL_RELAX_RES = (
    re.compile(r"\bwe\s+can\s+talk\s+about\s+(?:that|it|sex|this)\b", re.IGNORECASE),
    re.compile(
        r"\bit'?s\s+(?:ok|okay|fine)\s+to\s+talk\s+about\s+(?:sex|that|it)\b", re.IGNORECASE
    ),
    re.compile(r"\bactually.*\bwe\s+can\s+talk\s+about\b", re.IGNORECASE),
    re.compile(r"\blet'?s\s+talk\s+about\s+sex\b", re.IGNORECASE),
)

_NOQ_RELAX_RES = (
    re.compile(r"\byou\s+can\s+ask\b", re.IGNORECASE),
    re.compile(r"\bask\s+me\s+anything\b", re.IGNORECASE),
    re.compile(r"\bit'?s\s+(?:ok|okay|fine)\s+to\s+ask\b", re.IGNORECASE),
    re.compile(r"\bgo\s+ahead.*\bask\b", re.IGNORECASE),
)

_STOP_RELAX_RES = (
    re.compile(r"\bwe\s+can\s+keep\s+talk(?:ing)?\b", re.IGNORECASE),
    re.compile(
        r"\byou\s+can\s+(?:keep\s+)?(?:talk|message|text)(?:ing)?\s+(?:to\s+)?me\b", re.IGNORECASE
    ),
    re.compile(r"\bkeep\s+(?:talking|messaging)(?:\s+me)?\b", re.IGNORECASE),
)

_CONTACT_RELAX_RES = (
    re.compile(r"\byou\s+can\s+(?:message|text|contact)\s+me\b", re.IGNORECASE),
    re.compile(r"\bit'?s\s+(?:ok|okay|fine)\s+to\s+(?:message|text|contact)\s+me\b", re.IGNORECASE),
    re.compile(r"\bkeep\s+messaging\s+me\b", re.IGNORECASE),
    re.compile(r"\b(?:message|text)\s+me\s+(?:again|anytime|tomorrow|later)\b", re.IGNORECASE),
)

# -- Ambiguous-only signals (no durable effect) ------------------------------

#: Bare vagueness: never an assertion by itself.
_AMBIGUOUS_RES = (
    re.compile(r"^\s*stop\s*[.!]*\s*$", re.IGNORECASE),
    re.compile(r"\bstop\s+(?:it|that|this|please)\b", re.IGNORECASE),
    re.compile(r"\bthat'?s\s+too\s+much\b", re.IGNORECASE),
    re.compile(r"\bthat'?s\s+enough\b", re.IGNORECASE),
)


# ---------------------------------------------------------------------------
# Normalization + guards (pure)
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Lowercase-friendly normalization: quotes, apostrophes, whitespace."""
    try:
        cleaned = str(text)
    except Exception:
        return ""
    cleaned = (
        cleaned.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    # Ellipses / repeated punctuation are pauses, not clause boundaries:
    # "STOP... flirting?!" must still match "stop flirting". Collapse runs
    # of 2+ sentence-punctuation characters to a space (single trailing
    # punctuation is preserved for clause splitting below).
    try:
        cleaned = re.sub(r"[.!?;](?:\s*[.!?;])+", " ", cleaned)
    except Exception:
        pass
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _strip_attributed_quotes(normalized: str) -> str:
    """Remove double-quoted spans when third-party attribution is present.

    FP guard: ``he said "stop flirting"`` describes someone else and must
    not assert a boundary for this fan.
    """
    try:
        if _ATTRIBUTION_RE.search(normalized):
            return _QUOTED_RE.sub(" ", normalized)
    except Exception:
        pass
    return normalized


def _clauses_without_forget(normalized: str) -> list[str]:
    """Split into clauses, dropping ``don't forget ...`` clauses."""
    try:
        parts = _CLAUSE_SPLIT_RE.split(normalized)
    except Exception:
        return [normalized]
    out: list[str] = []
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        try:
            if _FORGET_RE.search(piece):
                continue
            if _MEAN_RE.search(piece):
                continue
        except Exception:
            pass
        out.append(piece)
    return out


def _any_match(patterns: Sequence[re.Pattern[str]], clauses: Sequence[str]) -> bool:
    for clause in clauses:
        for pattern in patterns:
            try:
                if pattern.search(clause):
                    return True
            except Exception:
                continue
    return False


# ---------------------------------------------------------------------------
# Public extractor (pure; never raises — unusable input yields neutral)
# ---------------------------------------------------------------------------


def extract_boundary_evidence(
    user_message: Any,
    *,
    recent_assistant_texts: Sequence[Any] | None = None,  # accepted, currently unused
) -> BoundaryTurnEvidence:
    """Extract turn-scoped boundary evidence from the current message.

    Pure function: no DB/Redis/LLM I/O, no history reads, no tone or
    signal inputs. ``recent_assistant_texts`` is accepted for future
    demonstrative resolution ("don't call me that") but assertions never
    depend on it today: "that" conservatively maps to NO_PET_NAME.

    Fail-open: any unusable input yields a neutral (empty) evidence.
    """
    try:
        if not isinstance(user_message, str) or not user_message.strip():
            return BoundaryTurnEvidence()
        normalized = _normalize(user_message)
        if not normalized:
            return BoundaryTurnEvidence()
        # Bound input (long pastes / transcripts stay cheap).
        if len(normalized) > 2000:
            normalized = normalized[:2000]
        dequoted = _strip_attributed_quotes(normalized)
        clauses = _clauses_without_forget(dequoted)
        if not clauses:
            return BoundaryTurnEvidence()

        no_flirting = _any_match(_FLIRT_ASSERT_RES, clauses) or bool(
            _TOO_MUCH_FLIRT_RE.search(dequoted)
        )
        no_sexual = _any_match(_SEXUAL_ASSERT_RES, clauses)
        discomfort = _any_match(_DISCOMFORT_RES, clauses)
        if discomfort:
            # Joint manner + sexual-topic de-escalation (see _DISCOMFORT_RES).
            no_flirting = True
            no_sexual = True
        no_pet = _any_match(_PET_ASSERT_RES, clauses)
        no_question = _any_match(_NOQ_ASSERT_RES, clauses)
        change_topic = _any_match(_CHANGE_ASSERT_RES, clauses)
        stop_conv = _any_match(_STOP_ASSERT_RES, clauses)
        do_not_contact = _any_match(_CONTACT_ASSERT_RES, clauses)

        temporary = bool(_TEMPORARY_RE.search(dequoted)) and (no_sexual or change_topic)

        relaxed_flirt = _any_match(_FLIRT_RELAX_RES, clauses)
        relaxed_sexual = _any_match(_SEXUAL_RELAX_RES, clauses)
        relaxed_pet = _any_match(_PET_RELAX_RES, clauses)
        relaxed_noq = _any_match(_NOQ_RELAX_RES, clauses)
        relaxed_stop = _any_match(_STOP_RELAX_RES, clauses)
        relaxed_contact = _any_match(_CONTACT_RELAX_RES, clauses)

        # Same-turn assertion wins over relaxation per type (explicit
        # current-turn requirement beats a hedged permission).
        if no_flirting:
            relaxed_flirt = False
        if no_sexual:
            relaxed_sexual = False
        if no_pet:
            relaxed_pet = False
        if no_question:
            relaxed_noq = False
        if stop_conv:
            relaxed_stop = False
        if do_not_contact:
            relaxed_contact = False

        has_assertion = any(
            (
                no_flirting,
                no_sexual,
                no_pet,
                no_question,
                change_topic,
                stop_conv,
                do_not_contact,
            )
        )
        ambiguous = False
        if not has_assertion:
            ambiguous = _any_match(_AMBIGUOUS_RES, clauses)

        return BoundaryTurnEvidence(
            no_flirting=no_flirting,
            no_sexual_topic=no_sexual,
            no_pet_name=no_pet,
            no_personal_question=no_question,
            change_topic=change_topic,
            stop_conversation=stop_conv,
            do_not_contact=do_not_contact,
            relaxed_no_flirting=relaxed_flirt,
            relaxed_no_sexual_topic=relaxed_sexual,
            relaxed_no_pet_name=relaxed_pet,
            relaxed_no_personal_question=relaxed_noq,
            relaxed_stop_conversation=relaxed_stop,
            relaxed_do_not_contact=relaxed_contact,
            temporary_qualifier=temporary,
            ambiguous=ambiguous,
        )
    except Exception:
        logger.debug("boundary evidence extraction failed (fail-open)", exc_info=True)
        return BoundaryTurnEvidence()


__all__ = [
    "BOUNDARY_SCOPES",
    "BOUNDARY_TYPES",
    "BoundaryScope",
    "BoundaryTurnEvidence",
    "BoundaryType",
    "extract_boundary_evidence",
]
