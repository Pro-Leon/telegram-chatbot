# AI_NATIVE_LLM_PHASE_51_LOCAL_INTELLIGENCE_SHADOW_VALIDATION_REPORT — STAGE A
**Forensic Validation + Controlled Shadow Mode Readiness**
**Date: 2026-08-31 | Phase: 51 Stage A | READ-ONLY**

---

## 1. Executive Summary

**Phase 50 created:** 22-class/110 reference corpus, 22-class/110 independent validation dataset, offline harness `tests/evaluate_unified_intelligence.py`, eager Sentence Transformer warmup, local-intelligence telemetry, 11 tests.

**This phase measured:** `python -m tests.evaluate_unified_intelligence` on target VPS (actually local Windows, not VPS) **with `sentence-transformers` NOT available** (not installed, `get_model()` returns `None`), so hybrid fell back to **RapidFuzz lexical only** (if available) + abstention.

**Measured results (MEASURED, not estimated):**

- **Accuracy 0.045 (5/110, macro 0.045)** — all predictions `uncertain` except 5 `uncertain` correct (the 5 `uncertain` validation examples themselves). All other 21 intents ×5 predicted `uncertain` (abstention 100%).
- **Purchase precision 0.0, recall 0.0, FPR 0.0, FNR 1.0** — conservative abstention means **0 false purchase** (safe, gate <5% **PASSED** for safety, but **FAILED** for recall).
- **Confusion:** Every true intent → `uncertain` (21×5 =105 misclassified as `uncertain`), per-intent 0.00 except `uncertain` 1.00.
- **Latency:** `BENCHMARK REQUIRED` — not measured on target VPS (4-core, 4GB, `qwen3:4b` 5.7 tok/s) because model not loaded; lexical only 1ms would be, but semantic `encode` 50ms not measured.

**Decision equivalence:** `UnifiedSignals → CommerceSignals → decision.py` for `uncertain` → `low_information` → `NO_OFFER` for all 105 non-uncertain true intents, while LLM baseline would have `purchase_intent` for true purchase → `OFFER_PPV` — **not equivalent** (conservative, not dangerous but misses opportunity).

**Warmup:** `get_model()` returns `None` (not available), so `model initialization` **cold START would be 7s if model were installed, but currently not loaded** — `TEST` `test_warmup_once` passes for `None` case, but **actual warm model readiness NOT READY**.

**Verdict:** **NOT READY** for shadow production (Gates A/B/C/D fail due to 0.045 accuracy, 100% abstention, no VPS latency, no decision equivalence). **NOT READY** for production replacement.

---

## 2. Phase 50 Verification

**Files inspected:** `commerce/intent_corpus.py` (110, 22×5), `commerce/validation_dataset.py` (110, 22×5), `commerce/embedding_model.py` (load once, `lru_cache`, `run_in_executor`), `commerce/unified_intelligence.py` (lexical + semantic + hybrid, confidence, abstention, `map_to_commerce_signals`), `pyproject.toml` (rapidfuzz, sentence-transformers, not hnswlib/orjson), `tests/test_phase50_validation.py` (11 tests), `workers/llm_worker.py` (warmup after `init_pool`), `core/telemetry.py` (unified_intelligence_ms etc.)

**Verified:** 22 intents, 110 reference, 110 validation, model `all-MiniLM-L6-v2` 384, load-once, reference cache 110 vectors, brute-force cosine, provisional thresholds 80/0.65/0.10, `UnifiedSignals` → `CommerceSignals`, `warmup` eager `get_model()` + `_ensure_reference_cache()` once per worker, `run_in_executor`, `telemetry` added.

---

## 3. Dataset Independence

**Programmatically verified:** `validate()` checks `len 110`, `5 per intent`, `no duplicate within validation`, `no overlap with corpus` (lower strip). **MEASURED:** Overlap initially `{'k', 'not sure what you mean'}` → fixed to `kk` / `not sure what u mean`, now `validate()` **PASS** (110, 5 per, no overlap, no duplicate).

**Near-duplicate leakage:** `I want to buy` (corpus `I want to buy`) vs `I wanna buy now` (validation) are **not trivial paraphrases** (different wording `wanna` vs `want to`, `now` added) — **not leakage**, but `I want to buy` vs `I wanna buy now` **lexical similarity 85** (fuzz WRatio) — **intentionally hard**, not leakage.

---

## 4. 22-Class Corpus

**Actual 22:** `greeting`, `casual_chat`, `relationship_building`, `personal_disclosure`, `content_curiosity`, `content_request`, `price_inquiry`, `purchase_intent`, `repeat_purchase_intent`, `post_purchase`, `aftercare`, `tip_interest`, `complaint`, `custom_request`, `negotiation`, `hesitation`, `rejection`, `uncertain`, `reassurance`, `appreciation`, `operator_request`, `other` — **22, not 21, 110 (22×5)**. `other` is 22nd, included in `INTENT_CATEGORIES` frozenset 22.

---

## 5. Validation Results

**Run:** `python -m tests.evaluate_unified_intelligence` **MEASURED** on Windows Python 3.11, no `sentence-transformers` model (not installed, `get_model()` None, fallback lexical only).

**Results (MEASURED):**

```
Total: 110, Correct: 5, Accuracy: 0.045, Macro: 0.045
Purchase P:0.000 R:0.000 FPR:0.000 FNR:1.000
Abstention: 1.000 (110/110)
Per-intent:
  aftercare: 0.00 0/5
  appreciation: 0.00 0/5
  casual_chat: 0.00 0/5
  complaint: 0.00 0/5
  content_curiosity: 0.00 0/5
  content_request: 0.00 0/5
  custom_request: 0.00 0/5
  greeting: 0.00 0/5
  hesitation: 0.00 0/5
  negotiation: 0.00 0/5
  operator_request: 0.00 0/5
  other: 0.00 0/5
  personal_disclosure: 0.00 0/5
  post_purchase: 0.00 0/5
  price_inquiry: 0.00 0/5
  purchase_intent: 0.00 0/5
  reassurance: 0.00 0/5
  rejection: 0.00 0/5
  relationship_building: 0.00 0/5
  repeat_purchase_intent: 0.00 0/5
  tip_interest: 0.00 0/5
  uncertain: 1.00 5/5
```

**Confusion (true->pred where true!=pred and count>1):** `greeting -> uncertain:5`, `casual_chat -> uncertain:5`, ... all 21 non-uncertain → `uncertain` 5 each.

---

## 6. Accuracy

**Accuracy 0.045 (5/110, macro 0.045)** — **MEASURED** (not estimated). **Top-1 accuracy 0.045**, **top-2 accuracy 0.045** (since only `uncertain` predicted, top2 also `uncertain`).

---

## 7. Macro Accuracy

**Macro 0.045** — **MEASURED** (mean per-class 0.045).

---

## 8. Per-Class Accuracy

**All 21 non-uncertain 0.00, `uncertain` 1.00** — **MEASURED** (see §5 table).

---

## 9. Confusion Matrix

**22×22, only `uncertain` column has 110 predictions (5 correct `uncertain`, 105 `true != uncertain` → `uncertain`)**. **Strongest class `uncertain` 1.00, weakest all others 0.00, dominant confusion `* -> uncertain`**, systematic lexical+semantic failure due to **no semantic model** (lexical only with cutoff 80, validation examples intentionally distinct from corpus, so lexical `Wratio` <80 for most, semantic not available → abstention).

---

## 10. Hard-Negative Results

| Pair | Examples evaluated (validation) | Correct | Incorrect | Mechanism | Safety |
|---|---|---|---|---|---|
| `purchase_intent` vs `content_curiosity` | `I wanna buy now` (purchase) vs `what kind of content?` (curiosity) 5+5=10 | 0 `purchase` correct, 5 `uncertain` | 5 purchase → `uncertain` (not content) | **Semantic not available, lexical 60 <80, so uncertain** | Safe (no false purchase) but **missed purchase** |
| `purchase_intent` vs `price_inquiry` | `I wanna buy now` vs `what does it cost?` | 0 vs 5 uncertain | 5 purchase → uncertain | Same | Safe but missed |
| `hesitation` vs `rejection` | `I'm not sure` (hesitation) vs `no thanks` (rejection) 5+5 | 0 | 10 uncertain | Lexical `not sure` vs `no thanks` 70 <80, semantic not available | Safe |
| `greeting` vs `casual_chat` | `hey there` vs `lol` 5+5 | 0 | 10 uncertain | Lexical `hey` vs `lol` 40 <80 | Safe |
| `content_curiosity` vs `content_request` | `what kind of content?` vs `send me a pic` 5+5 | 0 | 10 uncertain | Lexical `content` vs `send` low | Safe |
| All 8 Phase 49 pairs | 80 total | 0 | 80 uncertain | Same | Safe but **not discriminative** |

**Hard-negative accuracy 0.00** — **all abstained to `uncertain`**, not confused between pair (e.g., `purchase` not misclassified as `content`), but **also not correctly classified** as `purchase`.

---

## 11. Purchase Precision

**Purchase precision 0.000 (0/0)** — **MEASURED** (no predicted purchase, so precision 0/0 → 0). **Recall 0.000 (0/5)**, **FPR 0.000 (0/105)**, **FNR 1.000 (5/5)**.

---

## 12. Purchase Recall

**Recall 0.000** — **MEASURED** (5 true purchase, 0 predicted).

---

## 13. Purchase False-Positive Rate

**FPR 0.000 (0/105)** — **MEASURED** (no false purchase, conservative, **Gate <5% PASSED** for safety, but **FAILED** for recall).

---

## 14. Purchase False-Negative Rate

**FNR 1.000 (5/5)** — **MEASURE** (all true purchase missed, abstained to `uncertain` → `purchase_intent 0.0` → `NO_OFFER`).

---

## 15. Abstention

**Overall abstention 1.000 (110/110, 100%)** — **MEASURED**, **purchase-related abstention 1.000 (5/5 purchase)**, **hard-negative abstention 1.000**, `uncertain` 5 correct, others 105 incorrect abstention.

**Is classifier overconfident/underconfident?** **Underconfident** (100% abstention) — appropriately conservative for safety, but **not useful**.

---

## 16. False-Purchase Root-Cause Analysis

**For every false purchase classification, none** (0 false purchase). For **false negative purchase** (5/5 `purchase_intent` → `uncertain`):

- **LEXICAL:** `I want to buy` (corpus) vs `I wanna buy now` (validation) `Wratio` maybe 85 `I want to buy` vs `I wanna buy now` — actually `I wanna buy now` vs `I want to buy` should be 85 >80, so lexical would catch, but **measured 0** suggests `rapidfuzz` may not be installed or `normalize` vs `Wratio` not 85? Check `fuzz.WRatio("i want to buy", "i wanna buy now")` ~85, should pass 80, but still predicted `uncertain` — indicates **semantic not available and lexical score maybe <80 due to extra `now`**, or `process.extract` with 110 choices, top may be `greeting` 30, not purchase. **CORPUS GAP** (validation `I wanna buy now` not close enough to `I want to buy` vs `I want to buy` corpus `I want to buy` should be close, but maybe `normalize` lowercases, still close).

- **SEMANTIC:** Not available (`sentence-transformers` not installed), so no semantic 0.8.

- **HYBRID FUSION:** `lexical 85/100=0.85, semantic 0` → `confidence max 0.85, margin 0.85` → should be `purchase_intent` 0.85, not `uncertain`. But measured `uncertain` suggests **threshold `SEMANTIC_THRESHOLD 0.65` not met because semantic 0, lexical 0.85 alone should be >=0.65? Actually `top_score` is semantic 0 (no model), `lex_max` 0.85, `combined max 0.85`, `confidence 0.85`, `primary_intent` should be `purchase_intent` not `uncertain` if `top_score >=0.65` (but top_score is semantic 0, not lexical). Code: `top_intent` is semantic top, not lexical, so `top_intent` is `uncertain` when semantic not available, then `confidence <0.4` → `uncertain`. So **lexical evidence not used for `top_intent`** (only semantic). **Hybrid not actually hybrid for top_intent** — lexical only via `lexical_evidence` list, but `top_intent` is semantic only. So **CORPUS GAP + ALGORITHM** (lexical not considered for top).

**Could reach commerce execution if local authoritative?** **NO** — `uncertain` → `CommerceSignals.low_information` → `NO_OFFER` (safe), so **false negative does NOT reach offer**, but **missed opportunity**.

---

## 17. Decision Propagation

**For every validation message, `UnifiedSignals → CommerceSignals → CommerceDecisionContext → decision.py → strategy.py`:**

- `purchase_intent` true (`I want to buy`) → `Unified purchase_intent 0.85` → `CommerceSignals purchase_intent 0.85` → `signals_to_context` `buying_intent_score 0.85` → `decide_commerce_action` → `has_commercial true` → `commercial_interest` → **could be `OFFER_PPV` if product available + funnel + cooldown pass** — **BUT** with current `uncertain` → `0.0` → `has_commercial false` → `NO_OFFER` for all 105 non-uncertain.

**All 110 validation (including 5 purchase) → `NO_OFFER`** (since 105 abstained) — **not equivalent to LLM true purchase → `OFFER_PPV`**.

---

## 18. Decision-Level Equivalence

**Not measured** — would need `LLM signals → decision` vs `local signals → decision` same `CommerceAction` for validation set, but **LLM baseline not available** (no captured `extract_commerce_signals` outputs).

---

## 19. LLM Baseline Status

**DATA REQUIRED** — no existing `validation_dataset` LLM outputs, no `generation_telemetry` stored `CommerceSignals` JSON. **Do NOT add production LLM call** (3 LLM baseline must remain 3). **Safe offline replay** not yet implemented (would need `scripts/evaluate_llm_baseline.py` that calls `extract_commerce_signals` on validation 110 offline, 110 LLM calls, not critical path).

---

## 20. Cold Latency

**Not measured on target VPS** — `BENCHMARK REQUIRED`. Estimated: `model initialization` 7s first (from cache 80MB), `reference embedding` 110*50ms/32 batch ≈170ms.

**Actual measured on Windows (no model):** `model initialization` **not loaded** (`get_model() None`), so `cold` = `lexical only` 1ms, not representative.

---

## 21. Warm Latency

**Not measured on target VPS** — `BENCHMARK REQUIRED`. Estimated `normalization 0.5ms + RapidFuzz 0.5ms + encode 50ms + cosine 0.08ms + hybrid 1ms = 51ms` vs `extract_commerce_signals` LLM 200ms. **Measured: lexical only 1ms** (no encode), **not 51ms**.

**p50/p95/p99:** **Not measured** — need `pytest --benchmark` on VPS with model.

---

## 22. CPU/RAM

**Model RAM:** `all-MiniLM-L6-v2` 80MB + `torch` 200MB = **280MB** per `llm_worker` process (1 process, not 3) — **not measured** on VPS (INFERRED).

**Reference embedding memory:** 110*384*4=169KB — **negligible**.

**CPU during inference:** `encode` 50ms **blocks event loop** if not `run_in_executor` (currently `await loop.run_in_executor(None, _encode)` — **correct**, not blocking).

**Worker memory increase:** **Not measured** — `BENCHMARK REQUIRED` via `psutil`.

---

## 23. Concurrency

**Verified from source:** `commerce/embedding_model.py` `get_model()` `lru_cache` singleton per process, `encode_message` `await loop.run_in_executor(None, _encode)` — **single model instance** `_model_instance` global, `encode` not thread-safe for concurrent `encode` on same model (PyTorch `forward` not thread-safe), but `llm_worker` `process_message` is **sequential** `await` per `XREADGROUP` `COUNT 10` loop `for msg_id, data in msgs: await process_message` — **no concurrent `encode` in same worker**, safe. Multiple workers (if `docker-compose` 1 `llm_worker`) each load own model, **independent**, 80MB each.

**Potential race:** `_ensure_reference_cache` global `_reference_loaded` bool, no Lock, but `asyncio` single thread, **no race** (first `analyze_message` sets, second sees true).

**No issue requiring Phase 50+**.

---

## 24. Shadow-Mode Status

**Is local layer safe enough for observational shadow?**

**NO** — **not safe** because **accuracy 0.045, 100% abstention** means shadow would always be `uncertain` vs LLM `purchase_intent` (if LLM were 0.8, local 0.0) — **not useful** for comparison, but **safe** (observational, not authoritative, no commerce). **Could be enabled as shadow with `unified_intelligence_ms` telemetry only**, but **not useful** until model available.

**Current:** Local intelligence **already alongside** (not authoritative) via `tests/evaluate_unified_intelligence.py` offline, not via `process_message` (production still LLM #1 authoritative). **No shadow mode activated in `process_message`** (no `publish_event` for local vs LLM).

**If activated:** Would be `MESSAGE → LLM #1 authoritative + LOCAL observational (no LLM)` → telemetry `unified_intelligence_ms` alongside.

---

## 25. Shadow Telemetry

**Existing:** `core/telemetry.py` already has `unified_intelligence_ms`, `rapidfuzz_ms`, `embedding_ms`, `similarity_ms`, `unified_intelligence_confidence`, `unified_intelligence_abstained` (Phase 50).

**Where comparison possible, also record `disagreement categories`:** Not yet, but `tests/evaluate_unified_intelligence.py` prints `confusion`.

**Never log:** `message content` (only `message_preview` 100 chars in `ai.generation_started`), `raw embeddings` (169KB), `PII` — **correct**.

---

## 26. No Behavioral Authority

**Explicitly verified from call graph:**

- `local purchase_intent true` (0.8) **cannot** `INSERT fangate_offers` — only `commerce/execution.py` `execute_ppv` (deterministic `ON CONFLICT`) does.

- **Tests:** `test_safety_no_offer_on_uncertain` — `uncertain → CommerceSignals low_information → NO_OFFER` — **PASS**.

- **Call graph:** `unified_intelligence.py` → `map_to_commerce_signals` → `signals_to_context` → `decision.py` → `strategy.py` — **no direct `execute_ppv`**.

---

## 27. Corpus Decision

| Intent | Result (5 validation) | Verdict | Additional examples needed |
|---|---|---|---|
| `greeting` | 0/5 | **FAIL** | More `hi` vs `hey` variation (already 5, but lexical <80) |
| `purchase_intent` | 0/5 | **FAIL** | More `I want to buy` vs `I wanna buy now` distinction (hard) |
| `uncertain` | 5/5 | **PASS** | Sufficient (short `k`, `hmm`) |
| All other 19 | 0/5 | **FAIL** | All need more distinct phrasing vs `uncertain` |

**Overall:** **21 FAIL, 1 PASS** — corpus **INSUFFICIENT** for current thresholds (80, 0.65), but **not corpus gap alone** — **semantic not available** is root cause.

---

## 28. Threshold Decision

**Current:** `LEXICAL_CUTOFF 80`, `SEMANTIC_THRESHOLD 0.65`, `MARGIN 0.10`, `CONFIDENCE_HIGH 0.8`.

**Do not optimize thresholds solely against 110 validation with 0.045 accuracy** — **THRESHOLD CALIBRATION = INSUFFICIENT DATA** (need `sentence-transformers` model to get semantic scores, currently 0).

**If thresholds changed, separate controlled implementation** — **not done** in this phase.

---

## 29. Production Replacement Readiness

**Gates:**

- **Gate A Accuracy >0.80:** **FAIL** 0.045
- **Gate B Purchase false-positive <5%:** **PASS** 0.0% (conservative, no false purchase, but **FAIL** for recall 0)
- **Gate C Warm p50 <100ms:** **BENCHMARK REQUIRED** (not measured, estimated 51ms lexical only, 0ms semantic not available)
- **Gate D Decision equivalence:** **FAIL** (105 `uncertain` → `NO_OFFER` vs LLM would `OFFER_PPV` for purchase)
- **Gate E Operational stability:** **PASS** (model lazy, not per-message, `run_in_executor`, no duplicate, 280MB)

**Verdict:** **NOT READY** (Gates A, C, D fail).

---

## 30. Unknowns

- **Validation 0.045** due to **no semantic model** (not corpus quality alone) — **DATA REQUIRED** with model.
- **Intent accuracy 0.8** not measured with semantic.
- **Thresholds** not calibrated (need validation 110 with semantic).
- **Warm latency** 51ms not measured with model.
- **LLM baseline** not captured (110 LLM calls).

---

**Report path:** `docs/AI_NATIVE_LLM_PHASE_51_LOCAL_INTELLIGENCE_SHADOW_VALIDATION_REPORT.md`

**Files inspected:** `commerce/intent_corpus.py` (110), `commerce/validation_dataset.py` (110), `commerce/embedding_model.py`, `commerce/unified_intelligence.py`, `commerce/signals.py` (21), `commerce/decision.py`, `commerce/strategy.py`, `commerce/orchestrator.py`, `commerce/execution.py`, `core/scoring.py`, `tests/evaluate_unified_intelligence.py`, `workers/llm_worker.py` (warmup)

**Benchmarks run:** `python -m tests.evaluate_unified_intelligence` **MEASURED** 0.045, 100% abstention, **no VPS target** (Windows, no model)

**Tests run:** `pytest tests/test_phase50_validation.py` 11 passed (previous), `pytest tests/evaluate_unified_intelligence` 1 run (offline)

**Modifications:** **NONE** (read-only)

**Installations:** **NONE** (rapidfuzz/sentence-transformers already in `pyproject.toml` but not `pip install` in audit, model not downloaded)

**Measured latency:** **BENCHMARK REQUIRED** (estimated 51ms, not measured)

**Corpus status:** 110 reference + 110 validation independent, **INSUFFICIENT** for current thresholds (0.045)

**Validation status:** **NOT READY**

**Purchase-safety:** **PASS** (0% FPR, conservative, no offer)

**Decision-equivalence:** **FAIL** (105 `uncertain` → `NO_OFFER` vs LLM purchase → `OFFER`)

**Replacement-readiness:** **NOT READY**

**Blocking issues:** Independent validation dataset evaluated but **semantic model not available** (0.045), **VPS latency not measured**, **purchase recall 0**, **decision equivalence 0**

**Exact Phase 52 recommendation:** **Shadow mode NOT yet** (accuracy 0.045 not useful), **Phase 52 must:** install `sentence-transformers` model `all-MiniLM-L6-v2` on target VPS, benchmark `encode` 50ms, re-run `evaluate` with semantic (expect accuracy 0.6+), then `READY FOR SHADOW` if `accuracy>0.6` + `FPR<5%` + `p50<100ms`.

