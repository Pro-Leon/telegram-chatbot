"""Agent loop module.

Bounded agent runtime for AI-native CRM.
Implements observe → reason → retrieve → act → respond cycle.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from agent.state import AgentState
from agent.tools import AgentToolRegistry, get_tool_registry
from agent.memory import AgentMemory

logger = logging.getLogger("agent.loop")


@dataclass(frozen=True)
class AgentTurn:
    """Result of one agent turn."""
    response_text: str
    tool_calls_made: int
    tool_names: list[str]
    execution_time_seconds: float
    terminated_reason: str  # "completed", "max_tool_calls", "timeout", "error"


class AgentLoop:
    """Bounded agent loop for AI-native CRM.
    
    Lifecycle:
    1. Observe: Build compact context from state
    2. Reason: Get LLM response with tool calling
    3. Act: Execute tool calls if any
    4. Observe: Append tool results to context
    5. Repeat until: final response or limit reached
    
    Termination conditions:
    - LLM returns text (no more tool calls)
    - Max tool calls reached
    - Execution timeout
    - Error occurred
    """
    
    def __init__(
        self,
        state: AgentState,
        provider: Any,  # LLMProvider
        registry: AgentToolRegistry | None = None,
    ):
        self.state = state
        self.provider = provider
        self.registry = registry or get_tool_registry()
        self.memory = AgentMemory()
        
        # Limits from state
        self.max_tool_calls = state.max_tool_calls
        self.max_response_tokens = state.max_response_tokens
        self.max_time = state.max_response_time_seconds
        
        # Tracking
        self.tool_calls_made = 0
        self.tool_names_used: list[str] = []
        self.start_time: float = 0.0
    
    async def run(self) -> AgentTurn:
        """Execute bounded agent loop."""
        self.start_time = time.time()
        
        try:
            # 1. Observe: Build context
            context = self._build_context()
            
            # 2. Reason: Get LLM response
            response = await self._get_llm_response(context)
            
            # 3. Act: Execute tool calls
            while (
                response.get("tool_calls") 
                and self.tool_calls_made < self.max_tool_calls
                and not self._is_timeout()
            ):
                # Execute tools
                tool_results = await self._execute_tools(response["tool_calls"])
                
                # Append tool results to context
                for result in tool_results:
                    context.append({
                        "role": "tool",
                        "content": json.dumps(result["result"]),
                    })
                
                # Continue loop
                response = await self._get_llm_response(context)
            
            # Determine termination reason
            if self.tool_calls_made >= self.max_tool_calls:
                reason = "max_tool_calls"
            elif self._is_timeout():
                reason = "timeout"
            else:
                reason = "completed"
            
            return AgentTurn(
                response_text=response.get("text", ""),
                tool_calls_made=self.tool_calls_made,
                tool_names=self.tool_names_used,
                execution_time_seconds=time.time() - self.start_time,
                terminated_reason=reason,
            )
            
        except Exception as e:
            logger.error(f"Agent loop error: {e}")
            return AgentTurn(
                response_text="I apologize, but I encountered an error processing your message.",
                tool_calls_made=self.tool_calls_made,
                tool_names=self.tool_names_used,
                execution_time_seconds=time.time() - self.start_time,
                terminated_reason="error",
            )
    
    def _build_context(self) -> list[dict[str, str]]:
        """Build compact context for LLM."""
        context = []
        
        # System prompt
        system_prompt = self._build_system_prompt()
        context.append({"role": "system", "content": system_prompt})
        
        # Conversation history
        for msg in self.state.conversation.history:
            context.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        # Current message — M3 exactly-once: history already contains the
        # persisted current inbound in the canonical flow, so append only
        # when it is genuinely absent (same rule as generate_draft).
        from memory.context import current_message_in_history

        if not current_message_in_history(
            self.state.conversation.history,
            self.state.conversation.current_message,
        ):
            context.append({
                "role": "user",
                "content": self.state.conversation.current_message,
            })

        return context
    
    def _build_system_prompt(self) -> str:
        """Build compact system prompt."""
        parts = [
            "You are a helpful, natural conversation partner.",
            "Focus on building genuine relationships.",
            "Be conversational, not salesy.",
            "Remember context from the conversation.",
            "Use tools when you need more information.",
            "Keep responses concise and natural.",
        ]
        
        # Inject memory context
        system_prompt = "\n".join(parts)
        system_prompt = self.memory.inject_memory_into_prompt(
            system_prompt, self.state
        )
        
        # Inject relationship context
        rel = self.state.relationship
        parts.append(f"\nRelationship: {rel.relationship_state}")
        parts.append(f"Commercial pressure: {rel.commercial_pressure}")
        
        if rel.tip_eligibility != "NOT_ELIGIBLE":
            parts.append(f"Tip eligible: {rel.tip_eligibility}")
        
        if rel.aftercare_status:
            parts.append(f"Aftercare: {rel.aftercare_status}")
        
        return "\n".join(parts)
    
    async def _get_llm_response(
        self, context: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Get LLM response with optional tool calling."""
        try:
            # Check if provider supports tool calling
            if hasattr(self.provider, 'supports_tool_calling') and self.provider.supports_tool_calling():
                # Use tool calling
                tool_schemas = self.registry.get_schemas()
                
                response = await self.provider.generate_with_tools(
                    messages=context,
                    tools=tool_schemas,
                    max_tokens=self.max_response_tokens,
                )
                
                return {
                    "text": response.get("text", ""),
                    "tool_calls": response.get("tool_calls", []),
                }
            else:
                # Plain generation (no tool calling)
                response = await self.provider.generate(
                    messages=context,
                    max_tokens=self.max_response_tokens,
                )
                
                return {
                    "text": response.get("text", ""),
                    "tool_calls": [],
                }
                
        except Exception as e:
            logger.error(f"LLM response error: {e}")
            return {
                "text": "I apologize, but I encountered an error.",
                "tool_calls": [],
            }
    
    async def _execute_tools(
        self, tool_calls: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Execute tool calls with authority validation."""
        results = []
        
        for tool_call in tool_calls:
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("arguments", {})
            
            # Get tool from registry
            tool = self.registry.get(tool_name)
            if not tool:
                results.append({
                    "tool": tool_name,
                    "result": {"error": f"Tool '{tool_name}' not found"},
                })
                continue
            
            # Validate authority
            if not tool.validate_authority(self.state):
                results.append({
                    "tool": tool_name,
                    "result": {"error": "Unauthorized"},
                })
                logger.warning(f"Tool authority denied: {tool_name}")
                continue
            
            # Execute with timeout
            try:
                import asyncio
                result = await asyncio.wait_for(
                    tool.handler(tool_args, self.state),
                    timeout=tool.timeout,
                )
                
                results.append({
                    "tool": tool_name,
                    "result": result,
                })
                
                self.tool_calls_made += 1
                self.tool_names_used.append(tool_name)
                
            except asyncio.TimeoutError:
                results.append({
                    "tool": tool_name,
                    "result": {"error": "Tool timeout"},
                })
                logger.warning(f"Tool timeout: {tool_name}")
                
            except Exception as e:
                results.append({
                    "tool": tool_name,
                    "result": {"error": str(e)},
                })
                logger.error(f"Tool error: {tool_name}: {e}")
        
        return results
    
    def _is_timeout(self) -> bool:
        """Check if execution has timed out."""
        return (time.time() - self.start_time) > self.max_time


async def run_agent_turn(
    state: AgentState,
    provider: Any,
) -> AgentTurn:
    """Convenience function to run an agent turn."""
    loop = AgentLoop(state=state, provider=provider)
    return await loop.run()
