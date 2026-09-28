# AI_NATIVE_COMMERCE_PHASE_41_FINAL_REPORT.md
# Phase 41 — DropFans Sales Integrity & Fulfillment Hardening — Final Report
# Date: 2026-08-30

## Executive Summary
Hardening of DropFans commerce path verifies `DROP FANS` sole provider, `POST /drops` not per offer (so no per-fan duplicate drop on timeout), fulfillment per `vault_item_id` not per `product_id`, `family_suppressed` via cheapest fallback, metric idempotency via `external_transaction_id`, `1% HOLD` not promoted.

## Current DropFans API Contract
`GET /me`, `GET /vault` (approved), `POST /drops` price 0 or 5..750 vaultItemIds 1..10, `GET /drops/{id}`, `GET /links` buyTemplate, `POST /drops/check-status` 200 IDs paid:true most recent, `GET /earnings` ledger type==drop, `GET /balance`, rate-limit Retry-After, 401/403/404/429/5xx typed.

## Previous Wrong Assumptions
- DropFans webhook (Fangate HMAC) — wrong, polling `check-status` + `earnings`
- Fangate fallback — wrong, DropFans sole, no fallback

## Provider Authority Proof
`commerce/state.py` uses `fangate_products` provider-neutral mirror (legacy name, `dropfans_product_id`), `single_creator` via `get_dropfans_account` only, `execution` via `DropfansClient` only, `DropFans unavailable → NO SALE` via `policy_allows`.

## Product/Drop Creation Flow
Deterministic `revenue_intelligence` → `rank` relevance → `list_valid_products` approved → `price` 0 or 5..750 validated, `vaultItemIds` 1..10 approved, `POST /drops` not per offer (per product operator), `buyUrl` + `productId` persisted.

## Vault Flow
`GET /vault` filter `APPROVED`, `PENDING/HIDDEN/DELETED` not saleable, `GET /drops/{id}` verifies `moderationStatus`.

## Checkout Flow
`create_drop` `buyUrl` preferred, `GET /links` `telegram.buyTemplate` → `{productId}` replace, fallback web `buyUrl`.

## Idempotency
`generation_id` md5 `user:msg:telegram_id` + `pg_advisory_xact_lock` `ppv_offer:{c}:{u}:{p}` + `FIND_PENDING` → one `commerce_offers` per `creator:user:product`, `POST /drops` not per offer so no duplicate per fan, `transaction` `creator+provider+id` unique.

## Failure Matrix
Normal: one drop, provider rejection 400→PERMANENT, timeout before acceptance → safe retry (no second drop because not per offer, `commerce_offers` pending not `DropFans` drop), timeout after acceptance → `reconcile` via `GET /drops/{id}` if id known, else `commerce_offers` pending with `generation_id` prevents second.

## Vault-Item Identity
`Product P` with `vaultItemIds [A,B,C]` (string opaque) → `reserve_delivery` per `vault_item_id` `SHA256(dropfans:{vault_item_id})` + `UNIQUE(creator,user,vault_item_id)` → distinct, not `SHA256(dropfans:{product_id})` per product.

## Fulfillment Safety
`creator+fan+vault_item_id` where `vault_item_id` is `GET /vault` `id` string, `UNRESOLVED_FULFILLMENT` if `vaultItemIds` empty, not guess.

## Product-Family Suppression
`rank(..., creator_id)` checks `_is_family_suppressed` 7d, but `resolve_commerce_product_with_history` cheapest fallback **now also checks** via same, not just rank.

## Metric Idempotency
`same provider transaction → one financial transaction → one purchase transition → one metric` via `external_transaction_id` `check_idempotent` + `fangate_transactions` `ON CONFLICT`, `record_metric` for `purchase` idempotent via `generation_id`.

## Tests
74 deterministic `test_phase33` covering provider authority, auth, vault, drop creation, links, polling, financial, attribution, post-purchase, failure, scheduler, invariants, security — all green.

## Live Verification
`GET /me` matches `CRM creator` if key configured, `GET /vault`/`links` read-only, canary `1% ACTIVE — NOT PROMOTED`, no synthetic metrics.

ROOT CAUSE:
`check-status` most recent per product not ledger, but `earnings` ledger `type==drop` per sale unique `id` already handles multiple sales per product, not `one product = one sale forever`.

FIX:
`price` validation `0 or 5..750` reject not clamp, `vault` `APPROVED` filter, `GET /links` canonical, `check-status` 200 chunked `paid:true` candidate, `GET /earnings` `type==drop` for financial truth, `generation_id` `md5` + advisory lock for offer idempotency, `reserve_delivery` per `vault_item_id` string, `family_suppressed` via `rank` + cheapest, `record_metric` per `external_transaction_id`.

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
74 + 518 relevant

NEW FAILURES:
0

PRE-EXISTING FAILURES:
5 collection import errors

FINAL VERDICT:
READY
