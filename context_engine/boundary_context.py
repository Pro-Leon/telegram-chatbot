"""Boundary-aware context selection -- Phase 7.

Single deterministic selection step between the durable boundary state
(Phase 7, read-only here) plus current-turn boundary evidence, and
context assembly::

    durable boundary constraints (Phase 7, read-only)
            +
    current-turn boundary evidence (Phase 7, read-only)
            v
    ONE bounded selection (this module)
            v
    ONE bounded data-only representation (``render_boundary_context``)
            v
    Context Engine snapshot / legacy context list (guidance, advisory)

This module is architecturally parallel to, and independent from,
``context_engine/relationship_context.py`` (Phase 5) and
``context_engine/intimacy_context.py`` (Phase 6). The three selectors
never share state; relationship, intimacy, and boundary render as
separate blocks.

Current evidence wins: the effective active set is durable constraints
merged with this turn's assertions minus this turn's relaxations
(``snapshot_with_current_evidence``). Historical intimacy/relationship
state is never consulted here and can never suppress a boundary.

Design rules (mirror Phase 5/6 contracts):

* Pure and deterministic: no DB, no Redis, no network, no LLM, no
  randomness, no global mutable state, no clock reads inside selection
  (callers may pass ``now``; default is UTC now). The async
  ``assemble_boundary_context`` helper only reads the already-fetched
  ``profile`` mapping and performs no writes.
* Data, not directives to the model about validity: the rendered block
  lists active constraints only -- never raw user wording, never
  permission/consent scores, never commerce vocabulary.
* Guidance only: the block is NOT the enforcement mechanism. Output
  validation (``commerce/boundary_validation.py``) plus routing and the
  commerce veto enforce deterministically downstream.
* Fail-open for guidance: any unusable input yields an empty selection;
  rendering an empty selection yields ``""`` so callers leave the prompt
  byte-identical. (Load failure is surfaced via ``degraded`` on the
  snapshot so the worker can fail closed at routing; that decision lives
  in the worker, not here.)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from commerce.boundary_evidence import BoundaryTurnEvidence
from commerce.boundary_state import (
    BoundarySnapshot,
    derive_boundary_snapshot,
    get_boundary_constraints,
    snapshot_with_current_evidence,
)

logger = logging.getLogger("context_engine.boundary_context")


# ---------------------------------------------------------------------------
# Bounds (part of the Phase 7 contract; deterministic, testable)
# ---------------------------------------------------------------------------

#: Soft token ceiling for the rendered block. Carved from the existing
#: advisory/context budget: no global budget increase.
MAX_BOUNDARY_TOKENS = 120

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _count_tokens(text: str) -> int:
    try:
        return len(_TOKEN_RE.findall(text.lower()))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Selection (pure over already-fetched inputs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundarySelection:
    """One deterministic boundary selection (immutable, advisory)."""

    active: tuple[str, ...] = ()
    recovering: tuple[str, ...] = ()
    degraded: bool = False

    def is_empty(self) -> bool:
        return not self.active and not self.degraded


def select_boundary_context(
    *,
    snapshot: BoundarySnapshot | None = None,
    constraints: Mapping[str, Any] | None = None,
    evidence: BoundaryTurnEvidence | None = None,
    now: datetime | None = None,
) -> BoundarySelection:
    """Select the boundary context for this turn (pure, never raises).

    Precedence: explicit ``snapshot`` wins; otherwise ``constraints`` +
    ``evidence`` merge via ``snapshot_with_current_evidence``. Missing
    inputs yield an empty selection (prompt stays byte-identical).
    """
    try:
        moment = now
        try:
            if moment is None:
                moment = datetime.now(UTC)
        except Exception:
            moment = None
        effective: BoundarySnapshot | None = None
        if isinstance(snapshot, BoundarySnapshot):
            effective = snapshot
            if isinstance(evidence, BoundaryTurnEvidence) and (
                evidence.has_assertion() or evidence.has_relaxation()
            ):
                # Current evidence refines even an explicit snapshot.
                try:
                    from commerce.boundary_state import BoundaryConstraint

                    base = {
                        t: BoundaryConstraint(boundary_type=t, scope="", status="ACTIVE")
                        for t in effective.active
                    }
                    effective = snapshot_with_current_evidence(base, evidence, moment)
                    # Preserve degraded from the explicit snapshot.
                    if snapshot.degraded and not effective.degraded:
                        effective = BoundarySnapshot(
                            active=effective.active,
                            recovering=effective.recovering,
                            expired=effective.expired,
                            degraded=True,
                        )
                except Exception:
                    effective = snapshot
        elif constraints is not None or isinstance(evidence, BoundaryTurnEvidence):
            base_map = constraints if isinstance(constraints, Mapping) else {}
            if isinstance(evidence, BoundaryTurnEvidence):
                effective = snapshot_with_current_evidence(base_map, evidence, moment)
            else:
                effective = derive_boundary_snapshot(base_map, moment)
        if effective is None:
            return BoundarySelection()
        return BoundarySelection(
            active=tuple(effective.active),
            recovering=tuple(effective.recovering),
            degraded=bool(effective.degraded),
        )
    except Exception:
        logger.debug("boundary selection failed (fail-open empty)", exc_info=True)
        return BoundarySelection()


async def assemble_boundary_context(
    *,
    creator_id: int | None,
    user_id: int,
    profile: Any | None = None,
    evidence: BoundaryTurnEvidence | None = None,
    snapshot: BoundarySnapshot | None = None,
    now: datetime | None = None,
) -> BoundarySelection:
    """Read-only assembly over the already-fetched profile (no writes).

    Uses ``profile`` directly (generation-local reuse; no PG round-trip).
    Never raises: unusable input yields an empty selection.
    """
    try:
        if isinstance(snapshot, BoundarySnapshot):
            return select_boundary_context(snapshot=snapshot, evidence=evidence, now=now)
        constraints: Mapping[str, Any] | None = None
        try:
            if isinstance(profile, Mapping) and creator_id is not None:
                constraints = get_boundary_constraints(profile, int(creator_id))
        except Exception:
            constraints = None
        if constraints is None and not isinstance(evidence, BoundaryTurnEvidence):
            return BoundarySelection()
        return select_boundary_context(constraints=constraints, evidence=evidence, now=now)
    except Exception:
        logger.debug("boundary assembly failed (fail-open empty)", exc_info=True)
        return BoundarySelection()


# ---------------------------------------------------------------------------
# Rendering (concise constraint list; no raw wording, no scores)
# ---------------------------------------------------------------------------

#: Fixed line per boundary type (no user text ever interpolated).
_CONSTRAINT_LINES: dict[str, str] = {
    "NO_FLIRTING": "- no flirting",
    "NO_SEXUAL_TOPIC": "- no sexual topic",
    "NO_PET_NAME": "- avoid pet names",
    "NO_PERSONAL_QUESTION": "- avoid personal questions",
    "CHANGE_TOPIC": "- respect topic change",
    "STOP_CONVERSATION": "- close conversation briefly",
    "DO_NOT_CONTACT": "- no autonomous contact",
}

_HEADER = "BOUNDARY CONTEXT [DERIVED]:"
_PRECEDENCE = "Boundary constraints outrank persona style below."


def render_boundary_context(selection: BoundarySelection | None) -> str:
    """Render the boundary block for prompt injection (pure, fail-open).

    Returns "" when there is nothing to render, so callers can skip
    appending and leave the prompt byte-identical to today.
    """
    try:
        if not isinstance(selection, BoundarySelection):
            return ""
        if selection.degraded:
            # Degraded load: do not invent constraints; the worker fails
            # closed at routing instead of guiding the model.
            return ""
        lines = [_CONSTRAINT_LINES[t] for t in selection.active if t in _CONSTRAINT_LINES]
        if not lines:
            return ""
        lines = lines[:7]
        block = _HEADER + "\nactive constraints:\n" + "\n".join(lines) + "\n" + _PRECEDENCE
        if _count_tokens(block) > MAX_BOUNDARY_TOKENS:
            # Fixed vocabulary keeps this unreachable; truncate defensively
            # by dropping trailing lines (order is stable).
            while (
                lines
                and _count_tokens(
                    _HEADER + "\nactive constraints:\n" + "\n".join(lines) + "\n" + _PRECEDENCE
                )
                > MAX_BOUNDARY_TOKENS
            ):
                lines.pop()
            if not lines:
                return ""
            block = _HEADER + "\nactive constraints:\n" + "\n".join(lines) + "\n" + _PRECEDENCE
        return block
    except Exception:
        logger.debug("boundary render failed (fail-open empty)", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Strategy constraint (worker-level post-selection; Phase 4 untouched)
# ---------------------------------------------------------------------------
# Boundary constrains the EXISTING Phase 4 output vocabulary. This helper
# builds an adjusted ConversationalStrategy using only existing moves,
# hints, question policies, and reason codes. It never imports mutable
# state and never modifies commerce/conversation_strategy.py.


def constrain_strategy_for_boundary(
    strategy: Any | None,
    active: tuple[str, ...] | BoundarySelection | BoundarySnapshot | None,
) -> Any | None:
    """Constrain a selected Phase 4 strategy by active boundaries (pure).

    * STOP_CONVERSATION / DO_NOT_CONTACT -> safe-default ACKNOWLEDGE
      (react / NO_QUESTION / LOW), the existing non-interrogative default.
    * NO_PERSONAL_QUESTION / CHANGE_TOPIC with a question policy ->
      downgrade to the selector's own no-question variants
      (EXPLORE->SHARE/share, CALLBACK stays callback, else NO_QUESTION).
    * Otherwise the strategy passes through unchanged.
    Returns the original object when no constraint applies (or None when
    the input is unusable and a constraint exists, letting callers keep
    the original).
    """
    try:
        if active is None:
            return strategy
        if isinstance(active, (BoundarySelection, BoundarySnapshot)):
            types = tuple(active.active)
        else:
            try:
                types = tuple(str(t).strip() for t in active if str(t).strip())
            except Exception:
                return strategy
        if not types:
            return strategy
        if strategy is None:
            return None
        try:
            from commerce.conversation_strategy import ConversationalStrategy
        except Exception:
            return strategy
        if not isinstance(strategy, ConversationalStrategy):
            return strategy

        hard_close = any(t in ("STOP_CONVERSATION", "DO_NOT_CONTACT") for t in types)
        if hard_close:
            try:
                return ConversationalStrategy(
                    move="ACKNOWLEDGE",
                    realization_hint="react",
                    question_policy="NO_QUESTION",
                    reason_codes=("NO_QUESTION_REQUIRED",),
                    confidence="LOW",
                )
            except Exception:
                return strategy

        block_questions = any(t in ("NO_PERSONAL_QUESTION", "CHANGE_TOPIC") for t in types)
        if block_questions and getattr(strategy, "question_policy", "") == ("ONE_NATURAL_QUESTION"):
            try:
                move = str(getattr(strategy, "move", "ACKNOWLEDGE"))
                if move == "EXPLORE":
                    return ConversationalStrategy(
                        move="SHARE",
                        realization_hint="share",
                        question_policy="NO_QUESTION",
                        reason_codes=tuple(getattr(strategy, "reason_codes", ()))
                        or ("NO_QUESTION_REQUIRED",),
                        confidence=str(getattr(strategy, "confidence", "MEDIUM")),
                    )
                return ConversationalStrategy(
                    move=move,
                    realization_hint=str(getattr(strategy, "realization_hint", "react")),
                    question_policy="NO_QUESTION",
                    reason_codes=tuple(getattr(strategy, "reason_codes", ()))
                    or ("NO_QUESTION_REQUIRED",),
                    confidence=str(getattr(strategy, "confidence", "MEDIUM")),
                )
            except Exception:
                return strategy
        return strategy
    except Exception:
        logger.debug("boundary strategy constraint failed (pass-through)", exc_info=True)
        return strategy


__all__ = [
    "MAX_BOUNDARY_TOKENS",
    "BoundarySelection",
    "assemble_boundary_context",
    "constrain_strategy_for_boundary",
    "render_boundary_context",
    "select_boundary_context",
]
