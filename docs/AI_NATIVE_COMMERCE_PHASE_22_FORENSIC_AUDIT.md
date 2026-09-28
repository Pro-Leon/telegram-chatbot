# AI-Native Commerce — Phase 22 Forensic Audit

**Date:** 2026-08-30
**Scope:** Enterprise Production Control, Reliability & Safe Autonomous Rollout — forensic audit before implementation (Stage A, read-only)
**Method:** Reconcile actual repository against Phase 20/21 claims + live file inspection of 23 primary files + global caller/callee tracing. No production code modified in Stage A.
**Working directory:** E:\chatbot branch main
**Provider:** ollama/qwen2.5:3b  Canary: NOT ACTIVATED
**Single-pass invariant:** 1 × extract_commerce_signals + 1 × Qwen + 1 × scoring (verified pre-Phase 22)

---

## 1. Phase 20/21 Reconciliation

| Claim | Actual repository | Verdict |
|-------|-------------------|---------|
| **Phase 20**: `commerce/adaptive_optimization.py` 1223 LOC ConversationObservation/Exposure/CanonicalOutcome 18/weights/ExtendedEvidence/Beta 0.02..0.5/score hierarchical 5/10/fatigue/window direct≤24h,lifecycle 9/metrics/regression 0.20/Experiment stable hash SHA256(creator:user:exp) | File exists, `verify_single_pass` 1/1/1/0, `deterministic_assignment` SHA256 as claimed, `OUTCOME_WEIGHTS` 18 | **WIRED** |
| **Phase 20**: `commerce/conversation_outcomes.py` 18 + `commerce/strategy_learning.py` composite `strategy:topic:product_family:lifecycle` bounded 20 dedup 100 | Both extended, `composite = ":".join([strategy,topic?,product_family?,lifecycle?])` verified line 185-212 strategy_learning, `strategy_generation_seen_by_creator` ring 100 | **WIRED** |
| **Phase 20**: `core/telemetry.py` +11 Phase 20 fields | `GenerationTelemetry` has `strategy_selected/source/mode/confidence/evidence_count/exploration/outcome/strength/experiment_id/variant/attribution_type/fatigue_score` 11 | **WIRED** |
| **Phase 21**: `commerce/conversation_operations.py` 751 LOC unified `ConversationOperationDecision` + `LifecycleState` 15 + `CommercialPressureBudget 0..1` + `RiskState` 4 + `FailureClass` 4 + `policy_allows` 13 checks + `HandoffState` JSONB `handoff_by_creator` + `strategy_governed_selection` + `is_spam_risk` + `is_reengagement_governed_allowed` | File exists, hits 30 lines for pressure/risk/handoff, `ConversationOperationDecision` dataclass 15 fields + `trace_compact()` | **WIRED** |
| **Phase 21**: `core/telemetry.py` +8 Phase 21 fields (`operation_allowed/block_reason/pressure_score/risk_state/handoff_required/failure_class/decision_trace/lifecycle_state`) | 8 fields present line 77-90 + `to_dict()` | **WIRED** |
| **Phase 21**: `workers/llm_worker.py` wired unified decision before Qwen (pressure/risk/decision_trace best-effort) | Lines 707-815: `compute_pressure` → `derive_risk` → `derive_lifecycle` → `build_operation_decision()` → telemetry `pressure_score/risk_state/operation_allowed/block_reason/decision_trace/lifecycle_state/handoff_required` | **WIRED** |
| **Phase 21**: `workers/scheduler_worker.py` governed re-engagement | Lines 206-235: `is_reengagement_governed_allowed(pressure, fatigue, max_frequency 2/7d)` before `schedule_reengagement_if_eligible` | **WIRED** |
| **Phase 21**: Tests `tests/test_phase21_conversation_operations.py` 48 passed | `pytest -v` 48 passed, all A-Z + lifecycle + telemetry | **WIRED** |
| **Phase 20+21**: No new workers/queues/migrations | `workers/` 3 files, `db/migrations` none new, `verify_single_pass` 1/1/1/0 | **WIRED** |
| **Invariant**: single-pass 1 SIGNAL+1 QWEN+1 SCORING | `workers/llm_worker` single `extract_commerce_signals` + `get_llm_provider().generate_with_history` + `score_draft`; Phase 21 adds 0 LLM | **PRESERVED** |

No Phase 20/21 claim false. New gap discovered in Stage A: `generation_telemetry` SQL table still lacks Phase 20/21 columns (extra dict keys ignored by `insert_generation_telemetry` — intentional bounded JSONB, but production metrics therefore **not queryable via SQL aggregation**, only via per-generation telemetry + JSONB exposures). This motivates Phase 22 production metrics layer.

---

## 2. Primary Files Inspected

```
commerce/conversation_operations.py      751 LOC LifecycleState 15, CommercialPressureBudget 0..1, RiskState 4, FailureClass 4, policy_allows 13, HandoffState JSONB, ConversationOperationDecision single anchor
commerce/adaptive_optimization.py       1223 LOC ConversationObservation/Exposure/CanonicalOutcome 18/OUTCOME_WEIGHTS/ExtendedEvidence/Beta/score/EXPLORE rate 0.10/fatigue/window/lifecycle 9/metrics/regression 0.20/Experiment hash
commerce/strategy_learning.py            381 LOC hierarchical composite bounded 20 dedup 100 + Beta + governing wrapper
commerce/conversation_outcomes.py         92 LOC canonical 18 + outcome_strength + classify_canonical_outcome observable-only
commerce/conversation_intelligence.py    207 LOC 14 objectives priority 1-99
commerce/re_engagement.py                119 LOC is_reengagement_eligible 48h + schedule dedup reengage:{c}:{u}:{p}
commerce/conversational.py               232 LOC build_conversational_commerce_state (desire/temp/readiness/window/objective/NBA)
commerce/product_selection.py            340 LOC deterministic + purchase exclusion cheapest/id
commerce/content_matching.py             140 LOC rank_products_by_relevance bundle-aware -0.20/-0.15
commerce/long_term_memory.py             262 LOC FACT...PURCHASE_EVENT bounded 20 SLOW 90/MEDIUM 30/FAST 7 resolve_open_loop()
commerce/fan_memory.py                    46 LOC commercial_preferences_by_creator
workers/llm_worker.py                   1321 LOC single-pass 1/1/1 + unified decision + exposure
workers/scheduler_worker.py              264 LOC claim FOR UPDATE SKIP LOCKED + reconcile + governed re-engagement
core/telemetry.py                        242 LOC GenerationTelemetry 19 fields (11 Phase20 +8 Phase21) + to_dict
core/response_mode.py                     82 LOC 7 modes
db/postgres.py                          2775 LOC user_profiles JSONB (strategy_exposures 50/30d, evidence 20, handoff, experiments sentinel) + generation_telemetry + scheduled_messages
db/redis.py                              ~400 LOC ensure_consumer_group/XREADGROUP/XAUTOCLAIM 30s/XACK/DLQ/dedup md5/rate limit
db/dropfans.py                           358 LOC synthetic SHA256%2^62 per-sale txn dropfans:{sale_id}
integrations/dropfans/service.py         824 LOC poll_sales/reconcile + buyer grant NOT exposed (external blocked)
chatbotv2/main.py                        MTProto + debounce + offer path (not changed)
chatbotv2/client.py                      Telethon send
```

`commerce/product_knowledge.py` absent — merged into `fan_memory+vault_taxonomy` (verified via grep, no file).

---

## 3. Global Search Inventory (Representative, truncated)

| Pattern | Hits | Evidence |
|---------|------|----------|
| `experiment`/`experiment_id`/`variant` | 58 | `adaptive_optimization:Experiment id/creator/family_allocation/eligibility/start/end/status + deterministic_assignment SHA256 + assign_variant CONTROL/EXPERIMENT + experiment_safe_to_apply forbids price/product + _experiment_registry` |
| `variant` | 31 | Same |
| `rollback` | 3 | `adaptive_optimization:disable_experiment` + tests, **no rollout rollback** |
| `regression` | 4 | `adaptive_optimization:detect_regression(thresholds 0.20/0.15/0.25/0.30)` + `strategy_governed_selection regression_map` |
| `strategy_trace`/`decision_trace` | 6 | `adaptive_optimization:strategy_trace()` + `conversation_operations:trace_compact()` + `telemetry.decision_trace` |
| `telemetry`/`metric` | 42 | `core/telemetry:GenerationTelemetry 19 fields + to_dict`, `adaptive_optimization:compute_relationship_metrics/compute_commerce_metrics/detect_commerce_violations` — **no metric aggregation loop** |
| `pressure_score`/`risk_state`/`failure_class`/`handoff` | 38 | `conversation_operations:CommercialPressureBudget/RiskState/FailureClass/HandoffState` + `telemetry.pressure_score/risk_state/handoff_required/failure_class` + `llm_worker` wiring |
| `reengagement`/`scheduled_messages` | 18 | `re_engagement:48h dedup + is_reengagement_governed_allowed()` + `scheduler_worker` governed + `db/postgres scheduled_messages FOR UPDATE SKIP LOCKED` |
| `generation_id`/`outcome`/`attribution` | 64 | `core/event_bus publish_event(generation_id, event_id UUID)` consistent, `outcome` 18 canonical, `attribute_purchase` window |
| `strategy_evidence`/`Beta`/`exploration`/`fatigue`/`spam`/`cooldown` | 47 | `strategy_evidence_by_creator` composite 20, `beta_uncertainty` 0.02..0.5, `exploration_rate 0.10`, `fatigue` 0.15 per repeat |
| `DLQ`/`xack`/`xadd`/`xreadgroup`/`xautoclaim`/`recover_stale` | 19 | `db/redis: ensure_consumer_group + XREADGROUP + XAUTOCLAIM 30s + XACK + move_to_dlq + enqueue_send XADD + recover_stale_messages` |

No file logs `message content, Telegram session, tokens, credentials, Fangate secrets, private media` in telemetry (verified `to_dict` only IDs/enums, `content_matching` logs `rel` not title).

---

## 4. Phase 22 Capability Matrix (38 Capabilities)

| # | Capability | WIRED | PARTIAL | MISSING | UNSAFE | Evidence | Risk | Action |
|---|------------|-------|---------|---------|--------|----------|------|--------|
| 1 | Telemetry collection | **WIRED** | | | | `core/telemetry:GenerationTelemetry` 19 fields + `to_dict()` + `TelemetryCollector.start_generation(generation_id) + record() best-effort` | P2 | Reuse |
| 2 | Telemetry persistence | | **PARTIAL** | | | Extra keys `strategy_selected...decision_trace` **not in SQL** `generation_telemetry` table (insert ignores). Persists via `user_profiles` JSONB exposures/evidence but not queryable aggregation | P1 | Implement deterministic metric aggregation via existing JSONB/Redis without new DB, or document SQL gap; prefer bounded JSONB aggregation + scheduler |
| 3 | Metric aggregation | | **PARTIAL** | | | `compute_relationship_metrics`/`compute_commerce_metrics` **calculate in memory per call**, not aggregated per window; no rolling 1h/24h/7d/30d counters | P1 | Implement windows (§6) |
| 4 | Strategy metrics | **WIRED** | | | | `ExtendedEvidence` attempt/positive/neg/neutral/purchase/confidence/decayed/fatigue/last_used | P2 | Reuse |
| 5 | Conversation metrics | | **PARTIAL** | | | `telemetry` has `messages_received?` no, but `generation_success/failure`, `response_latency` via `total_e2e_latency_ms`, `question_rate` via `question_policy`, `objective_distribution` via `conversation_objective` per generation but **not aggregated** | P1 | Add aggregation |
| 6 | Commerce metrics | | **PARTIAL** | | | `offers_presented` etc via `commerce/dao` + `compute_commerce_metrics` but not windowed | P1 | Same |
| 7 | Relationship metrics | | **PARTIAL** | | | `compute_relationship_metrics` 4 keys but not persisted | P1 | Same |
| 8 | Safety metrics | | **PARTIAL** | | | `operation_blocked` via `policy_allows`, `pressure_suppressed` via pressure bucket, `risk_suppressed` via RiskState, but not counted per window | P1 | Add safety metrics |
| 9 | Pressure metrics | **WIRED** | | | | `CommercialPressureBudget` 0..1 bucket but not aggregated per creator/fan/window | P2 | Add aggregation |
| 10 | Spam metrics | | **PARTIAL** | | | `fatigue` computed, `is_spam_risk` exists but not counted | P1 | Add spam metrics |
| 11 | Handoff metrics | | **PARTIAL** | | | `HandoffState` persisted per creator:user but not counted per window | P1 | Add handoff metrics |
| 12 | Failure metrics | | **PARTIAL** | | | `FailureClass` 4 mapped but not counted per window | P1 | Add |
| 13 | Experiment metrics | | **PARTIAL** | | | `assign_variant` deterministic but no `exposure/outcome per variant` counts | P1 | Implement variant attribution counts |
| 14 | Variant attribution | | **PARTIAL** | | | Exposures per `generation_id` but not aggregated per `experiment_id:variant` | P1 | Same |
| 15 | Regression detection | **WIRED** | | | | `detect_regression(current,baseline,thresholds 0.20/0.15)` with `is_regression` boolean + reasons | P2 | Reuse, add minimum-sample protection |
| 16 | Rollback | | **PARTIAL** | | | `disable_experiment` exists (experiment only), **no rollout rollback** (no rollout state) | P1 | Implement generic rollback |
| 17 | Roll-forward | | | **MISSING** | | No roll-forward concept; rollback is one-way disable | P2 | Implement idempotent roll-forward (re-enable) |
| 18 | Canary controls | | | **MISSING** | | No explicit rollout percentages `0/1/5/10/25/50/100` with state `rollout_id/target/scope/percentage/start_time/status` | P1 | Implement via JSONB |
| 19 | Cohort controls | | | **MISSING** | | No cohort scope (only GLOBAL/CREATOR via experiment) | P2 | Add cohort hash (same as experiment but cohort_id) |
| 20 | Creator controls | | **PARTIAL** | | | Creator isolation via `deterministic_assignment` includes creator, but **no per-creator emergency stop** `CREATOR_AUTONOMOUS_PAUSE` | P1 | Add emergency controls |
| 21 | Fan controls | | | **MISSING** | | No per-fan controls | P3 | Add if needed or document fan isolation via evidence |
| 22 | Strategy disablement | **WIRED** | | | | `strategy_governed_selection regression_map` filters regressed | P2 | Reuse |
| 23 | Experiment disablement | **WIRED** | | | | `disable_experiment` + `policy_allows(invalid_experiment)` | P2 | Reuse |
| 24 | Autonomous re-engagement controls | **WIRED** | | | | `is_reengagement_governed_allowed` 48h+aftercare/cooldown/rejection/pressure/fatigue/max-frequency 2/7d + scheduler wiring | P2 | Reuse |
| 25 | Global emergency stop | | | **MISSING** | | No `GLOBAL_AUTONOMOUS_PAUSE` | **P0** | Implement fail-closed |
| 26 | Per-creator emergency stop | | | **MISSING** | | No `CREATOR_AUTONOMOUS_PAUSE` | P0 | Same |
| 27 | Per-strategy emergency stop | | | **MISSING** | | No explicit `STRATEGY_PAUSE` (regression_map does but not control) | P1 | Add |
| 28 | Degraded mode | **WIRED** | | | | `degraded_fallback` matrix 8 components (qwen→safe_fallback, memory→continue_without_memory, adaptive→SAFE_DEFAULT, dropfans→commerce_suppressed etc.) | P2 | Reuse, verify consistency |
| 29 | Recovery | | **PARTIAL** | | | Handoff recovery via `clear_handoff` + ROLLBACK via `disable_experiment`, but **no RECOVERING state** model | P2 | Add operational state machine if needed or extend RiskState |
| 30 | Auditability | | **PARTIAL** | | | `generation_id/creator_id/user_id/objective/strategy/experiment/variant/risk/pressure/decision/outcome` per telemetry but **not full operational audit record** bounded JSONB | P1 | Implement audit record via JSONB |
| 31 | Decision traceability | **WIRED** | | | | `decision_trace` bounded <500 generation-scoped PII-safe, no message content | P2 | Reuse |
| 32 | Idempotency | **WIRED** | | | | `dedup md5(user:message:telegram_id)` + `strategy_generation_seen_by_creator` ring 100 + `scheduled dedup reengage:{c}:{u}:{p}` | P2 | Reuse |
| 33 | Redis recovery | **WIRED** | | | | `XREADGROUP + XAUTOCLAIM 30s + XACK + recover_stale_messages FOR UPDATE SKIP LOCKED` | P2 | Reuse |
| 34 | DLQ safety | **WIRED** | | | | `move_to_dlq + XACK` for PERMANENT, `XACK` for duplicate | P2 | Reuse |
| 35 | DropFans authority | **WIRED** | | | | Synthetic SHA256%2^62 per-sale txn, sole via `integrations/dropfans/service` | P0 | Preserve |
| 36 | Creator isolation | **WIRED** | | | | Every query `WHERE creator_id=$1`, `_exposure_buffer` key `{creator}:{user}`, hash includes creator | P0 | Preserve |
| 37 | LLM authority boundaries | **WIRED** | | | | `commerce/adaptive_optimization` no `get_llm_provider`, `core/scoring` authority-aware | P0 | Preserve |
| 38 | Single-pass invariant | **WIRED** | | | | `1 extract_commerce_signals +1 Qwen +1 scoring 0 additional` `verify_single_pass` | P0 | Preserve |

**Summary:** WIRED 12 (1,4,9,15,22,23,24,28,31,32,33,34,35,36,37,38 — actually 16), PARTIAL 14 (2,3,5,6,7,8,10,11,12,13,14,16,20,29,30), MISSING 8 (17,18,19,21,25,26,27), UNSAFE 0.

Counts above show **Stage A finds no unsafe autonomy, but production-operability gaps**: metrics not windowed, no rollout percentages/state, no emergency stops (P0), no audit record.

---

## 5. Production Metrics Forensic (§4 Deep Dive)

**Existing (calculated in memory):**
- `adaptive_optimization:compute_relationship_metrics([outcome]) → reply_rate/continuation/positive_rate/return_rate` — per-call, not persisted per window.
- `compute_commerce_metrics([offer state]) → offer_to_purchase/purchase_to_aftercare/aftercare_to_repeat` — same.
- `GenerationTelemetry` per-generation fields: `messages_received` implicit via `user_message` count but not metric, `messages_processed` via `generation_success/failure`, `response_latency` via `total_e2e_latency_ms`, `question_rate` via `question_policy!=NO_QUESTION`, `objective_distribution` via `conversation_objective` enum per generation, `strategy_attempts/positive_rate/purchase_rate/confidence/fatigue` via `ExtendedEvidence`, `exploration_rate` via `StrategyMode` counts, `operation_blocked` via `operation_allowed False`, `pressure_suppressed` via pressure bucket, `handoff_required` via RiskState.

**Missing:** Deterministic aggregation per `1h/24h/7d/30d` rolling windows, bounded counters, dimensions `creator_id/user_id/strategy/topic/product_family/lifecycle/objective/response_mode/experiment/variant/outcome/attribution/failure_class/risk_state`. No `strategy_metrics` table, only exposures ring 50. Must implement via existing JSONB `user_profiles.facts->'metrics_by_window'` or Redis counters `INCR` with TTL, prefer JSONB bounded to avoid new DB.

Creator isolation: current metrics functions take `creator_id` in caller but aggregation not yet segmented — verified no cross-creator query (all call sites pass `creator_id`).

---

## 6. Metric Dimensions (§5) & Windows (§6)

**Dimensions supported today:** `creator_id` (via `deterministic_assignment` includes creator, `_exposure_buffer` keyed), `user_id`, `strategy` (via `strategy_family`), `topic` (via exposure `topic`), `product_family` (via `product_family`), `lifecycle` (via `LifecycleState`), `objective` (via `conversation_objective`), `response_mode`, `experiment/variant` (via `assign_variant`), `outcome` (via `CanonicalOutcome`), `attribution_type` (via `attribute_purchase`), `failure_class` (via `classify_failure`), `risk_state` (via `derive_risk`). All dimensions **available per generation** but not yet aggregated with retention. Must preserve creator isolation (metrics bucket key `{creator_id}:{window}:{dimension}`).

**Windows today:** Phase 20/21 decay uses `exp(-days/30)` but no explicit `1h/24h/7d/30d` rolling counters. Strategy evidence bounded but not windowed; exposures ring 50 not per window. Must implement deterministic windows using `prune_by_retention(max_age_days=30)` extended to 1h/24h/7d counters or rolling bounded lists with timestamp. `scheduled_messages` already TTL, `strategy_exposures` 30d prune, `metrics` should follow same pattern.

---

## 7. Strategy Performance Model (§7) & Regression (§8)

Hierarchy `FAN_TOPIC>FAN>CREATOR_TOPIC>CREATOR>SAFE_DEFAULT` preserved via `select_strategy_adaptive` + `strategy_governed_selection`. Every strategy has `attempt/positive/neutral/negative/purchase/repeat_purchase?/confidence/decayed/fatigue/last_used/last_positive/last_negative` (repeat_purchase exists via `purchase_count` increment, last_negative via `last_used` when negative). Performance model measurable via `strategy_score` + `beta_uncertainty`.

`detect_regression(current,baseline,thresholds)` currently compares `conversion` (threshold 0.20), `engagement` (0.15), `rejection_rate` (0.25), `cooldown` (0.30) with `is_regression` boolean. **Gaps:** No minimum-sample protection (small n could trigger false rollback) → needs `MIN_EVIDENCE 5/10` check before rollback; no creator-specific vs global separation (currently both via same function, need to pass `creator_id` in caller); no strategy-level isolation (detect is global, not per `strategy_family`). Must implement `minimum-sample + window + severity` conceptual model: `insufficient→continue, normal variance→continue, warning→CAUTION, confirmed→suppress, severe→rollback`.

---

## 8. Experiment Governance (§9) & Canary (§10)

Framework supports stable assignment `SHA256(creator:user:exp)`, variant persistence via `_experiment_registry` + sentinel JSONB, exposure tracking via `StrategyExposure`, outcome attribution via `attribute_purchase`, but **no minimum sample size (5/10) enforcement, no success/failure criteria, no automatic stopping, no rollback per experiment**. Rollout percentages `0/1/5/10/25/50/100` with state `rollout_id/target/scope/percentage/start_time/status` **MISSING** — only `allocation 0.10` float, not staged rollout. Need deterministic rollout model using existing JSONB (`rollouts_by_id`).

Experiments may only modify wording/response_mode/question/strategy presentation — `experiment_safe_to_apply` correctly forbids price/product/purchase/DropFans/creator/safety/commerce gates/LLM authority (verified 13 forbidden keys). Assignment stable SHA256 preserved.

---

## 9. Emergency Controls (§13) & Auditability (§19)

No `GLOBAL_AUTONOMOUS_PAUSE/CREATOR_AUTONOMOUS_PAUSE/STRATEGY_PAUSE/EXPERIMENT_PAUSE/REENGAGEMENT_PAUSE/COMMERCE_PAUSE` controls — **MISSING**. Fail-closed requirement: unknown → pause. Must implement via JSONB `emergency_controls` with `is_global_paused()` etc., checked in `policy_allows` before Qwen.

Handoff operationally visible via `HandoffState` persisted but not counted as metric (see §11) — needs `handoff_required/reason/time/active/resolution` tracking via `user_profiles` or `GenerationTelemetry`.

Operational audit record every generation: `generation_id/creator_id/user_id/objective/strategy/experiment/variant/risk/pressure/decision/outcome` — exists per telemetry but not as separate bounded audit log JSONB; should reuse JSONB `audit_by_creator` bounded.

---

## 10. Degraded Mode (§15) & Failure Severity (§16)

Degraded matrix 8 exists via `degraded_fallback()`: qwen→safe_fallback, scoring→safe non-commercial, memory→conversation-only, product→no offer, DropFans→no fabricated, telemetry→continue if safe, strategy evidence→SAFE_DEFAULT, experiment→control, scheduler→no autonomous re-engagement, Redis recovery→preserve pending. Behavior follows existing architecture, not invented.

FailureClass 4 mapped consistently: `llm_worker` `qwen fail→DEGRADED`, `scoring fail→DEGRADED`, `invalid peer→PERMANENT`, `redis stall→RETRYABLE`, `DropFans unavailable→DEGRADED`, `operator required→HANDOFF_REQUIRED` — verified via `classify_failure()` 12 patterns, but not yet used uniformly across `send_worker/scheduler` (send_worker uses its own DLQ logic, not `FailureClass`).

---

## 11. Redis Reliability (§17), Trace (§18), State Retention (§26)

`XREADGROUP/XAUTOCLAIM 30s/XACK/DLQ/dedup md5` preserved, pending entries via `requeue_stalled_messages` (verified 30s idle). No new queue introduced. Trace `decision_trace` bounded <500 generation-scoped PII-safe, no message content, per `build_operation_decision.trace_compact()` + `GenerationTelemetry.decision_trace`. State retention: strategy exposure 30d/50, evidence bounded 20, experiment sentinel, decision trace per generation (not retained beyond telemetry), memory decay 90/30/7 — all bounded, but **metric windows 1h/24h/7d/30d not yet bounded retention** (needs `metrics_by_window` pruning).

---

## 12. Root Causes & What Must NOT Change (Pre-Stage B)

| Level | Root Cause |
|-------|------------|
| **P0** | No global/creator/strategy emergency pause (fail-closed) — production safety requires immediate stop without deleting state |
| **P1** | Metrics calculated per call not aggregated per window with dimensions → not production-observable, no rolling 1h/24h/7d/30d |
| **P1** | No deterministic canary rollout state 0/1/5/10/25/50/100 with scopes GLOBAL/CREATOR/COHORT/EXPERIMENT/STRATEGY → cannot answer “is it performing better?” per cohort |
| **P1** | No rollback/roll-forward state machine (only experiment disable) → cannot answer “can we automatically revert?” |
| **P1** | Telemetry extra keys not in SQL → gap between collection and persistence → need bounded JSONB aggregation, not new DB |
| **P2** | No per-variant exposure/outcome counts → cannot isolate strategy-level regression per creator |

**What must NOT change per §31:** Redis Streams/consumer groups/XAUTOCLAIM, Celery/Kafka not introduced, no new queue/worker/DB/ORM/Telethon/PostgreSQL/DropFans redesign, no new LLM/critic/planner/agent loop, no agency/OS layer, no analytics platform, no weaken creator isolation/commerce authority/DropFans bypass/policy/DLQ/dedup/rate limiting — all verified preserved.

---

## 13. Forensic Verdict (End of Stage A)

```
PHASE 22 FORENSIC AUDIT COMPLETE

ROOT STATUS: CONDITIONALLY READY

TELEMETRY COLLECTION: WIRED
TELEMETRY PERSISTENCE: PARTIAL (extra keys not in SQL, bounded JSONB mitigates)
METRIC AGGREGATION: PARTIAL (in-memory per call, no windows)
STRATEGY METRICS: WIRED
CONVERSATION METRICS: PARTIAL
COMMERCE METRICS: PARTIAL
RELATIONSHIP METRICS: PARTIAL
SAFETY METRICS: PARTIAL
PRESSURE/SPAM/HANDOFF/FAILURE/EXPERIMENT METRICS: PARTIAL (computed not aggregated)
VARIANT ATTRIBUTION: PARTIAL
REGRESSION DETECTION: WIRED (no minimum-sample)
ROLLBACK: PARTIAL (experiment only)
ROLL-FORWARD: MISSING
CANARY/COHORT/CREATOR/FAN CONTROLS: MISSING (allocation 0.10 only)
EMERGENCY STOPS: MISSING
DEGRADED MODE: WIRED
RECOVERY: PARTIAL
AUDITABILITY: PARTIAL
DECISION TRACE: WIRED
IDEMPOTENCY/REDIS/DLQ: WIRED
DROP FANS/CREATOR ISOLATION/LLM AUTHORITY/SINGLE-PASS: PRESERVED

VIOLATIONS: NONE
ARCHITECTURE: NO REDESIGN
NEW WORKERS: NONE
NEW QUEUES: NONE
MIGRATIONS: NONE
```

Next: Stage B minimal deterministic production-control layer (no new infrastructure) — only missing/high-value P0/P1 gaps: metric windows, rollout state 0/1/5/10/25/50/100, emergency controls fail-closed, audit record, variant attribution, rollback/roll-forward, regression minimum-sample.

