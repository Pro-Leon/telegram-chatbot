"""Deterministic commerce strategy layer (Phase 5.4 Chk 2).

Pure translation of an ALREADY-DETERMINED :class:`CommerceDecision` into a
structured conversational strategy for the later response-generation layer.

Place in the pipeline:

    conversation context
          ↓
    DeepSeek signal extraction
          ↓
    deterministic commerce decision (commerce.decision)
          ↓
    [THIS MODULE] strategy selection
          ↓
    later: orchestration
          ↓
    later: DeepSeek response generation

Guarantees:

- Strategy NEVER decides whether an offer is allowed. That decision is owned
  by :mod:`commerce.decision`; this module only translates it. The output
  ``action`` always equals the input ``decision.action`` — never reinter-
  preted, never escalated, even if a caller passes contradictory context.
- Product authority is caller-supplied only. The strategy NEVER invents
  product_id, title, price, currency, sales URL, availability, or
  age-verification state. It only emits permission flags
  (``allow_price_reference`` / ``allow_product_reference``) derived from
  validated ``CommerceConversationContext`` product data; authoritative
  values travel separately to the response layer.
- Pressure is action-driven and deterministic: NONE for suppression, LOW for
  relationship/soft/follow-up, MODERATE only for an already-authorized
  ``offer_ppv``. There is NO HIGH pressure mode, no LLM input, no confidence
  coupling, no adaptive algorithm.
- Relationship-first and ethical constraints are structural: the response
  generator will read ``communication_constraints``, never prose generated
  here.

Security: no credentials, no payment data, no executable instructions, no
HTTP/send/enqueue directions. The models reject unknown fields.

This module is PURE and side-effect free: no I/O, no clock, no randomness,
no LLM clients, no event bus.
"""

import enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from commerce.context import CommerceConversationContext
from commerce.decision import CommerceDecision, CommerceReason
from commerce.models import CommerceAction


class SalesPressure(str, enum.Enum):
    """Deterministic sales pressure for a strategy.

    NONE  — suppression/no-sale actions
    LOW   — relationship building, soft offers, follow-ups
    MODERATE — an already-authorized offer_ppv decision
    DIRECT — fan explicitly requested commercial content

    There is deliberately NO HIGH mode: the strategy layer must never
    represent an aggressive sales posture.
    """

    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    DIRECT = "direct"


class StrategyKind(str, enum.Enum):
    """Stable strategy/instruction identifiers for the response layer.

    Values are fixed snake_case codes; the response generator maps each to
    its instruction set. Never rename or reuse.
    """

    NO_OFFER = "no_offer"
    RELATIONSHIP_BUILDING = "relationship_building"
    SOFT_OFFER = "soft_offer"
    FOLLOW_UP = "follow_up"
    OFFER_PPV = "offer_ppv"
    SUPPRESSED = "suppressed"
    CHAT = "chat"
    TIP_SUGGESTION = "tip_suggestion"
    OPERATOR_HANDOFF = "operator_handoff"


StrictBool = Annotated[bool, Field(strict=True)]


class CommunicationConstraints(BaseModel):
    """Structural prohibitions the response layer MUST respect.

    Every guarantee is expressed as a permission that defaults to/never
    exceeds False. The later DeepSeek response generator consumes these as
    hard constraints; none of them are prose or runtime instructions.
    """

    model_config = ConfigDict(extra="forbid")

    allow_repeated_pressure: StrictBool = False
    allow_urgency: StrictBool = False
    allow_guilt: StrictBool = False
    allow_scarcity_fabrication: StrictBool = False
    allow_coercion: StrictBool = False
    allow_repeat_ask_after_refusal: StrictBool = False
    allow_last_chance_language: StrictBool = False
    allow_invented_discounts: StrictBool = False
    allow_invented_deadlines: StrictBool = False
    allow_fabricated_social_proof: StrictBool = False
    allow_emotional_manipulation: StrictBool = False


# The universal, immutable constraint set for every strategy. There is no
# code path that raises any of these permissions.
DEFAULT_COMMUNICATION_CONSTRAINTS = CommunicationConstraints()


class CommerceStrategy(BaseModel):
    """Structured conversational strategy for an already-determined decision.

    Carries ONLY what the response-generation layer needs: the action, a
    stable strategy identifier, deterministic pressure, permission flags,
    relationship-first posture, follow-up allowance, the deterministic
    reason code, and structural communication constraints.

    It NEVER carries prices, sales URLs, product identities, credentials,
    payment data, or executable instructions.
    """

    model_config = ConfigDict(extra="forbid")

    action: CommerceAction
    kind: StrategyKind
    pressure: SalesPressure = SalesPressure.LOW
    allow_cta: StrictBool
    allow_price_reference: StrictBool
    allow_product_reference: StrictBool
    relationship_first: StrictBool
    follow_up_allowed: StrictBool
    reason: CommerceReason
    communication_constraints: CommunicationConstraints = Field(
        default_factory=CommunicationConstraints
    )


def _authoritative_price_reference_allowed(ctx: CommerceConversationContext) -> bool:
    """Price may only be referenced when authoritative product data exists.

    Verified price AND a valid sales URL AND an accessible product are all
    required; a fan quoting a price, or a product_state missing price/URL,
    never grants this.
    """
    ps = ctx.product_state
    if ps is None:
        return False
    return ps.price_minor is not None and ps.sales_url is not None and ps.is_accessible


def _supplied_product_reference_allowed(ctx: CommerceConversationContext) -> bool:
    """A product may only be referenced when one was explicitly supplied.

    Identity (id/title) must have been supplied and be available. The
    strategy never selects, manufactures, or infers a product.
    """
    if ctx.product_identity is None:
        return False
    return ctx.product_identity.available


def build_strategy(
    decision: CommerceDecision,
    context: CommerceConversationContext,
) -> CommerceStrategy:
    """Translate an already-determined decision into a conversational strategy.

    Pure and deterministic. The decision action is authoritative: it is
    never overridden or reinterpreted, and unknown actions are hard errors.

    ``context`` contributes ONLY product-authority permission flags; its
    conversation text and eligibility can never escalate or downgrade the
    decision.
    """
    try:
        action = CommerceAction(decision.action)
    except ValueError:
        raise ValueError(f"unsupported commerce action: {decision.action!r}") from None
    try:
        reason = CommerceReason(decision.reason_code)
    except ValueError:
        raise ValueError(f"invalid commerce reason code: {decision.reason_code!r}") from None

    price_ok = _authoritative_price_reference_allowed(context)
    product_ok = _supplied_product_reference_allowed(context)

    if action is CommerceAction.NO_OFFER:
        return CommerceStrategy(
            action=action,
            kind=StrategyKind.NO_OFFER,
            pressure=SalesPressure.NONE,
            allow_cta=False,
            allow_price_reference=False,
            allow_product_reference=False,
            relationship_first=True,
            follow_up_allowed=True,
            reason=reason,
        )
    if action is CommerceAction.RELATIONSHIP_BUILDING:
        return CommerceStrategy(
            action=action,
            kind=StrategyKind.RELATIONSHIP_BUILDING,
            pressure=SalesPressure.LOW,
            allow_cta=False,
            allow_price_reference=False,
            allow_product_reference=False,
            relationship_first=True,
            follow_up_allowed=True,
            reason=reason,
        )
    if action is CommerceAction.SOFT_OFFER:
        return CommerceStrategy(
            action=action,
            kind=StrategyKind.SOFT_OFFER,
            pressure=SalesPressure.LOW,
            allow_cta=True,
            allow_price_reference=price_ok,
            allow_product_reference=product_ok,
            relationship_first=True,
            follow_up_allowed=True,
            reason=reason,
        )
    if action is CommerceAction.FOLLOW_UP:
        return CommerceStrategy(
            action=action,
            kind=StrategyKind.FOLLOW_UP,
            pressure=SalesPressure.LOW,
            allow_cta=True,
            allow_price_reference=price_ok,
            allow_product_reference=product_ok,
            relationship_first=True,
            follow_up_allowed=True,
            reason=reason,
        )
    if action is CommerceAction.OFFER_PPV:
        return CommerceStrategy(
            action=action,
            kind=StrategyKind.OFFER_PPV,
            pressure=SalesPressure.MODERATE,
            allow_cta=True,
            allow_price_reference=price_ok,
            allow_product_reference=product_ok,
            relationship_first=True,
            follow_up_allowed=True,
            reason=reason,
        )
    if action is CommerceAction.DONT_OFFER:
        return CommerceStrategy(
            action=action,
            kind=StrategyKind.SUPPRESSED,
            pressure=SalesPressure.NONE,
            allow_cta=False,
            allow_price_reference=False,
            allow_product_reference=False,
            relationship_first=True,
            follow_up_allowed=False,
            reason=reason,
        )
    if action is CommerceAction.CHAT:
        return CommerceStrategy(
            action=action,
            kind=StrategyKind.CHAT,
            pressure=SalesPressure.NONE,
            allow_cta=False,
            allow_price_reference=False,
            allow_product_reference=False,
            relationship_first=True,
            follow_up_allowed=True,
            reason=reason,
        )
    if action is CommerceAction.TIP_SUGGESTION:
        return CommerceStrategy(
            action=action,
            kind=StrategyKind.TIP_SUGGESTION,
            pressure=SalesPressure.LOW,
            allow_cta=True,
            allow_price_reference=False,
            allow_product_reference=False,
            relationship_first=True,
            follow_up_allowed=True,
            reason=reason,
        )
    if action is CommerceAction.OPERATOR_HANDOFF:
        return CommerceStrategy(
            action=action,
            kind=StrategyKind.OPERATOR_HANDOFF,
            pressure=SalesPressure.NONE,
            allow_cta=False,
            allow_price_reference=False,
            allow_product_reference=False,
            relationship_first=True,
            follow_up_allowed=False,
            reason=reason,
        )
    raise ValueError(f"unsupported commerce action: {action!r}")  # pragma: no cover
