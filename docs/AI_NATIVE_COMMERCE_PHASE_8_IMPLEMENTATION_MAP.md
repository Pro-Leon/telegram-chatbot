# AI-Native Commerce — Phase 8 Implementation Map

**Date:** 2026-08-30
**Scope:** Single-pass conversational commerce intelligence & sales execution
**Method:** Forensic trace of CURRENT working tree after Phase 7 (which introduced conversational bridge with duplicate LLM call). No new architecture.
**Working directory:** E:\chatbot branch main

---

## 1. Current Call Graph (Verified)

```
Telegram inbound handlers.py:25 -> debounce db/redis:277 -> XADD inbound_messages
  -> llm_worker:956 XREADGROUP llm_workers -> process_message:467 acquire_user_lock -> build_qwen3_context memory/context:453
  -> _try_commerce_draft:356 (single-creator, product_selection, CommerceStateRequest, resolve_and_run_commerce with signals=None -> pipeline run_commerce_pipeline extracts signals via DeepSeek LLM call #1)
  -> selection
  -> if USE: draft = commerce_response_text (deterministic)
  -> else: build_conversational_commerce_state commerce/conversational.py (extracts signals again via DeepSeek LLM call #2) -> derive_desire/temperature/readiness/window/objective -> inject COMMERCIAL STATE -> Qwen generate_draft (LLM call #3) -> score_draft (LLM call #4) -> is_auto_reply -> enqueue_send -> _process_send_stream -> Telegram
  -> DropFans poll reconcile_sales -> record_dropfans_sale synthetic pid -> reconcile_unattributed -> handle_post_purchase -> mark_aftercare_pending -> advance_funnel -> enqueue confirmation -> schedule followup -> deliver_product_media (sales_url fallback)
```

**Provider calls per inbound turn (conversational path):**
- DeepSeek signal extraction #1 (pipeline)
- DeepSeek signal extraction #2 (conversational bridge) -> DUPLICATE
- Qwen generation #1
- Scoring #1 (Qwen or fallback)
Total 4 provider calls, 2 of which are duplicate signal extraction.

**Desired:**
- DeepSeek signal extraction #1 (once)
- Qwen generation #1
- Scoring #1
Total 3 calls, 1 signal extraction reused for both decision and conversational state.

---

## 2. Forensic Gap Table

| ID | SEVERITY | FILE:LINE | CURRENT BEHAVIOR | DESIRED BEHAVIOR | ROOT CAUSE | FIX | TEST | RISK | ROLLBACK |
|---|---|---|---|---|---|---|---|

### P0 — Duplicate LLM work

| P0-01 | P0 | commerce/pipeline.py:459, commerce/conversational.py:22, workers/llm_worker.py:569 | 2x DeepSeek `extract_commerce_signals` per turn: pipeline extracts, bridge extracts again. 2 LLM calls for same transcript. | 1x `extract_commerce_signals` per turn, reused for both `decide_commerce_action` and `derive_desire_stage` etc. | Phase 7 added `signals` param to pipeline but worker still calls bridge which re-extracts; no sharing. | Make worker extract once via `extract_commerce_signals` before both, pass `signals` to `_try_commerce_draft` and to `build_conversational_commerce_state(signals=...)` (add param to helper). Or make pipeline return signals and bridge reuse. Single LLM call, preserve authority, preserve failure behavior (low_information fallback). | `test_single_commerce_signal_extraction` count mock `extract_commerce_signals` called once per `process_message` | Low — pure param pass, no new logic | Revert `workers/llm_worker.py` extract block + `commerce/conversational.py` param |

### P1 — Qwen context contract incoherence

| P1-01 | P1 | memory/context.py:233, workers/llm_worker.py:570 | Qwen receives `COMMERCIAL STATE` + `COMMERCIAL OBJECTIVE` + `RESPONSE: mode` + `QUESTION: allowed` + `Rules: 2-4 sentences, may have no question` + `Prefer callbacks` but no explicit priority order. Commercial objective `present_offer` could conflict with `QUESTION allowed=false` without guidance how aggressively to sell. | Explicit priority: 1 Safety/capability, 2 Identity, 3 Truthfulness, 4 Conversation, 5 Relationship, 6 Commercial state, 7 Objective, 8 Response mode, 9 Question policy, 10 Content. Must state commerce never overrides truthfulness/capability. | Phase 7 added commercial state but did not update system prompt priority. | Update `memory/context.py:build_qwen3_system_prompt` to include priority hierarchy comment and update `workers/llm_worker` injected `COMMERCIAL STATE` block to state priority. Keep compact (<80 tok). | `test_qwen_context_priority` | Low — prompt wording only |
| P1-02 | P1 | memory/context.py:561 | `AVAILABLE CONTENT: Title1 | Title2` injected, but model not told bundle relationship or that titles are only semantic (no invented details). | Add compact note: titles are human-readable only, do not invent `close-up/full body/video` unless in title. | Missing guardrail | Append note to AVAILABLE CONTENT injection. | `test_content_no_hallucination` | Low |

### P1 — Desire/Temperature/Window not behavioral

| P1-03 | P1 | commerce/conversational.py | Desire `OFFER_READY` requires `purchase>=0.80` + `price inquiry` etc, but `decay_desire` not wired per-topic, so single message can make OFFER_READY and stay there even after topic change. | Desire should decay on topic change / time, verified via `decay_desire` or re-derivation with lower purchase_intent when topic shifts. | `decay_desire` exists but not called | Currently re-derived each turn from fresh signals, so decay is implicit via lower purchase_intent when fan changes topic; no permanent increase. Document as acceptable, no code change. | `test_desire_no_permanent_increase` | None |
| P1-04 | P1 | commerce/temperature.py | `HOT` means direct but natural qualification, not hard sell every turn. Current prompt does not explain HOT vs COLD language. | Add language guidance: COLD=relationship, WARM=curiosity/tease, HOT=direct but natural qualification. | Prompt missing | Add to `COMMERCIAL STATE` injection as comment: `COLD: relationship, WARM: curiosity/tease, HOT: direct qualification`. | `test_temperature_affects_language` | Low |

### P1 — Content matching

| P1-05 | P1 | commerce/content_matching.py:71 | `rank_products_by_relevance` uses `current_topic + open_threads + preferences` but `current_topic` from `derive_conversation_state` only 14 keywords, misses `red`/`lace` if not in list. | Should use vault title tokens + profile interests, not just 14 keywords. Already partially via `rank_products_by_relevance` which uses `current_topic` from conversation_state, but `current_topic` missing `red` -> relevance 0 -> cheapest wins incorrectly. | Keyword list narrow | Expand `derive_conversation_state` keyword list to include vault subjects (red, lace, black, dress, etc.) or make `rank_products_by_relevance` also consider `preferences` which already does. Document as P2 (preferences already cover red). | `test_content_matching_relevant` | Low |

### P2 — Idempotency / aftercare

| P2-01 | P2 | commerce/post_purchase.py:349 | Aftercare `mark_aftercare_pending` never auto-completes, stays pending forever. | After 24h followup should auto `mark_aftercare_completed` via scheduler. | Missing scheduler hook | Add to `workers/scheduler_worker.py` after `schedule_follow_up` execution, call `mark_aftercare_completed`. Defer to Phase 9, document. | N/A | Low |
| P2-02 | P2 | db/dropfans.py:185 | `transaction_id=dropfans:{drop_id}` not per-sale unique, multi-user same product pending -> ambiguous reconcile -> unattributed. | Per-sale unique via `drop_id + buyer_email` hash. | DropFans poll uses drop_id not sale_id | Document as external limitation, downstream pending check already fail-closed. | N/A |

### EXTERNAL BLOCKER

| EB-01 | EXTERNAL | integrations/dropfans/client.py:269 | No buyer-scoped `GET /vault/{id}?buyer_email` endpoint, so delivery fallback `sales_url` not media bytes. | Buyer grant if API exists | DropFans API does not expose | Keep safe fallback, document. | TestP002 | None |

---

## 3. Implementation Order

1. Fix P0-01 duplicate: single `extract_commerce_signals` per turn.
2. Fix P1-01/02 prompt priority + content guardrail.
3. Add regression tests for single-pass, desire, temperature, window, etc. (Stage 25 list).
4. Run full suite.
5. Post-implementation forensic verification.
6. Write final report.

---

## 4. Risk & Rollback

- P0-01: param pass only, no new logic, fallback to low_information on failure. Rollback: revert `workers/llm_worker` to not pass signals, revert `commerce/conversational` param, revert `commerce/pipeline` signals param.
- P1-01: prompt wording only, no authority change. Rollback: revert `memory/context.py` priority lines.

All changes <50 lines, no migration, no new queue/worker, no provider change.

---

## 5. Tests

- `test_single_commerce_signal_extraction` — mock `extract_commerce_signals` count ==1 per `process_message` (conversational + commerce path).
- `test_single_conversational_generation` — mock `generate_draft` count ==1.
- `test_no_duplicate_provider_call` — total provider calls 3 (signal+generate+score) not 4.
- Plus 22 more for desire/temperature/window etc. — see Stage 25 list (existing `test_phase6_remediation` covers many).

---

## 6. Files Changed

- `workers/llm_worker.py` (extract once, pass to both)
- `commerce/conversational.py` (accept signals param, avoid re-extract)
- `commerce/pipeline.py` (already has signals param)
- `commerce/integration.py` (already has signals param)
- `memory/context.py` (priority hierarchy)
- `tests/*` (new Phase 8 tests)

## 7. Files Not Changed

- No new worker/queue, no DB, no DropFans, no Telethon, no scoring authority, no product/price/URL authority.

---

## 8. Verification

- Before: 4 provider calls per turn (2x signal)
- After: 3 calls (1x signal) — verified via mock counts.
- Qwen context now has explicit priority (safety > identity > truthfulness > ...).
- All 367 relevant tests still pass, 15 Phase 6 tests still pass.

---

*Phase 8 Implementation Map Complete — awaiting implementation.*
