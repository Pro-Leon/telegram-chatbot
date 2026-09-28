"""Agent tools module.

Governed agent tools wrapping existing CRM capabilities.
Each tool validates authority before execution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from agent.state import AgentState, AgentIdentity

logger = logging.getLogger("agent.tools")


@dataclass
class AgentTool:
    """Governed agent tool with authority validation."""
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any], AgentState], Awaitable[dict[str, Any]]]
    authority_check: Callable[[AgentState], bool] | None = None
    timeout: float = 10.0
    
    def validate_authority(self, state: AgentState) -> bool:
        """Check if agent state allows this tool."""
        if self.authority_check is None:
            return True
        return self.authority_check(state)
    
    def to_schema(self) -> dict[str, Any]:
        """Convert to JSON Schema for LLM tool calling."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class AgentToolRegistry:
    """Registry of governed agent tools."""
    
    def __init__(self):
        self._tools: dict[str, AgentTool] = {}
    
    def register(self, tool: AgentTool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name}")
    
    def get(self, name: str) -> AgentTool | None:
        """Get tool by name."""
        return self._tools.get(name)
    
    def list_tools(self) -> list[AgentTool]:
        """List all registered tools."""
        return list(self._tools.values())
    
    def get_schemas(self) -> list[dict[str, Any]]:
        """Get JSON schemas for all tools."""
        return [tool.to_schema() for tool in self._tools.values()]


# Global registry
_registry = AgentToolRegistry()


def get_tool_registry() -> AgentToolRegistry:
    """Get the global tool registry."""
    return _registry


def register_tool(tool: AgentTool) -> None:
    """Register a tool in the global registry."""
    _registry.register(tool)


# Authority check helpers

def _require_identity(state: AgentState) -> bool:
    """Tool requires valid identity."""
    return state.identity.creator_id > 0 and state.identity.user_id > 0


def _require_sales_enabled(state: AgentState) -> bool:
    """Tool requires creator sales to be enabled."""
    return state.identity.sales_enabled


def _require_autonomy_enabled(state: AgentState) -> bool:
    """Tool requires autonomy to be enabled."""
    return state.identity.autonomy_enabled


# Tool definitions using existing CRM capabilities

async def _handle_get_relationship_state(
    args: dict[str, Any], state: AgentState
) -> dict[str, Any]:
    """Get current relationship state."""
    return {
        "relationship_state": state.relationship.relationship_state,
        "commercial_pressure": state.relationship.commercial_pressure,
        "tip_eligibility": state.relationship.tip_eligibility,
        "aftercare_status": state.relationship.aftercare_status,
        "rejection_count": state.relationship.rejection_count,
        "consecutive_rejections": state.relationship.consecutive_rejections,
    }


async def _handle_get_user_profile(
    args: dict[str, Any], state: AgentState
) -> dict[str, Any]:
    """Get user profile."""
    if state.memory.profile is None:
        return {"profile": None, "message": "No profile available"}
    return {"profile": state.memory.profile}


async def _handle_get_conversation_summary(
    args: dict[str, Any], state: AgentState
) -> dict[str, Any]:
    """Get conversation summary."""
    if state.memory.summary is None:
        return {"summary": None, "message": "No summary available"}
    return {"summary": state.memory.summary}


async def _handle_search_conversation_history(
    args: dict[str, Any], state: AgentState
) -> dict[str, Any]:
    """Search conversation history."""
    query = args.get("query", "")
    if not query:
        return {"error": "Query required"}
    
    # Use existing relevant memories
    results = [
        mem for mem in state.memory.relevant_memories
        if query.lower() in mem.lower()
    ]
    
    return {
        "query": query,
        "results": results[:5],  # Bounded
        "total": len(results),
    }


async def _handle_get_commerce_context(
    args: dict[str, Any], state: AgentState
) -> dict[str, Any]:
    """Get commerce context without making decisions."""
    return {
        "eligible_products": list(state.commerce.eligible_products),
        "active_offers": list(state.commerce.active_offers),
        "purchase_history": list(state.commerce.purchase_history),
        "has_relevant_product": state.commerce.has_relevant_product,
        "relationship_state": state.relationship.relationship_state,
        "commercial_pressure": state.relationship.commercial_pressure,
        "tip_eligibility": state.relationship.tip_eligibility,
    }


async def _handle_check_operator_handoff(
    args: dict[str, Any], state: AgentState
) -> dict[str, Any]:
    """Check if operator handoff is needed."""
    # Import here to avoid circular imports
    from commerce.relationship import check_operator_handoff
    
    should_handoff, reason = check_operator_handoff(
        relationship_state=state.relationship.relationship_state,
        consecutive_rejections=state.relationship.consecutive_rejections,
        tip_suggestions_ignored=state.relationship.tip_suggestions_ignored,
        hours_since_last_tip=state.relationship.hours_since_last_tip,
        tip_eligibility=state.relationship.tip_eligibility,
        commercial_pressure=state.relationship.commercial_pressure,
        rejection_count=state.relationship.rejection_count,
    )
    
    return {
        "should_handoff": should_handoff,
        "reason": reason,
    }


async def _handle_get_conversation_history(
    args: dict[str, Any], state: AgentState
) -> dict[str, Any]:
    """Get recent conversation history."""
    limit = min(args.get("limit", 10), 20)  # Bounded
    history = state.conversation.history[-limit:]
    return {
        "messages": list(history),
        "count": len(history),
    }


async def _handle_analyze_conversation_signals(
    args: dict[str, Any], state: AgentState
) -> dict[str, Any]:
    """Analyze conversation for commercial signals (read-only, no decisions)."""
    # This is a read-only analysis tool
    # The actual signal extraction happens in commerce pipeline
    # This tool just provides current signal context
    
    return {
        "relationship_state": state.relationship.relationship_state,
        "commercial_pressure": state.relationship.commercial_pressure,
        "tip_eligibility": state.relationship.tip_eligibility,
        "has_relevant_product": state.commerce.has_relevant_product,
        "active_offers_count": len(state.commerce.active_offers),
        "recent_purchases_count": len(state.commerce.purchase_history),
    }


# Register all tools

def _register_default_tools() -> None:
    """Register the default set of agent tools."""
    
    # Relationship tools (read-only)
    register_tool(AgentTool(
        name="get_relationship_state",
        description="Get current relationship state, commercial pressure, and tip eligibility.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_handle_get_relationship_state,
        authority_check=_require_identity,
    ))
    
    register_tool(AgentTool(
        name="get_user_profile",
        description="Get user profile with extracted facts and preferences.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_handle_get_user_profile,
        authority_check=_require_identity,
    ))
    
    register_tool(AgentTool(
        name="get_conversation_summary",
        description="Get conversation summary for context.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_handle_get_conversation_summary,
        authority_check=_require_identity,
    ))
    
    register_tool(AgentTool(
        name="search_conversation_history",
        description="Search conversation history by keyword.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
            },
            "required": ["query"],
        },
        handler=_handle_search_conversation_history,
        authority_check=_require_identity,
    ))
    
    register_tool(AgentTool(
        name="get_conversation_history",
        description="Get recent conversation messages.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of messages to retrieve (max 20)",
                    "default": 10,
                },
            },
            "required": [],
        },
        handler=_handle_get_conversation_history,
        authority_check=_require_identity,
    ))
    
    register_tool(AgentTool(
        name="get_commerce_context",
        description="Get commerce context: eligible products, active offers, purchase history.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_handle_get_commerce_context,
        authority_check=_require_identity,
    ))
    
    register_tool(AgentTool(
        name="check_operator_handoff",
        description="Check if operator handoff is needed based on relationship state.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_handle_check_operator_handoff,
        authority_check=_require_identity,
    ))
    
    register_tool(AgentTool(
        name="analyze_conversation_signals",
        description="Analyze conversation for commercial signals (read-only).",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_handle_analyze_conversation_signals,
        authority_check=_require_identity,
    ))


# Initialize default tools on module load
_register_default_tools()
