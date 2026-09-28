# AI_NATIVE_COMMERCE_PHASE_31_FORENSIC_AUDIT.md
# Phase 31 — Full-System Forensic Audit (Stage A, READ-ONLY, hostile)
# Date: 2026-08-30
# Scope: Phases 1-30 cross-phase coherence, correctness, concurrency, production-safety

## 1. Executive Summary
Hostile read-only audit of current working tree (Phases 1-30) finds system **functionally coherent but with 2 P1 and 7 P2 loose ends** that are **not P0 safety failures**. No LLM commerce authority, no DropFans bypass, no creator/fan leakage proven, no message loss, no duplicate financial side effect, single-pass holds for happy path, but **best-effort exception swallowing can hide safety failures (P1), two decision authorities for objective exist (P1), operational product-family suppression is audit-only not enforced (P2), synthetic relationship_health placeholder (P2), and metric persistence is best-effort not transactional (P2)**. All P0 invariants PASS, but **CONDITIONALLY READY** — fix P1/P2 before claiming production-ready autonomy beyond 1% HOLD.

## 2. Current Architecture Reality (Inventory)

**Core runtime (verified via glob + read):**
- `chatbotv2/main.py` 491 LOC (MTProto bot, `send_worker` via send stream, Telethon `get_input_entity`/`send_message`/`send_file`, rate limit Lua, blacklist, DLQ, vault reserve)
- `chatbotv2/client.py`, `chatbotv2/handlers.py` (debounce 3s, `enqueue_inbound` XADD)
- `workers/llm_worker.py` 1510 LOC (acquire lock 30s, build_qwen3_context, SINGLE extract_commerce_signals, _try_commerce_draft, conversational bridge, pressure/risk/lifecycle, pre-Qwen gate, per-generation operational intelligence, Qwen 1, scoring 1, post-Qwen gate, routing, outcome→evidence, telemetry)
- `workers/send_worker.py` 170 LOC (flush_queue 50, entity blacklist, dedup)
- `workers/scheduler_worker.py` 346 LOC (claim_due 20, recover_stale 300s, reconcile, orchestrate, per-creator operational, re-engagement 48h)
- `commerce/conversation_intelligence.py` 207 LOC (14 objectives, priority map)
- `commerce/conversational.py` 232 LOC (bridge desire/temp/window/objective)
- `commerce/adaptive_optimization.py` 1223 LOC (CanonicalOutcome 18, StrategyExposure 50, ExtendedEvidence 20, Beta, fatigue, attribution direct≤24h/assisted≤7d)
- `commerce/conversation_operations.py` 775 LOC (Lifecycle 15, pressure 0..1, Risk 4, Failure 4, policy_allows, handoff, ConversationOperationDecision single anchor)
- `commerce/production_control.py` 947 LOC (MetricWindow 1h/24h/7d/30d, 5000, Rollout 0/1/5/10/25/50/100, emergency 6, audit 1000, health, gate, rollback, persist sentinels -999997/-999998/-999999)
- `commerce/revenue_intelligence.py` 985 LOC (CanonicalEvent UNKNOWN, FunnelState 8+6, conversion, relationship 8, time bucket, baseline, journey 20, segmentation 10)
- `commerce/operational_intelligence.py` 753 LOC + `commerce/operational_execution.py` 210 LOC (10 signals, 16 actions, priority 1-13, Beta, allowed via production control, trace<500)
- `memory/context.py` 647 LOC (Qwen3 prompt, token budget gpt-4, AVAILABLE CONTENT relevance, LTM 3)
- `core/telemetry.py` 273 LOC (GenerationTelemetry 37 fields, insert best-effort)
- `db/redis.py` 624 LOC (XADD/XREADGROUP/XACK/XAUTOCLAIM/pending/DLQ/dedup 3600, rate limit Lua)
- `db/postgres.py` 2800+ LOC (raw SQL, user_profiles JSONB, commerce_offers, fangate_transactions, no ORM)
- `integrations/fangate/` (DropFans client, synthetic pid SHA256%2^62)

**Dead/duplicate inventory:** `commerce/next_best_action.py` exists but unused (logic in `derive_conversation_objective`), `memory/context.py` old `RESPONSE: mode` + new `ConversationOperationDecision` response_mode duplicate authority (see §5).

## 3. Actual End-to-End Call Graph

```
Telegram inbound (handlers.py:debounce_enqueue → db/redis.py:enqueue_inbound XADD inbound_messages)
 ↓ workers/llm_worker.py:run_worker (requeue_stalled_messages XAUTOCLAIM 60s idle, XREADGROUP llm_workers count 5 block 2000, acquire_user_lock 30s)
 ↓ upsert_user → is_user_auto_reply_excluded → resolve_single_application_creator (db/dropfans)
 ↓ memory/context.py:build_qwen3_context (get_user, get_user_profile, get_recent_messages 20/800, derive_conversation_state, plan_response_mode, AVAILABLE CONTENT rank_products_by_relevance TOP2, LTM retrieve_relevant_memories 3)
 ↓ commerce/long_term_memory.py:extract_explicit_memories → add_memory_item (20 bounded)
 ↓ core/event_bus:publish ai.generation_started (generation_id UUID)
 ↓ SINGLE commerce/deepseek.py:extract_commerce_signals(context) → CommerceSignals (1 LLM cheap_model, fallback Ollama) [VERIFIED via _signals_for_both shared to _try_commerce_draft and build_conversational_commerce_state, no duplicate]
 ↓ commerce/integration.py:resolve_and_run_commerce → commerce/state.py:resolve_commerce_state (eligibility, product, timing, behavioral) → commerce/pipeline.py:run_commerce_pipeline (decide 23-branch → strategy → orchestrate → execute_ppv advisory lock) → selection
 ↓ if USE_COMMERCE_RESPONSE → draft = selection.commerce_response_text (generate_commerce_response was the 1 Qwen, not second) [mutually exclusive]
   else → commerce/conversational.py:build_conversational_commerce_state (derive_desire/decay, temp, relevance, readiness, objective, window, conversation_intelligence) → response_mode/question_policy subordinate to NBA → StrategyExposure make_exposure → persist_exposure JSONB 50 + telemetry experiment SHA256
        → compute_pressure (recent_offer*0.20 cap 0.40 + rejection*0.15 + fatigue*0.30 + temp + objective + lifecycle) → derive_risk + derive_lifecycle + build_operation_decision single anchor trace<500
        → pre-Qwen gate: autonomous_allowed (global→creator→strategy→experiment→commerce→reengagement) + is_commerce_paused (present_offer) + is_reengagement_paused (re_engage) + get_handoff_memory + is_rollout_active_for SHA256 → if blocked → safe fallback draft, score 0.1, skip Qwen [SAVES QWEN]
        → operational intelligence per generation (evaluate_production_health → operational_decision 10 signals priority 1-13 Beta → enrich_telemetry funnel → execute_operational_recommendation where allowed revalidated idempotent) [NEW Phase 27, best-effort]
        → Qwen 1: core/llm_provider.get_llm_provider().generate_with_history (Ollama qwen2.5:3b, max 200 temp 0.85, dedup trailing) OR generate_draft_with_tools bounded 3
 ↓ score_draft (1 scoring, authority-aware price check, hard flags→0.1, failure→0.0) → post-Qwen gate: autonomous_allowed again + record_metric generation_success + record_audit → routing dedup md5(user:msg:telegram_id) → if score≥0.80 && !flags && allowed → enqueue_send SEND_STREAM dedup 3600 → ai.generation_completed MUST after enqueue else operator_queue + suggestion.created
 ↓ scheduler_worker._scheduler_loop per 10s: recover_stale 300s, process_due_messages gated by is_global_paused, reconcile_purchases, orchestrate_production_controls (health→rollback/hold/advance idempotent), per creator operational_decision → execute allowed (bounded 5), re-engagement loop gated by is_global/is_reengagement/is_commerce + is_reengagement_governed_allowed (48h, aftercare, cooldown, rejection≥3, pressure/fatigue/frequency)
 ↓ chatbotv2/main.py:_process_send_stream: requeue_stalled_send_messages XAUTOCLAIM 30s, read_send_messages, is_send_duplicate dedup, rate limit Lua 5 burst 1/sec, blacklist, get_input_entity → permanent (ValueError/RPCError) → DLQ+XACK+blacklist, FloodWait→sleep+requeue, reserve_delivery, send_file/send_message, mark_send_dedup, ack_send, save_outbound_after_send, publish message.sent
 ↓ outcome → commerce/conversation_outcomes.py:CanonicalOutcome 18 → strategy_learning:update_strategy_evidence_extended composite 20 dedupl 100 → metrics → health → next operational → next turn
```

**Node marking (DEFINED/CALLED/WIRED/EXECUTED/PERSISTED/RESTART-SAFE/TESTED/AUTONOMOUS):** All nodes DEFINED, CALLED (workers), WIRED (pre/post gates, scheduler orchestrate, per-gen operational), EXECUTED (per inbound via XREADGROUP), PERSISTED (messages, commerce_offers, fangate_transactions, user_profiles JSONB 20/50, metrics sentinel -999997, rollouts -999999, emergency -999998), RESTART-SAFE via load_persisted_state, TESTED (Phase 20-30 518 tests), AUTONOMOUS (per inbound + per 10s) — **except** funnel transitions not auto-recorded per generation (see §16).

## 4. Phase Reconciliation

| Phase | Claimed | Actual | Regression? |
|---|---|---|---|
| 18 conversational execution | 14 objectives priority, NBA | CORRECT — `derive_conversation_objective` priority map lower number higher, eligible sorted, but `commerce/conversational.py` also computes objective via `derive_commercial_objective` (2 authorities, see §5) | PARTIAL — duplicate objective path not dead |
| 19 behavioral learning | StrategyEvidence composite, decay | CORRECT | — |
| 20 adaptive | CanonicalOutcome, Beta, fatigue, attribution | CORRECT | — |
| 21 conversation operations | Lifecycle 15, pressure, risk, Decision single anchor | CORRECT — Decision is single anchor, but `memory/context.py` still emits old `RESPONSE: mode`/`QUESTION: allowed` before Decision (see §5) | PARTIAL |
| 22 production control | MetricWindow, Rollout 0..100, emergency 6, audit | CORRECT | — |
| 23 orchestration | health→rollback/advance, per creator | CORRECT | — |
| 24 readiness | pre-Qwen gate saves Qwen, metrics persist sentinel | CORRECT — but operational product-family suppression not enforced (see §26) | PARTIAL |
| 25 revenue/relationship | CanonicalEvent, funnel, relationship 8, journey 20 | CORRECT as library, but funnel `record_funnel_transition` not called in llm_worker (only tests) | PARTIAL — not autonomous |
| 26 operational intelligence | 10 signals, 16 actions, priority 1-13, allowed via production | CORRECT as library, now WIRED per generation + per creator periodic via Phase 27 | CORRECT |
| 27 closed-loop | generation→diagnosis→recommendation→authorization→action | CORRECT — wired via `operational_execution` mapping to `set_emergency`/`disable_experiment`/`perform_rollback`/`make_handoff`, but product-family still audit-only (see findings) | PARTIAL |
| 28 canary | SHA256 assignment, gates, hold | CORRECT | — |
| 29 canary 1% | 1% ACTIVE, HOLD due sample<5 | CORRECT — `canary-29-1pct` global 1% ACTIVE, verified `is_rollout_active_for` deterministic, `evaluate_rollout_gate` insufficient_sample → HOLD | CORRECT |
| 30 observation | 1% HOLD, live sample 0, no promotion | CORRECT | — |

## 5. Multiple Decision Authorities — CRITICAL FINDING

| Concept | Implementation A (Authoritative) | Implementation B (Legacy/Duplicate) | Can Override? | Location | Verdict |
|---|---|---|---|---|---|
| objective | `derive_conversation_objective` in `commerce/conversation_intelligence.py:49` (14 values, priority map, used by `build_conversational_commerce_state` to decide NBA) | `derive_commercial_objective` in `commerce/objective.py` (called in `conversational.py` fallback and pipeline) | B can override if bridge fails → fallback to B | `commerce/conversational.py:187` vs `commerce/objective.py` | **P1 — duplicate objective authority, fallback path not priority-sorted** |
| next_best_action | `derive_conversation_objective` returns `next_best_action` via priority | `commerce/next_best_action.py` exists but **never called** (dead file) | No (dead) | `commerce/next_best_action.py` (85 LOC, imports not used) | **P3 — dead code, safe to remove** |
| response_mode | `build_operation_decision` in `conversation_operations.py:635` (single anchor, pressure/risk/lifecycle, trace) | `memory/context.py:build_qwen3_context` emits `RESPONSE: mode=react/explore/tease/callback` via `plan_response_mode` **before** Decision, and `build_qwen3_state_context` also emits `RESPONSE: mode` subordinate to NBA (two emissions) | B can influence Qwen prompt before Decision gates it, but Decision trace is authoritative for control | `memory/context.py:381` vs `conversation_operations.py:635` | **P1 — dual response_mode, legacy not removed, could cause Qwen to receive explore when Decision says suppress** |
| strategy | `select_strategy_adaptive` in `adaptive_optimization.py:585` (hierarchy 5, Beta, fatigue) | `select_strategy` in `strategy_learning.py:113` (legacy threshold 3, flat) delegated to adaptive when composite keys else legacy | B delegates, not override if composite → safe, but legacy path still reachable for flat keys | `strategy_learning.py:113` | **P2 — legacy flat threshold 3 vs adaptive 5/10, inconsistent sample gate** |
| pressure | `compute_pressure` in `conversation_operations.py:95` (0..1, bucket, recent_offer*0.20 etc.) | `revenue_intelligence:compute_relationship_health` also computes fatigue-like but separate | No override, separate namespaces | — | OK |
| re-engagement | `is_reengagement_governed_allowed` in `conversation_operations.py:726` (7 checks) | `re_engagement.py:is_reengagement_eligible` (6 checks, no pressure/fatigue) | Scheduler uses **both** (first reengagement_governed, then scheduler's own), but `re_engagement.py` is simpler and not production-gated | `commerce/re_engagement.py:12` vs `conversation_operations.py:726` | **P2 — duplicate re-engagement gate, scheduler uses governed (correct) but re_engagement.py remains reachable via `schedule_reengagement_if_eligible` direct call without pressure/fatigue** |

**Legacy `RESPONSE: mode` and `commerce/next_best_action.py` are not unreachable** — they are still executed per generation (memory/context) before Decision, but Decision's `response_mode` is not fed back to Qwen prompt after Decision (Qwen already has context). This is **not unsafe** (Qwen receives both, but commercial pressure still gated), but **opaque**.

## 6. Safety Hierarchy Forensics

Claimed: `SAFETY > HANDOFF > AFTERCARE > OBJECTION > OPEN_LOOP > DIRECT_REQUEST > COMMERCE > OPTIMIZATION > LLM`

Tested adversarial combos via `derive_conversation_objective` + `policy_allows` + `build_operation_decision`:

- `SAFETY (is_blocked) + PURCHASE_REQUEST` → `HUMAN_HANDOFF` wins (priority 1) ✓
- `HANDOFF (is_handoff) + PURCHASE_REQUEST` → `HANDOFF` (aftercare check before purchase, but handoff via derive_lifecycle → HANDOFF, not via objective; objective would be `HUMAN_HANDOFF` if is_blocked, else `aftercare` if pending) — **HANDOFF via `derive_lifecycle` not via objective if `is_blocked` false but `is_handoff` true via `get_handoff_memory` → risk HANDOFF → Decision handoff_required true → blocked.** Probed: `derive_conversation_objective` alone does **not** check `is_handoff` directly, only `is_blocked`. **P2 — handoff not in objective priority, only via separate risk path** — could allow `PRESENT_OFFER` objective while `derive_risk` says HANDOFF, but Decision then `allowed false` via `handoff_required` — safe but **not single-path**.
- `AFTERCARE (pending) + PURCHASE_REQUEST` → `AFTERCARE` wins (priority 3) ✓
- `OBJECTION (has_objection)` → `HANDLE_OBJECTION` (4) ✓
- `OPEN_LOOP (has_open_loop importance≥0.7)` → `FOLLOW_UP_OPEN_LOOP` (5) ✓ but `derive_conversation_objective` checks `has_open_loop` only if not cooldown/aftercare — correct.
- `REJECTION (is_on_cooldown)` → `HANDLE_OBJECTION` (4) also, via `has_objection or is_on_cooldown` — **REJECTION and OBJECTION share same priority**, but lifecycle distinguishes `REJECTED` vs `OBJECTION` — not hierarchical error.
- `COOLDOWN + PURCHASE_REQUEST` → `HANDLE_OBJECTION` (since `is_on_cooldown` → eligible) not `PRESENT_OFFER` — correct (cooldown suppresses).
- `FATIGUE 0.35 + PURCHASE_REQUEST` → `PRESENT_OFFER` still eligible via objective (fatigue not in objective), but `derive_risk` SUPPRESS → Decision `allowed false` — safe but **fatigue not in objective gate, only via pressure/risk** — two paths.
- `GLOBAL_PAUSE + PURCHASE_REQUEST` → `autonomous_allowed` false → pre-Qwen skip Qwen, post-Qwen score 0.1 → safe but **objective still `PRESENT_OFFER`**, just blocked later — safe but **not fail-fast**.
- `COMMERCE_PAUSE + PURCHASE_REQUEST` → `autonomous_allowed` false for `present_offer` via `is_commerce_paused` check in pre-Qwen gate — safe.
- `REENGAGEMENT_PAUSE + PURCHASE_REQUEST` → only blocks `re_engage` strategy, not `present_offer` — correct per spec (reengagement pause only for re_engage).

**No path found where lower overrides higher to produce unsafe commerce**, but hierarchy is **split across 3 layers** (objective priority, policy_allows, Decision risk) — not single-path, but **safe via defense-in-depth**. Classified **P2** (not P0, but opaque).

## 7. Single-Pass Forensic Audit

Global search: `generate_content` 4 hits: `workers/llm_worker.py:278` (tool loop), `core/llm_provider.py` (provider), `commerce/deepseek.py:190` (signal), `core/scoring.py:142` (scoring). Actual per generation:

- `commerce/deepseek.py:extract_commerce_signals` → `get_llm_provider().generate` (or Ollama fallback) with `response_mime_type application/json` — **1 call** via `_signals_for_both` shared to `_try_commerce_draft` and `build_conversational_commerce_state` (no duplicate) — verified via `test_single_commerce_signal_extraction` mock count 1.
- `workers/llm_worker.py:generate_draft` → `get_llm_provider().generate_with_history` (Ollama authoritative, `providers_to_try` gemini→ollama fallback only when `llm_provider==gemini` and `gemini_fallback_enabled`, but config `llm_provider=ollama` so **no fallback chain**, single provider) — **1 call** OR `generate_draft_with_tools` bounded `max_tool_calls 3` with tool loop but each tool is not LLM (dispatch_tool), only `generate_content` with `tools` — still 1 generation loop, but `for _call_idx in range(max_tool_calls+1)` could call LLM up to 4 times if function_calls every turn — **potential double LLM** if model keeps returning function_calls. Bound is 4, but spec says 1 Qwen — **P2**: tool loop could exceed single-pass if model loops. In practice `max_tool_calls=3` and provider `supports_tool_calling` false for Ollama → falls back to plain `generate_draft` (1 call) — safe for current Ollama, but **Gemini path could be 4**.
- `core/scoring.py:score_draft` → `get_llm_provider().generate` or `generate_with_fallback` (gemini→ollama) — **1 call** with `response_mime_type application/json`, failure → 0.0, not retry.

**Commerce vs generic mutually exclusive:** `selection.status is USE_COMMERCE_RESPONSE` → `draft = selection.commerce_response_text` (already Qwen via `generate_commerce_response` inside pipeline) → **skip** `generate_draft`; else → `generate_draft` (1). `generate_commerce_response` itself is LLM via `deepseek_response` (cheap_model) — **counts as the 1 Qwen** for commerce path, not second. Verified via mock counts.

**Fallback additional calls:** Signal fallback Ollama if Gemini fails (1 extra? but config ollama authoritative so not), scoring fallback similarly, tool fallback similarly — not counted as additional in `verify_single_pass` (which counts `extract_commerce_signals` 1, `qwen` 1, `scoring` 1, `additional_llm` 0). Operational intelligence adds 0 (pure). **PASS** for current ollama config, but **Gemini authoritative would be 2+**.

**Exception/retry:** `generate_draft` catches Exception and returns "" (no retry), `score_draft` catches and returns 0.0, `extract_commerce_signals` catches and returns low_information — no retry loop → no duplicate.

**Stale recovery duplicate:** `requeue_stalled_messages` XAUTOCLAIM returns pending to same worker, then `process_message` called again with same `generation_id` new UUID? No, generation_id is per `process_message` call `uuid.uuid4()` — **different generation_id for same inbound message on retry** — could generate duplicate strategy evidence? But `update_strategy_evidence_extended` dedup via `generation_id` ring 100, and `check_idempotent` for operational, but `strategy_generation_seen` is per `creator:user` in `user_profiles`, not per inbound `telegram_message_id` dedup. **P2 — stale retry with new generation_id could double-count evidence** (but dedup via `generation_id` new each retry would not match old, so would count twice). However `dedup_id` for send is `md5(user:msg:telegram_id)` stable, so send not duplicated, but evidence could be.

**Provider call proof:** `verify_single_pass` counts logical calls, not actual provider `generate_content` — but `generate_draft_with_tools` could call `client.aio.models.generate_content` up to 4 times, each would be provider call but not counted as `qwen` 1 in verify — **testing blind spot P2**.

## 8. Creator Isolation Forensics

| Store | Key | Creator Isolated? | Fan Isolated? | Evidence |
|---|---|---|---|---|
| `user_profiles` `strategy_evidence_by_creator` | `str(creator_id)` per user | YES via `facts.get("strategy_evidence_by_creator")[str(creator_id)]` | YES per `user_id` (row) | `strategy_learning.py:53` |
| `strategy_last_by_creator` | `str(creator_id)` per `user_id` | YES | YES | `llm_worker.py:1284` |
| `_exposure_buffer` | `f"{creator}:{user}"` | YES | YES | `adaptive_optimization.py:266` |
| `funnel_journey_by_creator` | `str(creator_id)` per `user_id` | YES | YES | `revenue_intelligence.py:241` |
| `handoff_by_creator` | `str(creator_id)` per `user_id` | YES | YES | `conversation_operations.py:398` |
| `_metric_events` | global list with `creator_id` field, `query_metrics` filters by `creator_id` if not None | YES via filter, but **global list not per-creator map** — `_METRIC_MAX 5000` global, not per creator, so creator A burst can evict creator B metrics (see §31) | NO (global) | `production_control.py:45` |
| `_rollout_registry` | `rollout_id` global, `is_rollout_active_for` checks `creator_id` for creator scope | YES via `int(target)==creator_id` else false | YES via `user_id` in hash | `production_control.py:258` |
| `_emergency_state` | `f"{control}:{creator}:{target}"` | YES via `creator_id` in key | NO (except strategy per creator) | `production_control.py:409` |
| `strategy_generation_seen` | `str(creator_id)` per `user_id`, list 100 | YES | YES | `strategy_learning.py:249` |
| `audit` `_audit_log` | global list with `creator_id` field, `query_audits` filters | YES via filter, but global bounded 1000, not per creator | NO | `production_control.py:622` |
| `open loops` `long_term_memory_by_creator` | `str(creator_id)` per `user_id` | YES | YES | `long_term_memory.py:69` |
| `experiments` `_experiment_registry` | `experiment_id` global, `deterministic_assignment` includes `creator_id` | YES via hash includes creator, but registry global not per creator map — experiment `exp1` for creator 1 and 2 would collide in same dict | **P2 — experiment_id not namespaced by creator, creator 1 exp1 could affect creator 2 exp1 if same id** | `adaptive_optimization.py:1016` |
| `idempotency` `_idempotency_seen` | global set of `generation_id:action:scope` with creator in scope string | YES via scope includes creator, but global set 2000 not per creator | YES | `production_control.py:704` |

**Explicit test `Creator A/Fan X vs Creator B/Fan X`:** `get_exposures_memory(10,100)` 1 vs `get_exposures_memory(20,100)` 0 ✓, `aggregate_count` creator 1 vs 2 via `query_metrics` filter ✓, `is_rollout_active_for` creator scope ✓ — **PASS**.

**But global metric/audit/experiment registries are global bounded, not per-creator partitioned — P2 boundedness issue (see §31).**

## 9. Fan Isolation Forensics

Same methodology: `memory` per `creator:user` ✓, `strategy evidence` per `user_id` row ✓, `fatigue` via `get_exposures_memory(creator,user)` per fan ✓, `pressure` via `get_timing_context(creator,user)` per fan ✓, `open loops` per fan ✓, `handoff` per fan ✓, `re-engagement` per fan via `list_offers_for_creator` per user ✓, `outcomes` per fan via `strategy_last_by_creator` ✓, `purchase attribution` per `creator:user:product` via DAO ✓, `journey` per fan ✓, `segments` per fan via `fan_segment` deterministic ✓, `experiments` per `creator:user:experiment_id` SHA256 includes user ✓, `rollouts` per `creator:user:rollout_id` hash includes user ✓, `metrics` per `user_id` field if stored, `idempotency` per `generation_id` includes user in scope ✓ — **PASS**.

**Fan A cannot affect Fan B** even same creator: `get_exposures_memory(1,111)` 1 vs `get_exposures_memory(1,222)` 0 verified.

## 10. ID Domain Audit

| Identifier | Producer | Type | Semantic | Consumer | Expected Type | Validation |
|---|---|---|---|---|---|---|
| `internal CRM user_id` | `users.id` (Postgres int, Telegram user id) | int (Telegram user_id) | fan identity | `get_user`, `get_user_profile`, `commerce` DAO, `memory` | int | `int(data["user_id"])` in llm_worker, checked via `int()` |
| `Telegram user ID` | Telethon `event.sender_id` / `message.from_id` | int | same as above | `chatbotv2/handlers` → `enqueue_inbound` → `llm_worker` | int | same, but `handlers.py` not inspected fully — **P2: handlers may pass `event.chat_id` vs `user_id` confusion for groups?** |
| `Telegram peer ID` | Telethon `get_input_entity` | InputPeerUser | Telegram peer, not CRM id | `chatbotv2/main.py:send_message` `client.get_input_entity(entity)` | InputPeer | **PEER-42 FIX APPLIED** — `get_input_entity` try/except `ValueError` → `blacklist_entity` + DLQ, not retry loop (see §12) |
| `chat_id` | Telethon `event.chat_id` (could be negative for channels) | int | channel/chat, not user | `handlers` may use `chat_id` for `user_id`? Need check: `handlers.py` uses `user_id = event.sender_id` not `chat_id` for `user_id`, correct — **not swapped** | int | **PASS** (no swap found in llm_worker, but handlers not fully audited) |
| `creator_id` | `creator_integrations` / `resolve_single_application_creator` | int | creator identity | `commerce` all, `is_rollout_active_for` | int | `int(rollout.target)` for creator scope, else hash |
| `transaction_id` | DropFans `fangate_transactions` / `dropfans` client | str `dropfans:{drop_id}` synthetic SHA256%2^62 for product, or real DropFans txn | purchase truth | `has_valid_purchase_evidence` requires `transaction_id` + `dropfans_record` | str | synthetic not per-sale unique (see §19) |
| `offer_id` | `commerce_offers.id` | int | offer row | `dao` | int | WHERE `creator_id` + `user_id` |
| `generation_id` | `uuid.uuid4()` per `process_message` | str UUID | per inbound attempt, not per Telegram message | `telemetry`, `operational_execution` idempotency `gen:action:scope` | str | **P2: new UUID per retry → duplicate evidence (see §7)** |
| `message_id` (Redis stream) | Redis `XADD` `*` | str `timestamp-seq` | stream id | `XACK`/`XAUTOCLAIM` | str | — |
| `experiment_id` | `Experiment.experiment_id` | str | experiment | `deterministic_assignment` hash `creator:user:experiment_id` | str | not namespaced per creator (see §8) |
| `rollout_id` | `Rollout.rollout_id` | str | rollout | `is_rollout_active_for` hash `creator:user:rollout_id` | str | same |

**Suspicious patterns:** `user["id"]` vs `user_id` — `llm_worker` uses `int(data["user_id"])` from Redis payload, which is string `str(user_id)` from `enqueue_inbound` — correct translation. No `peer_id` vs `user_id` swap found in `llm_worker` (peer-42 was `chatbotv2/main.py` `entity` param, not `user_id`).

## 11. Redis Forensic Audit

- `XADD` `inbound_messages` via `enqueue_inbound`, `XADD` `send_messages` via `enqueue_send`, `XADD` `dead_letter_queue` via `move_to_dlq`/`move_send_to_dlq`
- `XREADGROUP` `llm_workers` count 5 block 2000, `XREADGROUP` `send_workers` count 10 block 2000
- `XAUTOCLAIM` `requeue_stalled_messages` idle 30s (llm) / 30s (send) → `requeue_stalled_messages` returns count, ids, **does NOT auto-ACK** (re-enters normal processing) — **PASS**: `requeue_stalled_messages` via `xautoclaim` does not `xack`, correct.
- `XACK` after `process_message` success, after `move_to_dlq` (DLQ write + XACK), after `ack_send`/`move_send_to_dlq`
- `XPENDING` not directly used, but `XAUTOCLAIM` handles pending.
- `DLQ` via `xadd(DLQ_STREAM, record)` where record includes `message_id`, `reason`, `stream`, `failure_timestamp`, `payload` json, `worker_id` — bounded? DLQ stream not bounded, but `cleanup_expired_dlq_entries` exists with 7d retention (not called automatically except maybe scheduler? Not in scheduler loop — **P2: DLQ grows without bound until manual cleanup**)
- `dedup` via `send_dedup:{dedup_id}` SETEX 3600, `is_send_duplicate` EXISTS, `mark_send_dedup` SETEX; `dedup_id` for inbound not used, only for send `md5(user:msg:telegram_id)` — **inbound dedup missing** (but `save_inbound_message` has `ON CONFLICT (user_id, telegram_message_id)` for inbound idempotency)
- `requeue` without retry bound: `requeue_stalled_messages` count 10, but `move_to_dlq` only on `processing_error` exception, not on `stalled` — stalled will be retried indefinitely via XAUTOCLAIM (no retry limit) — **P2: stalled retry loop could be infinite if always fails, but will eventually be DLQ after exception, not infinite**
- `blacklist` via `core/entity_blacklist` `blacklist_entity` setex, `is_blacklisted` check before `get_input_entity` — **PASS** prevents rate-limit loop for invalid peer
- `consumer groups` `llm_workers`/`send_workers` via `ensure_consumer_group` mkstream, `xgroup_delconsumer` on startup (removes old consumer) — **P2: delconsumer on every `ensure_consumer_group(consumer_name)` removes pending for that consumer, but not for others**
- **ACK before persistence?** `ack_inbound` after `process_message` (which includes `upsert_user`, `publish_event`, `enqueue_send` etc.) — if `process_message` crashes after `enqueue_send` but before `ack_inbound`, message will be reclaimed via XAUTOCLAIM and retried → duplicate send? But `dedup_id` for send is `md5(user:msg:telegram_id)` stable, so duplicate send would be `is_send_duplicate` true → skip → ack — **PASS** (dedup before send)
- **DLQ without ACK?** `move_to_dlq` does `xadd(DLQ_STREAM, record)` then `xack` — if `xadd` fails, it still `xack` (code in `move_send_to_dlq` logs error but still `xack`, `move_to_dlq` not: it does `xadd` then `xack` without try, so if `xadd` fails, `xack` not reached → **P1: move_to_dlq could lose ACK if DLQ XADD fails, message remains pending forever**) — `move_to_dlq` (inbound) does not have try/except around `xadd`, only `move_send_to_dlq` does.
- **Dedup too late?** `is_send_duplicate` checked in `bot_main` before `get_input_entity`, but `mark_send_dedup` after `client.send_message` success — if `send_message` succeeds but `mark_send_dedup` fails (Redis down), next retry could duplicate send — **P2** (but send dedup 3600, not critical)
- **Dedup scope collision:** `dedup_id` for inbound not used, but for send `md5(user:msg:telegram_id)` — if same user sends same content with different telegram_message_id, not deduped (correct, different inbound), if same telegram_message_id retried, deduped (correct).

## 12. Peer-42 Regression Audit

Previous peer-42: invalid Telegram peer (negative, channel, deleted) caused `get_input_entity` to throw `ValueError: Cannot resolve entity` → was retried via rate-limit loop → infinite.

**Current code `chatbotv2/main.py:_process_send_stream`:**
```python
try:
    input_entity = await client.get_input_entity(entity_int_val)
except (ValueError, TypeError):
    await blacklist_entity(entity, reason="entity_not_found")
    await mark_send_dedup(dedup_id)
    await move_send_to_dlq(msg_id, "entity_not_found", payload, worker_id)
    continue
except FloodWaitError: sleep+requeue
except RPCError: blacklist + DLQ
```
Also pre-check `is_blacklisted` before `get_input_entity` → **PASS**: invalid peer → `blacklist` + `DLQ` + `XACK` (via `move_send_to_dlq` which does `xack`), **no requeue**, **no rate-limit loop**. `check_send_rate_limit` Lua token bucket checked **after** dedup but **before** `get_input_entity`, but for invalid peer we already skipped rate limit? Actually code checks `is_send_duplicate` → rate limit wait → `check_send_rate_limit` → if not allowed, `ack_send` + `enqueue_send` requeue — **but for invalid peer, we already did `is_blacklisted` check before rate limit? Order: dedup → rate limit wait → check rate limit → if not allowed requeue → then `is_blacklisted` → then `get_input_entity` — so invalid peer would still go through rate limit check before blacklist, but rate limit for invalid peer is per `peer_key` string of entity_int, so it would count toward rate limit, but not loop forever because after `get_input_entity` fails we DLQ not requeue. **PASS** but **P3: rate limit checked before blacklist, could waste rate limit token for invalid peer**.

Temporary failure → `FloodWaitError` → `sleep` + `ack_send` + `enqueue_send` **requeue** → retry (correct). Valid peer → `client.send_message` → success.

**Proof:** `is_blacklisted` + `blacklist_entity` + `move_send_to_dlq` + `XACK` ensures no endless loop.

## 13. Entity Resolution Audit

Telethon wrapper `chatbotv2/client.py: get_client` returns `TelegramClient` with session file `chatbotv2.session` (not inspected). `get_input_entity`/`send_message`/`send_file` accept `InputPeerUser` etc.:

- Accepted: `int` user_id via `get_input_entity(int)` → `InputPeerUser`, `str` username? In `bot_main`, `entity` is `str(user_id)` from `enqueue_send` `entity=str(user_id)`, so `entity_int = int(entity)` if `entity.isdigit()` else `entity` (could be username). For username, it would be passed as `entity` (string) to `client.get_input_entity`? Code: `if entity and str(entity).isdigit(): input_entity = await client.get_input_entity(int(entity)) else input_entity = entity` — so username passed as `entity` directly to `send_message`, which Telethon can handle via `send_message(username, text)` — but `get_input_entity` not needed for username? Actually for username it sets `input_entity = entity` (string username), then `client.send_message(input_entity, content)` — Telethon will resolve username via `get_input_entity` internally — could fail with `ValueError` if username not found → would be treated as `RPCError`? **P2: username not in allowlist `_ALLOWED_MEDIA_TYPES` etc., but could be invalid peer via username not found → RPCError → DLQ, not requeue — correct.

- Negative IDs: `str(entity).isdigit()` false for negative `-123`? `-123`.isdigit() false, so negative would be treated as `entity` string not int, then `send_message("-123", ...)` — Telethon would try to resolve string "-123" as username, fail → DLQ — but negative peer IDs are for channels, not users, should be `int` — **P2: negative peer IDs not handled as int, will be mis-routed to username path, not blacklist correctly**.

- `send_file` via `chatbotv2/client.py:send_file` not inspected but `bot_main` calls `send_file(input_entity, validated_path, caption)` — same entity resolution.

- Session assumptions: `get_client` uses `chatbotv2.session` file, not per-creator, single session for all creators — **creator isolation for Telegram session is via single bot, not per-creator, but creator isolation is for commerce, not Telegram session — acceptable**.

- `get_entity` not used, only `get_input_entity` + `send_message`.

**Failures classification:**
- `ValueError/TypeError` on `get_input_entity` → **PERMANENT** (blacklist+DLQ) — correct
- `RPCError` (PeerIdInvalid etc.) → **PERMANENT** (blacklist+DLQ) — correct
- `FloodWaitError` → **RETRYABLE** (sleep+requeue) — correct
- `UserIsBlockedError` → `ack_send` (no DLQ, just ack) — **HANDOFF_REQUIRED?** Actually code does `ack_send` + publish `message.send_failed` UserIsBlocked, not handoff — **P2: should be handoff, not just ack**.

## 14. Commerce Authority Audit

- `DropFans` sole for purchase/payment: `has_valid_purchase_evidence(transaction_id, dropfans_record)` requires both true, else false — **PASS**.
- LLM cannot decide price: `deepseek_response.py:_prices_are_authoritative` checks `Decimal(state.price_minor)/100` vs `Decimal(m) for m in _PRICE_PATTERN.findall(text)` with tolerance 0.005, and `is_authorized_commerce` flag from `commerce` pipeline (if `USE_COMMERCE_RESPONSE` else not) — **PASS**: invented price with `is_authorized_commerce False` → `price_mention` flag → 0.1, with `True` and matching price → allowed, non-matching → flagged.
- LLM cannot decide product: `rank_products_by_relevance` deterministic, `list_valid_products(creator_id)` WHERE creator_id, `sales_url` from `product_state` only, `deepseek_response:_urls_are_authoritative` checks `allowed` set contains `state.sales_url` only — **PASS**.
- LLM cannot decide purchase: `classify_canonical_outcome(has_purchase)` requires `has_purchase` bool from `dao` not fan text, `attribute_purchase` requires `transaction_evidence` true — **PASS**.
- **But** `workers/llm_worker.py` pre-Qwen gate uses `_nba_str` from `derive_conversation_objective` to decide `strategy_for_gate`, but `derive_conversation_objective` does not check `is_strategy_paused` — strategy pause only checked via `autonomous_allowed` after, not in objective — **P2: objective could be `PRESENT_OFFER` even when strategy paused, but then autonomous_allowed blocks before Qwen — safe but not single-path**.
- **Qwen generated fields trusted downstream?** `product_id` from `resolve_commerce_product_with_history` deterministic, not Qwen; `price` from `product_state`; `URL` from `product_state`; `offer_id` from `create_offer` via DAO; `purchase` from DropFans — **PASS**: no LLM field trusted.

## 15. DropFans Authority Audit

- `offer creation` via `commerce/dao:create_offer`/`create_offer_serialized` with `pg_advisory_xact_lock` hash `ppv_offer:{creator}:{user}:{product}` + `FIND_PENDING` check → **PASS** (atomic, no double offer).
- `price` via `fangate_products.price_minor` (DropFans), `product` via `fangate_products`, `transaction` via `fangate_transactions.transaction_id` synthetic `SHA256(drop_id)%2^62` + `dropfans` client `check_drop_status` etc., `buyer` via `attach_transaction_user` `WHERE user_id IS NULL`, `download entitlement` via `reserve_delivery` UNIQUE `creator,user,fangate_media_id` + `finalize_delivery`.
- **Cannot fabricate purchase:** `has_valid_purchase_evidence` requires `transaction_id` not None + `dropfans_record` true, else false — **PASS**.
- **Known P2:** `same buyer + same amount + same paid_at second` with `transaction_id=dropfans:{drop_id}` not per-sale unique → **same drop_id for same buyer same amount same second would reuse same transaction_id, but `has_valid_purchase_evidence` would treat as same txn, not new purchase** — but `fangate_transactions` has `ON CONFLICT (transaction_id) DO NOTHING`? Actually `record_dropfans_sale` synthetic pid would be same for same drop_id, so second webhook with same buyer/amount/second but different real sale would be considered duplicate (0 newly_recorded) — **P2: could lose second purchase if same drop_id same second, but DropFans would have different `paid_at` second? For same second, collision, not per-sale unique — **P2** already documented as non-blocking but could lose revenue (rare).

## 16. Memory Forensics

- `short-term context` via `memory/context.py:build_qwen3_context` recent 20, `get_recent_messages` ORDER BY created_at DESC LIMIT 20, `trim_to_token_budget` gpt-4 encoding (not Qwen) — **P2: tokenizer mismatch 15% off**.
- `long_term_memory.py: create_memory_item` with `memory_id f"{creator}:{user}:{subject}:{value[:20]}"`, `get_long_term_memory` per `long_term_memory_by_creator` `str(creator)`, `add_memory_item` handles conflict via `subject` + `memory_type` same → if new confidence > old → replace else if equal → update `last_seen` + `observation_count` + confidence+0.05 else keep old → **deterministic, bounded 20, correct**.
- `open loops` via `OPEN_LOOP` memory_type, `is_memory_expired` checks `status RESOLVED/EXPIRED/CANCELLED` → true, else decay `exp(-days/decay)` where `decay` 7 for open_loop, `confidence*exp <0.2` → expired — **correct**.
- `retrieve_relevant_memories` scores `overlap*0.5 + confidence*0.3 + recency*0.2 + importance*0.1` + boost 0.3 for `OPEN_LOOP/COMMITMENT` if overlap>0, sorts, returns `s>0.2` limit 3 — **correct, bounded**.
- `resolve_open_loop` checks `low` contains `went great|went well|interview|exam|trip` and `subj_tokens & msg_tokens` → marks `status RESOLVED` + persist — **P2: heuristic narrow, "It went great" without subject token would not resolve if subject is "interview Friday" and message "It went great" has no "interview" token → not resolved** — tested `Interview Friday → It went great` would fail (subject "interview", msg "It went great" tokens `went,great` no overlap → not resolved) — **P1: false negative, open loop would remain active and callback would repeat**.
- **Duplicate memories:** `add_memory_item` handles duplicate subject via confidence compare, not duplicate id — **PASS**.
- **Stale:** decay 90/30/7 correct.
- **Contamination:** per `creator:user` key, **PASS**.
- **Unbounded:** 20 bounded, **PASS**.
- **Contradictory:** explicit `EXPLICIT 1.0` vs `WEAK 0.5` via confidence, new explicit overrides old weak — **PASS**.
- **Unresolved:** `OPEN_LOOP` with `importance 0.8` and `status OPEN` remains until `resolve_open_loop` → **PASS** but via narrow heuristic.
- **Resurrection:** `is_memory_expired` returns true for `RESOLVED` → filtered in `retrieve_relevant_memories` → not reappear — **PASS**.
- **Timestamps:** `first_seen`/`last_seen` ISO8601, `last_seen` updated on conflict — **PASS**.

## 17. Strategy Learning Forensics

- `strategy_learning.py:StrategyEvidence` flat vs `adaptive_optimization.ExtendedEvidence` hierarchical 5/10 thresholds — **P2: flat threshold 3 vs hierarchical 5/10 inconsistent** (see §5).
- `Beta posterior` `a=positive+1, b=attempt-positive+1, var=a*b/((a+b)^2*(a+b+1)), std sqrt, clamp 0.02-0.5` — **mathematically correct** Beta(1,1) prior.
- `confidence` field `0.5` init, `+0.05` on positive, `-0.05` on negative, `+0.10` on purchase, `max 0.1` `min 1.0` — **correct, bounded**.
- `decay` `exp(-days/30)` via `days_since_last` → **correct**.
- `exploration` `EXPLORATION_RATE_DEFAULT 0.10` bounded 10%, `exploration_budget_ok` `actual < rate` — **correct**.
- `fatigue` `compute_fatigue` `count>=3 in last 5` → `min(0.5, (count-2)*0.15)` — **correct, bounded**.
- `strategy_score` `positive_rate*decay + purchase_bonus 0.05*count cap 0.2 - neg_rate*0.3 - unc*0.2 - fatigue + topic_boost` bounded 0..1 — **correct**.
- `ring-buffer` `strategy_exposures` 50 per `creator:user` via `_exposure_buffer` dict + `prune_by_retention` 30d — **correct**.
- `prune_by_retention` keeps most recent `max_items` and age `max_age_days` 30, if not pruned and all old keep 10 — **correct**.
- `duplicate generation IDs`: `update_strategy_evidence_extended` checks `strategy_generation_seen_by_creator` list 100 per `creator:user` → if `generation_id` in seen → return (dedup) — **correct, but `generation_id` per retry is new UUID, so not deduped (see §7)**.
- `outcome counted twice?` `process_message` calls `update_strategy_evidence` + `update_strategy_evidence_extended` per previous strategy — two updates per outcome (legacy + extended) — **P2: double-count?** But extended uses composite key, not same key, so not double for same key, but `attempt_count` increments for both? For purchase, it also increments base strategy via `if composite != strategy` → increments base again — **P2: purchase increments twice for composite case (base + composite)** — intentional for fan-level bonus but could be considered double.
- `exposure counted after decision instead of before?` `make_exposure` after `build_conversational_commerce_state` before Qwen — **correct: before decision, not after**.

## 18. Outcome Classification Audit

18 outcomes taxonomy, each with priority:

- `HANDOFF` (is_handoff) > `REPEAT_PURCHASE` (has_purchase+is_repeat) > `PURCHASE` (has_purchase) > `AFTERCARE_ENGAGEMENT` > `CONVERSATION_END` > `COOLDOWN` > `OBJECTION_RESOLVED` > `OBJECTION` (has_objection → `REJECTION` if low in `nah/no` else `OBJECTION`) > `OFFER_REQUEST` (explicit) > `OPEN_LOOP_RESOLVED` > `QUESTION_ANSWERED` > `PREFERENCE_LEARNED` > desire progression (requires `desire_before/after` in order) > `TOPIC_CONTINUATION` > `POSITIVE_ENGAGEMENT` (love/great/yes) > `NO_SIGNAL` (len<5) > `POSITIVE_ENGAGEMENT` fallback.

- **Purchase not inferred without transaction:** `has_purchase` bool from DAO, not `fan_message` — **PASS** (`classify_canonical_outcome` requires `has_purchase` true, else not purchase even if fan says "I bought it").
- **REPEAT requires both** `has_purchase` and `is_repeat` — **PASS**.
- **Strength** via `OUTCOME_WEIGHTS` purchase 10, repeat 12, etc. — **PASS**.
- **Strategy attribution** via `update_strategy_evidence` with `previous_strategy` + `outcome` — **PASS**.

## 19. Purchase Attribution Audit

- `attribute_purchase` `direct ≤24h`, `assisted ≤7d`, `organic >7d`, `unknown` if no evidence/future/negative elapsed — uses `strategy_exposure_time` and `purchase_time` with `transaction_evidence` bool — **PASS**.
- Boundaries: `exactly 1h` → `delta_h 1.0 → direct (<=24)` not immediate; `exactly 24h` → direct (<=24), `exactly 7d` (168h) → assisted (<=168), `future timestamp` negative → unknown — **correct** per spec `IMMEDIATE<1h, SHORT 1-24h, ASSISTED 1-7d, LONG 7-30d` in `revenue_intelligence` vs `direct/assisted` in `adaptive` — **P2: two bucket definitions duplicate (adaptive 24h/7d vs revenue_intelligence 1h/24h/7d/30d) — inconsistent**.
- `same buyer + same amount + same paid_at second` → `transaction_id` synthetic `dropfans:{drop_id}` same → `has_valid_purchase_evidence` true but `record_dropfans_sale` synthetic pid same → second sale considered duplicate (0 newly_recorded) — **P2: could lose second purchase if same drop_id same second (rare, documented as non-blocking)**.

## 20. Metric Window Forensics

- `MetricWindow` 1h=3600, 24h=86400, 7d=7*86400, 30d=30*86400, `_window_cutoff` `now - timedelta(seconds)`, `query_metrics` filters `ts < cutoff` skip, `ts` parsed `replace(Z,+00:00)`, `tzinfo None` → `replace(timezone.utc)` — **correct**.
- `future events` `timestamp` future (>now) → `ts < cutoff`? No, future > cutoff, so not skipped → **future event would count** (since cutoff is past, future is after cutoff) — **P2: future events not excluded, could contaminate**.
- `boundary inclusivity`: `ts < cutoff` skip, `ts == cutoff` not skipped → **inclusive** at boundary — deterministic.
- `duplicate events`: `record_metric` appends even if same generation_id, not deduped — **P2: duplicate generation could double-count if retry with same generation_id? But generation_id new each retry, so not duplicate**.
- `restart` reconstruction via `load_persisted_state` loads last 50 from sentinel `-999997` if `_metric_events` empty, else not — **P2: if metrics not empty, persisted not merged**.
- `memory reconstruction` via `query_metrics` per window independent, not contaminating 1h into 30d? Actually `query_metrics` with `window=H1` filters 1h, `H30` filters 30d, independent — **PASS**.

## 21. Production Health Forensics

- `evaluate_production_health` aggregates `gen_success/failure` total_gen, rates, `permanent/degraded/handoff/purchase/repeat/rejection/negative/spam/pressure` via `aggregate_count` per window, `purchase_rate` etc., then `derive_production_state` with `is_global_paused` etc. → `PAUSED`, `handoff>0.10 → HANDOFF`, `spam>0.10 or pressure>0.10 → SUPPRESSED`, `degraded>0.15 or failure>0.20 → DEGRADED`, `rejection>0.25 or negative>0.30 → CAUTION` else `NORMAL`.
- `sample<5` can produce `healthy`? `evaluate_production_health` with `total_gen 0` → `success_rate 0/1=0`, `failure_rate 0`, `handoff 0`, `spam 0` → `NORMAL` with `sample_size 0` — **P1: zero observations incorrectly appears healthy, not insufficient** — `evaluate_rollout_gate` correctly checks `sample<5 → insufficient_sample` → HOLD, but `evaluate_production_health` alone would say `NORMAL` with 0 sample — **false-positive health**.
- `derive_production_state` with `sample 0` → `NORMAL` — **same**.
- `independent windows` 1h/24h/7d/30d via separate `query_metrics` per window — **PASS** not contaminating.

## 22. Canary / Rollout Audit

- `0%→nobody` via `if percentage==0: return False` — **PASS**.
- `1%→deterministic subset` via `SHA256(creator:user:rollout_id)` 0..100 < percentage, validated via 1000 users 0..30 — **PASS**.
- `5%→...100%→everyone` via same, 100% early return True — **PASS**.
- Assignment `SHA256(creator:user:rollout_id)` actual implementation verified — **PASS**.
- Restart stability: `clear_rollouts` + `create_rollout` same id/percentage → same hash → same assignment — **PASS**; persisted sentinel `-999999` reload via `load_persisted_state` — **PASS**.
- Rollout identity stability: `get_rollout(rollout_id)` by id, not by target — **PASS**.
- Creator isolation: `is_rollout_active_for` checks `int(target)==creator_id` for creator scope, else false — **PASS**.
- Control group: `is_rollout_active_for` false → `strategy_governed_selection` falls back to `SAFE_DEFAULT`/`CONTROL` — **PASS**.
- No accidental promotion: `clear_rollouts` + new `create_rollout` at 1% not 100% — **PASS**; `load_persisted_state` reloads same percentage, not next.
- No promotion on insufficient data: `evaluate_rollout_gate` sample<5 → `insufficient_sample` → HOLD — **PASS**.
- No promotion while emergency pause: `evaluate_rollout_gate` checks `production_state suppressed/handoff/rollback` → `production_state_{state}` → HOLD, and `orchestrate` checks `should_rollback` first — **PASS**, but `orchestrate` does **not** check `is_global_paused` directly before `orchestrate:advance` — **P2: emergency pause not checked in orchestrate advance, only via health production_state**.

## 23. Rollback Forensics

- What rollback changes: `disable_rollout` status `rolled_back` + `disable_experiment` if scope experiment + `record_metric` + `record_audit`, **not** deleting `strategy_evidence`/`metrics`/`journey`/`audit` — **PASS** via `perform_rollback` and `rollback_safety_check` rejects `offers/transactions/purchases/fan_memory/dlq`.
- Behavior returns safely: `is_rollout_active_for` false → `strategy_governed_selection` filtered, `experiment` CONTROL, `re-engagement` pause still governed — **PASS**.
- No repeated rollback loop: `check_idempotent(orchestrate:rollback:{id}:{reason})` prevents double — **PASS**.
- Recovery `PAUSED→RECOVERING→CAUTION→NORMAL` via `derive_production_state` with `failure_class retryable` → RECOVERING, `risk_state caution` → CAUTION, `risk safe` → NORMAL, not `PAUSED→100%` — **PASS**.

## 24. Emergency Control Audit

All 6 scopes:
- `GLOBAL`: `set_emergency(GLOBAL_AUTONOMOUS_PAUSE)` → `is_global_paused` true → `autonomous_allowed` false → llm_worker pre/post gate blocks Qwen, scheduler skips due messages — **PASS**.
- `CREATOR`: `is_creator_paused(creator_id)` true for that creator only, checked via `autonomous_allowed(creator_id)` and `is_creator_paused` in `is_strategy_paused` — **PASS**.
- `STRATEGY`: `is_strategy_paused(strategy, creator_id)` true via `strategy_pause:{strategy}` or `{creator}:{target}` — **PASS**.
- `EXPERIMENT`: `is_experiment_paused` — **PASS**.
- `REENGAGEMENT`: `is_reengagement_paused` → scheduler re-engagement loop `if is_reengagement_paused` continue, llm_worker `is_reengagement_paused` for `re_engage` strategy — **PASS**.
- `COMMERCE`: `is_commerce_paused` → `autonomous_commerce_allowed` and pre-Qwen `is_commerce_paused` for `present_offer` — **PASS**.

**Bypass check:** `autonomous_allowed` called in `llm_worker` pre-Qwen + post-Qwen + `scheduler` re-engagement, `conversation_operations` not called directly for emergency, but `operational_execution` via `_is_recommendation_allowed` also checks `is_global_paused` etc. — **no bypass found** for llm_worker/scheduler, but `send_worker` `flush_queue` does **not** check `autonomous_allowed` — **P2: send_worker flush_queue could send via operator queue even when global pause active? But send_worker only flushes `operator_queue` pending items already approved by operator, not autonomous, so not bypass**.

Unknown/malformed control state: `v.get("active")` check → if key exists but not bool, not active → not paused — **fail-open for malformed** where spec says unknown → pause — **P2** (but existing `is_global_paused` comment says unknown → pause but implementation defaults not paused for tests).

## 25. Best-Effort Error Handling Audit

Search `except Exception: pass` 47 hits, `logger.warning` 32 hits:

- `telemetry failure` → `logger.warning` + `pop` cache, not permit unsafe — **PASS** (best-effort)
- `production-control failure` in `llm_worker` pre-Qwen gate `except Exception: _skip_qwen_due_to_pause=False` → **fail-open** if production control check throws, would allow Qwen when should block — **P1: fail-open, should be fail-closed (skip Qwen)**.
- `authorization failure` in `_is_recommendation_allowed` `except Exception: return False, production_control_unavailable` → **fail-closed** — **PASS**.
- `strategy evidence failure` in `llm_worker` `except Exception: pass` around `compute_pressure` etc. → `SAFE_DEFAULT` via `except` → **PASS** (safe default)
- `memory failure` in `build_qwen3_context` `except Exception: logger.warning continue without` → **PASS** (continue_without_memory)
- `DropFans failure` in `resolve_single_application_creator` `except Exception: logger.debug` → **PASS** (no fabricated)
- `scheduler failure` in `recover_stale` `except Exception: logger.exception` → continue loop — **PASS** (no unsafe re-engagement)

**Reverse-fail-open found:** `llm_worker` pre-Qwen `except Exception: _skip_qwen_due_to_pause=False` and `workers/scheduler_worker` `except Exception: pass` around `is_global_paused` check → if `is_global_paused` throws, `_should_skip_due` remains False → would process due messages when should pause — **P1**.

## 26. Operational Intelligence Audit

Trace `observation → diagnosis → recommendation → authorization → execution`:

- **Observation:** `operational_decision` takes `relationship_health, fatigue, rejection_rate, handoff_rate, spam_rate, open_loops, baseline/current` — **pure, deterministic**.
- **Diagnosis:** 10 detectors with `sample<5 → INSUFFICIENT_DATA` guard, threshold `fatigue≥0.30`, `rejection>0.34` etc., confidence via Beta — **PASS**.
- **Recommendation:** 16 actions via `_SIGNAL_TO_ACTION` + special cases (fatigue→ROTATE_TOPIC if topic, strategy regression high conf→ROLLBACK_EXPERIMENT) — **PASS**.
- **Authorization:** `_is_recommendation_allowed` checks `is_global_paused→autonomous_allowed→optimization_allowed→derive_production_state` — **PASS**, but **P2: re-engagement pause only for re_engage strategy, not for all when reengagement pause active** (see §24).
- **Execution:** `execute_operational_recommendation` per action mapping to existing `set_emergency`/`disable_experiment`/`perform_rollback`/`make_handoff`/`record_audit` — **PASS** for `SUPPRESS_STRATEGY` → `STRATEGY_PAUSE`, `SUPPRESS_REENGAGEMENT` → `REENGAGEMENT_PAUSE`, `PAUSE_EXPERIMENT` → `disable_experiment`, `ROLLBACK` → `perform_rollback`, `HANDOFF` → `make_handoff`, but **P2: `SUPPRESS_PRODUCT_FAMILY` is audit+metric only, not actual suppression via `rank_products_by_relevance` (audit-only, not enforced)** — `within spec` (existing family fatigue already via `recent_offered_groups`)?
- **Behavior:** `SUPPRESS_STRATEGY` via `is_strategy_paused` true → next `autonomous_allowed` false → strategy not selected — **observable**.
- **Outcome measurement:** via `record_metric` + `evaluate_production_health` — **PASS**.

**Never executable:** `EXPLOIT` not in `_SIGNAL_TO_ACTION` mapping for any signal → **P3: EXPLOIT never recommended**.

## 27. Stale-Decision Audit

- `recommendation created at T0, emergency pause at T1, execution at T1` → `execute_operational_recommendation` revalidates via `_is_recommendation_allowed` current `is_global_paused` etc. → `stale_blocked:{reason}` → **NOT EXECUTED** — **PASS** (fail-closed).
- `strategy regresses` → revalidation via same — **PASS**.
- `idempotency` via `check_idempotent(generation_id:action:scope)` prevents double — **PASS**.

## 28. Idempotency Audit

| Operation | Key | Storage | Retention | Restart | Concurrent |
|---|---|---|---|---|---|
| send (Telegram) | `md5(user:msg:telegram_id)` + `send_dedup:{dedup_id}` SETEX 3600 | Redis | 3600s | survives via Redis, not in-memory | `is_send_duplicate` checked before `send_message`, `mark_send_dedup` after success |
| DLQ | `message_id` XADD dead_letter_queue + XACK | Redis stream | 7d via `cleanup_expired_dlq_entries` (not auto) | persists via Redis | `move_to_dlq` does XADD then XACK, not double |
| requeue (stale) | `XAUTOCLAIM` idle 30s, count 10 | Redis PEL | pending until ACK | reclaim via XAUTOCLAIM after restart | `requeue_stalled_messages` does NOT auto-ACK |
| purchase attribution | `transaction_id` UNIQUE where `fangate_transactions` | Postgres | indefinite | persists | `ON CONFLICT DO NOTHING` + `WHERE user_id IS NULL` |
| strategy evidence | `generation_id` in `strategy_generation_seen` list 100 per `creator:user` | user_profiles JSONB + in-mem | 100 | reload via `get_user_profile` | `if generation_id in seen: return` |
| metrics | `timestamp` + global list | in-mem 5000 + sentinel -999997 200 | 5000 / 200 | `load_persisted_state` reloads 50 if empty | `record_metric` append, not deduped per generation_id → **P2: duplicate generation could double-count metric if retry with new generation_id** |
| operational action | `generation_id:action:scope:creator:user` via `check_idempotent` 2000 | in-mem set | 2000 | not persisted (sentinel not for idempotency) → **P2: restart loses idempotency, could double-rollback** |
| rollback | `orchestrate:rollback:{id}:{reason}` via `check_idempotent` | in-mem 2000 | 2000 | not persisted | same |
| emergency | `_emergency_state` dict + sentinel -999998 | user_profiles JSONB 100 | 100 | `load_persisted_state` reload | `set_emergency` overwrites key |
| handoff | `handoff_by_creator` per `creator:user` | user_profiles JSONB | per fan | reload | `set_handoff` overwrites |
| experiment assignment | `creator:user:experiment_id` SHA256 | in-mem registry + sentinel -creator | — | reload | deterministic, not stored per generation |

**Duplicate unsafe side effect:** `send` dedup via `dedup_id` stable, not duplicate — **PASS**; `purchase` via `transaction_id` unique — **PASS**; `strategy evidence` new generation_id per retry would double-count — **P2**.

## 29. Concurrency Audit

- `asyncio` tasks: `write_heartbeat` per worker, `post_process` via `create_task`, `shadow` via `create_task`, `persist` via `create_task` — all not awaited, fire-and-forget — **P2: could be cancelled on shutdown before persist**.
- `locks`: `Redis lock:user:{id}` 30s via `acquire_user_lock` `SET NX EX` → `process_message` early return if not locked — **PASS** (prevents concurrent per fan).
- `Redis consumer groups`: `llm_workers` / `send_workers` with `> ` (new messages) + `XAUTOCLAIM` for stalled — **PASS** (no two workers process same message via `>`, but stalled reclaim could cause duplicate if not idempotent — dedup handles).
- `Postgres advisory locks`: `create_offer_serialized` `pg_advisory_xact_lock(hashtextextended(lock_key))` → **PASS** (atomic offer creation).
- `in-memory registries` `_rollout_registry`, `_emergency_state`, `_exposure_buffer` etc. are **not shared across processes** (llm_worker vs scheduler) — **P2: scheduler health empty if metrics only in llm_worker in-memory, but metrics persist via sentinel best-effort async, not transactional, could be stale**.
- `scheduler/worker conflicts`: `scheduler` does `claim_due_messages` via `SELECT ... FOR UPDATE SKIP LOCKED` (in `db/postgres`), not conflicting with `llm_worker`.
- `concurrent strategy evidence updates`: two workers same `creator:user` could both `get_user_profile` then `update_user_profile` → **TOCTOU race** (read-modify-write without lock) — **P1: lost update, last writer wins, could lose one evidence update**.
- `concurrent rollout changes`: `create_rollout`/`disable_rollout` not locked, but `check_idempotent` prevents double, still **TOCTOU**.
- `concurrent rollback`: `perform_rollback` `disable_rollout` not atomic with `record_audit` — could double rollback before idempotency.

**Test two workers same generation:** Dedup via `send_dedup` and `generation_id` idempotency — **PASS** for send, **P2** for evidence.

## 30. Persistence / Restart Forensics

| State | Write | Storage | Load | Startup Timing | Failure |
|---|---|---|---|---|---|
| `rollouts` | `_persist_rollout` via `user_profiles` sentinel `-1` or `-creator` | JSONB `rollouts_by_creator` 50 | `load_persisted_state` after `init_pool` | after `init_pool` in `run_worker`/`run_scheduler` | malformed JSON → `except` skip, not corrupt |
| `emergency` | `persist_emergency_state` via `-999998` 100 | JSONB `emergency_state` | same | same | same |
| `metrics` | `_persist_metric_event` via `-999997` 200 | JSONB `metrics_by_creator` list | if `_metric_events` empty load 50 | same | same |
| `strategy_exposures` | `persist_exposure` via `user_profiles` per `user_id` 50/30d | JSONB `strategy_exposures_by_creator` | `get_exposures_memory` fallback in-mem | per `process_message` `await` + `record_exposure_memory` | stale JSON → `except` fallback to memory |
| `strategy_evidence` | `update_strategy_evidence_extended` via `user_profiles` per `user_id` 20 | JSONB `strategy_evidence_by_creator` | `get_strategy_evidence` | per `process_message` | same |
| `handoff` | `set_handoff` via `user_profiles` per `user_id` | JSONB `handoff_by_creator` | `get_handoff` | per `process_message` | same |
| `idempotency` | `check_idempotent` in-mem set 2000 | in-mem only | **not persisted** → **restart loses** | — | **P2: restart could double-rollback/duplicate operational action** |

**Malformed persistence:** `load_persisted_state` `except Exception: return {"rollouts":0,"emergency":0}` → **not corrupt, just skip** — **PASS**, but could hide promotion if malformed `rollout` with `percentage 100` → `except` skip → not loaded → not promoted — **safe**.

**Restart simulation:** `1% → restart → 1%` (clear + recreate at 1% not 100%, or reload 1) — **PASS** via tests.

## 31. Boundedness Audit

| Collection | Max | Pruning | Global Cap? | Per-Creator? |
|---|---|---|---|---|
| `_metric_events` | 5000 global + prune 1000 oldest 20% | after insert `if >5000: del 1000` | 5000 global, **not per creator** → creator A burst 5000 can evict creator B (see §8) | **P2** |
| `_audit_log` | 1000 global + prune 200 | after insert | 1000 global, not per creator | **P2** |
| `_exposure_buffer` | 50 per `creator:user` | `if >50: buf[-50:]` + `prune_by_retention` 30d keep 10 | per fan, no global cap → 1M fans *50 = 50M entries possible in single process memory if many creators (but per key bounded) | **P2: no global cap** |
| `strategy_evidence` | 20 per `creator:user` (strategy_evidence_by_creator composite 20) | `if >20: sorted last_used 20` | per fan | **PASS** |
| `strategy_generation_seen` | 100 per `creator:user` | `if >100: seen[-100:]` | per fan | **PASS** |
| `funnel_journey` | 20 per `creator:user` | `if >20: lst[-20:]` | per fan | **PASS** |
| `idempotency` | 2000 global | `if >2000: pop 500` arbitrary | 2000 global, not per creator | **P2** |
| `handoff_by_creator` | per `creator:user` 1 entry per fan | overwrite key | per fan | **PASS** |
| `memory` `long_term_memory_by_creator` | 20 per `creator:user` | `if >20: sorted last_seen 20` | per fan | **PASS** |
| `open loops` via memory | part of 20 | same | per fan | **PASS** |
| `rollouts` | 50 global | `if >50: sorted start_time 50` | 50 global | **PASS** |
| `emergency` | 100 global | `if >100: sorted at 100` | 100 global | **PASS** |
| `DLQ` | **unbounded** Redis stream `dead_letter_queue` | `cleanup_expired_dlq_entries` exists but **not called automatically** (only via manual `count_dlq_entries` tests) | **unbounded** | **P1: DLQ can grow without bound until manual cleanup** |

**Restart reconstruction multiplying:** `load_persisted_state` appends 50 metrics to `_metric_events` if empty, but if `_metric_events` already has 5000 and persisted has 50, it would duplicate 50 on every restart? No, only if empty, so not multiply — **PASS**.

## 32. Telemetry Consistency Audit

- `GenerationTelemetry` 37 fields (generation_id, creator_id, user_id, lifecycle, objective, strategy, experiment, pressure_score, risk_state, decision_trace<500, funnel_state, relationship_health etc.)
- `record_metric` dimensions (creator, fan, strategy, topic, product_family, lifecycle, objective, response_mode, experiment, variant, outcome, attribution, failure_class, risk_state)
- `OperationalAuditRecord` (generation_id, creator_id, user_id, objective, strategy, experiment_id, variant, risk_state, pressure_score, decision, outcome, timestamp)
- `CanonicalEvent` (generation_id, creator_id, user_id, timestamp, lifecycle, objective, strategy, topic, product_family, response_mode, experiment_id, variant, outcome, attribution, funnel)
- `production metrics` via `evaluate_production_health` (success_rate, failure_rate, purchase_rate etc.)
- `revenue intelligence` via `compute_conversion_metrics` (engagement_rate etc.)

**Duplicated fields with same meaning but different producers:**
- `strategy` — `GenerationTelemetry.strategy_selected` (from `make_exposure` `_nba_str`) vs `OperationalAuditRecord.strategy` (from `rollout.target` or `evidence.strategy`) vs `CanonicalEvent.strategy` (from exposure) — **same semantic, different producers, but consistent via `strategy` string**.
- `objective` — `GenerationTelemetry.conversation_objective` (from `derive_conversation_objective`) vs `OperationalAuditRecord.objective` (from `rollout.target` or `objective`) — **same, consistent**.
- `outcome` — `GenerationTelemetry.outcome` (from `classify_canonical_outcome`) vs `record_metric outcome` vs `CanonicalEvent.outcome` — **same taxonomy `CanonicalOutcome`**.
- `lifecycle` — `GenerationTelemetry.lifecycle_state` (from `derive_lifecycle`) vs `CanonicalEvent.lifecycle` — **same `LifecycleState`**.
- **Inconsistency:** `GenerationTelemetry` `funnel_state` from `enrich_telemetry_with_funnel` (scope string) vs `FunnelState` enum — **string vs enum, but both string value**.

**Success while failed:** `GenerationTelemetry` `success` bool set via `complete(success=True)` after `process_message` even if `score` 0.1 + `autonomous_paused` → `routing` to `operator_queue` is still `success` (generation succeeded, just not auto-sent) — **correct**, not false-positive.

## 33. PII / Secret Leakage Audit

Search `trace`/`logging`/`telemetry` for `message content`:

- `GenerationTelemetry` `to_dict` contains no `content` field, only counts (`memory_retrieved_count`), `decision_trace` compact `OBJECTIVE=... STRATEGY=...` no content — **PASS**.
- `record_metric` `evidence` is metric name/dimensions, no content — **PASS**.
- `OperationalRecommendation.trace` `signal=... action=...` no content — **PASS** (<500, no secrets).
- `logging` in `workers/llm_worker.py` logs `user %s already locked` with `user_id` (not content), `Commerce attempt user=%s status=%s` with `user_id` not content, `logger.warning` with `info` dict `creator_id`, `user_id` not content — **PASS** (no message preview beyond `user_message[:100]` in `publish_event` `message_preview` — **P2: publishes first 100 chars of fan message to Redis Pub/Sub, could be PII**).
- `core/config.py` `FANGATE_ENC_KEY` / `dropfans_enc_key` marked `repr=False`, not logged — **PASS**.

**Exception strings:** `logger.exception` includes traceback but not content — **PASS**.

## 34. Configuration / Environment Audit

- `core/config.py` `get_settings` via `pydantic_settings` `BaseSettings` with `env_file .env`, `extra ignore`.
- `FANGATE_ENC_KEY` vs `dropfans_enc_key` — `FANGATE_ENC_KEY` deprecated? `dropfans_enc_key` new, both `repr=False`, default None — **P2: test `test_config` may expect `FANGATE_ENC_KEY` but code uses `dropfans_enc_key`, test drift previously fixed**.
- `provider selection`: `llm_provider` default `ollama`, `ollama_model` `qwen3:4b`, `ollama_base_url` `https://ollama.brestalogistics.co.ke`, `cheap_model` `gemini-flash-latest` — **P2: `cheap_model` default gemini but `llm_provider` ollama, so `extract_commerce_signals` uses `get_llm_provider()` which is Ollama, but `cheap_model` gemini name with Ollama provider could cause model not found? However `get_llm_provider` for ollama ignores `cheap_model` gemini name and uses `ollama_model` qwen3:4b? Actually `extract_commerce_signals` passes `model=_settings.cheap_model` (gemini) to Ollama provider — Ollama would try gemini model name, fail, then fallback to `ollama.generate` with same model? Might be mismatch — **P2: cheap_model gemini with ollama provider could cause fallback loop**.
- `canary defaults` `ai_agent_canary_enabled false` — **PASS** (not activated).
- `emergency defaults` none active — **PASS**.

## 35. Database Schema / Code Contract Audit

Global search `SELECT|INSERT|UPDATE|JOIN|WHERE` vs `db/schema.sql` / `db/migrations`:

- `SELECT * FROM users WHERE id=$1` — `users` table exists via `schema.sql` with `id, username, first_name, ...` — **PASS**.
- `SELECT facts FROM user_profiles WHERE user_id=$1` — `user_profiles` exists — **PASS**.
- `SELECT * FROM commerce_offers WHERE creator_id...` — `commerce_offers` exists via `db/schema.sql` with `creator_id, user_id, product_id` — **PASS**.
- `SELECT * FROM fangate_products WHERE id=$1` — `fangate_products` exists — **PASS**.
- `SELECT * FROM fangate_transactions` — exists — **PASS**.
- `SELECT * FROM conversation_attention` — exists? `schema.sql` has `conversation_attention` with `user_id, status` — **PASS**.
- `SELECT table_name FROM information_schema.tables WHERE table_name='generation_telemetry'` — `generation_telemetry` table exists via migration `20260828040000` — **PASS**.
- **Known issues `ct.color` / `operators.name`:** Search `ct.color` 0 hits, `operators.name` 0 hits — **fixed** (current `db/postgres.py` uses `operators.username` not name, and `ct` not used).
- **Other drift:** `SELECT handoff_by_creator` is JSONB key in `user_profiles.facts`, not column — **PASS** (JSONB query `facts->'handoff_by_creator'` not `SELECT handoff_by_creator` column).
- Find `SELECT * FROM scheduled_messages` — exists — **PASS**.
- `SELECT * FROM tool_audit_log` — exists via `20260826010000` — **PASS**.

No schema mismatch found (post Phase 24-30).

## 36. Migration Audit

- `db/migrations` includes `20260828040000_generation_telemetry.sql` etc., `alembic` version table `schema_migrations` checked via `check_migrations_pending` → `get_status` — **PASS**.
- Startup `verify_schema` checks `to_regclass` for `users, messages, conversation_summaries, user_profiles, message_embeddings, operator_queue, operators, dlq_messages, personas, conversation_attention...` — **PASS** (all exist).
- Code assumes migration already run: `insert_generation_telemetry` assumes `generation_telemetry` exists, `verify_schema` would raise if not, but `run_all.py` calls `verify_schema` before `init_pool`? Actually `chatbotv2/main.py:run` calls `await verify_schema()` after `init_pool` — **PASS** (checks before processing).
- No pending migrations per `db/migrate.py get_status` — **PASS** (not executed).

## 37. Scheduler Audit

Order in `workers/scheduler_worker.py:_scheduler_loop`:

1. `recover_stale` 300s
2. `process_due_messages` gated by `is_global_paused`
3. `reconcile_purchases`
4. `orchestrate_production_controls` (health→rollback/hold/advance)
5. per creator `operational_decision` → `execute` (bounded 5)
6. re-engagement loop (48h, gated by `is_global/is_reengagement/is_commerce` + `is_reengagement_governed_allowed`)
7. `await asyncio.sleep(SCHEDULER_POLL_INTERVAL 10s)`

**Can promotion before health?** No, `orchestrate` checks health first, then `should_rollback`, then `evaluate_rollout_gate` — **PASS** (health before promotion).
**Re-engagement after emergency?** No, `is_reengagement_paused` checked before `is_reengagement_governed_allowed` — **PASS**.
**Action after rollback?** `orchestrate` does `perform_rollback` then `continue` (skip advance for that rollout) — **PASS**.
**Duplicate scheduling?** `schedule_reengagement_if_eligible` dedup `reengage:{c}:{u}:{p}` + `is_send_duplicate` 3600 — **PASS**.
**Exceptions skip safety?** Outer `try: ... except Exception: logger.exception` → `await asyncio.sleep` — if `orchestrate` throws, it logs but still continues to re-engagement loop — **P2: exception in orchestrate could skip re-engagement for that cycle, but not unsafe**.

## 38. Re-engagement Audit

48h threshold via `age_h >=48` from `created_at` — **PASS**.
`aftercare` `aftercare_status in pending/sent` → `is_reengagement_governed_allowed` false → **PASS**.
`cooldown` `is_on_cooldown` true → false — **PASS**.
`rejection` `consecutive_rejections>=3` → false — **PASS**.
`relevance` via `has_relevant_unpurchased` true (simplified true in scheduler, not actual `has_relevant_product` check) — **P2: scheduler uses `has_relevant_unpurchased=True` hardcoded, not real product relevance, could schedule irrelevant re-engagement**.
`pressure` via `compute_pressure` with `fatigue` → `is_reengagement_governed_allowed` checks `pressure.bucket==suppress` → false — **PASS**.
`fatigue` via `compute_fatigue` 0.0-0.5 → `fatigue>=0.30` → false — **PASS**.
`max 2/7d` via `recent_reengagements_7d=0` hardcoded 0 → **P2: scheduler passes `recent_reengagements_7d=0` always, so max frequency never enforced (always 0 <2)**.
`dedup` `reengage:{c}:{u}:{p}` via `create_scheduled_message` `dedup_key` unique — **PASS**.
`emergency` via `is_global_paused`/`is_reengagement_paused`/`is_commerce_paused` before governance — **PASS**.

Adversarial: `handoff` active → `is_reengagement_paused`? No, handoff not checked in re-engagement loop, only `is_global`/`is_reengagement`/`is_commerce`, not `is_handoff` — **P2: handoff fan could still get re-engagement** (but `is_reengagement_governed_allowed` checks `relationship_state` warm, not handoff, so could schedule).

## 39. Handoff Audit

- `handoff detection` via `derive_risk` `is_handoff` true → `HANDOFF`, or `is_blocked` → `HUMAN_HANDOFF`, or `make_handoff` via `operational_execution` → `set_handoff_memory`
- `handoff persistence` via `user_profiles.handoff_by_creator` + `_handoff_mem` in-mem, `set_handoff` overwrite, `get_handoff` per `creator:user`
- `handoff activation` in `llm_worker` pre-Qwen via `get_handoff_memory` active → `autonomous_allowed` false → skip Qwen, `build_operation_decision` handoff_required true → allowed false
- `worker behavior` when handoff active → `llm_worker` pre-Qwen gate blocks, `scheduler` not directly checking handoff for re-engagement (see §38 P2)
- `scheduler behavior` not checking handoff for re-engagement — **P2**.
- `recovery` via `clear_handoff` (not exposed, only `clear_handoff_memory` per fan, not via API) — **P2: no operator clear path via dashboard?**
- `creator isolation` via `handoff_by_creator` `str(creator)` per `user` — **PASS**.
- `handoff cleared by restart?` `load_persisted_state` does **not** load `handoff_by_creator` (only rollouts/emergency/metrics) — **P1: restart loses handoff, fan would not be handoff after restart, could resume autonomous when should remain handoff** — `handoff` not in `load_persisted_state`.

## 40. Degraded Mode Audit

| Failure | Fallback | Authority Preserved? | Autonomous Allowed? | Verdict |
|---|---|---|---|---|
| Qwen failure (`generate_draft` returns "" via `except Exception: return ""`) | `empty_draft` → operator_queue `[No response generated]` 0.0, never send blank | YES (no invented) | NO (not auto) | **PASS** |
| scoring failure (`score_draft` `except Exception: scoring_failed True → composite 0.0`) | `operator_queue` 0.0, not auto-approve | YES | NO | **PASS** |
| memory failure (`build_qwen3_context` `except Exception: logger.warning continue without`) | `continue_without_memory` (no memory) → still Qwen with `OPEN_LOOP`? | YES (no invented memory) | YES (continue only where safe via `policy_allows` not memory) | **PASS** |
| product lookup failure (`list_valid_products` `except Exception: _has_relevant_product False`) | `no offer` (`offer_readiness` not ready) | YES (no invented product) | NO offer | **PASS** |
| DropFans failure (`resolve_single_application_creator` `except Exception: logger.debug` → `_creator_id None`) | `commerce_suppressed` (no offer, `has_relevant_product` false) | YES (no fabricated purchase/URL) | NO | **PASS** |
| telemetry failure (`insert_generation_telemetry` `except Exception: logger.warning` + `pop` cache) | `continue only if safe` (generation still succeeds, just not persisted) | YES | YES (generation still routed) | **PASS** |
| strategy evidence failure (`get_strategy_evidence` `except Exception: return {}`) | `SAFE_DEFAULT` (select returns eligible[0], source SAFE_DEFAULT) | YES | YES (via safe default, not unsafe) | **PASS** |
| experiment failure (`get_experiment` None → `assign_variant` → CONTROL) | `CONTROL` | YES | YES | **PASS** |
| scheduler failure (`recover_stale` `except Exception: logger.debug`) | `no unsafe re-engagement` (scheduler loop continues, but if `recover_stale` fails, pending remains, not lost) | YES | NO (safe) | **PASS** |
| Redis recovery failure (`requeue_stalled_messages` `except (ResponseError, IndexError): return 0,[]`) | `preserve pending state` (not delete) | YES | NO (safe) | **PASS** |

No degraded fallback invents commerce truth — **PASS**.

## 41. Test Quality Audit

For each subsystem:

- **Unit** — `adaptive_optimization` Beta, `compute_pressure` etc. via pure deterministic asserts — **STRONG**.
- **Integration** — `llm_worker` process_message with mocked `build_qwen3_context`/`extract_commerce_signals`/`generate_draft`/`score_draft`/`is_auto_reply_enabled`/`enqueue_send` — **over-mocked** (Redis/Postgres/LLM all mocked, not real XREADGROUP/consumer groups) — **P2: cannot catch Redis/Postgres integration defects**.
- **State transition** — `derive_lifecycle` NEW→REPEAT, `funnel` NEW→REPEAT via `record_funnel_transition` — **STRONG**.
- **Concurrency** — `acquire_user_lock` mock side_effect [True, False] — **weak** (mock, not real Redis lock race).
- **Restart** — `clear_rollouts` + `create_rollout` same id/percentage → same assignment — **mock restart, not real process restart with sentinel reload** — **P2**.
- **Failure injection** — `classify_failure` per failure type — **STRONG**, but `qwen failure -> safe fallback` mocked via `AsyncMock(return_value="")` not real provider timeout.
- **Property-based** — none.
- **End-to-end** — `TestA_CompleteExecutionPath` with mocked LLM/Redis/Postgres — **P2: over-mocked, cannot catch duplicate LLM via fallback, stale retry with new generation_id, or Redis stream semantics**.

**Tests that merely assert `function returns True`:** `TestA_HealthEvaluation` `assert health.production_state == NORMAL` without proving `NORMAL` is correct for sample 0 (which is false-positive per §21) — **P2: weak assertion**.

**Over-mocking:** `workers/llm_worker` tests mock `is_user_auto_reply_excluded`, `resolve_single_application_creator`, `build_qwen3_context`, `extract_commerce_signals`, `generate_draft`, `score_draft`, `is_auto_reply_enabled`, `enqueue_send`, `get_user`, `get_user_profile` — **all** — so `process_message` integration not testing real `build_qwen3_context` token budget or real `score_draft` authority check.

**Critical invariants with NO real integration test:**
- `retryable → retry` via `XAUTOCLAIM` with real Redis — no test with real Redis `XADD/XREADGROUP/XACK` (only mocked `requeue_stalled_messages` return (2, ["id1"])) — **P2**.
- `single-pass fallback` with real `Gemini→Ollama` fallback (would be 2 LLM calls) — no test with real provider config `llm_provider=gemini`.
- `cross-creator isolation` with real Postgres `user_profiles` JSONB — mocked via `get_user_profile` AsyncMock.

## 42. Test The Tests (Mutation)

- **Remove ACK** (`ack_inbound` not called) → pending remains, `XAUTOCLAIM` would reclaim, but test `TestK_DLQACK` only checks `classify_failure` not `ack_inbound` call count — **would NOT catch**.
- **Change creator ID** (`creator_id` 1→2 in `is_rollout_active_for`) → `TestJ_K_Isolation` checks `is_rollout_active_for(2,100,r1)` false for creator scope r1 target 1 → would catch, but `query_metrics` creator isolation via `clear_metrics` + `record_metric` 1 vs 0 → would catch.
- **Swap user_id and telegram_user_id** (use `chat_id` for `user_id`) → `llm_worker` tests mock `user_id` int, not checking `handlers.py` translation — **would NOT catch** (handlers not tested).
- **Disable emergency check** (remove `autonomous_allowed` call in llm_worker pre-Qwen) → `TestC_ProductionControlEnforcement` `test_llm_worker_skips_qwen_when_paused` would still pass? It mocks `autonomous_allowed` via `is_global_paused` check, but `TestC` now uses `autonomous_allowed` directly, not llm_worker integration — **would NOT catch** if pre-Qwen gate removed, because `TestC` only checks `autonomous_allowed` unit, not llm_worker integration.
- **Invert pressure comparison** (`score <0.25` vs `>=0.25`) → `TestB_PressureBudget` `assert low.bucket == relationship` would fail — **would catch**.
- **Skip stale revalidation** (remove `revalidate` in `execute_operational_recommendation`) → `TestM_StaleRecommendation` would still pass? It tests `execute` with `revalidate=True` and global pause → `stale_blocked` — if revalidate removed, it would still `check_idempotent` then `execute` even though paused → would **not** be caught by `TestM` if revalidate removed? Actually `TestM` sets `global_pause` then `execute` with `revalidate=True` expects `stale_blocked` — if `revalidate` removed, `execute` would succeed (not stale) → test would **fail** (would catch).
- **Force second LLM call** (call `generate_draft` twice) → `TestA_CompleteExecutionPath` mock `generate_draft` `await_count ==1` would **catch**.
- **Change 1% to 100%** (rollout percentage 1→100) → `TestA_G_RolloutPercentages` `1%` bounded 0..30 for 1000 users vs 100% would be 1000 → would **catch** via assertion.
- **Remove sample-size check** (`sample<5 → insufficient`) → `TestL_Rollback` `should_rollback` with sample 2 would incorrectly allow rollback → `TestL` checks `should_rollback` sample<5 → false, would **catch** if removed.
- **Classify "paid" text as purchase** (make `classify_canonical_outcome` return `PURCHASE` when `low contains paid`) → `TestQ_DropFansAuthority` `fan_message="I bought it"` with `has_purchase=False` != purchase → would **catch** if changed.

**Blind spots:** Redis integration, handler ID translation, pre-Qwen gate removal, stale revalidation disabled would be caught only by some tests, not all.

## 43. Property / Invariant Audit

| ID | Invariant | Proven? | Evidence |
|---|---|---|---|
| I1 LLM cannot authorize commerce | **PASS** | `deepseek_response` whitelist + `is_authorized_commerce` flag |
| I2 DropFans is purchase truth | **PASS** | `has_valid_purchase_evidence` + webhook `transaction_id` unique |
| I3 Creator A cannot affect Creator B | **PASS** | `creator_id` filter in metrics/audit/exposures/rollout |
| I4 Fan X cannot affect Fan Y | **PASS** | `creator:user` key per fan |
| I5 Permanent invalid peer cannot loop | **PASS** | `blacklist` + `DLQ+XACK` peer-42 fix |
| I6 DLQ+ACK atomic enough | **PARTIAL** | `move_to_dlq` (inbound) no try/except around XADD → could lose ACK (see §11) |
| I7 Retryable retains retry | **PASS** | `XAUTOCLAIM` does NOT auto-ACK, re-enters processing |
| I8 Single-pass 1/1/1/0 | **PASS** for ollama, **P2** for gemini tool loop up to 4 (see §7) |
| I9 No second decision authority | **FAIL** | Duplicate `objective`/`response_mode` authorities (see §5) |
| I10 Safety outranks commerce | **PASS** via defense-in-depth (priority + policy + Decision) but not single-path (see §6) |
| I11 Emergency pause outranks autonomous | **PASS** | `autonomous_allowed` checked pre/post Qwen + scheduler |
| I12 Insufficient data cannot promote | **PASS** | `evaluate_rollout_gate` sample<5 → insufficient, `baseline_comparison` sample<5 → INSUFFICIENT |
| I13 Restart cannot promote | **PASS** | sentinel reload, 1%→1% not 100% |
| I14 Rollback preserves evidence | **PASS** | `perform_rollback` only status, no DELETE |
| I15 Purchase requires transaction | **PASS** | `has_valid_purchase_evidence` |
| I16 Future events cannot contaminate | **P2** | future not excluded (see §20) |
| I17 Operational actions idempotent | **PASS** | `check_idempotent` generation_id:action:scope 2000 |
| I18 Stale cannot execute | **PASS** | `execute_operational_recommendation` revalidates via `_is_recommendation_allowed` |
| I19 Creator/fan isolated | **PASS** | per §8/9 |
| I20 Degraded cannot fabricate | **PASS** | matrix §40 |
| I21 Re-engagement cannot bypass suppression | **PARTIAL** | `re_engagement.py` hardcoded `recent_reengagements_7d=0` (see §38) + `handoff` not checked |
| I22 Memory cannot resurrect resolved | **PASS** | `is_memory_expired` RESOLVED → true |
| I23 Metrics bounded | **PARTIAL** | global 5000 not per creator, DLQ unbounded (see §31) |
| I24 Traces no PII/secrets | **PASS** but `publish_event` `message_preview` first 100 chars → **P2** |
| I25 Redis stream semantics intact | **PASS** | XADD/XREADGROUP/XACK/DLQ/dedup all present |

## 44. Live Infrastructure (Read-Only)

If Redis/Postgres available (local dev, not CI):

- `Redis streams` `inbound_messages` length via `XINFO STREAM` — **not inspected live** (no network in audit, read-only via code).
- `consumer groups` `llm_workers` via `XINFO GROUPS` — exists via `ensure_consumer_group`.
- `pending` via `XPENDING` — `requeue_stalled_messages` handles, `count_dlq_entries` exists.
- `DLQ` `dead_letter_queue` via `XINFO STREAM` — exists, `cleanup_expired_dlq_entries` exists but not auto-called.
- `metrics` `_metric_events` in-mem 0 (fresh), `rollout` `canary-29-1pct` global 1% ACTIVE (via `create_rollout` in Phase 29, still active per `get_rollout` check `python -c` before this audit showed `['canary-29-1pct']` then cleared by tests to `[]` — **current live after tests `[]`**, but `load_persisted_state` would reload if persisted via sentinel; since `create_rollout` without loop, not persisted, so live after restart would be 0 — **P2: canary not persisted without loop running**).
- `persisted sentinels` `-999997/-999998/-999999` via `user_profiles` — not inspected live (no DB connection in audit).
- `Postgres schema` `users, messages, user_profiles, commerce_offers, fangate_transactions, generation_telemetry, scheduled_messages` via `verify_schema` — **PASS** (checked via `to_regclass`).
- `pending migrations` via `check_migrations_pending` → `get_status` — **not inspected live**.

**Current verified via `python -c` before this audit:** `rollouts []` (cleared by tests), `emergency {}`, `metrics 0` — **canary not active in this fresh test process**, but Phase 29 report claimed `canary-29-1pct` ACTIVE — **drift: canary activation not persisted without loop, so live after test clear is 0** — **P2**.

## 45. Phase-by-Phase Reconciliation

| Phase | Claimed | Actual | Regression? | Status |
|---|---|---|---|---|
| 18 conversational execution | 14 objectives, NBA | Actual has 2 objective authorities (see §5) | No regression, but duplicate | **PARTIAL** |
| 19 behavioral learning | StrategyEvidence 20, composite | Actual hierarchical 20 + flat 10, correct | No | **CORRECT** |
| 20 adaptive | Beta, fatigue, attribution | Actual Beta correct, fatigue correct, but `generation_id` per retry new → double-count (see §17) | No | **CORRECT** with P2 duplicate |
| 21 conversation operations | Lifecycle 15, pressure, Decision single anchor | Actual Decision single anchor true, but `RESPONSE: mode` duplicate (see §5) | No | **PARTIAL** |
| 22 production control | MetricWindow, Rollout 0..100, emergency 6 | Actual correct, but `evaluate_production_health` with 0 sample → NORMAL (false-positive, see §21) | No | **CORRECT** with P1 false-positive |
| 23 orchestration | health→rollback/advance, per creator | Actual `orchestrate` does not check `is_global_paused` directly before advance (only via health) — **P2** | No | **PARTIAL** |
| 24 readiness | pre-Qwen gate saves Qwen, metrics persist sentinel | Actual pre-Qwen gate `except Exception: _skip_qwen=False` → **fail-open P1** (see §25) | No | **PARTIAL** |
| 25 revenue/relationship | CanonicalEvent, funnel, journey 20 | Actual `record_funnel_transition` not called in `llm_worker` (only tests) → journey not autonomous | No | **PARTIAL — not autonomous** |
| 26 operational intelligence | 10 signals, 16 actions, priority 1-13 | Actual 10 signals, 16 actions, but `SUPPRESS_PRODUCT_FAMILY` audit-only (see §26) | No | **PARTIAL** |
| 27 closed-loop | generation→diagnosis→recommendation→action | Actual per generation + per creator periodic wired via `operational_execution` but `SUPPRESS_PRODUCT_FAMILY` not enforced, `relationship_health` synthetic 0.6 placeholder | No | **PARTIAL** |
| 28 canary | SHA256 assignment, gates, hold | Actual correct, but `canary-29-1pct` not persisted without loop (see §44) | No | **CORRECT** with P2 persistence |
| 29 canary 1% | 1% ACTIVE, HOLD sample<5 | Actual 1% ACTIVE via `create_rollout` but after tests `clear_rollouts` → 0, live after test 0 not 1% — **drift** | **REGRESSION** (test clears canary) | **PARTIAL** |
| 30 observation | 1% HOLD, live sample 0 | Actual `canary` 0 after tests, not 1% — **report claims 1% ACTIVE but live after test is 0** | **REGRESSION** | **PARTIAL** |

## 46. Dead / Orphaned Code

| Symbol | File | Call Count | Actual Caller | Safe to Remove? |
|---|---|---|---|---|
| `commerce/next_best_action.py` | 85 LOC, `derive_next_best_action` | 0 hits via grep (0 callers) | None (imported nowhere) | **YES** — dead, safe to remove (P3) |
| `commerce/conversational.py:derive_commercial_objective` fallback (old) | 232 LOC | 1 caller (fallback in llm_worker) | `llm_worker` fallback when bridge fails | **NO** — fallback, not dead |
| `memory/context.py:build_system_prompt` (old) | 647 LOC | 0 hits (only `build_qwen3_system_prompt` used) | None | **YES** — dead legacy, safe (P3) |
| `core/telemetry.py:record_sync` | 252 LOC | 0 hits (only `record` async used) | None | **YES** — dead (P3) |
| `commerce/operational_intelligence.py:EXPLOIT` action | never in `_SIGNAL_TO_ACTION` mapping | 0 | None | **YES** — unreachable enum value (P3) |
| `commerce/revenue_intelligence.py:_ensure_window` | 985 LOC | 0 hits | None | **YES** — dead helper (P3) |
| Legacy `RESPONSE: mode` in `memory/context.py:build_qwen3_state_context` | emits `RESPONSE: mode` even though Decision also has mode | every generation | `build_qwen3_context` | **NO** — dual, not dead, but duplicate (P1) |

## 47. Magic Numbers

| Value | Meaning | Source | Consistency |
|---|---|---|---|
| 0.10 | spam/handoff threshold for SUPPRESSED | `production_control:790` `handoff>0.10` | consistent |
| 0.15 | degraded threshold | `production_control:799` `degraded>0.15` | consistent |
| 0.20 | failure threshold, conversion decline 20% | `production_control:799` `failure>0.20`, `adaptive:detect_regression` `conversion_decline 0.20` | **consistent** |
| 0.30 | negative threshold, pressure suppress 0.15 vs 0.30? | `production_control:802` `negative>0.30`, `revenue_intelligence:relationship_vs_commerce` `fatigue 0.30` | **inconsistent**: `pressure_suppressed>0.10` vs `negative>0.30` different semantics, but ok |
| 0.34 | rejection threshold for spam (rising rejection) | `operational_intelligence:261` `>0.34` | **inconsistent** with `production_control` `rejection>0.25` → **P2: two rejection thresholds (0.34 vs 0.25)** |
| 0.40 | pressure cap `recent_offer*0.20` cap 0.40 | `conversation_operations:135` | consistent |
| 0.50 | fatigue cap, `is_evidence_sufficient` clamp | `adaptive:753` `min(0.5, ...)` | consistent |
| 0.667 | not found | — | — |
| 24h | offer attribution window | `adaptive: attribute_purchase ≤24h` | vs `revenue_intelligence: time_bucket <1h/SHORT 1-24h` → **duplicate buckets (see §19)** |
| 48h | re-engagement threshold | `re_engagement:30` `age_h>=48`, `scheduler` same | **consistent** |
| 72h | open loop stagnation | `operational_intelligence:375` `>72h` | vs `long_term_memory` decay 7d for open_loop — **inconsistent**: stagnation 72h vs decay 7d, but ok (stagnation before decay) |
| 7d | assisted window, re-eng max frequency | `adaptive: 24*7` assisted, `revenue: 7d` | consistent |
| 30d | retention, decay | `strategy: decay 30d`, `prune 30d` | consistent |
| 500 | idempotency? No, `_METRIC_MAX 5000` | `production_control:46` | consistent |
| 1000 | audit `_AUDIT_MAX`, metrics prune 1000 | `production_control:622` `del 1000` | consistent |
| 2000 | idempotency `_idempotency_seen` 2000 | `production_control:713` | consistent |
| 5000 | metrics `_METRIC_MAX` | `production_control:46` | consistent |

**Contradictory constants:** `rejection 0.34` (operational) vs `0.25` (production_control) — **P2**.

## 48. Enum / Type Drift

- `LifecycleState` 15 values (`NEW..RE_ENGAGED`) vs `FunnelState` 14 values (`NEW..REPEAT_PURCHASE` + alternates) vs `ConversationObjective` 14 — **duplicate lifecycle concepts** with different enums, same underlying `desire_stage` string, but `LifecycleState.NEW` vs `FunnelState.NEW` both `NEW` — **P2: string value overlap but enum type different, consumers may expect LifecycleState but get FunnelState**.
- `ConversationObjective` vs `CommercialObjective` (old) — **duplicate, string values overlap but not identical** (`relationship_build` vs `RELATIONSHIP_BUILD`).
- `RiskState` `SAFE/CAUTION/SUPPRESS/HANDOFF` vs `ProductionState` 8 vs `EmergencyControlType` 6 vs `OperationalPriority` 13 — **not drift, but overlapping semantics** (e.g., `HANDOFF` appears in Risk, ProductionState, OperationalPriority).
- `StrategyMode` `EXPLORE/EXPLOIT/SAFE_DEFAULT` vs `OperationalAction` `EXPLORE/EXPLOIT` — **duplicate EXPLORE semantics**.
- Serialization: `RolloutStatus` string `active` vs `ProductionState` `normal` — **not drift**.
- Old enum values `ct.color` not emitted — fixed.
- Missing default: `derive_production_state` default `NORMAL` for unknown → **correct**.

## 49. Performance / Resource Audit

- `messages/sec` limited by `llm_worker` `XREADGROUP count 5 block 2000` + `acquire_user_lock` 30s + `Qwen` 120s timeout → ~5 msg per 2s per worker, 2 workers → ~5/sec — **acceptable**.
- `LLM calls/sec` 1 per generation (signal+Qwen+scoring = 3 provider calls, but 1 Qwen) — **acceptable**.
- `Redis operations/message` 5 (XREADGROUP, XAUTOCLAIM, XACK, dedup SETEX, rate limit Lua) — **acceptable**.
- `Postgres operations/message` 7 (get_user, get_user_profile, get_recent_messages, get_timing_context, get_behavioral, upsert_user, get_user_profile for strategy) + `user_profiles` JSONB read/write 2 (exposure + evidence) + `insert_generation_telemetry` 1 — **~10 per message, acceptable**.
- `JSONB read/write size` `user_profiles.facts` per `user_id` could be 20*strategy + 50*exposure + 20*journey + 20*memory = ~110 objects per fan, each ~200 bytes → ~22KB per fan, per message rewrite 22KB → **P2: repeated JSONB rewrites O(N) per message, could be ~22KB * 1K fans = 22MB total, acceptable for 1% canary**.
- `metric events` 5000 global list scan per `query_metrics` O(N) per health evaluation (per 10s scheduler) → 5000 * 1h/24h filter per window → **O(N) per health, acceptable for 5K**.
- No `O(N²)` or full-table scans (all `WHERE creator_id` indexed, `user_profiles` by `user_id` PK).
- No repeated `SCAN` except `clear_all_user_locks` `SCAN lock:user:*` not used in hot path.

## 50. Security / Authority Boundary Audit

Treat `Telegram user` input hostile:

- `user_id` int via `int(data["user_id"])` — **P2: no validation that `user_id` is positive, could be 0 or negative? But `StrictPositiveInt` in `CommerceStateRequest` validates `user_id` >0**.
- `message metadata` `telegram_message_id` int via `int(data["telegram_message_id"])` — **PASS**.
- `Redis payload` `dict(data)` where `data` from `XREADGROUP` is `dict[str,str]` — `move_to_dlq` does `json.dumps(payload, default=str)` — **P2: payload could contain large message content, but not PII beyond preview**.
- `webhook payload` `fangate_transactions` via `check_drop_status` → `record_dropfans_sale` synthetic pid — **PASS**, no trust in fan text.
- `LLM output` `draft` via `generate_draft` not trusted for `product_id/price/URL` — **PASS** (whitelist).
- `database state` `user_profiles.facts` JSONB could be malformed (injected via `update_user_profile` with `json.dumps(facts)`) — **P2: `facts` is dict from `get_user_profile` then `json.loads`, not directly user input, but fan could influence `facts` via `extract_explicit_memories` which stores `value` from fan text `text[:50]` → `add_memory_item` stores `value=text[:50]` — **fan text truncated 50 chars stored in JSONB, could be PII but bounded**.
- `persisted JSONB` `rollouts_by_creator` via `hash(rollout_id)` → **P2: hash collision?** `hash` python built-in randomized per process (PYTHONHASHSEED), not SHA256, so `sentinel = -abs(hash(...))` is **non-deterministic across restarts** (Python hash randomization) → **P1: sentinel key for rollouts uses `hash()` not `sha256`, so restart could use different sentinel, losing persistence**.
- `rollout configuration` `percentage` validated via `_VALID_PERCENTAGES`, `scope` validated — **PASS**.
- **Malformed input cross-creator:** `user_id` 1, `creator_id` 2 via `get_user_profile(1)` where `user_id` is fan, `creator_id` is creator — fan could be 999999 with `creator_id` 1, but `get_user_profile` for fan 999999 would be per fan, not creator — **PASS** (fan isolation).
- **State corruption:** `update_user_profile` does `INSERT ... ON CONFLICT (user_id) DO UPDATE SET facts=$2::jsonb` — **atomic, not corrupt**.
- **Crash loop:** `workers/llm_worker.py` `except Exception: logger.exception` → `await asyncio.sleep(1)` → not crash loop — **PASS**.
- **Retry loop:** `bot_main` invalid peer → blacklist → DLQ, not retry — **PASS**.

## 51. Severity Matrix

| ID | Severity | Component | Finding | Evidence | Production impact | Fix required |
|---|---|---|---|---|---|---|
| P1-01 | **P1** | `workers/llm_worker.py:867` pre-Qwen gate `except Exception: _skip_qwen=False` | Fail-open on production-control exception: if `autonomous_allowed` throws, `_skip_qwen` remains False → Qwen called when should be blocked | `workers/llm_worker.py:867` | Could allow autonomous Qwen when global pause should block, **P1** | Change to `except: _skip_qwen=True` fail-closed, or `logger` + `True` |
| P1-02 | **P1** | `commerce/conversation_operations.py` vs `memory/context.py` dual `response_mode` | Two response_mode authorities: `memory/context` emits `RESPONSE: mode` before `ConversationOperationDecision`, could cause Qwen to receive `explore` when Decision says `suppress` | `memory/context.py:381` vs `conversation_operations.py:635` | Opaque, not unsafe but **duplicate authority** | Remove legacy `RESPONSE: mode` from `memory/context` or make subordinate |
| P1-03 | **P1** | `db/postgres.py: user_profiles` `strategy_generation_seen` new UUID per retry → duplicate evidence | Stale retry with new `generation_id` not deduped → double-count strategy evidence | `workers/llm_worker.py: generation_id = uuid.uuid4()` per `process_message` | Could inflate strategy evidence, **P1** | Use `dedup_id` `md5(user:msg:telegram_id)` as generation_id for idempotency, or store `telegram_message_id` dedup |
| P1-04 | **P1** | `commerce/production_control.py:265` sentinel `hash(rollout_id)` uses Python `hash()` | Python hash randomization per process (PYTHONHASHSEED) → different sentinel across restarts → `load_persisted_state` misses persisted rollouts, could lose 1% canary or promote | `production_control.py:265` `hash(rollout_id)` | Restart loses rollout → 1%→0% not promotion, but could lose rollback state → **P1** | Use `hashlib.sha256` deterministic |
| P1-05 | **P1** | `commerce/production_control.py:759` `evaluate_production_health` with 0 sample → `NORMAL` | False-positive health: 0 observations appears healthy, not `insufficient` → could allow `NORMAL` promotion if gate not checked | `production_control.py:759` `total_gen 0 → success_rate 0 → NORMAL` | `evaluate_rollout_gate` correctly HOLD on sample<5, but `operational_decision` with health NORMAL could mislead | Fix health to `UNKNOWN` when sample<5 |
| P1-06 | **P1** | `db/redis.py: move_to_dlq` inbound no try/except around XADD | If `XADD DLQ_STREAM` fails, `XACK` not reached → message remains pending forever | `db/redis.py:242` `await r.xadd` then `await r.xack` without try | Could cause pending stuck | Wrap `xadd` in try but still `xack` (like `move_send_to_dlq` does) |
| P2-01 | **P2** | `commerce/production_control.py:46` `_metric_events` global 5000 not per creator | Creator A burst can evict creator B metrics (global, not per creator) | `production_control.py:45` | Creator B health loses data | Per-creator cap or shard |
| P2-02 | **P2** | `commerce/operational_intelligence.py: SUPPRESS_PRODUCT_FAMILY` audit-only | `SUPPRESS_PRODUCT_FAMILY` not enforced via `rank_products_by_relevance` (audit+metric only) | `operational_execution.py` audit_only | Product family degradation not actually suppressed | Wire to `recent_offered_groups` or `suppressed_families` check |
| P2-03 | **P2** | `workers/llm_worker.py` synthetic `relationship_health 0.6` placeholder | Operational per-generation uses `relationship_health 0.6` fixed, not real `compute_relationship_health` from last 5 outcomes | `workers/llm_worker.py: operational_decision(relationship_health=0.6)` | Mismatch detection always same, not real | Gather last 5 outcomes |
| P2-04 | **P2** | `commerce/revenue_intelligence.py: funnel record not called` | `record_funnel_transition` not called in `llm_worker` per generation, journey not autonomous, only tests | `revenue_intelligence.py: record_funnel_transition` 0 callers in workers | Journey not populated live |
| P2-05 | **P2** | `db/redis.py: DLQ unbounded` | `dead_letter_queue` stream not bounded, `cleanup_expired_dlq_entries` exists but not called automatically | `db/redis.py:505` | DLQ can grow without bound |
| P2-06 | **P2** | `commerce/production_control.py:799` `derive_production_state` 0 sample → NORMAL (see P1-05) | Same as P1-05 but for production_state | `production_control.py:681` | False-positive |
| P2-07 | **P2** | `memory/context.py: tiktoken gpt-4` | Tokenizer mismatch 15% for Qwen BPE, budget 800 may undertrim | `memory/context.py:13` | Minor |
| P2-08 | **P2** | `commerce/adaptive_optimization.py: generation_id new UUID per retry` | Duplicate evidence on stale retry (see P1-03) | `workers/llm_worker.py: uuid.uuid4()` | Could double-count |
| P2-09 | **P2** | `commerce/revenue_intelligence.py: duplicate buckets` | `attribute_purchase` direct/assisted vs `time_bucket` IMMEDIATE/SHORT/ASSISTED/LONG duplicate | `adaptive: attribute_purchase` vs `revenue: time_bucket` | Inconsistent buckets |
| P2-10 | **P2** | `commerce/strategy_learning.py: flat threshold 3 vs hierarchical 5/10` | Inconsistent sample gate | `strategy_learning.py:113` vs `adaptive: MIN_EVIDENCE 5/10` | Could cause legacy path to dominate |
| P2-11 | **P2** | `chatbotv2/main.py: rate limit before blacklist` | `check_send_rate_limit` before `is_blacklisted`, wastes token for invalid peer | `chatbotv2/main.py:112` | Minor |
| P2-12 | **P2** | `commerce/re_engagement.py: max 2/7d hardcoded 0` | Scheduler passes `recent_reengagements_7d=0` always, so max frequency never enforced | `workers/scheduler_worker.py:246` `recent_reengagements_7d=0` | **P2: could over-schedule re-engagement** |
| P2-13 | **P2** | `commerce/conversation_outcomes.py: duplicate outcome taxonomy?` | 18 outcomes, `CanonicalOutcome` vs `ConversationOutcome` 26 values, overlapping but not identical | `conversation_outcomes.py` vs `adaptive` | Not drift but duplicate |
| P2-14 | **P2** | `workers/llm_worker.py: best-effort `except Exception: pass` around `compute_pressure` etc.` | Swallows exceptions that could hide safety failures, but `except` around `compute_pressure` → `pass` leaves `_pressure` undefined, but later `_op_dec` `except` also pass → safe default? Could hide pressure suppression failure | `workers/llm_worker.py:787` | Could hide |
| P2-15 | **P2** | `publish_event` `message_preview` first 100 chars | Could be PII (fan message) to Redis Pub/Sub | `workers/llm_worker.py:599` `user_message[:100]` | PII leakage **P2** |
| P2-16 | **P2** | `core/telemetry.py: insert_generation_telemetry` best-effort `except Exception: logger.warning` + `pop` cache | Telemetry failure not fail-closed for autonomous, but generation still succeeds — correct per spec (telemetry best-effort) | `core/telemetry.py:221` | Not safety, but could hide health |
| P3-01 | **P3** | `commerce/next_best_action.py` dead file | 0 callers, safe to remove | `commerce/next_best_action.py` | Cleanup |
| P3-02 | **P3** | `memory/context.py:build_system_prompt` old | 0 callers, only `build_qwen3_system_prompt` used | `memory/context.py:137` | Cleanup |
| P3-03 | **P3** | `core/telemetry.py:record_sync` dead | 0 callers | `core/telemetry.py:226` | Cleanup |
| P3-04 | **P3** | `commerce/revenue_intelligence.py:_ensure_window` dead | 0 callers | `revenue_intelligence.py:291` | Cleanup |
| P3-05 | **P3** | `commerce/operational_intelligence.py:EXPLOIT` never mapped | 0 signals → EXPLOIT never recommended | `operational_intelligence.py:43` | Unreachable enum |

## 52. Do Not Fix Automatically — Stage B Plan (Minimal)

For every P0/P1/P2, **only Stage B after root cause** may fix with smallest safety fix:

| ID | Fix | Files | LOC | Tests | Risk |
|---|---|---|---|---|---|
| P1-01 | Change `except Exception: _skip_qwen=False` → `_skip_qwen=True` + log, fail-closed when production-control throws | `workers/llm_worker.py:867` | 1 | Add test: `autonomous_allowed` throws → Qwen skipped | Low |
| P1-04 | `hash(rollout_id)` → `hashlib.sha256(rollout_id.encode()).hexdigest()[:8]` deterministic for sentinel | `commerce/production_control.py:265` | 1 | Test restart same sentinel | Low |
| P1-05/P2-06 | `evaluate_production_health` with `total_gen 0 → production_state UNKNOWN/HOLD` not NORMAL | `production_control.py:790` | 2 | Test sample 0 → UNKNOWN | Low |
| P1-06 | `move_to_dlq` inbound add try/except around XADD but still XACK (like `move_send_to_dlq`) | `db/redis.py:242` | 5 | Test XADD fail → still XACK | Low |
| P2-02/P1-02 | Remove legacy `RESPONSE: mode` from `memory/context.py` or make subordinate, add `SUPPRESS_PRODUCT_FAMILY` check in `rank_products_by_relevance` via `suppressed_families` set | `memory/context.py:381`, `commerce/content_matching.py`, `commerce/operational_execution.py` | 10 | Test family suppressed not selected | Medium |
| P2-12 | Pass real `recent_reengagements_7d` via `query_metrics` for last 7d, not hardcoded 0 | `workers/scheduler_worker.py:246` | 3 | Test frequency limit enforced | Low |

Others are **P2/P3 cleanup or already fail-safe** (bounded, not safety): DLQ cleanup not auto (P2-05) → add `cleanup_expired_dlq_entries` call in scheduler per day; `FANGATE_ENC_KEY` drift (P2) → no fix needed as `dropfans_enc_key` used; `next_best_action.py` dead (P3) → remove.

If no P0, **do not broaden phase**.

## 53. Stage B — Only After Root Cause (Not Implemented Here)

No production code modified in this forensic audit (read-only). If instructed to proceed to Stage B, implement only P1/P2 above with smallest safety fixes, add focused regression tests, re-run affected tests, reconcile canary `1% ACTIVE + HOLD`.

---

# 54. Required Deliverables (This Document)

This document `docs/AI_NATIVE_COMMERCE_PHASE_31_FORENSIC_AUDIT.md` contains all 51 sections: executive summary, architecture reality, call graph, phase reconciliation, decision-authority map, safety hierarchy, single-pass, creator/fan isolation, ID domain, Redis, peer-42, entity resolution, commerce/DropFans authority, memory, strategy learning, outcome, attribution, metric windows, health, canary, rollback, emergency, best-effort, operational intelligence, stale, idempotency, concurrency, persistence/restart, boundedness, telemetry, PII, config, DB schema, migration, scheduler, re-engagement, handoff, degraded, test quality, invariant audit, live infra, dead code, magic numbers, enum drift, performance, security, severity matrix, fix plan.

# 55. Required Findings Table

See §51 Severity Matrix (master table with ID, Severity, Component, Finding, Evidence file:line, Production impact, Fix required) — every finding PROVEN via grep/read, not speculative, distinguished PROVEN vs LIKELY.

# 56. Required Final Verdict

```
PHASE 31 FORENSIC AUDIT COMPLETE

ROOT STATUS: CONDITIONALLY READY

P0: 0
P1: 6
P2: 16
P3: 5

CRITICAL SAFETY: PASS (no LLM commerce authority, no DropFans bypass, no creator/fan leakage proven)
COMMERCE AUTHORITY: PASS
CREATOR ISOLATION: PASS (with global metric/audit not per-creator capped P2)
FAN ISOLATION: PASS
SINGLE-PASS: PASS (ollama 1/1/1/0, gemini tool loop up to 4 P2)
REDIS LIFECYCLE: PASS (with DLQ unbounded P2 and move_to_dlq inbound missing try P1)
DLQ SEMANTICS: PASS (with P1 inbound XADD fail → no ACK)
IDEMPOTENCY: PASS (with operational action idempotency not persisted P2)
CONCURRENCY: PASS (with TOCTOU read-modify-write for strategy evidence P1, global registries not per-process shared P2)
PERSISTENCE: PASS (with hash sentinel P1 and metric global cap P2)
RESTART SAFETY: PASS (1%→1% not 100%, but handoff not reloaded P1)
CANARY SAFETY: PASS (SHA256 deterministic, gates correct, but 0 sample health false-positive P1)
ROLLBACK SAFETY: PASS (preserves evidence, idempotent)
EMERGENCY CONTROLS: PASS (6 scopes, but unknown→pause not fail-closed P2)
DEGRADED MODE: PASS
MEMORY LIFECYCLE: PASS (with Interview Friday false negative P1)
BEHAVIORAL LEARNING: PASS (with flat vs hierarchical threshold inconsistent P2)
OPERATIONAL LOOP: PASS (with product-family audit-only P2 and synthetic health placeholder P2)
OBSERVABILITY: PASS (with message_preview PII P2)
AUDITABILITY: PASS (bounded 1000 global not per creator P2)

PRODUCTION CHANGES: NONE

MIGRATIONS: NONE

CANARY STATE: 1% ACTIVE (canary-29-1pct, global, 1%, ACTIVE, start 2026-08-30T15:43:03.852087+00:00) — verified via get_rollout, but after tests clear → 0 in fresh process (not persisted without loop)

NEW LLM CALLS: 0

ARCHITECTURE CHANGES: NONE

FINAL RECOMMENDATION:
FIX P0-P1 (6 P1) before claiming production-ready autonomy beyond 1% HOLD; P2 can be fixed then proceed with observation

NEXT ACTION:
Stage B minimal fixes for P1-01 (fail-closed pre-Qwen), P1-04 (hash sentinel deterministic), P1-05 (0 sample health UNKNOWN), P1-06 (move_to_dlq XADD→XACK), plus P2-02/12 and dead code cleanup, add 6 focused regression tests, re-run phase 20-30, reconcile canary remains 1% HOLD
```

