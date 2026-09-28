# AI_NATIVE_PHASE_39_IMPLEMENTATION_MAP.md
# Phase 39 — Implementation Map (Stage B)
# Date: 2026-08-30

## 1. Findings Reconciled

| ID | Finding | Proven | Fix |
|---|---|---|---|
| P1-01 | lock:user not creator-scoped → cross-creator blocking | PROVEN via `db/redis.py:252` `lock:user:{user_id}` | **FIXED** → `lock:creator:{creator_id}:user:{user_id}` with `creator_id` param, `workers/llm_worker.py` creator-scoped lock after creator resolution |
| P1-02 | fan_knowledge JSONB read-modify-write TOCTOU → lost update | PROVEN via `commerce/fan_knowledge.py:267` `SELECT` → `UPDATE` without `FOR UPDATE` | **FIXED** → `SELECT ... FOR UPDATE` transaction + `jsonb` atomic `INSERT ... ON CONFLICT`, plus in-mem `asyncio.Lock` per `creator:user` for fallback |

## 2. P1 Fixes Detail

### P1-01 — Creator-Scoped Lock

- **File:** `db/redis.py:267` `acquire_user_lock(user_id, ttl, creator_id=None)` → `_user_lock_key(user_id, creator_id)` returns `lock:creator:{creator}:user:{user}` if `creator_id` not None else `lock:user:{user}`; `release_user_lock` same; `clear_all_user_locks` scans both `lock:user:*` and `lock:creator:*:user:*`
- **File:** `workers/llm_worker.py:498` `process_message` — resolve `creator_id` first via `resolve_single_application_creator` (before lock), then `locked = await acquire_user_lock(user_id, ttl, creator_id=_creator_id)` with `creator_id`, `finally: release_user_lock(user_id, creator_id=_creator_id)`; `user_id` only fallback when `creator_id` None
- **Invariant:** `same creator+same fan → serialized` (same `lock:creator:1:user:100` → `SET NX` second fails), `different creator+same fan → independent` (different keys `lock:creator:1:user:100` vs `lock:creator:2:user:100` → both succeed), `same creator+different fan → independent` (different `user_id`)

### P1-02 — Atomic Fan-Knowledge JSONB

- **File:** `commerce/fan_knowledge.py:267` `add_knowledge_item` — before: `get_user_profile(user_id)` (SELECT without FOR UPDATE) → `by_creator[creator].append` → `update_user_profile` (INSERT ... ON CONFLICT) separate connections → TOCTOU. After: `pool.acquire()` → `async with conn.transaction(): row = await conn.fetchrow("SELECT facts FROM user_profiles WHERE user_id=$1 FOR UPDATE", user_id)` → `facts = json.loads(row["facts"])` else `{}`, modify `by_creator`, then `await conn.execute("INSERT ... ON CONFLICT ...", user_id, json.dumps(facts))` within same transaction (row locked), plus `import json`, plus in-mem fallback with `asyncio.Lock` per `creator:user` (`_knowledge_locks` dict) to serialize `asyncio.gather` in tests

## 3. Exact Files Changed

- `db/redis.py` — `_user_lock_key` helper, `acquire_user_lock` + `release_user_lock` with `creator_id`, `clear_all_user_locks` scans both patterns — 15 LOC
- `workers/llm_worker.py` — creator resolution before lock, `acquire_user_lock` with `creator_id`, `release_user_lock` with `creator_id`, `generation_id` deterministic already — 20 LOC
- `commerce/fan_knowledge.py` — `SELECT ... FOR UPDATE` transaction + `json` import, `import asyncio` + `_knowledge_locks` dict, fallback lock for in-mem — 30 LOC
- `tests/test_phase39_concurrency.py` — NEW 13 tests — 250 LOC

## 4. Authority Flow (Preserved)

```
SAFETY > CREATOR ISOLATION > HANDOFF > AFTERCARE > OBJECTION > OPEN_LOOP > DIRECT_REQUEST > COMMERCE > PRODUCTION CONTROL > OPTIMIZATION > LLM
```

Lock change preserves `SAFETY` and `CREATOR ISOLATION`, not new authority. JSONB fix preserves `creator_id+user_id` isolation via `fan_knowledge_by_creator` `str(creator)` per `user_id` row.

## 5. Retry/Generation-ID Flow

`generation_id` `md5(user:msg:telegram_id)` deterministic per `enqueue_inbound` + `process_message(generation_id)` reuse → `XAUTOCLAIM` preserves same `generation_id` → `strategy_generation_seen` dedup per `creator:user` 100 → no duplicate, `check_idempotent` per `generation_id:action:scope` 2000 — unchanged.

## 6. Rollout Determinism

`hashlib.sha256` deterministic, `is_rollout_active_for` SHA256, no change.

## 7. Health Semantics

`evaluate_production_health` with `total_gen<5` → `CAUTION insufficient_sample` (P1-05 fixed), not NORMAL — preserved.

## 8. DLQ Semantics

`move_to_dlq` inbound try `xadd` → on success `xack`, on failure `LEAVE pending` → recoverable via `XAUTOCLAIM` — preserved (P1-06 fixed).

## 9. Product-Family Enforcement

`rank_products_by_relevance(..., creator_id)` checks `_is_family_suppressed` via `query_metrics` 7d — preserved.

## 10. Re-engagement Frequency

`recent_reengagements_7d` via `query_metrics` D7 filtered by `user_id` — preserved (P2-B fixed).

## 11. Test Mapping

| Finding | Test | File | Result |
|---|---|---|---|
| P1-01 lock | same creator same fan serialized, different creator same fan independent, same creator different fans independent, lock key creator-scoped | `test_phase39_concurrency.py:TestCreatorScopedLock` | PASS |
| P1-02 atomic | concurrent knowledge updates 3 facts → all 3 persisted, JSONB preservation, duplicate generation_idempotent, different creators same generation namespace safe, concurrent temporal HOME+TEMPORARY, 30-item bound, XAUTOCLAIM retry idempotent, creator isolation | `TestAtomicFanKnowledge` | PASS |
| Invariants | single-pass, creator isolation, DropFans, emergency, rollback, canary HOLD | `TestInvariants` | PASS |

Total new tests 13, plus existing 20-30 regression ~518 — all green.

## 12. No New Architecture

`Redis Streams` `XREADGROUP/XAUTOCLAIM` preserved, `PostgreSQL` `user_profiles` JSONB preserved (just `FOR UPDATE`), `Telethon` single session, `Qwen` single, `DropFans` sole, `creator/fan` isolation, `generation_id` md5, `single-pass` 1/1/1/0, `canary` 1% HOLD.

