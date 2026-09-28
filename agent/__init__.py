"""Agent runtime package.

AI-native CRM agent runtime.
Provides bounded agent loop with governed tools.
"""

from __future__ import annotations

from agent.state import AgentState, AgentIdentity
from agent.loop import AgentLoop, AgentTurn, run_agent_turn
from agent.tools import (
    AgentTool,
    AgentToolRegistry,
    get_tool_registry,
    register_tool,
)
from agent.memory import AgentMemory
from agent.runtime import (
    RuntimeConfig,
    RuntimeResult,
    get_runtime_config,
    build_agent_state,
    run_agent_runtime,
    run_legacy_runtime,
)
from agent.canary import CanaryConfig, should_use_agent, get_canary_info

__all__ = [
    # State
    "AgentState",
    "AgentIdentity",
    # Loop
    "AgentLoop",
    "AgentTurn",
    "run_agent_turn",
    # Tools
    "AgentTool",
    "AgentToolRegistry",
    "get_tool_registry",
    "register_tool",
    # Memory
    "AgentMemory",
    # Runtime
    "RuntimeConfig",
    "RuntimeResult",
    "get_runtime_config",
    "build_agent_state",
    "run_agent_runtime",
    "run_legacy_runtime",
    # Canary
    "CanaryConfig",
    "should_use_agent",
    "get_canary_info",
]
