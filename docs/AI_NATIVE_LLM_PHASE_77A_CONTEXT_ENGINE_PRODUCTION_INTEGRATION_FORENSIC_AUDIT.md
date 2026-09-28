# PHASE 77A — CONTEXT ENGINE + ONECALL PRODUCTION INTEGRATION FORENSIC AUDIT

**Date:** 2026-09-03
**Scope:** READ-ONLY forensic audit of actual runtime code paths
**Status:** COMPLETE

---

## 1. EXECUTIVE SUMMARY

**The Context Engine is NOT integrated into the production OneCall path.** The production path uses the legacy `build_qwen3_context()` function (`memory/context.py:521`) which constructs context from direct PostgreSQL queries, not from the Context Engine pipeline.

The OneCall path (`llm_path=new`) generates context via `build_one_call_context()` (`core/context_compact.py:37`) which is a **separate compact context builder** that also does NOT use the Context Engine. It reads from the same PG sources but builds a minimal context directly.

RapidFuzz, SentenceTransformer/MiniLM, and hnswlib are NOT in the production message path. The Context Engine pipeline exists as an observational-only module that runs in parallel when `context_engine_observational=True` (default: `False`).

The architecture has **three separate context builders** that do NOT share code:
1. `build_qwen3_context()` — legacy 3-LLM path context
2. `build_one_call_context()` — one-call path context
3. `ContextEngineIntegration.process()` — observational Context Engine (not connected to production)

---

## 2. ACTUAL CURRENT RUNTIME FLOW

```
Telegram Event
    │
    ▼
chatbotv2/handlers.py:26 handle_incoming_message()
    │  - event.get_sender() [Telethon]
    │  - check_rate_limit() [Redis]
    │  - unblacklist_entity() [Redis]
    │  - upsert_user() [PG]
    │  - resolve_single_application_creator() [deterministic]
    │  - save_inbound_message() [PG]
    │  - publish_event("message.created") [Redis Pub/Sub]
    │  - debounce_enqueue() [Redis]
    │
    ▼ (after debounce_window_seconds)
    │
handlers.py:128 _wait_and_process()
    │  - get_debounced_messages() [Redis]
    │  - get_cached_user_persona() / get_user_persona() [Redis/PG]
    │  - get_cached_default_persona() / get_default_persona() [Redis/PG]
    │  - enqueue_inbound() [Redis Stream XADD]
    │
    ▼ (LLM worker reads from Redis Stream)
    │
llm_worker.py:1666 read_inbound() [Redis XREADGROUP]
    │
    ▼
llm_worker.py:431 process_message()
    │
    ├─ resolve_single_application_creator() [deterministic]
    ├─ acquire_user_lock() [Redis SETNX]
    ├─ upsert_user() + is_user_auto_reply_excluded() [parallel PG]
    ├─ get_structured_persona_async() [PG]
    │
    ├─ build_qwen3_context() :509 ◄── LEGACY CONTEXT BUILDER
    │     │  [memory/context.py:521]
    │     ├─ get_user() [PG]
    │     ├─ get_user_profile() [PG]
    │     ├─ get_recent_messages() [PG]
    │     ├─ get_latest_summary_with_age() [PG]
    │     ├─ derive_conversation_state() [deterministic]
    │     ├─ build_qwen3_system_prompt() [deterministic]
    │     ├─ render_compact_persona_block() [deterministic]
    │     ├─ build_llm_context() → render_context() [PG]
    │     ├─ retrieve_relevant_memories() [PG — keyword match]
    │     ├─ get_fan_knowledge() [PG]
    │     ├─ rank_products_by_relevance() [deterministic]
    │     └─ trim_to_token_budget() [deterministic]
    │
    ├─ publish_event("ai.generation_started") [Redis Pub/Sub]
    │
    ├─ observe_context_engine() :538 [OBSERVATIONAL ONLY]
    │     │  [context_engine/worker_integration.py:66]
    │     │  enabled=_settings.context_engine_observational (default=False)
    │     │  When disabled: returns immediately, no work done
    │     │  When enabled:
    │     │    ├─ ContextEngineIntegration.process() [observational]
    │     │    │    ├─ ContextGatherer.gather_all() [7 sources]
    │     │    │    ├─ ContextScorer.score_items()
    │     │    │    ├─ ContextDeduplicator.deduplicate()
    │     │    │    ├─ TokenBudgetManager
    │     │    │    └─ CompactRenderer.render()
    │     │    └─ Returns ContextEngineObservation (telemetry only)
    │     │  Output: NEVER consumed by production path
    │
    ├─ extract_explicit_memories() [deterministic]
    ├─ extract_fan_knowledge() [deterministic]
    ├─ observe_behavioral_signal() [deterministic]
    ├─ Q1 Shadow launch [background, disabled by default]
    │
    ├─ _commerce_signals = None ◄── NO extract_commerce_signals() in outer scope
    │
    ├─ _llm_path check :1036
    │
    ├─ if _llm_path == "new":
    │     │
    │     ├─ one_call_pipeline_with_fallback() :1075
    │     │     │  [core/one_call_pipeline.py:162]
    │     │     ├─ build_one_call_context() ◄── SEPARATE CONTEXT BUILDER
    │     │     │     [core/context_compact.py:37]
    │     │     │     Reads: user, profile, persona, persona_name,
    │     │     │            identity_established, commerce_text,
    │     │     │            summary, summary_age_days,
    │     │     │            conversation_state, recent_messages
    │     │     │     Does NOT use Context Engine
    │     │     ├─ build_commerce_signal_hints() [deterministic]
    │     │     ├─ validate_one_call_context() [deterministic]
    │     │     ├─ provider.generate() ◄── LLM CALL #1 (Qwen structured JSON)
    │     │     │     [core/llm_provider_ollama.py:280]
    │     │     │     Model: qwen3:4b (config.py:90)
    │     │     │     num_ctx=8192, num_predict=400, temperature=0.7
    │     │     ├─ validate_one_call_response() [5-layer Pydantic]
    │     │     └─ validate_draft_quality() [deterministic]
    │     │
    │     ├─ if valid and reply:
    │     │     ├─ _commerce_signals = _one_call_result.signals
    │     │     ├─ _try_commerce_draft() :1111
    │     │     │     ├─ resolve_single_application_creator() [deterministic]
    │     │     │     ├─ resolve_commerce_product_with_history() [PG]
    │     │     │     ├─ resolve_and_run_commerce()
    │     │     │     │     ├─ build_conversation_context() [deterministic]
    │     │     │     │     ├─ signals passed (skip extract_commerce_signals)
    │     │     │     │     ├─ decide_from_signals() [deterministic]
    │     │     │     │     └─ orchestrate_commerce() [deterministic]
    │     │     │     └─ select_commerce_response() [deterministic]
    │     │     └─ If commerce selected: draft = commerce_response_text
    │     │
    │     ├─ else: _llm_path = "legacy"
    │     │
    │     └─ exception: _llm_path = "legacy"
    │
    ├─ if _llm_path == "legacy":
    │     ├─ extract_commerce_signals() ◄── LLM CALL #1 (legacy-only)
    │     ├─ _try_commerce_draft() [with signals]
    │     ├─ generate_draft_with_tools() or generate_draft() ◄── LLM CALL #2
    │     └─ score_draft() ◄── LLM CALL #3 (LLM-based scoring)
    │
    ├─ validate_persona_voice() [deterministic]
    ├─ send/handoff decision [deterministic]
    └─ enqueue_send() or add_to_operator_queue() [Redis]
```

---

## 3. INTENDED RUNTIME FLOW

```
Fan Message
    │
    ▼
AUTHORITATIVE APPLICATION STATE
    │
    ├─ PERSONA STATE (deterministic, HARD_POLICY)
    ├─ FAN/BUSINESS STATE (deterministic, DETERMINISTIC_DERIVATION)
    ├─ CONVERSATION STATE (deterministic, DETERMINISTIC_RULE)
    └─ COMMERCE STATE (deterministic, DETERMINISTIC_RULE)
    │
    ▼
CONTEXT ENGINE
    │
    ├─ RapidFuzz lexical retrieval ◄── NOT IN PRODUCTION
    ├─ SentenceTransformer semantic retrieval ◄── NOT IN PRODUCTION
    ├─ hnswlib vector search ◄── NOT IMPLEMENTED
    ├─ merge / dedup
    ├─ relevance / recency / importance scoring
    └─ hard token budget (2600 tokens)
    │
    ▼
COMPACT QWEN CONTEXT
    │
    ▼
QWEN2.5:3B ONE GENERATION ◄── ACTUALLY qwen3:4b
    │
    ▼
STRUCTURED RESPONSE (OneCallReply)
    │
    ▼
PYDANTIC VALIDATION
    │
    ▼
DETERMINISTIC AUTHORITY
    │
    ├─ COMMERCE / PPV (deterministic)
    ├─ PERSONA VALIDATION (deterministic)
    └─ SAFETY / HANDOFF (deterministic)
    │
    ▼
SEND / HANDOFF
```

**Differences:**
1. Context Engine is observational only, NOT the production context builder
2. Model is qwen3:4b, NOT Qwen2.5:3B
3. Production context is built by `build_qwen3_context()` or `build_one_call_context()`, NOT by Context Engine
4. RapidFuzz, MiniLM, hnswlib are NOT in the production path
5. The Context Engine's 2600-token budget is NOT used by production

---

## 4. ONECALL LLM CALL COUNT PROOF

### A. Normal conversational message (`llm_path=new`)

| Step | File:Line | LLM Call? | Provider | Model |
|------|-----------|-----------|----------|-------|
| extract_commerce_signals | NOT called (moved to legacy) | NO | — | — |
| one_call_generation | one_call_pipeline.py:122 | YES | Ollama | qwen3:4b |
| validate_one_call_response | one_call.py:88 | NO | — | — |
| validate_draft_quality | scoring_deterministic.py:178 | NO | — | — |
| _try_commerce_draft | llm_worker.py:1111 | NO | — | — |

**Total: 1 LLM call**

### B. Commerce-relevant message (`llm_path=new`)

Same as A. Commerce signals are embedded in the one-call JSON output. `_try_commerce_draft()` runs deterministically after generation.

**Total: 1 LLM call**

### C. Explicit PPV purchase request (`llm_path=new`)

Same as A. Commerce execution is deterministic.

**Total: 1 LLM call**

### D. OneCall failure (`llm_path=new` → fallback to legacy)

| Step | File:Line | LLM Call? | Provider | Model |
|------|-----------|-----------|----------|-------|
| one_call_generation fails | one_call_pipeline.py:131 | NO (returns error) | — | — |
| _fallback_3llm_pipeline | one_call_pipeline.py:212 | NO (returns empty) | — | — |
| Worker sets _llm_path = "legacy" | llm_worker.py:1129 | — | — | — |
| extract_commerce_signals | llm_worker.py:1142 | YES | Ollama | qwen3:4b |
| generate_draft_with_tools/generate_draft | llm_worker.py:1189/1207 | YES | Ollama | qwen3:4b |
| score_draft | llm_worker.py:1214 | YES | Ollama | qwen3:4b |

**Total: 4 LLM calls** (1 failed + 3 legacy)

### E. Legacy path (`llm_path=legacy`)

| Step | File:Line | LLM Call? | Provider | Model |
|------|-----------|-----------|----------|-------|
| extract_commerce_signals | llm_worker.py:1142 | YES | Ollama | qwen3:4b |
| generate_draft_with_tools/generate_draft | llm_worker.py:1189/1207 | YES | Ollama | qwen3:4b |
| score_draft | llm_worker.py:1214 | YES | Ollama | qwen3:4b |

**Total: 3 LLM calls**

### F. Agent/tool path

`ai_agent_canary_enabled=False` (config.py:128). Agent is only in legacy branch. Not reachable from one-call path.

**Total: 0 additional LLM calls (agent disabled)**

### LLM Call Count Matrix

| Runtime Path | LLM Calls | Provider | Model | Reason |
|---|---:|---|---|---|
| Normal new | 1 | Ollama | qwen3:4b | one_call_generation only |
| Commerce new | 1 | Ollama | qwen3:4b | one_call_generation + deterministic commerce |
| PPV request new | 1 | Ollama | qwen3:4b | one_call_generation + deterministic commerce |
| OneCall failure | 4 | Ollama | qwen3:4b | 1 failed + 3 legacy |
| Legacy | 3 | Ollama | qwen3:4b | extract + generate + score |
| Agent/tool | 0 | — | — | disabled |

---

## 5. PROVIDER/MODEL PROOF

| Setting | Value | Source | Notes |
|---------|-------|--------|-------|
| `llm_provider` | `"ollama"` | config.py:84 | Active provider |
| `ollama_model` | `"qwen3:4b"` | config.py:90 | Runtime model |
| `ollama_base_url` | `"https://ollama.brestalogistics.co.ke"` | config.py:89 | Remote endpoint |
| `ollama_num_ctx` | `8192` | config.py:94 | Context window |
| `model_name` | `"gemini-flash-latest"` | config.py:25 | Default for `generate_draft` — Ollama ignores this |
| `cheap_model` | `"gemini-flash-latest"` | config.py:26 | Used by `extract_commerce_signals` — Ollama ignores this |
| `max_tokens` | `200` | config.py:29 | Output cap (default for generate_draft) |
| `temperature` | `0.85` | config.py:30 | Sampling temp (default for generate_draft) |
| `llm_path` | `"new"` | config.py:141 | One-call active by default |
| `qwen_shadow_enabled` | `False` | config.py:99 | Shadow disabled |
| `context_engine_observational` | `False` | config.py:136 | Observational disabled |
| `context_engine_canary_mode` | `"disabled"` | config.py:148 | Canary disabled |
| `ai_agent_canary_enabled` | `False` | config.py:128 | Agent disabled |

**CRITICAL:** The model is `qwen3:4b`, NOT Qwen2.5:3B. The Ollama provider (`llm_provider_ollama.py:300-303`) ignores Gemini model name strings and uses `self._model` which comes from `ollama_model`.

---

## 6. CONTEXT ENGINE CALL GRAPH

```
observe_context_engine() [llm_worker.py:538]
    │
    ├─ enabled=False (default): returns immediately, NO WORK DONE
    │
    └─ enabled=True:
        │
        ├─ ContextEngineIntegration.process() [integration.py:148]
        │     │
        │     ├─ ContextGatherer.gather_all() [gatherer.py:876]
        │     │     │
        │     │     ├─ PersonaSource.gather() [gatherer.py:156]
        │     │     │     ├─ get_user_persona() [PG]
        │     │     │     └─ get_structured_persona_async() [PG]
        │     │     │
        │     │     ├─ FanStateSource.gather() [gatherer.py:233]
        │     │     │     ├─ get_user() [PG]
        │     │     │     ├─ derive_relationship_state() [deterministic]
        │     │     │     └─ derive_capability_contract() [deterministic]
        │     │     │
        │     │     ├─ ConversationHistorySource.gather() [gatherer.py:394]
        │     │     │     ├─ get_recent_messages() [PG]
        │     │     │     └─ get_latest_summary() [PG]
        │     │     │
        │     │     ├─ CommerceStateSource.gather() [gatherer.py:484]
        │     │     │     ├─ PG: commerce_offers (purchases)
        │     │     │     ├─ PG: commerce_offers (active offers)
        │     │     │     └─ get_timing_context() [PG]
        │     │     │
        │     │     ├─ MemorySource.gather() [gatherer.py:653]
        │     │     │     ├─ retrieve_relevant_knowledge() [PG]
        │     │     │     └─ get_latest_summary() [PG]
        │     │     │
        │     │     ├─ TemporalSource.gather() [gatherer.py:737]
        │     │     │     └─ retrieve_relevant_knowledge() + temporal_context_for_fan()
        │     │     │
        │     │     └─ EmbeddedKnowledgeSource.gather() [gatherer.py:804]
        │     │           ├─ render_persona_self_block() [deterministic]
        │     │           └─ derive_capability_contract() [deterministic]
        │     │
        │     ├─ ContextAssembler.assemble() [assembler.py:70]
        │     │     ├─ ContextScorer.score_items() [scorer.py:252]
        │     │     ├─ ContextDeduplicator.deduplicate() [dedup.py:150]
        │     │     │     └─ RapidFuzz (optional, observational only)
        │     │     ├─ TokenBudgetManager [budget.py:83]
        │     │     └─ ContextSnapshot
        │     │
        │     └─ CompactRenderer.render() [renderer.py:100]
        │           └─ RenderedContext (observational output)
        │
        └─ Returns ContextEngineObservation (telemetry only)
              Output: NEVER consumed by production path
```

---

## 7. RAPIDFUZZ AUDIT

### Import locations:
1. `context_engine/dedup.py:57-60` — `_are_lexically_similar()`
2. `commerce/unified_intelligence.py:45-50` — `_lexical_scores()`
3. `tests/phase67_forensic.py:124` — test harness
4. `tests/test_phase48_local_intelligence.py:38-77` — test assertions
5. `benchmark_local.py:36` — benchmark script

### Production path analysis:

| Location | Purpose | In Production Path? |
|----------|---------|-------------------|
| `context_engine/dedup.py:57` | Lexical similarity for deduplication | NO — Context Engine is observational only |
| `commerce/unified_intelligence.py:45` | Intent classification lexical evidence | NO — NOT called from `process_message()` |
| Tests/benchmarks | Test assertions | NO |

### Data flow:

```
fan message
   ↓
RapidFuzz (context_engine/dedup.py:57)
   ↓
candidate IDs (within observational Context Engine only)
   ↓
ContextSnapshot (observational)
   ↓
RenderedContext (observational)
   ↓
NEVER reaches production Qwen context
```

**RapidFuzz is NOT part of the production OneCall context.** It exists only in the observational Context Engine deduplication module.

---

## 8. SENTENCE TRANSFORMER / MINILM AUDIT

### Model:
- Model: `all-MiniLM-L6-v2`
- Dimension: 384
- Loading: Lazy singleton via `_load_model()` (`embedding_model.py:42`)
- Warmup: `llm_worker.py:1618-1626` at worker startup

### Usage locations:
1. `commerce/embedding_model.py:62` — `encode_message()` async wrapper
2. `commerce/embedding_model.py:82` — `encode_messages_sync()` batch sync
3. `commerce/unified_intelligence.py:108,180` — lexical+semantic hybrid

### Production path analysis:

| Location | Purpose | In Production Path? |
|----------|---------|-------------------|
| `commerce/unified_intelligence.py:108` | Batch encode reference examples | NO — NOT called from `process_message()` |
| `commerce/unified_intelligence.py:180` | Single message encoding | NO — NOT called from `process_message()` |
| Worker startup | Model warmup | YES — but model is never used in production |

### Does `current message → MiniLM embedding` occur in production?
**NO.** The embedding model loads at worker startup but is only used by `unified_intelligence.py` which is NOT imported or called from `llm_worker.py`.

### Does the embedding result affect the context sent to Qwen?
**NO.** The production context is built by `build_qwen3_context()` or `build_one_call_context()`, neither of which uses embeddings.

---

## 9. HNSWLIB AUDIT

**hnswlib does NOT exist in the codebase.** Zero grep matches across all `.py` files.

The architecture specification calls for hnswlib vector retrieval, but it was never implemented. The `unified_intelligence.py` uses brute-force cosine similarity against ~110 reference vectors.

---

## 10. MEMORY WRITE PATH

```
fan message
    ↓
extract_explicit_memories() [llm_worker.py:588]
    │  [commerce/long_term_memory.py:244]
    │  Deterministic regex-based extraction
    │  No LLM involved
    │
    ↓
add_memory_item() [llm_worker.py:594]
    │  [commerce/long_term_memory.py:79]
    │  PG: update user profile JSON
    │  No embedding generated
    │  No hnswlib indexing
    │
    ↓
Memory persisted to PG (JSON in user profile)
```

### Embedding at write time:
**NO.** Memories are stored as JSON in the user profile. No embeddings are generated at write time.

### hnswlib indexing at write time:
**NO.** hnswlib does not exist.

---

## 11. MEMORY RETRIEVAL PATH

```
current fan message
       ↓
retrieve_relevant_memories() [memory/context.py:756]
    │  [commerce/long_term_memory.py:190]
    │
    ├─ get_long_term_memory() [PG — user profile JSON]
    ├─ Filter expired (deterministic decay)
    ├─ Score relevance:
    │     - topic overlap (token intersection)
    │     - confidence
    │     - recency (days since last_seen)
    │     - importance
    │     - open_loop/commitment boost
    ├─ Sort by score descending
    └─ Return top-N (limit=3) with score > 0.2
```

### Retrieval method:
- Source: PostgreSQL (user profile JSON field)
- Filter: creator_id, user_id
- Scoring: keyword overlap + confidence + recency + importance
- Limit: 3 memories
- Method: **SQL-only** — token overlap, no semantic search, no RapidFuzz, no embeddings

### Does retrieved memory reach Qwen?
YES — via `build_qwen3_context()` at lines 748-761 (legacy) or via `MemorySource` in Context Engine (observational only).

---

## 12. RANKING / RECENCY / IMPORTANCE AUDIT

### Production ranking (memory retrieval):
```python
score = overlap * 0.5 + confidence * 0.3 + recency * 0.2 + importance * 0.1
```
Where:
- `overlap` = token intersection between memory and current topic/threads
- `confidence` = memory confidence (0.0-1.0)
- `recency` = max(0, 1 - days/30)
- `importance` = memory importance (0.0-1.0)

### Context Engine ranking (observational only):
```python
SCORING_WEIGHTS = {
    "source": 0.15,
    "topic": 0.30,
    "recency": 0.20,
    "importance": 0.20,
    "state_relevance": 0.10,
    "authority": 0.05,
}
```
**These weights are NOT used in production.** They exist only in the observational Context Engine.

---

## 13. CONTEXT BUDGET AUDIT

### Production context (`build_qwen3_context`):

| Component | Token Budget | Source |
|-----------|-------------|--------|
| System prompt | 400 tokens | `QWEN3_TOKEN_BUDGET["system"]` |
| State context | 200 tokens | `QWEN3_TOKEN_BUDGET["state"]` |
| Conversation | 800 tokens | `QWEN3_TOKEN_BUDGET["conversation"]` |
| Summary | 200 tokens | `QWEN3_TOKEN_BUDGET["summary"]` |
| Total | ~1600 tokens | — |

### One-call context (`build_one_call_context`):

| Component | Token Budget | Source |
|-----------|-------------|--------|
| System prompt | 350 tokens | `ONE_CALL_TOKEN_BUDGET["system"]` |
| State context | 150 tokens | `ONE_CALL_TOKEN_BUDGET["state"]` |
| Conversation | 600 tokens | `ONE_CALL_TOKEN_BUDGET["conversation"]` |
| Signals hint | 50 tokens | `ONE_CALL_TOKEN_BUDGET["signals_hint"]` |
| Max messages | 8 | `ONE_CALL_MAX_MESSAGES` |
| Total | ~1150 tokens | — |

### Context Engine budget (observational only):

| Component | Token Budget | Source |
|-----------|-------------|--------|
| Total | 2600 tokens | `TOTAL_CONTEXT_BUDGET` |
| System | 400 tokens | `CATEGORY_BUDGETS[SYSTEM]` |
| State | 200 tokens | `CATEGORY_BUDGETS[STATE]` |
| Commerce | 200 tokens | `CATEGORY_BUDGETS[COMMERCE]` |
| Memory | 150 tokens | `CATEGORY_BUDGETS[MEMORY]` |
| Conversation | 800 tokens | `CATEGORY_BUDGETS[CONVERSATION]` |

### Ollama parameters:

| Parameter | Value | Source |
|-----------|-------|--------|
| `num_ctx` | 8192 | config.py:94 |
| `num_predict` | 400 (one-call) / 300 (default) | one_call_pipeline.py:128 / llm_provider_ollama.py:44 |
| `temperature` | 0.7 | one_call_pipeline.py:129 |
| `think` | `False` | llm_provider_ollama.py:179 |

**Both contexts fit well within 8192 num_ctx.**

---

## 14. AUTHORITATIVE STATE AUDIT

### Persona state:
| Field | Source | Authoritative |
|-------|--------|---------------|
| persona text | PG `personas` table | YES (HARD_POLICY) |
| structured persona | `get_structured_persona_async()` | YES |
| behavior block | `derive_persona_behavior_state()` | YES (deterministic) |

### Fan/business state:
| Field | Source | Authoritative |
|-------|--------|---------------|
| funnel_stage | PG `users` table | YES |
| relationship_state | `derive_relationship_state()` | YES (deterministic) |
| purchase_count | PG `commerce_offers` | YES |
| is_blocked | PG `users` table | YES |
| do_not_auto_reply | PG `users` table | YES |

### Conversation state:
| Field | Source | Authoritative |
|-------|--------|---------------|
| current_topic | `derive_conversation_state()` | YES (deterministic) |
| identity_already_established | `derive_conversation_state()` | YES (deterministic) |
| lifecycle | `derive_lifecycle()` | YES (deterministic) |

### Commerce state:
| Field | Source | Authoritative |
|-------|--------|---------------|
| current_offer | PG `commerce_offers` | YES |
| offer_state | PG `commerce_offers` | YES |
| PPV pricing | PG `fangate_products.price_minor` | YES |
| has_active_offer | `build_conversational_commerce_state()` | YES |
| recent_offer_count | `get_timing_context()` | YES |

**The Context Engine cannot overwrite authoritative values.** It reads the same PG sources but its output is observational only.

---

## 15. PERSONA AUDIT

### Persona data flow (production):

```
get_user_persona() / get_default_persona() [PG]
    ↓
persona: str (raw text)
    ↓
build_qwen3_system_prompt() [memory/context.py:678]
    ↓
System message in context
    ↓
render_compact_persona_block() [memory/context.py:693]
    ↓
Additional "CREATOR PERSONA (compact)" system message
    ↓
derive_persona_behavior_state() + render_persona_behavior_block()
    ↓
Behavior block appended to context [llm_worker.py:1026]
```

### Persona validation:
- `validate_persona_voice()` at `llm_worker.py:1240` — DETERMINISTIC
- Checks draft against persona constraints
- Severe violations → flag added, score reduced

### Size:
- Raw persona: varies (typically 500-2000 chars)
- Compact persona block: ~3000 chars (Phase 44C)
- Behavior block: ~200-500 chars
- Total persona tokens reaching Qwen: ~400-500 tokens

---

## 16. COMMERCE / PPV AUDIT

### OneCall commerce path:

```
OneCallReply (one_call.py:38)
    ↓
commerce_signals (embedded in JSON)
    ↓
_try_commerce_draft() [llm_worker.py:1111]
    ↓
resolve_and_run_commerce() [commerce/pipeline.py:459]
    ├─ build_conversation_context() [deterministic]
    ├─ decide_from_signals() [deterministic]
    ├─ resolve_commerce_product_with_history() [PG]
    └─ orchestrate_commerce() [deterministic]
    ↓
select_commerce_response() [deterministic]
```

### Who determines price?
- Price originates from `fangate_products.price_minor` in PostgreSQL
- Product selection is deterministic: `resolve_commerce_product_with_history()`
- **Qwen CANNOT influence pricing** — the system prompt explicitly forbids it

### Who decides whether PPV can be sent?
- `decide_from_signals()` in `commerce/pipeline.py` — DETERMINISTIC
- Checks: relationship state, pressure, risk, cooldown, operator handoff
- LLM signals are advisory only

### What happens when LLM says sales_opportunity=true but deterministic state says no?
- The deterministic authority WINS
- `decide_from_signals()` checks actual commerce state
- If cooldown/ineligible → no offer created regardless of LLM signals

---

## 17. ONECALL CONTRACT AUDIT

### OneCallReply (Pydantic model — `one_call.py:38-67`):

| Field | Type | Required | Default | Validation |
|-------|------|----------|---------|------------|
| `reply` | `str` | YES | — | `min_length=1`, `max_length=2000`, strip, reject empty |
| `commerce_signals` | `CommerceSignals` | NO | `CommerceSignals.low_information()` | Inner validation |
| `confidence` | `float` | NO | `0.5` | `ge=0.0`, `le=1.0` |
| `needs_handoff` | `bool` | NO | `False` | — |

Model config: `extra="forbid"` — rejects unknown fields.

### Schema drift:
The prompt (`ONE_CALL_SYSTEM_PROMPT` at `one_call.py:315`) describes the same fields as the Pydantic schema. No drift detected.

### Validation layers (`one_call.py:88-172`):

1. **JSON parse** — `json.loads()` — malformed → `is_valid=False`
2. **Pydantic schema** — `OneCallReply.model_validate()` — invalid → `is_valid=False`
3. **Safety flags** — `_compute_safety_flags()` — keyword detection
4. **Quality heuristics** — `_compute_quality_heuristics()` — length/formality/generic/repetition
5. **Confidence/handoff override** — safety flags force `needs_handoff=True`

---

## 18. FAILURE / FALLBACK AUDIT

| Failure | Current Behavior | Extra LLM Calls? | Classification |
|---------|-----------------|-------------------|----------------|
| Context Engine fails | Returns `ContextEngineObservation(failed=True)` | 0 | fail-open |
| RapidFuzz fails | Jaccard fallback in dedup | 0 | fail-open |
| MiniLM fails | Returns None, no embedding | 0 | fail-open |
| hnswlib fails | N/A (not implemented) | 0 | N/A |
| OneCall invalid JSON | Returns `is_valid=False` | 0 | fallback |
| OneCall invalid schema | Returns `is_valid=False` | 0 | fallback |
| OneCall empty reply | Returns `is_valid=False` | 0 | fallback |
| `one_call_pipeline_with_fallback` exception | Returns `is_valid=True, reply=""` | 0 | fallback |
| Worker catches exception | `_llm_path = "legacy"` | +3 (legacy) | fallback |
| Worker catches invalid result | `_llm_path = "legacy"` | +3 (legacy) | fallback |

**Does OneCall failure resurrect the old 3-LLM path?**
YES — when `one_call_result.is_valid=False` or exception is caught, `_llm_path` is set to `"legacy"` and the full 3-LLM pipeline executes. Maximum 4 LLM calls on failure.

**Is this behavior desirable?**
It provides rollback capability but violates the "one-call" runtime invariant. A cleaner approach would be to return a safe fallback reply without resurrecting the 3-LLM pipeline.

---

## 19. FEATURE FLAG AUDIT

| Config Name | Default | Env Var | Runtime Consumer | Actual Branch |
|-------------|---------|---------|------------------|---------------|
| `llm_path` | `"new"` | `LLM_PATH` | `llm_worker.py:1036` | `"new"` → one-call, `"legacy"` → 3-LLM |
| `context_engine_observational` | `False` | `CONTEXT_ENGINE_OBSERVATIONAL` | `llm_worker.py:545` | When True: runs Context Engine observationally |
| `context_engine_canary_mode` | `"disabled"` | `CONTEXT_ENGINE_CANARY_MODE` | `worker_integration.py:232` | `"disabled"` or `"observe"` |
| `qwen_shadow_enabled` | `False` | `QWEN_SHADOW_ENABLED` | `llm_worker.py:645` | When True: shadow Qwen runs in parallel |
| `ai_agent_canary_enabled` | `False` | `AI_AGENT_CANARY_ENABLED` | `llm_worker.py:1144` | When True: agent runtime used |
| `autonomy_enabled` | `True` | `AUTONOMY_ENABLED` | `llm_worker.py:342,492` | When False: commerce disabled |

**Production default configuration:**
- `llm_path=new` — one-call path active
- `context_engine_observational=False` — Context Engine NOT running
- `context_engine_canary_mode="disabled"` — Canary NOT running
- `qwen_shadow_enabled=False` — Shadow NOT running
- `ai_agent_canary_enabled=False` — Agent NOT running
- `autonomy_enabled=True` — Commerce active

---

## 20. LEGACY CODE AUDIT

| Function | Reachable from New Path? | Reachable from Legacy? | Reachable on Failure? | Dead? |
|----------|------------------------|----------------------|---------------------|-------|
| `extract_commerce_signals()` | NO (moved to legacy) | YES | YES (legacy fallback) | NO |
| `generate_draft()` | NO | YES | YES (legacy fallback) | NO |
| `generate_commerce_response()` | NO | YES (via commerce) | YES (legacy fallback) | NO |
| `score_draft()` | NO | YES | YES (legacy fallback) | NO |
| `generate_draft_with_tools()` | NO | YES | YES (legacy fallback) | NO |
| `run_agent_runtime()` | NO | YES (if enabled) | NO | NO |
| `build_qwen3_context()` | YES (always called) | YES | YES | NO |
| `build_one_call_context()` | YES (one-call only) | NO | NO | NO |

**Legacy rollback path remains available** via `_llm_path = "legacy"` fallback.

---

## 21. PG / REDIS I/O AUDIT

### PG round trips in `build_qwen3_context`:
1. `get_user()` — 1 PG query
2. `get_user_profile()` — 1 PG query
3. `get_recent_messages()` — 1 PG query
4. `get_latest_summary_with_age()` — 1 PG query
5. `get_structured_persona_async()` — 1 PG query (if not cached)
6. `build_llm_context()` → `render_context()` — 1+ PG queries
7. `retrieve_relevant_memories()` — 1 PG query
8. `get_fan_knowledge()` — 1 PG query
9. `list_valid_products()` — 1 PG query
10. `_get_purchased_product_ids()` — 1 PG query

**Total: ~10 PG round trips** (some parallelized via `asyncio.gather`)

### Additional PG in `process_message`:
- `upsert_user()` — 1 PG query
- `is_user_auto_reply_excluded()` — 1 PG query (parallel with upsert)
- `get_user()` for auth — 1 PG query (legacy path)
- `get_user_profile()` for strategy — 1 PG query

### Context Engine PG (observational only):
When enabled, the Context Engine adds ~7 additional PG queries (one per source). These do NOT affect production when disabled.

### Redis round trips:
~12 per message (rate limit, debounce, lock, inbound, auto-reply, send/queue, ack, release, events).

---

## 22. PERFORMANCE AUDIT

### Known measurements:
- `context_build_ms` — `llm_worker.py:511`
- `generation_latency_ms` — `llm_worker.py:1126`
- `context_engine_ms` — `llm_worker.py:549` (observational only)
- `shadow_latency_ms` — `llm_worker.py:1315` (if enabled)

### Unknown:
- Individual PG query times
- Redis round-trip times
- Per-LLM-call latency breakdown
- Context Engine overhead (when enabled)

### Context Engine overhead (when enabled):
- 7 PG queries (sequential)
- Scoring (deterministic, fast)
- Deduplication (with optional RapidFuzz)
- Budget enforcement (deterministic)
- Rendering (deterministic)
- Estimated: 50-200ms additional latency

---

## 23. FAN ISOLATION AUDIT

### Memory ownership:
- Memories are stored in user profile JSON, keyed by `creator_id`
- `get_long_term_memory()` filters by `creator_id` and `user_id`
- `retrieve_relevant_memories()` filters by `creator_id` and `user_id`

### Retrieval filtering:
- All PG queries include `user_id` parameter
- All memory queries include `creator_id` parameter
- Context Engine sources include `creator_id` and `user_id` in `GathererConfig`

### Cache keys:
- Redis keys are user-scoped (`user_id`)
- No cross-user cache contamination possible

### hnswlib metadata:
- N/A (not implemented)

**Fan isolation is preserved.** A memory from Fan A cannot enter Fan B's Qwen context.

---

## 24. TEST COVERAGE AUDIT

### Context Engine tests:
- `tests/test_phase73_production_context_integration.py` — 1172 lines
  - Tests observational integration
  - Tests config feature flags
  - Tests fail-open behavior
  - **Does NOT prove production integration** (tests mock the Context Engine)

### OneCall tests:
- `tests/test_phase75b_one_call.py` — OneCallReply schema
- `tests/test_phase75c_context_compact.py` — Context compaction
- `tests/test_phase75d_commerce_prompt.py` — Commerce hints
- `tests/test_phase75e_scoring_deterministic.py` — Deterministic scoring
- `tests/test_phase75f_pipeline.py` — Pipeline integration
- `tests/test_phase75_runtime_validation.py` — Runtime validation

### Commerce tests:
- `tests/test_phase_c_relationship.py` — Relationship derivation
- `tests/test_phase_c1b_intelligence.py` — Intelligence tests

### What tests prove:
- ✅ OneCallReply schema is correct
- ✅ Context compaction works
- ✅ Deterministic scoring works
- ✅ Commerce signals are advisory
- ✅ Config flags work as expected
- ❌ Context Engine actually reaches OneCall — NOT TESTED
- ❌ RapidFuzz actually contributes — NOT TESTED
- ❌ MiniLM actually contributes — NOT TESTED
- ❌ hnswlib actually contributes — NOT TESTED (not implemented)
- ❌ Retrieved memories are correctly scoped — NOT TESTED
- ❌ Context budget is enforced in production — NOT TESTED
- ❌ OneCall remains exactly one LLM call — NOT TESTED
- ❌ Commerce signals reach PPV authority — NOT TESTED
- ❌ PPV pricing remains deterministic — NOT TESTED

---

## 25. PRODUCTION INTEGRATION CLASSIFICATION TABLE

| Component | Code Exists | Tested | Production Reachable | Affects Qwen Context | Status |
|-----------|-------------|--------|---------------------|---------------------|--------|
| RapidFuzz | YES | YES (tests) | NO (observational only) | NO | NOT INTEGRATED |
| MiniLM | YES | YES (tests) | NO (not called from production) | NO | NOT INTEGRATED |
| hnswlib | NO | NO | NO | NO | NOT IMPLEMENTED |
| Memory ranking | YES | YES (tests) | YES (SQL-based) | YES | PARTIAL |
| Context budget | YES | YES (tests) | YES (per-component) | YES | PASS |
| OneCall | YES | YES (tests) | YES (llm_path=new) | YES | PASS |
| Commerce signals | YES | YES (tests) | YES (embedded in OneCall) | YES | PASS |
| PPV authority | YES | YES (tests) | YES (deterministic) | NO (authoritative) | PASS |
| Context Engine | YES | YES (tests) | NO (observational, disabled) | NO | NOT INTEGRATED |
| Persona validation | YES | YES (tests) | YES (deterministic) | NO (authoritative) | PASS |
| Safety scoring | YES | YES (tests) | YES (deterministic) | NO (authoritative) | PASS |

---

## 26. GAP LIST

### P0 — Prevents safe production use:
None identified for the current one-call path. The one-call path works as designed with 1 LLM call.

### P1 — High-value correctness/performance issue:

1. **Context Engine is NOT integrated into production.** The Context Engine pipeline exists but is observational only. The production path uses `build_qwen3_context()` / `build_one_call_context()` which are separate context builders. The Context Engine's RapidFuzz + MiniLM + scoring architecture is not connected.

2. **Model mismatch.** Architecture spec says Qwen2.5:3B but runtime uses qwen3:4b (`config.py:90`). The one_call_pipeline.py docstring says "Qwen2.5" but the actual model is qwen3:4b.

3. **OneCall failure resurrects 3-LLM pipeline.** When one-call fails, `_llm_path` is set to `"legacy"` and the full 3-LLM pipeline executes (4 total LLM calls). This violates the one-call runtime invariant.

### P2 — Optimization/integration gap:

4. **Three separate context builders.** `build_qwen3_context()`, `build_one_call_context()`, and `ContextEngineIntegration.process()` are independent implementations that don't share code. This creates maintenance burden and inconsistency risk.

5. **Duplicate PG queries.** `get_user()` and `get_user_profile()` are called in both `build_qwen3_context()` and `process_message()` (lines 719-720). Phase 74B optimized some of these but duplicates remain.

6. **Context Engine PG overhead.** When enabled, the Context Engine adds ~7 additional PG queries per message. This needs measurement before activation.

### P3 — Cleanup/documentation:

7. **Docstring mismatch.** `one_call_pipeline.py` docstring says "Qwen2.5" but model is qwen3:4b.

8. **Unused embedding model warmup.** Worker warms up MiniLM at startup (`llm_worker.py:1618-1626`) but the model is never used in production.

---

## 27. EXACT FILES/FUNCTIONS INVOLVED

### Production path (active):
- `chatbotv2/handlers.py` — Telegram handler
- `workers/llm_worker.py` — Main worker, `process_message()`
- `memory/context.py` — `build_qwen3_context()` (legacy context builder)
- `core/context_compact.py` — `build_one_call_context()` (one-call context builder)
- `core/one_call_pipeline.py` — `one_call_generation()`, `one_call_pipeline_with_fallback()`
- `core/one_call.py` — `OneCallReply`, `validate_one_call_response()`
- `core/scoring_deterministic.py` — `validate_draft_quality()`
- `core/commerce_prompt.py` — `build_commerce_signal_hints()`
- `core/llm_provider_ollama.py` — Ollama provider
- `core/config.py` — Settings
- `commerce/long_term_memory.py` — Memory extraction/retrieval
- `commerce/fan_knowledge.py` — Fan knowledge
- `commerce/pipeline.py` — Commerce execution
- `commerce/persona_validation.py` — Persona validation

### Observational only (NOT in production):
- `context_engine/worker_integration.py` — `observe_context_engine()`
- `context_engine/integration.py` — `ContextEngineIntegration`
- `context_engine/gatherer.py` — 7 data sources
- `context_engine/scorer.py` — Relevance scoring
- `context_engine/dedup.py` — Deduplication (uses RapidFuzz)
- `context_engine/budget.py` — Token budget
- `context_engine/assembler.py` — Assembly
- `context_engine/renderer.py` — Rendering
- `context_engine/new_path.py` — New path (not connected to production)
- `context_engine/canary_observer.py` — A/B canary (disabled)
- `commerce/embedding_model.py` — SentenceTransformer (not called from production)
- `commerce/unified_intelligence.py` — RapidFuzz+MiniLM hybrid (not called from production)

---

## 28. RECOMMENDED NEXT PHASE

### Stage B scope:

1. **Integrate Context Engine into OneCall context.** Replace `build_one_call_context()` with Context Engine output. This requires:
   - Enabling Context Engine by default
   - Connecting Context Engine output to OneCall pipeline
   - Measuring PG overhead
   - Ensuring Context Engine budget (2600 tokens) fits within num_ctx (8192)

2. **Implement hnswlib or remove from spec.** The architecture calls for hnswlib but it was never implemented. Either implement it or update the spec.

3. **Align model name.** Either update config to use Qwen2.5:3B or update architecture spec to match qwen3:4b.

4. **Harden OneCall failure.** Consider returning a safe fallback reply instead of resurrecting the 3-LLM pipeline.

5. **Remove duplicate PG queries.** Eliminate redundant `get_user()` / `get_user_profile()` calls between `build_qwen3_context()` and `process_message()`.

---

## 29. EXPLICIT STATEMENT OF WHAT WAS NOT CHANGED

**NO production code was modified in this audit.**
- NO changes to `workers/llm_worker.py`
- NO changes to `context_engine/`
- NO changes to `commerce/`
- NO changes to `core/config.py`
- NO changes to Redis data
- NO changes to PostgreSQL data
- NO changes to tests
- NO changes to deployment configuration
- NO new migrations
- NO library installations

This is a READ-ONLY forensic audit.

---

## 30. FINAL VERDICT

```
PHASE 77A VERDICT

ONECALL RUNTIME: PASS (1 LLM call, working correctly)
ONE LLM CALL GUARANTEE: PASS (with llm_path=new, normal path)
QWEN2.5:3B: FAIL (model is qwen3:4b, not Qwen2.5)
CONTEXT ENGINE INTEGRATION: FAIL (observational only, NOT in production path)
RAPIDFUZZ PRODUCTION RETRIEVAL: FAIL (not in production path)
MINILM PRODUCTION RETRIEVAL: FAIL (not in production path)
HNSWLIB PRODUCTION RETRIEVAL: FAIL (not implemented)
MEMORY ISOLATION: PASS (creator-scoped, user-scoped)
CONTEXT BUDGET: PASS (both paths fit within num_ctx)
PERSONA PRESERVATION: PASS (compact persona, behavior block, deterministic validation)
COMMERCE/PPV AUTHORITY: PASS (deterministic, LLM cannot override)
ONECALL STRUCTURED CONTRACT: PASS (Pydantic schema matches prompt)
FAILURE SAFETY: PARTIAL (fail-open, but failure resurrects 3-LLM pipeline)
LEGACY ROLLBACK: PASS (legacy path remains available)

P0: 0
P1: 3
P2: 3
P3: 2

PRODUCTION CHANGES: NO
SCHEMA CHANGES: NO
MIGRATIONS: NO
NEW LLM CALLS: NO
NEW WORKERS: NO
NEW QUEUES: NO
ARCHITECTURE REDESIGN: NO

FINAL VERDICT:
CONDITIONALLY READY

The one-call path works correctly with 1 LLM call. However, the Context
Engine architecture (RapidFuzz + MiniLM + hnswlib) is NOT integrated into
production. The production path uses legacy context builders, not the
Context Engine. To activate the intended architecture, Stage B must:

1. Connect Context Engine output to OneCall pipeline
2. Implement hnswlib or remove from spec
3. Align model name (Qwen2.5 vs qwen3:4b)
4. Harden OneCall failure path

NEXT PHASE:
Stage B: Context Engine → OneCall integration
- Replace build_one_call_context() with Context Engine output
- Enable Context Engine by default
- Measure and optimize PG overhead
- Implement hnswlib vector retrieval
- Update model to Qwen2.5:3B or update spec
```
