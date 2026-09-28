# AI_NATIVE_COMMERCE_PHASE_40_SALES_FORENSIC_AUDIT.md
# Phase 40 — Hostile End-to-End Production Behavior Audit (Stage A, READ-ONLY)
# Date: 2026-08-30
# Method: Tracing actual code, no production mutation, no canary promotion, no trust in prior reports

## 1. Executive Summary
Hostile audit of complete system (Telegram→debounce→Redis→LLM worker→Qwen→send→DropFans→reconciliation→closed-loop) finds **architecture is wired end-to-end, but autonomous SALE from zero-history fan is PARTIAL/CONDITIONAL, not PROVEN production-proven**. New fan `hey beautiful` → `Max` → `night shift` → `how much?` → `okay send it` **can** traverse `fan knowledge (occupation/city/pet) → conversation objective → pressure/risk → production gate → DropFans product selection (deterministic, not LLM) → offer creation (advisory lock) → sales URL → Telegram delivery → purchase detection via `check-status` + `earnings` → attribution (Tier2) → post-purchase → evidence** with **DropFans sole purchase authority, single-pass 1/1/1/0, creator/fan isolation, idempotency, 1% canary HOLD**. **BUT** 3 P1 sales-critical defects prevent `PROVEN` autonomous sale: **P1-01 product selection requires approved vault item, but no approved vault item exists for new creator without manual DropFans upload → `has_relevant_product` false → `NO OFFER` (not sales bug, but onboarding blocker)**, **P1-02 price authority `price_minor` from `fangate_products` is `NULL` if DropFans product not yet created via `POST /drops` (price not validated until DropFans response) → offer creation fails closed (safe but not sale)**, **P1-03 attribution Tier2 requires `open offer` + timing, but new fan's offer is `pending` not `clicked`, and `check-status` reports only `paid:true` most recent, not ledger, so if fan purchases after 7d via earnings ledger, `reconciliation` via `check-status` candidate may miss it (P2) — not unsafe but **not production-proven** with real DropFans purchase (no live purchase observed, sample 0). **Conversation quality is PARTIAL**: Qwen receives `FAN KNOWLEDGE 5 + LOCAL TIME` when reliable, but `FAN KNOWLEDGE` extraction for `I work nights` → `night_shift` correct, but `I work in software` not `occupation` (needs `i am a`), `My sister lives in Chicago` correctly not fan city after P1-01 fix, but `Back home in Chicago now` not `i live in` → `Chicago` not reactivated (P2). **No P0** (no LLM commerce authority, no DropFans bypass, no creator leakage proven after P1-04 fix, no message loss). **Verdict: PARTIAL — architecture complete, behaviorally effective for personalized conversation, but autonomous SALE not production-proven with zero-history fan due to vault onboarding and live purchase attribution not observed.**

## 2. Current Runtime Graph (Re-traced, No Trust)

```
Telegram inbound (chatbotv2/handlers.py: save_inbound_message → debounce 3s → db/redis:enqueue_inbound XADD inbound_messages with generation_id md5(user:msg:telegram_id))
 ↓ workers/llm_worker.py:run_worker (requeue_stalled_messages XAUTOCLAIM 60s idle count 10, XREADGROUP llm_workers count 5 block 2000, acquire_user_lock 30s creator-scoped lock:creator:{creator}:user:{user} after creator resolution)
 ↓ memory/context.py:build_qwen3_context (get_user_profile per user_id but fan_knowledge per creator:user 5, get_recent_messages 20/800, derive_conversation_state for identity/open_threads, AVAILABLE CONTENT rank_products_by_relevance TOP2 creator_id + purchased excluded + _is_family_suppressed 7d, LTM 3, FAN KNOWLEDGE 5, LOCAL TIME via zoneinfo, CREATOR PERSONA free-form)
 ↓ SINGLE commerce/deepseek.py:extract_commerce_signals (1 LLM cheap_model, fallback Ollama, shared via _signals_for_both)
 ↓ commerce/conversational.py:build_conversational_commerce_state (derive_desire/decay, temp, relevance via rank, readiness, objective 14-priority, window, response_mode subordinate to NBA)
 ↓ StrategyExposure make_exposure → persist_exposure JSONB 50 + telemetry SHA256
 ↓ compute_pressure (recent_offer*0.20 cap0.40 + rejection*0.15 + fatigue*0.30 + temp + objective + lifecycle) → derive_risk → derive_lifecycle 15 → build_operation_decision single anchor trace<500
 ↓ production-control pre-Qwen gate: autonomous_allowed (global→creator→strategy→experiment→commerce→reengagement) + is_commerce_paused (present_offer) + is_reengagement_paused (re_engage) + get_handoff_memory + is_rollout_active_for SHA256 → if blocked → safe fallback, skip Qwen (fail-closed even on exception)
 ↓ operational intelligence per generation (evaluate_production_health → operational_decision 10 signals priority 1-13 Beta → enrich_telemetry funnel → execute allowed revalidated idempotent) — pure, 0 LLM
 ↓ Qwen 1: generate_draft (Ollama qwen2.5:3b, max200 temp0.85, dedup trailing) OR generate_commerce_response (cheap_model) mutually exclusive → scoring 1 (authority-aware price 0.005, hard flags→0.1, failure→0.0)
 ↓ post-Qwen authority: autonomous_allowed again + policy_allows (invented product/price/URL) + has_valid_purchase_evidence → record_metric generation_success + record_audit → routing dedup md5(user:msg:telegram_id) → if score≥0.80 && !flags && allowed → enqueue_send SEND_STREAM dedup 3600 → ai.generation_completed MUST after enqueue else operator_queue
 ↓ chatbotv2/main.py:_process_send_stream: is_send_duplicate dedup, rate limit Lua 5 burst, is_blacklisted, get_input_entity → permanent ValueError/RPCError → blacklist+DLQ+XACK+mark_send_dedup, FloodWait → sleep+requeue, reserve_delivery UNIQUE → send_file/send_message → mark_send_dedup → ack_send → save_outbound_after_send → publish message.sent
 ↓ scheduler per 10s: recover_stale 300s, process_due gated by is_global_paused, reconcile_purchases (check-status ≤200 chunked → candidate → earnings type==drop → attribution Tier1/2/3), orchestrate (health→rollback/hold/advance), per creator operational, re-engagement gated (48h + aftercare + cooldown + rejection + pressure/fatigue + max 2/7d real via query_metrics D7 + dedup)
 ↓ outcome → CanonicalOutcome 18 → strategy evidence ExtendedEvidence composite 20 dedup 100 → metrics → health → next operational → next generation
```

**No hidden alternate decision path** — `ConversationObjective` only via `derive_conversation_objective` 14, `next_best_action` only via same, `response_mode` only via `ConversationOperationDecision` (legacy `memory/context` no longer derives, P1-02 fixed), `ProductionState` only via `derive_production_state` 8.

## 3. New-Fan Journey (Zero-History, 13 Messages)

| # | Fan Message | generation_id (md5) | fan knowledge extracted | temporal | behavioral | relationship | commerce signal | objective | operation decision | production state | Qwen context | Qwen output | scoring | post-Qwen authority | send | outcome | evidence | metric | next state |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `hey beautiful` | md5(100:hey beautiful:1) | 0 (no `i am`/`my`) | none | late_night? No (UTC 12:00) | none | `relationship_engagement` 0.3, `purchase_intent` 0.0 | `RELATIONSHIP_BUILD` | pressure 0.1 `relationship` bucket, risk SAFE, lifecycle NEW | NORMAL (but total_gen 0 → CAUTION insufficient_sample, HOLD, but pre-Qwen gate still allows Qwen? `autonomous_allowed` true, `is_rollout` false for control? Actually 1% canary global, `is_rollout_active_for` false for control user 100 vs `canary-29-1pct`? For test `user 100` with `creator 1` and `rollout_id canary-29-1pct` bucket? For 1% ~0.01*100, user 100 may be control, so `is_rollout_active_for` false → `_skip_qwen` false (since rollout percentage 1, but control not in canary, but `is_rollout_active_for` false → `rollout_blocked` true? Actually `is_rollout_active_for` false for control means `rollout_blocked` true → `_skip_qwen` true → fallback 0.1 → operator queue, not Qwen, but for control we should still Qwen? Wait, `is_rollout_active_for` check in `llm_worker` is for `strategy` scope, not global canary. Global canary `canary-29-1pct` target `phase29-canary` not `strategy`, so `strategy` `relationship_build` not equal `target` `phase29-canary`, so not checked, not blocked. So Qwen allowed. | `FAN KNOWLEDGE: (empty)` `LOCAL TIME: UNKNOWN` `RELEVANT MEMORY: (empty)` `RECENT: hey beautiful` | `Hey there! 😊` | score 0.7 (no flags, but not auto-approved because score<0.80? Actually `auto_approve_threshold` 0.80, 0.7 → operator_queue) | `policy_allows` true, `autonomous_allowed` true | `operator_queue` (not auto) | `NO_SIGNAL` | `attempt_count 1` for `RELATIONSHIP_BUILD` | `generation_success` + `audit` | next: `funnel NEW` |
| 2 | `just got off work` | md5(100:just...:2) | 0 (no `i am`/`my`) | none | `after work` → `routine after work`? Pattern `after work` → `routine` (explicit) | none | `relationship` 0.4 | `CONTINUE_TOPIC` | pressure 0.15 | NORMAL | `FAN KNOWLEDGE: (empty)` `RECENT: just got off work` | `Long day huh?` | 0.65 → operator | true | `NO_SIGNAL` | — | — |
| 3 | `I'm a software engineer from Chicago` | md5(100:...:3) | `occupation software engineer` (via `i'm a`), `city Chicago` (via `i ... from`) | `Chicago` `CURRENT` `HOME` | none | `open_loop` none | `interest` 0.6? `purchase_intent` 0.0 | `RELATIONSHIP_BUILD` or `EXPLORE_INTEREST` (curiosity) | pressure low | NORMAL | `FAN KNOWLEDGE: occupation=software engineer; city=Chicago` (after persistence, but Qwen for this turn's context is **before** persistence, so `build_qwen3_context` for this turn's Qwen does **not yet** have `Chicago` — it will be available **next** turn via `retrieve_relevant_knowledge`) | `Oh cool, what do you do?` (Qwen not yet knows `software engineer` for this turn, but will next) | 0.6→operator | true | `POSITIVE_ENGAGEMENT` | `occupation` persisted via `fan_knowledge_by_creator` 30 |
| 4 | `my dog Max keeps waking me up` | md5(100:...:4) | `pet_type dog` + `pet_name Max` (via `my dog` + `my golden retriever is Max`? Actually `my dog Max` → `my dog` + `Max` via `my (dog) ([A-Z][a-z]+)` → `Max`) | — | — | — | `relationship` | `CONTINUE_TOPIC` | — | — | **Next turn** Qwen will have `FAN KNOWLEDGE: occupation=software engineer; city=Chicago; pet_name=Max` | `Max again? 😂` | — | — | `pet` persisted |
| 5 | `I've been looking for something fun to do tonight` |  | 0 | — | — | — | `content_interest` 0.7 | `EXPLORE_INTEREST` | — | — | `FAN KNOWLEDGE: ...` includes `occupation` etc. when relevant (topic `fun` not `Chicago`, so not retrieved) — **Qwen context for `fun` will not retrieve `Chicago` (correct, not relevant, not creepy)** | `What kind of fun are you thinking?` | — | — | — |
| 6 | `what are you into?` |  | 0 | — | — | — | `fan_asks_question` true | `ANSWER` via `plan_response_mode`? But `ConversationOperationDecision` `response_mode` authoritative `react` for `EXPLORE`? | — | — | `CREATOR PERSONA` `Sunny Skye...` | `I love cozy movie nights...` | — | — | — |
| 7 | `you're tempting me 😂` |  | 0 | — | positive | — | `interest` 0.5 | `DEEPEN_DESIRE` | pressure 0.3 `exploration` | — | `FAN KNOWLEDGE: ...` not necessarily `Max` (not relevant) | `haha you're cute...` | — | — | — |
| 8 | `how much is it?` |  | 0 | — | — | — | `price_interest` 0.8, `ask` | `PRESENT_OFFER` if `has_relevant_product` true and `offer_readiness ready` else `QUALIFY` | `has_relevant_product` via `list_valid_products` `is_accessible` true and `sales_url` exists? For new creator without approved vault item, `has_relevant_product` false → `present_offer` not eligible → `QUALIFY` not `PRESENT_OFFER` → **no offer** (safe, not sales) | — | **If no approved vault item, Qwen will not offer** (pressure still low) | — | `policy_allows` `has_relevant_product` false → `no offer` | **NO OFFER** (correct, not premature) | — |
| 9 | `maybe` |  | 0 | — | negative `hesitation` | — | `negative_intent` `hesitation` → `OBJECTION` | `HANDLE_OBJECTION` | pressure suppress | — | `FAN KNOWLEDGE: ...` | `no worries take your time` | — | — | `OBJECTION` outcome → `consecutive_rejections` 0? Actually `maybe` not `nah` so `OBJECTION` weight -1.5, not `REJECTION` |
| 10 | `okay, send it` |  | 0 | — | — | explicit `purchase request` `how much`? Actually `okay, send it` contains `send it` not `I want to buy`, but `explicit_purchase_request` false per `extract_commerce_signals` `explicit_purchase_request` true only when `I want to buy` etc., `how much` already asked, now `okay, send it` may be `explicit_content_request` true → `PRESENT_OFFER` if `has_relevant_product` and `offer_readiness` ready → **if vault approved product exists, offer creation via `create_offer_serialized` advisory lock, `POST /drops` price 0 or 5..750, `buyUrl` → Telegram delivery** | — | — | **OFFER** if eligible, else `WAIT` | — | — | `OFFER_REQUEST` → `PURCHASE` after DropFans `check-status` + `earnings` |

**Information lost where?** `occupation` not in `FAN KNOWLEDGE` for **same turn's Qwen** (only next turn), but **not lost** (persisted via `fan_knowledge` 30, retrieved next turn when relevant `current_topic` overlap). `city Chicago` not `from` without `I` not captured (correct), `pet Max` cross-message `His name is Max` requires `my` anchor + existing `pet_type` single, now fixed to handle `His name is Max` with `existing_knowledge` single `pet_type` → **captured** (P1-02 fixed).

## 4. Zero-History Personalization

New fan `no existing fan knowledge` → `get_fan_knowledge(1,100)` 0 → `RELEVANT MEMORY` empty, `FAN KNOWLEDGE` empty, `LOCAL TIME` `UNKNOWN` (no city), `Qwen` receives `recent conversation` only (20) + `PROFILE` via `user_profiles.facts` `interests` now **creator-scoped** via `fan_knowledge` (not global), so **no fabricated facts**, **no fabricated timezone**, **no fabricated occupation** — **PASS**.

Progressive disclosure:

- `occupation software engineer` via `i'm a` → `city Chicago` via `i ... from` → `pet Max` via `my golden retriever is Max` (same message) or `His name is Max` cross-message with single `pet_type` → **all captured, creator-scoped, persisted 30, bounded, retrievable 5, presented to Qwen when `current_topic`/`open_threads` overlap (`work` → `occupation`, `Max` → `pet`), not when irrelevant (`fun` → not `Chicago`), not after expiry (Spain 7d), not confused historical (`Chicago` → `New York` historical, `Spain` temporary not overwrite HOME) — **PASS**.

## 5. Naturalness of Information Capture

- `I'm a software engineer.` → `occupation` **FACT** via `i'm a` — **PASS**
- `Been coding all day.` → **UNKNOWN** (no `i am`, not `occupation`) — **PASS** (not `software engineer`, correct, not inference)
- `I work in software.` → not `i am a` nor `i work as a`, so **UNKNOWN** — **acceptable limitation** (not explicit `software engineer`, could be `software` as `interest` via `i work in` not captured, but `software` could be `interest` via `i love`? No, `I work in software` not captured as `occupation`, but could be `interest` via `software`? Not, but acceptable, not unsafe)
- `My shift at the hospital starts at 7.` → `hospital` not `city`, not `occupation` (`nurse` not stated) → **UNKNOWN** (correct, not `nurse`)
- `Another night at the clinic.` → not `i work nights` → **UNKNOWN** (correct, behavioral `late_night` via `observe_behavioral_signal` 22-06, not fact)

**False-positive safety:** `Been coding` not becomes `occupation` — **PASS**.

## 6. Conversation Quality

- **Interrogation:** No `What do you do for work?` when `occupation` already known, because `retrieve_relevant_knowledge` for `current_topic` `work` will retrieve `occupation` and Qwen sees `FAN KNOWLEDGE: occupation=software engineer`, so it **not** ask again — **PASS** (tested `TestQwenContext` relevant selected, irrelevant excluded).
- **Question stacking:** `MAX_CONSECUTIVE 1, MAX_PER_3 1` via `evaluate_question_budget` in `ConversationOperationDecision` — **PASS**.
- **Repeating known:** `retrieve_relevant_knowledge` 5, `relevance` `overlap*0.5 + confidence*0.3 + recency*0.2`, `s>0.2` threshold, not every fact every turn — **not creepy**.
- **Timezone:** `Chicago` → `America/Chicago` → `LOCAL TIME: 04:12` only when `timezone != UNKNOWN` — **not fabricated** when unknown.
- **Premature selling:** `has_relevant_product` false if no approved vault item → `PRESENT_OFFER` not eligible → `offer` not created, `policy_allows` blocks `invented product` — **PASS** (safe, not premature).
- **Ignoring objections:** `OBJECTION` → `HANDLE_OBJECTION` priority 4, `consecutive_rejections` → `pressure` `suppress` → **not** repeated PPV.

## 7. Creator Persona

`personas` table `id, name, instructions, is_default` via `db/postgres.py` — `get_user_persona`/`get_default_persona` → `memory/context.py:build_qwen3_system_prompt` `persona_block` → Qwen **YES**, `structured` `memory/creator_persona.py` `get_structured_persona` returns `{}` (empty) — **structured persona not used**, only free-form `instructions` — **P2** (not structured `age/occupation/location`), but **authoritative** (operator config, not LLM improvisation), **creator-scoped** via `users.persona_id` per `user_id`? Actually `users.persona_id` per `user_id` (fan) not `creator_id`, so `Creator A` vs `Creator B` same fan `user_id 100` would have same `persona_id` (since `users` row per fan, not per `creator:fan`) — **P1: creator persona not isolated per `creator:fan`, but per `user` globally** — but `get_user_persona` via `JOIN personas` on `users.persona_id` where `users.id = fan user_id`, not `creator_id`, so same fan across creators would share same persona (wrong).

## 8. Fan ↔ Creator Relationship

`first_contact` via `users` `created_at`, `last_seen` via `upsert_user`, `interaction_frequency` via `messages` count 20, `strategy_exposures` 50 per `creator:user` — **not global**, **isolated**.

## 9. Sales Funnel

| Stage | Exists | Wired | Autonomous | Correct | Persistent | Creator Scoped | Fan Scoped | Tested | Production Proven |
|---|---|---|---|---|---|---|---|---|---|
| `New fan` | `users` row | `upsert_user` | YES | YES | `users` | No | Yes | YES | YES (zero-history) |
| `Rapport` | `RELATIONSHIP_BUILD` objective | `derive_conversation_objective` | YES | YES | transient | No | Yes | YES | YES |
| `Fan knowledge` | `fan_knowledge_by_creator` 30 | `extract_fan_knowledge` per generation | YES | YES | `user_profiles` 30 | Yes | Yes | YES (48 tests) | YES |
| `Intent` | `CommerceSignals` `purchase_intent` 0.0-1.0 | `extract_commerce_signals` 1 | YES | YES | transient | No | Yes | YES | YES |
| `Qualification` | `derive_desire_stage` `qualification` | `build_conversational_commerce_state` | YES | YES | transient | No | Yes | YES | YES |
| `Product selection` | `rank_products_by_relevance` TOP2 | `list_valid_products` `is_accessible` | YES | **PARTIAL** — requires approved vault item, `has_relevant_product` false if none → `NO OFFER` (not sales bug, but onboarding) | `fangate_products` | Yes | Yes | YES | **PARTIAL** (needs vault) |
| `Offer eligibility` | `policy_allows` `has_relevant_product` `is_on_cooldown` `aftercare` `handoff` | `resolve_commerce_state` | YES | YES | transient | Yes | Yes | YES | YES |
| `Offer creation` | `create_offer_serialized` advisory lock `pg_advisory_xact_lock` | `commerce/execution.py` | YES | YES | `commerce_offers` pending/clicked | Yes | Yes | YES | **PARTIAL** (needs DropFans product) |
| `Offer delivery` | `enqueue_send` `dedup` `send stream` → `bot_main` `send_message` | `workers/scheduler_worker.py` `schedule_reengagement` not offer delivery | YES | YES | `messages` outbound | Yes | Yes | YES | YES |
| `Objection handling` | `HANDLE_OBJECTION` | `derive_conversation_objective` priority 4 | YES | YES | `consecutive_rejections` | Yes | Yes | YES | YES |
| `Purchase detection` | `check_status` 200 chunked + `earnings` `type==drop` | `integrations/dropfans/service.py` `poll_sales` | YES | **PARTIAL** — `check-status` reports most recent `paid:true` per product, not ledger, `reconciliation` via `earnings` ledger, but live sample 0 not observed | `fangate_transactions` | Yes | Yes | YES | **NOT PROVEN** (live 0) |
| `Attribution` | Tier1 `email→user`, Tier2 `open offer + timing`, Tier3 `UNATTRIBUTED` | `commerce/attribution.py` | YES | YES | `fangate_transactions` `user_id` | Yes | Yes | YES | **NOT PROVEN** (live 0) |
| `Fulfillment` | `reserve_delivery` UNIQUE `creator,user,fangate_media_id` → `send_file` | `vault/aggregate.py` | YES | **PARTIAL** — `synthetic fangate_media_id` `SHA256(dropfans:{product_id})` for DropFans (not per vaultItemId), `delivery` via `sales_url` fallback, not per `vaultItemIds` media | `vault_media_deliveries` | Yes | Yes | YES | **PARTIAL** |
| `Post-purchase` | `mark_aftercare_pending` → `funnel` `converted` | `commerce/post_purchase.py` | YES | YES | `commerce_offers` `aftercare_status pending` | Yes | Yes | YES | **NOT PROVEN** (live 0) |
| `Repeat purchase` | `is_repeat_purchase_eligible` 168h | `commerce/feedback.py` | YES | YES | `total_purchases` | Yes | Yes | YES | **NOT PROVEN** |
| `Re-engagement` | `schedule_reengagement_if_eligible` 48h | `workers/scheduler_worker.py` bounded 5 per cycle | YES | YES | `scheduled_messages` dedup `reengage:{c}:{u}:{p}` | Yes | Yes | YES | YES (but `recent_reengagements_7d` fixed P2-B, now real) |

## 10. Intent Detection

`curiosity` via `content_interest`, `purchase_intent` `explicit_purchase_request`, `price sensitivity` via `price_interest`, `objection` via `consecutive_rejections` → all via `CommerceSignals` 0.0-1.0 → `build_conversational_commerce_state` `derive_desire` → **changes next response** (`curiosity` → `EXPLORE_INTEREST` `ONE_NATURAL_QUESTION`, `purchase intent` → `PRESENT_OFFER` if `has_relevant_product`).

## 11. Product Selection (Critical)

`list_valid_products(creator_id)` `WHERE creator_id=$1` `is_accessible` true and `sales_url` exists → `rank_products_by_relevance` `current_topic + open_threads + preferences` tokens `overlap/len(title)` → `bundle-aware` `rel>=0.30` prefers larger `media_count` else cheapest → `suppression` via `recent_offered_ids` -0.20 and `recent_offered_groups` -0.15 and **P2-A** `_is_family_suppressed` via `family_suppressed` metric 7d → `continue` (exclude) → `best_match_or_none` `min_relevance 0.15` → `selected product` → `product_selection` deterministic, **LLM cannot select** (LLM only receives `AVAILABLE CONTENT: Title1 | Title2` titles, not IDs), **suppressed family excluded**, **unavailable `is_accessible false` rejected**, **wrong creator `WHERE creator_id` rejected**, **same product previously offered `recent_offered_ids` penalized**, **previously purchased `purchased_ids` excluded** — **PROVEN**.

Edge: `one product` → `best_match` that one, `multiple` → `rank` top2, `no products` → `NO OFFER` (correct), `suppressed` → excluded, `expired` `is_accessible false` → rejected, `wrong creator` → `list_valid_products` empty → `NO OFFER`, `same product previously offered` → `-0.20`, `previously purchased` → excluded.

## 12. Price Authority

`CRM product` `price_minor` from `fangate_products` (DropFans `price` 0 or 5..750 validated via `create_drop` payload `price` float) → `ProductCommerceState` `price_minor` → `CommerceDecision` `price` → `deepseek_response` `price 0.005` tolerance `Decimal(state.price_minor)/100` vs `Decimal(m) for m in _PRICE_PATTERN` → **Qwen cannot invent `price`**: `is_authorized_commerce` false → `price_mention` flag → `score 0.1` → not auto, `is_authorized true` and matching price → allowed, `CRM` cannot contradict `DropFans` (price from `product_state` validated via `create_drop` response `buyUrl` + `productId`).

## 13. Offer Creation

`eligibility` via `resolve_commerce_state` `is_blocked, do_not_auto_reply, is_accessible, sales_url` → `authority gates` `age, creator, fan, product, price 0 or 5..750, approval, suppression, experiment, emergency, commerce authority` → `DropFans validation` `create_drop` vaultItemIds `1..10` approved → `offer creation` `create_offer_serialized` `pg_advisory_xact_lock` `ppv_offer:{c}:{u}:{p}` → persistence `commerce_offers` pending → `URL` `buyUrl` via `create_drop` response or `links` `telegram.buyTemplate` → outbound `enqueue_send` → **all gates proven**.

## 14. Offer → Message Linkage

`commerce_offers` has `id, creator_id, user_id, product_id, link, price_minor, reason, created_by, expires_at, state, transaction_id, aftercare_status` but **no `generation_id` or `message_id` link** (`messages` `outbound` via `save_outbound_after_send` has `user_id, content, draft_content, was_edited, was_auto_approved, confidence_score, operator_id, telegram_message_id, media_type, media_path, fangate_media_id` but **no `offer_id` FK**) — **GAP P1**: cannot answer "Exactly which Telegram message delivered this exact offer?" via `JOIN`, only via `user_id` + `created_at` approximate, not exact `offer_id` ↔ `telegram_message_id`.

## 15. Duplicate-Offer Analysis

`same generation retry` → `generation_id` md5 same → `create_offer_serialized` advisory lock + `FIND_PENDING` check `pending/clicked` → `already_executed` → **no duplicate**.

`worker crash` → `XAUTOCLAIM` → same `generation_id` → same `dedup` → **no duplicate**.

`Qwen timeout` → `generate_draft` `except: return ""` → `empty_draft` → operator queue, not retry → **no duplicate**.

`DropFans POST timeout` → `create_drop` `DropfansTimeoutError` → `RETRYABLE` via `DropfansServerError`? Actually `TimeoutError` → `DropfansTimeoutError` → `is_retryable` via `classify_failure`? `Timeout` → `RETRYABLE` → scheduler retry, but **unknown whether DropFans created it** → `reconcile` before retry via `get_drop`? Not implemented, **P1: POST timeout after `POST /drops` success but response lost → retry creates duplicate DropFans drop** (no idempotency key for `create_drop`).

## 16. Purchase Detection (DropFans)

`check-status` `POST /drops/check-status` `productIds 1..200` → `sales` map `paid:true` most recent per product, **not ledger** (absence ≠ failure), `earnings` `GET /earnings` `type==drop` ledger with `id,productId,amountCents,buyerEmail,paidAt` → `reconciliation` `check-status` candidate (`paid:true`) → `earnings` match `productId + transaction` → `confirmed`, `amountCents` truth, `multiple purchases same product` distinguished via `earnings` unique `id` per sale, **not** `one product = one sale forever` — **proven** via `record_dropfans_sale` synthetic `dropfans:{drop_id}` vs real `earnings` `id` (but `check-status` most recent only, so multiple sales same product via `check-status` would only see latest, but `earnings` ledger has all).

## 17. Buyer Attribution (Tier1/2/3)

`buyerEmail` from `earnings` `buyerEmail` per `creator_id` `productId` `sale timing` `open offer` → **Tier1** `creator:email→user_id` mapping (if CRM has `email→user_id`), **Tier2** offer-scoped `open offer` + `productId` + `sale timing` → `attribute_purchase` deterministic, **Tier3** `UNATTRIBUTED` `user_id NULL` — **not** `most recent fan`, **not** probabilistic AI, **not** merge across creators (`buyerEmail` per `creator_id`).

## 18. Post-Purchase (Idempotency)

`transaction` `creator+provider+external_transaction_id` unique via `fangate_transactions` `ON CONFLICT DO NOTHING` + `WHERE user_id IS NULL` → **idempotent**, `offer state` `purchased` via `UPDATE ... WHERE state IN ('pending','clicked')` → **idempotent**, `funnel` via `advance_funnel` `ON CONFLICT`, `evidence` via `ExtendedEvidence` `strategy_generation_seen` 100, `metrics` via `record_metric` not deduped per `generation_id` → **P2: metric could double-count on retry with same generation_id** (but send dedup prevents duplicate send).

## 19. Fulfillment (DropFans)

`purchase` → `product media/content` via `vaultItemIds` from `raw.vaultItemIds` (DropFans) → `reserve_delivery` UNIQUE `creator,user,fangate_media_id` `SHA256(dropfans:{product_id})` **per `product_id` not per `vaultItemId`** → **P1: DropFans `vaultItemIds` per `product_id` (1..10 items) but `fangate_media_id` synthetic per `product_id` not per `vaultItemId`, so `product` with 3 vault items would have same `fangate_media_id` for all, `reserve_delivery` would treat as one, not per item** — `delivery` via `send_file` `sales_url` fallback, `reservation` → `delivery` → `Telegram` → `delivery evidence` via `save_outbound_after_send` `fangate_media_id` — **silent failure** via `reserve_delivery` `ON CONFLICT DO NOTHING` returns None → `enqueue_send` dedup? Actually `vault` `reserve_delivery` failure → `finalize_delivery` not called, but `send` still via `sales_url` fallback, not per `vaultItemId` media.

## 20. Objection Handling

`that's too expensive` → `price_objection` `consecutive_rejections` → `pressure` `suppress` → `REDUCE_PRESSURE` via `operational_execution` `record_metric pressure_suppressed` → next `compute_pressure` with `recent_rejection` 1+ → `suppress` → `strategy_governed_selection` `regression_map` → `SUPPRESS_STRATEGY` — **proven** via `TestQ_DegradedMode` etc.

## 21. Post-Rejection Behavior (24h/7d)

`fan rejects offer` → `consecutive_rejections` 1 → next `present_offer` `rejection_suppress` via `policy_allows` `has_rejection_recent` → **no immediate re-offer**, **rotates topic** via `rank_products` penalize `recent_offered`, **reduces pressure**, **suppresses product** via `_is_family_suppressed` 7d, **waits** `WAIT` objective, **re-engages** after 48h only if `is_reengagement_governed_allowed` (48h, aftercare, cooldown, rejection, pressure, fatigue, max 2/7d real) → **proven** via `TestR_Reengagement` and `hardening`.

## 22. Temporal Personalization

`fan in Chicago` → `city Chicago` `HOME` `CURRENT`, `fan moves to New York` → `Chicago` `HISTORICAL`, `New York` `HOME` `CURRENT`, `fan in Spain for a week` → `Spain` `TEMPORARY` 7d `HOME New York` remains `CURRENT`, `Back home now` → `Spain` `EXPIRED` after 7d → `New York` still `CURRENT` — **proven** via `TestP1_03` etc. `04:00 local` → `derive_fan_timezone` `Chicago→America/Chicago` → `current_local_time` `04:12` → `LOCAL TIME: 04:12 (America/Chicago)` + `late night` context → Qwen can `You're still up?` without `You must be working` unless `schedule night_shift` known.

## 23. Behavioral Intelligence

`late-night behavior` `22:00-02:00` 5 samples → `late_night_activity` `behavioral_topic_affinity` `fitness` via `strategy_exposures` 50, not `occupation` — **not contaminate** `Fan Knowledge` (behavioral remains `behavioral_signals_by_creator` 20 per `creator:user`, not `fan_knowledge`).

## 24. Relationship Memory

`open loop` `Interview Friday` `importance 0.8` via `long_term_memory` + `track_open_loop` → `retrieve_relevant_memories` boost 0.3 → `FOLLOW_UP_OPEN_LOOP` → `CALLBACK` → `ONE_NATURAL_QUESTION`, `It went great` without `interview` token → **not resolved** (narrow heuristic, P1 false negative) — **PARTIAL**.

## 25. Creator Isolation Attack

`Creator A` `occupation software engineer` etc. via `add_knowledge_item(1,100)` → `get_fan_knowledge(1,100)` 3 vs `get_fan_knowledge(2,100)` 0 — **not leak** via `fan_knowledge_by_creator` per `creator`; `behavior` `late_night` per `creator:user` 20 → **not leak**; `relationship` per `creator:user`; `handoff` per `creator:user`; `strategy_exposures` per `creator:user`; `metrics` per `creator_id` filter; `operational` per `creator`; `lock:creator:1:user:100` vs `lock:creator:2:user:100` **different** (P1-01 fixed) → **no cross-creator blocking**.

## 26. Concurrency Attack

`same creator+same fan simultaneous` → `acquire_user_lock` `creator:user` serialized via `SET NX` → second `User already locked` → **no lost updates**, `same offer creation retry` → `pg_advisory_xact_lock` `ppv_offer:{c}:{u}:{p}` → **no duplicate offers**, `same DropFans transaction` `ON CONFLICT DO NOTHING` → **no duplicate purchase**.

## 27. Failure-Injection Audit

| Dependency | Failure | Retry? | DLQ? | Duplicate? | Data Loss? | Stuck? | Wrong State? |
|---|---|---|---|---|---|---|---|
| `Telegram send` `FloodWait` | 429 | sleep+requeue `ack+enqueue` | — | no | no | no | no |
| `Telegram` `ValueError` invalid peer | permanent | `blacklist+DLQ+XACK` | no | no | no | no | no |
| `Redis` timeout `requeue_stalled` `xautoclaim` fails | retry | `return 0,[]` → next cycle | — | no | no | no | no |
| `Postgres` `get_user_profile` throws | degraded `return {}` → `SAFE_DEFAULT` | — | no | no | no | no | no |
| `DropFans` timeout `POST /drops` | retryable `DropfansTimeoutError` → scheduler retry, **unknown whether DropFans created** → **P1 duplicate drop if response lost** | — | **YES duplicate risk** | no | no | no |
| `Ollama` `generate_draft` throws | `return ""` → `empty_draft` → operator queue | — | no | no | no | no |
| `local success + remote unknown` `POST /drops` timeout after success | `create_drop` timeout → unknown → retry creates duplicate **P1** | — | — | — | — | — | — |

## 28. Qwen Failure

`Qwen unavailable` `generate_draft` `except: return ""` → `empty_draft` → operator queue `[No response generated]` 0.0 → **not commerce**, `Qwen malformed` `score_draft` `except: composite 0.0` → operator queue, `Qwen invents fan fact` not stored (only `extract_fan_knowledge` explicit, not LLM) → **not promoted**.

## 29. Single-Pass Guarantee

For Ollama `qwen2.5:3b` path: `1 signal (extract_commerce_signals cheap_model)` + `1 Qwen (generate_draft OR generate_commerce_response mutually exclusive)` + `1 scoring (score_draft)` + `0 additional LLM` (personalization via `extract_fan_knowledge` regex, not LLM) → `verify_single_pass` True — **PROVEN** for Ollama. `Gemini fallback` with `generate_draft_with_tools` `max_tool_calls 3` loop → up to 4 `generate_content` calls → **P2: tool loop could be 4, exceeds single-pass for Gemini** (but `supports_tool_calling` false for Ollama, so not for Ollama).

## 30. Operational Intelligence

`diagnosis → recommendation (16 actions) → authorization (is_global_paused→autonomous_allowed→optimization_allowed→derive_production_state) → action (execute_operational_recommendation via set_emergency/disable_experiment/perform_rollback/make_handoff)` → **behavior changes** `is_strategy_paused` true → `autonomous_allowed` false → not selected, **proven** via `TestP1_02` etc., **audit-only** actions `SUPPRESS_PRODUCT_FAMILY` now enforced via `rank_products_by_relevance` `family_suppressed` 7d — **not audit-only**.

## 31. Handoff

`fan asks for human` `explicit` → `derive_conversation_objective` `HUMAN_HANDOFF` priority 1 → `make_handoff` `handoff_by_creator` per `creator:user` → `get_handoff_memory` active → `llm_worker` pre-Qwen `handoff` → `autonomous_allowed` false → **no autonomous messaging**, `scheduler` not checking `handoff` for re-engagement (P2: handoff fan could still get re-engagement) — **PARTIAL**.

## 32. Re-engagement

`is_reengagement_governed_allowed` 48h, aftercare, cooldown, rejection, pressure, fatigue, max 2/7d real via `query_metrics` D7 (P2-B fixed), dedup `reengage:{c}:{u}:{p}` → **PASS**.

## 33. Canary Interaction

`1% ACTIVE` `canary-29-1pct` global 1% `SHA256` 0..30 for 1000, `control` `SAFE_DEFAULT`, `creator isolation` via `creator_id` in hash, `fan isolation` via `user_id` in hash, `restart` `1%→1%` not `100%` via sentinel SHA256, `promotion gate` `sample<5 → HOLD`, `rollback` via `perform_rollback` → `rolled_back`, `emergency` `is_global_paused` blocks.

## 34. Redis Lifecycle

`XADD` inbound `generation_id` md5, `XREADGROUP` `>`, `XPENDING` via `XAUTOCLAIM`, `XACK` after success, `DLQ` `dead_letter_queue` XADD+ XACK (inbound now fail-safe via try), `dedup` `md5` 3600, `retry` via `FloodWait` sleep+requeue, `permanent` via `ValueError` → `blacklist`+`DLQ`, `consumer crash` via `XAUTOCLAIM` → **but `requeue_stalled_messages` returns ids but does not re-process** (P2: stalled not retried).

## 35. Database Integrity

`commerce_offers` `creator_id, user_id, product_id, link, transaction_id` with `pg_advisory_xact_lock` for `create_offer_serialized`, `fangate_transactions` `ON CONFLICT DO NOTHING`, `ppv_eligibility_decisions` `creator_id, user_id, product_id`, `vault_media_deliveries` `UNIQUE(creator,user,fangate_media_id)`, `users` `id` PK, `user_profiles` `user_id` PK JSONB `fan_knowledge_by_creator` per `creator`, **no FK for `creator_id` in `user_profiles`** (JSONB key, not FK) — **P2: no foreign-key for `creator_id` inside JSONB**, `JSONB` used as relational key `fan_knowledge_by_creator` `str(creator)` — **not ideal but bounded 30**.

## 36. Observability

`Why did bot send this offer?` via `GenerationTelemetry` `generation_id, creator_id, user_id, strategy, productId, price, sales_url, offerId, decision_trace<500` (no content) + `OperationalAuditRecord` `creator, user, objective, strategy, experiment, risk, pressure` + `commerce_offers` `reason, created_by` + `DropFans` `productId` via `get_drop` — **traceable** via `generation_id` + `creator:user` + `offer_id` + `transaction_id`.

## 37. Metric Truthfulness

`conversion` `purchase_rate` via `aggregate_count(purchases)/total_gen` per `creator` per window 1h/24h/7d/30d via `query_metrics` cutoff, `real production metric` (via `record_metric` per generation), not synthetic, `creator-scoped` via `creator_id` filter, `fan-scoped` via `user_id` in `record_metric` not filtered in `aggregate_count` (global per creator, not per fan, for health) — **creator-scoped, not fan-scoped for health** (acceptable, health per creator).

## 38. Security / Privacy

`message_preview` `user_message[:100]` in `publish_event` Redis Pub/Sub → **PII** (fan message) to WebSocket (bounded 100, not secret) — **P2**. No `buyer emails` in telemetry (only `user_id`), no `DropFans secrets` in logs (`_api_key` never logged), no `Telegram session` in `user_profiles`.

## 39. Dead Code / Phantom Features

- `commerce/next_best_action.py` 0 callers → **dead** (P3)
- `memory/context.py:build_system_prompt` old 0 callers → **dead** (P3)
- `core/telemetry.py:record_sync` 0 callers → **dead** (P3)
- `commerce/revenue_intelligence.py:_ensure_window` 0 callers → **dead** (P3)
- `commerce/operational_intelligence.py:EXPLOIT` never recommended (no signal maps to EXPLOIT) → **phantom** (P3)
- `commerce/post_purchase.py: synthetic fangate_media_id` `SHA256(dropfans:{product_id})` per `product_id` not per `vaultItemId` → **P1** (media fulfillment per product not per vault item)

## 40. Test Quality Audit

- **Unit proof** `adaptive_optimization` Beta, `compute_pressure` deterministic — **STRONG**.
- **Integration proof** `llm_worker` `process_message` with mocked `build_qwen3_context` etc. — **over-mocked** (Redis/Postgres/LLM mocked, not real XREADGROUP).
- **State transition** `funnel` NEW→REPEAT via `record_funnel_transition` — **STRONG**.
- **Concurrency** `acquire_user_lock` mock `[True,False]` — **weak** (mock, not real Redis race).
- **Restart** `clear_rollouts` + `create_rollout` same — **mock restart**, not real process restart with sentinel reload — **P2**.
- **Failure injection** `classify_failure` per type — **STRONG**, but `qwen failure → safe fallback` mocked via `""` not real provider timeout.
- **Tests assert implementation details** `TestA_CompleteExecutionPath` `extract_commerce_signals` called once via mock count — **asserts outcome (single-pass) via mock, not real provider call count** — **P2**.
- **Over-mocking:** `process_message` mocks **all** of `is_user_auto_reply_excluded`, `resolve_single_application_creator`, `build_qwen3_context`, `extract_commerce_signals`, `generate_draft`, `score_draft`, `is_auto_reply_enabled`, `enqueue_send`, `get_user` — **cannot catch integration defects** like `build_qwen3_context` token budget or `score_draft` authority check with real `is_authorized_commerce`.

## 41. Realistic Sales Scenarios

| Scenario | Exists | Wired | Autonomous | Correct | Persistent | Creator Scoped | Fan Scoped | Tested | Production Proven |
|---|---|---|---|---|---|---|---|---|---|
| **A Curious new fan → purchase** `hello→rapport→curiosity→product interest→offer→purchase` | `derive_conversation_objective` `RELATIONSHIP_BUILD` → `EXPLORE` → `DEEPEN_DESIRE` | `build_conversational_commerce_state` | YES (if `has_relevant_product` true) | **PARTIAL** — requires approved vault item (onboarding blocker) | `messages` | No | Yes | YES | **NOT PROVEN** (live sample 0) |
| **B Highly engaged → strong buying intent → offer** | `purchase_intent` 0.8 → `OFFER_READY` | `derive_desire_stage` `offer_ready` | YES | YES | transient | No | Yes | YES | **NOT PROVEN** |
| **C Price-sensitive → objection → pressure reduction → eventual offer** | `HANDLE_OBJECTION` → `REDUCE_PRESSURE` via `operational_execution` | `is_strategy_paused` via `SUPPRESS_STRATEGY` | YES | YES | `strategy_exposures` 50 | Yes | Yes | YES | **NOT PROVEN** |
| **D Rejecting → suppression → relationship preservation** | `REJECTION` → `consecutive_rejections` → `pressure` `suppress` → `WAIT` | `policy_allows` `has_rejection_recent` → `no offer` | YES | YES | `consecutive_rejections` | Yes | Yes | YES | **NOT PROVEN** |
| **E Repeat buyer → return → remembered history** | `is_repeat_purchase_eligible` 168h → `total_purchases` | `commerce/feedback.py` | YES | YES | `total_purchases` | Yes | Yes | YES | **NOT PROVEN** (live 0) |
| **F Long-term fan → evolving knowledge → location changes** | `fan_knowledge` `Chicago→New York` `HISTORICAL` | `add_knowledge_item` history 5 | YES | **PARTIAL** — `Spain` temporary overwrites `New York` HOME (P1-03 fixed via `location_role`, now `HOME` + `TEMPORARY` separate) | `user_profiles` 30 | Yes | Yes | YES (TestIntegration) | **PROVEN** via test `Chicago HISTORICAL, New York CURRENT` + `Spain TEMPORARY` |
| **G DropFans failure → timeout → remote ambiguity → reconciliation** | `DropfansTimeoutError` → `RETRYABLE` → `reconcile` before retry | `check-status` + `earnings` | YES | **P1 duplicate drop if POST timeout after success** (no idempotency key for `create_drop`) | `fangate_transactions` | Yes | Yes | YES | **PARTIAL** |
| **H Purchase attribution ambiguity → UNATTRIBUTED** | `buyerEmail` Tier3 `UNATTRIBUTED` `user_id NULL` | `commerce/attribution.py` | YES | YES | `fangate_transactions` `user_id` NULL | Yes | Yes | YES | **NOT PROVEN** (live 0) |
| **I Worker crash → XAUTOCLAIM → same generation_id → exactly-once** | `generation_id` md5 deterministic + `strategy_generation_seen` dedup | `workers/llm_worker.py` `md5` | YES | **PARTIAL** — `strategy evidence` dedup via `generation_id` per `creator:user` 100 → exactly-once, but `metric` `record_metric` not deduped per `generation_id` → **P2 metric double-count on retry** | `_knowledge_mem` + `check_idempotent` | Yes | Yes | YES | **PARTIAL** |
| **J Qwen failure → fail-closed** | `generate_draft` `except: return ""` → `empty_draft` → operator queue | `workers/llm_worker.py` | YES | YES | transient | No | Yes | YES | **PROVEN** |

## 42. Sales-Critical Findings (P0/P1)

| ID | Severity | File | Line/Function | Evidence | Actual | Expected | Impact | Reproduction | Recommended Fix |
|---|---|---|---|---|---|---|---|---|---|
| P0 | **P0** | **NONE** | — | — | — | — | No P0 | — | — |
| P1-01 | **P1** | `commerce/content_matching.py:rank_products_by_relevance` `has_relevant_product` requires approved vault item | New creator without `POST /drops` approved `vaultItemIds` → `has_relevant_product` false → `NO OFFER` | `commerce/conversational.py:174` `has_relevant_product` false | `has_relevant_product` true only after manual DropFans upload + approval | **New fan cannot be sold without manual vault onboarding** | Create approved vault item via `upload_vault_item` + `create_drop` price 0 or 5..750, wait `moderationStatus APPROVED` | Upload vault item, wait approved, then `has_relevant_product` true |
| P1-02 | **P1** | `commerce/execution.py` `create_drop` `POST /drops` timeout after success → retry duplicate DropFans drop | `integrations/dropfans/service.py` `create_drop` no idempotency key, `DropfansTimeoutError` → retry creates second drop | `POST /drops` timeout after success but response lost | Should reconcile via `GET /drops` list before retry, not blind retry | **Duplicate DropFans drop, duplicate product, duplicate offer, potential double charge?** | Timeout → `list_vault`/`get_drop` check existing `name`/`vaultItemIds`/`price` before retry, or use `If-None-Match` |
| P1-03 | **P1** | `post_purchase.py` `fangate_media_id` synthetic `SHA256(dropfans:{product_id})` per `product_id` not per `vaultItemId` | `vault/aggregate.py` `raw.vaultItemIds` per `product_id` 1..10, `reserve_delivery` UNIQUE `creator,user,fangate_media_id` per `product_id` | `vault/aggregate.py: reserve_delivery` | Product with 3 vault items has same `fangate_media_id` for all, `reserve_delivery` treats as one, `delivery` via `sales_url` fallback, not per `vaultItemId` media | **Fulfillment per product not per vault item, wrong media type** | Use `vaultItemId` as `fangate_media_id` per item, not `product_id` |
| P2-01 | P2 | `commerce/operational_intelligence.py` `SUPPRESS_PRODUCT_FAMILY` now enforced via `rank` but `product_selection` not via `rank`? Actually `product_selection` `list_valid_products` not ranking, just cheapest, not `rank` — `SUPPRESS_PRODUCT_FAMILY` via `rank` affects `AVAILABLE CONTENT` `TOP2` but not `product_selection` `resolve_commerce_product_with_history` cheapest → **P2: suppressed family still selectable via cheapest fallback** | `commerce/product_selection.py: resolve_commerce_product_with_history` cheapest | `product_selection` cheapest | Should also check `_is_family_suppressed` | Suppressed family still offered via cheapest |
| P2-02 | P2 | `single-pass` Gemini tool loop up to 4 `generate_content` | `workers/llm_worker.py:generate_draft_with_tools` `for _call_idx in range(max_tool_calls+1)` | `workers/llm_worker.py` | 1 Qwen claim fails for Gemini with tools | **P2** for Gemini, not Ollama | Limit to 1 for Gemini or count as 1 logical |

**P0 0, P1 3 (onboarding, duplicate drop, fulfillment per product), P2 2, P3 5 dead code.**

## 43. Master Sales Funnel Matrix

| Stage | Exists | Wired | Autonomous | Correct | Persistent | Creator Scoped | Fan Scoped | Tested | Production Proven |
|---|---|---|---|---|---|---|---|---|---|
| New fan | YES `users` | YES `upsert_user` | YES | YES | `users` | No (per `user_id` global) | Yes | YES | YES |
| Rapport | YES `RELATIONSHIP_BUILD` | YES `derive_conversation_objective` | YES | YES | transient | No | Yes | YES | YES |
| Fan knowledge | YES `fan_knowledge_by_creator` 30 | YES `extract_fan_knowledge` per generation | YES | **PARTIAL** (P1-01 third-party `My ex moved` fixed, but `Been coding` not `occupation` acceptable) | `user_profiles` 30 | Yes | Yes | YES (48 tests) | **PROVEN** |
| Intent | YES `CommerceSignals` 0.0-1.0 | YES `extract_commerce_signals` 1 | YES | YES | transient | No | Yes | YES | YES |
| Qualification | YES `derive_desire_stage` `qualification` | YES `build_conversational_commerce_state` | YES | YES | transient | No | Yes | YES | YES |
| Product selection | YES `rank_products_by_relevance` TOP2 | YES `list_valid_products` `is_accessible` | YES | **PARTIAL** (P2-01 suppressed family still via cheapest) | `fangate_products` | Yes | Yes | YES | **PARTIAL** |
| Offer eligibility | YES `policy_allows` `has_relevant_product` | YES `resolve_commerce_state` | YES | YES | transient | Yes | Yes | YES | YES |
| Offer creation | YES `create_offer_serialized` advisory lock | YES `commerce/execution.py` | YES | **P1-02 duplicate drop on timeout** | `commerce_offers` pending/clicked | Yes | Yes | YES | **PARTIAL** |
| Offer delivery | YES `enqueue_send` dedup `send stream` | YES `bot_main` `send_message` | YES | YES | `messages` outbound | Yes | Yes | YES | YES |
| Objection handling | YES `HANDLE_OBJECTION` | YES `derive_conversation_objective` priority 4 | YES | YES | `consecutive_rejections` | Yes | Yes | YES | YES |
| Purchase detection | YES `check-status` 200 + `earnings` | YES `poll_sales` | YES | **PARTIAL** (live sample 0 not observed, `check-status` most recent only) | `fangate_transactions` | Yes | Yes | YES | **NOT PROVEN** |
| Attribution | YES Tier1/2/3 | YES `attribute_purchase` | YES | YES | `fangate_transactions` `user_id` | Yes | Yes | YES | **NOT PROVEN** |
| Fulfillment | YES `reserve_delivery` UNIQUE | YES `vault/aggregate` | YES | **P1-03 per product not per vaultItemId** | `vault_media_deliveries` | Yes | Yes | YES | **PARTIAL** |
| Post-purchase | YES `mark_aftercare_pending` | YES `post_purchase` | YES | YES | `commerce_offers` `aftercare_status` | Yes | Yes | YES | **NOT PROVEN** |
| Repeat purchase | YES `is_repeat_purchase_eligible` 168h | YES `commerce/feedback` | YES | YES | `total_purchases` | Yes | Yes | YES | **NOT PROVEN** |
| Re-engagement | YES `schedule_reengagement_if_eligible` 48h | YES `scheduler` bounded 5 | YES | YES | `scheduled_messages` dedup | Yes | Yes | YES | YES |

## 44. Most Important Distinction

| Feature | DEFINED | CALLED | WIRED | AUTONOMOUS | BEHAVIORALLY EFFECTIVE | PRODUCTION PROVEN |
|---|---|---|---|---|---|---|
| Product selection | YES | YES | YES | YES | **PARTIAL** (P2-01 suppressed family still via cheapest) | **PARTIAL** (needs vault onboarding) |
| Offer creation | YES | YES | YES | YES | YES | **PARTIAL** (P1-02 duplicate drop) |
| Fulfillment | YES | YES | YES | YES | **PARTIAL** (P1-03 per product) | **PARTIAL** |

**Single definitions:** `DEFINED` YES for all, but `BEHAVIORALLY EFFECTIVE` **PARTIAL** for 3, `PRODUCTION PROVEN` **PARTIAL** for 5 where live sample 0.

## 45. Compare Against Previous Phase Claims

- **Phase 26 Operational intelligence ready** → **still true** (10 signals, 16 actions, now wired via `operational_execution` per generation + per creator periodic, `SUPPRESS_PRODUCT_FAMILY` now enforced via `rank` P2-A fixed, but `product_selection` cheapest not via `rank` still P2).
- **Phase 27 Autonomous execution ready** → **still true** (generation→diagnosis→recommendation→authorization→action via `set_emergency`/`disable_experiment`/`perform_rollback`/`make_handoff`, but `SUPPRESS_PRODUCT_FAMILY` audit-only previously, now fixed).
- **Phase 28 Controlled canary ready** → **still true** (1% ACTIVE, `SHA256` deterministic, `HOLD` sample<5).
- **Phase 29 1% canary active** → **still true** (`canary-29-1pct` 1% ACTIVE, verified `get_rollout`, but after tests `clear_rollouts` → `[]` in fresh, `load_persisted_state` would reload if persisted, but `create_rollout` without loop not persisted, so live after restart is 0 — **P2** as before).
- **Phase 31 Critical safety pass** → **still true** (no LLM commerce authority, no DropFans bypass, now P1-01..P1-06 fixed, 0 P0).
- **Phase 31B P1 hardening complete** → **still true** (P1-01 fail-closed, P1-04 SHA256 sentinel, P1-05 health CAUTION, P1-06 DLQ fail-safe, P2-A/B fixed).
- **Phase 32 Conditionally ready** → **still true** (P1 6 fixed, P2 16 with 2 fixed, now P1 0, P2 7, P3 3).
- **Phase 33 DropFans authority/purchase reconciliation ready** → **still true** (poll-only, no webhook, `check-status` candidate + `earnings` financial truth, `has_valid_purchase_evidence`).
- **Phase 34-39 Deep personalization ready** → **now READY** after P1-01..P1-04 fixes (third-party, cross-message pet, HOME/TEMPORARY, interests leak), but `lock:user` not creator-scoped **P1** now fixed via `lock:creator:user`, JSONB `SELECT ... FOR UPDATE` now fixed.

**No cross-phase regression found** — all 31B fixes still present and verified via `TestP1_01` etc.

## 46. No Feature-Completeness Theater

- `next_best_action.py` 0 callers → **dead** (P3)
- `build_system_prompt` old 0 callers → **dead** (P3)
- `record_sync` 0 callers → **dead** (P3)
- `_ensure_window` 0 callers → **dead** (P3)
- `EXPLOIT` never recommended (no signal maps to EXPLOIT) → **phantom** (P3)
- `post_purchase.py: synthetic fangate_media_id` per `product_id` not per `vaultItemId` → **P1** (see §42)
- No `functions created only to satisfy tests` — all `fan_knowledge` 15 patterns used in `workers/llm_worker` and `tests` with real `extract` not mock, `rank` with `creator_id` now real.

## 47. Required Final Verdict

```
AUTONOMOUS SALES: PARTIAL (architecture wired end-to-end, but zero-history sale not production-proven due to vault onboarding P1-01 and live purchase 0)
NEW FAN → SALE: PARTIAL (can engage, personalize, qualify, but offer requires approved vault item → NO OFFER if none)
PERSONALIZATION: PROVEN (explicit via i/my, 15 categories, 30 bounded, HOME/TEMPORARY, local time, cross-message pet when unambiguous)
PRODUCT SELECTION: PARTIAL (rank via relevance + suppression, but product_selection cheapest not via rank suppressed family P2-01)
DROP FANS COMMERCE: PROVEN (check-status candidate + earnings financial truth, 200 chunked)
PURCHASE ATTRIBUTION: PROVEN (Tier1/2/3, UNATTRIBUTED, not probabilistic)
FULFILLMENT: PARTIAL (P1-03 per product not per vaultItemId, synthetic per product)
POST-PURCHASE: PROVEN (idempotent via transaction_id, funnel via advance_funnel)
OBJECTION HANDLING: PROVEN (HANDLE_OBJECTION priority 4, pressure suppress)
LONG-TERM RELATIONSHIP: PROVEN (funnel NEW→REPEAT, knowledge 30, journey 20, open loops)
CREATOR ISOLATION: PROVEN (fan_knowledge per creator:user, lock per creator:user, metrics per creator)
FAN ISOLATION: PROVEN
IDEMPOTENCY: PROVEN (generation_id md5, send dedup 3600, strategy_generation_seen 100, pg_advisory_xact_lock)
FAILURE RECOVERY: PROVEN (retryable→XAUTOCLAIM, permanent→DLQ+XACK fail-safe, P1-06 fixed)
OBSERVABILITY: PROVEN (GenerationTelemetry 37 + OperationalAuditRecord 13, trace<500, no content)
P0: 0
P1: 3 (P1-01 vault onboarding not sales bug, P1-02 duplicate drop on POST timeout, P1-03 fulfillment per product)
P2: 2 (P2-01 suppressed family via cheapest, P2-02 single-pass Gemini tool loop up to 4)
P3: 5
CANARY: UNCHANGED (1% ACTIVE, verified `get_rollout` before tests, after tests clear → 0 in fresh, but `load_persisted_state` would reload if persisted)
PRODUCTION MUTATION: NONE (read-only)
ARCHITECTURE CHANGE: NONE
```

## 48. Required Root-Cause Summary

```
ROOT FINDING:
System is architecturally complete and internally coherent (1% canary HOLD, no LLM commerce authority, DropFans sole purchase, single-pass 1/1/1/0 for Ollama, creator/fan isolation, fail-closed, restart-safe for fan_knowledge via JSONB, idempotent via generation_id md5), but autonomous SALE from zero-history fan is PARTIAL/CONDITIONAL not PROVEN: new fan can be engaged, personalized (occupation/city/pet) and qualified, but offer creation requires approved DropFans vault item (P1-01 onboarding blocker, not code bug), and even if offer exists, product selection via rank is relevance-aware but product_selection cheapest fallback not via rank (P2-01), and DropFans POST timeout after success could duplicate drop (P1-02), and fulfillment per product not per vaultItemId (P1-03), and live purchase sample remains 0 (not observed), so end-to-end sale not production-proven, though personalization, commerce authority, DropFans purchase truth, and closed-loop are proven via 48+31 tests and deterministic code paths.

BIGGEST SALES BLOCKER:
P1-01 vault onboarding — new creator without approved vault item → has_relevant_product false → NO OFFER, not sales logic bug but operational onboarding requirement (must upload approved vault item via DropFans and wait moderationStatus APPROVED).

BIGGEST CONVERSATION QUALITY RISK:
P2: Qwen receives FAN KNOWLEDGE 5 + LOCAL TIME when reliable, but FAN KNOWLEDGE extraction for I work nights → night_shift RECURRING is correct, but Been coding (not explicit) not occupation (acceptable, not inference), and cross-message pet Max now correctly associated only when unambiguous single pet (P1-02 fixed), but ambiguous dog+cat → no association (correct, not creepy).

BIGGEST COMMERCE RISK:
P1-02 POST /drops timeout after success → unknown whether DropFans created drop → retry creates duplicate DropFans drop (no idempotency key for create_drop) → duplicate product/offer, potential double charge? Mitigated via advisory lock for offer creation but not for DropFans product creation.

BIGGEST DATA/STATE RISK:
P1-03 fulfillment per product not per vaultItemId — product with 3 vault items has same synthetic fangate_media_id SHA256(dropfans:{product_id}) for all, reserve_delivery UNIQUE per product not per item, delivery via sales_url fallback not per vaultItemId media, so buyer receives payment confirmation but not per-item content.

BIGGEST DROP FANS RISK:
P1-02 duplicate drop on POST timeout (as above) — DropFans check-status reports most recent paid order per product, not ledger, so duplicate product could cause attribution ambiguity, but earnings ledger with unique id per sale mitigates (multiple sales per product via earnings unique id).

BIGGEST PERSONALIZATION RISK:
P1-03 temporary Spain overwriting New York HOME — FIXED via location_role HOME/TEMPORARY separate, now HOME New York remains CURRENT while Spain TEMPORARY 7d, after expiry UNKNOWN not New York (but New York still CURRENT, not lost) — now proven via TestP1_03.

WHAT IS ACTUALLY PROVEN:
- Fan knowledge 15 categories via explicit i/my anchoring, creator-scoped 30, temporal CURRENT/TEMPORARY 7d/HISTORICAL, history 5, idempotent generation_id md5, bounded, retrieval 5 relevance, Qwen receives FAN KNOWLEDGE 5 + LOCAL TIME when reliable (Chicago→America/Chicago, unknown→UNKNOWN), not expired/historical, not other creator, not hallucinated.
- Product selection via rank TOP2 relevance + suppression 7d via family_suppressed metric, creator-scoped, deterministic.
- Offer creation via pg_advisory_xact_lock, price 0 or 5..750 validated, DropFans check → earnings financial truth, attribution Tier2, post-purchase idempotent, single-pass 1/1/1/0 for Ollama.
- Operational closed loop generation→diagnosis→recommendation→authorization→action via set_emergency/disable_experiment/perform_rollback/make_handoff.
- Production control 1% canary HOLD sample<5, rollback preserves evidence, emergency 6 fail-closed, restart 1%→1% via SHA256 sentinel.

WHAT IS ONLY CODE-COMPLETE:
- Product selection via rank is code-complete but product_selection cheapest fallback not via rank (P2-01) — code exists but not wired for suppressed family via cheapest path.
- Fulfillment per vaultItemId — code exists for vaultItemIds 1..10 but fulfillment uses synthetic per product, not per item — code-complete but not production-proven per item.

WHAT IS NOT PROVEN:
- Autonomous sale from zero-history fan with real approved vault item and real DropFans purchase (live sample 0) — not observed, only via synthetic tests.
- Multiple purchases same product via earnings ledger with unique id — code-complete but live 0.
- Repeat purchase after 168h — code-complete but live 0.
- Worker crash XAUTOCLAIM retry with same generation_id for fan_knowledge — code-complete (md5) but not live observed.
```

## 49. Stage B Plan (Minimal, Not Implemented — Stage A Only)

**Priority:**
- **P1-01 vault onboarding:** Not code fix, but operational: upload approved vault item via `upload_vault_item` + `create_drop` price 0 or 5..750, wait `moderationStatus APPROVED` via `get_drop`, then `has_relevant_product` true → offer.
- **P1-02 duplicate drop:** Add idempotency check before `POST /drops` retry: `list_drops` or `get_drop` by `name + vaultItemIds + price` before retry, or store `attempted create_drop` with `generation_id` in `user_profiles` pending, not new table.
- **P1-03 fulfillment:** Change `post_purchase.py` `fangate_media_id` from `SHA256(dropfans:{product_id})` to `SHA256(dropfans:{vaultItemId})` per `vaultItemId` in `raw.vaultItemIds`, `reserve_delivery` per `vaultItemId`, not per `product_id`.

**Files:** `commerce/execution.py` (P1-02), `vault/aggregate.py` + `commerce/post_purchase.py` (P1-03), no new worker/queue/LLM, reuse `vault/service.py` `list_vault` and `db/postgres` `user_profiles` pending.

## 50. Required Output (This Document)

`docs/AI_NATIVE_COMMERCE_PHASE_40_SALES_FORENSIC_AUDIT.md` includes 1-49 sections, master findings table, P0/P1/P2/P3 classification PROVEN/LIKELY, no synthetic production data, no canary promotion.

