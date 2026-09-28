# AI_NATIVE_COMMERCE_PHASE_26_IMPLEMENTATION_MAP.md
# Phase 26 — Implementation Map (Stage B)
# Date: 2026-08-30

## 1. Actual Execution Graph (Phase 26 layer above existing)

```
Telegram inbound (Telethon)
  ↓ debounce_enqueue → XADD inbound_messages
  ↓ XREADGROUP llm_workers (XAUTOCLAIM 60s) → acquire_user_lock 30s
  ↓ build_qwen3_context (conversation_state, LTM 3, AVAILABLE CONTENT relevance, commerce_text)
  ↓ SINGLE extract_commerce_signals (cheap_model 1) → _try_commerce_draft OR conversational bridge
  ↓ conversation_intelligence (14 objectives, priority) → next_best_action
  ↓ conversation_operations (pressure 0..1 bucket, Risk SAFE/CAUTION/SUPPRESS/HANDOFF, Lifecycle 15, policy_allows, HandoffState, strategy_governed, is_spam_risk, ConversationOperationDecision single anchor, trace <500)
  ↓ production_control (MetricWindow 1h/24h/7d/30d, aggregate_count/rate, is_rollout_active_for SHA256, evaluate_rollout_gate sample≥5 window≥1h, should_rollback, perform_rollback, autonomous_allowed global→creator→strategy→experiment→commerce→reengagement, derive_production_state 8, orchestrate_production_controls per 10s, audit 1000, idempotent 2000, persist sentinels -999999/-999998/-999997, restart-safe)
  ↓ strategy selection (select_strategy_adaptive FAN_TOPIC>FAN>CREATOR_TOPIC>CREATOR>SAFE, Beta 0.02-0.5, fatigue 0.15, exploration 10%)
  ↓ response contract → Qwen 1 (generate_draft OR generate_commerce_response, pre-gate saves Qwen when paused) → scoring 1 (authority-aware, 0.1 cap, 0.0 fail-closed)
  ↓ send (enqueue_send dedup md5 → bot_main _process_send_stream: rate limit Lua 5 burst, blacklist, XAUTOCLAIM 30s, DLQ+XACK, vault reserve)
  ↓ outcome (classify_canonical_outcome 18, outcome_strength, attribute_purchase direct≤24h/assisted≤7d with DropFans)
  ↓ strategy learning (ExtendedEvidence, decay 30d, composite key 20, dedup 100)
  ↓ production metrics (record_metric per gen, audit)
  ↓ revenue intelligence (CanonicalEvent 15 fields UNKNOWN fallback, FunnelState 8+6, FunnelTransition bounded 20 JSONB, compute_conversion_metrics per window/dimension, compute_relationship_health 8 + vs commerce safety, time_bucket 4, strategy/product/topic/objective/response-mode/experiment/baseline, fan journey bounded 20, creator_intelligence isolated, fan_segment 10, fan_value LTV UNKNOWN if no amounts, enrich_telemetry 7 fields)
  ↓ **operational intelligence (NEW, pure, stateless, deterministic)**
      analyze_operational_state (relationship_health, commercial_intent, fatigue, rejection/handoff/spam/open_loops/baseline/current/product_family/response_mode/strategy) → list[OperationalDiagnosis] (signal, priority 1-13, reason_code, confidence via Beta, evidence, scope creator:fan, window)
      ↓ recommendation_for_diagnosis (signal → action via _SIGNAL_TO_ACTION: RELATIONSHIP_COMMERCE_MISMATCH→PRIORITIZE_RELATIONSHIP, STRATEGY_REGRESSION→SUPPRESS/ROLLBACK_EXPERIMENT, RISING_REJECTION→REDUCE_PRESSURE, FATIGUE→ROTATE_STRATEGY/TOPIC, HANDOFF_SPIKE→HANDOFF, SPAM→SUPPRESS_REENGAGEMENT, OPEN_LOOP_STAGNATION→FOLLOW_UP_OPEN_LOOP, CONVERSION_DECLINE→EXPLORE, PRODUCT_FAMILY→SUPPRESS_PRODUCT_FAMILY, RESPONSE_MODE→PAUSE_EXPERIMENT, INSUFFICIENT→OBSERVE, HEALTHY→NO_ACTION)
          → allowed via _is_recommendation_allowed (is_global_paused→autonomous_allowed→optimization_allowed→production_state suppressed/handoff/rollback → block unless OBSERVE/NO_ACTION, fail-closed if production control unavailable)
          → trace compact <500, no content/secrets/PII
      ↓ recommend_from_diagnoses (dedup, sort by priority)
      ↓ operational_decision (diagnoses + recommendations + production_state via evaluate_production_health)
  ↓ production-control authorization (autonomous_allowed, optimization_allowed, evaluate_rollout_gate, should_rollback, strategy/experiment governance) → existing autonomous behavior (NO second decision engine)
```

New intelligence never overrides: `ConversationObjective`/`next_best_action` (conversation intelligence), `ConversationOperationDecision`/`ProductionState` (conversation operations / production control) remain authoritative. Operational intelligence is **observational + recommendation**, not authority.

## 2. New Module(s)

| Module | LOC | Purpose | Calls Existing | Pure? | LLM? | New Worker/Queue? |
|---|---|---|---|---|---|---|
| `commerce/operational_intelligence.py` | 580 | Operational signals → diagnosis → recommendation → decision, deterministic priority 1-13, confidence via Beta, evidence, scope, allowed, trace, idempotent generation_id, creator/fan isolated, production-governed | `adaptive_optimization.beta_uncertainty`, `production_control.autonomous_allowed/is_global_paused/evaluate_production_health/derive_production_state`, `revenue_intelligence.optimization_allowed` | YES pure, no DB/Redis/network | 0 | 0 |

## 3. Existing Modules Reused (no redesign)

- `commerce/production_control.py` — `MetricWindow`, `record_metric`/`query_metrics`/`aggregate_count`/`aggregate_rate`/`metrics_by_dimension`, `HealthReport`, `derive_production_state`, `Rollout`/`is_rollout_active_for`/`_next_canary`, `should_rollback`/`perform_rollback`, `EmergencyControlType`/`autonomous_allowed`/`is_*_paused`, `OperationalAuditRecord`, `evaluate_production_health`/`evaluate_rollout_gate`/`orchestrate_production_controls`
- `commerce/conversation_operations.py` — `LifecycleState`, `compute_pressure`, `derive_risk`, `classify_failure`/`degraded_fallback`, `policy_allows`, `ConversationOperationDecision`, `is_reengagement_governed_allowed`, `is_spam_risk`, `get_handoff_memory`
- `commerce/conversation_intelligence.py` — `ConversationObjective` 14, `derive_conversation_objective` priority map
- `commerce/adaptive_optimization.py` — `CanonicalOutcome`, `ExtendedEvidence`, `beta_uncertainty`/`strategy_score`, `select_strategy_adaptive`, `compute_fatigue`, `attribute_purchase`, `Experiment`/`deterministic_assignment`, `detect_regression`
- `commerce/revenue_intelligence.py` — `CanonicalEvent`, `FunnelState`, `compute_conversion_metrics`, `compute_relationship_health`, `relationship_vs_commerce_safety`, `TimeBucket`, `strategy_performance_for_dimension`, `product_family/topic/objective/response_mode` metrics, `experiment_intelligence`, `baseline_comparison`, `optimization_quality`, `fan journey`, `creator_intelligence`, `fan_segment`, `optimization_allowed`
- `commerce/strategy_learning.py` — `ExtendedEvidence` persistence via `user_profiles`
- `core/telemetry.py` — `GenerationTelemetry` extended 7 Phase 25/26 fields, `insert_generation_telemetry` best-effort
- `workers/llm_worker.py` / `scheduler_worker.py` — unchanged single-pass 1/1/1/0, pre/post-gate production control, XAUTOCLAIM, DLQ, dedup
- `db/postgres.py`, `db/redis.py`, `db/dropfans.py` — existing persistence, streams, DropFans authority

## 4. Recommendation Flow

```
OperationalSignal detection (pure threshold + sample≥5 else INSUFFICIENT_DATA)
  → OperationalDiagnosis (signal, priority 1-13 per _PRIORITY_FOR_SIGNAL, reason_code, confidence 1-unc via Beta, evidence {sample_size, baseline, current, delta, rate, fatigue...}, scope creator:fan, window)
  → recommendation_for_diagnosis: signal → OperationalAction via _SIGNAL_TO_ACTION (with fatigue→ROTATE_TOPIC if topic present, strategy regression confidence>0.75 & sample≥20 → ROLLBACK_EXPERIMENT else SUPPRESS_STRATEGY, etc.) + _is_recommendation_allowed (is_global_paused → autonomous_allowed → optimization_allowed → production_state suppressed/handoff/rollback → block unless OBSERVE)
  → OperationalRecommendation (recommendation, priority, reason_code, confidence, evidence, scope, allowed, blocking_reason, source_metrics, creator_id, user_id, generation_id, trace<500)
  → recommend_from_diagnoses (dedup action:scope, sort priority)
  → operational_decision (diagnoses sorted priority + recommendations sorted priority + production_state via evaluate_production_health)
```

Example trace: `signal=REJECTION_RATE_HIGH action=REDUCE_PRESSURE priority=7 conf=0.82 allowed=true sample_size=47 rejection_rate=0.34 baseline=0.18`

## 5. Authority Boundaries

| Authority | Remains | Operational Intelligence Relation |
|---|---|---|
| SAFETY (human, platform) | `conversation_intelligence` HUMAN_HANDOFF priority 1, `policy_allows` before Qwen | Priority 1 SAFETY always wins, recommendation blocked if safety |
| CREATOR ISOLATION | `WHERE creator_id=$1`, `f"{creator}:{user}"` keys, `deterministic_assignment` includes creator | All diagnoses scoped `creator:X` or `creator:X:fan:Y`, never inherits other creator |
| HANDOFF | `make_handoff` `handoff_by_creator` JSONB, risk HANDOFF | Priority 4 HANDOFF, detection `handoff_spike>0.10` → HANDOFF, allowed checked via is_global_paused/autonomous_allowed |
| AFTERCARE | `aftercare_status` pending/sent → suppress offer | Priority 8 AFTERCARE/COOLDOWN, recommendation blocked if production_state SUPPRESSED |
| OBJECTION | `has_objection` → HANDLE_OBJECTION | Priority 7-8, strategy regression not override |
| OPEN_LOOP | `retrieve_relevant_memories` importance≥0.7, `resolve_open_loop` | Priority 9 OPEN_LOOP, `FOLLOW_UP_OPEN_LOOP` recommended but respects cooldown/aftercare via production control |
| DIRECT FAN INTENT | `explicit_purchase_request` → PRESENT_OFFER priority 5 | Not overridden by optimization |
| COMMERCE AUTHORITY | `price/product/URL` from authoritative state, `is_authorized_commerce` | Operational only recommends `EXPLORE`/`SUPPRESS_PRODUCT_FAMILY`, never invents price |
| PRODUCTION CONTROL | `autonomous_allowed`, `optimization_allowed`, `evaluate_rollout_gate`, `should_rollback` | Recommendation `allowed` via these, fails closed if unavailable |
| OPTIMIZATION | `select_strategy_adaptive` Beta/fatigue/budget | Operational recommends `SUPPRESS_STRATEGY`/`ROTATE`/`EXPLORE` via strategy governance, not direct mutate |
| LLM LANGUAGE | `generate_draft` single | 0 new LLM calls |

If recommendation conflicts with upstream authority: **BLOCK IT** via `allowed=False, blocking_reason`.

## 6. Production-Control Integration

`OperationalIntelligence → Recommendation → ProductionControl.authorize/block → existing action`

```
OperationalDiagnosis
  → recommendation_for_diagnosis
    → _is_recommendation_allowed:
        is_global_paused? → false global_pause
        is_creator_paused? → false creator_pause
        autonomous_allowed(strategy/experiment)? → false strategy_pause/experiment_pause
        optimization_allowed(creator/strategy)? → false commerce_pause / production_state suppressed/handoff/rollback
        evaluate_production_health → suppressed/handoff/rollback → block aggressive (allow OBSERVE only)
        else → true allowed
```

Uses existing `autonomous_allowed`, `is_*_paused`, `optimization_allowed`, `evaluate_production_health`/`derive_production_state`, `should_rollback`, `perform_rollback`, `strategy governance`, `experiment governance`. Never directly disables strategy/experiment/list.

## 7. Persistence / Retention

Operational intelligence is **stateless pure** — no new DB table, no new JSONB key, no unbounded list. Input metrics come from `production_control._metric_events` bounded 5000 + sentinel `-999997` 200 (load 50), `strategy_exposures` 50/30d, `funnel_journey` 20. Output diagnoses/recommendations are per-call, not stored (caller may audit via `record_audit` if needed). `retention_check_operational()` returns `{"operational_intelligence_persistence":"stateless_pure","bounded":True}`.

Existing persistence reused: `user_profiles` JSONB for exposures/evidence/journey/metrics/rollouts/emergency (bounded), `generation_telemetry` 22 cols + JSONB overflow, `commerce_offers`/`fangate_transactions`, `audit` 1000, `idempotency` 2000.

Restart behavior: no state to restore, no promotion, no erasure, no false success. `OperationalDecision` recomputed per call from current metrics.

## 8. Failure Behavior

| Failure | Required | Implemented |
|---|---|---|
| metrics unavailable (sample<5) | OBSERVE / INSUFFICIENT_DATA | `detect_*` returns `INSUFFICIENT_DATA` → `OBSERVE`, confidence 0.0 |
| revenue intelligence unavailable | continue only if safe | `analyze_operational_state` tolerates None inputs, returns HEALTHY or INSUFFICIENT |
| strategy evidence unavailable | SAFE_DEFAULT | `strategy_performance_for_dimension` insufficient_data true, no crash |
| baseline unavailable | INSUFFICIENT_DATA | `baseline_comparison` verdict INSUFFICIENT_DATA if sample<5 |
| experiment unavailable | CONTROL | `experiment_intelligence` insufficient true, governance falls back CONTROL |
| production control unavailable (exception) | FAIL CLOSED | `_is_recommendation_allowed` catches Exception → `allowed=False, blocking_reason=production_control_unavailable` |
| DropFans unavailable | NO fabricated purchase | `has_valid_purchase_evidence` requires transaction_id+dropfans_record, not inferred |
| memory unavailable | continue without memory if safe | Not used directly, but `open_loops` empty → no stagnation signal |
| Redis recovery unavailable | preserve pending | Existing `requeue_stalled_messages` XAUTOCLAIM already; operational not affected |
| Qwen unavailable | existing degraded fallback safe_fallback_response | Not affected (operational pure) |
| scoring unavailable | existing operator queue 0.0 | Not affected |
| telemetry unavailable | continue only if safe | Best-effort `enrich_telemetry_with_funnel` tolerant |

All new code `try/except` not propagating, bounded, no invented purchase/revenue.

## 9. Telemetry

New fields are **extend-only, compact, PII-free, <500 chars, no content/secrets**:
- `operational_signal` (via `reason_code` in Recommendation)
- `operational_recommendation` (action)
- `operational_priority` (1-13)
- `operational_allowed` (bool)
- `operational_block_reason` (global_pause etc.)
- `operational_confidence` (0.0-1.0 via Beta)

Reuse existing `decision_trace` style via `_trace_compact`: `signal=... action=... priority=... conf=... allowed=... sample_size=...`. No duplication of `pressure_score`/`risk_state`/`production_state`/`strategy_selected` etc. — those remain in telemetry.

## 10. Tests

`tests/test_phase26_operational_intelligence.py` 43 tests deterministic, no DB (mocked where needed), creator/fan isolated, DropFans authority, single-pass 1/1/1/0, trace bounded <500, idempotent generation_id, restart safe stateless, degraded, rollback preserves evidence, roll-forward safe, emergency 6, baseline, confidence, production authority, hierarchy, no architecture redesign (0 new workers/queues/LLM/migrations/providers).

## 11. Single-pass / No Redesign Proof

- `verify_operational_single_pass({"extract_commerce_signals":1,"qwen":1,"scoring":1,"additional_llm":0}) → True`; operational helpers never call LLM (`generate_content` not imported).
- `pathlib.Path("workers").glob("*.py")` remains 3 workers, no `operational` worker.
- `commerce/operational_intelligence.py` has no `NEW_STREAM`/`NEW_QUEUE`.
- `db/migrations/*.sql` has no `operational` migration.
- Provider remains `ollama/qwen2.5:3b`, cheap_model `gemini-flash-latest`.

