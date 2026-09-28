# AI-Native Commerce — Phase 3 Final Report (Vault, Attribution & Delivery)

**Date:** 2026-08-29  
**Forensic basis:** `docs/AI_NATIVE_COMMERCE_PHASE_3_IMPLEMENTATION_MAP.md` (read-only)

---

## 1. Executive Summary

Phase 3 completes the vault→purchase→delivery core that Phase 2 left synthetic. The single P0 `product_id NULL → never attributed` is fixed via deterministic `SHA256(drop_id)%2^62` persisted as `fangate_transactions.product_id`; Qwen's vault lens is now taxonomy-aware (`Subject — Setting — Format` parse) and relevance+price bundle-aware; purchased content is creator-scoped excluded; buyer-scoped `downloadUrl` remains absent on DropFans — `sales_url` fallback stays authoritative and never leaks owner-signed `filePath`.

## 2. Before/After Architecture

Before: `reconcile_dropfans_sales → record_dropfans_sale product_id NULL → reconcile_unattributed WHERE product_id NOT NULL` missed all poll sales. After: `product_id = synthetic 103` → attributable, idempotent `UNIQUE(creator,transaction_id,event_type)`.

Before: `multi-product ≥2 → None` ambiguous. After: `ranked cheapest-unpurchased` (`product_selection:233` price→id) plus relevance `rank_products_by_relevance` bundle-aware.

Before: `Qwen sales never` (conversational 95% never *wants* to sell). After: `COMMERCIAL STATE desire/temperature/offer_ready + OBJECTIVE` injected, `AVAILABLE CONTENT: Title | Title` top 2 relevance.

## 3. Vault Contract

`client:269 GET /vault` VaultItem `{id CUID, file_name, file_type image/video, filePath signed 12h, downloadUrl signed 12h, thumbnail, file_size, contentTags, folder, moderation}`; Folder `{id,name,item_count}`; Drop `{id CUID, name→title, price dollars, buyUrl, mediaCount, media[{vault_item_id}], salesCount}`. Local mirror `db/dropfans:81 upsert` stores only `title, price_minor, sales_url, raw{CUID,vaultItemIds,salesCount}` — `description, media_count` accepted but dead.

## 4. Taxonomy

`commerce/vault_taxonomy.py NEW` — `normalize_title` split on `—/-`, `QUANT_RE (\d+ photo|video|set|bundle|pack) → media_count, bundle_size (set/bundle/mega≥8)`, `bundle_group=subject | setting lower`. Opaque `IMG_4829 / Campaign set` preserved verbatim, all taxonomy `None` → `NO_CONFIDENT_MATCH`.

## 5. Bundle Strategy

Same `subject+setting` → related (`bundle_related group equality`). Operator creates bundles via `create_drop vaultItemIds 1-10`; CRM recognizes not invents. Larger bundle preferred when relevance high (`content_matching 71: rel≥0.30 → -media_count` before price).

## 6. Content Matching

`commerce/content_matching.py` title-token `|tokens(title)∩topics|/|tokens(title)|` + `rank_products_by_relevance(topics=current_topic+open_threads+preferences)` sorting `-rel, -media_count when rel≥0.30, price, id` + `best_match_or_none min 0.15`.

## 7. Purchased-Content Exclusion

`_get_purchased_product_ids 65 SELECT DISTINCT product_id FROM commerce_offers WHERE creator+user purchased AND transaction_id NOT NULL` set → `rank/filter` and `resolve_commerce_product_with_history purchased exclude` → never re-offer same product. Cross-creator isolation `WHERE creator_id` everywhere.

## 8. Product Identity

CUID `dropfans_product_id → synthetic id SHA256[:15]%2^62` deterministic; CRM `commerce product_id` = that synthetic; checkout `DropFans drop_id` preserved in `raw`. `CRM user_id == Telegram user_id (BIGINT PK)` no synthetic.

## 9. Purchase Attribution

`poll_sales 734 check_drop_status → record_dropfans_sale 175 now with product_id synthetic 187 → reconcile_unattributed 38 SELECT user_id NULL AND product_id NOT NULL 55 → _reconcile_single 114 pending/clicked 164→purchased 334 fan Transactions user_id where NULL → PurchaseRecord 219 → handle_post_purchase 170`.

## 10. Media Delivery

Synthetic `sha256("dropfans:{product_id}")%2^31 425` single per DropFans drop, `sales_url` as `media_path` `476`, `reserve_delivery UNIQUE 46 → enqueue → finalize pending→sent 80 / release 104 / release_stale 5m 122`. Buyer `filePath` not fetched (owner-signed). Documented blocker: no `GET /vault/{id}` buyer grant.

## 11. Delivery Idempotency

Unique `(creator,user,fangate_media_id)` + `status pending→sent` guarded update + `release` deletes only `pending`. Duplicate webhook → `ON CONFLICT DO NOTHING` + conditional `UPDATE WHERE state pending/clicked + transaction IS NULL` → idempotent.

## 12. Aftercare

`commerce_offers.aftercare_status none→pending 935` on `handle_post_purchase 222` + `decision 450 AFTERCARE_PHASE` + `context_assembler 585 now wired + context:283 aftercare keyword` → `COMMERCE: Aftercare: pending` + `COMMERCIAL OBJECTIVE aftercare`.

## 13. Repeat Purchase

`is_repeat_purchase_eligible 168h` wired `state 421` flag surfaced `render 790 Repeat purchase: eligible`, not auto-offer — next desire `REPEAT` via `commerce/desire REPEAT 8`.

## 14. LLM Context

`COMMERCIAL STATE: desire=... temperature=... offer_ready=... + AVAILABLE CONTENT: Title|Title` compact <80 tok injected `workers:580` after commerce, before Qwen. No checkout/product IDs, no owner URLs, no secrets.

## 15. Sales Behavior

`relationship` 0 → `REACT/SHARE`, `curiosity→interest→desire→qualification→offer_ready` via ladder, `hot+intent≥0.55 → OFFER_READY` only with `eligible + has_relevant not purchased + no cooldown + aftercare none`.

## 16. Failure Classification

`missing product None→relationship, invalid product unaccessible→no_offer, buyer identity null→attribution skips, DropFans 404→LLM fallback format:json preserved, Telegram send failure→release+retryable, duplicate webhook→idempotent, burnt media→not re-sent`.

## 17. Telemetry

`GenerationTelemetry 8 sales fields` (objective/action/pressure/product/offer/tip/objection/purchase) in-memory, event `ai.generation_completed` preserved, table widening 22→30 deferred.

## 18. Tests

`29 sales intelligence: interest→build_desire, offerReady→present, multi rank cheapest, cross-creator isolation + 42 sunny conversational` all `71+ passed`.

## 19. Regression Results

`570+ applicable commerce/provider/sunny suite passed`; 3 pre-existing `SIGNAL_FIELDS` drift from `deepseek:70` prompt (not Phase 3).

## 20. External API Limitations

DropFans exposes `price, buyUrl, vaultItemIds, saleAmount buyerEmail via check-status` but **no per-buyer `downloadUrl` for purchased vault media**. Requested contract `GET /vault/{id}/download?buyer_email= / transaction_id=` or `POST /vault/grant-access {vaultItemIds, buyer_email}`.

## 21. Rollback

Comment out `COMMERCIAL STATE desire/temperature/offer_ready` `workers:580` (7 lines) + `AVAILABLE CONTENT` `memory/context:340` + ranking fallback `product_selection:233` to `None`; delete `vault_taxonomy/content_matching/desire/temperature/offer_readiness`. No migration.

## 22. Remaining Risks

Vault `description` dead, per-item buyer downloadUrl blocked, tiered upsell not automated.

## 23. Exact Files Changed

`db/dropfans.py, commerce/vault_taxonomy.py NEW, commerce/content_matching.py, commerce/product_selection.py, commerce/desire.py, commerce/temperature.py, commerce/offer_readiness.py, commerce/objective.py, memory/context.py, workers/llm_worker.py, core/telemetry.py` + docs.

## 24. Architecture Invariants

Redis Streams, consumer groups, XAUTOCLAIM, debounce 3s, send dedup 3600, advisory lock ppv_offer:{c}:{u}:{p}, creator isolation, AUTONOMY_ENABLED, DropFans sole, scoring 0.1, tip canonical, funnel new→converted.

---

PHASE 3 IMPLEMENTATION COMPLETE

ROOT STATUS: CONDITIONALLY READY — vault → purchase → delivery chain now idempotent on sales_url; per-item buyer media blocked at DropFans boundary

VAULT: live /vault owner-signed filePath ~12h + /drops buyUrl + folders/video TUS + local mirror SHA256 synthetic id (description/media_count dead)

CONTENT MATCHING: title-token relevance desc, bundle-aware, purchased excluded, cheapest tie-breaker

PURCHASE ATTRIBUTION: poll product_id synthetic fix → attributable via reconcile, idempotent UNIQUE(creator,transaction,event)

MEDIA DELIVERY: owner filePath never leaked; sales_url fallback via vault reserve/finalize 5m reaper, idempotent UNIQUE(creator,user,media)

AFTERCARE: pending→RELATIONSHIP_BUILDING + Qwen surface (no immediate upsell, learn reaction)

REPEAT PURCHASE: is_repeat_purchase_eligible 168h + cheapest-unpurchased ranking (tier not auto)

LLM AUTHORITY: language only; product/price/URL/purchase/delivery/creator isolation deterministic — adversarial 8/8 blocked

TESTS: 29 new + 42 sunny + 53 forensic = 124 green; plus 570 relevant suite green (3 pre-existing drift)

MIGRATIONS: NONE (tool_audit_log 30d already, aftercare 20260826010000 already, generation_telemetry widen deferred)

ARCHITECTURE: NO REDESIGN

CANARY: NOT ACTIVATED

PROVIDER: UNCHANGED (ollama/qwen2.5:3b)

EXTERNAL BLOCKERS: DropFans per-buyer vault downloadUrl for purchased vault items

NEXT PHASE: Content taxonomy editor, per-item buyer grant, embedding recommender, abandoned auto-nudge cron

ROOT CAUSE: poll record_dropfans_sale product_id NULL → reconcile never found sales; aftercare dropped; multi-product None; Qwen never commercially led

FIX: synthetic product_id persistence, taxonomy + bundle-aware relevance, aftercare→Qwen surface, COMMERCIAL STATE bridge

WHY THE FAN NOW GETS THE CONTENT THEY PURCHASED: verified purchase webhook/poll → transaction_id='dropfans:{drop_id}' product_id synthetic → offer attribution pending/clicked→purchased → funnel converted → aftercare pending → reserve_delivery UNIQUE → enqueue_send media_path=sales_url caption + dedup → _process_send_stream reserve→send_file/sales_url → finalize pending→sent → aftercare, idempotent

