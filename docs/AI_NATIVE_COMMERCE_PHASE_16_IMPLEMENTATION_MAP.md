# AI-Native Commerce — Phase 16 Implementation Map

**Date:** 2026-08-30
**Scope:** Enterprise conversation-intelligence orchestration — forensic audit before implementation
**Method:** Read-only trace of CURRENT working tree after Phase 15 (long-term memory, fan memory v2, product knowledge). No code modified in Stage A.
**Working directory:** E:\chatbot branch main

> STAGE A — FORENSIC ONLY

---

## 1. Executive Summary

Forensic audit confirms the system has **deterministic desire/temperature/window/offer readiness** via `commerce/conversational.py` single bridge, **product knowledge** via `commerce/product_knowledge.py`, **objection** via `commerce/objection.py`, **qualification** via `commerce/qualification.py`, **next-best-action primitives** via `commerce/next_best_action.py`, but **no unified Conversation Intelligence Orchestrator** that deterministically ranks candidate objectives with priority, eligibility, and reason codes. Current `commercial_objective` (4 values: `relationship/build_desire/present_offer/aftercare`) is too coarse, `next_best_action` is primitive (11 values) not integrated into `memory/context.py` Qwen context, `response_mode` and `question_policy` are derived from `conversation_state` only, not from `desire/temperature/window/objective`, and `open_loops`/`commitments` are stored but not used to select `FOLLOW_UP_OPEN_LOOP`. Single-pass `1 signal + 1 Qwen + 1 scoring` is preserved, but orchestration is fragmented across `workers/llm_worker` and `memory/context`.

---

## 2. Current Architecture

Preserved: PostgreSQL, Redis Streams, consumer groups, XAUTOCLAIM, existing workers, Telethon, DropFans sole, `commerce/state` READ-ONLY, `commerce/decision` 23-branch, `commerce/strategy`, `commerce/execution` 11 gates, `commerce/selection`, `commerce/reconciliation`, `commerce/post_purchase`, `core/scoring` authority-aware, `core/conversation_state`, `core/response_mode`, `core/question_policy`, `core/capability_contract`, `AUTONOMY_ENABLED`, dedup, delivery reservation, creator isolation, `Qwen2.5:3b`.

---

## 3. Forensic Findings — Current NBA Derivation

**CURRENT:** `commerce/next_best_action.py:derive_next_best_action(desire, temperature, sales_window, offer_readiness, has_active_offer, aftercare_status)` returns 11 values, but **not called** from `workers/llm_worker` or `memory/context`. Instead, `workers/llm_worker` uses `commerce/objective.py:derive_commercial_objective` (4 values) and `commerce/conversational.py` returns `desire/temp/readiness/window/objective` separately. `next_best_action` is **dead** (0 callers).

**EVIDENCE:** `grep next_best_action` = 1 file `commerce/next_best_action.py` itself, 0 callers in `workers/`, `memory/`.

**DESIRED:** `commerce/conversation_intelligence.py` orchestrator that evaluates `conversation_state + long_term_memory + desire/temperature/window + offer_readiness + purchase/aftercare + open_loops/commitments + objections + qualification + product relevance` and returns `selected_objective` with `priority, eligibility, reason_code`.

**GAP:** `next_best_action` exists but unwired.

**FILE:** `commerce/next_best_action.py:1`, `workers/llm_worker.py`

---

## 4. Commercial Objective Derivation

**CURRENT:** `commerce/objective.py:derive_commercial_objective(selection, relationship_state)` maps `USE -> present_offer`, `FALLBACK -> relationship/build_desire/aftercare` via 4 values. **Not used** for `FOLLOW_UP_OPEN_LOOP`, `HANDLE_OBJECTION`, `RE_ENGAGE`.

**EVIDENCE:** `workers/llm_worker` calls it, `memory/context` does not.

**DESIRED:** Deterministic vocabulary `RELATIONSHIP_BUILD, CONTINUE_TOPIC, FOLLOW_UP_OPEN_LOOP, EXPLORE_INTEREST, DEEPEN_DESIRE, QUALIFY, HANDLE_OBJECTION, PRESENT_OFFER, COMPLETE_PURCHASE, AFTERCARE, LEARN_PREFERENCE, RE_ENGAGE, HUMAN_HANDOFF, WAIT` with explicit priority.

**GAP:** Objective vocabulary too coarse, no open-loop/commitment handling.

---

## 5. Response-Mode Derivation

**CURRENT:** `core/response_mode.py:plan_response_mode` 7 rules on `conversation_state` only (`CLARIFY` for `pic`, `SHARE` for `what are you up to`, `TEASE` for `flirty`, `ANSWER` for `?`, `EXPLORE` for `short fact`, `CALLBACK` for `saturday/netflix/popcorn`, `REACT` default). Not influenced by `desire/temperature/window/objective`.

**EVIDENCE:** `memory/context.py:528` calls it with `conversation_state` only.

**DESIRED:** `objective -> response_mode` deterministic: `FOLLOW_UP_OPEN_LOOP -> CALLBACK`, `EXPLORE_INTEREST -> EXPLORE`, `DEEPEN_DESIRE -> TEASE/EXPLORE`, `PRESENT_OFFER -> commerce response`, `AFTERCARE -> CALLBACK`.

**GAP:** Response mode not subordinate to commercial objective.

---

## 6. Conversation-State Derivation

**CURRENT:** `core/conversation_state.py:derive_conversation_state` pure, 14 keywords, `current_topic/recent_topics/open_threads/last_question/tone`, used in `memory/context` and `commerce/conversational`.

**GAP:** None — **WIRED**.

---

## 7. Memory Retrieval

**CURRENT:** `commerce/long_term_memory.py:retrieve_relevant_memories` relevance-ranked, bounded 3, creator-scoped via `current_topic + open_threads`, injected as `RELEVANT MEMORY: subject=value (type, conf)` in `memory/context.py`. **Not used** for `FOLLOW_UP_OPEN_LOOP` objective selection.

**GAP:** Memory retrieved but not used to select `FOLLOW_UP_OPEN_LOOP`.

---

## 8. Objection Handling

**CURRENT:** `commerce/objection.py:classify_objection` `PRICE/TIMING/TRUST/...` deterministic, `commerce/feedback:classify_rejection` `PRICE_OBJECTION` vs `TIMING`, `consecutive_rejections` -> `commercial_paused` -> `COOLDOWN`. `objection` memory exists but not used for `HANDLE_OBJECTION` objective.

**GAP:** Objection not wired to orchestrator.

---

## 9. Qualification

**CURRENT:** `commerce/qualification.py:derive_qualification_state` for `missing_high_value_fact` (e.g., `format_preference` unknown) and `known_facts`, but **not called** from `workers/llm_worker` or `memory/context`.

**GAP:** Qualification exists but unwired.

---

## 10. Sales-Window Decisions

**CURRENT:** `commerce/sales_window.py` `NO_WINDOW/BUILDING/OPEN/COOLDOWN/AFTERCARE` via `derive_sales_window`, used in `commerce/conversational` to derive `window`, but `window` not directly used to select `objective` beyond `commercial_objective`.

**GAP:** Window influences `objective` only indirectly via `readiness`.

---

## 11. Offer Readiness

**CURRENT:** `commerce/offer_readiness.py:evaluate_offer_readiness` with `has_active_offer/is_on_cooldown/aftercare_active/has_relevant_product/not_purchased` -> `NOT_READY/BUILD_DESIRE/TEST_INTEREST/READY`. **WIRED** and used to gate `PRESENT_OFFER`.

**GAP:** None.

---

## 12. Aftercare

**CURRENT:** `aftercare_status pending/sent` -> `AFTERCARE` window -> `AFTERCARE` objective, `mark_aftercare_completed` after 1h + inbound via `commerce/conversational.py`. **WIRED**.

---

## 13. Re-engagement

**CURRENT:** `commerce/re_engagement.py:is_reengagement_eligible` deterministic via `scheduled_messages` `reengage:{creator}:{user}:{product}` 48h, `workers/scheduler_worker.py` now calls `schedule_reengagement_if_eligible` for `has_active_offer && age>=48h`.

**GAP:** Not yet used to select `RE_ENGAGE` objective.

---

## 14. Handoff

**CURRENT:** `commerce/relationship:check_operator_handoff` returns `handoff_reason` `HIGH_VALUE/COMPLEX_OBJECTION/...` and `operator_queue` stores `commercial_state` + `last_offer` + `objection`. **Not used** to select `HUMAN_HANDOFF` objective.

**GAP:** Handoff not wired to orchestrator.

---

## 15. Scoring

**CURRENT:** `core/scoring.py:score_draft` authority-aware `is_authorized_commerce` — `USE_COMMERCE_RESPONSE` price ` $20` bypasses `price_mention` 0.1 cap, unauthorized remains 0.1. **WIRED**.

---

## 16. LLM Context

**CURRENT:** `memory/context.py:build_qwen3_context` compact: `IDENTITY` + `CONVERSATION: topic/open/last_q/tone` + `ABOUT SUNNY` + `CAPABILITIES` + `RESPONSE: mode` + `QUESTION: allowed` + `COMMERCE STATE` + `COMMERCIAL OBJECTIVE` + `AVAILABLE CONTENT: Title1 | Title2 (semantic only)` + `RELEVANT MEMORY` + `PROFILE` 4 fields + `COMMERCE` 5 facts + `SUMMARY` 2 sentences + recent history 20/800/3. Priority hierarchy added. **Missing:** `NEXT BEST ACTION`, `OBJECTIVE REASON`, `OPEN_LOOPS`, `COMMITMENTS`, `RELEVANT MEMORY` is already there but not `FOLLOW_UP_OPEN_LOOP` objective.

---

## 17. Telemetry

**CURRENT:** `GenerationTelemetry` has `desire_stage, temperature, sales_window, offer_readiness, commercial_objective, commerce_action, sales_pressure` + `generation_id, user_id, creator_id, runtime_mode, provider_name, model_name, latency`. Missing `conversation_objective, objective_reason, response_mode, question_policy, engagement_state, conversation_momentum, commercial_pressure, handoff_reason`.

---

## 18. Tests

**Current:** 403 relevant, 3 drift, plus 6 single-pass, 15 Phase6, 19 lifecycle. No dedicated `conversation intelligence` tests for `FOLLOW_UP_OPEN_LOOP`, `HANDLE_OBJECTION`, `RE_ENGAGE` via orchestrator.

---

## 19. Gaps Summary

| ID | Severity | File:Line | Current | Desired | Gap |
|---|---|---|---|---|---|
| P0 | P0 | `commerce/next_best_action.py` | 11 values defined, 0 callers | Wired orchestrator with priority, eligibility, reason codes, selected objective | Unwired |
| P1 | P1 | `memory/context.py` | `COMMERCIAL OBJECTIVE` 4 values, no `NEXT BEST ACTION` | Add `NEXT BEST ACTION` compact | Missing |
| P1 | P1 | `core/response_mode.py` | Not subordinate to `desire/temperature/window/objective` | Make `objective -> response_mode` deterministic | Unwired |
| P1 | P1 | `commerce/long_term_memory.py` | Retrieved but not used for `FOLLOW_UP_OPEN_LOOP` | Use `open_loops` to select `FOLLOW_UP_OPEN_LOOP` | Unwired |

---

## 20. Proposed Fixes

### Fix #1 — Conversation Intelligence Orchestrator

**File:** `commerce/conversation_intelligence.py` (NEW) or `commerce/next_best_action.py` extended.

Implement `ConversationIntelligenceOrchestrator` that evaluates `conversation_state + long_term_memory + desire/temperature/window + offer_readiness + purchase/aftercare + open_loops/commitments + objections + qualification + product relevance` and returns `CandidateObjectives` with `objective, priority, eligibility, reason_code, blocking_reason` and `selected_objective` via deterministic ranking.

**Priority (derived from forensic):**
1. SAFETY / BLOCKED (is_blocked, do_not_auto_reply)
2. ACTIVE PURCHASE / DELIVERY ISSUE (has_active_offer with delivery pending)
3. AFTERCARE (aftercare_status pending/sent)
4. ACTIVE OBJECTION (consecutive>=3 or recent `PRICE/TIMING` objection)
5. OPEN HIGH-VALUE CONVERSATIONAL LOOP (open_loop with `importance>=0.7` and `current_topic` overlap)
6. DIRECT FAN REQUEST (`how much?`, `can I buy?` via `explicit_purchase_request`)
7. RELATIONSHIP BUILDING (default)
8. DESIRE DEVELOPMENT (curiosity/interest)
9. QUALIFICATION (desire `QUALIFICATION` and `offer_readiness TEST_INTEREST`)
10. OFFER (`offer_ready READY` + `window OPEN`)
11. RE-ENGAGEMENT (abandoned 48h + relevant unpurchased)
12. WAIT (no window, no desire)

**Reason codes:** `NO_WINDOW, COOLDOWN_ACTIVE, AFTERCARE_PENDING, ACTIVE_OFFER, PURCHASED, NO_RELEVANT_PRODUCT, LOW_DESIRE, LOW_TEMPERATURE, DIRECT_REQUEST, ACTIVE_OBJECTION, OPEN_LOOP, QUALIFICATION_MISSING, REJECTION_RECENT, FATIGUE`.

**File:** `commerce/conversation_intelligence.py` NEW, `workers/llm_worker.py` calls it after `build_conversational_commerce_state` and before `Qwen generation`, `memory/context.py` injects `CONVERSATION INTELLIGENCE: objective=... response_mode=... question_policy=... reason=...`.

### Fix #2 — Integrate with Existing

`commerce/conversational.py` already returns `desire/temp/readiness/window/objective`, extend to also return `next_best_action` via new orchestrator, or make orchestrator consume `conversational_state`.

`workers/llm_worker.py` after `build_conversational_commerce_state`, call `derive_next_best_action` and inject `CONVERSATION INTELLIGENCE` compact.

`memory/context.py` extend `build_qwen3_state_context` to accept `next_best_action` + `objective_reason` and render as `CONVERSATION INTELLIGENCE: objective=...`.

Single-pass preserved (no new LLM call).

---

## 21. Tests

- `test_conversation_intelligence` for each objective (relationship, continue_topic, follow_up_open_loop, explore_interest, deepen_desire, qualify, handle_objection, present_offer, aftercare, re_engage, handoff, wait)
- Priority tests (higher beats lower)
- Direct request tests (`how much?` -> `PRESENT_OFFER` when ready)
- Open-loop tests (relevant open loop beats sales)
- Objection tests (price objection -> `HANDLE_OBJECTION` + `COOLDOWN`)
- Aftercare tests (aftercare -> `AFTERCARE` not `PRESENT_OFFER`)
- Cooldown tests (cooldown -> `RELATIONSHIP_BUILD`)
- Single-pass test (1 signal + 1 Qwen + 1 scoring)

---

## 22. Risk & Rollback

All changes <100 lines, deterministic, no new queue/worker, single-pass preserved, no DB migration. Rollback `git revert` for `commerce/conversation_intelligence.py`, `workers/llm_worker.py`, `memory/context.py`.

---

## 23. Verification

- Before: 4 `COMMERCIAL OBJECTIVE` values, no `NEXT BEST ACTION`, `response_mode` not subordinate to `desire`, `open_loops` not used for `FOLLOW_UP_OPEN_LOOP`.
- After: 13 `CONVERSATION OBJECTIVE` values with priority, eligibility, reason codes, `NEXT BEST ACTION` compact, `response_mode` deterministic from `objective`.

---

*Forensic Section Complete — awaiting implementation.*
