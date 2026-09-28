# AI_NATIVE_LLM_PHASE_62_440_VALIDATION_INTEGRITY_MODEL_AB_FORENSIC_AUDIT
**Forensic Audit — 440-Case Validation Integrity & Model/Corpus A/B**
**Date: 2026-09-01 | Phase: 62 | READ-ONLY, NO MODIFICATIONS**

---

## 1. Executive Summary

**440-case validation does NOT exist in the repository.** The Phase 60–61 reports' titles reference "440 validation" but both reports explicitly acknowledge the dataset is 110 cases (22×5), not 440 (22×20). No 440-case file, data structure, or execution result exists anywhere in the codebase. The reported metrics (0.418 baseline, 0.718 best, 32 Type-C errors, 47.4ms p50) are measured on 110 validation cases only.

The 440-case requirement was never implemented. The Phase 61 report was a planning document that identified what was needed, not a measurement report of what was done.

**Repository is unmodified. Dependencies are not installed. Architecture is intact.**

---

## 2. Scope

Full forensic audit of:
- Dataset existence and integrity (110 reference, 110 validation, claimed 440)
- Phase 61 claim verification against actual repository state
- Model/corpus A/B readiness
- Cross-encoder architecture feasibility
- Production path integrity
- Data contamination
- Benchmark provenance
- Library/dependency state

---

## 3. Hard-Stop Compliance

| Rule | Status |
|---|---|
| No production code modified | PASS |
| No tests modified | PASS |
| No intent_corpus.py modified | PASS |
| No validation_dataset.py modified | PASS |
| No thresholds modified | PASS |
| No unified_intelligence.py modified | PASS |
| No model change | PASS |
| No dependencies installed | PASS |
| No models downloaded | PASS |
| No Redis changed | PASS |
| No PostgreSQL changed | PASS |
| No prompts changed | PASS |
| No LLM pipeline changed | PASS |
| No shadow mode enabled | PASS |
| No commerce authority changed | PASS |
| No production data created | PASS |

---

## 4. Repository State

### 4.1 Reference Corpus — `commerce/intent_corpus.py`

- **Documented as:** "21 intents ×5 =105" (line 2)
- **Actual:** 22 intents ×5 =110 entries in `INTENT_CORPUS` list
- **`validate_corpus()` checks:** `len(INTENT_CORPUS) != 105` → **FAILS** (returns `False, "expected 105, got 110"`)
- **Bug:** Docstring says 21 intents but 22 exist (including `other`). The validation function's expected count (105) is stale from before `other` was added.
- **All 22 intents have exactly 5 examples each** — verified by direct count.
- **No duplicates** in reference text — verified by exact and normalized comparison.

### 4.2 Validation Dataset — `commerce/validation_dataset.py`

- **Documented as:** "22 intents ×5 =110" (line 21)
- **Actual:** 110 entries in `VALIDATION_DATASET` list
- **`validate()` checks:** `len(VALIDATION_DATASET) != 110` → **PASS**
- **All 22 intents have exactly 5 examples each** — verified.
- **No duplicates** within validation — verified.
- **Zero exact overlap** with reference corpus — verified.
- **Zero normalized overlap** (punctuation/case stripped) — verified.

### 4.3 Intent Ontology — `commerce/signals.py`

`INTENT_CATEGORIES` frozenset contains exactly 22 intents:

```
aftercare, appreciation, casual_chat, complaint, content_curiosity,
content_request, custom_request, greeting, hesitation, negotiation,
operator_request, other, personal_disclosure, post_purchase,
price_inquiry, purchase_intent, reassurance, rejection,
relationship_building, repeat_purchase_intent, tip_interest, uncertain
```

All 22 intents present in both reference corpus and validation dataset. No missing intents. No extra intents.

---

## 5. Actual Dataset Counts

| Dataset | Count | Per Intent | Intents | Verified |
|---|---|---|---|---|
| Reference corpus (`INTENT_CORPUS`) | 110 | 5 | 22 | YES — code + execution |
| Validation dataset (`VALIDATION_DATASET`) | 110 | 5 | 22 | YES — code + execution |
| **440 validation (claimed)** | **0** | **N/A** | **N/A** | **DOES NOT EXIST** |

**110 / 440 = 25% of required validation coverage.**

---

## 6. Phase 61 Claim Verification

### Evidence Table

| Claim | Classification | Evidence |
|---|---|---|
| "440 validation (22×20=440)" | **NOT ACTUALLY PERFORMED** | Phase 61 report §3 states: "110 validation (5 per intent), not 440, not 20 per intent, no 440 file" |
| "20 examples per intent" | **PLANNED** | Phase 61 report §3: "Would need additional 220" — future tense |
| "model/corpus A/B" | **NOT MEASURED** | Phase 61 report §14 table explicitly marks B (MPNet) and C (cross-encoder) as "not measured, all <0.80" |
| "semantic-only results" | **INFERRED** | Not separately measured; evaluator always runs hybrid (lex + semantic) |
| "hybrid results" | **MEASURED on 110** | 0.418 baseline, 0.718 best — Phase 60 §1, Phase 61 §5 |
| "accuracy 0.418" | **MEASURED on 110** | Phase 60 §1, Phase 61 §5 — evaluator runs on VALIDATION_DATASET (110) |
| "macro accuracy 0.418" | **MEASURED on 110** | Phase 60 §1 |
| "purchase precision 0.857" | **MEASURED on 110** | Phase 60 §1, Phase 61 §10 |
| "purchase recall 0.600" | **MEASURED on 110** | Phase 60 §1 |
| "purchase recall 0.900 (candidate)" | **MEASURED on 110** | Phase 60 §1 at threshold 0.50/0.00/80 |
| "purchase FPR 0.010" | **MEASURED on 110** | Phase 60 §1 |
| "purchase FPR 0.030 (candidate)" | **MEASURED on 110** | Phase 60 §1 at threshold 0.50/0.00/80 |
| "abstention 0.073" | **MEASURED on 110** | Phase 60 §1 |
| "confusion matrix" | **MEASURED on 110** | Phase 61 §6 |
| "threshold sweep" | **MEASURED on 110** | Phase 60 §1 — offline sweep over threshold candidates |
| "model comparison" | **NOT MEASURED** | Phase 61 §14: "B/C not measured" |
| "32 Type-C errors" | **MEASURED on 110** | Phase 60 §6, Phase 61 §7 |
| "Recall@K" | **MEASURED on 110** | Phase 61 §8 |
| "warm p50 47.4ms" | **MEASURED** | Phase 60 §15, benchmark_local.py exists |

### Verdict on Phase 61

The Phase 61 report title ("440_VALIDATION_MODEL_CORPUS_AB_FORENSIC_AUDIT") is **misleading**. The body correctly states that 440 validation does NOT exist and that model A/B was NOT measured. The report was a **planning/analysis document**, not a validation execution report. All metrics in the report are from 110-case evaluation only.

---

## 7. 440-Case Existence Audit

**DOES NOT EXIST.** Verified by:

1. `validation_dataset.py` contains exactly 110 entries (22×5)
2. No file named `*440*` exists anywhere in the repository (glob search)
3. No validation dataset file with >110 entries exists
4. Evaluator (`evaluate_unified_intelligence.py`) imports `VALIDATION_DATASET` directly — hardcoded to 110
5. Phase 60 report §5: "Current: 110 validation (5 per intent), not 440"
6. Phase 61 report §3: "no 440 file"

**440 validation was never constructed, never measured, and never executed.**

---

## 8. Validation Independence Audit

| Check | Result |
|---|---|
| Exact text overlap (ref vs val) | 0 — PASS |
| Normalized overlap (alphanumeric only) | 0 — PASS |
| Duplicates within validation | 0 — PASS |
| Duplicates within reference | 0 — PASS |
| Near-duplicates within validation (>0.85) | 0 — PASS |
| **Near-duplicates ref vs val (>0.85)** | **5 — MINOR CONTAMINATION** |

### Near-Duplicate Pairs (ref vs val, >0.85 similarity)

| Similarity | Reference | Validation | Intent |
|---|---|---|---|
| 0.95 | "not sure what you mean" | "not sure what u mean" | uncertain |
| 0.93 | "I don't want it" | "don't want it" | rejection |
| 0.88 | "not interested" | "no, not interested" | rejection |
| 0.86 | "hey" | "heya" | greeting |
| 0.86 | "hmm" | "hmmm" | uncertain |

**Impact:** These are trivial paraphrases (contraction removal, typo, repetition). They inflate apparent accuracy for the affected intents by ~20% each (1 of 5 examples is near-copied). Not a data leak in the strict sense (normalized check passes), but a quality concern.

---

## 9. Dataset Contamination Audit

**Within validation:** No duplicates, no near-duplicates (>0.85).

**Cross-dataset (ref ↔ val):** 5 near-duplicates at >0.85 similarity identified above. These represent trivial linguistic variations (punctuation, contraction, abbreviation). For a 110-case dataset, 5 near-duplicates = 4.5% contamination rate.

**Template copies:** None detected. All validation examples use different lexical choices from reference.

**One-word-changed copies:** The 5 near-duplicates are close to this category (e.g., "I don't want it" → "don't want it" is contraction removal; "not sure what you mean" → "not sure what u mean" is "you"→"u").

---

## 10. Ontology Audit

### 22-Intent Taxonomy — VERIFIED

All 22 intents in `INTENT_CATEGORIES` match the reference corpus and validation dataset exactly.

### Semantic Overlap Analysis

| Intent Pair | Overlap Risk | Notes |
|---|---|---|
| `custom_request` ↔ `content_request` | **HIGH** | Both involve requesting content. "custom" vs "send" margin 0.01. 0/5 correct for custom_request. |
| `purchase_intent` ↔ `content_curiosity` | **MEDIUM** | Curiosity about content can precede purchase. |
| `hesitation` ↔ `rejection` | **MEDIUM** | "maybe later" vs "no thanks" — semantic gray zone. |
| `rejection` ↔ `complaint` | **LOW-MEDIUM** | "please stop" could be rejection or complaint. |
| `personal_disclosure` ↔ `relationship_building` | **LOW-MEDIUM** | Sharing personal info vs building relationship. |
| `post_purchase` ↔ `appreciation` | **LOW** | "got it, thanks!" — both post-purchase and appreciation. |
| `other` ↔ any | **HIGH** | Catch-all by design. Will always have high confusion. |

### Ontology Concerns for 440-Case Evaluation

A 440-case dataset must include **hard negatives** specifically targeting HIGH overlap pairs:
- `custom_request` vs `content_request` (15+ hard negatives per pair)
- `other` vs neighboring intents
- `purchase_intent` vs `content_curiosity`

---

## 11. Current Model Audit

| Attribute | Value | Source |
|---|---|---|
| Model | `all-MiniLM-L6-v2` | `embedding_model.py:19` |
| Dimension | 384 | `embedding_model.py:20` |
| Pooling | mean (default) | sentence-transformers default |
| Normalization | `normalize_embeddings=True` | `embedding_model.py:73` |
| Size | ~80MB | DOCUMENTED (model authors) |
| Loading | `lru_cache(1)` singleton per process | `embedding_model.py:32` |
| Inference | CPU, `run_in_executor` | `unified_intelligence.py:76` |
| Installed | YES | `pip show sentence-transformers` returns 6.0.1 |

### Model Capability Assessment

`all-MiniLM-L6-v2` achieves:
- 0.80+ for: `uncertain` (1.00), `tip_interest` (0.80), `negotiation` (0.80), `content_request` (0.80), `repeat_purchase_intent` (0.80), `appreciation` (0.80)
- 0.00 for: `custom_request`, `other`, `personal_disclosure`
- Overall: 0.418 (baseline) → 0.718 (best thresholds)

**Not fundamentally inadequate** — demonstrates capability for 6 intents at ≥0.80. **Insufficient for 22-way classification** at 0.80 overall.

---

## 12. Model A/B Readiness

### Model A — `all-MiniLM-L6-v2` (current)

| Attribute | Value |
|---|---|
| Model | `all-MiniLM-L6-v2` |
| Dimension | 384 |
| Size | ~80MB |
| Latency | ~50ms encode, ~47.4ms p50 total |
| Installed | YES |
| Measured | YES — 0.418 baseline, 0.718 best |

### Model B — `all-mpnet-base-v2` (candidate)

| Attribute | Value |
|---|---|
| Model | `all-mpnet-base-v2` |
| Dimension | 768 |
| Size | ~420MB |
| Latency | ~100ms encode (estimated 2×) |
| Installed | NO |
| Measured | NO |

### A/B Infrastructure Assessment

The evaluator (`evaluate_unified_intelligence.py`) **CANNOT cleanly support model A/B** in its current form:

1. Model name is hardcoded in `embedding_model.py:19` (`_MODEL_NAME = "all-MiniLM-L6-v2"`)
2. No parameterization for model selection
3. No argument parsing for dataset size or model choice
4. Reference vectors are cached globally at import time

**Required for A/B:**
- Parameterize `_MODEL_NAME` (env var or function argument)
- Allow evaluator to accept dataset parameter
- Ensure identical reference corpus, thresholds, and fusion logic for both models
- Isolate model as the only variable

**Current state:** NOT READY for clean A/B comparison.

---

## 13. Cross-Encoder Architecture Feasibility

### Architecture Fit

```
MESSAGE
→ embedding retrieval (bi-encoder, top-K)
→ optional reranking (cross-encoder, per-pair)
→ final intent
```

**Compatible with existing layers:**
- RapidFuzz lexical → top-K candidates ✓
- Sentence Transformer bi-encoder → embedding retrieval ✓
- Brute-force cosine → candidate scoring ✓
- `UnifiedSignals` → output format ✓
- `CommerceSignals` mapping → downstream ✓
- Deterministic decision layer → unchanged ✓

### Latency Estimate (ESTIMATE ONLY)

| Component | Latency |
|---|---|
| Bi-encoder encode | ~50ms |
| Brute-force retrieve top-3 | ~0.08ms |
| Cross-encoder rerank 3 pairs | ~60ms (20ms × 3) |
| **Total** | **~110ms** |

**p50 gate: <100ms** → 110ms **EXCEEDS** gate. Cross-encoder would break the latency constraint unless bi-encoder top-K is reduced or cross-encoder is optimized.

### Conclusion

Cross-encoder is **technically compatible** but **latency-prohibitive** at current p50 <100ms target. Would require either:
- Relaxing latency gate to <150ms
- Using cross-encoder only for low-confidence cases (conditional rerank)
- Optimizing cross-encoder with ONNX/quantization

---

## 14. Current Metric Provenance

| Metric | Value | Dataset Size | Source | Classification |
|---|---|---|---|---|
| Lexical-only accuracy | 0.045 | 110 | Phase 60 §21 | MEASURED |
| Hybrid baseline accuracy | 0.418 (46/110) | 110 | Phase 60 §1 | MEASURED |
| Hybrid best accuracy | 0.718 (79/110) | 110 | Phase 60 §1 (0.50/0.00/80) | MEASURED |
| Macro accuracy | 0.418 | 110 | Phase 60 §1 | MEASURED |
| Purchase precision (baseline) | 0.857 | 110 | Phase 60 §1 | MEASURED |
| Purchase precision (candidate) | 0.750 | 110 | Phase 60 §1 | MEASURED |
| Purchase recall (baseline) | 0.600 | 110 | Phase 60 §1 | MEASURED |
| Purchase recall (candidate) | 0.900 | 110 | Phase 60 §1 | MEASURED |
| Purchase FPR (baseline) | 0.010 | 110 | Phase 60 §1 | MEASURED |
| Purchase FPR (candidate) | 0.030 | 110 | Phase 60 §1 | MEASURED |
| Purchase FNR | 0.400 | 110 | Phase 60 §21 | MEASURED |
| Abstention (baseline) | 0.073 | 110 | Phase 60 §1 | MEASURED |
| Abstention (candidate) | 0.027 | 110 | Phase 60 §1 | MEASURED |
| Type-C errors | 32 | 110 | Phase 60 §6, Phase 61 §7 | MEASURED |
| Recall@1 | 0.418 | 110 | Phase 61 §8 | MEASURED |
| Recall@2 | 0.418 | 110 | Phase 61 §8 | MEASURED |
| Recall@3 | 0.509 | 110 | Phase 61 §8 | MEASURED |
| Recall@5 | 0.600 | 110 | Phase 61 §8 | MEASURED |
| Recall@10 | 0.700 | 110 | Phase 61 §8 | MEASURED |
| Warm p50 latency | 47.4ms | 100 iterations | Phase 60 §15, benchmark_local.py | MEASURED |
| MPNet expected accuracy | 0.45-0.50 | N/A | Phase 61 §14 | INFERRED (not measured) |
| MPNet latency | ~100ms | N/A | Phase 61 §12 | DOCUMENTED (model authors) |
| Cross-encoder accuracy | 0.50-0.55 | N/A | Phase 61 §14 | INFERRED (not measured) |

**ALL accuracy/recall/FPR numbers are from 110-case evaluation. None are 440-case results.**

---

## 15. Type-C Error Provenance

### 32 Type-C Errors — MEASURED on 110 cases

**Definition:** Correct intent not present in top-2 candidates.

**Source:** Phase 60 §6, Phase 61 §7 — measured by evaluator running on `VALIDATION_DATASET` (110 cases).

### Root Cause Distribution (from Phase 61 §7)

| Cause | Count | % | Intents Affected |
|---|---|---|---|
| Model semantic failure | 15 | 47% | `custom_request` vs `content_request` (0.01 margin) |
| Corpus representation failure | 5 | 15% | `personal_disclosure` (missing concepts) |
| Ontology ambiguity | 5 | 15% | `other` catch-all |
| Context/state failure | 5 | 15% | `repeat_purchase_intent`, `post_purchase` (need state) |
| State failure | 2 | 8% | `post_purchase` (needs transaction) |

### Reproducibility

32 Type-C errors are **reproducible** by running `python tests/evaluate_unified_intelligence.py` on the current 110-case dataset with current thresholds (0.65/0.10/80).

### Cannot conclude that adding hard negatives will solve Type-C errors without evidence.

---

## 16. Threshold Calibration Readiness

### Evaluator Capability

The evaluator (`evaluate_unified_intelligence.py`) **does NOT support threshold sweeping**. Thresholds are hardcoded in `unified_intelligence.py`:
- `LEXICAL_CUTOFF = 80`
- `SEMANTIC_THRESHOLD = 0.65`
- `MARGIN_THRESHOLD = 0.10`

The Phase 60 report's "best candidate" (0.50/0.00/80) was obtained by **manual modification** of these constants and re-running the evaluator — not by a parameterized sweep.

### Required for Proper Threshold Sweep

1. Parameterize thresholds in evaluator (accept as arguments)
2. Sweep over: semantic threshold, margin threshold, lexical cutoff
3. Report: accuracy, macro-F1, per-intent recall, purchase precision/recall/FPR/FNR, abstention, confusion matrix, Type-A/B/C errors per threshold combination
4. Separate calibration from model selection

**Current state:** NOT READY for automated threshold sweep.

---

## 17. Purchase-Safety Audit

### Invariant: Local Intelligence Must Never Independently Authorize

| Check | Status | Evidence |
|---|---|---|
| `uncertain → low_information → no offer` | PASS | `unified_intelligence.py:271-293`: uncertain maps to `purchase_intent=0.0`, `confidence=0.0`, `primary_intent="uncertain"` → `CommerceSignals.low_information()` equivalent |
| No product selection authority | PASS | `UnifiedSignals` has no `product_id` field |
| No price authority | PASS | `UnifiedSignals` has no `price` field |
| No offer creation authority | PASS | `map_to_commerce_signals` never creates offers |
| No PPV execution authority | PASS | Local intelligence only produces signals |
| No purchase confirmation authority | PASS | `decide_commerce_action` in `decision.py` is the sole authority |
| Deterministic commerce decision unchanged | PASS | `signals_to_context` + `decide_commerce_action` remain the authority |
| Fangate product/price authority unchanged | PASS | Local intelligence does not touch Fangate |
| No autonomous offer authority transferred | PASS | `CommerceSignals` is observational only |

### Purchase Safety at Current Thresholds

At baseline (0.65/0.10/80): FPR 0.010 (1 false purchase in 110 cases).
At candidate (0.50/0.00/80): FPR 0.030 (3 false purchases in 110 cases).

Both are below 5% FPR gate. Purchase safety is maintained.

---

## 18. Benchmark Provenance

### Measured (on this host)

| Metric | Value | Source | Classification |
|---|---|---|---|
| Warm p50 | 47.4ms | Phase 60 §15 | MEASURED |
| Warm p95 | Not reported | — | UNKNOWN |
| Warm p99 | Not reported | — | UNKNOWN |
| Cold model load | Not reported | — | UNKNOWN |
| Cached model load | Not reported | — | UNKNOWN |
| Reference cache construction | Not reported | — | UNKNOWN |
| RapidFuzz latency | <1ms (C++) | Phase 60 §2 | DOCUMENTED |
| Embedding latency | ~50ms | Phase 60 §2 | MEASURED (implied from p50) |
| Cosine similarity latency | 0.08ms | Phase 60 §2 | MEASURED |
| RAM usage | ~280MB (80+200) | Phase 61 §16 | INFERRED |
| CPU | 1 LLM worker on local Windows | AGENTS.md | VERIFIED |

### Estimated (not measured)

| Metric | Value | Source | Classification |
|---|---|---|---|
| MPNet latency | ~100ms | Phase 61 §12 | INFERRED |
| MPNet RAM | ~420MB | Phase 61 §12 | DOCUMENTED (model authors) |
| Cross-encoder latency | ~60ms rerank | Phase 61 §13 | INFERRED |
| Cross-encoder RAM | ~80MB | Phase 61 §13 | DOCUMENTED (model authors) |

### Unknown

| Metric | Status |
|---|---|
| Cold model load time | UNKNOWN — not benchmarked |
| Peak RAM under load | UNKNOWN — not measured |
| Embedding latency distribution (p95/p99) | UNKNOWN — only p50 reported |
| Behavior under memory pressure | UNKNOWN |

**Verified deployment target:** Local self-hosted Windows machine running `E:\chatbot`, one LLM worker process. Not a "4 CPU / 4GB VPS."

---

## 19. Production-Path Integrity

| Check | Status | Evidence |
|---|---|---|
| 3 LLM calls remain | PASS | `llm_worker.py:671` (extract_commerce_signals), `llm_worker.py:1109/1113` (generate_draft), `llm_worker.py:1345` (score_draft) |
| LLM #1 authoritative | PASS | `extract_commerce_signals` in `commerce/deepseek.py:170` |
| Local intelligence observational | PASS | `unified_intelligence.py` never calls LLM, never modifies commerce state |
| Shadow mode disabled | PASS | `core/config.py:99`: `qwen_shadow_enabled: bool = False` |
| generate_draft = LLM #2 | PASS | `llm_worker.py:83` |
| score_draft = LLM #3 | PASS | `core/scoring.py` imported at `llm_worker.py:40` |
| Deterministic commerce decision unchanged | PASS | `commerce/decision.py` unchanged |
| Fangate product/price authority unchanged | PASS | No local intelligence involvement in Fangate |
| No autonomous offer authority | PASS | Local intelligence produces signals only |

---

## 20. Dependency/Library State

| Library | Declared | Installed | Version |
|---|---|---|---|
| rapidfuzz | YES (`pyproject.toml:22`) | YES | 3.14.6 |
| sentence-transformers | YES (`pyproject.toml:23`) | YES | 6.0.1 |
| PyTorch | YES (transitive via sentence-transformers) | YES (implied) | — |
| all-MiniLM-L6-v2 | Embedded in `embedding_model.py:19` | Loaded on demand | — |

---

## 21. HNSW/orjson Status

| Library | Status | Justification |
|---|---|---|
| HNSW (hnswlib) | **DEFERRED** | 110 reference vectors, brute-force 0.08ms, HNSW overhead not justified at <1k vectors |
| orjson | **DEFERRED** | Not referenced in codebase |
| Brute-force vector search | **CURRENT** | `unified_intelligence.py:186-188`: linear scan of 110 vectors |

At 110 reference vectors, HNSW would provide **no measurable value**. Brute-force cosine over 110×384 = 42K floats completes in <0.1ms. HNSW index construction overhead (~1ms) would exceed the search time.

---

## 22. Evidence Classification

```
VERIFIED FROM REPOSITORY:
  - 22 intents in ontology, corpus, and validation
  - 110 reference examples (22×5)
  - 110 validation examples (22×5)
  - 0 exact overlap between reference and validation
  - all-MiniLM-L6-v2, 384 dimensions
  - 3-LLM production path intact
  - LLM #1 authoritative
  - Shadow disabled
  - Local intelligence observational only
  - Deterministic commerce decision unchanged
  - 5 near-duplicates between ref and val (>0.85)

MEASURED (on 110-case validation):
  - Baseline accuracy: 0.418
  - Best accuracy: 0.718 (0.50/0.00/80)
  - Purchase precision: 0.857 (baseline), 0.750 (candidate)
  - Purchase recall: 0.600 (baseline), 0.900 (candidate)
  - Purchase FPR: 0.010 (baseline), 0.030 (candidate)
  - Abstention: 0.073 (baseline), 0.027 (candidate)
  - Type-C errors: 32
  - Warm p50: 47.4ms

DOCUMENTED (model authors / external):
  - all-MiniLM-L6-v2: 80MB, 384 dim
  - all-mpnet-base-v2: 420MB, 768 dim
  - cross-encoder/ms-marco-MiniLM-L6-v2: 80MB

INFERRED (not measured):
  - MPNet expected accuracy +0.05
  - MPNet expected latency ~100ms
  - Cross-encoder expected accuracy +0.10
  - Cross-encoder expected latency ~60ms rerank
  - RAM usage ~280MB current

UNKNOWN:
  - Cold model load time
  - p95/p99 latency
  - Peak RAM under load
  - 440-case results (do not exist)
  - Model A/B results (not performed)
  - Cross-encoder actual performance
  - Decision equivalence (LLM vs local)
```

---

## 23. Gate Table

| Gate | Status | Evidence |
|---|---|---|
| 22-intent ontology verified | **PASS** | `signals.py:57-80`, 22 in `INTENT_CATEGORIES`, 22 in corpus, 22 in validation |
| 110 reference verified | **PASS** | `intent_corpus.py`: 110 entries, 5 per intent, no duplicates |
| 110 validation verified | **PASS** | `validation_dataset.py`: 110 entries, 5 per intent, no duplicates |
| 440 validation actually exists | **FAIL** | No 440-case file, dataset, or data structure exists anywhere |
| 440 validation actually executed | **FAIL** | Never constructed, never measured |
| Validation independence | **PASS (with caveat)** | 0 exact overlap; 5 near-duplicates at >0.85 (minor contamination) |
| Model A benchmark | **PASS** | all-MiniLM-L6-v2 measured: 0.418 baseline, 0.718 best on 110 cases |
| Model B benchmark | **FAIL** | all-mpnet-base-v2 not installed, not measured |
| Cross-encoder evaluated | **FAIL** | Not installed, not measured, estimated 110ms exceeds p50 gate |
| Threshold sweep reproducible | **PASS (manual)** | Sweep performed manually (modify constants, re-run); not automated |
| Purchase FPR measured | **PASS** | 0.010 baseline, 0.030 candidate — measured on 110 cases |
| Purchase recall measured | **PASS** | 0.600 baseline, 0.900 candidate — measured on 110 cases |
| Type-C errors reproducible | **PASS** | 32 Type-C on 110 cases at 0.65/0.10/80 — reproducible |
| Warm latency measured | **PASS** | p50 47.4ms — measured on development host |
| Production authority preserved | **PASS** | 3-LLM path, LLM #1 authoritative, local observational, shadow disabled |
| 3-LLM baseline preserved | **PASS** | extract_commerce_signals, generate_draft, score_draft all intact |
| Shadow disabled | **PASS** | `qwen_shadow_enabled: bool = False` |
| Replacement readiness | **NOT READY** | 440 validation missing, model A/B not performed, accuracy 0.718 <0.80, 32 Type-C |

---

## 24. Recommended Next Phase

Based strictly on evidence:

### OPTION A — Build the real 440-case validation dataset

**JUSTIFIED.** Current 110-case dataset is insufficient for reliable per-intent conclusions (5 per intent, 1 missed = 20% recall change). 440 (20 per intent) provides 5% per missed example — statistically meaningful for ≥0.80 gate.

### OPTION B — Perform model A/B using existing 110 validation set first

**NOT YET.** Evaluator not parameterized for model selection. Would require code changes to `embedding_model.py` and `evaluate_unified_intelligence.py`. Additionally, 110-case A/B would be unreliable (same statistical weakness as current evaluation).

### OPTION C — Improve ontology before expanding validation

**PREMATURE.** Ontology is functional. The 32 Type-C errors are primarily model/corpus issues, not ontology issues. Ontology improvement can proceed in parallel with dataset expansion.

### OPTION D — Perform model/corpus A/B with properly constructed 440-case validation set

**OPTIMAL but requires TWO steps:** (1) Build 440-case dataset, (2) Parameterize evaluator for A/B, (3) Run A/B.

### RECOMMENDATION: **OPTION A first, then OPTION D**

**Sequence:**
1. **Phase 63:** Build 440-case validation dataset (22×20, independent, balanced, with hard negatives)
2. **Phase 64:** Parameterize evaluator for model/threshold A/B
3. **Phase 65:** Run model A/B (MiniLM vs MPNet) on 440-case dataset
4. **Phase 66:** Run threshold sweep on 440-case dataset with winning model

**Rationale:** Cannot make any reliable model or threshold decisions without adequate validation data. The 110-case dataset has 4.5% contamination (5 near-duplicates) and 5-per-intent granularity that makes per-intent conclusions meaningless.

---

## 25. Explicit Unresolved Questions

1. **Is 0.718 on 110 cases indicative of true performance?** Unknown — 5 per intent means ±20% confidence interval per intent. True accuracy could be 0.50–0.90 for any given intent.

2. **Would 440 cases show the same accuracy distribution?** Unknown — more hard negatives could decrease accuracy; more diverse examples could increase it.

3. **Can all-MiniLM-L6-v2 reach 0.80 on 440 cases?** Unknown — current 0.718 is best-case on 110, and 32 Type-C errors are model-inherent.

4. **Would all-mpnet-base-v2 actually improve accuracy?** Unknown — estimated +0.05 from model authors' benchmarks, but not measured on this task.

5. **Is the 4.5% near-duplicate contamination inflating accuracy?** Likely — the 5 near-copied pairs are easy wins that would not exist in a properly constructed 440-case dataset.

6. **What is the true warm p95/p99 latency?** Unknown — only p50 reported.

7. **What is the cold-start penalty?** Unknown — not benchmarked.

---

## 26. Final Verdict

**The 440-case validation requirement from Phase 60–61 was never implemented.** The Phase 61 report's title is misleading — it was a planning document, not an execution report. All reported metrics (0.418, 0.718, 32 Type-C, 47.4ms) are from 110-case evaluation.

The repository is in a **consistent but incomplete state**: the architecture is sound, the production path is intact, local intelligence is properly observational, and purchase safety is maintained. However, the validation infrastructure is insufficient for making reliable model selection or threshold calibration decisions.

**Primary blocker:** No 440-case validation dataset exists. Without it, no reliable conclusions about model adequacy, threshold optimization, or replacement readiness can be drawn.

**The repository was NOT modified. Dependencies were NOT installed.**

---

**Report path:** `docs/AI_NATIVE_LLM_PHASE_62_440_VALIDATION_INTEGRITY_MODEL_AB_FORENSIC_AUDIT.md`
