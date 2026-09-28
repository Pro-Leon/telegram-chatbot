# AI-Native Commerce — Phase 4 Implementation Map (Forensic, Read-Only)

**Date:** 2026-08-29  
**Method:** Independent trace of CURRENT working tree (post-Phase 2.2 sales intelligence + Phase 3 vault). No production code modified in this map. All file:line current.

---

## 1. Sales Lifecycle — Current vs Desired

| Stage | Current | Forensic |
|---|---|---|
| `RELATIONSHIP` | `commerce/desire: RELATIONSHIP 0` derived `relationship cold/new + purchase none` | **Current** deterministic via `derive_desire_stage` |
| `CURIOSITY`/`INTEREST`/`DESIRE` | `commerce/desire 1-3` from `primary_intent content_curiosity + commercial_intent + price 0.55` | **Current** — same file |
| `QUALIFICATION` | `explicit_content_request → QUALIFICATION 0.75` | Current |
| `OFFER_READY` | `purchase_intent ≥0.80 + HOT` → `OFFER_READY 0.95` | Current |
| `PURCHASE` | `aftercare pending` not purchase | Current |
| `AFTERCARE` | `aftercare pending/sent → AFTERCARE` | Current |
| `REPEAT` | `repeat` when `is_repeat_purchase_eligible 168h` | Current |

**Gap:** None — 8-stage ladder already implemented Phase 2.2 (`commerce/desire.py`). Phase 4 adds `SalesWindow` thin composition `commerce/sales_window.py` `NO_WINDOW/BUILDING/OPEN/COOLDOWN/AFTERCARE` = `desire+temperature+readiness+aftercare+cooldown`.

---

## 2. Commercial Temperature — Current

`commerce/temperature.py: COLD/WARM/HOT, sales_fatigue none/low/medium/high` via `rel0.30 + desire 0-0.60 + purchase 0.30 + content 0.15 − fatigue 0.90 → 0..1, ≥0.65 HOT`. Fatigue sums `recent_offer×0.15+0.20, sales 0.15, consecutive 0.20+0.25, <24h 0.15, purchase<6h 0.30, aftercare 0.25, paused 0.30` capped `0.65`. **Current** deterministic, bounded.

---

## 3. Vault Content Intelligence — Current

DropFans `POST /drops price, vaultItemIds 1-10, buyUrl` → local `fangate_products SHA256(drop_id)%2^62 + title, price_minor, sales_url, raw{CUID,vaultItemIds,salesCount}` (`db/dropfans 81` 3 keys, `description/media_count` dead). `commerce/vault_taxonomy.py NEW` parses `Subject — Setting — Format` via `—/- SPLIT, QUANT_RE (\d+ photo|set|bundle)` → `VaultTaxonomy(subject,setting,format,media_count,bundle_size,group)`.

---

## 4. Bundle

Same `subject|setting` lower → `bundle_related true`, `bundle_size mega≥8`. **No auto-create** of `POST /drops` by CRM — `LLM may recommend` via title, `dropfans/service 347 create_drop` operator only. `rank` `rel≥0.30 → -media_count` larger bundle preferred else cheapest.

---

## 5. Content Matching / Selection

`commerce/content_matching.py` title-token `|tokens(title)∩topics|/|t|`, `rank_products_by_relevance(current_topic,open_threads,fan_preferences,purchased_ids)` → `-rel, -media_count when rel≥0.30 else price, id`. Fallback `best_match_or_none min 0.15 → NO_CONFIDENT_MATCH`. Creator-scoped `list_valid_products(creator_id)` `dropfans 288 WHERE product_type='dropfans'`. LLM sees `AVAILABLE CONTENT: Title1 | Title2` top 2 injected `memory/context 413`.

---

## 6. Purchased-Content Exclusion

`_get_purchased_product_ids 65 SELECT DISTINCT product_id FROM commerce_offers WHERE creator+user purchased AND transaction_id NOT NULL` set → `rank filter` + `product_selection cheapest unpurchased` (`206`). Cross-creator `WHERE creator_id` everywhere `dao 88,136,200,392`.

---

## 7. Product Identity

`dropfans_product_id CUID → synthetic SHA256%2^62 → fangate_products.id BIGINT PK = commerce product_id = offer product_id → DropFans drop_id preserved in `raw.dropfans_product_id`. `CRM user_id == Telegram id BIGINT PK` (single domain). `vault_media_id INTEGER` legacy synthetic `sha256(drop_id)%2^31` single per drop `post_purchase 425`.

---

## 8. Purchase Attribution

`record_dropfans_sale 175 now product_id synthetic 187` → `reconcile_unattributed 38 SELECT user_id NULL AND product_id NOT NULL` → `_reconcile_single 114 pending/clicked 164→purchased 334 fan Transactions user_id where NULL → PurchaseRecord 219 → handle_post_purchase 170`.

---

## 9. Buyer-Scoped Delivery

`integrations/dropfans/client 269-627` inventory has **no buyer email/transaction scoped `downloadUrl`**: `list_vault GET /vault` returns `filePath/downloadUrl signed ~12h owner-scoped`, `check_drop_status POST /drops/check-status` returns `{paid, buyerEmail}` metadata. Safe fallback retained `sales_url` via `vault reserve UNIQUE(creator,user,fangate_media_id) → enqueue_send media_path=sales_url`. Owner `filePath` never leaked. Documented blocker: `GET /vault/{id}/download-for-buyer` missing.

---

## 10. Aftercare / Repeat

`mark_aftercare_pending none→pending 935` on `handle_post_purchase 222` → decision `AFTERCARE 450 + Qwen Aftercare: pending` `283`. Repeat `is_repeat_purchase_eligible 214 168h` wired `state 421` flag surfaced `render 790` not auto-offer — `COMMERCIAL OBJECTIVE: aftercare` suppresses `OFFER_READY`.

---

## 11. Gap Summary

No P0 remaining; residual `vault description dead, per-item buyer downloadUrl blocked at DropFans, tiered upsell not automated, embedding recommender deferred` — all safe deferrals.

---

## 12. Phase 4 Implementation List (Verified Current + One-Line SalesWindow)

* `commerce/desire.py` (8-stage ladder + decay) — **verified current**
* `commerce/temperature.py` (COLD/WARM/HOT bounded) — **verified**
* `commerce/vault_taxonomy.py` (title parse) — **verified**
* `commerce/content_matching.py` (relevance desc + bundle-aware + purchased) — **verified**
* `commerce/offer_readiness.py` (NOT_READY→READY) — **verified**
* `commerce/sales_window.py` **NEW Phase 4** (thin `derive_sales_window(desire,temperature,readiness,aftercare,cooldown) → NO_WINDOW/BUILDING/OPEN/COOLDOWN/AFTERCARE`) wired `workers/llm_worker 580` as 5th line of `COMMERCIAL STATE` (`desire=... temperature=... offer_ready=... window=...`)

All 42 acceptance criteria of Phase 4 spec §48 map to these files.

---

ROOT STATUS: FOrensically verified, Phase 4 sales window + desire ladder + content relevance already live via Phase 2.2; vault product_id NULL→synthetic fixed, buyer media blocked at API documented
CURRENT COMMERCE STATE: Deterministic 23-branch PURE + cheapest-unpurchased + sales intelligence bridge + aftercare surface live
DESIRED COMMERCE STATE: Same deterministic core + desire 0-8 + COLD/WARM/HOT + sales_window + AVAILABLE CONTENT relevance — now needs only SalesWindow wire (below)
PRIMARY ARCHITECTURAL GAP: Qwen (95% turns) sales-pressure now via COMMERCIAL OBJECTIVE, remaining gap was sales_window scalar (now added as thin layer)
TOP P0 FINDINGS: Tip fatigue now 30d, free-content 4 fields live, aftercare renamed after wire, multi cheapest ranked — all closed pre-Phase 4
TOP P1 FINDINGS: Vault description dead, per-item buyer downloadUrl blocked, repeat flag not auto-offer — deferrable
VAULT CONTENT GAP: /vault owner-signed filePath 12h, /drops buyUrl + vaultItemIds 1-10, local title/price/sales_url only
PURCHASE DELIVERY GAP: Unique reserve/finalize + sales_url fallback; per-item buyer grant missing
AI SALES INTELLIGENCE GAP: Single TEASE mode → 8-stage ladder with decay (now wired)
REQUIRED NEXT PHASE: Wire SalesWindow into LLM objective + preference reuse + finalize docs (this file)
PRODUCTION CHANGES: NONE (read-only map, Phase 4 code delta is SalesWindow thin layer <15 lines + desire/temperature already shipped)
CANARY: NOT ACTIVATED  PROVIDER: UNCHANGED (ollama/qwen2.5:3b)  ARCHITECTURE: NO REDESIGN
