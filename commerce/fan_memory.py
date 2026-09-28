"""Fan Memory V2 — creator-scoped, confidence-weighted preferences (Phase 14)."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("commerce.fan_memory")

# Confidence levels
EXPLICIT = 1.0
STRONG_INFERENCE = 0.8
WEAK_INFERENCE = 0.5
UNKNOWN = 0.0

async def get_fan_memory(creator_id: int, user_id: int) -> dict[str, Any]:
    """Get creator-scoped fan memory (preferences, interests)."""
    try:
        from db.postgres import get_commercial_preferences
        prefs = await get_commercial_preferences(creator_id, user_id)
        return {"preferences": prefs}
    except Exception:
        return {"preferences": {}}

async def update_fan_preference(creator_id: int, user_id: int, key: str, value: str, confidence: float = WEAK_INFERENCE) -> None:
    """Update a single preference with confidence/recency."""
    try:
        from db.postgres import get_commercial_preferences, update_commercial_preferences
        prefs = await get_commercial_preferences(creator_id, user_id)
        # Store as dict with count, confidence, last_seen
        existing = prefs.get(key, {})
        if isinstance(existing, dict):
            count = existing.get("count", 0) + 1
            # If explicit, set confidence to EXPLICIT, else max
            new_conf = max(confidence, existing.get("confidence", 0))
        else:
            count = 1
            new_conf = confidence
        prefs[key] = {"value": value, "confidence": new_conf, "count": count, "last_seen": datetime.now(timezone.utc).isoformat()}
        await update_commercial_preferences(creator_id, user_id, prefs)
    except Exception:
        logger.warning("update_fan_preference failed", exc_info=True)

def decay_preference(confidence: float, days_since: float) -> float:
    """Deterministic decay: exp(-days/30)."""
    import math
    return confidence * math.exp(-days_since / 30.0)
