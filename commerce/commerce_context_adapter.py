"""Phase 9 deterministic commerce-context adapter (turn-scoped, advisory only).

Maps current-turn deterministic evidence into a bounded categorical
commerce context WITHOUT granting commerce authority.

Architecture::

    Relationship / Intimacy (descriptive, NOT inputs here)
            |
    Current user evidence (deterministic text)
            |
    Phase 8 Content Transition (transition + evidence, read-only)
            |
    Phase 9 Commerce Context Adapter (this module)
            |
    existing deterministic Commerce Decision (authority, unchanged)
            |
    eligibility -> ranking -> sealing -> execution

What this module IS:

* Pure and deterministic: no DB, no Redis, no queues, no files, no
  network, no clock, no LLM calls, no persistence, no randomness, no
  global mutable state. Importing this module has no side effects.
* Advisory/suppressive/contextual only: it NEVER selects media, prices,
  eligibility, sealing, execution, payment URLs, ranking scores, or
  recommendations. It has zero commerce authority by construction
  (verified by static tests: no such surface exists here).
* Turn-scoped: one inbound message yields exactly one
  :class:`CommerceContext`. No durable state, no profile mutation, no
  LTM writes, no migrations.

What this module is NOT (enforced structurally):

* relationship/intimacy authority: it accepts NO relationship
  trajectory bands, NO intimacy bands, NO relationship scores, NO raw
  history, NO inventory/products/prices, NO user profile, NO LTM text,
  NO arbitrary LLM text. ``RelationshipSnapshot`` /
  ``IntimacySnapshot`` are never consumed (a static test asserts the
  source contains no such reference).
* boundary authority: the effective boundary snapshot is consumed
  read-only (duck-typed, never imported from persistence modules) as
  a veto. Phase 7 remains authoritative; this adapter never duplicates
  Phase 7 state logic.
* commerce authority: ``DEFER_TO_COMMERCE`` is treated as a bounded
  context signal ("the conversational layer should defer handling to
  the existing commerce system"), never as direct authorization. The
  commerce system independently evaluates its own gates.
* a scoring model: no new scores, no thresholds on relationship /
  intimacy / warmth / sexuality / familiarity / engagement. LLM floats
  (purchase_intent, relationship_engagement, content_interest) are
  never inputs here and can never become authorization through this
  module.

Precedence (earlier wins):

    1. Unusable evidence                    -> neutral (fail-closed)
    2. Current content disinterest          -> disinterest_present=True,
                                               no content/commercial guidance
    3. Boundary veto / unknown boundary     -> neutral, basis NONE
                                               (never conflicts with Phase 7)
    4. Deterministic buy/price/photo        -> user_initiated_commercial=True
                                               + bounded authorization_basis
    5. Phase 8 DEFER_TO_COMMERCE           -> user_initiated_commercial=True
                                               (context signal only)
    6. Thread continuation                  -> continuation_context=True,
                                               basis CONTENT_CONTINUATION
                                               (never auto-PPV)
    7. Curiosity alone                      -> current_content_interest=True,
                                               basis NONE (acknowledge only)
    8. Otherwise                             -> warmth_without_commercial_evidence=True

Failure behavior: never raises. Any exception yields a fail-closed
neutral context (no new commercial evidence, basis NONE,
warmth_without_commercial_evidence=True so downstream suppresses
commercial framing). Failure can never produce a new commercial action.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("commerce.commerce_context_adapter")


class AuthorizationBasis(str, enum.Enum):
    """Bounded categorical authorization basis (context label only).

    These labels describe WHAT deterministic current-turn evidence the
    worker observed. They are telemetry/context labels, never grants:
    the existing commerce authority independently decides whether a
    commercial action is permitted.

    Forbidden bases (never members here, enforced by tests):
    RELATIONSHIP, INTIMACY, WARMTH, SEXUALITY, FAMILIARITY, ENGAGEMENT.
    """

    NONE = "NONE"
    EXPLICIT_TEXT = "EXPLICIT_TEXT"
    PRICE_INQUIRY = "PRICE_INQUIRY"
    PURCHASE_INTENT = "PURCHASE_INTENT"
    CONTENT_REQUEST = "CONTENT_REQUEST"
    CONTENT_CONTINUATION = "CONTENT_CONTINUATION"


_ALLOWED_BASES = frozenset(b.value for b in AuthorizationBasis)


@dataclass(frozen=True)
class CommerceContext:
    """One turn of bounded commerce context (immutable, advisory).

    All booleans default to the fail-closed conversational posture:
    no commercial evidence, warmth suppression active, basis NONE.
    """

    continuation_context: bool = False
    disinterest_present: bool = False
    user_initiated_commercial: bool = False
    current_content_interest: bool = False
    warmth_without_commercial_evidence: bool = True
    authorization_basis: str = AuthorizationBasis.NONE.value
    reason: tuple[str, ...] = ("NEUTRAL",)

    def has_commercial_evidence(self) -> bool:
        """True when this turn carries deterministic commercial evidence."""
        try:
            return (
                bool(self.user_initiated_commercial)
                and self.authorization_basis != AuthorizationBasis.NONE.value
            )
        except Exception:
            return False


_NEUTRAL = CommerceContext()


def _boundary_suppresses(boundary_snapshot: Any | None) -> tuple[bool, str]:
    """Return (suppress, reason) for the effective boundary snapshot.

    Read-only duck-typing: never imports boundary persistence modules.
    Fail-closed: None / degraded / unknown suppresses autonomous
    commercial guidance (basis NONE). This never blocks commerce itself;
    downstream deterministic commerce evaluation runs independently and
    consults Phase 7 directly as the stronger veto.
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
        # Stronger veto first (mirrors Phase 8, read-only).
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
        return True, "BOUNDARY_UNKNOWN_FAIL_CLOSED"


def _evidence_flag(evidence: Any | None, name: str) -> bool:
    try:
        if evidence is None:
            return False
        return bool(getattr(evidence, name, False))
    except Exception:
        return False


def _transition_is(transition_decision: Any | None, name: str) -> bool:
    try:
        if transition_decision is None:
            return False
        transition = getattr(transition_decision, "transition", None)
        if transition is None:
            return False
        # Enum or string tolerant.
        try:
            value = transition.value if hasattr(transition, "value") else str(transition)
        except Exception:
            value = str(transition)
        return str(value).strip().upper() == name
    except Exception:
        return False


def build_commerce_context(
    *,
    transition_decision: Any | None = None,
    transition_evidence: Any | None = None,
    boundary_snapshot: Any | None = None,
    deterministic_buy: bool = False,
    deterministic_price: bool = False,
    deterministic_photo_request: bool = False,
) -> CommerceContext:
    """Build one turn of bounded commerce context (pure, never raises).

    Args:
        transition_decision: Phase 8 :class:`ContentTransitionDecision`
            (read-only). Only its ``transition`` label is read.
        transition_evidence: Phase 8 :class:`ContentTransitionEvidence`
            (read-only). Only its bounded booleans are read.
        boundary_snapshot: effective Phase 7 snapshot (read-only veto).
        deterministic_buy: existing deterministic buy-intent verifier
            result for the current raw turn (caller-computed via
            ``commerce.purchase_intent.is_explicit_purchase_request``).
        deterministic_price: existing deterministic price-inquiry verifier
            result for the current raw turn (caller-computed via
            ``commerce.purchase_intent.is_price_inquiry``).
        deterministic_photo_request: existing deterministic photo/request
            result for the current raw turn where already available
            (caller-computed via ``commerce.free_photo_routing`` or
            Phase 8 explicit-request evidence). Advisory corroboration
            only; never authority.

    Returns:
        Exactly one :class:`CommerceContext`. Unusable input yields a
        fail-closed neutral context (basis NONE, warmth suppression
        active). Never authorizes product/price/offer/selection/sealing/
        execution.
    """
    try:
        # 1. Unusable evidence -> neutral fail-closed.
        # Evidence must be a Phase 8 evidence object (duck-typed); raw
        # strings, dicts, or None yield neutral.
        _has_evidence_shape = False
        try:
            if transition_evidence is not None:
                _has_evidence_shape = all(
                    hasattr(transition_evidence, attr)
                    for attr in (
                        "explicit_request",
                        "curiosity",
                        "access_question",
                        "thread_continuation",
                        "purchase_intent",
                        "current_disinterest",
                    )
                )
        except Exception:
            _has_evidence_shape = False
        if not _has_evidence_shape:
            return CommerceContext(
                continuation_context=False,
                disinterest_present=False,
                user_initiated_commercial=False,
                current_content_interest=False,
                warmth_without_commercial_evidence=True,
                authorization_basis=AuthorizationBasis.NONE.value,
                reason=("NO_EVIDENCE",),
            )

        # 2. Current disinterest suppresses content guidance. Historical
        # signals cannot override it (history is not an input here).
        # Deterministic buy/price still surface as user-initiated so the
        # existing commerce authority can independently evaluate purchase
        # transaction state (disinterest never overrides purchase state,
        # boundary, eligibility, or fulfillment).
        try:
            _disinterest = bool(getattr(transition_evidence, "current_disinterest", False))
        except Exception:
            _disinterest = False
        if _disinterest:
            try:
                _buy = bool(deterministic_buy)
            except Exception:
                _buy = False
            try:
                _price = bool(deterministic_price)
            except Exception:
                _price = False
            try:
                _photo = bool(deterministic_photo_request)
            except Exception:
                _photo = False
            _user_init = bool(_buy or _price or _photo)
            _basis = AuthorizationBasis.NONE.value
            if _buy:
                _basis = AuthorizationBasis.EXPLICIT_TEXT.value
            elif _price:
                _basis = AuthorizationBasis.PRICE_INQUIRY.value
            elif _photo:
                _basis = AuthorizationBasis.CONTENT_REQUEST.value
            return CommerceContext(
                continuation_context=False,
                disinterest_present=True,
                user_initiated_commercial=_user_init,
                current_content_interest=False,
                warmth_without_commercial_evidence=not _user_init,
                authorization_basis=_basis,
                reason=("CURRENT_DISINTEREST_SUPPRESSES",),
            )

        # 3. Boundary veto (read-only). Never creates guidance that
        # conflicts with Phase 7. Unknown/degraded fails closed.
        suppress, veto_reason = _boundary_suppresses(boundary_snapshot)
        if suppress:
            return CommerceContext(
                continuation_context=False,
                disinterest_present=False,
                user_initiated_commercial=False,
                current_content_interest=False,
                warmth_without_commercial_evidence=True,
                authorization_basis=AuthorizationBasis.NONE.value,
                reason=(veto_reason,),
            )

        # 4-5. Deterministic commercial evidence (caller-verified) plus
        # Phase 8 DEFER_TO_COMMERCE as a bounded context signal only.
        try:
            _buy = bool(deterministic_buy)
        except Exception:
            _buy = False
        try:
            _price = bool(deterministic_price)
        except Exception:
            _price = False
        try:
            _photo = bool(deterministic_photo_request)
        except Exception:
            _photo = False

        _ev_explicit = _evidence_flag(transition_evidence, "explicit_request")
        _ev_access = _evidence_flag(transition_evidence, "access_question")
        _ev_purchase = _evidence_flag(transition_evidence, "purchase_intent")
        _ev_curiosity = _evidence_flag(transition_evidence, "curiosity")
        _ev_continuation = _evidence_flag(transition_evidence, "thread_continuation")
        _is_defer = _transition_is(transition_decision, "DEFER_TO_COMMERCE")

        _user_init = bool(
            _buy or _price or _photo or _is_defer or _ev_purchase or _ev_access or _ev_explicit
        )

        # Current content interest: any current deterministic interest
        # flag (curiosity, request, access, continuation, purchase).
        # Warmth/intimacy/history/inventory are not inputs and can never
        # set this by construction.
        _current_interest = bool(
            _ev_explicit or _ev_curiosity or _ev_access or _ev_continuation or _ev_purchase
        )
        _continuation = bool(_ev_continuation)

        # Warmth-without-evidence: no deterministic commercial evidence
        # and no current content interest/continuation. Downstream must
        # preserve ordinary relationship conversation while suppressing
        # commercial framing and unnecessary content suggestion.
        _warmth = bool(not _user_init and not _current_interest and not _continuation)

        # Authorization basis (context label only, never a grant).
        # Precedence: explicit text > price > deterministic purchase >
        # content request > continuation > none. Curiosity alone stays
        # NONE (acknowledge only, never PPV).
        _basis = AuthorizationBasis.NONE.value
        _reason = "NO_CURRENT_INTEREST"
        if _buy:
            _basis = AuthorizationBasis.EXPLICIT_TEXT.value
            _reason = "DETERMINISTIC_BUY"
        elif _price:
            _basis = AuthorizationBasis.PRICE_INQUIRY.value
            _reason = "DETERMINISTIC_PRICE"
        elif _ev_purchase:
            _basis = AuthorizationBasis.PURCHASE_INTENT.value
            _reason = "DETERMINISTIC_PURCHASE_EVIDENCE"
        elif _photo or _ev_explicit:
            _basis = AuthorizationBasis.CONTENT_REQUEST.value
            _reason = "DETERMINISTIC_CONTENT_REQUEST"
        elif _ev_access:
            _basis = AuthorizationBasis.CONTENT_REQUEST.value
            _reason = "DETERMINISTIC_ACCESS_REQUEST"
        elif _continuation:
            _basis = AuthorizationBasis.CONTENT_CONTINUATION.value
            _reason = "THREAD_CONTINUATION"
        elif _ev_curiosity:
            _basis = AuthorizationBasis.NONE.value
            _reason = "CURIOSITY_ACKNOWLEDGE_ONLY"
        elif _is_defer:
            # DEFER without mapped deterministic flags (defensive):
            # context signal only, never authorization.
            _basis = AuthorizationBasis.NONE.value
            _reason = "DEFER_CONTEXT_ONLY"
        else:
            _basis = AuthorizationBasis.NONE.value
            _reason = "NO_CURRENT_INTEREST"

        # DEFER_TO_COMMERCE without any deterministic flag is still a
        # context signal (user_initiated already True via _is_defer), but
        # basis stays NONE unless a mapped flag above set it. This keeps
        # DEFER prompt-only, never direct authorization.
        return CommerceContext(
            continuation_context=_continuation,
            disinterest_present=False,
            user_initiated_commercial=_user_init,
            current_content_interest=_current_interest,
            warmth_without_commercial_evidence=_warmth,
            authorization_basis=_basis,
            reason=(_reason,),
        )
    except Exception:
        logger.debug("commerce context adapter failed (fail-closed neutral)", exc_info=True)
        return CommerceContext(
            continuation_context=False,
            disinterest_present=False,
            user_initiated_commercial=False,
            current_content_interest=False,
            warmth_without_commercial_evidence=True,
            authorization_basis=AuthorizationBasis.NONE.value,
            reason=("ADAPTER_FAIL_CLOSED",),
        )


def should_suppress_commercial_framing(context: CommerceContext | None) -> bool:
    """True when commercial framing must be suppressed this turn (pure).

    Suppression covers: warmth without commercial evidence, current
    disinterest, or missing authorization basis. Ordinary relationship
    conversation is preserved (this returns False only when genuine
    current commercial/content evidence exists). Never raises.
    """
    try:
        if not isinstance(context, CommerceContext):
            return True
        if bool(context.disinterest_present):
            return True
        if bool(context.warmth_without_commercial_evidence):
            return True
        if context.authorization_basis == AuthorizationBasis.NONE.value:
            # Continuation alone (basis CONTENT_CONTINUATION) does not
            # suppress (contextual bridge allowed); curiosity/NONE does.
            if bool(context.continuation_context):
                return False
            return True
        return False
    except Exception:
        return True


def downgrade_response_mode(response_mode: Any, context: CommerceContext | None) -> Any:
    """Downgrade commercial framing modes when suppression applies (pure).

    * ``tease`` (flirty/playful escalation) under
      ``warmth_without_commercial_evidence`` becomes ``react``
      (ordinary relationship response, no commercial framing).
    * All other modes pass through unchanged.
    * Never introduces a new mode, never revives a removed engine, never
      raises. ``None``/unusable input passes through as-is except when
      suppression demands a safe default (returns ``"react"`` for the
      tease case only).
    """
    try:
        if context is not None and isinstance(context, CommerceContext):
            suppress = should_suppress_commercial_framing(context)
        else:
            suppress = True
        if not suppress:
            return response_mode
        try:
            mode_str = str(response_mode).strip().lower() if response_mode is not None else ""
        except Exception:
            return response_mode
        if mode_str == "tease":
            return "react"
        return response_mode
    except Exception:
        return response_mode


def should_suppress_content_suggestion(context: CommerceContext | None) -> bool:
    """True when assistant-initiated content promotion must be suppressed.

    Invariant: inventory exists + no current content interest + no
    explicit commercial request -> no promotion. This helper reports
    suppression purely from the adapter context (inventory itself is
    never an input here). Never raises.
    """
    try:
        if not isinstance(context, CommerceContext):
            return True
        if bool(context.disinterest_present):
            return True
        if bool(context.warmth_without_commercial_evidence):
            return True
        # Explicit commercial evidence allows normal handling downstream
        # (the existing commerce authority still decides).
        if bool(context.user_initiated_commercial):
            return False
        if bool(context.current_content_interest):
            return False
        if bool(context.continuation_context):
            return False
        return True
    except Exception:
        return True


__all__ = [
    "AuthorizationBasis",
    "CommerceContext",
    "build_commerce_context",
    "should_suppress_commercial_framing",
    "downgrade_response_mode",
    "should_suppress_content_suggestion",
]
