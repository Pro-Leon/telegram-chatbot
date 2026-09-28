# Phase 75A: One-Call Qwen2.5 Migration — Forensic Audit

**Date:** September 1, 2026  
**Status:** STAGE A — FORENSIC RECONNAISSANCE ONLY  
**Worker:** `workers/llm_worker.py` (1656 lines)  
**Model:** Qwen3:4b via Ollama, num_ctx=8192

---

## 1. Executive Summary

The current 3-LLM pipeline processes each inbound fan message through:
1. **LLM #1** — Commerce signal extraction (advisory, ~18 fields)
2. **LLM #2** — Response generation (Qwen3:4b, main conversational output)
3. **LLM #3** — Response scoring (quality assessment + safety flags)

**Key finding:** LLM #1 and LLM #3 are both candidates for elimination or consolidation into the single Qwen2.5 generation call. LLM #1 produces advisory signals that can be generated as structured output alongside the reply. LLM #3's safety-critical checks are already deterministic keyword detection; its LLM quality scores can be replaced with deterministic heuristics.

**The one-call migration is SAFE TO PROCEED with the following design:**
- Qwen2.5 produces structured JSON containing both the reply AND commerce signals
- Deterministic validation replaces LLM scoring
- Existing commerce authority, persona enforcement, and PPV safety are fully preserved

---

## 2. Current Worker Verification

Verified against current `workers/llm_worker.py` (1656 lines, SHA256: `d7b157440d...`):

- B1 (profile reuse): ✅ Uses cached profile via `get_last_profile()`
- B2 (redundant PG): ✅ Dead reads removed, `derive_conversation_state` fixed
- B3 (PG parallel): ✅ `asyncio.gather` for independent reads
- B4 (Redis batching): ✅ `publish_events_batch` in use
- B5 (orjson): ✅ Already in event_bus.py
- B1-B7 restoration patches: ✅ All verified

---

## 3. Exact 3-Call Call Graph

```
Telegram inbound
    ↓
handlers / debounce (chatbotv2/handlers.py)
    ↓
Redis inbound stream (XADD inbound_messages)
    ↓
llm_worker.py: process_message()
    ↓
acquire_user_lock()                                    [Redis × 1]
    ↓
upsert_user() + is_user_auto_reply_excluded()          [PG × 2, parallel]
    ↓
resolve_single_application_creator()                   [Deterministic]
    ↓
get_structured_persona_async(creator_id)               [PG/Redis]
    ↓
build_qwen3_context()                                  [PG × 4-5, parallel gather]
    ↓
    ├── get_user()
    ├── get_user_profile()
    ├── get_recent_messages()
    ├── get_latest_summary_with_age()
    └── get_structured_persona_async() (if no snapshot)
    ↓
    + build_llm_context()                              [PG × ~10 internal]
    + list_valid_products()                            [PG × 2]
    + _get_purchased_product_ids()                     [PG × 1]
    + retrieve_relevant_memories()                     [Profile dict]
    + retrieve_relevant_knowledge() × 2                [Profile dict]
    + get_fan_knowledge()                              [Profile dict]
    ↓
publish_event("ai.generation_started")                 [Redis × 1]
    ↓
observe_context_engine()                               [Observational, fail-open]
    ↓
extract_commerce_signals(context)                      [LLM #1: cheap_model, temp=0.0]
    ↓
build_conversational_commerce_state()                  [PG × ~15 internal]
    ↓
get_timing_context() + get_behavioral_feedback_context() [PG × 2, parallel]
    ↓
_try_commerce_draft(signals=_commerce_signals)         [Commerce pipeline]
    ├── resolve_commerce_state()                       [Deterministic]
    ├── decide_commerce_action()                       [Deterministic]
    ├── build_strategy()                               [Deterministic]
    ├── execute_ppv() (if OFFER_PPV)                   [Deterministic + Dropfans API]
    └── generate_commerce_response()                   [LLM #2 alt: cheap_model, temp=0.0]
    ↓
(If no commerce draft)
    ↓
generate_draft(context, user_message)                  [LLM #2: qwen3:4b, temp=0.7]
    ↓
validate_persona_voice(draft, persona, behavior)       [Deterministic regex]
    ↓
score_draft(draft, user_message, context)              [LLM #3: cheap_model, temp=0.2]
    ↓
routing (auto-approve ≥0.80 + no flags / operator queue)
    ↓
enqueue_send() OR add_to_operator_queue()              [Redis × 1 or PG × 1]
    ↓
publish_events_batch()                                 [Redis × 1]
    ↓
post_process() (background: profile + summary)         [PG × 4, fire-and-forget]
    ↓
ack_inbound()                                          [Redis × 1]
    ↓
release_user_lock()                                    [Redis × 1]
```

---

## 4. LLM #1 Forensic Analysis — Commerce Signal Extraction

### Function
`commerce/deepseek.py:170` — `extract_commerce_signals(conversation_context)`

### Provider/Model
- **Provider:** `get_llm_provider()` → Ollama (default) or Gemini
- **Model:** `_settings.cheap_model` = `"gemini-flash-latest"`
- **Temperature:** `0.0` (deterministic)
- **Max tokens:** `1024`
- **Response format:** `application/json`

### Input
- `conversation_context`: list of `{role, content}` dicts
- Transformed by `compose_signal_extraction_input()` into flat transcript
- Max 30 messages, each truncated to 800 chars
- System messages skipped

### System Prompt (key elements)
```
You are a commerce signal analyzer for a fan-management system.
Output ONE JSON object with exactly these fields:
[purchase_intent, content_interest, relationship_engagement, price_interest,
 explicit_purchase_request, explicit_content_request, requested_price,
 declined_recent_offer, accepted_recent_offer, asks_for_free_content,
 negative_sentiment, conversation_relevance, confidence, evidence,
 model_uncertainty, primary_intent, intent_tags, negative_intent_tags,
 fan_asks_question, topic_continuity]
Rules: floats 0.0-1.0, no invented signals, no payment data in evidence,
strict primary_intent enum (17 values), max 5 evidence items at 240 chars.
Output ONLY the JSON object.
```

### Output Schema — `CommerceSignals` (18 fields)
| Field | Type | Authority |
|-------|------|-----------|
| `purchase_intent` | float [0,1] | Advisory |
| `content_interest` | float [0,1] | Advisory |
| `relationship_engagement` | float [0,1] | Advisory |
| `price_interest` | float [0,1] | Advisory |
| `explicit_purchase_request` | bool | Advisory |
| `explicit_content_request` | bool | Advisory |
| `requested_price` | float \| None | Advisory |
| `declined_recent_offer` | bool | Advisory |
| `accepted_recent_offer` | bool | Advisory |
| `asks_for_free_content` | bool | Advisory |
| `negative_sentiment` | float [0,1] | Advisory |
| `conversation_relevance` | float [0,1] | Advisory (dead field) |
| `confidence` | float [0,1] | Advisory |
| `evidence` | list[EvidenceItem] | Advisory |
| `model_uncertainty` | float [0,1] | Advisory |
| `primary_intent` | str (17 values) | Advisory |
| `intent_tags` | list[str] | Advisory |
| `negative_intent_tags` | list[str] | Advisory |
| `fan_asks_question` | bool | Advisory |
| `topic_continuity` | str \| None | Advisory (dead field) |

### Consumer Trace
All 18 fields are consumed through `signals_to_context()` → `CommerceDecisionContext` → deterministic `decide_commerce_action()`. **No field directly authorizes price, payment, or access.** The deterministic engine applies 14+ hard-deny rules before any offer can be made.

### Failure Behavior
Any parse/validation failure → `CommerceSignals.low_information()` fallback. **Never raises.**

### Can It Be Removed?
**YES.** All signals are advisory. Qwen2.5 can output these signals as structured fields alongside the reply text.

---

## 5. LLM #2 Forensic Analysis — Response Generation

### Functions
- `llm_worker.py:83` — `generate_draft(context_messages, user_message)`
- `llm_worker.py:156` — `generate_draft_with_tools(context_messages, user_message, auth)` (Gemini only)
- `commerce/deepseek_response.py:465` — `generate_commerce_response(input_)` (commerce path)

### Provider/Model
- **Provider:** `get_llm_provider()` → Ollama (default)
- **Model:** `qwen3:4b` (Ollama), `num_ctx=8192`, `num_predict=200`
- **Temperature:** `0.7` (non-thinking mode, overriding caller's 0.85)
- **Top_p:** `0.8, top_k=20, presence_penalty=1.5`

### Context Size
| Component | Tokens |
|-----------|--------|
| System prompt (persona + rules) | ~400 |
| Creator persona compact block | ~300 |
| State context | ~200 |
| Behavior block | ~60 |
| Vault content | ~30 |
| Relevant memory | ~60 |
| Fan knowledge | ~80 |
| Local time | ~20 |
| Recent messages (800 token budget) | ~800 |
| Current user message | ~100 |
| **Total** | **~2,150 tokens** |

**62% of num_ctx (8192) used**, leaving ~5,042 for output (but num_predict=200 limits output).

### Most Common Path
With default config (`llm_provider="ollama"`):
```
generate_draft_with_tools → Ollama doesn't support tools → fallback to generate_draft
→ OllamaProvider.generate_with_history() → qwen3:4b
```

### Tool/Agent Path
- `generate_draft_with_tools`: Only Gemini supports tool calling. Ollama falls back immediately.
- `run_agent_runtime`: Disabled by default (`ai_agent_canary_enabled=False`)
- **Actual tool calls in production: 0** (with default Ollama config)

### Failure Behavior
Provider failure → try next provider in comma-separated list → last resort is Ollama. Timeout: 120s.

---

## 6. LLM #3 Forensic Analysis — Scoring

### Function
`core/scoring.py:81` — `score_draft(draft, user_message, context, ...)`

### Provider/Model
- **Provider:** `get_llm_provider()` → Ollama (default)
- **Model:** `_settings.cheap_model` = `"gemini-flash-latest"`
- **Temperature:** `0.2`
- **Max tokens:** `512`
- **Response format:** `application/json`

### Input
- `draft`: The generated response text
- `user_message`: The inbound fan message
- `context`: Unused (passed but never read)

### System Prompt
```
Score this chat response on these criteria.
Return JSON: {
  "contextually_aware": 0-10,
  "natural_tone": 0-10,
  "appropriate_length": 0-10,
  "not_repetitive": 0-10,
  "flags": ["off_topic"|"too_formal"|"too_generic"|"breaks_persona"|"awkward_phrasing"|"repetitive"]
}
```

### Output Schema
- 4 numeric scores (0-10 each) → composite score (0.0-1.0)
- 6 quality flags (none are HARD_FLAGS)
- 5 deterministic keyword flags (ALREADY RUN BEFORE LLM CALL)

### Scoring Behavior Classification

| Behavior | Mechanism | Required After One-Call? | Deterministic Replacement? |
|----------|-----------|--------------------------|---------------------------|
| `price_mention` | Keyword regex | YES | Already deterministic |
| `personal_info_request` | Keyword regex | YES | Already deterministic |
| `distress_signal` | Keyword regex | YES | Already deterministic |
| `legal_mention` | Keyword regex | YES | Already deterministic |
| `photo_promise` | Keyword regex | YES | Already deterministic |
| `contextually_aware` | LLM score | Quality only | YES — embedding similarity |
| `natural_tone` | LLM score | Quality only | YES — rule-based |
| `appropriate_length` | LLM score | Quality only | YES — trivially deterministic |
| `not_repetitive` | LLM score | Quality only | YES — n-gram overlap |
| `off_topic` | LLM flag | Quality only | YES — embedding distance |
| `too_formal` | LLM flag | Quality only | YES — keyword rules |
| `too_generic` | LLM flag | Quality only | YES — pattern matching |
| `breaks_persona` | LLM flag | Telemetry only | Can be removed |
| `awkward_phrasing` | LLM flag | Telemetry only | Can be removed |
| `repetitive` | LLM flag | Telemetry only | Already have `not_repetitive` |
| `persona_identity_violation` | Dead code | Dead code | Dead code |
| `persona_question_policy_violation` | Dead code | Dead code | Dead code |
| `persona_voice_severe` | Dead code | Dead code | Dead code |
| `competitor_mention` | Dead code | Dead code | Dead code |

### Routing Impact
- Composite score ≥ 0.80 AND `not flags` → auto-approve
- ANY flag (even `off_topic`) → operator queue
- Scoring failure → composite = 0.0 → operator queue (fail-closed)

### Can LLM #3 Be Removed?
**YES.** The safety-critical keyword flags are already deterministic. The LLM quality scores can be replaced with deterministic heuristics (embedding similarity, pattern matching, length checks). The `not flags` gate can use deterministic flags only.

---

## 7. Responsibility Matrix

| Responsibility | LLM #1 | LLM #2 | LLM #3 | Deterministic Code | Target (One-Call) |
|---------------|:------:|:------:|:------:|:-----------------:|:-----------------:|
| Commerce intent detection | ✅ | — | — | — | **Qwen structured output** |
| Purchase intent scoring | ✅ | — | — | — | **Qwen structured output** |
| Price interest detection | ✅ | — | — | — | **Qwen structured output** |
| Content request detection | ✅ | — | — | — | **Qwen structured output** |
| Negative sentiment | ✅ | — | — | — | **Qwen structured output** |
| Conversation relevance | ✅ | — | — | — | **Qwen structured output** |
| Primary intent classification | ✅ | — | — | — | **Qwen structured output** |
| Reply generation | — | ✅ | — | — | **Qwen text output** |
| Naturalness | — | ✅ | — | — | Qwen (inherent) |
| Persona adherence | — | ✅ | — | ✅ (validation) | Qwen + deterministic |
| Response length | — | ✅ | ✅ | — | Qwen + deterministic |
| Context awareness | — | ✅ | ✅ | — | Qwen (inherent) |
| Repetition avoidance | — | — | ✅ | — | **Deterministic** |
| Safety (distress, legal, etc.) | — | — | — | ✅ (keyword) | **Deterministic** |
| Price mention safety | — | — | — | ✅ (keyword) | **Deterministic** |
| Commerce authority | — | — | — | ✅ | **Deterministic** (unchanged) |
| PPV authorization | — | — | — | ✅ | **Deterministic** (unchanged) |
| Persona validation | — | — | — | ✅ (regex) | **Deterministic** (unchanged) |
| Handoff decision | — | — | ✅ | ✅ (signals) | **Deterministic** |
| Confidence assessment | ✅ | — | ✅ | — | **Qwen structured output** |
| Question policy | — | — | — | ✅ | **Deterministic** (unchanged) |

---

## 8. Commerce/PPV Authority Analysis

### Complete PPV Path
```
fan message
    ↓
extract_commerce_signals()                    [LLM #1 → Advisory]
    ↓
signals_to_context()                          [Deterministic transformation]
    ↓
decide_commerce_action()                      [14+ hard-deny rules, then decision]
    ↓
build_strategy()                              [Deterministic mapping]
    ↓
execute_ppv()                                 [12 sequential authority gates]
    ├── Dropfans integration check (DB)
    ├── Credential decryption (Fernet)
    ├── Fan eligibility (DB)
    ├── Product existence (DB)
    ├── No pending offer (DB)
    ├── No purchase history (DB)
    ├── evaluate_ppv_eligibility() (pure function)
    ├── Checkout link resolution (DB/API)
    ├── Serialized idempotent INSERT (DB)
    ├── Funnel rollup (best-effort)
    └── Event publish (best-effort)
    ↓
generate_commerce_response()                  [LLM #2 alt: text generation only]
    ↓
send
```

### LLM Influence on PPV Path
| Authority Point | LLM Influence | Can Qwen Replace? |
|----------------|---------------|-------------------|
| Price | **NONE** — from DB `fangate_products.price_minor` | N/A |
| Payment state | **NONE** — from DB `commerce_offers.state` | N/A |
| Product identity | **NONE** — from DB `context.product_identity` | N/A |
| Eligibility | **INDIRECT** — signals feed `buying_intent_score` which reaches step 8/9 of decision engine, but 11 prior hard-deny rules run first | YES — Qwen can output `purchase_intent` and `explicit_purchase_request` |
| Offer creation | **NONE** — `execute_ppv()` re-validates all conditions | N/A |
| Checkout URL | **NONE** — from DB/Dropfans API | N/A |

### Key Safety Property
**The LLM can influence WHETHER an offer is attempted** (by producing high `purchase_intent`), but it **cannot influence WHAT is offered** (product, price, URL) or **bypass ANY hard deny-rule** (eligibility, cooldowns, budgets, blocked users, inactive integrations, missing credentials, already-purchased).

**PPV AUTHORITY PRESERVED: YES**

---

## 9. Persona Analysis

### Current Persona Pipeline
1. `get_structured_persona_async(creator_id)` — fetch structured persona (23 fields)
2. `build_qwen3_system_prompt()` — render persona into system prompt (~400 tokens)
3. `derive_persona_behavior_state()` — compute behavioral state (16 fields)
4. `render_persona_behavior_block()` — render behavior instruction (~60 tokens)
5. `validate_persona_voice()` — post-generation validation (deterministic regex)

### What Qwen Receives
- Full persona instruction (unless identity already established → trimmed to "You are {first}")
- Profile facts (name, age, location, occupation, interests, etc.)
- Funnel stage guidance
- Behavior block (emotion, confidence, mode, voice policy, question policy)

### Minimum Authoritative Representation for One-Call
The persona MUST be in the context for Qwen to generate persona-adherent responses. The behavior block MUST be present to enforce voice policies. The validation MUST remain deterministic.

**PERSONA ENFORCEMENT PRESERVED: YES** — no change to persona pipeline; context compact enough for one-call.

---

## 10. Context Engine Integration

### Current State
- Phase 73: Observational only, feature-gated, fail-open
- Phase 74A/74B: I/O optimization completed
- `context_engine_observational` config flag controls activation

### What It Currently Observes
- Context construction timing
- Memory retrieval candidates
- Deduplication stats
- Token budget usage

### What It Does Not Yet Provide
- Compact authoritative state bundle
- Semantic memory retrieval
- RapidFuzz fuzzy matching
- SentenceTransformer embeddings

### Future Integration Point
The Context Engine should ultimately provide a pre-compact context bundle that replaces the individual PG reads. This is separate from the one-call LLM migration.

---

## 11. Memory/Retrieval Analysis

### Data Reaching Qwen

| Data | Source | Classification | RapidFuzz/ST Candidate? |
|------|--------|---------------|------------------------|
| Recent messages | `get_recent_messages()` | Recent context | No — already recent |
| Conversation summary | `get_latest_summary_with_age()` | Derived state | No — already compact |
| Fan profile | `get_user_profile()` | Authoritative state | No — must be exact |
| Fan knowledge | Profile dict | Retrievable memory | YES — fuzzy match by topic |
| Long-term memory | Profile dict | Retrievable memory | YES — semantic retrieval |
| Commerce history | `build_llm_context()` | Authoritative state | No — must be exact |
| Persona | `get_structured_persona_async()` | Authoritative state | No — must be exact |
| Vault content | `list_valid_products()` | Authoritative state | No — must be exact |

### Future Memory Integration
- RapidFuzz: Fan knowledge retrieval by topic similarity
- SentenceTransformer: Long-term memory retrieval by semantic similarity
- hnswlib: Vector index for fast nearest-neighbor search

---

## 12. Tool-Call Analysis

### Current Tool Path
- `generate_draft_with_tools`: Gemini only. Ollama falls back to `generate_draft()`.
- `run_agent_runtime`: Disabled by default (`ai_agent_canary_enabled=False`)
- **With default Ollama config: 0 tool calls in production**

### 7 Registered Tools
| Tool | Mutates State? | Reachable on Ollama? |
|------|---------------|---------------------|
| `get_purchase_history` | No | No (Gemini only) |
| `get_active_offers` | No | No |
| `get_product_information` | No | No |
| `list_products` | No | No |
| `propose_follow_up` | Yes | No |
| `propose_product_offer` | Yes | No |
| `suggest_tip` | Yes | No |

### One-Call Compatibility
Tools are unreachable on Ollama. The one-call design does not need to preserve tool calling. If tools are needed later, they can be added as a separate path with Gemini.

---

## 13. Provider/Fallback Analysis

### Current Fallback Chain
```
settings.llm_provider (comma-separated)
    ↓
try provider[0] → on failure → try provider[1] → ... → last resort: ollama
```

### One-Call Implications
- One logical generation = one provider attempt
- Fallback creates a second physical call but is still one logical generation
- Telemetry already tracks `provider_name` per generation
- The `generation_id` remains the same across fallback attempts

### Telemetry Distinction
- `runtime_mode`: "legacy" (current) → could add "one_call" mode
- `provider_name`: tracks which provider served
- `tool_calls_count`: 0 for one-call path

---

## 14. Failure/Retry Analysis

| Failure | Current Behavior | One-Call Behavior |
|---------|-----------------|-------------------|
| LLM timeout | Try next provider | Same |
| Provider failure | Try next provider | Same |
| Malformed JSON (signals) | `CommerceSignals.low_information()` fallback | Deterministic fallback signals |
| Missing required field | Default value in Pydantic | Same validation |
| Invalid enum | Default "uncertain" | Same |
| Low confidence | Operator queue (via scoring) | Operator queue (via deterministic check) |
| Persona identity violation | `persona_validation_severe` flag → score penalty | Same deterministic check |
| Commerce inconsistency | Deterministic engine rejects | Same (unchanged) |
| Scoring failure | composite = 0.0 → operator queue | Deterministic scoring → operator queue if flags |
| Telegram failure | Existing retry semantics | Unchanged |

---

## 15. Latency Analysis

### Known Metrics
| Metric | Value | Source |
|--------|-------|--------|
| LLM #1 (signal extraction) | UNKNOWN — instrumentation insufficient | No p50/p95 in telemetry |
| LLM #2 (generation) | UNKNOWN | Same |
| LLM #3 (scoring) | UNKNOWN | Same |
| Context construction | ~15ms saved (Phase 74B) | Phase 74B report |
| PG round-trips | ~18 per generation | Phase 74B report |
| Redis round-trips | 7 per generation | Phase 74B report |

### Estimated Savings (3→1)
- LLM #1 elimination: ~1 provider RTT + generation time
- LLM #3 elimination: ~1 provider RTT + generation time
- **Estimated total savings: 2 × (network RTT + generation time)**
- For Ollama (remote): network RTT could be 100-500ms each
- **Conservative estimate: 200-1000ms savings per message**

---

## 16. Token/Context Analysis

### Current Token Usage
| Component | Tokens |
|-----------|--------|
| System prompt | ~400 |
| Persona block | ~300 |
| State context | ~200 |
| Behavior block | ~60 |
| Vault/memory/knowledge | ~190 |
| Recent messages | ~800 |
| User message | ~100 |
| **Total input** | **~2,050** |
| Max output (num_predict) | 200 |
| **Total context** | **~2,250 of 8,192** |

### One-Call Token Budget
With structured output (JSON), the output will be larger:
- Reply text: ~100-200 tokens
- Commerce signals: ~18 fields × ~5 tokens each = ~90 tokens
- Confidence: ~5 tokens
- **Total output: ~200-300 tokens**

This fits within `num_predict=200` only if we increase it to ~400. The `num_ctx=8192` has ample room.

---

## 17. Candidate One-Call Schema

```json
{
  "reply": "Hey! I was just thinking about you 😊",
  "commerce_signals": {
    "purchase_intent": 0.3,
    "content_interest": 0.7,
    "relationship_engagement": 0.8,
    "price_interest": 0.1,
    "explicit_purchase_request": false,
    "explicit_content_request": false,
    "requested_price": null,
    "declined_recent_offer": false,
    "asks_for_free_content": false,
    "negative_sentiment": 0.0,
    "confidence": 0.85,
    "primary_intent": "casual_conversation",
    "intent_tags": ["greeting", "rapport"],
    "negative_intent_tags": [],
    "fan_asks_question": false
  },
  "confidence": 0.82,
  "needs_handoff": false
}
```

### Field-by-Field Design

| Field | Type | Source Responsibility | Consumer | Validation | Authoritative? |
|-------|------|----------------------|----------|------------|---------------|
| `reply` | string | Qwen generation | Send/Telegram | Non-empty, length ≤ 2000 | No — advisory |
| `commerce_signals.*` | varies | Qwen structured output | Commerce pipeline | Pydantic CommerceSignals | No — advisory |
| `confidence` | float [0,1] | Qwen self-assessment | Routing decision | ≥ 0.0, ≤ 1.0 | No — advisory |
| `needs_handoff` | bool | Qwen assessment | Operator queue | Boolean | No — advisory |

---

## 18. Deterministic Validation Design

### Post-Generation Validation Chain
```
ONE Qwen call → structured JSON
    ↓
JSON parse + Pydantic validation (CommerceSignals schema)
    ↓
Deterministic keyword flags (distress, legal, price, personal_info, photo)
    ↓
Persona voice validation (existing validate_persona_voice)
    ↓
Commerce authority (existing decide_commerce_action + execute_ppv)
    ↓
Routing (existing threshold + flags)
    ↓
Send or operator queue
```

### Validation Layers
1. **JSON parse**: `json.loads()` + field presence check
2. **Type validation**: Pydantic `CommerceSignals` model
3. **Safety flags**: Existing `FLAG_KEYWORDS` regex (already deterministic)
4. **Persona validation**: Existing `validate_persona_voice()` (already deterministic)
5. **Commerce authority**: Existing `decide_commerce_action()` (unchanged)
6. **PPV execution**: Existing `execute_ppv()` (unchanged)

---

## 19. Scoring Replacement Analysis

### Option A: ONE QWEN CALL → deterministic validation
- Qwen outputs reply + signals + confidence
- Deterministic keyword flags for safety
- Deterministic persona validation
- Deterministic quality heuristics (length, repetition, formality)
- **Risk: Quality heuristics may be less nuanced than LLM scoring**

### Option B: ONE QWEN generation → existing score_draft()
- Qwen outputs reply + signals
- Existing LLM scoring remains
- **Risk: Still 2 LLM calls, doesn't achieve 3→1**

### Option C: Hybrid — ONE QWEN call → deterministic hard validation → scorer for ambiguous
- Qwen outputs reply + signals + confidence
- Deterministic safety + persona validation
- Scorer ONLY when confidence < 0.7 or ambiguous flags
- **Risk: Complexity; occasional 2-call path**

### Recommendation: OPTION A
The LLM quality scores (contextually_aware, natural_tone, appropriate_length, not_repetitive) are all replaceable with deterministic heuristics. The safety flags are already deterministic. The quality heuristics don't need LLM-level nuance — they just need to catch obviously bad responses.

---

## 20. Risks

| Risk | Severity | Likelihood | Mitigation | Test | Rollback |
|------|----------|------------|------------|------|----------|
| Structured output unreliability on Qwen2.5 | HIGH | MEDIUM | Pydantic validation + fallback to low_information() | Parse success rate test | Revert to 3-call |
| Lost commerce nuance | MEDIUM | LOW | Commerce signals are advisory; deterministic engine handles decisions | Commerce regression suite | Revert to 3-call |
| Lost scoring safety | HIGH | LOW | Safety flags are already deterministic; add keyword detection to one-call path | Safety flag test | Revert to 3-call |
| Larger single prompt | LOW | HIGH | Context already fits in 8192; output increase is ~100 tokens | Token count test | Increase num_predict |
| JSON generation overhead | LOW | MEDIUM | JSON mode already used for signals; reuse for one-call | Parse latency test | Adjust temperature |
| Confidence hallucination | MEDIUM | MEDIUM | Use confidence for routing only; don't trust for authority | Routing test | Threshold adjustment |
| Persona drift | MEDIUM | LOW | Behavior block + persona validation unchanged | Persona regression suite | Revert to 3-call |
| Tool path incompatibility | LOW | LOW | Tools unreachable on Ollama; one-call doesn't need tools | Tool path test | N/A |
| Provider fallback | LOW | LOW | Fallback creates 2 physical calls but 1 logical generation; telemetry tracks both | Fallback test | Existing semantics |

---

## 21. Benchmark Design

### Categories
1. Casual conversation
2. Emotional conversation
3. Persona-heavy conversation
4. Memory-dependent conversation
5. Commerce interest (low intent)
6. Price objection
7. Purchase intent (explicit)
8. PPV eligible
9. PPV ineligible (cooldown)
10. Handoff-required (distress)
11. Invalid/ambiguous input

### Measurements
| Metric | Baseline (3-call) | Target (1-call) |
|--------|-------------------|-----------------|
| LLM calls | 3 | 1 |
| Logical generations | 1 | 1 |
| Physical provider requests | 1-3 | 1 |
| Prompt tokens | ~2,050 | ~2,200 |
| Output tokens | ~150 (draft) | ~300 (JSON) |
| Latency p50 | UNKNOWN | Measure |
| Latency p95 | UNKNOWN | Measure |
| Commerce correctness | Existing | ≥ Existing |
| Persona fidelity | Existing | ≥ Existing |
| Handoff correctness | Existing | ≥ Existing |

---

## 22. Feature-Flag/Rollback Design

### Configuration
```python
# core/config.py
llm_pipeline: str = "three_call"  # "three_call" or "one_call"
```

### Branch Point
```python
# workers/llm_worker.py — after context construction
if _settings.llm_pipeline == "one_call":
    result = await one_call_generation(context, user_message)
    # result has .reply, .commerce_signals, .confidence, .needs_handoff
else:
    # existing 3-call path
    _commerce_signals = await extract_commerce_signals(context)
    draft = await generate_draft(context, user_message)
    score, flags = await score_draft(draft, user_message, context)
```

### Telemetry
- Add `llm_pipeline` field to `GenerationTelemetry`
- Track `pipeline_mode` alongside existing fields
- Compare metrics between pipelines in shadow mode

### Rollback
- Change `llm_pipeline` back to `"three_call"` — instant revert
- No data migration needed
- No schema changes

---

## 23. Test Plan

| Test | Description | Priority |
|------|-------------|----------|
| A. One-call contract | Structured Qwen response parses correctly | CRITICAL |
| B. Commerce preservation | All 18 signal fields reach deterministic engine | CRITICAL |
| C. PPV safety | Qwen cannot directly authorize offer/price | CRITICAL |
| D. Persona preservation | PersonaBehaviorState enforced via behavior block | CRITICAL |
| E. Identity | Qwen cannot substitute creator identity | CRITICAL |
| F. Handoff | Low confidence / invalid output reaches operator queue | HIGH |
| G. Malformed JSON | Safe fallback to low_information() | HIGH |
| H. Missing fields | Pydantic defaults applied | HIGH |
| I. Invalid commerce metadata | Deterministic rejection by decision engine | HIGH |
| J. Memory | Relevant retrieved memory in context | MEDIUM |
| K. Context size | Compact context within 8192 num_ctx | MEDIUM |
| L. Provider fallback | One-call generation observable during fallback | MEDIUM |
| M. Existing baseline | Current 3-call pipeline unchanged | CRITICAL |
| N. Deduplication | No change to idempotency | HIGH |
| O. XAUTOCLAIM | Recovered messages process correctly | HIGH |
| P. Restart | No new state breaks restart safety | HIGH |

---

## 24. Implementation Map Summary

| Phase | Objective | Risk | Dependencies |
|-------|-----------|------|-------------|
| 75B | One-call response contract (Pydantic schema) | LOW | None |
| 75C | Deterministic context compaction | LOW | None |
| 75D | Commerce signal integration into Qwen prompt | MEDIUM | 75B |
| 75E | Scoring replacement (deterministic heuristics) | MEDIUM | 75B |
| 75F | Qwen2.5 one-call pipeline implementation | HIGH | 75B-75E |
| 75G | Benchmark / shadow comparison | LOW | 75F |
| 75H | Controlled activation (feature flag) | MEDIUM | 75G |

---

## 25. Final Recommendation

### CURRENT LLM CALLS:
3 synchronous logical LLM calls for the normal reply path.

### TARGET LLM CALLS:
1 synchronous Qwen2.5 generation call producing structured JSON with reply + commerce signals + confidence.

### CAN 3 → 1 BE DONE SAFELY:
**YES**

### LLM #1 RESPONSIBILITIES:
Commerce signal extraction (18 advisory fields). All fields are advisory — consumed by deterministic engine through `signals_to_context()` mechanical transformation.

### LLM #2 RESPONSIBILITIES:
Response generation (plain text). This IS the one-call Qwen generation — it stays.

### LLM #3 RESPONSIBILITIES:
Quality scoring (4 numeric scores + 6 quality flags + 5 safety keyword flags). Safety flags are already deterministic. Quality scores replaceable with deterministic heuristics.

### RESPONSIBILITIES MOVED INTO DETERMINISTIC CODE:
- Scoring quality heuristics (length, repetition, formality, generic patterns)
- Safety keyword flags (already deterministic, just not used in one-call path yet)
- Confidence threshold routing (already deterministic)

### RESPONSIBILITIES MOVED INTO ONE QWEN2.5 CALL:
- Commerce signal extraction (as structured output fields)
- Response generation (as text output)
- Confidence self-assessment (as structured output field)

### RESPONSIBILITIES THAT MUST REMAIN SEPARATE:
- Persona behavior derivation (deterministic, separate computation)
- Persona voice validation (deterministic, post-generation)
- Commerce decision engine (deterministic, unchanged)
- PPV execution (deterministic, unchanged)
- Redis Streams / ACK / lock semantics (unchanged)

### PPV AUTHORITY PRESERVED:
YES — LLM output is advisory; deterministic engine + execute_ppv() re-validates all authority conditions.

### PERSONA ENFORCEMENT PRESERVED:
YES — behavior block in context + validate_persona_voice() post-generation.

### HANDOFF SAFETY PRESERVED:
YES — confidence threshold + deterministic keyword flags + operator queue routing.

### ARCHITECTURE REDESIGN REQUIRED:
NO

---

## PHASE 75A VERDICT

```
CURRENT:
3 synchronous logical LLM calls for the normal reply path.

TARGET:
1 synchronous Qwen2.5 generation call producing structured JSON.

ONE-CALL MIGRATION:
READY FOR STAGE B

PRIMARY LATENCY OPPORTUNITY:
Eliminating 2 LLM round-trips (signal extraction + scoring) saves
2 × (network RTT + generation time) = estimated 200-1000ms per message.

PRIMARY QUALITY RISK:
Qwen2.5 structured output reliability — mitigated by Pydantic validation
and fallback to low_information() signals.

PRIMARY COMMERCE RISK:
None — all commerce authority is deterministic and unchanged.

PRIMARY PERSONA RISK:
None — persona pipeline unchanged; behavior block + validation preserved.

PRIMARY IMPLEMENTATION RISK:
JSON generation overhead for structured output — mitigated by JSON mode
and existing CommerceSignals schema.

RECOMMENDED NEXT STAGE:
75B — One-call response contract (Pydantic schema design)

PRODUCTION CHANGES:
NONE

SCHEMA CHANGES:
NONE

REDIS CHANGES:
NONE

CANARY CHANGES:
NONE
```
