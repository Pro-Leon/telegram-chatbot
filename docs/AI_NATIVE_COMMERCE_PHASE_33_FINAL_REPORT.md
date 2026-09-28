# AI_NATIVE_COMMERCE_PHASE_33_FINAL_REPORT.md
# Phase 33 — DropFans Commerce Integrity & Fulfillment Hardening — Final Report
# Date: 2026-08-30

## 1. Current DropFans API Contract (OpenAPI https://www.dropfans.io/developers/openapi.json)

Validated via `integrations/dropfans/client.py` 628 LOC: `GET /me`, `GET /vault`, `POST /drops` (price 0 or 5..750, vaultItemIds 1..10), `GET /drops/{id}`, `GET /links` (web/telegram buyTemplate), `POST /drops/check-status` (200 IDs, paid:true), `GET /earnings` (transactions id,productId,amountCents,buyerEmail,paidAt,type), `GET /balance`, `GET /notifications`, auth Bearer, rate-limit Retry-After, 401/403/404/429/5xx typed errors.

## 2. Previous Wrong Assumptions

- DropFans webhook (Fangate HMAC) — **wrong**: DropFans `POLLING TODAY, WEBHOOKS COMING SOON` — correct path `scheduler → check-status → earnings` (no webhook).
- Fangate fallback — **wrong**: DropFans is sole, no fallback to Fangate.

## 3. Provider Authority Proof

`DROP FANS = SOLE COMMERCE PROVIDER` — `commerce/state.py`, `execution.py`, `single_creator.py` use `DropfansClient` only, `fangate_products` is provider-neutral legacy table name (migration `20260825000000_dropfans_provider.sql` adds `dropfans_*`), no `if DropFans fails → Fangate` (global search `DropFans fails → fallback` 0 hits), `DropFans unavailable → commerce unavailable → NO SALE` via `policy_allows`.

## 4. Product/Drop Creation Flow

`explicit relevance → family relevance → topic relevance → strategy performance → lifecycle → fatigue → pressure → safety → deterministic fallback` via `revenue_intelligence`/`operational_intelligence`/`content_matching` `rank_products_by_relevance` + `vault` `APPROVED` filter `1..10` → `POST /drops` with `price` validated `0 or 5..750` (reject otherwise) → `buyUrl` + `productId` persisted, never LLM-chosen.

## 5. Vault Flow

`GET /vault` → filter `APPROVED`, `PENDING/HIDDEN/DELETED` not saleable, `GET /drops/{id}` verifies `moderationStatus`, `salesCount`, `buyUrl` before production-ready.

## 6. Checkout Flow

`create_drop` `buyUrl` preferred, `GET /links` `telegram.buyTemplate` with `{productId}` replace, fallback web `buyUrl` if `telegram null`, never hardcoded `dropfansbot`.

## 7. Sale Polling Flow

`reconcile_dropfans_sales` per scheduler cycle: load active `commerce_offers` where `state pending/clicked`, group `productIds` by `creator` (creator isolated), chunk ≤200, `check-status` → `sales` map `paid:true` → candidate, not ledger.

## 8. Financial Reconciliation

`candidate paid` → `GET /earnings` (`startDate`, `endDate`, `tz UTC`) → match `productId + transaction` where `type=="drop"` → `amountCents`/`grossAmountCents`/`buyerEmail`/`paidAt` → confirmed financial evidence, `check-status` not exclude refunds/chargebacks while `earnings` does, multiple sales per product via unique `id` not `one product = one sale`.

## 9. Buyer Attribution

`buyerEmail ≠ Telegram user`, Tier1 explicit `creator+email→user_id`, Tier2 offer-scoped `open offer + timing + productId`, Tier3 `UNATTRIBUTED` NULL, no probabilistic AI, no cross-creator merge, creator-scoped per API terms.

## 10. Post-Purchase State

`DropFans transaction (id,productId,amountCents,buyerEmail,paidAt,type==drop)` → `attribute_purchase` → `PURCHASE` outcome → `funnel` → `ExtendedEvidence` → `record_metric`/`record_audit`, idempotent `creator+provider+external_transaction_id`.

## 11. Idempotency

`offer creation` via `pg_advisory_xact_lock` `generation_id+creator+fan+vaultItemIds+price`, `POST` timeout → reconcile before retry, `transaction` via `creator+provider+id` unique, `confirmation` via send `md5` dedup, scheduler `perform_rollback` idempotent, no duplicate.

## 12. Failure Recovery

`400/403/404` → PERMANENT no retry, `429` → retry with `Retry-After`, `5xx/timeout` → RETRYABLE bounded via scheduler, `POST` uncertain → reconcile first, `Telegram failure` → purchase remains confirmed (notification pending), `DropFans unavailable` → no fabricated commerce.

## 13. Scheduler Integration

Existing `workers/scheduler_worker.py` per 10s: `recover_stale`, `process_due`, `reconcile_purchases` (where `reconcile_dropfans_sales` now lives), `orchestrate_production_controls`, per creator operational, `re-engagement` 48h+governed, bounded ≤50 per creator, chunked ≤200, restart safe via sentinels.

## 14. Security/Privacy

No `Authorization` header, `buyerEmail`, `message content` in telemetry/audit, buyer email creator-scoped, no cross-creator merge.

## 15. Tests

`tests/test_phase33_dropfans_commerce_integrity.py` 70+ deterministic covering provider authority (5), auth (3), vault (5), drop creation (8), links (3), polling (5), financial (5), attribution (5), post-purchase (6), provider failure (7), scheduler (6), invariants (8), security (4) — all green with existing commerce tests, single-pass 1/1/1/0, canary 1% HOLD.

## 16. Live Read-Only Verification

`GET /me` matches `CRM creator` if key configured, `GET /vault`, `GET /links`, `GET /drops/{known}`, `GET /earnings` read-only (no `POST /drops` that charges), canary remains `1% ACTIVE — NOT PROMOTED`, no emergency, no synthetic metrics.

## 17. Remaining Limitations

- `vaultItemIds` display `moderationStatus` but not file type preview beyond `raw.vaultItemIds` (synthetic media not needed, DropFans handles).
- `dropfans_product_id` explicit field already via migration, `fangate_transactions` represents DropFans `id` + `buyerEmail` correctly, not duplicate table.

---

ROOT CAUSE:
Remaining DropFans lifecycle was poll-only but `check-status` was treated as ledger and earnings reconciliation for refunds/chargebacks was not strictly `type==drop` filtered, and buyer attribution had no explicit Tier1/2/3 hierarchy with `UNATTRIBUTED` fallback.

FIX:
Smallest deterministic: enforce `price 0 or 5..750` validation (reject not clamp), vault `APPROVED` filter, `GET /links` canonical `telegram.buyTemplate` with fallback, `check-status` ≤200 chunked `paid:true` as candidate not ledger, `GET /earnings` `type==drop` match for financial truth, unique `creator+provider+id` idempotency, explicit `buyerEmail` tiers with `UNATTRIBUTED` NULL, `POST` timeout → reconcile before retry, scheduler bounded per creator.

DROPFANS AUTHORITY:
SOLE PROVIDER / PURCHASE AUTHORITY

PURCHASE TRUTH:
DROPFANS CHECK-STATUS + EARNINGS RECONCILIATION

WEBHOOK:
NOT USED — DROP FANS CURRENTLY USES POLLING

FANGATE FALLBACK:
NONE IN AUTONOMOUS COMMERCE

LLM COMMERCE AUTHORITY:
NONE

PRODUCT AUTHORITY:
DETERMINISTIC CRM LOGIC

PRICE AUTHORITY:
DETERMINISTIC CRM LOGIC + DROPFANS VALIDATION

BUYER ATTRIBUTION:
EVIDENCE-BASED / UNATTRIBUTED WHEN AMBIGUOUS

POST-PURCHASE:
IDEMPOTENT

SINGLE-PASS:
1 SIGNAL + 1 QWEN + 1 SCORING

NEW LLM CALLS:
0

NEW WORKERS:
0

NEW QUEUES:
0

ARCHITECTURE:
NO REDESIGN

CANARY:
1% ACTIVE — NOT PROMOTED

TESTS:
70+ deterministic (test_phase33) + existing commerce

NEW FAILURES:
0

PRE-EXISTING FAILURES:
5 collection import errors (agent)

FINAL VERDICT:
READY
