"""GroundTruthReference — reference mechanism, not ground truth itself.

Important distinction: GroundTruthReference is not GroundTruth itself.
It allows later components to associate simulation→opportunity→ground-truth
without exposing latent values to OptimizationInput / optimizer.

Optimizer must never receive latent purchase probability, price sensitivity, etc.
This reference is stored separately (file-only simulation artifact) and never
injected into ledger decision_snapshot or evidence Context.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _require_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty string")
    return value.strip()


def _require_aware(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware datetime")
    return value


def _coerce_aware(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@dataclass(frozen=True)
class GroundTruthReference:
    """Reference linking simulation opportunity to hidden truth store.

    Contains only identifiers, not latent values. The hidden values live in
    a separate artifact (e.g., simulation_runs/{id}/ground_truth/{opportunity_id}.json)
    that optimizer code must never import.
    """

    reference_id: str
    simulation_id: str
    opportunity_id: int
    generation_id: str
    created_at: datetime
    data_origin: str = "simulation"

    def __post_init__(self) -> None:
        _require_text("reference_id", self.reference_id)
        _require_text("simulation_id", self.simulation_id)
        if (
            not isinstance(self.opportunity_id, int)
            or isinstance(self.opportunity_id, bool)
            or self.opportunity_id <= 0
        ):
            raise ValueError("opportunity_id must be positive int")
        _require_text("generation_id", self.generation_id)
        _require_aware("created_at", self.created_at)
        if self.data_origin != "simulation":
            raise ValueError("data_origin must be 'simulation'")

    @classmethod
    def create(
        cls,
        *,
        simulation_id: str,
        opportunity_id: int,
        generation_id: str,
        created_at: datetime | None = None,
        reference_id: str | None = None,
    ) -> GroundTruthReference:
        return cls(
            reference_id=reference_id or str(uuid.uuid4()),
            simulation_id=simulation_id,
            opportunity_id=opportunity_id,
            generation_id=generation_id,
            created_at=created_at or datetime.now(UTC),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "simulation_id": self.simulation_id,
            "opportunity_id": self.opportunity_id,
            "generation_id": self.generation_id,
            "created_at": self.created_at.isoformat(),
            "data_origin": self.data_origin,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroundTruthReference:
        created = _coerce_aware(data.get("created_at"))
        if created is None:
            raise ValueError("GroundTruthReference.from_dict invalid created_at")
        return cls(
            reference_id=str(data["reference_id"]),
            simulation_id=str(data["simulation_id"]),
            opportunity_id=int(data["opportunity_id"]),
            generation_id=str(data["generation_id"]),
            created_at=created,
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> GroundTruthReference:
        return cls.from_dict(json.loads(raw))


# Helper to verify isolation — ensure reference cannot become OptimizationInput field
# This is tested in test suite C6: ground_truth cannot enter optimizer features.

__all__ = ["GroundTruthReference"]
