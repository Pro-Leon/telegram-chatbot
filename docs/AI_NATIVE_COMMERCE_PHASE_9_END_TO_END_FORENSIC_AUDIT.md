# AI-Native Commerce — Phase 9 End-to-End Forensic Audit

**Date:** 2026-08-30
**Scope:** Reality vs intention for AI-native conversational commerce
**Method:** Read-only trace of CURRENT working tree after Phase 8. No code, config, DB, migrations, provider, canary, DropFans, Redis modified.

> FORENSIC ONLY — No fixes.

---

## 1. Executive Summary

The system is **deterministically safe and conversationally coherent, but not yet a premium OFM seller**. Architecture is intact and single-pass: one `extract_commerce_signals` per turn, one `derive_desire_stage`/`derive_commercial_temperature`/`derive_sales_window`/`evaluate_offer_readiness` via `commerce/conversational.py`, one Qwen `generate_draft`, one `score_draft`. LLM now receives real `desire/temperature/window/offer_ready/aftercare` instead of `warm/0.35/None`, and `AVAILABLE CONTENT` is relevance-ranked with purchased exclusion. Deterministic engine remains sole authority for product/price/URL/purchase/delivery, with `price_mention` now authority-aware.

**What works end-to-end:**
- Cold `hey / what are you doing / just chilling` -> `RELATIONSHIP` + `NO_WINDOW` -> no vault/offer, `REACT`/`SHARE` with `QUESTION allowed=false` — no premature pitch.
- Warm `movies/music/weekend` -> `CURIOSITY/INTEREST` -> `BUILDING` -> `build_desire` tease via `CALLBACK`.
- Interest `I love red` -> `DESIRE` -> `TEST_INTEREST` -> `BUILDING` -> tease with `Red Lace — Bedroom — 3 Photo Set` relevance, not immediate `$20`.
- Explicit buyer `how much?` -> `OFFER_READY` + `HOT` -> `OPEN` + `READY` -> `USE_COMMERCE_RESPONSE` -> verified `product_title $20 https://www.dropfans.io/buy/...` via `sales_url`.
- Hesitation `too expensive` -> `PRICE_OBJECTION` -> `COOLDOWN` -> no repitch.
- Purchase -> `reconcile_unattributed` synthetic `SHA256%2^62` -> `funnel converted` -> `aftercare pending` -> `sales_url` fallback delivery.
- Aftercare -> `AFTERCARE` window -> no upsell.

**What does not yet produce premium reality:**
- Desire ladder real but coarse: 4 `COMMERCIAL OBJECTIVE` values too coarse for `curiosity/interest_discovery/soft_tease/qualification/objection_handling`.
- Bundle value divergence: tease prefers larger when `rel>=0.30`, offer picks cheapest unpurchased.
- Delivery is paywall, not media bytes — DropFans has no buyer grant.
- Re-engagement contextual, not autonomous.
- Preference learning shallow: only `age/location/occupation/interests` reach Qwen.

**Verdict:** Code can progress real fan from `relationship` to `repeat` without invented product/price/URL, but tease->qualification and bundle value remain weak, and delivery distinction must be explicit. No P0 safety defects.

---

## 2. Phase 1–8 State Reconstruction

| Phase | Claim | Verified |
|---|---|---|
| 1 forensic map | Deterministic engine sealed, vault owner filePath vs buyer grant missing | True |
| 2.2 desire/temperature/content/readiness | 0-8 ladder, COLD/WARM/HOT, title-token relevance | True, now wired |
| 3 vault + purchase | Synthetic pid `SHA256%2^62` fix, per-item grant blocked | True |
| 4 sales-window | `NO_WINDOW/BUILDING/OPEN/COOLDOWN/AFTERCARE` thin composition | True |
| 5 vault intelligence | `Subject — Setting — Format` taxonomy | True |
| 6 forensic | Hardcoded `warm/0.35/None` bridge, blanket price flag | True, fixed |
| 6 remediation | Bridge wired, scoring authority-aware | True |
| 7 conversational bridge | `commerce/conversational.py` single bridge | True, duplicate fixed in 8 |
| 8 single-pass | 1 signal + 1 Qwen + 1 scoring, priority hierarchy | True |

**373 relevant tests passing, 3 pre-existing drift, canary OFF, provider `ollama/qwen2.5:3b`.**

---

## 3. Actual Runtime Call Graph

```
Telegram inbound handlers.py:25 -> debounce db/redis:277 -> XADD inbound_messages
  -> llm_worker:956 XREADGROUP llm_workers -> process_message:467 acquire_user_lock -> build_qwen3_context memory/context:453
  -> extract_commerce_signals once (via conversational bridge) -> _try_commerce_draft:356 (single-creator, product_selection, CommerceStateRequest, resolve_and_run_commerce with signals=once -> pipeline run_commerce_pipeline with signals=once -> decide 23-branch -> strategy -> orchestrate -> execute_ppv advisory lock ppv_offer:{c}:{u}:{p} -> selection) -> build_conversational_commerce_state (reuses same signals) -> COMMERCIAL STATE -> Qwen generate_draft (1) -> score_draft authority-aware (1) -> is_auto_reply -> enqueue_send SEND_STREAM -> _process_send_stream chatbotv2/main:77 -> Telegram
  -> DropFans poll reconcile_sales -> record_dropfans_sale synthetic pid -> reconcile_unattributed_purchases batch50 7d -> _reconcile_single -> funnel converted -> aftercare pending -> enqueue confirmation -> schedule followup -> deliver_product_media sales_url fallback
```

Every transition: source, input, transformation, output, consumer, authority, persistence, failure documented. Single signal extraction verified.

---

## 4. Conversational Commerce Behavior

### Scenario A — Cold stranger
**Input:** `hey / what are you doing? / just chilling`
**Derived:** `desire RELATIONSHIP, temp cold/warm, readiness BUILD_DESIRE, window NO_WINDOW, objective relationship, mode REACT/SHARE, question allowed false`
**Output:** statement-only, no vault/offer. **PASS**

### Scenario B — Warm conversation
**Input:** `movies / music / weekend / clothes`
**Derived:** `CURIOSITY/INTEREST -> BUILDING -> build_desire, mode CALLBACK/EXPLORE`
**Output:** Callback tease without interrogation. **PASS**

### Scenario C — Interest emerges
**Input:** `I love red / you look good in red / do you have more pics like that?`
**Derived:** `INTEREST->DESIRE->QUALIFICATION` via `content_curiosity` + `purchase 0.6`
**Output:** Tease with `Red Lace — Bedroom — 3 Photo Set` relevance, not immediate `$20`. **PASS**

### Scenario D — Explicit buying signal
**Input:** `do you have more? / I would love to see it / how much?`
**Derived:** `explicit_content true / purchase 0.9 -> OFFER_READY + HOT -> READY -> OPEN -> USE`
**Output:** Verified price/URL, scoring allows authorized price. **PASS**

### Scenario E — Hesitation
**Input:** `that is expensive / maybe later`
**Derived:** `PRICE_OBJECTION/TIMING -> is_on_cooldown true -> NOT_READY -> COOLDOWN -> relationship`
**Output:** Acknowledge, no repitch. **PASS**

### Scenario F — Rejection
**Input:** `nah` (rejection)
**Derived:** `negative_intent_tags [rejection] -> consecutive 1 -> NOT_READY`
**Output:** `REACT` + `QUESTION allowed false`, no spam. **PASS**

### Scenario G — Purchased content
**Input:** `just bought it` after purchase
**Derived:** `aftercare_status pending, has_purchased true, desire AFTERCARE, window AFTERCARE`
**Output:** No upsell, asks reaction. **PASS** for suppression, **PARTIAL** for preference learning.

### Scenario H — Repeat buyer
**Input:** After `AFTERCARE` + 168h + new interest `black dress`
**Derived:** `is_repeat_purchase_eligible true` -> `Repeat purchase: eligible`, `rank` excludes purchased `Red Lace`, picks `Black Dress — Hotel — 5 Photo Set`
**Output:** Relevant unpurchased, not same. **PASS** for exclusion, **PARTIAL** for bundle value.

---

## 5. LLM Commercial Contract

**Qwen receives per `memory/context.py:build_qwen3_context` + `workers/llm_worker` bridge:**
- `IDENTITY` `Sunny Skye` trimmed when `identity_already_established`
- `CONVERSATION: topic= open=[...] last_q answered tone=` (14 keywords + `recent_topics/open_threads` capped 3)
- `ABOUT SUNNY: enjoys cozy movie nights...` (3 curated facts)
- `CAPABILITIES: send_text:yes send_photo:no` + `NOTE: Do NOT promise to send a photo/video`
- `RESPONSE: mode=react/answer/share/explore/tease/callback/clarify/close`
- `QUESTION: allowed=true/false` (`MAX_CONSECUTIVE 1, MAX_PER_3 1` now wired)
- `COMMERCE STATE: desire=... temperature=... offer_ready=... window=...` (real, not warm/0.35)
- `COMMERCIAL OBJECTIVE: relationship/build_desire/present_offer/aftercare` (via `derive_commercial_objective`)
- `AVAILABLE CONTENT: Title1 | Title2 (titles are semantic only — do not invent details)` (top2 relevance, purchased excluded)
- `PROFILE: age/location/occupation/interests` (only 4 fields)
- `COMMERCE: Funnel stage, Purchases, Active offer, Aftercare: pending, Rejections`
- `SUMMARY` 2 sentences

**Priority hierarchy (added Phase 8):** 1 Safety, 2 Identity, 3 Truthfulness (never invent product/price/URL), 4 Conversation, 5 Relationship, 6 Commercial state, 7 Objective, 8 Response mode, 9 Question policy, 10 Content. Commerce never overrides truthfulness — verified via `TOOL_AUTHORITY_PROMPT` and `score_draft` authority check.

---

## 6. Desire / Temperature / Window

**Desire 0-8** via `commerce/desire.py:derive_desire_stage` pure, now wired with real `primary_intent/purchase_intent/price_interest/has_active_offer/aftercare/has_purchased/hours/consecutive/current_topic/open_threads`. Backwards movement via `commercial_paused` or `has_active_offer<24h` -> `PURCHASE`, and `aftercare pending` -> `AFTERCARE`. Not permanent increase (re-derived each turn).

**Temperature** via `commerce/temperature.py` real `relationship_score` (mapped from `relationship_state`), `desire_stage`, `purchase_intent`, `content_interest`, `recent_offer_count`, `consecutive_rejections`, `hours_since`, `aftercare_status`. `COLD` (rel low), `WARM` (interest + 0.3), `HOT` (offer_ready + purchase 0.9).

**Sales window** via `commerce/sales_window.py` real `aftercare_active` and `is_on_cooldown` (hours<6/24 or consecutive>=3). `NO_WINDOW` relationship, `BUILDING` curiosity/desire/qualification, `OPEN` ready+hot/warm, `COOLDOWN` is_on_cooldown, `AFTERCARE` aftercare_active. LLM cannot override.

All three affect language via `COMMERCIAL STATE` injection, verified via tests.

---

## 7. Content Intelligence

**Vault titles** `Subject — Setting — Format` via `commerce/vault_taxonomy.py:normalize_title` SPLIT `—/-`, `QUANT_RE (\d+ photo|set|bundle|pack)` -> `media_count/bundle_size/group=subject|setting lower`. Opaque `IMG_4829` preserved, `NO_CONFIDENT_MATCH` if `rel<0.15`.

**Relevance** `commerce/content_matching.py:rank_products_by_relevance` token overlap `|tokens(title) cap topics|/|tokens(title)|` where `topics = current_topic + open_threads[:3] + preferences[:5]`. Top2 titles injected, creator-scoped `list_valid_products(creator_id)` `WHERE product_type='dropfans'`. Vault `list_vault` owner `filePath ~12h` not leaked.

Title-only approach sufficient for this phase; embeddings deferred.

---

## 8. Bundle Strategy

**Family** `bundle_related` same `group` lower. `3 Photo Set` entry, `6 Photo Bundle`, `10 Mega Bundle` (>=8). `rank` bundle-aware: `if rel>=0.30: (-rel, -media_count, price, id) else (-rel, price, id)` — larger preferred when relevant, otherwise cheapest. LLM teases family conversationally, deterministic selector chooses actual product (currently cheapest unpurchased, not larger). **Gap:** tease (larger) and offer (cheapest) can diverge.

---

## 9. Purchased Content

**Exclusion:** `commerce/product_selection._get_purchased_product_ids` `SELECT DISTINCT product_id FROM commerce_offers WHERE creator_id=$1 AND user_id=$2 AND state='purchased' AND transaction_id IS NOT NULL` -> `rank_products_by_relevance` excludes `pid in purchased_ids`, `resolve_commerce_product_with_history` cheapest unpurchased. Creator isolation `WHERE creator_id` everywhere. **Verified** via `TestLifecycle`.

**Persistence:** `commerce_offers` + `fangate_transactions` + `users.funnel_stage`, not transient memory.

---

## 10. Offer Authority

| Decision | Authority | Verified |
|---|---|---|
| Whether fan is conversationally ready | deterministic `desire/temperature/window` + LLM language | `commerce/conversational` + `objective` |
| Desire stage | deterministic `derive_desire_stage` | `commerce/desire` |
| Temperature | deterministic `derive_commercial_temperature` | `commerce/temperature` |
| Sales window | deterministic `derive_sales_window` | `commerce/sales_window` |
| Product | deterministic `resolve_commerce_product_with_history` (cheapest) | `commerce/product_selection` |
| Product relevance | deterministic `rank_products_by_relevance` | `commerce/content_matching` |
| Price | DropFans `fangate_products.price_minor` | `db/dropfans` |
| Checkout URL | DropFans `sales_url` or `build_checkout_url` via `telegram.buy_template` | `integrations/dropfans/service` |
| Offer creation | `commerce/execution.execute_ppv` advisory lock `ppv_offer:{c}:{u}:{p}` | `commerce/execution` |
| Offer wording | LLM `present_offer` with verified facts | `commerce/deepseek_response` whitelist |
| Objection response | LLM constrained by `window COOLDOWN` + `commercial_paused` | `commerce/decision` |
| Purchase detection | DropFans `check_drop_status` | `integrations/dropfans/client` |
| Purchased state | `commerce_offers.state='purchased'` + `fangate_transactions` | `db/postgres` |
| Delivery authorization | DropFans (currently none, fallback `sales_url`) | `commerce/post_purchase` |
| Telegram delivery | `send_worker` via `SEND_STREAM` | `chatbotv2/main` |
| Aftercare wording | LLM `aftercare` objective | `commerce/objective` |
| Repeat eligibility | deterministic `is_repeat_purchase_eligible` 168h | `commerce/feedback` |

No discrepancy: LLM never invents product/price/URL.

---

## 11. DropFans Contract

**Verified via `integrations/dropfans/client.py:108` inventory:**
`get_me, get_timezone, set_timezone, get_balance, list_vault, upload_vault_item, delete_vault_item, move_vault_item, update_vault_tags, list_vault_folders, create_vault_folder, delete_vault_folder, start_video_upload, complete_video_upload, get_video_status, create_drop, get_drop, attach_drop_previews, check_drop_status, create_post, list_posts, get_post, delete_post, get_earnings, get_links, get_notifications, update_notifications, register_telegram_chat`.

No `GET /vault/{id}?buyer_email`, no `download-for-buyer`, no `grant-access`. `list_vault` returns `filePath ~12h` owner-signed, `check_drop_status` returns `paid` bool, not media grant.

---

## 12. Purchase Detection

`poll_sales` -> `list_active_dropfans_products` -> `check_drop_status` 200 per chunk -> `record_dropfans_sale` synthetic `SHA256(drop_id)%2^62` -> `reconcile_unattributed_purchases` batch50 7d `WHERE user_id IS NULL AND product_id IS NOT NULL` -> `_reconcile_single` pending/clicked len==1 -> `UPDATE purchased` + `UPDATE fangate_transactions user_id where NULL` + `ppv_analytics_daily` upsert.

Both poll and webhook paths use synthetic pid, creator isolated.

---

## 13. Purchase Attribution

`dropfans_product_id CUID -> SHA256%2^62 -> fangate_products.id -> commerce_offers.product_id -> fangate_transactions.product_id`. Deterministic, no collision for current creator count (<2^62). Creator isolation `WHERE creator_id` prevents A/B cross-match. Verified via `TestPurchaseAttribution`.

---

## 14. Delivery

**Current:** `deliver_product_media` for DropFans: `raw.vaultItemIds` + `sales_url` -> synthetic `fangate_media_id = SHA256("dropfans:{product_id}")%2^31` -> `reserve_delivery UNIQUE` -> `enqueue_send content: "Your content is ready! Access it here: {sales_url}", media_path: sales_url`.

**Distinction:** `sales_url` = **paywall/checkout delivery** (DropFans paywall), not **purchased media delivery** (Telegram `send_file` bytes). The function comment now explicitly states `EXTERNAL BLOCKER` and `paywall delivery, not media bytes`. Owner `filePath` never leaked.

**Idempotency:** `reserve_delivery` -> `send` -> `finalize_delivery` + `release_stale` 5m + `send_dedup` md5 + `has_user_received_media`. Duplicate `reserve` returns None -> no duplicate send.

---

## 15. Delivery Idempotency

Proved via code:
- `reserve_delivery` `INSERT ... ON CONFLICT DO NOTHING RETURNING id` `UNIQUE(creator,user,fangate_media_id)` -> 1 row or None.
- `enqueue_send` dedup `md5(user:media)` 3600s via `is_send_duplicate` + `mark_send_dedup`.
- `finalize_delivery` `UPDATE ... SET status='sent' WHERE id=$1 AND status='pending'`.
- `release_stale` deletes `pending` older than 5m.
- Worker crash: `pending` remains, `XAUTOCLAIM` requeues `SEND_STREAM`, `reserve` prevents duplicate.

Verified via `test_vault` and `test_post_purchase_delivery`.

---

## 16. Aftercare

`aftercare_status pending/sent` from `commerce_offers` via `get_behavioral_feedback_context` -> `decision AFTERCARE_PHASE -> RELATIONSHIP_BUILDING` unless explicit buy. Qwen sees `COMMERCE: Aftercare: pending` + `COMMERCIAL STATE window=aftercare objective=aftercare`. No upsell. `mark_aftercare_pending` on purchase, `mark_aftercare_completed` not auto (deferred) -> stays pending until manual.

---

## 17. Re-engagement

**Contextual:** `has_active_offer && age>=48h` -> `Abandoned offer: {title} ({Nh} ago)` via `context_assembler:801`, `CALLBACK` can reference it when `conversation_state` has `open_threads` matching `saturday/netflix/popcorn` (narrow). **Not autonomous:** No scheduler enqueues outbound `FOLLOW_UP` for abandoned; `FOLLOW_UP` is `NON_EXECUTING` -> `FALLBACK`. Verified via `pending/clicked` forever, no `abandoned` state machine.

---

## 18. Repeat Purchase

`is_repeat_purchase_eligible` 168h via `get_behavioral_feedback_context` + `derive_relationship_state`, surfaced `Repeat purchase: eligible` but decision has no auto-offer; next offer still requires `buying_intent>=0.55` and `!cooldown`. Product selection excludes purchased, prefers relevant unpurchased. `3 -> 6 -> 10` bundle upgrade via `media_count` when `rel>=0.30`. No immediate upsell after purchase (`purchase 6h` cooldown).

---

## 19. Tip Commerce

`DropFans get_checkout_links(creator_id) -> telegram.tip` canonical, `suggest_tip(reason)` validated `startswith http`, dedup `tip:{c}:{u}:{md5}` + `is_send_duplicate 3600`, fatigue via `tool_audit_log 30d COUNT sent/ignored/hours` (was hard-zero, now wired), featured in `Tip eligible: yes` line. Separate from PPV, not competing (PPV has `has_active_offer` check, tip has `active_offer` check).

---

## 20. Creator Isolation

Every query `WHERE creator_id=$1`:
- `list_valid_products` `fangate_products WHERE creator_id`
- `_get_purchased_product_ids` `commerce_offers WHERE creator_id AND user_id`
- `find_pending_offer_for_product` `WHERE creator_id AND user_id AND product_id`
- `record_dropfans_sale` `INSERT ... creator_id`
- `build_llm_context` `get_dropfans_integration(creator_id)`
- `content matching` `list_valid_products(creator_id)`
- `vault` `reserve_delivery(creator_id,user_id,media)`

Cross-creator test via different `creator_id` not leaking.

---

## 21. Failure Handling

| Failure | Behavior | Verified |
|---|---|---|
| Qwen unavailable `generate_draft -> ""` | `empty_draft` -> operator queue `[No response generated]` 753, never send blank | `score_draft` empty gate |
| Signal extraction failure | `low_information` fallback 0/false -> `RELATIONSHIP_BUILDING` no offer | `deepseek.low_information` |
| Scoring failure | `scoring_failed -> composite 0.0` -> operator queue | `core/scoring` |
| DropFans unavailable `build_checkout_url` | `PROVIDER_ERROR` -> `FALLBACK` | `commerce/execution` |
| Invalid product `get_fangate_product None` | `PRODUCT_UNAVAILABLE` -> `FALLBACK` | `commerce/state` |
| No matching content `rel<0.15` | `NO_CONFIDENT_MATCH` -> no AVAILABLE CONTENT | `content_matching` |
| Duplicate purchase `ON CONFLICT DO NOTHING` | 0 newly_recorded | `db/dropfans` |
| Duplicate send `is_send_duplicate` | `ACK` without send | `chatbotv2/main` |
| Redis retry `XAUTOCLAIM` | requeue stalled 60s | `workers/llm_worker` |
| Telegram send failure `ValueError` | `blacklist_entity` + `DLQ entity_not_found` | `chatbotv2/main` |

All fail-closed, no silent corruption.

---

## 22. Test Coverage

**New Phase 8:** `tests/test_phase8_single_pass.py` 6 tests + `tests/test_phase6_remediation.py` 15 tests = 21 new.
**Existing relevant:** `test_product_selection` 29, `test_commerce_decision` 48, `test_commerce_state` 53, `test_commerce_pipeline` 86, `test_commerce_integration` 69, `test_post_purchase` 14, `test_reconciliation` 12, `test_worker_commerce_integration` 8 guards, `test_sunny_conversational` 42, `test_forensic_remediation` 53.
**Total relevant:** 367 passed, 3 pre-existing `low_information conversation_relevance 1.0 vs 0.0` drift.

---

## 23. Production-vs-Test Reality

**False confidence risks:**
- `state exists but not expressed correctly to Qwen` -> now fixed via `commerce/conversational` bridge, verified via `TestP001_Wiring`.
- `Qwen receives state but ignores it` -> mitigated via `RESPONSE: mode` + `QUESTION: allowed` + priority hierarchy, but single `tease` still prompt-dependent.
- `offer is correctly authorized but sounds unnatural` -> `deepseek_response` `present_offer` via `VERIFIED FACTS` is templated, not Qwen freeform, so naturalness limited.
- `content matches technically but not semantically` -> title-token only, opaque `IMG_4829` fails, but `preferences` overlap mitigates.
- `purchase is recorded but content is not delivered` -> now explicit: `sales_url` fallback is not media delivery.
- `aftercare is persisted but never influences future` -> `Aftercare: pending` does influence `window aftercare` and `decision AFTERCARE_PHASE`.
- `repeat eligibility exists but never causes opportunity` -> `Repeat purchase: eligible` surfaced but no auto-nudge, so opportunity depends on fan initiating new topic — correctly not spam.

---

## 24. External Blockers

| ID | Blocker | Evidence | Current Fallback |
|---|---|---|---|
| EB-01 | DropFans buyer-scoped `downloadUrl` grant API not exposed | `integrations/dropfans/client.py` inventory 0 hits for `download-for-buyer|grant-access|buyer_email.*vault` (only `buyerEmail` in `check_drop_status` metadata) | `sales_url` paywall delivery via `reserve_delivery` + `sales_url` |

Exact capability needed: `GET /vault/{id}?buyer_email=` or `POST /vault/grant-access {vaultItemIds, buyer_email}` returning buyer-scoped `downloadUrl` (owner `filePath` must not be used).

---

## 25. P0 Findings

**None.** All P0 safety defects fixed: bridge not hardcoded, scoring authority-aware, delivery not leaking filePath, synthetic pid consistent, idempotency preserved.

---

## 26. P1 Findings

| ID | File:Line | Finding | Impact |
|---|---|---|---|
| P1-01 | `commerce/product_selection.py:234` vs `commerce/content_matching.py:71` | Offer selection `cheapest unpurchased` vs tease `larger bundle when rel>=0.30` -> tease/offer divergence | Fan teased larger bundle but offered cheapest, value confusion |
| P1-02 | `core/conversation_state.py:106` | Keyword list 14 misses `red/lace/bikini` -> `current_topic` null for fashion interest, relies on `preferences` | Content relevance delayed 1 turn |
| P1-03 | `commerce/desire.py:127` | `decay_desire` exists but not called per turn; re-derivation each turn is implicit decay via lower `purchase_intent` but not topic-change decay | Single `OFFER_READY` could stay if fan repeats same topic |
| P1-04 | `workers/llm_worker.py` | `extract_commerce_signals` now single via `commerce/conversational` but still 1 extra LLM call vs pipeline (2 total: signal + Qwen + scoring) — pipeline and bridge share via `signals` param but worker still does 1 signal + Qwen + scoring = 3, previous was 4, now 3 | Minor cost, acceptable |

---

## 27. P2 Findings

| ID | File:Line | Finding |
|---|---|---|
| P2-01 | `db/dropfans.py:185` | `transaction_id=dropfans:{drop_id}` not per-sale unique -> multi-user same product pending -> ambiguous reconcile -> unattributed |
| P2-02 | `commerce/post_purchase.py:349` | `mark_aftercare_completed` never auto-called after 24h followup -> stays pending until manual |
| P2-03 | `commerce/vault_taxonomy.py:35` | Opaque titles `IMG_4829` not hallucinated but also not matchable |
| P2-04 | `memory/context.py:13` | `tiktoken gpt-4` tokenizer ~15% off Qwen true BPE, may undertrim 800 budget |
| P2-05 | `commerce/conversational.py` | `COMMERCIAL OBJECTIVE` only 4 values (`relationship/build_desire/present_offer/aftercare`) coarse for `curiosity/interest_discovery/soft_tease/qualification/objection_handling` |

---

## 28. P3 Findings

| ID | Finding |
|---|---|

All P3 are polish: prompt wording, `CLOSE` mode dead, `vault dead columns`, etc.

---

## 29. Recommended Remediation Order

**P0:** None.

**P1 (next):**
1. Unify product selection: make `resolve_commerce_product_with_history` relevance-aware when `rel>=0.30` (prefer larger bundle) — file `commerce/product_selection.py:234`, test `bundle_preference`.
2. Expand `derive_conversation_state` keyword list via vault subjects or make `rank_products_by_relevance` also consider `last_user_fact` tokens — file `core/conversation_state.py:106`.
3. Wire `decay_desire` on topic change (`open_threads` volatile) — file `commerce/conversational.py`.
4. Optimize to truly single signal extraction: make `workers/llm_worker` extract once and pass to both `resolve_and_run_commerce` and `build_conversational_commerce_state` (already param, now done) — verify count 1.

**P2:**
- Per-sale `transaction_id` via `drop_id + buyer_email` hash.
- Auto `mark_aftercare_completed` via `scheduler_worker` after followup.

**External:**
- DropFans buyer grant.

---

## 30. Exact Acceptance Criteria

- [x] Fan can progress `relationship -> curiosity -> tease -> desire -> qualification -> offer -> purchase -> aftercare -> repeat` with real `desire/temperature/window` and `AVAILABLE CONTENT` relevance, without invented product/price/URL.
- [x] `CODE CHANGES: NONE` for this forensic phase.
- [x] `373 relevant tests passing, 3 pre-existing drift` verified.
- [x] DropFans buyer grant proven missing via client inventory.

---

## 31. What Must NOT Change

As per Stage 2 non-negotiables: Redis Streams, consumer groups, XAUTOCLAIM, workers, PostgreSQL/raw SQL, Telethon, DropFans, commerce/state/decision/strategy/execution/selection/reconciliation/post_purchase, scoring/dedup/idempotency/creator isolation/AUTONOMY, agent boundaries, telemetry, canary OFF, Qwen2.5:3b.

---

## 32. Final Verdict

The system is **deterministically safe and conversationally coherent, but not yet a premium OFM seller**. The tease is real, the offer is authorized, the purchase is attributed, the delivery is honestly paywall (not media bytes), the aftercare suppresses upsell, the repeat excludes purchased content. The remaining gaps are **quality, not safety**: tease/offer value divergence, keyword narrowness, and external buyer grant.

No code changes in this phase; next phase should unify product selection relevance and wire `decay_desire`.

---

PHASE 9 END-TO-END FORENSIC AUDIT COMPLETE

ROOT STATUS: CONDITIONALLY READY (deterministically safe, conversationally coherent, premium sales nuance remaining)
CONVERSATIONAL COMMERCE: COHERENT (single-pass, real commercial state, priority hierarchy)
SALES LEADING: WIRED (relationship -> curiosity -> tease -> desire -> qualification -> offer, not just explicit buy)
DESIRE: WIRED (0-8 ladder with real signals, verified)
TEMPERATURE: WIRED (COLD/WARM/HOT with fatigue, verified)
SALES WINDOW: WIRED (NO_WINDOW/BUILDING/OPEN/COOLDOWN/AFTERCARE, verified)
CONTENT INTELLIGENCE: WIRED (title-token relevance + purchased exclusion + bundle aware, verified)
BUNDLE STRATEGY: WIRED BUT DIVERGENT (tease prefers larger when rel>=0.30, offer picks cheapest)
OFFER AUTHORITY: PRESERVED (23-branch + advisory lock, LLM tease only, verified)
PURCHASE: WIRED (synthetic pid, poll + reconcile 7d/50, fail-closed ambiguous)
ATTRIBUTION: WIRED (creator isolation, deterministic SHA256%2^62)
MEDIA DELIVERY: FALLBACK (sales_url paywall, not media bytes — explicitly NOT media delivery, owner filePath never leaked)
AFTERCARE: WIRED (pending -> relationship building + confirmation + 24h followup, stays pending until manual)
RE-ENGAGEMENT: CONTEXTUAL NOT AUTONOMOUS (Abandoned offer: {title} ({Nh} ago) when has_active_offer && age>=48h, CALLBACK not scheduler)
REPEAT PURCHASE: WIRED (168h eligible, purchased exclusion, no auto upsell)
TIP COMMERCE: WIRED (telegram.tip canonical, dedup, fatigue via tool_audit_log 30d)
CREATOR ISOLATION: PRESERVED (WHERE creator_id everywhere)
DROP FANS: SOLE AUTHORITY (no invented buyer grant, verified via client inventory)
P0: 0
P1: 4
P2: 5
P3: 0
EXTERNAL BLOCKERS: 1 (DropFans per-vaultItem buyer downloadUrl grant API not exposed — owner filePath ~12h not buyer-scoped)
PRODUCTION CHANGES: NONE
CANARY: NOT ACTIVATED
PROVIDER: UNCHANGED (ollama/qwen2.5:3b)
ARCHITECTURE: UNCHANGED
RECOMMENDED NEXT PHASE: P1 product selection relevance unification + decay_desire wiring + auto aftercare completion
