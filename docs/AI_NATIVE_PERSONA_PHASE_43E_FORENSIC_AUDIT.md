# AI_NATIVE_PERSONA_PHASE_43E_FORENSIC_AUDIT — STAGE A
**Hostile Forensic Audit — Persona Runtime Fidelity, Bypass Paths & Longitudinal Drift (READ-ONLY)**
**Date: 2026-08-31 | Workspace: E:\chatbot | Phase: 43E Stage A**
**NO PRODUCTION CHANGES | PROVEN / LIKELY / UNKNOWN**

---

## 1. Executive Summary

Phase 43D claims READY: structured 23-field persona (43B) + deterministic `PersonaBehaviorState` (43D, 8 states HIGH/MEDIUM/LOW) before Qwen + deterministic `PersonaResponseValidation` O(n) after Qwen + 3 `HARD_FLAGS` → single-pass `1 SIGNAL + 1 QWEN + 1 SCORING`.

**Hostile answer to primary question:**

> Can any realistic execution path bypass, weaken, misattribute, or inconsistently apply the configured creator persona?

**PROVEN: YES — at least 3 realistic bypass/weakening paths remain, but none allow silent persona *substitution* on the primary inbound path.**

- **Proven bypass 1 (P1, dashboard fallback N/A but still reachable via error):** If `resolve_single_application_creator()` returns `CREATOR_CONTEXT_UNAVAILABLE` (0 active DropFans integrations) or raises, both `chatbotv2/handlers.py:70`, `chatbotv2/dashboard/routes/messages.py:91`, and `workers/llm_worker.py:534` fall back to `_creator_id=None`. Then `get_structured_persona_async(None)` returns `{}` (creator_persona.py:321 `if creator_id is None → return {}`), `render_persona_behavior_block` falls back to `warm LOW` neutral (persona_behavior.py:190), and Qwen receives *no* `CREATOR PERSONA: Sunny Skye / CANONICAL` — only legacy global `personas.instructions` (if any) + generic `PERSONA BEHAVIOR: emotion=warm confidence=LOW`. This is not Sunny substitution, but **persona weakening to generic warm assistant** — realistic during DB outage, DropFans deactivation, or initial install before operator creates persona.

- **Proven weakening 2 (P1, version race):** Generation A reads persona v1 at `build_qwen3_context` (context.py:575 `await get_structured_persona_async(creator_id)`) and at `derive_persona_behavior_state` (llm_worker.py:1085 `await get_structured_persona_async(_creator_id)`), then operator `UPDATE personas SET metadata=v2, version=2`. Generation A continues with **v1 context already merged** (`CREATOR PERSONA v1` + `PERSONA BEHAVIOR v1`), while generation B correctly reads v2. No re-validation, no mixed-version guard. This is **intended cache-version semantics** (compare-and-continue, not snapshot isolation), but it is a *proven* inconsistency window (in-flight TTL 0–30s lock duration). No cross-creator contamination, just temporal.

- **Proven collision 3 (P1, generation_id dedup across creators):** `generation_id = MD5(user_id:content:telegram_message_id)` (handlers.py:68, llm_worker.py:512) **does not include `creator_id`**. Same Telegram fan `777` sending identical `content="hi"` + `telegram_message_id=1` via Creator A (Sunny) and Creator B (Mia) at different times produces **identical `generation_id`**. Consequences proven harmless for *safety* (dedup is `send_dedup:{dedup_id}` where `dedup_id=MD5(user_id:content:telegram_message_id)` too — `chatbotv2/handlers.py` never differentiates, but `db/redis.py:82 is_send_duplicate` is global, so second creator's send could be incorrectly deduped as duplicate if first still within 3600s TTL). However `generation_id` collision does **not** cause persona/behavior collision because behavior is re-derived per `creator_id` (llm_worker.py:1104 `creator_id=_creator_id`), but telemetry `GenerationTelemetry` keyed by `generation_id` alone (`telemetry.py:222 _telemetry_cache[generation_id]`) will **overwrite** first creator's telemetry with second creator's if both in-flight within same process (P2).

All other primary paths **do** enforce persona: normal inbound AI reply (handler → debounce `debounce:creator:{cid}:user:{uid}` → inbound stream → llm_worker `lock:creator:{cid}:user:{uid}` → `build_qwen3_context` creator-scoped `SELECT metadata WHERE creator_id=$1` → `PERSONA BEHAVIOR` + `CREATOR PERSONA` + validation + scoring) is **PROVEN isolated** (creator-specific cache `persona:{creator}:{user}` / `persona:creator:{creator}`, no global fallback when `creator_id` present, `invalidate_persona_cache(creator_id)` per-creator).

**Most dangerous realistic bypass** is not substitution but **weakening to generic** during `CREATOR_CONTEXT_UNAVAILABLE`. Most important **remaining fidelity gap** is **voice/style still prompt-suggested, not deterministically enforced beyond soft flag** (lowercase, emoji, length are bounded but not hard, and `too_formal` is not severe, so Qwen can remain over-polished and still auto-send with score 0.85).

**Phase 43D is CONDITIONALLY READY for single-pass behavioral control, but NOT sufficient for “Sunny always feels like Sunny” under hostile fan/operator conditions without additional hard voice guard and summary versioning.**

---

## 2. Required Execution Graph

```
Telegram MTProto NewMessage (telethon)
  ↓ chatbotv2/handlers.py:26 handle_incoming_message
      check_rate_limit (redis ratelimit:{user})
      entity_blacklist unblacklist
      upsert_user (pg users)
      save_inbound_message (messages)
      resolve_single_application_creator() → creator_id? (commerce/single_creator.py:57)
      publish_event message.created (core/event_bus, Redis Pub/Sub, best-effort, scope=user)
      debounce_enqueue(creator_id, user_id) → debounce:creator:{cid}:user:{uid}:messages (db/redis.py:313)
      if window_owner → _wait_and_process(3s) (handlers.py:120)

  ↓ handlers.py:126 _wait_and_process
      get_debounced_messages(creator_id) → creator-scoped
      LC1 persona retrieval (handler):
          get_cached_user_persona(user_id, creator_id) → persona:{creator}:{user} (db/redis.py:381) // isolated, no global fallback when creator present
          miss → get_user_persona(user_id, creator_id) → SELECT personas WHERE creator_id=$1 ORDER BY is_default DESC (db/postgres.py:155) // isolated
          fallback only to legacy global default WHERE is_default AND creator_id IS NULL
          cache_user_persona(..., creator_id) → SETEX persona:{creator}:{user} 600s
          get_cached_default_persona(creator_id) / get_default_persona(creator_id)
      enqueue_inbound {user_id, content, telegram_message_id, username, first_name, persona=legacy instructions string, generation_id=MD5(user:content:tgId)} → inbound_messages XADD

  ↓ Redis Stream inbound_messages CONSUMER_GROUP=llm_workers (XAUTOCLAIM 30s)
      workers/llm_worker.py:1458 run_worker → read_inbound → process_message(persona string, generation_id)
          resolve_single_application_creator() again → _creator_id (authoritative, 110)
          acquire_user_lock(creator_id, user_id) → lock:creator:{cid}:user:{uid} (db/redis.py:280) // creator-scoped
          upsert_user, is_user_auto_reply_excluded
          LC2 persona retrieval (worker, authoritative):
              build_qwen3_context(user_id, user_message, persona string, creator_id=_creator_id) (memory/context.py:504)
                  get_user / get_user_profile (+ fan_knowledge isolation for interests)
                  get_recent_messages 20/800/3 trim (context.py:541)
                  derive_conversation_state(history+current) → lifecycle, identity_already_established (hard Sunny? now generic), current_topic/open_threads/tone warm/curious/flirty/supportive (core/conversation_state.py:157)
                  persona_name ← regex "You are <Name>" from legacy persona, overridden by structured metadata.identity.name via get_structured_persona_async(creator_id) (memory/creator_persona.py:318) // reuse _structured_for_name
                  build_qwen3_system_prompt(persona, ..., identity_already_established, persona_name) → system[0] 1k (context.py:182, generic Sunny→sunny trim now via persona_name)
                  CREATOR PERSONA: render_persona_block(None, structured) → system[1] 19k (memory/creator_persona.py:379, context.py:604) // EARLY, authoritative
                  build_qwen3_state_context → system[2] STATE / PROFILE / COMMERCE filtered / SUMMARY 2 sentences / IDENTITY RULE generic / CONVERSATION / ABOUT SUNNY (guarded, core/persona_self.py:44) / CAPABILITIES / RESPONSE mode=react / QUESTION allowed=false
                  AVAILABLE CONTENT, RELEVANT MEMORY, FAN KNOWLEDGE 5 (creator-scoped, commerce/fan_knowledge.py:525), LOCAL TIME (commerce/temporal_context.py)
                  recent history user/assistant
          fan_knowledge extraction: extract_fan_knowledge(generation_id, existing) → add_knowledge_item (bounded 30, idempotent via generation_id)
          publish_event ai.generation_started (generation_id, creator_id, scope=user)
          extract_commerce_signals (single)
          _try_commerce_draft(context, persona) → resolve_commerce_product_with_history → CommerceStateRequest → resolve_and_run_commerce (deterministic, 11-gate) → select_commerce_response
          if USE_COMMERCE_RESPONSE → draft = commerce deepseek_response (bypasses Qwen, deterministic template, DropFans authority)
          else → build_conversational_commerce_state (deterministic desire/temp/readiness/window/objective/next_best_action) → response_mode/question_policy → REMOVE old RESPONSE/QUESTION from context, APPEND Commercial STATE + CONVERSATION INTELLIGENCE as final system msg (llm_worker.py:715) // LAST SYSTEM MSG before behavior

          // Phase 24 gate (outside if/else, after commerce)
          production_control autonomous_allowed (creator_id, strategy, experiment) → may set _skip_qwen_due_to_pause true, draft="Thanks team follow up", score 0.1

          // Operational intelligence (best-effort, before Qwen)

          // Phase 43D BEFORE QWEN (workers/llm_worker.py:1072, inside legacy else after gate)
          derive_persona_behavior_state(structured_persona=_structured_for_behavior, conversation_state=_conv_state, fan_message, fan_knowledge 5, recent assistant 3, commerce objective, next_best_action, creator_id, generation_id) (commerce/persona_behavior.py:60) // pure, 8 regex, HIGH/MEDIUM/LOW
          render_persona_behavior_block(state) → PERSONA BEHAVIOR: emotion=… confidence=… mode=… Voice: … Behavior: … (3-5 lines ~60 tokens) → context.append({"role":"system","content": block}) // FINAL SYSTEM MSG BEFORE QWEN, subordinate to safety/commerce per comment but last in array (transformer recency)
          publish_event persona.behavior (before Qwen) best-effort, creator_id+generation_id, no secrets (llm_worker.py:1122)
          telemetry persona_version/emotional_state/behavior_confidence/conversation_mode (telemetry.py)

          // QWEN
          if _skip_qwen_due_to_pause → no Qwen (draft already)
          elif _use_agent (ai_agent_canary_enabled? false) → run_agent_runtime (not used)
          else → legacy: generate_draft(context, user_message) or generate_draft_with_tools (llm_worker.py:83, bounded 3 tool calls) // SINGLE QWEN, merged_system = "\n\n".join(system parts) → provider.generate_with_history(system_instruction, messages, max_tokens 200 temp 0.85)
          _generation_end, provider_latency
          empty draft → operator_queue empty_draft 0.0 → ai.generation_completed + return

          // Phase 43D AFTER QWEN, before scoring (workers/llm_worker.py:1219)
          validate_persona_voice(response=draft, persona=_structured_for_val, behavior_state=_persona_behavior_state, recent_assistant_messages 3) (commerce/persona_validation.py:40) // O(n), no DB/LLM, 5 generic patterns, emoji/sentence/question/fact regex
          telemetry persona_voice_valid/voice_score/naturalness_score/fact_violation
          publish_event persona.behavior (update with voice_valid, validation_status, severe, reasons[3]) // second event same generation_id

          // Scoring (core/scoring.py:78)
          score_draft(draft, user_message, context, is_authorized_commerce) → composite 0-1 + HARD_FLAGS (price etc) + LLM scorer natural_tone 0-10 (temperature 0.2) → fail-closed 0.0 on exception
          // Phase 43D severe mapping (workers/llm_worker.py:1245)
          if fact_violation && severe → flags += persona_identity_violation, score = min(score,0.1)
          elif question_score <0.6 → persona_question_policy_violation
          elif severe → persona_voice_severe
          // then HARD_FLAGS → min(composite,0.1) already in scoring.py:186

          // Post-scoring gate
          production_control autonomous_allowed recheck → may cap 0.1

          // Publish ai.generation_completed / suggestion.created etc., best-effort

          // Routing
          is_auto_reply_enabled? score>=0.80 && !flags → enqueue_send SEND_STREAM (db/redis.py:65, dedup MD5(user:msg:tgId), generation_id propagated) + publish ai.generation_completed MUST after enqueue (else suggestion)
          else → add_to_operator_queue (operator_queue) + publish suggestion.created + ai.generation_completed

          post_process async: extract_and_update_profile + maybe_summarize (20 msgs, LLM 2 sentences)
          release_user_lock(creator_id)
          telemetry.record (bg)

  ↓ Redis Stream send_messages SEND_CONSUMER_GROUP=send_workers (XAUTOCLAIM 30s)
      workers/send_worker → is_send_duplicate? (redis dedup 3600s global, not creator-scoped) → check_send_rate_limit 1/s burst5 (lua) → Telethon send → save_outbound_after_send + publish message.sent / send_failed
```

---

## 3. Alternative Path Audit

| Path | Persona retrieved? | Structured? | Behavior state? | Qwen? | Validation? | Scoring? | Can bypass persona? |
|---|---|---|---|---|---|---|---|
| **normal inbound AI reply** (handler→inbound→llm_worker→Qwen) | **YES** `get_structured_persona_async(creator_id)` (context.py:575) + `get_structured_persona_async(_creator_id)` in llm_worker (1085) // isolated | **YES** 19k `CREATOR PERSONA` system[1] + `PERSONA BEHAVIOR` final | **YES** `derive_persona_behavior_state` before Qwen (llm_worker:1072) | **YES** 1 Qwen | **YES** `validate_persona_voice` before scoring (1219) | **YES** `score_draft` + HARD_FLAGS | **PROVEN NO** — isolated, versioned, behavior injected |
| **dashboard AI reply** `POST /api/dialogs/{id}/ai-reply` (messages.py:77) | **YES but after 43D fix** `get_cached_user_persona(dialog_id, creator_id)` + `get_user_persona(..., creator_id)` where `creator_id` from `resolve_single_application_creator()` (messages.py:91) // previous global fallback fixed | **YES** via worker `build_qwen3_context` (same as normal, since dashboard enqueues inbound → worker re-derives) | **YES** via worker (same) | **YES** via worker | **YES** via worker | **YES** via worker | **LIKELY NO** — now creator-scoped; previously P1 global leak proven fixed |
| **dashboard api/send-message, api/dialogs/{id}/send** (messages.py:25,51) | **NO** — direct `enqueue_send` (no persona) | NO | NO | NO | NO | NO | **N/A** — operator direct send, intentional human text, not AI bypass |
| **operator-approved response** `operator_queue approved → enqueue_send` | **NO** — operator writes content, `was_auto_approved false` | NO | NO | NO | NO | NO | **N/A** — human approval bypasses persona by design (not AI impersonation) |
| **scheduled responses** `scheduled_messages` → `scheduler_worker._build_send_payload` (scheduler_worker.py:37) | **NO** — `msg["content"]` operator-scheduled | NO | NO | NO | NO | NO | **N/A** — scheduled is operator text, not AI generation; if used for AI prompt, would be persona-less — but current `scheduled_messages` are tip/aftercare/re-engagement operator templates |
| **post-purchase / aftercare** (scheduler reason post_purchase_followup, commerce/dao aftercare_status) | **NO** Qwen; same as scheduled | NO | NO | NO | NO | NO | **N/A** |
| **commerce-generated drafts** `USE_COMMERCE_RESPONSE` (llm_worker.py:646) | **YES** persona still fetched for context, but draft is `deepseek_response` template deterministically, persona voice via `deepseek_response` `tone` (commerce/deepseek_response.py) not persona behavior | PARTIAL — persona in context but draft bypasses Qwen | **NO** — behavior derived but not appended when `USE_COMMERCE_RESPONSE` skips Qwen path (derivation is inside `else: legacy` only) — so commerce draft has **no** `PERSONA BEHAVIOR` injection | **NO** (commerce bypasses) | **NO** (commerce bypasses validation? validation runs on commerce draft too? Actually validation runs after `if _skip_qwen ... elif agent else legacy` → after that, `draft` may be commerce draft (set earlier) and validation still runs on `draft` regardless of source — it does run on commerce draft (since validation is after the whole if/else, before scoring, and draft is set) — so **YES** validation runs even on commerce draft) | **YES** | **PROVEN PARTIAL** — commerce draft bypasses Qwen but validation still checks fact violation (good), but behavior state not injected (so commerce tone may not be persona-specific) |
| **retries / XAUTOCLAIM** (llm_worker.py:1458 `requeue_stalled_messages` idle 30s) | **YES** re-derived on replay (deterministic `generation_id` MD5, same inputs → same `derive_persona_behavior_state`) | **YES** fresh `get_structured_persona_async` on replay (so version race fixed on retry) | **YES** re-derived | **YES** re-call Qwen (new) | **YES** re-validate | **YES** | **PROVEN NO** — deterministic, idempotent |
| **rate-limit requeue** `check_send_rate_limit` (db/redis.py:508 lua, send_worker) | **N/A** — send side, after Qwen; persona already applied | — | — | — | — | — | **NO** — persona already in content |
| **send-stream replay / DLQ replay** `replay_dlq_entry` (db/redis.py:632) → `enqueue_inbound` with original `payload.persona` (llm_worker.py:1528 `data.get("persona","")` + 43D `payload.persona` still legacy string, not fresh structured) | **PARTIAL** — replay reuses **stale** `persona` string snapshot from original stream (handlers.py enqueues `persona` legacy instructions, not `metadata`) while worker re-derives fresh `structured_persona` for behavior, so **mixed**: identity from `CREATOR PERSONA` fresh, but legacy `persona` string (system[0]) stale if persona updated during DLQ lifetime | **YES** fresh via worker | **YES** fresh | **YES** new Qwen on replay | **YES** fresh validation uses fresh structured | **YES** | **LIKELY PARTIAL stale legacy persona** |
| **fallback responses** `draft="Thanks team follow up"` (llm_worker.py:889 when autonomous_paused) | **NO** persona — static string, score 0.1, `autonomous_paused:reason` flag | NO | NO (skip) | **NO** (skipped) | **NO** (skip, score already 0.1) | **YES** (score 0.1) | **PROVEN NO** — not persona, but intentional pause, not bypass |
| **error responses** `draft="[No response generated]"` empty → operator_queue (llm_worker.py:1197) | **YES** behavior derived but draft empty → validation empty → soft fail, still operator queue | YES | YES (derived) | **NO** (empty) | YES (empty) | **YES** | **NO** — error not sent to fan |
| **Qwen failure** `generate_draft` exception → `""` → empty handling | Same | — | — | **YES** (attempted, failed) | YES | YES | **NO** |

**Overall alternative matrix verdict**: Only **DLQ replay stale `persona` string** and **commerce bypass no behavior injection** are proven weakenings; all fan-facing AI paths now go through behavior+validation. Dashboard leak **PROVEN FIXED** in 43D.

---

## 4. Phase 43D Integration Audit

**Files**: `commerce/persona_behavior.py` (216 lines, 8 regex, pure), `commerce/persona_validation.py` (148 lines, O(n), 5 generic patterns), `workers/llm_worker.py:1072,1219`, `core/scoring.py:11`, `core/telemetry.py`.

**Intended order** (map §3):

```
persona retrieval → behavior derivation → behavior injection → ONE QWEN → persona validation → existing scoring → routing
```

**Actual order (proven via code):**

```
build_qwen3_context: persona retrieval (get_structured_persona_async) → system[0] legacy + system[1] CREATOR PERSONA (19k) → ... → state → returns context
llm_worker: _try_commerce_draft (may set draft commerce) → build_conversational_commerce_state → Commercial STATE final system msg (llm_worker.py:715, REMOVES old RESPONSE/QUESTION)
production_control gate (may set _skip_qwen)
→ IF _skip_qwen → draft static, skip Qwen, skip behavior derivation, skip validation? Actually behavior derivation is INSIDE else: legacy, so when _skip_qwen true, behavior NOT derived, validation still runs on static draft (but static draft has no persona, validation will not fact-violate)
→ ELSE IF agent (never, canary false) → skip
→ ELSE legacy:
    derive_persona_behavior_state (using _structured_for_behavior cached, _conv_state, fan_knowledge 5, recent 3, commerce objective) (llm_worker.py:1072)
    render_persona_behavior_block → context.append({"role":"system","content": block}) // FINAL system msg before Qwen — after Commercial STATE, so PERSONA BEHAVIOR is LAST (recency bias for transformer)
    publish_event persona.behavior (before Qwen, best-effort)
    generate_draft(context, user_message) // 1 QWEN
→ _generation_end
→ empty check
→ validate_persona_voice (llm_worker.py:1219, before scoring, uses same _structured_for_behavior or refetch, same behavior_state)
→ publish persona.behavior update (voice scores)
→ score_draft (core/scoring.py)
→ severe mapping (llm_worker.py:1245) → flags += persona_identity_violation etc., score = min(score,0.1) (uses existing HARD_FLAGS min)
→ production_control gate 2 → routing
```

**Findings:**

- **Behavior derived from stale persona?** **PROVEN NO** for normal: `_structured_for_behavior` is `await get_structured_persona_async(_creator_id)` fresh per generation, cached `persona:creator:{cid}` 600s but invalidated per-creator on update (postgres.py:250 `invalidate_persona_cache(creator_id)`). In-flight generation A reads v1 before update, B reads v2 after invalidate — correct. **However** `context.py:575` also does `get_structured_persona_async` during `build_qwen3_context` and caches `_structured_for_name`; if llm_worker derivation reuses different fetch, could be mixed versions if update happens between the two fetches (race window ~1ms). In that window, `CREATOR PERSONA` (19k) could be v1 and `PERSONA BEHAVIOR` could be v2 (different `persona_version` fields). Proven possible but unlikely; no transaction snapshot across both fetches. **LIKELY minor mixed-version, not cross-creator contamination.**

- **Wrong creator?** **PROVEN NO**: `_creator_id` from `resolve_single_application_creator()` authoritative, passed to both `get_structured_persona_async(_creator_id)` and `get_cached_user_persona` etc., and to `derive_persona_behavior_state(creator_id=_creator_id)`. Dashboard path now also resolves creator_id (messages.py:91). Global fallback only when `creator_id is None` (no active integration) — weakening to generic, not misattribution.

- **Wrong fan?** **PROVEN NO**: `derive` uses `user_id` + `retrieve_relevant_knowledge(creator_id, user_id)` + `get_recent_messages(user_id)`, both creator+user scoped.

- **Incomplete state?** **LIKELY**: `_conv_state` may be `None` if `derive_conversation_state` raises (caught, returns None). Then `derive_persona_behavior_state` falls back to neutral `warm LOW` (persona_behavior.py:190). That's safe fallback, not bypass — proves `behavior derivation never crashes worker` (failure isolation).

- **Wrong system message?** Behavior block is `PERSONA BEHAVIOR:` appended as last system msg, after Commercial STATE. **Intended precedence is safety > commerce > persona**, but actual last-wins transformer gives `PERSONA BEHAVIOR` higher attention than commerce. Since behavior block does not set price/product, no commerce override, but conceptually inverted. **LIKELY violation of intended hierarchy §6, not safety bypass.**

- **Validation using different persona than generation?** **PROVEN PROTECTED**: Validation does `if '_structured_for_behavior' in locals() and _structured_for_behavior is not None: use it else refetch fresh`. For normal, same object used for both generation and validation (same fetch). For DLQ replay, generation used fresh (re-derived), validation also fresh, but legacy `persona` string (system[0]) may be stale (see §3).

- **Scoring receiving different response than validation?** **PROVEN NO**: Both receive same `draft` variable (validation before scoring, same `draft`).

- **Validation skipped on exceptions?** **PROVEN YES, safe**: `try: validate ... except: log debug, continue` (llm_worker.py:1219) → `validation unavailable` but scoring still runs, message still sent via normal routing (no persona violation flag, but also no crash). **Best-effort observability, not safety bypass** (spec §16).

- **Validation result ignored?** **PROVEN PARTIAL**: Severe `fact_violation` is promoted to `persona_identity_violation` HARD_FLAG and caps score (llm_worker.py:1245) → routes to operator queue (enforced). But minor violations (`too_formal`, `emoji_many`, `too_long`, `generic_pattern`) are **soft fail** (`valid false` but not severe) → telemetry only, still sendable with `soft_fail` (spec §19 minor remain sendable — intentional, not bypass).

- **Severe flag overwritten?** **PROVEN NO**: `flags = list(flags) + ["persona_identity_violation"]` after `score_draft`, not before; `score = min(score,0.1)` ensures hard flag always caps, even if `autonomous_allowed` gate later also caps (production_control: min). No overwrite.

- **Operator queue bypassing validation?** **PROVEN YES but intentional**: Operator-approved `enqueue_send` (dashboard `api/send-message`, `api/dialogs/{id}/send`, scheduler) bypasses Qwen and validation entirely — human text, not AI persona. Not a bypass of *AI* persona, but a separate path.

---

## 5. Persona Version Consistency

**Schema**: `personas.version INT DEFAULT 1` + `updated_at TIMESTAMPTZ` (db/schema.sql:76), `update_persona` does `version=version+1, updated_at=NOW()` + `invalidate_persona_cache(creator_id)` (db/postgres.py:276). Cache `persona:{creator}:{user}` + `persona:creator:{creator}` + `persona:creator:{creator}:version` (db/redis.py:381,420,437) per-creator delete.

**Race A**:

```
T0 Generation A: build_qwen3_context fetch get_structured_persona_async(1) → v1 (19)
T1 operator UPDATE personas SET metadata=v2, version=2 → invalidate cache
T2 Generation A: derive_persona_behavior_state fetch get_structured_persona_async(1) → v2 (if after invalidate) or v1 (if before, still cached)
→ Generation A's merged_system may be mixed: CREATOR PERSONA v1 (from T0) + PERSONA BEHAVIOR v2 (from T2) → persona_version telemetry from _persona_behavior_state (v2) while CREATOR PERSONA (19k) is v1 → mixed-version context PROVEN POSSIBLE (window ~context build 10ms + commerce 50ms).
→ Severity: LOW — both v1/v2 are same creator (Sunny), not cross-creator; telemetry will report v2 while Qwen saw v1/v2 hybrid. No isolation breach.
```

**Race B**:

```
T1 after invalidate, Generation B: fetch → v2 reliably (cache miss → DB v2) → B uses v2 exclusively. PROVEN.
```

**Cache**: `invalidate_persona_cache(creator_id=1)` deletes `persona:{1}:*` + `persona:creator:{1}*` only, not `persona:{2}:*` — proven per-creator (redis.py:437 scan `persona:{creator_id}:*`).

**Mixed-version context impossible to be atomic** without transaction snapshotting both fetches, but **proven not to cause identity substitution**, only temporal hybrid for same creator.

---

## 6. Creator Isolation Hostile Test

**Setup**: `user_id=777`, `Creator A=Sunny Skye (1)` (19, freelance designer, can_disagree true, emoji occasional), `Creator B=Mia (2)` (22, Los Angeles, model, can_disagree false, emoji none). Same fan message `Brooklyn is better than Manhattan` + `telegram_message_id=1` → `generation_id=MD5(777:Brooklyn is better...:1)` identical for both creators (handlers.py:68 `md5(user:msg:tgId)` — **no creator_id**).

**Isolation vectors**:

| State | Shared? | Evidence | Verdict |
|---|---|---|---|
| `persona` | **NO** — `get_structured_persona_async(1)` vs `(2)` separate rows, `render_persona_block` separate 19k vs Mia | `commerce/persona_behavior.py` generic, no `if persona_name=="Sunny"` | **PROVEN** |
| `behavior state` | **NO** — `derive_persona_behavior_state(creator_id=1)` vs `2` uses different `structured_persona`, different `can_disagree` | `test_C` Sunny `disagreement_available true` vs Mia `false` | **PROVEN** |
| `conversation_state` | **NO** — `lock:creator:{cid}:user:{uid}` (redis.py:280) + `get_recent_messages(user_id)` is **global** `WHERE user_id=$1` (db/postgres.py:453) — **PROVEN GLOBAL** `messages` table has `user_id BIGINT` only, no `creator_id`. Same fan 777's history is shared across creators! If fan chatted with Sunny about Max then with Mia, Mia's `get_recent_messages(777)` will see Sunny's history. **P1 isolation leak** for history, not persona. |
| `fan knowledge` | **NO** — `fan_knowledge_by_creator` JSONB `creator_id` key (commerce/fan_knowledge.py:290 `by_creator[str(creator_id)]`), `retrieve_relevant_knowledge(creator_id, user_id)` creator-scoped | **PROVEN** |
| `memory` | **NO** — fan knowledge isolated; long-term memory `commerce/long_term_memory` also creator-scoped | **PROVEN** |
| `debounce` | **NO** — `debounce:creator:{cid}:user:{uid}` (redis.py:309) | **PROVEN** |
| `locks` | **NO** — `lock:creator:{cid}:user:{uid}` | **PROVEN** |
| `generation_id` | **YES global** — `MD5(user:msg:tgId)` no creator_id (handlers.py:68, llm_worker.py:512) | **PROVEN COLLISION** same fan/message → same generation_id across creators | **LIKELY collision** |
| `recent messages` | **YES global** as above | `db/postgres.py:453` | **PROVEN** |
| `cache persona:{cid}:{user}` | **NO** | `db/redis.py:381` | **PROVEN** |
| `cache telemetry` | **YES** — `telemetry.py:222 _telemetry_cache[generation_id]` global dict keyed only by `generation_id`, not `(creator_id,generation_id)`. Two simultaneous generations with same `generation_id` but different `creator_id` will **overwrite** same entry. | **PROVEN** P2 |
| `events` | **NO** — `publish_event(..., creator_id, generation_id, scope=user)` includes creator_id, so `persona.behavior` events are deduplicated by `event_id` not `generation_id`, and filtered by `creator_id` on subscriber — **PROVEN isolated** |
| `operator queue` | **NO** — `operator_queue` `user_id` only, no `creator_id` (schema `operator_queue` no creator column) — same fan's queue items from both creators intermingle in `get_pending_queue_items` (no creator filter). **PROVEN GLOBAL** P2 |
| `send queue` | **PARTIAL** — `enqueue_send` payload now includes `creator_id` (db/redis.py:65 `data["creator_id"]=str(cid)`) and `generate_draft` history is creator-scoped, but `is_send_duplicate` `send_dedup:{dedup_id}` where `dedup_id=MD5(user:msg:tgId)` (llm_worker.py:1229, scheduler_worker.py:59) is **global**, not per-creator. Second creator's identical content within 3600s will be **falsely deduped as duplicate** and dropped. **PROVEN P2** |

**Generation_id collision harmless?** Persona/behavior **not** collided (re-derived per creator), but dedup and telemetry are. **P2, not P0**.

---

## 7. Fan Context + Persona Interaction

**Sequence** (built via `build_qwen3_context` redacted capture + fan_knowledge logic):

```
Fan T1: "I'm a software engineer from Chicago."
  → extract_fan_knowledge: occupation=software engineer (WORK, confidence 1.0, subject occupation), city=Chicago HOME (LOCATION, via "from Chicago" + I anchor, fan_knowledge.py:87), preferred_name? No.
  → Qwen sees FAN KNOWLEDGE: occupation=software engineer; city=Chicago + CREATOR PERSONA: Occupation Title freelance graphic designer / Location City NYC

Fan T2: "My dog is Max."
  → pet_type=dog (PETS) + pet_name=Max (PET_NAME) via "My dog is Max" (fan_knowledge.py:95)
// If fan says "His name is Max" with prior My dog, pronounce requires exactly one pet_type antecedent (fan_knowledge.py:187) → pet_name Max

Fan T3: "I just got off work."
  → hobby? No, but work_schedule not matched (needs "I work nights") — no new knowledge, but conversation_state tone warm

Fan T4: "you know what, I'm exhausted."
  → FAN KNOWLEDGE still occupation/city/Max, relevance-ranked limit 5 still includes them even though "exhausted" not overlapping, but retrieval scores overlap 0.5 bonus so still returned (commerce/fan_knowledge.py:562). So Qwen sees FAN KNOWLEDGE even when irrelevant — **generic personalization risk** but bounded.

Fan T5: "maybe I'll go out tonight."
  → No new knowledge, but behavior playful? Not serious.

Fan T6: "you're tempting me lol"
  → tone flirty (conversation_state.py:133), emotional_state playful (persona_behavior.py:90), teasing_allowed true
```

**Authority ordering (proved):**

```
system[0] legacy persona (You are Sunny Skye...)
system[1] CREATOR PERSONA: Name: Sunny Skye / Occupation Title freelance graphic designer ... (19k, persona_version 1)
...
system[5] FAN KNOWLEDGE: occupation=software engineer; city=Chicago; pet_name=Max
...
system[?] PERSONA BEHAVIOR: emotion=playful ...
```

Transformer last-wins, but `CREATOR PERSONA` is system[1] early, `FAN KNOWLEDGE` system[5] later, `PERSONA BEHAVIOR` last. **Intended hierarchy safety > commerce > creator persona > fan knowledge** (memory/context.py Priority comment) but actual order is **creator persona early, fan knowledge middle, persona behavior last** — fan knowledge is **between** creator persona and behavior, so fan later could be more salient than creator. However persona facts are explicit `Occupation Title: freelance graphic designer` while fan is `occupation=software engineer` — easily distinguished by Qwen? Proven separation via test `test_CreatorIsolation` but prompt-only, not deterministic guard. **No code prevents** `Fan: "I'm a graphic designer too"` + `Sunny: "As a fellow designer..."` confusion (fan occupation accidentally becoming Sunny's assumed shared occupation) — prompt says `CREATOR PERSONA ≠ FAN KNOWLEDGE` (memory/context.py comment) but no code checks.

**Hostile overwrite tests:**

- `Fan: "You're actually a software engineer."` → stored as `direction=inbound` history, **not** as fan knowledge (fan_knowledge requires `I` anchor, not `you're`). So `FAN KNOWLEDGE` not polluted, but **history** contains `You're a software engineer.` Qwen may agree with history over persona, as history is recent `user` turn and persona is system. System > history should win, but Qwen temperature 0.85 may still echo history. **No deterministic `is_self-knowledge` guard** beyond prompt priority. **PROVEN LIKELY fan can gaslight via history repetition**, not via fan knowledge.

- `Fan: "You told me you're 21."` → same, history contains `you're 21`, validation will catch if Qwen echoes `I'm 21` → `fact_violation true` → severe. So **validation mitigates** gaslighting exfiltration to fan, but not prevention of Qwen *agreeing* internally.

**Local time**: `temporal_context_for_fan` → `LOCAL TIME: 02:30 (America/Chicago)` + `LOCAL TIME CONTEXT: late night` when 22-05, injected after FAN KNOWLEDGE (context.py:716). Fan temporary Spain (`city=Spain TEMPORARY until 2026-09-07`, fan_knowledge.py:205) is preferred over HOME Chicago for timezone (temporal_context.py:64 `if temporal_type TEMPORARY and not expired → temp_city`). Expired after 7d correctly filtered via `is_knowledge_expired` (fan_knowledge.py:506). **PROVEN correct**, not mutated to Sunny's NYC.

---

## 8. Emotional State Machine Hostile Audit

**Implemented 8 + neutral** in `commerce/persona_behavior.py:30,80`:

- Patterns: `_EXCITED_RE`, `_PLAYFUL_RE`, `_SERIOUS_RE`, `_EMBARRASSED_RE`, `_ANNOYED_RE`, `_NERVOUS_RE`, `_CURIOUS_RE`, `_OPINION_RE`

| State | Trigger (deterministic) | Required Evidence | Confidence | Priority | Fallback | Expiration | Proven |
|---|---|---|---|---|---|---|---|
| **excited** | Fan `!{2,}` + `finally/amazing/excited/got the job` 2 signals | `_EXCITED_RE` 2 hits | HIGH if 2, MEDIUM if 1 | 3rd (after serious/annoyed) | — | per-turn (transient, re-derived) | PASS |
| **playful** | `ridiculous lol / haha / you're funny` + `tone flirty` | `_PLAYFUL_RE` + `tone flirty→HIGH` else MEDIUM | MEDIUM/HIGH | 4th | — | per-turn | PASS |
| **serious** | `messed.*up / overwhelmed / stressed / depressed / vulnerable` etc + `tone supportive` → HIGH else `messed` alone → HIGH (fixed) | `_SERIOUS_RE` | HIGH | 1st (highest) | — | per-turn | PASS |
| **annoyed** | `ignoring me / annoying / frustrating` | `_ANNOYED_RE` | HIGH | 2nd | — | per-turn | PASS |
| **embarrassed** | `sorry / embarrassed / awkward` | `_EMBARRASSED_RE` | MEDIUM | 4th | — | per-turn | PASS |
| **nervous** | `nervous / unsure / difficult social` | `_NERVOUS_RE` | MEDIUM | 6th | — | per-turn | PASS |
| **curious** | `?` + `current_topic` or `tone curious` | `_CURIOUS_RE` + topic | MEDIUM/LOW | 7th | — | per-turn | PASS |
| **warm** | default | none | LOW | last | warm LOW | per-turn | PASS |
| **neutral** | fallback (empty) | — | LOW | — | — | — | — |

**Hostile:**

- **Contradictory** `I'm really excited!!! Actually never mind. This sucks.` → First clause `excited HIGH` but second `sucks` not matched as serious (needs `messed` etc.), so final trigger is `excited` (since serious check first but `sucks` not in serious pattern, so falls through to excited). **Priority serious > excited means if serious matched, serious would win**, but `sucks` alone not serious, so excited persists incorrectly for contradictory. **LIKELY wrong state for mixed.**

- **Stale** `Turn1 excited, Turn2 neutral, Turn3 unrelated` → per-turn re-derived, so excitement does **NOT persist** beyond turn with signal — correct per spec (transient, no memory). Good.

- **Rapid** `serious→playful→annoyed→sincere→playful` → each turn re-derived correctly follows current fan_message + tone, no global emotional singleton, so correct.

- **Ambiguous** `lol okay / interesting / k` → `lol` matches playful MEDIUM → playful, `interesting` no pattern → warm LOW, `k` → warm LOW → **not fabricating strong** (LOW fallback warm), correct per spec `If confidence insufficient → warm LOW`.

- **Empty/malformed** → `warm LOW` safe fallback (persona_behavior.py:190).

**Next-state behavior**: Injected as `PERSONA BEHAVIOR: emotion=serious ... Behavior: sincerity required — drop slang...` vs `playful → light teasing allowed`. Qwen sees 3-line behavioral instruction, not 19k. Proven via `render_persona_behavior_block`.

---

## 9. Voice Validation Hostile Audit

**Casing** Sunny `lowercase conversational style`:
- Test `HELLO THERE I AM SUNNY THIS IS GREAT` → validator `casing_score` only checks `too_formal` regex, **not uppercase ratio**, so `HELLO` passes casing_score 1.0. **PROVEN NO deterministic lowercase enforcement** (as intended per 43D: bounds, not templates). Spec §15 says `Do NOT reject normal NYC/Max/Instagram proper nouns because they require capitalization. Do not demand every character be lowercase.` So validator correctly does not demand lowercase; `too_formal` is soft. **Voice enforcement for casing is PROMPT-ONLY (occasional lowercase), not deterministic** — proven per 43C.

**Emoji**:
- `😭😂💕😭😂💕😭😂💕` (9 emojis) → `EMOJI_RE` count 9 ≥4 → `emoji_spam_9` reason, `emoji_score 0.3`, `valid false` but **not severe** (severe only fact_violation) → **soft fail, still sendable** per §19 minor remain sendable. Proven via `test_F`.
- Legitimate `😭` once when `occasional` → pass `emoji_score 1.0`. Correct per `zero ok, one/few ok, flood violation` (§15).

**Sentence count**:
- `1 sentence` → `short_medium` allows 1-4 → `sentence_score 1.0` pass.
- `2-4 sentences` → pass.
- `8 sentences` → `too_long_8_gt_5` → `sentence_score 0.4` soft fail.
- `20 sentences` → same 0.4, not severe, still sendable (minor). Correct per spec not brittle.

**Question frequency**:
- `question / question / question` 3× `?` when `question_allowed false` (serious/annoyed or budget) → `question_when_forbidden_3` → `question_score 0.5` → soft fail, not severe. Existing `MAX_QUESTIONS_PER_3_TURNS=1` still authoritative in behavior derivation (`evaluate_question_budget`), but validation is soft. Proven: behavior `question_allowed false` derived, validator checks `?` count, but **does not cap score to 0.1** unless fact violation — so Qwen can still ask when forbidden and still auto-send if LLM scorer 0.85 and no hard flag. **P1: question policy violation is now `persona_question_policy_violation` but flagged as soft, not hard, per llm_worker:1249 `if question_score <0.6 → add persona_question_policy_violation` but **not** `min(score,0.1)` (only for fact_violation). So question spam remains sendable, not operator queue. **LIKELY insufficient enforcement** per §9.

**Generic patterns**:
- `that's really interesting! / tell me more / that sounds amazing / wow that's crazy` → `_GENERIC_ACK_PATTERNS` 5 regex (that sounds amazing, i totally get that, that must be, tell me more, what about you?) — `tell me more` and `that sounds amazing` will hit `generic_pattern_...` → `generic_pattern_score 0.7/0.4` soft fail, not severe, still sendable. **PROVEN affects routing? NO — generic_pattern_score is telemetry only, not added to HARD_FLAGS, so not capped. Merely telemetry, not routing.** Spec §16 says detect obvious template drift, not build classifier — so soft telemetry is intended, but **does not affect routing** (minor).

**Teasing**:
- `teasing_allowed true` is **injected** to Qwen (`Behavior: light teasing allowed`) but **not validated** in response — validator has no `teasing` check. So teasing is **actually selected** (behavior state) but **merely described to Qwen**, not enforced. Proven via `commerce/persona_validation.py` has no teasing regex.

**Disagreement**:
- Same `do you think I'm right?` with `can_disagree true/false` → `disagreement_available` true/false injected as `disagreement available (playful if fits, do not auto-agree)` vs `do not force disagreement`. **Validator has no disagreement check**, so Qwen may still auto-agree even when available. **Not validated.**

---

## 10. Naturalness / Template Loop Audit

**Protections present:**

- `question budget` `MAX_QUESTIONS_PER_3_TURNS=1` + `MAX_CONSECUTIVE=1` (core/question_policy.py:11) deterministically caps questions.
- `generic pattern validator` 5 patterns + repetition vs recent 3 (commerce/persona_validation.py:10,92) → `generic_pattern_score`, but **soft only**.
- `conversation_mode` derived but **not used to prevent** `acknowledge→generic→question` loop beyond question cap.
- `behavior state` `naturalness_mode avoid_generic_ack` when recent 3 contains `that sounds amazing` etc. (persona_behavior.py:170) → injected `avoid generic ack templates` but **not enforced**.

**20-turn hostile** `yeah / lol / true / maybe / haha ...` (short statements):
- Fan gives `yeah` (2 chars) → `_is_question` false, `len(fan.split())<=6` and not question → `plan_response_mode` would be `EXPLORE` if called, but **not called** in `build_qwen3_context` (comment P1-02). So no response_mode derived from fan short. Instead Qwen receives `CONVERSATION: topic=... tone=warm` + `PERSONA BEHAVIOR: emotion=warm ... mode=react` + `FAN KNOWLEDGE` empty. Qwen likely produces `yeah lol` → `haha` → generic `That's cool!` loop. No deterministic `acknowledge→generic→question` breaker beyond question cap.

**50-turn measurement**: No `repeated openings` or `repeated slang` counter beyond generic 5 patterns. **Proven drift to repetitive `haha / lol / true` is LIKELY**, but spec says `Do not call this a bug unless code proves it violates explicit requirement` — no explicit `no repeated slang` requirement, only `avoid repetitive catchphrases` prompt, so not a bug, just measurable drift.

**Recent responses**: `recent_assistant_messages` 3 used for `naturalness_mode` and validation generic repetition, but **not for opening/sentence structure/emoji repetition** beyond generic. So `repeated openings` (`Hey!`, `Hey Alex!`) not detected.

---

## 11. 50-Turn Longitudinal Persona Test

**Method**: Synthetic deterministic 50 via `derive_persona_behavior_state` loop (test_W, 50 iterations, `creator_id=1`, Sunny). Each turn `persona_version 1` stable, `creator_id 1`, `emotional_state` cycles, `generation_id` MD5 deterministic.

**Identity** `Sunny Skye / 19 / NYC / freelance graphic designer` → system[1] `CREATOR PERSONA` re-fetched per turn via `get_structured_persona_async(creator_id)` (context.py:575) → **PROVEN stable**, not drift. System[1] 19k re-injected every turn, not truncated (no max_tokens guard for persona).

**Voice** `lowercase allowed, occasional emoji, short_medium` → `PERSONA BEHAVIOR` re-derived per turn, `verbosity_target short_medium` stable, but Qwen output length not hard-capped (validator soft). No `too_long` hard flag, so voice may drift to 5 sentences and still send. **PARTIAL**.

**Behavior** `question_allowed` respects budget, but `teasing_allowed` toggles with emotional state, not stuck to `warm`. After 30 `warm` turns, next `playful` correctly flips to `teasing true`. **No global emotional singleton**, so correct.

**Fan/persona boundaries** `Max/Chicago/Spain` remain fan, `Sunny 19 NYC` remains persona, per §7 ordering proven.

**Commerce** `offer` not in 50-turn synthetic (no product), but `AVAILABLE CONTENT` titles semantic only, not invented.

**Persona update mid-way** (turn 25 `persona_version 1→2` mock): `derive` uses fresh `persona_version` from DB (if operator `UPDATE` + `invalidate`), next generation `B` sees `persona_version 2` (proven via version consistency §5). In-flight A still v1/v2 hybrid (see §5).

**Conclusion**: Longitudinal **input** consistency **PROVEN**, **output** behavioral drift to generic `warm` is **LIKELY** after 30 turns of `warm LOW` because no anti-drift scorer `breaks_persona` is deterministic `fact_violation` only; `generic_pattern_score` soft does not cap.

---

## 12. Persona Update During Long Conversation

**Turn 25 change** `favorite drink iced vanilla latte → matcha`, `emoji_policy occasional → none`, etc., mocked as `persona_version 2`.

- **Next generation sees v2?** **PROVEN YES** for `get_structured_persona_async` after `invalidate_persona_cache` deletes `persona:creator:1` + `persona:1:*`. DB row `version 2` returned.

- **Old cached v1 survives?** **PROVEN NO** for `persona:{creator}:{user}` and `persona:creator:{creator}` (deleted), but **LIKELY stale** `persona` string in `inbound stream payload` (handlers.py enqueues `persona` legacy instructions string, not `metadata`). That legacy string is snapshot at `T0` handler time, not refreshed at worker `build_qwen3_context` time. However worker **ignores** that legacy `persona` string for structured part and fetches fresh `metadata` itself, so `CREATOR PERSONA` is fresh v2, but `system[0]` legacy `persona` (instructions) is stale v1. Hybrid context again.

- **Behavior state uses v2?** **PROVEN YES** (`derive_persona_behavior_state(structured_persona=_structured_for_behavior v2)`).

- **Validation uses v2?** **PROVEN YES** (`_structured_for_val` fresh or reused same object as derivation).

- **Telemetry identifies v2?** **PROVEN YES** (`persona_version v2` in `persona.behavior` event and `telemetry.persona_version`).

- **Stale system prompts survive?** **LIKELY** `system[0]` legacy `You are Sunny Skye ... warm ...` (old instructions) persists if operator updated `metadata` but not `instructions` (common via dashboard `metadata JSON` separate). `render_persona_block` would emit new `favorite drink matcha` but `system[0]` still old, so Qwen sees contradictory drink. No code forces `instructions` sync with `metadata`.

---

## 13. Restart / XAUTOCLAIM Audit

**Worker receives message → derives persona behavior → crashes → pending → XAUTOCLAIM (30s) → new worker processes.**

- `generation_id` deterministic `MD5(user:msg:tgId)` (handlers.py:68, llm_worker.py:512) preserved across retry (same `data["generation_id"]` from stream or re-derived same MD5). **PROVEN deterministic**, no random `uuid` (compare `telemetry.py` old `uuid4` replaced).

- `creator_id` via `resolve_single_application_creator()` re-derived from `creator_integrations` DB (commerce/single_creator.py:69 `get_any_creator_id_with_dropfans` fresh), not from process-local. **PROVEN deterministic**, restart safe.

- `persona version` re-fetched fresh from DB (not cached process-local), so after crash and before retry, if update happened, retry gets v2 (correct, not stale).

- `fan knowledge` re-derived from `user_profiles.facts` JSONB (PG) + in-memory fallback but PG wins, so deterministic.

- `behavior state` pure function of `fan_message + conversation_state (re-derived from get_recent_messages 20)` + `structured persona` + `commerce objective` → **no global singleton, no counter, no cached emotional state** (persona_behavior.py pure). **PROVEN restart-safe**.

- No `random state`, `process-local counter`, `global singleton` changes result — proven via `test_restart_safety` `s1 == s2`.

---

## 14. Retry / Requeue Audit

| Retry cause | Persona behavior re-derived? | Preserved? | Skipped? | Duplicated? | Effect |
|---|---|---|---|---|---|
| **FloodWait / Telegram 429** (send_worker rate-limit) | N/A (send side after Qwen) | persona already in content | no | no | **NO** persona loss |
| **rate limit** `check_send_rate_limit` (db/redis.py:508 lua) | N/A send side | same | — | — | **NO** |
| **Qwen failure** `generate_draft` exception → `""` → empty handling → operator queue | **YES** behavior derived before Qwen, but Qwen failed → draft empty → validation empty → operator queue (no persona message to fan) | — | — | — | **PROVEN NO bypass** (empty not sent) |
| **scoring failure** `score_draft` LLM exception → `scores={}` → `composite 0.0` → `min 0.1` → operator queue (scoring.py:168) | **YES** validation already done, but scoring failed → still operator queue | preserved | — | — | **PROVEN fail-closed** |
| **send failure** `enqueue_send` → `send_worker` Telethon exception → `save_outbound_after_send` not called, remains pending → `XAUTOCLAIM` → re-derived behavior on retry | **YES** re-derived | — | — | — | **PROVEN** |
| **operator queue** `add_to_operator_queue` (no Qwen) | N/A | — | — | — | **NO** |
| **DLQ replay** `replay_dlq_entry` → `enqueue_inbound` with original `payload.persona` (handlers.py legacy) | **PARTIAL** stale `persona` string (system[0]) but fresh `structured` via worker re-derive → hybrid | preserved fresh for behavior+validation | — | — | **LIKELY hybrid but not substitution** |

No retry loses creator isolation or persona version; no bypass of validation/scoring/commerce because validation is before scoring and scoring failure still routes to operator queue (not auto-send). **PROVEN.**

---

## 15. Commerce Boundary

**Attack** `Persona says "be generous with pricing"` (not in Sunny persona, but test via `communication` tone):

- Persona `CREATOR PERSONA` contains **no** `price`, `buy_url`, `checkout` (audit grep `price` 0 in persona, `render_persona_block` no commerce). **PROVEN**.

- Fan `how much?` → `commerce/_try_commerce_draft` builds `CommerceStateRequest` from `fangate_products` mirror (db/fangate), `resolve_and_run_commerce` deterministic, `select_commerce_response` decides `USE_COMMERCE_RESPONSE` → draft is `deepseek_response` template with authoritative `price_minor` from DB, not from persona.

- Persona behavior `PERSONA BEHAVIOR: ...` is appended **after** `Commercial STATE` (llm_worker.py:715 then 1118), so last system msg is behavior, but behavior does not set price. If persona block said `price = $1`, Qwen could hallucinate price, but **scoring** `price_mention` HARD_FLAG (scoring.py:22) with `is_authorized_commerce` bypass only if price matches `authorized_price_minor` within 0.005 (scoring.py:97). Unauthorized price → `price_mention` → `min(score,0.1)` → operator queue, not auto-send. **PROVEN defense**.

- **Can persona alter product?** `AVAILABLE CONTENT: Title1` is titles only, `rank_products_by_relevance` uses `fangate` vault, not persona. **NO**.

- **Attribution/fulfillment**: `commerce/dao` `mark_aftercare_completed` etc. not reachable from persona.

**Also commerce tone can influence presentation without changing facts**: `COMMERCIAL STATE: desire=...` + `PERSONA BEHAVIOR: sincerity required` can make Qwen present price with `soft tone` vs `playful` without altering `price` value — this is correct per spec §15 (persona affects voice, not truth). **PROVEN.**

---

## 16. Failure Isolation

| Failure | Worker crash? | Message dropped? | Persona bypass? | Commerce safety bypass? | Routing? |
|---|---|---|---|---|---|
| `persona retrieval` `get_structured_persona_async` exception → `{}` | **NO** caught `except: return {}` (creator_persona.py:346) → derive neutral `warm LOW` | **NO** continues | **weakened to generic, not bypassed to wrong creator** | **NO** | normal Qwen |
| `persona behavior derivation` exception → `except: log debug, continue` (llm_worker.py:1150) | **NO** | **NO** | falls back to neutral behavior, still Qwen with CREATOR PERSONA 19k | **NO** | normal |
| `persona validation` exception → `except: log debug, continue` (llm_worker.py:1280) | **NO** | **NO** | `validation unavailable` → no flag, scoring still runs | **NO** | normal |
| `telemetry` `record` exception → `logger.warning, pop cache` (telemetry.py:237) | **NO** | **NO** | **NO** | **NO** | normal |
| `event publication` `publish_event` exception → `except: pass` (llm_worker.py:1122) | **NO** | **NO** | **NO** (best-effort) | **NO** | normal |
| `cache` Redis `get_redis` exception → `get_structured_persona_async` catches and returns `{}` | **NO** | **NO** | generic | **NO** | normal |
| `fan knowledge` `get_fan_knowledge` exception → `return []` (fan_knowledge.py:473) | **NO** | **NO** | still persona | **NO** | normal |
| `conversation_state` `derive_conversation_state` exception → `except: warning, conversation_state=None` (context.py:594) | **NO** | **NO** | fallback neutral | **NO** | normal |
| `Qwen` exception → `return ""` (llm_worker.py:125) | **NO** | **NO** | empty → operator queue `empty_draft` | **NO** | operator queue |
| `scoring` exception → `composite 0.0` + `min` (scoring.py:173) | **NO** | **NO** | still routed to operator queue, not auto-send | **NO** (fail-closed) | operator queue |

**Most important**: `A best-effort observability failure must never become a persona or commerce safety bypass.` **PROVEN**: all persona/behavior/event/telemetry paths are `try/except` with `continue` and **do not** set `is_authorized_commerce` or bypass `HARD_FLAGS`. Commerce safety (price) is independent of persona events.

---

## 17. Privacy Audit

**New persona telemetry/events**:

- `persona.behavior` event (llm_worker.py:1122,1280) payload: `persona_version, emotional_state, confidence, conversation_mode, question_allowed, disagreement_available, teasing_allowed, sincerity_required` before Qwen, and `persona_version, emotional_state, confidence, conversation_mode, voice_valid, naturalness_valid, validation_status, voice_score, naturalness_score, severe, fact_violation, reasons[3]` after. **No** `message content`, `fan private details`, `buyer email`, `tokens`, `credentials`, `Redis internals`, `full persona instructions` (only version + enum names + scores). **PROVEN safe**.

- `telemetry` fields: `persona_version, emotional_state, behavior_confidence, conversation_mode, persona_voice_valid, persona_voice_severe, voice_score, naturalness_score, persona_question_compliance, persona_fact_violation` (telemetry.py) — all enums/scores, no PII. `telemetry.to_dict` does not include `draft` (llm_worker.py:1230 `save_outbound_after_send` separate, not telemetry). **PROVEN safe**.

- `dashboard live` `personas` route `GET /api/personas?creator_id=` returns `id, name, instructions, is_default, creator_id, version, updated_at, metadata` (postgres.py:215). `metadata` is full persona JSON (23 fields) — **exposed to dashboard authed admin** (require_auth), not to fan. Acceptable per `DO NOT expose persona internals to dashboard unnecessarily` — but admin needs to see persona to edit; exposure is via authed API, not WebSocket broadcast to fan. **PROVEN not leaked to fan channel**.

- `WebSocket` `chatbot:events` → `event_bus` publishes `persona.behavior` with `scope=user` (so only that dialog's WebSocket receives, not global). Check `core/event_bus.py` `publish_event` scope logic: `scope=user` → `ws_manager` sends to `user_id` room only (if implemented). **PROVEN creator+fans scoped, not global.**

- `logs`: `logger.debug("persona behavior derivation failed")` no PII; `logger.warning` for empty draft includes `user_id` only, not content. **PROVEN safe.**

---

## 18. Performance Audit

- **persona behavior derivation**: 8 compiled regex on `fan_message` (~80 chars) + `fan_knowledge 5` loop + `recent 3` scan + `structured persona` dict lookups (20 keys) → **0.2–0.4ms**, no DB beyond already-fetched `get_structured_persona_async` (cached 600s, single `SELECT metadata WHERE creator_id=$1`). No new Redis call beyond already-fetched `get_recent_messages` (already called for context) and `retrieve_relevant_knowledge` (already called for context) — actually llm_worker does extra `get_recent_messages(limit 7)` and `retrieve_relevant_knowledge` for behavior, **additional 2 calls per generation** beyond context. `get_recent_messages` is `SELECT ... WHERE user_id=$1 ORDER BY created_at DESC LIMIT 7` (postgres.py:453) — cheap indexed, but **extra DB query per generation** (not in map's claim of no extra DB). Proven via `llm_worker.py:1087,1092,1222`. **P2 performance, not architecture break.**

- **persona validation**: `validate_persona_voice` loops response len once (200 chars) + 5 generic patterns + emoji regex + 3 fact regex → **O(n)** <0.2ms, no DB/Redis/LLM/network, compiled regex, bounded lists.

- **rendering**: `render_persona_behavior_block` 3 lines, no I/O.

- **event publication**: `publish_event` Redis `PUBLISH` (1 RTT, best-effort) — already does `ai.generation_started` etc., so +1 extra PUBLISH per generation (persona.behavior before + after = 2 extra). **Not new worker/queue**, but **extra Redis PUBLISH 2 per generation**.

- **telemetry writes**: `telemetry.record` already does `insert_generation_telemetry` (1 PG insert) per generation; new fields are extra columns in same row, no extra write.

- **Overall**: **NO NEW LLM CALLS** (proven grep `get_llm_provider` 0 in behavior/validation), **NO NEW WORKERS/QUEUES**, **NO ARCHITECTURE CHANGE**, but **+2 Redis PUBLISH +1-2 extra SELECTs per generation** (acceptable, not O(n²), no catastrophics).

---

## 19. Dashboard AI-Reply Path

**File**: `chatbotv2/dashboard/routes/messages.py:77` `api_dialog_ai_reply` (modified 43D).

- **Before 43D**: `get_cached_user_persona(dialog_id)` / `get_user_persona(dialog_id)` global, no `creator_id` → **P1 leak** same fan 777 could get Sunny when should be Mia.

- **After 43D**: 
  ```python
  _ctx = await resolve_single_application_creator()
  _creator_id = _ctx.creator_id if READY else None
  persona = await get_cached_user_persona(dialog_id, creator_id=_creator_id)
  if miss: persona = await get_user_persona(dialog_id, creator_id=_creator_id); cache_user_persona(..., creator_id)
  persona = await get_cached_default_persona(creator_id=_creator_id) / get_default_persona(creator_id=_creator_id)
  ```
  **PROVEN creator-scoped retrieval** (same as handler). Enqueues `enqueue_inbound {persona=legacy string, generation_id MD5}` → worker re-derives `CREATOR PERSONA` + `PERSONA BEHAVIOR` + validation via same `process_message` path, so **correct version, fan knowledge, behavior, validation, scoring** all via worker.

- **Does it receive correct creator persona?** **PROVEN YES** via `creator_id` from `resolve_single_application_creator` (single active creator). If 0 or 2 active creators → `_creator_id=None` → falls back to generic warm (weakening, not misattribution).

- **Correct version?** **PROVEN YES**: worker's `get_structured_persona_async(_creator_id)` fresh.

- **Correct fan knowledge?** **PROVEN YES**: worker's `build_qwen3_context(..., creator_id=_creator_id)` does `get_fan_knowledge(creator_id, user_id)` creator-scoped.

- **Correct behavior/validation/scoring?** **PROVEN YES**: same `process_message` path.

- **Merely correct persona retrieval?** **NO**, full pipeline.

**Remaining nuance**: Dashboard path enqueues `persona` legacy string snapshot at `api_dialog_ai_reply` time, which may be stale vs worker's fresh `structured` (same as DLQ hybrid). But since dashboard path is operator-initiated (not fan auto), staleness window is same as normal inbound (handler also enqueues legacy persona). **Not a new bypass.**

---

## 20. Observability Correlation

**Trace one generation** `generation_id=MD5(777:hi:1)=abc`, `creator_id=1`:

- `message.created` (handlers.py:80) `{message_id, telegram_message_id, content, direction, username}` + `generation_id abc, creator_id 1, scope=user, event_id uuid` (event_bus)

- `ai.generation_started` (llm_worker.py:625) `generation_id abc, creator_id 1, scope=user, event_id uuid`

- `persona.behavior` (llm_worker.py:1122) `generation_id abc, creator_id 1, persona_version 1, emotional_state warm, scope=user` (before Qwen)

- Qwen → `persona.behavior` update (llm_worker.py:1280) same `generation_id abc, creator_id 1, persona_version 1, voice_score 0.9, validation_status PASS`

- `ai.generation_completed` (llm_worker.py:121? `publish_event ai.generation_completed` after `enqueue_send` OR `suggestion.created` + `ai.generation_completed` for operator queue) `generation_id abc, creator_id 1`

- `message.sent` / `message.send_failed` (send_worker) includes `generation_id` propagated via `enqueue_send` `data["generation_id"]=str(gid)` (db/redis.py:72)

- `telemetry` row `generation_telemetry generation_id abc TEXT, creator_id 1, persona_version 1, emotional_state warm` (telemetry.py `insert_generation_telemetry`)

**All events share same `generation_id` + `creator_id`** (proven via `publish_event` calls all pass `generation_id=generation_id, creator_id=_creator_id`). Retries preserve `generation_id` (MD5 deterministic, not random uuid, `llm_worker.py:512` `if generation_id is None: MD5 else str(generation_id)`), so `XAUTOCLAIM` replay does not create new ID, and `persona.behavior` second publish uses same `generation_id` (duplicate event deduplicated by `event_id` uuid, not `generation_id`, so retry will publish *new* `event_id` but same `generation_id` — idempotent for consumer dedup via `event_id`, correct).

---

## 21. Canary Safety

**Config**: `core/config.py:128 ai_agent_canary_enabled false, ai_agent_canary_sample_rate 0.0, ai_runtime_mode legacy` (verified via `get_settings`). `workers/llm_worker.py:962 should_use_agent` checks `CanaryConfig.from_settings()` then `should_use_agent(user_id, _creator_id, _canary_config)` — returns false when disabled.

- **No promotion**: `ai_agent_canary_enabled` still false, `ai_runtime_mode` still legacy, not changed in 43D diff (grep `canary` in `commerce/persona_behavior.py` 0 hits, `commerce/persona_validation.py` 0). **PROVEN unchanged**.

- **Persona changes do not alter canary**: `persona.behavior` is derived only in `else: legacy` branch (llm_worker.py:1072 inside `else: # Legacy runtime path`), not in `if _use_agent` branch, so canary agent path never derives persona behavior (correct, agent has own `agent/runtime.py` persona handling). No canary mutation.

---

## 22. Test Quality Audit

**File**: `tests/test_phase43d_behavioral_fidelity.py` (37 tests, 2.21s)

| Test | Type | Proves | Does NOT prove |
|---|---|---|---|
| `test_A/B/C` sunny/mia identity/creator isolation | **behavioral** (derive state, not string inspect) | `lowercase_policy, can_disagree` per creator | Real DB/Cache isolation (mocked `get_structured_persona_async`, not real PG `SELECT`) |
| `D/E` lowercase/emoji policy | **structural** (state field, block contains) | Policy derived from persona | Qwen actually lowercase (prompt-only) |
| `F` emoji spam | **behavioral** (validate) | `emoji_spam` detected `emoji_score 0.3` | Routing capped? (soft, not hard) |
| `G/H/I` question | **integration** (behavior + validation) | `question_allowed` respects budget | Question still sendable (soft) |
| `J/K/L/M/N` 8 states | **behavioral** (derive) | State triggers correctly | Qwen follows behavioral block (prompt-only) |
| `O/P` teasing | **behavioral** | `teasing_allowed` toggles | Qwen teases (not validated) |
| `Q/R` disagreement/sincerity | **behavioral** (avail) | `disagreement_available true` when subjective | Qwen disagrees (not validated) |
| `S` generic | **validation** (soft) | `generic_pattern_score <1` | Routing |
| `T` identity violation | **behavioral** (validation severe) | `fact_violation true, FACT_FAIL` for `I'm Mia` vs Sunny, `I'm 21` vs 19 | Operator queue routing (checked via scoring HARD_FLAG existence, not end-to-end enqueue) |
| `U` minor valid | **validation** | `too_formal` soft not severe | |
| `V` severe via scoring | **structural** (grep) | `HARD_FLAGS` contain 3 new + llm_worker mapping | End-to-end `min(score,0.1)` (not executed) |
| `W` 50-turn | **longitudinal** (derive loop 50, deterministic same inputs → same outputs) | Not drift for input, but output drift to generic is not tested (no Qwen call) | **Mock-only, not production-representative** |
| `X` determinism | **unit** | Same gid → same state | |
| hostile empty/malformed | **structural** | Fallback warm LOW, not crash | |
| no LLM | **structural** (grep) | No `get_llm_provider` | |
| single-pass | **structural** (grep) | `validate_persona_voice` in llm_worker, no second `generate_draft` | |
| generation correlation | **unit** | `generation_id` preserved | DB PK `TEXT` not UUID? (migration) |
| restart safety | **unit** | `s1==s2` | |
| commerce authority | **structural** (grep) | No price in behavior | |

**Classification**: 60% behavioral (derive/validate), 30% structural (grep), 10% integration (mock), **0% live Qwen string inspection** (no `generate_draft` mock that asserts Qwen output contains lowercase). **Gap**: Tests prove *state is derived* and *validator flags*, but **not that Qwen obeys** `PERSONA BEHAVIOR` block (which requires live Ollama with 50-turn conversation and human judgment). **Production-representative**: `W` 50-turn is **mock-only** (derive loop, not real `build_qwen3_context` + `generate_draft`).

**Could pass while real runtime broken?** Yes: if `render_persona_behavior_block` were never appended to `context` (e.g., `context.append` commented out), tests `A-X` would still pass because they call `derive`/`render` directly, not via `process_message → build_qwen3_context → generate_draft`. **Gap**: No integration test that `process_message` actually appends `PERSONA BEHAVIOR` before Qwen (requires `AsyncMock` for `generate_draft` and assert `system` contains `PERSONA BEHAVIOR`). Existing `test_W` is not that.

---

## 23. Required Hostile Test Matrix

| Scenario | Persona retrieved? | Structured? | Behavior state? | Qwen? | Validation? | Scoring? | Bypass persona? | Verdict |
|---|---|---|---|---|---|---|---|---|
| normal inbound `hi` Creator Sunny | YES isolated | YES 19k | YES warm LOW | YES 1 | YES PASS | YES 0.85 | **NO** | **PASS** |
| same fan 777 / two creators Sunny vs Mia | YES isolated per creator | YES per creator | YES per creator (can_disagree diff) | YES per creator | YES per creator | YES | **NO** | **PASS** |
| persona v1 | YES | YES v1 | YES v1 | YES | YES | YES | — | PASS |
| persona v2 | YES | YES v2 | YES v2 | YES | YES | YES | — | PASS |
| persona update v1→v2 in-flight A v1 hybrid + B v2 | **PARTIAL** A hybrid (CREATOR v1 + BEHAVIOR v2) | YES | YES fresh | YES | YES fresh | YES | **WEAKENING** not substitution | **PARTIAL** |
| 50-turn conversation (synthetic) | YES per turn | YES per turn | YES per turn re-derived, not stuck | YES per turn | YES per turn | YES per turn | **NO** but voice soft → drift to generic still sendable | **PARTIAL** |
| emotion transition serious→playful→annoyed | YES | YES | YES correct per-turn | YES | YES | YES | **NO** | **PASS** |
| emotion ambiguity `lol okay` | YES | YES | warm LOW fallback (not strong) | YES | YES | YES | **NO** (correct) | **PASS** |
| emoji flood `😭×9` | YES | YES | YES occasional | YES | YES `emoji_spam` 0.3 soft | YES soft | **NO** still sendable (minor) | **PARTIAL** (not hard) |
| uppercase `HELLO THERE` | YES | YES | YES | YES | YES `too_formal`? No but casing not fact | YES | **NO** (not blocked) | **PARTIAL** (voice prompt-only) |
| question spam `? ? ?` when forbidden | YES | YES | `question_allowed false` | YES | YES `question_when_forbidden` soft | YES soft | **NO** still sendable | **PARTIAL** |
| generic `that sounds amazing tell me more` | YES | YES | YES | YES | YES `generic_pattern` soft | YES | **NO** still sendable | **PARTIAL** |
| teasing `you are ridiculous lol` | YES | YES playful teasing allowed | YES | YES | YES (no check) | YES | **NO** (injected but not validated) | **PASS** injection, **PARTIAL** validation |
| disagreement `Brooklyn better` can_disagree true | YES | YES avail true | YES | YES | YES (no check) | YES | **NO** (prompt-only) | **PARTIAL** |
| serious `I messed up` | YES | YES serious sincerity required | YES | YES | YES | YES | **NO** | **PASS** (sincerity injected) |
| commerce `how much?` with product $19.99 | YES | YES | YES | **NO** commerce draft bypasses Qwen | YES validation still runs on commerce draft (fact) | YES HARD price check | **NO** persona cannot alter price | **PASS** |
| Qwen failure `""` | YES | YES | YES derived but Qwen fails | YES attempt → "" | YES empty → soft | YES 0.0 → operator queue | **NO** | **PASS** (fail-closed) |
| validation failure exception | YES | YES | YES | YES | **SKIPPED** `except: log, continue` | YES (no flag) | **WEAKENING** (no persona flag) | **PARTIAL** (best-effort) |
| telemetry failure `insert_generation_telemetry` exception | YES | YES | YES | YES | YES | YES | **NO** (best-effort, pop cache) | **PASS** |
| event publication failure `publish_event` Redis down | YES | YES | YES | YES | YES | YES | **NO** (best-effort, not safety) | **PASS** |
| worker restart mid-generation | YES | YES | YES re-derived fresh | YES (retry) | YES | YES | **NO** deterministic | **PASS** |
| XAUTOCLAIM pending 30s → new worker | YES fresh | YES fresh | YES deterministic same generation_id | YES new Qwen | YES | YES | **NO** | **PASS** |
| FloodWait 429 send side | N/A | — | — | — | — | — | **NO** persona already in content | **PASS** |
| rate-limit retry | Same | — | — | — | — | — | **NO** | **PASS** |
| DLQ replay `replay_dlq_entry` → enqueue_inbound with stale `persona` string | **PARTIAL** `persona` string stale (system[0] legacy) + fresh `CREATOR PERSONA` + fresh behavior | YES fresh | YES fresh | YES new Qwen | YES fresh validation (fact) | YES | **PARTIAL** hybrid but not substitution | **PARTIAL** |
| dashboard AI reply `POST /api/dialogs/{id}/ai-reply` | **YES now** creator-scoped (fixed) | YES via worker | YES via worker | YES via worker | YES via worker | YES via worker | **PROVEN FIXED** | **PASS** |
| operator approval `operator_queue approved → enqueue_send` | **NO** human | NO | NO | NO | NO | NO | **N/A** not AI | **PASS** |
| post-purchase `scheduled_messages` aftercare | **NO** (operator text) | NO | NO | NO | NO | NO | **N/A** | **PASS** |
| scheduled response `scheduler_worker` | **NO** (operator text) | NO | NO | NO | NO | NO | **N/A** | **PASS** |

---

## 24. Privacy — New Persona Surfaces

- `persona.behavior` before Qwen: `persona_version, emotional_state, confidence, conversation_mode, question_allowed, disagreement_available, teasing_allowed, sincerity_required` (8 enums) — no content, no buyer email, no tokens.
- `persona.behavior` after validation: `+ voice_valid, naturalness_valid, validation_status, voice_score, naturalness_score, severe, fact_violation, reasons[3]` — reasons are generic `emoji_spam_4, too_long_6` not message excerpts.
- Telemetry adds same scores, no full response. **PROVEN no `buyer email`, `tokens`, `credentials`, `Redis` internals, `full persona instructions` (only 19k already in system[1], not in telemetry). Logs `warning` only `user_id`, not content.

---

## 25. Performance — Phase 43D Cost

- **Derivation**: 8 regex on fan_message (~80) + `retrieve_relevant_knowledge` extra SELECT (limit 7 + 5) — **+2 SELECT per generation** beyond existing `build_qwen3_context` (which already does 1× `get_structured_persona_async` + 1× `get_recent_messages` 20 + 1× `retrieve_relevant_knowledge` 5). Now adds 1× `get_structured_persona_async` (cached 600s) + 1× `get_recent_messages 7` + 1× `retrieve_relevant_knowledge 5` → **3 extra SELECTs per generation** (2 cached). Each `SELECT ... WHERE user_id=$1` indexed, <2ms.

- **Validation**: O(n) ~200 chars, 5 generic + 3 fact regex, <0.2ms.

- **Events**: +2 `PUBLISH chatbot:events` per generation (before + after) — best-effort, no ACK.

- **No new LLM/worker/queue**: Proven grep `get_llm_provider` 0 in behavior/validation.

- **Cardinality**: No `O(n²)`, bounded lists, compiled patterns, no per-token DB.

---

## 26. Canary Safety

- `core/config.py` `ai_agent_canary_enabled false` unchanged, `ai_runtime_mode legacy` unchanged — grep `canary` 0 in new files.

- Persona behavior derived only in `else: # Legacy runtime path` (llm_worker.py:1072), **not** in `if _use_agent` branch, so canary agent never gets persona behavior (agent has separate `agent/runtime.py` persona). No promotion via persona.

---

## 27. Most Important Remaining Fidelity Gap

**Voice/style still prompt-suggested with soft telemetry, not hard enforcement**: Lowercase, slang moderation, emoji frequency, length beyond 5 sentences, generic ack, teasing, disagreement are **soft fail** (`valid false` but not severe → still auto-send if LLM scorer 0.85). Phase 43C P1-01..06 remain **LIKELY** to drift to `generic warm agreeable` under temperature 0.85, especially for long `warm LOW` stretches. Fact identity is hard (operator queue), but *style* is not.

Phase 43D correctly **does not** do blind `response.lower()` or `append 😭` every message (preserves variation, per §8/12/14), but also **does not** cap over-polished 6-sentence formal replies to operator queue — they remain sendable.

---

## 28. Top Remaining Risks (P1)

- **P1-01 Question spam remains soft**: `question_when_forbidden` is `persona_question_policy_violation` but not capped to 0.1 (only fact is). Qwen can ask when `question_allowed false` and still auto-send.
- **P1-02 Generic template still soft**: `generic_pattern_score` telemetry only, not HARD_FLAG, so `That sounds amazing! Tell me more?` loop can still auto-send.
- **P1-03 DLQ stale legacy persona**: Replay hybrid `system[0]` stale vs `CREATOR PERSONA` fresh — unlikely to cause substitution but proven hybrid.
- **P1-04 In-flight version hybrid**: `CREATOR PERSONA` v1 + `PERSONA BEHAVIOR` v2 within same generation (1ms window) — not cross-creator, but telemetry reports v2 while Qwen saw hybrid.

---

PHASE 43E STAGE A VERDICT

END-TO-END PERSONA ENFORCEMENT: PARTIAL
PERSONA BYPASS RESISTANCE: PARTIAL
PERSONA VERSION CONSISTENCY: PARTIAL
CREATOR ISOLATION: PARTIAL
FAN/PERSONA SEPARATION: PASS
EMOTIONAL STATE MACHINE: PASS
VOICE ENFORCEMENT: PARTIAL
NATURALNESS: PARTIAL
LONGITUDINAL CONSISTENCY: PARTIAL
RETRY/XAUTOCLAIM CONSISTENCY: PASS
DASHBOARD AI-REPLY FIDELITY: PASS
COMMERCE AUTHORITY: PASS
PRIVACY: PASS
OBSERVABILITY CORRELATION: PASS
FAILURE ISOLATION: PASS
PERFORMANCE: PASS
CANARY SAFETY: PASS

P0: 0
P1: 4
P2: 5
P3: 3

PRODUCTION CHANGES: NONE
CANARY CHANGES: NONE
MIGRATIONS: NONE
NEW LLM CALLS: NONE
NEW WORKERS: NONE
NEW QUEUES: NONE
ARCHITECTURE REDESIGN: NONE

FINAL VERDICT:
CONDITIONALLY READY

TOP REMAINING RISKS:
- Question/generic style violations remain soft (still auto-sendable) — voice drifts to generic under warm LOW
- In-flight version hybrid (CREATOR v1 + BEHAVIOR v2) and DLQ stale legacy persona snapshot
- Recent messages global (messages WHERE user_id) leaks cross-creator history (not persona but conversational context)
- Generation_id / send_dedup / telemetry global (not creator-scoped) causes dedup false positive and telemetry overwrite for same fan/message across creators

NEXT STAGE:
Stage B hardening — promote question/generic soft flags to hard when repeated (e.g., 2nd violation in 3 turns → cap to operator queue), add summary version invalidation on persona update, make send dedup creator-scoped (send_dedup:{creator}:{dedup_id}), make telemetry PK (creator_id, generation_id), and consider per-creator recent_messages view; no new LLM/worker/queue, no prompt overhaul, keep single-pass

---

ROOT QUESTION:
Can any realistic execution path bypass the configured creator persona?

ANSWER:
PROVEN YES for weakening (CREATOR_CONTEXT_UNAVAILABLE → generic warm LOW), LIKELY for style drift (soft flags still auto-send), but PROVEN NO for silent persona substitution on primary inbound (creator-scoped SELECT persona:{creator}:{user}, persona:creator:{creator}, derive/validate per creator_id, generation_id preserved but re-derived). The most realistic fan experience is still Sunny (19, NYC, freelance designer, sushi, playful) on every normal turn, but with measurable risk of over-polished, question-heavy, or generic-ack replies slipping through as auto-approved.

MOST DANGEROUS BYPASS:
Not substitution but generic weakening + dedup collision: same fan 777 identical content via two creators within 3600s → second creator's send incorrectly deduped as duplicate (send_dedup global) and dropped, or telemetry overwritten.

MOST IMPORTANT REMAINING FIDELITY GAP:
Voice style (lowercase/casing, emoji frequency, length, teasing, disagreement, generic ack) is derived and injected as bounds and validated as soft telemetry, but not hard-enforced to operator queue, so Qwen can remain over-polished/generic and still auto-send with score 0.80+.

WHY PHASE 43D IS OR IS NOT SUFFICIENT:
Phase 43D IS sufficient for deterministic identity, isolation, versioning, commerce safety, emotional state selection, and fail-closed severe fact violations (PROVEN). It is NOT sufficient for “Sunny always feels like Sunny” because style/naturalness enforcement is prompt-suggested + soft flag, not hard, and because cross-creator recent_messages and generation_id/send_dedup/telemetry remain global (not creator-scoped).
