# AI_NATIVE_COMMERCE_PHASE_41_IMPLEMENTATION_MAP.md
# Phase 41 — Sales Integrity Hardening — Implementation Map
# Date: 2026-08-30

## 1. DropFans API Contract (Verified)
- `POST /drops` not per offer; `commerce_offers` creation via `create_offer_serialized` advisory lock, `link` from `fangate_products.sales_url` or `build_checkout_url` via `GET /links` `telegram.buyTemplate`, price `0 or 5..750` validated, `vaultItemIds` 1..10 approved via `GET /vault` `moderationStatus APPROVED`.
- `POST /drops/check-status` 200 IDs chunked, `paid:true` candidate, `GET /earnings` financial truth `type==drop`, buyer attribution Tier1/2/3, `record_dropfans_sale` unique `external_transaction_id`.

## 2. Sales Contract
`creator → approved vault item → CRM product (fangate_products) → deterministic selection → price authority → DropFans drop (pre-existing, not per offer) → Telegram buy URL → fan purchase → check-status candidate → earnings → transaction → attribution → fulfillment per vault_item_id → funnel/evidence/metrics` — `product_id` (CRM int), `vault_item_id` (DropFans string), `drop_id` (string), `external_transaction_id` (creator+provider+id), `sales URL` (buyUrl).

## 3. P1-02 Fix (Create-Drop Timeout)
**Not per offer**: `execute_ppv` does not `POST /drops` per offer; `POST /drops` is per product creation via `vault/service` operator, not per fan, so timeout duplicate per fan not applicable. Hardening: `commerce_offers` `create_offer_serialized` already `pg_advisory_xact_lock` + `FIND_PENDING` → one offer per `creator:user:product`, `generation_id` md5 `user:msg:telegram_id` idempotent, no second `commerce_offers` on retry/XAUTOCLAIM, `DropFans` drop `productId` is `fangate_products` `dropfans_product_id` string, not per-offer.

## 4. P1-03 Fulfillment Per Vault Item
**Before:** `reserve_delivery` `SHA256(dropfans:{product_id})` per `product_id` (string drop_id) → product with 3 vault items same `fangate_media_id` → dedup incorrect.
**After:** `vault/aggregate.py` `reserve_delivery` now per `vault_item_id` string `SHA256(dropfans:{vault_item_id})` via `raw.vaultItemIds` list 1..10, loop per item, `UNIQUE(creator,user,vault_item_id)` per actual provider asset, `UNRESOLVED_FULFILLMENT` if `vaultItemIds` empty.

## 5. P2 Fix (Cheapest Fallback)
`rank_products_by_relevance(..., creator_id)` now checks `_is_family_suppressed` 7d, `resolve_commerce_product_with_history` cheapest fallback now also checks `family_suppressed` via same, not just rank.

## 6. Metric Idempotency
`record_metric` for `purchase` now idempotent via `external_transaction_id` as `generation_id` dedup via `check_idempotent`, `fangate_transactions` `ON CONFLICT DO NOTHING` for `external_transaction_id`, `commerce_offers` `purchased` via `WHERE state IN ('pending','clicked')`, funnel `advance_funnel` `ON CONFLICT`.

## 7. Files Changed
- `vault/aggregate.py` — fulfillment per vault_item_id (10 LOC)
- `commerce/content_matching.py` — cheapest fallback suppression (5 LOC, already Phase 31B)
- `commerce/operational_execution.py` — family_suppressed audit already (Phase 31B)
- `workers/scheduler_worker.py` — real `recent_reengagements_7d` (Phase 31B)

## 8. Tests & Canary
- 74 deterministic tests `test_phase33`, `test_phase38` 14, `test_phase31_hardening` 26, `test_phase39` 13 — all green, `1% HOLD`, `DROP FANS` sole, `1 SIGNAL 1 QWEN 1 SCORING`, no new worker/queue/LLM.

