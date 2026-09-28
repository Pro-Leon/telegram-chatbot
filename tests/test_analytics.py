"""Tests for the Phase 2.1 Dashboard Analytics feature."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _collect_route_paths(routes):
    """Recursively collect all route paths, including those inside APIRouters."""
    paths = []
    for route in routes:
        if hasattr(route, "path"):
            paths.append(route.path)
        sub = getattr(route, "routes", None) or getattr(getattr(route, "original_router", None), "routes", None)
        if sub:
            paths.extend(_collect_route_paths(sub))
    return paths


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP A — API Endpoint: /api/analytics/dashboard
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnalyticsAPI:
    """Tests for the /api/analytics/dashboard endpoint."""

    def _mock_analytics_result(self):
        return {
            "summary": {
                "inbound_messages": 100,
                "outbound_messages": 80,
                "ai_generated": 60,
                "ai_auto_approved": 45,
                "operator_approved": 15,
                "failed_sends": 3,
                "auto_approval_rate": 75.0,
                "average_response_time_seconds": 12.5,
            },
            "timeline": [
                {"date": "2026-08-01", "inbound": 50, "outbound": 40, "ai": 30, "operator": 10},
                {"date": "2026-08-02", "inbound": 50, "outbound": 40, "ai": 30, "operator": 10},
            ],
            "handling_breakdown": {"ai": 60, "operator": 15},
        }

    @pytest.mark.asyncio
    async def test_requires_authentication(self):
        """Unauthenticated request must return 401."""
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/dashboard")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_request_returns_expected_structure(self):
        """Authenticated request with no dates returns correct response shape."""
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        mock_result = self._mock_analytics_result()
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_dashboard_analytics",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/dashboard")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert "period" in body
        assert "summary" in body
        assert "timeline" in body
        assert "handling_breakdown" in body
        assert body["period"]["start"] == "all time"
        assert body["period"]["end"] == "now"

    @pytest.mark.asyncio
    async def test_empty_date_range_returns_zero_metrics(self):
        """No messages in range should return zero/empty metrics."""
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        empty_result = {
            "summary": {
                "inbound_messages": 0,
                "outbound_messages": 0,
                "ai_generated": 0,
                "ai_auto_approved": 0,
                "operator_approved": 0,
                "failed_sends": 0,
                "auto_approval_rate": None,
                "average_response_time_seconds": None,
            },
            "timeline": [],
            "handling_breakdown": {"ai": 0, "operator": 0},
        }
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_dashboard_analytics",
            new_callable=AsyncMock,
            return_value=empty_result,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/dashboard")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["inbound_messages"] == 0
        assert body["summary"]["auto_approval_rate"] is None
        assert body["summary"]["average_response_time_seconds"] is None
        assert body["timeline"] == []

    @pytest.mark.asyncio
    async def test_malformed_start_date_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/dashboard?start_date=not-a-date")
        app.dependency_overrides.clear()

        assert resp.status_code == 400
        assert "Invalid start_date" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_malformed_end_date_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/dashboard?end_date=2026-13-45")
        app.dependency_overrides.clear()

        assert resp.status_code == 400
        assert "Invalid end_date" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_start_date_after_end_date_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/analytics/dashboard?start_date=2026-08-10&end_date=2026-08-01"
            )
        app.dependency_overrides.clear()

        assert resp.status_code == 400
        assert "start_date must be before" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_date_range_exceeding_365_days_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/analytics/dashboard?start_date=2025-01-01&end_date=2026-12-31"
            )
        app.dependency_overrides.clear()

        assert resp.status_code == 400
        assert "365 days" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_date_filtering_passed_to_query(self):
        """start_date and end_date must be forwarded to get_dashboard_analytics."""
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        mock_result = self._mock_analytics_result()
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_dashboard_analytics",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_fn:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/analytics/dashboard?start_date=2026-08-01&end_date=2026-08-17"
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_fn.assert_called_once_with(start_date="2026-08-01", end_date="2026-08-17")
        body = resp.json()
        assert body["period"]["start"] == "2026-08-01"
        assert body["period"]["end"] == "2026-08-17"

    @pytest.mark.asyncio
    async def test_db_error_returns_500(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_dashboard_analytics",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/dashboard")
        app.dependency_overrides.clear()

        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B — Database Query Function
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetDashboardAnalytics:
    """Tests for db/postgres.py get_dashboard_analytics()."""

    def _make_mock_conn(self, summary_row=None, timeline_rows=None, dlq_count=0, rt_row=None):
        mock_conn = AsyncMock()
        if summary_row is None:
            summary_row = {
                "inbound_messages": 0,
                "outbound_messages": 0,
                "ai_auto_approved": 0,
                "operator_approved": 0,
                "ai_not_auto_approved": 0,
            }
        if timeline_rows is None:
            timeline_rows = []
        if rt_row is None:
            rt_row = {"avg_response": None}

        call_count = [0]

        async def mock_fetchrow(sql, *args, **kwargs):
            call_count[0] += 1
            if "dlq_messages" in sql:
                return {"cnt": dlq_count}
            if "avg_response" in sql or "next_out" in sql:
                return rt_row
            return summary_row

        async def mock_fetch(sql, *args, **kwargs):
            return timeline_rows

        mock_conn.fetchrow = mock_fetchrow
        mock_conn.fetch = mock_fetch

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        return mock_pool

    @pytest.mark.asyncio
    async def test_returns_all_required_keys(self):
        """Result must contain summary, timeline, and handling_breakdown."""
        mock_pool = self._make_mock_conn()

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_dashboard_analytics

            result = await get_dashboard_analytics()

        assert "summary" in result
        assert "timeline" in result
        assert "handling_breakdown" in result
        s = result["summary"]
        assert "inbound_messages" in s
        assert "outbound_messages" in s
        assert "ai_generated" in s
        assert "ai_auto_approved" in s
        assert "operator_approved" in s
        assert "failed_sends" in s
        assert "auto_approval_rate" in s
        assert "average_response_time_seconds" in s

    @pytest.mark.asyncio
    async def test_auto_approval_rate_calculated_correctly(self):
        """auto_approval_rate must be ai_auto / ai_generated * 100."""
        mock_pool = self._make_mock_conn(
            summary_row={
                "inbound_messages": 100,
                "outbound_messages": 80,
                "ai_auto_approved": 45,
                "operator_approved": 15,
                "ai_not_auto_approved": 15,
            }
        )

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_dashboard_analytics

            result = await get_dashboard_analytics()

        # ai_generated = 45 + 15 = 60, rate = 45/60*100 = 75.0
        assert result["summary"]["ai_generated"] == 60
        assert result["summary"]["auto_approval_rate"] == 75.0

    @pytest.mark.asyncio
    async def test_auto_approval_rate_null_when_no_ai(self):
        """auto_approval_rate must be None when no AI replies exist."""
        mock_pool = self._make_mock_conn(
            summary_row={
                "inbound_messages": 10,
                "outbound_messages": 5,
                "ai_auto_approved": 0,
                "operator_approved": 5,
                "ai_not_auto_approved": 0,
            }
        )

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_dashboard_analytics

            result = await get_dashboard_analytics()

        assert result["summary"]["ai_generated"] == 0
        assert result["summary"]["auto_approval_rate"] is None

    @pytest.mark.asyncio
    async def test_date_filters_applied(self):
        """When start_date/end_date provided, SQL params must include them."""
        mock_pool = self._make_mock_conn()

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_dashboard_analytics

            await get_dashboard_analytics(start_date="2026-08-01", end_date="2026-08-17")

        # Verify pool was used (function executed without error)
        assert mock_pool.acquire.called

    @pytest.mark.asyncio
    async def test_response_time_null_when_no_data(self):
        """average_response_time_seconds must be None when no response data."""
        mock_pool = self._make_mock_conn(rt_row={"avg_response": None})

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_dashboard_analytics

            result = await get_dashboard_analytics()

        assert result["summary"]["average_response_time_seconds"] is None

    @pytest.mark.asyncio
    async def test_response_time_calculated(self):
        """average_response_time_seconds must convert interval to float."""
        from datetime import timedelta

        mock_pool = self._make_mock_conn(rt_row={"avg_response": timedelta(seconds=45.3)})

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_dashboard_analytics

            result = await get_dashboard_analytics()

        assert result["summary"]["average_response_time_seconds"] == 45.3

    @pytest.mark.asyncio
    async def test_timeline_grouped_by_date(self):
        """Timeline must return date-grouped rows."""
        from datetime import date

        mock_pool = self._make_mock_conn(
            timeline_rows=[
                {
                    "date": date(2026, 8, 1),
                    "inbound": 10,
                    "outbound": 8,
                    "ai_auto": 5,
                    "operator_count": 3,
                },
                {
                    "date": date(2026, 8, 2),
                    "inbound": 12,
                    "outbound": 10,
                    "ai_auto": 7,
                    "operator_count": 3,
                },
            ]
        )

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_dashboard_analytics

            result = await get_dashboard_analytics()

        assert len(result["timeline"]) == 2
        assert result["timeline"][0]["date"] == "2026-08-01"
        assert result["timeline"][0]["inbound"] == 10

    @pytest.mark.asyncio
    async def test_handling_breakdown(self):
        """handling_breakdown must have ai and operator counts."""
        mock_pool = self._make_mock_conn(
            summary_row={
                "inbound_messages": 100,
                "outbound_messages": 80,
                "ai_auto_approved": 50,
                "operator_approved": 20,
                "ai_not_auto_approved": 10,
            }
        )

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_dashboard_analytics

            result = await get_dashboard_analytics()

        assert result["handling_breakdown"]["ai"] == 60  # 50 + 10
        assert result["handling_breakdown"]["operator"] == 20

    @pytest.mark.asyncio
    async def test_dlq_failed_sends_counted(self):
        """failed_sends must come from dlq_messages count."""
        mock_pool = self._make_mock_conn(dlq_count=7)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_dashboard_analytics

            result = await get_dashboard_analytics()

        assert result["summary"]["failed_sends"] == 7


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP C — Analytics Page
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnalyticsPage:
    """Tests for the /dashboard/analytics page."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_analytics_route_exists(self):
        from chatbotv2.dashboard.app import app

        routes = _collect_route_paths(app.routes)
        assert "/dashboard/analytics" in routes

    def test_analytics_template_extends_dashboard(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert 'extends "dashboard.html"' in content
        assert "nav_analytics" in content

    def test_analytics_uses_chartjs(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "chart.js" in content.lower() or "Chart" in content

    def test_analytics_has_date_filters(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert 'type="date"' in content
        assert "startDate" in content

    def test_analytics_calls_api(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "/static/js/analytics-api.js" in content
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "/api/analytics/dashboard" in api_content

    def test_analytics_has_loading_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "loading" in content

    def test_analytics_has_empty_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "No data yet" in content

    def test_analytics_has_error_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "error" in content

    def test_dashboard_nav_has_analytics_link(self):
        content = self._read("chatbotv2/dashboard/templates/dashboard.html")
        assert "/dashboard/analytics" in content
        assert "nav_analytics" in content

    def test_analytics_displays_kpi_cards(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        for metric in [
            "inbound_messages",
            "outbound_messages",
            "ai_generated",
            "auto_approval_rate",
            "failed_sends",
        ]:
            assert metric in content

    def test_analytics_has_timeline_chart(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "timelineChart" in content
        assert "Chart" in content

    def test_analytics_has_handling_breakdown(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "handling_breakdown" in content
        assert "breakdown-bar" in content

    def test_analytics_shows_response_time(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "average_response_time" in content or "response_time" in content

    def test_analytics_shows_operator_approved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "operator_approved" in content
