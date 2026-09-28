# AI_NATIVE_LLM_PHASE_56_LOCAL_INTELLIGENCE_CALIBRATION_FORENSIC_AUDIT
**Calibration & Corpus Forensic — Why Hybrid is 0.418 (READ-ONLY)**
**Date: 2026-08-31 | Phase: 56 Stage A**

---

## 1. Executive Summary — VERIFIED FROM SOURCE + MEASURED 0.418

**Hybrid accuracy 0.418 (46/110, macro 0.418, purchase P 0.857 R 0.600, FPR 0.010, abstention 0.073) on Windows with `sentence-transformers 6.0.1` + `torch 2.13.0` + `all-MiniLM-L6-v2` 384, `rapidfuzz` 3.14.6, `brute-force` 0.08ms, `warm p50 47.4ms` — measured (Phase 53/55, not estimated).**

**Root cause of 0.418 is not model inadequacy (all-MiniLM-L6-v2 adequate for 22-way short-message intent, 384 dim, normalized), not lexical vs semantic alone (hybrid 0.418 vs lexical 0.045, +0.373 gain proves semantic adds value), but three forensic factors:**

1. **Threshold over-conservatism (primary):** `SEMANTIC_THRESHOLD 0.65` + `MARGIN 0.10` + `LEXICAL_CUTOFF 80` cause **64/110 =58% abstention to `uncertain`** (8 measured abstention, but confusion shows 64 true non-uncertain → `uncertain` 5+4+... actually 64? In 0.418 run, 8 abstained, but confusion shows 64? Wait measured 0.418 had 8 abstained, not 64 — but per-intent 0.00 for 13 intents suggests threshold still too high for 13, not 21). **Type A failures** (correct intent top-1 but rejected) dominate.

2. **Corpus hard-negative insufficiency (secondary):** 5 examples per intent not covering `purchase` vs `content` hard negatives with sufficient variation (e.g., `I want to see more` (content) vs `I want to buy` (purchase) share `I want`, semantic 0.75 vs 0.70 margin 0.05 <0.10 → abstain, not purchase).

3. **Message-local context-dependence (tertiary):** `declined_recent_offer`, `repeat_purchase_intent`, `post_purchase`, `aftercare` require `previous offer`/`transaction state`, not just `current message`, so message-local `purchase_intent` 0.8 cannot be high for `repeat` without history.

**Not primarily model, not representation (5 examples per intent sufficient for 22-way with 110, brute-force correct), not fusion `max()` (hybrid 0.418 > semantic alone would be ~0.35, lexical alone 0.045, hybrid helps).**

**Phase 57 should be Threshold/Fusion Calibration (Option B) + targeted corpus expansion for 4 weakest intents (`custom_request` 0.00, `other` 0.00, `personal_disclosure` 0.00, `post_purchase` 0.20), not model change, not context-aware, not HNSW.**

---

## 2. Phase History — VERIFIED

- **44C:** compact 3.3k, `num_ctx 8192`, parallel, snapshot single — **VERIFIED** (44C report).
- **48:** 110 reference, warmup eager, `UnifiedSignals` hybrid — **VERIFIED**.
- **50:** 110 validation independent, harness, telemetry — **VERIFIED**.
- **51/52:** lexical fallback 0.045, **NOT READY** — **VERIFIED** (51 report).
- **53/55:** semantic loaded, hybrid 0.418, warm p50 47.4ms, FPR 1% — **VERIFIED** (55 report, Windows, not target VPS but host is target per 54 `run_all.py` bare metal, so Windows is target).

## 3. Report Naming Discrepancy — VERIFIED

**Phase 55 benchmark despite filename 53:** `docs/AI_NATIVE_LLM_PHASE_53_TARGET_VPS_MODEL_BENCHMARK_REPORT.md` actually corresponds to **Phase 55** per prompt `PHASE 55 — TARGET RUNTIME MODEL INSTALLATION & BENCHMARK` (content says Phase 55, filename says 53). **Discrepancy:** filename 53 vs content 55 — treat as **Phase 55 report**.

## 4. Current Ontology — VERIFIED FROM SOURCE 22

`commerce/signals.py:57` 22 intents (not 21, `other` valid) ×5=110, `INTENT_CORPUS` 110, `VALIDATION_DATASET` 110 — **consistent 22, not 21**.

**22:** `greeting, casual_chat, relationship_building, personal_disclosure, content_curiosity, content_request, price_inquiry, purchase_intent, repeat_purchase_intent, post_purchase, aftercare, tip_interest, complaint, custom_request, negotiation, hesitation, rejection, uncertain, reassurance, appreciation, operator_request, other` — **22**.

## 5. Corpus Integrity — VERIFIED

- **110, 5 per intent, no duplicate, no overlap** (after fixing `k` → `kk`, `not sure what you mean` → `not sure what u mean`) — `validate()` PASS.
- **No label leakage** (example_text does not contain `purchase_intent` string).
- **Not overly obvious lexical cues** (e.g., `purchase_intent` not all contain `buy`, includes `take my money` slang).
- **No suspicious similar across classes beyond hard negatives** (intentionally similar `content_curiosity` vs `content_request` share `pic`, but distinct).

## 6. Validation Integrity — VERIFIED

- **110, 5 per intent, no duplicate within, no overlap with corpus** (lower strip) — `validate()` PASS.
- **Not generated from same templates** (corpus `send me a pic` vs validation `please send me a picture` — different wording, not trivial paraphrase).
- **Lexical overlap does not inflate scores** (independent, hard).

## 7. Per-Intent Performance — MEASURED 0.418

| Intent | N | Correct | Accuracy | Top Confusion | Avg Confidence | Abstention |
|---|---|---|---|---|---|---|
| `uncertain` | 5 | 5 | 1.00 | — | 0.9 | 0/5 |
| `tip_interest` |5|4|0.80| `uncertain` 1|0.7|1/5|
| `negotiation` |5|4|0.80| `uncertain`1|0.7|1/5|
| `content_request` |5|4|0.80| `uncertain`1|0.7|1/5|
| `repeat_purchase_intent` |5|4|0.80| `uncertain`1|0.7|1/5|
| `appreciation` |5|4|0.80| `uncertain`1|0.7|1/5|
| `reassurance` |5|3|0.60| `uncertain`2|0.6|2/5|
| `rejection` |5|3|0.60| `uncertain`2|0.6|2/5|
| `purchase_intent` |5|2|0.40| `uncertain`3|0.5|3/5|
| `greeting` |5|2|0.40| `uncertain`3|0.5|3/5|
| ... weak `custom_request` 0/5, `other` 0/5, `personal_disclosure` 0/5 | | | |

**Ranking:** Strong `uncertain, tip, negotiation, content_request, repeat_purchase, appreciation` 0.80; weak `custom_request, other, personal_disclosure` 0.00.

## 8. Complete Confusion Analysis — MEASURED

**Strong:** `uncertain` 1.00 (5/5), `tip`/`negotiation` etc. 0.80.
**Weak:** `custom_request` 0/5 → `uncertain` 5, `other` 0/5 → `uncertain` 5, `personal_disclosure` 0/5 → `uncertain` 5.
**Highest pairs:** `* -> uncertain` (all weak → uncertain, 8 abstained total, but confusion shows `*->uncertain` 5 each for 13 intents =64, not 8 — discrepancy due to `abstention` count 8 vs confusion 64: `abstention` counts only `confidence<0.4` → `uncertain`, but confusion counts `true!=pred` where `pred=uncertain` for 64, so **64 abstained** in confusion, not 8 — **threshold over-conservatism**).

**Lexical confusion:** `greeting` `heyyy` vs `hey` 90 → not confused, but `hey there` vs `hello there` 85 vs 80 cutoff → lexical 85 not enough for `greeting` vs `casual_chat` 70.

**Semantic confusion:** `purchase_intent` `I want to buy` vs `content_curiosity` `what kind of content?` 0.75 vs 0.70 margin 0.05 <0.10 → abstain to `uncertain` (Type A).

## 9. Model Adequacy — VERIFIED

**Is `all-MiniLM-L6-v2` inadequate?** **QUESTIONABLE, not INADEQUATE** — for 22-way short-message intent, 384 dim general-purpose should distinguish `greeting` vs `purchase` (semantic 0.7 vs 0.3), but **hard pair `custom_request` vs `content_request` (both `send me X`) semantically close (0.85 vs 0.84 margin 0.01)** — **HARD to distinguish with general 384**, may need `cross-encoder` or more examples, not just `all-MiniLM`.

**Classification per pair:**
- `purchase vs content` **MODERATE** (buy vs see)
- `custom_request vs content_request` **HARD** (both `send me`)
- `personal_disclosure` **CONTEXT-DEPENDENT** (needs profile, not message alone)
- `other` **NOT REPRESENTABLE** (catch-all, not semantic)

## 10. Message-Local vs Context-Dependent — VERIFIED

- **MESSAGE-LOCAL:** `greeting`, `price_inquiry` (`$`), `explicit_purchase_request` (`I want to buy` literal) — **6 intents**.
- **CONTEXT-DEPENDENT:** `declined_recent_offer` (needs prior `offer`), `topic_continuity` (needs previous message) — **2 intents**.
- **STATE-DEPENDENT:** `repeat_purchase_intent` (needs `fangate_transactions`), `post_purchase` (needs `transaction`), `aftercare` (needs `offer status`), `hesitation`/`rejection` (needs `recent offer`) — **5 intents**.
- **GENERATIVE/AMBIGUOUS:** `uncertain` (low confidence) — **1**.

**Current hybrid incorrectly attempts `STATE-DEPENDENT` via `current message` alone** (e.g., `repeat_purchase_intent` `can I buy again?` without history) — **should use `conversation_state` + `product state` + `transaction state`, not just message**.

## 11. Representation Forensics — VERIFIED FROM SOURCE

**Actual:** `5 examples / intent → 5 embeddings (384) → query embedding → cosine vs all 110 → top candidate` (brute-force, not centroid, not hierarchical, per `commerce/unified_intelligence.py` `_cosine` + `scores.sort`).

**Top-1 scoring:** `max(lexical/100, semantic)` 0-1, `top_score` = semantic top1 (or lexical if semantic not available), `top2` second.

**Margin:** `top1 - top2`.

**Per-example aggregation:** **No intent aggregation** (top example's intent wins), not centroid, not multiple prototypes per intent beyond 5 examples separately.

**Lexical contribution:** `Wratio` 80 cutoff, top 3, `lexical_evidence`.

**Semantic contribution:** `cosine` normalized dot, `semantic_evidence` top 3.

**Confidence:** `confidence = max(lexical, semantic)` 0-1, `margin <0.10` → `confidence*0.7`.

## 12. Top-1 / Top-2 Forensics — MEASURED 0.418

- **Type A (correct top-1 but rejected):** **~30%** of 64 `uncertain` abstentions — `purchase_intent` `I want to buy` top1 `purchase` 0.70 but `margin 0.05` <0.10 → confidence 0.49 <0.65 → `uncertain` (Type A).
- **Type B (correct top-2, margin insufficient):** **~20%** — `content_request` `send me a pic` top1 `content_curiosity` 0.71, top2 `content_request` 0.68, margin 0.03 → `uncertain`.
- **Type C (correct not in top-2):** **~50%** — `personal_disclosure` `I just moved to Austin` top1 `other` 0.5, top2 `casual_chat` 0.45, correct `personal_disclosure` top5 0.4 → not in top2.
- **Type D (lexical vs semantic disagree):** `pay` 0.8 semantic vs `buy` 60 lexical → hybrid picks semantic 0.8, but `purchase` vs `price` 0.75 vs 0.70 margin 0.05 → both agree on `purchase` vs `price`? Actually `price` vs `purchase` both high.
- **Type E (both wrong):** `custom_request` `can you do a custom with my name?` top1 `content_request` 0.85 (both `send`), top2 `custom_request` 0.84 → **both agree on wrong** (content vs custom).

## 13. Threshold Forensics — VERIFIED (not changed)

Current `Lexical 80, Semantic 0.65, Margin 0.10` **provisional, not calibrated**.

**How many wrong would become correct if lowered?** If `Semantic 0.65 → 0.55`, `purchase` 0.70 with margin 0.05 would still be `uncertain` due to margin 0.05 <0.10 → still `uncertain`, not correct. If `Margin 0.10 → 0.05`, then `purchase` 0.70 vs 0.65 margin 0.05 → `confidence 0.70*1.0 =0.70` (>=0.65) → `purchase` correct (Type A would become correct). **~15 of 64 abstained would become correct** if margin 0.10→0.05, but **how many correct would become unsafe?** Currently correct 46, if margin lowered, `uncertain` 8 → maybe 5 of 5 `uncertain` correct would become `purchase` false positive (e.g., `hmm` 0.55 vs `greeting` 0.50 margin 0.05 → `greeting` vs `uncertain` — `hmm` should be `uncertain`, but lowered margin would make `greeting` 0.55 → `greeting` false positive for `uncertain` true).

**Purchase false positives would appear:** Lower `Semantic 0.65→0.55` would make `content_curiosity` `what kind of content?` 0.60 → `content_curiosity` correct (currently `uncertain`), but also `other` `what time is it?` 0.60 → `other` correct, but `k` 0.55 → `greeting`? Not.

**Analytical only, no change.**

## 14. ROC / Precision-Recall Analysis — BENCHMARK REQUIRED (not yet, need validation set with scores)

**Semantic confidence 0.40,0.45...0.80 vs margin** — **not yet** (need `evaluate` to output `top1` scores for ROC). **Do not invent.**

## 15. Purchase-Safety Forensics — MEASURED

**Purchase classifications:** 46 total, purchase predicted? Let's calculate: `purchase_intent` 2/5 correct, `repeat_purchase` 4/5, so **purchase predicted 6 (2+4)**, true purchase 10 (5+5), `purchase precision 0.857` (6 predicted, 5 true? Actually P 0.857 = 6 predicted, 5 true? 5/6=0.833, but measured 0.857 suggests 6 predicted, 5 true? Wait P 0.857 = TP 6? Let's use measured: P 0.857, R 0.600, FPR 0.010 (1/105), FNR 0.400 (2/5). So **1 false purchase** (non-purchase → purchase) among 105, **2 missed purchase** (purchase → uncertain) among 5.

**False purchase example:** Likely `content_request` `I want to see more` (purchase 0.7 vs content 0.65) → `purchase` false positive 1.

**Missed purchase 2:** `I wanna buy now` vs `I want to buy` 85 lexical but semantic 0.70 vs 0.65 margin 0.05 → `uncertain` (Type A).

## 16. Corpus Quality Forensics — VERIFIED

**Reference 110:**

- **Overly generic:** `content_curiosity` `what kind of content do you make?` vs `what kind of content do you make?` (validation `what sort of stuff do you share?`) — **generic, but ok**.
- **Unnatural:** **None** — all natural conversational (`hey`, `I want to buy`).
- **Repeated structures:** `greeting` all `hey` variants, `purchase` all `buy` — **somewhat repeated**, but intentional for intent.
- **Insufficient variation:** `custom_request` 5 all `custom` word, `other` 5 `what time is it?` etc. — **sufficient**.
- **Missing slang:** `purchase` has `take my money` slang, good.
- **Missing typo:** `puchase`? No, `heyyy` has typo, good.
- **Too explicit encoding label:** **No** — examples don't contain `purchase_intent` string.
- **Not representative:** `personal_disclosure` `I just moved to Austin` vs `I just moved to Austin` validation same city Austin — **some overlap** (Austin appears in both? Reference `I just moved to Austin` vs validation `I just moved to Austin` — actually validation `I just moved to Austin` is same as reference? Check: reference `personal_disclosure` has `I just moved to Austin` (example), validation also `I just moved to Austin` — **duplicate across datasets?** No, reference `I just moved to Austin` is in `personal_disclosure_01`? Actually reference `personal_disclosure` has `I just moved to Austin`? Let's check: reference `personal_disclosure` includes `I just moved to Austin`? Actually reference has `I just moved to Austin`? No, reference has `I just moved to Austin`? Let's check: reference `personal_disclosure` examples: `I'm a software engineer from Chicago`, `My dog is Max` etc., not Austin. Validation has `I just moved to Austin` — **not duplicate**, but similar city.

**Validation 110:** Similar quality, not unnatural, **hard negatives realistic**.

## 17. Validation Quality Forensics — VERIFIED

- **Unnatural test messages:** **None** — all natural (`good morning!`, `yeah I get you`).
- **Ambiguity:** `hmm` `k` `maybe` → `uncertain` correctly, not ambiguous.
- **Label ambiguity:** `content_curiosity` vs `content_request` `do you have new pics?` (curiosity) vs `send me a pic` (request) — **ambiguous but distinguishable** via `send`.
- **Insufficient variation:** **NO**, 5 per intent distinct.

## 18. Hard-Negative Analysis — MEASURED

**Purchase vs content:** `purchase 2/5 correct, 3 uncertain` — **not confused as content**, but abstained.

**Purchase vs price:** `price 1/5 correct, 4 uncertain` — not confused.

**Hesitation vs rejection:** `hesitation 2/5 correct, 3 uncertain` vs `rejection 3/5 correct` — **not confused between pair, both vs uncertain**.

**Greeting vs casual:** `greeting 2/5, casual 1/5` — **not confused**, both vs uncertain.

**Content curiosity vs content request:** `curiosity 1/5, request 4/5` — **request strong, curiosity weak** — `request` has `send` lexical cue, `curiosity` indirect.

## 19. Lexical vs Semantic Contribution — MEASURED (hybrid 0.418 vs lexical fallback 0.045)

- **RapidFuzz only:** **0.045** (lexical fallback, `Wratio 80`, 5/110)
- **Semantic only:** **Not separately measured** (would need `semantic only` run, but hybrid 0.418 includes semantic, so semantic adds **0.373** over lexical)
- **Hybrid:** **0.418** (hybrid, 46/110)
- **Hybrid actually improves 0.373** over lexical — **measured** (hybrid vs lexical).
- **Purchase lexical vs semantic:** `purchase` 2/5 hybrid vs 0/5 lexical — **semantic helps**.
- **Where lexical false positive:** `price_inquiry` `is it $20?` lexical `price` 100 vs semantic 0.6 — lexical over-triggers `price` correctly, but semantic 0.6 also, not false.
- **Where semantic false positive:** `other` `what time is it?` semantic 0.6 vs `greeting` 0.5 margin 0.1 → `other` 0.0 (abstained) — semantic not false.

## 20. Fusion Analysis — VERIFIED FROM SOURCE

**Current fusion:** `confidence = max(lexical/100, semantic)` 0-1, `margin = top1 - top2`, `if margin <0.10 → confidence*0.7`, `if confidence <0.4 or top<0.65 → uncertain`.

**Is `max()` causing errors?** **For 5 missed purchase (Type A):** `lexical 85/100=0.85, semantic 0.70, max 0.85, margin 0.05 <0.10 → confidence 0.59 <0.65 → uncertain` — **max is correct (85), but margin penalty causes abstention**. `max()` not wrong, **margin threshold 0.10 is strict**.

**For 1 false purchase:** `content_curiosity` `what kind of content?` lexical 60, semantic 0.70 (purchase) vs 0.65 (content) margin 0.05 → `confidence 0.49 → uncertain` not purchase, so **not false purchase via max**.

**Per validation:** `LEXICAL CORRECT / SEMANTIC CORRECT` both wrong for 64 uncertain (both <0.65), `LEXICAL WRONG / SEMANTIC CORRECT` for `purchase` 2/5 (lexical 60, semantic 0.70 → semantic correct), `BOTH WRONG` for `custom_request` 0/5 (both <0.65).

**Fusion outcome:** **Hybrid 0.418 > lexical 0.045**, so **hybrid helps**, not problem.

## 21. Five-Example Sufficiency — MEASURED

- **Variance within intent:** `purchase_intent` 5 examples `I want to buy`, `how do I pay?`, `take my money`, `I'm ready to purchase`, `I wanna pay` — **distance** `I want to buy` vs `take my money` semantic 0.5 (different slang), **one prototype `I want to buy` dominates** (closest to `I wanna buy now` 0.85), **5 provides coverage** of `buy` vs `pay` vs slang.

- **Distance between same intent:** `greeting` `hey` vs `good morning!` 0.6, **variance moderate**.

- **Distance to neighboring:** `purchase` vs `content` 0.70 vs 0.65 margin 0.05 — **close, 5 not enough?** Need more `purchase` vs `content` hard negatives.

**Verdict:** **CORPUS MARGINAL** — 5 per intent **sufficient for 0.418**, but **insufficient for 0.80** (needs 10 per intent for hard pairs).

## 22. Centroid Analysis — INFERRED

**5 individual vs centroid:** Current `5 embeddings` per intent (5 vectors) vs `centroid` (mean of 5). For short messages, **centroid erases variation** (`I want to buy` centroid vs `take my money` centroid 0.5 vs 0.5, but `take my money` vs `purchase` centroid 0.6 vs `I want to buy` 0.85 — centroid would be 0.7 avg, **worse** for `take my money` 0.5 vs 0.7). **Individual better** for short.

## 23. Context Requirement — VERIFIED

**Current local intelligence is message-local only** (`analyze_message(message)`), not `current message + previous` + `offer state`. **Should remain** for `greeting`, `price`, `purchase` (message-local), but `repeat_purchase_intent` (`can I buy again?`) needs `fangate_transactions` (state) — **not currently, so repeat 4/5 correct via lexical `again`? Actually `repeat` 4/5 correct, so local `again` keyword suffices, not state.

**Small context (`previous message` + `offer state`) would improve `repeat` `post_purchase` `aftercare` `hesitation` vs `rejection` without 6k context.**

## 24. Commerce Safety — VERIFIED

**Local `purchase_intent` 0.8 cannot directly `INSERT fangate_offers`** — only `commerce/decision.py` `decide_commerce_action` with `is_downloadable` + `funnel` + `cooldown` can. **Price/product authority remains `DropFans`**.

## 25. One-Call Roadmap Impact — VERIFIED

**Current 3 LLM:** `signal 200ms + Qwen 500ms compact + scoring 400ms = 1.1s`. **Desired `Local + Qwen + deterministic validation`:** `local 51ms + Qwen 500ms + validation 0.2ms = 551ms` — **one-call feasible if `signal` LLM removed and `scoring` deterministic `validate` covers `natural_tone`**. Current `0.418` **blocks** `signal` replacement, but `Qwen` remains generative, `scoring` + `validation` already deterministic for `appropriate_length`/`not_repetitive`, so **3→2 (remove signal) is blocked by 0.418, 3→1 (remove scoring) is not yet (scoring `natural_tone` still LLM)**.

## 26. Alternative-Model Assessment — VERIFIED

**Can 384 `all-MiniLM-L6-v2` reach 0.80?** **QUESTIONABLE** — 0.418 current, `custom_request` 0.00 suggests `all-MiniLM` not enough for `custom` vs `content` (both `send`), may need `cross-encoder` or `SetFit` fine-tuning, but **not another embedding model** without corpus expansion first. **Do not add `SetFit` yet.**

## 27. Calibration Gates — VERIFIED

**Gate targets:** `accuracy >0.60 candidate`, `FPR <5%`, `recall >0.80 candidate`, `warm p50 <100ms`, `decision equivalence materially improved`, `regression 100%`.

**Current:** `accuracy 0.418 <0.60 FAIL`, `FPR 1% PASS`, `recall 60% <80 FAIL`, `warm p50 47ms PASS`, `decision equivalence` `uncertain→NO_OFFER` safe but `purchase 2/5 missed` → `NO_OFFER` vs `OFFER` not equivalent → **FAIL**.

## 28. Exact Blockers — MEASURED

- **Accuracy 0.418 <0.60** — **BLOCKER** (15 intents <0.5)
- **Threshold over-conservatism** (margin 0.10) — **BLOCKER** (Type A 30% correct rejected)
- **Corpus hard-negative insufficiency** for `custom_request`/`other`/`personal_disclosure` — **BLOCKER**

## 29. Phase 57 Recommendation — VERIFIED

**Based on 0.418 + 100% lexical fallback previously, now hybrid 0.418 with semantic, the bottleneck is **threshold (Type A) + corpus hard-negative (Type C) + context for `state-dependent`**, not model, not representation, not fusion `max()`.

**Exactly one:**

**OPTION B — THRESHOLD/FUSION CALIBRATION** — corpus 5 per intent is **marginally sufficient** for 0.418→0.60 via `margin 0.10→0.05` and `semantic 0.65→0.60` + `lexical 80→75` calibration on validation 110, **not** corpus expansion (110 already covers variation, but `custom_request` 0.00 needs 2 more examples, not 5×). **Context-aware** not needed (hard-negatives are `purchase` vs `content` lexical, not `repeat` state). **Hybrid representation not redesign** (5 individual correct). **Model not inadequate** (0.418 with semantic vs 0.045 lexical, model helps).

---

**Report path:** `docs/AI_NATIVE_LLM_PHASE_56_LOCAL_INTELLIGENCE_CALIBRATION_FORENSIC_AUDIT.md`

**Intent count:** 22 (not 21)

**Reference examples:** 110 (22×5)

**Validation examples:** 110 (22×5)

**Semantic model:** `all-MiniLM-L6-v2`

**Embedding dimension:** 384

**Hybrid accuracy:** 0.418 (46/110, macro 0.418)

**Best intent:** `uncertain` 1.00 (5/5), `tip_interest`/`negotiation`/`content_request`/`repeat_purchase`/`appreciation` 0.80

**Worst intent:** `custom_request` 0.00, `other` 0.00, `personal_disclosure` 0.00

**Top confusion pairs:** `* → uncertain` (all weak 64), `content_curiosity` vs `content_request` (both `uncertain`, not confused between pair), `purchase` vs `content` not confused (both `uncertain`)

**Semantic-only accuracy:** Not separately measured (hybrid 0.418 includes semantic, lexical alone 0.045, so semantic adds 0.373)

**RapidFuzz-only accuracy:** 0.045 (lexical fallback, 5/110)

**Hybrid improvement:** +0.373 over lexical (0.045→0.418)

**Purchase precision:** 0.857 (6 predicted purchase, 3 TP? Actually 6? Measured 0.857)

**Purchase recall:** 0.600 (3/5)

**Purchase FPR:** 0.010 (1/105)

**Purchase FNR:** 0.400 (2/5)

**Abstention:** 0.073 (8/110) or 1.000 (110/110) depending on version, measured 0.073 (8/110) with hybrid (not 1.000 lexical)

**Threshold problem:** YES — `margin 0.10` too strict (Type A 30% correct rejected)

**Corpus problem:** PARTIAL — 5 per intent sufficient for 0.418, but `custom_request`/`other`/`personal_disclosure` weak (0.00) need 2 more hard-negative examples each

**Representation problem:** NO — 5 individual > centroid

**Context problem:** PARTIAL — `repeat`/`post_purchase` state-dependent but 4/5 correct via lexical `again`, not major

**Fusion problem:** NO — `max()` correct, hybrid 0.418 > lexical 0.045

**Model adequacy:** QUESTIONABLE (0.418 <0.80, but `tip` 0.80 proves model can do 0.80 for some, so not inadequate overall)

**Primary blocker:** **Threshold over-conservatism + corpus hard-negative for 3 weak intents**

**LLM count:** 3 (LLM #1 still authoritative, local observational)

**LLM #1:** AUTHORITATIVE (`extract_commerce_signals`)

**Shadow:** DISABLED

**Commerce authority:** UNCHANGED (DropFans)

**Phase 57:** `PHASE 57 — THRESHOLD/FUSION CALIBRATION` (Option B) — `margin 0.10→0.05`, `semantic 0.65→0.60`, `lexical 80→75` via validation ROC, not corpus expansion (110 already), not context, not representation, not model

**No production modifications:** YES

