import asyncio
from collections import Counter, defaultdict

from commerce.validation_dataset import VALIDATION_DATASET
from commerce.intent_corpus import INTENT_CORPUS

# We need to reimplement analyze_message with variable thresholds without modifying production file
# Instead, we will monkey-patch thresholds via direct logic replication here for sweep
# Simpler: import unified_intelligence and temporarily override global thresholds via patch

from commerce import unified_intelligence as ui
import commerce.embedding_model as em

# Ensure reference cache loaded with model
from commerce.embedding_model import get_model

print("Loading model...")
model = get_model()
print(f"Model loaded: {model is not None}")
if model is not None:
    from commerce.unified_intelligence import _ensure_reference_cache
    _ensure_reference_cache()
    print("Reference cache ensured")

# Now sweep
import time

# Define thresholds to sweep
semantic_thresholds = [0.50,0.55,0.60,0.65,0.70,0.75]
margins = [0.00,0.02,0.05,0.07,0.10,0.15]
lexical_cutoffs = [60,65,70,75,80,85]

# For main surface, vary semantic and margin, keep lexical 80
results = []

async def evaluate_with_params(sem_thr, margin_thr, lex_cut):
    # Temporarily patch thresholds
    orig_sem = ui.SEMANTIC_THRESHOLD
    orig_margin = ui.MARGIN_THRESHOLD
    orig_lex = ui.LEXICAL_CUTOFF
    ui.SEMANTIC_THRESHOLD = sem_thr
    ui.MARGIN_THRESHOLD = margin_thr
    ui.LEXICAL_CUTOFF = lex_cut
    # Need to also patch if _ensure_reference_cache uses thresholds? No, thresholds used in analyze_message confidence calc
    correct = 0
    purchase_tp=0
    purchase_fp=0
    purchase_fn=0
    purchase_tn=0
    abstained=0
    per_intent_correct = Counter()
    per_intent_total = Counter()
    confusion = Counter()
    # Also track top scores for distribution
    top_scores_correct = []
    top_scores_incorrect = []
    margins_correct = []
    margins_incorrect = []
    for ex in VALIDATION_DATASET:
        text = ex["text"]
        true = ex["intent"]
        per_intent_total[true]+=1
        uni = await ui.analyze_message(text)
        pred = uni.primary_intent
        # Count abstention for all where uncertainty true (regardless of correct)
        if uni.uncertainty:
            abstained+=1
        if pred == true:
            correct+=1
            per_intent_correct[true]+=1
            top_scores_correct.append(uni.top_score)
            margins_correct.append(uni.margin)
        else:
            top_scores_incorrect.append(uni.top_score)
            margins_incorrect.append(uni.margin)
        confusion[(true,pred)]+=1
        # Purchase
        is_true_purchase = true in ("purchase_intent","repeat_purchase_intent")
        is_pred_purchase = pred in ("purchase_intent","repeat_purchase_intent") and not uni.uncertainty and uni.purchase_intent>0.5
        if is_true_purchase and is_pred_purchase:
            purchase_tp+=1
        elif not is_true_purchase and is_pred_purchase:
            purchase_fp+=1
        elif is_true_purchase and not is_pred_purchase:
            purchase_fn+=1
        else:
            purchase_tn+=1
    total = len(VALIDATION_DATASET)
    accuracy = correct/total
    # For purchase metrics
    purchase_precision = purchase_tp/(purchase_tp+purchase_fp) if (purchase_tp+purchase_fp) else 0
    purchase_recall = purchase_tp/(purchase_tp+purchase_fn) if (purchase_tp+purchase_fn) else 0
    fpr = purchase_fp/(purchase_fp+purchase_tn) if (purchase_fp+purchase_tn) else 0
    fnr = purchase_fn/(purchase_tp+purchase_fn) if (purchase_tp+purchase_fn) else 0
    abstention = abstained / total
    # Restore
    ui.SEMANTIC_THRESHOLD = orig_sem
    ui.MARGIN_THRESHOLD = orig_margin
    ui.LEXICAL_CUTOFF = orig_lex
    return {
        "semantic": sem_thr,
        "margin": margin_thr,
        "lexical": lex_cut,
        "accuracy": accuracy,
        "purchase_precision": purchase_precision,
        "purchase_recall": purchase_recall,
        "fpr": fpr,
        "fnr": fnr,
        "correct": correct,
        "abstained": abstained,
    }

async def main():
    # First baseline with current thresholds
    print("Baseline with current thresholds (0.65,0.10,80):")
    res = await evaluate_with_params(0.65, 0.10, 80)
    print(res)
    # Sweep semantic x margin with lexical 80 fixed
    print("\nCalibration surface: semantic x margin (lexical 80 fixed)")
    print("sem,margin,acc,prec,rec,fpr,abst")
    best = None
    for sem in semantic_thresholds:
        for mar in margins:
            r = await evaluate_with_params(sem, mar, 80)
            print(f"{sem:.2f},{mar:.2f},{r['accuracy']:.3f},{r['purchase_precision']:.3f},{r['purchase_recall']:.3f},{r['fpr']:.3f},{r['abstained']/110:.3f}")
            # Check gate: accuracy>0.60, FPR<0.05, recall>0.80
            if r['accuracy']>0.60 and r['fpr']<0.05 and r['purchase_recall']>0.80:
                if best is None or r['accuracy']>best['accuracy']:
                    best = r
    if best:
        print(f"\nBest meeting gates: {best}")
    else:
        print("\nNo config meets all gates (acc>0.60, FPR<0.05, recall>0.80)")
    # Lexical sweep with semantic 0.65, margin 0.10 fixed
    print("\nLexical sweep (semantic 0.65, margin 0.10):")
    for lex in lexical_cutoffs:
        r = await evaluate_with_params(0.65, 0.10, lex)
        print(f"lex {lex}: acc {r['accuracy']:.3f} fpr {r['fpr']:.3f} rec {r['purchase_recall']:.3f}")
    # Also test hybrid improvement: need to handle lexical vs semantic contribution
    # For now, just baseline

import asyncio
asyncio.run(main())
