"""SimulationDataOrigin — explicit origin tag.

Reuses existing repository convention for synthetic detection:
- generation_id prefix "synthetic:" (commerce/optimizer_readiness.py:147 SYNTHETIC_GENERATION_PREFIX)
- synthetic marker "SYNTHETIC_P356_FIXTURE" (commerce/offline_optimizer.py:167 SYNTHETIC_MARKER)

Spec invariants:
- simulation data must be distinguishable from production data
- no arbitrary string literals spread across codebase — use this module
"""

from __future__ import annotations

from typing import Any

# Canonical prefixes/markers — single source, mirrors existing code.
SYNTHETIC_GENERATION_PREFIX = "synthetic:"
RECOVERY_GENERATION_PREFIX = "recovered:"
SYNTHETIC_MARKER = "SYNTHETIC_P356_FIXTURE"

# Origin vocabulary (closed, no new authority).
ORIGIN_SIMULATION = "simulation"
ORIGIN_PRODUCTION = "production"
ORIGIN_TEST = "test"
ORIGIN_FIXTURE = "fixture"


class SimulationDataOrigin(str):
    """Explicit origin tag. Prefer ORIGIN_* constants."""

    SIMULATION = ORIGIN_SIMULATION
    PRODUCTION = ORIGIN_PRODUCTION
    TEST = ORIGIN_TEST
    FIXTURE = ORIGIN_FIXTURE


def is_simulation_row(row: Any) -> bool:
    """Return True iff row is simulation/synthetic (file: commerce/optimizer_readiness.py:167 logic).

    Mirrors _is_synthetic_row: checks `row.get("synthetic")` truthy or generation_id prefix synthetic:.
    No DB access. Never raises on bad input.
    """
    try:
        if isinstance(row, dict):
            if row.get("synthetic"):
                return True
            gen = row.get("generation_id")
        else:
            gen = getattr(row, "generation_id", None)
    except Exception:
        return False
    return isinstance(gen, str) and gen.startswith(SYNTHETIC_GENERATION_PREFIX)


def is_recovered_row(row: Any) -> bool:
    """Detect recovered: prefix (commerce/opportunity_evidence.py recovered handling)."""
    try:
        if isinstance(row, dict):
            gen = row.get("generation_id")
        else:
            gen = getattr(row, "generation_id", None)
    except Exception:
        return False
    return isinstance(gen, str) and gen.startswith(RECOVERY_GENERATION_PREFIX)


__all__ = [
    "ORIGIN_PRODUCTION",
    "ORIGIN_SIMULATION",
    "RECOVERY_GENERATION_PREFIX",
    "SYNTHETIC_GENERATION_PREFIX",
    "SYNTHETIC_MARKER",
    "SimulationDataOrigin",
    "is_recovered_row",
    "is_simulation_row",
]
