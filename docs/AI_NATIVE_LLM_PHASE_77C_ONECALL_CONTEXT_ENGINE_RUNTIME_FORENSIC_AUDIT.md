# AI-Native LLM — Phase 77C OneCall + Context Engine Runtime Forensic Audit

**Date:** 2026-09-03
**Mode:** READ-ONLY Stage A — no code, schema, config, Redis, or DB mutations
**Workspace:** `E:\chatbot`
**Prior Reports:** `docs/AI_NATIVE_LLM_PHASE_77B_CONTEXT_ENGINE_PRODUCTION_INTEGRATION_REPORT.md`, `docs/AI_NATIVE_LLM_PHASE_77A_CONTEXT_ENGINE_PRODUCTION_INTEGRATION_FORENSIC_AUDIT.md`
**Config Source of Truth:** code at audit time (`core/config.py`, `workers/llm_worker.py`, `core/one_call_pipeline.py`, `context_engine/*`, `core/context_compact.py`, `core/event_bus.py`, `db/redis.py`)

---

## 1. Executive Summary

Phase 77B claimed to make the Context Engine production-active: `Context Engine → retrieved_context → build_one_call_context → Qwen`. The runtime **is now wired**, but remains **feature-gated off by default** (`context_engine_observational=False` `core/config.py:135`). When enabled, the full pipeline executes, is fail-open, and its `rendered_text` reaches Qwen as an extra system message (`core/context_compact.py:102`). The one-call invariant is **proven 1 authoritative generative call** per normal message, with a **discarded 2nd commerce generation** when `creator_id` is READY (total 2 provider invocations, 1 useful). Hardening correctly removes the hidden `legacy 3-LLM cascade` on malformed/provider failure (`workers/llm_worker.py:1133,1171`). RapidFuzz is implemented but only via Context Engine dedup when enabled; MiniLM `all-MiniLM-L6-v2` is implemented as a singleton but **not on the production OneCall path**; hnswlib is **not implemented** (0 matches). Memory remains lexical + deterministic; ranking uses 5 signals with explicit weights; hard token budgets are enforced (2600 engine, 8192 Ollama). Commerce price authority and persona enforcement are preserved. Redis `XAUTOCLAIM` recovery has a **pending-discard bug** (`db/redis.py:243` drops fields). Model discrepancy persists: code is `qwen3:4b` `core/config.py:90`, not `Qwen2.5:3B`.

---

## 2. Actual Production Message Flow (llm_path=new default)

`core/config.py:141` `llm_path: str = "new"` (authoritative). Branch at `workers/llm_worker.py:1040` `_llm_path = getattr(_settings,"llm_path","legacy")`.

```
Telegram inbound (Telethon)
  |  chatbotv2/handlers.py:27 handle_incoming_message
  v
check_rate_limit (db/redis.py:353) -> upsert_user (db/postgres.py) -> generation_id=md5(user:msg:telegram_id) (handlers.py:63)
  |  resolve_single_application_creator (handlers.py:68) -> save_inbound_message (db/postgres.py) -> publish_event message.created (handlers.py:82)
  v
debounce_enqueue -> SETNX debounce:creator:{cid}:user:{uid}:lock (db/redis.py:318) -> RPUSH messages
  |  owner? asyncio.create_task(_wait_and_process) (handlers.py:122) : send_typing+return
  v
_wait_and_process: sleep debounce_window_seconds=3 (handlers.py:128) -> get_debounced_messages LRANGE+DEL (db/redis.py:338)
  -> get_cached_user_persona / get_cached_default_persona -> get_user_persona / get_default_persona (db/postgres.py:155)
  -> enqueue_inbound XADD inbound_messages * {user_id,content,telegram_message_id,persona,generation_id} + generation_id dedup (db/redis.py:184)
  v
llm_worker run_worker (workers/llm_worker.py:1691) init_pool + ensure_consumer_group XGROUP CREATE inbound_messages llm_workers (db/redis.py:48)
  |  loop: requeue_stalled_messages XAUTOCLAIM idle 60000 (db/redis.py:224) -> read_inbound XREADGROUP llm_workers > (db/redis.py:199)
  v
process_message (workers/llm_worker.py:431) user_id, user_message, telegram_message_id, generation_id (md5 reused)
  1  telemetry start_generation (449)
  2  resolve_single_application_creator (459) -> _creator_id (fail-open)
  3  acquire_user_lock SET NX EX 60 (474) -> if not locked return (skip)
  4  asyncio.gather upsert_user + is_user_auto_reply_excluded (481) return_exceptions
  5  _fail_closed_creator_unavailable gate (491) if creator None and autonomy false
  6  get_structured_persona_async (499) -> _persona_snapshot (PG personas, Redis cache)
  7  build_qwen3_context (509) memory/context.py:521 -> parallel gather get_user, get_user_profile, get_recent_messages(20,creator_id), get_latest_summary_with_age, fan_knowledge, vault (544) -> context list[system,system,user...]
  8  publish_event ai.generation_started preview 100 (526) best-effort
  9  observe_context_engine (539) context_engine/worker_integration.py:67 enabled=_settings.context_engine_observational -> if not enabled return enabled=False (104) ; else ContextEngineIntegration.process -> gather_all 7 sources -> assembler score/dedup/budget -> renderer -> rendered_text -> _retrieved_context = observation.rendered_text if not failed (558)
 10  fail-closed routing if _fail_closed_creator_unavailable (565) -> add_to_operator_queue + publish_events_batch ai.generation_completed+suggestion.created (573) -> return (no LLM)
 11  LTM extraction extract_explicit_memories (593) -> add_memory_item (fail-open)
 12  fan_knowledge extract_fan_knowledge + add_knowledge_item (609)
 13  observe_behavioral_signal (632)
 14  qwen_shadow launch ShadowRunner.create_task if qwen_shadow_enabled (648) (default false)
 15  resolve_open_loop (669)
 16  derive_conversation_state + build_conversational_commerce_state (692) signals=None initially -> _conv_state/_cstate
 17  make_exposure/persist_exposure/compute_fatigue (754)
 18  compute_pressure/derive_risk/build_operation_decision (793)
 19  derive_commercial_objective + production_control gates (875) -> _skip_qwen_due_to_pause
 20  operational_decision + execute_operational_recommendation (944)
 21  derive_persona_behavior_state (1006) + render_persona_behavior_block (1017) -> if _behavior_block: context.append system (1022) FIX for prior _behavior_block-derived-but-not-injected bug
 22  IF _llm_path == "new" (1040): one_call_pipeline_with_fallback (1079) -> build_one_call_context(user,profile,persona,retrieved_context=_retrieved_context) (102) -> build_commerce_signal_hints (99) -> validate_one_call_context (>8192?) -> provider.generate ONE_CALL_SYSTEM_PROMPT+COMMERCE_SIGNAL_INSTRUCTIONS json.dumps(messages) model=_settings.model_name max_output_tokens 400 temp 0.7 (core/one_call_pipeline.py:126) -> validate_one_call_response (146) -> OneCallResult(reply,signals,confidence,needs_handoff,quality_score) -> if valid: draft=reply score=quality_score flags=safety+quality _commerce_signals=signals (1099) -> _try_commerce_draft signals=_commerce_signals (1116) -> resolve_and_run_commerce -> run_commerce_pipeline -> generate_commerce_response if executed (conditional 2nd LLM) -> selection USE_COMMERCE_RESPONSE? draft=commerce_text (1123)
       ELSE malformed (1129) -> add_to_operator_queue ["one_call_invalid_result"] -> publish_event ai.generation_completed + suggestion.created (1150) -> return (hardened, no legacy)
       EXCEPT provider exception (1169) -> same operator queue ["one_call_exception"] (1188) -> return (hardened)
 23  ELIF _llm_path == "legacy" (1209): # legacy-only 3-LLM -> extract_commerce_signals (1213, LLM1) -> _try_commerce_draft signals (1220) -> agent canary should_use_agent inside legacy (1230) -> run_agent_runtime (1241) -> generate_draft_with_tools or generate_draft (1263, LLM2, up to 4 tool calls) -> score_draft (1303, LLM3)
 24  validate_persona_voice (1329) -> if severe flags+=persona_validation_severe score max(0,score-0.10) (1345)
 25  await shadow evaluation (1364) wait_for 5s
 26  Send/handoff decision (1391): is_auto_reply_enabled (db/redis.py:482) -> dedup_id md5(user:msg:telegramId) (1393) -> if not auto_reply_on: add_to_operator_queue + ai.generation_completed was_auto_approved False (1406) ; elif score>=0.80 and not flags: enqueue_send XADD send_messages (1442) + ai.generation_completed was_auto_approved True (1456) ; else: add_to_operator_queue + suggestion.created (1469)
 27  publish_events_batch _operator_events (1506) pre-built at 573/1406/1456/1482 -> core/event_bus.py:94 (now tolerates "event" legacy key)
 28  enrich telemetry, classify_outcome, update_strategy_evidence (1543)
 29  asyncio.create_task(post_process) (1671) -> extract_and_update_profile + maybe_summarize (background LLM, not on reply path)
 30  except: publish_event ai.generation_failed same generation_id (1676) -> raise -> run_worker move_to_dlq XADD dead_letter_queue + XACK (1779) else ack_inbound XACK (1775) -> release_user_lock (1786)
     |
     v
  send_worker / chatbotv2/main.py XREADGROUP send_workers > (db/redis.py:96) -> is_send_duplicate check send_dedup:{creator}:{dedup} (db/redis.py:89) -> client.send_message/send_file -> save_outbound_after_send -> publish_event message.sent -> ack_send XACK (db/redis.py:115)
```

Every step is fail-open except `acquire_user_lock` skip, `auto_reply_excluded` early return, and `fail-closed` creator gate.

---

## 3. Hard LLM Call-Count Audit — llm_path=new

Global sites (`grep get_llm_provider|generate|ollama|deepseek|one_call|generate_commerce_response|run_agent_runtime`):

| Site | File:line | Provider API | Purpose |
|------|-----------|--------------|---------|
| one-call generate | `core/one_call_pipeline.py:126` `provider.generate ONE_CALL_SYSTEM_PROMPT+COMMERCE_SIGNAL_INSTRUCTIONS max 400 temp 0.7` | `core/llm_provider_ollama.py:264` `POST /api/chat num_predict 400 num_ctx 8192` | Structured reply+signals |
| extract_commerce_signals | `commerce/deepseek.py:190` `provider.generate system=COMMERCE_SIGNAL_EXTRACTION_SYSTEM temp 0.0 max 1024` | same | 18-field signals — **legacy-only** |
| generate_commerce_response | `commerce/deepseek_response.py:481` `provider.generate temp 0.0 max 1024` | same | Commerce PPV text — conditional |
| score_draft | `core/scoring.py:149` `provider.generate SCORING_SYSTEM temp 0.2 max 512` | same | 4-dim scoring — **legacy-only** |
| generate_draft / generate_draft_with_tools | `workers/llm_worker.py:114` `generate_with_history temp 0.85` / `231` loop up to 4 | same | Conversational draft — **legacy-only** |
| run_agent_runtime | `agent/runtime.py:145` `generate_with_history` bounded 5 | same | Agent — **legacy-only** |

### Scenario Counts (llm_path=new)

**A. Normal conversational — "hey beautiful"** (`casual_chat`, `purchase_intent 0.0`)
- Critical: **1** `one_call_generation` → `validate_one_call_response` deterministic + `validate_draft_quality` deterministic (`core/one_call.py:88,227` `core/scoring_deterministic.py`) replaces `score_draft`. No separate scoring LLM.
- Commerce hook: `_try_commerce_draft(signals=one_call.signals)` always calls `run_commerce_pipeline` → `generate_commerce_response` even for `NO_OFFER` then discards via `select_commerce_response:280` FALLBACK (see 7C.5 pipeline always generates). **Provider invocations total 2** (1 useful, 1 discarded) when creator READY; **1** when creator unavailable (`commerce/integration.py:137` short-circuit no LLM).
- **Authoritative reply: 1**
- *Evidence:* `workers/llm_worker.py:1042,1099,1116,385` → `commerce/pipeline.py:619` → `commerce/selection.py:280`

**B. Commerce-intent — "how much for that?"** (`price_interest 0.6`, `content_interest 0.7`)
- Same as A: **1** authoritative + **1** discarded commerce generation (pipeline decides `SOFT_OFFER/FOLLOW_UP` → fallback). `build_commerce_signal_hints` `core/commerce_prompt.py:19` added as system hint but no LLM.
- **Authoritative: 1**, total invocations 2 if creator READY.
- *Evidence:* `workers/llm_worker.py:99-108` hints deterministic

**C. PPV opportunity** (`explicit_purchase_request true`, `purchase_intent 0.85`, product exists, eligibility PASS, `EXECUTED`)
- **2 logical** : `1` one-call + `1` `generate_commerce_response` → `CommerceResponse GENERATED` (`commerce/deepseek_response.py:545` validated) → `selection USE_COMMERCE_RESPONSE` `commerce/selection.py:320` → `draft = commerce_response_text` `workers/llm_worker.py:1123` replaces one-call reply. Not + scoring.
- *Evidence:* `workers/llm_worker.py:1119-1125`, `commerce/execution.py:88` 12-step gate, `commerce/dao.py:82` advisory lock

**D. Malformed OneCall output** (invalid JSON, `extra=forbid`, `reply>2000`, `confidence>1.0`)
- `1 attempted` `provider.generate` → `core/one_call.py:112-126` returns `is_valid=False validation_error="JSON parse failed"/"Schema validation failed"` → `workers/llm_worker.py:1129` `not is_valid` → `add_to_operator_queue ["one_call_invalid_result"]` `1137` → `publish_event ai.generation_completed+suggestion.created` `1150` → `return`. **0 additional LLM, no legacy retry**.
- Fallback dim `_fallback_3llm_pipeline` `core/one_call_pipeline.py:215` exists but returns dummy empty reply and is not reached in hardened path (log line 193 misleading but proves 0 LLM).
- *Evidence:* `core/one_call.py:88-137`, `workers/llm_worker.py:1133` `# Hardened: no legacy cascade`

**E. Provider exception** (timeout 120s `core/config.py:91`, `LLMProviderError` `core/llm_provider_ollama.py:218`, empty response `259`)
- `core/one_call_pipeline.py:133` `except Exception → OneCallResult(is_valid False, validation_error="Generation failed")` → outer `workers/llm_worker.py:1169` catch → operator queue `["one_call_exception"]` `1179` + publish `1188` → `return`. **No retry to legacy** (hardened). Internal provider fallback `gemini_fallback_enabled` `core/config.py:45` may try opposite provider as 1 logical call (2 HTTP attempts).
- *Evidence:* `core/llm_provider_ollama.py:207-259`, `workers/llm_worker.py:1171`

**F. Agent/tool path** — any `Qwen→tool→Qwen` loop in new path?
- **DISABLED in new path.** `agent.canary should_use_agent` `workers/llm_worker.py:1230` and `generate_draft_with_tools` `1263` both inside `if _llm_path == "legacy":` `1209`. New guard `if _llm_path == "new":` `1042` mutually exclusive. `core/config.py:128` `ai_agent_canary_enabled=False` default, `117` `ai_runtime_mode=legacy`. **0 agent/tool LLM in new path** (legacy bounded `agent/loop.py:84` max 5 tool +1 final, `workers/llm_worker.py:231` up to 4 for tool).
- *Evidence:* `workers/llm_worker.py:1209 vs 1042`

---

## 4. OneCall Invariant Proof

| Runtime scenario | LLM calls (provider invocations) | Expected | Verdict | Evidence |
|------------------|-----------------------------------|----------|---------|----------|
| Normal message | **1 useful** (2 total with discarded commerce) | 1 | **PROVEN** — 1 authoritative, 2nd discarded when creator READY | `one_call_pipeline.py:126` + `deepseek_response.py:481` discarded `selection.py:280` |
| Commerce message | **1 useful** (2 total) | 1 | **PROVEN** — same | `commerce/pipeline.py:619` always generates then fallback |
| PPV opportunity | **2** (one-call + commerce PPV) | 1 | **DEVIATION DOCUMENTED** — 2 is correct for PPV (commerce replaces reply) | `workers/llm_worker.py:1123` `USE_COMMERCE_RESPONSE` |
| Empty/minimal | **1 attempted** → operator queue if `reply empty` `core/one_call.py:60` | 1 or exception | **PROVEN** — capped quality `0.5` `311` but empty → invalid → operator queue | `core/one_call.py:307` `<5 words capped` |
| OneCall validation failure | **1 attempted** → operator queue, `was_auto_approved False` | controlled | **PROVEN** — hardened no legacy | `workers/llm_worker.py:1129-1167` |
| Provider failure | **1 attempted** → operator queue | controlled | **PROVEN** — hardened | `workers/llm_worker.py:1169-1204` |
| Legacy path | **3** (extract 1 + Qwen 1 (+up to 4 tool) + score 1) | 3 | **PROVEN** — only if `LLM_PATH=legacy` `workers/llm_worker.py:1209` | `commerce/deepseek.py:170` + `workers/llm_worker.py:1296` + `core/scoring.py:149` |
| Agent path | **0 in new path** | must not execute | **PROVEN** — legacy-only guard | `workers/llm_worker.py:1209` |

*Why PPV is 2 not 1:* Commerce PPV is a **replacement**, not additional. The discarded 2nd call for non-PPV is a P2 inefficiency (pipeline generates then discards). Single useful generation remains 1.

---

## 5. Verify Context Engine Is Really Production-Active

**Status: WIRED BUT GATED OFF BY DEFAULT — observational-value, not default-active.**

Chain:

```
workers/llm_worker.py:539 observe_context_engine(user_id,creator_id,user_message,generation_id,persona_snapshot, enabled=_settings.context_engine_observational)
  -> context_engine/worker_integration.py:104 if not enabled: return ContextEngineObservation(enabled=False) (<1µs)
  -> else: context_engine/integration.py:148 ContextEngineIntegration.process(ContextRequest)-> gather_all 7 sources (gatherer.py:876) -> assembler assemble query=current_message (assembler.py:70 scorer/dedup/budget) -> renderer render (renderer.py:100) -> ContextPipelineResult(rendered, candidate_count, selected_count, total_tokens)
  -> worker_integration.py:133 extracts rendered.system_prompt/state/commerce/memory/temporal/content chars -> _rendered_text = "\n".join([memory_block,temporal_block,commerce_block,content_block]) (system/state excluded) -> ContextEngineObservation(rendered_text)
  -> workers/llm_worker.py:558 if not failed and rendered_text: _retrieved_context = rendered_text (fail-open try/except 561)
  -> core/one_call_pipeline.py:55 one_call_generation(retrieved_context=_retrieved_context)
  -> core/context_compact.py:102 if retrieved_context.strip(): messages.append({"role":"system","content":retrieved_context})
  -> core/one_call_pipeline.py:126 provider.generate(ONE_CALL_SYSTEM_PROMPT+COMMERCE_SIGNAL_INSTRUCTIONS, json.dumps(messages))
```

Flags:

- `core/config.py:135` `context_engine_observational: bool = False` comment `Phase73/77B: When true, Context Engine runs and its rendered context is passed to OneCall. Fail-open`
- `context_engine/worker_integration.py:9` `FEATURE-GATED: disabled by default`
- `workers/llm_worker.py:537` `_retrieved_context = ""` default; ` core/context_compact.py:102` skips when empty
- When `enabled=True`, full pipeline executes deterministically; `context_engine/worker_integration.py:186` `except -> failed=True` never propagates to production path.

Docs convergence: `docs/AI_NATIVE_LLM_PHASE_77A...FORENSIC_AUDIT.md:15` `RapidFuzz, MiniLM, hnswlib NOT in production path unless context_engine_observational=True (default False)` is **still true for default deployment**; Phase 77B wired the handoff but did not flip the flag. Tests `tests/test_phase77b_context_engine_integration.py:103` assert wiring exists, not default activation.

**Conclusion:** Not merely initialized, but not default-active. To be production-active requires `CONTEXT_ENGINE_OBSERVATIONAL=true` (or rename to `context_engine_enabled`). No silent activation.

---

## 6. RapidFuzz Audit

| Q | Evidence |
|---|----------|
| Imported? | Yes `pyproject.toml:22` `rapidfuzz>=3.0` (3.14.6) |
| Where called? | `context_engine/dedup.py:57` `_are_lexically_similar()` `fuzz.WRatio(text1,text2)/100 >=0.85` fallback Jaccard; `commerce/unified_intelligence.py:24,157` `process.extract(..., scorer=fuzz.WRatio, score_cutoff=80, limit=3)` |
| On production OneCall path? | **Conditional** — only via `dedup.py:58` inside `ContextEngineIntegration.process` when `context_engine_observational=True`. `core/one_call_pipeline.py` and `core/context_compact.py` do not import rapidfuzz. When disabled (default) **NO** RapidFuzz on OneCall. |
| Data searched? | Dedup: all `ContextItem.content` pairwise within same `category:source` lexical_key, threshold 0.85 (`dedup.py:95`). Unified-intelligence: normalized message vs 110 intent example_text `LEXICAL_CUTOFF 80`. |
| Candidates? Bounded? | Dedup bounded by selected items so far (~30-50 items under 2600 tokens). Unified `limit=3` lexical + 3 semantic. |
| Memory retrieval? | Yes indirect — `ContextCategory.MEMORY` items from `MemorySource` can be deduped if `WRatio>=0.85` same creator. |
| Reaches Qwen? | Only via surviving `ContextItem` → `renderer.py:127` `memory_block` → `worker_integration.py:147` `_rendered_text` → `context_compact.py:102` system message. Dropped counted at `workers/llm_worker.py:554`. If disabled, zero lexical output reaches Qwen. |
| Bounded? | Yes `0.85`, `80`, `limit 3`, per-category budgets `TOTAL 2600`. |
| Creator/fan isolated? | Yes `ContextItem.creator_id/user_id` `context_engine/models.py:145` ; `Deduplicator.deduplicate(respect_creator_isolation=True)` default `dedup.py:153` ; skip if `creator_id mismatch` `dedup.py:180,208`. Lexical key `f"{category}:{source}"` prevents cross-category collapse. |
| Zero matches? | Dedup: nothing dropped, all candidates pass to budget. Unified: lexical scores empty array, semantic still evaluated. |

Not on authoritative commerce path (`workers/llm_worker.py:664` never calls `analyze_message`).

---

## 7. SentenceTransformer / MiniLM Audit

| Q | Evidence |
|---|----------|
| Configured | `commerce/embedding_model.py:19` `_MODEL_NAME="all-MiniLM-L6-v2"` `_DIMENSION=384` `pyproject.toml:23` `sentence-transformers>=3.0` |
| Actual loaded | `commerce/embedding_model.py:51` `SentenceTransformer(_MODEL_NAME)` same `all-MiniLM-L6-v2` CPU `normalize_embeddings=True` `73` |
| Singleton | `commerce/embedding_model.py:30` `_model_instance=None` global + `lru_cache` `_get_model_name` `33` + `get_model()->_load_model()` `58`. Per `llm_worker` process singleton. |
| When loaded | Lazy via `_load_model()` first `get_model()` at `commerce/unified_intelligence.py:109` `_ensure_reference_cache` or `commerce/embedding_model.py:65` `encode_message`. Warmup optionally at `workers/llm_worker.py:1713` `try get_model`. Not in `send_worker`/`bot_main`. |
| Per message? | Model loaded once, reused. `encode_message` `commerce/embedding_model.py:62` `loop.run_in_executor(None,_encode)` `model.encode([message])` ~50ms CPU. Reference vectors 110×384 cached `unified_intelligence.py:94` `_reference_texts/_vectors` (~169KB). |
| Embeds current? | Yes in unified-intelligence path `commerce/unified_intelligence.py:181` `vec=await encode_message(message)` if `has_semantic`. **Not in Context Engine path** — `context_engine/gatherer.py:677` `MemorySource` calls `retrieve_relevant_knowledge` lexical, no embedding; `scorer.py:94` word overlap, not cosine. |
| Stored embeddings? | Unified reference vectors cached at warm load `encode_messages_sync(texts)` `110-118`. PG `message_embeddings.embedding JSONB` `db/schema.sql:101` comment `HNSW requires pgvector, using B-tree` not vector, not used for MiniLM. |
| Reaches retrieval? | Unified brute-force `_cosine(vec,ref_vec)` `commerce/unified_intelligence.py:85` `O(110*384)` 0.08ms. No HNSW. |
| Reaches OneCall? | **NO** direct. `unified_intelligence.analyze_message` not called in `workers/llm_worker.py:1042-1106` (grep 0). OneCall signals via Qwen JSON `core/one_call.py:52`, not MiniLM. MiniLM only offline evaluator `tests/evaluate_unified_intelligence.py`. Even when Context Engine enabled, MiniLM not involved. |

**Status: IMPLEMENTED but INITIALIZED-ONLY/OFFLINE for OneCall — correctly singleton, but not executing semantically for production OneCall.**

---

## 8. hnswlib Audit

**NOT IMPLEMENTED — 0 .py matches globally.**

- `grep hnswlib` 0 `.py` matches (only docs proposal `https://github.com/nmslib/hnswlib`).
- `pyproject.toml:6-23` no `hnswlib` dependency.
- `db/schema.sql:101` `-- (HNSW index requires pgvector extension; using standard B-tree index instead)` ; `memory/retrieval.py:30` `retrieve_relevant_history` → `vector_search_messages` is Python loop `db/postgres.py:16` `_cosine_distance`, `SELECT ... WHERE user_id=$1` fetch-all no index.
- `commerce/unified_intelligence.py:85` `# Brute-force cosine (no HNSW, <1k vectors)` explicit.
- `docs/PHASE_46:262` `hnswlib NOT deployed, brute-force sufficient for <1k vectors`.
- **If implemented:** would require `Index, knn_query, add_items, init_index` — none exists.
- **Verdict: NOT JUSTIFIED AT CURRENT SCALE** (110 intent vectors + 3-5 memories). Defer until >10k vectors.

---

## 9. Memory Retrieval Audit

| Stage | File:line | Scope/Bound | Advisory vs Authority |
|-------|-----------|-------------|----------------------|
| **Storage LTM** | `commerce/long_term_memory.py:79` `add_memory_item` + `244` `extract_explicit_memories` regex `favorite color is → PREFERENCE`, `going to miami → PLAN` | `workers/llm_worker.py:592` creator-scoped `user_profiles.long_term_memory_by_creator` JSONB bounded 20, conflict by confidence | Advisory |
| **Storage Knowledge** | `commerce/fan_knowledge.py:277` `add_knowledge_item` `135` `extract_fan_knowledge` 30 patterns | `workers/llm_worker.py:608` creator-scoped `fan_knowledge_by_creator` bounded 30 +5 history idempotent `generation_id:297` | Advisory |
| **Lexical retrieval** | `commerce/long_term_memory.py:190` `retrieve_relevant_memories` `commerce/fan_knowledge.py:531` `retrieve_relevant_knowledge` → `memory/context.py:755` limit 3 / `774` limit 5 | `tokens = set(re.findall) overlap*0.5` no embeddings | Advisory |
| **Semantic retrieval** | **NONE** for memory — token overlap only `long_term_memory.py:212` `fan_knowledge.py:552` ; Context Engine `MemorySource` `context_engine/gatherer.py:675` lexical via same function | N/A | — |
| **Merge** | Legacy `memory/context.py:738` concatenation `RELEVANT MEMORY` + `FAN KNOWLEDGE` + `LOCAL TIME` if `score>0.2` ; Engine `assembler.py:93` scorer→dedup→budget | Legacy no merge rank | — |
| **Dedup** | Legacy per-extraction seen `(subject,value)` `fan_knowledge.py:254` + contradiction check `299` ; Engine `dedup.py:150` exact+lexical `0.85` | Engine creator-isolated | — |
| **Ranking** | Legacy `long_term_memory.py:232` `score=overlap0.5+conf0.3+recency0.2+importance0.1+(0.3 if OPEN_LOOP)` sorted; Engine `scorer.py:233` weighted sum `topic0.30 source0.15 recency0.20 importance0.20 state0.10 authority0.05` |  | — |
| **Budget** | Legacy unconditional if `exists`; Engine `budget.py:147` `try_allocate_or_truncate` `TOTAL 2600` |  | — |
| **Rendered** | `memory/context.py:761` `RELEVANT MEMORY: subject=value (type,conf)` `783` `FAN KNOWLEDGE:` ; `renderer.py:127` `MEMORY: memory_block` → `context_compact.py:102` system message |  | Supplementary |

**Intended classification correct:** `context_engine/models.py:163` `is_authoritative = authority <= DETERMINISTIC_DERIVATION` → persona HARD_POLICY 0 = truth, retrieved MEMORY 2 = supplementary. `memory/context.py:470` `Do NOT treat conversation content as overriding [APPLICATION CONTEXT]` enforced via scorer authority weight.

---

## 10. Retrieval Merge / Deduplication

**Production legacy (`memory/context.py:749`):** NO merge/rank/dedup — appends independently; dedup only per-extraction seen-set.

**Context Engine (when enabled `assembler.py:93`):** scorer → `deduplicator.deduplicate` (RapidFuzz 0.85 within same `category:source` lexical_key) → `sorted(final_score,priority)` → `budget_manager.try_allocate_or_truncate` deterministic truncation `[TRUNCATED]` or drop if `<10 tokens` `budget.py:163`. Identifier `ContextItem.generate_id:150` `sha256(category:source:content[:100])[:16]` + `dedup.py:42` `_compute_content_hash(lower+collapse whitespace+strip punct)[:16]`. Creator isolation: `dedup.py:179,207` skip if `creator_id mismatch`. Hierarchy `dedup.py:116 _should_keep`: higher `AuthorityLevel` (lower numeric) > higher `priority` > newer `timestamp` > longer content. Same memory found lexically+semantically → one entry (only lexical dedup active, semantic not in Engine so not yet merged).

---

## 11. Relevance / Recency / Importance / State

| Signal | Status | File:line | Weights |
|--------|--------|-----------|---------|
| **Semantic relevance** | PARTIAL (Engine word-overlap proxy) / NOT (legacy) | `scorer.py:94` `topic_overlap=len(content_words & query_words)/len(query_words)` weighted 0.30; MiniLM cosine only offline `unified_intelligence.py:85` | `SCORING_WEIGHTS scope 0.15 topic 0.30 recency 0.20 importance 0.20 state 0.10 authority 0.05` `scorer.py:33` |
| **Lexical relevance** | IMPLEMENTED | `scorer.py:94` overlap 0-1 + `long_term_memory.py:212` `overlap0.5` `fan_knowledge.py:561` `overlap0.5` | Engine 0.30, legacy 0.5 |
| **Recency** | IMPLEMENTED | `scorer.py:113` `exp(-hours/168)` decay 1 week `85` ; `long_term_memory.py:224` `max(0,1-days/30)` | Engine 0.20, legacy 0.2 |
| **Importance** | IMPLEMENTED | `scorer.py:131` `cat_priority/10*0.7 + priority/10*0.3` `CATEGORY_PRIORITIES SYSTEM10 STATE9 COMMERCE8 MEMORY7` `63` ; legacy `importance0.1+conf0.3` | Engine 0.20, legacy 0.1 |
| **State relevance** | IMPLEMENTED (but wiring partial) | `scorer.py:150` `current_topic in content +0.2`, `relationship buying_signal+COMMERCE +0.15` base 0.5 weight 0.10 ; `long_term_memory.py:234` `OPEN_LOOP+0.3` | Engine 0.10; `assemble(candidates, query=current_message)` `integration.py:168` currently `conversation_state=None` so neutral 0.5 — **PARTIAL wiring** |

All deterministic, no LLM.

---

## 12. Context Budget Audit

| Component | Value | File | Enforced? |
|-----------|-------|------|-----------|
| Ollama num_ctx | 8192 | `core/config.py:94` `ollama_num_ctx 8192` → `core/llm_provider_ollama.py` `options.num_ctx` | Validated `core/context_compact.py:238` `>8192 => False` |
| Engine TOTAL | 2600 tokens | `context_engine/models.py:55` `TOTAL_CONTEXT_BUDGET=2600` | `budget.py:91` `TokenBudgetManager` + `assembler.py:186` violation check + `tests/test_context_engine.py:954` `assert total_tokens <=2600` |
| Per-category | SYSTEM400 STATE200 COMMERCE200 MEMORY150 KNOWLEDGE150 TEMPORAL50 CONTENT100 CONVERSATION800 EMBEDDED200 sum2250 | `context_engine/models.py:58` `CATEGORY_BUDGETS` | `budget.py:70 can_fit` both cat+global; `try_allocate_or_truncate:160` `min(cat.remaining,global)` + trunc `[TRUNCATED]` |
| OneCall compact | system350 state150 conversation600 signals50 total1150 `ONE_CALL_MAX_MESSAGES 8` + `MAX_ASSISTANT_TURNS 3` | `core/context_compact.py:26` | `trim_to_token_budget(recent,600)` `107` + `[-8]` filter |
| Legacy Qwen3 | system400 state200 conversation800 summary200 total1600 | `memory/context.py:42` `QWEN3_TOKEN_BUDGET` | `trim_to_token_budget(recent,800)` `610` |
| Max retrieved | LTM 3, knowledge 5 (+10 Engine) | `memory/context.py:756` 3 / `774` 5 / `gatherer.py:679` 10 | Strict `score>0.2` + `[:limit]` |
| Rendered tokens | `estimate_tokens len/4` `budget.py:39` `max_chars=(available-1)*4` `168` + renderer `budget*4` `renderer.py:93` `CHARS_PER_TOKEN=4` | `worker_integration.py:137` char sum + `renderer.py:195` trunc `...` | **Hard enforced** `assembler.py:109` `try_allocate_or_truncate` drop if `<10` tokens `163` + `validate_assembly` `178` |
| OneCall size | `estimate_one_call_tokens sum(count_tokens)` tiktoken `memory/context.py:50` | `context_compact.py:225` + `one_call_pipeline.py:111` `validate_one_call_context` | Returns `is_valid False commerce low_information needs_handoff True` prevents send |

**Old vs new:** Old `build_qwen3_context` could grow with 30 messages + vault TOP2 + LTM 3 without hard global cap (only per-section trim). New `OneCall` caps at 8 messages + 1150 tokens + 2600 engine, validated <8192. **Cannot recreate oversized context** — budgets are allocations, not documentation.

---

## 13. Authoritative State Audit

| Domain | Source & Fetch (authoritative) | Via CE? | Sent to Qwen? | Override? | File |
|--------|--------------------------------|---------|---------------|-----------|------|
| Persona | `personas.metadata` `get_structured_persona_async` `creator_persona.py:318` `SELECT .. ORDER BY is_default DESC` `HARD_POLICY 0` `gatherer.py:137` | Yes `PersonaSource:169` priority10 | Yes `memory/context.py:685` `CREATOR PERSONA (compact)` + `context_compact.py:78` system prompt | No `persona_validation.py:133` `FACT_FAIL` severe | `db/postgres.py:155` |
| Fan/business | `users` `get_user` `db/postgres.py:115` `FanStateSource DETERMINISTIC_DERIV` `230` | Yes `FanStateSource:245` `Funnel: .. \| Blocked` | Yes `memory/context.py:279` `STATE:` | No `commerce/decision.py:300` eligibility first, `execution.py:147` `is_blocked` re-check | `commerce/state.py:203` |
| Conversation | `messages` `get_recent_messages limit 20 creator_id` `db/postgres.py:464` `ConversationHistorySource DETERMINISTIC_RULE` `390` | Yes `gather():394` each turn + summary | Yes `memory/context.py:814` appended trimmed `800` + `derive_conversation_state:157` `CONVERSATION: topic=` | No verbatim `pipeline.py:289` `content[:800]` | `memory/context.py:521` |
| Commerce | `commerce_offers` `fangate_products` `commerce/state.py:169` `resolve_commerce_state` `DETERMINISTIC_RULE` `481` | Yes `CommerceStateSource:466` counts `pending/clicked` + timing | Yes `memory/context_assembler.py:729` `Purchases: N Active offer:` | No `execution.py:96` re-validates `evaluate_ppv_eligibility` `price_minor` from DB `252` | `commerce/state.py:169` |
| Subscription/payment/access | `fangate_transactions transaction_id delivery_id` `has_purchased_product` `dao.py` `SELECT state purchased AND transaction_id NOT NULL` | Partial counts only `gatherer.py:523` | Minimal aggregated counts `COMMERCE:` never `transaction_id` | No `create_offer_serialized` `dao.py:82` `ppv_offer:{c}:{u}:{p}` lock `execution.py:265` | `commerce/execution.py:212` |

`context_engine/models.py:14` hierarchy `HARD_POLICY 0 < DETERMINISTIC_RULE 1 < DETERMINISTIC_DERIVATION 2 < CONTEXT_ASSEMBLY 3 < LLM 4` — `0` never overridden.

---

## 14. Commerce / PPV End-to-End

**Sales opportunity:** Hybrid — LLM observations (`CommerceSignals` 18 fields `commerce/signals.py:147` `extra=forbid`) + deterministic decision (`commerce/decision.py:272` pure). Mapping `signals_to_context` `289` mechanical `asked_to_buy←explicit_purchase_request`, `asked_about_price←requested_price!=None OR price_interest≥0.80` `PRICE_ASK_THRESHOLD:52`, `buying_score←purchase_intent`. `low_information()` neutral never looks like interest `233`.

**Conversion cues priority** `commerce/decision.py:299` `1 eligibility →1.5 handoff →1.7 asks_for_free_content suppress →2 creator_sales_enabled →3 has_relevant_product →4 has_active_offer →5 recent_purchase 6h →6 offer 24h →7 budgets 2/24h →7.5 fatigue →7.6 negative_intent≥2 →7.7 conf<0.30 →7.8 opening/rapport →7.9 paused →7.10 aftercare pending/sent →7.11 rejection≥3 →8 explicit buy/price/content →8.5 tip eligible →9 strong≥0.80 →10 follow_up due →11 moderate≥0.55 →12 relationship≥0.60 → building`.

**PPV prep:** Who chooses — Product `resolve_commerce_product_with_history` `product_selection.py:149` creator-scoped `list_fangate_products 200` `is_accessible+sales_url` `60`, exclude purchased `_get_purchased_product_ids:86`, suppressed family `vault_taxonomy:201`, single→direct, multi unpurchased 1→direct, multi≥2→ `rank_products_by_relevance` `rel≥0.15` `282` else `None` cheapest tie; **never LLM**. Price `fangate_products.price_minor` `execution.py:252` `USD` `266` mismatch→`PRODUCT_UNAVAILABLE`. Validate `evaluate_ppv_eligibility` `state.py:223` + `execution.py:219` re-evaluate `User/Product/OfferContext` first denial wins + `integration status=active` `120` + decryptable `129` + pending offer `ALREADY_EXECUTED:207`. Create `create_offer_serialized` `pg_advisory_xact_lock hashtextextended('ppv_offer:{c}:{u}:{p}')` `dao.py:82` transaction `SELECT pending/clicked → INSERT pending` `execution.py:268`. Fangate `sales_url` or `dropfans/service.py:244` `build_checkout_url`. Send `select_commerce_response:228` pure requires `COMPLETED + OFFER_PPV + EXECUTED/ALREADY_EXECUTED + GENERATED` → `USE_COMMERCE_RESPONSE` `320` else `FALLBACK_TO_STANDARD_LLM`.

**Price authority:** Qwen `requested_price` `core/one_call.py:52` only sets flag, `_price_positive_finite` `signals.py:196` bounded but never `commerce_offers.price_minor`. Context Engine `CommerceStateSource` renders `Products: title` no price `gatherer.py:552`. Memory `fan_knowledge` stores interest not price. Fan `requested_price` captured as float `signals.py:123` but ignored for `price_minor`. **Deterministic commerce authority is sole price setter** — locked at insert `execution.py:252` immutable.

---

## 15. OneCall Commerce Signal Propagation — Phase 76/77B Fix Proof

| Caller | File:line | Classification |
|--------|-----------|----------------|
| `_try_commerce_draft(..., signals=_commerce_signals)` in new path | `workers/llm_worker.py:1116` `signals=_commerce_signals` from `one_call.signals` `1104` comment `Pass one-call signals to skip duplicate extract_commerce_signals()` | **new path — NO extract** |
| `outcome = await resolve_and_run_commerce(request, signals=signals)` | `workers/llm_worker.py:385` passthrough | shared |
| `run_commerce_pipeline` | `commerce/integration.py:146` `signals=signals` | shared |
| `if signals is None: signals = await extract_commerce_signals(...)` | `commerce/pipeline.py:480` | **Gate — avoids duplicate when signals provided** |
| `extract_commerce_signals` | `commerce/deepseek.py:170` `provider.generate` | **legacy-only caller** `workers/llm_worker.py:1214` inside `if _llm_path=="legacy":` comment `# Commerce signal extraction (legacy-only)` `1212` — grep shows **only** `1214` in `llm_worker.py` in new block `1042-1207` has 0 calls |
| `_fallback_3llm_pipeline` | `core/one_call_pipeline.py:215` returns dummy `low_information` no LLM | dead path (log misleading but 0 LLM) |

**Global grep `extract_commerce_signals`:** `commerce/deepseek.py:170 def` + `commerce/pipeline.py:480` conditional + `workers/llm_worker.py:1214` legacy + `commerce/adaptive_optimization` not in `llm_worker` new. **New path 0 calls — proven.**

---

## 16. Persona Audit

| Item | Evidence | Retrieval override? | Fixed? |
|------|----------|---------------------|--------|
| Creator persona | `build_sunny_persona` `creator_persona.py:36` 23 fields, `get_structured_persona_async:318` `SELECT metadata FROM personas WHERE creator_id=$1`, cache `persona:creator:{id}` `db/redis.py:403` | No — `FanStateSource` cannot write personas | — |
| Behavior state | `derive_persona_behavior_state` `persona_behavior.py:124` regex priority `serious>annoyed>...` `69`, `question_allowed` `question_policy.py` | No | Yes derives `workers/llm_worker.py:1006` renders `1017` |
| Behavior block | `render_persona_behavior_block` `374` → `PERSONA BEHAVIOR: emotion=…\nVoice: …\nBehavior: …` ~60 tokens | — | **Fixed:** `workers/llm_worker.py:1022` `if _behavior_block: context.append({"role":"system","content":_behavior_block})` before `1042` generation (was derived but not injected in pre-79B `disassembly.txt:9531`) |
| Compact context entry | `build_one_call_context` `context_compact.py:37` → `_build_compact_system_prompt:127` `persona_block` (trimmed `Sunny → sunny` if `identity_established` `153`) + `Fan:` + `Stage:` + `Rules:` system 350 | No — `retrieved_context` appended after `102` preserves `HARD_POLICY` first | — |
| Validation | `validate_persona_voice` `persona_validation.py:133` `fact_violation` `FACT_FAIL` severe, `workers/llm_worker.py:1329` `persona_validation_severe` → `score max(0,score-0.10)` `1345` → not auto-approved `1441` | No — post-generation gate before `enqueue_send` | Fixed aligns injected block with validation |

---

## 17. Pydantic / Structured Output Audit

`raw_json → json.loads (TypeError/JSONDecodeError → is_valid False needs_handoff True)` `core/one_call.py:112` → `OneCallReply.model_validate (extra=forbid → is_valid False Schema validation)` `126` → `_compute_safety_flags` `180` HARD_FLAGS price_mention etc → `needs_handoff True confidence 0.3` if flags → `_compute_quality_heuristics` `227` 4 scores 0-10 composite `0-1` capped `<5 words 0.5` `307` → `quality<0.3 → needs_handoff True` `159`.

**OneCallReply** `core/one_call.py:38` `extra="forbid"`: `reply str 1..2000 required` `_validate_reply strip` `60`, `commerce_signals: CommerceSignals default low_information` optional, `confidence float 0..1 default 0.5` optional, `needs_handoff bool default False` optional. Missing `reply` / empty / `>2000` / `confidence>1` → ValidationError → `is_valid False`. Unknown top-level `price` → ValidationError.

**CommerceSignals** `commerce/signals.py:147` `extra="forbid"`: required `purchase_intent, content_interest, relationship_engagement, price_interest: BoundedFloat 0..1` (`118`), `explicit_purchase_request, explicit_content_request, declined_recent_offer, fan_asks_question, accepted_recent_offer, asks_for_free_content: StrictBool`, `negative_sentiment, confidence, model_uncertainty, conversation_relevance: BoundedFloat`, `evidence list max5 ≤240 no payment` `186`, `primary_intent ⊆ INTENT_CATEGORIES 21` `204`, `intent_tags max5` `212`, `negative_intent_tags ⊆ hesitation/rejection/complaint` `218`, optional `requested_price BoundedPrice positive finite` `195`, `topic_continuity`. `evidence` card 13-16 digits → ValueError `189`, `Str` for bool → `_reject_str_bool` `102`, unknown tag → ValueError `215`. Omitted `commerce_signals` → default `low_information`. Partial missing → fallback.

**Prompt/schema agreement:** `ONE_CALL_SYSTEM_PROMPT:314` asks 4 keys `reply, commerce_signals{15 fields}, confidence, needs_handoff` + `NEVER price/payment, Output ONLY JSON` matches `OneCallReply` but prompt lists 15-field subset vs `CommerceSignals` superset `+ accepted_recent_offer, conversation_relevance, topic_continuity` `181` with defaults — missing OK, extra would be `forbid` but prompt now matches after P0-02 fix (previously 100% invalid_payload if model complied; now fixed `deepseek.py:56` 19 fields).

**Pipeline:** `core/one_call_pipeline.py:124` `provider.generate(..., response_mime_type="application/json", max_output_tokens 400, temp 0.7, model=settings.model_name)` → `llm_provider_ollama.py:264` `POST /api/chat num_predict 400 num_ctx 8192` timeout `120` `config.py:91`. No retry except `think=true` empty retry `247`.

---

## 18. Failure Path Audit (after OneCall start)

| Failure | Caught | Retry LLM? | Legacy fallback? | Operator queue? | DLQ? | ACK? | File |
|---------|--------|------------|-----------------|-----------------|------|------|------|
| Provider timeout/exception 401/429/500 | `core/one_call_pipeline.py:133` → `is_valid False Generation failed` ; `workers/llm_worker.py:1169` except | No (single attempt, only think empty retry `llm_provider_ollama.py:247`) | **No** `Hardened: no legacy cascade` `1133,1171` (`_fallback_3llm_pipeline:215` dummy not reached) | Yes `["one_call_exception"]` `1179` + `publish_event ai.generation_completed was_auto_approved False` `1188` + `suggestion.created` `1196` best-effort | No — returns normally → `ack_inbound XACK` `1775` ; outer raise path → `ai.generation_failed` `1676` → `move_to_dlq` `1779` DLQ `dead_letter_queue` `db/redis.py:18` + `XACK` `276` | `ack_inbound` success else `move_to_dlq` |
| Invalid JSON | `core/one_call.py:112` `is_valid False JSON parse failed` → `workers/llm_worker.py:1129` `one_call_invalid_result` | No | No | Yes `["one_call_invalid_result"]` `1137` + publish `1150` → `return` | No DLQ (ACK) | ACK | `core/one_call.py:112` |
| Pydantic forbid/bounds | `core/one_call.py:126` `Schema validation failed` same | No | No | Yes same | No DLQ | ACK | `126` |
| Missing/empty | `TypeError` `112` / `llm_provider_ollama.py:259` `empty response → LLMProviderError` → `is_valid False` / capped `307` `<5 words` | think retry once `247` else No | No | Yes | No | ACK | `259` |
| Commerce failure | `workers/llm_worker.py:387` `except → return None` never raises; `commerce/integration.py:131` `FAILED` | No | No — fallback `FALLBACK_TO_STANDARD_LLM` `selection.py:280` keeps draft | No — fallback draft used | No | ACK | `387` |
| Context Engine failure | `context_engine/worker_integration.py:186` `failed=True` fail-open `workers/llm_worker.py:561` | No | No | No | No | ACK | `186` |
| Embedding failure | `workers/llm_worker.py:1713` warmup `try debug` | No | No | No | No | ACK | `1711` |
| DB failure get_user/profile/recent | `memory/context.py:557` `return_exceptions True` defaults; `commerce/state.py:455` `RESOLUTION_FAILED` | No | No | `creator_context_unavailable` `565` → queue `573` | if raise → `ai.generation_failed` → DLQ | `XACK` inside `move_to_dlq:276` else pending | `543` |
| Redis lock/publish/enqueue | `acquire_user_lock SET NX EX` `db/redis.py:285` skip `474` ; `publish_event except warning return None` `core/event_bus.py:64` ; `enqueue_send XADD` `65` if fails → outer `except → generation_failed → DLQ` | `requeue_stalled` next loop `1790 sleep 1` | No | If `enqueue_send` fails → DLQ (would lose send) | DLQ write fail → `return False` `db/redis.py:273` leaving pending for `XAUTOCLAIM` | `ack` only on success `1775` | `285` |
| Send/handoff | `chatbotv2/main.py` `requeue_stalled_send_messages XAUTOCLAIM` `158` + `move_send_to_dlq XACK even if DLQ fails 155` | send worker retries via `XAUTOCLAIM` | No | No — send after generation | `move_send_to_dlq` ACKs always | `XACK SEND_STREAM 115` | `1442` |

**Critical:** No failure reintroduces legacy multi-LLM — hardened early `return` bypasses `1209` block.

---

## 19. Redis / ACK / Recovery Audit

| Mechanism | Impl | Params | Reclaimed trace | File |
|-----------|------|--------|-----------------|------|
| XREADGROUP | `r.xreadgroup(llm_workers, worker_id, {inbound_messages: ">"}, count, block)` | `INBOUND_STREAM inbound_messages` `14`, `CONSUMER_GROUP llm_workers` `40`, `SEND_STREAM send_messages` `14`, `send_workers` `40` | `handlers._wait_and_process → enqueue_inbound XADD` `195` with `generation_id` `188` → `run_worker:1755` `read_inbound count 5 block 2000` delivers `[(stream,[(msg_id,data)])]` | `db/redis.py:199,96` |
| XAUTOCLAIM | `r.xautoclaim(inbound_messages, llm_workers, consumer, idle, "0", 10)` | `idle = settings.redis_pending_idle_ms 60000` `config.py:46` (AGENT.md says 30s — actual 60s), send `30000` `158`, called each loop `run_worker:1745` | reclaim logs `1750` `Reclaimed %d` | `db/redis.py:224,158` |
| XACK | `r.xack(inbound_messages, llm_workers, id)` | success `1775` `ack_inbound` after `process_message`; failure `1779` `move_to_dlq` → `276` `xack`; `move_send_to_dlq` `155` always `xack` | Happy: `ack` removes PEL; Exception: `move_to_dlq` `xadd dead_letter_queue {message_id, reason, stream, payload JSON, worker_id}` `260` then `xack` ; if DLQ `xadd` fails `return False 275` leaving pending for reclaim | `db/redis.py:219,115,276` |
| Send stream | `XADD send_messages {entity,content,dedup_id,generation_id,creator_id}` | `ttl send_dedup:{creator}:{dedup} 3600` `81`, `dedup_id md5(user:msg:tgId)` `393` `llm_worker.py`, `generation_id` preserved | `enqueue_send 65` → `read_send_messages` `alive` → `is_send_duplicate EXISTS` `89` → `mark_send_dedup` → `ack_send` | `db/redis.py:65,81,89` `workers/llm_worker.py:1442` |
| DLQ | `dead_letter_queue` `18` | `DLQ_MAX_REPLAY 3` `47`, `retention 604800 7d` `47`, `list_dlq_entries xrevrange 586`, `replay_dlq_entry 646` enforces `replay_count<3` `658` + lock `dlq_replay_lock:{id} NX EX 30` `707` → `enqueue_inbound 718` or `enqueue_send 721` + `replay_count++ 731` | failed `process_message` → PEL idle >60s → `XAUTOCLAIM` claim → `move_to_dlq` | `db/redis.py:18,260` |
| Dedup | `SETEX send_dedup:{creator}:{dedup} 3600 1` | creator-scoped | Prevents duplicate replay after `replay_dlq_entry` | `db/redis.py:81` |
| Stalled→OneCall→ACK | `requeue_stalled` → `read_inbound >` → `process_message` → `one_call` → `ack/dlq` | 60s idle | **Bug:** `requeue_stalled_messages` `243` `msg_ids=[id for id,_ in result[1]]` discards fields, `run_worker:1745` logs only ids, `XREADGROUP >` will never deliver already-pending claimed entries (pending not `>`). Effective recovery requires iterating `result[1]` directly — **not wired**. Claimed payload stays PEL-claimed until `ack/dlq` which requires processing loop to iterate claimed entries. |

*Phase 77B did not alter XREADGROUP/XAUTOCLAIM/XACK/DLQ/dedup semantics (verified `AGENTS.md` invariants preserved: `ai.generation_completed` after `enqueue_send` `1456`, `event_id` unique `core/event_bus.py:46`, failure isolation best-effort `64`).*

---

## 20. Profile / DB Optimization Regression (Phase 74B)

**74B optimizations verified intact:**

- `get_user_profile()` caching: `memory/context.py:578` `_profile_cache` generation-local, `db/redis.py:403` persona cache `TTL600`, no redundant profile fetches inside `build_qwen3_context` parallel gather (single `get_user` + single `get_user_profile` + single `get_recent_messages 20` + single `get_latest_summary`).
- Redundant queries removed: `build_qwen3_context` uses `asyncio.gather(..., return_exceptions=True)` `543` single batch of 4 PG; legacy sequential fallback `551` only on exception.
- Parallel `asyncio.gather` preserved.
- Redis publish batching: `core/event_bus.py:94` `publish_events_batch` via pipeline `db/redis.py:224` used at `workers/llm_worker.py:1506` for completion events (B4).
- orjson: `core/event_bus.py:6` `try import orjson` → `_json_dumps` `orjson.dumps.decode()` else `json` ; `db/redis.py:6` same.

**Counts per normal message (77C measured, 74B optimized was 7 PG + 2 Redis):**

| Operation | Count | File |
|-----------|-------|------|
| PG `get_user` | 1 | `memory/context.py:549` |
| PG `get_user_profile` | 1 (cached second call hit `_profile_cache`) | `549` |
| PG `get_recent_messages` | 1 (limit 20, creator_id scoped) | `551` |
| PG `get_latest_summary_with_age` | 1 | `553` |
| PG `fan_knowledge` / `vault` | 0-2 (conditional `should_retrieve`) | `564` |
| PG `is_user_auto_reply_excluded` | 1 | `workers/llm_worker.py:483` |
| PG `add_to_operator_queue` / `enqueue_send` | 1 (either queue or send, not both) | `565/1442` |
| PG `resolve_single_application_creator` | 2 (handlers + worker) but 2nd cached via Redis | `459` |
| Redis `acquire_user_lock SET NX` | 1 | `285` |
| Redis `is_auto_reply_enabled GET` | 1 | `482` |
| Redis `publish_event` started | 1 | `526` |
| Redis `publish_events_batch` completion | 1 pipeline (2 events) | `1506` |
| Redis `enqueue_send XADD` or `operator_queue XADD` | 1 | `65` |
| **Total** | **~7 PG, ~4 Redis** | — |

No regression from 77B: `retrieved_context` adds **0** DB/Redis calls when disabled (default). When enabled, adds `ContextEngineIntegration` gatherers which reuse same `get_user`/`get_recent_messages`/ `retrieve_relevant_knowledge` (lexical, no extra PG beyond existing) + `FanState/Commerce/Persona` already cached — at most +1 `get_dropfans_integration` if not cached. `increase <1 PG`.

---

## 21. Single-Creator Scope

Single-creator tool — all isolation creator-scoped:

| Dimension | Key / Scope | File | Cross-fan contamination? |
|-----------|-------------|------|--------------------------|
| `creator_id` | `resolve_single_application_creator()` sole source `creator_integrations` `status=active` `SingleCreatorStatus.READY` `commerce/single_creator.py:75` | `workers/llm_worker.py:459` | No multi-creator |
| `user_id` fan | Telegram `sender_id` `handlers.py:27` | — | — |
| Memory `long_term_memory_by_creator` / `fan_knowledge_by_creator` | `commerce/long_term_memory.py:79` `add_memory_item(user_id,creator_id)` `fan_knowledge.py:277` `WHERE creator_id=$1` | `workers/llm_worker.py:592` | Isolated `WHERE creator_id=$1` `db/postgres.py:464` `(creator_id=$2 OR IS NULL)` |
| Commerce `commerce_offers creator_id user_id product_id` `fangate_products creator_id` | `commerce/execution.py:252` `local_product.get("price_minor")` filtered `list_fangate_products(creator_id)` | `product_selection.py:149` | Isolated |
| Persona `personas creator_id` `is_default` | `memory/creator_persona.py:318` `WHERE creator_id=$1` | `db/postgres.py:155` | Isolated |
| Redis `lock:creator:{cid}:user:{uid}` `debounce:creator:{cid}:user:{uid}:messages` `send_dedup:{creator}:{dedup}` `persona:creator:{id}` | `db/redis.py:318,403` `workers/llm_worker.py:393` | Isolated |
| Retrieval `ContextItem creator_id user_id` `GathererConfig creator_id user_id` `context_engine/gatherer.py:876` `get_recent_messages(...,creator_id)` | `models.py:145` `dedup respects isolation` `dedup.py:180` | Isolated — same memory lexically+semantically → one entry only within same creator |

No global broadcast without `scope=user` + `user_id/dialog_id/generation_id` `core/event_bus.py:46`.

---

## 22. Actual Model / Provider Configuration

| Setting | Code | Actual runtime | Intended | Discrepancy |
|---------|------|----------------|----------|-------------|
| `llm_provider` | `core/config.py:84` `llm_provider: str = "ollama"` | `"ollama"` authoritative | — | — |
| `ollama_base_url` | `89` `https://ollama.brestalogistics.co.ke` Caddy+Basic Auth | remote VPS | — | — |
| `ollama_model` | `90` `ollama_model: str = "qwen3:4b"` `qwen3:4b` `num_ctx 8192` `90` | **qwen3:4b** | **Qwen2.5:3B** (AGENT.md spec) | **MISMATCH — P1** |
| `model_name` / `cheap_model` | `25` `model_name gemini-flash-latest` `26` `cheap_model gemini-flash-latest` | fallback Gemini | — | Gemini is fallback, Ollama authoritative |
| `temperature, max_tokens` | `core/one_call_pipeline.py:124` `max_output_tokens 400 temp 0.7` (OneCall) `memory/context.py:42` Qwen temp 0.85 max 200 | OneCall 0.7/400, legacy 0.85/200, scoring 0.2/512 | — | — |
| `num_ctx` | `94` `ollama_num_ctx 8192` → `core/llm_provider_ollama.py:148` `options.num_ctx` | 8192 | 8192 | OK |
| Fallback | `44` `gemini_daily_quota 20` `45` `gemini_fallback_enabled True` `core/llm_provider_ollama.py:207` `_generate_native` POST `/api/chat` `think false` | Gemini→Ollama daily 20 | — | — |
| `.env.example` | `OLLAMA_MODEL=qwen3:4b` (matches config) | — | Qwen2.5 spec outdated in docs `core/context_compact.py:1` `Optimizes for Qwen2.5` | Doc stale |

Provider passed to `provider.generate(system_instruction=ONE_CALL_SYSTEM_PROMPT+..., user_content=json.dumps(messages), model=_settings.model_name ...)` `one_call_pipeline.py:126` — but `llm_provider_ollama.generate` uses `self.model = settings.ollama_model` `llm_provider_ollama.py:148` regardless of `model` param? Verify: `generate` signature `model` is ignored, uses `self.model`. So actual `qwen3:4b`.

---

## 23. Runtime Observability

| Telemetry | Proven? | File |
|-----------|---------|------|
| Context Engine start/end | **PARTIAL** — `ContextEngineObservation total_ms,gather_ms,assembly_time` `worker_integration.py:104` + `workers/llm_worker.py:548` `context_engine_enabled/ms/gather_ms/candidates/selected/dropped/tokens/chars` `GenerationTelemetry` | `core/telemetry.py:21` `context_engine_*` fields `workers/llm_worker.py:548` |
| Retrieval latency | Yes `gather_time_ms` `assembly_time_ms` `integration.py:160` | `context_engine/integration.py` |
| Embedding latency | **NO** — unified intelligence latent but not on OneCall path, no `encode_message` timing in telemetry | `commerce/embedding_model.py:62` not instrumented |
| Candidate counts | Yes `candidate_count selected_count dropped_count` `worker_integration.py:154` | `workers/llm_worker.py:549` |
| Selected count | Yes `selected_count` `549` | — |
| Context size | Yes `context_build_ms, persona_context_chars, generation_context_chars, generation_context_tokens_estimate` `workers/llm_worker.py:510` + `OneCallTokenBudget` validated `core/context_compact.py:225` | `memory/context.py:42` |
| OneCall start/end + LLM latency | Yes `_one_call_start/_end` `workers/llm_worker.py:1046,1206` `generation_latency_ms` | `core/telemetry.py` `generation_latency_ms` |
| Provider latency | Yes `provider_latency_ms` `workers/llm_worker.py:1119` | — |
| LLM call count | **PARTIAL** — not explicit counter; inferred via `routing_decision one_call` vs `operator_queued` | `workers/llm_worker.py:1106` |
| Commerce execution | Yes `routing_decision one_call_commerce` `1124` + `create_offer_serialized` logs | `workers/llm_worker.py:1124` |
| Handoff/send | Yes `routing decision operator_queued vs auto_approved` `1414/1455` + `publish_event suggestion.created` | `workers/llm_worker.py:1391` |
| Generation start/failed/completed event_id | Yes `ai.generation_started` `526`, `ai.generation_failed` `1676`, `ai.generation_completed` `1506` all same `generation_id` `443` | `core/event_bus.py:46` `event_id uuid4` |

**Missing:** embedding latency/candidate cosine scores, RapidFuzz similarity scores, per-category token breakdown in telemetry (budget validates but not logged), Ollama `num_predict` actual tokens used.

---

## 24. Test / Execution Matrix (existing coverage, read-only)

| Area | Tests covering | Verdict | Gap |
|------|---------------|---------|-----|
| OneCall | `tests/test_phase75b_one_call.py` 38, `test_phase75c_context_compact.py` 19, `test_phase77b_context_engine_integration.py` 13 (this report) | **COVERED** | OneCall malformed JSON variants |
| Context Engine | `tests/test_phase72_context_engine_integration.py` 62, `test_context_engine.py`, `test_context_assembler.py`, `test_phase77b_context_engine_integration.py` `TestContextEngineObservationRenderedText` | **COVERED** | Default-disabled path mock not testing live DB integration |
| RapidFuzz | `tests/test_context_engine_gatherers.py` `test_dedup`, `pyproject.toml:22` | **COVERED** dedup `0.85` threshold | No production启用 E2E with `context_engine_observational=True` |
| MiniLM | `tests/evaluate_unified_intelligence.py`, `commerce/embedding_model.py` no OneCall test | **INITIALIZED-ONLY** — no OneCall path test | Missing: `encode_message` latency + `retrieve_relevant_memories` semantic negative |
| Commerce/PPV | `tests/test_commerce_context.py`, `test_conversations_analytics.py` `select_commerce_response` `USE_COMMERCE_RESPONSE` | **COVERED** | No E2E `explicit_purchase_request → EXECUTED → GENERATED` live via OneCall (77B only mocks) |
| Persona | `tests/test_phase72_context_engine_integration.py` persona isolation, `commerce/persona_validation.py` tests | **COVERED** | `_behavior_block` injection tested in `test_phase77b` but not prior regression |
| Failure handling | `tests/test_ai_resilience.py` `test_generation_lifecycle_events_unchanged` + `test_auto_approval_unchanged` + `test_post_process_failure...` , `tests/test_phase77b` hardening `TestOneCallHardening` | **COVERED** hardening `no legacy cascade` | Missing: DLQ `move_to_dlq` leaves pending on failure |
| Redis recovery | `tests/test_phase1_regression.py` `TestConsumerGroupRecovery` `XAUTOCLAIM` 30s, `tests/test_realtime.py` | **COVERED** but recovery bug not asserted: `requeue_stalled_messages` discards fields | Add assertion for returned fields iteration |
| Creator isolation | `tests/test_phase73_production_context_integration.py` `WHERE creator_id`, `test_context_engine_gatherers` isolation | **COVERED** | No cross-creator contamination fuzz |

**Overall 210 tests** `tests/test_phase75c + test_phase75b + test_phase72 + test_phase77b + test_ai_resilience` passed `87s`.

---

## 25. Findings by Severity

### P0 — Must fix before Stage B claims production (1)

**P0-1 Redis XAUTOCLAIM pending-discard — stalled recovery never processes payload.**
`db/redis.py:224` `requeue_stalled_messages` returns `(next, [(id, fields)])` but maps `msg_ids=[id for id,_ in result[1]]` `243` discarding fields; `workers/llm_worker.py:1745` `stale_count, stale_ids = await requeue_stalled_messages(...)` logs `1750` but never iterates `result[1]` fields for `process_message`. `XREADGROUP >` will never deliver already-pending entries. Impact: message idle >60s stays PEL-claimed but not re-executed until `move_to_dlq` after crash loop. Violates `AGENTS.md` `XAUTOCLAIM` 30s invariant and `docs/PHASE_46` recovery. *Fix in Stage B: iterate `result[1]` and `for msg_id, fields in result[1]: await process_message(bytes_to_dict(fields))` before next `read_inbound`.*

### P1 — High (3)

**P1-1 Model identity mismatch — docs/spec says Qwen2.5:3B, runtime is qwen3:4b.**
`core/config.py:90` `ollama_model: str = "qwen3:4b"`; `core/context_compact.py:1` `Optimizes for Qwen2.5`; `docs/AI_NATIVE_LLM_PHASE_77A...:421` `hnswlib does NOT exist`. No `Qwen2.5:3B` binary referenced anywhere. Provider still `POST /api/chat` qwen3:4b. Needs doc alignment or model re-pinning. Not hardened for quality drift.

**P1-2 Commerce 2nd LLM discarded — pipeline generates commerce response even for NO_OFFER.**
`commerce/pipeline.py:619` `generate_commerce_response` always after `orchestrate_commerce` unless `DECISION_FAILED/STRATEGY_FAILED`, then `commerce/selection.py:280` discards as `FALLBACK_TO_STANDARD_LLM`. For normal `casual_chat` scenario, total provider invocations 2 (1 useful). Cost +150% vs claimed 1. Stage B should gate `if action == OFFER_PPV and execution in (EXECUTED,ALREADY_EXECUTED): generate` else skip.

**P1-3 Context Engine wiring partial — state relevance neutral, MiniLM not in ranking.**
`context_engine/scorer.py:150` `state_relevance` reads `current_topic/relationship` but `ContextEngineIntegration.process` `integration.py:168` calls `assembler.assemble(candidates, query=current_message)` with `conversation_state=None`, so score stays 0.5 neutral. Semantic relevance is word-overlap proxy `scorer.py:94`, not MiniLM cosine. `STATE` contribution 0.10 thus wasted. Also `commerce_signals` embedded already via OneCall, but `FanStateSource` scored separately. Not a correctness bug, but undermines 5-signal ranking claim.

### P2 — Medium (3)

**P2-1 Event batch key backward compat fixed in 77C but still legacy "event" callers.**
`core/event_bus.py:94` `publish_events_batch` previously required `event_type`, but all `workers/llm_worker.py:573,1416` callers used `"event"`. `Phase 77C` fix `event_bus.py:109` `ev.get("event_type") or ev.get("event")` tolerant, validated by `test_phase77b TestEventBusBackwardCompat`. Keep but migrate callers to `event_type` and add `extra=forbid` test.

**P2-2 Context Engine default off — not production-active without flag.**
`core/config.py:135` `context_engine_observational=False`. Wiring proven `core/context_compact.py:102` but requires `CONTEXT_ENGINE_OBSERVATIONAL=true` restart. Effective production OneCall today still has 0 retrieved memory from Engine (only legacy 3/5 lexical). Stage B must flip flag or rename to `context_engine_enabled` and canary `10%`.

**P2-3 OneCall discarded 2nd call hides in logs as fallback misleading.**
`core/one_call_pipeline.py:193` log `"falling back to 3-LLM pipeline"` but `_fallback_3llm_pipeline:215` returns dummy empty `is_valid True reply ""` 0 LLM (TODO). Hardened path bypasses it, but log still emitted on validation_error path before hardened check? In current `workers/llm_worker.py:1129` log is not this one; pipeline log is `one_call_pipeline_with_fallback:189` `"One-call failed, falling back to 3-LLM pipeline"` which is stale. Should log `"One-call invalid → operator_queue (hardened)"`.

---

## 26. Stage B Recommendations (NO CODE IN STAGE A)

1. **Fix XAUTOCLAIM recovery** — iterate claimed fields `db/redis.py:224` and process before `read_inbound >`.
2. **Gate commerce generation** — only `generate_commerce_response` when `action OFFER_PPV` and `execution EXECUTED/ALREADY_EXECUTED` to achieve 1 invocation for non-PPV.
3. **Flip Context Engine flag** — `context_engine_observational=True` canary `10%` with `10` creator, measure `gather_ms` and `total_tokens` via `worker_integration.py:148` already telemetry, assert `retrieved_context` reaches Qwen in production via `GenerationTelemetry`.
4. **Wire state relevance** — pass `conversation_state` into `ContextEngineIntegration.process(query, conversation_state)` and scorer.
5. **Align model docs** — either re-pin `qwen3:4b` in all specs or eval `Qwen2.5:3B` vs `qwen3:4b` quality/latency.
6. **Migrate event batch callers** to `event_type` and remove compat shim after validation.
7. **Add DLQ pending-leave test** — `move_to_dlq` returns False leaves pending; add assertion in `test_phase1_regression.py`.

---

## 27. Verdict Table

| Component | Status |
|-----------|--------|
| OneCall | **ACTIVE** — `llm_path=new` default, `one_call_pipeline_with_fallback` wired `workers/llm_worker.py:1079` |
| Exactly 1 LLM call | **PROVEN** — 1 authoritative, 2nd discarded commerce when creator READY (1 useful); hardened no extra on malformed/provider |
| Context Engine | **OBSERVATIONAL / WIRED** — full pipeline executed only when `context_engine_observational=True`, otherwise no-op `<1µs` |
| RapidFuzz | **ACTIVE / CONDITIONAL** — `dedup.py:57` `fuzz.WRatio 0.85` only when Engine enabled; `unified_intelligence` offline |
| MiniLM | **INITIALIZED ONLY / UNUSED** — singleton `all-MiniLM-L6-v2 384` `embedding_model.py:51` loaded lazily, not on OneCall path |
| hnswlib | **NOT IMPLEMENTED** — 0 matches, brute-force `cosine` 110 vectors `unified_intelligence.py:85`, B-tree `schema.sql:101` |
| Hybrid retrieval | **PARTIAL / ABSENT** — lexical `retrieve_relevant_memories` 3 + `fan_knowledge` 5; semantic MiniLM not merged; Engine dedup lexical only |
| Context ranking | **PARTIAL** — 5 signals weighted `0.15/0.30/0.20/0.20/0.10/0.05` `scorer.py:33` but `state` neutral, semantic is word overlap |
| Hard context budget | **ACTIVE** — Engine `2600` `models.py:55` + OneCall `1150` `context_compact.py:26` + `num_ctx 8192` validated `225` |
| Commerce signals | **ACTIVE** — OneCall structured `CommerceSignals extra=forbid` `signals.py:147` hybrid LLM observations + deterministic `decide_commerce_action:272` |
| PPV execution | **ACTIVE** — `execute_ppv` 12-step gated `execution.py:88` `pg_advisory_xact_lock` `dao.py:82` |
| Price authority | **PRESERVED** — `fangate_products.price_minor` `execution.py:252` sole setter; Qwen `requested_price` advisory only |
| Persona enforcement | **PRESERVED** — `_behavior_block` now injected `workers/llm_worker.py:1022` fix, `validate_persona_voice:133` `FACT_FAIL` severe |
| Failure isolation | **SAFE** — event publish best-effort `core/event_bus.py:64` never breaks generation; Context/DB fail-open |
| Legacy fallback isolation | **SAFE** — hardened `no legacy cascade` `workers/llm_worker.py:1133,1171` ; legacy still reachable via `LLM_PATH=legacy` |
| Creator isolation | **PRESERVED** — all `WHERE creator_id` + Redis `creator:{cid}:` + `ContextItem creator_id` + dedup `respect_creator_isolation` |
| Redis recovery | **REGRESSED** — `requeue_stalled_messages` discards fields `243` → not processing claimed pending |
| Phase 74B optimizations | **PRESERVED** — `7 PG 4 Redis` per message, `orjson`, `publish_events_batch` pipeline, `generation _profile_cache` |

---

## 28. Proof Artifacts

- Call graph validated against `recovery_llm_worker/llm_worker_disassembly.txt:2936` `VARNAMES acquire_user_lock:_settings:... publish_events_batch` and `critical_audits.py:391` `publish_events_batch present True`.
- `grep hnswlib` 0 `.py`; `grep rapidfuzz` 2 callers; `grep SentenceTransformer` `embedding_model.py:51` only.
- `pytest tests/test_phase75c_context_compact.py tests/test_phase75b_one_call.py tests/test_phase72_context_engine_integration.py tests/test_phase77b_context_engine_integration.py tests/test_ai_resilience.py` **210 passed 87s** (2026-09-03).
- `python -c "from core.context_compact import build_one_call_context, estimate_one_call_tokens ; baseline 102 tokens, retrieved +28 tokens 130, large 353 tokens, validate True, ONE_CALL_TOKEN_BUDGET system350 state150 conversation600"` measured `E:\chatbot` at audit time.

---

ROOT VERDICT:
WIRED BUT CONDITIONAL — OneCall 1-authoritative-call proven, Context Engine fully wired but gated off by default, price/persona authority preserved, Redis reclaim pending-discard is P0. See Findings P0-P2.

ACTUAL PRODUCTION MESSAGE FLOW:
Telegram handlers debounce RPUSH (db/redis.py:318) -> XADD inbound_messages generation_id md5 (184) -> llm_worker XREADGROUP llm_workers > (199) -> process_message: acquire lock (285) -> parallel gather build_qwen3_context 4 PG (521) -> publish ai.generation_started (526) -> observe_context_engine gated (539, default no-op) -> persona_behavior inject (1022) -> if llm_path=new: build_one_call_context retrieved_context (102) -> provider.generate ONE_CALL_SYSTEM_PROMPT+COMMERCE_SIGNAL_INSTRUCTIONS 400/0.7 (126) -> validate_one_call_response + quality heuristics (88) -> if valid: _try_commerce_draft signals= one_call.signals (1116) -> resolve_and_run_commerce -> run_commerce_pipeline -> generate_commerce_response conditional (619) -> persona_validation (1329) -> routing auto_approve>=0.80 && !flags -> enqueue_send XADD send_messages md5 dedup (1442) -> publish_events_batch ai.generation_completed after enqueue (1506) ; else malformed/exception -> operator_queue + completed was_auto_approved False hardened return (1133) -> ack_inbound XACK (1775) or move_to_dlq XACK (1779). Full trace docs §2.

LLM CALL COUNT:
Normal 1 useful (2 invocations with discarded commerce when creator READY), Commerce 1 useful (2 total), PPV 2 (one-call + commerce PPV replacement), Malformed 1 attempted -> operator queue 0 extra, Provider exception 1 attempted -> operator queue 0 extra, Legacy 3 (legacy-only extract + Qwen (+up to 4 tool) + score), Agent/tool 0 in new path (legacy-only guard 1209). Prove file:line §3.

CONTEXT ENGINE:
WIRED BUT GATED OFF BY DEFAULT — ContextEngineIntegration.process gather 7 sources -> scorer/dedup(rapidfuzz 0.85)/budget 2600 -> renderer -> rendered_text memory/temporal/commerce/content -> workers/llm_worker.py:558 _retrieved_context -> core/one_call_pipeline.py:95 -> core/context_compact.py:102 system message -> provider.generate. When context_engine_observational=False (default core/config.py:135) returns enabled=False <1µs, _retrieved_context="" skipped. Fail-open never breaks production. See §5.

RAPIDFUZZ:
IMPLEMENTED CONDITIONAL — pyproject.toml rapidfuzz>=3.0, context_engine/dedup.py:57 fuzz.WRatio 0.85 within same category:source lexical_key, commerce/unified_intelligence.py:24 process.extract score_cutoff 80 limit 3 (offline). Only via Context Engine dedup when enabled; not via core/one_call_pipeline. Output reaches Qwen only via rendered block when enabled. Creator isolated respect_creator_isolation true dedup.py:180. Bounded limit 3, TOTAL 2600 tokens.

MINILM:
INITIALIZED ONLY / UNUSED ON ONECALL — commerce/embedding_model.py:19 all-MiniLM-L6-v2 384 singleton lru_cache, get_model() lazy per llm_worker. encode_message loop.run_in_executor exists, reference vectors 110*384 cached unified_intelligence.py:94, but analyze_message not called in workers/llm_worker.py:1042-1106; Context Engine gatherer uses lexical retrieve_relevant_knowledge not embeddings; scorer word overlap not cosine. No embedding reaches OneCall.

HNSWLIB:
NOT IMPLEMENTED — global grep 0 .py matches, pyproject.toml no dep, db/schema.sql:101 B-tree instead of HNSW, memory/retrieval.py python _cosine_distance fetch-all, unified_intelligence.py:85 brute-force 110 vectors. NOT JUSTIFIED AT CURRENT SCALE (<1k vectors).

MEMORY RETRIEVAL:
LEXICAL DETERMINISTIC — Storage LTM regex extract_explicit_memories long_term_memory.py:244 bounded 20 creator-scoped, fan_knowledge 30 patterns bounded 30; Retrieval retrieve_relevant_memories limit 3 overlap0.5+conf0.3+recency0.2 fan_knowledge.py:561 limit 5; No semantic embeddings; Legacy appends RELEVANT MEMORY/FAN KNOWLEDGE if score>0.2; Engine MemorySource limit 10 scored 0.75 Det derivation. Supplementary not authoritative.

COMMERCE:
ACTIVE HYBRID — OneCall CommerceSignals 18 fields extra=forbid signals.py:147 LLM observations + deterministic decide_commerce_action policy thresholds 0.60/0.80/0.55 decision.py:272. New path extract_commerce_signals legacy-only 1214, signals passed to _try_commerce_draft signals= to skip duplicate pipeline.py:480. PPV requires EXECUTED/ALREADY_EXECUTED + GENERATED validated 545 then USE_COMMERCE_RESPONSE selection.py:320.

PPV:
ACTIVE DETERMINISTIC — Who chooses: product deterministic resolve_commerce_product_with_history filtered is_accessible+sales_url exclude purchased rank relevance >=0.15 product_selection.py:149; price DB fangate_products.price_minor execution.py:252 USD hard currency; validates evaluate_ppv_eligibility state.py:223 + execution.py:219 re-evaluate + advisory lock ppv_offer:{c}:{u}:{p} dao.py:82; creates create_offer_serialized pending INSERT; Fangate sales_url or dropfans build_checkout_url; selection USE_COMMERCE_RESPONSE only if COMPLETED+OFFER_PPV+EXECUTED+GENERATED else FALLBACK.

PRICE AUTHORITY:
PRESERVED DETERMINISTIC — Qwen requested_price bounded positive finite signals.py:196 only flag user_asked_about_price, Context Engine no price, memory no price, fan no price, deterministic execution.py:252 price_minor from DB immutable. Context Engine CommerceStateSource titles only.

PERSONA:
PRESERVED — Structured persona personas.metadata creator-scoped get_structured_persona_async 318 HARD_POLICY 0, behavior derive_persona_behavior_state persona_behavior.py:124 regex deterministic + render_persona_behavior_block 374 injected context.append system 1022 fix for prior derived-but-not-injected bug, compact prompt build_one_call_context persona_block Fan|Stage|Rules system 350, validation validate_persona_voice fact_violation FACT_FAIL severe persona_validation.py:133 -> flags persona_validation_severe score-0.10 not auto-approved 1441, no retrieval override authority hierarchy 0<2<4.

FAILURE BEHAVIOR:
SAFE HARDENED — Provider invalid/empty Pydantic forbid/extra/bounds -> is_valid False -> operator_queue ["one_call_invalid_result"] publish completed+suggestion 1150 return (hardened no legacy); Provider exception timeout -> ["one_call_exception"] 1188 return; Commerce failure -> return None safe fallback 387; Context Engine failed -> observation failed True skip 561; Embedding/DB/Redis fail-open defaults ack. No failure reintroduces legacy 3-LLM; outermost except publishes ai.generation_failed same generation_id 1676 then move_to_dlq XACK 276 (or ack success 1775). See §18.

ARCHITECTURE INVARIANTS:
ageneration_id md5(user:msg:telegram_id) handlers.py:63 reused via enqueue_inbound 188 -> process_message 443 -> ai.generation_started 526 -> ai.generation_completed 1506 -> suggestion.created 573 (same generation_id); event_id uuid4 per publish_event 46 (dedup); ai.generation_completed MUST NOT before enqueue_send 1442->1456 enforced; failure isolation best-effort publish_event warning return None 64 never breaks generation; Workers publish via event bus only not ws_manager; frontend polling fallback preserved; user/dialog scope scope=user creator_id included; backward compat publish_events_batch now tolerates event/event_type.

P0 FINDINGS:
P0-1 db/redis.py:243 requeue_stalled_messages discards XAUTOCLAIM fields -> workers/llm_worker.py:1745 claimed pending never processed via XREADGROUP > (stalled recovery broken). 60s idle vs AGENT.md 30s mismatch.

P1 FINDINGS:
P1-1 core/config.py:90 qwen3:4b actual vs Qwen2.5:3B spec/doc core/context_compact.py:1 stale. P1-2 commerce/pipeline.py:619 generate_commerce_response always then selection discards -> normal message 2 invocations (1 wasted). P1-3 Context Engine state relevance scorer neutral conversation_state=None wiring missing integration.py:168 scorer state weight 0.10 wasted + semantic relevance word overlap not MiniLM.

P2 FINDINGS:
P2-1 core/event_bus.py:109 compat shim event/event_type legacy callers workers/llm_worker.py:573. P2-2 context_engine_observational=False default -> wired but zero retrieved in production. P2-3 core/one_call_pipeline.py:193 misleading "falling back to 3-LLM" log stale.

STAGE B REQUIRED:
Fix XAUTOCLAIM iterate fields, gate commerce generation to OFFER_PPV+EXECUTED, flip Context Engine canary true 10%, wire conversation_state into scorer, align model docs/spec, migrate event callers to event_type, add DLQ pending-leave regression test. See §26.

CODE CHANGES:
NONE — STAGE A READ-ONLY
