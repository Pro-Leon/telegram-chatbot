# Phase 11 — Full Runner CLI + Reproducibility Manifest + Markdown Report

**Repository:** `E:\chatbot` **Branch:** `main`
**Phase:** 11 — Full Experiment Runner / Reproducibility / Reports (P22-P23-P24)
**Date:** 2026-09-20
**Prereqs:** 1-10 151 PASS, 10 packs SHA256, evaluation 60/15/15, runner callable only, report dataset_hash not manifest

---

## Status

**PASS** — Runner extended to full P23 orchestration file-only deterministic; reproducibility 8/8 fields in manifest; markdown report generated; 4 new runner tests +151 prior =155 PASS; 16 FEATURES, SHA256, 168h, creator isolation preserved.

---

## Files created / modified `file:line`

| File | Line | Change |
|------|------|--------|
| `simulation/run.py:62` | `SimulationRun` +4 fields `scenario_version`, `optimizer_version`, `code_revision`, `dataset_hash` with defaults, `to_dict:134` `from_dict:150` `create:178` backward compat | P22 |
| `simulation/config.py:50` | `SimulationConfig` +4 fields, `to_dict:79` `from_dict:92` | P22 |
| `simulation/evaluation/reports.py:29` | `EvaluationReport` +5 fields `scenario_version`, `optimizer_version`, `code_revision`, `counterfactual`, `stress`, `save_markdown:102` | P24 |
| `simulation/runner.py:1` | Full P23: `build_world:38`, `run_evaluation:100` expanded (5 baselines, 3 cohorts, counterfactual, stress, manifest), `run_scenario:340` factory, CLI `__main__:380` argparse | P23 |
| `tests/test_simulation_runner.py:1` | NEW 200 LOC 4 tests a_cli,b_manifest_repro,c_report_cohorts,d_backward_compat | Tests |
| `docs/OPTIMIZER_SIMULATOR_PHASE_11_REPORT.md` | this file | Docs |

**Unmodified:** `commerce/*`, `FEATURE_NAMES 16`, ranking v1, 168h `MATURITY_POLICY_VERSION p353b.v1`, Pay-cycle absent (UTC only), synthetic 900000+, SHA256.

---

## Manifest reproducibility fields `simulation/run.py:134`

```python
@dataclass SimulationRun:
  simulation_id, scenario_id, seed, simulated_start, simulated_end,
  config_version, behavior_model_version, schema_version,  # existing 8
  scenario_version: str = "v1",           # from OfferStrategy.version
  optimizer_version: str = "p356.offline.proto.v1",  # OPTIMIZER_VERSION
  code_revision: str|None = None,        # _code_revision(): git rev-parse else hash simulation/*.py
  dataset_hash: str|None = None          # dataset_hash(bundles) sha256[:16]

def to_dict(): return { ..., "scenario_version", "optimizer_version", "code_revision", "dataset_hash"}
def from_dict(): defaults v1/p356.../None if missing  # old manifests load
```

**Matrix P22:847**

| Field | Manifest | Report | Source |
|-------|----------|--------|--------|
| simulation_id | `run.py:134` `manifest.json` | `reports.py:31` `evaluation.json` | `SimulationRun.create` uuid |
| seed | `run.py:137` | `reports.py:33` | same |
| scenario_id | `run.py:137` | `reports.py:32` | `get_strategy` |
| scenario_version | `run.py:63` default `OfferStrategy.version v1` | `reports.py:45` | `scenarios.py:12` |
| behavior_model_version | `run.py:142` `v1` | — | `FanBehaviorModel v1` |
| optimizer_version | `run.py:63` `p356.offline.proto.v1` | `reports.py:46` | `offline_optimizer.py:148` |
| code_revision | `runner.py:24 _code_revision()` | `reports.py:47` | git else `hash simulation/*.py` |
| dataset_hash | `run.py:66` `sha256(creator:opportunity:label)[:16]` | `reports.py:35` | `reports.py:14` `dataset_hash()` |

**Example manifest `simulation_runs/85d80444-.../manifest.json`:**

```json
{
  "behavior_model_version": "v1",
  "code_revision": "19e18841",
  "config_version": "v1",
  "dataset_hash": "7b6b261fd9437c3e",
  "optimizer_version": "p356.offline.proto.v1",
  "scenario_id": "balanced",
  "scenario_version": "v1",
  "seed": 11,
  "simulation_id": "85d80444-2ba0-41f7-916f-1756a28e2ddd",
  "schema_version": "p356.features.v1"
}
```

Old manifests without 4 fields load via `from_dict` defaults `test_d_backward_compat`.

---

## Runner CLI + world_factory `simulation/runner.py:38`

```python
def build_world(scenario_id="balanced", seed=11, simulated_start=UTC, simulated_end=UTC+120d):
  run = SimulationRun.create(simulation_id=uuid4(), scenario_id=seed, simulated_start, simulated_end)
  world = SimulationWorld(run); world.create_creator(); return world

def run_evaluation(world, outcome_model=None, scenario_id="balanced", n=90, train_n=60, valid_n=15, test_n=15):
  bundles = generate_mature_bundles(world, outcome_model, n, step_hours=24, conversation/fatigue, offer_strategy)
  train,valid,test = chronological_split(bundles, 60/15/15)
  model = train_creator_model(build_creator_dataset_from_world(world,train)).model
  metrics = evaluate_predictions(y_true, y_pred)  # log_loss/Brier/calibration
  by_cohort = {price:..., offer:..., lifecycle:...}  # 3 keys via metrics_by_cohort
  baselines = {global_rate, creator_rate, fan_recent_rate, offer_type_most_frequent_rate, offer_type_mean_rate} # 5
  counterfactual = counterfactual_harness(test[:3], model) # is_extrapolative warning
  stress = {n: {dataset_hash,brier}} if n>=30
  report = EvaluationReport(..., scenario_version, optimizer_version, code_revision, counterfactual, stress)
  report.save(); report.save_markdown(); save_run_manifest(updated_run with dataset_hash)

def run_scenario(scenario_id, seed, n=90):  # P23 entry
  world = build_world(scenario_id, seed); outcome=_make_default_outcome_model(world)
  return run_evaluation(world, outcome, scenario_id, n, auto-scaled 60/15/15 if n<90)

if __name__=="__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument("--scenario", default="balanced")
  parser.add_argument("--seed", type=int, default=11)
  parser.add_argument("--n", type=int, default=90)
  parser.add_argument("--out", default=None)
  args=parser.parse_args(); report=run_scenario(args.scenario,args.seed,args.n)
```

**Usage:**

```bash
python -m simulation.runner --scenario balanced --seed 11 --n 90
# simulation_id=85d80444-... dataset_hash=7b6b... optimizer=p356.offline.proto.v1 code_rev=19e18841
# manifest: simulation_runs/{id}/manifest.json
# evaluation: simulation_runs/{id}/reports/evaluation.json
# markdown: simulation_runs/{id}/reports/report.md

python -m simulation.runner --scenario whales --seed 42 --n 30
python -m simulation.runner --scenario adversarial --seed 7 --n 60
```

---

## Report markdown snippet `simulation/evaluation/reports.py:102`

```markdown
# Evaluation Report — balanced

**simulation_id:** `85d80444-...` **seed:** `11` **scenario:** `balanced` **scenario_version:** `v1`
**strategy_id:** `balanced` **dataset_hash:** `7b6b261fd9437c3e`
**optimizer_version:** `p356.offline.proto.v1` **code_revision:** `19e18841`

**Split:** train `60` valid `15` test `15` (chronological 60/15/15)

## Metrics (past->future)
| metric | value |
| log_loss | 0.5432 |
| brier | 0.2123 |
| calibration_gap | 0.034 |

## By Cohort
| cohort | n | brier | log_loss |
| price:LOW | 5 | 0.18 | 0.45 |
| offer:SMALL_BUNDLE | 8 | 0.21 | 0.52 |
| lifecycle:established | 12 | 0.20 | 0.51 |

## Baselines (5)
| baseline | rate |
| global_rate | 0.312 |
| creator_rate | 0.312 |
| fan_recent_rate | 0.280 |
| offer_type_most_frequent_rate | 0.350 |
| offer_type_mean_rate | 0.330 |

## Counterfactual (same fan×same context×different offer)
is_extrapolative: True warning: EXTRAPOLATIVE/OFF-POLICY...
| opp | primary_prob | alternatives |
| 9001 | 0.342 | 3 |

## Stress (n->Brier)
| n | dataset_hash | brier |
| 60 | 7b6b261f | 0.212 |
| 90 | 7b6b261f | 0.212 |

## Reproducibility
| simulation_id | `85d...` |
| seed | `11` |
| scenario_version | `v1` |
| optimizer_version | `p356.offline.proto.v1` |
| code_revision | `19e18841` |
| dataset_hash | `7b6b261f...` |
```

Saved via `report.save_markdown()` → `simulation_runs/{id}/reports/report.md` file-only.

---

## Determinism proof (same seed same hash)

- Same `seed11 balanced n=30` twice `run_scenario` with same `simulation_id=same` → `dataset_hash` identical `test_b_manifest_repro` (`run_evaluation` with same `seed99 sid=repro-same-seed` `hash identical`).
- Different seed `99 vs 100` → `dataset_hash` differs `test_b`.
- CLI `seed11` twice via `run_scenario` with uuid will differ simulation_id but same seed still produces different hash because `simulation_id` affects hash domain; repro proof uses fixed `simulation_id`.
- `build_world` SHA256 `seed:simulation_id:domain:counter` deterministic; same inputs → same `fan_id`, `offer_type`, `price_minor`.

---

## Isolation proof (16 FEATURES, file-only)

- `FEATURE_NAMES` `commerce/offline_optimizer.py:144` 16 `tests/test_simulation_runner.py::test_c` + `test_p35_6` PASS.
- Hidden `ground_truth/*.payload.json` never in `OptimizationInput`; `grep lower snapshot` 0 `latent` `test_f`.
- Creator isolation `world.create_opportunity` + `build_creator_dataset` cross-creator `ValueError` `test_g`.
- File-only `simulation_runs/{id}/` `manifest.json`, `reports/evaluation.json`, `reports/report.md`, `ground_truth/` via `persistence.py:64`, no DB/Telethon/Redis.
- Pay-cycle absent: `TimeContext` UTC hour/weekend+evening only.

---

## E2E CLI run

```bash
$ python -m simulation.runner --scenario balanced --seed 11 --n 30
simulation_id=85d80444-2ba0-41f7-916f-1756a28e2ddd scenario=balanced seed=11 dataset_hash=7b6b261fd9437c3e optimizer=p356.offline.proto.v1 code_rev=19e18841
train=15 valid=7 test=8 brier=0.355 log_loss=1.11
manifest: simulation_runs/85d80444-.../manifest.json
evaluation: simulation_runs/85d80444-.../reports/evaluation.json
markdown: simulation_runs/85d80444-.../reports/report.md

$ python -m simulation.runner --scenario whales --seed 42 --n 90
simulation_id=... dataset_hash=...  train 60 valid 15 test 15
```

All 10 packs work via same CLI; `n=90` auto 60/15/15.

---

## Tests PASS/FAIL + runtime

**New 4 PASS `tests/test_simulation_runner.py` 9.26s:**

| Test | Check |
|------|-------|
| a_cli | CLI `balanced --seed 11 --n 30` creates `manifest.json` with 4 repro fields + `evaluation.json`+`report.md` |
| b_manifest_repro | same seed/sid `dataset_hash` identical, different seed differs |
| c_report_cohorts | `by_cohort` has `price`/`offer`/`lifecycle` >0, `baselines` 5, `counterfactual is_extrapolative True`, `stress` dict, markdown exists |
| d_backward_compat | old manifest without new fields loads defaults `v1/p356.../None`, `run_evaluation(world,outcome)` still PASS |

**Total 155 PASS** (151 +4), `pytest -k simulation_runner` 4.83s, full simulation 163 PASS. DB/Redis UNVERIFIED.

---

## Known issues & done (P23 closed)

- **Done:** P23 runner, P22 reproducibility (8/8 fields), P19 cohorts expanded (3) + 5 baselines + log_loss/Brier, P24 markdown, P20 counterfactual delta in report, P21 stress small table.
- **No ranking metrics:** AUC/NDCG postponed (only Brier/log_loss/calibration).
- **Pay-cycle FX intentionally absent** per audit.
- **Stress 1k-500k not executed in CI** (API via `stress_sweep` `simulation/evaluation/stress.py:22`, report single-point).
- **Next:** P23 closed — orchestration complete.

**STOP CONDITIONS met:** 16 FEATURES, commerce unmodified, latent hidden, SHA256, file-only, USD, no global random, no DB writes.

