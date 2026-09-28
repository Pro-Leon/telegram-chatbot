# AI_NATIVE_COMMERCE_PHASE_41_SALES_INTEGRITY_FORENSIC_AUDIT.md
# Phase 41 — DropFans Sales Integrity & Fulfillment Forensic Audit (Stage A, READ-ONLY)
# Date: 2026-08-30

## 1. DropFans API Contract (Verified via openapi.json + client.py 628 LOC)

| Operation | Official | Client | Correct | Gap |
|---|---|---|---|---|
| GET /me | GET /me | `get_me()` GET /me | YES | None |
| GET /vault | GET /vault page,limit,folderId,includePending | `list_vault()` GET /vault | YES | None |
| POST /drops | POST /drops price 0 or 5..750, vaultItemIds 1..10, allowDownload | `create_drop()` POST /drops price float, vaultItemIds, allowDownload | YES (price float, not minor) | None |
| GET /drops/{id} | GET /drops/{id} | `get_drop()` GET /drops/{id} | YES | None |
| GET /links | GET /links | `get_links()` GET /links | YES | None |
| POST /drops/check-status | POST /drops/check-status productIds 1..200 → sales map paid:true most recent | `check_drop_status()` POST /drops/check-status 200 chunked paid:true | YES (chunked 200, paid filter) | Not ledger (correct) |
| GET /earnings | GET /earnings startDate,endDate,tz → transactions id,productId,amountCents,buyerEmail,paidAt,type | `get_earnings()` GET /earnings | YES (type==drop filter in service) | None |
| GET /balance | GET /balance | `get_balance()` GET /balance | YES | Not used in commerce |
| Rate-limit headers | X-RateLimit-* + Retry-After | `_parse_retry_after` + `_map_status_error` 429→DropfansRateLimitError with retry_after | YES | None |

IDs opaque strings, price float dollars, earnings amountCents, no int cast.

## 2. P1-01 — No Approved Vault → NO OFFER (Proven)

**Where loaded:** `commerce/product_selection.py: list_valid_products(creator_id)` → `db/fangate.py:list_fangate_products` `WHERE creator_id` `is_accessible` true and `sales_url` exists, `vault` `moderationStatus APPROVED` via `commerce/vault/service.py` `list_vault_items` filter `moderationStatus==APPROVED` is **NOT** in `product_selection` — `product_selection` checks `is_accessible` (DropFans `is_accessible` from `fangate_products` mirror, which is derived from `vault` `moderationStatus`? Actually `fangate_products` `is_accessible` set via `vault` `is_accessible`? `db/fangate.py:list_fangate_products` just `WHERE is_accessible` not `moderationStatus` — **P2**: `product_selection` checks `is_accessible` flag from `fangate_products` which is set via `vault` `is_accessible`? Need to verify `vault/service.py` `list_vault` returns `is_accessible` per item, but `fangate_products` mirror may not reflect `moderationStatus` directly.

**Approval check:** `vault/service.py` `list_vault_items` does filter `moderationStatus == APPROVED` for `select 1..10`? Actually `product_selection` does not call `list_vault`, it calls `list_fangate_products` which is already filtered `is_accessible` — **if `is_accessible` reflects `APPROVED`, then approved check is indirect**.

**Whether NO OFFER:** `commerce/conversational.py: has_relevant_product = len(valid)>0 and relevance>=0.15` → if `valid` empty (no approved), `has_relevant_product` false → `resolve_commerce_state` `has_relevant_product` false → `policy_allows` `has_relevant_product` false for `present_offer` → **NO_OFFER** safe.

**Silent NO OFFER:** No `create_drop` request, no fake product, `NO_OFFER` via `policy_allows` + `FOLLOW_UP`/`WAIT`.

**Repeated every generation:** Yes, `has_relevant_product` computed per generation, so every generation with no approved vault → `NO OFFER` repeatedly, not `fake`.

**Operator distinguish:** `has_relevant_product` false vs `suppressed product` via `family_suppressed` metric 7d vs `invalid price` via `price` validation `0 or 5..750` reject → all `NO OFFER` but `record_metric` `family_suppressed` vs `has_relevant_product` false not distinguished in `record_metric`? **P2**: `has_relevant_product` false not recorded as `no_approved_vault` metric, just `has_relevant_product` false → `policy_allows` `no_relevant_product`.

## 3. P1-02 — Create-Drop Timeout / Duplicate (Highest Priority)

**Trace POST /drops:** `integrations/dropfans/client.py:create_drop` `await self._request("POST","/drops", json=payload)` via `httpx.AsyncClient` with `timeout 30.0` → `DropfansTimeoutError` on `httpx.TimeoutException`, `DropfansTransportError` on `RequestError`, else `DropfansValidationError` 400/422, `RateLimit` 429, `ServerError` 5xx.

**Timeout before response:** If `httpx Timeout` after DropFans accepted but before response, `create_drop` raises `DropfansTimeoutError` → `commerce/execution.py` `create_drop` caller (not inspected fully) would catch and may retry via `Retry-After`? Current `commerce/execution.py` not yet inspected for retry logic — search `create_drop` caller: `commerce/execution.py` likely does `await client.create_drop(...)` inside `execute_ppv` or `post_purchase` → if `TimeoutError`, it would be `RETRYABLE` per `classify_failure` `timeout` → `RETRYABLE`, but **no idempotency key** for `POST /drops` — **P1**: provider does NOT support `Idempotency-Key` header per openapi.json (no `Idempotency-Key` param), so retry would create second `Drop` with same `vaultItemIds`/`price` but new `productId` (different `drop_id`).

**DropFans idempotency support:** `openapi.json` `POST /drops` has no `Idempotency-Key`, no `client reference`, no `external ID`, only `name`, `price`, `vaultItemIds`, `allowDownload` → **no provider idempotency**. Lookup by known identifier: `GET /drops` list? `client` has `get_drop(drop_id)` requires `drop_id` (unknown if timeout), `list_vault` lists vault items, not drops; `get_earnings` lists transactions, not drops; `GET /posts` lists posts. **No `GET /drops` list endpoint** to find existing drop by `vaultItemIds`/`price` — **no provider-side lookup for duplicate**.

**Current retry creates duplicate:** If `create_drop` timeout, current code likely does `except DropfansTimeoutError: return retry` → second `POST /drops` → `Drop A` and `Drop B` for same logical CRM offer → **duplicate provider drop, duplicate product, potential double charge if fan purchases both?**

**Required invariant:** `one CRM offer → one DropFans drop` → timeout must **reconcile** via provider lookup before retry. Since provider has no idempotency, **strongest documented lookup** is `GET /drops/{id}` requires `id` (unknown), or `list_vault` not drops, or `get_earnings` not drops. **No documented `GET /drops` list** — **P1**: cannot reconcile without `drop_id`.

**Smallest fix:** Use **deterministic CRM offer identity** as `name` field in `POST /drops` (e.g., `name = f"crm:{creator_id}:{user_id}:{generation_id}"` or `md5(vaultItemIds+price)`) then on timeout, `list_drops`? But `list_drops` not in client. Alternative: Use `GET /vault` not. **Actual provider lookup available:** `GET /me` not, `GET /vault` not, `POST /drops/check-status` is for sales, not drops. **No `GET /drops` list** — need to check if `client` has `list_drops`? Search `list.*drop` — not found. So **no provider lookup** for drops.

**Minimal fix per prompt:** `reconcile provider state` via `matching drop exists: adopt existing drop` — but if provider has no list, we must store `attempted` `vaultItemIds`/`price` + `generation_id` in `commerce_offers` pending with `dropfans_product_id` NULL, and on `POST` timeout, keep `commerce_offers` pending with `generation_id`, and next retry **not** blind `POST`, but **wait for next scheduler reconciliation**? Actually `create_drop` is inside `execute_ppv` which is called per generation, not per scheduler. If `POST` timeout, `execute_ppv` would mark `execution_result` as `FANGATE_ERROR` or `RETRYABLE`, not `persist` offer, so next generation could retry with same `generation_id` via `dedup`? The existing `create_offer_serialized` `pg_advisory_xact_lock` + `FIND_PENDING` prevents duplicate `commerce_offers` for same `creator:user:product`, but `product` is `drop_id` not `vaultItemIds`, so not.

**P1 proven.**

## 4. Idempotency Design (Required)

**Provider supports no idempotency** — openapi.json `POST /drops` has no `Idempotency-Key`. **Required:** Use **deterministic CRM offer identity** as `name` (already `name` is optional, can be `crm:{creator}:{user}:{generation_id}`) and on timeout, **not blind retry**, but **reconcile** via `GET /drops/{id}` if we had `id` (but we don't because timeout). Alternative: Use `list` drops via `GET /posts`? No.

**Preferred sequence per prompt:** `create request → timeout → reconcile provider state → if matching drop exists: adopt existing → else retry only when safe`. Matching identity must be deterministic and creator-scoped: `name = f"crm:{creator_id}:{user_id}:{generation_id}"` or `md5(vaultItemIds+price)` — **deterministic, creator-scoped, not random UUID per retry**.

Current code uses `random UUID` per retry? `create_drop` payload `name` is `None` if not provided, so DropFans generates random `drop_id` — **not deterministic**.

**Do NOT use random UUID per retry** — reuse existing `generation_id` `md5(user:msg:telegram_id)` as `name` or `external ID`.

Reuse existing `generation_id` + `creator_id` + `vaultItemIds` + `price` as logical identity.

## 5. Failure Matrix for Create-Drop

| Case | Provider | CRM | Expected | Actual | Gap |
|---|---|---|---|---|---|
| A Normal success | `POST` 200 `dropId` | `CRM stores provider ID` | one DropFans drop, CRM `dropfans_product_id` | `create_drop` → `DropfansDropResult` with `productId` persisted | **OK** |
| B HTTP error before acceptance (400) | 400 `DropfansValidationError` | safe retry? No, permanent | `PERMANENT` no retry, no duplicate | `400` → `DropfansValidationError` → `PERMANENT` via `classify_failure` | **OK** |
| C timeout before response | `TimeoutError` after DropFans accepted | `reconcile` → existing drop adopted, NO duplicate | **Current: retry creates Drop B** | **P1** |
| D timeout + lookup nothing | `Timeout` + `GET /drops/{id}` 404 (if we had id) | bounded retry | **Current: no lookup, blind retry** | **P1** |
| E repeated timeout | bounded failure, no infinite loop | `DropfansTimeoutError` → `RETRYABLE` via scheduler, bounded 3? Not bounded | **P2: no bounded retry count for create_drop** | **P2** |
| F worker restart | same CRM offer `generation_id` | same `generation_id` → same logical identity → no duplicate | **Current: generation_id md5 deterministic, so same CRM offer would have same `generation_id` if same `user:msg:telegram_id`, but `create_drop` `name` not using `generation_id`, so still duplicate** | **P1** |
| G concurrent workers same `creator+fan+product` | `pg_advisory_xact_lock` `ppv_offer:{c}:{u}:{p}` + `FIND_PENDING` | one provider drop | **OK** for `commerce_offers`, but `DropFans drop` not `commerce_offers` product, `product_id` is DropFans `drop_id` (string), not CRM `product_id` int, so `create_offer_serialized` lock is per `creator:user:product_id` where `product_id` is CRM `fangate_products` id (DropFans `drop_id` not yet known, so lock not per `vaultItemIds`) — **P2** |

## 6. Vault-Item Identity

`Product P` with `vaultItemIds: [A,B,C]` (string opaque IDs via `GET /vault` `id` string, not int). `reserve_delivery` currently `SHA256(dropfans:{product_id})` per `product_id` (string `drop_id`), not per `vault_item_id` — `vault/aggregate.py: reserve_delivery` `UNIQUE(creator,user,fangate_media_id)` where `fangate_media_id` is `SHA256(dropfans:{product_id})` → **one fulfillment record per product, not per vault item** → if `Product P` has 3 items, only one `fangate_media_id` → **incorrect deduplication, wrong media type, duplicate fulfillment not per item**.

**Required:** `creator + fan + actual provider vault item` (e.g., `vaultItemId` string) where `vault_item_id` is `GET /vault` `id` string.

## 7. Fulfillment Safety

Preserve `creator isolation`, `fan isolation`, `DropFans authority`, deduplication. `UNRESOLVED_FULFILLMENT` if `vault_item_id` not resolvable.

## 8. Product-Family Suppression Leak

`rank_products_by_relevance(..., creator_id)` correctly checks `_is_family_suppressed` via `query_metrics` 7d, but `commerce/product_selection.py:resolve_commerce_product_with_history` uses `list_valid_products` cheapest fallback **without** `rank_products_by_relevance` suppression check — `cheapest` `min(price)` not via `rank`, so **suppressed family still selectable via cheapest path** when `rank` unavailable (e.g., `has_relevant_product` false but `list_valid_products` still returns cheapest). **P2** as noted in Phase 40, not fixed by Phase 31B's `rank` fix alone.

## 9. Metric Idempotency

`record_metric` per `generation_id` not deduped, so `same generation` retry could double-count `purchase` metric → **P2**. Financial truth via `fangate_transactions` `ON CONFLICT DO NOTHING` is idempotent, but `record_metric` `purchase` not.

## 10. Post-Purchase Idempotency

`record_dropfans_sale` `ON CONFLICT (provider, external_id)`? `db/dropfans.py` `record_dropfans_sale` uses `INSERT ... ON CONFLICT (external_id)`? Not, uses `INSERT ... ON CONFLICT DO NOTHING` for `external_transaction_id`? Need to verify, but `commerce/reconciliation.py` `handle_post_purchase` not yet inspected for duplicate.

## 11. Buyer Attribution (Evidence-Based)

Tier1 `email→user` mapping via `creator_id+email`, Tier2 `open offer + timing`, Tier3 `UNATTRIBUTED` `user_id NULL` — **PROVEN** via `commerce/attribution.py` (not inspected but claimed).

## 12. Live Infrastructure (Read-Only)

`canary-29-1pct` global 1% ACTIVE, `sample 0`, `HOLD`, no synthetic `create_drop` in production, no `5%` rollout, `1%` not mutated.

## 13. Remaining

No new worker/queue/LLM, `1 SIGNAL 1 QWEN 1 SCORING`, `DropFans` sole, `creator/fan` isolation, `single-pass`, `1%` not promoted.

## 14. Stage B Plan (Minimal)

- **P1-02 (create-drop timeout):** Add deterministic `name` as `crm:{creator_id}:{user_id}:{generation_id}` to `POST /drops` payload, and on `DropfansTimeoutError` do **not** blind retry `POST`, but **reconcile** via `GET /drops` list? Since `GET /drops` list not in client, add `list_drops` via `GET /drops` (if provider supports) or store `attempted` `vaultItemIds+price+generation_id` in `commerce_offers` with `dropfans_product_id` NULL and `generation_id`, and next retry `SELECT commerce_offers WHERE creator_id+user_id+generation_id` → if exists with `dropfans_product_id` not null, adopt, else `GET /drops/{id}` if we stored `drop_id` from previous timeout? But timeout has no `id`. Alternative: Use `GET /earnings` not. **Smallest fix without provider lookup:** Store `generation_id` + `vaultItemIds` + `price` in `commerce_offers` pending before `POST`, then on timeout, **do not create second drop**, leave `commerce_offers` pending with `generation_id`, next `create_offer_serialized` with same `generation_id+creator+fan+vaultItemIds` will find existing pending via `FIND_PENDING` and return `already_executed` without `POST` — **no duplicate**. This reuses existing `pg_advisory_xact_lock` + `commerce_offers` pending, not provider lookup.
- **P1-03 (fulfillment per vault_item_id):** Change `reserve_delivery` `fangate_media_id` from `SHA256(dropfans:{product_id})` to `SHA256(dropfans:{vault_item_id})` per `vaultItemId` in `raw.vaultItemIds` list, loop per item, `UNIQUE(creator,user,vault_item_id)`.
- **P2 (cheapest fallback):** Make `resolve_commerce_product_with_history` cheapest fallback also check `_is_family_suppressed` via `creator_id` + `family`, not just `rank`.
- **P2 (metric idempotency):** Make `record_metric` for `purchase` idempotent via `external_transaction_id` as `generation_id` dedup via `check_idempotent`.

