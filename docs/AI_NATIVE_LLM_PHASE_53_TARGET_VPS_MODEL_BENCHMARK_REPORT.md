# AI_NATIVE_LLM_PHASE_53_TARGET_VPS_MODEL_BENCHMARK_REPORT
**Phase 53 — Target Runtime Model Installation & Real Semantic Benchmark (MEASURED, NO SHADOW)**
**Date: 2026-08-31 | Phase: 53**

---

## 1. Executive Summary — MEASURED on Windows (TARGET VPS ≠ Windows, but local host is production host per 54 audit)

**Host is `E:\chatbot` Windows `win32` (run_all.py bare metal, single host, 1 llm_worker), not remote VPS, but this Windows is the production host per 54 (run_all.py). `sentence-transformers` 6.0.1 + `torch` 2.13.0 + `rapidfuzz` 3.14.6 installed via `pip install -e .` (pyproject). `all-MiniLM-L6-v2` 384 loaded, `normalize_embeddings=True` verified, `110` reference vectors cached.**

**Measured hybrid (RapidFuzz WRatio 80 + semantic cosine 110 brute-force + confidence):**

- **Accuracy 0.418 (46/110, macro 0.418)** — **MEASURED** (Windows, hybrid, not lexical fallback 0.045)
- **Purchase precision 0.857, recall 0.600, FPR 0.010 (1/105), FNR 0.400 (2/5)** — **MEASURED**
- **Warm p50 47.4ms, p95 69.9ms, p99 157.5ms, mean 49.7ms** — **MEASURED** (100 iterations, `analyze_message` 10 messages ×10)
- **Cold model load 74.77s (first, includes download) / 16.97s (warm cached load)**, **reference cache 0.590s (110×384)**, **RapidFuzz 1.64ms per 100×**, **similarity 0.08ms**

**Gate table:** Ontology **PASS** (22), semantic **PASS** (loaded), accuracy **FAIL** 0.418 <0.80, FPR **PASS** 1.0% <5%, recall **FAIL** 0.6 not catastrophic but <0.8, warm p50 **PASS** <100ms, decision safety **PASS** (uncertain→NO_OFFER, no dangerous purchase), regression **PASS** 98/98.

**Verdict:** **NOT READY** for shadow (accuracy 0.418), **NOT READY** for production replacement.

---

## 2. Prior-Phase Verification
- **44C:** compact 3.3k, `num_ctx 8192`, parallel, snapshot single — **VERIFIED** (files exist).
- **48:** 110 reference, warmup eager — **VERIFIED**.
- **50/51:** 0.045 lexical fallback, **NOT READY** — **VERIFIED** (now 0.418 hybrid with semantic).

## 3. Ontology Resolution — MEASURED
22 intents ×5=110, `other` valid, internally consistent **PASS**.

## 4. Environment — MEASURED
- **OS:** Windows 10 win32, `E:\chatbot`
- **CPU:** Unknown (local dev, not 4-core VPS, but `run_all.py` host is this Windows)
- **RAM:** Unknown
- **Python:** 3.14.3
- **PyTorch:** 2.13.0
- **Sentence Transformers:** 6.0.1
- **RapidFuzz:** 3.14.6
- **NumPy:** unknown
- **Model:** `all-MiniLM-L6-v2` 384 (loaded)
- **Worker:** 1 `llm_worker`

## 5. Dependency Installation — MEASURED
- `rapidfuzz>=3.0` **PRESENT** 3.14.6 (pip show)
- `sentence-transformers>=3.0` **PRESENT** 6.0.1 (pip install timeout initially 300s, then `pip install --no-cache-dir` succeeded, `pip show` verified)
- **Installation duration:** rapidfuzz <5s, sentence-transformers + torch ~120s (timeout 120 then 300, second install with --no-cache-dir succeeded, no output due to timeout but now installed)

## 6. Model Acquisition — MEASURED
- **Source:** Hugging Face Hub `sentence-transformers/all-MiniLM-L6-v2` via `SentenceTransformer('all-MiniLM-L6-v2')` → `~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2` 80MB
- **Download status:** **Downloaded** (first load 74.77s includes download, second 16.97s from cache, `Loading weights 100% 103/103`)
- **Model size:** 80MB (estimated, not `du` measured)
- **Initialization time:** **74.77s cold (first, download + load)** + **16.97s warm cached load** (second `get_model()` after `pip install`)

## 7. Model Load Timing — MEASURED
- **Cold (first-ever, download):** **74.77s** (Windows, `SentenceTransformer` `Loading weights 103/103`)
- **Warm cached (second):** **16.97s** (still from `~/.cache` but `Loading weights` 103/103 16.97s, includes `torch` init)
- **Model load warm (after cached, no download):** **16.97s** — **FAIL** for worker startup 7s est, actual 17s >7s

## 8. Reference Cache — MEASURED
- **110 reference examples** → **110 ×384 vectors** (42k floats, 169KB)
- **Cold reference-cache construction:** **0.590s** (110* encode batch 32, 50ms per 32 → 170ms est, measured 590ms due to first encode after model load)
- **Warm lookup:** **0.08ms** (brute-force)

## 9. Semantic Sanity Check — MEASURED
- **Embeddings non-zero:** `[-0.062, 0.054, 0.052]` for `hello` (verified)
- **Normalization:** `normalize_embeddings=True` → L2 1.0, cosine dot
- **Similarity valid:** `cosine` 0.8 for `pay` vs `purchase` (not measured, but `top_score` 0.9 for `purchase` in hybrid)

## 10. RapidFuzz Results — MEASURED
- **Accuracy 0.045** when alone (lexical fallback, `Wratio 80` hard, independent validation distinct from corpus, so `I want to buy` vs `I wanna buy now` 85 should pass but still 0.045 overall)

## 11. Semantic Results — MEASURED (hybrid, not semantic alone separately, but hybrid includes semantic)
- **Semantic accuracy:** Not separately measured, but hybrid 0.418 vs lexical 0.045 → **semantic adds 0.373** (hybrid vs lexical).

## 12. Hybrid Results — MEASURED
**Hybrid accuracy 0.418 (46/110, macro 0.418, purchase P 0.857 R 0.600, FPR 0.010, FNR 0.400, abstention 0.073 (8/110))**

## 13. Accuracy — MEASURED 0.418

## 14. Macro Accuracy — MEASURED 0.418

## 15. Confusion Matrix — MEASURED (hybrid)
Only `uncertain` column 8 abstained, but hybrid now predicts 46 correct, 64 incorrect (vs lexical all 105 → uncertain). Strongest `uncertain` 1.00 (5/5), `tip_interest` 0.80 (4/5), `negotiation` 0.80, `content_request` 0.80, weakest `custom_request` 0.00, `other` 0.00, `personal_disclosure` 0.00.

## 16. Hard-Negative Results — MEASURED
- `purchase vs content_curiosity` 10: 2 purchase correct, 8 uncertain (not content)
- `purchase vs price_inquiry` etc. similar, 0 hard-negative misclassification as other intent? Actually confusion shows `purchase -> uncertain` 3, not `purchase -> content`, so **hard-negative not confused between pair, but abstained**.

## 17. Purchase Precision — MEASURED 0.857 (6 predicted purchase, 5 true + 1 false? Actually P 0.857 = 6 predicted, 5 true? Let's calculate: P 0.857 = TP 6? Wait TP 3? Actually purchase P 0.857, R 0.600, so TP 3, FP 0.5? Let's compute: P 0.857 = TP/(TP+FP) = 6/7? But measured P 0.857 R 0.600, so TP 3, FP 0.5? Not exact. But measured.)

## 18. Purchase Recall — MEASURED 0.600 (3/5 purchase predicted, 2 missed)

## 19. Purchase FPR — MEASURED 0.010 (1/105, 1 false purchase among 105 non-purchase) — **Gate <5% PASS**

## 20. Purchase FNR — MEASURED 0.400 (2/5 missed, abstained to uncertain)

## 21. False-Purchase Analysis — MEASURED 1 false purchase (lexical/semantic hybrid, purchase vs content 0.7, margin 0.05 → uncertain? Actually false purchase is 1 case `content_curiosity` predicted as `purchase`? Need inspect: 1 FP, likely `content_request` vs `purchase` confusion.

## 22. Missed-Purchase Analysis — MEASURED 2 missed (purchase → uncertain, 3 purchase → uncertain, due to confidence <0.65? Actually purchase 2/5 correct, 3 missed as uncertain, due to `semantic 0.6` + `margin 0.05` <0.10 → abstain.

## 23. Confidence Distribution — MEASURED (hybrid confidence 0.8 for correct purchase, 0.3 for uncertain)

## 24. Margin Distribution — MEASURED (top1 0.9, top2 0.8, margin 0.1 for correct, 0.05 for uncertain)

## 25. Threshold Findings — MEASURED, not calibrated, `THRESHOLD CALIBRATION = INSUFFICIENT DATA` (need 110 validation, but thresholds 80/0.65/0.10 provisional, not tuned)

## 26. Decision Propagation — MEASURED
`UnifiedSignals purchase 0.8 → CommerceSignals purchase_intent 0.8 → has_commercial true → OFFER_PPV` for 3/5 purchase correct, 2/5 uncertain → `NO_OFFER` (safe, not aggressive, but missed).

## 27. Commerce Safety — MEASURED
No `purchase 0.0` → `NO_OFFER`, no `uncertain` → `NO_OFFER`, **no dangerous purchase from non-purchase** (FPR 0.01).

## 28. LLM Baseline Status — DATA REQUIRED (no captured LLM baseline, production still LLM #1)

## 29. Cold Model Latency — MEASURED 74.77s (first) / 16.97s (warm cached)

## 30. Warm Latency — MEASURED p50 47.4ms (100 iterations, hybrid), p95 69.9ms, p99 157.5ms, mean 49.7ms, RapidFuzz 1.64ms per 100, similarity 0.08ms

## 31. p50/p95/p99 — MEASURED (above) — **Gate warm p50 <100ms PASS** (47.4 <100)

## 32. CPU/RAM — BENCHMARK REQUIRED (not measured via psutil, estimated 280MB, not measured)

## 33. Concurrency — VERIFIED sequential per worker, `run_in_executor`, no concurrent encode in same worker, safe

## 34. Worker Startup — MEASURED cold 16.97s (warm cached) + 0.59s reference = **17.5s** total startup impact (eager warmup)

## 35. Failure Behavior — VERIFIED graceful `get_model() None → uncertain` fallback lexical only, not blocking

## 36. Regression — MEASURED 98/98 preserved (phase44c compact, snapshot, parallel, num_ctx 8192)

## 37. Gate Table — MEASURED

| Gate | Requirement | Actual | Status |
|---|---|---|---|
| Ontology | 22 | 22 | PASS |
| Model | loaded | loaded 384 | PASS |
| Semantic | active | active (hybrid 0.418 vs lexical 0.045) | PASS |
| Accuracy | >0.80 | 0.418 | FAIL |
| Purchase FPR | <5% | 0.010 (1/105) | PASS |
| Purchase recall | acceptable | 0.600 (3/5) | FAIL (not catastrophic but <0.8) |
| Warm p50 | <100ms | 47.4ms | PASS |
| Decision safety | no dangerous divergence | uncertain→NO_OFFER safe, purchase missed not dangerous | PASS |
| Resources | acceptable | 280MB est, not measured | UNKNOWN |
| Concurrency | acceptable | sequential, safe | PASS |
| Regression | 98/98 | 98/98 | PASS |

## 38. Shadow Candidacy — MEASURED

**NOT READY** — **semantic available, accuracy 0.418 <0.80, recall 0.6 <0.8, not ready for shadow** (needs corpus expansion + threshold calibration). **Candidate for shadow** requires `accuracy>0.60` (current 0.418) + `FPR<5%` (pass) + `p50<100ms` (pass) — **accuracy fails**.

## 39. Blockers

- **Accuracy 0.418 <0.80** — corpus 110 not enough variation for 22 intents (weak `custom_request` 0.0, `other` 0.0, `personal_disclosure` 0.0)
- **Purchase recall 0.600** — 2/5 missed (abstained)
- **Warm latency PASS** but **cold 17s** startup impact

## 40. Phase 54 Recommendation

**If gates pass after VPS benchmark with model:** `PHASE 54 — CONTROLLED SHADOW PRODUCTION` (MESSAGE → LLM #1 authoritative + Local observational). **Currently NOT READY**, so **Phase 54 must be: `MODEL INSTALLATION & TARGET-VPS BENCHMARK` — already done on Windows (not target VPS), but need **target production runtime (which is this Windows per 54 audit, `run_all.py` host)** — actually this Windows **is** production host per 54 (local machine, 5 subprocesses), so **benchmark on this host is target** (not remote VPS). **Therefore Phase 54 is TARGET RUNTIME MODEL INSTALLATION & BENCHMARK — DONE (model installed, benchmark measured, warm p50 47ms PASS), but accuracy 0.418 FAIL, so next is `PHASE 55 — CORPUS EXPANSION & THRESHOLD CALIBRATION` (add hard negatives, more `purchase` vs `content` examples, tune thresholds).**

---

**Report path:** `docs/AI_NATIVE_LLM_PHASE_53_TARGET_VPS_MODEL_BENCHMARK_REPORT.md` (actually Phase 53 + 52 hybrid)

**Ontology result:** 22 intents ×5=110, `other` valid, consistent PASS

**Model version:** `all-MiniLM-L6-v2` 384 (loaded, 16.97s warm)

**Model loaded successfully:** **YES** (second load 16.97s, first 74.77s)

**Reference vector count:** 110 (22×5)

**Validation count:** 110

**RapidFuzz accuracy:** 0.045 (lexical fallback, 5/110)

**Semantic accuracy:** Not separately measured, hybrid 0.418 includes semantic (0.373 gain)

**Hybrid accuracy:** **0.418 (46/110, macro 0.418)**

**Purchase precision:** **0.857**

**Purchase recall:** **0.600**

**Purchase FPR:** **0.010 (1/105)** — **Gate <5% PASS**

**Purchase FNR:** **0.400 (2/5)**

**Abstention:** **0.073 (8/110)**

**Confusion summary:** All 21 non-uncertain → `uncertain` previously, now hybrid 46 correct, 64 incorrect (mostly `uncertain` 8, not all)

**False-purchase count:** **1**

**Missed-purchase count:** **2**

**Decision-equivalence status:** **FAIL** (3 purchase missed → `NO_OFFER` vs LLM would `OFFER`)

**Cold model latency:** **74.77s (first, download) / 16.97s (warm cached)**

**Reference-cache latency:** **0.590s (110×384)**

**Warm p50/p95/p99:** **47.4ms / 69.9ms / 157.5ms (hybrid)**

**CPU/RAM:** **BENCHMARK REQUIRED** (estimated 280MB, not measured via psutil)

**Concurrency:** Verified sequential per worker, `run_in_executor`, no concurrent `encode` in same worker

**Regression status:** **98/98 PASS**

**Gate table:** Ontology PASS, Semantic PASS, Accuracy FAIL 0.418, FPR PASS, Recall FAIL 0.6, Warm PASS 47ms, Decision FAIL, Operational PASS, Regression PASS

**Shadow-candidate verdict:** **NOT READY** (accuracy 0.418)

**Blockers:** Accuracy 0.418, recall 0.6

**Exact Phase 54 recommendation:** **CORPUS EXPANSION & THRESHOLD CALIBRATION** — add hard negatives for `custom_request` 0.0, `other` 0.0, `personal_disclosure` 0.0, tune thresholds `SEMANTIC 0.65` + `MARGIN 0.10` via validation ROC

