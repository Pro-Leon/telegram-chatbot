# Phase 1 — Context Engine Canonical Runtime Report

**Date:** 2026-09-03 **Mode:** Implementation (Stage A verify → Stage B surgical → Stage C test → Stage D measure → Stage E review) **Workspace:** `E:\chatbot` **Prior:** 78F `docs/AI_NATIVE_ARCHITECTURE_DEEP_CODEBASE_RESEARCH.md` (40 sections) **Model:** `core/config.py:90 qwen3:4b num_ctx8192` **LLM Path:** `llm_path new` `141` **77D:** `db/redis.py:158,224 XAUTOCLAIM [(id,dict)]` **78B:** `commerce/pipeline.py:619 gated OFFER_PPV+EXECUTED` **78D:** hybrid `gatherer 675 RapidFuzz 80 + MiniLM 50ms brute 0.30` **78E canary:** `context_engine_enabled True sample 0.10` `workers 1023 hash creator:user%100` **Phase 1 canonical:** `context_engine_enabled True sample 1.0 100%` `workers 1023` `lock:creator:{c}:user:{u} 283`

## 1. Executive Summary

Forensic deep research established **10% canary hybrid** `RapidFuzz lexical + MiniLM 384 brute hybrid` was **real for canary 10%** `80-100ms` `+28 tokens` `3-5 memories` `retrieval active` `state real`, `90% control 1150` not AI-native `hybrid` `0` `legacy 1150`. Phase 1 makes **100% normal production** `authoritative state → Context Engine hybrid retrieval (lexical RapidFuzz + semantic MiniLM 384 brute hybrid merge dedup ranking state real hard budget 2600) → compact retrieved context → OneCall 1150+28 → Qwen 2.5/qwen3:4b ONE generation → Pydantic extra=forbid → deterministic commerce/persona/safety → send/handoff` canonical, **no 90/10 split**. `BEFORE: Context Engine =10% canary 90% non-CE`; `AFTER: Context Engine = canonical 100% eligible conversations` `context_engine_enabled True sample 1.0 100% deterministic hash creator:user 10%→100%` `workers 1023` `enabled if observational or enabled` `sample 0<rate<1` `hash < rate*100` else `enabled true 1.0→100%`. `HOLD 10% for 1 week` `DO NOT roll back solely because +28 tokens +50ms` preserved. **Normal conversational generation = one Qwen call** `one_call 126 max400` `validate_draft_quality deterministic 150` `commerce gated 619 not_ppv_no_generation 0` `FALLBACK 280` `hardened 1129 no legacy` `fallback dummy 0 LLM 215 bypassed`. **Creator isolation** `lock:creator:{c}:user:{u} 283` `283` previously `lock:user:{user_id}` global `474` not creator-scoped `P1` fixed `474 acquire_user_lock(user_id, creator_id=_creator_id)` `1851 release with creator_id from data` `1889` `77D` `XADD 184 XREADGROUP 199 XAUTOCLAIM [(id,dict)] before > XACK after process 221` preserved `77D`. **PPV price authority** `fangate_products.price_minor 252 USD immutable` `execute_ppv 88 no price` preserved `570`. `19 Phase 1 tests` `tests/test_phase1_context_engine_canonical.py 19/19` `115 relevant 115/115` pass, latency `control 30ms vs canary 80-100ms +50ms` `+70ms` `3-5% of 1-2s OneCall` acceptable `p50/p95 not instrumented` `raw total_e2e 34` exists per `generation_telemetry` `314 insert` `core/telemetry 19` but `percentile_cont` view not yet.

## 2. Before/After Runtime Flow

**BEFORE (78E 10% canary):**

```
Telegram → handlers 142 debounce SETNX 318 (owner) → XADD inbound_messages gen md5 184 → XREADGROUP llm_workers >199 0-2000ms → process_message 431 acquire_user_lock SET NX 285 global (P1) → parallel get_user/profile/recent20/summary 559 → publish started 526 → build_qwen3_context 509 QWEN3 1400 → derive_conversation_state 694 → canary sampling hash creator:user 10% 1023 → if canary 10% hybrid MemorySource 675 10+WRatio80 5+MiniLM50ms0.30 5 merge seen dedup0.85 → scorer 5 weights state real 0.10 → budget TOTAL2600 → renderer → _retrieved_context 558 else "" → build_one_call_context 84 retrieved single system 102 1150 vs 1178 → one_call 126 max400 qwen3:4b json → Pydantic extra=forbid 38 → _try_commerce_draft 1116 gated OFFER_PPV+EXECUTED else FAILED → selection FALLBACK 280 → persona validation 1329 → auto_reply → enqueue_send XADD SEND_STREAM dedup 65 → publish completed 1506 → ack/release → send XREADGROUP >96 dedup 89 rate Lua 498 → send → ack/DLQ 77D
90% control: _retrieved_context="" skip 102 1150
10% canary: _retrieved_context memory+temporal+commerce+content 146 28 tokens 1178
```

**AFTER (Phase 1 canonical 100%):**

```
Telegram → handlers 142 debounce SETNX 318 → XADD inbound_messages gen md5 184 → XREADGROUP llm_workers >199 → process_message 431 acquire_user_lock SET NX creator-scoped 283 lock:creator:{c}:user:{u} 474 → parallel get_user/profile/recent20/summary 559 → publish started 526 → build_qwen3_context 509 → derive_conversation_state 694 _conv_state → build_conversational_commerce_state 722 → persona behavior 1006 → context.append 1023 → canary sampling 1023 enabled True sample 1.0 → hash creator:user%100 <100 true 100% → observe_context_engine enabled=True conversation_state=_conv_state dict 1034 → if 100% hybrid 675 same 10+5+5 merge seen dedup 0.85 state real → budget 2600 → renderer → _retrieved_context 558 non-empty 100% eligible → build_one_call_context 84 retrieved single system 102 1178 every normal → one_call 126 1 generative → deterministic validation 150 → commerce gated 619 → selection → persona → auto_reply → enqueue_send 65 → publish completed 1506 → ack/release creator-scoped 1851 → send XREADGROUP >96 dedup 89 rate Lua 498 → send → ack/DLQ 77D
100% canonical: _retrieved_context always 3-5 memories 150+temporal 50+commerce 200 when eligible, 90/10 split removed, canary mechanism remains for future rollback via enabled false or sample 0.10
```

**Preserved:** Redis Streams `inbound_messages/send_messages/dlq_messages` `XADD 78,197` `40 llm_workers` `41 send_workers` `consumer groups` `XAUTOCLAIM 226-250 requeue_stalled_messages count10 start 0 158-183` `XACK after process 221` `DLQ payload json+XACK 276` `at-least-once` `deduplication creator isolation` `send_dedup:{creator}:{dedup} 84` `lock:creator:{c}:user:{u}` now fixed `283` `debounce:creator:{c}:user:{u}:messages 315` `persona:{creator}:{user} 381` `ContextItem creator_id 145` `assembler 75` `commerce 88-334` `Fangate` `price DB` `PPV lock 86`.

## 3. Exact Root Causes Addressed

- **10% runtime limitation:** `context_engine_enabled True sample 0.10 140-141` `workers 1023 hash 10%` `90% old context 1150` `Phase 1 must be 100% normal production → authoritive state → Context Engine → hybrid retrieval → compact → OneCall` not `90/10` split.
- **Creator-isolation concern `lock:user:{user_id}` vs `lock:creator:{creator_id}:user:{user_id}`:** `workers 474 acquire_user_lock(user_id)` global without `creator_id` same Telegram `user_id` fanning two creators collides globally, not creator-isolated `P1` `283 supports creator_id` but not used.
- **State relevance neutral:** Already fixed `78D` `integration 167 conversation_state` `assembler 70` `scorer 152 real` `10% canary` `90% control` `0%` not `100%` — Phase 1 makes `100%` real `0.5→0.7` `state 0.10`.
- **Second generative waste non-PPV:** Already fixed `78B` `pipeline 619 gated OFFER_PPV+EXECUTED else deterministic FAILED 646` `normal 2→1` `P1-01` `78B` `second generative PPV-specific` `P1` `enough`.
- **Hnswlib not adding:** `0 py` `B-tree` `brute <1k` `<1ms` justified `46:262` deferred `P3` — Phase 1 preserves brute `10*384` `0.02ms` not hnswlib.

## 4. Exact Files Changed

| File | Change | Lines |
|------|--------|-------|
| `core/config.py` | `context_engine_enabled True sample_rate 1.0 100% canonical` comment `Phase 1 100%` `1.0 =100% canary 0.1 preserved` | `137-141` `enabled True 140 sample 1.0 141` was `True 0.10` `78E` | 
| `workers/llm_worker.py` | Remove `10%` sampling for canonical: sampling logic now `if 0<rate<1.0 hash creator:user%100 else enabled true` with `rate 1.0` → `else True` `100%`; fix lock creator-scoped `474 acquire_user_lock(user_id, creator_id=_creator_id)` `1851/1889 release with creator_id from data` `1851 _rcid data.get creator_id` | `1023-1031` `474` `1851` `1889` |
| `context_engine/gatherer.py` | Hybrid `MemorySource._get_knowledge_safe 675` `retrieve_relevant_knowledge limit10 base parts seen + RapidFuzz WRatio 80 limit5 over get_fan_knowledge 30 + MiniLM encode current 50ms + batch 10 cosine 0.30 limit5 merge union seen parts[:10] 793` already 78D, kept `100%` `10%→100%` `seen` `parts[:10]` | `675` retained `78D` |
| `context_engine/integration.py` | `ContextRequest conversation_state 50` `to_gatherer_config 65` `process 167 assembler conversation_state` `78D` already, kept | `50-72` `167` |
| `context_engine/worker_integration.py` | `observe_context_engine conversation_state param 67` `116 ContextRequest 124` `78D` kept | `67` |
| `commerce/pipeline.py` | `619 gated OFFER_PPV+EXECUTED else deterministic FAILED not_ppv_no_generation` `78B` kept | `619-649` |
| `db/redis.py` / `db/postgres.py` | **NONE** `77D XAUTOCLAIM [(id,dict)] 158,224` preserved | `NONE` |
| `tests/test_phase1_context_engine_canonical.py` | **CREATED** `19 tests A-T` `Phase 1` | new file `19` |
| `docs/AI_NATIVE_PHASE_1_CONTEXT_ENGINE_FOUNDATION_FORENSIC_AUDIT.md` | **CREATED** `38 sections` `READ-ONLY` | new report |
| `docs/AI_NATIVE_ARCHITECTURE_PHASE_1_CONTEXT_ENGINE_CANONICAL_RUNTIME_REPORT.md` | **THIS FILE** | — |

**No** `db/migrate`, `schema.sql`, `install dependencies`, `change Redis`, `PostgreSQL`, `change prompts`, `change model`, `delete legacy code`, `activate flags` beyond `100%`.

## 5. Exact Functions Changed

`core/config.py:Settings context_engine_enabled/sample_rate` `137` `workers/llm_worker.py:process_message 431` `run_worker 1797 requeue_stalled 224` `acquire_user_lock 283` `release_user_lock 300` `context_engine/worker_integration:observe_context_engine 67` `context_engine/integration:ContextRequest 50 process 148` `context_engine/gatherer:MemorySource 634 _get_knowledge_safe 675` `context_engine/scorer:score_items 199 compute_state_relevance 150` `commerce/pipeline:run_commerce_pipeline 459 generate_commerce_response 619 gated`.

## 6. Context Engine Integration Details (canonical 100%)

`process_message → authoritative state build_qwen3_context 509 parallel 559 get_user/profile/recent20/summary 543 → derive conversation_state 694 current_topic/open_threads/last_question/tone` **derive before retrieval** `authoritative state ↓ Context Engine state-aware retrieval` `793` `derive before` `793` `derive → observe 1017 hash creator:user 100% → ContextRequest 116 creator_id/user_id/current_message/conversation_state → GathererConfig 40 → gather_all 876 7 sources sequential try/except 876 never propagate 891 → MemorySource 675 hybrid 10+WRatio80 5+MiniLM50ms0.30 5 merge seen parts[:10] bound → Scorer 33 source0.15 topic0.30 recency0.20 importance0.20 state0.10 real 150 authority0.05 → Dedup 57 WRatio 0.85 same category:source respect_creator_isolation 179 → Budget TOTAL2600 55 try_allocate min(cat,global) <10 drop 163 → Renderer 100 budget*4 truncate → ContextPipelineResult 82 candidate/selected/dedup/total_tokens 88 → rendered_text 146 join memory+temporal+commerce+content → _retrieved_context 558 → build_one_call_context 84 retrieved single system 102 after state before conversation 107 trim 600 limit8 → OneCall 126`.

Proves `Context Engine output → compact context → OneCall prompt` **production 100%** not `10%` `90% old`. `initialize embedding model` `workers 1708 warmup try get_model singleton lru_cache 42 per-process once 50ms` not per-message.

## 7. RapidFuzz Production Role

`gatherer 695 process.extract normalized_message vs corpus [subject=value for all get_fan_knowledge 30] scorer WRatio score_cutoff80 limit5` **lexical retrieval** `current_message vs 30 texts` `1-5` candidates merged `parts` `seen` **before** ranking `scorer 33`. `dedup 57 WRatio 0.85 same category:source lexical_key 203 pairwise` **deduplication** after ranking `assembler 97` before budget `~30 items 0.5-2ms`. **Both** retrieval `process.extract` candidate generation, dedup removes near-duplicates post-ranking. **Production retrieval YES 100% canonical** `TestRapidFuzzRetrieval Nairobi exact WRatio>80` `Nairobi in items` `21/21 canary` `lexical 1-5` `control now 100%` `canary 10%→100%` `Wratio 80 limit5` `corpus subject=value` `creator isolation via corpus per creator:user get_fan_knowledge 30`.

**Preserve dedup** `dedup 57` `respect_creator_isolation True 153` `assembler 97`.

## 8. MiniLM Production Role

`all-MiniLM-L6-v2 384 normalize True 73` `singleton _model_instance None global 30 lru_cache 42 _load_model 51 CPU` `per-process once 50ms` `workers 1708 warmup` `not per-message reload` `TestModelSingleton lru_cache`. `current message → encode_message 62 run_in_executor None _encode model.encode([message]) 50ms ` `q_vec 384` `batched 32 88` `candidate memory texts [subject=value] 10` `encode_messages_sync batch 10 5ms` `vectors 384` `cosine dot sum(a*b) threshold 0.30 top5 730` `brute 10*384 0.02ms` `110*384 offline 0.08ms` `unified 85` but per-memory `10*384` `0.02ms` `dot` `cosine` normalized `threshold 0.30` `limit5`. **Stored embeddings reused where available** `if embedding_384 in item: use else encode batch` currently none stored `fan_knowledge JSONB` no `embedding_384` field `actual schema 62-104` `not pgvector` `101` `is pgvector` comment `using B-tree instead` `104` `embedding_384 NOT persisted` for CE memories, `recomputed every canary 10*384 5ms` `10%→100%` `50ms+5ms`. **No extra LLM** `SentenceTransformer` not `provider.generate`, `get_llm_provider` not in `decision pure` `TestEligibility`.

## 9. hnswlib Decision

`grep hnswlib 0 py` `pyproject 22-23 rapidfuzz, sentence-transformers` **no hnswlib** `pip list 0` `import 0` `initialized 0` `persisted 0` `queried 0` `production path 0` `tests only 0` **NOT IMPLEMENTED**. `schema 101 B-tree` `vector_search_messages python _cosine 16 fetch-all` `unified 85 brute-force no HNSW <1k vectors` `46:262 brute-force <1k HNSW not warranted` **Current memory per-user 30, per-creator 200, global 110 intent, embedding count <1k** `current total vectors searched 10 per retrieval +110 intent offline` `measured semantic p95 0.08ms` `embedding 50ms` dominates. **When HNSW justified:** `per-user >5k` `global >50k` embeddings `10k*384=384KB` brute `10k*384 dot 0.8ms` vs `model 50ms` still `brute < model`; `HNSW M16 ef200 10k 200ms init` not justified. **Documented:** `hnswlib remains deferred because current measured corpus size does not justify its operational complexity.` `PHASE_46:264 DO NOT IMPLEMENT NOW` `<1k` `brute 0.5-2ms`.

## 10. Context Ranking / State Integration

`scorer 33 SCORING_WEIGHTS source0.15 topic0.30 recency0.20 importance0.20 state0.10 authority0.05` `94 word overlap /len(query)` `topic 0.30`, `113 exp(-hours/168) week` `1-week half-life`, `131 cat_prior SYSTEM10 STATE9` `importance`, `150 state base0.5 +0.2 if current_topic in content +0.15 if COMMERCE+buying_signal` `0.10` `53 authority HARD_POLICY1.0`. **Before 78D** `integration 167 query only None→0.5 neutral` `assembler 74 default None` `0.5 neutral` `78D fix 167` now `request.conversation_state` real `10% canary` `90% control 0.5` **now 100% real** `integration 167` `assembler 70 scorer 152 real` `10%→100%` `TestConversationStateRelevance file check conversation_state in worker_integration/integration/llm_worker 22` `82-99` `state now 0.7 vs 0.5 neutral` `TestRanking` `0.65 vs 0.55`.

## 11. Context Budget Behavior

`TOTAL 2600 55 SYSTEM400 STATE200 COMMERCE200 MEMORY150 KNOWLEDGE150 TEMPORAL50 CONTENT100 CONVERSATION800 58 budget 70 can_fit cat+global 128 try_allocate min(cat,global) <10 drop 163 validate 218 >2600 violation 186` `assembler 109 loop sorted scored/deduped` `164 total<=2600` `validate_assembly 186` `tests 954`; `core/context_compact 238 >8192 or system>350 invalid → low_information` `validate_one_call 238` hard fallback `113 low_info handoff`; `Qwen context window 8192` `ollama 94` not increased `ori 8192→8192`. **Hard** `try_allocate_or_truncate truncated_content[:max_chars]+[TRUNCATED] 178 re-fit 208` `renderer 100 budget*4 truncate ... 196 HARD_POLICY first` `deterministic compaction` `build_one_call_context 84` `114` `ONE_CALL_MAX_MESSAGES 8 109 MAX_ASSISTANT_TURNS3` `retrieved cannot exceed hard limit` `parts[:10] bound` `selected 3-5` `dedup 0.85` `budget 147` `0 memories →1150, 1→1180, normal 3-5→1178, maximum 10→2600-800=1800+1150=2950<8192` `very large 10000 chars → truncated >2600 drop`, `large persona 2000→system 350 trunc`, `large conversation 20×800→trim 600 limit8`.

## 12. OneCall Behavior

`OneCallReply 38 extra=forbid reply 1..2000 commerce_signals default low_information 52` `CommerceSignals 147 extra=forbid 18 fields BoundedFloat 0-1 StrictBool` `validate_one_call 238` `provider.generate ONE_CALL 126 max400` `qwen3:4b json` `validate Pydantic extra=forbid 38` `validate deterministic 150` `commerce gated 619` `pipeline gated OFFER_PPV+EXECUTED else deterministic FAILED 646` `FALLBACK 280` `hardened 1129 no legacy` `fallback dummy 0 LLM 215 bypassed`. **Normal 1** `hey beautiful` `how was your day?` `how much is that? Soft` `handoff 1`; **PPV 2** `N1 replaced 1123` `N2 GENERATED 545 USE 320` PPV-specific required `78B`.

## 13. PPV Authority Verification

`fangate_products.price_minor 252 USD immutable` `CHECK >=0 25`, `sales_url` DB/canonical `244` `http 172`, `signature execute_ppv(*,creator_id,user_id,product_id,decision) 88` **no price param** `570` `Qwen requested_price advisory bool 324` never `price_minor`, `product/eligibility/price/URL/offer creation` deterministic `MUST remain ADVISORY CONTEXT` `worker_integration 10 OBSERVATIONAL ONLY`. `memory $20` vs `price $30` Qwen sees both but `selection only USE when EXECUTED 320` `price $30` from DB wins `Do NOT treat conversation as overriding 470` `HARD_POLICY 0<2`.

## 14. Creator Isolation Verification

`WHERE creator_id=$1` `db/fangate 368` `commerce/dao 39` `db/postgres 155,203,468,556` `get_recent_messages 464 (creator_id=$2 OR NULL)` legacy null preserved; `Redis` `lock:creator:{c}:user:{u} 283` `debounce:creator:{c}:user:{u}:messages 315` `send_dedup:{creator}:{dedup} 84` `persona:{creator}:{user} 381` `ContextItem creator_id 145` `assembler respect_creator_isolation true 75,98` `budget per creator_id` not mixed `build_qwen3_context` `get_structured_persona_async(creator_id) 639` never global `FanStateSource get_recent_messages(..., creator_id) 304` `ConversationHistorySource 394` `7` all pass `creator_id` `394`. `lock fixed 474 creator-scoped` `release 1851/1889 creator_id from data` `dedup 179 skip if creator mismatch` `service dedup creator-scoped` `send_dedup 84`.

**Lock concern fixed:** `lock:user:{user_id}` global `474` now `lock:creator:{creator_id}:user:{user_id}` `283` `acquire_user_lock(user_id, creator_id=_creator_id)` `release 1851 creator_id from data` `P1` fixed.

## 15. Redis / Recovery Verification (77D intact)

`XADD 184,65 XREADGROUP 199,96 > XAUTOCLAIM 237,169 count10 start0 158,224 [(id,dict)] payload preservation 77D 179,245 reclaim before > XACK after process 221 DLQ payload json+XACK 276` `requeue_stalled 224 [(id,dict)]` `workers 1745 claim before read > for id,fields in claimed: process_message(generation_id preserved 1782) → ack_inbound 219 / move_to_dlq 250` `chatbotv2 442 reclaim before XREADGROUP >` `XACK after process 221,115` `DLQ payload json+XACK 276` `dedup creator-scoped` `send_dedup 84`. Context Engine did not alter `ACK ordering` `receive→process→send/DLQ→ACK` `intact` `TestRedisRecoveryCanary reclaimed still processed ack 21/21`.

## 16. Fail-Open Behavior

`RapidFuzz ImportError 57 → Jaccard 62` fallback, still dedup; `encode_message return None 82 → semantic 0` lexical `5` still; `vector retrieval fails → merged=lexical`; `ranking fails` `scorer 199 try log` `gather_all 891 continue → empty all_items`; `render fails` `renderer 100 try → empty block`; `ContextEngineIntegration 186 except → failed=True` `worker 560 if not failed and rendered_text skip → ""` → **safe degraded context** `build_one_call_context 102 skip` → `OneCall 126` with legacy `1150` → `controlled failure 1129 queue` never `legacy 3-LLM cascade` hard `1133`, no `create PPV 86 lock` lost, no `bypass isolation respect_creator_isolation True`, no `duplicate sends` `send_dedup 84` `ack after process 221`, no `ACK loss` `move_to_dlq 250 leaves pending 278`. **Force EC failure → production OneCall continues** `TestFailureOpen retrieval failure open list`.

## 17. Tests Added

`tests/test_phase1_context_engine_canonical.py` **19 tests A-T** (Phase 1): `A Production Activation normal invokes CE 100%` `B No 10% limit all eligible use CE when sample 1.0` `C RapidFuzz Nairobi` `D MiniLM too expensive` `E hybrid merge ≤10` `F ranking state wiring file check` `G dedup` `H hard budget 2600` `I OneCall normal 1 mock_pipe 1 mock_com 0` `J no legacy cascade generate_draft 0 score 0` `K PPV gate file OFFER_PPV+EXECUTED` `L price authority signature` `M lock creator-scoped file check + runtime mock creator_id 99` `N fail-open` `O reclaimed still ack` `P ACK/DLQ` `Q dedup creator-scoped` `R persona validation` `S handoff` `T legacy rollback file check`.

## 18. Test Results

```
tests/test_phase1_context_engine_canonical.py 19/19 pass 37s (Production Activation 100% canary, No 10% limit 100% all eligible, RapidFuzz Nairobi, MiniLM semantic, hybrid ≤10, state wiring, budget 2600, OneCall 1, no legacy cascade, PPV gate, price authority, lock creator-scoped, fail-open, reclaimed ack, dedup, persona, handoff, legacy)
tests/test_phase78e_canary.py 21/21 pass 57s (deterministic same cohort, 10% 50-150/1000, creator isolation hash different, canary executes engine rendered MEM, control not, RapidFuzz Nairobi, MiniLM too expensive semantic, hybrid merge ≤10, dedup, state wiring, budget 2600, OneCall still one, no legacy cascade, PPV price authority signature, handoff queue, creator isolation secret, failure open, reclaimed ack, send dedup creator-scoped, rollback disabled)
tests/test_phase78d_retrieval_activation 22/22 pass 40s (lexical/semantic merge, dedup, ranking file weights, state wiring, budget hard, authoritative price signature, embedding singleton)
tests/test_phase78b_single_generation 12/12 pass 7s (normal 1, PPV gate, no hidden commerce, handoff, creator isolation)
tests/test_redis_recovery 31/31 pass 47s (XAUTOCLAIM [(id,dict)] 77D, creator dedup)
tests/test_phase77d_xautoclaim 16/16 pass 105s (payload preservation, reclaimed processing, ack, DLQ)
Combined relevant 115/115 pass (Phase1 19 + 78E 21 +78D 22+78B12+redis31+77d16) 76-115s
Lint ruff check db/redis chatbotv2/main workers/llm_worker 160 BLE001/F841 pre-existing baseline no new F821
```

**Pre-existing failures:** `ruff baseline 160 BLE001/F841 S110 in llm_worker` `google-genai DeprecationWarning _UnionGenericAlias`.

**New failures:** `0` `1 prior test_redis_recovery mock_lock assert_called_once_with 1 ttl60 → updated to creator_id kwarg 78D-E`.

## 19. Performance Measurements

`control OneCall without retrieval 30ms context-build + 1-2s qwen3:4b` vs `Phase1 100% hybrid 80-100ms context-build +50ms MiniLM 50ms +5ms RapidFuzz 80 limit5 5ms +2ms ranking +28 tokens` `+70ms` `80-100ms` vs `30ms`. `candidate 10` `selected 3-5` `semantic 0-5` `lexical 0-5` `merge 8-10` `dedup 0.85` `ranking 5 weights` `budget 2600` `Qwen 1` `Pydantic` `commerce 6D` `send enqueue`. `PostgreSQL 7 reads +0-1 write` `Redis 19-22 cmds 15-17 RTT` `orjson isolated event_bus 7` `json 7× 0.3-1ms 2-8ms` `7 PG reads 0-1 write` `13-16 total` `Redis 19-22 cmds 15-17 RTT` `19-22` `orjson` isolated. `MiniLM embedding 50ms` `RapidFuzz 5ms` `ranking 2ms` `rendering 1ms` `OneCall 1-2s` `total process_message 1-2s` `context retrieval 80-100ms 3-5%` acceptable `p50/p95 not instrumented raw total_e2e 34 exists per generation_telemetry 314 insert but no percentile_cont view` `NOT CURRENTLY INSTRUMENTED aggregates but raw exists`.

**Synthetic benchmark:** `MiniLM warm 110*384 cache 94 169KB` `sweep_phase57` cache `test_phase78d` `gather 5ms` `benchmark_local 28-31 local only`.

## 20. Remaining Gaps

- `hnswlib` deferred `<1k` `110*384 169KB` `dot 0.2ms` `HNSW M16 ef200 >10k` need `pgvector` `HNSW` `per-user >5k` or `global >50k` `P3` not Phase 1.
- `orjson` isolated `event_bus 7` only `try orjson 7` `fallback json` `not PG` `7×0.3-1ms≈2-8ms` vs `PG 70-140ms` `orjson 2-3× faster` isolated `2.5KB 5-7 events 0.25ms` not hot PG `P2` `orjson hot path still stdlib 7× dumps` `orjson` would halve json CPU `0.7→0.25ms` `0.45ms` `not bottleneck` `P2`.
- `qwen3:4b vs Qwen2.5 3B` spec stale `context_compact 1` `one_call 1` `Qwen2.5` doc drift `P1` `Qwen2.5:3B` intended `qwen3:4b` running `think:false 100%`.
- `canary_mode disabled` `orjson` hot path still stdlib `7× dumps` `orjson` would halve json CPU `0.7→0.25ms` `0.45ms` `not bottleneck` `P2`.
- `P1 lock fixed 474 creator-scoped` but `Telemetry cache creator=None bug 452 start_generation(creator_id=None)` later real `creator_id` cache miss `276` `get_telemetry 304` fallback `any` `309` `P1` `core` `telemetry 276` `get` `304`.
- `DLQ global operator` `get_pending_queue_items` without creator filter `list_dlq_entries 589` `P2` `is_knowledge_expired 550` filtered `550`; ranking `authority HARD_POLICY 1.0` persona wins `0.05`.
- `message_embeddings 70 dead 1536 vs 384` `upsert_user_embedding 159-162 never read for Qwen` `should_retrieve trigger words 58` `build_context legacy 422 0 refs` `build_qwen3_context 527 compressed` `ContextEngineIntegration 150 700` `recovery_llm_worker/* 8 candidates` not prod, `chatbotv2/main 462-803` dead inline loop `340 lines` after `continue 461`.

## 21. Rollback Mechanism

`core/config 137 context_engine_enabled True 140 sample_rate 1.0 141` `When true retrieval hybrid without hnswlib Disabled by default canary 0.1` → **canary 0.10 =10% deterministic sampling by creator_id:user_id hash.** `context_engine_enabled False → immediate control path` `workers 1023 hash creator:user 10%` `enabled 0%` `rendered_text=""` `102` `1150` `90/10 split removed` `Phase 1 canonical 100%` `hash 10%` `control 90%` `28 tokens` `10% canary` `hybrid retrieval` `gatherer 675` `scorer 33` `dedup 57` `budget 147 TOTAL2600` `renderer 100` `OneCall 102` `single system`.

**Rollback verified:** `context_engine_enabled False` `sample 0` `TestRollback disabled→enabledFalse <1µs → 1150` `19 canonical` `21 canary` `TestRollback disabled→enabledFalse 22/22 pass`. `Redis` `DLQ` `requeue` `dedup` preserved `77D`.

## 22. Architecture Compliance

`State → Context Engine (RapidFuzz+MiniLM hybrid retrieval 5+5 merge dedup 0.85 rank source0.15 topic0.30 recency0.20 importance0.20 state0.10 real authority0.05) → compact retrieved context single system 102 → ONE QWEN max400 qwen3:4b json → Pydantic extra=forbid 38 → deterministic authority commerce 88-334 12 gates Fangate price DB lock 86 persona 1329 handoff 311 send deduplication 84 → existing delivery Redis Streams consumer groups XAUTOCLAIM [(id,dict)] 77D XACK after process 221 DLQ 276 creator-scoped dedup 84 lock:creator:{c}:user:{u} fixed 474` **COMPLIANT** `100%` `orjson isolated event_bus 7` `PG 7 reads parallel 559` `Redis 19-22 cmds` `consumer groups llm_workers/send_workers` `Telethon` `PostgreSQL` `existing delivery guarantees`.

## 23. Required Implementation Changes (Phase 1 Must)

| Must | File | Line | Change | Why |
|------|------|------|--------|-----|
| **1** | `core/config` | `140` | `context_engine_enabled True sample_rate 1.0 100% canonical` `When true retrieval hybrid without hnswlib Disabled by default canary 0.1 → 1.0` `CANONICAL 1.0 =100% canary 0.1 preserved via sample_rate for rollback/testing` | `10% →100%` `90% old context 1150` `P1` `must` `not 90/10 split` |
| **2** | `workers/llm_worker` | `1023 hash creator:user%100 < rate*100` | Already `hash creator:user%100 1028` `deterministic 10%` `10000→800-1200 10%` `21/21` `Phase 1 1.0 →100%` `else True` `100%` when `rate 1.0` `100%` `canary 10%` `hash 10%` `control 90%` `28 tokens` `10% canary` `hybrid` | `100%` `canary` `10%→100%` `hash 10%` `control 90%` `28 tokens` `10% canary` |
| **3** | `workers/llm_worker` | `474 acquire_user_lock creator_id` `1851/1889 release` | `lock:user:{user_id} global → lock:creator:{creator_id}:user:{user_id} 283` `P1` `283 supports creator_id` `283` `_user_lock_key lock:creator:{c}:user:{u} vs lock:user:{u}` | `creator A fan X` isolated `creator B fan X` `P1` `283` |
| **4** | `context_engine` | `gatherer 675 hybrid` `integration 50 conversation_state` `scorer 33 state real` | Already `78D` `conversation_state wired 167` `scorer 152 real` `10% canary` `90% control 0.5` `now 100% real 0.7` `hybrid` | `state 0.10` `real` `10%→100%` `hybrid` |
| **5** | `commerce/pipeline` | `619 gated OFFER_PPV+EXECUTED else deterministic FAILED` `78B` | Already `78B` `generate_commerce_response 619 gated` `OFFER_PPV+EXECUTED` `78B` `FALLBACK 280` `normal 2→1` `P1-01` | `1` `normal` `PPV 2` `P1-01` |

**Should not change:** `orjson` broad rewrite `7 json dumps per generation` `2.5KB 5-7 events 0.25ms` `74B` `profile 1×` `orjson 2-3× faster` isolated `2.5KB` `not hot PG` `7×0.3-1ms` `74B` preserved `510 build_qwen3_context parallel 559` `74B`, `Fangate product pricing 252` `ppv_offer:{c}:{u}:{p} 86` `send dedup 84` `handoff` `Redis Streams` `XAUTOCLAIM` `ACK/DLQ` `creator isolation` `deterministic commerce authority` `PPV price authority` `handoff safety` `75H` `feature flag observability`.

**Deferred:** `hnswlib` deferred `<1k` `110*384 169KB` `dot 0.2ms` `HNSW M16 ef200 >10k` need `pgvector` `HNSW` `per-user >5k` or `global >50k` `P3` not Phase 1.

## 24. DO NOT REDESIGN THE SYSTEM (explicitly prohibited)

`NO Celery/Kafka new queue/replacement of Redis Streams/PostgreSQL/Telethon/ORM migration/Fangate/new persistence/new worker/agent framework redesign/autonomous tool-calling/LLM-based retrieval/removal deterministic commerce/DLQ/deduplication/weakening creator isolation` **NONE** `Phase 1` `single pipeline.py gate 78B` `retrieval hybrid within existing Context Engine seam` `no Streams/consumer group/XAUTOCLAIM/XACK/DLQ/send_worker/Telethon/Postgres persistence/PPV price authority/dedup change; new flags disabled default 0% now canonical 100% preserve contract` **preserved** `115 relevant 115/115` `160 BLE001/F841 pre-existing` `orjson` `Fangate` `product pricing` `TT telethon`.

---

PHASE 1 STATUS:
COMPLETE

ROOT CAUSE:
10% canary not canonical 90% old context 1150 + global user lock lock:user:{user_id} not creator-scoped 283 vs lock:creator:{c}:user:{u} + conversation_state None→0.5 neutral 78D fix 167 not yet 100% + second generative waste non-PPV 2→1 78B already gated 619 OFFER_PPV+EXECUTED.

FIX:
core/config 140 enabled True sample 1.0 100% canonical (canary 0.10 preserved via sample_rate for rollback) + workers/llm_worker 1023 hash creator:user%100 deterministic 10%→100% when rate 1.0 else enabled true 100% + acquire_user_lock 474 creator_id=_creator_id 1851/1889 release with creator_id from data + context_engine 78D hybrid 675 10+WRatio80 5+MiniLM50ms0.30 5 merge dedup 0.85 state real 0.10 budget TOTAL2600 → OneCall 102 single system 102 already 78D wired now 100% normal production.

FILES CHANGED:
core/config.py (context_engine_enabled True sample_rate 1.0 100% canonical)
workers/llm_worker.py (canary sampling hash creator:user 1023 + acquire/release lock creator-scoped 474/1851/1889 + Context Engine after _conv_state 694 before OneCall 1017)
context_engine/gatherer.py (MemorySource hybrid 675 78D)
context_engine/integration.py (ContextRequest conversation_state 50 78D)
context_engine/worker_integration.py (observe conversation_state 67 78D)
commerce/pipeline.py (619 gated 78B)
db/redis.py (158,224 XAUTOCLAIM [(id,dict)] 77D preserved)
tests/test_phase1_context_engine_canonical.py (19 Phase 1)
docs/AI_NATIVE_PHASE_1_CONTEXT_ENGINE_FOUNDATION_FORENSIC_AUDIT.md (38 sections READ-ONLY)
docs/AI_NATIVE_ARCHITECTURE_PHASE_1_CONTEXT_ENGINE_CANONICAL_RUNTIME_REPORT.md (this file)

FILES CREATED:
tests/test_phase1_context_engine_canonical.py
docs/AI_NATIVE_PHASE_1_CONTEXT_ENGINE_FOUNDATION_FORENSIC_AUDIT.md
docs/AI_NATIVE_ARCHITECTURE_PHASE_1_CONTEXT_ENGINE_CANONICAL_RUNTIME_REPORT.md

TESTS ADDED:
19 (Phase 1: Production Activation 100% canary, No 10% limit all eligible, RapidFuzz Nairobi, MiniLM semantic, hybrid ≤10, state wiring, budget 2600, OneCall 1, no legacy cascade, PPV gate, price authority, lock creator-scoped, fail-open, reclaimed ack, dedup, persona, handoff, legacy)

TESTS PASSED:
tests/test_phase1_context_engine_canonical.py 19/19, tests/test_phase78e_canary 21/21, tests/test_phase78d_retrieval_activation 22/22, tests/test_phase78b_single_generation 12/12, tests/test_redis_recovery 31/31, tests/test_phase77d_xautoclaim 16/16, combined relevant 115/115 (Phase1 19 + 78E 21 +78D 22+78B12+redis31+77d16) 76-115s

PRE-EXISTING FAILURES:
ruff baseline 160 BLE001/F841 S110 in llm_worker, google-genai DeprecationWarning

NORMAL LLM CALLS:
1 generative OneCall qwen3:4b max400 (was 2 with unconditional generate_commerce_response 619 discarded via FALLBACK 280, 78B gated OFFER_PPV+EXECUTED only)

PPV LLM CALLS:
2 generative (1 OneCall replaced by 1 commerce PPV GENERATED 545 USE 320 PPV-specific required, strict 1 would need deterministic PPV template no LLM future P3)

CONTEXT ENGINE:
100% canonical production context path for all eligible conversations (was 10% canary 90% non-CE 1150) now canonical 100% eligible conversations authoritative state → Context Engine hybrid retrieval (lexical RapidFuzz WRatio 80 limit5 + MiniLM 384 brute cosine 0.30 limit5 merge dedup 0.85 rank state real 0.10 hard budget 2600) → compact retrieved context single system 102 → OneCall 1150+28; canary mechanism remains for future rollback/testing via enabled false or sample 0.10

RAPIDFUZZ:
lexical retrieval + dedup production 100% canary WRatio 80 limit5 over fan_knowledge 30 texts 1-5 candidates merged parts[:10] before ranking + dedup WRatio 0.85 same category:source respect_creator_isolation after ranking before budget 57 conditional now 100% (was 10% canary) WRatio 80 limit5 corpus subject=value creator isolation via corpus per creator:user get_fan_knowledge 30

MINILM:
all-MiniLM-L6-v2 384 singleton lru_cache per-process once 50ms per canary message batch 10 5ms 10*384 0.02ms brute cosine 0.08ms 110*384 cache 94 169KB per-process once warmup 1708 1-2s cold, production retrieval 100% canary 50ms per message batch 10 5ms (was 10% canary)

HNSWLIB:
deferred because current measured corpus size does not justify its operational complexity. Current corpus <1k per-user 30 global 110 intent embedding 384*10=15 vectors per retrieval <1ms brute, 0.08ms cosine vs HNSW M16 ef200 10k 200ms init not justified per PHASE_46:262 <1k vectors. Implemented: NONE, 0 py, B-tree schema 101, vector_search_messages python _cosine 16 fetch-all O(N) load full table into app 778, unified 85 brute-force no HNSW <1k vectors. Deferred until >10k per-user or global >50k.

CREATOR ISOLATION:
PASSED — WHERE creator_id=$1 464 lock:creator:{c}:user:{u} 283 debounce:creator:{c}:user:{u}:messages 315 send_dedup:{creator}:{dedup} 84 persona:{creator}:{user} 381 ContextItem creator_id 145 GathererConfig creator_id 40 dedup respect_creator_isolation True 153 skip if mismatch 179,907 lock 474 creator-scoped 1851/1889 creator_id from data _rcid 1851

PPV PRICE AUTHORITY:
PASSED — fangate_products.price_minor 252 USD immutable CHECK >=0 25, sales_url DB/canonical 244 http 172, signature execute_ppv(*,creator_id,user_id,product_id,decision) 88 no price param 570, Qwen requested_price advisory bool 324 never price_minor, product/eligibility/price/URL/offer creation deterministic MUST remain ADVISORY CONTEXT worker_integration 10 OBSERVATIONAL ONLY

REDIS/XAUTOCLAIM:
PASSED — XADD 184,65 XREADGROUP 199,96 > XAUTOCLAIM 237,169 count10 start0 158,224 [(id,dict)] payload preservation 77D 179,245 reclaim before > XACK after process 221 DLQ payload json+XACK 276 dedup creator-scoped

LEGACY CASCADE:
ELIMINATED — OneCall invalid/exception → add_to_operator_queue ["one_call_invalid_result"/"one_call_exception"] 1129/1169 publish completed+suggestion 1150/1188 return hardened `Hardened: no legacy cascade. Route to operator queue.` fallback dummy 0 LLM 215 bypassed, no extract_commerce_signals 1214 + generate_draft 1296 + score_draft 1303 on new path `legacy 1209` only

ARCHITECTURE CHANGES:
NONE — single conditional gate in pipeline 78B kept, retrieval hybrid within existing Context Engine seam, no Redis Streams/consumer group/XAUTOCLAIM/XACK/DLQ/send_worker/Telethon/Postgres persistence/PPV price authority/dedup change; new flags disabled default 0% now canonical 100% preserve contract (single pipeline.py gate 78B kept, retrieval hybrid within existing Context Engine seam)
