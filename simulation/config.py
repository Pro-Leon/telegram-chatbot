"""SimulationConfig — minimal config to instantiate a run.

Potential fields: simulation_id, scenario_id, seed, start_time, config_version, behavior_model_version
Do not introduce scenario behavior yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def _require_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty string")
    return value.strip()


def _require_scope(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be positive int")
    return int(value)


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
class SimulationConfig:
    """Minimal simulation run configuration."""

    scenario_id: str
    seed: int
    start_time: datetime
    end_time: datetime
    config_version: str = "v1"
    behavior_model_version: str = "v1"
    schema_version: str = "p356.features.v1"
    simulation_id: str = ""
    # P22 optional extensions
    scenario_version: str = "v1"
    optimizer_version: str = "p356.offline.proto.v1"
    code_revision: str | None = None
    dataset_hash: str | None = None

    def __post_init__(self) -> None:
        _require_text("scenario_id", self.scenario_id)
        _require_scope("seed", self.seed)
        _require_aware("start_time", self.start_time)
        _require_aware("end_time", self.end_time)
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        _require_text("config_version", self.config_version)
        _require_text("behavior_model_version", self.behavior_model_version)
        _require_text("schema_version", self.schema_version)
        if self.simulation_id and not isinstance(self.simulation_id, str):
            raise ValueError("simulation_id must be str if provided")
        if self.scenario_version is not None:
            _require_text("scenario_version", self.scenario_version)
        if self.optimizer_version is not None:
            _require_text("optimizer_version", self.optimizer_version)

    @property
    def effective_simulation_id(self) -> str:
        return self.simulation_id or str(uuid4())

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "config_version": self.config_version,
            "behavior_model_version": self.behavior_model_version,
            "schema_version": self.schema_version,
            "scenario_version": self.scenario_version,
            "optimizer_version": self.optimizer_version,
            "code_revision": self.code_revision,
            "dataset_hash": self.dataset_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulationConfig:
        start = _coerce_aware(data.get("start_time"))
        end = _coerce_aware(data.get("end_time"))
        if start is None or end is None:
            raise ValueError("SimulationConfig.from_dict invalid timestamps")
        return cls(
            simulation_id=str(data.get("simulation_id") or ""),
            scenario_id=str(data["scenario_id"]),
            seed=int(data["seed"]),
            start_time=start,
            end_time=end,
            config_version=str(data.get("config_version", "v1")),
            behavior_model_version=str(data.get("behavior_model_version", "v1")),
            schema_version=str(data.get("schema_version", "p356.features.v1")),
            scenario_version=str(data.get("scenario_version", "v1")),
            optimizer_version=str(data.get("optimizer_version", "p356.offline.proto.v1")),
            code_revision=data.get("code_revision"),
            dataset_hash=data.get("dataset_hash"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> SimulationConfig:
        return cls.from_dict(json.loads(raw))

    def to_run_kwargs(self) -> dict[str, Any]:
        """Kwargs suitable for SimulationRun.create."""
        return {
            "simulation_id": self.effective_simulation_id,
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "simulated_start": self.start_time,
            "simulated_end": self.end_time,
            "config_version": self.config_version,
            "behavior_model_version": self.behavior_model_version,
            "schema_version": self.schema_version,
            "scenario_version": self.scenario_version,
            "optimizer_version": self.optimizer_version,
            "code_revision": self.code_revision,
            "dataset_hash": self.dataset_hash,
        }
