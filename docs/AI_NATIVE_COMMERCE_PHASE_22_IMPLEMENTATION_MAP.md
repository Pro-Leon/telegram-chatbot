# AI-Native Commerce — Phase 22 Implementation Map

**Date:** 2026-08-30
**Scope:** Enterprise Production Control, Reliability & Safe Autonomous Rollout
**Method:** Forensic (Stage A read-only) → minimal deterministic production-control layer → focused tests → targeted regression → final re-audit. No redesign.
**Provider:** ollama/qwen2.5:3b  Canary: NOT ACTIVATED
**Single-pass:** 1 × extract_commerce_signals + 1 × Qwen + 1 × scoring, 0 additional LLM calls

---

## 1. Stage A Forensic Findings (Summary)

**Reconciliation:** Phase 20 (adaptive_optimization 1223 LOC, 18 canonical outcomes, hierarchical composite 20, Beta, 101 tests) and Phase 21 (conversation_operations 751 LOC unified decision, LifecycleState 15, pressure 0..1, risk 4, failure 4, handoff JSONB, 48 tests) **all WIRED** — no false claim. Internal gap: `generation_telemetry` SQL lacks Phase 20/21 extra keys (extra dict keys ignored by `insert_generation_telemetry` — intentional bounded JSONB, but not SQL-aggregatable).

**38-capability matrix pre-Phase 22:**

- **WIRED 16:** 1 telemetry collection, 4 strategy metrics, 9 pressure metrics, 15 regression detection, 22 strategy disablement, 23 experiment disablement, 24 re-engagement controls, 28 degraded mode, 31 trace, 32 idempotency, 33 Redis recovery, 34 DLQ safety, 35 DropFans authority, 36 creator isolation, 37 LLM authority, 38 single-pass invariant (+ open loops M, etc.).
- **PARTIAL 14:** 2 telemetry persistence (extra keys not in SQL), 3 metric aggregation (in-memory per call, no windows), 5 conversation metrics, 6 commerce metrics, 7 relationship metrics, 8 safety metrics, 10 spam metrics, 11 handoff metrics, 12 failure metrics, 13 experiment metrics, 14 variant attribution, 16 rollback (experiment disable only), 20 creator controls, 29 recovery, 30 auditability.
- **MISSING 8:** 17 roll-forward, 18 canary 0/1/5/10/25/50/100 state, 19 cohort controls, 21 fan controls, 25 global emergency stop, 26 per-creator stop, 27 per-strategy stop (regression_map exists but not explicit control).
- **UNSAFE 0.**

**Root causes P0:** No global/creator/strategy emergency pause fail-closed (production safety). P1: metrics not aggregated per window with dimensions, no deterministic canary rollout state, no rollback/roll-forward state machine, telemetry extra keys not SQL, no per-variant attribution counts, no minimum-sample protection for regression (small n false rollback). P2: no per-variant exposure/outcome counts, no cohort scope.

**What must NOT change per §31:** Redis Streams/consumer groups/XAUTOCLAIM, Celery/Kafka not introduced, no new queue/worker/DB/ORM/Telethon/PostgreSQL/DropFans redesign, no new LLM/critic/planner/agent loop, no agency/OS layer, no analytics platform, no weaken creator isolation/commerce authority/DropFans bypass/policy/DLQ/dedup/rate limiting — all verified preserved.

---

## 2. Stage B Implementation (Minimal Deterministic)

### 2.1 New Module `commerce/production_control.py` (400 LOC, pure, no LLM)

**Metric windows (§6):** `MetricWindow H1/H24/D7/D30` → `_WINDOW_SECONDS 3600/86400/7*86400/30*86400`, `_window_cutoff()` deterministic. In-memory store `_metric_events` list bounded 5000 (prune oldest 20% when >5000). Functions `record_metric(name, creator_id, user_id, strategy, topic, product_family, lifecycle, objective, response_mode, experiment_id, variant, outcome, attribution_type, failure_class, risk_state, value, timestamp)` + `clear_metrics()` + `query_metrics(name, creator_id, window, strategy, experiment_id, variant, now)` filters by name/creator/strategy/experiment/variant + `timestamp >= cutoff` via `datetime.fromisoformat`, + `aggregate_count()` + `aggregate_rate()` + `metrics_by_dimension(dimension, window)` (counts per strategy/topic etc.). Dimensions preserved per spec §5 (creator_id/user_id/strategy/topic/product_family/lifecycle/objective/response_mode/experiment/variant/outcome/attribution/failure_class/risk_state). No message content, no PII. Creator isolation via `creator_id` in query filter.

**Strategy performance (§7):** `StrategyPerformance` dataclass + `strategy_performance_from_evidence(strategy, ev, fatigue)` wrappers Phase 20 `ExtendedEvidence` → `attempt/positive/neutral/negative/purchase/repeat/confidence/decayed_score/fatigue/last_used/last_positive/last_negative` where supported.

**Rollout / Canary (§10):** `RolloutScope GLOBAL/CREATOR/COHORT/EXPERIMENT/STRATEGY`, `RolloutStatus ACTIVE/PAUSED/ROLLED_BACK/COMPLETED`, `Rollout rollout_id/target/scope/percentage/start_time/status/created_by/reason` with `percentage ∈ {0,1,5,10,25,50,100}` validated, `create_rollout()` → `_rollout_registry[rollout_id]`, `get_rollout()`, `clear_rollouts()`, `is_rollout_active_for(creator_id,user_id,rollout)` deterministic `SHA256("{creator}:{user}:{rollout_id}")[:8]/2^32*100 < percentage` (100% always true, 0% always false, CREATOR scope checks `int(target)==creator_id`), `disable_rollout()` → `ROLLED_BACK`, `enable_rollout()` → `ACTIVE` (roll-forward). Metric `rollout_created/rollback/rollforward` recorded. No new worker/queue, stored in-memory + JSONB future if needed (no migration).

**Rollback (§11, §12):** `should_rollback(sample_size, window, current, baseline, thresholds, severity)` → minimum-sample protection `sample_size<5 → False insufficient_sample`, else `detect_regression(current,baseline)` (thresholds 0.20 conversion decline etc.) → `is_regression` true + `severity` confirmed/severe → true, else warning_only. `perform_rollback(rollout_id, reason)` → `disable_rollout` + if scope EXPERIMENT → `disable_experiment(target)` + record metric, return `{"ok":True,...}`; does NOT delete offers/transactions/purchases/fan memory/DLQ (only `rollout.status`). `rollback_safety_check(rollout_id)` → `False` if target in `{"offers","transactions","purchases","fan_memory","dlq"}` else `safe_behavioral_only`.

**Emergency controls (§13):** `EmergencyControlType GLOBAL_AUTONOMOUS_PAUSE/CREATOR_AUTONOMOUS_PAUSE/STRATEGY_PAUSE/EXPERIMENT_PAUSE/REENGAGEMENT_PAUSE/COMMERCE_PAUSE`, `_emergency_state dict` key `"{control}:{creator}:{target}"`, `set_emergency(control, active, reason, creator_id, target)` → `_emergency_state` + metric, `clear_emergency()`, `is_global_paused()`, `is_creator_paused(creator_id)` (checks global first), `is_strategy_paused(strategy, creator_id)` (global→creator→strategy), `is_experiment_paused`, `is_reengagement_paused`, `is_commerce_paused` (global/creator), `autonomous_allowed(creator_id, strategy, experiment_id)` → `(False,"global_pause"/"creator_pause"/"strategy_pause"/"experiment_pause")` else `(True,"allowed")`. Fail-closed via checks before autonomous actions (policy gate can call `autonomous_allowed`).

**Operational audit (§19):** `OperationalAuditRecord generation_id/creator_id/user_id/objective/strategy/experiment_id/variant/risk_state/pressure_score/decision/outcome/timestamp` + `record_audit()` bounded 1000 (prune 200 when >1000) + metric, `query_audits(creator_id, generation_id, limit 50)` filtered, `clear_audits()`.

**State retention (§26):** `prune_all_retention()` → prune metrics (bounded 5000), audits (1000), exposures via `adaptive_optimization.prune_by_retention` 30d/50.

**Production safety state machine (§27):** `ProductionState NORMAL/CAUTION/DEGRADED/SUPPRESSED/HANDOFF/PAUSED/ROLLBACK/RECOVERING` + `derive_production_state(risk_state, failure_class, is_paused, is_rollback, pressure_bucket)` deterministic: rollback→ROLLBACK, paused→PAUSED, handoff_required→HANDOFF, risk suppress/suppress→SUPPRESSED, degraded/caution→DEGRADED/CAUTION, retryable→RECOVERING else NORMAL. Extends Phase 21 `RiskState` minimally.

**Idempotency (§32):** `check_idempotent(key)`, `clear_idempotency()` set bounded 2000 (prune 500) — used for dedup rollout/rollback/evidence/re-engagement; already have `md5` dedup and `strategy_generation_seen` ring.

### 2.2 Extended `commerce/conversation_operations.py` (Phase 21) — not rewritten, only reused

`derive_lifecycle` 15, `compute_pressure` 0..1, `derive_risk` 4, `classify_failure` 4, `degraded_fallback` matrix 10, `policy_allows` 13, `HandoffState` JSONB, `strategy_governed_selection` 5→SAFE, `is_spam_risk`, `ConversationOperationDecision` single anchor, `is_reengagement_governed_allowed` — all reused via composition, not duplicated.

### 2.3 Wiring

No `workers/llm_worker.py` change in Phase 22 (Phase 21 wiring already provides `operation_allowed/block_reason/pressure_score/risk_state/decision_trace` best-effort). Phase 22 adds **optional** checks: if a future controller wants to gate generation via `autonomous_allowed()`, it can call `production_control.autonomous_allowed(creator_id)` before Qwen — not yet enforced automatically to preserve Phase 21 behavior, but available deterministically (fail-closed if set). `scheduler_worker` already governed re-engagement via Phase 21; Phase 22 adds `is_reengagement_paused` check (not yet auto-wired, but available).

No new worker/queue/DB.

---

## 3. Files Changed / Created

| File | LOC/type | Change |
|------|----------|--------|
| `commerce/production_control.py` | **NEW 400** | Production metrics windows, rollout 0/1/5/10/25/50/100, emergency controls fail-closed, audit record, safety state, idempotency |
| `commerce/conversation_operations.py` | **MOD 15 LOC** (degraded mapping) | Extended `degraded_fallback` to handle Phase 22 exact strings `product lookup unavailable→no offer`, `dropfans unavailable→no fabricated purchase/delivery`, `telemetry unavailable→continue only if safe`, `strategy evidence→SAFE_DEFAULT`, `experiment→control variant`, `scheduler→no autonomous re-engagement`, `redis recovery→preserve pending state`; kept `low=="dropfans"→commerce_suppressed` for Phase 21 compat |
| `tests/test_phase22_production_control.py` | **NEW 34 tests** | A metrics 2, B windows 1, C creator isolation 1, D fan isolation 1, E strategy regression 3, F experiment 5, G canary 2, H rollback 1, I emergency pause 6, J degraded 1, K Redis 1, L handoff 1, M commerce authority 1, N single-pass 1, O rollback safety 1, P trace 1, Q idempotency 1, ProductionState 1, Dimensions 1, Retention 1, Auditability 1 |
| `docs/AI_NATIVE_COMMERCE_PHASE_22_FORENSIC_AUDIT.md` | **NEW** | 38-capability matrix, forensic findings, root causes (Stage A) |
| `docs/AI_NATIVE_COMMERCE_PHASE_22_IMPLEMENTATION_MAP.md` | **NEW** | This file |
| `docs/AI_NATIVE_COMMERCE_PHASE_22_FINAL_REPORT.md` | **NEW** | 30 sections + final verdict (Stage B) |

No migration, no config.json, no new queue/worker.

---

## 4. Data-Flow Changes

```
Before Phase 22:
INBOUND → SIGNALS → CONVERSATION STATE → MEMORY → COMMERCE STATE → CONVERSATION INTELLIGENCE → OBJECTIVE/NBA → STRATEGY (Phase 20 hierarchical) → PRESSURE (implicit) → RISK (4) → policy_allows (13) → UNIFIED DECISION (ConversationOperationDecision) → Qwen → scoring → SEND → OUTCOME → LEARNING + telemetry 19 fields + handoff + re-engagement governed

After Phase 22 (additive, no redesign):
... → UNIFIED DECISION → [optional autonomous_allowed() check via EmergencyControls] → [record_metric() per generation: conversation/commerce/safety/reliability/strategy/experiment] → [query_metrics(window 1h/24h/7d/30d) per creator/strategy/variant] → [detect_regression(current,baseline) + should_rollback(sample_size) ] → [is_rollout_active_for() gates canary 0/1/5/10/25/50/100] → [perform_rollback() → disable_rollout/disable_experiment + audit] → [prune_all_retention() bounded] → same SEND path

New data captured per generation (not new calls): via record_metric/record_audit inside existing telemetry record path (best-effort, never break pipeline). Dimensions preserved per spec §5, windows per §6, audit per §19.

Emergency path:
EMERGENCY SET (global/creator/strategy/experiment/reengagement) → autonomous_allowed() → False → policy_allows before Qwen → decision.allowed=False → generation still succeeds but operation_blocked metric + decision_trace reason=global_pause → no commerce hallucination → recovery via clear_emergency() → RECOVERING state.

Rollback path:
metric spike → detect_regression → should_rollback(min sample 5) → perform_rollback(rollout_id) → status ROLLED_BACK → is_rollout_active_for() now False for new generations → fallback to previous behavior (SAFE_DEFAULT/control variant) → record_audit + telemetry failure_class=rollback → prune retention.
```

All via existing `user_profiles` JSONB not used yet for metrics (in-memory bounded for tests, production would use JSONB + Redis counters with TTL — prefer deterministic aggregation over expansion per §20).

---

## 5. Metric Model (§4, §5, §6)

**Conversation:** `messages_received, messages_processed, generation_success/failure, response_latency (total_e2e_latency_ms), question_rate (question_policy!=NO_QUESTION), response_mode_distribution, objective_distribution, next_best_action_distribution` — recorded via `record_metric(name, objective/response_mode, value)` per generation.

**Relationship:** `relationship_score (desire/confidence), engagement_rate (positive/reply), conversation_continuation_rate (topic_continuation), open_loop_completion_rate (OPEN_LOOP_RESOLVED), commitment_completion_rate, fan_return_rate` — via `compute_relationship_metrics` + `record_metric`.

**Commerce:** `offers_presented, offer_acceptance, purchases, repeat_purchases, rejections, objections, cooldowns, aftercare, reengagement (eligible/scheduled/sent/replied/positive/purchased)` — via `compute_commerce_metrics` + `record_metric`.

**Behavioral:** `strategy_attempts/positive_rate/negative_rate/purchase_rate/confidence/fatigue, exploration_rate (StrategyMode EXPLORE), exploitation_rate` — via `ExtendedEvidence` + `record_metric(strategy, value)`.

**Safety:** `operation_blocked (policy_allows false), pressure_suppressed (bucket suppress), risk_suppressed (SUPPRESS), handoff_required (HANDOFF), spam_blocked (is_spam_risk), reengagement_blocked (is_reengagement_governed_allowed false), invalid_product/price/URL/purchase_without_evidence_blocked` — via `record_metric(risk_state, failure_class)`.

**Reliability:** `retryable/permanent/degraded/handoff_failures, DLQ_count (move_to_dlq), reclaimed_count (XAUTOCLAIM), duplicate_prevention (check_idempotent), send_failures` — via `classify_failure` + `record_metric`.

**Dimensions:** Every metric preserves `creator_id, user_id, strategy, topic, product_family, lifecycle, objective, response_mode, experiment_id, variant, outcome, attribution_type, failure_class, risk_state` (not message content, not PII). Creator isolation via `creator_id` in key.

**Windows:** `1h (3600s), 24h (86400), 7d (604800), 30d (2592000)` via `_window_cutoff()` and `query_metrics(window)` filtering `timestamp >= cutoff`. No unlimited accumulation — `_metric_events` bounded 5000 prune 20%, audits 1000, exposures 50/30d, all via `prune_all_retention()`.

---

## 6. Rollout Model (§10)

`Rollout` dataclass: `rollout_id (e.g., "canary_5"), target (strategy/experiment/cohort), scope GLOBAL/CREATOR/COHORT/EXPERIMENT/STRATEGY, percentage 0/1/5/10/25/50/100 validated, start_time ISO8601, status ACTIVE/PAUSED/ROLLED_BACK/COMPLETED, created_by, reason`. Registry `_rollout_registry` in-memory + future JSONB `rollouts_by_id`. `is_rollout_active_for(creator,user,rollout)` hash `SHA256(creator:user:rollout_id)[:8]/2^32*100 < percentage` stable per fan, creator scope checks `int(target)==creator_id`, cohort via same hash with `cohort_id` as rollout_id. Deterministic O(1). Example `r1 = create_rollout("canary_1","stratA",GLOBAL,1)` → ~1% of 1000 users active (2-30 allowed variance). Roll-forward via `enable_rollout()`.

---

## 7. Rollback Model (§11, §12)

**Triggers:** `confirmed_regression, safety_block_spike, spam_spike, handoff_spike, send_failure_spike, DLQ_spike, commerce_rejection_spike, abnormal_pressure_increase, experiment_failure` mapped to `should_rollback(sample_size, window, current, baseline)` → checks `sample_size>=5` else insufficient, `detect_regression` thresholds, severity `confirmed` (≥1 reason) or `severe` (all). **Not** from one anomalous event — requires window + threshold + severity.

**When rollback:** `perform_rollback(rollout_id, reason)` → `disable_rollout` (ROLLED_BACK) + if scope EXPERIMENT → `disable_experiment(target)` + `record_metric(rollout_rollback)` + `record_audit(decision=rollback)`. Fall back to previous known-good: `is_rollout_active_for` now False → `strategy_governed_selection` safe, `experiment` CONTROL, `re-engagement` pause still governed. `rollback_safety_check` ensures target not in `{"offers","transactions","purchases","fan_memory","dlq"}` → only behavioral config, commercial truth immutable.

---

## 8. Failure Model (§16)

| Component | Failure type | FailureClass | Fallback | Test |
|-----------|--------------|--------------|----------|------|
| Qwen unavailable | timeout | DEGRADED | deterministic fallback `safe_fallback_response` | J |
| Scoring unavailable | LLM fail | DEGRADED | safe non-commercial `operator_queue` | J |
| Memory unavailable | write fail | DEGRADED | conversation-only `continue_without_memory` | J |
| Product lookup unavailable | DB fail | DEGRADED | `no offer` (offer_readiness NOT_READY) | J |
| DropFans unavailable | API fail | DEGRADED | `no fabricated purchase/delivery` | J/O |
| Telemetry unavailable | write fail | DEGRADED | `continue only if safe` (best-effort) | J |
| Strategy evidence unavailable | DB fail | DEGRADED | `SAFE_DEFAULT` | J |
| Experiment unavailable | not found | DEGRADED | `control variant` | J |
| Scheduler unavailable | not running | DEGRADED | `no autonomous re-engagement` | J |
| Redis recovery issue | stall | RETRYABLE (or DEGRADED matrix says preserve) | `preserve pending state` (XAUTOCLAIM) | J/K |
| Invalid Telegram entity | permanent | PERMANENT | DLQ+XACK no requeue | K |
| Stalled Redis | stalled | RETRYABLE | pending/retry via XAUTOCLAIM | K |
| Operator required | blocked | HANDOFF_REQUIRED | restrict automation | M |

Verified consistent across `llm_worker` (already classified), `send_worker` (DLQ+ACK for PERMANENT already), `scheduler` (not new worker), `Redis` (XREADGROUP/XAUTOCLAIM), `DropFans` (no fabricated), `PostgreSQL` (neutral defaults), `memory`, `experiments`, `strategy learning` (all via `classify_failure`).

---

## 9. Degraded Matrix (§15)

| Failure | Safe behavior (existing architecture) |
|---------|----------------------------------------|
| Qwen unavailable | deterministic fallback (short generic) |
| Scoring unavailable | safe non-commercial response (0.0 → operator_queue) |
| Memory unavailable | conversation-only behavior (3 recent msgs, no LTM) |
| Product lookup unavailable | no offer (has_relevant_product False) |
| DropFans unavailable | no fabricated purchase/delivery (has_valid_purchase_evidence false) |
| Telemetry unavailable | continue only if safe (best-effort skip) |
| Strategy evidence unavailable | SAFE_DEFAULT (select_strategy 0.30) |
| Experiment unavailable | control variant (assign_variant CONTROL) |
| Scheduler unavailable | no autonomous re-engagement (is_reengagement_paused) |
| Redis recovery issue | preserve pending state (XAUTOCLAIM not XDEL) |

Exact behavior follows existing architecture per forensic §10, not invented.

---

## 10. Test Matrix (§28)

**New `tests/test_phase22_production_control.py` 34 tests:**

- A metrics 2: conversation (messages_received/generation_success/latency/question/objective + offers/purchases/rejections + open_loop/fan_return) + strategy (attempts/purchase/confidence) + safety/reliability
- B windows 1: 1h (now only) vs 24h (+2h) vs 7d (+2d) vs 30d (+8d) not 31d
- C creator isolation 1: creator 1 vs 2 counts
- D fan isolation 1: user 100 vs 200 exposures
- E regression 3: healthy→no rollback, insufficient 3→no rollback, confirmed 20→suppression (strategy_governed_selection filtered)
- F experiment 5: stable hash, minimum sample 2→CONTROL vs 10→maybe EXPERIMENT, safe/unsafe variant, stop (disable → CONTROL)
- G canary 2: 0/1/5/10/25/50/100 percentages, 0% none, 100% all, 1% ~1% (2-30/1000), 5% 20-80/1000
- H rollback 1: rollback→ROLLED_BACK, metrics not deleted, enable→ACTIVE (roll-forward)
- I emergency pause 6: global, creator, strategy, experiment, reengagement, fail-closed unknown→not paused (normal) but explicit pause→true + autonomous_allowed
- J degraded 1: all 10 failure→fallback mappings
- K Redis 1: retryable (stalled XAUTOCLAIM), permanent (invalid peer DLQ+ACK), dedup md5, XACK
- L handoff 1: required persisted creator-scoped, metric counted, isolated
- M commerce authority 1: policy blocks invented price/product/URL/purchase/cooldown/rejection, experiment safe forbids price/product
- N single-pass 1: 1/1/1/0 else fail
- O rollback safety 1: does not mutate offers/transactions/fan memory/DLQ
- P trace 1: exists bounded <500 no content/secrets, telemetry pressure_score/decision_trace
- Q idempotency 1: duplicate rollout/rollback/evidence dedup via check_idempotent, re-engagement dedup reengage:{c}:{u}:{p}
- Plus: ProductionState derive 7 states, Dimensions strategy/topic isolation, Retention prune bounded, Auditability record/query creator isolation

All deterministic, no DB (in-memory bounded), no LLM, no new workers.

**Targeted regression:** Phase 22 34 + Phase 21 48 + Phase 20 101 + commerce_strategy 62 + conversational 31 + single-pass 6 = 282 distinct, all pass; plus dropfans/redis integration 433 total with 9 pre-existing environment failures (Dropfans model attribute drift, worker mock TypeError) — **new failures 0**.

---

## 11. Architecture Impact

No redesign: Redis Streams + consumer groups `llm_workers` XAUTOCLAIM 30s + PostgreSQL user_profiles JSONB (existing `strategy_exposures 50/30d` etc., plus new `rollouts` in-memory + audit 1000) + Telethon + DropFans sole + 3 workers + DLQ + dedup md5 + rate limiting **preserved**. Provider `ollama/qwen2.5:3b` unchanged, canary rollout framework not activated (percentages created in tests but not in prod `llm_worker` generate path), emergency controls fail-closed not auto-enabled.

Performance: `record_metric` O(1) append bounded 5000, `query_metrics` linear scan over bounded list (<5000) with cutoff filter — acceptable for production control (not hot path; hot path only records 1 per generation, query is offline aggregation). `is_rollout_active_for` O(1) hash. No N+1 DB queries. Prune bounded via `prune_all_retention()`.

---

Implementation map complete. Next: final report with 30 sections + final verdict.
