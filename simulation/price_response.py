"""Price Response — Phase 6 Fan × Price latent interaction.

Deterministic mapping:
    FanBehavior.price_sensitivity (fan latent)
    ×
    Observable offer price (price_minor, currency)
    → PriceResponse (latent penalty/bonus, bounded, monotonic)

Price is observable (price_minor, currency, price_bucket) and MAY appear in
DecisionSnapshot / OptimizationInput / ledger. Latent willingness / price_response
must remain hidden (ground truth separation).

Normalization: reference price 2000 minor units (USD, linear scaling) — simplest
that preserves production semantics, no FX. Documented, versioned.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from simulation.world import SyntheticContent, SyntheticFan

PRICE_RESPONSE_MODEL_VERSION = "v1"
REFERENCE_PRICE_MINOR = 2000
REFERENCE_CURRENCY = "USD"
PRICE_NORMALIZATION_CLAMP_MAX = 5.0  # clamp normalized_price to 5 (≈10000 minor) to keep penalty bounded


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
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


@dataclass(frozen=True)
class PriceResponse:
    """Immutable latent Fan×Price response result. Bounded, monotonic.

    price_minor / currency are observable copies, normalized_price is
    deterministic scaling, response is latent price penalty factor (bounded),
    price_multiplier is the factor applied to price sensitivity in logit.
    """

    fan_id: int
    price_minor: int
    currency: str
    normalized_price: float
    price_multiplier: float
    response: float  # price_sensitivity * normalized_price (bounded 0..5)
    price_response_model_version: str = PRICE_RESPONSE_MODEL_VERSION
    reference_price_minor: int = REFERENCE_PRICE_MINOR

    def __post_init__(self) -> None:
        if not isinstance(self.fan_id, int) or isinstance(self.fan_id, bool) or self.fan_id <= 0:
            raise ValueError("fan_id must be positive int")
        if not isinstance(self.price_minor, int) or isinstance(self.price_minor, bool) or self.price_minor < 0:
            raise ValueError("price_minor must be int >=0")
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValueError("currency required")
        if not isinstance(self.normalized_price, float) or isinstance(self.normalized_price, bool):
            raise ValueError("normalized_price must be float")
        if not 0.0 <= self.normalized_price <= PRICE_NORMALIZATION_CLAMP_MAX + 1e-9:
            raise ValueError(f"normalized_price must be in [0,{PRICE_NORMALIZATION_CLAMP_MAX}], got {self.normalized_price}")
        if not isinstance(self.response, float) or isinstance(self.response, bool):
            raise ValueError("response must be float")
        # response bounded 0..5 (sensitivity 0..1 * normalized 0..5)
        if not 0.0 <= self.response <= PRICE_NORMALIZATION_CLAMP_MAX + 1e-9:
            raise ValueError(f"response must be in [0,{PRICE_NORMALIZATION_CLAMP_MAX}], got {self.response}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fan_id": self.fan_id,
            "price_minor": self.price_minor,
            "currency": self.currency,
            "normalized_price": self.normalized_price,
            "price_multiplier": self.price_multiplier,
            "response": self.response,
            "price_response_model_version": self.price_response_model_version,
            "reference_price_minor": self.reference_price_minor,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PriceResponse:
        return cls(
            fan_id=int(data["fan_id"]),
            price_minor=int(data["price_minor"]),
            currency=str(data["currency"]),
            normalized_price=float(data["normalized_price"]),
            price_multiplier=float(data["price_multiplier"]),
            response=float(data["response"]),
            price_response_model_version=str(data.get("price_response_model_version", PRICE_RESPONSE_MODEL_VERSION)),
            reference_price_minor=int(data.get("reference_price_minor", REFERENCE_PRICE_MINOR)),
        )


class PriceResponseModel:
    """Deterministic Fan×Price response generator.

    Same (seed, simulation_id, fan_id, price_minor, currency, model_version) → same response.
    No global RNG. Fan-level stable.

    Normalization: linear reference price 2000 USD, clamped [0,5].
        normalized_price = min(price_minor / reference_price, 5.0)
        price_multiplier = 1 + (normalized_price - 1)  # deviation, used in outcome integration
        response = price_sensitivity * normalized_price  # bounded 0..5, monotonic

    Currency: USD only in current production; other currencies kept linear without FX (documented).
    """

    def __init__(
        self,
        seed: int,
        simulation_id: str,
        reference_price_minor: int = REFERENCE_PRICE_MINOR,
        reference_currency: str = REFERENCE_CURRENCY,
        price_response_model_version: str = PRICE_RESPONSE_MODEL_VERSION,
    ) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool) or seed <= 0:
            raise ValueError("seed must be positive int")
        if not simulation_id or not isinstance(simulation_id, str):
            raise ValueError("simulation_id required")
        if not isinstance(reference_price_minor, int) or reference_price_minor <= 0:
            raise ValueError("reference_price_minor must be positive int")
        if not reference_currency or not isinstance(reference_currency, str):
            raise ValueError("reference_currency required")
        self.seed = int(seed)
        self.simulation_id = str(simulation_id)
        self.reference_price_minor = int(reference_price_minor)
        self.reference_currency = str(reference_currency).strip().upper()
        self.price_response_model_version = str(price_response_model_version)
        self._cache: dict[tuple[int, int, str], PriceResponse] = {}

    def _normalized(self, price_minor: int, currency: str) -> float:
        # currency check: no FX, linear same reference; document that non-USD uses same 2000 scale
        if not isinstance(price_minor, int) or price_minor < 0:
            raise ValueError("price_minor must be int >=0")
        norm = float(price_minor) / float(self.reference_price_minor)
        if norm < 0:
            norm = 0.0
        if norm > PRICE_NORMALIZATION_CLAMP_MAX:
            norm = PRICE_NORMALIZATION_CLAMP_MAX
        return float(norm)

    def response_for(
        self,
        fan: SyntheticFan,
        price_minor: int,
        currency: str,
        fan_behavior: Any,
    ) -> PriceResponse:
        """Compute deterministic response for fan×price."""
        if not isinstance(fan, SyntheticFan):
            raise ValueError("fan must be SyntheticFan")
        if not isinstance(price_minor, int) or isinstance(price_minor, bool) or price_minor < 0:
            raise ValueError("price_minor must be int >=0")
        if not isinstance(currency, str) or not currency.strip():
            raise ValueError("currency required")
        try:
            sensitivity = float(getattr(fan_behavior, "price_sensitivity"))
        except Exception as exc:
            raise ValueError(f"fan_behavior missing price_sensitivity: {exc}") from exc
        if not 0.0 <= sensitivity <= 1.0:
            raise ValueError("price_sensitivity must be in [0,1]")

        key = (int(fan.fan_id), int(price_minor), str(currency).strip().upper())
        if key in self._cache:
            cached = self._cache[key]
            # verify sensitivity? sensitivity is per fan stable, so cache hit valid if same fan
            # need to recompute response if sensitivity differs? fan_behavior stable so ok
            # But if caller passes different sensitivity for same fan (should not happen deterministic), recompute
            # Quick check: response should equal sensitivity * normalized; if cached response != expected, recompute
            expected_resp = _clamp01(sensitivity) * cached.normalized_price  # but clamp?
            # Actually response = sensitivity * normalized_price, bounded 0..5, but normalized already clamped
            # We'll recompute if sensitivity mismatched beyond tolerance
            if abs(cached.response - sensitivity * cached.normalized_price) > 1e-9:
                # stale due to different sensitivity, recompute
                pass
            else:
                return cached

        normalized = self._normalized(price_minor, currency)
        # price_multiplier for outcome integration: deviation logic kept in outcome model, but store 1 + (normalized-1) = normalized
        # For simplicity, price_multiplier = normalized (at reference 1 => multiplier 1)
        price_multiplier = float(normalized)
        # response latent: sensitivity * normalized (bounded)
        resp = float(sensitivity * normalized)
        # clamp response to max
        if resp > PRICE_NORMALIZATION_CLAMP_MAX:
            resp = PRICE_NORMALIZATION_CLAMP_MAX
        # also ensure not negative
        if resp < 0:
            resp = 0.0

        pr = PriceResponse(
            fan_id=int(fan.fan_id),
            price_minor=int(price_minor),
            currency=str(currency).strip().upper(),
            normalized_price=float(normalized),
            price_multiplier=float(price_multiplier),
            response=float(resp),
            price_response_model_version=self.price_response_model_version,
            reference_price_minor=self.reference_price_minor,
        )
        self._cache[key] = pr
        return pr

    def response_for_content(
        self,
        fan: SyntheticFan,
        content: SyntheticContent,
        fan_behavior: Any,
    ) -> PriceResponse:
        """Convenience: derive price from SyntheticContent."""
        if not isinstance(content, SyntheticContent):
            raise ValueError("content must be SyntheticContent")
        return self.response_for(fan, int(content.price_minor), str(content.currency), fan_behavior)

    def clear_cache(self) -> None:
        self._cache.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "simulation_id": self.simulation_id,
            "reference_price_minor": self.reference_price_minor,
            "reference_currency": self.reference_currency,
            "price_response_model_version": self.price_response_model_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PriceResponseModel:
        return cls(
            seed=int(data["seed"]),
            simulation_id=str(data["simulation_id"]),
            reference_price_minor=int(data.get("reference_price_minor", REFERENCE_PRICE_MINOR)),
            reference_currency=str(data.get("reference_currency", REFERENCE_CURRENCY)),
            price_response_model_version=str(data.get("price_response_model_version", PRICE_RESPONSE_MODEL_VERSION)),
        )


# ---------------------------------------------------------------------------
# Outcome integration — Behavioral + Content + Price
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PriceAwareOutcomeConfig:
    """Transparent coefficients for fan×content×price purchase probability.

    logit(p) = intercept
               + purchase_weight * purchase_propensity
               + engagement_weight * engagement_level
               + relationship_weight * relationship_affinity
               - freebie_weight * freebie_tendency
               + content_affinity_weight * content_affinity
               + price_term

    price_term = - price_sensitivity_weight * price_sensitivity               when price_response_weight == 0 (identical to prior C model)
               = - price_sensitivity_weight * price_sensitivity
                 - price_response_weight * price_sensitivity * (normalized_price - 1)
                 when price_response_weight > 0

    Interpretation: baseline price sensitivity at reference price (2000) plus
    deviation scaled by price_response_weight. At reference price, extra 0 → identical to prior.
    Low price → negative deviation → less penalty (higher p). High price → positive deviation → more penalty (lower p).
    Bounded, monotonic, heterogeneity via price_sensitivity, transparent, deterministic.
    """

    intercept: float = -1.0
    purchase_weight: float = 1.5
    engagement_weight: float = 0.8
    relationship_weight: float = 0.6
    price_sensitivity_weight: float = 0.8
    freebie_weight: float = 0.9
    content_affinity_weight: float = 0.8
    price_response_weight: float = 0.6
    reference_price_minor: int = REFERENCE_PRICE_MINOR

    def __post_init__(self) -> None:
        for name in (
            "intercept",
            "purchase_weight",
            "engagement_weight",
            "relationship_weight",
            "price_sensitivity_weight",
            "freebie_weight",
            "content_affinity_weight",
            "price_response_weight",
        ):
            v = getattr(self, name)
            if not isinstance(v, (float, int)) or isinstance(v, bool):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(float(v)):
                raise ValueError(f"{name} must be finite")
        if not isinstance(self.reference_price_minor, int) or self.reference_price_minor <= 0:
            raise ValueError("reference_price_minor must be positive int")


class PriceAwareBehavioralOutcomeModel:
    """Composable behavioral+content+price outcome model.

    Preserves Baseline, Behavioral, ContentAware via shared SimulatedOutcome.
    With price_response_weight == 0 → identical to ContentAware (no price effect).
    With positive weight → monotonic price effect, heterogeneous via price_sensitivity,
    modest (not dominating), deterministic stochastic.
    """

    def __init__(
        self,
        behavior_model: Any,
        affinity_model: Any,
        price_model: PriceResponseModel,
        outcome_config: PriceAwareOutcomeConfig | None = None,
        purchase_delay_hours_min: int = 1,
        purchase_delay_hours_max: int = 48,
        non_purchase_outcome: str = "DECLINED",
    ) -> None:
        from simulation.behavior import FanBehaviorModel
        from simulation.content_affinity import ContentAffinityModel

        if not isinstance(behavior_model, FanBehaviorModel):
            raise ValueError("behavior_model must be FanBehaviorModel")
        if not isinstance(affinity_model, ContentAffinityModel):
            raise ValueError("affinity_model must be ContentAffinityModel")
        if not isinstance(price_model, PriceResponseModel):
            raise ValueError("price_model must be PriceResponseModel")
        self.behavior_model = behavior_model
        self.affinity_model = affinity_model
        self.price_model = price_model
        self.outcome_config = outcome_config or PriceAwareOutcomeConfig()
        if purchase_delay_hours_min < 0 or purchase_delay_hours_max < purchase_delay_hours_min:
            raise ValueError("invalid purchase_delay range")
        if non_purchase_outcome not in ("DECLINED", "EXPIRED"):
            raise ValueError("non_purchase_outcome must be DECLINED or EXPIRED")
        self.purchase_delay_hours_min = int(purchase_delay_hours_min)
        self.purchase_delay_hours_max = int(purchase_delay_hours_max)
        self.non_purchase_outcome = non_purchase_outcome

    def probability_for(
        self,
        fan: SyntheticFan,
        content: SyntheticContent,
    ) -> float:
        """Compute p = sigmoid(logit) for fan×content×price (latent)."""
        beh = self.behavior_model.for_fan(fan)
        affinity = self.affinity_model.affinity_for(fan, content, beh)
        pr = self.price_model.response_for_content(fan, content, beh)
        cfg = self.outcome_config
        # base logit without price deviation
        logit = (
            cfg.intercept
            + cfg.purchase_weight * beh.purchase_propensity
            + cfg.engagement_weight * beh.engagement_level
            + cfg.relationship_weight * beh.relationship_affinity
            - cfg.freebie_weight * beh.freebie_tendency
            + cfg.content_affinity_weight * affinity.score
        )
        # price term
        if cfg.price_response_weight == 0:
            # identical to prior C model: static penalty
            logit += - cfg.price_sensitivity_weight * beh.price_sensitivity
        else:
            # baseline + deviation
            # normalized_price already computed, deviation = normalized -1
            deviation = pr.normalized_price - 1.0
            # clamp deviation to avoid extreme? already normalized clamped 0..5 => deviation -1..4
            logit += - cfg.price_sensitivity_weight * beh.price_sensitivity - cfg.price_response_weight * beh.price_sensitivity * deviation
        return _clamp01(_sigmoid(float(logit)))

    def decide(
        self,
        opportunity: Any,
        fan: SyntheticFan,
        content: SyntheticContent,
        *,
        seed: int,
        run_id: str,
        counter: int,
    ) -> tuple[Any, Any, dict[str, Any]]:
        """Decide outcome for opportunity given fan, content, price.

        Returns (SimulatedOutcome, GroundTruthReference, hidden_payload) where
        hidden contains behavior, affinity, price_response, latent probability.
        """
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        from simulation.ground_truth import GroundTruthReference as _GTR

        try:
            opp_id = int(getattr(opportunity, "opportunity_id"))
            gen = str(getattr(opportunity, "generation_id"))
            eval_at = getattr(opportunity, "evaluated_at")
        except Exception as exc:
            raise ValueError(f"opportunity invalid: {exc}") from exc
        if fan is None or content is None:
            raise ValueError("fan and content are required for price-aware outcome")

        beh = self.behavior_model.for_fan(fan)
        affinity = self.affinity_model.affinity_for(fan, content, beh)
        pr = self.price_model.response_for_content(fan, content, beh)
        p = self.probability_for(fan, content)
        r = _hash_float(seed, run_id, f"price_aware_outcome:{opp_id}", counter)
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
            delay_h_int = int(delay_h * delay_range) + self.purchase_delay_hours_min
            delay_m = int(_hash_float(seed, run_id, f"purchase_min:{opp_id}", counter + 2000) * 60)
            purchase_at = sent_at + _td(hours=int(delay_h_int), minutes=int(delay_m))
            outcome_at = purchase_at
            transaction_id = f"txn:sym:{run_id}:{opp_id}"
        maturity_at = outcome_at

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
        hidden: dict[str, Any] = {
            "latent_purchase_probability": float(p),
            "rng_value": float(r),
            "outcome": outcome_state,
            "purchased": bool(purchased),
            "purchase_at": purchase_at.isoformat() if purchase_at else None,
            "sent_at": sent_at.isoformat(),
            "behavior": beh.to_dict(),
            "content_affinity": affinity.to_dict(),
            "content_affinity_score": float(affinity.score),
            "fan_preference_vector": self.affinity_model.preferences_for_fan(fan),
            "price_response": pr.to_dict(),
            "price_minor": int(content.price_minor),
            "currency": str(content.currency),
            "normalized_price": float(pr.normalized_price),
            "price_response_value": float(pr.response),
            "price_multiplier": float(pr.price_multiplier),
            "fan_id": int(fan.fan_id),
            "content_id": str(content.content_id),
            "price_sensitivity": float(beh.price_sensitivity),
            "price_response_model_version": self.price_model.price_response_model_version,
            "content_affinity_model_version": self.affinity_model.content_affinity_model_version,
            "behavior_model_version": self.behavior_model.behavior_model_version,
            "price_response_weight": float(self.outcome_config.price_response_weight),
            "price_sensitivity_weight": float(self.outcome_config.price_sensitivity_weight),
            "reference_price_minor": int(self.outcome_config.reference_price_minor),
        }
        return outcome, ref, hidden


__all__ = [
    "PRICE_RESPONSE_MODEL_VERSION",
    "REFERENCE_PRICE_MINOR",
    "REFERENCE_CURRENCY",
    "PriceResponse",
    "PriceResponseModel",
    "PriceAwareOutcomeConfig",
    "PriceAwareBehavioralOutcomeModel",
]
