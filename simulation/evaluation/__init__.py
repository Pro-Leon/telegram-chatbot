"""Evaluation harness — chronological split, metrics, cohorts, latent recovery, dataset hash."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from commerce.offline_optimizer import RowBundle

from simulation.evaluation.baselines import global_purchase_rate
from simulation.evaluation.metrics import Metrics, brier_score, evaluate_predictions, log_loss
from simulation.evaluation.reports import EvaluationReport, dataset_hash


def chronological_split(
    bundles: list[RowBundle],
    train_n: int = 60,
    valid_n: int = 15,
    test_n: int = 15,
    train_end: datetime | None = None,
    valid_start: datetime | None = None,
    test_start: datetime | None = None,
) -> tuple[list[RowBundle], list[RowBundle], list[RowBundle]]:
    """Deterministic split by evaluated_at then opportunity_id.

    Default 60/15/15 matches plan TRAIN 1-60 VALID 61-75 TEST 76-90.
    If datetimes provided, guard train_end <= valid_start <= test_start.
    No shuffling per P18.
    """
    sorted_bundles = sorted(bundles, key=lambda b: (b.input.evaluated_at, b.input.opportunity_id))
    if train_end is not None and valid_start is not None and test_start is not None:
        if not (train_end <= valid_start <= test_start):
            raise ValueError("chronology requires train_end <= valid_start <= test_start")
        train = [b for b in sorted_bundles if b.input.evaluated_at < train_end]
        valid = [b for b in sorted_bundles if valid_start <= b.input.evaluated_at < test_start]
        # need test_end? Use last valid+test_n
        test = [b for b in sorted_bundles if b.input.evaluated_at >= test_start]
        # slice to requested counts if datetimes over-select
        # keep deterministic: already sorted, so truncate to test_n
        if len(test) > test_n:
            test = test[:test_n]
        if len(valid) > valid_n:
            valid = valid[:valid_n]
        if len(train) > train_n:
            train = train[-train_n:]
        return train, valid, test
    # count-based default
    if len(sorted_bundles) < train_n + valid_n + test_n:
        raise ValueError(f"not enough bundles for split: have {len(sorted_bundles)} need {train_n+valid_n+test_n}")
    train = sorted_bundles[:train_n]
    valid = sorted_bundles[train_n : train_n + valid_n]
    test = sorted_bundles[train_n + valid_n : train_n + valid_n + test_n]
    # guard chronological: last train < first valid etc.
    if train and valid and not (train[-1].input.evaluated_at <= valid[0].input.evaluated_at):
        raise ValueError("train/valid not chronological")
    if valid and test and not (valid[-1].input.evaluated_at <= test[0].input.evaluated_at):
        raise ValueError("valid/test not chronological")
    return train, valid, test


def evaluate_vs_latent(bundles: list[Any], hidden_payloads: list[dict[str, Any]], probs: list[float]) -> Metrics:
    """Compare predicted probs vs latent_purchase_probability (diagnostic only)."""
    y_true = []
    for h in hidden_payloads:
        # latent is float prob, but for Brier we compare p_pred vs p_latent? Use latent as soft label
        y_true.append(float(h.get("latent_purchase_probability", 0.5)))
    # For Brier vs latent, treat latent as y (soft)
    if not y_true or len(y_true) != len(probs):
        return Metrics(None, None, None, None, None, None, None, None, None, 0, 0)
    # Brier vs latent soft
    brier = sum((p - y) ** 2 for p, y in zip(probs, y_true)) / len(probs) if probs else None
    # log loss vs soft not standard; use Brier for diagnostics
    return Metrics(log_loss=None, brier=brier, calibration_gap=None, accuracy=None, precision=None, recall=None, coverage=None, mean_predicted=sum(probs)/len(probs) if probs else None, observed_rate=sum(y_true)/len(y_true) if y_true else None, n=len(y_true), n_predicted=len(probs))


__all__ = [
    "chronological_split",
    "dataset_hash",
    "evaluate_predictions",
    "evaluate_vs_latent",
    "log_loss",
    "brier_score",
    "EvaluationReport",
]
