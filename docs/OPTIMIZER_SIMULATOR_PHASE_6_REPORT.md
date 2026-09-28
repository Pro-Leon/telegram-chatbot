# Phase 6 — Price Sensitivity / Price Response Report

**Repository:** `E:\chatbot` **Branch:** `main` **HEAD:** `19e188413dbc18ac29ce2ba246cee3a60f361876`
**Phase:** 6 — Price Sensitivity / Price Response (AUDIT → IMPLEMENT → VERIFY)
**Date:** 2026-09-20
**Prereqs:** `docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_2_DESIGN.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_2_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_3_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_4_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_5_REPORT.md`, `simulation/*` Phase 1-5

---

## Status

**PASS** — All 19 Phase 6 price-response tests + 87 prior simulation tests (30 contracts + 11 world + 13 outcome + 16 behavior + 17 content affinity) + 150 optimizer/readiness/maturity tests passed. No production files modified, baseline / behavioral / content models preserved, deterministic Fan×Price response verified.

---

## Audit

**Real price representation verified (actual repository, no invention):**

| Source | File:Symbol | Fields exposed | Constraints | Optimizer-visible mapping |
|--------|-------------|----------------|-------------|---------------------------|
| `OfferDefinition` | `commerce/models.py:218 OfferDefinition` / `db/offer_definitions.py:27` | `price_minor int>=0, currency str, offer_type {SINGLE,SMALL_BUNDLE,CORE_BUNDLE,PREMIUM}, canonical_vault_item_ids 1..10, family_id, allow_download, status, stable_key, version` | `price_minor>=0` enforced `_require_price_minor`, `currency` uppercased `_require_currency`, Vault 1..10 via `canonical_identity_ids`, `(creator,stable_key,version) UNIQUE` | `FrozenCandidate.price_minor int\|None, currency str\|None` → `_price_bucket:244 LOW<1000 MID<3000 HIGH else`, `currency` strip, `family_presence`, `vault_count_bucket`, `offer_type`, `drop_mapping` |
| `SyntheticContent` (simulator mirror) | `simulation/world.py:101 SyntheticContent`, `simulation/snapshot.py:59` | `price_minor int>=0, currency USD default, offer_type, vault_ids, mapped_drop_ids, content_id synthetic_vault:{run}:{counter}` | Validated `price_minor int>=0`, non-empty vault, high-range synthetic ID, no DB FK | `decision_snapshot.eligible[0].price_minor, currency` via `build_decision_snapshot` → frozen `price_minor` bucketed in `extract_features` |
| Optimizer features | `commerce/offline_optimizer.py:212 FEATURE_NAMES 16` / `244 _price_bucket` | `price_bucket {LOW,MID,HIGH,MISSING}` + `currency` + 14 other categorical | Thresholds are prototype-only `LOW<1000`, not data-dependent, no elasticity; price never authority | Frozen pinned-offer fact only, never price recommendation |

**Finding:** Production uses `price_minor` (minor currency units, e.g., cents) + `currency` (USD canonical, uppercased) as authoritative offer price. Simulator already carries `price_minor/currency` in `SyntheticContent` and `DecisionSnapshot`; optimizer sees `price_bucket` + `currency` as frozen facts. No FX, no discount model, no willingness fields in production. Simulator must keep `price_minor/currency/price_bucket` observable and `price_sensitivity, willingness_to_pay, elasticity, price_response` latent.

**Decision:** Reuse existing `price_minor/currency` semantics. Normalization: `normalized_price = price_minor / 2000` linear, reference price `2000` (= MID threshold, mid-range offer), clamped `[0,5]` to keep penalty bounded and modest. USD only; non-USD uses same 2000 scale without invented FX (documented). No new currency conversion.

---

## Price model

**File:** `simulation/price_response.py`

### Price normalization

```python
REFERENCE_PRICE_MINOR = 2000  # USD, linear, prototype reference (MID bucket boundary)
REFERENCE_CURRENCY = "USD"
PRICE_NORMALIZATION_CLAMP_MAX = 5.0  # clamp normalized to 5 (≈10000 minor) to avoid dominance
PRICE_RESPONSE_MODEL_VERSION = "v1"

def normalized_price(price_minor: int) -> float:
    norm = price_minor / 2000.0  # linear
    return clamp(norm, 0.0, 5.0)
# Examples: 100→0.05, 500→0.25, 1000→0.5, 2000→1.0, 3000→1.5, 5000→2.5, 10000→5.0 (clamped), 0→0.0
```

Currency: `currency.strip().upper()` carried verbatim; non-USD not converted, same reference scale (no invented FX), documented as currency-local linear.

### Response function

```python
@dataclass(frozen=True)
class PriceResponse:
    fan_id: int
    price_minor: int
    currency: str
    normalized_price: float ∈ [0,5]  # price_minor / 2000 clamped
    price_multiplier: float = normalized_price  # at reference 1.0
    response: float = price_sensitivity * normalized_price ∈ [0,5]  # latent Fan×Price
    price_response_model_version: str = "v1"
    reference_price_minor: int = 2000

class PriceResponseModel(seed, simulation_id, reference_price_minor=2000, reference_currency="USD", version="v1"):
    def response_for(fan: SyntheticFan, price_minor: int, currency: str, fan_behavior: FanBehavior) -> PriceResponse:
        sensitivity = fan_behavior.price_sensitivity ∈[0,1]  # deterministic from seed/simulation/fan_id
        normalized = _normalized(price_minor, currency)  # deterministic, no RNG
        response = sensitivity * normalized  # monotonic: higher price → higher response → larger penalty downstream
        # caching per (fan_id, price_minor, currency)
```

- Fan variation via `price_sensitivity` (deterministic per fan, Phase 4).
- Price variation via `normalized_price`.
- No extra per-price hash needed; determinism via `price_sensitivity` already deterministic from `seed+simulation_id+fan_id`.
- Bounded: `normalized ∈[0,5]`, `response ∈[0,5]`.

### Sensitivity effect

```python
# latent price effect magnitude controlled by FanBehavior.price_sensitivity
low sensitivity (0.1) → response 0.1*normalized → flat curve (e.g., 500:0.025, 10000:0.5 → range 0.475)
high sensitivity (0.9) → response 0.9*normalized → steep (500:0.225, 10000:4.5 → range 4.275)
```

Continuous, no hard thresholds, no whale boolean.

### Configuration

```python
@dataclass(frozen=True)
class PriceAwareOutcomeConfig:
    intercept: float = -1.0
    purchase_weight: float = 1.5
    engagement_weight: float = 0.8
    relationship_weight: float = 0.6
    price_sensitivity_weight: float = 0.8
    freebie_weight: float = 0.9
    content_affinity_weight: float = 0.8
    price_response_weight: float = 0.6  # explicit, modest, not dominating
    reference_price_minor: int = 2000

# All explicit, finite-checked, not hard-coded in multiple places.
# price_response_weight 0.6 → max extra penalty at 10000: 0.6*1.0*4 =2.4 logit, modest vs purchase 1.5 etc.
```

### Model version

`price_response_model_version = "v1"` in `PriceResponse` and `PriceResponseModel`, part of hidden ground truth, versioned alongside `behavior_model_version v1` and `content_affinity_model_version v1`. Ground truth records both versions.

---

## Outcome integration

**Preserved:** `BaselineOutcomeModel` (global p), `BehavioralOutcomeModel` (fan traits, static price penalty), `ContentAwareBehavioralOutcomeModel` (fan+content, static price) — all unchanged, callable (P16).

**New composable layer (no double-counting, documented):**

```
Baseline (p=0.5)
  ↓
Behavioral (logit = intercept + purchase*prop + eng + rel - price_sens_weight*price_sensitivity - freebie)
  ↓
ContentAware (same + content_affinity_weight*affinity)
  ↓
PriceAware (same base + price deviation)

PriceAware logit:
  logit = intercept
          + purchase_weight*purchase_propensity
          + engagement_weight*engagement_level
          + relationship_weight*relationship_affinity
          - freebie_weight*freebie_tendency
          + content_affinity_weight*content_affinity_score
          + price_term

  where price_term =
    if price_response_weight == 0:
        - price_sensitivity_weight * price_sensitivity   # identical to prior C model
    else:
        - price_sensitivity_weight * price_sensitivity
        - price_response_weight * price_sensitivity * (normalized_price - 1)

Interpretation: baseline price sensitivity at reference price (2000) plus deviation scaled by price_response_weight.
At reference price (2000, normalized=1) → extra 0 → identical to prior C model.
Low price (100, norm 0.05) → extra = 0.6*sensitivity*(-0.95) = -0.57*sensitivity → total penalty 0.8*sens -0.57*sens =0.23*sens (less penalty, higher p).
High price (10000, norm 5) → extra =0.6*sens*4=2.4*sens → total 3.2*sens (more penalty, lower p).

Bounded: normalized clamped 0..5 → deviation -1..4 → extra ∈ [-0.6*sens, 2.4*sens] → total penalty ∈ [0.2*sens, 3.2*sens] max 3.2 logit, not dominating (purchase 1.5 + affinity 0.8 etc. remain multi-dimensional).
Monotonic: higher price → larger normalized → larger extra → more negative logit → lower p (P3).
Heterogeneous: slope ∝ price_sensitivity (P4).

Implementation in `simulation/price_response.py:180` `PriceAwareBehavioralOutcomeModel.probability_for(fan, content)` and `decide(opportunity, fan, content, seed, run_id, counter)` → `SimulatedOutcome(purchased=r<p)` with `r=_hash_float(price_aware_outcome:opp_id)`, `sent_at+1m`, `purchase_at` hash delay 1..48h, `maturity_at` terminal.

Price comes from `content.price_minor` (observable, already in snapshot). No new latent injected into snapshot.

---

## Observable vs latent fields

**Observable (legitimately in production-visible data, may appear in DecisionSnapshot / OptimizationInput / ledger / training rows):**

- `price_minor int`
- `currency str`
- `price_bucket {LOW,MID,HIGH,MISSING}` (derived via `_price_bucket`)
- `offer_type, vault_count_bucket, drop_mapping, family_presence` (content affinity observable)
- `fan_commercial_summary, offer_history_summary, conversation_context` (frozen fan facts)

**Latent (must remain hidden, only in ground truth hidden_payload, never in observable):**

- `FanBehavior.price_sensitivity ∈[0,1]`
- `FanBehavior.purchase_propensity etc.` (all 6 traits)
- `ContentAffinity.score, fan_preference_vector`
- `PriceResponse.normalized_price, response, price_multiplier`
- `willingness_to_pay, reservation price, elasticity`
- `latent_purchase_probability p` (sigmoid logit)
- `price_sensitivity, normalized_price, price_response_value, price_multiplier`

**Verified:** P11 checks snapshot lower no `price_response/willingness/elasticity/price_sensitivity/latent`, ledger lower no `price_response/willingness`, OptimizationInput fields no `price_response/willingness/latent/behavior/price_sensitivity` (but `price_bucket` present via `extract_features`). P12 confirms hidden contains `price_response, normalized_price, price_sensitivity`.

---

## Population results

**1000 fans × 5 prices (500,1000,2000,5000,10000) — seed 60, run pop-price:**

| price_minor | mean response (sensitivity*normalized) | stdev | min | max | range |
|-------------|----------------------------------------|-------|-----|-----|-------|
| 500 | 0.125 | 0.052 | 0.007 | 0.243 | 0.236 |
| 1000 | 0.249 | 0.105 | 0.013 | 0.486 | 0.472 |
| 2000 | 0.498 | 0.209 | 0.027 | 0.971 | 0.945 |
| 5000 | 1.245 | 0.523 | 0.066 | 2.429 | 2.362 |
| 10000 | 2.491 | 1.046 | 0.133 | 4.857 | 4.724 |

- Heterogeneous at each price: stdev 0.05–1.0 >0, range >0.23, monotonic mean increase with price (0.125→2.491).
- Purchase probability by price band (200 fans sampled per price, same PriceAware model weight 0.6):

| price | empirical purchase rate |
|-------|------------------------|
| 500 | 0.570 |
| 1000 | 0.555 |
| 2000 | 0.530 |
| 5000 | 0.380 |
| 10000 | 0.310 |

Directional: higher price → lower aggregate purchase probability (0.57→0.31). Not zero at moderate price (5000 still 0.38), modest decline.

**Sensitivity heterogeneity (100 low vs 100 high sensitivity fans, same prices 500 vs 8000, weight 0.6):**

- Low sensitivity (bottom 100, price_sensitivity ~0.05–0.25) mean p decline (500→8000) = 0.066
- High sensitivity (top 100, ~0.75–0.97) mean decline = 0.269
- Difference 0.203 >0.02, confirms `low → flatter, high → steeper` (P4).

**High-willingness behavior (P6):** 300 fans, low sensitivity <0.3 & high propensity >0.7 candidates ≥10, high price 10000 still purchases >0 and <total (observed 20 candidates, purchased 5/20, not 0 nor all).

**High-sensitivity decline (P7):** mid-propensity 0.3–0.7, low vs high sensitivity groups 30 each, price 500→8000 mean decline low 0.08 vs high 0.32 difference >0.02 PASS.

---

## Ablation matrix

**A Baseline, B Behavioral, C Behavioral+Content, D Behavioral+Content+Price**

| model | class | price handling | callable |
|-------|-------|----------------|----------|
| A | `BaselineOutcomeModel(OutcomeConfig p=0.5)` | no fan traits | PASS |
| B | `BehavioralOutcomeModel(FanBehaviorModel)` | static `-0.8*price_sensitivity` | PASS |
| C | `ContentAwareBehavioralOutcomeModel(FanBehaviorModel+ContentAffinityModel)` | same static + affinity 0.8 | PASS |
| D (weight 0) | `PriceAwareBehavioralOutcomeModel(..., PriceResponseModel, PriceAwareOutcomeConfig(price_response_weight=0.0))` | static identical to C | PASS |
| D (weight 0.6) | same with `price_response_weight=0.6` | static + deviation `0.6*sensitivity*(norm-1)` | PASS |

- C vs D weight 0: `p_c == p_d_zero` within 1e-9 for same fan/content (verified `abs(p_c - p_d_zero)<1e-9` for high price 8000 content) → no price effect when weight 0 (P8).
- C vs D weight 0.6: high price 8000 vs reference 2000 for high sensitivity fan (>0.6) `|p_high - p_ref|>1e-4` and monotonic, demonstrating price changes outcomes when weight positive (D changes vs C).
- All models share `SimulatedOutcome` type, `synthetic_ledger_from_outcome`, `maturing_as_of` unchanged; no optimizer DB writes.

---

## Tests

**New Phase 6 module:** `tests/test_simulation_price_response.py` (19 tests):

| Test | P# | Verifies | Result |
|------|----|----------|--------|
| `test_p1_deterministic_response` | P1 | Same seed/simulation/fan/price same PriceResponse | PASSED |
| `test_p2_bounded` | P2 | 30 fans ×9 prices normalized 0..5 response 0..5 | PASSED |
| `test_p3_price_monotonicity` | P3 | Same fan/content prices 100→10000 p monotonic decreasing (100→10000) | PASSED |
| `test_p4_sensitivity_heterogeneity` | P4 | Low sensitivity variance < high variance (low 0.002 vs high 0.015, high>low*1.2) | PASSED |
| `test_p5_population_variation` | P5 | 1000 fans price 2000 responses uniq>50 stdev>0.05 range>0.5 | PASSED |
| `test_p6_high_price_behavior` | P6 | Low sens high prop at 10000 still some purchase (5/20) | PASSED |
| `test_p7_high_sensitivity_behavior` | P7 | High sens decline > low sens decline by 0.02 (0.32 vs 0.08) | PASSED |
| `test_p8_no_price_effect_at_zero_coeff` | P8 | Weight 0 identical to C model within 1e-9, price not effect, positive weight has effect | PASSED |
| `test_p9_content_compatibility` | P9 | Same content_id different price affinity unchanged | PASSED |
| `test_p10_behavior_compatibility` | P10 | Behavior traits unchanged after price responses | PASSED |
| `test_p11_no_latent_leakage` | P11 | Snapshot/ledger/Input/RowBundle/TrainingExample/Dataset no price_response/willingness, but price_bucket present | PASSED |
| `test_p12_ground_truth_separation` | P12 | Reference no latent, hidden has price_response/normalized/sensitivity | PASSED |
| `test_p13_creator_isolation` | P13 | Creator A price response not affecting B, cross-creator opportunity raises | PASSED |
| `test_p14_run_isolation` | P14 | Different simulation_id different fan_ids and cache isolation, same run same response | PASSED |
| `test_p15_serialization` | P15 | PriceResponse to_dict/from_dict and model to_dict/from_dict deterministic, world reload same normalized | PASSED |
| `test_p16_baseline_compatibility` | P16 | Baseline, Behavioral, ContentAware, PriceAware all callable | PASSED |
| `test_p17_end_to_end_optimizer` | P17 | World→Behavior→Affinity→Price→ledger→evidence→Input→RowBundle→Dataset→train→predict clean | PASSED |
| `test_ablation_matrix` | 19 | A/B/C/D callable, C vs D weight0 identical, C vs D weight positive differs | PASSED |
| `test_population_sanity_multi_price` | 20 | 1000×5 mean/stdev/range, purchase rate 0.57→0.31, low/high decline 0.066 vs 0.269 | PASSED |

**Total simulation tests:** 106 passed (30 contracts + 11 world + 13 outcome + 16 behavior + 17 content affinity + 19 price response) in 4.57s; plus 19 price = 106 total. With 87 prior +19 =106.
**Optimizer/readiness/maturity:** 150 passed (`test_p35_6_offline_optimizer` + `test_p36_optimizer_readiness` + `test_p35_3b_evidence_maturity`) in 3.18s.
**Full unit (filtered, no DB):** `tests/test_simulation_*` + `test_p35_6` = 106 + 150 = 256? Actually `simulation_*` 106 + offline 150 = 256. Integration requiring Postgres/Redis remains **UNVERIFIED** (expected).

---

## End-to-end optimizer

**Path exercised (P17) with multiple prices:**

```
SimulationRun(seed58) → SimulationWorld → FanBehaviorModel → ContentAffinityModel → PriceResponseModel(2000) → PriceAwareBehavioralOutcomeModel(weight0.6)
  → for i=1..12: fan = world.create_fan(creator), price = 500+(i*700)%5000 (varied 500..5000), content = world.create_content(offer_type SINGLE, price_minor=price)
     → affinity = affinity_model.affinity_for(fan, content, beh)
     → price_response = price_model.response_for_content(fan, content, beh) → normalized = price/2000
     → p = sigmoid(logit with affinity*0.8 and price deviation 0.6*sens*(norm-1))
     → outcome = decide(opp, fan, content, counter=i) → r<p purchased, sent_at+1m, purchase_at hash 1..48h
     → synthetic_ledger_from_outcome(opp, outcome) → ledger SENT, purchased_price_minor=price (observable), attribution
     → classify_opportunity_evidence(ledger, as_of=maturity_at) → FULL MATURE SENT
     → build_optimization_input(ledger, evidence) → OptimizationInput frozen price_minor bucketed, no latent
     → RowBundle(input, evidence, ledger)
     → world.clock.advance(24h)
  → build_creator_dataset(creator_id, bundles) → CreatorDataset sorted chronological
  → train_creator_model(dataset) → OfflineModel not abstained (n_primary12 floors 6/2/2)
  → synthetic_ledger_row new opp NONE → build_optimization_input → predict_for_input → probability or abstain, no latent leak
```

**Result: PASS** — price remains visible where production exposes it (`price_minor` in snapshot, `price_bucket` in features), latent `price_response/normalized/willingness` hidden; training succeeds.

Creator isolation holds: price responses per creator via fan_id high range, world.create_opportunity cross-creator raises.

---

## Production isolation

**Concrete evidence (grep `simulation/*.py`):**

- **No Telegram/Telethon:** `grep telethon` in `simulation/` = 0 (new `price_response.py` imports only `hashlib, math, dataclasses, simulation.world`)
- **No Dropfans/Fangate:** no `integrations/dropfans` nor `fangate_transactions` in `simulation/price_response.py`
- **No Redis writes:** no `db/redis` nor `XADD/SADD` nor `enqueue_send`
- **No Fangate transaction writes:** no `INSERT INTO fangate_transactions`
- **No commerce_offers inserts:** no `INSERT INTO commerce_offers`
- **No user-profile mutation:** `FanBehaviorModel.for_fan` and `PriceResponseModel.response_for` cache in-memory, not `users`
- **No pricing authority:** `price_response_weight` influences `p` via logit only, never calls `drop_content_key/create_drop` nor mutates `price_minor` authority (price is frozen fact)
- **No schema migrations:** `db/migrations/*` untouched
- **No optimizer production changes:** `commerce/offline_optimizer.py` not modified (verified `git diff --stat` only `simulation/price_response.py`, `tests/test_simulation_price_response.py`, this report)
- **Ground truth hidden:** `hidden_payload` never merged into ledger; `synthetic_ledger` lower no `price_response`.

**File-only + in-memory:** World `to_dict/from_dict` file-only, `save_ground_truth_reference` optional separate files, no `INSERT INTO commerce_opportunity_decisions`.

---

## Files changed

**New Phase 6:**

```
simulation/price_response.py
tests/test_simulation_price_response.py
docs/OPTIMIZER_SIMULATOR_PHASE_6_REPORT.md
```

**Phase 1-5 preserved (no modifications to production):**

```
simulation/__init__.py, simulation/run.py, simulation/clock.py, simulation/identity.py,
simulation/event.py, simulation/ground_truth.py, simulation/config.py, simulation/data_origin.py,
simulation/persistence.py, simulation/world.py, simulation/snapshot.py, simulation/adapter.py,
simulation/outcome.py, simulation/synthesizer.py, simulation/behavior.py, simulation/content_affinity.py
tests/test_simulation_contracts.py, tests/test_simulation_world.py, tests/test_simulation_outcome.py,
tests/test_simulation_behavior.py, tests/test_simulation_content_affinity.py
docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md,
docs/OPTIMIZER_SIMULATOR_PHASE_2_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_2_DESIGN.md,
docs/OPTIMIZER_SIMULATOR_PHASE_3_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_4_REPORT.md,
docs/OPTIMIZER_SIMULATOR_PHASE_5_REPORT.md
```

**Modified none** production (`commerce/*`, `db/*`, `workers/*`, `core/*`, `integrations/*`) — `git status --porcelain` shows only `?? simulation/price_response.py`, `?? tests/test_simulation_price_response.py`, `?? docs/OPTIMIZER_SIMULATOR_PHASE_6_REPORT.md` plus pre-existing dirty tree (preserved).

---

## Known issues

- `PriceResponseModel` cache keyed `(fan_id, price_minor, currency)` without sensitivity; if same fan/price recomputed with different `price_sensitivity` (should be stable per fan deterministic), cached response would be stale — low risk deterministic per fan, but ad-hoc different sensitivity would need invalidation (documented).
- `REFERENCE_PRICE_MINOR 2000` is prototype linear reference; production price distribution may be skewed, but linear suffices for monotonic test. Log scaling not used to keep simplest; future calibration could switch to log with version bump `v2` without breaking `v1`.
- Currency handling is USD-only linear, non-USD uses same 2000 scale without FX — documented, no invented FX rates, but multi-currency production would need explicit normalization per currency in future.
- `PriceAwareBehavioralOutcomeModel` price deviation is linear `0.6*sensitivity*(norm-1)`; at extreme price 20000 clamped 5 still produces penalty 3.2*sens logit (≈ -3.2 at sens1) — not zero probability but substantially lowered, acceptable per spec “not zero at moderate prices”.
- `SimulationWorld.from_dict` counter heuristic `len(collections)` not hash-perfect for next-id determinism after restore — noted Phase2 limitation, price response recomputation still deterministic via fan_id/price, not counter.
- No content×price interaction modeled (affinity independent of price) — per spec Phase6 keeps simple `Fan×Price` only; interaction reserved for later phases.

---

## Phase 7 readiness

**Ready: YES**

No blocker — Phase 6 Fan×Price deterministic, monotonic, heterogeneous response established, with baselines A/B/C/D preserved and ablations proven. Next phase can build:

- dynamic pricing strategy evaluation using `PriceAwareBehavioralOutcomeModel` to score candidate prices
- offer optimization / bundle economics (price + content affinity joint)
- timing, fatigue, scenario packs without rewriting price-response system (composable via logit)
- file-based `simulation_runs/{id}/price_responses.jsonl` dump via `PriceResponse.to_dict` if needed

No production optimizer change needed before pricing experiments.

**Blockers:** none. Contract stable, versions `v1` behavior, `v1` content affinity, `v1` price response, `p356.features.v1` schema.

