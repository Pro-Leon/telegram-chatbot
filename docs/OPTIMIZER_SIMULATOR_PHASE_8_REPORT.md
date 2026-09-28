# Phase 8 — Offer Strategy / Scenario Packs Report

**Repository:** `E:\chatbot` **Branch:** `main` **HEAD:** `19e188413dbc18ac29ce2ba246cee3a60f361876`
**Phase:** 8 — Offer Strategy / Scenario Packs (AUDIT → IMPLEMENT → VERIFY)
**Date:** 2026-09-20
**Prereqs:** Phase 7.1 wired snapshot (`simulation/snapshot.py` history_state/conversation_state), `simulation/world.py`, `simulation/synthesizer.py`, `simulation/offer_strategy.py` new, `simulation/scenarios.py` new, `commerce/opportunity_ranking.py` v1 pure lexicographic, 127 PASS pre-8.

---

## Status

**PASS** — 8 new Phase 8 tests + 127 prior simulation tests =135 PASS; 105 optimizer/readiness tests PASS; no production files modified, baseline replay identical, deterministic heterogeneous CreatorDataset verified, file-only.

---

## Strategy registry

**File:** `simulation/scenarios.py:10` `SCENARIO_PACKS: dict[str,OfferStrategy]`

| scenario_id | strategy_id | content_mix (weights → sum 1.0) | price_distribution (PriceDistribution) | vault_count_range | description | purpose |
|-------------|-------------|----------------------------------|----------------------------------------|-------------------|-------------|---------|
| `baseline` | baseline | `{"SMALL_BUNDLE":1.0}` | `price_min 1999 price_max 1999 ref 2000` constant | (2,2) | Baseline replay: single SMALL_BUNDLE 1999 (pre-8 single-content reuse, deterministic) | backward replay control |
| `cheap_single` | cheap_single | `{"SINGLE":0.7,"SMALL_BUNDLE":0.3}` | `500-1500` low-biased | (1,2) | Cheap single | LOW bucket heavy |
| `premium_mix` | premium_mix | `{"PREMIUM":0.4,"CORE_BUNDLE":0.3,"SMALL_BUNDLE":0.2,"SINGLE":0.1}` | `2500-5000` high-biased | (2,4) | Premium mix | HIGH bucket heavy |
| `balanced` | balanced | `{"SINGLE":0.25,"SMALL_BUNDLE":0.25,"CORE_BUNDLE":0.25,"PREMIUM":0.25}` | `500-5000` uniform | (1,3) | Balanced uniform | heterogeneous LOW/MID/HIGH |
| `novelty_heavy` | novelty_heavy | same uniform 0.25 each | `500-5000` uniform | (1,4) | Same as balanced but docs note ranking novelty via diverse vault_ids (input-shaping only, pure ranking v1 unchanged) | vault diversity |

All packs `currency USD`, `version v1` (`simulation/offer_strategy.py:30` `OFFER_STRATEGY_VERSION v1`), `OFFER_TYPES` truncated to `SINGLE/SMALL_BUNDLE/CORE_BUNDLE/PREMIUM` (`simulation/offer_strategy.py:21`).

**Lookup helpers** `simulation/scenarios.py:47` `get_strategy(scenario_id)` fallback `baseline` if unknown/empty, `51` `get_strategy_for_run(run)` via `run.scenario_id`. No `SimulationConfig.offer_strategy_id` added — uses existing `scenario_id` mapping to avoid migration, keeps `config_version v1` backward compat (old manifests without field still load). Documented alternative allowed but not needed.

---

## Sampling formulas (file:line)

**File:** `simulation/offer_strategy.py:19` helpers `_hash_float(seed, sim_id, domain, counter)`, `_hash_int`

```python
# offer_type weighted choice
r = _hash_float(seed, sim_id, f"offer_strategy:{strategy_id}:offer_type:{counter}",1)  # offer_strategy.py:68
total = sum(weights)
cumul += w/total
if r < cumul: return otype  # offer_strategy.py:71

# price uniform
PriceDistribution.sample: span=price_max-price_min+1, r=_hash_float(...:price:{counter},1), offset=int(r*span), price=price_min+offset  # offer_strategy.py:38-43
# vault count
vc_r = _hash_float(...:vault_cnt:{counter},2), cnt=int(vc_r*span)+lo clamped 1..10; SINGLE max2, CORE/PREMIUM min2  # offer_strategy.py:84-93
# vault_ids, mapped_drop_ids
vault_ids = tuple(f"V{counter}_{j}" for j in range(cnt))  # offer_strategy.py:94
mapped = tuple(f"drop_{counter}_{j}" for j in range(1))  # single drop  # offer_strategy.py:95
```

No global `random`, no `datetime.now()`, deterministic per `(seed, simulation_id, counter, strategy_id)`.

**Price heterogeneity:** `cheap_single` mean ~1000, `premium_mix` mean ~3750 → difference >500 verified test `test_c_price_heterogeneity`. `balanced` LOW/MID/HIGH each >5% of 100 samples, min<1000 max>=3000.

---

## Determinism proof

- **Same seed/scenario_id same sequence:** `test_b_content_mix_determinism` same `seed123 sim-b counter 0..19` twice → `seq1==seq2` PASS. `test_f_replay` same `seed11 premium_mix` twice → `offer_type, price_minor, vault_ids` per counter identical, `evaluated_at`, `generation_id synthetic:{run}:{id}`, `ledger rows`, `evidence label` identical for 12 bundles.

- **Advance equivalence:** still holds via `evaluated_at` clock (`world.clock.advance(hours=24)` per bundle) — strategy sampling per `counter=i` not per clock, so `advance(1h)+advance(2h)` vs `advance(3h)` still same final `evaluated_at` if strategy uses `counter` only; time effect remains via `evaluated_at` UTC as before.

- **Baseline replay identical:** `test_a_registry_baseline_replay` and `test_h_backward_compat` → `offer_strategy=None` legacy `generate_mature_bundles` (single content reuse) gives `types=={"SMALL_BUNDLE"}` and `prices=={1999}` identical to `baseline` strategy 12 opps all `SMALL_BUNDLE 1999` (since baseline price_min==price_max). `generate_mature_bundles` now checks `resolved_strategy is None` → fallback single `base_content` reuse, so old tests without strategy unchanged (127 PASS preserved).

---

## Heterogeneity proof (distribution counts per scenario)

**`test_c_price_heterogeneity` balanced n=100:**
- Buckets `LOW<1000` count >5, `MID<3000` >5, `HIGH>=3000` >5
- `min<1000` true, `max>=3000` true
- `premium_mix` mean  ~3750 vs `cheap_single` mean ~1000 diff >500

**`test_e_synthesizer_heterogeneous_e2e` balanced n=12:**
- `len(distinct offer_type) >=2` (balanced uniform gives at least 2 types in 12)
- `len(distinct price_bucket) >=2` (LOW/MID/HIGH not constant)
- Train `build_creator_dataset → train_creator_model` not abstained (needs both labels; with 12 and p=0.5 + heterogeneous prices, floors met)

**Vault:** `test_d_vault_distribution` 20 samples each 1..4 vault count, deterministic same price/type repeat.

---

## Isolation proof (grep 0, 16 FEATURES)

- `grep telethon` in `simulation/*.py` 0 hits (new `offer_strategy.py` imports only `hashlib, dataclasses, typing`)
- `grep "insert into commerce_offers"` 0 hits
- `FEATURE_NAMES` `commerce/offline_optimizer.py:212` still 16 unchanged `test_g_isolation` `len==16` PASS, `fatigue_score`/`engagement_score` not in FEATURES.
- Synthetic creator `900000+` isolation unchanged (`world.py:322`).

---

## E2E optimizer heterogeneous (mature FULL, train/predict, feature mix)

**`test_e_synthesizer_heterogeneous_e2e`:** `World(seed11, scenario balanced) → generate_mature_bundles(n=12, offer_strategy=balanced) → 12 opps heterogeneous offer_type/price_bucket` → `classify_opportunity_evidence FULL MATURE` → `build_optimization_input` → `RowBundle` heterogeneous `offer_type/price_bucket` features not constant → `build_creator_dataset` → `train_creator_model` not abstained → `predict_for_input` works, snapshot history wiring still holds (if fatigue model passed, counts match).

**`test_f_replay`:** same seed11 premium_mix twice identical (see above).

**File-only:** `simulation/offer_strategy.py` no DB, `scenarios.py` registry in-memory, `synthesizer.py` samples per counter via hash, no `db/postgres` writes.

---

## Tests (counts PASS/FAIL, new tests list, runtime)

**New:** `tests/test_simulation_offer_strategy.py` 8 tests **PASS** (3.69s):
- `a_registry_baseline_replay` PASS
- `b_content_mix_determinism` PASS
- `c_price_heterogeneity` PASS
- `d_vault_distribution` PASS
- `e_synthesizer_heterogeneous_e2e` PASS
- `f_replay` PASS
- `g_isolation` PASS
- `h_backward_compat` PASS

**Total simulation:** 135 PASS (30 contracts +11 world +13 outcome +16 behavior +17 content +19 price +13 conversation +8 wiring +8 offer_strategy) in 7.31s

**Optimizer/readiness:** 105 PASS `test_p35_6_offline` + `test_p36_optimizer_readiness` (no DB writes claimed PASS as before; Postgres/Redis UNVERIFIED but not required for file-only tests)

---

## Known issues & Phase 9 readiness

- **USD only:** `currency USD` hard-coded, no FX; non-USD not used.
- **Pure ranking v1 unchanged:** strategy is **input-shaping only** (which OfferDefinitions eligible via content/price), not ranking weight change. `rank_candidates` lexicographic `ranking.py:338` `novel_set→recent→type_order→stable_key` remains authoritative; optimizer heterogeneity comes from `selected` distribution shift, not ranking duplication.
- **No active offer simulation:** `has_active_offer False` constant in snapshot.
- **No DB offer definitions:** synthetic `OfferStrategy` does not write `commerce_offer_definitions`; ranking v1 input-shaping is via `world.create_content` price/type, not DB.
- **Phase 9 ready:** YES — heterogeneous CreatorDataset now possible per `scenario_id` (cheap vs premium vs balanced), deterministic replay holds, baseline control intact. Next phase can measure optimizer lift `balanced vs baseline` or `cheap_single vs premium_mix`.

**Open:** Should `SimulationConfig` gain optional `offer_strategy_id` with default None (keep v1) or continue scenario_id mapping? Either allowed if backward compat preserved (current chooses scenario_id mapping).

---

## Files changed/created (exact paths, line spans)

```
simulation/offer_strategy.py (new, 124 lines): OfferStrategy, PriceDistribution, _hash_float, sample_content_params:68-95
simulation/scenarios.py (new, 58 lines): SCENARIO_PACKS 5 packs:10-45, get_strategy:47, get_strategy_for_run:51
simulation/synthesizer.py:26 modified: generate_mature_bundles signature +33-36 param docs, +50-60 strategy resolve, +66-85 per-opp sampling, 93-120 outcome dispatch fallback
simulation/snapshot.py:20 7.1 wiring already (history_state, conversation_state) — unchanged in Phase8
simulation/world.py:387 7.1 wiring — unchanged
tests/test_simulation_offer_strategy.py (new, 8 tests, ~150 lines)
docs/OPTIMIZER_SIMULATOR_PHASE_8_REPORT.md (this report)
```

**Modified none production:** `commerce/*`, `db/*`, `workers/*`, `commerce/offline_optimizer.py` FEATURES 16 unchanged, `RANKING_POLICY_VERSION v1` unchanged, `simulation_runs/{id}/` file-only.

