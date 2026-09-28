"""Cohorts — metrics by creator/price_bucket/offer_type."""
from __future__ import annotations

from typing import Any, Callable

from commerce.offline_optimizer import extract_features

from simulation.evaluation.metrics import Metrics, evaluate_predictions


def cohort_key_price_bucket(bundle: Any) -> str:
    try:
        feats = extract_features(bundle.input)
        return feats.get("price_bucket", "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def cohort_key_offer_type(bundle: Any) -> str:
    try:
        feats = extract_features(bundle.input)
        return feats.get("offer_type", "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def cohort_key_lifecycle(bundle: Any) -> str:
    try:
        feats = extract_features(bundle.input)
        return feats.get("lifecycle", "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def metrics_by_cohort(
    bundles: list[Any],
    probs: list[float | None],
    key_fn: Callable[[Any], str],
) -> dict[str, Metrics]:
    grouped: dict[str, list[tuple[int, float]]] = {}
    for b, p in zip(bundles, probs):
        if p is None:
            continue
        k = key_fn(b)
        # derive y from bundle evidence via build_supervised_label?
        from commerce.offline_optimizer import make_training_example

        ex, _ = make_training_example(input=b.input, evidence=b.evidence, ledger_row=b.ledger_row)
        if ex is None:
            continue
        y = int(ex.binary)
        grouped.setdefault(k, []).append((y, float(p)))
    out: dict[str, Metrics] = {}
    for k, pairs in grouped.items():
        y_true = [y for y, _ in pairs]
        y_pred = [p for _, p in pairs]
        out[k] = evaluate_predictions(y_true=y_true, y_pred=y_pred)
    return out
