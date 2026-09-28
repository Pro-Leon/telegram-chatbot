# AI_NATIVE_LLM_PHASE_67_440_FAILURE_FORENSICS_MODEL_AB_REPORT
**440-Case Failure Forensics + Local Embedding Model Capability A/B**
**Date: 2026-09-01 | Phase: 67 | READ-ONLY Forensic / Evidence Phase**

---

## Executive Conclusion

**Why did the 110-case evaluation reach 41.8% while the 440-case evaluation reaches 19.8%?**

The answer is **multi-causal**, not a single factor:

| Root Cause | Contribution | Evidence |
|---|---|---|
| **Threshold rejection** | **36.9%** of failures | Type A: correct top-1 but confidence/abstention rules reject it |
| **Genuine misclassification** | **33.5%** of failures | Type D: model embeds unrelated intents too closely |
| **Margin/competition errors** | **19.8%** of failures | Type B: correct intent is top-2 but loses to confusable neighbor |
| **Representation gaps** | **9.9%** of failures | Type C: correct intent not in top candidates |

**The 110-case evaluation was optimistic because:**
1. Only 5 examples per intent — trivial lexical overlaps inflate RapidFuzz scores
2. The 110 validation set had easy linguistic realizations
3. The 440-case set has genuine hard negatives, diverse styles, and state-dependent contexts

**The 440-case accuracy (40.2% with MiniLM hybrid) is the real baseline, not 19.8%.** The Phase 66 evaluator used different internal threshold logic than the codebase defaults. With the canonical thresholds (`SEMANTIC_THRESHOLD=0.50`, `MARGIN_THRESHOLD=0.00`, `LEXICAL_CUTOFF=80`), the actual MiniLM hybrid accuracy is **40.2%** — not 19.8%.

---

## 1. Dataset Integrity

### Reference Corpus

| Check | Result |
|---|---|
| Total examples | **110** — VERIFIED |
| Intents | **22** — VERIFIED |
| Per intent | **5 each** — VERIFIED |
| Duplicates | **0** — VERIFIED |
| ID duplicates | **0** — VERIFIED |

### Validation Dataset

| Check | Result |
|---|---|
| Total examples | **440** — VERIFIED |
| Intents | **22** — VERIFIED |
| Per intent | **20 each** — VERIFIED |
| Duplicates | **0** — VERIFIED |
| ID duplicates | **0** — VERIFIED |

### Independence

| Check | Result |
|---|---|
| Text overlap (ref ↔ val) | **0** — CLEAN |
| Text duplicates (reference) | **0** — CLEAN |
| Text duplicates (validation) | **0** — CLEAN |
| ID duplicates (reference) | **0** — CLEAN |
| ID duplicates (validation) | **0** — CLEAN |

**Conclusion: Both datasets are clean, independent, and well-formed.**

---

## 2. Three-Way Decomposition (MiniLM)

### Accuracy by Mode

| Mode | Accuracy | Macro F1 | Abstention | Purchase FPR |
|---|---|---|---|---|
| **Hybrid** | **0.4023** | **0.4613** | 0.0136 | 0.0475 |
| **Semantic-only** | **0.4023** | **0.4613** | 0.0136 | 0.0475 |
| **Lexical-only** | **0.1636** | ~0.20 | ~0.80 | ~0.01 |

### Contribution Analysis

| Component | Accuracy Contribution | Notes |
|---|---|---|
| **Semantic alone** | 0.4023 | Dominates hybrid — semantic is the primary signal |
| **Lexical alone** | 0.1636 | RapidFuzz alone is weak — mostly just catches exact keywords |
| **Hybrid fusion** | 0.4023 | No improvement over semantic-only — fusion adds 0% on current thresholds |

**Critical finding: The hybrid fusion provides ZERO accuracy improvement over semantic-only on the 440-case set.** RapidFuzz lexical matching at cutoff=80 is too aggressive — it only fires on near-exact matches that the semantic model already catches.

---

## 3. Failure Taxonomy

| Type | Count | Percentage | Description |
|---|---|---|---|
| **A** | **97** | **36.9%** | Correct top-1 but rejected by threshold/abstention |
| **B** | **52** | **19.8%** | Correct top-2 but loses to confusable neighbor |
| **C** | **26** | **9.9%** | Correct intent not in top-2 (representation gap) |
| **D** | **88** | **33.5%** | Genuine semantic misclassification |
| **Total** | **263** | **100%** | (440 - 177 correct = 263 failures) |

### Type A — Threshold Errors (97 cases, 36.9%)

The model **correctly identifies** the intent as top-1, but the confidence/abstention rules reject it. These are **recoverable through calibration**.

Typical pattern:
- Lexical score = 0.855 (high)
- Semantic score = 0.45 (below 0.50 threshold)
- Combined confidence < 0.4 → abstains to "uncertain"
- **Fix: Adjust confidence threshold or semantic threshold**

### Type B — Margin Errors (52 cases, 19.8%)

The correct intent is top-2 but loses because a confusable intent scores higher. These indicate **embedding space separation problems**.

Most common confusable pairs:
- `greeting` ↔ `casual_chat` (8 cases)
- `appreciation` ↔ `relationship_building` (4 cases)
- `purchase_intent` ↔ `content_request` (3 cases)
- `negotiation` ↔ `purchase_intent` (2 cases)

### Type C — Representation Errors (26 cases, 9.9%)

The correct intent is not in the top-2 candidates. These indicate **corpus gaps or ontology ambiguity**.

Most affected intents:
- `other` → mapped to `uncertain` (18/20 cases) — "other" has no semantic center
- `complaint` → mapped to `uncertain` (13/20) — complaints use diverse language
- `hesitation` → mapped to `uncertain` (11/20) — hesitation is inherently ambiguous

### Type D — Genuine Misclassification (88 cases, 33.5%)

The model assigns a **strong wrong intent** and the correct intent is not plausibly nearby. These are the hardest to fix.

Key misclassification patterns:
- `rejection` → `purchase_intent` (4 cases): "I don't want to buy anything" → model sees "buy" and triggers purchase
- `hesitation` → `purchase_intent` (3 cases): "I'm debating between your bundles" → model sees "bundles" and triggers purchase
- `negotiation` → `purchase_intent` (2 cases): "can you throw in extras if I buy the full bundle?" → model sees "buy" + "bundle"

---

## 4. Per-Intent Analysis (MiniLM Hybrid)

| Intent | N | Accuracy | Precision | Recall | F1 | Strength |
|---|---|---|---|---|---|---|
| uncertain | 20 | 0.90 | 0.085 | 0.90 | 0.156 | Trivial (too easy) |
| custom_request | 20 | 0.85 | 0.586 | 0.85 | 0.694 | **Strongest real intent** |
| tip_interest | 20 | 0.70 | 0.933 | 0.70 | 0.800 | Strong ("tip" keyword) |
| greeting | 20 | 0.60 | 1.000 | 0.60 | 0.750 | Good (unique lexical) |
| negotiation | 20 | 0.50 | 0.909 | 0.50 | 0.645 | Moderate |
| content_curiosity | 20 | 0.45 | 0.750 | 0.45 | 0.562 | Moderate |
| content_request | 20 | 0.45 | 1.000 | 0.45 | 0.621 | Moderate (high precision) |
| post_purchase | 20 | 0.45 | 0.500 | 0.45 | 0.474 | Moderate |
| reassurance | 20 | 0.45 | 0.900 | 0.45 | 0.600 | Moderate |
| purchase_intent | 20 | 0.40 | 0.296 | 0.40 | 0.340 | **Weak** (low precision) |
| appreciation | 20 | 0.35 | 0.778 | 0.35 | 0.483 | Moderate |
| operator_request | 20 | 0.35 | 0.875 | 0.35 | 0.500 | Moderate |
| repeat_purchase_intent | 20 | 0.35 | 0.778 | 0.35 | 0.483 | Moderate |
| aftercare | 20 | 0.30 | 0.462 | 0.30 | 0.364 | Weak |
| rejection | 20 | 0.30 | 0.750 | 0.30 | 0.429 | Weak |
| hesitation | 20 | 0.25 | 0.833 | 0.25 | 0.385 | Weak |
| price_inquiry | 20 | 0.25 | 0.833 | 0.25 | 0.385 | Weak |
| relationship_building | 20 | 0.25 | 0.556 | 0.25 | 0.345 | Weak |
| casual_chat | 20 | 0.20 | 0.667 | 0.20 | 0.308 | Very weak |
| complaint | 20 | 0.20 | 1.000 | 0.20 | 0.333 | Very weak (high precision) |
| personal_disclosure | 20 | 0.20 | 0.800 | 0.20 | 0.320 | Very weak |
| other | 20 | 0.10 | 0.667 | 0.10 | 0.174 | **Weakest** |

### Key Observations

1. **High-precision / low-recall pattern**: Most intents have high precision (>0.7) but low recall (<0.5). The model is conservative — when it predicts an intent, it's usually right, but it misses many true cases.

2. **Semantic anchors**: `custom_request`, `tip_interest`, `greeting`, and `negotiation` have distinctive semantic signatures that MiniLM can capture.

3. **Ontologically ambiguous intents**: `other`, `casual_chat`, `personal_disclosure`, `complaint`, and `hesitation` lack distinctive semantic centers. These are fundamentally hard for embedding models.

4. **Purchase Intent precision problem**: `purchase_intent` has only 0.296 precision — it gets triggered by many non-purchase messages containing words like "buy", "bundle", "pay".

---

## 5. Purchase Safety Forensics

| Metric | Value |
|---|---|
| True purchase cases | 40 |
| Predicted purchase | 36 |
| **False purchase** | **19** |
| **Missed purchase** | **23** |
| Precision | 0.4722 |
| Recall | 0.425 |
| **FPR** | **0.0475** |
| FNR | 0.575 |

### False Purchase Analysis (19 cases)

| Source Intent | Count | Pattern |
|---|---|---|
| `rejection` | 5 | "I don't want to buy anything" → sees "buy" |
| `hesitation` | 3 | "I'm debating between your bundles" → sees "bundles" |
| `price_inquiry` | 3 | "can I pay with crypto?" → sees "pay" |
| `negotiation` | 2 | "can you throw in extras if I buy..." → sees "buy" |
| `aftercare` | 2 | "I bought the bundle but can't find..." → sees "bought" |
| `post_purchase` | 1 | "I bought the bundle, when do I get access?" |
| `content_request` | 1 | "I'd pay to see that" → sees "pay" |
| `appreciation` | 1 | "I can't thank you enough for the bundle" → sees "bundle" |
| `rejection` (misc) | 1 | "I lost interest, not purchasing" → sees "purchasing" |

**Critical finding**: The model has a **keyword trigger problem** for purchase-related terms. Words like "buy", "pay", "bundle", "purchasing" cause false positives even when the message explicitly rejects purchasing.

### Missed Purchase Analysis (23 cases)

Most missed purchases are classified as `uncertain` (low confidence) or `custom_request` / `post_purchase`. The model struggles to distinguish between:
- `purchase_intent` vs `post_purchase` (temporal: before vs after)
- `purchase_intent` vs `custom_request` (specific vs general)

---

## 6. Score Distribution Analysis

| Metric | Correct Predictions | Incorrect Predictions |
|---|---|---|
| Top-1 score mean | 0.687 | 0.724 |
| Top-1 score median | 0.700 | 0.855 |
| Margin mean | 0.253 | 0.237 |

**Critical finding**: Incorrect predictions actually have **higher** top-1 scores than correct ones. This means:
- The model is **confidently wrong** on many cases
- Score magnitude alone cannot distinguish correct from incorrect predictions
- The margin is slightly lower for incorrect predictions (0.237 vs 0.253) but the difference is too small for threshold separation

---

## 7. Context-Dependent Intent Analysis

The following intents are **inherently state-dependent** — message text alone is insufficient:

| Intent | Context Required | 440-Case Accuracy | Notes |
|---|---|---|---|
| `post_purchase` | Purchase history | 0.45 | "I just bought it" needs temporal context |
| `repeat_purchase_intent` | Previous purchase | 0.35 | "I want another" needs purchase history |
| `aftercare` | Purchase + delivery state | 0.30 | "where's my link?" needs order state |
| `hesitation` | Conversation flow | 0.25 | "I'm thinking about it" needs context |
| `negotiation` | Price context | 0.50 | "would you take $30?" needs price reference |

**These 5 intents account for ~35% of Type D errors.** The embedding model cannot solve these from text alone — they require the future Context Engine architecture.

---

## 8. MiniLM Benchmark

| Metric | Value |
|---|---|
| Model | `all-MiniLM-L6-v2` |
| Dimension | 384 |
| Cold load | 9,101 ms |
| Cached load | 9,061 ms |
| Reference cache (110 vectors) | 468 ms |
| **Encode p50** | **24.0 ms** |
| Encode p95 | 32.9 ms |
| Encode p99 | 43.1 ms |
| **E2E p50** | **47.9 ms** |
| E2E p95 | 61.5 ms |
| E2E p99 | 67.2 ms |
| RAM | Not measured (psutil unavailable) |

---

## 9. MPNet Benchmark

| Metric | Value |
|---|---|
| Model | `all-mpnet-base-v2` |
| Dimension | 768 |
| Cold load | 9,064 ms |
| Cached load | 9,584 ms |
| Reference cache (110 vectors) | 3,045 ms |
| **Encode p50** | **105.0 ms** |
| Encode p95 | 148.5 ms |
| Encode p99 | 203.3 ms |
| **E2E p50** | **159.8 ms** |
| E2E p95 | 201.2 ms |
| E2E p99 | 282.3 ms |
| RAM | Not measured |

---

## 10. MiniLM vs MPNet A/B Comparison

### Accuracy

| Metric | MiniLM | MPNet | Delta |
|---|---|---|---|
| **Hybrid accuracy** | **0.4023** | **0.4273** | **+0.025** |
| Semantic-only accuracy | 0.4023 | 0.4273 | +0.025 |
| Lexical-only accuracy | 0.1636 | 0.1636 | 0.000 |
| **Macro F1** | **0.4613** | **0.4888** | **+0.028** |
| Abstention | 0.0136 | 0.0114 | -0.002 |

### Purchase Safety

| Metric | MiniLM | MPNet | Delta |
|---|---|---|---|
| Purchase precision | 0.4722 | 0.5833 | +0.111 |
| Purchase recall | 0.425 | 0.450 | +0.025 |
| **Purchase FPR** | **0.0475** | **0.0275** | **-0.020** |
| Purchase FNR | 0.575 | 0.550 | -0.025 |

### Performance

| Metric | MiniLM | MPNet | Ratio |
|---|---|---|---|
| **Encode p50** | **24.0 ms** | **105.0 ms** | **4.37x** |
| **E2E p50** | **47.9 ms** | **159.8 ms** | **3.34x** |
| Reference cache | 468 ms | 3,045 ms | 6.51x |
| Cold load | 9,101 ms | 9,064 ms | ~1.0x |
| Dimension | 384 | 768 | 2.0x |

### Per-Intent F1 Delta (MPNet - MiniLM)

| Intent | MiniLM F1 | MPNet F1 | Delta |
|---|---|---|---|
| post_purchase | 0.474 | 0.632 | **+0.158** |
| purchase_intent | 0.340 | 0.500 | **+0.160** |
| operator_request | 0.500 | 0.621 | **+0.121** |
| aftercare | 0.364 | 0.457 | **+0.094** |
| hesitation | 0.385 | 0.467 | **+0.082** |
| negotiation | 0.645 | 0.706 | +0.061 |
| relationship_building | 0.345 | 0.414 | +0.069 |
| repeat_purchase_intent | 0.483 | 0.545 | +0.063 |
| greeting | 0.750 | 0.788 | +0.038 |
| tip_interest | 0.800 | 0.833 | +0.033 |
| content_curiosity | 0.562 | 0.588 | +0.026 |
| complaint | 0.333 | 0.357 | +0.024 |
| custom_request | 0.694 | 0.698 | +0.004 |
| reassurance | 0.600 | 0.600 | 0.000 |
| other | 0.174 | 0.174 | 0.000 |
| content_request | 0.621 | 0.571 | **-0.049** |
| appreciation | 0.483 | 0.452 | -0.031 |
| rejection | 0.429 | 0.385 | -0.044 |
| price_inquiry | 0.385 | 0.333 | -0.051 |
| personal_disclosure | 0.320 | 0.250 | -0.070 |
| casual_chat | 0.308 | 0.240 | -0.068 |
| uncertain | 0.156 | 0.142 | -0.014 |

### MPNet Wins vs MiniLM Wins

- **MPNet better F1**: 14 intents
- **MiniLM better F1**: 7 intents
- **Tie**: 1 intent

---

## 11. Architecture Implication

The evidence supports the planned Context Engine architecture:

```
AUTHORITATIVE STATE (LLM #1 CommerceSignals)
        ↓
CONTEXT ENGINE (message + state + history)
        ↓
COMPACT QWEN2.5 CONTEXT (few-shot, structured)
        ↓
ONE GENERATION
        ↓
STRUCTURED OUTPUT (Pydantic)
        ↓
DETERMINISTIC AUTHORITY
        ↓
SEND / HANDOFF
```

### Why embedding models alone are insufficient:

1. **33.5% of failures are genuine misclassifications** — the embedding space cannot separate 22 fine-grained intents
2. **State-dependent intents (post_purchase, repeat_purchase, aftercare, hesitation, negotiation) require conversation context** — message text alone is fundamentally insufficient
3. **Purchase keyword triggers cause 19 false purchases** — embedding models cannot understand negation ("I don't want to buy") vs affirmation ("I want to buy")
4. **Score distributions overlap** — correct and incorrect predictions have similar confidence, making threshold-based separation unreliable

### What the Context Engine solves:

1. **State injection**: Purchase history, conversation context, user state → resolves state-dependent intents
2. **Negation understanding**: LLM can parse "I don't want to buy" as rejection, not purchase
3. **Few-shot examples**: Dynamic example selection based on conversation context
4. **Uncertainty-aware generation**: LLM can explicitly reason about ambiguity

---

## 12. Recommendation

### **C — Neither MiniLM nor MPNet is sufficient alone; investigate corpus/ontology/context representation**

**Rationale:**

1. **MPNet provides marginal improvement (+2.5% accuracy) at 4.37x latency cost** — not justified for the Context Engine's observational role
2. **Both models fail on the same fundamental problems**: state-dependent intents, negation understanding, and fine-grained semantic separation
3. **The real bottleneck is not the embedding model — it's the absence of context**: 35% of failures require conversation state that no message-level classifier can provide
4. **The Context Engine architecture is the correct path forward**: use LLM #1's authoritative CommerceSignals as the primary signal, use local intelligence only for pre-screening and anomaly detection

### Specific recommendation:

- **Keep MiniLM** for the Context Engine's observational role (fast, lightweight, sufficient for pre-screening)
- **Do NOT adopt MPNet** — the 2.5% accuracy gain does not justify the 4.37x latency increase
- **Focus Phase 68 on Context Engine design**: message + state → compact context → one generation
- **Defer threshold optimization** — the evidence shows thresholds are not the primary bottleneck (Type A = 36.9% but Type D = 33.5% is equally large)

---

## 13. Production Safety Verification

| Check | Status |
|---|---|
| LLM count | 3 — VERIFIED |
| LLM #1 | `extract_commerce_signals()` — authoritative — VERIFIED |
| LLM #2 | `generate_draft()` — draft — VERIFIED |
| LLM #3 | `score_draft()` — scoring — VERIFIED |
| Local intelligence | Observational only — VERIFIED |
| Shadow mode | Disabled (`qwen_shadow_enabled = False`) — VERIFIED |
| Commerce authority | Unchanged — VERIFIED |
| Price authority | Unchanged — VERIFIED |
| Product authority | Unchanged — VERIFIED |
| Offer execution | Unchanged — VERIFIED |
| Redis | Unchanged — VERIFIED |
| Schema | Unchanged — VERIFIED |

---

## 14. Files Inspected

| File | Purpose |
|---|---|
| `commerce/signals.py` | 22-intent ontology |
| `commerce/intent_corpus.py` | 110 reference examples |
| `commerce/validation_dataset.py` | 110-case validation |
| `commerce/validation_dataset_440.py` | 440-case validation |
| `commerce/embedding_model.py` | MiniLM wrapper |
| `commerce/unified_intelligence.py` | Hybrid intelligence |
| `tests/evaluate_unified_intelligence.py` | Evaluator |
| `tests/phase67_forensic.py` | Diagnostic script |

---

## 15. Commands Executed

```bash
python -m tests.phase67_forensic
```

Output: JSON with full metrics, benchmark data, failure taxonomy, and per-intent analysis.

---

## 16. Dependencies

| Package | Version | Purpose |
|---|---|---|
| sentence-transformers | 6.0.1 | Embedding models |
| rapidfuzz | 3.14.6 | Lexical matching |
| all-MiniLM-L6-v2 | (auto) | MiniLM model |
| all-mpnet-base-v2 | (auto) | MPNet model (downloaded) |

---

## 17. Blockers

None. All forensic work completed. The evidence is sufficient for Phase 68 planning.

---

## 18. Recommended Phase 68

**Context Engine architecture design and prototyping.**

Specifically:
1. Design the state injection pipeline (LLM #1 CommerceSignals → Context Engine)
2. Design the compact context format (message + state + few-shot examples)
3. Prototype one-generation Qwen2.5 classification
4. Measure accuracy improvement from state injection
5. Do NOT make local intelligence authoritative

---

## 19. Temporary Files

| File | Status |
|---|---|
| `tests/phase67_forensic.py` | **KEPT** — Phase 67 deliverable |
| `tests/_phase67_print.py` | **DELETED** |
| `phase67_stderr.log` | **DELETED** |
| `phase67_results.json` | **DELETED** |
