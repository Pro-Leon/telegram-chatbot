"""Sunny V2 — new relationship-conversation architecture.

Status: stage D3 — domain, persistence, services, commerce read ports,
turn-context composer, and flag-gated worker observation are implemented;
live control remains disabled (all RELATIONSHIP_V2_* flags default false).
V1 remains DISABLED via core/architecture_router.py. Commerce remains
PRESERVED and authoritative; this package never writes commerce tables
or calls providers directly (see sunny_v2/SYSTEM_BOUNDARIES.md and
sunny_v2/COMMERCE_CONTRACT.md).

Owner: relationship_v2 (new architecture).
"""

from __future__ import annotations

from typing import Any

V2_STATUS = "STAGE_D3_SHADOW_OBSERVE"
V1_DEPENDENCY = False

__all__ = ["V1_DEPENDENCY", "V2_STATUS", "get_context"]


async def get_context(
    creator_id: int, user_id: int, generation_id: str, **kwargs: Any
) -> Any:
    """Assemble one turn's authoritative V2 context (lazy import, no cycles).

    Entry point for workers (Phased_Plan Phase 29): `relationship_v2.get_context(...)`.
    Callers must gate on `core.architecture_router.is_v2_read_enabled()`;
    this function itself performs no flag check so shadow harnesses and
    tests can invoke it directly.
    """
    from relationship_v2.services.turn_context import assemble_turn_context

    return await assemble_turn_context(creator_id, user_id, generation_id, **kwargs)
