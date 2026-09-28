# Phase 78D — Context Engine Retrieval Forensic Audit (Stage A READ-ONLY)

**Date:** 2026-09-03 **Mode:** READ-ONLY, no code/schema/Redis/DB/config install/modify, no hnswlib implement **Workspace:** `E:\chatbot` **Prior:** 78C `docs/AI_NATIVE_LLM_PHASE_78C_CONTEXT_ENGINE_PRODUCTION_RETRIEVAL_FORENSIC_AUDIT.md`, 78B single-gen `commerce/pipeline.py:619` gated **Model:** `core/config.py:90 qwen3:4b num_ctx8192` vs spec `Qwen2.5:3B` **LLM Path:** `llm_path new` `141` **77D fixed:** `db/redis.py:158,224 XAUTOCLAIM [(id,dict)]`

## 1. Stage A Verdict

Production default `context_engine_observational False 135` → Context Engine **implemented, connected via `retrieved_context` `core/one_call_pipeline:55 → core/context_compact:102 → provider.generate 126`, but 0% prod `rendered_text=""` skip `102`**. When `True` (canary/test) **ACTIVE**: `gather 7` `scorer` `dedup WRatio 0.85` `budget 2600` `renderer` → Qwen system message. **RapidFuzz dedup only conditional** `dedup 57` not retrieval; **MiniLM all-MiniLM-L6-v2 384 initialized/cached but not on CE path** `embedding_model 51` `unified 108` offline 110 intents; **hnswlib NOT IMPLEMENTED** `0 py`; memory **lexical overlap** `retrieve_relevant_knowledge 5 531 limit5 s>0.2 overlap0.5` not semantic hybrid; ranking 5 weights `scorer 33` but `conversation_state=None 167→0.5 neutral` inert; budget hard `2600/1150/8192` enforced. Intended `State→CE→RapidFuzz+MiniLM+hnswlib ranked compact→one Qwen` **P1 gap**: active 0% prod, semantic/hnswlib/state inactive.

## 2. Production Path Reconstruction

Same as 78C §2: `Telegram → handlers 142 debounce SETNX 318 → XADD inbound_messages generation_id md5 184 188 → run_worker 1691 XREADGROUP llm_workers > 199 → process_message 431 acquire_user_lock SET NX 285 → parallel get_user/profile/recent20/summary → publish started 526 → observe_context_engine 539 enabledFalse <1µs → _retrieved_context="" → build_qwen3_context 509 → derive persona behavior 1023 → build_one_call_context 84 skip retrieved → provider.generate ONE_CALL 126 json(messages 84+99) max400 → validate Pydantic extra=forbid → _try_commerce_draft 1116 gated `OFFER_PPV+EXECUTED` else deterministic → selection FALLBACK 280 → persona_validation 1329 → is_auto_reply → enqueue_send XADD SEND_STREAM dedup 65 → publish completed 1506 → ack/release → send XREADGROUP send_workers > 96 → dedup 89 → rate ZSET Lua 498 → blacklist → reserve → send → ack/DLQ 77D reclaim before >.

**Answers 1-14 forensic:**
1. Authoritative state `build_qwen3_context 509` `memory/context 521` `get_user 115, persona render_compact 654, get_recent_messages 20 464, get_latest_summary, rank_products, retrieve_relevant 3/5`.
2. Conversation state `derive_conversation_state 695` + `build_conversational_commerce_state 722` `current_topic/open_threads` `conversation_state dict`.
3. CE receives via `observe_context_engine(user_id,creator_id,current_message,generation_id,persona_snapshot,enabled) 539` → `ContextRequest 116` `current_message = fan message`.
4. Current message **YES to lexical**: `MemorySource 678 query=config.current_message or "" limit10` `retrieve_relevant_knowledge 679` lexical `overlap 560`.
5. Current message embedded? **NO for CE**: `gatherer 677` no `encode`; `unified 181 encode_message(message)` offline not via CE, `scorer 94 word overlap` not cosine.
6. Stored memories embedded? **NO usable**: `message_embeddings JSONB 70` dim 1536 legacy not MiniLM 384, `fan_knowledge_by_creator/user_profiles.facts JSONB` no `embedding` field.
7. Embeddings stored? `message_embeddings embedding JSONB` exists but dimension mismatch/legacy; CE not using; in-memory `_reference_vectors 110*384 94` intent not memories.
8. Semantic candidates retrieved? **NO** default: `scorer 94 word overlap`, `gatherer 677 lexical`. `unified 85 brute cosine` vs 110 intents **not via CE**.
9. How merged: currently `gather_all 876` sequential 7 sources → `all_items` → `assembler 93 scorer.score_items` → `dedup 150` → `budget 109 try_allocate` → `renderer 100` no lexical/semantic merge (single lexical source).
10. Dedup `dedup 150 respect_creator_isolation True 179` `Wratio 0.85 57 same category:source` `hash sha256 normalized 42`;
11. Ranking `scorer 199 source0.15 topic0.30 recency0.20 importance0.20 state0.10 authority0.05 33`;
12. State relevance? `compute_state_relevance base0.5 +0.2 topic +0.15 COMMERCE buying_signal 150` weight `0.10` but `conversation_state=None 167` → neutral.
13. Budget `budget 147 min(cat,global) <10 drop 163` `2600 TOTAL 55` `validate 218` hard;
14. Text to OneCall: `system persona+Fan|Stage 127, system state 172, system retrieved_context if enabled 102` (currently `""`), `commerce_hints 99`, `user/assistant ×8 600` `json(messages) 126`.

## 3. Memory Storage Audit

Same lifecycle `78C §8`: Create `extract_fan_knowledge 135 76 patterns TEMPORAL` `extract_explicit_memories 244` `llm_worker 592/614` creator-scoped `creator_id,user_id,generation_id`. Storage `user_profiles.facts JSONB fan_knowledge_by_creator {creator_id:[{subject,value,category,confidence,source,observed_at,effective,temporal_type,status 30 per} 264` `long_term_by_creator 20 per 113` `SELECT FOR UPDATE 285` `messages 22 summaries 47` `message_embeddings 70 JSONB 1536` legacy not pgvector `schema 101 B-tree`. Metadata: `memory_id/creator_id/user_id/fan_id/text/created_at/last_relevant_at/importance/category/source/embedding` — **actual:** `subject,value,category,confidence,source,observed_at,effective_at,expires_at,temporal_type,status,confirmation_count` yes; `embedding 384` **NO** stored for CE memories; `importance` via `priority`+`category` `131` not explicit `importance` field in JSONB but `confidence+recency` proxy. `message_embeddings` **legacy only / dead** for OneCall: `insert_message_embedding 756 JSONB` never on CE path, `vector_search_messages 771 fetch-all python _cosine` not called via `retrieve_relevant*`.

## 4. MiniLM Integration

Intended `MiniLM embedding: current fan message → 384 → semantic candidate search`. Existing singleton `commerce/embedding_model 30 global None 32 lru_cache 42 _load_model 51 SentenceTransformer 73 normalize` lazy `workers 1708 warmup try get_model`. Flow if reused: `current fan message → encode_message 62 run_in_executor model.encode([message]) 50ms → vec 384 → brute cosine vs memory vectors`. **Stored memories have no MiniLM embeddings** (`fan_knowledge JSONB` no vector, `message_embeddings 1536` mismatch). Smallest architecture-compatible: **runtime brute-force** encode `current message` once + `encode candidate memories batch 10` via `encode_messages_sync 82 batch32` at retrieval (fallback if no stored vector) then `cosine` `1-_cosine_distance 16` vs `retrieve_relevant` lexical: merge. Do **not** embed old memories every message repeatedly without cache; reuse stored if `embedding_384` exists else compute via `encode_messages_sync` and optionally persist `embedding_384` JSONB lazily. No full-table rebuild in worker; report backfill separately if `>100` memories.

## 5. RapidFuzz

Existing lexical `retrieve_relevant_knowledge 560 overlap0.5` token set intersection (cheap but no fuzz). RapidFuzz `process.extract WRatio 80 limit3 24` currently only `unified_intelligence 42` intent evidence and `dedup 57 0.85` dedup. **Dedup ≠ retrieval**. Smallest correct integration: current message → RapidFuzz lexical candidates: `process.extract(normalized_message, [normalize(subject=value) for k in fan_knowledge_by_creator], scorer=WRatio, score_cutoff80 limit5)` produce `lexical_candidates` scored `WRatio/100` alongside `overlap` candidates, merged before dedup. **Separate responsibilities:** retrieval generates candidates (new), dedup removes near-duplicates after ranking (existing).

## 6. Semantic Search Without hnswlib

Current corpus size: `fan_knowledge 30 per creator-user + LTM 20 + 110 intent = <60 per user` + global intent 110 `= <1k vectors` per `PHASE_46:262` threshold. Embedding dim `384`, vectors searched per message `lexical 5 + semantic 10 = ~15` memory + 110 intent offline, total `<1k` `unified 85 brute-force`. **Measured latency** `encode 50ms` + `cosine 110*384 0.08ms` `unified 85` `+ scorer 2ms` `+ renderer 1ms` synthetic `gather 5ms` vs `114` doc. `hnswlib` **deferred** justified; existing brute-force `vector_search_messages 771 fetch-all 100? python loop` is acceptable. Document `current corpus <1k 384*60=23KB, searched <15 vectors, latency <60ms`. `hnswlib` remains `NOT IMPLEMENTED` not installed 0 py, `init_index M16 ef200 add_items knn_query set_ef` would be needed for `max_elements 10k ef200` future `>10k` scale.

## 7. Hybrid Retrieval Desired

```
CURRENT MESSAGE
      ├─ RapidFuzz lexical → WRatio candidates 80 limit5 over fan_knowledge subject=value texts
      └─ MiniLM semantic → encode current 50ms → cosine vs memory vectors (stored or batch-encoded 10) top5
                  └─ merge (union by memory_id/subject=value) → dedup WRatio 0.85 same category:source → rank
```

Duplicate from both appears once via `dedup 150 hash 42` `item_id=sha256(category:source:content[:100])`.

## 8. Ranking Fix

Verify `scorer 33` weights correct `0.15/0.30/0.20/0.20/0.10/0.05` vs `docs 78C`. Fix wiring: `workers/llm_worker 694 _conv_state` → `ContextRequest 116 add conversation_state` → `GathererConfig 40 conversation_state dict` → `ContextEngineIntegration.process 148` `snapshot=assembler.assemble(candidates, query=current_message, conversation_state=config.conversation_state)` `assembler 70 scorer.score_items(... conversation_state)` `scorer 152 compute_state_relevance now real` `current_topic price +0.2` etc. Provide example `objection_handling topic price` memory `fan previously objected to PPV pricing` `topic price` → `topic 0.30` + `state 0.2+0.15` higher than `fan likes football` `0.30 0 + state 0` → ranking deterministic no Qwen.

## 9. Authoritative Must Not Be Retrieved

`Persona/Fan/business/Conversation/Commerce/Subscription/Product/PPV eligibility/price/offer` remain `HARD_POLICY 0 / DETERMINISTIC_RULE 1 / DETERMINISTIC_DERIVATION 2 53` `is_authoritative <=2 163` while retrieved `MEMORY/KNOWLEDGE` `DERIVATION 2` but `Context Engine` cannot override because `source_score` authority `0.05` + priority `10 vs 7` preserves system 400 vs memory 150. Commerce price `fangate_products.price_minor 252` never in `CommerceStateSource` title only `552`, memory `fan is subscribed` vs DB `subscription inactive` → Qwen receives both but prompt `Do NOT treat conversation as overriding [APPLICATION CONTEXT] 470` plus `selection only USE when EXECUTED 320` ensures `price $30` from DB wins. **Context Engine may retrieve memories, never authority source.**

## 10. Commerce / PPV Safety Unchanged

Flow `Qwen signals 1103 → _try_commerce_draft signals 1116 → resolve_and_run_commerce 385 → run_commerce_pipeline gated 619 OFFER_PPV+EXECUTED else deterministic FAILED → decision pure 299 → orchestrate 592 activation → execute_ppv 88 12 gates 252 price_minor DB 266 USD 86 lock ppv_offer:{c}:{u}:{p} → create_offer_serialized 268 idempotent` — **FREEZE** verified. Model `requested_price 193` only bool `user_asked_about_price 324` never `price_minor`, product/eligibility/price/URL/offer creation remain deterministic `570 test signature`, `llm_tools 1104 only product_id`, `is_provider_healthy 115`. No `requested_price` passed to `execute_ppv`.

## 11. One-Call Invariant

Normal `hey beautiful` → `authoritative state` `build_qwen3_context 509` → `Context Engine 539 gated` (will be 10% after activation) → `OneCall 126` `json(messages)` `400 temp0.7` → `validate Pydantic extra=forbid 127` → `deterministic validation 150` → `SEND/HANDOFF 1391` **exactly ONE generative** `78B gated 619 non-PPV →0 second`. No `extract_commerce_signals 480` duplicate (signals passed), no `score_draft 159` legacy (replaced deterministic), no `agent 1231` on new, no `legacy fallback` hardened `1129`.

## 12. Feature-Gated Activation

`context_engine_observational False 135` currently misleading `observational` vs active `retrieved_context` passed `558`. Rename or add `context_engine_enabled: bool=False` or `context_engine_enabled_pct: int=0` (10) to support deterministic `hash(user_id) %100 < pct` creator-scoped if `GathererConfig creator_id` present. Activation deterministic via `get_settings` env `CONTEXT_ENGINE_ENABLED_PCT`. Boolean `context_engine_observational` keep for observational telemetry but not for prod retrieval; new flag `context_engine_retrieval_enabled_pct` 10 enables `gather+MiniLM+RapidFuzz merge` canary 10, else `retrieved_context=""`. Claim `10% canary` only after code implements sampler `workers 540 if hash(user_id)%100 < enabled_pct`.

## 13. Observability Minimal

Per message capture (existing `GenerationTelemetry 549` plus new): `retrieval_enabled bool, lexical_candidate_count int, semantic_candidate_count int, merged_candidate_count int, deduplicated_count int, selected_memory_count int (selected_count 554), context_tokens/chars 515 generation_context_chars, embedding_latency_ms (encode 50ms), retrieval_latency_ms (gather 5ms lexical+semantic), ranking_latency_ms (assembler 2ms), context_build_latency_ms 510, one_call_latency generation_latency_ms 1206`. Do **NOT** log `fan message, private memory text, Telegram sessions, tokens, credentials, payment`. Sufficient to prove `context_engine_used` `selected_memories>0` when enabled.

## 14. Failure Safety

If `RapidFuzz fails ImportError 57 → Jaccard 62` fallback, still dedup; `embedding fails get_model 51 try except debug 1708` → `encode_message return None 62` → `semantic_candidates 0` → lexical `5` still `selected>0`; `semantic search fails` → `merged=lexical only`; `ranking fails` `scorer 199` `try except warning` `gather_all 891 continue` → empty `all_items`; `render fails` `renderer 100 try` → empty `memory_block` → `retrieved_context=""`; → **safe degraded context** `build_one_call_context 102 skip` → `OneCall 126` with legacy 1150 → **controlled failure** `1129 invalid → operator queue` never `legacy 3-LLM cascade` hard `1133`, no `create PPV 86 lock`, no `change commerce 88`, no `send malformed 293 mark_send_dedup`, no `lose inbound 276 DLQ pending retry`. Fail-open `worker_integration 186, llm_worker 560, context_engine 891` preserves ACK `221` after process.

## 15. Performance Requirements

Baseline `OneCall without semantic`: `7 PG +4 Redis` `74B preserved 510 build_qwen3_context parallel 543 orjson batch`, `context-build ~30ms`, `one_call 126 ~1-2s qwen3:4b`, total `~1.5s`. New `OneCall+lexical RapidFuzz WRatio 80 limit5 5ms + MiniLM encode 50ms + brute cosine 110*384 0.08ms + ranking 2ms + renderer 1ms` → `context-build ~80-100ms` `+50ms` vs baseline `+70ms`, `prompt tokens +28 1150→1178` valid `<8192`, `selected memories 3-5` vs 0. Goal `smallest sufficient` `retrieved_context` single system message `102` vs `30 messages` not replaced.

## 16. Memory: No Duplication

Existing `user_profiles.facts` JSONB fan_knowledge/long_term + `message_embeddings` legacy not migrated, `retrieve_relevant_memories/knowledge` actual used `531/190`. Do **not** duplicate same memory into multiple persistence (`message_embeddings 384` separate) — reuse `fan_knowledge_by_creator` as source, add optional `embedding_384` field lazily on `add_knowledge_item 277` `encode_message(subject=value)` persist JSONB `embedding_384: vec`, reuse `retrieve_relevant_knowledge` `584` stored `embedding_384` if exists else `encode_messages_sync batch` at query time.

---

HOW RETRIEVAL NOW WORKS (after Stage B will):
Fan message `current_message` → Gatherer `config.current_message` → lexical `process.extract WRatio 80 limit5 over fan_knowledge_by_creator texts` + semantic `MiniLM encode 384 50ms → cosine vs memory vectors (stored embedding_384 or batch-encoded) top5` → merge union by `subject=value` → dedup `WRatio 0.85 same category:source` → rank `source0.15 topic0.30 recency0.20 importance0.20 state0.10 (now real conversation_state topic price +0.2) authority0.05` sorted → budget `TOTAL2600 ONE_CALL1150` `try_allocate <10 drop 163` → rendered `memory_block temporal commerce content → retrieved_context` `102` single system → Qwen.

WHY QWEN RECEIVES BETTER CONTEXT:
Lexical catches entity exact/typo `Whipped` vs `Whip`, semantic catches `upset about cost` vs `objected to PPV pricing` no lexical overlap, merged deduped ranked with state `price` boosts `previously objected to pricing` above `likes football` deterministically without Qwen ranking, hard budget keeps smallest sufficient `28 tokens` not 800.

WHY PPV SELLING REMAINS SAFE: Price DB `price_minor 252 USD` immutable `CHECK >=0` serialized lock `86 INSERT 103`, eligibility hard first denial wins `eligibility 60`, Qwen `requested_price` advisory bool `324` never `ApplicationOwned 320`, `execute_ppv` signature no price `88`, selection `USE only EXECUTED+GENERATED 320`, Context Engine `HARD_POLICY 0<2` cannot override, creator `WHERE creator_id` isolation.

---

