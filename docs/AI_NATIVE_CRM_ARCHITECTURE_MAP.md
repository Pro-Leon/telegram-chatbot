# AI-Native CRM Architecture Map

**Date:** 2026-08-28  
**Status:** Phase 1 Complete — Forensic Architecture Mapping

---

## Executive Summary

The existing CRM architecture is already 80% agent-ready. The key insight: `core/llm_tools.py` already establishes the correct agent boundary with 7 governed tools. The implementation strategy is to add a thin agent runtime layer on top of existing infrastructure, not to redesign from scratch.

---

## Current Architecture → Agent Architecture Mapping

### What Becomes Agent State

| Current Component | Agent Role | Notes |
|-------------------|------------|-------|
| `LLMContext` (memory/context_assembler.py) | **Agent State** | Frozen dataclass with ~30 fields. Already compact. |
| `CommerceConversationContext` (commerce/context.py) | **Agent State** | Pydantic model, strict validation. |
| `CommerceDecisionContext` (commerce/decision.py) | **Agent State** | Pure engine input. |
| `CommerceStrategy` (commerce/strategy.py) | **Agent State** | Structured conversational strategy. |
| `ToolAuthContext` (core/llm_tools.py) | **Agent Identity** | Runtime identity injected by application. |

### What Becomes Agent Tools

| Current Function | Tool Name | Authority | Already Implemented |
|------------------|-----------|-----------|---------------------|
| `get_purchase_history()` | `get_purchase_history` | READ | ✅ Yes |
| `get_active_offers()` | `get_active_offers` | READ | ✅ Yes |
| `get_product_information()` | `get_product_information` | READ | ✅ Yes |
| `list_products()` | `list_products` | READ | ✅ Yes |
| `create_scheduled_message()` | `propose_follow_up` | PROPOSAL | ✅ Yes |
| `resolve_and_run_commerce()` | `propose_product_offer` | PROPOSAL | ✅ Yes |
| `check_tip_eligibility()` | `suggest_tip` | PROPOSAL | ✅ Yes |
| `derive_relationship_state()` | `get_relationship_state` | READ | 🔧 New |
| `check_operator_handoff()` | `check_operator_handoff` | READ | 🔧 New |
| `get_user_profile()` | `get_user_profile` | READ | 🔧 New |
| `retrieve_relevant_history()` | `search_conversation_history` | READ | 🔧 New |
| `extract_commerce_signals()` | `analyze_conversation_signals` | READ | 🔧 New |
| `derive_commercial_pressure()` | `get_commercial_pressure` | READ | 🔧 New |
| `check_tip_eligibility()` | `check_tip_eligibility` | READ | 🔧 New |

### What Remains Unchanged (Infrastructure)

| Component | File | Why Unchanged |
|-----------|------|---------------|
| Redis Streams | `db/redis.py` | Queue infrastructure — agents enqueue, not manage streams |
| Consumer groups | `db/redis.py` | Worker coordination — not agent-facing |
| User locking | `db/redis.py` | Concurrency control — must stay in worker |
| Debounce mechanism | `db/redis.py` | Rate smoothing — must stay in handler |
| Token bucket rate limiting | `db/redis.py` | Infrastructure — not agent-facing |
| Connection pools | `db/postgres.py`, `db/redis.py` | Infrastructure — managed by lifecycle |
| CredentialPool | `core/gemini_client.py` | Security boundary — agents must never handle credentials |
| Execute PPV | `commerce/execution.py` | Transactional authority — agent proposes, application executes |
| Autonomy kill switch | `core/config.py` | Safety boundary — server-side only |
| Event bus publish | `core/event_bus.py` | Best-effort notification — agents should not control |
| DLQ management | `db/redis.py` | Infrastructure — operator/admin concern |
| Schema migrations | `db/migrate.py` | Infrastructure — developer concern |

### What Can Be Removed (Dead/Duplicate)

| Component | File | Reason |
|-----------|------|--------|
| `DRAFT_STREAM` | `db/redis.py` | Dead code — never consumed |
| `save_outbound_message()` | `chatbotv2/telegram.py` | Fire-and-forget — audit trail gaps |
| Behavioral in-memory store | `memory/context.py` | Non-persistent — re-derive from DB |
| `shadow_is_handoff_paused()` | `core/config.py` | Dead code — removed |

---

## Agent Architecture Design

### Agent Runtime Components

```
┌─────────────────────────────────────────────────────────────┐
│                    AGENT RUNTIME                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │ Agent State │───▶│ Agent Loop  │───▶│ Tool Router │     │
│  └─────────────┘    └─────────────┘    └─────────────┘     │
│         │                   │                   │           │
│         ▼                   ▼                   ▼           │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │   Memory    │    │   LLM Provider│   │   Tools     │     │
│  └─────────────┘    └─────────────┘    └─────────────┘     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Agent State (Compact)

```python
@dataclass(frozen=True)
class AgentState:
    # Identity
    creator_id: int
    user_id: int
    
    # Conversation
    current_message: str
    conversation_history: list[dict]
    
    # Memory
    profile: dict | None
    summary: str | None
    relevant_memories: list[str]
    
    # Relationship
    relationship_state: str
    commercial_pressure: str
    
    # Commerce
    eligible_products: list[dict]
    active_offers: list[dict]
    purchase_history: list[dict]
    
    # Behavioral
    tip_eligibility: str
    aftercare_status: str
    rejection_count: int
    
    # Tools available
    available_tools: list[str]
    
    # Limits
    max_tool_calls: int = 5
    max_response_time: float = 30.0
```

### Agent Loop (Bounded)

```python
class AgentLoop:
    def __init__(self, state: AgentState, provider: LLMProvider):
        self.state = state
        self.provider = provider
        self.tool_calls = 0
        self.max_tool_calls = 5
        
    async def run(self) -> str:
        """Bounded agent loop - terminate deterministically."""
        
        # 1. Observe: Build compact context
        context = self._build_context()
        
        # 2. Reason: Get LLM response with tool calling
        response = await self.provider.generate_with_tools(
            messages=context,
            tools=self._get_tool_definitions(),
            max_tokens=500
        )
        
        # 3. Act: Execute tool calls if any
        while response.tool_calls and self.tool_calls < self.max_tool_calls:
            for tool_call in response.tool_calls:
                result = await self._execute_tool(tool_call)
                self.tool_calls += 1
                
                # Observe result
                context.append({"role": "tool", "content": result})
            
            # Continue loop
            response = await self.provider.generate_with_tools(
                messages=context,
                tools=self._get_tool_definitions(),
                max_tokens=500
            )
        
        # 4. Respond: Return final text
        return response.text
```

### Tool Router (Governed)

```python
class ToolRouter:
    def __init__(self, auth_context: ToolAuthContext):
        self.auth_context = auth_context
        
    async def execute(self, tool_name: str, args: dict) -> str:
        """Execute tool with authority validation."""
        
        # 1. Validate tool exists
        tool = self._get_tool(tool_name)
        if not tool:
            return json.dumps({"error": "Tool not found"})
        
        # 2. Validate authority
        if not tool.validate_auth(self.auth_context):
            return json.dumps({"error": "Unauthorized"})
        
        # 3. Validate arguments
        if not tool.validate_args(args):
            return json.dumps({"error": "Invalid arguments"})
        
        # 4. Execute with timeout
        try:
            result = await asyncio.wait_for(
                tool.handler(args, self.auth_context),
                timeout=tool.timeout
            )
            return json.dumps(result)
        except asyncio.TimeoutError:
            return json.dumps({"error": "Tool timeout"})
        except Exception as e:
            return json.dumps({"error": str(e)})
```

---

## Implementation Plan

### Phase 1: Architecture Mapping ✅ COMPLETE
- [x] Inspect current codebase
- [x] Identify agent state components
- [x] Identify agent tools
- [x] Identify unchanged infrastructure
- [x] Identify dead code
- [x] Create architecture map

### Phase 2: Agent Core (Next)
- [ ] Create `agent/state.py` — AgentState dataclass
- [ ] Create `agent/loop.py` — Bounded agent loop
- [ ] Create `agent/tools.py` — Tool router wrapping existing tools
- [ ] Create `agent/memory.py` — Memory integration
- [ ] Create `agent/__init__.py` — Public API
- [ ] Update `workers/llm_worker.py` — Add agent path
- [ ] Add tests for agent core

### Phase 3: Memory Tools (Future)
- [ ] Expose profile retrieval/update
- [ ] Expose conversation history
- [ ] Expose vector search
- [ ] Expose summarization

### Phase 4: Relationship Agent (Future)
- [ ] Integrate relationship state reasoning
- [ ] Integrate behavioral feedback
- [ ] Integrate commercial pressure assessment

### Phase 5: Commerce Tools (Future)
- [ ] Integrate commerce signal extraction
- [ ] Integrate decision evaluation
- [ ] Integrate strategy building

### Phase 6: Observability (Future)
- [ ] Record agent turns
- [ ] Record tool calls
- [ ] Record latency
- [ ] Record authority decisions

### Phase 7: Testing (Future)
- [ ] Adversarial tests
- [ ] Authority boundary tests
- [ ] Failure mode tests

---

## Critical Invariants Preserved

| Invariant | How Preserved |
|-----------|---------------|
| Deterministic commerce decision | Agent proposes, `decide_commerce_action()` decides |
| Product/price/URL authority | Agent never specifies these values |
| Creator isolation | `ToolAuthContext` injected by application |
| Offer idempotency | `execute_ppv()` serialized advisory lock |
| AUTONOMY_ENABLED kill switch | Checked before any autonomous action |
| DropFans-only commerce | Only active provider in execution path |
| No autonomous Fangate | Fangate not in autonomous path |
| No hidden Gemini fallback | Provider selection explicit |
| Existing send queue | Agent text → `enqueue_send_message()` |
| Existing debounce | Handler debounce unchanged |
| Existing memory persistence | Agent uses existing DB/Redis |
| Existing failure classification | Agent failures classified same way |
| Existing operator handoff | `check_operator_handoff()` tool |

---

*Architecture map created: 2026-08-28*
