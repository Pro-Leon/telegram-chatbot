"""FanBehavior — latent behavioral heterogeneity (Phase 4).

Traits are continuous in [0,1], deterministic from simulation identity,
with moderate intentional correlations.

No persona labels as primary abstraction; latent values are source of truth.
Categorical labels may be derived for debugging but not used as brittle logic.

Correlations (moderate, with counterexamples):
- engagement ↔ purchase (≈+0.3)
- relationship ↔ purchase (≈+0.3)
- freebie ↔ purchase (≈-0.3)
- price_sensitivity ↔ purchase (≈-0.3)
- content_preference independent (future fan×content)
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from simulation.world import SyntheticFan


def _hash_float(seed: int, run_id: str, domain: str, counter: int) -> float:
    payload = f"{seed}:{run_id}:{domain}:{counter}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    v = int(digest[:8], 16)
    return v / 4294967296.0


def _clamp01(v: float) -> float:
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return float(v)


def _sigmoid(x: float) -> float:
    # numerically stable
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


@dataclass(frozen=True)
class FanBehavior:
    """Immutable latent fan behavioral traits. All in [0,1]."""

    fan_id: int
    creator_id: int
    simulation_id: str
    behavior_model_version: str
    purchase_propensity: float
    engagement_level: float
    relationship_affinity: float
    price_sensitivity: float
    freebie_tendency: float
    content_preference_strength: float

    def __post_init__(self) -> None:
        for name in (
            "purchase_propensity",
            "engagement_level",
            "relationship_affinity",
            "price_sensitivity",
            "freebie_tendency",
            "content_preference_strength",
        ):
            v = getattr(self, name)
            if not isinstance(v, float) or isinstance(v, bool):
                raise ValueError(f"{name} must be float in [0,1]")
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} must be in [0,1], got {v}")

        if not isinstance(self.fan_id, int) or isinstance(self.fan_id, bool) or self.fan_id <= 0:
            raise ValueError("fan_id must be positive int")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fan_id": self.fan_id,
            "creator_id": self.creator_id,
            "simulation_id": self.simulation_id,
            "behavior_model_version": self.behavior_model_version,
            "purchase_propensity": self.purchase_propensity,
            "engagement_level": self.engagement_level,
            "relationship_affinity": self.relationship_affinity,
            "price_sensitivity": self.price_sensitivity,
            "freebie_tendency": self.freebie_tendency,
            "content_preference_strength": self.content_preference_strength,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FanBehavior:
        return cls(
            fan_id=int(data["fan_id"]),
            creator_id=int(data["creator_id"]),
            simulation_id=str(data["simulation_id"]),
            behavior_model_version=str(data.get("behavior_model_version", "v1")),
            purchase_propensity=float(data["purchase_propensity"]),
            engagement_level=float(data["engagement_level"]),
            relationship_affinity=float(data["relationship_affinity"]),
            price_sensitivity=float(data["price_sensitivity"]),
            freebie_tendency=float(data["freebie_tendency"]),
            content_preference_strength=float(data["content_preference_strength"]),
        )

    def derived_label(self) -> str:
        """Optional derived categorical for debugging — not used as logic."""
        # Simple heuristic: highest trait defines label, but not authoritative
        traits = {
            "purchase_prone": self.purchase_propensity,
            "engaged": self.engagement_level,
            "relational": self.relationship_affinity,
            "price_sensitive": self.price_sensitivity,
            "freebie": self.freebie_tendency,
            "content_focused": self.content_preference_strength,
        }
        return max(traits, key=lambda k: traits[k])  # type: ignore[arg-type]


@dataclass(frozen=True)
class BehavioralOutcomeConfig:
    """Transparent coefficients mapping latent traits to purchase probability.

    logit(p) = intercept
               + purchase_weight * purchase_propensity
               + engagement_weight * engagement_level
               + relationship_weight * relationship_affinity
               - price_sensitivity_weight * price_sensitivity
               - freebie_weight * freebie_tendency
               (+ content_weight * content_preference_strength — minimal until content modeled)

    All weights explicit, bounded, inspectable.
    """

    intercept: float = -1.0
    purchase_weight: float = 1.5
    engagement_weight: float = 0.8
    relationship_weight: float = 0.6
    price_sensitivity_weight: float = 0.8
    freebie_weight: float = 0.9
    content_weight: float = 0.0

    def __post_init__(self) -> None:
        # No range check beyond finiteness, keep configurable but sane
        for name in (
            "intercept",
            "purchase_weight",
            "engagement_weight",
            "relationship_weight",
            "price_sensitivity_weight",
            "freebie_weight",
            "content_weight",
        ):
            v = getattr(self, name)
            if not isinstance(v, (float, int)) or isinstance(v, bool):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(float(v)):
                raise ValueError(f"{name} must be finite")


class FanBehaviorModel:
    """Deterministic fan behavior generator.

    Same (seed, simulation_id, fan_id, behavior_model_version) → same traits.
    No global RNG. Fan-level stable.
    """

    def __init__(
        self,
        seed: int,
        simulation_id: str,
        behavior_model_version: str = "v1",
        correlation_strength: float = 0.4,
    ) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool) or seed <= 0:
            raise ValueError("seed must be positive int")
        if not simulation_id or not isinstance(simulation_id, str):
            raise ValueError("simulation_id required")
        if not 0.0 <= correlation_strength <= 1.0:
            raise ValueError("correlation_strength must be in [0,1]")
        self.seed = int(seed)
        self.simulation_id = str(simulation_id)
        self.behavior_model_version = str(behavior_model_version)
        self.correlation_strength = float(correlation_strength)
        self._cache: dict[int, FanBehavior] = {}

    def for_fan(self, fan: SyntheticFan) -> FanBehavior:
        """Return deterministic behavior for a fan (cached, stable)."""
        if not isinstance(fan, SyntheticFan):
            raise ValueError("fan must be SyntheticFan")
        fid = int(fan.fan_id)
        if fid in self._cache:
            return self._cache[fid]

        w = self.correlation_strength
        # 6 independent base uniforms [0,1) per fan, deterministic per fan
        # Use fan_id as part of domain to separate per fan, plus seed/run
        u_p = _hash_float(self.seed, self.simulation_id, f"fan:{fid}:purchase_propensity", 1)
        u_e = _hash_float(self.seed, self.simulation_id, f"fan:{fid}:engagement", 2)
        u_r = _hash_float(self.seed, self.simulation_id, f"fan:{fid}:relationship", 3)
        u_f = _hash_float(self.seed, self.simulation_id, f"fan:{fid}:freebie", 4)
        u_price = _hash_float(self.seed, self.simulation_id, f"fan:{fid}:price_sensitivity", 5)
        u_c = _hash_float(self.seed, self.simulation_id, f"fan:{fid}:content", 6)

        # Introduce moderate correlations:
        # Use weighted mix: trait = (1-w)*u_trait + w*driver
        # where driver = u_p for positive, (1-u_p) for negative
        purchase_propensity = _clamp01(u_p)
        engagement_level = _clamp01((1 - w) * u_e + w * u_p)
        relationship_affinity = _clamp01((1 - w) * u_r + w * u_p)
        freebie_tendency = _clamp01((1 - w) * u_f + w * (1.0 - u_p))
        price_sensitivity = _clamp01((1 - w) * u_price + w * (1.0 - u_p))
        content_preference_strength = _clamp01(u_c)  # independent

        behavior = FanBehavior(
            fan_id=int(fan.fan_id),
            creator_id=int(fan.creator_id),
            simulation_id=self.simulation_id,
            behavior_model_version=self.behavior_model_version,
            purchase_propensity=float(purchase_propensity),
            engagement_level=float(engagement_level),
            relationship_affinity=float(relationship_affinity),
            price_sensitivity=float(price_sensitivity),
            freebie_tendency=float(freebie_tendency),
            content_preference_strength=float(content_preference_strength),
        )
        self._cache[fid] = behavior
        return behavior

    def probability_for_fan(
        self, fan: SyntheticFan, config: BehavioralOutcomeConfig | None = None
    ) -> float:
        """Compute p = sigmoid(logit) for a fan (no opportunity content yet)."""
        beh = self.for_fan(fan)
        cfg = config or BehavioralOutcomeConfig()
        logit = (
            cfg.intercept
            + cfg.purchase_weight * beh.purchase_propensity
            + cfg.engagement_weight * beh.engagement_level
            + cfg.relationship_weight * beh.relationship_affinity
            - cfg.price_sensitivity_weight * beh.price_sensitivity
            - cfg.freebie_weight * beh.freebie_tendency
            + cfg.content_weight * beh.content_preference_strength
        )
        return _clamp01(_sigmoid(float(logit)))

    def clear_cache(self) -> None:
        self._cache.clear()


class BehavioralOutcomeModel:
    """Composable behavioral outcome model.

    Preserves Phase 3 OutcomeModel interface (decide) but replaces global p
    with fan-behavior-derived p = sigmoid(logit(behavior traits)).

    Randomness remains per-opportunity stochastic (r < p) with deterministic
    hash, not per-fan deterministic rule, so same fan + different opportunities
    may differ outcomes while behavior stays stable.
    """

    def __init__(
        self,
        behavior_model: FanBehaviorModel,
        outcome_config: BehavioralOutcomeConfig | None = None,
        purchase_delay_hours_min: int = 1,
        purchase_delay_hours_max: int = 48,
        non_purchase_outcome: str = "DECLINED",
    ) -> None:
        if not isinstance(behavior_model, FanBehaviorModel):
            raise ValueError("behavior_model must be FanBehaviorModel")
        self.behavior_model = behavior_model
        self.outcome_config = outcome_config or BehavioralOutcomeConfig()
        if purchase_delay_hours_min < 0 or purchase_delay_hours_max < purchase_delay_hours_min:
            raise ValueError("invalid purchase_delay range")
        if non_purchase_outcome not in ("DECLINED", "EXPIRED"):
            raise ValueError("non_purchase_outcome must be DECLINED or EXPIRED")
        self.purchase_delay_hours_min = int(purchase_delay_hours_min)
        self.purchase_delay_hours_max = int(purchase_delay_hours_max)
        self.non_purchase_outcome = non_purchase_outcome

    def decide(
        self,
        opportunity: Any,
        fan: Any,
        *,
        seed: int,
        run_id: str,
        counter: int,
    ) -> tuple[Any, Any, dict[str, Any]]:
        """Decide outcome for opportunity given fan behavior.

        Returns (SimulatedOutcome, GroundTruthReference, hidden_payload) where
        hidden_payload contains full behavior (latent) + latent probability.
        """
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        from simulation.ground_truth import GroundTruthReference as _GTR

        # Validate opportunity shape
        try:
            opp_id = int(getattr(opportunity, "opportunity_id"))
            gen = str(getattr(opportunity, "generation_id"))
            eval_at = getattr(opportunity, "evaluated_at")
        except Exception as exc:
            raise ValueError(f"opportunity invalid: {exc}") from exc
        if fan is None:
            from simulation.world import SyntheticFan as _SF

            # Try to find fan via opportunity.fan_id if available
            raise ValueError("fan is required for behavioral outcome (fan-level stability)")
        behavior = self.behavior_model.for_fan(fan)
        p = self.behavior_model.probability_for_fan(fan, self.outcome_config)
        # deterministic per-opportunity r in [0,1)
        r = _hash_float(seed, run_id, f"behavioral_outcome:{opp_id}", counter)
        purchased = r < p

        sent_at = eval_at + _td(minutes=1)
        if not purchased:
            outcome_state = self.non_purchase_outcome
            outcome_at = sent_at + _td(hours=2)
            purchase_at = None
            transaction_id = None
        else:
            outcome_state = "PURCHASED"
            delay_range = self.purchase_delay_hours_max - self.purchase_delay_hours_min + 1
            delay_h = _hash_float(seed, run_id, f"purchase_delay:{opp_id}", counter + 1000)
            # convert float 0..1 to int range
            delay_h_int = int(delay_h * delay_range) + self.purchase_delay_hours_min
            delay_m = int(_hash_float(seed, run_id, f"purchase_min:{opp_id}", counter + 2000) * 60)
            purchase_at = sent_at + _td(hours=int(delay_h_int), minutes=int(delay_m))
            outcome_at = purchase_at
            transaction_id = f"txn:sym:{run_id}:{opp_id}"
        if purchased:
            maturity_at = outcome_at  # type: ignore[assignment]
        else:
            maturity_at = outcome_at
        # Build SimulatedOutcome without importing to avoid circular
        from simulation.outcome import SimulatedOutcome

        outcome = SimulatedOutcome(
            opportunity_id=int(opp_id),
            generation_id=str(gen),
            purchased=bool(purchased),
            latent_purchase_probability=float(p),
            outcome_state=outcome_state,
            exposure_state="SENT",
            sent_at=sent_at,
            purchase_at=purchase_at,
            maturity_at=maturity_at,  # type: ignore[arg-type]
            transaction_id=transaction_id,
        )
        ref = _GTR.create(
            simulation_id=str(run_id),
            opportunity_id=int(opp_id),
            generation_id=str(gen),
            created_at=_dt.now(_UTC),
        )
        hidden_payload = {
            "latent_purchase_probability": float(p),
            "rng_value": float(r),
            "outcome": outcome_state,
            "purchased": bool(purchased),
            "purchase_at": purchase_at.isoformat() if purchase_at else None,
            "sent_at": sent_at.isoformat(),
            "behavior": behavior.to_dict(),
        }
        return outcome, ref, hidden_payload


__all__ = ["FanBehavior", "FanBehaviorModel", "BehavioralOutcomeConfig", "BehavioralOutcomeModel"]
