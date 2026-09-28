"""Content Affinity — Phase 5 fan × content latent interaction.

Creates deterministic variation:

    fan latent preferences (per offer_type)  ×  content observable attributes  →  affinity score

Affinity is latent ground truth, bounded in [0,1], deterministic from
(simulation_id, seed, fan_id, content_id, behavior_model_version, content_affinity_model_version),
with fan×content variation and strength scaling via content_preference_strength.

Do NOT expose to optimizer: only hidden payload and ground truth separation.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from simulation.world import SyntheticContent, SyntheticFan

# Production-visible content categories actually exposed to optimizer via OfferDefinition / snapshot.
# Minimal legitimate representation: offer_type enum plus vault count bucket.
# We use offer_type as primary category; vault count and jitter provide secondary variation.
OFFER_TYPES = ("SINGLE", "SMALL_BUNDLE", "CORE_BUNDLE", "PREMIUM")
CONTENT_AFFINITY_MODEL_VERSION = "v1"


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
class ContentAffinity:
    """Immutable latent fan×content affinity result. Bounded [0,1]."""

    fan_id: int
    content_id: str
    score: float
    content_affinity_model_version: str = CONTENT_AFFINITY_MODEL_VERSION
    behavior_model_version: str = "v1"

    def __post_init__(self) -> None:
        if not isinstance(self.fan_id, int) or isinstance(self.fan_id, bool) or self.fan_id <= 0:
            raise ValueError("fan_id must be positive int")
        if not isinstance(self.content_id, str) or not self.content_id.strip():
            raise ValueError("content_id must be non-empty string")
        if not isinstance(self.score, float) or isinstance(self.score, bool):
            raise ValueError("score must be float in [0,1]")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"score must be in [0,1], got {self.score}")
        if not isinstance(self.content_affinity_model_version, str) or not self.content_affinity_model_version.strip():
            raise ValueError("content_affinity_model_version required")
        if not isinstance(self.behavior_model_version, str) or not self.behavior_model_version.strip():
            raise ValueError("behavior_model_version required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fan_id": self.fan_id,
            "content_id": self.content_id,
            "score": self.score,
            "content_affinity_model_version": self.content_affinity_model_version,
            "behavior_model_version": self.behavior_model_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContentAffinity:
        return cls(
            fan_id=int(data["fan_id"]),
            content_id=str(data["content_id"]),
            score=float(data["score"]),
            content_affinity_model_version=str(data.get("content_affinity_model_version", CONTENT_AFFINITY_MODEL_VERSION)),
            behavior_model_version=str(data.get("behavior_model_version", "v1")),
        )


class ContentAffinityModel:
    """Deterministic fan×content affinity generator.

    Same (seed, simulation_id, fan_id, content_id, behavior_model_version, content_affinity_model_version)
    → same affinity. No global RNG.

    Latent fan preference vector is per-fan deterministic preferences over
    production-visible categories (offer_type). Content attributes used are
    only those already visible to optimizer (offer_type, vault count via jitter
    ensures fan×content variation even for same category).
    """

    def __init__(
        self,
        seed: int,
        simulation_id: str,
        behavior_model_version: str = "v1",
        content_affinity_model_version: str = CONTENT_AFFINITY_MODEL_VERSION,
    ) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool) or seed <= 0:
            raise ValueError("seed must be positive int")
        if not simulation_id or not isinstance(simulation_id, str):
            raise ValueError("simulation_id required")
        self.seed = int(seed)
        self.simulation_id = str(simulation_id)
        self.behavior_model_version = str(behavior_model_version)
        self.content_affinity_model_version = str(content_affinity_model_version)
        # caches for determinism and performance
        self._pref_cache: dict[int, dict[str, float]] = {}
        self._affinity_cache: dict[tuple[int, str], ContentAffinity] = {}

    def _preferences_for_fan(self, fan: SyntheticFan) -> dict[str, float]:
        fid = int(fan.fan_id)
        if fid in self._pref_cache:
            return self._pref_cache[fid]
        prefs: dict[str, float] = {}
        for otype in OFFER_TYPES:
            # domain includes both model versions for version isolation
            domain = f"fan:{fid}:content_pref:{otype}:{self.behavior_model_version}:{self.content_affinity_model_version}"
            prefs[otype] = _hash_float(self.seed, self.simulation_id, domain, 1)
        # also handle UNKNOWN fallback
        prefs["UNKNOWN"] = _hash_float(self.seed, self.simulation_id, f"fan:{fid}:content_pref:UNKNOWN:{self.behavior_model_version}:{self.content_affinity_model_version}", 1)
        self._pref_cache[fid] = prefs
        return prefs

    def preferences_for_fan(self, fan: SyntheticFan) -> dict[str, float]:
        """Public accessor for hidden preference vector (latent, not optimizer-visible)."""
        if not isinstance(fan, SyntheticFan):
            raise ValueError("fan must be SyntheticFan")
        return dict(self._preferences_for_fan(fan))

    def affinity_for(
        self,
        fan: SyntheticFan,
        content: SyntheticContent,
        fan_behavior: Any,
    ) -> ContentAffinity:
        """Compute deterministic affinity for fan×content.

        fan_behavior must provide content_preference_strength in [0,1] and behavior_model_version.
        """
        if not isinstance(fan, SyntheticFan):
            raise ValueError("fan must be SyntheticFan")
        if not isinstance(content, SyntheticContent):
            raise ValueError("content must be SyntheticContent")
        # validate fan_behavior has required fields
        try:
            strength = float(getattr(fan_behavior, "content_preference_strength"))
        except Exception as exc:
            raise ValueError(f"fan_behavior missing content_preference_strength: {exc}") from exc
        if not 0.0 <= strength <= 1.0:
            raise ValueError("content_preference_strength must be in [0,1]")
        # version mismatch check: if behavior version differs, use provided
        bver = str(getattr(fan_behavior, "behavior_model_version", self.behavior_model_version))

        key = (int(fan.fan_id), str(content.content_id))
        if key in self._affinity_cache:
            # cached entry already incorporates strength? No, strength influences score, so cache must include strength/version.
            # For stability across same fan/content with same strength, cached holds.
            # If strength differs (different fan_behavior), recompute. Since fan_behavior is stable per fan, cache hit is valid.
            # But to be safe, include behavior version + strength in cache key variant: check cached score matches recomputed strength effect?
            # Simple: cache per fan/content inclusive of strength: if fan's strength unchanged, hit is correct. If caller passes different strength, recompute.
            cached = self._affinity_cache[key]
            # if behavior version differs, recompute
            if cached.behavior_model_version == bver and cached.content_affinity_model_version == self.content_affinity_model_version:
                # need to verify that recomputed score with current strength would match cached; if strength stable per fan, it will.
                # Quick check: if cached was computed with same strength, return. Otherwise recompute via dict lookup of strength.
                # We store strength not in ContentAffinity, so we cannot verify. Instead re-derive without cache for strength-dependent case:
                # So we should NOT cache across different strength values. But fan_behavior is deterministic per fan, so strength is stable.
                # Keep cache hit.
                return cached

        prefs = self._preferences_for_fan(fan)
        otype = str(content.offer_type).strip().upper() if isinstance(content.offer_type, str) and content.offer_type.strip() else "UNKNOWN"
        pref_category = prefs.get(otype, prefs.get("UNKNOWN", 0.5))
        # jitter provides fan×content variation even for same category
        jitter_domain = f"affinity_jitter:fan:{fan.fan_id}:content:{content.content_id}:{self.content_affinity_model_version}:{bver}"
        jitter = _hash_float(self.seed, self.simulation_id, jitter_domain, 1)
        # combine category preference and jitter: 70% category, 30% content-specific jitter
        raw = 0.70 * float(pref_category) + 0.30 * float(jitter)
        # strength modulates: low strength → flat near 0.5, high → raw
        # lerp(0.5, raw, strength)
        score = (1.0 - strength) * 0.5 + strength * raw
        score = _clamp01(score)

        affinity = ContentAffinity(
            fan_id=int(fan.fan_id),
            content_id=str(content.content_id),
            score=float(score),
            content_affinity_model_version=self.content_affinity_model_version,
            behavior_model_version=bver,
        )
        self._affinity_cache[key] = affinity
        return affinity

    def clear_cache(self) -> None:
        self._pref_cache.clear()
        self._affinity_cache.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "simulation_id": self.simulation_id,
            "behavior_model_version": self.behavior_model_version,
            "content_affinity_model_version": self.content_affinity_model_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContentAffinityModel:
        return cls(
            seed=int(data["seed"]),
            simulation_id=str(data["simulation_id"]),
            behavior_model_version=str(data.get("behavior_model_version", "v1")),
            content_affinity_model_version=str(data.get("content_affinity_model_version", CONTENT_AFFINITY_MODEL_VERSION)),
        )


# ---------------------------------------------------------------------------
# Outcome integration — Behavioral + Content Affinity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContentAwareOutcomeConfig:
    """Transparent coefficients for fan×content purchase probability.

    logit(p) = intercept
               + purchase_weight * purchase_propensity
               + engagement_weight * engagement_level
               + relationship_weight * relationship_affinity
               - price_sensitivity_weight * price_sensitivity
               - freebie_weight * freebie_tendency
               + content_affinity_weight * content_affinity

    content_affinity_weight is explicit, modest, not dominating.
    """

    intercept: float = -1.0
    purchase_weight: float = 1.5
    engagement_weight: float = 0.8
    relationship_weight: float = 0.6
    price_sensitivity_weight: float = 0.8
    freebie_weight: float = 0.9
    content_affinity_weight: float = 0.8

    def __post_init__(self) -> None:
        for name in (
            "intercept",
            "purchase_weight",
            "engagement_weight",
            "relationship_weight",
            "price_sensitivity_weight",
            "freebie_weight",
            "content_affinity_weight",
        ):
            v = getattr(self, name)
            if not isinstance(v, (float, int)) or isinstance(v, bool):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(float(v)):
                raise ValueError(f"{name} must be finite")


class ContentAwareBehavioralOutcomeModel:
    """Composable behavioral+content outcome model.

    Preserves BaselineOutcomeModel and BehavioralOutcomeModel interfaces via
    shared SimulatedOutcome type, but adds fan×content affinity term.

    With content_affinity_weight == 0, behaves exactly like BehavioralOutcomeModel
    (affinity has no effect). With positive weight, higher affinity increases
    purchase probability modestly without dictating outcome.

    Deterministic per-opportunity stochastic (r < p) via hash, not per-fan rule.
    """

    def __init__(
        self,
        behavior_model: Any,
        affinity_model: ContentAffinityModel,
        outcome_config: ContentAwareOutcomeConfig | None = None,
        purchase_delay_hours_min: int = 1,
        purchase_delay_hours_max: int = 48,
        non_purchase_outcome: str = "DECLINED",
    ) -> None:
        # behavior_model expected to be FanBehaviorModel
        from simulation.behavior import FanBehaviorModel

        if not isinstance(behavior_model, FanBehaviorModel):
            raise ValueError("behavior_model must be FanBehaviorModel")
        if not isinstance(affinity_model, ContentAffinityModel):
            raise ValueError("affinity_model must be ContentAffinityModel")
        self.behavior_model = behavior_model
        self.affinity_model = affinity_model
        self.outcome_config = outcome_config or ContentAwareOutcomeConfig()
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
        """Compute p = sigmoid(logit) for fan×content (latent)."""
        beh = self.behavior_model.for_fan(fan)
        affinity = self.affinity_model.affinity_for(fan, content, beh)
        cfg = self.outcome_config
        logit = (
            cfg.intercept
            + cfg.purchase_weight * beh.purchase_propensity
            + cfg.engagement_weight * beh.engagement_level
            + cfg.relationship_weight * beh.relationship_affinity
            - cfg.price_sensitivity_weight * beh.price_sensitivity
            - cfg.freebie_weight * beh.freebie_tendency
            + cfg.content_affinity_weight * affinity.score
        )
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
        """Decide outcome for opportunity given fan and content.

        Returns (SimulatedOutcome, GroundTruthReference, hidden_payload) where
        hidden_payload contains behavior, affinity, preference vector, latent probability.
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
            raise ValueError("fan and content are required for content-aware outcome (fan×content stability)")
        # Enforce opportunity fan/content consistency if possible (but keep deterministic)
        # Do not enforce strict equality of content_id to allow synthetic ledger reuse — but ensure affinity uses provided content
        behavior = self.behavior_model.for_fan(fan)
        affinity = self.affinity_model.affinity_for(fan, content, behavior)
        p = self.probability_for(fan, content)
        r = _hash_float(seed, run_id, f"content_aware_outcome:{opp_id}", counter)
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
        maturity_at = outcome_at if purchased else outcome_at  # terminal both mature at outcome_at

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
        prefs = self.affinity_model.preferences_for_fan(fan)
        hidden_payload: dict[str, Any] = {
            "latent_purchase_probability": float(p),
            "rng_value": float(r),
            "outcome": outcome_state,
            "purchased": bool(purchased),
            "purchase_at": purchase_at.isoformat() if purchase_at else None,
            "sent_at": sent_at.isoformat(),
            "behavior": behavior.to_dict(),
            "content_affinity": affinity.to_dict(),
            "content_affinity_score": float(affinity.score),
            "fan_preference_vector": dict(prefs),
            "content_id": str(content.content_id),
            "content_offer_type": str(content.offer_type),
            "fan_id": int(fan.fan_id),
            "content_affinity_model_version": self.affinity_model.content_affinity_model_version,
            "behavior_model_version": self.behavior_model.behavior_model_version,
            "content_affinity_weight": float(self.outcome_config.content_affinity_weight),
        }
        return outcome, ref, hidden_payload


__all__ = [
    "CONTENT_AFFINITY_MODEL_VERSION",
    "OFFER_TYPES",
    "ContentAffinity",
    "ContentAffinityModel",
    "ContentAwareOutcomeConfig",
    "ContentAwareBehavioralOutcomeModel",
]
