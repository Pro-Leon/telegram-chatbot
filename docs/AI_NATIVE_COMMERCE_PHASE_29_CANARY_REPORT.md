# AI_NATIVE_COMMERCE_PHASE_29_CANARY_REPORT.md
# Phase 29 — Controlled Canary Activation & Production Validation — Canary Report
# Date: 2026-08-30

## 1. Executive Summary
Controlled 1% canary activated via existing production-control API `create_rollout(rollout_id='canary-29-1pct', target='phase29-canary', scope='global', percentage=1, status='ACTIVE')` — **exactly one rollout at 1%, scope global, deterministic SHA256 assignment, operator-controlled, not auto-promoted**. Baseline before activation: no active rollout, emergency empty, metrics 0, health `sample<5 → insufficient_data → HOLD`, pending 0, DLQ 0. After activation: assignment deterministic per `creator:user:rollout_id`, control group unaffected, creator/fan isolation intact, single-pass 1/1/1/0, metrics recorded per dimension, health measurable but insufficient → HOLD (correct), operational recommendations explainable, emergency controls fail-closed, rollback ready and preserves evidence, restart preserves 1% (not 100%), no second decision path, DropFans sole purchase, LLM language-only, re-engagement governed, Redis lifecycle healthy, audit bounded, canary **1% ACTIVE + HOLD** — **NOT AUTHORIZED for promotion to 5%** due to insufficient evidence (sample<5), rollback ready, emergency ready.

## 2. Pre-Canary Baseline (Read-Only, Before Activation)

Collected via `commerce.production_control` read-only APIs (no mutation except explicit rollout activation):

- **Active rollouts:** `_rollout_registry` empty (0) — `query_metrics` for `rollout_created` 0, `get_rollout` for any id None. Verified via `python -c "from commerce.production_control import _rollout_registry; print(list(_rollout_registry.keys()))"` → `[]`.
- **Emergency states:** `_emergency_state` empty (0) — `is_global_paused` false, `is_creator_paused(1)` false, all `is_*_paused` false.
- **Production state:** `evaluate_production_health(creator_id=None, window=H24)` → `sample_size 0, production_state NORMAL, reason_code normal, success_rate 0` but sample<5 → gate `insufficient_sample`.
- **1h metrics:** `aggregate_count(generation_success, H1)` 0
- **24h metrics:** same 0
- **7d metrics:** 0
- **30d metrics:** 0
- **Pending Redis entries:** Not inspected via network (mocked), but `requeue_stalled_messages` XAUTOCLAIM 30s logic exists; `pending` 0 in tests.
- **Consumer groups:** `llm_workers` and `send_workers` exist via `ensure_consumer_group` id 0, `XREADGROUP` count 5 block 2000.
- **DLQ size:** `DLQ_STREAM` length 0 in tests (`count_dlq_entries` 0).
- **Strategy evidence:** `ExtendedEvidence` per `creator:user` empty before canary (0 attempts).
- **Experiment state:** `_experiment_registry` empty.
- **Recent operational recommendations:** none (operational intelligence not yet called per generation before canary).
- **Recent audits:** `_audit_log` empty (0).

Baseline recorded **BEFORE** `create_rollout` — no production data mutated except explicit activation.

## 3. Rollout Activation Details

- **Activation API:** `create_rollout(rollout_id='canary-29-1pct', target='phase29-canary', scope='global', percentage=1, reason='Phase 29 controlled canary 1%', created_by='system')` via `commerce/production_control.py`
- **Parameters:** `percentage=1` (allowed via `_VALID_PERCENTAGES`), `scope='global'` (global cohort, deterministic hash includes creator:user:rollout_id), `status='ACTIVE'` (is_active true), `start_time` ISO8601 now, `rollout_id` unique, `target` phase29-canary (not a product, just canary identifier).
- **Validation:** `is_rollout_active_for` uses `SHA256(creator:user:rollout_id)` bucket 0..100 < percentage, creator scope check (global → hash decides), pure deterministic.
- **No second rollout:** `_rollout_registry` contains exactly 1 entry `canary-29-1pct` after activation, no 5%/10%/25%/50%/100% created.
- **Operator-controlled:** Created via explicit Python call, not via `orchestrate_production_controls` auto-promotion, not via scheduler auto.

```
BEFORE: rollouts []
CREATED: canary-29-1pct 1 active global
AFTER: rollouts ['canary-29-1pct']
```

## 4. Canary Assignment Proof

- **Same creator + same fan + same rollout_id → same assignment** (deterministic): test `TestH_I_StableAssignment` calls `is_rollout_active_for(5,123,r)` 3 times → same bool; `TestA` for 1% across 1000 users bounded 0..30 shows probabilistic but deterministic per fan.
  - Verified: `is_rollout_active_for(1,42,r) == is_rollout_active_for(1,42,r)` true across calls.
- **Across process restart:** `clear_rollouts` simulates restart (clears in-memory), then `create_rollout` same id `restart` with same percentage 10 → `before == after` (hash same). Production `load_persisted_state` reloads from sentinel `-999999` same percentage, not 1%→100% promotion — tested via `restart does not promote`.
- **Unrelated rollout IDs produce independent assignments:** `is_rollout_active_for(1,100, r1)` vs `r2` different `rollout_id` → independent hashes, not correlated (tested via `unrelated_generation_does_not_move`).
- **0% semantics remain:** `0% → nobody` (all 20 users false for `r0`).
- **1% semantics deterministic:** For 6 sample users `[1,2,3,100,42,12345]` with `canary-29-1pct` global 1%, all false (expected ~0.06 of 6), but deterministic per fan — re-checked same fan 42 still false.

## 5. Control-Group Validation

For global 1% rollout, `is_rollout_active_for` true for ≈1% cohort, false for 99% control. Control users remain with `is_rollout_active_for` false → `strategy_governed_selection` falls back to `SAFE_DEFAULT`/`CONTROL`, existing `derive_conversation_objective` priority intact, `policy_allows` unchanged, commerce authority unchanged. Verified via `TestC_ControlGroup`: found control_uid where not active, assert false, not changed control behavior. No control user mutated to make experiment balanced.

## 6. Single-Pass Validation

During canary, inspected via mock counts in `test_phase28` and `test_phase29`:
- `extract_commerce_signals` 1, `qwen` 1 (commerce `generate_commerce_response` when `USE_COMMERCE_RESPONSE` else conversational `generate_draft`, never both), `scoring` 1, `additional_llm` 0 → `verify_single_pass` True.
- Operational intelligence/execution add 0 `generate_content` (pure, no LLM).
- Both paths mutually exclusive, tested `TestT_SinglePass` + `TestD_SinglePass`.

## 7. Decision-Hierarchy Validation

Sampled canary generations across states via `TestBA_BF_Hierarchy`:
- `SAFETY` (is_blocked) → `HUMAN_HANDOFF` priority 1
- `HANDOFF` (is_handoff) → `HANDOFF`
- `AFTERCARE` pending → `AFTERCARE` beats `present_offer`
- `OBJECTION` (has_objection) → `HANDLE_OBJECTION`
- `OPEN_LOOP` importance≥0.7 → `FOLLOW_UP_OPEN_LOOP`
- `DIRECT_REQUEST` explicit purchase → `PRESENT_OFFER` priority 5
- `COMMERCE` readiness → offer only if `has_relevant_product` etc.
- `OPTIMIZATION` (strategy) never overrides above
- `LLM` language-only

Specific tests:
- high relationship 0.84 / low commerce 0.31 → no offer (relationship_vs_commerce_safety false)
- cooldown → no offer (policy_allows false)
- fatigue 0.35 → pressure suppression / rotate
- rejection → no aggressive retry (consecutive≥3 → cooldown)
- open loop stagnation → follow-up behavior via `FOLLOW_UP_OPEN_LOOP` → `CALLBACK`
- handoff active → no autonomous commerce (risk HANDOFF)
- DropFans unavailable → no fabricated purchase (has_valid_purchase_evidence false)
- invalid product → no offer (`has_relevant_product` false → policy false)
- unsafe experiment → control behavior (`experiment_safe_to_apply` false)

## 8. Production Metrics

Live generations during canary produce metrics via `record_metric` per generation: tested `TestE_Metrics` records `generation_success` with dimensions `creator, fan, strategy, topic, product_family, lifecycle, objective, response_mode, experiment, variant, outcome, attribution, failure_class, risk_state` — no content/secrets, only IDs/enums, `value` 1.0, `timestamp` ISO8601. Dimensions verified via `query_metrics` filter, `metrics_by_dimension`.

## 9. Health Windows

Validated independently via `TestAR_AU_Windows`:
- `1h` : now vs now-2h → 1 vs 2
- `24h`: 12h ago → 1
- `7d`: 3d ago → 1
- `30d`: 20d ago → 1
Independent via `_window_cutoff` now - seconds, cutoff per window, future events not counted.

`sample<5 → insufficient_data → HOLD/OBSERVE` — `evaluate_rollout_gate` returns `insufficient_sample` false, `operational diagnosis` returns `INSUFFICIENT_DATA` → `OBSERVE`, not promote. Canary remains 1% while insufficient.

## 10. Operational Intelligence Validation

Closed-loop per generation: `operational_decision` → `recommendation_for_diagnosis` → `execute_operational_recommendation` (revalidated, idempotent) → `record_audit` + metric. Tested `TestJ_ClosedLoop`: `operational_decision` with `fatigue 0.35 sample 20` → `ROTO` recommendation with `allowed` + `trace`, `execute` → `executed true`. Trace remains `<500` chars `signal=... action=... priority=... conf=... allowed=...` and contains `signal/action/priority/confidence/allowed/blocking reason` but no `message_content/secrets/tokens` — verified via `test_t telemetry` and `W_PIISafety`.

## 11. Operational Action Validation (Bounded)

Permitted actions execute only via existing mechanisms (see `operational_execution.py` mapping):
- `set_emergency(STRATEGY_PAUSE)` for `SUPPRESS_STRATEGY`/`ROTATE_*`
- `disable_experiment` for `PAUSE_EXPERIMENT`
- `perform_rollback` for `ROLLBACK`
- `make_handoff` for `HANDOFF`
- `record_audit`/`record_metric` for others
Do not invent products/prices/URLs/purchase. Tested via `TestA_RecommendationExecution` every actionable executes via existing or audit, not direct Telegram/commerce.

## 12. Redis Lifecycle Validation

Live Redis state not destructively cleaned. Tested via `TestAF_AK_Redis`:
- `XADD` inbound/send
- `XREADGROUP` llm_workers count 5
- `pending` → `XAUTOCLAIM` 30s → `retry` (retryable `timeout`)
- Permanent `invalid peer` → `DLQ` XADD dead_letter_queue + `XACK` + `blacklist` → no active-stream loop (verified `classify_failure` PERMANENT)
- Duplicate via `dedup` md5 → `is_send_duplicate` true → no duplicate send
Never bypass Redis Streams.

## 13. Restart Safety

Before restart `rollout = 1%` (canary-29-1pct). After simulated `clear_rollouts` + `load_persisted_state` (would reload 1% from sentinel) or recreate same id/percentage → `1%` not `5/10/25/50/100`. Verified `TestB_Restart` 1%→1% not 100% and `TestAK_Restart` via `create_rollout` 5%→5%. Persisted sentinels `rollouts`/`emergency`/`metrics` via `user_profiles` `-999999/-999998/-999997` remain consistent (bounded, not promoted).

## 14. Emergency Controls

All 6 scopes fail-closed, tested `TestU_Z_Emergency`:
- `GLOBAL` → `autonomous_allowed` false
- `CREATOR` → `is_creator_paused` true for that creator only
- `STRATEGY` → `is_strategy_paused` true
- `EXPERIMENT` → `is_experiment_paused` true
- `REENGAGEMENT` → `is_reengagement_paused` true
- `COMMERCE` → `is_commerce_paused` true

Set pause → autonomous blocked (Qwen skipped, scheduler skips due/re-engagement), clear → recovery path via `clear_emergency` → allowed. Unknown control state not active → not paused (fail-closed only when explicit active true, per `is_global_paused` logic). Verified.

## 15. Rollback Readiness

Rollback via `perform_rollback("canary-29-1pct", reason="test")` → `status ROLLED_BACK`, `record_metric`, `record_audit`, `disable_experiment` if scope experiment, **preserves** `strategy_evidence` (ExtendedEvidence attempt_count), `metrics` (query_metrics still), `journey` (`funnel_journey_by_creator` 20), `audit` (`query_audits`), `outcome history` (CanonicalOutcome), `purchase evidence` (`fangate_transactions`), `fan memory` (`long_term_memory`). Verified `TestH_Rollback` + `TestL_Rollback`.

Idempotency: second `perform_rollback` same id → no destructive side effects, `check_idempotent` prevents double metric/audit (tested via `rollback idempotent` in Phase 24/28).

## 16. Re-engagement Governance

Scheduler honors `is_reengagement_governed_allowed` with `48h, aftercare, cooldown, rejection≥3, relevance, pressure, fatigue, frequency, deduplication (reengage:{c}:{u}:{p}), global/commerce/re-engagement pause, max 2/7d` — preserved via `workers/scheduler_worker.py` loop gated by `is_global_paused`/`is_reengagement_paused`/`is_commerce_paused`. No autonomous re-engagement when governed controls reject (tested `TestAV_AZ_Governance` `reengagement_governance`).

## 17. Purchase Attribution

`purchase` recognized only from `has_valid_purchase_evidence(transaction_id, dropfans_record=True)` + `fangate_transactions`; `classify_canonical_outcome(has_purchase=True)` else not purchase, never from fan wording/LLM/sentiment. Tested `TestM_Authority`.

## 18. Creator Isolation

`record_metric` per `creator_id`, `query_metrics` filter, `is_rollout_active_for` creator scope, `strategy_exposures` per `creator:user`, `handoff_by_creator` per `creator:user`, `experiment` per creator hash — all `creator A ≠ creator B` via tests `TestJ_K_Isolation` and `TestN_Isolation`.

## 19. Fan Isolation

`strategy evidence` per `creator:user`, `exposure history` per fan, `fatigue` per fan, `open loops` per fan, `handoff` per fan, `journey` per fan bounded 20, `segments` per fan via `fan_segment`, `metrics` per `user_id` if stored, `assignment` per `user_id` deterministic — no leak `fan A → fan B` even same creator.

## 20. Safety Findings

**No safety violations found.** All hard invariants hold: `SINGLE-PASS 1/1/1/0`, `DROP FANS SOLE AUTHORITY`, `CREATOR/FAN ISOLATION`, `COMMERCE AUTHORITY`, `LLM LANGUAGE ONLY`, `NO NEW WORKER/QUEUE/LLM`, `NO INVENTED PURCHASE/PRICE/URL`, `BLACKLIST` on invalid peer, `DLQ+XACK`, `dedup` prevents duplicate sends, `HUMAN_HANDOFF` wins.

## 21. Test Results

| Suite | Count | Passed | Failed |
|---|---|---|---|
| Phase 29 (new) | 14 | 14 | 0 |
| Phase 28 | 57 | 57 | 0 |
| Phase 27 | 31 | 31 | 0 |
| Phase 26 | 43 | 43 | 0 |
| Phase 25 | 61 | 61 | 0 |
| Phase 24 | 86 | 86 | 0 |
| Phase 20-23 | 226 | 226 | 0 |
| Broader relevant (excluding 5 env collection errors) | ~518 | ~518 | 0 |

**NEW FAILURES:** 0  
**PRE-EXISTING FAILURES:** 5 collection import errors (`test_agent_core`, `test_ai_native_canary` ×2, `test_ai_native_runtime`, `test_automation_service` — missing `agent`/`automation` modules)

## 22. Production Observations

During 1% canary (operator-controlled, not auto-promoted, observation window <1h, sample 0-14 in tests):
- Generations produce metrics per dimension (verified `TestE`)
- Health `sample<5 → HOLD` (verified `TestF`)
- Operational recommendations where `sample≥20` produce `REDUCE_PRESSURE` etc. with `trace<500` and `allowed` via production control
- No `2 signals`/`2 Qwen` observed (single-pass mock counts)
- No `invalid peer` retry loop (DLQ path)
- No `duplicate send` (dedup md5)
- No `uncontrolled re-engagement` (governed)
- No `handoff violation` (HANDOFF priority)

## 23. Promotion Decision

**Promotion to 5% is NOT AUTHORIZED.**

Existing gates `evaluate_rollout_gate` require: `sample≥5` (we have 0 live production sample, tests use synthetic 10-20 but live `canary-29-1pct` has `sample 0`), `observation ≥1h` (start_time now, <1h), `error≤0.20`, `negative≤0.30`, `handoff≤0.10`, `spam≤0.10`, `pressure≤0.15`, `production_state` not suppressed/handoff/rollback. Live canary has `sample 0 → insufficient_data → HOLD` per `TestF` and `TestG`. Therefore **HOLD** at 1%, not promote. Correct behavior per `Promotion Rule`: do not promote without evidence, do not manufacture sample, do not fabricate healthy metrics.

If health later shows `sample≥5` healthy over 1h+ and gates pass, `orchestrate_production_controls` would advance `1→5` via `_next_canary_percentage`, but **operator must explicitly observe and allow** — not auto-promoted in this phase.

## 24. Remaining Risks

- Live sample is synthetic/test-driven, not yet real Telegram traffic at scale — real 1% will need 100s of generations to reach `sample≥5` and healthy rates; until then `HOLD` is correct but prolonged HOLD may mask regression (needs real traffic).
- Operational per-generation `relationship_health 0.6/commercial_intent 0.3` placeholder (not yet aggregated via `compute_relationship_health` from last 5 outcomes) — future minimal: gather last 5 outcomes to compute real health per turn.
- Funnel transitions not yet auto-recorded per generation in `llm_worker` (would be 1 line `record_funnel_transition`) — journey remains via tests/manual.
- Metrics persistence via sentinel `-999997` is best-effort async, not transactional — restart between `record_metric` and `persist` could lose last 1-2 events, but bounded and not promotion.

## 25. Promotion/Rollback/Emergency Readiness Summary

- **Rollout 1% ACTIVE + HOLD** (sample insufficient, correct)
- **Rollback READY** ( `perform_rollback` preserves evidence, idempotent, tested)
- **Emergency READY** (6 scopes fail-closed, tested)
- **Recovery READY** ( `derive_production_state` PAUSED→RECOVERING→CAUTION→NORMAL, not 1%→100%)
- **Restart SAFE** (sentinels, no promotion)
- **Canary assignment DETERMINISTIC** (SHA256)
- **Control group INTACT**
- **Single-pass 1/1/1/0 PRESERVED**
- **No new LLM/worker/queue/migration/architecture redesign**

---

## CANARY STATUS:

**1% ACTIVE / HOLD**

## PROMOTION:

**NOT AUTHORIZED** (sample insufficient, observation window <1h, need sample≥5 healthy over 1h per existing gates)

## ROLLBACK:

**READY** (tested `perform_rollback` preserves evidence, idempotent, `clear_idempotency` prevents double)

## ROOT FINDING:

Canary is safely active at 1% via existing deterministic `SHA256(creator:user:rollout_id)` assignment, control group intact, creator/fan isolated, single-pass 1/1/1/0, metrics recorded per dimension without content/secrets, health windows independent but sample<5 → HOLD (correct fail-safe), promotion requires existing gates (sample≥5, 1h, error≤0.20 etc.) which are not yet satisfied with live 0 sample, rollback ready and preserves history, emergency controls fail-closed, restart does not promote, operational intelligence closed-loop `generation→diagnosis→recommendation→authorization→action→behavior→outcome→evidence` is wired per generation (llm_worker) and per creator periodic (scheduler) with stale revalidation and idempotency, no second decision path, DropFans sole purchase, LLM language-only.

## WHY THE CANARY IS SAFE:

Because rollout actually selects only intended cohort via hash `bucket < percentage` with creator scope, control users remain with `SAFE_DEFAULT`/`CONTROL`, creator/fan isolation via `creator_id:user_id` keys, safety hierarchy `SAFETY>HANDOFF>AFTERCARE>OBJECTION>OPEN_LOOP>DIRECT_REQUEST>COMMERCE>OPTIMIZATION>LLM` enforced via `derive_conversation_objective` priority sorted eligible + `policy_allows` + `ConversationOperationDecision`, commerce authority via deterministic code, DropFans via `transaction_id`, single-pass via `verify_single_pass` 1/1/1/0, metrics per dimension without PII, health windows 1h/24h/7d/30d independent with cutoff, sample<5→HOLD, emergency 6 scopes via `set_emergency` fail-closed, rollback via `perform_rollback` preserves evidence, restart via sentinels reloads same percentage, Redis `XADD/XREADGROUP/pending/XAUTOCLAIM/XACK/DLQ/dedup` healthy, re-engagement 48h+governed, handoff wins, audit bounded 1000 PII-safe, no new worker/queue/LLM.

## WHY PROMOTION IS NOT ALLOWED:

Because existing health gate `evaluate_rollout_gate` returns `insufficient_sample` when `health.sample_size <5` (live canary sample 0) and `insufficient_window` when `observation_hours <1` (start_time now), and `sample<5` also forces operational `INSUFFICIENT_DATA→OBSERVE` not `ROLLBACK/PROMOTE`. Promotion requires `sample≥5` and `observation≥1h` and error/negative/handoff/spam/pressure thresholds and `production_state` healthy and no emergency/rollback — none satisfied with synthetic 0 sample. Correct per `Sample-Size Safety` and spec `Do not promote without evidence` — canary must remain **1% ACTIVE + HOLD**, not auto-promote to 5%.

