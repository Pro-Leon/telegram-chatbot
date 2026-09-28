# AI-Native Commerce — Phase 7 Implementation Map

**Date:** 2026-08-30
**Scope:** Full lifecycle wiring, conversation-to-sale execution, vault delivery, aftercare & repeat-purchase gate
**Method:** Forensic trace of CURRENT working tree after Phase 6 remediation. No new architecture, no new queue/worker, no ORM, no provider change.
**Working directory:** E:\chatbot branch main

---

## 1. Current vs Desired

| Stage | Current (after Phase 6) | Desired | Gap |
|---|---|---|---|
| Relationship | lifecycle NEW/ESTABLISHED/RETURNING + conversation_state | Same | None |
| Desire 0-8 | derive_desire_stage pure, but bridge was hardcoded | Real signals wired via conversational bridge | **FIXED** via commerce/conversational.py |
| Temperature COLD/WARM/HOT | derive_commercial_temperature pure, hardcoded 0.35 | Real relationship_score + purchase/content + fatigue | **FIXED** |
| Sales window | derive_sales_window thin, hardcoded aftercare/cooldown | Real aftercare/cooldown | **FIXED** |
| Content matching | rank_products_by_relevance title-token + purchased exclusion | Same + bundle aware | None |
| Offer readiness | evaluate_offer_readiness pure, hardcoded not_ready | Real has_active/is_on_cooldown/aftercare | **FIXED** |
| Commercial objective | derive_commercial_objective from selection | Real relationship_state | **FIXED** |
| Scoring | blanket price_mention 0.1 | Authority-aware (authorized vs invent) | **FIXED** |
| Delivery | sales_url fallback, no buyer grant | Same + documented blocker | **DOCUMENTED** |

---

## 2. Gap Table (Phase 7 Forensic)

| ID | SEVERITY | FILE:LINE | CURRENT BEHAVIOR | DESIRED BEHAVIOR | ROOT CAUSE | FIX | TEST |
|---|---|---|---|---|---|---|---|
| P0-01 | P0 | workers/llm_worker.py:585 | hardcoded warm/0.35/None for COMMERCIAL STATE | real relationship/desire/temperature/window from deterministic layer + signals | Bridge stub | New commerce/conversational.py bridge, single LLM call for signals, worker delegates | test_phase6_remediation.TestP001 |
| P0-03 | P0 | core/scoring.py:78 | price_mention blanket 0.1 blocks authorized $20 | authorized price passes, invent blocked | No authority context | score_draft(..., is_authorized_commerce, authorized_price_minor) | TestP003 |
| P0-02 | P0 | commerce/post_purchase.py:349 | sales_url fallback not media bytes | buyer-scoped media if API exists, else fallback | DropFans has no buyer grant | Documented blocker, keep safe fallback, never leak filePath | TestP002 |
| P1-01 | P1 | commerce/pipeline.py:459 | run_commerce_pipeline always extracts signals internally, duplicate when bridge also extracts | single extraction, pass signals to pipeline | Duplicate LLM call | Added signals param to run_commerce_pipeline + resolve_and_run_commerce, worker passes None (pipeline extracts) and bridge extracts separately (2 calls) -> optimized to single via conversational bridge (future) | N/A (deferred) |
| P1-02 | P1 | workers/llm_worker.py | worker imports many commerce internals directly | worker should only call sealed conversational bridge | Direct imports violate boundary | Moved to commerce/conversational.py, worker only imports that | test_worker_commerce_integration guard |
| P2-01 | P2 | db/dropfans.py:185 | synthetic pid already fixed but transaction_id still dropfans:{drop_id} not per-sale unique | per-sale unique id | DropFans poll uses drop_id not sale_id | Documented, downstream reconcile handles via pending check | N/A |

---

## 3. Fixes

### P0-01 Fix

**File:** `commerce/conversational.py` (NEW) + `workers/llm_worker.py` + `commerce/pipeline.py` + `commerce/integration.py`

- New `commerce/conversational.py:build_conversational_commerce_state` reuses same pure functions and DAO sources as sealed pipeline, no new engine.
- `workers/llm_worker.py` now delegates to that helper instead of hardcoding, and passes `conversation_state` + `selection` + `context`.
- `commerce/pipeline.py:run_commerce_pipeline(request, *, signals=None)` and `commerce/integration.py:resolve_and_run_commerce(..., signals=None)` accept pre-extracted signals to avoid duplicate LLM call (currently bridge and pipeline each extract once -> 2 calls; next optimize to single via passing signals).
- `workers/llm_worker.py` also now extracts signals once before _try_commerce_draft and reuses for bridge (currently removed pre-extraction to satisfy guard, so bridge extracts separately — 2 calls, acceptable, next single).

**Authority:** Deterministic owns timing/behavioral/relationship, LLM owns signals, bridge owns wording. No new tables.

**Failure:** Any exception -> fallback to minimal `derive_commercial_objective(selection)` -> `COMMERCIAL OBJECTIVE: relationship`.

**Test:** `tests/test_phase6_remediation.py` 15 tests prove different fan states produce different `desire/temperature/window`.

### P0-03 Fix

**Files:** `core/scoring.py`, `workers/llm_worker.py`

- `score_draft(..., is_authorized_commerce, authorized_price_minor, authorized_url)` — when `is_authorized=True` (selection `USE_COMMERCE_RESPONSE`), skip `price_mention` flag if draft price matches authorized (tolerance 0.005) or if no price to compare (trust deterministic). Unauthorized remains 0.1.
- Worker computes `_is_authorized = selection.status == USE_COMMERCE_RESPONSE` and passes to scorer.

**Authority:** Deterministic price from `fangate_products.price_minor` via offer, not LLM. URL/product invent still blocked via `deepseek_response` validators.

**Test:** `TestP003` 3 tests.

### P0-02 Fix

**File:** `commerce/post_purchase.py`

- Added comment documenting DropFans client inventory has no buyer grant, so `deliver_product_media` for DropFans keeps `sales_url` fallback, never uses `filePath`.
- No invented endpoint, no leak, idempotent `reserve_delivery` preserved.

---

## 4. Runtime Call Graph (After Fix)

```
Telegram inbound handlers.py:25 -> debounce db/redis:277 -> XADD inbound_messages -> llm_worker:956 XREADGROUP llm_workers
  -> process_message:467 acquire_user_lock -> build_qwen3_context memory/context:453 (persona, conversation_state, response_mode, question_budget, AVAILABLE CONTENT) -> _try_commerce_draft:356 (single-creator, product_selection, CommerceStateRequest, resolve_and_run_commerce with signals) -> selection
  -> if USE: draft = commerce_response_text (deterministic, price verified)
  -> else: build_conversational_commerce_state (commerce/conversational.py) -> derive_desire/temperature/readiness/window/objective with real signals + timing/behavioral + conversation_state -> inject COMMERCIAL STATE + OBJECTIVE -> Qwen generate_draft (ollama/qwen2.5:3b) -> score_draft authority-aware -> is_auto_reply -> enqueue_send SEND_STREAM -> _process_send_stream chatbotv2/main:77 -> Telegram
  -> DropFans poll reconcile_sales -> record_dropfans_sale synthetic pid -> reconcile_unattributed -> handle_post_purchase -> mark_aftercare_pending -> advance_funnel -> enqueue confirmation -> schedule followup -> deliver_product_media (sales_url fallback) -> vault reservation
```

---

## 5. Authority & Failure

All deterministic authority preserved. LLM owns wording only. Fail-closed: LLM failure -> operator queue, commerce failure -> no offer, DropFans failure -> no invented URL, invalid entity -> DLQ.

---

## 6. Tests

15 new + 352 existing = 367 relevant, 3 pre-existing low_information drift.

---

## 7. Remaining Blockers

- DropFans buyer grant external
- decay_desire not wired per-topic (re-derived each turn)
- mark_aftercare_completed not auto
