# AI_NATIVE_COMMERCE_PHASE_25_FORENSIC_AUDIT.md
# Phase 25 — Forensic Audit (STAGE A, no production code changes)
# Date: 2026-08-30

## 1. Scope & Method
Inspected actual implementation of: `commerce/adaptive_optimization.py` (1223 LOC), `commerce/conversation_operations.py` (775), `commerce/conversation_intelligence.py` (207), `commerce/conversation_outcomes.py` (113), `commerce/strategy_learning.py` (321), `commerce/long_term_memory.py` (262), `commerce/fan_memory.py` (46), `commerce/product_knowledge.py` (46), `commerce/re_engagement.py` (119), `commerce/content_matching.py` (140), `commerce/product_selection.py`, `commerce/dao.py` (1060), `core/telemetry.py` (252), `workers/llm_worker.py` (1458), `workers/scheduler_worker.py` (307), `db/postgres.py` (2800+), `db/dropfans.py`, `integrations/dropfans/client.py`, `integrations/dropfans/service.py`, plus `tests/`, `docs/`, migrations.

Searched globally for: `record_metric`, `query_metrics`, `metrics_by_dimension`, `strategy_evidence`, `ExtendedEvidence`, `StrategyExposure`, `attributable_exposure`, `CanonicalOutcome`, `outcome_strength`, `purchase attribution`, `lifecycle`, `relationship`, `commerce`, `funnel`, `conversion`, `repeat`, `rejection`, `handoff`, `pressure`, `fatigue`, `experiment`, `rollout`, `rollback`, `audit`, `generation_id`, `transaction_id`, `sale_id`.

No production code was modified during this audit.

## 2. Existing Event Sources

| Event | Source | Producer | Identifier | Creator Scope | Fan Scope | Timestamp | Dimensions | Persistence | Retention |
|---|---|---|---|---|---|---|---|---|---|
| inbound message | Telethon `client.on(events.NewMessage)` → `debounce_enqueue` → `enqueue_inbound` | chatbotv2 | `telegram_message_id`, `user_id` | via `creator_id` resolved later | `user_id` | `sent_at` (messages) | direction, content hash | `messages` table | indefinite |
| generation started | `workers/llm_worker.process_message` → `publish_event("ai.generation_started")` | llm_worker | `generation_id` UUID | `creator_id` (resolved) | `user_id` | event `timestamp` | message_preview | Redis Pub/Sub (transient) + telemetry cache | ephemeral |
| generation completed | `publish_event("ai.generation_completed")` after `enqueue_send` OR `add_to_operator_queue` | llm_worker | `generation_id` (same) | `creator_id` | `user_id` | same event | draft, score, flags, was_auto_approved | Redis Pub/Sub + `generation_telemetry` | telemetry table + 30d JSONB |
| generation failed | `publish_event("ai.generation_failed")` on exception | llm_worker | `generation_id` | `creator_id` | `user_id` | event ts | error code | Pub/Sub + telemetry | same |
| strategy exposure | `make_exposure` → `persist_exposure` + `record_exposure_memory` | llm_worker (after conversational bridge) | `generation_id` | `creator_id` | `user_id` | `exposure.timestamp` ISO8601 | strategy_family, variant, topic, conversation_stage, desire_stage, temperature, sales_window, NBA, response_mode, question_policy, product_id/family | `user_profiles.strategy_exposures_by_creator` JSONB + in-memory `_exposure_buffer` dict | 50 per fan + 30d prune (10 min keep) |
| response generated | `generate_draft` / `generate_commerce_response` inside pipeline | llm_worker / pipeline | `generation_id` | `creator_id` | `user_id` | generation latency | provider, model | transient | not persisted separately |
| response sent | `chatbotv2/main._process_send_stream` → `client.send_message` → `save_outbound_after_send` → `publish_event("message.sent")` | bot_main | `telegram_message_id` (real) | `creator_id` (if media vault) | `user_id` (entity) | `sent_at` | `was_auto_approved`, `confidence_score`, media_type | `messages` outbound + Redis dedup | indefinite |
| delivery success/failure | `publish_event("message.sent")` / `message.send_failed`, `vault.media_sent`, `move_send_to_dlq` | bot_main | `message_id` (Redis stream) | `creator_id` | `user_id` | DLQ `failure_timestamp` | reason, stream | `DLQ_STREAM` + `dlq_messages` | dlq_retention_seconds (7d) |
| objection | `commerce/conversation_intelligence` `has_objection` → `HANDLE_OBJECTION` objective | conversational bridge | `generation_id` | `creator_id` | `user_id` | generation ts | objection_type (via DAO consecutive_rejections) | not persisted as event, only as `aftercare_status`/`consecutive_rejections` in `commerce_offers` | derived per turn |
| qualification | `derive_desire_stage` → qualification, `evaluate_offer_readiness` | conversational bridge | `generation_id` | `creator_id` | `user_id` | ts | desire_stage | transient | not persisted |
| offer presented | `commerce/dao.create_offer` + `create_offer_serialized` advisory lock, `commerce_offers` state pending/clicked | dao / pipeline `orchestrate_commerce` | `offer_id` | `creator_id` | `user_id` | `created_at`, `expires_at` | `product_id`, price_minor, link | `commerce_offers` table | indefinite (but recent_offer_count 24h window) |
| purchase | `commerce/dao.attribute_purchase_from_webhook` / `mark_offer_purchased` + `fangate_transactions` | webhook / reconciliation | `transaction_id` (DropFans) | `creator_id` | `user_id` (attached via attach_transaction_user) | `occurred_at` (DropFans) + `purchased_at` | `product_id`, `seller_earning`, `set_price`, currency | `commerce_offers` state purchased + `fangate_transactions` | indefinite |
| repeat purchase | `classify_canonical_outcome(has_purchase + is_repeat_purchase)` | adaptive_optimization | `generation_id` | `creator_id` | `user_id` | fan message ts | outcome=repeat_purchase weight 12.0 | via strategy_evidence purchase_count | 30d |
| rejection | `classify_canonical_outcome(REJECTION)` / DAO `mark_offer_declined` | adaptive / dao | `generation_id` / `offer_id` | `creator_id` | `user_id` | ts | consecutive_rejections | `commerce_offers` state declined | 10 recent window |
| aftercare | `mark_aftercare_pending` → pending, `mark_aftercare_completed` | dao | `offer_id` | `creator_id` | `user_id` | `purchased_at` | aftercare_status | `commerce_offers.aftercare_status` | per offer |
| re-engagement | `schedule_reengagement_if_eligible` → `scheduled_messages` dedup `reengage:{c}:{u}:{p}` | re_engagement / scheduler | `dedup_key` | `creator_id` | `user_id` | `execute_at` | `product_id` | `scheduled_messages` table | per run |
| handoff | `make_handoff` → `handoff_by_creator` JSONB, `get_handoff_memory` | conversation_operations | `creator_id:user_id` key | `creator_id` | `user_id` | `at` ISO8601 | reason, automation_restricted | `user_profiles.handoff_by_creator` + `_handoff_mem` in-memory | bounded per fan |
| failure | `classify_failure` 4 classes + `publish_event` | conversation_operations / workers | `generation_id` / `message_id` | `creator_id` (if known) | `user_id` | ts | failure_class, degraded_fallback | telemetry `failure_class`, DLQ | ephemeral |
| rollback | `perform_rollback` → `disable_rollout` + `disable_experiment` + `record_metric` + `record_audit` | production_control / scheduler orchestrate | `rollout_id` | `creator_id` (if scope creator) | — | `start_time` | reason, status rolled_back | `_rollout_registry` + sentinel -999999 JSONB + metrics | 50 rollouts |
| experiment | `Experiment` + `deterministic_assignment` + `assign_variant` + `register_experiment` | adaptive_optimization | `experiment_id` | `creator_id` | `user_id` | `start_time`/`end_time` | allocation, variant, status | `_experiment_registry` + sentinel `-creator_id` JSONB |  bound per creator |

**Gaps:** No unified canonical `RevenueEvent`/`FunnelEvent` abstraction; events are fragmented across `messages`, `commerce_offers`, `fangate_transactions`, `strategy_exposures`, `generation_telemetry`, `metrics`, `audits`, `scheduled_messages`. No single `FanJourneyEvent` store. No `sale_id` separate from `transaction_id` (DropFans uses `transaction_id` as sale identifier). No explicit `funnel transition` records.

## 3. Existing Funnel Information

Can the code answer:

| Question | Answer | Source / Missing |
|---|---|---|
| How many fans entered? | **Partial** — `users` table count per creator via `list_offers_for_user` not per funnel; no `NEW` funnel state persisted. `derive_lifecycle(NEW)` exists but not counted. | Missing transition log; can answer via `users` created_at but not via funnel. |
| How many became engaged? | **No** — `LifecycleState.ENGAGED` derived per turn but not aggregated. `compute_relationship_metrics` has `continuation` but not funnel counts. | No persistent funnel counts. |
| How many became interested? | **No** — `INTERESTED`/`QUALIFIED` derived but not counted. | Same. |
| How many became qualified? | **No** — same. |  |
| How many received an offer? | **Yes** — `commerce_offers` where state pending/clicked/purchased counts per creator, also `ppv_analytics_daily.offers_created`. | Exists via DAO + analytics daily. |
| How many purchased? | **Yes** — `commerce_offers` state purchased + `fangate_transactions` with `transaction_id`, also `ppv_analytics_daily.offers_purchased`. | Exists. |
| How many repeated? | **Partial** — `classify_canonical_outcome(REPEAT_PURCHASE)` weight exists, `total_purchases` counts but not repeat funnel aggregated. `compute_commerce_metrics` has `aftercare_to_repeat` but not funnel. | Not as funnel. |
| How many rejected? | **Partial** — `consecutive_rejections` and `mark_offer_declined`, metrics `rejections` exists. | Exists but not funnel. |
| How many returned after re-engagement? | **No** — `ReengagementMetrics` has `eligible/scheduled/sent/replied/positive/purchased` but not tied to funnel transition. | Gap: no funnel re-engagement return tracking. |

**Missing:** Explicit funnel state machine `NEW→ENGAGED→INTERESTED→QUALIFIED→OFFER_PRESENTED→PURCHASED→AFTERCARE→REPEAT_PURCHASE` with alternates, plus `metrics_by_dimension` for funnel steps, time-to-transition.

## 4. Existing Relationship Information

| Metric | Implemented | Location | Placeholder? |
|---|---|---|---|
| relationship health | **Placeholder** — `relationship_state` derived (`cold`/`warm` etc.) but no 0.0-1.0 health score. `lifecycle` includes `NEW/ENGAGED` but not numeric health. | `commerce/relationship.py` (not inspected but referenced) | Yes — no numeric. |
| engagement | **Partial** — `compute_relationship_metrics` reply_rate, continuation, positive_rate, return_rate (4). | `adaptive_optimization` | Implemented for events, not per fan realtime. |
| trust | **No** — not explicit, inferred via `positive_engagement` weight. | — | Missing. |
| conversation depth | **Partial** — `open_threads`, `current_topic`, `topic_continuity` exists, but no depth score. | `memory/context` | Missing numeric. |
| responsiveness | **No** — not measured (would be reply latency). | — | Missing. |
| commercial intent | **Yes** — `purchase_intent`, `content_interest` via CommerceSignals (0.0-1.0) | `commerce/deepseek` + `telemetry.commercial_objective` | Implemented. |
| fatigue | **Yes** — `compute_fatigue` 0.0-0.5 per strategy/family/mode/question | `adaptive_optimization` | Implemented. |
| negative sentiment | **Yes** — `negative_sentiment` via CommerceSignals, `REJECTION` weight -4.0 | `adaptive` | Implemented. |
| topic affinity | **Partial** — `rank_products_by_relevance` overlap, but not per-topic engagement metric. | `content_matching` | Missing systematic. |
| preference stability | **No** — `commercial_preferences_by_creator` with confidence/count but not stability metric. | `fan_memory` | Missing. |

**Separation:** `compute_relationship_metrics` vs `compute_commerce_metrics` already separate namespaces, but relationship lacks health/composite isolation test (must prove high relationship low commerce ≠ offer).

## 5. Existing Attribution

| Attribution | Supported? | Mechanism | Evidence |
|---|---|---|---|
| strategy → outcome | **Yes** — `ExtendedEvidence` per strategy (attempt/positive/neutral/negative/purchase) + `classify_canonical_outcome` + `outcome_strength` | `adaptive_optimization` `select_strategy_adaptive` | hierarchical FAN_TOPIC>CREATOR, Beta, fatigue |
| strategy → purchase | **Yes** — `purchase_count` in `ExtendedEvidence`, `attribute_purchase` window, `has_valid_purchase_evidence` requires DropFans | `adaptive` `has_valid_purchase_evidence` | DropFans authority, not language |
| strategy → repeat purchase | **Yes** — `REPEAT_PURCHASE` weight 12.0, purchase_count includes repeat? Actually repeat separate but weight same path, `purchase_count` incremented on PURCHASE/REPEAT | same | same |
| topic → purchase | **No** — topic in exposure but not aggregated per topic purchase rate (only `topic` field exists in exposure) | exposure has `topic` but no `metrics_by_dimension` for topic→purchase | Gap |
| product family → purchase | **Partial** — `product_family` in exposure + `content_matching` + `get_recent_offered_groups` but not aggregated purchase rate per family | exposure has `product_family` | Missing aggregation |
| lifecycle → purchase | **Partial** — `lifecycle_specific_outcome_weights` adjusts weights per lifecycle, but not measured conversion per lifecycle | `adaptive` weights | Missing metrics |
| objective → purchase | **No** — `conversation_objective` in telemetry but not aggregated per objective purchase | — | Gap |
| response mode → purchase | **No** — same, `response_mode` in exposure but not aggregated | — | Gap |
| experiment variant → outcome | **Partial** — `Experiment` + `deterministic_assignment` + `metrics_by_dimension` supports `experiment_id`/`variant` dimension, but no `baseline comparison` helper | `production_control` `metrics_by_dimension` | Exists but not baseline helper |
| experiment variant → purchase | **Partial** — same as above, but not via DropFans purchase join | — | Same gap |

## 6. Existing Time-to-Outcome

Current `attribute_purchase` buckets:
- `direct ≤24h`
- `assisted ≤24*7 (7d)`
- `organic >7d`
- `unknown` if no evidence / future / missing

**Phase 25 requires:** `IMMEDIATE <1h`, `SHORT 1h-24h`, `ASSISTED 1d-7d`, `LONG 7d-30d`, `UNKNOWN`. Current system has `direct` covering `<24h` without distinguishing `<1h` vs `1h-24h`, and `assisted` is same as Phase 25 ASSISTED but missing `LONG` distinction (organic is bucket for >7d but not bounded to 30d). Need to extend to 4 buckets + UNKNOWN, preserve direct/assisted mapping.

No duplicate attribution system needed — extend existing `attribute_purchase` to support 4 buckets or add `time_bucket` helper.

## 7. Existing Production Metrics

Phase 22/23/24 production metrics are **directly reusable**:
- `MetricWindow` 1h/24h/7d/30d, `_metric_events` bounded 5000 (now persisted sentinel -999997), `record_metric`/`query_metrics`/`aggregate_count`/`aggregate_rate`/`metrics_by_dimension`, `StrategyPerformance`, `HealthReport` via `evaluate_production_health` (success/failure/purchase/rejection/handoff/spam/pressure), `is_rollout_active_for`, `should_rollback`, `perform_rollback`, `derive_production_state`, `orchestrate_production_controls`, `OperationalAuditRecord`.

No second metrics store needed. Funnel/relationship/revenue metrics can be derived via `record_metric` with new `name` values or via `metrics_by_dimension` over existing dimensions (`creator_id`, `strategy`, `topic`, `product_family`, `lifecycle`, `objective`, `response_mode`, `experiment_id`, `variant`), reusing same store with new names (`funnel_transition`, `relationship_health`, etc.). Bounded retention already via `_METRIC_MAX` prune + 30d prune for exposures.

**Decision:** Reuse existing `production_control` metric store for new intelligence; do not create new DB.

## 8. Data Model Principle

Existing persistent stores:
- `user_profiles` JSONB: `strategy_exposures_by_creator` 50/30d, `strategy_evidence_by_creator` 20, `strategy_generation_seen` 100, `long_term_memory_by_creator` 20, `commercial_preferences_by_creator`, `handoff_by_creator`, `experiments_by_creator` (sentinel -creator), `metrics_by_creator` (new sentinel -999997), `rollouts_by_creator` (sentinel -999999), `emergency_state` (sentinel -999998), `conversation_observations` could be added but not needed new table.
- `generation_telemetry` 22 cols + JSONB overflow (new fields in `GenerationTelemetry.to_dict()` already include 19+ strategy + 8 operation fields; table widening deferred but best-effort insert).
- `commerce_offers` (offer state machine, price, aftercare, purchase), `fangate_transactions` (purchase truth), `ppv_analytics_daily` (daily rollup), `messages`, `scheduled_messages`, `tool_audit_log`.

**Funnel/relationship/revenue intelligence can be supported without migration** by:
- Reusing `record_metric` for funnel steps and relationship signals (no new table)
- Adding `commerce/revenue_intelligence.py` pure module with deterministic helpers operating on existing `user_profiles` JSONB + metrics + DAO
- Adding bounded journey history via `user_profiles` key `funnel_journey_by_creator` (similar to exposures, 20 bounded)

**Migration needed?** Only if we need SQL-aggregatable funnel transitions for dashboards. Existing `user_profiles` JSONB is sufficient for Phase 25 per retention principle; no migration for now. Prove via audit that `user_profiles` can support bounded journey (20) + `metrics` for funnel.

## 9. Forensic Verdict: What Is Genuinely Missing

| Phase 25 Requirement | Already Exists? | Genuinely Missing | Minimal Fix |
|---|---|---|---|
| Canonical event model (generation_id, creator_id, user_id, timestamp, lifecycle, objective, strategy, topic, product_family, response_mode, experiment/variant, outcome, attribution) | Partial (ConversationObservation has 20 fields, StrategyExposure has 13, but not unified Revenue/Funnel/Relationship) | **Missing unified deterministic event** with UNKNOWN handling, no message content, bounded | Create smallest `RevenueEvent` common dataclass in new module, reusing existing fields, no DB |
| Funnel `NEW→REPEAT` with alternates | Partial (LifecycleState 15, but not funnel transition log) | **Missing explicit funnel states + transition records** | Extend funnel mapping without overriding derive_lifecycle |
| Funnel transitions from_state/to_state + dimensions | No | **Missing** | Add `record_funnel_transition` bounded JSONB |
| Conversion metrics per window/dimension | Partial (aggregate_count exists but not funnel rates) | **Missing funnel conversion helpers** | Add `compute_funnel_conversion` reusing metrics |
| Relationship intelligence separate | Partial (4 metrics) | **Missing 8+ metrics (health, engagement, responsiveness, depth, trust, etc.)** | Extend `compute_relationship_metrics` deterministically |
| Time-to-outcome 4 buckets | Partial (direct/assisted) | **Missing IMMEDIATE/SHORT distinction + LONG bounded 30d** | Extend `attribute_purchase` with new bucket helper |
| Strategy performance per 7 dimensions | Partial (strategy_score, but not per lifecycle/product etc.) | **Missing per-dimension helpers** | Add `strategy_performance_by_dimension` reusing ExtendedEvidence |
| Product-family intelligence | Partial (exposure has field) | **Missing aggregation** | Add helper via metrics |
| Topic intelligence | No systematic | **Missing** | Add via metrics |
| Objective performance (14) | No aggregation | **Missing** | Add via metrics |
| Response-mode intelligence | No aggregation | **Missing** | Add via metrics |
| Experiment intelligence baseline comparison | Partial (metrics_by_dimension, but no baseline vs optimized helper) | **Missing IMPROVED/STABLE/REGRESSED/INSUFFICIENT_DATA helper** | Add reusing detect_regression |
| Fan journey inspectable bounded | No | **Missing bounded journey** | Add JSONB bounded 20 via user_profiles |
| Creator intelligence isolated | Partial (production health per creator) | **Missing creator-scoped funnel/relationship rollup** | Add via metrics |
| Fan segmentation behavioral explainable | No | **Missing deterministic segments** | Add 10 segments pure |
| No black-box score | Existing strategy_score explainable, but no composite | Ensure no mysterious fan_score, expose components | Keep separate dimensions |
| Retention bounded | Existing 30d/50/20 but not for journey | Need journey bound | Add 20 bound |
| Observability | GenerationTelemetry 30+ fields, but not funnel/relationship fields | **Missing funnel_state, relationship_health etc.** | Extend telemetry with optional bounded fields, compact |
| Production control integration | Already governs via autonomous_allowed, but new intelligence must not bypass | Ensure new intelligence is read-only, not authority | Document hierarchy |
| Single-pass, no new worker/queue/LLM | Already 1/1/1/0 | Must preserve | Verify 0 new LLM in new module |

**Root cause of missing intelligence:** System has strong **operational** intelligence (pressures, risks, rollouts) but lacks **analytical** intelligence that is deterministic, creator-isolated, bounded, and evidence-based for revenue/relationship/funnel over time and dimensions, without creating a competing authority.

