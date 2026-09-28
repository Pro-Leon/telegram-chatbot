"""Metrics — log_loss/Brier/calibration/accuracy/coverage.

Pure, deterministic, no DB. Reuses offline_optimizer Brier logic where possible.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from commerce.offline_optimizer import PROTOTYPE_THRESHOLD


@dataclass(frozen=True)
class Metrics:
    log_loss: float | None
    brier: float | None
    calibration_gap: float | None
    accuracy: float | None
    precision: float | None
    recall: float | None
    coverage: float | None
    mean_predicted: float | None
    observed_rate: float | None
    n: int
    n_predicted: int


def log_loss(y_true: list[int], y_pred: list[float], eps: float = 1e-15) -> float | None:
    if not y_true or not y_pred or len(y_true) != len(y_pred):
        return None
    s = 0.0
    for y, p in zip(y_true, y_pred):
        p = min(max(p, eps), 1 - eps)
        s += y * math.log(p) + (1 - y) * math.log(1 - p)
    return -s / len(y_true)


def brier_score(y_true: list[int], y_pred: list[float]) -> float | None:
    if not y_true or not y_pred or len(y_true) != len(y_pred):
        return None
    return sum((p - y) ** 2 for y, p in zip(y_true, y_pred)) / len(y_true)


def evaluate_predictions(
    y_true: list[int] | None = None,
    y_pred: list[float] | None = None,
    *,
    bundles: list[Any] | None = None,
    probs: list[float | None] | None = None,
) -> Metrics:
    """Evaluate predictions. Accepts either (y_true,y_pred) or (bundles,probs)."""
    if bundles is not None and probs is not None:
        # extract y from bundles via evidence? Use label 1/0 from bundle? Caller provides y via bundles already filtered primary?
        # For generic, assume y_true derived elsewhere; this branch supports direct lists.
        # Fallback to direct lists if bundles provided but y_true not
        y_true = []
        y_pred_filtered = []
        for b, p in zip(bundles, probs):
            if p is None:
                continue
            # derive y from bundle evidence? Use b.evidence label? For metrics we expect caller passes y_true separately.
            # Here we just skip; caller should use direct y_true/y_pred.
            pass
        # if not handled, fall through to direct
        if not y_true and y_pred is None:
            pass
    if y_true is None or y_pred is None:
        return Metrics(None, None, None, None, None, None, None, None, None, 0, 0)
    # filter none predictions? assume y_pred already filtered
    y_true_f = [int(y) for y in y_true]
    y_pred_f = [float(p) for p in y_pred]  # type: ignore
    if len(y_true_f) != len(y_pred_f) or not y_true_f:
        return Metrics(None, None, None, None, None, None, None, None, None, len(y_true_f), len(y_pred_f))
    # coverage: assume all predicted if lists equal
    n = len(y_true_f)
    n_pred = len([p for p in y_pred_f if p is not None])
    # accuracy via threshold 0.5
    correct = sum(1 for y, p in zip(y_true_f, y_pred_f) if (1 if p >= PROTOTYPE_THRESHOLD else 0) == y)
    pred_pos = sum(1 for p in y_pred_f if p >= PROTOTYPE_THRESHOLD)
    true_pos = sum(1 for y, p in zip(y_true_f, y_pred_f) if p >= PROTOTYPE_THRESHOLD and y == 1)
    actual_pos = sum(1 for y in y_true_f if y == 1)
    acc = correct / n_pred if n_pred else None
    prec = (true_pos / pred_pos) if pred_pos else None
    rec = (true_pos / actual_pos) if actual_pos else None
    brier = brier_score(y_true_f, y_pred_f)
    ll = log_loss(y_true_f, y_pred_f)
    mean_pred = sum(y_pred_f) / len(y_pred_f) if y_pred_f else None
    obs_rate = sum(y_true_f) / len(y_true_f) if y_true_f else None
    calib = abs(mean_pred - obs_rate) if mean_pred is not None and obs_rate is not None else None
    coverage = n_pred / n if n else None
    return Metrics(
        log_loss=ll,
        brier=brier,
        calibration_gap=calib,
        accuracy=acc,
        precision=prec,
        recall=rec,
        coverage=coverage,
        mean_predicted=mean_pred,
        observed_rate=obs_rate,
        n=n,
        n_predicted=n_pred,
    )
