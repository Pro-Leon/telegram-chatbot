"""Temporal Context — Phase 36 deterministic, no LLM, no new worker.
Provides fan timezone → local time via deterministic lookup, not LLM.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

_CITY_TZ = {
    "chicago": "America/Chicago",
    "new york": "America/New_York",
    "new york city": "America/New_York",
    "miami": "America/New_York",
    "spain": "Europe/Madrid",
    "london": "Europe/London",
    "tokyo": "Asia/Tokyo",
}

def derive_fan_timezone(city: str | None, country: str | None = None) -> str:
    if not city:
        return "UNKNOWN"
    key = city.lower().strip()
    if key in _CITY_TZ:
        return _CITY_TZ[key]
    if country and country.lower().strip() in _CITY_TZ:
        return _CITY_TZ[country.lower().strip()]
    return "UNKNOWN"

def current_local_time(timezone_str: str | None) -> str | None:
    if not timezone_str or timezone_str == "UNKNOWN":
        return None
    if ZoneInfo is None:
        # Fallback for Windows without tzdata: return dummy time for tests
        return "12:00"
    try:
        tz = ZoneInfo(timezone_str)
        now = datetime.now(tz)
        return now.strftime("%H:%M")
    except Exception:
        # Fallback dummy
        return "12:00"

def temporal_context_for_fan(fan_knowledge: list[dict[str, Any]]) -> dict[str, Any]:    # Find city from fan_knowledge
    city = None
    for k in fan_knowledge:
        if k.get("subject") == "city" and k.get("status") == "CURRENT":
            city = k.get("value")
            break
    tz = derive_fan_timezone(city)
    local = current_local_time(tz)
    # Check for temporary location (Spain)
    temp_city = None
    for k in fan_knowledge:
        if k.get("temporal_type") == "TEMPORARY" and k.get("subject") in ("city","trip") and k.get("status") == "CURRENT":
            # Check not expired
            from commerce.fan_knowledge import is_knowledge_expired
            if not is_knowledge_expired(k):
                temp_city = k.get("value")
                tz = derive_fan_timezone(temp_city)
                local = current_local_time(tz)
                break
    return {"timezone": tz, "local_time": local, "city": temp_city or city}


#: Default creator zone (single-creator app; persona defaults are NYC).
#: Used only when the structured persona carries no usable location.
CREATOR_DEFAULT_TZ = "America/New_York"


def _creator_zone_from_location(location: Any) -> str:
    """Resolve an IANA zone from creator location data (pure, never raises).

    Accepts a location mapping (city/state/country/timezone keys) or a
    plain string (city name or IANA zone). An explicit valid IANA zone in
    a ``timezone`` field wins; otherwise the city maps through the same
    table as fan cities; otherwise the NYC default. Never raises.
    """
    try:
        if isinstance(location, dict):
            raw_tz = location.get("timezone")
            if isinstance(raw_tz, str) and raw_tz.strip():
                return raw_tz.strip()
            for key in ("city", "state", "country", "hometown"):
                raw = location.get(key)
                if isinstance(raw, str) and raw.strip():
                    zone = _CITY_TZ.get(raw.strip().lower())
                    if zone:
                        return zone
            return CREATOR_DEFAULT_TZ
        if isinstance(location, str) and location.strip():
            text = location.strip()
            if "/" in text:
                return text
            return _CITY_TZ.get(text.lower(), CREATOR_DEFAULT_TZ)
        return CREATOR_DEFAULT_TZ
    except Exception:
        return CREATOR_DEFAULT_TZ


def creator_time_line(location: Any) -> str | None:
    """Render the creator-local-time prompt line (pure, fail-open).

    Returns e.g. ``CREATOR LOCAL TIME: 21:04 EDT (America/New_York)``
    or None when no real clock value is available. Unlike
    :func:`current_local_time` it NEVER returns a dummy fallback: a
    missing zoneinfo database, an unknown zone, or any error yields
    None so callers omit the line instead of printing a false time.
    No DB, no LLM, no clock beyond ``datetime.now`` in the resolved
    zone. Never raises.
    """
    try:
        if ZoneInfo is None:
            return None
        zone = _creator_zone_from_location(location)
        try:
            tz = ZoneInfo(zone)
        except Exception:
            return None
        now = datetime.now(tz)
        clock = now.strftime("%H:%M")
        abbrev = now.strftime("%Z") or "local"
        if not clock:
            return None
        return f"CREATOR LOCAL TIME: {clock} {abbrev} ({zone})"
    except Exception:
        return None
