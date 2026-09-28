"""Baselines — global/creator/fan recent/deterministic (P24)."""
from __future__ import annotations

from typing import Any

from commerce.offline_optimizer import make_training_example


def _clamp(p: float) -> float:
    return min(max(p, 0.01), 0.99)


def global_purchase_rate(train_bundles: list[Any]) -> float:
    ys = []
    for b in train_bundles:
        ex, _ = make_training_example(input=b.input, evidence=b.evidence, ledger_row=b.ledger_row)
        if ex is not None:
            ys.append(int(ex.binary))
    if not ys:
        return 0.5
    return _clamp(sum(ys) / len(ys))


def creator_purchase_rate(train_bundles: list[Any], creator_id: int) -> float:
    ys = []
    for b in train_bundles:
        if int(b.input.creator_id) != int(creator_id):
            continue
        ex, _ = make_training_example(input=b.input, evidence=b.evidence, ledger_row=b.ledger_row)
        if ex is not None:
            ys.append(int(ex.binary))
    if not ys:
        return global_purchase_rate(train_bundles)
    return _clamp(sum(ys) / len(ys))


def fan_recent_rate(train_bundles: list[Any], fan_id: int, window: int = 5) -> float | None:
    # look at most recent window bundles for this fan (by evaluated_at)
    candidates = [b for b in train_bundles if int(b.input.user_id) == int(fan_id)]
    candidates.sort(key=lambda b: b.input.evaluated_at)
    recent = candidates[-window:] if candidates else []
    ys = []
    for b in recent:
        ex, _ = make_training_example(input=b.input, evidence=b.evidence, ledger_row=b.ledger_row)
        if ex is not None:
            ys.append(int(ex.binary))
    if not ys:
        return None
    return _clamp(sum(ys) / len(ys))


class BaselinePredictor:
    def __init__(self, prob: float) -> None:
        self.prob = _clamp(float(prob))

    def predict(self, bundle: Any) -> float:
        return self.prob


def most_frequent_offer_type_rate(train_bundles: list[Any]) -> dict[str, float]:
    from collections import defaultdict

    counts: dict[str, list[int]] = defaultdict(list)
    for b in train_bundles:
        ex, _ = make_training_example(input=b.input, evidence=b.evidence, ledger_row=b.ledger_row)
        if ex is None:
            continue
        # offer_type from features
        from commerce.offline_optimizer import extract_features

        try:
            feats = extract_features(b.input)
            otype = feats.get("offer_type", "UNKNOWN")
        except Exception:
            otype = "UNKNOWN"
        counts[otype].append(int(ex.binary))
    out: dict[str, float] = {}
    for k, ys in counts.items():
        out[k] = _clamp(sum(ys) / len(ys)) if ys else 0.5
    return out
