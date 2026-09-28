"""Relationship Intelligence — bounded, deterministic.
Extends long_term_memory open_loop to promises/plans/events.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

# Reuse long_term_memory for open loops, but add richer event
def create_relationship_event(creator_id: int, user_id: int, subject: str, value: str, importance: float = 0.7, event_type: str = "open_loop", generation_id: str | None = None) -> dict[str, Any]:
    return {
        "creator_id": creator_id,
        "user_id": user_id,
        "subject": subject,
        "value": value,
        "importance": importance,
        "event_type": event_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_referenced_at": datetime.now(timezone.utc).isoformat(),
        "status": "OPEN",
        "generation_id": generation_id,
    }

# Use long_term_memory as persistence, but also provide helpers that wrap it
async def track_open_loop(creator_id: int, user_id: int, subject: str, value: str, importance: float = 0.8, generation_id: str | None = None) -> bool:
    try:
        from commerce.long_term_memory import create_memory_item, add_memory_item
        item = create_memory_item(creator_id=creator_id, user_id=user_id, memory_type="open_loop", subject=subject, value=value, confidence=0.9, source="explicit", importance=importance)
        if generation_id:
            item["generation_id"] = generation_id
        await add_memory_item(creator_id, user_id, item)
        return True
    except Exception:
        return False
