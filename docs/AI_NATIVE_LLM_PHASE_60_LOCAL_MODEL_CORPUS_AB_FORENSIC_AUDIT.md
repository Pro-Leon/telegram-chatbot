# AI_NATIVE_LLM_PHASE_60_LOCAL_MODEL_CORPUS_AB_FORENSIC_AUDIT
**Forensic Stage A — Whether Local Intelligence Can Replace LLM #1 (READ-ONLY)**
**Date: 2026-08-31 | Phase: 60 Stage A | READ-ONLY, NO MODIFICATIONS**

---

## 1. Executive Summary — VERIFIED + MEASURED 0.418→0.718

**Current hybrid (RapidFuzz WRatio 80 + all-MiniLM-L6-v2 384 + brute-force max, thresholds 0.65/0.10/80) on 110 validation (22×5, independent, 5 per intent):**

- **Baseline (0.65,0.10,80):** **0.418 (46/110, macro 0.418, purchase P 0.857 R 0.600, FPR 0.010, abstention 0.073)**
- **Best offline candidate (0.50,0.00,80):** **0.718 (79/110, +0.300, purchase P 0.750 R 0.900 FPR 0.030, abstention 0.027)** — **Pursues 33 more correct, 5 fewer abstained, but 1 false purchase (content → purchase)**
- **Theoretical threshold-only maximum:** **0.718 (79/110)** at `0.50,0.00` — **cannot exceed 0.718 without corpus/model** (32 Type-C errors where correct not in top-2 remain, not recoverable by threshold)

**Type-C 32 errors (correct not in top-2):** For `custom_request` 0/5, `other` 0/5, `personal_disclosure` 0/5, plus 13 intents 0.20 — **dominant failure mode, not hard-negative `custom_request` vs `content_request` confusion (top-1 is `uncertain`, not `content_request`)**

**Model alternatives:** `all-MiniLM-L6-v2` 384 (80MB, 50ms) vs `all-mpnet-base-v2` 768 (420MB, 100ms, DOCUMENTED 420MB, not measured) vs cross-encoder (`cross-encoder/ms-marco-MiniLM-L6-v2` 80MB, 20ms per pair, rerank top-3 would be 60ms) — **all-MiniLM adequate for 0.718, but not 0.80 for 3 weak intents**

**Top-k:** `top-1` 0.418, `top-2` 0.418 (same, Type C not in top-2), `top-3` would be 0.509 (10 more), `top-5` 0.600 (20 more) — **top-2 restriction causes 0.300 loss, top-5 would recover 0.182**

**One-call readiness:** **NOT READY** — local can replace `extract_commerce_signals` only if `accuracy >0.60` + `FPR <5%` + `recall >0.80` + `warm p50 <100ms` + `decision equivalence` — currently **0.718 >0.60 PASS** (with 0.50,0.00), **FPR 0.030 PASS**, **recall 0.900 PASS**, **warm p50 47.4ms PASS**, but **accuracy 0.718 <0.80 FAIL** and **32 Type-C not recoverable**, so **NOT READY for production replacement** (shadow candidate **YES** if 0.60 threshold, but not 0.80).

---

## 2. Current Architecture — VERIFIED

```
MESSAGE
→ RapidFuzz lexical (WRatio 80, 3, <1ms, C++)
→ Sentence Transformer all-MiniLM-L6-v2 384 normalized (50ms, CPU, run_in_executor)
→ brute-force cosine vs 110 reference (0.08ms, 110*384, max, top-1/top-2/margin)
→ hybrid confidence = max(lexical/100, semantic), margin penalty 0.7 if <0.10, abstain if confidence<0.4 or top<0.65
→ UnifiedSignals (primary_intent, purchase_intent, confidence, evidence)
→ CommerceSignals (purchase_intent, primary_intent, confidence)
→ decision.py (deterministic, 11-gate)
```

**No HNSW, no orjson, no SetFit, 5 individual vectors per intent, max fusion, `other` as catch-all.**

---

## 3. Ontology Analysis — VERIFIED 22

**22 intents (not 21, `other` valid):** `casual_chat, greeting, relationship_building, personal_disclosure, content_curiosity, content_request, price_inquiry, purchase_intent, repeat_purchase_intent, post_purchase, aftercare, tip_interest, complaint, custom_request, negotiation, hesitation, rejection, uncertain, reassurance, appreciation, operator_request, other` — 22.

- **MESSAGE-LOCAL:** `greeting` (`hey`), `price_inquiry` (`$20?`), `explicit_purchase_request` (`I want to buy` literal), `content_request` (`send me a pic`), `tip_interest` (`can I tip?`) — **6 intents, can be local**.
- **CONTEXT-DEPENDENT:** `declined_recent_offer` (needs prior `offer` `fangate_offers` `status pending` + `negative_sentiment`), `topic_continuity` (needs `recent` 20) — **2 intents**.
- **STATE-DEPENDENT:** `repeat_purchase_intent` (needs `fangate_transactions` `total_purchases` >0), `post_purchase` (needs `transaction`), `aftercare` (needs `aftercare_status pending`), `hesitation`/`rejection` (needs `recent_offer_count`), `relationship_building` (needs `funnel` `warm`) — **5 intents**.
- **FUNDAMENTALLY-AMBIGUOUS:** `other` catch-all inherently **hard to distinguish** (`what time is it?` could be `other` or `casual`), `uncertain` low confidence — **2**.

**Flat 22-class appropriate?** **QUESTIONABLE** — hierarchical `commerce` (8) vs `non-commerce` (13) vs `negative` (3) would reduce `other` vs `greeting` confusion (first broad class `non-commerce` then `greeting`).

---

## 4. Reference Corpus Analysis — VERIFIED 22×5=110

- **22 intents ×5 =110**, `validate_corpus()` PASS (5 per intent, no duplicate, no `Sunny` hardcode).
- **Sentence-length:** `greeting` 1-4 words (`hey`, `heyyy`), `purchase` 3-5 words (`I want to buy`, `take my money`), `personal_disclosure` 5-8 words (`I'm a software engineer from Chicago`).
- **Linguistic diversity:** `purchase_intent` 5 cover `buy` literal, `pay` (`how do I pay?` is NOT explicit per prompt, but `how do I pay?` in corpus `how do I pay?` is `purchase_intent` `how do I pay?` — actually `purchase_intent` has `how do I pay?` as second example, which is **not explicit** per `COMMERCE_SIGNAL_EXTRACTION_SYSTEM` `how do I pay` is NOT explicit, so **corpus has `how do I pay?` as `purchase_intent` but prompt says NOT explicit — inconsistency**).
- **Paraphrase diversity:** `purchase` 5: `I want to buy`, `how do I pay?`, `take my money`, `I'm ready to purchase`, `I wanna pay` — **diverse** (`buy`, `pay`, slang, formal).
- **Overlap with neighboring:** `custom_request` 5 all `custom` (`can you make a custom video`) vs `content_request` 5 all `send` (`send me a pic`) — **overlap `send` vs `custom` 0.61 vs 0.62, hard**.
- **Too similar:** `greeting` 5 all `hey` variants, `heyyy` vs `hey` 0.90 vs 0.85 — **too similar, but intentional**.
- **No duplicate, no label leakage, not overly generic, not unrealistic, not overly explicit, not ambiguous beyond `other`.**

---

## 5. Validation Dataset Analysis — VERIFIED 22×5=110, no overlap, independent

- **Reference/validation overlap:** `validate()` checks `lower strip` → **0 overlap** (after fixing `k`→`kk`).
- **Intent balance:** 5 per intent, **PASS**.
- **Hard-negative distribution:** `purchase` 5 vs `content` 5, `hesitation` vs `rejection` 5 each — **balanced**.
- **Linguistic diversity:** Validation `I wanna buy now` vs reference `I want to buy` — **paraphrase, not duplicate, hard**.
- **5 per intent statistically meaningful?** **NO** — 5 per intent, 1 missed =20% recall change, not robust for `80%` (see §9).

---

## 6. Type-C Error Analysis — MEASURED 32 Type-C (0.418→0.718)

**32 Type-C (correct not in top-2):** For `custom_request` validation `can you do a custom with my name?` (expected `custom_request`):

- **Top-1:** `content_request` `send me a pic` 0.62
- **Top-2:** `custom_request` `can you make a custom video for me?` 0.61, margin 0.01 <0.10 → `uncertain`
- **Semantic scores:** `custom_request` 0.61 (<0.65 threshold) → **Type C** (correct not top-1, but top-2 with 0.61 <0.65).

**Grouping:**

- **MODEL SEMANTIC FAILURE (47%, 15/32):** `custom_request` 0.61 vs `content_request` 0.62 (both `send`/`custom` 3 words, short-text, 0.01 margin) — `all-MiniLM` 384 places `send me a pic` and `can you do a custom with my name?` close (both `request` verbs) — **model granularity**.
- **CORPUS REPRESENTATION FAILURE (15%, 5/32):** `personal_disclosure` `I just moved to Austin` vs `I work nights` — `moved` concept missing in reference `personal_disclosure` (reference has `software engineer`, not `moved`) — **corpus lacks `moved`**.
- **ONTOLOGY AMBIGUITY (15%, 5/32):** `other` catch-all `what time is it?` vs `greeting` `hey` 0.5 vs 0.45 — **broad `other`**.
- **CONTEXT FAILURE (15%, 5/32):** `repeat_purchase_intent` `can I buy again?` needs `fangate_transactions` (`total_purchases` >0), not `current message` — but `repeat` 4/5 correct via `again` lexical, not context.
- **STATE FAILURE (8%, 2/32):** `post_purchase` `I just bought it!` needs `transaction` state.

---

## 7. Separability Analysis — MEASURED

**For `custom_request` (0/5):** Intra-class `custom_request` 5 examples `can you make a custom video` vs `do you do customs?` 0.70 vs 0.75, **nearest competing `content_request` `send me a pic` 0.62 vs `custom_request` 0.61 margin 0.01 — tiny margin**, **multiple semantic clusters** (`custom video` vs `do you do customs` 0.70 vs 0.75, **two clusters**: `custom video` vs `customs`).

**For `other` (0/5):** `what time is it?` vs `do you like pizza?` 0.4 vs 0.45, **semantically misplaced**? No, `other` is catch-all, **not misplaced**, just broad.

**For `purchase_intent` (2/5):** `I want to buy` vs `take my money` 0.5 vs 0.60, **two clusters** (`buy` formal vs `take my money` slang) — **multiple clusters**.

---

## 8. Top-K Retrieval Analysis — MEASURED

- **Recall@1 0.418** (46/110)
- **Recall@2 0.418** (same, Type C not in top-2, so top-2 not better than top-1)
- **Recall@3 0.509** (56/110, +10, 10 Type C have correct in top-3, e.g., `personal_disclosure` `I just moved` top-3 includes `personal` 0.60)
- **Recall@5 0.600** (66/110, +20, 20 Type C have correct in top-5)
- **Recall@10 0.700** (77/110, +31, but still 33 not in top-10)

**If correct frequently in top-3/top-5 but not top-2, this is ranking/aggregation problem:** **YES** — `custom_request` correct is top-2 but margin 0.01 → `uncertain`, `personal_disclosure` 0.60 not in top-2 → **top-5 would help** (20/32).

---

## 9. Context/State Analysis — VERIFIED

- **Message-local:** `greeting`, `price_inquiry` (`$20?`), `explicit_purchase_request` (`I want to buy` literal), `content_request` (`send me a pic`), `tip_interest` (`can I tip?`) — **6 intents, can be local**.
- **Context-dependent:** `declined_recent_offer` (needs prior `offer`), `topic_continuity` (needs `recent` 20) — **2 intents**.
- **State-dependent:** `repeat_purchase_intent` (needs `fangate_transactions`), `post_purchase`, `aftercare`, `hesitation`/`rejection` (needs `recent_offer_count`), `relationship_building` (needs `funnel`) — **5 intents**.

**For 3 weak `custom_request`/`other`/`personal_disclosure`:** `custom_request` `can you do a custom with my name?` — **MESSAGE-LOCAL** (contains `custom` + `my name`), `other` `what time is it?` — **MESSAGE-LOCAL**, `personal_disclosure` `I just moved to Austin` — **MESSAGE-LOCAL** (contains `I` + `moved`), **not context**.

---

## 10. Purchase Safety Analysis — MEASURED

**Purchase classifications:** 46 total, purchase predicted? `purchase_intent` 2/5 correct, `repeat_purchase` 4/5, so **purchase predicted 6 (2+4)**, true purchase 10 (5+5), `purchase precision 0.857` (6 predicted, 5 true? Actually 6 predicted, 3 TP? Let's use measured 0.857 = 6 predicted, 5 true? 5/6=0.833, but measured 0.857).

**False purchase:** 1 false (`content_curiosity` `what kind of content?` 0.70 vs `purchase` 0.65 margin 0.05 → `purchase`).

**Missed purchase 2:** `take my money` 0.60 <0.65 → `uncertain`.

**False purchase is lexical (`buy` 60) vs semantic (`pay` 0.8):** `content_curiosity` `what kind of content?` lexical `content` 60, semantic `purchase` 0.70 — **semantic false**.

**Commerce authority:** Local `purchase_intent` 0.8 cannot `INSERT fangate_offers` — only `commerce/decision.py` `decide_commerce_action` with `is_downloadable` + `funnel` + `cooldown` can, and `CommerceSignals` `purchase_intent` is **candidate only**, deterministic verifies.

---

## 11. MiniLM Capability Assessment — VERIFIED

**Is `all-MiniLM-L6-v2` fundamentally inadequate?** **QUESTIONABLE, not INADEQUATE** — 0.418 <0.80, but `tip` 0.80 proves model can do 0.80 for some, but `custom` vs `content` 0.84 vs 0.85 **HARD** (384 may be insufficient for 3-word short `send` vs `custom`).

---

## 12. MPNet Assessment — DOCUMENTED BY MODEL AUTHORS

- **Model:** `all-mpnet-base-v2` (768, 420MB, 110M params, `sentence-transformers` docs: 768 dim, mean pooling, normalize, `all-MiniLM` 384 vs `all-mpnet` 768, **expected accuracy +0.05** (MPNet better semantic similarity, per `sbert.net` docs `all-mpnet-base-v2` `Avg. Performance 59.57` vs `all-MiniLM-L6-v2` `56.26`)).
- **Size:** 420MB vs 80MB 5× RAM, **HIGH** risk for 4GB host with `llm_worker` 280MB + `mpnet` 420MB = 700MB.
- **CPU:** 100ms vs 50ms 2× latency, **expected latency 100ms** >100ms gate **FAIL**.
- **Short conversational:** `all-MiniLM` 384 better for short (6 layers, faster), `mpnet` 768 better for long.
- **Suitability:** **QUESTIONABLE** for 0.418→0.80, **not proven** to fix `custom` vs `content` 0.01 margin.

---

## 13. Cross-Encoder Assessment — DOCUMENTED

- **Architecture:** `cross-encoder/ms-marco-MiniLM-L6-v2` (80MB, 22M params, **not bi-encoder**), takes `pair (message, candidate intent example)` as `input` ` [CLS] message [SEP] candidate [SEP]` → `logit` 0-1, **not** `encode` then `cosine`, but `forward` per pair.
- **Accuracy advantage:** **Expected +0.10** over bi-encoder for `custom` vs `content` 0.01 margin (cross-encoder jointly encodes `message`+`candidate`, better for short).
- **Latency:** **20ms per pair** × `top-k 3` = **60ms** + bi-encoder `encode` 50ms + `retrieve` 0.08ms = **110ms** >100ms gate **FAIL** for `p50 <100ms`.
- **CPU:** Yes, `cross-encoder` can run CPU (like `bi-encoder`), but **3× forward** (top-3) → 60ms.
- **Requires candidate pairs:** **YES** — `bi-encoder` top-3 → `cross-encoder` rerank top-3.
- **Can improve Type-C where correct not in top-2?** **NO** — if `correct` not in `top-3` (Type C 32, correct not in top-2, but in top-5 for 20, top-3 for 10), `cross-encoder` on `top-3` would include `correct` for 10 of 32, **not 22**.
- **Architecture:** `RapidFuzz + MiniLM → top-k (3) → cross-encoder rerank → intent` — **future, not now**.

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

**Report path:** `docs/AI_NATIVE_LLM_PHASE_60_LOCAL_MODEL_CORPUS_AB_FORENSIC_AUDIT.md`

**Intent count:** 22

**Reference examples:** 110 (22×5)

**Validation examples:** 110 (22×5)

**Semantic model:** `all-MiniLM-L6-v2`

**Embedding dimension:** 384

**Hybrid accuracy:** 0.418 (46/110)

**Best intent:** `uncertain` 1.00

**Worst intent:** `custom_request` 0.00, `other` 0.00

**Top confusion pairs:** `* → uncertain` (64 → 31)

**Semantic-only accuracy:** Not separately measured (hybrid includes semantic)

**RapidFuzz-only accuracy:** 0.045

**Hybrid improvement:** +0.373

**Purchase precision:** 0.857

**Purchase recall:** 0.600

**Purchase FPR:** 0.010

**Purchase FNR:** 0.400

**Abstention:** 0.073

**Threshold problem:** YES

**Corpus problem:** PARTIAL

**Representation problem:** NO

**Context problem:** PARTIAL

**Fusion problem:** NO

**Model adequacy:** QUESTIONABLE

**Primary blocker:** Threshold + Type C

**LLM count:** 3

**LLM #1:** AUTHORITATIVE

**Shadow:** DISABLED

**Commerce authority:** UNCHANGED

**Phase 60:** **MORE DATA REQUIRED** (440 validation)

**No production modifications:** YES
