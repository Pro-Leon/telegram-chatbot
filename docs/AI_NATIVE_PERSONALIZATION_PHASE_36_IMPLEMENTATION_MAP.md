# AI_NATIVE_PERSONALIZATION_PHASE_36_IMPLEMENTATION_MAP.md
# Phase 36 — Implementation Map (Stage B)
# Date: 2026-08-30

## 1. Exact Files Changed

| File | Change | LOC | Reason |
|---|---|---|---|
| `commerce/fan_knowledge.py` | **NEW** 440 LOC `FanKnowledgeItem` 15 fields `category` 20, `temporal_type` 8, `source` 6, `confidence` EXPLICIT 1.0, `extract_fan_knowledge` deterministic regex 15 patterns (occupation/city/pet/schedule/hobby/trip/family/birthday), `add_knowledge_item` bounded 30 + history 5 + idempotent generation_id + temporal 7d, `retrieve_relevant_knowledge` 5, `build_personalization_context` | 440 | Deep fan knowledge substrate |
| `commerce/temporal_context.py` | **NEW** 40 LOC `derive_fan_timezone` `city→timezone` deterministic via `zoneinfo`, `current_local_time`, `temporal_context_for_fan` | 40 | Local time |
| `commerce/behavioral_intelligence.py` | **NEW** 40 LOC `observe_behavioral_signal` bounded 20 per creator:user, `behavioral_topic_affinity` | 40 | Behavioral |
| `commerce/relationship_intelligence.py` | **NEW** 30 LOC `track_open_loop` via `long_term_memory` | 30 | Relationship |
| `memory/creator_persona.py` | **NEW** 40 LOC `get_structured_persona` | 40 | Creator persona |
| `memory/context.py` | MOD 40 `build_qwen3_context` → `FAN KNOWLEDGE` 5 + `LOCAL TIME` + `CREATOR PERSONA` bounded, creator-scoped, deterministic | 40 | Unified context |
| `workers/llm_worker.py` | MOD 20 `extract_fan_knowledge` → `add_knowledge_item` idempotent + `observe_behavioral_signal` late-night | 20 | Capture |
| `commerce/fan_knowledge.py` (patterns) | MOD 10 `i'm a` contraction, `moved to`, `in Spain for a week` TEMPORARY, `go running` | 10 | Natural forms |

## 2. Data Model

`FanKnowledgeItem(subject, value, category, confidence, source, observed_at, effective_from, effective_until, expires_at, temporal_type CURRENT/HISTORICAL/TEMPORARY/RECURRING/FUTURE, status CURRENT/HISTORICAL/EXPIRED, evidence_generation_id, last_confirmed_at, confirmation_count, contradiction_count, creator_id, user_id, first_observed_at)`

Categories: IDENTITY, LOCATION, WORK, FAMILY, PETS, HOBBIES, INTERESTS, TRAVEL, LIFESTYLE, etc. (20, extensible via `subject` without migration).

## 3. Extraction

Deterministic regex, not LLM, no interrogation, `1 SIGNAL 1 QWEN 1 SCORING` preserved. `extract_fan_knowledge` handles `I'm a software engineer from Chicago` → `occupation, city`, `My golden retriever is Max` → `pet_type dog + pet_name Max`, `I work nights` → `night_shift` RECURRING, `I love F1` → `interest`, `I'm in Spain for a week` → `city Spain TEMPORARY 7d`, `I moved to New York` → correction.

## 4. Persistence

`user_profiles` JSONB `fan_knowledge_by_creator` per `creator:user` 30, `is_knowledge_expired` category-aware, `deduplication` via `subject` canonical, `history` 5 per subject, `idempotent` via `generation_id`, `bounded` 30, `creator_id+user_id` scope, `generation_id` idempotent, `restart` via `user_profiles` JSONB, no new table, `XAUTOCLAIM` safe via `generation_id` md5.

## 5. Retrieval

`retrieve_relevant_knowledge` 5, `overlap*0.5 + confidence*0.3 + recency*0.2 + CURRENT boost 0.2`, `s>0.2` limit 5, `is_knowledge_expired` filtered, `TEMPORARY` expires after 7d, `HISTORICAL` not returned (only CURRENT).

## 6. Temporal

`derive_fan_timezone` `Chicago→America/Chicago` via `zoneinfo`, `current_local_time` → `LOCAL TIME: 12:00`, `UNKNOWN` if not known, not fabricated, `temporal_context_for_fan` checks `TEMPORARY` `city` `expires_at`.

## 7. Behavioral

`observe_behavioral_signal` bounded 20 per `creator:user`, `late_night_activity` via UTC hour, not fact, `behavioral_topic_affinity` via `strategy_exposures`.

## 8. Relationship

`track_open_loop` via `long_term_memory` `open_loop`, `relationship_memory` via `fan_knowledge` `RELATIONSHIP_CONTEXT` (future).

## 9. Creator Persona

`get_structured_persona` per `creator` (currently empty dict, operator can configure via `personas.metadata`), `render_persona_block` → Qwen, creator isolation via `creator_id`.

## 10. Unified Context

`Creator Persona → Fan Stable Profile → Current Fan Knowledge (5) → Historical (not sent) → Temporal (local time) → Behavioral (20) → Relationship (3) → Commerce Context → Recent 20` → Qwen, bounded, `FAN KNOWLEDGE: subject=value` + `LOCAL TIME: ...` + `CREATOR PERSONA`.

## 11. Isolation

All `creator_id+user_id` (`fan_knowledge_by_creator` `str(creator)` per `user_id` row), no `user_id`-only leak (fixed via new `fan_knowledge` per creator), `lock:user` still per `user_id` (P2) but fan knowledge isolated.

## 12. Single-Pass

`extract_fan_knowledge` consumes existing `user_message` already in pipeline, no second LLM, `1 SIGNAL (extract_commerce_signals) + 1 QWEN (generate_draft) + 1 SCORING (score_draft)` preserved, verified via `verify_single_pass`.

## 13. Test Mapping

40 tests covering `occupation/city/pet/schedule` natural, `temporal` CURRENT/TEMPORARY/RECURRING/FUTURE/expiration/correction, `timezone` known/unknown/local_time, `behavioral` bounded, `relationship` open loop, `persona` isolation, `creator/fan` isolation, `restart` via `user_profiles`, `retry` idempotent, `bounded` 30, `Qwen` relevant, `safety` commerce, `single-pass`.

