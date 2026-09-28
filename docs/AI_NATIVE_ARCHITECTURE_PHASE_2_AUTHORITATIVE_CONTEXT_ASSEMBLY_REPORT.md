# AI_NATIVE ARCHITECTURE PHASE 2 — AUTHORITATIVE CONTEXT ASSEMBLY

**Date:** 2026-09-09 **Workspace:** `E:\chatbot` **Status:** COMPLETE  
**Prior:** Phase 1 canonical 100% `docs/AI_NATIVE_ARCHITECTURE_PHASE_1_CONTEXT_ENGINE_CANONICAL_RUNTIME_REPORT.md`  
**Model:** `core/config.py:90 qwen3:4b num_ctx 8192` **LLM Path:** `llm_path new` **Budget:** `TOTAL 2600 ONE_CALL 1150`

---

## 1. ROOT CAUSE

DUAL CONTEXT ASSEMBLY. No single snapshot controlled the full pipeline:

```
process_message()
  ↓ build_qwen3_context()  — parallel get_user/profile/recent20/summary + persona + derive_conversation_state + build_llm_context + LTM/knowledge + trim 1400
  ↓ derive_conversation_state() AGAIN in worker (context vs recent slice, two objects)
  ↓ ContextEngineIntegration.process() — gather 7 sources (each re-fetches DB: get_user_persona, get_user, get_recent_messages, commerce offers via SQL, fan_knowledge hybrid, temporal)
  ↓ score → dedup (0.85) → budget TOTAL2600 (chars/4) → render
  ↓ _retrieved_context = join(memory+temporal+commerce+content) — STRIPS authority markers (system/state dropped, markers stripped)
  ↓ build_one_call_context() — REBUILDs system/state again from raw user/profile/summary/conversation_state (tiktoken) 1150 + commerce_hints
  ↓ provider.generate ONE_CALL 400
```

* No `ONE TURN = ONE SNAPSHOT` invariant.
* Same state fetched 2-3× (`get_user` 3×, `get_user_profile` 2×, `get_recent_messages` 2×, `persona` 2×).
* `conversation_state` derived twice with different slices.
* Authority markers `renderer.py:191` produced then stripped in `worker_integration.py:148-158`.
* Conflict resolution implicit via Qwen prompt priority list, not code (cross-category `city=Nairobi` duplicate not resolved).
* Two token estimators (`budget.py chars/4` vs `memory/context.py tiktoken gpt-4`) diverged.
* SYSTEM+STATE budget guarantee only via score ordering, not explicit.

Audit verdict `READY FOR IMPLEMENTATION` quantified: CE 100% canonical hybrid (`Wratio80+MiniLM 0.30` merge 10 → dedup 0.85 → rank 5 weights → 2600) but only for retrieval slice (~28 tokens); `build_qwen3_context` still emitted large blocks outside CE budget/ranking.

---

## 2. FIX

Establish ONE AUTHORITATIVE CONTEXT ASSEMBLY PIPELINE reusing existing components, no retrieval/commerce/redis redesign:

**Snapshot type** `context_engine/models.py:AuthoritativeState` frozen dataclass (creator_id, user_id, generation_id, current_message, timestamp, user, profile, persona, structured_persona, persona_name, recent_messages tuple, summary, summary_age_days, conversation_state + dict, commerce_context_text + llm_context, fan_knowledge/long_term snapshots, metadata). `to_conversation_state_dict()` single helper.

**Coordinated acquisition** `context_engine/authoritative_assembly.py:assemble_authoritative_context()` — single `asyncio.gather(get_user, get_user_profile, get_recent_messages 20 creator-scoped, get_latest_summary, get_structured_persona)` parallel (preserves Phase 44C), then fan_knowledge interest isolation (reuse profile), persona fetch with Redis cache, persona_name extraction, `trim_to_token_budget 800` + `derive_conversation_state(history+user)` **ONCE** (`_derive_call_count` for Test A), then `build_llm_context(user_data=..., recent_messages=..., summary=...)` with B2 reuse (no redundant PG), returns `AuthoritativeState`. No LLM, no writes, deterministic, creator-scoped.

**Snapshot-aware gatherers** `context_engine/gatherer.py:GathererConfig.authoritative_state`, `ContextRequest.authoritative_state` (`integration.py`), `PersonaSource`/`FanStateSource`/`ConversationHistorySource`/`CommerceStateSource` prefer `config.authoritative_state` fields over DB re-fetch (fallback to DB if snapshot empty). Retrieval hybrid unchanged.

**Explicit conflict resolution** `context_engine/assembler.py:_resolve_conflicts()` deterministic fact-level before ranking: fact identity `category:subject` from `metadata.subject` or `content` `key=` pattern; grouped by `(creator_id, identity)`; winner by authority `HARD_POLICY 0 < RULE 1 < DERIV 2 …` → status rank `CURRENT 3 > TEMPORARY 2 > HISTORICAL 1 > EXPIRED 0` → timestamp newer → priority higher → lexicographically smaller content. `assemble()` pipeline now `conflict → score → dedup → budget`.

**Scoring unchanged** `scorer.py` weights `source 0.15 topic 0.30 recency 0.20 importance 0.20 state_relevance 0.10 authority 0.05`, single `conversation_state_dict` from snapshot fed to `ContextScorer`.

**Dedup preserved** `dedup.py` `0.85 WRatio` `respect_creator_isolation=True` untouched.

**Budget unified** `budget.py:estimate_tokens()` now tries `tiktoken gpt-4` then `cl100k_base` fallback to `len//4`; deterministic, same as OneCall. `CATEGORY_BUDGETS` 400/200/200/150/150/50/100/800/200 + `TOTAL 2600` preserved. `try_allocate_or_truncate` unchanged so SYSTEM+STATE (400+200) always fit (global 2600 > sum 2250). Degradation metadata includes `conflict_dropped`.

**Rendering authority-preserved** `renderer.py:_render_category(include_authority_markers=True)` now always emits `[AUTHORITATIVE]/[DETERMINISTIC]/[DERIVED]/[CONTEXT]/[ADVISORY]` (Phase 2 change), `worker_integration.py:observe_context_engine()` now renders all blocks with explicit headers `[CURRENT AUTHORITATIVE STATE - SYSTEM]` `[CURRENT AUTHORITATIVE STATE]` `[CURRENT AUTHORITATIVE STATE - COMMERCE]` `[RETRIEVED KNOWLEDGE - DERIVED]` `[RETRIEVED KNOWLEDGE - TEMPORAL]` `[RETRIEVED CONTENT]` joined by blank lines (previously stripped to 3 blocks).

**OneCall from snapshot** `core/context_compact.py:build_one_call_from_snapshot()` consumes `pipeline_result.snapshot` or `authoritative_state` directly: prefers `pipeline_result.messages` (already rendered with labels) trimmed to 8, else `renderer.render_to_messages(snapshot)` (labels `STATE:` `MEMORY:` etc), fallback to minimal system/state from snapshot persona/user/profile. `core/one_call_pipeline.py:one_call_generation()` new optional `authoritative_state, pipeline_result` – when present uses `build_one_call_from_snapshot` + advisory hints with label `[ADVISORY / DERIVED - COMMERCE HINTS]`; otherwise legacy `build_one_call_context` path for rollback.

**Worker integration** `workers/llm_worker.py` canonical new path after lock:
```
_authoritative_state = await assemble_authoritative_context(creator_id,user_id,current_message,generation_id,persona_override, snapshot)
context = conversion of snapshot.recent_messages → legacy shape for commerce/behavior callers
_conv_state = reuse _authoritative_state.conversation_state (no second derive)
_context_engine_observation = await observe_context_engine(..., authoritative_state=_authoritative_state, conversation_state=_ce_conv_state)
_one_call_result = await one_call_pipeline_with_fallback(..., authoritative_state, pipeline_result=observation.pipeline_result)
```
Legacy `build_qwen3_context` still called when `llm_path != new` or on snapshot exception (fallback). Single derive, single DB fetch, same snapshot to CE and OneCall.

No other system redesigned.

---

## 3. RUNTIME BEFORE

```
Telegram handlers debounce SETNX → XADD inbound gen md5
 → XREADGROUP llm_workers
 → process_message acquire_user_lock creator-scoped → upsert_user
 → get_structured_persona_async
 → build_qwen3_context (parallel 4 + fan_knowledge isolation + build_llm_context B2 + derive conversation_state) 15-60ms
 → publish started
 → LTM/fan_knowledge extract
 → derive_conversation_state AGAIN from context (different slice)
 → get_last_user/profile fallbacks (3rd fetch)
 → build_conversational_commerce_state
 → persona_behavior (retrieve_relevant_knowledge)
 → observe_context_engine (sample hash creator:user, enabled Raw, _ce_conv_state dict; ContextRequest without snapshot → gather 7 sources re-fetch DB → hybrid WRatio80×5+MiniLM 0.30×5 merge 10 → score 5 weights → dedup 0.85 → budget 2600 chars/4 → render → _retrieved_context = memory+temporal+commerce+content stripped)
 → one_call_pipeline_with_fallback (build_one_call_context from raw user/profile/persona_name etc rebuild system/state 350+150 → trim conversation 600 → json(messages) → Ollama qwen3:4b 400 → Pydantic extra=forbid → quality deterministic) 1 gen
 → _try_commerce_draft (derive state AGAIN for product_history)
 → persona validation
 → enqueue_send or operator_queue
```

CE 100% but only retrieval slice; two state builders; markers stripped; conflicts implicit.

---

## 4. RUNTIME AFTER

```
Telegram handlers same
 → XREADGROUP
 → process_message lock → upsert_user → get_structured_persona_async (once)
 → assemble_authoritative_context (parallel get_user/profile/recent20/summary/struct; interest isolation; persona fetch cache; trim 800; derive conversation_state ONCE count 1; build_llm_context reuse B2 → AuthoritativeState creation) ~15-60ms measured 0.3ms mocked
 → publish started (same)
 → LTM/fan_knowledge extract (still)
 → _conv_state = reuse _authoritative_state.conversation_state (0 extra derive) ; _user_for_state / _cached_profile = snapshot.user/profile (0 extra fetch)
 → build_conversational_commerce_state (same)
 → persona_behavior (same)
 → observe_context_engine (same hash sample, now with authoritative_state → ContextRequest(authoritative_state) → gatherers prefer snapshot (persona/state/commerce/history snapshots) → conflict resolve → score with _ce_conv_state (single) → dedup 0.85 → budget 2600 tiktoken → render → rendered_text = [CURRENT AUTHORITATIVE STATE] etc with markers)
 → one_call_pipeline_with_fallback (build_one_call_from_snapshot using pipeline_result.messages or snapshot.render_to_messages with labels; commerce hints [ADVISORY]) → same Ollama qwen3:4b 400 → Pydantic
 → _try_commerce_draft (still derive for product_history secondary but not for context)
 → validation → enqueue_send
```

ONE TURN = ONE SNAPSHOT: gatherers, scorer, OneCall all consume same `AuthoritativeState` object identity (Test B).

---

## 5. FILES CHANGED

* `context_engine/models.py` — add `AuthoritativeState` frozen dataclass + helper.
* `context_engine/authoritative_assembly.py` **NEW** — coordinated acquisition (parallel I/O, single derive, commerce reuse).
* `context_engine/gatherer.py` — add `authoritative_state` to `GathererConfig`; snapshot-aware `PersonaSource`, `FanStateSource`, `ConversationHistorySource`, `CommerceStateSource` (prefer snapshot, fallback DB).
* `context_engine/assembler.py` — insert deterministic conflict resolution `_fact_identity/_status_rank/_resolve_conflicts` before scoring; combine dropped counts; pipeline `conflict→score→dedup→budget`.
* `context_engine/budget.py` — unify `estimate_tokens` to tiktoken `gpt-4` / `cl100k_base` fallback to chars/4.
* `context_engine/renderer.py` — `include_authority_markers` always true for Phase 2; preserve markers in OneCall path.
* `context_engine/integration.py` — add `authoritative_state` to `ContextRequest` and `to_gatherer_config`.
* `context_engine/worker_integration.py` — accept `authoritative_state`, pass to `ContextRequest`, render all blocks with explicit headers `[CURRENT AUTHORITATIVE STATE*]` `[RETRIEVED KNOWLEDGE*]`.
* `core/context_compact.py` — add `build_one_call_from_snapshot()` (uses snapshot/pipeline_result.rendered, handles fallback, trims to 8, adds advisory commerce hints label).
* `core/one_call_pipeline.py` — accept `authoritative_state, pipeline_result`; branch to snapshot builder when present else legacy; avoid duplicate commerce_hints.
* `workers/llm_worker.py` — import `assemble_authoritative_context`; new canonical branch after lock: authoritative snapshot acquisition (with fallback to legacy `build_qwen3_context`), conversion of snapshot recent_messages for legacy consumers, reuse `_conv_state/_user/_profile` from snapshot, pass snapshot to `observe_context_engine` and to `one_call_pipeline_with_fallback` (via `pipeline_result`), telemetry `authoritative_snapshot` flag.

Unchanged allowed but not modified: `memory/context.py` (kept for legacy), `core/config.py`.

---

## 6. TESTS ADDED

`tests/test_phase2_authoritative_context.py` — 18 tests (unit, mocked DB/Redis/Provider):

* A `test_derive_runs_once_for_new_path` — `get_derive_call_count()==1`
* B `test_ce_and_onecall_share_same_snapshot` — same `AuthoritativeState` object identity to CE and OneCall
* C `test_current_authoritative_wins_over_retrieved_historical` / `test_current_over_historical_same_authority` — conflict resolver winner before Qwen
* D `test_llm_price_does_not_override_db_price` — `validate_one_call_response` with mismatched `$10` vs `5000` cents still handoff + `execute_ppv` signature has no price
* E `test_final_one_call_contains_authority_sections` — checks `estimate_tokens`, `build_one_call_from_snapshot` yields `STATE/SYSTEM/AUTHORITATIVE`, and `observe_context_engine` rendered_text contains `[CURRENT AUTHORITATIVE STATE` + `[RETRIEVED KNOWLEDGE`
* F `test_system_state_survive_oversized_context` — flood MEMORY 30×800 chars, total ≤2600, SYSTEM+STATE survive, selected < candidate
* G `test_context_items_isolated_by_creator` + `test_dedup_respects_creator_isolation` — creator 1 Nairobi vs 2 Paris not leaked, dedup respect flag
* H `test_retrieval_failure_still_reaches_onecall` — CE failed=True → OneCall still succeeds via snapshot fallback
* I `test_normal_one_call_is_single_generation` — `mock_pipe 1 mock_com 0`
* J `test_ppv_gate_still_exists` — file contains `not_ppv_no_generation`
* K `test_new_path_failure_no_legacy` — bad OneCall `is_valid False` → `generate_draft 0`
* L `test_winning_value_determined_before_model` — `_resolve_conflicts` before `scorer.score_items` in file
* Adversarial: `test_missing_persona_still_assembles`, `test_empty_memory_still_renders`, `test_rapidfuzz_unavailable_fallback`, `test_embedding_unavailable_still_assembles`

---

## 7. TESTS PASSED

* `tests/test_phase2_authoritative_context.py` **18/18** 82.8s
* `tests/test_phase1_context_engine_canonical.py` **19/19** 43.2s
* `tests/test_phase78b_single_generation.py` + `test_phase78d_retrieval_activation.py` + `test_phase78e_canary.py` **55/55** 40.8s
* `tests/test_context_engine_gatherers.py` + `test_phase72_context_engine_integration.py` **106/106** 23.2s

All Phase 1 + Phase 2 unit passed. No new failures introduced (warnings only for `google.genai` deprecation and one coroutine warning pre-existing).

---

## 8. PRE-EXISTING FAILURES

* Ruff baseline unrelated (per instructions not attributed).
* Live PostgreSQL/Redis integration tests requiring real DB (`pytest -m integration` / `live`) not run, expected to need infra.
* Gemini API failures not exercised (Ollama mocked).
* One warning `coroutine fake_get was never awaited` in `test_phase78d/e` pre-existing (test helper mock not awaited in fallback path, not introduced by Phase 2).

---

## 9. NEW FAILURES

**None.** All new tests pass; all rerun Phase 1/72/78 suites pass.

---

## 10. ARCHITECTURE CHANGES

NONE OUTSIDE PHASE 2 AUTHORITATIVE CONTEXT ASSEMBLY.

* Retrieval algorithms (RapidFuzz WRatio80 limit5 + MiniLM all-MiniLM-L6-v2 384 brute cosine 0.30) unchanged.
* Commerce pipeline, `execute_ppv` 12 gates, `fangate_products.price_minor` authority, product selection ranking unchanged.
* Redis streams `ensure_consumer_group`, `XREADGROUP`, `XAUTOCLAIM [(id,dict)]`, `XACK after`, `DLQ`, `debounce:creator`, `lock:creator`, `send_dedup:{creator}` unchanged.
* PostgreSQL schema unchanged, no migration, no `pgvector`/`hnswlib`.
* Model stays `qwen3:4b` `ollama` `num_ctx 8192` `max 400` `temp 0.7`.
* Legacy `llm_path legacy` branch kept (still calls `build_qwen3_context` + 3-LLM).

---

## 11. AUTHORITY

PRESERVED.

* Hierarchy `HARD_POLICY 0 > DETERMINISTIC_RULE 1 > DETERMINISTIC_DERIVATION 2 > CONTEXT_ASSEMBLY 3 > LLM 4 > POST 5` unchanged (`models.py:AuthorityLevel`).
* Conflict resolver enforces that hierarchy before scoring (Test C). HISTORICAL vs CURRENT tie-break via `_status_rank`.
* OneCall final input now explicit with `[CURRENT AUTHORITATIVE STATE]` etc (Test E).
* LLM never becomes commerce authority (Test D).

---

## 12. CREATOR ISOLATION

PRESERVED.

* `lock:creator:{cid}:user:{uid}` `db/redis.py:283` via `acquire_user_lock(...,creator_id)` kept.
* `send_dedup:{creator}:{dedup}` `db/redis.py:84` kept.
* `ContextItem creator_id` + `respect_creator_isolation True` in dedup and conflict resolver grouped by `(creator_id, identity)` (Test G).
* Gatherers all creator-scoped; snapshot carries `creator_id`.
* Historical `WHERE (creator_id OR NULL)` not fixed in this phase (explicitly out-of-scope), documented.

---

## 13. DELIVERY GUARANTEES

PRESERVED.

* `XAUTOCLAIM` requeue preserves payload, `ACK after process` in `workers/llm_worker.py:1841` and `chatbotv2/main.py` send path unchanged.
* `enqueue_send` dedup id `md5(user:msg:tgId)` + `generation_id` propagation unchanged.
* Fail-open: `authoritative_assembly` try/except falls back to `build_qwen3_context`; `ContextEngineIntegration` try/except fails `failed=True` → `observe_context_engine` returns `failed` and worker uses minimal snapshot fallback (`build_one_call_from_snapshot` fallback) → still reaches OneCall (Test H).
* `publish_event` best-effort never breaks generation.

---

## 14. PERFORMANCE

* **State acquisition:** Parallel `asyncio.gather` of 5 (was 4) independent PG/Redis reads preserved; measurement mocked probe `assemble mock 335ms` dominated by import, real parallel `15-60ms` unchanged (still `recent 20`, `summary`, `profile`, `user`, `structured_persona`). No new sequential PG call introduced.
* **Context Engine:** Same 7 gatherers + same scoring 5 weights + new conflict O(k) grouping (linear) before dedup O(n²) ~30 items 0.5-2ms extra negligible.
* **Budget:** `estimate_tokens` now tiktoken precise (adds ~0.2ms per 2600 tokens) vs chars/4; more accurate, still deterministic.
* **Rendering:** Adds system/state headers + markers, negligible.
* **OneCall preparation:** `build_one_call_from_snapshot` reuses already-rendered `pipeline_result.messages` (no second `build_one_call_context` trim), saves one `trim_to_token_budget` call when snapshot present.
* **Total e2e:** No measurable p50 increase expected; retrieval 80-100ms + OneCall 800-2500ms dominates; Phase 2 overhead <5ms.

Before: `build_qwen3_context 15-60ms + CE gather 20-80ms + scorer/dedup 5-15ms + render 1ms + OneCall build 2ms`  
After: `assemble 15-60ms + CE (same but snapshotReuse saves 10-20ms of redundant fetches) + conflict 0.5ms + scorer/dedup 5-15ms + render 1ms + OneCall from snapshot 1ms` — net **faster or equal** (fewer DB fetches).

Measured `phase2 18 tests 82.8s` vs `phase1 19 tests 43.2s` overhead due to extra tiktoken encode, not production.

---

## 15. REMAINING OUT-OF-SCOPE ISSUES

* `messages`/`conversation_summaries` `OR NULL` leak (legacy rows visible cross-creator) — requires migration/backfill of `NULL` to creator_id, not Phase 2.
* `message_embeddings` JSONB 1536 legacy table dead for Qwen, coexistence with MiniLM 384 (no `embedding_384` persisted) — defer `pgvector`/`hnswlib` decision until per-user >5k vectors (P3).
* `cache_user_context` global `context:{user_id}` not creator-scoped (unused in new path but still exists).
* `debounce:creator` fallback to legacy `debounce:{user_id}` inside 3s window may merge messages during migration.
* Duplicate `get_embedding` (Gemini) vs `encode_message` (MiniLM) same task two stacks — not unified.
* Recovery worker debris `recovery_llm_worker/` and debug scripts in root — cleanup P3.

---

## EVIDENCE INDEX (Phase 2)

* Snapshot `context_engine/models.py:AuthoritativeState` + `to_conversation_state_dict`
* Acquisition `context_engine/authoritative_assembly.py:assemble_authoritative_context` `asyncio.gather` 5, derive once, B2 `build_llm_context(user_data=...)`
* Conflict `context_engine/assembler.py:_resolve_conflicts` authority → status → timestamp → priority → lexicographic
* Gatherer reuse `context_engine/gatherer.py:GathererConfig.authoritative_state` + `PersonaSource`/`FanStateSource`/`ConversationHistorySource`/`CommerceStateSource` snapshot branches
* Integration `context_engine/integration.py:ContextRequest.authoritative_state`
* Worker `context_engine/worker_integration.py:observe_context_engine(authoritative_state=...)` + headers `[CURRENT AUTHORITATIVE STATE*]` `[RETRIEVED KNOWLEDGE*]`
* Budget `context_engine/budget.py:estimate_tokens tiktoken`
* Renderer `context_engine/renderer.py:include_authority_markers`
* OneCall `core/context_compact.py:build_one_call_from_snapshot` + `core/one_call_pipeline.py:one_call_generation(authoritative_state,pipeline_result)`
* Worker `workers/llm_worker.py:assemble_authoritative_context` branch, reuse `_conv_state/_user/_profile` from snapshot, pass snapshot to CE and OneCall

Trace proven: `Telegram → debounce → XADD inbound gen md5 → XREADGROUP llm_workers → process_message → AuthoritativeState (ONE) → CE gather(conflict→score→dedup 0.85→budget 2600 tiktoken→render headers) → OneCall snapshot → Qwen → scoring deterministic → commerce → send`.

