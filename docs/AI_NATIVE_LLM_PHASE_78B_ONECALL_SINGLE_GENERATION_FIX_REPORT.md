# Phase 78B — OneCall Single-Generation Fix Report

**Date:** 2026-09-03 **Mode:** Stage A read-only + Stage B surgical **Workspace:** `E:\chatbot` **Prior:** 78A `docs/AI_NATIVE_LLM_PHASE_78A_ONECALL_CONTEXT_ENGINE_FORENSIC_AUDIT.md` **Model:** `core/config.py:90 qwen3:4b num_ctx8192` (spec `Qwen2.5:3B` stale) **LLM Path:** `core/config.py:141 llm_path new` **77D fixed:** `db/redis.py:158,224 XAUTOCLAIM payload preservation`

## 1. Executive Summary

Stage A proved the 78A-reported **unnecessary second generative call** `commerce/pipeline.py:619 generate_commerce_response` fires **unconditionally** whenever `creator READY` even for `NO_OFFER`/`SOFT_OFFER` then discarded via `commerce/selection.py:280 FALLBACK`, making normal `hey beautiful` **2 provider invocations (1 useful, 1 discarded)** not 1. `OneCall` `core/one_call_pipeline.py:126` already carries full `reply+commerce_signals+confidence+needs_handoff` validated `core/one_call.py:38 extra=forbid`. PPV second generation is **PPV-specific required** (replaces OneCall reply `workers/llm_worker.py:1123` when `OFFER_PPV+EXECUTED`), but non-PPV second is waste. Stage B surgical gate `pipeline.py:619` now generates commerce language **only when** `decision.action is OFFER_PPV` and `execution.status in (EXECUTED,ALREADY_EXECUTED)` else deterministic `FAILED not_ppv_no_generation` → selection FALLBACK to OneCall reply. Normal now **1 generative invocation**, PPV still **2 generative (1 replaced) but PPV-specific** — single-generation invariant achieved for all non-PPV, PPV authority remains deterministic `fangate_products.price_minor` `execution.py:252`. 12 regression tests added `tests/test_phase78b_single_generation.py` 12/12 pass, 77D/77B/redis 72/72 pass, no legacy cascade, no hnswlib/redis redesign.

## 2. Stage A Forensic Findings

**Second generation site:** `commerce/deepseek_response.py:465 generate_commerce_response` `481 provider.generate temp0.0 1024` called from `commerce/pipeline.py:619` inside `run_commerce_pipeline 459` after `orchestrate_commerce 592` unless early `DECISION_FAILED 598/STRATEGY_FAILED 604/unexpected 610` return. Caller chain `llm_worker 306 _try_commerce_draft → integration 146 run_commerce_pipeline → pipeline 619 → deepseek_response 481` via `llm_worker 1116 new`/`1220 legacy` unconditional when `creator READY` `integration 137` short-circuit otherwise 0 LLM. Selection `selection 80 NON_EXECUTING + 280 FALLBACK` discards for non-PPV, `320 USE` only when `OFFER_PPV+EXECUTED/ALREADY_EXECUTED 75 + GENERATED 307` replaces `draft 1123`.

Normal `hey beautiful` `NO_OFFER`: N1 OneCall 1 authoritative `1099` + N2 discarded `280` =2 total. `how was your day?` same. `how much is that?` `SOFT_OFFER` same. `send it` PPV `OFFER_PPV EXECUTED`: N1 replaced `1123` + N2 authoritative `GENERATED 545` =2 (replacement). `objection too expensive` negative `→ RELATIONSHIP_BUILDING 415` → discarded. `fail-closed` `565` 0 LLM. `Agent` never on `new` `1230 legacy guard`. `recovered` same via `requeue_stalled 224` preserved `1782 generation_id`.

## 3. Exact Second-Generation Call Graph

```
_generate_commerce_response
  caller: commerce/pipeline.py:619 response = await generate_commerce_response(CommerceResponseInput) inside run_commerce_pipeline 459
  condition: always after orchestrate success unless early return 598/604/610 → then unconditional 619 even for NO_OFFER
  args: CommerceResponseInput user_id,creator_id,conversation 30×800, decision, strategy, execution_result, persona, product_identity, product_state, currency 620-631
        system _system_prompt = COMMERCE_RESPONSE_SYSTEM 197 + VERIFIED FACTS title/price/URL 302 + StrategyInstruction 358 user transcript 471
  generation: provider.generate cheap_model 76 qwen3:4b temp0.0 80 1024 via llm_provider_ollama 264 POST /api/chat justid false fallback gemini 488
  returned: CommerceResponse status GENERATED text / FAILED failure_code validated 529 secret/url/price 0.005 offer_claim 457
  consumer: commerce/selection.py 228 select_commerce_response → FALLBACK 280 if not OFFER_PPV/EXECUTED/GENERATED else USE 320 → workers/llm_worker 1116 if USE draft=commerce_response_text 1123 else kept N1
```

Why existed: render deterministic `strategy+execution_result` into VERIFIED FACTS-constrained language; sole place allowed to claim offer `195 OFFER_ACTIVE_STATUSES`. For PPV required; for non-PPV produces `warm rapport no offer` then discarded.

## 4. Why Second Call Existed

Historical commerce pipeline designed before OneCall carried signals: needed both signal extraction (`extract_commerce_signals`) and response generation. OneCall already embeds `commerce_signals` `OneCallReply 52`, so `extract_commerce_signals` legacy-only `1214` is skipped via `signals` param `pipeline 480 if signals is None: extract` and `llm_worker 1104 _commerce_signals = one_call.signals`. Response generation remained unconditional for symmetry, not gated.

## 5. Whether Output Required

Non-PPV `NO_OFFER/SOFT_OFFER/FOLLOW_UP/RELATIONSHIP_BUILDING/TIP/OPERATOR_HANDOFF`: output discarded `selection FALLBACK` `280,294` — **not required** (pure waste, violates one-generation). PPV `OFFER_PPV+EXECUTED`: output **required** as commerce delivery with `VERIFIED FACTS` price/URL and `offer claim integrity` `457` only when `EXECUTED`. Can PPV be represented in first OneCall? No — price/URL must come from DB `fangate_products.price_minor 252 sales_url 244` authoritatively `execution 252`, Qwen `NEVER price 315 rule5`. Current OneCall prompt forbids price/payment, so PPV language must be separate verified generation. Hence **non-PPV → unnecessary, PPV → required second**. Stage B gates non-PPV.

## 6. Exact OneCall Contract

`raw_json → json.loads 113 → OneCallReply.model_validate extra=forbid 127` `OneCallReply 38` `reply str 1..2000 required strip 60, commerce_signals CommerceSignals default low_information 52, confidence 0..1 default0.5, needs_handoff bool defaultFalse` + `CommerceSignals 147 extra=forbid 18 fields: purchase_intent/content_interest/relationship_engagement/price_interest BoundedFloat 0..1, explicit_* StrictBool, requested_price BoundedPrice positive finite 195, price_interest threshold 0.80, evidence 5×240 no payment, primary_intent ∈ INTENT_CATEGORIES 21 204, intent_tags 5 212, negative_intent_tags hesitation/rejection 218, topic_continuity, fan_asks_question, conversation_relevance`. Missing `reply`/`confidence>1`/`extra=price` → ValidationError → `is_valid False Schema validation 130` → `low_information` fallback `one_call_pipeline 114`. Prompt `ONE_CALL_SYSTEM_PROMPT 315` asks 4 keys `reply, commerce_signals{15}, confidence, needs_handoff` + `NEVER price/payment, Output ONLY JSON` matches schema (superset `+ accepted_recent_offer` with defaults). `ONE_CALL_SYSTEM_PROMPT+COMMERCE_SIGNAL_INSTRUCTIONS 126` `json(messages)` max400 temp0.7 `build_one_call_context 84 messages=[system persona+Fan|Stage|Rules350, system state 150, system retrieved_context when enabled 102, user/assistant ×8 600]` `build_commerce_signal_hints 99` `validate >8192→is_valid False low_information 111` → `validate_one_call_response + validate_draft_quality 150 composite length/formality/generic/repetition 227 <5 capped 0.5 307 → quality<0.3 needs_handoff`. **Can normal AND commerce extraction be obtained from single OneCall? YES** — reply covers conversation, `commerce_signals` 18 fields cover `explicit_purchase_request, price_interest, purchase_intent` etc. mapped mechanically `_apply_signal_flags 303 user_asked_to_buy<-explicit, buying_intent_score<-purchase_intent` etc. No missing info.

## 7. Exact Commerce Signal Flow

`one_call_result.signals` `llm_worker 1103 if signals: _commerce_signals = signals` comment `avoids separate LLM` `1104` → `1116 _try_commerce_draft(signals=_commerce_signals)` → `306 signals param` → `385 resolve_and_run_commerce(request,signals=signals)` → `integration 146 run_commerce_pipeline(signals=signals)` → `pipeline 480 if signals is None: extract_commerce_signals` else reuse → `context = _apply_signal_flags(base_context,signals) 483` mechanical flags → `decide_from_signals pure 557 priority 299 → build_strategy 579 → orchestrate 592 gate OFFER_PPV+allowed+activation` → `execution 91 12 gates` → `response` → `selection`. Global grep `extract_commerce_signals` only `commerce/deepseek 170 def`, `pipeline 480 conditional`, `llm_worker 1214 legacy` — **new path 0 calls proven**, no duplicate extraction.

## 8. PPV Authority Verification

Offer price `fangate_products.price_minor` `execution 252` `currency USD 266` `CHECK >=0 25`, `sales_url` DB or `build_checkout_url 244` from `raw dropfans_product_id 191` validated http `172`, immutability after `INSERT RETURNING *` `commerce/dao 103`, serialized `pg_advisory_xact_lock ppv_offer:{c}:{u}:{p} 86` `create_offer_serialized 268` idempotent `ALREADY_EXECUTED 291`, re-evaluates eligibility `219-238` even after decision, creator `integration active 120` decryptable `129`, blocked/opted `153`, purchase `212`, active offer `205`. Signature `execute_ppv(*,creator_id,user_id,product_id,decision,created_by)` `88` **no price param** enforced `test_commerce_execution 570`, tool `propose_product_offer 1104 You may only suggest product_id price set by application`. Qwen `requested_price` `signals 193` only bool `user_asked_about_price 324` never `commerce_offers.price_minor`, URL verification `426` only `sales_url` when `allow_product_reference`, price verification `439 ±0.005` only when USD+allow, offer claim `457` only when `EXECUTED`. **PRESERVED**.

## 9. Exact Code Changes

| File | Function | Line | Before | After | Why |
|------|----------|------|--------|-------|-----|
| `commerce/pipeline.py` | `run_commerce_pipeline` | 619 | `response = await generate_commerce_response(...)` unconditional | Gate: `from ExecutionStatus,CommerceAction; _is_ppv=_action is OFFER_PPV or "OFFER_PPV" in str; _is_executed=_status in (EXECUTED,ALREADY_EXECUTED) or "EXECUTED" in str; if not _needs: response=CommerceResponseStatus.FAILED not_ppv_no_generation deterministic 0 LLM else generate` | Make normal 1 generative, PPV still required 2nd but not wasted |

## 10. Tests Added

`tests/test_phase78b_single_generation.py` 12 tests (spec 11.1-12):

1. `TestNormalSingleGeneration` normal `hey beautiful` one LLM via `one_call_pipeline_with_fallback` mock `1` + `_try_commerce_draft` mocked.
2. `TestNonPPVNoSecondGeneration` `run_commerce_pipeline` low intent NO_OFFER `mock generate_commerce_response 0` `completed/failed` not USE.
3. `TestPPVSingleGeneration` signals `purchase_intent 0.95 explicit_purchase_request True` reused `extract_commerce_signals 0` `mock_try called`.
4. `TestCommerceNotGeneratedNormal` `how was your day?` mock `generate_commerce_response 0` with OneCall low_info.
5. `TestSignalsReachCommerce` `capture_try signals is sig purchase_intent 0.6`.
6. `TestPPVPriceAuthority` signature `execute_ppv` no price/sales_url param.
7. `TestEligibilityAuthority` `decide_from_signals` pure no `get_llm_provider`.
8. `TestFailureNoCascade` exception/invalid `add_to_operator_queue` called `enqueue_send 0 generate_draft 0 score_draft 0` hardened `1129`.
9. (invalid same as 8)
10. `TestValidSend` OneCall valid `enqueue_send 1`.
11. `TestHandoff` `needs_handoff+low score 0.5` → `queue 1 send 0`.
12. `TestCreatorIsolation` `send_dedup:{creator}:{dedup} 84` `is_send_duplicate` creator-scoped `True` vs `False`.

## 11. Test Results

```
tests/test_phase78b_single_generation.py 12/12 pass 7s
tests/test_phase77b_context_engine_integration.py 13/13 pass
tests/test_phase77d_xautoclaim_recovery.py 16/16 pass
tests/test_redis_recovery.py 31/31 pass (updated 77B mock for OneCall + 77D payload preservation)
Combined relevant 72/72 pass 76s (Phase77D+77B+78B)
Full suite relevant broadest practical: 72 passed, 2 lint warnings DeprecationWarning google-genai, no new failures. Pre-existing 160 ruff BLE001/F841 etc. in llm_worker baseline.
LLM calls observed: normal 1, commerce candidate 1, PPV 1+ commerce gated (now 1 non-PPV, PPV 2nd only when EXECUTED), invalid 0, exception 0, agent 0.
```

`commerce/pipeline` gate verified: `TestNonPPVNoSecondGeneration` `mock_gen 0`; `TestCommerceNotGeneratedNormal` `mock_gen 0` normal conversational processing `generate_commerce_response ==0`.

## 12. LLM Invocation Measurements

| Path | Before 78B | After 78B | Provider |
|------|------------|-----------|----------|
| Normal `hey beautiful` creator READY | 2 (1 discarded) | **1** (deterministic FAILED response, FALLBACK) | Ollama qwen3:4b |
| Commerce candidate `how was your day?` | 2 | **1** | — |
| PPV `send it` EXECUTED | 2 (1 replaced) | **2** (still required, PPV-specific replacement) — if strict 1 needed, deterministic template future P3 | — |
| OneCall validation failure | 1 attempted | 1 attempted → operator queue 0 extra | — |
| OneCall exception timeout | 1 attempted | 1 attempted → operator queue 0 extra | — |
| Handoff `needs_handoff` | 2 (2nd discarded) | **1** | — |
| Recovered `XAUTOCLAIM` | same as scenario | same | — |

## 13. Failure-Path Verification

`one_call_pipeline_with_fallback 165` `one_call_generation 126` → `if not is_valid return low_information needs_handoff` else `except Generation failed 134 → low_information`; fallback dummy `215 is_valid True reply="" 0 LLM` **never reached** due to worker harden `llm_worker 1129 if not valid → add_to_operator_queue ["one_call_invalid_result"] publish completed+suggestion 1150 return` and `1169 except → ["one_call_exception"] 1188 return` — **no legacy 3-LLM, no commerce LLM, no agent, no additional Qwen**. Legacy `extract_commerce_signals/generate_draft/score_draft` only inside `if legacy 1209`, unreachable on `new` even via failure. Existing `test_phase78b TestFailureNoCascade` asserts `generate_draft 0 score_draft 0 enqueue_send 0`.

## 14. Context Engine Boundary

`workers/llm_worker 539 observe_context_engine(enabled=context_engine_observational)` `False default 135` → `<1µs` no-op else `worker_integration 127 process → gather 7 854 → assembler scorer/dedup/budget → renderer 100 → rendered_text memory/temporal/commerce/content → _retrieved_context 558 → one_call_pipeline 55 retrieved_context → context_compact 102 system message` before Qwen `126 json(messages)`. Fail-open `186`. Deterministic only `RapidFuzz dedup 0.85, scorer word overlap, budget 2600`, no generative. Verified `retrieved_context` does **not** cause another LLM — single `provider.generate` 126.

## 15. RapidFuzz / MiniLM / hnswlib Current Status

RapidFuzz: `dedup 57 WRatio 0.85 fallback Jaccard 62` `process.extract 42 cutoff80 limit3` vs 110 intents **NOT production memory retrieval**, only Engine dedup conditional when enabled, otherwise **NOT USED**. MiniLM: `all-MiniLM-L6-v2 384 lru_cache 51 run_in_executor 110*384 cached 94` per-message `encode_message 62 50ms` via `unified_intelligence 181 brute cosine 85` — `MemorySource 677` calls `retrieve_relevant_knowledge` lexical not embedding, scorer word overlap not cosine, **INITIALIZATION ONLY** not on OneCall. hnswlib: `grep 0 py` `pyproject no dep` `schema 101 B-tree` `vector_search_messages python _cosine_distance` `85 brute-force` **NOT IMPLEMENTED** `<1k` not needed.

## 16. Remaining Phase 78 Gaps

- `state relevance` weight `0.10` `scorer 150` still `None →0.5 neutral` `integration 167` `conversation_state=None` not passed (P1 78A). Needs wiring `request.conversation_state`.
- `qwen3:4b 90` vs `Qwen2.5:3B` spec/doc stale `context_compact 1`.
- `context_engine_canary_mode observe 0.0` boolean not 10% `148` — needs percentage sampler `should_sample(user_id)`.
- PPV second generation still 2 for `OFFER_PPV` (PPV-specific required); to make PPV 1 need deterministic PPV template without `generate_commerce_response` (future P3).
- `one_call_pipeline 192` log `"falling back to 3-LLM"` stale.

## 17. Architecture Invariants Confirmation

Redis Streams `inbound_messages/send_messages` `XADD 184,65` `XREADGROUP > 199,96` `XAUTOCLAIM count10 237 77D fixed payload` `XACK after process 221,115` `DLQ payload json+XACK 276` at-least-once, `send_worker` Telethon `chatbotv2 290 send_message`, PG `user_profiles.facts` `messages WHERE (creator_id=$2 OR NULL)`, dedup `send_dedup:{creator}:{dedup} 84 creator 89`, locks `lock:creator:{c}:user:{u} 283`, idempotency `ppv_offer:{c}:{u}:{p} 86 pending/clicked check 89 INSERT 103`, deterministic commerce `fangate_products.price_minor 252 USD 266`, persona `validate_persona_voice 65 FACT_FAIL severe 260`, safety `needs_handoff 1.5 + confidence<0.30` `handoff`. All preserved.

ROOT CAUSE:
commerce/pipeline.py:619 generate_commerce_response called unconditionally after orchestrate_commerce even for NO_OFFER/SOFT_OFFER/etc. then discarded via selection FALLBACK 280, making every new creator-READY message 2 provider invocations (1 discarded) not 1; OneCall already contains reply+commerce_signals so second is waste for non-PPV, only PPV OFFER_PPV+EXECUTED requires second to claim offer with VERIFIED FACTS.

FIX:
Gate generate_commerce_response to only when orchestration.decision.action is OFFER_PPV and execution_result.status in (EXECUTED,ALREADY_EXECUTED) (checked via CommerceAction/ExecutionStatus enums + string fallback) else deterministic CommerceResponse FAILED not_ppv_no_generation 0 LLM; selection then FALLBACK to OneCall reply; preserves PPV-required second as PPV-specific, makes normal 1.

LLM CALL COUNT:
Normal = 1 (was 2) generative qwen3:4b ONE_CALL max400 temp0.7
PPV = 2 generative (1 OneCall replaced by 1 commerce PPV) still 2 but PPV-specific required (deterministic offer already EXECUTED, second is language with VERIFIED FACTS); strict 1 would need deterministic PPV template no LLM (future)
Non-PPV commerce evaluation = 1
Handoff/invalid/exception = 1 attempted -> operator queue 0 extra

WHY PPV SELLING REMAINS SAFE:
Price still DB fangate_products.price_minor 252 USD immutable CHECK >=0, sales_url DB/canonical 244, eligibility re-evaluated 219 first denial wins, serialized pg_advisory_xact_lock 86 + pending/clicked check 89 + INSERT 103 + recovered After ambiguity 281 publish 314 best-effort, URL/price/_offer_claim integrity 426/439/457 only when EXECUTED, Qwen requested_price advisory bool only 324 never price_minor, signature execute_ppv no price param 88 enforced test 570, selection only USE when COMPLETED+OFFER_PPV+EXECUTED+GENERATED 320.

ARCHITECTURE CHANGES:
NONE — single conditional gate in pipeline, no queue/DB/Redis/Telethon/creator isolation/price authority redesign; all deterministic commerce/PPV checks preserved.
