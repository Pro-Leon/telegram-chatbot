# Qwen3 Q1 Shadow Forensic Audit

## Current Runtime Call Graph

```
Telegram inbound
    │
    ▼
handlers.handle_incoming_message()  [handlers.py:25]
    │ debounce → enqueue_inbound()
    ▼
llm_worker.run_worker()  [llm_worker.py:577]
    │ read_inbound()
    ▼
llm_worker.process_message()  [llm_worker.py:364]
    │
    ├─► upsert_user()
    ├─► is_user_auto_reply_excluded()
    ├─► resolve_single_application_creator()  [creator context]
    │
    ├─► build_qwen3_context()  [context.py:380]
    │       │
    │       ├─► get_user() + get_user_profile()
    │       ├─► build_qwen3_system_prompt()
    │       ├─► build_llm_context() + render_context()  [commerce facts]
    │       ├─► get_latest_summary_with_age()
    │       └─► get_recent_messages() + trim_to_token_budget()
    │
    ├─► publish_event("ai.generation_started")
    │
    ├─► _try_commerce_draft()  [llm_worker.py:253]
    │       │
    │       ├─► resolve_single_application_creator()
    │       ├─► resolve_commerce_product_with_history()
    │       ├─► CommerceStateRequest()
    │       ├─► resolve_and_run_commerce()
    │       └─► select_commerce_response()
    │
    ├─► [if commerce] draft = selection.commerce_response_text
    ├─► [elif tools]   draft = generate_draft_with_tools()
    ├─► [else]         draft = generate_draft()
    │       │
    │       └─► get_llm_provider() → provider.generate_with_history()
    │
    ├─► score_draft()  [scoring.py:71]
    │       │
    │       ├─► keyword flag detection (hard flags)
    │       └─► provider.generate()  [LLM scoring]
    │
    ├─► [if auto_reply_off] add_to_operator_queue()
    ├─► [if auto_approve]   enqueue_send()
    ├─► [else]              add_to_operator_queue() + notify_operators()
    │
    ├─► publish_event("ai.generation_completed" / "suggestion.created")
    ├─► post_process()  [asyncio.create_task]
    │       ├─► extract_and_update_profile()
    │       └─► maybe_summarize()
    └─► release_user_lock()
```

## Conversational LLM Call Site

**Primary call:** `llm_worker.py:468` → `generate_draft()` or `generate_draft_with_tools()`

`generate_draft()` (line 80):
- Merges system parts from context_messages
- Calls `get_llm_provider().generate_with_history()`
- Provider is selected by `LLM_PROVIDER` env var (currently "gemini")

`generate_draft_with_tools()` (line 115):
- Same context building but adds `TOOL_AUTHORITY_PROMPT`
- Uses Gemini function calling directly (bypasses provider abstraction)
- Falls back to `generate_draft()` when provider doesn't support tools

## Context Construction Path

**Qwen3 compact path:** `memory/context.py:380` → `build_qwen3_context()`

1. `get_user(user_id)` → user dict (first_name, funnel_stage, relationship_state)
2. `get_user_profile(user_id)` → profile dict (age, location, occupation, interests)
3. `build_qwen3_system_prompt(persona, user, profile)` → compressed system prompt (~400 tokens)
4. `build_llm_context(creator_id, user_id)` → commerce context (deterministic facts)
5. `get_latest_summary_with_age(user_id)` → compressed summary (~200 tokens)
6. `build_qwen3_state_context()` → compressed state (STATE/PROFILE/RELATIONSHIP/COMMERCE/SUMMARY)
7. `get_recent_messages(user_id, limit=20)` → last 20 messages
8. `trim_to_token_budget(recent, 800)` → fit within 800 token budget
9. Drop excess assistant turns (keep last 3)

**Total context budget:** ~1600 tokens (system 400 + state 200 + conversation 800 + summary 200)

## Provider Path

`get_llm_provider()` → `core/llm_provider.py:184`
- Reads `LLM_PROVIDER` env var
- Returns `GeminiProvider()` or `OllamaProvider()`
- Both implement `generate_with_history()` interface

**Current production:** Gemini (via `GeminiProvider`)
**Shadow target:** Ollama (via `OllamaProvider(think_mode=False)`)

## Side-Effect Boundaries

| Side Effect | Location | Shadow Access |
|---|---|---|
| `enqueue_send()` | redis.py:65 | MUST NOT touch |
| `add_to_operator_queue()` | postgres.py | MUST NOT touch |
| `extract_and_update_profile()` | memory/profile.py | MUST NOT touch |
| `maybe_summarize()` | memory/summarizer.py | MUST NOT touch |
| `resolve_and_run_commerce()` | commerce/integration.py | MUST NOT touch |
| `select_commerce_response()` | commerce/selection.py | MUST NOT touch |
| `publish_event()` | event_bus.py | MUST NOT touch |
| `dispatch_tool()` | llm_tools.py | MUST NOT touch |
| `release_user_lock()` | redis.py | MUST NOT touch |

## Proposed Shadow Insertion Point

**After:** `build_qwen3_context()` at `llm_worker.py:408`
**Before:** `_try_commerce_draft()` at `llm_worker.py:421`

```
context = await build_qwen3_context(...)
                                          ◄── INSERT SHADOW HERE
# Shadow runs as fire-and-forget asyncio.Task
# Authoritative path continues unmodified
await publish_event("ai.generation_started", ...)
selection = await _try_commerce_draft(...)
```

**Why this point:**
1. Context is already built (reuses production-shaped context)
2. Before any commerce/scoring/routing decisions
3. Fire-and-forget: shadow runs in parallel, never blocks
4. After shadow launch, authoritative path continues unchanged

## Authority Analysis

| Concern | Risk Level | Mitigation |
|---|---|---|
| Shadow modifies context | NONE | Context is read-only, shadow gets a copy |
| Shadow blocks authoritative | LOW | Semaphore + timeout + fire-and-forget |
| Shadow enqueues send | NONE | Shadow runner has no access to redis.enqueue_send |
| Shadow mutates DB | NONE | Shadow runner has no DB access |
| Shadow affects scoring | NONE | Scoring runs on authoritative draft only |
| Shadow affects commerce | NONE | Commerce decision is deterministic, not LLM-driven |
| Shadow leaks to fan | NONE | No path from shadow result to outbound |

## Failure Analysis

| Failure Mode | Impact on Authoritative CRM |
|---|---|
| Ollama timeout | NONE — shadow task abandoned |
| Ollama DNS failure | NONE — exception caught, logged |
| Ollama auth failure | NONE — exception caught, logged |
| Ollama 500/502/503 | NONE — exception caught, logged |
| Shadow evaluation crash | NONE — exception caught, logged |
| Shadow semaphore full | NONE — returns empty result |
| Shadow import failure | NONE — try/except at launch |

## Concurrency Implications

- Shadow runs as separate `asyncio.Task`
- Bounded by `asyncio.Semaphore(max_concurrent=3)`
- Independent timeout (180s default)
- Does not hold user lock (lock is held by `process_message`)
- CPU-bound: Ollama inference is on VPS, not local
- Network: single HTTPS POST per shadow call
- Memory: ~1600 tokens context + 300 tokens max output

**Conclusion:** Shadow insertion at this point is safe. Zero risk to authoritative path.
