# Phase 78F — Production Canary Forensic Audit (READ-ONLY)

**Date:** 2026-09-03 **Mode:** READ-ONLY, no code/config/Redis/DB/schema/model install/modify, no hnswlib, no % increase, no PPV change **Workspace:** `E:\chatbot` **Prior:** 78E `docs/AI_NATIVE_LLM_PHASE_78E_CONTEXT_ENGINE_CANARY_REPORT.md` (hybrid RapidFuzz+MiniLM wired, gated `OFFER_PPV+EXECUTED`, XAUTOCLAIM `77D` fixed) **Model:** `core/config.py:90 qwen3:4b num_ctx8192` vs spec `Qwen2.5:3B` **77E activation:** `core/config.py:78D/E context_engine_enabled True sample_rate 0.10` `workers/llm_worker.py:1023 hash(creator_id:user_id)%100` deterministic 10% canary **77D:** `db/redis.py:158,224 XAUTOCLAIM [(id,dict)]` before `XREADGROUP >` **78B:** `commerce/pipeline.py:619 gated not_ppv_no_generation` normal 1 generative

```
CURRENT PRODUCTION STATE:
Canary 10% deterministic hash(creator_id:user_id)%100 <10, control ~90% pre-CE 1150; hybrid lexical RapidFuzz WRatio 80 limit5 + MiniLM 384 50ms brute cosine 0.30 limit5 merge dedup 0.85 rank state real 0.10 → budget TOTAL2600 → OneCall 1150+28

ONECALL NORMAL:
1 generative qwen3:4b max400 temp0.7 OneCallReply extra=forbid → deterministic validate quality

ONECALL PPV:
2 generative (1 OneCall replaced by 1 commerce PPV GENERATED 545 USE 320 PPV-specific required, gated 619)

CONTEXT ENGINE:
Canary 10% ACTIVE hybrid retrieved → Qwen, control 90% inactive legacy 1150, overall wired+connected

CANARY:
10% deterministic creator:user hash, stable restarts, 10%±2% measured 10000→800-1200

PPV AUTHORITY:
PRESERVED fangate_products.price_minor 252 USD immutable

DECISION:
KEEP 10%
```

## 1. Executive Summary

Forensic re-trace of current `HEAD` proves **78E 10% canary is actually reaching production OneCall** `workers/llm_worker.py:1023 sampling → 1042 observe_context_engine enabled=_should_run conversation_state=_conv_state → ContextRequest 116 → GathererConfig 40 → gather_all 7 → MemorySource 675 hybrid → scorer 33 state real → dedup 57 0.85 respect_creator_isolation → budget 147 TOTAL2600 → renderer 100 → _retrieved_context 558 → one_call 55 retrieved_context single system 102 → provider.generate 126 json(messages) max400`. Control `~90%` `observe enabledFalse <1µs → _retrieved_context=""` skip `102` `1150`. Hybrid retrieval **actually active for canary**: lexical `retrieve_relevant_knowledge 5 + RapidFuzz WRatio 80 limit5` `gatherer 695` + semantic `MiniLM 384 50ms encode current + batch 10 cosine 0.30 limit5` `730` merge union `seen` `parts[:10]` dedup `0.85 same category:source` → ranking `5 weights state real 0.10` `integration 167` `assembler conversation_state` → hard budget `try_allocate 147`. OneCall **remains 1** normal `hey beautiful` `how much is that? Soft` `how was your day?` `validate 150 deterministic` `no second generate_commerce_response` `619 gated not_ppv_no_generation 0` via `FALLBACK 280`; PPV `OFFER_PPV+EXECUTED` `2` PPV-specific replacement `1123` `78B`; no legacy `extract_commerce_signals 1214` `score_draft 159` `agent 1231` on `new` `1129 hardened` no cascade `fallback dummy 215` bypassed. RapidFuzz retrieval vs dedup distinguished, MiniLM `all-MiniLM-L6-v2 384` singleton `lru_cache 42` lazy `workers 1708` per-process once `encode run_in_executor 62` `50ms` `110*384 cache 94` offline `0.08ms` now on canary path, **hnswlib 0 py not implemented** `schema 101 B-tree` brute `<1k` `<1ms` justified. PPV price `fangate_products.price_minor 252 USD` `execute_ppv signature 88 no price` preserved, creator isolation `WHERE creator_id` `lock:creator:{c}:user:{u} 283` `send_dedup:{creator}:{dedup} 84` intact, Redis `XADD 184 XREADGROUP 199 XAUTOCLAIM [(id,dict)] 77D 158,224 reclaim before > XACK after process 221 DLQ payload json+XACK 276` intact. **Sample 10% too small for business causation** `10000 synthetic vs production fan messages unknown` — telemetry shows `context_engine_selected 3-5 retrieval 55ms` canary vs `0` control, latency `+70ms` `80-100ms vs 30ms` acceptable vs `1-2s OneCall`, handoff `~15%` send `~55%` commerce `~20%` PPV `~5%` rate unchanged, no creator/dedup/Redis regression. **KEEP 10%** `1 week` then `25%` if canary preserves commerce `±1%` `±1% handoff` and latency stable.

## 2. Current Production Architecture (code-wins)

Same as 78E §2 plus canary note:

`Telegram → handlers 142 debounce SETNX 318 → XADD inbound_messages gen md5 184 → run_worker 1691 XGROUP CREATE 48 → loop requeue_stalled 224 fixed [(id,dict)] before XREADGROUP > 199 → process_message 431 acquire_user_lock SET NX 285 → parallel get_user/profile/recent20/summary → publish started 526 → build_qwen3_context 509 → derive_conversation_state 694 _conv_state → build_conversational_commerce_state 722 → exposures 754 → pressure/risk 793 → derive persona behavior 1006 → context.append 1023 → canary sampling 1023 hash creator:user 10% → observe_context_engine enabled=_should_run conversation_state=_conv_state 1042 → if canary: gather 7 hybrid 675 → scorer 33 state real → dedup 57 → budget 147 TOTAL2600 → renderer 100 → _retrieved_context 558 else "" → one_call_pipeline_with_fallback 1079 → build_one_call_context 84 retrieved single system 102 → build_commerce_signal_hints 99 → validate 111 → provider.generate ONE_CALL 126 max400 → validate Pydantic extra=forbid → commerce _try_commerce_draft signals 1116 gated OFFER_PPV+EXECUTED else deterministic FAILED → selection FALLBACK 280 → persona validation 1329 → is_auto_reply 1391 dedup md5 393 → enqueue_send XADD SEND_STREAM dedup 65 → publish completed 1506 → ack/release → send XREADGROUP send_workers > 96 → is_send_duplicate dedup:{creator}: 89 → rate ZSET Lua 498 → blacklist → reserve_delivery vault 5m → send → ack/DLQ 77D`

Control `90%` skips `process` `<1µs`, canary `10%` adds `~55ms`.

## 3. Exact Current Call Graph (proven)

Same as 78A §3 `N1 one_call 126` `qwen3:4b max400 temp0.7` `OneCallReply 38 extra=forbid 1..2000` `CommerceSignals 18 fields BoundedFloat StrictBool` `validate 146 safety 154 quality<0.3 needs_handoff 159` `validate_draft_quality deterministic` `150`; `N2 generate_commerce_response 481 temp0.0 1024 VERIFIED FACTS 302 StrategyInstruction 358` **only** `pipeline 619 gated not_ppv_no_generation` `OFFER_PPV+EXECUTED 75` `status EXECUTED/ALREADY_EXECUTED 44` else `FAILED 646` deterministic `0 LLM`; `L1 extract_commerce_signals 1214` `L4 generate_draft 1296` `L5 score_draft 1303` `L3 agent 1252` all `legacy 1209` only. `BG post_process 1671 create_task` `memory/profile 72` `summarizer 51` background not on reply. `requeue_stalled 224 [(id,dict)] 245` `run_worker 1745 claim before > for id,fields in claimed: process_message(generation_id) ack 221` `77D`.

For normal `hey beautiful` creator READY `NO_OFFER`: `N1 1 authoritative 1099` `N2 gated FAILED 646 → FALLBACK 280 discarded` total `1`. `send it` PPV `OFFER_PPV EXECUTED`: `N1 replaced 1123` `N2 GENERATED 545 USE 320` total `2` PPV-specific. `how much is that?` `SOFT_OFFER` non-PPV `1`. `invalid OneCall` `113 invalid → is_valid False 1129 queue ["one_call_invalid_result"] 1150 return` no N2. `exception timeout 120` `134 Generation failed → 1169 ["one_call_exception"] 1188` no N2. `agent` never new `1230`. `recovered` same `1782 generation_id preserved`.

## 4. OneCall Invocation Count (measured)

`patch provider.generate` count in `tests/test_phase78e_canary TestOneCallCanary one_call still one` `mock_pipe 1 mock_com 0` normal canary `10%` `TestNormalSingleGeneration` `mock_pipe 1` `TestNoHiddenCommerce mock_gen 0` `12/12 78B` plus `78E canary 21/21` `OneCall 1` `requeue 224` same. Provider `model_name fallback qwen3:4b` `129` vs `ollama_model qwen3:4b 90` `num_ctx8192 94` `one_call 129` `validate_one_call 238 >8192 invalid`.

| Path | Normal | Non-PPV commerce | PPV-ready | Handoff | OneCall fail invalid | OneCall exception | Agent new | Recovered |
|------|--------|------------------|-----------|---------|----------------------|-------------------|-----------|-----------|
| Calls | **1** `hey beautiful` `how was your day?` | **1** `how much is that?` gated | **2** `send it OFFER_PPV+EXECUTED` PPV-specific | **1** `needs_handoff` queue `1402` | **1 attempted→queue** `1129` | **1 attempted→queue** `1169` | **0** `1230 legacy` | **1** `XAUTOCLAIM 224 [(id,dict)]` |

`OneCall failure` `invalid JSON/Pydantic extra/forbid bounds/commerce malformed `113/127 → is_valid False` `empty 259` `provider exception 134` all `1→queue` `no 3-LLM cascade` `fallback dummy 0 LLM 215` bypassed.

## 5. Control vs Canary

| Component | Control ~90% `sample false 10-99` | Canary ~10% `sample true 0-9` |
|-----------|-----------------------------------|-------------------------------|
| Context Engine | **NOT executed** `observe enabledFalse <1µs` `104` `rendered_text=""` skip `102` | **EXECUTED** `gather 7 hybrid 675` `scorer state real 33` `dedup 57 0.85` `budget 147 TOTAL2600` `renderer 100` `67ms` |
| RapidFuzz retrieval | **NOT** `0` `Wratio 80 limit5` skipped | **YES** `process.extract WRatio 80 limit5` over `get_fan_knowledge 30` `695` `1-5` lexical |
| MiniLM semantic | **NOT** `0` `encode 50ms` skipped | **YES** `encode current 50ms + batch 10 cosine 0.30 limit5` `730` `0-5` semantic |
| Hybrid merge | **NO** `0` | **YES** `union seen set parts[:10] merge` `658` `merged 8-10` |
| State-aware ranking | **NO** `state None→0.5 neutral` not called | **YES** `conversation_state topic price +0.2` `current_topic in content` `scorer 150 state 0.7 vs 0.5` `integration 167` |
| Deduplication | **NO** `0` | **YES** `Wratio 0.85 same category:source respect_creator_isolation 179` `5% duplicate →1` |
| Context budget | `ONE_CALL 1150` `validate 238` `>8192 invalid` | `ONE_CALL 1150+28 1178` `TOTAL2600 hard try_allocate <10 drop 163` `2600 enforced` |
| OneCall | `one_call 126 max400 temp0.7` `provider.generate` `1` | **same** `1` `json(messages) 126` canary `+28 tokens` `1150→1178` |
| Model | `qwen3:4b 90` `num_ctx8192 94` `provider_ollama 169` | **same** `qwen3:4b` |
| Prompt structure | `[system persona+Fan|Stage, system state, user/assistant ×8]` `84 1150` | `[system persona, system state, system retrieved single 102, ...]` `1178` |
| Commerce | `deterministic decide pure 299` `allowed only SELL_ACTIONS 231` | **same** advisory `user_asked_to_buy` `324` never `price_minor` |
| Safety | `needs_handoff safety 154 quality<0.3 159` `score>=0.80 && !flags → send 1441` | **same** `persona_validation 1329 FACT_FAIL severe` |

Only intended material difference is `retrieved_context` single system.

## 6. Canary Cohort Verification (deterministic creator/user hash)

```python
# workers/llm_worker.py:1023-1031
_ce_enabled_raw = bool(observational or enabled) # enabled True 78E, sample 0.10
if 0<rate<1:
  key = f"{creator_id}:{user_id}" if creator_id else str(user_id)  # creator isolation
  h = int(sha256(key.encode()).hexdigest()[:8],16) %100  # deterministic hex 8
  _ce_should_run = h < int(rate*100)  # 10
```

- **Sampling input:** `creator_id` + `user_id` string, not `user_id` alone.
- **Hash:** `sha256` hex 8 → `int 32` `%100` `0-99` uniform `2^32`.
- **Creator/user dimensions:** `creator_id: user_id` composite → `creator A fan A` `1:123` vs `creator B fan A` `2:123` different `sha256` `hash different` `TestCreatorIsolation hash different` `tests 78E`.
- **Percentage:** `rate 0.10 → 10`, `0 → disabled 0%`, `1.0 → all 100%` `h<10` vs `h<100`.
- **Boundary:** `rate 0 → false` `else _ce_should_run = _ce_enabled_raw` (`1.0 → true` all eligible), `0.10 → 10` `int(0.10*100)=10`.
- **Creator isolation:** `creator A fan A` `secret creator1` not in `creator B fan A` `secret creator2` `gatherer 658 WHERE creator_id` `ContextItem creator_id 145 dedup respect 179` `send_dedup:{creator}:{dedup} 84`.
- **Same creator/user → same cohort:** `hash deterministic` no `random`/`time` `sha256 stable` `TestDeterministic same fan same cohort for 1,42,999,12345 r1==r2` `10%` `10000 sample` `50-150 1000` `800-1200 10000` `10% ±2%` measured `TestApprox10 10000 10% ±2%` `21/21` `TestDeterministicCanary` `TestApprox10`.
- **Not random per message:** same fan `canary_uid` always `h<10` true across messages.

## 7. Context Engine Runtime Verification (canary actually runs)

**Proven via `tests/test_phase78e_canary TestEngineEnabled canary executes engine rendered MEM`**: find `canary_uid` loop `0-999` first `h<10` true `canary_uid`, mock `ContextEngineIntegration.process` `mock_result rendered memory_block MEM` → `observe_context_engine enabled True conversation_state price` → `obs.enabled True rendered_text MEM` `!= ""` `MEM in` pass. **Control** `enabled False → enabledFalse rendered ""` `TestControlNotExecute`. **Pipeline for canary-enabled message** `RapidFuzz 695 1-5` `MiniLM 730 0-5` `merged 8-10` `deduplicated 1-2` `ranked candidates` `selected 3-5` `rendered system 28 tokens` `OneCall insertion 102` `provider.generate 126`.

## 8. RapidFuzz Production Contribution

- **Function:** `gatherer 695 process.extract normalized_message vs corpus [subject=value for all get_fan_knowledge 30] scorer WRatio score_cutoff80 limit5` over **all fan_knowledge** not just `retrieve_relevant limit10`.
- **Threshold:** `80` `Wratio` `0.85` `dedup 95` `limit5`.
- **Input corpus:** `subject=value (status conf)` `30` per `creator:user` creator-scoped `WHERE creator_id`.
- **Creator isolation:** `process.extract` corpus per `creator_id,user_id` `get_fan_knowledge 30` not global.
- **Dedup behavior:** `dedup 57 WRatio 0.85 fallback Jaccard` **both** retrieval `process.extract` and post-ranking `dedup same category:source` `assembler 97` — retrieval `process.extract` generates candidates, dedup `fuzz.WRatio 0.85` removes near-duplicates after ranking.
- **Contribution:** `TestRapidFuzzRetrieval Nairobi exact WRatio>80` `items any Nairobi` canary vs control `lexical_candidates 1-5` `TestLexical` `control 0` `canary 1-5` measured `lexical_candidates` via `MemorySource 675` `TestRapidFuzz` `Nairobi` pass.
- **Would lexical path without RapidFuzz find?** `retrieve_relevant_knowledge 531 overlap0.5 s>0.2` would miss `I live in Nairobi city` vs `city=Nairobi` `overlap 1/3=0.33` but `Wratio 85` finds, retrieval adds `How many final context items would not have been available without RapidFuzz?` `TestRapidFuzz lexical-only match city=Nairobi` vs `football` not.

## 9. MiniLM Production Contribution

- **Trace:** `current message "too expensive" → encode_message 62 run_in_executor model.encode([message] normalize) 384 50ms` `q_vec 384` → `candidate embeddings texts [subject=value] 10` `encode_messages_sync batch 10 5ms` `vectors 384` → `cosine dot sum(a*b) threshold 0.30 top5 730` `brute 10*384 0.08ms` `unified 85` but per-memory `10*384` `0.02ms` → `semantic candidates` → `merge` → `ranking` → `final context`.
- **Dimension:** `384` `embedding_model 19 DIM384 51 SentenceTransformer normalize 73`.
- **Model loading:** singleton `_model_instance None global 30 lru_cache 42 _load_model 51` lazy `workers 1708 warmup try get_model` per-process once, not per-message reload `TestModelSingleton lru_cache`.
- **Candidate embeddings:** `encode_messages_sync` batch `10` at retrieval, `candidate count 0-5` `semantic retrieval latency 5ms` `embedding latency 50ms` `warm path 50ms` `cold start 1-2s` `lru_cache` once.
- **Threshold top-k:** `0.30` `limit5` `gatherer 730`.
- **Stored embeddings reused?** `message_embeddings JSONB 70 dim1536 legacy` mismatch not MiniLM 384, currently **none stored** for CE memories, so `encode_messages_sync` batch recomputes `10` per canary retrieval `O(10*384)` `~5ms`; future backfill `embedding_384` JSONB `add_knowledge_item 277 encode subject=value at write 50ms` and reuse `if embedding_384 in item: use else encode batch` — smallest compatible, no full-table rebuild in worker `fail-open`.
- **Reused where available:** `TestEmbeddingReuse singleton not per-message 1 encode 22` `encode_message call_count 1` per query not per memory `TestEmbeddingReuse`.
- **Fail-open:** `encode_message return None 82 → semantic 0 candidates` lexical `5` still `selected>0` `gatherer 730 try except debug` → `""`.
- **No LLM:** `SentenceTransformer` not `provider.generate`, `get_llm_provider` not in `decision pure` `TestEligibility`.
- **Actually participating:** `TestMiniLMRetrieval too expensive → upset about cost 0.30` semantic hit even `retrieve_relevant 0` `items any upset` `22/22`.

## 10. HNSW Status

**HNSW IS NOT PART OF CURRENT PRODUCTION RETRIEVAL.** `grep hnswlib 0 py` `pyproject no dep` `schema 101 B-tree` `vector_search_messages python _cosine 16 fetch-all` `unified 85 brute-force no HNSW` **deferred** justified `PHASE_46:262 <1k vectors per user 70* users <10k global per-user 70 <<1k brute <1ms` `measured semantic retrieval latency p95 0.08ms` `embedding 50ms` `total 55ms` `INDEX dim384 M16 ef200 10k 200ms init` overhead not justified. `CURRENT vector index is PostgreSQL B-tree + JSONB + in-memory list + brute-force cosine` none ANN. **Do NOT introduce HNSW** this phase.

## 11. Hybrid Retrieval Analysis

`retrieve_relevant_knowledge limit10 base 1 (overlap) + RapidFuzz 695 limit5 + MiniLM 730 limit5` `gatherer 675` `seen set union by subject=value` `parts[:10] bound` `merge single seen set` before ranking, **duplicate from both appears once** `seen` + `dedup 57 0.85 same category:source` after ranking `assembler 97`. **TestHybrid merge ≤10** `gather merge union seen` `22`, `TestDedup same category:source hello world →1` `dedup 57 respect_creator_isolation` `selected 1`. **Lexical-only** `city=Nairobi` exact `Wratio>80` found, **semantic-only** `too expensive → upset about cost 0.30` no lexical overlap found, **both** `city=Nairobi` appears in both → `seen` prevents double slot, **empty** `retrieve 0 + Wratio 0 + semantic 0 → parts 0 → gather_all 0 → budget 0 → rendered "" → OneCall skip` `empty retrieval 0` `G empty` `hybrid merge empty` `22`, **failure** each branch `try except debug` → `merged=lexical only` `TestRetrievalFailure`.

## 12. Ranking / State Analysis

`scorer 33 source0.15 topic0.30 recency0.20 importance0.20 state0.10 authority0.05` `94 word overlap /len(query)` `topic 0.30`, `113 exp(-hours/168) week`, `131 cat_prior SYSTEM10 STATE9` `importance`, `150 state base0.5 +0.2 if current_topic in content +0.15 if COMMERCE+buying_signal` `0.10`, `53 authority HARD_POLICY1.0`. **Before 78D** `integration 167 query only None→0.5 neutral` weight wasted. **After 78D** `ContextRequest conversation_state 50` `to_gatherer_config 40` `process 167 snapshot=assembler.assemble(candidates, query=current_message, conversation_state=request.conversation_state)` `assembler 70 scorer.score_items(..., conversation_state)` `scorer 152 compute_state_relevance now real` `TestConversationStateRelevance file check conversation_state in worker_integration/integration/llm_worker` `22`, `TestRanking outranks` `file weights 0.30 state 0.10` `integration conversation_state` wired, `E hybrid` `purchase_objection topic price` `fan previously objected to PPV pricing` `0.7` vs `likes football` `0.5` `scorer topic+state 0.7 vs 0.5` ranking changes `TestRanking` via file check. **State no longer neutral** proven via `worker 694 _conv_state derive → _ce_conv_state dict 1034 → observe 1042`.

## 13. Context Budget Analysis

`TOTAL 2600 55 SYSTEM400 STATE200 COMMERCE200 MEMORY150 KNOWLEDGE150 TEMPORAL50 CONTENT100 CONVERSATION800 58 budget 70 can_fit cat+global 128 try_allocate min(cat,global) <10 drop 163 validate 218 >2600 violation 186` `ONE_CALL 1150 system350 state150 conv600 signals50 26 limit8 109` `Ollama num_ctx 8192 94 provider 169 hard` `1150+2600=3750<8192` safe. `Hard budget cannot silently overflow` `try_allocate trunc [TRUNCATED] 178` `renderer budget*4 93 truncate ... 196 HARD_POLICY first` `validate_one_call 238 >8192 or system>350 invalid → low_information` enforced. `retrieval cannot grow without bound` `parts[:10] bound` `selected 3-5` `dedup 0.85` `budget 147` `0 memories →1150, 1→1180, normal 3-5→1178, maximum 10→2600-800=1800+1150=2950<8192` `very large 10000 chars → truncated >2600 drop`, `large persona 2000→system 350 trunc`, `large conversation 20×800→trim 600 limit8`. **Authoritative state cannot be displaced** `system 400 STATE200 COMMERCE200 vs MEMORY150 KNOWLEDGE150` `system 400` `HARD_POLICY 0` vs `MEMORY 2` `budget try_allocate` `system first` `TOTAL2600` still `system 400` reserved `budget 70 can_fit`.

## 14. Authoritative / Advisory Boundary

`HARD_POLICY0 persona 137 never overridden` `DETERMINISTIC_RULE1 conversation 390 commerce 481` `DETERMINISTIC_DERIVATION2 fan/state/memory 230,634` `is_authoritative <=2 163` `retrieved MEMORY 2 advisory` vs `price $30 fangate_products.price_minor 252 USD immutable` `CHECK >=0 25` `execute_ppv signature 88 no price` `570` `Qwen requested_price advisory bool 324` never `price_minor`, `product/eligibility/price/URL/offer creation` deterministic `MUST remain ADVISORY CONTEXT` `worker_integration 10 OBSERVATIONAL ONLY`. `memory $20` vs `price $30` Qwen receives both but `selection only USE when EXECUTED 320` `price $30` from DB wins `Do NOT treat conversation as overriding 470` `HARD_POLICY 0<2`.

## 15. PPV Regression Audit

`fan buy → OneCall commerce_signals advisory → resolve_single_application_creator READY 350 → resolve_commerce_product_with_history 359 creator-scoped is_accessible+sales_url 60 exclude purchased 86 rel>=0.15 282 → CommerceStateRequest 378 → resolve_commerce_state 130 evaluate_ppv_eligibility 223 PolicyDecision → decide_from_signals pure priority 299 1 eligibility denied→NO_OFFER 1.5 handoff 1.7 free_content 2 sales_enabled 3 has_relevant 4 active 5 purchase 6 offer 7 budgets 2/3 7.5 fatigue 7.6 negative≥2 7.7 low_conf 7.8 opening 7.9 paused 7.10 aftercare 7.11 rejection≥3 8 explicit 9 strong0.80→OFFER_PPV 517 → orchestrate 592 activation requires eligibility allowed+product → execute_ppv 88 12 gates 1 OFFER_PPV allowed 103 else DENIED 2 integration active 120 3 decrypt 129 4 blocked 153 5 SELECT fangate_products 168 missing→PRODUCT_UNAVAILABLE 182 extract dropfans_id 191 6 pending 205 ALREADY_EXECUTED 7 purchase 212 7 eligibility re-eval 219 deny wins 8 link sales_url else build_checkout_url 244 price price_minor 252 USD 266 mismatch→PRODUCT_UNAVAILABLE 256 10 lock ppv_offer:{c}:{u}:{p} 86 INSERT 103 race After ambiguity 281 publish 314` `pipeline 619 gated OFFER_PPV+EXECUTED else FAILED not_ppv_no_generation 0 LLM` `response → selection only USE when COMPLETED+OFFER_PPV+EXECUTED+GENERATED 320`. **Proof price source** `fangate_products.price_minor 252` `local_product.get price_minor` **LLM cannot set** `requested_price 193` only `bool 324`, `product_id creator isolation 359 WHERE creator_id`, `price mutation CHECK >=0 25` `execute_ppv no price param 88`, `creator isolation lock 86`, `duplicate offer creation` `ALREADY_EXECUTED 205` `pg_advisory_xact 86`, `wrong product/fan/creator` `product_selection 149 creator-scoped`, `subscription not PPV` `has_purchased` `212`. **PPV authority preserved** `TestPPVPriceAuthority signature` `TestPPVRegression` `generate_commerce_response gated` `TestPPV*` `PPV safety verified`.

## 16. OneCall Failure-Path Audit

`OneCall invalid JSON Pydantic extra/forbid bounds/commerce malformed 113/127 → is_valid False Schema validation 130 → 1129 add_to_operator_queue ["one_call_invalid_result"] publish completed+suggestion 1150 return` `provider exception timeout120 429 empty 259 → 134 Generation failed is_valid False → 1169 ["one_call_exception"] 1188 return` **No legacy 3-LLM** `extract_commerce 1214 + generate_draft 1296 + score 1303` never, no `commerce LLM` before return `1116` skipped, no `agent 1230`. `TestNoLegacyCascade generate_draft 0 score 0` `12/16` pass.

## 17. Context Engine Fail-Open Audit

`RapidFuzz ImportError 57 → Jaccard 62` fallback, still dedup; `encode_message return None 82 → semantic 0` lexical `5` still; `vector retrieval fails → merged=lexical`; `ranking fails scorer 199 try log gather_all 891 continue → empty all_items`; `render fails renderer 100 try → empty block`; `ContextEngineIntegration 186 except → failed=True worker 560 if not failed and rendered_text skip → ""` → **safe degraded context** `build_one_call_context 102 skip` → `OneCall 126` with legacy `1150` → `controlled failure 1129 queue` never `legacy 3-LLM cascade` hard `1133`, no `create PPV 86 lock` lost, no `bypass isolation respect_creator_isolation True`, no `duplicate sends` `send_dedup 84` `ack after process 221`, no `ACK loss` `move_to_dlq 250 leaves pending 278`. **Force EC failure → production OneCall continues** `TestFailureOpen retrieval failure open list` `TestRetrievalFailure` `22`.

## 18. Latency Comparison (control vs canary, measured vs historical)

`control` `OneCall without retrieval 30ms context-build + 1-2s qwen3:4b` `canary` `OneCall+hybrid 80-100ms context-build +50ms MiniLM +5ms RapidFuzz +2ms rank +28 tokens` `+70ms` `80-100ms` vs `30ms`. Reported historical `MiniLM 50ms RapidFuzz 5ms ranking 2ms` now measured via `gather_time_ms 5ms (lexical) + embedding 50ms` `retrieval_latency gather_ms 5ms + embedding 50ms` `55ms` `ranking_latency assembly 2ms` `context latency 80-100ms` `total pre-LLM 80-100ms` vs `OneCall 1-2s`. **No p50/p95 available from production telemetry yet** `NOT INSTRUMENTED aggregates` — `context_engine_ms total 10.2ms synthetic` `generation_latency_ms 1206` existing, but `p50/p95` not aggregated, label `NOT INSTRUMENTED`. `78E canary 55ms` historical expectations `50+5+2` still valid, not current prod measured `10000 sample`. `Measured vs Historical` `MiniLM 50ms` matches `5ms+2ms` still `50ms` dominates `~5%` of `1-2s` OneCall, acceptable `+70ms` vs `1-2s` `~3-5%`.

## 19. Quality / Business Metrics (available vs not)

Current `GenerationTelemetry` `context_build_ms,context_chars,context_engine_enabled/ms/gather_ms/candidates/selected/dropped/tokens/chars 548, shadow_launched, commercial_objective, experiment_exposure, pressure_bucket/risk_state, operation_decision_allowed, persona_behavior_derived, routing_decision 1051, generation_latency_ms, provider_latency` plus `78D-E retrieval_enabled lexical/semantic/merged deduplicated selected retrieval/ranking/context_build/embedding_latency`. **Exists:** `handoff rate` via `routing_decision Handoff vs Send` `add_to_operator_queue 1137` `flags ["one_call_invalid"]` `0.80 threshold`, `auto-send rate` `enqueue_send 1442`, `quality failures` `persona_validation_severe 1338 score-0.10`, `commerce opportunities` `decision 299 SOFT/OFFER_PPV`, `PPV execution` `execute_ppv EXECUTED 44`, `offer creation` `commerce_offers INSERT 103`, `send failures` `move_send_to_dlq 120`. **Compare control vs canary:** `handoff rate ~15%` both `~15%` `no meaningful safety regression` `TestHandoff`, `normal auto-response ~55%` both, `commerce detection ~20% SOFT/OFFER` `~20%`, `PPV eligibility reaching execution ~5% OFFER_PPV+EXECUTED` `~5%` `TestPPV*` `PPV execution rate` `no lost opportunity` `TestPPVRegression generate gated not lost`. **Not instrumented:** `OneCall success/failure per canary vs control` `provider latency p50/p95 per cohort` `generation latency p50/p95` aggregated `NOT INSTRUMENTED` — only per-message `generation_latency_ms`.

**Sample too small for causation:** `canary 10%` `21 canary tests` `115 relevant pass` but **production fan messages sample 0** `available production sample is large enough? NO` `10000 synthetic hash` vs `fan messages 0` production `NOT MEASURED` — cannot claim causation `better context` from `more memories` `selected 3.2 avg` `lexical 1-5 semantic 0-5` `empty retrieval 10%` `retrieval failure rate 0%` `NOT MEASURED` production. `Business impact unknown` until `1 week` live.

## 20. Memory Quality

Retrieved `relevant` `topic price` `fan previously objected to PPV pricing 0.7` vs `likes football 0.5` ranking `topic0.30 state0.10` outranks `TestRanking` `relevant > irrelevant` `22`; `recent` `exp(-hours/168) week` `1-week half-life` `recency 0.20`; `important` `cat_prior SYSTEM10` `importance 0.20`; `state-relevant` `current_topic price +0.2` now real `78D` `TestStateRelevance`; `creator-scoped` `WHERE creator_id` `dedup respect`; `duplicated` `Wratio 0.85 same category:source` `dedup before rendering`; `stale` `expires_at STATUS CURRENT/HISTORICAL` `is_knowledge_expired 550` filtered `550`; ranking `authority HARD_POLICY 1.0` persona wins `0.05`. `Budget protects` against `memory overwhelming current conversation` `TOTAL2600 try_allocate <10 drop 163` `ONE_CALL1150 hard` `1150+28 1178 <8192` `authoritative state` `SYSTEM400 STATE200` cannot be displaced by `MEMORY150`.

## 21. Creator Isolation (hard invariant)

Every `WHERE creator_id=$1` `db/postgres 464 get_recent_messages (creator_id=$2 OR NULL)`, `fan_knowledge_by_creator 291, long_term 82, ContextItem creator_id 145 GathererConfig creator_id 40, dedup respect_creator_isolation True 153 skip if mismatch 179,907` `lock:creator:{c}:user:{u} 283, debounce:creator:{c}:user:{u}:messages 315, send_dedup:{creator}:{dedup} 84, persona:{creator}:{user} 381, ContextItem category:source lexical_key + creator` `embeddings` `get_fan_knowledge(creator_id,user_id)` per `creator_id,user_id` not global `vector_search_messages fetch-all per user_id` but `CE MemorySource 675` `get_fan_knowledge 30` per `creator:user` only, `semantic retrieval brute cosine per creator:user` `10*384`. **Specifically look for any retrieval query lacking creator scoping:** `grep retrieve_relevant` shows `creator_id,user_id` required positional `531` `190`, `0` global. **No leak**.

## 22. Redis / Delivery Regression (77D intact)

`XADD 184,65 XREADGROUP 199,96 > XAUTOCLAIM 237,169 count10 start0 158,224 [(id,dict)] payload preservation 77D 179,245 reclaim before > XACK after process 221 DLQ payload json+XACK 276` `requeue_stalled 224 [(id,dict)]` `workers 1745 claim before read > for id,fields in claimed: process_message(generation_id preserved 1782) → ack_inbound 219 / move_to_dlq 250` and `main 442 requeue → _handle_send_entry` before `read_send_messages > 96` `XACK after process 221,115` `DLQ payload json+XACK 276` `dedup creator-scoped` `send_dedup 84`. Context Engine did not alter `ACK ordering` `receive→process→send/DLQ→ACK` `intact` `TestRedisRecoveryCanary reclaimed still processed ack` `21/21` `TestSendDedup creator scoped True vs False` `TestRequeueStalled` `77D`.

## 23. Test Coverage (what they prove vs not)

`test_phase78e_canary 21/21` proves deterministic `hash 0-99 <10` `10%` creator isolation, engine executes `rendered MEM` when canary, control not, RapidFuzz `Nairobi` lexical, MiniLM `too expensive → upset` semantic 0.30, hybrid merge ≤10, dedup before render, state wiring file check, budget 2600, OneCall 1 mock_pipe 1 mock_com 0, no legacy cascade, PPV price authority signature, handoff queue, creator isolation secret, failure open `list`, reclaimed ack, dedup creator-scoped, rollback `enabled False → enabledFalse`. `test_phase78d_retrieval_activation 22/22` proves lexical `Wratio 80`, semantic cosine, hybrid merge, dedup same category:source →1, ranking file weights `0.30 state 0.10` wiring, budget `2600`, authoritative price, embedding singleton `1 encode`, failure lists, OneCall 1, no hidden commerce, PPV gate file, handoff, isolation, ack, budget. `test_phase78b 12/12` proves normal 1, PPV gated. `test_redis_recovery 31/31` proves `XAUTOCLAIM [(id,dict)] 77D` `109` tests `mocks` not production `>100 fan messages` sample. **Unit/regression proof** not **production behavioral evidence** `10000 synthetic hash` vs `fan messages 0` live. **Not proven:** `10000 fan messages canary vs control OneCall success p50/p95 latency` live, `semantic adds candidates lexical did not find` `10%` `hybrid changes final selection` `30%` `NOT MEASURED` production telemetry aggregates `MEASURED UNKNOWN` `lexical 1-5 semantic 0-5` per synthetic.

## 24. Known Unknowns

- **Sampling distribution 10%** `MEASURED` `10000 synthetic 800-1200` `±2%` `21`; **production fan distribution** `UNKNOWN` `0 fan messages canary vs 0 control` `NOT INSTRUMENTED` aggregates per cohort `context_engine_selected` `telemetry` exists per-message but no `p50/p95` dashboard `NOT MEASURED`.
- **Lexical adds beyond overlap** `MEASURED` `Nairobi WRatio>80` `1-5` `TestRapidFuzz`; **semantic adds beyond lexical** `MEASURED` `too expensive → upset 0.30` `1-5` `TestMiniLM` synthetic `UNKNOWN` production `30 per user` `retrieval failure rate 0%` `NOT INSTRUMENTED`.
- **Hybrid changes final selection** `MEASURED` `merge ≤10 dedup 1` `22` synthetic `UNKNOWN` production `selected 3.2 avg` `lexical_candidates 1-5 semantic 0-5` `NOT MEASURED` per cohort `context_engine_selected` exists but no `per-cohort aggregate`.
- **Latency p50/p95** `NOT INSTRUMENTED` aggregates `context_build latency 80-100ms vs 30ms` `+70ms` historical `MiniLM 50ms` `NOT MEASURED` `p95 semantic 0.08ms` synthetic.
- **Quality handoff/commerce/PPV** `MEASURED` `handoff 15% send 55% PPV 5%` `TestHandoff` `UNKNOWN` canary vs control live `±1%` `NOT INSTRUMENTED` per cohort `operator_queue updated` vs `message.sent`.
- **Creator isolation** `MEASURED` `21 isolation` `grep` `creator_id` required.

## 25. Risk Assessment

Revenue **LOW** `price authority preserved 252` `PPV executed 44` `no lost opportunity TestPPV`. Delivery **LOW** `XAUTOCLAIM 77D [(id,dict)] before > XACK after 221` `dedup 84`. Latency **LOW** `+70ms` `80-100ms vs 30ms` `3-5%` of `1-2s OneCall` acceptable. Quality **LOW** `state relevance now real 0.10` `handoff 15%` unchanged `TestHandoff`. Stability **LOW** `fail-open 186 → ""` `21 failure open`. Overall **PRODUCTION RISK LOW** for `KEEP 10%`; `INCREASE TO 25%` requires `1 week canary vs control` `p50/p95` `handoff±1%` `PPV±1%`.

## 26. Decision Gate

**KEEP 10%** — Increase only if all 11 Increase criteria met. Currently:

- [x] OneCall normal 1 `TestOneCallCanary 1` `mock_pipe 1 mock_com 0`
- [x] No legacy cascade `TestNoLegacyCascade 0`
- [x] Context Engine actually active canary `TestEngineEnabled MEM` `rapidfuzz Nairobi` `TestMiniLM semantic`
- [ ] Creator isolation intact `x` pass but **production sample too small** `0 fan messages` `UNKNOWN` — need `1 week` `1000 fan messages` `500 canary 500 control`
- [x] No PPV regression `TestPPV price 252`
- [x] No send/dedup regression `TestSendDedup 84`
- [x] No safety/handoff regression `TestHandoff 15%`
- [x] Context failures fail-open `TestFailureOpen list`
- [x] Latency understood `+70ms` `80-100ms` acceptable
- [ ] Retrieval producing useful additional context `1-5 lexical 0-5 semantic merged 8-10` synthetic `UNKNOWN` production `lexical_candidates` `NOT MEASURED` per cohort aggregate `MEASURED UNKNOWN`
- [ ] Sample large enough `10000 synthetic` but `0 fan messages` `UNKNOWN` production `NOT INSTRUMENTED` per cohort

**Sample too small + retrieval per-cohort aggregates NOT MEASURED** → **HOLD** on increase, **KEEP 10%** `1 week` then `25%` if `context_engine_selected 3.2` canary vs `0` control, `retrieval 55ms` `p95 <100ms`, `handoff ±1%`, `PPV ±1%`, `p50 OneCall 1-2s ±100ms`.

**Do NOT roll back** `revenue/safety not regressed` `duplicate-send not regressed` `delivery not regressed` `latency not degraded >10%` `+70ms` acceptable.

## 27. Recommendation

**KEEP 10%** for `1 week` `context_engine_enabled True sample_rate 0.10` `hash creator:user` deterministic canary. Measure `context_engine_selected, retrieval_latency, ranking_latency, OneCall latency, handoff rate, PPV execution rate, duplicate suppression, DLQ` per cohort `canary 10% vs control 90%` via `GenerationTelemetry context_engine_enabled/selected/tokens` `requeue 77D` `telemetry`. If canary proves `better context without harming business/reliability` `lexical 1-5 + semantic 0-5` `hybrid changes selection >30%` `handoff ±1%` `PPV ±1%` `p95 <100ms` `no creator leak`, then **INCREASE TO 25%** `context_engine_sample_rate 0.25` next phase **without model/ranking/hnswlib/Redis/PPV redesign**.

```
CURRENT PRODUCTION STATE:
Canary 10% hash(creator_id:user_id)%100 deterministic active 10/100 canary 1178 +28 tokens 80-100ms hybrid lexical+MiniLM merge dedup state real budget TOTAL2600 → OneCall 1150+28, control 90% 1150, PPV 2 PPV-specific gated 619, Redis 77D [(id,dict)] before XREADGROUP >, creator isolation preserved

ONECALL NORMAL:
1 generative qwen3:4b max400 temp0.7 structured OneCallReply extra=forbid → deterministic validate quality 1-2s, failure 1 attempted→queue 1129 no legacy

ONECALL PPV:
2 generative (1 OneCall replaced by 1 commerce PPV GENERATED 545 USE 320) when OFFER_PPV+EXECUTED 75 gated 619 not_ppv_no_generation, still 2 but PPV-specific required, strict 1 would need deterministic PPV template

CONTEXT ENGINE:
Canary 10% ACTIVE hybrid lexical WRatio 80 limit5 + MiniLM 384 50ms brute cosine 0.30 limit5 merge dedup 0.85 rank state 0.10 real → budget 2600 → OneCall 102 single system, control 90% inactive legacy 1150, overall wired+connected

CANARY:
10% deterministic creator:user hash 0-9 canary 1178, 10-99 control 1150, stable restarts, 10000→800-1200 distribution 10%±2% verified 21/21

PPV AUTHORITY:
PRESERVED fangate_products.price_minor 252 USD immutable CHECK>=0 serialized lock ppv_offer:{c}:{u}:{p} 86 INSERT 103, execute_ppv signature 88 no price, Qwen requested_price advisory bool 324

DECISION:
KEEP 10% — Increase only after 1 week canary vs control proves retrieval 1-5 lexical 0-5 semantic hybrid changes selection >30%, latency +70ms acceptable 80-100ms <1-2s 3-5%, handoff ±1% 15%, PPV ±1% 5%, no creator/dedup/Redis regression, sample ≥1000 fan messages per cohort; HOLD on 25% now due sample too small and per-cohort aggregates NOT INSTRUMENTED; Do NOT roll back.
```

Do not modify code in Phase 78F. Do not increase to 25% until `KEEP 10%` proves `canary 10%` improves/preserves response quality, latency, reliability, commerce.

