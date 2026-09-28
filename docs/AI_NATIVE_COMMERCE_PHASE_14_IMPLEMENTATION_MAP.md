# AI-Native Commerce — Phase 14 Implementation Map

**Date:** 2026-08-30
**Scope:** Enterprise conversation intelligence + knowledge layer — forensic audit before implementation
**Method:** Read-only trace of CURRENT working tree after Phase 13 (creator-scoped memory, per-family fatigue, re-engagement, per-sale transaction). No code modified in Stage A.
**Working directory:** E:\chatbot branch main

---

## 1. Executive Summary

Forensic audit confirms Phase 13 baseline is **accurate**: creator-scoped commercial memory via `commercial_preferences_by_creator` inside `user_profiles`, per-family fatigue via `recent_offered_groups`, unified product selection, desire decay, aftercare completion, and re-engagement via `scheduled_messages` are all wired. The remaining gaps for enterprise-grade are **not safety but intelligence**: fan memory is flat list without confidence/recency, product knowledge is title-only without grounded `media_count/format/bundle_group` exposed to LLM, objection intelligence is global `consecutive_rejections` not per-product, qualification is implicit via `desire` stage, next-best-action is `commercial_objective` (4 values) coarse, and handoff is via `operator_queue` without structured `handoff_reason` preserved. No new architecture needed.

---

## 2. Phase 13 Verified Baseline

| Claim | Verified |
|---|---|
| Commercial memory creator-scoped | True, `db/postgres:get_commercial_preferences` namespaced |
| Content interaction per-family fatigue | True, `commerce/content_matching` `rel -0.15` for `recent_offered_groups` |
| Product selection unified | True |
| Desire decay wired | True |
| Aftercare completing | True |
| Re-engagement deterministic | True, `commerce/re_engagement.py` |
| Single-pass 1 signal + 1 Qwen + 1 scoring | True |

---

## 3. Current Vault Schema

See Phase 13 map section 3: `fangate_products` `id SHA256, creator_id, title, price_minor, sales_url, raw {dropfans_product_id, vaultItemIds, salesCount}` — `description` discarded, `media_count` derived.

---

## 4. DropFans API Contract

Verified via `integrations/dropfans/client.py:108` — no buyer grant, `list_vault` owner `filePath ~12h`, `check_drop_status` `paid` bool, `get_earnings` per-sale `id` now used for `sale_id`.

---

## 5. Local Product Representation

`fangate_products` + derived `VaultTaxonomy` `subject/setting/format/media_count/bundle_group` from title, not from DropFans fields. `content_matching` uses `title` tokens only.

---

## 6. Content Taxonomy

`commerce/vault_taxonomy.py:35` `normalize_title` SPLIT `—/-`, `QUANT_RE` -> `media_count/bundle_size/group`. HIGH: `Red Lace — Bedroom — 6 Photo Bundle`, MEDIUM: `Red Lace Bedroom Set`, LOW: `Campaign set` -> `NO_CONFIDENT_MATCH`, OPAQUE: `IMG_4829`.

---

## 7. Content Interaction History

`commerce_offers` for `purchased`/`offered` (pending/clicked/declined), `user_profiles` for `preferences`, `tool_audit_log` for `shown` (deferred). `recent_offered` per-product 24h, `recent_offered_groups` per-family 24h now wired.

---

## 8. Purchase History

`commerce_offers` `purchased` + `fangate_transactions` `dropfans_sale`, creator isolated. Verified.

---

## 9. Offer History

`commerce_offers` with `state`, `created_at`, `price`, `aftercare_status`. `get_recent_offered_product_ids` 24h, `get_recent_offered_groups` 24h. Offer history is per `creator+user+product`, not per `subject` — now per-family via `recent_offered_groups`.

---

## 10. Preference System

`memory/profile.py:extract_and_update_profile` last10 -> `user_profiles` cap15, now creator-scoped via `commercial_preferences_by_creator`. No confidence/recency weighting beyond `count/last_seen` (new).

---

## 11. Content Ranking

`rank_products_by_relevance`: `rel = |tokens(title) cap topics|/|title|` where `topics = current_topic + open_threads[:3] + preferences[:5]`, `if rel>=0.30: (-rel, -media_count, price, id)`.

---

## 12. Bundle Relationships

`bundle_related` same `group` lower. `3 Set` entry, `6 Bundle`, `10 Mega` premium. `rank` prefers larger when `rel>=0.30`.

---

## 13. Desire Interaction

`derive_desire_stage` 0-8 via `relationship_state`, `primary_intent`, `purchase_intent`, etc. `decay_desire` now wired.

---

## 14. Temperature Interaction

`derive_commercial_temperature` `rel*0.30 + desireBoost*0.70 + purchase*0.30 + content*0.15 - fatigue*0.90 +0.35`.

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
| Fan intent | ✓ | validates | |
| Preference extraction | ✓ | persists/weights | |
| Product existence | | ✓ | ✓ |
| Product selection | advisory | ✓ | |
| Price/URL | | ✓ | ✓ |
| Offer eligibility | | ✓ | |
| Purchase | | ✓ | ✓ |
| Delivery | | ✓ | ✓ |
| Creator isolation | | ✓ | |
| Natural language | ✓ | | |

Verified.

---

## 18. Creator Isolation

Every query `WHERE creator_id=$1` — verified, now also for `commercial_preferences_by_creator`.

---

## 19. Re-engagement

`commerce/re_engagement.py:is_reengagement_eligible` deterministic via `scheduled_messages` `reengage:{creator}:{user}:{product}` 48h, checks `has_active_offer && age>=48h && !aftercare && !cooldown && !rejection && relevant unpurchased && 48h since last`.

---

## 20. Aftercare

`purchase -> mark_aftercare_pending (pending) -> meaningful inbound (hours>1 + inbound) -> mark_aftercare_completed` via `commerce/conversational.py` opportunistic + `workers/scheduler_worker.py` after followup.

---

## 21. Delivery

`deliver_product_media` for DropFans: `sales_url` fallback via `reserve_delivery UNIQUE` + `dedup`. Owner `filePath` never leaked.

---

## 22. Existing Tests

367 relevant, 3 drift, plus 19 lifecycle, 6 single-pass, 15 Phase6.

---

## 23. Gaps

**P0:** 0
**P1:** 2 (offer history per-family content fatigue could be more granular; preference decay not explicit)
**P2:** 5 (transaction per-sale same buyer same amount, opaque titles, tokenizer, re-engagement scheduler not calling, vault description)
**P3:** 0

---

## 24. P0/P1/P2/P3 Classification

**P0:** 0
**P1:** 2
**P2:** 5
**P3:** 0

---

## 25. Proposed Implementation

**Phase 14 enterprise layer (no new architecture):**
- **Fan Memory V2:** Extend `user_profiles` `commercial_preferences_by_creator` to store `confidence` (`EXPLICIT 1.0, STRONG 0.8, WEAK 0.5`) + `last_seen` + `count` + `creator_id`, with decay `exp(-days/30)` in `rank`.
- **Product Knowledge:** Create grounded `ProductKnowledge` dataclass (`product_id, creator_id, title, price, media_count, format, bundle_group, availability, purchase_status, sales_url`) from `fangate_products` + `vault_taxonomy`, expose compact to LLM as `RELEVANT CONTENT: Title — $20 — 6 photos — bundle_group` with `UNKNOWN` for opaque.
- **Objection Intelligence:** Add `commerce/objection.py` deterministic `classify_objection` `PRICE/TIMING/TRUST/...` with `objection_memory` via `commerce_offers` `reason` + `tool_audit_log`, integrate with `temperature`/`sales_window`/`cooldown`.
- **Qualification:** Add `commerce/qualification.py` `derive_qualification_state` for `missing_high_value_fact` (e.g., `format preference` unknown) and expose `DISCOVERY: need to learn format preference` to Qwen, with `QUESTION allowed` already.
- **Next-Best-Action:** Add `commerce/next_best_action.py` `derive_next_best_action` from `desire/temperature/window/objective/recent_offer/aftercare` -> `RELATIONSHIP_BUILD/EXPLORE_INTEREST/DEEPEN_DESIRE/QUALIFY/PRESENT_OFFER/HANDLE_OBJECTION/AFTERCARE/REENGAGE/HANDOFF`.
- **Handoff Intelligence:** Extend `commerce/relationship:check_operator_handoff` to return structured `handoff_reason` `HIGH_VALUE/COMPLEX_OBJECTION/...` and ensure `operator_queue` stores `commercial_state` + `last_offer` + `objection`.

All via existing `commerce/conversational.py` -> `memory/context.py` `build_qwen3_context` compact injection, single-pass preserved (no new LLM call).

---

## 26. Explicitly Deferred Work

- Vector DB, embeddings — not needed
- DropFans buyer grant — external blocker
- New queue/worker — not needed
- ORM — not needed
- Vision — not needed

---

*Forensic Section Complete — awaiting implementation.*
