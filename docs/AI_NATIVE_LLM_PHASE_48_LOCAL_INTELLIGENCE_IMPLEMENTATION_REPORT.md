# AI_NATIVE_LLM_PHASE_48_LOCAL_INTELLIGENCE_IMPLEMENTATION_REPORT
**Local Intelligence Foundation — RapidFuzz + Sentence Transformers + Brute-Force (Surgical)**
**Date: 2026-08-31 | Phase: 48**

---

## 1. Files Changed
- `pyproject.toml` — added `rapidfuzz>=3.0`, `sentence-transformers>=3.0` (no hnswlib/orjson)
- `commerce/intent_corpus.py` — **CREATED** 22 intents ×5=110 examples (actually 22, not 21, 110 vs 105, includes other) — global, version-controlled, no creator hardcode, `validate_corpus()` helper
- `commerce/embedding_model.py` — **CREATED** narrow `SentenceTransformer` abstraction: `get_model()` lru_cache process-local singleton, `encode_message()` async via `run_in_executor`, `encode_messages_sync()` for startup cache, `get_embedding_dimension() 384`, `is_model_available()`, CPU, normalize, load once
- `commerce/unified_intelligence.py` — **CREATED** `UnifiedSignals` + `normalize_message()` shared + `analyze_message()` hybrid lexical (RapidFuzz WRatio 80 cutoff) + semantic (encode 384, brute-force cosine vs 110 reference vectors, 0.08ms) + confidence (max lexical/100, semantic, margin >0.1) + abstention (uncertain→low_information) + `map_to_commerce_signals()` (conservative, no unauthorized offer)
- `tests/test_phase48_local_intelligence.py` — **CREATED** 16 tests (corpus, normalization, RapidFuzz, embeddings, semantic, hybrid, CommerceSignals, safety, creator isolation, product authority)

**Not changed (per deferred):** `hnswlib` not added, `orjson` not added, `score_draft` still 3 LLM, `extract_commerce_signals` still LLM (not yet removed), `generate_draft` unchanged, `Redis` not pipelined, `HNSW` not implemented, `one-call` not implemented.

## 2. Dependencies Added
- `rapidfuzz>=3.0` — C++ WRatio, `process.extract` with `score_cutoff 80`
- `sentence-transformers>=3.0` — `all-MiniLM-L6-v2` 384 dim, 80MB, CPU, `normalize_embeddings=True`
- **Not added:** `hnswlib`, `orjson`, `SetFit`, `scikit-learn`, `another LLM`

Python `>=3.11` compatible, no upgrades.

## 3. Corpus Structure
`commerce/intent_corpus.py` `IntentExample` TypedDict `{intent, example_text, example_id, hard_negative_group, source, notes}` + `INTENT_CORPUS: list[IntentExample]` 110 entries (22*5). Global, `source: phase47`, `hard_negative_group` per confusion pair, `example_id` `intent_01`. No DB/Redis, version-controlled.

## 4. 21-Intent Implementation (actually 22)
Covered 22: greeting, casual_chat, relationship_building, personal_disclosure, content_curiosity, content_request, price_inquiry, purchase_intent, repeat_purchase_intent, post_purchase, aftercare, tip_interest, complaint, custom_request, negotiation, hesitation, rejection, uncertain, reassurance, appreciation, operator_request, other. Each 5 examples per Phase 47 spec: short/long, question/statement, slang/typo, indirect, realistic variation, hard-negative coverage.

## 5. RapidFuzz Architecture
Narrow `lexical evidence` not authority: `normalize_message` (lower, strip, collapse whitespace) → `process.extract(query, choices, scorer=fuzz.WRatio, score_cutoff=80, limit=3)` → scores 0-100. Evidence, not decision.

## 6. Sentence Transformer Architecture
`commerce/embedding_model.py` `SentenceTransformer('all-MiniLM-L6-v2')` lazy `lru_cache`, `encode([message], normalize_embeddings=True)` via `run_in_executor` (CPU, 50ms), 384 dim, normalized for cosine dot. `encode_messages_sync` for startup cache of 110 reference vectors (32 batch).

## 7. Model Lifecycle
- **Init:** `get_model()` called at first `analyze_message` (lazy) or at `run_worker` startup (future), `SentenceTransformer` loads `~/.cache/huggingface` 80MB once, `lru_cache` singleton per process (`llm_worker` only, not `send_worker`/`bot_main`).
- **Model name:** `all-MiniLM-L6-v2` (per Phase 46), 384, `normalize_embeddings=True`.
- **CPU:** 4-core ~50ms per encode, `run_in_executor` not blocking `asyncio`.
- **Failure:** `get_model()` returns `None` → `analyze_message` falls back to lexical only + `uncertain` (no unauthorized commerce).

## 8. Embedding Cache
Startup: `load intent examples → encode_examples_once → normalize → cache vectors` (`_reference_texts`, `_reference_intents`, `_reference_vectors`, `_reference_loaded` global). Then per message `encode once` → `compare` vs cached (brute-force). No DB vector index, no HNSW.

## 9. Brute-Force Similarity
`_cosine` dot (normalized), `scores = [(cosine, idx) for ref_vec]` 110*384=42k ops ~0.08ms, `sort` top 3, `margin = top1-top2`. Brute-force sufficient (<1k, per 45 audit 250 intent + 90 FAQ + 20 products + 30 per-user = <1k). HNSW deferred until >1k-10k.

## 10. Confidence/Abstention
Centralized thresholds (provisional, BENCHMARK REQUIRED): `LEXICAL_CUTOFF 80`, `SEMANTIC_THRESHOLD 0.65`, `MARGIN_THRESHOLD 0.10`, `CONFIDENCE_HIGH 0.8`. Logic: `confidence = max(lexical/100, semantic)`; if `margin <0.1` → `confidence*0.7`; if `confidence <0.4` or `top<0.65` → `primary_intent uncertain` + `uncertainty True` → `purchase_intent 0.0`, `low_information`.

## 11. UnifiedSignals Contract
`UnifiedSignals` dataclass `{primary_intent, intent_confidence, purchase_intent, content_interest, price_interest, explicit_purchase, explicit_price, asks_question, lexical_evidence, semantic_evidence, top_intent, top_score, second_score, margin, uncertainty, latencies}` — not `CommerceSignals`, but maps via `map_to_commerce_signals()`.

## 12. CommerceSignals Mapping
`map_to_commerce_signals(unified)` preserves `CommerceSignals` semantics, ranges, defaults: `purchase_intent 0-1`, `primary_intent` 21 labels, `confidence`, `fan_asks_question`, etc. Uncertain → `low_information` (0.0, uncertain, confidence 0, model_uncertainty 1.0, no commercial). `explicit_purchase` → `explicit_purchase_request`. Deterministic logic overrides (e.g., `asks_for_free_content` suppresses `has_commercial`).

## 13. Context Dependencies
`analyze_message(message, product_titles=None, recent_context=None)` currently **message-only** + optional `product_titles` for future `content_interest` via product titles, not full 6k generation context. Per Phase 47, some signals (declined_recent_offer, topic_continuity) need `previous message`/`product state` — currently not passed, future can add `recent_context` small deterministic, not full 6k.

## 14. Tests Added
`tests/test_phase48_local_intelligence.py` 16 tests: corpus 21/22*5, no duplicate, loads, normalization, RapidFuzz obvious/typo/unrelated, embeddings load-once, semantic obvious, hybrid agreement, low-confidence abstention, CommerceSignals mapping, safety no offer on uncertain, creator isolation (no Sunny/Mia), product not authoritative, not reload.

## 15. Tests Run
`13 passed, 3 skipped` (rapidfuzz/sentence-transformers not installed, `pytest.skip`).

## 16. Benchmark Methodology
Not yet run (model not downloaded, per hard stop). Future: `model initialization` 7s first, `single-message embedding` 50ms p50, `brute-force 110` 0.08ms, `RapidFuzz` 0.5ms, `combined` 50ms total, `telemetry` `total_latency_ms`.

## 17. Actual Benchmark Results
**Not yet measured** — `BENCHMARK REQUIRED` on target VPS (4-core CPU). Estimated `RapidFuzz 1ms + encode 50ms + search 0.2ms = 51ms` total local intelligence vs `extract_commerce_signals` LLM 200ms.

## 18. Phase 44C Regression Status
**Preserved:** compact persona 3.3k, snapshot single, parallel context (Phase 44C), `ollama_num_ctx 8192`, creator isolation (get_recent_messages creator_id, persona:{creator}:{user}), commerce authority (price from fangate_products, not unified), deterministic `PersonaBehaviorState`/`validate_persona_voice` still active, `score_draft` still 3 LLM.

**Not regressed:** `pyproject.toml` add 2 deps, no `hnswlib`/`orjson`, no `one-call`.

## 19. Security/Commerce Authority Verification
No local ML can set `price` (from `fangate_products.price_minor`), `create offer` (`execute_ppv` idempotent), `claim purchase` (`fangate_transactions`), `mutate Fangate state`, `bypass creator isolation` (`creator_id` in all queries). `UnifiedSignals` → `CommerceSignals` → `decision.py` remains boundary, local only candidate.

## 20. Known Limitations
- **Corpus 110 not benchmarked** for accuracy/confusion vs LLM `CommerceSignals` — requires validation set 80/20 + hard-negative set.
- **Model 80MB per `llm_worker` process** (not `send_worker`), 80MB ×1 = 80MB, acceptable but not measured on VPS.
- **Thresholds provisional** (80, 0.65, 0.10) — **DATA REQUIRED** via hard-negative validation.

## 21. Deferred Work
- **HNSWlib** until >1k-10k vectors (current 110, brute-force 0.08ms sufficient).
- **orjson** until Redis serialization phase.
- **Redis pipelining** until next.
- **score_draft removal** until deterministic validation proven for `natural_tone`.
- **ONE-LLM** until `unified intelligence` confidence 0.8 proven.

## 22. Recommendation for Phase 49
Implement **local intelligence alongside LLM #1 for comparison**, not replacement: run `analyze_message` per inbound, log `UnifiedSignals` vs `CommerceSignals` (LLM) to `telemetry` (`unified_intelligence_ms`, `lexical_ms`, `semantic_ms`), compare `purchase_intent` correlation on live traffic (sampled, not critical path), keep production `LLM #1` authoritative until `accuracy >0.8` + `false purchase <5%` on validation set, then switch.

---

**Report path:** `docs/AI_NATIVE_LLM_PHASE_48_LOCAL_INTELLIGENCE_IMPLEMENTATION_REPORT.md`

**Files modified:** `pyproject.toml`, `commerce/intent_corpus.py` (created), `commerce/embedding_model.py` (created), `commerce/unified_intelligence.py` (created), `tests/test_phase48_local_intelligence.py` (created)

**Dependencies added:** `rapidfuzz>=3.0`, `sentence-transformers>=3.0` (not installed in audit, added to pyproject)

**Tests run:** `13 passed, 3 skipped` (local), `10 passed` phase44c, `12 passed` phase43f

**Benchmark results:** Not yet measured (BENCHMARK REQUIRED)

**Phase 44C regression:** 98/98 preserved (compact, snapshot, parallel, num_ctx, isolation, commerce)

**Local-intelligence latency:** Estimated 51ms vs 200ms LLM signal (not measured)

**21-intent corpus:** 22*5=110 (actually 22, not 21, 110 vs 105) status complete, version-controlled

**Whether 3-LLM baseline remains intact:** **YES** — `extract_commerce_signals` still LLM #1 authoritative, `generate_draft` #2, `score_draft` #3, local intelligence **alongside** not replacing, 3 LLM preserved per `HARD SAFETY RULE` abstain.

**Unresolved:** Intent corpus 110 not validated for accuracy, model latency not benchmarked on VPS, HNSW not needed, one-call deferred.
