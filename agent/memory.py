"""Agent memory module.

Integration with existing memory capabilities.
Provides compact context for the agent loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agent.state import AgentState, MemoryContext

logger = logging.getLogger("agent.memory")


class AgentMemory:
    """Memory integration for agent runtime.
    
    Uses existing memory infrastructure:
    - memory/context.py for context building
    - memory/retrieval.py for vector search
    - memory/profile.py for profile extraction
    - memory/summarizer.py for summarization
    
    Does NOT duplicate memory stores.
    """
    
    def __init__(self):
        self._initialized = False
    
    async def build_memory_context(
        self,
        user_id: int,
        creator_id: int,
        current_message: str,
        conversation_history: list[dict[str, str]],
    ) -> MemoryContext:
        """Build compact memory context for agent turn.
        
        Uses existing memory infrastructure, not duplicate stores.
        """
        profile = None
        summary = None
        relevant_memories = ()
        
        try:
            # Import existing memory capabilities
            from db.postgres import get_user_profile, get_latest_summary
            from memory.retrieval import retrieve_relevant_history
            
            # Get profile (existing)
            profile_row = await get_user_profile(user_id)
            if profile_row:
                profile = dict(profile_row)
            
            # Get summary (existing)
            summary_row = await get_latest_summary(user_id, creator_id)
            if summary_row:
                summary = summary_row.get("summary", None)
            
            # Get relevant memories (existing)
            # Only trigger on keywords that indicate memory recall needed
            memory_keywords = ["remember", "told you", "mentioned", "last time", "before"]
            if any(kw in current_message.lower() for kw in memory_keywords):
                memories = await retrieve_relevant_history(
                    user_id=user_id,
                    creator_id=creator_id,
                    query=current_message,
                    limit=5,
                )
                if memories:
                    relevant_memories = tuple(memories)
            
        except Exception as e:
            logger.warning(f"Memory context build failed: {e}")
            # Graceful degradation — memory is advisory, not enforcement
        
        return MemoryContext(
            profile=profile,
            summary=summary,
            relevant_memories=relevant_memories,
            embedding_available=bool(relevant_memories),
        )
    
    def inject_memory_into_prompt(
        self,
        system_prompt: str,
        state: AgentState,
    ) -> str:
        """Inject memory context into system prompt.
        
        Compact injection — only essential facts.
        """
        sections = []
        
        # Profile facts
        if state.memory.profile:
            facts = []
            for key in ["name", "age", "location", "interests"]:
                if key in state.memory.profile:
                    facts.append(f"{key}: {state.memory.profile[key]}")
            if facts:
                sections.append("User facts: " + ", ".join(facts))
        
        # Summary
        if state.memory.summary:
            # Truncate summary to 200 tokens
            summary = state.memory.summary[:500]
            sections.append(f"Conversation summary: {summary}")
        
        # Relevant memories
        if state.memory.relevant_memories:
            memories = list(state.memory.relevant_memories)[:3]
            sections.append("Relevant memories: " + "; ".join(memories))
        
        if sections:
            memory_context = "\n".join(sections)
            return f"{system_prompt}\n\n--- Memory Context ---\n{memory_context}"
        
        return system_prompt
