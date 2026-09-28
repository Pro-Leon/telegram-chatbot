"""Context Engine Canary Observation Record (Phase 77).

Structured observation records for the A/B canary comparison between
the Context Engine + Qwen path and the existing authoritative 3-LLM pipeline.

SAFETY:
- Records are for forensic analysis only
- Records do not affect production behavior
- Records are not persisted to production databases
- Sensitive content is not logged unnecessarily
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any


class DisagreementType(str, enum.Enum):
    """Taxonomy of disagreements between authoritative path and canary path.

    FULL_AGREEMENT: Both paths agree on all decision dimensions.
    INTENT_DISAGREEMENT: Intent differs, no commerce/safety conflict.
    COMMERCE_DISAGREEMENT: Commerce action differs (HIGH PRIORITY).
    HANDOFF_DISAGREEMENT: Operator handoff suggestion differs.
    RESPONSE_DISAGREEMENT: Decision equivalent, response text differs.
    STRUCTURED_OUTPUT_FAILURE: Canary output cannot be parsed/validated.
    CONTEXT_FAILURE: Context Engine fails or produces invalid context.
    AUTHORITY_VIOLATION_ATTEMPT: Canary specifies authoritative field.
    INFRASTRUCTURE_FAILURE: Timeout, provider error, model error.
    """

    FULL_AGREEMENT = "full_agreement"
    INTENT_DISAGREEMENT = "intent_disagreement"
    COMMERCE_DISAGREEMENT = "commerce_disagreement"
    HANDOFF_DISAGREEMENT = "handoff_disagreement"
    RESPONSE_DISAGREEMENT = "response_disagreement"
    STRUCTURED_OUTPUT_FAILURE = "structured_output_failure"
    CONTEXT_FAILURE = "context_failure"
    AUTHORITY_VIOLATION_ATTEMPT = "authority_violation_attempt"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class AuthorityViolationType(str, enum.Enum):
    """Types of authority violations the canary might attempt."""

    PRICE_AUTHORIZATION = "price_authorization"
    OFFER_CREATION = "offer_creation"
    PRODUCT_IDENTITY = "product_identity"
    PAYMENT_STATE = "payment_state"
    SEND_AUTHORIZATION = "send_authorization"
    COMMERCE_STATE_MUTATION = "commerce_state_mutation"


@dataclass(frozen=True)
class AuthoritativePathSnapshot:
    """Snapshot of the authoritative pipeline's state for comparison.

    Captures the key decision points from the existing 3-LLM pipeline
    so that the canary path can be compared against them.
    """

    # From LLM #1 (extract_commerce_signals)
    purchase_intent: float = 0.0
    explicit_purchase_request: bool = False
    price_interest: float = 0.0
    content_interest: float = 0.0
    negative_sentiment: float = 0.0
    primary_intent: str = "unknown"
    intent_tags: list[str] = field(default_factory=list)

    # From deterministic commerce layer
    commerce_action: str = "none"  # NO_OFFER, SOFT_OFFER, OFFER_PPV, etc.
    commerce_allowed: bool = False
    handoff_required: bool = False

    # From LLM #3 (score_draft)
    draft_score: float = 0.0
    hard_flags: list[str] = field(default_factory=list)

    # Send decision
    would_send: bool = False
    would_operator_queue: bool = False

    # Timing
    total_latency_ms: float = 0.0


@dataclass
class CanaryObservationRecord:
    """Complete observation record for A/B canary comparison.

    Captures everything needed for forensic analysis of the canary path
    vs the authoritative path.
    """

    # Identifiers
    generation_id: str = ""
    user_id: int = 0
    creator_id: int | None = None
    timestamp: float = field(default_factory=time.time)

    # Input
    user_message: str = ""
    message_length: int = 0

    # Context Engine metrics
    context_engine_enabled: bool = False
    context_engine_ms: float = 0.0
    context_items_gathered: int = 0
    context_items_selected: int = 0
    context_tokens: int = 0
    context_chars: int = 0
    context_rendered_messages: int = 0

    # Canary Qwen generation
    canary_model: str = ""
    canary_generation_ms: float = 0.0
    canary_parse_success: bool = False
    canary_response_length: int = 0
    canary_intent: str = "unknown"
    canary_commerce_action: str = "none"
    canary_handoff_suggested: bool = False
    canary_confidence: float = 0.0

    # Authoritative path
    authoritative: AuthoritativePathSnapshot = field(
        default_factory=AuthoritativePathSnapshot
    )

    # Comparison
    disagreement: DisagreementType = DisagreementType.FULL_AGREEMENT
    intent_agreement: bool = True
    commerce_agreement: bool = True
    handoff_agreement: bool = True
    response_similar: bool = True

    # Authority violations
    authority_violations: list[AuthorityViolationType] = field(default_factory=list)

    # Failure info
    canary_failed: bool = False
    canary_error: str | None = None
    context_engine_failed: bool = False
    context_engine_error: str | None = None

    # Total timing
    total_canary_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for logging/storage."""
        return {
            "generation_id": self.generation_id,
            "user_id": self.user_id,
            "creator_id": self.creator_id,
            "timestamp": self.timestamp,
            "message_length": self.message_length,
            "context_engine": {
                "enabled": self.context_engine_enabled,
                "ms": self.context_engine_ms,
                "items_gathered": self.context_items_gathered,
                "items_selected": self.context_items_selected,
                "tokens": self.context_tokens,
                "chars": self.context_chars,
                "failed": self.context_engine_failed,
                "error": self.context_engine_error,
            },
            "canary": {
                "model": self.canary_model,
                "generation_ms": self.canary_generation_ms,
                "parse_success": self.canary_parse_success,
                "response_length": self.canary_response_length,
                "intent": self.canary_intent,
                "commerce_action": self.canary_commerce_action,
                "handoff_suggested": self.canary_handoff_suggested,
                "confidence": self.canary_confidence,
                "failed": self.canary_failed,
                "error": self.canary_error,
            },
            "authoritative": {
                "purchase_intent": self.authoritative.purchase_intent,
                "primary_intent": self.authoritative.primary_intent,
                "commerce_action": self.authoritative.commerce_action,
                "handoff_required": self.authoritative.handoff_required,
                "draft_score": self.authoritative.draft_score,
                "would_send": self.authoritative.would_send,
                "total_latency_ms": self.authoritative.total_latency_ms,
            },
            "comparison": {
                "disagreement": self.disagreement.value,
                "intent_agreement": self.intent_agreement,
                "commerce_agreement": self.commerce_agreement,
                "handoff_agreement": self.handoff_agreement,
                "response_similar": self.response_similar,
            },
            "authority_violations": [v.value for v in self.authority_violations],
            "total_canary_ms": self.total_canary_ms,
        }
