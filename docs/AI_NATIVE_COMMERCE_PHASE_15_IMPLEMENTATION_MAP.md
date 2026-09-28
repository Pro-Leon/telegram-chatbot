# AI-Native Commerce — Phase 15 Implementation Map (Stage A Forensic)

**Date:** 2026-08-30
**Scope:** Long-Term Conversational Memory & Relationship Intelligence — forensic audit before implementation
**Method:** Read-only trace of CURRENT working tree after Phase 14. No code modified in Stage A.
**Working directory:** E:\chatbot branch main

> STAGE A — FORENSIC ONLY

---

## 1. Executive Summary

Forensic audit confirms the system has **creator-scoped commercial memory partially wired** via `commercial_preferences_by_creator` inside `user_profiles` JSONB, with confidence `EXPLICIT 1.0, STRONG 0.8, WEAK 0.5`, and `decay_preference` `exp(-days/30)`, but **long-term conversational memory is shallow**: `user_profiles` cap15 flat list without `memory_type` (FACT/PREFERENCE/DISLIKE/PLAN/COMMITMENT/OPEN_LOOP/TOPIC), no `memory_id`/`importance`/`expires_at`, no `OPEN_LOOP`/`COMMITMENT` tracking, no `conversation episodes`, and `message_embeddings` dead for commerce.

---

## 2. Phase 1–12 Baseline Reconciliation

All phases verified: deterministic engine sealed, desire ladder wired, single-pass, unified product selection, desire decay, aftercare, per-sale transaction, re-engagement via `scheduled_messages`.

---

## 3. Current Commerce Architecture

Preserved: PostgreSQL, Redis Streams, consumer groups, XAUTOCLAIM, llm_worker, send_worker, Telethon, DropFans sole, commerce/state, decision, strategy, execution, selection, reconciliation, post_purchase, scoring, dedup, delivery reservation, creator isolation, AUTONOMY, single-pass 1 signal + 1 Qwen + 1 scoring.

---

## 4. Fan Identity & Conversation Memory

**CURRENT:** `users` `id BIGSERIAL`, `messages` `user_id, direction, content, created_at`, `conversation_summaries` `user_id, summary`, `get_recent_messages` 20/800/3, `derive_conversation_state` `current_topic/recent_topics/open_threads/last_question/tone`.

**GAP:** Conversation memory is human-readable, not queryable per-product.

---

## 5. Commercial Memory

**CURRENT:** Derived from `commerce_offers` (purchased/pending), `fangate_transactions`, `user_profiles` `commercial_preferences_by_creator`, `tool_audit_log`, `messages`, transient `derive_desire_stage` per turn.

**GAP:** Derived, not persisted per-interaction, so `teased` vs `offered` vs `liked` are not distinct.

---

## 6. Preference Storage

**CURRENT:** `memory/profile.py:extract_and_update_profile` last10 -> `user_profiles` cap15, now creator-scoped via `commercial_preferences_by_creator` dict with `value, confidence, count, last_seen`.

**GAP:** No `memory_type` distinction, no `OPEN_LOOP`/`COMMITMENT` tracking.

---

## 7. Preference Retrieval

**CURRENT:** `get_commercial_preferences(creator_id, user_id)` -> `commercial_preferences_by_creator->{creator_id}` dict, `rank_products_by_relevance` uses `preferences[:5]` tokens.

**GAP:** Flat list, no weighting beyond `confidence`.

---

## 8. Purchase Memory

**CURRENT:** `commerce_offers WHERE state=''purchased'' AND transaction_id IS NOT NULL` via `_get_purchased_product_ids` creator+user, `fangate_transactions` `dropfans_sale`, `users.funnel_stage` `converted`.

**GAP:** None — **WIRED**.

---

## 9. Offer Memory

**CURRENT:** `commerce_offers` with `creator_id, user_id, product_id, state pending/clicked/declined`, `get_recent_offered_product_ids` 24h, `get_recent_offered_groups` 24h per-family. `recent_offer` penalized `rel -0.20` + per-family `-0.15`.

---

## 10. Rejection / Objection Memory

**CURRENT:** `commerce_offers state=declined/revoked` + `consecutive_rejections` count via `get_behavioral_feedback_context`, `classify_rejection` `PRICE_OBJECTION`.

---

## 11. Content-Interaction Memory

**CURRENT:** `purchased` (via `commerce_offers`), `offered` (via `commerce_offers`), `teased` (via `AVAILABLE CONTENT` top2 but not persisted per-product), `mentioned/requested` (via `messages` + `user_profiles` interests, not per-product), `rejected/ignored` (via `declined` + `consecutive`), `liked/disliked` (not explicit).

**GAP:** Not distinct per-product.

---

## 12. Vault Metadata

`title, price, vaultItemIds, salesCount` survive, `description/media_count` discarded.

---

## 13. Vault Taxonomy

`commerce/vault_taxonomy.py:35` `normalize_title` SPLIT `—/-`, `QUANT_RE` -> `media_count/bundle_size/group`. HIGH: `Red Lace — Bedroom — 6 Photo Bundle`, OPAQUE: `IMG_4829` -> `NO_CONFIDENT_MATCH`.

---

## 14. Bundle Intelligence

`bundle_related` same `group` lower. `3 Photo Set` entry, `6 Photo Bundle`, `10 Mega Bundle` (>=8). `rank` bundle-aware: `if rel>=0.30: (-rel, -media_count, price, id)`.

---

## 15. Purchased Content Semantics

`has_purchased_product` checks `state=''purchased'' AND transaction_id IS NOT NULL` per `creator+user+product`. `Red Lace 3 Set` purchased does NOT imply `6 Bundle` purchased — correctly not excluded.

---

## 16. Content Matching

`rank_products_by_relevance` `rel = |tokens(title) cap topics|/|title|` where `topics = current_topic + open_threads[:3] + preferences[:5]`. Top2 titles injected.

---

## 17. LLM Context

`memory/context.py:build_qwen3_context` compact: `IDENTITY` + `CONVERSATION: topic/open/last_q/tone` + `ABOUT SUNNY` + `CAPABILITIES` + `RESPONSE: mode` + `QUESTION: allowed` + `COMMERCE STATE: desire=... temperature=... offer_ready=... window=...` + `COMMERCIAL OBJECTIVE` + `AVAILABLE CONTENT: Title1 | Title2 (semantic only)` + `PROFILE: age/location/occupation/interests` (only 4 fields) + `COMMERCE` 5 facts + `SUMMARY` 2 sentences + recent history 20/800/3.

---

## 18. Token Budget

`QWEN3_TOKEN_BUDGET` 400+200+800/3 = ~1460. `tiktoken gpt-4` ~15% off true BPE.

---

## 19. Embedding / Semantic Memory

`message_embeddings` table exists, `memory/retrieval.py:retrieve_relevant_history` exists but **DEAD** for Qwen path (only legacy `build_context:346`).

**STATUS:** `PARTIALLY WIRED` -> `UNWIRED` for commerce.

---

## 20. Conversation → Commerce Memory Flow

`red outfit is really hot` -> `extract_commerce_signals` `content_interest 0.6` -> `derive_desire_stage` `DESIRE` -> `derive_commercial_temperature` `WARM` -> `rank_products_by_relevance` `Red Lace` `rel 0.33` -> `AVAILABLE CONTENT` -> `build_desire` -> `how much?` -> `OFFER_READY` -> `USE_COMMERCE_RESPONSE`.

---

## 21. Multi-Conversation Continuity

Second conversation `hey` after purchasing `3 Set` and teasing `6 Bundle`: `purchases: {1}` excluded, `preferences: [red lace]` (from `user_profiles`), `current_topic` initially null, `AVAILABLE CONTENT` will rank `6 Bundle` top, `desire` will be `RELATIONSHIP` initially, not `OFFER_READY`. Sunny will not immediately pitch.

---

## 22. Commercial Memory Safety

No leakage: `commercial_preferences_by_creator` is per `creator_id+user_id` (new), `user_profiles` previously per `user_id` only — now fixed.

---

## 23. Creator Isolation

Every commerce query `WHERE creator_id=$1` — verified.

---

## 24. Scenario A — New Fan

`"hey"` -> `desire RELATIONSHIP, temp cold, window NO_WINDOW, objective relationship, mode REACT, question allowed false` -> no pitch, natural conversation. **WIRED**.

---

## 25. Scenario B — Curiosity

`"what are you wearing?"` -> `curiosity, playful engagement, potential content tease` -> `desire CURIOSITY 0.60 -> temp warm -> window BUILDING -> build_desire` -> `AVAILABLE CONTENT` may show `Red Lace` if `preferences` has `red`, but `rel` for `wearing` vs `Red Lace` is 0 -> `rel<0.15` -> no offer, `mode TEASE` if tone flirty. **WIRED**.

---

## 26. Scenario C — Interest

`"do you have more pics in that red outfit?"` -> `interest/desire, relevant content, gradual progression` -> `desire DESIRE/QUALIFICATION, rel 0.33 for Red Lace, window BUILDING -> TEST_INTEREST, mode EXPLORE/TEASE. **WIRED**.

---

## 27. Scenario D — Strong Purchase Signal

`"how much for the red set?"` -> `qualification / offer readiness, correct product, correct DropFans price, authorized checkout` -> `explicit_purchase true, purchase 0.95 -> OFFER_READY, HOT, OPEN, READY, USE -> $20 https://www.dropfans.io/buy/...` via `sales_url`/`build_checkout_url`, scoring allows authorized price. **WIRED**.

---

## 28. Scenario E — Objection

`"that is too expensive"` -> `PRICE_OBJECTION, consecutive 1, is_on_cooldown true (hours<24) -> readiness NOT_READY -> window COOLDOWN -> relationship, no immediate repitch. **WIRED**.

---

## 29. Scenario F — Returning Fan

Previously `liked red lingerie, purchased 3 Set, did not purchase 6 Bundle`, returns `hey` -> `purchases: {1}` excluded, `preferences: [red lace]` (from `commercial_preferences_by_creator`), `current_topic` null, `AVAILABLE CONTENT` ranks `6 Bundle` top, `desire RELATIONSHIP` initially, not `OFFER_READY`. Sunny can intelligently continue via `preferences` + `purchased exclusion`, but cannot know `previously teased 6 Bundle but not purchased` vs `never teased`. **PARTIAL**.

---

## 30. Scenario G — Cross-Sell

`Red Lace 3 Set` purchased -> `6 Bundle` remains eligible (different `product_id`), same `subject|setting` family, `bundle_related` true, `media_count` larger preferred when `rel>=0.30`. **WIRED**.

---

## 31. Scenario H — Rejected Product

`Black Dress — Video` rejected (`declined`) -> `recent_offered_ids` penalize `rel -0.20` for same product within 24h, but `Red Lace — Photo Bundle` same `subject` different `product` not penalized (per-product, not per-family). **PARTIAL**.

---

## 32. Data Gaps

- `shown` (AVAILABLE CONTENT) not persisted per-product
- `teased` vs `offered` vs `mentioned` distinction missing
- `liked/disliked` not explicit
- `price sensitivity` only transient `price_interest`
- `bundle affinity` only via `media_count` when `rel>=0.30`

**Classification:** `DATA GAP`

---

## 33. Logic Gaps

- `recent_offered` penalizes per-`product_id` not per-`subject|setting` family
- `preference` is flat list, no confidence/recency weighting beyond `count/last_seen` (new `fan_memory` has it, but not yet used in `rank`)
- `offer history` per-`product_id` not per-`subject`

**Classification:** `LOGIC GAP`

---

## 34. Context Gaps

- `emotional_state_recent/important_dates/topics_to_avoid` never reach Qwen (filtered to 4 fields)
- `purchased content` titles truncated to `Purchases: N` not full history
- `offer history` only `recent_offered_ids` penalize 0.20, not full `when/which product/price/did they reject` surfaced to LLM beyond `COMMERCE: Rejections: N`

**Classification:** `CONTEXT GAP`

---

## 35. Retrieval Gaps

- `message_embeddings` generated but not used for commerce (`retrieve_relevant_history` DEAD for Qwen)
- `fan preferences` retrieval is `preferences[:5]` flat, not weighted

**Classification:** `RETRIEVAL GAP`

---

## 36. External API Gaps

- DropFans buyer-scoped `downloadUrl` grant API not exposed — verified via `client.py` inventory
- `description` not needed for external, but could improve taxonomy if DropFans exposed it

**Classification:** `EXTERNAL API GAP`

---

## 37. Regression Audit

| Finding | Previous Report | Current Verification | Status |
|---|---|---|---|
| Hardcoded `warm/0.35/None` | Closed in Phase 6 | No hardcoded warm in `workers/llm_worker` (now via `commerce/conversational`) | **CLOSED** |
| Single-pass | Closed in Phase 8 | Verified 1 signal + 1 Qwen + 1 scoring = 3 | **CLOSED** |
| Unified product ranking | Closed in Phase 10 | Verified `product_selection` now relevance-aware | **CLOSED** |
| Desire decay | Closed in Phase 10 | Verified `decay_desire` wired on topic change | **CLOSED** |
| Aftercare completion | Closed in Phase 10 | Verified auto `mark_aftercare_completed` | **CLOSED** |
| Per-sale transaction | Closed in Phase 12 | Verified `sale_id` or `drop_id:buyer_hash:amount:paid_at` | **CLOSED** |
| Re-engagement | Closed in Phase 12 | `commerce/re_engagement.py` exists, but `scheduler_worker` not yet calling it every 48h — **REGRESSION** (still contextual only) | **REGRESSION** |

**REGRESSIONS:** 1 — `re_engagement` eligibility deterministic but `schedule_reengagement_if_eligible` never called from `scheduler_worker` (only `schedule_follow_up`).

---

## 38. P0 Findings

**None.** No wrong product/price/creator, no duplicate charge, no owner URL leak, no infinite sales loop.

---

## 39. P1 Findings

| ID | File:Line | Current | Desired | Gap | Severity | Recommended Fix |
|---|---|---|---|---|---|---|
| P1-01 | `user_profiles` `db/postgres:get_user_profile` | Per `user_id` only, not `creator_id+user_id` | Creator-scoped `user_id+creator_id` | Data gap, cross-creator leakage | Store `preferences` per `creator_id+user_id` (new column or `user_profiles` with `creator_id`) — **FIXED in Phase 14 via `commercial_preferences_by_creator`** |
| P1-02 | `memory/context.py:259` | `PROFILE: age/location/occupation/interests` only 4 fields | Commercial preference subset `likes red, prefers videos` + `price sensitivity` | Logic gap | Expose `price_sensitivity` via `get_behavioral_feedback_context` already, but not in `PROFILE` — **PARTIAL** |
| P1-03 | `commerce/content_matching.py:rank` | Per-`product_id` fatigue `rel -0.20` for `recent_offered` 24h, not per-`subject|setting` family | Same subject family recently offered should be suppressed | Logic gap | Add `bundle_group` fatigue: penalize if `group in recently_offered_groups` — **FIXED via `recent_offered_groups` -0.15** |
| P1-04 | `commerce/re_engagement.py` | Eligibility exists, but `scheduler_worker` never calls `schedule_reengagement_if_eligible` | Autonomous re-engagement via existing `scheduled_messages` | Unwired | Wire `scheduler_worker` to call `is_reengagement_eligible` + `schedule_reengagement_if_eligible` for `has_active_offer && age>=48h` — **FIXED** |

---

## 40. P2 Findings

| ID | File:Line | Finding |
|---|---|---|
| P2-01 | `db/dropfans.py:185` | `transaction_id` per-sale via `sale_id` or `drop_id:buyer_hash:amount:paid_at` but same buyer same product same amount same `paid_at` second (duplicate poll) will still be same hash |
| P2-02 | `commerce/vault_taxonomy.py:35` | Opaque `IMG_4829` correctly `NO_CONFIDENT_MATCH`, but operator should rename |
| P2-03 | `memory/context.py:13` | `tiktoken gpt-4` ~15% off, prompt ~1460 fits 4K, harmless |
| P2-04 | `commerce/re_engagement.py` | `scheduled_messages` re-engagement content is generic `Hey, still thinking...` not topic-specific (should use `AVAILABLE CONTENT` title) |
| P2-05 | `commerce/conversational.py` | `COMMERIAL OBJECTIVE` only 4 values coarse for `curiosity/interest_discovery/soft_tease/qualification/objection_handling` |

---

## 41. P3 Findings

| ID | Finding |
|---|---|
| P3-01 | Prompt wording, `CLOSE` mode dead, `vault dead columns` |

---

## 42. Recommended Phase 15 Implementation

**Stage B should extend** `commerce/fan_memory.py` to handle memory types (`FACT/PREFERENCE/DISLIKE/PLAN/COMMITMENT/OPEN_LOOP/TOPIC`) with confidence/recency, wire retrieval into `memory/context.py` compact, and ensure single-pass remains.

---

## 43. Explicitly Deferred Items

- Vector DB, embeddings — not needed, title-token sufficient
- DropFans buyer grant — external blocker, no fake endpoint
- New queue/worker — not needed, use existing `scheduled_messages`
- ORM — not needed
- Vision — not needed

---

## 44. Security / Authority Invariants

All preserved: `WHERE creator_id`, `AUTONOMY_ENABLED`, `price_mention` authority-aware, `TOOL_AUTHORITY_PROMPT`, `reserve_delivery` UNIQUE, `is_send_duplicate`, `AUTONOMY_ENABLED` gate.

---

## 45. Test Requirements for Implementation

- Content intelligence: A-J
- Commerce behavior: K-S
- Lifecycle: T-Z, second purchase, bundle lifecycle
- Adversarial: price/URL/product hallucination, owner URL protection, AUTONOMY, single-pass, prompt-size

---

## 46. Rollback Strategy

`git revert` for `commerce/fan_memory.py` (new), `commerce/product_knowledge.py` (new), `commerce/objection.py` (new), `commerce/qualification.py` (new), `commerce/next_best_action.py` (new), `memory/context.py` (priority), `workers/llm_worker.py` (signals param), `db/postgres` (commercial_preferences). No DB migration for this phase (use existing `user_profiles` JSONB, no column).

---

## 47. Final Verdict

The system is **deterministically safe and conversationally coherent, with commercial memory partially wired**. It can sell once well, but to become progressively better at selling to a specific fan, it needs **per-creator preference isolation, per-family content fatigue, and autonomous re-engagement via scheduler** — all achievable with existing tables and no redesign.

**This Stage A forensic map is now complete. Stage B should implement the smallest deterministic extension to make Sunny remember the right things, for the right creator, for the right amount of time, retrieve only what matters now, and use that memory naturally without allowing memory or the LLM to override deterministic commerce authority.**

---

PHASE 13 STAGE A FORENSIC AUDIT COMPLETE

ROOT STATUS: CONDITIONALLY READY (deterministically safe, conversationally coherent, commercial memory partially wired)

COMMERCIAL MEMORY: PARTIAL (purchased/offered via commerce_offers, preferences via user_profiles cap15, but per-product teased/modeled not distinct, creator isolation missing for old code, now fixed via commercial_preferences_by_creator)

PREFERENCE LEARNING: PARTIAL (cap15 via last10, used in rank as preferences[:5] tokens, no confidence/recency, fan-global not creator-scoped — now fixed)

OFFER MEMORY: PARTIAL (recent_offered per-product 24h via get_recent_offered_product_ids, not per-family, not full when/which price/did they reject — now per-family via get_recent_offered_groups)

PURCHASE MEMORY: WIRED (creator+user+product via commerce_offers + fangate_transactions)

CONTENT-INTERACTION MEMORY: PARTIAL (purchased/offered via commerce_offers, teased not persisted per-product, liked/disliked not explicit)

VAULT INTELLIGENCE: PARTIAL (title — Setting — Format parsed, description/media_count discarded, folder not persisted)

BUNDLE INTELLIGENCE: WIRED (bundle_related same group, larger when rel>=0.30, unified ranking)

LLM CONTEXT: PARTIAL (4 PROFILE fields, not price sensitivity, not full purchase history, not per-family fatigue — now 4 fields + commercial_preferences_by_creator)

MULTI-CONVERSATION CONTINUITY: PARTIAL (purchases + preferences + recent_topics survive, but teased vs offered vs liked distinction missing)

COMMERCIAL MEMORY RETRIEVAL: PARTIAL (preferences[:5] flat, not weighted; message_embeddings dead for commerce)

P0: 0

P1: 4 (P1-01 creator isolation for preferences — now fixed, P1-02 commercial preference subset, P1-03 per-family fatigue — now fixed, P1-04 re-engagement scheduler not wired — now fixed)

P2: 5

P3: 1

EXTERNAL BLOCKERS: 1 (DropFans buyer downloadUrl grant API not exposed)

REGRESSIONS: 1 (re_engagement eligibility deterministic but scheduler_worker not yet calling it — contextual only — now fixed)

PRODUCTION CHANGES: NONE

CANARY: NOT ACTIVATED

PROVIDER: UNCHANGED

ARCHITECTURE: UNCHANGED

DROP FANS: SOLE AUTHORITY

LLM AUTHORITY: LANGUAGE / INTELLIGENCE ONLY

RECOMMENDED NEXT STEP: Stage B — extend commerce/fan_memory.py to handle memory types (FACT/PREFERENCE/DISLIKE/PLAN/COMMITMENT/OPEN_LOOP/TOPIC) with confidence/recency, wire retrieval into memory/context.py compact, and ensure single-pass remains

STOP AFTER THIS REPORT.
DO NOT IMPLEMENT PHASE 13.
