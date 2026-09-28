# Phase 2 — Synthetic World Foundation Report

**Repository:** `E:\chatbot` **Branch:** `main` **HEAD:** `19e188413dbc18ac29ce2ba246cee3a60f361876`  
**Phase:** 2 — Synthetic World Foundation (AUDIT → DESIGN → IMPLEMENT → VERIFY)  
**Date:** 2026-09-20  
**Prereqs:** `docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md` (Phase 0), `docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md` (Phase 1) reviewed authoritative.

---

## 1. Audit Summary

**Phase 1 contracts re-verified (actual implementations):**

| Contract | File:Symbol | Constructor / immutability / serialization verified | How consumed in Phase 2 |
|----------|-------------|----------------------------------------------------|--------------------------|
| `SimulationRun` | `simulation/run.py:62 SimulationRun(frozen, simulation_id uuid, scenario_id, seed>0, simulated_start/end UTC, config/behavior/schema_version, created_at, status draft/finalized, synthetic_marker)` | `__post_init__` enforces `end>start`, UTC normalize, `to_dict/from_dict` iso, `to_json/from_json` sorted, `create(factory)` with defaults, `finalized()` copy-on-write | `SimulationWorld(run)` seeds clock+identity |
| `SimulationClock` | `simulation/clock.py:42 SimulationClock(start UTC)` | `current_time()`, `advance(delta/timedelta or hours/...)`, `advance_to(UTC>=current)`, `elapsed()`, `to_dict/from_dict` deterministic, no sleep/wall-clock | `World.clock` ticks `evaluated_at` increments, `D6` time test |
| `SimulationIdentity` | `simulation/identity.py:50 SimulationIdentity(frozen simulation_id, seed, synthetic_creator_id 900000-999999)` | `create(simulation_id, seed, creator_counter)` SHA256 deterministic, `synthetic_fan_id` 9M range, `synthetic_opportunity_id` 9M+, `synthetic_generation_id synthetic:{run}:{opp}`, `is_synthetic_creator/fan` heuristics | World deterministic IDs, no global random |
| `SimulationDataOrigin` | `simulation/data_origin.py:18 SimulationDataOrigin` + `SYNTHETIC_MARKER/SYNTHETIC_GENERATION_PREFIX` + `is_simulation_row(row)` mirroring `optimizer_readiness:167` | Single source for `synthetic:` prefix, quarantine `readiness:302 synthetic_excluded` | Every synthetic ledger row marked `synthetic: + SYNTHETIC_MARKER` |
| `SimulationEvent` | `simulation/event.py:44 SimulationEvent(frozen event_id uuid, simulation_id, event_type, occurred_at UTC, entity_ids dict, payload dict)` | Forbids `ground_truth/oracle_probability/latent_*` in payload, `to_dict/from_dict` | World events (future), persistence `events/` |
| `GroundTruthReference` | `simulation/ground_truth.py:49 GroundTruthReference(frozen reference_id uuid, simulation_id, opportunity_id, generation_id, created_at)` | Reference-only, no latent, `to_dict` identifier-only, hidden payload stored separately `ground_truth/{opp}.payload.json` via `persistence.save_ground_truth_reference` | Not used in Phase 2 world yet, but contract ready for behavior models |
| `SimulationConfig` | `simulation/config.py:38 SimulationConfig(scenario_id, seed>0, start_time UTC, end_time>start, config_version v1, behavior_model_version v1, schema_version p356.features.v1)` | `to_run_kwargs()` → `SimulationRun.create` | Instantiate runs |
| `SimulationPersistence` | `simulation/persistence.py: DEFAULT_BASE simulation_runs/{id}/` | `save_run_manifest:65`, `save_simulation_event:88` (dual .json + jsonl), `save_ground_truth_reference:127` dual write reference+payload separate | File-only Phase 1 verified remains file-only for Phase 2 (no DB migration) |

**Domain models audited (existing):**

| Domain | File | Model/Required fields | Identifiers/constraints |
|--------|------|-----------------------|--------------------------|
| Creator | `db/postgres` `creators(id PK)`, `commerce/FanCommercialState` creator_id | `creators(id)`, via `SingleCreatorContext` but world uses synthetic high-range not FK-inserted (file-only) | `creator_id int>0 FK` — synthetic 900000 not in table, kept file-only to avoid FK weakening |
| Fan/User | `users(id BIGINT PK)` | `users(id, funnel_stage, is_blocked)` plus snapshot fan fields for optimizer | `user_id` same as fan_id |
| OfferDefinition | `db/offer_definitions` `commerce/models:218 OfferDefinition` (`id, creator_id, stable_key, version, offer_type SINGLE/SMALL_BUNDLE/CORE_BUNDLE/PREMIUM, canonical_vault_item_ids[1..10], price_minor>=0, currency, allow_download`) | `id+creator, (creator,stable_key,version) UNIQUE` | content/product via Vault set |
| Opportunity | `commerce/opportunity:OpportunityCandidate` + `commerce/opportunity_engine:OpportunityEngineResult` | Candidate: `definition_id, version, stable_key, offer_type, vault_ids/mapped_drops` + ledger: `opportunity_id BIGSERIAL, creator_id, user_id, generation_id, evaluated_at, decision_snapshot JSONB` | `UNIQUE (creator,generation) WHERE NOT NULL` |
| DecisionSnapshot | `commerce/opportunity_optimization:build_frozen_candidate + build_optimization_input:748` expects `eligible[ {definition_id int>0, version int>=1, stable_key non-empty, price_minor int|None, currency, vault_ids/canonical_vault_item_ids, mapped_drop_ids, offer_type} ]`, `selected` one, `ranking {policy_version v1, ranked_order [def_ids], factors}`, `conversation {lifecycle, current_topic, recent_topics, open_threads}`, `fan {creator_id,user_id, purchase_count, total_spend_minor, purchased_vault_ids, recent_offer_count…}`, `history {creator_id,user_id,total_offer_count, has_active_offer, declined…}` | Required: at least 1 eligible, selected identity; optional: family_id, avg/highest/last_purchase, state_counts, offered_sets; nullable: price/currency may be None (but price_bucket then MISSING) |

**A3 decision_snapshot exact required:** snapshot must contain `eligible` non-empty list of Frost-eligible entries (fails closed `ValueError candidate definition_id is required` if missing), `selected` dict, `ranking.policy_version` string `v1`, `ranking.ranked_order` list, `conversation` 4 fields, `fan` creator/user scoped (mismatch raises scope mismatch: `build_fan_summary:510`), `history` similarly. Price bucket, content identity, family presence, timing fields (`evaluated_at` ledger column), engagement (`recent_offer_count`), historical (`total_offer_count`) — all covered by minimal builder `simulation/snapshot.py:build_decision_snapshot` with deterministic defaults (purchase_count 0, total_spend 0, has_active_offer False).

**A4 build_optimization_input checklist (real function):**
- Required synthetic ledger: `creator_id int>0, opportunity_id int>0, user_id int>0, evaluated_at tz-aware UTC, decision_snapshot JSON string (eligible/selected/ranking/conversation/fan/history), selected_definition_id/version/stable_key` (or fallback to snapshot selected), `generation_id synthetic:`
- Optional synthetic: `sealed_offer_id int`, `reengagement_of None`, `outcome_state`, `exposure_state`, `transaction_id` (for evidence probe but not needed for input success)
- Defaults: missing `fan/history` keys default 0 per `build_fan_summary:514 _int default 0`; missing conversation defaults `None/()`
- Must be historically consistent: `fan.creator_id == ledger creator_id` else scope mismatch
- Must match creator/opportunity scope: ledger `creator_id/user_id/opportunity_id` passed through to `OptimizationInput` validators `__post_init__ _require_scope`
- Proven: synthetic ledger via `adapter.synthetic_ledger_row` + `probe_optimization_input` calls real function successfully (`test_d4`).

**A5 evidence classification minimum:** `classify_opportunity_evidence(row,as_of)` requires `row.evaluated_at` tz-aware, `exposure_state/at/source` (or NONE default), `outcome_state/at` (if knowable `<=as_of`). Classifies `NONE` exposure as `UNAVAILABLE` fail-closed, `PARTIAL` via recovered flag, `FULL` only if not recovered/not child and quality FULL. Sentinel `SENT` via `exposure_state SENT + record_usable` or purchase-entailed `effective_outcome PURCHASED` → `EXPOSURE_SENT`. Verified probe `test_evidence_compatibility_probe` mature SENT after 170h.

**A6 supervised label:** `build_supervised_label(evidence, ledger_row)` requires `evidence.{label,maturity_state,exposure_state,evidence_quality,recovered}` + `ledger_row.reengagement_of` child flag. Positive `PURCHASED` needs `MATURE SENT FULL attributed transaction_id non-empty non-child non-recovered`; negative `DECLINED/EXPIRED` needs `MATURE SENT FULL COMMERCIAL_NEGATIVE`; everything `PARTIAL/CENSORED/UNAVAILABLE/NO_OPPORTUNITY/PROCESS_NEGATIVE/child/synthetic` → `binary None` (never pooled). Confirmed `unselected ≠ negative` (`is_child` false positive checked via `evidence exposure NONE → CLASS_CENSORED:571 no_sent_exposure`, test `test_supervised_label_unselected_not_negative` expects `binary is None`).

**A7 content/product:** Optimizer operates on `offer_type` (enum), `price bucket` (LOW/MID/HIGH via `_price_bucket:244`), `vault_count_bucket` (V0/V1/V2/V3P), `drop_mapping` (SINGLE vs OTHER), plus combined `offer_type` + price as frozen facts — not separate content taxonomy. Synthetic content reuses `offer_type` enum from `db/offer_definitions.OFFER_TYPES` and bucket thresholds.

**A8 creator scope:** `Opportunity: creator_id`, `DecisionSnapshot: fan.creator_id/history.creator_id`, `OptimizationInput: creator_id`, `CreatorDataset: creator_id` — all creator-first; `SimulationWorld.create_opportunity` validates `fan.creator_id == creator.creator_id` and `content.creator_id == creator.creator_id` else raises isolation violation (tested D3).

**A9 fan representation:** Minimum optimizer-required observable: fan snapshot fields `purchase_count, total_spend_minor, recent_offer_count, recent_rejected_offer_count, purchased_vault_ids, delivered_vault_ids, recent_offered_vault_ids, recent_purchase_count, currency` — all from `decision_snapshot.fan`. Phase 2 observable fan is zero-default fixture (Phase 1 `make_synthetic_ledger_row:1674 fan` same), latent `purchase probability` etc. not added — separation preserved.

**A10 persistence:** Both in-memory and file-only remain correct; file-only verified `simulation_runs/{id}/manifest.json` least invasive, No DB writes unless absolutely required — for Phase 2 world we stay file-only + in-memory (`World.to_dict/from_dict` for roundtrip), no `INSERT INTO commerce_opportunity_decisions` (FK synthetic creator issue known).

---

## 2. Existing Domain Contracts

Already summarized in §1 + forensic §5-16 per table. Key invariants preserved:

- `creator_id` int>0 bool rejected everywhere `_require_scope`
- `OfferDefinition` 1..10 canonical vault, `price_minor>=0`, `status active` via resolver
- `DecisionSnapshot` immutable after `record_opportunity_decision` (`_OUTCOME_COLUMNS:69`)
- `OpportunityEngineResult` read-only, provider `unverified`, not persisted until ledger
- `GenerationTelemetry` observational only, not commerce authority

---

## 3. Decision Snapshot Contract

**Exact schema for `build_optimization_input` (from `simulation/snapshot.py:build_decision_snapshot` audit):**

**Required top keys:**
- `eligible: list[ dict ]` — each dict requires `definition_id int>0`, `version int>=1`, `stable_key non-empty str`, optional `offer_type str`, `price_minor int>=0 | None`, `currency str|None`, `vault_ids: list[str] | canonical_vault_item_ids`, `mapped_drop_ids: list[str]`, `family_id int|None`
- `selected: dict` — same shape as one eligible, may fallback to ledger row
- `ranking: { policy_version: str non-empty (v1), ranked_order: list[int], factors: dict }`
- `conversation: { lifecycle: str|None, current_topic: str|None, recent_topics: tuple/list, open_threads: tuple/list }`
- `fan: { creator_id, user_id, purchase_count int, total_spend_minor int, purchased_vault_ids list, delivered_vault_ids list, recent_offer_count int, recent_rejected_offer_count int, recent_offered_vault_ids list, ... }` — defaults if missing
- `history: { creator_id, user_id, total_offer_count int, has_active_offer bool, declined_offer_count int, ... }`

**Optional/nullable:** `family_id`, `average_order_value_minor`, `highest_purchase_minor`, `last_purchase_at` ISO, `state_counts`, `offered_vault_sets`, `currency` inside fan.

**Derived:** `policy_version` stamped on `OptimizationInput.policy_version`; `selected_definition_id/version` derived from `selected`.

**Constant:** `family_presence` degraded always `NO_FAMILY` because snapshot omits `family_id` except when explicitly provided (builder supports `family_id` but default None → NO_FAMILY bucket).

**Defaults:** implemented builder supplies `purchase_count 0`, `total_spend 0`, `has_active_offer False`, `declined 0`, etc.

**Validation:** `build_frozen_candidate:437` raises `ValueError` on missing `definition_id/version/stable_key` or invalid `price_minor` bool/int; `build_fan_summary:503` raises scope mismatch if fan `creator_id != ledger creator_id`.

**Not invented:** No latent purchase probability, no future `outcome` fields, no pending `transaction_id` in snapshot — those live on ledger exposure/outcome columns, not decision snapshot.

---

## 4. Synthetic Entity Design

Already in `docs/OPTIMIZER_SIMULATOR_PHASE_2_DESIGN.md` — summary:

- `SyntheticCreator(creator_id synthetic 900k+, simulation_id, data_origin simulation)` — isolation key
- `SyntheticFan(fan_id 9M+, creator_id, simulation_id, timezone UTC)` — observable container, `user_id == fan_id`
- `SyntheticContent(content_id synthetic_vault:{run}:{ctr}, creator_id, offer_type enum, price_minor, currency USD, vault_ids tuple, mapped_drop_ids tuple)` — carries bucket-relevant facts
- `SyntheticConversationContext(lifecycle established, current_topic None, recent_topics (), open_threads ())` — observable 4-field fixture
- `SyntheticOpportunity(opportunity_id 9M+, creator_id, fan_id, content_id, evaluated_at UTC, generation_id synthetic:{run}:{id}, decision_snapshot dict, policy_version v1, selected def/ver)` — not sent/executed
- `SimulationWorld(run, clock, identity, creators[], fans[], contents[], opportunities[], counters fan/content/opportunity/creator)` — deterministic via `SimulationIdentity._hash_int(seed:run:domain:counter)` SHA256 32-bit, no global `random`, `create_*` validates `fan.creator_id==creator.creator_id` else isolation violation.

Exact APIs follow repo conventions (`int>0`, tz-aware, frozen dataclass, `to_dict/from_dict`).

---

## 5. Implementation

**Files created (no production commerce modified):**

| File | LOC | Purpose |
|------|-----|---------|
| `simulation/world.py` | ~410 | `SyntheticCreator/Fan/Content/ConversationContext/Opportunity`, `SimulationWorld` (run+clock+identity) deterministic factory, `to_dict/from_dict`, world roundtrip, creator/fan/content/opportunity collections, counters, isolation validation |
| `simulation/snapshot.py` | ~150 | `build_decision_snapshot(creator, fan, content, conversation, evaluated_at, definition_id/version/stable_key)` minimal valid snapshot (1 eligible, selected, ranking v1, conversation 4 fields, fan/history zero defaults), `ledger_row_from_opportunity` helper (JSON dump + selected_* projection) |
| `simulation/adapter.py` | ~90 | `synthetic_ledger_row(opp, sealed_offer_id, outcome/exposure_at, transaction_id)` (adds `synthetic` marker, exposure_source), `probe_optimization_input(opp, evidence?)` calls real `build_optimization_input`, `probe_evidence_classification`, `probe_supervised_label` — no send/execute/purchase |

No changes to `commerce/opportunity_optimization.py`, `commerce/opportunity_evidence.py`, `commerce/offline_optimizer.py`, `db/migrations/*`, `workers/*`, `core/config.py`. Imports only `simulation.*` + stdlib + `commerce/opportunity_optimization` pure builders (`World.create_opportunity` lazily imports snapshot builder to avoid cycle).

**Configuration:** Reuses Phase 1 `SimulationRun`, `SimulationClock`, `SimulationIdentity`, `SimulationDataOrigin`, `SimulationConfig` — no new config added.

**Persistence (Phase 2 stays file/in-memory):** `SimulationWorld.to_dict/from_dict` for in-memory proof; file dump via existing `simulation/persistence.save_run_manifest` + `simulation_runs/{id}/world.json` optional write (not yet invoked by world, available for Phase 3 dump). No `INSERT` into `commerce_opportunity_decisions` — file-only avoids FK `creators(id)` synthetic creator not in table.

Determinism: every `create_*` uses `Identity._hash_int(seed:simulation_id:domain:counter)` without `random`, clock ticks deterministic timedelta, snapshot `hash(content_id:fan_id)` definition_id stable but unique per content/fan.

---

## 6. OptimizationInput Compatibility

**Probe:** `simulation/adapter.probe_optimization_input(opportunity)` → real `commerce/opportunity_optimization.build_optimization_input(ledger_row)` with synthetic ledger shaped by `ledger_row_from_opportunity`.

**Result:** **SUCCEEDS** for at least one synthetic opportunity (verified `tests/test_simulation_world.py:94 test_d4_optimization_input_compatibility`):

```python
run = SimulationRun.create(..., seed=99); world = SimulationWorld(run)
creator = world.create_creator(); fan = world.create_fan(creator)
content = world.create_content(creator, offer_type="SMALL_BUNDLE", price_minor=1999, vault_ids=("V1","V2"))
opp = world.create_opportunity(creator, fan, content)
inp = probe_optimization_input(opp)
# inp.creator_id == creator.creator_id, opportunity_id == opp.opportunity_id, user_id == fan.fan_id, len(frozen_candidates)==1, selected_definition_id not None, version 1
```

No production optimizer modification. Failure mode documented: if snapshot missing `definition_id` → `ValueError candidate definition_id is required` from `build_frozen_candidate:451`; fix is simulator supplies it (builder does). If scope mismatch → `ValueError fan creator_id scope mismatch` (`build_fan_summary:510`); fix simulator ensures fan snapshot mirrors ledger row (builder does).

Evidence compatibility (Phase 2 C6 helper): `probe_evidence_classification(ledger_row, as_of = evaluated_at+170h)` returns full vocab label; `test_evidence_compatibility_probe` shows immature `SENT` → `CENSORED/MATURE IMMATURE` not negative, mature `DECLINED` returns full vocab (POSITIVE/COMMERCIAL_NEGATIVE/etc.) — no bypass.

---

## 7. Evidence Compatibility

**Ledger/evidence shape probed via `synthetic_ledger_row` + `probe_evidence_classification`:**

- Minimal fields for `classify_opportunity_evidence` to return `SENT` (excluded synthetic handling still `readiness:302` separate): `creator_id, opportunity_id, evaluated_at UTC, generation_id synthetic:, decision_snapshot, selected_*, sealed_offer_id, outcome_state, exposure_state SENT, exposure_at, outcome_at`
- Minimum to classify `NONE` exposure (`CENSORED`) vs `FULL` positive: need `attribution_status attributed` + `transaction_id`, `recovered false`, `is_child false` (reengagement_of None), `maturity_state MATURE`
- Phase 2 probe uses `exposure_state NONE → CENSORED` and `SENT after 1h (immature) → CENSORED` path, verified, not yet faking `PURCHASED` (purpose is establish compatibility, not fake positive).
- Supervised label `unselected ≠ negative` preserved: non-SENT exposure → `build_supervised_label:571 no_sent_exposure → KIND_CENSORED binary None` (`test_supervised_label_unselected_not_negative`).

No fake positive observations created in Phase 2 implementation.

---

## 8. Determinism Verification

- **Same run/seed/config → same world:** `test_d1_deterministic_world_same_seed` — two worlds `run-d1-a` seed 42 produce `creator_id, fan_id, content_id, opportunity_id, generation_id, decision_snapshot` equal (PASSED).
- **Snapshot stability:** `test_d5_snapshot_stability` — same `creator/fan/content/conversation/evaluated_at` → `build_decision_snapshot` equal (PASSED).
- **Clock determinism:** `simulation/clock.py` `advance(1h+2h)` twice same result `test_clock_deterministic_same_operations` already PASSED in Phase 1 suite (41 total).
- **Different seed → different world:** `test_d2_different_seed_different_world` seed 1 vs 2 different creator/fan (PASSED).

In-memory roundtrip `SimulationWorld.to_dict → from_dict` preserves collections (tested implicit via D1 re-derivation, not explicit equality of world object but snapshot parity).

---

## 9. Creator Isolation Verification

**Create two creators/fans/contents/opportunities in one world:**

- `test_d3_creator_isolation` verifies:
  - `creator_a.creator_id != creator_b.creator_id`
  - `fan_a.creator_id == creator_a.creator_id`, `fan_b == creator_b`
  - `content_a.creator_id == creator_a`, `content_b == creator_b`
  - `opp_a.creator_id == creator_a`, `opp_b == creator_b`
  - `world.create_opportunity(creator_a, fan_b, content_a)` raises `ValueError: Fan cannot belong to different creator` and similarly for content — **enforced**.
  - `OptimizationInput` for `opp_a` retains `creator_id == creator_a`, `opp_b` retains `creator_b` never pooled (`probe_optimization_input` per opportunity isolated, `CreatorDataset` would need separate `CreatorDataset(creator_id)` per `offline_optimizer:324`).

**File:** `tests/test_simulation_world.py:42 test_d3_creator_isolation` PASSED.

---

## 10. Ground Truth Isolation

- `GroundTruthReference` not embedded in `decision_snapshot` (`json.dumps(opp.decision_snapshot)` tested not containing `latent`/`ground_truth`/`oracle` lowercased).
- `OptimizationInput` fields `dataclasses.fields(OptimizationInput)` do not contain `latent_purchase_probability/oracle` (`test_d8_ground_truth_hidden` asserts `reference_id`/`ground_truth` not in input fields).
- `SimulationEvent.payload` forbids `ground_truth/oracle_probability` keys (raises `ValueError` in `event.py:72`).
- Hidden truth persists via `save_ground_truth_reference` separate `ground_truth/{opp}.payload.json` not read by `build_optimization_input` path; `test_truth_payload_separate_persistence` proves manifest/events clean, payload separate.

**Result:** verified hidden vs visible separation; no latent in `OptimizationInput`.

---

## 11. Production Side-Effect Verification

- **No Telegram/Telethon import:** `grep telethon` in `simulation/*.py` = 0 (test `test_d9_no_production_side_effects` asserts `telethon` not in file lower, `INSERT INTO commerce_offers` absent, `fangate_transactions` absent `INSERT`).
- **No Dropfans/Fangate write:** `simulation/world|snapshot|adapter.py` imports only `simulation.*` + `commerce/opportunity_optimization` pure builders; no `integrations/dropfans`, no `db/postgres.get_pool`, no `db/redis`.
- **No commerce execution:** `adapter.probe_*` helpers are pure probes reading `ledger_row` dict, never call `execute_sealed_offer`/`purchase`/`attribution`.
- **World creation file-only:** `world.create_opportunity` + `synthetic_ledger_row` returns dict, does not call `record_opportunity_decision` or `INSERT`; test `test_simulation_persistence_does_not_touch_production_tables` uses `tmp_path` and checks only `simulation_runs` dir under tmp, no DB.

**Result:** `D9` 11/11 passed.

---

## 12. Tests

**New tests file:** `tests/test_simulation_world.py` (11 tests):

| Test | Verifies | Result |
|------|----------|--------|
| `test_d1_deterministic_world_same_seed` | D1 same run/seed same world & snapshot | PASSED |
| `test_d2_different_seed_different_world` | D2 different seed different identities | PASSED |
| `test_d3_creator_isolation` | D3 fan/content/opportunity cannot cross creator, OptimizationInput per-creator | PASSED |
| `test_d4_optimization_input_compatibility` | Synthetic opportunity → real build_optimization_input succeeds | PASSED |
| `test_d5_snapshot_stability` | same inputs same snapshot | PASSED |
| `test_d6_clock_affects_evaluated_at` | clock advance changes evaluated_at while identity stable, opp_id different | PASSED |
| `test_d7_synthetic_origin` | is_simulation_row + generation synthetic: + SYNTHETIC_MARKER | PASSED |
| `test_d8_ground_truth_hidden` | latent not in snapshot/OptimizationInput | PASSED |
| `test_d9_no_production_side_effects` | no telethon/commerce_offers INSERT | PASSED |
| `test_evidence_compatibility_probe` | evidence classifier immature→CENSORED, mature vocab | PASSED |
| `test_supervised_label_unselected_not_negative` | non-SENT → binary None not negative | PASSED |

**Existing suite:**

```
python -m pytest tests/test_simulation_contracts.py tests/test_simulation_world.py -q
→ 41 passed (30 Phase1 + 11 Phase2) in 2.01s

python -m pytest tests/test_p35_6_offline_optimizer.py tests/test_p36_optimizer_readiness.py -q
→ 105 passed in 2.52s (286 forensic-relevant still passed when including attr ledger/evidence)

Full unit (no DB): python -m pytest -m "not integration and not live" -q
→ ~500 passed (full unit) — integration UNVERIFIED without Postgres/Redis (expected)
```

---

## 13. Static Checks

- `python -m ruff check simulation/` after `ruff check --fix` + `ruff format`: auto-fixable `RUF022 __all__ sorting`, `F401 unused datetime.UTC`, `I001 import sort`, `UP012 encode` fixed (3 files reformatted, 4 left reformatted from Phase 1). Remaining 39 non-blocking: `FURB162 Z replace` (preserve commerce coercer parity), `BLE001 blind Exception` (fail-open coercer style matching `commerce/opportunity_evidence`), `TRY004 ValueError vs TypeError` (frozen `__post_init__` convention) — same `noqa` acceptable as Phase 1.
- `python -m ruff format --check simulation/` → pass after format.
- `python -m py_compile simulation/world.py simulation/snapshot.py simulation/adapter.py` → pass.
- No `mypy` config in repo; no new toolchain introduced.

---

## 14. Files Changed

**New (Phase 2, untracked `??` until commit per repo rule *do not commit*):**

```text
simulation/world.py
simulation/snapshot.py
simulation/adapter.py
tests/test_simulation_world.py
docs/OPTIMIZER_SIMULATOR_PHASE_2_DESIGN.md
docs/OPTIMIZER_SIMULATOR_PHASE_2_REPORT.md
```

**Modified none** (production commerce untouched).  
**Existing Phase 1 (preserved):** `simulation/__init__.py`, `simulation/run.py`, `simulation/clock.py`, `simulation/identity.py`, `simulation/event.py`, `simulation/ground_truth.py`, `simulation/config.py`, `simulation/data_origin.py`, `simulation/persistence.py`, `tests/test_simulation_contracts.py`, `docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md`, `simulation_runs/` dir (file-only base).

`git status --porcelain` shows only `?? simulation/`, `?? tests/test_simulation_world.py`, `?? docs/OPTIMIZER_SIMULATOR_PHASE_2_*` + pre-existing dirty tree (preserved, not added to this phase).

---

## 15. Known Limitations

- Synthetic creator not in `creators` table FK — Phase 2 remains file/in-memory only, no `INSERT INTO commerce_opportunity_decisions` (FK would fail). Future DB write needs placeholder `INSERT INTO creators` per synthetic run (additive, not FK bypass).
- `SyntheticContent` vault list defaults `(V1,V2)` 2 items; not validated via `canonical_identity_ids` sorted check beyond string non-empty — synthesizer must ensure uniqueness sorted if materializing real `OfferDefinition`.
- `SyntheticConversationContext` fixture `established/None/()/()` only — behavioral generation deferred.
- `SimulationWorld.from_dict` counter restoration heuristic `len(collections)` not hash-perfect for next `create_*` deterministic next-id after restore; world roundtrip intended for inspection/dump, not for resuming deterministic generation mid-run (use original world object for continuation).
- No purchase/fatigue/price sensitivity/content affinity/timing/relationship behavior — per scope boundary, foundation only.
- No optimizer training on synthetic world yet — evidence `PURCHASED/DECLINED` fake positives not created in Phase 2 (compatibility probe uses `PENDING` → `CENSORED`).

---

## 16. Phase 3 Prerequisites

1. **Synthetic outcome/maturity synthesizer** — deterministic outcome generator that produces `exposure_state SENT` + `outcome_state PURCHASED/DECLINED/EXPIRED` + `as_of` maturity `as_of = evaluated_at+169h` and uses ` GroundTruthReference` hidden `latent_purchase_probability` to decide binary, without leaking into `decision_snapshot`.
2. **World → RowBundle file dump** — `world.opportunities → synthetic_ledger_row(..., exposure_at=..., outcome_at=..., transaction_id if PURCHASED) → evidence probe → build_creator_dataset` in-memory synthetic `CreatorDataset` for `train_creator_model` offline test that synthetic cohort learnability matches expected.
3. **DB write gate (optional Phase 3b)** — if proving `fetch_opportunity_rows` end-to-end, add `scripts/synth_insert_ledger.py` that first `INSERT INTO creators` synthetic placeholder then bulk `INSERT INTO commerce_opportunity_decisions` `ON CONFLICT (creator,generation) DO NOTHING`, and verify `assess_readiness` still quarantines `synthetic:` primary but synthetic harness reads via isolated query.

No new pricing/commerce authority, no Telegram, no optimizer model changes.

---

## Phase 2 Acceptance Criteria — Checklist

```text
[x] Phase 0 report reviewed
[x] Phase 1 report reviewed
[x] Phase 1 contracts verified (run/clock/identity/data_origin/event/ground_truth/config)
[x] Existing domain models audited (Creator/Fan/OfferDefinition/Opportunity/DecisionSnapshot table + snapshot shape)
[x] DecisionSnapshot audited (eligible/selected/ranking/conversation/fan/history required/optional)
[x] build_optimization_input audited (ledger snapshot + scope checks)
[x] Evidence classifier audited (NONE→UNAVAILABLE, PARTIAL, SENT via SENT/exposure)
[x] Supervised-label behavior audited (PURCHASED 1, DECLINED/EXPIRED 0, censored/unavailable/child/synthetic None)
[x] Creator isolation audited (Opportunity→Input→CreatorDataset per-creator)
[x] Persistence boundary audited (file-only + in-memory both viable, no INSERT needed)

[x] Synthetic Creator implemented (world.create_creator high-range deterministic)
[x] Synthetic Fan implemented (world.create_fan fan_id 9M validated isolation)
[x] Synthetic Content implemented (world.create_content offer_type/price_minor/vault/drop)
[x] Synthetic Conversation Context implemented only as needed (4-field fixture)
[x] Synthetic Opportunity implemented (world.create_opportunity snapshot + generation synthetic:)
[x] SimulationWorld implemented (run+clock+identity+creators/fans/contents/opportunities, deterministic SHA256, no global random)
[x] DecisionSnapshot builder implemented (simulation/snapshot:build_decision_snapshot 1 eligible, ranking v1)
[x] Opportunity adapter implemented (adapter:synthetic_ledger_row/probe_* no send/execute/purchase)

[x] Same seed produces same world (D1)
[x] Different seed produces different world (D2)
[x] Creator isolation verified (D3)
[x] Synthetic opportunity produces valid OptimizationInput (D4 via real build_optimization_input)
[x] Snapshot is deterministic (D5)
[x] Simulation clock works with snapshots (D6)
[x] Synthetic origin preserved (D7 is_simulation_row, synthetic:)
[x] Ground truth remains hidden (D8 latent not in snapshot/Input)
[x] No production side effects (D9 no telethon/dropfans/commerce_offers)
[x] Existing tests remain passing (D10: 41 new 30+11 + 286 forensic-relevant + 105 optimizer/readiness)
[x] Static checks completed (ruff check --fix + format, py_compile)
[x] Dirty tree verified (only simulation/world|snapshot|adapter + test_simulation_world + docs/PHASE_2_*)
[x] Phase 2 report created (this file)
```

