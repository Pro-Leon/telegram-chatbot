# AI_NATIVE_COMMERCE_PHASE_27_IMPLEMENTATION_MAP.md
# Phase 27 — Implementation Map (Stage B)
# Date: 2026-08-30

## 1. Actual Production Call Graph (after wiring)

```
Telegram inbound (Telethon)
  ↓ debounce_enqueue → XADD inbound_messages (Redis Streams)
  ↓ llm_worker.run_worker: requeue_stalled_messages XAUTOCLAIM 60s, XREADGROUP llm_workers
  ↓ acquire_user_lock 30s
  ↓ build_qwen3_context (conversation_state, LTM 3, AVAILABLE CONTENT)
  ↓ SINGLE extract_commerce_signals (cheap_model 1) → _try_commerce_draft OR conversational bridge
  ↓ conversation_intelligence (14 objectives, priority) → next_best_action
  ↓ conversation_operations (pressure 0..1, Risk, Lifecycle 15, policy_allows, ConversationOperationDecision single anchor)
  ↓ production_control (MetricWindow, health, rollout SHA256, autonomous_allowed, derive_production_state, orchestrate)
  ↓ strategy selection (select_strategy_adaptive Beta/fatigue)
  ↓ **pre-Qwen production gate** (autonomous_allowed + is_commerce_paused + is_reengagement_paused + get_handoff_memory + is_rollout_active_for) → if not allowed → safe fallback, skip Qwen (saves Qwen)
  ↓ **operational intelligence per generation** (NEW wiring, best-effort, pure, 0 LLM):
      operational_decision(creator_id, user_id, generation_id, fatigue, rejection_rate, handoff_rate, spam_rate, open_loops, baseline/current purchase_rate, sample_size, strategy) → list[OperationalDiagnosis] priority 1-13 → recommendation_for_diagnosis → OperationalRecommendation (action, priority, reason_code, confidence 1-unc, evidence, scope creator:fan, allowed via _is_recommendation_allowed → is_global_paused → autonomous_allowed → optimization_allowed → derive_production_state, trace<500)
      → enrich_telemetry_with_funnel (funnel_state, transition, optimization_state) + decision_trace OP: trace
      → for rec in recommendations where allowed: execute_operational_recommendation(rec, revalidate=True) → see §4
  ↓ Qwen 1 (generate_draft OR generate_commerce_response) → scoring 1 → post-scoring production gate (record_metric, record_audit) → routing dedup md5 → ai.generation_completed MUST after enqueue_send
  ↓ scheduler_worker._scheduler_loop every 10s:
      recover_stale 300s, process_due_messages gated by is_global_paused, reconcile_purchases
      → orchestrate_production_controls (health→rollback/hold/advance idempotent)
      → **operational intelligence per creator** (NEW wiring, bounded 5 creators per cycle, pure):
          evaluate_production_health per creator → operational_decision(creator_id, rejection/handoff/spam/pressure, baseline/current, sample) → for rec where allowed and not NO_ACTION/OBSERVE: rec.generation_id = scheduler-{creator}-{ts}-{action} → execute_operational_recommendation (revalidate, idempotent)
      → re-engagement loop (list_offers pending 50, age≥48h, gated by is_global/is_reengagement/is_commerce paused + is_reengagement_governed_allowed)
  ↓ bot_main._process_send_stream: rate limit Lua, blacklist, DLQ+XACK, vault reserve
  ↓ outcome → strategy evidence (ExtendedEvidence, composite 20, dedup 100, Beta) → metrics → health → operational intelligence (closed-loop)
```

## 2. New Module(s)

| Module | LOC | Purpose | Existing Reused | Pure? |
|---|---|---|---|---|
| `commerce/operational_execution.py` | 210 | Execute OperationalRecommendation via existing mechanisms: `set_emergency(STRATEGY_PAUSE)`, `disable_experiment`, `perform_rollback`, `make_handoff`, `record_audit`/`record_metric`, `check_idempotent` generation_id+action+scope, stale revalidation via `_is_recommendation_allowed`, creator/fan isolation, bounded trace | `production_control.set_emergency/disable_experiment/perform_rollback/check_idempotent/record_audit`, `adaptive_optimization.disable_experiment`, `conversation_operations.make_handoff/set_handoff_memory` | YES pure side-effect via existing, no DB/Redis/LLM new |

Existing `commerce/operational_intelligence.py` (753 LOC) remains pure library, now **consumed** per generation (llm_worker) and per creator periodic (scheduler), not duplicated.

## 3. Wiring Changes (minimal, best-effort, fail-closed)

| File | Change | LOC |
|---|---|---|
| `workers/llm_worker.py` | After pre-Qwen gate, added **operational intelligence per generation** block: `evaluate_production_health` → `operational_decision` (relationship_health 0.6, commercial_intent 0.3, fatigue, rejection/handoff/spam, open_loops via `retrieve_relevant_memories`, baseline/current purchase, sample, strategy) → `enrich_telemetry_with_funnel` + `decision_trace` OP: → loop `execute_operational_recommendation` where allowed (revalidate, idempotent) | +45 |
| `workers/scheduler_worker.py` | After `orchestrate_production_controls`, added **per creator operational execution** bounded 5 creators: `evaluate_production_health` per creator → `operational_decision` → for rec allowed and not NO_ACTION/OBSERVE: set `generation_id = scheduler-{cid}-{ts}-{action}` → `execute_operational_recommendation` | +32 |
| `commerce/operational_intelligence.py` | No change (already pure, bounded, isolated) | 0 |
| `tests/test_phase27_autonomous_execution.py` | NEW 31 tests A-Z, hierarchy, single-pass, no redesign | 550 |

No new worker/queue/LLM/DB table/migration/provider, no redesign, no second ConversationObjective/ProductionState.

## 4. Action Execution Matrix (Concrete Effects)

| Action | Existing Mechanism Called | Observable Effect | Evidence Preserved? |
|---|---|---|---|
| NO_ACTION, OBSERVE | audit only (`record_audit` + metric `operational_action`) | None (correct) | Yes |
| EXPLORE, EXPLOIT | audit only (explore via existing `select_strategy_adaptive` 10% budget) | Next strategy may explore if under-observed | Yes |
| REDUCE_PRESSURE | audit + `record_metric pressure_suppressed` | Next `compute_pressure` with fewer offers lower | Yes |
| SUPPRESS_STRATEGY | `set_emergency(STRATEGY_PAUSE, target=strategy, creator_id)` | `is_strategy_paused` true → `autonomous_allowed` false → strategy not selected | Yes (evidence not deleted, `check_idempotent` prevents duplicate) |
| ROTATE_STRATEGY/TOPIC | same as SUPPRESS_STRATEGY for fatigued strategy | Fatigued strategy paused → alternative via `select_strategy_adaptive` | Yes |
| SUPPRESS_PRODUCT_FAMILY | audit + `record_metric family_suppressed` (family fatigue already via `recent_offered_groups` 24h) | Metrics show suppression, product authority preserved (no invented product) | Yes |
| PRIORITIZE_RELATIONSHIP | audit + metric `relationship_prioritized` | `relationship_vs_commerce_safety` already high_rel low_commerce → NO_OFFER | Yes |
| FOLLOW_UP_OPEN_LOOP | audit + metric `open_loop_followup` | `derive_conversation_objective` will produce `FOLLOW_UP_OPEN_LOOP` → `CALLBACK` next turn if loop still OPEN importance≥0.7 | Yes (resolved not re-triggered via `resolve_open_loop` status RESOLVED) |
| SUPPRESS_REENGAGEMENT | `set_emergency(REENGAGEMENT_PAUSE, creator_id)` | `is_reengagement_paused` true → scheduler skips re-engagement | Yes |
| PAUSE_EXPERIMENT | `disable_experiment` | `assign_variant` → CONTROL | Yes |
| ROLLBACK_EXPERIMENT/ROLLOUT | `perform_rollback` (or `disable_experiment` fallback) | `get_rollout` status rolled_back → `is_rollout_active_for` false → fallback CONTROL/SAFE_DEFAULT | Yes (evidence not deleted) |
| HANDOFF | `make_handoff` + `set_handoff_memory` | `get_handoff_memory` active → risk HANDOFF → `ConversationOperationDecision` handoff_required true → no autonomous commercial | Yes |

All actions check `allowed` first, revalidate current `is_global_paused`/`autonomous_allowed`/`optimization_allowed`/`production_state` (stale safety), idempotent via `check_idempotent(generation_id:action:scope)`, creator/fan isolated via `creator_id:user_id` scope, trace <500, no content/secrets.

## 5. Authorization Flow

```
OperationalDiagnosis → recommendation_for_diagnosis
  → _is_recommendation_allowed:
      is_global_paused? → false global_pause
      is_creator_paused? → false creator_pause
      autonomous_allowed(strategy/experiment)? → false strategy_pause/experiment_pause
      optimization_allowed(creator/strategy)? → false commerce_pause / production_state suppressed/handoff/rollback
      health.production_state suppressed/handoff/rollback → block aggressive (allow OBSERVE only)
      else → true allowed
  → OperationalRecommendation(allowed, blocking_reason)
  → execute_operational_recommendation:
      if not allowed → executed false, no mutation
      if check_idempotent(generation_id:action:scope) → false duplicate
      revalidate via _is_recommendation_allowed again (stale) → false if now paused else continue
      per action → existing mechanism (see §4) → record_audit + metric → return executed true + effect
```

Uses existing `autonomous_allowed`, `is_*_paused`, `optimization_allowed`, `evaluate_production_health`/`derive_production_state`, `should_rollback`, `perform_rollback`, `strategy governance`, `experiment governance` — no new authority.

## 6. Closed-Loop Flow

```
generation_id (UUID per inbound)
  → operational_decision(creator, user, generation_id, metrics) → OperationalDiagnosis/Recommendation (priority, reason, confidence, evidence, scope, allowed, trace)
  → execute_operational_recommendation (if allowed, revalidated, idempotent) → action via existing mechanism (e.g., set_emergency STRATEGY_PAUSE)
  → behavior: next generation's select_strategy_adaptive excludes paused strategy → different strategy
  → outcome: fan responds → classify_canonical_outcome → outcome_strength → attribute_purchase (DropFans) → update_strategy_evidence extended (composite, dedup 100, Beta, decay)
  → metrics: record_metric → evaluate_production_health → next operational_decision
```

Via `generation_id` + `check_idempotent` + `record_audit(generation_id)` + `generation_telemetry` bounded, no content/secrets.

## 7. Stale Recommendation Safety

`recommendation generated at T0, action at T1` → before execution, `execute_operational_recommendation` revalidates via `_is_recommendation_allowed` current `is_global_paused`, `autonomous_allowed` with current `strategy`, `optimization_allowed` with current health — if state changed to `global pause`, `cooldown`, `purchase`→`aftercare`, `experiment disabled`, `DropFans unavailable`, `handoff`, then `allowed false` → `stale_blocked:{reason}` fail-closed, no mutation. Idempotency also prevents double-rollback.

## 8. Restart Safety

- Operational intelligence is stateless pure → no state to lose, no promotion.
- Actions that **do** persist (`set_emergency` STRATEGY/REENGAGEMENT pause, `disable_experiment`, `perform_rollback`, `make_handoff`) use existing restart-safe sentinels: `load_persisted_state` reloads `_rollout_registry`, `_emergency_state` via `-999999/-999998`, `strategy_exposures` via `user_profiles`, `check_idempotent` 2000 bounded, `generation_id` deduplication prevents duplicate exposure. No jump `1%→100%`, no duplicate `strategy_exposure`, no evidence deletion.

## 9. Concurrency

Pure functions + existing persistence `f"{creator}:{user}"` keys, `pg_advisory_xact_lock` for `create_offer_serialized`, `check_idempotent` set, `suppressed_strategies` via `set_emergency` creator-scoped (key `strategy_pause:{creator}:{target}`), `handoff_by_creator` per fan — no cross-creator/fan contamination. Tested via concurrent `SUPPRESS_STRATEGY` for `creator:1` vs `creator:2` same strategy → isolated.

## 10. Failure/Decay/ Single-pass

- Failures: operational intelligence unavailable → safe existing behavior (llm_worker best-effort try/except, scheduler best-effort); recommendation unavailable → safe default OBSERVE; strategy evidence unavailable → SAFE_DEFAULT; memory unavailable → continue without memory; DropFans unavailable → no fabricated purchase; Qwen/scoring unavailable → existing degraded fallback; Redis recovery → preserve pending; telemetry unavailable → continue if safe; scheduler unavailable → no re-engagement; production control unavailable → fail closed (`production_control_unavailable`).
- Single-pass: `extract_commerce_signals` 1, `qwen` 1, `scoring` 1, `additional_llm` 0 — operational intelligence/helpers are pure, 0 `generate_content`. Verified via `verify_single_pass` reuse.

## 11. Persistence / Retention / Telemetry

- New execution uses existing bounded: `_metric_events` 5000, `_audit_log` 1000, `_exposure_buffer` 50/30d, `funnel_journey` 20, `_rollout_registry` 50, `_idempotency_seen` 2000, `strategy_exposures` 50, `handoff_by_creator` per fan, all via `user_profiles` JSONB sentinels, no unbounded.
- Telemetry: enrich existing `GenerationTelemetry` with `funnel_state/transition/optimization_state` via `enrich_telemetry_with_funnel` compact <500, no content/secrets, extends `decision_trace` with `OP:` prefix bounded 480.

## 12. Tests

`tests/test_phase27_autonomous_execution.py` 31 tests deterministic, no DB (mocked where needed), isolated, DropFans authority, single-pass, trace, idempotent, restart safe, degraded, closed-loop via `classify_canonical_outcome` → `ExtendedEvidence`, canary, emergency, hierarchy, no redesign.

