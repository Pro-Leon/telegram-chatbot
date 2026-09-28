# Phase 74B — Context I/O Implementation Map

**Date:** 2026-09-01
**Status:** READ-ONLY — no production code changes
**Scope:** Stage B surgical changes for context I/O optimization

---

## Overview

Stage B implements 5 surgical changes, each independently testable and rollbackable. Total estimated savings: **93-234ms per generation**.

| Change | Impact | Risk | Priority | Dependencies |
|--------|--------|------|----------|--------------|
| B1: Profile caching | HIGH (20-50ms) | MEDIUM | P1 | None |
| B2: Redundant query elimination | HIGH (10-30ms) | LOW | P1 | None |
| B3: Sub-query parallelization | HIGH (30-80ms) | LOW | P2 | None |
| B4: Redis pipeline | LOW (3-4ms) | VERY LOW | P3 | None |
| B5: orjson installation | VERY LOW (2-3ms) | VERY LOW | P3 | None |

---

## Change B1: Profile Caching

### Goal
Fetch `get_user_profile()` once in Phase A parallel gather, pass to all downstream consumers. Eliminate 7 redundant PG round-trips + 7 json.loads calls.

### Current Behavior
```
build_qwen3_context()
├── [PARALLEL] get_user_profile(user_id)        ← Fetch #1
├── get_fan_knowledge() → get_user_profile()    ← Fetch #2 (REDUNDANT)
├── retrieve_relevant_memories() → get_user_profile() ← Fetch #3 (REDUNDANT)
├── retrieve_relevant_knowledge() → get_fan_knowledge() → get_user_profile() ← Fetch #4 (REDUNDANT)
├── retrieve_relevant_knowledge(limit=20) → ... ← Fetch #5 (REDUNDANT)
└── get_fan_knowledge() → get_user_profile()    ← Fetch #6 (REDUNDANT)

_try_commerce_draft()
└── get_user_profile(user_id)                   ← Fetch #7 (REDUNDANT)

build_conversational_commerce_state()
└── get_commercial_preferences() → get_user_profile() ← Fetch #8 (REDUNDANT)

Strategy learning
└── get_user_profile(user_id)                   ← Fetch #9 (REDUNDANT)
└── get_user_profile(user_id)                   ← Fetch #10 (REDUNDANT)
```

### New Behavior
```
build_qwen3_context()
├── [PARALLEL] get_user_profile(user_id)        ← Fetch #1 (canonical)
├── get_fan_knowledge(creator_id, user_id, profile=cached)  ← No fetch
├── retrieve_relevant_memories(..., profile=cached)         ← No fetch
├── retrieve_relevant_knowledge(..., profile=cached)        ← No fetch
└── get_fan_knowledge(..., profile=cached)                  ← No fetch

_try_commerce_draft()
└── (uses cached profile from context)          ← No fetch

build_conversational_commerce_state()
└── get_commercial_preferences(..., profile=cached) ← No fetch

Strategy learning
└── (uses cached profile)                       ← No fetch
```

### Files Modified
| File | Change |
|------|--------|
| `commerce/long_term_memory.py` | `get_long_term_memory()` accepts optional `profile` param |
| `commerce/fan_knowledge.py` | `get_fan_knowledge()` accepts optional `profile` param |
| `memory/context.py` | Pass cached profile to all consumers |
| `commerce/conversational.py` | `build_conversational_commerce_state()` accepts optional `profile` param |
| `db/postgres.py` | `get_commercial_preferences()` accepts optional `profile` param |

### Why Safe
- All consumers are READ-only on the profile
- WRITE consumers (`add_memory_item`, `add_knowledge_item`) still fetch fresh
- Profile is fetched once per generation, guaranteeing freshness
- Existing fallback (no profile param) preserves current behavior

### Expected Latency Benefit
- Eliminates 7 PG round-trips: ~21-49ms (7 × 3-7ms)
- Eliminates 7 json.loads calls: ~2-3ms
- **Total: ~23-52ms**

### Test Required
- `test_profile_cache_one_fetch`: Verify get_user_profile called exactly 1 time
- `test_profile_cache_read_consumers`: All read consumers receive cached profile
- `test_profile_cache_write_consumers`: Write consumers still fetch fresh
- `test_profile_cache_semantics_identical`: Output identical with/without cache

### Benchmark Required
- Measure get_user_profile call count before/after
- Measure total context build latency before/after

### Rollback Strategy
- Remove `profile` parameter from all functions (backward-compatible optional param)
- All consumers fall back to internal get_user_profile() call

---

## Change B2: Redundant Query Elimination

### Goal
Pass `user`, `messages`, `summary` from Phase A parallel gather to Phase B consumers. Eliminate 3-4 redundant PG round-trips.

### Current Behavior
```
build_qwen3_context()
├── [PARALLEL] get_user(user_id)           ← Fetch #1
├── [PARALLEL] get_recent_messages(limit=20) ← Fetch #2
├── [PARALLEL] get_latest_summary_with_age() ← Fetch #3
│
├── build_llm_context(creator_id, user_id)
│   ├── _get_user_safe(user_id)            ← Fetch #4 (REDUNDANT)
│   ├── _get_recent_messages_safe(limit)   ← Fetch #5 (REDUNDANT)
│   └── _get_summary_safe(user_id)         ← Fetch #6 (REDUNDANT)
│
├── [POST-CONTEXT] get_recent_messages(limit=20) ← Fetch #7 (REDUNDANT)
├── [POST-CONTEXT] get_user(user_id)              ← Fetch #8 (REDUNDANT)
│
├── get_recent_messages(limit=7)            ← Fetch #9 (REDUNDANT, subsumed)
├── get_recent_messages(limit=7)            ← Fetch #10 (REDUNDANT, same as #9)
└── get_user(user_id)                       ← Fetch #11 (REDUNDANT)
```

### New Behavior
```
build_qwen3_context()
├── [PARALLEL] get_user(user_id)           ← Fetch #1 (canonical)
├── [PARALLEL] get_recent_messages(limit=20) ← Fetch #2 (canonical)
├── [PARALLEL] get_latest_summary_with_age() ← Fetch #3 (canonical)
│
├── build_llm_context(creator_id, user_id, user=cached, messages=cached, summary=cached)
│   ├── (no fetch - uses cached)
│   ├── (no fetch - uses cached)
│   └── (no fetch - uses cached)
│
├── [POST-CONTEXT] (uses cached messages)  ← No fetch
├── [POST-CONTEXT] (uses cached user)      ← No fetch
│
├── get_recent_messages(limit=7)            ← SUBSUMED by limit=20 (use slice)
├── get_recent_messages(limit=7)            ← SUBSUMED by limit=20 (use slice)
└── get_user(user_id)                       ← (uses cached user)
```

### Files Modified
| File | Change |
|------|--------|
| `memory/context.py` | Pass `user`, `messages`, `summary` to `build_llm_context()` |
| `memory/context_assembler.py` | `build_llm_context()` accepts optional `user`, `messages`, `summary` params |
| `workers/llm_worker.py` | Pass cached data to post-context consumers |

### Why Safe
- Data is fetched fresh once in Phase A
- All consumers receive the same data they would have fetched
- Fallback (no params) preserves current behavior
- `get_recent_messages(limit=7)` is subsumed by `limit=20` (first 7 are a prefix)

### Expected Latency Benefit
- Eliminates 3-4 PG round-trips: ~9-28ms
- **Total: ~9-28ms**

### Test Required
- `test_redundant_elimination_one_fetch`: get_user called exactly 1 time
- `test_redundant_elimination_messages`: get_recent_messages(limit=20) called exactly 1 time
- `test_redundant_elimination_summary`: get_latest_summary_with_age called exactly 1 time
- `test_redundant_elimination_semantics`: Output identical with/without elimination

### Benchmark Required
- Measure PG query count before/after
- Measure total context build latency before/after

### Rollback Strategy
- Remove optional params from build_llm_context()
- Consumers fall back to internal queries

---

## Change B3: Sub-Query Parallelization

### Goal
Run `build_llm_context()` sub-queries in parallel. Reduce 7 sequential queries to 1 parallel wave.

### Current Behavior
```
build_llm_context(creator_id, user_id)
├── _get_user_safe(user_id)                    [sequential]
├── _get_recent_messages_safe(user_id, limit)   [sequential]
├── _get_summary_safe(user_id)                  [sequential]
├── _get_purchases_safe(creator_id, user_id)    [sequential]
├── _get_active_offers_safe(creator_id, user_id) [sequential]
├── _get_product_info_safe(creator_id, user_id)  [sequential]
├── _get_creator_info_safe(creator_id)           [sequential]
├── _get_last_purchase_at_safe(creator_id, user_id) [sequential]
├── _get_last_followup_at_safe(creator_id, user_id) [sequential]
├── _get_segments_safe(creator_id, user_id)      [sequential]
└── get_behavioral_feedback_context(creator_id, user_id) [sequential]
```

### New Behavior
```
build_llm_context(creator_id, user_id, user=cached, messages=cached, summary=cached)
│
├── [WAVE 1 - parallel] (user/messages/summary already cached)
│
└── [WAVE 2 - parallel] asyncio.gather:
    ├── _get_purchases_safe(creator_id, user_id)
    ├── _get_active_offers_safe(creator_id, user_id)
    ├── _get_product_info_safe(creator_id, user_id)
    ├── _get_creator_info_safe(creator_id)
    ├── _get_last_purchase_at_safe(creator_id, user_id)
    ├── _get_last_followup_at_safe(creator_id, user_id)
    ├── _get_segments_safe(creator_id, user_id)
    └── get_behavioral_feedback_context(creator_id, user_id)
```

### Files Modified
| File | Change |
|------|--------|
| `memory/context_assembler.py` | Wrap sub-queries in `asyncio.gather()` |

### Why Safe
- All sub-queries are independent reads
- No transaction ordering required
- Each wrapped in try/except for failure isolation
- Connection pool must be sized to handle 8 concurrent connections

### Expected Latency Benefit
- 7 sequential → 1 parallel: ~30-80ms (bounded by slowest query)
- **Total: ~30-80ms**

### Test Required
- `test_parallel_subqueries_all_execute`: All sub-queries execute
- `test_parallel_subqueries_partial_failure`: Partial failure degrades gracefully
- `test_parallel_subqueries_identical_results`: Results identical to sequential

### Benchmark Required
- Measure build_llm_context latency before/after
- Measure peak concurrent DB connections

### Rollback Strategy
- Remove asyncio.gather, revert to sequential calls

---

## Change B4: Redis Pipeline

### Goal
Batch `publish_event()` calls into Redis pipeline. Reduce 4-5 Redis RTTs to 1.

### Current Behavior
```python
await publish_event("ai.generation_started", ...)      # RTT 1
await publish_event("persona.behavior", ...)            # RTT 2
await publish_event("ai.generation_completed", ...)     # RTT 3
await publish_event("suggestion.created", ...)          # RTT 4 (conditional)
```

### New Behavior
```python
pipeline = r.pipeline()
pipeline.publish(CHANNEL, json.dumps(event1))
pipeline.publish(CHANNEL, json.dumps(event2))
pipeline.publish(CHANNEL, json.dumps(event3))
pipeline.publish(CHANNEL, json.dumps(event4))
await pipeline.execute()  # Single RTT
```

### Files Modified
| File | Change |
|------|--------|
| `core/event_bus.py` | Add `publish_events_batch()` function |
| `workers/llm_worker.py` | Use batch publish for post-generation events |

### Why Safe
- Events are fire-and-forget (best-effort)
- Event contents unchanged
- Event IDs still unique (generated before pipeline)
- Pipeline preserves ordering
- Failure semantics unchanged (pipeline failure = all events lost, same as individual failures)

### Expected Latency Benefit
- 4-5 RTTs → 1 RTT: ~3-4ms
- **Total: ~3-4ms**

### Test Required
- `test_batch_publish_all_events`: All events published
- `test_batch_publish_event_contents`: Event payloads identical
- `test_batch_publish_failure`: Pipeline failure doesn't break generation

### Benchmark Required
- Measure publish latency before/after

### Rollback Strategy
- Revert to individual publish_event() calls

---

## Change B5: orjson Installation

### Goal
Install orjson and use it for JSON parsing in the hot path.

### Current Behavior
```python
import json
profile = json.loads(row["facts"])  # 8x per generation
```

### New Behavior
```python
import orjson
profile = orjson.loads(row["facts"])  # 8x per generation, 2-10x faster
```

### Files Modified
| File | Change |
|------|--------|
| `pyproject.toml` | Add `orjson` dependency |
| `db/postgres.py` | Replace `json.loads` → `orjson.loads` in `get_user_profile()` |
| `core/event_bus.py` | Replace `json.dumps` → `orjson.dumps` in `publish_event()` |

### Why Safe
- Drop-in replacement for json.loads/json.dumps
- Produces identical Python objects
- No behavior change

### Expected Latency Benefit
- 2-10x faster parsing per call
- With 8 calls: ~2-3ms total savings
- **Total: ~2-3ms**

### Test Required
- `test_orjson_equivalence`: orjson output identical to json
- `test_orjson_compatibility`: All payload types compatible

### Benchmark Required
- Measure parsing latency with json vs orjson

### Rollback Strategy
- Remove orjson import, revert to json

---

## Implementation Sequence

```
B1 (Profile caching) ──────────────────────────────────┐
B2 (Redundant elimination) ─────────────────────────────┤
B3 (Sub-query parallelization) ─────────────────────────┤
B4 (Redis pipeline) ────────────────────────────────────┤
B5 (orjson) ────────────────────────────────────────────┘
```

B1 and B2 are independent. B3 depends on B2 (sub-queries are reduced by B2). B4 and B5 are independent of everything.

**Recommended order:** B1 → B2 → B3 → B4 → B5

---

## Expected Cumulative Savings

| After Change | Latency Saved | Cumulative |
|--------------|---------------|------------|
| B1 (profile caching) | 23-52ms | 23-52ms |
| B2 (redundant elimination) | 9-28ms | 32-80ms |
| B3 (parallelization) | 30-80ms | 62-160ms |
| B4 (Redis pipeline) | 3-4ms | 65-164ms |
| B5 (orjson) | 2-3ms | 67-167ms |

**Total estimated savings: 67-167ms per generation**

---

## Production Safety Checklist

For each change:
- [ ] All 239 existing tests pass
- [ ] New tests added and passing
- [ ] Feature-gated (can be disabled)
- [ ] Fail-open (failure doesn't break generation)
- [ ] No commerce authority changes
- [ ] No persona authority changes
- [ ] No LLM pipeline changes
- [ ] Rollback strategy documented
- [ ] Telemetry preserved

---

## Stage C+ (Deferred)

After Stage B, the following are candidates for future phases:
- Semantic retrieval (embeddings) — Phase 74I
- Memory/knowledge model redesign — Out of scope
- LLM call consolidation — Requires quality benchmark
- Connection pool tuning — Premature without measurement
