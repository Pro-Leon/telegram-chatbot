# AI-Native Commerce — Phase 6 End-to-End Forensic Audit

**Date:** 2026-08-30
**Forensic basis:** All docs listed in Phase 6 task + CURRENT working tree read-only trace. No production code, prompts, config, DB, migrations, Redis, DropFans, provider, canary, tests modified.
**Method:** Independent code trace anchored to FILE:LINE. Treat Phase 1-5 reports as claims to verify.
**Working directory:** E:\chatbot branch main

> FORENSIC ONLY — No fixes, no refactors, no placeholder implementations.

---

## 1. Executive Summary

The CRM is a **deterministic commerce engine with conversational phrasing**, not an AI-native conversational seller. The deterministic authority (creator isolation, offer idempotency, DropFans-only, AUTONOMY_ENABLED, rate limiting, dedup, DLQ, vault reservation) is **sound and sealed**. The conversational layer (Sunny C.1-F: lifecycle, response modes, question budget, persona self-facts, capability contract) is **materially fixed** for naturalness and capability safety.

**The critical gap is the AI/commerce separation is inverted in the wrong direction:** The deterministic engine decides correctly *when to offer*, but the LLM is **never told the real commercial state**. The bridge that injects `COMMERCIAL STATE` into the LLM prompt (`workers/llm_worker.py:585-595`) is **hardcoded**:
```
_rel = "warm"
desire = derive_desire_stage(relationship_state=_rel, primary_intent=None)
temp = derive_commercial_temperature(relationship_score=0.35, desire_stage=desire.stage.value)
readiness = evaluate_offer_readiness(desire.stage.value, temp.level)
```
Every turn, regardless of what the fan said, the LLM sees `desire=relationship|interest temperature=cold|warm offer_ready=build_desire window=no_window|building`. The real signals (`purchase_intent`, `price_interest`, `primary_intent`, `content_curiosity`, fatigue, aftercare, cooldown) are computed inside the sealed pipeline (`commerce/deepseek.py:170 extract_commerce_signals` -> `commerce/signals.py:254 signals_to_context` -> `commerce/decision.py:272 decide_commerce_action`) but **never reach the LLM prompt**. The LLM therefore **cannot actively lead** `relationship -> curiosity -> tease -> desire -> qualification -> offer`. It can only react with good manners.

Vault delivery remains synthetic `sales_url` fallback (per-item buyer `downloadUrl` missing at DropFans API). Product identity and purchase attribution are correct but delivery is not media delivery.

**Verdict:** The system detects *already-ready* buyers and presents deterministic offers correctly. It does **not** produce a skilled conversational seller that creates desire.

---

## 2. Phase 1-5 Verification (Are Prior Reports True?)

| Claim (Report) | File:Line Verified | Result |
|---|---|---|
| Tip fatigue 30d via tool_audit_log | commerce/dao.py:559-598 wired; context_assembler.py:657-667 uses it | **TRUE** FIXED (was hard-zero) |
| asks_for_free_content fixed (4 optional fields) | commerce/signals.py:181-184, commerce/deepseek.py:56 extra=forbid removed, decision.py:321 suppresses | **TRUE** FIXED — decision now RELATIONSHIP_BUILDING no_sale, not invalid_payload |
| Aftercare surfaced | memory/context_assembler.py:589-594 reads aftercare_status; memory/context.py:285 includes "aftercare" keyword; commerce/decision.py:462 aftercare suppresses; context_assembler.py:795 renders Aftercare: pending | **TRUE** PARTIALLY — Qwen sees Aftercare: pending but *only* when commerce pipeline produced aftercare_active; llm_worker bridge computes _is_aftercare from objective=="aftercare" with hardcoded warm (weak) |
| Multi-product None -> cheapest ranked | commerce/product_selection.py:234-254 sorted(price, id) picks cheapest; list_valid_products deterministic | **TRUE** FIXED |
| Desire ladder 0-8 + decay | commerce/desire.py:20-137 exists, 9 stages, derive + decay | **TRUE** but **UNWIRED to LLM** (hardcoded inputs, see 6) |
| Temperature COLD/WARM/HOT bounded | commerce/temperature.py:31-101 formula rel0.30+desire0.70+purchase+content-fatigue | **TRUE** but duplicated (relationship.py derives differently) |
| Content matching title-token + purchased exclusion | commerce/content_matching.py:71-104 rank_products_by_relevance; vault_taxonomy.py:67 parse_taxonomy; memory/context.py:563-583 injects AVAILABLE CONTENT top2 | **TRUE** WIRED |
| Vault taxonomy Subject—Setting—Format | commerce/vault_taxonomy.py:35-91 normalize/parse/bundle_related | **TRUE** but operator title free-text no validation (fangate.py:368 non-empty only) |
| Sales window NO_WINDOW/BUILDING/OPEN/COOLDOWN/AFTERCARE | commerce/sales_window.py:24-49 thin composition; workers/llm_worker.py:593 derive_sales_window | **TRUE** exists but inputs are synthetic (see above) so window is always BUILDING/NO_WINDOW |
| Product_id NULL -> synthetic fix | db/dropfans.py:185-211 _synthetic_pid SHA256%2^62, reconciliation.py:55 WHERE product_id IS NOT NULL | **TRUE** FIXED |
| Buyer per-item downloadUrl | integrations/dropfans/client.py:269 list_vault returns owner filePath ~12h; check_drop_status only paid bool; NO buyer grant endpoint | **TRUE** BLOCKED external — not fixed, synthetic sales_url fallback retained commerce/post_purchase.py:424-479 |
| Conversation state + response mode + question policy + capability | core/conversation_state.py:157 derive, core/response_mode.py:32 plan_response_mode, core/question_policy.py:23 evaluate_question_budget, core/capability_contract.py:37 derive | **TRUE** WIRED via memory/context.py:503-528 |
| Question policy MAX_PER_3 now wired | core/question_policy.py:45 questions_in_last_3 >= MAX_QUESTIONS_PER_3_TURNS | **TRUE** FIXED (was dead) |
| Response_mode precedence bug fixed | core/response_mode.py:76 any(k in low for k in ...) | **TRUE** FIXED (now uses any, not and precedence) |
| Vault media dead columns remain | db/migrations 202608250000 dropfans_media_id TEXT never written (grep 0 writes) | **TRUE** still dead, harmless |
| Offer readiness READY gate | commerce/offer_readiness.py:25-60 needs hot+0.55, never true with hardcoded 0.0 purchase_intent | **TRUE** but never fires due to bridge |

**Overall:** P0 closures (tip 30d, free-content, multi-cheapest, aftercare keyword) are real. Phase 2-5 intelligence modules are implemented but **the wiring that would make the LLM commercially aware is stubbed with hardcoded values**. Reports that say "Qwen now receives desire/temperature/offer_ready" are technically true (a line is injected) but **semantically false** (the values are fake).

---

## 3. Runtime Call Graph

### 3.1 Telegram Inbound -> Debounce -> Redis
```
Telegram NewMessage(incoming) chatbotv2/handlers.py:25 handle_incoming_message
  check_rate_limit db/redis.py:304, unblacklist core/entity_blacklist.py:52
  upsert_user db/postgres.py:96 + save_inbound_message db/postgres.py:259 -> messages
  publish_event message.created core/event_bus.py:64
  debounce_enqueue db/redis.py:277 lock debounce:{user}:lock nx ex 3s; rpush debounce:{user}:messages
  if not window_owner: send_typing + return (message saved but not enqueued) handlers.py:94
  else: create_task _wait_and_process handlers.py:102
_wait_and_process handlers.py:108 sleep 3s; get_debounced_messages db/redis.py:296 (LRANGE+DEL); latest=debounced[-1] (only latest enqueued, earlier buffered lost except DB)
  get_cached_user_persona db/redis.py:329 cache 600s or db/postgres get_user_persona or get_default_persona WHERE is_default=true
  enqueue_inbound db/redis.py:171 XADD inbound_messages {user_id, content=latest only, telegram_message_id, persona}
setup_handlers handlers.py:141 @client.on(events.NewMessage(incoming=True))
chatbotv2/main.py:438 run() get_client + setup_handlers + create_task _process_send_stream:462 + run_until_disconnected
```

### 3.2 llm_workers Consumer -> process_message
```
workers/llm_worker.py:956 run_worker(worker_id)
  ensure_consumer_group db/redis.py:45 XGROUP_CREATE inbound_messages llm_workers / send_messages send_workers
  requeue_stalled_messages db/redis.py:203 XAUTOCLAIM idle>30s (pending_idle_ms)
  read_inbound db/redis.py:178 XREADGROUP llm_workers consumer count5 block2000
  process_message handlers.py:1022 -> llm_worker.py:467
    acquire_user_lock db/redis.py:252 SET lock:user:{id} nx ex user_lock_ttl(60s) :475 fail -> return
    generation_id uuid4 :482 (same id for all lifecycle events invariant)
    telemetry start_generation core/telemetry.py start_generation :487
    upsert_user :495, is_user_auto_reply_excluded :497
    resolve_single_application_creator commerce/single_creator.py:57 -> creator_id/status READY if one active DropFans integration
    build_qwen3_context memory/context.py:453 :530 (see 5-6)
    Shadow fire-and-forget if enabled core/qwen3_shadow.py:541 should_sample
    publish ai.generation_started core/event_bus.py :555
    _try_commerce_draft :566 (see 3.3)
    if selection USE_COMMERCE_RESPONSE -> draft=selection.commerce_response_text (bypass Qwen) :567-574
    else: COMMERCIAL STATE bridge (hardcoded) :578-609 append to context list
    canary should_use_agent agent/canary.py:51 + ai_runtime_mode legacy/canary :612-616 (currently false -> LEGACY)
      LEGACY: if llm_tools_enabled && creator_id: generate_draft_with_tools :725 else generate_draft :731
      AGENT path dead (would call build_agent_state with hardcoded dummy relationship_state NEW etc :640-658)
    empty draft -> operator queue + publish ai.generation_completed was_auto_approved False :745-774 return
    score_draft core/scoring.py:78 :778
    shadow collect evaluate :785-813
    is_auto_reply_enabled db/redis.py:357 :815
    dedup_id md5(user_id:content:telegram_message_id) :817
    if not auto_reply: operator_queue + publish ai.generation_completed False + suggestion.created :821-859
    elif score>=0.80 and not flags: enqueue_send SEND_STREAM db/redis.py:65 + publish ai.generation_completed True :860-887 (invariant: after enqueue_send)
    else: operator_queue + notify_operators + publish completed False + suggestion.created :888-922
    post_process create_task extract_and_update_profile + maybe_summarize :924
    telemetry complete :927; publish ai.generation_failed on exception :941; finally release_user_lock :953
```

### 3.3 Commerce Sealed Pipeline (deterministic, PURE until execution)
```
_try_commerce_draft llm_worker.py:356
  autonomy_enabled false -> None :391
  resolve_single_application_creator commerce/single_creator.py:57 :399
  resolve_commerce_product_with_history commerce/product_selection.py:149 :408 (list_fangate_products 200, _is_valid_product is_accessible && sales_url, purchased_ids via _get_purchased_product_ids, valid 0->None, 1->id, 2+ exclude purchased, 1 remains->id, 0->None, 2+-> sorted(price,id) cheapest)
  CommerceStateRequest(user_id, creator_id, product_id, messages[-30:], persona) commerce/state.py:89 :414
  resolve_and_run_commerce commerce/integration.py:113 :421
    resolve_commerce_state commerce/state.py:169 READ-ONLY
      get_user 177, is_user_auto_reply_excluded 180, _resolve_creator_relationship 182 (explicit creator verify or list_offers_for_user latest order), dropfans integration get_dropfans_integration status active? 192, fangate product get_fangate_product 207, has_active_offer find_pending_offer_for_product, has_purchased has_purchased_product 219, evaluate_ppv_eligibility 223, identity/commerce_state ProductIdentity/ProductCommerceState 250, persona fallback get_user_persona 265, segments list_segments + check_user_in_segment 277, timing get_timing_context 306, behavioral get_behavioral_feedback_context 325, CommercePipelineRequest 336, derive_relationship_state 384, derive_commercial_pressure 394, check_tip_eligibility 403, check_operator_handoff 415, is_repeat_purchase_eligible 423, model_copy update relationship/commercial etc 439
    run_commerce_pipeline commerce/pipeline.py:459
      build_conversation_context 477, extract_commerce_signals commerce/deepseek.py:170 (CredentialPool, cheap_model, temp0.0, bounded 30*800, format=json) -> CommerceSignals low_information on fail, _apply_signal_flags 482, handoff re-eval 490, classify_rejection mark_offer_declined 531, decide_from_signals commerce/signals.py:441 -> signals_to_context 254 -> decide_commerce_action commerce/decision.py:272 (23-branch priority), build_strategy commerce/strategy.py:176 (NONE/LOW/MODERATE), orchestrate_commerce commerce/orchestrator.py:157 -> decide_commerce_action 175 -> build_strategy 181 -> execute_ppv if OFFER_PPV && allowed && activation 189 -> commerce/execution.py:87 (11-gate, advisory lock ppv_offer:{c}:{u}:{p}, create_offer_serialized), generate_commerce_response commerce/deepseek_response.py:465, PipelineResult COMPLETED etc, selection select_commerce_response commerce/selection.py:228 PURE
```

### 3.4 Scoring -> Send Queue -> Telegram
```
generate_draft llm_worker.py:81 merge system_parts, dedup trailing user, provider generate_with_history llm_provider ollama qwen2.5:3b or gemini fallback, temp 0.85->provider 0.7, num_predict 200, presence 1.5; generate_draft_with_tools llm_worker.py:178 Gemini tool loop else fallback draft, tool declarations get_gemini_function_declarations 198, supports_tool_calling false for Ollama -> fallback plain draft
score_draft core/scoring.py:78 flag_keywords user/draft lower, LLM json scores 4 dims /40, hard flag cap min 0.1, scoring_failed 0.0
enqueue_send db/redis.py:65 XADD send_messages {entity, content, draft_content, was_edited, was_auto_approved, confidence_score, dedup_id, save_to_db}
_process_send_stream chatbotv2/main.py:77 XREADGROUP send_workers, is_send_duplicate 98, rate limit 1/s burst5 lua, blacklist, get_input_entity, reserve_delivery db/vault.py 225 UNIQUE(creator,user,fangate_media_id) pending, validate_media_path https only, client.send_message 281 or send_file 274, mark_send_dedup 283 ttl3600, ack_send, save_outbound_after_send db/postgres.py 294, finalize_delivery 315, publish message.sent + vault.media_sent
```

### 3.5 Purchase Path: DropFans -> Transaction -> Attribution -> Funnel -> Aftercare -> Delivery
```
DropFans poll reconcile_dropfans_sales commerce/reconciliation.py:251 list_active_creator_ids -> df_service.reconcile_sales per creator -> record_dropfans_sale db/dropfans.py:177 synthetic product_id SHA256%2^62, INSERT fangate_transactions (creator_id, transaction_id=dropfans:{drop_id}, product_id=_synthetic, event_type dropfans_sale) ON CONFLICT DO NOTHING
then reconcile_unattributed_purchases commerce/reconciliation.py:38 SELECT fangate_transactions WHERE user_id IS NULL AND product_id IS NOT NULL 7d window LIMIT50 -> _reconcile_single 114 SELECT commerce_offers WHERE creator+product pending/clicked len0/1/>1 fail-closed, UPDATE commerce_offers state purchased conditional, UPDATE fangate_transactions user_id where NULL, INSERT ppv_analytics_daily UPSERT, PurchaseRecord -> handle_post_purchase commerce/post_purchase.py:170
handle_post_purchase: behavioral _behavioral_store 213, mark_aftercare_pending commerce/dao.py:935 pending, advance_funnel_to_converted 46 (SELECT funnel_stage then UPDATE converted idempotent), enqueue_purchase_confirmation 85 dedup post_purchase:{txn}:{user} + enqueue_send confirmation, schedule_follow_up 282 dedup post_purchase_followup:{txn} + scheduled_messages execute_at +24h, deliver_product_media 349 (dropfans: vaultItemIds from raw, synthetic fangate_media_id sha256%2^31, reserve_delivery UNIQUE, enqueue_send sales_url fallback)
```

### 3.6 Repeat Commerce
```
Aftercare pending -> decision commerce/decision.py:462 AFTERCARE_PHASE RELATIONSHIP_BUILDING suppression unless explicit buy -> Qwen sees Aftercare: pending via context_assembler
Preference learning: post_process memory/profile.py extract_and_update_profile last 10 -> user_profiles.facts interests/preferences cap15
Cooldown: decision recent_purchase 6h, recent_offer 24h, budgets 2/3, fatigue, consecutive 3
Repeat eligibility: commerce/feedback.py is_repeat_purchase_eligible 168h via commerce/state.py:423 and context_assembler.py:597 surfaced Repeat purchase: eligible; NOT wired to auto-offer (decision no branch checks it except render)
Next opportunity: rank_products_by_relevance with fan_preferences overlap -> repeat eligible flag already true but next offer needs new buying intent (0.55) + no active/cooldown
```

For every transition: deterministic owns creator, product, price, URL, purchase, delivery auth, cooldown, dedup; LLM owns wording only (but wording is not given real commercial intent).

---

## 4. Conversation Entry Audit

### First message
Expected: natural introduction NO repeated identity NO generic chatbot greeting NO forced question NO sales pitch.
Actual: **PASS with caveat.**
- Identity: core/conversation_state.py:40 identity_already_established_from_messages regex sunny skye|i am sunny; derive_lifecycle 53 NEW when message_count<=2 else ESTABLISHED/RETURNING 48h gap; memory/context.py:203-212 persona trimmed You are Sunny Skye -> You are sunny + RULE Do NOT re-introduce when established true. First message (count 0-2) correctly gets full persona, later suppressed. fallback message_count>8 -> assumed true 182 prevents re-intro after truncation.
- Greeting: No template greeting exists; memory/context.py:220-230 Rules 2-4 sentences, may have no question, prefer callbacks. No hard-coded Hello fan.
- Sales pitch: decision conversational_phase opening/rapport suppresses commerce unless explicit buy 439. Route fires before LLM.

### Returning fan
Expected: no re-introduction, recognize prior relationship, use known context.
Actual: **PARTIAL.**
- No re-intro: WIRED as above.
- Recognize prior relationship: funnel_stage guidance new/warming/engaged/converted but funnel never advances without purchase (users.funnel_stage stays new at 47 msgs 8151382101). So returning after 3 days with 47 messages still sees Stage: New fan. Warm welcome. Wrong.
- Use known context: profile + history within 800/20/3. No persistent open_threads beyond transient derived topics. No summary unless 20 msgs modulo.

### Existing conversation
Expected: respond to what was actually said, maintain topic, avoid repeating questions, avoid generic filler.
Actual: **PASS for infrastructure, WEAK for execution.**
- Topic continuity: ConversationState cur topic first of 14 keywords within last 8, open_threads capped 3, last_question+answered, tone. Rendered as CONVERSATION: topic= netflix open=[netflix,popcorn] last_q answered tone=flirty 316. Planner uses it.
- Avoid repeating questions: question_policy blocks consecutive and per-3.

Sources of generic filler:
- Grep producer hits 0 prod templates for How is your day phrases. They are **prompt-induced + response-mode induced**. Before C.1-F, persona friendly + legacy Ask one follow-up rule forced it. Now C.1-F compressed rule "A reply may have no question; only ask when genuinely curious" plus QUESTION allowed false for REACT/SHARE reduces. Residual risk remains when LLM ignores QUESTION allowed flag (LLM can ignore). No hard validator strips ?.

---

## 5. Conversational Memory Audit

What Sunny remembers (reach test):

| Fact | Persisted | Derived | In recent history | Injected into LLM | Lost between turns |
|---|---|---|---|---|---|
| fan name | users.first_name | - | - | STATE: Fan header | No |
| known preferences | user_profiles JSONB interests/preferences extracted post_process memory/profile last10 cap15 | - | + in recent 20 | PROFILE: Interests (only age/location/occupation/interests reach Qwen via build_qwen3_state_context 259) | No, but communication_style dropped |
| occupation/schedule | user_profiles occupation | - | + | PROFILE yes when present | No |
| likes/dislikes | preferences | - | + | PROFILE preferences when present | No |
| prior topics | messages durable | _extract_topics last8 14 keywords -> recent_topics/open_threads | + raw history | CONVERSATION: topic open | Yes if not in keywords list (hiking etc not tracked) |
| sexual preferences legit | interests includes sexual activities | - | + | PROFILE interests includes it | No |
| previous purchases | commerce_offers purchased + fangate_transactions | get_purchases_safe count + recent 3 | - | COMMERCE: Purchases: N | No |
| purchased content titles | same | - | - | Purchases: - Title price — date lines but single-product only | Partial |
| previous offers | commerce_offers | _get_active_offers_safe | - | COMMERCE: Active offer + Rejections + Commercial pause | Not full history beyond most recent |
| rejected offers | same | consecutive_rejections via dao loop 10 | - | Rejections: N | No |
| price objections | feedback classify but not persisted except mark_offer_declined | - | + via history | Rejections count only | Price interest lost |
| successful sales | purchases count total_purchases | - | - | Purchases: N | No |
| aftercare | commerce_offers aftercare_status pending/sent | - | - | Aftercare: pending + COMMERCIAL OBJECTIVE aftercare | Conflict aftercare objective fake warm |
| unfinished threads | transient open_threads only (naive copy capped 3, never closes) | - | + raw history | CONVERSATION: open | Degrades — never closes, holds stale work/netflix |
| price mention history | not persisted | - | + | - | Lost unless in recent 20 |

Stored but never surfaced: emotional_state_recent, important_dates, topics_to_avoid, communication_style, mentioned_topics beyond interests, funnel_stage real value beyond new (stale), purchased media_id set (only count), creator_display_name rarely.

Surfaced but not useful: noisy interests (fun, chat, work) low signal for relevance.

---

## 6. Sales Intelligence Audit

### 6.1 Signal Production
LLM extractor commerce/deepseek.py:170 via cheap_model (OLLAMA qwen2.5:3b or gemini) temp0.0 bounded 30*800 JSON with 20 fields; validation strict extra=forbid bounded floats; fallback low_information 0/false/uncertainty1.

### 6.2 Distinctions

| Fan utterance | Expected | Actual system | Pass |
|---|---|---|---|
| "haha" / "nice" | LOW -> relationship no offer | purchase 0.0 decision RELATIONSHIP_BUILDING no offer; LLM sees build_desire but still no pressure | PASS (deterministic suppression, not LLM) |
| "just chilling" / "work was long" | LOW -> relationship | content_curiosity false purchase 0 -> rapport -> RELATIONSHIP_BUILDING | PASS |
| "what are you wearing?" | CURIOSITY -> tease exploration | primary content_curiosity desire CURIOSITY 0.60 but decision still blocks due to rapport phase; LLM mode TEASE via tone flirty if horny keywords else not | PARTIAL |
| "what kind of pics?" | CURIOSITY -> exploration | content_curiosity true -> DESIRE -> TEST_INTEREST but temp cold -> BUILD_DESIRE window BUILDING | PARTIAL |
| "you have anything in red?" | CURIOSITY -> tease vault relevance | purchase 0.3 intent content_curiosity -> DESIRE -> TEST_INTEREST | PARTIAL — vault ranking correct, LLM not told to tease |
| "show me" / "do you have more?" | INTEREST -> qualification | explicit_content true -> OFFER_READY QUALIFICATION 0.85 or OFFER_PPV explicit -> jumps to offer | FAIL interest should not immediate offer |
| "that is hot" / "how much?" | DESIRE -> high temp offer | purchase 0.55+ -> DESIRE -> SOFT_OFFER; how much maps to price_ask -> OFFER_PPV | PARTIAL |
| "how much for the set?" / "send link" / "I will buy" | PURCHASE READINESS immediate offer | explicit_purchase true -> OFFER_READY 0.95 -> decision OFFER_PPV -> executed bypass Qwen | PASS |
| "I will buy it" | same | explicit true -> OFFER_PPV | PASS |

Overall: deterministic engine distinguishes 5 levels via thresholds (0.55 soft, 0.80 strong, explicit). LLM does not — sees same hardcoded COMMERCIAL STATE.

---

## 7. Critical: Active Sales Leading

**Desired:** relationship -> curiosity -> tease -> content interest -> desire -> qualification -> offer, with Sunny leading.

**Actual architecture permits?** **NO — proven from code.**

Active leading would require:
1. LLM receives fan preference (I like red) as evidence -> desire ladder upgrades.
2. LLM receives vault titles ranked high for red -> knows what to tease.
3. LLM receives commercial objective build_desire/test_interest that tells it to tease rather than sell.
4. LLM generates teasing that references vault content without offering.

Steps 1-2 partially wired: AVAILABLE CONTENT does rank Red Lace titles (content_matching relevance). Step 3 is **broken**: workers/llm_worker.py:585 hardcoded desire ignores fan utterance; objective derived from relationship warm, not real desire. So LLM COMMERCIAL OBJECTIVE is always relationship/build_desire.

**Root cause file:** workers/llm_worker.py:585-595 P0 bridge constants not signals.

If bridge passed real signals, architecture *could* lead — planner TEASE/CALLBACK + AVAILABLE CONTENT sufficient for Qwen to tease. Block is not architectural redesign but wiring.

**Spontaneous sale vs leading:** Current does `intent detected -> offer` via sealed pipeline bypass (correct for purchase-ready). It cannot do `conversation -> curiosity -> desire -> qualification -> offer` conversationally.

---

## 8. Response Mode Audit

Modes: REACT ANSWER SHARE EXPLORE TEASE CALLBACK CLARIFY CLOSE core/response_mode.py:14

| Mode | Who selects | Data used | Prompt | Can LLM ignore? | Can deterministic override? | If wrong? |
|---|---|---|---|---|---|---|
| CLARIFY | plan_response_mode rule1 48 | capability_needs_clarify (pic && !send_photo) memory/context.py:514 | RESPONSE: mode=clarify | Yes guidance | Yes scoring photo_promise cap 0.1 | Deflect vs tease error |
| SHARE | rule2 52 | low contains what are you etc + open_threads | mode=share | Yes | No | Share vs callback precedence tweak 54 |
| TEASE | rule tone==flirty 80 | _derive_tone horny/sexy/naughty/hot etc 133 | mode=tease | Yes | No | Wearing red not flirty misses tease |
| ANSWER | rule _is_question 60 | fan message ? + not sexual | mode=answer | Yes | No | how much -> answer not tease correct but should qualify |
| EXPLORE | rule len<=6 68 | fan short fact + last_question answered | mode=explore | Yes | No | Short fact explore vs react |
| CALLBACK | rule open_threads && low contains saturday/netflix/popcorn 76 | open_threads + low substring | mode=callback | Yes | No | Requires open_threads non-empty and exact keyword (only 3 tokens) |
| REACT | default 82 | fallback | mode=react | Yes | No | Safe |
| CLOSE | defined 22 never returned | - | never | - | - | DEAD mode harmless |

Influence: APPENDED as RESPONSE: mode=... 347 + QUESTION allowed. Qwen may ignore but tests show 82/100 human-likeness improvement. No validator enforces mode. Dead/weak: CLOSE never emitted; CALLBACK narrow; TEASE missing fashion.

---

## 9. Question Pressure Audit

Policy: MAX_CONSECUTIVE 1, MAX_QUESTIONS_PER_3_TURNS 1 core/question_policy.py:11
```
evaluate_question_budget(last_question, answered, consecutive, mode, questions_in_last_3)
if mode in explore|clarify:
  if last_q unanswered -> blocked
  if consecutive>=1 -> blocked
  if questions_in_last_3 >=1 -> blocked
  else allow
else non-explore -> blocked reason mode X should not ask
```
Conceptual 15-turn transcript shows Sunny can produce several statements without ?: REACT SHARE TEASE CALLBACK all false. Good. Test shows ~1 per2 historical 8/9 -> now ~1/2.

Can Sunny have several natural statements without interrogative? **YES** blocks enforce, but LLM may still produce ? even when allowed false (no post-validator strips ?). Scoring not penalize many questions. So policy advisory, not enforced.

---

## 10. Commercial State Audit

Values reaching LLM: desire temperature offer_ready sales_window commercial_objective
Actual values per turn (due to hardcoded bridge): desire=interest temperature=warm offer_ready=test_interest window=building objective=build_desire regardless of fan saying "I will buy" or "just chilling". For aftercare objective derived warm -> build_desire not aftercare (because aftercare check needs objective=="aftercare" derived from selection reason with hardcoded warm -> false). So aftercare never aftercare.

Are they actionable? **NO.** Temperature HOT never happens (needs purchase 0.3 boost). Offer_ready READY never happens (needs hot+0.55, purchase 0.0 never). LLM never gets present_offer even when fan purchase-ready; that case bypasses LLM via USE_COMMERCE_RESPONSE anyway.

If temperature HOT + offer_ready true could happen only if purchase intent real, but bridge never passes. So gap: descriptive labels not actionable.

---

## 11. Commercial Objective Audit

File commerce/objective.py:25-56 derive_commercial_objective
Inputs: selection status+reason, relationship_state (hardcoded warm)
Mapping: USE_COMMERCE_RESPONSE -> present_offer; COMMERCE_UNAVAILABLE -> no_sale; FALLBACK with reason mapping _OBJECTIVE_MAP; fallback reason commerce_not_applicable -> relationship -> warm -> build_desire.

Distinct behavior check:
- relationship (no_sale): LLM tells no_sale -> Rule do not invent ... -> Sunny chats normally no sales guidance.
- build_desire: job build_desire — but memory/context has no rule linking build_desire to TEASE instruction; LLM prompt just says job is build_desire, no guidance what build_desire means.
- present_offer: only when commerce bypass already selected; LLM not used. So present_offer objective never seen by LLM in conversational path.

build_desire has no conversational guidance: objective string alone without strategy pressure tones. LLM must infer. present_offer prevents aggressive language? No, because present_offer path bypasses LLM (sealed commerce response via deepseek). So prompt injection present_offer never evaluated.

---

## 12. Vault Content Intelligence

LLM knows per turn: title only via AVAILABLE CONTENT: Title1 | Title2 top2 relevance-ranked titles truncated 42 chars memory/context.py:582. Only titles, not description, tags.

Subject/setting/format/bundle: parsed via vault_taxonomy.py:35 but only for sorting (media_count) not shown to LLM as separate fields. LLM sees only final title string e.g. Red Lace — Bedroom — 3 Photo Set.

Relevance/purchased/availability: purchased excluded via _get_purchased_product_ids 66; is_accessible && sales_url validated; LLM sees only valid unpurchased top2.

Related content example family Red Lace 3 vs 6 vs 10 same tokens red lace bedroom overlap -> relevance identical. Ranking: rel >=0.30 -> sort -rel, -media_count, price, id . So for fan interested in red, larger media_count first -> Mega preferred. But purchase history exclusion may remove 3 set if purchased. LLM could see group as related via title tokens same prefix, not explicit group. Without seeing images, can distinguish same subject/setting different bundle size via format portion.

But title taxonomy not enforced: operator may create IMG_4829 or Hot Pics -> normalize returns subject IMG_4829 no setting, tokens not match, bundle_related false, media_count 0. That title cannot be matched reliably.

---

## 13. Content-Selection Behavior

Influences: fan preference (AVAILABLE CONTENT rank uses fan_preferences), conversation topic (current_topic + open_threads), desire/temperature/purchase history NOT directly, bundle relationship via -media_count when rel >=0.30, price tie-breaker when rel low.

Checks: purchased exclusion YES, creator isolation WHERE creator_id, relevance threshold best_match_or_none 0.15 -> NO_CONFIDENT_MATCH, bundle preference larger when relevant else cheapest, fallback cheapest wins when rel equal or low.

Cheapest wins despite better relevant? Example fan likes red lace titles Red Lace Bedroom 3 Set $20 vs Beach Bikini $5. Relevance red lace 0.66 vs bikini 0.0 -> red wins despite cheaper. Good. When two red products both relevance 0.5, cheaper $10 vs $25 with 3 vs 10 size -> larger 10 wins over cheaper because rel high. For upsell. Situation where cheapest wins despite relevant: If relevance calculation misses due to keyword not in title (fan says cozy vibes but title Red Dress Balcony) -> relevance 0 for both then cheapest $5 bikini wins over $20 relevant red. Wrong. Token overlap only, no synonym.

---

## 14. Content Naming Audit

Current expected taxonomy Subject — Setting — Format per vault_taxonomy: Subject lower, Setting lower, Format lower (e.g. red lace — bedroom — 3 photo set).

Supports: single maybe Red Lace — Bedroom — Photo (media_count none) still group, small set 3 Photo Set -> set, bundle 6 Photo Bundle -> bundle, mega 10 -> mega bundle cnt>=8, video 2 Video -> word video, mixed media not handled.

Ambiguous titles: IMG_4829 (opaque, len <4 still attempted but tokens img not match), Campaign set, Hot Pics (parse subject hot pics setting None), new drop, set 3 (parse wrong). Search ILIKE balcony vs beach vs bedroom synonym fails.

Cannot reliably match: titles without subject/setting (Sunset Vibes — 5 Photos -> subject sunset vibes setting 5 photos -> wrong). Taxonomy brittle.

No validation enforced: fangate create_drop name non-empty only 368.

---

## 15. Purchase History Audit

Can Sunny distinguish:
- never purchased: purchases 0, has_purchased false, aftercare none -> desire RELATIONSHIP, decision rapport -> relationship building. AVAILABLE CONTENT cheapest. Correct.
- offered but not purchased (pending/clicked): COMMERCE Active offer line -> decision OFFER_EXISTS NO_OFFER, pending age maybe >48h -> Abandoned offer line. Can Sunny accidentally re-offer? product_selection returns same product if pending but decision blocks new offer via has_active_offer. OK.
- purchased: Purchases:1 + Title -- date -> decision recent_purchase cooldown 6h -> NO_OFFER, aftercare pending. Suppressed.
- purchased recently (hours <6): same + aftercare suppression + purchase cooldown -> no sale.
- purchased long ago (hours >168): repeat_purchase_eligible true -> rendered Repeat purchase: eligible ; decision not auto offer — next offer needs new buying intent.
- currently in aftercare (pending/sent): decision AFTERCARE_PHASE RELATIONSHIP_BUILDING unless explicit buy -> suppress.
- eligible for repeat (168h): flag true, not branch.

Previously purchased content accidentally offered again? No: product_selection filters purchased DISTINCT product_id, rank excludes pid in purchased_ids, so reselling same product impossible unless product_id differs but same vault items via new drop (different synthetic id not in set) -> loophole same media offered again via new product_id.

---

## 16. Offer Experience Audit

Trace when offer becomes ready:
- Who chooses product: commerce/product_selection.py:149 resolve_commerce_product_with_history -> cheapest unpurchased creator-scoped. Not LLM (LLM AVAILABLE CONTENT but selection cheapest). LLM proposal tool propose_product_offer unreachable under Ollama supports_tool_calling false.
- Who chooses price: db.fangate_products price_minor int(price*100) at upsert 114. Execution copies price_minor 251. No LLM invent, validators reject invent.
- Who creates DropFans offer: NOT created at offer time. Product already exists as drop created via operator dashboard integrations/dropfans/service create_drop 426 POST /drops. Offer creation is INSERT commerce_offers pending with link.
- Who generates URL: local fangate_products sales_url or build_checkout_url dropfans/service 465 telegram buy_template. Authority deterministic.
- Who writes text: If selection USE_COMMERCE_RESPONSE -> commerce/deepseek_response via deepseek temp 0.0 with VERIFIED FACTS + StrategyInstruction OFFER_PPV. Validators _urls_are_authoritative, _prices_are_authoritative. If not USE -> Qwen generate_draft with no offer.
- Who sends: workers enqueue_send SEND_STREAM -> _process_send_stream Telethon.

Expected split LLM framing + deterministic product/price/URL/DropFans paywall + send queue -> **PARTIALLY** correct. LLM framing for offer is not Qwen phrasing via COMMERCIAL STATE; it is deepseek templated present_offer via pipeline. Qwen build_desire never writes offer; only pipeline writes.

No leakage: LLM could invent price via Qwen even when deterministic not yet created, but scoring price_mention cap 0.1 + queue, tool propose validated. No leakage.

---

## 17. Offer Timing Audit

- Fan clearly ready "send me link": Expected do not over-chat offer promptly. Actual signals explicit_purchase true -> decision OFFER_PPV explicit -> execute -> selection USE -> bypass Qwen -> immediate offer. **PASS**
- Fan curious but not ready "what kind of pics?": Expected build desire. Actual primary content_curiosity -> desire CURIOSITY -> decision not strong -> soft? But rapport block -> RELATIONSHIP_BUILDING. No offer. LLM mode EXPLORE but question_allowed maybe blocked. **PARTIAL** build desire via chat but no deterministic step.
- Fan cold "haha nice": Expected relationship. Actual purchase 0 relationship cold confidence low -> LOW_CONFIDENCE -> RELATIONSHIP_BUILDING -> Qwen REACT. **PASS**
- Fan rejected "not now maybe later": Expected cool down no immediate re-pitch. Actual SOFT hesitation not marked declined, negative 1, pending maybe false, hours <24 -> COOLDOWN_ACTIVE -> NO_OFFER. So no re-pitch. Correct but SOFT path not strict cooldown.
- Fan changed topic horny -> work: Expected follow conversation temperature decays. Actual derive_conversation_state topic changes, _extract_topics still holds old netflix in last8 stale. Temperature fatigue not decay per topic. So temp stays warm not decay. **FAIL**
- Fan returns later 48h gap: Expected new sales window based new evidence. Actual derive_lifecycle RETURNING gap>=48, but abandoned pending still exists -> decision OFFER_EXISTS blocks new offer -> no window. **PARTIAL**

---

## 18. Objection Handling

| Objection | Detected | Response | Differentiated? |
|---|---|---|---|
| too expensive | PRICE_OBJECTION price_interest>=0.60 + hesitation -> 191 | mark_offer_declined reason price_objection 541 for HARD/PRICE only; strategy never invented discounts | PARTIAL — classification yes, handling suppression not tailored |
| maybe later | SOFT hesitation ->203 | NO mark declined, negative 1, continues relationship | WEAK same as generic |
| not now | same SOFT | same | same |
| I am broke | not distinct from too expensive -> likely PRICE if price_interest present else SOFT | same as too expensive if tagged hesitation+price else generic | WEAK |
| not interested | HARD rejection 195 | mark_declined HARD consecutive 1 -> REJECTION_ESCALATION at 3 | PARTIAL |
| just chatting | asks_for_free_content or no commercial -> RELATIONSHIP_BUILDING | suppress | WEAK not differentiated from haha |
| send free | asks_for_free_content true -> decision 321 RELATIONSHIP_BUILDING suppressed | suppress | PASS distinct |
| show me first | ambiguous explicit_content true -> OFFER_PPV vs soft | if explicit_content true -> OFFER_PPV which is wrong should qualify | FAIL over-eager |

All objections eventually funnel to cooldown/Rejection escalation. Not sufficient: price objection context lost, no differentiated recovery.

---

## 19. Silence / Abandoned Offer

- When created: INSERT commerce_offers state pending via create_offer_serialized 121 advisory lock, expires_at optional.
- When surfaced: context_assembler.py:801-814 if has_active_offer && age_h>=48 -> Abandoned offer: {title} ({Nh} ago) line rendered; memory/context.py 285 aftercare keyword includes but abandoned not in commerce filter? Since contains offer -> kept. LLM sees it.
- Can Sunny naturally reference it? CALLBACK mode when fan returns with netflix/popcorn/saturday but not abandoned token. Title Red Lace not in 3 keyword list -> not callback. Reference unlikely.
- Does it become spam? No automatic nudge scheduled. FOLLOW_UP action requires previous_offer_status in declined etc + cooldown clear 528 but selection treats FOLLOW_UP as NON_EXECUTING 80 -> FALLBACK not sent. So spam avoided.
- Automatic outbound? No scheduler polls pending>48h to auto-send. Only post_purchase followup 24h exists.

Distinction: contextual callback (when fan returns LLM may see Abandoned offer line) vs automatic outbound (cron). System has only former, not latter — documented gap, correct anti-spam.

---

## 20. Purchase Event Audit

DropFans check_drop_status client.py:478 POST /drops/check-status {productIds} -> maps sales paid true 491 -> service reconcile_sales.

Reconcile: db/dropfans.py:177 record_dropfans_sale synthetic pid persists fangate_transactions with user_id NULL initially. transaction_id = dropfans:{drop_id} (not per-sale unique! Same product = same transaction_id, multiple sales cannot be distinguished via polling). Then reconciliation.py:38 finds WHERE user_id IS NULL AND product_id IS NOT NULL 7d LIMIT50 -> _reconcile_single 114 finds commerce_offers pending/clicked for creator+product fail 0 or>1, update purchased, update transaction user_id where NULL, insert ppv_analytics_daily.

Can any branch still produce product_id=NULL? **NO** after fix 187 synthetic pid. Before fix yes; now fixed. ON CONFLICT DO NOTHING unique (creator,transaction_id,event_type) duplicate polling no duplicate.

Unattributed purchase? Only if multiple pending offers for same product -> ambiguous fail-closed false -> remains unattributed. Correct but lossy.

Duplicate transaction? ON CONFLICT DO NOTHING idempotent.

Incorrect creator? WHERE creator_id throughout.

Cross-user ambiguity: reconciliation queries by creator+product without user_id, if 10 users have pending same product and one sale occurs, query returns 10 candidates -> ambiguous -> never attributed. Real attribution requires buyer_email.

---

## 21. Delivery Audit

DropFans paywall -> purchased -> delivery reservation -> send queue -> Telegram -> finalization.

For DropFans path deliver_product_media 402: sales_url is checkout URL not download URL. Synthetic fangate_media_id sha256(dropfans:{product_id})%2^31 424. Reservation reserve_delivery UNIQUE(creator,user,fangate_media_id) -> finalize pending->sent.

What fan receives: chatbotv2/main.py _process_send_stream: if media_path is sales_url (https) and media_type photo -> send_file input_entity, sales_url, caption "Your content is ready! Access it here: {sales_url}" 479. So fan receives **checkout/sales URL fallback**, not vault item file. No per-vaultItem download_url fetched.

If fallback, explicitly **NOT equivalent to actual media delivery**. Report must state: fallback NOT delivery.

Fan does NOT receive actual purchased media bytes inside Telegram; they receive link to DropFans to access content (paywall delivery, not Telegram media delivery).

---

## 22. Aftercare Audit

Purchase -> mark_aftercare_pending none->pending commerce/dao.py:935 -> Qwen sees COMMERCE Aftercare: pending + COMMERCIAL OBJECTIVE aftercare (if bridge worked) + RELATIONSHIP_BUILDING AFTERCARE_PHASE suppression 462.

Expected: purchase -> acknowledge -> allow reaction -> learn preference -> relationship -> cooldown -> future opportunity.

Actual:
- Acknowledge: enqueue_purchase_confirmation 85 deterministic "Your purchase is confirmed! Your content is now available." No LLM variation, correct.
- Allow reaction: No explicit prompt to ask reaction, but post-purchase followup 24h later says "Hey! Just checking in — hope you are enjoying it!" 40 — generic, not tailored.
- Learn preference: Aftercare reaction via profile extract could add interests but post_purchase_satisfaction always null.
- Persistence: aftercare_status pending/sent/completed; mark_aftercare_completed never invoked except manual? Grep 0 callers found. So aftercare stays pending forever -> decision suppresses offers indefinitely unless explicit buy overrides.
- Does Sunny immediately say Want another bundle? No, due to aftercare suppression plus commercial pause. Correct.
- What extracted? Only generic interests from last10, not satisfaction.

Verdict: Aftercare suppression correct, but preference learning and cooldown release not complete; aftercare never transitions to completed automatically.

---

## 23. Repeat-Purchase Audit

168h eligibility: commerce/feedback.py is_repeat_purchase_eligible requires total_purchases>=1, hours_since_last_purchase >168, engagement <7d, satisfaction != negative, no recent rejection <48h. Derived in commerce/state.py 423 (hours None -> defaults) context_assembler 598 with hours None -> repeat eligible if total_purchases>=1 + engagement true and not paused -> may be true early, not accurate.

Does eligible cause new opportunity or merely flag? Merely flag: rendered as Repeat purchase: eligible line 798, surfaced to LLM via commerce filter and to decision via repeat_purchase_eligible field (decision has no branch that checks it except metadata). No auto-offer; next sales window still requires new buying intent.

No what actually causes repeat: Fan must initiate new topic relevant to new unpurchased product; LLM curiosity may surface AVAILABLE CONTENT new product; then moderate buying signal needed. No proactive outbound.

---

## 24. LLM Prompt Audit

Reconstructed final prompt reaching Qwen2.5:3b per build_qwen3_context + llm_worker bridge:

Order: IDENTITY system persona + Fan header + Stage + Rules (6 lines) + STATE-PROFILE-RELATIONSHIP-COMMERCE-SUMMARY-IDENTITY-CONVERSATION-ABOUT SUNNY-CAPABILITIES-RESPONSE-QUESTION block 252 + AVAILABLE CONTENT top2 + COMMERCIAL STATE + OBJECTIVE bridge injected by llm_worker 595 + Recent history last 20 trimmed to 800 tokens, 3 assistant turns, plus current user message dedup.

Example reconstructions (hypothetical LLM output labelled):

A) new fan "hi"
- identity not established -> full persona You are Sunny Skye...
- Fan: Alex Interests: none Stage: New fan. Warm welcome.
- COMMERCE: Funnel stage: new; Purchases: 0
- IDENTITY: established=false lifecycle=new
- CONVERSATION: topic=null open=[] tone=warm
- RESPONSE: mode=react question false
- AVAILABLE CONTENT: Red Lace — Bedroom — 3 Photo Set | Red Lace — Bedroom — 6 Photo Bundle
- COMMERCIAL STATE: desire=interest temperature=warm offer_ready=test_interest window=building OBJECTIVE build_desire
=> hypothetical Qwen: "Hey Alex! I am sunny — love cozy nights and late chats, what is your vibe today?" no offer

B) fan discussing Netflix "Netflix and popcorn tonight"
- topic netflix open [netflix,popcorn] tone warm mode CALLBACK question false AVAILABLE CONTENT cheapest wins -> Red Lace still
- COMMERCIAL STATE same interest/warm/test_interest/building/build_desire (hardcoded, not updated for netflix)
=> hypothetical "Mmm cozy night done right — popcorn is non-negotiable" statement no question

C) fan expresses interest in red outfit "I like when you wear red"
- topic red open [red] relevance red lace titles score ~0.33 -> AVAILABLE CONTENT now Red Lace set ranked first correctly.
- Tone warm (red not flirty) -> mode REACT not TEASE -> wrong. LLM should tease but mode not tease because red not in flirty keywords.
- COMMERCIAL STATE still hardcoded interest/warm/test_interest -> LLM told build_desire generically, not specifically tease red.
- Hypothetical good: "Red is my favorite on me too — thought you would say that" vs actual generic "Nice! Red is great."

D) fan clearly ready "how much for the set?"
- signals price_ask true -> would be true but bridge still hardcoded interest/test_interest, not READY. However _try_commerce_draft will succeed before LLM path: decision OFFER_PPV -> execute -> selection USE -> draft bypasses Qwen entirely, so prompt audit irrelevant; commerce response via deepseek present_offer bypasses LLM. Correct.

E) fan rejected previous offer "maybe later"
- No mark declined (SOFT), but age <24 -> decision RECENT_OFFER COOLDOWN_ACTIVE -> RELATIONSHIP_BUILDING. COMMERCE line Rejections maybe 0. LLM sees ACTIVE OFFER? Actually offer still pending, has_active_offer true -> OFFER_EXISTS NO_OFFER. COMMERCIAL STATE bridge still interest/warm/test_interest/build_desire (hardcoded) — does not reflect cooldown or aftercare. So LLM told to build_desire when deterministic says cooldown active -> mismatch.

F) fan just purchased (aftercare pending)
- COMMERCE now includes Aftercare: pending and Purchases:1; relationship maybe purchased; but llm_worker bridge derives _is_aftercare from objective=="aftercare" which depends on selection reason with hardcoded warm -> false, so window not aftercare, COMMERCIAL STATE still interest/warm/test_interest/building. Qwen not told aftercare. However context_assembler LLMContext does have Aftercare: pending, and build_qwen3_context commerce_text does include it, so Qwen does see Aftercare: pending in COMMERCE block, but commercial objective not aftercare. So mixed signal: sees aftercare in facts but objective says build_desire -> conflict.

For Qwen 3B, can it realistically execute? No — conflict warm build_desire vs real cooldown/aftercare, missing pressure instruction, vault titles without price, 200 token limit 2-4 sentences, instruction conflict be conversational/warm/sell subtly/ask questions/do not ask too many etc without priority.

---

## 25. Qwen2.5:3B Specific Audit

Current provider ollama/qwen2.5:3b 1.9GB via https://ollama.brestalogistics.co.ke core/config.py:89, temp 0.7/0.8/1.5 num_predict 200 timeout 120 core/llm_provider_ollama.py.

Prompt design appropriateness for 3B:
- Competing objectives: "be conversational be warm sell subtly ask questions do not ask too many build desire do not pressure present offers do not invent content" — Single system prompt contains many structured blocks. Risk instruction following degrades, mid-context dilution.
- Instruction conflicts: "Prefer callbacks" vs "Vary sentence structure avoid repetition" vs "Only ask when genuinely curious" vs "QUESTION allowed=false" vs "RESPONSE mode explore should ask" — no priority order.
- Long context: system 400 + state 200 + conversation 800 + summary 200 + vault top2 ~30 = 1630 input, within 4K but heavy for 3B.
- Tokenizer tiktoken gpt-4 not qwen tokenizer ~15% off, may undertrim.
- Redundant instructions: STATE Fan|new + PROFILE repeated?
- Ambiguous priorities: When commercial_state says build_desire but question_allowed false, should LLM tease statement vs explore question? Not defined.
- Missing examples: No few-shot example of tease without selling, how to frame offer, how to handle aftercare, how to reference abandoned offer.

Overall: Architecture could work with larger model or concise priority-ordered prompt + examples. Current prompt not optimized for 3B.

---

## 26. Fallback Audit

| Failure | Fallback | Generic danger | Sales pressure | Duplicate | Price |
|---|---|---|---|---|---|
| LLM failure generate_draft returns "" | workers 122 return "" -> operator queue with [No response generated] 753 publish completed false | Safe no generic, queued | No | Prevented | No |
| scoring failure | core/scoring 119 scoring_failed true -> composite 0.0 129 -> operator queue | Safe | No | No | No |
| commerce failure signals fails -> low_information | decision rapport/low_conf -> RELATIONSHIP_BUILDING no offer | Safe | No | No | No |
| DropFans failure on execute_ppv build_checkout_url | commerce/execution 244 DropfansError -> PROVIDER_ERROR -> FALLBACK not sent | Safe | No | No | No |
| entity resolution invalid peer | chatbotv2/main 149 ValueError -> blacklist + DLQ | Safe | No | No | No |
| vault reservation failure | main 245 DLQ delivery_reservation_failed -> skip send | Safe | No | No | No |
| tip suggestion failure | core/llm_tools dispatch ToolResult success false -> safe_message Could not retrieve | Generic but not salesy | No | No | No |
| duplicate offers advisory lock | commerce/dao 82 lock + pending check 88 -> ALREADY_EXECUTED | Duplicate suppressed via dedup md5 817 | No | Dedup prevents | No |

Fallbacks fail-closed to queue/relationship, safe but not conversationally graceful.

---

## 27. Duplication Audit

| Concept | Computing A | Computing B | Authority | Inputs differ | Can disagree? |
|---|---|---|---|---|---|
| desire | commerce/desire.py derive (hardcoded warm) | commerce/signals->decision buying_intent+primary_intent | B authoritative | A warm constant, B real signals | YES LLM sees interest while pipeline sees OFFER_READY |
| commercial temperature | commerce/temperature.py derive (rel0.30+desire0.70) | commerce/relationship.py derive_commercial_pressure + decision cooldown | B | A hardcoded 0.35, B real timing | YES |
| offer readiness | commerce/offer_readiness.py evaluate (desire+temp+intent 0) | commerce/decision.py decide (eligibility+active+purchase cooldown etc) | B | A 0 intent always NOT_READY, B explicit buy leads OFFER_PPV | YES |
| sales window | commerce/sales_window.py derive (desire+temp+readiness synthetic) | commerce/strategy.py SalesPressure | B | A synthetic, B PURE policy | YES duplicate taxonomy |
| product selection | commerce/content_matching.py rank by relevance (prompt) | commerce/product_selection.py resolve cheapest (execution) | B execution authority | A relevance vs B price | YES divergence prompt red vs execution bikini |
| strategy | commerce/strategy.py build_strategy | llm_worker bridge commercial objective + temp (hardcoded) | B | A from decision, B from hardcoded | duplicate naming |
| provider selection | core/llm_provider.py get_llm_provider | workers/llm_worker generate_draft_with_tools direct Gemini SDK call 175 bypass | Conflict | - | P1-4 bypass inert under Ollama false |
| aftercare | commerce/dao mark_aftercare 935 vs feedback _behavioral_store append only | DAO durable, feedback dead | DAO authority | - | Duplicate store dead |

Two components calculate same concept with different formulas without shared enum — P2 duplication.

---

## 28. Dummy / Placeholder / Fake-Data Audit

- TODO/FIXME: grep commerce/core 0 hits. None.
- pass: bare pass only in workers dedup check 101 intentional and telemetry try pass blocks — not dummy.
- return None / return "": heavy in commerce as fail-closed pattern (product_selection 137 None for 0 valid, content_matching 117 None for no ranked 0.15, attribution 42 None for no pending offer). Not dummy, safe fallback. workers generate_draft returns "" on exception.

Hard-coded URLs: core/config fangate https://fangate.info/api 52, dropfans https://www.dropfans.io 57, ollama https://ollama.brestalogistics.co.ke 89 — canonical base via settings, not dummy.

Hard-coded product IDs: none. Synthetic IDs deterministic SHA256, not fixture.

Fake profile facts: _SUNNY_SELF_FACTS hard-coded 3 curated lines in core/persona_self.py 21 enjoy cozy movie nights etc — intentional authoritative self-facts, not fake fan data.

Vault dead columns dropfans_media_id TEXT never written but synthetic id used instead — intentional dead not bug.

No production dummy defect found (no return pass TODO placeholder that corrupts money).

---

## 29. Unwired / Dead Logic Audit

| Component | Status |
|---|---|
| commerce/attribution.py attribute_purchase 26 | DEAD 0 prod imports; real webhook uses commerce/dao attribute_purchase_from_webhook; tests only |
| commerce/follow_up.py | MISSING (no file) — logic lives in post_purchase schedule_follow_up 282 via scheduled_messages |
| commerce/feedback.py _behavioral_store append 213 | PARTIAL DEAD — append-only never read by production |
| commerce/desire.decay_desire 127 | DEAD never called except tests; decay not applied per turn |
| core/capability_contract _THINKING_LEAK_PATTERNS 55 | DEAD defined but never invoked |
| core/response_mode.CLOSE | DEAD defined 22 never returned |
| agent/loop.py generate_with_tools signature mismatch 189 | BROKEN even if enabled canary false -> never runs; if toggled would raise AttributeError |
| agent memory retrieve_relevant_history | DEAD for Qwen path (only legacy build_context 419) |
| memory/context.build_context legacy | DEAD never called in prod (build_qwen3_context authoritative) |
| commerce/state derive_relationship_state funnel_stage None hardcode 385 | WEAK intentionally ignoring DB funnel_stage |
| affinity derive_commercial_temperature callers | Only llm_worker bridge (fake) + tests; no prod commerce consumer |
| vault dropfans_media_id columns | DEAD never written/read |
| scheduled_messages abandoned auto-nudge cron | DEAD not implemented — abandoned detection render only, no scheduler polls pending>48h |
| is_repeat_purchase_eligible use | Called to set flag but flag never branches decision to auto-offer -> dead branch |

---

## 30. End-to-End Scenario Matrix (20 scenarios)

| # | Scenario | Current Path | Expected Path | Actual Path | PASS/PARTIAL/FAIL | Root Cause File:Line |
|---|---|---|---|---|---|---|
|1| New fan neutral hi | state cold new -> rapport -> RELATIONSHIP_BUILDING -> Qwen REACT | warm welcome no sales | sees interest/warm/test_interest/building build_desire -> Qwen may tease slight but no sales -> near pass | PARTIAL | llm_worker bridge warm constant |
|2| New fan immediate sexual interest horny lol | tone flirty -> TEASE, purchase 0, low_conf maybe RELATIONSHIP_BUILDING -> no offer | tease build desire not immediate offer | TEASE correct decision suppress correct -> PASS | core/conversation_state tone 133 |
|3| New fan asks what Sunny is wearing | wearing not in TEASE keywords -> ANSWER | curiosity -> tease | Mode ANSWER generic, not tease; no sales | FAIL | response_mode 62 TEASE gate narrow |
|4| Fan talks about work | topic work -> EXPLORE -> question allowed -> Qwen explore | relationship building ask about work | PASS | response_mode 68 |
|5| Fan reveals preference I like red | preference stored in profile interests, AVAILABLE CONTENT ranks red lace high | remember preference curate vault tease | Preference stored but lag 1 turn + relevance correct -> second turn yes | PARTIAL | profile lag post_process 889 |
|6| Fan changes topic repeatedly | open_threads holds 3 recent but never closes, last8 keywords keep stale | follow new topic temperature decays | Stale open, temp not decay, no decay_desire call | FAIL | desire decay dead 127 |
|7| Fan expresses curiosity what kind of pics? | primary content_curiosity -> desire CURIOSITY -> decision not strong -> soft? | tease explore not offer | EXPLORE vs TEASE ambiguous -> generic | PARTIAL | decision 439 rapport vs curiosity |
|8| Fan likes specific outfit I love red lace | purchase 0.3 desire DESIRE -> decision moderate maybe SOFT_OFFER if warm | strong tease qualification prepare relevant bundle | SOFT_OFFER would allow price/product conditional but LLM not told product relevance | PARTIAL | product_selection cheapest vs relevance |
|9| Fan asks for more do you have more? | explicit_content true -> QUALIFICATION 0.85 or OFFER_PPV | should qualify not offer | FAIL over-eager explicit triggers offer | desire 87 explicit_content -> OFFER_PPV |
|10| Fan clearly asks price how much? | price_interest 0.8+ explicit -> OFFER_PPV explicit | immediate offer | PASS executes | PASS |
|11| Fan is purchase-ready send me link | explicit_purchase true -> OFFER_READY 0.95 -> decision OFFER_PPV -> executed | immediate offer promptly | PASS | execution 87 |
|12| Fan hesitates too expensive | PRICE_OBJECTION -> mark_declined price_objection if hard | differentiated handling empathy not discount | Text not tailored but decision suppresses -> PARTIAL | feedback 191 |
|13| Fan says maybe later | SOFT hesitation -> not marked declined, negative 1 | cooldown no immediate re-pitch | Decision negative 1 <2 -> not suppressed, but hours <24 -> COOLDOWN_ACTIVE -> NO_OFFER? Actually previous status decl? Not, so maybe still SOFT_OFFER allowed -> could re-pitch incorrectly | FAIL | decision 415 negative >=2 only |
|14| Fan requests free content send free | asks_for_free true -> RELATIONSHIP_BUILDING suppressed | no promotion maintain relationship | PASS | decision 321 |
|15| Fan rejects not interested | HARD rejection -> marked declined consecutive 1 -> RECENT_DECLINE cooldown 24h -> NO_OFFER | cooldown no re-pitch | PASS for first rejection | PASS |
|16| Fan purchases poll | reconcile_unambiguous -> handle_post_purchase -> aftercare pending -> scheduled + reserve + sales_url | attribution funnel aftercare delivery | Synthetic sales_url fallback not media -> PARTIAL | post_purchase 424 |
|17| Fan receives aftercare | Aftercare pending -> decision AFTERCARE_PHASE RELATIONSHIP_BUILDING -> no offer | warm aftercare not upsell | Conflict but suppression correct -> PARTIAL | llm_worker 591 |
|18| Fan returns after several days dormant | derive_lifecycle RETURNING 48h but abandoned pending still exists -> OFFER_EXISTS blocks new offer | new sales window | FAIL | dao has_active_offer true forever |
|19| Fan purchased one bundle wants something new after 168h | repeat eligible true purchased ids excludes old rank picks new relevant | relevant new content upsell tier | PASS ranking correct but no tier price ladder | PARTIAL | content_matching 94 |
|20| Fan attempts to buy already-purchased content | has_purchased true -> eligibility already_purchased -> ELIGIBILITY_DENIED | block duplicate offer already owned | PASS but edge duplicate vaultItems via new product_id bypass exclusion -> FAIL | execution 211 |

Overall 20: 4 PASS, 8 PARTIAL, 8 FAIL — indicates system detects ready buyers but fails conversational sales progression.

---
---

## 31. Full Sales Lifecycle Scenario (Deterministic Walkthrough, Actual Code)

Constructed hypothetical fan "Alex" 24 turns, labeling actual vs hypothetical LLM:

**Turn1: Fan enters "hey"**
- DB: upsert_user funnel new, message_count1, no offers
- Context: recent 1, persona Sunny Skye full, lifecycle NEW, identity false, topic null, mode REACT, question false, commerce Purchases0, vault top2 cheapest
- Pipeline: signals greeting 0 purchase, decision rapport -> RELATIONSHIP_BUILDING no_offer, selection FALLBACK, bridge desire interest/warm/test_interest/building/build_desire (hypothetical Qwen: "Hey Alex! I am sunny — love cozy nights and late chats, what is your vibe today?" no offer)

**Turn2-5 Relationship building**
- Fan: "work was long" -> topic work, mode EXPLORE, Qwen asks work. Profile stores occupation employee post lag. No commerce.

**Turn6-8 Preference discovered**
- Fan Turn6: "I like when you wear red" -> history includes red, _extract_topics now red? red not in 14 keywords! So current_topic still work, not red. AVAILABLE CONTENT ranking: fan_preferences includes red from profile only next turn after post_process, not yet. So ranking still cheapest. LLM mode REACT -> generic "Red is one of my faves too" maybe but not guaranteed. **Weak:** red not recognized as topic due to keyword list, preference lag 1 turn.

**Turn9-12 Curiosity**
- Fan: "what kind of pics do you take? Do you have anything in red?" -> explicit content_curiosity? Question triggers ANSWER, desire CURIOSITY 0.60 but decision rapport? not suppressed. If confidence >0.30 falls through to moderate 0.55? purchase 0 -> not moderate. Then relationship_ready? maybe warm -> SOFT_OFFER possible. Selection FOLLOW_UP? Actually decision SOFT_OFFER action soft but selection treats SOFT_OFFER as NON_EXECUTING -> FALLBACK, no commerce text, Qwen handles. Mode ANSWER should answer curiosity.

**Turn13-16 Desire**
- Fan: "that is hot I want to see it" -> purchase 0.6? desire DESIRE -> decision moderate 0.65 SOFT_OFFER -> execution not run (soft not OFFER_PPV). So still fallback. LLM mode TEASE if tone flirty hot -> yes TEASE.

**Turn17-18 Qualification**
- Fan: "Do you have a red lace set? Show me" -> explicit_content true -> derive_desire QUALIFICATION 0.75, decision explicit -> OFFER_PPV 0.95 -> execution gate: resolve product cheapest red lace? Actually cheapest among unpurchased is maybe Beach Bikini $5 cheaper than Red Lace $20, so execution picks Beach Bikini not red — mismatch!

**Turn19 Offer**
- If execution picks Red Lace (if only one valid red or cheapest is red) -> execution serialized pending, strategy allow_price true, link sales_url https://www.dropfans.io/buy/<id>. Deepseek present_offer via VERIFIED FACTS. Scoring hard_flag price_mention caps to 0.1 but commerce response bypass? Scoring still runs hard flag -> price_mention -> score 0.1 <0.80 -> operator queue not auto-send! Every offer goes to operator queue. That blocks auto-approval.

**Turn20 Purchase** — fan clicks link, pays on DropFans, poll records, reconcile finds pending single -> purchased, handle_post_purchase -> funnel converted, aftercare pending, confirmation enqueue, followup 24h, delivery synthetic.

**Turn21-24 Aftercare** — fan says "that was hot!" -> profile extract. Aftercare pending -> decision suppress, LLM sees aftercare via commerce block but objective mismatch; no upsell. Good.

**Later Repeat** — after 168h, repeat eligible flag, new interest red lace again? Already purchased red lace would be excluded, next AVAILABLE CONTENT picks Beach Bikini. Fan says "got more in black?" -> relevance black lace high -> ranking new product -> SOFT_OFFER if warm.

**Where implementation breaks:** Turn6 topic miss, Turn8 product selection cheapest not relevance, Turn13 soft vs explicit misclass, Turn17 offer product mismatch, Turn19 scoring blocks auto-send, Turn24 aftercare never completes, later pending blocks new window, purchase delivery fallback not media.

---

## 32. Scoring (0-100 per dimension)

| Dimension | Score | Reason |
|---|---|---|
| Conversation naturalness | 75 | C.1-F fixed repetition/question loops, 82/100 human-likeness, but generic tease due to mode keywords narrow, hardcoded state |
| Memory continuity | 60 | Profile+history+summary present but keyword limited 14, open_threads stale, summary often None at 42 msgs |
| Relationship building | 65 | Lifecycle suppresses re-intro, warm path exists but funnel_stage stuck new, no staged rapport ladder |
| Active sales leading | 25 | **Critical FAIL** hardcoded bridge prevents LLM leading; pipeline can sell ready, not lead curiosity->desire |
| Intent recognition | 70 | DeepSeek 20 intents bounded floats, but Qwen small model reliable moderate |
| Desire progression | 30 | Desire ladder pure but dead wiring, temperature duplicate, decay unused |
| Commercial timing | 55 | Deterministic timing correct (cooldown budgets), but LLM timing mismatch, abandoned pending blocks new window |
| Vault relevance | 70 | Title-token relevance + bundle aware + purchased exclusion + AVAILABLE CONTENT top2 wired; weak for opaque titles |
| Offer quality | 50 | Product existence/price/URL authority sealed, but cheapest vs relevance divergence + scoring price Mention blocks auto-send |
| Objection handling | 40 | Classify HARD/SOFT/PRICE distinct, but no tailored recovery wording |
| Purchase attribution | 75 | synthetic pid fix, bounded idempotent, but polling transaction_id non-unique cross-user ambiguous |
| Content delivery | 20 | **FAIL** reservation idempotent works, but fan receives sales_url not media bytes; owner filePath never leaked correct but not delivery |
| Aftercare | 45 | pending suppression correct, but aftercare never completes automatically |
| Repeat sales | 35 | 168h eligible flag but no auto window, no outbound |
| LLM prompt quality | 40 | Compact but conflicting objectives, no priority, no examples, long blocks, mismatch |
| Fallback safety | 85 | All fallbacks fail-closed to queue/relationship, safe |
| Authority separation | 90 | LLM never owns price/URL/product/purchase, deterministic owns |
| Creator isolation | 95 | WHERE creator_id everywhere |
| Idempotency | 85 | Advisory lock, send dedup, vault reservation UNIQUE, but poll transaction_id collision breaks multi-user |
| Observability | 45 | Telemetry 8 fields in-memory but table not widened, token counts missing |

Average ~53, not production-ready for AI-native commerce but safe.

---

## 33. P0/P1/P2/P3 Findings Classification

| ID | Severity | Type | File:Line | Problem | Impact |
|---|---|---|---|---|---|
| F-01 | P0 | BROKEN | workers/llm_worker.py:585-595 | COMMERCIAL STATE bridge hardcoded warm 0.35 desire primary None — LLM never sees real signals | Money: LLM cannot lead, conversion gap |
| F-02 | P0 | BROKEN | commerce/post_purchase.py:424-479 | Delivery fallback is sales_url not media bytes — NOT actual media delivery | Money: fan pays but receives link not Telegram media |
| F-03 | P0 | BROKEN | core/scoring.py:22 price_mention includes $ + workers 778 | OFFER_PPV text contains price $20 -> price_mention hard flag -> score 0.1 -> never auto-approved | Authority: breaks autonomy 0.80 auto flow |
| F-04 | P1 | BROKEN | commerce/product_selection.py:234 vs content_matching.py:71 | Product selection cheapest vs prompt relevance mismatch — wrong product offered | Commerce: wrong product for preference |
| F-05 | P1 | BROKEN | commerce/reconciliation.py:131 + db/dropfans.py:206 | Poll transaction_id = dropfans:{drop_id} not per-sale unique; multi-user pending -> ambiguous never attributed | Attribution unattributed forever |
| F-06 | P1 | WEAK | core/conversation_state.py:106 keywords 14 | Topic extraction only 14 keywords, misses red/lace etc | Quality |
| F-07 | P1 | WEAK | commerce/desire.py:127 decay_desire never called | Decay x0.7 per 24h/topic not wired — desire never decays | Sales: temperature never cools |
| F-08 | P1 | UNWIRED | commerce/state.py:385 funnel_stage None | Relationship derivation ignores DB funnel_stage -> always cold/new | Sales timing |
| F-09 | P1 | DUPLICATED | commerce/temperature.py:90 vs relationship.py:177 | Three fatigue/temperature formulas diverge | Maintainability |
| F-10 | P1 | WEAK | core/response_mode.py:62-76 | TEASE only flirty keywords, CALLBACK only 3 keywords | Conversational |
| F-11 | P1 | BROKEN | commerce/dao.py:935 mark_aftercare_pending never completed | Aftercare pending forever -> decision suppresses offers indefinitely | Aftercare repeat blocked |
| F-12 | P1 | UNWIRED | commerce/feedback.py:21 _behavioral_store append only | In-memory behavioral events never read | Observability |
| F-13 | P2 | WEAK | commerce/vault_taxonomy.py:35 | Taxonomy parse brittle for opaque titles, no validation | Vault |
| F-14 | P2 | DEAD | commerce/attribution.py:26 | attribute_purchase dead, webhook uses dao duplicate | Dead code |
| F-15 | P2 | DEAD | core/capability_contract leak patterns dead, agent loop broken | Thinking leak strip dead, agent mismatch | Dead |
| F-16 | P2 | UNVERIFIED | integrations/dropfans/client.py | DropFans buyer grant unverified externally | External blocker |
| F-17 | P2 | DUMMY | vault_media dead columns dropfans_media_id TEXT | Never written but synthetic used instead — intentional | Polish |
| F-18 | P2 | WEAK | memory/context.py:13 tiktoken gpt-4 | 15% miscount vs Qwen tokenizer | Perf |
| F-19 | P2 | WEAK | commerce/offer_readiness.py:58 hot+0.65 never reachable via bridge | Offer readiness never READY via prompt | Quality |
| F-20 | P3 | UNWIRED | scheduled_messages abandoned poll | No cron for pending>48h auto-nudge (intentionally not spam, but weak) | Polish |
| F-21 | P3 | DEAD | core/response_mode CLOSE, agent legacy build_context | Never emitted/called | Cleanup |
| F-22 | P3 | DUPLICATED | telemetry 22-col table not widened vs in-memory | Observability gap | H |
| F-23 | P3 | WEAK | post_purchase followup generic 40 | Not tailored to purchase, no preference ask | Aftercare |

Counts: P0 3, P1 9, P2 7, P3 4. UNWIRED 4, DEAD 4, DUMMY 0 production dummy; BROKEN 6, WEAK 5, DUPLICATED 2, EXTERNAL 1.

---

## 34. Remediation Roadmap (NO implementation, order only)

**Group A. Conversational intelligence (surgical, no architecture)**

| # | Problem | Why matters | File Function | Data needed | Authority | Dep | Risk | Test |
|---|---|---|---|---|---|---|---|---|
| A1 | Wire real signals into bridge | Fix F-01 | workers/llm_worker.py process_message 585, commerce/state resolve, commerce/deepseek signals | purchase_intent, primary_intent, aftercare, has_active_offer, hours | Deterministic supplies, LLM reads | None | Low 7 lines | test_bridge_red_lace |
| A2 | Expand topic keywords dynamic via title tokens + profile | F-06 red miss | core/conversation_state _extract_topics 106, vault_taxonomy tokens | vault titles tokens | Deterministic | A1 | Low | test_topic_red |
| A3 | Wire decay_desire per turn topic_changed | F-07 no decay | commerce/desire decay_desire 127 | last_transition, topic_changed | Deterministic | A1 | Low | test_decay_topic |
| A4 | Broaden response_mode TEASE/CALLBACK keywords | F-10 weak modes | core/response_mode plan_response_mode 62-76 | vault taxonomy subjects | Deterministic | None | Low | test_tease_red |

**Group B. Sales intelligence**

| B1 | Make offer_readiness use real temp+desire+intent | F-19 never READY | workers bridge 588 pass purchase_intent real | signals purchase_intent | Deterministic | A1 | Low | test_ready_hot |
| B2 | Fix funnel_stage None -> use DB user funnel | F-08 miscompute | commerce/state 385 | users.funnel_stage | Deterministic | None | Low | test_warm_after_20 |
| B3 | Differentiated objection response mapping | objection weak | commerce/feedback classify + workers | rejection type | Deterministic to prompt | None | Low | test_price_prompt |

**Group C. Vault intelligence**

| C1 | Validate title taxonomy at drop creation | F-13 ambiguous | integrations/dropfans/service create_drop 426 add normalize check | title string | Deterministic validate not block | None | Low | test_opaque_warning |

**Group D. Offer lifecycle**

| D1 | Unify product selection: execution use relevance-ranked when preference strong | F-04 mismatch | commerce/product_selection resolve_commerce_product_with_history add topics param | current_topic, preferences | Deterministic | A2 | Medium | adversarial cheapest vs relevance |
| D2 | Fix scoring auto-approve for commerce offers: exclude price_mention when USE | F-03 never auto | workers 778 if selection USE then flags filtered | selection status | Deterministic | None | Medium | test_offer_auto |
| D3 | Expire stale pending>72h or transition to expired | pending blocks new window | commerce/dao add expire stale + scheduler | days | Deterministic | None | Low | test_return_window |

**Group E. Purchase/delivery**

| E1 | Replace synthetic sales_url delivery with buyer-scoped filePath fetch when DropFans exposes grant | F-02 fallback | commerce/post_purchase deliver_product_media 412 | vaultItemIds + buyer grant endpoint | DropFans owns grant | External | Low | test_grant |
| E2 | Make transaction_id per-sale unique (include sale timestamp or buyer hash) | F-05 ambiguous | db/dropfans record_dropfans_sale 206 use saleId or email hash | DropFans saleId | DropFans | External | Medium | test_multi_pending |

**Group F. Aftercare/repeat**

| F1 | Auto-complete aftercare after followup 24h | F-11 pending forever | commerce/post_purchase handle + scheduler | scheduled_messages dedup | Deterministic | None | Low | test_aftercare_completed |
| F2 | Remove _behavioral_store or wire to DB read | F-12 dead | commerce/feedback | — | Deterministic | — | Low | delete |

**Group G. LLM prompt/context**

| G1 | Reorder prompt priority header | weak prompt | memory/context build_qwen3_state_context 232 | — | LLM | A1 | Low | human eval |
| G2 | Add 2 few-shot examples (tease without offer, qualification) | Qwen 3B needs examples | memory/context system prompt | curated | LLM | G1 | Low | qwen eval |
| G3 | Switch tokenizer to Qwen heuristic | 15% miscount | memory/context count_tokens 13 | — | — | — | Low | trim edge |

**Group H. Observability**

| H1 | Widen generation_telemetry 22->30 cols for 8 sales fields | gap | db migration | — | — | None | Low | insert check |

**Group I. Testing**

| I1 | Add 20-scenario matrix as integration tests | coverage | tests | — | — | D1 | Low | matrix |
| I2 | Add 24-turn determinism test mocked provider | — | tests | — | — | All | Low | — |

No redesign, reversible via git revert.

---

## 35. Dependencies & Risks

- A1 unblocks B1, D1, G1 — must first.
- D2 scoring fix must be gated to commerce response only, not all drafts -> risk price spam if mis-applied; keep hard flag for non-commerce.
- E1 buyer grant blocked until DropFans exposes grant API — external blocker; fallback retained.
- D1 relevance vs cheapest risk: could pick expensive mega when fan only hint red but not ready — mitigate threshold rel>=0.30 and desire not OFFER_READY still chooses larger; keep cheapest fallback when readiness not READY.
- No DB migrations for A-D except optional H1; reversible.

---

## 36. Design Principle

```
Conversational LLM (wording, tease) <-- commercial intent (signals) <-- Deterministic commerce (product/price/URL/purchase/delivery)
```
Current respects boundary: LLM owns tone/wording, deterministic owns price/URL/product etc. Violation is opposite: deterministic owns signals but LLM not given them (read-only gap). No violation where LLM invents product/price — guarded.

---

## 37. Special Question 1

> Can the current architecture produce an LLM-driven conversational seller that actively leads a fan toward a purchase, rather than merely detecting when a fan is already ready to buy?

**Answer: NO**

Proof:

1. Lead requires LLM to know demonstrated interest (red lace) and current desire stage. Signal producer commerce/deepseek.py:170 produces desire-relevant purchase_intent and primary_intent but consumer workers/llm_worker.py:585 discards it (primary_intent None). LLM COMMERCIAL STATE desire always interest regardless of fan saying "I love red lace" vs "haha".

2. Lead requires vault knowledge ranked high. That is wired (memory/context.py:581 real topics/preferences) so LLM *does* see red lace titles. But without desire ladder instruction, no reason to tease that title vs generic chat. Response mode TEASE requires tone flirty (horny etc) not fashion curiosity, so leading via tease fails.

3. Sales window that would license lead is derived from fake readiness (hot never). commerce/offer_readiness.py:53 READY requires hot && 0.55, but temp hardcoded warm via 0.35 -> never hot, so window never OPEN. LLM never told window OPEN.

4. Only path that sells is sealed pipeline bypass (selection USE) which fires only when explicit buy intent already detected. That is *detection*, not leading.

Therefore architecture contains components but wiring stubbed with constants prevents leading. Without fixing bridge, active leading impossible.

---

## 38. Special Question 2

> What is the single biggest remaining technical limitation preventing Sunny from behaving like a highly skilled conversational seller?

**ROOT CAUSE:** Deterministic commerce signals computed but never injected into LLM prompt; LLM receives hardcoded fake commercial state.

**FILE:** workers/llm_worker.py:585-595

```python
_rel = "warm"
desire = derive_desire_stage(relationship_state=_rel, primary_intent=None)
temp = derive_commercial_temperature(relationship_score=0.35, desire_stage=desire.stage.value)
readiness = evaluate_offer_readiness(desire.stage.value, temp.level)
objective = derive_commercial_objective(selection, relationship_state=_rel)
```

**FUNCTION:** process_message (bridge P0) calls pure desire/temperature/readiness/sales_window with constants.

**WHY IT MATTERS:** Sunny is only job guidance is COMMERCIAL STATE + OBJECTIVE + window. If those are fake, every generation is generic relationship building. No vault ranking or response-mode can make her lead.

**MINIMAL FIX:** Replace constants with real derived state already available:
```
sig = await extract_commerce_signals(context) or low_information
desire = derive_desire_stage(relationship_state=real_rel, primary_intent=sig.primary_intent, purchase_intent=sig.purchase_intent, price_interest=sig.price_interest, explicit_content_request=sig.explicit_content_request, explicit_purchase_request=sig.explicit_purchase_request, aftercare_status=behavioral_aftercare, has_active_offer=real_has_active, ...)
temp = derive_commercial_temperature(relationship_score=real_rel_score, desire_stage=desire.stage.value, purchase_intent=sig.purchase_intent, content_interest=sig.content_interest, recent_offer_count=timing.recent_offer_count, ...)
readiness = evaluate_offer_readiness(desire.stage.value, temp.level, purchase_intent=sig.purchase_intent, has_active_offer=real_has_active, is_on_cooldown=real_is_on_cooldown, ...)
window = derive_sales_window(...)
objective = derive_commercial_objective(selection, relationship_state=real_rel.value)
```
7-15 lines, no DB migration, no architecture change, reversible.

---

## 39. Special Question 3

> Can Qwen2.5:3B perform the desired behavior with the current architecture if the missing logic is fixed, or is the model itself now the limiting factor?

**Architecture limitation:** YES — bridge hardcoded prevents leading (fixable).

**Prompt/context limitation:** YES — current prompt long (~800), conflicting, no priority, no few-shot, no ladder explanation. For 3B quantized, will struggle to prioritize "when to explore vs tease vs qualify". Needs concise priority-ordered prompt + 2 examples (Group G). Not fatal, just engineering.

**Model capability limitation:** PARTIALLY — qwen2.5:3b 1.9GB quantized can handle tease/qualification framing with correct guidance (C.1-F achieved 82/100 human-likeness). But nuanced desire building (subtle tease referencing vault title without offering vs direct offer) is near limit for 3B without examples; 4B+ would give more headroom. For now 3B sufficient for relationship->desire with single tease, not sophisticated qualification.

Conclusion: **Fix architecture+prompt first; model is not yet blocker. If after A1+G1+G2 conversion still over-sells, then model becomes limiter benchmarked vs qwen3:4b.**

---

## 40. Final Verdict

System is **deterministically safe but conversationally passive**: it will not lose money, corrupt authority, or leak media, but it will not create sales beyond already-ready fans. Intelligence modules exist as pure functions and are tested, but integration that would make Sunny an AI-native seller is stubbed.

With surgical fixes (A1 bridge, D2 scoring, D1 relevance tie-in, G prompt priority) existing architecture can produce active leading without redesign, within Qwen2.5:3b limits.

No production changes, canary, provider, config, DB, migrations performed during this audit.

---

PHASE 6 END-TO-END FORENSIC AUDIT COMPLETE

ROOT STATUS: FORENSIC
CONVERSATIONAL COMMERCE: PASSIVE (deterministic sell-while-ready, no active leading)
ACTIVE SALES LEADING: NO (hardcoded bridge prevents desire-driven teasing)
VAULT INTELLIGENCE: WIRED (title-token relevance + purchased exclusion live, but taxonomy free-text)
OFFER LIFECYCLE: SEALED BUT MISMATCHED (cheapest vs relevance divergence; OFFER_PPV never auto-approves due to price flag)
PURCHASE: WIRED (synthetic pid fix, reconciliation bounded, cross-user ambiguous >1 pending remains)
DELIVERY: FALLBACK (sales_url not media bytes — explicitly NOT media delivery)
AFTERCARE: SUPPRESSED (pending never completes; no preference-tailored followup)
REPEAT SALES: FLAGGED NOT DRIVEN (168h eligible flag only, no outbound)
QWEN 2.5 3B: SUFFICIENT POST-FIX (prompt is limiter today, not model alone)
P0: 3
P1: 9
P2: 7
P3: 4
UNWIRED: 4
DEAD: 4
DUMMY: 0
BROKEN: 6
UNVERIFIED: 1
EXTERNAL BLOCKERS: 1
PRODUCTION CHANGES: NONE
CANARY: NOT ACTIVATED
PROVIDER: UNCHANGED
ARCHITECTURE: NO REDESIGN
CODE CHANGES: NONE
CONFIG CHANGES: NONE
DB CHANGES: NONE
MIGRATIONS: NONE
