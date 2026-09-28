# Phase 74B — Context-IO Elimination: Final Report

## Executive Summary

Phase 74B implemented five targeted optimizations to reduce redundant I/O in the context-construction pipeline. All five deliverables are complete, tested (87 tests passing), and backward-compatible. No schema changes, no new workers, no pipeline redesign.

## Deliverables

### B1: Canonical Profile Reuse

**Problem:** `get_user_profile()` called 8x per generation, each parsing ~200KB JSON.

**Solution:** Added `_profile_cache` dict in `memory/context.py` with `get_last_profile()`/`get_last_user()` getters. Added optional `profile` param to all downstream consumers.

**Files modified:**
- `memory/context.py` — cache + getters
- `commerce/long_term_memory.py` — optional `profile` param
- `commerce/fan_knowledge.py` — optional `profile` param
- `commerce/conversational.py` — optional `profile` param
- `db/postgres.py` — optional `profile` param
- `memory/context_assembler.py` — optional `profile` param
- `workers/llm_worker.py` — pass cached profile to all consumers

**Savings:** 7 redundant `get_user_profile()` calls eliminated (~35-70ms).

---

### B2: Redundant Query Elimination

**Problem:** `build_llm_context()` re-fetched data already available from Phase A parallel gather or from context messages.

**Solution:** Added optional `user_data`, `recent_messages`, `summary` params to `build_llm_context()`. Updated `llm_worker.py` to pass Phase A results. Replaced `get_recent_messages(limit=7)` calls for persona behavior/validation with context extraction. Replaced `get_user()` auth calls with cached user.

**Files modified:**
- `memory/context_assembler.py` — accept optional params
- `workers/llm_worker.py` — pass cached data, use context messages for persona

**Savings:** 3-5 redundant PG queries eliminated (~15-25ms).

---

### B3: Parallelized Sub-queries

**Problem:** `build_llm_context()` sub-queries (purchases, offers, product, creator, segments) ran sequentially.

**Solution:** Replaced 7 sequential try/except blocks with `asyncio.gather()` — all independent queries execute concurrently.

**Files modified:**
- `memory/context_assembler.py` — `asyncio.gather()` for 7 sub-queries

**Savings:** ~50-100ms (7 sequential → 1 parallel batch).

---

### B4: Redis Pipeline for publish_event

**Problem:** 4-5 `publish_event()` calls in post-generation path each made a separate Redis PUBLISH.

**Solution:** Added `publish_events_batch()` to `core/event_bus.py` using Redis pipeline. Updated both operator-queued routing paths to batch 2 events each.

**Files modified:**
- `core/event_bus.py` — `publish_events_batch()` function
- `workers/llm_worker.py` — batch publish in operator-queued paths

**Savings:** 4-5 Redis round-trips → 2 pipeline calls (~8-16ms).

---

### B5: orjson on Hot Paths

**Problem:** `json.dumps()` used for Redis event serialization.

**Solution:** Added `orjson` with fallback to stdlib `json`. Applied to `event_bus.py` publish paths.

**Files modified:**
- `core/event_bus.py` — `_json_dumps()` wrapper

**Savings:** ~2-5ms per serialization (orjson is 2-10x faster than stdlib json).

---

## Test Results

```
87 passed in 4.24s (core + persona + isolation + hardening tests)
239 passed in 3.54s (context engine tests)
```

## Estimated Total Savings

| Optimization | Before | After | Saved |
|---|---|---|---|
| PG round-trips | 56 | ~35 | ~21 |
| Redis round-trips | 20-29 | ~15 | ~5-14 |
| JSON parsing | 8x get_user_profile | 1x | 7x |
| Sub-query latency | sequential | parallel | ~50-100ms |
| **Total estimated** | **~200-300ms** | | **~120-215ms** |

## Invariants Preserved

- 3-LLM Qwen2.5 pipeline: untouched
- Commerce authority scoring: untouched
- Persona behavior: untouched
- Database schema: no changes
- Redis Streams task queue: untouched
- WebSocket/realtime events: schema preserved
- Backward compatibility: all changes are additive

## Files Modified (Complete List)

| File | Changes |
|---|---|
| `core/event_bus.py` | orjson, `publish_events_batch()` |
| `memory/context.py` | `_profile_cache`, `get_last_profile()`, `get_last_user()` |
| `memory/context_assembler.py` | optional params, `asyncio.gather()` |
| `commerce/long_term_memory.py` | optional `profile` param |
| `commerce/fan_knowledge.py` | optional `profile` param |
| `commerce/conversational.py` | optional `profile` param |
| `db/postgres.py` | optional `profile` param |
| `workers/llm_worker.py` | B1-B5 consumer changes |
