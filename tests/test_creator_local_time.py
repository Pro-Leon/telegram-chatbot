"""Creator-local-time injection tests (deterministic, no DB, no network).

Pins the option-1 contract: per-turn server-computed creator clock,
distinctly labeled from the fan LOCAL TIME line, fail-open (omitted,
never a dummy).
"""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from commerce import temporal_context as tc

LINE_RE = re.compile(r"^CREATOR LOCAL TIME: \d{2}:\d{2} \S+ \(([^)]+)\)$")


def test_nyc_dict_yields_new_york_line():
    line = tc.creator_time_line({"city": "New York City", "state": "NY"})
    assert line is not None
    m = LINE_RE.match(line)
    assert m and m.group(1) == "America/New_York"


def test_explicit_zone_string_wins():
    line = tc.creator_time_line("Europe/London")
    assert line is not None
    assert "(Europe/London)" in line


def test_explicit_timezone_field_wins_over_city():
    line = tc.creator_time_line({"city": "Chicago", "timezone": "Asia/Tokyo"})
    assert line is not None
    assert "(Asia/Tokyo)" in line


def test_unknown_city_falls_back_to_nyc_default():
    line = tc.creator_time_line({"city": "Nowhereville"})
    assert line is not None
    assert "(America/New_York)" in line


def test_none_location_falls_back_to_nyc_default():
    assert tc.creator_time_line(None) is not None


def test_bad_zone_yields_none_never_dummy():
    assert tc.creator_time_line("Not/AZone") is None


def test_odd_inputs_never_raise_never_dummy():
    for bad in ("", "   ", 123, ["x"], {"city": 123}, object()):
        assert tc.creator_time_line(bad) is None or LINE_RE.match(tc.creator_time_line(bad))


def test_missing_zoneinfo_yields_none():
    with patch.object(tc, "ZoneInfo", None):
        assert tc.creator_time_line({"city": "New York City"}) is None


def test_creator_line_unconfusable_with_fan_line():
    line = tc.creator_time_line({"city": "New York City"})
    assert line is not None
    assert line.startswith("CREATOR LOCAL TIME:")
    assert "LOCAL TIME: " not in line.replace("CREATOR LOCAL TIME:", "")


@pytest.mark.asyncio
async def test_gatherer_temporal_appends_creator_line():
    from context_engine.gatherer import GathererConfig, TemporalSource

    auth = SimpleNamespace(structured_persona={"location": {"city": "New York City"}})
    config = GathererConfig(creator_id=7, user_id=9, authoritative_state=auth)
    fan_know = [
        {"subject": "city", "value": "Chicago", "status": "CURRENT"},
    ]
    with patch(
        "commerce.fan_knowledge.get_fan_knowledge",
        new_callable=AsyncMock,
        return_value=fan_know,
    ):
        text = await TemporalSource()._get_temporal_safe(config)
    assert text is not None
    assert "Fan city: Chicago" in text
    assert "CREATOR LOCAL TIME:" in text
    assert "(America/New_York)" in text


@pytest.mark.asyncio
async def test_gatherer_temporal_without_snapshot_still_fan_parts():
    from context_engine.gatherer import GathererConfig, TemporalSource

    config = GathererConfig(creator_id=7, user_id=9, authoritative_state=None)
    fan_know = [
        {"subject": "city", "value": "Chicago", "status": "CURRENT"},
    ]
    with patch(
        "commerce.fan_knowledge.get_fan_knowledge",
        new_callable=AsyncMock,
        return_value=fan_know,
    ):
        text = await TemporalSource()._get_temporal_safe(config)
    assert text is not None
    assert "Fan city: Chicago" in text
    # No snapshot: location None -> NYC default still renders (documented).
    assert "CREATOR LOCAL TIME:" in text
