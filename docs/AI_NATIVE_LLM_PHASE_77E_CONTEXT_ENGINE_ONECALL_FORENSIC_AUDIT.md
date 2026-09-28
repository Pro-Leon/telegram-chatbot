# Phase 77E — Context Engine Canary & OneCall Invocation Forensic Audit (Stage A READ-ONLY)

**Date:** 2026-09-03 **Mode:** READ-ONLY, no code/schema/Redis/DB/config mutation **Workspace:** `E:\chatbot` **Prior:** 77C audit `docs/AI_NATIVE_LLM_PHASE_77C_ONECALL_CONTEXT_ENGINE_RUNTIME_FORENSIC_AUDIT.md`, 77D fix `db/redis.py:158,224` **Client:** `redis 8.1.0` **Model:** `core/config.py:90 ollama_model qwen3:4b num_ctx 8192` vs spec `Qwen2.5:3B`

## 1. Executive Summary

Production **is** `llm_path=new` default `core/config.py:141` branching at `workers/llm_worker.py:1040`. OneCall is ACTIVE and reaches `provider.generate` `core/one_call_pipeline.py:126` with exactly **1 authoritative generation** per normal/commerce message. A **discarded 2nd `generate_commerce_response`** occurs whenever `creator READY` (even `NO_OFFER`), making provider invocations 2 (1 useful) — generative, not deterministic. PPV adds 2nd *replacement* generation (also generative). No legacy `extract_commerce_signals/generate_draft/score_draft` executes on `new` path (`1209` legacy-only), and hardened `1129/1169` routes malformed/provider failure to operator queue, NOT legacy cascade (fallback in `one_call_pipeline_with_fallback:192` logs but returns dummy 0 LLM and is bypassed by hardened early return). Context Engine is **wired but gated off** `context_engine_observational=False` `135`; when enabled full `gather 7 → scorer → dedup(RapidFuzz 0.85) → budget 2600 → renderer` executes and `rendered_text` reaches Qwen as system message `core/context_compact.py:102`. RapidFuzz ACTIVE conditional (dedup only when enabled, `dedup.py:57`); MiniLM `all-MiniLM-L6-v2 384` `commerce/embedding_model.py:51` INITIALIZATION ONLY (not on OneCall); hnswlib NOT IMPLEMENTED (0 py matches). Memory lexical `retrieve_relevant_memories 3 / fan_knowledge 5`, ranking 5 weights `scorer.py:33` but `conversation_state` not supplied → state relevance neutral, budget hard enforced `2600/1150/8192`. PPV price authority preserved `fangate_products.price_minor` `execution.py:252`, canary is boolean `disabled|observe` `148` plus `context_engine_observational` boolean — no true 10% rollout without code change.

## 2. Actual Production Call Graph

```
Telegram Telethon → handlers.py:27 handle_incoming_message → check_rate_limit 353 → upsert_user → generation_id md5(user:content:tgId) 63 → resolve_single_application_creator 68 → save_inbound_message → publish message.created 82 → debounce_enqueue SETNX lock: 318 → _wait_and_process sleep 3 → get_debounced_messages LRANGE 338 → get_cached_user_persona → enqueue_inbound XADD inbound_messages 184 (generation_id auto)

llm_worker run_worker 1691 init_pool + XGROUP CREATE llm_workers 48 → loop: requeue_stalled_messages XAUTOCLAIM idle 60000 224 (now returns [(id,fields)] 77D fix) → for claimed: process_message(generation_id) → ack_inbound XACK 219 / move_to_dlq 250 → read_inbound XREADGROUP llm_workers > count5 block2000 199 → for msg: process_message 431 → ack/release

process_message: start_generation 451 → resolve_single_application_creator 459 → acquire_user_lock SET NX 285 → gather upsert_user+is_auto_reply_excluded 483 → get_structured_persona_async 499 → build_qwen3_context 509 (memory/context.py:521 parallel get_user/get_user_profile/get_recent_messages 20 creator-scoped/get_latest_summary) → publish ai.generation_started 526 → observe_context_engine 539 (context_engine_observational=False → return enabled=False) → _retrieved_context="" → fail-closed gate 565 → LTM extract 592 → fan_knowledge 608 → behavioral 632 → shadow launch 648 (qwen_shadow_enabled False) → resolve_open_loop 669 → derive_conversation_state 695 + build_conversational_commerce_state 722 (signals None) → exposures/persist 754 → pressure/risk/operation 793 → derive_commercial_objective 875 + production_control 883 → operational_intel 939 → derive_persona_behavior_state 1006 + render 1017 → context.append behavior_block 1022 →

IF llm_path=new 1042: one_call_pipeline_with_fallback 1079 → build_one_call_context user/profile/persona/retrieved_context="" 84 → build_commerce_signal_hints 99 → validate_one_call_context 111 (>8192?) → provider.generate ONE_CALL_SYSTEM_PROMPT+COMMERCE_SIGNAL_INSTRUCTIONS json.dumps(messages) model model_name max400 temp0.7 126 → validate_one_call_response json→OneCallReply→safety→quality 146 → validate_draft_quality 150 → if valid: draft=reply score=quality signals=one_call.signals 1099 → _try_commerce_draft signals= 1116 → resolve_and_run_commerce 385 → run_commerce_pipeline 459 → _apply_signal_flags 321 → decide_from_signals → build_strategy → orchestrate_commerce → generate_commerce_response 481 (conditional) → selection 386 → if USE_COMMERCE_RESPONSE draft=commerce_text 1123 else invalid 1129 → operator_queue ["one_call_invalid_result"] → publish completed+suggestion 1150 return (hardened) ; except provider 1169 → operator_queue ["one_call_exception"] 1188 return

ELIF legacy 1209: extract_commerce_signals 1214 LLM1 → _try_commerce_draft 1220 → agent canary 1230 → run_agent_runtime 1241 → generate_draft_with_tools/generate_draft 1263 LLM2 → score_draft 1303 LLM3

→ validate_persona_voice 1329 → shadow wait 1364 → Send/handoff 1391 is_auto_reply_enabled 482 → dedup md5 393 → if not auto: queue + completed False 1406 ; elif score>=0.80 && !flags: enqueue_send XADD send_messages 65 + completed True 1456 ; else queue+suggestion 1469 → publish_events_batch 1506 (event/event_type compat 77C) → enrich telemetry/classify_outcome 1543 → asyncio.create_task(post_process 1671 profile/summarizer background not on reply)

Send worker chatbotv2/main.py:77 _process_send_stream: requeue_stalled_send_messages XAUTOCLAIM 158 fixed → for claimed: _handle_send_entry → read_send_messages XREADGROUP send_workers > 96 → for each: is_send_duplicate dedup:{creator}:{dedup} 89 → rate limit ZSET Lua 523 → blacklist → reserve_delivery → send_file/send_message → mark_send_dedup → ack_send XACK 115 / move_send_to_dlq 120 → save_outbound_after_send → publish message.sent 353
```

Every I/O fail-open except lock skip, auto_reply excluded return, fail-closed creator gate. Each step file:line above.

## 3. Active LLM Path

`core/config.py:141` `llm_path: str = "new"` (comment `new=Context Engine+one Qwen default active, legacy fallback`). `workers/llm_worker.py:1040` `_llm_path = getattr(_settings,"llm_path","legacy")` then `1042 if _llm_path=="new":` and `1209 if _llm_path=="legacy":` mutually exclusive. No code before `1042` invokes LLM on new path except background `post_process` (not reply). Proved: `grep extract_commerce_signals` only `1214` inside legacy block comment `# Commerce signal extraction (legacy-only)` `1212`; `generate_draft` `114`/`generate_draft_with_tools` `156`/`score_draft` `scoring.py:81`/`run_agent_runtime` `agent/runtime.py:145` all inside `1209` legacy guard. `grep get_llm_provider.*generate` in new block only `core/one_call_pipeline.py:126` + conditional `commerce/deepseek_response.py:481`.

## 4. One-Call Invocation Matrix

| Scenario | Example | LLM calls (provider invocations) | Breakdown | Expected | Verdict |
|----------|---------|----------------------------------|-----------|----------|---------|
| Normal | "hey beautiful" casual | **1 useful / 2 total when creator READY, 1 total when no creator** | 1× `provider.generate` `one_call_pipeline.py:126` structured JSON + 1× `generate_commerce_response` 481 via `_try_commerce_draft` even for `NO_OFFER` then discarded `selection.py:280` FALLBACK | 1 | PROVEN 1 authoritative, P1 inefficiency |
| Commerce-interest | "how much is that?" price 0.6 | **1 useful /2 total** | same hints `build_commerce_signal_hints` 99 deterministic, no extra | 1 | PROVEN |
| Explicit PPV | "send it" explicit_purchase_request | **2** | 1× one_call +1× `generate_commerce_response` → `GENERATED` `545` → `USE_COMMERCE_RESPONSE` `320` `draft=commerce_text` `1123` replacement | 1 | DEVIATION — 2 generative, correct for PPV |
| Objection | "too expensive" negative | **1 useful/2 total** | pipeline decides `OFFER_EXISTS`/`RESISTANCE` → not `OFFER_PPV` → fallback, still generates discarded | 1 | Same inefficiency |
| Existing subscriber / non-subscriber | purchase state | Same as above | `has_purchased` checked `product_selection 86` before `generate_commerce_response` but generation still happens regardless (always after orchestrate) | 1 | Same |
| Handoff `needs_handoff` | distress | **1** | `validate_one_call_response` `154` safety flags → `needs_handoff True` → score still 1, routing `needs_handoff` → operator queue `handoff:reason` `decision.py:1.5`, no 2nd | 1 deterministic | OK |
| OneCall failure (timeout/invalid JSON/Pydantic) | malformed | **1 attempted 0 extra** | `one_call: Generation failed` `134` `is_valid False` → `one_call_invalid_result` `1129` or `one_call_exception` `1169` → operator queue `was_auto_approved False` `1150/1188` return, **no legacy** hardened | controlled | PROVEN hardened; `_fallback_3llm_pipeline:215` dummy 0 LLM never reached |

**Why 2nd exists:** `commerce/pipeline.py:619` `generate_commerce_response` is *unconditionally* called after `orchestrate_commerce` unless `DECISION_FAILED/STRATEGY_FAILED`. `commerce/selection.py:286` gates `USE_COMMERCE_RESPONSE` on `EXECUTED/ALREADY_EXECUTED`+`GENERATED`. So for non-PPV, LLM is invoked and discarded. Generative (`provider.generate temp0.0 max1024`).

## 5. Commerce Hook

`workers/llm_worker.py:1103 if _one_call_result.signals: _commerce_signals = signals` comment `avoids separate LLM` → `1116 _try_commerce_draft(user_id,context,persona,signals=_commerce_signals)` → `306 def _try_commerce_draft(...,signals)` → `385 outcome=await resolve_and_run_commerce(request,signals=signals)` → `commerce/integration.py:113` → `146 run_commerce_pipeline(signals=signals)` → `commerce/pipeline.py:480 if signals is None: signals=await extract_commerce_signals(...)` else reuse. So **new path 0 `extract_commerce_signals`** proven via single legacy import `1214`. Deterministic? No — `generate_commerce_response` 481 is generative `provider.generate` validated against `VERIFIED FACTS` whitelist and price `0.005` `439`. PPV creation `execution.py:88` 12-step gated + `dao.py:82` advisory lock remains deterministic; product selection `product_selection.py:149` deterministic.

## 6. PPV Authority

Trace: fan "I want to buy" → OneCall `commerce_signals.requested_price` bounded `signals.py:196` advisory only → `signals_to_context:289` `user_asked_to_buy` → `build_conversational_commerce_state` → `one_call` → `_try_commerce_draft` → `resolve_single_application_creator` READY `350` → `resolve_commerce_product_with_history 359` creator-scoped `list_fangate_products 200` `is_accessible+sales_url 60` exclude purchased `86` relevance `rel>=0.15 282` cheapest tie — **never LLM** → `CommerceStateRequest 378` → `resolve_commerce_state 130` `evaluate_ppv_eligibility` `state.py:223` (blocked/do_not_auto_reply/is_accessible/sales_url/has_active_offer/already_purchased) → `decide_commerce_action` priority `1 eligibility→12 relationship 299` → `orchestrate_commerce` → `execute_ppv 88` re-validates `User/Product/OfferContext 219` + `integration active 120` + decryptable `129` + pending offer `ALREADY_EXECUTED 207` → `local_product.get("price_minor") 252` `USD 266` mismatch→`PRODUCT_UNAVAILABLE` → `create_offer_serialized pg_advisory_xact_lock hashtextextended('ppv_offer:{c}:{u}:{p}') 265` transaction `SELECT pending/clicked → INSERT pending price_minor` idempotent → `sales_url` from `fangate_products` or `dropfans/service 244` → `select_commerce_response:228` requires `COMPLETED+OFFER_PPV+EXECUTED+GENERATED` → `USE_COMMERCE_RESPONSE` else `FALLBACK`.

Price origin `fangate_products.price_minor` authoritative DB; Qwen `requested_price` never written, `commerce/offers.price_minor` immutable after insert, idempotent `ALREADY_EXECUTED 291`, creator isolation `WHERE creator_id`, subscription/access via `has_purchased_product`/`transaction_id`, dedup `send_dedup:{creator}:{dedup} 3600`.

## 7. Context Engine Runtime Map

```
workers/llm_worker.py:539 observe_context_engine(enabled=_settings.context_engine_observational)
 -> context_engine/worker_integration.py:104 if not enabled: return ContextEngineObservation(enabled=False) fail-open
 -> else: ContextEngineIntegration.process(ContextRequest creator_id/user_id/current_message generation_id) 116
    -> gatherer.py:876 gather_all 7 sources (PersonaSource HARD_POLICY 0 137, FanState 230, ConversationHistory DETERMINISTIC_RULE 390, CommerceState 481, MemorySource, TemporalSource, EmbeddedKnowledgeSource)
    -> assembler.py:70 scorer/dedup/budget
    -> renderer.py:100 render -> rendered_text memory+temporal+commerce+content (system/state excluded 147)
 -> workers/llm_worker.py:558 if not failed and rendered_text: _retrieved_context=rendered_text
 -> one_call_generation retrieved_context=_fields 55 -> build_one_call_context 84 if retrieved_context.strip(): messages.append system 102 -> provider.generate 126
```

**Classification:** `PRODUCTION ACTIVE` only when `context_engine_observational=True` else `OBSERVATIONAL` gated off. Current default `False` `core/config.py:135` → `<1µs` no-op, ` _retrieved_context=""` skip. No startup load; lazy. Fail-open `worker_integration.py:186` `failed=True` → production continues.

## 8. RapidFuzz

`pyproject.toml:22 rapidfuzz>=3.0` imported `context_engine/dedup.py:57` `_are_lexically_similar() fuzz.WRatio/100>=0.85 fallback Jaccard` + `commerce/unified_intelligence.py:24 process.extract scorer WRatio score_cutoff 80 limit3`. Production caller: `dedup.py:58` inside `assembler -> deduplicator` when Engine enabled `respect_creator_isolation True 153` `creator_id mismatch skip 180`. Compares `ContextItem.content` pairwise within same `category:source` lexical_key, threshold `0.85` `95`, bounded `~30-50 items under 2600 tokens`. Output via `memory_block` → `rendered_text` → system message when enabled; if disabled **NOT USED** (`core/one_call_pipeline.py` no import). Also `unified_intelligence` offline not on OneCall.

Status: **ACTIVE CONDITIONAL** (dedup when Engine enabled) / **TEST ONLY** otherwise.

## 9. MiniLM / SentenceTransformer

`commerce/embedding_model.py:19 _MODEL_NAME all-MiniLM-L6-v2 _DIMENSION 384` `51 SentenceTransformer(_MODEL_NAME)` CPU `normalize True 73` singleton `_model_instance None 30` `lru_cache 33` `get_model()->_load_model 58`. Lazy: first `get_model()` at `commerce/unified_intelligence.py:109` `_ensure_reference_cache` or `workers/llm_worker.py:1713` warmup try. Per-message `encode_message 62 loop.run_in_executor _encode model.encode([message]) 50ms`, reference vectors `110*384` cached `_reference_texts/_vectors 94 169KB`. `unified_intelligence.py:181 vec=encode_message(message)` + `85 cosine vs 110 intent examples brute-force 0.08ms` `score_cutoff`. **Context Engine** `MemorySource 675` calls `retrieve_relevant_knowledge` lexical not embedding; `scorer.py:94` word overlap not cosine. Not called in `one_call_pipeline` or `build_one_call_context`.

Status: **INITIALIZATION ONLY / OBSERVATIONAL** — loads, not on OneCall path.

## 10. hnswlib

Global `grep hnswlib|knn_query|init_index|add_items|Index` across `.py`: **0 matches**. `pyproject.toml` no dep, `db/schema.sql:101` `-- HNSW requires pgvector, using B-tree`, `memory/retrieval.py:30 vector_search_messages` is Python `_cosine_distance` loop `db/postgres.py:16` `SELECT WHERE user_id fetch-all`. `commerce/unified_intelligence.py:85` `# Brute-force cosine (no HNSW, <1k vectors)`. **NOT IMPLEMENTED** — brute-force sufficient `PHASE_46:262`.

## 11. Memory Retrieval

Storage LTM `commerce/long_term_memory.py:79 add_memory_item 244 extract_explicit_memories regex` bounded 20 `user_profiles.long_term_memory_by_creator` per-creator; Knowledge `commerce/fan_knowledge.py:277 add_knowledge_item 135 extract 30 patterns` bounded 30. Retrieval lexical `retrieve_relevant_memories limit3 190` `overlap0.5` + `retrieve_relevant_knowledge limit5 531` `overlap0.5` via `memory/context.py:755/774` `if score>0.2` sorted `score=overlap0.5+conf0.3+recency0.2+importance0.1`; **no semantic embeddings**. Legacy concatenation `RELEVANT MEMORY/FAN KNOWLEDGE` sequential; Engine `MemorySource gather 634 limit10` `priority7 score0.75` `DETERMINISTIC_DERIVATION 2` scored via same lexical.

## 12. Ranking / Scoring

`context_engine/scorer.py:33 SCORING_WEIGHTS source0.15 topic0.30 recency0.20 importance0.20 state0.10 authority0.05`. Signals: `compute_topic_overlap 94 word-set 0-1`, `recency exp(-hours/168) 113 week half-life 85`, `importance cat_priority/10 131 SYSTEM10 STATE9 COMMERCE8 MEMORY7 63`, `state_relevance current_topic +0.2 etc 150 base0.5`, `source_reliability postgres1.0 etc 52`, `authority HARD_POLICY1.0 etc 52`. `assembler 93 score_items(candidates,query=current_message)` → dedup → `try_allocate_or_truncate`. **State relevance wiring PARTIAL:** `integration.py:168 assembler.assemble(candidates, query=current_message)` passes `conversation_state=None` default, so `state_relevance` stays 0.5 neutral despite weight 0.10.

## 13. Conversation-State Integration

`derive_conversation_state context 695` + `build_conversational_commerce_state 722` produce `current_topic/open_threads/last_question/tone` → `_conv_state` → used for persona behavior `1006` and commerce state reads, but **not** passed to `ContextEngineIntegration.process`. `scorer state` thus inactive. Intended architecture says `conversation/state relevance` active — actual **INACTIVE** wiring.

## 14. Context Budget

`core/config.py:94 ollama_num_ctx 8192` → `llm_provider_ollama 148 options.num_ctx`; Engine `context_engine/models.py:55 TOTAL 2600` `58 CATEGORY_BUDGETS SYSTEM400 STATE200 COMMERCE200 MEMORY150 KNOWLEDGE150 TEMPORAL50 CONTENT100 CONVERSATION800 EMBEDDED200 sum2250`; OneCall `core/context_compact.py:26 system350 state150 conversation600 signals50 total1150 ONE_CALL_MAX_MESSAGES 8 MAX_ASSISTANT_TURNS3 111`; Legacy `QWEN3_TOKEN_BUDGET system400 state200 conversation800 42`. Render `budget.py:39 len/4` `168 max_chars=(available-1)*4` `renderer.py:93 budget*4` trunc `...`. **Enforced:** `budget.py:70 can_fit` both cat+global, `try_allocate_or_truncate 160 min(cat.remaining,global)`, drop `<10 tokens 163`, `validate_assembly 178` `tests/test_context_engine.py:954 total_tokens<=2600`. OneCall `validate_one_call_context 111 >8192 => is_valid False low_information needs_handoff`. Legacy commerce `count_tokens(commerce_text)<=200 465` soft skip if over. Cannot replace `30 messages` with `47 memories` — hard caps keep `messages[-8]` + `retrieved_context` single system message `102`.

## 15. Authoritative vs Advisory

`models.py:14` hierarchy `HARD_POLICY0 < DETERMINISTIC_RULE1 < DETERMINISTIC_DERIVATION2 < CONTEXT_ASSEMBLY3 < LLM4`. Persona `0` `gatherer.py:137` never overridden; Fan `2`, Conversation `1`, Commerce `1` authoritative. Retrieved `MEMORY/KNOWLEDGE` `2` advisory `is_authoritative <=2 163` but persona `0` wins scorer + dedup `HARD_POLICY` first. Memory `Price $20` vs commerce `Products: title` no price — price never in `CommerceStateSource` `552` title only, final `$30` from `execution.py:252`. `memory/context.py:470` `Do NOT treat conversation as overriding [APPLICATION CONTEXT]` enforced via authority weight.

## 16. OneCall Schema

`raw_json → json.loads TypeError→is_valid False` `core/one_call.py:112` → `OneCallReply.model_validate extra=forbid 38: reply str 1..2000 required _validate_reply strip 60, commerce_signals default low_information 52, confidence 0..1 default0.5, needs_handoff bool defaultFalse` → safety `_compute_safety_flags 180 HARD_FLAGS price_mention` → quality `_compute_quality_heuristics 227 4 scores composite capped <5 words 0.5 307 → quality<0.3 needs_handoff`. `CommerceSignals` `signals.py:147 extra=forbid` required `purchase_intent/content_interest/relationship_engagement/price_interest 0..1 BoundedFloat 118`, `explicit_purchase..., StrictBool`, `negative_sentiment/confidence/model_uncertainty/conversation_relevance, evidence list5*240, primary_intent ∈ INTENT_CATEGORIES 21 204, intent_tags5 212, optional requested_price positive finite 195`. Missing `reply`/`confidence>1`/`extra=price` → ValidationError → `is_valid False Schema validation 126` → `low_information` fallback `one_call_pipeline:114`. Prompt `314` asks 4 keys `reply, commerce_signals{15}, confidence, needs_handoff` + `NEVER price, Output ONLY JSON` — superset `+ accepted_recent_offer/conversation_relevance` with defaults, fixed `P0-02`. Pipeline `provider.generate system ONE_CALL_SYSTEM_PROMPT+COMMERCE_SIGNAL_INSTRUCTIONS json.dumps(messages) model model_name max400 temp0.7 126` → `llm_provider_ollama POST /api/chat num_predict400 num_ctx8192 148` timeout120 `91` think empty retry once `247`.

## 17. OneCall Failure Behavior

`provider timeout HTTP 401/429/500 → one_call: Generation failed 134 is_valid False needs_handoff` → `workers/llm_worker.py:1169 outer except` → `add_to_operator_queue ["one_call_exception"] 1179 + publish completed+suggestion 1188 best-effort try/pass → return` **No legacy** `Hardened: no legacy cascade 1171` (`_fallback_3llm_pipeline 215` dummy 0 LLM bypassed). `invalid JSON/Pydantic extra/bounds 112/126 → is_valid False Schema validation → one_call_invalid_result 1129 1137` same. `missing/empty TypeError / llm_provider_ollama 259 empty → same` capped `<5 words`. Commerce failure `workers 387 except→return None` → fallback draft. Context Engine failure `worker_integration 186 failed=True` skip `558`. DB `return_exceptions` defaults `new/there/[]` `memory/context 543` `RESOLUTION_FAILED 455` → fail-closed `creator_context_unavailable 565` queue batch `573`; Redis lock `acquire_user_lock SET NX 285` skip `475`, `publish_event warning return None 64`, `enqueue_send XADD 65 fail → outer except → ai.generation_failed 1676 → move_to_dlq XACK 276 (or leave pending if DLQ fails 274)`.

## 18. Canary Capability

`core/config.py:135 context_engine_observational: bool=False` (boolean, not percentage) comment `When true, Context Engine runs and its rendered context is passed to OneCall`. `148 context_engine_canary_mode: str="disabled" # disabled|observe` `149 sample_rate 0.0` `150 timeout 30 151 max_tokens500 152 model ""`. `context_engine/canary_observer.py` etc exists but `workers/llm_worker.py` only checks `context_engine_observational`, not `canary_mode` for OneCall injection. No `_should_sample 10%` logic on production OneCall path. **True 10% rollout requires code change** (percentage sampler + creator sharding). Current supports only `boolean enable/disable` or `observe` parallel without send. Cannot do `10%` canary without Stage B implement.

## 19. Observability

Proven via `core/telemetry.py` `GenerationTelemetry`: `context_build_ms 510, context_chars, context_engine_enabled/ms/gather_ms/candidates/selected/dropped/tokens/chars 548, shadow_launched/timeout, commercial_objective, experiment_exposure, pressure_bucket/risk_state, operation_decision_allowed, persona_behavior_derived, routing_decision one_call/one_call_commerce/one_call_failed, generation_latency_ms 1206, provider_latency, shadow_latency, persona_validation_status`. Retrieval latency `gather_time_ms assembly_time_ms integration 160`, candidate/selected `worker_integration 154`, context size `generation_context_chars 515`, `estimate_one_call_tokens 225`. Missing: embedding latency (not measured), RapidFuzz similarity scores, per-category token breakdown (budget validates not logged), Ollama `num_predict` actual tokens, `justid` cursor.

## 20. Performance

Measured `77C` `python -c build_one_call_context`: baseline 102 tokens 8 msgs, with retrieved `MEMORY/COMMERCE/TEMPORAL` +28 tokens 130 valid True, large 2k chars 353 valid True vs `8192`. PG round-trips per normal: `get_user 1 + get_user_profile 1 (cached second hit) + get_recent_messages 20 1 + get_latest_summary 1 + fan_knowledge 0-1 + is_user_auto_reply_excluded 1 + add_to_operator/ enqueue_send 1 + resolve_single_application_creator 2 (cached Redis 2nd)` ≈ `7 PG 4 Redis` `publish_event started 1 + batch completed 1 + XADD 1` preserved after 77C `orjson` + `publish_events_batch` pipeline `74B optimization intact`. Retrieval latency `gather 5ms` synthetic, embedding not on path so 0. Unknown benchmark for live Ollama `qwen3:4b` latency — `UNKNOWN — instrumentation required` for true `generation_latency` distribution; gateway `https://ollama.brestalogistics.co.ke` Caddy + Basic Auth `ollama_username/api_key`.

## 21. Revenue Safety

Context Engine cannot change: PPV eligibility `evaluate_ppv_eligibility state 223 + execution 219` deny first, product identity `product_selection 149` deterministic, price `price_minor 252` `USD 266` immutable insert, offer creation `create_offer_serialized lock ppv_offer:{c}:{u}:{p} 265`, creator isolation `WHERE creator_id`, subscription `has_purchased 212` `transaction_id`, handoff `needs_handoff` override `154 safety flags`, dedup `send_dedup:{creator}:{dedup} 3600 81`, delivery `XACK after enqueue_send 1456` (ACK after process, 77D fixed reclaim before ACK). Authority boundary `Context Engine advisory → Qwen language+signals → deterministic commerce business authority` preserved.

## 22. Redis / Delivery Safety (77D fixed)

77D repaired `db/redis.py:158,224` `XAUTOCLAIM` discard bug: now returns `[(id,fields)]` with fields, `workers/llm_worker.py:1743` and `chatbotv2/main.py:442` iterate claimed `for id,fields in claimed: process_message/enqueue_send + ack/dlq` before `XREADGROUP >`. `XADD` `MADD`, `XREADGROUP >` `199`, `XAUTOCLAIM` count10 `158`, `XACK` `219/115`, `DLQ dead_letter_queue` `18` `move_to_dlq XADD+` `XACK even if DLQ fails 155 but leaves pending 274 for inbound`, dedup creator-scoped, isolation preserved. Context Engine does not move ACK earlier: `receive → process (OneCall+validation+commerce+persona) → send/DLQ → ACK` unchanged.

## 23. Database Contract

Every value used by CE: `creator_id` `creator_integrations` `resolve_single_application_creator` `commerce/single_creator 75`; `user_id` `users.id` `get_user 115`; `Telegram ID` `telegram_message_id` not stored as CRM id; `product_id` `fangate_products.id` `product_selection 149` `is_accessible+sales_url`; `offer_id` `commerce_offers.id` `create_offer_serialized`; `subscription` `fangate_transactions transaction_id delivery_id UNIQUE` `reconciliation 251` `has_purchased`; `relationship_state` `users funnel_stage/message_count` derived `relationship_engaged_min_purchases 155` etc.; `conversation_state` `messages.creator_id scoped 464` `derive_conversation_state 157`. No CRM ID leaks to Telegram `entity` is `user_id` str via `enqueue_send`.

## 24. Exact Gaps

**ROOT gaps from CODE WINS:**

- 2nd `generate_commerce_response` discarded for non-PPV → 2 invocations not 1.
- `context_engine_observational False` default → 0 retrieved in prod.
- `conversation_state` not supplied to scorer → state weight wasted.
- `qwen3:4b` vs `Qwen2.5:3B` spec mismatch.
- `context_engine_canary_mode observe` not wired to OneCall injection for % rollout.

## 25. Recommended Stage B Changes (surgical, no architecture redesign)

1. Gate `commerce/pipeline.py:619` `if action==OFFER_PPV and execution in (EXECUTED,ALREADY_EXECUTED): generate` else skip to achieve 1 non-PPV.
2. Flip `context_engine_observational=True` behind `get_settings` canary or add `context_engine_enabled_pct` sampler `should_sample(user_id) %100 <10` creator sharding, plus wire `conversation_state` into `ContextEngineIntegration.process(..., conversation_state=_conv_state)`.
3. Align docs `core/context_compact.py:1` `Qwen2.5` → `qwen3:4b` or re-pin model.
4. Add `context_engine_canary_mode` percentage handling or rename to `context_engine_enabled`.
5. Add telemetry `embedding_latency`, `rapidfuzz scores`, `per-category tokens`.

## 26. Risk Assessment

**Revenue:** LOW — price authority intact. **Delivery:** LOW after 77D fix (pending-discard fixed). **Latency:** MEDIUM — 2nd discarded LLM +28 tokens not huge but 150% cost for `casual_chat`. **Quality:** MEDIUM — state relevance inactive, semantic not used.

## 27. Acceptance Criteria (for Stage B)

- [ ] `hey beautiful` → 1 provider.generate (no `generate_commerce_response` when not PPV)
- [ ] `how much is that?` → 1
- [ ] `send it` PPV → 2nd is replacement not discarded, price from DB
- [ ] `context_engine_observational=True` 10% canary reaches Qwen as system message
- [ ] `XAUTOCLAIM` still preserves fields (77D)

---

PHASE: 77E

CURRENT RUNTIME:
llm_path=new default reaches OneCall 1042; Context Engine wired but gated off (boolean False 135, requires true to pass retrieved_context 55->102); RapidFuzz dedup conditional, MiniLM init only, hnswlib not implemented; PPV still triggers 2nd generative generate_commerce_response even for non-PPV then discards; hardened no legacy cascade; Redis reclaim fixed 77D.

LLM CALL COUNT:
normal = 1 useful (2 total with discarded commerce when creator READY, 1 when no creator)
commerce = 1 useful (2 total)
PPV = 2 (one-call + commerce PPV replacement, generative)
failure = 1 attempted -> controlled operator queue (["one_call_invalid_result"]/["one_call_exception"]) + completed+suggestion publish, 0 legacy

CONTEXT ENGINE:
RapidFuzz = OBSERVATIONAL (ACTIVE only when context_engine_observational=True via dedup 0.85, offline unified_intelligence cutoff80)
MiniLM = INITIALIZATION ONLY (all-MiniLM-L6-v2 384 singleton loads, not on OneCall path)
hnswlib = NOT IMPLEMENTED
semantic retrieval = INACTIVE (lexical overlap only; MiniLM cosine offline 110 vectors)
ranking = ACTIVE (5 weights source0.15 topic0.30 recency0.20 importance0.20 state0.10 authority0.05 scorer.py:33)
conversation-state relevance = INACTIVE (weight 0.10 but conversation_state=None not supplied integration.py:168)
hard context budget = ACTIVE (2600 engine +1150 OneCall +8192 Ollama validated 225)

ONECALL:
ACTIVE

PPV AUTHORITY:
PRESERVED (product deterministic product_selection 149, price DB fangate_products.price_minor 252 USD immutable, offer serialized lock dao 82, Qwen requested_price advisory only)

FAILURE FALLBACK:
controlled handoff/operator queue — OneCall invalid/provider timeout -> add_to_operator_queue draft="" confidence0 + publish ai.generation_completed was_auto_approved False + suggestion.created best-effort then return (hardened 1133/1171), _fallback_3llm_pipeline dummy 0 LLM bypassed, no legacy 3-LLM cascade unless llm_path=legacy explicitly

ROOT GAPS:
2nd generate_commerce_response discarded for non-PPV (pipeline unconditional 619), context_engine_observational False default (0 prod retrieved), conversation_state not wired to scorer, qwen3:4b vs Qwen2.5 spec, canary boolean not percentage

P0:
None after 77D fix (pending-discard P0 resolved). Previous P0 XAUTOCLAIM fields discard fixed db/redis.py 158,224 + reclaim processing 77D.

P1:
P1-1 Model mismatch qwen3:4b 90 vs Qwen2.5 spec; P1-2 Commerce 2nd LLM discarded wasted; P1-3 State relevance wiring missing (state weight neutral)

P2:
P2-1 Event batch compat shim event/event_type 77C; P2-2 Context Engine default off; P2-3 Log "falling back to 3-LLM" stale one_call_pipeline 192

STAGE B RECOMMENDATION:
Gate generate_commerce_response to OFFER_PPV+EXECUTED only, flip Context Engine canary 10% with conversation_state wiring, align model docs, add percentage canary handling, keep 77D Redis fix; do not add hnswlib/replace Streams

ARCHITECTURE CHANGES:
NONE
