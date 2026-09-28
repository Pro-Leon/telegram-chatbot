"""Simulation Run Persistence — file-only Phase 1.

Phase 0 proposed: file-only vault/simulation_runs/{run}/
For Phase 1 we use repository-native `simulation_runs/{simulation_id}/` as bounded
artifact. No DB migration.

Structure:
  simulation_runs/
    {simulation_id}/
      manifest.json          <-- SimulationRun
      events/
        {event_id}.json      <-- SimulationEvent per file, or events.jsonl
      ground_truth/
        {opportunity_id}.json <-- hidden truth store (never in optimizer path)
      metadata/
        config.json          <-- SimulationConfig

All helpers are pure file IO, no Telegram/Dropfans/Redis.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simulation.config import SimulationConfig
from simulation.event import SimulationEvent
from simulation.ground_truth import GroundTruthReference
from simulation.run import SimulationRun

# Base directory relative to repository root (E:\chatbot). Caller can override.
DEFAULT_BASE = Path(__file__).parent.parent / "simulation_runs"


def _base_path(base: Path | str | None) -> Path:
    if base is None:
        return DEFAULT_BASE
    return Path(base)


def run_dir(simulation_id: str, base: Path | str | None = None) -> Path:
    if not simulation_id or not str(simulation_id).strip():
        raise ValueError("simulation_id is required")
    return _base_path(base) / str(simulation_id).strip()


def manifest_path(simulation_id: str, base: Path | str | None = None) -> Path:
    return run_dir(simulation_id, base) / "manifest.json"


def events_dir(simulation_id: str, base: Path | str | None = None) -> Path:
    return run_dir(simulation_id, base) / "events"


def ground_truth_dir(simulation_id: str, base: Path | str | None = None) -> Path:
    return run_dir(simulation_id, base) / "ground_truth"


def metadata_dir(simulation_id: str, base: Path | str | None = None) -> Path:
    return run_dir(simulation_id, base) / "metadata"


def save_run_manifest(run: SimulationRun, base: Path | str | None = None) -> Path:
    """Persist SimulationRun manifest.json. Creates directories. Returns path."""
    if not isinstance(run, SimulationRun):
        raise ValueError("run must be SimulationRun")
    mpath = manifest_path(run.simulation_id, base)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    # Also ensure sibling dirs exist for Phase 2 extensibility
    events_dir(run.simulation_id, base).mkdir(parents=True, exist_ok=True)
    ground_truth_dir(run.simulation_id, base).mkdir(parents=True, exist_ok=True)
    metadata_dir(run.simulation_id, base).mkdir(parents=True, exist_ok=True)
    mpath.write_text(run.to_json(), encoding="utf-8")
    return mpath


def load_run_manifest(simulation_id: str, base: Path | str | None = None) -> SimulationRun:
    """Load manifest.json for a run. Raises if missing/invalid."""
    mpath = manifest_path(simulation_id, base)
    if not mpath.exists():
        raise FileNotFoundError(f"manifest not found: {mpath}")
    raw = mpath.read_text(encoding="utf-8")
    return SimulationRun.from_json(raw)


def save_simulation_event(event: SimulationEvent, base: Path | str | None = None) -> Path:
    """Persist one SimulationEvent as events/{event_id}.json."""
    if not isinstance(event, SimulationEvent):
        raise ValueError("event must be SimulationEvent")
    epath = events_dir(event.simulation_id, base) / f"{event.event_id}.json"
    epath.parent.mkdir(parents=True, exist_ok=True)
    epath.write_text(event.to_json(), encoding="utf-8")
    # Also append to events.jsonl for streaming
    jl = events_dir(event.simulation_id, base) / "events.jsonl"
    with jl.open("a", encoding="utf-8") as f:
        f.write(event.to_json() + "\n")
    return epath


def load_simulation_events(
    simulation_id: str, base: Path | str | None = None
) -> list[SimulationEvent]:
    """Load all events for a run via events.jsonl or per-file fallback."""
    ed = events_dir(simulation_id, base)
    if not ed.exists():
        return []
    jl = ed / "events.jsonl"
    events: list[SimulationEvent] = []
    if jl.exists():
        for line in jl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(SimulationEvent.from_json(line))
        return events
    # fallback per-file
    for p in ed.glob("*.json"):
        if p.name == "events.jsonl":
            continue
        events.append(SimulationEvent.from_dict(json.loads(p.read_text(encoding="utf-8"))))
    return events


def save_ground_truth_reference(
    ref: GroundTruthReference,
    truth_payload: dict[str, Any],
    base: Path | str | None = None,
) -> Path:
    """Persist reference + hidden truth payload separately.

    Reference: ground_truth/{reference_id}.json (contains GroundTruthReference)
    Hidden payload: ground_truth/{opportunity_id}.payload.json (never loaded by optimizer path)
    This enforces isolation: optimizer code imports GroundTruthReference but must not read payload.
    """
    if not isinstance(ref, GroundTruthReference):
        raise ValueError("ref must be GroundTruthReference")
    gdir = ground_truth_dir(ref.simulation_id, base)
    gdir.mkdir(parents=True, exist_ok=True)
    ref_path = gdir / f"{ref.reference_id}.json"
    ref_path.write_text(ref.to_json(), encoding="utf-8")
    payload_path = gdir / f"{ref.opportunity_id}.payload.json"
    payload_path.write_text(json.dumps(truth_payload, sort_keys=True), encoding="utf-8")
    return ref_path


def load_ground_truth_reference(
    reference_id: str, simulation_id: str, base: Path | str | None = None
) -> GroundTruthReference:
    gdir = ground_truth_dir(simulation_id, base)
    rpath = gdir / f"{reference_id}.json"
    if not rpath.exists():
        raise FileNotFoundError(f"ground truth reference not found: {rpath}")
    return GroundTruthReference.from_json(rpath.read_text(encoding="utf-8"))


def save_config(
    config: SimulationConfig, simulation_id: str, base: Path | str | None = None
) -> Path:
    mdir = metadata_dir(simulation_id, base)
    mdir.mkdir(parents=True, exist_ok=True)
    cpath = mdir / "config.json"
    cpath.write_text(config.to_json(), encoding="utf-8")
    return cpath


def load_config(simulation_id: str, base: Path | str | None = None) -> SimulationConfig:
    cpath = metadata_dir(simulation_id, base) / "config.json"
    if not cpath.exists():
        raise FileNotFoundError(f"config not found: {cpath}")
    return SimulationConfig.from_json(cpath.read_text(encoding="utf-8"))


__all__ = [
    "events_dir",
    "ground_truth_dir",
    "load_config",
    "load_ground_truth_reference",
    "load_run_manifest",
    "load_simulation_events",
    "manifest_path",
    "run_dir",
    "save_config",
    "save_ground_truth_reference",
    "save_run_manifest",
    "save_simulation_event",
]
