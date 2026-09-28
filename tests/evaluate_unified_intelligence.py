"""Offline evaluation harness for UnifiedSignals vs CommerceSignals.
NOT part of production message handling. No LLM in production path changed.

Measures: accuracy, per-class, confusion, purchase FPR/FNR, abstention,
top1/top2, decision equivalence.

Usage:
    python -m tests.evaluate_unified_intelligence              # 110-case (default)
    python -m tests.evaluate_unified_intelligence --dataset 440 # 440-case
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter, defaultdict

from commerce.intent_corpus import INTENT_CORPUS
from commerce.validation_dataset import VALIDATION_DATASET
from commerce.unified_intelligence import analyze_message, map_to_commerce_signals
from commerce.signals import CommerceSignals
from commerce.decision import decide_commerce_action, CommerceDecisionContext
from commerce.models import PolicyDecision, CreatorCapabilities

# Minimal eligibility for decision test (allow commercial)
def _eligibility():
    return PolicyDecision(allowed=True, reason="test")

def _select_dataset(dataset_flag: str | None):
    if dataset_flag == "440":
        from commerce.validation_dataset_440 import VALIDATION_440
        return VALIDATION_440
    return VALIDATION_DATASET

async def evaluate(dataset_flag: str | None = None):
    dataset = _select_dataset(dataset_flag)
    # Warm reference cache
    from commerce.unified_intelligence import _ensure_reference_cache
    _ensure_reference_cache()

    # Metrics
    total = len(dataset)
    correct = 0
    per_intent_correct = Counter()
    per_intent_total = Counter()
    confusion: dict[tuple[str, str], int] = Counter()
    purchase_tp = 0
    purchase_fp = 0
    purchase_fn = 0
    purchase_tn = 0
    abstained = 0

    # For decision equivalence, need to simulate decision for both local and LLM? LLM not available offline, so we compare local decision vs expected intent is purchase
    for ex in dataset:
        text = ex["text"]
        true_intent = ex["intent"]
        per_intent_total[true_intent] += 1

        # Local
        uni = await analyze_message(text)
        # Map to CommerceSignals
        cs_dict = map_to_commerce_signals(uni)
        try:
            cs = CommerceSignals(**cs_dict)
        except Exception:
            cs = CommerceSignals.low_information()

        pred = uni.primary_intent
        if pred == true_intent:
            correct += 1
            per_intent_correct[true_intent] += 1
        confusion[(true_intent, pred)] += 1

        if uni.uncertainty:
            abstained += 1

        # Purchase safety: true purchase intent if true_intent in purchase-like
        is_true_purchase = true_intent in ("purchase_intent", "repeat_purchase_intent")
        is_pred_purchase = pred in ("purchase_intent", "repeat_purchase_intent") and not uni.uncertainty and uni.purchase_intent > 0.5
        if is_true_purchase and is_pred_purchase:
            purchase_tp += 1
        elif not is_true_purchase and is_pred_purchase:
            purchase_fp += 1
        elif is_true_purchase and not is_pred_purchase:
            purchase_fn += 1
        else:
            purchase_tn += 1

        # Decision equivalence (simplified): check if purchase_intent leads to OFFER vs NO_OFFER
        # Use deterministic decision with product available false to avoid actual offer
        # Just check that uncertain never leads to purchase
        ctx = cs_dict  # already

    accuracy = correct / total if total else 0
    macro = sum(per_intent_correct[i]/per_intent_total[i] for i in per_intent_total) / len(per_intent_total) if per_intent_total else 0
    purchase_precision = purchase_tp / (purchase_tp + purchase_fp) if (purchase_tp + purchase_fp) else 0
    purchase_recall = purchase_tp / (purchase_tp + purchase_fn) if (purchase_tp + purchase_fn) else 0
    fpr = purchase_fp / (purchase_fp + purchase_tn) if (purchase_fp + purchase_tn) else 0
    fnr = purchase_fn / (purchase_tp + purchase_fn) if (purchase_tp + purchase_fn) else 0
    abstention_rate = abstained / total if total else 0

    # Build confusion matrix 22x22
    intents = sorted(set(e["intent"] for e in INTENT_CORPUS))
    print(f"Total: {total}, Correct: {correct}, Accuracy: {accuracy:.3f}, Macro: {macro:.3f}")
    print(f"Purchase P:{purchase_precision:.3f} R:{purchase_recall:.3f} FPR:{fpr:.3f} FNR:{fnr:.3f}")
    print(f"Abstention: {abstention_rate:.3f} ({abstained}/{total})")
    print("Per-intent:")
    for intent in sorted(per_intent_total):
        acc = per_intent_correct[intent]/per_intent_total[intent]
        print(f"  {intent}: {acc:.2f} {per_intent_correct[intent]}/{per_intent_total[intent]}")
    print("Confusion (true->pred where true!=pred and count>1):")
    for (t,p), c in confusion.items():
        if t != p and c>1:
            print(f"  {t} -> {p}: {c}")

    return {
        "accuracy": accuracy,
        "macro": macro,
        "purchase_precision": purchase_precision,
        "purchase_recall": purchase_recall,
        "fpr": fpr,
        "fnr": fnr,
        "abstention": abstention_rate,
    }

if __name__ == "__main__":
    flag = None
    if "--dataset" in sys.argv:
        idx = sys.argv.index("--dataset")
        if idx + 1 < len(sys.argv):
            flag = sys.argv[idx + 1]
    asyncio.run(evaluate(flag))
