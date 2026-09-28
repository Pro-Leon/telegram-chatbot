# Phase 78C — Context Engine Production Retrieval Forensic Audit (Stage A READ-ONLY)

**Date:** 2026-09-03 **Mode:** READ-ONLY, no code/schema/Redis/DB/config install/modify, no hnswlib implement **Workspace:** `E:\chatbot` **Prior:** 77E `docs/AI_NATIVE_LLM_PHASE_77E_CONTEXT_ENGINE_ONECALL_FORENSIC_AUDIT.md`, 78A `docs/AI_NATIVE_LLM_PHASE_78A_ONECALL_CONTEXT_ENGINE_FORENSIC_AUDIT.md`, 78B single-gen fix `commerce/pipeline.py:619` **Model:** `core/config.py:90 qwen3:4b num_ctx8192` vs spec `Qwen2.5:3B` **LLM Path:** `llm_path new` `141` **77D:** `db/redis.py:158,224 XAUTOCLAIM payload preservation` **77E-78A verdict:** Context Engine wired but `context_engine_observational False` default

## 1. Executive Summary

Production **is** `llm_path=new` `core/config.py:141` → `workers/llm_worker.py:1042`. After 78B gate, **normal `hey beautiful` is 1 generative invocation** `core/one_call_pipeline.py:126` `qwen3:4b` `OneCallReply` validated; `generate_commerce_response` `pipeline:619` now gated `OFFER_PPV+EXECUTED` only, so `NO_OFFER` `2→1` waste removed. PPV still 2 generative (1 replaced) but PPV-specific required `USE 320`. Context Engine is **connected to OneCall boundary** `core/one_call_pipeline.py:55 retrieved_context` → `core/context_compact.py:102 system message` → Qwen `126 json(messages)`, but **production retrieval is NOT active** — `context_engine_observational False 135` default → `<1µs` early return `worker_integration:104` `rendered_text=""` skipped, Qwen sees legacy compact `persona+Fan|Stage+Rules` + state + `8 msgs` + `commerce_hints` only. RapidFuzz **dedup only conditional** `dedup.py:57 WRatio 0.85` when Engine enabled, not production memory retrieval; MiniLM `all-MiniLM-L6-v2 384` `embedding_model.py:51` **initialized/cached but not on CE path** (only `unified_intelligence` brute-force 110 intents offline); **hnswlib NOT IMPLEMENTED** `0 py` `pyproject` no dep `schema 101 B-tree`. Memory lexical bounded `retrieve_relevant_knowledge 5 531` `retrieve_relevant_memories 3 190` `overlap0.5` no semantic; ranking `scorer.py:33` 5 weights implemented but `conversation_state=None` `integration 167` → state `0.5 neutral` inert; budget hard `TOTAL2600 + ONE_CALL1150 <8192`. Intended `State→CE→RapidFuzz+MiniLM+hnswlib→ranked compact→one Qwen→deterministic authority` is **NOT matching actual**: `State→(CE gated off)→compact 1150→one Qwen→deterministic` — `RapidFuzz/MiniLM/hnswlib/semantic ranking/state relevance` **P1 gaps**, not P0 revenue/safety.

## 2. Actual Production Message Flow (code-wins, default `context_engine_observational False`)

```
Telegram NewMessage → handlers 142 → check_rate_limit 353 INCR ratelimit:{user} → upsert_user → generation_id md5(user:content:tgId) 63 → resolve_single_application_creator 68 → save_inbound → publish message.created 82 → debounce_enqueue SETNX lock: 318 → _wait_and_process sleep3 → get_debounced LRANGE 338 → enqueue_inbound XADD inbound_messages 184 generation_id md5 188

run_worker 1691 XGROUP CREATE llm_workers 48 → loop requeue_stalled_messages XAUTOCLAIM 224 fixed [(id,fields)] 243 → for claimed: process_message(generation_id preserved 1782) → ack_inbound 219 / move_to_dlq 250 → read_inbound XREADGROUP > 199 count5 block2000 → process_message 431 → ack/release

process_message 431: start_generation 451 → resolve_single_application_creator 459 → acquire_user_lock SET NX 285 → gather upsert_user+is_auto_reply_excluded 483 → get_structured_persona_async 499 → build_qwen3_context 509 (parallel get_user/get_user_profile/get_recent_messages 20 creator-scoped/get_latest_summary) → publish ai.generation_started 526 → observe_context_engine 539 (context_engine_observational False → enabled=False <1µs → _retrieved_context="" 558) → fail-closed 565 false → LTM extract 592 → fan_knowledge 608 → behavioral 632 → shadow disabled 648 → resolve_open_loop 669 → derive_conversation_state 695 + build_conversational_commerce_state 722 (signals None) → exposures 754 → pressure/risk 793 → derive_commercial_objective 875 + production_control 883 → operational 939 → derive_persona_behavior_state 1006 + render 1017 → context.append behavior_block 1022 →

IF new 1042: one_call_pipeline_with_fallback 1079 → build_one_call_context 84 (retrieved_context="" skip 102) → build_commerce_signal_hints 99 → validate_one_call_context 111 (>8192?) → provider.generate ONE_CALL_SYSTEM_PROMPT+COMMERCE_SIGNAL_INSTRUCTIONS json(messages) max400 temp0.7 126 → validate_one_call_response 146 json→OneCallReply extra=forbid→safety→quality → if valid: draft=reply score=quality signals=signals 1099 → _try_commerce_draft signals 1116 → resolve_and_run_commerce 385 → run_commerce_pipeline 459 → _apply_signal_flags 321 → decide_from_signals→build_strategy→orchestrate→ gated generate_commerce_response 619 ONLY if OFFER_PPV+EXECUTED else deterministic FAILED not_ppv_no_generation → selection 386 → if USE draft=commerce_text 1123 else → persona_validation 1329 severe→flag → is_auto_reply 1391 → dedup md5 393 → not auto → queue 1403 ; elif score>=0.80&&!flags → enqueue_send XADD send_messages 65 dedup + completed True 1456 else queue 1469 → publish_events_batch 1506 → post_process create_task 1671 background profile/summarizer

ELIF legacy 1209: extract_commerce_signals 1214 LLM1 → _try_commerce_draft 1220 → agent canary 1231 → run_agent 1252 → generate_draft_with_tools/draft 1263 LLM2 → score_draft 1303 LLM3

Send: _process_send_stream 77 requeue_stalled_send_messages XAUTOCLAIM fixed 158 → for claimed: _handle_send_entry → read_send_messages XREADGROUP send_workers > 96 → is_send_duplicate dedup:{creator}:{dedup} 89 → rate limit ZSET Lua 498 → blacklist → reserve_delivery vault 5m → send_file/send_message → mark_send_dedup → ack_send 115 / move_send_to_dlq 120 → save_outbound → publish message.sent
```

**Actual Context Engine flow when disabled:** `llm_worker 539 → worker_integration 104 return enabledFalse` → no `gatherer 854`, no `scorer 33`, no `dedup 57`, no `budget 147`, no `renderer 100`, no `retrieved_context` → Qwen prompt without `memory_block`. When `enabled=True` canary/test: `gather 7 → scorer → dedup` `Wratio 0.85` `assembler 93 → budget try_allocate 147 → renderer 100 → worker_integration 132 rendered_text memory/temporal/commerce/content → _retrieved_context 558 → one_call 55 → context_compact 102 system → Qwen 126`. **Code paths authoritative, not docs.**

## 3. Actual Context Engine Flow (detailed when enabled)

| Stage | File:line | What happens | Called production default? | Result to Qwen? |
|-------|-----------|--------------|----------------------------|-----------------|
| Observe entry | `workers/llm_worker 539` `context_engine/worker_integration 67` | `if not enabled: return enabled=False 104` `<1µs` | **YES but disabled** `context_engine_observational False 135` → no work | `""` skipped `context_compact 102` |
| Integration harness | `worker_integration 116-127` `ContextRequest(creator_id,user_id,current_message,generation_id)` `ContextEngineIntegration.process` | When enabled true | `ContextEngineIntegration.process 148` | `ContextRequest` flows to gatherer |
| Gather 7 sources | `gatherer 854 gather_all 865` `PersonaSource 137 HARD_POLICY 0, FanState 230, ConversationHistory 390, CommerceState 481, MemorySource 634 limit10, Temporal 718, Embedded 785` sequential try/except `876` `if creator_id None return [] 158` `WHERE creator_id=$1` `524,543` | Conditional `True` only | `~30-50 candidates` under `2600t` creator-scoped |
| Scorer | `scorer 33 SCORING_WEIGHTS source0.15 topic0.30 recency0.20 importance0.20 state0.10 authority0.05` `94 word overlap /len(query)`, `113 exp(-hours/168) week`, `131 cat_prior SYSTEM10`, `150 state base0.5+0.2 topic` | Conditional | scores, but `conversation_state=None` `integration 167` → state `0.5 neutral` |
| Dedup | `dedup 49 ContextDeduplicator 57 WRatio/100 0.85 fallback Jaccard 62 95 threshold 0.85 42 hash sha256 normalized 150 respect_creator_isolation True 179 skip if creator mismatch` `assembler 97` | Conditional | `deduped → budget` |
| Budget | `budget 90 TokenBudgetManager TOTAL 55×4 39 CHARS_PER_TOKEN 4 128 can_fit 147 try_allocate min(cat,global) <10 drop 163` `assembler 109 loop try_allocate per sorted scored/deduped` `164 total<=2600` `validate_assembly 186` | Conditional | trunc `[TRUNCATED]` 178 |
| Renderer | `renderer 86 CompactRenderer max_chars budget*4 93 100 render groups by category 115 truncate ... 196 HARD_POLICY first` `worker_integration 146 _blocks [memory,temporal,commerce,content] join -> rendered_text` | Conditional | `RenderedContext` fields |
| OneCall boundary | `one_call_pipeline 84 build_one_call_context 102 if retrieved_context.strip(): messages.append system` after state before conversation `107 trim 600 limit8` `126 provider.generate json(messages)` `worker_integration 146` | Conditional ("" skip) | **YES when enabled** single system message reaches Qwen |

## 4. Library Usage Matrix (Installed vs Production Retrieval)

| Library | Installed | Imported | Prod call | Result consumed | Actual role | File:line |
|---------|-----------|----------|-----------|-----------------|-------------|-----------|
| **orjson** | Yes `pyproject` `core/event_bus` fallback | `core/event_bus 6 try orjson dumps else json` `db/redis 5` | YES per publish/XADD | YES `_json_dumps` used | Production JSON serialization `event_bus 6` `redis 243` | — |
| **RapidFuzz** | Yes `pyproject 22 rapidfuzz>=3.0` `3.14.6` | `context_engine/dedup 57 from rapidfuzz import fuzz` `commerce/unified_intelligence 24 process.extract` | **CONDITIONAL** dedup `Wratio 0.85` only when Engine enabled (`assembler 97`); `unified_intelligence WRatio 80 limit3` offline never on OneCall (`one_call_pipeline` no import) | Dedup selected `assembler 97 → budget 109 → renderer 100 → rendered_text 146 → context_compact 102 → Qwen 126` when enabled | **DEDUP ONLY / OBSERVATIONAL** not memory retrieval candidate generation; `unified_intelligence` off-path dead for OneCall | `dedup 57, commerce/unified 24` |
| **SentenceTransformers / MiniLM all-MiniLM-L6-v2** | Yes `pyproject 23 sentence-transformers>=3.0` `commerce/embedding_model 19 _MODEL_NAME dim384` | `commerce/embedding_model 51 SentenceTransformer` `commerce/unified_intelligence 108` | **NO production retrieval** | **NOT consumed** `gatherer 677` calls `retrieve_relevant_knowledge` lexical not `encode`; `scorer 94` word overlap not cosine; `unified_intelligence 181 encode_message run_in_executor 110*384 cached 94` offline vs 110 intent examples not memories | **INITIALIZATION ONLY** singleton `30 lru_cache 42 _load_model global 51` lazy `workers 1708 warmup try get_model` per-process once, `encode` `62 50ms` not on CE path | `embedding_model 19,30,51,62 unified 108` |
| **hnswlib** | **NO** `pyproject` no dep `pip list` 0 | **NO** `grep hnswlib 0 py` `Index/knn_query/init_index/add_items/set_ef 0` | **NO** | — | **NOT IMPLEMENTED** (deferred `<1k vectors` `docs 46:262`) | `schema 101 B-tree` `vector_search_messages python _cosine_distance 16` `unified 85 brute-force no HNSW` |
| **tiktoken** | Yes `core/context` | `memory/context 50 count_tokens` | YES `context_compact 225 sum(count_tokens)` `validate 238 >8192 invalid` | YES | Token budget enforcement | `context_compact 225` |

*Initialization ≠ production usage.* Model loads (`get_model` lazy) is counted as `Installed+Imported` but `Production call = NO` because no encoding reaches `one_call_pipeline` → Qwen default path.

## 5. RapidFuzz Findings

Current production use: **only dedup**.

- `dedup.py:49-95` `ContextDeduplicator(similarity_threshold 0.85 95)` `_are_lexically_similar 57 try fuzz.WRatio(t1,t2)/100 >=0.85 else Jaccard 62` `42 _compute_content_hash sha256(normalized lower+collapse ws+strip punct)[:16]` `150 deduplicate(items respect_creator_isolation True)` `172 exact hash` `202 lexical_key category:source` pairwise `215 _are_lexically_similar` only same `category:source` `116 _should_keep higher Authority (lower numeric 53 HARD_POLICY 1.0) > priority > newer timestamp > longer content` `179 creator mismatch → distinct 207 same`.
- Not memory retrieval candidate generation: no `process.extract` over knowledge base with `score_cutoff` for memories; `unified_intelligence 42 WRatio 80 limit3` over **intent corpus 110 examples** is lexical intent evidence, **dead code** for OneCall (never imported in `one_call_pipeline`/`llm_worker new`).
- Per fan message invoked only when Engine enabled `assembler 97` `~30 items` pairwise bounded; deduped count `dropped` logged `worker_integration 154` `llm_worker 554`.
- Output contributes to `memory_block` → `rendered_text` → system message when enabled, otherwise none.

**Candidate generation?** No — gathers via PG `SELECT ... WHERE creator_id` lexical `retrieve_relevant_knowledge limit10 677`; RapidFuzz only removes near-duplicates after scoring, not creates candidates.

## 6. MiniLM / SentenceTransformer Findings

- `commerce/embedding_model.py:19-80` `30 _model_instance None global 32 @lru_cache get_model_name 42 _load_model if not None return else SentenceTransformer(_MODEL_NAME) CPU 52 dim384 51` singleton per `llm_worker` process `94 reference vectors cache global` `108 get_model` `111 encode_messages_sync 110*384 169KB` lazy `workers 1708 warmup try get_model`.
- Per-message `encode_message 62 run_in_executor None _encode model.encode([message],normalize True) 74 vec[0].tolist() 50ms` + `82 encode_messages_sync batch32` startup `111` 110 intent vectors.
- What embedded: `texts=[e["example_text"] for e in INTENT_CORPUS] 105` 110 intent examples **not fan memories**, per-message `current fan_message` `message 31 normalized lower/collapse` `unified_intelligence 181 vec=encode + 85 brute cosine dot 110*384 0.08ms` `threshold 0.65/0.10 25`.
- Embeddings persisted? **NOT for CE:** `message_embeddings embedding JSONB 70` exists but dimension legacy `1536` `text-embedding-3-small 27` not MiniLM 384, not used; queried vectors are in-memory `_reference_vectors` from `unified_intelligence 94`; `vector_search_messages 771 SELECT ... fetch all + python _cosine_distance` brute-force but **not called** via `retrieve_relevant_memories/knowledge`.
- Similarity reaches CE? **NO** `scorer 94 word overlap`, `gatherer 677 lexical`. No `cosine` in `scorer` or `gatherer`.
- Reaches Qwen? **NO** default `context_engine_observational False` → `unified_intelligence` not called in `one_call_pipeline` or `llm_worker new` `1079`.
- `MODEL EXISTS` vs `MODEL IS ACTUALLY PART OF PRODUCTION RETRIEVAL` → **EXISTS but NOT PART** — `INITIALIZATION ONLY / OBSERVATIONAL` `unified_intelligence` offline evaluator.

## 7. hnswlib Findings

Global `grep -r hnswlib` `0 .py` (`docs` proposals only), `grep Index.*384|knn_query|init_index|add_items|set_ef` exhaustive `0` outside tests `benchmark_local 42`. `pyproject` no `hnswlib`, `pip list` not installed `docs 74A NOT INSTALLED`. `db/schema.sql:101 -- HNSW requires pgvector using B-tree` `postgres 102 B-tree` `memory/retrieval 30 vector_search_messages` python loop `score=1-_cosine_distance 16`, `unified_intelligence 85 # Brute-force cosine (no HNSW, <1k vectors)`. Actual vector index: **PostgreSQL B-tree + JSONB + in-memory list + brute-force cosine**, none are ANN. **hnswlib = NOT IMPLEMENTED** — correctly deferred `PHASE_46:264 DO NOT IMPLEMENT NOW` `<1k` brute-force sufficient; would need `Index(space='cosine',dim=384).init_index(max_elements 10k ef 200 M16).add_items(embedding, id_hash(creator:user:subject:value)).knn_query(query_vec k5 ef50)`.

Actual retrieval vector index **not used in production** (B-tree index not vector).

## 8. Memory Storage Lifecycle

| Lifecycle | Creator | DB / Redis / Python | File:line | Creator-scoped? |
|-----------|---------|---------------------|-----------|-----------------|
| Create | fan message regex | `extract_fan_knowledge 135 76 _PATTERNS occupation/city/pet 21 TEMPORAL CURRENT/TEMPORARY/HISTORICAL` `extract_explicit_memories 244 color/preference/plan 50 PREFERENCE` via `llm_worker 592 extract_explicit_memories 614 extract_fan_knowledge creator_id,user_id,generation_id` | `fan_knowledge 135, long_term 244, llm_worker 592` | Yes `creator_id,user_id` |
| Persistence metadata | bounded JSONB | `user_profiles.facts JSONB fan_knowledge_by_creator: {creator_id:[{subject,value,category,confidence,source,observed_at,effective_from/until,expires_at,temporal_type,status CURRENT/HISTORICAL,confirmation_count,creator_id,user_id}] 30 per} 264` `long_term_memory_by_creator: {creator_id:[{memory_id,creator_id,user_id,memory_type,subject,value,confidence,source,first_seen,last_seen,observation_count,importance,expires_at,status}] 20 per} 113` `SELECT facts FOR UPDATE 285` bounded `ON CONFLICT 366` fallback `_knowledge_mem dict 273` `messages(id,user_id,creator_id,direction,content) schema:22` `conversation_summaries 47` | `fan_knowledge 277 add_knowledge_item 285 SELECT FOR UPDATE 366 INSERT, long_term 79 add_memory_item 82 get_user_profile` `schema 62` | **YES** `facts[_KNOWLEDGE_KEY][str(creator_id)] 291` `WHERE user_id=$1 FOR UPDATE` |
| Embedding generation | **NOT for stored memory** | `embedding_model singleton vec 384 normalize` + `_reference_vectors 110*384` in-memory `unified_intelligence 94` | `embedding_model 62 encode_message 82 encode_messages_sync, unified 110` | No memory table for 384 |
| Embedding persistence | legacy | `message_embeddings(message_id,user_id,embedding JSONB 70` dimension 1536 legacy `text-embedding-3-small 27` not MiniLM 384, legacy `embedding JSONB` `insert_message_embedding 756 JSONB` never on CE critical path | — | — |
| Retrieval | lexical | `retrieve_relevant_knowledge 531 get_fan_knowledge 544 filter not expired 550 subj_tokens overlap current_topic+open_threads 560 score=overlap0.5+conf0.3+recency(1-days/30)0.2+0.2 CURRENT 573 s>0.2 limit5` `retrieve_relevant_memories 190 limit3 overlap0.5+conf0.3+recency0.2+importance0.1 +0.3 OPEN_LOOP` `gatherer MemorySource 677 limit10` `Temporal 755` `ConversationHistory 427 get_recent_messages 20` | `fan_knowledge 531, long_term 190, gatherer 677,752, postgres 464 WHERE (creator_id=$2 OR NULL)` | **YES** per `creator_id,user_id` |
| Ranking | weighted scorer | `scorer 199 source0.15 topic0.30 recency0.20 importance0.20 state0.10 authority0.05` legacy `507` open_loop boost | `scorer 33,199` `assembler 93 scorer.score_items` | authority `0<1<2<3` persona wins |
| Rendering | compact blocks | `CompactRenderer 100 per-category budget*4 93 RENDERER 115 truncate ... 196 HARD_POLICY first` `worker_integration 146 blocks [memory,temporal,commerce,content] -> rendered_text` `core/context_compact 102 system` `memory/context 722 AVAILABLE CONTENT titles semantic only, 749 RELEVANT MEMORY,765 FAN KNOWLEDGE,796 LOCAL TIME` creator isolation | `renderer 100 worker_integration 132 context_compact 102` | — |
| Redis | streams/locks | `INBOUND_STREAM inbound_messages / SEND_STREAM send_messages / DLQ dead_letter_queue XADD/XREADGROUP XAUTOCLAIM 15-18 consumer_group llm_workers 40 acquire_user_lock lock:creator:{c}:user:{u} 283 debounce:creator:{c}:user:{u}:messages RPUSH 315 send_dedup:{creator}:{dedup} SETEX 84 persona:{creator}:{user} 381` | `redis 15-40,283,315,84,381` | creator isolated |

**Do message_embeddings have stored embeddings?** `message_embeddings` exists but **dimension mismatch 1536** not MiniLM 384, **not populated for CE memories**, not queried for `retrieve_relevant_knowledge` (lexical). **LEGACY ONLY / DEAD** for production OneCall.

## 9. Current Memory Retrieval (retrieve_relevant_knowledge)

`commerce/fan_knowledge.py:531 retrieve_relevant_knowledge(creator_id,user_id,current_topic,open_threads,limit=5, profile)` input `creator_id,user_id, current_topic string or None, open_threads tuple, limit int, profile dict` via `workers/llm_worker 998 query=user_message 1000 limit5` + `memory/context 756/774` also via `gatherer 677 limit10`. Candidate source: `get_fan_knowledge(creator_id,user_id) → list[dict]` from `user_profiles.facts` JSONB `544` `filter not is_knowledge_expired 550`. Matching: tokenization `re.findall \w+ lower` → `subj_tokens = set(topic.split)`? Actually `current_topic+open_threads[:3] joint tokens 560` `overlap = len(subj_tokens ∩ query_tokens)/len(query_tokens)` `candidate limit none` `score=overlap0.5+conf0.3+recency(1-days/30)0.2 +0.2 if CURRENT 573` `0-1`. Threshold `score>0.2` `579` sorted desc `limit5`. **Lexical/overlap retrieval, NOT semantic** — no `fuzz`, no `encode`, no `cosine`, no `embedding`. Overlap logic exact token set intersection, case-insensitive, no stemming, no WRatio. Dedup later via `dedup 150`. Caller: `workers/llm_worker 998` (persona behavior fan_knowledge 5) + `memory/context 756 LTM 3, 774 knowledge 5, gatherer 677 knowledge 10`. **Available but lexical**.

## 10. Context Ranking Audit

Actual formula `scorer.py:33` `SCORING_WEIGHTS source0.15 topic0.30 recency0.20 importance0.20 state0.10 authority0.05`. **source** `210 source_scores postgres1.0 redis0.85 ...` `source_score`; **topic** `213 compute_topic_overlap set(content.lower.split) & set(query.lower.split)/len(query)` word overlap, **NOT MiniLM cosine**; **recency** `217 exp(-hours/168)` week half-life `113`; **importance** `222 cat_prior SYSTEM10 STATE9.../10*0.7+prio/10*0.3 131`; **state** `150 compute_state_relevance base0.5 +0.2 if current_topic in content +0.15 if COMMERCE+buying_signal` weight `0.10`; **authority** `53 HARD_POLICY1.0 DETERMINISTIC_RULE0.9 DERIV2 0.8`. Legacy `long_term 560` `overlap0.5+conf0.3+recency0.2+importance0.1`.

**State populated?** `workers/llm_worker 694 _conv_state=derive_conversation_state(context)` exists but **NOT passed**: `integration 167 snapshot=assembler.assemble(candidates,query=request.current_message)` no `conversation_state` arg → `assembler 74 default None` → `scorer 152 if None return 0.5 neutral` constant `0.05` contribution never discriminates. Weight exists but value neutral/defaulted/hardcoded 0.5. **Not derived** for ranking.

## 11. Authoritative State Audit

| Domain | Source query/function | Authoritative? | Passed to CE? | Passed to Qwen? | Source file |
|--------|----------------------|----------------|---------------|-----------------|-------------|
| Persona `identity,demographics,lifestyle` | `SELECT metadata,version FROM personas WHERE creator_id=$1 ORDER BY is_default 318` `get_structured_persona_async` `render_compact_persona_block 654 FACTS/BEHAVIOR/LIFESTYLE` `HARD_POLICY 0 137` | **AUTHORITATIVE** | Yes `PersonaSource 169 priority10` | Yes `memory/context 685 CREATOR PERSONA compact` `context_compact 127 persona_block` system350 | `creator_persona 318,36` |
| Fan/business `funnel_stage,message_count,is_blocked,do_not_auto_reply,first_name,relationship` | `get_user 115, get_user_profile 549 FanStateSource 235` `DETERMINISTIC_DERIVATION 2` | **AUTHORITATIVE** | Yes `FanState 245 Funnel:..` | Yes `STATE fan|funnel 279 RELATIONSHIP` | `postgres 115, gatherer 214` |
| Conversation `recent 20, summary, intent/stage/mood/topic/last_action/objection` | `get_recent_messages 464 WHERE (creator_id=$2 OR NULL) 20, get_latest_summary 583, derive_conversation_state 157 694` `ConversationHistorySource 390 DETERMINISTIC_RULE 1` | **AUTHORITATIVE history + derived** | Yes `394 each turn` | Yes `recent conversation 814 trimmed 600 limit8` `CONVERSATION topic` `357` | `memory/context 521,357` |
| Commerce `offer,product,eligibility,pricing,execution` | `resolve_commerce_state 169 SELECT get_user, dropfans_integration, fangate_product, timing, behavioral, has_purchased 177-223 evaluate_ppv_eligibility 223 PolicyDecision` `CommerceStateSource 481 DETERMINISTIC_RULE 1` | **AUTHORITATIVE** | Yes `466 purchases SELECT COUNT pending etc` | Yes `AVAILABLE CONTENT titles 724` `Purchases N 729` `has_relevant_product` `commerce_prompt 99` | `state 169` |
| Access/payment `subscription,payment,purchase,access` | `has_purchased_product SELECT state purchased 212, fangate_transactions transaction_id, commerce_offers state 212` `OfferContext already_purchased,has_active_offer` | **AUTHORITATIVE** | Partial counts only `523` no `transaction_id` | Minimal `COMMERCE: Purchases N` never secret | `dao, execution 212, gatherer 515` |

**Authoritative = truth, retrieved = supplementary** hierarchy `models 14 HARD_POLICY0< DETERMINISTIC_RULE1< DERIV2< ASSEMBLY3< LLM4` persona 0 never overridden.

## 12. Context Engine → OneCall Boundary

`ContextEngineIntegration.process 148` → `candidates gather_all 162 → snapshot assembler 167 → rendered renderer 174 → RenderedContext` `integration 185 candidate/selected/total_tokens`. `worker_integration 132-155 rendered_text = "\n".join([memory_block,temporal_block,commerce_block,content_block])` `146 blocks` `rendered.system_prompt/state excluded`. `workers/llm_worker 558 if not failed and rendered_text: _retrieved_context=rendered_text` `one_call_pipeline 55 retrieved_context` `84 build_one_call_context 102 if retrieved_context.strip(): messages.append({"role":"system","content":retrieved_context})` single system message after state before conversation `107 trim 600 limit8` → `126 provider.generate json(messages)`. **Does output materially change prompt? YES when enabled** (adds system memory) else NO (empty skip). `memory_block temporal commerce content` distinguished, not overwriting persona `HARD_POLICY`. Boundary is `one_call_pipeline 84` deterministic, no LLM in CE.

## 13. Feature-Flag Behavior

| flag | default | env override | production behavior | Branch file:line |
|------|---------|--------------|---------------------|------------------|
| `context_engine_observational` | `False 135 Phase73/77B` `When true Context Engine runs and rendered_text passed to OneCall fail-open` | `CONTEXT_ENGINE_OBSERVATIONAL` `Settings env_file .env extra ignore 167` | `workers 546 enabled=_settings.context_engine_observational → observe_context_engine 540 104 if not enabled return enabled=False <1µs` → `_retrieved_context=""` → Qwen sees legacy 1150 only | `config 135, worker_integration 104, llm_worker 539` |
| `context_engine_canary_mode` | `disabled 148 disabled\|observe 150 sample 0 timeout30 max500 model "" 149` | `CONTEXT_ENGINE_CANARY_MODE etc` | `worker_integration 241 CanaryConfig 246 DISABLED→None 249 should_run hash 259 observe parallel never send 284 fail-open` not on prod OneCall injection | `config 148, worker_integration 241` |
| `llm_path` | `new 141 new\|legacy` `When one-call fails routes to operator queue no legacy cascade` | `LLM_PATH` | `llm_worker 1040 new→one_call 1079 else legacy 1209 3-LLM legacy-only 1214` failure hardened `1129` | `config 141, llm_worker 1040` |
| `llm_provider` | `ollama 84` `ollama_model qwen3:4b 90` `num_ctx8192 94` | `LLM_PROVIDER OLLAMA_*` | `llm_provider 184 OllamaProvider 89 https` `provider.generate` | `config 84, provider_ollama 90` |
| `shadow` `qwen_shadow_enabled` | `False 99 sample0` | `QWEN_SHADOW_*` | `llm_worker 648 if enabled&&should_sample&&creator → create_task run_shadow 651` best-effort | `config 99, llm_worker 648` |
| `ai_agent_canary_enabled` | `False 128 sample0` `ai_runtime_mode legacy 117` | `AI_AGENT_CANARY_*` | `llm_worker 1231 should_use_agent false→never` | `config 128, llm_worker 1231` |

**Active for every fan?** **NOT active** default `False` → observational only `0%` production unless `CONTEXT_ENGINE_OBSERVATIONAL=true` (requires redeploy/restart). No percentage rollout without code change (boolean).

## 14. Current LLM Call Count (actual runtime after 78B gate)

`core/one_call_pipeline 126 provider.generate` structured + `one_call 113/127 invalid → is_valid False` + `validate_draft_quality 150` deterministic `scoring_deterministic` not LLM. Second `generate_commerce_response pipeline 619` now gated `OFFER_PPV+EXECUTED` only `78B` (was unconditional) → normal `1`.

| Path | Normal | Non-PPV commerce | PPV-ready | Handoff | OneCall validation fail | Exception | Agent | Recovered |
|------|--------|------------------|-----------|---------|-------------------------|-----------|-------|-----------|
| `hey beautiful` | **1** `N1` +0 `N2 gated FAILED not_ppv_no_generation` → `FALLBACK` `selection 280` total1 | same | — | same | — | — | 0 never new `1230` | same |
| `how was your day?` | **1** | — | — | — | — | — | — | — |
| `how much is that?` price `SOFT_OFFER` | **1** (decided `SOFT_OFFER` → gated no LLM) | — | — | — | — | — | — | — |
| `send it` PPV `OFFER_PPV EXECUTED` | — | — | **2** `N1 replaced 1123` + `N2 GENERATED 545 USE 320` | — | — | — | — | — |
| `objection too expensive` | **1** | — | — | **1** (handoff `negative≥2 → RELATIONSHIP_BUILDING 415` → discarded) | — | — | — | — |
| Handoff `needs_handoff` | **1** | — | — | **1** `safety 154` → queue `1402` `score>=0.80 && !flags` fails | — | — | — | — |
| OneCall invalid JSON/Pydantic `415` `extra=forbid` `bounds` | **1 attempted 0 extra** → `1129 operator_queue ["one_call_invalid_result"] publish completed+suggestion 1150 return hardened no legacy` | — | — | — | 1→ queue | — | — | — |
| Exception `timeout120` `429` `empty 259` | **1 attempted** → `134 Generation failed → 1169 ["one_call_exception"] 1188 return` no N2 | — | — | — | — | 1→ queue | — | — |
| Legacy `legacy` `hey` | 3 advisory+Qwen+score | — | — | — | — | — | 4 `L3a` if canary true | — |

**One generation invariant PRESERVED for normal/non-PPV** after 78B (`hey beautiful` 2→1). PPV `2` is PPV-specific required replacement.

## 15. PPV Authority Verification

Same as 77E: trace `fan buy → OneCall commerce_signals advisory → resolve_single_application_creator READY 350 → resolve_commerce_product_with_history 359 creator-scoped is_accessible+sales_url 60 exclude purchased 86 rel>=0.15 282 → CommerceStateRequest 378 → resolve_commerce_state 130 + run_commerce_pipeline 146 → _apply_signal_flags 303 mechanical → decide_from_signals pure priority 299 1 eligibility denied→NO_OFFER 1.5 handoff→OPERATOR 1.7 free_content→RELATIONSHIP 2 sales_enabled 3 has_relevant 4 active 5 purchase 6 offer 7 budgets 2/3 7.5 fatigue 7.6 negative≥2 7.7 low_conf 7.8 opening 7.9 paused 7.10 aftercare 7.11 rejection≥3 8 explicit/intent 0.95/0.85→OFFER_PPV 517 11 moderate0.55→SOFT 12 rel0.60→SOFT else building, allowed only SELL_ACTIONS 231 → orchestrate 592 activation requires eligibility allowed+product → execute_ppv 88 12 gates 1 OFFER_PPV allowed 103 else DENIED 2 integration active 120 3 decrypt 129 4 blocked 153 5 SELECT fangate_products 168 missing→PRODUCT_UNAVAILABLE 182 extract dropfans_id 191 6 pending 205 ALREADY_EXECUTED 7 purchase 212 7 eligibility re-eval 219 deny wins 8 link sales_url else build_checkout_url 244 price price_minor 252 USD 266 mismatch→PRODUCT_UNAVAILABLE 256 10 lock ppv_offer:{c}:{u}:{p} 86 INSERT 103 race After ambiguity 281 publish 314` → `pipeline 619 gated now` `response → selection only USE when COMPLETED+OFFER_PPV+EXECUTED+GENERATED 320`. **Proof Qwen cannot set price:** `requested_price` `signals 193` only bool `user_asked_about_price 324` `ApplicationOwned 320` never `price_minor`, `PriceMinor >=0 114` `CHECK >=0 25`, Tool `propose_product_offer 1104 You may only suggest product_id price set by application`, signature `execute_ppv(*,creator_id,user_id,product_id,decision) 88` **no price param** `570 test signature`, `retrieve_relevant_knowledge` never authoritative commerce state, memory `price $20` stale never reaches `execution 252`.

## 16. Creator-Isolation Verification

Every `WHERE creator_id=$1` `db/postgres 464 get_recent_messages (creator_id=$2 OR NULL)`, `fan_knowledge_by_creator 291, long_term 82, ContextItem creator_id 145 GathererConfig creator_id 40, dedup respect_creator_isolation True 153 skip if mismatch 179,207, lock:creator:{c}:user:{u} 283, debounce:creator:{c}:user:{u}:messages 315, send_dedup:{creator}:{dedup} 84, persona:{creator}:{user} 381, ContextItem category:source lexical_key + creator`. Semantic cross-creator blocked: `unified_intelligence` not on production; even if enabled, `respect_creator_isolation` prevents. Qwen context `creator_id` already isolated earlier, not re-filtered but source isolated. **PRESERVED**.

## 17. Context-Budget Verification

`TOTAL 2600 55 SYSTEM400 STATE200 COMMERCE200 MEMORY150 KNOWLEDGE150 TEMPORAL50 CONTENT100 CONVERSATION800 58 budget*4 93 RENDER 93 truncate ... 196 HARD_POLICY first` `budget 70 can_fit cat+global 90,147 try_allocate min(cat,global) <10 drop 163, validate 218 >2600 violation 186, tests 954 <=2600`. `ONE_CALL 1150 system350 state150 conv600 signals50 26 limit8 109 MAX_ASSISTANT 3 116 validate_one_call 238 >8192 or system>350 invalid 246` `Ollama num_ctx 8192 94 provider 169 hard`. Truncation **after** gather→scorer→dedup **before** renderer `assembler 109` then renderer secondary `budget*4`. `1150+2600=3750<8192` safe. Retrieved cannot exceed hard limit.

## 18. I/O / Latency Impact (vs 74B baseline 7 PG 4 Redis)

PG `get_user 1 + get_user_profile 1 cached + get_recent_messages 20 1 + get_latest_summary 1 + fan_knowledge 0-1 + is_user_auto_reply_excluded 1 + add_to_operator/enqueue_send 1 + resolve_single_application_creator 2 cached` ≈`7 PG` unchanged. Redis `acquire_user_lock 285 1 + is_auto_reply_enabled 485 1 + publish started 1 + batch completed 1 + XADD 1` `4 Redis`. **When disabled:** `retrieved_context` adds `0` PG/Redis, `0` embedding, `0` RapidFuzz, `0` ranking, warm path <1µs, cold not. When enabled: add `gather 7 sequential` reuse cached `get_user` etc. `+0-1 PG` `get_dropfans_integration` if miss, `+0 Redis`, `+~30-50 dedup pairwise` CPU, `+50ms` if MiniLM warm (currently 0 because offline), `+2ms scorer` `+1ms renderer`. No redundant `profile parsing` beyond `_profile_cache 578`. `74B optimization preserved` `asyncio.gather return_exceptions True 543` `orjson 6` `publish_events_batch pipeline 94`.

## 19. Failure-Mode Analysis

| Failure | Trace | Result | LLM cascade? |
|---------|-------|--------|--------------|
| RapidFuzz import fails `dedup 57 ImportError` | `62 Jaccard` fallback | trunc `[TRUNCATED]` 178 still | NO legacy |
| MiniLM `get_model` warmup `embedding_model 51` fails `workers 1708 try except debug` | `unified_intelligence 108` not on path, no effect | NO |
| Vector retrieval `vector_search_messages` python fetch-all `_cosine_distance 16` | not called via CE | NO |
| hnswlib unavailable | 0 `pyproject` → not called | NO |
| Memory retrieval `get_fan_knowledge` throw `fan_knowledge 544` | `retrieve_relevant_knowledge 531` catches → empty list 550 | empty memory block | NO |
| Context Engine throws `gather 876` | `worker_integration 186 except → failed=True` `560 if not failed and rendered_text` skip → `""` | empty `retrieved_context` | NO cascade `186 fail-open never affects production:9` |
| OneCall timeout `ollama 217` /invalid JSON `one_call 113` /Pydantic `127 extra/forbid` /empty `259` /commerce malformed | `one_call_pipeline 134 Generation failed is_valid False 1169 except → add_to_operator_queue draft="" confidence0 flags one_call_* 1179 + publish completed+suggestion 1188 return` `1129 invalid → same 1137` | operator queue `was_auto_approved False` `1150` | **NO legacy** hardened `1133 no legacy cascade` `fallback dummy 0 215` bypassed |
| Pydantic validation `reply 1..2000 extra/forbid` fails | `is_valid False` → same as above | queue | NO |
| Commerce decision fails `decide 566` | `DECISION_FAILED 573 no response` | FALLBACK to OneCall reply via `selection 80` | NO |
| DLQ `move_to_dlq 276` leaves pending if `XADD DLQ fails 278 false` | `requeue_stalled 224` next idle 60s reclaims | pending retry | NO loss infinite reclaim fixed 77D |

Intended `Context Engine failure → safe degraded context → OneCall / controlled failure` **PRESERVED** `worker_integration 186 → "" → OneCall with 1150`.

## 20. Observability

Fields exist `core/telemetry GenerationTelemetry context_build_ms,context_chars,context_engine_enabled/ms/gather_ms/candidates/selected/dropped/tokens/chars 548, shadow_launched/timeout, commercial_objective, experiment_exposure, pressure_bucket/risk_state, operation_decision_allowed, persona_behavior_derived, routing_decision one_call/one_call_commerce/one_call_failed, generation_latency_ms 1206, provider_latency, shadow_latency, persona_validation_status`. Retrieval `gather_time_ms assembly_time_ms 160` candidate/selected `154` context size `generation_context_chars 515` `estimate_one_call_tokens 225`. **Exists:** `context_engine_used, lexical candidates? No (dedup count only), semantic candidates NO (MiniLM offline), selected memories 554, deduped 554 dropped, ranking score not per-item, context_chars/tokens yes, embedding latency NO (MiniLM offline), retrieval latency gather 5ms synthetic, ranking latency assembly 2ms, one_call_latency generation_latency_ms yes, validation failures via `failure_code one_call_invalid_result` 1137. `llm_call_count` not explicit counter but inferred `routing_decision one_call vs one_call_commerce` + `is_valid` + `generate_commerce_response` gating 78B `not_ppv_no_generation`. No `message content` logged.

## 21. Test Coverage

| Test suite | Count | Type | Prove semantic memory to Qwen? | File |
|------------|-------|------|--------------------------------|------|
| `test_phase72_context_engine_integration` | 62 | unit | **NO** `enabled=False` mock, not live DB | `tests/test_phase72` |
| `test_context_engine` `test_context_assembler` | 30+ | unit | NO | — |
| `test_phase77b_context_engine_integration` | 13 | unit | **NO** `build_one_call_context retrieved_context` mock not live | `tests/test_phase77b` `103` |
| `test_phase78b_single_generation` | 12 | unit | **NO** semantic, proves 1 LLM `mock one_call 1` `mock generate_commerce_response 0 non-PPV` `tests 78B` | `tests/test_phase78b` |
| `test_phase77d_xautoclaim_recovery` | 16 | integration mock Redis `xautoclaim 8.1.0` | NO semantic | `tests/test_phase77d` |
| `test_redis_recovery` | 31 | integration `return [(id,dict)]` `77D` | NO | `tests/test_redis_recovery` |
| `test_redis` / `test_realtime` | — | unit | NO | — |
| **Production-path semantic memory** | 0 | — | **Is there a test proving actual production OneCall receives retrieved semantic memory from MiniLM/hnswlib? NO** `explicitly` — only observational `rendered_text` string mock | — |

Most tests `mock-only` `AsyncMock` `patch get_redis` `return [(id,dict)]`, not real PG `get_recent_messages`. `observational-only` `test_phase77b TestContextEngineObservationRenderedText` `worker_integration 146` mock, not live. **Gap:** no integration `fan message → PG INSERT fan_knowledge → retrieve_relevant_knowledge lexical overlap → CE scorer → Qwen prompt contains memory` live DB test.

## 22. Exact Gaps

| Gap | Evidence | Severity |
|-----|----------|----------|
| Second generation unconditional waste for non-PPV **FIXED 78B** now gated `pipeline 619 OFFER_PPV+EXECUTED` → normal 1 | `pipeline 619→649 deterministic FAILED` `selection 280 FALLBACK` | **P0 fixed** |
| Context Engine production retrieval **INACTIVE** default `False 135` `enabledFalse <1µs` → 0 retrieved in prod | `worker 539 → 104 return` `context_compact 102 skip` | **P1** intended `RapidFuzz+MiniLM+hnswlib ranked compact` not active |
| RapidFuzz **only dedup** not memory retrieval candidate generation | `dedup 57 WRatio 0.85 same category:source` `unified WRatio 80 limit3` offline never `process.extract over knowledge base` | **P1** spec `lexical retrieval → RapidFuzz` not satisfied |
| MiniLM **init only** not production retrieval | `embedding_model 51 lazy singleton` `encode_message 62 not called from gatherer 677 lexical` `scorer 94 word overlap` | **P1** spec `semantic → MiniLM+hnswlib` not active |
| hnswlib **NOT IMPLEMENTED** 0 py | `pyproject no dep` `schema 101 B-tree` `unified 85 brute-force` | **P1** spec but P3 per `<1k` `<1k vectors 1k` not needed scale | 
| `conversation_state` **neutral** `0.5 weight 0.10 wasted` | `integration 167 query only  None default assembler 74 → scorer 152 None→0.5` | **P1** state relevance inert though weight exists |
| `qwen3:4b 90` vs `Qwen2.5:3B` spec stale | `config 90 qwen3:4b` `context_compact 1 Qwen2.5` | **P1** quality drift |
| `context_engine_canary_mode observe 0.0` boolean not `10%` rollout | `config 148 disabled\|observe 0.0` `worker_integration 241 246 DISABLED→None` no `should_sample 10%` for OneCall injection | **P2** |

## 23. Priority Classification

| P | Gap |
|---|-----|
| **P0** | None after 78B (second-gen waste fixed) + 77D (XAUTOCLAIM) fixed. Previous P0 XAUTOCLAIM fields discard `db/redis 243` resolved 77D. |
| **P1** | Context Engine production retrieval inactive (observational `False` default) → intended `RapidFuzz+MiniLM+hnswlib ranked compact` not active for every fan message; RapidFuzz only dedup not retrieval; MiniLM offline; hnswlib not implemented; `conversation_state` neutral; `qwen3:4b` vs `Qwen2.5:3B` mismatch |
| **P2** | Robust rollout `10%` canary `148 boolean` not percentage; observability `embedding_latency/semantic_candidates/per-category tokens/rapidfuzz scores` missing; log stale `one_call_pipeline 192 One-call failed, falling back` dummy |
| **P3** | Scale `hnswlib` defer justified `<1k`; deterministic PPV template to make PPV also 1 generative (currently PPV-specific 2) future |

## 24. Recommended Next Phase (surgical, no architecture redesign)

| Change ID | Root cause | File | Line | Current | Required | Why | Dependencies | Risk | Tests | Rollback |
|-----------|------------|------|------|---------|----------|-----|--------------|------|-------|----------|
| P1-01 | Context Engine gated off | `core/config 135` `workers/llm_worker 539` `worker_integration 104` | `context_engine_observational False → enabled=False` | Flip to `True` behind `pct` sampler `hash(user_id)%100 <10` creator sharding, wire `conversation_state` param `ContextRequest` `integration 167` | Enable `10% canary` without full flip, activate `state weight 0.10` | `telemetry 549, scorer 152` | Low deterministic | `test_phase77b enabled True` `test_context_engine_gatherers` `test_phase78b` mock `should_sample` | `RL.AGEN` boolean |
| P1-02 | RapidFuzz only dedup not retrieval | `context_engine/gatherer 677` `retrieve_relevant_knowledge 5` lexical only | Add `process.extract(fuzz.WRatio, score_cutoff80 limit5) over fan_knowledge_by_creator texts` merge with `overlap0.5` lexical candidates before `scorer 199` (hybrid lexical→rank→dedup) | Achieve `lexical retrieval → RapidFuzz` spec without sem vector | `scorer weights` `gatherer 677` | Low additive candidate | `test_rapidfuzz lexical retrieval vs dedup` `creator isolation hash` | Remove `process.extract` |
| P1-03 | MiniLM/hnswlib offline | `commerce/embedding_model 51` `unified_intelligence 108` `db/schema 101` | Wire `encode_message(current_message) 62` embedding `384` + `encode_messages_sync` for new memories at `add_knowledge_item 277` persist `message_embeddings embedding JSONB` `384 dim + creator_id/user_id` + `vector_search_messages 771` cosine `1-_cosine` fetch-all vs `retrieve_relevant` merge `semantic+lexical` `score 0.65` before scorer → replace `topic word overlap` with `cosine*0.30` | Make `semantic retrieval → MiniLM (+ hnswlib defer brute <1k)` active spec; until `>10k` keep brute, gate `hnswlib` behind `>1k` check | `gatherer 677` `scorer 94` | Medium CPU `50ms` `run_in_executor` | `test_embedding 50ms` `test_hnswlib not needed` `test_semantic_lexical hybrid` | Feature flag `embedding_enabled` boolean |
| P1-04 | State relevance inert | `integration 167` `assembler 70` | Pass `conversation_state=_conv_state` `llm_worker 694` via `ContextRequest 116` to `assembler assemble 70` to `scorer 199` | Activate weight `0.10` already exists | Low | `test_scorer state 0.5 vs 0.7` | Revert param |
| P2-01 | Model mismatch | `core/config 90` `context_compact 1` | Re-pin `qwen3:4b` vs `qwen2.5:3b` or update docs | Remove P1 confusion | Low | `model_alignment` test | env `OLLAMA_MODEL` |
| P2-02 | Observability gap | `core/telemetry` `worker_integration 132` | Add `embedding_latency, rapidfuzz scores, per-category tokens` already `integration 132` but log | Prove canary not regress | Low | `test_telemetry` | boolean |
| P3-01 | hnswlib defer | `unified 85` `<1k brute` | Keep defer, gate behind vector count `>1000` | Avoid premature complexity | None | benchmark `>1k` | N/A |
| Existing 78B | Second-gen waste | `commerce/pipeline 619` | Already gated `OFFER_PPV+EXECUTED` 78B | Keep | — | — | — | — |

---

ROOT VERDICT:
CONDITIONALLY READY — OneCall single-generation fixed 78B (normal 1, PPV-specific 2), Redis 77D fixed, Context Engine fully implemented+connected but gated off (0% prod), PPV authority/creator isolation preserved; NOT READY for intended State→CE→RapidFuzz+MiniLM+hnswlib→ranked compact→one Qwen (P1 retrievals inactive).

ACTUAL ARCHITECTURE:
Fan → handlers debounce RPUSH → XADD inbound_messages generation_id md5 → XREADGROUP llm_workers > → process_message acquire_user_lock → parallel get_user/profile/recent20/summary → publish started → observe_context_engine disabledFalse (<1µs) → _retrieved_context="" → build_qwen3_context → derive persona behavior 1023 → build_one_call_context 102 skip retrieved → provider.generate ONE_CALL 126 json messages 400 temp0.7 qwen3:4b → validate Pydantic extra=forbid → commerce _try_commerce_draft signals → run_commerce_pipeline → gated generate_commerce_response 619 ONLY if OFFER_PPV+EXECUTED else deterministic FAILED → selection FALLBACK → persona_validation → is_auto_reply→enqueue_send XADD SEND_STREAM dedup creator-scoped → publish completed was_auto_approved → ack/release → send XREADGROUP send_workers > → is_send_duplicate → rate limit ZSET Lua → blacklist → reserve → send → ack/DLQ (77D reclaim preserves fields before >).

INTENDED ARCHITECTURE:
AUTHORITATIVE STATE (persona/fan/conversation/commerce/subscription) → CONTEXT ENGINE (RapidFuzz lexical + MiniLM/hnswlib semantic merge ranking dedup hard budget 2600) → COMPACT 1150 + recent8 + retrieved → ONE QWEN2.5 generation → Pydantic → deterministic commerce/persona/safety/send.

PRIMARY GAP:
Context Engine production retrieval inactive by default — RapidFuzz only dedup conditional when enabled, MiniLM all-MiniLM-L6-v2 initialized/cached but not on CE gatherer/scorer path (only offline unified_intelligence 110 intent brute-force), hnswlib 0 py not implemented, memory retrieval lexical overlap 5/3 not semantic hybrid.

SECONDARY GAPS:
conversation_state weight 0.10 but None→0.5 neutral (integration 167 not passed); qwen3:4b vs Qwen2.5 spec stale; canary boolean observe 0.0 not 10%; second-gen waste already gated 78B for non-PPV (PPV still 2 but required) vs strict 1; observability embedding/rapidfuzz/per-category missing.

P0:
None after 78B/77D (previous P0 XAUTOCLAIM fixed, second-gen waste gated). 

P1:
P1-1 Context Engine 0% prod (observational False default) → RapidFuzz/MiniLM/hnswlib not active retrieval (P1); P1-2 State relevance inert despite weight; P1-3 Model mismatch qwen3:4b vs Qwen2.5.

P2:
P2-1 Canary 10% rollout requires pct sampler not boolean; P2-2 Observability gap embedding/rapidfuzz/per-category; P2-3 Log stale fallback; P2-4 PPV second generation still 2 (PPV-specific) vs strict 1 ideal.

P3:
P3-1 hnswlib defer justified <1k brute keep B-tree; P3-2 Scale 1k→10k hnswlib later; P3-3 Deterministic PPV template to make PPV also 1 generative (currently 2).

NEXT PHASE:
P1 surgical Stage B: flip Context Engine 10% canary behind `should_sample(user_id)%100<10` creator sharding, wire `conversation_state` into scorer, add RapidFuzz lexical retrieval merge (process.extract 80 limit5 hybrid) before scorer, wire `encode_message 50ms`/`vector_search_messages` brute cosine semantic merge (MiniLM) with `score 0.65` hybrid before dedup/budget, keep `hnswlib deferred`, gate `qwen3:4b` doc alignment, keep pipeline gate 619 and Redis 77D. Prove 10% prod Qwen receives retrieved semantic memory. Do NOT move price authority, Redis Streams, DLQ, dedup.

ARCHITECTURE CHANGES:
NONE

PPV AUTHORITY:
PRESERVED — fangate_products.price_minor 252 USD immutable CHECK>=0, serialized lock ppv_offer:{c}:{u}:{p} 86 pending check 89 INSERT 103, URL/price/offer claim integrity 426/439/457 only when EXECUTED, Qwen requested_price advisory only 324 signature 88 no price.

CREATOR ISOLATION:
PRESERVED — WHERE creator_id, lock:creator:{c}:user:{u}, debounce:creator:{c}:user:{u}, send_dedup:{creator}:{dedup}, persona:{creator}:{user}, ContextItem creator_id dedup 179.

ONE-CALL INVARIANT:
PRESERVED (normal 1 after 78B gate, PPV 2 PPV-specific required but deterministic commerce authority intact).

REDIS DELIVERY ARCHITECTURE:
PRESERVED — 77D XADD/XREADGROUP/XAUTOCLAIM [(id,dict)] preserved 243, XACK after process 221/115, DLQ payload json+XACK 276, creator dedup, reclaimed before > intact.
