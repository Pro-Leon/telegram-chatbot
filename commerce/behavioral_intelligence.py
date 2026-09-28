"""Behavioral Intelligence — bounded, deterministic, not facts.
Reuses strategy_exposures 50, not new persistence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_BEHAVIORAL_MAX = 20
_behavioral_mem: dict[str, list[dict[str, Any]]] = {}

def observe_behavioral_signal(creator_id: int, user_id: int, signal: str, value: Any, generation_id: str | None = None) -> None:
    key = f"{creator_id}:{user_id}"
    lst = _behavioral_mem.get(key, [])
    # Deduplicate via generation_id + signal
    if generation_id:
        for e in lst:
            if e.get("generation_id") == generation_id and e.get("signal") == signal:
                return
    lst.append({"signal": signal, "value": value, "timestamp": datetime.now(timezone.utc).isoformat(), "generation_id": generation_id})
    if len(lst) > _BEHAVIORAL_MAX:
        lst = lst[-_BEHAVIORAL_MAX:]
    _behavioral_mem[key] = lst

def get_behavioral_signals(creator_id: int, user_id: int, limit: int = 5) -> list[dict[str, Any]]:
    return list(_behavioral_mem.get(f"{creator_id}:{user_id}", [])[-limit:])

def clear_behavioral(creator_id: int | None = None, user_id: int | None = None) -> None:
    if creator_id is None and user_id is None:
        _behavioral_mem.clear()
    elif creator_id is not None and user_id is not None:
        _behavioral_mem.pop(f"{creator_id}:{user_id}", None)

def behavioral_topic_affinity(creator_id: int, user_id: int, topic: str) -> float:
    # Count exposures for topic
    from commerce.adaptive_optimization import get_exposures_memory
    exps = get_exposures_memory(creator_id, user_id)
    total = len(exps)
    if total == 0:
        return 0.0
    cnt = sum(1 for e in exps if e.get("topic") == topic)
    return round(cnt / total, 3)
