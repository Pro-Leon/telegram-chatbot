"""Phase 5: explicit first-message routing decision boundary.

Single identifiable choke point that answers, deterministically:

    Why was this generation sent, queued, or suppressed?

Precedence (fail-closed — first matching veto wins):

    1. invalid_output            OneCall output invalid / unvalidatable
    2. boundary_violation         deterministic Phase 7 boundary violation
                                  (generated draft violates an active
                                  user-established conversational
                                  constraint; safe completion queued for
                                  operator review, never auto-sent)
    3. excluded                  deterministic do-not-auto-reply exclusion
    4. commerce_deny             deterministic commerce denial
    5. commerce_handoff_required deterministic commerce handoff
                                  (canonical OPERATOR_HANDOFF decision or
                                  active deterministic handoff memory)
    6. sealed_suppressed         a sealed commerce action exists for this turn
                                  but was not handed to the send stream, so an
                                  ordinary auto-send must not race it
    7. auto_reply_disabled       auto_reply is off
    8. safety_block              deterministic safety/persona hard block
    9. blocking_flags            any HARD-tier flag present (prompt_echo, safety,
                                commercial-authority, unknown); info-tier flags
                                (speaker/fan/repeat/markup/quality advisories)
                                are score-penalty-only, never a veto
    10. below_threshold           score/quality threshold not met
    11. corroborated_handoff     needs_handoff set by deterministic layers only
    12. approved                 otherwise (bare advisory handoff included)

needs_handoff policy (H3 — the LLM is advisory, never authority):

* The pipeline merges the model's advisory flag with deterministic handoff
  triggers (validation failure, safety flags, poor quality) into
  ``OneCallResult.needs_handoff``; the raw model flag is preserved
  separately as ``OneCallResult.advisory_handoff``.
* needs_handoff + any deterministic veto above  -> QUEUE (corroborated).
* needs_handoff from deterministic layers only -> QUEUE (corroborated).
* Bare advisory needs_handoff (valid output, all deterministic signals
  clean, score/flags gate otherwise passing) -> does NOT veto; the turn
  follows normal deterministic routing with the advisory signal recorded
  in routing/telemetry/event metadata. This prevents the model from
  creating a denial-of-service mechanism by returning needs_handoff=true
  for every message.

Commercial authority is untouched: this module takes caller-computed
booleans (price/product/eligibility/seal/execute all stay in the
deterministic commerce engine) and only maps them to a routing action.

Pure function, no I/O — safe to unit test with fakes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

# Phase 1.4 flag tiers: HARD vetoes routing even at high score; INFO is
# score-penalty-only (caps in pipeline/rails route those turns via
# below_threshold). Unknown future flags default hard (fail-closed).
# Cycle-safe: core.scoring never imports core.routing.
from core.scoring import HARD_FLAGS as _SAFETY_HARD_FLAGS


class RoutingAction(str, Enum):
    """Closed routing outcome set."""

    AUTO_SEND = "auto_send"
    QUEUE = "queue"
    SUPPRESS = "suppress"


# Stable machine-readable reason codes. Never free-form exception text.
REASON_INVALID_OUTPUT = "invalid_output"
REASON_BOUNDARY_VIOLATION = "boundary_violation"
REASON_EXCLUDED = "excluded"
REASON_COMMERCE_DENY = "commerce_deny"
REASON_COMMERCE_HANDOFF_REQUIRED = "commerce_handoff_required"
REASON_SEALED_SUPPRESSED = "sealed_suppressed"
REASON_AUTO_REPLY_DISABLED = "auto_reply_disabled"
REASON_SAFETY_BLOCK = "safety_block"
REASON_BLOCKING_FLAGS = "blocking_flags"
REASON_BELOW_THRESHOLD = "below_threshold"
REASON_CORROBORATED_HANDOFF = "corroborated_handoff"
REASON_APPROVED = "approved"


#: Phase 1.4 HARD tier: these flags veto routing even at high score.
#: Everything else is INFO (score-penalty-only via pipeline/rails caps).
#: Unknown future flags are NOT listed here — callers must treat unlisted
#: flags as hard (see ``_hard`` computation below, fail-closed).
HARD_QUALITY_FLAGS = frozenset({"prompt_echo", "unauthorized_commercial_cta", *_SAFETY_HARD_FLAGS})

#: Phase 1.4 INFO tier: closed vocabulary of penalty-only flags. Any flag
#: not in HARD_QUALITY_FLAGS and not in INFO_QUALITY_FLAGS is treated as
#: HARD (fail-closed for unknown future flags).
INFO_QUALITY_FLAGS = frozenset(
    {
        # output-rails verdicts (prompt_echo and markup_echo classified above)
        "fan_word",
        "speaker_prefix",
        "repeat",
        "markup_echo",
        # deterministic quality advisories (core/scoring_deterministic.py)
        "too_formal",
        "too_generic",
        "repetitive",
        "short_reply",
        "no_grounding",
        "name_request_known",
        # conversational grounding advisories (core/one_call.py Phase 89R)
        "speaker_inversion",
        "character_as_player_inversion",
        "player_as_character_inversion",
        "unauthorized_player_speech",
        "player_agency_violation",
        "out_of_character",
        "unanswered_question",
        "topic_pivot",
        "generic_deflection",
        "speaker_prefix_leak",
    }
)


@dataclass(frozen=True)
class RoutingDecision:
    """Deterministic routing outcome for one generation."""

    action: RoutingAction
    reason: str
    # Raw advisory LLM handoff flag observed on this turn.
    advisory_handoff: bool = False
    # True when needs_handoff was corroborated by deterministic policy.
    corroborated_handoff: bool = False


def decide_routing(
    *,
    is_valid: bool,
    boundary_violation: bool = False,
    excluded: bool = False,
    commerce_deny: bool = False,
    commerce_handoff_required: bool = False,
    sealed_suppressed: bool = False,
    auto_reply_on: bool = True,
    safety_block: bool = False,
    score: float = 0.0,
    auto_approve_threshold: float = 0.8,
    has_blocking_flags: bool = False,
    needs_handoff: bool = False,
    advisory_handoff: bool = False,
    flags: Sequence[str] = (),
) -> RoutingDecision:
    """Map validated generation + deterministic state to a routing action.

    All inputs are caller-computed deterministic facts except the two
    advisory handoff signals. Deterministic given the same inputs.

    Phase 1.4 tiers: when ``flags`` is provided, only HARD-tier flags veto
    (``blocking_flags``); INFO-tier flags are score-penalty-only. A legacy
    ``has_blocking_flags=True`` with an empty ``flags`` list keeps vetoing.
    """
    try:
        _score = float(score)
    except (TypeError, ValueError):
        _score = 0.0

    def _queue(reason: str) -> RoutingDecision:
        return RoutingDecision(
            action=RoutingAction.QUEUE,
            reason=reason,
            advisory_handoff=bool(advisory_handoff),
            corroborated_handoff=bool(needs_handoff),
        )

    # Hard vetoes — fail closed, in precedence order.
    if not is_valid:
        return _queue(REASON_INVALID_OUTPUT)
    if boundary_violation:
        return _queue(REASON_BOUNDARY_VIOLATION)
    if excluded:
        return RoutingDecision(
            action=RoutingAction.SUPPRESS,
            reason=REASON_EXCLUDED,
            advisory_handoff=bool(advisory_handoff),
            corroborated_handoff=False,
        )
    if commerce_deny:
        return _queue(REASON_COMMERCE_DENY)
    if commerce_handoff_required:
        return _queue(REASON_COMMERCE_HANDOFF_REQUIRED)
    if sealed_suppressed:
        return _queue(REASON_SEALED_SUPPRESSED)
    if not auto_reply_on:
        return _queue(REASON_AUTO_REPLY_DISABLED)
    if safety_block:
        return _queue(REASON_SAFETY_BLOCK)
    _hard = any(f in HARD_QUALITY_FLAGS or f not in INFO_QUALITY_FLAGS for f in flags) or (
        has_blocking_flags and not flags
    )
    if _hard:
        return _queue(REASON_BLOCKING_FLAGS)
    if _score < float(auto_approve_threshold):
        return _queue(REASON_BELOW_THRESHOLD)
    if needs_handoff and not advisory_handoff:
        # Deterministic layers raised handoff with no other traceable veto
        # (e.g. quality-gate disapproval); still corroborated — fail closed.
        return RoutingDecision(
            action=RoutingAction.QUEUE,
            reason=REASON_CORROBORATED_HANDOFF,
            advisory_handoff=False,
            corroborated_handoff=True,
        )
    # Bare advisory needs_handoff (or no handoff at all): the model must not
    # veto by itself. Normal deterministic routing applies; the advisory
    # signal is preserved in metadata by the caller.
    return RoutingDecision(
        action=RoutingAction.AUTO_SEND,
        reason=REASON_APPROVED,
        advisory_handoff=bool(advisory_handoff),
        corroborated_handoff=False,
    )
