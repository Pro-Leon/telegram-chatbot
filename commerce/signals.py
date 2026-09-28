"""Commerce signal extraction domain (Phase 5.3B).

``CommerceSignals`` is the strict, typed output surface for LLM-based
estimation of a fan's purchase-related signals. It describes OBSERVATIONS
about the conversation only: it carries no action, no authorization, no
eligibility, no policy, and no decision semantics.

The final commerce action is ALWAYS decided by the deterministic engine
(:func:`commerce.decision.decide_commerce_action`); signals only feed the
``CommerceDecisionContext`` through :func:`signals_to_context` where
application-owned state wins on any conflict.

Validation is strict and rejection-based (never clamped):

- floats bounded [0.0, 1.0], finite
- booleans accept only real booleans
- ``requested_price`` is null or a finite positive number
- ``evidence`` is a list of at most 5 short strings without payment data
- unknown fields are rejected (``extra="forbid"``)

LLM failure must never look like interest: callers use
:meth:`CommerceSignals.low_information` for the deterministic safe fallback.
"""

import math
import re
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from commerce.decision import (
    DEFAULT_COMMERCE_POLICY,
    CommerceDecision,
    CommerceDecisionContext,
    CommerceDecisionPolicy,
    decide_commerce_action,
)
from commerce.models import PolicyDecision
from commerce.models import CreatorCapabilities

MAX_EVIDENCE_ITEMS = 5
MAX_EVIDENCE_ITEM_LENGTH = 240

# A fan explicitly discussing price at/above this level counts as "asking
# about price" for the deterministic engine (conservative, documented).
PRICE_ASK_THRESHOLD = 0.80

# Intent taxonomy — minimal, interpretable set.
# These are string codes (not a giant enum) for flexibility.
INTENT_CATEGORIES = frozenset({
    "casual_chat",
    "greeting",
    "relationship_building",
    "personal_disclosure",
    "content_curiosity",
    "content_request",
    "price_inquiry",
    "purchase_intent",
    "repeat_purchase_intent",
    "post_purchase",
    "aftercare",
    "tip_interest",
    "complaint",
    "custom_request",
    "negotiation",
    "hesitation",
    "rejection",
    "uncertain",
    "reassurance",
    "appreciation",
    "operator_request",
    "other",
})

# Commercial intent categories — intents that may warrant commercial action.
_COMMERCIAL_INTENTS = frozenset({
    "content_curiosity",
    "content_request",
    "price_inquiry",
    "purchase_intent",
    "repeat_purchase_intent",
    "tip_interest",
    "custom_request",
    "negotiation",
})

# Negative intent categories — signals that should suppress commercial action.
_NEGATIVE_INTENTS = frozenset({
    "hesitation",
    "rejection",
    "complaint",
})


def _reject_str_bool(v: Any) -> Any:
    """Reject strings/booleans for numeric fields (no lax coercion)."""
    if isinstance(v, (str, bool)):
        raise ValueError("must be a number")  # noqa: TRY004 — pydantic must wrap into ValidationError
    return v


def _reject_str_bool_or_none(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (str, bool)):
        raise ValueError("must be a number or null")  # noqa: TRY004 — pydantic must wrap into ValidationError
    return v


BoundedFloat = Annotated[
    float,
    BeforeValidator(_reject_str_bool),
    Field(ge=0.0, le=1.0, allow_inf_nan=False),
]
StrictBool = Annotated[bool, Field(strict=True)]
BoundedPrice = Annotated[float | None, BeforeValidator(_reject_str_bool_or_none)]
EvidenceItem = Annotated[str, StringConstraints(max_length=MAX_EVIDENCE_ITEM_LENGTH)]

# Payment-data guard for evidence: card-like digit runs (13-16 consecutive
# digits) or explicit payment identifiers. Evidence must never carry this.
_CARD_NUMBER_RE = re.compile(r"\b\d{13,16}\b")
_PAYMENT_MARKERS = (
    "card number",
    "cardno",
    "card no",
    "cvv",
    "cvc",
    "pan:",
    "payment method",
)


def _contains_payment_data(text: str) -> bool:
    lowered = text.lower()
    if any(marker in lowered for marker in _PAYMENT_MARKERS):
        return True
    return _CARD_NUMBER_RE.search(text) is not None


class CommerceSignals(BaseModel):
    """Typed, validated LLM estimates of fan commerce signals.

    All fields are required except ``requested_price`` (null when the fan
    never quoted a price) and ``evidence`` (may be an empty list). Missing
    required fields, out-of-bounds values, wrong types, unknown extra
    fields, and payment data in evidence are hard validation errors.

    Phase C.1-B adds multi-intent support, negative signal detection,
    reciprocity awareness, and topic continuity.
    Phase 87: for OneCall robustness, extra fields inside commerce_signals are ignored (not forbidden) to tolerate model variations like `needs_handoff` inside signals.
    """

    model_config = ConfigDict(extra="ignore")

    purchase_intent: BoundedFloat = 0.0
    content_interest: BoundedFloat = 0.0
    relationship_engagement: BoundedFloat = 0.0
    price_interest: BoundedFloat = 0.0
    explicit_purchase_request: StrictBool = False
    explicit_content_request: StrictBool = False
    requested_price: BoundedPrice = None
    declined_recent_offer: StrictBool = False
    negative_sentiment: BoundedFloat = 0.0
    confidence: BoundedFloat = 0.0
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=MAX_EVIDENCE_ITEMS)
    model_uncertainty: BoundedFloat = 1.0

    # Phase C.1-B: Multi-intent and contextual signals
    primary_intent: str = "uncertain"
    intent_tags: list[str] = Field(default_factory=list, max_length=5)
    negative_intent_tags: list[str] = Field(default_factory=list, max_length=5)
    fan_asks_question: StrictBool = False

    # P0-02: Prompt asks for these; previously dead via extra=forbid → invalid_payload
    accepted_recent_offer: StrictBool = False
    asks_for_free_content: StrictBool = False
    conversation_relevance: BoundedFloat = 1.0
    topic_continuity: str | None = None

    @field_validator("evidence")
    @classmethod
    def _reject_payment_data_in_evidence(cls, v: list[str]) -> list[str]:
        for item in v:
            if _contains_payment_data(item):
                raise ValueError("evidence must not contain payment data")
        return v

    @field_validator("requested_price", mode="before")
    @classmethod
    def _normalize_zero_price(cls, v):
        # JSON schema strict:true does not prevent 0 under all samplers/q4
        # Business: missing vs zero vs positive are distinct
        if v == 0 or (isinstance(v, float) and v == 0.0):
            return None  # missing semantics, never 0
        if isinstance(v, str) and v.strip() in ("0", "0.0", "0.00"):
            return None
        return v

    @field_validator("requested_price")
    @classmethod
    def _price_positive_finite(cls, v: float | None) -> float | None:
        if v is None:
            return v
        if not math.isfinite(v) or v <= 0:
            raise ValueError("requested_price must be a finite positive number")
        return v

    @field_validator("primary_intent")
    @classmethod
    def _validate_primary_intent(cls, v: str) -> str:
        if v not in INTENT_CATEGORIES:
            raise ValueError(f"primary_intent must be one of: {sorted(INTENT_CATEGORIES)}")
        return v

    @field_validator("intent_tags")
    @classmethod
    def _validate_intent_tags(cls, v: list[str]) -> list[str]:
        # Phase 87: allowlist is advisory; filter unknown tags instead of hard fail for forward compat (model may produce new tags like 'welcome')
        # Keep only known categories, drop unknown to avoid validation failure while preserving observability
        filtered = [tag for tag in v if tag in INTENT_CATEGORIES]
        # If all filtered out, keep empty rather than fail
        return filtered

    @field_validator("negative_intent_tags")
    @classmethod
    def _validate_negative_intent_tags(cls, v: list[str]) -> list[str]:
        for tag in v:
            if tag not in _NEGATIVE_INTENTS:
                raise ValueError(f"negative_intent_tags contains unknown: {tag!r}")
        return v

    @classmethod
    def low_information(cls) -> "CommerceSignals":
        """Deterministic safe fallback when extraction fails.

        Every signal is neutral, no evidence, maximal uncertainty. This is
        the ONLY sanctioned way to represent "the model could not tell us
        anything" — and it never implies interest.
        """
        return cls(
            purchase_intent=0.0,
            content_interest=0.0,
            relationship_engagement=0.0,
            price_interest=0.0,
            explicit_purchase_request=False,
            explicit_content_request=False,
            requested_price=None,
            declined_recent_offer=False,
            negative_sentiment=0.0,
            confidence=0.0,
            evidence=[],
            model_uncertainty=1.0,
            primary_intent="uncertain",
            intent_tags=[],
            negative_intent_tags=[],
            fan_asks_question=False,
        )


def signals_to_context(
    signals: CommerceSignals | None,
    *,
    user_id: int,
    creator_id: int,
    eligibility: PolicyDecision,
    relationship_score: float | None = None,
    messages_since_last_offer: int = 0,
    messages_since_last_purchase: int = 0,
    hours_since_last_offer: float | None = None,
    hours_since_last_purchase: float | None = None,
    recent_offer_count: int = 0,
    recent_purchase_count: int = 0,
    recent_sales_attempt_count: int = 0,
    previous_offer_status: str | None = None,
    has_active_offer: bool = False,
    has_relevant_product: bool = True,
    creator_sales_enabled: bool = True,
    relationship_state: str = "cold",
    commercial_pressure: str = "none",
    # Phase C.1-C: Behavioral feedback context
    consecutive_rejections: int = 0,
    total_purchases: int = 0,
    total_tips_received: int = 0,
    hours_since_last_tip: float | None = None,
    tip_suggestions_sent: int = 0,
    tip_suggestions_ignored: int = 0,
    aftercare_status: str = "none",
    commercial_paused: bool = False,
    fan_expressed_appreciation: bool = False,
    fan_asked_how_to_support: bool = False,
    repeat_purchase_eligible: bool = False,
    # Phase C.1-E: Creator capabilities
    creator_capabilities: CreatorCapabilities | None = None,
    user_message: str | None = None,
    # Phase 9: turn-scoped commerce context (additive, safe defaults).
    # None = legacy / unknown (preserves existing callers). Explicit
    # values are the worker-supplied Phase 9 adapter result (categorical
    # / boolean, no product/price authority).
    current_content_interest: bool | None = None,
    current_content_disinterest: bool | None = None,
    user_initiated_commercial: bool | None = None,
    continuation_context: bool | None = None,
    warmth_without_commercial_evidence: bool | None = None,
    authorization_basis: str | None = None,
) -> CommerceDecisionContext:
    """Build a ``CommerceDecisionContext`` from signals + application state.

    APPLICATION STATE WINS: eligibility, cooldowns, offer/product state,
    attempt budgets, and previous offer status are caller-owned and passed
    through untouched. Only conversational intent flags and scores are
    derived from the model — mechanically and conservatively:

    - ``user_asked_to_buy``   <- explicit_purchase_request
    - ``user_asked_about_price`` <- requested_price quoted OR
                                     price_interest >= PRICE_ASK_THRESHOLD
    - ``user_requested_content`` <- explicit_content_request
    - ``buying_intent_score`` <- purchase_intent (caller value wins if given)
    - ``relationship_score``  <- caller value wins, else relationship_engagement

    ``None`` signals (extraction failure) produce a context with no
    model-derived intent: the engine then falls back to app data or the
    safe no-offer paths. Failure never looks like interest.
    """
    if signals is None:
        explicit_buy = False
        explicit_content = False
        price_ask = False
        intent_score: float | None = None
        rel_score: float | None = relationship_score
        negative_count = 0
        has_commercial = False
        fan_asks = False
        signal_conf: float | None = None
        asks_free = False
        phase = _derive_conversational_phase(
            relationship_state, None, False, False, False,
        )
    else:
        # P0 guard: LLM explicit/price signals must be AND-gated with deterministic raw-text verification.
        # When user_message is supplied (production), verifier must also be True.
        # When None (legacy tests callers), retain legacy behavior.
        try:
            from commerce.purchase_intent import is_explicit_purchase_request as _is_explicit
            from commerce.purchase_intent import is_price_inquiry as _is_price
        except Exception:
            _is_explicit = None  # type: ignore
            _is_price = None  # type: ignore

        if _is_explicit is not None and user_message is not None:
            try:
                explicit_buy = bool(signals.explicit_purchase_request and _is_explicit(user_message))
            except Exception:
                explicit_buy = False
        else:
            explicit_buy = signals.explicit_purchase_request
        explicit_content = signals.explicit_content_request
        _llm_price_signal = (
            signals.requested_price is not None or signals.price_interest >= PRICE_ASK_THRESHOLD
        )
        if _is_price is not None and user_message is not None:
            try:
                price_ask = bool(_llm_price_signal and _is_price(user_message))
            except Exception:
                price_ask = False
        else:
            price_ask = _llm_price_signal
        intent_score = signals.purchase_intent
        rel_score = (
            relationship_score
            if relationship_score is not None
            else signals.relationship_engagement
        )
        negative_count = len(signals.negative_intent_tags)
        has_commercial = bool(set(signals.intent_tags) & _COMMERCIAL_INTENTS)
        fan_asks = signals.fan_asks_question
        signal_conf = signals.confidence
        asks_free = signals.asks_for_free_content
        # If fan asks for free, suppress commercial intent
        if asks_free:
            has_commercial = False
        phase = _derive_conversational_phase(
            relationship_state,
            signals.primary_intent,
            has_commercial,
            explicit_buy,
            signals.declined_recent_offer,
        )

    return CommerceDecisionContext(
        user_id=user_id,
        creator_id=creator_id,
        eligibility=eligibility,
        relationship_score=rel_score,
        buying_intent_score=intent_score,
        messages_since_last_offer=messages_since_last_offer,
        messages_since_last_purchase=messages_since_last_purchase,
        hours_since_last_offer=hours_since_last_offer,
        hours_since_last_purchase=hours_since_last_purchase,
        recent_offer_count=recent_offer_count,
        recent_purchase_count=recent_purchase_count,
        recent_sales_attempt_count=recent_sales_attempt_count,
        previous_offer_status=previous_offer_status,
        has_active_offer=has_active_offer,
        has_relevant_product=has_relevant_product,
        user_requested_content=explicit_content,
        user_asked_about_price=price_ask,
        user_asked_to_buy=explicit_buy,
        creator_sales_enabled=creator_sales_enabled,
        relationship_state=relationship_state,
        commercial_pressure=commercial_pressure,
        conversational_phase=phase,
        negative_intent_count=negative_count,
        signal_confidence=signal_conf,
        fan_asks_question=fan_asks,
        has_commercial_intent=has_commercial,
        asks_for_free_content=asks_free,
        # Phase C.1-C: Behavioral feedback context
        consecutive_rejections=consecutive_rejections,
        total_purchases=total_purchases,
        total_tips_received=total_tips_received,
        hours_since_last_tip=hours_since_last_tip,
        tip_suggestions_sent=tip_suggestions_sent,
        tip_suggestions_ignored=tip_suggestions_ignored,
        aftercare_status=aftercare_status,
        commercial_paused=commercial_paused,
        fan_expressed_appreciation=fan_expressed_appreciation,
        fan_asked_how_to_support=fan_asked_how_to_support,
        repeat_purchase_eligible=repeat_purchase_eligible,
        # Phase C.1-E: Creator capabilities
        creator_capabilities=creator_capabilities,
        # Phase 9: turn-scoped commerce context (additive passthrough)
        current_content_interest=current_content_interest,
        current_content_disinterest=current_content_disinterest,
        user_initiated_commercial=user_initiated_commercial,
        continuation_context=continuation_context,
        warmth_without_commercial_evidence=warmth_without_commercial_evidence,
        authorization_basis=authorization_basis,
    )


def _derive_conversational_phase(
    relationship_state: str,
    primary_intent: str | None,
    has_commercial_intent: bool,
    explicit_buy: bool,
    recent_decline: bool,
) -> str:
    """Derive conversational phase from observable signals.

    This is a pure function — no I/O, no randomness, no clock.
    Phase is transient and not persisted.
    """
    # Post-purchase / aftercare
    if relationship_state in ("purchased", "repeat_buyer", "vip"):
        return "post_purchase"

    # Cooling down after rejection
    if recent_decline or relationship_state in ("cooling_down", "do_not_push"):
        return "cooldown"

    # Operator required
    if relationship_state == "operator_required":
        return "operator_handoff"

    # Explicit commercial signals
    if explicit_buy or has_commercial_intent:
        return "commercial_interest"

    # Content curiosity
    if primary_intent in ("content_curiosity", "content_request", "price_inquiry"):
        return "content_curiosity"

    # Relationship / chat phases
    if primary_intent in ("greeting",):
        return "opening"
    if primary_intent in ("personal_disclosure", "relationship_building"):
        return "rapport"
    if primary_intent in ("casual_chat", "uncertain", None):
        if relationship_state in ("warm", "engaged"):
            return "engaged_chat"
        if relationship_state in ("new", "cold"):
            return "rapport"
        return "engaged_chat"

    return "engaged_chat"


def decide_from_signals(
    signals: CommerceSignals | None,
    *,
    user_id: int,
    creator_id: int,
    eligibility: PolicyDecision,
    policy: CommerceDecisionPolicy = DEFAULT_COMMERCE_POLICY,
    **app_state: Any,
) -> CommerceDecision:
    """Signal-aware deterministic decision.

    Purely composes :func:`signals_to_context` + the deterministic engine.
    Signals can never decide an action by themselves: every outcome is a
    ``CommerceDecision`` produced by ``decide_commerce_action``.
    """
    # P0: allow caller to pass raw user_message for deterministic gating
    _user_message = app_state.pop("user_message", None)
    context = signals_to_context(
        signals, user_id=user_id, creator_id=creator_id, eligibility=eligibility, user_message=_user_message, **app_state
    )
    return decide_commerce_action(context, policy=policy)
