# AI_NATIVE_PERSONA_PHASE_43D_IMPLEMENTATION_MAP — STAGE A RECONNAISSANCE
**Date: 2026-08-31 | Phase: 43D Stage A | READ-ONLY**

---

## 1. Current Call Graph (proven, not assumed)

```
Telegram inbound (Telethon events.NewMessage)
  → chatbotv2/handlers.py:26 handle_incoming_message
      check_rate_limit, upsert_user, save_inbound_message
      resolve_single_application_creator → creator_id (commerce/single_creator.py:57, single active creator)
      publish_event message.created (core/event_bus, Redis Pub/Sub best-effort)
      debounce_enqueue(creator_id, user_id) → debounce:creator:{cid}:user:{uid}:messages (db/redis.py:313)
      if owner → _wait_and_process(3s)

  → chatbotv2/handlers.py:126 _wait_and_process
      get_debounced_messages(creator_id)
      get_cached_user_persona(user_id, creator_id) → persona:{creator}:{user} (db/redis.py:381)  // isolated, no global fallback when creator present
      miss → get_user_persona(user_id, creator_id) → SELECT personas WHERE creator_id=$1 ORDER BY is_default DESC (db/postgres.py:155) // isolated
      cache_user_persona(..., creator_id) 600s
      get_cached_default_persona(creator_id) / get_default_persona(creator_id)
      enqueue_inbound {persona=legacy instructions string, generation_id=MD5(user:content:tgId), creator_id not in payload? actually persona string only } → inbound_messages XADD

  → Redis Stream inbound_messages CONSUMER_GROUP=llm_workers
      workers/llm_worker.py:1458 run_worker → read_inbound → process_message(user_id, user_message, persona string, generation_id)
          resolve_single_application_creator → _creator_id (again, authoritative)
          acquire_user_lock(creator_id, user_id) → lock:creator:{cid}:user:{uid} (db/redis.py:280)
          upsert_user, is_user_auto_reply_excluded
          build_qwen3_context(user_id, user_message, persona string, creator_id) (memory/context.py:504)  // QWEN INPUT CONSTRUCTION
              get_user, get_user_profile (+ fan_knowledge isolation for interests)
              get_recent_messages 20/800/3
              derive_conversation_state(history+current) → lifecycle, identity_already_established, current_topic/open_threads/tone (core/conversation_state.py:157)
              persona_name ← regex "You are <Name>" from legacy persona string, overridden by structured metadata.identity.name via get_structured_persona_async(creator_id) (memory/creator_persona.py:318) // reuse _structured_for_name
              build_qwen3_system_prompt(persona, user, profile, identity_already_established, persona_name) → system[0] 1077 chars
              CREATOR PERSONA: render_persona_block(None, structured) → system[1] 19541 chars (memory/creator_persona.py:379, context.py:604)  // EARLY, authoritative
              build_qwen3_state_context → system[2] STATE / PROFILE / COMMERCE filtered / SUMMARY 2 sentences / IDENTITY RULE generic / CONVERSATION topic/open/tone / ABOUT SUNNY (only if persona_name Sunny, core/persona_self.py:44 guard) / CAPABILITIES send_photo:no / RESPONSE mode=react / QUESTION allowed=false
              AVAILABLE CONTENT (vault) system[3] (creator-scoped)
              RELEVANT MEMORY (3) system[4]
              FAN KNOWLEDGE (5, temporal hint) system[5]  FAN KNOWLEDGE: occupation=... (commerce/fan_knowledge.py:525)
              LOCAL TIME (+ late night) system[6] (commerce/temporal_context.py:47)
              recent history user/assistant (20/3 trim)
          fan_knowledge extraction: extract_fan_knowledge(generation_id, existing) → add_knowledge_item (bounded 30, idempotent)
          publish_event ai.generation_started (generation_id, creator_id)
          extract_commerce_signals (single)
          _try_commerce_draft(context, persona) → resolve_commerce_product_with_history → CommerceStateRequest → resolve_and_run_commerce (deterministic 11-gate) → select_commerce_response
          if USE_COMMERCE_RESPONSE → draft = commerce deepseek_response (bypasses Qwen, template)
          else → build_conversational_commerce_state (deterministic desire/temp/readiness/window/objective/next_best_action) → response_mode/question_policy → REMOVE old RESPONSE/QUESTION from context, APPEND Commercial STATE + CONVERSATION INTELLIGENCE as final system msg (llm_worker.py:715)  // LAST SYSTEM MSG WINS
          production_control autonomous_allowed gate → may skip Qwen (draft="Thanks team follow up" score 0.1)
          operational intelligence → aftercare etc (best-effort)
          canary check should_use_agent (ai_agent_canary_enabled false → never)
          if skip → no Qwen
          elif agent → run_agent_runtime (not used)
          else → generate_draft(context, user_message) (llm_worker.py:83) // SINGLE QWEN
                 merged_system = "\n\n".join(system parts) → provider.generate_with_history(system_instruction, messages, max_tokens 200, temp 0.85, top_p 0.95) (core/llm_provider_ollama or gemini)
                 // OR generate_draft_with_tools (bounded 3 tool calls) if llm_tools_enabled
          score_draft(draft, user_message, context, is_authorized_commerce) → composite 0-1 + HARD_FLAGS price/personal_info/distress/photo_promise (core/scoring.py:78) // LLM scorer natural_tone 0-10 via provider.generate (cheap_model), fail-closed 0.0
          production_control autonomous_allowed recheck → may cap score 0.1 + autonomous_paused flag
          shadow Qwen (if enabled, never sent)
          routing: is_auto_reply_enabled? score>=0.80 && !flags → enqueue_send SEND_STREAM + publish ai.generation_completed MUST after enqueue; else operator_queue + suggestion.created + ai.generation_completed (was_auto_approved false)
          post_process async: extract_and_update_profile + maybe_summarize (20)
          release_user_lock(creator_id)
      → Redis Stream send_messages → send_worker → check_send_rate_limit 1/s burst5 → Telethon send → save_outbound_after_send + publish message.sent

Alternative paths:
  - dashboard POST /api/dialogs/{id}/ai-reply (chatbotv2/dashboard/routes/messages.py:77) → get_cached_user_persona(dialog_id) WITHOUT creator_id → global fallback (leak, not isolated) → enqueue_inbound {persona}
  - dashboard POST /api/send-message /api/dialogs/{id}/send → enqueue_send directly (no persona)
  - scheduled_messages → scheduler_worker._build_send_payload(msg["content"]) → enqueue_send (no persona, no Qwen) (workers/scheduler_worker.py:37)
  - operator_queue approved → operator bot send (no Qwen)
  - DLQ replay → re-enqueues original persona snapshot (stale possible)
```

**Scoring boundary** is AFTER Qwen: `generate_draft → score_draft → routing`. No validation between.

**Telemetry/events** existing: `core/event_bus.publish_event` (Redis Pub/Sub `chatbot:events`, generation_id + creator_id + scope=user, best-effort), `core/telemetry.GenerationTelemetry` (provider_latency, context_build_ms, scoring, routing, pressure, etc.), `core/execution_stage` / `chatbotv2/dashboard/event_subscriber` → WebSocket `ws_manager` (acceleration, polling fallback). No persona_behavior stage exists.

**Persona retrieval**: `memory/creator_persona.py:318 async get_structured_persona_async(creator_id)` → `SELECT metadata, version WHERE creator_id=$1`. Rendering `render_persona_block` 19k deterministic, 23 fields, facts vs behavioral_rules separated. Legacy `personas.instructions` still first system msg. Persona cache `persona:{creator}:{user}` + `persona:creator:{creator}` + version, explicit per-creator invalidate (db/redis.py:437, db/postgres.py:250). No anti-drift beyond re-injection.

---

## 2. Behavioral Gaps (Phase 43C hostile findings, verified)

| Category | Stored | Injected | Deterministically Enforced | Verdict | File:Line |
|---|---|---|---|---|---|
| identity/age/location/occupation | YES 23-field | YES Name: Sunny Skye / Age 19 ... | NO guard vs `you're 21` history | PROMPT-ONLY (factual recall) | creator_persona.py:49, context.py:615 |
| appearance | YES | YES Appearance Height... | NO validator, Qwen can invent | PROMPT ONLY | creator_persona.py:75 |
| personality 20 traits | YES | YES Personality Traits warm... | NO branching | PROMPT ONLY | creator_persona.py:87 |
| communication lowercase/slang/emoji/medium | YES | YES Communication Casing lowercase / Preferred Emojis 😭😂💕 / Slang moderate | **0 code** — grep lowercase 0 outside persona; no post-processor; no emoji counter; max_tokens 200 but no sentence hard cap | **FAIL** | creator_persona.py:107, context.py:227 |
| message length 2-4 sentences | YES | YES Rules: 2-4 sentences | PARTIAL — scorer appropriate_length LLM-judged 0-10, not hard | PARTIAL | scoring.py:58, config.py:29 |
| rhythm ack→generic→question prevention | NO counter | NO | NO — plan_response_mode defined (core/response_mode.py) but never called in build_qwen3_context (comment P1-02) | PROMPT ONLY | response_mode.py exists but unused |
| teasing/comfortable, sarcasm/annoyed, sincerity/serious | YES When ... injected | YES When Annoyed: shorter sarcastic ... | **NO state** — tone only warm/curious/flirty/supportive (conversation_state.py:128); annoyed/serious/comfortable never derived, stored, expired | **FAIL** 7/8 prompt-only | conversation_state.py:128 |
| emotional states 8 | YES emotional_behavior | YES When Excited: ... | **NO trigger/storage/selection/injection consequence** | FAIL | same |
| disagreement can_disagree playful_or_sincere | YES behavioral_rules | YES Behavioral Rule... | **NO planner**, Qwen agreeable default; no too_agreeable flag | FAIL | creator_persona.py:274, scoring.py no flag |
| question naturalness vs interrogation | YES questioning natural_followups | YES plus deterministic MAX_QUESTIONS_PER_3=1 (question_policy.py:11) | **PASS frequency**, FAIL naturalness | PARTIAL | question_policy.py:39 |
| NYC moderation, generic agreement, memory dump, forced persona | YES boundaries | YES | **0 code** — no NYC mention counter, no generic ack regex scorer | FAIL | persona not read |
| flaws (impulsive, overthinks...), strengths | YES | YES Flaws: ... | **NEVER acted** — no delay/overthink code | PROMPT ONLY |  |
| longitudinal anti-drift | re-inject persona each turn | YES | **NO scorer** — breaks_persona LLM-judged natural_tone 0-10, temp 0.85 drift to generic | FAIL | scoring.py:72 |

**Conclusion 43C**: Only 2 deterministic behaviors exist: question frequency cap and identity dedup. 90% prompt-only.

---

## 3. Integration Point (safest, architecture-compatible)

**Constraints**: 1 SIGNAL + 1 QWEN + 1 SCORING, no new LLM/worker/queue/ generation path, preserve commerce gates, keep canary, keep creator isolation.

**Chosen point: TWO-PHASE insertion into existing `process_message` without new generation:**

**Phase A — BEFORE Qwen (influences generation, cheap, no extra LLM):**
- Right after `build_qwen3_context` and `build_conversational_commerce_state` (which already appends Commercial STATE as final system msg), insert deterministic `PersonaBehaviorState` derivator that reads: `structured persona (cached _structured_for_name)`, `conversation_state`, `fan_knowledge (relevant 5)`, `recent history (trimmed)`, `commerce objective`.
- It outputs concise behavioral constraint block `PERSONA BEHAVIOR:` (3-5 lines, <150 tokens) appended as final system msg BEFORE Qwen, so it outranks earlier large persona but is bounded. Example:
  ```
  PERSONA BEHAVIOR: emotional_state=excited confidence=HIGH
  Voice: expressive, 1-2 exclamations ok, occasional emoji 😭 allowed
  Rule: you may ask ONE playful follow-up, disagreement available playfully
  ```
  For serious: `emotional_state=serious confidence=HIGH → drop slang, prioritize sincerity, do not joke`.

**Phase B — AFTER Qwen (validates, no second LLM, fail-closed via existing fallback):**
- Immediately after `draft = await generate_draft(...)` and before `score_draft`, run deterministic `validate_persona_voice` / `validate_naturalness` / `validate_persona_consistency` O(length of response) that checks:
  - casing/emoji frequency/sentence count vs persona communication (bounds, not templates)
  - facts: age/location/occupation not contradicted vs CREATOR PERSONA (regex check)
  - question compliance vs question_policy
  - generic pattern detection via bounded recent assistant turns (last 3)
- Output `PersonaResponseValidation {voice_score, naturalness_score, persona_consistency, question_compliance, violations[]}` — diagnostic, cheap.
- **Correction policy (no second LLM):**
  - Minor (e.g., trailing whitespace, duplicate punctuation) → safe deterministic fix
  - Moderate (emoji spam 5 in 1 msg vs occasional) → allow but record telemetry, may cap score slightly (existing scorer already does)
  - Severe persona fact violation (Qwen says `I am 21 from Chicago`) → do NOT invent new response; route via existing safe fallback: treat as scoring flag `breaks_persona` → `min(composite,0.1)` → operator_queue (already exists in `score_draft` HARD_FLAGS). Add new deterministic flag `breaks_persona_fact` to trigger same. No new LLM call, uses existing `add_to_operator_queue` + `suggestion.created` path.
- This keeps 1 Qwen: Qwen → validation (deterministic) → scoring (existing LLM scorer, but validation adds deterministic flag) → routing.

**Why this point is safe:**
- No DB reread beyond already-fetched `structured persona` and `fan knowledge` (both already in context, reuse).
- No new persistence: state derived per-turn from recent messages + conversation_state (transient, like derive_conversation_state), restart-safe (re-derived from Postgres history).
- Failure-isolated: validation is pure function; exception → log warning, continue with original draft and no flag (best-effort, like commerce enrichment).
- Commerce authority untouched: validation never sets product/price/URL; commerce already appended as final system msg and is last, not overridden.
- Existing telemetry/event bus reused: new fields appended to `GenerationTelemetry` (already has pressure, decision_trace etc.) and optional `persona.behavior` Pub/Sub event (best-effort, like ai.generation_started).

**Alternative considered and rejected:**
- Inserting before `build_qwen3_context` would require passing behavioral block into context construction — more invasive, duplicates logic already in context.py.
- Replacing Qwen prompt entirely with persona_behavior — would lose 19k facts.
- Post-scoring correction with second LLM — FORBIDDEN (1 QWEN), would break single-pass.
- New worker/queue for validation — FORBIDDEN.

---

## 4. State Model (generic, not Sunny-hardcoded)

**Location**: `commerce/persona_behavior.py` (chosen because `commerce/` already holds deterministic single-creator, fan_knowledge, temporal, conversation_operations, pressure, adaptive_optimization — all pure, creator-scoped, no new deps; alternatively `memory/persona_behavior.py` would duplicate; `commerce` is correct per existing style where `build_conversational_commerce_state` lives).

**Design principles**: Generic over `persona` dict, bounded, restart-safe, tiny token cost.

```python
@dataclass(frozen=True)
class PersonaBehaviorState:
    emotional_state: str          # excited | happy | playful | curious | embarrassed | annoyed | serious | nervous | neutral
    confidence: str               # HIGH | MEDIUM | LOW
    conversation_mode: str        # react | explore | share | tease | callback | clarify | answer (reuse ResponseMode)
    question_allowed: bool
    question_policy: str          # NO_QUESTION | ONE_NATURAL_QUESTION | OPTIONAL_QUESTION
    disagreement_available: bool  # can_disagree true && not serious/cooldown/handoff
    teasing_allowed: bool
    sincerity_required: bool
    verbosity_target: str         # short | short_medium | medium (from persona.communication.message_length)
    emoji_policy: str             # none | occasional | allow_one
    lowercase_policy: str         # neutral | allow_lowercase
    naturalness_mode: str         # normal | avoid_generic_ack
    persona_version: int
    creator_id: int | None
    generation_id: str
```

**Derivation inputs (all already fetched, no extra PG queries):**
- `structured persona` (creator_persona.py build_sunny_persona or generic) → `communication.slang_level, emoji_style, message_length, behavioral_rules, emotional_behavior, conversation_behavior`
- `conversation_state` (core/conversation_state.py) → `tone, last_question, consecutive_questions, open_threads, current_topic, lifecycle`
- `fan_knowledge relevant 5` (commerce/fan_knowledge.py retrieve_relevant_knowledge) + `fan message text` → triggers
- `recent assistant turns (3)` for question/frequency/repetition
- `commerce objective/next_best_action` already appended → informs teasing/sincerity

**Heuristic triggers (deterministic regex, no LLM, LOW confidence fallback to neutral):**
- `I finally got the job!!!` + `!{2,}` + `got|won|yay` → excited HIGH
- `you are ridiculous lol` + playful keywords + tone flirty → playful MEDIUM
- `I messed everything up / overwhelmed / stressed` + sad/supportive keywords → serious HIGH (blocks teasing, forces sincerity)
- `why are you ignoring me` + `ignoring/dismissed` → serious CONCERNED
- `you're wrong / Brooklyn better than Manhattan` + subjective opinion pattern + `can_disagree true` → disagreement_available true, playful_disagreement
- Default → neutral LOW → no strong behavioral injection, allows variation.

**Confidence**: HIGH when 2+ signals agree, MEDIUM 1 signal, LOW fallback neutral.

**Genericity**: Reads `persona.behavioral_rules.can_disagree`, `communication.slang_level`, etc.; no `if persona.name == "Sunny Skye"` branch. Sunny is richest fixture but not special-cased.

---

## 5. Validation Model (diagnostic first, cheap)

```python
@dataclass(frozen=True)
class PersonaResponseValidation:
    voice_score: float            # 0-1
    naturalness_score: float      # 0-1
    persona_consistency: float    # 1.0 = no fact contradiction, 0.0 = age/occ/location flipped
    emotional_consistency: float  # 1.0 = matches expected state behavior
    question_compliance: bool     # respects question_policy
    disagreement_compliance: bool # not generic agree when disagreement_available
    violations: list[str]         # e.g., "too_formal", "emoji_spam 5/2", "too_long 6>4 sentences", "fact_agedrift 19→21", "generic_ack_template"
    validation_status: str        # PASS | SOFT_FAIL | FACT_FAIL
```

**Checks O(len response)):**
- `casing` — not force lower, but detect `corporate` (`That is certainly an interesting perspective. I completely agree...` → too_formal)
- `sentence count` via `re.split(r'[.!?]+')` vs persona `message_length short_medium` → 1-4 sentences bound
- `emoji frequency` via count vs `emojis.frequency occasional` → max 1 per msg, not every msg (check last 3)
- `formality` via ban phrases (`That is an interesting perspective`, `As an AI`, corporate) → too_formal
- `fact consistency` via regex scan `I am \d+` vs persona age 19, `I live in \w+` vs NYC, `I am a \w+ designer` vs freelance graphic designer → `fact_agedrift` etc → severe
- `question compliance` via `?` count vs `question_allowed` + recent 3 count
- `generic pattern` via `That sounds...`/`That makes sense...` repeated in last 3 assistant turns → `repeated_template`

**No rewriting**: Minor whitespace/punct deterministic fix only; otherwise flag → scoring will cap to 0.1 via new `breaks_persona_fact` HARD_FLAG, routing to operator queue (existing safe fallback). No second LLM.

---

## 6. Event Model (reuse existing bus)

**Channel**: `chatbot:events` via `core/event_bus.publish_event` (Redis Pub/Sub, best-effort, creator-scoped, failure-isolated like `ai.generation_started`).

**New event**: `persona.behavior` (alongside `ai.generation_started/completed/failed`, `suggestion.created`).

Payload (bounded, no secrets, no full fan message):
```json
{
  "event_type": "persona.behavior",
  "creator_id": 1,
  "generation_id": "md5(user:msg:tgId)",
  "persona_version": 4,
  "emotional_state": "excited",
  "confidence": "HIGH",
  "conversation_mode": "share",
  "question_allowed": false,
  "disagreement_available": true,
  "voice_score": 0.91,
  "naturalness_score": 0.88,
  "validation_status": "PASS",
  "violations": []
}
```

**Delivery**: emitted in `process_message` right after validation, before scoring, with `scope=user` (like generation events). Frontend deduplicates via `event_id` (existing bus does). Workers never import `ws_manager`.

**Dashboard**: Extend existing execution-stage / live panel (Phase 42 realtime) to show `PERSONA Sunny v4 | BEHAVIOR Playful/Curious | VOICE 0.91 | NATURALNESS 0.88 | QUESTION Allowed | DISAGREEMENT Available | STATUS PASS` by consuming `persona.behavior` event, not new WebSocket. Polling remains fallback per transport invariant.

**Privacy**: No `buyer email`, tokens, Redis internals, full persona JSON (only version + state names + scores).

---

## 7. Failure Behavior (fail-closed, existing fallback)

- **Persona fetch fails** (DB exception): `get_structured_persona_async` returns `{}` (already does) → `PersonaBehaviorState` falls back to neutral (no emotional state, default `react`, `question_allowed=false`, `verbosity short_medium`) → still inject minimal `PERSONA BEHAVIOR: neutral` (2 lines) → continue, never block generation.
- **Fan knowledge fails**: Same fallback, temporal LOCAL TIME not injected → behavior not late-night specific, but not crash.
- **Emotional derivator exception**: Catch, log `warning("persona behavior derivation failed")`, return neutral state, continue.
- **Validation exception**: Catch, log, return `validation_status PASS` with empty violations, do not cap score, continue to existing scoring.
- **Validation severe fact violation**: Add `breaks_persona_fact` to `flags` (existing HARD_FLAGS list extended), so `score_draft` does `min(composite,0.1)` → routes to `operator_queue` (existing `add_to_operator_queue` + `suggestion.created`), not auto-send. No new queue, no silent fabricate.
- **Retry/XAUTOCLAIM**: `generation_id` preserved (MD5 deterministic), behavioral state re-derived from same inputs, idempotent, no duplicate event (event_id dedup).
- **Restart**: No new persistence; state re-derived from Postgres history (messages, user_profiles fan knowledge) + persona metadata (PG), so restart safe.
- **Performance**: Derivator + validator are pure Python, bounded recent 3 + response length, no PG scan beyond already-fetched, no LLM, <1ms.

---

## 8. Test Plan (478 words, deterministic, no live LLM)

File `tests/test_phase43d_behavioral_fidelity.py` (reuse `conftest.py` mocks, no real PG/Redis needed).

**A. Emotional state** — all 8: feed fan msg `I finally got the job!!!` → `excited HIGH`; `you are ridiculous lol` → `playful`; `I messed up` → `serious`; `why ignoring me` → `serious/concerned`; `you're wrong` → `playful_disagreement` available; `horny` → playful/tease but blocked by serious gate if needed.

**B. Excitement** — `I got the job!!!` → `PERSONA BEHAVIOR: excited` line present in context before Qwen.

**C. Serious** — `I've been overwhelmed with family` → `serious` + `sincerity_required true` + `drop slang`.

**D. Playfulness** — flirty history → `playful` + `teasing_allowed`.

**E. Annoyance** — `why ignoring` + `tone` annoyed → `shorter, mild sarcasm, not hostile` (verify `verbosity_target short`).

**F. Voice** — Sunny `wait stop 😭 that's actually so funny` passes voice_score >0.8; `That is certainly an interesting perspective. I completely agree` → `too_formal` violation.

**G. Over-polished** — formal flagged.

**H. Emoji moderation** — 3 emojis in one msg vs `occasional` → violation; 0 emojis when `allow_one` → pass (not every msg needs emoji).

**I. Question budget** — `MAX_QUESTIONS_PER_3_TURNS=1` still enforced; after Q `What do you do?` unanswered, next turn `QUESTION allowed=false` regardless of excited.

**J. Disagreement available** — subjective `Brooklyn better than Manhattan` + `can_disagree true` → `disagreement_available true` injected.

**K. Not forced agreement** — same subjective, but serious context `I messed up` → disagreement NOT available (blocked).

**L. Naturalness** — repeated `That sounds...` in last 3 assistant → `repeated_template` violation.

**M. Longitudinal 50-turn** — synthetic 50 via `build_qwen3_context` loop with causal/flIRT/disagree/humor/serious/commerce/late-night + fan knowledge Max/Chicago→NY→Spain→back → verify `CREATOR PERSONA` still Sunny 19 each turn + `PersonaBehaviorState` not drift to generic `warm`.

**N. Creator isolation** — same fan 777, `creator_id=Sunny` vs `Mia` ( Mia persona without Sunny traits) → `PERSONA BEHAVIOR: playful` only for Sunny, not Mia unless Mia persona defines it.

**O. Versioning** — `personas.metadata persona_version 1→2` + behavioral_rules change → `PersonaBehaviorState.persona_version` reflects new.

**P. Fan knowledge** — `occupation=software engineer, pet_name Max` → `Max destroyed couch` does not trigger `Who's Max?`; verify `FAN KNOWLEDGE` present and `disagreement` not confused.

**Q. Temporal** — `city=Spain temporary until 2026-09-07` expired (now+8d) → not in `FAN KNOWLEDGE` retrieval.

**R. Commerce** — persona behavior never sets `price/product/URL`; `validate_persona_voice` does not bypass `price_mention` flag; DropFans authority test.

**S. Single-pass** — count `generate_draft` calls per `process_message` ==1 via mock provider counter.

**T. No second LLM** — patch `get_llm_provider` ensure `generate` called once (scoring LLM counts as scoring, not persona).

**U. Generation correlation** — `generation_id` + `creator_id` preserved through `PersonaBehaviorState` → `persona.behavior` event → telemetry.

**V. Restart** — clear in-memory `commerce/fan_knowledge._knowledge_mem` then re-derive, still correct via DB fallback.

**W. Generic creator** — Mia persona (22, Los Angeles, model) → `PERSONA BEHAVIOR` uses Mia's `communication` not Sunny's.

**X. No Sunny singleton** — `creator_id=Mia` with `Mia` metadata must not get `ABOUT SUNNY` nor `Sunny Skye` in CREATOR PERSONA; verify `Sunny Skye` absent.

**Hostile edge**: empty persona {}, legacy instructions only, missing behavioral_rules/emotional_behavior/communication, malformed metadata, one-word response `hi` (voice short but pass), emoji-heavy `😭😂💕😭😂` (spam), uppercase `HELLO`, formal, multiple `?`, zero `?`, repeated phrases, XAUTOCLAIM duplicate generation_id processed twice.

---

## 9. Files to Change

**Create**:
- `commerce/persona_behavior.py` — `PersonaBehaviorState`, `derive_persona_behavior_state(structured, conversation_state, fan_msg, fan_knowledge, recent_assistant, commerce_state)` pure, deterministic, generic. ~180 lines.
- `commerce/persona_validation.py` — `PersonaResponseValidation`, `validate_persona_voice(response, persona, behavior_state, recent)` pure, O(n) cheap. ~150 lines.

**Modify**:
- `workers/llm_worker.py:499 process_message` — import derivator/validator, call after `context = await build_qwen3_context` + after `build_conversational_commerce_state` (before Qwen) to derive + append `PERSONA BEHAVIOR:` system msg; after `draft = await generate_draft` call `validate_persona_voice` then inject deterministic `breaks_persona_fact` flag into `score_draft` `flags` (extend `HARD_FLAGS`). Add telemetry fields `persona_version, emotional_state, voice_score` to `GenerationTelemetry` (extend via `telemetry.py` already has generic `decision_trace`). Publish `persona.behavior` event via `core/event_bus.publish_event` (like `ai.generation_started`). No new LLM call. ~80 lines delta.
- `memory/context.py:504` — no direct change needed if llm_worker does injection; alternative is to add `persona_behavior` param to `build_qwen3_context` but keeping worker injection simpler and less invasive to context tests — **choose worker injection** to avoid changing context signature (preserve existing tests). Context already handles prior behavioral block; worker append is final.
- `core/scoring.py:11` — extend `HARD_FLAGS` with `breaks_persona`, `breaks_persona_fact` (deterministic fact violation → `min(composite,0.1)`). ~2 lines.
- `core/telemetry.py` — add optional `persona_version, emotional_state, voice_score, naturalness_score` fields (generic, no DB). ~10 lines.
- `chatbotv2/dashboard/routes/live.py` or `chatbotv2/dashboard/event_subscriber.py` — consume `persona.behavior` and forward to live panel (reuse ws_manager). ~30 lines, or just log and expose via existing `ai_intel` route (minimal).
- `tests/test_phase43d_behavioral_fidelity.py` — new, 24 test classes A-X + hostile edge, 39+ tests, deterministic, mocked PG/Redis.

**Alternative location considered**: `memory/persona_behavior.py` vs `commerce/persona_behavior.py` — `commerce` chosen because `commerce/persona_behavior.py` keeps deterministic, creator-scoped, bounded logic near existing `fan_knowledge`, `temporal_context`, `conversation_operations`, `adaptive_optimization` (all commerce/ deterministic). `memory` currently holds `creator_persona.py` storage, not behavior; behavior is execution-time.

---

## 10. Files NOT to Change

- `db/schema.sql`, `db/migrations/*` — persona storage already correct, no new columns.
- `db/postgres.py` — `get_structured_persona_async` already creator-scoped versioned.
- `db/redis.py` — cache already per-creator with version.
- `memory/creator_persona.py` — build_sunny_persona and render_persona_block remain authoritative; no Sunny singleton.
- `core/persona_self.py` — guard `render(None)==""` already correct, keep.
- `chatbotv2/handlers.py`, `chatbotv2/dashboard/routes/personas.py` — isolation already fixed.
- `core/llm_provider*`, `core/gemini_client`, `core/config` — no new provider, keep single Qwen (qwen3:4b) authoritative.
- `commerce/execution.py`, `commerce/pipeline`, `commerce/dao`, `commerce/fan_knowledge`, `commerce/temporal_context`, `commerce/long_term_memory` — reuse, not redesign.
- `canary` config (`core/config.py:128 ai_agent_canary_enabled false`), `agent/*` — not activated.
- No new queue/stream, no worker, no Telethon replacement.

---

## 11. Risk & Why This Map Is Minimal

- **Token cost**: Added `PERSONA BEHAVIOR:` 3-5 lines (~60 tokens) negligible vs 19k persona. Validation is O(response) ~200 tokens, no DB scan beyond already-fetched. Performance <1ms.
- **Idempotency**: `generation_id` MD5 preserved; derivator re-derives same state for same `generation_id`+`context` (deterministic), so XAUTOCLAIM replay does not duplicate event (event_id dedup via bus).
- **Isolation**: Derivator reads `structured persona` by `creator_id`, `fan_knowledge` by `creator_id:user_id`, so Creator A Sunny vs B Mia fully isolated; test X proves Mia does not get Sunny behavior.
- **No template injection**: Behavioral block is bounds-based (`occasional emoji allowed`, `question optional`), not `append 😭` every message, so natural variation preserved, unlike blind rewriter.

**STOP — Stage A complete. Awaiting approval before Stage B implementation.**

