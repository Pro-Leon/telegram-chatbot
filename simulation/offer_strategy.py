"""OfferStrategy — Phase 8 deterministic offer sampling.

File-only, synthetic, no DB/Ranking duplication, no global random.
Provides deterministic (seed, simulation_id, counter) → (offer_type, price_minor, vault_ids)
for heterogeneous CreatorDataset while preserving baseline replay.

Determinism via SHA256( seed:simulation_id:domain:counter )[:8] → float 0..1 / int.
USD only, no FX (spec).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

OFFER_STRATEGY_VERSION = "v1"
OFFER_TYPES = ("SINGLE", "SMALL_BUNDLE", "CORE_BUNDLE", "PREMIUM")
REFERENCE_PRICE_MINOR = 2000


def _hash_float(seed: int, run_id: str, domain: str, counter: int) -> float:
    payload = f"{seed}:{run_id}:{domain}:{counter}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    v = int(digest[:8], 16)
    return v / 4294967296.0


def _hash_int(seed: int, run_id: str, domain: str, counter: int, modulo: int) -> int:
    payload = f"{seed}:{run_id}:{domain}:{counter}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    return int(digest[:8], 16) % modulo


@dataclass(frozen=True)
class PriceDistribution:
    """Deterministic price sampling range (USD minor units)."""

    price_min: int = 500
    price_max: int = 5000
    reference: int = REFERENCE_PRICE_MINOR

    def __post_init__(self) -> None:
        if not isinstance(self.price_min, int) or self.price_min < 0:
            raise ValueError("price_min must be int >=0")
        if not isinstance(self.price_max, int) or self.price_max < self.price_min:
            raise ValueError("price_max must be >= price_min")
        if not isinstance(self.reference, int) or self.reference <= 0:
            raise ValueError("reference must be positive int")

    def sample(self, seed: int, simulation_id: str, strategy_id: str, counter: int) -> int:
        """Deterministic uniform integer in [price_min, price_max] inclusive."""
        if self.price_min == self.price_max:
            return int(self.price_min)
        span = self.price_max - self.price_min + 1
        r = _hash_float(seed, simulation_id, f"offer_strategy:{strategy_id}:price:{counter}", 1)
        # use float to int mapping: round down
        offset = int(r * span)
        if offset >= span:
            offset = span - 1
        return int(self.price_min + offset)


@dataclass(frozen=True)
class OfferStrategy:
    """Frozen deterministic offer sampling strategy."""

    strategy_id: str
    description: str
    content_mix: dict[str, float]  # offer_type → weight (sum 1.0)
    price_distribution: PriceDistribution
    vault_count_range: tuple[int, int] = (1, 3)
    family_id: int | None = None
    currency: str = "USD"
    version: str = OFFER_STRATEGY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise ValueError("strategy_id must be non-empty string")
        if not isinstance(self.content_mix, dict) or not self.content_mix:
            raise ValueError("content_mix must be non-empty dict")
        total = 0.0
        for k, v in self.content_mix.items():
            if k not in OFFER_TYPES:
                raise ValueError(f"content_mix key must be one of {OFFER_TYPES}, got {k}")
            if not isinstance(v, (float, int)) or isinstance(v, bool) or v < 0:
                raise ValueError(f"content_mix weight for {k} must be >=0")
            total += float(v)
        if total <= 0:
            raise ValueError("content_mix total weight must be >0")
        # normalize not required but check close to 1.0 within tolerance? allow any positive, sampling normalizes
        if not isinstance(self.price_distribution, PriceDistribution):
            raise ValueError("price_distribution must be PriceDistribution")
        if not isinstance(self.vault_count_range, (list, tuple)) or len(self.vault_count_range) != 2:
            raise ValueError("vault_count_range must be tuple (min,max)")
        lo, hi = self.vault_count_range
        if not isinstance(lo, int) or not isinstance(hi, int) or lo < 1 or hi < lo or hi > 10:
            raise ValueError("vault_count_range must be 1..10 with lo<=hi")
        if self.family_id is not None and (not isinstance(self.family_id, int) or isinstance(self.family_id, bool) or self.family_id <= 0):
            raise ValueError("family_id must be positive int or None")
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValueError("currency must be non-empty")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be non-empty")

    def _sample_offer_type(self, seed: int, simulation_id: str, counter: int) -> str:
        r = _hash_float(seed, simulation_id, f"offer_strategy:{self.strategy_id}:offer_type:{counter}", 1)
        total = sum(float(v) for v in self.content_mix.values())
        cumul = 0.0
        for otype in OFFER_TYPES:
            w = float(self.content_mix.get(otype, 0.0))
            if w <= 0:
                continue
            cumul += w / total
            if r < cumul:
                return otype
        # fallback last with weight >0
        for otype in reversed(OFFER_TYPES):
            if float(self.content_mix.get(otype, 0.0)) > 0:
                return otype
        return "SMALL_BUNDLE"

    def sample_content_params(
        self, seed: int, simulation_id: str, counter: int
    ) -> tuple[str, int, tuple[str, ...], tuple[str, ...]]:
        """
        Deterministic sampling per (seed, simulation_id, counter):
        returns (offer_type, price_minor, vault_ids, mapped_drop_ids)
        No global RNG, no wall clock.
        """
        offer_type = self._sample_offer_type(seed, simulation_id, counter)
        price_minor = self.price_distribution.sample(seed, simulation_id, self.strategy_id, counter)
        # vault count: per-offer-type nuance but bounded by vault_count_range global for simplicity
        # 1..3 default gives heterogeneity across types still (SMALL vs PREMIUM share range)
        lo, hi = self.vault_count_range
        span = hi - lo + 1
        # use separate hash for vault count to keep deterministic but independent of price
        vc_r = _hash_float(seed, simulation_id, f"offer_strategy:{self.strategy_id}:vault_cnt:{counter}", 2)
        cnt = int(vc_r * span) + lo
        if cnt < lo:
            cnt = lo
        if cnt > hi:
            cnt = hi
        # For realism, adjust SINGLE to at most 2, bundle at least 2 if strategy mix has bundle weight
        # keep simple clamp: SINGLE max 2
        if offer_type == "SINGLE" and cnt > 2:
            cnt = 2
        if offer_type in ("CORE_BUNDLE", "PREMIUM") and cnt < 2:
            cnt = 2
        vault_ids = tuple(f"V{counter}_{j}" for j in range(cnt))
        mapped = tuple(f"drop_{counter}_{j}" for j in range(1))  # single drop per content to keep SINGLE mapping valid
        return offer_type, int(price_minor), vault_ids, mapped

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "description": self.description,
            "content_mix": dict(self.content_mix),
            "price_distribution": {
                "price_min": self.price_distribution.price_min,
                "price_max": self.price_distribution.price_max,
                "reference": self.price_distribution.reference,
            },
            "vault_count_range": list(self.vault_count_range),
            "family_id": self.family_id,
            "currency": self.currency,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OfferStrategy:
        pd = data.get("price_distribution", {})
        if isinstance(pd, dict):
            price_dist = PriceDistribution(
                price_min=int(pd.get("price_min", 500)),
                price_max=int(pd.get("price_max", 5000)),
                reference=int(pd.get("reference", REFERENCE_PRICE_MINOR)),
            )
        else:
            price_dist = PriceDistribution()
        vcr = data.get("vault_count_range", [1, 3])
        return cls(
            strategy_id=str(data["strategy_id"]),
            description=str(data.get("description", "")),
            content_mix=dict(data["content_mix"]),
            price_distribution=price_dist,
            vault_count_range=(int(vcr[0]), int(vcr[1])),
            family_id=data.get("family_id"),
            currency=str(data.get("currency", "USD")),
            version=str(data.get("version", OFFER_STRATEGY_VERSION)),
        )


__all__ = ["OFFER_STRATEGY_VERSION", "OFFER_TYPES", "PriceDistribution", "OfferStrategy"]
