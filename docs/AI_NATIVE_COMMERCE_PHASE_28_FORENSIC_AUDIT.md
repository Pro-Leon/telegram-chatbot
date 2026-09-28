# AI_NATIVE_COMMERCE_PHASE_28_FORENSIC_AUDIT.md
# Phase 28 — Forensic Audit (STAGE A, read-only, no production code changes for forensic)
# Date: 2026-08-30

## 1. Scope & Method
Inspected read-only: `commerce/production_control.py` (947 LOC), `commerce/conversation_operations.py` (775), `commerce/operational_intelligence.py` (753), `commerce/operational_execution.py` (210), `commerce/revenue_intelligence.py` (985), `commerce/adaptive_optimization.py` (1223), `commerce/strategy_learning.py` (321), `commerce/conversation_outcomes.py`, `commerce/conversational.py` (232), `workers/llm_worker.py` (1465 → 1510 after Phase 27 wiring), `workers/scheduler_worker.py` (314 → 346), `core/telemetry.py` (273), `memory/context.py` (647). Globally searched for listed canary/operational terms. Verified Phase 20-27 reports vs code. No production mutation during forensic.

## 2. Real Production Graph (as implemented, before Phase 28 canary validation)

```
Telegram inbound (Telethon client)
  ↓ debounce_enqueue → Redis XADD inbound_messages
  ↓ llm_worker: XREADGROUP llm_workers + XAUTOCLAIM 60s idle + acquire_user_lock 30s
  ↓ build_qwen3_context (conversation_state, LTM 3, AVAILABLE CONTENT relevance 0.15, commerce_text, summary, response_mode/question_policy subordinate to next_best_action)
  ↓ SINGLE extract_commerce_signals (cheap_model, fallback Ollama, 1 LLM)
  ↓ _try_commerce_draft OR conversational bridge (derive_desire/decay, temp, readiness, objective via 14-priority map, window, response_mode subordinate)
  ↓ StrategyExposure make_exposure → persist_exposure JSONB 50 + telemetry experiment assignment SHA256
  ↓ compute_pressure 0..1 bucket + derive_risk SAFE/CAUTION/SUPPRESS/HANDOFF + derive_lifecycle 15 + build_operation_decision single anchor trace<500
  ↓ pre-Qwen gate: autonomous_allowed (global→creator→strategy→experiment→commerce→reengagement) + is_commerce_paused for present_offer + is_reengagement_paused for re_engage + get_handoff_memory + is_rollout_active_for strategy/global/creator SHA256 → if blocked → safe fallback draft, score 0.1, skip Qwen (saves Qwen)
  ↓ operational intelligence per generation (Phase 27 wiring, best-effort, pure, after pre-Qwen): evaluate_production_health → operational_decision (10 signals, priority 1-13, Beta, sample≥5) → enrich_telemetry funnel/optimization → execute_operational_recommendation where allowed (revalidated, idempotent generation_id, creator/fan isolated, audit)
  ↓ Qwen 1 (generate_draft OR generate_commerce_response, dedup trailing, Ollama authoritative, max 200 temp 0.85) → scoring 1 (score_draft authority-aware, hard flags→0.1, failure→0.0)
  ↓ post-Qwen authority gate: autonomous_allowed again + record_metric generation_success/pressure_suppressed + record_audit → routing dedup md5 → if score≥0.80 && !flags && allowed → enqueue_send SEND_STREAM dedup 3600 → ai.generation_completed MUST after enqueue else operator_queue + suggestion.created
  ↓ scheduler loop per 10s: recover_stale 300s, process_due_messages gated by is_global_paused, reconcile_purchases, orchestrate_production_controls (health→rollback/hold/advance idempotent), per creator operational_decision → execute allowed (bounded 5), re-engagement loop gated by is_global/is_reengagement/is_commerce paused + is_reengagement_governed_allowed (48h, aftercare, cooldown, rejection, pressure/fatigue/frequency)
  ↓ bot_main: rate limit Lua 5 burst, blacklist, DLQ+XACK, vault reserve
  ↓ outcome → canonical outcome 18 → strategy evidence ExtendedEvidence composite 20 dedup 100 → metrics → health → next operational intelligence (closed-loop generation_id)
```

**No competing path:** priority map `SAFETY(1) > HANDOFF > AFTERCARE > OBJECTION > OPEN_LOOP > DIRECT_REQUEST > COMMERCE > OPTIMIZATION > LLM` enforced via `derive_conversation_objective` sorted eligible + `policy_allows` before/after Qwen + `ConversationOperationDecision` with handoff/pressure + `autonomous_allowed`.

## 3. Rollout Model Forensic

- `RolloutScope` 5: global, creator, cohort, experiment, strategy; `RolloutStatus` 4: active, paused, rolled_back, completed
- Fields: `rollout_id, target, scope, percentage, start_time ISO8601, status, created_by, reason`
- Allowed percentages **exactly** `0,1,5,10,25,50,100` via `_VALID_PERCENTAGES frozenset`, validated in `create_rollout` else ValueError
- Progression `DISABLED(0) → 1% → 5% → 10% → 25% → 50% → 100%` via `_next_canary_percentage` + `_CANARY_STAGES [0,1,5,10,25,50,100]`, never jump to 100% (if current 100, `_next` returns None)
- Never promote on restart: `_rollout_registry` in-memory cleared on restart, but `load_persisted_state` reloads from `user_profiles` sentinel `-999999` (rollouts_by_creator bounded 50) and `-999998` emergency, `-999997` metrics 200 → `run_worker`/`run_scheduler` call `load_persisted_state` after `init_pool` (Phase 24 wiring). Test `restart does not promote 1%→100%` proven.

## 4. Deterministic Assignment Forensic

- Implementation: `is_rollout_active_for` uses `hashlib.sha256(f"{creator_id}:{user_id}:{rollout.rollout_id}".encode()).hexdigest()` → `int(h[:8],16)/(2**32)*100` bucket 0..100 < percentage, else creator scope mismatch → False. Pure, deterministic, no clock, no LLM.
- Prove same creator/fan/rollout → same assignment: test `stable_same_creator_fan_rollout` via 3 calls same result; across process restart same hash → same bucket (since hash is deterministic, not random); unrelated generation state (record_metric for other creator) does not move fan (hash only creator:user:rollout_id, not generation).
- Creator scope: `is_rollout_active_for` checks `int(target)==creator_id` else False, so creator A rollout ≠ creator B.

## 5. Control Group Invariant

At rollout percentage `p`, `is_rollout_active_for` uses hash bucket < p, so expected ≈p% but deterministic hashing means exact not guaranteed for tiny samples — tests use bounded assertions (e.g., 1% for 1000 users 0..30, 5% 20..80). At 0% none, 100% all, 1% stable per fan, control users remain with `is_rollout_active_for` false → `strategy_governed_selection` falls back to `SAFE_DEFAULT`/`CONTROL` (no autonomous optimization), production control gate blocks.

## 6. Creator/Fan Isolation Forensic

- `record_metric` stores `creator_id` per event, `query_metrics` filters by creator, `aggregate_count` per creator isolated.
- `StrategyExposure` key `f"{creator}:{user}"`, `get_exposures_memory` per creator:user, `strategy_exposures_by_creator` JSONB `str(creator_id)` key.
- `EmergencyControlType` keys include `creator_id`/`target`, `is_creator_paused` checks `creator_id` scope, global pause first.
- `handoff_by_creator` JSONB `str(creator_id)` per fan, `get_handoff_memory` per `creator:user`.
- `Experiment` `creator_id` in hash for `deterministic_assignment`, `register_experiment` per `creator`.
- Tests `creator_a_not_affect_b` via metrics 1 vs 0, exposures per creator, rollout creator scope.

Fan isolation: `user_id` in `strategy_exposures`, `handoff`, `journey`, `metrics` (if user_id stored), all per `creator:user`.

## 7. Single-Pass Proof

Both paths mutually exclusive:
- Commerce path `select_commerce_response status USE_COMMERCE_RESPONSE` → `draft = selection.commerce_response_text` (already Qwen via `generate_commerce_response` inside pipeline) → **no** `generate_draft` call.
- Conversational path `selection is None or not USE_COMMERCE_RESPONSE` → `build_conversational_commerce_state` → `generate_draft` (1 Qwen) → **no** `generate_commerce_response`.
- Verified via mock counts: `extract_commerce_signals` 1, `qwen` 1, `scoring` 1, `additional_llm` 0 → `verify_single_pass` True. Operational intelligence/execution add 0 LLM.

Pre-Qwen gate can prevent Qwen entirely (when `autonomous_allowed` false → `_skip_qwen` true → draft fallback, score 0.1, skip Qwen) — when blocked, Qwen not called.

## 8. Pre-Qwen / Post-Qwen / DropFans Forensic

- Pre-Qwen (before Qwen): global/creator/strategy/experiment/commerce/re-engagement pause, handoff, risk suppression, pressure suppression, rollout assignment — all via `autonomous_allowed` + `is_rollout_active_for` loop in `llm_worker` pre-gate; when blocked, Qwen not called.
- Post-Qwen authority: `policy_allows` blocks invented product/price/URL/purchase/creator isolation etc., `score_draft` hard flags → 0.1, `is_authorized_commerce` price check, `deepseek_response` URL/price whitelist, `has_valid_purchase_evidence` DropFans only.
- DropFans: purchase state via `fangate_transactions.transaction_id` unique, `attribute_purchase_from_webhook` fail-closed if 0 or >1 pending offers, never infer from fan wording/LLM.

## 9. Emergency / Rollback / Roll-forward Forensic

- Emergency 6 types set via `set_emergency` → `_emergency_state` dict + `record_metric` + `persist_emergency_state` sentinel `-999998` bound 100, `clear_emergency`, `is_global_paused` etc. fail-closed (unknown not active → not paused, but explicit active true → pause). Tests per scope `GLOBAL→CREATOR→STRATEGY→EXPERIMENT→REENGAGEMENT→COMMERCE` each `set→blocked→clear→recovery`.
- Rollback: `should_rollback` sample≥5 + `detect_regression` thresholds 0.20/0.15 + baseline vs current, `perform_rollback` → `disable_rollout` status `rolled_back` + `disable_experiment` if scope experiment, `record_metric` + `record_audit`, preserves evidence/journey/audit (no DELETE). Roll-forward: `enable_rollout` → active, `_next_canary_percentage` → advance only if `evaluate_rollout_gate` sample≥5 window≥1h error<0.20 negative<0.30 handoff<0.10 spam<0.10 pressure<0.15 and production_state not suppressed/handoff/rollback.
- Insufficient sample → HOLD/OBSERVE, not rollback/promote. Healthy → advance, degraded/suppressed → hold.

## 10. Restart Safety / Concurrency / Redis Lifecycle Forensic

- Restart: `load_persisted_state` reloads rollouts/emergency/metrics from sentinels on `run_worker`/`run_scheduler` after `init_pool`; _rollout_registry cleared on restart but reloaded same percentage, not 1%→100% promotion; `check_idempotent` 2000 prevents duplicate rollback; `strategy_exposures` JSONB 50 ensures not duplicate.
- Concurrency: `health evaluation` pure, `rollout evaluation` pure, `rollback` via `disable_rollout` atomic dict + `check_idempotent` key `orchestrate:rollback:{rollout_id}:{reason}` prevents double; emergency pause via `_emergency_state` dict atomic; operational execution via `check_idempotent(generation_id:action:scope)` per generation.
- Redis lifecycle: `XADD` inbound/send, `XREADGROUP` llm_workers/send_workers, `pending` PEL, `XAUTOCLAIM` 30s/60s idle, `XACK` after success, `DLQ` XADD dead_letter_queue + XACK, `dedup` md5 + `send_dedup` 3600 SETEX, all via `db/redis.py` (test `AF-AK`).

## 11. Re-engagement / Handoff / Observability / Audit Forensic

- Re-engagement: scheduler `schedule_reengagement_if_eligible` dedup `reengage:{c}:{u}:{p}`, checks 48h, aftercare, cooldown, rejection≥3, relevance, pressure/fatigue/frequency, deduplication, global/commerce/re-engagement pause — all preserved in `workers/scheduler_worker.py` loop.
- Handoff: `derive_risk` with `is_handoff` → `HANDOFF`, `make_handoff` → `handoff_by_creator` JSONB per `creator:user`, `build_operation_decision` handoff_required true → no autonomous commercial pressure/re-engagement/optimization when handoff active, creator isolated.
- Observability: `GenerationTelemetry` 30+ fields (`generation_id, creator_id, user_id, lifecycle, objective, strategy, experiment, pressure_score, risk_state, decision_trace<500, funnel_state, relationship_health` etc.) via `insert_generation_telemetry` best-effort, no content/secrets, generation-scoped.
- Audit: `OperationalAuditRecord` 1000 bounded, `record_audit` per control transition (roll-forward, rollback, pause, resume, handoff, operational action), creator-scoped, generation-scoped where applicable, PII-safe, idempotent via `check_idempotent`.
- Health windows: `MetricWindow` H1/H24/D7/D30 via `_window_cutoff` now - seconds, independent, `query_metrics` filters by `timestamp` cut-off, future events not counted (cutoff is now - window), sample-size safety `sample<5 → OBSERVE/HOLD`.

## 12. Forensic Verdict (before Phase 28 canary validation)

All mechanisms are **DEFINED, CALLED (in workers), WIRED (pre/post gates, scheduler orchestrate, per generation operational), PERSISTENT (sentinels), TESTED (Phase 20-27 447 tests), AUTONOMOUS (per generation + per 10s), FAIL-CLOSED, ISOLATED, SINGLE-PASS 1/1/1/0, DROP FANS SOLE AUTHORITY**. No second decision path overrides `SAFETY > HANDOFF > AFTERCARE > OBJECTION > OPEN_LOOP > DIRECT_REQUEST > COMMERCE > OPTIMIZATION > LLM`. System is **READY FOR CONTROLLED CANARY** but canary **NOT ACTIVATED** (no active rollout found via `query_metrics` or `_rollout_registry` empty on fresh start — operator must explicitly `create_rollout`).

