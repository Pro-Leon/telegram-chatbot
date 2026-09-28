# AI_NATIVE_COMMERCE_PHASE_25_IMPLEMENTATION_MAP.md
# Phase 25 — Implementation Map (Stage B, minimal deterministic layer)
# Date: 2026-08-30

## 1. Architecture Invariant (preserved)
```
1 commerce signal (extract_commerce_signals, cheap_model, fallback Ollama)
1 Qwen generation (generate_draft OR generate_commerce_response, never both; pre-gate may skip to 0 when paused)
1 scoring pass (score_draft, authority-aware, hard flags → 0.1, failure → 0.0)
0 additional LLM calls (all new Phase 25 code is pure: metrics, funnel, relationship, attribution, journey, segmentation)
No new worker, queue, Redis Stream, Telethon, PostgreSQL, DropFans, ORM, redesign, bypass.
```

## 2. Actual Runtime Graph (Phase 25 sits above existing controls)
```
Event Source
  ↓
Canonical Observation (FunnelEvent/RelationshipEvent/RevenueEvent/StrategyOutcomeEvent = CanonicalEvent with UNKNOWN fallback, no message content, generation_id/creator_id/user_id/timestamp + lifecycle/objective/strategy/topic/product_family/response_mode/experiment/variant/outcome/attribution/funnel)
  ↓
Funnel (funnel_state_for_lifecycle: LifecycleState → FunnelState NEW..REPEAT + alternates, is_valid_funnel_transition forward-only, record_funnel_transition bounded 20 via user_profiles funnel_journey_by_creator + _journey_mem)
  ↓
Relationship Metrics (compute_relationship_health: engagement, responsiveness, conversation_depth, topic_continuity, trust_signal, negative_signal, fatigue, commercial_intent + relationship_vs_commerce_safety: high_rel_low_commerce → NO OFFER, high_fatigue → suppress)
  ↓
Commerce Metrics (reuses production_control: aggregate_count/rate per MetricWindow 1h/24h/7d/30d per creator/strategy/topic/product_family/lifecycle/objective/response_mode/experiment; compute_conversion_metrics: engagement/qualification/offer/purchase/repeat/rejection/handoff/re_engagement rates, requires sample>=5 else insufficient)
  ↓
Attribution (attribute_purchase direct≤24h/assisted≤7d/organic else unknown + time_bucket_for_purchase IMMEDIATE<1h/SHORT 1h-24h/ASSISTED 1d-7d/LONG 7d-30d/UNKNOWN, DropFans transaction required, fan text "I bought" insufficient)
  ↓
Strategy Evidence (ExtendedEvidence + strategy_performance_for_dimension per strategy/creator/fan/topic/product_family/lifecycle/objective/response_mode, reusing get_exposures_memory 50 + query_metrics, sample>=5, Beta uncertainty 0.02-0.5, fatigue, decay exp(-days/30))
  ↓
Baseline (baseline_comparison: current vs baseline split half, detect_regression thresholds 0.20/0.15/0.25/0.30, verdict IMPROVED/STABLE/REGRESSED/INSUFFICIENT_DATA, confidence 1-unc) + optimization_quality (RETAIN/ROLLBACK/HOLD/PAUSE/INSUFFICIENT_DATA)
  ↓
Optimization (strategy/product/topic/objective/response-mode/experiment intelligence via metrics_by_dimension, experiment_intelligence control vs variant, no authority over price/product/DropFans/safety, only behavioral variables)
  ↓
Production Control (optimization_allowed: is_global_paused → autonomous_allowed(creator/strategy/experiment/commerce) → evaluate_production_health → derive_production_state PAUSED/ROLLBACK/SUPPRESSED/HANDOFF blocks optimization; pressure/risk/fatigue also gate)
```

Workers unchanged: `llm_worker.process_message` and `scheduler_worker._scheduler_loop` remain single-pass; new intelligence is read-only, not authority. `core/telemetry.py` extended with `funnel_state, funnel_transition, relationship_health, commercial_intent, conversion_window, baseline_state, optimization_state` (compact, bounded, PII-free) via `enrich_telemetry_with_funnel`.

## 3. Files Changed (minimal)

| File | Change | LOC | Nature |
|---|---|---|---|
| `commerce/revenue_intelligence.py` | **NEW** 970 LOC pure deterministic: CanonicalEvent + aliases, FunnelState 8+6, funnel_state_for_lifecycle, is_valid_funnel_transition, FunnelTransition, record_funnel_transition (bounded 20 JSONB + _journey_mem), journey helpers, compute_conversion_metrics (reuse production_control), metrics_by_dimension wrapper, compute_relationship_health (+relationship_vs_commerce_safety), time_bucket 4 + attribution_with_timebucket, strategy_performance_for_dimension, product_family/topic/objective/response_mode metrics, experiment_intelligence, baseline_comparison, optimization_quality, journey_from_transitions, fan_value_model (LTV UNKNOWN if no amounts), creator_intelligence isolated, fan_segment 10 behavioral, retention_check, optimization_allowed gating, verify single-pass reuse | 970 | NEW pure |
| `core/telemetry.py` | Extended `GenerationTelemetry` with 7 Phase 25 fields (`funnel_state`, `funnel_transition`, `relationship_health`, `commercial_intent`, `conversion_window`, `baseline_state`, `optimization_state`) + `to_dict` entries | +14 | MOD |
| `tests/test_phase25_revenue_relationship_intelligence.py` | **NEW** 61 tests A-Z + canonical/segmentation, deterministic, creator/fan isolated, DropFans authority, production control, single-pass, no new worker, degraded, PII, idempotency, concurrency, regression | ~550 | NEW tests |
| `docs/AI_NATIVE_COMMERCE_PHASE_25_FORENSIC_AUDIT.md` | Forensic audit Stage A | — | DOC |
| `docs/AI_NATIVE_COMMERCE_PHASE_25_IMPLEMENTATION_MAP.md` | This map | — | DOC |
| `docs/AI_NATIVE_COMMERCE_PHASE_25_FINAL_REPORT.md` | Final report | — | DOC |

No migration, no new worker/queue/ORM/LLM, no redesign, provider unchanged (ollama/qwen2.5:3b), DropFans sole authority.

## 4. Canonical Event Model (§6)
- `CanonicalEvent` dataclass: `generation_id, creator_id, user_id, timestamp, lifecycle, objective, strategy, topic, product_family, response_mode, experiment_id, variant, outcome, attribution_type, funnel_state, funnel_transition, relationship_health, commercial_intent, conversion_window` — all bounded to 120 chars, uses `UNKNOWN`/`NOT_AVAILABLE` never fabricated, `to_dict`/`sanitized` removes content/secrets.
- Aliases `RevenueEvent = RelationshipEvent = FunnelEvent = StrategyOutcomeEvent = FanJourneyEvent = CanonicalEvent` share representation.

## 5. Funnel Intelligence (§7, §8)
- `FunnelState` enum: `NEW, ENGAGED, INTERESTED, QUALIFIED, OFFER_PRESENTED, PURCHASED, AFTERCARE, REPEAT_PURCHASE` + `RELATIONSHIP_ONLY, OBJECTION, REJECTED, HANDOFF, COOLDOWN, SUPPRESSED, UNKNOWN`.
- `funnel_state_for_lifecycle(lifecycle, has_active_offer, has_purchased, is_on_cooldown, is_handoff, is_rejected, is_suppressed, has_objection)` — analytical interpretation, never overrides `derive_lifecycle`.
- `_ORDERED_FUNNEL` main progression 0-7 forward-only, alternates allow anywhere.
- `FunnelTransition` dataclass + `record_funnel_transition` (valid only if `is_valid_funnel_transition`, idempotent via `generation_id` + from/to, bounded 20 per `creator:user` via `user_profiles.funnel_journey_by_creator` JSONB + `_journey_mem` fallback) + `get_funnel_journey` + `journey_from_transitions` (dedup consecutive).

## 6. Conversion Metrics (§9)
- `compute_conversion_metrics(creator_id, window)` reuses `production_control.query_metrics` with `MetricWindow` 1h/24h/7d/30d; counts `generation_success/failure` as entered, `funnel_*`/`offers_presented`/`purchases`/`repeat_purchases`/`rejections`/`handoff_required`/`reengagement_sent`; returns `engagement_rate, qualification_rate, offer_rate, purchase_rate (purchases/offered if offered>=5), repeat_purchase_rate (repeated/purchased if purchased>=5), rejection_rate, handoff_rate, re_engagement_rate` with `sample_size` and `insufficient_data = entered<5`.
- `metrics_by_dimension_via_production(name, dimension, creator_id, window)` thin wrapper over `production_control.metrics_by_dimension`.

## 7. Relationship Intelligence (§10, §11)
- `compute_relationship_health(events)` separate namespace: `relationship_health` composite `(eng*0.3 + trust*0.3 + (1-neg)*0.2 + (1-fat)*0.2)`, plus `engagement, responsiveness, conversation_depth, topic_continuity, trust_signal, negative_signal, fatigue, commercial_intent` — never collapsed to single mysterious score without components.
- `relationship_vs_commerce_safety(relationship_health, commercial_intent, fatigue)` → `high_rel (≥0.7) + low_commerce (≤0.35) → NO OFFER`, `fatigue≥0.30 → suppress`, high commerce proceeds only if gates allow (hierarchy SAFETY>HANDOFF>AFTERCARE>OBJECTION>OPEN_LOOP>DIRECT_REQUEST>COMMERCE>OPTIMIZATION).
- Existing `compute_relationship_metrics` 4 + `compute_commerce_metrics` 3 remain, new health is independent.

## 8. Time-to-Outcome (§12)
- `TimeBucket` IMMEDIATE<1h, SHORT 1h-24h, ASSISTED 1d-7d, LONG 7d-30d, UNKNOWN.
- `time_bucket_for_purchase(exposure_time, purchase_time, transaction_evidence)` deterministic, uses DropFans timestamps, never infers from text.
- `attribution_with_timebucket` returns `(attribution direct/assisted/organic/unknown via adaptive_optimization.attribute_purchase, bucket)` preserving Phase 20.

## 9. Strategy Performance (§13, §14)
- `strategy_performance_for_dimension(strategy, creator/fan/topic/product_family/lifecycle/objective/response_mode)` reuses `get_exposures_memory` 50 filtered + `query_metrics` D30, returns `exposure_count, sample_size, insufficient_data (<5)`.
- Purchase attribution remains evidence-based `has_valid_purchase_evidence(transaction_id, dropfans_record)` + `attribute_purchase` 24h/7d, never `fan said "I bought"`.

## 10. Product-Family / Topic / Objective / Response-Mode (§15-§18)
- `product_family_metrics`, `topic_metrics`, `objective_metrics` (14 objectives), `response_mode_metrics` all via `metrics_by_dimension` / `query_metrics`, return `count, purchase_rate, rejection_rate, etc., sample_size, insufficient_data (<5)`, respect fatigue/cooldown/aftercare via production controls (not bypassed).
- Keep topic and commercial intent separate (fitness high relationship low purchase valid).

## 11. Experiment Intelligence (§19)
- `experiment_intelligence(experiment_id, creator_id, window)` → `{control: {sample, purchases, purchase_rate, negative_rate, handoff_rate, insufficient}, variant: same, comparison: IMPROVED/STABLE/REGRESSED/INSUFFICIENT_DATA, reasons}` via `detect_regression` on purchase_rate, uses `query_metrics` with `experiment_id`/`variant` dimension.

## 12. Baseline vs Optimized (§20, §21)
- `baseline_comparison(creator_id, strategy, window)` splits current metrics half as baseline vs current, rates via purch/total, uses `detect_regression` + Beta `1-unc` confidence, verdict `IMPROVED/STABLE/REGRESSED/INSUFFICIENT_DATA`.
- `optimization_quality(strategy, baseline_rate, current_rate, sample, confidence, fatigue)` → `RETAIN (improved + conf>0.7 + fatigue<0.3), ROLLBACK (regressed*0.8), PAUSE (fatigue≥0.3), HOLD, INSUFFICIENT_DATA`.
- Exposes `what changed, why, evidence, sample, confidence, baseline, current, risk` via returned dict, no statistical certainty without calculation.

## 13. Fan Journey (§22) + Value (§23)
- Journey via `record_funnel_transition` bounded 20, `journey_from_transitions` dedup, `get_funnel_journey` creator-isolated.
- `fan_value_model(creator_id, user_id, purchase_amounts)` returns `relationship_value (min(1, exps/20)), commercial_value (freq/5), purchase_frequency, repeat_purchase_value, engagement_value, ltv (sum if amounts else UNKNOWN)` — no arbitrary LTV estimate.

## 14. Creator Intelligence (§24) + Segmentation (§25)
- `creator_intelligence(creator_id, window)` → `{conversations, conversions, relationship_health, engagement, strategy_performance, product_family_performance, topic_performance, fatigue}` via conversion + relationship + metrics_by_dimension, creator-isolated.
- `fan_segment(...)` deterministic 10 behavioral segments: `RELATIONSHIP_HIGH_COMMERCE_LOW, RELATIONSHIP_HIGH_COMMERCE_HIGH, ENGAGED_EXPLORER, QUALIFIED_OPPORTUNITY, RECENT_PURCHASER, REPEAT_PURCHASER, OBJECTION_RECOVERY, COOLDOWN, FATIGUED, REENGAGEMENT_ELIGIBLE`, returns `(segment, reason)` explainable, no demographics.

## 15. Observability (§28) + Production Integration (§29)
- `enrich_telemetry_with_funnel(telemetry, funnel_state, ...)` sets 7 new fields on `GenerationTelemetry` if present (otherwise setattr), compact, bounded, PII-free via `sanitized`.
- `optimization_allowed(creator_id, strategy, experiment_id)` → checks `is_global_paused` → `autonomous_allowed` (global/creator/strategy/experiment/commerce) → `evaluate_production_health` + `derive_production_state` PAUSED/ROLLBACK/SUPPRESSED/HANDOFF blocks → returns `(allowed, reason)`. Optimization never bypasses `SAFETY>HANDOFF>AFTERCARE>OBJECTION>OPEN_LOOP>DIRECT_REQUEST>COMMERCE`.

## 16. Persistence / Retention (§27) + Single-pass (§30)
- Bounded: exposures 50/30d, evidence 20, journey 20, metrics 5000, audits 1000, experiments per creator bounded, _journey_mem 20, all via `user_profiles` JSONB + in-memory fallback, prune 30d.
- No new worker/queue/LLM: verified via `verify_single_pass` reuse + `verify_revenue_intelligence_single_pass`, no `generate_content`, no `NEW_STREAM`.

## 17. Test Mapping (A-Z)
- A funnel chain + state + transition + journey
- B invalid (no fabricated purchase/qualification, no lifecycle override)
- C high rel low commerce, low rel high commerce, fatigue suppression, negative
- D 1h/24h/7d/30d, insufficient
- E direct/immediate, short, assisted, long, unknown
- F strategy positive/rejection/purchase/repeat
- G family performance/fatigue/purchase
- H topic rel/commerce separate
- I 14 objectives
- J response modes
- K control/variant/insufficient/rollback
- L baseline verdicts + optimization_quality
- M creator isolation (metrics + journey)
- N fan isolation (exposures)
- O restart safety bounded 20
- P retention bounded
- Q DropFans authority
- R production control blocked when GLOBAL/STRATEGY pause, allowed when clear
- S safety hierarchy (optimization cannot override aftercare/global pause)
- T single-pass 1/1/1/0
- U no new worker/queue
- V degraded (metrics empty → insufficient, attribution unknown not crash)
- W PII (no message_content/secrets in event/telemetry)
- X idempotency (funnel generation_id dedup)
- Y concurrency isolated per creator:fan
- Z regression (existing still callable)

