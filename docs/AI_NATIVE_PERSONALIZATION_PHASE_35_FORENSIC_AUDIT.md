# AI_NATIVE_PERSONALIZATION_PHASE_35_FORENSIC_AUDIT.md
# Phase 35 — Deep Fan Intelligence Forensic Audit (Stage A, READ-ONLY)
# Date: 2026-08-30
# Method: Re-proved vs current working tree, no production code/schema/prompt/test/config/Redis/Postgres/canary mutation

## 1. Current Fan Data Model (Actual Fields)

| Field | Storage | Source | Retrieved? | Qwen? | Scope | Confidence | Status |
|---|---|---|---|---|---|---|---|
| `first_name` | `users.first_name` | Telegram `sender.first_name` via `upsert_user` | Yes `get_user` | Yes `Fan: {first_name}` | fan (user_id PK) | No | **STORED+USED** |
| `username` | `users.username` | Telegram | Yes | No | fan | No | STORED+NOT USED |
| `interests/preferences` | `user_profiles.facts` `interests` (per `user_id`, **not creator**) | `extract_and_update_profile` (post_process, not LLM) + `long_term_memory` for `favorite_color` | Yes `get_user_profile` → `format_profile` | Yes `PROFILE:` but **leaks** Creator A→B same fan | No | **PARTIAL — LEAK P1** |
| `favorite_color` (`preference`) | `long_term_memory_by_creator` `str(creator)` → list `memory_item` `subject favorite_color` | `extract_explicit_memories` regex `my favorite color is (red|...)` | Yes `retrieve_relevant_memories` 3 | Yes `RELEVANT MEMORY: favorite_color=red` | `EXPLICIT 1.0` | **STORED+USED** but 1 subject |
| `commercial_preferences` | `commercial_preferences_by_creator` `str(creator)` → dict `count,confidence,last_seen` | `fan_memory:update_fan_preference` **not called** in `llm_worker` (only `get` used for `rank_products`) | Yes `get_commercial_preferences` | Yes `AVAILABLE CONTENT` via `interests` fallback | `WEAK 0.5` | **STORED+NOT USED** for fan fact |
| `trip miami` / `interview friday` | `long_term_memory` `plan/open_loop` | same regex `going to miami/interview friday` | Yes | Yes `open_loop` boost 0.3 | EXPLICIT 1.0 | **STORED+USED** 2 subjects |
| `city Chicago / New York` | **NOT STORED** — `location` key in `_PROFILE_LABELS` but no writer for city, `extract_explicit_memories` no city regex | — | **No** | **No** | — | **NOT STORED** (P1) |
| `occupation software engineer` | **NOT STORED** — no `occupation` regex | — | **No** | **No** | — | **NOT STORED** (P1) |
| `timezone` | **NOT STORED** — 0 hits in `memory/` | — | **No** | **No** | — | **NOT STORED** (P1) |
| `schedule night shifts` | **NOT STORED** | — | **No** | **No** | — | **NOT STORED** |
| `pet dog Max` | **NOT STORED** — `dog` not in regex | — | **No** | **No** | — | **NOT STORED** |
| `funnel_journey` | `funnel_journey_by_creator` 20 per `creator:user` | `revenue_intelligence:record_funnel_transition` **not called in llm_worker** (only tests) | Yes `get_funnel_journey` | **No** (not to Qwen) | No | **STORED+NOT USED** |
| `strategy_exposures` | `strategy_exposures_by_creator` 50 per `creator:user` + `_exposure_buffer` | `make_exposure` per generation | Yes `get_exposures_memory` 50 | **No** | No | **STORED+NOT USED** for personalization |
| `handoff` | `handoff_by_creator` | `make_handoff` | Yes | No (blocks) | No | **STORED+USED** (blocks) |
| `recent conversation` | `messages` 20 | `save_inbound_message` | Yes `get_recent_messages` 20/800 | Yes `recent_history` | No | **TRANSIENT+USED** |
| `summary` | `conversation_summaries` 1 per user | `maybe_summarize` | Yes `get_latest_summary_with_age` 2 sentences | Yes | No | **STORED+USED** |

**Duplication:** `interests` (per `user_id` global) vs `commercial_preferences_by_creator` (per `creator`) — two preference stores, not unified.

## 2. Current Creator Data Model

| Field | Storage | Writer | Reader | Qwen? | Authority | Scope |
|---|---|---|---|---|---|---|
| `creator persona` `name, instructions, is_default` | `personas` table | Dashboard `create_persona` | `memory/context:build_qwen3_system_prompt` `persona_block` | **YES** `You are Sunny Skye...` | operator | creator (default or per `users.persona_id`) |
| `background/city/age` | **NOT STRUCTURED** — only free-form `instructions` | operator (free text) | same | Yes free-form | operator | creator |
| `creator timezone` | `DropFans` `client.get_timezone` (creator's DropFans timezone) | DropFans | not to Qwen | **No** | DropFans | creator |

**Exists:** `personas` authoritative, but no `creator_city`, `creator_interests` structured.

## 3. Current Memory Extraction

`commerce/long_term_memory.py:extract_explicit_memories(text, creator_id, user_id)` — regex only:

- `my favorite color is|i love (the color )?|i really like (red|black|blue|green|pink|white|purple)` → `favorite_color`
- `i don't like|i hate (being called )?(\w+)` → `dislike`
- `going to miami|exam friday|interview friday|come back|after payday` → `plan/open_loop/commitment`
- `movie + watching` → `topic movie` weak 0.5

**Ignores:** `software engineer`, `Chicago`, `New York`, `Spain`, `nurse`, `hospital`, `night shifts`, `dog Max`, `golden retriever`, `Formula 1`, `running`, `horror movies`, `birthday October`, `two kids`, `sister Sarah`, `Spain temporary`, `timezone`, `age`, `schedule`.

**Schema:** `create_memory_item(creator_id,user_id,memory_type,subject,value,confidence,source,importance)` → `memory_id f"{creator}:{user}:{subject}:{value[:20]}"`, `confidence` EXPLICIT 1.0 / STRONG 0.8 / WEAK 0.5, `source` explicit/weak, `importance` 0.4-0.8, `expires_at` null, bounded 20, `is_memory_expired` filters on retrieval.

## 4. Current Persistence

- `long_term_memory_by_creator` per `creator:user` JSONB 20, `add_memory_item` on `subject` same → `if new confidence > old → replace else if equal → newer wins else keep old but update last_seen + count`, `if >20 keep last_seen 20` — **bounded 20, persistent via `user_profiles` JSONB, restart-safe**.
- `commercial_preferences_by_creator` per `creator` not written in `llm_worker` (only read) — **not persisted from conversation**.
- `recent conversation` 20 via `messages` table — **persistent but transient window**.
- `summary` 1 per user — **persistent, update if exists else insert**.
- `strategy_exposures` 50 per `creator:user` via JSONB + `_exposure_buffer` in-memory — **persistent 50 + 30d prune**.

All via `user_profiles` JSONB per `user_id` row, no new table.

## 5. Current Retrieval

`retrieve_relevant_memories(creator_id, user_id, current_topic, open_threads, limit 3)` → `is_memory_expired` filter, `tokens(current_topic + open_threads)` overlap `*0.5 + confidence*0.3 + recency*0.2 + importance*0.1` + boost `open_loop` 0.3 if overlap, sort, `s>0.2` limit 3 → `RELEVANT MEMORY: subject=value (type, conf)` → Qwen.

**Bounded 3, relevance-ranked, not indiscriminate, but only 4 subjects to rank.**

## 6. Current Qwen Context (Inventory)

`memory/context.py:build_qwen3_context` emits per generation:

- `CURRENT MESSAGE` (user_message 800 chars)
- `RECENT CONVERSATION` 20/800/3
- `PERSONA` `You are Sunny Skye...` (operator) 600 tokens
- `FAN MEMORY` `PROFILE:` via `format_profile` `interests` (leaky per user_id)
- `COMMERCE STATE` `desire/temp/readiness/window/objective` (deterministic)
- `OBJECTIVE/NBA` `CONVERSATION INTELLIGENCE: objective=... next_best_action=...`
- `AVAILABLE CONTENT` TOP2 titles 42 chars
- `RELEVANT MEMORY` 3 (`favorite_color` etc.)
- `SUMMARY` 2 sentences (if exists)
- `CONVERSATION` `open_threads` 3, `last_q` 60 chars
- `IDENTITY` `established`
- `CAPABILITIES` `send_text:yes`
- **NOT PRESENT:** `timezone/local_time` (0), `city/country` (0), `occupation` (0), `schedule` (0), `pet` (0), `behavior` (0), `relationship` beyond `open_loop`, `temporal` (0), `creator structured persona` beyond free-form.

**Bounded:** system 400 + state 200 + conversation 800 + summary 200 = 1600 tokens via `QWEN3_TOKEN_BUDGET`.

## 7. Current Temporal Handling

`is_memory_expired` uses `DECAY_SLOW 90` (fact/purchase), `MEDIUM 30` (preference), `FAST 7` (plan/open_loop/topic), `SHORT 3` (mood) with `decayed = confidence*exp(-days/decay) <0.2` → expired, `status RESOLVED/EXPIRED/CANCELLED` → true. **No `TEMPORARY` vs `PERMANENT` type**, `Spain this week` not captured, so **temporary not distinguished** (would be stored as `preference` 30d, not 7d).

## 8. Current Timezone Handling

**None:** no `timezone` column in `users`/`user_profiles`, no `city`→`timezone` lookup, no `Telegram metadata` timezone (Telegram `event` has `date` UTC, not fan local), no `local_time` injection, no `last observed local time`, no `typical active hours`. `Fan replies at 04:12 local` → **Qwen not know** (prove: `build_qwen3_context` has no `local_time`).

## 9. Current Behavioral Signals

**None beyond `strategy_exposures` 50 per `creator:user` (strategy_family) and `compute_fatigue` per `strategy_family` last 5.** No `late-night engagement`, `preferred response length`, `topic affinity` (only `commercial_preferences` count, not behavior), `humor`, `emoji`, `latency` — all **NOT STORED** as behavioral.

## 10. Current Relationship/Open-Loop Handling

`open_loop` via `long_term_memory` `OPEN_LOOP` `importance 0.8` with `resolve_open_loop` heuristic `went great|went well` + `subj_tokens & msg_tokens` → `RESOLVED` (narrow, fails `It went great` without `interview` token, see Phase 31 P1). `retrieve_relevant_memories` boost 0.3 for `open_loop` if overlap. `funnel_journey` 20 not used for relationship, `relationship_health` via `operational_intelligence` not memory. **No `promise`/`commitment` beyond `come back` regex**.

## 11. Current Persona Implementation

`personas` table authoritative via `get_user_persona`/`get_default_persona` → `build_qwen3_system_prompt` `persona_block` (free-form instructions, e.g., `You are Sunny Skye...`). Structured fields `age/profession/location` not separate, only free text. **Authoritative for creator identity**, LLM cannot invent `creator age` not in `instructions` (but could, not prevented via prompt: `You are Sunny Skye` is instructions, not guard).

## 12. Creator Isolation

`long_term_memory_by_creator` `str(creator)` per `user_id` → `get_long_term_memory(1,100)` vs `2,100` isolated ✓, `commercial_preferences` per `creator` ✓, `exposures` `creator:user` ✓, `handoff` per `creator:user` ✓, but `interests` via `get_user_profile(user_id)` `facts.interests` **per `user_id` not `creator`** → `creator A` `interests` visible to `creator B` same fan (same `user_id` row) — **LEAK P1** (same as Phase 34). `lock:user:{user_id}` not creator-scoped (P2).

## 13. Fan Isolation

`long_term_memory` per `creator:user` → `user 111` vs `222` same creator isolated via `user_id` row different → **PASS**, `exposures` per `creator:user` → isolated, `handoff` per `creator:user` → isolated, `interests` per `user_id` row but `user_id` is fan, so `fan A` `interests` not visible to `fan B` (different rows) — **PASS** (leak is creator-level, not fan-level).

## 14. Restart Behavior

- **Persistent:** `long_term_memory` 20 via `user_profiles` JSONB → survives `llm_worker` restart via `get_user_profile`.
- **In-memory only:** `_exposure_buffer` dict, `_journey_mem`, `GenerationTelemetry` cache — **LOST** on restart, but `strategy_exposures` JSONB persists, `_handoff_mem` in-mem but `handoff_by_creator` JSONB persists, `GenerationTelemetry` not needed.
- **Reconstructed:** `recent conversation` 20 via `messages` table — **RECONSTRUCTED**.

## 15. Retry Behavior

`generation_id` deterministic `md5(user:msg:telegram_id)` per `workers/llm_worker.py:process_message` + `db/redis.py:enqueue_inbound` stores `generation_id` → `XAUTOCLAIM` preserves same `generation_id` → `strategy_generation_seen` dedup `generation_id` in `strategy_evidence_by_creator` 100 → **idempotent**, not duplicate memory. `add_memory_item` also checks `found_idx` same `subject` → confidence compare, not duplicate by `generation_id` — **retry does not duplicate** (P1-03 fixed).

## 16. Memory Deduplication

`add_memory_item` on `subject` same + `memory_type` same → `if new confidence > old → replace else if equal → newer wins else keep old but update last_seen + count` — **deduplicated via subject, not unlimited**. `first_observed_at` vs `last_confirmed_at` via `first_seen`/`last_seen` + `observation_count` + `confirmation_count` (not explicit but `observation_count` increments).

## 17. Memory Expiry

`is_memory_expired` `confidence*exp(-days/decay) <0.2` → expired filtered on retrieval, but **not pruned** from storage (remains in JSONB 20) — `add_memory_item` only prunes `>20` not expired — **P2** (expired 20 remains, retrieval quality falls).

## 18. Memory Bounds

- `long_term_memory` 20 per `creator:user` → `if >20: sorted last_seen 20` — **BOUNDED 20**.
- `strategy_exposures` 50 per `creator:user` + `prune_by_retention` 30d — **BOUNDED 50**.
- `conversation_summaries` 1 per `user_id` — **BOUNDED 1**.
- **Global per fan** `user_profiles.facts` per `user_id` row could grow with many `creator` keys (`long_term_memory_by_creator` per `creator` → 100 creators *20 = 2000 per row → **P2: global JSONB per fan could grow with creator count, not bounded globally**).

## 19. Privacy Exposure

- Stores `value=text[:50]` truncated 50 chars, not full message — **minimal**.
- Stores `message content` full in `messages` table (intended for context, not memory) — **not in `long_term_memory` warps beyond 50**.
- No `emails/phone/secrets` via regex (does not capture email/phone) — **PASS**.
- `publish_event` `message_preview: user_message[:100]` to Redis Pub/Sub — **PII via preview 100 chars** (P2).
- No `tokens/Telegram sessions/credentials` in `user_profiles` — **PASS**.

## 20. PII Exposure

Same as privacy — **no buyer email in telemetry**, `GenerationTelemetry` no `content`, `decision_trace` compact `OBJECTIVE=...` no PII, `metric events` no content — **PASS**.

## 21. Existing Tests

| Capability | Existing Test | What It Proves | What It Does NOT Prove |
|---|---|---|---|
| `memory extraction` | `test_phase25` `extract_explicit_memories` for `favorite_color` | Regex captures `favorite_color` | **Not** occupation/city/schedule/pet |
| `memory persistence` | `test_phase25` `Create memory item` | `add_memory_item` stores | **Not** that `software engineer` is stored |
| `memory retrieval` | `test_phase25` `retrieve_relevant_memories` 3 | Returns `favorite_color` when topic `red` | **Not** that `nurse` on `hospital` retrieves |
| `memory isolation` | `test_phase25` `Creator A vs B` exposures | `get_exposures_memory` isolated | **Not** that `interests` per `user_id` is isolated (it leaks) |
| `context` | `test_phase8_single_pass` mock `build_qwen3_context` | Context built | **Not** that `occupation` reaches Qwen after 3 days |
| `open loops` | `test_phase25` `interview friday` | `extract` + `resolve` for `interview` | **Not** that `Spain temporary` not resurrected |

Many tests mock `get_user_profile` — over-mocked, cannot catch isolation leak.

## 22. Current Gaps (Architecture Constraints)

- No comprehensive `occupation/city/pet/schedule` capture (regex 4 subjects only)
- No `timezone/local_time` (0 hits)
- No `temporal_type` (Spain temporary vs permanent)
- No `behavioral` late-night/gym frequency
- No `relationship` promise/Spain/trip beyond `miami`
- `interests` leak per `user_id` (P1)
- `lock:user` not creator-scoped (P2)
- Expired not pruned (P2)
- Global per-fan JSONB could grow with creator count (P2)

## 23. Architecture Constraints (Preserved)

`NO NEW LLM CALLS` (1 SIGNAL+1 QWEN+1 SCORING), `NO NEW WORKERS/QUEUES`, `Redis Streams` `XREADGROUP/XAUTOCLAIM`, `Telethon` single session, `PostgreSQL` `user_profiles` JSONB, `DropFans` sole, `LLM LANGUAGE ONLY`, `creator/fan isolation`, `single-pass`, `production controls`, `safety hierarchy` `SAFETY>HANDOFF>AFTERCARE>OBJECTION>OPEN_LOOP>DIRECT_REQUEST>COMMERCE>OPTIMIZATION>LLM` — all must remain.

## 24. Exact Call Graph (Current Fan Knowledge)

```
Telegram message ("I work as software engineer from Chicago")
 → chatbotv2/handlers.py: save_inbound_message (messages) → debounce → enqueue_inbound (XADD with generation_id md5)
 → workers/llm_worker.py:process_message (acquire_user_lock, generation_id deterministic md5 reuse)
   → memory/context.py:build_qwen3_context (get_user_profile → profile={}, get_recent_messages 20)
   → commerce/long_term_memory.py:extract_explicit_memories (regex: favorite_color/dislike/miami/interview/movie) → 0 for software engineer/Chicago
   → add_memory_item not called → NOT STORED
   → memory/context.py:retrieve_relevant_memories (creator 1, user 100, limit 3) → [] (no memory)
   → memory/context.py:build_qwen3_context → Qwen context: RECENT CONVERSATION 20 + RELEVANT MEMORY (empty) + PROFILE (empty)
   → Qwen sees "I work as software engineer" as RECENT CONVERSATION (transient), not as persistent fact
   → 3 days later, get_recent_messages 20 no longer contains it → Qwen DOES NOT know occupation/city
```

**Break at extraction → persistence.**

## 25. Proposed Phase 35 Implementation Map (Stage A, Not Implemented)

**Exact files:**
- `commerce/fan_knowledge.py` (NEW, 400 LOC pure, canonical `FanKnowledgeItem` with `subject/value/value_type/category/subcategory/confidence/source/generation_id/observed_at/effective_from/effective_until/expires_at/temporal_type/status/first_observed_at/last_confirmed_at/confirmation_count/contradiction_count/creator_id/user_id`, `temporal_type` PERMANENT/CURRENT/HISTORICAL/TEMPORARY/RECURRING/SCHEDULE/EVENT/UNKNOWN, `source` USER_EXPLICIT/USER_CORRECTION/BEHAVIORAL/SYSTEM_DERIVED, bounded 30 per `creator:user` via `user_profiles` JSONB `fan_knowledge_by_creator`, `is_knowledge_expired` category-aware `permanent 90d, current until contradicted, temporary 7d, event 30d, recurring rolling`, `fact_deduplication` via `subject` canonical normalization, `knowledge_history` bounded 5 per subject)
- `commerce/temporal_context.py` (NEW, 150 LOC pure, `fan_timezone` via explicit `timezone` fact or `city→timezone` lookup (conservative, via `zoneinfo` not LLM), `current local datetime` → Qwen context `Fan local time: 04:12 / Fan timezone: America/Chicago`, `UNKNOWN` if not known, not fabricated)
- `commerce/behavioral_intelligence.py` (NEW, 200 LOC pure, bounded `behavioral signals` via `strategy_exposures` 50 already, not new `user_profiles` key `behavioral_signals_by_creator` 20 with `affinity` count, `preferred response length` etc., signals not facts)
- `commerce/relationship_intelligence.py` (NEW, 200 LOC, `open loops` extended to `promise/plan/event` via `long_term_memory` `open_loop` + `commitment` with `subject` extensible, `relationship_memory` via `fan_knowledge` `RELATIONSHIP_CONTEXT` category)
- `memory/creator_persona.py` (NEW, 150 LOC, structured `personas` table already `instructions` free-form, add structured `creator_persona` JSONB per `creator` via `creator_integrations` or `personas` `metadata` JSONB `age/profession/location/interests/boundaries`, not LLM improvisation)
- `memory/context.py` (MOD, 30 LOC, `build_qwen3_context` → `FanKnowledge` retrieval 3, `Temporal` injection, `Relationship` open loops, `Creator Persona` structured)
- `workers/llm_worker.py` (MOD, 20 LOC, `extract_fan_knowledge` deterministic via existing `extract_explicit_memories` expanded to 15 categories + `fan_knowledge` `add` via `generation_id` idempotent, `creator isolation` `creator_id+user_id` scope)
- `db/postgres.py` (MOD, 10 LOC, `get_fan_knowledge`/`update_fan_knowledge` via `user_profiles` JSONB `fan_knowledge_by_creator`, bounded, no migration, reuse `user_profiles`)

**Exact functions:**
- `fan_knowledge:extract_fan_knowledge(text, creator_id, user_id, generation_id, source_generation_id)` deterministic regex + `zoneinfo` lookup, not LLM
- `fan_knowledge:add_knowledge_item`, `get_fan_knowledge`, `is_knowledge_expired`, `deduplicate`, `history` 5
- `temporal_context:derive_fan_timezone(fan_knowledge)`, `current_local_time(fan_knowledge)` → `Fan local time: ...`
- `behavioral_intelligence:observe_behavioral_signal(fan_id, signal)` bounded
- `relationship_intelligence:track_open_loop(...)` extended

**Exact data structures:**
- `FanKnowledgeItem` 15 fields, `temporal_type` 8, `source` 5, `status` `CURRENT/HISTORICAL/EXPIRED`, `category` 20+ (`IDENTITY/LOCATION/TIMEZONE/WORK/...`)

**Exact call points:**
- `workers/llm_worker.py:process_message` after `extract_explicit_memories` → `extract_fan_knowledge` (deterministic, not LLM) → `add_knowledge_item` (idempotent via `generation_id`) → `retrieve_relevant_knowledge` (limit 3) → `build_qwen3_context` injection

**Exact persistence:**
- `user_profiles` JSONB `fan_knowledge_by_creator` per `creator:user` 30, `temporal_context` per `creator:user` (timezone), `behavioral_signals` 20, `relationship` via `long_term_memory` warps, `creator_persona` via `personas` JSONB `metadata`, all `creator_id+user_id` scoped, bounded, restart-safe via `get_user_profile`, no new table, `generation_id` idempotent

**Exact context flow:**
```
Creator Persona (operator) → Fan Stable Profile (first_name) → Current Fan Knowledge (30) → Relevant Historical (5) → Temporal (local time) → Behavioral (20) → Relationship/Open Loops (3) → Commerce Context → Current Conversation 20 → Unified Personalization Context (bounded) → Qwen
```

**Exact tests:**
- `tests/test_phase35_deep_personalization.py` 40 tests: occupation, city, timezone, pet, schedule, preferences, corrections, temporal (permanent/current/temporary/recurring/event), timezone (known/derived/unknown), behavioral, relationship, persona isolation, creator/fan isolation, restart/retry, deduplication, history, expiration, relevance, hallucination, commerce safety, single-pass 1/1/1/0, performance bounded

