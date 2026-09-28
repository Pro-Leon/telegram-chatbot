# AI-Native Runtime Integration Audit

**Date:** 2026-08-28  
**Status:** Phase 1 Complete — Forensic Integration Audit

---

## Executive Summary

The current `process_message()` execution path is well-structured with clear authority boundaries. The agent runtime should replace ONLY the generation step (lines 444-469) while preserving all context building, scoring, routing, and persistence. The existing tool infrastructure in `core/llm_tools.py` already establishes the correct agent boundary.

---

## Current Call Graph

```
process_message() [llm_worker.py:365]
  |
  +-- acquire_user_lock()                    [Redis NX lock]
  +-- upsert_user()                          [Postgres UPSERT]
  +-- is_user_auto_reply_excluded()          [Postgres SELECT]
  |
  +-- resolve_single_application_creator()   [Postgres SELECT]
  |
  +-- build_qwen3_context()                  [Multi-DB read assembly]
  |     +-- get_user()                       [Postgres SELECT]
  |     +-- get_user_profile()               [Postgres SELECT]
  |     +-- build_qwen3_system_prompt()      [Pure string render]
  |     +-- build_llm_context()              [12 parallel DB reads]
  |     +-- render_context()                 [Pure string render]
  |     +-- get_latest_summary_with_age()    [Postgres SELECT]
  |     +-- build_qwen3_state_context()      [Pure string render]
  |     +-- get_recent_messages()            [Postgres SELECT]
  |     +-- trim_to_token_budget()           [Pure function]
  |
  +-- [shadow task launch]                   [Async, fire-and-forget]
  +-- publish_event("ai.generation_started") [Redis Pub/Sub]
  |
  +-- _try_commerce_draft()                  [Commerce authority boundary]
  |     +-- resolve_single_application_creator()
  |     +-- resolve_commerce_product_with_history()
  |     +-- resolve_and_run_commerce()       [6D boundary]
  |     +-- select_commerce_response()       [6E boundary]
  |
  +-- [AGENT REPLACES THIS SECTION]          [Lines 444-469]
  |     Path A: Commerce response (if available)
  |     Path B: generate_draft_with_tools()  [Tool loop boundary]
  |     Path C: generate_draft()             [Plain LLM generation]
  |
  +-- score_draft()                          [Scoring boundary]
  +-- [shadow collection + evaluation]
  +-- is_auto_reply_enabled()                [Redis read]
  |
  +-- [Routing decision]:
  |     +-- [auto_reply OFF]: add_to_operator_queue()
  |     +-- [score >= 0.80 AND no flags]: enqueue_send()
  |     +-- [else]: add_to_operator_queue() + notify_operators()
  |
  +-- publish_event("ai.generation_completed") [Redis Pub/Sub]
  +-- post_process() [fire-and-forget]
  +-- release_user_lock() [finally]          [Redis DELETE]
```

---

## Proposed Call Graph (Agent Mode)

```
process_message() [llm_worker.py:365]
  |
  +-- acquire_user_lock()                    [UNCHANGED]
  +-- upsert_user()                          [UNCHANGED]
  +-- is_user_auto_reply_excluded()          [UNCHANGED]
  |
  +-- resolve_single_application_creator()   [UNCHANGED]
  |
  +-- build_qwen3_context()                  [UNCHANGED - used for agent state]
  |
  +-- [shadow task launch]                   [UNCHANGED]
  +-- publish_event("ai.generation_started") [UNCHANGED]
  |
  +-- _try_commerce_draft()                  [UNCHANGED - still runs first]
  |
  +-- [AGENT RUNTIME]                        [NEW - replaces lines 444-469]
  |     +-- build_agent_state()              [NEW - thin adapter]
  |     +-- AgentLoop.run()                  [NEW - bounded loop]
  |     |     +-- _build_context()           [Compact context]
  |     |     +-- _get_llm_response()        [LLM with tool calling]
  |     |     +-- _execute_tools()           [Governed tools]
  |     |     +-- [repeat until done]
  |     +-- return AgentTurn                 [Structured result]
  |
  +-- score_draft()                          [UNCHANGED]
  +-- [shadow collection + evaluation]       [UNCHANGED]
  +-- is_auto_reply_enabled()                [UNCHANGED]
  |
  +-- [Routing decision]                     [UNCHANGED]
  +-- publish_event("ai.generation_completed") [UNCHANGED]
  +-- post_process()                         [UNCHANGED]
  +-- release_user_lock()                    [UNCHANGED]
```

---

## Authority Boundaries

### What the Agent CAN Do

| Action | How | Authority |
|--------|-----|-----------|
| Read relationship state | `get_relationship_state` tool | READ_ONLY |
| Read user profile | `get_user_profile` tool | READ_ONLY |
| Read conversation summary | `get_conversation_summary` tool | READ_ONLY |
| Search conversation history | `search_conversation_history` tool | READ_ONLY |
| Read commerce context | `get_commerce_context` tool | READ_ONLY |
| Check operator handoff | `check_operator_handoff` tool | READ_ONLY |
| Analyze conversation signals | `analyze_conversation_signals` tool | READ_ONLY |
| Generate conversational text | LLM generation | TEXT_ONLY |

### What the Agent CANNOT Do

| Action | Why | Authority Boundary |
|--------|-----|-------------------|
| Invent product IDs | Deterministic product selection | `resolve_commerce_product_with_history()` |
| Invent prices | Application-controlled | `fangate_products` table |
| Invent URLs | Application-controlled | `fangate_products` table |
| Create offers directly | Must go through commerce pipeline | `resolve_and_run_commerce()` |
| Send Telegram messages | Must go through send queue | `enqueue_send()` |
| Access DropFans directly | Only through governed tools | `AutomationService` |
| Access Fangate directly | Read-only mirror table | `fangate_products` |
| Bypass AUTONOMY_ENABLED | Server-side setting | `_settings.autonomy_enabled` |
| Bypass creator isolation | `ToolAuthContext` injected by app | `ToolAuthContext` |
| Override scoring | Deterministic scoring | `score_draft()` |
| Override routing | Deterministic routing | Auto-approve threshold |

---

## State Ownership

| State | Owner | Agent Access |
|-------|-------|--------------|
| User identity | `db/postgres.py` | READ_ONLY |
| User profile | `memory/profile.py` | READ_ONLY |
| Conversation history | `db/postgres.py` | READ_ONLY |
| Conversation summary | `memory/summarizer.py` | READ_ONLY |
| Relationship state | `commerce/relationship.py` | READ_ONLY |
| Commercial pressure | `commerce/relationship.py` | READ_ONLY |
| Tip eligibility | `commerce/relationship.py` | READ_ONLY |
| Product identity | `commerce/product_selection.py` | READ_ONLY |
| Product price | `fangate_products` table | READ_ONLY |
| Product URL | `fangate_products` table | READ_ONLY |
| Offer state | `commerce/dao.py` | READ_ONLY |
| Purchase history | `db/postgres.py` | READ_ONLY |
| Behavioral feedback | `commerce/dao.py` | READ_ONLY |
| Agent state | `agent/state.py` | READ_WRITE (ephemeral) |
| Agent tool results | `agent/tools.py` | READ_WRITE (ephemeral) |

---

## Tool Ownership

| Tool | Existing Implementation | Agent Tool Wraps | Duplicates? |
|------|------------------------|------------------|-------------|
| `get_relationship_state` | `commerce/relationship.py` | `derive_relationship_state()` | No - wraps |
| `get_user_profile` | `db/postgres.py` | `get_user_profile()` | No - wraps |
| `get_conversation_summary` | `db/postgres.py` | `get_latest_summary()` | No - wraps |
| `search_conversation_history` | `memory/retrieval.py` | `retrieve_relevant_history()` | No - wraps |
| `get_conversation_history` | `db/postgres.py` | `get_recent_messages()` | No - wraps |
| `get_commerce_context` | `memory/context_assembler.py` | `build_llm_context()` | No - wraps |
| `check_operator_handoff` | `commerce/relationship.py` | `check_operator_handoff()` | No - wraps |
| `analyze_conversation_signals` | `commerce/deepseek.py` | `extract_commerce_signals()` | No - wraps |

**Verdict: No duplicate logic. All tools wrap existing implementations.**

---

## Failure Boundaries

| Failure | Agent Handling | Existing Handling | Compatible? |
|---------|---------------|-------------------|-------------|
| LLM timeout | Return error text | Return empty string | Yes |
| Tool timeout | Return tool error | Return tool error | Yes |
| Tool failure | Return tool error | Return tool error | Yes |
| Context build failure | Use empty context | Use empty context | Yes |
| Memory retrieval failure | Skip memory | Skip memory | Yes |
| Commerce failure | Continue without commerce | Continue without commerce | Yes |
| Provider failure | Return error text | Return error text | Yes |
| Max iterations reached | Return last response | N/A (new) | Yes |
| Max tool calls reached | Return last response | N/A (new) | Yes |

---

## Rollback Boundary

### Rollback Procedure

1. Set `AI_RUNTIME_MODE=legacy` in `.env`
2. Restart worker: `python -m workers.llm_worker --worker-id worker_1`
3. Agent runtime disabled, legacy path active
4. No data loss, no state corruption

### Rollback Safety

- Agent runtime is additive, not replacing
- Legacy path remains fully functional
- No schema changes required
- No infrastructure changes required
- No configuration changes required (default is legacy)

---

## Exact Files Requiring Modification

### Must Modify

| File | Change | Risk |
|------|--------|------|
| `workers/llm_worker.py` | Add agent runtime path (conditional on AI_RUNTIME_MODE) | Low - additive |
| `core/config.py` | Add AI_RUNTIME_MODE setting | Low - new setting |
| `agent/runtime.py` | Create runtime adapter | New file |
| `tests/test_ai_native_runtime.py` | Create tests | New file |

### Must NOT Modify

| File | Why |
|------|-----|
| `commerce/pipeline.py` | Commerce authority boundary |
| `commerce/orchestrator.py` | Commerce authority boundary |
| `commerce/decision.py` | Commerce authority boundary |
| `commerce/execution.py` | Commerce authority boundary |
| `commerce/relationship.py` | Relationship authority boundary |
| `core/llm_tools.py` | Existing tool infrastructure |
| `core/llm_provider.py` | Provider abstraction |
| `core/llm_provider_gemini.py` | Gemini provider |
| `core/llm_provider_ollama.py` | Ollama provider |
| `memory/context.py` | Memory infrastructure |
| `memory/context_assembler.py` | Context assembly |
| `db/postgres.py` | Persistence layer |
| `db/redis.py` | Infrastructure |
| `chatbotv2/handlers.py` | Inbound handler |
| `chatbotv2/persistence.py` | Debounce infrastructure |

---

## Duplicate Logic Check

### Agent Loop vs Existing Systems

| Component | Agent Loop | Existing System | Duplicate? |
|-----------|------------|-----------------|------------|
| Context building | `_build_context()` | `build_qwen3_context()` | No - agent uses compact state |
| Tool execution | `_execute_tools()` | `dispatch_tool()` | No - agent wraps existing tools |
| Memory retrieval | `AgentMemory` | `memory/retrieval.py` | No - agent calls existing |
| Commerce decisions | Agent proposes | `decide_commerce_action()` | No - agent proposes, engine decides |
| Product selection | Agent reads | `resolve_commerce_product_with_history()` | No - agent reads, doesn't select |
| Offer creation | Agent cannot | `execute_ppv()` | No - agent cannot create offers |
| Scoring | `score_draft()` | `score_draft()` | No - same scoring |
| Routing | Same routing | Same routing | No - same routing |

**Verdict: No duplicate authority. Agent orchestrates, application decides.**

---

## Conclusion

The integration is safe and straightforward:

1. **Agent replaces ONLY the generation step** (lines 444-469)
2. **All context building remains unchanged** (lines 409-441)
3. **All scoring/routing remains unchanged** (lines 471-608)
4. **All persistence remains unchanged** (lines 383, 610)
5. **All authority boundaries remain unchanged**
6. **No duplicate logic**
7. **No infrastructure changes**
8. **No schema changes**
9. **Default remains legacy mode**
10. **Rollback is immediate**

---

*Audit completed: 2026-08-28*
