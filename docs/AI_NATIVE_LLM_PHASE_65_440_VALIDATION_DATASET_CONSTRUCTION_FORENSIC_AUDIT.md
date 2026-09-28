# AI_NATIVE_LLM_PHASE_65_440_VALIDATION_DATASET_CONSTRUCTION_FORENSIC_AUDIT
**READ-ONLY Forensic Audit — 440-Case Dataset Construction Readiness**
**Date: 2026-09-01 | Phase: 65 | READ-ONLY, NO MODIFICATIONS**

---

## 1. Scope

Full read-only audit to determine exact requirements for constructing the 440-case independent validation dataset. Verifies all Phase 63 specifications against current post-Phase 64 source state.

---

## 2. Read-Only Compliance

No files modified. Temporary diagnostic script created and deleted. Repository clean.

---

## 3. Authoritative Ontology

**Source:** `commerce/signals.py:57-80`

**22 intents, verified:**

```
INTENT_CATEGORIES = frozenset({
    "casual_chat", "greeting", "relationship_building", "personal_disclosure",
    "content_curiosity", "content_request", "price_inquiry", "purchase_intent",
    "repeat_purchase_intent", "post_purchase", "aftercare", "tip_interest",
    "complaint", "custom_request", "negotiation", "hesitation", "rejection",
    "uncertain", "reassurance", "appreciation", "operator_request", "other",
})
```

| Check | Result |
|---|---|
| Intent count | 22 — VERIFIED |
| `other` valid | YES — intentional catch-all |
| Any intent in `CommerceSignals` not in corpus | NO — all 22 in `INTENT_CATEGORIES` match corpus |
| Any corpus intent invalid | NO — all 22 present in `INTENT_CATEGORIES` |
| Ordering matters | NO — `frozenset`, unordered |
| Consumers assume 21 | NO — Phase 64 corrected all stale references |

**Authoritative set: exactly 22 intents.**

---

## 4. Reference Corpus Audit (Post-Phase 64)

**Source:** `commerce/intent_corpus.py`

| Attribute | Value | Verified |
|---|---|---|
| Total examples | 110 | YES — code + execution |
| Intents | 22 | YES — Counter check |
| Per intent | 5 | YES — all 22 intents have exactly 5 |
| Duplicates | 0 | YES — exact + normalized check |
| Structural validity | PASS | YES — `validate_corpus()` returns `True, "ok"` |
| Phase 64 correction applied | YES | `validate_corpus()` now expects 110, not 105 |

**Stale reference search results:**

| Pattern | Found | Location | Status |
|---|---|---|---|
| `105` in `commerce/*.py` | 0 | None | CLEAN |
| `21 intents` | 0 | None | CLEAN |
| `cosine vs 105` | 0 | None | CLEAN |
| `expected 105` | 0 | None | CLEAN |
| `105` (comment in test) | 1 | `test_phase48_local_intelligence.py:13` | WORKAROUND COMMENT (documents the fix, not a bug) |

**One remaining stale reference:** `tests/test_phase48_local_intelligence.py:13` — comment reads "Allow 110 (22*5) not 105 (21*5) — either is ok, just check 5 per intent". This is a **workaround comment** documenting the historical issue, not a functional bug. The test itself correctly passes with 110.

**Phase 64 correction is correct. No remaining stale 105/21 references in production code.**

---

## 5. Existing Validation Audit

**Source:** `commerce/validation_dataset.py`

| Attribute | Value | Verified |
|---|---|---|
| Total examples | 110 | YES — code + execution |
| Intents | 22 | YES |
| Per intent | 5 | YES — all 22 have exactly 5 |
| `validate()` | `True, "ok"` | YES |
| Exact overlap with reference | 0 | YES |
| Normalized overlap | 0 | YES |
| Internal duplicates | 0 | YES |

### Near-Duplicate Contamination (>0.80 similarity)

| Similarity | Reference | Validation | Intent |
|---|---|---|---|
| 0.95 | "not sure what you mean" | "not sure what u mean" | uncertain |
| 0.93 | "I don't want it" | "don't want it" | rejection |
| 0.88 | "not interested" | "no, not interested" | rejection |
| 0.86 | "hey" | "heya" | greeting |
| 0.86 | "hmm" | "hmmm" | uncertain |

**5 near-duplicate pairs at >0.80 similarity. 4.5% contamination rate.** These are trivial paraphrases (contraction removal, typo, abbreviation). The 440-case dataset must achieve 0 near-duplicates at >0.80.

---

## 6. 440 Dataset Existence Check

**440 validation cases actually exist: NO**

| Search | Result |
|---|---|
| `validation_dataset_440.py` | DOES NOT EXIST |
| 440-case JSON/CSV/JSONL | DOES NOT EXIST |
| Fixture with 440 labeled cases | DOES NOT EXIST |
| Generated validation data | DOES NOT EXIST |
| Any file named `*440*` | Only docs referencing the plan |

### Repository Search Results

| Pattern | Matches | Nature |
|---|---|---|
| `*440*` (filenames) | 3 docs only | Phase 60/61/62 reports (planning documents) |
| `22 × 20` | docs only | Planning specifications |
| `20 per intent` | docs only | Planning specifications |

**No 440-case dataset exists anywhere in the repository. All references are to planning documents.**

---

## 7. Data Structure Audit

### Existing Structures

| Structure | File | Fields | Reusable? |
|---|---|---|---|
| `IntentExample` | `intent_corpus.py` | `intent, example_text, example_id, hard_negative_group, source, notes` | Partially — reference-specific fields (`source`, `hard_negative_group`) |
| `ValidationExample` | `validation_dataset.py` | `text, intent, notes` | Too minimal — missing `example_id`, `hard_negative_for`, `context`, `length_category`, `style` |

### Recommended Approach: Option C — Extend `ValidationExample`

**Safest option:** Create a new `ValidationExample440` TypedDict in the new file, extending the existing `ValidationExample` pattern with the additional fields specified by Phase 63.

```python
class ValidationExample440(TypedDict):
    text: str
    intent: str
    notes: str
    example_id: str              # {intent}_{NN}
    hard_negative_for: str | None # competing intent this tests
    context: dict | None          # state for state-dependent intents
    length_category: str          # "short" | "medium" | "long"
    style: str                    # "question" | "statement" | "exclamation"
```

**Why not Option A (reuse `IntentExample`):** `IntentExample` has `source` and `hard_negative_group` which are reference-corpus-specific. The validation dataset has different semantics.

**Why not Option B (minimal `ValidationExample`):** The existing 3-field structure lacks `example_id`, `hard_negative_for`, `context`, `length_category`, `style` — all required by the Phase 63 specification.

---

## 8. 22×20 Distribution Audit

### Mathematical Verification

| Component | Count | Total |
|---|---|---|
| 22 intents × 20 cases | 22 × 20 | **440** |
| Short (4-6 per intent) | 22 × 5 avg | ~110 |
| Medium (8-10 per intent) | 22 × 9 avg | ~198 |
| Long (4-6 per intent) | 22 × 6 avg | ~132 |
| **Total** | | **440** |

### Per-Intent Composition (Phase 63 ranges)

| Category | Range | Avg | Can produce exactly 20? |
|---|---|---|---|
| Short (1-3 words) | 4-6 | 5 | YES — e.g., 5 short + 9 medium + 6 long = 20 |
| Medium (4-10 words) | 8-10 | 9 | YES |
| Long (11+ words) | 4-6 | 6 | YES |
| Questions | 6-8 | 7 | YES |
| Statements | 10-12 | 11 | YES |
| Exclamations | 2-4 | 3 | YES |
| Hard negatives | 2-3 | 2.5 | YES |

**Sample valid composition per intent:**
- 5 short + 9 medium + 6 long = 20 ✓
- 7 questions + 11 statements + 2 exclamations = 20 ✓
- 2.5 hard negatives avg (some intents 2, some 3) ✓

**No mathematical conflict. The ranges can produce exactly 20 cases per intent.**

---

## 9. Hard-Negative Audit

### Required Coverage

| Pair | Direction | Required | Per Intent |
|---|---|---|---|
| `custom_request` ↔ `content_request` | Both | 5 each | 5 hard negatives for `custom_request`, 5 for `content_request` |
| `purchase_intent` ↔ `content_curiosity` | Both | 3 each | 3 hard negatives for each |
| `other` ↔ `greeting` | Both | 3 each | 3 hard negatives for each |
| `other` ↔ `casual_chat` | Both | 3 each | 3 hard negatives for each |
| `personal_disclosure` ↔ `casual_chat` | Both | 3 each | 3 hard negatives for each |
| `hesitation` ↔ `rejection` | Both | 2 each | 2 hard negatives for each |
| Medium-risk pairs (8 pairs) | Both | 1-2 each | ~16 total |

### Hard-Negative Budget

| Intent | Hard negatives needed | Out of 20 |
|---|---|---|
| `custom_request` | 5 (vs `content_request`) | 5/20 = 25% |
| `content_request` | 5 (vs `custom_request`) + 2 (vs `content_curiosity`) | 7/20 = 35% |
| `purchase_intent` | 3 (vs `content_curiosity`) | 3/20 = 15% |
| `content_curiosity` | 3 (vs `purchase_intent`) + 2 (vs `content_request`) | 5/20 = 25% |
| `other` | 3 (vs `greeting`) + 3 (vs `casual_chat`) | 6/20 = 30% |
| `personal_disclosure` | 3 (vs `casual_chat`) | 3/20 = 15% |
| All others | 1-2 each | 1-2/20 = 5-10% |

### Conflict Check

**No conflict.** All hard-negative requirements fit within the 20-per-intent budget. The highest allocation is `content_request` at 35% (7/20), which leaves 13 non-hard-negative cases — sufficient for natural linguistic variation.

---

## 10. Context/State Audit

### State-Dependent Intents

| Intent | Required Context | Runtime Field | Synthetic or Real? |
|---|---|---|---|
| `repeat_purchase_intent` | `prior_purchase_count >= 1` | `fangate_transactions.total_purchases` | **Synthetic** — evaluator metadata only |
| `post_purchase` | `recent_transaction = True` | `fangate_transactions` | **Synthetic** — evaluator metadata only |
| `aftercare` | `pending_delivery = True` | `aftercare_status` | **Synthetic** — evaluator metadata only |
| `hesitation` | `recent_offer_count >= 1` | `messages_since_last_offer` | **Synthetic** — evaluator metadata only |
| `negotiation` | `active_offer_price = numeric` | `CommerceDecisionContext` | **Synthetic** — evaluator metadata only |

### Critical Distinction

These context fields are **synthetic evaluator metadata** for offline testing only. They are NOT:
- Production state
- Runtime database queries
- Real transaction records

The local classifier currently receives **current message only** (no context). The context fields in the 440 dataset are for **future evaluator enhancement** — they document what context WOULD be needed for reliable classification, but the current evaluator does not use them.

### Current Evaluator Impact

The current evaluator (`evaluate_unified_intelligence.py`) processes `ex["text"]` only. Context fields in the 440 dataset will be **ignored by the current evaluator** but preserved for future use when the evaluator is enhanced to support context-aware classification.

---

## 11. Independence/Contamination Audit

### Methodology (Available Without Installation)

| Check | Library | Available |
|---|---|---|
| Exact duplicate | Python `set` | YES |
| Normalized duplicate | Python `re` | YES |
| Lexical similarity | `difflib.SequenceMatcher` | YES |
| Semantic similarity | `sentence_transformers` (installed) | YES |

### Contamination Protocol for 440 Dataset

1. **Exact match** against `INTENT_CORPUS` → REJECT
2. **Normalized match** (alphanumeric only) → REJECT
3. **SequenceMatcher >0.80** against `INTENT_CORPUS` → REJECT
4. **SequenceMatcher 0.70-0.80** against `INTENT_CORPUS` → REVIEW
5. **Embedding cosine >0.90** against `INTENT_CORPUS` → REVIEW
6. **Internal duplicates** within 440 → REJECT
7. **Internal near-duplicates >0.80** within 440 → REJECT

### Target

| Metric | Current (110) | Target (440) |
|---|---|---|
| Exact duplicates | 0 | 0 |
| Near-duplicates (>0.80) | 5 (4.5%) | **0 (0%)** |
| Semantic similarity (>0.90) | Unknown | **0** |

---

## 12. Evaluator Compatibility

**Source:** `tests/evaluate_unified_intelligence.py`

### Current Capabilities

| Feature | Status |
|---|---|
| Arbitrary dataset size | NO — hardcoded import `VALIDATION_DATASET` |
| 440 cases | NO — would require import change |
| 22 intents | YES — derived from data |
| Context metadata | NO — only processes `text` field |
| Per-intent accuracy | YES |
| Macro accuracy | YES |
| Weighted accuracy | NO |
| Per-intent precision/recall/F1 | NO — only accuracy |
| Purchase precision/recall/FPR/FNR | YES |
| Abstention | YES |
| Confusion matrix | YES (printed, not returned) |
| Top-1/top-2 score | NO |
| Margin | NO |
| Threshold reporting | NO |
| Model selection | NO |
| Threshold parameterization | NO |

### Minimum Changes Required for 440

**Option 1 (minimal):** Create `commerce/validation_dataset_440.py` with `VALIDATION_440` list, modify evaluator import from `VALIDATION_DATASET` to `VALIDATION_440`. **1 line change.**

**Option 2 (parameterized):** Add `dataset` parameter to `evaluate()` function. **~5 line change.**

**Option 2 is recommended** for future model A/B and threshold sweep compatibility.

### Does Evaluator Need Context Support?

**Not yet.** The current evaluator processes `text` only. Context fields in the 440 dataset are for future use. The evaluator will correctly process all 440 cases using `text` + `intent` fields, ignoring `context` metadata.

---

## 13. Model A/B Readiness

| Attribute | Model A | Model B |
|---|---|---|
| Model | `all-MiniLM-L6-v2` | `all-mpnet-base-v2` |
| Dimension | 384 | 768 |
| Installed | YES | NO |
| Embedding abstraction | `embedding_model.py` — model-agnostic | Parameterizable |
| Evaluator support | Current | Requires `_MODEL_NAME` parameterization |

### Isolation Assessment

The evaluator uses `analyze_message()` which calls `encode_message()` from `embedding_model.py`. The model name is hardcoded at `embedding_model.py:19`. To support A/B:

1. Parameterize `_MODEL_NAME` in `embedding_model.py` (env var or function arg)
2. Pass model choice to evaluator
3. Reference corpus must be re-encoded for each model (different dimensions)

**Current state: NOT READY for A/B without evaluator/model changes.**

---

## 14. Cross-Encoder Readiness

| Attribute | Status |
|---|---|
| Architecture compatible | YES — top-K reranking after bi-encoder |
| Production authority affected | NO — purely offline evaluation |
| Evaluator compatible | YES — can add rerank step |
| Blocked by evaluator design | NO |
| Recommended at this stage | NO — premature until 440 baseline established |

---

## 15. Threshold Status

| Threshold | Current Value | Changed Since Phase 57? |
|---|---|---|
| Semantic threshold | 0.65 | NO — unchanged |
| Margin threshold | 0.10 | NO — unchanged |
| RapidFuzz cutoff | 80 | NO — unchanged |
| Best candidate (offline) | 0.50 / 0.00 / 80 | MEASURED on 110 cases |

**All thresholds unchanged. 440 dataset must be evaluated before threshold decisions.**

---

## 16. Production Safety Boundary

| Check | Status | Evidence |
|---|---|---|
| `extract_commerce_signals()` = LLM #1 | VERIFIED | `commerce/deepseek.py:170` |
| LLM #1 authoritative | VERIFIED | Production path unchanged |
| LLM #2 draft generation | VERIFIED | `llm_worker.py:83` |
| LLM #3 scoring | VERIFIED | `core/scoring.py` |
| Local intelligence observational | VERIFIED | `unified_intelligence.py` never calls LLM |
| `CommerceSignals` authority | VERIFIED | `signals.py` unchanged |
| Product authority | VERIFIED | Local intelligence has no product_id |
| Price authority | VERIFIED | Local intelligence has no price field |
| Offer creation authority | VERIFIED | Local intelligence never creates offers |
| Fangate authority | VERIFIED | No local intelligence involvement |
| Shadow mode disabled | VERIFIED | `core/config.py:99`: `qwen_shadow_enabled: bool = False` |

---

## 17. Exact Blockers

| Blocker | Severity | Resolution |
|---|---|---|
| No 440 dataset exists | **BLOCKER** | Construct in Phase 66 |
| Evaluator hardcoded import | **MINOR** | 1-line import change or parameterize |
| Evaluator missing metrics | **MINOR** | Add per-intent F1, Type-A/B/C, weighted accuracy |
| Model A/B not parameterized | **DEFERRED** | Not needed until Phase 67 |
| Near-duplicate contamination | **KNOWN** | 440 dataset must achieve 0% at >0.80 |

---

## 18. Phase 66 Implementation Specification

### File to Create

`commerce/validation_dataset_440.py`

### Data Structure

```python
class ValidationExample440(TypedDict):
    text: str                    # current message
    intent: str                  # expected primary intent (exactly one)
    notes: str                   # explanation of intent
    example_id: str              # unique ID: {intent}_{NN}
    hard_negative_for: str | None # competing intent this tests against
    context: dict | None          # state for state-dependent intents
    length_category: str          # "short" | "medium" | "long"
    style: str                    # "question" | "statement" | "exclamation"
```

### Distribution

```
22 intents × 20 cases = 440 total
```

### Hard-Negative Distribution (total ~55-60 of 440)

| Intent | Hard Negatives | From Competing Intent |
|---|---|---|
| `custom_request` | 5 | `content_request` |
| `content_request` | 7 | `custom_request` (5) + `content_curiosity` (2) |
| `purchase_intent` | 3 | `content_curiosity` |
| `content_curiosity` | 5 | `purchase_intent` (3) + `content_request` (2) |
| `other` | 6 | `greeting` (3) + `casual_chat` (3) |
| `personal_disclosure` | 3 | `casual_chat` |
| `hesitation` | 2 | `rejection` |
| `rejection` | 2 | `hesitation` |
| `tip_interest` | 2 | `purchase_intent` |
| `reassurance` | 2 | `price_inquiry` |
| `negotiation` | 2 | `price_inquiry` |
| `repeat_purchase_intent` | 2 | `purchase_intent` |
| `post_purchase` | 2 | `purchase_intent` |
| All others | 1-2 | Nearest competing intent |

### Context Metadata (for state-dependent intents)

```python
# repeat_purchase_intent
{"prior_purchase_count": 1}  # or higher

# post_purchase
{"recent_transaction": True}

# aftercare
{"pending_delivery": True}

# hesitation
{"recent_offer_count": 1}  # or higher

# negotiation
{"active_offer_price": 20.0}  # numeric
```

### Independence Requirements

1. Zero exact duplicates against `INTENT_CORPUS`
2. Zero normalized duplicates against `INTENT_CORPUS`
3. Zero near-duplicates (>0.80) against `INTENT_CORPUS`
4. Zero internal duplicates within 440
5. Zero internal near-duplicates (>0.80) within 440

### Validation Helper

```python
def validate_440() -> tuple[bool, str]:
    # Check count = 440
    # Check 20 per intent
    # Check no duplicates
    # Check no overlap with INTENT_CORPUS
    # Check no near-duplicates with INTENT_CORPUS
    ...
```

### Tests to Add

| Test | Purpose |
|---|---|
| `test_dataset_440_count` | Verify 440 entries |
| `test_dataset_440_per_intent` | Verify 20 per intent |
| `test_dataset_440_no_duplicates` | Verify no internal duplicates |
| `test_dataset_440_independent_from_corpus` | Verify no overlap with reference |
| `test_dataset_440_no_near_duplicates` | Verify no near-duplicates with reference |
| `test_dataset_440_hard_negatives_present` | Verify hard-negative coverage |
| `test_dataset_440_all_intents_present` | Verify all 22 intents |

### What Must Remain Untouched

- `commerce/intent_corpus.py` — FROZEN
- `commerce/validation_dataset.py` — existing 110 unchanged
- `commerce/unified_intelligence.py` — unchanged
- `commerce/embedding_model.py` — unchanged
- `commerce/signals.py` — unchanged
- `commerce/decision.py` — unchanged
- All thresholds — unchanged
- `all-MiniLM-L6-v2` — unchanged
- 3-LLM pipeline — unchanged
- Shadow mode — disabled

---

## 19. Final Verdict

| Metric | Value |
|---|---|
| Reference count | 110 |
| Current validation count | 110 |
| Required validation count | 440 |
| Additional cases required | **330** |
| Contamination count | 5 near-duplicates (4.5%) |
| 440 validation exists | **NO** |
| Dependencies installed | **NO** |
| Model downloaded | **NO** |
| Production behavior changed | **NO** |
| 3-LLM baseline intact | **YES** |
| Ready to construct 440 dataset | **YES** |

---

**Report path:** `docs/AI_NATIVE_LLM_PHASE_65_440_VALIDATION_DATASET_CONSTRUCTION_FORENSIC_AUDIT.md`
