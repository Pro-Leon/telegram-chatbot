"""SimulationEvent — minimal extensible event envelope.

Conceptually: event_id, simulation_id, event_type, occurred_at, entity identifiers, payload
Extensible for later phases. Serializable. No hidden ground truth in payload.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
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
class SimulationEvent:
    """Minimal simulation event envelope (immutable)."""

    event_id: str
    simulation_id: str
    event_type: str
    occurred_at: datetime
    entity_ids: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    data_origin: str = "simulation"

    def __post_init__(self) -> None:
        _require_text("event_id", self.event_id)
        _require_text("simulation_id", self.simulation_id)
        _require_text("event_type", self.event_type)
        _require_aware("occurred_at", self.occurred_at)
        if not isinstance(self.entity_ids, dict):
            raise ValueError("entity_ids must be dict")
        if not isinstance(self.payload, dict):
            raise ValueError("payload must be dict")
        # Ensure data_origin is simulation for Phase 1
        if self.data_origin != "simulation":
            raise ValueError("data_origin must be 'simulation' for SimulationEvent")
        # Prohibit accidental ground truth leakage: payload must not contain oracle keys
        # This is a contract guard — Phase 1 payload should be application-visible only.
        forbidden = {"ground_truth", "oracle_probability", "latent_probability", "latent_affinity"}
        intersect = forbidden.intersection(self.payload.keys())
        if intersect:
            raise ValueError(f"payload must not contain ground-truth keys: {intersect}")

    @classmethod
    def create(
        cls,
        *,
        simulation_id: str,
        event_type: str,
        occurred_at: datetime,
        entity_ids: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> SimulationEvent:
        return cls(
            event_id=event_id or str(uuid.uuid4()),
            simulation_id=simulation_id,
            event_type=event_type,
            occurred_at=occurred_at,
            entity_ids=dict(entity_ids or {}),
            payload=dict(payload or {}),
            data_origin="simulation",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "simulation_id": self.simulation_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "entity_ids": dict(self.entity_ids),
            "payload": dict(self.payload),
            "data_origin": self.data_origin,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulationEvent:
        occurred = _coerce_aware(data.get("occurred_at"))
        if occurred is None:
            raise ValueError("SimulationEvent.from_dict invalid occurred_at")
        return cls(
            event_id=str(data["event_id"]),
            simulation_id=str(data["simulation_id"]),
            event_type=str(data["event_type"]),
            occurred_at=occurred,
            entity_ids=dict(data.get("entity_ids") or {}),
            payload=dict(data.get("payload") or {}),
            data_origin=str(data.get("data_origin", "simulation")),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> SimulationEvent:
        return cls.from_dict(json.loads(raw))
