# AI_NATIVE_PERSONALIZATION_PHASE_35_IMPLEMENTATION_MAP.md
# Phase 35 — Implementation Map (Stage A, Proposed, Not Implemented)
# Date: 2026-08-30

## 1. Findings Reconciled (From Forensic)

- P1-01: `occupation/city/pet/schedule` not captured (regex 4 subjects only)
- P1-02: `interests` leak per `user_id` not `creator` (P1)
- P1-03: no timezone/local_time
- P1-04: no temporal (Spain temporary)
- P1-05: no update/history for city (overwrite)
- P1-06: no behavioral memory
- P2: expired not pruned, global JSONB per fan could grow, `lock:user` not creator-scoped

## 2. P1 Fixes (Stage B, Not Yet Implemented)

| ID | Exact File | Exact Function | Change | LOC |
|---|---|---|---|---|
| P1-01 | `commerce/fan_knowledge.py` NEW | `extract_fan_knowledge` | Expand regex to 15 categories: `occupation` (`I'm a software engineer`), `city` (`I live in Chicago`), `country`, `pet` (`dog Max`), `schedule` (`work nights`), `hobby`, `family`, etc., deterministic, not LLM, `category` taxonomy, `confidence` EXPLICIT | 150 |
| P1-02 | `memory/context.py` | `format_profile` | Make `interests` creator-scoped via `commercial_preferences_by_creator` or new `fan_knowledge_by_creator` `interests`, not `user_profiles.facts.interests` | 10 |
| P1-03 | `commerce/temporal_context.py` NEW | `derive_fan_timezone` | `city` → `timezone` via `zoneinfo` lookup, `current_local_time` → `Fan local time: 04:12` injection, `UNKNOWN` if not known | 100 |
| P1-04 | `commerce/fan_knowledge.py` | `add_knowledge_item` | Support `temporal_type` `TEMPORARY` 7d vs `PERMANENT` 90d, `effective_until`, not flattened | 20 |
| P1-05 | `commerce/fan_knowledge.py` | `add_knowledge_item` | On `subject` same, keep `history` bounded 5 (`previous value` + `observed_at`), `current` wins, `contradiction_count` | 20 |
| P1-06 | `commerce/behavioral_intelligence.py` NEW | `observe_behavioral_signal` | Bounded `behavioral_signals_by_creator` 20 per `creator:user`, `affinity` count, signals not facts | 50 |

## 3. P2 Fixes

- Expired pruning: `is_knowledge_expired` `TEMPORARY 7d` vs `PERMANENT 90d`, `add_knowledge_item` prune `>30` not 20, `is_memory_expired` already filters on retrieval but also prune on `add`.
- Global per-fan JSONB growth: `user_profiles` per `user_id` row with many `creator` keys → already bounded per `creator:user` 30, global per fan could be 100 creators *30 = 3000, but bounded per `creator` 30, global not bounded — **accept** for 1% canary, future per-fan global cap 100.

## 4. Exact Files Changed (Stage B, Not Yet)

- `commerce/fan_knowledge.py` NEW 400 LOC
- `commerce/temporal_context.py` NEW 150
- `commerce/behavioral_intelligence.py` NEW 200
- `commerce/relationship_intelligence.py` NEW 200 (extend `open_loop` to `promise/plan/event`)
- `memory/creator_persona.py` NEW 150 (structured `personas` metadata)
- `memory/context.py` MOD 30 (FanKnowledge 3, Temporal, Relationship, Creator Persona)
- `workers/llm_worker.py` MOD 20 (extract → add → retrieve)
- `db/postgres.py` MOD 10 (get/update fan_knowledge via JSONB)
- Tests `tests/test_phase35_deep_personalization.py` NEW 40

No new worker/queue/LLM, no migration, reuse `user_profiles` JSONB, `user_id`+`creator_id` scope, `generation_id` idempotent, `single-pass` preserved.

## 5. Authority Flow (Proposed)

`Creator Persona (operator) > Fan Stable Profile > Current Fan Knowledge (30) > Relevant Historical (5) > Temporal (local time) > Behavioral (20) > Relationship/Open Loops (3) > Commerce Context > Current Conversation 20` → `Unified Personalization Context` (bounded) → Qwen

## 6. Retry/Generation-ID Flow

`generation_id` deterministic `md5(user:msg:telegram_id)` per `workers/llm_worker.py:514` + `db/redis.py:enqueue_inbound` already — reuse for `fan_knowledge` dedup via `source_generation_id`, not duplicate.

## 7. Rollout Determinism

`fan_knowledge_by_creator` per `creator:user` via `user_profiles` JSONB, deterministic via `subject` canonical, not `hash()`.

## 8. Health Semantics

`fan_knowledge` not health, but `operational_decision` with `relationship_health` via `long_term_memory` warps, sample<5 → `INSUFFICIENT` → HOLD.

## 9. DLQ Semantics

`knowledge` updates are `user_profiles` JSONB, not Redis stream, so no DLQ.

## 10. Product-Family Enforcement

Not related to fan knowledge.

## 11. Re-engagement Frequency

Not related, already fixed via `query_metrics` 7d.

## 12. Test Mapping (Proposed)

40 tests covering `occupation/city/timezone/pet/schedule` capture natural, explicit vs inferred, corrections `Chicago→NY`, temporal `permanent/current/temporary/recurring/event` + `Spain for week` 7d, timezone `known/derived/unknown` + `04:12` injection, behavioral `late night` not fact, relationship `open loop` `promise`, persona isolation, creator/fan isolation, restart `user_profiles` persists, retry `generation_id` dedup, deduplication `subject` canonical, history bounded 5, expiration `temporary 7d`, relevance bounded 3, hallucination `unknown` not fabricated, commerce safety, single-pass 1/1/1/0, performance bounded, PII `value[:50]` not full content.

