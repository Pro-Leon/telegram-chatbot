"""Deterministic commerce decision model (Phase 5.3A).

Separates hard policy ("can we sell?") from commerce strategy
("should we sell right now?"). The hard side is owned by
:mod:`commerce.eligibility`; this module consumes its ``PolicyDecision`` and
decides a structured, machine-readable commerce action.

The engine is PURE: no randomness, no clock reads, no network, no database,
no LLM calls. Every input arrives through ``CommerceDecisionContext``;
"now" is expressed by the caller as ``hours_since_*`` values.

The output ``CommerceDecision`` is designed to be consumed by a future
DeepSeek V4 Flash adapter. The AI may map this decision into natural
language, but it can NEVER override ``allowed``, eligibility, cooldowns,
purchase history, or creator policy — those stay deterministic here.
"""

import enum
from dataclasses import dataclass, field
from typing import Any

from commerce.models import CommerceAction, OfferState, PolicyDecision, CreatorCapabilities, CapabilityStatus


class CommerceReason(str, enum.Enum):
    """Stable machine-readable reason codes for commerce decisions.

    Values are fixed snake_case codes; they feed analytics later, so never
    rename or reuse them.
    """

    # Hard policy
    POLICY_DENIED = "policy_denied"
    USER_BLOCKED = "user_blocked"
    USER_OPTED_OUT = "user_opted_out"
    CREATOR_NOT_READY = "creator_not_ready"
    PRODUCT_UNAVAILABLE = "product_unavailable"
    PRODUCT_MISSING_SALES_URL = "product_missing_sales_url"
    ALREADY_PURCHASED = "already_purchased"
    OFFER_EXISTS = "offer_exists"
    AGE_VERIFICATION_REQUIRED = "age_verification_required"

    # Relationship / timing
    INSUFFICIENT_RELATIONSHIP = "insufficient_relationship"
    RECENT_PURCHASE = "recent_purchase"
    RECENT_OFFER = "recent_offer"
    RECENT_DECLINE = "recent_decline"
    RECENT_IGNORE = "recent_ignore"
    TOO_MANY_OFFERS = "too_many_offers"
    TOO_MANY_SALES_ATTEMPTS = "too_many_sales_attempts"
    COOLDOWN_ACTIVE = "cooldown_active"
    NO_BUYING_SIGNAL = "no_buying_signal"
    NO_RELEVANT_PRODUCT = "no_relevant_product"

    # Positive signals
    STRONG_BUYING_SIGNAL = "strong_buying_signal"
    MODERATE_BUYING_SIGNAL = "moderate_buying_signal"
    RELATIONSHIP_READY = "relationship_ready"
    FOLLOW_UP_DUE = "follow_up_due"

    # Phase C: Relationship-aware decisions
    TIP_ELIGIBLE = "tip_eligible"
    OPERATOR_HANDOFF_NEEDED = "operator_handoff_needed"
    RELATIONSHIP_CHAT = "relationship_chat"

    # Phase C.1-B: Conversational intelligence
    CONVERSATIONAL_CHAT = "conversational_chat"
    NEGATIVE_SIGNALS_SUPPRESSED = "negative_signals_suppressed"
    OFFER_FATIGUE = "offer_fatigue"
    LOW_CONFIDENCE_CHAT = "low_confidence_chat"
    UNCERTAIN_AMBIGUOUS = "uncertain_ambiguous"

    # Phase C.1-C: Behavioral feedback
    COMMERCIAL_PAUSED = "commercial_paused"
    REJECTION_ESCALATION = "rejection_escalation"
    AFTERCARE_PHASE = "aftercare_phase"


@dataclass(frozen=True)
class CommerceDecisionContext:
    """Inputs for one deterministic commerce decision.

    Only fields that can be populated from existing state are required;
    signal scores (future DeepSeek adapter output) are optional and default
    to "unknown".

    Phase C.1-B adds conversational phase, negative signal count, offer
    fatigue, and reciprocity awareness.
    """

    user_id: int
    creator_id: int
    eligibility: PolicyDecision

    relationship_score: float | None = None
    buying_intent_score: float | None = None

    messages_since_last_offer: int = 0
    messages_since_last_purchase: int = 0

    hours_since_last_offer: float | None = None
    hours_since_last_purchase: float | None = None

    recent_offer_count: int = 0
    recent_purchase_count: int = 0
    recent_sales_attempt_count: int = 0

    previous_offer_status: str | None = None

    has_active_offer: bool = False
    has_relevant_product: bool = True

    user_requested_content: bool = False
    user_asked_about_price: bool = False
    user_asked_to_buy: bool = False

    # Phase C: Relationship context (from derive_relationship_state)
    relationship_state: str = "cold"
    commercial_pressure: str = "none"
    tip_eligibility: str = "ineligible"
    tip_reason: str = ""
    handoff_needed: bool = False
    handoff_reason: str | None = None

    creator_sales_enabled: bool = True

    # Phase C.1-B: Intent and conversational intelligence
    conversational_phase: str = "unknown"
    negative_intent_count: int = 0
    signal_confidence: float | None = None
    fan_asks_question: bool = False
    has_commercial_intent: bool = False

    # Phase C.1-C: Behavioral feedback intelligence
    consecutive_rejections: int = 0
    last_rejection_type: str | None = None
    aftercare_status: str = "pending"
    total_purchases: int = 0
    total_tips_received: int = 0
    tip_suggestions_ignored: int = 0
    tip_suggestions_sent: int = 0
    hours_since_last_tip: float | None = None
    fan_expressed_appreciation: bool = False
    fan_asked_how_to_support: bool = False
    repeat_purchase_eligible: bool = False
    commercial_paused: bool = False
    post_purchase_satisfaction: str | None = None

    # Phase C.1-E: Creator capabilities
    creator_capabilities: CreatorCapabilities | None = None

    # P0-02: Fan asking for free content must not be treated as purchase intent
    asks_for_free_content: bool = False

    # Phase 9: turn-scoped commerce context (advisory, additive, safe defaults).
    # None = legacy / unknown (no Phase 9 context supplied; preserves existing
    # callers). Explicit True/False = worker-supplied Phase 9 adapter result.
    # These carry no product/price authority and never authorize commerce by
    # themselves; they only allow the float/relationship branches below to
    # require deterministic current-turn corroboration when Phase 9 is present.
    current_content_interest: bool | None = None
    current_content_disinterest: bool | None = None
    user_initiated_commercial: bool | None = None
    continuation_context: bool | None = None
    warmth_without_commercial_evidence: bool | None = None
    authorization_basis: str | None = None


@dataclass(frozen=True)
class CommerceDecisionPolicy:
    """Centralized, overrideable policy knobs for the commerce decision
    engine. Production values live here, not scattered through the engine."""

    offer_cooldown_hours: float = 24.0
    purchase_cooldown_hours: float = 6.0
    max_offers_per_24h: int = 2
    max_sales_attempts_per_24h: int = 3
    minimum_relationship_score: float = 0.60
    strong_buying_intent_score: float = 0.80
    moderate_buying_intent_score: float = 0.55
    min_signal_confidence_for_commerce: float = 0.30

    # Phase C.1-C: Behavioral feedback thresholds
    rejection_escalation_threshold: int = 3
    aftercare_suppress_hours: float = 48.0
    rejection_override_hours: float = 48.0


DEFAULT_COMMERCE_POLICY = CommerceDecisionPolicy()


@dataclass(frozen=True)
class CommerceDecision:
    """Structured verdict of the deterministic commerce engine.

    ``confidence`` is NOT a model confidence: it is a fixed strength of the
    rule(s) that produced the decision. ``metadata`` is a safe surface for
    rule documentation (never credentials or raw payloads).

    Phase 10 note: version attribution travels in ``metadata``
    (``config_version`` / ``strategy_version`` / ``ranking_policy_version``)
    so the dataclass shape stays frozen for purity. Historical records may
    carry the legacy default ``unversioned-legacy``; forward decisions are
    stamped via ``commerce.phase10_learning.stamp_decision_with_version``
    where creator scope is known (worker/ledger call sites).
    """

    action: CommerceAction
    reason_code: CommerceReason
    allowed: bool
    confidence: float
    requires_human_review: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


# Deterministic rule-strength constants (fixed, documented, not tunable).
# The engine reports how strongly the applied rules support the decision;
# it never pretends to know more than its rules say.
CONF_HARD_RULE = 1.0
CONF_EXPLICIT_INTENT = 0.95
CONF_STRONG_SIGNAL = 0.85
CONF_FOLLOW_UP = 0.75
CONF_MODERATE_SIGNAL = 0.65
CONF_RELATIONSHIP_READY = 0.60
CONF_BUILDING = 0.55
CONF_NO_OFFER = 0.50

# Hard-eligibility denial reasons mapped to stable decision reason codes.
_DENIAL_REASON_MAP = {
    "user_blocked": CommerceReason.USER_BLOCKED,
    "user_opted_out": CommerceReason.USER_OPTED_OUT,
    "creator_not_ready": CommerceReason.CREATOR_NOT_READY,
    "product_unavailable": CommerceReason.PRODUCT_UNAVAILABLE,
    "product_missing_sales_url": CommerceReason.PRODUCT_MISSING_SALES_URL,
    "already_purchased": CommerceReason.ALREADY_PURCHASED,
    "offer_exists": CommerceReason.OFFER_EXISTS,
    "age_verification_required": CommerceReason.AGE_VERIFICATION_REQUIRED,
}

_NEGATIVE_STATUSES = frozenset({OfferState.DECLINED.value, OfferState.REVOKED.value})
_IGNORED_STATUSES = frozenset({OfferState.CLICKED.value, OfferState.EXPIRED.value})
_FOLLOW_UP_STATUSES = frozenset(
    {
        OfferState.DECLINED.value,
        OfferState.REVOKED.value,
        OfferState.CLICKED.value,
        OfferState.EXPIRED.value,
    }
)

_SELL_ACTIONS = frozenset(
    {CommerceAction.SOFT_OFFER, CommerceAction.OFFER_PPV, CommerceAction.FOLLOW_UP}
)


def _classify_aftercare_intent(context: CommerceDecisionContext) -> str:
    """Classify the type of intent during aftercare phase.

    Returns a string describing the intent type for metadata.
    """
    if context.user_asked_to_buy or context.user_asked_about_price or context.user_requested_content:
        return "explicit_buying"
    if context.fan_expressed_appreciation:
        return "appreciation"
    if context.fan_asks_question:
        return "curiosity"
    if context.has_commercial_intent:
        return "commercial_interest"
    return "casual"


def _decision(
    action: CommerceAction,
    reason: CommerceReason,
    confidence: float,
    *,
    review: bool = False,
    metadata: dict[str, Any] | None = None,
) -> CommerceDecision:
    """Build a decision. `allowed` is True only for actions that make a sale
    move (offers, soft mentions, follow-ups)."""
    # Phase 10: version attribution travels in metadata with legacy defaults
    # so this pure module keeps its import purity (stdlib + domain models
    # only). Forward decisions gain the real version via
    # phase10.stamp_decision_with_version where creator scope is known.
    _meta = dict(metadata or {})
    _meta.setdefault("config_version", "unversioned-legacy")
    _meta.setdefault("strategy_version", "strategy.v1")
    return CommerceDecision(
        action=action,
        reason_code=reason,
        allowed=action in _SELL_ACTIONS,
        confidence=confidence,
        requires_human_review=review,
        metadata=_meta,
    )


# Phase 9: deterministic corroboration helpers (pure, bounded, no I/O).
# These enforce the relationship/commerce boundary without introducing new
# scores, new classifiers, or new authority. They only read the additive
# Phase 9 context fields plus the existing deterministic buy/price flags.

_PHASE9_OFFER_BASES = frozenset({"EXPLICIT_TEXT", "PRICE_INQUIRY", "PURCHASE_INTENT"})


def _phase9_present(context: CommerceDecisionContext) -> bool:
    """True when the worker supplied Phase 9 adapter context.

    All six Phase 9 fields default to None (legacy / unknown). Any
    explicit value (True/False/string) means the Phase 9 adapter ran for
    this turn and its guidance must be honored. Never raises.
    """
    try:
        return (
            context.current_content_interest is not None
            or context.current_content_disinterest is not None
            or context.user_initiated_commercial is not None
            or context.continuation_context is not None
            or context.warmth_without_commercial_evidence is not None
            or context.authorization_basis is not None
        )
    except Exception:
        return False


def _phase9_blocks_float_offer(context: CommerceDecisionContext) -> bool:
    """True when a high LLM float must NOT become OFFER_PPV (Phase 9).

    Blocking conditions (when Phase 9 present):
    - current disinterest present, or
    - no deterministic buy/price flag AND no Phase 9 user-initiated
      corroboration with an offer-grade basis.
    Explicit deterministic buy/price flags always corroborate (existing
    P0 gates, already verifier-backed in production). Legacy callers
    without Phase 9 context never block (preserves existing behavior).
    """
    try:
        if not _phase9_present(context):
            return False
        # Current disinterest suppresses float offers (history cannot
        # override; explicit deterministic path in step 8 still owns its
        # own purchase authority and is evaluated before this branch).
        try:
            if context.current_content_disinterest is True:
                return True
        except Exception:
            pass
        # Existing deterministic gates corroborate.
        try:
            if bool(context.user_asked_to_buy) or bool(context.user_asked_about_price):
                return False
        except Exception:
            pass
        # Phase 9 corroboration: user-initiated with an offer-grade basis.
        try:
            if context.user_initiated_commercial is True:
                basis = str(context.authorization_basis or "").strip().upper()
                if basis in _PHASE9_OFFER_BASES:
                    return False
        except Exception:
            pass
        return True
    except Exception:
        # Fail-closed for new guidance: block the float offer.
        return True


def _phase9_blocks_soft_offer(context: CommerceDecisionContext) -> bool:
    """True when warmth/float alone must NOT become SOFT_OFFER (Phase 9).

    Blocking conditions (when Phase 9 present):
    - current disinterest present, or
    - warmth without commercial evidence, or
    - no current content interest, no continuation, and no
      user-initiated commercial evidence.
    Genuine current evidence (user-initiated, content interest, or
    continuation) allows the existing soft path. Legacy callers without
    Phase 9 context never block.
    """
    try:
        if not _phase9_present(context):
            return False
        try:
            if context.current_content_disinterest is True:
                return True
        except Exception:
            pass
        try:
            if context.warmth_without_commercial_evidence is True:
                return True
        except Exception:
            pass
        try:
            if context.user_initiated_commercial is True:
                return False
        except Exception:
            pass
        try:
            if context.current_content_interest is True:
                return False
        except Exception:
            pass
        try:
            if context.continuation_context is True:
                return False
        except Exception:
            pass
        return True
    except Exception:
        return True


def decide_commerce_action(
    context: CommerceDecisionContext,
    policy: CommerceDecisionPolicy = DEFAULT_COMMERCE_POLICY,
) -> CommerceDecision:
    """Decide the commerce action for one fan/creator pair.

    Strict priority (safety first):
      1. Hard eligibility denial
      1.5. Operator handoff needed (Phase C)
      2. Creator commerce disabled
      3. No relevant product
      4. Existing active offer
      5. Recent purchase cooldown
      6. Recent offer cooldown (with outcome-aware reasons)
      7. Excessive offers / sales attempts per 24h
      8. Explicit user buying intent
      8.5. Tip suggestion eligible (Phase C)
      9. Strong buying signal
      10. Follow-up due
      11. Moderate buying signal
      12. Relationship readiness
      13. Relationship building
      14. No offer

    PURE: reads only the context and policy; never the clock, DB, network,
    or an LLM.
    """
    # 1. Hard eligibility (owned by commerce/eligibility.py)
    if not context.eligibility.allowed:
        reason = _DENIAL_REASON_MAP.get(
            context.eligibility.denial_reason, CommerceReason.POLICY_DENIED
        )
        return _decision(
            CommerceAction.NO_OFFER,
            reason,
            CONF_HARD_RULE,
            metadata={"eligibility_denial": context.eligibility.denial_reason},
        )

    # 1.5. Operator handoff needed (Phase C) — checked immediately after eligibility
    if context.handoff_needed:
        return _decision(
            CommerceAction.OPERATOR_HANDOFF,
            CommerceReason.OPERATOR_HANDOFF_NEEDED,
            CONF_HARD_RULE,
            metadata={"handoff_reason": context.handoff_reason},
        )

    # 1.7. Fan asks for free content — suppress commerce (P0-02)
    if context.asks_for_free_content:
        return _decision(
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceReason.NO_BUYING_SIGNAL,
            CONF_BUILDING,
            metadata={"free_content_request": True},
        )

    # 2. Creator commerce disabled
    if not context.creator_sales_enabled:
        return _decision(
            CommerceAction.NO_OFFER,
            CommerceReason.CREATOR_NOT_READY,
            CONF_HARD_RULE,
            metadata={"creator_sales_enabled": False},
        )

    # 3. No relevant product
    if not context.has_relevant_product:
        return _decision(
            CommerceAction.NO_OFFER, CommerceReason.NO_RELEVANT_PRODUCT, CONF_HARD_RULE
        )

    # 4. Existing active offer
    if context.has_active_offer:
        return _decision(
            CommerceAction.NO_OFFER,
            CommerceReason.OFFER_EXISTS,
            CONF_HARD_RULE,
            metadata={"active_offer": True},
        )

    # 5. Recent purchase cooldown
    if (
        context.hours_since_last_purchase is not None
        and context.hours_since_last_purchase < policy.purchase_cooldown_hours
    ):
        return _decision(
            CommerceAction.NO_OFFER,
            CommerceReason.RECENT_PURCHASE,
            CONF_HARD_RULE,
            metadata={"hours_since_last_purchase": context.hours_since_last_purchase},
        )

    # 6. Recent offer cooldown (outcome-aware reasons)
    if (
        context.hours_since_last_offer is not None
        and context.hours_since_last_offer < policy.offer_cooldown_hours
    ):
        status = context.previous_offer_status
        if status in _NEGATIVE_STATUSES:
            return _decision(CommerceAction.NO_OFFER, CommerceReason.RECENT_DECLINE, CONF_HARD_RULE)
        if status in _IGNORED_STATUSES:
            return _decision(CommerceAction.NO_OFFER, CommerceReason.RECENT_IGNORE, CONF_HARD_RULE)
        if status is None:
            return _decision(
                CommerceAction.NO_OFFER, CommerceReason.COOLDOWN_ACTIVE, CONF_HARD_RULE
            )
        return _decision(CommerceAction.NO_OFFER, CommerceReason.RECENT_OFFER, CONF_HARD_RULE)

    # 7. Excessive recent sales activity (24h budgets)
    if context.recent_offer_count >= policy.max_offers_per_24h:
        return _decision(
            CommerceAction.NO_OFFER,
            CommerceReason.TOO_MANY_OFFERS,
            CONF_HARD_RULE,
            metadata={"recent_offer_count": context.recent_offer_count},
        )
    if context.recent_sales_attempt_count >= policy.max_sales_attempts_per_24h:
        return _decision(
            CommerceAction.NO_OFFER,
            CommerceReason.TOO_MANY_SALES_ATTEMPTS,
            CONF_HARD_RULE,
            metadata={"recent_sales_attempt_count": context.recent_sales_attempt_count},
        )

    # Human review: eligibility says "not purchased" but purchase history says
    # otherwise -> data inconsistency (e.g. attribution lag). Never auto-offer.
    review = context.recent_purchase_count > 0

    # 7.5. Offer fatigue — too many recent offers suppresses commercial action
    if context.recent_offer_count >= 1 and context.recent_sales_attempt_count >= 2:
        if context.buying_intent_score is not None and context.buying_intent_score < policy.strong_buying_intent_score:
            return _decision(
                CommerceAction.NO_OFFER,
                CommerceReason.OFFER_FATIGUE,
                CONF_HARD_RULE,
                metadata={
                    "recent_offer_count": context.recent_offer_count,
                    "recent_sales_attempt_count": context.recent_sales_attempt_count,
                },
            )

    # 7.6. Negative intent signals suppress commercial action
    if context.negative_intent_count >= 2:
        return _decision(
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceReason.NEGATIVE_SIGNALS_SUPPRESSED,
            CONF_HARD_RULE,
            metadata={"negative_intent_count": context.negative_intent_count},
        )

    # 7.7. Low confidence + no explicit intent → prefer conversation
    if (
        context.signal_confidence is not None
        and context.signal_confidence < policy.min_signal_confidence_for_commerce
        and not context.user_asked_to_buy
        and not context.user_asked_about_price
        and not context.user_requested_content
    ):
        return _decision(
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceReason.LOW_CONFIDENCE_CHAT,
            CONF_BUILDING,
            metadata={"signal_confidence": context.signal_confidence},
        )

    # 7.8. Conversational phase: opening/rapport phases suppress commercial
    if context.conversational_phase in ("opening", "rapport"):
        if not context.user_asked_to_buy and not context.user_asked_about_price:
            return _decision(
                CommerceAction.RELATIONSHIP_BUILDING,
                CommerceReason.CONVERSATIONAL_CHAT,
                CONF_BUILDING,
                metadata={"conversational_phase": context.conversational_phase},
            )

    # 7.9. Commercial pause — complaint or escalation-induced pause
    if context.commercial_paused:
        return _decision(
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceReason.COMMERCIAL_PAUSED,
            CONF_HARD_RULE,
            metadata={"commercial_paused": True},
        )

    # 7.10. Aftercare phase — post-purchase, no immediate selling
    # Aftercare is a policy boundary with explicit intent handling:
    # - Casual conversation, appreciation, curiosity → suppress commerce
    # - Explicit buying intent → honor if policy permits (Step 8 will handle)
    # - Ambiguous interest → suppress (fail-closed)
    if (
        context.aftercare_status in ("pending", "sent")
        and context.total_purchases > 0
    ):
        # Explicit buying intent overrides aftercare only if policy permits
        # This is checked at Step 8, so we only suppress non-explicit intents here
        if not context.user_asked_to_buy and not context.user_asked_about_price and not context.user_requested_content:
            return _decision(
                CommerceAction.RELATIONSHIP_BUILDING,
                CommerceReason.AFTERCARE_PHASE,
                CONF_BUILDING,
                metadata={
                    "aftercare_status": context.aftercare_status,
                    "aftercare_intent_type": _classify_aftercare_intent(context),
                },
            )

    # 7.11. Rejection escalation — consecutive rejections suppress commerce
    if context.consecutive_rejections >= policy.rejection_escalation_threshold:
        return _decision(
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceReason.REJECTION_ESCALATION,
            CONF_HARD_RULE,
            metadata={"consecutive_rejections": context.consecutive_rejections},
        )

    # 8. Explicit user buying intent — Phase 99: content desire alone must NOT authorize PPV
    # `user_requested_content` (from LLM `explicit_content_request`) is now routed through
    # the deterministic free-photo path; it does not directly authorize commerce.
    if (
        context.user_asked_to_buy
        or context.user_asked_about_price
    ):
        # Check creator capability before offering content
        if context.creator_capabilities and not context.creator_capabilities.can_sell_content():
            return _decision(
                CommerceAction.RELATIONSHIP_BUILDING,
                CommerceReason.CREATOR_NOT_READY,
                CONF_HARD_RULE,
                metadata={"content_ineligible_reason": "creator_cannot_sell_content"},
            )
        # Conflicting signals: user asks to buy right after a decline/revoke.
        review = review or context.previous_offer_status in _NEGATIVE_STATUSES
        return _decision(
            CommerceAction.OFFER_PPV,
            CommerceReason.STRONG_BUYING_SIGNAL,
            CONF_EXPLICIT_INTENT,
            review=review,
        )

    # 8.5. Tip suggestion eligible (Phase C)
    if context.tip_eligibility == "eligible":
        # Check creator capability before suggesting tip
        if context.creator_capabilities and not context.creator_capabilities.can_accept_tips():
            return _decision(
                CommerceAction.RELATIONSHIP_BUILDING,
                CommerceReason.CREATOR_NOT_READY,
                CONF_HARD_RULE,
                metadata={"tip_ineligible_reason": "creator_cannot_accept_tips"},
            )
        return _decision(
            CommerceAction.TIP_SUGGESTION,
            CommerceReason.TIP_ELIGIBLE,
            CONF_MODERATE_SIGNAL,
            metadata={"tip_reason": context.tip_reason},
        )

    # 9. Strong buying signal (implicit) — Phase 9 fenced.
    # A high LLM purchase-intent float alone must never create OFFER_PPV.
    # When Phase 9 context is present (worker-supplied adapter result),
    # deterministic current-turn corroboration is required: an explicit
    # deterministic buy/price flag (already P0-gated) or a Phase 9
    # user-initiated basis of EXPLICIT_TEXT / PRICE_INQUIRY /
    # PURCHASE_INTENT. CONTENT_REQUEST / CONTENT_CONTINUATION alone never
    # authorize PPV (Phase 99 preserved). Current disinterest suppresses
    # float offers. Legacy callers without Phase 9 context (all Phase 9
    # fields None) preserve existing behavior.
    if (
        context.buying_intent_score is not None
        and context.buying_intent_score >= policy.strong_buying_intent_score
    ):
        if _phase9_blocks_float_offer(context):
            pass  # fall through: no OFFER_PPV from float alone
        else:
            return _decision(
                CommerceAction.OFFER_PPV,
                CommerceReason.STRONG_BUYING_SIGNAL,
                CONF_STRONG_SIGNAL,
                review=review,
            )

    # 10. Follow-up due (previous offer had an outcome, cooldown already cleared)
    if context.previous_offer_status in _FOLLOW_UP_STATUSES:
        return _decision(
            CommerceAction.FOLLOW_UP,
            CommerceReason.FOLLOW_UP_DUE,
            CONF_FOLLOW_UP,
            review=review,
            metadata={"previous_offer_status": context.previous_offer_status},
        )

    # 11. Moderate buying signal (implicit) — Phase 9 fenced.
    # relationship_engagement / warmth alone must not create SOFT_OFFER.
    # When Phase 9 context is present, require current-turn commercial /
    # content evidence; warmth-without-evidence or current disinterest
    # suppresses the soft offer (falls through to relationship building).
    # Legacy callers without Phase 9 context preserve existing behavior.
    if (
        context.buying_intent_score is not None
        and context.buying_intent_score >= policy.moderate_buying_intent_score
    ):
        if _phase9_blocks_soft_offer(context):
            pass  # fall through: no SOFT_OFFER from float alone
        else:
            return _decision(
                CommerceAction.SOFT_OFFER,
                CommerceReason.MODERATE_BUYING_SIGNAL,
                CONF_MODERATE_SIGNAL,
                review=review,
            )

    # 12. Relationship readiness — Phase 9 fenced.
    # A high relationship/familiarity/engagement score alone must never
    # authorize commerce. When Phase 9 context is present, require genuine
    # current-turn commercial/content evidence; otherwise fall through to
    # relationship building (ordinary warm conversation preserved, no
    # commercial framing).
    if (
        context.relationship_score is not None
        and context.relationship_score >= policy.minimum_relationship_score
    ):
        if _phase9_blocks_soft_offer(context):
            pass  # fall through: warmth alone -> no SOFT_OFFER
        else:
            return _decision(
                CommerceAction.SOFT_OFFER,
                CommerceReason.RELATIONSHIP_READY,
                CONF_RELATIONSHIP_READY,
                review=review,
            )

    # 13. Relationship building (keep chatting, no offer)
    if context.relationship_score is not None:
        return _decision(
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceReason.INSUFFICIENT_RELATIONSHIP,
            CONF_BUILDING,
            review=review,
        )
    if context.messages_since_last_offer >= 1:
        return _decision(
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceReason.NO_BUYING_SIGNAL,
            CONF_BUILDING,
            review=review,
        )

    # 14. No offer (nothing to act on)
    return _decision(
        CommerceAction.NO_OFFER,
        CommerceReason.NO_BUYING_SIGNAL,
        CONF_NO_OFFER,
        review=review,
    )
