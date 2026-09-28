# AI Native LLM Phase 69: Context Engine Architecture Specification

**Status:** SPECIFICATION — No production changes
**Date:** 2026-09-01
**Previous Phase:** Phase 68A (Reporting Correction Forensic Audit)
**Next Phase:** Phase 70 (Implementation Boundary)
**Scope:** Context Engine architecture specification for Qwen2.5 evolution path

---

## 1. Executive Summary

This specification defines the Context Engine architecture that enables the system to evolve from the current multi-LLM pipeline (Gemini flash → DeepSeek V4 Flash → Qwen3:4b) toward a one-generation Qwen2.5 architecture with deterministic authority boundaries.

### 1.1 Problem Statement

The current system uses three separate LLM calls per inbound message:
1. **LLM #1** (`extract_commerce_signals`): Gemini or DeepSeek — advisory signals only
2. **LLM #2** (`generate_draft`): Qwen3:4b — response generation
3. **LLM #3** (`score_draft`): Gemini — quality scoring

This architecture has:
- **3x latency** (three serial LLM calls)
- **3x token cost** (three separate context windows)
- **Inconsistent context** (each LLM sees different information)
- **No unified context** (signals, conversation, commerce are separate)

### 1.2 Solution: Context Engine

A Context Engine is a deterministic, single-pass context assembly layer that:
- Gathers all relevant data (persona, fan state, conversation history, commerce state)
- Structures it into a single, token-budgeted context window
- Feeds it to Qwen2.5 (or Qwen3) in one generation call
- Produces both the response AND structured signals in one pass

### 1.3 Key Invariants

1. **Deterministic authority is never overridden**: The Context Engine assembles context; it does not make decisions.
2. **LLM output is advisory**: The single Qwen generation produces text + optional signals; deterministic authority remains in `commerce.decision`.
3. **Production safety**: The 3-LLM pipeline continues to run. The Context Engine is additive.
4. **Token budget discipline**: Every context item has a budget; overflow triggers graceful degradation.

---

## 2. Forensic Reconnaissance Results

### 2.1 Current Data-Access Inventory

The forensic reconnaissance identified **10 data-access categories** with **47 distinct data sources**:

| Category | Data Sources | Authority Level |
|---|---|---|
| Persona State | 12 sources | Authoritative (DB) |
| Fan State | 8 sources | Mixed (DB + LLM) |
| Conversation History | 6 sources | Authoritative (DB) |
| Commerce State | 14 sources | Authoritative (DB + Pure) |
| Subscription/Payment/Access | 4 sources | Authoritative (DB) |
| Operator State | 6 sources | Authoritative (DB) |
| Embeddings/Vector Search | 3 sources | Advisory (API) |
| Qwen3 Integration | 5 sources | Mixed |
| Structured Output Schemas | 12 schemas | Mixed |
| Deterministic Authority Boundaries | 11 boundaries | Authoritative |

### 2.2 Current Context Assembly Path

The current context assembly happens in `memory.context.build_qwen3_context()` (lines 504-793):

```
1. get_user(user_id)                          → fan identity
2. get_user_profile(user_id)                  → profile facts
3. get_recent_messages(user_id, limit=20)     → conversation history
4. get_latest_summary_with_age(user_id)       → compressed history
5. get_structured_persona_async(creator_id)   → persona snapshot
6. build_qwen3_system_prompt(...)             → system prompt
7. render_compact_persona_block(structured)   → persona block
8. build_llm_context(creator_id, user_id)     → commerce context
9. build_qwen3_state_context(...)             → state context
10. rank_products_by_relevance(...)           → vault content
11. retrieve_relevant_memories(...)           → long-term memory
12. retrieve_relevant_knowledge(...)          → fan knowledge
13. temporal_context_for_fan(...)             → temporal context
14. Recent messages loop                      → conversation turns
```

**Total data sources per turn:** 14 independent reads + 1 conversation loop
**Total token budget:** ~1600 tokens (system:400, state:200, conversation:800, summary:200)

### 2.3 Current Authority Boundaries

| Boundary | Owner | Rule |
|---|---|---|
| `PolicyDecision.allowed` | `commerce.eligibility` | Hard eligibility gate |
| `CommerceDecision.action` | `commerce.decision` | PURE engine, first match wins |
| `creator_sales_enabled` | `db.dropfans` | Active integration required |
| Product identity/price/URL | `db.fangate` | Never from conversation |
| Purchase attribution | `commerce.dao` | Atomic DB transaction |
| Cooldowns | `commerce.dao` | DB timestamps |
| Offer state transitions | `commerce.dao` | Conditional UPDATEs |
| Relationship state | `commerce.relationship` | Derived from funnel_stage + purchase |
| Operator handoff | `commerce.relationship` | Deterministic rules |
| `CommerceSignals` | LLM output | Advisory only |
| Profile facts | LLM → DB | Merged, highest confidence wins |
| Conversation summary | LLM → DB | Advisory, saved |

---

## 3. Context Engine Architecture

### 3.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Context Engine                            │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Data Gatherer │  │ Token Budget │  │ Context      │      │
│  │              │  │ Manager      │  │ Assembler    │      │
│  │ - Persona    │  │              │  │              │      │
│  │ - Fan State  │  │ - Per-item   │  │ - System     │      │
│  │ - Conversation│  │   budgets   │  │ - State      │      │
│  │ - Commerce   │  │ - Overflow   │  │ - History    │      │
│  │ - Memory     │  │   handling   │  │ - Signals    │      │
│  │ - Temporal   │  │ - Degradation│  │              │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                 │                  │               │
│         └─────────────────┼──────────────────┘               │
│                           │                                  │
│                    ┌──────▼───────┐                          │
│                    │ Single-Gen   │                          │
│                    │ Qwen2.5/3    │                          │
│                    │ Call         │                          │
│                    └──────┬───────┘                          │
│                           │                                  │
│                    ┌──────▼───────┐                          │
│                    │ Output       │                          │
│                    │ Parser       │                          │
│                    │ (text +      │                          │
│                    │  signals)    │                          │
│                    └──────────────┘                          │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Component Definitions

#### 3.2.1 Data Gatherer

**Purpose:** Collect all relevant data from existing data sources in parallel.

**Inputs:**
- `user_id: int`
- `creator_id: int | None`
- `current_message: str`
- `persona: str`
- `structured_persona_snapshot: dict | None`

**Outputs:**
- `ContextData` dataclass with all gathered data

**Data Sources (in priority order):**

| Priority | Source | Function | Token Budget |
|---|---|---|---|
| 1 | Persona instructions | `get_user_persona()` | 400 |
| 2 | Fan identity | `get_user()` | 50 |
| 3 | Fan profile | `get_user_profile()` | 200 |
| 4 | Conversation summary | `get_latest_summary_with_age()` | 200 |
| 5 | Commerce state | `build_llm_context()` | 200 |
| 6 | Conversation state | `derive_conversation_state()` | 100 |
| 7 | Long-term memory | `retrieve_relevant_memories()` | 150 |
| 8 | Fan knowledge | `retrieve_relevant_knowledge()` | 150 |
| 9 | Temporal context | `temporal_context_for_fan()` | 50 |
| 10 | Vault content | `rank_products_by_relevance()` | 100 |
| 11 | Recent messages | `get_recent_messages()` | 800 |
| 12 | Embedded history | `retrieve_relevant_history()` | 200 |

**Total token budget:** 2,600 tokens

**Concurrency:**
- Sources 1-6 are independent → parallel via `asyncio.gather()`
- Sources 7-10 depend on conversation_state → sequential after 1-6
- Source 11 is independent → parallel with 7-10
- Source 12 depends on current_message → sequential after 11

#### 3.2.2 Token Budget Manager

**Purpose:** Enforce per-item token budgets and handle overflow gracefully.

**Token Counting:**
- Use `tiktoken` with `gpt-4` encoding (current implementation)
- Future: Qwen-specific tokenizer if available

**Overflow Strategy:**
1. **Hard overflow:** Item exceeds budget → truncate with `[TRUNCATED]` marker
2. **Soft overflow:** Total exceeds budget → degrade lowest-priority items
3. **Graceful degradation:** If all items overflow → keep system prompt + recent messages only

**Degradation Order (lowest to highest priority):**
1. Vault content titles
2. Temporal context
3. Fan knowledge (non-critical)
4. Long-term memory (non-critical)
5. Commerce state (non-critical facts)
6. Conversation summary
7. Recent messages (never degrade below 3 turns)
8. System prompt (never degrade)

#### 3.2.3 Context Assembler

**Purpose:** Structure gathered data into Qwen-compatible context format.

**Output Format:**
```python
@dataclass(frozen=True)
class AssembledContext:
    system_prompt: str          # Compressed persona + rules
    state_block: str            # Deterministic facts
    commerce_block: str         # Commerce context
    memory_block: str           # Long-term memory + fan knowledge
    temporal_block: str         # Temporal context
    content_block: str          # Vault content titles
    conversation_turns: list[dict]  # Recent messages
    token_count: int            # Total tokens used
    degradation_level: int      # 0=none, 1=minor, 2=moderate, 3=severe
```

**Assembly Rules:**
1. System prompt always first
2. State block second (compressed deterministic facts)
3. Commerce block third (when applicable)
4. Memory block fourth (when applicable)
5. Temporal block fifth (when applicable)
6. Content block sixth (when applicable)
7. Conversation turns last (most recent first, within budget)

---

## 4. Context Item Schema

### 4.1 ContextItem Definition

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class ContextItemType(str, Enum):
    """Types of context items for budget allocation."""
    SYSTEM_PROMPT = "system_prompt"
    STATE_FACTS = "state_facts"
    COMMERCE_CONTEXT = "commerce_context"
    LONG_TERM_MEMORY = "long_term_memory"
    FAN_KNOWLEDGE = "fan_knowledge"
    TEMPORAL_CONTEXT = "temporal_context"
    VAULT_CONTENT = "vault_content"
    CONVERSATION_TURN = "conversation_turn"
    EMBEDDED_HISTORY = "embedded_history"

class ContextItemAuthority(str, Enum):
    """Authority level of context item source."""
    AUTHORITATIVE = "authoritative"   # DB-derived, deterministic
    ADVISORY = "advisory"             # LLM-derived, observational
    DERIVED = "derived"               # Computed from authoritative sources

@dataclass(frozen=True)
class ContextItem:
    """A single item in the context window."""
    item_type: ContextItemType
    content: str
    token_count: int
    authority: ContextItemAuthority
    source: str                       # Module/function that produced it
    priority: int                     # Higher = more important
    ttl_seconds: int | None = None    # Cache validity
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 4.2 Predefined Budget Allocations

```python
CONTEXT_ITEM_BUDGETS: dict[ContextItemType, int] = {
    ContextItemType.SYSTEM_PROMPT: 400,
    ContextItemType.STATE_FACTS: 200,
    ContextItemType.COMMERCE_CONTEXT: 200,
    ContextItemType.LONG_TERM_MEMORY: 150,
    ContextItemType.FAN_KNOWLEDGE: 150,
    ContextItemType.TEMPORAL_CONTEXT: 50,
    ContextItemType.VAULT_CONTENT: 100,
    ContextItemType.CONVERSATION_TURN: 200,  # per turn
    ContextItemType.EMBEDDED_HISTORY: 200,
}

TOTAL_CONTEXT_BUDGET = 2600  # tokens
```

### 4.3 ContextData Aggregation

```python
@dataclass
class ContextData:
    """All gathered data for context assembly."""
    # Identity
    user: dict[str, Any]
    profile: dict[str, Any]
    persona: str
    persona_name: str | None
    structured_persona: dict | None

    # Conversation
    conversation_state: ConversationState | None
    recent_messages: list[dict]
    summary: str | None
    summary_age_days: int | None

    # Commerce
    commerce_text: str
    llm_context: LLMContext | None

    # Memory
    long_term_memories: list[dict]
    fan_knowledge: list[dict]

    # Temporal
    temporal_context: dict[str, Any]

    # Content
    vault_content: list[dict]

    # Embedded history
    embedded_history: list[dict]

    # Token tracking
    token_counts: dict[ContextItemType, int] = field(default_factory=dict)
```

---

## 5. Retrieval Architecture

### 5.1 Retrieval Strategy

The Context Engine uses a **multi-source retrieval** strategy:

1. **Direct Retrieval:** Query existing data sources directly (PostgreSQL, Redis)
2. **Relevance-Ranked Retrieval:** Filter by topic/open_threads for memory/knowledge
3. **Vector Retrieval:** Optional embedding-based retrieval for conversation history
4. **Temporal Retrieval:** Time-aware retrieval for fan knowledge

### 5.2 Retrieval Pipeline

```
Current Message
    │
    ├─→ Direct: get_user(), get_user_profile(), get_recent_messages()
    │
    ├─→ Relevance: retrieve_relevant_memories(topic, open_threads)
    │              retrieve_relevant_knowledge(topic, open_threads)
    │
    ├─→ Vector: vector_search_messages(user_id, query_embedding, k=3)
    │
    └─→ Temporal: temporal_context_for_fan(fan_knowledge)
```

### 5.3 Retrieval Scoring

Each retrieved item receives a relevance score:

```python
@dataclass(frozen=True)
class RetrievalScore:
    """Score for a retrieved context item."""
    source_score: float      # 0.0-1.0, how relevant the source is
    topic_overlap: float     # 0.0-1.0, topic match with current message
    recency_score: float     # 0.0-1.0, how recent the item is
    authority_score: float   # 0.0-1.0, authority level of source
    final_score: float       # Weighted combination

def compute_retrieval_score(
    source: str,
    topic_match: bool,
    recency_hours: float,
    authority: ContextItemAuthority,
) -> RetrievalScore:
    """Compute retrieval score for a context item."""
    weights = {
        "source": 0.3,
        "topic": 0.4,
        "recency": 0.2,
        "authority": 0.1,
    }
    source_scores = {
        "postgres": 1.0,
        "redis": 0.8,
        "in_memory": 0.6,
        "llm_derived": 0.4,
    }
    authority_scores = {
        ContextItemAuthority.AUTHORITATIVE: 1.0,
        ContextItemAuthority.DERIVED: 0.8,
        ContextItemAuthority.ADVISORY: 0.6,
    }

    final = (
        weights["source"] * source_scores.get(source, 0.5) +
        weights["topic"] * (1.0 if topic_match else 0.3) +
        weights["recency"] * max(0.0, 1.0 - recency_hours / 168) +  # decay over 1 week
        weights["authority"] * authority_scores.get(authority, 0.5)
    )

    return RetrievalScore(
        source_score=source_scores.get(source, 0.5),
        topic_overlap=1.0 if topic_match else 0.3,
        recency_score=max(0.0, 1.0 - recency_hours / 168),
        authority_score=authority_scores.get(authority, 0.5),
        final_score=final,
    )
```

### 5.4 Retrieval Budget Enforcement

After scoring, items are sorted by `final_score` and selected until the token budget is exhausted:

```python
def select_context_items(
    candidates: list[ContextItem],
    token_budget: int,
) -> list[ContextItem]:
    """Select context items within token budget, prioritized by score."""
    sorted_items = sorted(candidates, key=lambda x: x.priority, reverse=True)
    selected: list[ContextItem] = []
    tokens_used = 0

    for item in sorted_items:
        if tokens_used + item.token_count <= token_budget:
            selected.append(item)
            tokens_used += item.token_count
        else:
            # Try truncated version
            remaining = token_budget - tokens_used
            if remaining > 50:  # Minimum useful size
                truncated = truncate_item(item, remaining)
                if truncated:
                    selected.append(truncated)
                    tokens_used += truncated.token_count
            break

    return selected
```

---

## 6. Context Ranking and Relevance

### 6.1 Ranking Criteria

Context items are ranked by multiple criteria:

1. **Priority:** Hardcoded by item type (system > state > commerce > memory > content)
2. **Relevance:** Topic overlap with current message
3. **Recency:** Time since item was created/updated
4. **Authority:** Deterministic > Derived > LLM-derived
5. **Usage Count:** How often this item has been referenced

### 6.2 Ranking Formula

```python
def rank_context_item(
    item: ContextItem,
    current_message: str,
    conversation_state: ConversationState | None,
) -> float:
    """Rank a context item for inclusion in context window."""
    # Base priority (from item type)
    base_priority = item.priority / 10.0  # Normalize to 0-1

    # Topic relevance
    topic_relevance = compute_topic_relevance(item.content, current_message)

    # Recency (exponential decay)
    recency = compute_recency_score(item.metadata.get("created_at"))

    # Authority bonus
    authority_bonus = {
        ContextItemAuthority.AUTHORITATIVE: 0.2,
        ContextItemAuthority.DERIVED: 0.1,
        ContextItemAuthority.ADVISORY: 0.0,
    }.get(item.authority, 0.0)

    # Conversation state alignment
    state_alignment = 0.0
    if conversation_state:
        if item.item_type == ContextItemType.COMMERCE_CONTEXT:
            state_alignment = 0.15 if conversation_state.tone == "flirty" else 0.05
        elif item.item_type == ContextItemType.LONG_TERM_MEMORY:
            state_alignment = 0.1 if conversation_state.current_topic else 0.0

    return (
        base_priority * 0.4 +
        topic_relevance * 0.3 +
        recency * 0.15 +
        authority_bonus * 0.1 +
        state_alignment * 0.05
    )
```

### 6.3 Deduplication

Duplicate or near-duplicate context items are detected and merged:

```python
def deduplicate_context_items(
    items: list[ContextItem],
) -> list[ContextItem]:
    """Remove duplicate context items, keeping highest priority."""
    seen_hashes: dict[str, ContextItem] = {}
    deduplicated: list[ContextItem] = []

    for item in items:
        # Create content hash (first 100 chars + type)
        hash_key = f"{item.item_type}:{item.content[:100]}"
        if hash_key not in seen_hashes:
            seen_hashes[hash_key] = item
            deduplicated.append(item)
        else:
            # Keep higher priority
            existing = seen_hashes[hash_key]
            if item.priority > existing.priority:
                deduplicated.remove(existing)
                deduplicated.append(item)
                seen_hashes[hash_key] = item

    return deduplicated
```

---

## 7. Context Budget

### 7.1 Token Budget Allocation

```python
# Total budget for Qwen2.5/3 context window
TOTAL_CONTEXT_TOKENS = 2600

# Per-item budgets (must sum to TOTAL_CONTEXT_TOKENS)
TOKEN_BUDGETS = {
    "system_prompt": 400,      # Compressed persona + rules
    "state_facts": 200,        # Deterministic CRM state
    "commerce_context": 200,   # Commerce facts
    "long_term_memory": 150,   # Relevant memories
    "fan_knowledge": 150,      # Relevant knowledge
    "temporal_context": 50,    # Timezone + local time
    "vault_content": 100,      # Product titles
    "conversation": 800,       # Recent messages (10-15 turns)
    "embedded_history": 200,   # Vector-retrieved past messages
}

# Conversation turn budget
MAX_CONVERSATION_TURNS = 15
MAX_ASSISTANT_TURNS = 4
```

### 7.2 Overflow Handling

```python
class ContextOverflowStrategy:
    """Handle context window overflow gracefully."""

    def handle_overflow(
        self,
        items: list[ContextItem],
        total_tokens: int,
        budget: int,
    ) -> list[ContextItem]:
        """Select items within budget, degrading lowest-priority first."""
        if total_tokens <= budget:
            return items

        # Sort by priority (ascending) for degradation
        sorted_items = sorted(items, key=lambda x: x.priority)
        remaining = budget
        selected: list[ContextItem] = []

        for item in reversed(sorted_items):
            if remaining >= item.token_count:
                selected.append(item)
                remaining -= item.token_count
            elif remaining > 50:
                # Truncate
                truncated = self.truncate_item(item, remaining)
                if truncated:
                    selected.append(truncated)
                    remaining -= truncated.token_count

        return selected

    def truncate_item(
        self,
        item: ContextItem,
        max_tokens: int,
    ) -> ContextItem | None:
        """Truncate a context item to fit within token budget."""
        words = item.content.split()
        truncated_words: list[str] = []
        token_count = 0

        for word in words:
            word_tokens = len(word.split()) + 1  # Rough estimate
            if token_count + word_tokens > max_tokens - 10:
                truncated_words.append("[TRUNCATED]")
                break
            truncated_words.append(word)
            token_count += word_tokens

        if not truncated_words:
            return None

        return ContextItem(
            item_type=item.item_type,
            content=" ".join(truncated_words),
            token_count=token_count,
            authority=item.authority,
            source=item.source,
            priority=item.priority,
            ttl_seconds=item.ttl_seconds,
            metadata={**item.metadata, "truncated": True},
        )
```

### 7.3 Degradation Levels

```python
class DegradationLevel(int, Enum):
    """Context degradation levels."""
    NONE = 0          # Full context within budget
    MINOR = 1         # Non-critical items truncated
    MODERATE = 2      # Some items removed (temporal, vault)
    SEVERE = 3        # Only system + state + conversation
    CRITICAL = 4      # Only system + last 3 messages
```

---

## 8. Qwen Context Format

### 8.1 Current Format (Qwen3)

The current Qwen3 context format uses a compressed structure:

```
{system_prompt}

Fan: {first_name}
{profile_facts}
Stage: {stage_guidance}

Rules:
- 2-4 sentences, match their energy
- Reference their history naturally
- Never reveal AI/bot/automated — you are human
- ...

CREATOR PERSONA (compact): {persona_block}

STATE: {first_name} | {funnel_stage}
PROFILE: {interests}
RELATIONSHIP: {relationship_state}
COMMERCE: {commerce_facts}
SUMMARY: {compressed_summary}
IDENTITY: established={bool} lifecycle={stage}

COMMERCIAL STATE: desire={stage} temperature={temp} ...
COMMERCIAL OBJECTIVE: {objective}
CONVERSATION INTELLIGENCE: ...

AVAILABLE CONTENT: {product_titles}

RELEVANT MEMORY: {memories}

FAN KNOWLEDGE: {knowledge_items}

LOCAL TIME: {time} ({timezone})

[Recent messages...]
```

### 8.2 Future Format (Qwen2.5/3 with Context Engine)

The Context Engine produces a structured context that can be serialized to Qwen's expected format:

```python
def serialize_context_for_qwen(
    assembled: AssembledContext,
) -> list[dict[str, str]]:
    """Serialize assembled context for Qwen2.5/3."""
    messages: list[dict[str, str]] = []

    # System prompt (compressed persona + rules)
    messages.append({
        "role": "system",
        "content": assembled.system_prompt,
    })

    # State block (deterministic facts)
    if assembled.state_block:
        messages.append({
            "role": "system",
            "content": f"STATE:\n{assembled.state_block}",
        })

    # Commerce block (when applicable)
    if assembled.commerce_block:
        messages.append({
            "role": "system",
            "content": f"COMMERCE:\n{assembled.commerce_block}",
        })

    # Memory block (when applicable)
    if assembled.memory_block:
        messages.append({
            "role": "system",
            "content": f"MEMORY:\n{assembled.memory_block}",
        })

    # Temporal block (when applicable)
    if assembled.temporal_block:
        messages.append({
            "role": "system",
            "content": f"TEMPORAL:\n{assembled.temporal_block}",
        })

    # Content block (when applicable)
    if assembled.content_block:
        messages.append({
            "role": "system",
            "content": f"CONTENT:\n{assembled.content_block}",
        })

    # Conversation turns
    for turn in assembled.conversation_turns:
        messages.append({
            "role": turn["role"],
            "content": turn["content"],
        })

    return messages
```

---

## 9. Structured Output Contract

### 9.1 Single-Generation Output

The Context Engine feeds a single Qwen2.5/3 generation that produces both text AND optional signals:

```python
@dataclass(frozen=True)
class ContextEngineOutput:
    """Output from a single Context Engine generation."""
    # Primary output
    response_text: str

    # Optional structured signals (when Qwen supports structured output)
    signals: CommerceSignals | None = None

    # Metadata
    token_count: int
    generation_time_ms: int
    degradation_level: int
    context_items_used: int

    # Deterministic authority
    decision: CommerceDecision | None = None
```

### 9.2 Output Parsing

```python
def parse_context_engine_output(
    raw_output: str,
    format: str = "text",
) -> ContextEngineOutput:
    """Parse Qwen output into structured format."""
    if format == "json":
        # Parse JSON response with signals
        try:
            data = json.loads(raw_output)
            return ContextEngineOutput(
                response_text=data.get("response", ""),
                signals=CommerceSignals(**data.get("signals", {})),
                token_count=data.get("token_count", 0),
                generation_time_ms=data.get("generation_time_ms", 0),
                degradation_level=data.get("degradation_level", 0),
                context_items_used=data.get("context_items_used", 0),
            )
        except (json.JSONDecodeError, ValidationError):
            # Fallback to text parsing
            pass

    # Text format: extract response only
    return ContextEngineOutput(
        response_text=raw_output,
        signals=None,
        token_count=len(raw_output.split()),
        generation_time_ms=0,
        degradation_level=0,
        context_items_used=0,
    )
```

### 9.3 Deterministic Authority Invariant

The Context Engine output MUST NOT override deterministic authority:

```python
def validate_output_authority(
    output: ContextEngineOutput,
    decision: CommerceDecision,
) -> bool:
    """Validate that output respects deterministic authority."""
    # Response text must not contain product/price/URL inventing
    if output.response_text:
        # Check for invented product details
        if re.search(r"\$\d+\.\d+", output.response_text):
            logger.warning("Output contains price mentions — possible authority violation")
            return False

    # If signals are present, they must be advisory only
    if output.signals:
        # Signals should never set allowed/action directly
        if hasattr(output.signals, "allowed"):
            logger.warning("Signals contain 'allowed' field — authority violation")
            return False

    return True
```

---

## 10. Deterministic Authority Hierarchy

### 10.1 Authority Levels

```
Level 0: Hard Policy (never overridden)
    ├── PolicyDecision.allowed (commerce.eligibility)
    ├── creator_sales_enabled (db.dropfans)
    ├── Product identity/price/URL (db.fangate)
    └── Purchase attribution (commerce.dao)

Level 1: Deterministic Rules (PURE, no LLM)
    ├── CommerceDecision.action (commerce.decision)
    ├── Cooldowns (commerce.dao)
    ├── Offer state transitions (commerce.dao)
    ├── Relationship state (commerce.relationship)
    └── Operator handoff (commerce.relationship)

Level 2: Deterministic Derivation (computed, no LLM)
    ├── ConversationState (core.conversation_state)
    ├── Desire stage (commerce.desire)
    ├── Commercial temperature (commerce.temperature)
    ├── Offer readiness (commerce.offer_readiness)
    └── Next best action (commerce.conversation_intelligence)

Level 3: Context Assembly (Context Engine)
    ├── Data gathering (parallel DB reads)
    ├── Token budget management
    ├── Context ranking and relevance
    └── Serialization for Qwen

Level 4: LLM Generation (Qwen2.5/3)
    ├── Response text generation
    ├── Optional signal extraction (advisory)
    └── Quality scoring (advisory)

Level 5: Post-Generation (deterministic)
    ├── Commerce decision (if signals present)
    ├── Response validation
    └── Send/queue routing
```

### 10.2 Authority Boundaries

| Boundary | Level | Owner | Rule |
|---|---|---|---|
| Hard eligibility | 0 | `commerce.eligibility` | Any denial = NO_OFFER |
| Creator sales enabled | 0 | `db.dropfans` | Active integration required |
| Product identity/price/URL | 0 | `db.fangate` | Never from conversation |
| Purchase attribution | 0 | `commerce.dao` | Atomic DB transaction |
| Commerce decision | 1 | `commerce.decision` | PURE engine, first match |
| Cooldowns | 1 | `commerce.dao` | DB timestamps |
| Offer state | 1 | `commerce.dao` | Conditional UPDATEs |
| Relationship state | 1 | `commerce.relationship` | Derived from funnel + purchase |
| Operator handoff | 1 | `commerce.relationship` | Deterministic rules |
| ConversationState | 2 | `core.conversation_state` | Derived from messages |
| Desire/temperature | 2 | `commerce.desire/temperature` | Pure functions |
| Context assembly | 3 | Context Engine | Budget-enforced |
| Response generation | 4 | Qwen2.5/3 | Advisory text |
| Signal extraction | 4 | Qwen2.5/3 | Advisory signals |
| Response validation | 5 | Post-generation | Deterministic rules |

---

## 11. Migration Plan

### 11.1 Phase 70: Implementation Boundary

**Objective:** Define what can be implemented without production changes.

**Scope:**
- Context Engine module structure
- Data gatherer implementation
- Token budget manager
- Context assembler
- Serialization for Qwen format
- Unit tests

**Out of Scope:**
- LLM worker modifications
- Production deployment
- Shadow mode changes
- 3-LLM pipeline changes

### 11.2 Phase 71: Context Engine Core

**Objective:** Implement the Context Engine as a standalone module.

**Deliverables:**
- `context_engine/gatherer.py` — Data gathering
- `context_engine/budget.py` — Token budget management
- `context_engine/assembler.py` — Context assembly
- `context_engine/ranker.py` — Item ranking
- `context_engine/serializer.py` — Qwen format serialization
- `context_engine/tests/` — Unit tests

### 11.3 Phase 72: Integration Testing

**Objective:** Test Context Engine against production data.

**Deliverables:**
- Integration tests with real data
- Token budget validation
- Authority boundary verification
- Performance benchmarks

### 11.4 Phase 73: Shadow Mode Integration

**Objective:** Run Context Engine in shadow alongside current pipeline.

**Deliverables:**
- Shadow mode configuration
- Comparison logging
- Accuracy metrics
- Latency metrics

### 11.5 Phase 74: Production Migration (Future)

**Objective:** Gradually shift traffic to Context Engine.

**Deliverables:**
- Canary routing configuration
- Rollback mechanisms
- Monitoring dashboards
- Performance validation

---

## 12. Phase 70 Boundary

### 12.1 What Phase 70 CAN Do

1. **Create Context Engine module structure:**
   ```
   context_engine/
   ├── __init__.py
   ├── gatherer.py
   ├── budget.py
   ├── assembler.py
   ├── ranker.py
   ├── serializer.py
   ├── models.py
   └── tests/
   ```

2. **Implement data gathering:**
   - Parallel DB reads
   - Relevance-ranked retrieval
   - Vector search integration

3. **Implement token budget management:**
   - Per-item budgets
   - Overflow handling
   - Degradation levels

4. **Implement context assembly:**
   - Priority-based selection
   - Deduplication
   - Serialization for Qwen

5. **Implement unit tests:**
   - Data gathering tests
   - Budget management tests
   - Assembly tests
   - Authority boundary tests

### 12.2 What Phase 70 CANNOT Do

1. **Modify LLM worker:**
   - Cannot change `workers/llm_worker.py`
   - Cannot change `memory/context.py`
   - Cannot change `commerce/pipeline.py`

2. **Modify production configuration:**
   - Cannot change `core/config.py`
   - Cannot change `core/scoring.py`

3. **Modify deterministic authority:**
   - Cannot change `commerce/decision.py`
   - Cannot change `commerce/eligibility.py`
   - Cannot change `commerce/relationship.py`

4. **Deploy to production:**
   - Cannot change Docker configuration
   - Cannot change deployment scripts
   - Cannot change monitoring configuration

### 12.3 Phase 70 Acceptance Criteria

1. **Module structure created:**
   - All files in `context_engine/` directory
   - No changes to existing files

2. **Data gathering works:**
   - Parallel DB reads function correctly
   - Relevance-ranked retrieval returns expected results
   - Vector search integration works

3. **Token budget enforced:**
   - Per-item budgets respected
   - Overflow handling triggers correctly
   - Degradation levels applied

4. **Context assembly correct:**
   - Priority-based selection works
   - Deduplication removes duplicates
   - Serialization produces valid Qwen format

5. **Authority boundaries preserved:**
   - No LLM-derived data marked as authoritative
   - Deterministic authority not overridden
   - Advisory data clearly marked

6. **Tests pass:**
   - All unit tests pass
   - No regressions in existing tests
   - Production safety verified

---

## 13. Performance Considerations

### 13.1 Latency Budget

| Component | Current | Target | Notes |
|---|---|---|---|
| Data gathering | 50-100ms | 20-50ms | Parallel DB reads |
| Token budget | 5ms | 2ms | In-memory operations |
| Context assembly | 10ms | 5ms | String operations |
| Serialization | 5ms | 3ms | String formatting |
| **Total overhead** | **70-120ms** | **30-60ms** | **Before LLM call** |

### 13.2 Token Efficiency

| Metric | Current | Target | Notes |
|---|---|---|---|
| Context tokens | 1600 | 2600 | +1000 for richer context |
| Conversation turns | 10-15 | 15-20 | More history |
| Memory items | 3 | 5 | More recall |
| Knowledge items | 5 | 8 | Deeper personalization |

### 13.3 Quality Metrics

| Metric | Current | Target | Notes |
|---|---|---|---|
| Context relevance | Baseline | +15% | Topic-aware retrieval |
| Response coherence | Baseline | +10% | Consistent context |
| Signal accuracy | Baseline | +20% | Richer context |
| Authority compliance | 100% | 100% | No regression |

---

## 14. Risk Assessment

### 14.1 Technical Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Token budget overflow | Response quality degradation | Graceful degradation strategy |
| Parallel DB read failures | Missing context | Fallback to sequential reads |
| Vector search latency | Increased response time | Optional, not blocking |
| Qwen format incompatibility | Generation failure | Multiple format options |

### 14.2 Authority Risks

| Risk | Impact | Mitigation |
|---|---|---|
| LLM output overriding deterministic authority | Commerce policy violation | Strict authority boundary validation |
| Context assembly bypassing eligibility | Unauthorized sales | Pre-generation eligibility check |
| Signal extraction producing false positives | Inappropriate offers | Advisory-only signals, deterministic decision |

### 14.3 Production Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Context Engine failure | Response generation blocked | Fallback to current pipeline |
| Performance degradation | Increased latency | Canary routing, rollback |
| Token budget miscalculation | Context truncation | Conservative budgets, monitoring |

---

## 15. Monitoring and Observability

### 15.1 Metrics to Track

```python
# Context Engine metrics
context_engine_tokens_used = Histogram("context_engine_tokens_used", "Tokens used per context assembly")
context_engine_items_selected = Histogram("context_engine_items_selected", "Items selected per assembly")
context_engine_degradation_level = Gauge("context_engine_degradation_level", "Current degradation level")
context_engine_gather_latency_ms = Histogram("context_engine_gather_latency_ms", "Data gathering latency")
context_engine_assembly_latency_ms = Histogram("context_engine_assembly_latency_ms", "Context assembly latency")

# Authority boundary metrics
context_engine_authority_violations = Counter("context_engine_authority_violations", "Authority boundary violations detected")
context_engine_output_validated = Counter("context_engine_output_validated", "Outputs validated for authority compliance")
```

### 15.2 Logging Strategy

```python
# Structured logging for context assembly
logger.info(
    "context_engine_assembly",
    user_id=user_id,
    creator_id=creator_id,
    items_gathered=len(gathered_items),
    items_selected=len(selected_items),
    tokens_used=assembled.token_count,
    degradation_level=assembled.degradation_level,
    gather_latency_ms=gather_latency,
    assembly_latency_ms=assembly_latency,
)

# Authority boundary logging
logger.warning(
    "context_engine_authority_violation",
    user_id=user_id,
    violation_type="llm_output_overriding_deterministic",
    details=violation_details,
)
```

### 15.3 Dashboard Panels

1. **Context Engine Performance:**
   - Tokens used per assembly
   - Items selected per assembly
   - Degradation level distribution
   - Gather/assembly latency

2. **Authority Boundary:**
   - Violation count
   - Validation success rate
   - Advisory vs authoritative ratio

3. **Quality Metrics:**
   - Context relevance scores
   - Response coherence scores
   - Signal accuracy metrics

---

## 16. Testing Strategy

### 16.1 Unit Tests

```python
# Test data gathering
def test_parallel_data_gathering():
    """Test that data gathering runs in parallel."""
    pass

def test_relevance_ranked_retrieval():
    """Test that retrieval ranks by relevance."""
    pass

def test_vector_search_integration():
    """Test vector search returns expected results."""
    pass

# Test token budget
def test_per_item_budgets():
    """Test per-item budgets are enforced."""
    pass

def test_overflow_handling():
    """Test overflow triggers degradation."""
    pass

def test_degradation_levels():
    """Test degradation levels applied correctly."""
    pass

# Test context assembly
def test_priority_based_selection():
    """Test items selected by priority."""
    pass

def test_deduplication():
    """Test duplicate items removed."""
    pass

def test_serialization():
    """Test serialization produces valid Qwen format."""
    pass

# Test authority boundaries
def test_authoritative_data_preserved():
    """Test authoritative data not removed."""
    pass

def test_advisory_data_marked():
    """Test advisory data clearly marked."""
    pass

def test_llm_output_not_overriding():
    """Test LLM output cannot override deterministic authority."""
    pass
```

### 16.2 Integration Tests

```python
# Test with real data
def test_context_engine_with_production_data():
    """Test Context Engine with production-like data."""
    pass

def test_token_budget_with_real_messages():
    """Test token budget with real message history."""
    pass

def test_context_relevance_with_real_topics():
    """Test context relevance with real conversation topics."""
    pass

# Test authority boundaries
def test_commerce_decision_authority():
    """Test commerce decision remains deterministic."""
    pass

def test_eligibility_authority():
    """Test eligibility remains deterministic."""
    pass

def test_relationship_authority():
    """Test relationship state remains deterministic."""
    pass
```

### 16.3 Performance Tests

```python
# Test latency
def test_gather_latency():
    """Test data gathering completes within budget."""
    pass

def test_assembly_latency():
    """Test context assembly completes within budget."""
    pass

def test_total_overhead():
    """Test total Context Engine overhead within budget."""
    pass

# Test scalability
def test_concurrent_assemblies():
    """Test concurrent context assemblies don't interfere."""
    pass

def test_large_message_history():
    """Test with large message histories."""
    pass
```

---

## 17. Implementation Checklist

### Phase 70 Deliverables

- [ ] Create `context_engine/` directory structure
- [ ] Implement `context_engine/models.py` — ContextItem, ContextData, AssembledContext
- [ ] Implement `context_engine/gatherer.py` — Parallel data gathering
- [ ] Implement `context_engine/budget.py` — Token budget management
- [ ] Implement `context_engine/ranker.py` — Item ranking and relevance
- [ ] Implement `context_engine/assembler.py` — Context assembly
- [ ] Implement `context_engine/serializer.py` — Qwen format serialization
- [ ] Implement `context_engine/tests/test_models.py`
- [ ] Implement `context_engine/tests/test_gatherer.py`
- [ ] Implement `context_engine/tests/test_budget.py`
- [ ] Implement `context_engine/tests/test_ranker.py`
- [ ] Implement `context_engine/tests/test_assembler.py`
- [ ] Implement `context_engine/tests/test_serializer.py`
- [ ] Implement `context_engine/tests/test_authority.py`
- [ ] Run all tests, verify pass
- [ ] Run production safety verification
- [ ] Create deliverable report

### Acceptance Criteria

- [ ] No changes to existing production files
- [ ] All unit tests pass
- [ ] Token budgets enforced correctly
- [ ] Authority boundaries preserved
- [ ] Serialization produces valid Qwen format
- [ ] Performance within budget (30-60ms overhead)

---

## 18. Appendix A: Current Data-Access Paths

### A.1 Persona State

| Data | Source | Function | Authority |
|---|---|---|---|
| Persona instructions | PostgreSQL `personas.instructions` | `get_user_persona()` | Authoritative |
| Structured persona | PostgreSQL `personas.metadata` | `get_structured_persona_async()` | Authoritative |
| Persona name | Derived from metadata | `render_compact_persona_block()` | Authoritative |
| Identity lifecycle | In-memory derivation | `identity_already_established_from_messages()` | Deterministic |

### A.2 Fan State

| Data | Source | Function | Authority |
|---|---|---|---|
| Fan identity | PostgreSQL `users` | `get_user()` | Authoritative |
| Fan profile | PostgreSQL `user_profiles.facts` | `get_user_profile()` | Authoritative |
| Funnel stage | PostgreSQL `users.funnel_stage` | `get_user()` | Authoritative |
| Message count | PostgreSQL `users.message_count` | `get_user()` | Authoritative |

### A.3 Conversation History

| Data | Source | Function | Authority |
|---|---|---|---|
| Recent messages | PostgreSQL `messages` | `get_recent_messages()` | Authoritative |
| Conversation summary | PostgreSQL `conversation_summaries` | `get_latest_summary_with_age()` | Authoritative |
| Message embeddings | PostgreSQL `message_embeddings` | `vector_search_messages()` | Advisory |
| Conversation state | Derived from messages | `derive_conversation_state()` | Deterministic |

### A.4 Commerce State

| Data | Source | Function | Authority |
|---|---|---|---|
| Commerce context | PostgreSQL (multiple tables) | `build_llm_context()` | Authoritative |
| Commerce signals | LLM output | `extract_commerce_signals()` | Advisory |
| Commerce decision | Pure rules | `decide_commerce_action()` | Authoritative |
| Eligibility | Pure rules | `evaluate_ppv_eligibility()` | Authoritative |

### A.5 Memory

| Data | Source | Function | Authority |
|---|---|---|---|
| Long-term memory | PostgreSQL `user_profiles.facts` | `retrieve_relevant_memories()` | Authoritative |
| Fan knowledge | PostgreSQL `user_profiles.facts` | `retrieve_relevant_knowledge()` | Authoritative |
| Temporal context | Derived from knowledge | `temporal_context_for_fan()` | Deterministic |

---

## 19. Appendix B: Token Budget Calculations

### B.1 Current Budget (1600 tokens)

```
System prompt:     400 tokens (25%)
State facts:       200 tokens (12.5%)
Conversation:      800 tokens (50%)
Summary:           200 tokens (12.5%)
Total:            1600 tokens (100%)
```

### B.2 Future Budget (2600 tokens)

```
System prompt:     400 tokens (15.4%)
State facts:       200 tokens (7.7%)
Commerce context:  200 tokens (7.7%)
Long-term memory:  150 tokens (5.8%)
Fan knowledge:     150 tokens (5.8%)
Temporal context:   50 tokens (1.9%)
Vault content:     100 tokens (3.8%)
Conversation:      800 tokens (30.8%)
Embedded history:  200 tokens (7.7%)
Total:            2600 tokens (100%)
```

### B.3 Overflow Thresholds

| Level | Tokens | Action |
|---|---|---|
| NONE | ≤ 2600 | Full context |
| MINOR | 2601-2800 | Truncate vault content |
| MODERATE | 2801-3000 | Remove temporal + vault |
| SEVERE | 3001-3200 | Remove memory + knowledge |
| CRITICAL | > 3200 | System + last 3 messages only |

---

## 20. Appendix C: Authority Boundary Validation

### C.1 Validation Rules

```python
AUTHORITY_VALIDATION_RULES = {
    # Level 0: Hard policy — never overridden
    "policy_decision_allowed": lambda x: isinstance(x, bool),
    "creator_sales_enabled": lambda x: isinstance(x, bool),
    "product_identity": lambda x: x is None or isinstance(x, str),
    "product_price": lambda x: x is None or isinstance(x, (int, float)),
    "product_url": lambda x: x is None or isinstance(x, str),

    # Level 1: Deterministic rules — PURE
    "commerce_decision_action": lambda x: isinstance(x, CommerceAction),
    "commerce_decision_allowed": lambda x: isinstance(x, bool),
    "cooldowns": lambda x: isinstance(x, dict),
    "offer_state": lambda x: isinstance(x, OfferState),

    # Level 2: Deterministic derivation — computed
    "conversation_state": lambda x: isinstance(x, ConversationState),
    "desire_stage": lambda x: isinstance(x, str),
    "commercial_temperature": lambda x: isinstance(x, str),
    "offer_readiness": lambda x: isinstance(x, str),

    # Level 3: Context assembly — budget-enforced
    "context_items": lambda x: isinstance(x, list),
    "token_count": lambda x: isinstance(x, int) and x >= 0,
    "degradation_level": lambda x: isinstance(x, int) and 0 <= x <= 4,

    # Level 4: LLM generation — advisory
    "response_text": lambda x: isinstance(x, str),
    "signals": lambda x: x is None or isinstance(x, CommerceSignals),

    # Level 5: Post-generation — deterministic
    "output_validated": lambda x: isinstance(x, bool),
}
```

### C.2 Validation Pipeline

```python
def validate_context_engine_output(
    output: ContextEngineOutput,
    context_data: ContextData,
) -> tuple[bool, list[str]]:
    """Validate Context Engine output against authority boundaries."""
    violations: list[str] = []

    # Check Level 0: Hard policy
    if output.decision:
        if not AUTHORITY_VALIDATION_RULES["policy_decision_allowed"](output.decision.allowed):
            violations.append("policy_decision_allowed violation")

    # Check Level 4: LLM generation
    if output.signals:
        if hasattr(output.signals, "allowed"):
            violations.append("LLM signals contain 'allowed' field")

    # Check response text for authority violations
    if output.response_text:
        if re.search(r"\$\d+\.\d+", output.response_text):
            violations.append("Response contains price mentions")

    return len(violations) == 0, violations
```

---

## 21. Appendix D: Serialization Examples

### D.1 Current Qwen3 Format

```json
[
  {"role": "system", "content": "You are Sunny Skye..."},
  {"role": "system", "content": "CREATOR PERSONA (compact): ..."},
  {"role": "system", "content": "STATE: John | engaged\nPROFILE: ...\nRELATIONSHIP: ...\nCOMMERCE: ...\nSUMMARY: ..."},
  {"role": "system", "content": "AVAILABLE CONTENT: Product A | Product B"},
  {"role": "system", "content": "RELEVANT MEMORY: ..."},
  {"role": "system", "content": "FAN KNOWLEDGE: ..."},
  {"role": "system", "content": "LOCAL TIME: 14:30 (EST)"},
  {"role": "user", "content": "Hey, how are you?"},
  {"role": "assistant", "content": "I'm great! ..."},
  {"role": "user", "content": "What about that photo you mentioned?"}
]
```

### D.2 Future Context Engine Format

```json
[
  {"role": "system", "content": "You are Sunny Skye...\n\nRules:\n- 2-4 sentences..."},
  {"role": "system", "content": "STATE:\n- Name: John\n- Stage: engaged\n- Relationship: warm\n- Last seen: 2 hours ago"},
  {"role": "system", "content": "COMMERCE:\n- Desire stage: warming\n- Temperature: soft\n- Offer readiness: not_ready\n- Objective: build_rapport"},
  {"role": "system", "content": "MEMORY:\n- interest=photography (fact, conf 0.9)\n- plan=trip_to_europe (plan, conf 0.7)"},
  {"role": "system", "content": "TEMPORAL:\n- Timezone: EST\n- Local time: 14:30\n- Context: afternoon"},
  {"role": "system", "content": "CONTENT:\n- Available: Sunset Collection | Beach Series"},
  {"role": "user", "content": "Hey, how are you?"},
  {"role": "assistant", "content": "I'm great! ..."},
  {"role": "user", "content": "What about that photo you mentioned?"}
]
```

---

**End of Phase 69 Specification**
