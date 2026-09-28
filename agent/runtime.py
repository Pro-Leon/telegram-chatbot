"""Agent runtime adapter.

Thin integration layer between existing CRM context and agent loop.
Does NOT contain business rules.
Does NOT duplicate authority.
Does NOT bypass existing infrastructure.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from agent.state import AgentState
from agent.loop import AgentLoop, AgentTurn
from agent.tools import get_tool_registry

logger = logging.getLogger("agent.runtime")


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime configuration."""
    max_tool_calls: int = 5
    max_response_tokens: int = 120
    max_response_time_seconds: float = 30.0
    enabled: bool = False


@dataclass(frozen=True)
class RuntimeResult:
    """Result from agent runtime."""
    response_text: str
    tool_calls_made: int
    tool_names: list[str]
    execution_time_seconds: float
    terminated_reason: str
    runtime_mode: str
    success: bool
    error: str | None = None


# Default configuration
_DEFAULT_CONFIG = RuntimeConfig(
    max_tool_calls=5,
    max_response_tokens=120,
    max_response_time_seconds=30.0,
    enabled=False,
)


def get_runtime_config() -> RuntimeConfig:
    """Get runtime configuration from settings."""
    try:
        from core.config import Settings
        settings = Settings()
        
        return RuntimeConfig(
            max_tool_calls=settings.agent_max_tool_calls,
            max_response_tokens=settings.agent_max_response_tokens,
            max_response_time_seconds=settings.agent_max_runtime_seconds,
            enabled=settings.ai_runtime_mode == "agent",
        )
    except Exception as e:
        logger.warning(f"Failed to load runtime config: {e}")
        return _DEFAULT_CONFIG


async def build_agent_state(
    *,
    user_id: int,
    creator_id: int | None,
    user_message: str,
    conversation_history: list[dict[str, str]],
    context: dict[str, Any],
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
    profile: dict[str, Any] | None = None,
    summary: str | None = None,
) -> AgentState:
    """Build AgentState from existing CRM context.
    
    This is a THIN adapter. It translates existing authoritative context
    into the agent state format. It does NOT re-derive any state.
    """
    config = get_runtime_config()
    
    # Get available tools
    registry = get_tool_registry()
    available_tools = tuple(t.name for t in registry.list_tools())
    
    return AgentState.create(
        creator_id=creator_id or 0,
        user_id=user_id,
        current_message=user_message,
        history=tuple(dict(m) for m in conversation_history),
        profile=profile,
        summary=summary,
        relevant_memories=(),  # Will be populated by memory module
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
        eligible_products=eligible_products,
        active_offers=active_offers,
        purchase_history=purchase_history,
        has_relevant_product=has_relevant_product,
        creator_capabilities=creator_capabilities,
        sales_enabled=sales_enabled,
        autonomy_enabled=autonomy_enabled,
        persona=persona,
        available_tools=available_tools,
        max_tool_calls=config.max_tool_calls,
        max_response_tokens=config.max_response_tokens,
        max_response_time_seconds=config.max_response_time_seconds,
    )


async def run_agent_runtime(
    state: AgentState,
    provider: Any,
) -> RuntimeResult:
    """Run agent runtime with bounded execution.
    
    This is the main entry point for agent mode.
    It invokes the bounded agent loop and returns a structured result.
    """
    start_time = time.time()
    
    try:
        # Create and run agent loop
        loop = AgentLoop(state=state, provider=provider)
        turn = await loop.run()
        
        # Check if agent terminated with error
        success = turn.terminated_reason != "error"
        
        return RuntimeResult(
            response_text=turn.response_text,
            tool_calls_made=turn.tool_calls_made,
            tool_names=turn.tool_names,
            execution_time_seconds=turn.execution_time_seconds,
            terminated_reason=turn.terminated_reason,
            runtime_mode="agent",
            success=success,
            error="Agent loop error" if not success else None,
        )
        
    except Exception as e:
        logger.error(f"Agent runtime error: {e}")
        return RuntimeResult(
            response_text="I apologize, but I encountered an error processing your message.",
            tool_calls_made=0,
            tool_names=[],
            execution_time_seconds=time.time() - start_time,
            terminated_reason="error",
            runtime_mode="agent",
            success=False,
            error=str(e),
        )


async def run_legacy_runtime(
    context: list[dict[str, str]],
    user_message: str,
    provider: Any,
    max_tokens: int = 500,
) -> RuntimeResult:
    """Run legacy runtime (existing behavior).
    
    This wraps the existing generate_draft() function.
    Used for legacy mode and shadow mode fallback.
    """
    start_time = time.time()
    
    try:
        # Use existing provider
        response_text = await provider.generate_with_history(
            messages=context,
            max_tokens=max_tokens,
        )
        
        return RuntimeResult(
            response_text=response_text or "",
            tool_calls_made=0,
            tool_names=[],
            execution_time_seconds=time.time() - start_time,
            terminated_reason="completed",
            runtime_mode="legacy",
            success=True,
        )
        
    except Exception as e:
        logger.error(f"Legacy runtime error: {e}")
        return RuntimeResult(
            response_text="",
            tool_calls_made=0,
            tool_names=[],
            execution_time_seconds=time.time() - start_time,
            terminated_reason="error",
            runtime_mode="legacy",
            success=False,
            error=str(e),
        )
