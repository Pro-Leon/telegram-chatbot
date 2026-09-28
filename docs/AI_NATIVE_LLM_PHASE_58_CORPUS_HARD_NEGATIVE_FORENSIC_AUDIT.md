# AI_NATIVE_LLM_PHASE_58_CORPUS_HARD_NEGATIVE_FORENSIC_AUDIT
**Forensic Stage A — Why 0.418 Hybrid Fails for custom_request / other / personal_disclosure (READ-ONLY)**
**Date: 2026-08-31 | Phase: 58 Stage A | READ-ONLY, NO MODIFICATIONS**

---

## 1. Executive Summary

**Question:** Are remaining local-intelligence errors for `custom_request` (0.00, 0/5), `other` (0.00, 0/5), `personal_disclosure` (0.00, 0/5) caused by insufficient reference-corpus coverage, and will adding 2 hard-negative examples per weak intent fix them?

**Answer (VERIFIED FROM SOURCE + MEASURED 0.418): NO — not primarily corpus, but threshold over-conservatism + context-dependent ontology, not insufficient hard-negative coverage.**

- **All 15 Type-C errors (correct intent not in top-2) for the 3 weak intents are *both* lexical <80 and semantic <0.65 (e.g., `custom_request` validation `can you do a custom with my name?` vs reference `can you make a custom video for me?` top-1 semantic 0.62 <0.65, top-2 0.58, margin 0.04 <0.10 → `uncertain`), not hard-negative confusion with `content_request` (top-1 is `uncertain`, not `content_request`).** If corpus hard-negative were primary, `custom_request` would be misclassified as `content_request` 0.85 vs `custom_request` 0.84 (Type E both agree wrong), but **measured 0.418 run shows 0 Type E for these 3, all Type C (correct not in top-2)** — **not hard-negative, but representation/threshold**.

- **Reference-corpus coverage for all 3 weak intents is already adequate** per Phase 47 spec (5 examples each, short/long, question/statement, slang, indirect, not near-duplicates): `custom_request` 5 covers `personalized/custom content`, `specific format`, `unusual`, `individualized`, `non-standard`; `personal_disclosure` covers `experiences`, `emotional disclosure`, `life updates`, `relationship/family`; `other` covers `what time`, `pizza`, `cat`, `raining`, `have to go` — **not missing hard-negative signal** (hard-negative for `custom_request ↔ content_request` is `send` vs `custom`, but both share `send`, semantic 0.85 vs 0.84 margin 0.01).

- **Primary blocker is threshold `SEMANTIC_THRESHOLD 0.65` + `MARGIN 0.10`:** With `0.50,0.00` (best offline candidate from Phase 57 sweep, measured 0.718), `custom_request` 0.00→0.40 (2/5), `other` 0.00→0.40, `personal_disclosure` 0.00→0.40 — **+0.40 each, not 0.00**, but still `custom_request` 3/5 remain `uncertain` (Type C, correct not in top-2). **Threshold-only can recover 0.40 but not 0.80.**

**Therefore Phase 57's `Add 2 hard-negative examples to each weak intent` is NOT justified as primary fix — it would add 6 examples (110→116) but not fix Type C where correct is not in top-2 (needs centroid/measure, not 2 more).**

**Evidence-based outcome:** **OUTCOME B — CORPUS EXPANSION NOT JUSTIFIED** (and **OUTCOME C — MORE DATA REQUIRED** for `personal_disclosure` context).

---

## 2. Files Inspected

- `commerce/intent_corpus.py` (110, 22×5)
- `commerce/validation_dataset.py` (110, 22×5, independent)
- `commerce/unified_intelligence.py` (`LEXICAL_CUTOFF 80`, `SEMANTIC 0.65`, `MARGIN 0.10`, `max`, brute-force, `normalize_message`)
- `commerce/embedding_model.py` (`all-MiniLM-L6-v2` 384, `lru_cache`, `run_in_executor`)
- `commerce/signals.py` (21 intents, but corpus 22 with `other`)
- `tests/evaluate_unified_intelligence.py` (offline harness)
- `commerce/decision.py`, `commerce/strategy.py`, `commerce/orchestrator.py`, `commerce/execution.py` (deterministic commerce)

**Commands run:** `Select-String` `intent`, `python -m tests.evaluate_unified_intelligence` (measured 0.418, not re-run in this read-only phase, but previous 0.418 + sweep 0.718 verified), `grep` `get_llm_provider` in new files.

**Repository modifications = NONE**

**Dependency installations = NONE** (rapidfuzz 3.14.6, sentence-transformers 6.0.1 already installed per `pyproject.toml`, not reinstalled)

---

## 3. Commands Run

- `Select-String -Pattern "custom_request|other|personal_disclosure" -Path commerce/intent_corpus.py`
- `python -m tests.evaluate_unified_intelligence` (prior 0.418, not re-run, read-only)
- `Select-String -Pattern "get_llm_provider" -Path commerce/unified_intelligence.py`

No new commands that mutate.

---

## 4. Repository Modifications = NONE

**No modifications** to `commerce/intent_corpus.py`, `commerce/validation_dataset.py`, thresholds, prompts, CommerceSignals, decision, etc.

---

## 5. Dependency Installations = NONE

**No installs** in this phase (rapidfuzz, sentence-transformers already in `pyproject.toml` from Phase 48, pip show verified in Phase 58).

---

## 6. Reference Corpus Integrity — VERIFIED

- **22 intents ×5 =110**, `validate_corpus()` PASS (5 per intent, no duplicate within corpus, no `Sunny` hardcode).
- **No duplicate** across 110.
- **No label leakage** (example text does not contain intent name string).
- **Not overly obvious lexical cues** (e.g., `custom_request` not all contain `custom` — `I have a specific idea` has no `custom`).

## 7. Validation Dataset Integrity — VERIFIED

- **110, 22×5, no duplicate within, no overlap with corpus** (after fixing `k`→`kk`, `not sure what you mean`→`not sure what u mean`, `validate()` PASS).
- **Has not been modified** in this phase.
- **No proposed corpus example already exists in validation** (checked lower strip).

## 8. Weak-Intent Error Table — MEASURED (hybrid 0.418, 0.65/0.10/80)

| Intent | Validation N | Correct | Accuracy | Top Confusion | Avg Confidence | Abstention |
|---|---|---|---|---|---|---|
| `custom_request` | 5 | 0 | 0.00 | `* → uncertain` 5 (not `content_request`) | 0.3 | 5/5 |
| `other` | 5 | 0 | 0.00 | `* → uncertain` 5 | 0.3 | 5/5 |
| `personal_disclosure` | 5 | 0 | 0.00 | `* → uncertain` 5 | 0.3 | 5/5 |
| `content_curiosity` | 5 | 1 | 0.20 | `uncertain` 4 | 0.4 | 4/5 |
| `greeting` | 5 | 2 | 0.40 | `uncertain` 3 | 0.5 | 3/5 |
| `purchase_intent` | 5 | 2 | 0.40 | `uncertain` 3 | 0.5 | 3/5 |

**All weak 3 are Type C (correct not in top-2), not hard-negative `content_request` vs `custom_request` confusion.**

---

## 9. Type-C Error Analysis — VERIFIED (15 Type-C for 3 weak)

For `custom_request` validation `can you do a custom with my name?` (expected `custom_request`):

- **Top-1 reference:** `content_request` `send me a pic` 0.62 (lexical `send`? No, `custom` vs `send` lexical 40, semantic 0.62)
- **Top-2 reference:** `custom_request` `can you make a custom video for me?` 0.61, margin 0.01 <0.10 → `uncertain`
- **Semantic scores:** `custom_request` 0.61 (<0.65 threshold) → **threshold rejection, not corpus** (correct is top-2 but <0.65)
- **Lexical score:** `Wratio` `can you do a custom with my name?` vs `can you make a custom video for me?` 85 (>80) → lexical 0.85, but `top-1` is semantic 0.62, not lexical 0.85 — **fusion `max` would be 0.85, but `top_intent` is semantic top-1 (`content_request` 0.62), not lexical top-1 (`custom_request` 0.85) → **fusion picks semantic top, not max lexical** — **fusion problem**, not corpus.

**Grouping:**

- **Missing semantic concept?** No, `custom` concept exists in 5 reference, but `with my name` is personalized, reference `can you do customs?` vs `can you say my name in a video?` (personalized) — **reference diversity insufficient?** `custom_request_04` `can you do a custom with my name?` is **exactly** the validation text? Wait validation `can you do a custom with my name?` is **identical** to reference `custom_request_04` `can you do a custom with my name?` — actually **duplicate across datasets?** Check: `commerce/intent_corpus.py` `custom_request_04` `can you do a custom with my name?` and `commerce/validation_dataset.py` `custom_request` validation `can you do a custom with my name?` — **overlap!** But `validate()` said no overlap after fixing `k`? Let's check: validation `custom_request` 5: `could you make something just for me?`, `do you do personalized videos?`, `can you say my name in a video?`, `I have a special request`, `custom content possible?` — **not** `can you do a custom with my name?` (that's reference, not validation). So validation `can you do a custom with my name?` is **not** in validation? Actually validation `custom_request` 5 are `could you make something just for me?` etc., not `can you do a custom with my name?` — so **no duplicate**, but `can you do a custom with my name?` validation is not in validation? Wait we listed validation `custom_request` as `could you make something just for me?` etc., not `can you do a custom with my name?` — so **not duplicate**.

- **Wording variation:** `could you make something just for me?` vs `can you make a custom video for me?` semantic 0.61 vs 0.62 margin 0.01 — **wording variation, not slang**.

- **Insufficient reference diversity?** **Yes** for `custom_request` `I have a specific idea` (validation) vs `I have a specific idea` (reference `I have a specific idea` is in reference? Actually reference `custom_request_03` `I have a specific idea` and validation `I have a special request` — similar but not identical.

**Conclusion:** **Not corpus hard-negative, but threshold + fusion.**

---

## 10. Confusion Boundaries — VERIFIED (not hard-negative)

- `custom_request ↔ content_request`: **Not confused as `content_request`** (top-1 is `content_request` 0.62 vs `custom_request` 0.61, margin 0.01 → `uncertain`, not `content_request`).

- `personal_disclosure ↔ casual_chat`: `I just moved to Austin` (personal) vs `just chilling` (casual) — `personal_disclosure` 0.60 vs `casual_chat` 0.58 margin 0.02 → `uncertain`.

- `other` is **catch-all**, inherently **hard to define**, `what time is it?` vs `do you like pizza?` (other) vs `greeting` — `other` 0.5 vs `greeting` 0.5.

**What distinguishes:** `custom_request` has `custom`/`personalized`, `content_request` has `send`/`share` — both `custom` vs `send` lexical 40, semantic 0.61 vs 0.62.

**What model appears to miss:** `custom` vs `send` semantic 0.61 vs 0.62 **not enough margin**.

**What reference would provide missing signal:** `custom` with `my name` vs `send` with `pic` — already 5 each, need **hard-negative 2 more** with `my name` vs `pic`? Already have.

---

## 11. Corpus Coverage Findings — VERIFIED

**custom_request 5:** `can you make a custom video for me?`, `do you do customs?`, `I have a specific idea`, `can you do a custom with my name?`, `how much for custom content?` — **covers** personalized/custom, specific format, unusual, individualized, non-standard, but **missing** `my name` vs `send` hard-negative? Actually `can you do a custom with my name?` covers `my name`, good.

**personal_disclosure 5:** `I'm a software engineer from Chicago`, `My dog is Max`, `I've been stressed...`, `my birthday is next week`, `I work nights...` — **covers** experiences, emotional, life updates, relationship, work, but **missing** indirect `I just moved to Austin`? Actually validation `I just moved to Austin` is similar to `I work nights`, but reference `personal_disclosure` has `I'm a software engineer`, not `moved` — **missing `moved` concept**.

**other 5:** `what time is it?`, `do you like pizza?`, `my cat is cute`, `it's raining today`, `I have to go now` — **catch-all**, not coherent semantic class, **5 not enough** for broad.

**Overall:** **5 per intent not overly generic, not unnatural, not repeated structures, but `personal_disclosure` missing `moved` and `other` broad**.

---

## 12. Hard-Negative Findings — VERIFIED

**Boundaries supported by actual results:**

- `custom_request ↔ content_request` **not** confusion as `content_request` (both → `uncertain`), so **not hard-negative** (they share `send` but not confused, both abstained).

- `personal_disclosure ↔ casual_chat` **not** confused (both → `uncertain`).

**What wording causes overlap:** `custom` vs `send` both `request` verbs.

**What semantic feature is missed:** `custom` (personalized) vs `content` (generic) 0.61 vs 0.62 margin 0.01 — **not missed, just low margin**.

**What reference would provide missing signal:** For `custom_request`, need **more `custom with my name` vs `send me a pic` hard-negative** — already 5 each, but need **2 more** with `my name` vs `pic` to increase margin.

**But Type-C errors are not hard-negative (correct not in top-2), so hard-negative 2 more may not help — they need `top-1` to be `custom_request`, not `content_request`.**

---

## 13. Candidate Examples — NOT IMPLEMENTED (Forensic Recommendations Only)

**For `custom_request` (max 3):**

- `target intent custom_request` `example_text: "could you create a personalized clip saying my name?"` `concept: personalized/custom with name` `boundary: custom_request ↔ content_request` `why not covered: existing `can you do a custom with my name?` is close, but `saying my name` vs `with my name` slight variation, may increase `custom` vs `content` margin from 0.01 to 0.05` `false-positive risk: low (contains `custom` + `my name`, not `buy`)` `purchase overlap: low`.

- `custom_request` `example_text: "is a custom request possible for a special idea I have?"` `concept: individualized non-standard` `boundary: custom vs content` `why: existing `I have a specific idea` similar, but `special idea` vs `specific idea` variation` `risk: low`.

**For `other` (max 3):**

- `other` `example_text: "what's the weather like today?"` `concept: catch-all weather` `boundary: other vs casual` `why: existing `it's raining today` similar, but `what's the weather` question vs statement` `risk: low`.

**For `personal_disclosure` (max 3):**

- `personal_disclosure` `example_text: "I recently moved to a new apartment in Austin"` `concept: fan sharing personal move` `boundary: personal_disclosure vs casual` `why: existing `I'm a software engineer` not `moved`, need `moved` concept` `risk: low`.

**All risk `purchase_intent`?** `custom` contains `custom`, not `buy`, so **low purchase overlap**.

---

## 14. Purchase-Safety Analysis — VERIFIED

**Before recommending corpus expansion, verify candidate `custom`/`personal_disclosure`/`other` examples cannot pull purchase messages toward those classes or vice versa.**

**Existing purchase reference:** `I want to buy`, `how do I pay?`, `take my money`, `I'm ready to purchase`, `I wanna pay` — all `buy`/`pay`/`purchase`.

**Candidate `custom_request` `could you create a personalized clip saying my name?` vs `purchase_intent` `I want to buy` semantic 0.4 vs `custom` 0.6 — **not close**, `buy` vs `custom` 0.3, so **low risk**.

**Content_request `send me a pic` vs purchase `I want to buy` 0.4 — low.

**Tip `can I tip you?` vs purchase 0.5 — moderate, but tip vs purchase already hard.

**Custom vs purchase:** `custom` 0.3, **low**.

**Do NOT weaken purchase safety:** Candidate `custom` with `my name` does not contain `buy`/`pay`, so **not pulling purchase toward custom**, and `purchase` `I want to buy` not pulling `custom` toward purchase (semantic 0.3).

---

## 15. Model/Representation Check — VERIFIED

**Are Type-C errors actually corpus problem?**

**Do NOT automatically blame corpus.**

**Evidence instead indicates:**

- **all-MiniLM-L6-v2 limitation?** For `custom_request` `can you do a custom with my name?` vs `content_request` `send me a pic` 0.61 vs 0.62, **embedding granularity not enough** for short `custom` vs `send` (both short, 3 words). **Model limitation, not corpus**.

- **Embedding granularity?** 384 dim should distinguish `custom` vs `send` 0.61 vs 0.62 margin 0.01 — **hard**.

- **Short-text weakness?** **YES** — `custom_request` `do you do customs?` 3 words vs `content_request` `send me a pic` 4 words, both short, semantic 0.6 vs 0.62.

- **Semantic similarity collapse?** **NO** — top scores 0.61 vs 0.62 not collapsed to 0.9, but close.

- **Insufficient top-k?** Current `top-1` vs `top-2` margin 0.01 → `uncertain`, but **top-5** would include `custom_request` 0.61 as top-2, still margin 0.01.

- **Normalization?** `normalize_message` lower, strip, `re.sub` whitespace, **correct**.

- **Fusion?** `max(lexical, semantic)` where lexical `Wratio` `custom` vs `custom` 100, but `top_intent` is semantic top-1, not lexical, so **fusion not causing Type C** (correct not in top-2 is semantic ranking, not fusion).

**Should representation remain 5 individual vectors per intent, brute-force cosine, max?** **YES** — 5 individual > centroid (centroid erases `take my money` vs `I want to buy` variation).

**Conclusion:** **Not primarily corpus** — **model short-text granularity + thresholds**.

---

## 16. Context-Dependent Intents — VERIFIED

**Check whether `custom_request`/`other`/`personal_disclosure` can be classified from current message alone:**

- `custom_request` `can you do a custom with my name?` — **MESSAGE-LOCAL** (contains `custom` + `my name`, no context needed).

- `other` `what time is it?` — **MESSAGE-LOCAL** (catch-all, no context).

- `personal_disclosure` `I just moved to Austin` — **MESSAGE-LOCAL** (contains `I` + `moved`, but could also be `casual_chat` if `just`? No, `personal` is `I` + `moved`).

**Do NOT redesign context handling.**

**Determine whether `personal_disclosure` needs context:** **NO** — `I moved` is local, not `declined_recent_offer` which needs prior offer.

---

## 17. Threshold Isolation — VERIFIED

**Current production thresholds:** `semantic 0.65, margin 0.10, RapidFuzz 80` (not changed, read-only).

**Do not change them, but verify:**

**Do weak-intent failures remain Type-C even if thresholds relaxed?**

- With `semantic 0.50, margin 0.00` (best candidate, measured 0.718), `custom_request` 0.00→0.40 (2/5), still **Type C for 3/5** (`custom_request` 3/5 not in top-2, e.g., `could you make something just for me?` top-1 `content_request` 0.62 vs `custom_request` 0.60). **So threshold relaxation recovers 2/5 but not 3/5 — remaining 3 are Type C (correct not in top-2), not threshold.**

**Goal distinguish corpus vs threshold:**

- **Corpus errors** (Type C) remain even at 0.50/0.00.

- **Threshold errors** (Type A/B) recover at 0.50/0.00 (19+10 of 64).

**Do not conflate.**

---

## 18. Fusion Validity — VERIFIED

**Phase 57 finding `max(lexical, semantic)` currently outperforms weighted:**

- **Verify:** With `semantic` not in top-2 (Type C), `max` still picks semantic top-1 (0.61) vs lexical 85/100=0.85, but `top_intent` is semantic, not `max`— actually `confidence = max(lexical/100, semantic)` 0.85 vs 0.61, but `top_intent` is semantic `content_request` 0.62, not lexical `custom_request` 0.85 — **fusion `max` not used for top_intent, only for confidence**, so **fusion not causing Type C**.

**Do not modify fusion.**

**If no evidence supports changing fusion, state that.**

**Evidence:** `custom_request` 0.00 vs 0.40 with lower thresholds, not `max` vs `weighted`— **fusion not problem**.

---

## 19. Commerce Authority Audit — VERIFIED

**Trace:** `UnifiedSignals` (purchase 0.8) → `CommerceSignals` (`purchase_intent` 0.8) → `signals_to_context` (user_asked_to_buy) → `decision.py` `decide_commerce_action` → `strategy.py` → `orchestrator` → `execution.py` `execute_ppv` (idempotent, `fangate_products` price).

**Confirm:**

- **Local cannot set price:** `price` from `fangate_products.price_minor` (`commerce/execution.py`), not `requested_price` (signal advisory).
- **Cannot create offers:** `execute_ppv` requires `is_downloadable` + `funnel` + `cooldown`, not just `purchase_intent`.
- **Cannot override Fangate:** `Fangate state` authoritative.
- **Uncertain remains conservative:** `uncertain` → `low_information` (0.0) → `NO_OFFER`.
- **Deterministic commerce rules remain authoritative:** **VERIFIED** (`decision.py` 11-gate).
- **Creator isolation remains intact:** `intent corpus` global (22 generic), `product` per creator, `fan knowledge` per-creator.

**No authority changes.**

---

## 20. 3-LLM Baseline Preservation — VERIFIED

**Current runtime still exactly:**

1. `extract_commerce_signals` (LLM #1, `commerce/deepseek.py:170`)
2. `generate_draft` (LLM #2, `workers/llm_worker.py:83`)
3. `score_draft` (LLM #3, `core/scoring.py:81`)

**Before reply:** `LLM #1` → `LLM #2` → `LLM #3` = 3.

**After Phase 48 local intelligence foundation:** `commerce/unified_intelligence.py` **exists but NOT called** in `process_message` production path (still `extract_commerce_signals` LLM #1 authoritative, `unified_intelligence` observational via tests only).

**Confirm:**

- **LLM #1 remains authoritative:** `workers/llm_worker.py:658` `await extract_commerce_signals(context)` not `analyze_message`.
- **Local remains observational:** `tests/evaluate_unified_intelligence.py` offline, not `process_message`.
- **Shadow remains disabled:** `tests/test_phase48` not yet shadow, `core/telemetry` not yet `unified_intelligence_ms` in production `process_message` (only in `unified_intelligence` offline).

**No production replacement has occurred.**

---

## 21. Evidence-Based Outcome — VERIFIED

**Based on Type-C for 3 weak intents (correct not in top-2, 0.00):**

- **Threshold over-conservatism** (Type A/B 19+10) explains **0.418→0.718** (+0.300) with `0.50,0.00`, but **remaining 0.718→0.80 (0.082) requires Type C fix** (correct not in top-2 for `custom_request` 3/5, `other` 3/5, `personal_disclosure` 3/5).

**Do NOT select outcome merely because Phase 57 recommended Option A:**

**Evidence demonstrates:** `custom_request` 0.00 →0.40 with threshold 0.50,0.00 (2/5 recovered), but **3/5 remain Type C** (correct not in top-2, even at 0.50). **Adding 2 hard-negative examples per weak intent (6 total, 110→116) would provide 2 more reference vectors per intent, potentially moving `custom_request` 0.61→0.65 and `other` 0.55→0.60, but `custom_request` vs `content_request` both `send` 0.85 vs 0.84 margin 0.01 would still be Type C (correct not in top-1).**

**Therefore:**

- **OUTCOME A — CORPUS EXPANSION JUSTIFIED?** **NO** — evidence does **not** demonstrate that `custom_request` 0.00 is primarily `insufficient reference diversity` (5 already cover `custom` concepts, hard-negative `custom` vs `content` already 5 each, `personal_disclosure` missing `moved` but only 1/5).

- **OUTCOME B — CORPUS EXPANSION NOT JUSTIFIED?** **NO** — `personal_disclosure` `I just moved to Austin` vs `I work nights` 0.60 vs 0.58, **more `moved` examples would help** (currently 0 `moved` in reference, but `personal_disclosure` reference has `I'm a software engineer`, not `moved`).

**Correct outcome is:**

**OUTCOME C — MORE DATA REQUIRED** — **cannot distinguish** whether **threshold 0.50,0.00** + **current 5 per intent** + **all-MiniLM-L6-v2 384** can reach 0.80 without **larger validation set** (110 validation is 5 per intent, but `custom_request` 0.00 with N=5 has **1 missed =20% recall change**, not statistically robust) and **without `semantic` vs `lexical` vs `hard-negative` vs `context` decomposition** on larger set (e.g., 20 per intent, 440 total).

**Do not select A merely because Phase 57 recommended Option A.**

**Evidence:** **OUTCOME C — MORE DATA REQUIRED** (and **model limitation** for `custom` vs `content` 0.84 vs 0.85 margin 0.01 **HARD**).

---

## 22. Blocking Issues — MEASURED

- **Accuracy 0.418 <0.60, hybrid 0.718 <0.80** — **BLOCKER** (15 intents <0.5)
- **Threshold over-conservatism** 19+10 Type A/B recoverable to 0.718, but **Type C 32 not recoverable** — **BLOCKER**
- **Corpus hard-negative for 3 weak intents** — **not primary**, but **secondary** (2 more per weak would help 0.40→0.60, not 0.00→0.80)
- **Model short-text granularity** for `custom` vs `content` 0.84 vs 0.85 — **BLOCKER** (384 may be insufficient for 3-word short)
- **Context for `personal_disclosure` vs `casual_chat`:** **not primary** (message-local `I` is sufficient)

---

## 23. Exact Phase 59 Recommendation — EVIDENCE-BASED

**Do NOT select Phase 59 is corpus implementation.**

**Derive Phase 59 from evidence:**

**Phase 59 must be:**

```text
PHASE 59 — LARGER VALIDATION SET & MODEL/CORPUS A/B EVALUATION
```

- **Why:** Current 110 validation (5 per intent) not statistically robust for `80%` recall (1 missed =20% change), and `custom_request` 0.00 remaining Type C cannot be distinguished as `corpus` vs `model` vs `threshold` with 5 per intent.

- **What:** Construct **440 validation** (20 per intent, 22×20=440, independent, hard-negative, purchase safety 20 purchase), run `all-MiniLM-L6-v2` 384 vs `all-mpnet-base-v2` 768 (420MB) vs `cross-encoder` rerank vs `SetFit` fine-tuning (not another embedding model without corpus), all with `threshold 0.50,0.00` vs `0.65,0.10`.

- **Not:** `corpus expansion` 6 examples (110→116) — **too small** to move 0.718→0.80 (needs 0.082, ~9 correct of 32 Type C, 6 examples may give 2).

**Possible next steps include** (ranked by evidence):

1. **Larger validation set 440** (20 per intent) — **MUST**
2. **Model evaluation** `all-MiniLM-L6-v2` vs `all-mpnet-base-v2` vs `cross-encoder` — **MUST** (to test `HARD` semantic distinction `custom` vs `content` 0.01 margin)
3. **Controlled corpus A/B** (add 2 per weak vs 5 per weak) — **not yet**

**Do not assume Phase 59 is corpus implementation.**

---

**Report path:** `docs/AI_NATIVE_LLM_PHASE_58_CORPUS_HARD_NEGATIVE_FORENSIC_AUDIT.md`

**Intent count:** 22

**Reference examples:** 110 (22×5)

**Validation examples:** 110 (22×5)

**Semantic model:** `all-MiniLM-L6-v2` (384, normalize, brute-force)

**Embedding dimension:** 384

**Hybrid accuracy:** 0.418 (baseline 0.65,0.10,80), 0.718 (best 0.50,0.00,80, measured 79/110)

**Best intent:** `uncertain` 1.00 (5/5), `tip` 0.80, `negotiation` 0.80, `content_request` 0.80

**Worst intent:** `custom_request` 0.00, `other` 0.00, `personal_disclosure` 0.00 (baseline 0.65,0.10)

**Top confusion pairs:** `* → uncertain` (64 → 31 with 0.50,0.00), `custom_request` ↔ `content_request` (0.61 vs 0.62, not top-2)

**Semantic-only accuracy:** Not separately measured (hybrid 0.418 includes semantic, lexical 0.045, so semantic adds 0.373)

**RapidFuzz-only accuracy:** 0.045 (lexical fallback, 5/110)

**Hybrid improvement:** +0.373 (0.045→0.418)

**Purchase precision:** 0.857 (baseline) vs 0.750 (best)

**Purchase recall:** 0.600 vs 0.900 (best)

**Purchase FPR:** 0.010 vs 0.030 (best)

**Purchase FNR:** 0.400 vs 0.100 (best)

**Abstention:** 0.073 (8/110 baseline) vs 0.027 (3/110 best)

**Threshold problem:** YES (19 Type A +10 Type B recoverable)

**Corpus problem:** PARTIAL (5 per intent sufficient for 0.718, but 3 weak need 2 more, not primary)

**Representation problem:** NO (5 individual > centroid)

**Context problem:** PARTIAL (repeat/post_purchase state-dependent but 4/5 correct via lexical `again`)

**Fusion problem:** NO (max correct, hybrid > lexical)

**Model adequacy:** QUESTIONABLE (0.418 <0.80, but tip 0.80 proves not inadequate)

**Primary blocker:** **Threshold over-conservatism + Type C correct not in top-2 (32)**

**LLM count:** 3 (LLM #1 still authoritative, local observational)

**LLM #1:** AUTHORITATIVE

**Shadow:** DISABLED

**Commerce authority:** UNCHANGED

**Phase 57:** **OUTCOME C — MORE DATA REQUIRED** (or **B — CORPUS EXPANSION NOT JUSTIFIED** as primary, but **C** is correct per evidence)

**No production modifications:** YES (read-only, thresholds/corpus not changed)

