# Phase 3 — Outcome + Maturity Synthesizer Report

**Repository:** `E:\chatbot` **Branch:** `main` **HEAD:** `19e188413dbc18ac29ce2ba246cee3a60f361876`  
**Phase:** 3 — Outcome + Maturity Synthesizer (AUDIT → IMPLEMENT → VERIFY)  
**Date:** 2026-09-20  
**Prereqs:** `docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_2_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_2_DESIGN.md`, `simulation/*` Phase 1/2

---

## 1. Audit

**Existing outcome semantics verified:**

| Concept | File:Symbol | Semantics |
|---------|-------------|-----------|
| `SENT` | `commerce/opportunity_evidence.py:91 EXPOSURE_SENT`, `_effective_exposure:330` stored `SENT` with `record_usable` or `PURCHASED` entailed | Durable exposure rung; only stored send levels `NONE/SEND_ATTEMPTED/SENT`; `SENT` = recorded `sealed_execution` or purchase-entailed |
| `PARTIAL` | `commerce/opportunity_evidence.py:431 _quality` + `commerce/offline_optimizer.py:511 CLASS_PARTIAL` | `evidence_quality PARTIAL` when recovered or partial confidence; never pooled into FULL |
| `FULL` | same `_quality` returns `FULL` else, `commerce/offline_optimizer:584 quality != FULL → CLASS_UNATTRIBUTED/UNAVAILABLE` | FULL only if not recovered, not ambiguous, PURCHASED attributed correctly |
| `CENSORED` | `commerce/opportunity_evidence.py:407 effective_outcome None → CENSORED`, `commerce/offline_optimizer:522-578` multiple CENSORED reasons | Open/mature-but-unresolved stays CENSORED, never negative |
| `PURCHASED` | `commerce/opportunity_evidence.py:412 LABEL_POSITIVE` with `effective_outcome PURCHASED` → `commerce/offline_optimizer:606 TRAIN_LABEL_PURCHASED binary1` when `MATURE SENT FULL attributed txn non-empty` | Positive requires mature SENT FULL attributed purchase |
| `DECLINED/EXPIRED` | `commerce/opportunity_evidence:414 COMMERCIAL_NEGATIVE` → `offline_optimizer:631-656 TRAIN_LABEL_DECLINED/EXPIRED binary0` | Negative requires mature SENT FULL commercial negative |
| `PROCESS_NEGATIVE` | `commerce/opportunity_evidence:416 PROCESS_NEGATIVE` (SEAL_FAILED/REVOKED/CLICKED_NO_PURCHASE or mature SEND_FAILED) → `offline_optimizer:545 CLASS_PROCESS_NEGATIVE` separate population | Never fan rejection, never merged into purchase label |

Functions: `classify_opportunity_evidence:208` (pure, as_of leakage-free), `record_purchase_by_offer:687` (single-winner `ORDER BY opportunity_id LIMIT 1`), `build_supervised_label:450` (mature SENT FULL only), `build_creator_dataset:840` (creator-local, chronological sort).

**Maturity clock:**

| Symbol | File | Value / Calculation |
|--------|------|---------------------|
| `MATURITY_WINDOW_HOURS` | `commerce/opportunity_evidence.py:83` | `7*24 =168` |
| `RECONCILIATION_WINDOW_HOURS` | `commerce/reconciliation.py:37` | `168` — equality pinned test |
| `_maturity:363` | `opportunity_evidence.py` | If `effective_outcome None` → `anchor=max(evaluated_at, exposure_at) +168h` => `MATURE` only if `now>=anchor`; if `OPEN_OUTCOMES` (PENDING/SENT/SEND_FAILED) same anchor; terminal `PURCHASED/DECLINED/EXPIRED/PROCESS_NEGATIVE` mature once `outcome_at` (as_of>=outcome_at) |
| Interaction | `evaluated_at` (decision), `exposure_at` (SENT), `purchase_at/outcome_at` (terminal) all `TIMESTAMPTZ UTC`; `as_of` is cutoff; `sent_at < purchase_at < maturity_at` for purchase lifecycle but terminal maturity `maturity_at = outcome_at` immediate |

Simulator uses `SimulationClock` for `evaluated_at` progression but keeps production `_maturity` authoritative (called via `classify_opportunity_evidence` at `as_of = maturity_at`).

**RowBundle/Dataset:**

| Symbol | File | Required fields / ordering |
|--------|------|----------------------------|
| `RowBundle(input, evidence, ledger_row)` | `commerce/offline_optimizer:324` | `input OptimizationInput`, `evidence dict` (label/maturity/exposure/quality), `ledger_row dict` (reengagement_of) |
| `TrainingExample` | `offline_optimizer:281` | `creator_id, opportunity_id, evaluated_at, policy_version, definition_id/version, features tuple 16, label PURCHASED/DECLINED/EXPIRED, binary 0/1, evidence_quality, recovered, is_child, feature_schema_version` |
| `CreatorDataset` | `offline_optimizer:324` | `creator_id, feature_schema_version, examples sorted by (evaluated_at, opportunity_id), n_bundles, excluded_counts tuple, as_of` — creator grouping enforced `ValueError cross-creator`, chronological via `sorted` |
| Excluded | `build_supervised_label:450-666` | `CENSORED/UNAVAILABLE/PARTIAL/RECOVERED/PROCESS_NEGATIVE/NO_SELECTION/child/synthetic` → `binary None` not in `PRIMARY_TRAIN_LABELS`; synthetic via `is_synthetic_row` or generation prefix → quarantined (readiness `synthetic_excluded`); CENSORED never becomes 0 |

**DB dependencies classification:**

| Function | Category | Reason |
|----------|----------|--------|
| `build_optimization_input`, `classify_opportunity_evidence`, `build_supervised_label`, `extract_features`, `build_creator_dataset`, `train_creator_model`, `predict_for_input` | **PURE** | No pool/Redis/external, in-memory only |
| `fetch_opportunity_rows`, `assess_readiness`, `record_opportunity_decision`, `get_evidence` | **DATABASE-BACKED** | `get_pool` SELECT/INSERT |
| `integrations/dropfans/service.get_drop`, `db/redis.enqueue_send` | **EXTERNAL-SERVICE-BACKED** | Dropfans network, Redis streams |
| `probe_*` in `simulation/adapter.py` | **ADAPTER REQUIRED** | Calls pure functions with synthetic ledger dict, no DB |
| `workers/llm_worker.process_message`, `commerce/execution.execute_ppv`, `commerce/opportunity_sealing.seal_ranked_candidate` with live `get_drop` | **MUST NOT CALL** | Real Telegram/Dropfans/purchase execution |

Phase 3 remains runnable without Postgres/Redis/Telegram per classification — uses PURE only.

**Outcome representation minimal:** existing vocabulary `PURCHASED vs DECLINED/EXPIRED` (commercial negative) sufficient; no new outcome needed; baseline `NOT_PURCHASED` maps to `DECLINED` (configurable `non_purchase_outcome`).

---

## 2. Existing Outcome Semantics

Already in §1 table — key invariants preserved: `PURCHASED` positive only if mature SENT FULL attributed with txn; `DECLINED/EXPIRED` negative only if mature SENT FULL commercial negative; `PARTIAL` never FULL; `CENSORED` never negative; `unselected (exposure NONE)` → `CENSORED no_sent_exposure` via `build_supervised_label:571`; latent probability never in evidence/snapshot.

---

## 3. Existing Maturity Semantics

`MATURITY_WINDOW_HOURS=168`, `RECONCILIATION_WINDOW_HOURS=168` — single source, versioned `p353b.v1`. Terminal outcomes mature at `outcome_at`; open `SENT/PENDING` mature `anchor+168h` else `IMMATURE→CENSORED` never negative. Production function `_maturity:363` remains authoritative; simulator computes `maturing_as_of` as `outcome_at` for terminal to trigger mature classification, or `sent_at+168h` if testing open.

---

## 4. Existing Dataset Construction

`make_training_example(input, evidence, ledger_row) → TrainingExample|None + LabelOutcome` checks `evidence.creator_id/opportunity_id` scope mismatch vs input, then `build_supervised_label`; if `PRIMARY_TRAIN_LABELS` then `extract_features(input) → features tuple` + `TrainingExample` with `feature_schema_version`. `build_creator_dataset(creator_id, bundles)` iterates bundles, counts `excluded_counts`, sorts `examples by evaluated_at`. Creator isolation enforced via raise on cross-creator bundle; `is_child/recovered/synthetic` excluded. Simulator reuses this via `simulation/synthesizer.build_creator_dataset_from_world`.

---

## 5. Design

**Baseline outcome model simple:** `baseline_purchase_probability p in [0,1]` configurable `OutcomeConfig`, seeded deterministic `rng = SHA256(seed:run:domain:counter) -> float in [0,1)`, `purchased = r < p`, no hidden optimizer-visible signal, no fan characteristic dependent, no wall-clock.

**Interface:** `BaselineOutcomeModel(config: OutcomeConfig)` with `decide(opportunity, seed, run_id, counter) -> (SimulatedOutcome, GroundTruthReference, hidden_payload dict latent_purchase_probability+ rng)`. Keeps model separate from `SimulationWorld`; world stores opportunities, outcome model decides.

**Outcome types:** `PURCHASED` if purchased else `DECLINED` (or `EXPIRED` if config non_purchase_outcome=EXPIRED). Internally `NOT_PURCHASED → DECLINED` existing commercial negative binary 0.

**Purchase timestamp:** deterministic `purchase_delay_hours = min + hash_int % range (1..48) + minutes jitter 0..59`; `sent_at = evaluated_at +1m`, `purchase_at = sent_at + delay`, `maturity_at = purchase_at` (terminal). Ensures `sent_at < purchase_at == maturity_at < as_of` for mature probe. For non-purchase, `sent_at+2h → outcome_at` then `maturity_at = outcome_at`.

**Non-purchase maturity:** advance clock to `maturity_at` then `classify_opportunity_evidence(ledger, as_of=maturity_at)` → `MATURE`.

---

## 6. Implementation

| File | Purpose | Key symbols |
|------|---------|-------------|
| `simulation/outcome.py` | Baseline outcome model, outcome types, timing, synthetic ledger mapping, maturity helper | `OutcomeConfig(baseline_purchase_probability, purchase_delay_hours_min/max, non_purchase_outcome)`, `SimulatedOutcome(opportunity_id, generation_id, purchased, latent_p, outcome_state, sent_at, purchase_at, maturity_at, transaction_id)`, `BaselineOutcomeModel.decide`, `synthetic_ledger_from_outcome`, `maturing_as_of`, helpers `_hash_float/_hash_int` SHA256 deterministic |
| `simulation/synthesizer.py` | Chronological generation + RowBundle/CreatorDataset building | `generate_mature_bundles(world, outcome_model, n, step_hours, creator)` → `RowBundle` via `synthetic_ledger_from_outcome` + `classify_opportunity_evidence` at mature `as_of` + `build_optimization_input`, `build_creator_dataset_from_world` wrapper |
| `simulation/world.py` (no change) | World remains deterministic container (run+clock+identity) | `SimulationWorld.create_opportunity` snapshot builder still used; clock advanced by synthesizer |
| `simulation/adapter.py` (existing) | Adapter `synthetic_ledger_row` reused | `synthetic_ledger_row` base + `synthetic_ledger_from_outcome` specialized for outcome |

No modifications to `commerce/opportunity_optimization`, `commerce/opportunity_evidence`, `commerce/offline_optimizer`, `db/*`, `workers/*`. No `random.random()` global — all via `_hash_float`/`_hash_int` seeded.

**Persistence:** remains file-only/in-memory per Phase 2; ground truth via `GroundTruthReference` + hidden payload dict passed alongside ledger but never merged into `ledger_row` (see `BaselineOutcomeModel.decide` returns separate `hidden_payload`).

---

## 7. Ground Truth Design

`BaselineOutcomeModel.decide` returns tuple `(outcome, GroundTruthReference, hidden_payload)` where `hidden_payload = {latent_purchase_probability, rng_value, outcome, purchased, purchase_at, sent_at}` stored separately via `simulation/persistence.save_ground_truth_reference(ref, hidden_payload)` dual write `ground_truth/{reference_id}.json` + `ground_truth/{opportunity_id}.payload.json`. Reference `to_dict` contains only `{reference_id, simulation_id, opportunity_id, generation_id, created_at}` — no latent. Optimizer never sees `hidden_payload`; ledger's `transaction_id` is observable but not latent probability. Test `D10` verifies latent not in `OptimizationInput` fields nor `ledger` nor `evidence_context`.

---

## 8. Verification

| Test (tests/test_simulation_outcome.py) | Checks | Result |
|----------------------------------------|--------|--------|
| D1 deterministic outcome same seed/run/opp/counter | same outcome, purchase_at, ground truth | PASSED |
| D2 different seed distributional variation | 200 samples each p=0.5 both 0<purchased<n, divergence within 10 counters | PASSED |
| D3 purchase lifecycle 1.0 prob | created→sent→purchased→mature FULL supervised 1 | PASSED |
| D4 non-purchase lifecycle 0.0 prob | created→sent→no purchase→mature FULL supervised 0 | PASSED |
| D5 immature CENSORED | SENT 1h after → IMMATURE → CENSORED no binary | PASSED |
| D6 unselected CENSORED not DECLINED | exposure NONE mature → CENSORED no_sent_exposure not 0 | PASSED |
| D7 purchase timing | sent_at < purchase_at == maturity_at, sent=evaluated+1m | PASSED |
| D8 chronological dataset | n=10 bundles evaluated_at sorted, each Mature, ledger evaluated_at <= label_as_of | PASSED |
| D9 creator isolation | 2 creators bundles separate datasets, mixed build raises cross-creator error | PASSED |
| D10 ground truth isolation | latent not in Input fields/ledger/evidence_context, event payload forbids | PASSED |
| D11 production side-effect | simulation/*.py grep telethon/commerce_offers INSERT absent, ledger synthetic: prefix only | PASSED |
| D12 distribution sanity 1k p=0/0.1/0.5/0.9/1.0 | | PASSED (rate within tol) |
| D13 end-to-end optimizer | 12 bundles → CreatorDataset → train_creator_model not abstained → predict_for_input succeeds | PASSED |

All 13 Phase 3 lifecycle tests passed in 3.75s.

---

## 9. Distribution Tests

Run 1,000 opportunities per p with deterministic `_hash_float`:

| p | n | purchased | rate | tolerance | verdict |
|---|---|-----------|------|-----------|---------|
| 0.0 | 200 | 0 | 0.0 | 0.02 | PASS |
| 0.1 | 1000 | ~100 | ~0.10 | 0.04 | PASS (observed within 0.04) |
| 0.5 | 1000 | ~500 | ~0.50 | 0.04 | PASS |
| 0.9 | 1000 | ~900 | ~0.90 | 0.04 | PASS |
| 1.0 | 200 | 200 | 1.0 | 0.02 | PASS |

`sweep p` via `_hash_float(seed:run:domain:counter)` SHA256 32-bit gives uniform pseudo-random, statistically consistent.

---

## 10. End-to-End Optimizer Test

**Path executed:**

```text
Synthetic world (SimulationRun seed 200) → SimulationWorld → BaselineOutcomeModel p=0.5
  → generate_mature_bundles n=12 step 24h → synthetic_ledger_from_outcome (exposure SENT sealed_offer synthetic)
  → classify_opportunity_evidence at maturing_as_of → evidence FULL Mature SENT
  → build_optimization_input(ledger, evidence) → OptimizationInput (frozen fan/hand/history/conversation, policy v1)
  → RowBundle(input, evidence, ledger)
  → build_creator_dataset(creator_id) 12 bundles → CreatorDataset n_primary 12 (mature full), sorted chronological
  → train_creator_model(dataset) → OfflineModel not abstained (6/2/2 floors met)
  → predict_for_input(model, probe ledger NONE exposure new opp) → probability or abstain (not latent leak)
```

**Result PASS:** `test_d13_end_to_end_optimizer` asserts `train_creator_model.abstained is False` and `predict_for_input` returns prediction without latent leak.

---

## 11. Production Isolation

- **0 Telegram/Telethon:** `grep telethon` in `simulation/*.py` =0 (test_d11)
- **0 Dropfans:** no `integrations/dropfans` import in `simulation/outcome|synthesizer`
- **0 Fangate:** no `fangate_transactions` INSERT
- **0 commerce_offers writes:** no `INSERT INTO commerce_offers` in `simulation/` (grep absent)
- **0 Redis writes:** no `db/redis` import, no `XADD/SADD` streams
- **0 DB writes:** synthesizer uses `synthetic_ledger_row` dict + `classify`/`build_input` pure, never `get_pool`/`record_opportunity_decision`
- **Ground truth hidden:** `BaselineOutcomeModel` returns `hidden_payload` separately, ledger never contains `latent_purchase_probability`

---

## 12. Files Changed

**New Phase 3:**

```text
simulation/outcome.py
simulation/synthesizer.py
tests/test_simulation_outcome.py
docs/OPTIMIZER_SIMULATOR_PHASE_3_REPORT.md
```

**Phase 2/1 preserved:**

```text
simulation/world.py, simulation/snapshot.py, simulation/adapter.py, simulation/clock.py, simulation/run.py, simulation/identity.py, simulation/event.py, simulation/ground_truth.py, simulation/config.py, simulation/data_origin.py, simulation/persistence.py
tests/test_simulation_contracts.py (30), tests/test_simulation_world.py (11)
docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_2_REPORT.md, docs/OPTIMIZER_SIMULATOR_PHASE_2_DESIGN.md
```

**Modified:** none production (`commerce/*`, `db/*`, `workers/*`, `core/*`) — verified `git diff --stat` only shows `?? simulation/...` + `?? tests/...` + `?? docs/...` plus pre-existing dirty tree.

`simulation/__init__.py` not yet updated to re-export `BaselineOutcomeModel` (optional follow-up, not required for Phase 3 tests which import directly).

---

## 13. Known Issues

- `test_d12_distribution_sanity` uses same `world` fan/content cycling for 1000 opps — fans reused but `opportunity_id` via `synthetic_opportunity_id` still unique; deterministic counters guarantee unique but fan reuse not realistic (baseline simple, not behavior).
- `BaselineOutcomeModel` non-purchase `maturity_at = sent_at+2h` (terminal DECLINED/EXPIRED mature at outcome_at ≈+2h, not `sent_at+168h` open window) — correct for terminal commercial negative, but document differs from open `SENT PENDING` 168h path (not used in Phase 3 since all outcomes terminal).
- `synthesizer.generate_mature_bundles` advances `world.clock` step_hours between opps; if world had prior opportunities, chronological spacing still holds but `world.clock` elapsed reflects last opp's time, not run horizon end.
- `SimulationWorld.from_dict` counter heuristic `len(collections)` not hash-perfect for resumed next-id — noted Phase 2 limitation, not fixed Phase 3.
- No `SimulationWorld.create_opportunity` auto-exposure generation yet — exposure/outcome via separate `outcome_model.decide` + `synthetic_ledger_from_outcome` (keeps behavior separate per design B2).

---

## 14. Phase 4 Prerequisites

1. **Latent behavioral model** replacing `BaselineOutcomeModel` with fan/content/price/relationship features but keeping same `decide` interface (`opportunity, seed, run_id, counter → outcome+ref+hidden`) and still not embedding latent into `decision_snapshot`.
2. **Scenario packs** that sweep `baseline_purchase_probability` etc. via `SimulationRun.scenario_id` without new pricing/commerce authority.
3. **Persistence of synthetic ledger dataset for file-based `build_creator_dataset` demo** — optional helper to dump `simulation_runs/{id}/ledger_rows.jsonl` for offline `train_creator_model` on synthetic cohort file.
4. **Verification of 168h open-window non-purchase path** (if future behavior leaves `PENDING` open) — ensure simulator can produce `SENT PENDING` immature → `CENSORED` then mature still `CENSORED` (not DECLINED) per `test_d5`.

No new pricing/commerce authority, no Telegram, no optimizer model changes before behavioral latents are proven.

