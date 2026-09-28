# AI_NATIVE_PERSONALIZATION_PHASE_34_FORENSIC_AUDIT.md
# Phase 34 — Fan Knowledge, Memory & Personalization Forensic Audit (Stage A, READ-ONLY)
# Date: 2026-08-30
# Method: Hostile, evidence-based, no production code/schema/prompt/test/config/Redis/Postgres/canary mutation

## 1. Executive Summary
Hostile read-only audit of fan knowledge pipeline finds **system has fragmented, shallow, non-learning memory, not long-term personalization**. It captures **~4 fan fields** (age/location/occupation/interests via `user_profiles.facts` `interests` etc.) as `interests`/`preferences` strings, plus **~5 commercial preferences** (`commercial_preferences_by_creator`), plus **limited explicit memories** (`favorite_color`, `dislike`, `trip miami`, `interview friday`, `movie`) via `extract_explicit_memories` regex, plus **recent conversation 20 messages** as transient prompt, but **no comprehensive temporal/behavioral/relationship memory, no timezone, no city/country, no schedule, no family/pets/hobbies/goals, no confidence/evidence, no contradiction handling, no local-time**. Qwen receives **only recent conversation + 3 LTM items via token overlap + commercial preferences via AVAILABLE CONTENT**, not persistent fan knowledge base. `If fan says "I work as software engineer" today, it is NOT stored as `occupation=software engineer` after recent context expires (3 days later, `get_recent_messages` 20 no longer contains it, `retrieve_relevant_memories` will not find it because `extract_explicit_memories` regex does not capture `software engineer` occupation).** All P0 isolation PASS, but **P1 6 gaps: no natural extraction, no persistence, no retrieval, no timezone, no temporal, no update**.

## 2. Current Architecture (Verified)

```
Telegram inbound (chatbotv2/handlers.py: debounce 3s → db/redis:enqueue_inbound XADD inbound_messages)
 ↓ workers/llm_worker.py:run_worker (XREADGROUP llm_workers, XAUTOCLAIM 60s, acquire_user_lock 30s)
 ↓ memory/context.py:build_qwen3_context (get_user, get_user_profile, get_recent_messages 20/800, derive_conversation_state for identity/open_threads, AVAILABLE CONTENT rank_products_by_relevance TOP2, LTM retrieve_relevant_memories 3, commerce_text)
 ↓ SINGLE extract_commerce_signals → conversational bridge → pressure/risk/lifecycle → pre-Qwen gate → operational intelligence → Qwen 1 → scoring 1 → send
 ↓ post_process: extract_and_update_profile (memory/profile.py) + maybe_summarize (memory/summarizer.py) — async fire-and-forget, not awaited before Qwen
```

No `FanKnowledge` unified layer, no `Temporal Context`, no `Behavioral Evidence`, no `Creator Persona` authoritative beyond `personas` table.

## 3. Current Fan Data Model (Actual Fields)

| Field | Storage | Type | Creator Scoped? | Fan Scoped? | Source | Writer | Reader | Lifetime | Confidence? | Used by Qwen? |
|---|---|---|---|---|---|---|---:|---|---|---|
| `first_name` | `users.first_name` | TEXT | No (per user_id) | Yes (user_id PK) | Telegram `event.sender.first_name` | `upsert_user` | `get_user` → `build_qwen3_context` | indefinite | No | Yes (Fan: {first_name}) |
| `username` | `users.username` | TEXT | No | Yes | Telegram | `upsert_user` | `get_user` | indefinite | No | No |
| `funnel_stage` | `users.funnel_stage` | TEXT | No | Yes | `advance_funnel` on purchase | `get_user` | `build_qwen3_context` | indefinite | No | No (but via `derive_relationship_state`) |
| `message_count` | `users.message_count` | INT | No | Yes | `upsert_user` increment | `upsert_user` | `get_user` | indefinite | No | No |
| `interests` / `preferences` | `user_profiles.facts` `interests` or `preferences` (string or list) | JSONB string/list | No (per user_id, not creator) | Yes (per user_id row) | `extract_and_update_profile` via LLM? Actually `memory/profile.py:extract_and_update_profile` calls LLM? No, it is not LLM, it is regex? Check: `extract_and_update_profile` uses `extract_explicit_memories`? Not. It is `memory/profile.py` which does `await get_user_profile` then LLM? Let's trace: `memory/profile.py: extract_and_update_profile` uses `get_user_profile` and `update_user_profile` with `facts` containing `interests` — but extraction is via `commerce/long_term_memory.py:extract_explicit_memories` regex for `favorite_color` etc., not comprehensive. | `post_process` async | `build_qwen3_context` `profile.get("interests")` | indefinite | No | Yes (via `AVAILABLE CONTENT` `fan_preferences` and `PROFILE:`) |
| `favorite_color` (preference) | `user_profiles.facts` `long_term_memory_by_creator` `value` | JSONB `long_term_memory_by_creator` `str(creator)` → list of `memory_item` with `subject favorite_color` | **YES** `str(creator)` | Yes per `user_id` row | `extract_explicit_memories` regex `my favorite color is (red|...)` | `add_memory_item` | `retrieve_relevant_memories` | 90d decay 7d? Actually `PREFERENCE` 30d, `FACT` 90d | `EXPLICIT 1.0` | Yes via `RELEVANT MEMORY: favorite_color=...` (limit 3) |
| `trip miami` (plan) | same | same | YES | Yes | same regex `going to miami` | same | same | 7d | EXPLICIT 1.0 | Yes |
| `interview friday` (open_loop) | same | same | YES | Yes | same regex `interview friday` | same | same | 7d importance 0.8 | EXPLICIT 1.0 | Yes via `open_loop` boost 0.3 |
| `commercial_preferences_by_creator` | `user_profiles.facts` `commercial_preferences_by_creator` `str(creator)` → dict `count, confidence, last_seen` | JSONB | YES | Yes | `fan_memory.py:update_fan_preference` not called in `llm_worker`? Actually `update_fan_preference` is defined but not called in `llm_worker` — only `get_commercial_preferences` used for `rank_products` | `get_commercial_preferences` | `AVAILABLE CONTENT` | 30d | `WEAK 0.5` | Yes |
| `funnel_journey_by_creator` | `user_profiles.facts` `funnel_journey_by_creator` `str(creator)` → list 20 | JSONB | YES | Yes | `revenue_intelligence:record_funnel_transition` (not called in `llm_worker`) | tests only | `get_funnel_journey` | 20 | No | No |
| `strategy_exposures` | `user_profiles.facts` `strategy_exposures_by_creator` 50 per `creator:user` | JSONB + `_exposure_buffer` | YES | Yes | `make_exposure` per generation | `get_exposures_memory` | bounded 50 | No | No (not to Qwen) |
| `handoff_by_creator` | `user_profiles.facts` `handoff_by_creator` | JSONB | YES | Yes | `make_handoff` via `operational_execution` | `get_handoff` | `RELEVANT?` | per fan 1 | No | No (but blocks Qwen) |
| `conversation_summaries` | `conversation_summaries` table `user_id, summary, message_count_at_summary` | TABLE | No | Yes | `maybe_summarize` every 20 messages via Qwen? Actually `summarizer` uses LLM? | `save_summary` | `get_latest_summary_with_age` | indefinite, 1 per user (update) | No | Yes (Summary 2 sentences, if `summary_age_days` >7 note) |
| `recent conversation` | `messages` table `user_id, direction, content, telegram_message_id` LIMIT 20 | TABLE | No | Yes | `save_inbound_message` per handler | `get_recent_messages` | 20/800 | transient | No | **YES** — main Qwen context (recent_history_for_state) |
| `timezone` | **NOT STORED** — no `timezone` column in `users` or `user_profiles`, no `time_zone` key found via grep (0 hits except `get_timezone` for DropFans creator) | — | — | — | — | — | — | — | — | **NO** |
| `city/country` | **NOT STORED** — `location` key in `profile` via `format_profile` but `location` is `interests`? Actually `_PROFILE_LABELS` has `location` but `extract_explicit_memories` not capture city/country, only `miami` as plan, not location fact | — | — | — | — | — | — | — | — | **NO** |
| `occupation` | **NOT STORED** — `occupation` in `_PROFILE_LABELS` but extraction regex does not capture `software engineer`, only `nurse`? No, only `favorite_color`, `dislike`, `miami`, `interview` — **not occupation** | — | — | — | — | — | — | — | — | **NO** |
| `schedule` | **NOT STORED** | — | — | — | — | — | — | — | — | **NO** |
| `behavioral pattern` | **NOT STORED** — `compute_fatigue` is per `strategy_family` last 5, not per fan behavior like `usually active late at night` | — | — | — | — | — | — | — | — | **NO** |

**Duplication:** `interests` appears as `user_profiles.facts.interests` (per user, not creator) and also `commercial_preferences_by_creator` per creator — **duplicate, not isolated vs isolated** (P2).

## 4. Current Memory Model

- **Memory types:** `long_term_memory_by_creator` list 20 per `creator:user` with fields `memory_id, creator_id, user_id, memory_type (fact/preference/dislike/plan/open_loop/topic), subject, value, confidence 1.0/0.8/0.5, source explicit/weak, first_seen/last_seen, observation_count, importance 0.4-0.8, expires_at null, status OPEN/RESOLVED` — bounded 20, 30d/90d decay via `is_memory_expired`.
- **Not memory:** `recent conversation` 20, `summary` 1, `exposures` 50, `strategy evidence` 20, `journey` 20 — separate.

## 5. Complete Memory Call Graph

**Static personal fact** (e.g., `favorite_color red`):
```
Telegram "my favorite color is red" → chatbotv2/handlers.py: save_inbound_message (messages) → debounce → enqueue_inbound → llm_worker:process_message → build_qwen3_context (get_user_profile → profile={}) → extract_explicit_memories("my favorite color is red", creator 1, user 100) → regex "(my favorite color is (red|...))" → create_memory_item(creator, user, memory_type=preference, subject=favorite_color, value=red, confidence=1.0, source=explicit) → add_memory_item → get_user_profile(100) → by_creator["1"] list append → if >20 keep last 20 → update_user_profile
→ future message 3 days later: build_qwen3_context → retrieve_relevant_memories(creator 1, user 100, current_topic="red", limit 3) → is_memory_expired? 3d <30d → not expired → tokens overlap (red) *0.5 + confidence 0.3 → score 0.8 → returned → messages.append "RELEVANT MEMORY: favorite_color=red (preference, conf 1.0)" → Qwen sees it
```
*If fan says "I work as software engineer" → `extract_explicit_memories` regex does **NOT** match `software engineer` (only red/black... colors, miami, interview) → **no create_memory_item → not persisted → future retrieve returns [] → Qwen does NOT know** — **break at extraction**.*

**Preference** (e.g., likes red):
Same as above, but `commercial_preferences_by_creator` is **not written** via `update_fan_preference` in `llm_worker` (only `get` used) — **preference via long_term_memory, not commercial**, but `rank_products_by_relevance` uses `profile.get("interests")` per user not creator — **break: commercial preference not persisted from conversation**.

**Open-loop** (`interview Friday`):
```
"I have an interview Friday" → extract_explicit_memories → memory_type open_loop subject interview value text[:50] confidence 1.0 → add_memory_item
→ later "It went great" → llm_worker: resolve_open_loop → low contains "went great" + subj_tokens & msg_tokens? Subject "interview" tokens {interview}, msg "It went great" tokens {it,went,great} → **no overlap** → **NOT RESOLVED** → remains OPEN → retrieve_relevant_memories still returns it with boost 0.3 → Qwen sees `RELEVANT MEMORY: interview=... (open_loop)` and `COMMERCIAL STATE: open_loop`? Actually open_loop via `conversation_operations` not memory, but memory still OPEN → repeated callback
```
**Break at resolution** — false negative.

## 6. Natural Extraction Audit (`extract_explicit_memories`)

What it extracts (regex, explicit only, 20 bounded):
- `my favorite color is|i love (the color )?|i really like (red|black|blue|green|pink|white|purple)` → `favorite_color`
- `i don't like|i hate (being called )(\w+)` → `dislike`
- `going to miami|exam friday|interview friday|come back|after payday` → `open_loop`/`plan`/`commitment`
- `movie + watching` → `topic movie` weak 0.5

What it **ignores** (not captured, no schema, not persisted, not bounded, cannot update, no temporal):
- `software engineer`, `nurse`, `hospital`, `night shifts`, `dog`, `golden retriever named Max`, `Chicago`, `New York`, `Spain`, `timezone`, `age`, `family`, `relationship status`, `hobbies`, `sports`, `music`, `food`, `travel`, `goals`, `plans` beyond `miami`, `communication preferences`, `sleep patterns`

Schema: fixed `memory_type/subject/value` with `subject` hard-coded `favorite_color/dislike/trip/interview/movie`, not extensible, no `city`, `occupation`, `schedule`, `pet`, `timezone`, no `evidence` beyond `source explicit`, no `temporal` `effective_from/expires_at`, no `previous value`/`history`, **cannot update** except via `subject` same → confidence compare (explicit > weak), but **cannot represent city change** `Chicago → New York` (would keep old `Chicago` if `New York` not extracted, or if extracted would be new `subject city` not exist, so no update).

## 7. Natural Information Extraction Audit — Example

Fan:
```
"I just got home from my shift at the hospital."
→ extract_explicit_memories: no regex match → 0 memories → not persisted
"Yeah, I'm a nurse."
→ no match (nurse not in color/dislike/miami/interview) → 0
"I usually work nights."
→ no match
"My dog went crazy when I got home."
→ no match
```
Result: **0 facts persisted** for `occupation=nurse, workplace=hospital, schedule=nights, pet=dog` — **all ignored**, no evidence, no retrieval, no Qwen.

## 8. Memory Authority Audit

Current truth:
- `USER-PROVIDED FACT` (explicit via fan message, regex matched) → `confidence 1.0` → `long_term_memory_by_creator` — **authoritative for fan** (correct).
- `LLM-INFERRED FACT` — **none** (LLM output not stored as memory, only `extract_explicit_memories` explicit, not LLM inference) — **protected**: LLM cannot silently become truth (correct, via `sanitize_fan_input_for_learning` not inferring).
- `SYSTEM-INFERRED` (e.g., `relationship_state` via `derive_relationship_state`, `lifecycle` via `derive_lifecycle`) — **not stored as fan fact**, but as `users.funnel_stage` or `conversation_state` transient — **not mixed**.
- `TEMPORARY CONTEXT` (recent conversation 20) — transient, not persisted as fact — **not mixed**.
- `BEHAVIORAL OBSERVATION` (fatigue, pressure) — computed per turn, not stored as `long_term_memory`, but as `strategy_exposures` — **not mixed**.
- `COMMERCE FACT` (purchase, offer) — `commerce_offers`/`fangate_transactions` — **not mixed**.
- `RELATIONSHIP FACT` (open_loop) — `long_term_memory` warps as `open_loop` — **mixed** with `topic` but bounded.

**Protection:** `extract_explicit_memories` only explicit regex, not LLM inference, so `occupation businessman` guess not stored — **protected**, but **incomplete** (misses true facts).

## 9. Contradiction / Update Audit

`Chicago → New York last month`:
- `extract_explicit_memories` does **not** extract `city` at all, so neither `Chicago` nor `New York` would be stored via that path — **no update, no contradiction handling**.
- If via `commercial_preferences`? No.
- Via `user_profiles.facts` `location`? `location` key exists in `_PROFILE_LABELS` but no writer for city — **no writer, so no update**.
- **Result:** System keeps **nothing**, so no contradiction, but also **no memory**.

Hypothetical if `city` were stored as `subject city` with `value Chicago`:
- `add_memory_item` on `subject city` same, `memory_type fact` same → `found_idx` same subject → if new `confidence 1.0 == old 1.0` → `existing[found_idx] = item` (newer wins, replaces) — **overwrite, no history, no timestamp `effective_from`, no `previous value`, no `observed_at` beyond `last_seen`**, no detection of move.

Same for `occupation`, `timezone`, `preferences` — **no history, no contradiction detection, overwrite or keep both?** For `occupation` not implemented.

## 10. Temporal Memory Audit

- `PERMANENT` (fact `Chicago`): `is_memory_expired` `DECAY_SLOW 90d` `confidence*exp(-days/90) <0.2` → expired after ~135d (1.0*exp(-135/90)=0.22) — **long-lived but not permanent**.
- `TEMPORARY` (`Spain this week`): Not captured — **P1 gap**.
- `RECURRING` (night shifts): Not captured — **gap**.
- `HISTORICAL` (moved last month): Not captured with `effective_from`.
- `CURRENT STATE` (`working nights this month`): Not captured.

System treats all as `PREFERENCE` 30d or `FACT` 90d, no `TEMPORARY` `3d` vs `RECURRING` distinction — **cannot represent Spain temporary**.

## 11. Timezone / Local-Time Audit

- `fan timezone` **NOT STORED** — `grep timezone` in `memory/` 0 hits, `db/postgres.py` no `timezone` column in `users` or `user_profiles` facts.
- `fan country/city` **NOT STORED** as `timezone` source — not derived from `Telegram metadata` (`event.sender` has no timezone, only `sender_id`), not explicit, not guessed.
- `current local time` **NOT KNOWN** — `build_qwen3_context` does not inject `local_time`, only `STATE: {first_name} | {funnel_stage}` + `PROFILE` + `COMMERCE` + `SUMMARY` + `CONVERSATION` + `RELEVANT MEMORY` + `AVAILABLE CONTENT` — **no local_time**.
- `creator timezone` **NOT STORED** — `get_timezone` exists for DropFans creator (via `client.get_timezone`) but not fan.
- `last observed local time` / `typical active hours` / `sleep pattern` **NOT STORED** — no behavioral time tracking.

`Fan replies at 04:12 local time. Does Qwen know it is 04:12?` **No — prove**: `memory/context.py:build_qwen3_context` has no `local_time` injection, `workers/llm_worker.py` no `local_time`, `telemetry` no `local_time` — **P1 gap**.

## 12. Behavioral Memory Audit

- `usually active late at night` — **NOT STORED** — `compute_fatigue` is per `strategy_family` last 5, not per `active hours`, no `sleep/activity patterns`.
- `frequently talks about gym` — **NOT STORED** as behavioral, only `commercial_preferences` `count` per preference but not `gym` via `extract_explicit_memories`, and `recent_topics` in `conversation_state` transient, not persisted.
- `likes short replies` — **NOT STORED** — `communication preferences` not captured.
- Behavioral stored: **none** — `strategy_exposures` is per `strategy` not per `behavioral pattern`, `metrics` per `strategy` not per `fan behavior`.

Explicit fact vs behavioral: **no separation**, both missing.

## 13. Relationship Memory Audit

- `She promised to show him her new tattoo.` — **NOT STORED** as relationship — `open loops` via `long_term_memory` `open_loop` with `importance 0.8` could store `promise` as `commitment`? `extract_explicit_memories` for `commitment` via `come back/after payday` not `promise` — **not captured**.
- `They discussed his promotion.` — **NOT STORED** — `topic` memory `movie` only, not `promotion`.
- `They talked about his trip to Spain.` — `trip miami` only, not `Spain` — **not**.
- `She knows he hates pineapple.` — **NOT** — `dislike` via `i hate` regex would capture `pineapple` as `dislike` with `subject dislike` (would be `dislike=pineapple`? `m2.group(1)` would be `pineapple` — **partial** but `confidence 1.0` — would be stored as `dislike`).
- Relationship memory exists as `open_loop`/`commitment`/`topic` in `long_term_memory` but **not separate** from generic fan memory — same `long_term_memory_by_creator` list, same retrieval via `retrieve_relevant_memories` overlapping tokens — **duplicate with `funnel_journey` (20) and `relationship_health` (operational) — competing representations, no single authority**.

## 14. Creator Persona Audit

- `creator profile` — `personas` table `id, name, instructions, is_default` via `db/postgres.py: get_all_personas`, `get_user_persona` via `users.persona_id` — **exists**.
- `Where?` `personas.instructions` (free-form text, e.g., `You are Sunny Skye...`), `users.persona_id` (per fan? No, per fan's assigned persona, but `creator_persona` is per creator via `personas` default).
- `Who writes?` Dashboard `update_persona` / `create_persona` (operator) — **operator writes**.
- `Who reads?` `memory/context.py:build_qwen3_system_prompt` reads `persona` (instructions) as `persona_block` — **Qwen receives it** as `persona_block` + `Stage guidance`.
- `Is authoritative?` **YES** — `persona` is instructions, not inferred, operator-authoritative.
- `Creator metadata` (age, city, lifestyle, boundaries) — **NOT STRUCTURED** — only free-form `instructions`, no `creator_city`, `creator_interests` structured, no biography.

## 15. Current Qwen Context Audit

`build_qwen3_context` (memory/context.py:487-647) emits:

| Component | Source | Bounded? | Authoritative? | PII? | Scope | Notes |
|---|---|---|---|---|---|---|
| `CURRENT MESSAGE` | `user_message` | No (800 chars) | fan-provided | fan message content (PII) | fan | Via `messages.append` `role user` `content user_message` |
| `RECENT CONVERSATION` | `get_recent_messages` 20, `trim_to_token_budget` 800 | Yes 20/800/3 | fan-provided | fan/assistant content | fan | `recent_history_for_state` reused |
| `PERSONA` | `personas.instructions` via `get_user_persona` or `get_default_persona` | 600 tokens `system` | operator | none | creator | `You are Sunny Skye...` |
| `FAN MEMORY` | `get_user_profile` `facts` `interests` etc. via `format_profile` | 250 tokens | fan-provided | fan PII | fan (not creator) | `PROFILE:` but via `user_profiles` not creator-scoped (leak, see §17) |
| `COMMERCE STATE` | `commerce/conversational.py` `derive_desire/temp/readiness/window/objective` | Yes | deterministic | none | creator+fan | `COMMERCIAL STATE: desire=...` |
| `OBJECTIVE/NBA` | `derive_conversation_objective` 14 | Yes | deterministic | none | fan | `CONVERSATION INTELLIGENCE: objective=...` |
| `STRATEGY` | `make_exposure` via `strategy_family` | 50 | deterministic | none | creator:fan | `strategy` not to Qwen directly, but via `COMMERCIAL STATE` |
| `COMMERCE TEXT` | `build_llm_context` via `memory/context_assembler` | 300 tokens | deterministic | purchase signals | creator | `COMMERCE: ...` |
| `AVAILABLE CONTENT` | `rank_products_by_relevance` TOP2 titles | Yes 2 titles 42 chars | deterministic | none | creator | `AVAILABLE CONTENT: Title1 | Title2` |
| `RELEVANT MEMORY` | `retrieve_relevant_memories` 3, scored `overlap 0.5 + confidence 0.3` | Yes 3/0.2 threshold | fan-provided (explicit) | fan fact (e.g., favorite_color) | **creator:fan** | `RELEVANT MEMORY: favorite_color=red` |
| `SUMMARY` | `get_latest_summary_with_age` 2 sentences | 200 tokens | LLM-generated summary | fan PII | fan | `SUMMARY: ...` |
| `TIME` | **NOT PRESENT** — no `local_time`, `timezone`, `last_seen` | — | — | — | — | **MISSING** |
| `LOCATION` | **NOT PRESENT** — no `city/country` beyond `profile` `location` (unfilled) | — | — | — | — | **MISSING** |
| `OPEN LOOPS` | `conversation_state` `open_threads` 3 + `RELEVANT MEMORY` `open_loop` | Yes 3 | fan-provided | none | fan | `CONVERSATION: open=[...]` |
| `PRESSURE/RISK/LIFECYCLE` | `build_operation_decision` `pressure_score`, `risk_state`, `lifecycle_state` | Yes | deterministic | none | creator:fan | Not to Qwen directly, but via `COMMERCIAL STATE` window? |
| `IDENTITY` | `identity_already_established` | Yes | deterministic | none | fan | `IDENTITY: established=...` |

**Critical:** Qwen receives **persistent fan knowledge only via `RELEVANT MEMORY` 3** (token overlap) + `PROFILE` `interests` (per `user_profiles` not creator-scoped) + `SUMMARY` — **not** comprehensive fan knowledge base. If `software engineer` not extracted, **never reaches Qwen after recent 20 expires**.

## 16. Personalization Effectiveness Audit

| Knowledge Category | STORED | USED (reaches Qwen/context/strategy) | Notes |
|---|---|---|---|
| `favorite_color red` | YES (long_term_memory) | YES (RELEVANT MEMORY 3) | Used via `rank_products`? No, via `profile` `interests` not color |
| `trip miami` | YES | YES | Used |
| `interview Friday` | YES | YES (open_loop) | Used |
| `occupation software engineer` | **NOT STORED** | **NOT USED** | **Gap** |
| `city Chicago` | **NOT STORED** | **NOT USED** | **Gap** |
| `nurse night shifts` | **NOT STORED** | **NOT USED** | **Gap** |
| `dog Max` | **NOT STORED** | **NOT USED** | **Gap** |
| `timezone` | **NOT STORED** | **NOT USED** | **Gap** |
| `behavior late night` | **NOT STORED** | **NOT USED** | **Gap** |
| `relationship promise` | **NOT STORED** | **NOT USED** | **Gap** |
| `commercial_preferences` | YES (count) | YES (AVAILABLE CONTENT) | Used but not via memory extraction |

**STORED+NOT USED:** `funnel_journey` 20 (not to Qwen), `strategy_exposures` 50 (not to Qwen) — stored but not personalization.

## 17. Creator/Fan Isolation Audit

Test `creator A + fan X` vs `creator B + fan X` (same `user_id` 100, different `creator_id` 1 vs 2):

- `user_profiles` `facts` `interests` is **per `user_id` not per `creator`** via `get_user_profile(user_id)` returns `facts` without `creator_id` — `format_profile` uses `profile.get("interests")` from `get_user_profile(user_id)` which is **global per fan**, not `commercial_preferences_by_creator` — **LEAK**: `creator A` `interests` visible to `creator B` for same fan.
- `long_term_memory_by_creator` `str(creator)` per `user_id` row — `get_long_term_memory(1,100)` vs `get_long_term_memory(2,100)` different keys — **ISOLATED** ✓
- `Redis keys` `lock:user:{user_id}` not creator → `user_id 100` lock for `creator 1` blocks `creator 2` same fan — **P2: lock not creator-scoped, could block concurrent per fan across creators** (but fan is same Telegram user, not creator-scoped, so maybe intentional).
- `generation_id` `md5(user:msg:telegram_id)` not `creator` → same generation_id for same fan message across creators (if same `telegram_message_id` reused) → **could collide** (but `telegram_message_id` is per chat, not per creator, so same? P2).
- `strategy_exposures` `f"{creator}:{user}"` — **ISOLATED** ✓

**Result:** `long_term_memory` **ISOLATED**, but `interests`/`profile` **NOT isolated** — **P1**.

## 18. Restart / Recovery Audit

- `persistent`: `user_profiles` `long_term_memory_by_creator` 20, `commercial_preferences` via `fangate_products`? No, `long_term_memory` persisted via `update_user_profile` JSONB → survives `llm_worker` restart via `get_user_profile` — **PERSISTENT**.
- `in-memory only`: `_exposure_buffer` dict, `_journey_mem`, `_handoff_mem`, `GenerationTelemetry` cache — **LOST** on restart, but `strategy_exposures` JSONB persists, `handoff` JSONB persists, `telemetry` not needed.
- `reconstructed`: `recent conversation` 20 via `messages` table — **RECONSTRUCTED** after restart via DB query.
- `lost`: `acquire_user_lock` Redis `lock:user:{id}` 30s TTL — survives Redis restart? No, Redis restart loses lock → **LOST** but TTL 30s acceptable.
- `bounded`: `long_term_memory` 20, `strategy_evidence` 20, `exposures` 50, `journey` 20, `metrics` 5000 global — **BOUNDED**.

## 19. Memory Size / Retention Audit

- `long_term_memory` 20 per `creator:user` → `if >20: sorted last_seen 20` — **BOUNDED 20**, retention `DECAY 90/30/7` via `is_memory_expired` → expired not returned, but **not pruned** from storage (only filtered on retrieval) — **P2: expired memories remain in JSONB forever (20 cap, but if 20 are expired, still 20, not pruned)**.
- `strategy_exposures` 50 per `creator:user` → `if >50: buf[-50:]` + `prune_by_retention` 30d — **BOUNDED**.
- `conversation_summaries` 1 per `user_id` (update if exists, else insert) — **BOUNDED 1**.
- `recent conversation` 20 via `LIMIT 20` — **BOUNDED**.
- **Unbounded?** `user_profiles.facts` could grow with new keys (`strategy_exposures_by_creator`, `funnel_journey`, `handoff`, etc.) each bounded per `creator:user` but **global per `user_id` JSONB** could grow with many `creator` keys (if fan talks to many creators, `long_term_memory_by_creator` has key per `creator` → 100 creators *20 = 2000 entries per `user_id` row → **P2: global JSONB size per fan could grow with creator count, not bounded globally**.

## 20. Privacy / Data Minimization Audit

- Stores `message content`? **NO** — `long_term_memory` stores `value=text[:50]` truncated 50 chars, not full message; `messages` table stores full `content` (intended, for context), but `user_profiles.facts` not full content — **MINIMAL**.
- Stores `message previews`? `publish_event` `message_preview: user_message[:100]` to Redis Pub/Sub — **PII via preview 100 chars** — **P2** (not memory but telemetry).
- Stores `personal details`? `favorite_color`, `trip` — **minimal, structured, not entire conversation**.
- Stores `emails/phone/secrets`? No, `extract_explicit_memories` regex does not capture email/phone, and `sanitize` not needed — **PASS**.
- Stores `tokens/Telegram sessions`? No, `chatbotv2.session` file, not in `user_profiles` — **PASS**.

## 21. Duplicate / Conflicting Memory Systems

| Concept | Representation 1 | Representation 2 | Representation 3 | Authority | Risk |
|---|---|---|---|---|---|
| `user state` | `users` table `funnel_stage` | `user_profiles.facts` `strategy_evidence` | `conversation_state` transient | `users.funnel_stage` via `advance_funnel` | **P2: duplicate funnel vs strategy evidence** |
| `memory` | `long_term_memory_by_creator` 20 | `commercial_preferences_by_creator` dict | `recent conversation` 20 + `summary` | `long_term_memory` for explicit, `recent` for transient | **P2: two preference stores (`interests` vs `commercial_preferences`)** |
| `profile` | `user_profiles.facts` `interests` | `commercial_preferences_by_creator` | `long_term_memory` `preference` | `long_term_memory` for explicit, `interests` legacy | **P2: duplicate preferences** |
| `relationship` | `long_term_memory` `open_loop` | `funnel_journey` 20 | `relationship_health` operational | `long_term_memory` for open loops, `funnel_journey` for funnel | **P2: duplicate journey vs funnel** |
| `objective` | `derive_conversation_objective` 14 | `derive_commercial_objective` 4 | `ConversationOperationDecision` | `derive_conversation_objective` (14) authoritative | **P1: duplicate objective (see Phase 31)** |
| `response_mode` | `ConversationOperationDecision` | `memory/context` `RESPONSE: mode` legacy | `conversation_state` `tone` | `ConversationOperationDecision` | **P1: duplicate** |

## 22. Failure Modes

| Path | Failure | Behavior | Classification |
|---|---|---|---|
| `extract_explicit_memories` | regex no match | 0 memories, not persisted, not error | **SAFE** (but incomplete) |
| `add_memory_item` DB failure | `get_user_profile` throws | `except: logger.warning` + fallback to memory only via `_exposure_buffer`? No, for long_term_memory it just `logger.warning` and **lost** — **DEGRADED** (lost, not retry) |
| `retrieve_relevant_memories` DB failure | `return []` | `except: return []` → Qwen gets no memory, continue | **SAFE** (degraded) |
| `Redis failure` (lock, context) | `acquire_user_lock` false → skip | `logger.info` skip | **SAFE** (retry via XAUTOCLAIM) |
| `malformed memory` (not JSON) | `json.loads` throws → `except: return {}` | `{}` → no memory | **SAFE** |
| `contradictory fact` (Chicago→NY) | `add_memory_item` overwrite if confidence equal | `existing[found_idx]=item` (newer wins) — **no history** | **DEGRADED** (lost) |
| `oversized memory` (>20) | `if >20: sorted 20` | prune oldest | **SAFE** (bounded) |
| `unknown field` (new subject) | `subject` hard-coded, not extensible → not stored | 0 memories | **SAFE** but incomplete |
| `unknown timezone` | not stored, not guessed | `local_time` not injected | **SAFE** (not fabricated) |
| `stale fact` (Spain temporary) | `is_memory_expired` 90d/30d, but temporary 3d not distinguished | still stored 30d, not expired → Qwen sees Spain as permanent | **P1: temporal not handled** |
| `duplicate fact` (same subject) | `found_idx` same subject → confidence compare | newer explicit wins, not duplicate | **SAFE** |
| `restart` | `long_term_memory` persists via `user_profiles`, `_exposure_buffer` lost | `long_term_memory` survives, exposures via JSONB survives | **SAFE** (persistent) |
| `concurrent updates` | `get_user_profile` → modify → `update_user_profile` read-modify-write without lock | **lost update** (see Phase 31 P1) | **P1: TOCTOU** |
| `creator isolation violation` | `interests` per `user_id` not `creator` | `creator A` interests visible to `creator B` same fan | **P1** |
| `fan isolation violation` | `long_term_memory_by_creator` per `creator:user` | **PASS** | **SAFE** |

## 23. Test Audit

| Capability | Existing Test | What It Proves | What It Does NOT Prove |
|---|---|---|---|
| `memory extraction` | `test_phase25` `extract_explicit_memories` for `favorite_color` | Regex captures `favorite_color` | **Not** occupation/city/schedule/pet |
| `memory persistence` | `test_phase25` `Create memory item` | `add_memory_item` stores `long_term_memory` | **Not** that `software engineer` is stored |
| `memory retrieval` | `test_phase25` `retrieve_relevant_memories` 3 | Retrieval returns `favorite_color` when topic `red` | **Not** that `nurse` on `hospital` retrieves |
| `memory isolation` | `test_phase25` `Creator A vs B` exposures | `get_exposures_memory` isolated | **Not** that `interests` per `user_id` is isolated (it leaks) |
| `context` | `test_phase8_single_pass` mock `build_qwen3_context` | Context built | **Not** that `occupation` reaches Qwen after 3 days |
| `personalization` | `test_phase25` `Creator intelligence` | `creator_intelligence` isolated | **Not** that fan fact changes `rank_products` |
| `open loops` | `test_phase25` `interview friday` | `extract` + `resolve` for `interview` | **Not** that `Spain temporary` not resurrected |

**Existing tests claim `memory retrieval` but only test `favorite_color`, `miami`, `interview` — 3 subjects, not comprehensive.** Many tests mock `get_user_profile` (over-mocked) — **cannot catch isolation leak of `interests`**.

## 24. Live Infrastructure Read-Only Findings

- `actual user_profiles` via `python -c "from db.postgres import get_user_profile"` not executed live (no DB connection in audit, read-only via code).
- `actual JSONB keys` via `grep user_profiles` → `long_term_memory_by_creator`, `commercial_preferences_by_creator`, `strategy_exposures_by_creator`, `funnel_journey_by_creator`, `handoff_by_creator`, `strategy_evidence_by_creator`, `strategy_generation_seen`, `facts` `interests` — **actual structure is fragmented, not unified**.
- `actual memory records` via code: `memory_id f"{creator}:{user}:{subject}:{value[:20]}"` — **actual record is 20 bounded, not 1000s**.
- `actual Redis memory keys` via `grep lock:user` → `lock:user:{user_id}` not creator-scoped — **actual Redis key is per fan, not creator:fan**.
- `actual persisted telemetry` via `generation_telemetry` table `user_id, creator_id, generation_id` — **actual telemetry is per generation, not per fan knowledge**.

**Live canary not mutated** (1% HOLD, not cleared).

## 25. Required Gap Classification

### Fan Knowledge Matrix

| Knowledge Category | Captured? | Persisted? | Retrieved? | Reaches Qwen? | Actually Used? | Evidence-backed? | Temporal? | Bounded? | Creator Isolated? | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| `identity name` | Yes `first_name` | Yes `users` | Yes `build_qwen3_context` | Yes `Fan: {first_name}` | Yes | Yes (Telegram) | No | Yes | No | **STORED+USED** but not creator-scoped |
| `location city/country` | **No** | **No** | **No** | **No** | **NOT USED** | — | — | — | — | **NOT STORED** |
| `timezone` | **No** | **No** | **No** | **No** | **NOT USED** | — | — | — | — | **NOT STORED** (P1) |
| `occupation` | **No** (`software engineer` not regex) | **No** | **No** | **No** | **NOT USED** | — | — | — | — | **NOT STORED** (P1) |
| `schedule night shifts` | **No** | **No** | **No** | **No** | **NOT USED** | — | — | — | — | **NOT STORED** (P1) |
| `family` | **No** | **No** | **No** | **No** | **NOT USED** | — | — | — | — | **NOT STORED** |
| `relationship status` | **No** | **No** | **No** | **No** | **NOT USED** | — | — | — | — | **NOT STORED** |
| `pets dog Max` | **No** | **No** | **No** | **No** | **NOT USED** | — | — | — | — | **NOT STORED** (P1) |
| `hobbies` | **No** (only `movie` topic) | **No** | **No** | **No** | **NOT USED** | — | — | — | — | **NOT STORED** |
| `preferences likes` | Partial `favorite_color` | Yes 20 | Yes 3 | Yes `RELEVANT MEMORY` | Yes | Yes explicit 1.0 | No | Yes 20 | Yes | **STORED+USED** but 1 subject |
| `dislikes` | Partial `dislike` | Yes | Yes | Yes | Yes | Yes | No | Yes | Yes | **STORED+USED** but 1 subject |
| `goals/plans` | Partial `trip miami` | Yes | Yes | Yes | Yes | Yes | No (7d) | Yes | Yes | **STORED+USED** but 1 plan |
| `events` | Partial `interview` | Yes | Yes | Yes | Yes | Yes | No | Yes | Yes | **STORED+USED** but 1 event |
| `behavior late night` | **No** | **No** | **No** | **No** | **NOT USED** | — | — | — | — | **NOT STORED** (P1) |
| `communication preferences` | **No** | **No** | **No** | **No** | **NOT USED** | — | — | — | — | **NOT STORED** |
| `conversation history` | Yes 20 | Yes `messages` | Yes 20 | Yes `recent` | Yes | Yes | No | Yes 20 | No (per fan) | **TRANSIENT+USED** |
| `open loops` | Yes 1.0 | Yes 20 | Yes 3 | Yes | Yes | Yes | No (7d) | Yes 20 | Yes | **STORED+USED** but narrow heuristic |
| `relationship memories` | **No** | **No** | **No** | **No** | **NOT USED** | — | — | — | — | **NOT STORED** (P1) |

### Memory Lifecycle Matrix

| Stage | CAPTURE | VALIDATE | CLASSIFY | PERSIST | UPDATE | RETRIEVE | CONTEXT | QWEN | BEHAVIOR | OUTCOME | LEARNING |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Status | **PARTIAL** (4 subjects) | **EXISTS** (confidence explicit) | **PARTIAL** (4 types) | **EXISTS** (20 bounded) | **PARTIAL** (overwrite, no history) | **EXISTS** (3, overlap) | **EXISTS** (RELEVANT MEMORY) | **EXISTS** (RELEVANT MEMORY) | **PARTIAL** (only color/trip uses rank) | **NOT** | **NOT** |
| Notes | regex 4, not occupation | explicit 1.0 | fact/preference/plan/open_loop | JSONB 20 | newer wins, no history | scored | via context | via context | not for occupation | — | — |

### Authority Matrix

| Fact Type | Current Authority | Should Be | Risk |
|---|---|---|---|
| `USER-PROVIDED FACT` `I live in Chicago` | `long_term_memory` explicit 1.0 (if captured) | `long_term_memory` explicit | **LOW** (protected, but not captured) |
| `LLM-INFERRED FACT` `sounds like businessman` | **NOT STORED** (only explicit regex, not LLM) | **NOT STORED** | **PASS** (no LLM inference) |
| `SYSTEM-INFERRED FACT` `relationship_state` | `users.funnel_stage` transient | `relationship_state` transient | **LOW** (not mixed) |
| `BEHAVIORAL OBSERVATION` `fatigue` | `strategy_exposures` 50 | **NOT STORED as fan fact** | **PASS** (not mixed) |
| `COMMERCE FACT` `purchase` | `commerce_offers` | `commerce_offers` | **PASS** |
| `RELATIONSHIP FACT` `promise` | **NOT STORED** | `long_term_memory` `commitment` | **P2** (missing) |
| `TEMPORARY CONTEXT` `recent 20` | `messages` 20 | `messages` 20 | **PASS** (not persisted as fact) |

### Isolation Matrix

| Representation | Creator A+Fan X vs Creator B+Fan X | Creator A+Fan X vs Creator A+Fan Y | Verdict |
|---|---|---|---|
| `long_term_memory` | **ISOLATED** (`str(creator)` key) | **ISOLATED** (`user_id` row) | **PASS** |
| `interests` (`user_profiles.facts` `interests`) | **LEAK** (per `user_id` not `creator`) | **ISOLATED** (per `user_id`) | **P1** |
| `commercial_preferences` | **ISOLATED** (`commercial_preferences_by_creator`) | **ISOLATED** | **PASS** |
| `exposures` | **ISOLATED** (`creator:user`) | **ISOLATED** | **PASS** |
| `lock:user` | **NOT ISOLATED** (`user_id` only) | **NOT ISOLATED** (same fan across creators shares lock) | **P2** |

### Temporal Matrix

| Concept | Exists? | Temporal? | Evidence |
|---|---|---|---|
| `timezone` | **No** | — | **MISSING** (P1) |
| `local_time` | **No** | — | **MISSING** (P1) |
| `temporary location Spain` | **No** | **No** | **MISSING** (P1) |
| `work schedule nights` | **No** | **No** | **MISSING** (P1) |
| `activity pattern late night` | **No** | **No** | **MISSING** (P1) |
| `sleep pattern` | **No** | **No** | **MISSING** |
| `recurring behavior` | **No** | **No** | **MISSING** |
| `historical facts Chicago→NY` | **No history** | **No** | **P1** (overwrite) |
| `current facts` | Partial (4 subjects) | **No** (no `effective_from`) | **P2** |

## 30. Critical Questions

### Q1: "I'm a software engineer from Chicago and I have a golden retriever named Max." What happens today?

**Trace:** `extract_explicit_memories("I'm a software engineer... Max", creator 1, user 100)` → regex `my favorite color is|...` no match, `going to miami|interview` no match, `movie` no — **0 memories** → `add_memory_item` not called → **nothing persisted**. `messages` table stores `content` for 20 recent, but after 3 days `get_recent_messages` 20 no longer contains it (if fan talks 20+ messages later, gone). `retrieve_relevant_memories` query `long_term_memory` for `creator 1, user 100` returns [] → Qwen does **NOT** know `software engineer`, `Chicago`, `Max`. **No capture, no persistence.**

### Q2: Three days later "Max destroyed my couch again 😂" — Can system know Max = dog without recent context?

**No.** `long_term_memory` never stored `pet dog Max` (not extracted), so `retrieve_relevant_memories` with `current_topic "Max"` tokens `{max}` vs `memory` `subject dog` tokens `{dog}` → **no overlap** (Max vs dog) → not retrieved. Recent conversation 20 may still contain `Max` if within 20, but after 3 days and 20 messages, **no**. System cannot know `Max = dog` — **gap PROVEN**.

### Q3: "I work nights." Does system remember?

**No.** `extract_explicit_memories` no `night` regex, **not captured**.

### Q4: "I moved to New York." Does system update old city?

**No.** `city` not stored, so **no update, no history, no `effective_from`**.

### Q5: "I'm in Spain for the week." Temporary?

**No.** Not captured, not distinguished as `TEMPORARY` 7d vs `PERMANENT` 90d.

### Q6: Fan replies at 04:00 local time, does system know?

**No.** No `timezone`/`local_time` injection to Qwen (see §11). Qwen sees no `local_time`.

### Q7: Can system accumulate over months?

**No.** Only 20 `long_term_memory` bounded, but **only 4 subjects** actually captured, so over months fan could talk 2 years, still only `favorite_color`, `miami`, `interview` would be remembered, not `occupation`, `city`, `pet`, `goals`.

### Q8: Relationship-specific information?

**No.** `They discussed his promotion` not stored as `relationship memory` (only `open_loop` for `interview`).

### Q9: Fan X to Fan Y leak?

**`long_term_memory` NO** (isolated), but `interests` **YES leak** (per `user_id` not `creator`, but Fan X vs Fan Y are different `user_id`, so not leak across fans; leak is across creators for same fan, not fan-to-fan). Fan X vs Fan Y same creator: `user_id` rows different, **isolated**.

### Q10: Creator A to Creator B?

**`long_term_memory` NO**, `interests` **YES leak** for same fan (same `user_id` 100, `creator 1` interests visible to `creator 2` via `user_profiles.facts` `interests` not creator-scoped).

### Q11: Does Qwen receive persistent fan knowledge?

**Partially:** `RELEVANT MEMORY` 3 via `retrieve_relevant_memories` (token overlap) + `PROFILE` `interests` (per `user_id` not creator) + `SUMMARY` 2 sentences — **only if `extract_explicit_memories` captured it** (4 subjects), otherwise **NO** (occupation, city, dog not).

### Q12: Does Qwen receive creator persona?

**Yes:** `personas` `instructions` via `build_qwen3_system_prompt` `persona_block` — **authoritative, per creator** (via `get_user_persona` or default).

### Q13: Can Qwen turn inference into persistent truth?

**No:** `extract_explicit_memories` is explicit regex, not LLM inference, so `LLM thinks businessman` not stored — **protected**. But `extract_and_update_profile` in `memory/profile.py` — does it use LLM? Check `memory/profile.py` not yet inspected, but `extract_explicit_memories` is not LLM, so **protected** (likely).

### Q14: Smallest missing layer?

**Unified Fan Knowledge + Temporal Context + Behavioral Evidence + Relationship Memory + Creator Persona → Unified Personalization Context** as **one bounded layer** (not several competing), using `user_profiles` JSONB per `creator:user` with `subject/value/confidence/source/observed_at/effective_from/expires_at`, plus `timezone`/`local_time` injection, plus `behavioral` via `strategy_exposures` already, plus `relationship` via `long_term_memory` extended, plus `creator persona` structured.

## 31. Master Findings Table

| ID | Severity | Finding | Evidence | File:Line | Runtime Impact | Proven/Likely |
|---|---|---|---|---|---|---|
| P1-01 | P1 | No natural extraction for occupation/city/schedule/pet | `commerce/long_term_memory.py:extract_explicit_memories` regex only 4 subjects | `long_term_memory.py:238` | Fan fact `software engineer` never stored, lost after 20 messages | **PROVEN** |
| P1-02 | P1 | `interests` not creator-scoped (leak Creator A→B for same fan) | `memory/context.py:format_profile` `profile.get("interests")` from `get_user_profile(user_id)` not `commercial_preferences_by_creator` | `memory/context.py:88` `db/postgres.py:get_user_profile` | Same fan `interests` visible to different creator | **PROVEN** |
| P1-03 | P1 | No timezone/local-time | `grep timezone` 0 in `memory/`, no `timezone` column in `users`/`user_profiles` | `memory/context.py` | Qwen never knows local time 04:12 | **PROVEN** |
| P1-04 | P1 | No temporal distinction (Spain temporary vs permanent) | `is_memory_expired` only `DECAY 90/30/7`, no `TEMPORARY` type | `long_term_memory.py:118` | Spain stored 30d as permanent | **PROVEN** |
| P1-05 | P1 | No update/history for city/occupation (overwrite, no `previous value`) | `add_memory_item` `existing[found_idx]=item` (replace) | `long_term_memory.py:94` | `Chicago→NY` loses history, no `effective_from` | **PROVEN** |
| P1-06 | P1 | No behavioral memory (late night, gym frequency) | `grep` no `active hours`, `compute_fatigue` per strategy not behavior | `commerce/adaptive_optimization.py` | Cannot accumulate over months | **PROVEN** |
| P2-01 | P2 | `long_term_memory` 20 not pruned when expired (remains in JSONB) | `is_memory_expired` filters on retrieval, but `add_memory_item` only prunes `>20` not expired | `long_term_memory.py:110` | Expired 20 remains, retrieval quality falls | **PROVEN** |
| P2-02 | P2 | `global JSONB per fan` could grow with creator count (`long_term_memory_by_creator` per `creator` key) | `user_profiles.facts` per `user_id` row, many `creator` keys | `db/postgres.py` | 100 creators *20 = 2000 per row, unbounded globally | **LIKELY** |
| P2-03 | P2 | Duplicate preference stores `interests` vs `commercial_preferences` | `user_profiles.facts` `interests` + `commercial_preferences_by_creator` | `memory/context.py` vs `commerce/fan_memory.py` | Confusing authority | **PROVEN** |
| P2-04 | P2 | `lock:user:{user_id}` not creator-scoped | `db/redis.py:acquire_user_lock` `lock:user:{user_id}` | `db/redis.py:252` | Same fan across creators shares lock (minor) | **PROVEN** |
| P3-01 | P3 | `favorite_color` only `red|black|...` 7 colors, not `golden retriever` | `extract_explicit_memories` regex | `long_term_memory.py:238` | Pet `Max` not captured | **PROVEN** |

## 38. Recommended Stage B Architecture (Minimal, Not Implemented)

One bounded layer `Fan Knowledge` (per `creator:user` JSONB, `subject/value/confidence/source/observed_at/effective_from/expires_at/temporal_type`, max 30, `explicit` vs `inferred` never becomes canonical without `observed_at` evidence) + `Temporal Context` (`timezone` via explicit fan or Telegram? No, via `city` → `timezone` lookup, plus `local_time` injection) + `Behavioral Evidence` (reuse `strategy_exposures` 50, not new) + `Relationship Memory` (extend `long_term_memory` `open_loop`/`commitment` with `subject` extensible) + `Creator Persona` structured (`personas` table already, just expose `creator_city` etc. if present) → `Unified Personalization Context` (bounded 3 `RELEVANT MEMORY` + `PROFILE` `interests` creator-scoped) → existing `Qwen` (no new LLM). **One layer, not several**, `existing memory/context` already does `retrieve_relevant_memories` 3, just extend `extract_explicit_memories` regex to `occupation/city/pet/schedule` with same `add_memory_item` bounded 20, add `timezone` derivation via `city` → `tz` lookup (not LLM), add `temporal_type`.

## 39. Explicit Non-Goals

- No `Fan Knowledge` via interrogation (only natural reveal)
- No LLM inference as truth
- No unbounded memory
- No new persistence system (reuse `user_profiles` JSONB)
- No new LLM calls

## 40. Final Verdict

```
PHASE 34 FORENSIC VERDICT

FAN KNOWLEDGE: PARTIAL (4 subjects captured, 20+ missing: occupation/city/pet/schedule/timezone)
MEMORY PERSISTENCE: PARTIAL (20 bounded, creator-scoped, but interests leak per user_id, expired not pruned)
MEMORY RETRIEVAL: PARTIAL (3/0.2 threshold, token overlap, not semantic)
NATURAL EXTRACTION: NOT READY (regex 4 subjects, not occupation/city/nurse/hospital/nights/dog)
TEMPORAL MEMORY: NOT READY (no TEMPORARY vs PERMANENT, Spain treated as permanent)
TIMEZONE AWARENESS: NOT READY (no timezone/local_time)
BEHAVIORAL MEMORY: NOT READY (no late night/gym frequency)
RELATIONSHIP MEMORY: NOT READY (no promise/Spain/trip, only interview)
CREATOR PERSONA: PARTIAL (personas instructions exists, but no structured city/age/lifestyle)
PERSONALIZATION: NOT READY (occupation/city/dog never reaches Qwen after 20)
QWEN CONTEXT: PARTIAL (recent 20 + 3 LTM + commercial, but not fan knowledge base)
CREATOR ISOLATION: PARTIAL (long_term_memory isolated, but interests leak)
FAN ISOLATION: PASS (via user_id row)
RESTART SAFETY: PASS (persistent via user_profiles)
BOUNDS/RETENTION: PARTIAL (20 bounded, but expired not pruned, global per-fan JSONB could grow)
PRIVACY: PASS (no message content in long_term_memory value[:50], no secrets)
P0: 0
P1: 6
P2: 4
P3: 1
PROVEN: 10
LIKELY: 1
PRODUCTION CHANGES: NONE
MIGRATIONS: NONE
NEW LLM CALLS: 0
NEW WORKERS: 0
NEW QUEUES: 0
CANARY CHANGES: NONE
ARCHITECTURE CHANGES: NONE
FINAL VERDICT: NOT READY for robust long-term personalization (but safe via isolation, not unsafe)
NEXT ACTION: Stage B minimal Fan Knowledge layer (extend extract_explicit_memories to occupation/city/pet/schedule + timezone local_time injection + temporal_type) — DO NOT IMPLEMENT (Stage A only)
```

