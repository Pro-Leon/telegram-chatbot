"""Drift / Whale-Freebie / Adversarial helpers — Phase 10.

Deterministic via SHA256(seed:simulation_id:domain:counter) only.
No global random, no DB, file-only, USD only.

Packs:
- whales: purchase_propensity +0.25 / price_sensitivity -0.3 (heavy-tail)
- freebie_heavy: freebie_tendency 0.7-0.9 / purchase_propensity -0.2
- temporal_drift: latent p/logit += drift_rate * days_since_simulated_start * sign_hash
  drift_rate 0.003/day (~0.27/90d), sign_hash = hash(fan:{fid}:drift_sign)>0.5?+1:-1, v1
- adversarial: content_affinity weight 0.8 before day45 else -0.8

Behavior overrides keep 16 FEATURES, hidden_payload not leaked.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from simulation.world import SyntheticFan

DRIFT_MODEL_VERSION = "v1"
DRIFT_RATE = 0.003  # per day
ADVERSARIAL_THRESHOLD_DAYS = 45
ADVERSARIAL_WEIGHT_BEFORE = 0.8
ADVERSARIAL_WEIGHT_AFTER = -0.8


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


class DriftModel:
    """Deterministic drift: drift = drift_rate * days * sign_hash per fan.

    Formula (P17 temporal_drift): latent p/logit delta += drift_rate * days_since_simulated_start * sign_hash
    sign_hash = 1 if hash(fan:{fid}:drift_sign) >0.5 else -1
    Deterministic via SHA256(seed:simulation_id:fan:{fid}:drift_sign:counter).
    Bounded via clamp when applied to probability.
    """

    def __init__(
        self,
        seed: int,
        simulation_id: str,
        simulated_start: datetime,
        drift_rate: float = DRIFT_RATE,
        model_version: str = DRIFT_MODEL_VERSION,
    ) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool) or seed <= 0:
            raise ValueError("seed must be positive int")
        if not isinstance(simulation_id, str) or not simulation_id.strip():
            raise ValueError("simulation_id required")
        if not isinstance(simulated_start, datetime) or simulated_start.tzinfo is None:
            raise ValueError("simulated_start must be tz-aware")
        if not isinstance(drift_rate, (float, int)) or not isinstance(drift_rate, float) and not isinstance(drift_rate, int):
            raise ValueError("drift_rate must be numeric")
        self.seed = int(seed)
        self.simulation_id = str(simulation_id)
        self.simulated_start = simulated_start
        self.drift_rate = float(drift_rate)
        self.model_version = str(model_version)
        self._cache: dict[tuple[int, str], float] = {}

    def drift_for(self, fan: SyntheticFan, evaluated_at: datetime) -> float:
        """Return deterministic drift delta for fan at evaluated_at."""
        if not isinstance(fan, SyntheticFan):
            raise ValueError("fan must be SyntheticFan")
        if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        key = (int(fan.fan_id), evaluated_at.isoformat())
        if key in self._cache:
            return self._cache[key]
        days = (evaluated_at - self.simulated_start).total_seconds() / 86400.0
        if days < 0:
            days = 0.0
        fid = int(fan.fan_id)
        sign_hash = _hash_float(self.seed, self.simulation_id, f"fan:{fid}:drift_sign:{self.model_version}", 1)
        sign = 1 if sign_hash > 0.5 else -1
        drift = sign * self.drift_rate * float(days)
        self._cache[key] = float(drift)
        return float(drift)

    def sign_for(self, fan: SyntheticFan) -> int:
        """Return sign_hash for fan (+1/-1) independent of time."""
        fid = int(fan.fan_id)
        h = _hash_float(self.seed, self.simulation_id, f"fan:{fid}:drift_sign:{self.model_version}", 1)
        return 1 if h > 0.5 else -1

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "simulation_id": self.simulation_id,
            "simulated_start": self.simulated_start.isoformat(),
            "drift_rate": self.drift_rate,
            "model_version": self.model_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DriftModel:
        start = data["simulated_start"]
        if isinstance(start, str):
            start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        return cls(
            seed=int(data["seed"]),
            simulation_id=str(data["simulation_id"]),
            simulated_start=start,
            drift_rate=float(data.get("drift_rate", DRIFT_RATE)),
            model_version=str(data.get("model_version", DRIFT_MODEL_VERSION)),
        )


def whale_biased_behavior(behavior: Any, fan: SyntheticFan, seed: int, simulation_id: str) -> Any:
    """Apply whale heavy-tail bias: purchase_propensity +0.25, price_sensitivity -0.3 (clamped).

    Deterministic via trait hash bias but fixed offsets per P17 whales assumption.
    Returns new FanBehavior with adjusted traits (version retained).
    """
    from simulation.behavior import FanBehavior

    if not isinstance(behavior, FanBehavior):
        raise ValueError("behavior must be FanBehavior")
    pp = _clamp01(float(behavior.purchase_propensity) + 0.25)
    ps = _clamp01(float(behavior.price_sensitivity) - 0.3)
    return FanBehavior(
        fan_id=int(behavior.fan_id),
        creator_id=int(behavior.creator_id),
        simulation_id=str(behavior.simulation_id),
        behavior_model_version=str(behavior.behavior_model_version),
        purchase_propensity=float(pp),
        engagement_level=float(behavior.engagement_level),
        relationship_affinity=float(behavior.relationship_affinity),
        price_sensitivity=float(ps),
        freebie_tendency=float(behavior.freebie_tendency),
        content_preference_strength=float(behavior.content_preference_strength),
    )


def freebie_biased_behavior(behavior: Any, fan: SyntheticFan, seed: int, simulation_id: str) -> Any:
    """Apply freebie_heavy bias: freebie_tendency 0.7-0.9 hash, purchase_propensity -0.2.

    Freebie elevated via _hash_float deterministic 0.7-0.9, purchase -0.2 clamped.
    """
    from simulation.behavior import FanBehavior

    if not isinstance(behavior, FanBehavior):
        raise ValueError("behavior must be FanBehavior")
    fid = int(fan.fan_id) if isinstance(fan, SyntheticFan) else int(behavior.fan_id)
    # deterministic hash for freebie range
    r = _hash_float(int(seed), str(simulation_id), f"fan:{fid}:freebie_heavy_bias", 1)
    freebie = 0.7 + r * 0.2  # 0.7-0.9
    freebie = _clamp01(freebie)
    pp = _clamp01(float(behavior.purchase_propensity) - 0.2)
    return FanBehavior(
        fan_id=int(behavior.fan_id),
        creator_id=int(behavior.creator_id),
        simulation_id=str(behavior.simulation_id),
        behavior_model_version=str(behavior.behavior_model_version),
        purchase_propensity=float(pp),
        engagement_level=float(behavior.engagement_level),
        relationship_affinity=float(behavior.relationship_affinity),
        price_sensitivity=float(behavior.price_sensitivity),
        freebie_tendency=float(freebie),
        content_preference_strength=float(behavior.content_preference_strength),
    )


def adversarial_content_weight(evaluated_at: datetime, simulated_start: datetime, base_weight: float = ADVERSARIAL_WEIGHT_BEFORE) -> float:
    """Return adversarial content_affinity weight: 0.8 before 45d else -0.8.

    Deterministic via evaluated_at < simulated_start+45d check.
    OFF-POLICY: misleading historical correlation that flips later.
    """
    if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at must be tz-aware")
    if not isinstance(simulated_start, datetime) or simulated_start.tzinfo is None:
        raise ValueError("simulated_start must be tz-aware")
    days = (evaluated_at - simulated_start).total_seconds() / 86400.0
    if days < ADVERSARIAL_THRESHOLD_DAYS:
        return float(ADVERSARIAL_WEIGHT_BEFORE)
    return float(ADVERSARIAL_WEIGHT_AFTER)


class ScenarioBehaviorModel:
    """Thin wrapper applying scenario bias without mutating base FanBehaviorModel.

    Usage:
        base = FanBehaviorModel(seed, simulation_id)
        wrapped = ScenarioBehaviorModel(base, scenario_id="whales")
        beh = wrapped.for_fan(fan)  # biased if whales/freebie_heavy else base
    """

    def __init__(self, base_model: Any, scenario_id: str = "baseline", seed: int | None = None, simulation_id: str | None = None) -> None:
        from simulation.behavior import FanBehaviorModel

        if not isinstance(base_model, FanBehaviorModel):
            raise ValueError("base_model must be FanBehaviorModel")
        self.base_model = base_model
        self.scenario_id = str(scenario_id).strip() if isinstance(scenario_id, str) else "baseline"
        # seed/simulation_id for bias hash; default to base_model's
        self.seed = int(seed) if seed is not None else int(base_model.seed)
        self.simulation_id = str(simulation_id) if simulation_id is not None and simulation_id.strip() else str(base_model.simulation_id)
        self._cache: dict[int, Any] = {}

    def for_fan(self, fan: SyntheticFan) -> Any:
        fid = int(fan.fan_id)
        if fid in self._cache:
            return self._cache[fid]
        base = self.base_model.for_fan(fan)
        if self.scenario_id == "whales":
            biased = whale_biased_behavior(base, fan, self.seed, self.simulation_id)
        elif self.scenario_id == "freebie_heavy":
            biased = freebie_biased_behavior(base, fan, self.seed, self.simulation_id)
        else:
            biased = base
        self._cache[fid] = biased
        return biased

    def probability_for_fan(self, fan: SyntheticFan, config: Any | None = None) -> float:
        beh = self.for_fan(fan)
        # reuse base probability logic with biased beh
        from simulation.behavior import BehavioralOutcomeConfig
        import math

        cfg = config or BehavioralOutcomeConfig()
        logit = (
            cfg.intercept
            + cfg.purchase_weight * beh.purchase_propensity
            + cfg.engagement_weight * beh.engagement_level
            + cfg.relationship_weight * beh.relationship_affinity
            - cfg.price_sensitivity_weight * beh.price_sensitivity
            - cfg.freebie_weight * beh.freebie_tendency
        )
        # sigmoid
        if logit >= 0:
            p = 1.0 / (1.0 + math.exp(-logit))
        else:
            e = math.exp(logit)
            p = e / (1.0 + e)
        return _clamp01(float(p))

    def clear_cache(self) -> None:
        self._cache.clear()
        self.base_model.clear_cache()


__all__ = [
    "DRIFT_MODEL_VERSION",
    "DRIFT_RATE",
    "ADVERSARIAL_THRESHOLD_DAYS",
    "DriftModel",
    "whale_biased_behavior",
    "freebie_biased_behavior",
    "adversarial_content_weight",
    "ScenarioBehaviorModel",
]
