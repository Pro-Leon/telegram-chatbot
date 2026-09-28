"""Phase 8 deterministic content-transition selector.

Maps one turn of :class:`ContentTransitionEvidence
<commerce.content_transition_evidence.ContentTransitionEvidence>` to one
bounded transition state:

    CURRENT USER CONTENT INTEREST
            |
    CONTENT TRANSITION SELECTOR (this module)
            |
    NATURAL CONVERSATIONAL GUIDANCE
            |
    LLM REALIZATION
            |
    EXISTING DETERMINISTIC COMMERCE WHEN ELIGIBLE

What this module is NOT:

* commerce authority (it never selects media, prices, eligibility,
  sealing, or execution; ``DEFER_TO_COMMERCE`` merely indicates the
  existing commerce subsystem should handle the turn).
* a strategy engine (Phase 4 ``commerce/conversation_strategy.py``
  remains the canonical move selector and is never called, wrapped, or
  extended here).
* a boundary engine (Phase 7 is authoritative; the already-computed
  effective boundary snapshot is consumed read-only as a veto).
* a sales funnel (relationship warmth, intimacy, sexual conversation,
  desire, temperature, persona teasing permission, historical content
  interest, and available product inventory can NEVER activate a
  transition — enforced structurally: this module accepts none of those
  inputs).

Selector vocabulary (closed)::

    NONE               — no content-transition guidance this turn.
    ACKNOWLEDGE_ONLY   — content interest present; answer naturally,
                         no commercial transition.
    BRIDGE             — clear current curiosity/continuity; a natural
                         conversational bridge is appropriate (guidance,
                         never an offer command).
    DEFER_TO_COMMERCE  — the turn already belongs to an existing
                         deterministic commercial path (explicit purchase
                         intent, price/access inquiry, direct content
                         request).

Precedence (earlier wins):

    1. No usable evidence / no current interest            -> NONE
    2. Current content disinterest                        -> NONE
    3. Boundary veto / unknown boundary state             -> NONE
    4. Explicit purchase intent or price/access inquiry   -> DEFER_TO_COMMERCE
    5. Explicit content request                           -> DEFER_TO_COMMERCE
    6. Curiosity + thread continuation                    -> BRIDGE
    7. Thread continuation alone                          -> BRIDGE
    8. Curiosity alone                                    -> ACKNOWLEDGE_ONLY
    9. Otherwise                                          -> NONE

Design rules enforced here (mirrors Phase 4 contracts):

* Pure and deterministic: no DB, no Redis, no network, no LLM, no
  randomness, no global mutable state, no clock.
* Fail-closed for guidance: unknown/degraded boundary state yields
  NONE (withhold autonomous guidance); the pipeline itself stays
  fail-open because this output is advisory text only.
* ``no_offer_from_warmth`` is always True on every decision (pinned by
  tests): warmth-derived offers are never authorized by this layer.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Any

from commerce.content_transition_evidence import ContentTransitionEvidence

logger = logging.getLogger("commerce.content_transition")


class ContentTransition(str, enum.Enum):
    """Closed transition vocabulary (string values are the contract)."""

    NONE = "NONE"
    ACKNOWLEDGE_ONLY = "ACKNOWLEDGE_ONLY"
    BRIDGE = "BRIDGE"
    DEFER_TO_COMMERCE = "DEFER_TO_COMMERCE"


class UserInterest(str, enum.Enum):
    """Bounded user-interest label for the turn."""

    NONE = "NONE"
    CURIOSITY = "CURIOSITY"
    REQUEST = "REQUEST"
    CONTINUATION = "CONTINUATION"
    PURCHASE = "PURCHASE"


class Realization(str, enum.Enum):
    """Bounded realization hint (guidance, never authority)."""

    NATURAL = "natural"
    DIRECT_RESPONSE = "direct_response"
    COMMERCE_HANDOFF = "commerce_handoff"


@dataclass(frozen=True)
class ContentTransitionDecision:
    """One deterministic transition decision (immutable, advisory)."""

    transition: ContentTransition = ContentTransition.NONE
    user_interest: UserInterest = UserInterest.NONE
    realization: Realization = Realization.NATURAL
    reason: tuple[str, ...] = ()
    no_offer_from_warmth: bool = True

    def is_empty(self) -> bool:
        """True when there is no guidance (render nothing)."""
        return self.transition is ContentTransition.NONE


_NEUTRAL_DECISION = ContentTransitionDecision()


# ---------------------------------------------------------------------------
# Boundary veto (read-only over the already-computed effective snapshot)
# ---------------------------------------------------------------------------

#: Boundary types that suppress autonomous content-transition guidance.
#: Manner-only constraints (NO_FLIRTING / NO_PET_NAME /
#: NO_PERSONAL_QUESTION) are deliberately absent: they constrain
#: realization downstream through Phase 7, never here.
_SUPPRESSING_BOUNDARIES = frozenset(
    {
        "NO_SEXUAL_TOPIC",
        "CHANGE_TOPIC",
        "STOP_CONVERSATION",
        "DO_NOT_CONTACT",
    }
)


def _boundary_veto(boundary_snapshot: Any | None) -> tuple[bool, str]:
    """Return (suppress, reason) for the effective boundary snapshot.

    Fail-closed: ``None`` or degraded/unknown state suppresses
    autonomous guidance (NONE). This never blocks commerce itself —
    downstream deterministic commerce evaluation runs independently.
    """
    try:
        if boundary_snapshot is None:
            return True, "BOUNDARY_UNKNOWN_FAIL_CLOSED"
        try:
            if bool(getattr(boundary_snapshot, "degraded", False)):
                return True, "BOUNDARY_DEGRADED_FAIL_CLOSED"
        except Exception:
            return True, "BOUNDARY_UNKNOWN_FAIL_CLOSED"
        try:
            active = getattr(boundary_snapshot, "active", None)
            if active is None:
                return True, "BOUNDARY_UNKNOWN_FAIL_CLOSED"
            types = frozenset(str(t).strip() for t in active if str(t).strip())
        except Exception:
            return True, "BOUNDARY_UNKNOWN_FAIL_CLOSED"
        if "STOP_CONVERSATION" in types:
            return True, "BOUNDARY_STOP"
        try:
            if bool(getattr(boundary_snapshot, "blocks_contact", lambda: False)()):
                return True, "BOUNDARY_NO_CONTACT"
        except Exception:
            pass
        if "DO_NOT_CONTACT" in types:
            return True, "BOUNDARY_NO_CONTACT"
        if "CHANGE_TOPIC" in types:
            return True, "BOUNDARY_CHANGE_TOPIC"
        if "NO_SEXUAL_TOPIC" in types:
            return True, "BOUNDARY_NO_SEXUAL_TOPIC"
        return False, ""
    except Exception:
        logger.debug("content transition boundary veto failed (fail-closed)", exc_info=True)
        return True, "BOUNDARY_UNKNOWN_FAIL_CLOSED"


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------


def select_content_transition(
    *,
    evidence: ContentTransitionEvidence | None = None,
    boundary_snapshot: Any | None = None,
) -> ContentTransitionDecision:
    """Select one transition state for the turn (pure, never raises).

    Args:
        evidence: current-turn deterministic evidence. ``None`` or a
            non-evidence value yields NONE.
        boundary_snapshot: already-computed effective boundary snapshot
            (read-only veto). ``None`` / degraded yields NONE.

    The selector consumes no relationship / intimacy / desire /
    temperature / history / inventory inputs: those cannot activate a
    transition by construction.
    """
    try:
        if not isinstance(evidence, ContentTransitionEvidence):
            return ContentTransitionDecision(
                transition=ContentTransition.NONE,
                user_interest=UserInterest.NONE,
                realization=Realization.NATURAL,
                reason=("NO_EVIDENCE",),
            )
        # Current disinterest suppresses everything (history cannot
        # override it: history is not even an input here).
        if bool(evidence.current_disinterest):
            return ContentTransitionDecision(
                transition=ContentTransition.NONE,
                user_interest=UserInterest.NONE,
                realization=Realization.NATURAL,
                reason=("CURRENT_DISINTEREST_SUPPRESSES",),
            )
        # Phase 7 is authoritative: veto before any guidance.
        suppress, veto_reason = _boundary_veto(boundary_snapshot)
        if suppress:
            return ContentTransitionDecision(
                transition=ContentTransition.NONE,
                user_interest=UserInterest.NONE,
                realization=Realization.NATURAL,
                reason=(veto_reason,),
            )
        # Existing deterministic commercial boundaries own these turns.
        if bool(evidence.purchase_intent):
            return ContentTransitionDecision(
                transition=ContentTransition.DEFER_TO_COMMERCE,
                user_interest=UserInterest.PURCHASE,
                realization=Realization.COMMERCE_HANDOFF,
                reason=("EXPLICIT_PURCHASE_INTENT",),
            )
        if bool(evidence.access_question):
            return ContentTransitionDecision(
                transition=ContentTransition.DEFER_TO_COMMERCE,
                user_interest=UserInterest.PURCHASE,
                realization=Realization.COMMERCE_HANDOFF,
                reason=("PRICE_ACCESS_INQUIRY",),
            )
        if bool(evidence.explicit_request):
            return ContentTransitionDecision(
                transition=ContentTransition.DEFER_TO_COMMERCE,
                user_interest=UserInterest.REQUEST,
                realization=Realization.COMMERCE_HANDOFF,
                reason=("EXPLICIT_CONTENT_REQUEST",),
            )
        # Conversational bridging (guidance only, never an offer).
        if bool(evidence.curiosity) and bool(evidence.thread_continuation):
            return ContentTransitionDecision(
                transition=ContentTransition.BRIDGE,
                user_interest=UserInterest.CONTINUATION,
                realization=Realization.NATURAL,
                reason=("CURIOSITY_WITH_CONTINUATION",),
            )
        if bool(evidence.thread_continuation):
            return ContentTransitionDecision(
                transition=ContentTransition.BRIDGE,
                user_interest=UserInterest.CONTINUATION,
                realization=Realization.NATURAL,
                reason=("THREAD_CONTINUATION",),
            )
        if bool(evidence.curiosity):
            return ContentTransitionDecision(
                transition=ContentTransition.ACKNOWLEDGE_ONLY,
                user_interest=UserInterest.CURIOSITY,
                realization=Realization.DIRECT_RESPONSE,
                reason=("CURIOSITY_ACKNOWLEDGE",),
            )
        return ContentTransitionDecision(
            transition=ContentTransition.NONE,
            user_interest=UserInterest.NONE,
            realization=Realization.NATURAL,
            reason=("NO_CURRENT_INTEREST",),
        )
    except Exception:
        logger.debug("content transition selection failed (fail-closed NONE)", exc_info=True)
        return _NEUTRAL_DECISION


__all__ = [
    "ContentTransition",
    "ContentTransitionDecision",
    "Realization",
    "UserInterest",
    "select_content_transition",
]
