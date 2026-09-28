# Phase 7 — Conversation State, Timing & Fatigue Report

**Repository:** `E:\chatbot` **Branch:** `main` **HEAD:** `19e188413dbc18ac29ce2ba246cee3a60f361876`
**Phase:** 7 — Conversation State, Timing & Fatigue (AUDIT → IMPLEMENT → VERIFY)
**Date:** 2026-09-20
**Prereqs:** `docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_3_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_4_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_5_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_6_REPORT.md`, `simulation/*` Phase 1-6

---

## Status

**PASS** — All 13 Phase 7 tests + 106 prior simulation tests (30 contracts +11 world +13 outcome +16 behavior +17 content affinity +19 price response) =119 simulation tests pass; 150 optimizer/readiness/maturity tests pass. No production files modified, all prior models callable, deterministic replay verified.

---

## Audit

**Exact production conversation/history/time fields discovered (current repo, not assumed):**

| Area | File:Symbol | Field | Type | Source | Observable? | Optimizer feature? | Simulator populates? |
|------|-------------|-------|------|--------|-------------|--------------------|----------------------|
| Conversation | `commerce/opportunity_optimization.py:233 ConversationContext` | `lifecycle` | `str\|None` | `decision_snapshot.conversation.lifecycle` | Yes | Via `conversation_context.lifecycle → lifecycle` bucket `MISSING/lifecycle` | Yes via `SyntheticConversationContext` |
| | same | `current_topic` | `str\|None` | `decision_snapshot.conversation.current_topic` | Yes | `topic_presence HAS_TOPIC/NO_TOPIC` | Yes |
| | same | `recent_topics` | `tuple[str]` | `decision_snapshot.conversation.recent_topics` | Yes | Not directly feature, but part of context | Yes (empty default) |
| | same | `open_threads` | `tuple[str]` | `decision_snapshot.conversation.open_threads` | Yes | Not feature | Yes |
| Fan commercial summary | `commerce/opportunity_optimization.py:172 FanCommercialSummary` | `purchase_count`, `total_spend_minor`, `average_order`, `highest`, `last_purchase_at`, `recent_purchase_count`, `recent_spend`, `owned_vault_ids`, `delivered`, `recent_offer_count`, `recent_rejected`, `last_offer_at`, `recent_offered_vault`, `currency` | ints/str/tuples | `decision_snapshot.fan` frozen at `evaluated_at` | Yes | Buckets `fan_purchase_bucket P0/P1_2/P3P`, `fan_spend S0/S_LOW/S_HIGH`, `fan_recent R0/R1/R2P`, `fan_rejected J0/J1P`, `owned O0/O1_2/O3P` | Zero defaults in `simulation/snapshot.py:86` (Phase 2) — not yet populated from history in Phase 7 latent path (kept deterministic latent for probability) |
| History summary | `commerce/opportunity_optimization.py:204 OfferHistorySummary` | `total_offer_count`, `recent_offer_count`, `last_offer_at`, `declined_offer_count`, `recent_declined`, `state_counts`, `has_active_offer`, `active_offer_count`, `offered_vault_sets`, `active_vault_sets` | ints/bool/tuples | `decision_snapshot.history` frozen | Yes | Buckets `history_total H0_2/H3_5/H6P`, `history_declined D0/D1P`, `has_active ACTIVE/NO_ACTIVE` | Zero defaults initially; per-fan deterministic history generated via `FatigueModel._history_for_fan` for fatigue/topology |
| Evaluated_at | `commerce/opportunity_optimization.py:313 OptimizationInput.evaluated_at` | `datetime tz-aware UTC` | ledger `evaluated_at` + snapshot | Yes | Ordering only, not feature | Yes via `SimulationClock` |
| Prior opportunities | `commerce/opportunity_optimization.py:291 ReengagementContext` / `commerce/offline_optimizer.py` `reengagement_of` | `reengagement_of`, `is_child`, `sibling_touch_count`, `purchase_winner_opportunity_id`, `revenue_events` | int/bool | ledger `reengagement_of` | Yes (child excluded from primary) | Counts only | No synthetic re-engagement yet |
| Exposure/selected | `commerce/opportunity_optimization.py:111 FrozenCandidate` | `definition_id, version, stable_key, offer_type, price_minor, currency, vault_count, drop_mapping` | snapshot eligible/selected | Yes | Features `offer_type, price_bucket, vault_count_bucket, drop_mapping` | Yes |
| Ranking | `simulation/snapshot.py:74 ranking` | `policy_version v1`, `ranked_order`, `factors` | snapshot | Yes | `policy_version` segmentation only | Yes `v1` fixed |
| Cooldown/fatigue | `commerce/opportunity_optimization.py:416 VALIDATOR_CHECKS` governance | `pressure/fatigue/cooldown` mentioned as future validator check, no current production field | N/A | No optimizer feature yet | No | Latent only |
| Time/context | `simulation/world.py:63 SyntheticFan.timezone` | `timezone UTC` (always) | fan | Yes but constant | No feature yet | Yes UTC only, no local conversion |
| Established truth | `docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md` | No separate `conversation activity` timestamps in ledger; `evaluated_at` is decision time, `exposure_at`/`outcome_at` are evidence times | — | — | — | — |

**Finding:** Production exposes 4 conversation fields, 14 fan summary fields (including recent offer/purchase counts), 12 history fields, plus evaluated_at and price. No latent `engagement_score, recency, fatigue, time_effect` in production. Simulator must populate observable fields with deterministic defaults (zero/empty) for now, while latent scores drive probability via hidden ground truth. Timezone is always UTC (`SyntheticFan.timezone="UTC"`), so local hour not invented; hour is UTC hour.

**Decision:** Phase 7 populates latent continuous scores via hash, keeps observable snapshot at zero defaults (still valid per builders) to avoid inventing a parallel optimizer input. Latent scores affect hidden purchase probability only; observable snapshot remains production-shaped, verifiable via `json.lower()` / `dataclasses.fields()` checks.

---

## State Model

**File:** `simulation/conversation_state.py`

### Conversation state (continuous, latent)

```python
@dataclass ConversationState:
  fan_id, evaluated_at UTC
  engagement_score ∈[0,1] via _hash_float(seed, sim_id, f"conv:fan:{id}:engagement:{v1}",1)
  recency_score = exp(-hours_ago/24) where hours_ago = hash*72 ∈[0,72] → recency 1.0→0.05
  activity_score = recent_cnt/5 where recent_cnt = hash*6 ∈0..5
  conversation_age_hours = hash*240 ∈[0,240]
  derived_label HOT if avg>=0.65 else WARM if >=0.35 else COLD (reporting only)
```

Deterministic per `(seed, simulation_id, fan_id, evaluated_at iso, version v1)`, cached per `(fan_id, evaluated_at)`.

### History model (chronological, deterministic)

Per-fan history generated at `FatigueModel._history_for_fan` via hash:

```
n_offers = int(hash(fan:{id}:n_offers)*6) 0..5
for i in 0..n_offers-1:
  offset_h = hash(fan:{id}:offer:{i}:offset)*240
  at = simulated_start + offset_h  (absolute UTC)
  outcome = PURCHASED if hash<0.3 else DECLINED
sorted ascending → events tuple
```

`HistoryState` at `evaluated_at` filters `at <= evaluated_at`, computes `total_offer_count, recent_offer_count (≤72h), last_offer_at, last_purchase_at, recent_declined`. Chronological, creator/fan isolated via fan_id high range, replayable.

### Fatigue model (latent, bounded, decay)

```
FatigueModel(seed, simulation_id, simulated_start, decay_rate=0.05/h, version v1)
fatigue_for(fan, evaluated_at):
  hist = history_state_for(fan, evaluated_at)
  fatigue = Σ 0.3*exp(-decay* hours_ago) for each past offer at hours_ago = evaluated_at - at
          + recent_declined*0.1*exp(-decay*0.5)
  fatigue = clamp01(fatigue) ∈[0,1]
  hours_since_last_offer = evaluated_at - last_offer_at if exists else None
```

Monotonic: more recent offers → higher fatigue; time passes → exp decay → fatigue decreases. Bounded, deterministic, versioned. Tested F1 baseline ~0, F2 high offers mean +0.03 higher than low, F3 decay 72h decreases, F6 bounded, F7 not zero-forcing.

### Time model (observable UTC, no local invention)

```
TimeContextModel(version v1)
context_for(evaluated_at UTC):
  hour_of_day = evaluated_at.hour 0..23 UTC
  day_of_week = weekday 0=Mon..6=Sun
  is_weekend = dow>=5
  timezone = "UTC" (fan.timezone always UTC, no conversion)
  time_effect = (is_weekend?0.2:0) + (18<=hour<=22?0.15:0) clamped [0,1]
```

Deterministic, no FX/payday calendar invented (documented as future scenario variable). Audit: no location/pay-cycle fields exist, so payday not implemented.

---

## Observable State

Reaches `OptimizationInput` via `build_optimization_input`:

- `conversation_context` 4 fields (lifecycle, current_topic, recent_topics, open_threads) — observable, currently empty defaults except lifecycle established
- `fan_commercial_summary` 14 fields — observable, currently zero/None defaults (counts not yet wired to fatigue history for Phase 7 latent path; kept valid per builders)
- `history_state` counts — observable via `history` snapshot if wired; currently zero defaults but history generated for latent fatigue
- `evaluated_at`, `price_minor`, `currency`, `offer_type` etc. — observable
- `hour_of_day/day_of_week/is_weekend` — observable UTC derived, not yet a feature in FEATURE_NAMES (future)

**Not observable:** engagement_score, recency_score, activity_score, fatigue_score, time_effect, latent receptiveness, latent purchase probability (verified via `json.lower()` and `dataclasses.fields`).

---

## Latent State

Remains in hidden ground truth only (`Phase7OutcomeModel.decide` hidden dict):

- `conversation_state` dict (engagement, recency, activity, derived_label)
- `fatigue_state` dict (fatigue_score, recent counts, hours_since, decay_rate)
- `time_context` dict (hour, dow, is_weekend, time_effect)
- `history_state` events
- `latent_purchase_probability`, `content_affinity`, `price_response`

Stored via `simulation/persistence.save_ground_truth_reference` separate files, never in ledger snapshot.

---

## Fatigue Model

**Formula:** `fatigue(t) = Σ 0.3*exp(-0.05*Δt_i) + 0.1*recent_declined*exp(-0.025)` where Δt_i = hours since past offer i, first term per-offer 0.3 decayed, second declined extra, clamped [0,1]. Assumes each offer contributes linearly decaying fatigue, declined slightly more.

**Parameters:** `decay_rate 0.05/h` (half-life ~13.9h), `offer_weight 0.3`, `declined_extra 0.1`, `recent window 72h`, `history span 240h`. Configurable via `FatigueModel(decay_rate)`, documented.

**Version:** `FATIGUE_MODEL_VERSION v1`.

**Assumptions:** No production fatigue field exists; fatigue is simulator realism hypothesis, not business rule. Purchases not increasing fatigue (only offers/declines). Exponential decay chosen for simplicity, not from repo.

---

## Time Model

**Timezone handling:** `SyntheticFan.timezone="UTC"` always; `evaluated_at` is UTC from `SimulationClock`; `hour_of_day` is UTC hour, not local. Documented `timezone = unavailable` per spec if local needed, but repo has only UTC.

**Day/hour representation:** `hour_of_day 0..23 UTC`, `day_of_week 0..6`, `is_weekend bool`. No payday calendar (audit found no location/pay-cycle fields). Time effect `weekend 0.2 + evening 18-22 UTC 0.15` bounded [0,1], modest, not dominating.

**Version:** `TIME_MODEL_VERSION v1`.

---

## Outcome Integration

**Preserved:** Baseline, Behavioral, ContentAware, PriceAware all callable (P16, ablation).

**Phase 7 extension (composition, not rewrite):**

```
logit Phase6 D = intercept + purchase*prop + eng + rel - freebie + content_affinity*0.8 + price_term
price_term = -price_sens_weight*sens - price_response_weight*sens*(normalized-1) (normalized=price/2000)

logit Phase7 G = logit D
                + conversation_weight*engagement_score (0.5)
                + recency_weight*recency_score (0.4)
                - fatigue_weight*fatigue_score (0.7)
                + time_weight*time_effect (0.3)
```

**Config:** `Phase7OutcomeConfig(intercept -1.0, purchase 1.5, eng 0.8, rel 0.6, price_sens 0.8, freebie 0.9, content 0.8, price_response 0.6, conversation 0.5, recency 0.4, fatigue 0.7, time 0.3, reference 2000)` all explicit finite-checked. Fatigue subtracts, conversation adds, time adds modestly.

**State does not determine outcome:** stochastic `purchased = r < p` where `r=_hash_float(phase7_outcome:opp_id)`, so high fatigue reduces p but not to zero (tested P F7 p>0.01 at fatigue>0.7).

**Same fan/content/price different state:** conversation/fatigue/time differ → p differs (tested via probability_for with different evaluated_at or history).

---

## Ablation

**A Baseline (global p0.5), B Behavioral (fan traits), C Behavioral+Content (affinity), D Behavioral+Content+Price (price deviation), E = D + Conversation, F = E + Fatigue, G = F + Time**

| transition | zero-weight reproduces prior | positive-weight changes |
|------------|------------------------------|-------------------------|
| E - D (conversation) | `conversation_weight=0, recency=0` → `abs(p_D - p_E0)<1e-9` PASS | `conversation_weight 0.5` → mean diff across fans >1e-4 (tested via population) |
| F - E (fatigue) | `fatigue_weight=0` → `abs(p_E - p_F0)<1e-9` PASS | `fatigue_weight 0.7` with high fatigue fan → p lower than low fatigue |
| G - F (time) | `time_weight=0` → `abs(p_F - p_G0)<1e-9` PASS | `time_weight 0.3` weekend evening vs weekday → diff 0.06*0.3 |

All models remain callable via shared `SimulatedOutcome` type.

---

## Replay

**Exact reproducibility (P test_end_to_end_and_replay):**

Run seed 11 scenario e2e twice:

```
run = SimulationRun(seed11, sim_id e2e, start 2026-01-01)
world→creator→for i 1..12: fan, content price 500+(i*600)%5000, opp, decide via Phase7, ledger SENT, classify FULL MATURE, Input, RowBundle, clock+24h
→ build_creator_dataset → train → predict
```

Second run same seed/sim_id/config reproduces:

- fan_ids identical
- content_ids identical
- opportunities evaluated_at identical
- conversation states identical
- fatigue scores identical
- price responses identical
- outcomes purchased identical
- ledger rows identical
- training examples identical
- predictions identical

Verified `b1.input.evaluated_at == b2.input.evaluated_at` and `b1.evidence.label == b2.evidence.label` for all 12 bundles. Deterministic via SHA256 hashes, no global RNG.

**Time advance equivalence (T3):** `advance(1h)+advance(2h)` vs `advance(3h)` produce same `TimeContext` for same final `evaluated_at` (since time derived solely from `evaluated_at UTC`, not clock steps). Verified via `time_model.context_for(ev+1h+2h) == time_model.context_for(ev+3h)`.

---

## Population Tests

**1,000 fans, fixed evaluated_at 2026-01-05 15:00 UTC (Monday), price 2000, content SINGLE:**

| metric | min | max | mean | stdev |
|--------|-----|-----|------|-------|
| engagement_score | 0.0006 | 0.999 | 0.508 | 0.290 |
| recency_score | 0.050 | 0.999 | 0.309 | 0.252 |
| fatigue_score | 0.0 | 0.896 | 0.107 | 0.147 |
| time_effect (UTC 15 Mon) | 0.0 | 0.0 | 0.0 | 0.0 (weekday non-evening) |
| purchase prob p (Phase7) | 0.120 | 0.923 | 0.575 | 0.185 |

Heterogeneous, not collapsed, not always 0/1, time not dominating (0 at this slot, weekend evening would be 0.35).

**Bands:**

- Engagement ≥0.8 mean p 0.597 vs <0.2 0.581 → higher engagement slightly higher p, not hard rule, counterexamples exist (high engagement can still decline due to stochastic).
- Fatigue <0.2 mean p 0.586 vs >0.6 mean p 0.435 → higher fatigue lower rate, not zero.
- Recency: similar spread, no pathological dominance.

---

## End-to-End Optimizer

**Path exercised (P test_end_to_end_and_replay):**

```
SimulationRun(seed11, e2e) → SimulationWorld → FanBehaviorModel → ContentAffinityModel → PriceResponseModel(2000) → ConversationStateModel(v1) → FatigueModel(0.05, start 2026-01-01) → TimeContextModel → Phase7OutcomeModel(weight conv0.5 recency0.4 fatigue0.7 time0.3)
  → for i=1..12: fan, content price 500+(i*600)%5000, opp at clock, decide → SimulatedOutcome, ledger SENT, classify FULL MATURE, build_optimization_input (no latent), RowBundle, clock+24h
  → build_creator_dataset → train_creator_model → OfflineModel not abstained → predict_for_input clean
```

Also verified **multiple fans/prices/content types/conversation states/history states/times** in population test.

**Result: PASS** — training succeeds, prediction succeeds, creator isolation holds, no latent leakage, deterministic replay holds, prior Phase 6 behavior available via weight 0 configs.

---

## Production Isolation

**Concrete evidence (`grep simulation/*.py` lower):**

- No Telegram/Telethon: 0 hits (new `conversation_state.py` imports only `hashlib, math, dataclasses, datetime, simulation.world`)
- No Dropfans/Fangate: 0 `integrations/dropfans`, 0 `fangate_transactions`
- No Redis: 0 `db/redis`, 0 `XADD`, 0 `enqueue_send`
- No commerce_offers: 0 `INSERT INTO commerce_offers`
- No user-profile mutation: `FanBehaviorModel`/`FatigueModel` caches in-memory dict, not `users`
- No pricing authority: `price_response_weight` logit only, never `drop_content_key/create_drop`
- No migrations: `db/migrations/*` untouched
- No optimizer production changes: `commerce/offline_optimizer.py` not modified (`git diff --stat` only `simulation/conversation_state.py`, `tests/test_simulation_conversation_state.py`, this report)
- File/in-memory only, no `INSERT INTO commerce_opportunity_decisions`, no `scheduled_messages`.

---

## Tests

**New Phase 7:** `tests/test_simulation_conversation_state.py` (13 tests):

| Test | Verifies | Result |
|------|----------|--------|
| test_conversation_state_continuous | engagement/recency/activity ∈[0,1] | PASS |
| test_recency_monotonic | same fan same time same recency, many fans varied | PASS |
| test_interaction_history_chronological | chronological, deterministic, creator/fan isolation | PASS |
| test_fatigue_baseline | no recent offers fatigue <0.2 | PASS |
| test_fatigue_increases_with_offers | high total offers mean +0.03 higher than low | PASS |
| test_fatigue_decay | 72h later fatigue decreases | PASS |
| test_fatigue_bounded_not_determines_outcome | bounded [0,1], p not forced 0 at high fatigue | PASS |
| test_time_determinism_and_advance | T1 same time same state, T2 advance changes, T3 equivalent elapsed, T4 no leak | PASS |
| test_ablation_zero_weights | E-D zero reproduces D, F-E zero reproduces E, G-F zero reproduces F | PASS |
| test_ground_truth_isolation | hidden has fatigue/conversation/time/latent, reference/ledger/snapshot/Input clean | PASS |
| test_production_isolation | no telethon/commerce_offers | PASS |
| test_end_to_end_and_replay | 12 bundles train predict, same seed replay identical | PASS |
| test_population_heterogeneity | 1000 fans eng/rec/fatigue/prob heterogeneous not collapsed | PASS |

**Total simulation:** 119 passed (30 contracts +11 world +13 outcome +16 behavior +17 content affinity +19 price response +13 conversation_state) in 8.88s
**Optimizer/readiness/maturity:** 150 passed (`test_p35_6_offline`, `test_p36_optimizer_readiness`, `test_p35_3b_evidence_maturity`)
**Full unit filtered:** 119+150=269 considered, integration requiring Postgres/Redis UNVERIFIED (expected).

---

## Files Changed

**New Phase 7:**

```
simulation/conversation_state.py
tests/test_simulation_conversation_state.py
docs/OPTIMIZER_SIMULATOR_PHASE_7_REPORT.md
```

**Phase 1-6 preserved (no production modifications):**

```
simulation/__init__.py, simulation/run.py, simulation/clock.py, simulation/identity.py,
simulation/event.py, simulation/ground_truth.py, simulation/config.py, simulation/data_origin.py,
simulation/persistence.py, simulation/world.py, simulation/snapshot.py, simulation/adapter.py,
simulation/outcome.py, simulation/synthesizer.py, simulation/behavior.py, simulation/content_affinity.py,
simulation/price_response.py
tests/test_simulation_contracts.py, tests/test_simulation_world.py, tests/test_simulation_outcome.py,
tests/test_simulation_behavior.py, tests/test_simulation_content_affinity.py, tests/test_simulation_price_response.py
docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md,
docs/OPTIMIZER_SIMULATOR_PHASE_2_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_2_DESIGN.md,
docs/OPTIMIZER_SIMULATOR_PHASE_3_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_4_REPORT.md,
docs/OPTIMIZER_SIMULATOR_PHASE_5_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_6_REPORT.md,
```

**Modified none** production (`commerce/*`, `db/*`, `workers/*`, `core/*`, `integrations/*`) — `git status --porcelain` shows only `?? simulation/conversation_state.py`, `?? tests/test_simulation_conversation_state.py`, `?? docs/OPTIMIZER_SIMULATOR_PHASE_7_REPORT.md` plus pre-existing dirty tree.

---

## Known Issues

- Observable snapshot fan/history counts remain zero defaults; latent fatigue uses hash-generated history not yet wired to snapshot observable counts. Intentional to keep Phase 7 latent-only; wiring to snapshot would require extending `build_decision_snapshot` to accept HistoryState and is future work without breaking optimizer contract.
- FatigueModel history window 240h from `simulated_start`; evaluated_at before history events would give zero fatigue — correct for early-opportunity baseline.
- TimeContext timezone always UTC; repo has `SyntheticFan.timezone="UTC"` only, so no local hour/payday effects. Payday calendar not implemented (audit found no pay-cycle fields) documented as future scenario variable.
- ConversationState recency uses hash*72h not actual last interaction timestamp from history; simplified deterministic model not yet linked to HistoryState recency. Future could unify.
- Decay rate 0.05/h is simulator assumption, not production-derived; configurable but not calibrated.
- No content×price interaction; conversation×price also simple additive, not multiplicative.

---

## Phase 8 Readiness

**Ready: YES** — Phase 7 provides deterministic, replayable Fan×Content×Price×Conversation×Fatigue×Time environment, heterogeneous, stochastic, isolated, with ablation weights to test strategies.

Next phase can build scenario packs, bundle economics, timing optimization, or fatigue-aware offer throttling without rewriting state models.

**Blockers:** none. Versions stable: `conversation v1`, `fatigue v1 (decay 0.05)`, `time v1`, `price v1 (2000)`, `content v1`, `behavior v1`, `p356.features.v1`.

