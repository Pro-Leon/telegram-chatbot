"""Sunny V2 domain entities (Phase 1). Typed contracts only, no I/O."""

from relationship_v2.domain.activation import (
    ActivationDecision,
    ActivationPreconditions,
    ActivationScope,
)
from relationship_v2.domain.assembled import (
    AssembledContext,
    ConflictResolution,
    ContextSection,
)
from relationship_v2.domain.commerce import (
    CommerceContext,
    PurchaseFeedback,
    StrategyAction,
)
from relationship_v2.domain.commerce_ref import (
    CommerceActionRequest,
    CommerceActionResult,
    CommerceActionResultCode,
    CommerceContextRef,
)
from relationship_v2.domain.context import (
    ReentryContext,
    RelationshipContextSnapshot,
    RelationshipEvidence,
)
from relationship_v2.domain.conversation import (
    Conversation,
    ConversationLifecycle,
    ConversationState,
    ConversationTurn,
)
from relationship_v2.domain.escalation import EscalationStage, EscalationState
from relationship_v2.domain.event import (
    NON_MUTATING_TYPES,
    SCHEMA_VERSION,
    ProcessedEvent,
    V2Event,
    V2EventType,
    is_relationship_mutating,
)
from relationship_v2.domain.identity import FanScope
from relationship_v2.domain.inbound import (
    InboundDecision,
    InboundEvent,
    InboundVerdict,
    RejectReason,
)
from relationship_v2.domain.intimacy import (
    IntimateKind,
    IntimateMemory,
    IntimateStatus,
    is_valid_intimate_transition,
)
from relationship_v2.domain.memory import (
    MemoryEpisode,
    MemoryEpisodeType,
    MemoryFact,
    MemoryFactStatus,
    MemoryImportance,
)
from relationship_v2.domain.open_loop import (
    OpenLoop,
    OpenLoopPriority,
    OpenLoopStatus,
    is_valid_open_loop_transition,
)
from relationship_v2.domain.relationship import (
    Relationship,
    RelationshipLifecycle,
    RelationshipState,
)
from relationship_v2.domain.response import (
    GeneratedResponse,
    OutboundState,
    ResponseIntent,
    ResponsePlan,
    ValidationOutcome,
    ValidationVerdict,
)
from relationship_v2.domain.retirement import RetirementDecision, RetirementReadiness
from relationship_v2.domain.shadow import (
    ShadowDivergence,
    ShadowInputs,
    ShadowReport,
)
from relationship_v2.domain.signals import InteractionSignal, SignalPolarity
from relationship_v2.domain.strategy import (
    ConfidenceBreakdown,
    StrategyDecision,
    StrategyInputs,
)

__all__ = [
    "NON_MUTATING_TYPES",
    "SCHEMA_VERSION",
    "ActivationDecision",
    "ActivationPreconditions",
    "ActivationScope",
    "AssembledContext",
    "CommerceActionRequest",
    "CommerceActionResult",
    "CommerceActionResultCode",
    "CommerceContext",
    "CommerceContextRef",
    "ConfidenceBreakdown",
    "ConflictResolution",
    "ContextSection",
    "Conversation",
    "ConversationLifecycle",
    "ConversationState",
    "ConversationTurn",
    "EscalationStage",
    "EscalationState",
    "FanScope",
    "GeneratedResponse",
    "InboundDecision",
    "InboundEvent",
    "InboundVerdict",
    "InteractionSignal",
    "IntimateKind",
    "IntimateMemory",
    "IntimateStatus",
    "MemoryEpisode",
    "MemoryEpisodeType",
    "MemoryFact",
    "MemoryFactStatus",
    "MemoryImportance",
    "OpenLoop",
    "OpenLoopPriority",
    "OpenLoopStatus",
    "OutboundState",
    "ProcessedEvent",
    "PurchaseFeedback",
    "ReentryContext",
    "RejectReason",
    "Relationship",
    "RelationshipContextSnapshot",
    "RelationshipEvidence",
    "RelationshipLifecycle",
    "RelationshipState",
    "ResponseIntent",
    "ResponsePlan",
    "RetirementDecision",
    "RetirementReadiness",
    "ShadowDivergence",
    "ShadowInputs",
    "ShadowReport",
    "SignalPolarity",
    "StrategyAction",
    "StrategyDecision",
    "StrategyInputs",
    "V2Event",
    "V2EventType",
    "ValidationOutcome",
    "ValidationVerdict",
    "is_relationship_mutating",
    "is_valid_intimate_transition",
    "is_valid_open_loop_transition",
]
