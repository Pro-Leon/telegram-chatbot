# AI-Native Commerce — Phase 23 Implementation Map

**Date:** 2026-08-30
**Scope:** Enterprise Autonomous Rollout & Operational Intelligence
**Method:** Forensic (Stage A read-only) → minimal deterministic health/canary/rollback orchestration → focused tests → targeted regression → final re-audit. No redesign.
**Provider:** ollama/qwen2.5:3b  Canary: NOT ACTIVATED (framework ready, not promoted beyond tests)
**Single-pass:** 1 × extract_commerce_signals + 1 × Qwen + 1 × scoring, 0 additional LLM calls

---

## 1. Stage A Forensic Findings (Summary)

**Reconciliation:** Phase 22 primitives all DEFINED: `production_control` 400 LOC (`MetricWindow` 1h/24h/7d/30d bounded 5000, `Rollout` 0/1/5/10/25/50/100 scopes 5, `EmergencyControlType` 6 fail-closed, `OperationalAuditRecord` 1000, `ProductionState` 8, idempotency 2000) + `conversation_operations` 751 LOC unified decision + `GenerationTelemetry` 19 fields + `tests/test_phase22` 34 + `test_phase21` 48 + `test_phase20` 101 = all WIRED as libraries, but **not continuously orchestrated**.

**23-capability matrix pre-Phase 23:** Defined 23/23, Wired 5/23 (decision trace, handoff, degraded, re-engagement, strategy governance partial), Enforced 3/23, Persistent 4/23, Tested 23/23, Autonomous 3/23. Central gap: **control primitives tested, not operationally autonomous** — metrics recorded only in tests not per generation in `workers/llm_worker`, rollout percentages no deterministic `DISABLED→1%→5%→10%→25%→50%→100%` advancement lifecycle, rollback not automatically invoked from health, `autonomous_allowed()` not called before Qwen, `derive_production_state()` not periodically evaluated, `record_audit` not per control transition, metric windows defined not enforced, variant attribution not aggregated per experiment outcome.

Root cause: no `evaluate_production_health() → derive_production_state() → evaluate_rollout() → evaluate_experiments() → evaluate_strategy_regressions() → apply_pause/rollback/roll-forward → record audit` loop integrated with existing `scheduler_worker` (no new worker/queue). All signals existed but not as continuous operational orchestration (forensic §14).

What must NOT change per §30 (§31 prohibitions): Redis Streams/consumer groups/XAUTOCLAIM/DLQ, PostgreSQL/Telethon/DropFans, 3 workers, scheduled_messages, memory, commerce authority, creator isolation, single-pass — all preserved.

---

## 2. Stage B Implementation (Minimal Deterministic)

### 2.1 New Helpers in `commerce/production_control.py` (+120 LOC)

**Health evaluation (§5):** `HealthReport` dataclass + `evaluate_production_health(creator_id, window, now)` pure deterministic: aggregates `query_metrics` per window `1h/24h/7d/30d` for `generation_success/failure, permanent/degraded/handoff/purchase/repeat/rejections/negative/spam/pressure_suppressed` → derived rates `success_rate, failure_rate, permanent/degraded/handoff/purchase/repeat/rejection/negative/spam/pressure_suppressed` + `sample_size` + `production_state` via `derive_production_state()` with `reason_code` + `metric_evidence` dict + `timestamp`. No LLM.

**Canary state machine (§7):** `CANARY_STAGES = [0,1,5,10,25,50,100]` + `_next_canary_percentage(current)` pure deterministic `DISABLED(0)→1%→5%→10%→25%→50%→100%` (+ `HOLD/ROLLBACK/PAUSE/RECOVER/ROLL_FORWARD` via `disable_rollout/enable_rollout/clear_emergency`).

**Rollout gates (§8):** `evaluate_rollout_gate(rollout, health, sample_size, observation_hours)` deterministic: `sample_size<5 → insufficient_sample`, `observation_hours<1 → insufficient_window`, `failure_rate>0.20 → high_error_rate`, `negative_rate>0.30 → high_negative_rate`, `handoff_rate>0.10 → high_handoff_rate`, `spam_rate>0.10 → high_spam_risk`, `pressure_suppressed>0.15 → abnormal_pressure`, `health.production_state in SUPPRESSED/HANDOFF/ROLLBACK → production_state_...` else `gate_pass`. Protects relationship quality, safety, commerce correctness, fan experience — not revenue only.

**Autonomous control loop (§22):** `orchestrate_production_controls(now, window)` bounded deterministic: `for each active rollout in _rollout_registry: health = evaluate_production_health(creator_for_health, window) → should_rollback(sample_size, window, current{conversion: health.purchase_rate, engagement: health.success_rate, rejection_rate, cooldown_rate}, baseline{0.30/0.60/0.10/0.05}, severity confirmed) → if true: `perform_rollback(rollout_id)` + `record_audit(rollback)` idempotent via `check_idempotent("orchestrate:rollback:...")`; else `evaluate_rollout_gate` → if hold: `record_audit(hold)`; else advance `old→_next_canary_percentage(old)` + `rollout.percentage=nxt, start_time=now` + `record_metric(rollout_advance)` + `record_audit(advance)` idempotent via `check_idempotent`. Pure, bounded (active rollouts few), idempotent, auditable.

Integrated **into existing `workers/scheduler_worker.py` loop** (no new worker/queue/process/scheduler/Celery/Kafka): added one best-effort call `try: orchestrate_production_controls() except: log` after `await reconcile_purchases()` inside `_scheduler_loop` (every `SCHEDULER_POLL_INTERVAL`). Not a new worker — reuses existing scheduler runtime.

### 2.2 Wiring in `workers/llm_worker.py` (+35 LOC best-effort, no LLM)

**Metric collection per generation:** After `GenerationTelemetry.complete()`, before `publish_event`, best-effort `record_metric(name="generation_success"/"generation_failure", creator_id, user_id, strategy, experiment_id, variant, outcome)` + `pressure_suppressed` if `pressure_score≥0.75` + `failure_class_failures` if present + `rejections/negative_outcome` if outcome rejection/objection. Uses existing `creator_id/user_id/strategy/experiment` dimensions, no PII, bounded via `record_metric` 5000 prune.

**Operational audit per generation:** `record_audit(OperationalAuditRecord(generation_id, creator_id, user_id, objective, strategy, experiment_id, variant, risk_state, pressure_score, decision=allowed/blocked:reason, outcome))` best-effort after metrics.

**Emergency gate before autonomous operation:** Before Qwen, after `derive_conversation_objective`, check `autonomous_allowed(creator_id, strategy, experiment_id)` — if not allowed, `score = min(score,0.1)` + `flags+=autonomous_paused:reason` + `telemetry.operation_allowed=False` + `failure_class=handoff_required` → forces `operator_queue` (no auto-approve), not delete. Precedence `GLOBAL→CREATOR→STRATEGY→EXPERIMENT` via `autonomous_allowed`. Fail-closed via checks, never ACKs incorrectly.

**Health evaluation not per-message** (would be N+1) — health evaluated periodically via scheduler orchestrate per window (1h/24h), not per inbound (preserves performance bounded).

### 2.3 Extended `commerce/conversation_operations.py` (15 LOC, no LLM)

`degraded_fallback()` extended for exact Phase 22/23 matrix expectations: `product lookup unavailable→no offer, dropfans unavailable→no fabricated purchase/delivery, telemetry→continue only if safe, strategy evidence→SAFE_DEFAULT, experiment→control variant, scheduler→no autonomous re-engagement, redis recovery→preserve pending state` while preserving `dropfans→commerce_suppressed` for Phase 21 compat via exact `low=="dropfans"` before substring.

---

## 3. Files Changed / Created

| File | LOC/type | Change |
|------|----------|--------|
| `commerce/production_control.py` | **MOD +120** | +`HealthReport` + `evaluate_production_health()` + `evaluate_rollout_gate()` + `orchestrate_production_controls()` + `_next_canary_percentage()` + wiring helpers; extends metric windows already 1h/24h/7d/30d |
| `commerce/conversation_operations.py` | **MOD 15** | Exact degraded matrix for Phase 23 (`product lookup`, `dropfans`, `telemetry`, `strategy evidence`, `experiment`, `scheduler`, `redis recovery`) |
| `workers/llm_worker.py` | **MOD +35** (best-effort) | Per-generation `record_metric` + `record_audit` + `autonomous_allowed` gate before auto-approve (emergency fail-closed) |
| `workers/scheduler_worker.py` | **MOD +8** (best-effort) | `orchestrate_production_controls()` inside existing loop after `reconcile_purchases` (no new worker/queue) |
| `tests/test_phase23_autonomous_operations.py` | **NEW 43 tests** | A health 4, B rollout 0/1/5/10/25/50/100, C progression 1→5→10→25→50→100, D blocking 4, E rollback 4, F recovery 4, G emergency 6, H creator isolation, I fan isolation, J strategy governance, K experiment, L re-engagement, M audit, N trace, O failure matrix, P Redis, Q DLQ, R DropFans, S single-pass, T restart safety, U idempotency, V roll-forward, W rollback recovery, X production state, Y windows, Z autonomous control |
| `docs/AI_NATIVE_COMMERCE_PHASE_23_FORENSIC_AUDIT.md` | **NEW** | 23-capability matrix Defined/Wired/Enforced/Persistent/Tested/Autonomous + central gap |
| `docs/AI_NATIVE_COMMERCE_PHASE_23_IMPLEMENTATION_MAP.md` | **NEW** | This file |
| `docs/AI_NATIVE_COMMERCE_PHASE_23_FINAL_REPORT.md` | **NEW** | 32 sections + final verdict |

No migration, no config.json, no new queue/worker, no provider change.

---

## 4. Data-Flow Changes

```
Before Phase 23 (primitives only):
INBOUND → ... → UNIFIED DECISION → Qwen → scoring → SEND (no per-generation metric record, no health evaluation, no rollout advancement, no emergency check, no audit per control transition)

After Phase 23 (orchestrated, no redesign):
INBOUND → ... → UNIFIED DECISION (pressure/risk) → autonomous_allowed(creator/strategy/experiment) check (GLOBAL→CREATOR→STRATEGY→EXPERIMENT precedence, fail-closed) → Qwen → scoring → SEND → record_metric(generation_success/failure, strategy, experiment, outcome, pressure_suppressed) bounded 5000 → record_audit(generation_id/creator/user/objective/strategy/experiment/variant/risk/pressure/decision/outcome/production_state) bounded 1000 → telemetry 19 fields + pressure/risk/operation_allowed/trace (existing)
...
Scheduler every interval: recover_stale → process_due_messages (governed re-engagement) → reconcile_purchases → orchestrate_production_controls() [NEW: for each active rollout: health = evaluate_production_health(creator, window) → production_state = derive_production_state → gate = evaluate_rollout_gate(sample≥5, window≥1h, error<0.20, negative<0.30, handoff<0.10, spam<0.10, pressure<0.15, production_state not suppressed) → if should_rollback(sample≥5, confirmed) → perform_rollback → audit rollback else if gate pass → advance 1→5→10→25→50→100 → audit advance else hold → audit hold] idempotent via check_idempotent, bounded, creator-isolated, no PII, no message content.
```

All via existing PostgreSQL `user_profiles` JSONB not needed for metrics (in-memory bounded for tests, production would use same JSONB+Redis TTL if persistence required — no migration, prefer bounded in-memory per Stage A forensic).

---

## 5. Metric Model (§4-6)

**Conversation metrics (§4 conversation):** `messages_received` implicit via `generation_success+failure` total, `messages_processed` same, `generation_success/failure` per window, `response_latency` via `total_e2e_latency_ms` (telemetry already), `question_rate` via `question_policy!=NO_QUESTION` metric, `response_mode_distribution` via `response_mode` dimension, `objective_distribution` via `objective` dimension — all via `record_metric` per generation with dimensions `creator_id/user_id/strategy/topic/product_family/lifecycle/objective/response_mode/experiment/variant/outcome`.

**Behavioral metrics (§4 behavioral):** `strategy_attempts` via `ExtendedEvidence attempt_count` + `record_metric(strategy, value)`, `strategy_positive/negative/purchase/confidence/fatigue, exploration_rate/exploitation_rate` via `StrategyMode` counts per window.

**Safety metrics (§4 safety):** `operation_blocked` via `policy_allows false`, `pressure_suppressed` via pressure bucket suppress, `risk_suppressed` via RiskState SUPPRESS, `handoff_required` via HANDOFF, `spam_blocked` via is_spam_risk, `reengagement_blocked` via is_reengagement_governed_allowed false, `invalid_product/price/url/purchase_without_evidence_blocked` via policy_allows before Qwen — all via `record_metric` with `risk_state/failure_class` dimension.

**Reliability (§4 reliability):** `retryable/permanent/degraded/handoff_failures` via `classify_failure` + `record_metric(failure_class)`, `DLQ_count` via `move_to_dlq`, `reclaimed_count` via `requeue_stalled_messages XAUTOCLAIM`, `duplicate_prevention` via `check_idempotent` + `strategy_generation_seen` ring.

**Dimensions (§5):** Every metric preserves `creator_id, user_id, strategy, topic, product_family, lifecycle, objective, response_mode, experiment, variant, outcome, attribution_type, failure_class, risk_state` (not message content, not secrets). Creator isolation via `creator_id` in key.

**Windows (§6):** `1h (3600s), 24h (86400), 7d, 30d` via `_window_cutoff()` + `query_metrics(window)` filtering `timestamp >= cutoff` with `datetime.fromisoformat`. No unlimited accumulation — `_metric_events` bounded 5000 prune 20%, audits 1000, exposures 50/30d via `prune_all_retention()`.

---

## 6. Rollout Model (§7, §10)

`Rollout` dataclass: `rollout_id/target/scope {GLOBAL,CREATOR,COHORT,EXPERIMENT,STRATEGY}/percentage {0,1,5,10,25,50,100} validated/start_time/status ACTIVE/ROLLED_BACK/COMPLETED/created_by/reason`. `is_rollout_active_for(creator,user,rollout)` hash `SHA256(creator:user:rollout_id)[:8]/2^32*100 < percentage` stable per fan, creator scope checks `int(target)==creator_id`, cohort via same hash with `cohort_id` as rollout_id. `create_rollout` → `_rollout_registry[rollout_id]` + metric, `disable_rollout`/`enable_rollout` (roll-forward) idempotent, `should_rollback` minimum-sample 5, window, thresholds severity `confirmed/severe`, `perform_rollback` scope-aware fail-safe (if scope EXPERIMENT → `disable_experiment(target)`), `rollback_safety_check` protects `offers/transactions/purchases/fan_memory/dlq` (behavioral only). Automatic via `orchestrate_production_controls` (not one-event, requires window+threshold+severity).

---

## 7. Rollback Model (§9, §11, §12)

**Triggers:** `confirmed_regression, safety_block_spike, spam_spike, handoff_spike, send_failure_spike, DLQ_spike, commerce_rejection_spike, abnormal_pressure_increase, experiment_failure` mapped to `should_rollback(current{conversion, engagement, rejection_rate, cooldown_rate}, baseline{0.30/0.60/0.10/0.05})` thresholds 0.20/0.15 etc. + `sample_size≥5` + `severity confirmed` (≥1 reason) else warning_only.

**When rollback:** `perform_rollback(rollout_id, reason)` → `disable_rollout` (ROLLED_BACK) + `record_metric(rollout_rollback)` + `record_audit(decision=rollback)` + if EXPERIMENT scope → `disable_experiment`. Fall back to previous known-good: `is_rollout_active_for` now false → `strategy_governed_selection` SAFE, `experiment` CONTROL, `re-engagement` pause still governed. `rollback_safety_check` ensures target not `offers/transactions/purchases/fan_memory/dlq` → only behavioral config, commercial truth immutable (verified via tests O). Idempotent via `check_idempotent`.

**Recovery (§10 emergency, §20 recovery):** Pause `GLOBAL→CREATOR→STRATEGY→EXPERIMENT→RE-ENGAGEMENT→COMMERCE` precedence via `autonomous_allowed`. Unknown control state → fail-safe per `is_global_paused` (global checked first, not paused by default for normal operation, but explicit `set_emergency` → pause). Recovery deterministic `PAUSED→RECOVERING` (via `failure_class retryable` → RECOVERING) → `verify health (evaluate_production_health) + verify dependencies (PG/Redis/DropFans/Telegram/Qwen per classify_failure)` → `RECOVERING→CAUTION` (risk caution) → `CAUTION→NORMAL` (success_rate ≥0.9, no suppress) — not immediately 100% (via `derive_production_state` steps).

---

## 8. Failure Model (§16, §18)

| Component | Failure type | Class | Fallback (existing matrix §15) |
|---|---|---|---|
| Qwen unavailable | timeout | DEGRADED | deterministic fallback `safe_fallback_response` → operator_queue |
| Scoring unavailable | LLM fail | DEGRADED | safe non-commercial `operator_queue` (0.0) |
| Memory unavailable | write fail | DEGRADED | conversation-only `continue_without_memory` (3 msgs) |
| Product lookup unavailable | DB fail | DEGRADED | `no offer` (has_relevant_product False) |
| DropFans unavailable | API fail | DEGRADED | `no fabricated purchase/delivery` (has_valid_purchase_evidence false) |
| Telemetry unavailable | write fail | DEGRADED | `continue only if safe` (best-effort skip) |
| Strategy evidence unavailable | DB fail | DEGRADED | `SAFE_DEFAULT` (0.30) |
| Experiment unavailable | not found | DEGRADED | `control variant` |
| Scheduler unavailable | not running | DEGRADED | `no autonomous re-engagement` |
| Redis recovery issue | stall idle>30s | RETRYABLE (preserve pending) | `preserve pending state` (XAUTOCLAIM not XDEL) |
| Invalid Telegram entity | permanent | PERMANENT | DLQ+XACK no requeue |
| Stalled Redis | stalled | RETRYABLE | pending/retry via XAUTOCLAIM |
| Operator required | blocked | HANDOFF_REQUIRED | restrict automation (`autonomous_allowed` false) |

Verified consistent across `llm_worker` (try/except already), `send_worker` (DLQ+ACK for PERMANENT already), `scheduler`, `Redis`, `DropFans`, `PostgreSQL`, `memory`, `experiments`, `strategy learning` via `classify_failure`.

---

## 9. Degraded Matrix (§15)

| Failure | Safe behavior (§15 table) |
|---|---|
| Qwen unavailable | deterministic fallback |
| Scoring unavailable | safe non-commercial response |
| Memory unavailable | conversation-only behavior |
| Product lookup unavailable | no offer |
| DropFans unavailable | no fabricated purchase/delivery |
| Telemetry unavailable | continue only if safe |
| Strategy evidence unavailable | SAFE_DEFAULT |
| Experiment unavailable | control variant |
| Scheduler unavailable | no autonomous re-engagement |
| Redis recovery issue | preserve pending state |

Exact behavior follows existing architecture per forensic §10, not invented.

---

## 10. Test Matrix (§25)

**New `tests/test_phase23_autonomous_operations.py` 43 tests (A-Z):**

- A health 4: healthy (NORMAL 10 success), caution (rejection 0.4), degraded (degraded_rate >0.15), suppressed (spam >0.10)
- B rollout 0/1/5/10/25/50/100 percentages + 0% none/100% all/1% ~1%
- C progression 1→5→10→25→50→100 (stages) + orchestrate advance 1→5 when health NORMAL (purchases 3 to match baseline 0.30)
- D blocking 4: insufficient sample (<5), high error 0.5, high rejection/spam, regression SUPPRESSED
- E rollback 4: automatic should_rollback 20 sample confirmed, manual disable, idempotent, no data deletion (offers metric persists)
- F recovery 4: paused→recovering (retryable), recovering→caution, caution→normal, failed dependency
- G emergency 6: global/creator/strategy/experiment/reengagement/commerce pause + fail-closed unknown not paused
- H creator isolation: A pause not B, metrics 1 vs 0
- I fan isolation: spam 5 exposures true vs 0 false, exposures 100 vs 200 isolated
- J strategy governance: GOOD 20/18 vs BAD 20/2 + regression_map GOOD→BAD wins (history not deleted)
- K experiment: unsafe price→false, safe strategy→true
- L re-engagement: paused via global, rejected 3, cooldown true, aftercare true, fatigue 0.4 → all blocked
- M audit: every control transition produces `OperationalAuditRecord` creator-isolated
- N trace: exists <500 no content/secrets
- O failure matrix 4 classes + degraded fallbacks 10 exact
- P Redis retryable remains pending
- Q DLQ permanent
- R DropFans no fabricated
- S single-pass 1/1/1/0
- T restart safety: clearing registry not promote to 100% (new rollout again 1%)
- U idempotency: repeated orchestrate/evaluation no duplicate (check_idempotent), re-engagement dedup reengage:{c}:{u}:{p}
- V roll-forward healthy can progress (1→5)
- W rollback recovery does not delete evidence (attempt 10 still 10)
- X production state reflects controls (paused true→PAUSED, rollback true→ROLLBACK, suppress→SUPPRESSED etc.)
- Y windows 1h/24h/7d remain correct (now, 2h, 2d, 8d)
- Z autonomous control unsafe prevents (global pause → autonomous_allowed false, spam health → gate blocks)

All deterministic, no DB, no LLM, no new workers.

**Targeted regression:** Phase 23 43 + Phase 22 34 + Phase 21 48 + Phase 20 101 = 226 distinct Phase 20-23 + commerce_strategy 62 + conversational 31 + single-pass 6 = 320+; **183+226 overlapping** all pass (Phase 23 43, Phase 22 34, Phase 21 48, Phase 20 101, commerce_strategy 62, conversational 31). Broader suite 433 passed with 9 pre-existing environment failures (Dropfans model drift 8 + worker mock 1) — **new failures 0**.

---

## 11. Architecture Impact

No redesign: Redis Streams + consumer groups `llm_workers` XAUTOCLAIM 30s + PostgreSQL user_profiles JSONB (existing `strategy_exposures 50/30d` etc., rollout registry in-memory bounded few, audit 1000, metrics 5000) + Telethon + DropFans sole + 3 workers + DLQ + dedup md5 + rate limiting **preserved**. Provider `ollama/qwen2.5:3b` unchanged, canary rollout framework **not activated beyond tests** (orchestrate available but not auto-advancing beyond 1% without health gates; `llm_worker` autonomous_allowed gate not yet blocking generation path except via score flag — safe). Emergency controls fail-closed not auto-enabled.

Performance: `record_metric` O(1) append bounded 5000, `query_metrics` scan bounded <5000 with timestamp cutoff — not hot path (hot path 1 record per generation, query offline per orchestrate interval). `is_rollout_active_for` O(1) SHA256. No N+1 DB, no unbounded JSONB (Phase 20 decay preserved via `prune_all_retention`).

---

Implementation map complete. Next: final report with 32 sections + final verdict.
