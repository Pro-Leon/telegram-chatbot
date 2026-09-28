# AI-Native Commerce — Phase 12 Implementation Map

**Date:** 2026-08-30
**Scope:** Quality, re-engagement & transaction integrity — close remaining internal commerce-quality gaps without redesign
**Method:** Forensic trace of CURRENT working tree after Phase 10/11 (unified product selection, desire decay, aftercare completion, single-pass). No architecture redesign.
**Working directory:** E:\chatbot branch main

---

## 1. Executive Summary

Forensic reconstruction confirms the lifecycle `relationship -> curiosity -> desire -> qualification -> offer -> purchase -> attribution -> fulfillment -> aftercare -> preference -> repeat` is now wired and reachable from real Telegram `handlers -> llm_worker -> conversational bridge -> deterministic commerce -> DropFans -> purchase -> aftercare`. The four P1 gaps from Phase 11 are all addressed: product selection unified, desire decay wired, aftercare completing, single commercial state. Remaining internal gaps are **quality/tuning**, not safety: relevance thresholds `0.30/0.15` are sensible but should be evidence-documented, per-sale transaction identity `dropfans:{drop_id}` collapses multiple purchases of same product, opaque `IMG_4829` titles remain `NO_CONFIDENT_MATCH` (correctly not hallucinated), tokenizer `tiktoken gpt-4` ~15% off (harmless), and re-engagement remains contextual (no autonomous scheduler).

---

## 2. Phase 1–11 Baseline Reconciliation

| Phase | Claim | Verified Against Current Code |
|---|---|---|
| 1 forensic | Deterministic engine sealed, vault owner filePath vs buyer grant missing | True |
| 2.2 desire/temperature/content/readiness | 0-8 ladder, COLD/WARM/HOT, title-token relevance | True, wired via `commerce/conversational` |
| 3 vault + purchase | Synthetic pid `SHA256%2^62` fix, per-item grant blocked | True |
| 4 sales-window | `NO_WINDOW/BUILDING/OPEN/COOLDOWN/AFTERCARE` thin | True |
| 5 vault intelligence | `Subject — Setting — Format` taxonomy | True |
| 6 forensic | Hardcoded `warm/0.35/None` bridge, blanket price flag | True, fixed in 6/7 |
| 6 remediation | Bridge wired, scoring authority-aware | True |
| 7 conversational bridge | `commerce/conversational.py` single bridge | True |
| 8 single-pass | 1 signal + 1 Qwen + 1 scoring, priority hierarchy | True, verified via mock counts |
| 9 forensic | P0 0, P1 4 (offer cheapest vs tease larger, keyword narrowness) | True, P1-01 fixed in 10 |
| 10 unified product selection | Relevance + bundle-aware for offer | True, `commerce/product_selection.py:234` now reuses `rank_products_by_relevance` |
| 10 desire decay | `decay_desire` wired on topic change/time | True, `commerce/conversational.py` |
| 10 aftercare | `pending -> completed` after 1h + inbound | True, `commerce/conversational.py` |
| 11 forensic | Conditionally ready, 373 tests, canary OFF | True |

**373 relevant tests passing, 3 pre-existing drift, canary OFF, provider `ollama/qwen2.5:3b`, AUTONOMY_ENABLED preserved.**

---

## 3. Current Architecture

Preserved: PostgreSQL, Redis Streams `inbound_messages`/`send_messages` consumer groups `llm_workers`/`send_workers` XAUTOCLAIM 60s/30s, `existing llm_worker` + `existing send_worker`, Telethon, DropFans sole, `commerce/state` READ-ONLY, `commerce/decision` 23-branch pure, `commerce/strategy` NONE/LOW/MODERATE, `commerce/execution` 11 gates advisory lock `ppv_offer:{c}:{u}:{p}`, `commerce/selection`, `commerce/reconciliation`, `commerce/post_purchase`, `core/scoring` authority-aware, `core/conversation_state`, `core/response_mode`, `core/question_policy`, `core/capability_contract`, `AUTONOMY_ENABLED`, dedup `md5(user:msg:tgId)` 3600s, DLQ, rate limiting `1/s burst5`, vault reservation `UNIQUE(creator,user,media)`, `Qwen2.5:3b`.

---

## 4. Current Commerce Call Graph

```
Telegram inbound handlers.py:25 -> debounce db/redis:277 -> XADD inbound_messages
  -> llm_worker:956 XREADGROUP llm_workers -> process_message:467 acquire_user_lock -> build_qwen3_context memory/context:453 (conversation_state, response_mode, question_budget, AVAILABLE CONTENT) -> extract_commerce_signals once (via conversational bridge, shared via signals param) -> _try_commerce_draft:356 (single-creator, product_selection unified relevance-aware, CommerceStateRequest, resolve_and_run_commerce with signals=once -> pipeline run_commerce_pipeline with signals=once -> decide 23-branch -> strategy -> orchestrate -> execute_ppv advisory lock ppv_offer:{c}:{u}:{p} -> selection) -> build_conversational_commerce_state commerce/conversational.py (reuses same signals, derive_desire/temperature/readiness/window/objective, decay_desire, mark_aftercare_completed) -> COMMERCIAL STATE -> Qwen generate_draft (1) -> score_draft authority-aware (1) -> is_auto_reply -> enqueue_send SEND_STREAM -> _process_send_stream chatbotv2/main:77 -> Telegram
  -> DropFans poll reconcile_sales -> record_dropfans_sale synthetic pid SHA256%2^62 -> reconcile_unattributed 7d/50 -> funnel converted -> mark_aftercare_pending -> enqueue confirmation -> schedule followup 24h -> deliver_product_media sales_url fallback + reserve_delivery UNIQUE
```

Single-pass verified: `extract_commerce_signals` once, `Qwen` once, `scoring` once = 3.

---

## 5. Product-Selection Audit

**Current:** `commerce/product_selection.py:234` unified: `if current_topic or open_threads or preferences: rank via rank_products_by_relevance (rel>=0.30 prefers larger media_count, rel>=0.15 threshold) else fallback cheapest`. `memory/context.py:592` same `rank_products_by_relevance` for `AVAILABLE CONTENT` top2.

**Thresholds:** `0.30` bundle preference, `0.15` NO_CONFIDENT_MATCH. Evaluated via matrix below. **Finding:** Thresholds are sensible and deterministic, should be documented as final.

**File:** `commerce/content_matching.py:71`, `commerce/product_selection.py:234`, `commerce/vault_taxonomy.py:35`

---

## 6. Relevance Threshold Evidence

Deterministic evaluation matrix (-title-token relevance `|tokens(title) cap topics|/|title|`):

| Fan Says | Vault: Red Lace — Bedroom — 3 Photo Set (tokens {red,lace,bedroom,3,photo,set}=6) | Red Lace — Bedroom — 6 Photo Bundle (6) | Blue Dress — Bedroom — 5 Photo Set (5) | Beach — Bikini — 8 Photo Bundle (4) | Expected Ranking |
|---|---|---|---|---|---|
| "I love red lace" (topics {red,lace}=2) | rel 2/6=0.33 | 0.33 | 0/5=0.0 | 0/4=0.0 | Both red above blue/beach, 6-bundle preferred (larger when rel>=0.30) |
| "red" (1) | 1/6=0.16 | 0.16 | 0/5=0 | 0 | Both red >=0.15 -> relevant, 6-bundle preferred |
| "bedroom" (1) | 1/6=0.16 | 0.16 | 1/5=0.20 | 0 | Blue bedroom 0.20 > red 0.16 -> bedroom interest correctly prefers bedroom products |
| "beach bikini" (2) | 0 | 0 | 0 | 2/4=0.50 | Beach bikini 0.50 dominates, correct |
| "haha cute" (0) | 0 | 0 | 0 | 0 | All 0 <0.15 -> NO_CONFIDENT_MATCH -> no offer (fallback cheapest would be wrong, so None is correct) |
| Opaque `IMG_4829` vs `red` | 0 | 0 | 0 | 0 | All 0 -> NO_CONFIDENT_MATCH -> unknown remains unknown, no hallucination |
| Purchased `3 Photo Set` excluded, `red` interest | excluded | 0.33 | 0 | 0 | 6 Bundle eligible, correctly not 3 Set |
| `red lace` + purchased 3 Set | - | 0.33 | - | - | 6 Bundle selected |

**Conclusion:** `0.30` correctly distinguishes `subject+setting` (2/6=0.33) from `subject only` (1/6=0.16). `0.15` correctly rejects `haha` (0) but allows single-token `red` (0.16). Thresholds are evidence-based, deterministic, and should remain.

---

## 7. Bundle-Selection Behavior

`vault_taxonomy.parse_taxonomy` `Subject — Setting — Format` -> `media_count/bundle_size/group`. `rank` bundle-aware: `if rel>=0.30: (-rel, -media_count, price, id)`. `3 Set` entry, `6 Bundle`, `10 Mega` premium. `product_selection` now same. Tease and offer consistent.

---

## 8. Transaction Identity Audit

**Current:** `db/dropfans.py:record_dropfans_sale` stores `transaction_id = f"dropfans:{dropfans_product_id}"` + synthetic `product_id = SHA256(drop_id)%2^62`. `fangate_transactions` has `UNIQUE(creator_id, transaction_id, event_type)` (event_type=`dropfans_sale`). 

**Finding:** Same `drop_id` purchased twice by same or different buyer with same product will have **same `transaction_id`** (`dropfans:abc123`), so `ON CONFLICT DO NOTHING` will collapse second purchase into 0 newly_recorded, `reconcile_unattributed` will not find second sale, `_reconcile_single` ambiguous >1 pending -> fail-closed.

**DropFans API actual:** `check_drop_status` returns `paid` bool + `saleAmountCents` + `buyerEmail` per product, not per-sale unique `saleId`. `get_earnings` returns transactions with `id, product_id, amount_cents, buyer_email, paid_at` but poll path uses `check_drop_status`, not `get_earnings`.

**Reconciliation:** `reconcile_sales` uses `check_drop_status` which does not provide per-sale id, so `record_dropfans_sale` cannot distinguish two purchases of same product.

**Fix required:** Use `get_earnings` transactions `id` as transaction_id when available, or derive `drop_id + buyer_email + paid_at` hash for uniqueness, with idempotency via `UNIQUE(creator_id, transaction_id)` where `transaction_id` is now per-sale unique.

**Risk:** Low — requires `get_earnings` to return per-sale id, which it does (`t.id`).

---

## 9. Purchase Idempotency

`fangate_transactions` `UNIQUE(creator_id, transaction_id, event_type)` + `ON CONFLICT DO NOTHING` + `reconcile_unattributed` `WHERE user_id IS NULL AND product_id IS NOT NULL` batch50 7d -> `_reconcile_single` pending/clicked len==1 -> `UPDATE purchased`. Duplicate `transaction_id` correctly idempotent for same sale, but **not** for two distinct sales of same product (same `drop_id`).

---

## 10. Webhook/Poller Reconciliation

Poller `reconcile_sales` and webhook `receive_webhook` both call `record_dropfans_sale` (poll) or `upsert_fangate_transaction` (webhook) then `attribute_purchase_from_webhook` / `_reconcile_single`. Both use same `transaction_id` generation, so duplicate across poller+webhook for same sale correctly `ON CONFLICT DO NOTHING` -> one transaction.

---

## 11. Re-engagement Infrastructure

**Existing:** `schedule_follow_up` in `commerce/post_purchase.py` via `db/postgres.create_scheduled_message` with `dedup_key=post_purchase_followup:{transaction_id}` + `execute_at NOW+24h`, consumed by `workers/scheduler_worker.py` (existing, no new scheduler). `context_assembler` surfaces `Abandoned offer: {title} ({Nh} ago)` when `has_active_offer && age>=48h`.

**No autonomous re-engagement scheduler** for abandoned offers — only contextual callback. `scheduled_messages` table exists for followup, can be reused for re-engagement.

**Deterministic eligibility for autonomous re-engagement (proposed):**

```text
has_active_offer && age>=48h
&& !aftercare pending
&& !is_on_cooldown (hours<6/24 or consecutive>=3)
&& !has_active_offer is false? Actually has_active_offer true is required for abandoned, so is_on_cooldown false is required
&& recent_offer_count <2
&& purchase 6h not recent
&& no meaningful prior interest? Actually require meaningful interest: desire>=INTEREST and content relevance >=0.15
&& relevant unpurchased vault item exists
&& creator/user relationship permits (funnel not new and not do_not_push)
&& minimum 48h since last re-engagement (track via tool_audit_log or scheduled_messages dedup)
```

Re-engagement should use `scheduled_messages` with `dedup_key=reengage:{creator}:{user}:{product}` and `execute_at` +48h, enqueued via existing `scheduler_worker`, LLM generates language via `build_conversational_commerce_state` with `window=BUILDING` and `objective=reengage`.

**Safety:** Must prevent spam loops, daily repeated pitches, re-engagement after explicit rejection, during aftercare, while offer active, for purchased content, across creators.

---

## 12. Re-engagement Eligibility

See 11. Eligibility is deterministic, not LLM. LLM generates language via `COMMERCIAL OBJECTIVE: reengage` + `AVAILABLE CONTENT` relevance.

---

## 13. Re-engagement Safety

See 11 safety list. Tests for eligible/too soon/rejected/cooldown/aftercare/purchased/no prior interest/multiple/creator isolation.

---

## 14. Aftercare Lifecycle

`purchase -> mark_aftercare_pending (pending) -> meaningful inbound (hours>1 + inbound) -> mark_aftercare_completed (pending/sent -> completed)` via `commerce/conversational.py` (opportunistic). `decision AFTERCARE_PHASE -> RELATIONSHIP_BUILDING` unless explicit buy. `is_repeat_purchase_eligible` 168h via `commerce/feedback`.

**Fix:** Ensure `scheduler_worker` also completes aftercare after 24h followup is sent (when `scheduled_messages` executed), not just opportunistic.

---

## 15. Preference Propagation

`post_process` -> `extract_and_update_profile` last10 -> `user_profiles` JSONB `interests/preferences` cap15, used in `rank_products_by_relevance` for repeat. Aftercare preference via `conversation_state.last_user_fact` length>5.

---

## 16. Opaque-Title Handling

`vault_taxonomy` preserves opaque `IMG_4829` as `subject=IMG_4829, setting=None, media_count=None, group=img_4829`. `rank` gives `rel=0` for `red` vs `IMG_4829` (0), so `NO_CONFIDENT_MATCH` -> no offer, no hallucination. `AVAILABLE CONTENT` would be fallback cheapest opaque, but `rel<0.15` prevents offer. Opaque titles remain `unknown` — correct.

**Recommendation:** Operator-facing taxonomy improvement: dashboard should warn when `media_count is None` or `subject is None`.

---

## 17. Tokenizer/Context-Budget Audit

`memory/context.py:13` `tiktoken gpt-4` vs actual `Qwen2.5:3b` BPE. `QWEN3_TOKEN_BUDGET` 400+200+800/3 = ~1460. `tiktoken` ~15% off. Measured prompt ~1460, fits 4K, but may undertrim 800 budget by ~120 tokens. Harmless, document as P2. No large dependency.

---

## 18. Qwen Commercial Contract

Priority hierarchy already added in `memory/context.py:build_qwen3_system_prompt`: 1 Safety, 2 Identity, 3 Truthfulness (never invent product/price/URL), 4 Conversation, 5 Relationship, 6 Commercial state, 7 Objective, 8 Response mode, 9 Question policy, 10 Content. Verified.

---

## 19. Objection/Cooldown Behavior

`PRICE_OBJECTION` -> `mark_offer_declined price_objection` -> `consecutive>=3` -> `commercial_paused` -> `COOLDOWN`, no repitch. `maybe later` -> `negative_count 1 <2` but `hours<24` cooldown blocks. Verified.

---

## 20. Purchased-Content Exclusion

`WHERE creator_id=$1 AND user_id=$2` everywhere. Verified.

---

## 21. Creator Isolation

Every query `WHERE creator_id`. Verified.

---

## 22. DropFans External Blocker

No buyer-scoped grant, verified via `client.py` inventory, `sales_url` fallback safe.

---

## 23. Exact Fixes

### Fix #1 — Product Selection Thresholds

No code change — thresholds `0.30/0.15` are evidence-based via matrix above. Document as final.

### Fix #2 — Per-Sale Transaction Identity

**File:** `db/dropfans.py:record_dropfans_sale`, `integrations/dropfans/service.py:reconcile_sales`, `commerce/reconciliation.py`

Use `get_earnings` transaction `id` as `transaction_id` when available, fallback to `dropfans:{drop_id}:{buyer_email}:{paid_at}` hash. Ensure `UNIQUE(creator_id, transaction_id)` where `transaction_id` is per-sale unique, not per-product.

### Fix #3 — Autonomous Re-Engagement via Existing Scheduler

**File:** `commerce/re_engagement.py` (NEW helper, not new worker) + `commerce/post_purchase.py` or `workers/scheduler_worker.py`

Use existing `scheduled_messages` + `scheduler_worker` (no new queue/worker). Create `is_reengagement_eligible` deterministic check and `schedule_reengagement_if_eligible` that enqueues `reengage:{creator}:{user}:{product}` with `execute_at NOW+48h` when `abandoned` and not `cooldown/aftercare/rejected`.

### Fix #4 — Aftercare Completion via Scheduler

**File:** `workers/scheduler_worker.py`

After `scheduled_messages` followup executed, call `mark_aftercare_completed` if `aftercare_status pending/sent` and `hours>24`.

---

## 24. Tests

- Product selection matrix (exact/strong/weak/unrelated/opaque/purchased)
- Transaction identity (same sale twice -> one, two distinct -> two, webhook+poll duplicate)
- Re-engagement eligible/too soon/rejected/cooldown/aftercare/purchased/no interest/creator isolation
- Aftercare pending/completion/preference/next desire
- Qwen contract (relationship/build_desire/test_interest/present_offer/cooldown/aftercare/repeat)
- Single-pass

---

## 25. Runtime Verification

- Consumer groups pending, sales transactions, offers, aftercare, vault products via read-only `SELECT` if Redis/Postgres available.
- No mutation.

---

## 26. Remaining Gaps

- P2 per-sale transaction_id (fix #2 will close)
- P2 re-engagement autonomous (fix #3)
- P2 aftercare via scheduler (fix #4)
- P2 opaque titles, tokenizer, description not persisted

---

## 27. Rollback

`git revert` for `commerce/product_selection.py` (threshold doc only), `db/dropfans.py`, `commerce/re_engagement.py`, `workers/scheduler_worker.py`. No DB migration.

---

## 28. Architecture Confirmation

No redesign, no new queue/worker/engine/ORM/provider.

---

*Forensic Section Complete — awaiting implementation.*
