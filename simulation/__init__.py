"""Simulation contracts package — Phase 1: Simulation Contracts.

Re-exports for convenience. No production side effects.
All contracts are pure, deterministic, and file-only for Phase 1.
"""

from simulation.clock import SimulationClock
from simulation.config import SimulationConfig
from simulation.data_origin import SimulationDataOrigin, is_simulation_row
from simulation.event import SimulationEvent
from simulation.ground_truth import GroundTruthReference
from simulation.identity import SimulationIdentity
from simulation.persistence import (
    load_run_manifest,
    save_run_manifest,
    save_simulation_event,
)
from simulation.run import SimulationRun

__all__ = [
    "GroundTruthReference",
    "SimulationClock",
    "SimulationConfig",
    "SimulationDataOrigin",
    "SimulationEvent",
    "SimulationIdentity",
    "SimulationRun",
    "is_simulation_row",
    "load_run_manifest",
    "save_run_manifest",
    "save_simulation_event",
]
