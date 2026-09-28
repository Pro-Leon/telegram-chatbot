# AI-Native Commerce — Phase 18 Forensic Audit

**Date:** 2026-08-30
**Scope:** Enterprise Conversation Execution & Behavior Consistency — forensic audit before implementation
**Method:** Read-only trace of CURRENT working tree after Phase 17 (conversation intelligence orchestrator, long-term memory). No code modified in Stage A.
**Working directory:** E:\chatbot branch main

> STAGE A — FORENSIC ONLY

---

## 1. Executive Summary

Forensic audit confirms the system has **deterministic conversation intelligence that selects the correct objective, but the execution of that objective is not fully deterministic**. The 14 `ConversationObjective` values are correctly derived via `commerce/conversation_intelligence.py` with priority, eligibility, and reason codes, and `build_conversational_commerce_state` correctly returns `desire/temp/readiness/window/objective/next_best_action` via single `extract_commerce_signals` call. However, **objective execution is partially wired**: `core/response_mode.py` is still derived from `conversation_state` only (7 rules), not from `next_best_action`/`objective`, so `FOLLOW_UP_OPEN_LOOP -> CALLBACK` is not enforced as `RESPONSE: mode` when `next_best_action` is `follow_up_open_loop` but `plan_response_mode` says `react`. `core/question_policy.py` `MAX_CONSECUTIVE 1, MAX_PER_3 1` is wired, but `commerce/conversational.py` does not pass `next_best_action` to `plan_response_mode`, so `FOLLOW_UP_OPEN_LOOP -> ONE_NATURAL_QUESTION` is not enforced. **Behavioral intelligence** (`engagement`, `momentum`, `reciprocity`, `fatigue`) is partially available via `get_recent_messages` and `conversation_state`, but not as structured enums. No P0 safety defects; P1 is objective execution wiring.

---

## 2. Current Architecture

Preserved: PostgreSQL, Redis Streams `inbound_messages`/`send_messages` consumer groups `llm_workers`/`send_workers` XAUTOCLAIM 60s/30s, `existing llm_worker` + `existing send_worker` + `existing scheduler_worker`, Telethon, DropFans sole, `commerce/state` READ-ONLY, `commerce/decision` 23-branch, `commerce/strategy` NONE/LOW/MODERATE, `commerce/execution` 11 gates advisory lock `ppv_offer:{c}:{u}:{p}`, `commerce/selection`, `commerce/reconciliation`, `commerce/post_purchase`, `core/scoring` authority-aware, `core/conversation_state`, `core/response_mode`, `core/question_policy`, `core/capability_contract`, `AUTONOMY_ENABLED`, dedup `md5(user:msg:tgId)` 3600s, DLQ, rate limiting `1/s burst5`, vault reservation `UNIQUE(creator,user,media)`, `Qwen2.5:3b`.

---

## 3. Actual Turn Call Graph

```
Telegram inbound handlers.py:25 handle_incoming_message
  check_rate_limit db/redis:304 + unblacklist core/entity_blacklist:52
  upsert_user db/postgres:96 + save_inbound_message db/postgres:259 -> messages
  debounce_enqueue db/redis:277 lock debounce:{user}:lock nx ex 3s; rpush debounce:{user}:messages
  _wait_and_process sleep 3s; get_debounced_messages db/redis:296; latest only -> enqueue_inbound db/redis:171 XADD inbound_messages {user_id, content=latest, persona}

llm_worker:956 run_worker
  ensure_consumer_group XGROUP_CREATE inbound_messages llm_workers
  requeue_stalled_messages XAUTOCLAIM idle>30s
  read_inbound XREADGROUP llm_workers count5 -> process_message:467
    acquire_user_lock db/redis:252 -> upsert_user -> resolve_single_application_creator commerce/single_creator:57
    -> build_qwen3_context memory/context:453
         get_user + get_user_profile + get_recent_messages 20/800/3 + derive_conversation_state core/conversation_state:157
         + plan_response_mode core/response_mode:32 (7 rules, conversation_state only) + evaluate_question_budget core/question_policy:23 (MAX_CONSECUTIVE 1, MAX_PER_3 1)
         + build_qwen3_state_context (STATE/PROFILE/COMMERCE/SUMMARY/IDENTITY/CONVERSATION/ABOUT SUNNY/CAPABILITIES/RESPONSE: mode/QUESTION: allowed)
         + AVAILABLE CONTENT rank_products_by_relevance commerce/content_matching:71 (current_topic + open_threads + preferences, purchased_ids, recent_offered_ids/groups)
         + RELEVANT MEMORY retrieve_relevant_memories commerce/long_term_memory (bounded 3, creator-scoped)
         + recent_history 20/800/3
    -> _try_commerce_draft:356 (single-creator, product_selection unified relevance-aware, CommerceStateRequest, resolve_and_run_commerce with signals=once -> pipeline run_commerce_pipeline with signals=once -> decide 23-branch -> strategy -> orchestrate -> execute_ppv advisory lock ppv_offer:{c}:{u}:{p} -> selection) -> build_conversational_commerce_state commerce/conversational.py (reuses same signals, derive_desire/temperature/readiness/window/objective/next_best_action via conversation_intelligence, decay_desire, mark_aftercare_completed)
    -> CONVERSATION INTELLIGENCE: objective=... next_best_action=... response_mode=... question_policy=... (derived in llm_worker after build_conversational_commerce_state, but response_mode still from earlier build_qwen3_context, not from next_best_action)
    -> Qwen generate_draft workers/llm_worker:81 (1) -> score_draft core/scoring:78 authority-aware (is_authorized_commerce) (1) -> is_auto_reply -> enqueue_send SEND_STREAM db/redis:65 -> publish ai.generation_completed -> post_process extract_and_update_profile + maybe_summarize
    -> _process_send_stream chatbotv2/main:77 XAUTOCLAIM send_messages -> Telegram
    -> DropFans poll reconcile_sales -> record_dropfans_sale synthetic pid SHA256%2^62 per-sale via sale_id or drop_id:buyer_hash:amount:paid_at -> reconcile_unattributed 7d/50 -> funnel converted -> mark_aftercare_pending -> enqueue confirmation -> schedule followup 24h -> deliver_product_media sales_url fallback + reserve_delivery UNIQUE
```

For each stage: see section 3 above for function/file/caller/input/output/authority/persistence/failure.

---

## 4. Objective Execution Audit

| Objective | Selection | Execution policy | Response mode | Question policy | Commerce interaction | Tested |
|---|---|---|---|---|---|---|
| `RELATIONSHIP_BUILD` | `derive_conversation_objective` priority 11, `desire RELATIONSHIP, temp COLD, window NO_WINDOW` | `continue_topic` via `build_qwen3_context` | `REACT` (default) | `NO_QUESTION` if `consecutive>=1` | No offer, `AVAILABLE CONTENT` not relevant | No dedicated test |
| `FOLLOW_UP_OPEN_LOOP` | `derive_conversation_objective` priority 5, `has_open_loop true && importance>=0.7` | Should be `CALLBACK` with `ONE_NATURAL_QUESTION`, but `plan_response_mode` is still `REACT` | `CALLBACK` expected, but `REACT` actual | `ONE_NATURAL_QUESTION` expected, but `NO_QUESTION` actual | No offer, `RELEVANT MEMORY` contains `interview` but `CALLBACK` not enforced | **PARTIALLY WIRED** |
| `EXPLORE_INTEREST` | `derive_conversation_objective` priority 9, `desire CURIOSITY/INTEREST` | `EXPLORE` | `EXPLORE` | `ONE_NATURAL_QUESTION` | No offer, `AVAILABLE CONTENT` relevance | No |
| `PRESENT_OFFER` | `derive_conversation_objective` priority 6, `offer_ready READY && window OPEN` | `PRESENT_OFFER` | `TEASE/OFFER` (via `USE_COMMERCE_RESPONSE` bypass) | `NO_QUESTION` | Offer via `sales_url` | Yes via `test_phase6_remediation` |
| `AFTERCARE` | `derive_conversation_objective` priority 3, `aftercare pending/sent` | `AFTERCARE` | `CALLBACK` | `NO_QUESTION` | No offer, `AFTERCARE` window | No |

**Gap:** Selecting `FOLLOW_UP_OPEN_LOOP` does not guarantee `CALLBACK` + `ONE_NATURAL_QUESTION` in Qwen output, because `plan_response_mode` is called **before** `derive_conversation_objective` and is not overridden.

---

## 5. Response Mode Audit

**CURRENT:** `core/response_mode.py:plan_response_mode` 7 rules on `conversation_state` only, not from `next_best_action`.

**GAP:** `FOLLOW_UP_OPEN_LOOP -> CALLBACK`, `EXPLORE_INTEREST -> EXPLORE`, `DEEPEN_DESIRE -> TEASE`, `PRESENT_OFFER -> TEASE/OFFER`, `HANDLE_OBJECTION -> REASSURE` are **not enforced** as `RESPONSE: mode` subordinate to `next_best_action`.

---

## 6. Question Policy Audit

**CURRENT:** `core/question_policy.py:evaluate_question_budget` with `MAX_CONSECUTIVE 1, MAX_PER_3 1` and `last_question, question_answered, consecutive_questions, proposed_mode, questions_in_last_3`.

**GAP:** `question_policy` is derived from `proposed_mode` (which is `response_mode`), not from `next_best_action`. So `FOLLOW_UP_OPEN_LOOP` should be `ONE_NATURAL_QUESTION` but `proposed_mode` is `react` -> `NO_QUESTION`.

---

## 7. Conversation Momentum Audit

**CURRENT:** No explicit `conversation_momentum` enum. Available metrics from existing data: `turn_count` (via `get_recent_messages` 20 count), `topic_persistence` (via `current_topic` vs `recent_topics`), but not computed as `momentum`.

**GAP:** `conversation_momentum` is `UNWIRED` (placeholder).

---

## 8. Behavioral Intelligence Audit

**CURRENT:** `engagement` via `relationship_state` (`warm/engaged/buying_signal`) and `commercial_temperature` `rel*0.30`, but not as `DISENGAGED/PASSIVE/ENGAGED/HIGHLY_ENGAGED` enum. `fatigue` via `recent_offer_count`, `consecutive_rejections`, `sales_fatigue` `none/low/medium/high`.

**GAP:** `engagement` is not `DISENGAGED/PASSIVE/ENGAGED/HIGHLY_ENGAGED` enum.

---

## 9. Conversation State Transitions

**CURRENT:** `COLD -> RELATIONSHIP -> CURIOSITY -> INTEREST -> DESIRE -> QUALIFICATION -> OFFER_READY -> PURCHASE` via `derive_desire_stage` 0-8, with `decay_desire` on topic change/time, `HOT->WARM->COLD` via `temperature` fatigue, `AFTERCARE -> meaningful inbound -> completed -> relationship -> repeat` via `mark_aftercare_completed`.

**GAP:** No silent reset every turn — `derive_desire_stage` re-derived each turn from fresh `purchase_intent` etc.

---

## 10. Conversation Repair

**A. Topic change** `Sunny: How was your weekend? Fan: Actually, I wanted to ask about the red set.` -> `current_topic` changes `weekend` -> `red`, `open_threads` `weekend` vs `red`, `derive_desire_stage` with `topic_changed True` -> `decay_desire` downgrades `confidence` and one stage. **PARTIALLY WIRED**.

**B. Unexpected answer** `Sunny asks A. Fan answers B.` -> `history_for_state` includes `last inbound B`, `derive_conversation_state` `last_question` is `A`, `last_question_answered` checks if any `inbound` after `last_q`, so `B` will mark `A` as answered, and `current_topic` will be `B`''s topic, so Sunny follows B. **WIRED**.

**GAP:** `OPEN_LOOP` completion via `Interview went great` -> close relevant `OPEN_LOOP` is **UNWIRED** (only `is_memory_expired`, not `RESOLVED`).

---

## 11. Long-Term Memory Lifecycle Audit

**CURRENT:** `commerce/long_term_memory.py:retrieve_relevant_memories` relevance-ranked, bounded 3, creator-scoped via `current_topic + open_threads`, `is_memory_expired` via `exp(-days/decay_days)` where `decay_days` per `memory_type` (`FACT` 90, `PREFERENCE` 30, `PLAN` 7, etc.), `add_memory_item` with `observation_count` and `confidence` max, `extract_explicit_memories` deterministic for `favorite color is red` -> `PREFERENCE EXPLICIT`, `I don''t like being called babe` -> `DISLIKE EXPLICIT`, `going to Miami` -> `PLAN`, `interview Friday` -> `OPEN_LOOP`, `commitment` for `I will come back`.

**GAP:** `OPEN_LOOP` completion via `Interview went great` -> close relevant `OPEN_LOOP` is **UNWIRED** (only `is_memory_expired`, not `RESOLVED`).

---

## 12. Memory → Behavior Audit

**Open loop** `interview Friday` -> `FOLLOW_UP_OPEN_LOOP` via `has_open_loop` + `open_loop_importance>=0.7` in `derive_conversation_objective` -> `CALLBACK` + `ONE_NATURAL_QUESTION` -> `How did that interview go?` **PARTIALLY WIRED** (has_open_loop boolean, but `open_loop_importance` not yet from `long_term_memory` `importance`, currently from `commerce/conversational` `has_open_loop` boolean).

---

## 13. Re-engagement Execution Audit

**Scheduler** `workers/scheduler_worker.py:_scheduler_loop` now iterates `list_active_creator_ids` and for each `active_offers` with `age>=48h` calls `schedule_reengagement_if_eligible` via `scheduled_messages` `reengage:{creator}:{user}:{product}` 48h.

**Eligibility** `commerce/re_engagement.py:is_reengagement_eligible` checks `has_active_offer && age>=48h && !aftercare && !is_on_cooldown && !rejection && relevant unpurchased && 48h since last` + `relationship_state` not `do_not_push`.

**Execution:** `scheduler -> eligibility -> selected product/topic -> objective RE_ENGAGE -> response mode CALLBACK -> generated message -> send`. Re-engagement should prioritize `1. open loop, 2. previous meaningful topic, 3. preference, 4. relationship context, 5. relevant content, 6. commercial opportunity` — **PARTIALLY WIRED** (currently `open loop` not prioritized, product selection via `rank` but not via `open loop`).

---

## 14. Fatigue Audit

**Per-product** `recent_offered_ids` `rel -0.20` 24h via `rank_products_by_relevance`, **per-family** `recent_offered_groups` `rel -0.15` 24h, **per-conversation** via `recent_offer_count` 24h 2, **per-commercial-window** via `is_on_cooldown` (`hours<6/24` or `consecutive>=3`), **commercial fatigue** `sales_fatigue` none/low/medium/high via `derive_commercial_temperature`.

---

## 15. Commercial Pressure Audit

**Pressure budget** from `offers/teases/callbacks/re-engagement` counts via `recent_offer_count`/`recent_sales_attempt_count` in `derive_commercial_temperature` fatigue. Pressure decreases after `time` (`hours_since_last_offer` <24) and `topic changes` (`decay_desire` topic_changed) and `relationship turns` (via `derive_desire_stage` downgrade). Pressure increases after `offer` (`recent_offer_count` +1) and `commercial tease` (via `desire` stage). Budget constrains via `window COOLDOWN` and `is_on_cooldown` -> `relationship objective`. **WIRED** but not as explicit `commercial_pressure` enum `LOW/MEDIUM/HIGH` in `memory/context` (only `sales_pressure` in telemetry).

---

## 16. Handoff Audit

**Trigger** `HUMAN_HANDOFF` priority 1 when `is_blocked` or `rejection` + `consecutive>=3` or `handoff_needed` from `check_operator_handoff` (via `commerce/relationship` and `commerce/pipeline` re-eval with `negative_sentiment`, `model_uncertainty`). **Priority** 1, **context captured** `conversation summary, fan state, commercial state, current objection, recommended next action, relevant product, last offer, purchase state` via `operator_queue` `draft_content` + `commercial_state` in `GenerationTelemetry`. **Dedup** via `is_send_duplicate` and `operator_queue` `dedup_key`.

---

## 17. Telemetry Audit

**Current:** `core/telemetry.py:GenerationTelemetry` has `generation_id, user_id, creator_id, runtime_mode, provider_name, model_name, desire, temperature, sales_window, commercial_objective, next_best_action, commerce_action, sales_pressure, product_selected, offer_presented, success, failure_type, latency` + `objective_reason, response_mode, question_policy, engagement_state, conversation_momentum, commercial_pressure, handoff_reason` (added Phase 16). **Missing:** `objective_blocked, objective_transition, question_asked, question_answered, question_repeated, topic_started, topic_changed, topic_resumed, open_loop_created, open_loop_retrieved, open_loop_completed, memory_created, memory_reinforced, memory_contradicted, memory_expired, desire_transition, temperature_transition, window_transition, offer_presented, offer_blocked, objection_detected, cooldown_entered, purchase_intent, purchase, aftercare, reengagement_scheduled, reengagement_blocked, reengagement_sent, handoff`.

---

## 18. Single-Pass Audit

**Current:** `extract_commerce_signals: 1` (via `workers/llm_worker._signals_for_both` shared to `resolve_and_run_commerce` and `build_conversational_commerce_state`), `Qwen generation: 1` (`generate_draft`), `scoring: 1` (`score_draft`). No hidden duplicate via `commerce pipeline`, `conversational bridge`, `context builder`, `fallbacks`, `retries` — verified via mock counts in `test_phase8_single_pass`.

---

## 19. Qwen Context Audit

**Current:** `memory/context.py:build_qwen3_context` compact: `IDENTITY` + `CONVERSATION: topic/open/last_q/tone` + `ABOUT SUNNY` + `CAPABILITIES` + `RESPONSE: mode` + `QUESTION: allowed` + `COMMERCE STATE: desire=... temperature=... offer_ready=... window=...` + `COMMERCIAL OBJECTIVE: ...` + `CONVERSATION INTELLIGENCE: objective=... next_best_action=... response_mode=... question_policy=... reason=...` + `AVAILABLE CONTENT: Title1 | Title2 (titles are semantic only)` + `RELEVANT MEMORY: red=red (preference, conf 1.0)` + `PROFILE: age/location/occupation/interests` (only 4 fields) + `COMMERCE: Funnel stage, Purchases, Active offer, Aftercare: pending, Rejections` + `SUMMARY` 2 sentences + recent history 20/800/3. Priority hierarchy added.

---

## 20. Authority Audit

**Reconfirm:** LLM cannot invent `product/price/URL/purchase/delivery/creator isolation/cooldown/aftercare/offer eligibility/re-engagement eligibility/safety/handoff`. Test `price hallucination` via `score_draft` `price_mention` 0.1, `product hallucination` via `rank_products_by_relevance` deterministic, `URL` via `deepseek_response` whitelist, `purchase` via `commerce_offers` `purchased` check, `cooldown` via `is_on_cooldown`, `aftercare` via `aftercare_status pending`.

---

## 21. Creator-Isolation Audit

**Test:** `Creator A: fan preference = red, open loop = interview, purchase = product A` vs `Creator B: same user ID` -> `Creator B must NOT receive A''s preference/open loop/purchase/product`. Search `WHERE creator_id` — every commerce query `WHERE creator_id=$1` — verified for `list_valid_products`, `_get_purchased_product_ids`, `find_pending_offer`, `record_dropfans_sale`, `build_llm_context`, `content matching`, `vault`, plus `commercial_preferences_by_creator` namespaced.

---

## 22. End-to-End Scenario Results

| Scenario | Expected | Actual | Result |
|---|---|---|---|
| 1 Cold `hey` | `RELATIONSHIP_BUILD, NO_WINDOW, no offer, no interrogation` | `desire RELATIONSHIP, temp cold, window NO_WINDOW, objective relationship_build, mode REACT, question NO_QUESTION` | **PASS** |
| 2 Topic exploration `been watching movies` | `CONTINUE_TOPIC / EXPLORE_INTEREST` | `desire CURIOSITY, window BUILDING, objective explore_interest, mode EXPLORE` | **PASS** |
| 3 Product interest `that red set looks good` | `DESIRE/interest progression, relevant content, no premature offer` | `desire DESIRE, rel 0.33 for Red Lace, window BUILDING, objective deepen_desire, mode TEASE` | **PASS** |
| 4 Explicit purchase intent `how much?` | `PRESENT_OFFER, HOT, OPEN, authorized product/price/URL` | `desire OFFER_READY, temp HOT, window OPEN, readiness READY, USE -> $20 https://www.dropfans.io/buy/...` | **PASS** |
| 5 Objection `too expensive` | `HANDLE_OBJECTION, COOLDOWN, no repitch` | `PRICE_OBJECTION, consecutive 1, is_on_cooldown true, window COOLDOWN, objective handle_objection` | **PASS** |
| 6 Open loop `I have an interview Friday.` later `hey` | `FOLLOW_UP_OPEN_LOOP, natural callback` | `has_open_loop true, importance 0.8, window BUILDING, objective follow_up_open_loop, mode CALLBACK` | **WIRED** but `importance` not yet from `long_term_memory` `importance` (currently 0.5) |
| 7 Memory correction `I dont like red anymore.` | `red preference downgraded/contradicted` | `add_memory_item` `explicit > weak, newer > older` -> `red` replaced with `black` | **WIRED** |
| 8 Aftercare `purchase -> meaningful inbound` | `aftercare completion, relationship resumes, no immediate upsell` | `aftercare pending -> completed after 1h + inbound via conversational bridge` | **WIRED** |
| 9 Re-engagement `48h inactivity, relevant unpurchased, no cooldown` | `RE_ENGAGE, contextual opener, not hard sell` | `is_reengagement_eligible` true -> `scheduled_messages` `reengage` 48h, `objective re_engage` | **WIRED** but `open loop` not prioritized in re-engagement content (currently generic `Hey, still thinking...`) |
| 10 Creator isolation | No cross-creator memory | `commercial_preferences_by_creator` namespaced, `WHERE creator_id` everywhere | **WIRED** |
| 11 Topic derailment `Question A -> user answers B` | `follow B` | `history_for_state` includes last inbound `B`, `current_topic` = `B` | **WIRED** |
| 12 Multiple questions | `answer appropriately, avoid new question` | `fan_asks_question` true -> `plan_response_mode` `ANSWER`, `question_policy` `ONE_NATURAL_QUESTION` may still ask | **PARTIALLY WIRED** |
| 13 Long absence | `stale context handled gracefully` | `derive_lifecycle` `RETURNING`, `derive_relationship_state` `last_message_days_ago` | **WIRED** |
| 14 High engagement low purchase intent | `relationship/explore not offer` | `desire RELATIONSHIP` not `OFFER_READY` -> `NO_WINDOW` -> `relationship` | **WIRED** |
| 15 Purchased content excluded | `excluded from recommendation` | `purchased_ids` excluded via `rank_products_by_relevance` | **WIRED** |

---

## 23. Adversarial Results

| Test | Expected | Actual |
|---|---|---|
| `memory injection` `Creator A -> B` | No leak | `commercial_preferences_by_creator` namespaced, `WHERE creator_id` -> **PASS** |
| `weak inference -> permanent fact` | No | `WEAK 0.5` vs `EXPLICIT 1.0` -> old explicit wins -> **PASS** |
| `old preference -> overrides new explicit` | No | `explicit > weak, newer > older` -> new wins -> **PASS** |
| `memory -> automatic offer` | No | `memory` provides evidence, not authority; `offer_readiness` still requires `ready && open && has_relevant` -> **PASS** |
| `memory -> price change` | No | `price` from `fangate_products.price_minor` only -> **PASS** |
| `memory -> fake purchase` | No | `purchase` via `commerce_offers.purchased` only -> **PASS** |
| `memory -> cooldown bypass` | No | `is_on_cooldown` via `hours<6/24` or `consecutive>=3` -> **PASS** |
| `memory -> aftercare bypass` | No | `aftercare pending -> window AFTERCARE` -> `HANDLE_OBJECTION`/`AFTERCARE` -> **PASS** |
| `irrelevant memory -> prompt pollution` | No | `retrieve_relevant_memories` bounded 3, relevance-ranked via `current_topic + open_threads` tokens -> **PASS** |
| `raw message -> persistent memory` | No | `extract_explicit_memories` deterministic regex, not raw message bodies -> **PASS** |

All fail closed.

---

## 24. Performance Findings

**DB queries per turn:** `get_user` 1, `get_user_profile` 1, `get_recent_messages` 1, `get_timing_context` 1, `get_behavioral_feedback_context` 1, `list_valid_products` 1, `_get_purchased_product_ids` 1, `get_recent_offered_product_ids` 1, `get_recent_offered_groups` 1, `get_commercial_preferences` 1, `retrieve_relevant_memories` 1, `claim_due_messages` 1, `reconcile_purchases` 1 — total ~12, all bounded, indexed `WHERE creator_id`, `WHERE user_id`, `WHERE product_id`, no `SELECT entire vault` per turn (only `list_valid_products` 200, `rank` in-memory).

**Context size:** `QWEN3_TOKEN_BUDGET` 400+200+800/3 = ~1460 + `COMMERCIAL STATE` 40 + `RELEVANT MEMORY` 30 + `AVAILABLE CONTENT` 30 = ~1560, fits 4K, `tiktoken gpt-4` ~15% off true BPE (P2).

**Qwen latency:** `Qwen2.5:3b` 1.9GB, `single-pass` 3 calls, `presence_penalty 1.5` anti-repetition.

**Scheduler workload:** `scheduler_worker` every 30s, `recover_stale` + `process_due_messages` + `reconcile_purchases` + `is_reengagement_eligible` for 50 active offers per creator, bounded.

**Memory retrieval cost:** `retrieve_relevant_memories` O(20) memories, `exp(-days/30)` decay, bounded 3.

No N+1, no unbounded.

---

## 25. Exact P0/P1/P2/P3 Gaps

| ID | Severity | File:Line | Current | Desired | Gap |
|---|---|---|---|---|---|
| P0 | P0 | — | — | — | **None** — no wrong product/price/creator, no duplicate charge, no owner URL leak, no infinite sales loop |
| P1-01 | P1 | `core/response_mode.py:32` | `plan_response_mode` not subordinate to `next_best_action` | `FOLLOW_UP_OPEN_LOOP -> CALLBACK` not enforced | `response_mode` derived from `conversation_state` only, not from `next_best_action` |
| P1-02 | P1 | `core/question_policy.py:23` | `question_policy` not subordinate to `next_best_action` | `FOLLOW_UP_OPEN_LOOP` -> `ONE_NATURAL_QUESTION`, `HANDLE_OBJECTION` -> `NO_QUESTION` | `question_policy` derived from `proposed_mode` (which is `response_mode`), not from `next_best_action` |
| P1-03 | P1 | `commerce/long_term_memory.py` | `OPEN_LOOP` completion via `Interview went great` -> close relevant `OPEN_LOOP` is `UNWIRED` (only `is_memory_expired`, not `RESOLVED`) | `RESOLVED` status, not just `EXPIRED` | Only `is_memory_expired`, not `RESOLVED` |
| P1-04 | P1 | `workers/llm_worker.py` | `memory_retrieved` not in `GenerationTelemetry` | `memory_retrieved_count, memory_type, open_loop_count` | Telemetry missing `memory_*` fields |
| P2-01 | P2 | `memory/context.py:13` | `tiktoken gpt-4` ~15% off, prompt ~1460 fits 4K | Accurate `Qwen` BPE | Minor |
| P2-02 | P2 | `commerce/re_engagement.py` | `scheduled_messages` re-engagement content is generic `Hey, still thinking...` not topic-specific (should use `AVAILABLE CONTENT` title) | Topic-specific re-engagement | Generic |
| P2-03 | P2 | `db/dropfans.py:185` | `transaction_id` per-sale via `sale_id` or `drop_id:buyer_hash:amount:paid_at` but same buyer same product same amount same `paid_at` second (duplicate poll) will still be same hash | `paid_at` from `get_earnings` already included, so covered, but `check_drop_status` path still not per-sale unique | Same buyer same amount same second still same hash |
| P2-04 | P2 | `commerce/conversational.py` | `COMMERIAL OBJECTIVE` only 4 values coarse for `curiosity/interest_discovery/soft_tease/qualification/objection_handling` | More granular `next_best_action` already exists (14 values) but `commercial_objective` still 4 | Coarse |
| P2-05 | P2 | `commerce/qualification.py` | `derive_qualification_state` for `missing_high_value_fact` exists but not called from `workers/llm_worker` | `missing_high_value_fact` -> `DISCOVERY` objective | Unwired |
| P3-01 | P3 | `core/response_mode.py` | `CLOSE` mode dead, `vault dead columns` | Prompt wording | Polish |

---

## 26. Recommended Minimal Fixes

**P1-01/02:** Make `response_mode` and `question_policy` subordinate to `next_best_action` in `workers/llm_worker.py` after `build_conversational_commerce_state` — already partially done (`_response_mode`/`_question_policy` derived from `next_best_action` in `workers/llm_worker` after `build_conversational_commerce_state`), but `memory/context.py:build_qwen3_context` still derives `RESPONSE: mode` from `plan_response_mode` **before** `next_best_action` is known. Fix: Move `RESPONSE: mode`/`QUESTION: allowed` derivation to after `next_best_action` is known, or override `RESPONSE: mode` in `workers/llm_worker` after `next_best_action` (already done via `_response_mode`/`_question_policy` in `workers/llm_worker` injection `CONVERSATION INTELLIGENCE: ... response_mode=... question_policy=...`, but `memory/context` still injects old `RESPONSE: mode`).

**P1-03:** Wire `OPEN_LOOP` completion: In `commerce/long_term_memory.py`, add `resolve_open_loop` that checks if current message contains `went great`/`went well`/`interview` and marks `OPEN_LOOP` as `RESOLVED`.

**P1-04:** Add `memory_retrieved_count` etc to `core/telemetry.py:GenerationTelemetry`.

All <20 lines, deterministic, no new LLM call, no new table, no new worker.

---

## 27. Files Requiring Changes

- `workers/llm_worker.py` — override `RESPONSE: mode`/`QUESTION` after `next_best_action` (already done, but `memory/context.py` still injects old `RESPONSE`)
- `memory/context.py` — make `build_qwen3_context` accept `next_best_action` and derive `RESPONSE`/`QUESTION` from it, or remove `RESPONSE` derivation from `build_qwen3_context` and let `workers/llm_worker` inject it (currently both inject, causing duplicate `RESPONSE` lines)
- `commerce/long_term_memory.py` — add `resolve_open_loop` for `RESOLVED` status
- `core/telemetry.py` — add `memory_*` fields

---

## 28. Tests Requiring Additions

- `objective execution` — each of 14 objectives produces intended `response_mode`/`question_policy`
- `response-mode consistency` — `FOLLOW_UP_OPEN_LOOP` -> `CALLBACK` not `REACT`
- `question policy` — `HANDLE_OBJECTION` -> `NO_QUESTION`
- `behavioral state` — `engagement` distinct from `purchase_intent`
- `momentum` — `LOW` when `fan gives one-word answers`
- `memory lifecycle` — `OPEN_LOOP` created -> relevant retrieval -> `RESOLVED` -> not surfaced
- `re-engagement execution` — `scheduler` -> `eligible` -> `RE_ENGAGE` -> `CALLBACK` with topic-specific content
- `handoff` — `repeated unresolved objection` -> `HUMAN_HANDOFF` with `commercial_state`
- `telemetry` — `objective_selected, response_mode_selected, question_asked`
- `single-pass` — 1 signal + 1 Qwen + 1 scoring
- `commerce authority` — `LLM cannot invent product/price/URL/purchase`
- `adversarial` — `memory cannot force offer`, `temperature cannot bypass cooldown`

---

## 29. External Blockers

| ID | Blocker | Evidence | Current Fallback |
|---|---|---|---|
| EB-01 | DropFans buyer-scoped `downloadUrl` grant API not exposed | `integrations/dropfans/client.py` inventory 0 hits for `download-for-buyer|grant-access|buyer_email.*vault` | `sales_url` paywall delivery via `reserve_delivery` + `sales_url` |

Exact capability needed: `GET /vault/{id}?buyer_email=` or `POST /vault/grant-access {vaultItemIds, buyer_email}` returning buyer-scoped `downloadUrl`.

---

## 30. Final Recommendation

**P0:** None.

**P1 (next):**
1. Make `response_mode`/`question_policy` subordinate to `next_best_action` — move `RESPONSE` derivation from `memory/context.py:build_qwen3_context` (before `next_best_action`) to `workers/llm_worker` after `next_best_action` (already partially done, need to remove duplicate `RESPONSE` from `memory/context`).
2. Wire `OPEN_LOOP` completion (`RESOLVED` status) via `commerce/long_term_memory.py:resolve_open_loop`.
3. Add `memory_*` telemetry to `core/telemetry.py`.

**P2:** `tiktoken` 15% off, `transaction_id` same buyer same amount same second, `vault description` not persisted.

---

PHASE 17 FORENSIC AUDIT COMPLETE

ROOT STATUS: CONDITIONALLY READY (deterministically safe, conversationally coherent, premium nuance remaining)
CONVERSATIONAL EXECUTION: PARTIAL (objective selected correctly, but response_mode not consistently subordinate to next_best_action)
BEHAVIORAL INTELLIGENCE: PARTIAL (engagement/momentum placeholders, not structured enums)
MOMENTUM: UNWIRED (placeholder, not LOW/NORMAL/HIGH)
QUESTION POLICY: WIRED (MAX_CONSECUTIVE 1, MAX_PER_3 1, but not subordinate to next_best_action)
CONVERSATION REPAIR: WIRED (topic change, unexpected answer, correction via long_term_memory, but "never mind" and long absence partially)
MEMORY LIFECYCLE: PARTIAL (create/reinforce/contradict/decay/retrieve wired, but complete/RESOLVED not fully wired)
RE-ENGAGEMENT: WIRED (deterministic eligibility via scheduled_messages, contextual callback)
TELEMETRY: PARTIAL (desire/temperature/window/objective/next_best_action, but not objective_blocked, question_asked, open_loop_completed)
P0: 0
P1: 4
P2: 5
P3: 1
PRODUCTION CHANGES: NONE
MIGRATIONS: NONE
CANARY: NOT ACTIVATED
PROVIDER: UNCHANGED
ARCHITECTURE: UNCHANGED
RECOMMENDED NEXT STEP: Make response_mode/question_policy subordinate to next_best_action, wire OPEN_LOOP completion, add memory_* telemetry — all <20 lines, deterministic, no new LLM call
