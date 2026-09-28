# Phase 9 — Evaluation Harness / Chronological / Baselines / Runner Report

**Repository:** `E:\chatbot` **Branch:** `main` **HEAD:** `19e188413dbc18ac29ce2ba246cee3a60f361876`
**Phase:** 9 — Evaluation Harness (P18-P19/P23-P24) (AUDIT → IMPLEMENT → VERIFY)
**Date:** 2026-09-20
**Prereqs:** Phases 1-8 (135 PASS), `commerce/offline_optimizer.py:1267` chronological_holdout, `simulation/synthesizer.py:26`, `simulation/persistence.py:42`

---

## Status

**PASS** — 8 new evaluation tests +135 prior simulation =143 PASS; 105 optimizer/readiness PASS; file-only, deterministic chronological split, observable metrics + latent recovery, baselines, runner verified.

---

## Chronological splitter + metrics formulas

**File:** `simulation/evaluation/__init__.py:12` `chronological_split`

```python
sorted = sorted(bundles, key=lambda b: (b.input.evaluated_at, b.input.opportunity_id))
# count-based default P18: TRAIN 1-60 VALID 61-75 TEST 76-90
train = sorted[:60]; valid = sorted[60:75]; test = sorted[75:90]
# datetime guard mirrors offline_optimizer.py:1279
if train_end and valid_start and test_start: assert train_end <= valid_start <= test_start
# no shuffling per P18:766
```

**Metrics** `simulation/evaluation/metrics.py:14`

```python
log_loss = -mean(y*log(p+1e-15)+(1-y)*log(1-p+1e-15))  # eps1e-15
brier = mean((p-y)^2)  # wraps offline_optimizer 1512 logic
calibration_gap = |mean(p)-mean(y)|
accuracy = correct/n_pred threshold 0.5, precision true_pos/pred_pos, recall true_pos/actual_pos, coverage n_pred/n
```

**Dataset hash** `simulation/evaluation/reports.py:11` `dataset_hash = sha256(sorted creator_id:opportunity_id:label)[:16]`

---

## Baselines registry

**File:** `simulation/evaluation/baselines.py:5` `global_purchase_rate(train) = clamp(mean(y),0.01..0.99)`, `creator_purchase_rate(train, creator_id)`, `fan_recent_rate(train, fan_id, window=5)`, `most_frequent_offer_type_rate`. Each `predict(bundle)->float 0.01..0.99` clamped. Deterministic per creator_id first predicate, fallback 0.5.

---

## Determinism proof

Same bundles twice `chronological_split` same `train/valid/test` sizes and `opportunity_id` ordering, guard raises `ValueError` if `train_end <= valid_start` violated (`test_a_chronological_split_deterministic`). Runner `run_evaluation` same seed11 balanced n=90 twice `dataset_hash` identical `report.metrics.brier` identical.

---

## Heterogeneous E2E (90 bundles split 60/15/15, metrics, by_cohort, baselines diff)

**`test_e_e2e_evaluation`:** `World(seed11, balanced, n=90) → generate_mature_bundles (heterogeneous via offer_strategy) → mature FULL → chronological_split 60/15/15 → train_creator_model on train → predict test → Metrics Brier/log_loss, by_cohort price_bucket, baselines global_rate vs creator_rate`. Report contains `dataset_hash 16hex, train_n 60 valid 15 test 15, Brier/log_loss, by_cohort keys LOW/MID/HIGH, baselines comparison, file-only `reports/evaluation.json` via `EvaluationReport.save()`.

---

## Isolation proof (16 FEATURES, hidden_payload not in snapshot)

- `FEATURE_NAMES` `commerce/offline_optimizer.py:212` still 16 `test_g_isolation` PASS.
- Evaluator may read `ground_truth/{id}.payload.json` for `evaluate_vs_latent` but `grep lower snapshot` in `tests/test_simulation_evaluation.py: f` shows `latent_purchase` not in `opp.decision_snapshot`, `GroundTruthReference` identifier only.
- Creator isolated: `chronological_split` sorts but does not mix creators; `build_creator_dataset` enforces `creator_id first predicate`.

---

## Tests PASS/FAIL list + runtime

**New 8 PASS (6.95s):** `a_chronological_split_deterministic`, `b_metrics_known_values (log_loss 0.105, brier 0.01)`, `c_cohorts (balanced LOW/MID/HIGH)`, `d_baselines (0.01..0.99)`, `e_e2e_evaluation (60/15/15 hash+metrics)`, `f_latent_recovery (Brier vs latent)`, `g_no_signal_control (Brier ≈ baseline)`, `h_selection_bias (CENSORED not negative)`.

**Total simulation 143 PASS, optimizer/readiness 105 PASS, DB/Redis UNVERIFIED.**

---

## Known issues & Phase 10 readiness

- No AUC yet (only Brier/log_loss/calibration), no drift/adversarial P16/P17 postponed, counterfactual diagnostic via `score_candidates_extrapolative` not yet wrapped.
- `simulation/persistence.py` manifest not yet auto-injects `dataset_hash` (optional, backward compat).
- **Phase 10 ready:** YES — heterogeneous dataset → chronological split → metrics → baselines → report → runner (P23:875) provides foundation for drift/adversarial/counterfactual.

**Files created:** `simulation/evaluation/__init__.py`, `simulation/evaluation/metrics.py`, `simulation/evaluation/baselines.py`, `simulation/evaluation/cohorts.py`, `simulation/evaluation/reports.py`, `simulation/runner.py`, `tests/test_simulation_evaluation.py`, `docs/OPTIMIZER_SIMULATOR_PHASE_9_REPORT.md`

**Modified none production** `commerce/*`, `db/*`, `workers/*`.

