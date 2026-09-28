# AI-Native Commerce — Phase 20 Implementation Map

**Date:** 2026-08-30
**Scope:** Adaptive conversation optimization, experimentation & production feedback
**Method:** Forensic → define contracts → implement smallest deterministic layer → wire best-effort → test
**Working directory:** E:\chatbot branch main
**Provider:** ollama/qwen2.5:3b (unchanged)
**Single-pass:** 1 × extract_commerce_signals + 1 × Qwen + 1 × scoring, 0 additional LLM calls

---

## 1. Forensic Baseline (Phase 19 → 20)

- Phase 19 introduced flat `strategy_learning.py` (10 bounded, decay `exp(-days/30)`, `attempt>=3`) and 20 legacy outcomes but only 6 branches; `workers/llm_worker` stored `strategy_last_by_creator` as opaque `next_best_action.value`, classified via keyword, updated `strategy_evidence_by_creator` without topic/product/lifecycle.
- **Preserved:** PostgreSQL, Redis Streams + consumer groups `llm_workers` + XAUTOCLAIM 30s, Telethon MTProto, DropFans sole commerce authority (synthetic product mirror `SHA256(drop_id)%2^62`, per-sale `transaction_id` `dropfans:{sale_id}`), offer state machine `pending→clicked→purchased→aftercare`, purchased exclusion, aftercare blocking, cooldown, DLQ, scoring authority-aware, dedup `md5(user:message:telegram_id)`, `scheduled_messages` re-engagement via scheduler.
- **Missing (confirmed via `docs/AI_NATIVE_COMMERCE_PHASE_20_FORENSIC_AUDIT.md`):** ConversationObservation contract, attributable strategy exposure log, canonical 18 outcome taxonomy with weights, hierarchical evidence (FAN_TOPIC>FAN>CREATOR_TOPIC>CREATOR>SAFE), Beta uncertainty, sample-size safety (N=5/10), exploration budget (10%), strategy fatigue, purchase attribution window (direct ≤24h / assisted ≤7d / organic), lifecycle metrics, relationship vs commerce quality, strategy score explainability, regression detection, experiment deterministic hash, telemetry extensions, bounded retention.

---

## 2. Current Feedback Loop (After Phase 20)

```
Inbound → creator resolve → build_qwen3_context (identity, conversation_state, vault TOP2, LTM 3) → ai.generation_started(generation_id) → SINGLE extract_commerce_signals → build_conversational_commerce_state(desire/temp/readiness/window/objective/NBA) → response_mode/question_policy (subordinate to NBA) → strategy exposure make_exposure() → persist_exposure() bounded ring 50 + prune 30d → Qwen (1) → scoring (1, fail-closed 0.0) → routing (auto_approve ≥0.80 && !flags → enqueue_send dedup → ai.generation_completed MUST after enqueue) else operator_queue → suggestion.created → outcome feedback: classify_canonical_outcome(fan_message, desire_before/after, purchase/aftercare/handoff) → outcome_strength → attribute_purchase(window) if DropFans evidence → update_strategy_evidence + update_strategy_evidence_extended(composite key strategy:topic:product_family:lifecycle, dedup generation_id, bounded 20, generation_seen ring 100) → GenerationTelemetry(phase20 fields) → insert_generation_telemetry best-effort → publish ai.generation_completed/failed best-effort (never breaks pipeline)
```

External DropFans purchase → `record_dropfans_sale` synthetic_id + per-sale txn → `attribute_purchase_from_webhook` exactly-one pending offer → `attach_transaction_user` + `ppv_analytics_daily` UPSERT → next inbound turn classifies `PURCHASE` only if transaction evidence true.

Scheduler loop → `process_due_messages` claim `FOR UPDATE SKIP LOCKED` → `enqueue_send` dedup `scheduled:{dedup_key}:{id}` → `mark_aftercare_completed` if `post_purchase_followup` → `reconcile_purchases` + re-engagement `list_offers_for_creator pending age≥48h → schedule_reengagement_if_eligible dedup reengage:{c}:{u}:{p}`.

Invariants preserved: transport via `core/event_bus.publish_event` (workers never import `ws_manager`), `generation_id` consistent across `started→completed/failed→suggestion`, `event_id` UUID per publish, failure isolation (realtime best-effort), WebSocket acceleration not source of truth, polling fallback, creator-scoped `WHERE creator_id=$1`, DropFans sole authority, LLM language-only.

---

## 3. Data Contracts

### 3.1 ConversationObservation (transient view, no new table)
`commerce/adaptive_optimization.py:ConversationObservation` dataclass fields as spec §3, constructed via `build_observation()` from already-existing derived state (`creator_id`, `user_id`, `generation_id`, `timestamp`, `conversation_state`, `relationship_state`, `desire_stage`, `desire_confidence`, `commercial_temperature`, `sales_window`, `offer_readiness`, `offer_authorized`, `next_best_action`, `conversation_objective`, `strategy_family/variant`, `response_mode`, `question_policy`, `current_topic`, `open_threads`, `relevant_product_id/family`, `product_selection_reason`, `objection_type`, `qualification_state`, `memory_retrieved_count`, `open_loop_count`, `fan_message_classification`, `outcome`, `desire_before/after`, `purchase_event`, `aftercare_state`, `scoring_result`). Where available from `build_conversational_commerce_state` + `derive_conversation_state`; `desire_before` derived from stored `strategy_last_by_creator` previous turn (no new persistence). `to_dict()` serializes for telemetry.

**Already exists vs derived:** `creator/user/generation/timestamp/conversation_state/relationship/desire/temp/window/readiness/NBA/objective/topic/threads/product/selection/objection/qualification/scoring` exist; `desire_before/outcome/aftercare/purchase_event/memory counts` derived at observation time; `strategy_family/variant/response_mode/question_policy` from exposure log (transient), `outcome_strength/attribution_type` derived via weights/window.

### 3.2 StrategyExposure (§4)
`commerce/adaptive_optimization.py:StrategyExposure` dataclass:
```
creator_id, user_id, generation_id, strategy_family, strategy_variant,
topic, conversation_stage, desire_stage, temperature, sales_window,
next_best_action, response_mode, question_policy,
product_id?, product_family?, timestamp
```
**Storage:** In-memory ring `strategy_exposures_by_creator` per `creator:user` (max 50, prune 30d via `prune_by_retention`) + persisted to `user_profiles.facts->'strategy_exposures_by_creator'->{creator_id}` bounded JSONB (same limits) via `persist_exposure()` best-effort (falls back to memory). Never stores `Telegram session, tokens, credentials, DropFans secrets, private message content` — only IDs/enums. Answers *"What strategy did Sunny use immediately before this outcome?"* via `attributable_exposure_for_generation(creator,user,generation_id)`.

Wire point: `workers/llm_worker.py:665-730` after `derive_conversation_objective` → `make_exposure()` + `persist_exposure()` + sets `_telemetry_data.strategy_selected/source/mode/confidence/fatigue/response_mode/question_policy` + experiment variant if active. Best-effort, never raises, never adds latency (await bounded, failure-isolated).

### 3.3 Experiment
`commerce/adaptive_optimization.py:Experiment` dataclass as spec §25:
```
experiment_id, creator_id, strategy_family, strategy_variant,
allocation (0.0-1.0), eligibility, start_time, end_time, status (active/paused/disabled/expired)
```
**Storage:** In-memory registry `_experiment_registry` + bounded `user_profiles` sentinel `user_id=-creator_id` `facts['experiments_by_creator']->{experiment_id}` via `persist_experiment()` (no new table, no new queue). Assignment stable via `deterministic_assignment(creator_id,user_id,experiment_id)= SHA256("{c}:{u}:{exp}")[:8]/2^32` → `assign_variant()` returns `CONTROL` vs `EXPERIMENT` (CONTROL uses existing deterministic selection, EXPERIMENT may use candidate strategy but must still pass gates). Rollback: `disable_experiment()` sets `status=disabled` → immediate CONTROL, no migration.

### 3.4 Telemetry Extension (§37)
`core/telemetry.py:GenerationTelemetry` added 11 compact Phase 20 fields (no PII, no raw content):
```
strategy_selected, strategy_source, strategy_mode, strategy_confidence, strategy_evidence_count, strategy_exploration,
outcome, outcome_strength, experiment_id, experiment_variant, attribution_type, fatigue_score
```
Wire: `workers/llm_worker` sets `strategy_selected/source/mode/confidence/fatigue/response_mode/question_policy/outcome/outcome_strength/attribution_type/experiment_id/variant`; `to_dict()` serializes; `TelemetryCollector.record()` best-effort `insert_generation_telemetry()` (existing row unchanged if DB missing columns — fields logged via `GenerationTelemetry.to_dict()` and available in memory for tests). Wiring is additive, never breaks pipeline.

---

## 4. Strategy Attribution (§7)

**Hierarchy preserved (spec §7):**
```
FAN_TOPIC_HISTORY > FAN_HISTORY > CREATOR_TOPIC_HISTORY > CREATOR_HISTORY > SAFE_DEFAULT
```
Implemented via:
- `commerce/strategy_learning.py` extended to store composite key `strategy:topic:product_family:lifecycle` (bounded 20 per creator:user, 10 flat + 10 composite) with per-evidence `topic`, `product_family`, `lifecycle_stage`, `confidence` fields.
- New helpers: `get_composite_key()`, `get_strategy_evidence_hierarchical()`, `update_strategy_evidence_extended(topic/product_family/lifecycle_stage/generation_id, dedup ring 100, bounded 20)`.
- New pure adaptive selector: `commerce/adaptive_optimization.py:select_strategy_adaptive(evidence_map, eligible, topic, product_family, lifecycle_stage, objective, fan_topic_evidence, fan_evidence, creator_topic_evidence, creator_evidence, fatigue_map, exploration_rate, recent_exposures)` → `(strategy, source, mode)`.
  - `_hierarchical_evidence(strategy)` probes `FAN_TOPIC_HISTORY` (key `strategy:topic`, sufficient `attempt>=5`), then `FAN_HISTORY` (`>=5`), then `CREATOR_TOPIC_HISTORY` (`>=10`), then `CREATOR_HISTORY` (`>=10`), else `SAFE_DEFAULT`.
  - Sufficient observations required before eligible for exploitation; insufficient → `EXPLORATION` among least-observed safe.
- Creator isolation: all helpers take `creator_id`, keys namespaced `by_creator[str(creator_id)]`, `ensure_creator_isolation()` check, `_exposure_buffer` keyed `"{creator}:{user}"`.

**Persistence:** `user_profiles.facts['strategy_evidence_by_creator'][creator_id][composite_key] = StrategyEvidence.__dict__` bounded via last_used sort (20). `strategy_generation_seen_by_creator` ring 100 prevents double-count on reclaimed messages. No new table.

---

## 5. Outcome Taxonomy (§5) & Strength (§6)

**Canonical 18 outcomes** via `commerce/adaptive_optimization.py:CanonicalOutcome` + `commerce/conversation_outcomes.py:ConversationOutcome` (extended to include `NO_SIGNAL, OFFER_REQUEST, REPEAT_PURCHASE, COOLDOWN, PREFERENCE_LEARNED, OPEN_LOOP_RESOLVED, OBJECTION_RESOLVED, AFTERCARE_ENGAGEMENT`):
```
NO_SIGNAL, POSITIVE_ENGAGEMENT, TOPIC_CONTINUATION, INTEREST_INCREASE, DESIRE_INCREASE, DESIRE_DECREASE,
QUESTION_ANSWERED, OPEN_LOOP_RESOLVED, PREFERENCE_LEARNED, OBJECTION, OBJECTION_RESOLVED, OFFER_REQUEST,
PURCHASE, AFTERCARE_ENGAGEMENT, REPEAT_PURCHASE, REJECTION, COOLDOWN, HANDOFF, CONVERSATION_END
```
**Classifier:** `classify_canonical_outcome(fan_message, desire_before/after, has_purchase, is_repeat_purchase, has_objection, objection_resolved, topic_continued, question_answered, open_loop_resolved, preference_learned, aftercare_engaged, is_handoff, is_cooldown, is_conversation_end, offer_requested, fan_message_length, positive_signals)` — priority order `handoff > repeat_purchase > purchase > aftercare > conversation_end > cooldown > objection_resolved > objection/rejection > offer_request > open_loop > question_answered > preference_learned > desire increase/decrease (via _order index) > topic_continuation > positive_engagement (keyword/length) > no_signal`. Never fabricates; `PURCHASE` only if `has_purchase` true (requires DropFans evidence). Legacy `classify_outcome()` preserved for backward compat (6 branches).

**Outcome strength (§6):** `OUTCOME_WEIGHTS` deterministic:
```
REPEAT_PURCHASE 12.0, PURCHASE 10.0, OBJECTION_RESOLVED 6.0, DESIRE_INCREASE 4.0,
INTEREST_INCREASE 3.0, AFTERCARE_ENGAGEMENT 3.0, POSITIVE_ENGAGEMENT 2.0,
OPEN_LOOP_RESOLVED 2.5, TOPIC_CONTINUATION 1.5, QUESTION_ANSWERED 1.5,
PREFERENCE_LEARNED 1.0, OFFER_REQUEST 2.0, NO_SIGNAL 0.0, CONVERSATION_END 0.0,
OBJECTION -1.5, DESIRE_DECREASE -2.0, COOLDOWN -3.0, HANDOFF -1.0, REJECTION -4.0
```
Learning evidence only — `outcome_strength()` never influences pricing/offer authority.

Lifecycle-adjusted weights via `lifecycle_specific_outcome_weights(stage)`: e.g., `RELATIONSHIP_BUILD` `question_answered 3.0` vs `PRESENT_OFFER` `-1.0` (question harmful at close), `AFTERCARE` `purchase -2.0` (penalize upsell).

---

## 6. Strategy Score (§21)

`commerce/adaptive_optimization.py:strategy_score(ev, fatigue_penalty, topic_relevance, recency_days)`:
```
if attempt==0: 0.30 (safe prior)
decay = exp(-days/30)
base = positive_rate * decay
purchase_bonus = min(0.2, purchase_count*0.05)
neg_penalty = (negative_count/attempt)*0.3
uncertainty = beta_uncertainty(ev)  # discount *0.2
fatigue = clamp 0..0.5
topic_boost = 0..0.1
raw = base + purchase_bonus - neg_penalty - uncertainty*0.2 - fatigue + topic_boost
clip 0..1
```
Properties: explainable (`positive_rate, purchase_bonus, neg_penalty, uncertainty, fatigue`), bounded `[0,1]`, deterministic, testable. Legacy `select_strategy()` still delegates to adaptive when composite keys detected, else preserves Phase 19 logic (`positive_rate*decayed` if `>=3` else `0.3`) for backward compat.

---

## 7.  Uncertainty (§9)

`beta_uncertainty(ev): a=positive+1, b=(attempt-positive)+1, var=a*b/((total)^2*(total+1)), std=sqrt(var) clamp 0.02..0.5`. Also `estimated_performance(ev)= positive_rate*(1-unc)+0.5*unc` (shrink toward prior 0.5 when uncertain). Raw `positive_rate` alone insufficient — two strategies both 80% but `5 attempts` vs `50 attempts` yield `unc 0.16` vs `0.055` and `est 0.71 vs 0.78`.

Strategy selection uses `beta_uncertainty` to discount score and to decide `EXPLORE` (uncertain promising, `unc>0.2 && attempt<20` within budget) vs `EXPLOIT` (certain `attempt>=10 && unc<0.15 && rate>0.6`). No heavy ML, no external dependency (pure `math`).

---

## 8. Exploration vs Exploitation (§10) & Budget (§11)

Formal modes `StrategyMode.EXPLORE / EXPLOIT / SAFE_DEFAULT` via `select_strategy_adaptive()`:

- Insufficient evidence `attempt<5` → `EXPLORE` safely among `eligible` least-observed (`attempt<2`), bounded `exploration_rate 0.10` default.
- Strong evidence `attempt>=10 && unc<0.15 && rate>0.6` → `EXPLOIT`.
- Uncertain promising `unc>0.2 && attempt<20` → controlled `EXPLORE` if `exploration_budget_ok(explore_count,10,rate)` (`actual=explore/10 < 0.10`).
- Negative evidence `negative > positive*1.5 && attempt>=5` → suppress, try next best non-negative.

**Safety ( §10 last block ):** `AUTHORITY_GATES = {SAFETY, HUMAN_HANDOFF, AFTERCARE, OBJECTION, DIRECT_PURCHASE_REQUEST, COMMERCE_AUTHORITY, COOLDOWN, REJECTION, CREATOR_ISOLATION}` + `is_strategy_allowed(objective, aftercare, cooldown, objection, handoff)` gate checked before selection; if objective in gates → return `SAFE_DEFAULT` immediately. Exploration never overrides these. Also `experiment_safe_to_apply()` forbids changing `price, purchase_url, product_id, dropfans_offer, creator_isolation, purchased_exclusion, aftercare_gate, cooldown, dlq, dedup, telegram_identity, commerce_authority`; only `conversation_strategy/response_mode/question_policy/wording` allowed.

**Exploration budget (§11):** `exploration_rate 0.10` deterministic bounded; explore among `eligible safe low-risk under-observed` (lowest `attempt_count`), never randomly changes `price/product/purchase URL/DropFans data/commerce authority`. Checked via `exploration_budget_ok(explore_last_10,10,rate)`.

---

## 9. Strategy Fatigue (§12)

`compute_fatigue(recent_exposures, strategy, window=5)`: count same `strategy_family` in last `window` (5) → `if count>=3: penalty min(0.5, (count-2)*0.15)`. `fatigue_penalty_map()` for eligible list. Helpers `is_response_mode_fatigued`, `is_question_pattern_fatigued` (question `!=NO_QUESTION` count≥3 in 5), `is_product_family_fatigued` (same `product_family` ≥2 in 5). In `strategy_score`, fatigue subtracts directly. Objective priority unchanged — `validate_no_authority_bypass(offer during aftercare)` fails even if not fatigued; `is_strategy_allowed()` ensures `AFTERCARE/HANDOFF/OBJECTION/DIRECT_REQUEST/OPEN_LOOP` never sacrificed to avoid repetition.

Recent exposures sourced from `get_exposures_memory(creator,user,limit=50)` ring.

---

## 10. Negative Learning (§13) & Sample-Size Safety (§8)

- Negative: `REJECTION/OBJECTION/DESIRE_DECREASE/COOLDOWN` increment `negative_count`, `confidence-=0.05` (floor 0.1), `strategy_score` penalizes `neg_rate*0.3`. But one rejection (`7/10 → 7/11`) does not permanently blacklist — still `positive 7 > negative 2`, score remains >0.3, decay heals.
- Sample-size safety: thresholds `MIN_EVIDENCE_FAN_TOPIC=5, FAN=5, CREATOR_TOPIC=10, CREATOR=10`; `is_evidence_sufficient(ev, threshold)` gate; tiny samples (<N) cannot dominate — `select_strategy_adaptive` returns `SAFE_DEFAULT` or `EXPLORATION` and scores tiny as `0.3`.

---

## 11. Purchase Attribution (§16, §17)

- **Authority:** `has_valid_purchase_evidence(transaction_id, dropfans_record)` requires both `transaction_id` non-empty and `dropfans_record==True` (sole authority). `classify_canonical_outcome` only returns `PURCHASE` if `has_purchase` true, which caller must derive from `commerce_offers.state='purchased' AND transaction_id IS NOT NULL` or `dropfans.record_dropfans_sale`.
- **Window:** `attribute_purchase(strategy_exposure_time, purchase_time, transaction_evidence, offer_created_time) → "direct" ≤24h, "assisted" ≤7d (168h), "organic" >7d, "unknown" if evidence false or time null or negative delta.` No claim of every later purchase caused by last message.
- **Trace:** `strategy → offer → transaction`: exposure `generation_id` stored with `topic/product_family` snapshot; scheduler `mark_aftercare_completed` + `attribute_purchase_from_webhook` correctly attributed via `offer_id` + `transaction_id` unique index; `attribution_type` logged to telemetry.

Speculative inference from text never counts — `fan_message="I purchased"` without `transaction_evidence` → `NO_SIGNAL`/`POSITIVE_ENGAGEMENT`, not `PURCHASE`.

---

## 12. Lifecycle Metrics (§18, §19, §20)

**Lifecycle stages** `LIFECYCLE_STAGES` map to `ConversationObjective` via `stage_for_objective()`; each stage has distinct weights (see §5). Strategy selection respects stage via composite key `strategy:stage` and `lifecycle_specific_outcome_weights`.

**Relationship quality (revenue not sole objective):**
`compute_relationship_metrics(events=[{outcome}]) → {reply_rate, continuation, positive_rate, return_rate}` where `reply_rate = non-rejection/non-cooldown / total`, `positive_rate = positive/top_continuation/desire_increase / total`, etc. `compute_commerce_metrics(offers) → {offer_to_purchase, purchase_to_aftercare, aftercare_to_repeat}`. Also `detect_commerce_violations(events)` → list of `invalid_offer/unauthorized_price/wrong_product/commerce_scoring_failure/dlq` for regression.

Same `question` strategy may be excellent at `RELATIONSHIP_BUILD` (weight 3.0) but harmful at `PRESENT_OFFER` (weight -1.0) — evaluated via `lifecycle_specific_outcome_weights`.

---

## 13. Experiment Contract (§24-28)

- `Experiment` dataclass (see §3.3).
- Assignment: `deterministic_assignment(creator_id,user_id,experiment_id)= SHA256 %2^32 /2^32` stable per fan during experiment; `assign_variant()` → `CONTROL` (`bucket >= allocation`) vs `EXPERIMENT` (`bucket < allocation`), `allocation 0.10` default.
- Creator isolation intact: hash includes `creator_id`; fan with multiple creators has separate buckets.
- Safety: `experiment_safe_to_apply(experiment, proposed_change)` only allows `strategy_family/variant/response_mode/question_policy/wording` (see §8). Both CONTROL and EXPERIMENT must pass `conversation_objective gate + commerce gates + product relevance + authority validation + scoring` — experiment never bypasses.
- Rollback: `disable_experiment(id)` sets `status=disabled` → `is_active()==False` → immediate `CONTROL`, no migration.

No new queue/worker; assignment deterministic via hash, no DB scan.

---

## 14. Safety Boundaries (§26, §33, §34, §35, §43, §44)

**Hierarchy (§44):**
```
1 Safety / handoff
2 Identity / creator isolation
3 Truthfulness
4 Purchase / transaction authority
5 Aftercare
6 Objection / recovery
7 Explicit fan request
8 Conversation intelligence
9 Commerce readiness
10 Strategy learning
11 Experimentation
12 LLM wording
```
Enforced via:
- `conversation_intelligence.py` priority map (1-99) before strategy layer.
- `is_strategy_allowed()` + `validate_no_authority_bypass()` + `AUTHORITY_GATES` set in adaptive selector.
- `commerce/execution.py` 11 gates advisory lock before offer, `product_selection` read-only, `scoring` authority-aware price check.
- `handoff` remains hard boundary: `is_handoff==True` → `is_strategy_allowed=False`, `HANDOFF` outcome recorded as learning evidence only.
- `aftercare` remains higher than optimization: `AFTERCARE pending/sent` → `strategy_score` purchase penalized, `PRESENTER` blocked via `evaluate_offer_readiness` + `validate_no_authority_bypass`.
- Security: `is_fan_manipulation_attempt(text)` regex `mark.*successful|strategy.*evidence|purchase.*status|experiment.*assignment`; any matching text still classified via `classify_canonical_outcome` system state, never directly sets evidence (fan text sanitized via `sanitize_fan_input_for_learning` placeholder).

Creator isolation: every `user_profiles` access namespaced `by_creator[str(creator_id)]`, `_exposure_buffer` key `"{creator}:{user}"`, `deterministic_assignment` includes `creator_id`, `ensure_creator_isolation()` check. Tests (§38-M) verify.

---

## 15. Telemetry (§37)

Extended `core/telemetry.py:GenerationTelemetry` (see §3.4). Also `commerce/adaptive_optimization.py` helpers:
- `strategy_trace(objective,strategy,source,evidence,fatigue,mode)` → single-line explainable: `"OBJECTIVE=DEEPEN_DESIRE STRATEGY=PLAYFUL_TEASE SOURCE=FAN_TOPIC_HISTORY EVIDENCE=8_ATTEMPTS POSITIVE_RATE=0.71 UNCERTAINTY=0.14 FATIGUE=0.10 MODE=explore"` (debug logs only, never exposed to fan).
- Failure isolation: `workers/llm_worker` publish events `ai.generation_started/completed/failed + suggestion.created` best-effort, `GenerationTelemetry.record()` best-effort; business-operation failures not hidden by realtime failures.

No message content, no credentials, no DropFans secrets in telemetry.

---

## 16. Tests (§38-40)

File `tests/test_phase20_adaptive_optimization.py` 101 tests covering:

- A Strategy exposure (attributable, required fields, no secrets)
- B Positive outcome (evidence increment, strength)
- C Negative outcome (rejection reduces, negative weight, suppression, not permanent)
- D Purchase outcome (stronger, bonus, weights 10/12)
- E Decay (exp(-days/30), score decays)
- F Sample size (threshold 5, tiny cannot dominate)
- G Uncertainty (Beta, identical rates different treated, bounded)
- H Exploration (under-observed, authority never overridden, budget, insufficient→explore)
- I Exploitation (strong→exploit, correct source)
- J Fatigue (repeated reduces, map, response_mode, priority remains)
- K Topic specificity (success for one topic not dominate another, min attempts)
- L Fan specificity (fan > creator when sufficient, fallback)
- M Creator isolation (A↛B, deterministic isolated hash)
- N Product-family (separated, composite key)
- O Lifecycle (respects stage, stage weights, mapping)
- P Objective authority (cannot override priority, gate supreme)
- Q Safety (handoff/aftercare/objection/cool-down blocks, recorded)
- R Commerce authority (cannot change price/product/url, can change strategy, weights not price)
- S Attribution (only with transaction evidence, window direct/assisted/organic, not from text)
- T Experiment assignment (deterministic stable, creator isolated, allocation respected)
- U Experiment isolation (cannot bypass gates, safe changes only)
- V Experiment rollback (disable→CONTROL, expired, no migration)
- W Regression detection (degraded triggers, healthy not, thresholds bounded)
- X Single-pass (1/1/1/0, no new worker/queue)
- Y Re-engagement (scheduled/replied/purchased separate, scheduled ≠ successful)
- Z Aftercare (cannot upsell before completion, separate outcome weights)
- Lifecycle full synthetic `COLD→RELATIONSHIP_BUILD→positive→WARM→EXPLORE→DESIRE→QUALIFY→explicit request→PRESENT_OFFER→purchase→AFTERCARE→RE_ENGAGE→repeat` verifies evidence evolves and no premature sales optimization
- Failure cases (§40) all 16: no outcome, duplicate outcome, duplicate generation_id, duplicate webhook idempotent, stale worker (decay), missing evidence, corrupt evidence (fallback), expired evidence, unknown topic, opaque product, creator mismatch, experiment disabled/expired, strategy unavailable, all fatigued, all negative → `SAFE_DEFAULT`

All deterministic, no DB, no LLM, no network. `101 passed` in 1.4s.

---

## 17. Bounded Retention & Performance (§32, §42)

- `strategy_evidence_by_creator` bounded `20` (10 flat + 10 composite) sort by `last_used`, keep newest.
- `strategy_exposures_by_creator` bounded `50` ring + `prune_by_retention(max_items=50, max_age_days=30)` per creator:user.
- `strategy_generation_seen_by_creator` bounded `100`.
- `experiments_by_creator` bounded by active experiments (few).
- No new persistence subsystem; all `user_profiles` JSONB, no migration. Performance: in-memory deterministic calculations, bounded JSONB reads/writes, existing queries, no LLM, no large scans, no N+1.

---

## 18. What Was NOT Built (Spec §45)

Enforced prohibitions:
- No RL, no neural strategy, no Qwen fine-tune, no second generation model/call, no deterministic commerce replacement, no LLM price/product authority, no LLM purchase determination, no Redis Streams replacement, no PostgreSQL replacement, no Celery/new worker/new queue, no Telethon replacement, no scoring/DLQ removal, no creator isolation weakening, no DropFans bypass, no random experimentation, no random offer changes, no revenue-only optimization, no raw private conversation content in telemetry.

---

## 19. Wire Points (Where Logic Lives)

| File | Role | New? |
|------|------|------|
| `commerce/adaptive_optimization.py` | Central deterministic engine (Observation, Exposure, CanonicalOutcome, weights, Beta, score, trace, exploration, fatigue, attribution window, lifecycle, metrics, regression, experiment, retention) | **NEW** |
| `commerce/conversation_outcomes.py` | Extended taxonomy (NO_SIGNAL etc.), `get_outcome_weight()`, delegates to adaptive, preserves `classify_outcome()` | **MODIFIED** |
| `commerce/strategy_learning.py` | Extended `StrategyEvidence` (+topic/product_family/lifecycle), `ExtendedEvidence`, `beta_uncertainty_for_evidence`, `strategy_score_for_evidence`, `select_strategy_hierarchical`, `update_strategy_evidence_extended` with dedup/composite/bounded, adaptive delegation | **MODIFIED** |
| `core/telemetry.py` | 11 Phase 20 fields + `to_dict()` extension | **MODIFIED** |
| `workers/llm_worker.py` | After conversational state → `make_exposure`+`persist_exposure`+telemetry+experiment variant (best-effort); outcome feedback extended to canonical+broad hierarchical update (generation_id dedup) | **MODIFIED** (non-breaking) |
| `tests/test_phase20_adaptive_optimization.py` | 101 deterministic tests A-Z + lifecycle + failure cases | **NEW** |
| `docs/AI_NATIVE_COMMERCE_PHASE_20_FORENSIC_AUDIT.md` | Stage A forensic audit | **NEW** |
| `docs/AI_NATIVE_COMMERCE_PHASE_20_IMPLEMENTATION_MAP.md` | This file | **NEW** |
| `docs/AI_NATIVE_COMMERCE_PHASE_20_FINAL_REPORT.md` | Stage B final report | **NEW (next)** |

---

## 20. Single-Pass & Architecture Confirmation

- Calls per turn: `1 extract_commerce_signals` (line `commerce/deepseek.py:extract_commerce_signals`) + `1 get_llm_provider().generate_with_history` (Qwen 2.5:3b via `generate_draft`/`generate_draft_with_tools`) + `1 score_draft` (authority-aware). `verify_single_pass()` asserts `additional_llm==0`.
- Architecture: **NO REDESIGN**. No new worker, no new queue, no Redis Streams replacement, no PostgreSQL replacement, no ORM change. `provider` unchanged, `canary` not activated, `AUTONOMY_ENABLED` respected.

---

Implementation map complete. Next: `docs/AI_NATIVE_COMMERCE_PHASE_20_FINAL_REPORT.md` with 25 sections + final verdict.
