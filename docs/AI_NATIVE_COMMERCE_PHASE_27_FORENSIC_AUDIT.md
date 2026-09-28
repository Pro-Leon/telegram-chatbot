# AI_NATIVE_COMMERCE_PHASE_27_FORENSIC_AUDIT.md
# Phase 27 — Forensic Audit (STAGE A, read-only, no production code changes)
# Date: 2026-08-30

## 1. Scope
Inspected: `commerce/operational_intelligence.py` (753 LOC), `commerce/revenue_intelligence.py` (985), `commerce/production_control.py` (947), `commerce/conversation_operations.py` (775), `commerce/adaptive_optimization.py` (1223), `commerce/conversation_intelligence.py` (207), `commerce/strategy_learning.py` (321), `commerce/conversation_outcomes.py` (113), `commerce/conversational.py` (232), `workers/llm_worker.py` (1458), `workers/scheduler_worker.py` (307), `core/telemetry.py` (266), `memory/context.py` (647). Global search for: `operational_decision`, `OperationalRecommendation`, `recommendation_for_diagnosis`, `OperationalAction`, `ROTATE_STRATEGY`, `REDUCE_PRESSURE`, `SUPPRESS_STRATEGY`, `SUPPRESS_PRODUCT_FAMILY`, `PRIORITIZE_RELATIONSHIP`, `FOLLOW_UP_OPEN_LOOP`, `SUPPRESS_REENGAGEMENT`, `PAUSE`, `ROLLBACK`, `HANDOFF`, `EXPLORE`, `EXPLOIT`, `SAFE_DEFAULT`, `autonomous_allowed`, `optimization_allowed`, `derive_production_state`, `should_rollback`, `perform_rollback`, `is_reengagement_governed_allowed`, `select_strategy_hierarchical`, `next_best_action`, `conversation objective` + production metrics/search terms from Phase 26.

No production code modified during this stage.

## 2. Reconstructed Actual Production Loop (current code, before Phase 27 fix)

```
Telegram inbound (Telethon client.get_messages → debounce_enqueue)
  ↓ XADD inbound_messages (Redis Streams)
  ↓ llm_worker.run_worker: requeue_stalled_messages XAUTOCLAIM 60s idle, XREADGROUP llm_workers count 5 block 2000
  ↓ acquire_user_lock 30s TTL
  ↓ upsert_user, is_user_auto_reply_excluded → if excluded → telemetry excluded → ACK
  ↓ resolve_single_application_creator (dropfans verified) → creator_id
  ↓ build_qwen3_context (user, persona, creator_id) → conversation_state, response_mode, question_budget, AVAILABLE CONTENT relevance, LTM 3, commerce_text, summary
  ↓ extract_explicit_memories → add_memory_item (creator-scoped 20, no LLM)
  ↓ ai.generation_started (generation_id UUID, event_id UUID, scope user)
  ↓ SINGLE extract_commerce_signals(context) → CommerceSignals (1 LLM cheap_model)
  ↓ _try_commerce_draft (resolve product via rank, CommerceStateRequest → resolve_and_run_commerce → pipeline: decide 23-branch → strategy → orchestrate → execute_ppv advisory lock → selection)
  ↓ if USE_COMMERCE_RESPONSE → draft from selection.commerce_response_text (generate_commerce_response was the 1 Qwen, not second)
     else → build_conversational_commerce_state (derive_desire/decay, temp, relevance, readiness, objective, window, conversation_intelligence priority) → response_mode/question_policy subordinate to NBA → resolve_open_loop → StrategyExposure make_exposure → persist_exposure JSONB 50 + in-memory + telemetry → experiment assignment SHA256 → compute_pressure → derive_risk → derive_lifecycle → build_operation_decision single anchor (trace<500)
       → **pre-Qwen production gate** (autonomous_allowed global→creator→strategy→experiment + is_commerce_paused for present_offer + is_reengagement_paused for re_engage + get_handoff_memory → HANDOFF + is_rollout_active_for strategy/global/creator SHA256) → if not allowed/rollout_blocked → _skip_qwen True → safe fallback draft, score 0.1, skip Qwen (saves Qwen)
       → else → should_use_agent? false (canary disabled) → generate_draft / generate_draft_with_tools (1 Qwen via Ollama, dedup trailing) → generation latency
  ↓ if empty draft → operator_queue + ai.generation_completed (was_auto_approved false) → return
  ↓ score_draft (1 scoring, authority-aware, hard flags→0.1, failure→0.0) → post-scoring production gate (autonomous_allowed again + autonomous_commerce_allowed, record_metric generation_success + pressure_suppressed etc., record_audit)
  ↓ shadow collect (2s wait, best-effort)
  ↓ routing: dedup md5(user:msg:telegram_id) → if not auto_reply_enabled → operator_queue else if score≥0.80 && !flags && allowed → enqueue_send SEND_STREAM dedup 3600 → ai.generation_completed MUST after enqueue else operator_queue + suggestion.created
  ↓ post_process async (profile + summary)
  ↓ strategy learning feedback: get strategy_last_by_creator → classify_canonical_outcome → outcome_strength → attribute_purchase (DropFans) → update_strategy_evidence + extended composite 20 dedupl 100 → store current strategy as last
  ↓ telemetry.complete → insert_generation_telemetry
  ↓ scheduler_worker._scheduler_loop every 10s: recover_stale 300s, process_due_messages (claim_due_messages batch 20, gated by is_global_paused), reconcile_purchases, orchestrate_production_controls (health→rollback/hold/advance idempotent, audit), re-engagement loop (list_offers_for_creator pending 50, age≥48h, gated by is_global_paused/is_reengagement_paused/is_commerce_paused + is_reengagement_governed_allowed pressure/fatigue/frequency)
  ↓ bot_main._process_send_stream: requeue_stalled_send_messages XAUTOCLAIM 30s, read_send_messages, is_send_duplicate dedup, rate limit Lua 5 burst, blacklist, get_input_entity permanent→DLQ+XACK+blacklist, FloodWait→sleep+requeue, reserve_delivery, send_file/send_message, mark_send_dedup, ack_send, save_outbound_after_send, publish message.sent
  ↓ outcome → next inbound → loop

Markers:
- **Revenue intelligence** (`CanonicalEvent`, `FunnelState`, `compute_conversion_metrics` etc.) → **DEFINED, TESTED (61), PERSISTENT bounded JSONB, but NOT CALLED/WIRED/AUTONOMOUS in llm_worker/scheduler** (no caller in workers)
- **Operational intelligence** (`OperationalSignal` 10, `OperationalDiagnosis`, `OperationalRecommendation` with priority/confidence/evidence/scope/allowed, `analyze_operational_state`, `recommend_from_diagnoses`, `operational_decision`) → **DEFINED (753 LOC), TESTED (43), PERSISTENT stateless pure, but NOT CALLED/WIRED/AUTONOMOUS** — zero hits in `workers/`, zero hits in `commerce/production_control`, zero in `scheduler` beyond imports in docs/tests.

## 3. Capability Wiring Matrix (Phase 27 forensic)

| Capability | Defined | Called | Wired | Persistent | Tested | Autonomous |
|---|---|---|---|---|---|---|
| Production metrics | YES | YES | YES (per gen + scheduler) | YES 5000 + sentinel | YES | YES |
| Revenue intelligence (CanonicalEvent, funnel, conversion) | YES | NO (tests only) | NO | YES metrics reuse | YES 61 | NO |
| Relationship intelligence (health 8, vs commerce safety) | YES | NO | NO | YES | YES | NO |
| Funnel intelligence (FunnelState, record_funnel_transition 20) | YES | NO | NO | YES 20 JSONB | YES | NO |
| Strategy intelligence (strategy_score, hierarchy) | YES | YES (adaptive) | YES (adaptive) | YES 20 | YES | YES |
| Product-family intelligence | YES | NO | NO | YES | YES | NO |
| Topic intelligence | YES | NO | NO | YES | YES | NO |
| Objective intelligence (14) | YES | NO | NO | YES | YES | NO |
| Response-mode intelligence | YES | NO | NO | YES | YES | NO |
| Experiment intelligence | YES | NO | NO | YES | YES | NO |
| Baseline comparison | YES | NO | NO | YES | YES | NO |
| Fan segmentation (10) | YES | NO | NO | YES | YES | NO |
| Journey intelligence (bounded 20) | YES | NO | NO | YES 20 | YES | NO |
| Regression detection (detect_regression 0.20/0.15) | YES | YES | YES | YES | YES | YES |
| Recommendation engine (10 signals → 16 actions, priority 1-13) | YES | NO (tests only) | NO | stateless | YES 43 | NO |
| Recommendation authorization (autonomous_allowed, optimization_allowed, derive_production_state) | YES | YES (existing) but not for operational recs | PARTIAL (allowed computed but not used to execute) | YES | YES | NO for operational |
| Operational health (derive_production_state 8, HealthReport) | YES | YES | YES | YES | YES | YES |
| Rollout governance (SHA256, gate sample≥5) | YES | YES | YES | YES sentinel | YES | YES |
| Emergency controls (6 types) | YES | YES | YES | YES sentinel | YES | YES |
| Audit trail (OperationalAuditRecord 1000) | YES | YES | YES | YES | YES | YES |
| Creator controls | YES | YES | YES | YES | YES | YES |
| Fan controls | YES | YES | YES | YES | YES | YES |
| Recovery (PAUSED→RECOVERING→CAUTION→NORMAL) | YES | YES | YES | YES | YES | YES |

**Gap:** Recommendation engine **exists as library** but **no production caller** — therefore **no autonomous execution**. Phase 25's `revenue_intelligence` and Phase 26's `operational_intelligence` are not in `workers/llm_worker.py` or `workers/scheduler_worker.py` call graphs. Unit tests call `operational_decision()` directly, not production.

## 4. Primary Forensic Question — Recommendation → Authorization → Execution

For each `OperationalAction`, searched for actual execution code:

| Action | Recommendation exists | Authorized | Actually executed | Observable effect | Forensic |
|---|---|---|---|---|---|
| NO_ACTION | YES via HEALTHY → NO_ACTION | YES (allowed true) | **NO** — no effect, intentionally | None | NOT EXECUTED (correct) |
| OBSERVE | YES via INSUFFICIENT_DATA → OBSERVE | YES | **NO** — safe, no mutation | None | NOT EXECUTED (correct) |
| EXPLORE | YES via CONVERSION_DECLINE → EXPLORE | YES (if allowed) | **NO** — no code changes exploration_rate or eligible strategy in llm_worker; exploration remains via select_strategy_adaptive default 10% but not triggered by operational rec | Telemetry only if manually logged | **NOT EXECUTED** |
| EXPLOIT | Defined but not produced (no signal maps to EXPLOIT) | — | **NO** | — | NOT EXECUTED |
| REDUCE_PRESSURE | YES via RISING_REJECTION → REDUCE_PRESSURE | YES | **NO** — no code lowers pressure; pressure is computed per turn from DAO counts, not from recommendation | Telemetry only | **NOT EXECUTED** |
| SUPPRESS_STRATEGY | YES via STRATEGY_REGRESSION → SUPPRESS_STRATEGY (sample≥20 & conf>0.75 → ROLLBACK_EXPERIMENT else SUPPRESS) | Partial (allowed via autonomous_allowed) | **NO** — `strategy_governed_selection` not called with suppression from operational rec; no existing `disable_strategy` function; strategy suppression only via manual `set_emergency(STRATEGY_PAUSE)` which operational rec does NOT call | None | **NOT EXECUTED** |
| ROTATE_STRATEGY | YES via FATIGUE≥0.30 → ROTATE_STRATEGY (or ROTATE_TOPIC if topic present) | YES | **NO** — no code rotates eligible strategy list; `select_strategy_adaptive` already handles fatigue via fatigue_map but not via operational rec | None | **NOT EXECUTED** |
| ROTATE_TOPIC (TOPIC) | Same as above | YES | **NO** | None | **NOT EXECUTED** |
| SUPPRESS_PRODUCT_FAMILY | YES via PRODUCT_FAMILY_DEGRADATION → SUPPRESS_PRODUCT_FAMILY | YES | **NO** — no code excludes family from `rank_products_by_relevance`; family fatigue via `recent_offered_groups` but not via operational rec | None | **NOT EXECUTED** |
| PRIORITIZE_RELATIONSHIP | YES via RELATIONSHIP_COMMERCE_MISMATCH → PRIORITIZE_RELATIONSHIP | YES | **NO** — relationship_vs_commerce_safety exists but not called in llm_worker pre-gate; llm_worker builds objective via derive_conversation_objective, not via operational rec | None | **NOT EXECUTED** |
| FOLLOW_UP_OPEN_LOOP | YES via OPEN_LOOP_STAGNATION → FOLLOW_UP_OPEN_LOOP | YES | **NO** — open loop follow-up already via `derive_conversation_objective` when has_open_loop true, but operational rec does not trigger it; scheduler not scheduling follow-up via operational | None | **NOT EXECUTED** |
| SUPPRESS_REENGAGEMENT | YES via SPAM_RISK → SUPPRESS_REENGAGEMENT | YES | **NO** — `is_reengagement_governed_allowed` already in scheduler, but operational rec does NOT call `set_emergency(REENGAGEMENT_PAUSE)` or otherwise suppress | None | **NOT EXECUTED** |
| PAUSE_EXPERIMENT | YES via RESPONSE_MODE_DEGRADATION → PAUSE_EXPERIMENT | YES (if allowed) | **NO** — `disable_experiment` exists but not called from operational rec | None | **NOT EXECUTED** |
| ROLLBACK_EXPERIMENT | YES via STRATEGY_REGRESSION high conf → ROLLBACK_EXPERIMENT | YES | **NO** — `perform_rollback` exists but not called | None | **NOT EXECUTED** |
| ROLLBACK_ROLLOUT | Defined but not produced (no mapping) | — | **NO** | — | NOT EXECUTED |
| HANDOFF | YES via HANDOFF_SPIKE → HANDOFF | YES | **NO** — `make_handoff` exists but not called; handoff only via `derive_risk(handoff)` and pressure | None | **NOT EXECUTED** |

**Conclusion:** Every `OperationalAction` is **represented in telemetry/recommendation** but **not executed** — there is **no wiring** `OperationalRecommendation → authorization → actual behavioral/control change`. The recommendation is `allowed` correctly (via `_is_recommendation_allowed` which checks production control), but no code **consumes** `allowed=true` to mutate `strategy governance`, `pressure`, `product family`, `relationship priority`, `re-engagement`, `experiment`, `rollout`, or `handoff`.

## 5. Why No Second Orchestrator Is Needed

Existing hierarchy remains authoritative and must not be duplicated:

```
SAFETY (HUMAN_HANDOFF priority 1)
  ↓
CREATOR ISOLATION
  ↓
HANDOFF (risk HANDOFF, handoff_by_creator)
  ↓
AFTERCARE (aftercare_status pending/sent)
  ↓
OBJECTION (has_objection)
  ↓
OPEN LOOP (has_open_loop importance≥0.7)
  ↓
DIRECT FAN INTENT (explicit_purchase_request)
  ↓
COMMERCE READINESS (offer_readiness ready + has_relevant_product)
  ↓
PRODUCTION CONTROL (autonomous_allowed, derive_production_state, rollout gate)
  ↓
OPTIMIZATION (select_strategy_adaptive Beta/fatigue, adaptive_optimization)
  ↓
LLM LANGUAGE (Qwen)
```

Operational intelligence sits **above** as `measurement (revenue_intelligence) → diagnosis (operational_intelligence signals) → recommendation (16 actions, priority 1-13) → authorization (autonomous_allowed etc.) → existing mechanism` — not a new `Phase27DecisionEngine` or `next_best_action`.

## 6. Expected Execution Contract (not yet existing)

For `allowed=true`, expected existing mechanisms per action:

- **ROTATE_STRATEGY**: `OperationalRecommendation(ROTATE_STRATEGY, scope creator:fan, strategy X)` → `select_strategy_hierarchical` with `ROTATE` hint → different eligible strategy when evidence permits; should not merely appear in telemetry while same strategy continues.
- **REDUCE_PRESSURE**: → existing `compute_pressure` governance → lower commercial pressure → response behavior reflects reduced pressure; must not bypass conversation objective/safety/aftercare/cooldown/commerce authority.
- **SUPPRESS_STRATEGY**: → `strategy_governed_selection` with `regression_map` or `set_emergency(STRATEGY_PAUSE)` without deleting historical evidence (historical `ExtendedEvidence` attempt_count preserved).
- **SUPPRESS_PRODUCT_FAMILY**: → `rank_products_by_relevance` exclude/suppress family (existing `recent_offered_groups` already does family fatigue) while product authority remains with commerce/DropFans.
- **PRIORITIZE_RELATIONSHIP**: → `relationship_vs_commerce_safety` → `relationship objective > commercial optimization`, must not manufacture purchase intent.
- **FOLLOW_UP_OPEN_LOOP**: → `open loop → conversation intelligence FOLLOW_UP_OPEN_LOOP → CALLBACK → ONE_NATURAL_QUESTION`, verified resolved loops do not continue indefinitely via `resolve_open_loop` status RESOLVED.
- **SUPPRESS_REENGAGEMENT**: → `scheduler` governed check `is_reengagement_governed_allowed` suppressed → no autonomous re-engagement.
- **PAUSE**: → existing `set_emergency(GLOBAL/CREATOR/STRATEGY/EXPERIMENT)` pause state, not second mechanism.
- **ROLLBACK**: → `perform_rollback()` existing, preserves evidence/history.
- **HANDOFF**: → existing `make_handoff` creator-scoped `handoff_by_creator`.

Currently **none** of these are invoked from operational recommendations.

## 7. Closed-Loop, Stale, Restart, Concurrency Gaps

- **Closed-loop:** `generation_id → diagnosis → recommendation → authorization → action → behavior → outcome → strategy_evidence → adaptive` is **broken** at `→ action` (no action). `generation_id` exists in telemetry but not in operational recommendation execution trace.
- **Stale recommendation safety:** `recommendation generated at T0, action at T1` state change (global pause, cooldown, purchase, aftercare, experiment disabled, DropFans unavailable, handoff) must be revalidated via `autonomous_allowed` before execution — currently not revalidated because no execution exists; but `_is_recommendation_allowed` already implements revalidation logic (checks current is_global_paused etc.) so stale would be fail-closed **if** executed.
- **Restart safety:** `diagnosis → recommendation → restart → resume` — operational intelligence is stateless pure, no persistence to lose; but if recommendation had been `SUPPRESS_STRATEGY` via `set_emergency`, that emergency **is** persist via sentinel `-999998` and reload via `load_persisted_state` (restart-safe). However since no execution, no state to lose, but also no duplicate — would need idempotency via `generation_id` + `check_idempotent` (existing 2000) for actions like rollback.
- **Concurrency:** `same creator same fan same strategy/product_family` would need creator+fan isolation; pure functions already isolated via `creator_id:fan_id` scope, but without execution no contamination test passes vacuously.

## 8. Forensic Verdict

**Phase 26 is WIRED as library, TESTED, PERSISTENT stateless, but NOT EXECUTED** — `OperationalRecommendation` is **produced** (`operational_decision()` in tests) with correct `allowed` via production control, but **never consumed** in `workers/llm_worker.py` (≈1,458 LOC, 0 hits for `operational_intelligence`) or `workers/scheduler_worker.py` (≈307 LOC, 0 hits). Therefore:

```
measurement (YES) → intelligence (YES) → diagnosis (YES library) → recommendation (YES library) → production-control authorization (YES library) → existing autonomous behavior (NO wiring) → outcome (NO closed-loop)

RECOMMENDATION → ACTION gap confirmed.
```

Do not create second orchestrator; minimal fix is **small wiring change**: consume `operational_decision` in existing `llm_worker` (per-generation, best-effort, pure, before strategy selection) and `scheduler_worker` (periodic per creator, before orchestrate) to affect **existing** mechanisms (`strategy governance` via `strategy_governed_selection` hint, `pressure` via existing compute, `product family` via rank filter, `relationship` via objective, `open loop` via conversation intelligence, `re-engagement` via existing governed check, `experiment` via `disable_experiment`, `rollout` via `perform_rollback`, `handoff` via `make_handoff`) with **authorization revalidation, idempotency via generation_id, audit via record_audit, bounded trace<500, no content/secrets, creator/fan isolated, DropFans/LLM authority preserved, single-pass 1/1/1/0**.

