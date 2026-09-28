# AI-Native Commerce — Phase 23 Forensic Audit

**Date:** 2026-08-30
**Scope:** Enterprise Autonomous Rollout & Operational Intelligence — forensic audit before implementation (Stage A, read-only)
**Method:** Reconcile actual implementation against Phase 22 claims + live global search of 22 primary files + caller/callee tracing. No production code modified in Stage A.
**Working directory:** E:\chatbot branch main
**Provider:** ollama/qwen2.5:3b  Canary: NOT ACTIVATED
**Single-pass invariant:** 1 × extract_commerce_signals + 1 × Qwen + 1 × scoring (verified pre-Phase 23)

---

## 1. Phase 22 Reconciliation

| Claim (Phase 22 docs) | Actual repository | Verdict |
|---|---|---|
| `commerce/production_control.py` 400 LOC MetricWindow 1h/24h/7d/30d bounded 5000, StrategyPerformance, Rollout 0/1/5/10/25/50/100 scopes 5, EmergencyControlType 6 fail-closed, OperationalAuditRecord 1000, ProductionState 8, idempotency 2000 | File exists 400 LOC, `MetricWindow` enum 4, `_metric_events` bounded 5000, `Rollout` validated percentages, `EmergencyControlType` 6, `OperationalAuditRecord` 1000, `ProductionState` 8, `check_idempotent` bounded 2000 — counts match | **RECONCILED — DEFINED** |
| `commerce/conversation_operations.py` 751 LOC LifecycleState 15, CommercialPressureBudget 0..1, RiskState 4, FailureClass 4, policy_allows 13, HandoffState JSONB, ConversationOperationDecision single anchor, re-engagement governed | File exists 751 LOC, `LifecycleState` 15 hit, `CommercialPressureBudget` 0..1 bucket relationship/exploration/opportunity/suppress, `RiskState` 4, `FailureClass` 4, `policy_allows` 13 checks line 304, `HandoffState` JSONB line 340, `ConversationOperationDecision` 15 fields + `trace_compact()` | **RECONCILED — DEFINED + WIRED via llm_worker 707-815** |
| `core/telemetry.py` +8 Phase 21 fields (operation_allowed/block_reason/pressure_score/risk_state/handoff_required/failure_class/decision_trace/lifecycle_state) | 8 fields present line 77-90 + `to_dict()` | **RECONCILED — WIRED** (wired in llm_worker best-effort) |
| `workers/llm_worker.py` wired unified decision before Qwen (pressure/risk/decision_trace) | Lines 707-815 `compute_pressure`→`derive_risk`→`derive_lifecycle`→`build_operation_decision()`→telemetry `pressure_score/risk_state/operation_allowed/block_reason/decision_trace/lifecycle_state/handoff_required` — wired | **RECONCILED — WIRED** |
| `workers/scheduler_worker.py` governed re-engagement (pressure/fatigue/max-frequency 2/7d) | Lines 206-235 `is_reengagement_governed_allowed(pressure, fatigue, max_frequency 2/7d)` before `schedule_reengagement_if_eligible` — wired | **RECONCILED — WIRED** |
| Tests `tests/test_phase22_production_control.py` 34 passed + `test_phase21` 48 + `test_phase20` 101 | `pytest -q` 34+48+101 = 183 passed (run 2026-08-30, 1.34s/2.16s) — all A-Q + isolation + canary + rollback etc. | **RECONCILED — TESTED** |
| No new workers/queues/migrations, single-pass 1/1/1/0 | `workers/` 3 files, `db/migrations` none Phase 22, `verify_single_pass` 1/1/1/0 | **RECONCILED — PRESERVED** |

No Phase 22 claim false. **New discovery in Stage A:** primitives are **defined + tested** but **not all wired/enforced/persistent/autonomous in production call graph** (see §3-4).

---

## 2. Primary Files Inspected

```
commerce/production_control.py         400 LOC MetricWindow + record/query/aggregate + Rollout 0/1/5/10/25/50/100 + Emergency 6 + Audit 1000 + ProductionState 8 + idempotency
commerce/conversation_operations.py    751 LOC LifecycleState 15 + CommercialPressureBudget + RiskState + FailureClass + policy_allows 13 + Handoff JSONB + ConversationOperationDecision single anchor
commerce/adaptive_optimization.py     1223 LOC ConversationObservation/Exposure/CanonicalOutcome 18/weights/ExtendedEvidence/Beta 0.02..0.5/score hierarchical 5/10/fatigue/window/lifecycle 9/metrics/regression
commerce/strategy_learning.py          381 LOC composite bounded 20 dedup 100 + governing wrapper
commerce/conversation_intelligence.py  207 LOC 14 objectives priority 1-99
commerce/conversation_outcomes.py       92 LOC canonical 18
commerce/conversational.py             232 LOC build_conversational_commerce_state
commerce/long_term_memory.py           262 LOC FACT...PURCHASE_EVENT 20 SLOW 90/MEDIUM 30/FAST 7
commerce/fan_memory.py                  46 LOC commercial_preferences_by_creator
commerce/product_selection.py          340 LOC deterministic + purchase exclusion
commerce/content_matching.py           140 LOC rank_products bundle-aware -0.20/-0.15
commerce/dao.py                       1060 LOC commerce_offers + timing/behavioral + aftercare
memory/context.py                      647 LOC build_qwen3_context compact
core/telemetry.py                      242 LOC GenerationTelemetry 19 fields (11+8)
workers/llm_worker.py                 1321 LOC single-pass 1/1/1 + unified decision (wired)
workers/scheduler_worker.py            264 LOC claim FOR UPDATE SKIP LOCKED + governed re-engagement
db/postgres.py                        2775 LOC user_profiles JSONB (exposures 50/30d, evidence 20, handoff) + generation_telemetry + scheduled_messages
db/dropfans.py                         358 LOC synthetic SHA256%2^62 per-sale txn
integrations/dropfans/service.py       824 LOC poll_sales + buyer grant NOT exposed (external blocked)
chatbotv2/main.py / client.py          MTProto + debounce (unchanged)
db/redis.py                            ~400 LOC ensure_consumer_group/XREADGROUP/XAUTOCLAIM 30s/XACK/DLQ/dedup md5
```

---

## 3. Global Search Inventory (Representative)

| Pattern | Hits | Representative evidence |
|---|---|---|
| `record_metric` | 10 | All in `commerce/production_control.py` (definition) — **0 hits** in `workers/`, `commerce/conversational`, `core/telemetry` (production not calling) |
| `query_metrics`/`aggregate_count`/`metrics_by_dimension` | 7 | All in `production_control.py` + `tests/test_phase22_production_control.py` — 0 in `workers/` |
| `Rollout`/`is_rollout_active_for`/`disable_rollout`/`enable_rollout` | 18 | Defined in `production_control.py` 18 lines;.called only in `tests/test_phase22` + `production_control` itself — 0 in `workers/` or `commerce/conversational` |
| `should_rollback`/`perform_rollback`/`rollback_safety_check` | 5 | Defined in `production_control.py`; called only in `tests/test_phase22` — 0 in `workers/` or `scheduler` |
| `EmergencyControlType`/`autonomous_allowed` | 14 | Defined in `production_control.py` 14 lines; `autonomous_allowed` called 0 in `workers/` (llm_worker checks `is_global_paused`? No — search 0 hits in workers) |
| `derive_production_state` | 2 | Defined in `production_control.py`; 0 hits in `workers/` |
| `OperationalAuditRecord`/`query_audits` | 4 | Defined in `production_control.py`; `record_audit` called 0 in `workers/` (only tests) |
| `idempotency`/`strategy_generation_seen` | 6 | `production_control:check_idempotent` + `adaptive_optimization:strategy_generation_seen_by_creator` ring 100 — `llm_worker` uses `strategy_generation_seen` via `update_strategy_evidence_extended` (wired), but not `check_idempotent` |
| `experiment` | 58 | Defined `adaptive_optimization:Experiment stable hash SHA256(creator:user:exp)` + `experiment_safe_to_apply`; `production_control:experiment_governed_assignment`; wired in `llm_worker` telemetry `experiment_id/variant` but not in rollout gate |
| `regression` | 4 | `adaptive_optimization:detect_regression(thresholds 0.20/0.15)` + `production_control:should_rollback` — not called periodically |
| `scheduler`/`reengagement` | 18 | `scheduler_worker` governed re-engagement wired; `production_control` metrics not wired to scheduler health |
| `telemetry`/`handoff` | 38 | `core/telemetry GenerationTelemetry 19 fields` wired per generation; `HandoffState` JSONB `handoff_by_creator` wired in `conversation_operations` + in-memory fallback — but handoff metrics not aggregated per window |
| `ScheduledMessages` | 18 | `db/postgres scheduled_messages FOR UPDATE SKIP LOCKED` + `scheduler_worker` claim — metrics not aggregated |

**Conclusion:** Phase 22 primitives are **importable and tested** but **production call graph does not invoke** `record_metric`/`query_metrics` per generation, `is_rollout_active_for` per strategy, `should_rollback` per window, `autonomous_allowed` before Qwen, `derive_production_state` continuously, or `record_audit` per control transition. In other words, **control primitives defined, not continuously enforced**.

---

## 4. Capability Matrix (Required §3)

| Capability | Defined | Wired | Enforced | Persistent | Tested | Autonomous | Verdict |
|---|---|---|---|---|---|---|---|
| Metric collection | **YES** | **NO** | **NO** | **PARTIAL** (in-memory bounded 5000, not per-generation in prod) | **YES** (tests/test_phase22 A) | **NO** | **PARTIAL** |
| Metric aggregation | **YES** | **NO** | **NO** | **PARTIAL** | **YES** (test B windows) | **NO** | **PARTIAL** |
| 1h window | **YES** | **NO** | **NO** | **PARTIAL** | **YES** | **NO** | **PARTIAL** |
| 24h window | **YES** | **NO** | **NO** | **PARTIAL** | **YES** | **NO** | **PARTIAL** |
| 7d window | **YES** | **NO** | **NO** | **PARTIAL** | **YES** | **NO** | **PARTIAL** |
| 30d window | **YES** | **NO** | **NO** | **PARTIAL** | **YES** | **NO** | **PARTIAL** |
| Creator metrics | **YES** | **NO** | **NO** | **PARTIAL** | **YES** (C) | **NO** | **PARTIAL** |
| Fan metrics | **YES** | **NO** | **NO** | **PARTIAL** | **YES** (D) | **NO** | **PARTIAL** |
| Strategy metrics | **YES** | **PARTIAL** (`ExtendedEvidence` via strategy_learning) | **NO** | **YES** (JSONB 20) | **YES** | **NO** | **PARTIAL** |
| Experiment metrics | **YES** | **NO** | **NO** | **PARTIAL** | **YES** (F) | **NO** | **PARTIAL** |
| Variant metrics | **YES** | **NO** | **NO** | **PARTIAL** | **YES** | **NO** | **PARTIAL** |
| Regression detection | **YES** | **NO** | **NO** | **NO** (in-memory thresholds) | **YES** (E) | **NO** | **PARTIAL** |
| Canary rollout | **YES** | **NO** | **NO** | **PARTIAL** (registry in-memory, not JSONB) | **YES** (G) | **NO** | **PARTIAL** |
| Roll-forward | **YES** | **NO** | **NO** | **PARTIAL** | **YES** (H) | **NO** | **PARTIAL** |
| Rollback | **YES** | **NO** | **NO** | **PARTIAL** | **YES** (H) | **NO** | **PARTIAL** |
| Emergency pause | **YES** | **NO** | **NO** | **PARTIAL** (in-memory dict, not JSONB) | **YES** (I) | **NO** | **PARTIAL** |
| Recovery | **YES** | **NO** | **NO** | **PARTIAL** | **YES** (via derive_production_state) | **NO** | **PARTIAL** |
| Audit trail | **YES** | **NO** | **NO** | **PARTIAL** (1000 bounded, not per-generation in prod) | **YES** | **NO** | **PARTIAL** |
| Decision trace | **YES** | **YES** (llm_worker sets `telemetry.decision_trace` via `build_operation_decision.trace_compact()` bounded <500) | **YES** (scoring gate) | **YES** (telemetry per generation) | **YES** (P) | **YES** | **WIRED** |
| Handoff | **YES** | **YES** (llm_worker `is_handoff`→`is_strategy_allowed` blocks; persistence via `handoff_by_creator` JSONB + in-memory) | **PARTIAL** (blocks this turn, metrics not aggregated) | **YES** (JSONB) | **YES** (L) | **NO** | **PARTIAL** |
| Degraded mode | **YES** | **YES** (`degraded_fallback` matrix 10 used in `classify_failure` + `llm_worker` empty draft→operator_queue) | **YES** | **N/A** | **YES** (J) | **YES** | **WIRED** |
| Re-engagement governance | **YES** | **YES** (scheduler_worker governed via `is_reengagement_governed_allowed(pressure, fatigue, max 2/7d)`) | **YES** (before schedule) | **YES** (scheduled_messages) | **YES** (X in Phase21 + Phase22 X) | **YES** | **WIRED** |
| Strategy governance | **YES** | **YES** (`strategy_governed_selection` wrapped via `adaptive_optimization` + `conversation_operations` governed) | **YES** (risk SUPPRESS→SAFE) | **YES** (evidence 20) | **YES** (J Phase21) | **NO** (not per rollout percentage) | **PARTIAL** |

**Summary counts:** Defined **23/23**, Wired **5/23** (decision trace, handoff, degraded, re-engagement, strategy governance partial), Enforced **3/23**, Persistent **4/23**, Tested **23/23**, Autonomous **3/23**. **Central pattern: primitives tested, not operationally autonomous.**

---

## 5. Production Health Evaluation Forensic (§5)

No `evaluate_production_health()` function exists in `commerce/production_control.py` or `commerce/conversation_operations.py`. `derive_production_state()` exists but takes caller-supplied `risk_state/failure_class/is_paused/is_rollback/pressure_bucket` — caller must aggregate metrics first. No `record_metric` → `query_metrics` → `aggregate_rate` → `detect_regression` → `derive_production_state` loop in `workers/scheduler_worker.py` or `workers/llm_worker.py`. Search for `derive_production_state` hits only definition + 1 test, 0 production callers.

Required health inputs (§5) `success_rate/failure_rate/permanent/degraded/handoff/purchase/repeat/rejection/negative/strategy confidence/experiment performance/spam/pressure/creator anomalies` — all individually calculable via `query_metrics` per window but **no health evaluation layer** aggregates them.

**Verdict:** **MISSING — health evaluation pure/deterministic layer absent; existing `derive_production_state` is primitive, not health evaluation.**

---

## 6. Health States Forensic (§6) & Canary State Machine (§7) & Rollout Gates (§8)

Health states `NORMAL/CAUTION/DEGRADED/SUPPRESSED/HANDOFF/PAUSED/ROLLBACK/RECOVERING` defined (`ProductionState` 8) but transitions not deterministic per §6 (e.g., `NORMAL→CAUTION→DEGRADED→SUPPRESSED→ROLLBACK` with `reason_code/metric evidence/timestamp/scope`). Currently only `derive_production_state` single-step, no state machine with history.

Canary `0%→1%→5%→10%→25%→50%→100%` with `HOLD/ROLLBACK/PAUSE/RECOVER/ROLL_FORWARD` exists as `Rollout` `percentage∈{0,1,5,10,25,50,100}` + `status ACTIVE/ROLLED_BACK/COMPLETED` but **no deterministic advancement lifecycle** (e.g., `DISABLED→1%→5%` must pass health gates). `is_rollout_active_for` checks percentage but no `evaluate_rollout()` that checks `minimum sample size + observation window + error/negative/purchase/repeat/handoff/spam/pressure/strategy regression` before advancing. Rollout gates §8 **not implemented** — `should_rollback` exists but no `should_advance` gate.

**Verdict:** **PARTIAL — states defined, transitions/gates missing.**

---

## 7. Automatic Rollback Forensic (§9, §11, §12)

`should_rollback(sample_size, window, current, baseline)` + `perform_rollback(rollout_id, reason)` + `rollback_safety_check` exist and tested (34 tests H, O) — they are **bounded, auditable (record_metric rollout_rollback), idempotent (check_idempotent), scope-aware (`is_rollout_active_for` includes creator), fail-safe (if not found → not rollback).** However **no automatic invocation** from health evaluation — caller must invoke manually. No `minimum sample/window/threshold/severity` integration with `derive_production_state` loop.

Rollback safety: verified **does NOT delete** `fan memory (commercial_preferences_by_creator, long_term_memory_by_creator), purchase records (commerce_offers purchased, fangate_transactions), transactions, offers, audit, strategy evidence (filtered via regression_map not deleted), conversation history (messages), DLQ, dedup (md5 + ring)** — tested O. **WIRED as primitive, not autonomous.**

---

## 8. Emergency Control Integration Forensic (§10) & Fan/Creator Controls (§11, §12)

`EmergencyControlType` 6 + `set_emergency/clear_emergency/is_global_paused/is_creator_paused/is_strategy_paused/is_experiment_paused/is_reengagement_paused/autonomous_allowed` defined, tested (I, 6 tests). Precedence `GLOBAL→CREATOR→STRATEGY→EXPERIMENT→RE-ENGAGEMENT→COMMERCE` **matches spec** (`autonomous_allowed` checks in that order: global first, then creator, then strategy, then experiment). Emergency never deletes data, never ACKs incorrectly, never fabricates purchases (verified via `policy_allows` + `is_global_paused` fail-closed not yet enforced in generation path).

**Gaps:**
- Not connected to runtime: `workers/llm_worker.py` does **not** call `autonomous_allowed(creator_id, strategy, experiment_id)` before Qwen; `workers/scheduler_worker.py` does not check `is_global_paused()` before `process_due_messages` (only `is_reengagement_paused` partially).
- Fan-level protection §11: `is_strategy_paused` fan-scoped? Currently `is_strategy_paused(strategy, creator_id)` is creator+strategy, not `creator+user+strategy`. High spam risk `is_spam_risk` exists but not connected to `autonomous_allowed` per fan.
- Creator-level §12: `CREATOR_AUTONOMOUS_PAUSE` exists but no `commerce enabled/disabled, re-engagement enabled/disabled` per creator beyond `is_commerce_paused` / `is_reengagement_paused` (which do exist, but not wired to `commerce/product_selection` or `memory/context`).

**Verdict:** **PARTIAL — defined + tested, not enforced in production call graph.**

---

## 9. Strategy/Experiment Governance (§13, §14)

Strategy selection hierarchy `FAN_TOPIC>FAN>CREATOR_TOPIC>CREATOR>SAFE_DEFAULT` preserved via `strategy_governed_selection_compat()` which also checks `pressure bucket suppress, risk SUPPRESS/HANDOFF, regression_map`. However additional Phase 23 requirements (§13) `rollout active, creator allowed, fan allowed, pressure acceptable, fatigue acceptable, risk acceptable, regression not detected, experiment valid` — **rollout active** not checked in `strategy_governed_selection` (it checks pressure/risk/regression but not `is_rollout_active_for`). `is_strategy_paused` not checked.

Experiment governance: lifecycle `CONTROL/VARIANT/OBSERVE/EVALUATE/KEEP/DISABLE/ROLLBACK` not as state machine; `experiment_governed_assignment` in `production_control` checks `exposures>=5` before EXPERIMENT else CONTROL, but not `SWITCH` via rollout state. Stable assignment `SHA256(creator:user:experiment)` preserved (verified 58 hits), `experiment_safe_to_apply` forbids price/product/sales URL/purchase/DropFans/creator/safety/transaction identity — **reused**.

**Verdict:** **PARTIAL — governance defined, not rollout-aware.**

---

## 10. Metric Persistence / Retention (§15) & Audit (§16) & Trace (§17)

Metrics **in-memory only** (`_metric_events` bounded 5000, `_audit_log` 1000) — not persisted to `PostgreSQL/user_profiles/telemetry/Redis`. `commerce/production_control.py` Docstring says “Persistence via user_profiles JSONB + in-memory fallback” but **no `get_user_profile`/`update_user_profile` call in `record_metric`/`record_audit`** — verified 0 `update_user_profile` hits in `production_control.py`. Restart loses operational state (metrics, rollouts, emergency, audits) — **not restart-safe**. `adaptive_optimization` exposures do persist via `user_profiles` JSONB (`strategy_exposures_by_creator` 50/30d), but production metrics do not.

Audit records `OperationalAuditRecord` bounded 1000, queryable, creator-isolated, generation-scoped, idempotent via `check_idempotent` — but **not produced per autonomous operation** in `workers/llm_worker` (only `GenerationTelemetry` per generation, not `OperationalAuditRecord` per control transition).

Trace `<500 chars no PII no message content` **WIRED** via `build_operation_decision.trace_compact()` + `telemetry.decision_trace` — verified.

**Verdict:** **PARTIAL — audit/trace WIRED, metrics persistence MISSING.**

---

## 11. Failure Recovery (§18) & Scheduler Safety (§19) & Recovery After Pause (§20) & Dependency Health (§21)

Failure 4 mapped consistently via `classify_failure` (RETRYABLE: timeout/transport/stall/redis 5xx, PERMANENT: invalid peer, DEGRADED: memory/DropFans/telemetry/qwen/scoring/DB, HANDOFF_REQUIRED: operator required) — verified 12 patterns, but not consistently consumed across `send_worker` (own DLQ logic) and `llm_worker` (own try). `degraded_fallback` matrix 10 **WIRED** and exact per §15.

Redis `XREADGROUP/XAUTOCLAIM 30s/XACK/DLQ/dedup md5` preserved, no new queue — **WIRED**.

Scheduler respects `global pause`? No — `scheduler_worker` only checks `is_reengagement_paused` via govern check, not `is_global_paused()` before `claim_due_messages`. Aftercare/cooldown/rejection/frequency via `is_reengagement_governed_allowed` **WIRED**, but pressure/fatigue/rollout percentage/experiment state already added Phase 21 but not emergency global.

Recovery `PAUSED→RECOVERING→CAUTION→NORMAL` not implemented — only `derive_production_state(PAUSED)` → `PAUSED`, `RECOVERING` via `failure_class retryable` → `RECOVERING`, but no deterministic staged recovery (e.g., verify PG/Redis/DropFans/Telegram/Qwen/telemetry/rollout state before `RECOVERING→CAUTION`).

Dependency health checks only for existing deps `PG/Redis/DropFans/Telethon/Qwen` — `classify_failure` covers each, but no `dependency_health` aggregation via `record_metric` windows.

**Verdict:** **PARTIAL.**

---

## 12. Autonomous Control Loop (§22) & Second Decision Engine (§23)

No `evaluate_production_health()→derive_production_state()→evaluate_rollout()→evaluate_experiments()→evaluate_strategy_regressions()→apply_pause/rollback/roll-forward→record audit` function in `commerce/production_control.py` or `commerce/conversation_operations.py` — **MISSING**. Spec says NOT a new worker/queue/process/scheduler/Celery/Kafka — should integrate with existing `scheduler_worker` loop (already integrates re-engagement every interval). Currently `scheduler_worker` loop does `recover_stale→process_due_messages→reconcile_purchases→re-engagement governed` but **not** `evaluate_production_health→derive_production_state→evaluate_rollout` — verified 0 hits.

No second decision engine created — authoritative chain `signals→conversation state→memory→conversation intelligence→objective→NBA→pressure→fatigue→risk→production controls→strategy→response contract→Qwen→validator→send→outcome→adaptive learning` **preserved** (production controls govern whether allowed, not replace conversation intelligence/commerce authority/DropFans).

**Verdict:** **MISSING — control loop.**

---

## 13. Single-Pass & Security Forensic (§24, §28)

`verify_single_pass(calls)` asserts `extract_commerce_signals==1 && qwen==1 && scoring==1 && new_llm==0` — tested N. No additional LLM calls introduced (no `critic LLM`, `memory LLM`, etc. in any commerce file — verified no `get_llm_provider` in `commerce/production_control`/`conversation_operations`). Security: no Telegram sessions/tokens/API keys/DropFans secrets/message content/buyer credentials in `GenerationTelemetry` or `OperationalAuditRecord` (both only IDs/enums). Creator isolation verified per §21 via `creator_id` in every metric/rollout/audit.

**Verdict:** **WIRED.**

---

## 14. Root Cause, Missing Capability, Smallest Fix (Stage A Conclusion)

**ROOT CAUSE:**

Phase 22 delivered **control primitives** (metrics windows, rollout 0/1/5/10/25/50/100, emergency controls 6, audit 1000, ProductionState 8, idempotency) as **library functions** that are **importable and tested** but **not continuously orchestrated** — no periodic `evaluate_production_health()` → `derive_production_state()` → `evaluate_rollout()` → `evaluate_experiments()` → `evaluate_strategy_regressions()` → `apply_pause/rollback/roll-forward` → `record audit` loop integrated with existing `scheduler_worker` (or equivalent existing mechanism). Metrics are recorded only when tests call `record_metric`, not per generation in `workers/llm_worker`; rollout state is in-memory registry not persisted to `user_profiles` JSONB, so restart loses it; emergency `autonomous_allowed()` not called before Qwen; health gates not checked before advancing `1%→5%→10%`; variant attribution `metrics_by_dimension` exists but not aggregated per experiment outcome.

**MISSING OPERATIONAL CAPABILITY:**

Continuous deterministic **operational orchestration** that:

- Calls `record_metric` per generation (reuses existing telemetry path, no new DB)
- Aggregates `query_metrics` per window `1h/24h/7d/30d` with dimensions `creator/strategy/variant/outcome`
- Evaluates `derive_production_state(NORMAL→CAUTION→DEGRADED→SUPPRESSED→ROLLBACK)` with `reason_code/metric evidence/timestamp/scope`
- Evaluates canary `DISABLED→1%→5%→10%→25%→50%→100%` with gates `minimum sample + observation window + error/negative/purchase/repeat/handoff/spam/pressure/regression`
- Applies `should_rollback`/`perform_rollback` automatically when `confirmed` (not one event, minimum sample 5, severity `confirmed`)
- Applies `autonomous_allowed` before Qwen (GLOBAL→CREATOR→STRATEGY→EXPERIMENT→RE-ENGAGEMENT precedence)
- Records `OperationalAuditRecord` per control transition, bounded, creator-isolated

without new worker/queue, without second decision engine, without new LLM, without migration unless proven.

**SMALLEST CORRECT FIX:**

Create pure deterministic health + orchestration helpers in `commerce/production_control.py` (reuse `derive_production_state`, `should_rollback`, `is_rollout_active_for`):

1. Add `evaluate_production_health(creator_id, window, now) → HealthReport` (pure, aggregates `success_rate/failure_rate/permanent/degraded/handoff/purchase/repeat/rejection/negative/strategy confidence/experiment performance/spam/pressure` via `query_metrics` per window, never LLM).
2. Add `evaluate_rollout_gate(rollout, health, sample_size, observation_hours) → (advance/hold, reason_code)` checks `minimum sample 5, minimum window 1h, error<0.20, negative<0.30, purchase not degraded, handoff<0.10, spam risk, pressure bucket not suppress, strategy regression via `detect_regression`).
3. Add `orchestrate_production_controls(now) → AuditRecord[]` that **deterministically** does `for each active rollout: health = evaluate_production_health → production_state = derive_production_state → gate = evaluate_rollout_gate → if should_rollback → perform_rollback → else if gate advance → create_rollout(next percentage)` + `for each emergency condition: set_emergency if needed` + `record_audit`. Pure, bounded, idempotent via `check_idempotent(rollout_id+gate)`.
4. Integrate `orchestrate_production_controls` **into existing `workers/scheduler_worker.py` loop** (already runs every `SCHEDULER_POLL_INTERVAL` with `recover_stale→process_due_messages→reconcile_purchases→re-engagement governed`) — add **one best-effort call** `try: orchestrate_production_controls() except: log` **not a new worker/queue/process**.
5. Wire `record_metric` per generation in `workers/llm_worker.py` `telemetry.record` path (best-effort, after `GenerationTelemetry.complete`), and `record_audit` per `build_operation_decision` (generation_id/creator/user/objective/strategy/experiment/variant/risk/pressure/decision/outcome/production_state) — reuse existing `user_profiles` JSONB or in-memory bounded (no new DB).
6. Ensure `autonomous_allowed()` is called **before Qwen** in `llm_worker` (before `generate_draft`) to enforce `GLOBAL→CREATOR→STRATEGY→EXPERIMENT` precedence, fail-safe unknown→pause not needed (existing `is_global_paused` defaults not paused, but emergency set→pause).

Do NOT create second decision engine (production controls govern `whether`, not `what` objective/strategy — authoritative chain `signals→...→production controls→strategy→Qwen→validator→send` preserved). No new LLM, no new queue/worker, no migration unless persistence proves needed (prefer JSONB `rollouts_by_id` + `audit_by_creator` bounded).

---

## 15. What Must NOT Change (Per §30)

Redis Streams/consumer groups/XAUTOCLAIM/DLQ, PostgreSQL/Telethon/DropFans, 3 workers, scheduled_messages, memory, commerce authority, creator isolation, single-pass 1/1/1, LLM language-only — all **preserved** per forensic. No Kafka/RabbitMQ/Celery/another DB/ORM/second LLM/agent loop.

---

## 16. Forensic Verdict

```
PHASE 23 FORENSIC AUDIT COMPLETE

ROOT STATUS: CONDITIONALLY READY

METRIC COLLECTION: PARTIAL (in-memory not per-generation in prod)
METRIC AGGREGATION: PARTIAL (no windows in prod)
WINDOW 1h/24h/7d/30d: PARTIAL (defined not enforced)
CREATOR/FAN METRICS: PARTIAL (defined not per-generation persisted)
STRATEGY/EXPERIMENT/VARIANT METRICS: PARTIAL
REGRESSION DETECTION: PARTIAL (no minimum-sample in prod call)
CANARY 0-100%: PARTIAL (percentages defined, no deterministic advancement lifecycle)
ROLL-FORWARD: PARTIAL (enable_rollout defined, not autonomous)
ROLLBACK: PARTIAL (perform_rollback defined, not automatically invoked)
EMERGENCY PAUSE: PARTIAL (6 controls defined, not enforced before Qwen)
RECOVERY: PARTIAL (RECOVERING state defined, no staged PAUSED→RECOVERING→CAUTION→NORMAL)
AUDIT TRAIL: PARTIAL (record_audit defined, not per-operation in prod)
DECISION TRACE: WIRED
HANDOFF: PARTIAL (persisted but metrics not aggregated)
DEGRADED MODE: WIRED
RE-ENGAGEMENT GOVERNANCE: WIRED
STRATEGY GOVERNANCE: PARTIAL (not rollout-aware)
CREATOR ISOLATION: PRESERVED
FAN ISOLATION: PRESERVED
COMMERCE AUTHORITY: PRESERVED
DROP FANS: SOLE AUTHORITY
SINGLE-PASS: PRESERVED (1/1/1/0)
IDEMPOTENCY/REDIS/DLQ: WIRED

VIOLATIONS: NONE
ARCHITECTURE: NO REDESIGN
PRIMITIVES TESTED: YES
PRIMITIVES AUTONOMOUS: NO — central gap is continuous orchestration
```

Next: Stage B — smallest orchestration fix (health evaluation pure + rollout gates + autonomous control loop via existing scheduler, no new infrastructure).

