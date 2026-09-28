"""Context Engine Canary Structured Output Schema (Phase 77).

Pydantic models for the structured output of the Context Engine +
one-generation Qwen path. The canary Qwen generation MUST return
output conforming to this schema.

SAFETY:
- This schema is for OBSERVATION ONLY
- The model CANNOT authorize prices, products, offers, or payments
- The model CANNOT send messages or mutate state
- Any commerce action is a PROPOSAL that must pass deterministic authority
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field


class CanaryIntent(str, enum.Enum):
    """Interpreted intent from the fan's message."""

    PURCHASE = "purchase"
    PRICE_INQUIRY = "price_inquiry"
    CONTENT_REQUEST = "content_request"
    NEGOTIATION = "negotiation"
    REJECTION = "rejection"
    HESITATION = "hesitation"
    COMPLAINT = "complaint"
    CASUAL_CHAT = "casual_chat"
    PERSONAL_DISCLOSURE = "personal_disclosure"
    QUESTION = "question"
    GREETING = "greeting"
    FAREWELL = "farewell"
    GRATITUDE = "gratitude"
    CURIOUS = "curious"
    POST_PURCHASE = "post_purchase"
    REPEAT_PURCHASE = "repeat_purchase"
    AFTERCARE = "aftercare"
    UNKNOWN = "unknown"


class CanaryAction(str, enum.Enum):
    """Proposed action (observational only, not executed)."""

    NONE = "none"
    RESPOND = "respond"
    OFFER_PPV = "offer_ppv"
    SOFT_OFFER = "soft_offer"
    RELATIONSHIP_BUILDING = "relationship_building"
    FOLLOW_UP = "follow_up"
    TIP_SUGGESTION = "tip_suggestion"
    OPERATOR_HANDOFF = "operator_handoff"


class CanaryCommerceIntent(BaseModel):
    """Proposed commerce interpretation (observational only).

    This is a PROPOSAL. The deterministic commerce layer decides legality.
    """

    model_config = ConfigDict(extra="forbid")

    has_commercial_intent: bool = Field(
        default=False,
        description="Whether the message contains commercial intent",
    )
    proposed_action: CanaryAction = Field(
        default=CanaryAction.NONE,
        description="Proposed commerce action (not executed)",
    )
    price_mentioned: bool = Field(
        default=False,
        description="Whether a price was mentioned in the message",
    )
    requested_price: float | None = Field(
        default=None,
        description="Price mentioned by fan, if any",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence in commerce interpretation",
    )
    reasoning: str = Field(
        default="",
        max_length=500,
        description="Brief reasoning for the commerce interpretation",
    )


class CanaryOutput(BaseModel):
    """Structured output from the Context Engine + Qwen canary path.

    This is the complete structured response that the canary Qwen
    generation produces. It is compared against the authoritative
    pipeline's decisions for A/B analysis.

    SAFETY: This output is OBSERVATIONAL ONLY.
    - response: the text response (not sent)
    - intent: interpreted intent
    - commerce: commerce interpretation (proposal only)
    - handoff_required: whether operator handoff is suggested
    - conversation_state: interpreted conversation state
    """

    model_config = ConfigDict(extra="forbid")

    response: str = Field(
        default="",
        max_length=2000,
        description="Proposed response text (not sent)",
    )
    intent: CanaryIntent = Field(
        default=CanaryIntent.UNKNOWN,
        description="Interpreted primary intent",
    )
    commerce: CanaryCommerceIntent = Field(
        default_factory=CanaryCommerceIntent,
        description="Commerce interpretation (proposal only)",
    )
    handoff_required: bool = Field(
        default=False,
        description="Whether operator handoff is suggested",
    )
    handoff_reason: str = Field(
        default="",
        max_length=200,
        description="Reason for handoff suggestion",
    )
    conversation_state: str = Field(
        default="unknown",
        max_length=50,
        description="Interpreted conversation state",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall confidence in interpretation",
    )
    reasoning: str = Field(
        default="",
        max_length=500,
        description="Brief reasoning summary",
    )

    @classmethod
    def low_confidence(cls) -> CanaryOutput:
        """Produce a safe low-confidence output for parse failures."""
        return cls(
            confidence=0.0,
            reasoning="parse_failure",
        )
