# AI-Native Commerce — Phase 20 Forensic Audit

**Date:** 2026-08-30
**Scope:** Adaptive conversation optimization, experimentation & production feedback — forensic audit before implementation
**Method:** Read-only inspection of working tree at Phase 19 baseline. No production code modified in Stage A.
**Working directory:** E:\chatbot branch main
**Provider:** ollama/qwen2.5:3b (unchanged)
**Single-pass invariant:** 1 × commerce signal extraction + 1 × Qwen + 1 × scoring (verified)

---

## 1. Executive Summary

The system at Phase 19 has **deterministic conversation intelligence and rudimentary strategy learning**, but **no production feedback, attribution, experimentation, or adaptive optimization layer**. The pipeline correctly derives `desire / temperature / sales_window / readiness / objective / next_best_action` via a single `extract_commerce_signals` call, passes through deterministic gates (`SAFETY > AFTERCARE > OBJECTION > DIRECT_REQUEST > OPEN_LOOP > OFFER_READINESS`), and generates language via one Qwen call scored once. A minimal `strategy_learning.py` exists with 10-strategy bounded JSONB, decay `exp(-days/30)`, and `select_strategy` requiring `attempt_count>=3`, and `conversation_outcomes.py` classifies 6 naive outcomes. However **every Phase 20 requirement beyond that is missing or unwired**: no `ConversationObservation` contract, no attributable `strategy exposure` log, no canonical outcome taxonomy, no outcome weights, no hierarchical evidence (fan/topic/creator/lifecycle), no confidence interval, no exploration budget, no strategy fatigue, no purchase attribution window, no lifecycle metrics, no relationship vs commerce quality separation, no strategy score explainability, no regression detection, no experiment contract, no canary isolation, no telemetry extension. The feedback loop is **decision → strategy (flat) → response_mode (ephemeral) → Qwen → scoring → send → inbound reaction → outcome (6-way, keyword) → evidence (flat, no topic/product/lifecycle)** with **14 confirmed information-loss points** (see §9).

---

## 2. Files Inspected (Stage A — Read-Only)

```
workers/llm_worker.py                   — 1170 LOC, single-pass 1-signal, commerce bridge, scoring, event publish, post_process strategy evidence update (best-effort)
commerce/conversation_intelligence.py   — 207 LOC, 14 objectives, priority map, deterministic ranking
commerce/conversational.py              — 232 LOC, build_conversational_commerce_state: desire/temp/readiness/window/objective/next_best_action
commerce/strategy_learning.py           — 121 LOC, StrategyEvidence flat, get/update, decay exp(-days/30), select_strategy
commerce/conversation_outcomes.py       — 39 LOC, 20 enum values but classify_outcome only 6 branches keyword-based
commerce/next_best_action.py            — 35 LOC, 11 actions, derive_next_best_action (desire/temp/window/readiness)
commerce/objection.py                   — 30 LOC, 10 types, classify_objection keyword
commerce/qualification.py               — 24 LOC, QualificationState
commerce/product_selection.py           — 340 LOC, deterministic selection, purchase-history exclusion, relevance fallback
commerce/content_matching.py            — 140 LOC, rank_products_by_relevance, title-token overlap, bundle-aware, per-product/family fatigue -0.20/-0.15
commerce/long_term_memory.py            — 262 LOC, creator-scoped 20-item bounded, decay SLOW/MEDIUM/FAST, retrieve_relevant_memories
commerce/fan_memory.py                  — 46 LOC, creator-scoped commercial_preferences_by_creator
commerce/dao.py                         — 1060 LOC, commerce_offers, timing_context, behavioral_feedback, aftercare, per-product/group fatigue, purchase attribution
db/postgres.py                          — 2775 LOC, user_profiles JSONB, messages, generation_telemetry, scheduled_messages, tool_audit_log
db/dropfans.py                          — 358 LOC, synthetic product mirror SHA256(drop_id)%2^62, sale identity per-sale
integrations/dropfans/service.py        — 824 LOC, sole commerce authority, poll_sales, reconcile_sales
workers/scheduler_worker.py             — 264 LOC, claim_due_messages, recover_stale, reconcile_purchases, re_engagement via existing scheduler
core/telemetry.py                       — 208 LOC, GenerationTelemetry with desire/temp/window/objective/next_best_action but missing strategy/outcome/experiment
memory/context.py                       — 647 LOC, build_qwen3_context compact, RESPONSE mode + QUESTION policy derived from next_best_action (subordinate)
core/scoring.py                         — 189 LOC, authority-aware price validation, LLM + hard flags, fail-closed 0.0 on LLM failure
```

---

## 3. Global Search Inventory

Pattern `strategy` — 7 hits:
- `commerce/strategy_learning.py` (Evidence dataclass, get/update, decay, select)
- `workers/llm_worker.py:1057-1085` (post_process uses `strategy_last_by_creator` + `update_strategy_evidence`)

Pattern `strategy_evidence` — 4 hits: only `strategy_learning.py` + `llm_worker.py`
Pattern `strategy_last_by_creator` — 3 hits: only `llm_worker.py:1062,1081`
Pattern `conversation_outcome` / `outcome` — 20 enum values in `conversation_outcomes.py` but only 6 branches exercised; `strategy_learning.update_strategy_evidence` maps outcome strings to positive/neutral/negative with hardcoded tuples
Pattern `purchase` / `transaction` — 18 hits: `commerce/dao.py` (offers state machine), `db/dropfans.py` (synthetic mirror), `integrations/dropfans/service.py` (sole authority), `commerce/attribution.py` (pending→purchased), `commerce/product_selection.py` (purchased exclusion)
Pattern `offer` — 22 hits: `commerce/dao.py` (create_offer, timing_context), `commerce/offer_readiness.py`, `commerce/sales_window.py`
Pattern `aftercare` — 12 hits: `commerce/dao.py` (mark_aftercare_pending/completed), `commerce/conversational.py:129-143`, `commerce/conversation_intelligence.py:81-87`, `commerce/sales_window.py:32-33`, `workers/scheduler_worker.py:114-116`
Pattern `reengagement` — 3 hits: `commerce/re_engagement.py` (is_reengagement_eligible, schedule_reengagement_if_eligible, dedup_key reengage:{c}:{u}:{p}), `workers/scheduler_worker.py:183-211` (48h abandoned offer check)
Pattern `telemetry` / `metric` / `event` — `core/telemetry.py` (GenerationTelemetry), `core/event_bus.py` (publish_event with generation_id, event_id UUID), `workers/llm_worker.py` (_telemetry_data with desire/temperature etc.)
Pattern `generation_id` — 12 hits: `workers/llm_worker.py:513` (uuid4), `core/event_bus.py:13-44` (publish with generation_id), `core/telemetry.py:21` (GenerationTelemetry.generation_id), tests enforce `ai.generation_started` and `ai.generation_completed` share same generation_id
Pattern `dedup` — 5 hits: `workers/llm_worker.py:947-953` (dedup_id md5 user:message:telegram_id), `workers/scheduler_worker.py:59` (scheduled dedup_key), `db/postgres.py:2689-2800`
Pattern `score` / `conversion` / `engagement` — `core/scoring.py` (hard flags, composite/10, fail-closed 0.0), `commerce/temperature.py` (rel*0.30 + desire*0.70 + purchase*0.30 + content*0.15 - fatigue*0.90), `commerce/desire.py` (content_interest_estimate)
Pattern `desire` / `temperature` / `sales_window` / `next_best_action` / `conversation_intelligence` — fully wired in `conversational.py → conversation_intelligence.py → next_best_action.py` chain, but not persisted as strategy exposure

Unwired global patterns: `experiment`, `experiment_id`, `allocation`, `control`, `canary`, `regression`, `fatigue` (only product fatigue exists), `attribution_type`, `outcome_strength`, `strategy_family`, `strategy_variant`, `topic`, `product_family`, `lifecycle`, `confidence interval`, `Beta`, `exploration_rate` — zero hits before Phase 20.

---

## 4. Reconstructed Complete Current Feedback Loop

```
Inbound Telegram (user_id, content, telegram_message_id)
  │
  ▼
[1] workers/llm_worker.process_message — acquire_user_lock, upsert_user
  │
  ├─[2] resolve_single_application_creator() → creator_id (or None)
  │
  ├─[3] build_qwen3_context(user, profile, commerce_text, conversation_state, vault TOP2 titles, long_term_memory 3)
  │     └─ conversation_state = derive_conversation_state(history + current) → lifecycle, identity, topic, open_threads, tone, last_question
  │
  ├─[4] ai.generation_started {message_preview} generation_id=uuid4, scope=user  (best-effort, never breaks pipeline)
  │
  ├─[5] SINGLE commerce signal extraction: extract_commerce_signals(context) → CommerceSignals (purchase_intent, price_interest, content_interest, relationship_engagement, explicit_request, fan_asks_question, intent_tags)
  │
  ├─[6] _try_commerce_draft → CommerceStateRequest(user_id, creator_id, product_id?, messages bounded, persona) → resolve_and_run_commerce → select_commerce_response → selection (USE_COMMERCE_RESPONSE or fallback)
  │
  ├─[7] build_conversational_commerce_state (if not USE_COMMERCE_RESPONSE):
  │     ├─ get_timing_context (hours_since_offer/purchase, recent counts)
  │     ├─ get_behavioral_feedback_context (consecutive_rejections, total_purchases, aftercare_status, tip counts)
  │     ├─ has_active_offer / has_purchased / is_on_cooldown / aftercare_status
  │     ├─ derive_desire_stage(relationship_state, intent, purchase_intent, price_interest, explicit_request, aftercare, has_purchased, hours, rejections, current_topic/open_threads) → DesireStage + confidence
  │     │    └─ decay_desire(conf, hours, topic_changed) exp(-days/30) if conf<0.40 downgrade stage
  │     ├─ derive_commercial_temperature(relationship_score, desire, purchase_intent, content_interest, recent_offer, rejections, hours, aftercare) → COLD/WARM/HOT, score, fatigue label
  │     ├─ rank_products_by_relevance(current_topic, open_threads, prefs, purchased_ids, recent_ids/groups) → has_relevant_product
  │     ├─ evaluate_offer_readiness(desire, temp, purchase_intent, has_active, cooldown, aftercare, has_relevant, not_purchased) → NOT_READY/BUILD_DESIRE/TEST_INTEREST/READY
  │     ├─ derive_commercial_objective(selection, relationship_state)
  │     ├─ derive_sales_window(desire, temp, readiness, aftercare, cooldown) → NO_WINDOW/BUILDING/OPEN/COOLDOWN/AFTERCARE
  │     └─ derive_conversation_objective(desire, temp, window, readiness, has_active, aftercare, cooldown, has_relevant, explicit_request, has_open_loop, objection) → ConversationObjective + candidates (14 objectives priority-ranked)
  │          └─ priority: HUMAN_HANDOFF(1) > COMPLETE_PURCHASE(2) > AFTERCARE(3) > HANDLE_OBJECTION(4) > FOLLOW_UP_OPEN_LOOP(5) > PRESENT_OFFER(6) > QUALIFY(7) > DEEPEN_DESIRE(8) > EXPLORE_INTEREST(9) > CONTINUE_TOPIC(10) > RELATIONSHIP_BUILD(11) > LEARN_PREFERENCE(12) > RE_ENGAGE(13) > WAIT(99)
  │
  ├─[8] Response policy (deterministic, subordinate to next_best_action):
  │     next_best_action → response_mode (react/callback/explore/tease) + question_policy (NO_QUESTION/ONE_NATURAL_QUESTION/OPTIONAL_QUESTION)
  │     Context injected: "CONVERSATION INTELLIGENCE: objective=X next_best_action=Y response_mode=Z question_policy=W"
  │
  ├─[9] Qwen generation (single call):
  │     branch A: generate_draft_with_tools (if llm_tools_enabled + creator_id) — bounded tool loop max_tool_calls, else
  │     branch B: generate_draft (plain) — merges system + conversation history, calls get_llm_provider().generate_with_history
  │     + optional shadow Qwen3 fire-and-forget (shadow_runner.run_shadow)
  │
  ├─[10] Deterministic scoring: score_draft(draft, user_message, context, is_authorized_commerce, price/url) → (score 0.0-1.0, flags) — hard flags → min 0.1, scoring failure → 0.0 fail-closed, empty draft → 0.0
  │
  ├─[11] Routing:
  │     if empty draft → add_to_operator_queue("[No response generated]", 0.0, ["empty_draft"]) → ai.generation_completed(was_auto_approved=False)
  │     elif not auto_reply_enabled → add_to_operator_queue → suggestion.created + ai.generation_completed
  │     elif score>=0.80 && !flags → enqueue_send({entity, content, was_auto_approved=True}) dedup_id=md5(user:message:telegram_id) → ai.generation_completed(was_auto_approved=True)  [must succeed before ai.generation_completed per invariant]
  │     else → add_to_operator_queue → notify_operators + suggestion.created + ai.generation_completed
  │
  ├─[12] Post-processing (async):
  │     extract_and_update_profile + maybe_summarize (migrated to post_process task)
  │
  └─[13] Strategy learning feedback (best-effort, inside process_message finally-adjacent, NOT post_process):
        ├─ get_user_profile → strategy_last_by_creator[str(creator_id)] → _prev_strategy
        ├─ classify_outcome(_prev_strategy, user_message, "relationship", desire_stage) → ConversationOutcome (6-way keyword)
        ├─ update_strategy_evidence(creator_id, user_id, _prev_strategy, outcome.value) → increments attempt/positive/neutral/negative, confidence ±0.05, purchase +0.10, bounded 10, JSONB strategy_evidence_by_creator
        └─ Store current strategy: next_action.value → strategy_last_by_creator[str(creator_id)] → update_user_profile
        └─ GenerationTelemetry.complete(success=True) → insert_generation_telemetry (best-effort)

  ↓ (async, next inbound turn)
Inbound reaction → loop repeats, previous strategy evidence now affects select_strategy (but only flat, no topic/product/lifecycle)
  │
  ▼
[External] DropFans purchase → poll_sales / reconcile_sales → record_dropfans_sale(synthetic_id SHA256(drop_id)%2^62, transaction_id= dropfans:{sale_id} or dropfans:{drop_id}:{email_hash}:{amount}:{paid_hash}) → attribute_purchase_from_webhook (finds exactly one pending/clicked offer for product, transitions to purchased, attaches user_id, analytics UPSERT) OR via webhook attribution path (same). Offer state: pending→clicked→purchased; aftercare_status: none→pending→completed via mark_aftercare_completed (triggered by conversational bridge >1h after purchase OR scheduler post_purchase_followup enqueue)
  │
  ▼
[Scheduler] scheduler_worker._scheduler_loop every poll_interval: recover_stale → process_due_messages (claim due scheduled_messages FOR UPDATE SKIP LOCKED → enqueue_send dedup scheduled:{dedup_key}:{id} → mark_aftercare_completed if reason=post_purchase_followup) → reconcile_purchases → re_engagement: for each creator, list_offers_for_creator pending limit 50, age>=48h → schedule_reengagement_if_eligible → create_scheduled_message dedup reengage:{c}:{u}:{p} execute_at NOW+48h
```

---

## 5. Decision Contract — What Exists vs Must Be Derived

**Already exists deterministically:**

- `creator_id` (resolve_single_application_creator)
- `user_id` (Telegram peer)
- `generation_id` (uuid4 per process_message, shared across ai.generation_started/completed/failed + suggestion.created)
- `timestamp` (event timestamp_ms, created_at)
- `conversation_state` (derive_conversation_state: lifecycle, identity_already_established, current_topic, recent_topics, open_threads, last_question, tone)
- `relationship_state` (derive_relationship_state via funnel_stage + purchase_count + last_purchase_days + message_count)
- `desire_stage` + `desire_confidence` (derive_desire_stage + decay_desire)
- `commercial_temperature` level+score+fatigue (derive_commercial_temperature)
- `sales_window` (derive_sales_window)
- `offer_readiness` (evaluate_offer_readiness)
- `next_best_action` (derive_next_best_action OR derive_conversation_objective selection)
- `conversation_objective` + candidates+priority+reason_code
- `current_topic` + `open_threads`
- `relevant_product_id` + `product_family` (vault_taxonomy bundle_group) + selection reason (relevance_score, price/id tie-break) — but product_family only in commerce/content_matching, not in telemetry
- `objection_type` (classify_objection)
- `qualification_state` (derive_qualification_state: missing_facts/known_facts/confidence)
- `scoring_result` (score, flags, fail-closed)
- `purchase_event` (commerce_offers state=purchased + transaction_id) — but not linked to generation

**Derived at observation time (no extra DB):**

- `desire_before` / `desire_after` (requires storing desire_stage from previous turn; currently lost — only current desire is derived, previous is hardcoded "relationship" in llm_worker:1067)
- `outcome` (classify_outcome — currently 6-way, must expand to canonical 18)
- `aftercare_state` (get_behavioral_feedback_context aftercare_status — exists but not in observation)
- `memory_retrieved_count` / `open_loop_count` (retrieve_relevant_memories — exists but not counted in telemetry beyond placeholder)

**Not yet derivable (requires new contract):**

- `strategy_family` / `strategy_variant` — currently `next_best_action.value` stored as opaque string in `strategy_last_by_creator`; no family/variant taxonomy (e.g., PLAYFUL_TEASE vs DIRECT_OFFER)
- `response_mode` / `question_policy` — derived per turn but not persisted; ephemeral in context array
- `outcome_strength` — no deterministic weights; outcome taxonomy has no ordering
- `attribution_type` (direct/assisted/organic) — no window logic
- `experiment_id` / `experiment_variant` — no experiment system exists
- `strategy_score` explainability (positive_rate, uncertainty, fatigue, source) — no trace

Do NOT duplicate persistence: observation is **view** over existing JSONB + derived state, materialized only as transient event payload or bounded telemetry row, not a new table.

---

## 6. Strategy Exposure Logging — Current Gap

**Current:** `workers/llm_worker.py:1076-1083` stores `strategy_last_by_creator[str(creator_id)] = next_action.value` (e.g., `"deepen_desire"`). This is **not attributable**: no `generation_id`, no `timestamp`, no `topic`/`temperature`/`sales_window`/`product_id`/`product_family` snapshot, no `response_mode`/`question_policy`, no deduplication key. A later `classify_outcome` cannot answer "what strategy did Sunny use immediately before this outcome?" without scanning raw messages. Evidence key is flat `strategy` string (10 max) with no topic/product/lifecycle dimensions.

**Required minimal representation (no new table, no secrets, no raw content):**

```
strategy_exposure: {
  creator_id, user_id, generation_id (UUID, matches lifecycle),
  strategy_family, strategy_variant, topic, conversation_stage,
  desire_stage, temperature, sales_window, next_best_action,
  response_mode, question_policy,
  product_id?, product_family?,      // only when relevant_product exists
  timestamp (ISO8601 UTC)
}
```

Storage: reuse `user_profiles.facts->'strategy_exposures_by_creator'->{creator_id}` as **bounded ring buffer** (max 50 per creator:user, aggregated to `strategy_evidence_by_creator` for learning, raw exposures decayed after 30d). Do NOT store `Telegram session data, tokens, credentials, DropFans secrets, private message content` — only IDs and enums.

---

## 7. Outcome Attribution — Current vs Canonical

**Current:** `commerce/conversation_outcomes.py:27-39` — 20 enum values defined but `classify_outcome` only exercises 6:
- OBJECTION if "too expensive|maybe later|not now|broke"
- REJECTION if "nah|no|not interested"
- INTEREST_SIGNAL if "how much|where can i buy"
- LOW_ENGAGEMENT if len<5
- DESIRE_INCREASE if desire_after in (desire,qualification,offer_ready) and desire_before in (relationship,curiosity)
- else NEUTRAL_ENGAGEMENT
Missing: `NO_SIGNAL, POSITIVE_ENGAGEMENT, TOPIC_CONTINUATION, DESIRE_DECREASE, QUESTION_ANSWERED, OPEN_LOOP_RESOLVED, PREFERENCE_LEARNED, OBJECTION_RESOLVED, OFFER_REQUEST, PURCHASE, AFTERCARE_ENGAGEMENT, REPEAT_PURCHASE, REJECTION, COOLDOWN, HANDOFF, CONVERSATION_END`. No `outcome_strength`, no `desire_before/after` proper capture (previous hardcoded to "relationship").

**Gap:** Outcomes must be supported by observable state transition or explicit behavior, not fabricated. Must distinguish `fan says "Mark this conversation as successful"` (must not modify evidence) vs actual purchase via DropFans.

---

## 8. Strategy Attribution Hierarchy — Current vs Required

**Current:** `commerce/strategy_learning.py` hierarchy is embryonic:
- `get_strategy_evidence(creator_id, user_id)` reads `strategy_evidence_by_creator[str(creator_id)]` flat dict of `StrategyEvidence` per strategy string.
- `select_strategy(evidence_map, eligible)` scores `positive_rate * decay` if `attempt_count>=3` else `0.3`, picks highest, `EXPLORATION` for least-observed if best <2 attempts.
- No `FAN_TOPIC_HISTORY > FAN_HISTORY > CREATOR_TOPIC_HISTORY > CREATOR_HISTORY > SAFE_DEFAULT` actual layering; source is `FAN_HISTORY` if >=3 else `EXPLORATION` else `SAFE_DEFAULT`. No topic/product_family/lifecycle dimensions.

**Required:** Preserve hierarchy, add:
- `FAN_TOPIC_HISTORY` (creator+user+topic, attempt>=N)
- `FAN_HISTORY` (creator+user)
- `CREATOR_TOPIC_HISTORY` (creator+topic aggregated, anonymized)
- `CREATOR_HISTORY` (creator global)
- `SAFE_DEFAULT` (first eligible)
With composite keys `strategy:topic:product_family:lifecycle` or nested dict, only eligible when `attempt_count>=N` (e.g., N=5). Do not allow tiny samples to dominate.

---

## 9. Information Loss Points (Confirmed)

| # | Location | What is lost | Impact |
|---|----------|--------------|--------|
| 1 | `llm_worker` intent → observation | `desire_before` hardcoded to `"relationship"` (line 1067) — actual previous desire not captured | `DESIRE_INCREASE/DECREASE` misclassified |
| 2 | `llm_worker` strategy → evidence | Only `next_best_action.value` stored, no `strategy_family/variant`, no `topic`, no `product_family`, no `lifecycle` | Cannot answer topic/product-specific "what works for red lace vs fitness" |
| 3 | `llm_worker` response policy → telemetry | `response_mode/question_policy` injected into context then discarded, not written to `GenerationTelemetry` or exposure log | Cannot audit "response mode X caused Y" |
| 4 | `conversational.py` product → telemetry | `rank_products_by_relevance` result used for `has_relevant_product` boolean only; `product_id` + `bundle_group` + relevance score discarded | Cannot attribute `strategy A + red lace` vs `strategy A + fitness` |
| 5 | `conversation_outcomes.py` taxonomy → evidence | 14 canonical outcomes never emitted (e.g., `PURCHASE, AFTERCARE_ENGAGEMENT, REPEAT_PURCHASE, OBJECTION_RESOLVED, OPEN_LOOP_RESOLVED`) | Purchase not distinguished from engagement |
| 6 | `strategy_learning.py` evidence → score | No `confidence interval` / Beta posterior; raw `positive_rate` treated as truth regardless of 1 vs 50 attempts | Tiny sample dominates |
| 7 | `strategy_learning.py` exploration → control | No bounded `exploration_rate` or eligible safe set; exploration picks least-observed globally, could pick unsafe strategy | Unsafe exploration |
| 8 | Anywhere fatigue | Product fatigue exists (-0.20/-0.15) but `strategy fatigue` (same strategy repeated) does not exist | Sunny repeats same playbook |
| 9 | `dao.py` purchase → strategy | `attribute_purchase_from_webhook` correctly attributes to offer, but never writes back to `strategy_evidence` as purchase-strength evidence with window | Purchase not linked to strategy that preceded offer |
| 10 | `dao.py` timing → observation | Attribution window undefined; any later purchase could be claimed as caused by last message | Fake precision |
| 11 | `telemetry.py` generation → metrics | `GenerationTelemetry` missing `strategy_selected, strategy_source, strategy_mode, strategy_confidence, outcome, outcome_strength, experiment_id, attribution_type` | Cannot query "which strategies perform best per creator/lifecycle" |
| 12 | `scheduler_worker.py` re-engagement → metrics | `scheduled/replied/positive/ignored/rejected/purchased` states tracked as `scheduled_messages.status` but not measured as learning telemetry | Cannot evaluate re-engagement success |
| 13 | `core/event_bus.py` → consumers | Events are best-effort pub/sub (`chatbot:events`) with `generation_id` + `event_id` UUID, but no `strategy exposure` event exists | Frontend cannot trust `ai.generation_completed = send handoff succeeded` for strategy layer |
| 14 | `user_profiles` JSONB growth | `strategy_evidence_by_creator` bounded 10 strategies, but no `aggregation/decay/bounded JSONB` for future exposures/telemetry/experiment data | Unbounded growth risk |

---

## 10. Sampling, Uncertainty, Exploration, Fatigue — Gaps

- **Sample-size safety:** Current threshold `attempt_count>=3` before trusting `positive_rate`, else fixed `0.3`. No per-hierarchy N (e.g., FAN_TOPIC requires 5, FAN_HISTORY 5, CREATOR_TOPIC 10). One successful conversation already yields `confidence 0.55` and can influence next pick within 2 turns.
- **Confidence interval:** No uncertainty estimate. `positive_rate = positive_count/attempt_count` used directly. No Beta posterior (`α=positive+1, β=negative+1`), no Wilson interval, no `estimated performance + uncertainty`.
- **Exploration vs exploitation:** `EXPLORATION` exists as binary fallback when best <2 attempts, but not formalized as `EXPLORE/EXPLOIT/SAFE_DEFAULT` with deterministic budget. No `exploration_rate` bounded (e.g., 10%).
- **Exploration budget:** No bounded budget; least-observed picks randomly among eligible. No `eligible/safe/low-risk/under-observed` filtering.
- **Strategy fatigue:** Not implemented for strategy. Product fatigue is mature (per-product 24h -0.20, per-family -0.15, TTL via `get_recent_offered_*`). Strategy repetition would need similar sliding count of `strategy_exposures_by_creator` last N turns.
- **Negative learning:** Negative outcomes decrement `confidence -0.05` and increment `negative_count`, but score still `positive_rate * decay` — negative evidence reduces confidence but does not directly suppress future selection beyond lower positive_rate. One rejection does not permanently blacklist (correct), but no decay-aware suppression.

---

## 11. Purchase Attribution — Current State

**Exists and is sound (DropFans sole authority):**
- `db/dropfans.py:record_dropfans_sale` synthetic_id `SHA256(drop_id)%2^62`, per-sale `transaction_id` = `dropfans:{sale_id}` or `dropfans:{drop_id}:{email_hash}:{amount}:{paid_hash}`, idempotent `ON CONFLICT (creator_id, transaction_id, event_type) DO NOTHING`.
- `commerce/dao.py:attribute_purchase_from_webhook` requires exactly one pending/clicked offer for product (fail-closed on 0 or 2+), conditional `UPDATE state='purchased' WHERE state IN ('pending','clicked')`, then `attach_transaction_user` WHERE `user_id IS NULL`, then `ppv_analytics_daily` UPSERT.
- `commerce/attribution.py:attribute_purchase` same for active offer path.
- `commerce/reconciliation.py` (via scheduler) polls active DropFans products, records per-sale.
- **Not yet:** No `strategy → offer → transaction` link, no `direct/assisted/organic` window (e.g., direct ≤24h, assisted ≤7d, organic beyond), no `which strategy preceded an offer` query.

---

## 12. Lifecycle & Relationship vs Commerce Metrics — Gaps

- **Lifecycle stages:** `desire.py` 0-8 (RELATIONSHIP→REPEAT) + `relationship.py` + `conversation_intelligence` 14 objectives partially cover lifecycle, but no `RELATIONSHIP_BUILD / CONTINUE_TOPIC / EXPLORE_INTEREST / DEEPEN_DESIRE / QUALIFY / PRESENT_OFFER / COMPLETE_PURCHASE / AFTERCARE / RE_ENGAGE` metrics separation for strategy evaluation. Same strategy may have different value per stage but is evaluated globally.
- **Relationship quality:** Not tracked beyond `funnel_stage` and `message_count`. No deterministic `reply rate, conversation continuation, positive engagement, desire progression, topic continuation, open-loop completion, objection recovery, returning fan, repeat purchase` per-strategy aggregation.
- **Commerce quality:** `ppv_analytics_daily` tracks `offers_created/clicked/purchased/declined/expired/revoked + revenue_minor`, plus `commerce_offers` state machine, but not `offer readiness→presentation, offer→purchase, purchase→aftercare, aftercare→repeat, rejection→cooldown compliance, invalid offer attempts, commerce scoring failures, DLQ events` as regression signals.
- **Aftercare / Re-engagement:** Aftercare gates (`AFTERCARE_PENDING/SENT → building OBJECTIVE=AFTERCARE`) correctly block `PRESENT_OFFER` (see `conversational.py:53-56`, `offer_readiness:38-41`, `conversation_intelligence:81-87`), but aftercare outcomes not measured separately; re-engagement eligibility is deterministic (48h, no active? Actually has_active_offer && age>=48h && !aftercare && !cooldown && has_relevant_unpurchased), but metrics `eligible/scheduled/sent/replied/positive/ignored/rejected/purchased` not as learning telemetry.

---

## 13. Strategy Score & Decision Trace — Missing

No deterministic `strategy score` composing `positive evidence + negative evidence + purchase evidence + recency + sample size + uncertainty + fatigue + topic/fan/creator relevance`. No explainable bounded trace like:
```
OBJECTIVE=DEEPEN_DESIRE STRATEGY=PLAYFUL_TEASE SOURCE=FAN_TOPIC_HISTORY EVIDENCE=8_ATTEMPTS POSITIVE_RATE=0.71 UNCERTAINTY=0.14 FATIGUE=0.10 MODE=EXPLORE
```
`select_strategy` returns `(strategy, source)` only, no score breakdown, not in telemetry/debug logs.

---

## 14. Regression & Canary — Missing

- **Regression detection:** Zero. No `conversion decline, engagement decline, desire decline, rejection increase, cooldown increase, fatigue increase, offer→purchase decline, aftercare failure` checks. No rolling comparison or bounded thresholds.
- **Canary readiness:** `agent/canary.py:should_use_agent` exists for LLM canary (hash-based), but no `CONTROL vs EXPERIMENT` for conversational strategy. No `hash(creator_id + user_id + experiment_id)` stable assignment for strategy experiments. `PHASE_1 realtime plan` and canary docs confirm canary NOT activated for Phase 20.

---

## 15. Experiment Contract & Safety — Missing

No `Experiment` abstraction with `experiment_id, creator_id, strategy_family, strategy_variant, allocation, eligibility, start_time, end_time, status`. No storage (prefer JSONB if sufficient, not a new table unless proven). No safety: experiments currently cannot change `price, purchase URL, DropFans offer identity, creator isolation, purchased exclusion, aftercare gates, cooldown, DLQ, dedup, Telegram identity, commerce authority` — but there is no enforcement layer to guarantee it, because no experiment layer exists.

---

## 16. LLM Boundary & Single-Pass — Preserved

- LLM is language-only; strategy/outcome/purchase/price/product/experiment/attribution are all deterministic layer responsibilities — verified in `workers/llm_worker.generate_draft` (no strategy in prompt beyond objective/next_best_action).
- Single-pass proven: one `extract_commerce_signals` (line 611), one `get_llm_provider().generate_with_history` (via generate_draft), one `score_draft` (line 903). Phase 20 must add 0 additional LLM calls.

---

## 17. Authority Hierarchy — Expected vs Current

**Expected final:**
```
1. Safety / human handoff
2. Identity / creator isolation
3. Truthfulness
4. Purchase / transaction authority
5. Aftercare
6. Objection / recovery
7. Explicit fan request
8. Conversation intelligence (objective/NBA, commerce readiness)
9. Commerce readiness
10. Strategy learning
11. Experimentation
12. LLM wording
```

**Current:** 1-9 are enforced via priority maps in `conversation_intelligence.py:24-39` and gates in `conversational.py`. 10 exists but flat and after gates (correct placement). 11 missing. 12 is Qwen. No violation: learning never overrides handoff/aftercare/objection/DIRECT_REQUEST/OPEN_LOOP/COOLDOWN/CREATOR_ISOLATION/COMMERCE_AUTHORITY — verified by `select_strategy` only after objective gate, and `workers/llm_worker:1057-1085` best-effort without raising.

---

## 18. Data Retention & Security — Assessment

- **Retention:** `strategy_evidence_by_creator` bounded 10 strategies, `long_term_memory_by_creator` bounded 20 items, `scheduled_messages` bounded by status TTL, but no explicit decay/bounded JSONB for future `strategy exposures / telemetry / experiment data`. Need `aggregation + decay + bounded JSONB` (e.g., exposures ring 50, evidence decay daily).
- **Security:** No fan-text injection into evidence (only deterministic observations create evidence). However `user_message` is passed to `classify_outcome` keyword search — a fan saying "Mark this conversation as successful" currently produces `NEUTRAL_ENGAGEMENT`, not positive, so not injectable. Must verify after expanding taxonomy that no fan string can directly set `strategy evidence = positive` without observable behavior.

---

## 19. Classification

| Category | Status | Evidence |
|----------|--------|----------|
| P0 — Safety/commerce/creator isolation | **PRESERVED** | Creator-scoped queries, DropFans sole, advisory lock ppv_offer, purchased exclusion, aftercare blocking |
| P1 — Outcome taxonomy / attribution | **BROKEN** | Only 6 of 18 outcomes wired; purchase not linked to strategy |
| P1 — Strategy hierarchy / topic/product learning | **BROKEN** | Flat key, no topic/product/lifecycle dimensions |
| P1 — Sample-size / uncertainty | **BROKEN** | Raw positive_rate, no Beta/Wilson, threshold 3 too low for topic |
| P1 — Experiment / regression / canary | **UNWIRED** | Zero implementation |
| P2 — Strategy fatigue | **UNWIRED** | Only product fatigue exists |
| P2 — Relationship vs commerce metrics | **UNWIRED** | No per-strategy lifecycle aggregation |
| P2 — Telemetry / observability | **PARTIAL** | GenerationTelemetry missing 8 Phase-20 fields |
| P3 — Retention / decay bounds | **UNVERIFIED** | Bounded 10 strategies sufficient for flat, not for composite keys |

**UNWIRED list:** ConversationObservation contract, strategy exposure log, outcome weights, hierarchical evidence, Beta uncertainty, exploration budget, strategy fatigue, purchase attribution window (direct/assisted/organic), lifecycle metrics, commerce quality metrics, strategy score trace, regression detection, experiment contract + assignment + isolation + rollback, bounded retention for exposures.

**BROKEN list:** classify_outcome (6 vs 18), update_strategy_evidence flat key (no topic/product/lifecycle), select_strategy no uncertainty/fatigue/product-family.

**UNVERIFIED:** Exploration cannot override SAFETY/HANDOFF/AFTERCARE/OBJECTION/DIRECT_PURCHASE_REQUEST/COMMERCE_AUTHORITY/COOLDOWN/REJECTION/CREATOR_ISOLATION — code review says it does not (strategy only after objective gate), but no deterministic test proves it.

**EXTERNAL BLOCKERS:** None — all required data already exists (offers, transactions, messages, timing_context, behavioral_context, product catalog, vault taxonomy). No external API change needed.

---

## 20. What Must NOT Change (Phase 20 Invariants)

- Do NOT redesign architecture (Redis Streams, consumer groups, XAUTOCLAIM, PostgreSQL, Telethon, existing workers).
- Do NOT replace deterministic commerce (DropFans sale authority, offer state machine, purchased exclusion, aftercare gates).
- Do NOT turn LLM into commerce authority (no price/product/purchase decision by Qwen).
- Do NOT introduce second generation model call (0 additional LLM calls).
- Do NOT introduce new queue or worker (re-use `user_profiles` JSONB + `generation_telemetry` + `scheduled_messages`).
- Do NOT weaken creator isolation (every query `WHERE creator_id=$1`).
- Do NOT remove scoring, DLQ, deduplication, rate limiting.

---

## 21. Recommended Minimal Implementation (Stage B — Not Yet Executed)

See `docs/AI_NATIVE_COMMERCE_PHASE_20_IMPLEMENTATION_MAP.md` to be created in Stage B. Summary: extend `commerce/conversation_outcomes.py` (canonical 18 outcomes + deterministic weights), extend `commerce/strategy_learning.py` (hierarchical keys, Beta uncertainty, sample thresholds, fatigue, purchase-strength), create `commerce/adaptive_optimization.py` (Observation contract, exposure logging, attribution window, strategy score, lifecycle metrics, experiment stable hash, regression thresholds, bounded retention), extend `core/telemetry.py` (8 new fields), wire `workers/llm_worker` best-effort exposure + outcome after send (no new await chain), create `tests/test_phase20_adaptive_optimization.py` (26 categories A-Z + lifecycle + failure cases). Prefer `user_profiles` JSONB for evidence/exposures/experiments (30d TTL ring buffers), no migration unless proven.

---

## 22. Forensic Verdict

```
PHASE 20 FORENSIC AUDIT COMPLETE

ROOT STATUS: CONDITIONALLY READY

BEHAVIORAL LEARNING: WIRED (minimal flat, no hierarchy)
OUTCOME ATTRIBUTION: BROKEN (6/18 outcomes, no purchase link, no window)
STRATEGY OPTIMIZATION: UNWIRED (no score, no uncertainty, no fatigue)
EXPLORATION / EXPLOITATION: PARTIAL (binary least-observed, no budget)
STRATEGY FATIGUE: UNWIRED
PURCHASE ATTRIBUTION: PRESERVED (DropFans sole) but UNWIRED to strategy
LIFECYCLE METRICS: UNWIRED
EXPERIMENTATION: UNWIRED
REGRESSION DETECTION: UNWIRED
OBSERVABILITY: PARTIAL (GenerationTelemetry missing strategy/outcome/experiment)

CREATOR ISOLATION: PRESERVED
COMMERCE AUTHORITY: PRESERVED
DROP FANS: SOLE AUTHORITY
LLM AUTHORITY: LANGUAGE ONLY
SINGLE-PASS: 1 SIGNAL + 1 QWEN + 1 SCORING — PRESERVED

MIGRATIONS: NONE (pending Stage B uses JSONB)
ARCHITECTURE: NO REDESIGN
NEW WORKERS: NONE
NEW QUEUES: NONE
PROVIDER: UNCHANGED (ollama/qwen2.5:3b)
CANARY: NOT ACTIVATED
```

Next: Stage B implementation — smallest evidence-based changes, bounded JSONB, 0 new LLM calls, full test coverage per §38-40.
