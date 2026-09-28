"""Behavioral feedback events for Phase C.1-C.

Every meaningful commercial event produces a structured BehavioralEvent that
influences future decisions. Events are lightweight, creator-scoped, and
purely additive — they never modify transaction truth (which stays in the
provider DB).

Design principles:
- Structured, not raw conversation data
- Creator-scoped (isolated per creator)
- Deterministic event types (no LLM classification needed)
- Influences future decisions without duplicating transaction truth
"""

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# In-memory store for behavioral events (placeholder for DB-backed storage)
_behavioral_store: list["BehavioralEvent"] = []


class FeedbackEventType(str, enum.Enum):
    """Types of behavioral feedback events."""

    # Purchase outcomes
    PURCHASE_COMPLETED = "purchase_completed"
    PURCHASE_FULFILLMENT_FAILED = "purchase_fulfillment_failed"
    PURCHASE_DUPLICATE_BLOCKED = "purchase_duplicate_blocked"

    # Rejection outcomes
    HARD_REJECTION = "hard_rejection"
    SOFT_REJECTION = "soft_rejection"
    PRICE_OBJECTION = "price_objection"
    REJECTION_AMBIGUOUS = "rejection_ambiguous"

    # Offer outcomes
    OFFER_IGNORED = "offer_ignored"
    OFFER_CLICKED = "offer_clicked"
    OFFER_ACCEPTED = "offer_accepted"
    OFFER_FATIGUE_DETECTED = "offer_fatigue_detected"

    # Tip outcomes
    TIP_SUGGESTED = "tip_suggested"
    TIP_RECEIVED = "tip_received"
    TIP_IGNORED = "tip_ignored"
    TIP_COOLDOWN_ACTIVE = "tip_cooldown_active"

    # Aftercare
    AFTERCARE_SENT = "aftercare_sent"
    AFTERCARE_POSITIVE_RESPONSE = "aftercare_positive_response"
    AFTERCARE_COMPLAINT = "aftercare_complaint"

    # Complaint
    COMPLAINT_RECEIVED = "complaint_received"

    # Engagement
    REENGAGEMENT_SUCCESSFUL = "reengagement_successful"
    REENGAGEMENT_FAILED = "reengagement_failed"


class RejectionType(str, enum.Enum):
    """Structured rejection classification.

    Each type produces different cooldown and pressure consequences.
    """

    HARD = "hard"           # "no", "don't want it", "stop asking", "not interested"
    SOFT = "soft"           # "maybe later", "not right now", "I'll think about it"
    PRICE_OBJECTION = "price_objection"  # "too expensive", "can't afford"
    UNCERTAIN = "uncertain"  # changing topic, vague, ambiguous


# Rejection severity mapping (higher = more restraint needed)
REJECTION_SEVERITY: dict[RejectionType, float] = {
    RejectionType.HARD: 1.5,       # Explicit rejection → longest cooldown
    RejectionType.PRICE_OBJECTION: 1.0,  # Price hesitation → moderate cooldown
    RejectionType.SOFT: 0.5,       # Soft hesitation → shorter cooldown
    RejectionType.UNCERTAIN: 0.3,  # Ambiguous signal → shortest cooldown
}


class AftercareStatus(str, enum.Enum):
    """Post-purchase aftercare lifecycle."""

    PENDING = "pending"          # Purchase confirmed, aftercare not yet sent
    SENT = "sent"                # Aftercare message sent
    POSITIVE_RESPONSE = "positive"  # Fan responded positively
    COMPLAINT = "complaint"      # Fan complained about purchase
    SKIPPED = "skipped"          # Aftercare skipped (complaint, timeout)
    EXPIRED = "expired"          # Aftercare window passed without response


class TipContext(str, enum.Enum):
    """Context for tip suggestions."""

    APPRECIATION = "appreciation"     # Fan expressed gratitude
    EXPLICIT_SUPPORT = "explicit"     # Fan asked how to support
    RELATIONSHIP_BASED = "relationship"  # Warm relationship, natural moment
    POST_PURCHASE = "post_purchase"   # After successful purchase
    UNSOLICITED = "unsolicited"       # No contextual trigger


@dataclass(frozen=True)
class BehavioralEvent:
    """Structured behavioral feedback event.

    Pure data — no I/O, no provider calls, no credentials.
    Events are recorded and used to influence future decisions.
    """

    event_type: FeedbackEventType
    creator_id: int
    user_id: int
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    # Optional context fields
    rejection_type: RejectionType | None = None
    tip_context: TipContext | None = None
    aftercare_status: AftercareStatus | None = None
    product_id: int | None = None
    transaction_id: str | None = None
    offer_id: int | None = None


@dataclass
class BehavioralSummary:
    """Aggregated behavioral history for a fan/creator pair.

    Updated incrementally as events are recorded. Used to influence
    future decisions without querying the full event history.
    """

    # Rejection tracking
    consecutive_rejections: int = 0
    last_rejection_type: RejectionType | None = None
    total_hard_rejections: int = 0
    total_soft_rejections: int = 0
    total_price_objections: int = 0

    # Purchase tracking
    total_purchases: int = 0
    last_purchase_hours_ago: float | None = None
    aftercare_status: AftercareStatus = AftercareStatus.PENDING
    post_purchase_satisfaction: str | None = None  # "positive", "neutral", "negative"

    # Tip tracking
    total_tips_received: int = 0
    last_tip_hours_ago: float | None = None
    tip_suggestions_ignored: int = 0
    tip_suggestions_sent: int = 0

    # Offer tracking
    total_offers: int = 0
    offers_ignored: int = 0
    offers_clicked: int = 0

    # Cooldown state
    cooldown_hours_remaining: float = 0.0
    commercial_paused: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize for context injection."""
        return {
            "consecutive_rejections": self.consecutive_rejections,
            "last_rejection_type": self.last_rejection_type.value if self.last_rejection_type else None,
            "total_purchases": self.total_purchases,
            "aftercare_status": self.aftercare_status.value,
            "post_purchase_satisfaction": self.post_purchase_satisfaction,
            "total_tips_received": self.total_tips_received,
            "tip_suggestions_ignored": self.tip_suggestions_ignored,
            "commercial_paused": self.commercial_paused,
        }


def classify_rejection(
    negative_intent_tags: list[str],
    negative_sentiment: float,
    price_interest: float,
    intent_tags: list[str],
) -> RejectionType:
    """Classify rejection type from signal observations.

    Pure function — no I/O, no randomness.
    """
    tags = set(negative_intent_tags)

    # Price objection: hesitation + price interest
    if "hesitation" in tags and price_interest >= 0.60:
        return RejectionType.PRICE_OBJECTION

    # Hard rejection: explicit rejection signal
    if "rejection" in tags:
        return RejectionType.HARD

    # Complaint: negative sentiment is high
    if "complaint" in tags or negative_sentiment >= 0.70:
        return RejectionType.HARD

    # Soft rejection: hesitation without price signal
    if "hesitation" in tags:
        return RejectionType.SOFT

    # Uncertain: some negative signal but no clear type
    if negative_sentiment >= 0.30:
        return RejectionType.UNCERTAIN

    # No clear rejection
    return RejectionType.UNCERTAIN


def is_repeat_purchase_eligible(
    *,
    total_purchases: int,
    hours_since_last_purchase: float | None,
    current_engagement: bool,
    post_purchase_satisfaction: str | None,
    commercial_paused: bool,
    consecutive_rejections: int,
    hours_since_last_rejection: float | None = None,
    min_days_since_purchase: float = 7.0,
) -> bool:
    """Determine if a fan is eligible for repeat-purchase suggestion.

    Not just purchase_count > 0 — requires multiple factors.

    Pure function — no I/O, no randomness.
    """
    # Never suggest during commercial pause
    if commercial_paused:
        return False

    # Must have at least one previous purchase
    if total_purchases < 1:
        return False

    # Must have cooled down since last purchase
    if (
        hours_since_last_purchase is not None
        and hours_since_last_purchase < min_days_since_purchase * 24
    ):
        return False

    # Must be actively engaged
    if not current_engagement:
        return False

    # Dissatisfied buyers are not eligible
    if post_purchase_satisfaction == "negative":
        return False

    # Recent rejection overrides eligibility
    if (
        consecutive_rejections > 0
        and hours_since_last_rejection is not None
        and hours_since_last_rejection < 48
    ):
        return False

    return True
