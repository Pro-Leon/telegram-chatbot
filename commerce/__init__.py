"""Commerce domain (Phase 5.1A).

CRM-side offer state + fan purchase attribution for Fangate PPV sales.
The core package has no dependency on the realtime layer; only the Phase 5.3C
execution module (and tests) reach the Fangate service for live verification.
Proposals flow from the AI layer through models into the DAO.
"""

from .attribution import attribute_purchase
from .context import (
    PRODUCT_TITLE_MAX,
    CommerceConversationContext,
    CommerceConversationMessage,
    ProductCommerceState,
    ProductIdentity,
)
from .decision import (
    DEFAULT_COMMERCE_POLICY,
    CommerceDecision,
    CommerceDecisionContext,
    CommerceDecisionPolicy,
    CommerceReason,
)
from .deepseek import (
    COMMERCE_SIGNAL_EXTRACTION_SYSTEM,
    extract_commerce_signals,
)
from .deepseek_response import (
    CommerceResponse,
    CommerceResponseInput,
    CommerceResponseStatus,
    generate_commerce_response,
)
from .eligibility import (
    OfferContext,
    ProductEligibilityState,
    UserEligibilityState,
    evaluate_ppv_eligibility,
)
from .execution import ExecutionResult, ExecutionStatus, execute_ppv
from .models import (
    OFFER_STATES,
    CommerceAction,
    CommerceProposal,
    OfferState,
    PolicyDecision,
    PpvOffer,
    PurchaseRecord,
)
from .orchestrator import (
    DEFAULT_CREATED_BY,
    ORCHESTRATION_FAILURE_CODES,
    CommerceOrchestrationResult,
    orchestrate_commerce,
)
from .pipeline import (
    PIPELINE_CREATED_BY,
    PIPELINE_FAILURE_CODES,
    CommercePipelineRequest,
    CommercePipelineResult,
    CommercePipelineStatus,
    build_conversation_context,
    role_content_messages,
    run_commerce_pipeline,
)
from .signals import (
    CommerceSignals,
    decide_from_signals,
    signals_to_context,
)
from .strategy import (
    DEFAULT_COMMUNICATION_CONSTRAINTS,
    CommerceStrategy,
    CommunicationConstraints,
    SalesPressure,
    StrategyKind,
    build_strategy,
)

__all__ = [
    "COMMERCE_SIGNAL_EXTRACTION_SYSTEM",
    "DEFAULT_COMMERCE_POLICY",
    "DEFAULT_COMMUNICATION_CONSTRAINTS",
    "DEFAULT_CREATED_BY",
    "OFFER_STATES",
    "ORCHESTRATION_FAILURE_CODES",
    "PIPELINE_CREATED_BY",
    "PIPELINE_FAILURE_CODES",
    "PRODUCT_TITLE_MAX",
    "CommerceAction",
    "CommerceConversationContext",
    "CommerceConversationMessage",
    "CommerceDecision",
    "CommerceDecisionContext",
    "CommerceDecisionPolicy",
    "CommerceOrchestrationResult",
    "CommercePipelineRequest",
    "CommercePipelineResult",
    "CommercePipelineStatus",
    "CommerceProposal",
    "CommerceReason",
    "CommerceResponse",
    "CommerceResponseInput",
    "CommerceResponseStatus",
    "CommerceSignals",
    "CommerceStrategy",
    "CommunicationConstraints",
    "ExecutionResult",
    "ExecutionStatus",
    "OfferContext",
    "OfferState",
    "PolicyDecision",
    "PpvOffer",
    "ProductCommerceState",
    "ProductEligibilityState",
    "ProductIdentity",
    "PurchaseRecord",
    "SalesPressure",
    "StrategyKind",
    "UserEligibilityState",
    "attribute_purchase",
    "build_conversation_context",
    "build_strategy",
    "decide_from_signals",
    "evaluate_ppv_eligibility",
    "execute_ppv",
    "extract_commerce_signals",
    "generate_commerce_response",
    "orchestrate_commerce",
    "role_content_messages",
    "run_commerce_pipeline",
    "signals_to_context",
]
