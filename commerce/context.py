"""Commerce conversation context boundary (Phase 5.4 Chk 1).

``CommerceConversationContext`` is the authoritative input boundary between
untrusted conversation messages and structured application state, before any
strategy or response-generation layer runs.

Authority invariant: conversation text NEVER overrides structured state.
A fan writing "I already bought this", "send it for $5", or "I paid" cannot
change eligibility, cooldowns, offer/purchase state, creator readiness,
price, sales URL, age verification, or Fangate product identity. Those
values arrive only as validated structured fields; ``messages`` is
preserved verbatim for audit and never feeds any authoritative field.

The conversation surface accepts the canonical roles emitted by
``memory.context.build_context`` (system/user/assistant) and expects
messages to already be token-bounded by the memory layer — this module adds
no trimming of its own. Validation is per-message shape only.

Validation is strict and rejection-based (never clamped):

- IDs are genuine positive integers (no strings, booleans, or floats)
- scores are finite floats in [0.0, 1.0]; durations are finite and >= 0
- prices are non-negative integer minor units or None (pay-what-you-want)
- sales URLs, when present, must be well-formed http(s) URLs
- booleans accept only real booleans (no string/bool coercion anywhere)
- ``eligibility`` must be an actual :class:`commerce.models.PolicyDecision`
  verdict instance — serialized text can never claim eligibility for itself
- unknown fields are rejected (``extra="forbid"``)

Security: the model carries NO credential fields. Because extras are
forbidden, deep-fake structured credentials (API keys, webhook secrets,
bearer tokens, DB passwords, ciphertext) cannot be stored by the model.
Ordinary conversation text is never scrubbed.

This module is PURE: no I/O, no clock, no randomness, no event bus.
:meth:`CommerceConversationContext.to_decision_context` projects validated
state onto the deterministic engine exactly; no strategy decisions are made
here.
"""

import math
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from commerce.decision import CommerceDecisionContext
from commerce.models import OFFER_STATES, OfferState, PolicyDecision

# Canonical roles produced by memory/context.py::build_context().
CONVERSATION_ROLES = frozenset({"system", "user", "assistant"})

# Descriptive title cap (identity only, never commerce authority).
PRODUCT_TITLE_MAX = 200


def _strict_int(v: Any) -> Any:
    """Reject strings, booleans, and floats: only genuine integers pass."""
    if isinstance(v, (str, bool, float)):
        raise ValueError("must be an integer")  # noqa: TRY004 — pydantic wraps into ValidationError
    return v


def _strict_int_or_none(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (str, bool, float)):
        raise ValueError("must be an integer or null")  # noqa: TRY004 — pydantic wraps into ValidationError
    return v


def _reject_str_bool(v: Any) -> Any:
    """Reject strings/booleans for numeric fields (no lax coercion)."""
    if isinstance(v, (str, bool)):
        raise ValueError("must be a number")  # noqa: TRY004 — pydantic wraps into ValidationError
    return v


def _reject_str_bool_or_none(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (str, bool)):
        raise ValueError("must be a number or null")  # noqa: TRY004 — pydantic wraps into ValidationError
    return v


def _finite_non_negative(v: float | None) -> float | None:
    if v is None:
        return None
    if v < 0 or not math.isfinite(v):
        raise ValueError("must be a finite non-negative number")
    return v


def _non_negative_int(v: int | None) -> int | None:
    if v is None:
        return None
    if v < 0:
        raise ValueError("must be a non-negative integer")
    return v


StrictBool = Annotated[bool, Field(strict=True)]
StrictPositiveInt = Annotated[int, BeforeValidator(_strict_int), Field(gt=0)]
StrictNonNegativeInt = Annotated[int, BeforeValidator(_strict_int), Field(ge=0)]
PriceMinor = Annotated[
    int | None,
    BeforeValidator(_strict_int_or_none),
    AfterValidator(_non_negative_int),
]
ScoreFloat = Annotated[
    float,
    BeforeValidator(_reject_str_bool),
    Field(ge=0.0, le=1.0, allow_inf_nan=False),
]
NonNegativeFloat = Annotated[
    float | None,
    BeforeValidator(_reject_str_bool_or_none),
    AfterValidator(_finite_non_negative),
]


class CommerceConversationMessage(BaseModel):
    """One already-bounded conversation message (verbatim, untrusted)."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class ProductIdentity(BaseModel):
    """Identity/descriptive product info only — never commerce authority.

    Id, title, and availability describe WHICH Fangate product is in play.
    Price, sales URL, accessibility, and age-verification belong to
    ``ProductCommerceState``, which carries only app-verified values.
    """

    model_config = ConfigDict(extra="forbid")

    product_id: StrictPositiveInt
    title: Annotated[str, StringConstraints(max_length=PRODUCT_TITLE_MAX)] = ""
    available: StrictBool = True


class ProductCommerceState(BaseModel):
    """Authoritative commerce attributes, verified from app/Fangate state.

    There is no mechanism to populate these from conversation text: a fan
    cannot quote a price, paste a link, or claim accessibility here.
    ``price_minor`` is informational only (None means pay-what-you-want or
    unknown), matching ``commerce.eligibility.ProductEligibilityState``.
    """

    model_config = ConfigDict(extra="forbid")

    price_minor: PriceMinor = None
    sales_url: str | None = None
    is_accessible: StrictBool = True
    age_verification_required: StrictBool = False
    age_verified: StrictBool = False

    @field_validator("sales_url")
    @classmethod
    def _sales_url_must_be_http(cls, v: str | None) -> str | None:
        if v is None:
            return v
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("sales_url must be a valid http(s) URL")
        return v


class CommerceConversationContext(BaseModel):
    """Validated boundary between conversation and commerce state.

    Only ``user_id``, ``creator_id``, and ``eligibility`` are required; every
    other structured field defaults to the deterministic engine's neutral
    value and must be supplied by the caller when non-neutral state exists.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: StrictPositiveInt
    creator_id: StrictPositiveInt

    messages: list[CommerceConversationMessage] = Field(default_factory=list)

    eligibility: PolicyDecision

    product_identity: ProductIdentity | None = None
    product_state: ProductCommerceState | None = None

    relationship_score: ScoreFloat | None = None
    buying_intent_score: ScoreFloat | None = None

    messages_since_last_offer: StrictNonNegativeInt = 0
    messages_since_last_purchase: StrictNonNegativeInt = 0

    hours_since_last_offer: NonNegativeFloat = None
    hours_since_last_purchase: NonNegativeFloat = None

    recent_offer_count: StrictNonNegativeInt = 0
    recent_purchase_count: StrictNonNegativeInt = 0
    recent_sales_attempt_count: StrictNonNegativeInt = 0

    previous_offer_status: str | None = None

    has_active_offer: StrictBool = False
    has_relevant_product: StrictBool = True

    user_requested_content: StrictBool = False
    user_asked_about_price: StrictBool = False
    user_asked_to_buy: StrictBool = False

    creator_sales_enabled: StrictBool = True

    # Segment context (informational only — not authorization)
    user_segment_names: list[str] = Field(default_factory=list)
    user_segment_count: StrictNonNegativeInt = 0

    # Phase C: Relationship context (from derive_relationship_state)
    relationship_state: str = "cold"
    commercial_pressure: str = "none"
    tip_eligibility: str = "ineligible"
    tip_reason: str = ""
    handoff_needed: StrictBool = False
    handoff_reason: str | None = None

    # Phase C.1-B: Conversational intelligence
    conversational_phase: str = "unknown"
    negative_intent_count: StrictNonNegativeInt = 0
    signal_confidence: ScoreFloat | None = None
    fan_asks_question: StrictBool = False
    has_commercial_intent: StrictBool = False

    # Phase C.1-C: Behavioral feedback intelligence
    consecutive_rejections: StrictNonNegativeInt = 0
    last_rejection_type: str | None = None
    aftercare_status: str = "pending"
    total_purchases: StrictNonNegativeInt = 0
    total_tips_received: StrictNonNegativeInt = 0
    tip_suggestions_ignored: StrictNonNegativeInt = 0
    tip_suggestions_sent: StrictNonNegativeInt = 0
    hours_since_last_tip: NonNegativeFloat = None
    fan_expressed_appreciation: StrictBool = False
    fan_asked_how_to_support: StrictBool = False
    repeat_purchase_eligible: StrictBool = False
    commercial_paused: StrictBool = False
    post_purchase_satisfaction: str | None = None

    # Phase 9: turn-scoped commerce context (worker-supplied, advisory only).
    # None = legacy / unknown: fences dormant. Explicit values engage the
    # float/soft/relationship fences in commerce/decision.py.
    current_content_interest: bool | None = None
    current_content_disinterest: bool | None = None
    user_initiated_commercial: bool | None = None
    continuation_context: bool | None = None
    warmth_without_commercial_evidence: bool | None = None
    authorization_basis: str | None = None

    @field_validator("eligibility", mode="before")
    @classmethod
    def _eligibility_must_be_structured_verdict(cls, v: Any) -> Any:
        """eligibility is authoritative: only real PolicyDecision verdicts
        pass, and their booleans must already be genuine booleans."""
        if not isinstance(v, PolicyDecision):
            raise ValueError("eligibility must be a PolicyDecision instance")  # noqa: TRY004 — pydantic wraps into ValidationError
        if not isinstance(v.allowed, bool):
            raise ValueError("eligibility.allowed must be a real boolean")  # noqa: TRY004 — pydantic wraps into ValidationError
        if not isinstance(v.denial_reason, str):
            raise ValueError("eligibility.denial_reason must be a string")  # noqa: TRY004 — pydantic wraps into ValidationError
        return v

    @field_validator("previous_offer_status", mode="before")
    @classmethod
    def _offer_status_from_closed_set(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, OfferState):
            return v.value
        if isinstance(v, str) and v in OFFER_STATES:
            return v
        raise ValueError("previous_offer_status must be a known offer state")

    def to_decision_context(self) -> CommerceDecisionContext:
        """Project validated state onto the deterministic engine input.

        Pure projection, no strategy: conversation text plays NO role here —
        only structured fields are projected, so the engine can always trust
        that structured state is exactly what was validated.
        """
        return CommerceDecisionContext(
            user_id=self.user_id,
            creator_id=self.creator_id,
            eligibility=self.eligibility,
            relationship_score=self.relationship_score,
            buying_intent_score=self.buying_intent_score,
            messages_since_last_offer=self.messages_since_last_offer,
            messages_since_last_purchase=self.messages_since_last_purchase,
            hours_since_last_offer=self.hours_since_last_offer,
            hours_since_last_purchase=self.hours_since_last_purchase,
            recent_offer_count=self.recent_offer_count,
            recent_purchase_count=self.recent_purchase_count,
            recent_sales_attempt_count=self.recent_sales_attempt_count,
            previous_offer_status=self.previous_offer_status,
            has_active_offer=self.has_active_offer,
            has_relevant_product=self.has_relevant_product,
            user_requested_content=self.user_requested_content,
            user_asked_about_price=self.user_asked_about_price,
            user_asked_to_buy=self.user_asked_to_buy,
            creator_sales_enabled=self.creator_sales_enabled,
            # Phase C: Relationship context
            relationship_state=self.relationship_state,
            commercial_pressure=self.commercial_pressure,
            tip_eligibility=self.tip_eligibility,
            tip_reason=self.tip_reason,
            handoff_needed=self.handoff_needed,
            handoff_reason=self.handoff_reason,
            # Phase C.1-B: Conversational intelligence
            conversational_phase=self.conversational_phase,
            negative_intent_count=self.negative_intent_count,
            signal_confidence=self.signal_confidence,
            fan_asks_question=self.fan_asks_question,
            has_commercial_intent=self.has_commercial_intent,
            # Phase C.1-C: Behavioral feedback intelligence
            consecutive_rejections=self.consecutive_rejections,
            last_rejection_type=self.last_rejection_type,
            aftercare_status=self.aftercare_status,
            total_purchases=self.total_purchases,
            total_tips_received=self.total_tips_received,
            tip_suggestions_ignored=self.tip_suggestions_ignored,
            tip_suggestions_sent=self.tip_suggestions_sent,
            hours_since_last_tip=self.hours_since_last_tip,
            fan_expressed_appreciation=self.fan_expressed_appreciation,
            fan_asked_how_to_support=self.fan_asked_how_to_support,
            repeat_purchase_eligible=self.repeat_purchase_eligible,
            commercial_paused=self.commercial_paused,
            post_purchase_satisfaction=self.post_purchase_satisfaction,
            # Phase 9: turn-scoped commerce context (None = legacy, fences dormant).
            current_content_interest=self.current_content_interest,
            current_content_disinterest=self.current_content_disinterest,
            user_initiated_commercial=self.user_initiated_commercial,
            continuation_context=self.continuation_context,
            warmth_without_commercial_evidence=self.warmth_without_commercial_evidence,
            authorization_basis=self.authorization_basis,
        )
