# Phase 7.1 — Snapshot Wiring (Conversation + History → Observable) Report

**Repository:** `E:\chatbot` **Branch:** `main` **HEAD:** `19e188413dbc18ac29ce2ba246cee3a60f361876`
**Phase:** 7.1 — Snapshot Wiring (latent → observable, zero-defaults → wired counts)
**Date:** 2026-09-20
**Prereqs:** Phase 7 `simulation/conversation_state.py` (119 simulation PASS)

---

## Files changed (exact paths)

```
simulation/snapshot.py:20 build_decision_snapshot signature extended with optional history_state, conversation_state kwargs (default None) and wiring logic 70-170
simulation/world.py:387 create_opportunity signature extended with history_state, conversation_state forwarded to snapshot 419
simulation/synthesizer.py:26 generate_mature_bundles extended with conversation_model, fatigue_model optional wiring per opportunity
tests/test_simulation_snapshot_wiring.py (new 8 tests)
docs/OPTIMIZER_SIMULATOR_PHASE_7_1_REPORT.md (this report)
```

**No production modifications:** `commerce/*`, `db/*`, `workers/*`, `core/*`, `integrations/*`, `db/schema.sql`, `db/migrations/*` untouched. `commerce/offline_optimizer.py:212 FEATURE_NAMES` still 16, maturity 168h unchanged, file-only `simulation_runs/{run}/`.

---

## Snapshot wiring mapping implemented

**Code snippets file:line**

- `simulation/snapshot.py:20` `def build_decision_snapshot(*, creator, fan, content, conversation=None, evaluated_at=None, definition_id=None, version=None, stable_key=None, policy_version="v1", history_state=None, conversation_state=None)`
- `simulation/snapshot.py:70-88` conversation lifecycle wiring: if `conversation_state` not None → `derived_label` lowercased → `SyntheticConversationContext(lifecycle=derived.lower())` else default `"established"`. Valid per `commerce/opportunity_optimization.py:629` `build_conversation_context` any string/None → bucket.
- `simulation/snapshot.py:90-150` history wiring: when `history_state` not None, derive
  - `fan_recent_offer_count = history_state.recent_offer_count` (`simulation/snapshot.py:115`)
  - `fan_recent_rejected = history_state.recent_declined_count`
  - `fan_last_offer_at = lo.isoformat() if lo else None` where `lo = history_state.last_offer_at`
  - `fan_last_purchase_at` same
  - `fan_purchase_count = count PURCHASED in history_state.events` (or `total - declined`)
  - `fan_recent_purchase_count = max(0, recent_offer - recent_declined)`
  - `history total_offer_count = history_state.total_offer_count`
  - `history recent_offer_count = history_state.recent_offer_count`
  - `history declined_offer_count = total_declined from events`
  - `history recent_declined = history_state.recent_declined_count`
  - `history state_counts = {("PURCHASED", n_p), ("DECLINED", n_d)} filtered>0`
  - `has_active_offer False`, etc. All ints validated `int(...)` and scope `creator_id/user_id` copied verbatim.

- `simulation/world.py:387` `def create_opportunity(..., history_state=None, conversation_state=None)` `simulation/world.py:419` `snapshot = build_decision_snapshot(..., history_state=history_state, conversation_state=conversation_state)` — dependency injection, no import of `FatigueModel` inside world, preserves isolation/determinism.

- `simulation/synthesizer.py:26` `def generate_mature_bundles(..., conversation_model=None, fatigue_model=None)` `simulation/synthesizer.py:70-90` per `i` computes `evaluated_at = clock.current_time()`, `history_state = fatigue_model.history_state_for(fan, evaluated_at) if fatigue_model else None`, `conversation_state = conversation_model.state_for(fan, evaluated_at) if conversation_model else None`, then `world.create_opportunity(..., history_state, conversation_state)`. Outcome decide supports all signatures via `for attempt in ("fan_content","fan","baseline")` try.

**When None:** keeps zero defaults `recent_offer_count 0, last_offer_at None, lifecycle established` — existing 13 Phase7 tests unchanged. When provided: observable counts become deterministic non-zero (e.g., fan with 3 recent offers → `fan.recent_offer_count=3` → feature bucket `R2P`), lifecycle `hot/warm/cold` lowercased → feature `lifecycle hot` bucket.

**Validation kept:** all `_int`/`_iso` paths still valid, scope `creator_id == fan.creator_id` copied, no `ValueError scope mismatch`.

---

## Determinism proof (T1 same time / T2 advance / T3 replay)

- **T1 same seed/state/time → identical:** `test_d_determinism_same_counts_lifecycle` same `fan, evaluated_at 2026-01-05 12:00` twice → same `recent_offer_count`, `last_offer_at`, `total_offer_count`, `lifecycle`. `ConversationStateModel` cache key `(fan_id, evaluated_at.isoformat())`, `FatigueModel` history derived from `hash(fan:{id}:n_offers)` and `at = simulated_start + hash*240` filtered by `evaluated_at` — deterministic.

- **T2 advancing clock changes time-derived state deterministically:** `TimeContextModel.context_for` derived from `evaluated_at UTC hour/day`, advancing `SimulationClock` 24h changes `evaluated_at` → new `TimeContext` but deterministic per `evaluated_at`. Tested via `test_time_determinism_and_advance` and synthesizer `world.clock.advance(hours=24)` loop.

- **T3 replay:** `test_g_e2e_optimizer_and_replay` runs same `seed 11` scenario twice with history wiring: 12 bundles `price 500+(i*600)%5000`, each `fan, content, evaluated_at`, `history_state`, `conversation_state` → `opportunity.evaluated_at` identical, `history.total_offer_count` identical, `conversation lifecycle` identical, `ledger rows` identical, `evidence label` identical, `RowBundle` identical, predictions identical. Uses `world.clock.current_time()` progression and `FatigueModel` with fixed `simulated_start`, so replay holds. Same as Phase7 T3 `advance(1h)+advance(2h) == advance(3h)` holds because state derived solely from `evaluated_at`, not clock steps.

---

## Isolation proof (grep lower 0 hits, dataclass check)

- **Snapshot/ledger clean:** `json.dumps(opp.decision_snapshot).lower()` contains no `fatigue_score, engagement_score, recency_score, time_effect, hours_since, decay_rate, latent_purchase` — only counts/ISO/lifecycle. Tested `test_f_ground_truth_isolation` grep lower 0 hits.

- **Ledger clean:** `json.dumps(ledger, default=str).lower()` same 0 hits.

- **OptimizationInput dataclass:** `dataclasses.fields(OptimizationInput)` still 16 fields via `commerce/offline_optimizer.py:212` unchanged, no new `fatigue`/`conversation` latent fields. Tested `test_f_ground_truth_isolation` `fields` check.

- **Hidden payload:** `Phase7OutcomeModel.decide` returns `hidden["fatigue_state"], hidden["conversation_state"], hidden["time_context"]` with scores/hours, not in snapshot. `GroundTruthReference.to_dict()` identifier only.

---

## Ablation verification (weight0 1e-9)

- `test_h_ablation_weight0_reproduces_phase6`: `PriceAwareBehavioralOutcomeModel` `p_price` (price term only) vs `Phase7OutcomeModel` with `Phase7OutcomeConfig(conversation_weight=0, recency=0, fatigue=0, time=0, price_response_weight=0.6)` → `abs(p_price - p7_zero) <1e-9` for same `fan, content 2000, ev 2026-01-05`. Latent `p` identical even though snapshot now has non-zero counts (counts orthogonal to latent weights). Documented ablation orthogonality: snapshot wiring independent of logit weights.

---

## E2E optimizer result (mature full, train/predict)

- `test_g_e2e_optimizer_and_replay` with wiring: `SimulationWorld(seed11) → FatigueModel/ConversationStateModel → history_state/conversation_state per fan/evaluated_at → create_opportunity wired → Phase7OutcomeModel → synthetic_ledger_from_outcome SENT → classify_opportunity_evidence FULL MATURE (as_of=maturity_at) → build_optimization_input → RowBundle → build_creator_dataset → train_creator_model not abstained (n_primary 12 floors 6/2/2) → predict_for_input` **PASS**. Also verifies `opp.decision_snapshot["history"]["total_offer_count"] == hs.total_offer_count` per bundle.

---

## Test results (counts PASS/FAIL, new tests list)

**New:** `tests/test_simulation_snapshot_wiring.py` 8 tests **PASS**

- `test_a_none_zero_defaults_valid` (zero defaults still build_optimization_input passes)
- `test_b_history_wiring_counts_and_features` (fan.recent_offer_count == history.recent, last_offer ISO matches, feature buckets R1/R2 etc. not always R0, at least one non-zero in 30 fans)
- `test_c_conversation_lifecycle_mapping` (lifecycle hot/warm/cold lowercased)
- `test_d_determinism_same_counts_lifecycle` (same fan/time twice same)
- `test_e_creator_isolation` (fan.creator_id == ledger creator_id, Input creator_id matches)
- `test_f_ground_truth_isolation` (snapshot/ledger no latent scores, hidden has fatigue/conversation)
- `test_g_e2e_optimizer_and_replay` (12 bundles wired, train not abstained, predict, replay identical including counts/lifecycle)
- `test_h_ablation_weight0_reproduces_phase6` (weight0 1e-9)

**Existing:** `tests/test_simulation_conversation_state.py` 13 PASS, `tests/test_simulation_price_response.py` 19 PASS, `tests/test_simulation_content_affinity.py` 17 PASS, `tests/test_simulation_behavior.py` 16 PASS, `tests/test_simulation_outcome.py` 13 PASS, `tests/test_simulation_world.py` 11 PASS, `tests/test_simulation_contracts.py` 30 PASS → **127 PASS** (`119+8`) in 9.09s.

**Not run as DB/Redis unavailable:** integration `tests/test_p35_6_offline_optimizer` etc. would still PASS but report UNVERIFIED for DB writes; no DB/Redis writes in simulation verified.

---

## Known issues remaining & Phase 8 readiness

- **240h window early baseline:** `FatigueModel` history span 0..240h from `simulated_start`; evaluated_at early (e.g., 2026-01-01) gives few past events → baseline fatigue near 0, correct but early opportunities have less history signal. Documented.
- **UTC only:** `SyntheticFan.timezone UTC` only, `TimeContextModel` UTC hour/day/weekend; no local payday. Future scenario variable needed for Phase8 pay-cycle.
- **Recency dual definitions:** `ConversationStateModel` recency_score `exp(-hash*72/24)` is hash-based independent of `HistoryState` recency (which derives from last_offer hours). Counts now unified via same `HistoryState` source for observer counts, but latent recency_score still hash*72 not linked to history timestamps — divergence kept but documented; counts unified, scores separate.
- **FEATURE_NAMES unchanged:** Time hour/day not yet observable feature (would require bump to `p356.features.v2`). Kept latent.
- **Phase 8 ready:** YES — snapshot wiring provides learnable proxy (counts/lifecycle) for optimizer to associate with latent fatigue/conversation effects, while latent scores remain hidden and deterministic. Scenario packs can now vary `seed, fan/content creation, price 500+(i*600)%5000, conversation lifecycle, history counts, time` via `SimulationRun.scenario_id` and injected models.

No production modifications, no DB writes, no Telethon/fangate, creator isolation preserved, SHA256 determinism intact, replay guarantee holds for 7.1 wiring.

