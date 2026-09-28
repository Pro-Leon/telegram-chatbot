# PHASE 76 — ONE-CALL + CONTEXT ENGINE PRODUCTION FORENSIC AUDIT

**Date:** 2026-09-02
**Scope:** Read-only forensic verification of actual runtime code paths
**Status:** COMPLETE

---

## 1. EXECUTIVE SUMMARY

**The codebase does NOT implement a true one-call pipeline in production.** Despite the Phase 75 report claiming a single LLM call, the production runtime makes **2 synchronous LLM calls** for every normal fan message when `llm_path=new`:

1. `extract_commerce_signals()` at `llm_worker.py:662-663` — unconditional, runs BEFORE the one-call branch
2. `one_call_generation()` at `one_call_pipeline.py:122-130` — the actual one-call generation

The root cause: `extract_commerce_signals()` runs at line 662 **outside** the `_llm_path` branch. It executes unconditionally for both `new` and `legacy` paths. This was never removed when the one-call path was integrated.

Additionally, the Context Engine is **observational only** (disabled by default). It does NOT control the context sent to Qwen. RapidFuzz is used only for deduplication, not retrieval. SentenceTransformers loads for local intent classification but is NOT used in the Context Engine retrieval path. **hnswlib does not exist anywhere in the codebase.**

---

## 2. ACTUAL INBOUND MESSAGE CALL GRAPH

```
Telegram event
    ↓
chatbotv2/handlers.py:26 handle_incoming_message()
    ↓
save_inbound_message() [PostgreSQL]
    ↓
debounce_enqueue() [Redis]
    ↓
_wait_and_process() [debounce window]
    ↓
get_debounced_messages() [Redis]
    ↓
enqueue_inbound() [Redis Streams]
    ↓
workers/llm_worker.py:1666 read_inbound() [Redis XREADGROUP]
    ↓
process_message() [line 431]
    ├─ resolve_single_application_creator()
    ├─ acquire_user_lock() [Redis]
    ├─ upsert_user() + is_user_auto_reply_excluded() [parallel PG]
    ├─ get_structured_persona_async() [PG]
    ├─ build_qwen3_context() [PG + tiktoken] ← BUILDS FULL CONTEXT
    ├─ publish_event("ai.generation_started") [Redis Pub/Sub]
    ├─ observe_context_engine() ← OBSERVATIONAL ONLY, disabled by default
    ├─ extract_explicit_memories() [deterministic]
    ├─ extract_fan_knowledge() [deterministic]
    ├─ observe_behavioral_signal() [deterministic]
    ├─ Q1 Shadow launch [background, disabled]
    ├─ extract_commerce_signals() ← LLM CALL #1 (UNCONDITIONAL)
    ├─ resolve_open_loop() [deterministic]
    ├─ build_conversational_commerce_state() [deterministic]
    ├─ derive_persona_behavior_state() [deterministic]
    ├─ _llm_path check (line 1043)
    │
    ├─ if _llm_path == "new":
    │   ├─ one_call_pipeline_with_fallback()
    │   │   ├─ build_one_call_context() [compact, ~1150 tokens]
    │   │   ├─ build_commerce_signal_hints() [deterministic]
    │   │   ├─ validate_one_call_context() [deterministic]
    │   │   ├─ provider.generate() ← LLM CALL #2 (Qwen2.5 structured JSON)
    │   │   ├─ validate_one_call_response() [5-layer Pydantic]
    │   │   └─ validate_draft_quality() [deterministic]
    │   ├─ if invalid → _llm_path = "legacy" (FALLBACK)
    │
    ├─ if _llm_path == "legacy":
    │   ├─ _try_commerce_draft() → resolve_and_run_commerce()
    │   │   └─ extract_commerce_signals() [LLM #1 — but already called above!]
    │   ├─ generate_draft_with_tools() or generate_draft() ← LLM #2
    │   └─ score_draft() ← LLM #3
    │
    ├─ validate_persona_voice() [deterministic]
    ├─ send/handoff decision [deterministic]
    ├─ enqueue_send() or add_to_operator_queue() [Redis]
    └─ post_process() [background: profile extraction, summarization]
```

---

## 3. ACTUAL LLM CALL COUNT

### Normal fan message with `llm_path=new`:

| # | Function | Provider | Model | Purpose | Line |
|---|----------|----------|-------|---------|------|
| 1 | `extract_commerce_signals()` | Ollama | `gemini-flash-latest` (cheap_model) | Commerce signal extraction | `llm_worker.py:662` |
| 2 | `one_call_generation()` → `provider.generate()` | Ollama | `qwen3:4b` (via `model_name`) | One-call structured JSON response | `one_call_pipeline.py:122` |

**Total: 2 synchronous LLM calls. NOT 1.**

### Normal fan message with `llm_path=legacy`:

| # | Function | Provider | Model | Purpose | Line |
|---|----------|----------|-------|---------|------|
| 1 | `extract_commerce_signals()` | Ollama | `gemini-flash-latest` | Commerce signal extraction | `llm_worker.py:662` |
| 2 | `generate_draft_with_tools()` or `generate_draft()` | Ollama | `qwen3:4b` | Draft generation | `llm_worker.py:1189` or `:1207` |
| 3 | `score_draft()` | Ollama | `gemini-flash-latest` | Quality scoring (LLM-based) | `llm_worker.py:1214` |

**Total: 3 synchronous LLM calls.**

---

## 4. ONE-CALL ROUTING VERIFICATION

**Configuration:**
- `core/config.py:141`: `llm_path: str = "new"` — default is `"new"`
- `workers/llm_worker.py:1043`: `_llm_path = getattr(_settings, "llm_path", "legacy")`

**Branch logic:**
```python
# Line 1043-1128
_llm_path = getattr(_settings, "llm_path", "legacy")

if _llm_path == "new":
    # ONE-CALL PATH
    try:
        _one_call_result = await one_call_pipeline_with_fallback(...)
        if _one_call_result.is_valid and _one_call_result.reply:
            draft = _one_call_result.reply
            score = _one_call_result.quality_score
            flags = list(_one_call_result.safety_flags) + list(_one_call_result.quality_flags)
        else:
            _llm_path = "legacy"  # FALLBACK on invalid result
    except Exception:
        _llm_path = "legacy"  # FALLBACK on exception

if _llm_path == "legacy":
    # LEGACY 3-LLM PATH
    selection = await _try_commerce_draft(...)
    draft = await generate_draft_with_tools(...) or generate_draft(...)
    score, flags = await score_draft(...)
```

**FINDING:** The one-call branch exists and is reachable. However, `extract_commerce_signals()` at line 662 runs BEFORE this branch, making the "one-call" actually 2 calls.

**Silent fallback:** Yes. If `one_call_pipeline_with_fallback` returns invalid result or raises, `_llm_path` is reassigned to `"legacy"` and the full 3-LLM path executes. This means a malformed OneCall response triggers the legacy pipeline (3 more LLM calls).

---

## 5. QWEN2.5 PROVIDER/MODEL VERIFICATION

| Setting | Value | Source |
|---------|-------|--------|
| Model name | `qwen3:4b` | `core/config.py:90` |
| Provider | `ollama` | `core/config.py:84` |
| Endpoint | `https://ollama.brestalogistics.co.ke` | `core/config.py:89` |
| num_ctx | `8192` | `core/config.py:94` |
| num_predict | `300` (default) | `llm_provider_ollama.py:44` |
| Temperature | `0.7` | `llm_provider_ollama.py:183` |
| think_mode | `False` | `llm_provider_ollama.py:79` |
| Fallback provider | Gemini (when `gemini_fallback_enabled=True`) | `llm_provider_ollama.py:198` |

**CRITICAL FINDING:** The model is **Qwen3:4b**, NOT Qwen2.5 as specified in the architecture. `core/config.py:90` says `ollama_model: str = "qwen3:4b"`. The one-call system prompt at `one_call.py:315` says "Qwen2.5" but the actual runtime model is Qwen3:4b.

The `model_name` setting at `core/config.py:25` is `"gemini-flash-latest"` (used as default for `generate_draft` and `extract_commerce_signals`), but the Ollama provider ignores Gemini model names and uses `ollama_model` (`qwen3:4b`).

---

## 6. CONTEXT ENGINE INTEGRATION

**Status: OBSERVATIONAL ONLY — NOT ACTIVE IN PRODUCTION**

| Component | Status | Details |
|-----------|--------|---------|
| Feature flag | `context_engine_observational=False` | `core/config.py:136` |
| Default | `False` | Disabled by default |
| Integration | `observe_context_engine()` | `llm_worker.py:538-558` |
| Output used | NO | Only telemetry metrics recorded |
| Canary | `context_engine_canary_mode="disabled"` | `core/config.py:148` |

The Context Engine pipeline (gather → score → dedup → budget → assemble → render) is fully implemented in `context_engine/integration.py`. It runs against real production data sources (7 data sources in `context_engine/gatherer.py`). However, its output is never consumed by the production context sent to Qwen.

**The actual context sent to Qwen is built by `build_qwen3_context()`** at `memory/context.py`, which uses the legacy context building path (system prompt + profile + summary + commerce + recent messages).

---

## 7. RAPIDFUZZ VERIFICATION

**Usage locations:**
1. `context_engine/dedup.py:57-61` — `_are_lexically_similar()` for context deduplication
2. `commerce/unified_intelligence.py:45-57` — `_lexical_scores()` for intent classification
3. `commerce/unified_intelligence.py:157-169` — lexical evidence in `analyze_message()`

**Is RapidFuzz used for retrieval?** NO. It is used for:
- Context deduplication (within Context Engine assembly, which is observational only)
- Local intent classification (in `unified_intelligence.py`, which is NOT called from `process_message()`)

**Is it in the production message path?** The Context Engine uses it in dedup, but the Context Engine is observational only. The `unified_intelligence.py` module is NOT imported or called from `llm_worker.py`. RapidFuzz is NOT in the production fan message retrieval path.

---

## 8. SENTENCE TRANSFORMER VERIFICATION

| Component | Status |
|-----------|--------|
| Module | `commerce/embedding_model.py` |
| Model | `all-MiniLM-L6-v2` |
| Dimension | 384 |
| Loading | Lazy singleton via `_load_model()` |
| Warmup | `llm_worker.py:1618-1626` at worker startup |
| Usage | `commerce/unified_intelligence.py:180-181` — `encode_message()` |

**Is the embedding model used in the production message path?** NO. It is used by `unified_intelligence.py` for local intent classification. This module is NOT called from `process_message()`. The Context Engine's gatherer does NOT use embeddings.

**Does `current message → MiniLM embedding` occur?** NO. The production path does not generate embeddings for incoming messages.

---

## 9. HNSWLIB VERIFICATION

**hnswlib is NOT present anywhere in the codebase.**

Grep for `hnswlib` across all `.py` files: **0 matches.**

The architecture specifies hnswlib for vector retrieval, but it was never implemented. The `unified_intelligence.py` uses brute-force cosine similarity against ~110 reference vectors (line 186-188), not hnswlib.

---

## 10. HYBRID RETRIEVAL VERIFICATION

**The intended retrieval flow is NOT implemented.**

Actual flow:
```
Fan message
    ↓
build_qwen3_context() [legacy path]
    ├─ get_user() [PG]
    ├─ get_user_profile() [PG]
    ├─ get_latest_summary_with_age() [PG]
    ├─ get_recent_messages() [PG]
    └─ retrieve_relevant_history() [PG — keyword trigger only]
```

There is NO:
- RapidFuzz lexical retrieval for memories
- MiniLM semantic retrieval for memories
- hnswlib vector search
- Merge/dedup of retrieval candidates
- Relevance ranking with semantic + recency + importance weights

The `retrieve_relevant_history()` function at `memory/retrieval.py` uses keyword triggers (`RETRIEVAL_TRIGGERS` at `memory/context.py:58-70`) to decide whether to retrieve old messages, but this is a simple keyword match, not semantic retrieval.

---

## 11. CONTEXT COMPACTION VERIFICATION

**Legacy path (build_qwen3_context):**
- Token budget: `system=400, state=200, conversation=800, summary=200` (QWEN3_TOKEN_BUDGET)
- Recent messages: bounded by `trim_to_token_budget()`
- Output: list of message dicts sent to Qwen

**One-call path (build_one_call_context):**
- Token budget: `system=350, state=150, conversation=600, signals_hint=50`
- Max messages: 8 (`ONE_CALL_MAX_MESSAGES`)
- Max assistant turns: 3
- Separate from legacy context — builds its own compact context

**The ~19k-character persona problem:** The legacy `build_qwen3_context` includes the full persona text. The one-call path uses `_build_compact_system_prompt()` which compresses persona into ~350 tokens. However, the one-call path still receives the full `context` from `build_qwen3_context()` (line 1091: `recent_messages=context`), meaning the full legacy context is available even though the compact context is built separately.

---

## 12. CONTEXT BUDGET

| Parameter | Value | Source |
|-----------|-------|--------|
| Ollama num_ctx | 8192 | `core/config.py:94` |
| One-call system budget | 350 tokens | `context_compact.py:27` |
| One-call state budget | 150 tokens | `context_compact.py:28` |
| One-call conversation budget | 600 tokens | `context_compact.py:29` |
| One-call signals budget | 50 tokens | `context_compact.py:30` |
| One-call max messages | 8 | `context_compact.py:34` |
| Max output tokens | 300 (default) | `llm_provider_ollama.py:44` |
| Max output tokens (one-call) | 400 | `one_call_pipeline.py:128` |

Total one-call context: ~1150 tokens. Well within 8192 num_ctx.

---

## 13. AUTHORITATIVE STATE

| Data | Source | Function | Authoritative | Sent to Qwen |
|------|--------|----------|---------------|--------------|
| Persona | PG | `get_structured_persona_async()` | YES (HARD_POLICY) | YES (system prompt) |
| Fan profile | PG | `get_user_profile()` | YES | YES (compact state) |
| Relationship state | Derived | `derive_relationship_state()` | YES (deterministic) | YES (compact state) |
| Conversation state | Derived | `derive_conversation_state()` | YES (deterministic) | YES (compact state) |
| Commerce state | PG | `build_conversational_commerce_state()` | YES | YES (commerce hints) |
| Subscription/payment | PG | Direct queries | YES | NO (not in context) |
| PPV pricing | PG | `resolve_commerce_product_with_history()` | YES | NO (LLM cannot set price) |
| Access state | PG | Direct queries | YES | NO |
| Cooldowns | PG | `get_timing_context()` | YES | NO |
| Offers | PG | Direct queries | YES | NO |

**PPV pricing authority:** Deterministic. Price originates from `fangate_products.price_minor` in PostgreSQL. The LLM never sets or modifies price. One-call response contains `commerce_signals` but these are advisory — the actual commerce execution in `commerce/pipeline.py` uses `decide_from_signals()` which is deterministic.

---

## 14. COMMERCE SIGNAL FLOW

### One-call path:
```
extract_commerce_signals() [LLM #1 — UNCONDITIONAL]
    ↓
CommerceSignals object
    ↓
one_call_pipeline_with_fallback()
    ├─ commerce_text built from signals (line 1076-1080)
    ├─ build_commerce_signal_hints() adds hints to context
    ├─ Qwen generates structured JSON with embedded commerce_signals
    └─ validate_one_call_response() extracts signals from Qwen output
```

**Are one-call commerce signals passed to the commerce pipeline?** YES, but only the pre-extracted `_commerce_signals` from `extract_commerce_signals()`. The one-call result's `signals` are only used if `_commerce_signals is None` (line 1106-1107). Since `_commerce_signals` is always set (line 662), the one-call signals are effectively ignored.

**Commerce signal extraction runs TWICE:** Once at line 662 (unconditional), and potentially again inside `resolve_and_run_commerce()` if `_try_commerce_draft` is called (legacy path, line 480 in `pipeline.py`).

---

## 15. PPV OPPORTUNITY IDENTIFICATION

In the one-call path, PPV opportunities are identified by:
1. `extract_commerce_signals()` identifies purchase intent (LLM #1)
2. `_try_commerce_draft()` in legacy path runs `resolve_and_run_commerce()` which calls `decide_from_signals()`
3. In one-call path, `_try_commerce_draft()` is NOT called (line 1128-1207 is legacy-only)
4. **PPV execution in one-call path:** Falls through to persona validation and send/handoff decision. The one-call path does NOT trigger commerce execution directly — it only generates the reply and signals.

**FINDING:** In the one-call path, commerce execution (PPV creation, offer sending) does NOT happen. The one-call path generates a reply and signals, but the commerce pipeline (`resolve_and_run_commerce`) is only called in the legacy path via `_try_commerce_draft()`.

---

## 16. PPV PRICING AUTHORITY

| Step | Location | Authority |
|------|----------|-----------|
| Product selection | `commerce/product_selection.py` | Deterministic (PG query) |
| Price origin | `fangate_products.price_minor` | Deterministic (PG) |
| Price validation | `commerce/execution.py` | Deterministic |
| Price in Qwen output | Forbidden by system prompt | LLM cannot set price |
| One-call commerce_signals | Advisory only | LLM cannot authorize |

**Qwen CANNOT influence pricing.** The one-call system prompt explicitly says "NEVER include price, payment, or access details in your reply" (`one_call.py:345`). The deterministic commerce engine handles all pricing.

---

## 17. PPV PREPARATION/SEND FLOW

```
process_message()
    ↓
draft + score + flags determined
    ↓
if auto_reply_on AND score >= 0.80 AND no flags:
    enqueue_send() [Redis Stream]
    ↓
send_worker reads from Redis Stream
    ↓
Telethon sends message
```

**In one-call path:** PPV is NOT prepared. The one-call path only generates conversational replies. Commerce execution (PPV creation, offer links) happens only in the legacy path via `_try_commerce_draft()` → `resolve_and_run_commerce()`.

---

## 18. STRUCTURED OUTPUT VALIDATION

**One-call validation layers (one_call.py:88-172):**
1. JSON parse (`json.loads`)
2. Pydantic schema (`OneCallReply.model_validate`)
3. Deterministic safety flags (`_compute_safety_flags`)
4. Quality heuristics (`_compute_quality_heuristics`)
5. Confidence/handoff override

**OneCallReply schema (one_call.py:38-58):**
- `reply: str` (min_length=1, max_length=2000)
- `commerce_signals: CommerceSignals` (default: low_information)
- `confidence: float` (0.0-1.0)
- `needs_handoff: bool`

**Malformed output causes:** Returns `OneCallResult(is_valid=False)`, which triggers fallback to legacy path (line 1118-1119 in llm_worker.py). This does NOT cause additional LLM calls in the one-call path itself, but the legacy fallback adds 2 more LLM calls.

---

## 19. DETERMINISTIC POST-QWEN AUTHORITY

After Qwen returns in the one-call path:

| Check | Location | Authority |
|-------|----------|-----------|
| Persona validation | `commerce/persona_validation.py:1240` | Deterministic |
| Quality scoring | `core/scoring_deterministic.py` | Deterministic |
| Safety flags | `core/one_call.py:175` | Deterministic (keyword) |
| Auto-approve threshold | `llm_worker.py:1352` | Deterministic |
| Operator queue routing | `llm_worker.py:1381` | Deterministic |

**Qwen output is advisory.** All downstream decisions are deterministic.

---

## 20. PHASE 74B OPTIMIZATION VERIFICATION

| Optimization | Status | Evidence |
|-------------|--------|----------|
| B1: Profile reuse | PARTIAL | `get_last_profile()` used for commerce (line 713-714), but `get_user_profile()` still called separately for strategy learning (line 1465) |
| B2: Dead reads eliminated | YES | `_cached_profile_for_commerce` from `get_last_profile()` avoids duplicate PG query |
| B3: Independent PG parallelized | YES | `asyncio.gather(_upsert_coro, _auto_reply_coro)` at line 483; `asyncio.gather(_timing_coro, _behavioral_coro)` at line 810 |
| B4: Redis publish batching | YES | `publish_events_batch()` at line 1418 |
| B5: orjson usage | NOT FOUND | No orjson imports found in production code |

---

## 21. BACKGROUND PROCESSING

| Task | Location | Latency Impact |
|------|----------|---------------|
| Profile extraction | `post_process()` at line 1582 | Background (asyncio.create_task) |
| Memory summarization | `maybe_summarize()` | Background |
| Strategy learning | `update_strategy_evidence()` at line 1508 | Synchronous (within process_message) |
| Behavioral signals | `observe_behavioral_signal()` at line 630 | Synchronous |
| Experiment tracking | `persist_exposure()` at line 772 | Synchronous |

**All LLM calls in the main path are synchronous and user-visible.** Background tasks are deterministic (no LLM).

---

## 22. REDIS/DELIVERY VERIFICATION

| Component | Status | Evidence |
|-----------|--------|----------|
| Inbound XADD | INTACT | `enqueue_inbound()` at `handlers.py:160` |
| XREADGROUP | INTACT | `read_inbound()` at `llm_worker.py:1666` |
| Consumer group | INTACT | `ensure_consumer_group()` at `llm_worker.py:1607` |
| Send stream | INTACT | `enqueue_send()` at `llm_worker.py:1353` |
| XACK | INTACT | `ack_inbound()` at `llm_worker.py:1686` |
| XAUTOCLAIM | INTACT | `requeue_stalled_messages()` at `llm_worker.py:1656` |
| DLQ | INTACT | `move_to_dlq()` at `llm_worker.py:1690` |
| Deduplication | INTACT | `dedup_id` hash at `llm_worker.py:1304` |

---

## 23. RESTART/ACK/XAUTOCLAIM SAFETY

| Scenario | Behavior |
|----------|----------|
| Worker crashes before Qwen | Lock held until TTL (`user_lock_ttl=60s`), then XAUTOCLAIM reclaims |
| Worker crashes after Qwen | Draft lost, message reclaimed by XAUTOCLAIM, reprocessed |
| Qwen returns malformed | Falls back to legacy 3-LLM path (2 additional LLM calls) |
| Qwen times out | Exception caught at line 1121, falls back to legacy |
| Redis operation fails | Exception propagated, message moved to DLQ |
| Send enqueue fails | Generation events published but message not sent — potential silent failure |
| Send worker crashes | Messages remain in Redis Stream, reclaimed by XAUTOCLAIM |

**ACK ordering:** `ack_inbound()` is called AFTER `process_message()` completes (line 1686), which is correct. Lock release is in `finally` block (line 1697).

---

## 24. LEGACY PATH INVENTORY

| Function | Location | Status | Classification |
|----------|----------|--------|---------------|
| `extract_commerce_signals()` | `commerce/deepseek.py:170` | ACTIVE | Called unconditionally |
| `generate_draft()` | `workers/llm_worker.py:83` | ACTIVE (legacy fallback) | FALLBACK |
| `generate_draft_with_tools()` | `workers/llm_worker.py:156` | ACTIVE (legacy fallback) | FALLBACK |
| `score_draft()` | `core/scoring.py:81` | ACTIVE (legacy fallback) | FALLBACK |
| `_try_commerce_draft()` | `workers/llm_worker.py:306` | ACTIVE (legacy path only) | FALLBACK |
| `resolve_and_run_commerce()` | `commerce/pipeline.py:460` | ACTIVE (legacy path only) | FALLBACK |
| `run_agent_runtime()` | `agent/runtime.py` | ACTIVE (canary only) | CANARY |

---

## 25. RUNTIME CALL MATRIX

| Scenario | LLM Calls | Provider | Path |
|----------|-----------|----------|------|
| Normal fan message (`new`) | 2 | Ollama | extract_commerce + one_call |
| Normal fan message (`legacy`) | 3 | Ollama | extract_commerce + generate_draft + score_draft |
| OneCall malformed | 3 | Ollama | extract_commerce + one_call(fail) + legacy(generate+score) |
| OneCall timeout | 3 | Ollama | extract_commerce + one_call(fail) + legacy(generate+score) |
| Commerce opportunity (`new`) | 2 | Ollama | Same as normal — commerce NOT executed in new path |
| Commerce opportunity (`legacy`) | 3-4 | Ollama | extract_commerce + commerce_pipeline + generate + score |
| Background memory | 0 | N/A | Deterministic only |
| Shadow mode | +1 | Ollama | Additional async shadow call (disabled) |
| Context Engine observational | +0 | N/A | Observational only (disabled) |

---

## 26. PROVEN GAPS

### P0 — CRITICAL

**GAP 1: Two-call production path masquerading as one-call**
- `extract_commerce_signals()` runs unconditionally at `llm_worker.py:662`
- `one_call_generation()` makes a second LLM call at `one_call_pipeline.py:122`
- Result: 2 synchronous LLM calls per message, not 1
- Root cause: `extract_commerce_signals()` was never removed from the one-call path

**GAP 2: One-call path does NOT execute commerce**
- `_try_commerce_draft()` is only called in the legacy branch (line 1131)
- The one-call path generates reply + signals but never triggers PPV creation, offer sending, or commerce execution
- Result: Fans in one-call path receive conversational replies but NO commerce actions

### P1 — HIGH

**GAP 3: Model is Qwen3:4b, not Qwen2.5**
- `core/config.py:90`: `ollama_model: str = "qwen3:4b"`
- Architecture spec says Qwen2.5
- Not a bug per se, but contradicts the intended architecture

**GAP 4: Context Engine is observational only**
- `context_engine_observational=False` (default)
- Context Engine output is never consumed by production
- The actual context to Qwen is built by legacy `build_qwen3_context()`

**GAP 5: One-call fallback resurrects full legacy pipeline**
- If `one_call_pipeline_with_fallback()` fails, `_llm_path` becomes `"legacy"`
- This triggers the full 3-LLM legacy path
- Total calls on failure: 1 (extract_commerce) + 1 (one_call fail) + 3 (legacy) = up to 5 LLM calls

### P2 — MEDIUM

**GAP 6: RapidFuzz not in retrieval path**
- Used only for deduplication (observational) and local intent classification (not called from production)

**GAP 7: SentenceTransformer not in retrieval path**
- Loaded at worker startup but only used for `unified_intelligence.py` (not called from production)

**GAP 8: hnswlib not implemented**
- Does not exist in the codebase

**GAP 9: Context Engine retrieval not integrated**
- `context_engine/gatherer.py` has 7 real data sources but they are observational only
- Production uses legacy `build_qwen3_context()` which does simple keyword-triggered retrieval

### P3 — LOW

**GAP 10: orjson not used**
- Phase 74B specified orjson for performance, but no orjson imports found

**GAP 11: Strategy learning is synchronous**
- `update_strategy_evidence()` at line 1508 runs synchronously within process_message
- Could be backgrounded

---

## 27. SEVERITY CLASSIFICATION

| # | Gap | Severity | Impact |
|---|-----|----------|--------|
| 1 | Two-call path (not one-call) | P0 | 2x latency, 2x Ollama load |
| 2 | Commerce not executed in new path | P0 | No PPV offers, no revenue |
| 3 | Qwen3 not Qwen2.5 | P1 | Architecture mismatch |
| 4 | Context Engine observational | P1 | No semantic retrieval |
| 5 | Fallback resurrects 3-LLM | P1 | Up to 5 LLM calls on failure |
| 6-8 | RapidFuzz/ST/hnswlib not in path | P2 | No hybrid retrieval |
| 9 | Context Engine not integrated | P2 | Legacy context only |
| 10-11 | orjson/strategy async | P3 | Minor optimization |

---

## 28. RECOMMENDED STAGE B SCOPE

### Immediate fixes (P0):

1. **Remove unconditional `extract_commerce_signals()` from one-call path**
   - Move `extract_commerce_signals()` inside the `if _llm_path == "legacy"` branch
   - Or: make one-call pipeline consume its own commerce signals (already generated by Qwen)
   - Target: truly 1 LLM call for normal messages

2. **Integrate commerce execution into one-call path**
   - After one-call generation, check commerce signals and trigger `resolve_and_run_commerce()` if appropriate
   - Or: keep `_try_commerce_draft()` available in the one-call path
   - Target: PPV offers work in new path

### Architecture alignment (P1):

3. **Decide on Qwen3 vs Qwen2.5** — update architecture spec or model config
4. **Decide on Context Engine activation** — enable or remove
5. **Harden one-call fallback** — avoid resurrecting full legacy pipeline; use deterministic fallback instead

### Retrieval integration (P2):

6. **Integrate Context Engine retrieval into production context** — if Context Engine is intended to replace legacy context
7. **Implement hnswlib or remove from spec** — if vector retrieval is intended

---

## 29. REQUIRED FINAL VERDICT

```
PHASE 76 VERDICT

ONE-CALL RUNTIME: FAIL (2 LLM calls, not 1; commerce not executed)
QWEN2.5 RUNTIME: FAIL (model is Qwen3:4b, not Qwen2.5)
CONTEXT ENGINE RUNTIME: FAIL (observational only, not active)
RAPIDFUZZ INTEGRATION: FAIL (not in production retrieval path)
SENTENCE TRANSFORMER INTEGRATION: FAIL (not in production retrieval path)
HNSWLIB INTEGRATION: FAIL (not implemented)
CONTEXT COMPACTION: PARTIAL (one-call compaction exists but uses legacy context)
STRUCTURED OUTPUT: PASS (5-layer validation works)
DETERMINISTIC AUTHORITY: PASS (pricing, persona, safety all deterministic)
COMMERCE COMPATIBILITY: FAIL (one-call path does not execute commerce)
PPV PRICE AUTHORITY: PASS (LLM cannot influence price)
DELIVERY ARCHITECTURE: PASS (Redis Streams intact)
RESTART SAFETY: PASS (XAUTOCLAIM, ACK ordering correct)

SYNCHRONOUS LLM CALLS FOR NORMAL MESSAGE: 2

P0: 2
P1: 3
P2: 4
P3: 2

PRODUCTION CHANGES: NONE

FINAL VERDICT:
NOT READY

ROOT FINDING:
The "one-call" pipeline is actually a TWO-call pipeline because
extract_commerce_signals() runs unconditionally before the one-call branch.
Additionally, the one-call path does not execute commerce (PPV creation,
offer sending), making it revenue-inactive.

MOST IMPORTANT DISCONNECT:
The architecture specifies "Context Engine → Qwen2.5 → one generation" but
the actual runtime is "extract_commerce_signals (LLM #1) → one_call_generation
(LLM #2) → no commerce execution". The commerce pipeline is only reachable
via the legacy 3-LLM path.

NEXT SURGICAL STAGE:
Stage B should:
1. Move extract_commerce_signals() inside the legacy branch only
2. Make one-call pipeline self-contained (its own commerce signal generation)
3. Add commerce execution hook to the one-call path
4. Target: 1 LLM call + deterministic commerce execution
```
