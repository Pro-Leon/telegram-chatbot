"""Scenario Packs — Phase 8 deterministic strategy registry.

No DB, no ranking duplication, file-only.
Scenario_id maps to OfferStrategy; unknown → baseline fallback.
Preserves baseline replay: baseline = single SMALL_BUNDLE 1999.
"""

from __future__ import annotations

from typing import Any

from simulation.offer_strategy import OfferStrategy, PriceDistribution

# Registry: 10 packs per P17:699-746 (5 original preserved)
SCENARIO_PACKS: dict[str, OfferStrategy] = {
    "baseline": OfferStrategy(
        strategy_id="baseline",
        description="Baseline replay: single SMALL_BUNDLE 1999 (pre-8 single-content reuse, deterministic)",
        content_mix={"SMALL_BUNDLE": 1.0},
        price_distribution=PriceDistribution(price_min=1999, price_max=1999, reference=2000),
        vault_count_range=(2, 2),
        family_id=None,
        currency="USD",
    ),
    "cheap_single": OfferStrategy(
        strategy_id="cheap_single",
        description="Cheap single: SINGLE 0.7 SMALL_BUNDLE 0.3 price LOW-biased 500-1500 vault 1-2",
        content_mix={"SINGLE": 0.7, "SMALL_BUNDLE": 0.3},
        price_distribution=PriceDistribution(price_min=500, price_max=1500, reference=2000),
        vault_count_range=(1, 2),
        family_id=None,
        currency="USD",
    ),
    "premium_mix": OfferStrategy(
        strategy_id="premium_mix",
        description="Premium mix: PREMIUM 0.4 CORE_BUNDLE 0.3 SMALL_BUNDLE 0.2 SINGLE 0.1 price HIGH-biased 2500-5000 vault 2-4",
        content_mix={"PREMIUM": 0.4, "CORE_BUNDLE": 0.3, "SMALL_BUNDLE": 0.2, "SINGLE": 0.1},
        price_distribution=PriceDistribution(price_min=2500, price_max=5000, reference=2000),
        vault_count_range=(2, 4),
        family_id=None,
        currency="USD",
    ),
    "balanced": OfferStrategy(
        strategy_id="balanced",
        description="Balanced: uniform 0.25 each price 500-5000 uniform vault 1-3",
        content_mix={"SINGLE": 0.25, "SMALL_BUNDLE": 0.25, "CORE_BUNDLE": 0.25, "PREMIUM": 0.25},
        price_distribution=PriceDistribution(price_min=500, price_max=5000, reference=2000),
        vault_count_range=(1, 3),
        family_id=None,
        currency="USD",
    ),
    "novelty_heavy": OfferStrategy(
        strategy_id="novelty_heavy",
        description="Novelty heavy: same as balanced but docs will test ranking novelty via diverse vault_ids (input-shaping only, pure ranking v1 unchanged)",
        content_mix={"SINGLE": 0.25, "SMALL_BUNDLE": 0.25, "CORE_BUNDLE": 0.25, "PREMIUM": 0.25},
        price_distribution=PriceDistribution(price_min=500, price_max=5000, reference=2000),
        vault_count_range=(1, 4),
        family_id=None,
        currency="USD",
    ),
    # Phase 10 — 5 new deterministic packs (P17)
    "whales": OfferStrategy(
        strategy_id="whales",
        description="Whales heavy-tail: balanced mix 0.25 each price 1500-5000 vault 2-4; behavior override purchase_propensity +0.25 / price_sensitivity -0.3 via trait hash bias (small high spenders, v1, OFF-POLICY assumption)",
        content_mix={"SINGLE": 0.25, "SMALL_BUNDLE": 0.25, "CORE_BUNDLE": 0.25, "PREMIUM": 0.25},
        price_distribution=PriceDistribution(price_min=1500, price_max=5000, reference=2000),
        vault_count_range=(2, 4),
        family_id=None,
        currency="USD",
    ),
    "freebie_heavy": OfferStrategy(
        strategy_id="freebie_heavy",
        description="Freebie-heavy: SINGLE 0.6 SMALL_BUNDLE 0.2 CORE_BUNDLE 0.1 PREMIUM 0.1 price 500-2000 vault 1-2; freebie_tendency 0.7-0.9 purchase_propensity -0.2 via hash, content_mix SINGLE heavy",
        content_mix={"SINGLE": 0.6, "SMALL_BUNDLE": 0.2, "CORE_BUNDLE": 0.1, "PREMIUM": 0.1},
        price_distribution=PriceDistribution(price_min=500, price_max=2000, reference=2000),
        vault_count_range=(1, 2),
        family_id=None,
        currency="USD",
    ),
    "temporal_drift": OfferStrategy(
        strategy_id="temporal_drift",
        description="Temporal drift: balanced 0.25 each price 500-5000 vault 1-3; latent p += drift_rate*days*sign_hash drift_rate 0.003/day (~0.27/90d) hash-based v1 (simulation/drift.py DriftModel)",
        content_mix={"SINGLE": 0.25, "SMALL_BUNDLE": 0.25, "CORE_BUNDLE": 0.25, "PREMIUM": 0.25},
        price_distribution=PriceDistribution(price_min=500, price_max=5000, reference=2000),
        vault_count_range=(1, 3),
        family_id=None,
        currency="USD",
    ),
    "no_signal": OfferStrategy(
        strategy_id="no_signal",
        description="No-signal control: balanced 0.25 each price 500-5000 vault 1-3; constant p=0.5 via BaselineOutcomeModel (outcome.py:38) for failure-suite control",
        content_mix={"SINGLE": 0.25, "SMALL_BUNDLE": 0.25, "CORE_BUNDLE": 0.25, "PREMIUM": 0.25},
        price_distribution=PriceDistribution(price_min=500, price_max=5000, reference=2000),
        vault_count_range=(1, 3),
        family_id=None,
        currency="USD",
    ),
    "adversarial": OfferStrategy(
        strategy_id="adversarial",
        description="Adversarial flip after day45: balanced 0.25 each price 500-5000 vault 1-3; content_affinity weight 0.8 before 45d else -0.8 via evaluated_at < simulated_start+45d (misleading correlation, OFF-POLICY)",
        content_mix={"SINGLE": 0.25, "SMALL_BUNDLE": 0.25, "CORE_BUNDLE": 0.25, "PREMIUM": 0.25},
        price_distribution=PriceDistribution(price_min=500, price_max=5000, reference=2000),
        vault_count_range=(1, 3),
        family_id=None,
        currency="USD",
    ),
}


def get_strategy(scenario_id: str) -> OfferStrategy:
    """Lookup strategy by scenario_id; unknown falls back to baseline (backward compat)."""
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        return SCENARIO_PACKS["baseline"]
    return SCENARIO_PACKS.get(scenario_id.strip(), SCENARIO_PACKS["baseline"])


def get_strategy_for_run(simulation_run: Any) -> OfferStrategy:
    """Lookup via SimulationRun.scenario_id."""
    try:
        sid = getattr(simulation_run, "scenario_id")
        if isinstance(sid, str) and sid.strip():
            return get_strategy(sid)
    except Exception:
        pass
    return SCENARIO_PACKS["baseline"]


__all__ = ["SCENARIO_PACKS", "get_strategy", "get_strategy_for_run"]
