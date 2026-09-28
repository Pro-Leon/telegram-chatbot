"""Phase 67 — 440-Case Failure Forensics + Local Model A/B
Temporary diagnostic script. Produces JSON output for the Phase 67 report.

NOT part of production. Does not modify any production code.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from typing import Any

# ─── CONFIG ───────────────────────────────────────────────────────────
SEMANTIC_THRESHOLD = 0.50
MARGIN_THRESHOLD = 0.00
LEXICAL_CUTOFF = 80
MINILM_NAME = "all-MiniLM-L6-v2"
MPNET_NAME = "all-mpnet-base-v2"
WARMUP_ROUNDS = 5
BENCH_ROUNDS = 100

# ─── HELPERS ──────────────────────────────────────────────────────────
def normalize_message(text: str) -> str:
    if not text:
        return ""
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot


def _f1(precision, recall):
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _per_intent_metrics(predictions, ground_truth, intents):
    """Compute per-intent precision, recall, F1, accuracy."""
    results = {}
    for intent in intents:
        tp = sum(1 for p, g in zip(predictions, ground_truth) if p == intent and g == intent)
        fp = sum(1 for p, g in zip(predictions, ground_truth) if p == intent and g != intent)
        fn = sum(1 for p, g in zip(predictions, ground_truth) if p != intent and g == intent)
        n = sum(1 for g in ground_truth if g == intent)
        correct = tp
        accuracy = correct / n if n else 0.0
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = _f1(precision, recall)
        results[intent] = {
            "n": n,
            "accuracy": round(accuracy, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }
    return results


def _confusion_matrix(predictions, ground_truth, intents):
    cm = {}
    for g in intents:
        cm[g] = {}
        for p in intents:
            cm[g][p] = 0
    for p, g in zip(predictions, ground_truth):
        cm[g][p] += 1
    return cm


# ─── EVALUATION ENGINE ────────────────────────────────────────────────
async def evaluate_model(model_name, dataset, ref_texts, ref_intents, mode="hybrid"):
    """
    Evaluate a single model on the dataset.
    mode: 'hybrid', 'semantic_only', 'lexical_only'
    """
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    try:
        dim = model.get_embedding_dimension()
    except AttributeError:
        dim = model.get_sentence_embedding_dimension()

    # Encode reference corpus
    ref_vecs = model.encode(ref_texts, normalize_embeddings=True, show_progress_bar=False, batch_size=32)
    if hasattr(ref_vecs, "tolist"):
        ref_vecs = ref_vecs.tolist()

    # Results storage
    results = []
    latencies = []

    for ex in dataset:
        text = ex["text"]
        true_intent = ex["intent"]
        norm = normalize_message(text)

        t0 = time.monotonic()

        # ── Lexical (RapidFuzz) ──
        lex_score = 0.0
        lex_intent = "uncertain"
        lex_top3 = []
        lex_latency = 0.0

        if mode in ("hybrid", "lexical_only"):
            t_lex = time.monotonic()
            try:
                from rapidfuzz import fuzz, process
                res = process.extract(
                    norm,
                    [normalize_message(t) for t in ref_texts],
                    scorer=fuzz.WRatio,
                    score_cutoff=0,
                    limit=10,
                )
                for matched, score, idx in res:
                    lex_top3.append({
                        "text": ref_texts[idx],
                        "intent": ref_intents[idx],
                        "score": float(score),
                    })
                if lex_top3:
                    lex_score = lex_top3[0]["score"] / 100.0
                    lex_intent = lex_top3[0]["intent"]
            except Exception:
                pass
            lex_latency = (time.monotonic() - t_lex) * 1000

        # ── Semantic (SentenceTransformer) ──
        sem_score = 0.0
        sem_intent = "uncertain"
        sem_top3 = []
        sem_latency = 0.0
        top_score = 0.0
        second_score = 0.0

        if mode in ("hybrid", "semantic_only"):
            t_sem = time.monotonic()
            vec = model.encode([text], normalize_embeddings=True, show_progress_bar=False)
            if hasattr(vec, "tolist"):
                vec = vec[0].tolist()
            else:
                vec = list(vec[0])
            # Brute-force cosine
            scores = []
            for idx, ref_vec in enumerate(ref_vecs):
                s = _cosine(vec, ref_vec)
                scores.append((s, idx))
            scores.sort(key=lambda x: x[0], reverse=True)
            if scores:
                top_score = scores[0][0]
                sem_intent = ref_intents[scores[0][1]]
                sem_score = top_score
                if len(scores) > 1:
                    second_score = scores[1][0]
                for s, idx in scores[:3]:
                    sem_top3.append({
                        "text": ref_texts[idx],
                        "intent": ref_intents[idx],
                        "score": float(s),
                    })
            sem_latency = (time.monotonic() - t_sem) * 1000

        total_latency = (time.monotonic() - t0) * 1000
        latencies.append(total_latency)

        # ── Fusion logic (same as unified_intelligence.py) ──
        top2_intents = []
        if mode == "lexical_only":
            primary_intent = lex_intent if lex_score * 100 >= LEXICAL_CUTOFF else "uncertain"
            top_score_val = lex_score
            second_score_val = 0.0
            margin_val = lex_score
            confidence = lex_score
            uncertainty = primary_intent == "uncertain"
            top2_intents = [s["intent"] for s in lex_top3[:2]]
        elif mode == "semantic_only":
            margin_val = top_score - second_score if top_score and second_score else top_score
            confidence = top_score
            if margin_val < MARGIN_THRESHOLD:
                confidence *= 0.7
            primary_intent = sem_intent if top_score >= SEMANTIC_THRESHOLD and confidence >= 0.5 else "uncertain"
            if confidence < 0.4 or (top_score < SEMANTIC_THRESHOLD and 0.0 < 0.8):
                primary_intent = "uncertain"
                uncertainty = True
            else:
                uncertainty = False
            top_score_val = top_score
            second_score_val = second_score
            top2_intents = [s["intent"] for s in sem_top3[:2]]
        else:  # hybrid
            lex_max = lex_score
            sem_max = top_score
            combined = max(lex_max, sem_max)
            margin_val = top_score - second_score if top_score and second_score else top_score
            confidence = combined
            if margin_val < MARGIN_THRESHOLD:
                confidence *= 0.7
            if lex_score == 0.0 and top_score == 0.0:
                confidence = 0.0
            primary_intent = sem_intent if top_score >= SEMANTIC_THRESHOLD and confidence >= 0.5 else "uncertain"
            if confidence < 0.4 or (top_score < SEMANTIC_THRESHOLD and lex_max < 0.8):
                primary_intent = "uncertain"
                uncertainty = True
            else:
                uncertainty = False
            top_score_val = combined
            second_score_val = second_score
            top2_intents = [s["intent"] for s in sem_top3[:2]]

        # ── Failure classification ──
        error_type = None
        if primary_intent != true_intent:
            if true_intent == (top2_intents[0] if top2_intents else None):
                error_type = "A"
            elif true_intent in top2_intents:
                error_type = "B"
            else:
                # Check if correct intent is in top-10 of semantic scores
                found_in_top10 = False
                if mode != "lexical_only":
                    for s_item in sem_top3:
                        if s_item["intent"] == true_intent:
                            found_in_top10 = True
                            break
                if found_in_top10:
                    error_type = "C"
                else:
                    error_type = "D"

        results.append({
            "example_id": ex.get("example_id", 0),
            "text": text[:100],
            "true_intent": true_intent,
            "predicted_intent": primary_intent,
            "top_score": round(top_score_val, 4),
            "second_score": round(second_score_val, 4),
            "margin": round(margin_val, 4),
            "lex_score": round(lex_score, 4),
            "sem_score": round(sem_score, 4),
            "abstained": uncertainty,
            "correct": primary_intent == true_intent,
            "error_type": error_type,
            "top2_intents": top2_intents[:2],
            "latency_ms": round(total_latency, 2),
        })

    return results, latencies, dim


def compute_purchase_metrics(results):
    """Compute purchase-specific safety metrics."""
    purchase_intents = {"purchase_intent", "repeat_purchase_intent"}
    tp = fp = fn = tn = 0
    false_purchases = []
    missed_purchases = []

    for r in results:
        is_true_purchase = r["true_intent"] in purchase_intents
        is_pred_purchase = r["predicted_intent"] in purchase_intents and not r["abstained"]

        if is_true_purchase and is_pred_purchase:
            tp += 1
        elif not is_true_purchase and is_pred_purchase:
            fp += 1
            false_purchases.append(r)
        elif is_true_purchase and not is_pred_purchase:
            fn += 1
            missed_purchases.append(r)
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    fnr = fn / (tp + fn) if (tp + fn) else 0.0

    return {
        "true_purchase": tp + fn,
        "predicted_purchase": tp + fp,
        "false_purchase": fp,
        "missed_purchase": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "fpr": round(fpr, 4),
        "fnr": round(fnr, 4),
        "false_purchases": false_purchases,
        "missed_purchases": missed_purchases,
    }


def compute_overall_metrics(results):
    """Compute overall accuracy, macro P/R/F1."""
    intents = sorted(set(r["true_intent"] for r in results))
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    accuracy = correct / total if total else 0.0

    # Per-intent
    per_intent = _per_intent_metrics(
        [r["predicted_intent"] for r in results],
        [r["true_intent"] for r in results],
        intents,
    )

    # Macro
    macro_precision = statistics.mean(m["precision"] for m in per_intent.values())
    macro_recall = statistics.mean(m["recall"] for m in per_intent.values())
    macro_f1 = statistics.mean(m["f1"] for m in per_intent.values())

    # Abstention
    abstained = sum(1 for r in results if r["abstained"])
    abstention_rate = abstained / total if total else 0.0

    # Score stats
    correct_scores = [r["top_score"] for r in results if r["correct"]]
    incorrect_scores = [r["top_score"] for r in results if not r["correct"]]
    margins = [r["margin"] for r in results]

    # Failure type counts
    error_counts = Counter(r["error_type"] for r in results if r["error_type"])

    return {
        "total": total,
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "macro_precision": round(macro_precision, 4),
        "macro_recall": round(macro_recall, 4),
        "macro_f1": round(macro_f1, 4),
        "abstention_rate": round(abstention_rate, 4),
        "abstained_count": abstained,
        "per_intent": per_intent,
        "error_type_counts": dict(error_counts),
        "score_stats": {
            "correct_top1_mean": round(statistics.mean(correct_scores), 4) if correct_scores else 0,
            "correct_top1_median": round(statistics.median(correct_scores), 4) if correct_scores else 0,
            "incorrect_top1_mean": round(statistics.mean(incorrect_scores), 4) if incorrect_scores else 0,
            "incorrect_top1_median": round(statistics.median(incorrect_scores), 4) if incorrect_scores else 0,
            "margin_mean": round(statistics.mean(margins), 4),
            "margin_median": round(statistics.median(margins), 4),
        },
    }


def benchmark_model(model_name, ref_texts):
    """Benchmark model load, cache construction, and inference latency."""
    import gc

    # ── Cold load ──
    gc.collect()
    t0 = time.monotonic()
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)
    cold_load = (time.monotonic() - t0) * 1000

    try:
        dim = model.get_embedding_dimension()
    except AttributeError:
        dim = model.get_sentence_embedding_dimension()

    # ── Cached load (second import, model already in memory) ──
    t0 = time.monotonic()
    model2 = SentenceTransformer(model_name)
    cached_load = (time.monotonic() - t0) * 1000

    # ── Reference cache construction ──
    t0 = time.monotonic()
    ref_vecs = model.encode(ref_texts, normalize_embeddings=True, show_progress_bar=False, batch_size=32)
    cache_time = (time.monotonic() - t0) * 1000

    # ── Warm encode latency (single message) ──
    # Warmup
    for _ in range(WARMUP_ROUNDS):
        model.encode(["test message"], normalize_embeddings=True, show_progress_bar=False)

    encode_latencies = []
    for _ in range(BENCH_ROUNDS):
        t0 = time.monotonic()
        model.encode(["hey, how are you doing today?"], normalize_embeddings=True, show_progress_bar=False)
        encode_latencies.append((time.monotonic() - t0) * 1000)

    # ── Warm end-to-end (encode + brute-force similarity) ──
    e2e_latencies = []
    test_msg = model.encode(["hey, how are you doing today?"], normalize_embeddings=True, show_progress_bar=False)
    if hasattr(test_msg, "tolist"):
        test_msg = test_msg[0].tolist()
    else:
        test_msg = list(test_msg[0])

    for _ in range(BENCH_ROUNDS):
        t0 = time.monotonic()
        vec = model.encode(["hey, how are you doing today?"], normalize_embeddings=True, show_progress_bar=False)
        if hasattr(vec, "tolist"):
            vec = vec[0].tolist()
        else:
            vec = list(vec[0])
        for ref_vec in ref_vecs:
            _cosine(vec, ref_vec)
        e2e_latencies.append((time.monotonic() - t0) * 1000)

    encode_latencies.sort()
    e2e_latencies.sort()

    def percentile(data, p):
        k = (len(data) - 1) * p / 100
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return data[int(k)]
        return data[f] * (c - k) + data[c] * (k - f)

    # ── RAM estimate ──
    try:
        import psutil
        process = psutil.Process(os.getpid())
        ram_mb = process.memory_info().rss / (1024 * 1024)
    except ImportError:
        ram_mb = -1

    return {
        "model_name": model_name,
        "dimension": dim,
        "cold_load_ms": round(cold_load, 2),
        "cached_load_ms": round(cached_load, 2),
        "reference_cache_ms": round(cache_time, 2),
        "reference_count": len(ref_texts),
        "encode_p50_ms": round(percentile(encode_latencies, 50), 4),
        "encode_p95_ms": round(percentile(encode_latencies, 95), 4),
        "encode_p99_ms": round(percentile(encode_latencies, 99), 4),
        "encode_mean_ms": round(statistics.mean(encode_latencies), 4),
        "e2e_p50_ms": round(percentile(e2e_latencies, 50), 4),
        "e2e_p95_ms": round(percentile(e2e_latencies, 95), 4),
        "e2e_p99_ms": round(percentile(e2e_latencies, 99), 4),
        "e2e_mean_ms": round(statistics.mean(e2e_latencies), 4),
        "ram_mb": round(ram_mb, 2) if ram_mb > 0 else "unknown",
    }


# ─── MAIN ─────────────────────────────────────────────────────────────
async def main():
    output: dict[str, Any] = {}

    # ── 1. DATASET INTEGRITY ──
    print("=== STEP 1: Dataset Integrity ===", file=sys.stderr)
    from commerce.intent_corpus import INTENT_CORPUS
    from commerce.validation_dataset_440 import VALIDATION_440

    ref_texts = [e["example_text"] for e in INTENT_CORPUS]
    ref_intents = [e["intent"] for e in INTENT_CORPUS]
    val_texts = [e["text"] for e in VALIDATION_440]
    val_intents = [e["intent"] for e in VALIDATION_440]

    ref_text_set = set(t.lower().strip() for t in ref_texts)
    val_text_set = set(t.lower().strip() for t in val_texts)
    overlap = ref_text_set & val_text_set

    ref_id_set = set(e["example_id"] for e in INTENT_CORPUS)
    val_id_set = set(e["example_id"] for e in VALIDATION_440)

    output["integrity"] = {
        "reference_count": len(ref_texts),
        "reference_intents": len(set(ref_intents)),
        "reference_per_intent": dict(Counter(ref_intents)),
        "reference_duplicates": len(ref_texts) - len(set(t.lower().strip() for t in ref_texts)),
        "validation_count": len(val_texts),
        "validation_intents": len(set(val_intents)),
        "validation_per_intent": dict(Counter(val_intents)),
        "validation_duplicates": len(val_texts) - len(set(t.lower().strip() for t in val_texts)),
        "text_overlap": len(overlap),
        "overlap_examples": list(overlap)[:5],
        "reference_id_duplicates": len(ref_texts) - len(ref_id_set),
        "validation_id_duplicates": len(val_texts) - len(val_id_set),
    }

    print(f"  Reference: {len(ref_texts)} examples, {len(set(ref_intents))} intents", file=sys.stderr)
    print(f"  Validation: {len(val_texts)} examples, {len(set(val_intents))} intents", file=sys.stderr)
    print(f"  Text overlap: {len(overlap)}", file=sys.stderr)

    # ── 2. MINILM THREE-WAY DECOMPOSITION ──
    print("\n=== STEP 2: MiniLM Three-Way Decomposition ===", file=sys.stderr)

    print("  Running hybrid...", file=sys.stderr)
    hybrid_results, hybrid_latencies, minilm_dim = await evaluate_model(
        MINILM_NAME, VALIDATION_440, ref_texts, ref_intents, mode="hybrid"
    )
    hybrid_metrics = compute_overall_metrics(hybrid_results)
    hybrid_purchase = compute_purchase_metrics(hybrid_results)

    print("  Running semantic-only...", file=sys.stderr)
    sem_results, sem_latencies, _ = await evaluate_model(
        MINILM_NAME, VALIDATION_440, ref_texts, ref_intents, mode="semantic_only"
    )
    sem_metrics = compute_overall_metrics(sem_results)
    sem_purchase = compute_purchase_metrics(sem_results)

    print("  Running lexical-only...", file=sys.stderr)
    lex_results, lex_latencies, _ = await evaluate_model(
        MINILM_NAME, VALIDATION_440, ref_texts, ref_intents, mode="lexical_only"
    )
    lex_metrics = compute_overall_metrics(lex_results)
    lex_purchase = compute_purchase_metrics(lex_results)

    output["minilm_decomposition"] = {
        "dimension": minilm_dim,
        "hybrid": {
            "metrics": hybrid_metrics,
            "purchase": {k: v for k, v in hybrid_purchase.items() if k not in ("false_purchases", "missed_purchases")},
            "false_purchases": hybrid_purchase["false_purchases"],
            "missed_purchases": hybrid_purchase["missed_purchases"],
        },
        "semantic_only": {
            "metrics": sem_metrics,
            "purchase": {k: v for k, v in sem_purchase.items() if k not in ("false_purchases", "missed_purchases")},
        },
        "lexical_only": {
            "metrics": lex_metrics,
            "purchase": {k: v for k, v in lex_purchase.items() if k not in ("false_purchases", "missed_purchases")},
        },
    }

    print(f"  Hybrid accuracy: {hybrid_metrics['accuracy']}", file=sys.stderr)
    print(f"  Semantic-only accuracy: {sem_metrics['accuracy']}", file=sys.stderr)
    print(f"  Lexical-only accuracy: {lex_metrics['accuracy']}", file=sys.stderr)

    # ── 3. FAILURE TAXONOMY (from hybrid) ──
    print("\n=== STEP 3: Failure Taxonomy ===", file=sys.stderr)
    failures = [r for r in hybrid_results if not r["correct"]]
    error_counts = Counter(r["error_type"] for r in failures)
    total_failures = len(failures)

    output["failure_taxonomy"] = {
        "total_failures": total_failures,
        "type_a_count": error_counts.get("A", 0),
        "type_a_pct": round(error_counts.get("A", 0) / total_failures * 100, 2) if total_failures else 0,
        "type_b_count": error_counts.get("B", 0),
        "type_b_pct": round(error_counts.get("B", 0) / total_failures * 100, 2) if total_failures else 0,
        "type_c_count": error_counts.get("C", 0),
        "type_c_pct": round(error_counts.get("C", 0) / total_failures * 100, 2) if total_failures else 0,
        "type_d_count": error_counts.get("D", 0),
        "type_d_pct": round(error_counts.get("D", 0) / total_failures * 100, 2) if total_failures else 0,
        "type_a_examples": [r for r in hybrid_results if r["error_type"] == "A"][:5],
        "type_b_examples": [r for r in hybrid_results if r["error_type"] == "B"][:5],
        "type_c_examples": [r for r in hybrid_results if r["error_type"] == "C"][:5],
        "type_d_examples": [r for r in hybrid_results if r["error_type"] == "D"][:5],
    }

    print(f"  Type A (threshold): {error_counts.get('A', 0)}", file=sys.stderr)
    print(f"  Type B (margin): {error_counts.get('B', 0)}", file=sys.stderr)
    print(f"  Type C (representation): {error_counts.get('C', 0)}", file=sys.stderr)
    print(f"  Type D (misclassification): {error_counts.get('D', 0)}", file=sys.stderr)

    # ── 4. PURCHASE SAFETY DEEP DIVE ──
    print("\n=== STEP 4: Purchase Safety ===", file=sys.stderr)
    output["purchase_safety"] = {
        "hybrid": {
            "true_purchase_cases": hybrid_purchase["true_purchase"],
            "predicted_purchase_cases": hybrid_purchase["predicted_purchase"],
            "false_purchase_cases": hybrid_purchase["false_purchase"],
            "missed_purchase_cases": hybrid_purchase["missed_purchase"],
            "precision": hybrid_purchase["precision"],
            "recall": hybrid_purchase["recall"],
            "fpr": hybrid_purchase["fpr"],
            "fnr": hybrid_purchase["fnr"],
            "false_purchase_details": [
                {"example_id": r["example_id"], "text": r["text"], "true_intent": r["true_intent"], "predicted": r["predicted_intent"], "top_score": r["top_score"]}
                for r in hybrid_purchase["false_purchases"]
            ],
            "missed_purchase_details": [
                {"example_id": r["example_id"], "text": r["text"], "predicted": r["predicted_intent"], "top_score": r["top_score"], "sem_score": r["sem_score"], "lex_score": r["lex_score"]}
                for r in hybrid_purchase["missed_purchases"]
            ],
        }
    }

    # ── 5. MINILM BENCHMARK ──
    print("\n=== STEP 5: MiniLM Benchmark ===", file=sys.stderr)
    minilm_bench = benchmark_model(MINILM_NAME, ref_texts)
    output["minilm_benchmark"] = minilm_bench
    print(f"  Cold load: {minilm_bench['cold_load_ms']}ms", file=sys.stderr)
    print(f"  Encode p50: {minilm_bench['encode_p50_ms']}ms", file=sys.stderr)
    print(f"  E2E p50: {minilm_bench['e2e_p50_ms']}ms", file=sys.stderr)

    # ── 6. MPNET EVALUATION ──
    print("\n=== STEP 6: MPNet Evaluation ===", file=sys.stderr)
    print("  Running MPNet hybrid...", file=sys.stderr)
    mpnet_hybrid_results, mpnet_hybrid_latencies, mpnet_dim = await evaluate_model(
        MPNET_NAME, VALIDATION_440, ref_texts, ref_intents, mode="hybrid"
    )
    mpnet_hybrid_metrics = compute_overall_metrics(mpnet_hybrid_results)
    mpnet_hybrid_purchase = compute_purchase_metrics(mpnet_hybrid_results)

    print("  Running MPNet semantic-only...", file=sys.stderr)
    mpnet_sem_results, _, _ = await evaluate_model(
        MPNET_NAME, VALIDATION_440, ref_texts, ref_intents, mode="semantic_only"
    )
    mpnet_sem_metrics = compute_overall_metrics(mpnet_sem_results)

    print("  Running MPNet lexical-only...", file=sys.stderr)
    mpnet_lex_results, _, _ = await evaluate_model(
        MPNET_NAME, VALIDATION_440, ref_texts, ref_intents, mode="lexical_only"
    )
    mpnet_lex_metrics = compute_overall_metrics(mpnet_lex_results)

    output["mpnet_evaluation"] = {
        "dimension": mpnet_dim,
        "hybrid": {
            "metrics": mpnet_hybrid_metrics,
            "purchase": {k: v for k, v in mpnet_hybrid_purchase.items() if k not in ("false_purchases", "missed_purchases")},
            "false_purchases": mpnet_hybrid_purchase["false_purchases"],
            "missed_purchases": mpnet_hybrid_purchase["missed_purchases"],
        },
        "semantic_only": {"metrics": mpnet_sem_metrics},
        "lexical_only": {"metrics": mpnet_lex_metrics},
    }

    print(f"  MPNet hybrid accuracy: {mpnet_hybrid_metrics['accuracy']}", file=sys.stderr)
    print(f"  MPNet semantic-only accuracy: {mpnet_sem_metrics['accuracy']}", file=sys.stderr)

    # ── 7. MPNET BENCHMARK ──
    print("\n=== STEP 7: MPNet Benchmark ===", file=sys.stderr)
    mpnet_bench = benchmark_model(MPNET_NAME, ref_texts)
    output["mpnet_benchmark"] = mpnet_bench
    print(f"  Cold load: {mpnet_bench['cold_load_ms']}ms", file=sys.stderr)
    print(f"  Encode p50: {mpnet_bench['encode_p50_ms']}ms", file=sys.stderr)

    # ── 8. MINILM VS MPNET COMPARISON ──
    print("\n=== STEP 8: A/B Comparison ===", file=sys.stderr)
    output["ab_comparison"] = {
        "minilm": {
            "dimension": minilm_dim,
            "hybrid_accuracy": hybrid_metrics["accuracy"],
            "semantic_accuracy": sem_metrics["accuracy"],
            "lexical_accuracy": lex_metrics["accuracy"],
            "macro_f1": hybrid_metrics["macro_f1"],
            "abstention": hybrid_metrics["abstention_rate"],
            "purchase_fpr": hybrid_purchase["fpr"],
            "purchase_recall": hybrid_purchase["recall"],
            "cold_load_ms": minilm_bench["cold_load_ms"],
            "encode_p50_ms": minilm_bench["encode_p50_ms"],
            "e2e_p50_ms": minilm_bench["e2e_p50_ms"],
            "reference_cache_ms": minilm_bench["reference_cache_ms"],
            "ram_mb": minilm_bench["ram_mb"],
        },
        "mpnet": {
            "dimension": mpnet_dim,
            "hybrid_accuracy": mpnet_hybrid_metrics["accuracy"],
            "semantic_accuracy": mpnet_sem_metrics["accuracy"],
            "lexical_accuracy": mpnet_lex_metrics["accuracy"],
            "macro_f1": mpnet_hybrid_metrics["macro_f1"],
            "abstention": mpnet_hybrid_metrics["abstention_rate"],
            "purchase_fpr": mpnet_hybrid_purchase["fpr"],
            "purchase_recall": mpnet_hybrid_purchase["recall"],
            "cold_load_ms": mpnet_bench["cold_load_ms"],
            "encode_p50_ms": mpnet_bench["encode_p50_ms"],
            "e2e_p50_ms": mpnet_bench["e2e_p50_ms"],
            "reference_cache_ms": mpnet_bench["reference_cache_ms"],
            "ram_mb": mpnet_bench["ram_mb"],
        },
        "accuracy_delta": round(mpnet_hybrid_metrics["accuracy"] - hybrid_metrics["accuracy"], 4),
        "f1_delta": round(mpnet_hybrid_metrics["macro_f1"] - hybrid_metrics["macro_f1"], 4),
        "encode_latency_ratio": round(mpnet_bench["encode_p50_ms"] / minilm_bench["encode_p50_ms"], 2) if minilm_bench["encode_p50_ms"] > 0 else "N/A",
    }

    # ── 9. PER-INTENT COMPARISON (MiniLM vs MPNet) ──
    print("\n=== STEP 9: Per-Intent Comparison ===", file=sys.stderr)
    all_intents = sorted(set(r["true_intent"] for r in hybrid_results))
    per_intent_compare = {}
    for intent in all_intents:
        mm = hybrid_metrics["per_intent"].get(intent, {})
        mp = mpnet_hybrid_metrics["per_intent"].get(intent, {})
        per_intent_compare[intent] = {
            "minilm_f1": mm.get("f1", 0),
            "mpnet_f1": mp.get("f1", 0),
            "delta_f1": round(mp.get("f1", 0) - mm.get("f1", 0), 4),
            "minilm_accuracy": mm.get("accuracy", 0),
            "mpnet_accuracy": mp.get("accuracy", 0),
        }
    output["per_intent_comparison"] = per_intent_compare

    # ── 10. ALL FAILURE EXAMPLES (hybrid) ──
    output["all_hybrid_failures"] = [r for r in hybrid_results if not r["correct"]]

    # ── Write output ──
    print("\n=== Writing JSON output ===", file=sys.stderr)
    with open("phase67_results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
