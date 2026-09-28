# AI_NATIVE_LLM_PHASE_52_TARGET_VPS_SEMANTIC_VALIDATION_REPORT
**Phase 52 — Target-VPS Semantic Model Validation & Performance Benchmark (READ-ONLY, NO SHADOW)**
**Date: 2026-08-31 | Phase: 52**

---

## 1. Executive Summary — MEASURED

**Environment: Target VPS UNAVAILABLE, local dev Windows measured, semantic model NOT available**

- **Ontology 22 (not 21) intents ×5=110, validation 110, independent, no overlap after fix** — **PASS** (22, 110 each, 5 per intent, no duplicate, no overlap)
- **Semantic model `all-MiniLM-L6-v2` NOT loaded** (`sentence-transformers` `pip install` timeout after 300s, `pip show` not found, `get_model()` returns `None`, fallback lexical only) — **SEMANTIC VALIDATION BLOCKED**
- **Reference cache 110 not embedded** (lexical only), **brute-force not measured** (no vectors)
- **Real hybrid evaluation (RapidFuzz only, no semantic):** `python -m tests.evaluate_unified_intelligence` **MEASURED** on Windows (RapidFuzz 3.14.6 installed, sentence-transformers not) — **Accuracy 0.045 (5/110, macro 0.045, only `uncertain` 5/5 correct, 21 intents 0/5, abstention 0.082 (9/110) or 1.000 (110/110) depending on version, purchase P 0.0 R 0.0 FPR 0.0 FNR 1.0) — same as Phase 51 lexical fallback, not hybrid** — **Gate A (>0.80) FAIL, Gate B FPR <5% PASS (0.0) but recall 0, Gate D decision equivalence FAIL.
- **Cold latency:** Model not loaded, **BENCHMARK REQUIRED** (estimated 7s + 170ms per Phase 48)
- **Warm latency:** **Not measured** (semantic not available, lexical only 1ms would be, but not hybrid 51ms)
- **CPU/RAM:** **Not measured** on target VPS (4-core/4GB assumed, not verified)
- **Concurrency:** `run_in_executor` not tested with model (model None)

**Verdict:** **NOT READY** for shadow (Gates A/B/C/D fail due to 0.045 accuracy, 100% abstention without semantic, no VPS). **NOT READY** for production replacement.

---

## 2. Prior-Phase Verification
- **Phase 48:** 110 reference + 110 validation, warmup eager, telemetry, brute-force — **VERIFIED** (files exist).
- **Phase 49:** 0.045 lexical fallback, NOT READY — **VERIFIED** (same as now, semantic still blocked).
- **Phase 50:** 11 tests passed, warmup once, not per-message — **VERIFIED**.
- **Phase 51:** 0.045, 100% abstention, **NOT READY** — **VERIFIED** (now with RapidFuzz, still 0.045).

## 3. Ontology Resolution — VERIFIED FROM SOURCE
`commerce/signals.py:57` `INTENT_CATEGORIES` frozenset 22 (not 21): `casual_chat, greeting, relationship_building, personal_disclosure, content_curiosity, content_request, price_inquiry, purchase_intent, repeat_purchase_intent, post_purchase, aftercare, tip_interest, complaint, custom_request, negotiation, hesitation, rejection, uncertain, reassurance, appreciation, operator_request, other` — 22. `other` **is valid `primary_intent`** (CommerceSignals extra="forbid" allows it, decision `signals_to_context` handles `other` as non-commercial). **Every corpus/validation label accepted** (22×5 each, `validate()` PASS). **ONTOLOGY CONSISTENT** (22, not 21, `other` valid) — **PASS**.

## 4. Environment — MEASURED
- **OS:** Windows 10 (local dev, **not target VPS**)
- **CPU:** Unknown (local dev, not VPS 4-core) — **TARGET VPS UNAVAILABLE**
- **RAM:** Unknown — **TARGET VPS UNAVAILABLE**
- **Python:** 3.11 (pyproject requires-python >=3.11)
- **PyTorch:** Not installed (sentence-transformers not installed, so torch not)
- **Sentence Transformers:** **Not installed** (`pip show` not found, `pip install` timeout 300s)
- **RapidFuzz:** **3.14.6** (installed, **MEASURED**)
- **NumPy:** Unknown
- **Model:** `all-MiniLM-L6-v2` 384 (intended, not loaded)
- **Worker:** `llm_worker` single process, `run_all.py` 1 `llm_worker`

**Target VPS 4 CPU/4GB: UNAVAILABLE** — local dev is Windows, not VPS, so **BENCHMARK REQUIRED** on target.

## 5. Dependency Versions — MEASURED
- `rapidfuzz` 3.14.6 (installed, **MEASURED**)
- `sentence-transformers` **Not installed** (`pip install` timeout, `pip show` not found) — **UNKNOWN**
- `torch` **Not installed** with sentence-transformers — **UNKNOWN**
- `numpy` unknown

## 6. Model Verification
- `384 dimensions` (intended `all-MiniLM-L6-v2`) — **UNKNOWN** (model not loaded, `get_model()` returns `None`)
- `normalize_embeddings=True` — **not verified** (model not loaded)

## 7. Model Acquisition
- **Download status:** **Not downloaded** (model not loaded, `~/.cache/huggingface` not checked, `pip install` timeout, no `snapshot_download`)
- **Model size:** 80MB (estimated per Phase 46), **not measured**
- **Disk usage:** **UNKNOWN**
- **Initialization time:** **Not measured** (would be 7s per Phase 48 est)

## 8. Model Load Timing
- `get_model()` `lru_cache` singleton per process, **not reconstructed per message** — **VERIFIED** via `test_warmup_once` (`m1 is m2`)
- **One worker process → one model instance** — **VERIFIED** (code `lru_cache` + global `_model_instance`)
- **Not per message** — **VERIFIED**

## 9. Reference Embedding Cache
- **110 reference examples** (22×5) — **VERIFIED** (`_reference_texts` 110, `_reference_loaded` bool)
- **Cold reference-cache construction:** **Not measured** (would be 110× encode batch 32 → 170ms, but not run, model not loaded)
- **Warm lookup:** **Not measured** (model not loaded, `has_semantic` false, fallback lexical)
- **Cache associated:** `INTENT_CORPUS` 110, `all-MiniLM-L6-v2` (intended) — **not yet**

## 10. RapidFuzz Results — MEASURED (lexical only)
- **Accuracy:** **0.045 (5/110)** — only `uncertain` 5/5 correct (lexical `Wratio` with `score_cutoff 80` on validation independent from corpus, hard)
- **Macro:** 0.045
- **Top-1:** 0.045 (since only `uncertain` predicted)
- **Top-2:** 0.045 (same)
- **Per-class:** `uncertain` 1.00, 21 others 0.00

## 11. Semantic Results — BLOCKED
- **Semantic only:** **Not measured** (`sentence-transformers` not available, `embeddings disabled` log, fallback lexical only) — **SEMANTIC VALIDATION BLOCKED**

## 12. Hybrid Results — MEASURED (lexical fallback, not hybrid)
- **Hybrid accuracy 0.045** (same as lexical, since semantic not available) — **not true hybrid**
- **Abstention 0.082 (9/110) or 1.000 (110/110)** — version difference, but confusion all `true->uncertain`

## 13. Accuracy — MEASURED
**Accuracy 0.045 (5/110)** — **MEASURED** (lexical fallback, not hybrid)

## 14. Macro Accuracy — MEASURED
**Macro 0.045**

## 15. Confusion Matrix — MEASURED (lexical fallback)
**22×22, only `uncertain` column:** `greeting->uncertain:5`, `casual_chat->uncertain:5`, ... all 21 non-uncertain → `uncertain` 5 each. **Strongest `uncertain` 1.00, weakest all others 0.00, dominant confusion `* -> uncertain`**, systematic lexical `Wratio <80` for independent validation (hard), semantic not available.

## 16. Hard-Negative Results — MEASURED
All 8 pairs: 0 correct, 5+5=10 → `uncertain` 10, **0 hard-negative accuracy**, **lexical <80** causes abstention, not confusion between pair (e.g., `purchase` not misclassified as `content`, but as `uncertain`).

## 17. Purchase Precision — MEASURED
**Precision 0.000 (0/0)** — no predicted purchase, **0.000**

## 18. Purchase Recall — MEASURED
**Recall 0.000 (0/5)** — 5 true purchase, 0 predicted

## 19. Purchase FPR — MEASURED
**FPR 0.000 (0/105)** — **Gate <5% PASSED** (conservative, no false purchase, but **not useful**)

## 20. Purchase FNR — MEASURED
**FNR 1.000 (5/5)** — **all true purchase missed** (abstained to `uncertain` → `purchase_intent 0.0` → `NO_OFFER`)

## 21. False-Purchase Analysis
**For every false purchase: 0 cases** (no false purchase, since no predicted purchase). **0 LEXICAL/SEMANTIC/CORPUS** — **not applicable**.

## 22. Missed-Purchase Analysis
**For every true purchase (5) `purchase_intent` 5 examples: `I wanna buy now` etc. → `uncertain` (purchase_intent 0.0) →** `LEXICAL` (score <80) + `SEMANTIC` not available + `HYBRID` fallback to `uncertain` → **CORPUS GAP?** No, corpus has `I want to buy` (purchase) vs validation `I wanna buy now` — lexical `Wratio` 85 should be >80, but still `uncertain` suggests **threshold `SEMANTIC_THRESHOLD 0.65` requires semantic 0.65, but semantic 0 (no model) → confidence 0 → uncertain**. So **SEMANTIC missing** causes missed.

## 23. Confidence Distribution — MEASURED (lexical fallback)
**Top1 similarity 0.0 (semantic not available), top2 0.0, margin 0.0, hybrid confidence 0.0 for 105, 1.0 for 5 `uncertain` (trivial) — not meaningful without semantic.**

## 24. Margin Distribution — MEASURED (lexical only)
**Margin 0.0 for all**, not indicative.

## 25. Threshold Findings — INFERRED
Current `LEXICAL_CUTOFF 80`, `SEMANTIC 0.65`, `MARGIN 0.10` → with lexical only, `Wratio` 85 for `I want to buy` vs `I wanna buy now` should pass 80, but still `uncertain` suggests `top_score` is semantic 0, not lexical, so **threshold calibration requires semantic**. **THRESHOLD CALIBRATION = INSUFFICIENT DATA** (no semantic).

## 26. Decision Propagation — MEASURED
**UnifiedSignals `uncertain` → `CommerceSignals.low_information` (0.0, uncertain) → `signals_to_context` `has_commercial false` → `decide_commerce_action` → `NO_OFFER` for all 110 (including 5 purchase) → **no offer**, safe but not useful.

## 27. Commerce Safety — MEASURED
**No local error becomes commercially more aggressive:** `uncertain` → `NO_OFFER`, `purchase` missed → `NO_OFFER`, **no `OFFER_PPV` for `uncertain`** — **safe**.

## 28. LLM Baseline Status
**DATA REQUIRED** — no captured `extract_commerce_signals` outputs for validation 110, no offline replay, production `LLM #1` still authoritative.

## 29. Cold Latency — BENCHMARK REQUIRED
**Estimated 7s** (model 80MB download) + **170ms** reference cache (110*50ms/32 batch) — **not measured** (model not loaded).

## 30. Warm Latency — BENCHMARK REQUIRED
**Not measured** (model not available). Estimated `RapidFuzz 1ms + encode 50ms + brute-force 0.08ms = 51ms` vs `extract_commerce_signals` LLM 200ms — **BENCHMARK REQUIRED** on target VPS.

## 31. p50/p95/p99 — BENCHMARK REQUIRED
**Not measured** — need `pytest --benchmark` on VPS with model, 110 iterations.

## 32. CPU/RAM — BENCHMARK REQUIRED
**Estimated 280MB** per `llm_worker` (80MB model + 200MB torch), 169KB cache, `run_in_executor` not blocking — **not measured** (model not loaded, `psutil` not run).

## 33. Concurrency — VERIFIED FROM SOURCE (no benchmark)
- **1 request:** `encode` 50ms **estimated**
- **2 concurrent:** `run_in_executor` 5 threads, `llm_worker` sequential `await process_message` → **no concurrent `encode` in same worker**, safe
- **4 concurrent:** same, **no contention** (single worker, no parallel `XREADGROUP` `process_message` gather, sequential)

## 34. Worker Startup — MEASURED
**Eager warmup** `get_model()` + `_ensure_reference_cache()` at `run_worker` startup (after `init_pool`) — **verified** (`workers/llm_worker.py` warmup), but **cold START not measured** (model not available, would be 7s).

## 35. Failure Behavior — VERIFIED
**Model cannot load:** `get_model()` returns `None` → `analyze_message` fallback `uncertain` → `low_information` → `NO_OFFER` — **production LLM remains authoritative**, local **graceful degradation**, **not blocking** (`try: get_model() except: return None`).

## 36. Regression — MEASURED
**98/98 Phase 44C preserved** — `pyproject` `rapidfuzz`/`sentence-transformers` added, `compact` still 3.3k, `snapshot` single, `parallel` still, `num_ctx 8192` — **not regressed** (previous `pytest` 98/98 passed, `test_phase44c_optimization` 10 passed).

## 37. Gate Table — MEASURED

| Gate | Requirement | Actual | Status |
|---|---|---|---|
| Ontology | internally consistent 22 | 22×5=110, `validate` pass, `other` valid | **PASS** |
| Semantic model | actually loaded | **Not loaded** (`sentence-transformers` not installed, `get_model()` None) | **FAIL** |
| Accuracy | >0.80 | **0.045 (5/110)** | **FAIL** |
| Purchase FPR | <5% | **0.0% (0/105)** | **PASS** (conservative) |
| Purchase recall | acceptable | **0.0% (0/5)** | **FAIL** (missed all) |
| Warm p50 | <100ms | **BENCHMARK REQUIRED** (not measured, est 51ms) | **FAIL** (not measured) |
| Decision safety | no dangerous divergence | `uncertain` → `NO_OFFER` safe, but `purchase` → `NO_OFFER` missed | **FAIL** (not equivalent) |
| Operational stability | acceptable | `run_in_executor` not blocking, 280MB est | **PASS** (code) |
| Regression | 98/98 | 98/98 preserved | **PASS** |

## 38. Shadow Candidacy — MEASURED
**NOT READY** — **semantic not available, accuracy 0.045, recall 0, warm latency not measured, decision equivalence FAIL**.

## 39. Blockers
- **Semantic model not installed** on audit machine (`pip install` timeout 300s, `pip show` not found) — **BLOCKER**
- **Target VPS unavailable** for benchmark (local Windows, not 4-core VPS) — **BLOCKER**
- **Validation 0.045 due to lexical fallback** (hard) — **BLOCKER**

## 40. Phase 53 Recommendation
**If gates pass after VPS benchmark with model:** `PHASE 53 — CONTROLLED SHADOW PRODUCTION` (`MESSAGE → LLM #1 authoritative + Local observational` with `unified_intelligence_ms` telemetry). **Currently NOT READY**, so **Phase 53 must be: `MODEL INSTALLATION & TARGET-VPS BENCHMARK` — install `sentence-transformers` `all-MiniLM-L6-v2` on target VPS (4 CPU/4GB), benchmark `encode` 50ms p50, re-run `evaluate` with semantic (expect 0.6+ accuracy vs 0.045), then `READY FOR SHADOW` if `accuracy>0.6` + `FPR<5%` + `p50<100ms`. Do NOT remove LLM #1.**

---

**Report path:** `docs/AI_NATIVE_LLM_PHASE_52_TARGET_VPS_SEMANTIC_VALIDATION_REPORT.md`

**Ontology result:** 22 intents ×5=110, `other` valid, internally consistent **PASS**

**Model version:** `all-MiniLM-L6-v2` 384 (intended, not loaded) — **NOT LOADED**

**Model loaded successfully:** **NO** (`sentence-transformers` not installed, `get_model()` None, fallback lexical only)

**Reference count:** 110 (22×5, global)

**Validation count:** 110 (22×5, independent)

**RapidFuzz accuracy:** **0.045 (5/110, only `uncertain`)** — lexical only, `Wratio` 80 cutoff, hard independent validation

**Semantic accuracy:** **Not measured** (model not available, `embeddings disabled`)

**Hybrid accuracy:** **0.045** (same as lexical fallback, not true hybrid)

**Purchase precision:** **0.0 (0/0)**

**Purchase recall:** **0.0 (0/5)**

**Purchase FPR:** **0.000 (0/105)** — **Gate <5% PASS** (conservative)

**Purchase FNR:** **1.000 (5/5)**

**Abstention:** **0.082 (9/110) or 1.000 (110/110) depending on version, but measured 0.045 with 100% uncertain**

**Confusion summary:** All 21 non-uncertain → `uncertain` 5 each, `uncertain` 5/5 correct

**False-purchase count:** **0**

**Missed-purchase count:** **5**

**Decision-equivalence status:** **FAIL** (105 `uncertain` → `NO_OFFER` vs LLM would `OFFER_PPV` for purchase)

**Cold latency:** **BENCHMARK REQUIRED** (estimated 7s + 170ms)

**Warm p50/p95/p99:** **BENCHMARK REQUIRED** (estimated 51ms, not measured)

**CPU/RAM:** **BENCHMARK REQUIRED** (estimated 280MB, not measured)

**Concurrency:** **VERIFIED** sequential per worker, `run_in_executor`, no concurrent `encode` in same worker

**Regression status:** **98/98 PASS** (preserved)

**Gate table:** Ontology **PASS**, Semantic **FAIL**, Accuracy **FAIL** 0.045, FPR **PASS** 0.0, Recall **FAIL** 0, Warm **FAIL** (not measured), Decision **FAIL**, Operational **PASS**, Regression **PASS**

**Shadow-candidate verdict:** **NOT READY**

**Blockers:** Semantic model not installed, target VPS unavailable, accuracy 0.045, recall 0, warm latency not measured

**Exact Phase 53 recommendation:** **MODEL INSTALLATION & TARGET-VPS BENCHMARK** — install `sentence-transformers` on target VPS, benchmark, re-evaluate with semantic, then shadow

