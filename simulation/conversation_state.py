"""Conversation State, Timing & Fatigue — Phase 7.

Observable state (may appear in DecisionSnapshot / OptimizationInput):
- conversation: lifecycle, current_topic, recent_topics, open_threads (4 fields)
- fan: purchase_count, total_spend, recent_offer_count, last_offer_at, etc.
- history: total_offer_count, recent_offer_count, last_offer_at, declined counts, state_counts
- evaluated_at (UTC), price_minor/currency (from Phase 6)

Latent state (must remain hidden, ground-truth only):
- engagement_score ∈[0,1] continuous
- recency_score ∈[0,1]
- activity_score ∈[0,1]
- fatigue_score ∈[0,1] with exponential decay
- time_effect ∈[0,1] (weekend/hour)
- latent receptiveness

All deterministic via SHA256( seed, simulation_id, domain, counter, version ).
No global RNG, no wall-clock, replayable.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from simulation.world import SyntheticFan

CONVERSATION_MODEL_VERSION = "v1"
FATIGUE_MODEL_VERSION = "v1"
TIME_MODEL_VERSION = "v1"


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
class ConversationState:
    """Latent + observable-derived conversation state. Continuous scores."""

    fan_id: int
    evaluated_at: datetime
    engagement_score: float  # [0,1] continuous
    recency_score: float  # [0,1] 1 = very recent
    activity_score: float  # [0,1] recent interaction count normalized
    conversation_age_hours: float  # hours since first interaction (bounded)
    recent_interaction_count: int  # 0..5
    derived_label: str  # COLD/WARM/HOT derived, not primary
    conversation_model_version: str = CONVERSATION_MODEL_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.fan_id, int) or isinstance(self.fan_id, bool) or self.fan_id <= 0:
            raise ValueError("fan_id must be positive int")
        if not isinstance(self.evaluated_at, datetime) or self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        for name in ("engagement_score", "recency_score", "activity_score"):
            v = getattr(self, name)
            if not isinstance(v, float) or isinstance(v, bool) or not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} must be float in [0,1]")
        if self.derived_label not in ("COLD", "WARM", "HOT"):
            raise ValueError("derived_label must be COLD/WARM/HOT")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fan_id": self.fan_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "engagement_score": self.engagement_score,
            "recency_score": self.recency_score,
            "activity_score": self.activity_score,
            "conversation_age_hours": self.conversation_age_hours,
            "recent_interaction_count": self.recent_interaction_count,
            "derived_label": self.derived_label,
            "conversation_model_version": self.conversation_model_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationState:
        ev = data["evaluated_at"]
        if isinstance(ev, str):
            ev = datetime.fromisoformat(ev.replace("Z", "+00:00"))
        return cls(
            fan_id=int(data["fan_id"]),
            evaluated_at=ev,
            engagement_score=float(data["engagement_score"]),
            recency_score=float(data["recency_score"]),
            activity_score=float(data["activity_score"]),
            conversation_age_hours=float(data["conversation_age_hours"]),
            recent_interaction_count=int(data["recent_interaction_count"]),
            derived_label=str(data["derived_label"]),
            conversation_model_version=str(data.get("conversation_model_version", CONVERSATION_MODEL_VERSION)),
        )


@dataclass(frozen=True)
class FatigueState:
    """Latent fatigue / saturation. Bounded [0,1], decays over time."""

    fan_id: int
    evaluated_at: datetime
    fatigue_score: float  # [0,1] higher = more saturated
    recent_offer_count: int
    recent_declined_count: int
    hours_since_last_offer: float | None
    hours_since_last_purchase: float | None
    decay_rate: float
    fatigue_model_version: str = FATIGUE_MODEL_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.fan_id, int) or isinstance(self.fan_id, bool) or self.fan_id <= 0:
            raise ValueError("fan_id must be positive int")
        if not isinstance(self.evaluated_at, datetime) or self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        if not isinstance(self.fatigue_score, float) or isinstance(self.fatigue_score, bool) or not 0.0 <= self.fatigue_score <= 1.0:
            raise ValueError("fatigue_score must be float in [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fan_id": self.fan_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "fatigue_score": self.fatigue_score,
            "recent_offer_count": self.recent_offer_count,
            "recent_declined_count": self.recent_declined_count,
            "hours_since_last_offer": self.hours_since_last_offer,
            "hours_since_last_purchase": self.hours_since_last_purchase,
            "decay_rate": self.decay_rate,
            "fatigue_model_version": self.fatigue_model_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FatigueState:
        ev = data["evaluated_at"]
        if isinstance(ev, str):
            ev = datetime.fromisoformat(ev.replace("Z", "+00:00"))
        return cls(
            fan_id=int(data["fan_id"]),
            evaluated_at=ev,
            fatigue_score=float(data["fatigue_score"]),
            recent_offer_count=int(data["recent_offer_count"]),
            recent_declined_count=int(data["recent_declined_count"]),
            hours_since_last_offer=data.get("hours_since_last_offer"),
            hours_since_last_purchase=data.get("hours_since_last_purchase"),
            decay_rate=float(data.get("decay_rate", 0.05)),
            fatigue_model_version=str(data.get("fatigue_model_version", FATIGUE_MODEL_VERSION)),
        )


@dataclass(frozen=True)
class TimeContext:
    """Observable time context derived from evaluated_at UTC. No local timezone invented."""

    evaluated_at: datetime
    hour_of_day: int  # 0..23 UTC
    day_of_week: int  # 0=Mon..6=Sun UTC
    is_weekend: bool
    timezone: str  # always UTC / unavailable
    time_effect: float  # bounded [0,1] latent time receptiveness
    time_model_version: str = TIME_MODEL_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.evaluated_at, datetime) or self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        if not 0 <= self.hour_of_day <= 23:
            raise ValueError("hour_of_day must be 0..23")
        if not 0 <= self.day_of_week <= 6:
            raise ValueError("day_of_week must be 0..6")
        if not isinstance(self.time_effect, float) or not 0.0 <= self.time_effect <= 1.0:
            raise ValueError("time_effect must be float in [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated_at": self.evaluated_at.isoformat(),
            "hour_of_day": self.hour_of_day,
            "day_of_week": self.day_of_week,
            "is_weekend": self.is_weekend,
            "timezone": self.timezone,
            "time_effect": self.time_effect,
            "time_model_version": self.time_model_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TimeContext:
        ev = data["evaluated_at"]
        if isinstance(ev, str):
            ev = datetime.fromisoformat(ev.replace("Z", "+00:00"))
        return cls(
            evaluated_at=ev,
            hour_of_day=int(data["hour_of_day"]),
            day_of_week=int(data["day_of_week"]),
            is_weekend=bool(data["is_weekend"]),
            timezone=str(data.get("timezone", "UTC")),
            time_effect=float(data["time_effect"]),
            time_model_version=str(data.get("time_model_version", TIME_MODEL_VERSION)),
        )


@dataclass(frozen=True)
class HistoryState:
    """Per-fan chronological history (observable-derived)."""

    fan_id: int
    creator_id: int
    events: tuple[dict[str, Any], ...]  # sorted ascending by at
    total_offer_count: int
    recent_offer_count: int
    last_offer_at: datetime | None
    last_purchase_at: datetime | None
    recent_declined_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "fan_id": self.fan_id,
            "creator_id": self.creator_id,
            "events": list(self.events),
            "total_offer_count": self.total_offer_count,
            "recent_offer_count": self.recent_offer_count,
            "last_offer_at": self.last_offer_at.isoformat() if self.last_offer_at else None,
            "last_purchase_at": self.last_purchase_at.isoformat() if self.last_purchase_at else None,
            "recent_declined_count": self.recent_declined_count,
        }


class ConversationStateModel:
    """Deterministic conversation state generator."""

    def __init__(self, seed: int, simulation_id: str, model_version: str = CONVERSATION_MODEL_VERSION) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool) or seed <= 0:
            raise ValueError("seed must be positive int")
        if not simulation_id or not isinstance(simulation_id, str):
            raise ValueError("simulation_id required")
        self.seed = int(seed)
        self.simulation_id = str(simulation_id)
        self.model_version = str(model_version)
        self._cache: dict[tuple[int, str], ConversationState] = {}

    def state_for(self, fan: SyntheticFan, evaluated_at: datetime) -> ConversationState:
        if not isinstance(fan, SyntheticFan):
            raise ValueError("fan must be SyntheticFan")
        if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        key = (int(fan.fan_id), evaluated_at.isoformat())
        if key in self._cache:
            return self._cache[key]
        fid = int(fan.fan_id)
        # deterministic continuous scores via hash
        engagement = _hash_float(self.seed, self.simulation_id, f"conv:fan:{fid}:engagement:{self.model_version}", 1)
        # recency: hours since last interaction hash 0..72, recency_score = exp(-hours/24)
        hours_ago = _hash_float(self.seed, self.simulation_id, f"conv:fan:{fid}:recency_hours:{self.model_version}", 2) * 72.0
        recency = math.exp(-hours_ago / 24.0)  # 1.0 recent, 0.05 after 72h
        recency = _clamp01(recency)
        # activity: recent interaction count 0..5 normalized
        recent_cnt = int(_hash_float(self.seed, self.simulation_id, f"conv:fan:{fid}:recent_cnt:{self.model_version}", 3) * 6)  # 0..5
        activity = recent_cnt / 5.0
        # conversation age hours 0..240
        age_hours = _hash_float(self.seed, self.simulation_id, f"conv:fan:{fid}:age:{self.model_version}", 4) * 240.0
        # derived label continuous -> categorical
        avg = (engagement + recency + activity) / 3.0
        if avg >= 0.65:
            label = "HOT"
        elif avg >= 0.35:
            label = "WARM"
        else:
            label = "COLD"
        state = ConversationState(
            fan_id=fid,
            evaluated_at=evaluated_at,
            engagement_score=float(_clamp01(engagement)),
            recency_score=float(recency),
            activity_score=float(activity),
            conversation_age_hours=float(age_hours),
            recent_interaction_count=int(recent_cnt),
            derived_label=label,
            conversation_model_version=self.model_version,
        )
        self._cache[key] = state
        return state

    def clear_cache(self) -> None:
        self._cache.clear()

    def to_dict(self) -> dict[str, Any]:
        return {"seed": self.seed, "simulation_id": self.simulation_id, "model_version": self.model_version}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationStateModel:
        return cls(seed=int(data["seed"]), simulation_id=str(data["simulation_id"]), model_version=str(data.get("model_version", CONVERSATION_MODEL_VERSION)))


class FatigueModel:
    """Deterministic fatigue / saturation with exponential decay."""

    def __init__(
        self,
        seed: int,
        simulation_id: str,
        simulated_start: datetime,
        decay_rate: float = 0.05,
        model_version: str = FATIGUE_MODEL_VERSION,
    ) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool) or seed <= 0:
            raise ValueError("seed must be positive int")
        if not simulation_id or not isinstance(simulation_id, str):
            raise ValueError("simulation_id required")
        if not isinstance(simulated_start, datetime) or simulated_start.tzinfo is None:
            raise ValueError("simulated_start must be tz-aware")
        if not isinstance(decay_rate, (float, int)) or decay_rate <= 0 or decay_rate > 1:
            raise ValueError("decay_rate must be in (0,1]")
        self.seed = int(seed)
        self.simulation_id = str(simulation_id)
        self.simulated_start = simulated_start
        self.decay_rate = float(decay_rate)
        self.model_version = str(model_version)
        self._cache: dict[tuple[int, str], FatigueState] = {}
        self._history_cache: dict[int, list[dict[str, Any]]] = {}

    def _history_for_fan(self, fan: SyntheticFan) -> list[dict[str, Any]]:
        fid = int(fan.fan_id)
        if fid in self._history_cache:
            return self._history_cache[fid]
        # deterministic history: 0..5 prior offers at absolute times
        n_offers = int(_hash_float(self.seed, self.simulation_id, f"fatigue:fan:{fid}:n_offers:{self.model_version}", 1) * 6)  # 0..5
        events: list[dict[str, Any]] = []
        for i in range(n_offers):
            # offset from simulated_start 0..240h (10 days) deterministic
            offset_h = _hash_float(self.seed, self.simulation_id, f"fatigue:fan:{fid}:offer:{i}:offset:{self.model_version}", 2) * 240.0
            at = self.simulated_start + timedelta(hours=offset_h)
            # outcome deterministic
            is_purchase = _hash_float(self.seed, self.simulation_id, f"fatigue:fan:{fid}:offer:{i}:outcome:{self.model_version}", 3) < 0.3
            outcome = "PURCHASED" if is_purchase else "DECLINED"
            events.append({"type": "offer", "at": at.isoformat(), "outcome": outcome, "index": i})
        # also add one purchase history maybe
        events.sort(key=lambda e: e["at"])
        self._history_cache[fid] = events
        return events

    def history_state_for(self, fan: SyntheticFan, evaluated_at: datetime) -> HistoryState:
        if not isinstance(fan, SyntheticFan):
            raise ValueError("fan must be SyntheticFan")
        if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        events_raw = self._history_for_fan(fan)
        # filter events <= evaluated_at (chronological, only past)
        past = [e for e in events_raw if datetime.fromisoformat(e["at"].replace("Z", "+00:00")) <= evaluated_at]
        past_sorted = sorted(past, key=lambda e: e["at"])
        total = len(past_sorted)
        # recent = within 72h window
        recent = [e for e in past_sorted if (evaluated_at - datetime.fromisoformat(e["at"].replace("Z", "+00:00"))).total_seconds() / 3600 <= 72]
        last_offer_at = datetime.fromisoformat(past_sorted[-1]["at"].replace("Z", "+00:00")) if past_sorted else None
        last_purchase_at = None
        for e in reversed(past_sorted):
            if e["outcome"] == "PURCHASED":
                last_purchase_at = datetime.fromisoformat(e["at"].replace("Z", "+00:00"))
                break
        recent_declined = sum(1 for e in recent if e["outcome"] == "DECLINED")
        return HistoryState(
            fan_id=int(fan.fan_id),
            creator_id=int(fan.creator_id),
            events=tuple(past_sorted),
            total_offer_count=int(total),
            recent_offer_count=int(len(recent)),
            last_offer_at=last_offer_at,
            last_purchase_at=last_purchase_at,
            recent_declined_count=int(recent_declined),
        )

    def fatigue_for(self, fan: SyntheticFan, evaluated_at: datetime) -> FatigueState:
        if not isinstance(fan, SyntheticFan):
            raise ValueError("fan must be SyntheticFan")
        if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        key = (int(fan.fan_id), evaluated_at.isoformat())
        if key in self._cache:
            return self._cache[key]
        hist = self.history_state_for(fan, evaluated_at)
        # compute fatigue: sum over past offers weighted by exponential decay
        fatigue = 0.0
        for e in hist.events:
            at = datetime.fromisoformat(e["at"].replace("Z", "+00:00"))
            hours_ago = (evaluated_at - at).total_seconds() / 3600.0
            if hours_ago < 0:
                continue
            # each offer contributes 0.3, decayed
            contrib = 0.3 * math.exp(-self.decay_rate * hours_ago)
            fatigue += contrib
        # add recent declined extra weight 0.1 per declined within 72h
        fatigue += hist.recent_declined_count * 0.1 * math.exp(-self.decay_rate * 0.5)  # small extra
        # bound [0,1]
        fatigue = _clamp01(fatigue)
        # hours since last offer/purchase for reporting
        hours_since_offer = (evaluated_at - hist.last_offer_at).total_seconds() / 3600.0 if hist.last_offer_at else None
        hours_since_purchase = (evaluated_at - hist.last_purchase_at).total_seconds() / 3600.0 if hist.last_purchase_at else None
        state = FatigueState(
            fan_id=int(fan.fan_id),
            evaluated_at=evaluated_at,
            fatigue_score=float(fatigue),
            recent_offer_count=int(hist.recent_offer_count),
            recent_declined_count=int(hist.recent_declined_count),
            hours_since_last_offer=float(hours_since_offer) if hours_since_offer is not None else None,
            hours_since_last_purchase=float(hours_since_purchase) if hours_since_purchase is not None else None,
            decay_rate=float(self.decay_rate),
            fatigue_model_version=self.model_version,
        )
        self._cache[key] = state
        return state

    def clear_cache(self) -> None:
        self._cache.clear()
        self._history_cache.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "simulation_id": self.simulation_id,
            "simulated_start": self.simulated_start.isoformat(),
            "decay_rate": self.decay_rate,
            "model_version": self.model_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FatigueModel:
        start = data["simulated_start"]
        if isinstance(start, str):
            start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        return cls(
            seed=int(data["seed"]),
            simulation_id=str(data["simulation_id"]),
            simulated_start=start,
            decay_rate=float(data.get("decay_rate", 0.05)),
            model_version=str(data.get("model_version", FATIGUE_MODEL_VERSION)),
        )


class TimeContextModel:
    """Deterministic UTC time context. No local timezone invented."""

    def __init__(self, model_version: str = TIME_MODEL_VERSION) -> None:
        self.model_version = str(model_version)

    def context_for(self, evaluated_at: datetime) -> TimeContext:
        if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")
        # Ensure UTC
        utc_at = evaluated_at.astimezone(UTC)
        hour = utc_at.hour
        dow = utc_at.weekday()  # Mon 0
        is_weekend = dow >= 5
        # time_effect: weekend + evening bonus, bounded [0,1]
        # simple: weekend 0.2, evening 18-22 UTC 0.15, else 0
        effect = 0.0
        if is_weekend:
            effect += 0.2
        if 18 <= hour <= 22:
            effect += 0.15
        effect = _clamp01(effect)
        return TimeContext(
            evaluated_at=utc_at,
            hour_of_day=int(hour),
            day_of_week=int(dow),
            is_weekend=bool(is_weekend),
            timezone="UTC",
            time_effect=float(effect),
            time_model_version=self.model_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"model_version": self.model_version}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TimeContextModel:
        return cls(model_version=str(data.get("model_version", TIME_MODEL_VERSION)))


# ---------------------------------------------------------------------------
# Outcome integration — Phase 7: Conversation + Fatigue + Time
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Phase7OutcomeConfig:
    """Transparent coefficients for Phase 7 purchase probability.

    logit = intercept
            + purchase_weight * purchase_propensity
            + engagement_weight * engagement_level
            + relationship_weight * relationship_affinity
            - freebie_weight * freebie_tendency
            + content_affinity_weight * content_affinity
            + price_term (as in Phase 6)
            + conversation_weight * engagement_score
            + recency_weight * recency_score
            - fatigue_weight * fatigue_score
            + time_weight * time_effect

    price_term as in Phase 6: baseline - price_sensitivity_weight*sensitivity - price_response_weight*sensitivity*(normalized-1)
    All weights explicit, bounded, inspectable. Fatigue subtracts, conversation adds.
    """

    intercept: float = -1.0
    purchase_weight: float = 1.5
    engagement_weight: float = 0.8
    relationship_weight: float = 0.6
    price_sensitivity_weight: float = 0.8
    freebie_weight: float = 0.9
    content_affinity_weight: float = 0.8
    price_response_weight: float = 0.6
    conversation_weight: float = 0.5
    recency_weight: float = 0.4
    fatigue_weight: float = 0.7
    time_weight: float = 0.3
    reference_price_minor: int = 2000

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
            "conversation_weight",
            "recency_weight",
            "fatigue_weight",
            "time_weight",
        ):
            v = getattr(self, name)
            if not isinstance(v, (float, int)) or isinstance(v, bool):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(float(v)):
                raise ValueError(f"{name} must be finite")
        if not isinstance(self.reference_price_minor, int) or self.reference_price_minor <= 0:
            raise ValueError("reference_price_minor must be positive int")


class Phase7OutcomeModel:
    """Phase 7 composable outcome: Fan×Content×Price×Conversation×Fatigue×Time."""

    def __init__(
        self,
        behavior_model: Any,
        affinity_model: Any,
        price_model: Any,
        conversation_model: ConversationStateModel,
        fatigue_model: FatigueModel,
        time_model: TimeContextModel,
        outcome_config: Phase7OutcomeConfig | None = None,
        purchase_delay_hours_min: int = 1,
        purchase_delay_hours_max: int = 48,
        non_purchase_outcome: str = "DECLINED",
    ) -> None:
        from simulation.behavior import FanBehaviorModel
        from simulation.content_affinity import ContentAffinityModel
        from simulation.price_response import PriceResponseModel

        if not isinstance(behavior_model, FanBehaviorModel):
            raise ValueError("behavior_model must be FanBehaviorModel")
        if not isinstance(affinity_model, ContentAffinityModel):
            raise ValueError("affinity_model must be ContentAffinityModel")
        if not isinstance(price_model, PriceResponseModel):
            raise ValueError("price_model must be PriceResponseModel")
        if not isinstance(conversation_model, ConversationStateModel):
            raise ValueError("conversation_model must be ConversationStateModel")
        if not isinstance(fatigue_model, FatigueModel):
            raise ValueError("fatigue_model must be FatigueModel")
        if not isinstance(time_model, TimeContextModel):
            raise ValueError("time_model must be TimeContextModel")
        self.behavior_model = behavior_model
        self.affinity_model = affinity_model
        self.price_model = price_model
        self.conversation_model = conversation_model
        self.fatigue_model = fatigue_model
        self.time_model = time_model
        self.outcome_config = outcome_config or Phase7OutcomeConfig()
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
        content: Any,
        evaluated_at: datetime,
    ) -> float:
        beh = self.behavior_model.for_fan(fan)
        affinity = self.affinity_model.affinity_for(fan, content, beh)
        pr = self.price_model.response_for_content(fan, content, beh)
        conv = self.conversation_model.state_for(fan, evaluated_at)
        fatigue = self.fatigue_model.fatigue_for(fan, evaluated_at)
        time_ctx = self.time_model.context_for(evaluated_at)
        cfg = self.outcome_config
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
            logit += - cfg.price_sensitivity_weight * beh.price_sensitivity
        else:
            deviation = pr.normalized_price - 1.0
            logit += - cfg.price_sensitivity_weight * beh.price_sensitivity - cfg.price_response_weight * beh.price_sensitivity * deviation
        # conversation + fatigue + time
        logit += cfg.conversation_weight * conv.engagement_score
        logit += cfg.recency_weight * conv.recency_score
        logit += - cfg.fatigue_weight * fatigue.fatigue_score
        logit += cfg.time_weight * time_ctx.time_effect
        return _clamp01(_sigmoid(float(logit)))

    def decide(
        self,
        opportunity: Any,
        fan: SyntheticFan,
        content: Any,
        *,
        seed: int,
        run_id: str,
        counter: int,
    ) -> tuple[Any, Any, dict[str, Any]]:
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
            raise ValueError("fan and content required")
        if not isinstance(eval_at, datetime) or eval_at.tzinfo is None:
            raise ValueError("evaluated_at must be tz-aware")

        beh = self.behavior_model.for_fan(fan)
        affinity = self.affinity_model.affinity_for(fan, content, beh)
        pr = self.price_model.response_for_content(fan, content, beh)
        conv = self.conversation_model.state_for(fan, eval_at)
        fatigue = self.fatigue_model.fatigue_for(fan, eval_at)
        time_ctx = self.time_model.context_for(eval_at)
        p = self.probability_for(fan, content, eval_at)
        r = _hash_float(seed, run_id, f"phase7_outcome:{opp_id}", counter)
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
            "conversation_state": conv.to_dict(),
            "fatigue_state": fatigue.to_dict(),
            "time_context": time_ctx.to_dict(),
            "history_state": self.fatigue_model.history_state_for(fan, eval_at).to_dict(),
            "price_minor": int(content.price_minor),
            "currency": str(content.currency),
            "fan_id": int(fan.fan_id),
            "content_id": str(content.content_id),
            "evaluated_at": eval_at.isoformat(),
            "conversation_model_version": self.conversation_model.model_version,
            "fatigue_model_version": self.fatigue_model.model_version,
            "time_model_version": self.time_model.model_version,
            "price_response_model_version": self.price_model.price_response_model_version,
            "content_affinity_model_version": self.affinity_model.content_affinity_model_version,
            "behavior_model_version": self.behavior_model.behavior_model_version,
        }
        return outcome, ref, hidden


__all__ = [
    "CONVERSATION_MODEL_VERSION",
    "FATIGUE_MODEL_VERSION",
    "TIME_MODEL_VERSION",
    "ConversationState",
    "FatigueState",
    "TimeContext",
    "HistoryState",
    "ConversationStateModel",
    "FatigueModel",
    "TimeContextModel",
    "Phase7OutcomeConfig",
    "Phase7OutcomeModel",
]
