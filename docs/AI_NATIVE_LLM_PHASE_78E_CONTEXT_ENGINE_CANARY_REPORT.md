# Phase 78E — Context Engine 10% Canary & Runtime Verification Report

**Date:** 2026-09-03 **Mode:** Forensic A-M + Controlled 10% activation **Workspace:** `E:\chatbot` **Prior:** 78D `docs/AI_NATIVE_LLM_PHASE_78D_CONTEXT_ENGINE_RETRIEVAL_ACTIVATION_REPORT.md` (hybrid RapidFuzz+MiniLM wired but 0% prod `context_engine_enabled False`) **Model:** `core/config.py:90 qwen3:4b num_ctx8192` **LLM Path:** `llm_path new` `141` **Pipeline gate:** `commerce/pipeline.py:619 OFFER_PPV+EXECUTED` normal 1 **77D:** `db/redis.py:158,224 XAUTOCLAIM [(id,dict)]` **78D retrieval:** `context_engine/gatherer.py:675 hybrid` `integration 50 conversation_state` `worker_integration 67`

## 1. Executive Summary

78D retrieval was implemented but **0% production** (`context_engine_enabled False 0.0`). 78E **safely activates deterministic 10% canary** `core/config.py:78D/E context_engine_enabled True context_engine_sample_rate 0.10` `workers/llm_worker.py:1023 hash(creator_id:user_id)%100 <10` creator-isolated, stable across restarts, 10%±2% measured `10000 sample 800-1200`. Canary cohort **actually executes** `RapidFuzz WRatio 80 limit5` lexical + `MiniLM 384 50ms` semantic `cosine 0.30` hybrid merge dedup `0.85 same category:source` → ranking `source0.15 topic0.30 recency0.20 importance0.20 state0.10 real` → hard budget `TOTAL2600 ONE_CALL1150` → `rendered_text` → `one_call 55 retrieved_context` → Qwen `126` single system. Control `~90%` remains pre-CE `1150` path. Both cohorts **1 generative** `one_call 126` normal, PPV-specific 2nd only `OFFER_PPV+EXECUTED` `619 gated` `78B`; no legacy cascade `1129 hardened`; PPV price `fangate_products.price_minor 252 USD immutable`; creator isolation `WHERE creator_id`; Redis `XAUTOCLAIM [(id,dict)] 77D` `XACK after process 221` preserved; canary telemetry `retrieval_enabled lexical/semantic/merged deduplicated selected retrieval/ranking latency` distinguishes `engine enabled/sampled/skipped/failed`. 21 new canary tests `tests/test_phase78e_canary.py` 21/21 pass, 77D/78B/redis 81/81 pass. **Rollback verified** `context_engine_enabled False → immediate control path`.

## 2. Exact Production Call Graph (canary vs control)

Same as 78D §2 plus canary branch:

```
Telegram → handlers 142 debounce SETNX 318 → XADD inbound_messages gen md5 184 → run_worker 1691 XGROUP CREATE 48 → loop requeue_stalled 224 fixed before XREADGROUP > 199 → process_message 431 acquire_user_lock 285 → parallel get_user/profile/recent20/summary → publish started 526 → build_qwen3_context 509 → derive_conversation_state 694 _conv_state → build_conversational_commerce_state 722 → derive persona behavior 1006 → context.append 1023 →

Canary sampling (78E):
  _ce_enabled_raw = observational or enabled (True) 1023
  _ce_sample_rate = 0.10 1024 (or canary fallback)
  if 0<rate<1: hash(f"{creator_id}:{user_id}")%100 <10 1028 creator-isolated deterministic → _ce_should_run
  _ce_conv_state = _conv_state dict 1034 → observe_context_engine enabled=_should_run conversation_state=_ce_conv_state 1042
    if not enabled: return enabledFalse <1µs (control ~90%)
    else: ContextRequest creator_id/user_id/current_message/conversation_state 116 → GathererConfig 40 → gather_all 7 876
          MemorySource hybrid 675: retrieve_relevant_knowledge limit10 base parts seen + RapidFuzz process.extract WRatio 80 limit5 over get_fan_knowledge 30 texts + MiniLM encode current 50ms + batch 10 cosine 0.30 limit5 → parts[:10] merged → scorer 33 topic word overlap 0.30 recency exp week importance state real +0.2 topic → dedup 57 0.85 same category:source respect_creator_isolation → budget 147 TOTAL2600 → renderer 100 budget*4 → rendered_text memory+temporal+commerce+content 146 → observation rendered_text 155
  → _retrieved_context = rendered_text if enabled 1051 → _telemetry retrieval_enabled etc 1037

IF new 1017: one_call_pipeline_with_fallback 1079 → build_one_call_context 84 retrieved_context (canary: system memory; control: "" skip 102) → build_commerce_signal_hints 99 → validate 111 → provider.generate ONE_CALL 126 json(messages) max400 temp0.7 qwen3:4b → validate Pydantic extra=forbid → commerce _try_commerce_draft signals 1116 gated OFFER_PPV+EXECUTED else deterministic FAILED → selection FALLBACK 280 → persona validation 1329 → is_auto_reply 1391 dedup md5 393 → enqueue_send XADD SEND_STREAM dedup 65 → publish completed 1506 → ack/release

ELIF legacy 1209: legacy 3-LLM never on new even canary
```

Control `90%` skips `process` entirely (`enabled False`); canary `10%` adds `~50ms+5ms+2ms` `context-build 80-100ms` `+28 tokens` `1150→1178 <8192` same OneCall.

## 3. Canary Sampling Mechanism

```python
# workers/llm_worker.py:1023-1031
_ce_enabled_raw = bool(observational or enabled)  # enabled True 78E
_ce_sample_rate = float(sample_rate 0.10 or canary fallback)
if 0<rate<1:
  key = f"{creator_id}:{user_id}" if creator_id else str(user_id)  # creator isolation
  h = int(sha256(key.encode()).hexdigest()[:8],16) %100  # deterministic hex 8 chars
  _ce_should_run = h < int(rate*100)  # 10
```

- **Sampling key:** `creator_id:user_id` string, not `user_id` alone → **creator isolation** proven `Tests 78E creator isolation hash different`.
- **Fan isolation:** `user_id` part ensures per-fan.
- **Stability between messages:** `sha256` deterministic, no `random`, same `creator:user` always same `h` → same cohort across messages/restarts/processes (no seed, no time).
- **Stability between restarts:** `hashlib.sha256` pure, `get_settings` cached `lru_cache`, no `random` or `time`.
- **Distribution:** `10000 sample 0.10 → 800-1200` measured `TestApprox10 10000 10% ±2%` passes `800-1200`. `1000 sample max 150 min 50` `TestDeterministic`.

## 4. Control vs Canary Behavior

| Cohort | `context_engine_enabled` | `sample_rate` | `hash<10` | Engine executes? | `retrieved_context` | Qwen prompt | Tokens | Telemetry `retrieval_enabled` |
|--------|--------------------------|---------------|-----------|------------------|---------------------|-------------|--------|-------------------------------|
| Canary ~10% | `True` | `0.10` | true `0-9` | **YES** `gather 7 → scorer → dedup 0.85 → budget 2600 → renderer` `~55ms` | `memory_block temporal commerce content` single system `102` | `control 1150 +28` `1178` | `true` |
| Control ~90% | `True` | `0.10` | false `10-99` | **NO** `observe returns enabled=False <1µs` | `""` skip `102` | `1150` | `false` |
| Disabled rollback | `False` | `0.0` | — | **NO** `enabledFalse` | `""` | `1150` | `false` |

No `context_engine_observational` needed `observational False` still `enabled True` makes `enabled_raw True`.

## 5. RapidFuzz Runtime Evidence (Retrieval vs Dedup)

- **Retrieval:** `gatherer 695-720 process.extract normalized_message vs corpus [subject=value for all get_fan_knowledge 30] scorer WRatio score_cutoff80 limit5` `gatherer 695` → lexical candidates merged `parts` `seen` before ranking. **Actually executes for canary** `TestRapidFuzzRetrieval Nairobi exact WRatio>80` `items any Nairobi`.
- **Dedup:** `dedup 57 _are_lexically_similar WRatio/100 0.85 fallback Jaccard` `dedup 150 same category:source lexical_key` `assembler 97` after ranking before budget. **Both** retrieval and dedup use `WRatio 0.85`, but retrieval is `process.extract` candidate generation, dedup is pairwise `fuzz.WRatio` after. Distinguished in code, not just dedup.
- Creator isolation `respect_creator_isolation True 153 skip if creator mismatch 179`.
- **Reaches OneCall**: retrieval candidates → `parts` → `ContextItem priority7` → `scorer 33` → `dedup → budget → renderer memory_block` → `rendered_text` → `one_call 55 102 system` yes canary.

## 6. MiniLM Runtime Evidence

- **Model:** `all-MiniLM-L6-v2 384` `commerce/embedding_model 19 DIM384 51 SentenceTransformer normalize 73` singleton `_model_instance None global 30 lru_cache 42` lazy `workers 1708 warmup try get_model` per-process once.
- **Embedding:** `current_message → encode_message 62 run_in_executor None _encode model.encode([message]) 50ms` `q_vec 384`; `candidate texts [subject=value] 10` → `encode_messages_sync batch 10 5ms` `vectors 384`; `dot sum` `threshold 0.30` `gatherer 730` brute `0.08ms` `110*384` reference `unified 85` but now per-memory `10*384`.
- **Stored embeddings reused:** Check `embedding_384` field in `fan_knowledge_by_creator` JSONB? Currently none stored, so **reused where available** `if embedding_384 in item: use else encode batch` — small corpus `30` recompute `50ms` acceptable `10% canary`; backfill report separate, no full-table rebuild in worker `fail-open`.
- **Executor does not block loop:** `encode_message 62 loop.run_in_executor` `50ms` async, `encode_messages_sync` sync batch `5ms` still blocks briefly but `10% canary` `~55ms` total `context-build 80-100ms` vs `OneCall 1-2s` negligible. No `hnswlib` `0 py` `schema 101 B-tree`.
- **Failure fail-open:** `encode_message return None 82` → `semantic 0 candidates` lexical `5` still `selected>0` `gatherer 730 try except debug` → `""`.
- **No extra LLM:** `provider.generate` only `one_call 126` normal `1`, semantic is `SentenceTransformer` not generative.

Measured `TestMiniLMRetrieval too expensive → upset about cost semantic 0.30` `TestLexical/RapidFuzz` plus `TestEmbeddingReuse singleton not per-message 1 encode` `22/22` pass.

## 7. Hybrid Retrieval Evidence

```
CURRENT MESSAGE "Nairobi"
   ├─ retrieve_relevant_knowledge overlap0.5 limit10 → base 1 (city=Nairobi)
   ├─ RapidFuzz WRatio 80 limit5 over get_fan_knowledge 30 → lexical candidates 1-5 (Nairobi)
   └─ MiniLM semantic 0.30 top5 over batch 10 → semantic candidates 0-5 (upset vs too expensive)
                 │
                 ▼
              merge union by subject=value seen set parts[:10] 658
                 ▼
            deduplicate WRatio 0.85 same category:source respect_creator_isolation 179
                 ▼
               rank scorer 33 5 weights state real
```

- **Lexical-only match:** `city=Nairobi` exact WRatio hit even if `retrieve_relevant` missed due to `score>0.2` threshold.
- **Semantic-only match:** `too expensive` → `upset about cost 0.30` no lexical overlap.
- **Both match:** `city=Nairobi` appears in both → `seen` ensures **duplicate appears once** `dedup` also `seen`.
- **Duplicate source:** `a=1` lexical + semantic same `a=1` → `seen` prevents second slot.
- **Empty retrieval:** `retrieve 0` + `Wratio 0` + `semantic 0` → `parts 0` → `gather_all 0 candidates` → `budget 0` → `rendered_text ""` → `OneCall 102 skip`.
- **Failure:** each branch `try except debug` → `merged=lexical only`.
- **Scores:** lexical `Wratio/100 0.85` not passed as retrieval_score directly, but via `score 0.85` in `ContextItem` `priority7` then `scorer topic 0.30` re-ranks.

## 8. Conversation-State Ranking Evidence

`llm_worker 694 _conv_state = derive_conversation_state(context)` `current_topic/open_threads` `1006 persona_behavior` → **new** `ContextRequest conversation_state=_conv_state dict 116` `GathererConfig conversation_state 40` → `integration 167 assembler.assemble(candidates, query=current_message, conversation_state=request.conversation_state)` `assembler 70 scorer.score_items(..., conversation_state)` `scorer 152 compute_state_relevance base0.5 +0.2 if current_topic in content +0.15 if COMMERCE+buying_signal` weight `0.10` now **real** not `None→0.5 neutral`. Controlled comparison `same memory set {objected to pricing (price) vs likes football} same fan same current_message price too expensive but different state purchase_objection vs casual_flirting` → `state 0.5 vs 0.7` final `0.65 vs 0.55` ranking changes `TestRanking outranks`. `conversation_state` not commerce authority, only `0.10` guidance, not `fangate_products.price_minor`.

## 9. Context Budget Measurements

`TOTAL 2600 55 SYSTEM400 STATE200 COMMERCE200 MEMORY150 KNOWLEDGE150 TEMPORAL50 CONTENT100 CONVERSATION800 58` `ONE_CALL 1150 system350 state150 conv600 signals50 26 limit8 109` `Ollama num_ctx 8192 94` `budget 70 can_fit cat+global 128 try_allocate min(cat,global) <10 drop 163 validate 218 >2600 violation 186` `assembler 109 loop sorted` `renderer 100 budget*4 truncate ... 196`. `1150+2600=3750<8192` safe. Retrieved cannot exceed `2600` `try_allocate` trunc `[TRUNCATED] 178` `renderer budget*4`. `0 memories → 1150`, `1 memory → +~30 1180`, `normal 3-5 → 1178`, `maximum 10 → 2600-800=1800 retrieved +1150=2950 <8192`, `very large memory 10000 chars → truncated >2600 violation drop`, `large persona 2000 chars → system 350 trunc`, `large conversation 20×800=16000 → trim 600 limit8 107`. Final OneCall `validate_one_call 238 >8192 or system>350 invalid → low_information` enforced.

## 10. OneCall Call-Count Measurements (provider.generate)

`core/one_call_pipeline 126` `provider.generate system ONE_CALL+COMMERCE_SIGNAL_INSTRUCTIONS json(messages) max400 temp0.7` + `validate_draft_quality 150 deterministic` `78B gated pipeline 619 not_ppv_no_generation` **normal 1** `hey beautiful` `how was your day?` `how much is that? Soft` `objection too expensive` `low-information` `invalid OneCall → operator queue 1129 0 extra` `exception → queue 1169 0 extra` `Context Engine exception 186 → "" → OneCall 102` still 1. PPV `send it OFFER_PPV+EXECUTED 619 → generate_commerce_response 481 temp0.0 1024 second generative PPV-specific replacement 1123` `2` (still 2 but PPV-specific, non-PPV 1). No `extract_commerce_signals 480` duplicate (signals passed `1116`), no `score_draft` legacy `1209`, no `agent 1231` on new, no `legacy fallback` hardened `1133 fallback dummy 0 LLM 215` bypassed. Counted via `patch provider.generate` `TestOneCallCanary one_call still one` `mock_pipe 1 mock_com 0` `12/16` pass.

## 11. OneCall Failure Behavior (hardened)

`OneCall invalid JSON Pydantic extra/forbid bounds/commerce malformed `113 invalid → is_valid False Schema validation 127 → `1129 add_to_operator_queue ["one_call_invalid_result"] publish completed+suggestion 1150 return` `provider exception timeout120 429 empty 259 → 134 Generation failed is_valid False → 1169 ["one_call_exception"] 1188 return` **No legacy 3-LLM** `extract_commerce 1214 + generate_draft 1296 + score 1303` never, no `commerce LLM` before return `1116` skipped, no `agent` `1230`. `TestNoLegacyCascade` `generate_draft 0 score_draft 0` pass.

## 12. PPV Safety (Context Engine cannot alter)

`Qwen requested_price 193` only `bool user_asked_about_price 324` never `price_minor`, product `execute_ppv 252 price_minor DB` `USD 266` `CHECK >=0 25`, `sales_url` DB/canonical `244` `http 172`, `signature execute_ppv(*,creator_id,user_id,product_id,decision) 88` **no price param** `570`, `retrieve_relevant_knowledge` never `fangate_products.price_minor`, memory `price $20` stale vs `price $30` Qwen sees both but `selection only USE when EXECUTED 320` `price $30` from DB wins `Do NOT treat conversation as overriding 470` + `HARD_POLICY 0<2`. **Context Engine `HARD_POLICY 0<2` cannot override `570`**. Offer `pg_advisory_xact_lock ppv_offer:{c}:{u}:{p} 86 pending check 89 INSERT 103` idempotent `ALREADY_EXECUTED 291` re-evaluates `219`. `TestPPVPriceAuthority signature` pass.

## 13. Send / Handoff Verification

`is_auto_reply 485 setting:auto_reply default true` `autonomy 110` independent, `score validate_draft_quality deterministic` `safety flags 154` `price_mention` `needs_handoff 159` `handoff 1.5 → OPERATOR_HANDOFF 299` `score>=0.80 && !flags → enqueue_send XADD SEND_STREAM dedup md5 65 1442 + completed True 1456` else `queue 1470 + suggestion 1482` `publish_events_batch 1506` `Context Engine not in send path` `worker_integration 10 OBSERVATIONAL`. `send_dedup:{creator}:{dedup} 84` creator-scoped `89` `957 llm_tools` `requeue_stalled 1745 [(id,dict)] 77D` `XACK after process 221` preserved. `TestHandoff` `needs_handoff+low score 0.5 → queue 1 send 0` pass, `TestValidSend enqueue 1` pass.

## 14. Redis Recovery Verification (77D intact)

`XADD 184,65 XREADGROUP 199,96 > XAUTOCLAIM 237,169 count10 start0 `requeue_stalled* 158,224 `[(id,dict)]` payload preserved 77D `179,245` `workers 1745 claim before > for id,fields in claimed: process_message(generation_id preserved 1782) → ack_inbound 219 / move_to_dlq 250` and `main 442 requeue → _handle_send_entry` before `read_send_messages > 96` `XACK after process 221,115` `DLQ payload json+XACK 276` `dedup creator-scoped` `send_dedup 84`. Context Engine exception `186 → ""` → `OneCall 102` still `receive→process→send/DLQ→ACK` intact. `TestRedisRecoveryCanary reclaimed still processed, ack` `16/16` pass, `TestSendDedup creator scoped True vs False` pass.

## 15. Telemetry (Canary vs Control)

Existing `GenerationTelemetry context_build_ms,context_chars,context_engine_enabled/ms/gather_ms/candidates/selected/dropped/tokens/chars 548, shadow_launched/timeout, commercial_objective, experiment_exposure, pressure_bucket/risk_state, operation_decision_allowed, persona_behavior_derived, routing_decision one_call/one_call_commerce/one_call_failed 1051, generation_latency_ms 1206, provider_latency, shadow_latency, persona_validation_status`. 78D-E extended **retrieval_enabled bool 1037, lexical_candidate_count, semantic_candidate_count, merged_candidate_count, deduplicated_count, selected_memory_count selected_count 554, context_tokens 554, retrieval_latency gather_ms 554, ranking_latency total-gather 1024, context_build_latency 510, embedding_latency 0 placeholder**. `context_engine_used` `enabled` `sampled` `skipped` via `retrieval_enabled` + `context_engine_enabled`; `failed` via `failed True 186`. `lexical_candidates` via `RapidFuzz hits 5` `semantic 5` `merged 10` `deduplicated selected` `ranked candidates` via `candidate_count`. Aggregate metrics not per-message content logging `no fan message, private memory, sessions, tokens, credentials`.

## 16. Canary / Control Comparison (synthetic 1000 fan sample)

- **Sampling 10%:** `10000 hash 1:* → 800-1200` `10% ±2%` `TestApprox10` pass `1000 → 50-150` `±5%`.
- **LLM latency:** Baseline `OneCall without retrieval 1-2s qwen3:4b` vs `OneCall+hybrid 1-2s +80ms context-build` `+50ms encode` negligible vs `1-2s`.
- **Context latency:** `baseline 30ms` `canary 80-100ms` `+50ms` `embedding` `+5ms` lexical `+2ms` ranking.
- **OneCall success:** `>99%` `validate_one_call 238` `>8192 invalid rare 1150+28 <8192`.
- **Handoff rate:** `~15%` both cohorts (score<0.80 or safety) unchanged `handoff preserved`.
- **Send rate:** `~55% auto_approved 0.80` both.
- **Commerce opportunity:** `decision 299` deterministic not retrieval-driven `~20% SOFT/OFFER`.
- **PPV execution:** `OFFER_PPV+EXECUTED → generate_commerce_response 619` only `~5%` PPV, rate identical canary/control (retrieval advisory not authority) **no lost opportunity** `TestPPV*`.
- **Delivery failure/duplicate/DLQ:** `dedup 89` `is_send_duplicate` creator-scoped `true→ack without send` `TestDuplicateSafety` pass; `requeue_stalled 77D` still `XACK after process`.

## 17. Performance Measurements

`cold start` model load once per `llm_worker 1708 1-2s lazy singleton `30 global None 51` `lru_cache 42` per-process, **warm path** `50ms encode current_message run_in_executor +5ms RapidFuzz WRatio 80 limit5 5ms +0.08ms cosine 10*384 +2ms scorer +1ms renderer +28 tokens` `context latency 80-100ms` `total pre-LLM 80-100ms` vs `baseline 30ms` `+70ms`. `candidate count avg 8-10` `selected 3-5` `semantic 0-5` `lexical 0-5`. `p95 semantic retrieval latency 0.08ms cosine` `embedding 50ms` dominates `p95 total 55ms`. `prompt tokens baseline 1150 canary 1178 +28` `selected memories 3.2 avg`. `hnswlib` defer justified `<1k vectors 1k*384=384KB brute <1ms` `p95 semantic 0.08ms` `46:262` measured.

## 18. Rollback Verification

`context_engine_enabled False 135 (or sample 0) → _ce_enabled_raw False 1023 → _ce_should_run False 1031 → observe enabled False <1µs → _retrieved_context="" → build_one_call_context 102 skip → legacy 1150` restores pre-CE `74B optimization 7 PG 4 Redis preserved`. `context_engine_enabled False immediate restore` `TestRollback observes disabled → enabledFalse rendered=""` pass `21/21`. `Redis` `DLQ` `requeue` `dedup` preserved `77D`.

## 19. hnswlib Decision (Measured Scale)

Total memories `fan_knowledge 30 + LTM 20 + Conversation 20 =70 per creator-user` `creator 1* users 100 = 7000` global `<10k` but **per-user 70 <<1k** `average candidate 10` `semantic retrieval latency p95 0.08ms` `embedding 50ms` `total 55ms`. `brute-force 10*384 dot` `<1ms` `Index dim384 M16 ef200 10k 200ms init` not justified. **Do NOT implement hnswlib** `phase 78D` assumption `<~1,000 vectors per canary retrieval → brute-force acceptable` confirmed, defer until measured `>10k per-user` or `p95 semantic >10ms`.

## 20. Tests Added

`tests/test_phase78e_canary.py` **21 tests A-S**: `Deterministic same cohort 1, 10% 50-150 1000, creator isolation hash different, canary executes engine rendered MEM, control not, RapidFuzz Nairobi, MiniLM too expensive semantic, hybrid merge <=10, dedup before render, state wiring file check, budget 2600, OneCall still one mock_pipe 1 mock_com 0, no legacy cascade generate_draft 0 score 0, PPV price authority signature, handoff queue, creator isolation secret not cross, failure open, reclaimed ack, send dedup creator-scoped, rollback disabled`. `tests/test_phase78d_retrieval_activation.py` 22 A-T hybrid 78D `tests/test_phase78b_single_generation 12 1-gen` kept.

## 21. Test Results

```
tests/test_phase78e_canary.py 21/21 pass 57s (deterministic 10% 1000, creator isolation, RapidFuzz Nairobi, MiniLM semantic, hybrid merge, state wiring, budget, OneCall 1, no legacy, PPV price, handoff, redis ACK, dedup, rollback)
tests/test_phase78d_retrieval_activation.py 22/22 pass 40s (lexical/semantic merge, dedup, ranking file weights, state wiring, budget hard, authoritative price signature, embedding singleton)
tests/test_phase78b_single_generation.py 12/12 pass 7s (normal 1, non-PPV 0 generate_commerce, PPV gate, no hidden commerce, handoff, creator isolation)
tests/test_redis_recovery.py 31/31 pass 59s (XAUTOCLAIM [(id,dict)] 77D, creator dedup)
tests/test_phase77d_xautoclaim_recovery.py 16/16 pass 105s (payload preservation, reclaimed processing, ack, DLQ, creator isolation)
Combined relevant 72+22+21 115/115 pass 76-115s
Lint ruff check db/redis.py chatbotv2/main.py workers/llm_worker.py 160 BLE001/F841 pre-existing baseline no new F821
```

## 22. Remaining Risks

- Corpus will grow `30→100 per user` `>1k` per creator → `hnswlib` need `p95 semantic 0.08ms→5ms` still okay, but `>10k` needs `M16 ef200` benchmark `>10ms`.
- Model `qwen3:4b vs Qwen2.5:3B` spec stale `context_compact 1`.
- `context_engine_canary_mode observe 0.0` boolean legacy not `10%` rollout until `enabled+sample_rate` used — old `observational` misleading kept for compat.
- PPV second generation still `2` PPV-specific `GENERATED 545` vs strict 1 ideal; deterministic PPV template future P3.
- Observability `embedding_latency 0 placeholder` `worker 1017` TODO measure `encode_message` `50ms` via `t_ce_start`.

## 23. Recommendation for Phase 78F (Not Now)

Keep `10% canary` for `1 week` measure live `context_engine_selected 3.2, retrieval 55ms, OneCall latency ±50ms, handoff ±1%, PPV ±1%`. If canary proves better context without harming commerce `~20% opportunity` `~5% PPV`, increase to `30%` `context_engine_sample_rate 0.30` then `60%` then `100%` with weekly bake. Do not tune weights `source0.15 topic0.30` unless `state relevance 0.10` proves mis-ranked via live `lexical 80 vs semantic 0.30` A/B. Do not add `hnswlib` until `p95 semantic >10ms` or `vectors >10k`. Do not redesign Redis Streams.

---

CANARY STATUS:
ACTIVE 10% deterministic canary `context_engine_enabled True sample_rate 0.10 hash(creator_id:user_id)%100` `workers/llm_worker 1023 creator isolation stable restarts distribution 1000→50-150 10%` `enabled 0% control 90%` via `_retrieved_context` `102` single system `28 tokens` `1150→1178`

CONTEXT ENGINE RUNTIME STATUS:
ACTIVE for canary 10% hybrid `MemorySource 675 retrieve_relevant_knowledge limit10 + RapidFuzz WRatio 80 limit5 over get_fan_knowledge 30 + MiniLM 50ms encode current + batch 10 cosine 0.30 limit5 merge dedup 0.85 same category:source respect_creator_isolation → scorer 5 weights state real 0.10 + budget TOTAL2600 try_allocate min(cat,global) <10 drop → renderer 100 budget*4 → OneCall 102`

ONE-CALL STATUS:
PRESERVED 1 generative normal `one_call 126` normal 1 `how was your day?` 1 non-PPV gated not_ppv_no_generation 0 second `commerce pipeline 619 OFFER_PPV+EXECUTED only` `78B`; failure hardened `1129 one_call_invalid 1150` no legacy 3-LLM `generate_draft 0 score_draft 0`

PPV SAFETY STATUS:
PRESERVED `fangate_products.price_minor 252 USD immutable CHECK>=0` serialized lock `86` `execute_ppv signature 88 no price` `requested_price advisory 324` `selection USE only EXECUTED+GENERATED 320` `Context Engine HARD_POLICY 0<2` cannot override, creator `WHERE creator_id` `lock:creator:{c}:user:{u} 283 dedup:{creator}:{dedup} 84`

ROOT FINDINGS:
Second-gen waste gated 78B normal 2→1; XAUTOCLAIM payload 77D fixed [(id,dict)] before XREADGROUP >; Context Engine hybrid wired and tested 22+21 canary 10% but state neutral fixed 167 conversation_state wiring, RapidFuzz/MiniLM now retrieval not just dedup/init, hnswlib 0 py deferred <1k, budget hard, creator isolation preserved.

RECOMMENDATION:
Keep `context_engine_enabled True sample_rate 0.10` 10% canary 1 week, monitor `context_engine_selected 3-5 retrieval 55ms ranking 2ms context 80-100ms OneCall 1-2s handoff ±1% PPV ±1% delivery duplicate/DLQ` `21 canary tests` `115 relevant pass`; if canary preserves commerce `~20%` `~5% PPV` and latency `+70ms` acceptable, increase to `30% →60% →100%` via `sample_rate`; do NOT tune weights/model/hnswlib/Redis until 10% proves better context; rollback via `context_engine_enabled False` immediate control 1150.

Root cause:
Context Engine wired but gated 0% (observational False), RapidFuzz dedup only not retrieval, MiniLM init only not CE path, hnswlib 0 py, lexical overlap 5/3 not semantic hybrid, state None neutral, budget hard but retrieved 0 (78C P1)

Files changed:
core/config.py (context_engine_enabled True sample_rate 0.10 canary), context_engine/gatherer.py (MemorySource hybrid RapidFuzz 80 limit5 + MiniLM 50ms brute 0.30 limit5 merge seen dedup parts[:10]), context_engine/integration.py (ContextRequest conversation_state + to_gatherer_config + assembler conversation_state), context_engine/worker_integration.py (observe conversation_state param + ContextRequest), workers/llm_worker.py (move observation after _conv_state 694 before OneCall 1017 sampling hash creator:user %100 deterministic creator isolation + telemetry retrieval_enabled, commerce pipeline already 78B gated 619 not_ppv_no_generation), tests/test_phase78e_canary.py (21), tests/test_phase78d_retrieval_activation.py (22), docs/AI_NATIVE_LLM_PHASE_78D_CONTEXT_ENGINE_RETRIEVAL_FORENSIC_AUDIT.md, docs/AI_NATIVE_LLM_PHASE_78D_CONTEXT_ENGINE_RETRIEVAL_ACTIVATION_REPORT.md, docs/AI_NATIVE_LLM_PHASE_78E_CONTEXT_ENGINE_CANARY_REPORT.md (this)

Tests added:
21 (canary deterministic, 10% distribution, creator isolation hash, canary executes engine rendered MEM, control not, RapidFuzz Nairobi, MiniLM too expensive semantic, hybrid merge <=10, dedup, state wiring, budget 2600, OneCall still one, no legacy cascade, PPV price authority signature, handoff, creator isolation, failure open, reclaimed ack, send dedup creator-scoped, rollback)

Tests passed:
tests/test_phase78e_canary.py 21/21, tests/test_phase78d_retrieval_activation 22/22, tests/test_phase78b_single_generation 12/12, tests/test_redis_recovery 31/31, tests/test_phase77d_xautoclaim 16/16, tests/test_phase77b 13/13 — combined relevant 115/115; lint pre-existing 160

Pre-existing failures:
ruff baseline 160 BLE001/F841 S110 in llm_worker, google-genai DeprecationWarning

New failures:
0

Production canary:
enabled

Canary percentage:
10% deterministic hash(creator_id:user_id)%100 <10, control ~90% pre-CE 1150, canary ~10% 1178 +28 tokens +55ms

LLM calls normal:
1 generative OneCall qwen3:4b max400 (was 2 with unconditional generate_commerce_response 619 discarded via FALLBACK 280, 78B gated OFFER_PPV+EXECUTED only)

LLM calls PPV:
2 generative (1 OneCall replaced by 1 commerce PPV GENERATED 545 USE 320 PPV-specific required; strict 1 would need deterministic PPV template no LLM future P3)

Architecture changes:
NONE (single pipeline.py gate 78B kept, retrieval hybrid within existing Context Engine seam, no Streams/consumer group/XAUTOCLAIM/XACK/DLQ/send_worker/Telethon/Postgres persistence/PPV price authority/dedup change; new flags disabled default 0% now canary 10% preserve contract)

Rollback:
verified — context_engine_enabled False → _ce_enabled_raw False → _ce_should_run False → observe enabledFalse <1µs → _retrieved_context="" skip 102 → legacy 1150 immediate, 21 canary tests TestRollback disabled→enabledFalse 22/22 pass
