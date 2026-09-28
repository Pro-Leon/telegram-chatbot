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

## 6. Recommended Minimal Implementation

**Forensic Section Complete — awaiting implementation.**

- Create `commerce/strategy_learning.py` with `StrategyEvidence` dataclass and `get_strategy_evidence`/`update_strategy_evidence` using existing `user_profiles` JSONB `strategy_evidence_by_creator` (no new table, bounded 10 strategies per creator+user).
- Create `commerce/conversation_outcomes.py` with `classify_conversation_outcome` deterministic.
- Wire `workers/llm_worker.py` after `post_process` to classify outcome and update `strategy_evidence` via `commerce/strategy_learning.py` (best-effort, no second LLM call).
- Extend `commerce/conversation_intelligence.py` to consider `strategy evidence` after deterministic objective gate.

---

PHASE 19 FORENSIC AUDIT COMPLETE
