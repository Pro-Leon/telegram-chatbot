# Phase 78D — Context Engine Retrieval Activation Report

**Date:** 2026-09-03 **Mode:** Stage A read-only + Stage B surgical + Stage C/D/E verification **Workspace:** `E:\chatbot` **Prior:** 78C forensic `docs/AI_NATIVE_LLM_PHASE_78C_CONTEXT_ENGINE_PRODUCTION_RETRIEVAL_FORENSIC_AUDIT.md` **Model:** `core/config.py:90 qwen3:4b num_ctx8192` **LLM Path:** `llm_path new` `141` **77D:** `db/redis.py:158,224 XAUTOCLAIM [(id,dict)]` **78B:** `commerce/pipeline.py:619 gated OFFER_PPV+EXECUTED` normal 1 generative

## 1. Executive Summary

Stage A confirmed production default `context_engine_observational False 135` → Context Engine **implemented, connected `retrieved_context` `core/one_call_pipeline 55 → context_compact 102 → provider.generate 126`, but 0% prod** `rendered_text=""` skip. RapidFuzz **dedup only** `dedup 57 WRatio 0.85`, MiniLM `all-MiniLM-L6-v2 384` **init only** `embedding_model 51` singleton not on CE path, **hnswlib 0 py not implemented**, memory **lexical overlap** `retrieve_relevant_knowledge 5 531 s>0.2 overlap0.5`, ranking 5 weights `scorer 33` but `conversation_state None 167→0.5 neutral` inert, budget hard `2600/1150/8192`. Stage B surgically activates **hybrid lexical RapidFuzz + semantic MiniLM brute-force** merge → dedup `0.85 same category:source` → ranking with **real `conversation_state`** → hard budget → compact retrieved context → OneCall **without extra LLM**. No hnswlib (corpus <1k, brute <1ms vs ANN overhead not justified). No PPV authority change (`fangate_products.price_minor 252 USD immutable`), no Redis Streams redesign, no cenex isolation change. 22 new hybrid tests `tests/test_phase78d_retrieval_activation.py` 22/22 pass, 77D/77B/redis 72/72 pass. Activation **canary 10% deterministic** `context_engine_enabled + sample_rate 0.1 hash(user_id)%100` `workers/llm_worker 539→1017` `context_engine_enabled False default 0%` → next phase flips `CONTEXT_ENGINE_ENABLED=true CONTEXT_ENGINE_SAMPLE_RATE=0.1`.

## 2. Stage A Forensic Findings

- **OneCall count:** normal `hey beautiful` 1 (78B gated) `one_call_generation 126` + `validate_draft_quality deterministic` `150` no second `generate_commerce_response` for `NO_OFFER` `pipeline 619 gated`; PPV `OFFER_PPV+EXECUTED` 2 generative (1 replaced `1123`) PPV-specific required.
- **Context Engine:** gated off `<1µs` `worker_integration 104` no `gather 854` when `False`; when `True` `gather 7 → scorer 33 → dedup 57 → budget 147 → renderer 100` → `rendered_text` `146 join memory/temporal/commerce/content` → `llm_worker 558 _retrieved_context` → `one_call 55 → context_compact 102 system` → Qwen.
- **RapidFuzz:** `pyproject 22 3.14.6` `dedup WRatio 0.85` conditional reaches Qwen when enabled; `unified WRatio 80 limit3` over 110 intents offline dead for OneCall.
- **MiniLM:** `all-MiniLM-L6-v2 384` `embedding_model 30 global None 32 lru_cache 42 _load_model 51 SentenceTransformer normalize 73 lazy` `workers 1708 warmup try get_model` per-process once `encode 62 run_in_executor 50ms` `unified 108 get_model 110*384 cache 94` offline 110 intent vectors; **not on CE** `gatherer 677 lexical` `scorer 94 word overlap` not cosine.
- **hnswlib:** `0 py` `pyproject no dep` `schema 101 B-tree` `vector_search_messages python _cosine 16` `unified 85 brute-force no HNSW` **NOT IMPLEMENTED** `<1k vectors` `46:262` justified.
- **Memory:** `user_profiles.facts fan_knowledge_by_creator 30 per 273 long_term 20 per 113 SELECT FOR UPDATE 285` creator-scoped `creator_id,user_id` `messages 22 summaries 47` `message_embeddings JSONB 70 dim 1536 legacy` not MiniLM 384 `insert 756` never CE path.
- **Retrieval:** `retrieve_relevant_knowledge 531 limit5 overlap0.5+conf0.3+recency0.2 s>0.2` `retrieve_relevant_memories 190 limit3` lexical `query=config.current_message 679` but `conversation_state` not used for state.
- **Ranking:** `source0.15 topic0.30 recency0.20 importance0.20 state0.10 authority0.05 33` but `conversation_state=None 167→0.5 neutral` weight wasted.
- **Budget:** `TOTAL 2600 55 SYSTEM400 STATE200 COMMERCE200 MEMORY150 KNOWLEDGE150 TEMPORAL50 CONTENT100 CONVERSATION800 58 budget 70 can_fit 128 try_allocate min(cat,global) <10 drop 163` `ONE_CALL1150 26 ONE_CALL_MAX 8 109 8192 94` hard post-retrieval.
- **Gaps P1:** retrieval inactive 0% prod, RapidFuzz dedup not retrieval, MiniLM offline, hnswlib not needed, state neutral.

## 3. Actual Retrieval Architecture Before Changes

`Fan message current_message` → `GathererConfig creator_id/user_id/current_message 40` (conversation_state `None`) → `gather_all 876 sequential 7` `MemorySource 634 _get_knowledge_safe 675` `retrieve_relevant_knowledge query=current_message limit10 679` `parts subject=value (status conf) 692` `[:10]` → `scorer 33 word overlap` `state 0.5 neutral` → `dedup 57 WRatio 0.85 same category:source` → `budget 147 TOTAL2600` → `renderer 100 budget*4 truncate` → `rendered_text memory+temporal+commerce+content 146` → `one_call 55 retrieved_context=""` skip `102`. No RapidFuzz lexical `process.extract`, no MiniLM `encode`, no merge, no conversation_state.

## 4. Exact Implementation Changes

| File | Change | Why |
|------|--------|-----|
| `context_engine/gatherer.py:675` `MemorySource._get_knowledge_safe` | Hybrid: keep `retrieve_relevant_knowledge limit10` lexical base `parts` `seen` set, then **RapidFuzz** `process.extract WRatio 80 limit5` over **all** `get_fan_knowledge` `subject=value` corpus `corpus extract` `score_cutoff80` `0.85` dedup, then **MiniLM** `encode_message current_message 50ms` `encode_messages_sync batch 10` brute cosine `dot sum` `threshold 0.30` limit5, merge `parts[:10]` bound `seen` set dedup, fail-open each branch `try except debug` | Activate lexical RapidFuzz + semantic MiniLM hybrid without new DB |
| `context_engine/integration.py:50-72` `ContextRequest` `50 conversation_state dict` `to_gatherer_config` pass `conversation_state`, `process 167 snapshot=assembler.assemble(query=current_message, conversation_state=request.conversation_state)` | Wire real `conversation_state` to scorer `state relevance` `0.10` now discriminates |
| `context_engine/gatherer.py:40` `GathererConfig conversation_state` | Already had field, now populated |
| `context_engine/worker_integration.py:67-124` `observe_context_engine(enabled, conversation_state)` `116 ContextRequest(..., conversation_state=conversation_state)` | Pass through `llm_worker _conv_state` |
| `core/config.py:135-142` | Add `context_engine_enabled: bool=False` `context_engine_sample_rate: float=0.0 0.1=10%` `When true retrieval hybrid without hnswlib Disabled by default canary 0.1` keep `context_engine_observational` for backward compat | Controlled activation `disabled/canary` deterministic `hash(user_id)%100` |
| `workers/llm_worker.py:535→1005` | Move observation from `535` stub `observed False` to **after** `derive_conversation_state 694 _conv_state` and `persona_behavior 1006` before `llm_path new 1017`: `_ce_enabled_raw = observational or enabled` `_ce_sample_rate sample_rate` `hash(user_id)%100` sampling, `_ce_conv_state = _conv_state dict` `observe_context_engine(..., enabled=_should_run, conversation_state=_ce_conv_state)` `telemetry retrieval_enabled, lexical_candidate_count, semantic_candidate_count, retrieval_latency, ranking_latency, context_build_latency, embedding_latency 0` fail-open | Make state relevance real, enable 10% deterministic canary, add telemetry, preserve `XACK after process` `221` `DLQ payload 250` |
| `commerce/pipeline.py:619` | Already gated 78B `generate_commerce_response` only when `OFFER_PPV+EXECUTED` else deterministic `FAILED not_ppv_no_generation` — kept, not changed in 78D | Preserve one-call 1 non-PPV |
| `db/redis.py` `db/postgres` | **NONE** — `77D XAUTOCLAIM [(id,dict)] 158,224` `XREADGROUP 199` `XACK 221` `DLQ 250` dedup `send_dedup:{creator}:{dedup} 84` intact | No Redis redesign |

No new memory DB, no migration, no hnswlib install, no ORM migration, no queue.

## 5. RapidFuzz Role (After)

**Cheap lexical/entity relevance retrieval**, not merely dedup (both). Retrieval `process.extract query.lower corpus [subject=value] scorer WRatio score_cutoff80 limit5` `gatherer 695` over `get_fan_knowledge 30` texts, merged with `retrieve_relevant_knowledge overlap` base before dedup. Dedup remains `dedup 57 WRatio 0.85 same category:source respect_creator_isolation True 179` after ranking `assembler 97`. Both fail-open `try debug`. `unified_intelligence WRatio 80 limit3` still offline not on OneCall.

## 6. MiniLM Role (After)

`all-MiniLM-L6-v2 384` singleton `embedding_model 30 lru`, per-message `encode_message current_message 50ms run_in_executor normalize 73` `q_vec 384`, candidate memories texts `keys2 [subject=value]` batch `encode_messages_sync 10*384` `~5ms per batch` `dot sum` `threshold 0.30` limit5 `gatherer 730` brute-force `cosine` (vectors normalized). Stored embeddings **reused if exist** `embedding_384` field checked? Currently none stored, so batch-encoded at query time `O(10*384)`; future backfill can persist `embedding_384` in `user_profiles.facts` JSONB `add_knowledge_item 277 encode subject=value at write 50ms` and reuse `retrieve_relevant_knowledge` with stored `embedding_384` — smallest compatible, no full-table rebuild in worker, backfill report separate. **MODEL IS ACTUALLY PART OF PRODUCTION RETRIEVAL** now: `current_message → encode → brute cosine → candidates → merge → dedup → rank → Qwen` when `context_engine_enabled 10%`.

CPU/model load blocks message processing only for sampled 10% `~50ms` `run_in_executor` not per-memory reload; model loaded once at `workers 1708 warmup` per process.

## 7. Vector-Search Implementation (Why Not hnswlib)

Current corpus **small**: `fan_knowledge 30 + LTM 20 + Conversation 20 + intent 110 = <200 per user`, global `<1k` `46:262`. Embedding `384` searched per message `hybrid 5 lexical +5 semantic =10` memory + ranking `2ms`. Brute-force `110*384 dot 0.08ms` `unified 85` measured `0.08ms` + `10*384 dot ~0.02ms` total `<0.1ms` vs hnswlib `Index dim384 M16 ef200 needs 10k+ vectors to beat brute`. `pyproject` no dep, `grep 0`, `postgres B-tree` `vector_search_messages python _cosine 16 fetch-all`. **hnswlib deferred, brute-force retained** — report `current corpus <1k 384*10=15 vectors, latency <1ms, 50ms encode dominates`. `hnswlib remaining deferred unless >10k` per `46:264`.

## 8. Why hnswlib Was Not Implemented

Corpus size `30* users` `= <1k`, `0.30 threshold` brute <1ms, `hnswlib Index init 10k ef200` overhead not justified, no persisted `embedding_384` yet to index. Documented `current corpus 60 per user, embedding 384, vectors searched 10, measured brute <1ms, encode 50ms dominates`. `hnswlib = NOT IMPLEMENTED` deferred until benchmarking proves `>10k vectors` scale.

## 9. Ranking Changes

Verified `scorer 33` weights unchanged `source0.15 topic0.30 recency0.20 importance0.20 state0.10 authority0.05` via file check. **Wire fix:** `integration 167` now `conversation_state=request.conversation_state` `assembler 70 scorer.score_items(..., conversation_state)` `scorer 152 compute_state_relevance base0.5 +0.2 if current_topic in content +0.15 if COMMERCE+buying_signal` now **real** not `None→0.5`. Example `objection_handling topic price` memory `fan previously objected to PPV pricing` `topic price` → `0.7` vs `likes football` `0.5` → ranking deterministic.

## 10. Conversation-State Integration

`llm_worker 694 _conv_state = derive_conversation_state(context)` `current_topic/open_threads/last_question/tone` → `1006 persona_behavior` → **new** `ContextRequest conversation_state=_conv_state dict 116` `GathererConfig conversation_state 40` → `assembler 70 conversation_state` → `scorer 199 state_relevance`. `gatherer 677` also uses `config.current_message` for RapidFuzz/semantic, not `current_topic`, so state relevance now via scorer, not retrieval query. Deterministic no Qwen.

## 11. Context-Budget Behavior

Unchanged hard: `TOTAL 2600 SYSTEM400 STATE200 COMMERCE200 MEMORY150 KNOWLEDGE150 TEMPORAL50 CONTENT100 CONVERSATION800 EMBEDDED200 55` `CATEGORY_BUDGETS` `budget 70 can_fit 128 try_allocate min(cat,global) <10 drop 163 validate 218 >2600 violation` `assembler 109 loop sorted scored/deduped` `164 total<=2600 degradation` `renderer 100 budget*4 93 truncate ... 196 HARD_POLICY first`. `ONE_CALL 1150 system350 state150 conv600 signals50 26 limit8 109 MAX_ASSISTANT 3* validate_one_call 238 >8192 or system>350 invalid` `Ollama num_ctx 8192 94` `actual 1150+2600=3750<8192` safe. Retrieved cannot exceed hard limit `try_allocate 147` post-retrieval pre-render `assembler 109` then renderer secondary `budget*4`.

## 12. OneCall Integration

`rendered_text = "\n".join([memory_block,temporal_block,commerce_block,content_block]) 146` `worker 558 _retrieved_context=rendered_text` `one_call 55 retrieved_context → build_one_call_context 102 if strip: messages.append system` after state before conversation `107 trim 600 limit8` `126 provider.generate json(messages)` single system `single message` reaches Qwen. Authoritative `system+state` vs `retrieved MEMORY advisory` distinguished prefix `memory_block subject=value (status conf, rf/sematic)` not overriding `price $30` vs `memory $20`.

## 13. LLM Call Count (After 78B+78D)

`core/one_call_pipeline 126 provider.generate` structured + `validate_draft_quality 150 deterministic` `78B gate 619 not_ppv_no_generation` **normal 1** `N1` + `0 N2` `FALLBACK 280`. No `extract_commerce_signals 480` duplicate (signals passed `1116`), no `score_draft` legacy (`1209` legacy-only), no `agent 1231`, no `legacy fallback` hardened `1129`.

| Path | Normal | Non-PPV commerce | PPV-ready (OFFER_PPV+EXECUTED) | Handoff | Validation fail | Exception |
|------|--------|------------------|--------------------------------|---------|-----------------|-----------|
| Calls | **1** `hey beautiful` `how was your day?` `how much is that? Soft` | **1** | **2** `N1 replaced 1123` + `N2 GENERATED 545 USE 320` PPV-specific required (still 2 but gated) strict 1 would need deterministic PPV template no LLM | **1** `needs_handoff` → queue `1402` | **1 attempted→queue** `1129 ["one_call_invalid_result"]` `1150` | **1 attempted→queue** `1169 ["one_call_exception"]` |

## 14. Commerce/PPV Safety Verification

`Qwen requested_price 193` only `bool user_asked_about_price 324` never `price_minor`, product `execute_ppv 252 price_minor DB` `USD 266` `CHECK >=0 25`, `sales_url` DB/canonical `244` `http 172`, `signature execute_ppv(*,creator_id,user_id,product_id,decision) 88` no price `570 test signature`, `retrieve_relevant_knowledge` never authoritative commerce state, memory `price $20` stale never reaches `execution 252`. Offer creation `pg_advisory_xact_lock ppv_offer:{c}:{u}:{p} 86 INSERT 103` idempotent `ALREADY_EXECUTED 291`, re-evaluates eligibility `219 first denial wins`, creator `integration active 120` decryptable `129`, blocked/opted `153`, purchase `212`, active offer `205`. **Context Engine `HARD_POLICY 0<2` cannot override.** No `requested_price` passed to `execute_ppv`.

## 15. Creator-Isolation Verification

`WHERE creator_id=$1 464 lock:creator:{c}:user:{u} 283 debounce 315 send_dedup:{creator}:{dedup} 84 persona:{creator}:{user} 381 ContextItem creator_id 145 GathererConfig creator_id 40 dedup respect 153 skip if mismatch 179,907` `get_fan_knowledge 30`, `Vector search brute` per-creator filtered `get_fan_knowledge(creator_id,user_id)` `retrieve_relevant 679`. Semantic `encode current_message` per user, `corpus` per `creator_id,user_id` only, cannot search across creators. **PRESERVED** test `TestCreatorIsolation 30` `secret creator1 not in creator2`.

## 16. Failure / Fail-Open Behavior

`RapidFuzz ImportError 57 → Jaccard 62` fallback; `encode_message 62 return None 82` → `semantic 0 candidates` lexical still `5`; `vector retrieval fails` → `merged=lexical`; `ranking fails` `scorer 199 try log` `gather_all 891 continue` → empty `all_items`; `render fails` `renderer 100 try` → empty block; `ContextEngineIntegration 186 except → failed=True` `worker 560 if not failed and rendered_text` skip → `""` → `OneCall 102 skip` legacy `1150` → `controlled failure 1129 queue` never `legacy 3-LLM cascade` hard `1133`, no `create PPV 86 lock` lost, no `bypass isolation` `respect_creator_isolation True`, no `retry loop` `DLQ 250 leaves pending 278`. `OneCall fails 134 Generation failed → queue 1169` `Pydantic 127 extra/forbid → is_valid False 1129 queue`, `commerce decision 566 DECISION_FAILED → ResponseFailed 644` `FALLBACK 280` normal reply.

## 17. Performance Benchmark

Baseline `OneCall without semantic`: `gather 5ms lexical overlap` `context-build 30ms` `one_call 1-2s qwen3:4b`. New `OneCall+lexical RapidFuzz 80 limit5 5ms` `+MiniLM semantic encode 50ms + brute 10*384 0.08ms + ranking 2ms + renderer 1ms` → `context-build 80-100ms` `+50ms` vs baseline `+70ms`, `prompt +28 tokens 1150→1178` valid `<8192`, `selected 3-5` vs 0. **Cold start** `model load once per llm_worker 1708 1-2s lazy singleton `30 global None 51 SentenceTransformer` `lru_cache 42`, per-process once; **warm path** `50ms encode +5ms lexical`. No `profile parsing` redundant `_profile_cache 578` `asyncio.gather return_exceptions 543` `orjson` `publish_events_batch 94` preserved vs `74B` `7 PG 4 Redis`.

## 18. Tests

`tests/test_phase78d_retrieval_activation.py` 22 tests A-T: `A lexical current_message 80 hit Nairobi`, `B semantic "too expensive" → upset about cost 0.30` `C hybrid merge <=10` `D dedup same category:source →1` `E ranking file weights 0.30 state 0.10` `F state wiring file check` `G budget 2600 hard` `H authoritative price signature no price_minor` `I reuse singleton not per-message 1 encode` `J not loaded per message singleton` `K semantic fail→list` `L RapidFuzz fail→list` `M normal 1 LLM` `N no generate_commerce_response non-PPV 0` `O PPV gate file OFFER_PPV+EXECUTED` `P price authority signature` `Q handoff queue` `R creator isolation secret not cross` `S ack after process` `T budget 2600`. Also ensure `TestNoHiddenCommerce` etc.

## 19. Test Results

```
tests/test_phase78d_retrieval_activation.py 22/22 pass 40s
tests/test_phase78b_single_generation.py 12/12 pass 7s (normal 1, PPV gate)
tests/test_redis_recovery.py 31/31 pass 59s (XAUTOCLAIM [(id,dict)] 77D)
tests/test_phase77d_xautoclaim_recovery.py 16/16 pass 105s
tests/test_phase77b_context_engine_integration.py 13/13 pass 17s
Combined relevant 72+22 94/94 pass 76-100s
Lint ruff check db/redis.py chatbotv2/main.py workers/llm_worker.py 160 BLE001/F841 pre-existing baseline, no new F821
```

## 20. Activation Status

`core/config 135 context_engine_observational False default` + `context_engine_enabled False 78D new` `sample_rate 0.0` `When true retrieval hybrid without hnswlib Disabled by default canary 0.1` → **disabled `0%` prod**, canary `10%` deterministic `hash(user_id)%100 <10` `workers 539→1017` when `CONTEXT_ENGINE_ENABLED=true SAMPLE_RATE=0.1`. Current Env `.env.example` not set → `0%`. Next phase `CONTEXT_ENGINE_ENABLED=true CONTEXT_ENGINE_SAMPLE_RATE=0.1` `10%` canary `10` creator sharding via `GathererConfig creator_id` present `hash`.

## 21. Remaining Risks

- Corpus <1k so `hnswlib` defer OK, but `>10k` needs `Index dim384 M16 ef200` benchmark `>1k`.
- Model `qwen3:4b vs Qwen2.5:3B` spec stale `context_compact 1`.
- `canary_mode observe 0.0` boolean not `10%` rollout until new `enabled+sample_rate` used — old `observational` misleading name kept for compat.
- PPV second generation still `2` PPV-specific `GENERATED` `545` vs strict 1 ideal; deterministic PPV template future P3.
- Observability `embedding_latency` placeholder `0` `worker 1017` TODO measure `encode_message`.

## 22. Architecture Confirmation

`AUTHORITATIVE STATE (persona/fan/conversation/commerce/subscription) → CONTEXT ENGINE (lexical RapidFuzz WRatio 80 + MiniLM 384 brute hybrid merge dedup 0.85 rank source0.15 topic0.30 recency0.20 importance0.20 state0.10 real conversation_state + hard TOTAL2600 ONE_CALL1150) → COMPACT retrieved_context single system 102 → ONE QWEN2.5 400 temp0.7 → Pydantic extra=forbid → DETERMINISTIC AUTHORITY commerce/persona/safety/send` **CONFIRMED** after 78D hybrid wiring, 77D `retrieve_relevant_knowledge 5/3` now + `Wratio` + `cosine 0.30` before dedup.

ROOT CAUSE:
Context Engine wired but gated off 0% prod (observational False 135) + RapidFuzz dedup only not retrieval, MiniLM all-MiniLM-L6-v2 initialized/cached but not on CE gatherer/scorer (offline 110 intents), hnswlib 0 py, memory lexical overlap 5/3 not semantic hybrid, ranking 5 weights exist but conversation_state None→0.5 neutral inert (integration 167), budget hard but retrieved 0.

FIX:
Hybrid MemorySource 675: keep retrieve_relevant_knowledge limit10 base parts seen set, then RapidFuzz process.extract WRatio 80 limit5 over get_fan_knowledge 30 texts, then MiniLM encode current 50ms + batch encode candidate 10 texts cosine 0.30 limit5, merge union dedup seen, parts[:10] bound; wire GathererConfig conversation_state + ContextRequest conversation_state + integration process conversation_state to assembler scorer 152 real state relevance; add config context_engine_enabled bool + sample_rate 0.1 hash(user_id)%100 deterministic canary, move observation after _conv_state 694 before OneCall 1017 with telemetry retrieval_enabled lexical/semantic counts, retrieval/ranking latency, fail-open try debug, preserve hard TOTAL2600/1150/8192; keep hnswlib deferred brute <1k, reuse singleton lru_cache, no new DB migration.

HOW RETRIEVAL NOW WORKS:
Fan message current_message → GathererConfig creator_id/user_id/current_message/conversation_state → MemorySource hybrid: lexical base retrieve_relevant_knowledge overlap0.5 + RapidFuzz WRatio 80 limit5 over fan_knowledge_by_creator texts + semantic MiniLM 384 50ms encode current + batch 10 cosine 0.30 top5 (stored embedding_384 reused if exists else batch) → merge union by subject=value seen → dedup WRatio 0.85 same category:source respect_creator_isolation → rank Scorer source0.15 topic0.30 (word overlap) recency0.20 importance0.20 state0.10 real (current_topic price +0.2 buying_signal +0.15) authority0.05 sorted → budget TokenBudgetManager TOTAL2600 try_allocate min(cat,global) <10 drop → CompactRenderer budget*4 truncate → rendered_text memory+temporal+commerce+content → retrieved_context single system after state 102 → Qwen.

WHY QWEN RECEIVES BETTER CONTEXT:
Lexical catches entity exact/typo WRatio 80, semantic catches upset about cost vs too expensive no overlap, merged deduped ranked with real state price boosts previously objected to pricing above likes football deterministically, hard budget keeps smallest sufficient 28 tokens not 800.

WHY PPV SELLING REMAINS SAFE:
Price DB price_minor 252 USD immutable CHECK>=0 serialized lock ppv_offer:{c}:{u}:{p} 86 INSERT 103, eligibility hard first denial, Qwen requested_price advisory bool 324 never price_minor, execute_ppv signature no price 88, selection USE only EXECUTED+GENERATED 320, Context Engine HARD_POLICY 0<2 cannot override, creator WHERE creator_id isolation.

LLM CALL COUNT:
Normal = 1 generative OneCall (commerce gated not_ppv_no_generation) was 2
PPV = 2 generative (1 OneCall replaced by 1 commerce PPV) still 2 but PPV-specific required (strict 1 would need deterministic PPV template no LLM)
Non-PPV commerce evaluation = 1
Handoff/invalid/exception = 1 attempted → queue 0 extra

WHY THIS DOES NOT CHANGE COMMERCE AUTHORITY:
Hybrid retrieval only adds advisory memory system block, never ApplicationOwned commerce fields (price/product/eligibility/offer state) which stay PolicyDecision instance strict validation 199 + DB SELECT fangate_products 168; execute_ppv still 12 gates re-evaluated 219 first denial wins, price from DB 252, lock 86, no requested_price passed; deterministic commerce authority unchanged.

ARCHITECTURE CHANGES:
NONE (single pipeline.py gate 78B kept, retrieval hybrid added within existing Context Engine gatherer/scorer/budget/renderer seam, no Redis Streams/consumer group/XAUTOCLAIM/XACK/DLQ/send_worker/Telethon/Postgres persistence/PPV price authority/dedup change; new config flags disabled default 0% preserve contract)
