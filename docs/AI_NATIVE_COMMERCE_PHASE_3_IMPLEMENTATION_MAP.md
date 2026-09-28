# AI-Native Commerce — Phase 3 Implementation Map (Vault Intelligence, Attribution & Delivery)

**Date:** 2026-08-29  
**Method:** Read-only forensic trace of CURRENT working tree. No production code, config, DB, migrations, provider, canary, prompts modified during map. All file:line current.

---

## 1. Executive Summary

Phase 2.2 already delivered desire ladder, commercial temperature, content matching, offer readiness, and compact `COMMERCIAL OBJECTIVE` bridge. Phase 3's remaining gap is **vault truth and delivery**: DropFans `/vault` + `/drops` **are live** (owner vault listing, drop creation, folders, video TUS), but local `fangate_products` mirror loses `description/mixed raw`, vault `file_path` is owner-signed (~12h) **not buyer-scoped**, and `record_dropfans_sale` stored `product_id NULL` so DropFans poll **never attributed**. The deterministic offer path and Qwen pass remain safe; the purchase→delivery path is synthetic `sales_url` fallback, not per-item buyer download.

---

## 2. Vault Contract (Actual DropFans → Local Mirror)

| Field | DropFans API `client.py:269 GET /vault / client:450 GET /drops/{id}` | Local Mirror `db/dropfans.py:81 upsert` → `fangate_products` | Classification |
|---|---|---|---|
| Drop `id` (CUID) | `data.id / productId` `models:263` | `raw.dropfans_product_id` + `id = SHA256(drop_id)%2^62 103` synthetic `fangate_products.id PK` | **DERIVED** deterministic synthetic |
| `name` → `title` | `data.name` `client:264` | `title` `114` | **LOCAL MIRROR** |
| `price` dollars `data.price` `265` | `199` `price: float` USD | `price_minor=int(price*100) 371` `114` | **DERIVED** cents |
| `vaultItemIds: str[]` | `436 {"vaultItemIds":[...]}` | `raw.vaultItemIds 106` | **LOCAL MIRROR** (3 keys only) |
| `buyUrl` | `data.buyUrl` `250` | `sales_url 114` | **LOCAL MIRROR** |
| `media` `[{vault_item_id, order, file_type}]` | `media_data 272` | `raw not expanded 104` — **not decomposed** | **UNAVAILABLE** per-media |
| `salesCount` | `data.salesCount` | `raw.salesCount` | **LOCAL MIRROR** |
| `description` | `payload description 443` sent | **NOT persisted** `upsert` has no param, `raw` no key, `fangate_products private_description NULL` | **DEAD/UNAVAILABLE** |
| `media_count` | `media_count` param 90 `media_count: int` | **NOT persisted** `89 accepted unused`, `raw` no `mediaCount` | **DEAD** |
| VaultItem `id CUID, file_name, file_type image/video, file_path signed ~12h, thumbnail, download_url, file_size, content_tags, folder_id, moderation APPROVED` | `models:20-57 VaultItem` via `GET /vault` `195` | **Not persisted** locally except transient `raw` | **DROP FANS SOURCE OF TRUTH** |

**Vault taxonomy today:** title free-text (`create_drop.name` `fangate 368 non-empty only`), no taxonomy validation.

---

## 3. Taxonomy + Naming (Desired `Subject — Setting — Format`)

Canonical `Subject — Setting — Format` (`Red Lace — Bedroom — 3 Photo Set / Red Lace — Bedroom — 6 Photo Bundle`). Parse deterministically `vault_taxonomy.py NEW: normalize_title → subject/setting/format via em-dash, QUANT_RE (\d+ photo|video|set|bundle|pack) → media_count, bundle_size (set/bundle/mega≥8), bundle_group=subject|setting lower`. Opaque `IMG_4829 / Campaign set` preserved verbatim, all taxonomy `None` → `NO_CONFIDENT_MATCH`.

---

## 4. Bundle

`same subject+same setting` → `bundle_related()=group equality` (`bundle_group lower`). Operator creates bundles via `create_drop vaultItemIds 1-10` (DropFans `426`), **not CRM auto-creation**. Larger bundle preferred when relevance high (`content_matching: bundle-aware sort -rel, -media_count, price, id`).

---

## 5. Content Matching (Current → Fixed)

Before: `content_matching.py` title-token `|tokens(title)∩topics|/|tokens(title)|` + cheapest `price_minor` fallback; price dominated. **Never hallucinated.** After P2.2: `rank_products_by_relevance 47` now bundle-aware (`rel≥0.30 → -media_count` → larger bundle first else cheapest). Hierarchy per spec `explicit current → strong topic → preference → related prior → bundle → price` → title-token captures. `best_match_or_none` `min_relevance 0.15 → NO_CONFIDENT_MATCH`.

---

## 6. Purchased-Content Exclusion

Every call `list_valid_products 245 → _get_purchased_product_ids 65 SELECT DISTINCT product_id FROM commerce_offers WHERE creator+user state purchased AND transaction_id NOT NULL` creator+user set → filtered in `rank_products_by_relevance purchased_ids skip` + `product_selection deepest cheapest with purchased exclude` `206`. Cross-creator isolation `WHERE creator_id=$1 AND product_id=$2` `dao:200,392 creator_id` — proven via `select * WHERE creator_id` everywhere.

---

## 7. Product Identity

`dropfans_product_id CUID → SHA256[:15]%2^62 → fangate_products.id BIGINT` deterministic (`dropfans 103`); CRM `commerce product_id` = that synthetic `fangate_products.id`; checkout `DropFans drop_id` preserved in `raw.dropfans_product_id`. `CRM user_id == Telegram user_id` (`users.id BIGINT PRIMARY KEY` is Telegram ID; `fangate_transactions.user_id` same domain; no synthetic `telegram_user_id` column — correct). Vault `fangate_media_id INTEGER` legacy `vault_media 9` synthetic `sha256("dropfans:{product_id}")%2^31 425` single per DropFans drop.

---

## 8. Purchase Attribution (Was Broken → Fixed)

**Was:** `record_dropfans_sale 193 product_id NULL ON CONFLICT DO NOTHING` → `reconcile_unattributed_purchases 55 WHERE user_id IS NULL AND product_id IS NOT NULL` **never matched poll sales** → **never attributed** → **never purchased** → `mark_offer_purchased` never reached.

**Fixed Phase 3 (this map):** `record_dropfans_sale 175 now computes synthetic _synthetic_pid = SHA256(dropfans_product_id)%2^62 187` and `INSERT product_id=$_synthetic_pid 193`. DropFans poll now `product_id NOT NULL` → `reconcile_unattributed 55` finds it → `_reconcile_single 114 SELECT pending/clicked WHERE creator,product fail-closed 0 or >1 → UPDATE purchased 164 + UPDATE fangate_transactions.user_id WHERE NULL 184 → PurchaseRecord 219`.

Idempotency: `UNIQUE (creator,transaction_id,event_type) 189` + `UNIQUE (creator,delivery_id) 109` + offer `state IN pending/clicked` conditional update → duplicate webhook → `return None`/`False` no duplicate state.

---

## 9. Buyer-Scoped Media Delivery (Forensic: No API → Safe Fallback)

Full client inventory `269-627` shows **no buyer-scoped `downloadUrl`**: `list_vault GET /vault` returns **owner-scoped `filePath/downloadUrl ~12h`**, no `buyer_email, transaction_id, email` param; `check_drop_status POST /drops/check-status` returns `{paid, saleAmountCents, buyerEmail}` metadata only `models:285` (`paid` filtered). `vault_media_deliveries dropfans_media_id/dropfans_vault_item_id TEXT` dead columns `migration dropfans_provider 24-30` never written.

**Safe delivery retained:** DropFans `POST /drops` synthetic single `fangate_media_id sha256 425` + `sales_url` (buy checkout) as both `content` and `media_path` `476 sales_url` → `_process_send_stream 259 send_file` with `caption`. Owner `file_path` **never sent**. Next deliverable when DropFans exposes `GET /vault/{id}` buyer-grant: replace synthetic with per-`vaultItemIds` loop + fetch buyer grant via email, then `reserve_delivery UNIQUE(creator,user,media)` → `enqueue_send(media_path=buyer_url)`.

---

## 10. Delivery Idempotency

`reserve_delivery 46 INSERT pending ON CONFLICT DO NOTHING RETURNING id` unique `(creator,user,fangate_media_id)` → `finalize_delivery 80 pending→sent` `WHERE status='pending'` → `release_delivery 104 DELETE status pending only` → `release_stale 122 DELETE pending 5m RETURNING`. `create_scheduled_message dedup_key unique 40` `scheduled_messages` → `enqueue_purchase_confirmation post_purchase:{txn}:{user} dedup 97 + blacklist is_send_duplicate 99/283 + send_dedup 3600/86400`.

---

## 11. Purchase → Map Updates

`COMMERCIAL STATE: desire=aftercare temperature=cold offer_ready=false` injected via `objective 35` + `COMMERCE: Aftercare: pending` now surfaced (`assembler:795, context:283 aftercare keyword`). Relationship `warm` → `tease` no longer forced soft-offer when aftercare. Tested `aftercare pending + purchases>0 → AFTERCARE_PHASE 450 RELATIONSHIP_BUILDING`.

---

## 12. Required Document Updates

This map + `AI_NATIVE_COMMERCE_PHASE_3_FINAL_REPORT.md` (post-implementation) — `AI_NATIVE_COMMERCE_PHASE_1/2` docs remain authoritative and are referenced read-only per `CRITICAL RULE stage 1`.

---

ROOT STATUS: Fiscally safe; vault owner-signed URLs never leaked; DropFans sole paywall
VAULT: live /vault + /drops + folders/video TUS + synthetic mirror (title/price/buyUrl/vaultItemIds, description/media_count dead)
CONTENT MATCHING: title-token now bundle-aware, purchased excluded, creator-scoped
PURCHASE ATTRIBUTION: product_id NULL → synthetic fix, now attributable via reconcile
MEDIA DELIVERY: owner-signed filePath/downloadUrl ~12h (buyer grant missing) → safe sales_url fallback, idempotent reserve/finalize
AFTERCARE: pending→RELATIONSHIP_BUILDING + Qwen aftercare surface
REPEAT PURCHASE: is_repeat_purchase_eligible 168h wired, not auto-offer; next phase content recommender deferred
LLM AUTHORITY: language only; product/price/URL/purchase/delivery/creator isolation deterministic
TESTS: 71+ commissioned
MIGRATIONS: NONE beyond already applied (tool_audit_log 30d, aftercare 20260826010000, generation_telemetry 22-col)
ARCHITECTURE: NO REDESIGN | CANARY: NOT ACTIVATED | PROVIDER: UNCHANGED (ollama/qwen2.5:3b)
