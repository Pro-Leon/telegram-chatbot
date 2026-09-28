# AI-Native Commerce — Phase 21 Implementation Map

**Date:** 2026-08-30
**Scope:** Enterprise Autonomous Conversation Operations
**Method:** Forensic → root cause → minimal deterministic composition → wiring → deterministic tests → re-audit. No redesign.
**Provider:** ollama/qwen2.5:3b  Canary: NOT ACTIVATED
**Single-pass:** 1 × extract_commerce_signals + 1 × Qwen + 1 × scoring, 0 additional LLM calls

---

## 1. Forensic Baseline (Phase 20 → 21)

Phase 20 complete: `adaptive_optimization.py` 1223 LOC, `conversation_outcomes` 18 canonical + hierarchical `strategy_learning` composite keys `strategy:topic:product_family:lifecycle` bounded 20, Beta uncertainty, `core/telemetry` +11 fields, `workers/llm_worker` exposure before Qwen + outcome after, 101 tests passed, no migration, no new workers/queues. Verified via `docs/AI_NATIVE_COMMERCE_PHASE_21_FORENSIC_AUDIT.md` reconciliation table (6 existing, 10 partial, 3 missing, 1 external).

Remaining P1 gaps: duplicated conversation_state/response_mode/question_policy (memory/context vs llm_worker bridge), no explicit `CommercialPressureBudget 0..1`, no unified `RiskState` before Qwen, handoff not persisted restricted, failure taxonomy scattered, degraded modes implicit, experiment governance lacks thresholds/rollback, relationship vs commerce metrics unused, re-engagement not pressure/fatigue governed, decision trace partial, operator observability missing pressure/risk/allowed/trace.

---

## 2. Current Feedback Loop (After Phase 21)

```
INBOUND (Telegram peer, content, telegram_message_id)
 ↓ acquire_user_lock + upsert_user + is_user_auto_reply_excluded
 ↓ resolve_single_application_creator → creator_id? (creator isolation)
 ↓ build_qwen3_context (identity, conversation_state, vault TOP2, LTM 3) — still computed but now superseded by unified decision
 ↓ ai.generation_started(generation_id UUID, scope=user) best-effort
 ↓ SINGLE extract_commerce_signals(context) → CommerceSignals (purchase_intent, price_interest, content_interest, relationship_engagement, explicit_request, fan_asks_question)
 ↓ build_conversational_commerce_state: timing_context + behavioral_feedback + has_active_offer/has_purchased/is_on_cooldown/aftercare → derive_desire_stage + decay_desire → derive_commercial_temperature → rank_products_by_relevance → evaluate_offer_readiness → derive_commercial_objective → derive_sales_window → derive_conversation_objective 14 objectives priority 1-99 → next_best_action + objective_reason + candidates
 ↓ response_mode/question_policy (bridge subordinate to NBA, legacy context still computed but unified decision overrides)
 ↓ ConversationOperationDecision (NEW — authoritative): compute_pressure(recent_offer/rejection/strategy_family/question/fatigue/temp/lifecycle) → pressure_score 0..1 bucket relationship/exploration/opportunity/suppress → derive_risk(SAFE/CAUTION/SUPPRESS/HANDOFF) → derive_lifecycle(NEW..RE_ENGAGED) → strategy_governed_selection (FAN_TOPIC>FAN>CREATOR_TOPIC>CREATOR>SAFE, Beta, fatigue, pressure, regression filtered) → policy_allows(before Qwen: aftercare/cooldown/rejection/no product/invented price/product/URL/purchase claim/creator cross-contam/unsafe pressure/repeated questions/spammy re-engagement/invalid experiment) → build_operation_decision(objective/reason/NBA/strategy/source/confidence/mode/pressure/fatigue/risk/response_mode/question_policy/lifecycle/experiment/variant/allowed/blocking_reason/handoff_required/failure_class/decision_trace) → STORED as single authoritative gate
 ↓ make_exposure(strategy_family/topic/conversation_stage/desire/temperature/window/NBA/response_mode/question_policy/product_family) → persist_exposure() bounded 50 + prune 30d → in-memory ring for fatigue
 ↓ Qwen (1) — generate_draft or generate_draft_with_tools (provider ollama authoritative, no fallback to second LLM) — receives COMMERCIAL STATE + CONVERSATION INTELLIGENCE + RESPONSE mode + QUESTION policy + AVAILABLE CONTENT titles (semantic only) — no price/product authority
 ↓ scoring (1) authority-aware: price_mention hard flag only if unauthorized (is_authorized_commerce + authorized_price_minor), fail-closed 0.0 on LLM fail → composite 0..1
 ↓ policy_allows AFTER QWEN (authority validation): scoring flags → min 0.1 if hard flag, invented product/URL checked via has_relevant_product/sales_url existence
 ↓ routing: empty draft → operator_queue(empty_draft) → ai.generation_completed(was_auto_approved False); else if !auto_reply_enabled → operator_queue → suggestion.created + completed; else if score≥0.80 && !flags → enqueue_send dedup md5(user:message:telegram_id) → ai.generation_completed(was_auto_approved True) MUST after enqueue (lifecycle invariant); else operator_queue → suggestion.created + completed
 ↓ outcome feedback (before telemetry complete): classify_canonical_outcome(fan_message, desire_before/after, has_purchase via DropFans evidence, aftercare/handoff/cooldown) → outcome_strength + attribute_purchase(window) → update_strategy_evidence + update_strategy_evidence_extended(composite key, dedup generation_id ring 100) → compute_relationship_metrics vs compute_commerce_metrics separate
 ↓ handoff check: if objective==HUMAN_HANDOFF or is_handoff → set_handoff_memory(creator,user, reason) automation_restricted=True (persisted to user_profiles JSONB handoff_by_creator on next turn via async)
 ↓ GenerationTelemetry (Phase 21 extended): + operation_allowed/block_reason/pressure_score/risk_state/handoff_required/failure_class/decision_trace/lifecycle_state + Phase 20 fields + Phase 17 fields → telemetry.complete(success) → insert_generation_telemetry best-effort (never breaks pipeline)
 ↓ publish ai.generation_completed/failed best-effort (event_id UUID per publish, generation_id consistent) → release_user_lock
```

External purchase: `poll_sales` → synthetic_id SHA256(drop_id)%2^62 per-sale txn `dropfans:{sale_id}` → `attribute_purchase_from_webhook` exactly-one pending/clicked → attach_transaction_user WHERE user_id IS NULL → analytics UPSERT → next inbound classifies PURCHASE only if transaction_evidence true.
Scheduler: `claim_due_messages FOR UPDATE SKIP LOCKED` → `_check_user_eligible(is_blocked/do_not_auto_reply)` → `enqueue_send` dedup `scheduled:{dedup_key}:{id}` → `mark_aftercare_completed` if post_purchase_followup → `reconcile_purchases` → re-engagement governed: `list_offers_for_creator pending` age≥48h → compute_pressure + fatigue + is_reengagement_governed_allowed(48h+aftercare/cooldown/rejection/relevance/purchase/pressure bucket/fatigue/max_frequency 2/7d) → `schedule_reengagement_if_eligible dedup reengage:{c}:{u}:{p}`.

Invariants: generation_id consistent across started→completed/failed→suggestion, event_id UUID per publish, failure isolation (realtime best-effort), WebSocket acceleration not source of truth, polling fallback, creator-scoped `WHERE creator_id=$1`, DropFans sole, LLM language-only.

---

## 3. Data Contracts

### 3.1 ConversationOperationDecision (authoritative, new)
`commerce/conversation_operations.py:ConversationOperationDecision` dataclass fields as spec §27:
```
objective, objective_reason, next_best_action,
strategy, strategy_source, strategy_confidence, strategy_mode,
commercial_pressure (CommercialPressureBudget), fatigue, risk_state,
response_mode, question_policy,
lifecycle (LifecycleState), has_relevant_product,
experiment_id, variant,
allowed (bool), blocking_reason, handoff_required (bool),
failure_class, decision_trace (compact), generation_id, creator_id, user_id
```
Constructed via `build_operation_decision(**)` which normalizes enums (LifecycleState, RiskState, FailureClass from str), enforces policy (handoff→not allowed, pressure suppress bucket + commercial objective→not allowed, SUPPRESS/HANDOFF risk→not allowed), generates `decision_trace = trace_compact()` bounded <500 chars, structured `KEY=value` space-separated, no PII, generation-scoped. `to_dict()` serializes for telemetry.

Composes existing objects: `objective` from `conversation_intelligence`, `strategy` from `adaptive_optimization.select_strategy_adaptive`, `pressure` from `compute_pressure`, `risk` from `derive_risk`, `lifecycle` from `derive_lifecycle`, `response_mode` from `core/response_mode.plan_response_mode` (but unified: bridge already subordinate, new decision is single anchor). No duplication — references existing state.

Storage: not persisted as new table; telemetry `decision_trace` + `operation_allowed/block_reason/pressure_score/risk_state` stored per-generation in `GenerationTelemetry` (bounded, no secrets), exposures still in `user_profiles` JSONB.

### 3.2 CommercialPressureBudget (new)
`commerce/conversation_operations.py:CommercialPressureBudget`:
```
pressure_score 0..1, bucket  relationship(0.00-0.25)/exploration(0.25-0.50)/opportunity(0.50-0.75)/suppress(0.75-1.00),
recent_offer_count, recent_rejection_count, aftercare_active, is_on_cooldown, recent_question_count, fatigue_score, temperature_score
```
Computed via `compute_pressure(recent_offer_count, recent_rejection_count, recent_strategy_exposure, recent_family_exposure, aftercare, cooldown, re_engagement_age_hours, purchase_history_count, objective, temperature_score, recent_questions, fatigue, lifecycle, is_on_cooldown, aftercare_active)` deterministic from existing signals (`get_timing_context.recent_offer_count`, `get_behavioral_feedback_context.consecutive_rejections/aftercare`, `get_exposures_memory` fatigue, `temperature.score`, `objective`, `lifecycle`). Weights: offer 0.20 cap 0.40, rejection 0.15 cap 0.40, strategy/family 0.07, aftercare/cooldown +0.30 each, re-engagement <48h +0.15, purchase history -0.05/-0.10, questions +0.03, fatigue*0.30, hot temp +0.10, commercial objective +0.10, clamp 0..1 round 3 decimals. Lifecycle-aware (aftercare/cooldown/rejected +0.20). Creator/fan specific via inputs per user+creator.

### 3.3 RiskState + FailureClass (new)
`RiskState: SAFE, CAUTION, SUPPRESS, HANDOFF` via `derive_risk(pressure, is_handoff, is_blocked, consecutive_rejections, fatigue_score, has_unresolved_high_risk)`:
- HANDOFF if `is_handoff|is_blocked|high_risk`
- SUPPRESS if `bucket==suppress` or `rejections>=3` or `fatigue>=0.30` or `aftercare|cooldown`
- CAUTION if `bucket opportunity/exploration` with recent offer/rejection or `pressure>=0.40`
- else SAFE

`FailureClass: RETRYABLE, PERMANENT, DEGRADED, HANDOFF_REQUIRED` via `classify_failure(error_type, context)`:
- `invalid peer/entity/not found` → PERMANENT (DLQ+ACK)
- `operator required/handoff/blocked` → HANDOFF_REQUIRED
- `timeout/transport/stall/redis/5xx/connection/rate limit/xaautoclaim` → RETRYABLE (requeue)
- `memory write/dropfans/telemetry/attribution/qwen fail/scoring/database` → DEGRADED (continue degraded)
- default DEGRADED (fail safe). `degraded_fallback(component)` maps `qwen→safe_fallback_response, memory→continue_without_memory, adaptive→SAFE_DEFAULT, dropfans→commerce_suppressed, telemetry→best_effort_skip, scoring→operator_queue`.

### 3.4 HandoffState (new)
`HandoffState: active, reason, at (ISO8601), automation_restricted` via `make_handoff(reason)`. Persistence `user_profiles.facts->'handoff_by_creator'->{creator_id}` JSONB via `set_handoff/get_handoff/clear_handoff` async (best-effort) + in-memory `set_handoff_memory/get_handoff_memory/clear_handoff_memory` for tests. Automation restricted: `is_reengagement_governed_allowed` and `build_operation_decision` check `handoff_required` → `allowed=False`, `blocking_reason=handoff_required`, `HUMAN_HANDOFF` objective priority 1 still wins.

### 3.5 Experiment Governance (extended)
Existing `Experiment` (Phase 20) extended via `experiment_governed_assignment(creator,user,exp_id, allocation,status, exposures,outcomes)` which checks `minimum exposure threshold 5` before assigning EXPERIMENT else CONTROL, keeps deterministic hash. Future variant persistence already via `persist_experiment` sentinel; outcome attribution per-variant via exposures ring (strategy_family includes experiment variant).

### 3.6 LifecycleState (new, 15)
`LifecycleState: NEW, CURIOUS, ENGAGED, INTERESTED, QUALIFIED, DESIRING, OFFER_READY, PURCHASED, AFTERCARE, REPEAT, COOLDOWN, REJECTED, HANDOFF, DORMANT, RE_ENGAGED` via `derive_lifecycle(desire_stage, relationship_state, aftercare_status, is_on_cooldown, consecutive_rejections, has_purchased, is_handoff, hours_since_last_message)`. Maps `desire_stage` 0-8 → lifecycle, else relationship/hours. Used in pressure, strategy composite `strategy:lifecycle`, and `has_relevant_product` already.

### 3.7 Telemetry Phase 21 (extended)
`core/telemetry.py:GenerationTelemetry` added 8 fields: `operation_allowed, operation_block_reason, pressure_score, risk_state, handoff_required, failure_class, decision_trace, lifecycle_state` (all compact, no PII). `to_dict()` updated. Wire: `workers/llm_worker` sets `pressure_score/risk_state/operation_allowed/block_reason/decision_trace/lifecycle_state/handoff_required` from `ConversationOperationDecision` best-effort before Qwen (also after outcome). `core/event_bus` still best-effort.

---

## 4. Strategy Attribution (Phase 20 preserved, now governed)

Hierarchy `FAN_TOPIC_HISTORY > FAN_HISTORY > CREATOR_TOPIC_HISTORY > CREATOR_HISTORY > SAFE_DEFAULT` via `select_strategy_adaptive` now wrapped by `strategy_governed_selection_compat(evidence_map, eligible, topic, product_family, lifecycle_stage, objective, fatigue_map, pressure, risk_state, regression_map)` which:
- If `risk_state in (SUPPRESS,HANDOFF)` or `pressure.bucket==suppress` with commercial objective → immediate `SAFE_DEFAULT` (no learning override).
- If `regression_map[strategy]==True` → filtered eligible, if empty → SAFE_DEFAULT.
- Else delegates to Phase 20 adaptive (Beta, fatigue, budget, hierarchy). No duplication, composition only.

Regression integration: `detect_regression(current, baseline)` thresholds 0.20 conversion decline etc. → `regression_map` for governed selection.

---

## 5. Outcome Taxonomy (Phase 20 re-used)

Canonical 18 via `adaptive_optimization:CanonicalOutcome` re-exported in `conversation_outcomes`: NO_SIGNAL … CONVERSATION_END; `classify_canonical_outcome()` observable only, `get_outcome_weight()`/`outcome_strength()`, lifecycle-adjusted `lifecycle_specific_outcome_weights()`. No change.

---

## 6. Strategy Score (Phase 20 re-used, now pressure-aware)

`strategy_score(ev, fatigue_penalty, topic_relevance, recency_days)` explainable bounded 0..1 (base `positive_rate*decay + purchase_bonus - neg_penalty - uncertainty*0.2 - fatigue + topic_boost`). New pressure-aware suppression via governed selection (not inside score) to keep score explainable.

---

## 7. Uncertainty (Phase 20 re-used)

`beta_uncertainty(a=positive+1,b=attempt-positive+1)` std clamp 0.02..0.5, `estimated_performance = positive_rate*(1-unc)+0.5*unc`. No change.

---

## 8. Exploration / Exploitation (Phase 20 + governance)

Modes `EXPLORE/EXPLOIT/SAFE_DEFAULT`, `should_explore`, `exploration_budget_ok(actual<rate 0.10)`, `AUTHORITY_GATES`. Governed selection respects `risk_state` and `regression_map` before exploration. Never explores by price/product.

---

## 9. Fatigue (Phase 20 + extended)

`compute_fatigue`, `fatigue_penalty_map`, `is_response_mode_fatigued`, `is_question_pattern_fatigued`, `is_product_family_fatigued` re-used; new `is_spam_risk(recent_exposures, strategy, product_family, question_count_last_3, reengagement_count_last_7d)` checks `same_strategy≥3/5`, `same_product_family≥3/5`, `questions≥2/3`, `reengagement≥3/7d`. `policy_allows(repeated_questions, spammy_reengagement)` blocks.

---

## 10. Purchase Attribution (Phase 20 re-used)

`attribute_purchase` window direct ≤24h / assisted ≤7d / organic + `has_valid_purchase_evidence(transaction_id, dropfans_record)` sole authority. No change.

---

## 11. Lifecycle Metrics (Phase 20 + LifecycleState)

`LifecycleState` 15 now explicit; `derive_lifecycle` respects objective/strategy/pressure/offer/re-engagement; pressure includes lifecycle +0.20 for aftercare/cooldown/rejected, -0.05 for new/curious. Metrics `compute_relationship_metrics` vs `compute_commerce_metrics` remain separate and are now demonstrated as used in governance (pressure uses commerce counts, relationship not purchase).

---

## 12. Experiment Contract (Phase 20 extended)

`Experiment` dataclass as before + `experiment_governed_assignment` with `minimum exposure threshold 5` before EXPERIMENT else CONTROL, deterministic hash `SHA256(creator:user:exp)`, `experiment_safe_to_apply` forbids price/product/purchase/creator/isolation, `is_active` start/end/status, `clear_experiments/disable_experiment/persist_experiment` sentinel. No activation in prod.

---

## 13. Safety Boundaries

Hierarchy §44: Safety(1) > Identity(2) > Truthfulness(3) > Purchase authority(4) > Aftercare(5) > Objection(6) > Explicit request(7) > Conversation intelligence(8) > Commerce readiness(9) > Strategy learning(10) > Experimentation(11) > LLM wording(12). Enforced via `conversation_intelligence` priority + `is_strategy_allowed` + `validate_no_authority_bypass` + `AUTHORITY_GATES` + `policy_allows` before/after Qwen + scoring authority check.

---

## 14. Telemetry (Phase 21 extended)

`GenerationTelemetry` now 20 fields (Phase 17 8 + Phase 20 8 + Phase 21 8, but actually 12+8=20). `decision_trace` compact `OBJECTIVE=... REASON=... STRATEGY=... SOURCE=... CONFIDENCE=... MODE=... PRESSURE=... FATIGUE=... RISK=... EXPERIMENT=...:variant RESPONSE_MODE=... QUESTION_POLICY=... ALLOWED=... HANDOFF=...` bounded <500, no message content, no secrets, generation-scoped. Best-effort `insert_generation_telemetry`.

---

## 15. Tests (Phase 21)

File `tests/test_phase21_conversation_operations.py` 48 tests covering §34 A-Z plus lifecycle + telemetry:

- A unified decision authoritative
- B pressure suppresses selling
- C anti-spam repeated offer → pressure+fatigue → suppress
- D rejection ≥3 → cannot pitch
- E aftercare suppresses upsell
- F cooldown suppresses commercial
- G high engagement without intent does not auto-offer
- H explicit purchase can offer when gates pass
- I handoff blocks autonomous (persisted restricted, lifecycle HANDOFF, commerce/memory survive)
- J regressed strategy filtered (history not deleted)
- K unsafe experiment rejected (policy gate)
- L stable assignment deterministic
- M retryable/permanent/degraded/handoff classification + degraded_fallback
- N degraded Qwen safe_fallback no hallucination (invented price→block)
- O DropFans failure commerce_suppressed no fabricated product/URL/purchase
- P creator isolation (handoff memory, exposure buffer keyed creator, deterministic hash)
- Q decision trace contains metadata no content bounded <500 structured
- R single-pass 1/1/1/0 + no new workers
- S permanent invalid peer → DLQ+ACK not retry
- T stalled → RETRYABLE reclaimed still sends
- U duplicate dedup md5 idempotent
- V relationship vs commerce separate metrics
- W resolved open loop not repeatedly followed up (importance <0.7 or RESOLVED)
- X re-engagement governed 48h+aftercare/cooldown/rejection/relevance/pressure/fatigue/max-frequency 2/7d+dedup
- Y least-observed explore within 0.10 budget
- Z SAFE_DEFAULT fallback
- Lifecycle coherence, telemetry Phase 21 fields no secrets

All deterministic, no DB, no network.

---

## 16. Bounded Retention & Performance

- `strategy_exposures_by_creator` 50 ring + 30d prune
- `strategy_evidence_by_creator` 20 (10 flat +10 composite) + generation_seen 100
- `handoff_by_creator` few, bounded
- `experiments_by_creator` few
- All `user_profiles` JSONB, no migration
- Performance: pure math, bounded loops (eligible ≤20, exposures 5), O(1) experiment hash, no N+1.

---

## 17. What Was NOT Built (§45 forbidden)

No RL, no neural, no fine-tune, no second LLM, no deterministic commerce replacement, no LLM price/product authority, no Redis Streams replacement, no Celery/Kafka, no new worker/queue, no Telethon replacement, no scoring/DLQ removal, no creator isolation weakening, no DropFans bypass, no random experimentation, no revenue-only optimization, no raw content in telemetry.

---

## 18. Wire Points

| File | Change | Type |
|------|--------|------|
| `commerce/conversation_operations.py` | NEW 751 LOC deterministic enterprise library (Lifecycle, Pressure, Risk, FailureClass, policy_allows, HandoffState, strategy_governed_selection, is_spam_risk, ConversationOperationDecision, build_operation_decision, is_reengagement_governed_allowed) | **NEW** |
| `core/telemetry.py` | +8 fields `operation_allowed/block_reason/pressure_score/risk_state/handoff_required/failure_class/decision_trace/lifecycle_state` + to_dict | **MODIFIED** |
| `workers/llm_worker.py` | After conversational bridge: compute_pressure + derive_risk + derive_lifecycle + build_operation_decision → telemetry pressure_score/risk_state/operation_allowed/block_reason/decision_trace/lifecycle_state/handoff_required (best-effort, no LLM) | **MODIFIED** |
| `workers/scheduler_worker.py` | Re-engagement loop: before schedule, compute governed pressure+fatigue+is_reengagement_governed_allowed (best-effort) | **MODIFIED** |
| `commerce/adaptive_optimization.py` | No change (reused) | — |
| `tests/test_phase21_conversation_operations.py` | NEW 48 tests A-Z + lifecycle + telemetry | **NEW** |
| `docs/AI_NATIVE_COMMERCE_PHASE_21_FORENSIC_AUDIT.md` | Stage A forensic (capability matrix) | **NEW** |
| `docs/AI_NATIVE_COMMERCE_PHASE_21_IMPLEMENTATION_MAP.md` | This file | **NEW** |
| `docs/AI_NATIVE_COMMERCE_PHASE_21_FINAL_REPORT.md` | Stage B final report | **NEW** |

---

## 19. Single-Pass & Architecture Confirmation

- Calls per turn: `1 extract_commerce_signals` (commerce/deepseek) + `1 get_llm_provider().generate_with_history` (ollama/qwen2.5:3b) + `1 score_draft` (authority-aware). `verify_single_pass()` asserts `additional_llm==0`. Phase 21 adds `0` LLM calls (all deterministic).
- Architecture: **NO REDESIGN** — Redis Streams/consumer groups `llm_workers` XAUTOCLAIM 30s, PostgreSQL raw SQL, Telethon, 3 workers, DropFans sole, DLQ/dedup/rate limiting, creator isolation, single-pass preserved. Provider unchanged, canary not activated.

Implementation map complete. Next: `docs/AI_NATIVE_COMMERCE_PHASE_21_FINAL_REPORT.md` with 32 sections + final verdict.
