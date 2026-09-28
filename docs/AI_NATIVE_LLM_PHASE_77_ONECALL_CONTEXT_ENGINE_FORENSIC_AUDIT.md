# PHASE 77 — ONE-CALL QWEN2.5 + CONTEXT ENGINE FORENSIC RUNTIME AUDIT

**Date:** 2026-09-02
**Scope:** Read-only forensic verification of actual runtime code paths
**Status:** COMPLETE — Stage A

---

## 1. EXECUTIVE SUMMARY

The production runtime does NOT implement a true one-call pipeline. For a normal fan message with `llm_path=new`, **2 synchronous LLM calls** execute before the reply is sent:

1. `extract_commerce_signals()` — runs unconditionally at `llm_worker.py:662`, BEFORE the one-call branch
2. `one_call_generation()` — the actual one-call LLM generation at `one_call_pipeline.py:122`

The root cause: `extract_commerce_signals()` at line 662 executes outside and before the `_llm_path` branch at line 1043. It was never removed when the one-call path was integrated.

Additionally:
- The model is `qwen3:4b`, not Qwen2.5 as specified in architecture
- The Context Engine is observational only (disabled by default), does NOT control context
- RapidFuzz is only used in observational deduplication, NOT in production retrieval
- SentenceTransformer loads but is NOT used in the production message path
- hnswlib does not exist in the codebase
- The one-call fallback (`_fallback_3llm_pipeline`) returns empty reply — it does NOT invoke the legacy 3-LLM pipeline despite its name
- Commerce execution (PPV creation, offer sending) does NOT occur in the one-call path

---

## 2. CURRENT RUNTIME CALL GRAPH

```
Telegram event
    │
    ▼
chatbotv2/handlers.py:26 handle_incoming_message()
    │
    ├─ event.get_sender() [Telethon API]
    ├─ check_rate_limit() [Redis]
    ├─ unblacklist_entity() [Redis]
    ├─ upsert_user() [PostgreSQL]
    ├─ publish_event("message.created") [Redis Pub/Sub]
    ├─ debounce_enqueue() [Redis]
    │
    ▼ (after debounce window)
    │
_wait_and_process() :128
    │
    ├─ get_debounced_messages() [Redis]
    ├─ get_cached_user_persona() / get_user_persona() [Redis/PG]
    ├─ get_cached_default_persona() / get_default_persona() [Redis/PG]
    ├─ enqueue_inbound() [Redis Stream XADD]
    │
    ▼ (LLM worker reads from Redis Stream)
    │
workers/llm_worker.py:1666 read_inbound() [Redis XREADGROUP]
    │
    ▼
process_message() :431
    │
    ├─ resolve_single_application_creator() [deterministic]
    ├─ acquire_user_lock() [Redis SETNX]
    ├─ upsert_user() + is_user_auto_reply_excluded() [parallel PG]
    ├─ get_structured_persona_async() [PG]
    ├─ build_qwen3_context() :509 [PG × 4 parallel + derive_conversation_state]
    │     ├─ get_user() [PG]
    │     ├─ get_user_profile() [PG]
    │     ├─ get_recent_messages() [PG]
    │     ├─ get_latest_summary_with_age() [PG]
    │     ├─ derive_conversation_state() [deterministic]
    │     ├─ render_compact_persona_block() [deterministic]
    │     ├─ build_llm_context() → render_context() [PG]
    │     ├─ retrieve_relevant_memories() [PG — keyword trigger only]
    │     ├─ get_fan_knowledge() [PG]
    │     ├─ rank_products_by_relevance() [deterministic]
    │     └─ trim_to_token_budget() [deterministic]
    │
    ├─ publish_event("ai.generation_started") [Redis Pub/Sub]
    ├─ observe_context_engine() :538 [OBSERVATIONAL ONLY, disabled by default]
    ├─ extract_explicit_memories() [deterministic]
    ├─ extract_fan_knowledge() [deterministic]
    ├─ observe_behavioral_signal() [deterministic]
    ├─ Q1 Shadow launch [background, disabled by default]
    │
    ├─ extract_commerce_signals() :662 ◄── LLM CALL #1 (UNCONDITIONAL)
    │     └─ commerce/deepseek.py:190 provider.generate()
    │
    ├─ resolve_open_loop() [deterministic]
    ├─ build_conversational_commerce_state() [PG + deterministic]
    ├─ derive_persona_behavior_state() [deterministic]
    │
    ├─ _llm_path check :1043
    │
    ├─ if _llm_path == "new":
    │     ├─ one_call_pipeline_with_fallback() :1082
    │     │     ├─ build_one_call_context() [deterministic — compact ~1150 tokens]
    │     │     ├─ build_commerce_signal_hints() [deterministic]
    │     │     ├─ validate_one_call_context() [deterministic]
    │     │     ├─ provider.generate() ◄── LLM CALL #2 (Qwen structured JSON)
    │     │     ├─ validate_one_call_response() [5-layer Pydantic]
    │     │     └─ validate_draft_quality() [deterministic]
    │     ├─ if invalid → _llm_path = "legacy" (FALLBACK)
    │
    ├─ if _llm_path == "legacy":
    │     ├─ _try_commerce_draft() :1131
    │     │     └─ resolve_and_run_commerce()
    │     │           └─ extract_commerce_signals() ◄── LLM CALL #1 (DUPLICATE)
    │     ├─ generate_draft_with_tools() or generate_draft() ◄── LLM CALL #2
    │     └─ score_draft() :1214 ◄── LLM CALL #3 (LLM-based scoring)
    │
    ├─ validate_persona_voice() [deterministic]
    ├─ send/handoff decision [deterministic]
    ├─ enqueue_send() or add_to_operator_queue() [Redis]
    └─ post_process() [background: profile extraction, summarization]
```

---

## 3. EXACT LLM CALL COUNT

### Normal message with `llm_path=new` (ACTIVE DEFAULT):

| # | Function | File:Line | Provider | Model | Purpose | Blocking |
|---|----------|-----------|----------|-------|---------|----------|
| 1 | `extract_commerce_signals()` | `llm_worker.py:662` → `commerce/deepseek.py:190` | Ollama | `qwen3:4b` | Commerce signal extraction | YES |
| 2 | `one_call_generation()` | `one_call_pipeline.py:122` | Ollama | `qwen3:4b` | One-call structured JSON | YES |

**ACTUAL SYNCHRONOUS LLM CALLS = 2**

### Normal message with `llm_path=legacy`:

| # | Function | File:Line | Provider | Model | Purpose | Blocking |
|---|----------|-----------|----------|-------|---------|----------|
| 1 | `extract_commerce_signals()` | `llm_worker.py:662` | Ollama | `qwen3:4b` | Commerce signal extraction | YES |
| 2 | `generate_draft_with_tools()` or `generate_draft()` | `llm_worker.py:1189` or `:1207` | Ollama | `qwen3:4b` | Draft generation | YES |
| 3 | `score_draft()` | `llm_worker.py:1214` | Ollama | `qwen3:4b` | LLM-based quality scoring | YES |

**ACTUAL SYNCHRONOUS LLM CALLS = 3**

### OneCall failure path (exception or invalid result):

| Step | LLM Calls | Cumulative |
|------|-----------|------------|
| `extract_commerce_signals()` | 1 | 1 |
| `one_call_generation()` fails | 0 (returns cached result) | 1 |
| `_llm_path = "legacy"` → full legacy | 3 | **4** |

**Maximum LLM calls on one-call failure: 4**

---

## 4. LLM PROVIDER/MODEL MATRIX

| Setting | Value | Source | Notes |
|---------|-------|--------|-------|
| `llm_provider` | `"ollama"` | `config.py:84` | Active provider |
| `ollama_model` | `"qwen3:4b"` | `config.py:90` | Runtime model |
| `ollama_base_url` | `"https://ollama.brestalogistics.co.ke"` | `config.py:89` | Remote endpoint |
| `ollama_num_ctx` | `8192` | `config.py:94` | Context window |
| `model_name` | `"gemini-flash-latest"` | `config.py:25` | Default for `generate_draft` — Ollama ignores this |
| `cheap_model` | `"gemini-flash-latest"` | `config.py:26` | Used by `extract_commerce_signals` and `score_draft` — Ollama ignores this |
| `max_tokens` | `200` | `config.py:29` | Output cap |
| `temperature` | `0.85` | `config.py:30` | Sampling temp |
| `llm_path` | `"new"` | `config.py:141` | One-call active |
| `qwen_shadow_enabled` | `False` | `config.py:99` | Shadow disabled |
| `context_engine_observational` | `False` | `config.py:136` | Observational disabled |
| `context_engine_canary_mode` | `"disabled"` | `config.py:148` | Canary disabled |

**CRITICAL:** The model is `qwen3:4b`, NOT Qwen2.5. The Ollama provider (`llm_provider_ollama.py:300-303`) ignores Gemini model name strings and uses `self._model` which comes from `ollama_model`.

---

## 5. ONE-CALL BRANCH AUDIT

### Configuration read:
- `core/config.py:141`: `llm_path: str = "new"` (default)
- `workers/llm_worker.py:1043`: `_llm_path = getattr(_settings, "llm_path", "legacy")`

### Branch logic (llm_worker.py:1043-1128):
```python
_llm_path = getattr(_settings, "llm_path", "legacy")  # "new" by default

if _llm_path == "new":
    try:
        _one_call_result = await one_call_pipeline_with_fallback(...)
        if _one_call_result.is_valid and _one_call_result.reply:
            draft = _one_call_result.reply
            score = _one_call_result.quality_score
            flags = ...
        else:
            _llm_path = "legacy"  # FALLBACK
    except Exception:
        _llm_path = "legacy"  # FALLBACK

if _llm_path == "legacy":
    # Full 3-LLM legacy pipeline
```

### Key findings:
1. **Worker DOES use it:** Yes, `process_message()` reads `_llm_path` and branches
2. **Code BEFORE the branch makes LLM call:** YES — `extract_commerce_signals()` at line 662
3. **Code AFTER the branch makes LLM calls:** Only if `_llm_path` becomes `"legacy"` (fallback)
4. **Failure resurrection:** YES — if `one_call_pipeline_with_fallback()` returns invalid or raises, `_llm_path` becomes `"legacy"` and the full 3-LLM path executes
5. **Commerce before one-call:** YES — `extract_commerce_signals()` runs at line 662, before the branch at line 1043
6. **Scoring after one-call:** NO — in the one-call path, scoring is done by `validate_draft_quality()` (deterministic). The LLM-based `score_draft()` at line 1214 is only in the legacy path
7. **Shadow mode:** `qwen_shadow_enabled=False` by default — no additional LLM call
8. **Agent execution:** `ai_agent_canary_enabled=False` by default — no agent LLM calls

---

## 6. ONE-CALL SCHEMA AUDIT

### OneCallReply (Pydantic model — `one_call.py:38-67`):

| Field | Type | Required | Default | Validation |
|-------|------|----------|---------|------------|
| `reply` | `str` | YES | — | `min_length=1`, `max_length=2000`, strip, reject empty |
| `commerce_signals` | `CommerceSignals` | NO | `CommerceSignals.low_information()` | Inner validation |
| `confidence` | `float` | NO | `0.5` | `ge=0.0`, `le=1.0` |
| `needs_handoff` | `bool` | NO | `False` | — |

Model config: `extra="forbid"` — rejects unknown fields.

### OneCallResult (dataclass — `one_call.py:70-85`):

| Field | Type | Default |
|-------|------|---------|
| `reply` | `str` | — |
| `signals` | `CommerceSignals` | — |
| `confidence` | `float` | — |
| `needs_handoff` | `bool` | — |
| `quality_score` | `float` | `0.0` |
| `quality_flags` | `list[str]` | `[]` |
| `safety_flags` | `list[str]` | `[]` |
| `is_valid` | `bool` | `True` |
| `validation_error` | `str \| None` | `None` |

### Validation layers (`one_call.py:88-172`):

1. **JSON parse** — `json.loads()` — malformed → `is_valid=False`
2. **Pydantic schema** — `OneCallReply.model_validate()` — invalid → `is_valid=False`
3. **Safety flags** — `_compute_safety_flags()` — keyword detection
4. **Quality heuristics** — `_compute_quality_heuristics()` — length/formality/generic/repetition
5. **Confidence/handoff override** — safety flags force `needs_handoff=True`

### Malformed response behavior:
- Returns `OneCallResult(is_valid=False)` — no retry, no additional LLM call
- Worker catches this at line 1113-1119: `_llm_path = "legacy"` → full legacy pipeline

### Fallback function (`one_call_pipeline.py:212-234`):
```python
async def _fallback_3llm_pipeline(...):
    """Fallback to existing 3-LLM pipeline."""
    # TODO: Integrate with existing llm_worker.py pipeline
    return OneCallResult(
        reply="",
        signals=CommerceSignals.low_information(),
        confidence=0.0,
        needs_handoff=True,
        is_valid=True,  # ← NOTE: is_valid=True but reply=""
        validation_error="Fallback to 3-LLM pipeline",
    )
```

**CRITICAL:** The `_fallback_3llm_pipeline` does NOT actually invoke the 3-LLM pipeline. It returns an empty reply with `is_valid=True`. This means:
- `one_call_pipeline_with_fallback` returns this result
- Worker checks `_one_call_result.is_valid and _one_call_result.reply` at line 1101
- `is_valid=True` but `reply=""` → condition is False
- `_llm_path = "legacy"` at line 1119 → full legacy pipeline

The fallback effectively degrades to the legacy path via the worker's own branching logic.

---

## 7. FALLBACK AUDIT

| Failure | Current Behavior | Extra LLM Calls? | Max Total |
|---------|-----------------|-------------------|-----------|
| `extract_commerce_signals()` fails | Returns `low_information()` — continues | 0 | 2 |
| `one_call_generation()` provider error | Returns `OneCallResult(is_valid=False)` | 0 | 2 |
| OneCall invalid JSON | Returns `OneCallResult(is_valid=False)` | 0 | 2 |
| OneCall invalid schema | Returns `OneCallResult(is_valid=False)` | 0 | 2 |
| OneCall empty reply | Returns `OneCallResult(is_valid=False)` | 0 | 2 |
| `one_call_pipeline_with_fallback` exception | Worker catches, `_llm_path = "legacy"` | +3 (legacy) | **4** |
| OneCall returns `is_valid=True, reply=""` | Worker: `_llm_path = "legacy"` | +3 (legacy) | **4** |
| Legacy `generate_draft_with_tools` fails | Tries `generate_draft()` | +1 | 4 |
| Legacy `score_draft` LLM fails | Returns 0.0 score, fail-closed | 0 | 4 |
| Legacy `_try_commerce_draft` calls `resolve_and_run_commerce` which calls `extract_commerce_signals` again | Duplicate signal extraction | +1 (duplicate) | 5 |

**Maximum LLM calls in worst case: 5** (extract_commerce + one_call_fail + legacy_extract_commerce + generate_draft + score_draft)

---

## 8. CONTEXT ENGINE AUDIT

### Status: OBSERVATIONAL ONLY — NOT ACTIVE

| Component | Implemented | Called in Production | Result Used by Qwen |
|-----------|-------------|---------------------|---------------------|
| Feature flag | YES (`context_engine_observational`) | NO (default `False`) | NO |
| Integration | `observe_context_engine()` at `llm_worker.py:538` | Only when enabled | Telemetry only |
| Gatherer (7 sources) | YES (`gatherer.py`) | Only when enabled | NO |
| Scorer | YES (`scorer.py`) | Only when enabled | NO |
| Deduplicator | YES (`dedup.py`) | Only when enabled | NO |
| Budget manager | YES (`budget.py`) | Only when enabled | NO |
| Assembler | YES (`assembler.py`) | Only when enabled | NO |
| Renderer | YES (`renderer.py`) | Only when enabled | NO |
| Canary | YES (`canary_observer.py`) | NO (default `disabled`) | NO |

The Context Engine pipeline is fully implemented but its output is NEVER consumed by the production context sent to Qwen. The actual context is built by `build_qwen3_context()` at `memory/context.py:521`.

---

## 9. RAPIDFUZZ AUDIT

| Location | Purpose | In Production Path? |
|----------|---------|-------------------|
| `context_engine/dedup.py:57-61` | Lexical similarity for deduplication | NO (observational only) |
| `commerce/unified_intelligence.py:45-57` | Intent classification lexical evidence | NO (not called from `process_message`) |
| `commerce/unified_intelligence.py:157-169` | Intent classification lexical evidence | NO (not called from `process_message`) |
| `benchmark_local.py:36` | Benchmark script | NO (test tool) |
| Tests | Various test assertions | NO |

**RapidFuzz is NOT in the production fan message retrieval path.** It is used only in observational deduplication and in `unified_intelligence.py` which is NOT imported or called from `llm_worker.py`.

---

## 10. SENTENCE TRANSFORMER / MINILM AUDIT

| Component | Status |
|-----------|--------|
| Module | `commerce/embedding_model.py` |
| Model | `all-MiniLM-L6-v2` |
| Dimension | 384 |
| Loading | Lazy singleton via `_load_model()` |
| Warmup | `llm_worker.py:1618-1626` at worker startup |
| Used by | `commerce/unified_intelligence.py:180-181` |
| Called from `process_message`? | NO |

The embedding model loads at worker startup and is used ONLY by `unified_intelligence.py` for local intent classification. This module is NOT called from `process_message()`. The Context Engine gatherer does NOT use embeddings.

**Does `current message → MiniLM embedding` occur in production?** NO.

---

## 11. HNSWLIB AUDIT

**hnswlib does NOT exist in the codebase.** Zero matches for `hnswlib` across all `.py` files.

The architecture specification calls for hnswlib vector retrieval, but it was never implemented. The `unified_intelligence.py` uses brute-force cosine similarity against ~110 reference vectors.

---

## 12. MEMORY RETRIEVAL AUDIT

### Current retrieval path (`memory/context.py:748-763`):

```python
from commerce.long_term_memory import retrieve_relevant_memories
_relevant_mems = await retrieve_relevant_memories(
    creator_id, user_id,
    current_topic=_cur_mem,
    open_threads=tuple(_topics_mem),
    limit=3,
    profile=profile,
)
```

### Retrieval method:
- Source: PostgreSQL table (via `commerce/long_term_memory.py`)
- Filter: `creator_id`, `user_id`
- Ordering: topic overlap with `current_topic` and `open_threads`
- Limit: 3 memories
- Method: **SQL-only** — keyword/topic matching, no semantic search, no RapidFuzz, no embeddings

### Historical message retrieval (`memory/context.py:487-497`):

```python
if should_retrieve(current_message):
    retrieved = await retrieve_relevant_history(user_id, current_message, k=3)
```

- Trigger: keyword match against `RETRIEVAL_TRIGGERS` ("remember", "told you", "said", etc.)
- Method: **SQL keyword query** — no semantic search

### Does retrieved memory reach Qwen?
YES — memories are appended as system messages in `build_qwen3_context()` at lines 758-761.

---

## 13. CONTEXT RANKING AUDIT

The Context Engine scorer (`context_engine/scorer.py:33-40`) defines weights:

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

**These weights are NOT used in production.** They exist only in the observational Context Engine. The production `build_qwen3_context()` does not compute any ranking scores — it uses SQL ordering and simple limits.

---

## 14. CONTEXT BUDGET AUDIT

### Production context (`build_qwen3_context`):

| Component | Token Budget | Source |
|-----------|-------------|--------|
| System prompt | 400 tokens | `QWEN3_TOKEN_BUDGET["system"]` |
| State context | 200 tokens | `QWEN3_TOKEN_BUDGET["state"]` |
| Conversation | 800 tokens | `QWEN3_TOKEN_BUDGET["conversation"]` |
| Summary | 200 tokens | `QWEN3_TOKEN_BUDGET["summary"]` |
| Total | 1600 tokens | — |

### One-call context (`build_one_call_context`):

| Component | Token Budget | Source |
|-----------|-------------|--------|
| System prompt | 350 tokens | `ONE_CALL_TOKEN_BUDGET["system"]` |
| State context | 150 tokens | `ONE_CALL_TOKEN_BUDGET["state"]` |
| Conversation | 600 tokens | `ONE_CALL_TOKEN_BUDGET["conversation"]` |
| Signals hint | 50 tokens | `ONE_CALL_TOKEN_BUDGET["signals_hint"]` |
| Max messages | 8 | `ONE_CALL_MAX_MESSAGES` |
| Total | 1150 tokens | — |

### Ollama parameters:

| Parameter | Value | Source |
|-----------|-------|--------|
| `num_ctx` | 8192 | `config.py:94` |
| `num_predict` | 300 (default) | `llm_provider_ollama.py:44` |
| `temperature` | 0.7 | `llm_provider_ollama.py:183` |
| `top_p` | 0.8 | `llm_provider_ollama.py:184` |
| `think` | `False` | `llm_provider_ollama.py:179` |

**Both contexts fit well within 8192 num_ctx.**

---

## 15. PERSONA AUDIT

### Persona data flow:

```
get_user_persona() / get_default_persona() [PG]
    ↓
persona: str (raw text)
    ↓
build_qwen3_system_prompt() [memory/context.py:199]
    ↓
System message in context
    ↓
render_compact_persona_block() [memory/context.py:685]
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

### Behavioral constraints:
- Injected as system message at `llm_worker.py:1026`
- Content: persona behavior state, knowledge, commerce objective
- DETERMINISTIC — no LLM involved

---

## 16. AUTHORITATIVE STATE AUDIT

### Conversation state:
| Field | Source | Authoritative |
|-------|--------|---------------|
| current_topic | `derive_conversation_state()` | YES (deterministic) |
| identity_already_established | `derive_conversation_state()` | YES (deterministic) |
| lifecycle | `derive_lifecycle()` | YES (deterministic) |
| response_mode | `ConversationOperationDecision` | YES (deterministic) |
| question_policy | `ConversationOperationDecision` | YES (deterministic) |

### Fan/business state:
| Field | Source | Authoritative |
|-------|--------|---------------|
| funnel_stage | PG `users` table | YES |
| relationship_state | `derive_relationship_state()` | YES (deterministic) |
| purchase_count | PG `commerce_offers` | YES |
| is_blocked | PG `users` table | YES |
| do_not_auto_reply | PG `users` table | YES |

### Commerce state:
| Field | Source | Authoritative |
|-------|--------|---------------|
| current_offer | PG `commerce_offers` | YES |
| offer_state | PG `commerce_offers` | YES |
| PPV pricing | PG `fangate_products.price_minor` | YES |
| has_active_offer | `build_conversational_commerce_state()` | YES |
| recent_offer_count | `get_timing_context()` | YES |
| cooldown | `get_timing_context()` | YES |

### Persona state:
| Field | Source | Authoritative |
|-------|--------|---------------|
| persona text | PG `personas` table | YES (HARD_POLICY) |
| structured persona | `get_structured_persona_async()` | YES |
| behavior block | `derive_persona_behavior_state()` | YES (deterministic) |

**All authoritative state is deterministic. No field is LLM-derived.**

---

## 17. COMMERCE OPPORTUNITY AUDIT

### Sales opportunity identification:

1. `extract_commerce_signals()` at `llm_worker.py:662` — LLM-derived signals
2. `build_conversational_commerce_state()` at `llm_worker.py:725` — deterministic state
3. `derive_commercial_objective()` at `llm_worker.py:879` — deterministic
4. `compute_pressure()` at `llm_worker.py:827` — deterministic
5. `derive_risk()` at `llm_worker.py:834` — deterministic

### PPV preparation (legacy path only):

```
_try_commerce_draft() [llm_worker.py:1131]
    ↓
resolve_and_run_commerce() [commerce/pipeline.py:460]
    ├─ build_conversation_context() [deterministic]
    ├─ extract_commerce_signals() [LLM — DUPLICATE if already called]
    ├─ decide_from_signals() [deterministic]
    ├─ resolve_commerce_product_with_history() [PG]
    ├─ orchestrate_commerce() [deterministic]
    └─ generate_commerce_response() [LLM — deepseek_response.py:481]
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
- If cooldown/ ineligible → no offer created regardless of LLM signals

---

## 18. ONE-CALL → COMMERCE PROPAGATION AUDIT

### Signal flow in one-call path:

1. `extract_commerce_signals()` at line 662 → `_commerce_signals` (LLM #1)
2. `_commerce_signals` is converted to `_commerce_text` at line 1075-1080
3. `_commerce_text` is passed to `one_call_pipeline_with_fallback()` as `commerce_text`
4. Inside `one_call_generation()`:
   - `build_commerce_signal_hints()` adds hints to context
   - Qwen generates structured JSON with embedded `commerce_signals`
   - `validate_one_call_response()` extracts signals from Qwen output
5. At line 1106: `if _commerce_signals is None and _one_call_result.signals:` — one-call signals only used if pre-extracted signals are None

**FINDING:** Since `extract_commerce_signals()` at line 662 always sets `_commerce_signals`, the one-call result's signals are effectively IGNORED (line 1106 condition is False). The pre-extracted signals from LLM #1 are used downstream.

### Does one-call path execute commerce?
**NO.** `_try_commerce_draft()` is only called in the legacy branch at line 1131. The one-call path generates a reply + signals but does NOT trigger PPV creation, offer sending, or commerce execution.

---

## 19. SCORING AUDIT

### One-call path scoring:
- `validate_draft_quality()` at `one_call_pipeline.py:147` — DETERMINISTIC
- `score_draft_deterministic()` at `scoring_deterministic.py:23` — heuristics only
- `compute_safety_flags()` at `scoring_deterministic.py:120` — keyword detection
- **No LLM call**

### Legacy path scoring:
- `score_draft()` at `scoring.py:81` — **LLM-BASED**
- Calls `provider.generate()` at `scoring.py:159`
- Uses `SCORING_SYSTEM_PROMPT` for quality assessment
- **This is an additional LLM call**

### OneCall response self-assessment:
- `confidence` field (0.0-1.0) — LLM self-assessment
- `needs_handoff` field — LLM recommendation
- Both are advisory — deterministic authority overrides

---

## 20. AGENT/TOOL AUDIT

| Component | Status | In One-Call Path? |
|-----------|--------|-------------------|
| `run_agent_runtime()` | `agent/runtime.py:204` | NO (`ai_agent_canary_enabled=False`) |
| `generate_draft_with_tools()` | `llm_worker.py:156` | NO (legacy path only) |
| Tool declarations | `core/llm_tools.py` | NO (legacy path only) |
| Agent canary | `agent/canary.py` | NO (disabled) |

**Agent/tool execution is NOT reachable from the one-call path.** It is only in the legacy branch and disabled by default.

---

## 21. BACKGROUND LLM AUDIT

| Task | Location | LLM Call? | Blocking? |
|------|----------|-----------|-----------|
| Profile extraction | `memory/profile.py:75` | YES (background) | NO (`asyncio.create_task`) |
| Summarization | `memory/summarizer.py:54` | YES (background) | NO (`asyncio.create_task`) |
| Strategy learning | `llm_worker.py:1508` | NO (deterministic) | YES (within process_message) |
| Shadow evaluation | `llm_worker.py:1280` | NO (eval only) | YES (within process_message) |
| Behavioral signals | `llm_worker.py:630` | NO (deterministic) | YES |

**Background LLM calls (profile extraction, summarization) do NOT block the reply.** They run as fire-and-forget tasks.

---

## 22. POSTGRESQL I/O AUDIT

### Round trips in `build_qwen3_context`:
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

### Duplicate reads:
- `get_user()` called in both `build_qwen3_context` and `process_message` (line 719)
- `get_user_profile()` called in both `build_qwen3_context` and `process_message` (line 720)

---

## 23. REDIS I/O AUDIT

| Operation | Location | Count |
|-----------|----------|-------|
| `check_rate_limit()` | `handlers.py:42` | 1 |
| `debounce_enqueue()` | `handlers.py:99` | 1 |
| `get_debounced_messages()` | `handlers.py:141` | 1 |
| `enqueue_inbound()` (XADD) | `handlers.py:160` | 1 |
| `read_inbound()` (XREADGROUP) | `llm_worker.py:1666` | 1 |
| `acquire_user_lock()` | `llm_worker.py:474` | 1 |
| `is_auto_reply_enabled()` | `llm_worker.py:1302` | 1 |
| `enqueue_send()` or `add_to_operator_queue()` | `llm_worker.py:1353/1381` | 1 |
| `ack_inbound()` (XACK) | `llm_worker.py:1686` | 1 |
| `release_user_lock()` | `llm_worker.py:1697` | 1 |
| `publish_event()` (Pub/Sub) | `llm_worker.py:526` | 1+ |
| `publish_events_batch()` | `llm_worker.py:1418` | 1 |

**Total: ~12 Redis round trips** per message.

---

## 24. SERIALIZATION AUDIT

| Location | Operation | Purpose |
|----------|-----------|---------|
| `one_call_pipeline.py:125` | `json.dumps(messages)` | Serialize context for Qwen |
| `one_call.py:113` | `json.loads(raw_json)` | Parse Qwen JSON response |
| `scoring.py:167` | `json.loads(response_text)` | Parse scoring LLM response |
| `deepseek.py:239` | `_parse_signals_json()` | Parse commerce signals |

**No `orjson` usage found in production code.** Phase 74B orjson optimization was not implemented.

---

## 25. PROVIDER/NETWORK AUDIT

| Component | Value |
|-----------|-------|
| HTTP client | `httpx.AsyncClient` (persistent, per-provider) |
| Connection reuse | YES (client kept alive) |
| Timeout | 120s (`config.py:91`) |
| Endpoint | `https://ollama.brestalogistics.co.ke/api/chat` |
| Auth | HTTP Basic Auth |
| Request sequence | SEQUENTIAL (LLM calls are awaited one at a time) |

**Calls are sequential, not parallel.** Each LLM call waits for the previous to complete.

---

## 26. LATENCY/TELEMETRY AUDIT

### Existing measurements in `llm_worker.py`:

| Metric | Location | Available |
|--------|----------|-----------|
| `context_build_ms` | `llm_worker.py:511` | YES |
| `generation_latency_ms` | `llm_worker.py:1126` | YES |
| `scoring_latency_ms` | `llm_worker.py:1216` | YES (legacy only) |
| `context_engine_ms` | `llm_worker.py:549` | YES (observational) |
| `shadow_latency_ms` | `llm_worker.py:1297` | YES (if enabled) |

**Known:** context build time, generation time, scoring time
**Unknown:** individual PG query times, Redis round-trip times, per-LLM-call latency breakdown

---

## 27. CURRENT ARCHITECTURE DIAGRAM

```
Fan Message
    │
    ▼
Telegram → handlers.py → debounce → Redis Stream
    │
    ▼
LLM Worker → process_message()
    │
    ├─ build_qwen3_context() [legacy context builder]
    │     ├─ PG: user, profile, recent, summary (parallel)
    │     ├─ derive_conversation_state() [deterministic]
    │     ├─ render_compact_persona_block() [deterministic]
    │     ├─ build_llm_context() → render_context() [PG]
    │     ├─ retrieve_relevant_memories() [PG — keyword only]
    │     ├─ get_fan_knowledge() [PG]
    │     ├─ rank_products_by_relevance() [deterministic]
    │     └─ trim_to_token_budget() [deterministic]
    │
    ├─ extract_commerce_signals() ◄── LLM #1 (UNCONDITIONAL)
    │
    ├─ if llm_path == "new":
    │     └─ one_call_generation() ◄── LLM #2
    │           ├─ build_one_call_context() [compact]
    │           ├─ provider.generate() [Qwen structured JSON]
    │           └─ validate_draft_quality() [deterministic]
    │
    ├─ if llm_path == "legacy":
    │     ├─ _try_commerce_draft() → resolve_and_run_commerce()
    │     │     └─ extract_commerce_signals() ◄── LLM #1 (DUPLICATE)
    │     ├─ generate_draft_with_tools() ◄── LLM #2
    │     └─ score_draft() ◄── LLM #3 (LLM-based)
    │
    ├─ validate_persona_voice() [deterministic]
    ├─ send/handoff decision [deterministic]
    └─ enqueue_send() → Redis Stream → send worker → Telethon
```

---

## 28. TARGET ARCHITECTURE DIAGRAM

```
Fan Message
    │
    ▼
Telegram → handlers.py → debounce → Redis Stream
    │
    ▼
LLM Worker → process_message()
    │
    ▼
Context Engine
    ├─ RapidFuzz lexical retrieval
    ├─ MiniLM semantic retrieval
    ├─ hnswlib vector search
    ├─ merge / deduplicate
    ├─ relevance / recency / importance scoring
    └─ hard token budget
    │
    ▼
Compact Qwen Context
    │
    ▼
Qwen2.5 ONE GENERATION
    │
    ▼
Pydantic Validation
    │
    ▼
Deterministic Authority
    ├─ Commerce (PPV pricing, offers)
    ├─ Persona validation
    └─ Safety scoring
    │
    ▼
Send / Handoff / PPV
```

---

## 29. DIVERGENCE MATRIX

| # | Target | Current | Severity |
|---|--------|---------|----------|
| 1 | 1 Qwen call per message | 2 LLM calls (extract_commerce + one_call) | P0 |
| 2 | Context Engine controls context | Context Engine observational only | P0 |
| 3 | RapidFuzz for retrieval | RapidFuzz not in production path | P1 |
| 4 | MiniLM for semantic retrieval | MiniLM not in production path | P1 |
| 5 | hnswlib for vector search | hnswlib not implemented | P1 |
| 6 | Qwen2.5 model | Model is qwen3:4b | P1 |
| 7 | One-call includes commerce | One-call path does not execute commerce | P0 |
| 8 | Deterministic scoring | Legacy path still uses LLM scoring | P2 |
| 9 | Hybrid retrieval ranking | SQL-only keyword retrieval | P2 |
| 10 | orjson serialization | No orjson usage | P3 |

---

## 30. REQUIRED STAGE B CHANGES

### P0 — Must fix:

1. **Remove unconditional `extract_commerce_signals()`** from the main path. Move it inside the legacy branch only, OR have the one-call pipeline generate its own commerce signals (already does via Qwen structured output).

2. **Add commerce execution hook to one-call path.** After one-call generation, check if commerce signals warrant PPV execution and call `resolve_and_run_commerce()` if appropriate.

### P1 — Should fix:

3. **Update model to Qwen2.5** or update architecture spec to match Qwen3:4b.

4. **Integrate Context Engine into production context** — if semantic retrieval is intended, enable the Context Engine and consume its output.

5. **Implement hnswlib or remove from spec.**

### P2 — Nice to have:

6. **Replace LLM scoring with deterministic scoring** in legacy path.

7. **Add RapidFuzz/MiniLM to memory retrieval** for hybrid search.

---

## 31. ANSWERS TO Q1–Q30

**Q1:** How many synchronous LLM calls occur for one normal fan message?
**A1:** 2 (with `llm_path=new`). `extract_commerce_signals()` + `one_call_generation()`.

**Q2:** What exact functions make those calls?
**A2:** `commerce/deepseek.py:extract_commerce_signals()` (line 190) and `core/one_call_pipeline.py:one_call_generation()` (line 123).

**Q3:** Which model/provider actually performs them?
**A3:** Ollama provider, model `qwen3:4b`, endpoint `https://ollama.brestalogistics.co.ke`.

**Q4:** Is Qwen2.5 actually running?
**A4:** NO. The model is `qwen3:4b` (`config.py:90`).

**Q5:** Is the OneCall path actually the production path?
**A5:** YES — `llm_path="new"` is the default. But `extract_commerce_signals()` runs before the branch.

**Q6:** Does any LLM call occur before the OneCall branch?
**A6:** YES — `extract_commerce_signals()` at line 662, before the branch at line 1043.

**Q7:** Does any LLM call occur after the OneCall generation?
**A7:** NO — in the one-call path, scoring is deterministic. LLM scoring is legacy-only.

**Q8:** Does OneCall failure resurrect the legacy pipeline?
**A8:** YES — `_llm_path = "legacy"` at lines 1119/1123, triggering full 3-LLM legacy path.

**Q9:** Are RapidFuzz results actually used?
**A9:** NO — only in observational Context Engine dedup and `unified_intelligence.py` (not called from production).

**Q10:** Are SentenceTransformer/MiniLM embeddings actually used?
**A10:** NO — model loads at startup but only used by `unified_intelligence.py` (not called from production).

**Q11:** Is hnswlib actually queried?
**A11:** NO — hnswlib does not exist in the codebase.

**Q12:** Does retrieved memory actually reach Qwen?
**A12:** YES — via `build_qwen3_context()` at lines 748-761. SQL-based retrieval, not semantic.

**Q13:** What exact context reaches Qwen?
**A13:** System prompt (persona + rules) + compact state (funnel, profile, relationship, commerce) + recent messages (trimmed) + persona behavior block + commerce context + memories + vault content titles.

**Q14:** How large is that context?
**A14:** ~1150-1600 tokens for one-call; ~1600+ for legacy. Well within 8192 num_ctx.

**Q15:** What determines a sales opportunity?
**A15:** `extract_commerce_signals()` (LLM) + `build_conversational_commerce_state()` (deterministic) + `compute_pressure()` (deterministic).

**Q16:** How does a sales opportunity become a PPV?
**A16:** ONLY in legacy path: `_try_commerce_draft()` → `resolve_and_run_commerce()` → `decide_from_signals()` → `orchestrate_commerce()`. NOT in one-call path.

**Q17:** Who determines PPV price?
**A17:** `fangate_products.price_minor` from PostgreSQL. Deterministic. LLM cannot override.

**Q18:** Can the LLM override PPV price or eligibility?
**A18:** NO. System prompt forbids it. Deterministic authority overrides LLM output.

**Q19:** Do OneCall commerce signals reach deterministic commerce authority?
**A19:** PARTIALLY — signals from `extract_commerce_signals()` (LLM #1) are passed to `build_conversational_commerce_state()`. OneCall's embedded signals are effectively ignored because `_commerce_signals` is already set.

**Q20:** Is scoring still an LLM call?
**A20:** In one-call path: NO (deterministic). In legacy path: YES (`score_draft()` calls LLM).

**Q21:** Can agent/tool execution create additional LLM calls?
**A21:** NO — `ai_agent_canary_enabled=False` by default, agent is not in one-call path.

**Q22:** What happens when OneCall returns malformed output?
**A22:** `validate_one_call_response()` returns `is_valid=False`. Worker sets `_llm_path = "legacy"`. Full legacy pipeline executes (up to 3 more LLM calls).

**Q23:** What happens when OneCall fails?
**A23:** Same as Q22 — fallback to legacy pipeline. Maximum 4 LLM calls total.

**Q24:** How many PG round trips occur?
**A24:** ~10-14 (10 in build_qwen3_context, 3-4 additional in process_message).

**Q25:** How many Redis round trips occur?
**A25:** ~12 (rate limit, debounce, lock, inbound, auto-reply, send/queue, ack, release, events).

**Q26:** What work is actually on the user-visible critical path?
**A26:** All LLM calls (2-4), context building (~10 PG queries), persona validation, send decision. Total: ~15-30 seconds typical.

**Q27:** What work is background-only?
**A27:** Profile extraction, summarization (both via `asyncio.create_task`).

**Q28:** Does the current runtime satisfy "one fan message → one Qwen2.5 generation"?
**A28:** NO. It executes 2 LLM calls, and the model is Qwen3:4b, not Qwen2.5.

**Q29:** If not, exactly why not?
**A29:** `extract_commerce_signals()` runs unconditionally at line 662, before the one-call branch at line 1043.

**Q30:** What is the smallest safe Stage B implementation required?
**A30:** Move `extract_commerce_signals()` inside the `if _llm_path == "legacy"` branch. Have one-call pipeline use its own embedded commerce signals (already generated by Qwen). Add commerce execution hook to one-call path for PPV.

---

## 32. FINAL VERDICT

```
PHASE 77 STAGE A VERDICT

ONE-CALL RUNTIME: FAIL (2 LLM calls, not 1)
QWEN2.5 RUNTIME: FAIL (model is qwen3:4b)
CONTEXT ENGINE INTEGRATION: FAIL (observational only)
RAPIDFUZZ PRODUCTION USE: FAIL (not in production path)
MINILM PRODUCTION USE: FAIL (not in production path)
HNSWLIB PRODUCTION USE: FAIL (not implemented)
MEMORY RETRIEVAL: PARTIAL (SQL keyword-only, reaches Qwen but no semantic)
COMMERCE PROPAGATION: PARTIAL (signals pass through, but commerce not executed in one-call)
PPV AUTHORITY: PASS (deterministic, LLM cannot override)
SCORING ELIMINATION: PASS (deterministic in one-call path)
FALLBACK SAFETY: FAIL (failure resurrects legacy 3-LLM pipeline, up to 4 calls)
PERSONA FIDELITY: PASS (compact persona, behavior block, deterministic validation)
CRITICAL-PATH LATENCY: FAIL (2 LLM calls sequential + ~10 PG queries)

P0: 3
P1: 3
P2: 3
P3: 2

ACTUAL SYNCHRONOUS LLM CALLS: 2
TARGET SYNCHRONOUS LLM CALLS: 1

FINAL VERDICT:
NOT READY

ROOT CAUSE:
extract_commerce_signals() at llm_worker.py:662 runs unconditionally BEFORE
the llm_path branch at line 1043. This makes the "one-call" pipeline actually
a two-call pipeline. Additionally, the one-call path does not execute commerce
(PPV creation, offer sending), making it revenue-inactive.

CURRENT RUNTIME:
Fan → debounce → Redis → LLM Worker
  → build_qwen3_context (legacy, ~10 PG queries)
  → extract_commerce_signals (LLM #1)
  → if llm_path=new:
      → one_call_generation (LLM #2)
      → validate (deterministic)
  → validate persona (deterministic)
  → send/handoff (deterministic)
  → Redis → Telethon

TARGET RUNTIME:
Fan → debounce → Redis → LLM Worker
  → Context Engine (RapidFuzz + MiniLM + hnswlib)
  → compact context
  → Qwen2.5 ONE generation (structured JSON)
  → Pydantic validation
  → deterministic authority
  → commerce execution (PPV if applicable)
  → send/handoff

MINIMUM REQUIRED STAGE B:
1. Move extract_commerce_signals() inside legacy branch only
2. Have one-call pipeline use embedded commerce signals (already in Qwen output)
3. Add commerce execution hook after one-call generation
4. Target: 1 LLM call + deterministic commerce execution
```
