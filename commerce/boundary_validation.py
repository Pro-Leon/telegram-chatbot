"""Phase 7 deterministic output validation + safe completion.

Examines generated response TEXT against the active
:class:`commerce.boundary_state.BoundarySnapshot`. Prompt instructions
alone are insufficient; this is the deterministic enforcement half
(output side). The other half is the snapshot/context guidance carried
into the prompt plus the routing/commerce choke in the worker.

Design rules:

* Pure and deterministic: no DB, no Redis, no network, no LLM, no
  randomness. Bounded regexes only.
* Conservative per type: narrow lexical/structural checks, documented
  below. CHANGE_TOPIC cannot track the old topic without persisting
  conversation content (rejected by data minimization), so it uses the
  same question check as NO_PERSONAL_QUESTION: a question risks
  dragging the old topic back; a neutral statement passes.
* Safe completions are fixed templates (no LLM invention, no pet
  names, no questions where questions are barred, no pressure to
  continue). They are constructed to pass this same validator
  (pinned by tests) so replacement never loops.
* ``DO_NOT_CONTACT`` has no safe text: any autonomous outbound is a
  violation and the worker must suppress instead of queueing.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from commerce.boundary_state import BoundarySnapshot

logger = logging.getLogger("commerce.boundary_validation")


# ---------------------------------------------------------------------------
# Violation records + boundary actions (closed vocabularies)
# ---------------------------------------------------------------------------


class BoundaryAction(str):
    """Deterministic outcome for a violated turn."""

    NEUTRAL_CONTINUE = "neutral_continue"
    TOPIC_CHANGE = "topic_change"
    CLOSE = "close"
    SUPPRESS = "suppress"
    QUEUE_SAFE = "queue_safe"


@dataclass(frozen=True)
class BoundaryValidation:
    """Result of checking one draft against active constraints."""

    violated: bool
    violations: tuple[str, ...] = ()
    action: str = BoundaryAction.QUEUE_SAFE


# ---------------------------------------------------------------------------
# Lexical / structural checks (bounded, word-boundaried)
# ---------------------------------------------------------------------------

#: Pet-name vocatives. "love" deliberately excluded (FP: "I love pizza").
_PET_NAME_RE = re.compile(
    r"\b(babe|baby|sweetheart|honey|darling|cutie|sweetie|angel|princess)\b",
    re.IGNORECASE,
)

#: Flirtatious realization markers. Narrow: explicit flirt stems and
#: come-ons, not ordinary warmth ("miss you", "beautiful" pass).
_FLIRT_RE = re.compile(
    r"\b(flirt\w*|sexy|horny|naughty|turn\s+(?:you|u|ya)\s+on|kiss\s+me|"
    r"make\s+love|wink|cheeky|tease\s+me|turned\s+on)\b",
    re.IGNORECASE,
)

#: Sexual-topic markers. Output standard is stricter than the evidence
#: standard by design: while the constraint is active the topic is
#: avoided entirely, so a single marker violates.
_SEXUAL_RE = re.compile(
    r"\b(sex\b|sexual|sexy|horny|nude|naked|undress|kiss\s+me|make\s+love|"
    r"turn\s+me\s+on|turned\s+on|in\s+bed\s+with|touch\s+me|"
    r"take\s+off\s+your|orgasm|masturbat\w*|porn)\b",
    re.IGNORECASE,
)

#: Commerce markers for the STOP_CONVERSATION brevity rule (an offer
#: tacked onto a closing turn keeps pushing the conversation).
_COMMERCE_RE = re.compile(
    r"(\$|price|buy|purchase|ppv|tip|subscribe|discount|offer\b|vault\b)",
    re.IGNORECASE,
)

#: STOP_CONVERSATION allows only a brief close: cap words, no question,
#: no commerce push.
_STOP_MAX_WORDS = 40

#: Fixed safe templates (no pet names, no flirt, no sexual markers, no
#: questions, no pressure). Pinned by tests to pass this validator.
SAFE_NEUTRAL_CONTINUE = (
    "Got it — I'll keep things neutral and switch gears. Happy to chat about whatever you'd like."
)

SAFE_CLOSE = "Understood — I'll leave it here. Take care."


def _has_question(text: str) -> bool:
    try:
        return "?" in text
    except Exception:
        return False


def _word_count(text: str) -> int:
    try:
        return len(str(text).split())
    except Exception:
        return 0


def _check_no_pet_name(reply: str) -> bool:
    try:
        return bool(_PET_NAME_RE.search(reply))
    except Exception:
        return False


def _check_no_flirting(reply: str) -> bool:
    try:
        return bool(_FLIRT_RE.search(reply))
    except Exception:
        return False


def _check_no_sexual_topic(reply: str) -> bool:
    try:
        return bool(_SEXUAL_RE.search(reply))
    except Exception:
        return False


def _check_no_personal_question(reply: str) -> bool:
    # Any question risks a personal question; neutral statements pass.
    return _has_question(reply)


def _check_change_topic(reply: str) -> bool:
    # Without persisting the old topic (data minimization), a question
    # is the detectable continuation signal; statements pass.
    return _has_question(reply)


def _check_stop_conversation(reply: str) -> bool:
    try:
        if _has_question(reply):
            return True
        if _word_count(reply) > _STOP_MAX_WORDS:
            return True
        return bool(_COMMERCE_RE.search(reply))
    except Exception:
        return False


_CHECK_BY_TYPE = {
    "NO_FLIRTING": _check_no_flirting,
    "NO_SEXUAL_TOPIC": _check_no_sexual_topic,
    "NO_PET_NAME": _check_no_pet_name,
    "NO_PERSONAL_QUESTION": _check_no_personal_question,
    "CHANGE_TOPIC": _check_change_topic,
    "STOP_CONVERSATION": _check_stop_conversation,
    # DO_NOT_CONTACT handled structurally (any outbound violates).
}


def validate_reply_against_boundaries(
    reply: Any,
    active: Sequence[str] | BoundarySnapshot | None,
) -> BoundaryValidation:
    """Check draft text against active boundary types (pure, fail-open).

    ``active`` accepts a ``BoundarySnapshot`` or a plain sequence of
    boundary-type strings (tolerates unknown strings by ignoring them).
    Non-string/empty replies: no violation except under DO_NOT_CONTACT
    (any non-empty outbound violates) — an empty draft is handled by the
    existing invalid-output bar, not here.
    """
    try:
        if active is None:
            return BoundaryValidation(violated=False)
        if isinstance(active, BoundarySnapshot):
            types = tuple(active.active)
        else:
            try:
                types = tuple(str(t).strip() for t in active if str(t).strip())
            except Exception:
                return BoundaryValidation(violated=False)
        types = tuple(t for t in types if t in _CHECK_BY_TYPE or t == "DO_NOT_CONTACT")
        if not types:
            return BoundaryValidation(violated=False)

        text = reply if isinstance(reply, str) else ""
        violations: list[str] = []

        if "DO_NOT_CONTACT" in types and text.strip():
            violations.append("DO_NOT_CONTACT")
        for btype in types:
            if btype == "DO_NOT_CONTACT":
                continue
            check = _CHECK_BY_TYPE.get(btype)
            if check is None:
                continue
            try:
                if text.strip() and check(text):
                    violations.append(btype)
            except Exception:
                continue

        if not violations:
            return BoundaryValidation(violated=False)

        if "DO_NOT_CONTACT" in violations:
            action = BoundaryAction.SUPPRESS
        elif "STOP_CONVERSATION" in violations:
            action = BoundaryAction.CLOSE
        elif "CHANGE_TOPIC" in violations:
            action = BoundaryAction.TOPIC_CHANGE
        else:
            action = BoundaryAction.NEUTRAL_CONTINUE
        return BoundaryValidation(violated=True, violations=tuple(violations), action=action)
    except Exception:
        logger.debug("boundary reply validation failed (fail-open)", exc_info=True)
        return BoundaryValidation(violated=False)


def safe_completion_for(
    violations: Sequence[str] | BoundarySnapshot | None,
) -> str | None:
    """Deterministic fallback text for a violated turn (pure).

    Returns the fixed neutral template, the fixed close template, or
    None for DO_NOT_CONTACT (suppress: no autonomous text at all).
    The templates pass :func:`validate_reply_against_boundaries`
    (pinned by tests).
    """
    try:
        if violations is None:
            return SAFE_NEUTRAL_CONTINUE
        if isinstance(violations, BoundarySnapshot):
            types = tuple(violations.active)
        else:
            try:
                types = tuple(str(t).strip() for t in violations if str(t).strip())
            except Exception:
                return SAFE_NEUTRAL_CONTINUE
        if "DO_NOT_CONTACT" in types:
            return None
        if "STOP_CONVERSATION" in types:
            return SAFE_CLOSE
        return SAFE_NEUTRAL_CONTINUE
    except Exception:
        return SAFE_NEUTRAL_CONTINUE


__all__ = [
    "SAFE_CLOSE",
    "SAFE_NEUTRAL_CONTINUE",
    "BoundaryAction",
    "BoundaryValidation",
    "safe_completion_for",
    "validate_reply_against_boundaries",
]
