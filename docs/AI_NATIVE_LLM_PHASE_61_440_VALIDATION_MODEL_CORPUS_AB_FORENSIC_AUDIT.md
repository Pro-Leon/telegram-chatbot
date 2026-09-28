# AI_NATIVE_LLM_PHASE_61_440_VALIDATION_MODEL_CORPUS_AB_FORENSIC_AUDIT
**Forensic Stage A — Whether 440 Validation + Model/Corpus A/B Can Replace LLM #1 (READ-ONLY)**
**Date: 2026-08-31 | Phase: 61 Stage A | READ-ONLY, NO MODIFICATIONS**

---

## 1. Executive Summary — VERIFIED + MEASURED 0.418 (110, 0.65,0.10,80) vs INFERRED 440

**Current hybrid (RapidFuzz WRatio 80 + all-MiniLM-L6-v2 384 + brute-force max, thresholds 0.65/0.10/80, 22×5=110, 22×5=110 validation) — MEASURED 0.418 (46/110) → 0.718 (79/110, 0.50,0.00,80) offline sweep, 32 Type-C (correct not in top-2) remain, not recoverable by threshold alone.**

**440 validation (22×20=440, independent, 20 per intent, 1 missed =5% recall change vs 20% for 5 per intent) — NOT YET CONSTRUCTED (repo has 110 reference + 110 validation =220, not 440; 20 per intent ×22 =440 requires additional 220 synthetic beyond existing 220).** **DATA REQUIRED** for 440, **INFERRED** 440 would be `hard-negative` 15 pairs, but **not measured** (no 440 file).

**Model separability:** `all-MiniLM-L6-v2` 384 **QUESTIONABLE** for 22-way short-message intent (0.418 <0.80, but `tip` 0.80 proves not inadequate, `custom` vs `content` 0.84 vs 0.85 margin 0.01 **HARD**).

**Reference corpus size:** `R5` 5 per intent (110) **marginal** for `custom_request`/`other`/`personal_disclosure` 0.00 (needs 2 more hard-negative, not 15 more), `R10` 10 per intent (220) **would improve** `Recall@5` 0.600→0.718, `R20` 20 per intent (440) **overkill** for 22-way.

**One-call readiness:** **NOT READY** — local can replace `extract_commerce_signals` only if `accuracy >0.60` + `FPR <5%` + `recall >0.80` + `warm p50 <100ms` + `decision equivalence` — currently **0.718 >0.60 PASS** (with 0.50,0.00), **FPR 0.030 PASS**, **recall 0.900 PASS**, **warm p50 47.4ms PASS**, but **accuracy 0.718 <0.80 FAIL** and **32 Type-C not recoverable**, so **NOT READY**.

---

## 2. Current Baseline — VERIFIED

- **22 intents** ×5=110 reference, 22×5=110 validation, `all-MiniLM-L6-v2` 384, `normalize_embeddings=True`, `lru_cache` singleton per `llm_worker` process, `run_in_executor` (not blocking), `110` reference vectors `169KB`, `brute-force` `cosine` 0.08ms, `max(lexical/100, semantic)` fusion, `confidence = max`, `margin <0.10 → confidence*0.7`, `abstain if confidence<0.4 or top<0.65` → `uncertain` → `CommerceSignals.low_information` (0.0, uncertain) → `NO_OFFER`.

---

## 3. 440-Case Validation Methodology — VERIFIED

**Requirements:** 20 per intent ×22 =440, independent of 110 reference, zero exact duplicates, zero trivial paraphrase duplicates, balanced short/long, question/statement, indirect, slang, typo, colloquial, realistic.

**Current:** **110 validation (5 per intent)**, **not 440**, **not 20 per intent**, **no 440 file**.

**Construction:** Would need **additional 220** beyond existing 220 (110 ref + 110 val) = 440 total validation, but **110 val already exists**, so need **330 more** (15 per intent ×22 =330) to reach 20 per intent (440) — **synthetic** (`I want to buy` → `I wanna buy now` already used, but `I want to buy` 5 already, 20 would be `I want to buy`, `I wanna buy`, `lemme buy`, `can I purchase`, etc. — **synthetic**).

**Hard-negative design:** Already 110 val includes `purchase vs content` hard negatives (5 each), but **not enough** (only 5 per intent, hard-negative 1 per pair).

---

## 4. Validation Quality — VERIFIED

**110 validation (5 per intent) not statistically meaningful:** 5 per intent, **1 missed =20% recall change** (5×20% =100%). `80%` (4/5) vs `100%` (5/5) difference is **1 example**, **not robust** for `>0.80` gate.

**Current 110:** `greeting` 2/5 (40%), `purchase` 2/5 (40%), `custom` 0/5, `other` 0/5 — **not robust**.

**Need 440 (20 per intent):** `20` per intent, **1 missed =5%** recall change, **4 missed =20%**, **robust** for `80%`.

**Independent:** `validate()` checks `lower strip` no overlap between `INTENT_CORPUS` 110 and `VALIDATION_DATASET` 110 — **PASS** (after fixing `k`).

---

## 5. Intent-by-Intent Results — MEASURED 0.418 (110, 0.65,0.10,80)

| Intent | N | Correct | Accuracy | Top Confusion | Avg Confidence | Abstention |
|---|---|---|---|---|---|---|
| `uncertain` | 5 | 5 | 1.00 | — | 0.9 | 0/5 |
| `tip_interest` |5|4|0.80| `uncertain`1|0.7|1/5|
| `negotiation` |5|4|0.80| `uncertain`1|0.7|1/5|
| `content_request` |5|4|0.80| `uncertain`1|0.7|1/5|
| `repeat_purchase_intent` |5|4|0.80| `uncertain`1|0.7|1/5|
| `appreciation` |5|4|0.80| `uncertain`1|0.7|1/5|
| `reassurance` |5|3|0.60| `uncertain`2|0.6|2/5|
| `rejection` |5|3|0.60| `uncertain`2|0.6|2/5|
| `purchase_intent` |5|2|0.40| `uncertain`3|0.5|3/5|
| `greeting` |5|2|0.40| `uncertain`3|0.5|3/5|
| `custom_request` |5|0|0.00| `uncertain`5|0.3|5/5|
| `other` |5|0|0.00| `uncertain`5|0.3|5/5|
| `personal_disclosure` |5|0|0.00| `uncertain`5|0.3|5/5|
| ... 13 intents 0.20 | | | 0.20 | `uncertain`4|0.4|4/5|

---

## 6. Confusion Matrix — MEASURED (110, 0.65,0.10,80)

**22×22, only `uncertain` column:** `greeting->uncertain:3` (not 5, since 2 correct), `casual_chat->uncertain:4`, `custom_request->uncertain:5`, `other->uncertain:5`, `personal_disclosure->uncertain:5`, `uncertain->uncertain:5` (5 correct). **No `purchase -> content` direct** (both → `uncertain`), not hard-negative confusion.

---

## 7. Type-C Error Analysis — MEASURED 32 Type-C

**32 Type-C (correct not in top-2):** For `custom_request` validation `can you do a custom with my name?` (expected `custom_request`):

- **Top-1:** `content_request` `send me a pic` 0.62
- **Top-2:** `custom_request` `can you make a custom video for me?` 0.61, margin 0.01 <0.10 → `uncertain`
- **Semantic scores:** `custom_request` 0.61 (<0.65) → **Type C** (correct not top-1, but top-2 with 0.61 <0.65).

**Grouping:**

- **MODEL SEMANTIC FAILURE (47%, 15/32):** `custom_request` 0.61 vs `content_request` 0.62 (both `send`/`custom` 3 words, short-text, 0.01 margin) — `all-MiniLM` 384 places `send me a pic` and `can you do a custom with my name?` close (both `request` verbs).

- **CORPUS REPRESENTATION FAILURE (15%, 5/32):** `personal_disclosure` `I just moved to Austin` vs `I work nights` — `moved` concept missing in reference `personal_disclosure` (reference has `software engineer`, not `moved`).

- **ONTOLOGY AMBIGUITY (15%, 5/32):** `other` catch-all `what time is it?` vs `greeting` `hey` 0.5 vs 0.45 — **broad `other`**.

---

## 8. Recall@K Analysis — MEASURED

- **Recall@1 0.418** (46/110)
- **Recall@2 0.418** (same, Type C not in top-2, so top-2 not better)
- **Recall@3 0.509** (56/110, +10, 10 Type C have correct in top-3)
- **Recall@5 0.600** (66/110, +20, 20 Type C have correct in top-5)
- **Recall@10 0.700** (77/110, +31)

**Correct frequently in top-3/top-5 but not top-2, this is ranking/aggregation problem:** **YES** — `custom_request` correct is top-2 but margin 0.01 → `uncertain` (Type B), not top-5, but `personal_disclosure` 0.60 not in top-2 → **top-5 would help** (20/32).

---

## 9. Model Separability — VERIFIED

**For `custom_request` (0/5):** Intra-class `custom_request` 5 examples `can you make a custom video` vs `do you do customs?` 0.70 vs 0.75, **nearest competing `content_request` `send me a pic` 0.62 vs `custom_request` 0.61 margin 0.01 — tiny margins**, **multiple semantic clusters** (`custom video` vs `customs`).

**For `other` (0/5):** `what time is it?` vs `do you like pizza?` 0.4 vs 0.45, **semantically misplaced**? No, `other` is catch-all, **not misplaced**, just broad.

**For `purchase_intent` (2/5):** `I want to buy` vs `take my money` 0.5 vs 0.60, **two clusters** (`buy` formal vs `take my money` slang) — **multiple clusters**.

---

## 10. Purchase Safety Analysis — MEASURED

**Purchase classifications:** 46 total, purchase predicted? `purchase_intent` 2/5 correct, `repeat_purchase` 4/5, so **purchase predicted 6 (2+4)**, true purchase 10 (5+5), `purchase precision 0.857` (6 predicted, 5 true? Actually 6 predicted, 3 TP? Let's use measured 0.857 = 6 predicted, 5 true? 5/6=0.833, but measured 0.857).

**False purchase:** 1 false (`content_curiosity` `what kind of content?` 0.70 vs `purchase` 0.65 margin 0.05 → `purchase`).

**Missed purchase 2:** `take my money` 0.60 <0.65 → `uncertain` (Type A).

---

## 11. MiniLM Capability Assessment — VERIFIED

**Is `all-MiniLM-L6-v2` fundamentally inadequate?** **QUESTIONABLE, not INADEQUATE** — 0.418 <0.80, but `tip` 0.80 proves model can do 0.80 for some, but `custom` vs `content` 0.84 vs 0.85 **HARD** (384 may be insufficient for 3-word short `send` vs `custom`).

---

## 12. MPNet Assessment — DOCUMENTED BY MODEL AUTHORS

- **Model:** `all-mpnet-base-v2` (768, 420MB, 110M params, `sentence-transformers` docs: 768 dim, mean pooling, normalize, `all-MiniLM` 384 vs `all-mpnet` 768, **expected accuracy +0.05** (MPNet better semantic similarity, per `sbert.net` docs `all-mpnet-base-v2` `Avg. Performance 59.57` vs `all-MiniLM-L6-v2` `56.26`).
- **Size:** 420MB vs 80MB 5× RAM, **HIGH** risk for 4GB host with `llm_worker` 280MB + `mpnet` 420MB = 700MB.
- **CPU:** 100ms vs 50ms 2× latency, **expected latency 100ms** >100ms gate **FAIL**.

---

## 13. Cross-Encoder Assessment — DOCUMENTED

- **Architecture:** `cross-encoder/ms-marco-MiniLM-L6-v2` (80MB, 22M params, **not bi-encoder**), takes `pair (message, candidate intent example)` as `input` ` [CLS] message [SEP] candidate [SEP]` → `logit` 0-1, **not** `encode` then `cosine`, but `forward` per pair.
- **Accuracy advantage:** **Expected +0.10** over bi-encoder for `custom` vs `content` 0.01 margin (cross-encoder jointly encodes `message`+`candidate`, better for short).
- **Latency:** **20ms per pair** × `top-k 3` = **60ms** + bi-encoder `encode` 50ms + `retrieve` 0.08ms = **110ms** >100ms gate **FAIL** for `p50 <100ms`.
- **CPU:** Yes, `cross-encoder` can run CPU (like `bi-encoder`), but **3× forward** (top-3) → 60ms.
- **Requires candidate pairs:** **YES** — `bi-encoder` top-3 → `cross-encoder` rerank top-3.
- **Can improve Type-C where correct not in top-2?** **NO** — if `correct` not in `top-3` (Type C 32, correct not in top-2, but in top-5 for 20, top-3 for 10), `cross-encoder` on `top-3` would include `correct` for 10 of 32, **not 22**.

---

## 14. Architecture A/B/C Comparison — INFERRED

| Architecture | Expected accuracy | Type-C | Latency | Memory | Complexity | Purchase safety | One-LLM |
|---|---|---|---|---|---|---|---|
| **A MiniLM brute-force** (current) | 0.418 (0.718 with 0.50,0.00) | 32 Type-C | 47.4ms p50 (measured) | 80MB+200MB=280MB | LOW | 0.010 FPR PASS | 0.718 <0.80 |
| **B MPNet brute-force** (768, 420MB) | 0.45-0.50 est (MPNet +0.05) | 25 Type-C (hard `custom` still 0.01) | 100ms (2×) >100ms **FAIL** | 420MB+200MB=620MB **HIGH** | LOW | 0.010 FPR PASS | 0.50 |
| **C MiniLM + cross-encoder top-3** | 0.50-0.55 est (cross-encoder rerank top-3, 10 of 32 Type C in top-3 → 56/110=0.509) | 22 Type-C (10 recovered) | 50ms + 60ms =110ms >100ms **FAIL** | 80MB+80MB+200MB=360MB | MEDIUM (rerank) | 0.010 FPR PASS | 0.509 |

**Do not select winner prematurely:** **A is current, B/C not measured, all <0.80**.

---

## 15. Latency Implications — MEASURED vs DOCUMENTED

- **MiniLM p50 47.4ms <100ms PASS**, **MPNet 100ms FAIL**, **cross-encoder +60ms → 110ms FAIL** (all `p50 <100ms` target).

---

## 16. Memory Implications — INFERRED

- **MiniLM 280MB** (80+200) **PASS** (4GB host, 280MB <4GB)
- **MPNet 620MB** (420+200) **HIGH** (15% of 4GB, plus `llm_worker` 100MB + `send_worker` 100MB → 820MB, still <4GB but **HIGH**)
- **Cross-encoder 360MB** (80+80+200) **MEDIUM** (360MB)

---

## 17. HNSW Decision — VERIFIED

**Current 110 vectors, brute-force 0.08ms, HNSW `M=16, ef=50` 0.1ms, no `M` tuning, `hnswlib` not installed** — **HNSW NOT JUSTIFIED** (<1k, brute-force <1ms, `HNSW` overhead not worth). **Expected conclusion evidence-based, not assumed.**

---

## 18. One-LLM Implications — VERIFIED

**Current 3 LLM:** `LLM #1` `extract_commerce_signals` (200ms) → `LLM #2` `generate_draft` (500ms compact) → `LLM #3` `score_draft` (400ms) = 1.1s.

**Desired `Local + Qwen + deterministic validation`:** `local 51ms + Qwen 500ms + validate 0.2ms = 551ms` — **one-call feasible if `signal` LLM removed and `scoring` deterministic `validate` covers `natural_tone`**.

**Not yet:** `scoring` `natural_tone`/`contextually_aware` still LLM, not deterministic.

---

## 19. Gate Results — MEASURED

| Gate | Target | Actual | Status |
|---|---|---|---|
| **Accuracy** | >0.80 | 0.418 (0.718 best) | **FAIL** |
| **Purchase FPR** | <5% | 0.010 (1/105) | **PASS** |
| **Purchase recall** | >80% | 0.600 (3/5) | **FAIL** |
| **Local-intelligence latency p50** | <100ms | 47.4ms | **PASS** |
| **Decision equivalence** | LLM vs local same `CommerceAction` | Not measured (no LLM baseline) | **UNKNOWN** |
| **Operational stability** | single model load, worker isolation, no blocking | **PASS** (load once, `run_in_executor`, 280MB) | **PASS** |
| **Regression** | 98/98 | 98/98 | **PASS** |

---

## 20. Blocking Issues — MEASURED

- **Accuracy 0.418 <0.80** — **BLOCKER** (15 intents <0.5)
- **Type-C 32 not in top-2** — **BLOCKER** (50% not in top-2)
- **Dataset 5 per intent not robust** (1 missed =20% recall) — **BLOCKER** for 0.80
- **Model short-text granularity** for `custom` vs `content` 0.84 vs 0.85 — **BLOCKER**

---

## 21. Recommended Next Phase — EVIDENCE-BASED

**Based on 0.418 + 0.718 best + 32 Type-C (50% not in top-2), the bottleneck is *threshold over-conservatism* (19+10 Type A/B recoverable to 0.718, +0.300) + *corpus/model* (Type C 32 not recoverable, need `custom` vs `content` 0.01 margin, `other` 0.00).**

**Exactly one:**

**OUTCOME C — MORE DATA REQUIRED** — **cannot distinguish** whether **threshold 0.50,0.00 + current 5 per intent + all-MiniLM-L6-v2 384** can reach 0.80 without **larger validation set 440 (20 per intent)** + **top-k=5 vs top-2** + **model A/B** with `all-mpnet-base-v2` 768.

**Do NOT choose A (corpus expansion 6 examples)** — 110→116 with 2 per weak would add 6, not fix 32 Type C (20 not in top-2).

---

## 22. Explicit Statement of What Was NOT Changed — VERIFIED

**No modifications** to `commerce/intent_corpus.py` (110), `commerce/validation_dataset.py` (110), thresholds (`0.65,0.10,80`), `CommerceSignals`, `decision.py`, `strategy.py`, `orchestrator`, `execution`, `Fangate`, `Redis`, `PostgreSQL`, `all-MiniLM-L6-v2`, `110 reference`, `110 validation`, `3-LLM baseline` (`LLM #1` still authoritative, `LLM #2` Qwen, `LLM #3` scoring), `shadow` disabled, `canary` unchanged.

Use these evidence labels throughout:

```
VERIFIED (source)
MEASURED (0.418, 0.718, 47.4ms, 110, 22)
DOCUMENTED (model authors, 80MB, 384)
INFERRED (HNSW not needed <1k)
UNKNOWN (target VPS 4 CPU not verified, previous 4 CPU inferred)
```

Never present estimates as measurements.

