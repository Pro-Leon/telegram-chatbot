# Phase 78A — OneCall + Context Engine Production Forensic Audit (Stage A READ-ONLY)

**Date:** 2026-09-03 **Mode:** READ-ONLY (no code/schema/Redis/DB/config install/modify) **Workspace:** `E:\chatbot` **Prior:** 77C `docs/AI_NATIVE_LLM_PHASE_77C_ONECALL_CONTEXT_ENGINE_RUNTIME_FORENSIC_AUDIT.md`, 77D XAUTOCLAIM fix `db/redis.py:158,224`, 77E canary audit **Client:** `redis 8.1.0` **Streams:** `inbound_messages` `send_messages` `dead_letter_queue` **Groups:** `llm_workers`/`send_workers` **Model configured:** `core/config.py:90 ollama_model qwen3:4b num_ctx 8192` (docs spec `Qwen2.5:3B` stale)

## 1. Executive Summary

Production default is `llm_path=new` `core/config.py:141` — ACTIVE at `workers/llm_worker.py:1042`. Exactly **1 authoritative generative LLM** (Qwen `provider.generate`) is proven for normal fan message. `one_call_generation` `core/one_call_pipeline.py:126` produces structured `OneCallReply` validated by Pydantic `extra=forbid` then deterministic `validate_draft_quality`. The unconditional `commerce/pipeline.py:619 generate_commerce_response` inside `_try_commerce_draft` `workers/llm_worker.py:1116` still fires whenever `creator READY` even for `NO_OFFER` then discarded via `commerce/selection.py:280`, making **total provider invocations 2 (1 useful) non-PPV, 2 (1 replaced) PPV** — a **P1 unnecessary second generative LLM** violating strict one-generation invariant. No legacy `extract_commerce_signals/generate_draft/score_draft/run_agent_runtime` executes on `new` (`1209` legacy-only), hardened `1129/1169` routes invalid/provider failure to operator queue `add_to_operator_queue` + `ai.generation_completed` `was_auto_approved False` without legacy cascade (`_fallback_3llm_pipeline 215` dummy 0 LLM bypassed). Context Engine is **wired but gated off** `context_engine_observational=False 135` — when disabled `<1µs` no-op `rendered_text=""` skipped `core/context_compact.py:102`; when enabled full `gather 7 → scorer → dedup RapidFuzz 0.85 → budget 2600 → renderer` executes and reaches Qwen as extra system message. RapidFuzz **conditional dedup only**, MiniLM `all-MiniLM-L6-v2 384` **initialization only** (not on Context Engine path), hnswlib **not implemented** (0 py matches). Memory lexical bounded 3 LTM/5 knowledge, ranking 5 weights but `conversation_state` not supplied → state neutral, budget hard `2600/1150/8192`. PPV price authority preserved `fangate_products.price_minor` `execution.py:252`, creator isolation intact, Redis 77D reclaim payload preservation + `XACK after process` intact, shadow/agent/canary disabled by default.

## 2. Exact Current Architecture (code-wins)

```
Fan → handlers.py:142 NewMessage → check_rate_limit 353 → upsert_user → generation_id md5(user:content:tgId) 63 → resolve_single_application_creator 68 → save_inbound 82 → publish message.created → debounce_enqueue SETNX lock 318 → _wait_and_process sleep 3 → get_debounced_messages 338 → enqueue_inbound XADD inbound_messages 184 (generation_id md5 188)

run_worker 1691 XGROUP CREATE llm_workers 48 → loop requeue_stalled_messages XAUTOCLAIM 224 fixed (returns [(id,fields)] 243) → for claimed: process_message(generation_id preserved 1782) → ack_inbound 219 / move_to_dlq 250 → read_inbound XREADGROUP > 199 → process_message 431 → ack/release

process_message: start_generation 451 → resolve_single_application_creator 459 → acquire_user_lock SET NX 285 → gather upsert_user+is_auto_reply_excluded 483 → get_structured_persona_async 499 → build_qwen3_context 509 (parallel get_user/get_user_profile/get_recent_messages 20 creator-scoped/get_latest_summary) → publish ai.generation_started 526 → observe_context_engine 539 (context_engine_observational false → enabled=False) → _retrieved_context="" → fail-closed gate 565 → LTM extract 592 → fan_knowledge 608 → behavioral 632 → shadow launch 648 (disabled) → resolve_open_loop 669 → derive_conversation_state 695 + build_conversational_commerce_state 722 (signals None) → exposures 754 → pressure/risk/operation 793 → derive_commercial_objective 875 + production_control 883 → operational_intel 939 → derive_persona_behavior_state 1006 + render 1017 → context.append behavior_block 1022 →

IF new 1042: one_call_pipeline_with_fallback 1079 → build_one_call_context 84 (retrieved_context "" unless CE true) → build_commerce_signal_hints 99 → validate_one_call_context 111 (>8192?) → provider.generate ONE_CALL_SYSTEM_PROMPT+COMMERCE_SIGNAL_INSTRUCTIONS json(messages) max400 temp0.7 126 → validate_one_call_response 146 json→OneCallReply→safety→quality  → if valid: draft=reply score=quality signals=one_call.signals 1099 → _try_commerce_draft signals 1116 → resolve_and_run_commerce 385 → run_commerce_pipeline 459 → _apply_signal_flags 321 → decide_from_signals→build_strategy→orchestrate→generate_commerce_response 619 conditional → selection 386 → if USE draft=commerce_text 1123 else invalid 1129 operator_queue ["one_call_invalid_result"] → publish completed+suggestion 1150 return hardened ; except provider 1169 → operator_queue ["one_call_exception"] 1188 return

ELIF legacy 1209: extract_commerce_signals 1214 LLM1 → _try_commerce_draft 1220 → agent canary 1231 → run_agent_runtime 1252 → generate_draft_with_tools/generate_draft 1263 LLM2 → score_draft 1303 LLM3

→ validate_persona_voice 1329 → shadow wait 1369 → is_auto_reply_enabled 1391 → dedup md5 393 → not auto → queue + completed False 1406 ; elif score>=0.80 && !flags → enqueue_send XADD send_messages 65 dedup + completed True 1456 ; else queue+suggestion 1469 → publish_events_batch 1506 → post_process create_task 1671 background profile/summarizer not on reply

Send: _process_send_stream 77 requeue_stalled_send_messages XAUTOCLAIM fixed 158 → for claimed: _handle_send_entry 442 → read_send_messages XREADGROUP send_workers > 96 → for each: is_send_duplicate dedup:{creator}:{dedup} 89 → rate limit ZSET Lua 498 → blacklist → reserve_delivery vault 5m → send_file/send_message → mark_send_dedup → ack_send 115 / move_send_to_dlq 120 → save_outbound_after_send → publish message.sent
```

Non-negotiable preserved: PG/Redis Streams/consumer groups/XAUTOCLAIM/XACK/DLQ/Telethon/send dedup/creator isolation `creator_id` everywhere/Fangate/PPV decision+price authority `execution.py:252`/idempotent `ppv_offer:{c}:{u}:{p}` lock `dao 82`/handoff `needs_handoff`.

## 3. Exact LLM Call Graph

| # | Caller | Callee | File:line | Condition | Provider/model | Purpose | Input | Output | Sync | Discarded? | Next |
|---|--------|--------|-----------|-----------|---------------|---------|-------|--------|------|------------|------|
| N1 | `process_message` → `one_call_pipeline_with_fallback` | `one_call_generation` `one_call.py:validate` `scoring_deterministic` | `llm_worker 1079 → one_call_pipeline 180 → one_call_pipeline 126 provider.generate` | `if llm_path new 1042` always | Ollama `qwen3:4b` (`llm_provider_ollama 264 POST /api/chat num_predict400 num_ctx8192 temp0.7`) fallback gemini→ollama logical 1 | Structured JSON `reply+commerce_signals+confidence+needs_handoff` | system `ONE_CALL_SYSTEM_PROMPT+COMMERCE_SIGNAL_INSTRUCTIONS` + user `json(build_one_call_context+build_commerce_signal_hints)` max400 | `OneCallResult(reply,signals,quality_score,safety_flags,is_valid,validation_error)` deterministic `json→Pydantic extra=forbid→safety→quality` `one_call 113,127,175,227` | await blocks | authoritative if valid `1099`; discarded if PPV replacement `1123` or invalid `1129` return | conditional N2 |
| N2 | `_try_commerce_draft` | `generate_commerce_response` via `run_commerce_pipeline` | `llm_worker 1116 → 385 resolve_and_run_commerce → integration 146 run_commerce_pipeline → pipeline 619 generate_commerce_response → deepseek_response 481 provider.generate` | new && creator READY `integration 137` && not `DECISION/STRATEGY_FAILED` `pipeline 598,604` then **unconditional** even for `NO_OFFER` → selection gates `280` | Ollama `qwen3:4b` `cheap_model` temp0.0 1024 `deepseek_response 76,80` | Render authorized `strategy+execution_result` into `VERIFIED FACTS` constrained natural language | system `_system_prompt` `COMMERCE_RESPONSE_SYSTEM+VERIFIED FACTS(title,price/100,sales_url,execution_fact)+StrategyInstruction` `397` user transcript `30×800` `471` | `CommerceResponse GENERATED text / FAILED` validated `529-545` `empty/malformed/oversized/secret/url/price/offer_claim` | await blocks | discarded `FALLBACK 280` non-PPV; authoritative `USE 320` PPV replaces N1 | no |
| L1 | `process_message legacy` | `extract_commerce_signals` | `llm_worker 1214` inside `if legacy 1209` | legacy always | Ollama `cheap_model` temp0.0 1024 `deepseek 190` | 18-field advisory `CommerceSignals` | system `COMMERCE_SIGNAL_EXTRACTION_SYSTEM 56` user `compose_signal_extraction_input 30×800` | `CommerceSignals` or `low_information()` fallback `231` | await | advisory | → L2 |
| L2 | `_try_commerce_draft legacy` | `generate_commerce_response` | `llm_worker 1220 → same 619` | legacy creator READY not failed → unconditional | same qwen3:4b 1024 | same commerce text | same | same | await | same gating | conditional |
| L3a/b | `process_message legacy` | `run_agent_runtime` | `llm_worker 1252,1287` via `agent/canary should_use_agent 1231` | legacy && !draft && `_use_agent` (`ai_agent_canary_enabled False` `128` → never) | get_llm_provider | Agent loop bounded tool calls `agent_max_tool_calls 5` | AgentState | RuntimeResult | await when enabled | authoritative if success skips Qwen | → scoring |
| L4a | legacy | `generate_draft_with_tools` | `llm_worker 1278` `generate_draft_with_tools 156` `250 client.aio.models.generate_content` | legacy && !draft && llm_tools_enabled && creator && supports_tool_calling (Ollama false → fallback to `generate_draft`) `1263,181` | Gemini `generate_content` `gemini_client credential` 1..4 loops `231 max_tool_calls+1 3` | Qwen with tools | system `merged+TOOL_AUTHORITY_PROMPT 207` user `gtypes.Content` | `response.text` | await up to 4 | authoritative | → score |
| L4b | legacy | `generate_draft` | `llm_worker 1296 → 114 generate_with_history` | legacy && !draft fallback | Ollama `generate_with_history 327` max200 temp0.85 | Standard reply | merged system | `response_text` `122` | await | authoritative | → score |
| L5 | legacy | `score_draft` | `llm_worker 1303 → scoring.py 159 provider.generate` | legacy always after L4 | Ollama temp0.2 max512 | Composite 0-1 + HARD_FLAGS | system `SCORING_SYSTEM_PROMPT 66` | `(0.0-1.0, flags)` fail-closed `0.0` `176` | await | authoritative for threshold `0.80 1441` | — |
| BG1/2 | `post_process` create_task `1671` | `extract_and_update_profile` `memory/profile 72` / `maybe_summarize 51` | background after enqueue | Ollama cheap | Profile/summary | `provider.generate` | async not on reply | — | — |

Provider `llm_provider ollama` `config 84` authoritative `https://ollama.brestalogistics.co.ke` `89`, `model_name gemini-flash-latest` legacy fallback only when `llm_provider gemini` `workers 105 gemini_fallback_enabled`.

## 4. OneCall Call-Count Matrix

| Runtime path | LLM calls (authoritative) | Total invokes incl. discarded | Provider | Purpose |
|--------------|---------------------------|-------------------------------|----------|---------|
| Normal conversation `hey beautiful` creator READY | **1** | **2** (1 discarded commerce) | Ollama qwen3:4b | N1 structured reply + N2 discarded `FALLBACK` |
| Normal no creator | **1** | **1** | — | N1 only `CREATOR_CONTEXT_UNAVAILABLE short-circuit 137` no N2 |
| Commerce candidate `how much is that?` | **1** | **2** | — | same `SOFT_OFFER` discarded |
| PPV execution `send it` explicit OFFER_PPV EXECUTED | **2 gen but 1 final** (N1 replaced) | **2** | — | N1 replaced `1123` + N2 authoritative `GENERATED` `320` |
| Handoff `needs_handoff` | **1** | **2** (or 1) | — | safety flags `154` → operator queue |
| OneCall validation failure `invalid JSON/Pydantic` | **0** | **1 attempted** | — | `1129 add_to_operator_queue ["one_call_invalid_result"]` publish completed `1150` return hardened |
| Exception `timeout 120s 429 empty` | **0** | **1 attempted** | — | `1169 ["one_call_exception"]` `1188` return |
| Agent path `new` | **0** | **0** | — | never `1230` legacy guard |
| Recovered/stalled `XAUTOCLAIM` | same as above | same | — | `run_worker 1745 requeue -> process_message same` `generation_id` preserved `1782` |
| Legacy `hey beautiful` | **3** advisory+Qwen+score | **4** (1 discarded) | — | L1+L2 discarded+L4b+ L5 |

One-generation invariant **violated** on `new` normal/commerce candidate (2 invokes) and PPV (2 generative). See §4 second-gen.

## 5. Second Generation `generate_commerce_response` Forensics

Callers: `llm_worker 306 def _try_commerce_draft`, `1116 new`/`1220 legacy` → `integration 146 run_commerce_pipeline` → `pipeline 619 response = await generate_commerce_response` `deepseek_response 465`. Input `CommerceResponseInput 248` `{user_id,creator_id,conversation 30×800, decision, strategy, execution_result, persona, product_identity, product_state, currency}` ; System `COMMERCE_RESPONSE_SYSTEM 197 + VERIFIED FACTS 302 (title, price_minor/100, sales_url, execution_fact) + StrategyInstruction 358` user transcript `471`; Output `CommerceResponse(status GENERATED text 545 / FAILED 531 code)` checks `empty,oversized,structured,secret,url,price 0.005,offer_claim` `531-543` no `price/link` invention (`price_minor` DB only `252`, `sales_url` DB/canonical `244`). Called when `new && creator READY && not DECISION/STRATEGY_FAILED` **unconditionally** `pipeline 619` (only early returns `574,586,603,610` skip). Selection `selection 80 NON_EXECUTING {NO_OFFER,RELATIONSHIP_BUILDING,SOFT_OFFER,...}` → `FALLBACK 280` discards; only `OFFER_PPV + EXECUTED/ALREADY_EXECUTED 75 + GENERATED 307 + non-empty 313` → `USE_COMMERCE_RESPONSE 320` replaces `draft 1123` (legacy `1225` skips Qwen). **PPV requires it** (only place that may claim `payment link is ready` `457` when `EXECUTED`). For `hey beautiful` second is **discarded** → **UNNECESSARY SECOND GENERATION** generative `temp0.0 1024`; for PPV **PPV-SPECIFIC REQUIRED SECOND GENERATION** but still 2 generative violates strict 1. Not legacy remnant (both paths), not unknown. Fix: gate `619` to `if action is OFFER_PPV and status in EXECUTED` only.

## 6. OneCall Failure Path

`one_call_pipeline_with_fallback 165`: `one_call_generation 180` → `if is_valid return` else `log One-call failed, falling back 192 → _fallback_3llm_pipeline 193` (`215` returns dummy `low_information is_valid True reply="" 230` 0 LLM, TODO). Outer `except 202 → same dummy`. **But worker hardens** `llm_worker 1129 if not is_valid` and `1169 except` → `add_to_operator_queue draft="" flags ["one_call_invalid_result"/"one_call_exception"] 1137/1179` → `publish ai.generation_completed was_auto_approved False + suggestion.created 1150/1188` (`try/pass`) → `return` before commerce/N2/score. **No legacy 3-LLM, no commerce LLM, no agent, no additional Qwen**. Exhaustive: invalid JSON `one_call 113` → is_valid False, Pydantic `127` extra/bounds/strict bool → false, empty `259 empty` → Generation failed, timeout `ollama 217` → Generation failed, `validate_one_call_context 111 >8192` → false, malformed signals `signals 159 strict` → false, context `>8192` → false — all → same operator queue hardened. Stale log `192` misleading but bypassed.

## 7. Context Engine Runtime Trace

`llm_worker 539 observe_context_engine(enabled=context_engine_observational) 546` → `worker_integration 67` `104 if not enabled return enabled=False <1µs` → when `True` `116 ContextRequest(creator_id,user_id,current_message,generation_id)` `127 ContextEngineIntegration.process` → `gatherer 854 gather_all 7 sources 865 Persona/FanState/ConversationHistory/CommerceState/Memory/Temporal/Embedded 865 sequential try/except per source never propagate creator None → [] 158,235,486,655,739` → `assembler 70 scorer/dedup/budget` → `renderer 100 render → RenderedContext blocks system/state/commerce/memory/temporal/content + turns` → `worker_integration 132-155 char_count + rendered_text join memory/temporal/commerce/content` `186 except failed=True` fail-open `560` `telemetry context_engine_*` `549` `_retrieved_context=rendered_text if not failed` `558` → `one_call_pipeline 55 retrieved_context` → `context_compact 102 if retrieved_context.strip(): messages.append system` after state before conversation → `provider.generate 126` `json(messages)`. **Stage:** `IMPLEMENTED + CONNECTED + FEATURE-GATED DISABLED (default) / OBSERVATIONAL ONLY + FAIL-OPEN` `gatherer 854`, `assembler 70`, `renderer 100`, `one_call_pipeline 84`, `context_compact 102`. When disabled not executed; when enabled fully connected.

## 8. RapidFuzz

`pyproject 22 rapidfuzz>=3.0`. `dedup.py:57 _are_lexically_similar fuzz.WRatio/100 >=0.85 fallback Jaccard 62` `95 threshold 0.85` `42 content_hash sha256 normalized`. Called only via Engine `assembler 97 deduplicate(respect_creator_isolation True 153, creator mismatch skip 179,207, lexical_key category:source 203 pairwise)` bounded `~30-50`. `unified_intelligence.py:42 process.extract scorer WRatio score_cutoff80 limit3` vs 110 intent examples **NOT on OneCall** (never imported in `one_call_pipeline`/`llm_worker new`) — offline shadow. When Engine disabled **UNUSED**; when enabled **DEDUP ONLY** `memory_block → rendered_text → system` reaches Qwen. Creator-scoped `dedup 179` never collapses cross-creator.

Status: **DEDUP ONLY / CONDITIONAL** (not retrieval).

## 9. MiniLM / SentenceTransformer

`commerce/embedding_model.py:19 all-MiniLM-L6-v2 DIM384 30 _model_instance None global 32 lru_cache 42 _load_model 51 SentenceTransformer 73 normalize True lazy` `workers 1708 warmup try get_model` `unified_intelligence 108 get_model 111 vecs=encode_messages_sync 110*384 global cache 94`. Per-message `encode_message 62 run_in_executor model.encode([message]) 50ms` `unified_intelligence 181 vec=encode + 85 cosine dot 110*384 0.08ms score_cutoff 0.65/0.10 limit3`. **Context Engine gatherer `677 retrieve_relevant_knowledge limit10` is lexical `overlap*0.5+conf*0.3+recency` not embedding; `scorer 94 compute_topic_overlap word-set /len(query)` not cosine**. No `message_embeddings` persisted for CE (PG `message_embeddings` JSONB not pgvector). **INITIALIZATION ONLY / NOT CONNECTED to Context Engine / OBSERVATIONAL via unified_intelligence offline**.

## 10. hnswlib

Global `grep hnswlib|Index.*384|knn_query|init_index|add_items|set_ef` across `.py`: **0**. `pyproject` no dep, `schema.sql:101 -- HNSW requires pgvector using B-tree`, `memory/retrieval 30 vector_search_messages` python `_cosine_distance 16` `SELECT WHERE user_id fetch-all`, `unified_intelligence 85 brute-force cosine (no HNSW, <1k vectors)` `docs 46:262 <1k HNSW not warranted`. What would need indexing: `fan_knowledge.py:42 subject,value,category,confidence,source,temporal_type,status,creator_id,user_id, created_at/importance` + `embedding 384` per memory. **NOT IMPLEMENTED** (deferred).

## 11. Memory Lifecycle

Creation `extract_fan_knowledge 135` `76 _PATTERNS` `21 TEMPORAL` `long_term_memory 244` `PREFERENCE` via `llm_worker 592 extract_explicit_memories 614 extract_fan_knowledge` creator-scoped `creator_id,user_id,generation_id`. Storage `user_profiles.facts JSONB fan_knowledge_by_creator: {creator_id:[items subject,value,category,confidence,source,observed_at,effective_until,temporal_type,status 30 per]} 264` `long_term_memory_by_creator 20 per 113` `SELECT FOR UPDATE 285` bounded `ON CONFLICT 366` fallback `_knowledge_mem dict`. Embedding **NOT** for stored memory (reference vectors 110 intent not memory). Indexing NONE. Retrieval `retrieve_relevant_knowledge 531 get_fan_knowledge 544 filter not expired 550 subj_tokens overlap current_topic+open_threads 560 score=overlap0.5+conf0.3+recency(1-days/30)0.2 +0.2 CURRENT 573 s>0.2 limit5` `retrieve_relevant_memories 190 limit3 import s>0.2`. Engine `MemorySource 677 limit10` same lexical. Ranking scorer `33` 5 weights, legacy `507` open_loop boost. Rendering `renderer 100` per-category `budget*4` `worker_integration 146 join memory/temporal/commerce/content` → `core/context_compact` system, `memory/context 722 AVAILABLE CONTENT titles semantic only, 749 RELEVANT MEMORY,765 FAN KNOWLEDGE,796 LOCAL TIME` creator-isolated `WHERE (creator_id=$2 OR NULL) 464`.

## 12. Ranking

`scorer.py:33 SCORING_WEIGHTS source0.15 topic0.30 recency0.20 importance0.20 state0.10 authority0.05`. `topic 213 compute_topic_overlap word-set Jaccard len(overlap)/len(query)`, `recency 217 exp(-hours/168) 1-week`, `importance 222 cat_prior SYSTEM10 STATE9.../10*0.7+prio/10*0.3`, `state 150 base0.5 +0.2 topic in content +0.15 COMMERCE+buying_signal`, `source postgres1.0 210`, `authority HARD_POLICY1.0 53`. **Actual `conversation_state=None` passed** `integration 167 snapshot=assembler.assemble(candidates,query=request.current_message)` no state arg → `assembler 74 default None` → `scorer 152 if None return 0.5 neutral` weight 0.10 constant 0.05 never discriminates. Documented formula exists but value inert. Legacy `long_term_memory 560` same but `OPEN_LOOP+0.3`.

## 13. Context Budget

Ollama `num_ctx 8192` `config 94` `provider_ollama 169` hard KV outer. Engine `TOTAL 2600` `models 55` `CATEGORY_BUDGETS SYSTEM400 STATE200 COMMERCE200 MEMORY150 KNOWLEDGE150 TEMPORAL50 CONTENT100 CONVERSATION800 EMBEDDED200 sum2250` `budget.py:70 can_fit cat+global 90, 128 can_fit, 147 try_allocate_or_truncate min(cat.remaining,global) 161 <10 drop max_chars=(available-1)*4 168 truncate +[TRUNCATED] 178 re-fit 208 allocate, validate 218 >2600 violation, assembler 109 loop sorted scored/deduped, 164 total_tokens<=2600 degradation, 178 validate_assembly, tests 954`. OneCall compact `1150 system350 state150 conv600 signals50 26` `ONE_CALL_MAX_MESSAGES 8 109 MAX_ASSISTANT_TURNS3 116` `validate_one_call_context 238 >8192 or system>350 → invalid`. Truncation **after** gather→scorer→dedup **before** renderer `assembler 109` then renderer secondary `budget*4 93`. Cannot replace 30 messages with 47 memories `messages[-8]` cap. `1150+2600=3750<8192` safe.

## 14. OneCall Context Contract

`core/one_call_pipeline:84 messages=build_one_call_context(...)` + `107 append commerce_hints build_commerce_signal_hints` `validate 111`. `core/context_compact 37 build_one_call_context` returns `[system(compressed persona+Fan|Stage+Rules Never reveal AI) 127, system(state: fan|funnel relationship summary identity response/question 172), system(retrieved_context when enabled 102), user/assistant ×8 trimmed 600 107]`. `commerce_hints` `commerce_prompt 19` `relationship_state, recent_offer_count etc`. `msg_user=user_message` added at `format_one_call_prompt`? Actually `one_call_pipeline 126` `user_content=json.dumps(messages)` where `messages` already includes `retrieved_context`. Each field: persona `get_structured_persona_async 499` authoritative compact `render_compact_persona_block 654` required creator `is_default` 320, fan `get_user 115` authoritative `funnel_stage/message_count/is_blocked`, conversation `get_recent_messages 464 creator-scoped` `MAX 20`, commerce `resolve_commerce_state 169` purchase counts/active offer/timing, subscription `has_purchased_product` `dao`, retrieved `ContextItem MEMORY/KNOWLEDGE advisory` distinguished `system` extra block labeled `[AUTHORITATIVE]/[DETERMINISTIC]` `renderer 191`. Max sizes system350 state150 etc. Retrieved clearly `system` after state, not overriding persona `HARD_POLICY0`.

## 15. Commerce / PPV Trace (summary §2 auth)

`fan buy` → `OneCall signals 18 fields extra=forbid 147 bounded 0..1 strict bool` advisory `low_information` on failure `233` → `_try_commerce_draft 306 autonomy kill 342 creator READY 350 product deterministic 359` bounded `PIPELINE_MAX_MESSAGES 30×800 100` → `resolve_and_run_commerce 113 1:1 status 70 → run_commerce_pipeline 146 exactly once` → `pipeline 459 compose 276 truncation → _apply_signal_flags 303 mechanical purchase_intent→buying_intent_score etc 337 → decide_from_signals pure 557 priority 299 → build_strategy 579 → orchestrate 592 sole execute_ppv 191 if OFFER_PPV && allowed && activation` → `execute_ppv 88 12 gates 1 decision allowed 103 else DENIED 2 integration active 120 3 decrypt 129 4 blocked/opted 153 5 SELECT fangate_products 168 product_missing/sales_url else PRODUCT_UNAVAILABLE 182 extract dropfans_id 191 6 pending find_pending 205 ALREADY_EXECUTED 7 purchase already_purchased 212 7 eligibility re-eval 219 deny wins 8 sales_url else build_checkout_url 244 remote_price price_minor 252 DB link_missing→PRODUCT_UNAVAILABLE 256 9 mismatches 10 advisory lock ppv_offer:{c}:{u}:{p} 86 + create_offer_serialized 268 atomic pending/clicked check 89 INSERT 103 idempotent race After ambiguity 281 → publish offer_created 314 best-effort` → `pipeline 619 generate_commerce_response validated 529 secret/url/price 0.005 457 → selection 228 closed machine: CREATOR_CONTEXT_UNAVAILABLE etc COMMERCE_UNAVAILABLE, _NON_EXECUTING 80 → FALLBACK, not EXECUTED→FALLBACK 294, not GENERATED/empty→FALLBACK, else USE 320 offer_active`. Worker `386 select` → `1123 draft=commerce_text` only if `USE`. Fidelity: `compose_signal_extraction_input` never invent creator/product, `role_content_messages 276` keeps newest.

## 16. PPV Price Authority

Source `local_product.get("price_minor") 252` `currency USD 266` `PriceMinor Annotated int>=0 114` `ProductCommerceState price_minor 27 None→pay-what-you-want not denial` `sales_url http(s) 172` DB `CHECK >=0 25` `DROP`. No LLM `requested_price` `signals 193 validated positive finite` only bool `user_asked_about_price 324` via `PRICE_ASK_THRESHOLD 0.80` never `ApplicationOwned 320` never `commerce_offers.price_minor`. Validation `_prices_are_authoritative 439 Decimal/100 ±0.005 only when USD + allow_price_reference + product price` else `not amounts` rejected, `_urls_are_authoritative 426 only sales_url when allow_product_reference`, `_offer_claim 457 only when ACTIVE`. Signature `execute_ppv(*,creator_id,user_id,product_id,decision,created_by)` `88` **no price/link param** enforced by tests `test_commerce_execution 570 signature not contain price`, `llm_tools 1104 You may only suggest product_id price set by application`. Immutability: `INSERT RETURNING *` price captured, `offer_active True` requires `EXECUTED/ALREADY_EXECUTED 75`.

## 17. Commerce Info Available to OneCall

Authoritative `build_conversational_commerce_state 722` provides `relationship_state, has_active_offer, recent_offer_count, recent_purchase_count, has_relevant_product` → `one_call_generation` `50` → `build_commerce_signal_hints 99` system hint `recent_offer_count etc`; `commerce_text` from signals `primary_intent` `1072`. `has_active_offer`/`purchase history`/`product identity` via `resolve_commerce_state` but **price not included** (`CommerceStateSource 552` title only, price omitted). Advisory `fan asks purchase` via OneCall signals not DB. No stale `price $20` vs `$30` possible because price never in OneCall prompt; current price `$30` from `fangate_products` authoritative never sent to Qwen except via `VERIFIED FACTS` inside `generate_commerce_response` under price gate.

## 18. Persona Authority

DB `personas metadata version WHERE creator_id` `creator_persona 318` 23-field `36` `render_compact_persona_block 654 FACTS/BEHAVIOR/LIFESTYLE` → `build_qwen3_context 678 build_qwen3_system_prompt` `Priority 1 Safety 2 Creator/persona identity 203` truthfulness > memory, appended as `CREATOR PERSONA (compact):` `695` after system prompt. Behavioral `derive_persona_behavior_state 124` regex `serious>...` `question_allowed` via `core/question_policy` from `structured_persona,conversation_state,fan_message,fan_knowledge 3, commerce_objective, next_best_action` `1006` → `render 374 60 tokens PERSONA BEHAVIOR emotion/mode Voice: ...` injected `context.append 1023` before generation. Context Engine `PersonaSource HARD_POLICY 0 137` never overridden. Retrieved memories separate `system` `AVAILABLE CONTENT/RELEVANT MEMORY/FAN KNOWLEDGE` labeled `titles semantic only 744` never mutates `persona_block`. Pydantic `extra=forbid` prevents `price`, validation `validate_persona_voice 65 fact_violation I'm <Name> vs Sunny` `FACT_FAIL severe 260` → `persona_validation_severe 1338 score-0.10 1345 not auto_approved`.

## 19. Send / Handoff Authority

`OneCall 1099` → `validate_one_call_response is_valid?` invalid→operator `1137` → commerce `1116` → persona `1329 severe add flag 1338` → production_control metric `1353` → `is_auto_reply_enabled Redis setting:auto_reply 485` default true vs `autonomy_enabled` commerce kill `342`; `dedup md5 user:msg:tgId 1393` → `is_auto_reply_enabled?` `1391` `!auto→queue 1403`; `score>=0.80 && !flags → enqueue_send SEND_STREAM dedup 1442 + completed True 1456` else `queue 1470 + suggestion 1482` → `publish_events_batch 1506` fallback single `1510`. Deduplication `db/redis 65 enqueue_send creator_id propagate 75` `mark_send_dedup 84 SETEX send_dedup:{creator}:{dedup} 3600` `is_send_duplicate 89` creator-scoped `957` in `llm_tools`, plus `requeue_stalled 1745` preserves payload `1782` and ACK after process. Rate limiting `SEND_RATE_LIMIT_PER_SEC 1.0 BURST 5 498` Lua `CHECK_TOKEN_LUA 502 ZSET` atomic `EVAL 538`. Blacklist `is_blacklisted 60` → `resolve queue failed 73` never cycles. Delivery reservation `pg_advisory_xact_lock ppv_offer 86` serialized + vault `5m`. Context Engine never in send path `worker_integration 10 OBSERVATIONAL`.

## 20. Redis / Recovery Regression

77D fixed `requeue_stalled*_messages` now `list[(id, dict)]` `db/redis 179,245` preserves `decode_responses` strings; workers `1745 claim before read >` iterate `for id,fields in claimed: process_message(generation_id=fields.get) → ack_inbound 219 / move_to_dlq 250` and `main 442 requeue_stalled_send_messages → _handle_send_entry` before `read_send_messages > 96`; `XADD 184,65`, `XREADGROUP 199,96`, `XAUTOCLAIM 237,169 start_id 0 count10`, `XACK after process 221,115`, `DLQ dead_letter_queue 18 move_to_dlq XADD payload json + XACK 276 (inbound pending left if DLQ fail 278, send always ACK 155)`, dedup `send_dedup:{creator}: 84`, reclaimed `generation_id md5` preserved `193` `1782`. Context Engine does not move ACK earlier `receive→process→send/DLQ→ACK` intact.

## 21. Feature Flags

| flag | default | env | branch |
|------|---------|-----|--------|
| `llm_path` | `new` `141` new\|legacy | `LLM_PATH` | `llm_worker 1040 new→one_call 1079 else legacy 1209 3-LLM` failure hardened `1133` no cascade |
| `context_engine_observational` | `False` `135` | `CONTEXT_ENGINE_OBSERVATIONAL` | `llm_worker 546 → observe_context_engine enabled` `worker_integration 104` false→`enabledFalse` no generation, true→rendered_text→retrieved_context 102 |
| `context_engine_canary_mode` | `disabled` `148` disabled\|observe `sample_rate0 timeout30 149` | `CONTEXT_ENGINE_CANARY_MODE` etc | `worker_integration 241 CanaryConfig 246 DISABLED→None 249 should_run hash 259 observe parallel never send` not on prod path |
| `one_call` implied | via `llm_path new` | — | fallback dummy `223 low_information` TODO, but hardened bypass |
| `qwen_shadow_enabled` | `False` `99` sample0 `100` | `QWEN_SHADOW_*` | `llm_worker 648 if enabled&&should_sample&&creator 649 → create_task run_shadow 651 → wait 5s 1373 evaluate` best-effort semaphore 3 `464` |
| `ai_agent_canary_enabled` / `ai_runtime_mode legacy` | `False 128` / `legacy 117` `agent_max_tool_calls5` `120` | `AI_AGENT_CANARY_*` `AI_RUNTIME_MODE` | `llm_worker 1231 should_use_agent false→never`, `ai_runtime_mode` not used beyond agent |
| `autonomy_enabled` | `True` `110` | `AUTONOMY_ENABLED` | `llm_worker 342 commerce skip, 492 fail_closed` `production_control autonomous_allowed 887` |
| `llm_tools_enabled` | `True 77 max3 78` | `LLM_TOOLS_*` | `1263 if enabled&&creator → ToolAuthContext 1268 → generate_draft_with_tools 1278 else plain` |
| `llm_provider` | `ollama 84` `ollama_model qwen3:4b 90 before https 89` `num_ctx8192 94` | `LLM_PROVIDER OLLAMA_*` | factory `llm_provider 184` OllamaProvider `301 strip gemini prefix`, GeminiProvider `gemini_client` `84` fallback `gemini_fallback_enabled true 45` |

## 22. Model Alignment

| dimension | file | actual | requested |
|-----------|------|--------|-----------|
| Config default `model_name/cheap_model` | `config 25-26 gemini-flash-latest` | legacy Gemini default | — |
| Authoritative Ollama | `config 84 ollama,89 https://ollama.brestalogistics.co.ke,90 qwen3:4b,94 8192` `llm_provider_ollama 90 _model=ollama_model 169 options num_ctx 8192 148,302 filter gemini prefix` `one_call_pipeline 129 model_name fallback qwen3:4b` | **qwen3:4b** running | **Qwen2.5:3B** stale `core/context_compact 1 Optimizes for Qwen2.5` doc vs `qwen3:4b` code — **model/doc mismatch P1** |
| Documentation | `core/context_compact 1, docs` | Qwen2.5 3B | intended Qwen2.5 3B per arch diagram | Mismatched, no re-pin in 77E |

## 23. Concrete Normal-Message Trace `Fan: hey beautiful` `user 123 creator 1`

1 inbound NewMessage → 2 check_rate_limit 353 `INCR ratelimit:123` pass → 3 debounce lock 318 SETNX 3s owner → 4 _wait_and_process sleep3 → 5 get_debounced LRANGE → 6 get_cached_user_persona → 7 enqueue_inbound XADD inbound_messages `{user_id123,content hey...,tgId 100,generation_id md5}` 184 **ACTUALLY RUNS**
→ 8 XREADGROUP llm_workers 199 → 9 acquire_user_lock SET NX lock:creator:1:user:123 285 → 10 build_qwen3_context 509 `SELECT user 115, profile, recent 20, summary` parallel → 11 publish ai.generation_started 526 → 12 observe_context_engine enabledFalse → **DOES NOT RUN** engine gather/scorer/dedup/budget/renderer (0µs) → 13 RapidFuzz **DOES NOT RUN** → 14 MiniLM **DOES NOT RUN** (not via gather, encode not called) → 15 vector retrieval **DOES NOT RUN** (B-tree fallback) → 16 ranking **DOES NOT RUN** (state neutral but engine gated) → 17 context compaction `build_one_call_context 84 retrieved_context=""` 102 skip → `trim 600 limit8 107` 18 OneCall `provider.generate ONE_CALL_SYSTEM_PROMPT 126` max400 temp0.7 **ACTUALLY RUNS** (1) → 19 Pydantic `OneCallReply extra=forbid 38` + `CommerceSignals bounded 147` → 20 commerce signals `purchase_intent 0.1 etc` low → 21 _try_commerce_draft 1116 `creator READY` → `run_commerce_pipeline 459` → `generate_commerce_response 619` **ACTUALLY RUNS** (2nd generative 1024 temp0.0, then discarded `FALLBACK 280` due `NO_OFFER`) → 22 persona_validation `1329 FACT_FAIL?` No → 23 is_auto_reply true → score 0.85 `validate_draft_quality` → `score>=0.80 && !flags` → `enqueue_send XADD send_messages dedup md5 65` → 24 publish ai.generation_completed was_auto_approved true 1456 → 25 XREADGROUP send_workers 96 → 26 is_send_duplicate false → 27 rate limit allow → 28 client.send_message → 29 mark_send_dedup → 30 ack_send XACK 115 publish message.sent. **Marked CONDITIONAL/DES/DOES NOT RUN as above**.

## 24. Executive Verdict

```
ARCHITECTURE VERDICT:
CONDITIONALLY READY — OneCall single-generation wiring ACTIVE and hardened, Context Engine fully implemented and connected but gated off by default, PPV second generation violates strict 1 and is wasteful non-PPV, Redis 77D fixed, creator isolation preserved; ready only after gating second commerce LLM and flipping Context Engine canary.

ONE-CALL VERDICT:
CONDITIONAL — Exactly one authoritative generation proven, but total provider invocations 2 non-PPV (1 discarded) and 2 generative PPV (1 replaced); strict one-generation invariant NOT MET due to unconditional generate_commerce_response pipeline.

CONTEXT ENGINE VERDICT:
PARTIALLY ACTIVE — IMPLEMENTED+CONNECTED+FEATURE-GATED DISABLED (default OBSERVATIONAL). When enabled, ACTIVE (gather→scorer→dedup→budget→renderer→OneCall). Default prod DISCONNECTED.

RAPIDFUZZ:
DEDUP ONLY / CONDITIONAL — pyproject present, dedup WRatio 0.85 conditional reaches Qwen when enabled; unified_intelligence WRatio 80 offline not on OneCall.

MINILM:
INITIALIZATION ONLY — all-MiniLM-L6-v2 384 singleton loads lazy, reference 110 vectors cached, never called from Context Engine gatherer/scorer; semantic retrieval INACTIVE.

HNSWLIB:
NOT IMPLEMENTED — 0 py matches, no dep, B-tree, brute-force 110, not justified <1k.

COMMERCE:
PARTIALLY ACTIVE — PPV decision pure priority 299, execution 12 gates deterministic, but unconditional commerce LLM waste; tool proposal still deterministic via resolve_and_run_commerce.

PPV AUTHORITY:
PRESERVED — price DB price_minor 252 USD immutable, eligibility hard first denial wins, serialized lock, offer claim integrity, LLM cannot set price.

CREATOR ISOLATION:
PRESERVED — WHERE creator_id, Redis lock:creator:{c}:user:{u}, send_dedup:{creator}:{dedup}, ContextItem creator_id, dedup respect.

REDIS RECOVERY:
WORKING — 77D fix preserves (id,fields), reclaim before XREADGROUP >, XACK after process, DLQ payload JSON, dedup rate limit intact.

PRODUCTION RISK:
MEDIUM — Revenue LOW, delivery LOW, latency/per-cost MEDIUM due to 2× LLM non-PPV, quality/state relevance inactive.
```

## 25. Findings Table

| Area               | Actual implementation | Production active? | Gap | Severity |
| ------------------ | --------------------- | ------------------ | --- | -------- |
| OneCall | `one_call_generation provider.generate 126 validate 146 + validate_draft_quality 150` `workers 1079` | **YES** `llm_path new 141` | 2nd commerce LLM unconditional | P1 |
| Second generation | `generate_commerce_response pipeline 619 deepseek_response 481 temp0.0 1024 VERIFIED FACTS` always when creator READY not DECISION_FAILED, gated `USE 320` else `FALLBACK 280` discarded | **YES** (always) `llm_worker 1116` | Discarded non-PPV violates 1 | P1 |
| OneCall failure | `one_call 113/127 invalid → is_valid False` → `llm_worker 1129 add_to_operator_queue ["one_call_invalid_result"] publish completed 1150 return` hardened `1133`; provider exception `134→1169 ["one_call_exception"] 1188`; `_fallback dummy 0 LLM 215` bypassed | **YES** hardened no legacy cascade | Stale log 192 | P2 |
| RapidFuzz | `dedup 57 WRatio/100 0.85 fallback Jaccard 62 process.extract 24 cutoff80 limit3 offline` dedup `150 respect_creator_isolation 153` threshold 95 cat:source | **CONDITIONAL** only when Engine enabled `worker_integration 104` | Not retrieval | P2 |
| MiniLM | `embedding_model 19 all-MiniLM-L6-v2 384 30 lru_cache 51 SentenceTransformer normalize 73 lazy 62 run_in_executor 110*384 cache 94` encode per-message 181 brute cosine 85 | **INITIALIZATION ONLY** not on CE path `gatherer 677 lexical` scorer word overlap `94` | Semantic not used | P2 |
| hnswlib | `0 py` `pyproject no dep` `schema 101 B-tree` `vector_search_messages python _cosine_distance` brute-force `85 no HNSW` | **NO** | Not needed <1k | P3 |
| Memory storage | `user_profiles.facts fan_knowledge_by_creator 30 per 273 + long_term_memory 20 per 113 SELECT FOR UPDATE 285 bounded` creator-scoped `creator_id,user_id` `messages 22 summaries 47` | **YES** `llm_worker 592,614 extract*` | — | — |
| Memory embedding | `message_embeddings JSONB` legacy `embedding JSONB` not pgvector | **NO** for CE memories | Stored global reference 110 not memory | P2 |
| Vector retrieval | `retrieve_relevant_knowledge 531 limit5 overlap0.5+conf0.3+recency0.2 s>0.2` `retrieve_relevant_memories 190 limit3` `get_recent_messages 464 WHERE (creator_id=$2 OR NULL)` `gatherer MemorySource limit10 677` | **YES** lexical | Not semantic | P2 |
| Ranking | `scorer 33 source0.15 topic0.30 recency0.20 importance0.20 state0.10 authority0.05` `94 word set /len(query)` `113 exp(-hours/168)` `131 cat_prior` `150 state base0.5` `210 source` `53 authority` | **YES** but state neutral `integration 167 None →0.5` | conversation_state not passed | P1 |
| Context budget | `TOTAL 2600 55 CATEGORY SYSTEM400 STATE200 COMMERCE200 MEMORY150 KNOWLEDGE150 TEMPORAL50 CONTENT100 CONVERSATION800 58` `ONE_CALL 1150 system350 state150 conv600 signals50 26` `Ollama 8192 94` `budget 70 can_fit` `147 truncate <10 drop 163` `validate 218` `assembler 109` `renderer budget*4 93` `validate_one_call 238 >8192 invalid` | **YES** hard post-retrieval pre-render | Legacy soft skip | — |
| Persona | `creator_persona 318 SELECT metadata WHERE creator_id` `build_sunny 36 23 fields` `render_compact 654 3k FACTS/BEHAVIOR/LIFESTYLE` `build_qwen3_system_prompt priority 2 identity 203` injected `695` `behavior derive 124 regex` `render 374` `context.append 1023` `validate_persona_voice 65 FACT_FAIL severe 260 score-0.10 1345` | **YES** `workers 499,976` | — | — |
| Fan state | `get_user 115 is_user_auto_reply_excluded` `FanStateSource 230 HARD_POLICY 0 vs 2` `STATE fan|funnel 279` `FOLLOWUP` | **YES** | — | — |
| Conversation state | `get_recent_messages 20 464` `derive_conversation_state 157 current_topic/open_threads` `ConversationHistorySource 390` `assembler query current_message only 167` | **YES** storage + derive, **NO** scorer wiring | state neutral | P1 |
| Commerce state | `resolve_commerce_state 169 SELECT user/dropfans/get_fangate/find_pending/has_purchased/timing/behavioral 177-223 evaluate_ppv_eligibility 223 PolicyDecision` `build_conversational_commerce_state 722` `CommerceStateSource 466 purchases count 521 active 572 timing` `memory/context 729 Purchases: N` `722 AVAILABLE CONTENT titles` | **YES** | price not in OneCall prompt (correct) | — |
| PPV decision | `decision 272 pure priority 299 eligibility 1→1.5 handoff 1.7 free_content 2 sales_enabled 3 has_relevant 4 active 5 purchase 6 offer 7 budgets 2/3 7.5 fatigue 7.6 negative≥2 7.7 low_conf 7.8 opening 7.9 paused 7.10 aftercare 7.11 rejection≥3 8 explicit 8.5 tip 9 strong0.80 10 followup 11 moderate0.55 12 rel0.60 else building` `allowed only SELL_ACTIONS 231` | **YES** | — | — |
| PPV price | `execution 168 SELECT fangate_products 252 price_minor USD 266 link DB/canonical 244 mismatch→PRODUCT_UNAVAILABLE 256` `PriceMinor >=0 114 sales_url http 172` `CHECK >=0 25` | **YES** | Qwen `requested_price` advisory only `324` | — |
| Offer creation | `execution 268 pg_advisory_xact_lock 86 check pending/clicked 89 INSERT 103 idempotent race 281 publish offer_created 314 best-effort` | **YES** | — | — |
| Send | `is_auto_reply 485 setting:auto_reply default true, autonomy 110 independent` score `validate_persona/quality safety 150` `>=0.80 && !flags → enqueue_send XADD SEND_STREAM dedup md5 65 1442` `mark_send_dedup 84 SETEX 3600 creator-scoped 89` `is_send_duplicate 89` | **YES** | — | — |
| Handoff | `needs_handoff safety 154 quality<0.3 159 → flags HARD_FLAGS 11→0.1` `check_operator_handoff pipeline 512` `needs_handoff 1.5 → OPERATOR_HANDOFF` → `operator_queue 1137/1470` `publish ai.generation_completed+suggestion.created 1506` `notify_operators 1470` | **YES** | — | — |
| Redis recovery | `XADD 184,65 XREADGROUP 199,96 > XAUTOCLAIM 237,169 count10 start0 158,224 reclaim [(id,dict)] fields preserved 77D fix 179,245 ack_inbound 221/115 after process move_to_dlq 250 XADD payload json+XACK 276` `requeue_stalled 1745 claim before > for id,fields in claimed: process_message(generation_id preserved) ack` | **YES** `77D WORKING` | cursor pagination not iterated beyond 10 per loop but next loop re-scans 0 | — |
| Creator isolation | `WHERE creator_id=$1 464 lock:creator:{c}:user:{u} 283 debounce 315 send_dedup:{creator}: 84 persona:{creator}: 381 ContextItem creator_id 145 dedup respect 153` | **YES** | — | — |

## 26. Stage B Implementation Map (surgical, proven necessary only)

| Change ID | Root cause | File | Function | Line | Current | Required | Why | Dependencies | Risk | Tests | Rollback |
|-----------|------------|------|----------|------|---------|----------|-----|--------------|------|-------|----------|
| **P0-1** | XAUTOCLAIM discard — already fixed 77D | `db/redis.py` / `workers/llm_worker.py:1745` / `chatbotv2/main.py:442` | `requeue_stalled*` `run_worker` | `158,224,1745,442` | Was discarding `_fields`, now preserves `[(id,dict)]` and processes before `>` | Keep 77D fix, add cursor loop if `>10` pending | Prevents silent loss infinite reclaim | None | `test_phase77d 16/16` `test_redis_recovery 31/31` | Revert to `msg_ids` |
| **P1-01** | 2nd commerce LLM unconditional | `commerce/pipeline.py` | `run_commerce_pipeline` | 619 | `response=await generate_commerce_response` unconditional after orchestration unless early return; discarded via `selection FALLBACK` for `NO_OFFER` | Gate: `if decision.action is not OFFER_PPV or execution_result.status not in (EXECUTED/ALREADY_EXECUTED): return PipelineResult without response (skip LLM)` else generate | Achieves `1` non-PPV (currently 2) — strict one-generation invariant; preserves PPV required 2nd as replacement | `commerce/selection 75` `OFFER_ACTIVE_STATUSES` | Medium — changes PPV gate, must test `SOFT_OFFER` still fallback without LLM | `test_phase77d` mock `generate_commerce_response` call count `0` non-PPV `1` PPV + `test_commerce_pipeline 1296` |
| **P1-02** | State relevance inert | `context_engine/integration.py` | `ContextEngineIntegration.process` | 167 | `assembler.assemble(candidates, query=current_message)` `conversation_state=None` → `scorer 152 0.5 neutral` | Add `conversation_state` param, pass `request.conversation_state or llm_worker _conv_state` to `assembler.assemble`; add `ContextRequest.conversation_state` field via `GathererConfig` | Activates weight `0.10` (0.05 final) for `current_topic+0.2 buying_signal+0.15` — proven intended but wasted | `llm_worker 694 derive_conversation_state` `gatherer 854` | Low — additive score, deterministically sorted, budget still caps | `test_context_engine scorer state 0.5 vs 0.7` `assembler` with/without state |
| **P1-03** | Model/doc mismatch | `core/config.py` + `core/context_compact.py:1` `docs` | `Settings.ollama_model` | 90 | `qwen3:4b` running, spec `Qwen2.5:3B` stale `context_compact 1 Optimizes for Qwen2.5` | Either re-pin `ollama_model="qwen2.5:3b"` and re-bench or update all spec/doc/arch diagrams to `qwen3:4b num_ctx8192` plus `OLLAMA_MODEL` env | Removes P1 confusion, aligns latency/token budgeting | `llm_provider_ollama 90` | Low — model pin changes latency/quality | `model_alignment` test |
| **P2-01** | Canary boolean not 10% | `core/config.py` `context_engine_canary_mode` + `context_engine/worker_integration.py` `CanaryObserver` | `should_run` `CanaryConfig` | 148 `disabled\|observe 0.0` `241 246,249` | `canary_mode observe` samples via `hash(user_id)%100 < sample_rate*100 93-99` but main OneCall injection only via `context_engine_observational` boolean, no percentage for production OneCall | Add `context_engine_enabled_pct int 0-100` or reuse `sample_rate*100` for `context_engine_observational` gating: `if hash(user_id+genId)%100 < pct:` set `retrieved_context` else `""`; log `telemetry context_engine_canary_hit` | Enables true 10% canary without full boolean flip | `worker_integration 104 enabled check` `telemetry 549` | Low — deterministic sampler preserves isolation | `test_phase77d` canary `should_run` 82-99 + `test_context_engine_gatherers` |
| **P2-02** | 10% canary needs observability gap | `core/telemetry.py` `GenerationTelemetry` | `record` | — | missing `embedding_latency, rapidfuzz scores, per-category tokens` `19` has `context_engine_*` but not per-stage | Add `context_engine_scoring_ms, dedup_ms, budget_ms` already `integration 132` but log; `per_category_tokens dict`; `rapidfuzz_sim` | Required to prove 10% canary does not regress latency/budget | `worker_integration 132` | Low | `test_telemetry` |
| **P3-01** | Stale log misleading | `core/one_call_pipeline.py` | `one_call_pipeline_with_fallback` | 192 | `log "One-call failed, falling back to 3-LLM pipeline"` but dummy 0 LLM bypassed via hardened `1133` | Change to `log "One-call failed → operator_queue (hardened, no legacy)"` | Avoids P2 confusion, aligns with 77C `P2-3` | — | None | `loggrep` test |

## 27. Risk Assessment

Revenue **LOW** (price authority preserved), Delivery **LOW** (77D reclaim fixed, payload before ACK, dedup preserved), Latency/Cost **MEDIUM** (2× LLM non-PPV 1 waste `temp0.0 1024` ~30-50% overhead `P1-01`), Quality **MEDIUM** (state weight wasted `P1-02`, semantic inactive), Stability **LOW** (fail-open CE, shadow/agent disabled, lock TTL60, DLQ 3 retries 7d). Overall **PRODUCTION RISK MEDIUM** — ready only after `P1-01` gating and `P1-02` canary 10% flip.

ARCHITECTURE VERDICT:
CONDITIONALLY READY

ONE-CALL VERDICT:
CONDITIONAL — Exactly one authoritative but 2 provider invocations non-PPV (1 discarded) violates strict invariant; PPV 2 (1 replaced) required.

CONTEXT ENGINE VERDICT:
PARTIALLY ACTIVE — IMPLEMENTED+CONNECTED but FEATURE-GATED DISABLED default (OBSERVATIONAL). When true ACTIVE full gather/scorer/dedup/budget/renderer→Qwen. Default DISCONNECTED.

RAPIDFUZZ:
DEDUP ONLY / CONDITIONAL — pyproject 3.14.6 dedup WRatio 0.85 reaches Qwen when enabled; unified_intelligence WRatio 80 offline not on OneCall.

MINILM:
INITIALIZATION ONLY — all-MiniLM-L6-v2 384 singleton loads lazy, reference 110*384 cached, never called from Context Engine gatherer/scorer; semantic retrieval INACTIVE.

HNSWLIB:
NOT IMPLEMENTED — 0 py, no dep, B-tree, brute-force.

COMMERCE:
PARTIALLY ACTIVE — decision pure priority hard rules correct, execution 12 gates deterministic, but unconditional commerce LLM waste.

PPV AUTHORITY:
PRESERVED — fangate_products.price_minor immutable USD, serialized lock, offer claim integrity, Qwen cannot set.

CREATOR ISOLATION:
PRESERVED

REDIS RECOVERY:
WORKING — 77D payload preservation + XACK after process + DLQ + creator dedup + reclaimed before >.

PRODUCTION RISK:
MEDIUM
