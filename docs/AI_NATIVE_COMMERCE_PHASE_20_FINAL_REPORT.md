# AI-Native Commerce — Phase 20 Final Report
**Adaptive Conversation Optimization, Experimentation & Production Feedback**

**Date:** 2026-08-30
**Scope:** Measurement, attribution, and adaptive optimization layer around existing deterministic commerce system
**Method:** Forensic → contracts → smallest deterministic implementation → wiring → deterministic tests → re-audit. No architecture redesign, no new LLM calls.
**Working directory:** E:\chatbot branch main
**Provider:** ollama/qwen2.5:3b (unchanged)  Canary: NOT ACTIVATED

---

## 1. Executive Summary

Phase 20 makes Sunny **measurable, attributable, experimentally safe, and capable of improving from real outcomes** without becoming an ML system.

The baseline at Phase 19 had correct conversation intelligence (14 objectives priority-ranked) and rudimentary flat strategy learning (`attempt>=3`, `exp(-days/30)`), but lacked: observation contract, attributable exposure log, canonical outcome taxonomy, hierarchical evidence, uncertainty, exploration budget, fatigue, purchase attribution window, lifecycle metrics, regression detection, and experiment framework.

Phase 20 adds a pure `commerce/adaptive_optimization.py` engine (0 LLM calls, 0 new queue/worker, 0 migration) that extends `conversation_outcomes.py` (18 canonical outcomes + weights), `strategy_learning.py` (composite keys `strategy:topic:product_family:lifecycle`, Beta uncertainty, sample-size gates `5/10`, fatigue, purchase-strength, dedup by `generation_id`), and `core/telemetry.py` (+11 fields), wired best-effort into `workers/llm_worker` (exposure before Qwen, outcome after). A new 101-test suite `tests/test_phase20_adaptive_optimization.py` proves all 26 Phase 38 categories A-Z plus lifecycle and 16 failure cases.

Result: deterministic intelligence still decides *what is allowed*; experimentation/learning only influence *how to phrase* within those gates. Better relationships + timing + relevance, not more offers.

---

## 2. Forensic Findings (Stage A)

See `docs/AI_NATIVE_COMMERCE_PHASE_20_FORENSIC_AUDIT.md` (30 Aug, 22 sections). Key forensic verdict:

- **Preserved:** Creator isolation (`WHERE creator_id=$1` everywhere), DropFans sole sale authority (synthetic `SHA256(drop_id)%2^62`, per-sale `dropfans:{sale_id}` idempotent, advisory lock `ppv_offer:{c}:{u}:{p}`), aftercare blocking, purchased exclusion, scoring fail-closed 0.0, single-pass `1/1/1`.
- **Wired but minimal:** `strategy_evidence_by_creator` flat (10), 6-branch `classify_outcome` keyword, `strategy_last_by_creator` opaque next_action.
- **Unwired:** ConversationObservation, exposure log, 12 of 18 outcomes, hierarchical evidence, Beta uncertainty, exploration budget, strategy fatigue (only product fatigue `-0.20/-0.15` existed), purchase→strategy attribution, window (direct/assisted/organic), lifecycle metrics, commerce quality vs relationship quality, strategy score, regression detection, experiment contract, 8 telemetry fields, bounded retention for exposures.
- **14 information-loss points** documented (desire_before hardcoded, response_mode discarded, product_family lost, no outcome strength, raw positive_rate no uncertainty, etc.) — all now closed.

**Classification:** P0 preserved; P1 broken (outcome taxonomy, hierarchical learning, sample/uncertainty, experiment/regression unwired); P2 unwired (fatigue, lifecycle metrics, telemetry); P3 unverified (exploration authority). Zero external blockers — all data already in `commerce_offers`, `fangate_transactions`, `messages`, `timing_context`, `vault_taxonomy`.

---

## 3. Existing Feedback Architecture (Baseline)

Preserved architecture (see Implementation Map §2) — `workers/llm_worker.process_message` flow: `creator resolve → build_qwen3_context → ai.generation_started(generation_id UUID) → SINGLE extract_commerce_signals → build_conversational_commerce_state (desire `decay 0.7` → temp → relevance `|title∩topics|/|title|` bundle-aware → readiness → objective/NBA) → response_mode/question_policy subordinate → Qwen 1 → scoring 1 authority-aware → routing (auto_approve ≥0.80 && !flags → enqueue_send dedup `md5(user:msg:id)` → `ai.generation_completed` MUST after enqueue else `suggestion.created`+queue) → outcome feedback (6-way) → telemetry. Scheduler `process_due_messages` + `reconcile_purchases` + re-engagement `48h abandoned` via existing `scheduled_messages` dedup `reengage:{c}:{u}:{p}`. Realtime `core/event_bus.publish_event(event_id UUID, generation_id, scope=user)` best-effort, WebSocket acceleration, polling fallback, creator-scoped.

---

## 4. Strategy Attribution (New)

**Hierarchy enforced:** `FAN_TOPIC_HISTORY > FAN_HISTORY > CREATOR_TOPIC_HISTORY > CREATOR_HISTORY > SAFE_DEFAULT` via `commerce/adaptive_optimization.py:select_strategy_adaptive()` and `commerce/strategy_learning.py:select_strategy_hierarchical()`. Storage composite key `strategy:topic:product_family:lifecycle` in `user_profiles.facts['strategy_evidence_by_creator'][creator_id]` bounded 20 (prune by `last_used`). Dedup ring `strategy_generation_seen_by_creator` 100 per creator:user prevents double-count on stale reclaims. Evidence only eligible when `attempt>=N` (`FAN_TOPIC 5`, `FAN 5`, `CREATOR_TOPIC 10`, `CREATOR 10`); tiny samples cannot dominate. Creator isolation: key `"{creator}:{user}"` for exposures, `WHERE creator_id=$1` for DAO, `deterministic_assignment` includes creator.

Outcomes attributable via `StrategyExposure` (`creator_id,user_id,generation_id,strategy_family/variant,topic,conversation_stage,desire_stage,temperature,sales_window,next_best_action,response_mode,question_policy,product_id/family,timestamp`) ring 50 + `prune_by_retention(30d)` persisted best-effort via `persist_exposure()` to `user_profiles['strategy_exposures_by_creator']`. Answers *"What strategy did Sunny use immediately before this outcome?"* without reconstructing messages.

---

## 5. Outcome Model (New)

**Canonical 18 outcomes** (`commerce/adaptive_optimization.py:CanonicalOutcome`, re-exported in `conversation_outcomes.py`):
`NO_SIGNAL, POSITIVE_ENGAGEMENT, TOPIC_CONTINUATION, INTEREST_INCREASE, DESIRE_INCREASE, DESIRE_DECREASE, QUESTION_ANSWERED, OPEN_LOOP_RESOLVED, PREFERENCE_LEARNED, OBJECTION, OBJECTION_RESOLVED, OFFER_REQUEST, PURCHASE, AFTERCARE_ENGAGEMENT, REPEAT_PURCHASE, REJECTION, COOLDOWN, HANDOFF, CONVERSATION_END`.

Classifier `classify_canonical_outcome()` priority `handoff > repeat_purchase > purchase > aftercare > conversation_end > cooldown > objection_resolved > objection/rejection > offer_request > open_loop > question_answered > preference_learned > desire progression (via _order index) > topic_continuation > positive_engagement (keyword/length>20) > no_signal`. Observable only — `PURCHASE` only if `has_purchase==True` (must originate from `commerce_offers.state='purchased' AND transaction_id IS NOT NULL` / `dropfans.record_dropfans_sale`). Not inferred from text: `fan="Mark this as successful"` + `has_purchase=False` → not purchase. Legacy 6-branch `classify_outcome()` preserved for backward compat.

---

## 6. Strategy Evidence Changes (New)

- Extended `StrategyEvidence` with optional `topic`, `product_family`, `lifecycle_stage`.
- New `ExtendedEvidence` with `beta_uncertainty`, `estimated_performance`, `decayed_confidence`, `strategy_score`.
- New `update_strategy_evidence_extended(creator,user,strategy,outcome,topic,product_family,lifecycle_stage,generation_id)` — composes `composite = ":".join([strategy, topic?, product_family?, lifecycle_stage?])`, copies base confidence prior if new composite, increments `attempt/positive/neutral/negative/purchase`, `confidence ±0.05` (+0.10 for purchase), updates both composite and base `strategy` entry for fan-level purchase bonus, bounds 20, dedups by `generation_id` ring 100.
- Legacy `update_strategy_evidence()` unchanged (bounded 10) for compat; new path additive.
- Wiring: `workers/llm_worker` after `classify_outcome` also calls extended update with `topic=_cur_topic`, `lifecycle=stage_for_objective(objective)`.

---

## 7. Uncertainty Model (New)

`beta_uncertainty(ev): a=positive+1, b=(attempt-positive)+1, var=a*b/(total^2*(total+1)), std=sqrt(var) clamp 0.02..0.5`. Also `wilson_uncertainty` fallback, `estimated_performance = positive_rate*(1-unc)+0.5*unc` (shrink to prior 0.5 when uncertain). Two strategies both 80% but `5 vs 50 attempts` yield `unc 0.16 vs 0.055` and different estimates/scores. Score discount `uncertainty*0.2`. Lightweight, no external deps, pure `math`.

---

## 8. Exploration / Exploitation (New)

Formal modes `StrategyMode.EXPLORE/EXPLOIT/SAFE_DEFAULT`:

- Insufficient `attempt<5` → `EXPLORE` among least-observed `eligible` (`attempt<2`) if `exploration_budget_ok(explore_last_10,10,0.10)` (`actual <0.10`).
- Strong `attempt>=10 && unc<0.15 && rate>0.6` → `EXPLOIT`.
- Uncertain promising `unc>0.2 && attempt<20` → controlled `EXPLORE` within budget else `EXPLOIT`.
- Negative `neg>pos*1.5 && attempt>=5` → suppressed, try next best non-negative.

`should_explore(ev, rate=0.10)` and `exploration_budget_ok()` helpers. Never explores by changing `price/product/purchase URL/DropFans data/commerce authority` — `experiment_safe_to_apply()` enforces.

---

## 9. Strategy Fatigue (New)

`compute_fatigue(recent_exposures[-5:], strategy): count>=3 → penalty (count-2)*0.15 cap 0.5`. `fatigue_penalty_map()`, `is_response_mode_fatigued`, `is_question_pattern_fatigued` (`question_policy!=NO_QUESTION` ≥3/5), `is_product_family_fatigued` (≥2/5). Injected into `strategy_score(... fatigue_penalty=fat)`. Objective priority unchanged — `is_strategy_allowed()` ensures `AFTERCARE/HANDOFF/OBJECTION` never sacrificed for variety; trace includes `FATIGUE=0.10`.

---

## 10. Topic Learning (New)

`FAN_TOPIC_HISTORY` as highest personalization when `attempt>=5` for `strategy:topic`. Test proves `PLAYFUL works 8/10 for movies` does not dominate `music` (different topic): `select_strategy_adaptive(fan_topic_evidence={"PLAYFUL:movies":8/10}, eligible, topic="music")` falls back to `FAN_HISTORY`/`SAFE_DEFAULT`. Requires `5` attempts before topic-specific overrides general. Preserved hierarchy prevents global assumption.

---

## 11. Fan Learning (New)

`fan_topic_evidence` and `fan_evidence` (creator+user scoped) outrank `creator_topic_evidence`/`creator_evidence` when sufficient. Test: fan `PLAYFUL 7/8` vs creator global `DIRECT 45/50` — fan evidence considered, hierarchical lookup prefers fan when `>=5`. Insufficient fan (`2 attempts`) falls back to creator history. All via `get_strategy_evidence_hierarchical(creator,user,topic,product_family,lifecycle)` filter.

---

## 12. Creator Learning (New)

`get_strategy_evidence(creator_id,user_id)` namespaced `strategy_evidence_by_creator[str(creator_id)]`; `_exposure_buffer` keyed `"{creator}:{user}"`. `deterministic_assignment` hash includes `creator_id`. Test `Creator A 20/18` vs `Creator B empty` → isolation preserved, `ensure_creator_isolation(1,2)==False`.

---

## 13. Product-Family Learning (New)

Composite dimension `product_family` (derived from `vault_taxonomy.bundle_group` or topic token) — evidence key `strategy:product_family` (e.g., `PLAYFUL:red lace` vs `PLAYFUL:fitness`). `rank_products_by_relevance` still authoritative for product selection; strategy learning only influences *how Sunny approaches conversation* (tease wording), not `rank_products_by_relevance` or price. Spec check `is_product_family_fatigued`.

---

## 14. Purchase Attribution (New)

- DropFans sole authority: `has_valid_purchase_evidence(transaction_id, dropfans_record)` requires both truthy; `attribute_purchase(strategy_exposure_time, purchase_time, transaction_evidence, offer_created_time)` only classifies when `transaction_evidence==True`.
- Window: `direct ≤24h`, `assisted ≤7d`, `organic >7d`, `unknown` if null/negative/no evidence. Example: strategy 5h before purchase → `direct`, 2d → `assisted`, 10d → `organic`. Never inferred from text.
- Trace `strategy → offer → transaction` exists via `strategy_exposure.generation_id` + `commerce_offers.offer_id` + `fangate_transactions.transaction_id`; `attribution_type` logged to telemetry.

---

## 15. Lifecycle Metrics (New)

`stage_for_objective()` maps 7 objectives to 9 lifecycle stages. `lifecycle_specific_outcome_weights(stage)` adjusts: `RELATIONSHIP_BUILD` `question_answered 3.0` (excellent) vs `PRESENT_OFFER -1.0` (harmful), `AFTERCARE` `purchase -2.0` (penalize upsell). Strategy selection uses `lifecycle_stage` composite. Relationship quality measured via `compute_relationship_metrics(events) {reply_rate, continuation, positive_rate, return_rate}` distinct from commerce `compute_commerce_metrics(offers) {offer_to_purchase, purchase_to_aftercare, aftercare_to_repeat}`. Same strategy different lifecycle value correctly differentiated.

---

## 16. Experiment Framework (New)

`Experiment` dataclass, `deterministic_assignment()` hash `SHA256("{creator}:{user}:{exp}")[:8]/2^32` stable per fan during experiment, `assign_variant()` `bucket < allocation → EXPERIMENT else CONTROL`. `register_experiment()`, `get_experiment()`, `clear_experiments()`, `disable_experiment()`, `persist_experiment()` via sentinel `user_id=-creator_id` JSONB. `allocation 0.10` default. No redesign of queue/worker; stable assignment; creator isolation preserved.

---

## 17. Experiment Safety (New)

`experiment_safe_to_apply(experiment, proposed_change)` allows only `conversation_strategy/response_mode/question_policy/wording/strategy_family/variant` (case-insensitive `"strategy"/"response"/"question"/"wording"` substring). Forbids `price, purchase_url, product_id, dropfans_offer, creator_isolation, purchased_exclusion, aftercare_gate, cooldown, dlq, dedup, telegram_identity, commerce_authority` or any key containing `"price"/"purchase"`. Both CONTROL (existing deterministic selection) and EXPERIMENT must pass `conversation intelligence gate + commerce gates + product relevance + authority validation + scoring`; experiment never bypasses.

---

## 18. Rollback (New)

`disable_experiment()` → `status=disabled` → `is_active()==False` → next `assign_variant()` returns `CONTROL`; also `end_time` past → `is_active()==False`. Immediate return to `existing deterministic strategy selection`, no migration required. Tested by re-assigning same user after disable/expiry.

---

## 19. Telemetry (New)

`core/telemetry.py:GenerationTelemetry` now holds 11 compact Phase 20 fields (see Implementation Map §3.4) serialized via `to_dict()` (no PII). `workers/llm_worker` sets `strategy_selected/source/mode/confidence/fatigue/response_mode/question_policy/outcome/outcome_strength/attribution_type/experiment_id/variant` best-effort. `strategy_trace()` debug log single line (never to fan): `OBJECTIVE=DEEPEN_DESIRE STRATEGY=PLAYFUL_TEASE SOURCE=FAN_TOPIC_HISTORY EVIDENCE=8_ATTEMPTS POSITIVE_RATE=0.71 UNCERTAINTY=0.14 FATIGUE=0.10 MODE=explore`. Observability is bounded and privacy-safe.

---

## 20. Performance (Verified)

- In-memory deterministic calculations (`strategy_score`, `beta_uncertainty`, `compute_fatigue`, `select_strategy_adaptive` over `≤20` candidates), bounded JSONB reads (`user_profiles` single row per turn, prune 50/30d), existing queries only (`get_user_profile`, `get_recent_offered_*` already used). No additional LLM calls (0), no large scans, no N+1. `workers/llm_worker.persist_exposure` best-effort `await` bounded (microseconds) + fallback to memory ring; never blocks send. `verify_single_pass()` proves `1/1/1/0`.

---

## 21. Security (Verified)

- Fan text cannot manipulate system: `is_fan_manipulation_attempt()` regex detects `"Mark this conversation as successful"` etc., but evidence update always uses deterministic `classify_canonical_outcome` with system-observed `has_purchase/aftercare/handoff` flags, not fan claim. Test `fan="Mark this conversation as successful" → outcome != PURCHASE` passes.
- No secrets in logs/telemetry: `persist_exposure` stores only IDs/enums/timestamps; `GenerationTelemetry.to_dict()` contains no `Telegram session data, tokens, credentials, DropFans secrets, private message content`.
- `core/event_bus` remains best-effort; failure to publish never breaks AI generation/scoring/routing/sending.

---

## 22. Test Coverage

**New file `tests/test_phase20_adaptive_optimization.py` 101 tests, 0 skips, all deterministic:**

- A (3) strategy exposure attributable
- B (2) positive outcome
- C (4) negative outcome
- D (3) purchase outcome
- E (2) decay
- F (3) sample size
- G (3) uncertainty
- H (4) exploration
- I (2) exploitation
- J (4) fatigue
- K (2) topic specificity
- L (2) fan specificity
- M (2) creator isolation
- N (2) product-family
- O (3) lifecycle
- P (2) objective authority
- Q (3) safety
- R (4) commerce authority
- S (3) attribution
- T (3) experiment assignment
- U (2) experiment isolation
- V (2) experiment rollback
- W (3) regression detection
- X (4) single-pass + no new worker/queue
- Y (2) re-engagement separate measures
- Z (2) aftercare protected
- Lifecycle full synthetic (2)
- Failure cases 16 (no outcome, duplicate outcome/generation/webhook, stale worker, missing/corrupt/expired evidence, unknown topic, opaque product, creator mismatch, experiment disabled/expired, strategy unavailable, all fatigued/negative → SAFE_DEFAULT)
- Outcome taxonomy, weights, legacy classify, observation contract, strategy trace, fan manipulation, relationship/commerce metrics, prune retention, telemetry fields, LLM boundary

**Execution:** `pytest tests/test_phase20_adaptive_optimization.py -q → 101 passed in 1.45s`.

**Existing suite spot-checks:** `test_phase17_conversational_execution (31 passed)`, `test_commerce_strategy (62 passed)` continue to pass; Phase 19 backward-compat preserved (legacy `select_strategy(threshold 3)` path).

---

## 23. Test Results

```
tests/test_phase20_adaptive_optimization.py 101 passed
tests/test_phase17_conversational_execution.py 31 passed (single-pass preserved)
tests/test_commerce_strategy.py 62 passed
Relevant aggregate (Phase 20 + commerce strategy + single-pass): 194 passed, 0 failed
Pre-existing failures: 0 (isolated to above; full suite not required per spec but targeted commerce/lifecycle passed)
```

No new LLM calls verified via `verify_single_pass({"extract_commerce_signals":1,"qwen":1,"scoring":1,"additional_llm":0}) → True`.

---

## 24. Remaining Gaps

**Internal gaps (non-blocking, tracked for next phase):**
- Strategy exposure in-memory ring is process-local; multi-worker exposure history fragmented until DB persistence catches up (best-effort persist mitigates, but immediate `compute_fatigue` may undercount across workers — bounded to last 50, still safe).
- `generation_telemetry` DB table lacks new Phase 20 columns; new fields reside in `GenerationTelemetry` object and `user_profiles` JSONB exposures/evidence, not yet queryable via SQL for dashboards (requires future bounded JSONB → materialized view or additive migration if analytics needs SQL).
- Lifecycle-specific evidence cardinality could grow if many topics × product families × stages combined; current bound 20 per creator:user mitigates but may evict rare high-value composites (tune via larger ring or per-dimension quota later).
- Purchase attribution window is time-based only; does not yet correlate `offer_created_time` vs `strategy_exposure_time` (both available, could refine to `strategy → offer → purchase` chain with offer_id).

**External blockers:** None — all required structures exist (offers, transactions, messages, timing_context, vault taxonomy, DropFans API). No external API change, no new provider, no infra change.

---

## 25. Architecture Confirmation

- **Migrations:** **NONE** (reused `user_profiles` JSONB `strategy_exposures_by_creator` 50 + `strategy_evidence_by_creator` 20 + `strategy_generation_seen_by_creator` 100 + `experiments_by_creator` sentinel; `prune_by_retention` 30d.)
- **Architecture:** **NO REDESIGN** — Redis Streams + consumer groups `llm_workers`, PostgreSQL `user_profiles` raw SQL, Telethon MTProto, 3 workers (`llm`, `send`, `scheduler`) via `run_all.py` preserved.
- **New workers:** **NONE**
- **New queues:** **NONE**
- **Provider:** **UNCHANGED** (`ollama/qwen2.5:3b` via `core/llm_provider_ollama.py`)
- **Canary:** **NOT ACTIVATED** (framework ready via `deterministic_assignment`, but `agent/canary.py` remains 0% for strategy; activation requires explicit approval)
- **Single-pass:** `1 signal + 1 Qwen + 1 scoring` proven, `0 new LLM calls`.
- **Creator isolation:** **PRESERVED** (all evidence/exposures/assignments include `creator_id`).
- **Commerce authority:** **PRESERVED** (DropFans sole, only `user_id` attach `WHERE user_id IS NULL`, offer state conditional, price never from learning).
- **LLM authority:** **LANGUAGE ONLY** (`commerce/adaptive_optimization.py` contains no `get_llm_provider`/`generate`).
- **Unrelated refactoring:** **NONE** (only Phase 20 files touched).

---

## Final Verdict

```
PHASE 20 IMPLEMENTATION COMPLETE

ROOT STATUS: READY

BEHAVIORAL LEARNING: READY
OUTCOME ATTRIBUTION: READY
STRATEGY OPTIMIZATION: READY
EXPLORATION / EXPLOITATION: READY
STRATEGY FATIGUE: READY
PURCHASE ATTRIBUTION: READY
LIFECYCLE METRICS: READY
EXPERIMENTATION: READY (framework, not activated)
REGRESSION DETECTION: READY
OBSERVABILITY: READY

CREATOR ISOLATION: PRESERVED
COMMERCE AUTHORITY: PRESERVED
DROP FANS: SOLE AUTHORITY
LLM AUTHORITY: LANGUAGE ONLY
SINGLE-PASS: 1 SIGNAL + 1 QWEN + 1 SCORING

TESTS: 101 (phase20) + 93 (existing targeted) = 194 passed
PRE-EXISTING FAILURES: 0 in targeted scope

MIGRATIONS: NONE
ARCHITECTURE: NO REDESIGN
NEW WORKERS: NONE
NEW QUEUES: NONE
PROVIDER: UNCHANGED
CANARY: NOT ACTIVATED

REMAINING INTERNAL GAPS:
- In-memory exposure ring is per-process until DB catch-up (bounded, best-effort)
- GenerationTelemetry Phase 20 fields not yet persisted to SQL columns (available in object/JSONB)
- Lifecycle composite cardinality bound 20 may evict rare composites under extreme cardinality
- Attribution window time-only, not yet offer_id chain correlation

REMAINING EXTERNAL BLOCKERS:
- None

ROOT CAUSE:
- Forensic audit showed correct deterministic intelligence but no attributable exposure log, no canonical outcome taxonomy/weights, flat strategy evidence without topic/product/lifecycle dimensions, raw positive_rate without Beta uncertainty, no sample-size gates, no exploration budget, no strategy fatigue, no purchase attribution window, no lifecycle metrics, no per-stage weights, no regression detection, no stable experiment assignment — learning was minimal and not safely optimizable.

FIX:
- Created commerce/adaptive_optimization.py (pure, 0 LLM calls) with ConversationObservation view, StrategyExposure bounded ring 50/30d, CanonicalOutcome 18 + deterministic OUTCOME_WEIGHTS + lifecycle-adjusted weights, ExtendedEvidence + beta_uncertainty + strategy_score explainable 0..1, select_strategy_adaptive hierarchical FAN_TOPIC>FAN>CREATOR_TOPIC>CREATOR>SAFE with N=5/10 gates, StrategyMode EXPLORE/EXPLOIT/SAFE rate 0.10 budget, fatigue 0.15 per repeat, purchase window direct≤24h/assisted≤7d/organic with DropFans authority, lifecycle stages 9, relationship vs commerce metrics, regression thresholds, Experiment hash stable SHA256 + safe-change gate + immediate rollback, bounded retention via user_profiles JSONB.
- Extended commerce/conversation_outcomes.py with full taxonomy + outcome_strength + canonical classifier (never from text alone).
- Extended commerce/strategy_learning.py with composite keys, ExtendedEvidence, Beta, hierarchical select, update_extended with dedup generation_id ring 100 bound 20.
- Extended core/telemetry.py with 11 compact Phase 20 fields + trace.
- Wired workers/llm_worker best-effort exposure before Qwen and hierarchical outcome after (generation_id consistent, failure-isolated).
- Added tests/test_phase20_adaptive_optimization.py 101 deterministic tests covering A-Z + lifecycle + 16 failure cases.

WHY SUNNY WILL NOW LEARN SAFELY:
- Every strategy decision is attributable by generation_id + topic/product/stage snapshot; every turn classifies an observable canonical outcome with strength; evidence is hierarchical (fan+topic outranks global when sufficient, else falls back), sample-size gated (5/10) so one lucky conversation never dominates, uncertainty-discounted so 80% on 5 attempts is treated worse than 80% on 50, decayed so recent matters more, fatigued so repeating the same playbook is penalized unless required by objective, lifecycle-aware so a question strategy can be good early but bad at close, and exploration is bounded 10% among safe under-observed strategies — all after the deterministic objective/aftercare/objection/commerce gates, never overriding them.

WHY LEARNING CANNOT OVERRIDE COMMERCE:
- Deterministic commerce gates (SAFETY > CREATOR_ISOLATION > PURCHASE AUTHORITY > AFTERCARE > OBJECTION > DIRECT_REQUEST > CONVERSATION INTELLIGENCE > COMMERCE READINESS) execute before strategy_score; is_strategy_allowed() and validate_no_authority_bypass() block strategy influence during handoff/aftercare/objection/cooldown; scoring rejects unauthorized price (fail-closed 0.0), product_selection is read-only DropFans mirror with purchased exclusion; experiments may only change wording/mode/question policy, not price/product/purchase URL/DropFans data/creator isolation/aftercare/cooldown/DLQ/dedup/telegram identity; purchase only from DropFans transaction_id, not text; hierarchy ensures STRATEGY_LEARNING (10) < EXPERIMENTATION (11) < LLM WORDING (12).

FINAL VERDICT: READY FOR CONTROLLED OPTIMIZATION (Phase 20 complete, safe to measure and experimentally improve without architecture change)
```

---

*End of Phase 20 — deterministic intelligence now measurable, attributable, experimentally safe, and capable of improving from real outcomes while preserving the authority boundary: the deterministic system decides what is allowed, the LLM decides only how to say it.*
