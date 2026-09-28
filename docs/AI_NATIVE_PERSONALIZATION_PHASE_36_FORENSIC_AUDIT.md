# AI_NATIVE_PERSONALIZATION_PHASE_36_FORENSIC_AUDIT.md
# Phase 36 — Deep Fan Intelligence Forensic Audit (Stage A, READ-ONLY, re-proved vs current tree)
# Date: 2026-08-30
# Method: No production code/schema/prompt/test/config/Redis/Postgres/canary mutation, re-traced actual code

## 1. Current Fan Data Model (Re-proved)

| Field | Storage | Source | Retrieved? | Qwen? | Scope | Confidence | Status |
|---|---|---|---|---|---|---|---|
| `first_name` | `users.first_name` | Telegram `upsert_user` | Yes `get_user` | Yes `Fan: {first_name}` | fan | No | **STORED+USED** |
| `interests` | `user_profiles.facts` `interests` per `user_id` **not creator** | `extract_and_update_profile` (post_process) + `long_term_memory` warps | Yes `get_user_profile` → `format_profile` | Yes `PROFILE:` but **leaks** Creator A→B | No | **PARTIAL LEAK P1** |
| `favorite_color` | `long_term_memory_by_creator` `str(creator)` → list `memory_item` | `commerce/long_term_memory.py:extract_explicit_memories` regex `my favorite color is (red|...)` | Yes `retrieve_relevant_memories` 3 | Yes `RELEVANT MEMORY` | EXPLICIT 1.0 | **STORED+USED** 1 subject |
| `commercial_preferences` | `commercial_preferences_by_creator` per `creator` | `fan_memory:update_fan_preference` **not called** in `llm_worker` | Yes `get_commercial_preferences` | Yes `AVAILABLE CONTENT` | WEAK 0.5 | **STORED+NOT USED** |
| `city Chicago` | **NOT STORED** | `extract_explicit_memories` no city regex | **No** | **No** | — | **NOT STORED** (P1) proves Phase 35 forensic correct |
| `occupation software engineer` | **NOT STORED** | no `occupation` regex | **No** | **No** | — | **NOT STORED** (P1) |
| `timezone` | **NOT STORED** | 0 hits in `memory/` | **No** | **No** | — | **NOT STORED** (P1) |
| `schedule night shifts` | **NOT STORED** | not in regex | **No** | **No** | — | **NOT STORED** |
| `pet dog Max` | **NOT STORED** | not `dog Max` regex | **No** | **No** | — | **NOT STORED** |

**Discrepancy vs Phase 35 proposed:** Phase 35 proposed `commerce/fan_knowledge.py` NEW 400 LOC `FanKnowledgeItem` 15 fields etc. **Actual repo has NO `commerce/fan_knowledge.py`** (glob `commerce/fan_knowledge.py` 0 hits) — **Phase 35 Stage B was deferred, not implemented** — re-proven: forensic claims `long_term_memory` shallow 4 subjects, not deep. **Correct.**

## 2. Current Creator Data Model

`personas` table `id, name, instructions, is_default` via `db/postgres.py` — `get_user_persona` / `get_default_persona` → `memory/context.py:build_qwen3_system_prompt` `persona_block` → Qwen **YES** but free-form only, no structured `age/occupation/location/interests/boundaries` — **P1** as Phase 35 forensic.

## 3. Current Memory Extraction

`commerce/long_term_memory.py:extract_explicit_memories` regex only 4 subjects (favorite_color, dislike, miami, interview, movie weak) — **PROVEN** via read `238` lines. `commerce/fan_knowledge.py` **NOT EXISTS** (re-proved). `memory/profile.py:extract_and_update_profile` not LLM, just `get_user_profile` + `update_user_profile` with `facts` — **no occupation/city extraction**.

## 4. Current Persistence

- `long_term_memory_by_creator` 20 per `creator:user` JSONB `user_profiles` → `add_memory_item` bounded 20, persistent via `update_user_profile` — **PERSISTENT**
- `recent conversation` 20 via `messages` table — **PERSISTENT but transient window**
- `summary` 1 per user — **PERSISTENT**
- `strategy_exposures` 50 per `creator:user` — **PERSISTENT**
- `fan_knowledge` deep **NOT EXISTS** — **MISSING**

## 5. Current Retrieval

`retrieve_relevant_memories(creator_id, user_id, current_topic, open_threads, limit 3)` → `is_memory_expired` filter, `overlap*0.5 + confidence*0.3 + recency*0.2 + importance*0.1` + boost `open_loop` 0.3 → `s>0.2` limit 3 → `RELEVANT MEMORY` → Qwen — **BOUNDED 3, relevance-ranked, not indiscriminate, but only 4 subjects to rank**.

## 6. Current Qwen Context

`memory/context.py:build_qwen3_context` emits: `CURRENT MESSAGE` + `RECENT 20/800/3` + `PERSONA` + `PROFILE interests` (leaky) + `COMMERCE STATE` + `OBJECTIVE/NBA` + `AVAILABLE CONTENT TOP2` + `RELEVANT MEMORY 3` + `SUMMARY 2` + `CONVERSATION open_threads` — **NO `timezone/local_time`, `city`, `occupation`, `schedule`, `pet`, `behavior`, `relationship` beyond open_loop** — **Qwen receives only recent + 3 LTM, not fan knowledge base**.

## 7. Current Temporal Handling

`is_memory_expired` `DECAY_SLOW 90` (fact), `MEDIUM 30` (preference), `FAST 7` (plan/open_loop), `SHORT 3` (mood) — **no TEMPORARY vs PERMANENT type**, `Spain this week` not captured, so **temporary not distinguished** (P1).

## 8. Current Timezone Handling

**None:** `grep timezone` 0 in `memory/`, no `timezone` column in `users`/`user_profiles`, no `city→timezone` lookup, no `local_time` injection — `Fan replies at 04:12 local` → Qwen **not know** — **P1**.

## 9. Current Behavioral Signals

**None beyond `strategy_exposures` 50 per `creator:user` and `compute_fatigue` per `strategy_family` last 5** — no `late-night`, `preferred length`, `topic affinity` — **NOT STORED**.

## 10. Current Relationship/Open-Loop Handling

`open_loop` via `long_term_memory` `OPEN_LOOP` `importance 0.8` with `resolve_open_loop` heuristic `went great` + `subj_tokens & msg_tokens` → `RESOLVED` (narrow, fails `It went great` without `interview` token — P1 false negative). `funnel_journey` 20 not used for relationship, `relationship_health` via `operational_intelligence` not memory.

## 11. Current Persona Implementation

`personas` free-form, `build_qwen3_system_prompt` `persona_block` → Qwen **YES**, but no structured `age/occupation/location/interests/boundaries` — P1.

## 12. Creator Isolation

`long_term_memory_by_creator` `str(creator)` per `user_id` → isolated ✓, `interests` per `user_id` not creator → **LEAK** `Creator A` `interests` visible to `Creator B` same fan (same `user_id` row) — **P1** (same as Phase 34).

## 13. Fan Isolation

`long_term_memory` per `creator:user` → `user 111` vs `222` same creator isolated via `user_id` row different → **PASS**.

## 14. Restart Behavior

`long_term_memory` persistent via `user_profiles` 20 → **survives**, `_exposure_buffer` in-mem → **LOST** but JSONB persists, `GenerationTelemetry` cache lost but `generation_telemetry` table not needed, `recent` via `messages` 20 → **RECONSTRUCTED**.

## 15. Retry Behavior

`generation_id` md5 deterministic `user:msg:telegram_id` per `workers/llm_worker.py:514` + `db/redis.py:enqueue_inbound` stores `generation_id` → `XAUTOCLAIM` preserves same `generation_id` → `strategy_generation_seen` dedup `generation_id` in `strategy_evidence_by_creator` 100 → **idempotent**, `add_memory_item` checks `found_idx` same `subject` → confidence compare, not duplicate — **PASS**.

## 16. Memory Deduplication

`add_memory_item` on `subject` same + `memory_type` same → `if new confidence > old → replace else if equal → newer wins else keep old but update last_seen + count` — **deduplicated via subject, not unlimited**.

## 17. Memory Expiry

`is_memory_expired` `confidence*exp(-days/decay) <0.2` → expired filtered on retrieval, but **not pruned** from storage (remains in JSONB 20) — **P2**.

## 18. Memory Bounds

- `long_term_memory` 20 per `creator:user` → `if >20: sorted last_seen 20` — **BOUNDED 20**.
- Global per fan `user_profiles.facts` per `user_id` row could grow with many `creator` keys (`long_term_memory_by_creator` per `creator` → 100 creators *20 = 2000 per row) — **P2**.

## 19. Privacy Exposure

`long_term_memory` `value=text[:50]` truncated 50, not full message — **minimal**. `publish_event` `message_preview: user_message[:100]` to Redis Pub/Sub — **PII via preview 100** (P2). No `emails/phone/secrets` via regex — **PASS**.

## 20. PII Exposure

Same — **no buyer email in telemetry**, `GenerationTelemetry` no `content`, `decision_trace` `OBJECTIVE=...` no PII — **PASS**.

## 21. Existing Tests

| Capability | Existing Test | What It Proves | What It Does NOT Prove |
|---|---|---|---|
| `memory extraction` | `test_phase25` `extract_explicit_memories` for `favorite_color` | Regex captures `favorite_color` | **Not** occupation/city/schedule/pet |
| `memory persistence` | `test_phase25` `Create memory item` | `add_memory_item` stores | **Not** that `software engineer` is stored |
| `memory retrieval` | `test_phase25` `retrieve_relevant_memories` 3 | Returns `favorite_color` when topic `red` | **Not** that `nurse` on `hospital` retrieves |
| `isolation` | `test_phase25` `Creator A vs B` exposures | `get_exposures_memory` isolated | **Not** that `interests` per `user_id` is isolated (it leaks) |

Many tests mock `get_user_profile` — over-mocked, cannot catch isolation leak.

## 22. Current Gaps (vs Phase 35 Requirements)

- P1: `occupation/city/pet/schedule/timezone` not captured (regex 4 only)
- P1: `interests` leak per `user_id` (not `creator:user`)
- P1: no temporal `TEMPORARY` vs `PERMANENT`
- P1: no `local_time` (no timezone)
- P1: no behavioral `late night`
- P1: `It went great` false negative (no `interview` token)
- P2: expired not pruned, global per-fan JSONB could grow
- P2: `lock:user:{user_id}` not creator-scoped
- P3: `favorite_color` only 7 colors

## 23. Architecture Constraints (Preserved)

`NO NEW LLM CALLS` (1/1/1/0), `NO NEW WORKERS/QUEUES`, `Redis Streams` `XREADGROUP/XAUTOCLAIM`, `Telethon` single session, `PostgreSQL` `user_profiles` JSONB, `DropFans` sole, `LLM LANGUAGE ONLY`, `creator/fan isolation`, `single-pass`, `production controls`, `safety hierarchy` — all must remain.

## 24. Exact Call Graph (Fan Knowledge)

```
Telegram "I work as software engineer from Chicago" → chatbotv2/handlers: save_inbound_message → debounce → enqueue_inbound (XADD with generation_id md5)
 → workers/llm_worker:process_message (acquire_user_lock, generation_id md5 reuse)
   → memory/context:build_qwen3_context (get_user_profile → profile={}, get_recent_messages 20)
   → commerce/long_term_memory:extract_explicit_memories (regex 4) → 0 for software engineer/Chicago → add_memory_item not called → NOT STORED
   → memory/context:retrieve_relevant_memories (creator 1, user 100, limit 3) → [] (no memory)
   → memory/context:build_qwen3_context → Qwen context: RECENT 20 + RELEVANT MEMORY (empty) + PROFILE (empty) → Qwen sees "I work as software engineer" as RECENT (transient), not persistent
   → 3 days later, get_recent_messages 20 no longer contains it → Qwen DOES NOT know occupation/city
```

**Break at extraction → persistence.**

## 25. Proposed Phase 35 Implementation Map (Stage A, Not Implemented)

Exact files (proposed, not yet created):
- `commerce/fan_knowledge.py` NEW 400 `FanKnowledgeItem` 15 fields `temporal_type` 8
- `commerce/temporal_context.py` NEW 150 `fan_timezone` via `city→timezone` `zoneinfo`
- `commerce/behavioral_intelligence.py` NEW 200 `behavioral_signals_by_creator` 20
- `commerce/relationship_intelligence.py` NEW 200 `open loops` extended
- `memory/creator_persona.py` NEW 150 structured `personas` metadata
- `memory/context.py` MOD 30 (FanKnowledge 3, Temporal, Relationship, Persona)
- `workers/llm_worker.py` MOD 20 (extract→add→retrieve)
- `db/postgres.py` MOD 10 (get/update fan_knowledge via JSONB)

Exact call points: `workers/llm_worker.py:process_message` after `extract_explicit_memories` → `extract_fan_knowledge` deterministic (regex + zoneinfo, not LLM) → `add_knowledge_item` idempotent via `generation_id` → `retrieve_relevant_knowledge` limit 3 → `build_qwen3_context` injection.

## Proposed vs Actual Discrepancy

**Phase 35 forensic proposed `commerce/fan_knowledge.py` 400 LOC but actual repo has NO such file** — re-proved via `glob commerce/fan_knowledge.py 0 hits` — **Stage B was deferred, not implemented** — correct per Stage A discipline.

## Final Classification

| Finding | Severity | Proven |
|---|---|---|
| `occupation/city/pet/schedule` not captured | P1 | **PROVEN** via `extract_explicit_memories` regex read |
| `interests` leak per `user_id` | P1 | **PROVEN** via `format_profile` `get_user_profile(user_id)` not `creator` |
| `timezone/local_time` not stored | P1 | **PROVEN** via `grep timezone` 0 |
| `temporal` not distinguished | P1 | **PROVEN** via `is_memory_expired` only `DECAY` |
| `behavioral` not stored | P1 | **PROVEN** via no `active hours` |
| `It went great` false negative | P1 | **PROVEN** via `subj_tokens & msg_tokens` no overlap |
| Expired not pruned | P2 | **PROVEN** via `add_memory_item` only `>20` prune |
| Proposed `fan_knowledge.py` not implemented | — | **PROVEN** via `glob` 0 hits — Stage B pending |

**Stage A complete — no production code modified.**

