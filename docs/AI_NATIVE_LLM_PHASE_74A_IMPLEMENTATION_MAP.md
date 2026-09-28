# Phase 74A — Context Engine Implementation Map

**Date:** 2026-09-01
**Status:** READ-ONLY — no production code changes
**Scope:** Phased implementation plan for context pipeline optimization (Phases 74B-74J)

---

## Executive Summary

Phase 74A forensic research identified three categories of work:

1. **Quick wins** (74B-74C): Low-effort, immediate latency savings (~40-100ms)
2. **Core optimizations** (74D-74G): Medium-effort, high-impact context pipeline improvements (~150-300ms savings)
3. **Strategic enhancements** (74H-74J): High-effort, long-term capability additions

**Total estimated pipeline savings:** ~200-400ms per generation (from ~80-260ms context build down to ~40-80ms)

**Dependencies:** Each phase builds on prior phases. No phase requires modifying the 3-LLM pipeline (LLM #1/#2/#3).

---

## Phase Dependency Graph

```
74B (orjson) ──────────────────────────────────────────┐
74C (Redis pipeline) ──────────────────────────────────┤
74D (profile cache) ─── 74E (eliminate redundant PG) ──┤
74F (parallelize sub-queries) ─── 74G (consolidate) ───┤
74H (fuzzy retrieval) ─── 74I (semantic embeddings) ───┤
74J (full context engine integration) ─────────────────┘
```

---

## Phase 74B: orjson Integration

**Goal:** Replace standard `json.loads()` with `orjson.loads()` in the hot path.
**Effort:** LOW (~1-2 hours)
**Latency Savings:** ~0.5-3ms per generation
**Risk:** LOW — drop-in replacement

### Scope
- Install `orjson` package
- Replace `json.loads()` in `db/postgres.py:get_user_profile()` with `orjson.loads()`
- Replace `json.loads()` in `commerce/long_term_memory.py` and `commerce/fan_knowledge.py`
- Replace `json.dumps()` in `update_user_profile()` with `orjson.dumps()`

### Files Modified
| File | Change |
|------|--------|
| `pyproject.toml` | Add `orjson` dependency |
| `db/postgres.py` | Replace `json.loads` → `orjson.loads` in `get_user_profile()`, `json.dumps` → `orjson.dumps` in `update_user_profile()` |
| `commerce/long_term_memory.py` | Replace `json.loads`/`json.dumps` |
| `commerce/fan_knowledge.py` | Replace `json.loads`/`json.dumps` |
| `core/event_bus.py` | Replace `json.dumps` in `publish_event()` |

### Tests
- Existing tests in `tests/test_context_engine.py` must pass
- Add `tests/test_phase74b_orjson.py` verifying serialization equivalence
- Verify `orjson.loads()` produces identical output to `json.loads()` for all payload types

### Acceptance Criteria
- [ ] `orjson` installed and importable
- [ ] All `json.loads()` in hot path replaced with `orjson.loads()`
- [ ] All `json.dumps()` in hot path replaced with `orjson.dumps()`
- [ ] All existing tests pass
- [ ] New tests verify equivalence

---

## Phase 74C: Redis Pipeline Batching

**Goal:** Batch sequential Redis `publish_event()` calls into pipelines.
**Effort:** LOW (~2-3 hours)
**Latency Savings:** ~3-4ms per generation
**Risk:** LOW — fire-and-forget operations

### Scope
- Modify `core/event_bus.py:publish_event()` to accept an optional `pipeline` parameter
- In `llm_worker.py`, create a Redis pipeline before the publish_event block, pass it to each call, then execute the pipeline once
- Batch `get_cached_user_persona()` + `get_cached_default_persona()` into a single pipeline

### Files Modified
| File | Change |
|------|--------|
| `core/event_bus.py` | Add pipeline parameter to `publish_event()` |
| `workers/llm_worker.py` | Create pipeline before publish block, execute after |
| `db/redis.py` | Add `pipeline()` context manager method |

### Tests
- Add `tests/test_phase74c_redis_pipeline.py` verifying:
  - Pipeline executes all PUBLISH commands in one RTT
  - Event payloads are preserved exactly
  - Pipeline failure doesn't break the generation flow (fail-open)

### Acceptance Criteria
- [ ] All `publish_event()` calls in `llm_worker.py` batched into one pipeline
- [ ] `get_cached_user_persona()` + `get_cached_default_persona()` batched
- [ ] All existing tests pass
- [ ] New tests verify pipeline behavior

---

## Phase 74D: User Profile Cache in Context Result

**Goal:** Fetch `user_profile` once in Phase A, pass to all consumers. Eliminate 6-8 redundant `get_user_profile()` calls.
**Effort:** MEDIUM (~3-4 hours)
**Latency Savings:** ~10-30ms per generation
**Risk:** MEDIUM — changes data flow

### Scope
- In `build_qwen3_context()`, capture the `user_profile` from Phase A parallel gather
- Pass `user_profile` to:
  - `get_fan_knowledge()` (currently calls `get_user_profile()` internally)
  - `retrieve_relevant_memories()` (currently calls `get_user_profile()` internally)
  - `retrieve_relevant_knowledge()` (currently calls `get_fan_knowledge()` → `get_user_profile()`)
  - `build_llm_context()` sub-queries that call `get_user_profile()`
- Modify these functions to accept an optional `user_profile` parameter
- If `user_profile` is provided, skip the internal `get_user_profile()` call

### Files Modified
| File | Change |
|------|--------|
| `memory/context.py` | Capture `user_profile` in Phase A, pass to consumers |
| `commerce/long_term_memory.py` | `get_long_term_memory()` and `retrieve_relevant_memories()` accept optional `profile` param |
| `commerce/fan_knowledge.py` | `get_fan_knowledge()` and `retrieve_relevant_knowledge()` accept optional `profile` param |
| `memory/context_assembler.py` | `_get_user_safe()` accepts optional `user_profile` param |

### Tests
- Add `tests/test_phase74d_profile_cache.py` verifying:
  - `get_user_profile()` called exactly once per generation
  - All consumers receive the cached profile
  - Behavior is identical when profile is provided vs. not provided
  - Cache invalidation doesn't cause stale data (profile is fetched fresh each generation)

### Acceptance Criteria
- [ ] `get_user_profile()` called exactly 1 time in `build_qwen3_context()`
- [ ] All consumers accept optional `profile` parameter
- [ ] All existing tests pass
- [ ] No behavioral change when profile is cached

---

## Phase 74E: Eliminate Redundant PG Queries

**Goal:** Pass already-fetched data from Phase A to Phase B. Eliminate ~7 redundant queries.
**Effort:** MEDIUM (~4-5 hours)
**Latency Savings:** ~30-70ms per generation
**Risk:** MEDIUM — changes data flow

### Scope
- In `build_qwen3_context()`, capture `user`, `messages`, `summary` from Phase A
- Pass to `build_llm_context()` instead of re-fetching:
  - `_get_user_safe(user_id)` → accept optional `user` param
  - `_get_recent_messages_safe(user_id)` → accept optional `messages` param
  - `_get_summary_safe(user_id)` → accept optional `summary` param
- Modify `build_llm_context()` to accept these as optional parameters

### Files Modified
| File | Change |
|------|--------|
| `memory/context.py` | Pass `user`, `messages`, `summary` to `build_llm_context()` |
| `memory/context_assembler.py` | `build_llm_context()` accepts optional `user`, `messages`, `summary` params |
| `memory/context_assembler.py` | `_get_user_safe()`, `_get_recent_messages_safe()`, `_get_summary_safe()` accept optional params |

### Tests
- Add `tests/test_phase74e_eliminate_redundant.py` verifying:
  - `get_user()` called exactly 1 time
  - `get_recent_messages(limit=20)` called exactly 1 time
  - `get_latest_summary_with_age()` called exactly 1 time
  - All existing tests pass

### Acceptance Criteria
- [ ] `get_user()` called exactly 1 time in entire context path
- [ ] `get_recent_messages()` called exactly 1 time (limit=20)
- [ ] `get_latest_summary_with_age()` called exactly 1 time
- [ ] All existing tests pass

---

## Phase 74F: Parallelize Context Sub-Queries

**Goal:** Run `build_llm_context()` sub-queries in parallel.
**Effort:** MEDIUM (~3-4 hours)
**Latency Savings:** ~50-100ms per generation
**Risk:** LOW — independent queries

### Scope
- In `context_assembler.py:build_llm_context()`, the following sub-queries are sequential but independent:
  - `_get_purchases_safe()`
  - `_get_active_offers_safe()`
  - `_get_product_info_safe()`
  - `_get_creator_info_safe()`
  - `_get_last_purchase_at_safe()`
  - `_get_last_followup_at_safe()`
  - `_get_segments_safe()`
- Wrap in `asyncio.gather()` to run in parallel
- Also parallelize `get_timing_context()` + `get_behavioral_feedback_context()` in `commerce/conversational.py`

### Files Modified
| File | Change |
|------|--------|
| `memory/context_assembler.py` | Wrap sub-queries in `asyncio.gather()` |
| `commerce/conversational.py` | Parallelize timing + behavioral feedback queries |

### Tests
- Add `tests/test_phase74f_parallel_queries.py` verifying:
  - All sub-queries execute (no skipped queries)
  - Results are identical to sequential execution
  - Partial failure doesn't break the context (graceful degradation)

### Acceptance Criteria
- [ ] `build_llm_context()` sub-queries run in parallel
- [ ] `get_timing_context()` + `get_behavioral_feedback_context()` run in parallel
- [ ] All existing tests pass
- [ ] No behavioral change

---

## Phase 74G: Consolidate Context Build Functions

**Goal:** Eliminate the separate `build_llm_context()` path. Single context build function.
**Effort:** HIGH (~6-8 hours)
**Latency Savings:** ~20-40ms (eliminates redundant function call overhead)
**Risk:** HIGH — major refactor

### Scope
- Merge `memory/context_assembler.py:build_llm_context()` into `memory/context.py:build_qwen3_context()`
- Single function that fetches once, assembles all context, renders output
- Eliminates the need for `render_context()` as a separate step
- Preserves all existing context categories and token budgets

### Files Modified
| File | Change |
|------|--------|
| `memory/context.py` | Absorb `build_llm_context()` logic |
| `memory/context_assembler.py` | Mark as deprecated, keep for backward compatibility |
| `workers/llm_worker.py` | Update import if needed |

### Tests
- All existing tests must pass
- Add `tests/test_phase74g_consolidated_context.py` verifying:
  - Single context build function produces identical output
  - All context categories are present
  - Token budgets are enforced

### Acceptance Criteria
- [ ] Single `build_qwen3_context()` function handles all context
- [ ] `context_assembler.py` deprecated but functional
- [ ] All existing tests pass
- [ ] No behavioral change

---

## Phase 74H: Fuzzy Retrieval for Memory/Knowledge

**Goal:** Replace crude token-overlap scoring with rapidfuzz fuzzy matching.
**Effort:** MEDIUM (~4-5 hours)
**Latency Savings:** Negligible (better relevance, not speed)
**Risk:** MEDIUM — changes retrieval behavior

### Scope
- Replace token-overlap scoring in `commerce/long_term_memory.py:retrieve_relevant_memories()` with rapidfuzz `token_set_ratio`
- Replace token-overlap scoring in `commerce/fan_knowledge.py:retrieve_relevant_knowledge()` with rapidfuzz `token_set_ratio`
- Preserve existing scoring weights (confidence, recency, importance)
- Add `rapidfuzz` to dependencies

### Files Modified
| File | Change |
|------|--------|
| `pyproject.toml` | Add `rapidfuzz` dependency |
| `commerce/long_term_memory.py` | Replace token overlap with `rapidfuzz.fuzz.token_set_ratio()` |
| `commerce/fan_knowledge.py` | Replace token overlap with `rapidfuzz.fuzz.token_set_ratio()` |

### Tests
- Add `tests/test_phase74h_fuzzy_retrieval.py` verifying:
  - Fuzzy matching finds relevant memories/knowledge that token overlap misses
  - Scoring is still bounded [0, 1]
  - Existing filter threshold (> 0.2) still applies
  - All existing tests pass

### Acceptance Criteria
- [ ] `rapidfuzz` installed and used for retrieval
- [ ] Memory retrieval finds semantically similar entries
- [ ] Knowledge retrieval finds semantically similar entries
- [ ] All existing tests pass

---

## Phase 74I: Semantic Embeddings for Memory/Knowledge

**Goal:** Add vector embeddings to memories and knowledge for semantic retrieval.
**Effort:** HIGH (~8-12 hours)
**Latency Savings:** N/A (capability addition)
**Risk:** HIGH — requires schema migration, background processing

### Scope
- Generate embeddings for new memories/knowledge items using `sentence-transformers` (`all-MiniLM-L6-v2`)
- Store embeddings in `user_profiles.facts` JSONB (new keys: `memory_embeddings_by_creator`, `knowledge_embeddings_by_creator`)
- Add hnswlib for vector similarity search
- Background embedding generation on memory/knowledge creation
- Retrieval: vector search + lexical search combined score

### Files Modified
| File | Change |
|------|--------|
| `pyproject.toml` | Add `hnswlib` dependency |
| `commerce/long_term_memory.py` | Generate embedding on `add_memory_item()`, store in facts |
| `commerce/fan_knowledge.py` | Generate embedding on `add_knowledge_item()`, store in facts |
| `commerce/long_term_memory.py` | Add `retrieve_relevant_memories_semantic()` using vector search |
| `commerce/fan_knowledge.py` | Add `retrieve_relevant_knowledge_semantic()` using vector search |
| `memory/context.py` | Use semantic retrieval when available, fallback to lexical |

### Tests
- Add `tests/test_phase74i_semantic_embeddings.py` verifying:
  - Embeddings are generated for new items
  - Embeddings are stored in facts JSONB
  - Vector search returns relevant results
  - Fallback to lexical when embeddings unavailable
  - All existing tests pass

### Acceptance Criteria
- [ ] New memories get embeddings
- [ ] New knowledge items get embeddings
- [ ] Vector search works with hnswlib
- [ ] Graceful fallback when embeddings missing
- [ ] All existing tests pass

---

## Phase 74J: Full Context Engine Integration

**Goal:** Activate the Context Engine (Phases 70-73) as the primary context pipeline.
**Effort:** HIGH (~8-12 hours)
**Latency Savings:** N/A (architecture change)
**Risk:** HIGH — production pipeline change

### Scope
- Set `context_engine_observational=True` in production config
- Monitor for 1 week in observational mode
- Compare Context Engine output vs. current pipeline
- If metrics are equivalent or better, switch to Context Engine as primary
- Keep current pipeline as fallback

### Files Modified
| File | Change |
|------|--------|
| `core/config.py` | `context_engine_observational=True` (observational mode) |
| `workers/llm_worker.py` | Update integration block to use Context Engine output |
| `core/config.py` | `context_engine_enabled=True` (after validation) |

### Tests
- All existing tests must pass
- Add `tests/test_phase74j_full_integration.py` verifying:
  - Context Engine produces valid context
  - Token budgets are enforced
  - All context categories present
  - Generation quality is equivalent or better

### Acceptance Criteria
- [ ] Observational mode enabled for 1 week
- [ ] Metrics show equivalent or better performance
- [ ] Full integration enabled
- [ ] Current pipeline available as fallback
- [ ] All existing tests pass

---

## Implementation Priority Matrix

| Phase | Effort | Impact | Risk | Priority | Dependencies |
|-------|--------|--------|------|----------|--------------|
| 74B | LOW | LOW | LOW | **P1** | None |
| 74C | LOW | LOW | LOW | **P1** | None |
| 74D | MEDIUM | MEDIUM | MEDIUM | **P2** | None |
| 74E | MEDIUM | HIGH | MEDIUM | **P2** | 74D |
| 74F | MEDIUM | HIGH | LOW | **P2** | None |
| 74G | HIGH | MEDIUM | HIGH | **P3** | 74D, 74E |
| 74H | MEDIUM | MEDIUM | MEDIUM | **P3** | None |
| 74I | HIGH | HIGH | HIGH | **P4** | 74H |
| 74J | HIGH | HIGH | HIGH | **P4** | 74B-74I |

---

## Latency Budget Projection

### Current State
| Phase | Latency |
|-------|---------|
| Context build | ~80-260ms |
| LLM generation | ~800-2,500ms |
| Post-generation | ~50-100ms |
| **Total** | **~930-2,860ms** |

### After 74B-74F (Quick Wins + Core Optimizations)
| Phase | Latency | Savings |
|-------|---------|---------|
| Context build | ~40-80ms | ~100-180ms saved |
| LLM generation | ~800-2,500ms | 0 |
| Post-generation | ~50-100ms | 0 |
| **Total** | **~890-2,680ms** | **~40-180ms saved** |

### After 74B-74J (Full Optimization)
| Phase | Latency | Savings |
|-------|---------|---------|
| Context build | ~30-60ms | ~150-200ms saved |
| LLM generation | ~800-2,500ms | 0 |
| Post-generation | ~50-100ms | 0 |
| **Total** | **~880-2,660ms** | **~50-200ms saved** |

---

## Production Safety Invariants

1. **3-LLM pipeline untouched** — No phase modifies `commerce/deepseek.py`, `commerce/decision.py`, or `commerce/signals.py`
2. **Feature-gated** — All changes behind `context_engine_observational` or `context_engine_enabled` flags
3. **Fail-open** — Any failure in optimization code falls back to existing behavior
4. **Telemetry preserved** — All 13 context engine telemetry fields remain
5. **Test coverage** — All 239 existing tests must pass after each phase

---

## Appendix: Estimated Test Counts

| Phase | New Tests | Cumulative |
|-------|-----------|------------|
| 74B | ~5 | 244 |
| 74C | ~5 | 249 |
| 74D | ~8 | 257 |
| 74E | ~6 | 263 |
| 74F | ~5 | 268 |
| 74G | ~8 | 276 |
| 74H | ~6 | 282 |
| 74I | ~10 | 292 |
| 74J | ~8 | 300 |
| **Total** | **~61** | **~300** |
