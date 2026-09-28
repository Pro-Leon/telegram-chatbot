"""Context Engine foundation (Phase 70).

A standalone, deterministic, testable subsystem for assembling compact
context windows for LLM generation.

Architecture (Phase 69):

    AUTHORITATIVE STATE
            ↓
        CONTEXT ENGINE
            ↓
        COMPACT CONTEXT
            ↓
        ONE QWEN GENERATION
            ↓
        STRUCTURED OUTPUT
            ↓
        DETERMINISTIC AUTHORITY
            ↓
        SEND / HANDOFF

Phase 70 implements ONLY the Context Engine foundation.
The existing production architecture remains authoritative and untouched.

Invariants:
- DETERMINISTIC: identical inputs → identical outputs
- READ-ONLY: no writes, no side effects
- BOUNDED: every list is capped; overall context size is bounded
- CREATOR-SCOPED: every query filters by creator_id
- FAILURE-ISOLATED: any single source failure degrades gracefully
- NO SECRETS: credentials, API keys, ciphertext never exposed
- NO AUTHORITY: context is informational only, never authorizes actions
"""

from context_engine.assembler import ContextAssembler
from context_engine.budget import TokenBudgetManager
from context_engine.dedup import ContextDeduplicator
from context_engine.gatherer import (
    CommerceStateSource,
    ContextGatherer,
    ConversationHistorySource,
    DataSource,
    EmbeddedKnowledgeSource,
    FanStateSource,
    GathererConfig,
    MemorySource,
    PersonaSource,
    TemporalSource,
)
from context_engine.integration import (
    ContextEngineIntegration,
    ContextPipelineResult,
    ContextRequest,
)
from context_engine.models import (
    AuthorityLevel,
    ContextCategory,
    ContextItem,
    ContextSnapshot,
    RetrievalScore,
)
from context_engine.renderer import CompactRenderer
from context_engine.scorer import ContextScorer
from context_engine.worker_integration import (
    ContextEngineObservation,
    observe_context_engine,
)

__all__ = [
    "AuthorityLevel",
    "CommerceStateSource",
    "CompactRenderer",
    "ContextAssembler",
    "ContextCategory",
    "ContextDeduplicator",
    "ContextEngineIntegration",
    "ContextEngineObservation",
    "ContextGatherer",
    "ContextItem",
    "ContextPipelineResult",
    "ContextRequest",
    "ContextScorer",
    "ContextSnapshot",
    "ConversationHistorySource",
    "DataSource",
    "EmbeddedKnowledgeSource",
    "FanStateSource",
    "GathererConfig",
    "MemorySource",
    "PersonaSource",
    "RetrievalScore",
    "TemporalSource",
    "TokenBudgetManager",
    "observe_context_engine",
]
