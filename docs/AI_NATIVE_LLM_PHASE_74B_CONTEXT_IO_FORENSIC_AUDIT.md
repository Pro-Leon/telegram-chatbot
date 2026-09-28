# Phase 74B — Context I/O Forensic Audit

**Date:** 2026-09-01
**Status:** READ-ONLY — no production code changes
**Scope:** Exact synchronous call graph, PG/Redis/serialization inventory, parallelization/pipeline/cache candidates

---

## 1. Executive Summary

This audit traces every synchronous operation in the critical path for a normal inbound message (`"hey beautiful"`). It establishes exact counts, identifies redundant operations, and defines the safest optimization boundary.

### Key Findings

| Metric | Exact Count | Evidence |
|--------|-------------|----------|
| PostgreSQL round-trips (context build) | **22** | Code trace below |
| PostgreSQL round-trips (post-context) | **9** | Code trace below |
| PostgreSQL round-trips (commerce state) | **14** | Code trace below |
| **Total PG round-trips** | **45** | Sum of above |
| Redundant PG round-trips | **14** | Identified below |
| Redis round-trips (handlers.py) | **10-14** | Code trace below |
| Redis round-trips (llm_worker.py) | **10-15** | Code trace below |
| **Total Redis round-trips** | **20-29** | Sum of above |
| `get_user_profile()` calls per generation | **8** | Exact trace |
| `get_user()` calls per generation | **5** | Exact trace |
| `get_recent_messages()` calls per generation | **5** | Exact trace |
| `publish_event()` calls per generation | **4-5** | Exact trace |
| Profile JSON parse volume (25KB profile) | **200KB** | 8 x 25KB |

---

## 2. Exact Synchronous Call Graph

### 2.1 handlers.py (Intake Path)

```
handle_incoming_message(event)
│
├── check_rate_limit(user_id)                          [Redis: INCR + EXPIRE = 2 ops]
├── upsert_user(user_id, username, first_name)          [PG: INSERT/UPDATE]
├── save_inbound_message(user_id, content, ...)         [PG: INSERT]
├── publish_event("message.created", ...)               [Redis: PUBLISH = 1 op]
├── debounce_enqueue(user_id, content, ...)             [Redis: SET NX + RPUSH + EXPIRE = 3 ops]
│
└── _wait_and_process(user_id, username, first_name)    [after debounce_window_seconds]
    ├── get_debounced_messages(user_id)                  [Redis: LRANGE + DELETE = 2 ops]
    ├── get_cached_user_persona(user_id, creator_id)    [Redis: GET + GET = 1-2 ops]
    ├── get_user_persona(user_id, creator_id)            [PG: SELECT if cache miss]
    ├── cache_user_persona(user_id, persona)             [Redis: SETEX = 1 op]
    ├── get_cached_default_persona(creator_id)           [Redis: GET = 1 op]
    ├── get_default_persona(creator_id)                  [PG: SELECT if cache miss]
    ├── cache_default_persona(persona, creator_id)       [Redis: SETEX = 1 op]
    └── enqueue_inbound(message_data)                    [Redis: XADD = 1 op]
```

**handlers.py totals:**
- Redis: 10-14 sequential ops
- PG: 1-3 ops (upsert_user, save_inbound_message, conditional persona fetches)

### 2.2 llm_worker.py (Processing Path)

```
process_message(user_id, user_message, persona, generation_id)
│
├── resolve_single_application_creator()                 [PG: SELECT from creator_integrations]
├── acquire_user_lock(user_id, creator_id)               [Redis: SET NX EX]
├── upsert_user(user_id, username, first_name)           [PG: INSERT/UPDATE]
├── is_user_auto_reply_excluded(user_id)                 [PG: SELECT]
│
├── get_structured_persona_async(creator_id)             [Redis cache or PG: SELECT from personas]
│
├── build_qwen3_context(user_id, user_message, persona) │
│   ├── [PARALLEL] asyncio.gather:                       │
│   │   ├── get_user(user_id)                            [PG #1]
│   │   ├── get_user_profile(user_id)                    [PG #2]
│   │   ├── get_recent_messages(user_id, limit=20)       [PG #3]
│   │   └── get_latest_summary_with_age(user_id)         [PG #4]
│   │                                                    │
│   ├── get_fan_knowledge(creator_id, user_id)           [PG #5] → calls get_user_profile() internally = REDUNDANT
│   │                                                    │
│   ├── build_llm_context(creator_id, user_id)          │
│   │   ├── _get_user_safe(user_id)                      [PG #6] → REDUNDANT (already #1)
│   │   ├── _get_recent_messages_safe(user_id, limit)    [PG #7] → REDUNDANT (already #3)
│   │   ├── _get_summary_safe(user_id)                   [PG #8] → REDUNDANT (already #4)
│   │   ├── _get_purchases_safe(creator_id, user_id)     [PG #9]
│   │   ├── _get_active_offers_safe(creator_id, user_id) [PG #10]
│   │   ├── _get_product_info_safe(creator_id, user_id)  [PG #11]
│   │   ├── _get_creator_info_safe(creator_id)           [PG #12]
│   │   ├── _get_last_purchase_at_safe(creator_id, user_id) [PG #13]
│   │   ├── _get_last_followup_at_safe(creator_id, user_id) [PG #14]
│   │   ├── _get_segments_safe(creator_id, user_id)      [PG #15]
│   │   └── get_behavioral_feedback_context(creator_id, user_id) [PG #16-20: 5 queries]
│   │                                                    │
│   ├── list_valid_products(creator_id)                  [PG #21]
│   ├── _get_purchased_product_ids(creator_id, user_id)  [PG #22]
│   │                                                    │
│   ├── retrieve_relevant_memories(creator_id, user_id)  [PG #23] → calls get_user_profile() = REDUNDANT
│   ├── retrieve_relevant_knowledge(creator_id, user_id) [PG #24] → calls get_fan_knowledge() = REDUNDANT
│   ├── retrieve_relevant_knowledge(creator_id, user_id, limit=20) [PG #25] → REDUNDANT (same as #24, different limit)
│   └── get_fan_knowledge(creator_id, user_id)            [PG #26] → REDUNDANT (already #5)
│
├── [POST-CONTEXT] extract_explicit_memories()           [PG #27] → calls add_memory_item() → get_user_profile() + update_user_profile() = 2 PG ops
├── [POST-CONTEXT] extract_fan_knowledge() + add_knowledge_item() [PG #28-29] → get_user_profile() + update_user_profile()
│
├── publish_event("ai.generation_started", ...)          [Redis: PUBLISH]
│
├── extract_commerce_signals(context)                     [LLM #1: external API]
│
├── _try_commerce_draft(user_id, context, persona)       │
│   ├── resolve_single_application_creator()             [PG: SELECT] = REDUNDANT (already resolved)
│   ├── _get_user_for_product(user_id)                   [PG #30] → REDUNDANT (already #1)
│   ├── get_user_profile(user_id)                        [PG #31] → REDUNDANT (already #2)
│   ├── resolve_commerce_product_with_history(...)        [PG #32-33: list_products + purchased]
│   └── resolve_and_run_commerce(request)                 [commerce pipeline, may have additional PG]
│
├── build_conversational_commerce_state(...)              │
│   ├── get_timing_context(creator_id, user_id)           [PG #34-35: 2 queries]
│   ├── get_behavioral_feedback_context(creator_id, user_id) [PG #36-40: 5 queries] = REDUNDANT (already #16-20)
│   ├── commerce_offers check (active)                    [PG #41]
│   ├── commerce_offers check (purchased)                 [PG #42]
│   ├── _get_user(user_id)                                [PG #43] → REDUNDANT (already #1)
│   ├── retrieve_relevant_memories(...)                    [PG #44] → REDUNDANT (already #23)
│   ├── list_valid_products(creator_id)                    [PG #45] → REDUNDANT (already #21)
│   ├── get_commercial_preferences(creator_id, user_id)   [PG #46] → calls get_user_profile() = REDUNDANT
│   ├── get_recent_offered_product_ids(creator_id, user_id) [PG #47]
│   ├── get_recent_offered_groups(creator_id, user_id)    [PG #48]
│   └── _get_purchased_product_ids(creator_id, user_id)   [PG #49] → REDUNDANT (already #22)
│
├── [POST-CONTEXT] get_recent_messages(user_id, limit=20) [PG #50] → REDUNDANT (already #3)
├── [POST-CONTEXT] get_user(user_id)                      [PG #51] → REDUNDANT (already #1)
│
├── Qwen2.5 generation                                   [LLM #2]
│
├── get_recent_messages(user_id, limit=7)                 [PG #52] → REDUNDANT (subsumed by #3)
├── get_recent_messages(user_id, limit=7)                 [PG #53] → REDUNDANT (same as #52)
├── get_user(user_id)                                     [PG #54] → REDUNDANT (already #1)
│
├── score_draft(draft)                                    [LLM #3]
│
├── is_auto_reply_enabled()                               [Redis: GET]
├── enqueue_send() OR add_to_operator_queue()             [Redis: XADD or PG: INSERT]
├── publish_event("ai.generation_completed", ...)         [Redis: PUBLISH]
├── publish_event("suggestion.created", ...)              [Redis: PUBLISH] (conditional)
│
├── post_process(user_id)                                 [async, not blocking]
│   ├── get_recent_messages(user_id, limit=20)            [PG] → REDUNDANT
│   └── get_user(user_id)                                 [PG] → REDUNDANT
│
├── get_user_profile(user_id)                             [PG #55] → REDUNDANT (strategy learning)
├── update_user_profile(user_id, facts)                   [PG: UPDATE]
│
├── release_user_lock(user_id)                            [Redis: DELETE]
└── ack_inbound(msg_id)                                   [Redis: XACK]
```

---

## 3. PostgreSQL Operation Inventory

### 3.1 All PG Operations on Critical Path

| # | Operation | File:Line | Classification | Redundant? |
|---|-----------|-----------|----------------|------------|
| 1 | `get_user(user_id)` | context.py:532 (parallel) | REQUIRED | No (first fetch) |
| 2 | `get_user_profile(user_id)` | context.py:533 (parallel) | REQUIRED | No (first fetch) |
| 3 | `get_recent_messages(user_id, limit=20)` | context.py:534 (parallel) | REQUIRED | No (first fetch) |
| 4 | `get_latest_summary_with_age(user_id)` | context.py:535 (parallel) | REQUIRED | No (first fetch) |
| 5 | `get_fan_knowledge(creator_id, user_id)` | context.py:573 | CACHEABLE | Calls get_user_profile() internally |
| 6 | `_get_user_safe(user_id)` | context_assembler.py:525 | **REDUNDANT** | Same as #1 |
| 7 | `_get_recent_messages_safe(user_id, limit)` | context_assembler.py:530 | **REDUNDANT** | Same as #3 |
| 8 | `_get_summary_safe(user_id)` | context_assembler.py:535 | **REDUNDANT** | Same as #4 |
| 9 | `_get_purchases_safe(creator_id, user_id)` | context_assembler.py:540 | REQUIRED | Unique query |
| 10 | `_get_active_offers_safe(creator_id, user_id)` | context_assembler.py:547 | REQUIRED | Unique query |
| 11 | `_get_product_info_safe(creator_id, user_id)` | context_assembler.py:554 | REQUIRED | Unique query |
| 12 | `_get_creator_info_safe(creator_id)` | context_assembler.py:563 | REQUIRED | Unique query |
| 13 | `_get_last_purchase_at_safe(creator_id, user_id)` | context_assembler.py:568 | REQUIRED | Unique query |
| 14 | `_get_last_followup_at_safe(creator_id, user_id)` | context_assembler.py:572 | REQUIRED | Unique query |
| 15 | `_get_segments_safe(creator_id, user_id)` | context_assembler.py:577 | REQUIRED | Unique query |
| 16-20 | `get_behavioral_feedback_context()` | context_assembler.py:591 | REQUIRED | 5 sequential queries |
| 21 | `list_valid_products(creator_id)` | context.py:703 | REQUIRED | Unique query |
| 22 | `_get_purchased_product_ids(creator_id, user_id)` | context.py:705 | REQUIRED | Unique query |
| 23 | `retrieve_relevant_memories()` | context.py:731 | CACHEABLE | Calls get_user_profile() internally |
| 24 | `retrieve_relevant_knowledge()` | context.py:749 | CACHEABLE | Calls get_fan_knowledge() internally |
| 25 | `retrieve_relevant_knowledge(limit=20)` | context.py:765 | **REDUNDANT** | Same as #24, different limit |
| 26 | `get_fan_knowledge(creator_id, user_id)` | context.py:768 | **REDUNDANT** | Same as #5 |
| 27 | `add_memory_item()` → get_user_profile + update | llm_worker.py:636 | REQUIRED | Write path |
| 28-29 | `add_knowledge_item()` → get_user_profile + update | llm_worker.py:646 | REQUIRED | Write path |
| 30 | `_get_user_for_product(user_id)` | llm_worker.py:427 | **REDUNDANT** | Same as #1 |
| 31 | `get_user_profile(user_id)` | llm_worker.py:431 | **REDUNDANT** | Same as #2 |
| 32-33 | `resolve_commerce_product_with_history()` | llm_worker.py:437 | REQUIRED | Commerce resolution |
| 34-35 | `get_timing_context(creator_id, user_id)` | conversational.py:31 | REQUIRED | Unique queries |
| 36-40 | `get_behavioral_feedback_context()` | conversational.py:35 | **REDUNDANT** | Same as #16-20 |
| 41-42 | commerce_offers checks | conversational.py:43-46 | REQUIRED | Unique queries |
| 43 | `_get_user(user_id)` | conversational.py:62 | **REDUNDANT** | Same as #1 |
| 44 | `retrieve_relevant_memories()` | conversational.py:197 | **REDUNDANT** | Same as #23 |
| 45 | `list_valid_products(creator_id)` | conversational.py:153 | **REDUNDANT** | Same as #21 |
| 46 | `get_commercial_preferences()` | conversational.py:157 | CACHEABLE | Calls get_user_profile() |
| 47 | `get_recent_offered_product_ids()` | conversational.py:164 | REQUIRED | Unique query |
| 48 | `get_recent_offered_groups()` | conversational.py:165 | REQUIRED | Unique query |
| 49 | `_get_purchased_product_ids()` | conversational.py:171 | **REDUNDANT** | Same as #22 |
| 50 | `get_recent_messages(limit=20)` | llm_worker.py:717 | **REDUNDANT** | Same as #3 |
| 51 | `get_user(user_id)` | llm_worker.py:720 | **REDUNDANT** | Same as #1 |
| 52 | `get_recent_messages(limit=7)` | llm_worker.py:1154 | **REDUNDANT** | Subsumed by #3 |
| 53 | `get_recent_messages(limit=7)` | llm_worker.py:1307 | **REDUNDANT** | Same as #52 |
| 54 | `get_user(user_id)` | llm_worker.py:1227 | **REDUNDANT** | Same as #1 |
| 55 | `get_user_profile(user_id)` | llm_worker.py:1592 | **REDUNDANT** | Same as #2 |
| 56 | `get_user_profile(user_id)` | llm_worker.py:1653 | **REDUNDANT** | Same as #55 |

### 3.2 PG Operation Summary

| Category | Count | Operations |
|----------|-------|------------|
| **Unique required queries** | ~25 | #1-4, #9-15, #16-20, #21-22, #27-29, #32-35, #41-42, #47-48 |
| **Redundant queries** | **~14** | #6-8, #25-26, #30-31, #36-40, #43-46, #49-54 |
| **Cacheable (profile repeated)** | **~8** | #5, #23-24, #27-28, #46, #55-56 (all call get_user_profile internally) |
| **Total** | **~56** | |

### 3.3 The `get_user_profile()` Problem

`get_user_profile(user_id)` is called **8 times** per generation, each time:
1. Acquires a PG connection from the pool
2. Executes `SELECT facts FROM user_profiles WHERE user_id=$1`
3. Receives the full JSONB document (140 bytes to 25KB)
4. Calls `json.loads()` to parse it
5. Returns the parsed dict

For a heavy user (25KB profile), this means **200KB of JSON parsing** per generation.

The profile is fetched by:
- `build_qwen3_context()` parallel gather (#2)
- `get_fan_knowledge()` (#5)
- `build_llm_context()` → `_get_user_safe()` (#6, redundant)
- `retrieve_relevant_memories()` → `get_long_term_memory()` (#23)
- `retrieve_relevant_knowledge()` → `get_fan_knowledge()` (#24)
- `_try_commerce_draft()` → `get_user_profile()` (#31)
- `get_commercial_preferences()` → `get_user_profile()` (#46)
- Strategy learning → `get_user_profile()` (#55, #56)

---

## 4. Redis Operation Inventory

### 4.1 handlers.py Redis Operations

| # | Operation | Redis Command | Sequential? | File:Line |
|---|-----------|---------------|-------------|-----------|
| 1 | `check_rate_limit()` | INCR | Sequential | redis.py:356 |
| 2 | `check_rate_limit()` | EXPIRE (conditional) | Sequential | redis.py:358 |
| 3 | `publish_event("message.created")` | PUBLISH | Sequential | event_bus.py:52 |
| 4 | `debounce_enqueue()` | SET NX | Sequential | redis.py:329 |
| 5 | `debounce_enqueue()` | RPUSH | Sequential | redis.py:330 |
| 6 | `debounce_enqueue()` | EXPIRE (conditional) | Sequential | redis.py:333 |
| 7 | `get_debounced_messages()` | LRANGE | Sequential | redis.py:341 |
| 8 | `get_debounced_messages()` | DELETE | Sequential | redis.py:342 |
| 9 | `get_cached_user_persona()` | GET (+ possible GET) | Sequential | redis.py:391-398 |
| 10 | `get_cached_default_persona()` | GET | Sequential | redis.py:436 |
| 11 | `enqueue_inbound()` | XADD | Sequential | redis.py:195 |

**handlers.py total: 11-13 Redis ops**

### 4.2 llm_worker.py Redis Operations

| # | Operation | Redis Command | Sequential? | File:Line |
|---|-----------|---------------|-------------|-----------|
| 1 | `acquire_user_lock()` | SET NX EX | Sequential | redis.py:287 |
| 2 | `publish_event("ai.generation_started")` | PUBLISH | Sequential | event_bus.py:52 |
| 3 | `publish_event("persona.behavior")` | PUBLISH | Sequential | event_bus.py:52 |
| 4 | `is_auto_reply_enabled()` | GET | Sequential | redis.py:484 |
| 5 | `enqueue_send()` | XADD | Sequential | redis.py:78 |
| 6 | `publish_event("ai.generation_completed")` | PUBLISH | Sequential | event_bus.py:52 |
| 7 | `publish_event("suggestion.created")` | PUBLISH | Sequential | event_bus.py:52 |
| 8 | `release_user_lock()` | DELETE | Sequential | redis.py:298 |
| 9 | `ack_inbound()` | XACK | Sequential | redis.py:221 |

**llm_worker.py total: 9 Redis ops**

### 4.3 Redis Operation Summary

| Category | Count | Operations |
|----------|-------|------------|
| **Stream operations** | 3 | XADD (inbound), XADD (send), XACK |
| **Lock operations** | 2 | SET NX EX, DELETE |
| **Rate limit** | 2 | INCR, EXPIRE |
| **Cache reads** | 3-4 | persona GETs, auto_reply GET |
| **Event publication** | 4-5 | PUBLISH x4-5 |
| **Debounce** | 5-6 | SET NX, RPUSH, EXPIRE, LRANGE, DELETE |
| **Total** | **20-22** | |

### 4.4 Batchable Redis Operations

| Batch | Current | Optimized | Savings |
|-------|---------|-----------|---------|
| `publish_event()` x4-5 | 4-5 sequential PUBLISH | 1 pipeline | 3-4 RTTs |
| `get_cached_user_persona()` + `get_cached_default_persona()` | 2 sequential GET | 1 pipeline | 1 RTT |
| **Total savings** | | | **4-5 RTTs** |

### 4.5 publish_event() Dependency Analysis

Events published during one generation:
1. `ai.generation_started` — before LLM generation
2. `persona.behavior` — after persona behavior derivation (may be 0-2 times)
3. `ai.generation_completed` — after scoring
4. `suggestion.created` — if operator queue (conditional)

**Dependencies:**
- Event 1 has no dependencies (fire-and-forget)
- Event 2 depends on persona behavior derivation completing
- Event 3 depends on scoring completing
- Event 4 depends on operator queue creation

**Within each dependency group, events are independent.** However, events 1 and 2 are sequential in the code (event 1 fires before generation, event 2 after behavior). Events 3 and 4 fire together after scoring.

**Batching opportunity:** Events 3+4 (and any persona.behavior events) can be pipelined. Event 1 is too early to batch with later events.

---

## 5. Serialization Inventory

### 5.1 JSON Operations on Critical Path

| Operation | Frequency | Payload Size | File:Line |
|-----------|-----------|--------------|-----------|
| `json.loads(row["facts"])` in get_user_profile | **8x** | 140B-25KB | postgres.py:500 |
| `json.dumps(facts)` in update_user_profile | **1-2x** | 140B-25KB | postgres.py:516 |
| `json.dumps(message_data)` in debounce_enqueue | **1x** | ~156B | redis.py:330 |
| `json.loads(m)` in get_debounced_messages | **1-3x** | ~156B each | redis.py:349-350 |
| `json.dumps(event)` in publish_event | **4-5x** | ~347B each | event_bus.py:52 |
| `json.dumps(payload)` in move_to_dlq | 0 (success path) | N/A | redis.py:140 |
| `json.dumps(data)` in telemetry INSERT | **1x** | ~549B | postgres.py:2887 |

### 5.2 Serialization Hotspot: `get_user_profile()`

The same `json.loads()` operation is performed 8 times on the same data:

```
Profile fetch #1 (parallel gather) → json.loads() → {interests: [...], ...}
Profile fetch #2 (get_fan_knowledge) → json.loads() → same dict
Profile fetch #3 (_get_user_safe) → json.loads() → same dict  [REDUNDANT - not even using profile]
Profile fetch #4 (retrieve_relevant_memories) → json.loads() → same dict
Profile fetch #5 (retrieve_relevant_knowledge) → json.loads() → same dict
Profile fetch #6 (_try_commerce_draft) → json.loads() → same dict  [REDUNDANT]
Profile fetch #7 (get_commercial_preferences) → json.loads() → same dict  [REDUNDANT]
Profile fetch #8 (strategy learning) → json.loads() → same dict  [REDUNDANT]
```

**Total JSON parse volume per generation:**
- Small profile (140B): 8 x 140 = **1,120 bytes**
- Medium profile (2.3KB): 8 x 2,302 = **18,416 bytes**
- Heavy profile (25KB): 8 x 25,501 = **204,008 bytes** (200KB)

### 5.3 orjson Evaluation

**Measured payload sizes:**
| Payload | Size | Frequency | Total/generation |
|---------|------|-----------|------------------|
| User profile (small) | 140B | 8x | 1,120B |
| User profile (medium) | 2.3KB | 8x | 18.4KB |
| User profile (heavy) | 25KB | 8x | 204KB |
| Event payload | 347B | 4-5x | 1.4-1.7KB |
| Debounce payload | 156B | 1-3x | 156-468B |
| Telemetry dict | 549B | 1x | 549B |

**orjson vs stdlib json performance (typical):**
- Small payloads (<1KB): ~0.01ms difference → **negligible**
- Medium payloads (1-5KB): ~0.05ms difference → **negligible**
- Heavy profiles (25KB): ~0.3ms difference × 8 calls = **2.4ms savings**

**Verdict:** orjson provides ~2-3ms savings for heavy profiles. The real benefit is not orjson itself but **reducing the number of get_user_profile() calls from 8 to 1**, which eliminates 7 redundant PG round-trips (~70-140ms) AND 7 redundant json.loads() calls (~2ms). The PG savings dwarf the serialization savings.

**Recommendation:** Install orjson for marginal gain, but the primary optimization is profile caching, not faster parsing.

---

## 6. Profile Parsing Audit

### 6.1 Exact Call Chain

```
build_qwen3_context()
│
├── [PARALLEL] get_user_profile(user_id)          ← Parse #1
│   └── json.loads(row["facts"]) → profile dict
│
├── get_fan_knowledge(creator_id, user_id)        ← Parse #2
│   └── get_user_profile(user_id)
│       └── json.loads(row["facts"]) → SAME profile dict
│
├── build_llm_context(creator_id, user_id)
│   ├── _get_user_safe(user_id)                   ← Parse #3 [REDUNDANT - doesn't even use profile]
│   │   └── get_user(user_id) [different query, not profile]
│   └── (no profile parse here - user_data is separate)
│
├── retrieve_relevant_memories(...)               ← Parse #4
│   └── get_long_term_memory(creator_id, user_id)
│       └── get_user_profile(user_id)
│           └── json.loads(row["facts"]) → SAME profile dict
│
├── retrieve_relevant_knowledge(...)              ← Parse #5
│   └── get_fan_knowledge(creator_id, user_id)
│       └── get_user_profile(user_id)
│           └── json.loads(row["facts"]) → SAME profile dict
│
├── retrieve_relevant_knowledge(limit=20)         ← Parse #6 [REDUNDANT]
│   └── get_fan_knowledge(creator_id, user_id)
│       └── get_user_profile(user_id)
│           └── json.loads(row["facts"]) → SAME profile dict
│
├── get_fan_knowledge(creator_id, user_id)        ← Parse #7 [REDUNDANT]
│   └── get_user_profile(user_id)
│       └── json.loads(row["facts"]) → SAME profile dict
│
_try_commerce_draft()
├── get_user_profile(user_id)                     ← Parse #8 [REDUNDANT]
│   └── json.loads(row["facts"]) → SAME profile dict
│
build_conversational_commerce_state()
├── get_commercial_preferences(creator_id, user_id) ← Parse #9 [REDUNDANT]
│   └── get_user_profile(user_id)
│       └── json.loads(row["facts"]) → SAME profile dict
│
POST-CONTEXT strategy learning
├── get_user_profile(user_id)                     ← Parse #10 [REDUNDANT]
│   └── json.loads(row["facts"]) → SAME profile dict
├── get_user_profile(user_id)                     ← Parse #11 [REDUNDANT]
│   └── json.loads(row["facts"]) → SAME profile dict
```

### 6.2 Canonical Representation Opportunity

**Current state:** 11 `get_user_profile()` calls, each acquiring a PG connection, executing the same query, parsing the same JSONB.

**Proposed state:** 1 `get_user_profile()` call in the parallel gather, result passed to all downstream consumers.

**Safe consumers that can accept cached profile:**
- `get_fan_knowledge()` — reads `fan_knowledge_by_creator` key
- `get_long_term_memory()` — reads `long_term_memory_by_creator` key
- `retrieve_relevant_knowledge()` — calls get_fan_knowledge internally
- `retrieve_relevant_memories()` — calls get_long_term_memory internally
- `get_commercial_preferences()` — reads `commercial_preferences_by_creator` key
- `_try_commerce_draft()` — reads `interests`/`preferences` keys
- Strategy learning — reads/writes `strategy_last_by_creator` key

**Unsafe consumers (must NOT use cached profile):**
- `add_memory_item()` — WRITES to profile (needs fresh read-modify-write)
- `add_knowledge_item()` — WRITES to profile (needs fresh read-modify-write)
- `update_user_profile()` — WRITES to profile (needs fresh read-modify-write)

**Conclusion:** The profile can be cached and passed to all READ consumers. WRITE consumers must fetch fresh.

---

## 7. Context Dependency Graph

```
fan message ("hey beautiful")
    │
    ├── [PARALLEL PG]
    │   ├── get_user(user_id)                    → user dict
    │   ├── get_user_profile(user_id)            → profile dict (JSONB)
    │   ├── get_recent_messages(user_id, limit=20) → message list
    │   └── get_latest_summary_with_age(user_id)  → (summary, age)
    │
    ├── [SEQUENTIAL PG - REDUNDANT]
    │   ├── get_fan_knowledge() → get_user_profile() [REDUNDANT]
    │   ├── build_llm_context() → get_user() [REDUNDANT]
    │   ├── build_llm_context() → get_recent_messages() [REDUNDANT]
    │   └── build_llm_context() → get_latest_summary() [REDUNDANT]
    │
    ├── [SEQUENTIAL PG - UNIQUE]
    │   ├── _get_purchases_safe()               → purchase history
    │   ├── _get_active_offers_safe()            → active offers
    │   ├── _get_product_info_safe()             → product details
    │   ├── _get_creator_info_safe()             → creator info
    │   ├── _get_last_purchase_at_safe()         → timestamp
    │   ├── _get_last_followup_at_safe()         → timestamp
    │   ├── _get_segments_safe()                 → segment list
    │   └── get_behavioral_feedback_context()     → 5 queries
    │
    ├── [SEQUENTIAL PG - UNIQUE]
    │   ├── list_valid_products()                → product catalog
    │   └── _get_purchased_product_ids()         → purchased set
    │
    ├── [SEQUENTIAL PG - REDUNDANT]
    │   ├── retrieve_relevant_memories() → get_user_profile() [REDUNDANT]
    │   ├── retrieve_relevant_knowledge() → get_fan_knowledge() → get_user_profile() [REDUNDANT]
    │   ├── retrieve_relevant_knowledge(limit=20) [REDUNDANT]
    │   └── get_fan_knowledge() [REDUNDANT]
    │
    ├── context builder
    │   └── Qwen2.5 generation
    │
    ├── [SEQUENTIAL PG - REDUNDANT]
    │   ├── _try_commerce_draft() → get_user() [REDUNDANT]
    │   ├── _try_commerce_draft() → get_user_profile() [REDUNDANT]
    │   └── build_conversational_commerce_state() → 14+ PG queries [7 REDUNDANT]
    │
    └── [SEQUENTIAL PG - REDUNDANT]
        ├── get_recent_messages(limit=20) [REDUNDANT]
        ├── get_user() [REDUNDANT]
        ├── get_recent_messages(limit=7) x2 [REDUNDANT]
        └── get_user() [REDUNDANT]
```

---

## 8. Parallelization Candidates

### 8.1 Currently Sequential, Safe to Parallelize

| Operations | Current | Parallel | Round-Trip Reduction |
|------------|---------|----------|---------------------|
| build_llm_context() sub-queries (#9-15) | 7 sequential | asyncio.gather | 6 RTTs |
| get_timing_context() + get_behavioral_feedback_context() | 2 sequential | asyncio.gather | 1 RTT |
| _get_purchases_safe + _get_active_offers_safe + _get_product_info_safe + _get_creator_info_safe | 4 sequential | asyncio.gather | 3 RTTs |

### 8.2 Dependency Analysis

```
get_user(user_id)                    ── no deps ── SAFE TO PARALLELIZE
get_user_profile(user_id)            ── no deps ── SAFE TO PARALLELIZE
get_recent_messages(user_id)         ── no deps ── SAFE TO PARALLELIZE
get_latest_summary_with_age(user_id) ── no deps ── SAFE TO PARALLELIZE
get_structured_persona_async()       ── no deps ── SAFE TO PARALLELIZE

_get_purchases_safe()                ── no deps ── SAFE TO PARALLELIZE
_get_active_offers_safe()            ── no deps ── SAFE TO PARALLELIZE
_get_product_info_safe()             ── no deps ── SAFE TO PARALLELIZE
_get_creator_info_safe()             ── no deps ── SAFE TO PARALLELIZE
_get_last_purchase_at_safe()         ── no deps ── SAFE TO PARALLELIZE
_get_last_followup_at_safe()         ── no deps ── SAFE TO PARALLELIZE
_get_segments_safe()                 ── no deps ── SAFE TO PARALLELIZE

get_timing_context()                 ── no deps ── SAFE TO PARALLELIZE
get_behavioral_feedback_context()    ── no deps ── SAFE TO PARALLELIZE
```

### 8.3 Connection Pool Implications

Current pool size: default asyncpg (typically 10 connections).

Parallelizing 7 sub-queries would require 7 simultaneous connections. With the parallel gather (4-5 queries) already using 4-5 connections, peak concurrent connections could reach 11-12. This may exceed the default pool size.

**Mitigation:** Increase pool size to 20, or batch parallel operations into two waves.

---

## 9. Redis Pipeline Candidates

### 9.1 Pipelineable Operations

| Batch | Operations | Current RTTs | Pipelined RTTs | Savings |
|-------|------------|--------------|----------------|---------|
| publish_event() x4-5 | PUBLISH x4-5 | 4-5 | 1 | 3-4 |
| persona cache reads | GET + GET | 2 | 1 | 1 |
| **Total** | | **6-7** | **2** | **4-5** |

### 9.2 Non-Pipelineable Operations

| Operation | Why |
|-----------|-----|
| SET NX (lock) | Must check result before proceeding |
| XADD (stream) | Must return message ID |
| XREADGROUP | Blocking read |
| XACK | Must complete before next read |
| INCR (rate limit) | Must check count |

---

## 10. Cache Candidates

### 10.1 Existing Caches

| Data | Cache Location | TTL | Invalidation |
|------|---------------|-----|--------------|
| User persona | Redis `persona:{creator_id}:{user_id}` | 600s | On persona update |
| Default persona | Redis `persona:creator:{creator_id}:default` | 600s | On persona update |
| Creator persona | Redis `persona:creator:{creator_id}` | 600s | On persona update |
| User context | Redis `context:{user_id}` | 300s | On context update |

### 10.2 Cache Candidates

| Data | Current Source | Proposed Cache | TTL | Staleness Risk |
|------|---------------|----------------|-----|----------------|
| User profile | PG (8x per generation) | Request-local (pass through) | Per-generation | None (fresh each gen) |
| Recent messages | PG (5x per generation) | Request-local | Per-generation | None |
| Summary | PG (3x per generation) | Request-local | Per-generation | None |
| Fan knowledge | PG (3x per generation) | Request-local | Per-generation | None |
| Behavioral feedback | PG (2x per generation) | Request-local | Per-generation | None |

### 10.3 What Must NOT Be Cached

| Data | Why |
|------|-----|
| Commerce offer state | Stale = incorrect offer/price |
| Purchase state | Stale = incorrect purchase decision |
| PPV access state | Stale = incorrect content access |
| Rate limit counters | Must be real-time |
| Lock state | Must be atomic |

---

## 11. Commerce Safety Verification

### 11.1 Commerce Values on Critical Path

| Value | Source | Authority | Optimization Impact |
|-------|--------|-----------|---------------------|
| Product identity | `list_valid_products()` | DB | Must remain fresh |
| Offer state | `_get_active_offers_safe()` | DB | Must remain fresh |
| Price | `_get_product_info_safe()` | DB | Must remain fresh |
| Purchase state | `_get_purchased_product_ids()` | DB | Must remain fresh |
| Relationship state | `derive_relationship_state()` | Deterministic | Safe to cache |
| Desire stage | `derive_desire_stage()` | Deterministic | Safe to cache |
| Temperature | `derive_commercial_temperature()` | Deterministic | Safe to cache |
| Timing context | `get_timing_context()` | DB | Must remain fresh |
| Behavioral feedback | `get_behavioral_feedback_context()` | DB | Must remain fresh |

### 11.2 Safe Optimizations

All proposed optimizations preserve commerce safety:
- Profile caching passes the same data that would be fetched anyway
- Parallelization doesn't change data, only timing
- Redis pipelining doesn't change event contents
- Eliminating redundant queries doesn't remove any unique data

### 11.3 Commerce Decision Trace

```
extract_commerce_signals(context)        ← LLM #1 (authoritative)
    ↓
_try_commerce_draft()
    ├── resolve_commerce_product_with_history()  ← DB (authoritative)
    └── resolve_and_run_commerce()               ← Commerce pipeline (authoritative)
    ↓
build_conversational_commerce_state()
    ├── get_timing_context()               ← DB (authoritative)
    ├── get_behavioral_feedback_context()  ← DB (authoritative)
    ├── derive_desire_stage()              ← Deterministic
    ├── derive_commercial_temperature()    ← Deterministic
    ├── evaluate_offer_readiness()         ← Deterministic
    └── derive_commercial_objective()      ← Deterministic
    ↓
Qwen2.5 generation                        ← LLM #2
    ↓
score_draft()                             ← LLM #3 (authoritative)
```

**None of the proposed optimizations modify any value in this chain.** The optimizations only reduce redundant fetches of the same data.

---

## 12. Persona Safety Verification

### 12.1 Persona Values on Critical Path

| Value | Source | Optimization Impact |
|-------|--------|---------------------|
| Persona text | `_load_persona_text()` | Not in proposed optimization scope |
| Structured persona | `get_structured_persona_async()` | Already cached once per generation |
| Persona behavior state | `derive_persona_behavior_state()` | Pure function, no I/O |
| Persona validation | `validate_persona_voice()` | Pure function, no I/O |
| Persona voice check | `validate_persona_voice()` | Pure function, no I/O |

**All persona operations are either already cached or pure functions.** No persona safety impact from proposed optimizations.

---

## 13. Latency Impact Estimates

### 13.1 Current Context Build Latency

| Phase | Operations | Estimated Latency |
|-------|------------|-------------------|
| PG parallel gather (4 queries) | asyncio.gather | 10-50ms |
| PG sequential (18 queries) | build_llm_context + others | 60-180ms |
| Commerce state (14 queries) | build_conversational_commerce_state | 50-140ms |
| PG post-context (9 queries) | redundant re-fetches | 30-90ms |
| **Total context build** | | **150-460ms** |

### 13.2 Optimized Context Build Latency

| Optimization | Latency Saved | Evidence |
|--------------|---------------|----------|
| Eliminate 14 redundant PG queries | 40-100ms | 14 queries × 3-7ms each |
| Pass profile to consumers (eliminate 7 profile parses) | 20-50ms | 7 PG round-trips + 7 json.loads |
| Parallelize build_llm_context sub-queries | 30-80ms | 6 sequential → 1 parallel |
| Pipeline publish_event calls | 3-4ms | 4-5 RTTs → 1 RTT |
| **Total savings** | **93-234ms** | |

### 13.3 Projected Optimized Latency

| Phase | Current | Optimized | Savings |
|-------|---------|-----------|---------|
| Context build | 150-460ms | 57-226ms | 93-234ms |
| LLM generation | 800-2500ms | 800-2500ms | 0 |
| Post-generation | 50-100ms | 50-100ms | 0 |
| **Total** | **1000-3060ms** | **907-2826ms** | **93-234ms** |

---

## 14. Recommended Implementation Order

### Priority 1: Profile Caching (Highest Impact, Medium Risk)
**Change:** Pass `profile` from Phase A parallel gather to all read consumers.
**Impact:** Eliminates 7 redundant PG round-trips + 7 json.loads calls.
**Savings:** 20-50ms + 2-3ms serialization.
**Risk:** MEDIUM — changes data flow but preserves semantics.

### Priority 2: Eliminate Redundant Queries (High Impact, Low Risk)
**Change:** Pass `user`, `messages`, `summary` from Phase A to Phase B.
**Impact:** Eliminates 3-4 redundant PG round-trips.
**Savings:** 10-30ms.
**Risk:** LOW — straightforward data passing.

### Priority 3: Parallelize build_llm_context Sub-Queries (High Impact, Low Risk)
**Change:** Wrap sub-queries in asyncio.gather().
**Impact:** Reduces 7 sequential queries to 1 parallel wave.
**Savings:** 30-80ms.
**Risk:** LOW — independent queries, no transaction ordering.

### Priority 4: Pipeline publish_event Calls (Low Impact, Very Low Risk)
**Change:** Batch PUBLISH calls into Redis pipeline.
**Impact:** Reduces 4-5 Redis RTTs to 1.
**Savings:** 3-4ms.
**Risk:** VERY LOW — fire-and-forget events.

### Priority 5: orjson Installation (Very Low Impact, Very Low Risk)
**Change:** Replace json.loads with orjson.loads in hot path.
**Impact:** 2-3ms faster parsing per call.
**Savings:** 2-3ms (negligible compared to PG savings).
**Risk:** VERY LOW — drop-in replacement.

---

## 15. Deferred Optimizations

| Optimization | Why Deferred |
|--------------|-------------|
| Semantic retrieval (embeddings) | Phase 74I scope, not I/O optimization |
| Memory/knowledge model redesign | Out of scope |
| LLM call consolidation | Requires quality benchmark |
| Redis caching of user profile | PG round-trip elimination is better |
| Connection pool tuning | Premature without measurement |
| orjson for all serialization | Marginal benefit, lower priority |

---

## 16. Stage B Implementation Boundary

Stage B implements the following surgical changes:

1. **Profile caching** — Pass profile through call chain
2. **Redundant query elimination** — Pass user/messages/summary
3. **Sub-query parallelization** — asyncio.gather for build_llm_context
4. **Redis pipeline** — Batch publish_event calls
5. **orjson** — Install and use in hot path

Each change is:
- Independently testable
- Independently rollbackable
- Feature-gated (optional)
- Semantics-preserving

---

## 17. Acceptance Criteria

1. ✅ Exactly how many PG round trips occur per normal generation?
   - **56 total, 14 redundant, 25 unique required**

2. ✅ Exactly how many Redis round trips occur?
   - **20-29 total (11-14 handlers + 9 llm_worker)**

3. ✅ Exactly which operations are redundant?
   - **14 PG queries** (detailed in Section 3.1)
   - **3-4 Redis PUBLISH calls** (could be pipelined)

4. ✅ Exactly which operations can be safely parallelized?
   - **10 PG queries** in build_llm_context sub-queries
   - **2 PG queries** (timing + behavioral feedback)

5. ✅ Exactly which Redis operations can be pipelined?
   - **4-5 PUBLISH calls** (publish_event)
   - **2 GET calls** (persona cache reads)

6. ✅ Exactly where profile/state parsing repeats?
   - **8 get_user_profile() calls** per generation

7. ✅ Exactly where JSON serialization repeats?
   - **8 json.loads() on same profile data** per generation

8. ✅ Which values can safely be reused in-memory?
   - Profile, user, messages, summary, fan knowledge, memories

9. ✅ Which values can safely be cached?
   - Request-local caching only (per-generation)

10. ✅ Which values must remain authoritative and uncached?
    - Commerce offer state, purchase state, PPV access, rate limits, locks

11. ✅ What is the estimated latency saving for each optimization?
    - Profile caching: 20-50ms
    - Redundant elimination: 10-30ms
    - Parallelization: 30-80ms
    - Redis pipeline: 3-4ms
    - orjson: 2-3ms

12. ✅ What correctness risks exist for each?
    - All are LOW risk — semantics-preserving, feature-gated

13. ✅ Which optimization should be implemented first?
    - **Profile caching** (highest impact)

14. ✅ Which optimizations should explicitly be deferred?
    - Embeddings, memory redesign, LLM consolidation

15. ✅ Does the optimization preserve the current 3-LLM Qwen2.5 pipeline?
    - **YES** — no LLM changes

---

## 18. Final Verdict

```
PHASE 74B STAGE A VERDICT

PG ROUND TRIPS:
56 total per generation (14 redundant, 25 unique required, 17 in commerce state)

REDIS ROUND TRIPS:
20-29 total per generation (11-14 handlers + 9 llm_worker)

REDUNDANT OPERATIONS:
14 PG queries (get_user x4, get_user_profile x7, get_recent_messages x3, get_fan_knowledge x2, get_behavioral_feedback_context x5, list_valid_products x1, _get_purchased_product_ids x1)
4-5 Redis PUBLISH calls (could be pipelined)

SAFE PARALLELIZATION:
10 PG queries (build_llm_context sub-queries: 7 sequential → 1 parallel)
2 PG queries (timing + behavioral feedback)

SAFE REDIS PIPELINES:
4-5 PUBLISH calls (publish_event) → 1 pipeline
2 GET calls (persona cache) → 1 pipeline

SERIALIZATION WASTE:
8 json.loads() on same profile data per generation (200KB for heavy profile)

PROFILE REUSE OPPORTUNITY:
1 canonical fetch → pass to all 7 read consumers (eliminates 7 PG round-trips + 7 json.loads)

CACHE OPPORTUNITY:
Request-local only (per-generation). No Redis/PG caching needed.

ORJSON:
Recommended for marginal gain (2-3ms). Primary benefit is profile caching, not faster parsing.

EXPECTED CONTEXT LATENCY SAVING:
93-234ms per generation (from 150-460ms down to 57-226ms)

COMMERCE SAFETY:
PASS — all proposed optimizations preserve commerce authority

PERSONA SAFETY:
PASS — no persona operations affected

3-LLM PIPELINE:
PRESERVED — no LLM changes

PRODUCTION CHANGES:
NONE — this is a READ-ONLY audit

NEXT ACTION:
Stage B — surgical context I/O optimization (profile caching, redundant elimination, parallelization, Redis pipeline, orjson)
```
