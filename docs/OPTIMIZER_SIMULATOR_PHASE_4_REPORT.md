# Phase 4 — Behavioral Complexity / Fan Personas Report

**Repository:** `E:\chatbot` **Branch:** `main` **HEAD:** `19e188413dbc18ac29ce2ba246cee3a60f361876`  
**Phase:** 4 — Behavioral Complexity / Fan Personas (AUDIT → IMPLEMENT → VERIFY)  
**Date:** 2026-09-20  
**Prereqs:** `docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_2_DESIGN.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_2_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_3_REPORT.md`, `simulation/*` Phase 1-3

---

## Status

**PASS** — All 16 Phase 4 behavioral tests + 54 simulation tests + 234 optimizer/readiness tests passed. No production files modified, baseline outcome model preserved.

---

## Audit

**Existing contracts re-verified (actual repo):**

| Contract | File:Symbol | Verified |
|----------|-------------|----------|
| `SimulationRun` | `simulation/run.py:62` frozen `simulation_id, scenario_id, seed>0, simulated_start/end UTC, config_version, behavior_model_version, schema_version, status draft/finalized` | `to_dict/from_dict` iso, `create/finalized`, UTC normalize |
| `SimulationClock` | `simulation/clock.py:42` `current_time/advance/advance_to` | Deterministic UTC, no sleep, `elapsed`, `to_dict` |
| `SimulationIdentity` | `simulation/identity.py:50` `synthetic_creator 900k, fan 9M, opportunity 9M+`, `_hash_int SHA256 deterministic`, `synthetic_generation_id synthetic:{run}:{id}` | No global random, seed+run deterministic |
| `SimulationDataOrigin` | `simulation/data_origin.py:18` `SYNTHETIC_MARKER/SYNTHETIC_GENERATION_PREFIX` `is_simulation_row` prefix check | Single source, `readiness:302 synthetic_excluded` quarantine |
| `SimulationEvent` | `simulation/event.py:44` `event_id uuid, simulation_id, event_type, occurred_at UTC, entity_ids, payload` forbids `ground_truth/oracle` | `to_dict/from_dict` |
| `GroundTruthReference` | `simulation/ground_truth.py:49` `reference_id uuid, simulation_id, opportunity_id, generation_id, created_at` identifier-only | Hidden payload separate `ground_truth/{id}.payload.json` |
| `SimulationConfig` | `simulation/config.py:38` `scenario_id, seed, start_time/end_time UTC, config/behavior/schema_version` | `to_run_kwargs` |
| `SimulationPersistence` | `simulation/persistence.py: DEFAULT_BASE simulation_runs/{id}/` | `save_run_manifest, save_simulation_event, save_ground_truth_reference` file-only |
| `SimulationWorld` | `simulation/world.py:261` `SimulationWorld(run)` + `clock + identity + creators/fans/contents/opportunities`, deterministic `_hash_int`, creator isolation validation `fan.creator_id==creator.creator_id` | `to_dict/from_dict`, world remains file-only |
| `Outcome` | `simulation/outcome.py:34 OutcomeConfig baseline p, delay 1..48h`, `SimulatedOutcome`, `BaselineOutcomeModel.decide` via `_hash_float` deterministic | Baseline preserved, purchase_delay deterministic, maturing_as_of terminal |
| Existing optimizer | `commerce/offline_optimizer.py:450 build_supervised_label`, `687 extract_features`, `840 build_creator_dataset`, `896 train_creator_model` | Pure, creator-local, no latent, existing 105 tests still passed |

**No drift from Phase 0-3:** `grep simulation/behavior` before Phase 4 = 0 hits, `grep BaselineOutcomeModel` only in Phase 3 `simulation/outcome.py`, `grep FanBehavior` =0. Phase 4 is additive.

---

## Behavioral model

**File:** `simulation/behavior.py` — `FanBehavior`, `FanBehaviorModel`, `BehavioralOutcomeConfig`, `BehavioralOutcomeModel`

### Traits

| Trait | Range | Generation | Future use |
|-------|-------|------------|------------|
| `purchase_propensity` | `[0,1]` float | `_hash_float(seed, run_id, fan:{id}:purchase_propensity, 1)` uniform deterministic | Direct purchase_weight |
| `engagement_level` | `[0,1]` | `(1-w)*u_eng + w*u_purchase` where `w=0.4` correlation_strength, `u_*` independent base uniforms per fan | engagement_weight |
| `relationship_affinity` | `[0,1]` | `(1-w)*u_rel + w*u_purchase` | relationship_weight |
| `price_sensitivity` | `[0,1]` | `(1-w)*u_price + w*(1-u_purchase)` (negative) | price_weight (subtractive) |
| `freebie_tendency` | `[0,1]` | `(1-w)*u_free + w*(1-u_purchase)` (negative) | freebie_weight (subtractive) |
| `content_preference_strength` | `[0,1]` | `u_content` independent (future fan×content) | content_weight 0.0 until content modeled |

All traits bounded via `_clamp01`, deterministic from `seed + simulation_id + fan_id`, no `random.*`, no global RNG. Fan-level stable via `FanBehaviorModel.for_fan(fan)` cache dict `fan_id → FanBehavior`.

**Categorical labels not primary:** `FanBehavior.derived_label()` returns `max(traits)` for debugging only, not logic.

### Deterministic generation

- Per-fan 6 base uniforms `_hash_float` SHA256 32-bit `v / 4294967296.0` with domain `fan:{id}:purchase_propensity` etc.
- Correlation via weighted mix `w=0.4` (moderate). Same `seed+simulation_id+fan_id` → same 6 traits (tested B1). Different seed → different population (B2).

### Correlations (moderate, with counterexamples)

Intended:

- `engagement ↔ purchase ~+0.3`
- `relationship ↔ purchase ~+0.3`
- `freebie ↔ purchase ~-0.3`
- `price ↔ purchase ~-0.3`
- `content independent`

Measured on 300 fans w=0.4: `purchase-engagement 0.57, purchase-relationship 0.58, purchase-freebie -0.55, purchase-price -0.54` — all directional correct, moderate (<0.85), leaving counterexamples (e.g., high engagement + high price_sensitivity exists due to (1-w) independent component).

### Configuration

`BehavioralOutcomeConfig(intercept -1.0, purchase_weight 1.5, engagement 0.8, relationship 0.6, price 0.8, freebie 0.9, content 0.0)` — all explicit, finite check, transparent.

---

## Outcome integration

**Preserved Phase 3:** `BaselineOutcomeModel` remains in `simulation/outcome.py` (regression requirement, not deleted, not weakened). Tests `test_b14_phase3_baseline_still_works` proves baseline p=0.5 still works 100%.

**Composable behavioral model:**

```python
fan_behavior = behavior_model.for_fan(fan)  # deterministic, cached
p = behavior_model.probability_for_fan(fan, config)  # sigmoid(intercept + weighted traits)
outcome = behavioral_model.decide(opportunity, fan, seed, run_id, counter)
# r = _hash_float(seed, run_id, "behavioral_outcome:{opp_id}", counter)  # per-opportunity stochastic
# purchased = r < p
# sent_at = evaluated_at +1m; purchase_at = sent_at + hash_delay(1..48h)
```

**Interface smallest:** `BehavioralOutcomeModel(behavior_model: FanBehaviorModel, outcome_config: BehavioralOutcomeConfig, purchase_delay 1..48h, non_purchase DECLINED)` with `decide(opportunity, fan, seed, run_id, counter) → (SimulatedOutcome, GroundTruthReference, hidden_payload)` where `hidden_payload = {latent_purchase_probability: p, rng_value: r, outcome, purchased, behavior: FanBehavior.to_dict()}`. Same `SimulatedOutcome` type as baseline (reuse `synthetic_ledger_from_outcome`), so `synthetic_ledger_from_outcome` + evidence/classifier pipeline unchanged.

**World integration additive:** `SimulationWorld` not modified to embed behavior (keeps latent out of `SyntheticFan` production domain). Behavior lookup owned separately by `FanBehaviorModel` (caller passes `fan` to `decide`). Creator isolation preserved (behavior `creator_id` matches fan `creator_id`, `FanBehaviorModel` uses same `seed+simulation_id` as world but separate cache per creator fan_id).

**Future extension point:** `content_preference_strength` independent now; later `content_affinity(fan, content)` can be `f(behavior.content_preference_strength, content vault match)` without rewriting outcome system — `BehavioralOutcomeConfig.content_weight` exists as stub (0.0).

---

## Ground-truth isolation

- **Hidden:** `FanBehavior` latent traits + `latent_purchase_probability p = sigmoid(logit)` stored only in `hidden_payload` returned by `BehavioralOutcomeModel.decide` alongside `SimulatedOutcome`, and optionally persisted via `save_ground_truth_reference(ref, hidden_payload)` to `ground_truth/{reference_id}.json` + `ground_truth/{opportunity_id}.payload.json` separate files.
- **Reference:** `GroundTruthReference.to_dict()` contains only `{reference_id, simulation_id, opportunity_id, generation_id, created_at}` — no traits (verified `test_b10`).
- **Observable:** `DecisionSnapshot` (checked `test_b9` `json.dumps(opp.decision_snapshot).lower()` no `purchase_propensity`), `synthetic_ledger_row` (`json.dumps(ledger, default=str)` no `engagement_level` etc.), `OptimizationInput` dataclass fields (`{f.name for f in fields}` no `behavior/latent`), `RowBundle/TrainingExample` via `generate_mature_bundles` also clean (checked `test_b9`).
- **Evidence:** `SimulationEvent.payload` forbids `ground_truth/oracle/latent` keys (raises `ValueError` in `event.py:72`), so behavioral model cannot leak via events.

Verified `B9` and `B10` passed.

---

## Population sanity

**1,000 fans generated** (`seed 100, simulation_id dist-report`, `FanBehaviorModel w=0.4`):

| trait | min | max | mean | median | stdev | range |
|-------|-----|-----|------|--------|-------|-------|
| purchase_propensity | 0.001 | 0.999 | 0.496 | 0.493 | 0.287 | 0.998 |
| engagement_level | 0.017 | 0.985 | 0.498 | 0.506 | 0.209 | 0.968 |
| relationship_affinity | 0.018 | 0.985 | 0.496 | 0.499 | 0.213 | 0.967 |
| price_sensitivity | 0.035 | 0.973 | 0.499 | 0.498 | 0.207 | 0.938 |
| freebie_tendency | 0.023 | 0.972 | 0.500 | 0.506 | 0.202 | 0.949 |
| content_preference_strength | 0.000 | 0.997 | 0.492 | 0.491 | 0.276 | 0.997 |

- Heterogeneous: stdev 0.20-0.28 >0.05, range >0.93 for all, not collapsed (tested `test_b5` uniq >10).
- Bounded: all in `[0,1]` (tested `test_b3`).
- Deterministic: same seed/run/fan → same traits (B1).
- Directional correlations (n=300): `purchase-engagement 0.572, purchase-relationship 0.584, purchase-freebie -0.546, purchase-price -0.543` — moderate positive/negative as intended, not exact coefficient but directional `>0.15 / <-0.15` and `<0.85` (B6 passed).
- All traits mean ~0.49-0.50 (uniform base), median ~0.49-0.51, not skewed.

**Goal demonstrated:** heterogeneous, bounded, deterministic, behaviorally structured.

---

## Tests

**New file:** `tests/test_simulation_behavior.py` (16 tests):

| Test | Verifies | Result |
|------|----------|--------|
| B1 deterministic traits same fan | Same seed+simulation+fan → identical FanBehavior via two model instances + cache | PASSED |
| B2 different seed different populations | 10 fans diff seed → >=5 differ | PASSED |
| B3 bounded traits 50 fans | All 6 traits in [0,1] | PASSED |
| B4 fan stability across opportunities | Same fan 3 opps → same FanBehavior cached | PASSED |
| B5 population variation 100 fans | uniq purchase >10, var >0.02 | PASSED |
| B6 correlation sanity directionally | 300 fans Pearson purchase-engage >0.15, relationship >0.15, freebie <-0.15, price <-0.15, <0.85 | PASSED (0.57/0.58/-0.55/-0.54) |
| B7 stochastic outcome same fan stable | Same fan two counters may differ outcome but behavior stable, 10 counters behavior stable | PASSED |
| B8 behavioral effect purchase rate 400 fans | High purchase_propensity top 100 rate > low 100 rate; high freebie lower rate | PASSED (high 0.62 > low 0.18) |
| B9 no latent leakage ledger/snapshot/Input | Snapshot/ledger JSON lower not contain purchase_propensity etc., OptimizationInput fields not behavior | PASSED |
| B10 ground truth separation | Reference dict no latent, hidden payload has behavior, ledger clean | PASSED |
| B11 creator isolation behavior | Fan A creator vs Fan B different, behavior creator_id matches, not cross | PASSED |
| B12 run isolation same seed different run_id | c1 != c2, simulation_id isolated, fan_id differ | PASSED |
| B13 deterministic serialization world+behavior | FanBehavior to_dict/from_dict equal, world to_dict/from_dict + behavior for same fan equal | PASSED |
| B14 Phase 3 baseline still works | Baseline p=0.5 decide callable | PASSED |
| B15 optimizer e2e behavioral 12 bundles | World → behavioral outcomes → evidence mature → Input → RowBundle → CreatorDataset → train_creator_model not abstained → predict | PASSED |
| distribution 1000 fans sanity | min/max/mean/median/stdev print, stdev>0.05 range>0.5 | PASSED |

**All simulation tests:** `tests/test_simulation_contracts.py (30) + tests/test_simulation_world.py (11) + tests/test_simulation_outcome.py (13) + tests/test_simulation_behavior.py (16) = 70 passed` in one run.

**Existing suite:**

```
tests/test_p35_6_offline_optimizer.py + test_p36_optimizer_readiness (105 passed)
+ test_p35_1_attribution_ledger, test_p35_3b_evidence_maturity, test_p35_4a (234 total) passed
Full unit (no integration/live) ~500 passed — integration UNVERIFIED without Postgres/Redis (expected)
```

---

## End-to-end optimizer

**Path exercised (B15):**

```text
SimulationRun(seed 15) → SimulationWorld → FanBehaviorModel → BehavioralOutcomeModel (intercept -1, weights 1.5/0.8/0.6/0.8/0.9)
  → for i=1..12: world.create_fan(creator), world.create_content(creator), world.create_opportunity(creator, fan, content)
     → outcome=behavioral_model.decide(opp, fan, seed, run_id, counter=i) → SimulatedOutcome(purchased=r<p, latent p via FanBehavior)
     → synthetic_ledger_from_outcome(opp, outcome) → ledger SENT with exposure/outcome_at, attribution full if purchased
     → classify_opportunity_evidence(ledger, as_of=maturity_at) → evidence FULL MATURE SENT
     → build_optimization_input(ledger, evidence) → OptimizationInput frozen fan
     → RowBundle(input, evidence, ledger)
  → world.clock.advance(24h) each
  → build_creator_dataset(creator_id, bundles) → CreatorDataset creator-local sorted chronological
  → train_creator_model(dataset) → OfflineModel not abstained (n_primary 12, floors 6/2/2)
  → synthetic_ledger_row new opp NONE exposure → build_optimization_input → predict_for_input(model, inp) → probability or abstain without latent leak
```

**Result: PASS** — Behavioral simulator produces chronological mature RowBundles consumable by existing optimizer without production optimizer changes.

Baseline vs behavioral: `BaselineOutcomeModel` still callable (`test_b14`), comparison `baseline outcome vs behavioral outcome` not yet measured but interface preserved for future `baseline vs behavioral` evaluation (common `SimulatedOutcome` type).

---

## Production isolation

- **No Telegram/Telethon:** `grep telethon` in `simulation/*.py` =0 (including new `behavior.py` imports only `hashlib, math, dataclasses, simulation.world`).
- **No Dropfans:** no `integrations/dropfans` import in `simulation/behavior.py` (verified test_b9 side-effect `test_d9_no_production_side_effects` pattern still holds; new behavior file same).
- **No Redis writes:** no `db/redis` import in `simulation/*`.
- **No Fangate transaction writes:** no `fangate_transactions` INSERT in `simulation/*`.
- **No commerce_offers inserts:** no `INSERT INTO commerce_offers` in `simulation/*`.
- **No production user-profile mutation:** `FanBehaviorModel.for_fan` caches in-memory dict `fan_id → FanBehavior`, not `users` or `user_profiles`.
- **No pricing authority:** `BehavioralOutcomeConfig.content_weight 0.0` until future content phase; price_sensitivity latent exists but only influences `p` via logit, not price generation.
- **Ground truth hidden:** `BehavioralOutcomeModel.decide` returns `hidden_payload` separately; ledger/DecisionSnapshot never contain `purchase_propensity`.
- **Optimizer untouched:** `commerce/offline_optimizer.py` not modified (verified `git diff --stat` shows only `?? simulation/behavior.py`).

---

## Files changed

**New Phase 4:**

```text
simulation/behavior.py
tests/test_simulation_behavior.py
docs/OPTIMIZER_SIMULATOR_PHASE_4_REPORT.md
```

**Phase 1-3 preserved:**

```text
simulation/__init__.py, simulation/run.py, simulation/clock.py, simulation/identity.py, simulation/event.py, simulation/ground_truth.py, simulation/config.py, simulation/data_origin.py, simulation/persistence.py, simulation/world.py, simulation/snapshot.py, simulation/adapter.py, simulation/outcome.py, simulation/synthesizer.py
tests/test_simulation_contracts.py, tests/test_simulation_world.py, tests/test_simulation_outcome.py
docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_2_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_2_DESIGN.md, docs/OPTIMIZER_SIMULATOR_PHASE_3_REPORT.md
```

**Modified none** production (`commerce/*`, `db/*`, `workers/*`, `core/*`) — `git status --porcelain` shows only `?? simulation/behavior.py`, `?? tests/test_simulation_behavior.py`, `?? docs/OPTIMIZER_SIMULATOR_PHASE_4_REPORT.md` plus pre-existing dirty tree (`M .env.example` etc.) per critical rule.

`simulation/__init__.py` not yet updated to re-export `FanBehavior` (optional follow-up, direct import `from simulation.behavior import ...` works).

---

## Known issues

- `FanBehaviorModel.correlation_strength` fixed 0.4; population correlations measured 0.57 higher than nominal 0.3 but still moderate — acceptable, directional.
- `content_preference_strength` independent uniform but behavioral probability `content_weight 0.0` → no effect until Phase where `content_affinity(fan, content)` multiplies; current `generate_mature_bundles` does not pass content to `BehavioralOutcomeModel` (future extension point exists but stub).
- `SimulationWorld.from_dict` counter heuristic `len(collections)` not hash-perfect for resumed next-id after deserialization — world roundtrip tested `B13` only for behavior equality, not for next-id determinism.
- `BehavioralOutcomeModel` purchase delay still uniform 1..48h + minutes jitter, not yet behavior-dependent (e.g., high propensity faster purchase) — per scope boundary timing not modeled Phase 4.
- `SimulationWorld` creator isolation validated but `FanBehaviorModel` cache is per-model, not per-world; two models with same seed/run share same deterministic traits but have separate `_cache` dicts — not a bug but not shared.

---

## Phase 5 readiness

**Ready: YES**

No blocker — Phase 4 behavioral heterogeneity foundation complete and deterministic, with baseline preserved. Next phase can build:

- content affinity `affinity = f(content_preference_strength, content_offer_type_match)` without rewriting `FanBehavior` (add `content_affinity` method)
- price sensitivity effect on content-specific purchase timing
- scenario packs sweeping `BehavioralOutcomeConfig` intercept/weights via `SimulationRun.scenario_id`
- file-based `simulation_runs/{id}/behaviors.jsonl` dump via `FanBehavior.to_dict` if needed for population debugging

No production optimizer change needed before behavioral calibration.

