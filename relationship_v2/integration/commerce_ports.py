"""Commerce read ports (Stage D1). V2's only path to commerce truth.

Each port matches the `services.commerce_adapter` contract shapes:
- eligibility -> {"eligible": bool, "reason": str}
- opportunity -> {"active_offer": bool, "opportunities": list, "cooldowns": dict}
- purchase    -> {"status": str, "count": int, "last_at": datetime|None,
                  "owned_refs": list, "aftercare": str}

All reads are preserved commerce truth (dao, fan_commercial_state,
offer_history, opportunity_engine) plus the shared users row. No writes,
no provider clients, no sealing/execution, no legacy relationship modules.
Failures propagate (caller sees unknown, never confirmed). Fan-level
eligibility covers blocks/opt-outs only; per-product gates stay in
commerce at request time.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("sunny.v2.commerce_ports")


def _scope_ok(creator_id: int, user_id: int) -> None:
    if not isinstance(creator_id, int) or creator_id <= 0:
        raise ValueError("creator_id must be a positive int (fail-closed)")
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("user_id must be a positive int (fail-closed)")


async def eligibility_port(creator_id: int, user_id: int) -> dict[str, Any]:
    """Fan-level commerce gates: blocked / opted-out. Read-only."""
    _scope_ok(creator_id, user_id)
    from db.postgres import get_user

    user = await get_user(user_id)
    if user is None:
        return {"eligible": False, "reason": "unknown_fan"}
    if user.get("is_blocked"):
        return {"eligible": False, "reason": "user_blocked"}
    if user.get("do_not_auto_reply"):
        return {"eligible": False, "reason": "user_opted_out"}
    return {"eligible": True, "reason": "ok"}


async def opportunity_port(creator_id: int, user_id: int) -> dict[str, Any]:
    """Evaluated opportunity + active-offer flag. Read-only, never sealed."""
    _scope_ok(creator_id, user_id)
    from commerce.offer_history import get_offer_history
    from commerce.opportunity_engine import evaluate_opportunity

    history = await get_offer_history(creator_id, user_id)
    result = await evaluate_opportunity(creator_id, user_id, None, datetime.now(UTC))
    opportunities: list[dict[str, Any]] = []
    if result.selected_candidate is not None:
        opportunities.append(
            {
                "status": result.status,
                "selected": str(result.selected_candidate)[:280],
            }
        )
    return {
        "active_offer": bool(history.has_active_offer),
        "opportunities": opportunities,
        "cooldowns": {},
    }


async def purchase_port(creator_id: int, user_id: int) -> dict[str, Any]:
    """Buyer truth: reconciled counts, owned sets, aftercare. Read-only."""
    _scope_ok(creator_id, user_id)
    from commerce.dao import get_aftercare_status
    from commerce.fan_commercial_state import get_fan_commercial_state

    state = await get_fan_commercial_state(creator_id, user_id)
    aftercare = await get_aftercare_status(creator_id, user_id)
    count = int(state.purchase_count or 0)
    status = "repeat" if count >= 2 else "first_time" if count == 1 else "none"
    return {
        "status": status,
        "count": count,
        "last_at": state.last_purchase_at,
        "owned_refs": sorted(state.purchased_vault_ids or frozenset()),
        "aftercare": aftercare or "none",
    }
