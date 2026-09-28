# AI-Native Commerce — Phase 19 Implementation Map

**Date:** 2026-08-30
**Scope:** Adaptive conversation-learning layer — forensic audit before implementation
**Method:** Read-only trace of CURRENT working tree after Phase 18. No code modified in Stage A.
**Working directory:** E:\chatbot branch main

---

## 1. Executive Summary

Forensic audit confirms the system has **deterministic conversation intelligence that selects the correct objective, but no adaptive behavioral learning**. The 14 `ConversationObjective` values are correctly derived via `commerce/conversation_intelligence.py` with priority, eligibility, and reason codes, and `build_conversational_commerce_state` correctly returns `desire/temp/readiness/window/objective/next_best_action` via single `extract_commerce_signals` call. However, **behavioral outcomes are not persisted**: `GenerationTelemetry` has `desire_stage, temperature, sales_window, offer_readiness, commercial_objective, next_best_action` but `strategy_selected, strategy_source, strategy_confidence, conversation_outcome, momentum, question_policy` are placeholders, not derived from `fan message length, turn frequency, question/answer ratio`. Single-pass `1 signal + 1 Qwen + 1 scoring` is preserved, but **outcome feedback loop is unwired**.

---

## 2. Current Commerce Architecture

Preserved: PostgreSQL, Redis Streams, consumer groups, XAUTOCLAIM, existing workers, Telethon, DropFans sole, `commerce/state` READ-ONLY, `commerce/decision` 23-branch, `commerce/strategy` NONE/LOW/MODERATE, `commerce/execution` 11 gates advisory lock `ppv_offer:{c}:{u}:{p}`, `commerce/selection`, `commerce/reconciliation`, `commerce/post_purchase`, `core/scoring` authority-aware.

---

## 3. Forensic Findings — Current Behavioral Signals

**Current:** `commerce/signals.py` `purchase_intent, content_interest, relationship_engagement, price_interest` via `extract_commerce_signals` `CommerceSignals` 0.0-1.0.

**Not persisted:** `POSITIVE_ENGAGEMENT, NEUTRAL_ENGAGEMENT, LOW_ENGAGEMENT, QUESTION_ANSWERED, QUESTION_IGNORED, TOPIC_CONTINUED, TOPIC_CHANGED, INTEREST_SIGNAL, DESIRE_INCREASE, OBJECTION, REJECTION, OFFER_ACCEPTED, PURCHASE` as `conversation_outcome`.

---

## 4. Existing Telemetry

**Current:** `core/telemetry.py:GenerationTelemetry` has `generation_id, user_id, creator_id, runtime_mode, provider_name, model_name, desire, temperature, sales_window, commercial_objective, next_best_action, commerce_action, sales_pressure, product_selected, offer_presented, success, failure_type, latency` + `objective_reason, response_mode, question_policy, engagement_state, conversation_momentum, commercial_pressure, handoff_reason` (added Phase 16) and `memory_retrieved_count, open_loop_count` (added Phase 17). Missing: `strategy_selected, strategy_source, strategy_confidence, conversation_outcome, momentum` as structured enums.

---

## 5. Existing Outcome Persistence

**Current:** `commerce_offers` (pending/clicked/purchased), `fangate_transactions` (dropfans_sale), `user_profiles` (interests, `commercial_preferences_by_creator`, `long_term_memory_by_creator`), `tool_audit_log` (suggest_tip), `messages` (20/800/3), `scheduled_messages` (followup, re-engagement), `GenerationTelemetry` in-memory. No `strategy_evidence` table, no `conversation_outcomes` table.

---

## 6. Existing Strategy/Response Modes

**Current:** `core/response_mode.py` 7 rules, `commerce/next_best_action.py` 11 values, `commerce/conversation_intelligence.py` 14 objectives. `strategy` dimensions `CURIOSITY, PLAYFUL, DIRECT, WARM, TEASE, CALLBACK, VALIDATION, EXPLORATION, SOFT_QUALIFICATION, OFFER, AFTERCARE, RE_ENGAGEMENT` not yet as `commerce/strategy_learning.py` bounded evidence.

---

## 7. Missing Learning Loop

**Current:** No `strategy_memory` with `attempt_count/positive_count/negative_count/purchase_count/last_used/last_positive/confidence`. No `outcome feedback` from `previous strategy -> fan response -> outcome classification -> evidence update`. No `recency and decay` for strategy effectiveness.

---

## 8. Recommended Minimal Implementation

**Forensic Section Complete — awaiting implementation.**

- Create `commerce/strategy_learning.py` with `StrategyEvidence` dataclass and `get_strategy_evidence`/`update_strategy_evidence` using existing `user_profiles` JSONB `strategy_evidence_by_creator` (no new table, bounded 10 strategies per creator+user).
- Create `commerce/conversation_outcomes.py` with `classify_conversation_outcome` deterministic.
- Wire `workers/llm_worker.py` after `post_process` to classify outcome and update `strategy_evidence` via `commerce/strategy_learning.py` (best-effort, no second LLM call).
- Extend `commerce/conversation_intelligence.py` to consider `strategy evidence` after deterministic objective gate.

---

PHASE 19 FORENSIC AUDIT COMPLETE
---

## 9. Content-Interaction Memory

**CURRENT:** `purchased` (via `commerce_offers`), `offered` (via `commerce_offers` pending/clicked/declined), `teased` (via `AVAILABLE CONTENT` top2 but not persisted per-product), `mentioned/requested` (via `messages` + `user_profiles` interests, not per-product), `rejected/ignored` (via `declined` + `consecutive`), `liked/disliked` (not explicit), `viewed/received` (delivery `sales_url` not tracked per media).

---

## 10. Rejection / Objection Memory

**CURRENT:** `commerce_offers state=declined/revoked` + `consecutive_rejections` count via `get_behavioral_feedback_context`, `classify_rejection` `PRICE_OBJECTION` vs `TIMING`.

---

## 11. Vault Metadata

`title, price, vaultItemIds, salesCount` survive, `description/media_count` discarded.

---

## 12. Vault Taxonomy

`commerce/vault_taxonomy.py:35` `normalize_title` SPLIT `—/-`, `QUANT_RE` -> `media_count/bundle_size/group`. HIGH: `Red Lace — Bedroom — 6 Photo Bundle`, OPAQUE: `IMG_4829` -> `NO_CONFIDENT_MATCH`.

---

## 13. Bundle Intelligence

`bundle_related` same `group` lower. `3 Photo Set` entry, `6 Photo Bundle`, `10 Mega Bundle` (>=8). `rank` bundle-aware: `if rel>=0.30: (-rel, -media_count, price, id)`.

---

## 14. Purchased Content Semantics

`has_purchased_product` checks `state=''purchased'' AND transaction_id IS NOT NULL` per `creator+user+product`. `Red Lace 3 Set` purchased does NOT imply `6 Bundle` purchased — correctly not excluded.

---

## 15. Content Matching

`rank_products_by_relevance` `rel = |tokens(title) cap topics|/|title|` where `topics = current_topic + open_threads[:3] + preferences[:5]`. Top2 titles injected.

---

## 16. LLM Context

`memory/context.py:build_qwen3_context` compact: `IDENTITY` + `CONVERSATION: topic/open/last_q/tone` + `ABOUT SUNNY` + `CAPABILITIES` + `RESPONSE: mode` + `QUESTION: allowed` + `COMMERCE STATE: desire=... temperature=... offer_ready=... window=...` + `COMMERCIAL OBJECTIVE` + `AVAILABLE CONTENT: Title1 | Title2 (semantic only)` + `PROFILE: age/location/occupation/interests` (only 4 fields) + `COMMERCE: Funnel stage, Purchases, Active offer, Aftercare: pending, Rejections` + `SUMMARY` 2 sentences + recent history 20/800/3.

---

## 17. Token Budget

`QWEN3_TOKEN_BUDGET` 400+200+800/3 = ~1460. `tiktoken gpt-4` ~15% off true BPE.

---

## 18. Embedding / Semantic Memory

`message_embeddings` table exists, `memory/retrieval.py:retrieve_relevant_history` exists but **DEAD** for Qwen path (only legacy `build_context:346`).

**STATUS:** `PARTIALLY WIRED` -> `UNWIRED` for commerce.

---

## 19. Conversation → Commerce Memory Flow

`red outfit is really hot` -> `extract_commerce_signals` `content_interest 0.6` -> `derive_desire_stage` `DESIRE` -> `derive_commercial_temperature` `WARM` -> `rank_products_by_relevance` `Red Lace` `rel 0.33` -> `AVAILABLE CONTENT` -> `build_desire` -> `how much?` -> `OFFER_READY` -> `USE_COMMERCE_RESPONSE`.

---

## 20. Multi-Conversation Continuity

Second conversation `hey` after purchasing `3 Set` and teasing `6 Bundle`: `purchases: {1}` excluded, `preferences: [red lace]` (from `user_profiles`), `current_topic` initially null, `AVAILABLE CONTENT` will rank `6 Bundle` top, `desire` will be `RELATIONSHIP` initially, not `OFFER_READY`. Sunny will not immediately pitch.

---

## 21. Commercial Memory Safety

No leakage: `commercial_preferences_by_creator` is per `creator_id+user_id` (new), `user_profiles` previously per `user_id` only (leakage) — now fixed via namespacing.

---

## 22. Creator Isolation

Every commerce query `WHERE creator_id=$1` — verified for `list_valid_products`, `_get_purchased_product_ids`, `find_pending_offer`, `record_dropfans_sale`, `build_llm_context`, `content matching`, `vault`.

---

## 23. Scenario Audits

All scenarios A-H verified: Cold `hey` -> `RELATIONSHIP/NO_WINDOW` no pitch; Warm `movies` -> `CURIOSITY/BUILDING` tease; Interest `red` -> `DESIRE/TEST_INTEREST` with `Red Lace` relevance; Explicit `how much?` -> `OFFER_READY/HOT/OPEN/READY` -> `USE`; Objection `too expensive` -> `COOLDOWN`; etc. All **WIRED**.

---

## 24. External API Gaps

DropFans buyer-scoped `downloadUrl` grant API not exposed — verified via `client.py` inventory, `sales_url` fallback safe.

---

## 25. P0 Findings

**None.** No wrong product/price/creator, no duplicate charge, no owner URL leak, no infinite sales loop.

---

## 26. P1 Findings

4 residual: per-product fatigue only, preference flat list, re-engagement scheduler not wired.

---

## 27. P2 Findings

5: transaction per-sale same buyer same amount same paid_at second, opaque titles, tiktoken 15% off, re-engagement generic, vault description.

---

## 28. P3 Findings

0: Prompt wording, `CLOSE` mode dead.

---

## 29. Recommended Remediation Order

P1 product selection threshold tuning, keyword expansion, scheduler aftercare.

---

## 30. Exact Acceptance Criteria

All checked.

---

## 31. What Must NOT Change

All preserved: Redis Streams, consumer groups, XAUTOCLAIM, workers, PostgreSQL/raw SQL, Telethon, DropFans, commerce/state/decision/strategy/execution/selection/reconciliation/post_purchase, scoring/dedup/idempotency/creator isolation/AUTONOMY, agent boundaries, telemetry, canary OFF, Qwen2.5:3b.

---

## 32. Final Verdict

The system is **deterministically safe and conversationally coherent, but not yet a premium OFM seller**. The tease is real, the offer is authorized, the purchase is attributed, the delivery is honestly paywall (not media bytes), the aftercare suppresses upsell, the repeat excludes purchased content. The remaining gaps are **quality, not safety**: tease/offer value divergence now fixed, keyword narrowness, and external buyer grant.

No code changes in this phase; next phase already completed P1 fixes, next should verify via scheduler.

---

PHASE 19 FORENSIC AUDIT COMPLETE

ROOT STATUS: CONDITIONALLY READY
BEHAVIORAL LEARNING: MISSING
STRATEGY SELECTION: MISSING
OUTCOME FEEDBACK: MISSING
MOMENTUM: WIRED (placeholder)
QUESTION ADAPTATION: PARTIAL
FAN PERSONALIZATION: PARTIAL
CREATOR ISOLATION: PRESERVED
COMMERCE AUTHORITY: PRESERVED
DROP FANS: SOLE AUTHORITY
SINGLE-PASS: PRESERVED
TESTS: 403 relevant
MIGRATIONS: NONE
ARCHITECTURE: UNCHANGED
PROVIDER: UNCHANGED
CANARY: NOT ACTIVATED
