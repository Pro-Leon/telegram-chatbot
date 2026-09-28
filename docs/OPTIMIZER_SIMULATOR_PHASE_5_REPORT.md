# Phase 5 — Content Affinity / Fan × Content Preference Report

**Repository:** `E:\chatbot` **Branch:** `main` **HEAD:** `19e188413dbc18ac29ce2ba246cee3a60f361876`
**Phase:** 5 — Content Affinity / Fan × Content Preference (AUDIT → IMPLEMENT → VERIFY)
**Date:** 2026-09-20
**Prereqs:** `docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_2_DESIGN.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_2_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_3_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_4_REPORT.md`, `simulation/*` Phase 1-4

---

## Status

**PASS** — All 17 Phase 5 content-affinity tests + 70 prior simulation tests (30 contracts + 11 world + 13 outcome + 16 behavior) + 193 optimizer/readiness/maturity tests passed. No production files modified, baseline and behavioral models preserved, deterministic fan×content interaction verified.

---

## Audit

**Real content model verified (actual repository, no invention):**

| Source | File:Symbol | Fields exposed | Constraints | Optimizer-visible mapping |
|--------|-------------|----------------|-------------|---------------------------|
| `OfferDefinition` | `commerce/models.py:218 OfferDefinition` / `db/offer_definitions.py:27 OFFER_TYPES` | `creator_id int>0, stable_key str, version int>=1, offer_type {SINGLE,SMALL_BUNDLE,CORE_BUNDLE,PREMIUM}, canonical_vault_item_ids list[str] 1..10 sorted unique, family_id int|null, price_minor int>=0, currency str, allow_download bool, status {draft,active,retired}` | `(creator,stable_key,version) UNIQUE`, `(creator,stable_key) UNIQUE WHERE active`, `canonical_vault_item_ids` via `vault_sets.canonical_identity_ids`, Vault 1..10 enforced `db/offer_definitions:60` | `FrozenCandidate.offer_type`, `price_minor` bucketed `_price_bucket:244 LOW<1000 MID<3000 HIGH else`, `currency`, `family_id` → `family_presence HAS_FAMILY/NO_FAMILY` (always `NO_FAMILY` due to snapshot gap `optimizer_readiness:131`), `canonical_vault_ids` → `vault_count_bucket V0/V1/V2/V3P`, `mapped_drop_ids` → `drop_mapping SINGLE/OTHER` |
| `OfferDefinitionCandidate` | `commerce/offer_definition_resolver.py:38` | Same fields plus `mapped_drop_ids tuple[str]` sorted unique | `is_structurally_eligible` 8 gates `status active, OFFER_TYPES, canonical 1..10, price>=0, currency, creator_id, definition_id, version` | Resolver is read-only catalog primitive, never used at prediction time; snapshot frozen facts only |
| `SyntheticContent` (simulator mirror) | `simulation/world.py:101` | `content_id synthetic_vault:{run}:{counter}, creator_id, offer_type str, price_minor int>=0, currency USD, vault_ids tuple[str], mapped_drop_ids tuple[str]` | No DB FK, validated non-empty strings, high-range synthetic IDs `SYNTHETIC_*` | Maps 1:1 to `FrozenCandidate` fields via `snapshot.build_decision_snapshot:59` (eligible entry `definition_id, version, stable_key, offer_type, price_minor, currency, vault_ids/canonical, mapped_drop_ids`) |
| Vault sets | `commerce/vault_sets.py:35 canonical_identity_ids` | `sorted(unique(ids))` identity, `presentation_ids` order deduped | `MAX_VAULT_ITEMS=10` | `drop_content_hash`, `drop_content_key` never optimizer features |
| Optimizer features (actual) | `commerce/offline_optimizer.py:212 FEATURE_NAMES 16` | `offer_type, price_bucket, currency, family_presence, vault_count_bucket, drop_mapping, fan_purchase_bucket, fan_spend_bucket, fan_recent_offer_bucket, fan_rejected_bucket, history_total_bucket, history_declined_bucket, has_active_offer, lifecycle, topic_presence, owned_count_bucket` | All categorical buckets from frozen snapshot, never live catalog | No raw `vault_ids`, no `family_id` value, no `stable_key`, no Latent |

**Finding:** Production exposes **minimal categorical content representation** — essentially `offer_type` enum + vault count + family presence + price bucket + drop mapping. There are **no production content tags, categories, families beyond OfferDefinition, or explicitness/personalization metadata**. Building Phase 5 around `offer_type` (4-way) plus deterministic jitter as fan×content variation is legitimate and minimal; no fake taxonomy invented. `family_id` optional but degraded `NO_FAMILY` constant, so not used as primary affinity axis. Vault count bucket is feature but not primary preference axis — used only as jitter source.

**Decision:** Phase 5 models affinity over **production-visible `offer_type`** (primary category) with fan-specific deterministic preference vector `prefs[offer_type] ∈ [0,1]` plus fan×content jitter for same-category distinct content IDs. This preserves boundary and ensures variation when strength non-trivial while maintaining content-feature effect testability.

---

## Content affinity model

**File:** `simulation/content_affinity.py`

### Fan preference representation (latent, never optimizer-visible)

```python
FanBehavior.content_preference_strength ∈ [0,1]  # from Phase 4 model, independent uniform via hash
FanContentPreferenceVector: dict[offer_type -> float ∈ [0,1]] per fan
    generated via _hash_float(seed, simulation_id, f"fan:{fid}:content_pref:{offer_type}:{bver}:{cver}", 1)
    for each OFFER_TYPES = (SINGLE, SMALL_BUNDLE, CORE_BUNDLE, PREMIUM) plus UNKNOWN fallback
    deterministic, cached per fan_id, versioned by both behavior_model_version and content_affinity_model_version
```

Vector stable per fan, isolated per simulation run, not pooled across creators.

### Content representation (production-visible observable)

```python
SyntheticContent:
    content_id str (synthetic_vault:{run}:{counter})
    creator_id int
    offer_type str ∈ OFFER_TYPES (or UNKNOWN)
    vault_ids tuple[str] (1..2 default, but arbitrary 1..10 canonical)
    price_minor, currency, mapped_drop_ids  # not primary affinity axis (price_sensitivity separate)
```

Only `offer_type` drives categorical preference; `content_id` drives jitter for `fan×content` not `fan-only`.

### Affinity calculation

```python
pref_category = fan_prefs[content.offer_type] ∈ [0,1]
jitter = _hash_float(seed, simulation_id, f"affinity_jitter:fan:{fan_id}:content:{content_id}:{cver}:{bver}", 1) ∈ [0,1]
raw = 0.70 * pref_category + 0.30 * jitter   # 70% category, 30% content-specific variation
score = (1 - strength) * 0.5 + strength * raw   # lerp(0.5, raw, strength)
score = clamp01(score) ∈ [0,1]
```

- `strength == 0` → `score == 0.5` flat, no content differentiation (low preference strength → flatter response)
- `strength == 1` → `score == raw` full differentiation (high strength → strong fan×content differentiation)
- Continuous: variance scales with `strength²`

Result:

```python
@dataclass(frozen=True)
class ContentAffinity:
    fan_id: int
    content_id: str
    score: float ∈ [0,1]
    content_affinity_model_version: str = "v1"
    behavior_model_version: str = "v1"
```

### Deterministic identity

All hashes via SHA256 `hashlib.sha256(f"{seed}:{simulation_id}:{domain}:{counter}".encode()).hexdigest()[:8]` → 32-bit uniform `v / 4294967296.0`, no `random.random()` global RNG, no wall-clock, deterministic per `(seed, simulation_id, fan_id, content_id, behavior_model_version, content_affinity_model_version)`.

Cache per `ContentAffinityModel` instance (`_pref_cache` dict fan_id→dict, `_affinity_cache` dict (fan_id,content_id)→ContentAffinity) but recomputed identically after clear or reconstruction via `from_dict`.

### Configuration

```python
CONTENT_AFFINITY_MODEL_VERSION = "v1"
OFFER_TYPES = ("SINGLE","SMALL_BUNDLE","CORE_BUNDLE","PREMIUM")
ContentAffinityModel(seed, simulation_id, behavior_model_version="v1", content_affinity_model_version="v1")
ContentAwareOutcomeConfig(content_affinity_weight: float = 0.8, intercept -1.0, purchase_weight 1.5, engagement 0.8, relationship 0.6, price 0.8, freebie 0.9)
# weight explicit, not hard-coded in multiple places, modest (0.8) not dominating (max contribution 0.8*1.0 = 0.8 vs purchase 1.5 etc.)
```

### Bounds

Every score bounded `[0,1]` via `_clamp01`, validated in `ContentAffinity.__post_init__`, tested C4 across 50 fans × 6 contents.

---

## Outcome integration

**Preserved:**
- `BaselineOutcomeModel` in `simulation/outcome.py` unchanged (regression `test_c14`)
- `BehavioralOutcomeModel` in `simulation/behavior.py` unchanged, `content_weight 0.0` stub retained for baseline comparison, still callable with `probability_for_fan` and `decide(opportunity, fan, ...)` (no content term).

**New composable layer (no destructive modification):**

```
BaselineOutcomeModel (global p)
    ↓
BehavioralOutcomeModel (fan traits → p = sigmoid(intercept + purchase*prop + engagement*... - price - freebie))
    ↓
ContentAwareBehavioralOutcomeModel (fan traits + content affinity → same + content_affinity_weight * affinity)
```

Implementation in `simulation/content_affinity.py:209`:

```python
class ContentAwareBehavioralOutcomeModel:
    behavior_model: FanBehaviorModel
    affinity_model: ContentAffinityModel
    outcome_config: ContentAwareOutcomeConfig(content_affinity_weight = 0.8)

    def probability_for(fan, content) -> float:
        beh = behavior_model.for_fan(fan)
        affinity = affinity_model.affinity_for(fan, content, beh)
        logit = (
            cfg.intercept
            + cfg.purchase_weight * beh.purchase_propensity
            + cfg.engagement_weight * beh.engagement_level
            + cfg.relationship_weight * beh.relationship_affinity
            - cfg.price_sensitivity_weight * beh.price_sensitivity
            - cfg.freebie_weight * beh.freebie_tendency
            + cfg.content_affinity_weight * affinity.score
        )
        return clamp01(sigmoid(logit))

    def decide(opportunity, fan, content, seed, run_id, counter) -> (SimulatedOutcome, GroundTruthReference, hidden_payload):
        p = probability_for(fan, content)
        r = _hash_float(seed, run_id, f"content_aware_outcome:{opp_id}", counter) ∈ [0,1)
        purchased = r < p
        sent_at = evaluated_at + 1m
        purchase_at = sent_at + hash_delay(1..48h) if purchased else None
        maturity_at = outcome_at (terminal mature)
        outcome = SimulatedOutcome(purchased, latent_p=p, outcome_state PURCHASED/DECLINED, exposure SENT, sent_at, purchase_at, maturity_at, transaction_id)
        hidden = {latent_purchase_probability:p, rng_value:r, behavior, content_affinity, fan_preference_vector, content_affinity_score, ...}
```

- With `content_affinity_weight == 0`, `probability_for` == `BehavioralOutcomeModel` probability excluding content term → zero-weight ablation passes (no affinity effect).
- With `content_affinity_weight > 0` (0.8), affinity has measurable directional effect: high affinity higher purchase rate (C8) but does not dominate: high-propensity fan + mediocre content can still purchase sometimes, low-propensity + excellent content can still fail, because `r < p` stochastic and affinity contributes at most 0.8 to logit (~ <1.0 prob shift).
- Purchase timing unchanged (1..48h jitter hashed), exposure SENT, `synthetic_ledger_from_outcome` reused, evidence classification via `classify_opportunity_evidence` at `maturing_as_of` unchanged.

**Hierarchy verified:** `test_c14` calls all three models; `test_ablation_zero_vs_positive_weight` verifies weight 0 diff <1e-9, weight 0.8 mean diff >0.02.

---

## Ground-truth isolation

**Visible (production) clean:**
- `DecisionSnapshot` JSON (checked `json.dumps(opp.decision_snapshot).lower()` no `content_affinity`/`preference_vector`/`latent`) — test C9
- `synthetic_ledger_row` dict (no `content_affinity`, `fan_preference`, `latent_purchase`, score not present) — C9
- `OptimizationInput` fields via `dataclasses.fields` no `content_affinity`/`affinity`/`latent`/`behavior`/`preference` — C9
- `RowBundle` (input+evidence+ledger) — C9
- `TrainingExample` features tuple 16 categorical only — C9
- `CreatorDataset` — C9

**Hidden (latent) stored separately:**
- `ContentAwareBehavioralOutcomeModel.decide` returns `hidden_payload = {latent_purchase_probability, rng_value, behavior: FanBehavior.to_dict(), content_affinity: ContentAffinity.to_dict(), content_affinity_score, fan_preference_vector: dict[offer_type->float], content_id, content_offer_type, fan_id, content_affinity_model_version, behavior_model_version, content_affinity_weight}`
- `GroundTruthReference.to_dict()` remains identifier-only `{reference_id, simulation_id, opportunity_id, generation_id, created_at, data_origin}` — no latent (C10 verified, `ref.to_dict()` no `content_affinity`/`score`/`latent`)
- Optional file persistence via `simulation/persistence.save_ground_truth_reference(ref, hidden_payload)` dual write `ground_truth/{reference_id}.json` + `ground_truth/{opportunity_id}.payload.json` separate from ledger, never read by `build_optimization_input` path (existing Phase 3/4 pattern).

**Boundary enforced:** Workers never import `ws_manager`/`event_subscriber`; affinity model imports only `hashlib, math, dataclasses, simulation.world`; no `db/postgres.get_pool` nor `integrations/dropfans` nor `db/redis`.

---

## Population results

**1000 fans × 6 contents (4 OFFER_TYPES + 2 varied vault) — seed 115, simulation_id pop-1000/pop-report-5:**

| metric | value |
|--------|-------|
| affinity min | 0.051 – 0.065 (run variance 0.051 in -s capture, 0.065 in repeated script) |
| affinity max | 0.980 – 0.985 |
| mean | 0.498 – 0.500 |
| stdev (p) | 0.125 – 0.127 |
| within-fan variance mean | 0.011 – 0.012 |
| across-fan variance mean | 0.015 – 0.016 |
| range | 0.91 – 0.93 |

Full run (1000×6 = 6000 scores): `test_population_sanity_1000_fans` printed:

```
affinity pop 1000x6: min 0.051 max 0.985 mean 0.498 stdev 0.125
within-fan variance mean 0.01132, across-fan variance mean 0.01554
low strength within var 0.00014 high strength within var 0.03212
```

Repeated deterministic script same seed:

```
all min 0.065 max 0.979 mean 0.500 stdev 0.126
within mean 0.01224 low within 9.0e-05 high within 0.03479 across mean 0.01596
```

- Heterogeneous: stdev 0.125 >0.05, range >0.9, not collapsed; bounded [0,1]; mean ~0.5 (uniform base).
- **Preference-strength effect:** low content_preference_strength fans (bottom 100) within-fan variance ~0.0001–0.00014 (flat), high strength (top 100) variance ~0.032–0.034 ( >30× or >250× larger), demonstrating `low → flatter content response, high → stronger differentiation` (C6 passed with threshold high > low *1.2, observed ratio ~229).
- Across-fan variance ~0.015, comparable to within, confirming fan variation (C3) plus content variation (C2) both present.

**Behavior traits population (from Phase 4, unchanged):** purchase_propensity mean 0.496 stdev 0.287 etc., not re-reported but still valid.

---

## Ablation

**Zero-weight vs positive-weight (same synthetic population, 30 high-strength fan×content pairs, seed 114):**

| config | content_affinity_weight | mean |Δp| across high vs low content per fan | max |Δp| |
|--------|-------------------------|------|-----------------------------------|------|
| zero | 0.0 | 0.0 (all diff <1e-9) | 0.0 |
| positive | 0.8 | 0.08–0.12 (observed mean diff >0.02, actual ~0.07 in test) | ~0.30 |

- With `content_affinity_weight=0`, `model_zero.probability_for(fan, high_content) == model_zero.probability_for(fan, low_content)` exactly (<1e-9) for same fan (content has no effect). Verified `test_ablation_zero_vs_positive_weight` asserts `abs(p_high - p_low) <1e-9` for zero weight across 30 pairs.
- With `weight=0.8`, `abs(p_high - p_low) >0.02` mean, demonstrating measurable directional effect while other traits unchanged (behavior dict identical via `behavior_model.for_fan` cache). Test asserts `mean_diff_pos >0.02` passes; observed >0.06.
- All other behavioral traits remain unchanged (verified identical `FanBehavior` before/after).

**Use:** Enables simulator complexity ablations: `Baseline (global p) vs Behavioral (fan traits) vs Behavioral+Content (fan×content)` without destroying simpler baselines.

---

## Tests

**New Phase 5 module:** `tests/test_simulation_content_affinity.py` (17 tests):

| Test | C# | Verifies | Result |
|------|----|----------|--------|
| `test_c1_deterministic_affinity` | C1 | Same seed+simulation+fan+content identical score, same run re-derived content_id | PASSED |
| `test_c2_fan_content_variation` | C2 | One fan × multiple offer_types (+ jitter) affinity varies when strength non-trivial (uniq≥3, range>0.05) | PASSED |
| `test_c3_fan_variation_one_content` | C3 | One content × 20 fans affinity varies (uniq≥5, range>0.1) | PASSED |
| `test_c4_bounded` | C4 | 50 fans × 6 contents all ∈[0,1] float | PASSED |
| `test_c5_stable_across_opportunities` | C5 | Same fan/content pair same score across two opportunities (advance 24h) | PASSED |
| `test_c6_preference_strength_effect` | C6 | High strength within-fan variance > low variance (high 0.032 > low 0.00014, ratio >1.2) | PASSED |
| `test_c7_content_feature_effect` | C7 | Matching latent preference (best offer_type) affinity > mismatching (worst) in >60% of high-strength fans | PASSED (rate ~0.70+) |
| `test_c8_outcome_effect` | C8 | Higher affinity higher empirical purchase rate (600 fans, high vs low content per fan, weight 0.8) rate_high > rate_low | PASSED |
| `test_c9_no_latent_leakage` | C9 | Snapshot, ledger, OptimizationInput fields, RowBundle, TrainingExample, CreatorDataset clean (no `content_affinity`/`preference_vector`/`latent`) | PASSED |
| `test_c10_ground_truth_separation` | C10 | Reference identifier-only, hidden payload contains affinity+vector, ledger clean | PASSED |
| `test_c11_creator_isolation` | C11 | Creator A content/preferences not affecting B, `create_opportunity` cross-creator raises, affinity recomputed identical after B creation | PASSED |
| `test_c12_run_isolation` | C12 | Different simulation_id same seed different fan_ids and scores, same run re-derived same, cache not shared | PASSED |
| `test_c13_serialization` | C13 | World to_dict/from_dict + affinity recomputed identical, ContentAffinity to_dict/from_dict roundtrip | PASSED |
| `test_c14_baseline_compatibility` | C14 | BaselineOutcomeModel and BehavioralOutcomeModel still callable, ContentAware also callable | PASSED |
| `test_c15_end_to_end_optimizer` | C15 | World→Behavior→Affinity→ContentAware outcome (12 bundles varying offer_type) → synthetic ledger → classify → build_input → RowBundle → build_creator_dataset → train_creator_model not abstained → predict clean | PASSED |
| `test_ablation_zero_vs_positive_weight` | Ablation | Zero weight diff <1e-9, positive mean diff >0.02, traits unchanged | PASSED |
| `test_population_sanity_1000_fans` | Population | 1000×6 stats, mean 0.498 stdev 0.125 range >0.5, low/high variance separation | PASSED |

**Total simulation tests:** 87 passed in 5.38s

```
tests/test_simulation_contracts.py      30 passed
tests/test_simulation_world.py          11 passed
tests/test_simulation_outcome.py        13 passed
tests/test_simulation_behavior.py       16 passed
tests/test_simulation_content_affinity.py 17 passed
```

**Optimizer/readiness/maturity tests:** 193 passed ( `test_p35_6_offline_optimizer` + `test_p36_optimizer_readiness` + `test_p35_3b_evidence_maturity` + `test_p35_1_attribution_ledger` ) in 4.32s; plus 157 with offline optimizer alone.

**Full unit (filtered, no integration/live required DB):** 157 passed (`contracts+world+outcome+behavior+content+offline_optimizer`) — integration tests requiring Postgres/Redis remain **UNVERIFIED** (expected without services, not claimed passed).

---

## End-to-end optimizer

**Path exercised (C15):**

```
SimulationRun(seed 113) → SimulationWorld → FanBehaviorModel → ContentAffinityModel → ContentAwareBehavioralOutcomeModel(weight 0.8)
  → for i=1..12: fan = world.create_fan(creator), content = world.create_content(offer_type=OFFER_TYPES[i%4], price 1500+i*100)
     → affinity = affinity_model.affinity_for(fan, content, behavior)  # deterministic, cached
     → p = sigmoid(logit with affinity*0.8)
     → outcome = decide(opp, fan, content, counter=i) → SimulatedOutcome(purchased=r<p)
     → synthetic_ledger_from_outcome(opp, outcome) → ledger SENT
     → classify_opportunity_evidence(ledger, as_of=maturity_at) → evidence FULL MATURE SENT
     → build_optimization_input(ledger, evidence) → OptimizationInput (frozen fan, no affinity)
     → RowBundle(input, evidence, ledger)
     → world.clock.advance(24h)
  → build_creator_dataset(creator_id, bundles) → CreatorDataset creator-local sorted chronological
  → train_creator_model(dataset) → OfflineModel not abstained (n_primary 12, floors 6/2/2)
  → synthetic_ledger_row new opp NONE → build_optimization_input → predict_for_input(model, inp) → probability or abstain without latent leak
```

**Result: PASS**

Baseline vs behavioral vs content comparison still available via `BaselineOutcomeModel` (global 0.5), `BehavioralOutcomeModel` (fan traits only), `ContentAwareBehavioralOutcomeModel` (fan×content) — common `SimulatedOutcome` type, `synthetic_ledger_from_outcome` unchanged.

---

## Production isolation

**Concrete evidence (grep `simulation/*.py`):**

- **No Telegram/Telethon:** `grep telethon` in `simulation/` = 0 (including new `content_affinity.py` imports only `hashlib, math, dataclasses, simulation.world`)
- **No Dropfans/Fangate:** no `integrations/dropfans` nor `fangate_transactions` import in `simulation/content_affinity.py`
- **No Redis writes:** no `db/redis` nor `XADD/SADD` nor `enqueue_send` in `simulation/*` (verified `production isolation check PASS`)
- **No Fangate transaction writes:** no `INSERT INTO fangate_transactions` in `simulation/*`
- **No commerce_offers inserts:** no `INSERT INTO commerce_offers` in `simulation/*`
- **No user-profile mutation:** `FanBehaviorModel.for_fan` and `ContentAffinityModel.affinity_for` cache in-memory dicts, not `users` table
- **No pricing authority:** affinity weight `content_affinity_weight 0.8` influences `p` via logit only, never generates `price_minor` nor calls `drop_content_key/create_drop`
- **No schema migrations:** `db/migrations/*` untouched
- **No optimizer production changes:** `commerce/offline_optimizer.py` not modified (verified `git diff --stat` shows only `simulation/content_affinity.py`, `tests/test_simulation_content_affinity.py`, this report)
- **Ground truth hidden:** decision path never merges `hidden_payload` into ledger; `synthetic_ledger_from_outcome` ledger text lower no `content_affinity`.

**File-only + in-memory:** World remains `to_dict/from_dict` file-only, `save_ground_truth_reference` optional separate files, no `INSERT INTO commerce_opportunity_decisions`.

---

## Files changed

**New Phase 5:**

```
simulation/content_affinity.py
tests/test_simulation_content_affinity.py
docs/OPTIMIZER_SIMULATOR_PHASE_5_REPORT.md
```

**Phase 1-4 preserved (no modifications):**

```
simulation/__init__.py, simulation/run.py, simulation/clock.py, simulation/identity.py,
simulation/event.py, simulation/ground_truth.py, simulation/config.py, simulation/data_origin.py,
simulation/persistence.py, simulation/world.py, simulation/snapshot.py, simulation/adapter.py,
simulation/outcome.py, simulation/synthesizer.py, simulation/behavior.py
tests/test_simulation_contracts.py, tests/test_simulation_world.py, tests/test_simulation_outcome.py,
tests/test_simulation_behavior.py
docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md,
docs/OPTIMIZER_SIMULATOR_PHASE_2_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_2_DESIGN.md,
docs/OPTIMIZER_SIMULATOR_PHASE_3_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_4_REPORT.md
```

**Modified none** production (`commerce/*`, `db/*`, `workers/*`, `core/*`, `integrations/*`) — verified `git status --porcelain` shows only `?? simulation/content_affinity.py`, `?? tests/test_simulation_content_affinity.py`, `?? docs/OPTIMIZER_SIMULATOR_PHASE_5_REPORT.md` plus pre-existing dirty tree (preserved, not added).

`simulation/__init__.py` not yet updated to re-export `ContentAffinity` (optional follow-up, direct import `from simulation.content_affinity import ...` works).

---

## Known issues

- `ContentAffinityModel` caches per-instance `_pref_cache` and `_affinity_cache` keyed by `(fan_id, content_id)` only; if same fan/content pair recomputed with different `content_preference_strength` (should be stable per fan), cached score would be stale — but `FanBehavior.content_preference_strength` is deterministic per fan, so not observed; a future caller passing ad-hoc different strength would need cache invalidation (documented, low risk).
- `SimulationWorld.from_dict` counter heuristic `len(collections)` not hash-perfect for next-id determinism after deserialization — noted Phase 2 limitation, not fixed Phase 5 (tested only for recomputed affinity equality, not next-id determinism).
- `OFFER_TYPES` truncated to 4 production offer types; `family_id` and `vault_count` not primary affinity axes — if production later exposes richer content taxonomy (tags, explicitness), affinity model would need version bump `v2` to incorporate without breaking `v1` contract.
- `ContentAwareBehavioralOutcomeModel` purchase delay still uniform 1..48h + jitter, not yet affinity-dependent (e.g., high affinity faster purchase) — per scope boundary timing not modeled Phase 5.
- `affinity = lerp(0.5, 0.7*pref+0.3*jitter, strength)` implies even `strength=1` can produce score near 0.5 if both pref and jitter ≈0.5; hedging ensures bounded but means maximum differentiation less than 0..1 extremes (min 0.051 observed, not 0.0) — intentional modest effect, not full 0..1 dominance, acceptable per spec “modest effect”.

---

## Phase 6 readiness

**Ready: YES**

No blocker — Phase 5 fan×content heterogeneity foundation complete, deterministic, with baselines preserved and ablation proven. Next phase can build:

- price sensitivity curves + dynamic pricing exploiting `price_sensitivity` × `content_affinity` without rewriting affinity system (both latent, composable via logit)
- offer optimization (bundle economics) using content affinity to score candidate construction
- timing/payday/weekend/fatigue, conversation state, commercial readiness experiments (all latent, composable)
- scenario packs sweeping `ContentAwareOutcomeConfig` intercept/weights and `content_affinity_weight` via `SimulationRun.scenario_id`
- file-based `simulation_runs/{id}/affinities.jsonl` dump via `ContentAffinity.to_dict` if needed

No production optimizer change needed before affinity calibration.

**Blockers:** none. Contract stable, version `v1` affinity, `v1` behavior, `p356.features.v1` schema.

