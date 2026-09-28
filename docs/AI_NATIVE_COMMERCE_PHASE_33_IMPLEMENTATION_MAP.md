# AI_NATIVE_COMMERCE_PHASE_33_IMPLEMENTATION_MAP.md
# Phase 33 — Implementation Map (Production Hardening, No Redesign)
# Date: 2026-08-30

## 1. DropFans API Contract (Verified)

- `GET /me` → `DropfansAccount.id` (opaque string, used for creator mapping)
- `GET /vault` → `VaultListResult` with `moderationStatus` filter
- `POST /drops` → `price 0 or 5..750`, `vaultItemIds 1..10`, `allowDownload`
- `GET /drops/{id}` → `Drop` with `moderationStatus`, `salesCount`, `buyUrl`
- `POST /drops/check-status` → `productIds 1..200` → `sales` map `paid:true`
- `GET /earnings` → `transactions[]` `id,productId,amountCents,buyerEmail,paidAt,type`
- `GET /links` → `web.buyTemplate, telegram.buyTemplate`
- Auth `Bearer`, rate-limit `Retry-After`, 401/403/404/429/5xx classified

## 2. Provider Authority (No Fallback)

- `commerce/state.py`, `single_creator.py`, `execution.py` use `DropfansClient` only, no Fangate fallback.
- `fangate_products` table is provider-neutral (legacy name, stores `dropfans_product_id` via `20260825000000`), not fallback.
- `DropFans unavailable → commerce unavailable → NO SALE` (safe fallback via `policy_allows`).

## 3. Creator Identity

- `GET /me` validates `api_key → /me.id` vs `CRM creator.id` via `creator_integrations.dropfans_creator_id`, mismatch → fail-closed no product/offer/purchase.

## 4. Product/Drop Selection

- Deterministic via `revenue_intelligence`/`operational_intelligence`/`strategy_learning` + `content_matching` relevance, topic, lifecycle, fatigue, pressure, safety, fallback `NO OFFER`.

## 5. Vault Media (Approved Only)

- `list_vault` → filter `APPROVED`, `PENDING/HIDDEN/DELETED` not saleable, `GET /drops/{id}` verifies `moderationStatus`.

## 6. Price Authority

- Deterministic CRM price `0 OR 5..750`, never LLM, validated, persisted `price_minor`, `dropfans_product_id`, not clamped.

## 7. Checkout URL

- `create_drop` `buyUrl` preferred, `GET /links` `telegram.buyTemplate` → `{productId}` replace, fallback web, never hardcoded.

## 8. Idempotency

- `generation_id+creator+fan+vaultItemIds+price` via `pg_advisory_xact_lock`, `POST` timeout → reconcile before retry.

## 9. Sale Polling & Financial Truth

- `check-status` ≤200 chunked, absence ≠ failure, `paid:true` → candidate → `GET /earnings` `type==drop` match `productId` → confirmed, `amountCents` truth, multiple sales per product via unique `id`.

## 10. Buyer Attribution

- `buyerEmail ≠ Telegram user`, Tier1 explicit mapping, Tier2 offer-scoped timing, Tier3 `UNATTRIBUTED` NULL, no probabilistic, no cross-creator merge.

## 11. Post-Purchase / Vault / Scheduler

- `PURCHASE` outcome → funnel → evidence → metrics → idempotent confirmation via send stream dedup, `vaultItemIds` only, scheduler bounded ≤50 per creator, chunked ≤200, idempotent `creator+provider+id`.

## 12. Tests / Live Verification

- 70+ deterministic tests covering provider authority, auth, vault, drop creation, links, polling, financial, attribution, post-purchase, failure, scheduler, invariants, security — via existing `tests/test_phase33*` + commerce tests.
- Live read-only: `GET /me` matches, canary `1% ACTIVE` remains `1%`, no emergency, no synthetic metrics.

## 13. Files Changed

- No new worker/queue/LLM, no redesign, provider unchanged, `DROP FANS` sole, `LLM LANGUAGE ONLY`, `1 SIGNAL 1 QWEN 1 SCORING`, `canary 1% ACTIVE — NOT PROMOTED`.
- Minimal hardening: ensure `price` validation rejects `0<price<5` and `>750`, ensure `vault` filter, ensure `buyUrl` via `get_links`, ensure `recent_reengagements_7d` real (Phase 31B), ensure `family_suppressed` (Phase 31B).

