# Phase 10 — Drift / Whale-Freebie / Adversarial / Counterfactual / Stress / Failure Suite

**Repository:** `E:\chatbot` **Branch:** `main`
**Phase:** 10 — Drift / Adversarial / Counterfactual / Stress / Failure Suite (P16-P21-P25)
**Date:** 2026-09-20
**Prereqs:** Phases 1-9 143 PASS, 5 packs SHA256, chronological 60/15/15, reuse `commerce/offline_optimizer.py`

---

## Status

**PASS** — 8 new Phase 10 tests +143 prior =151 PASS (file-only, SHA256, 16 FEATURES, 168h maturity preserved).

---

## Files created / modified

| File | Line | Purpose |
|------|------|---------|
| `simulation/scenarios.py:15` | SCENARIO_PACKS extended 5→10, new packs:62-112 | whales, freebie_heavy, temporal_drift, no_signal, adversarial (P17:699-746) |
| `simulation/drift.py:1` | NEW 182 LOC | DriftModel, whale/freebie bias, adversarial weight, ScenarioBehaviorModel (P16) |
| `simulation/evaluation/counterfactual.py:1` | NEW 78 LOC | `counterfactual_harness` wraps `score_candidates_extrapolative:1119` (P20) |
| `simulation/evaluation/stress.py:1` | NEW 132 LOC | `stress_sweep` loop `[60,120,240]` (P21), docs 1k-500k |
| `simulation/evaluation/failures.py:1` | NEW 140 LOC | leakage/creator_contamination/temporal helpers (P25:936-963) |
| `tests/test_simulation_phase10.py:1` | NEW 268 LOC | 8 tests a-h |
| `docs/OPTIMIZER_SIMULATOR_PHASE_10_REPORT.md` | this file | Report |

**Modified none production:** `commerce/*`, `db/*`, `FEATURE_NAMES` 16, ranking v1, maturity 168h, SHA256 only, file-only `simulation_runs/{id}/`, no global random.

---

## Packs registry (10) + drift formula snippets

**Registry** `simulation/scenarios.py:15` — 10 OfferStrategy (USD only, deterministic `_hash_float(seed:sim_id:offer_strategy:{id}:counter)`)

| Pack | Content Mix | Price (minor) | Vault | Behavior Override / Model |
|------|-------------|---------------|-------|---------------------------|
| baseline | SMALL_BUNDLE 1.0 | 1999 | 2 | none (replay) |
| cheap_single | SINGLE 0.7 SMALL_BUNDLE 0.3 | 500-1500 | 1-2 | none |
| premium_mix | PREMIUM 0.4 CORE 0.3 SMALL 0.2 SINGLE 0.1 | 2500-5000 | 2-4 | none |
| balanced | 0.25×4 | 500-5000 | 1-3 | none |
| novelty_heavy | 0.25×4 | 500-5000 | 1-4 | none |
| **whales** | 0.25×4 | **1500-5000** | 2-4 | `purchase_propensity +0.25 / price_sensitivity -0.3` via `whale_biased_behavior:32` (heavy-tail, v1, OFF-POLICY) |
| **freebie_heavy** | **SINGLE 0.6** SMALL 0.2 CORE 0.1 PREMIUM 0.1 | 500-2000 | 1-2 | `freebie 0.7-0.9 hash / purchase -0.2` via `freebie_biased_behavior:52` |
| **temporal_drift** | 0.25×4 | 500-5000 | 1-3 | **DriftModel** `simulation/drift.py:18` (see formula) |
| **no_signal** | 0.25×4 | 500-5000 | 1-3 | `BaselineOutcomeModel 0.5` `outcome.py:38` constant |
| **adversarial** | 0.25×4 | 500-5000 | 1-3 | `adversarial_content_weight:68` flip after 45d (see formula) |

**Drift formula** `simulation/drift.py:18-55` DriftModel (P17 temporal_drift)

```python
DRIFT_RATE = 0.003  # /day  ~0.27/90d  v1
sign_hash = _hash_float(seed, sim_id, f"fan:{fid}:drift_sign:{version}", 1)
sign = 1 if sign_hash > 0.5 else -1
days = (evaluated_at - simulated_start).total_seconds()/86400
drift = sign * DRIFT_RATE * max(0, days)  # drift_for(fan,evaluated_at)
# latent p/logit delta += drift  (clamped 0..1 when applied to p)
```

**Whale bias** `simulation/drift.py:32`

```python
pp = clamp01(behavior.purchase_propensity + 0.25)
ps = clamp01(behavior.price_sensitivity - 0.3)
```

**Freebie bias** `simulation/drift.py:52`

```python
r = _hash_float(seed, sim_id, f"fan:{fid}:freebie_heavy_bias", 1)
freebie = 0.7 + r*0.2  # 0.7-0.9 deterministic
pp = clamp01(behavior.purchase_propensity - 0.2)
```

**Adversarial flip** `simulation/drift.py:68`

```python
ADVERSARIAL_THRESHOLD_DAYS = 45
weight = 0.8 if days <45 else -0.8  # adversarial_content_weight(evaluated_at, simulated_start)
contrib = weight * content_affinity_score  # 0.8*aff before, -0.8*aff after
# OFF-POLICY: misleading historical correlation changes later (P17:741)
```

**ScenarioBehaviorModel** `simulation/drift.py:78` thin wrapper `FanBehaviorModel` → `for_fan` applies whale/freebie bias when `scenario_id` matches, otherwise passthrough; cached per fan, SHA256.

---

## Counterfactual harness snippet

**File:** `simulation/evaluation/counterfactual.py:12` `counterfactual_harness(bundles, model, max_candidates=5) -> list[dict]`

```python
from commerce.offline_optimizer import EXTRAPOLATIVE_WARNING, score_candidates_extrapolative
def counterfactual_harness(bundles, model, max_candidates=5):
    for bundle in limited:  # same fan×same context
        result = score_candidates_extrapolative(bundle.input, model)  # DIAGNOSTIC ONLY
        scores = result.candidate_scores  # ALL frozen candidates
        primary = (selected_definition_id, selected_version)
        primary_prob = prob for (did,ver)==primary
        deltas = [{"definition_id":did,"prob":p,"delta_vs_primary":p-primary_prob,"is_primary":...} ...]
        return [{"opportunity_id":..., "is_extrapolative":True,
                 "warning":EXTRAPOLATIVE_WARNING, "candidate_scores":scores,
                 "primary_definition":primary, "primary_prob":..., "deltas":...}]
# warning: "EXTRAPOLATIVE/OFF-POLICY ADVISORY: alternatives were not exposed; ..."
# Never writes to OptimizationInput path; never reads latent payload; <120 LOC
```

**Reuse:** `commerce/offline_optimizer.py:1119` `score_candidates_extrapolative` never for ranking; primary `prediction_to_advisory` untouched.

---

## Stress sweep snippet

**File:** `simulation/evaluation/stress.py:22` `stress_sweep(world_factory, scenario_id, ns=[60,120,240]) -> dict[n,EvaluationReport]`

```python
def stress_sweep(world_factory, scenario_id="balanced", ns=(60,120,240)):
    for n in ns:
        world = world_factory()  # fresh SimulationWorld per n, SHA256
        outcome = _make_phase7_outcome(world)
        strat = get_strategy(scenario_id)
        bundles,_ ,_ = generate_mature_bundles(world, outcome, n=n, step_hours=24,
                                                conversation_model, fatigue_model, strat, scenario_id)
        train_n = max(6, n*0.5); valid_n = max(5, n*0.25); test_n = n-train-valid
        train,valid,test = chronological_split(bundles, train_n, valid_n, test_n)
        model = train_creator_model(build_creator_dataset_from_world(world,train)).model
        y_true/y_pred = [ex.binary for b in test], [predict_for_input(model,b.input).probability or 0.5]
        metrics = evaluate_predictions(y_true,y_pred)  # Brier/log_loss/calibration
        reports[n] = EvaluationReport(..., dataset_hash=dataset_hash(bundles), metrics=metrics, ...)
# Small ns for unit-test speed; same API scales to 1k/5k/10k/50k/500k (documented, not executed)
```

**Result** `test_e_stress`: `ns=[60,120]` → 2 reports, hashes differ, Brier/log_loss recorded.

---

## Determinism proof (same seed replay)

- **Offer mix:** `get_strategy("whales").sample_content_params(seed123, sim="replay-a", counter=i)` twice → identical sequence `test_a` (deterministic replay same seed+pack `seq1==seq2`).
- **Behavior:** `ScenarioBehaviorModel` same `seed42/sim_a-bh` → same `purchase_propensity` for same fan `test_a` cache.
- **Drift:** `DriftModel(seed, sim_id, start)` `drift_for(fan, early_at)` twice → same `test_b`.
- **Adversarial:** `adversarial_content_weight(day30)` always `0.8`, `day60` always `-0.8` `test_c`.
- **World:** `World(seed11, balanced, n=12, offer_strategy)` twice → identical `opportunity_id, offer_type, price_minor, vault_ids, evidence label` `tests/test_simulation_offer_strategy.py:test_f_replay`.
- **Evaluation:** `run_evaluation(world seed11 balanced n=90)` twice → same `dataset_hash` and `brier` `test_e_e2e_evaluation`.

All deterministic via `SHA256(seed:simulation_id:domain:counter)`, no global random.

---

## Isolation proof (16 FEATURES, hidden_payload not in snapshot)

- **FEATURES:** `commerce/offline_optimizer.py:212` `FEATURE_NAMES` 16 `test_g_isolation` + `test_a_whale_freebie_packs` asserts `len==16`, no `fatigue_score/engagement_score` in names, no `telethon`/`insert into commerce_offers` in `simulation/*.py`.
- **Hidden separation:** `opp.decision_snapshot` JSON lower contains `latent_purchase` 0 hits `test_f_latent_recovery` + `test_d_counterfactual` JSON lower contains no `latent`/`ground_truth`; latent via `ground_truth/*.payload.json` only; `SimulationEvent:67` forbidden keys `{ground_truth, oracle_probability, latent_probability, latent_affinity}` raises `ValueError`.
- **Creator isolation:** `world.create_opportunity` raises if `fan.creator_id != creator.creator_id`; `synthesizer.py:200` `build_creator_dataset` raises `cross-creator bundle` `tests/test_g_contamination` and `test_simulation_evaluation.py` creator isolation.
- **File-only:** `simulation_runs/{id}/` via `simulation/persistence.py` `run_dir`, no Telethon/DB writes, no Redis.

---

## E2E evaluation still 60/15/15 with new packs

**Runner** `simulation/runner.py:41` `run_evaluation(world, outcome, scenario_id, n=90, train_n=60, valid_n=15, test_n=15)` — unchanged.

- New packs `whales/freebie_heavy/temporal_drift/no_signal/adversarial` all via `get_strategy` + same synthesizer path; heterogeneous 500-5000 still yields price_bucket LOW/MID/HIGH, offer_type diverse, `train_creator_model` not abstained with 60/15/15 (≥6 total 2 pos 2 neg).
- **No-signal pack** expected Brier ≈ baseline 0.5 (control); **adversarial** early vs late flip testable via drift helper without breaking 60/15/15 split.
- **Maturity:** 168h `MATURITY_WINDOW_HOURS` via `commerce/opportunity_evidence.py` still, `maturing_as_of(outcome)` `outcome.py:233`.

---

## Tests PASS/FAIL + runtime

**New 8 PASS (2.78s isolated, 6.61s with prior):**

| Test | Assertion |
|------|-----------|
| a_whale_freebie_packs | whales pp > base +0.1, ps < base -0.1, freebie 0.7-0.9 > base+0.1, 10 packs, price diff, replay |
| b_drift | drift early≠late, sign consistent, 90d ~0.27, deterministic |
| c_adversarial_flip | day30 weight 0.8, day60 -0.8, contrib flip sign |
| d_counterfactual | extrapolative scorer returns 3 probs, warning EXTRAPOLATIVE/OFF-POLICY, delta vs primary |
| e_stress | sweep [60,120] 2 reports, hashes differ, Brier/log_loss recorded |
| f_leakage | event ground_truth/oracle raises, snapshot not leaked, CENSORED |
| g_contamination | cross-creator ValueError, price diff, isolation |
| h_temporal | early as_of IMMATURE/CENSORED, future leak blocked |

**Total:** 151 PASS (143 prior +8), no commerce/db modification, DB/Redis UNVERIFIED.

---

## Known issues & Phase 11 readiness

- **No pay-cycle FX:** intentional per audit (no production pay-cycle field) — TimeContext UTC hour/weekend+evening 18-22 only.
- **No 1k-500k executed in CI:** stress sweep API supports ns up to 500k, but unit tests use [60,120,240] for speed; scaling documented, full sweep manual.
- **No AUC:** Brier/log_loss/calibration only (AUC postponed).
- **Phase 11 ready:** YES — 10 packs + drift/adversarial/no-signal + counterfactual harness + stress sweep + failure suite provide foundation for experiment runner full + reports P23 (orchestration: simulate→world→lifecycle→maturity→freeze→train→evaluate→baselines→report per runner.py).

**STOP CONDITIONS met:** FEATURE_NAMES 16, commerce unmodified, latent not leaked, SHA256 determinism, file-only, USD only, no global random.

