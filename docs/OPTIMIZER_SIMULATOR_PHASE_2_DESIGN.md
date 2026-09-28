# Phase 2 — Synthetic World Foundation Design

**Prereq:** `docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md`, `docs/OPTIMIZER_SIMULATOR_PHASE_1_REPORT.md`, `simulation/*` Phase 1 contracts.

## Goal

Minimal synthetic world that can produce a deterministic opportunity-shaped record compatible with existing `build_optimization_input`, `classify_opportunity_evidence`, `build_supervised_label` without invoking production commerce.

## Entities

### SyntheticCreator
- `creator_id: int (synthetic high range 900000+)`
- `simulation_id: str`
- `data_origin: simulation`
- Isolation: creator is the grouping key for `CreatorDataset`; no pooled.

No behavior, exists to enforce isolation.

### SyntheticFan
- `fan_id / user_id: int (9M+ range)`
- `creator_id: int` (parent creator)
- `simulation_id: str`
- `timezone: UTC` (all timestamps UTC)
- Observable state derived deterministically for fan snapshot: `purchase_count`, `total_spend_minor`, `recent_offer_count` etc. — but Phase 2 observable representation is **empty/zero defaults** plus optional deterministic fixture; latent behavioral state not yet.

Phase 2 fan = minimal observable container; latent purchase probability not added.

### SyntheticContent
- `content_id: str` (synthetic_vault:{run}:{counter})
- `creator_id: int`
- `content_type: enum reuse OfferType {SINGLE, SMALL_BUNDLE, CORE_BUNDLE, PREMIUM} or Vault item shape`
- `vault_ids: tuple[str]` 1..2 ids per content for commerce
- `price_bucket: derived from price_minor` — optimizer operates on price bucket, not raw price alone; synthetic content carries `price_minor int` (frozen fact) e.g., 499..1999.
- `mapped_drop_ids: tuple[str]` single synthetic_drop CUID
- `simulation_id, data_origin`

Optimizer operates on content/product via `offer_type + price_bucket + vault_count_bucket + drop_mapping` — so synthetic content must represent those. Reuse repository enum `offer_type` and bucket thresholds from `offline_optimizer:244 _price_bucket`.

No parallel taxonomy.

### SyntheticConversationContext
- Observable structure required by `decision_snapshot.conversation`: `{lifecycle, current_topic, recent_topics, open_threads}`
- Deterministic fixture: lifecycle `established|new`, current_topic `None|str`, recent_topics `()`, open_threads `()`
- No random messages, no LLM generation.

### SyntheticOpportunity
- `opportunity_id: int (synthetic 9M+)`
- `creator_id: int`
- `fan_id: int`
- `content_id: str`
- `evaluated_at: datetime UTC` (from SimulationClock)
- `generation_id: str synthetic:{run}:{opportunity_id}`
- `decision_snapshot: dict` shaped for `build_optimization_input` (eligible/selected/ranking/fan/history/conversation)
- `data_origin: simulation`
- `opportunity metadata` optional: `policy_version v1`

Not sent/executed; just shaped.

### SimulationWorld (container)

```python
world = SimulationWorld(run: SimulationRun)  # creates clock = SimulationClock(run.simulated_start), identity = SimulationIdentity(run.simulation_id, run.seed)
creator = world.create_creator()  # deterministic synthetic_creator_id via identity
fan = world.create_fan(creator)  # deterministic synthetic_fan_id
content = world.create_content(creator, vault_ids=..., price_minor=..., offer_type=...)
opportunity = world.create_opportunity(creator, fan, content)  # evaluated_at = clock.current_time(), snapshot via builder, generation_id synthetic
```

Determinism: every create_* uses `SimulationIdentity._hash_int(seed, simulation_id, domain, counter)` without global random.

## Decision Snapshot Contract

Required for `build_optimization_input` to succeed (pure, no DB):

**Minimum synthetic fields:**
- `eligible: list[{"definition_id": int>0, "version": int>=1, "stable_key": str non-empty, "offer_type": str, "price_minor": int|None, "currency": str|None, "vault_ids" or "canonical_vault_item_ids": list[str], "mapped_drop_ids": list[str]}]` — at least 1 entry
- `selected`: same shape as one eligible (or ledger fallback `selected_definition_id/version`)
- `ranking: {"policy_version": "v1", "ranked_order": [definition_id], "factors": {}}` — policy_version carried to OptimizationInput.policy_version
- `conversation: {"lifecycle": str|None, "current_topic": str|None, "recent_topics": tuple, "open_threads": tuple}` — 4 fields only
- `fan: {"creator_id", "user_id", "purchase_count": int, "total_spend_minor": int, "purchased_vault_ids": list, "recent_offer_count": int, "recent_rejected_offer_count": int}` — missing keys default 0, but scope mismatch raises if present
- `history: {"creator_id", "user_id", "total_offer_count": int, "has_active_offer": bool, "declined_offer_count": int}` — defaults
- `evaluated_at` on ledger row side (outside snapshot) must match snapshot fan/history scope `creator_id/user_id`.

**Optional/derived:** `family_id` (omit → NO_FAMILY), `average_order_value_minor`, `highest_purchase_minor`, `last_purchase_at`, `recent_purchase_count`, `state_counts`, `offered_vault_sets`, etc. — default 0/None.

**Constants:** `family_presence` always NO_FAMILY due to snapshot gap (documented).

**Creator scope:** snapshot fan/history `creator_id/user_id` must equal ledger row `creator_id/user_id` else `ValueError scope mismatch`.

## Adapter

`SyntheticOpportunity -> ledger_row dict` for `build_optimization_input`:

- ledger_row: `{creator_id, opportunity_id, user_id, generation_id, evaluated_at, decision_snapshot JSON dump, selected_definition_id/version/stable_key, decision_status, sealed_offer_id (synthetic or int), reengagement_of None, outcome_state, exposure_state, etc.}`
- evidence: synthetic evidence classification dict for maturity (or None for immature probe)
- Input probe helper: `probe_optimization_input(synthetic_opp) -> OptimizationInput` calls real `build_optimization_input` and asserts no exception.

## Persistence Strategy

Phase 2 remains **file-only + in-memory** (audit A10): world generated in memory; optional dump to `simulation_runs/{run_id}/world.json` (ledger_rows list) without DB writes. File-only avoids FK `creators(id)` issue (synthetic creator not in table). In-memory `CreatorDataset` via `build_creator_dataset` can be driven from file without inserts.

Both paths: pure in-memory via `RowBundle(input, evidence, ledger_row)` same as `offline_optimizer.make_synthetic_bundle` shape, but sourced from SimulationWorld.

## Isolation

- Synthetic IDs high range + generation `synthetic:` prefix → `is_simulation_row` true, `readiness synthetic_excluded`, `build_supervised_label` quarantined.
- Creator isolation: `world.create_opportunity(creatorA, fanB)` must raise if fan.creator_id != creator.creator_id; same for content.
- No production writes: world never calls `db/postgres.get_pool` nor `integrations/dropfans` nor `db/redis`.

## Ground Truth

Phase 2 does NOT add latent truth; keeps hidden payload separate per `GroundTruthReference` file-only. Opportunity builder does not embed `true purchase probability`.

## Out of Scope

Purchase probability, behavioral states, fatigue, timing, content affinity, price sensitivity, scenario packs — Phase 3+.

## Files to Create

- `simulation/world.py` (World + Creator/Fan/Content/Opportunity)
- `simulation/snapshot.py` (DecisionSnapshotBuilder)
- `simulation/adapter.py` (SyntheticOpportunity -> ledger + probe)
- Tests `tests/test_simulation_world.py`, possibly `tests/test_simulation_snapshot.py` merged.
