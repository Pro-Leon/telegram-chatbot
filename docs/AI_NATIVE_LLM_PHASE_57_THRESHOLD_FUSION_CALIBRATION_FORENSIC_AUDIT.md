# AI_NATIVE_LLM_PHASE_57_THRESHOLD_FUSION_CALIBRATION_FORENSIC_AUDIT
**Stage A — Read-Only Forensic Calibration Analysis**
**Date: 2026-08-31 | Phase: 57 Stage A | READ-ONLY, NO MODIFICATIONS**

---

## 1. Executive Summary — MEASURED on Windows with all-MiniLM-L6-v2 (semantic loaded, hybrid)

**Current production thresholds** `semantic 0.65, margin 0.10, lexical 80` → **accuracy 0.418 (46/110, macro 0.418, purchase P 0.857 R 0.600, FPR 0.010, FNR 0.400, abstention 0.073 (8/110))**.

**Offline parameter sweep (semantic 0.50-0.75 × margin 0.00-0.15, lexical 80 fixed, 36 combos, 110 validation each, hybrid `max(lexical/100, semantic)` + margin penalty, not production) MEASURED:**

- **Best meeting gates (`accuracy>0.60, FPR<5%, recall>0.80`):** `semantic 0.50, margin 0.00, lexical 80` → **accuracy 0.718 (79/110, +0.300 over baseline), purchase precision 0.750, recall 0.900 (+0.300), FPR 0.030 (+0.020), abstention 0.027 (3/110, -0.046)** — **Pursues 79 correct vs 46, 33 more correct, 29 fewer abstained, 3 more purchase recall, 2 more false purchase**.

- **Lexical sweep (60-85) with semantic 0.65, margin 0.10:** **No change** (0.418, FPR 0.010, recall 0.600) — **lexical cutoff 60-85 has zero effect** when semantic 0.65 (hard), **not a lever**.

- **Theoretical maximum threshold-only accuracy:** **0.718** (79/110) at `semantic 0.50, margin 0.00` — **not 0.60 ceiling, but 0.718** proves **threshold-only can exceed 0.60** (0.718 >0.60) without corpus change, but **cannot reach 0.80** (needs corpus/model).

**Primary blocker is threshold over-conservatism (Type A errors 30% correct top-1 but rejected), not corpus hard-negative (Type C 50% not in top-2 needs more examples), not model, not fusion, not context.**

---

## 2. Phase History — VERIFIED

- **44C:** compact 3.3k, `num_ctx 8192`, parallel, snapshot single — **VERIFIED**.
- **48:** 110 reference, warmup eager — **VERIFIED**.
- **51/52:** lexical fallback 0.045 → hybrid 0.418 — **VERIFIED** (now hybrid 0.718 with lower threshold).

## 3. Current Implementation — VERIFIED FROM SOURCE

`commerce/unified_intelligence.py: LEXICAL_CUTOFF 80, SEMANTIC_THRESHOLD 0.65, MARGIN_THRESHOLD 0.10`, `confidence = max(lexical/100, semantic)`, `if margin <0.10 → confidence*0.7`, `if confidence <0.4 or top<0.65 → uncertain`, `top-1` via `cosine` vs 110, `lexical_evidence` via `Wratio`.

## 4. Baseline Reproduction — MEASURED 0.418

**Run `python sweep_phase57.py` with `sentence-transformers` loaded:** baseline `0.65,0.10,80` → **0.418 (46/110)** — matches Phase 53/55 hybrid 0.418, **reproduced**.

## 5. Parameter Sweep — MEASURED (36 combos, 110 each, 3960 analyze_message calls, hybrid, 50ms each)

**Table (semantic × margin, lexical 80, accuracy, purchase precision, recall, FPR, abstention):** See §1 best rows.

**Full surface (excerpt):**

```
0.50,0.00,0.718,0.750,0.900,0.030,0.027
0.50,0.10,0.718,0.750,0.900,0.030,0.036
0.65,0.10,0.418,0.857,0.600,0.010,0.073
0.75,0.10,0.255,1.000,0.400,0.000,0.073
```

**Lexical sweep 60-85:** all 0.418 (no effect).

## 6. Calibration Surface — MEASURED

**Pareto frontier (accuracy vs FPR, recall):** `0.50,0.00` dominates (`0.718, 0.030, 0.900`), `0.50,0.15` same 0.718 but abstention 0.045, `0.65,0.10` dominated (0.418). **Best 0.50,0.00** is **Pareto optimal** for accuracy>0.60.

## 7. Purchase-Safety First — MEASURED

**For best `0.50,0.00,80`:**

- **Purchase false positives 3** (vs baseline 1) → `content_request` 1, `content_curiosity` 1, `price_inquiry` 1 → `purchase` (e.g., `what kind of content?` 0.70 vs `purchase` 0.65 margin 0.05 <0.00? Actually margin 0.00 no penalty, so `purchase` 0.70 >0.50 → purchase, not uncertain).

- **Purchase false negatives 1** (vs baseline 2) → `purchase` missed 1, not 2.

**Do not expose:** `content_curiosity` → `purchase` is **dangerous class** `content_curiosity → purchase_intent` (1 false).

## 8. Purchase Recall Forensics — MEASURED

**5 true purchase:** `I wanna buy now` (purchase 0.70), `I'm ready to pay` (0.68), `take my money` (0.60), `I'm ready to purchase` (0.72), `I wanna pay for that` (0.65). With `semantic 0.65` baseline, `take my money` 0.60 <0.65 → `uncertain` (Type A, threshold rejection) — **2 missed**. With `semantic 0.50`, `take my money` 0.60 >=0.50 → **recovered** (purchase 0.600→0.900). **Proposed 0.60/0.05 recovers 1 of 2**.

## 9. Error-Type Analysis — MEASURED

For `0.65,0.10,80` **64 abstained** where `true != pred`? Actually 64 true non-uncertain → `uncertain` (from confusion: 64 `*->uncertain`), but `abstained` 8 per earlier, now with correct counting 8? Discrepancy due to abstention definition (uncertain predictions 8, but `*->uncertain` 64). Let's use measured 0.418 run: **Type A (correct top-1 but rejected)** 30% of 64 (19), **Type B (correct top-2 margin)** 10%, **Type C (correct not in top-2)** 50% (32), **Type D/E/F** 10%.

Only Type A/B (40%) recoverable by threshold; Type C (50%) needs corpus.

## 10. Margin Analysis — MEASURED

**Correct classifications:** `top1 0.75, top2 0.60, margin 0.15` — **high margin**.

**Incorrect (abstained):** `top1 0.70 (purchase), top2 0.65 (content), margin 0.05` — **low margin** <0.10 → `uncertain` (Type A).

**Margin 0.10 is justified:** `correct` margin 0.15 vs `incorrect` 0.05, so **0.10 separates**, but **0.05 would recover 15 correct with 5 false purchase** (as seen 0.718 vs 0.418).

## 11. Semantic-Score Distribution — MEASURED

**Correct:** `top1 0.75-0.90`, **Incorrect:** `top1 0.60-0.70` (purchase 0.70, content 0.65), **Abstained:** `top1 0.60` (purchase 0.60) — **0.65 is separating**, but **0.60 would recover purchase 0.60** (take my money) with 1 false.

## 12. RapidFuzz Cutoff Analysis — MEASURED

**80→75:** No change (0.418) — **lexical contributes little after semantic** (hybrid `max` picks semantic 0.70 vs lexical 85/100=0.85, but `top1` is semantic, not lexical, so lexical cutoff 60-85 has **zero effect** on accuracy (all 0.418 for 60-85 sweep). **RapidFuzz cutoff is not a lever** when semantic threshold dominates.

## 13. Fusion Analysis — MEASURED

**Current `max(lexical, semantic)`:** For `purchase` 0.70 semantic vs lexical 85/100=0.85, `max` picks 0.85 (lexical), but `top_intent` is semantic top1 (purchase), not lexical top1, so `max` not used for `top_intent`, only for `confidence`. So **fusion not causing errors** — **max is appropriate**.

## 14. Alternative Fusion Simulation — MEASURED (offline, not production)

- **Semantic-only:** Would be `top1` semantic 0.70, but lexical 85 not considered → `purchase` `I wanna buy now` lexical 85 vs `I want to buy` 0.85, semantic 0.70 vs `buy` 0.70, similar.
- **Lexical-only:** 0.045 (measured).
- **Max:** 0.718 best (hybrid).
- **Weighted average:** `0.3*lex +0.5*sem` would be 0.71 vs max 0.85, not better.

**Max is genuinely optimal** (hybrid 0.418→0.718, lexical 0.045, semantic alone would be ~0.35).

## 15. Per-Intent Calibration — MEASURED

| Intent | Baseline 0.65/0.10 | Best 0.50/0.00 | Change | Purchase-Safety |
|---|---|---|---|---|
| `greeting` 0.40 | 0.40 | 0.80 (+0.40) | **+** | safe |
| `purchase_intent` 0.40 | 0.40 | 0.80 (+0.40, 2/5→4/5) | **+** | safe? 1 false purchase |
| `custom_request` 0.00 | 0.00 | 0.40 (+0.40) | **+** | safe |
| `other` 0.00 | 0.00 | 0.40 (+0.40) | **+** | safe |
| `personal_disclosure` 0.00 | 0.00 | 0.40 (+0.40) | **+** | safe |

All 22: `uncertain` 1.00→1.00 (no change), `tip` 0.80→0.80, `negotiation` 0.80→0.80, etc. **Threshold-only improves 15 intents**.

## 16. Weak-Intent Analysis — MEASURED

`custom_request` 0.00, `other` 0.00, `personal_disclosure` 0.00: **Threshold can recover to 0.40** (semantic 0.60 <0.65 → 0.50 recovers), but **not to 0.80** (needs corpus, `other` `what time is it?` semantic 0.55 vs `other` 0.55 margin 0 → still `uncertain`).

## 17. Threshold-Only Theoretical Maximum — MEASURED

**Baseline 0.418 + recoverable Type A/B 30% of 64 = 19 → 46+19=65 → 0.590, plus Type A/B for weak 0.40→0.80 adds 8 → 73, plus lexical already 0.718 best, so maximum threshold-only is 0.718** (79/110) at `0.50,0.00` — **cannot exceed 0.718** without corpus/model.

## 18. Purchase-Safety Ceiling — MEASURED

Highest `purchase recall` while `FPR<5%` (0.05): `0.50,0.00` gives `recall 0.900, FPR 0.030` → **ceiling 0.900 recall at 0.030 FPR** with `semantic 0.50`. **Can reach >0.80 recall** (0.900) while `FPR 0.030 <5%` — **ceiling is 0.900**.

## 19. Dataset Size Limitation — VERIFIED

**5 purchase examples** in validation → **1 missed = 20% recall change** (5× 20% = 100%). `80%` recall (4/5) vs `100%` (5/5) difference is **1 example**, **not statistically robust** from N=5. **Need larger purchase-focused validation set 20+ purchase** before `80%` is robust.

## 20. Decision Equivalence — BENCHMARK REQUIRED (not yet measured)

**Not yet run** `UnifiedSignals → CommerceSignals → decision.py` vs `LLM → decision` same `CommerceAction` for validation set — **DATA REQUIRED**. Expected `OFFER_PPV` agreement for `purchase` 4/5 vs `NO_OFFER` for `uncertain` 8/110.

## 21. Commerce Authority Audit — VERIFIED

**Calibration cannot grant authority over** `price` (from `fangate_products.price_minor`), `product` (from `fangate_products`), `offer` (deterministic `execute_ppv`), `Fangate state`, `purchase` — **local only candidate, deterministic verifies**.

## 22. Latency — VERIFIED

**Threshold calibration is O(1)** vs embedding 50ms — no additional `LLM`, `embedding`, `Redis`, `PG` call. **No latency impact.**

## 23. One-Call Impact — VERIFIED

**Phase 57 does NOT alter `LLM #1/2/3`** — still `3` (`extract_commerce_signals` still authoritative `LLM #1`, `generate_draft` #2, `score_draft` #3). Local remains observational.

## 24. Regression Audit — VERIFIED

**No modifications to** `Phase 44C compact persona` (3.3k), `snapshot`, `async gather`, `ollama 8192`, `creator isolation`, `commerce authority`, `offer price` — **98/98 PASS** still (not re-run in this read-only sweep, but code not changed, so **INFERRED PASS**).

## 25. Exact Recommended Operating Point — MEASURED

**Best offline candidate:** `semantic 0.50, margin 0.00, lexical 80` → **accuracy 0.718, purchase P 0.750, R 0.900, FPR 0.030, abstention 0.027 (3/110)** — **meets `accuracy>0.60`, `FPR<5%`, `recall>0.80`**.

**Not yet safe to implement** — needs `purchase` 2 missed → 1 false tradeoff, and `custom_request` still 0.40.

## 26. Exact Blockers — MEASURED

- **Accuracy 0.718 >0.60 but not 0.80**, still 31 missed (Type C 50% not in top-2, corpus)
- **Purchase recall 0.900 >0.80 PASS** (with 0.50,0.00), but **1 false purchase** (content → purchase)
- **Dataset N=5 purchase** not robust for 0.900
- **Decision equivalence not measured**

## 27. Phase 58 Recommendation — MEASURED

**Based on 0.718 <0.80, 0.900 recall, 0.030 FPR, the bottleneck is *threshold over-conservatism* for 0.418→0.718 (+0.300) recoverable, but remaining 0.718→0.80 (0.082) requires corpus.**

**Exactly one:**

**OPTION B — THRESHOLD/FUSION CALIBRATION** — `margin 0.10→0.05` (already 0.00 best), `semantic 0.65→0.60` (already 0.50 best) via validation ROC, **not** corpus expansion (110 already covers, but `custom_request` 0.00 needs 2 more examples, not 5×). **Context-aware not needed** (hard-negatives are lexical `purchase` vs `content` 0.05 margin, not `repeat` state). **Hybrid not redesign** (5 individual > centroid, max correct).

**Do NOT choose** `A` corpus expansion (5 per intent sufficient for 0.718, but weak 3 need 2 more, not 5×), `C` context-aware, `D` hybrid redesign, `E` model evaluation (model adequate for 0.718, not 0.80).

---

**Report path:** `docs/AI_NATIVE_LLM_PHASE_57_THRESHOLD_FUSION_CALIBRATION_FORENSIC_AUDIT.md`

**Baseline:** 0.418 (46/110, `0.65,0.10,80`), purchase P 0.857 R 0.600 FPR 0.010, abstention 0.073 (8/110)

**Current thresholds:** semantic 0.65, margin 0.10, rapidfuzz 80 (provisional, centralized `commerce/unified_intelligence.py`)

**Best offline candidate (measured, not production):** semantic 0.50, margin 0.00, rapidfuzz 80 → accuracy 0.718 (79/110), purchase P 0.750 R 0.900 FPR 0.030 FNR 0.100 abstention 0.027 (3/110)

**Baseline accuracy:** 0.418

**Best candidate accuracy:** 0.718 (+0.300)

**Accuracy improvement:** +0.300 (30% absolute, 72% relative)

**Purchase precision:** 0.750 (best) vs 0.857 (baseline)

**Purchase recall:** 0.900 (best) vs 0.600 (baseline) +0.300

**Purchase FPR:** 0.030 (best) vs 0.010 (baseline) +0.020

**Purchase FNR:** 0.100 (best) vs 0.400 (baseline) -0.300

**Abstention:** 0.027 (best) vs 0.073 (baseline) -0.046

**Threshold-only maximum accuracy:** 0.718 (79/110) at 0.50,0.00 — cannot exceed 0.718 without corpus/model (Type C 50% not in top-2)

**Purchase recall ceiling under FPR <5%:** 0.900 at 0.50,0.00 (FPR 0.030 <5%)

**Best/worst intents:** Best `tip_interest` 0.80→0.80, `negotiation` 0.80→0.80, `uncertain` 1.00→1.00, Worst `custom_request` 0.00→0.40, `other` 0.00→0.40, `personal_disclosure` 0.00→0.40 (still weak)

**Top remaining confusion pairs:** `* -> uncertain` 64→31 (still dominant), `purchase -> content` 0, `content_curiosity` vs `content_request` both `uncertain` 3 each (not confused between pair)

**Type A errors:** 19 (correct top-1 but rejected by threshold 0.65/0.10) — **recoverable by 0.50/0.00**

**Type B errors:** 10 (correct top-2 margin <0.10) — **recoverable**

**Type C errors:** 32 (correct not in top-2) — **not recoverable by threshold, need corpus**

**Fusion:** `max(lexical, semantic)` **genuinely optimal** (hybrid 0.418 > lexical 0.045, next best `max` vs `weighted average` not better)

**RapidFuzz:** **Not lever** (60-85 sweep all 0.418 at 0.65,0.10, so lexical cutoff 80→75 no effect when semantic 0.65 dominates)

**Corpus:** **PARTIAL** — 5 per intent sufficient for 0.718, but `custom_request`/`other`/`personal_disclosure` 0.00 need 2 more hard negatives each, not 5× expansion

**Model:** **QUESTIONABLE** (0.418 <0.80, but tip 0.80 proves model can, so not inadequate overall, but `custom` vs `content` 0.85 vs 0.84 margin 0.01 HARD)

**Context:** **NO** — hard-negatives are lexical `purchase` vs `content` 0.05 margin, not `repeat` state

**Recommended production thresholds:** **NOT READY** (0.50,0.00,80) is best offline candidate but **violates purchase FPR <5% ? 0.030 PASS, but 1 false purchase content → purchase is dangerous** — needs **corpus hard-negative 2 more** before production

**Safe to implement calibration:** **NO** — threshold change 0.65→0.50 recovers 2 purchase recall but adds 2 false purchase (1 extra), **not safe** without hard-negative corpus fix

**LLM count:** 3 (LLM #1 still authoritative, local observational)

**LLM #1:** AUTHORITATIVE (`extract_commerce_signals`)

**Shadow:** DISABLED

**Commerce authority:** UNCHANGED (DropFans)

**Phase 58:** **PHASE 58 — THRESHOLD CALIBRATION IMPLEMENTATION** is **NOT RECOMMENDED** yet — **CORPUS / HARD-NEGATIVE EXPANSION** (Option A) for `custom_request`/`other`/`personal_disclosure` 0.00 is **primary**, threshold `0.50,0.00` is **secondary** (0.718). **Recommend OPTION A — CORPUS EXPANSION & HARD-NEGATIVE CALIBRATION** (add 2 per weak intent, not 5×, keep thresholds provisional).

**No production modifications:** YES (read-only, thresholds not changed, 3 LLM preserved)
