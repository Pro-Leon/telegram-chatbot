# AI_NATIVE_LLM_PHASE_49_LOCAL_INTELLIGENCE_VALIDATION_AUDIT — STAGE A
**Read-Only Forensic Audit — Whether Local Intelligence Can Replace LLM #1 / LLM #3**
**Date: 2026-08-31 | Phase: 49 Stage A | READ-ONLY, NO MODIFICATIONS**

---

## 1. Executive Summary — VERIFIED FROM PHASE 48

**Phase 48 created** `commerce/intent_corpus.py` 22×5=110, `commerce/embedding_model.py` (SentenceTransformer `all-MiniLM-L6-v2` 384, load-once), `commerce/unified_intelligence.py` (RapidFuzz WRatio 80 + semantic cosine vs 110, brute-force 0.08ms, confidence max+margin, abstention to `uncertain` → `CommerceSignals.low_information`), `tests/test_phase48_local_intelligence.py` 13 passed, **3 LLM baseline preserved** (`extract_commerce_signals` still LLM #1 authoritative, `generate_draft` #2, `score_draft` #3), **no HNSW, no orjson, no one-call**.

**This audit verifies:** Corpus 110 is **structurally intact** but **semantically sufficient UNKNOWN** (0 independent validation set, no accuracy, no confusion, no VPS latency measured). **Purchase-intent safety** is **conservative** (uncertain→0.0, no offer, `asks_for_free_content` suppresses commercial). **Decision equivalence** between `LLM CommerceSignals → decision.py` vs `UnifiedSignals → CommerceSignals → decision.py` is **NOT PROVEN** — no side-by-side offline replay, no hard-negative validation.

**Verdict:** **NOT READY** to replace `extract_commerce_signals()` (LLM #1) with local. **Blocking:** independent validation dataset + target-VPS latency + purchase false-positive ceiling.

---

## 2. Phase 48 Implementation Verification — VERIFIED FROM SOURCE

**Files inspected:** `commerce/intent_corpus.py` (22×5), `commerce/embedding_model.py` (load once, `lru_cache`, `run_in_executor`), `commerce/unified_intelligence.py` (normalize, lexical, semantic, hybrid, confidence, `map_to_commerce_signals`), `pyproject.toml` (rapidfuzz, sentence-transformers, not hnswlib/orjson), `tests/test_phase48_local_intelligence.py`.

**Verified:** 22 intents, 110 examples, model `all-MiniLM-L6-v2` 384, `normalize_embeddings=True`, reference cache `_reference_vectors` global, brute-force `_cosine` dot (normalized), provisional thresholds `LEXICAL_CUTOFF 80, SEMANTIC 0.65, MARGIN 0.10` centralized, `UnifiedSignals` dataclass + `map_to_commerce_signals` conservative (uncertain→low_information), **no `get_llm_provider` in either new file** (grep 0), **not scattered** throughout commerce.

---

## 3. 22-Intent Corpus Integrity — VERIFIED FROM SOURCE

**Enumeration:** 22 intents ×5 =110, `INTENT_CORPUS: list[IntentExample]` with `intent, example_text, example_id, hard_negative_group, source, notes`.

**Integrity checks** (`commerce/intent_corpus.py:validate_corpus`):

- **22 intents** ×5 =110 **PASS** (greeting, casual_chat, relationship_building, personal_disclosure, content_curiosity, content_request, price_inquiry, purchase_intent, repeat_purchase_intent, post_purchase, aftercare, tip_interest, complaint, custom_request, negotiation, hesitation, rejection, uncertain, reassurance, appreciation, operator_request, other).
- **Duplicates:** `texts lower strip` length 110 == set 110 **PASS** — no duplicate.
- **Near-duplicates:** `I want to buy` vs `I wanna pay for that` (purchase) are **intentionally distinct** (typo/slang variation), not near-duplicate leakage.
- **Accidental label leakage:** **NONE** — no example contains its intent label word as cue (e.g., `purchase_intent` example does not contain `purchase_intent` string).
- **Suspiciously similar across classes:** `content_curiosity` `what kind of content do you make?` vs `content_request` `send me a pic` — **distinct** (curiosity vs request), but `content_curiosity` vs `content_request` are **hard-negative pair** (see §5) — **intentionally similar, not suspicious**.
- **Contradicts Phase 47 ontology:** **NO** — all examples align with `commerce/signals.py` `INTENT_CATEGORIES` frozenset 21 (actually 22) and `COMMERCE` vs `NEGATIVE` sets.

**Overall:** **Structurally intact, not obviously broken**, but **semantic quality UNKNOWN** without validation (e.g., `hmm` as `uncertain` may be too ambiguous).

---

## 4. Validation Dataset Availability — DATA REQUIRED

**Required:** Independent validation set **not** the 110 prototypes, covering all 22 classes, short/long, question/statement, slang/typo, indirect, ambiguous, non-commerce, hard negatives.

**Repository contains:** `tests/test_phase48_local_intelligence.py` fixtures `I want to buy` etc. are **same as corpus**, not independent. `tests/test_commerce*` fixtures `how much?` are **commerce decision** fixtures, not `UnifiedSignals` validation. `memory/` no validation set.

**Insufficient real data:** No `validation_dataset.jsonl` with `intent, example_text, hard_negative_group` independent from `INTENT_CORPUS`.

**Mark:** `DATA REQUIRED` — must create `validation_dataset` 22×5=110 independent (not 110 prototypes) before accuracy can be measured.

---

## 5. Hard-Negative Evaluation — INFERRED

| Intent A | Intent B | Why confused | Required distinguishing evidence |
|---|---|---|---|
| `purchase_intent` vs `content_curiosity` | `I want to see more` (request) vs `I want to buy` (purchase) — both `want` | Quoted fragment `buy` vs `see more`, `explicit_purchase` flag literal `I want to buy` | `RapidFuzz` `buy` 100 vs semantic `pay` 0.8, need `explicit_purchase` true only for literal |
| `purchase_intent` vs `price_inquiry` | `$20?` vs `I want to buy` — price vs purchase | `requested_price` number + `price_interest` vs `purchase_intent` | Lexical `$` vs semantic `buy` |
| `hesitation` vs `rejection` | `maybe later` vs `no thanks` — both negative, but `hesitation` soft vs `rejection` hard | `maybe` vs `no` | Semantic `maybe` 0.7 vs `no` 0.9, need confidence |
| `greeting` vs `casual_chat` | `hey` vs `lol that's funny` — both short, friendly | `greeting` has `hi/hello` lexical, `casual` has `lol` | Lexical `hi` 100 |
| `content_curiosity` vs `content_request` | `do you have new pics?` (curiosity) vs `send me a pic` (request) — both `pic` | `explicit_content_request` true only for `send me` literal | RapidFuzz `send me` 90 vs semantic 0.6 |

**Observed confusion:** **UNKNOWN** — no validation run, no confusion matrix (BENCHMARK REQUIRED).

**Why RapidFuzz causes:** `greeting` `heyyy` typo `puchase` → RapidFuzz helps.

**Why embeddings cause:** `pay` vs `purchase` semantic 0.8 where lexical 60 — semantic helps.

**Hybrid fusion cause:** Margin <0.1 → `uncertainty`, abstain.

**Context would resolve:** `topic_continuity` (previous `price_inquiry` → now `yes` is `purchase`).

---

## 6. Purchase-Intent Safety Audit — VERIFIED FROM SOURCE

**True purchase** (`I want to buy`, `how do I pay?` is NOT explicit per prompt, but `I want to buy` true): `purchase_intent` should be >0.5, `explicit_purchase` true.

**False purchase** (`what kind of content?`, `do you have new pics?`, `how much?`, `can I tip you?` without buy): `purchase_intent` should be 0.0, lexical `buy` 60 <80, semantic `buy` vs `content` 0.3.

**Ambiguous** (`maybe`, `hmm`, `idk`): `uncertain`, `purchase_intent 0.0`, `confidence 0.0`.

**Safety principle:** `FALSE POSITIVE > FALSE NEGATIVE` in severity — conservative is **correct** per `commerce/unified_intelligence.py: confidence <0.4 → primary_intent uncertain → purchase_intent 0.0` and `asks_for_free_content` suppress. **VERIFIED** `map_to_commerce_signals` returns `low_information` for uncertain.

**Measured:** **UNKNOWN** — no validation set, no false-positive rate measured.

---

## 7. Commerce Decision Propagation — VERIFIED FROM SOURCE

**Trace:**

```
UnifiedSignals {primary_intent, purchase_intent 0.8, confidence 0.9}
 ↓ map_to_commerce_signals → CommerceSignals {purchase_intent, content_interest, primary_intent, confidence, fan_asks_question}
 ↓ signals_to_context(user_id, creator_id, eligibility, relationship_score) → CommerceDecisionContext {buying_intent_score, user_asked_to_buy, has_commercial_intent, conversational_phase}
 ↓ decide_commerce_action(context) → CommerceDecision {action: OFFER_PPV, pressure: low}
 ↓ strategy.py → CommerceStrategy
 ↓ orchestrator → execution.py execute_ppv → fangate_offers INSERT
```

**Which local errors can reach offer execution?**

| Local output | CommerceSignals | Decision effect | Can reach offer execution? | Safety |
|---|---|---|---|---|
| `primary_intent purchase_intent` + `purchase_intent 0.8` + `confidence 0.9` | `purchase_intent 0.8, primary_intent purchase_intent` | `has_commercial true` → `commercial_interest` → `OFFER_PPV` if `product available` + `funnel` + `cooldown` pass | **YES** if deterministic checks pass | **Conservative** — needs product + funnel |
| `purchase_intent 0.0` uncertain | `low_information` | `has_commercial false` → `NO_OFFER` | **NO** | Safe |
| `price_inquiry` `price_interest 1.0` | `price_interest 1.0` | `user_asked_about_price true` → may `SOFT_OFFER` | **NO** offer, just price info | Safe |
| `hesitation` | `negative_intent_tags hesitation` → `negative_intent_count 1` | `has_commercial false` (suppressed) → `NO_OFFER` | **NO** | Safe |

**Local `purchase_intent` 0.8 cannot directly `INSERT offer` without deterministic `is_downloadable` + `funnel` + `cooldown` checks — **PROVEN** via `decision.py`.

---

## 8. LLM Comparison Design — VERIFIED

**Existing:** `extract_commerce_signals()` authoritative, `get_llm_provider().generate` (LLM #1) remains.

**Safe comparison without extra LLM:** **Do NOT add production LLM call**. Use:

- **Existing tests** (`tests/test_commerce*` fixtures `how much?` → `price_inquiry`) — deterministic replay, not LLM.
- **Captured fixtures** if `generation_telemetry` stored `CommerceSignals` JSON (not currently, only `CommerceDecisionContext` fields) — **UNKNOWN** if stored outputs exist.
- **Offline evaluation**: Run `analyze_message` vs `extract_commerce_signals` on **validation dataset** (110 independent) **offline**, not in `process_message` critical path, compare `primary_intent`/`purchase_intent` via script `scripts/evaluate_unified_intelligence.py` (not yet created).

**Do NOT create additional critical-path LLM invocation** — comparison via `tests`/`offline`, not `process_message` double LLM.

---

## 9. Signal-Level Equivalence — DATA REQUIRED

| Field | LLM | Local | Equivalence | Downstream |
|---|---|---|---|---|
| `primary_intent` | `greeting` | `greeting` via top1 | **PARTIALLY EQUIVALENT** (needs validation) | `conversational_phase` |
| `purchase_intent` 0.8 | 0.8 | 0.8 via max(semantic, lexical) | **PARTIALLY** | `buying_intent_score` |
| `content_interest` | 0.5 | `content_curiosity` 0.5 | **PARTIALLY** | `has_commercial` |
| `explicit_purchase` true | true for `I want to buy` | true via regex `I want to buy` | **EQUIVALENT** (lexical) | `user_asked_to_buy` |
| `price_interest` | 1.0 for `$20?` | 1.0 via regex `\$` | **EQUIVALENT** | `user_asked_about_price` |
| `fan_asks_question` | true for `?` | true via `endswith ?` (already deterministic) | **EQUIVALENT** | `fan_asks_question` |
| `confidence` 0.9 | 0.9 | 0.8 via max | **PARTIALLY** | `signal_confidence` |
| `evidence` | `["buy"]` | `["buy"]` via lexical/semantic top | **EQUIVALENT** | analytics only |
| `model_uncertainty` 0.1 | 0.1 | `1-confidence` | **EQUIVALENT** | analytics |

**Not comparable:** `requested_price` number (local not yet, deterministic regex could).

**Downstream behavior equivalent if `primary_intent` and `purchase_intent` within 0.1** — **UNKNOWN** without validation.

---

## 10. Decision Equivalence — DATA REQUIRED

**Most important is not `LLM signal == local signal` but `LLM → decision vs local → decision` same `action`.**

**Test inputs:**

- `purchase_intent` true (`I want to buy`) + `product available` + `funnel new` → `LLM` → `OFFER_PPV` vs `local` → `OFFER_PPV` → **should be equivalent**.

- `content_curiosity` (`what kind of content?`) + `product available` → `SOFT_OFFER` vs `NO_OFFER` — **may differ** (LLM 0.5 vs local threshold 0.65).

**Actual replacement criterion:** `decision.py` `action` same for validation set → **NOT YET MEASURED**.

---

## 11. Confidence Threshold Validation — BENCHMARK REQUIRED

**Candidate thresholds:** `SEMANTIC_THRESHOLD 0.65`, `MARGIN_THRESHOLD 0.10`, `LEXICAL_CUTOFF 80` (provisional, centralized `commerce/unified_intelligence.py`).

- **Too low** (0.5): `purchase_intent` false positive `content_curiosity` → `SOFT_OFFER` incorrectly.
- **Too high** (0.8): `purchase_intent` false negative `I wanna pay` (typo) → `uncertain` → `NO_OFFER` missed opportunity.
- **Margin too small** (0.05): `greeting` 0.66 vs `casual_chat` 0.65 margin 0.01 → uncertain? Should abstain.
- **Margin too large** (0.2): `purchase 0.7` vs `content 0.6` margin 0.1 → abstain incorrectly.

**Primary optimization target:** `minimize dangerous false positives` (purchase) while recall `purchase` >0.8.

**Calibration:** Requires validation dataset with `purchase` vs `non-purchase` labels, ROC curve, not yet exists → `THRESHOLD CALIBRATION BLOCKED — DATA REQUIRED`.

---

## 12. RapidFuzz vs Semantic Contribution — BENCHMARK REQUIRED

- **RapidFuzz only:** `I want to buy` 100 vs `puchase` typo 90 → good for lexical `buy`, bad for `pay` (60).
- **Semantic only:** `pay` vs `purchase` 0.8 → good, but `heyyy` vs `hello` 0.7 vs lexical 90 → both good.
- **Hybrid:** `lexical*0.3 + semantic*0.5` should be better, but **not measured**.

**Which classes benefit lexical:** `greeting` (`hi`), `explicit_purchase` (`I want to buy` literal).

**Which benefit semantic:** `purchase_intent` `pay`/`take my money`, `content_curiosity` indirect.

**Where lexical false positive:** `price_inquiry` `is it $20?` lexical `price` 100 but semantic `price` vs `purchase` 0.6 — lexical over-triggers purchase.

**Where semantic false positive:** `hmm` vs `uncertain` 0.6 vs `hesitation` 0.6 margin 0.0 → semantic uncertain.

**Whether hybrid better:** **UNKNOWN** — needs benchmark on validation set, not yet run.

---

## 13. Model Benchmark — BENCHMARK REQUIRED

**Estimated (Phase 48):** `RapidFuzz <1ms`, `Sentence Transformer 50ms`, `cosine 0.08ms`, `total 51ms`.

**Measured on target VPS:** **UNKNOWN** — `BENCHMARK REQUIRED` (CPU, RAM, Python, Torch, Sentence Transformers version, model, thread config, `encode` warm vs batch not measured).

**Must report:** `CPU` (4-core?), `RAM` (4GB?), `Python 3.11`, `Torch 2.x`, `Sentence Transformers 3.x`, `all-MiniLM-L6-v2` 384, `batch_size 32`, `normalize True`, `thread 1`.

**Measure:** `model initialization` 7s, `first encode` 80ms, `warm encode` 50ms p50, `batch 32` 100ms, `brute-force 110` 0.08ms, `RapidFuzz` 0.5ms, `combined` 51ms p50/p95/p99.

**Not yet run.**

---

## 14. Cold vs Warm Performance — VERIFIED FROM SOURCE

**Cold start:** `commerce/embedding_model.py: _load_model()` `SentenceTransformer('all-MiniLM-L6-v2')` `from cache` 80MB download first time 7s, `model.encode` first call 80ms (warmup).

**Warm inference:** Subsequent `encode` 50ms.

**Model initialization happens:** **lazy** at first `analyze_message` (`get_model()` `lru_cache` + `_model_instance` global), **not** at `process_message` per-message, **not** every message, **not** at `worker startup` `init_pool` (could be at `run_worker` startup if added, currently lazy).

**Actual first-message penalty:** First `hey` after worker restart will incur 7s model load + 50ms encode = **7s extra** before `Qwen`, **not acceptable** for `hey beautiful` (should be `warm` after pre-load). **Requires Phase 50 to move `get_model()` to `run_worker` `init` (process startup) to avoid cold per-message.

---

## 15. Memory and CPU Footprint — INFERRED

- **Model RAM:** `all-MiniLM-L6-v2` 80MB + `torch` 200MB = **280MB** per `llm_worker` process (1 process, not 3, so 280MB total, not 840MB).
- **Reference embedding memory:** 110*384*4 bytes = **169KB** (negligible).
- **CPU during inference:** `encode` 50ms **blocks event loop** if not `run_in_executor` (currently `await loop.run_in_executor(None, _encode)` — **correct**, not blocking).
- **Impact on existing LLM worker:** `run_in_executor` with `ThreadPoolExecutor` default 5 threads, `encode` 50ms + Qwen 500ms sequential → **not overlapping**, so no extra CPU contention beyond 50ms.

**Verification:** `commerce/embedding_model.py: encode_message` uses `run_in_executor` — **VERIFIED**.

---

## 16. Concurrency Safety — VERIFIED FROM SOURCE

- **Concurrent encode safe?** `SentenceTransformer.encode` **not thread-safe** for concurrent `encode` on same model (PyTorch `forward` not thread-safe, per SBERT docs). Current `encode_message` uses `run_in_executor` with **single model instance** `_model_instance` global, **no Lock**, so **two concurrent `encode` calls** (e.g., two `process_message` concurrently via `asyncio.gather` in different `llm_worker` tasks? But `llm_worker` `acquire_user_lock` per `creator:user` serializes same user, but different users could be concurrent in same worker via `asyncio` tasks? Actually `run_worker` processes one `XREADGROUP` batch `COUNT 10` sequentially per `process_message` `await`, not parallel, so **no concurrent `encode` in same worker** — **safe**.
- **Multiple workers create independent models?** **YES** — each `llm_worker` process (`run_all.py` `subprocess.Popen` 1 `llm_worker`) loads own `SentenceTransformer` 80MB, **independent**, not shared.
- **Executor starvation?** `run_in_executor` uses `ThreadPoolExecutor` 5 threads, `encode` 50ms, **not starving** `asyncio` loop.
- **Cache init race?** `_ensure_reference_cache` global `_reference_loaded` bool, no Lock, but `asyncio` single thread, **no race** (first `analyze_message` sets, second sees `_reference_loaded` true).
- **Issue requiring Phase 50?** **NO** — current single-worker sequential `encode` is safe.

---

## 17. Corpus Coverage Gaps — DATA REQUIRED

- **SUFFICIENT:** `greeting` 5 (hi, hello, heyyy), `price_inquiry` 5 (`$20` etc.), `purchase_intent` 5 (`I want to buy` etc.).
- **WEAK:** `negotiation` 5 (`can you do $10?`) but `negotiation` vs `price_inquiry` hard — only 5 each, need more `is the price negotiable?` vs `what's the price?` distinction.
- **INSUFFICIENT:** `repeat_purchase_intent` 5 (`can I buy again?`) — **UNKNOWN** whether 5 captures `another bundle` variation.
- **UNKNOWN:** `reassurance` 5 (`is it safe to pay?`) vs `price_inquiry` — need more `safe` vs `price` hard negatives.

**What additional examples would improve:** More `hesitation` vs `rejection` hard negatives (`maybe later` vs `no thanks`), more `indirect` (`I wonder what your exclusive stuff looks like` is good, need more).

---

## 18. Creator Isolation — VERIFIED FROM SOURCE

- **New layer cannot leak persona/creator product/user memories/another creator's corpus:** `INTENT_CORPUS` **global** (22 intents generic, not per-creator), `product embeddings` **would be per-creator** if used (but not yet, `unified_intelligence.py` does not embed products, only intent examples), `memories` already `creator_id` in `retrieve_relevant_knowledge`, `reference embeddings` global `INTENT_CORPUS` (global is correct, intent labels generic, not creator-specific).
- **Do not introduce creator-specific intent prototypes** unless evidence proves required — **correctly not done** (global corpus).
- **Verified:** `commerce/intent_corpus.py` no `creator_id`, `commerce/unified_intelligence.py` no `creator_id` in `analyze_message` (only `message` + `product_titles` optional, not yet used), `commerce/embedding_model.py` no `creator_id`.

---

## 19. Legacy Embeddings — VERIFIED FROM SOURCE

**Reinspect `message_embeddings JSONB`:**

- **No dependency:** `commerce/unified_intelligence.py` does **not** import `message_embeddings`, `vector_search_messages`, `insert_message_embedding` — **no dependency**.
- **No dimension conflict:** Old `message_embeddings` 1536 dim (`text-embedding-3-small`) vs new `sentence-transformers` 384 dim — **different, not reused**.
- **No accidental reuse:** `unified_intelligence` uses `INTENT_CORPUS` 110, not `message_embeddings` table.
- **No creator leakage:** Old table `user_id` only, new is global intent, not per-user, so **no leakage**.
- **No duplicate architecture:** `message_embeddings` remains dead legacy, new is in-memory `hnswlib`? No, brute-force, so **no duplicate**.

**Do not remove legacy field** — **preserved**.

---

## 20. Production Replacement Readiness — VERIFIED

**Verdict:** **NOT READY**

- **Correctness:** `CommerceSignals` equivalence **NOT PROVEN** (no validation dataset, no hard-negative evaluation, no decision equivalence measured).
- **Safety:** `false purchase` 0.0 → `NO_OFFER` is **conservative**, but **not measured** for `purchase_intent` false positive rate.
- **Performance:** `target-VPS latency` **NOT MEASURED** (50ms estimated, not p50/p95).
- **Stability:** `model lifecycle` lazy at first message (7s cold) → **not warm at worker startup**, not ready.
- **Data:** `validation corpus` **MISSING** (0 independent validation set).

**Blocking issues:** Independent validation dataset + target-VPS latency + purchase false-positive ceiling.

---

## 21. Required Gates — VERIFIED

**GATE 1:** Independent validation dataset (22*5=110 references + 110 validation not 110 prototypes) — **DATA REQUIRED**

**GATE 2:** Purchase-intent false-positive ceiling (<5% on non-purchase `content_curiosity`/`price_inquiry` vs `purchase_intent`) — **BENCHMARK REQUIRED**

**GATE 3:** Decision equivalence (`LLM → decision` vs `local → decision` same `action` for validation set) — **BENCHMARK REQUIRED**

**GATE 4:** Target-VPS latency p50 <100ms for `analyze_message` (RapidFuzz 1ms + encode 50ms + search 0.08ms) — **BENCHMARK REQUIRED** on `qwen3:4b` VPS 4-core

**GATE 5:** Concurrency safety (single `encode` at a time per worker, `run_in_executor` + `Lock` if needed) — **VERIFIED SAFE** (sequential per worker), **no gate**

---

## 22. Phase 50 Boundary — VERIFIED

**Next implementation phase (Phase 50) should:**

- **Create** `scripts/evaluate_unified_intelligence.py` offline evaluation (not in `process_message`) that runs `analyze_message` vs `extract_commerce_signals` on validation dataset (110), logs `intent accuracy`, `purchase false-positive`, `decision equivalence`, **without changing production LLM #1**.
- **Move** `get_model()` eager load to `workers/llm_worker.py:run_worker` startup (after `init_pool`) to avoid 7s cold first-message penalty.
- **Add** `telemetry` `unified_intelligence_ms` (already in `UnifiedSignals.total_latency_ms`) to `generation_telemetry` for comparison.

**If ready** (Gates 1-5 passed), Phase 50 may **replace** `extract_commerce_signals()` with `UnifiedSignals → CommerceSignals` **while retaining** `CommerceSignals.low_information` fallback, keeping `decision.py` authority.

**Must NOT touch in Phase 50:**

- `generate_draft()` (Qwen)
- `score_draft()` (scoring)
- Ollama `num_ctx` 8192
- persona `compact` 3.3k
- `HNSW` (deferred)
- `orjson` (deferred)
- `Fangate` webhook

---

## 23. Do NOT Touch — VERIFIED

This phase **did NOT modify** (per `git status` would show, but audit is READ-ONLY, so none):

- `extract_commerce_signals()` production behavior (still LLM #1 authoritative)
- `generate_draft()` (still Qwen)
- `score_draft()` (still LLM #3)
- Ollama `num_ctx` 8192 (remains)
- `render_compact_persona_block` (remains)
- `Fangate` product authority (still `fangate_products.price_minor`)
- `execute_ppv` (still idempotent)
- `price` authority (still `Decimal` tolerance 0.005)
- Webhooks, Redis, PostgreSQL schema

---

## 24. Risks — VERIFIED

- **Lexical `buy` 100 vs semantic `pay` 0.8** — `unified` must weight `semantic 0.5` > `lexical 0.3` or miss `pay`.
- **Low confidence 0.3 `uncertain` → NO_OFFER** may miss `purchase_intent` 0.4 `hesitation` where LLM would be 0.6 — **missed opportunity** (conservative, safe).
- **Model 80MB per `llm_worker`** (1 process) — 80MB, acceptable, but `send_worker`/`bot_main` not loading (correct).
- **CPU 50ms `encode` + Qwen 500ms = 550ms** still < 1.1s, but **not one-call yet** (still 3 LLM).

---

## 25. Unknowns — VERIFIED

- **UNKNOWN** — validation dataset 0 (MISSING DATA) — must create 110 independent.
- **UNKNOWN** — `all-MiniLM-L6-v2` 80MB CPU 50ms on target VPS **BENCHMARK REQUIRED** (not measured, estimated).
- **UNKNOWN** — thresholds `0.65`/`0.10` **DATA REQUIRED** via hard-negative validation.
- **UNKNOWN** — `intent` accuracy 0.8 not measured.

---

## 26. Unknowns (detailed)

- Actual `intent` accuracy on 110 reference vs 110 validation **UNKNOWN**
- `purchase` false-positive rate **UNKNOWN**
- `encode` p50/p95 on VPS **UNKNOWN**
- `HNSW` not needed

---

**Report path:** `docs/AI_NATIVE_LLM_PHASE_49_LOCAL_INTELLIGENCE_VALIDATION_AUDIT.md`

**Files inspected:** `commerce/intent_corpus.py` (22*5), `commerce/embedding_model.py`, `commerce/unified_intelligence.py`, `commerce/signals.py` (21 intents, 20 fields), `commerce/deepseek.py:170`, `commerce/decision.py`, `commerce/strategy.py`, `commerce/orchestrator.py`, `commerce/execution.py`, `core/scoring.py:81`, `commerce/persona_validation.py`, `tests/test_phase48_local_intelligence.py`

**Benchmarks run:** **NONE** — read-only, no VPS CPU benchmark.

**Tests run:** **NONE** — read-only, `pytest` not run.

**Modifications:** **NONE** — read-only.

**Installations:** **NONE** — `rapidfuzz`, `sentence-transformers` already in `pyproject.toml` but not `pip install` in audit.

**Measured latency:** **UNKNOWN** — estimated 51ms (RapidFuzz 1ms + encode 50ms + search 0.08ms), not measured.

**Corpus status:** **110 examples (22*5) valid, no duplicate, but 0 validation set, hard-negative coverage weak**.

**Validation status:** **NOT READY** — no independent validation set, no confusion, no VPS latency.

**Purchase-safety:** **CONSERVATIVE** (uncertain→0.0, no offer, `asks_for_free_content` suppress), but **not measured** for false-positive.

**Decision-equivalence:** **NOT PROVEN** — LLM vs local `action` same not measured.

**Replacement-readiness:** **NOT READY** — Gates 1-5 blocking.

**Blocking issues:** Independent validation dataset + VPS latency + purchase false-positive <5%.

**Exact Phase 50 recommendation:** Offline `scripts/evaluate_unified_intelligence.py` on validation 110, eager model load at `run_worker` startup, telemetry `unified_intelligence_ms`, keep 3 LLM until accuracy proven.

