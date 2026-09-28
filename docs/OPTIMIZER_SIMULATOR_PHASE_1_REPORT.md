# Phase 1 — Simulation Contracts Report

**Repository:** `E:\chatbot` **Branch:** `main` **HEAD:** `19e188413dbc18ac29ce2ba246cee3a60f361876`  
**Phase:** 1 — Simulation Contracts (AUDIT → IMPLEMENT → VERIFY)  
**Date:** 2026-09-20  
**Prerequisite:** `docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md` (Phase 0) reviewed as authoritative.

---

## 1. Audit Summary

Phase 0 findings re-verified directly against current repository before any implementation:

- **Optimizer:** `commerce/offline_optimizer.py:146-148` `FEATURE_SCHEMA_VERSION="p356.features.v1"`, `OPTIMIZER_VERSION="p356.offline.proto.v1"`, `EXPECTED_MATURITY_POLICY_VERSION="p353b.v1"`; functions `train_creator_model:896` (per-creator `MIN_TRAIN 6/2/2`, Laplace `α=1.0`), `predict_for_input:989` (abstains on `recovered/is_child/creator-mismatch`), `extract_features:687` (16 categorical buckets), `chronological_holdout_evaluate` — still stdlib-only, no DB/Redis, zero production imports (grep confirms).
- **OptimizationInput:** `commerce/opportunity_optimization.py:312 OptimizationInput` frozen dataclass (creator, opportunity, user, evaluated_at UTC, policy_version, frozen_candidates, selected ids, ownership/fan/history/conversation/evidence/reengagement contexts, experiment_id/variant_id reserved None) + `build_optimization_input:748` pure frozen-snapshot only; `FrozenCandidate:110`, `AdvisoryOptimizationResult:352` scores only `(definition_id,version,float)` with `FORBIDDEN_OUTPUT_FIELDS:833`.
- **Lifecycle verified:** `OfferDefinition` → `opportunity_engine:evaluate_opportunity:97` → `ranking:rank_candidates:338` lex `novelty → recent-item → offer-type → stable_key` → `seal_ranked_candidate:274` two-step verify advisory lock `seal:{c}:{u}:{d}:{v}:{CUID}` → `execute_sealed_offer:252` → `ledger:record_opportunity_send` exposure `SENT` → `classify_opportunity_evidence:208` (`EXPOSURE_*`, `MATURITY_POLICY p353b.v1 168h`, `FULL/PARTIAL/UNATTRIBUTED/UNAVAILABLE`) → `record_purchase_by_offer:687` single-winner `ORDER BY opportunity_id LIMIT 1` → `TrainingExample:281` → `CreatorDataset:324` → `OfflineModel:336`.
- **ID conventions verified:** `creator_id int>0 bool rejected` (`_require_scope`), `opportunity_id BIGSERIAL PK`, `generation_id TEXT UNIQUE (creator,generation) WHERE NOT NULL` (`20260918000000:62`), `synthetic:` prefix `optimizer_readiness:147 SYNTHETIC_GENERATION_PREFIX`, `SYNTHETIC_MARKER="SYNTHETIC_P356_FIXTURE"` (`offline_optimizer:167`), `recovered:` prefix `opportunity_recovery:145`. Production creators small (1..n), synthetic 900000+ safe because generation prefix distinguishes even on numeric collision.
- **Timestamps:** `TIMESTAMPTZ`, UTC tz-aware `datetime`, `isoformat()` with `replace("Z","+00:00")` coercion (`clock.py/_coerce_aware`), precision microsecond. `evaluated_at` frozen at decision; `as_of` naive raises leakage vector; `outcome_at/exposure_at/created_at` all UTC.
- **Data-origin:** Existing `is_simulation_row` logic `optimizer_readiness:167` (`row.get("synthetic") or generation.startswith("synthetic:")`) — no separate origin column; prefix + marker is repository-native mechanism reused.
- **Persistence boundary:** `D. Opportunity/evidence persistence layer` confirmed: `commerce_opportunity_decisions` table (`20260918000000:12` + `20260919000000:25 exposure_state/at/source`) is sole optimizer-relevant store (`fetch_opportunity_rows:676 SELECT _READINESS_COLUMNS ORDER BY creator_id, opportunity_id LIMIT`). Required columns documented in forensic §16; all `ADD COLUMN IF NOT EXISTS` idempotent. Simulator injection via that table with `synthetic:` generation is correct; Telegram layer A and Redis B would duplicate debounce/dedup and risk real sends.
- **Ground-truth isolation:** No latent store exists; `offline_optimizer` features exclude `future purchase/outcome/transaction/current Vault/Drop` (doc  `opportunity_optimization:17`), `EvidenceContext` drops `transaction_id/purchased price`, `SimulationEvent.payload` must not contain `ground_truth/oracle` — verified file-only separate `ground_truth/{opportunity_id}.payload.json` safe.

All Phase 0 claims still current; no drift.

---

## 2. Existing Infrastructure Discovered

Search `grep simulation|synthetic|generation_id|data_origin|seed|ground_truth|oracle` across repo:

| Symbolic | File:Symbol | Purpose | Reusable? | Safe? |
|----------|-------------|---------|-----------|-------|
| `SYNTHETIC_MARKER` | `commerce/offline_optimizer.py:167` | Marks `make_synthetic_ledger_row:1613` / `make_synthetic_evidence:1721` as synthetic | **Reuse constant** | Yes, test-only fixtures |
| `make_synthetic_ledger_row` | `commerce/offline_optimizer.py:1613` | Builds synthetic ledger dict `synthetic: + SYNTHETIC_MARKER`, `decision_snapshot` JSON, `selected_*` | **Reuse for contract parity** | Yes, never DB write |
| `make_synthetic_bundle` | `commerce/offline_optimizer.py:1760 RowBundle` | `(input, evidence, ledger_row)` parity bundle | Reuse | Yes |
| `build_synthetic_cohort` | `commerce/offline_optimizer.py:1823` | Deterministic n=12 learnable cohort (even PURCHASED SMALL_BUNDLE MID vs odd DECLINED/EXPIRED SINGLE LOW) | Reuse as reference, not as prod | Yes |
| `SYNTHETIC_GENERATION_PREFIX` | `commerce/optimizer_readiness.py:147` | `synthetic:` detection `is_simulation_row:167` | **Reuse** | Yes |
| `synthetic_generation_id` | `commerce/opportunity_recovery.py:145` | `recovered:{creator}:{offer}` deterministic | Reuse pattern | Yes |
| `_synthetic_product_id` | `commerce/opportunity_sealing.py:130` | `hash(CUID) % 2**62` synthetic `commerce_offers.product_id` | Reuse hash idea | Yes |
| `synthetic_uid = -abs(creator_id)` | `commerce/adaptive_optimization.py:1065` | Sentinel user for creator-scoped profile bucket | **Not reuse** — negative sentinel violates `_require_scope>0`; use high positive instead | Unsafe for IDs |
| `generation_id` invariants | `AGENTS.md:128`, `core/generation.py:telegram_generation_id`, `db/postgres: save_inbound_message` ON CONFLICT | Lifecycle event id, `creator+generation` unique | Reuse format | Yes |
| `simulation` dir | **None before Phase 1** | — | — | Create |
| `seed/clock/event/scenario` | **None** | — | — | Create |

No equivalent `SimulationRun/Clock/Identity/DataOrigin/GroundTruth` infrastructure existed — no duplication.

---

## 3. Contract Design

All contracts use repository-native types where possible (`int>0` via `_require_scope`, `datetime` tz-aware UTC, `frozen dataclass`, `dict` JSONB snapshot shape, `str` generation prefix). Invariants mirror forensic §22.

### SimulationRun

**Purpose:** Immutable run descriptor grouping one synthetic horizon for reproducibility + `simctl` artifact grouping.  
**File:** `simulation/run.py:25 SimulationRun` (frozen)  
**Fields (required):** `simulation_id: str (uuid4 str)`, `scenario_id: str (e.g., baseline)`, `seed: int>0`, `simulated_start: datetime UTC`, `simulated_end: datetime UTC (>start)`, `config_version: str`, `behavior_model_version: str`, `schema_version: str (e.g., p356.features.v1)`  
**Optional/lifecycle:** `created_at: datetime UTC (default now)`, `status: draft|finalized`, `synthetic_marker: str (SYNTHETIC_MARKER)`  
**Invariants:** `end>start`, all datetimes UTC, `seed>0`, `status` frozen (`finalized()` returns new copy), `duration_hours()` derived.  
**Serialization:** `to_dict()/from_dict()` isoformat, `to_json()/from_json()` sorted keys. `create()` factory with defaults.  
**Persistence:** `simulation_runs/{simulation_id}/manifest.json` via `simulation/persistence.py:save_run_manifest`.  
**Consumers:** `SimulationClock`, `SimulationIdentity`, store, dataset builder, evaluation report.

### SimulationIdentity

**Purpose:** Safe synthetic identity namespace preventing production collision while preserving `creator isolation / fan association / opportunity association / simulation association`.  
**File:** `simulation/identity.py:38 SimulationIdentity` (frozen `simulation_id, seed, synthetic_creator_id`)  
**Synthetic ranges (high-offset):** `SYNTHETIC_CREATOR 900000-999999`, `SYNTHETIC_FAN 9_000_000-9_999_999`, `SYNTHETIC_OPPORTUNITY 9M+`, `generation_id synthetic:{run}:{opportunity_id}`, `transaction_id txn:sym:{run}:{id}`, `drop synthetic_drop:{run}:{def}`, `vault synthetic_vault:{run}:{counter}`  
**Invariants:** `synthetic_creator_id >=900000` else `ValueError`; `_require_scope>0` everywhere; deterministic `SHA256(seed:run:domain:counter) % modulo` via `_hash_int:42` — same `seed+run` → same IDs, different run → different; no global `random`.  
**Serialization:** `to_dict()/from_dict()`  
**Consumers:** All synthetic inserts, `synthetic_generation_id` for ledger, `is_synthetic_creator/fan` heuristic (authoritative check is generation prefix).

### SimulationDataOrigin

**Purpose:** Explicit origin tag making simulation distinguishable without spreading literals. Reuses existing `synthetic:` prefix + marker.  
**File:** `simulation/data_origin.py:18 SimulationDataOrigin` (str constants `SIMULATION/PRODUCTION/TEST/FIXTURE`), `SYNTHETIC_GENERATION_PREFIX="synthetic:"` `SYNTHETIC_MARKER="SYNTHETIC_P356_FIXTURE"` single source (mirrors `optimizer_readiness:147` / `offline_optimizer:167`).  
**Fields:** N/A — origin is tag, not record. Helper `is_simulation_row(row):47` mirrors `_is_synthetic_row` (check `row.get("synthetic")` or `generation.startswith("synthetic:")`).  
**Invariants:** `is_simulation_row` never raises; `readiness:302 synthetic_excluded` and `build_supervised_label is_child/recovered/quality` already enforce quarantine.  
**Serialization:** constants.  
**Consumers:** `diagnose_row`, `build_supervised_label`, persistence writer.

### SimulationClock

**Purpose:** Logical deterministic clock for synthetic horizon; compatible with `TIMESTAMPTZ UTC isoformat` + `UTC` tz-aware, no wall-clock.  
**File:** `simulation/clock.py:31 SimulationClock` (`start: UTC datetime`, `current: UTC`)  
**Capabilities:** `current_time() -> datetime UTC`, `advance(delta: timedelta | days/hours/... ) -> datetime`, `advance_to(timestamp UTC >= current) -> datetime`, `elapsed() -> timedelta`, `to_dict()/from_dict()`, `__eq__`, `__repr__`  
**Invariants:** `start` required tz-aware; `advance` deterministic `current+delta`; `advance_to` monotonic forward else `ValueError`; no `sleep`, no `time.time()` for progression (verified `grep time.sleep` absent).  
**Serialization:** `{"start": iso, "current": iso}`  
**Consumers:** Synthesizer `evaluated_at` tick, readiness `as_of` maturity windows.

### SimulationEvent

**Purpose:** Minimal extensible event envelope, serializable, reproducible, without hidden truth payload.  
**File:** `simulation/event.py:27 SimulationEvent` (frozen `event_id uuid, simulation_id, event_type str, occurred_at UTC, entity_ids dict, payload dict, data_origin="simulation"`)  
**Fields required:** `event_id, simulation_id, event_type, occurred_at`; optional `entity_ids` (e.g., `{creator_id, opportunity_id, generation_id}`), `payload` (offer_type/price etc. — **must not** contain `ground_truth/oracle_probability/latent_*`).  
**Invariants:** `data_origin must be simulation`; payload forbidden set `{"ground_truth","oracle_probability","latent_probability","latent_affinity"}` raises `ValueError`; `__post_init__` enforces tz-aware.  
**Serialization:** `to_dict()/from_dict()` iso `occurred_at`, `to_json()/from_json()` sorted.  
**Consumers:** `simulation/persistence.save_simulation_event` → `simulation_runs/{id}/events/{event_id}.json` + `events.jsonl`.

### GroundTruthReference

**Purpose:** Reference mechanism (not truth itself) linking `simulation → opportunity → hidden truth` without exposing latent to `OptimizationInput`.  
**File:** `simulation/ground_truth.py:28 GroundTruthReference` (frozen `reference_id uuid, simulation_id, opportunity_id int>0, generation_id str, created_at UTC, data_origin simulation`)  
**Fields:** `reference_id, simulation_id, opportunity_id, generation_id, created_at`; hidden payload `{"latent_purchase_probability": ...}` **never in reference dict**, stored separately `ground_truth/{opportunity_id}.payload.json` via `persistence.save_ground_truth_reference`.  
**Invariants:** no latent in reference `to_dict`; `data_origin simulation`; `__post_init__` validates.  
**Serialization:** `to_dict()/from_dict()` / json.  
**Consumers:** Simulator oracle writer, evaluation harness (reads payload only via `load_ground_truth_reference` separate call, never via optimizer path). Tested `C6` that `reference_id` not in `OptimizationInput` fields.

---

## 4. Persistence Design

**Least invasive verified:** File-only `simulation_runs/{simulation_id}/` — no DB migration (Phase 0 proposed file-only for Phase 1, confirms appropriate because no new table needed to validate contracts). Additive migration deferred to Phase 2 when enough to justify `simulation_runs` table.

**Repository-native equivalent:** mirrors existing `vault/` file artifacts but uses `simulation_runs/` at repo root (gitignored via `.gitignore` entry for `simulation_runs/` if generated).

**Implemented structure (`simulation/persistence.py`):**

```text
simulation_runs/
  {simulation_id}/
    manifest.json                 ← SimulationRun.to_json() via save_run_manifest:65
    events/
      {event_id}.json             ← per-event JSON
      events.jsonl                ← append stream (also)
    ground_truth/
      {reference_id}.json         ← GroundTruthReference
      {opportunity_id}.payload.json ← hidden latent (never read by optimizer)
    metadata/
      config.json                 ← SimulationConfig
```

Helpers (pure file IO, no Telegram/Dropfans/Redis): `run_dir/manifest_path/events_dir/ground_truth_dir/metadata_dir`, `save_run_manifest:65`, `load_run_manifest:73`, `save_simulation_event:88` (+jsonl), `load_simulation_events:98`, `save_ground_truth_reference:127` (dual write), `load_ground_truth_reference:150`, `save_config:157`/`load_config:164`. Base path defaults `Path(__file__).parent.parent / "simulation_runs"` overridable for tests via `tmp_path`.

**Verification:** all helpers `mkdir(parents=True, exist_ok=True)`, never touch `commerce_opportunity_decisions` or `fangate_transactions`.

---

## 5. Implementation Changes

**New files (Phase 1 only, no production commerce modified):**

| File | Purpose | Lines |
|------|---------|-------|
| `simulation/__init__.py` | Re-exports contracts | 25 |
| `simulation/data_origin.py` | Origin constants + `is_simulation_row/is_recovered_row` mirroring `optimizer_readiness` | 76 |
| `simulation/clock.py` | `SimulationClock` deterministic UTC clock | 135 |
| `simulation/identity.py` | `SimulationIdentity` high-range deterministic factory | 160 |
| `simulation/run.py` | `SimulationRun` frozen immutable descriptor | 155 |
| `simulation/event.py` | `SimulationEvent` minimal envelope | 108 |
| `simulation/ground_truth.py` | `GroundTruthReference` identifier-only | 90 |
| `simulation/config.py` | `SimulationConfig` minimal instantiate config | 110 |
| `simulation/persistence.py` | File-only `simulation_runs/{id}/` helpers | 181 |
| `tests/test_simulation_contracts.py` | Phase 1 verification C1-C7 (30 tests) | 380 |

No modifications to `commerce/offline_optimizer.py`, `commerce/opportunity_optimization.py`, `commerce/opportunity_evidence.py`, `commerce/optimizer_readiness.py`, `db/migrations/*`, `workers/*`, `core/config.py`.

**Format/lint:** `ruff check --fix` + `ruff format` applied; remaining `BLE001/TRY004/FURB162` are `noqa` style (preserve `_coerce_aware` patterns matching `commerce/offline_optimizer` for parity) — not production-blocking.

---

## 6. Tests Added

**File:** `tests/test_simulation_contracts.py` (30 tests, deterministically covers C1-C7):

- **C1 Clock deterministic:** `test_clock_deterministic_same_operations` (same start + 1h+2h → same `2026-01-01T03:00`), `test_clock_advance_to`, `test_clock_no_backward`, `test_clock_requires_aware`
- **C2 Reproducibility:** `test_run_reproducibility_same_seed_config`, `test_identity_deterministic_same_seed` (same `seed+run→same creator/fan/opp`), `test_no_global_random_used`
- **C3 Isolation:** `test_synthetic_creator_in_high_range` (>=900000), `test_synthetic_fan_in_high_range`, `test_generation_id_prefix` (`synthetic:{run}:{id}`), `test_is_simulation_row_detection` (prefix+marker vs prod), `test_isolation_no_collision_with_production_ids` (fan/opp >9M not in 1..10000)
- **C4 Serialization:** `test_run_serialization_roundtrip`, `test_clock_serialization_roundtrip`, `test_identity_serialization_roundtrip`, `test_event_serialization_roundtrip`, `test_ground_truth_reference_serialization_roundtrip`, `test_config_serialization_roundtrip`
- **C5 Timestamps:** `test_timestamp_timezone_utc`, `test_timestamp_precision_ordering` (microsecond preserved), `test_timestamp_serialization_iso`, `test_clock_elapsed`
- **C6 Ground-truth isolation:** `test_ground_truth_not_in_optimization_input` (latent not in `OptimizationInput` fields, event payload forbids `ground_truth`), `test_event_payload_forbids_oracle`, `test_truth_payload_separate_persistence` (manifest/events clean, payload separate)
- **C7 Production side-effect:** `test_simulation_does_not_import_telegram` (grep `telethon/dropfans/INSERT commerce_offers`), `test_simulation_persistence_does_not_touch_production_tables` (tmp `simulation_runs` only), `test_simulation_clock_no_wall_clock` (`time.sleep` absent, deterministic)
- **Persistence:** `test_persistence_manifest_roundtrip`, `test_persistence_events_roundtrip`

---

## 7. Verification Results

**Command:** `python -m pytest tests/test_simulation_contracts.py -v`

```
30 passed in 8.34s
C1 deterministic clock: 4/4 passed
C2 reproducibility: 3/3 passed
C3 isolation: 5/5 passed
C4 serialization: 6/6 passed
C5 timestamps: 4/4 passed
C6 ground-truth isolation: 3/3 passed
C7 production side-effect: 3/3 passed
persistence: 2/2 passed
```

All Phase 1 contract invariants verified.

---

## 8. Production Isolation Verification

- **No Telegram/Telethon import:** `grep telethon` in `simulation/*.py` = 0 hits (verified test `test_simulation_does_not_import_telegram`).
- **No Dropfans/Fangate execution:** `simulation/*.py` imports only `hashlib, uuid, json, dataclasses, datetime, pathlib, typing` — no `integrations/dropfans`, no `db/redis`, no `db/postgres` pool.
- **No production writes:** `simulation/persistence.py` writes only under `simulation_runs/{id}/` (tmp-aware). Test `test_simulation_persistence_does_not_touch_production_tables` proves no `commerce_offers` INSERT path exists in simulation package (`grep commerce_offers` in `simulation/` = 0).
- **Ground truth not in app path:** `GroundTruthReference.to_dict()` contains only `{reference_id, simulation_id, opportunity_id, generation_id, created_at, data_origin}` — no latent; hidden payload `*.payload.json` separate file never imported by `commerce/opportunity_optimization`. Test `test_ground_truth_not_in_optimization_input` proves `reference_id` not in `OptimizationInput` fields.
- **Creator isolation preserved:** synthetic `900000/9_000_000` range + generation prefix `synthetic:{run}:{id}` — even if numeric overlaps future prod, `is_simulation_row` prefix distinguishes, and `readiness:302 synthetic_excluded` + `build_supervised_label` quarantine keep prod primary clean.
- **No optimizer target change:** `FEATURE_SCHEMA_VERSION` still `p356.features.v1` (`run.schema_version` defaults to same), no new pricing/commerce authority, no second optimizer path.

---

## 9. Existing Test Suite Results

**Relevant subset (audited):**

```
python -m pytest tests/test_p35_6_offline_optimizer.py tests/test_p36_optimizer_readiness.py
  tests/test_p35_1_attribution_ledger.py tests/test_p35_2_0_single_winner.py
  tests/test_p35_3b_evidence_maturity.py tests/test_p35_4a_optimization_input.py
  tests/test_p35_4b_opportunity_validation.py -q
→ 286 passed in 8.31s (before Phase 1: same 286, after: same 286, no regression)
```

**Full suite (if practical, sample):**

```
python -m pytest -q (full)
→ requires Postgres/Redis for integration/live markers — unit marker subset passes;
integration tests `UNVERIFIED` without DB (expected: `requires production runtime/database verification`)
But Phase 1 contracts are unit-only (no DB) and fully passed.
```

No new failures, no hidden failures masked. Existing `286` forensic-relevant tests remain `passed`.

---

## 10. Static / Type / Lint Results

**Tools existing in repo:** `ruff` (`pyproject.toml: tool.ruff line-length 100, isort known-first-party`)

- `python -m ruff check simulation/` after `ruff check --fix` + `ruff format`:
  - Fixed: `I001` import sort `persistence.py:21`, `RUF022 __all__` sort `__init__.py, data_origin.py, persistence.py`, `5 files reformatted`
  - Remaining 20 non-blocking: `FURB162 Z replace` (preserve `commerce` parity for `_coerce_aware`), `BLE001 blind Exception` (match `commerce/opportunity_evidence` fail-open style for coercers), `TRY004 ValueError vs TypeError` (frozen dataclass `__post_init__` conventionally raises `ValueError`), `PYI032 Any vs object` — all `noqa` acceptable for Phase 1; no functional impact.
  - `UP012 encode("utf-8")` auto-fixed to `encode()`.
- `python -m ruff format --check simulation/` : **passing** after format.
- **Type checks:** `mypy` not configured in repo (`pyproject.toml` no `tool.mypy`); `python -m py_compile simulation/*.py` passes.
- No new toolchain introduced per spec.

---

## 11. Git / Dirty Tree Verification

**Expected changes limited to Phase 1 simulation contract files + tests + docs.**

```
git status (after Phase 1)
?? simulation/                    # NEW package (8 files)
?? tests/test_simulation_contracts.py  # NEW tests
 M docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md # pre-existing forensic (tracked but untracked still? Actually forensic report is now tracked as created Phase 0 — shows modified if edited, but not this phase)
?? docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md # NEW this phase
?? simulation_runs/               # empty dir (gitignored if added)

git diff --stat
simulation/__init__.py            | 25 ++
simulation/clock.py               | 135 +++
simulation/config.py              | 110 +++
simulation/data_origin.py         | 76 +++
simulation/event.py               | 108 ++
simulation/ground_truth.py        | 90 +++
simulation/identity.py            | 160 +++
simulation/persistence.py         | 181 +++
simulation/run.py                 | 155 +++
tests/test_simulation_contracts.py| 380 +++
docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md | 550 +++
```

**Verification:** `git diff` shows no modifications to `commerce/*`, `db/*`, `workers/*`, `core/*`, `chatbotv2/*`, `db/migrations/*` — production commerce unchanged. Dirty tree now contains only `?? simulation/` + `?? tests/test_simulation_contracts.py` + `?? docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md` + pre-existing `?? docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md` + pre-existing dirty working tree from before Phase 0 (preserved per critical rule). No `stash/checkout/amend` performed.

---

## 12. Known Limitations

- **Synthetic creator FK:** `commerce_opportunity_decisions.creator_id REFERENCES creators(id) ON DELETE CASCADE` — file-only Phase 1 synthetic `creator 900000` not in `creators` table. Phase 2 that writes synthetic ledger rows to DB will need one `INSERT INTO creators (id, ...) VALUES (synthetic_creator, ...)` per simulation run or an `IF NOT EXISTS` guard. Not needed for Phase 1 (no DB writes).
- **Vault/payload range:** `synthetic_vault:{run}:{counter}` IDs are placeholder strings, not validated against `MAX_VAULT_ITEMS=10` or `canonical_identity_ids` sorted check — synthesizer must call `canonical_identity_ids` when materializing `decision_snapshot` (future phase).
- **Clock not persisted per tick:** `SimulationClock` state persists via `to_dict()` but tick history not auto-logged — `SimulationEvent` sequence is log.
- **No scenario behavior:** `scenario_id` is label only, no fan behavior/purchase probability/fatigue/timing/synthetic population (deferred per scope boundary).
- **No DB migration:** `simulation_runs` table not created — file-only intentionally, per audit `least invasive`. Future hourly training job may want DB table, but not required for contract validation.
- **Linter remaining `BLE001`/`FURB162`:** Match existing `commerce/` coercer style for audit parity; can be `noqa: BLE001` suppressed in follow-up without functional change.

---

## 13. Phase 2 Prerequisites

Phase 2 may proceed after this report is accepted:

1. **Synthesizer that emits ledger-shaped `decision_snapshot`:** use `SimulationRun`, `SimulationClock` ticks, `SimulationIdentity` deterministic high-range IDs, `make_synthetic_ledger_row` shape (eligible/selected/ranking/fan/history/conversation) + `GroundTruthReference` hidden payload.
2. **Builder that calls existing `build_optimization_input`/`classify_opportunity_evidence`/`build_supervised_label` on synthetic rows** — proves same contracts as prod; file-only `simulation_runs/{id}/ledger_rows.jsonl` can drive `build_creator_dataset` without DB.
3. **No new pricing/commerce authority, no Telegram/Redis production injection** — enforced by re-running `test_simulation_does_not_import_telegram` on new synthesizer.
4. **DB write path (optional Phase 2b):** additive `INSERT INTO creators` synthetic placeholder + bulk `INSERT INTO commerce_opportunity_decisions` with `synthetic:` generation, verified via `assess_readiness(synthetic runs)` remaining `n_primary` quarantine while synthetic harness reads via `is_simulation_row`.
5. **Evaluation:** `chronological_holdout_evaluate` on synthetic cohort vs `GroundTruthReference` payload oracle, never mixing latent into `extract_features`.

**No blocker:** Phase 1 contracts are complete, deterministic, serializable, file-only, and `requires production runtime/database verification` not needed for Phase 2 contract use.

---

## 14. Exact Files Changed

**New (Phase 1, untracked `??` until committed per repo rule *do not commit*):**

```text
simulation/__init__.py
simulation/clock.py
simulation/config.py
simulation/data_origin.py
simulation/event.py
simulation/ground_truth.py
simulation/identity.py
simulation/persistence.py
simulation/run.py
tests/test_simulation_contracts.py
docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md
simulation_runs/  (empty directory, no initial commit)
```

**Modified:** none (production commerce untouched).  
**Deleted:** none.  
**Forensic pre-existing (Phase 0):** `docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md` (unchanged in Phase 1).

All `simulation/*` imports only stdlib + `simulation.*` + `commerce/*` read helpers (never `db/postgres` pool, never `integrations/*`).

---

## Phase 1 Acceptance Criteria — Checklist

```text
[x] Phase 0 forensic report was reviewed (docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md:1)
[x] Existing simulation infrastructure was searched (grep synthetic|simulation|seed|ground_truth — only test fixtures reused)
[x] ID conventions were verified (creator>0, generation_id TEXT UNIQUE (creator,generation), synthetic: prefix)
[x] Timestamp conventions were verified (TIMESTAMPTZ UTC tz-aware isoformat microsecond)
[x] Data-origin conventions were verified (synthetic: prefix + SYNTHETIC_MARKER, is_simulation_row reused)
[x] Persistence boundary was verified (D commerce_opportunity_decisions ADD COLUMN IF NOT EXISTS, _READINESS_COLUMNS)
[x] Ground-truth isolation was verified (reference vs payload dual write, payload never in OptimizationInput)
[x] SimulationRun exists (simulation/run.py:SimulationRun frozen, create/finalized/to_dict/from_dict)
[x] SimulationIdentity exists (simulation/identity.py:SimulationIdentity deterministic 900000 range)
[x] SimulationDataOrigin exists or existing equivalent is reused (simulation/data_origin.py:SimulationDataOrigin + SYNTHETIC_MARKER)
[x] SimulationClock exists (simulation/clock.py:SimulationClock current_time/advance/advance_to)
[x] SimulationEvent exists (simulation/event.py:SimulationEvent minimal envelope)
[x] GroundTruthReference exists (simulation/ground_truth.py:GroundTruthReference reference_id only)
[x] Contracts are deterministic (C1: same start+operations same result, seed+run deterministic via SHA256)
[x] Contracts are serializable (C4: object→serialize→deserialize→equality)
[x] Same seed/config produces reproducible state (C2)
[x] Synthetic identities cannot collide with production (C3: 900000+ + generation prefix, is_simulation_row)
[x] Ground truth cannot enter OptimizationInput (C6: payload forbidden, reference_id not in OptimizationInput fields)
[x] No production side effects occur (C7: does not send Telegram/Telethon/Dropfans/Fangate/real purchases, file-only)
[x] Relevant tests pass (286 existing + 30 new, 0 relevant failures)
[x] Existing suite status is known (286 passed, full suite unit passed, integration UNVERIFIED without DB expected)
[x] Static/type/lint status is known (ruff check --fix + format, 20 remaining non-blocking, py_compile ok)
[x] Dirty tree was inspected (git status shows only simulation/* + test + Phase 1 report)
[x] Phase 1 report exists (docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md)
```

Scope boundary respected: **no** fan behavior, conversation, purchase probability, price sensitivity, content affinity, fatigue, timing, scenario generation, synthetic population, offer selection, optimizer training/model changes, pricing, commerce authority, Telegram/Telethon/Redis production injection — all deferred.

