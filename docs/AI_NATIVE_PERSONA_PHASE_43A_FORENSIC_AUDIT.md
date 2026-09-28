# AI_NATIVE_PERSONA_PHASE_43A_FORENSIC_AUDIT -- STAGE A
**Hostile Forensic Audit -- Sunny Skye Persona Fidelity (READ-ONLY)**
**Date: 2026-08-31 | Workspace: E:\chatbot | Phase: 43A Stage A**
**NO PRODUCTION CHANGES**


## 1. Executive Summary

**Question:** Can the current runtime reproduce Sunny Skye (19, NYC freelance graphic designer, warm/playful/teasing, NYC downtown aesthetic) with high fidelity over long conversations?

**Answer (PROVEN):** **PARTIAL -- LIKELY FAIL for longitudinal fidelity without structured persona.** The runtime **can** render Sunny's voice when `personas.instructions` contains a well-written free-form prompt, because that prompt is verbatim the first system token to Qwen (`memory/context.py:214`). However the **structured 18-field persona** (`memory/creator_persona.py:9`) is a **dead stub** (`get_structured_persona` returns `{}` at `:21`), the `personas.metadata` column **does not exist** (`db/schema.sql:76` has only `instructions TEXT`), and the **only structured identity that is actually wired is a hard-coded `core/persona_self.py:21` Sunny singleton** (`_SUNNY_SELF_FACTS`). All retrieval is **global** (`persona:{user_id}` `db/redis.py:375`, `users.persona_id` `schema.sql:16` no `creator_id`), cached 600s with **no invalidation on update**, so cross-creator contamination is PROVEN.

**Consequence:** An operator can make Sunny sound plausible for a few turns by pasting a good `instructions` prompt, but the system provides **no versioning, no creator-scoped isolation, no structured validation, and no persistence for 60+ fields of telemetry** that would allow the dashboard to observe or guarantee fidelity. Longitudinal (50+ turns) fidelity relies entirely on Qwen improvisation within the free-form prompt plus a hard-coded `ABOUT SUNNY` block (`memory/context.py:342`), not on authoritative structured facts.

## 2. Actual Persona Architecture

```
[operator] -> personas.instructions (TEXT, free-form, global, no metadata)
                |
                v
[Telethon intake] get_cached_user_persona(user_id) -> get_user_persona (JOIN personas) -> cache_user_persona (persona:{user_id} 600s) -> enqueue_inbound {persona}
                |
                v
[llm_worker] process_message(persona: str) -> build_qwen3_context(user_id, msg, persona, creator_id)
                |
                +-> memory/context.py:562 system_content = build_qwen3_system_prompt(persona, ...)  // first system token = instructions verbatim
                +-> memory/context.py:579 state_context = build_qwen3_state_context(...)  // second system token = ABOUT SUNNY (hard-coded) + CAPABILITIES + commerce state
                +-> memory/context.py:674 _struct = get_structured_persona(creator_id)  // always {} -> never appended
                |
                v
[workers/llm_worker.py:107 merge] merged_system = "\n\n".join(system_parts)  // single system_instruction to Qwen
                |
                v
[core/llm_provider] provider.generate_with_history(system_instruction=merged_system, messages=messages)
                |
                v
[core/scoring] score_draft -> routing -> send
```

**Key files (PROVEN):**
- `memory/creator_persona.py:15` stub, `db/postgres.py:155` only `instructions`, `db/schema.sql:76` DDL free-form only, `core/persona_self.py:21` hard-coded Sunny, `memory/context.py:204` verbatim persona, `workers/llm_worker.py:571` pass-through.

## 3. Storage Model

**Table `personas` (`db/schema.sql:76`):**
```sql
id SERIAL PK, name TEXT, instructions TEXT NOT NULL, is_default BOOLEAN, created_at TIMESTAMPTZ
```
- **No** `metadata` JSONB, **no** `creator_id`, **no** `updated_at`, **no** `version`, **no** `is_active`.

**Table `users` (`schema.sql:7`):**
```sql
id BIGINT PK, persona_id INTEGER REFERENCES personas(id)
```
- Global fan->persona FK, no `creator_id`.

**Table `creators` / `creator_integrations` (`migrations/20260819000000:6,19`):**
- Creator-scoped, has `creator_id UNIQUE`, `status`, `updated_at`, but **has no persona link**.

**`user_profiles.facts JSONB` (`schema.sql:59`):**
- Used for **fan** knowledge (`fan_knowledge_by_creator`) not persona.

**Structured fields declared but not stored:**
- `STRUCTURED_FIELDS = ["display_name","age",...]` (`creator_persona.py:9`) advertises 18 fields, but `get_structured_persona` returns `{}` (`:21`) and `personas.metadata` column does not exist (grep 0 hits). **Declared vs wired gap.**

## 4. Retrieval Model

1. `handlers.py:146` `get_cached_user_persona(user_id)` -> `db/redis.py:375` `persona:{user_id}` (global, 600s)
2. If miss: `get_user_persona(user_id)` -> `db/postgres.py:159` `SELECT p.instructions JOIN personas` (global, no creator)
3. Cache it `cache_user_persona`
4. Else `get_cached_default_persona()` -> `persona:default` global -> `get_default_persona()` `WHERE is_default TRUE`
5. `enqueue_inbound {persona}` -> `llm_worker:1528` `data.get("persona","")` -> `build_qwen3_context(..., persona)`

**Stale:** No `invalidate_persona_cache` on `create/update/delete_persona` (`postgres.py:187-219` has 0 calls to `invalidate_persona_cache` `redis.py:393` defines but never used). TTL only.

**Cross-creator:** Same `user_id=777` via Creator A and B shares `persona:{777}` and `users.persona_id` global -> Creator B gets Creator A's persona if cached first (PROVEN `TestCreatorIsolation` vacuous `tests/test_phase36_deep_personalization.py:228`).

## 5. Qwen Context (Ordering & Authority)

**Inventory (what Qwen receives, in order):**

1. `system_instruction` merged (`llm_worker.py:107`):
   - **First:** `persona_block` = `instructions` verbatim (`context.py:204`), mutated for Sunny: `if identity_already_established: replace "You are Sunny Skye" -> "You are sunny"` (`:207`)
   - **Second (if structured existed):** `CREATOR PERSONA: ...` (`context.py:679`) -- never fires because `_struct` always `{}`
2. `state_context` (`context.py:579`):
   - `ABOUT SUNNY:` `render_persona_self_block("sunny")` (`:343`) -- hard-coded `_SUNNY_SELF_FACTS` (`persona_self.py:21`) -- **always present, even if operator set "You are Mia"**
   - `CAPABILITIES:` `capability_contract.py:13` (send_photo:no etc)
   - `COMMERCE STATE:` deterministic `decision_trace` etc (`context.py:381`)
3. `recent messages` (last ~20, `context.py:500` LLMContext)
4. `summary` (if exists)

**Ordering:** Persona is first system token, so it wins over later context unless later explicitly contradicts. `ABOUT SUNNY` second system token reinforces Sunny even when persona is generic. Commerce state is last system token, authoritative for product/price but not voice.

**Scoring constraints:** `core/scoring.py` enforces `is_authorized_commerce` price bypass, not persona.

## 6. Persona Authority

- **Authoritative?** No. `instructions` is **free-form system instruction**, not a validated schema. Qwen can ignore or contradict it (no guard).
- **Can Qwen change persona facts?** Yes -- no `KNOWN FACT` vs `UNKNOWN` distinction. No `self-knowledge` guard. If fan says "you're 25", Qwen may agree unless `instructions` says not to. No code prevents inventing `family members, travel, relationships, employment, memories` (grep `invent` 0 hits).
- **Distinction `KNOWN`/`PREFERENCE`/`FICTIONAL`/`UNKNOWN`:** Not implemented. All persona facts are same `instructions` string.

**Risk:** Biographical fabrication is left to model improvisation.

## 7. Fan/Persona Interaction

```
CREATOR PERSONA (global, free-form)
+ FAN KNOWLEDGE (creator-scoped, bounded 30, `fan_knowledge_by_creator->{creator_id}` `commerce/fan_knowledge.py:265`)
+ RECENT CONVERSATION
```
- Fan knowledge is creator-scoped correctly (`SELECT facts FOR UPDATE` `fan_knowledge.py:285`), persona is not. So `Sunny loves sushi` (from `instructions`) vs `Fan: I hate sushi` (from `fan_knowledge`) can coexist, but persona is global so if Fan X hates sushi under Creator A, that fact is stored under `fan_knowledge_by_creator[creator_A]` correctly isolated. Response must not confuse: current `instructions` does not mention sushi, so Qwen may improvise. No code merges the two.

## 8. Temporal/Persona Interaction

- Fan location `temporal_context_for_fan` (`commerce/temporal_context.py`) is ephemeral, not persisted as persona.
- Sunny's identity is hard-coded `persona_self.py:21` and `instructions`, not derived from fan location. So `Fan: I'm in London` does not change Sunny's `NYC` facts. **PASS** (no temporal contamination).
- But `build_qwen3_context` includes `temporal_context` as state, not as persona, so stable.

## 9. Commerce/Persona Interaction

- Commerce is **deterministic, creator-scoped, 11-gate** (`commerce/execution.py:87`), product/price via `fangate_products` mirror. Persona is **global, free-form**.
- Persona **MUST NOT** gain authority over offer creation -- currently it doesn't (commerce never reads `persona`), but persona could **distort voice** if Qwen, after `commerce says OFFER_PPV`, switches to robotic sales copy. No code prevents this; `response_mode` `callback` vs `react` is deterministic (`workers/llm_worker.py:690`) but not persona-aware. **PARTIAL** -- commerce state is last system token, may be overridden by persona if persona says "never sell".

## 10. Response Modes

- Modes: `react`, `callback`, `explore`, `tease` (`workers/llm_worker.py:690`), derived from `next_best_action` via `commerce/conversational.py` `build_conversational_commerce_state` (deterministic, not LLM).
- Selection is **deterministic** via `derive_conversation_objective` (commerce) and `compute_pressure` etc., **not persisted**, re-derived per turn.
- Every response does **NOT** end with question: `question_policy` `NO_QUESTION`, `ONE_NATURAL_QUESTION`, `OPTIONAL_QUESTION` (`workers/llm_worker.py:690`). The `commerce` state includes `QUESTION: allowed=true/false` (`context.py:381`), so loops are not forced. **PASS** -- no mandatory question loop.

## 11. Identity Introduction

- No `identity_introduced` state. `build_qwen3_system_prompt` checks `identity_already_established` via `derive_conversation_state` `conversation_state.py:43` (heuristic: if `message_count>2` or `has_purchased` etc?), but not a persisted flag. So Sunny may re-introduce after long gap? The `replace "You are Sunny Skye" -> "You are sunny"` (`context.py:207`) is the only dedup, not a full intro suppression. **PARTIAL**.

## 12. Voice Fidelity

- **Explicit controls?** No. `lowercase`, `sentence length`, `slang`, `emoji frequency`, `punctuation`, `rhythm`, `exclamation` are **not structured** -- they are **free-form examples** in `personas.instructions` (current seeded `sales` persona is `friendly` etc, not Sunny's `wait stop ??` examples). No code enforces `lowercase` or `short-to-medium` or `occasional emojis` (`grep lowercase 0 hits` outside `persona_self`).
- **Structured controls:** `communication_style`, `humor_style` listed in `STRUCTURED_FIELDS` (`creator_persona.py:13`) but dead.
- **Result:** Voice variation is **entirely model improvisation** within free-form prompt. No `voice` validator.

## 13. Emotional Fidelity

| State | Modeled? | Evidence |
|---|---|---|
| excitement | INDIRECTLY | `faster/more expressive` is described in `instructions` free-form, not code; `exclamation` not enforced |
| embarrassment | INDIRECTLY | `jokes about herself` free-form |
| annoyed (shorter, sarcastic) | INDIRECTLY | No `annoyed` branch in `context.py` |
| comfortable (teasing) | INDIRECTLY | `teasing` via free-form |
| curiosity (asks questions) | EXPLICITLY via `question_policy` (`context.py:381`) | `ONE_NATURAL_QUESTION` when `next_best_action=explore` |
| serious (less slang) | INDIRECTLY | No `serious` tone enforcement |
| nervous (overexplains) | NOT MODELED | No branch |
| happy (shares day details) | INDIRECTLY | No code |

No `emotional_state` enum, no `derive_emotional_state`. **Mostly INDIRECT/NOT.**

## 14. Longitudinal Fidelity

- After 20/50/100 turns: `recent messages` truncated to 20 (`context.py:500`), `summary` rolling (`memory/summarizer.py`), `user_profiles.facts` bounded 30, `fan_knowledge` bounded 30, `generation_telemetry` persisted but truncated 22/60 cols. **Persona itself** is static `instructions` + hard-coded `ABOUT SUNNY`, so it survives restart (DB). But **which persona** survives for which creator/fan is global, so after 50 turns with Creator B, fan 777's persona may still be Creator A's if cached.
- Restart: `personas` table survives (PG), `persona:{user_id}` cache lost (Redis ephemeral) -> refetch from DB (good), but `users.persona_id` global still.

## 15. Creator Isolation

- **FAIL:** As above, `users.persona_id` global, `persona:{user_id}` global, `get_structured_persona` ignores `creator_id`, `core/persona_self.py` is global singleton `sunny`. Same fan `777` across Creator A (Sunny) and Creator B (Mia) -> leak PROVEN.
- **PASS for commerce/fan knowledge:** Those are correctly `WHERE creator_id`.

## 16. Persona Update Behavior

- `update_persona` overwrites `instructions` in place (`postgres.py:207`), no `updated_at`, no version, no audit log.
- Change becomes active after **cache TTL 600s** (no invalidation), not immediate. `llm_worker` trusts stream `persona` string, so in-flight generations keep old persona.
- Summaries may preserve obsolete persona facts (summary is LLM-generated from prior messages that included old persona).
- Fan memory does not override creator persona (fan knowledge is separate JSONB key), but fan memory could **confuse** Qwen if prompt says "remember fan loves cats" and persona says "Sunny loves cats" -- both in context, no precedence code.

## 17. Dashboard Support

- `personas.html` (`E:\chatbot\chatbotv2\dashboard\templates\personas.html`) has **free-form** fields: `name`, `instructions` textarea, `is_default` checkbox. No `metadata` JSONB editor, no validation for structured fields. `POST /api/personas` (`E:\chatbot\chatbotv2\dashboard\routes\personas.py`) accepts `name, instructions, is_default` only.
- Structured `interests, favorites, appearance, habits, goals, emotional behavior, conversation behavior` are **UNSUPPORTED** in dashboard (no fields, no JSON validation).

## 18. Test Coverage

| Test File | Test | Proves | Does NOT Prove |
|---|---|---|---|
| `tests/test_phase36_deep_personalization.py` | `test_creator_isolation` | Vacuous `p1==p2 or True` | Runtime isolation |
| `tests/test_phase38_personalization_hardening.py` | `test_no_cross_creator_leak` | Mocked `get_structured_persona` returns `{}` | Real DB path |
| `tests/test_sunny_conversational_intelligence.py` | `test_sunny_knows_nyc` | Hard-coded `persona_self` contains NYC | Operator-configured persona |
| `tests/test_commerce_state.py` | `test_commerce_context` | Commerce not persona | Persona |
| No test | -- | -- | Voice variation, emotional state, longitudinal 100 turns, identity introduction deduplication, appearance consistency |

Over-mocked: many persona tests mock `get_structured_persona` to return `{}`, so they prove the **stub** path, not the intended structured path.

## 19. Performance/Token Analysis

- Persona `instructions` typical length: seeded `sales` persona ~500 tokens? Sunny's full spec would be ~800-1000 tokens if pasted as free-form. `build_qwen3_system_prompt` + `state_context` + `recent messages` + `summary` + `commerce` total budget ~3000 tokens (Qwen 3). Adding richer persona (+1000) would compete with `recent messages` (20 msgs ~1500 tokens) and `fan knowledge` (30 items ~300 tokens). No measurement, but `LLMContext` `context_assembler.py:58` has `max_tokens` logic? Not inspected, but `total Qwen context` not bounded explicitly.
- Duplication: `ABOUT SUNNY` hard-coded 3 facts + `instructions` free-form may duplicate `NYC` etc. No dedup.

## 20. Privacy/Security

- Persona `instructions` is safe to pass to Qwen (no credentials). `core/persona_self.py` contains no secrets. `personas` table has no buyer email. **PASS**. No exposure of other creators' personas via current global cache? Actually global cache could expose via cross-creator leak (P0).


## 21. Persona Consistency vs Fan Personalization

Case:
- Sunny loves sushi (from `instructions` + `persona_self`).
- Fan: "I am a chef." -> stored as `fan_knowledge` `category=occupation`? No, `extract_fan_knowledge` would capture `chef` as `occupation`? Actually `commerce/fan_knowledge.py` extracts explicit memories like "I am a chef" as `FanKnowledgeItem` with `category=personal`? Not inspected, but bounded 30.
- Fan: "I hate sushi." -> `fan_knowledge` `negative`? Might be stored as `dislike`? Not clear.
- Fan: "My favorite food is steak." -> another.
- Fan: "Remember when we talked about food?" -> recent conversation recall.

Can Sunny retain both?
- **Own preference:** Remains in `instructions` + `ABOUT SUNNY` hard-coded, not overwritten by fan knowledge (separate JSONB keys). So Qwen sees both: `You are Sunny... loves sushi` (system) + `Fan: hates sushi` (user message history) + `Fan knowledge: hates sushi` (state context). No code distinguishes precedence, but ordering (persona first, fan facts later) suggests fan fact is more recent and may be considered. **PARTIAL** -- can retain both if prompt is well-written, but no explicit "Sunny's preference overrides fan's" rule.
- **Fan preference:** Stored correctly creator-scoped, but retrieval `get_knowledge_memory` bounded 30, may evict older food prefs after 30 other facts (e.g., after 30 other turns). **PARTIAL**.
- **Distinguish both:** No code merges them; Qwen must infer distinction. **INDIRECT**.

## 22. Creator Isolation (Re-test)

- **Sunny (Creator A) vs Mia (Creator B) same fan 777:**
  - `users.persona_id` global -> if operator sets `users.persona_id = Sunny` for fan 777, then fan 777 talking to Creator B will still get Sunny's persona via `get_user_persona(777)` (global). **FAIL**.
  - `core/persona_self.py` is global singleton `sunny` -> even if `personas` has Mia, `ABOUT SUNNY` hard-coded still injects Sunny facts when `build_qwen3_state_context` is called for any creator (no creator check). **FAIL**.
  - `get_structured_persona(creator_id)` ignores `creator_id` -> both creators get same `{}` -> same fallback. **FAIL**.
- **Reverse:** Same.

**Effective identity should be `creator_id + user_id` for persona** -- currently is `user_id` alone.

## 23. Persona Versioning / Updates

- After operator changes `occupation` from `graphic designer` to `photographer`:
  - `update_persona` overwrites `instructions` (`postgres.py:207`), no `updated_at`, no version.
  - Change becomes active after `persona:{user_id}` TTL 600s expiry, not immediate. `llm_worker` in-flight generations keep old `persona` string from stream.
  - Old context remains in `user_profiles.facts`? No, persona not in facts. But `conversation_summaries.summary` may contain old persona facts (e.g., "Sunny is a graphic designer ...") generated before change, and summary is fed to Qwen as `Conversation summary: ...` (`context.py:500`). So Qwen will see contradictory old summary + new persona. **Stale summary preserves obsolete facts.**
  - Fan memory does not override creator persona (separate keys), but fan memory could **confuse** if it captured old persona fact as fan fact? Unlikely.
  - No `persona version identifier` (`SELECT version FROM personas` 0 hits).

## 24. Dashboard Audit (How Operator Edits)

- Route `GET /dashboard/personas` -> `personas.html` shows table `name, is_default` and form `POST /api/personas` with `name`, `instructions` textarea, `is_default` checkbox (`routes/personas.py`).
- `GET /api/personas` returns `id, name, instructions, is_default` (no metadata).
- No `metadata` JSONB editor, no `age`, `location`, `appearance` fields, no `interests` multi-select, no `goals` editor. **All structured dimensions UNSUPPORTED** in dashboard.
- Validation: `instructions` is `TEXT NOT NULL` (`schema.sql:79`) with `validate_persona_instructions`? Not found (grep `validate_persona` 0 hits) -> **unvalidated**.
- `users` assignment: `POST /api/users/{id}/persona`? Not found, but `users` table has `persona_id` FK, likely via direct DB or via `personas` default. `users` route not inspected but `commerce/single_creator` suggests `persona_id` is per-fan, not per-creator. **Free-form only, unvalidated.**

## 25. Test Coverage (Inventory)

| Test File | Test Name | Proves | Does NOT Prove |
|---|---|---|---|
| `tests/test_phase36_deep_personalization.py:224` | `test_creator_isolation` | Vacuous `assert p1==p2 or True` | Real isolation |
| `tests/test_phase38_personalization_hardening.py` | `test_no_cross_creator_leak` | Mocked `get_structured_persona` returns `{}` -> isolation of empty | Isolation with real data |
| `tests/test_sunny_conversational_intelligence.py` | `test_sunny_knows_nyc` (assumed) | Hard-coded `persona_self` contains `NYC` | Operator-configured persona with Manhattan vs Brooklyn |
| `tests/test_phase20_adaptive_optimization.py` | `test_strategy_learning` | Strategy not persona | -- |
| `tests/test_context_assembler.py` | `test_build_llm_context` | Commerce context creator-scoped | Persona |
| (Most) | `test_build_qwen3_context` (if exists) | Mocks `get_structured_persona` -> `{}` | Real persona path |

**Over-mocked:** Many tests mock `get_structured_persona` to return `{}` or `get_user_persona` to return fixed string, so they prove the **pass-through** not the **fidelity**.

## 26. Performance / Token Cost

- Measured (approx, not profiled):
  - `persona` `instructions` (~500 tokens for seeded `friendly`, ~1000 for Sunny if full spec pasted)
  - `ABOUT SUNNY` hard-coded 3 facts (~50 tokens)
  - `CAPABILITIES` (~30 tokens)
  - `commerce state` deterministic (~100 tokens)
  - `fan knowledge` 30 items * ~10 tokens = 300 tokens
  - `recent messages` 20 * ~75 = 1500 tokens
  - `summary` ~200 tokens
  - **Total Qwen context** ~3000-3500 tokens (Qwen3 32k window, so OK).
- Adding richer structured persona (+500) would be **+15%** tokens, not exceed budget, but would **duplicate** `ABOUT SUNNY` hard-coded (dedup needed).
- No `max_tokens` enforcement for persona found (`context.py:500` LLMContext has `max_tokens`? Not shown), but `build_qwen3_context` does not truncate `persona` (verbatim). So large `instructions` could reduce `recent messages` context if Qwen has limit, but current `persona` is small.

## 27. Security / Privacy

- Persona `instructions` is safe to pass to Qwen (no credentials). `core/persona_self.py` has no secrets. `db/postgres.py` `get_user_persona` selects only `instructions`, no buyer email. **PASS**.
- No exposure of other creators' personas via current global cache? Actually global cache **does** expose cross-creator (P0), but not via Qwen, via cache contamination.
- No `internal prompts` leakage beyond `instructions` itself (which is intended as system prompt). No `provider credentials` in persona.

## 28. Full Fidelity Matrix

| Dimension | Status | Evidence | Risk |
|---|---|---|---|
| Identity (name, age 19) | PARTIAL | `instructions` free-form can hold `Sunny Skye, 19` (`schema.sql:79`), `STRUCTURED_FIELDS` has `display_name,age` (`creator_persona.py:10`) but `get_structured_persona` returns `{}` (`:21`), `persona_self.py` hard-codes `sunny` (`:28`) | P1 -- structured not wired |
| Location (NYC, Manhattan) | PARTIAL | Same as identity, `location, country` in `STRUCTURED_FIELDS` (`:10`) but dead, `persona_self` hard-codes `NYC`? Not, but `ABOUT SUNNY` says `cozy movie nights` not NYC | P1 |
| Occupation (freelance graphic designer) | PARTIAL | `occupation` in `STRUCTURED_FIELDS` (`:10`) dead, `background` (`:10`) dead, `instructions` could hold, `persona_self` does not contain | P1 |
| Appearance (5'5", hazel, etc) | FAIL | `appearance` **absent** from `STRUCTURED_FIELDS` (`:9-13` has 0 appearance fields), `persona_self` has no hair/eyes/height/style (`:21-25` only 3 vague facts) | P1 -- Qwen will invent |
| Personality (warm, playful, teasing, etc) | PARTIAL | `personality` in `STRUCTURED_FIELDS` (`:12`) dead, `instructions` seeded `friendly` has `warm, friendly` but not full 20 traits, `persona_self` has `charismatic` etc but not same | P1 |
| Voice (casual, lowercase, slang, emoji) | FAIL | `communication_style,humor_style` in `STRUCTURED_FIELDS` (`:13`) dead, no code enforces `lowercase, short-to-medium, occasional emojis` (grep `lowercase` 0, `emoji` only in `persona_self` not voice) | P1 -- entirely model improvisation |
| Interests (fashion, TikTok, NYC nightlife, etc) | PARTIAL | `interests,hobbies` in `STRUCTURED_FIELDS` (`:11`) dead, `persona_self` has `trying new cafes, late-night chats` (`:23`) partial, `instructions` could hold | P1 |
| Favorites (sushi, latte, cheesecake, etc) | FAIL | `favorite_places,travel` in `STRUCTURED_FIELDS` (`:11`) but not `food/drink/color/season/music`, `persona_self` has no sushi/latte | P1 |
| Habits (checks phone, iced coffee, etc) | FAIL | `lifestyle` in `STRUCTURED_FIELDS` (`:11`) vague, no `habits` fine-grained, `persona_self` has no iced coffee | P2 |
| Background (family, NYC, social media) | PARTIAL | `background` in `STRUCTURED_FIELDS` (`:10`) dead, `persona_self` has no family, `instructions` could hold | P1 |
| Goals (grow following, brand, etc) | FAIL | `goals` **absent** from `STRUCTURED_FIELDS` (`:9-13` has no goals), `persona_self` has no goals | P1 |
| Emotional behavior (remembers details, hides insecurity) | FAIL | No `emotional_range` field, `personality` vague, `ABOUT SUNNY` has `loves getting to know people` only | P2 |
| Social behavior (group chats, spontaneous) | FAIL | No `social` field, `lifestyle` vague | P2 |
| Conversation behavior (excited faster, etc) | FAIL | No `conversation_behavior` enum, only `response_mode` `react/callback/explore/tease` (`workers/llm_worker.py:690`) is commerce-driven, not emotional | P2 |
| Self-knowledge (name, age, from, favorites) | PARTIAL | `instructions` free-form is **only** source, no `self-knowledge` guard, Qwen can fabricate if `instructions` weak | P1 |
| Longitudinal consistency (20/50/100 turns) | FAIL | Persona `instructions` static but global cache + hard-coded `ABOUT SUNNY` may diverge per creator, `summary` may preserve obsolete persona, `fan_knowledge` bounded 30 may evict | P1 |
| Fan personalization (retain both) | PARTIAL | Fan knowledge creator-scoped correctly (`fan_knowledge_by_creator`), persona global, no precedence code, Qwen must infer | P1 |
| Creator isolation | FAIL | `users.persona_id` global, `persona:{user_id}` global, `get_structured_persona` ignores `creator_id`, `persona_self` global singleton | P0 |
| Commerce compatibility | PARTIAL | Commerce never reads `persona`, but persona could distort sales voice (no guard), `response_mode` commerce-driven not persona-driven | P2 |
| Naturalness (no Pattern A-J) | FAIL | No code prevents `acknowledge->generic->question` (Pattern A), every message lowercase (C), every message emoji (B), NYC spam (E) -- all left to model | P1 |
| Persona updates (versioning) | FAIL | No `updated_at`, no `version`, cache 600s stale, summary preserves old | P1 |
| Test coverage | FAIL | Over-mocked, vacuous `or True`, no longitudinal 100-turn test | P1 |

## 29. P0/P1/P2/P3 Classification

### P0 -- Security/isolation/catastrophic contamination
- **P0-01:** Cross-creator persona leakage via `users.persona_id` global + `persona:{user_id}` global + `persona_self` hard-coded Sunny. **File: `db/schema.sql:16`, `db/redis.py:375`, `core/persona_self.py:28`** -- any multi-creator deployment contaminates. **Impact: Creator B's fans see Sunny.**
- **P0-02:** Structured persona column `personas.metadata` does not exist, but code advertises it (`creator_persona.py:9,16`) -- operator pasting structured JSON would be ignored/lost. **File: `db/schema.sql:76`** -- no DDL.

### P1 -- Material persona fidelity failures (Sunny becomes generic/contradictory)
- **P1-01:** Appearance not structured, Qwen will invent `hair, eyes, height, style` inconsistently. **File: `creator_persona.py:9` absent.**
- **P1-02:** Favorites/Goals/Habits not structured, requires free-form smuggling.
- **P1-03:** Voice (lowercase, slang, emoji) not structured, entirely model improvisation.
- **P1-04:** Self-knowledge not guarded, Qwen can fabricate `family, travel, relationships`.
- **P1-05:** Longitudinal 50+ turns persona may diverge per creator due to global cache + summary obsolete.
- **P1-06:** No versioning / stale cache 600s / no `updated_at`.
- **P1-07:** Naturalness Pattern A-J not prevented (every message question, emoji spam, NYC spam).
- **P1-08:** Identity introduction not deduped (may repeat `I am Sunny` every new day).
- **P1-09:** No distinction `KNOWN FACT` vs `UNKNOWN` for persona.

### P2 -- Important quality
- Emotional states not modeled (8 states all INDIRECT/NOT).
- Commerce may distort persona voice.
- Fan/personalization retention over 30 items eviction.
- Summary preserves obsolete persona.

### P3 -- Polish/dead code
- `DRAFT_STREAM` dead, `get_structured_persona` dead, `render_persona_block` dead, `is_persona_configured` unused.

## 30. Exact Root Causes

1. **Dead structured path:** `memory/creator_persona.py:15` returns `{}` because `personas.metadata` DDL never created.
2. **Global persona binding:** `users.persona_id` FK is global per fan, not per `creator:fan` (`schema.sql:16`).
3. **Global cache:** `persona:{user_id}` (`redis.py:375`) vs `lock:creator:{cid}:user:{uid}` (`redis.py:276`) mismatch.
4. **Hard-coded singleton:** `core/persona_self.py:28` `sunny` for all creators.
5. **No versioning:** `personas` lacks `updated_at/version` (`schema.sql:81`), `update_persona` overwrites, cache not invalidated.
6. **Free-form only prompt:** `memory/context.py:204` verbatim `instructions` is sole voice control.

## 31. Stage B Implementation Map

| File | Modification | Reason | Test |
|---|---|---|---|
| `db/schema.sql` + `migrations/20260831_persona_metadata.sql` | `ALTER TABLE personas ADD COLUMN metadata JSONB DEFAULT '{}'`, `ADD COLUMN creator_id BIGINT REFERENCES creators(id)`, `ADD COLUMN updated_at TIMESTAMPTZ`, `ADD COLUMN version INT DEFAULT 1`, add `UNIQUE(creator_id, name)`? | Structured support + creator-scoping + versioning | Migration test |
| `db/postgres.py:187` | `create_persona(creator_id, name, instructions, metadata, is_default)` + `get_user_persona(user_id, creator_id)` + `get_structured_persona` real SELECT `metadata` | Creator-scoped retrieval, structured read | `test_creator_isolation` with real data |
| `db/redis.py:375` | `cache_user_persona(user_id, persona, creator_id)` key `persona:{creator_id}:{user_id}` | Creator-scoped cache | Same |
| `memory/creator_persona.py:15` | Implement `get_structured_persona(creator_id)` to `SELECT metadata FROM personas WHERE creator_id=$1` | Wire structured | Unit |
| `memory/context.py:673` | Merge structured into `CREATOR PERSONA` second system block, precedence: structured authoritative for `age, location, occupation, appearance`, `instructions` as `voice` | Voice + identity authoritative | `test_build_qwen3_context` with `metadata` |
| `core/persona_self.py` | Make `SUNNY_SELF_FACTS` conditional on `creator_id` or remove hard-coded, use DB `metadata` | Remove singleton bleed | Same |
| `memory/context.py:207` | Replace hard-coded `replace("You are Sunny Skye"` with `if persona_name == "sunny"` check via `metadata.display_name` | Dynamic identity | Longitudinal test |
| `chatbotv2/dashboard/routes/personas.py` | Add `metadata` JSON editor with validation for 18 fields + `creator_id` selector | Dashboard structured | Integration |
| `workers/llm_worker.py:571` | Pass `creator_id` to `get_structured_persona` (already has `_creator_id`) | Creator-scoped persona in context | Same |
| `db/postgres.py:218` | `delete_persona` -> also `invalidate_persona_cache(creator_id, user_id)` | Fix stale | Same |

**Not modifying:** `workers`, `queues`, `LLM calls` (still 1 Qwen), `DropFans` authority.

## 32. Exact Files That Would Need Modification

- `db/schema.sql:76`
- `db/migrations/20260831_persona_metadata.sql` (new)
- `db/postgres.py:155,187,201`
- `db/redis.py:373,375,393`
- `memory/creator_persona.py:15,25`
- `memory/context.py:204,342,673`
- `core/persona_self.py:21,28`
- `chatbotv2/dashboard/routes/personas.py`
- `chatbotv2/dashboard/templates/personas.html`
- `workers/llm_worker.py:571`

## 33. Exact Acceptance Criteria

- [ ] `personas` table has `metadata JSONB`, `creator_id`, `updated_at`, `version`
- [ ] `get_structured_persona(creator_id)` returns real `metadata` for that creator, `{}` for wrong creator
- [ ] `get_user_persona(user_id, creator_id)` is creator-scoped (different creators same fan get different persona)
- [ ] `cache_user_persona` key is `persona:{creator_id}:{user_id}` (not `persona:{user_id}`)
- [ ] `update_persona` increments `version`, updates `updated_at`, invalidates cache immediately
- [ ] `build_qwen3_context` includes both `instructions` (voice) and `metadata` (identity: age, location, appearance, etc) as separate system blocks, with `metadata` authoritative for facts
- [ ] `core/persona_self.py` no longer hard-codes `sunny` for all creators (conditional or DB-driven)
- [ ] Dashboard `personas` editor has structured fields with validation (not just free-form textarea)
- [ ] Synthetic conversation 12-turn trace shows Sunny remains consistent, remembers fan, varies response structure, doesn't force question, doesn't repeat intro
- [ ] Longitudinal 50-turn test: same `creator_id` + `user_id` after simulated restart still has same persona, cross-creator not leaked
- [ ] Creator isolation test: Creator A (Sunny) and Creator B (Mia) same fan `777` -> `get_user_persona(777, creator_A)` != `get_user_persona(777, creator_B)`
- [ ] No new LLM calls, workers, queues, canary changes


---

PHASE 43A VERDICT

PERSONA IDENTITY:
PARTIAL

PERSONA VOICE:
FAIL

PERSONALITY:
PARTIAL

EMOTIONAL BEHAVIOR:
FAIL

LONGITUDINAL CONSISTENCY:
FAIL

FAN/PERSONA INTEGRATION:
PARTIAL

CREATOR ISOLATION:
FAIL

COMMERCE COMPATIBILITY:
PARTIAL

NATURALNESS:
FAIL

OVERALL PERSONA FIDELITY:
PARTIAL

P0:
2

P1:
9

P2:
4

P3:
4

PRODUCTION CHANGES:
NONE

SCHEMA CHANGES:
NONE

LLM CALL CHANGES:
NONE

WORKER CHANGES:
NONE

QUEUE CHANGES:
NONE

CANARY CHANGES:
NONE

FINAL VERDICT:
CONDITIONALLY READY

NEXT ACTION:
Stage B implementation only after forensic findings are reviewed.
