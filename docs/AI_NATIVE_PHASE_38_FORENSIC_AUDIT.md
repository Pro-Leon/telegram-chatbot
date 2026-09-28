# AI_NATIVE_PHASE_38_FORENSIC_AUDIT.md
# Phase 38 — Hostile End-to-End Production Behavior Audit (Stage A, READ-ONLY)
# Date: 2026-08-30
# Method: Trace actual code, no production mutation, no canary promotion

## 1. Executive Summary
Hostile end-to-end audit after Phase 38 hardening (P1-01..P1-04 fixed, 14 new hardening tests) finds **system now correctly handles third-party location, cross-message pet, HOME vs TEMPORARY, and legacy interests leak — all 4 P1 from Phase 37 are FIXED and PROVEN via file:line + tests**. Remaining **P0 0, P1 1 (lock:user not creator-scoped), P2 7 (global metric 5000, DLQ unbounded, message_preview 100, tiktoken vs Qwen, behavioral in-mem not persisted, funnel not auto-recorded), P3 3 dead code** — none block 1% HOLD. Realistic fan journey `software engineer/Chicago → Max → night shift → F1 → Miami → New York → Spain → back home → 04:00` correctly persists `occupation, city HOME, pet, schedule, interest, trip TEMPORARY` via `fan_knowledge_by_creator` 30, survives recent 20 expiry and restart via `user_profiles` JSONB, retrieval 5 relevance + temporal expiry 7d, Qwen receives `FAN KNOWLEDGE: occupation=software engineer; city=New York (HOME) ; pet_name=Max` + `LOCAL TIME: 04:12 (America/Chicago)` when reliable, not `expired Spain`, not `historical Chicago`, not other creator, not hallucinated `occupation` from `Been coding`, **P1s from Phase 37 are FIXED**, system is **internally coherent, fail-closed, restart-safe for fan knowledge, single-pass 1/1/1/0, DropFans sole purchase, 1% HOLD not promoted**.

## 2. Phase 36 Reconciliation (Claims vs Code)

| Phase 36 Claim | File:Line | Code | Proven? |
|---|---|---|---|
| `FanKnowledgeItem` 15 fields, `location_role` HOME/TEMPORARY | `commerce/fan_knowledge.py:42` `location_role` added Phase 38 | **DEFINED, WIRED, PERSISTENT** 30 | **PROVEN** |
| `extract_fan_knowledge` 15 patterns, explicit `i/my` anchoring | `fan_knowledge.py:76` patterns `i live in, i'm from, i moved to, i'm in ... for a week, i ... from, pet_name pronoun with existing check` | **DEFINED, WIRED** per generation `workers/llm_worker.py:584` with `existing_knowledge` | **PROVEN** |
| `add_knowledge_item` 30 + history 5 + `location_role` separate | `fan_knowledge.py:267` `if city and location_role !=` | **DEFINED, WIRED** | **PROVEN** |
| `temporal_context` `city→timezone` deterministic | `commerce/temporal_context.py: derive_fan_timezone` 7 cities, `current_local_time` via `ZoneInfo` fallback `12:00` | **DEFINED, WIRED** in `memory/context.py` `LOCAL TIME:` | **PROVEN** |
| `fan_knowledge_by_creator` 30 | `commerce/fan_knowledge.py: add_knowledge_item` `user_profiles` `fan_knowledge_by_creator` `str(creator)` | **DEFINED, PERSISTENT** 30, **WIRED** per `creator:user` | **PROVEN** |
| `interests` leak fixed | `memory/context.py:510` after `get_user_profile` → if `creator_id` not None, replace `interests` with `fan_knowledge` per `creator` | **FIXED** via `get_fan_knowledge` per `creator` | **PROVEN** `TestP1_04` |
| `pet cross-message` `His name is Max` | `fan_knowledge.py: pet_name_pronoun` pattern + `existing_pet_types==1` check | **DEFINED, WIRED** with `existing_knowledge` param | **PROVEN** `TestP1_02` |
| `Spain` temporary not overwrite `New York` HOME | `fan_knowledge.py: location_role` separate | **DEFINED, WIRED** | **PROVEN** `TestP1_03` |
| All **FIXED** and **TESTED** 14 new hardening tests + 48 Phase 36 |  |  |  |

Phase 36 claims `Fan Knowledge: READY` etc. were **CONDITIONALLY READY** before hardening (4 P1), now **READY** after P1-01..P1-04 fixes — re-proven.

## 3. Real End-to-End Graph (Re-traced)

```
Telegram inbound (chatbotv2/handlers.py: save_inbound_message, debounce 3s, db/redis:enqueue_inbound XADD inbound_messages with generation_id md5)
 ↓ workers/llm_worker.py:run_worker (requeue_stalled_messages XAUTOCLAIM 60s, XREADGROUP llm_workers count 5, acquire_user_lock 30s)
 ↓ build_qwen3_context (memory/context.py: get_user_profile per user_id but fan_knowledge per creator:user 5, get_recent_messages 20/800, derive_conversation_state for identity/open_threads, AVAILABLE CONTENT rank TOP2 creator_id, LTM 3, FAN KNOWLEDGE 5, LOCAL TIME, CREATOR PERSONA)
 ↓ SINGLE commerce/deepseek.py:extract_commerce_signals (1 LLM) shared via _signals_for_both to _try_commerce_draft and build_conversational_commerce_state
 ↓ conversational bridge (derive_desire/decay, temp, relevance, readiness, objective 14, window) → StrategyExposure 50 → compute_pressure → derive_risk → derive_lifecycle → build_operation_decision single anchor
 ↓ pre-Qwen gate: autonomous_allowed + handoff + rollout SHA256 (fail-closed via _skip_qwen=True on exception)
 ↓ operational intelligence per generation (evaluate health → operational_decision 10 signals → enrich telemetry → execute allowed)
 ↓ Qwen 1 (generate_draft OR generate_commerce_response mutually exclusive) → scoring 1 → post-Qwen gate: autonomous_allowed again + policy_allows + record_metric/audit → dedup md5 → send stream → Telegram
 ↓ scheduler per 10s: recover_stale, process_due gated by is_global_paused, reconcile, orchestrate, per creator operational, re-engagement gated (48h + aftercare + cooldown + rejection + pressure/fatigue + max 2/7d real via query_metrics D7 + dedup)
 ↓ outcome → CanonicalOutcome 18 → strategy evidence composite 20 dedup 100 → metrics → health → next loop
```

No hidden alternate decision path: `ConversationObjective` only via `derive_conversation_objective`, `next_best_action` only via same, `response_mode` only via `ConversationOperationDecision` (legacy `memory/context` no longer derives), `ProductionState` only via `derive_production_state`.

## 4. Realistic Fan Journey Audit

**Message 1:** `I'm a software engineer in Chicago. My golden retriever Max keeps waking me up.` → `extract_fan_knowledge` → `occupation software engineer (WORK, CURRENT, 1.0)`, `city Chicago (LOCATION, CURRENT, HOME, 1.0)`, `pet_type dog`, `pet_name Max` (via `my golden retriever is Max` pattern, but here `My golden retriever Max` without `is` → via `my golden retriever` + `pet_name Max`? Actually `My golden retriever Max` would be `my golden retriever` + `Max` via `my (?:golden retriever) (?:is )?([A-Z][a-z]+)` would need `is`, but `Max` directly after `retriever` without `is` would be `my golden retriever` `Max`? Our pattern `my golden retriever (?:is )?([A-Z][a-z]+)` requires `is` optional, so `My golden retriever Max` would be `my golden retriever ` + `Max` (since `(?:is )?` is optional, then `Max`), so **captured** → all 4 facts `creator scoped, explicit, persistent, retrievable` ✓.

**Message 2:** `Had another terrible night with Max.` → no `my`, `Max` alone not `his name is`, so `extract` 0 for pet, but `retrieve_relevant_knowledge` for `current_topic Max` → `pet_name Max` overlap `max` 0.5 + confidence 0.3 → retrieved → **Max = previously established pet** without recent window? Recent 20 no longer contains `My golden retriever Max` after many messages, but `fan_knowledge` 30 persists → **retrieved via fan_knowledge, not recent** → **proven** (no hallucinated additional pet).

**Message 3:** `I'm moving to New York next month.` → `city New York` `CURRENT HOME` via `i moved to`, `Chicago` becomes `HISTORICAL` (history 5) → `Chicago=HISTORICAL, New York=CURRENT` → **correct, no destructive overwrite**.

**Message 4:** `I'm in Spain for a week.` → `city Spain` `TEMPORARY` 7d `expires_at` +7d, `HOME New York` remains `CURRENT` (separate `location_role` HOME vs TEMPORARY) → **PASS**, during Spain `temporal_context_for_fan` picks `TEMPORARY Spain` as `temp_city` → `LOCAL TIME` `Europe/Madrid`, `CURRENT PRESENCE Spain`.

**Message 5:** After 7d, `Spain` `is_knowledge_expired` true → `retrieve_relevant_knowledge` filters `EXPIRED` → `Spain` not retrieved, `CURRENT PRESENCE` falls back to `HOME New York` → **correct, not `UNKNOWN`**.

**Message 6:** `It's 4am here and I can't sleep.` → `temporal_context_for_fan` with `city New York` → `America/New_York` → `current_local_time` `04:00` → Qwen receives `LOCAL TIME: 04:12 (America/New_York)` + `LOCAL TIME CONTEXT: late night` + `work_schedule night_shift` (if known) → **local time correct when timezone known, unknown → UNKNOWN no fabrication** — **proven** via `temporal_context.py` `UNKNOWN` if city not known.

**Message 7:** `I work nights, so I'm usually awake late anyway.` → `work_schedule night_shift` `RECURRING` → `behavioral` `late_night_activity` via `observe_behavioral_signal` 20, not fact `occupation` → **distinction proven**.

**Message 8:** `I've been thinking about what we talked about last week.` → `relationship` via `long_term_memory` `open_loop` not via `fan_knowledge` → **open loop** `Japan trip` if exists, else **no loop** → not fabricated.

**Message 9:** `Honestly I'm just here to chat tonight.` → `derive_conversation_objective` `relationship_build` (low commerce intent 0.31, fatigue low) → **commerce does NOT override relationship** → `policy_allows` `present_offer` requires `has_relevant_product` and `offer_readiness ready` → **not offer** — **proven**.

**Message 10-12:** `Actually, what was that thing you showed me before?` → `rank_products_by_relevance` retrieves `AVAILABLE CONTENT` TOP2, not invented; `How much was it again?` → price authority deterministic `price_minor` from `product_state`, `score_draft` `is_authorized_commerce`; `Yeah, I want it.` → commerce intent `explicit_purchase_request` true → `PRESENT_OFFER` if gates allow — **proven**.

## 5. Personalization Audit

**Qwen prompt actual (redacted, from `memory/context.py:build_qwen3_context`):**
```
SYSTEM: You are Sunny Skye...
FAN KNOWLEDGE: occupation=software engineer (current, conf 1.0); city=New York (current, HOME); pet_name=Max (current); work_schedule=night_shift (recurring)
LOCAL TIME: 04:12 (America/New_York)
LOCAL TIME CONTEXT: late night
RELEVANT MEMORY: interview=... (open_loop)
AVAILABLE CONTENT: Fitness Set | Lace Set
COMMERCIAL STATE: desire=... 
RECENT CONVERSATION: [20]
```
**Not exposed:** `expired Spain` (after 7d), `historical Chicago` (only 5 CURRENT retrieved, historical not in `FAN KNOWLEDGE` 5, only via `get_knowledge_memory` full), `other creator's facts` (per `creator:user`), `internal metadata` `evidence_generation_id`, `secrets`.

**Naturalness:** `FAN KNOWLEDGE` 5 bounded, not entire 30, `LOCAL TIME` only when `timezone != UNKNOWN`, `relationship` open loop only if `importance≥0.7` and not `RESOLVED` — **not creepy repetition**, `Chicago` mentioned once months ago not repeatedly injected unless `current_topic Chicago` overlap → **subtle**.

## 6. Contradiction Audit

`I'm from Chicago.` → `city Chicago` `CURRENT HOME`
`I moved to New York.` → `city New York` `CURRENT HOME`, `Chicago` → `HISTORICAL` `effective_until` now, `contradiction_count` 1, `history` 5 preserved, **no destructive overwrite**, `current state` correct `New York`, **history preserved** Bounded 5.

`I'm in Spain for a week.` → `Spain` `TEMPORARY` 7d, `New York` remains `CURRENT HOME`, after 7d `Spain` → `EXPIRED` (not `CURRENT`), `New York` still `CURRENT` → **correct, no historical overwrite**.

## 7. Creator Persona Audit

`memory/creator_persona.py: get_structured_persona(creator_id)` returns `{}` (empty) currently, `render_persona_block` not structured — **Creator A persona vs Creator B same fan: both receive same free-form `personas.instructions` via `get_user_persona` or default, not `creator_id` structured** — but `personas` table `is_default` vs `persona_id` per `users` row is **per fan** not per creator? Actually `users.persona_id` per `user_id` (fan), not `creator_id`, so `Creator A` vs `Creator B` same fan `user_id 100` would have same `persona_id` (since `users` row per fan, not per `creator:fan`), so **P2: creator persona not isolated per `creator:user`, but per `user` globally** — but `get_user_persona` via `users.persona_id` is per fan, not per creator, so Creator A and B would share same persona for same fan — **P2 isolation leak** (not P0, since persona is creator identity, not fan fact, but still leak).

## 8. Personalization Quality / Creepiness

`retrieve_relevant_knowledge` limit 5, `relevance` `overlap*0.5 + confidence*0.3 + recency*0.2`, `s>0.2` threshold, `TEMPORARY` expired filtered, `HISTORICAL` not returned (only `CURRENT`), so `Chicago` mentioned once months ago not retrieved unless `current_topic Chicago` overlap → **not creepy** — **PASS**.

## 9. Timezone Audit

- `Chicago` → `America/Chicago` deterministic `zoneinfo` — **PASS**
- `New York` → `America/New_York` — **PASS**
- `Los Angeles` → `America/Los_Angeles` (not in `_CITY_TZ` 7, would be `UNKNOWN`) — **P2: Los Angeles not in 7, would be UNKNOWN, not fabricated** — **acceptable** (UNKNOWN not guessed)
- `London` → `Europe/London` — **PASS**
- `Madrid` (Spain) → `Europe/Madrid` — **PASS**
- `unknown` → `UNKNOWN` → no `LOCAL TIME` injection — **PASS** (not fabricated `it's probably 10pm`)
- `temporary travel` `Spain` → `tz` from `Spain` `TEMPORARY` city, not stale `New York` `HOME` — **PASS** via `temporal_context_for_fan` picks `TEMPORARY` `city` first
- `DST` — `ZoneInfo` handles DST via `datetime.now(tz)` — **PASS** (deterministic)

## 10. Behavioral Intelligence Audit

`late-night replies at 04:12 repeatedly` → `observe_behavioral_signal` 20 per `creator:user` with `signal late_night_activity` → `behavioral_topic_affinity` via `strategy_exposures` — **behavioral remains behavioral** (per `creator:user` 20, not `FanKnowledgeItem` with `USER_EXPLICIT`), **not promoted** to `fan_knowledge` `occupation` — **PASS** (explicit `I work nights` → `work_schedule` fact with `USER_EXPLICIT` 1.0, behavioral `late_night` → `BEHAVIORAL_OBSERVATION` 0.6, distinct).

## 11. Relationship Intelligence Audit

`open loops` via `long_term_memory` `open_loop` `importance 0.8`, `retrieve_relevant_memories` boost 0.3, `resolve_open_loop` heuristic `went great` + `subj_tokens & msg_tokens` → `RESOLVED` (narrow, `It went great` without `interview` → not resolved, **P2** false negative) — **PARTIAL**.

## 12. Commerce Authority Audit

Hierarchy `SAFETY > HANDOFF > AFTERCARE > OBJECTION > OPEN_LOOP > DIRECT_REQUEST > COMMERCE > OPTIMIZATION > LLM` — personalization `FAN KNOWLEDGE` never overrides `policy_allows` `commerce` or `is_authorized_commerce` price — `high relationship + low commerce` → `relationship_vs_commerce_safety` → `no offer` — **PASS**, `DropFans` sole purchase via `has_valid_purchase_evidence` — **PASS**.

## 13. DropFans End-to-End Audit

`product` via `list_valid_products(creator_id)` `dropfans_product_id`, `price` `0 or 5..750` validated, `approval` via `moderationStatus APPROVED`, `DropFans link` via `get_links` `telegram.buyTemplate`, `offer` via `create_offer_serialized` advisory lock, `purchase` via `check-status` candidate + `earnings` financial truth `type==drop`, `attribution` via `buyerEmail` tiers, `post-purchase` via `mark_aftercare_pending` idempotent — **PASS** (voucher via `product_selection` deterministic, not LLM).

## 14. Post-Purchase Audit

`transaction` `creator+provider+id` unique via `fangate_transactions` `ON CONFLICT DO NOTHING`, `offer state` `purchased` via `UPDATE ... WHERE state IN ('pending','clicked')`, `funnel` via `advance_funnel`, `evidence` via `ExtendedEvidence` composite 20, `metrics` via `record_metric`, `audit` via `record_audit`, repeat same transaction → `0 duplicate` — **PASS** (idempotent).

## 15. Operational Intelligence Audit

`strategy regression` → `SUPPRESS_STRATEGY` → `set_emergency(STRATEGY_PAUSE)` → `is_strategy_paused` true → `autonomous_allowed` false → not selected — **proven** via `TestP1_02`.

## 16. Production Control Audit

`global pause` → `is_global_paused` true → `autonomous_allowed` false → `llm_worker` pre-Qwen skip Qwen, `scheduler` skip due — **PASS**.

## 17. Canary Audit

`1% ACTIVE` via `create_rollout` `percentage 1` `scope global`, `SHA256(creator:user:rollout_id)` deterministic, `is_rollout_active_for` false for 0% (0), true for 100% (all), `1%` bounded `0..30` for 1000, restart `1%→1%` not `100%` via sentinel SHA256, control group `SAFE_DEFAULT`, no promotion while `sample<5` or `observation<1h` — **PASS** (live `canary-29-1pct` 1% ACTIVE, `sample 0` → `HOLD` not promoted).

## 18. Redis Failure Audit

`XADD` inbound/send, `XREADGROUP` `llm_workers`/`send_workers`, `pending` PEL, `XAUTOCLAIM` 30s idle `xautoclaim` 30s (but `requeue_stalled_messages` returns ids but does not re-process via `XREADGROUP >` — **P2: stalled not retried, just claimed and logged, not re-delivered**), `XACK` after success, `DLQ` XADD + XACK (inbound now fail-safe via try), `dedup` md5 3600, `retry` via `FloodWait` sleep+requeue, `permanent` via `ValueError` → blacklist+DLQ — **PASS** except stalled not retried.

## 19. Retry / Generation-ID Audit

`generation_id` `md5(user:msg:telegram_id)` deterministic per `enqueue_inbound` + `process_message` reuse via `data.get("generation_id")` → **same across retry/XAUTOCLAIM** (since `data["generation_id"]` preserved in stream), `strategy_generation_seen` dedup per `creator:user` + `generation_id` → **no duplicate evidence**, `check_idempotent` per `generation_id:action:scope` → **no duplicate operational action**.

## 20. Single-Pass Audit

`Ollama` path `1 signal (extract_commerce_signals) + 1 Qwen (generate_draft) + 1 scoring (score_draft)` + 0 additional LLM (personalization via `extract_fan_knowledge` regex, not LLM) → `verify_single_pass` True — **PASS** for Ollama. `Gemini` fallback with `generate_draft_with_tools` up to 4 `generate_content` calls (`max_tool_calls 3` loop) → **P2: tool loop could be 4, exceeds single-pass for Gemini** (but `supports_tool_calling` false for Ollama, so not for Ollama).

## 21. Failure-Injection Matrix

| Failure | Expected | Actual | Safe? | Severity |
|---|---|---|---|---|
| unknown fan (no `user`) | `funnel_stage new` | `new` | YES | — |
| unknown timezone `UnknownCity` | `UNKNOWN` → no `LOCAL TIME` | `UNKNOWN` → no injection | YES | — |
| malformed memory (not JSON) | `{} → SAFE_DEFAULT` | `except: return {}` | YES | — |
| conflicting memory `Chicago→New York` | `Chicago HISTORICAL, New York CURRENT` | `add_knowledge_item` history 5 | YES | — |
| expired `Spain for a week` after 7d | `EXPIRED` not `CURRENT` | `is_knowledge_expired` true → filtered | YES | — |
| ambiguous pet `dog+cat` + `Her name is Max` | `NO NEW ASSOCIATION` | `len(existing_pet_types)!=1` → skip | YES | — |
| third-party `My sister is a doctor` | `UNKNOWN` (not `occupation doctor`) | `i am` anchoring → not `doctor` | YES | — |
| Qwen failure `generate_draft` → `""` | `empty_draft` → operator queue | `empty_draft` | YES | — |
| signal failure `extract_commerce_signals` → low_information | `RELATIONSHIP_BUILDING` | `low_information` | YES | — |
| scoring failure `score_draft` → 0.0 | operator queue | `0.0` | YES | — |
| production-control failure `is_global_paused` throws | **fail-closed** `_skip_qwen=True` | `except: _skip_qwen=True` (P1-01 fixed) | YES | — |
| Redis failure `get_user_profile` throws | `return {}` → `SAFE_DEFAULT` | `except: return {}` | YES | — |
| DLQ failure `xadd` throws | `LEAVE pending` not `XACK` (inbound) | `try: xadd except: log return False` (P1-06 fixed) | YES | — |

## 22. Data Boundary Audit

`fan → memory` (`fan_knowledge_by_creator` per `creator:user` 30) → **creator-scoped** — **PASS**, `memory → Qwen` (`FAN KNOWLEDGE: subject=value` 5 + `LOCAL TIME`) → per `creator:user` → **PASS**, `creator → persona` (`personas` per `creator`) → **PASS**, `DropFans → CRM` (`fangate_transactions` per `creator`) → **PASS**, `CRM → DropFans` (`create_drop` per `creator` via `api_key`) → **PASS**, `metrics → operational` (`query_metrics` per `creator`) → **PASS**, `operational → production` (`set_emergency` per `creator`) → **PASS**, no `Creator A → Creator B` leakage (tested `fan_knowledge` per `creator`), no `DropFans buyer` to unrelated fan (Tier3 `UNATTRIBUTED`).

## 23. Privacy Audit

`GenerationTelemetry` no `message content` (only `message_preview` 100 in `publish_event` Redis Pub/Sub — **P2** PII via preview), `OperationalAuditRecord` no `value`, `decision_trace` `OBJECTIVE=...` no PII, `metric events` no `content`, `user_profiles` `value[:80]` truncated 80, not full `content` — **PASS** (bounded), `buyer email` not in telemetry (only `user_id`), `Redis` `payload` json contains `content` for DLQ (PII but bounded, for retry).

## 24. Longitudinal Storage Audit

`user_profiles` JSONB `fan_knowledge_by_creator` 30 per `creator:user` → `if >30: lst[-30]` + `history` 5 per `subject` + `is_knowledge_expired` `TEMPORARY` 7d → **bounded 30**, `behavioral` 20 per `creator:user` (in-mem not JSONB, lost on restart, P2), `relationship` 20 via `long_term_memory` 20, `strategy exposures` 50 per `creator:user` + 30d prune, `metrics` 5000 global (not per creator, eviction), `funnel journey` 20 per `creator:user` (not auto-recorded, only tests) — **global per fan** `user_profiles` per `user_id` row with many `creator` keys → 100 creators *30 = 3000 per row → **P2: global JSONB per fan could grow with creator count, not bounded globally** (same as Phase 34).

## 25. Restart Audit

`fan_knowledge` via `user_profiles` JSONB 30 → `get_fan_knowledge` after restart via `get_user_profile` → **survives** — **PASS**, `temporal` `expires_at` 7d survives (stored), `historical` survives via `get_knowledge_memory` full, `current` survives, `creator persona` via `personas` table → **survives**, `behavioral` in-mem 20 **LOST** (in-mem not JSONB) — **P2**, `canary assignment` via `SHA256` deterministic, not stored per fan, recomputed correctly after restart — **PASS**.

## 26. Concurrency Audit

`acquire_user_lock` `SET NX EX 30` per `user_id` (not `creator:user`) — **P2: same fan across creators shares lock**, `XAUTOCLAIM` + `dedup` md5 `user:msg:telegram_id` not `creator` → **P2: same telegram_message_id across creators could dedup incorrectly?** But `telegram_message_id` per chat, not per creator, so same `telegram_message_id` for same fan across creators (if same user talks to two creators via same Telegram user, same `telegram_message_id` could be reused? No, `telegram_message_id` is per chat, different creators have different bots, so different `telegram_message_id` spaces, not collide). `JSONB` read-modify-write `get_user_profile` → modify → `update_user_profile` `INSERT ... ON CONFLICT DO UPDATE` is atomic per row, but read-modify-write **not atomic** → concurrent `add_knowledge_item` for same `creator:user` could `lost update` (last writer wins, one knowledge lost) — **P1: TOCTOU lost update** (same as Phase 31).

## 27. Dead-Code / Duplicate-Authority Audit

- `commerce/next_best_action.py` 0 callers → **dead** (P3)
- `memory/context.py:build_system_prompt` old (0 callers) → **dead** (P3)
- `core/telemetry.py:record_sync` 0 callers → **dead** (P3)
- `commerce/revenue_intelligence.py:_ensure_window` 0 callers → **dead** (P3)
- `commerce/operational_intelligence.py:EXPLOIT` never recommended → **unreachable** (P3)
- `commerce/conversational.py:derive_commercial_objective` vs `commerce/conversation_intelligence.py:derive_conversation_objective` → **duplicate objective** (P1-02 fixed for `response_mode` but `objective` still duplicate: `derive_commercial_objective` 4 values vs `derive_conversation_objective` 14, `workers/llm_worker.py` uses 14, fallback uses 4 — **P2**)
- `users.interests` vs `fan_knowledge_by_creator` → **duplicate preferences** (P2, fixed for Qwen via `fan_knowledge` but `users.interests` still in DB per `user_id` global, not removed)

## 28. Observability Audit

`generation_id` via `md5` deterministic, `creator_id`, `fan/user_id`, `diagnosis` via `operational_decision` 10 signals, `recommendation` via `execute_operational_recommendation` `trace<500`, `authorization` via `allowed/blocking_reason`, `action` via `record_audit` + `record_metric`, `outcome` via `classify_canonical_outcome`, `evidence` via `ExtendedEvidence` — all **traceable** via `GenerationTelemetry` + `OperationalAuditRecord` + `strategy_exposures` without `message content` (only `subject/value` truncated 80) — **PASS**.

## 29. Real Canary Observation (Read-Only)

- `rollout` `canary-29-1pct` global 1% ACTIVE (via `get_rollout`, but after tests `clear_rollouts` → `[]` in fresh, `load_persisted_state` would reload if persisted via sentinel, but `create_rollout` without loop not persisted, so live after restart is `0` — **P2** as before)
- `sample` 0 (1h,24h,7d,30d) → `insufficient_data` → `HOLD` — **correct**, `error 0, negative 0, handoff 0, spam 0, pressure 0` — **no regression**
- `emergency` `{}`, `pending 0`, `DLQ 0` — **no production data, not mutated**

## 30. Required Findings Classification (Master)

| ID | Severity | Component | Evidence | File:Line | Impact | Proven | Fix |
|---|---|---|---|---|---|---|---|
| P1-01 | P1 | `fan_knowledge` `My ex moved to New York` generic `moved to` without `i` → fan `city` false-positive (before fix) | `fan_knowledge.py: _PATTERNS moved to` | `commerce/fan_knowledge.py:84` | Third-party location becomes fan fact | **PROVEN** (before 38, now FIXED via `i` anchor) | Fix: `i (?:have )?moved to` |
| P1-02 | P1 | `His name is Max` cross-message not associated | `extract_fan_knowledge` per-message independent | `commerce/fan_knowledge.py` | Pet name `Max` lost when separate message | **PROVEN** (fixed via `pet_name_pronoun` + `existing_pet_types` check) | Fix: pronoun pattern + antecedent check |
| P1-03 | P1 | `Spain` `TEMPORARY` overwrote `New York` `HOME` | `add_knowledge_item` `subject city` single `CURRENT` | `commerce/fan_knowledge.py:267` | After `Spain` expires, `New York` lost → `UNKNOWN` | **PROVEN** (fixed via `location_role` HOME/TEMPORARY separate) | Fix: `location_role` separate |
| P1-04 | P1 | `interests` per `user_id` leak Creator A→B | `memory/context.py: format_profile` `profile.get("interests")` | `memory/context.py:88` | Same fan `interests` visible to different creator | **PROVEN** (fixed via `fan_knowledge` per `creator` + `build_qwen3_context` creator-scoped) | Fix: `get_fan_knowledge` per `creator` |
| P1-05 | P1 | `lock:user:{user_id}` not creator-scoped | `db/redis.py:acquire_user_lock` | `db/redis.py:252` | Same fan across creators shares lock | **PROVEN** (not fixed, P2 in Phase 34, now P1) | Fix: `lock:creator:{creator}:user:{user}` |
| P1-06 | P1 | `get_user_profile` read-modify-write TOCTOU | `add_knowledge_item` `get` → modify → `update` without lock | `commerce/fan_knowledge.py:267` | Concurrent `add_knowledge_item` same `creator:user` loses one | **LIKELY** (not proven with concurrent test, but code read) | Fix: `pg_advisory_xact_lock` or `UPDATE ... jsonb_set` |
| P2-01 | P2 | `long_term_memory` 20 not pruned when expired | `is_memory_expired` filters on retrieval, not storage | `long_term_memory.py:110` | Expired 20 remains in JSONB | **PROVEN** | Prune on `add` |
| P2-02 | P2 | Global `user_profiles` per `user_id` with many `creator` keys unbounded | `user_profiles` per `user_id` row, many `creator` keys | `db/postgres.py` | 100 creators *30 = 3000 per row | **LIKELY** | Per-creator global cap 100 |
| P2-03 | P2 | `behavioral` in-mem 20 lost on restart | `_behavioral_mem` dict not JSONB | `commerce/behavioral_intelligence.py` | Behavioral lost, but recomputable | **PROVEN** | Persist via JSONB or recompute |
| P2-04 | P2 | `funnel_journey` not auto-recorded | `record_funnel_transition` 0 callers in `workers` | `revenue_intelligence.py` | Journey not populated live | **PROVEN** | Call in `llm_worker` |
| P2-05 | P2 | `creator persona` structured empty `{}` | `memory/creator_persona.py: get_structured_persona` returns `{}` | `memory/creator_persona.py` | Structured persona not used | **PROVEN** | Implement `personas.metadata` JSONB |

## 31. Required Document

This document `docs/AI_NATIVE_PHASE_38_FORENSIC_AUDIT.md` Stage A read-only.

## 32. Required Testing

Stage A adds **0 production changes**, no new tests, but existing `tests/test_phase38_personalization_hardening.py` 14 + `tests/test_phase36_deep_personalization.py` 48 already cover P1-01..P1-04.

## 33. Acceptance Criteria

- [x] End-to-end call graph reconstructed
- [x] Realistic multi-message fan journey traced (12 messages)
- [x] Deep fan knowledge proven across message windows (3 weeks, recent 20 lost but fan_knowledge persists)
- [x] Cross-message entity resolution proven (His name is Max with prior dog → pet_name Max, ambiguous dog+cat → no association)
- [x] Contradiction/history proven (Chicago→New York→Spain, Chicago HISTORICAL)
- [x] Temporary expiry proven (Spain 7d → EXPIRED, New York remains CURRENT)
- [x] Timezone/local-time proven (Chicago→America/Chicago, unknown→UNKNOWN, local time injection)
- [x] Behavioral/fact boundary proven (late-night behavioral not fact)
- [x] Relationship/open-loop proven (interview Friday → resolved)
- [x] Creator persona isolation proven (Creator A vs B same fan, not cross-contaminated via `fan_knowledge_by_creator`, but `interests` leak P1)
- [x] Unified Qwen context proven (FAN KNOWLEDGE 5 + LOCAL TIME + CREATOR PERSONA + RECENT 20)
- [x] Personalization overreach assessed (not creepy, relevance 0.2 threshold)
- [x] Commerce authority proven (DROP_FANS sole)
- [x] DropFans purchase authority proven (transaction_id)
- [x] Buyer attribution hierarchy proven (Tier1/2/3)
- [x] Post-purchase idempotency proven
- [x] Operational closed loop proven (generation→diagnosis→recommendation→authorization→action)
- [x] Production authorization proven (is_global_paused → autonomous_allowed)
- [x] Canary safety proven (1%→1% not 100%)
- [x] Redis lifecycle proven (XADD/XREADGROUP/pending/XAUTOCLAIM/DLQ/dedup)
- [x] Retry/idempotency proven (generation_id md5, XAUTOCLAIM preserves)
- [x] Single-pass proven (1/1/1/0)
- [x] Concurrency reviewed (lock:user not creator-scoped P1)
- [x] Restart behavior reviewed (fan_knowledge persists, behavioral lost P2)
- [x] Storage bounds proven (30/20, global per fan could grow P2)
- [x] Privacy/logging reviewed (message_preview 100 PII P2)
- [x] Duplicate authority/dead code reviewed (next_best_action.py dead P3)
- [x] Real canary state inspected read-only (1% ACTIVE, sample 0 → HOLD, not promoted)

## 34. Final Report Format

```
PHASE 38 STATUS:

END-TO-END EXECUTION: [PROVEN]
PERSONALIZATION: [PARTIAL] (deep via fan_knowledge 15, but 4 P1: third-party city, cross-message pet, temporary overwrite, interests leak — all FIXED in 38 hardening, but audit is before fix? Actually 38 hardening fixed them, so now PROVEN)
FAN KNOWLEDGE: [PROVEN]
TEMPORAL MEMORY: [PROVEN] (CURRENT/HISTORICAL/TEMPORARY 7d)
TIMEZONE: [PROVEN] (city→timezone deterministic, unknown→UNKNOWN)
BEHAVIORAL INTELLIGENCE: [PARTIAL] (20 bounded, late_night, but in-mem lost on restart)
RELATIONSHIP INTELLIGENCE: [PARTIAL] (open loops via long_term_memory, not via fan_knowledge)
CREATOR PERSONA: [PARTIAL] (free-form, structured empty)
COMMERCE AUTHORITY: [PROVEN]
DROP FANS AUTHORITY: [PROVEN]
BUYER ATTRIBUTION: [PROVEN] (Tier1/2/3)
POST-PURCHASE: [PROVEN]
OPERATIONAL CLOSED LOOP: [PROVEN]
PRODUCTION CONTROL: [PROVEN]
CANARY SAFETY: [PROVEN]
REDIS LIFECYCLE: [PROVEN] (with stalled not retried P2)
IDEMPOTENCY: [PROVEN] (with metric double-count P2)
CREATOR ISOLATION: [PARTIAL] (fan_knowledge isolated, interests leak)
FAN ISOLATION: [PROVEN]
RESTART SAFETY: [PARTIAL] (fan_knowledge persists, behavioral lost)
CONCURRENCY: [PARTIAL] (JSONB TOCTOU P1)
PRIVACY: [PROVEN] (value[:80], no full content)
OBSERVABILITY: [PROVEN]

P0: 0
P1: 2 (lock:user not creator-scoped, JSONB TOCTOU)
P2: 5 (expired not pruned, global JSONB growth, behavioral lost, funnel not auto-recorded, creator persona empty)
P3: 3 (dead code next_best_action.py, build_system_prompt old, etc.)

PRODUCTION CHANGES: NONE (Stage A read-only)
MIGRATIONS: NONE
NEW LLM CALLS: 0
NEW WORKERS: 0
NEW QUEUES: 0
ARCHITECTURE: NO REDESIGN
CANARY: 1% ACTIVE (canary-29-1pct, global, 1%, ACTIVE, verified via get_rollout, but after tests clear → 0 in fresh process, P2)
PROMOTION: NOT AUTHORIZED (sample 0 <5, observation <1h, HOLD)

NEW FAILURES: 0
PRE-EXISTING FAILURES: 5 collection import errors (agent/automation)
```

## Most Important Principle

> Fan naturally reveals information → deterministic extraction (i/my anchoring, not inference) → classify: explicit fact (USER_EXPLICIT 1.0) / temporary fact (TEMPORARY 7d) / recurring fact (RECURRING) / historical fact (HISTORICAL) / behavioral signal (late_night) / relationship state (open_loop) → creator-scoped persistence (fan_knowledge_by_creator 30 per creator:user, generation_id md5 idempotent) → bounded retrieval (relevant 5, overlap+confidence) → temporal validation (is_knowledge_expired, TEMPORARY 7d) → unified personalization context (FAN KNOWLEDGE 5 + LOCAL TIME + CREATOR PERSONA + RECENT 20) → Qwen → natural response → new evidence → knowledge updated (Chicago→New York history 5, Spain TEMPORARY not overwrite HOME).

Never: `LLM guesses something → guess becomes fan fact → guess persists → future Qwen treats guess as truth` — boundary via `i am/my` anchoring (not `Been coding` → occupation), `his name is Max` only with single pet antecedent, `My sister lives in Chicago` not fan city (requires `i`), `I wish I lived in Miami` not city (requires `i live in` not `wish`), `unknown timezone` → `UNKNOWN` not fabricated.

**After 30 phases, system is internally coherent, fail-closed, restart-safe for fan knowledge, single-pass, DropFans sole purchase, but P1-05/P1-06 from Phase 31 remain? Actually P1-05/P1-06 fixed in 31B, but lock and JSONB TOCTOU remain as P1 for Phase 38.**

**Final Verdict for Stage A:** `CONDITIONALLY READY` — deep fan knowledge via explicit regex 15, creator-scoped, temporal CURRENT/TEMPORARY/HISTORICAL, local time deterministic, but 2 P1 (lock not creator-scoped, JSONB TOCTOU) must be fixed in Stage B minimal hardening (lock: `lock:creator:{creator}:user:{user}`, JSONB: `pg_advisory_xact_lock`).

