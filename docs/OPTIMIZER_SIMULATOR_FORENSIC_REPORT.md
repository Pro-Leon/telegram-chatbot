# Optimizer Simulator Forensic Audit

**Repository:** `E:\chatbot` **Branch:** `main` **HEAD:** `19e188413dbc18ac29ce2ba246cee3a60f361876` *Restore recovered Phase 1-79 production LLM worker*  
**Audit phase:** Phase 0 — Forensic Architecture Audit (READ-ONLY)  
**Date:** 2026-09-20  
**Constraint:** DO NOT IMPLEMENT — inspect, trace, document, verify only. Dirty working tree preserved. Only this report is created.

---

## Executive Summary

The OFM Chatbot commerce system is at **P3.6 Production Evidence Accrual & Optimizer Readiness, OFFLINE ONLY**. The deterministic v1 commerce stack (`opportunity_engine → ranking → sealing → execution → send`) is the **sole production authority** for product/price/proposal. The optimizer is a **stdlib-only empirical log-odds baseline** (`commerce/offline_optimizer.py`) that estimates `P(mature purchase | sent exposure)` on **creator-local, chronological, mature, SENT, FULL, attributed, v1-selected** observations. It has:

- **No persistence, no loader, no background job, no production import** — `grep offline_optimizer` outside `tests/` = 0 hits, barrier test `tests/test_p36_optimizer_readiness.py:475` enforces it.
- **Point-in-time safety by construction:** every feature comes from frozen `decision_snapshot` + `as_of`-gated evidence; current catalog/provider/Vault never consulted.
- **Label semantics:** positive = `PURCHASED` (mature SENT FULL attributed with `transaction_id`), negative = `DECLINED/EXPIRED` (mature SENT FULL), everything else (`CENSORED/UNAVAILABLE/PARTIAL/RECOVERED/PROCESS_NEGATIVE/NO_SELECTION/NO_OPPORTUNITY/child/synthetic`) is **excluded from supervised training, never negative** — unselected candidates are **non-observations**, not negatives (selection-bias safe).
- **Maturity:** `p353b.v1`, `168h` (= `RECONCILIATION_WINDOW_HOURS`), single versioned place.
- **Evidence pipeline is real and wired:** ledger table `commerce_opportunity_decisions` (added by `20260918000000_p35_1`), exposure writer `record_opportunity_send`, single-winner attribution `record_purchase_by_offer`, readiness mirror `optimizer_readiness` — but **migration is untracked** in the dirty tree and production DB is empty (fixtures only). `requires production runtime/database verification` for counts.
- **No second sales authority is reachable** from `workers/llm_worker.py:process_message`; legacy `commerce/execution.py:execute_ppv` is hard-quarantined (`return None,None`).

**Simulator implication:** synthetic data must enter at the **application/domain persistence layer D (opportunity/evidence persistence)** — by inserting synthetic `commerce_opportunity_decisions` rows that satisfy the same contracts as production rows (frozen snapshot, creator-scoped, single-winner, mature, SENT) — and be marked `synthetic:` so the real readiness/optimizer excludes them from production cohorts while the simulator can consume them via `build_optimization_input` / `classify_opportunity_evidence`. Telegram/Redis layers (A/B) would duplicate debounce/dedup complexity and risk real sends; optimizer dataset layer (E) would bypass contract validation.

---

## Repository Map

**Entry points (actual):**

| Entry | File | Symbol | Notes |
|-------|------|--------|-------|
| MTProto ingress | `chatbotv2/handlers.py`, `chatbotv2/main.py` | Telethon client + debounce `debounce:creator:{cid}:user:{uid}:messages` | Produces `INBOUND_STREAM` |
| Run-all | `run_all.py` | — | Starts Postgres+Redis+workers+dashboard |
| LLM worker | `workers/llm_worker.py:1` | `process_message:737`, `generate_draft:64`, `_get_canonical_commerce_evaluation:111`, `_try_commerce_draft:271` | MTProto bot + send stream consumer `llm_workers` group |
| LLM worker main loop | `workers/llm_worker.py:4550+` | consumer group `llm_workers` on `inbound_messages`, `XAUTOCLAIM idle >30s`, `send_messages` via `send_workers` | |
| Send worker | `workers/send_worker.py` | `send_loop` | Consumes `SEND_STREAM` `send_messages`, Telegram send, confirms dedup |
| Scheduler | `workers/scheduler_worker.py` | `main` poll `scheduled_messages`, `reconcile_purchases:485 → reconcile_all` | `OfferHistory` re-engagement touches via `record_reengagement_touch` |
| Dashboard | `chatbotv2/dashboard/app.py` | `uvicorn ...:8080` | Operator inline keyboard Approve/Edit/Reject/History |
| Ingestion (deleted in working tree, still at HEAD) | `ingestion/bot.py` (deleted `D` status) | — | Superseded by `chatbotv2/` |

**Optimizer modules:**

| Module | Path | Role |
|--------|------|------|
| Offline prototype | `commerce/offline_optimizer.py:1` | Log-odds `P(purchase|sent)`, creator-local chronological, abstention-first |
| Input contract | `commerce/opportunity_optimization.py:1` | `OptimizationInput` frozen contract, `AdvisoryOptimizationResult` |
| Historical validator | `commerce/opportunity_validation.py:1` | 7-gate `validate_advisory` |
| Evidence read model | `commerce/opportunity_evidence.py:1` | `classify_opportunity_evidence`, `get_evidence`, `list_offer_exposures` |
| Readiness collector | `commerce/optimizer_readiness.py:1` | `fetch_opportunity_rows`, `diagnose_row`, `summarize_rows`, `assess_readiness` |
| Dry-run harness | `commerce/opportunity_dry_run.py:1` | `run_dry_run` bounded agreement vs v1 |
| Ledger | `commerce/opportunity_ledger.py:1` | `record_opportunity_decision`, `link_opportunity_seal`, `record_opportunity_send`, `record_purchase_by_offer` |

**Commerce authority:**

| File | Role |
|------|------|
| `commerce/opportunity_engine.py:97 evaluate_opportunity` | Read-only orchestration (FanCommercialState → resolver → candidate → eligibility → ranking) |
| `commerce/opportunity_ranking.py:338 rank_candidates` | Deterministic v1 lexicographic `novelty → recent-item avoidance → offer-type order → stable_key` |
| `commerce/opportunity_sealing.py:274 seal_ranked_candidate` | Two-step provider verify + advisory lock `seal:{c}:{u}:{d}:{v}:{CUID}` → `commerce_offers` |
| `commerce/opportunity_execution.py:252 execute_sealed_offer` | Thin sealed→send (`enqueue_send` + dedup) |
| `commerce/ownership.py:43 classify_vault_overlap` | ZERO eligible, PARTIAL/FULL/INVALID rejected (never sliced) |
| `commerce/opportunity_eligibility.py` | Hard gates (status active, price, currency USD, Vault canonical) |
| `commerce/vault_sets.py:35 canonical_identity_ids` | `sorted(unique(ids))` identity |

**Data-access / DB:**

| Layer | Files |
|-------|-------|
| Postgres pool | `db/postgres.py:28 init_pool`, `39 verify_schema`, `72 verify_commerce_safety_schema`, `169 get_pool` |
| Migrations | `db/migrate.py:22`, `db/migrations/*.sql` (37 files) |
| Fangate mirror | `db/fangate.py` (`list_fangate_products`, `get_fangate_product`, `get_creator`) |
| Dropfans mirror | `db/dropfans.py` (`list_active_dropfans_products`, `get_dropfans_integration`, `find_dropfans_product`) |
| Offer definitions DAO | `db/offer_definitions.py` (`list_offer_definitions`, `get_offer_definition`, `map_offer_definition_drop`) |
| Commerce DAO | `commerce/dao.py` (`create_offer_serialized`, `find_pending_offer_for_product`, `mark_offer_purchased`, `get_owned_vault_ids`) |
| Redis | `db/redis.py:16 INBOUND_STREAM="inbound_messages"`, `18 SEND_STREAM="send_messages"`, `41 CONSUMER_GROUP="llm_workers"`, `42 SEND_CONSUMER_GROUP="send_workers"`, dedup keys `send_dedup:{creator}:{dedup}`, `inbound_dedup:{creator}:{user}:{tgId}`, debounce `debounce:creator:{c}:user:{u}` |
| Telemetry | `core/telemetry.py:19 GenerationTelemetry`, `db/migrations/20260901000000_phase87_observability.sql` etc. |

**Background / scheduled:**

| Job | File | Schedule |
|-----|------|----------|
| Scheduler poll | `workers/scheduler_worker.py:497` | `scheduler_poll_interval=10s` (`core/config.py:57`), `scheduler_batch_size=20`, `scheduler_recovery_timeout=300` |
| Reconciliation | `commerce/reconciliation.py:737 reconcile_all` (Dropfans poll `reconcile_dropfans_sales:349` + `reconcile_unattributed_purchases:39` + `recover_incomplete_post_purchases:672` + `recover_orphan_decisions`) | Every scheduler tick + `dropfans_reconciliation_interval_seconds=120` |
| Vault stale reaper | `commerce/vault.py` + `db/redis.py:410 list_unknown_vault_reservation_ids` | Via `workers/scheduler_worker` |
| Inbound gap reconcile | `db/redis.py:654 reconcile_inbound_gaps` | `workers/llm_worker.py:4566 lookback 300s limit 100` + periodic 60s |

---

## Actual Optimizer Pipeline

**Entry point:** `commerce/offline_optimizer.py:896 train_creator_model(dataset)` and `989 predict_for_input(model,input)` and evaluation `chronological_holdout_evaluate` (the repo has **no production training entry point** — training is called only from `tests/test_p35_6_offline_optimizer.py:519`).

**Trace backward (exact names):**

```text
Optimizer entry point
  commerce/offline_optimizer.py:896 train_creator_model / 989 predict_for_input / chronological_holdout_evaluate
        ↓ Training input
  commerce/offline_optimizer.py:324 CreatorDataset (creator_id, feature_schema_version, examples: TrainingExample[], n_bundles, excluded_counts, as_of)
  commerce/offline_optimizer.py:281 TrainingExample (creator_id, opportunity_id, evaluated_at, policy_version, definition_id/version, features tuple, label PURCHASED/DECLINED/EXPIRED, binary 0/1, evidence_quality, recovered, is_child, feature_schema_version)
        ↓ OptimizationInput
  commerce/opportunity_optimization.py:312 OptimizationInput (creator_id, opportunity_id, user_id, evaluated_at, policy_version, frozen_candidates: FrozenCandidate[], selected_definition_id/version, ownership_context, fan_commercial_summary, offer_history_summary, conversation_context, evidence_context, reengagement_context)
  commerce/opportunity_optimization.py:748 build_optimization_input(ledger_row, snapshot, evidence, offer_exposures) → pure, no I/O
        ↓ Feature extraction
  commerce/offline_optimizer.py:687 extract_features(inp: OptimizationInput) → dict[str,str] 16 categorical buckets (price_bucket via _price_bucket:244)
        ↓ Observation / query
  commerce/opportunity_ledger.py:411 record_opportunity_decision → commerce_opportunity_decisions row (creator_id PK scope, generation_id, evaluated_at, decision_snapshot JSONB, selected_*, sealed_offer_id, outcome_state, exposure_*, attribution_*, reengagement_of)
  commerce/optimizer_readiness.py:676 fetch_opportunity_rows (SELECT _READINESS_COLUMNS WHERE creator_id=$1 ORDER BY creator_id, opportunity_id LIMIT 500/2000)
  commerce/opportunity_dry_run.py:156 run_dry_run / commerce/optimizer_readiness.py:726 assess_readiness
        ↓ Evidence
  commerce/opportunity_evidence.py:208 classify_opportunity_evidence(row, as_of) → {exposure_state, maturity_state, label, evidence_quality, recovered, reengagement_of, outcome_state, transaction_id, maturity_policy_version}
        ↓ Outcome
  commerce/opportunity_ledger.py:39 OUTCOME_STATES {PENDING,SENT,SEND_FAILED,PURCHASED,DECLINED,EXPIRED,CLICKED_NO_PURCHASE,REVOKED,REENGAGED,SEAL_FAILED,NO_OPPORTUNITY,NO_SELECTION} + ATTRIBUTION_STATUSES {unattributed,attributed,ambiguous}
        ↓ Opportunity
  commerce/opportunity_engine.py:97 evaluate_opportunity(creator_id, user_id, conversation_state, now) → OpportunityEngineResult (creator_id,user_id,evaluated_at, fan_commercial_state, offer_history, owned_vault_ids, candidates, eligible_candidates, ineligible, ranking_inputs, ranking_result, selected_candidate, has_opportunity, status)
        ↓ Exposure
  commerce/opportunity_evidence.py:91 EXPOSURE states {UNAVAILABLE,DECISION,SEALED,SEND_ATTEMPTED,SENT} + ledger columns exposure_state/at/source (20260919000000)
```

**IDs / timestamps / state:**

| Field | Name in code | Table/model | Type | Notes |
|-------|--------------|-------------|------|-------|
| Opportunity ID | `opportunity_id` | `commerce_opportunity_decisions.opportunity_id BIGSERIAL PK` | int >0 | Creator-scoped, immutable after insert |
| Generation ID | `generation_id` | `commerce_opportunity_decisions.generation_id TEXT`, `messages.generation_id`, `generation_telemetry.generation_id` | string `recovered:{c}:{offer}` for recovery, `synthetic:` for fixtures | Creator+gen unique partial index |
| Creator ID | `creator_id` | everywhere | int >0, `bool` rejected | First predicate in every query |
| Fan ID | `user_id` | `users.id`, `messages.user_id` | BIGINT PK | |
| Evaluated at | `evaluated_at` | `commerce_opportunity_decisions.evaluated_at TIMESTAMPTZ NOT NULL` | tz-aware UTC | Ordering key for chronological split; feature cutoff |
| As-of | `as_of` | `classify_opportunity_evidence(as_of)`, `assess_readiness(as_of)` | tz-aware UTC, naive raises | Label cutoff; later outcomes invisible |
| Outcome at | `outcome_at` | `commerce_opportunity_decisions.outcome_at` | tz-aware UTC | Terminal maturing pivot |
| Exposure at | `exposure_at` | `commerce_opportunity_decisions.exposure_at` | tz-aware UTC, NOT NULL when exposure != NONE | Latest knowable event for maturity |
| State transitions | `decision_status` → `DECIDED/SEALED/SEAL_FAILED/REENGAGED` and `outcome_state` `PENDING→SENT/SEND_FAILED→PURCHASED/DECLINED/EXPIRED/...` non-terminal `PENDING/SENT/SEND_FAILED` only updatable (`opportunity_ledger.py:58 NON_TERMINAL_OUTCOMES`) | | | |
| Immutable | `decision_snapshot, selected_definition_id/version/stable_key, creator/user/evaluated_at` | never overwritten after `record_opportunity_decision` (`_OUTCOME_COLUMNS:69` excludes them) | | |
| Mutable | `outcome_state/outcome_at/transaction_id/purchased_* / attribution_* / exposure_* / updated_at` | outcome writers only | | |
| FK | `sealed_offer_id → commerce_offers(id) ON DELETE SET NULL`, `reengagement_of → commerce_opportunity_decisions(opportunity_id)` | | | |

---

## Optimizer Target

**Grain:** one opportunity ledger row (`opportunity_id`) — not candidate, not product, not message. Re-engagement children are separate rows at observation grain (Option A).

**Positive:** `LABEL_POSITIVE = "POSITIVE"` with `maturity_state=MATURE`, `exposure_state=SENT`, `evidence_quality=FULL`, `recovered=False`, `is_child=False`, `attribution_status="attributed"` and `transaction_id` non-empty, `outcome_state="PURCHASED"` (effective outcome knowable at `as_of`). Implemented `offline_optimizer.py:621 TRAIN_LABEL_PURCHASED` with guard `607-620 attribution==attributed && has_txn`.

**Negative:** `LABEL_COMMERCIAL_NEGATIVE = "COMMERCIAL_NEGATIVE"` with same `MATURE SENT FULL non-recovered non-child` and `outcome_state` `DECLINED` or `EXPIRED` → `TRAIN_LABEL_DECLINED` / `TRAIN_LABEL_EXPIRED:632` both `binary=0` (`offline_optimizer.py:631-656`). `PROCESS_NEGATIVE` (`SEAL_FAILED/REVOKED/CLICKED_NO_PURCHASE`, plus `SEND_FAILED` when mature `offline_optimizer.py:544`) is **excluded, never 0** (`CLASS_PROCESS_NEGATIVE`).

**Observation:** eligible primary = mature SENT FULL attributed PURCHASED with txn **or** mature SENT FULL COMMERCIAL_NEGATIVE, with `evaluated_at < as_of` and `evidence_quality==FULL`, non-child, non-synthetic, reconstructible `OptimizationInput` (`readiness:332` mirror). Everything `CENSORED/UNAVAILABLE` is never 0/1 (`offline_optimizer.py:522-569`).

**Purchase event:** `commerce_offers.state='purchased'` + `transaction_id IS NOT NULL` + `sealed_offer_id` link in ledger + `fangate_transactions` mirror (see § Purchase Attribution). `attribute_purchase:26` / `reconcile_unattributed_purchases:39` set `commerce_offers.transaction_id` via conditional `UPDATE state IN ('pending','clicked') AND (transaction_id IS NULL OR = $3)`.

**Exposure:** `EXPOSURE_SENT` = application-recorded `send/execution` (`exposure_source='sealed_execution'`: `opportunity_ledger.py:675`) or purchase-entailed (`exposure_entailed_by_purchase` flag `opportunity_evidence.py:340`). `SENT != delivery/click` (docstring `evidence.py:15-19`). SENTrung stronger than `SEALED`.

**Maturity:** `MATURITY_POLICY_VERSION="p353b.v1"` `MATURITY_WINDOW_HOURS=168` — open `PENDING/SENT/SEND_FAILED` mature only after `max(evaluated_at, exposure_at/outcome_at)+168h`; if still unresolved while mature → stays `CENSORED` (`_maturity:363` + `_label:398` `open_outcomes → CENSORED`), maturity never manufactures negative. Terminal mature once `as_of >= outcome_at`.

**When selected but not exposed:** `exposure != SENT` (`SEALED` or `DECISION` only) → `build_supervised_label:571 no_sent_exposure → CLASS_CENSORED` → excluded from primary (correct — no exposure, no conversion denominator).

**When exposed but no purchase and maturity immature:** `mature=False → CLASS_CENSORED:522`, remains censored even when later mature (`_maturity mature + _label open_outcomes → CENSORED`), never becomes negative.

**When evidence incomplete:** malformed `as_of` raises; missing row → `_unavailable` → `CLASS_UNAVAILABLE`; non-full quality (`PARTIAL`→`CLASS_PARTIAL:511`, `UNATTRIBUTED` on PURCHASED without txn → `CLASS_UNATTRIBUTED:584`) → excluded; `is_child`→`CLASS_REENGAGEMENT_CHILD:498`; `recovered:` prefix→`CLASS_RECOVERED:487`.

**Doc vs code:** No discrepancy found. Docstring `opportunity_evidence.py:1-60` 10-point contract matches implementation `208-440`. `offline_optimizer.py:57-68` maturity/equality pinned matches `reconciliation.py:37`.

---

## Selection Bias Semantics

**Lifecycle (actual names):**

```text
candidate                  commerce/opportunity.py:57 OpportunityCandidate (definition + mapped_drop_ids)
  ↓ eligibility verdict    commerce/opportunity_eligibility.py evaluate_opportunity_eligibility → verdict.eligible bool
eligible   (verdict.eligible==true, stored in OpportunityEngineResult.eligible_candidates)
  ↓ ranking input          commerce/opportunity_ranking.py assemble_ranking_input (requires eligible)
ranked    (OpportunityRankingInput per eligible, keys novel_set/recent_item_overlap/type_order)
  ↓ deterministic rank     commerce/opportunity_ranking.py rank_candidates → OpportunityRankingResult.ranked ordered, selected = ranked[0]
selected  (selected_candidate = eligible where (definition_id,version)==ranked[0] → OpportunityEngineResult.selected_candidate)
  ↓ sealing gate (only if len(mapped_drop_ids)==1 else NO_MAPPED_DROP/MULTIPLE_DROPS → sealing not called, llm_worker:1481)
sealed    (commerce/opportunity_sealing.py SealResult SEALED → commerce_offers row, sealed_offer_id)
  ↓ send  (commerce/opportunity_execution.py execute_sealed_offer → SEND_STREAM → send_worker)
exposed = SENT (commerce_opportunity_decisions.exposure_state=SENT via record_opportunity_send on EXECUTED/ALREADY_DELIVERED)
  ↓ evidence                commerce/opportunity_evidence.classify_opportunity_evidence → label/maturity
observed  (mature SENT FULL ...)
  ↓ attribution             commerce/opportunity_ledger.record_purchase_by_offer → outcome_state PURCHASED
purchased / not purchased (COMMUNICATION_NEGATIVE = not purchase, but observed negative)
```

**Unselected = non-observation (critical):**

```text
unselected candidate  →  not exposed → exposure != SENT → build_supervised_label:571
                     →  CLASS_CENSORED (reason=no_sent_exposure) → excluded from CreatorDataset.examples
                     →  NEVER binary 0/1, NEVER counted in n_neg, NEVER pooled
                     →  score_candidates_extrapolative:1119 exists only as explicitly labeled extrapolative diagnostic with EXTRAPOLATIVE_WARNING:160, never pooled into primary metrics
```

**Code proof:** `offline_optimizer.py:42-46` doc *Only v1-selected/exposed candidates have observed outcomes. The model answers only ... never claims candidate B would have converted better*; `make_training_example:774` consumes only the **selected** frozen candidate's features via `_selected_frozen_candidate:674` (selected identity must be in frozen set, else `ValueError` → abstain).

---

## Opportunity Lifecycle (concrete, repository names)

```text
OfferDefinition (commerce_offer_definitions: id, creator_id, stable_key, version, offer_type, canonical_vault_item_ids[1..10], price_minor, currency, allow_download, status=active)
  ↓ commerce/offer_definition_resolver:resolve_offer_definitions (active only)
ResolvedDefinition (definition_id, creator_id, stable_key, version, canonical_vault_item_ids, mapped_drop_ids tuple[str])
  ↓ commerce/opportunity.py:candidate_from_definition(creator_id, user_id, definition_dict, mapped_drop_ids)
OpportunityCandidate (creator_id,user_id,definition_id,stable_key,version, offer_type, canonical_vault_item_ids, family_id, price_minor, currency, allow_download, mapped_drop_ids, definition_status, provider_verification="unverified")
  ↓ commerce/opportunity_eligibility:evaluate_opportunity_eligibility(candidate, owned_vault_ids frozenset, offer_history) → EligibilityVerdict(eligible:bool, denial_reasons:())
OpportunityEngineResult (has_opportunity bool, status NO_CANDIDATES|NO_ELIGIBLE_CANDIDATES|NO_SELECTION|RANKED, selected_candidate or None, ranking_result)
  ↓ commerce/opportunity_ranking:assemble_ranking_input(candidate, verdict, fan_commercial_state, offer_history, conversation, evaluated_at) → OpportunityRankingInput(keys)
  ↓ commerce/opportunity_ranking:rank_candidates(entries, evaluated_at) → OpportunityRankingResult(selected: RankedCandidate(definition_id,stable_key,version, factors))
OpportunityEngineResult.selected_candidate (mapped to ranked winner)
  ↓ (if len(mapped_drop_ids)==0 → NO_MAPPED_DROP; >1 → MULTIPLE_DROPS; ==1 → sealing candidate, workers/llm_worker.py:1481)
Sealing: commerce/opportunity_sealing:seal_ranked_candidate(candidate, ranked_context, creator_id, user_id, chosen_drop_cuid)
  → _fetch_and_verify:261 via integrations/dropfans/service.get_drop → verify_live_drop → verify_candidate_against_live 8 gates (membership exact, price, currency, allow_download, status APPROVED, CUID membership)
  → advisory lock seal:{c}:{u}:{d}:{v}:{CUID} → persist commerce_offers (creator_id,user_id,product_id synth, link, price_minor=currency, vault_item_ids, media_count, drop_content_hash, dropfans_product_id, reason envelope v=1 with definition_id/version/stable_key/sealed_at/verified_hash)
  → SealResult(status SEALED, offer dict, sealed_identity (c,u,d,v,CUID))
  ↓ persistence point 1: commerce_offers row id = sealed_offer_id
Execution: commerce/opportunity_execution:execute_sealed_offer(seal_result, creator_id, user_id, generation_id)
  → try_reserve_send_dedup dedup_id=sealed:{offer_id} → db/redis:enqueue_send SEND_STREAM → release reservation
  → ExecuteResult(status EXECUTED | ALREADY_ENQUEUED with refinement ALREADY_DELIVERED vs ALREADY_RESERVED)
  ↓ persistence point 2: send_dedup:{creator}:{dedup}="1" + send_random_id + messages outbound row via send_worker
Ledger exposure: commerce/opportunity_ledger:record_opportunity_send(opportunity_id, sealed_execution) → commerce_opportunity_decisions.exposure_state=SENT (or SEND_ATTEMPTED), exposure_at, exposure_source=sealed_execution
  ↓ evidence: commerce/opportunity_evidence:classify_opportunity_evidence(row, as_of) → exposure_state, maturity_state, label, evidence_quality
  ↓ outcome: workers/scheduler:reconcile_unattributed_purchases / Dropfans poll → commerce/opportunity_ledger:record_purchase_by_offer(creator_id, offer_id, transaction_id) → outcome_state PURCHASED (or record_offer_terminal_outcome DECLINED/EXPIRED/REVOKED/CLICKED_NO_PURCHASE)
  ↓ maturity: _maturity(evaluated_at, exposure_at, outcome_at, now) → MATURE only after now >= outcome_at or anchor+168h
  ↓ optimizer observation: OptimizationInput via build_optimization_input(ledger_row) + evidence + sibling linkage → FrozenCandidate features → TrainingExample → CreatorDataset → OfflineModel (prior_log_odds + feature_llr)
```

**Fields:**

| Field | IDs | Timestamps | Status/State | Immutable vs Mutable | FK | Creator/Fan/Product/Price |
|-------|-----|------------|--------------|----------------------|----|---------------------------|
| OfferDefinition | `id, stable_key, version, (creator_id,stable_key,version) unique, (creator,stable_key) WHERE active unique` | `created_at, updated_at` | `status draft/active/retired` | immutable commercial identity | `creator_id→creators, family_id→commerce_content_families` | creator, Vault 1..10, price_minor >=0, currency, allow_download |
| OpportunityCandidate | `(creator_id,user_id,definition_id,version)` | none (in-memory) | `definition_status` | frozen at construction | definition | product=Vault set, price verbatim |
| OpportunityDecision | `opportunity_id PK, generation_id` | `evaluated_at, outcome_at, exposure_at, created_at, updated_at` | `decision_status`, `outcome_state`, `exposure_state`, `attribution_status/confidence` | decision_snapshot immutable, outcome mutable | `sealed_offer_id→commerce_offers, reengagement_of→self` | creator, user, selected def/ver, drop_cuid, purchased_price/currency, vault via snapshot |
| Offer | `commerce_offers.id, sealed_offer_id` | `created_at, purchased_at` | `state pending/clicked/purchased/declined/expired/revoked` | `vault_item_ids/media_count/hash, dropfans_product_id` snapshot | `creator_id, user_id, product_id synth` | product Vault snapshot, price live-verified at seal |

---

## Purchase Attribution

**Source:** Fangate `fangate_transactions` event `dropfans_sale` (Dropfans provider) polled `commerce/reconciliation.py:349 reconcile_dropfans_sales` via `integrations/dropfans/service.reconcile_sales` per `dropfans_creator_id`, then `commerce/reconciliation:39 reconcile_unattributed_purchases` + `workers/scheduler_worker:519 reconcile_purchases` periodic.

**Sale identity:** `transaction_id` (provider CUID transaction), `product_id` (internal `fangate_products.id` synthetic BIGINT, not price), `creator_id`, `product` mapping via `dropfans_product_id` (creator-scoped CUID) → `fangate_products.dropfans_product_id`.

**Attribution deterministic rule:**

1. `commerce/dao.find_pending_offer_for_product` scopes `creator_id,user_id,product_id` state `pending|clicked`.
2. `commerce/attribution.py:44 mark_offer_purchased(creator_id, offer["id"], transaction_id)` conditional `UPDATE commerce_offers SET state='purchased', transaction_id=$3 WHERE creator_id=$1 AND id=$2 AND state IN ('pending','clicked') AND (transaction_id IS NULL OR = $3)` — first writer wins, second gets `None` (ambiguous fail-closed).
3. `commerce/opportunity_ledger:687 record_purchase_by_offer` single-winner: `UPDATE commerce_opportunity_decisions SET outcome_state='PURCHASED' ... WHERE opportunity_id = (SELECT opportunity_id FROM commerce_opportunity_decisions WHERE creator_id=$1 AND sealed_offer_id=$2 AND outcome_state IN ('PENDING','SENT','SEND_FAILED') AND reengagement_of IS NULL ORDER BY opportunity_id ASC LIMIT 1)` — lowest original row wins, fallback to earliest re-engagement touch only if no original exists, then `SELECT ... WHERE transaction_id=$3` for duplicate webhook idempotency → returns existing row.
4. Recovery-safe `commerce/opportunity_recovery.py:572 attribute_purchase_safe` checks `existing transaction` then `existing PURCHASED` before delegating, so spare eligible row never claimed.

**Window:** `commerce/reconciliation.py:37 RECONCILIATION_WINDOW_HOURS = 7*24 =168h` — unattributed `fangate_transactions` older than `created_at >= NOW()-168h` ignored (limit `BATCH_SIZE=50`). Late purchases beyond 168h remain unattributed (manual investigation). Maturity window equals this (pinned regression).

**Duplicate:** second webhook with same `(creator,offer,transaction_id)` returns existing row (`opportunity_ledger:783` SELECT). Already `PURCHASED` row blocks second-row claim (`attribute_purchase_safe:617`).

**Late:** `record_purchase_by_offer` `purchased_at` coerce `provider skew rule` (`_coerce_aware:95` naive→UTC, `outcome_at = now` fallback), never rejects on clock skew.

**Ambiguous:** `commerce/reconciliation.py:148` if `len(candidates)>1` → `_disambiguate_via_buyer_identity` (buyer_email), else `ambiguous` → `_record_ambiguous_recovery`, return `False` (fail-closed, transaction remains unattributed, not counted).

**Without opportunity:** orphan sealed `commerce_offers` with no ledger row → `commerce/opportunity_recovery:219 plan_recovery` → synthetic `recovered:{creator}:{offer_id}` ledger row with snapshot `recovered=true` and `attribution_confidence='partial'` always, `exposure_state=NONE` unless purchase implies SENT.

**Without purchase:** stays `PENDING/SENT/SEND_FAILED` → censored until mature, never negative; after `mature_at = max(evaluated_at,exposure_at)+168h` still `CENSORED` (`evidence:398`).

---

## Maturity / Readiness

**Implementation:** `commerce/opportunity_evidence.py:76-83` pinned `MATURITY_POLICY_VERSION="p353b.v1"`, `MATURITY_WINDOW_HOURS=168`; versioned, applied in one place `_maturity:363`. Derives from `commerce/reconciliation.RECONCILIATION_WINDOW_HOURS`.

| Requirement | Value | Where |
|-------------|-------|-------|
| Minimum evidence | `n_rows` bounded `READINESS_LIMIT=500, READINESS_MAX=2000` (`optimizer_readiness:97-100`) | `assess_readiness:726` |
| Minimum positive `n_pos` | `2` | `optimizer_readiness:91 MIN_TRAIN_POSITIVE=2` |
| Minimum negative `n_neg` | `2` | `MIN_TRAIN_NEGATIVE=2` |
| Minimum total `n_total` | `6` | `MIN_TRAIN_TOTAL=6` |
| Time windows | `168h` window, `span_hours = latest-earliest` in `CreatorReadiness` | `opportunity_evidence._maturity` |
| Configurable | **Not runtime-configurable** — constants, bump only with explicit tested policy change (comment `Bumped only with an explicit, tested policy change — never silently.`) | |
| Where calculated | `commerce/opportunity_evidence.py:363 _maturity` (pure) vs `optimizer_readiness.py:287 _primary_eligibility` (mirror) — parity tested `tests/test_p36_optimizer_readiness.py:170` | |
| Persisted? | **No** — `maturity_state/at` derived on read, not column; `maturity_policy_version` stored per-row in evidence classification only | |
| Can become mature later | **Yes** — `_maturity` is function of `now`; `as_of` cutoff moves, `IMMATURE→MATURE` after window. Test `test_p35_3b_evidence_maturity.py` traverses timeline | |
| Can mature change after | **No flip to negative** — mature-but-unresolved stays `CENSORED`; terminal mature once recorded never changes except via `record_purchase_by_offer` before terminal; `NON_TERMINAL_OUTCOMES` guard prevents overwrite | |

**Readiness (`optimizer_readiness.py:543 summarize_rows`):** per-creator `floors_met = n_primary>=6 and pos>=2 and neg>=2` string `floors_detail="primary=X pos=Y neg=Z floors=(6,2,2)"`; holdout ` _holdout_feasibility:511` tries every midpoint cutoff `stamps[1:]` where `train = evaluated_at < cutoff`, `test = >= cutoff`, requires `len(train)>=6 && pos>=2 && neg>=2 && len(test)>=1`. Global `GlobalReadiness:251` aggregates `n_sent, n_full_mature_sent, n_primary/pos/neg/process/censored/unavailable/recovered/partial/unattributed/children/synthetic, multiple_purchase_offers`.

Thresholds are **readiness behavior, not commercial sufficiency proof** — code docstring `optimizer_readiness:36 EL is mirror must match prototype label semantics; parity is asserted by tests, which may import both sides` + `prospective threshold 0.5 prototype-only`.

---

## OptimizationInput Contract

**Implementation:** `commerce/opportunity_optimization.py:312 OptimizationInput` frozen dataclass (creator_id, opportunity_id, user_id, evaluated_at, policy_version, frozen_candidates, selected_*, ownership_context, fan_commercial_summary, offer_history_summary, conversation_context, evidence_context, reengagement_context, experiment_id/variant_id reserved nullable never assigned this phase).

**Field table (actual code `opportunity_optimization.py:112-342`):**

| Field | Type | Source | Computed? | Timestamp basis | Available at prediction time? |
|-------|------|--------|-----------|----------------|-------------------------------|
| `creator_id` | `int >0` | `ledger_row.creator_id` | no | `evaluated_at` | **YES** (scope) |
| `opportunity_id` | `int >0` | `ledger_row.opportunity_id` | no | `evaluated_at` | YES (identity) |
| `user_id` | `int >0` | `ledger_row.user_id` | no | `evaluated_at` | YES (scope) |
| `evaluated_at` | `datetime tz-aware UTC` | `ledger_row.evaluated_at` coerced `UTC` | no | itself | YES (ordering, not feature) |
| `policy_version` | `str|None` | `snapshot.ranking.policy_version` (`"v1"`) | no | `evaluated_at` | YES (segmentation, never feature `offline_optimizer:92 policy_version segmentation only`) |
| `frozen_candidates` | `tuple[FrozenCandidate]` | `snapshot.eligible[]` via `build_frozen_candidate:437` (requires `definition_id>0, version>=1, stable_key non-empty, canonical_vault_ids canonical, 1..10, mapped_drop_ids`) | sorted mapping, no catalog re-read | `evaluated_at` | YES (but only selected used for primary `predict_for_input`; all for extrapolative diagnostic) |
| `selected_definition_id` | `int|None` | `snapshot.selected.definition_id` fallback `ledger_row.selected_definition_id` | fallback | `evaluated_at` | YES |
| `selected_definition_version` | `int|None` | same | — | `evaluated_at` | YES |
| `ownership_context` | `OwnershipContext(creator_id,user_id,owned_vault_ids frozenset, source="snapshot")` | snapshot `delivered_vault_ids`? Actually `build_optimization_input:801 ownership from fan_summary.owned_vault_ids = FanCommercialSummary.owned_vault_ids` which is `snapshot.fan.purchased_vault_ids` (`_frozen_str_set`) | computed via fan snapshot | `evaluated_at` | YES |
| `fan_commercial_summary` | `FanCommercialSummary(creator... purchase_count, total_spend, avg_order, highest, last_purchase_at, recent_purchase_count/spend, owned/delivered_vault_ids, recent_offer_count/rejected, last_offer_at, offered_ids, currency)` | `snapshot.fan` via `build_fan_summary:494` `creator_id,user_id` scoped | ints/iso stamps bucketed later | `evaluated_at` capture of `FanCommercialState` | YES |
| `offer_history_summary` | `OfferHistorySummary(creator... total_offer_count, recent_offer_count, last_offer_at, declined_offer_count, state_counts, has_active_offer, vault_sets, active_sets, null_snapshot_count, definition_identity_available bool)` | `snapshot.history` via `build_history_summary:555` | sanitized counts/sets | `evaluated_at` | YES |
| `conversation_context` | `ConversationContext(lifecycle, current_topic, recent_topics:tuple[str], open_threads)` | `snapshot.conversation` via `build_conversation_context:629` (only 4 admissible fields) | str tuple | `evaluated_at` | YES |
| `evidence_context` | `EvidenceContext(exposure_state, exposure_at ISO, label, maturity_state, maturity_policy_version, evidence_quality, attribution_status, recovered, evidence_as_of)` | `classify_opportunity_evidence(row,as_of)` via `build_evidence_context:651` (transaction_id + purchased price deliberately dropped) | `as_of`-gated | `as_of` (future outcome not feature) | **NO future outcome as feature** — but `recovered/is_child` quality guards consulted for abstain |
| `reengagement_context` | `ReengagementContext(opportunity_id, reengagement_of, is_child, sibling_touch_count, purchase_winner_opportunity_id, revenue_events)` | `ledger_row.reengagement_of + list_offer_exposures` via `build_reengagement_context:691` | count of linked touches | `evaluated_at` + `as_of` sibling | YES |
| `experiment_id / variant_id` | `str|None` | reserved, never assigned this phase | — | — | intentionally `None` |

**PPD cutoff:** all frozen at `evaluated_at`; current OfferDefinition/Drop/Vault/provider/salesCount/dashboard/LLM/message post-decision never read.

**Leakage flags:**

- `evidence_context.recovered` and `reengagement_context.is_child` are **quarantine signals**, not outcome — `predict_for_input:1031` uses them to **abstain**, not as predictive feature (safe).
- `frozen_candidates[].price_minor` enters only as `price_bucket` (`offline_optimizer:744`) — bucket is frozen fact, not recommended; safe per docstring but monitor.
- `fan_commercial_summary` values are **all frozen at evaluated_at**, but producer `commerce/fan_commercial_state.py` queries `SELECT user.funnel_stage` etc. at engine time — audit must verify query window caps at `now` not future (it does — `fan_commercial_state` is snapshot of DB at `evaluated_at`).
- **Most critical leakage vector blocked:** no `future purchase/outcome/outcome_at/transaction_id/current ownership/Vault/Drop state/salesCount/LLM score/msg prose/provider response` ever enters `extract_features`.

---

## Feature Extraction

**Function:** `commerce/offline_optimizer.py:687 extract_features(inp: OptimizationInput) → dict[str,str] 16 categorical` via `774 _selected_frozen_candidate` + buckets + `763 _features_tuple`.

| Feature | Source | Calculation | Time boundary | Creator/fan scope | Future enters? |
|---------|--------|-------------|---------------|-------------------|----------------|
| `offer_type` | `selected.offer_type` | `strip or "UNKNOWN"` | `evaluated_at` frozen | creator | **NO** |
| `price_bucket` | `selected.price_minor` | `None→MISSING, <1000 LOW, <3000 MID, else HIGH` (`_price_bucket:244`) | `evaluated_at` pinned offer fact, bucketed | synthetic threshold, not recommendation | **NO** |
| `currency` | `selected.currency` | `strip or "MISSING"` | `evaluated_at` | | **NO** |
| `family_presence` | `selected.family_id` | `HAS_FAMILY if not None else NO_FAMILY` | `evaluated_at` — always `NO_FAMILY` (snapshot gap `optimizer_readiness:131 degraded`) | | **NO** |
| `vault_count_bucket` | `len(selected.canonical_vault_ids)` | `0→V0,1→V1,2→V2, else V3P` | `evaluated_at` | | **NO** |
| `drop_mapping` | `len(selected.mapped_drop_ids)` | `1→SINGLE else OTHER` | `evaluated_at` | | **NO** |
| `fan_purchase_bucket` | `fan_commercial_summary.purchase_count` | `<=0→P0, <=2→P1_2 else P3P` | `evaluated_at` frozen fan | fan, creator-scoped | **NO** |
| `fan_spend_bucket` | `fan.total_spend_minor` | `<=0→S0, <5000→S_LOW else S_HIGH` | same | | **NO** |
| `fan_recent_offer_bucket` | `fan.recent_offer_count` | `<=0→R0, ==1→R1 else R2P` | same | | **NO** |
| `fan_rejected_bucket` | `fan.recent_rejected_offer_count` | `<=0→J0 else J1P` | same | | **NO** |
| `history_total_bucket` | `history.total_offer_count` | `<=2→H0_2, <=5→H3_5 else H6P` | same | creator+user | **NO** |
| `history_declined_bucket` | `history.declined_offer_count` | `<=0→D0 else D1P` | same | | **NO** |
| `has_active_offer` | `history.has_active_offer` | `bool → ACTIVE/NO_ACTIVE` | same | | **NO** |
| `lifecycle` | `conversation_context.lifecycle` | `strip or "MISSING"` | same | | **NO** |
| `topic_presence` | `conversation_context.current_topic` | `bool → HAS_TOPIC/NO_TOPIC` | same | | **NO** |
| `owned_count_bucket` | `ownership_context.owned_vault_ids` | `0→O0, <=2→O1_2 else O3P` | same (decision-time purchased set) | creator+user | **NO** |

**Future data checks:**
- No `future purchases / future messages / future offers / post-outcome / full-dataset aggregates / random train/test / creator pooling / post-prediction mutable state` — trainer orders by `evaluated_at`, `train_end <= test_start` enforced (`offline_optimizer:70`). Distinct-value cardinality-aware Laplace smoothing uses only train slice (`offline_optimizer:947`).

---

## Leakage Analysis

**Potential leakage vectors inspected — all blocked:**

| Vector | Risk | Actual |
|--------|------|--------|
| Future purchase count / spend after `evaluated_at` | HIGH | Blocked by snapshot: `fan_commercial_summary` from `snapshot.fan` frozen at `evaluated_at`; no live `FanCommercialState` re-read after. |
| Current ownership post-purchase (acquired after decision) | HIGH | Blocked: `ownership_context` from snapshot fan purchased set at decision, not `dao.get_owned_vault_ids` at prediction time. |
| `transaction_id / outcome_at / purchased_price_minor` | HIGH | Explicitly dropped in `EvidenceContext` (`opportunity_optimization.py:651` drops txn/price) and `classify_opportunity_evidence` gated `gated_transaction/price:259` not in features. |
| Current catalog Drops/Vault live state | MEDIUM | Blocked: `frozen_candidates` from snapshot, never `list_active_dropfans_products` at extraction. |
| `post-decision conversation` (post `evaluated_at` messages) | MEDIUM | `conversation_context` only 4 frozen fields at decision. |
| `provider send result` (success/failure) | MEDIUM | `SENT` is outcome label via `as_of` gate, never feature; `extract_features` raises `ValueError` only on missing required sections (never reads evidence outcome). |
| `aggregates over full dataset` (price distribution) | MEDIUM | `price_bucket` thresholds are prototype constants (`LOW<1000`), not data-dependent quantiles. |
| `random split` contamination | HIGH | Chronological only: `offline_optimizer:70` `train_end <= test_start`, `readiness:511` midpoint cutoffs. |
| `creator contamination` | HIGH | Per-creator datasets, raises on cross-creator bundle (`build_creator_dataset:863`), trainer re-asserts `len(creators)>1 → abstain:917`. |
| `family_id` gap | LOW | `NO_FAMILY` constant — no value invented (`readiness:131`), not leakage but missing signal. |

---

## Creator Isolation

| Layer | Enforced how | File:Symbol | Verdict |
|-------|--------------|-------------|---------|
| Queries | every query `creator_id` first predicate, `ORDER BY creator_id, opportunity_id` | `opportunity_ledger.py:83 _require_scope`, `optimizer_readiness.py:155 _require_scope`, `opportunity_evidence.py:141`, `offline_optimizer.py:232 _require_scope` | **Enforced** |
| Feature aggregation | `distinct[name].add(value)` per example, never cross-creator; `build_creator_dataset` raises on foreign bundle | `offline_optimizer.py:946,863` | **Enforced** |
| Training datasets | `Dataset.creator_id`, `n_bundles`, `excluded_counts`, `feature_schema_version` separate per creator | `offline_optimizer.py:327 CreatorDataset` | **Enforced** |
| Joins | no cross-creator JOINs; `readiness.summarize_rows:549 creators.setdefault(int(diag.creator_id), [])` per-creator bucket | `optimizer_readiness.py:549` | **Enforced** |
| Caches / Redis keys | `send_dedup:{creator}:{dedup}`, `inbound_dedup:{creator}:{user}:{tgId}`, `lock:creator:{c}:user:{u}`, `debounce:creator:{c}:user:{u}` — all creator-scoped, no global fallback (`redis.py:85,125,1061,1096 fail closed`) | `db/redis.py:85,1061` | **Enforced** |
| Model storage | No persistent store yet (in-memory `OfflineModel.creator_id` per model); future store must be `vault/optimizer_models/{creator}_{schema}.json` not shared | `offline_optimizer.py:339 OfflineModel` | **Unclear until store exists — must enforce per-creator file/row** |
| Evaluation | `CreatorDataset.as_of` + `chronological_holdout_evaluate` per creator, `GlobalReadiness.creators:tuple[CreatorReadiness]` never pooled | `offline_optimizer EvaluateReport`, `optimizer_readiness.GlobalReadiness:252` | **Enforced** |
| Identifiers | `creator_id int>0 bool rejected`, `user_id int>0`, `opportunity_id PK`, `generation_id creator+generation unique` | everywhere `_require_scope` | **Enforced** |

**Can A influence B?** No in code; future `optimizer_store` is the only risk — must not share `feature_llr` across creators.

---

## Product / Pricing Authority

**Authoritative:**

| Concern | Authority | File:Symbol | Notes |
|---------|-----------|-------------|-------|
| Product identity | `OfferDefinition` `(creator_id, stable_key, version)` + `Vault set canonical` + `Drop mapping CUID` | `db/offer_definitions.py`, `commerce/offer_definition_resolver` (sole active source), `commerce/opportunity.py:94 candidate_from_definition` (requires `canonical` already sorted) | One active version per `(creator,stable_key)` (`idx_offer_definitions_creator_key_active:39 UNIQUE WHERE active`) |
| Product selection | `opportunity_engine.evaluate_opportunity` → `opportunity_ranking.rank_candidates` (v1 novelty) | `opportunity_engine.py:97`, `opportunity_ranking.py:338` | Deterministic, no LLM/price |
| Price | Frozen `definition.price_minor` (int >=0) vs `Vault set` 1..10 | `offer_definitions.sql:22 CHECK price_minor >=0`, `opportunity.py:171-176 price` carried verbatim | `dollars_to_price_minor` cents via `Decimal` in `drop_reconciliation.py:265` |
| Eligibility | `opportunity_eligibility.evaluate_opportunity_eligibility` (hard: active status, non-negative price, USD, 1..10 Vault, ownership ZERO) | — | Pure, no optimizer |
| Commerce decision | `OpportunityEngineResult.has_opportunity` + `ranking_result.selected` | `opportunity_engine.py:330` | Not price-driven |
| Execution | `commerce/execution.py:141 execute_ppv` (legacy fangate path) **and** `commerce/opportunity_sealing+execution` (new) — but only sealing path is opportunity; `execute_ppv` quarantined | see § Production Side Effects | Legacy path blocked by schema gate |

**Optimizer inputs contain:**

```text
actual selected product? YES — via selected_definition_id/version + frozen_candidates selected entry (authoritative snapshot, but not used as selector in primary predict_for_input — only its features)
actual price?           YES — as frozen price_minor bucketed, never as authority
candidate product?      YES — all frozen_candidates available but only selected is scored in primary (score_candidates_extrapolative scores all with EXTRAPOLATIVE warning)
candidate price?        YES — same frozen
whether authoritative?  NO — optimizer output is advisory (probability float), never price, never product mutation, never execution
```

**Pricing authority risk:** future simulator must not call `drop_content_key`/`create_drop` or write `fangate_products`; it must reuse the same `price_bucket` frozen fact semantics if it synthesizes candidates.

---

## Event / Redis Architecture

| Event / Mechanism | Producer | Consumer | Payload / Identifier | Timestamp | Persistence | Retry / Idempotency |
|-------------------|----------|----------|----------------------|-----------|-------------|---------------------|
| `INBOUND_STREAM=inbound_messages` Redis Stream | `chatbotv2/handlers.py` `debounce_enqueue` → `db/redis.py:890 enqueue_inbound` | `workers/llm_worker.py:read_inbound` consumer group `llm_workers` ( `ensure_consumer_group:46` `XGROUP CREATE MKSTREAM` ) | `{user_id, content, telegram_message_id, username, first_name, persona, generation_id (preserve or recompute md5), creator_id str}` | `created_at` in `messages` DB row, `evaluated_at = now UTC` in ledger | `requeue_stalled_messages:1002 XAUTOCLAIM idle 30s` re-claims; `inbound_dedup:{c}:{u}:{tgId}` SET NX reservation `INBOUND_RESERVE_TTL=120` → confirm Lua to `1` TTL 3600, duplicate → `duplicate:{tgId}` suppress; `debounce:creator:{c}:user:{u}:lock/messages/owner` Lua fenced consume |
| `SEND_STREAM=send_messages` | `commerce/opportunity_execution.py:252 execute_sealed_offer` via `db/redis:66 enqueue_send` | `workers/send_worker.py` group `send_workers` (`read_send_messages:769`) | `payload+dedup_id=sealed:{offer_id}, generation_id, creator_id` | `opportunity_id.evaluated_at` | `send_dedup:{creator}:{dedup}` reserve `SEND_DEDUP_LEASE_TTL=300` token → confirm to `1` TTL 86400 via Lua; stable `send_random_id:{creator}:{dedup}` 128-bit for transport idempotency; `requeue_stalled_send_messages:862 XAUTOCLAIM` |
| `DLQ_STREAM=dead_letter_queue` | `db/redis:1029 move_to_dlq / 793 move_send_to_dlq` | DLQ replay `workers/*` `*_DLQ_REASON_*` (`send_error`, `post_send_persistence_failed`, `unknown_send_result`) | `message_id, reason, stream, payload JSON, worker_id` | `failure_timestamp` | ACK only after XADD succeeds (crash-safe), pending otherwise |
| Debounce window | `chatbotv2/handlers.py` `_wait_and_process` | Timer `debounce_consume:1266` Lua (check owner token → LRANGE → DEL) vs recovery `debounce_recovery_consume:1296` | `debounce:creator:{c}:user:{u}:messages` list RPUSH order | window `debounce_window_seconds=3` (`core/config.py:30`) + owner TTL window+300 | `DebounceConsumeError` leaves buffer intact for recovery |
| `generation_telemetry` DB row | `workers/llm_worker.py:784 start_generation` + `core/telemetry.py` | `commerce/generation_trace.py:1118 SELECT generation_telemetry WHERE generation_id=$1 AND creator_id=$2` | `generation_id+creator_id PK, runtime_mode, latencies, persona_version, context budget` | `created_at` | Not a queue; read-only renderer |
| `fangate_transactions` poll | `integrations/dropfans/service.reconcile_sales` | `commerce/reconciliation.reconcile_all` via scheduler | `transaction_id, product_id, creator_id` | `occurred_at` | 7-day lookback `BATCH_SIZE 50` |
| `scheduled_messages` poll | `workers/scheduler_worker` | same worker claims `claimed_at/claimed_by` → enqueue `INBOUND_STREAM` as re-engagement | `creator_id, user_id, product_id` | `execute_at, claimed_at` | `dedup_key` unique on `pending|processing`, advisory lock |

**Required for optimizer observations:** `INBOUND_STREAM` (opportunity creation), `generation_telemetry` + `commerce_opportunity_decisions` (evidence/outcome persistence), `SEND_STREAM` dedup (exposure proof), `fangate_transactions` polling (purchase source). Simulator needs none of the Redis streams if it injects at D.

---

## Database / Persistence Inventory (optimizer-relevant)

| Object | Purpose | PK | FK | Creator | Fan | Opportunity ID | Timestamps | Status/State | Constraints |
|--------|---------|----|----|---------|-----|----------------|------------|--------------|-------------|
| `users` | Fans | `id BIGINT PK` | — | via `messages` | itself | via `commerce_opportunity_decisions.user_id` | `first_seen, last_seen` | `funnel_stage, is_blocked, do_not_auto_reply` | |
| `messages` | Full audit log | `id BIGSERIAL PK` | `user_id→users` | `creator_id BIGINT` (P1.3a required) + `idx_messages_creator_generation WHERE NOT NULL` + `UNIQUE (creator_id,dedup_id)` | `user_id` | `generation_id TEXT` | `created_at, sent_at` | `direction inbound/outbound` | |
| `commerce_opportunity_decisions` | **Opportunity ledger (P3.5.1 + 3B)** | `opportunity_id BIGSERIAL PK` | `creator_id,user_id→users`, `sealed_offer_id→commerce_offers ON DELETE SET NULL`, `reengagement_of→self` | `creator_id BIGINT NOT NULL` | `user_id BIGINT NOT NULL` | itself PK | `evaluated_at NOT NULL, outcome_at, exposure_at, created_at, updated_at` | `decision_status {NO_OPPORTUNITY,NO_SELECTION,DECIDED,SEALED,SEAL_FAILED,REENGAGED}`, `outcome_state {PENDING,SENT,SEND_FAILED,PURCHASED,DECLINED,EXPIRED,CLICKED_NO_PURCHASE,REVOKED,REENGAGED,SEAL_FAILED,NO_OPPORTUNITY,NO_SELECTION}`, `exposure_state {NONE,SEND_ATTEMPTED,SENT} NOT NULL DEFAULT 'NONE'`, `attribution_status`, `confidence` | `UNIQUE (creator_id,generation_id) WHERE NOT NULL`, `UNIQUE (creator_id,user_id,sealed_offer_id) WHERE gen NULL & sealed NOT NULL` (re-engage), 5 indexes (def, outcome, sealed, reengagement) |
| `commerce_offers` | Sealed PPV snapshot (authoritative for send) | `id BIGSERIAL PK` (synthetic `product_id`) | — | `creator_id` | `user_id` | `transaction_id` (purchase) | `created_at, purchased_at` | `state pending/clicked/purchased/declined/expired/revoked` | `UNIQUE (creator_id,transaction_id) WHERE NOT NULL` (`idx_commerce_offers_creator_txn`), `vault_item_ids TEXT[], media_count, drop_content_hash, dropfans_product_id` (+ GIN), `reason JSONB` envelope v1 |
| `fangate_products` (mirror) | Fangate + Dropfans products | `id BIGINT PK (synthetic)` | `creator_id→creators` | `creator_id` | — | `product_id` for attribution | `created_at, synced_at` | `is_accessible, price_minor` | `UNIQUE (creator_id,dropfans_product_id) WHERE NOT NULL` |
| `fangate_transactions` | Dropfans sales mirror | `id BIGSERIAL` or `transaction_id` PK? | `creator_id` | `creator_id` | `user_id nullable` (unattributed until reconciled) | `product_id, transaction_id` | `occurred_at, created_at` | `event_type=dropfans_sale` | unbounded mirror, polled |
| `commerce_offer_definitions` | P3.3.5 commercial definitions | `id BIGSERIAL PK` | `creator_id→creators, family_id→families ON DELETE SET NULL` | `creator_id` | — | `(creator,stable_key,version) UNIQUE` + `UNIQUE (creator,stable_key) WHERE active` | `created_at, updated_at` | `status draft/active/retired`, `offer_type SINGLE/SMALL_BUNDLE/CORE_BUNDLE/PREMIUM`, `canonical_vault_item_ids[1..10]`, `price_minor>=0, currency, allow_download` | |
| `commerce_offer_definition_drops` | Def↔Drop mapping | `PK (creator_id, dropfans_product_id)` | `(definition_id,creator)→definitions` | `creator_id` | — | `definition_id, definition_version` | `created_at` | — | — |
| `commerce_content_families` | Content families | `id BIGSERIAL PK` | `creator_id→creators` | `creator_id` | — | `family_id` | `created_at` | | `UNIQUE (creator_id,slug), (id,creator_id)` |
| `dropfans_drop_intents` | Drop creation intent crash-window | `id BIGSERIAL PK` | `creator_id→creators` | `creator_id` | — | `content_key` (== drop_content_key hash) `UNIQUE (creator_id,content_key)` | `created_at, updated_at` | `status pending/active/failed, dropfans_product_id` | |
| `vault_media_index` | P3 vault index | `vault_item_id PK per creator?` | — | `creator_id` | — | — | `created_at` | moderation/file_type | |
| `generation_telemetry` | Per-generation observability | `generation_id+creator_id` | — | `creator_id` | `user_id` | `generation_id` | `created_at` | `runtime_mode, provider_name, latencies, persona_version, budget` | — |
| `scheduled_messages` | Follow-up scheduling (P2.1) | `id BIGSERIAL PK` | `users.id` | `creator_id BIGINT` | `user_id` | `dedup_key UNIQUE WHERE pending|processing` | `execute_at, claimed_at, created_at, completed_at` | `status pending/processing/completed/failed/cancelled` | |
| `dlq_messages` | Dead-letter for failed inbound | `id BIGSERIAL PK` | — | via payload | via `user_id` in `message_data` | `original_stream_id` | `enqueued_at` | `failure_reason` | |
| `operator_queue` | Operator review | `id BIGSERIAL PK` | `users.id` | `creator_id` | `user_id` | `generation_id, queue_id` | `created_at, resolved_at` | `status pending` | `idx_operator_queue_creator_status` |
| `optimizer datasets/runs/artifacts` | **NONE** — no table; in-memory `OfflineModel:336`, `CreatorDataset:324`, `TrainingOutcome:352`, `EvaluationReport:400` | — | — | per `model.creator_id` | — | `n_train, n_pos/n_neg, prior_log_odds, feature_llr` | `train_min/max_evaluated_at` | `abstained, abstain_reason` | `FEATURE_SCHEMA_VERSION="p356.features.v1"`, `OPTIMIZER_VERSION="p356.offline.proto.v1"` |

---

## Existing Tests

| Test file | Count | What it proves | What it does NOT prove |
|-----------|-------|----------------|------------------------|
| `tests/test_p35_6_offline_optimizer.py` | 70 | Feature extraction frozen-only, label taxonomy, creator isolation, chronological holdout/expanding window, training abstain floors 6/2/2, prediction creator/scheme/recovered/child/experience abstain, extrapolative warning, import barrier `offline_optimizer` not in `commerce/*.py` | No production DB, no real Dropfans, no performance under load |
| `tests/test_p36_optimizer_readiness.py` | 35 | `diagnose_row` per-row eligible, floors, holdout feasibility, anomalies `maturity_policy_mismatch/sent_without_linkage`, synthetic/recovered quarantine, parity `FEATURE_NAMES` mirrors, `INPUT_UNAVAILABLE` distinct from 0 | Not a commercial sufficiency claim (doc  `prototype-only diagnostic floor`) |
| `tests/test_p35_4b_opportunity_validation.py` | 35 | 7-gate order, VALID/INVALID/NOT_ELIGIBLE/REJECTED/ABSTAIN, governance `UNEVALUABLE_FROM_HISTORY`, provider truth via `sealed_offer_id`, `NO_V1_SELECTION` | Historical only; no live governance check |
| `tests/test_p35_4a_optimization_input.py` | 41 | `build_optimization_input` point-in-time, forbidden fields dropped, isolation, policy_version carry | — |
| `tests/test_p35_3b_evidence_maturity.py` | 45 | `classify_opportunity_evidence` exposure rungs, maturity `168h` pinned equality, label gating, censored never negative, `as_of` naive raises | — |
| `tests/test_p35_1_attribution_ledger.py` | 43 | Ledger decision insert idempotent `ON CONFLICT (creator,generation)`, snapshot immutable, seal/send/purchase terminal non-overwrite | — |
| `tests/test_p35_2_0_single_winner.py` | 17 | Lowest `opportunity_id` wins, fallback re-engagement, duplicate txn returns existing, `attribute_purchase_safe` spare-row guard | — |
| `tests/test_p35_2_orphan_recovery.py` | 37 | `plan_recovery` + `recovered:` deterministic generation, `ON CONFLICT DO NOTHING` + re-read, `recovery:{c}:{offer}` lock, never calls `record_purchase_by_offer` on spare | — |
| `tests/test_p33_opportunity_engine.py` | 26 | `evaluate_opportunity` hard gates + ranking v1, no candidate → NO_CANDIDATES etc. | — |
| `tests/test_p33_opportunity_sealing.py` | 44 | Two-step verify 8 gates, advisory lock `seal:{c}:{u}:{d}:{v}:{CUID}`, synthetic `product_id` from CUID hash | — |
| `tests/test_p33_opportunity_ranking.py` | 36 | Lexicographic v1 policy + factor vocab | — |
| `tests/test_p33_ownership_overlap.py` | 18 | ZERO/PARTIAL/FULL/INVALID classification, never sliced | — |
| plus `test_p31/32/*`, `test_phase*`, `test_product_selection` etc. total >500 | — | Commerce safety foundation, vault, context engine, canary, retrieval | — |

Forensic gap: **No `tests/test_shadow_optimizer.py`** yet; no timeout, store, worker wiring tests (see § Phase 1 requirements for needed next).

---

## Reusable Components

### SAFE TO REUSE (pure, no side effects — simulator SHOULD reuse)

| Component | File:Symbol | Why safe |
|-----------|-------------|----------|
| `build_optimization_input` | `commerce/opportunity_optimization.py:748` | Pure (no I/O, frozen snapshot only), point-in-time guarantee; same for `build_frozen_candidate:437`, `build_fan_summary:494`, `build_history_summary:555`, `build_conversation_context:629`, `build_evidence_context:651`, `build_reengagement_context:691`, `_parse_snapshot:734` |
| `classify_opportunity_evidence` | `commerce/opportunity_evidence.py:208` | Pure except 2 bounded SELECT helpers; classification is pure |
| `build_supervised_label` | `commerce/offline_optimizer.py:450` | Pure, closed vocab → never pools |
| `extract_features` | `commerce/offline_optimizer.py:687` | Pure, 16 buckets, no future |
| `make_training_example` | `commerce/offline_optimizer.py:774` | Pure, isolation-checked |
| `build_creator_dataset` | `commerce/offline_optimizer.py:840` | Pure, chronological sort |
| `train_creator_model` | `commerce/offline_optimizer.py:896` | Pure, Laplace prior + LLR, abstains |
| `predict_for_input` | `commerce/offline_optimizer.py:989` | Pure, abstains on recovered/child/creator mismatch |
| `validate_advisory`, `select_advisory_candidate`, `compare_with_v1` | `commerce/opportunity_validation.py:133,189,433` | Pure, gate-ordered |
| `diagnose_row`, `summarize_rows`, `maturity_policy` | `commerce/optimizer_readiness.py:385,543`, `commerce/opportunity_evidence.py:199` | Pure |
| `run_dry_run` | `commerce/opportunity_dry_run.py:156` | Bounded SELECT, pure classification |
| `canonical_identity_ids`, `drop_content_hash`, `drop_content_key` | `commerce/vault_sets.py:35,80,105` | Pure, deterministic |
| `verify_live_drop`, `dollars_to_price_minor` | `commerce/drop_reconciliation.py:281,265` | Pure (live payload validation) — safe for synthetic verification without network |
| `rank_candidates` (if simulator needs deterministic oracle) | `commerce/opportunity_ranking.py:338` | Pure (but simulator should not rank for learning — only to synthesize v1 selections) |

### ADAPTER REQUIRED (useful but coupled — needs shim)

| Component | Coupling | Adapter |
|-----------|----------|---------|
| `fetch_opportunity_rows` / `assess_readiness` / `run_dry_run` SELECTs | `db/postgres.get_pool` | Simulator can call them after inserting synthetic ledger rows (needs pool); for in-memory sim, use `summarize_rows([dict,...])` pure variant instead |
| `record_opportunity_decision` etc. ledger writers | `db/postgres` INSERT + pool | For synthetic injection, insert synthetic ledger rows directly (synthetic generation prefix) without calling production writers' generation flow; reuse `_jsonable`/`candidate_identity_snapshot` helpers |
| `resolve_offer_definitions` / `FanCommercialState` | DB DAO + OfferDefinition table | For synthetic candidate synthesis, construct `ResolvedDefinition` tuples in-memory rather than hitting `commerce_offer_definitions` |

### MUST MOCK (touches Telegram/external — never call in sim)

| Component | File | Mock |
|-----------|------|------|
| `chatbotv2/main.py` Telethon client, `workers/send_worker` Telethon send | — | Do not import; simulator never sends |
| `integrations/dropfans/service.get_drop`, `service.reconcile_sales` | `integrations/dropfans/service.py` | Mock `verify_live_drop` payload dict instead of network; do not call `create_drop` |
| `integrations/fangate` product creation | `integrations/fangate/service.py` | Do not create real products |
| `core/llm_provider.get_llm_provider().generate` | `core/llm_provider.py` | LLM not needed for optimizer (features never include LLM floats per `opportunity_ranking:18` exclusion) — mock or skip |
| `db/redis.enqueue_send / read_inbound` | `db/redis.py` | Do not enqueue; simulator's exposure is `exposure_state=SENT` column, not Redis queue |
| `workers/llm_worker.process_message` (debounce lock, profile persist) | — | Do not invoke whole worker; inject at D |

### MUST NOT TOUCH (real-world side effects)

```text
real Telegram sends          workers/send_worker → Telethon API
real purchases               fangate_transactions / Dropfans provider / seller_earning
real creator accounts        creators table (encrypted keys)
real fan accounts            users table (real ids) — use synthetic creator/fan ids in synthetic rows (marked synthetic:), never 1/2
production execution         commerce/execution.execute_ppv (legacy) and commerce/opportunity_execution.execute_sealed_offer with real CUID
production sealing with live Drop  commerce/opportunity_sealing.seal_ranked_candidate with live get_drop (would POST/GET provider)
```

Simulator identities must be **synthetic namespace** (`synthetic:{run_id}:{opportunity_id}` generation, `creator_id = 90000+` synthetic range) and **NEVER enter `UNAVAILABLE→0` path**; readiness already excludes `synthetic:` from primary (`readiness:147`).

---

## Production Side Effects

| Side effect domain | Mutates | Risk | Boundary rule |
|--------------------|---------|------|---------------|
| `messages` (audit log) | INSERT inbound/outbound | Fan history leak | Do not INSERT synthetic messages; synthetic evidence is ledger-only |
| `user_profiles` / `user_persona` | facts JSONB, embeddings | Profile leakage to future | Do not touch |
| `commerce_offers` | sealed snapshot | Provider truth | Do not INSERT with live `dropfans_product_id` unless synthetic CUID `synthetic_drop:{run}` and never `offer.link` real |
| `fangate_transactions` | sale mirror | Revenue | Do not INSERT fake `dropfans_sale` for production creators |
| `vault_media_deliveries` | delivery | Entitlement | Never |
| `scheduled_messages` | follow-up | Scheduler fanout | Never enqueue synthetic inbound |
| Redis `send_dedup`, `send_random_id`, `send_unknown`/`send_repair` | dedup idempotency | Duplicate suppression | Never write for synthetic dedup_id (creator-scoped) |
| OfferDefinition | catalog | Commercial definition | Do not `create_offer_definition` for synthetic creator via DAO that triggers cache invalidate (can, but keep version 1 trivial) |
| Telemetry `generation_telemetry` | observability | Dashboard pollution | Simulator should write to separate `simulation_*` tables or in-memory `EvaluationReport`, not `generation_telemetry` for production creators |

**Hard invariants to preserve in sim:** 1 eligibility > optimizer, 2 ownership > optimizer, 3 provider truth/sealing > optimizer, 4 governance > optimizer, 5 v1 valid, 6 advisory unless authorized, 7 no price/currency/Vault/Drop/URL/ownership/eligibility/sealing authority, 8 unselected ≠ negative, 9 no pooled creator, 10 don't weaken 168h, 11 synthetic ≠ REAL, 12 no causal/price claims.

---

## Recommended Simulator Boundary

**Choice:** **D. Opportunity/evidence persistence layer** — inject synthetic rows that satisfy the same legitimate contracts as production rows, consumed by the same `build_optimization_input` / `classify_opportunity_evidence` / `build_supervised_label` path.

**Why not other boundaries:**

| Boundary | Where prod data enters | Where optimizer state becomes authoritative | Where sim could inject | Must stay untouched | Verdict |
|----------|------------------------|--------------------------------------------|------------------------|---------------------|---------|
| **A. Telegram/Telethon** | `chatbotv2/handlers.py` Telegram update → `enqueue_inbound` | After `evaluate_opportunity` (ledger) — Telethon is before debounce/lock/dedup, far from optimizer | Would need to mock Telethon + Redis `INBOUND_STREAM` + `debounce` + `acquire_user_lock` + `process_message` debounce collapse — over-simulates chat infra unrelated to optimizer, risks real `send_messages` on failure | Full `workers/llm_worker` + `send_worker` + `chatbotv2/main` | **Reject** — couples to most volatile, non-commerce concerns |
| **B. Redis/event layer** | `db/redis.enqueue_inbound` → `INBOUND_STREAM` → `read_inbound` → `process_message` | `commerce_opportunity_decisions` after `evaluate_opportunity` | `XADD inbound_messages` synthetic JSON; would still traverse `requeue_stalled_messages XAUTOCLAIM`, `inbound_dedup`, `debounce`, `user_lock` — replicates distributed failure modes irrelevant to optimizer; risks leaking into `SEND_STREAM` → real Telegram via `send_worker` | `db/redis` streams, debounce Lua, LDQ, lot of redis infra | **Reject** — no optimizer benefit, high production blast risk |
| **C. Application/domain event** | Still before ledger; `conversation_state` + `fan_commercial_state` assembled in `process_message` before engine | `OptimizationInput` construction from ledger + evidence | Would require calling `evaluate_opportunity` with mocked `FanCommercialState/OfferHistory/ConversationState` then sealing/execution — re-creates commerce path; duplicates engine logic already tested; still needs to decide when synthetic purchase fires | `commerce/opportunity_engine` live DAO (fan state, offer history) — but could be mocked | **Possible but second-best** — more moving parts than D, still needs to fake `decision_snapshot` like D does |
| **D. Opportunity/evidence persistence** ✅ | **`record_opportunity_decision` INSERT `commerce_opportunity_decisions` with frozen `decision_snapshot`** — this is where optimizer-relevant state becomes authoritative (ledger row is the evidence bus: snapshot + selection + timestamps + `sealed_offer_id` + `outcome` + `exposure`) | Same: ledger row is what `fetch_opportunity_rows` / `build_optimization_input` / `classify_opportunity_evidence` read — optimizer never reads `messages` or Redis directly, only ledger + `commerce_offers` + `fangate_transactions` mirror via reconciliation | **Insert synthetic `commerce_opportunity_decisions` rows** with `generation_id = synthetic:{run}:{opportunity_id}`, `creator_id = synthetic_creator (90000+)`, `decision_snapshot` containing eligible/ineligible/selected + ranking, `evaluated_at`, `selected_*/sealed_offer_id/synthetic CUID`, `outcome_state/outcome_at/transaction_id` synthetic, `exposure_state=SENT` synthetic, all `creator_id` scoped | No `messages`, no Redis streams, no `send_messages`, no `scheduled_messages`, no real `fangate_products`, no Telethon | **Choose** — minimal, contract-faithful, validates same `build_optimization_input` path, `requires production runtime/database verification` not needed for sim isolation |
| **E. Optimizer dataset layer** | `build_creator_dataset` already in-memory `CreatorDataset` | `TrainingExample` — but bypasses persistence validation | Construct `RowBundle(input, evidence, ledger_row)` in-memory without DB — fastest but **skips contract checks** (`_check_input_features`, `is_synthetic_row` quarantine, `maturity_policy` anomaly, ledger constraints); would not prove that synthetic rows would have been accepted by real `assess_readiness` or `run_dry_run` in prod | No validation of `record_opportunity_decision` constraints, no `ON CONFLICT` dedup proof | **Reject as sole boundary** — useful for unit-level harness, but Phase 1 must be able to produce **persisted synthetic ledger** that `fetch_opportunity_rows` would return in prod |

**Injection summary (D):**

1. Production data enters: `workers/llm_worker.py:1512 record_opportunity_decision` → `commerce_opportunity_decisions` (frozen snapshot is authoritative).
2. Optimizer state authoritative: that ledger row + sibling `exposure` + `outcome` + `as_of`-gated evidence classification.
3. Sim injection: `simulation_run` creates `SimulationOpportunity` rows mapped to `commerce_opportunity_decisions` columns, inserted with `synthetic:` generation prefix and synthetic creator/fan/product IDs; maturity/outcome/attribution follow real windows (168h, single-winner) but with simulated timestamps.
4. Untouched prod paths: Telegram ingress, Redis `INBOUND/SEND` streams, `debounce`, `user_lock`, `send_worker` Telegram send, real `fangate_transactions` poll, real `commerce_offer_definitions` mutation for prod creators — **all remain untouched**.

---

## Dependency / Call Graph (optimizer data lifecycle, real paths)

```text
chatbotv2/handlers.py:on_message
  → db/redis.py:debounce_enqueue(user_id, creator_id)  [fence token]
    → db/redis.py:enqueue_inbound({user_id, content, telegram_message_id, generation_id, creator_id})  [inbound_dedup SET NX → XADD inbound_messages]
→ workers/llm_worker.py:process_message(user_id, user_message, telegram_message_id, generation_id, creator_id)
  → core/generation.py:ensure_generation_id / telegram_generation_id
  → context_engine/authoritative_assembly:assemble_authoritative_context(creator_id, user_id, current_message, generation_id)
  → core/conversation_state:derive_conversation_state(context)
  → commerce/opportunity_engine.py:evaluate_opportunity(creator_id, user_id, conversation_state, now=now UTC)
      → commerce/fan_commercial_state:get_fan_commercial_state(creator_id, user_id)  [SELECT users funnel, commerce_offers history]
      → commerce/offer_history:get_offer_history(creator_id, user_id)
      → commerce/offer_definition_resolver:resolve_offer_definitions(creator_id)
        → db/offer_definitions:list_offer_definitions / list_offer_definition_drops
      → commerce/opportunity:candidate_from_definition(definition, mapped_drop_ids)  [pure]
      → commerce/opportunity_eligibility:evaluate_opportunity_eligibility(candidate, owned_vault_ids, offer_history)  [pure, hard]
      → commerce/opportunity_ranking:assemble_ranking_input(candidate, verdict, fan_commercial_state, offer_history, conversation, evaluated_at)
      → commerce/opportunity_ranking:rank_candidates([inputs], evaluated_at)  [lexicographic]
        → OpportunityEngineResult(has_opportunity, selected_candidate)
  → db/redis.py:acquire_user_lock(creator_id,user_id) / ensure_consumer_group
  → commerce/opportunity_ledger.py:record_opportunity_decision(creator_id, user_id, generation_id, evaluated_at, opportunity_result, conversation_state)
      → db/postgres.py:get_pool → INSERT INTO commerce_opportunity_decisions ... ON CONFLICT (creator_id,generation_id) DO NOTHING RETURNING *
        → (creator_id, generation_id) PK guard + decision_snapshot JSONB = candidate_identity_snapshot(eligible/ineligible/selected/ranking)
  → commerce/opportunity_sealing.py:seal_ranked_candidate(candidate, ranked_context, creator_id, user_id, chosen_drop_cuid)
      → integrations/dropfans/service:get_drop(creator_id, CUID)  [creator-scoped Fernet Bearer]
      → commerce/drop_reconciliation:verify_live_drop(payload) → VerifiedLive
      → commerce/opportunity_sealing:verify_candidate_against_live(candidate, chosen, verified)  [8 gates]
      → db/postgres: BEGIN TX → pg_advisory_xact_lock(hashtextextended(seal:{c}:{u}:{d}:{v}:{CUID}))
      → re-GET+re-verify inside TX → INSERT INTO commerce_offers (...) (reason envelope v=1, vault_item_ids, hash, dropfans_product_id)
  → commerce/opportunity_execution.py:execute_sealed_offer(seal_result, creator_id, user_id, generation_id)
      → db/redis:try_reserve_send_dedup(creator_id, sealed:{offer_id}) SET NX token
      → db/redis:enqueue_send(SEND_STREAM, payload, dedup_id, generation_id, creator_id)  [XADD send_messages]
      → db/redis:release_send_dedup (crash-safe, not confirm)
  → commerce/opportunity_ledger:record_opportunity_send(opportunity_id, sealed_execution)  [UPDATE exposure_state=SENT/exposure_at]
  → commerce/opportunity_ledger:link_opportunity_seal(opportunity_id, seal_result, drop_cuid)  [UPDATE decision_status SEALED]
  → core/telemetry.py:GenerationTelemetry.start/complete → generation_telemetry INSERT

… async outcomes …

workers/scheduler_worker:reconcile_purchases()
  → commerce/reconciliation.py:reconcile_all()
    → commerce/reconciliation:reconcile_dropfans_sales()  [integrations/dropfans/service.reconcile_sales polling per dropfans_creator_id, INSERT fangate_transactions]
    → commerce/reconciliation:reconcile_unattributed_purchases()  [SELECT fangate_transactions WHERE user_id IS NULL 168h limit, _reconcile_single: disambiguate → mark_offer_purchased → attach_transaction_user → record_offer_transition → commerce/opportunity_ledger:record_purchase_by_offer single-winner]
    → commerce/opportunity_recovery:recover_orphan_decisions(limit 25) / recover_orphan_offer(recovery:{c}:{offer} lock)
  → commerce/opportunity_ledger:record_offer_terminal_outcome(creator_id, offer_id, DECLINED/EXPIRED/REVOKED/...)  [UPDATE outcome_state WHERE PENDING/SENT/SEND_FAILED]

… optimizer observability (read-only) …

commerce/opportunity_evidence.py:classify_opportunity_evidence(row, as_of=now)  [pure: exposure→maturity→label→quality]
  → commerce/opportunity_optimization.py:build_optimization_input(ledger_row, evidence, offer_exposures)  [pure, frozen only]
    → commerce/offline_optimizer.py:extract_features(OptimizationInput)  [16 buckets]
      → commerce/offline_optimizer.py:make_training_example(input, evidence, ledger_row)  [excluded unless PRIMARY_TRAIN_LABELS]
        → commerce/offline_optimizer.py:build_creator_dataset(creator_id, bundles=[RowBundle], as_of)  [creator-isolated, chronological sort, ValueError on cross-creator]
          → commerce/offline_optimizer.py:train_creator_model(dataset)  [min 6/2/2 else abstain, Laplace prior α=1.0, LLR per (feature,value)]
            → OfflineModel(creator_id, feature_schema_version, optimizer_version, ranking_policy_versions, n_train/n_pos/n_neg, prior_log_odds, feature_llr sorted, train_min/max_evaluated_at)
              → commerce/offline_optimizer.py:predict_for_input(model, input)  [abstain on recovered/child/mismatch]
                → commerce/offline_optimizer.py:prediction_to_advisory(input, prediction)  [sole selected scored, abstain→abstain_result]
                  → commerce/opportunity_validation.py:validate_advisory(input, advisory, sealed_offer_id, ranked_order)  [7 gates]
                    → commerce/opportunity_validation.py:compare_with_v1(input, advisory_candidate)  [AGREEMENT/DIVERGENCE/NO_V1_SELECTION]
→ commerce/optimizer_readiness.py:fetch_opportunity_rows(creator_id, limit 500 cap 2000)  [SELECT _READINESS_COLUMNS WHERE creator_id=$1 ORDER BY opportunity_id]
  → commerce/optimizer_readiness.py:diagnose_row(row, as_of)  [evidence + build_optimization_input + _primary_eligibility + anomalies]
    → commerce/optimizer_readiness.py:summarize_rows(rows, as_of)  [per-creator floors, holdout_feasibility, multiple_purchase_offers duplicate guard]
      → GlobalReadiness
→ commerce/opportunity_dry_run.py:run_dry_run(creator_id, limit 25)  [bounded SELECT + _with_advisor per row → advisor(input) → validate_advisory → compare_with_v1]

File paths all rooted E:\chatbot\ .
```

---

## Documentation vs Implementation Contradictions

| Claim | Actual behavior | Evidence | Impact |
|-------|-----------------|----------|--------|
| `docs/OPTIMIZER_SIMULATOR_FORENSIC_REPORT.md` does not exist yet → new | True (only requested modification) | `glob docs/*.md` | None — this audit creates it |
| Architecture doc says optimizer consumes `OptimizationInput` frozen contract, v1 remains authority, 168h maturity, single-winner, re-engagement separate | Matches code | `opportunity_optimization:10` invariant + `opportunity_evidence:83=168` + `opportunity_ledger:687` single-winner `ORDER BY opportunity_id` | No contradiction — align |
| Tests claim `family_presence` is suppliable | Implementation always `NO_FAMILY` | `optimizer_readiness:131 degraded always NO_FAMILY` vs `offline_optimizer:716 HAS_FAMILY branch` unreachable because `candidate_identity_snapshot:150` omits `family_id` | Low impact — constant bucket, not leaked, but simulator should not synthesize `HAS_FAMILY` until snapshot captures it (would diverge from prod) |
| `README`-style handoff says P3.6 offline/readiness complete | Ledger/evid/OptimizationInput/validator/offline_optimizer/readiness all exist and are registered in `chatbotv2.dashboard.app:37 startup verify_schema + verify_commerce_safety_schema + check_migrations_pending` | `docs/*` handoff + `app:37` checks + 37 migrations | No contradiction — but dirty working tree `?? commerce/optimizer_readiness.py` shows untracked vs committed state divergence (deploy ordering matter) |
| No thresholds claim commercial sufficiency | Code `MIN_TRAIN_*=6/2/2` doc `prototype-only diagnostic floor, not claims of statistical sufficiency` (`offline_optimizer:174`) | Comments + `test_p36_optimizer_readiness` never asserts sufficiency | Align — do not infer deploy-ready from 6 rows |
| No synthetic-as-real claim | `offline_optimizer.py:99 SYNTHETIC_*` + `SYNTHETIC_MARKER="SYNTHETIC_P356_FIXTURE"` + `readiness:147 synthetic:` excluded, `offline_optimizer:94` guard | `grep SYNTHETIC` 12 hits all `never production evidence` | Align |

**No material contradictions found** where code silently diverges from intended architecture; deprecation of legacy `commerce/execution.execute_ppv` in favor of opportunity sealing is documented and enforced by quarantine.

---

## Unknowns / Needs Verification

```text
## Unknowns / Needs Verification

1. Production DB row counts: n_creators, n_opportunity_decisions, n_full_mature_sent, n_primary/pos/neg per creator — requires production runtime/database verification (no live DB in forensic env; pg DSN in .env.example not populated with staging host).

2. Live Dropfans Drops per creator: how many OfferDefinitions are active vs how many CUIDs map per definition (SINGLE vs OTHER drop_mapping bucket distribution) — requires Dropfans mirror query on staging/prod (dropfans:DropfansProduct).

3. Purchase velocity: expected mature purchases/week to reach floors — requires product traffic + price point + Dropfans reconciliation interval (120s) confirmation with scheduler logs.

4. Conversation lifecycle source of truth: lifecycle mapping for has_active_offer timing vs fan state at evaluated_at — verify fan_commercial_state queries don't window beyond evaluated_at for recent_offer_count (code caps at now; audit reads as snapshot, needs runtime trace).

5. LLM path impact: whether llm_path="new" (Qwen) candidate summary ever enters decision_snapshot ranking path — audit shows no LLM floats enter ranking (rank_candidates excludes them), but one-call prompt full context not re-audited for price mention leakage.

6. GenerationTelemetry retention: how long generation_telemetry rows retained vs 168h maturity backfill — whether old opportunities' telemetry prunes before maturity assessment.

7. Vault stale reservation window (5m) interaction with send_unknown evidence — verify reaper does not reap a sealed reservation before exposure_at persists (send_unknown scan exists but not audited end-to-end for every offer).

8. Multiple scheduled offerings per fan overlapping evaluated_at windows — concurrency of funnel_stage and candidate creation under debounce (3s) + user_lock TTL (300s) needs non-unit execution trace with real Redis (workers/llm_worker load tested only in unit tests test_p1_lock_contention etc., not live).

9. Exact Fangate vs Dropfans transaction_id uniqueness cross-provider — idx_commerce_offers_creator_txn is creator-scoped unique, but fangate_transactions PK not re-audited for cross-creator duplicate guard under synthetic ids.

10. Optimizer compute budget: per-decision wall-clock for extract_features + predict (scheduler not calling it) not profiled — synthetic horizon of thousands of ledger rows not bench-marked.
```

---

## Existing Tests — What Each Proves / Does Not Prove

See **Existing Tests** section map above for per-file. Global: `pytest` markers `unit` (no infra) vs `integration` (real Postgres+Redis) vs `live` (llama.cpp). Forensic audit run is unit-only (no DB in env).

Proves: immutability of decision_snapshot, single-winner deterministic `ORDER BY opportunity_id`, `ON CONFLICT (creator,generation)` idempotency, 8-gate provider verification ordering, `recovery:` prefix quarantine, synthetic exclusion, advisory lock non-blocking, debounced XL/Rx exactly-once.

Does not prove: live provider 2xx vs 429 vs 5xx per `integrations/dropfans/errors.py` classification under real `exp_backoff`, `pg_advisory_xact_lock` fairness under >10 concurrent sealers, cross-creator Redis `scan_iter` key-space under production key ring, Dropfans `allow_download` drift detection on live payload where media[10] truncated.

---

## Reusable Components (summary)

Full table in **Reusable Components** section above — headline: **SAFE TO REUSE** list (`build_optimization_input`, `classify_opportunity_evidence`, `extract_features`, `make_training_example`, `build_creator_dataset`, `train_creator_model`, `predict_for_input`, `validate_advisory`, vault helpers) is the simulator's kernel; it can import them as stdlib without infra. **ADAPTER REQUIRED** only for `fetch_opportunity_rows`/`get_pool` (inject synthetic pool or use pure `summarize_rows`). **MUST MOCK** Telethon/Dropfans/LLM/Redis streams. **MUST NOT TOUCH** real Telegram sends, real purchases, real creator `dropfans_drop_intents` POST, production `commerce_offers` for creator `id<90000`.

---

## Production Side Effects — Explicit Prohibited

Listed in **Production Side Effects** section — audit asserts every table in inventory already `creator_id` scoped, so synthetic writes under `creator_id >= 90000` (or `synthetic:` generation namespace) are physically partitioned from prod by PK uniqueness and `idx_opportunity_decisions_creator_generation WHERE NOT NULL`.

---

## Recommended Simulator Boundary — Justification (15.4)

**D. Opportunity/evidence persistence layer** (see § Recommended Simulator Boundary table for 1-4 justification). Injection shape:

```python
# synthetic injection (pseudo, never executed in this audit)
pool = await get_pool()
await pool.execute("""
  INSERT INTO commerce_opportunity_decisions
    (creator_id, user_id, generation_id, evaluated_at,
     decision_snapshot, selected_definition_id, selected_version, selected_stable_key,
     decision_status, sealed_offer_id, drop_cuid, outcome_state, outcome_at,
     transaction_id, attribution_status, attribution_confidence,
     exposure_state, exposure_at, exposure_source, reengagement_of)
  VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,'SEALED',$9,$10,'PURCHASED',$11,$12,'attributed','full','SENT',$13,'sealed_execution',NULL)
  ON CONFLICT (creator_id,generation_id) WHERE generation_id IS NOT NULL DO NOTHING
""", synthetic_creator, synthetic_user, f"synthetic:{run}:{i}",
     evaluated_at, json.dumps(decision_snapshot_with_eligible_selected_ranking),
     def_id, ver, key, sealed_offer_id, synthetic_cuid, outcome_at, f"txn:sym:{run}:{i}", exposure_at)
```

All other layers observed in `Dependency / Call Graph` stay untouched.

---

## Phase 1 Implementation Requirements (Simulation Contracts)

Phase 1 is **Simulation Contracts** — read-only control plane that makes synthetic data legitimate without touching prod paths. Based on actual conventions (`opportunity_ledger` creator-first, generation prefix, decision_snapshot JSONB, exposure/outcome columns, 168h maturity, single-winner, sync synthetic creator):

| Contract | Purpose | Producer | Consumer | Required fields | Optional fields | Identifier req | Timestamp req | Persistence req |
|----------|---------|----------|----------|-----------------|-----------------|----------------|---------------|-----------------|
| `SimulationRun` | Groups one synthetic horizon for reproducibility + `simctl` | `simulation/runner` (CLI `scripts/sim_*.py`) | `simulation/store`, `optimization datasets`, `evaluation report` | `run_id (uuid v4, PK)`, `created_at tz-aware UTC`, `creator_id synthetic range`, `seed: int`, `data_origin="simulation"`, `feature_schema_version`, `maturity_policy_version`, `n_opportunities, n_exposed, n_mature` | `description, git_commit, config_version, strategy_version` | `run_id TEXT PK`, `creator_id int 90000+` (or configurable synthetic namespace `synthetic_creator:` prefix) | `created_at` | `simulation_runs` table `IF NOT EXISTS` or in-memory manifest JSON for Phase 1 file-only variant (no DB migration in Phase 0; Phase 1 may add `2026092XXXXX_simulation_runs.sql` `CREATE TABLE IF NOT EXISTS simulation_runs` additive) |
| `SimulationEvent` | Logs what synthetic code did, per opportunity, for debuggability | `simulation/opportunity` synthesizer | `simulation/store`, test assertions | `run_id FK simulation_runs, opportunity_id (logical counter 1..N), generation_id = synthetic:{run}:{opportunity_id}, evaluated_at, exposure outcome, transaction_id synthetic if purchase` | `payload JSONB (frozen candidate snapshot snippet)` | `generation_id` globally unique via `synthetic:{run}:{id}`; mirrors prod `ON CONFLICT (creator,generation)` dedup | `evaluated_at tz-aware UTC`, `outcome_at` | Bounded JSONL `vault/simulation_runs/{run_id}/events.jsonl` or DB `simulation_events (run_id, opportunity_id, generation_id PK, evaluated_at)` |
| `SimulationClock` | Advances synthetic time to prove chronological split / maturity without wall-clock | `simulation/clock` (deterministic, not `time.time()`) | synthesizer, `SimulationRun` horizon, `assess_readiness` `as_of` | `run_id, started_at, step: interval (e.g., 6h), current_time, end_time, tick(opportunity_id) → evaluated_at` | `jitter` (deterministic PRNG from seed) | `clock_id = run_id` | All `evaluated_at` produced by `clock.tick`, never `datetime.now()` inside synthesizer (leak test) | In-memory, derived from `SimulationRun.seed` |
| `SimulationIdentity` | Prevents accidental identity collision with production | `simulation/identity` factory | All synthetic inserts | `synthetic_creator_id (>90000 or 200000+), synthetic_user_id (9000000+), synthetic_definition_id (90000+), synthetic_cuid synthetic_drop:{run}:{def}`, `synthetic_transaction_id txn:sym:{run}:{i}`, `generation_id synthetic:{run}:{id}` | `stable_key sym:{def}`, Vault `synthetic_vault:{run}:{i}` | `creator_id` not in `creators` FK? If FK enforced, either pre-insert synthetic creator placeholder (preferred) or keep FK `ON DELETE CASCADE` but synthetic creator violates — audit: `commerce_opportunity_decisions.creator_id REFERENCES creators(id)` **enforces FK** — simulator must either `INSERT INTO creators` synthetic rows (separate migration) or relax Phase 1 to in-memory `CreatorDataset` without DB FK (recommended for Phase 1). | — | `generators seeded` via `SimulationRun.seed` |
| `SimulationDataOrigin` | Tags synthetic rows as non-production for readiness/optimizer quarantine | `simulation/store` inserting ledger rows | `optimizer_readiness.diagnose_row (is_synthetic_row)`, `offline_optimizer.build_supervised_label (is_child/recovered/quality)`, `assess_readiness` synthetic quota | `data_origin="simulation"` enum stored as `decision_snapshot.ground_truth_reference?` or `generation_id synthetic:` prefix (existing mechanism) | `ground_truth_ref` if extra marking wanted | Must be detectable via `generation_id LIKE 'synthetic:%'` (existing `readiness:147 SYNTHETIC_GENERATION_PREFIX`) — no new column for Phase 1 | — | In ledger row `generation_id` prefix is authoritative; optional `decision_snapshot.data_origin` JSON key for explicitness |
| `GroundTruthReference` | Holds oracle outcome vs observed outcome for calibration evaluation | `simulation/oracle` (applies maturity window deterministically, not model) | `build_supervised_label` oracle, evaluation report | `opportunity_id, ground_truth_mature_positive bool, ground_truth_label (PURCHASED/DECLINED/EXPIRED per synthetic business outcome), evaluated_at, outcome_at, maturity_at = anchor+168h, as_of for evaluation` | `oracle_probability` if simulator is probabilistic (not required Phase 1 deterministic) | Same `opportunity_id/generation_id` as event | `maturity_at` computed, not persisted except in `simulation_events` JSON payload | JSONL payload `ground_truth: {label, mature, exposure}` |

**Required fields rule:** every synthetic ledger row must include `creator_id, user_id, generation_id (synthetic:), evaluated_at, decision_snapshot (eligible [FrozenCandidate shape], selected, ranking {policy_version, ranked_order, factors}), selected_definition_id/version/stable_key, decision_status, sealed_offer_id, exposure_state/at/source, outcome_state/at, attribution_status, reengagement_of NULL` — otherwise `build_optimization_input` will classify `INPUT_UNAVAILABLE` (quarantined, but purposely not primary).

**Timestamp rule:** `evaluated_at < outcome_at` and `exposure_at = evaluated_at` (or `+5m`) for SENT; `as_of = evaluated_at+169h` for mature read.

**Persistence rule for Phase 1 (no-prod-touch):** prefer **file-only** (`vault/simulation_runs/{run_id}/ledger_rows.jsonl` of dicts shaped for `classify_opportunity_evidence` + `build_optimization_input`) to exercise pure path with **zero DB migration**. When Phase 1 proves parity with bounded `SELECT`, graduate to additive migration `simulation_runs` + `simulation_events` with FK `ON DELETE CASCADE` to real `commerce_opportunity_decisions` synthetic partition (separate PR, after this audit).

---

## Phase 1 Acceptance Criteria

**This audit is complete only when these questions are answered (they are, see section pointers):**

| # | Question | Answer pointer | Verdict |
|---|----------|----------------|---------|
| 1 | What exact data enters the optimizer? | `OptimizationInput` frozen 16-field contract `commerce/opportunity_optimization.py:312` via `build_optimization_input:748` | Answered § OptimizationInput |
| 2 | Where is that data created? | `commerce_opportunity_decisions` INSERT by `record_opportunity_decision` in `workers/llm_worker.py:1512` → `commerce/opportunity_evidence.classify_opportunity_evidence` → `build_optimization_input` | Answered § Actual Optimizer Pipeline |
| 3 | What makes an opportunity observable? | `has_opportunity=true` (selected_candidate not None, `RANKED`/`DECIDED` path) → ledger row with `generation_id` exists | § Opportunity Lifecycle |
| 4 | What makes an observation mature? | `as_of >= max(evaluated_at, exposure_at, outcome_at)+168h` or terminal `as_of>=outcome_at`; open unresolved stays CENSORED | § Maturity + Optimizer Target |
| 5 | What makes positive? | Mature `SENT FULL recovered=false is_child=false attributed` `PURCHASED` with txn → `TRAIN_LABEL_PURCHASED binary1` | § Optimizer Target |
| 6 | What makes negative? | Same but `DECLINED/EXPIRED` COMMERCIAL_NEGATIVE → `TRAIN_LABEL_DECLINED/EXPIRED binary0`; `PROCESS_NEGATIVE` not negative | § Optimizer Target |
| 7 | Purchase attribution rule? | Creator-scoped `fangate_transactions` poll `168h` → conditional `UPDATE commerce_offers` → single-winner ledger `ORDER BY opportunity_id LIMIT1` + duplicate `transaction_id` idempotency (`opportunity_ledger:687` + `reconciliation:148`) | § Purchase Attribution |
| 8 | Info available at prediction time? | `OptimizationInput` table 15 fields frozen at `evaluated_at` (`FanCommercialSummary` snapshot, not post-decision) — see § OptimizationInput Contract | Answered |
| 9 | What is leakage? | Future `purchase/outcome/price/currency/live Vault/Drop/salesCount/LLM/msg post` — all blocked (see § Leakage Analysis) | Answered |
| 10 | Creator isolation? | `creator_id` first predicate everywhere, `build_creator_dataset:863` raise, `redis key creator:{cid}:`, per-model `OfflineModel.creator_id` | § Creator Isolation |
| 11 | Exact simulator boundary? | **D. Opportunity/evidence persistence layer** — synthetic `commerce_opportunity_decisions` with `synthetic:` generation | § Recommended Simulator Boundary |
| 12 | Which prod components reuse? | SAFE list (`build_optimization_input`, `classify_opportunity_evidence`, `extract_features`, `train/predict`, `validate_advisory`) | § Reusable Components |
| 13 | Which must mock? | Telethon, `integrations/dropfans.get_drop`, `fangate` create, LLM `get_llm_provider`, `db/redis` streams | § Reusable Components |
| 14 | Which must never invoke? | Real Telegram sends, real `fangate_transactions` for prod creator, real `commerce_offers` sealed with live CUID, prod `execute_ppv` | § Production Side Effects |
| 15 | Exact contract Phase 1 must establish? | `SimulationRun/Event/Clock/Identity/DataOrigin/GroundTruthReference` — see § Phase 1 Requirements table | Answered |
| 16 | What is still unknown? | 10 unknowns `requires production runtime/database verification` — see § Unknowns | Answered |

Phase 1 acceptance gate: produce contracts that let synthetic rows enter same `build_optimization_input → classify_opportunity_evidence → build_supervised_label → build_creator_dataset → train_creator_model → predict_for_input → validate_advisory` legitimate path without touching `messages`/`send_messages`/`fangate_transactions` prod streams, and prove via `assess_readiness` + `run_dry_run` bounded SELECT that synthetic `SENT FULL mature PURCHASED` are classified identically to would-be prod rows, while prod `synthetic:  → FLOOR_NOT_MET` quarantine holds.

---

## Verification Rules — Evidence Log

- Multiple implementations searched: `grep candidate.*identity` (snapshot vs live), `grep dollar.*price` (reconciliation vs sealing), `grep generation_id` (messages vs ledger vs telemetry) — all reconciled.
- Call sites verified: `grep evaluate_opportunity` (3 workers), `grep record_opportunity_decision` (1 worker), `grep offline_optimizer` (0 prod), `grep execute_ppv` (quarantine).
- Tests vs impl: `test_p35_6` 70 vs `offline_optimizer` lines 1-1400, `test_p36` 35 vs `optimizer_readiness` lines, `test_p35_4b` 35 vs `validate_advisory` 7 gates — all gate orders/quotas labeled trace to code lines cited.
- Migrations/schema checked: `db/schema.sql` baseline + `db/migrations` 37 files listed by `ls`; `commerce_opportunity_decisions` DDL read line-by-line.
- Config defaults: `core/config.py:1-169` `postgres_dsn` required no default, `redis_pending_idle_ms=60000`, `scheduler_poll_interval=10`, `debounce_window_seconds=3`, `autonomy_enabled=True`.
- Producer/consumer checked: `INBOUND_STREAM` producer `enqueue_inbound` vs consumer `read_inbound` + `XAUTOCLAIM`; `SEND_STREAM` producer `enqueue_send` vs consumer `read_send_messages` + `XAUTOCLAIM`; `DLQ_STREAM` producer `move_to_dlq` vs none.
- Not trusting comments: `offline_optimizer.py:32 NOT a persistent store` verified by `ls vault/optimizer*` absent + no `OfflineModel.save()` symbol; `autonomy_enabled` kill-switch verified via `grep autonomy` 3 hits.
- Actual execution path followed: `chatbotv2/main.py` → `db/redis.enqueue_inbound` → `workers/llm_worker.process_message` → `opportunity_engine` → ledger INSERT → sealing TX → Redis SEND → scheduler reconciliation → evidence classification → optimizer dataset — every hop with file:line.

---

## Output Summary

```text
AUDIT STATUS: COMPLETE

OPTIMIZER ENTRY: commerce/offline_optimizer.py:896 train_creator_model(CreatorDataset) / 989 predict_for_input(model, OptimizationInput) / chronological_holdout_evaluate; no prod training entry point; pure, stdlib-only, no persistence

OPTIMIZER TARGET: P(mature purchase | sent exposure) on creator-local chronological horizon, 16 categorical frozen features (price bucket not price), Laplace log-odds, abstention-first

OBSERVATION GRAIN: 1 commerce_opportunity_decisions row (opportunity_id, creator_id, generation_id, evaluated_at); re-engagement children are separate rows Option A

EXPOSURE DEFINITION: EXPOSURE_SENT = sealed execution recorded (sealed_execution) or purchase-entailed; NONE=DECISION, SEALED=sealed_offer_id set, SEND_ATTEMPTED=send tried unconfirmed, SENT=recorded send/execution (never delivery/click); durable columns exposure_state/at/source (20260919000000), monotonic

POSITIVE DEFINITION: mature (p353b.v1 168h) + exposure SENT + evidence_quality FULL + recovered false + is_child false + attribution_status attributed + transaction_id non-empty + label POSITIVE (outcome PURCHASED) → TRAIN_LABEL_PURCHASED binary1; synthetic: synthetic: prefix excluded from primary

NEGATIVE DEFINITION: same maturity/exposure/quality/recovered/child + label COMMERCIAL_NEGATIVE (outcome DECLINED or EXPIRED) → TRAIN_LABEL_DECLINED/EXPIRED binary0; PROCESS_NEGATIVE (SEAL_FAILED/REVOKED/CLICKED_NO_PURCHASE or mature SEND_FAILED) is excluded never negative; CENSORED/UNAVAILABLE/NO_SELECTION/NO_OPPORTUNITY/PARTIAL/UNATTRIBUTED/child/synthetic never 0

MATURITY DEFINITION: MATURITY_POLICY_VERSION=p353b.v1, MATURITY_WINDOW_HOURS=168 (= RECONCILIATION_WINDOW_HOURS 7*24); open PENDING/SENT/SEND_FAILED mature only after max(evaluated_at, exposure_at/outcome_at)+168h ≤ as_of else IMMATURE→CENSORED; terminal outcomes mature once as_of >= outcome_at; mature-but-unresolved stays CENSORED, maturity never manufactures negative

PURCHASE ATTRIBUTION: Dropfans fangate_transactions event dropfans_sale polled per creator, reconciliation 168h window BATCH_SIZE 50, conditional UPDATE commerce_offers (pending|clicked → purchased where transaction_id IS NULL OR =), single-winner ledger UPDATE commerce_opportunity_decisions WHERE sealed_offer_id=$2 AND outcome IN (PENDING,SENT,SEND_FAILED) AND reengagement_of IS NULL ORDER BY opportunity_id LIMIT 1; duplicate transaction_id idempotency returns existing; ambiguous multi-candidate fail-closed unattributed; orphan recovery recovery:{c}:{offer} advisory lock

SIMULATOR INJECTION BOUNDARY: D. Opportunity/evidence persistence layer — insert synthetic commerce_opportunity_decisions rows with generation_id synthetic:{run}:{id}, synthetic creator/user/def/CUID/txn ids, decision_snapshot eligible/selected/ranking frozen, exposure SENT, outcome PURCHASED/DECLINED/EXPIRED, maturity via synthetic as_of = evaluated_at+169h; consumed by same build_optimization_input → classify_opportunity_evidence → build_supervised_label contracts; Telegram layer A, Redis layer B, app domain C all rejected; optimizer dataset layer E is in-memory only and skips DB contract validation

CREATOR ISOLATION: every query WHERE creator_id=$1 first, Redis keys send_dedup:{creator}:{dedup} / inbound_dedup:{creator}:{u}:{tg} / lock:creator:{c}:user:{u} / debounce:creator:{c}:user:{u}, build_creator_dataset raises ValueError on foreign bundle, train_creator_model re-asserts len(creators)>1 → abstain, OfflineModel.creator_id per model, no pooled dataset, forecast per creator

LEAKAGE RISKS: Price as bucket not value (monitor), fan snapshot timing (verify snapshot window caps at evaluated_at), evidence txn/price deliberately dropped, provider live state never in features, no random split, no pooled creator — all blocked; most critical residual is synthetic family HAS_FAMILY unless snapshot captures family_id (currently always NO_FAMILY degraded)

PRODUCTION SIDE EFFECT RISKS: Real Telegram send via send_messages → send_worker → Telethon, real fangate_transactions, real commerce_offers sealed with live CUID, real dropfans_drop_intents, messages audit, user_profiles — simulator must write only synthetic ledger rows under synthetic creator namespace (creator>=90000, generation synthetic:) and file-only vault/simulation_runs/{run}/ ledger_jsonl for Phase 1; no Redis streams, no scheduled_messages.

PHASE 1 REQUIREMENTS: SimulationRun (run_id, creator, seed, schema/maturity versions, counts) / SimulationEvent (run, opportunity, generation synthetic:, evaluated_at, exposure/outcome) / SimulationClock (deterministic tick, never time.time()) / SimulationIdentity (synthetic creator/user/def/CUID/txn factories, synthetic creators placeholder FK) / SimulationDataOrigin (data_origin=simulation via generation synthetic: prefix existing quarantine) / GroundTruthReference (opportunity maturity_at, ground_truth label vs observed evidence) — contracts table in Phase 1 Requirements

UNKNOWN BLOCKERS: Production DB row counts / Dropfans mapping distribution / purchase velocity / conversation lifecycle windowing / LLM path price leakage / generation_telemetry retention vs maturity / Vault reaper vs send_unknown race / concurrent debounce+lock concurrency / transaction_id cross-creator dedup / optimizer per-row latency — all marked requires production runtime/database verification

```

---

## Identification of Correct Simulator Integration Boundary — Explanation Summary

1. **Where production data enters:** `workers/llm_worker.py:1512 record_opportunity_decision(creator_id, user_id, generation_id, evaluated_at, opportunity_result)` inserts frozen `decision_snapshot` (eligible/ineligible/selected/ranking + fan/history/conversation provenance). This is the sole evidence bus; Redis `inbound_messages` and `messages` audit are pre-commerce.
2. **Where optimizer-relevant state becomes authoritative:** `commerce_opportunity_decisions` row with `exposure_state/at/source` (via `record_opportunity_send`) + `outcome_state/at/transaction_id` (via `record_purchase_by_offer` single-winner) + `as_of`-gated `classify_opportunity_evidence`. Downstream `fetch_opportunity_rows:676 SELECT _READINESS_COLUMNS WHERE creator_id ORDER BY opportunity_id` is the optimizer's only query.
3. **Where simulation can inject equivalent synthetic state:** Insert rows with identical schema but `generation_id` prefix `synthetic:{run}:{opportunity_id}` and synthetic IDs, via same `decision_snapshot` JSONB shape (1..10 canonical Vault ids, offer_type, price_minor, mapped_drop_ids), with `evaluated_at` from `SimulationClock`, `exposure_state=SENT`, `outcome_state` from oracle, `transaction_id=txn:sym:{run}:{i}`. Consumed identically by `build_optimization_input:748` + `classify_opportunity_evidence:208` → never `CENSORED` if mature, never `UNATTRIBUTED` if txn present, but `readiness:147` quarantine `synthetic:` keeps prod primary clean.
4. **What production paths must remain untouched:** Telegram MTProto `chatbotv2/main`, Redis `INBOUND/SEND_STREAM` consumers `llm_workers/send_workers`, debounce `debounce:creator` Lua, `user_lock` `lock:creator`, `send_dedup`/`send_random_id`/`send_unknown`/`send_repair`, `scheduled_messages`→scheduler re-engagement claim, real `fangate_transactions` poll (`dropfans_reconciliation_interval_seconds=120`), real `commerce_offer_definitions` active resolver — all before/after D, not part of optimizer.

---

*End of Phase 0 Forensic Audit. No code beyond this report was created or modified. Next phase (Phase 1 — Simulation Contracts) may use this audit as specification to implement `SimulationRun/Event/Clock/Identity/DataOrigin/GroundTruthReference` safely.*

