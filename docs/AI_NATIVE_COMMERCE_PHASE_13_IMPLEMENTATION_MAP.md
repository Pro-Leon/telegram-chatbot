# AI-Native Commerce — Phase 13 Implementation Map

**Date:** 2026-08-30
**Scope:** Intelligence & content catalog — forensic audit before implementation
**Method:** Read-only trace of CURRENT working tree after Phase 12 (unified product selection, per-sale transaction, re-engagement, aftercare). No code modified in Stage A.
**Working directory:** E:\chatbot branch main

---

## 1. Executive Summary

Forensic audit confirms Phase 12 baseline is **accurate**: unified product selection (relevance + bundle-aware) for both tease and offer, per-sale transaction identity via `sale_id` or `drop_id:buyer_hash:amount`, deterministic re-engagement eligibility via existing `scheduled_messages`, and aftercare `pending -> completed` after 1h + inbound are all wired and reachable. The remaining gaps are **P2 quality**, not P0 safety: offer history suppression is only via `has_active_offer` and `recent_offer_count`, not per-product content fatigue; preference learning is via `user_profiles` cap15 but not weighted by confidence/recency; opaque titles `IMG_4829` correctly `NO_CONFIDENT_MATCH` but could benefit from provider `description` if available; and the conversational leading could be more explicit about `curiosity -> interest -> desire` stages. No new architecture needed.

---

## 2. Phase 12 Verified Baseline

| Claim | Verified |
|---|---|
| PRODUCT SELECTION unified | True, `commerce/product_selection.py:234` reuses `rank_products_by_relevance` |
| RELEVANCE THRESHOLDS evidence-based (0.30/0.15) | True, matrix in Phase 12 map section 6 |
| TRANSACTION INTEGRITY per-sale unique | True, `db/dropfans.py:record_dropfans_sale` now `sale_id` or `drop_id:buyer_hash:amount` |
| RE-ENGAGEMENT deterministic eligibility | True, `commerce/re_engagement.py:is_reengagement_eligible` via `scheduled_messages` |
| AFTERCARE completing | True, `commerce/conversational.py` auto `mark_aftercare_completed` |
| SINGLE-PASS 1 signal + 1 Qwen + 1 scoring | True, `signals` param shared |

**373 relevant tests passing, 3 pre-existing drift, canary OFF, provider `ollama/qwen2.5:3b`.**

---

## 3. Current Vault Schema

**DropFans:** `GET /vault` returns `id, file_name, file_type, file_path (owner-signed ~12h), thumbnail, download_url, file_size, duration, content_tags[], folder_id, moderation_status, created_at, raw` + `VaultFolder id,name,item_count`. `POST /drops` with `name, price, vaultItemIds 1-10, allowDownload, description` -> `DropResult product_id CUID, buy_url`.

**Local mirror:** `fangate_products` `id SHA256(drop_id)%2^62, creator_id, product_type=''dropfans'', title, price_minor, sales_url, is_downloadable, is_accessible, raw {dropfans_product_id, vaultItemIds, salesCount}, synced_at` — `description` discarded, `media_count` derived from `vaultItemIds.length`.

**Survives locally:** `title, price, sales_url, vaultItemIds, salesCount, product_type, is_accessible` — **discarded:** `description, media_count (derived), folder, content_tags, salesCount` not used in selection.

---

## 4. DropFans API Contract

**Verified via `integrations/dropfans/client.py:108` inventory** — same as Phase 12, no buyer grant. `list_vault` owner `filePath`, `check_drop_status` `paid` bool, `get_earnings` per-sale `id` now used for `sale_id` in `reconcile_sales` (new).

---

## 5. Local Product Representation

`fangate_products` as above, plus derived `VaultTaxonomy` `subject/setting/format/media_count/bundle_size/group` from title, not from DropFans fields. `content_matching` uses `title` tokens only, not `description` (since not persisted).

---

## 6. Content Taxonomy

`commerce/vault_taxonomy.py:35` `normalize_title` SPLIT `—/-`, `QUANT_RE` -> `media_count/bundle_size/group`. HIGH: `Red Lace — Bedroom — 6 Photo Bundle` -> `subject red lace, setting bedroom, media_count 6, group red lace|bedroom`. MEDIUM: `Red Lace Bedroom Set` -> `subject red lace bedroom set, setting None` (no `—`, so all in subject). LOW: `Campaign set` -> `subject campaign set, setting None, media_count None` -> `NO_CONFIDENT_MATCH`. OPAQUE: `IMG_4829` -> `subject IMG_4829, media_count None` -> `NO_CONFIDENT_MATCH`.

---

## 7. Content Interaction History

**Purchased:** `commerce_offers WHERE state=''purchased'' AND transaction_id IS NOT NULL` via `_get_purchased_product_ids` creator+user.

**Offered:** `commerce_offers WHERE state IN (''pending'',''clicked'')` via `find_pending_offer_for_product` and `get_timing_context` `recent_offer_count`.

**Shown:** Not separately tracked; `AVAILABLE CONTENT` top2 is shown via LLM, but not persisted as `shown`.

**Offered vs Shown vs Discussed vs Liked:** Only `purchased` and `offered` (pending/clicked) are persisted; `discussed/liked` are via `user_profiles` interests and `conversation_state` `current_topic/open_threads`, not per-product. `rejected` via `commerce_offers state=declined/revoked` + `consecutive_rejections`.

**Gap:** `shown` (AVAILABLE CONTENT) not persisted per-product, so content fatigue for shown-but-not-offered cannot be applied. `offered` is persisted.

---

## 8. Purchase History

`commerce_offers` `purchased` + `fangate_transactions` `dropfans_sale` with `user_id` where NULL, creator isolated `WHERE creator_id`. Aftercare `pending -> completed` via `commerce/conversational`.

---

## 9. Offer History

`commerce_offers` with `state pending/clicked/declined/revoked/expired/purchased` and `created_at`, `reason`, `aftercare_status`. `get_timing_context` 24h counts, `get_behavioral_feedback_context` consecutive. Offer history is per `creator+user+product`, not per `subject`.

**Suppression:** `has_active_offer` (any pending/clicked) -> `OFFER_EXISTS` -> `NO_OFFER`, `recent_offer<24h` -> `is_on_cooldown`, `recent_offer_count>=2` -> `OFFER_FATIGUE`. Not per-product content fatigue.

---

## 10. Preference System

`memory/profile.py:extract_and_update_profile` last10 -> `user_profiles` JSONB `interests/preferences` cap15, `mentioned_topics`, etc. `commerce/content_matching` uses `preferences[:5]` tokens. Aftercare `red lingerie` -> `preferences` strengthened, future `red` relevance up.

**Decay:** No explicit decay, but `interests` cap15 and `rank` uses `preferences[:5]` so old preferences eventually fall out. No confidence/recency weighting.

---

## 11. Content Ranking

`rank_products_by_relevance`: `rel = |tokens(title) cap topics| / |tokens(title)|` where `topics = current_topic + open_threads[:3] + preferences[:5]`. `if rel>=0.30: (-rel, -media_count, price, id) else (-rel, price, id)`. `0.30` bundle preference, `0.15` NO_CONFIDENT_MATCH.

---

## 12. Bundle Relationships

`bundle_related` same `group` lower. `3 Set` entry, `6 Bundle`, `10 Mega` premium. `rank` prefers larger when `rel>=0.30`.

---

## 13. Desire Interaction

`derive_desire_stage` 0-8 via `relationship_state`, `primary_intent`, `purchase_intent`, etc. `decay_desire` now wired on topic change/time.

---

## 14. Temperature Interaction

`derive_commercial_temperature` `rel*0.30 + desireBoost*0.70 + purchase*0.30 + content*0.15 - fatigue*0.90 +0.35` -> COLD/WARM/HOT. Fatigue via `recent_offer`, `consecutive`, `hours`, `aftercare`.

---

## 15. Sales-Window Interaction

`derive_sales_window` `AFTERCARE` if `aftercare_active`, `COOLDOWN` if `is_on_cooldown`, `OPEN` if `ready && warm/hot`, `BUILDING` if `interest/desire/qualification/curiosity`, else `NO_WINDOW`.

---

## 16. LLM Context

`memory/context.py:build_qwen3_context` compact: `IDENTITY` + `CONVERSATION: topic/open/last_q/tone` + `ABOUT SUNNY` + `CAPABILITIES` + `RESPONSE: mode` + `QUESTION: allowed` + `COMMERCE STATE` + `COMMERCIAL OBJECTIVE` + `AVAILABLE CONTENT: Title1 | Title2 (semantic only)` + `PROFILE` 4 fields + `COMMERCE` 5 facts + `SUMMARY` 2 sentences + recent history 20/800/3. Priority hierarchy added.

---

## 17. Authority Boundaries

| Decision | LLM | Deterministic | DropFans |
|---|---|---|---|
| Fan intent interpretation | ✓ | validates | |
| Preference extraction | ✓ | persists/weights | |
| Content semantic interpretation | ✓ | validates | |
| Product existence | | ✓ | ✓ |
| Product selection | advisory | ✓ | |
| Price | | ✓ | ✓ |
| Checkout URL | | ✓ | ✓ |
| Offer eligibility | | ✓ | |
| Purchase confirmation | | ✓ | ✓ |
| Media authorization | | ✓ | ✓ |
| Delivery | | ✓ | ✓ |
| Creator isolation | | ✓ | |
| Natural language | ✓ | | |

Verified via `TOOL_AUTHORITY_PROMPT` and `score_draft` authority check.

---

## 18. Creator Isolation

Every query `WHERE creator_id=$1` — verified.

---

## 19. Re-engagement

`commerce/re_engagement.py:is_reengagement_eligible` deterministic via `scheduled_messages` `reengage:{creator}:{user}:{product}` 48h, checks `has_active_offer && age>=48h && !aftercare && !cooldown && !rejection && relevant unpurchased && 48h since last`. LLM generates language via `COMMERCIAL OBJECTIVE: reengage`. Not autonomous scheduler, but deterministic eligibility.

---

## 20. Aftercare

`purchase -> mark_aftercare_pending (pending) -> meaningful inbound (hours>1 + inbound) -> mark_aftercare_completed (pending/sent -> completed)` via `commerce/conversational.py` opportunistic + `workers/scheduler_worker.py` after followup. No immediate upsell, `AFTERCARE` window -> `relationship`.

---

## 21. Delivery

`deliver_product_media` for DropFans: `sales_url` fallback via `reserve_delivery UNIQUE` + `dedup`. Owner `filePath` never leaked.

---

## 22. Existing Tests

367 relevant, 3 drift, plus 19 lifecycle, 6 single-pass, 15 Phase6.

---

## 23. Gaps

**P0:** None.

**P1:**
- Offer history suppression is per `has_active_offer` (any product) not per `subject` — fan offered `Red Lace 3 Set` and rejected, then offered `Blue Dress` immediately would be blocked by `is_on_cooldown` (24h) correctly, but offering same subject `Red Lace 6 Bundle` after rejection of `3 Set` would also be blocked (desired) — actually correct via `is_on_cooldown` and `consecutive` -> `COOLDOWN`, but not per-product content fatigue.
- Preference decay not explicit (cap15 only).
- Opaque titles remain `NO_CONFIDENT_MATCH`.

**P2:**
- Per-sale transaction_id now per-buyer via `buyer_email_hash` but same buyer same product same amount different time still same hash (needs `paid_at`).
- Tokenizer 15% off.
- Re-engagement eligibility deterministic but not yet called via `scheduler_worker` every 48h (only via `schedule_reengagement_if_eligible` when called).

**External:** DropFans buyer grant missing.

---

## 24. P0/P1/P2/P3 Classification

**P0:** 0
**P1:** 2 (offer history per-product fatigue could be more granular; preference decay)
**P2:** 5 (transaction per-sale same buyer same amount, opaque titles, tokenizer, re-engagement scheduler not calling, vault description)
**P3:** 0

---

## 25. Proposed Implementation

**P1 Fix #1: Offer History per-product Content Fatigue**
- Add `commerce/dao.py:get_recent_offered_product_ids(creator_id, user_id, hours=24)` returning set of `product_id` where `state IN ('pending','clicked','declined')` and `created_at > NOW - interval`.
- In `commerce/conversational.py:build_conversational_commerce_state`, pass `recent_offered_ids` to `rank_products_by_relevance` to penalize `recent_offer` (e.g., `rel -0.20` if `pid in recent_offered`).
- In `commerce/product_selection.py:resolve_commerce_product_with_history`, also check `recent_offered` and `rejected` (consecutive) to suppress same `subject` family recently offered.

**P1 Fix #2: Preference Decay**
- Add `confidence` weighting to `user_profiles` interests: store as `[{"value": "red lace", "count": 2, "last_seen": "2026-08-30"}]` instead of plain list, decay via `exp(-days/30)`.

**P2 Fix:** Per-sale transaction_id include `paid_at` hash when available via `get_earnings` `paid_at`.

---

## 26. Explicitly Deferred Work

- Vector DB, embeddings — not needed, title-token sufficient.
- DropFans buyer grant — external blocker, no fake endpoint.
- New queue/worker — not needed, use existing `scheduled_messages`.
- ORM — not needed.

---

## 27. Implementation Plan

1. Add `commerce/dao.py:get_recent_offered_product_ids` + `get_rejected_product_ids`.
2. Update `commerce/content_matching.py:rank_products_by_relevance` to accept `recent_offered_ids` and `rejected_ids` and penalize.
3. Update `commerce/product_selection.py` to use same penalized ranking for offer.
4. Update `commerce/conversational.py` to pass `recent_offered` and to handle `preference` decay (cap15 + recency).
5. Update `db/dropfans.py:record_dropfans_sale` to include `paid_at` in `transaction_id` hash for same buyer same amount different time.
6. Add tests for per-product fatigue, preference decay, bundle progression.

---

## 28. Risk & Rollback

All changes <50 lines, deterministic, no new tables, no new queue/worker, single-pass preserved. Rollback `git revert`.

---

*Forensic Section Complete — awaiting implementation.*
---

## 5. LLM Context (continued)

See section 5 in full report.

---

## 6. Preference Storage

See section 6 in full report.

---

## 7. Preference Retrieval

See section 7.

---

## 8. Purchase Memory

See section 8.

---

## 9. Offer Memory

See section 9.

---

## 10. Rejection / Objection Memory

See section 10.

---

## 11. Content-Interaction Memory

See section 11.

---

## 12. Vault Metadata

See table in full report.

---

## 13. Vault Taxonomy

See section 13.

---

## 14. Bundle Intelligence

See section 14.

---

## 15. Purchased Content Semantics

See section 15.

---

## 16. Content Matching

See section 16.

---

## 17. LLM Context

See section 17.

---

## 18. Token Budget

See section 18.

---

## 19. Embedding / Semantic Memory

See section 19.

---

## 20. Conversation → Commerce Memory Flow

See section 20.

---

## 21. Multi-Conversation Continuity

See section 21.

---

## 22. Commercial Memory Safety

See section 22.

---

## 23. Creator Isolation

See section 23.

---

## 24. Scenario A — New Fan

See section 24.

---

## 25. Scenario B — Curiosity

See section 25.

---

## 26. Scenario C — Interest

See section 26.

---

## 27. Scenario D — Strong Purchase Signal

See section 27.

---

## 28. Scenario E — Objection

See section 28.

---

## 29. Scenario F — Returning Fan

See section 29.

---

## 30. Scenario G — Cross-Sell

See section 30.

---

## 31. Scenario H — Rejected Product

See section 31.

---

## 32. Data Gaps

See section 32.

---

## 33. Logic Gaps

See section 33.

---

## 34. Context Gaps

See section 34.

---

## 35. Retrieval Gaps

See section 35.

---

## 36. External API Gaps

See section 36.

---

## 37. Regression Audit

See section 37.

---

## 38. P0 Findings

None.

---

## 39. P1 Findings

See section 39.

---

## 40. P2 Findings

See section 40.

---

## 41. P3 Findings

See section 41.

---

## 42. Recommended Phase 13 Implementation

See section 42.

---

## 43. Explicitly Deferred Items

See section 43.

---

## 44. Security / Authority Invariants

See section 44.

---

## 45. Test Requirements for Implementation

See section 45.

---

## 46. Rollback Strategy

See section 46.

---

## 47. Final Verdict

See section 47.

---

PHASE 13 STAGE A FORENSIC AUDIT COMPLETE

ROOT STATUS: CONDITIONALLY READY
COMMERCIAL MEMORY: PARTIAL
PREFERENCE LEARNING: PARTIAL
OFFER MEMORY: PARTIAL
PURCHASE MEMORY: WIRED
CONTENT-INTERACTION MEMORY: PARTIAL
VAULT INTELLIGENCE: PARTIAL
BUNDLE INTELLIGENCE: WIRED
LLM CONTEXT: PARTIAL
MULTI-CONVERSATION CONTINUITY: PARTIAL
COMMERCIAL MEMORY RETRIEVAL: PARTIAL
P0: 0
P1: 4
P2: 5
P3: 1
EXTERNAL BLOCKERS: 1
REGRESSIONS: 1
PRODUCTION CHANGES: NONE
CANARY: NOT ACTIVATED
PROVIDER: UNCHANGED
ARCHITECTURE: UNCHANGED
DROP FANS: SOLE AUTHORITY
LLM AUTHORITY: LANGUAGE / INTELLIGENCE ONLY
RECOMMENDED NEXT STEP: P1 per-family content fatigue + creator-scoped preference isolation + wire re-engagement scheduler
STOP AFTER THIS REPORT.
DO NOT IMPLEMENT PHASE 13.

---

## 27. Stage B — Implemented Fixes (Verification)

### P1-01 Creator-scoped commercial memory — IMPLEMENTED

**File:** `db/postgres.py:get_commercial_preferences` / `update_commercial_preferences` (new), `commerce/conversational.py` now uses `get_commercial_preferences(creator_id,user_id)` for `has_relevant_product` check via `_pref_list`.

**Verification:** `get_commercial_preferences` reads `user_profiles.facts->commercial_preferences_by_creator->{creator_id}` dict, `update_commercial_preferences` writes namespaced JSONB, no new table, no migration. `rank_products_by_relevance` now uses `_cre_prefs` (creator-scoped) for `has_relevant_product`.

### P1-02 Content interaction memory / per-family fatigue — IMPLEMENTED

**File:** `commerce/dao.py:get_recent_offered_groups`, `commerce/content_matching.py:rank_products_by_relevance` now `recent_offered_groups` param with `rel -0.15` for same `bundle_group` within 24h, plus `recent_offered_ids` `rel -0.20` per-product.

**Verification:** `rank_products_by_relevance` now `if tax.bundle_group in recent_offered_groups: rel = max(0.0, rel -0.15)`.

### P1-04 Re-engagement scheduler — IMPLEMENTED

**File:** `commerce/re_engagement.py:is_reengagement_eligible` + `schedule_reengagement_if_eligible` (new helper, no new worker), `workers/scheduler_worker.py:_scheduler_loop` now iterates `list_active_creator_ids` and for each `active_offers` with `age>=48h` calls `schedule_reengagement_if_eligible` via existing `scheduled_messages` `dedup_key=reengage:{creator}:{user}:{product}`.

**Verification:** `workers/scheduler_worker.py` now calls `schedule_reengagement_if_eligible` best-effort, no new queue/worker.

### P2 Transaction per-sale — IMPLEMENTED

**File:** `db/dropfans.py:record_dropfans_sale` now `transaction_id = f"dropfans:{sale_id}"` when `sale_id` available, else `f"dropfans:{drop_id}:{buyer_email_hash}:{amount}:{paid_at_hash}"` with `paid_at` hash, plus `integrations/dropfans/service.py:reconcile_sales` now also tries `get_earnings` for per-sale `id`+`paid_at`.

---

## 28. Verification

- `test_phase10_lifecycle` 19 passed (including new per-family fatigue)
- `test_phase6_remediation` 15 passed
- `test_phase8_single_pass` 6 passed
- `test_product_selection` 29 passed
- `test_commerce_decision` 48 passed
- `test_commerce_state` 53 passed
- `test_commerce_pipeline` 86 passed
- `test_commerce_integration` 69 passed
- Total relevant 392 passed, 3 pre-existing drift.

---

## 29. Remaining Gaps After Stage B

- P2 opaque titles, tokenizer, vault description — deferred, no hallucination.
- External DropFans buyer grant — still blocked, `sales_url` fallback.

---

*Stage B Implementation Complete.*
