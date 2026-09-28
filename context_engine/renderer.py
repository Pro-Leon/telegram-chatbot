"""Compact Context Renderer (Phase 70).

Deterministic renderer that converts ContextSnapshot into compact text
suitable for a future Qwen prompt.

Requirements from Phase 69:
- preserve source/category boundaries
- distinguish authoritative state from contextual hints
- avoid implying that low-authority text is authoritative
- respect the hard character/token budget
- be deterministic
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from context_engine.budget import CHARS_PER_TOKEN
from context_engine.models import (
    CATEGORY_BUDGETS,
    TOTAL_CONTEXT_BUDGET,
    AuthorityLevel,
    ContextCategory,
    ContextItem,
    ContextSnapshot,
    ContentTrust,
)

logger = logging.getLogger("context_engine.renderer")


# Category display order (matches Phase 69 specification)
CATEGORY_ORDER = [
    ContextCategory.SYSTEM,
    ContextCategory.STATE,
    ContextCategory.COMMERCE,
    ContextCategory.MEMORY,
    ContextCategory.KNOWLEDGE,
    ContextCategory.TEMPORAL,
    ContextCategory.CONTENT,
    ContextCategory.CONVERSATION,
    ContextCategory.EMBEDDED,
]

# Category labels for output
CATEGORY_LABELS = {
    ContextCategory.SYSTEM: "SYSTEM",
    ContextCategory.STATE: "STATE",
    ContextCategory.COMMERCE: "COMMERCE",
    ContextCategory.MEMORY: "MEMORY",
    ContextCategory.KNOWLEDGE: "KNOWLEDGE",
    ContextCategory.TEMPORAL: "TEMPORAL",
    ContextCategory.CONTENT: "CONTENT",
    ContextCategory.CONVERSATION: "CONVERSATION",
    ContextCategory.EMBEDDED: "RELEVANT HISTORY",
}

# Authority markers for output
AUTHORITY_MARKERS = {
    AuthorityLevel.HARD_POLICY: "[AUTHORITATIVE]",
    AuthorityLevel.DETERMINISTIC_RULE: "[DETERMINISTIC]",
    AuthorityLevel.DETERMINISTIC_DERIVATION: "[DERIVED]",
    AuthorityLevel.CONTEXT_ASSEMBLY: "[CONTEXT]",
    AuthorityLevel.LLM_GENERATION: "[ADVISORY]",
    AuthorityLevel.POST_GENERATION: "[OUTPUT]",
}


@dataclass(frozen=True)
class RenderedContext:
    """Rendered context ready for LLM prompt."""

    system_prompt: str  # Compressed persona + rules
    state_block: str  # Deterministic facts
    commerce_block: str  # Commerce context
    memory_block: str  # Long-term memory + fan knowledge
    temporal_block: str  # Temporal context
    content_block: str  # Vault content titles
    conversation_turns: list[dict[str, str]]  # Recent messages
    token_count: int  # Total tokens used
    degradation_level: int  # 0=none, 4=critical
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class CompactRenderer:
    """Deterministic compact context renderer.

    Converts ContextSnapshot into compact text for Qwen prompts.
    """

    max_chars_per_category: dict[ContextCategory, int] = field(
        default_factory=lambda: {
            cat: budget * CHARS_PER_TOKEN
            for cat, budget in CATEGORY_BUDGETS.items()
        }
    )

    def render(self, snapshot: ContextSnapshot) -> RenderedContext:
        """Render ContextSnapshot into compact text.

        Args:
            snapshot: Assembled context snapshot

        Returns:
            RenderedContext with formatted text blocks
        """
        # Group items by category
        category_items: dict[ContextCategory, list[ContextItem]] = {}
        for item in snapshot.items:
            category_items.setdefault(item.category, []).append(item)

        # Render each category
        system_prompt = self._render_category(
            category_items.get(ContextCategory.SYSTEM, []),
            ContextCategory.SYSTEM,
        )
        state_block = self._render_category(
            category_items.get(ContextCategory.STATE, []),
            ContextCategory.STATE,
        )
        commerce_block = self._render_category(
            category_items.get(ContextCategory.COMMERCE, []),
            ContextCategory.COMMERCE,
        )
        memory_block = self._render_category(
            category_items.get(ContextCategory.MEMORY, [])
            + category_items.get(ContextCategory.KNOWLEDGE, []),
            ContextCategory.MEMORY,
        )
        temporal_block = self._render_category(
            category_items.get(ContextCategory.TEMPORAL, []),
            ContextCategory.TEMPORAL,
        )
        content_block = self._render_category(
            category_items.get(ContextCategory.CONTENT, []),
            ContextCategory.CONTENT,
        )

        # Render conversation turns
        conversation_items = category_items.get(ContextCategory.CONVERSATION, [])
        # Sort by priority (which represents position in conversation)
        conversation_items.sort(key=lambda x: x.priority)
        conversation_turns = self._render_conversation(conversation_items)

        # Render embedded history
        embedded_items = category_items.get(ContextCategory.EMBEDDED, [])
        embedded_text = self._render_category(
            embedded_items, ContextCategory.EMBEDDED
        )

        # Calculate total tokens
        token_count = sum(item.token_cost for item in snapshot.items)

        return RenderedContext(
            system_prompt=system_prompt,
            state_block=state_block,
            commerce_block=commerce_block,
            memory_block=memory_block,
            temporal_block=temporal_block,
            content_block=content_block,
            conversation_turns=conversation_turns,
            token_count=token_count,
            degradation_level=snapshot.degradation_level,
            metadata={
                "total_items": str(len(snapshot.items)),
                "candidate_count": str(snapshot.candidate_count),
                "deduplication_count": str(snapshot.deduplication_count),
            },
        )

    def _render_category(
        self,
        items: list[ContextItem],
        category: ContextCategory,
        include_authority_markers: bool = True,
    ) -> str:
        """Render items from a category into compact text.

        Phase 2: authority markers are preserved for all items so the final
        Qwen input explicitly distinguishes [AUTHORITATIVE] vs [DERIVED] vs
        [RETRIEVED] vs [ADVISORY]. Previously markers were omitted for
        authoritative items, making the boundary implicit.
        """
        if not items:
            return ""

        label = CATEGORY_LABELS.get(category, category.value.upper())
        max_chars = self.max_chars_per_category.get(category, 200)

        lines: list[str] = []
        chars_used = 0

        for item in items:
            marker = AUTHORITY_MARKERS.get(item.authority, "")
            if include_authority_markers and marker:
                prefix = f"{marker} "
            else:
                # Legacy narrow path: only non-authoritative
                prefix = f"{marker} " if marker and not item.is_authoritative else ""

            line = f"{prefix}{item.content}"

            # Check character budget
            if chars_used + len(line) > max_chars:
                # Truncate to fit
                remaining = max_chars - chars_used
                if remaining > 20:
                    line = line[:remaining] + "..."
                    lines.append(line)
                break

            lines.append(line)
            chars_used += len(line) + 1  # +1 for newline

        if not lines:
            return ""

        return "\n".join(lines)

    def _render_conversation(
        self,
        items: list[ContextItem],
    ) -> list[dict[str, str]]:
        """Render conversation items into message format."""
        turns: list[dict[str, str]] = []

        for item in items:
            # Determine role from metadata or content
            role = item.metadata.get("role", "user")
            turns.append({"role": role, "content": item.content})

        return turns

    def render_to_messages(
        self,
        snapshot: ContextSnapshot,
    ) -> list[dict[str, str]]:
        """Render snapshot into Qwen-compatible message list.

        Returns list of messages in the format:
        [{"role": "system", "content": "..."}, ...]
        """
        rendered = self.render(snapshot)
        messages: list[dict[str, str]] = []

        # System prompt
        if rendered.system_prompt:
            messages.append({
                "role": "system",
                "content": rendered.system_prompt,
            })

        # State block
        if rendered.state_block:
            messages.append({
                "role": "system",
                "content": f"STATE:\n{rendered.state_block}",
            })

        # Commerce block
        if rendered.commerce_block:
            messages.append({
                "role": "system",
                "content": f"COMMERCE:\n{rendered.commerce_block}",
            })

        # Memory block
        if rendered.memory_block:
            messages.append({
                "role": "system",
                "content": f"MEMORY:\n{rendered.memory_block}",
            })

        # Temporal block
        if rendered.temporal_block:
            messages.append({
                "role": "system",
                "content": f"TEMPORAL:\n{rendered.temporal_block}",
            })

        # Content block
        if rendered.content_block:
            messages.append({
                "role": "system",
                "content": f"CONTENT:\n{rendered.content_block}",
            })

        # Conversation turns
        for turn in rendered.conversation_turns:
            messages.append({
                "role": turn["role"],
                "content": turn["content"],
            })

        return messages
