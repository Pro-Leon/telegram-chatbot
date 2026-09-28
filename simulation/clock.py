"""SimulationClock — deterministic logical clock.

Requirements (Phase 1 B4):
- deterministic, timezone-aware (UTC), no sleep, no wall-clock for progression, reproducible
- current_time(), advance(delta), advance_to(timestamp)
- reproducible given same start/operations

Timestamp semantics mirror application:
- TIMESTAMPTZ in DB
- UTC tz-aware datetime
- isoformat serialization
- precision microsecond (datetime default)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


def _require_aware(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
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


class SimulationClock:
    """Deterministic logical simulation clock.

    Example:
        clock = SimulationClock(start)
        clock.current_time()
        clock.advance(hours=6)
        clock.current_time()  # deterministic

    No sleeping, no wall-clock dependency for simulation progression.
    """

    def __init__(self, start: datetime) -> None:
        self._start = _require_aware("start", start)
        # normalize to UTC
        if self._start.tzinfo is not UTC:
            self._start = self._start.astimezone(UTC)
        self._current = self._start

    @property
    def start(self) -> datetime:
        return self._start

    def current_time(self) -> datetime:
        """Return current logical time (tz-aware UTC). Pure, no side effect."""
        return self._current

    def advance(self, delta: timedelta | None = None, **kwargs: Any) -> datetime:
        """Advance by timedelta or kwargs (hours, minutes, seconds, days). Returns new current_time.

        Supported kwargs: days, hours, minutes, seconds, milliseconds, microseconds, weeks
        Mirrors timedelta construction. deterministic.
        """
        if delta is not None:
            if not isinstance(delta, timedelta):
                raise ValueError("delta must be timedelta or None")
            if kwargs:
                raise ValueError("provide delta or kwargs, not both")
            step = delta
        else:
            if not kwargs:
                raise ValueError("advance requires delta or time kwargs")
            try:
                step = timedelta(**kwargs)
            except Exception as exc:
                raise ValueError(f"invalid advance kwargs: {exc}") from exc
        # timedelta can be negative? For Phase 1 we allow only forward, but check monotonic.
        new_time = self._current + step
        # No restriction on negative step for flexibility, but log if backwards? Keep deterministic.
        self._current = new_time
        return self._current

    def advance_to(self, timestamp: datetime) -> datetime:
        """Advance to absolute timestamp (must be >= current and tz-aware). Returns new current_time."""
        ts = _require_aware("timestamp", timestamp)
        if ts.tzinfo is not UTC:
            ts = ts.astimezone(UTC)
        if ts < self._current:
            raise ValueError("advance_to timestamp must be >= current_time (no backward time)")
        self._current = ts
        return self._current

    def elapsed(self) -> timedelta:
        """Elapsed since start."""
        return self._current - self._start

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self._start.isoformat(),
            "current": self._current.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulationClock:
        start_raw = data.get("start")
        current_raw = data.get("current")
        start = _coerce_aware(start_raw)
        if start is None:
            raise ValueError("SimulationClock.from_dict missing/invalid start")
        clock = cls(start)
        if current_raw is not None:
            cur = _coerce_aware(current_raw)
            if cur is None:
                raise ValueError("invalid current in SimulationClock dict")
            clock._current = cur
        return clock

    def __repr__(self) -> str:
        return (
            f"SimulationClock(start={self._start.isoformat()}, current={self._current.isoformat()})"
        )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SimulationClock):
            return self._start == other._start and self._current == other._current
        return False
