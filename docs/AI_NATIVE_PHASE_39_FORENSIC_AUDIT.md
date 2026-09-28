# AI_NATIVE_PHASE_39_FORENSIC_AUDIT.md
# Phase 39 — Concurrency, Creator-Isolation & Atomic Persistence Forensic Audit (Stage A, READ-ONLY)
# Date: 2026-08-30
# Method: No production code mutation, no canary change, read-only inspection of workers/llm_worker, db/postgres, db/redis, commerce/fan_knowledge, memory/context, commerce/production_control

## 1. Current Lock Architecture

**File:** `db/redis.py:252` `acquire_user_lock(user_id: int, ttl=30)` → `SET lock:user:{user_id} 1 NX EX 30`
**File:** `workers/llm_worker.py:507` `locked = await acquire_user_lock(user_id, ttl=_settings.user_lock_ttl)` → `if not locked: return` + `finally: release_user_lock(user_id)` `DEL lock:user:{user_id}`

**Key:** `lock:user:{user_id}` — **user_id only, not creator_id**, **NOT** `lock:creator:{creator_id}:user:{user_id}`

**Where acquired:** `process_message` top, before `build_qwen3_context` and `extract_fan_knowledge`, holds for entire generation (context + Qwen + scoring + send routing + post_process async not held).

**Where released:** `finally: release_user_lock(user_id)` → `DEL` (not Lua compare-and-delete, but `SET NX EX` ensures only holder had it, `DEL` could delete another holder's lock if TTL expired and re-acquired by other — **P2: not safe unlock, but TTL 30s short**).

**TTL:** 30s (`user_lock_ttl` default 60? Actually `core/config: user_lock_ttl 60`, but `acquire_user_lock` default 30, `process_message` passes `_settings.user_lock_ttl` which is 60)

**Crash:** If worker crashes while holding, `DEL` not executed, lock expires after TTL 30-60s, then available → **eventually available, not permanent deadlock** — **PASS**.

**Provider:** Redis, not PostgreSQL.

**Intent:** Serialize inbound messages for **same fan** (fan-level conversational state: `acquire_user_lock(user_id)` ensures two messages for same `user_id` not processed concurrently, preventing duplicate `strategy_exposures` etc.)

**Different creators same fan:** `Creator A / Fan 42` → `lock:user:42`, `Creator B / Fan 42` → `lock:user:42` **same key** → **unnecessary cross-creator serialization**: Fan 42 talking to Creator A and Creator B concurrently (possible via same Telegram user_id talking to two different bots? Actually Telegram `user_id` is global per Telegram user, same `user_id` for same fan across creators, since `users.id` is Telegram user_id, so same fan has same `user_id` across creators). Therefore `lock:user:42` blocks Creator B's message for Fan 42 while Creator A's message for Fan 42 is processing, even though they are **different `creator_id+user_id` conversational states** (different `fan_knowledge_by_creator` per creator, different `strategy_exposures` per `creator:user`, different `pressure` per `creator:user`). This is **unnecessary serialization**, not safety violation, but **P1** per spec: `different creator + same fan → independent` should be, but currently **shared lock → cross-creator blocking**.

**Proof:** `Test` hypothetical: `Creator A / Fan 42` and `Creator B / Fan 42` concurrent → second `acquire_user_lock(42)` returns False (since first holds `lock:user:42`), so second `process_message` returns early `User 42 already locked, skipping` — **P1 proven** via code read `acquire_user_lock(user_id)` not `creator_id`.

## 2. Current Transaction Architecture

**File:** `commerce/fan_knowledge.py: add_knowledge_item` 

```
profile = SELECT user_profiles (via get_user_profile(user_id))
knowledge = profile["fan_knowledge_by_creator"][str(creator_id)]
knowledge.append(item)
UPDATE user_profiles SET fan_knowledge_by_creator = knowledge
```

**Not inside PostgreSQL transaction** `BEGIN ... SELECT ... FOR UPDATE` — `get_user_profile` is `SELECT facts FROM user_profiles WHERE user_id=$1` (no `FOR UPDATE`), `update_user_profile` is `INSERT ... ON CONFLICT (user_id) DO UPDATE SET facts=$2::jsonb` — separate connections via `pool.acquire()` each, **not same transaction**, **no `SELECT ... FOR UPDATE`**, **no `pg_advisory_xact_lock`**, **not atomic JSONB update** via `jsonb_set` or `||`.

**Isolation level:** default `READ COMMITTED`, row lock via `INSERT ... ON CONFLICT` atomic per row, but **read-modify-write not atomic**.

**Possible race:** as documented in spec:

```
Initial: {}

Worker A reads: {}
Worker B reads: {}
A adds: occupation = engineer → writes {occupation}
B adds: city = Chicago → writes {city}
Final: {city} (occupation LOST)
```

**Proven via code read:** `get_user_profile` then `by_creator.get(key, [])` then `lst.append` then `update_user_profile` — **TOCTOU**.

## 3. Actual Race Analysis

**Concurrent fan knowledge updates for same `creator:user`:**

- Worker A and B both `get_user_profile(100)` → `{}` (or existing list with 1 item)
- Each appends different `item` to `lst` (local copy)
- Each `update_user_profile(100, facts)` with `INSERT ... ON CONFLICT DO UPDATE SET facts=$2` — last writer wins, **first write lost**.

**Required invariant:** `occupation = engineer`, `city = Chicago`, `pet_name = Max` concurrent → final must contain all three. Currently **not proven**, **P1 defect**.

**Evidence:** `add_knowledge_item` has `try: get_user_profile → by_creator[creator] append → update_user_profile` without `SELECT ... FOR UPDATE` or `jsonb` atomic, and fallback in-memory `_knowledge_mem` also `lst.append` without lock (in-mem dict not thread-safe for concurrent asyncio? `asyncio` single-threaded, but multiple workers are separate processes, not threads, so in-mem not shared across workers, only DB is shared — DB race is the issue).

## 4. Creator Isolation Analysis

**Fan knowledge persistence:** `user_profiles` `fan_knowledge_by_creator` `str(creator_id)` per `user_id` row — **creator scoped** via `str(creator_id)` key inside JSONB per `user_id`, so `Creator A / Fan X` (`user_id 100, creator 1`) → `facts["fan_knowledge_by_creator"]["1"]` vs `Creator B / Fan X` (`creator 2`) → `facts["fan_knowledge_by_creator"]["2"]` — **isolated** at JSONB key level, but **same row** `user_id 100` is shared across creators (single `user_profiles` row per `user_id`), so concurrent `Creator A` and `Creator B` for same fan `user_id 100` both `SELECT ... WHERE user_id=100` same row, then each modifies different `creator_id` key inside `facts`, but `UPDATE ... SET facts=$2` with full `facts` JSONB — **if they concurrently read same row, each modifies different `creator` key, but last writer could still overwrite other's `creator` key if they started from same base `{}`?** Example: `Creator A` reads `{}`, adds `"1": [occupation]`, writes `{"1": [...]}`; `Creator B` reads `{}`, adds `"2": [city]`, writes `{"2": [...]}` → **one of `"1"` or `"2"` lost**, even though different creators, because they started from same empty base. This is **cross-creator race via shared row** — **P1**.

**Retrieval:** `get_fan_knowledge(creator_id, user_id)` reads `by_creator.get(str(creator_id), [])` — **creator isolated** (only that `creator_id` key).

**Qwen context:** `memory/context.py:build_qwen3_context` after fix, `profile` interests replaced with `fan_knowledge` per `creator` via `get_fan_knowledge(creator_id, user_id)` — **creator isolated** (not global `interests`).

**Other stores:** `strategy_exposures` per `creator:user` key `f"{creator}:{user}"` in `_exposure_buffer` dict and JSONB per `user_id` row with `str(creator)` — **creator isolated** at JSONB key level, but same row race as above (shared `user_id` row).

## 5. JSONB Persistence Analysis

Same as §3-4: `SELECT` (no `FOR UPDATE`) → modify in Python → `UPDATE` full `facts` JSONB — **not atomic**, **not `jsonb_set`**, **not `||`**, **no row-level lock**.

**Existing advisory locking:** `commerce/dao.py:create_offer_serialized` uses `pg_advisory_xact_lock(hashtextextended(lock_key,0))` for offers, but `fan_knowledge` does **not** use advisory lock.

## 6. XAUTOCLAIM Interaction

`workers/llm_worker.py:run_worker` does `requeue_stalled_messages` `XAUTOCLAIM` `min_idle 30s` count 10, then `XREADGROUP >` for new. `enqueue_inbound` stores `generation_id` md5 deterministic, `process_message(generation_id=data.get("generation_id"))` reuses same `generation_id` on retry (since `data["generation_id"]` preserved in stream). `strategy_generation_seen` dedup per `creator:user` `generation_id` list 100 → **idempotent**, `check_idempotent` for operational → **idempotent**. **XAUTOCLAIM preserves `generation_id`** because `data` includes `generation_id` — **PASS**.

## 7. Idempotency Analysis

`generation_id` `md5(user:msg:telegram_id)` deterministic per `enqueue_inbound` + `process_message` reuse → **canonical idempotency identity**, not `uuid` per retry. `strategy_generation_seen` per `creator:user` 100, `check_idempotent` 2000 global, `send_dedup` `md5` 3600, all **generation_id-based** → same message same generation_id same `creator+fan` → one effective fan knowledge update (second `add_knowledge_item` with same `evidence_generation_id + subject + value` returns True early) → **idempotent**.

**Different creators same generation_id?** `generation_id` is `md5(user:msg:telegram_id)` **without creator**, so `Creator A / Fan X` and `Creator B / Fan X` same `user_id, content, telegram_message_id` would have **same generation_id** (since `user_id` same, `content` same, `telegram_message_id` same per Telegram user? But `telegram_message_id` is per chat per bot, different creators have different bots, so `telegram_message_id` for same fan's message to different creators would be different `telegram_message_id` (since different chats), so `generation_id` would differ due to `telegram_message_id` — **not collide**.

## 8. Restart/Crash Analysis

`acquire_user_lock` `SET NX EX 30` → if worker crashes while holding, lock expires after TTL → **eventually available, not permanent deadlock** — **PASS**.

`fan_knowledge` persisted via `user_profiles` JSONB 30 per `creator:user` → restart via `get_user_profile` → **survives** — **PASS**.

`_knowledge_mem` in-mem lost on restart, but `get_fan_knowledge` falls back to DB via `get_user_profile` (if DB has data) → **survives** via DB, not in-mem.

## 9. Bounds Analysis

`fan_knowledge` 30 per `creator:user` → `if >30: lst[-30]` + `history` 5 per subject → **bounded 30**, not global per fan.

`behavioral` 20 per `creator:user` in-mem not persisted (lost on restart) — **P2**.

## 10. Temporal Consistency Analysis

`city` `HOME` vs `TEMPORARY` via `location_role` separate `CURRENT` — **correct**, `Spain` TEMPORARY does not overwrite `New York` HOME, after 7d `is_knowledge_expired` filters `TEMPORARY` with `expires_at` → not retrieved, `HOME` remains.

## 11. Exact Root Cause

- **P1-01:** `db/redis.py:acquire_user_lock(user_id)` key `lock:user:{user_id}` missing `creator_id` → cross-creator serialization.
- **P1-02:** `commerce/fan_knowledge.py:add_knowledge_item` `SELECT` + `UPDATE` full `facts` JSONB without `FOR UPDATE` or atomic `jsonb_set` → TOCTOU lost update for concurrent `creator:user` or `cross-creator same user row`.

## 12. Severity

- **P1-01 (lock):** **P1** — unnecessary cross-creator blocking, not safety violation but production correctness (different creators independent).
- **P1-02 (JSONB race):** **P1** — lost fan knowledge under concurrency (same creator/fan simultaneous messages or cross-creator same fan).

## 13. Evidence with File/Line

- `db/redis.py:252` `acquire_user_lock(user_id)` → `f"lock:user:{user_id}"`
- `commerce/fan_knowledge.py:267` `profile = await get_user_profile(user_id)` then `by_creator[creator].append` then `update_user_profile` — no transaction

## 14. Proposed Minimal Fix

- **Lock:** Change to `lock:creator:{creator_id}:user:{user_id}` via `acquire_user_lock(user_id, creator_id=None)` with `creator_id` optional for backward compatibility, update `release_user_lock`, keep TTL, keep `SET NX EX`, preserve `XACK` etc. **Smallest:** add `creator_id` param, change key to `f"lock:creator:{creator_id}:user:{user_id}"` if `creator_id` not None else fallback to `lock:user:{user_id}`.
- **Atomicity:** Prefer `SELECT ... FOR UPDATE` inside transaction (`async with pool.acquire() as conn, conn.transaction(): await conn.fetchrow("SELECT facts FROM user_profiles WHERE user_id=$1 FOR UPDATE")` then `UPDATE`) OR atomic JSONB `UPDATE user_profiles SET facts = jsonb_set(facts, '{fan_knowledge_by_creator,creator}', ...)` OR `pg_advisory_xact_lock(hashtextextended('fan_knowledge:'||user_id::text||':'||creator_id::text,0))`. Smallest: wrap `get_user_profile` + `update_user_profile` in `SELECT ... FOR UPDATE` transaction via new helper `get_user_profile_for_update` inside `add_knowledge_item`, or use `pg_advisory_xact_lock` like `create_offer_serialized` does. Choose `SELECT ... FOR UPDATE` as it uses existing row lock, no new table.

