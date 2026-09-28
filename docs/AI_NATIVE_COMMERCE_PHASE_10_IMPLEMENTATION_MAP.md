# AI-Native Commerce — Phase 10 Implementation Map

**Date:** 2026-08-30
**Scope:** Consistency & lifecycle closure — unify product selection, wire desire decay, aftercare completion, single commercial state
**Method:** Forensic trace of CURRENT working tree after Phase 8 single-pass + Phase 9 forensic. No architecture redesign.
**Working directory:** E:\chatbot branch main

---

## 1. Forensic Baseline (Stage A)

### 1.1 Current Lifecycle (Verified)

```
Telegram inbound handlers.py:25 -> debounce db/redis:277 -> XADD inbound_messages
  -> llm_worker:956 XREADGROUP llm_workers -> process_message:467 acquire_user_lock -> build_qwen3_context memory/context:453 (conversation_state, response_mode, question_budget, AVAILABLE CONTENT relevance-ranked) -> extract_commerce_signals once (via conversational bridge) -> _try_commerce_draft:356 (single-creator, product_selection cheapest, CommerceStateRequest, resolve_and_run_commerce with signals=once -> pipeline run_commerce_pipeline with signals=once -> decide 23-branch -> strategy -> orchestrate -> execute_ppv advisory lock ppv_offer:{c}:{u}:{p} -> selection) -> build_conversational_commerce_state commerce/conversational.py (reuses same signals, derive_desire/temperature/readiness/window/objective) -> COMMERCIAL STATE -> Qwen generate_draft (1) -> score_draft authority-aware (1) -> is_auto_reply -> enqueue_send SEND_STREAM -> _process_send_stream chatbotv2/main:77 -> Telegram
  -> DropFans poll reconcile_sales -> record_dropfans_sale synthetic pid SHA256%2^62 -> reconcile_unattributed 7d/50 -> funnel converted -> mark_aftercare_pending -> enqueue confirmation -> schedule followup 24h -> deliver_product_media sales_url fallback + reserve_delivery UNIQUE
```

Single-pass verified: 1 signal + 1 Qwen + 1 scoring = 3, not 4. Worker delegates to `commerce/conversational` helper, not hardcoded.

### 1.2 Product-Selection Trace

| Location | Function | Ranking Rule | Authority |
|---|---|---|---|
| `commerce/content_matching.py:71` | `rank_products_by_relevance` | `rel = |tokens(title) cap topics|/|title|` where `topics = current_topic + open_threads[:3] + preferences[:5]`; `if rel>=0.30: (-rel, -media_count, price, id) else (-rel, price, id)`; `media_count` from `vault_taxonomy` or `raw.vaultItemIds.length`; purchased excluded via `purchased_ids` | For tease: determines `AVAILABLE CONTENT` top2 |
| `commerce/product_selection.py:234` | `resolve_commerce_product_with_history` | `valid = is_accessible && sales_url` (creator isolated `WHERE creator_id`); `if 1 valid -> id`; `if 2+ valid: exclude purchased; if 1 unpurchased -> id; if 2+ unpurchased -> sorted(price, id) cheapest` | For offer: determines `CommerceStateRequest.product_id` -> `ProductIdentity` -> `execute_ppv` |
| `memory/context.py:592` | `rank_products_by_relevance` call | Same as content_matching, top2 titles | For LLM tease |
| `commerce/conversational.py` | `build_conversational_commerce_state` | Calls `list_valid_products` to check `has_relevant_product` but not for ranking; uses `derive_desire_stage` etc. | For conversational state |

**Contradiction:** Tease ranking is `relevance + bundle-aware` (larger when `rel>=0.30`), offer ranking is `cheapest` (price, id). Example: `Red Lace — Bedroom — 3 Photo Set $20` vs `6 Photo Bundle $30` with `rel=0.66` for red interest -> tease ranks 6 Bundle first (`-rel, -6, price`), offer ranks 3 Set first (`price 20 < 30`). Fan teased larger bundle but offered smaller — value confusion. **P1-01**.

### 1.3 Bundle-Selection Trace

`commerce/vault_taxonomy.py:67` `parse_taxonomy` SPLIT `—/-`, `QUANT_RE (\d+ photo|set|bundle|pack)` -> `media_count/bundle_size/group=subject|setting lower`. `bundle_related` same `group`. `rank_products_by_relevance` uses `media_count` when `rel>=0.30`. `product_selection` uses `price` only, not `media_count`. Bundle awareness exists for tease, not for offer.

### 1.4 Desire-Decay Trace

`commerce/desire.py:127` `decay_desire(confidence, hours_elapsed, topic_changed)` defined as `factor = 1.0; if hours>=24: factor*=0.70*(hours/24) capped 0.3-1.0 else max(0.7); if topic_changed: factor*=0.70; return max(0.15, min(0.95, confidence*factor))`. **Verified:** 0 callers in production (grep `decay_desire` = 0 hits outside tests). `commerce/conversational.py` re-derives `desire` each turn from fresh `purchase_intent` etc, so decay is implicit via lower `purchase_intent` when topic changes, but not via `decay_desire`. The function is **defined but unwired**. `commerce/temperature.py` also has no decay param. **P1-02**.

### 1.5 Aftercare Trace

`commerce/post_purchase.py:handle_post_purchase` calls `mark_aftercare_pending(creator_id,user_id)` (sets `aftercare_status=pending` where `state=purchased AND aftercare_status=none`). `commerce/dao.py:935` defines `mark_aftercare_pending`, `mark_aftercare_completed`, `get_aftercare_status`. `get_behavioral_feedback_context` returns `aftercare_status`. `decision.py` `AFTERCARE_PHASE -> RELATIONSHIP_BUILDING` unless explicit buy. **Verified:** `mark_aftercare_completed` is defined but 0 callers in production (grep 0). Stays `pending` forever until manual. No scheduler calls it after 24h followup. **P1-03**.

### 1.6 Commercial-Objective Trace

`commerce/objective.py:derive_commercial_objective` maps `selection` + `relationship_state` to `relationship/build_desire/present_offer/aftercare` (4 values). `derive_desire_stage` has 9 stages, `derive_sales_window` has 5, but `objective` is coarse (4). `build_conversational_commerce_state` returns `desire/temp/readiness/window/objective` — 5 separate values, but `objective` is derived only from `selection` + `relationship_state`, not from `desire/temperature/window` directly. **Contradiction:** `temperature=COLD` + `objective=present_offer` is prevented (since `present_offer` only when `selection USE`), but `offer_ready=False` + `objective=present_offer` is also prevented (same). However `window=aftercare` + `objective=relationship` could happen when aftercare pending but selection is `FALLBACK` -> `objective=relationship` (should be `aftercare`). The current mapping in `objective.py` does handle `aftercare` when `relationship_state` in `PURCHASED/VIP` -> `aftercare`, but `build_conversational_commerce_state` passes `relationship_state` from `derive_relationship_state` which may be `purchased` after purchase, so it does map to `aftercare`. Not contradictory, but coarse. **P1-04**.

### 1.7 Purchased Exclusion Trace

Every lookup `WHERE creator_id=$1 AND user_id=$2 AND state=purchased` via `_get_purchased_product_ids` and `has_purchased_product`. Verified creator isolation `WHERE creator_id` everywhere. **No gap.**

### 1.8 Offer-Readiness / Cooldown / Repeat

`commerce/offer_readiness.py` `evaluate_offer_readiness` with `has_active_offer/is_on_cooldown/aftercare_active/has_relevant_product/not_purchased` — correctly gated. `get_timing_context` 24h/6h, `get_behavioral_feedback_context` consecutive>=3. `is_repeat_purchase_eligible` 168h via `commerce/feedback`. Aftercare completion missing -> repeat never auto, but gated correctly.

---

## 2. Current Lifecycle

See 1.1. Desired lifecycle is same, but with unified ranking, wired decay, and aftercare completion.

---

## 3. Product-Selection Trace

See 1.2.

---

## 4. Bundle-Selection Trace

See 1.3.

---

## 5. Desire-Decay Trace

See 1.4.

---

## 6. Aftercare Trace

See 1.5.

---

## 7. Commercial-Objective Trace

See 1.6.

---

## 8. Contradiction Analysis

| Contradiction | Impact | Root Cause |
|---|---|---|
| Tease (relevance+bundle) vs Offer (cheapest) | Fan teased larger bundle but offered smaller — breaks `TEASE vs OFFER consistency` invariant | Two ranking policies, `product_selection` not bundle-aware |
| Desire decay defined but not propagated | Single `OFFER_READY` could stay if fan repeats same topic, no gradual `HOT->WARM->COLD` | `decay_desire` unwired, re-derivation each turn is not topic-change decay |
| Aftercare pending forever | `AFTERCARE_PENDING` never -> `COMPLETED` -> `relationship` -> `repeat` blocked until manual | No scheduler hook after followup |
| Commercial objective coarse (4 values) vs desire 9 stages | `curiosity/interest_discovery/soft_tease/qualification/objection_handling` all map to `build_desire` — Qwen cannot distinguish tease vs qualification | `objective.py` mapping too coarse |

---

## 9. Exact Fixes

### Fix #1 — Unify Product Selection (P1-01)

**File:** `commerce/product_selection.py:234`

**Current:** `sorted(unpurchased, key=lambda p: (price, id))` cheapest.

**Desired:** `rank_products_by_relevance` logic but deterministic and explainable, with relevance as primary, bundle as secondary, price as tie-breaker, id as final. Must use same `topics = current_topic + open_threads + preferences` and same `rel>=0.30` bundle rule, but for deterministic offer we cannot use LLM `current_topic`? We can: `current_topic` from `derive_conversation_state` is deterministic (14 keywords + recent). Use it.

**Fix:** Change `resolve_commerce_product_with_history` to accept `current_topic, open_threads, preferences` (optional, from conversational state) and rank via `rank_products_by_relevance` when available, else fallback to cheapest. Keep `creator isolation + purchased exclusion + unavailable exclusion + relevance threshold 0.15 (if no confident match, no offer)` as per `content_matching.best_match_or_none`. Ensure `list_valid_products` already filters `is_accessible && sales_url`. Keep `price` tie-breaker.

**Authority:** Deterministic `fangate_products` mirror, `price_minor`, `sales_url`, `creator_id` — no LLM invent. Reuse `commerce/content_matching.rank_products_by_relevance` and `commerce/vault_taxonomy.parse_taxonomy` already, no new ranking.

**Failure:** If `rel<0.15` for all -> `NO_CONFIDENT_MATCH` -> `product_id=None` -> `NO_OFFER` (relationship behavior) — not invent.

**Test:** `Test 1 unified product ranking`, `Test 2 purchased exclusion`, `Test 3 bundle consistency`.

### Fix #2 — Wire Desire Decay (P1-02)

**File:** `commerce/conversational.py`

**Fix:** After deriving `desire` via `derive_desire_stage`, check if `conversation_state` indicates `topic_changed` (compare `current_topic` vs `recent_topics` or `open_threads` volatile) and `hours_since_last_offer`. If `topic_changed` or `hours>=24`, call `decay_desire(desire.confidence, hours_elapsed, topic_changed)` and if decayed confidence `<0.40`, downgrade `desire.stage` one step (`OFFER_READY->QUALIFICATION->DESIRE->INTEREST->CURIOSITY->RELATIONSHIP`). Do not set to 0 directly. Preserve relationship.

**File:** `commerce/temperature.py` already has fatigue, but ensure `hours_since` passed correctly (already).

**Test:** `Test 6 desire decay` and `Test 7 decay does not erase relationship`.

### Fix #3 — Aftercare Completion (P1-03)

**File:** `commerce/post_purchase.py` + `workers/scheduler_worker.py` (existing scheduler)

**Fix:** Add `mark_aftercare_completed_if_eligible(creator_id, user_id)` that checks: `aftercare_status` is `pending` and `hours_since_purchase` >24 and fan has sent at least one message since purchase (or `extract_and_update_profile` has run). Call it from `workers/scheduler_worker.py` after `schedule_follow_up` execution (when `scheduled_messages` due) or from `commerce/conversational.py` when building state: if `aftercare_status pending` and `last_message` is inbound after `purchased_at`, then `await mark_aftercare_completed`. This transitions `pending -> completed` via existing `commerce/dao:mark_aftercare_completed` (sets where `state=purchased AND aftercare_status IN ('pending','sent')`). No new table.

**Test:** `Test 8 aftercare pending` (immediately after purchase, no upsell) and `Test 9 aftercare completion` (after meaningful interaction, `AFTERCARE_COMPLETED`).

### Fix #4 — One Commercial State (P1-04)

**File:** `commerce/conversational.py`

**Fix:** Consolidate `desire/temp/readiness/window/objective/selected_product` derivation into single `build_conversational_commerce_state` call per turn, already done. Ensure `commercial_objective` mapping is consistent with `desire/temperature/window` by adding check: if `window==aftercare` then `objective=aftercare`, if `window==cooldown` then `objective=relationship` (already via `derive_commercial_objective` + `relationship_state`), and if `readiness==ready && window==open` then `objective=present_offer` only when `selection USE`. Add assertion in helper to log inconsistency.

**Test:** `Test 13 commercial objective consistency`.

---

## 10. Authority Boundaries

LLM: language/tone/tease/qualification wording only. Deterministic: product/price/URL/purchase/delivery/creator isolation/AUTONOMY. No change.

---

## 11. Test Plan

- 18 tests as per Phase 10 Testing Requirements (unified ranking, purchased exclusion, bundle, weak interest, strong signal, decay, aftercare pending/completed, preference learning, repeat, objection, objective consistency, creator isolation, LLM authority, offer/delivery idempotency, full lifecycle).
- Adversarial: topic change, free content, photo request, price reject, ignore, return, DropFans fail, LLM fail, duplicate webhook.

---

## 12. Deferred Issues

- P2 transaction uniqueness `dropfans:{drop_id}` not per-sale -> document, downstream fail-closed.
- P2 opaque titles `IMG_4829` -> no embedding, title-token only.
- P2 re-engagement contextual not autonomous -> keep.
- P2 tokenizer `tiktoken gpt-4` ~15% off -> defer.
- P2 `vault description` not persisted -> defer.

---

*Forensic Section Complete — awaiting implementation.*
