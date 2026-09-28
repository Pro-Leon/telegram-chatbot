"""SimulationRun — immutable run descriptor.

Conceptually: simulation_id, scenario_id, seed, simulated_start/end, config_version, behavior_model_version, schema_version

Every field has purpose:
- simulation_id: unique run PK (uuid4 str)
- scenario_id: scenario label (e.g., "baseline") for grouping
- seed: deterministic seed for reproducibility (int>0)
- simulated_start/end: logical horizon (tz-aware UTC) — determines evaluated_at range for maturity
- config_version: simulation config contract version (str)
- behavior_model_version: latent behavior model version (str) — for ground truth provenance
- schema_version: feature schema version the run targets (mirrors FEATURE_SCHEMA_VERSION)

Run is immutable once finalized; status field tracks draft/finalized but object itself is frozen.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from simulation.data_origin import SYNTHETIC_MARKER


def _require_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required and must be non-empty string")
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
class SimulationRun:
    """Immutable simulation run descriptor."""

    simulation_id: str
    scenario_id: str
    seed: int
    simulated_start: datetime
    simulated_end: datetime
    config_version: str
    behavior_model_version: str
    schema_version: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: str = "draft"
    synthetic_marker: str = SYNTHETIC_MARKER
    # P22 reproducibility extensions — optional with defaults for backward compat
    scenario_version: str = "v1"
    optimizer_version: str = "p356.offline.proto.v1"
    code_revision: str | None = None
    dataset_hash: str | None = None

    def __post_init__(self) -> None:
        _require_text("simulation_id", self.simulation_id)
        _require_text("scenario_id", self.scenario_id)
        _require_scope("seed", self.seed)
        start = _require_aware("simulated_start", self.simulated_start)
        end = _require_aware("simulated_end", self.simulated_end)
        if end <= start:
            raise ValueError("simulated_end must be after simulated_start")
        _require_text("config_version", self.config_version)
        _require_text("behavior_model_version", self.behavior_model_version)
        _require_text("schema_version", self.schema_version)
        _require_aware("created_at", self.created_at)
        if self.status not in ("draft", "finalized"):
            raise ValueError("status must be draft or finalized")
        # P22 optional fields — validate if present
        if self.scenario_version is not None:
            _require_text("scenario_version", self.scenario_version)
        if self.optimizer_version is not None:
            _require_text("optimizer_version", self.optimizer_version)
        if self.code_revision is not None and not isinstance(self.code_revision, str):
            raise ValueError("code_revision must be str or None")
        if self.dataset_hash is not None and not isinstance(self.dataset_hash, str):
            raise ValueError("dataset_hash must be str or None")
        # normalize to UTC if not already (for equality determinism)
        object.__setattr__(
            self, "simulated_start", start.astimezone(UTC) if start.tzinfo is not UTC else start
        )
        object.__setattr__(
            self, "simulated_end", end.astimezone(UTC) if end.tzinfo is not UTC else end
        )
        object.__setattr__(
            self,
            "created_at",
            self.created_at.astimezone(UTC)
            if self.created_at.tzinfo is not UTC
            else self.created_at,
        )

    @property
    def data_origin(self) -> str:
        return "simulation"

    def duration_hours(self) -> float:
        return (self.simulated_end - self.simulated_start).total_seconds() / 3600.0

    def is_finalized(self) -> bool:
        return self.status == "finalized"

    def finalized(self) -> SimulationRun:
        """Return a new finalized copy (immutable copy-on-write)."""
        if self.is_finalized():
            return self
        return SimulationRun(
            simulation_id=self.simulation_id,
            scenario_id=self.scenario_id,
            seed=self.seed,
            simulated_start=self.simulated_start,
            simulated_end=self.simulated_end,
            config_version=self.config_version,
            behavior_model_version=self.behavior_model_version,
            schema_version=self.schema_version,
            created_at=self.created_at,
            status="finalized",
            synthetic_marker=self.synthetic_marker,
            scenario_version=self.scenario_version,
            optimizer_version=self.optimizer_version,
            code_revision=self.code_revision,
            dataset_hash=self.dataset_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "simulated_start": self.simulated_start.isoformat(),
            "simulated_end": self.simulated_end.isoformat(),
            "config_version": self.config_version,
            "behavior_model_version": self.behavior_model_version,
            "schema_version": self.schema_version,
            "created_at": self.created_at.isoformat(),
            "status": self.status,
            "synthetic_marker": self.synthetic_marker,
            "data_origin": self.data_origin,
            "scenario_version": self.scenario_version,
            "optimizer_version": self.optimizer_version,
            "code_revision": self.code_revision,
            "dataset_hash": self.dataset_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulationRun:
        start = _coerce_aware(data.get("simulated_start"))
        end = _coerce_aware(data.get("simulated_end"))
        created = _coerce_aware(data.get("created_at"))
        if start is None or end is None or created is None:
            raise ValueError("SimulationRun.from_dict missing/invalid timestamps")
        # backward compat defaults for P22 fields
        return cls(
            simulation_id=str(data["simulation_id"]),
            scenario_id=str(data["scenario_id"]),
            seed=int(data["seed"]),
            simulated_start=start,
            simulated_end=end,
            config_version=str(data["config_version"]),
            behavior_model_version=str(data["behavior_model_version"]),
            schema_version=str(data["schema_version"]),
            created_at=created,
            status=str(data.get("status", "draft")),
            synthetic_marker=str(data.get("synthetic_marker", SYNTHETIC_MARKER)),
            scenario_version=str(data.get("scenario_version", "v1")),
            optimizer_version=str(data.get("optimizer_version", "p356.offline.proto.v1")),
            code_revision=data.get("code_revision"),
            dataset_hash=data.get("dataset_hash"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> SimulationRun:
        return cls.from_dict(json.loads(raw))

    @classmethod
    def create(
        cls,
        *,
        scenario_id: str = "baseline",
        seed: int,
        simulated_start: datetime,
        simulated_end: datetime,
        config_version: str = "v1",
        behavior_model_version: str = "v1",
        schema_version: str = "p356.features.v1",
        simulation_id: str | None = None,
        created_at: datetime | None = None,
        scenario_version: str = "v1",
        optimizer_version: str = "p356.offline.proto.v1",
        code_revision: str | None = None,
        dataset_hash: str | None = None,
    ) -> SimulationRun:
        """Factory with defaults."""
        sid = simulation_id or str(uuid.uuid4())
        # default code_revision via local hash (avoid circular import)
        if code_revision is None:
            try:
                import hashlib
                from pathlib import Path

                h = hashlib.sha256()
                sim_dir = Path(__file__).parent
                for p in sorted(sim_dir.rglob("*.py")):
                    try:
                        h.update(p.read_bytes())
                    except Exception:
                        continue
                code_revision = h.hexdigest()[:8]
            except Exception:
                code_revision = None
        # scenario_version from OfferStrategy.version if scenario exists
        if scenario_version == "v1":
            try:
                from simulation.scenarios import get_strategy

                strat = get_strategy(scenario_id)
                scenario_version = getattr(strat, "version", "v1") or "v1"
            except Exception:
                pass
        return cls(
            simulation_id=sid,
            scenario_id=scenario_id,
            seed=seed,
            simulated_start=simulated_start,
            simulated_end=simulated_end,
            config_version=config_version,
            behavior_model_version=behavior_model_version,
            schema_version=schema_version,
            created_at=created_at or datetime.now(UTC),
            status="draft",
            scenario_version=str(scenario_version),
            optimizer_version=str(optimizer_version),
            code_revision=code_revision,
            dataset_hash=dataset_hash,
        )
