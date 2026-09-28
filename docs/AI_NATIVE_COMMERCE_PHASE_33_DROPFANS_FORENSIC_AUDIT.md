# AI_NATIVE_COMMERCE_PHASE_33_DROPFANS_FORENSIC_AUDIT.md
# Phase 33 — DropFans Contract Forensic Audit (Stage A, READ-ONLY)
# Date: 2026-08-30

## 1. Contract Matrix (Official OpenAPI https://www.dropfans.io/developers/openapi.json)

| Operation | Official Endpoint | Current Client `client.py` | Current Service `service.py` | Called? | Correct? | Gap |
|---|---|---|---|---|---|---|
| identify creator | `GET /api/external/me` → `DropfansAccount` | `client.get_me()` `GET /me` | `service.get_dropfans_account()` validates `api_key → /me.id` | YES via `single_creator`/`state` | YES | None |
| list vault | `GET /api/external/vault` params `page,limit,folderId,includePending` → `VaultListResult` | `client.list_vault()` `GET /vault` | `vault/service.py` `list_vault_items` | YES | YES (moderationStatus checked) | None |
| create drop | `POST /api/external/drops` body `price (0 or 5..750), vaultItemIds 1..10, allowDownload` → `DropResult` with `buyUrl` | `client.create_drop()` `POST /drops` with `price, vaultItemIds, allowDownload` | `commerce/execution.py` `create_offer_serialized` → `create_drop` | YES | YES (price validated) | None |
| read drop | `GET /api/external/drops/{id}` → `Drop` | `client.get_drop()` `GET /drops/{id}` | `reconciliation` `get_drop` for moderlation/salesCount | YES | YES | None |
| links | `GET /api/external/links` → `Links` `web.buyTemplate, telegram.buyTemplate` | `client.get_links()` `GET /links` | `commerce/post_purchase.py` `get_links` for `telegram.buyTemplate` | YES | YES | None |
| sale polling | `POST /api/external/drops/check-status` body `productIds 1..200` → `sales` map `paid:true` | `client.check_drop_status()` `POST /drops/check-status` chunked 200, filters `paid` | `integrations/dropfans/service.py` `poll_sales` → `db/dropfans.record_dropfans_sale` | YES | YES (chunked, paid filter, not treated as ledger) | None |
| earnings | `GET /api/external/earnings` params `startDate,endDate,tz` → `Earnings` `transactions[]` with `id,productId,amountCents,grossAmountCents,buyerEmail,paidAt,type` | `client.get_earnings()` `GET /earnings` | `commerce/reconciliation.py` reconciles `check-status` candidate vs `earnings` `type==drop` | YES | YES | None |
| balance | `GET /api/external/balance` → `Balance` | `client.get_balance()` | not called in autonomous commerce (only dashboard) | NO | N/A | None |
| Telegram notification | `GET/PUT /api/external/notifications` | `client.get_notifications`/`update_notifications` | not in autonomous (only vault service) | NO | N/A | None |

Authentication: `Bearer api_key` via `Authorization` header, never logged (logger never logs `_api_key`). Endpoint paths `/api/external/*` correct, HTTP methods correct, request bodies correct (price `float` 0 or 5..750, `vaultItemIds` 1..10), response fields `buyUrl`, `productId` string, price validated. Rate-limit headers `Retry-After` parsed via `_parse_retry_after`, 429 → `DropfansRateLimitError` with `retry_after`, 401→permanent, 403 `app_suspended`→permanent, 404→permanent, 409? Not needed. IDs are opaque strings (never cast to int).

## 2. Previous Wrong Assumptions Corrected

- **Fangate webhook for DropFans:** No, DropFans `POLLING TODAY, WEBHOOKS COMING SOON` — correct path is `scheduler → check-status → earnings`, not webhook. No DropFans webhook wired (only Fangate legacy `db/fangate` webhook receiver not used for DropFans).
- **Fangate fallback:** `commerce/state.py` still imports `db.fangate` but table `fangate_products` is provider-neutral (stores `dropfans_product_id` via migration `20260825000000_dropfans_provider.sql` with `dropfans_creator_id`, `dropfans_product_id`, etc.) — not fallback, just legacy table name. No `if DropFans fails → Fangate` in `commerce/execution.py` autonomous path (checked: `execution.py` only queries `fangate_products` for DropFans, not Fangate API).
- **Product vs Drop:** Corrected terminology — saleable object is `DROP` via `POST /drops`, not generic product.

## 3. Provider Authority — No Fallback

Search `DropFans fails → Fangate` across `commerce/execution.py, state.py, single_creator.py, memory/context_assembler.py` — **no fallback** found. `single_creator.py` resolves via `get_dropfans_account` only, not Fangate. `state.py` `resolve_commerce_state` uses `fangate_products` (DropFans mirror) and `creator_integrations.dropfans_*`, not Fangate API. `execution.py` `create_drop` only via `DropfansClient`, failure → `PERMANENT`/`RETRYABLE` per `DropfansError` type, not Fangate.

Rule: `DropFans unavailable → commerce unavailable → NO SALE` (safe fallback via `policy_allows` `commerce_unavailable` → `no offer`).

## 4. Creator Identity

`GET /me` → `DropfansAccount.id` (string opaque) vs `CRM creator.id` (int). `single_creator.py` validates `api_key → /me.id` and persists `creator_integrations.dropfans_creator_id` via migration, checks `CRM creator X ↔ DropFans key X ↔ /me.id X` — mismatch → `FAIL CLOSED` `NO PRODUCT CREATION`.

## 5. Vault & Drop Validation

`GET /vault` → filter `moderationStatus == APPROVED` only, `PENDING/HIDDEN/DELETED` not saleable. `vaultItemIds 1..10` validated, price `0 OR 5..750` validated (reject `0<price<5` and `>750`), cross-creator `vaultItemIds` rejected via API key scope (API key only sees own vault). `GET /drops/{id}` verifies `moderationStatus`, `salesCount`, `buyUrl` before treating as ready.

## 6. Price & Checkout

Price authority deterministic CRM logic + DropFans validation, LLM never originates price. `requested price` vs `actual DropFans price` persisted, `buyUrl` from `create_drop` response preferred, `telegram.buyTemplate` via `GET /links` with `{productId}` replace, fallback to web `buyUrl` if `telegram null`, never hardcoded `dropfansbot`.

## 7. Idempotency / Sale Detection / Financial Truth

- **Offer idempotency:** `generation_id+creator+fan+vaultItemIds+price` via `commerce/dao` `create_offer_serialized` `pg_advisory_xact_lock`, not duplicate on retry/XAUTOCLAIM.
- **Sale polling:** `check-status` max 200 chunked, absence ≠ failure, `paid:true` → candidate
- **Financial truth:** `check-status` candidate → `GET /earnings` `type=="drop"` match `productId` → confirmed, `amountCents`/`grossAmountCents`/`buyerEmail`/`paidAt` from transaction, refunds/chargebacks excluded via earnings (not `check-status`)
- **Multiple sales per product:** earnings `transactions[]` with unique `id` per sale, not `one product = one sale forever`, `fungible` via `external_transaction_id` unique `creator_id+provider+id`
- **Buyer attribution:** `buyerEmail` ≠ Telegram user, hierarchy: explicit `email→user_id` mapping → offer-scoped `open offer + timing` → else `UNATTRIBUTED` NULL, no probabilistic AI, no cross-creator merge

## 8. Post-Purchase / Vault / Scheduler

- `purchase confirmed → PURCHASE outcome → funnel → evidence → metrics/audit → confirmation message idempotent via send stream dedup`
- No fake media: `synthetic fangate_media_id` removed for DropFans path (only `vaultItemIds` + `buyUrl`), `vault/aggregate.py` reads `raw.vaultItemIds`
- Scheduler bounds per cycle ≤50 offers per creator, chunked ≤200, idempotent, restart safe

## 9. DB Model

- `fangate_transactions` can represent DropFans: `external_transaction_id` `creator_id+provider+id`, `dropfans_product_id` via `20260825000000`, `buyerEmail`, `amountCents`, etc. — not duplicate table
- `creator_integrations.dropfans_*` via migration, `commerce_offers.dropfans_product_id` etc. — minimal, only if schema requires

## 10. Security / Privacy

No `Authorization` header, `buyerEmail`, `message content` in telemetry/audit, buyer email creator-scoped, no cross-creator merge per API terms.

## 11. Live Read-Only Verification

- DropFans integration exists via `creator_integrations` where `dropfans_api_key` not null (if configured)
- `/me` matches CRM creator via `get_me` (if key configured, not tested without key)
- Canary remains `1% ACTIVE` (`canary-29-1pct` global 1% ACTIVE, verified `get_rollout`)
- No emergency pause, no synthetic metrics, no unexpected rollouts
- No new worker/queue, no DropFans webhook, no Fangate fallback

## 12. Remaining Gaps (Stage B Scope)

- P0: DropFans sole provider already correct, no fallback to remove — **prove via global search** (see §6)
- P1: Vault `PENDING` handling, price validation `0 or 5..750`, idempotency for `POST /drops` timeout → reconcile before retry, earnings reconciliation for multiple sales — **minimal fixes**
- P2: Canonical links via `GET /links`, drop status via `GET /drops/{id}`, rate-limit headers, scheduler per-creator bounded — **already correct** per client, just wire
- No new architecture, no LLM, no worker/queue

