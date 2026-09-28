"""Phase 4.2 live boards tests (read-only routes, no live DB/Redis).

Covers GET /api/live/quality, /api/live/latency, /api/live/streams with a
fake pool/gauges. Patterns mirror tests/test_dashboard_crm.py (ASGI +
dependency_overrides) and tests/test_outbox_relay.py (FakeRedis).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _ready_creator():
    from commerce.single_creator import SingleCreatorStatus

    ctx = MagicMock()
    ctx.status = SingleCreatorStatus.READY
    ctx.creator_id = 7
    return ctx


def _override_auth():
    from chatbotv2.dashboard.app import app
    from chatbotv2.dashboard.auth import require_auth

    app.dependency_overrides[require_auth] = lambda: {"username": "admin"}


def _clear_auth():
    from chatbotv2.dashboard.app import app

    app.dependency_overrides.clear()


class _FakeConn:
    def __init__(self, script):
        # script: list of rows-or-row to return per fetch/fetchrow call.
        self._script = list(script)
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch(self, query, *args):
        self.queries.append(query)
        assert "creator_id" in query or "creator_id" in str(args), "must be creator-scoped"
        assert "INSERT" not in query.upper() and "UPDATE" not in query.upper()
        result = self._script.pop(0) if self._script else []
        return result if isinstance(result, list) else [result]

    async def fetchrow(self, query, *args):
        self.queries.append(query)
        assert "INSERT" not in query.upper() and "UPDATE" not in query.upper()
        result = self._script.pop(0) if self._script else None
        if isinstance(result, list):
            return result[0] if result else None
        return result


class _FakePool:
    def __init__(self, script):
        self._conn = _FakeConn(script)
        self.script = script

    def acquire(self):
        return self._conn


def _vrow(**over):
    row = {
        "turns": 10,
        "prompt_echo_turns": 2,
        "safety_failures": 1,
        "quality_failures": 1,
        "auto_approved": 4,
        "operator_queued": 6,
        "advisory_handoffs": 3,
        "corroborated_handoffs": 1,
        "unrecorded_verdict": 4,
    }
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_quality_empty_db_zeros_200():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app

    pool = _FakePool(
        [
            _vrow(
                turns=0,
                prompt_echo_turns=0,
                safety_failures=0,
                quality_failures=0,
                auto_approved=0,
                operator_queued=0,
                advisory_handoffs=0,
                corroborated_handoffs=0,
                unrecorded_verdict=0,
            ),
            [],
            {"outbound_turns": 0, "repeat_pairs": 0},
            {"dedup_hits": 0, "already_executed": 0, "inbound_redeliveries": 0, "turns": 0},
        ]
    )
    with (
        patch(
            "chatbotv2.dashboard.routes.live.resolve_single_application_creator",
            new=AsyncMock(return_value=_ready_creator()),
        ),
        patch("chatbotv2.dashboard.routes.live.get_pool", new=AsyncMock(return_value=pool)),
    ):
        _override_auth()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/live/quality?window_hours=24")
        finally:
            _clear_auth()
    assert resp.status_code == 200
    body = resp.json()
    assert body["turns"] == 0 and body["repeat_pairs"] == 0 and body["dedup_hits"] == 0
    assert body["veto_split"] == [] and body["degraded"] is False


@pytest.mark.asyncio
async def test_quality_null_verdicts_unrecorded_bucket():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app

    pool = _FakePool(
        [
            _vrow(turns=5, unrecorded_verdict=5, advisory_handoffs=0, corroborated_handoffs=0),
            [{"veto": "unrecorded", "turns": 5}],
            {"outbound_turns": 5, "repeat_pairs": 1},
            {"dedup_hits": 2, "already_executed": 0, "inbound_redeliveries": 1, "turns": 5},
        ]
    )
    with (
        patch(
            "chatbotv2.dashboard.routes.live.resolve_single_application_creator",
            new=AsyncMock(return_value=_ready_creator()),
        ),
        patch("chatbotv2.dashboard.routes.live.get_pool", new=AsyncMock(return_value=pool)),
    ):
        _override_auth()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/live/quality?window_hours=24")
        finally:
            _clear_auth()
    assert resp.status_code == 200
    body = resp.json()
    assert body["unrecorded_verdict"] == 5
    assert body["veto_split"] == [{"veto": "unrecorded", "turns": 5}]
    assert body["turns"] == 5 and body["repeat_pairs"] == 1 and body["dedup_hits"] == 2


@pytest.mark.asyncio
async def test_quality_db_error_degraded_not_500():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from db import postgres as _pg

    async def _boom():
        raise RuntimeError("pg down")

    with (
        patch(
            "chatbotv2.dashboard.routes.live.resolve_single_application_creator",
            new=AsyncMock(return_value=_ready_creator()),
        ),
        patch("chatbotv2.dashboard.routes.live.get_pool", new=AsyncMock(side_effect=_boom)),
        patch.object(_pg, "get_pool", new=AsyncMock(side_effect=_boom)),
    ):
        _override_auth()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/live/quality")
        finally:
            _clear_auth()
    assert resp.status_code == 200
    assert resp.json()["degraded"] is True


@pytest.mark.asyncio
async def test_latency_p50_p95_and_empty():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app

    for script, turns, p50, p95 in [
        ([{"turns": 4, "p50": 800.0, "p95": 1500.0}], 4, 800.0, 1500.0),
        ([{"turns": 0, "p50": None, "p95": None}], 0, None, None),
    ]:
        pool = _FakePool(script)
        with (
            patch(
                "chatbotv2.dashboard.routes.live.resolve_single_application_creator",
                new=AsyncMock(return_value=_ready_creator()),
            ),
            patch("chatbotv2.dashboard.routes.live.get_pool", new=AsyncMock(return_value=pool)),
        ):
            _override_auth()
            try:
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.get("/api/live/latency?window_hours=24")
            finally:
                _clear_auth()
        assert resp.status_code == 200
        body = resp.json()
        assert body["turns"] == turns and body["p50_ms"] == p50 and body["p95_ms"] == p95
        assert body["degraded"] is False


@pytest.mark.asyncio
async def test_streams_gauges_and_scheduler_lag():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from db import redis as _r

    pool = _FakePool([{"oldest_pending": None, "pending_count": 0}])
    with (
        patch(
            "chatbotv2.dashboard.routes.live.resolve_single_application_creator",
            new=AsyncMock(return_value=_ready_creator()),
        ),
        patch("chatbotv2.dashboard.routes.live.get_pool", new=AsyncMock(return_value=pool)),
        patch.object(_r, "get_send_pending_count", new=AsyncMock(return_value=3)),
        patch.object(_r, "get_inbound_pending_count", new=AsyncMock(return_value=1)),
        patch.object(_r, "get_send_stream_length", new=AsyncMock(return_value=40)),
        patch.object(_r, "get_inbound_stream_length", new=AsyncMock(return_value=12)),
        patch.object(_r, "get_dlq_age_seconds", new=AsyncMock(return_value=90)),
    ):
        _override_auth()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/live/streams")
        finally:
            _clear_auth()
    assert resp.status_code == 200
    body = resp.json()
    assert body["send_pending"] == 3 and body["inbound_pending"] == 1
    assert body["send_stream_length"] == 40 and body["dlq_age_seconds"] == 90
    assert body["scheduler_lag_seconds"] == 0 and body["degraded"] is False


@pytest.mark.asyncio
async def test_boards_auth_creator_window():
    from httpx import ASGITransport, AsyncClient

    from chatbotv2.dashboard.app import app
    from commerce.single_creator import SingleCreatorStatus

    _clear_auth()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/api/live/quality", "/api/live/latency", "/api/live/streams"):
            assert (await client.get(path)).status_code == 401, path

    not_ready = MagicMock()
    not_ready.status = SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE
    not_ready.creator_id = None
    with patch(
        "chatbotv2.dashboard.routes.live.resolve_single_application_creator",
        new=AsyncMock(return_value=not_ready),
    ):
        _override_auth()
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                assert (await client.get("/api/live/quality")).status_code == 503
        finally:
            _clear_auth()

    with patch(
        "chatbotv2.dashboard.routes.live.resolve_single_application_creator",
        new=AsyncMock(return_value=_ready_creator()),
    ):
        _override_auth()
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                assert (await client.get("/api/live/quality?window_hours=999")).status_code == 422
        finally:
            _clear_auth()
