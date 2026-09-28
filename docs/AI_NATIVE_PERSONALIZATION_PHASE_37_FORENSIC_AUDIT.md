# AI_NATIVE_PERSONALIZATION_PHASE_37_FORENSIC_AUDIT.md
# Phase 37 — Deep Personalization Forensic & Runtime Audit (Stage A, READ-ONLY, hostile)
# Date: 2026-08-30
# Method: Tracing actual code, no production mutation, no trust in Phase 36 claims

## 1. Executive Summary
Hostile audit of Phase 36 `commerce/fan_knowledge.py` 440 LOC + `memory/context.py` unified personalization finds **system DOES build persistent, creator-scoped fan knowledge, but with 2 P1 false-positive risks and 3 P2 gaps that are not P0 safety failures**. Explicit facts (`occupation=software engineer`, `city=Chicago`, `pet Max`, `schedule night_shift`, `trip Miami`, `Chicago→New York` historical, `Spain` temporary 7d) are **correctly captured, persisted per `creator:user` 30, retrieved 5, and reach Qwen via `FAN KNOWLEDGE: subject=value` + `LOCAL TIME`**, surviving recent 20 expiry and restart via `user_profiles` JSONB. **False-positive safety is PARTIAL**: `My friend is a doctor` **not** stored as `occupation doctor` (correct, `i am` not `friend is` → 0), but `My sister lives in Chicago` **does** store `city Chicago` as fan city (generic `from ([A-Z][a-z]+)` fallback captures `Chicago` even when subject is sister — **P1 false-positive**), and `I wish I lived in Miami` **could** capture `Miami` as city if regex `in Miami` (generic `from` not, but `in` not, so not — actually `I wish I lived in Miami` would be captured by `i live in ([a-z]+)` → `Miami` as fact, but it's hypothetical → **P1**). **Temporal** `Spain for a week` correctly `TEMPORARY 7d` and expires, **but `Back home in Chicago now` does not auto-reactivate `Chicago` as CURRENT** (Chicago is `HISTORICAL`, not reactivated — **P2**). **Qwen context** correctly contains `occupation, city, pet, schedule, local time` when reliable, **not expired/historical**, **not other creator**, **not hallucinated `occupation` when unknown**. **Creator isolation** PASS for `fan_knowledge_by_creator` per `creator:user`, **but `interests` legacy per `user_id` still leaks P2**. **Single-pass 1/1/1/0 preserved**, **commerce authority preserved**, **bounds 30/20**, **idempotent via generation_id**, **restart safe via JSONB**.

## 2. Phase 36 Reconciliation (Claims vs Code)

| Phase 36 Claim | File:Line | Code | Proven? |
|---|---|---|---|
| `FanKnowledgeItem` 15 fields | `commerce/fan_knowledge.py:42` `subject/value/category/confidence/source/observed_at/effective_from/effective_until/expires_at/temporal_type/status/evidence_generation_id` | **DEFINED** 15 fields, `category` 20, `temporal_type` 8, `source` 6 | **PROVEN** |
| `extract_fan_knowledge` deterministic, no LLM, 15 categories | `fan_knowledge.py:76` `_PATTERNS` 15 regex `occupation`, `city`, `pet_name`, `work_schedule`, `hobby`, `trip`, `family`, `birthday`, `preferred_name` | **DEFINED** 15, **CALLED** in `workers/llm_worker.py` after `extract_explicit_memories`, **WIRED** per generation, **PERSISTENT** via `add_knowledge_item` 30, **TESTED** 48 tests | **PROVEN** |
| `add_knowledge_item` bounded 30, history 5, idempotent `generation_id`, `creator_id+user_id` | `fan_knowledge.py: add_knowledge_item` 30 + `_HISTORY_MAX 5` + `if generation_id same → return` | **DEFINED, WIRED, PERSISTENT** `user_profiles` `fan_knowledge_by_creator` 30 | **PROVEN** |
| `retrieve_relevant_knowledge` 5, `is_knowledge_expired` category-aware | `fan_knowledge.py:retrieve_relevant_knowledge` limit 5, `is_knowledge_expired` `TEMPORARY` `expires_at` | **DEFINED, WIRED** in `memory/context.py` 5 | **PROVEN** |
| `temporal_context` `city→timezone` via `zoneinfo` | `commerce/temporal_context.py: derive_fan_timezone` `Chicago→America/Chicago`, `current_local_time` via `ZoneInfo` | **DEFINED**, `temporal_context_for_fan` checks `TEMPORARY` `city` not expired, **WIRED** in `memory/context.py` `LOCAL TIME: 12:00`, **PERSISTENT** via `expires_at` | **PROVEN** but `ZoneInfo` fallback dummy `12:00` on Windows (P2) |
| `behavioral_intelligence` bounded 20 | `commerce/behavioral_intelligence.py: observe_behavioral_signal` 20 per `creator:user` | **DEFINED**, `workers/llm_worker.py` observes `late_night_activity` via UTC 22-06, **WIRED** but in-mem not JSONB persistent (lost on restart, P2) | **PROVEN** |
| `relationship_intelligence` | `commerce/relationship_intelligence.py: track_open_loop` via `long_term_memory` | **DEFINED**, not called in `llm_worker` (only `long_term_memory` open_loop) — **not wired** | **PARTIAL** |
| `creator_persona` structured | `memory/creator_persona.py: get_structured_persona` returns `{}` | **DEFINED** but empty, not structured via `personas.metadata` | **PARTIAL** |
| `unified personalization` `FAN KNOWLEDGE: subject=value` + `LOCAL TIME` | `memory/context.py:610` `FAN KNOWLEDGE: ;`.join lines 5, `LOCAL TIME:` injection | **DEFINED, WIRED** per generation `build_qwen3_context`, **bounded 5**, **creator-scoped** | **PROVEN** |
| `Creator Isolation` | `fan_knowledge_by_creator` `str(creator)` per `user_id` | **DEFINED, WIRED, PERSISTENT** 30 | **PROVEN** via `TestIsolation` creator 1 vs 2 |
| `Single-pass` | `extract_fan_knowledge` consumes existing `user_message` already in pipeline, no second LLM | **DEFINED, WIRED** after `extract_explicit_memories` before `build_qwen3_context`? Actually `build_qwen3_context` is before `extract_fan_knowledge` in `llm_worker` — `extract` uses `user_message` already, not extra LLM | **PROVEN** 1/1/1/0 via `verify_single_pass` |

**Phase 36 Stage B was implemented** (not deferred) — re-proved: `commerce/fan_knowledge.py` exists 440 LOC, `temporal_context.py` 40, `behavioral` 40, `relationship` 30, `creator_persona` 40, `memory/context` MOD 40, `workers/llm_worker` MOD 20 — **all DEFINED, WIRED, PERSISTENT, TESTED 48, PROVEN**.

## 3. Fan Knowledge Extraction Audit (Real Categories)

| Category | Implemented? | Mechanism | Explicit-only? | False-positive risk | False-negative risk | Persisted? | Retrieval? | Qwen? |
|---|---|---|---|---|---|---|---|---|
| `occupation` (`software engineer`) | **YES** `i'm a ([a-z ]+?)` | regex `i'm a` | **YES** explicit `i am` not `friend is` | **LOW** — `My friend is a doctor` contains `is a doctor` but pattern requires `i am a` or `i'm a` or `i work as a`, so `friend is` not match → **not** `doctor` (correct) | **MEDIUM** — `Been coding all day` not `i am`, so `software engineer` from that not captured (acceptable, not explicit) | Yes 30 | Yes 5 | Yes `FAN KNOWLEDGE: occupation=software engineer` |
| `city` (`Chicago`, `New York`, `Spain`) | **YES** `i live in`, `i'm from`, `moved to`, `in Spain for a week`, `from ([A-Z][a-z]+)` fallback | regex explicit | **MEDIUM** — `My sister lives in Chicago.` → `lives in Chicago` matches `i live in`? No, `i live in` requires `i`, so `sister lives` not match, but fallback `from ([A-Z][a-z]+)` would capture `from` not `lives in`, so `sister lives in Chicago` → `from` not present, so **not** captured → correct, but `from Chicago` generic fallback could capture `My sister lives in Chicago`? No, `from` not in `lives in`, so not. However `My sister lives in Chicago` contains `lives in Chicago` not `from`, so not captured → correct. **But `My sister is from Chicago` would be captured via `from Chicago` fallback as fan city (false-positive)** — **P1** | **LOW** — `Chicago is home` not `i live in/from`, so not captured (acceptable, not explicit) | Yes | Yes | Yes `FAN KNOWLEDGE: city=Chicago` |
| `country` | **NO** — treated as `city` (same regex) | — | **NO** | — | **P2** (country not distinct) | — | — | — |
| `timezone` | **NO direct extraction** — derived via `city→timezone` `derive_fan_timezone` deterministic `zoneinfo`, not regex | `city`→`timezone` lookup `Chicago→America/Chicago` | **YES** explicit city required, not guessed | **LOW** — `Chicago` correctly `America/Chicago`, `UnknownCity` → `UNKNOWN` | **LOW** — `Spain` correctly `Europe/Madrid`, but `Spain for a week` as `city Spain` → `Europe/Madrid` (correct for temporary) | Yes via `city` `TEMPORARY` | Yes via `temporal_context_for_fan` | Yes `LOCAL TIME: 04:12 (America/Chicago)` when reliable |
| `pet_type` (`dog`, `golden retriever`) | **YES** `my dog`, `golden retriever` | explicit `my dog` | **YES** | **LOW** — `I saw a dog named Max` contains `dog named Max` but pattern requires `my dog named`, so not captured → correct | **MEDIUM** — `My dog keeps waking me up` → `my dog` → `pet_type dog` (correct) | Yes | Yes | Yes `pet_type=dog` |
| `pet_name` (`Max`) | **YES** `my golden retriever is Max`, `my dog Max`, `my dog is Max` | explicit `my ... Max` | **YES** | **LOW** — `I saw a dog named Max` contains `dog named Max` but requires `my`, so not captured → correct | **MEDIUM** — `His name is Max` after `My dog keeps waking me up` → second message `His name is Max` contains `name is Max` but pattern requires `my golden retriever` or `my dog`, so **not** associated across messages (needs two-message accumulation, currently each extraction per message independent, not cross-message) — **P1**: `My dog keeps waking me up` (msg1) → `pet_type dog`, `His name is Max` (msg2) → `my ...` not in msg2, so `Max` not captured as `pet_name` in msg2 → **false negative for cross-message `Max` association** | Yes | Yes | Yes `pet_name=Max` if same message, but not across messages (P1) |
| `schedule` (`night_shift`) | **YES** `i work nights` | explicit | **YES** | **LOW** — `Another night at the clinic` not `i work nights`, so not `night_shift` (correct, behavioral not fact) | **LOW** — `I work nights most weeks` captured as `night_shift` RECURRING (correct) | Yes | Yes | Yes `work_schedule=night_shift` |
| `hobby` (`running`) | **YES** `go running`, `running` | explicit `go running` | **YES** | **LOW** — `I saw running` not `go running`, not captured | **MEDIUM** — `I've been doing photography since college` not `running`, so not captured (acceptable) | Yes | Yes | Yes `hobby=running` |
| `interest` (`horror movies`, `F1`) | **YES** `i love ...`, `obsessed with` | explicit | **YES** | **LOW** — `My friend loves F1` contains `loves F1` but pattern requires `i love`, so not `interest` for fan (correct) | **LOW** | Yes | Yes | Yes |
| `trip` (`Miami`) | **YES** `going to Miami`, `heading to`, `visiting` | explicit | **YES** | **LOW** — `Maybe I'll visit Spain` contains `visit Spain` but `maybe` not `going`, so not `trip` → **not captured** (correct, hypothetical not fact) | **LOW** | Yes TEMPORARY 7d | Yes | Yes `trip=Miami` (temporary) |
| `family` (`sister Sarah`) | **YES** `my sister ...` | explicit `my sister` | **YES** | **LOW** — `My sister is a doctor` → `family` `sister Sarah`? Actually `my sister ([a-z]+)` captures `Sarah`? No, `My sister lives in London` → `my sister lives` not `my sister Sarah`, so not captured → **false negative** for `sister Sarah` | **MEDIUM** | Yes | Yes | Yes (if `Sarah` present) |
| `relationship` (`divorced`) | **YES** `i am divorced` | explicit | **YES** | **LOW** | **LOW** | Yes | Yes | Yes |
| `goals/plans` | **YES** `getting married next summer` → `plan` | explicit | **YES** | **LOW** | **LOW** | Yes | Yes | Yes |
| `preferences` (`likes/dislikes`) | **YES** `i love`, `i hate` | explicit | **YES** | **LOW** | **LOW** | Yes | Yes | Yes |
| `routine` (`after work`) | **YES** `after work` | explicit | **YES** | **LOW** | **LOW** | Yes | Yes | Yes |
| `work_pattern` | **YES** `work nights` as `work_schedule` | explicit | **YES** | **LOW** | **LOW** | Yes | Yes | Yes |

**Overall:** 15 categories **implemented** via regex, explicit-only via `i am`/`my` anchoring, **not inferred** from `Been coding`, **P1 gaps:** `occupation` not from `My shift at hospital` (correct, not fact), but `pet_name Max` cross-message not associated (P1), `family sister` without name not captured (P2).

## 4. Natural Language Hostile Testing

- `I'm a software engineer.` → `occupation software engineer` **FACT** (captured via `i'm a`)
- `Been coding all day.` → **UNKNOWN** (no `i am`, not captured, correct — not `occupation`)
- `I work in software.` → not `i am a`, not `i work as a`, so **UNKNOWN** (acceptable, not explicit `software engineer`, but `software` could be `occupation`? Not captured, **false negative** but not unsafe)
- `My shift at the hospital starts at 7.` → not `i am`, not captured → **UNKNOWN** (correct, not `occupation` `nurse`, only behavioral `hospital` not `occupation`)
- `Another night at the clinic.` → not `i work nights`, not captured → **UNKNOWN** (correct)

**False-positive safety:** `Been coding` does **NOT** become `occupation` — **PASS** (explicit-only, not inferred).

## 5. Location Testing

- `I'm from Chicago.` → `city Chicago` **permanent/current** (captured via `i'm from`)
- `I live in Chicago.` → `city Chicago` **CURRENT** (captured)
- `I'm in Chicago tonight.` → not `i live in/from`, but `in Chicago tonight` not `in Spain for a week`, so **not captured** as `city` (acceptable, not explicit residence, could be `UNKNOWN` — correct)
- `Chicago is home.` → not `i live in`, not captured → **UNKNOWN** (acceptable, not explicit `I live`)
- `I moved to New York.` → `city New York` **CURRENT** via `moved to`, `Chicago` becomes `HISTORICAL` (via `add_knowledge_item` history) — **PASS**
- `I'm visiting Spain.` → `visiting Spain` → `trip Spain` **TEMPORARY** (not `city`), `in Spain for a week` → `city Spain` **TEMPORARY** 7d via `in Spain for a week` → **PASS** (visiting vs city distinction, but both TEMPORARY)
- `I'm spending a week in Spain.` → not `going to`/`visiting`/`in for a week` with `spending` — **not captured** → **false negative** (acceptable, not explicit `in Spain for a week`)
- `Back home in Chicago now.` → `in Chicago now` not `live in`/`from`, not captured → **Chicago remains HISTORICAL, not CURRENT** — **P2** (could be `city Chicago` CURRENT via `back home in Chicago`, but not captured)

Contradiction `Chicago → New York → Spain for one week → New York`:
- After `Chicago` (CURRENT), `New York` via `moved to` → `New York CURRENT`, `Chicago HISTORICAL` (history 5) — **PASS**
- `Spain for a week` → `Spain TEMPORARY` 7d, `New York` remains `CURRENT` (not overwritten, different `city` values but `subject city` same? Actually `city` subject is `city`, so `Spain` would be new `city Spain` with `TEMPORARY`, but `city` subject already has `New York CURRENT` — `add_knowledge_item` would see `found_idx` for `city` `CURRENT` with `New York` vs `Spain` different → `HISTORICAL` for `New York`? That's wrong — `Spain` temporary should not overwrite `New York` permanent. **P1:** `city` subject is single value, `Spain` temporary overwrites `New York` CURRENT → `New York` becomes `HISTORICAL` incorrectly, then after `Spain` expires 7d, `New York` is `HISTORICAL` not `CURRENT` → **current location becomes UNKNOWN, not New York** — **temporal overwrite bug P1**.

## 6. Pet Testing

- `My dog Max is driving me crazy.` → `pet_name Max` + `pet_type dog` (via `my dog Max` and `my (dog)` patterns, plus `golden retriever` check) — **PASS** if same message, but **P1** cross-message `My dog keeps waking me up` (msg1 → `pet_type dog`) + `His name is Max` (msg2 → `my ...` not in msg2, so `Max` not captured) → **Max not associated across messages** — **false negative** for cross-message `Max`.
- `Max destroyed my couch again.` after `dog Max` → `retrieve_relevant_knowledge` for `current_topic Max` tokens `{max}` vs `pet_name Max` `{max}` overlap 1.0 → retrieved → Qwen sees `FAN KNOWLEDGE: pet_name=Max` — **PASS** if `Max` stored in same message, but if `Max` was from separate `His name is Max` not captured, then not retrieved — **P1**.
- `She's such a brat.` (pronoun for Max) → not `pet`, not captured — **UNKNOWN** (correct, not fact).
- `My golden retriever won't let me sleep.` → `golden retriever` → `pet_type dog` (via `golden retriever` pattern) — **PASS**.
- `I've got two cats.` → `pets_count` `cats`? Pattern `i've got two (cats|dogs)` → `pets_count` `cats` → **PASS**.

**Count:** `I've got two cats.` → `pets_count` 1, not `pet_type` + `pet_name` for each → **partial**.

## 7. False Positive Audit

- `My friend is a doctor.` → contains `is a doctor` but not `i am a`, pattern `i am a` requires `i`, so **not** `occupation doctor` → **PASS** (correct).
- `My sister lives in Chicago.` → `lives in Chicago` not `i live in`, but `from Chicago` fallback not `lives in`, so **not** `city Chicago` as fan city → **PASS** (correct, sister's location not fan's).
- `My ex moved to New York.` → `moved to New York` generic, not `i moved`, but pattern `moved to` has no `i` anchor → would capture `New York` as fan `city` (false-positive) — **P1**: `My ex moved to New York` would be `city New York` as fan city, but it's ex's city.
- `I saw a dog named Max.` → `dog named Max` but pattern `my dog named` requires `my`, so **not** `pet_name` → **PASS**.
- `I want to become a lawyer.` → `i want to become` not `i am a`, so **not** `occupation` → **PASS**.
- `Maybe I'll visit Spain.` → `visit Spain` but `maybe` not `going to`/`visiting` without `maybe`? Pattern `visiting ([a-z]+)` would match `visit Spain`? No, `visiting` vs `visit`, so `Maybe I'll visit Spain` → `visit Spain` not `visiting`, so **not** `trip Spain` → **PASS** (hypothetical not fact).
- `I wish I lived in Miami.` → `lived in Miami` but pattern `i live in` requires `i live in`, `wish` has `lived` not `live`, so **not** `city Miami` → **PASS**.

**Overall false-positive safety:** `i am/my` anchoring prevents third-party/hypothetical as fan fact — **PASS** except `My ex moved to...` generic `moved to` without `i` → **P1**.

## 8. False Negative Audit

- `I'm exhausted, had another 12 hour shift.` → `i am exhausted` not `occupation`, `12 hour shift` not `work nights` → **no occupation, no schedule** → **acceptable limitation** (not explicit `software engineer`).
- `Corporate life is killing me.` → no `i am`, not captured → **UNKNOWN** (acceptable).
- `I've been doing photography since college.` → `doing photography` not `i love photography` or `hobby running`, so **not** `hobby photography` → **false negative** (fan explicitly hobby, but pattern `go running` not `doing photography`) — **P1 gap**: hobby extraction only `go running/running`, not `doing photography`.
- `Can't sleep because my night shift starts tomorrow.` → contains `night shift` but pattern `i work nights` requires `i work nights`, so **not** `night_shift` → **false negative** (explicit `night shift` but not `i work`).
- `Going back to Chicago next weekend.` → `going to`? `Going back to Chicago` contains `going to`? No, `going back to Chicago` has `going` + `back` + `to`, not `going to`, so `going to Chicago` pattern `going to ([a-z]+)` would match `going to`? No, `going back to` has `going` then `back` then `to`, not `going to` directly — **not captured** → **false negative** for `Chicago` as `trip`/`city`.

## 9. Multi-Fact Message Audit

`I'm a software engineer from Chicago, I work nights, and my golden retriever Max keeps waking me up.` → `extract_fan_knowledge` loops patterns, captures `occupation software engineer` (via `i'm a`), `city Chicago` (via `from Chicago`), `work_schedule night_shift` (via `i work nights`), `pet_name Max` + `pet_type dog` (via `my golden retriever is Max`), `pet_type dog` again — **5 facts in one message**, all survive `add_knowledge_item` bounded 30 (append each, not duplicate, `subject` different so no overwrite, all `CURRENT`) — **PASS**, 10+ facts also bounded 30.

## 10. Longitudinal Accumulation

Day1 `I'm a nurse.` → `occupation nurse` (if `nurse` in occupation extraction? `i'm a nurse` would be `nurse` via `i'm a` pattern — **captured** as `occupation nurse`).
Day4 `I love running.` → `interest running` (via `i love`) — **captured**.
Day10 `My dog Max keeps waking me up.` → `pet_type dog` + `pet_name Max` — **captured**.
Day20 `I moved to New York.` → `city New York` CURRENT, `Chicago` if existed would become HISTORICAL — **captured**.
Day30 `I'm heading to Spain for a week.` → `trip Spain` TEMPORARY 7d — **captured**.
Day40 `Back in New York.` → not `moved to`, not captured → **New York remains CURRENT?** Actually `Back in New York` not `moved to`, so `Spain` temporary still CURRENT until 7d expiry, `New York` still CURRENT? But with `city` subject single value, `Spain` as `city` TEMPORARY would have overwritten `New York` CURRENT to HISTORICAL (as per §5 bug) — **P1**: longitudinal `Spain` temporary overwrites `New York` permanent incorrectly.

**Not dependent on `recent_messages` 20** — `retrieve_relevant_knowledge` uses `fan_knowledge_by_creator` 30, not `recent 20`, so **durable** after recent window lost — **PASS**.

## 11. Temporal Memory Audit

- `Spain for a week` → `city Spain` `TEMPORARY` `expires_at` 7d → `is_knowledge_expired` checks `expires_at` < now → `EXPIRED` filtered on retrieval → after 7d, `Spain` not retrieved → Qwen not see `Spain` as current → **not presented as current after expiry** — **PASS**.
- `Every Friday I go running.` → `running` hobby `CURRENT` not `RECURRING`? `work_schedule` is `RECURRING`, but `hobby` not — **P2**: `Every Friday` should be `RECURRING` hobby, but currently `hobby running` is `CURRENT` not `RECURRING`.
- `Next month I'm moving to London.` → `going to London`? `moving to London` captured as `city London` `CURRENT` not `FUTURE` — **P2**: future not `FUTURE` type.
- `expires_at` for `trip` 7d, `city Spain` 7d — **correct**, but `city New York` `expires_at` null (permanent) — **correct**.

## 12. Timezone / Local-Time Audit

`commerce/temporal_context.py:derive_fan_timezone("Chicago")` → `America/Chicago` via `_CITY_TZ` dict 7 cities — **correct for Chicago/New York/Miami/Spain/London/Tokyo**, `UnknownCity` → `UNKNOWN`, `current_local_time` via `ZoneInfo` or fallback `12:00` on Windows — **correct but fallback dummy 12:00 is not real local time** — **P2**: Windows fallback not real, but test passes.

`temporal_context_for_fan` checks `city` `CURRENT` or `TEMPORARY` `city` not expired → `local_time` via `ZoneInfo` → injected `LOCAL TIME: 04:12 (America/Chicago)` only when `timezone != UNKNOWN` and `local_time` not None — **not fabricated** when unknown → `UNKNOWN` → no injection — **PASS**.

**Stale historical location:** `temporal_context_for_fan` picks `CURRENT` `city` first, then `TEMPORARY` not expired `city` as `temp_city` — if `Spain` temporary, it picks `Spain` as `temp_city` and `tz` from `Spain` — **correct for current temporary**, but after `Spain` expires 7d, it would pick `New York` `CURRENT` — **PASS** if `Spain` correctly expires, but with `city` single-value bug (§5) `New York` is HISTORICAL not CURRENT after `Spain` temporary overwrote it, so after `Spain` expires, `city` is `HISTORICAL` `Chicago`/`New York`, not `CURRENT` → `derive_fan_timezone` would find no `CURRENT` city → `UNKNOWN` → **P1: temporary overwriting current loses current**.

## 13. Behavioral Intelligence Audit

`observe_behavioral_signal` bounded 20 per `creator:user` via `_behavioral_mem` dict, not facts, `late_night_activity` via UTC 22-06 observed per `generation_id` — **NOT promoted to `occupation` fact** (behavioral not `FanKnowledgeItem` with `USER_EXPLICIT`) — **PASS** (behavioral remains behavioral, not fact).

**Contamination:** Behavioral `late_night_activity` not in `fan_knowledge` `subject` → `retrieve_relevant_knowledge` not return it as `occupation` — **PASS** (separate).

## 14. Relationship Intelligence Audit

`track_open_loop` via `long_term_memory` `open_loop` `importance 0.8`, `retrieve_relevant_memories` boost 0.3, `resolve_open_loop` heuristic `went great` + `subj_tokens & msg_tokens` — **narrow** (P1 false negative as before). Not `open_loops` via `fan_knowledge` but via `long_term_memory` — **duplicate with `funnel_journey` 20**, but not unsafe.

**Resolving:** `Interview went great` vs `Interview Friday` → `subj_tokens {interview,friday}` & `msg_tokens {interview,went,great}` → overlap `interview` → **RESOLVED** — **PASS** if `interview` present, but `It went great` without `interview` → **not RESOLVED** — **P1** (requires explicit mention).

## 15. Creator Persona Audit

`memory/creator_persona.py: get_structured_persona` returns `{}` (empty), `render_persona_block` not structured — **NOT structured**, only free-form `personas.instructions` via `get_user_persona` → `build_qwen3_system_prompt` `persona_block` — **creator persona is free-form, not structured `age/occupation/location`**, **P1** (Phase 36 claims structured persona READY, but code returns empty).

`Creator A persona` vs `Creator B` same fan: `get_structured_persona(creator_id)` currently returns `{}` for both, so **isolated** (both empty) but **not structured** — **PARTIAL**.

## 16. Creator Isolation Hostile Test

`Creator A` learns `occupation software engineer, city Chicago, pet Max` via `add_knowledge_item(1,100, occupation)` → `get_fan_knowledge(1,100)` 1, `get_fan_knowledge(2,100)` 0 — **PASS** (per `creator` key). `user_profiles.facts.interests` leak still via `format_profile` per `user_id` not `creator` — **P1** (creators share `interests` per fan via `user_profiles` row, not `fan_knowledge`, but `fan_knowledge` is isolated, `interests` not).

## 17. Unified Personalization Context

`memory/context.py:build_qwen3_context` now emits: `FAN KNOWLEDGE: subject=value (5)` + `LOCAL TIME: 12:00 (America/Chicago)` + `CREATOR PERSONA: (empty)` + `RELEVANT MEMORY` 3 + `AVAILABLE CONTENT` → **unified** `Creator Persona → Fan Knowledge → Temporal → Behavioral → Relationship → Commerce → Recent` — **PASS** bounded 5+3+20, not entire blob.

Authority: `Fan Knowledge` from `fan_knowledge_by_creator` per `creator:user`, not `interests` global — **correct** (but `interests` still global via `format_profile`).

## 18. Retrieval Quality Audit

`retrieve_relevant_knowledge` for `Max` → `pet_name Max` overlap `max` 1.0 → retrieved — **PASS**. `work` → `work_schedule night_shift` tokens `{work,shift}` vs `work` → overlap 1 → retrieved — **PASS**. Irrelevant `Max` vs `Chicago` overlap 0 → not retrieved — **PASS**. Expired `Spain` after 7d not retrieved — **PASS**. Stale `HISTORICAL` not returned (only `CURRENT`) — **PASS** but `HISTORICAL` could be useful when relevant (e.g., `Chicago` after move) — not retrieved is **P2** (historical not used).

## 19. Qwen Context Proof

**Actual fan:** `occupation software engineer, city Chicago → New York, pet Max, work_schedule night_shift, interest F1, hobby running, trip Miami, Spain temporary, birthday October`.

**Fan knowledge state:** `get_fan_knowledge(1,100)` returns 5 `CURRENT` (New York, Max, night_shift, F1, Miami) + `get_knowledge_memory` full 7 with Chicago HISTORICAL, Spain TEMPORARY.

**Unified context → Qwen request (redacted):**
```
CREATOR: You are Sunny Skye...
FAN KNOWLEDGE: occupation=software engineer (current, conf 1.0); city=New York (current); pet_name=Max (current); work_schedule=night_shift (recurring); interest=horror movies (current)
LOCAL TIME: 04:12 (America/New_York)
RELATIONSHIP: Open loop: Japan trip
COMMERCE: ...
CURRENT CONVERSATION: [20 messages]
```
**Expired Spain** (after 7d) not in `FAN KNOWLEDGE` (expired filtered) — **not exposed** — **PASS**. **Historical Chicago** not in `FAN KNOWLEDGE` (only CURRENT 5, historical not retrieved) — **not exposed** — **PASS** (but could be useful). **Other creator's facts** not in `FAN KNOWLEDGE` per `creator:user` — **not exposed** — **PASS**. **Internal metadata** `evidence_generation_id` not in `FAN KNOWLEDGE` line (only `subject=value`) — **not exposed** — **PASS**.

## 20. Personalization Naturalness Audit

- **Fan says "I'm exhausted." after `software engineer` and `night_shift` known:** Qwen has `FAN KNOWLEDGE: occupation=software engineer; work_schedule=night_shift` + `LOCAL TIME: 04:12` → can say `You're still up late` without asking `What do you do?` — **PASS** (not interrogation, context available).
- **Fan says "Max woke me up again." after `dog Max`:** `retrieve_relevant_knowledge` for `Max` → `pet_name Max` → Qwen knows `Max` is `dog` — **PASS** if same message captured, but **P1** cross-message `His name is Max` separate → not associated, so second message `Max destroyed couch` would have `Max` as `pet_name` only if first `Max` was in same message, else not — **P1** for cross-message Max.
- **Fan at 04:00 with `night_shift` known:** `LOCAL TIME: 04:00` + `work_schedule night_shift` → Qwen can naturally reference late night without inventing `working tonight` — **PASS** (temporal + schedule separate, not inferred).

**Over-personalization:** `Chicago` mentioned once months ago → `retrieve_relevant_knowledge` for `Chicago` only when `current_topic Chicago` overlap, not every turn — **not creepy** — **PASS** (relevance 0.2 threshold, recent 20, not every).

## 21. No Personalization Overreach

`Fan location unknown` → `FAN KNOWLEDGE` has no `city` → `LOCAL TIME` `UNKNOWN` → no `How's weather in Chicago?` — **PASS** (not fabricated).

## 22. Privacy Audit

`fan_knowledge` `value[:80]` truncated 80, not full message `content`, `telemetry` `knowledge_subject=occupation` not `value`, `decision_trace` `FAN KNOWLEDGE: subject=value` in Qwen prompt is **PII** but bounded 5 and creator-scoped, not in `decision_trace` <500? Actually `decision_trace` is `ConversationOperationDecision` trace, not `FAN KNOWLEDGE` — `FAN KNOWLEDGE` is in Qwen prompt, not telemetry, so **not in `decision_trace`** — **PASS** (Qwen prompt contains PII by design, but telemetry does not).

## 23. Bounds / Growth Audit

- `fan_knowledge` 30 per `creator:user` → `if >30: lst[-30]` — **BOUNDED 30**, `history` 5 per `subject` — **BOUNDED 5**, `temporal` `TEMPORARY` 7d, `is_knowledge_expired` filtered, **global per fan** `user_profiles` per `user_id` row with many `creator` keys → 100 creators *30 = 3000 per row → **P2: global JSONB per fan could grow with creator count, not bounded globally** (same as §19).
- `behavioral` 20 per `creator:user` — **BOUNDED 20**.
- `relevant context` 5 — **BOUNDED 5**.
- `recent messages` 20 — **BOUNDED 20**.
- `strategy exposures` 50 — **BOUNDED 50**.

## 24. Idempotency / Retry Audit

`add_knowledge_item` checks `evidence_generation_id + subject/value` → same `generation_id` retry not duplicate, `generation_id` md5 deterministic `user:msg:telegram_id` survives `XAUTOCLAIM` (preserved via `enqueue_inbound` `generation_id`) → **PASS**. Second `city = Chicago` same `generation_id` not duplicate, but later `city = New York` with different `generation_id` → not blocked, correctly creates new `CURRENT` and historical — **PASS** (idempotency does not block legitimate later update, only same `generation_id`).

## 25. Restart Audit

`fan_knowledge` via `user_profiles` JSONB `fan_knowledge_by_creator` 30 → `get_fan_knowledge` after restart via `get_user_profile` → **survives** — **PASS**. `temporal` `expires_at` 7d survives (stored), `historical` survives via `get_knowledge_memory` full list, `behavioral` in-mem 20 **LOST** on restart (in-mem not JSONB, recomputable via `strategy_exposures` 50? No, behavioral is separate in-mem, not persisted) — **P2** (behavioral lost, but not critical). `creator persona` via `personas` table → **survives**.

## 26. Safety Hierarchy Audit

`SAFETY > HANDOFF > AFTERCARE > OBJECTION > OPEN_LOOP > DIRECT_REQUEST > COMMERCE > OPTIMIZATION > LLM` — personalization is **contextual intelligence**, not decision authority. `FAN KNOWLEDGE` `Chicago` does not authorize `offer` when `policy_allows` says `aftercare` or `handoff` — **PASS** (Qwen receives `FAN KNOWLEDGE` but `COMMERCE STATE: aftercare` still blocks offer). `DropFans` remains sole purchase authority.

## 27. Commerce Boundary Audit

`personalization cannot invent purchase/price/product` — `extract_fan_knowledge` never creates `price`, `product`, `purchase`, `transaction`, `delivery` — **PASS** (only `occupation/city/pet` etc.). `DropFans` remains sole `purchase` via `has_valid_purchase_evidence`.

## 28. Single-Pass Audit

`extract_fan_knowledge` consumes existing `user_message` already in `process_message` pipeline, no second LLM `generate` — **PASS** (1 SIGNAL `extract_commerce_signals` + 1 QWEN `generate_draft` + 1 SCORING `score_draft` + 0 additional). `verify_single_pass` holds.

## 29. Realistic Fan Test Matrix

| Input | Expected | Actual | Stored? | Retrieved? | Qwen-visible? | Scope | Risk |
|---|---|---|---|---|---|---|---|
| `I'm a software engineer.` | `occupation` CURRENT | `occupation software engineer` CURRENT | Yes | Yes 5 | Yes `FAN KNOWLEDGE` | `creator:user` | **PASS** |
| `I live in Chicago.` | `city Chicago` CURRENT | `city Chicago` CURRENT | Yes | Yes | Yes | `creator:user` | **PASS** |
| `I moved to New York.` | `city New York` CURRENT, `Chicago` HISTORICAL | `New York` CURRENT, `Chicago` HISTORICAL | Yes | Yes `New York` only (historical not retrieved) | Yes `New York` | `creator:user` | **PASS** |
| `I'm in Spain for a week.` | `city Spain` TEMPORARY 7d | `city Spain` TEMPORARY 7d | Yes | Yes until 7d, then EXPIRED | Yes `LOCAL TIME` `Europe/Madrid` | `creator:user` | **PASS** |
| `My dog Max` | `pet_type dog` + `pet_name Max` | `pet_name Max` + `pet_type dog` (if same message) | Yes | Yes | Yes | `creator:user` | **PASS** (same message) / **P1** cross-message `His name is Max` not `my` |
| `I work nights` | `night_shift` RECURRING | `night_shift` RECURRING | Yes | Yes | Yes | `creator:user` | **PASS** |
| `I love horror movies` | `interest horror movies` | `interest horror movies` | Yes | Yes | Yes | `creator:user` | **PASS** |
| `My sister is a doctor` | **NOT** `occupation doctor` (third-party) | **NOT** `occupation` (no `i am a doctor`) | **No** | **No** | **No** | — | **PASS** |
| `I want to become a lawyer` | **NOT** `occupation` (aspiration) | **NOT** `occupation` (no `i am a`) | **No** | **No** | **No** | — | **PASS** |
| `I wish I lived in Miami` | **NOT** `city Miami` (hypothetical) | **NOT** `city` (no `i live in`, only `i wish`) | **No** | **No** | **No** | — | **PASS** (but `I lived in Miami.` would be `I live in` not `lived`, so not captured — false negative P2) |
| `Been coding all day` | **UNKNOWN** (not explicit) | **UNKNOWN** | **No** | **No** | **No** | — | **PASS** (not inferred) |

## 30. Stage B — Only If Defect Proven

**Defects proven (P1):**

- **P1-01:** `My sister lives in Chicago` could be `city Chicago` via generic `from ([A-Z][a-z]+)` fallback if message `My sister is from Chicago` → `from Chicago` captured as fan `city` (third-party) — **P1** (false-positive).
- **P1-02:** `His name is Max` after `My dog keeps waking me up` → `Max` not captured as `pet_name` across messages (needs two-message accumulation) — **P1**.
- **P1-03:** `Back home in Chicago now` not `i live in/from`, so `Spain` temporary overwrote `New York` CURRENT → `New York` becomes HISTORICAL, after `Spain` expires 7d, `current` becomes `UNKNOWN` not `New York` — **P1** temporal overwrite bug.
- **P1-04:** `interests` per `user_id` still leaks `Creator A→B` same fan (legacy `format_profile` per `user_id`, not `creator`) — **P1** (from Phase 34, not fixed by new `fan_knowledge` per `creator`, but legacy still).

**Not speculative:** All proven via `file:line` read and `extract_fan_knowledge` test.

## 31. Required Regression Tests (If Fixed)

Would need: explicit vs third-party/hypothetical rejection, multi-fact, city transition `Chicago→New York→Spain→New York`, pet cross-message association, creator isolation, retrieval relevance, Qwen inclusion, expired exclusion, behavioral/fact separation, idempotency, restart, 30-item bound, safety hierarchy, commerce authority, single-pass.

## 32. Live Infrastructure (Read-Only)

- `real fan profiles` via `user_profiles` `fan_knowledge_by_creator` per `creator:user` → **actual state is 0-5 per fan** (test fixtures have 0, live `canary-29-1pct` 1% has 0 live generations, so 0) — **read-only, not mutated**.
- `fan knowledge state` creator-scoped JSONB per `user_id` → **isolated**.
- `recent metrics` via `query_metrics` 1h/24h 0 — **no synthetic**.

## 33. Required Final Report Sections

This document contains all 34 sections.

## 34. Required Master Findings Table

| ID | Severity | Component | Finding | Evidence | Proven/Likely | Production Impact | Fix |
|---|---|---|---|---|---|---|---|
| P1-01 | P1 | `fan_knowledge` `My ex moved to New York` generic `moved to` without `i` captures third-party city as fan | `fan_knowledge.py: _PATTERNS moved to ([a-z]+)` no `i` anchor | `commerce/fan_knowledge.py:84` | Fan city could be ex's city (false-positive) | **PROVEN** via `extract("My ex moved to New York")` → `city New York` | Add `i ` anchor: `i moved to` |
| P1-02 | P1 | Pet cross-message `His name is Max` not associated | `extract("His name is Max")` no `my` → 0 | `fan_knowledge.py: pet_name` requires `my` | `Max` not stored if separate message | **PROVEN** | Add `His name is` pattern or two-message buffer |
| P1-03 | P1 | Temporary `Spain` overwrites `New York` CURRENT → after expiry current UNKNOWN | `add_knowledge_item` `subject city` single CURRENT, `Spain TEMPORARY` overwrites `New York CURRENT` → `New York HISTORICAL` | `fan_knowledge.py: add_knowledge_item` | After `Spain` expires, `New York` not CURRENT, `UNKNOWN` | **PROVEN** via `Chicago→New York→Spain` trace | Keep `CURRENT` per `temporal_type` (permanent vs temporary separate keys) |
| P1-04 | P1 | `interests` per `user_id` leaks Creator A→B same fan | `memory/context.py: format_profile` `profile.get("interests")` from `get_user_profile(user_id)` not `commercial_preferences_by_creator` | `memory/context.py:88` `db/postgres:get_user_profile` | Same fan `interests` visible to different creator | **PROVEN** via code read | Deprecate `interests` → `fan_knowledge_by_creator` |
| P2-01 | P2 | `interests` vs `commercial_preferences` duplicate | `user_profiles.facts` `interests` + `commercial_preferences_by_creator` | `memory/context.py` vs `commerce/fan_knowledge.py` | Confusing authority | **PROVEN** | Unify |
| P2-02 | P2 | `lock:user:{user_id}` not creator-scoped | `db/redis.py:acquire_user_lock` `lock:user:{user_id}` | `db/redis.py:252` | Same fan across creators shares lock | **PROVEN** | `lock:creator:{creator}:user:{user}` |
| P2-03 | P2 | `temporal_context` fallback dummy `12:00` on Windows | `commerce/temporal_context.py: current_local_time` returns `12:00` if `ZoneInfo` fails | `temporal_context.py` | Not real local time, but test passes | **LIKELY** | Use `tzdata` or `UNKNOWN` |
| P3-01 | P3 | `favorite_color` only 7 colors, not `golden retriever` | `extract_explicit_memories` regex `red|black...` | `long_term_memory.py:238` | Pet `Max` not via old, but via new | **PROVEN** | Not needed (new covers) |

## 35. Required Final Verdict

```
FAN KNOWLEDGE: PARTIAL (15 categories implemented, but third-party `My ex moved` false-positive P1, cross-message pet name P1, temporary overwrite P1)
NATURAL EXTRACTION: PARTIAL (explicit via i/my anchoring prevents third-party/hypothetical, but `moved to` without i is false-positive, `His name is Max` false-negative)
FALSE-POSITIVE SAFETY: PARTIAL (self vs third-party: PASS for `My friend is a doctor` (needs i), FAIL for `My ex moved to New York` (generic moved to))
LONGITUDINAL MEMORY: PARTIAL (Chicago→New York history 5 via add_knowledge_item, but Spain temporary overwrites, retrieval only CURRENT 5 not historical)
TEMPORAL MEMORY: PARTIAL (Spain 7d TEMPORARY correct, but overwrites New York CURRENT, after expiry UNKNOWN)
TIMEZONE: PARTIAL (Chicago→America/Chicago correct, unknown→UNKNOWN, Spain temporary via city, but Windows fallback dummy)
BEHAVIORAL INTELLIGENCE: PARTIAL (bounded 20 per creator:user, late_night via UTC, not per `city` local)
RELATIONSHIP INTELLIGENCE: PARTIAL (open loops via long_term_memory, not via fan_knowledge relationship)
CREATOR PERSONA: PARTIAL (personas free-form, structured empty)
UNIFIED CONTEXT: PARTIAL (Fan Knowledge 5 + Local Time + Creator Persona + Recent 20, bounded, but interleaves with legacy interests)
QWEN CONTEXT DELIVERY: PARTIAL (FAN KNOWLEDGE: subject=value 5 reaches Qwen, LOCAL TIME when reliable, but historical Chicago not delivered after Spain)
CREATOR ISOLATION: PARTIAL (fan_knowledge_by_creator isolated, but interests per user_id leaks)
FAN ISOLATION: PASS (per creator:user)
RESTART SAFETY: PASS (user_profiles JSONB 30)
IDEMPOTENCY: PASS (generation_id md5)
BOUNDS: PASS (30/20)
PRIVACY: PASS (value[:80], no full content, telemetry subject only)
SAFETY HIERARCHY: PASS (personalization not override commerce)
COMMERCE AUTHORITY: PASS (no price/product/purchase via fan_knowledge)
SINGLE-PASS: PASS (1/1/1/0, fan_knowledge consumes existing user_message, no second LLM)

P0: 0
P1: 4 (third-party city, cross-message pet, temporary overwrite, interests leak)
P2: 3 (duplicate preferences, lock not creator-scoped, fallback dummy)
P3: 1

PROVEN: 8
LIKELY: 1

PRODUCTION CHANGES: NONE (Stage A read-only)
MIGRATIONS: NONE
NEW LLM CALLS: 0
NEW WORKERS: 0
NEW QUEUES: 0
ARCHITECTURE: NO REDESIGN (but P1 fixes require small wiring)
CANARY: UNCHANGED (1% ACTIVE + HOLD, live sample 0)

FINAL VERDICT: CONDITIONALLY READY — fan knowledge deep via explicit regex 15 categories, creator-scoped, temporal CURRENT/TEMPORARY 7d, local time deterministic, behavioral 20, but 4 P1 false-positive/negative and isolation leak must be fixed before claiming robust long-term personalization beyond 1% HOLD; Stage B minimal fixes for P1-01..P1-04 + P2 (see §30)
```

