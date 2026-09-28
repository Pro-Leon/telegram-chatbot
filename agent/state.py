"""Agent state module.

Compact agent state for the AI-native CRM runtime.
This module defines the frozen state that travels through the agent loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent.state")


@dataclass(frozen=True)
class AgentIdentity:
    """Runtime identity injected by application. Never LLM-specified."""
    creator_id: int
    user_id: int
    sales_enabled: bool = False
    autonomy_enabled: bool = True


@dataclass(frozen=True)
class ConversationContext:
    """Current conversation state."""
    current_message: str
    history: tuple[dict[str, str], ...] = ()
    persona: str | None = None


@dataclass(frozen=True)
class MemoryContext:
    """Relevant memory retrieved for this turn."""
    profile: dict[str, Any] | None = None
    summary: str | None = None
    relevant_memories: tuple[str, ...] = ()
    embedding_available: bool = False


@dataclass(frozen=True)
class RelationshipContext:
    """Relationship and behavioral state."""
    relationship_state: str = "NEW"
    commercial_pressure: str = "NONE"
    tip_eligibility: str = "NOT_ELIGIBLE"
    aftercare_status: str | None = None
    rejection_count: int = 0
    consecutive_rejections: int = 0
    tip_suggestions_sent: int = 0
    tip_suggestions_ignored: int = 0
    hours_since_last_tip: float = 0.0
    hours_since_last_offer: float = 0.0
    hours_since_last_purchase: float = 0.0


@dataclass(frozen=True)
class CommerceContext:
    """Commerce eligibility and state."""
    eligible_products: tuple[dict[str, Any], ...] = ()
    active_offers: tuple[dict[str, Any], ...] = ()
    purchase_history: tuple[dict[str, Any], ...] = ()
    has_relevant_product: bool = False
    creator_capabilities: dict[str, Any] | None = None


@dataclass(frozen=True)
class AgentState:
    """Complete agent state for one inbound turn.
    
    Frozen — immutable throughout the agent loop.
    Compact — only essential context for this turn.
    Isolated — scoped to one user, one creator, one conversation.
    """
    # Identity (injected by application, never LLM-specified)
    identity: AgentIdentity
    
    # Conversation
    conversation: ConversationContext
    
    # Memory
    memory: MemoryContext
    
    # Relationship
    relationship: RelationshipContext
    
    # Commerce
    commerce: CommerceContext
    
    # Tools available to agent
    available_tools: tuple[str, ...] = ()
    
    # Limits
    max_tool_calls: int = 5
    max_response_tokens: int = 500
    max_response_time_seconds: float = 30.0
    
    @classmethod
    def create(
        cls,
        *,
        creator_id: int,
        user_id: int,
        current_message: str,
        history: tuple[dict[str, str], ...] = (),
        profile: dict[str, Any] | None = None,
        summary: str | None = None,
        relevant_memories: tuple[str, ...] = (),
        relationship_state: str = "NEW",
        commercial_pressure: str = "NONE",
        tip_eligibility: str = "NOT_ELIGIBLE",
        aftercare_status: str | None = None,
        rejection_count: int = 0,
        consecutive_rejections: int = 0,
        tip_suggestions_sent: int = 0,
        tip_suggestions_ignored: int = 0,
        hours_since_last_tip: float = 0.0,
        hours_since_last_offer: float = 0.0,
        hours_since_last_purchase: float = 0.0,
        eligible_products: tuple[dict[str, Any], ...] = (),
        active_offers: tuple[dict[str, Any], ...] = (),
        purchase_history: tuple[dict[str, Any], ...] = (),
        has_relevant_product: bool = False,
        creator_capabilities: dict[str, Any] | None = None,
        sales_enabled: bool = False,
        autonomy_enabled: bool = True,
        persona: str | None = None,
        available_tools: tuple[str, ...] = (),
        max_tool_calls: int = 5,
        max_response_tokens: int = 500,
        max_response_time_seconds: float = 30.0,
    ) -> AgentState:
        """Factory method to create AgentState from individual components."""
        return cls(
            identity=AgentIdentity(
                creator_id=creator_id,
                user_id=user_id,
                sales_enabled=sales_enabled,
                autonomy_enabled=autonomy_enabled,
            ),
            conversation=ConversationContext(
                current_message=current_message,
                history=history,
                persona=persona,
            ),
            memory=MemoryContext(
                profile=profile,
                summary=summary,
                relevant_memories=relevant_memories,
                embedding_available=bool(relevant_memories),
            ),
            relationship=RelationshipContext(
                relationship_state=relationship_state,
                commercial_pressure=commercial_pressure,
                tip_eligibility=tip_eligibility,
                aftercare_status=aftercare_status,
                rejection_count=rejection_count,
                consecutive_rejections=consecutive_rejections,
                tip_suggestions_sent=tip_suggestions_sent,
                tip_suggestions_ignored=tip_suggestions_ignored,
                hours_since_last_tip=hours_since_last_tip,
                hours_since_last_offer=hours_since_last_offer,
                hours_since_last_purchase=hours_since_last_purchase,
            ),
            commerce=CommerceContext(
                eligible_products=eligible_products,
                active_offers=active_offers,
                purchase_history=purchase_history,
                has_relevant_product=has_relevant_product,
                creator_capabilities=creator_capabilities,
            ),
            available_tools=available_tools,
            max_tool_calls=max_tool_calls,
            max_response_tokens=max_response_tokens,
            max_response_time_seconds=max_response_time_seconds,
        )
    
    def to_compact_dict(self) -> dict[str, Any]:
        """Convert to compact dictionary for LLM context injection."""
        return {
            "user_id": self.identity.user_id,
            "creator_id": self.identity.creator_id,
            "current_message": self.conversation.current_message,
            "relationship_state": self.relationship.relationship_state,
            "commercial_pressure": self.relationship.commercial_pressure,
            "tip_eligibility": self.relationship.tip_eligibility,
            "has_relevant_product": self.commerce.has_relevant_product,
            "recent_purchases": len(self.commerce.purchase_history),
            "active_offers": len(self.commerce.active_offers),
            "available_tools": list(self.available_tools),
        }
