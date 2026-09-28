"""Content-transition context selection -- Phase 8.

Single deterministic selection step between the current-turn
content-transition evidence (Phase 8, read-only here) plus the effective
boundary snapshot (Phase 7, read-only here), and context assembly::

    current-turn content evidence (Phase 8, read-only)
            +
    effective boundary snapshot (Phase 7, read-only)
            v
    ONE bounded selection (selector in commerce/content_transition.py)
            v
    ONE bounded categorical representation
    (``render_content_transition_context``)
            v
    Context Engine snapshot / legacy context list (guidance, advisory)

This module is architecturally parallel to, and independent from,
``context_engine/relationship_context.py`` (Phase 5),
``context_engine/intimacy_context.py`` (Phase 6), and
``context_engine/boundary_context.py`` (Phase 7). The four selectors
never share state; relationship, intimacy, boundary, and content
transition render as separate blocks in canonical order::

    RELATIONSHIP -> INTIMACY -> BOUNDARY -> CONTENT TRANSITION ->
    STRATEGY -> PERSONA BEHAVIOR -> PLAYER

Design rules (mirror Phase 5/6/7 contracts):

* Pure and deterministic: no DB, no Redis, no network, no LLM, no
  randomness, no global mutable state, no clock reads. The async
  ``assemble_content_transition_context`` helper only reads
  caller-supplied turn inputs and performs no writes; selection itself
  is pure (delegated to ``commerce.content_transition``).
* Data, not directives: the rendered block contains bounded categorical
  labels only (transition / user_interest / realization /
  no_offer_from_warmth) -- never raw user wording, never scores, never
  hidden commercial rules, never a specific paid item, never an offer
  command, never permission/consent vocabulary.
* Guidance only: the block is NOT commerce authority and NOT a
  strategy move. The existing deterministic commerce pipeline
  (decision / eligibility / opportunity / selection / free-photo) and
  the Phase 4 strategy selector run unchanged downstream; the Phase 7
  common validator still checks all final output.
* Fail-open for guidance: any unusable input yields NONE, and rendering
  NONE yields ``""`` so callers leave the prompt byte-identical.
  (Degraded/unknown boundary state yields NONE here -- fail-closed for
  autonomous guidance, fail-open for the pipeline.)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from commerce.content_transition import (
    ContentTransition,
    ContentTransitionDecision,
    Realization,
    UserInterest,
    select_content_transition,
)
from commerce.content_transition_evidence import (
    ContentTransitionEvidence,
    extract_content_transition_evidence,
)

logger = logging.getLogger("context_engine.content_transition_context")


# ---------------------------------------------------------------------------
# Bounds (part of the Phase 8 contract; deterministic, testable)
# ---------------------------------------------------------------------------

#: Soft token ceiling for the rendered block. Carved from the existing
#: advisory/context budget: no global budget increase.
MAX_CONTENT_TRANSITION_TOKENS = 120

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _count_tokens(text: str) -> int:
    try:
        return len(_TOKEN_RE.findall(text.lower()))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Selection (thin, pure wrapper over the canonical selector)
# ---------------------------------------------------------------------------


def select_content_transition_context(
    *,
    evidence: ContentTransitionEvidence | None = None,
    boundary_snapshot: Any | None = None,
) -> ContentTransitionDecision:
    """Select the content-transition guidance for this turn (pure).

    Delegates to :func:`commerce.content_transition.select_content_transition`.
    Missing inputs yield NONE (prompt stays byte-identical). Never raises.
    """
    try:
        return select_content_transition(evidence=evidence, boundary_snapshot=boundary_snapshot)
    except Exception:
        logger.debug("content transition selection failed (fail-open NONE)", exc_info=True)
        return ContentTransitionDecision()


async def assemble_content_transition_context(
    *,
    creator_id: int | None = None,
    user_id: int | None = None,
    current_message: str = "",
    conversation_state: Any | None = None,
    boundary_snapshot: Any | None = None,
    open_loop_subjects: Sequence[str] | None = None,
    has_prior_context_reference: bool = False,
) -> ContentTransitionDecision:
    """Assemble one turn of content-transition guidance (fail-open).

    Extracts deterministic current-turn evidence from ``current_message``
    plus the caller-supplied turn context, then selects the bounded
    transition. Performs zero DB/Redis writes, zero LLM calls, zero
    retrieval. Never raises: any failure yields NONE.

    ``creator_id`` / ``user_id`` are accepted for signature parity with
    the Phase 5/6/7 assemblers and for future observability; they never
    influence selection (history-derived state cannot activate a
    transition).
    """
    try:
        if not isinstance(current_message, str) or not current_message.strip():
            return ContentTransitionDecision()
        try:
            evidence = extract_content_transition_evidence(
                current_message,
                conversation_state=conversation_state,
                open_loop_subjects=open_loop_subjects,
                has_prior_context_reference=bool(has_prior_context_reference),
            )
        except Exception:
            return ContentTransitionDecision()
        return select_content_transition_context(
            evidence=evidence, boundary_snapshot=boundary_snapshot
        )
    except Exception:
        logger.debug("content transition assembly failed (fail-open NONE)", exc_info=True)
        return ContentTransitionDecision()


# ---------------------------------------------------------------------------
# Rendering (concise categorical block; no raw wording, no scores)
# ---------------------------------------------------------------------------

_HEADER = "CONTENT TRANSITION [DERIVED]"


def render_content_transition_context(
    decision: ContentTransitionDecision | None,
    *,
    max_tokens: int = MAX_CONTENT_TRANSITION_TOKENS,
) -> str:
    """Render the advisory content-transition block (pure, fail-open).

    Returns ``""`` when there is no guidance (NONE or unusable input)
    so callers skip appending and leave the prompt byte-identical to
    today. Otherwise renders only bounded categorical labels::

        CONTENT TRANSITION [DERIVED]
        - transition: BRIDGE
        - user_interest: CONTINUATION
        - realization: natural
        - no_offer_from_warmth: true
    """
    try:
        if not isinstance(decision, ContentTransitionDecision):
            return ""
        if decision.transition is ContentTransition.NONE:
            return ""
        try:
            transition = ContentTransition(decision.transition).value
        except Exception:
            return ""
        try:
            interest = UserInterest(decision.user_interest).value
        except Exception:
            return ""
        try:
            realization = Realization(decision.realization).value
        except Exception:
            return ""
        if transition not in (
            "ACKNOWLEDGE_ONLY",
            "BRIDGE",
            "DEFER_TO_COMMERCE",
        ):
            return ""
        if interest not in (
            "CURIOSITY",
            "REQUEST",
            "CONTINUATION",
            "PURCHASE",
        ):
            return ""
        if realization not in ("natural", "direct_response", "commerce_handoff"):
            return ""
        lines = [
            _HEADER,
            f"- transition: {transition}",
            f"- user_interest: {interest}",
            f"- realization: {realization}",
            "- no_offer_from_warmth: true",
        ]
        block = "\n".join(lines)
        if _count_tokens(block) > max_tokens:
            # Fixed vocabulary keeps this unreachable; fail-open empty
            # rather than inventing a shorter (lossy) encoding.
            return ""
        return block
    except Exception:
        logger.debug("content transition render failed (fail-open empty)", exc_info=True)
        return ""


@dataclass(frozen=True)
class ContentTransitionSelection:
    """Snapshot-carrier view of one turn's transition (immutable)."""

    transition: str = "NONE"
    user_interest: str = "NONE"
    realization: str = "natural"

    def is_empty(self) -> bool:
        return self.transition == "NONE"


def to_selection(decision: ContentTransitionDecision | None) -> ContentTransitionSelection:
    """Project a decision to its carrier view (pure, fail-open)."""
    try:
        if not isinstance(decision, ContentTransitionDecision):
            return ContentTransitionSelection()
        if decision.transition is ContentTransition.NONE:
            return ContentTransitionSelection()
        return ContentTransitionSelection(
            transition=str(decision.transition.value),
            user_interest=str(decision.user_interest.value),
            realization=str(decision.realization.value),
        )
    except Exception:
        return ContentTransitionSelection()


__all__ = [
    "MAX_CONTENT_TRANSITION_TOKENS",
    "ContentTransitionSelection",
    "assemble_content_transition_context",
    "render_content_transition_context",
    "select_content_transition_context",
    "to_selection",
]
