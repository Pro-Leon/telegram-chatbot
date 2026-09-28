"""SimulationIdentity — safe synthetic identity namespace.

Prevents accidental collision with production IDs.

Conventions (verified against repo):
- production creator_id int>0 FK creators(id), opportunity_id BIGSERIAL, fan(user) id BIGINT, generation_id TEXT with UNIQUE (creator_id,generation_id) WHERE NOT NULL
- existing synthetic: generation_id prefix "synthetic:" + SYNTHETIC_MARKER, synthetic_generation_id "recovered:{creator}:{offer}"
- Phase 0 proposal synthetic creator >=90000; verified safe because generation prefix distinguishes even if numeric collides, but high offset reduces confusion.

This module makes collision extremely difficult:
- synthetic creators in high range (900000..999999 by default, configurable)
- synthetic fans/opportunities/events include run_id hash component and are deterministic per run+seed
- no production creator/fan row is ever written by simulator (file-only Phase 1)
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

# Default synthetic ranges — high enough to avoid typical production (1..~10000)
SYNTHETIC_CREATOR_RANGE_START = 900_000
SYNTHETIC_CREATOR_RANGE_END = 999_999
SYNTHETIC_FAN_RANGE_START = 9_000_000
SYNTHETIC_USER_RANGE_END = 9_999_999

# For opportunity_id when not using BIGSERIAL sequence, keep in high range
SYNTHETIC_OPPORTUNITY_BASE = 900_000_0  # 9M+

# Generation namespace (mirrors existing)
SYNTHETIC_GENERATION_PREFIX = "synthetic:"


def _require_scope(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required and must be positive int")
    return int(value)


def _hash_int(seed: int, run_id: str, domain: str, counter: int, modulo: int) -> int:
    """Deterministic int from seed+run_id+domain+counter, 0..modulo-1."""
    payload = f"{seed}:{run_id}:{domain}:{counter}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    # Use first 8 hex chars (32 bits) for speed and determinism
    return int(digest[:8], 16) % modulo


@dataclass(frozen=True)
class SimulationIdentity:
    """Identity factory for one simulation run.

    Holds run_id + seed so synthetic IDs are deterministic and reproducible.
    No DB access. No global randomness.
    """

    simulation_id: str
    seed: int
    synthetic_creator_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.simulation_id, str) or not self.simulation_id.strip():
            raise ValueError("simulation_id is required")
        # validate uuid-like but allow any non-empty
        _require_scope(
            "seed", int(self.seed) if isinstance(self.seed, int) and self.seed > 0 else 1
        )
        if not isinstance(self.synthetic_creator_id, int) or isinstance(
            self.synthetic_creator_id, bool
        ):
            raise ValueError("synthetic_creator_id must be int")
        if self.synthetic_creator_id < SYNTHETIC_CREATOR_RANGE_START:
            raise ValueError(
                f"synthetic_creator_id must be >= {SYNTHETIC_CREATOR_RANGE_START} to avoid production collision"
            )

    @classmethod
    def create(
        cls,
        simulation_id: str,
        seed: int,
        *,
        creator_counter: int = 0,
    ) -> SimulationIdentity:
        """Create a synthetic creator identity deterministically.

        creator_id is derived as SYNTHETIC_CREATOR_RANGE_START + hash(seed, simulation_id, 'creator', counter).
        This ensures same seed+simulation_id → same creator, different runs → different.
        Direct synthetic_creator_id can also be constructed explicitly for tests.
        """
        if not isinstance(seed, int) or isinstance(seed, bool) or seed <= 0:
            raise ValueError("seed must be positive int")
        if not isinstance(simulation_id, str) or not simulation_id.strip():
            raise ValueError("simulation_id is required")
        rng = _hash_int(
            seed,
            simulation_id,
            "creator",
            creator_counter,
            SYNTHETIC_CREATOR_RANGE_END - SYNTHETIC_CREATOR_RANGE_START + 1,
        )
        creator_id = SYNTHETIC_CREATOR_RANGE_START + rng
        return cls(
            simulation_id=simulation_id.strip(),
            seed=int(seed),
            synthetic_creator_id=int(creator_id),
        )

    def synthetic_fan_id(self, fan_counter: int) -> int:
        """Deterministic synthetic fan/user_id for this run."""
        _require_scope("fan_counter", fan_counter)
        rng = _hash_int(
            self.seed,
            self.simulation_id,
            "fan",
            fan_counter,
            SYNTHETIC_USER_RANGE_END - SYNTHETIC_FAN_RANGE_START + 1,
        )
        return SYNTHETIC_FAN_RANGE_START + rng

    def synthetic_opportunity_id(self, opportunity_counter: int) -> int:
        """Deterministic synthetic opportunity_id (run-local, high range)."""
        _require_scope("opportunity_counter", opportunity_counter)
        # Use 0..899999 offset + base to stay high but within int
        rng = _hash_int(self.seed, self.simulation_id, "opp", opportunity_counter, 900_000)
        return SYNTHETIC_OPPORTUNITY_BASE + rng + opportunity_counter

    def synthetic_event_id(self) -> str:
        """Unique synthetic event_id (uuid4, not deterministic — event identity per occurrence)."""
        return str(uuid.uuid4())

    def synthetic_generation_id(self, opportunity_id: int) -> str:
        """Generation_id for synthetic opportunity — matches existing synthetic: prefix."""
        _require_scope("opportunity_id", opportunity_id)
        return f"{SYNTHETIC_GENERATION_PREFIX}{self.simulation_id}:{opportunity_id}"

    def synthetic_transaction_id(self, opportunity_id: int) -> str:
        """Synthetic transaction_id for purchase attribution simulation."""
        _require_scope("opportunity_id", opportunity_id)
        return f"txn:sym:{self.simulation_id}:{opportunity_id}"

    def synthetic_drop_cuid(self, definition_id: int, suffix: str = "") -> str:
        """Synthetic Drop CUID."""
        _require_scope("definition_id", definition_id)
        base = f"synthetic_drop:{self.simulation_id}:{definition_id}"
        return f"{base}:{suffix}" if suffix else base

    def synthetic_vault_id(self, counter: int) -> str:
        """Synthetic Vault item id."""
        _require_scope("counter", counter)
        return f"synthetic_vault:{self.simulation_id}:{counter}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "seed": self.seed,
            "synthetic_creator_id": self.synthetic_creator_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulationIdentity:
        return cls(
            simulation_id=str(data["simulation_id"]),
            seed=int(data["seed"]),
            synthetic_creator_id=int(data["synthetic_creator_id"]),
        )

    def is_synthetic_creator(self, creator_id: int) -> bool:
        """Check if a creator_id is in synthetic range (heuristic, generation prefix is authoritative)."""
        try:
            cid = int(creator_id)
        except Exception:
            return False
        return SYNTHETIC_CREATOR_RANGE_START <= cid <= SYNTHETIC_CREATOR_RANGE_END

    def is_synthetic_fan(self, fan_id: int) -> bool:
        try:
            fid = int(fan_id)
        except Exception:
            return False
        return SYNTHETIC_FAN_RANGE_START <= fid <= SYNTHETIC_USER_RANGE_END
