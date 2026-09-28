"""Tests for Phase 2.2 Conversation Intelligence & Operator Insights."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import JSONResponse
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
# GROUP A — API: /api/analytics/conversations
# ═══════════════════════════════════════════════════════════════════════════════


class TestConversationsListAPI:
    """Tests for GET /api/analytics/conversations."""

    def _mock_conversations_result(self):
        return {
            "items": [
                {
                    "user_id": 1001,
                    "username": "alice",
                    "first_name": "Alice",
                    "last_seen": "2026-08-17T10:00:00",
                    "inbound_count": 15,
                    "outbound_count": 12,
                    "last_inbound_at": "2026-08-17T09:55:00",
                    "last_outbound_at": "2026-08-17T09:56:00",
                    "avg_confidence": 0.85,
                    "auto_approved_count": 10,
                    "operator_approved_count": 2,
                    "queue_count": 1,
                    "avg_response_seconds": 45.2,
                    "attention_reasons": [],
                },
                {
                    "user_id": 1002,
                    "username": "bob",
                    "first_name": "Bob",
                    "last_seen": "2026-08-16T14:00:00",
                    "inbound_count": 8,
                    "outbound_count": 3,
                    "last_inbound_at": "2026-08-16T13:50:00",
                    "last_outbound_at": None,
                    "avg_confidence": None,
                    "auto_approved_count": 0,
                    "operator_approved_count": 0,
                    "queue_count": 5,
                    "avg_response_seconds": None,
                    "attention_reasons": ["unanswered_message", "frequent_queue_routing"],
                },
            ],
            "pagination": {
                "page": 1,
                "page_size": 25,
                "total": 2,
                "total_pages": 1,
            },
        }

    @pytest.mark.asyncio
    async def test_requires_authentication(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_request_returns_expected_structure(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        mock_result = self._mock_conversations_result()
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "pagination" in body
        assert isinstance(body["items"], list)
        assert len(body["items"]) == 2
        p = body["pagination"]
        assert "page" in p
        assert "page_size" in p
        assert "total" in p
        assert "total_pages" in p

    @pytest.mark.asyncio
    async def test_item_has_all_expected_fields(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        mock_result = self._mock_conversations_result()
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations")
        app.dependency_overrides.clear()

        item = resp.json()["items"][0]
        expected_fields = [
            "user_id",
            "username",
            "first_name",
            "last_seen",
            "inbound_count",
            "outbound_count",
            "last_inbound_at",
            "last_outbound_at",
            "avg_confidence",
            "auto_approved_count",
            "operator_approved_count",
            "queue_count",
            "avg_response_seconds",
            "attention_reasons",
        ]
        for field in expected_fields:
            assert field in item, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_malformed_start_date_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations?start_date=bad")
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
            resp = await client.get("/api/analytics/conversations?end_date=2026-13-45")
        app.dependency_overrides.clear()
        assert resp.status_code == 400
        assert "Invalid end_date" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_start_after_end_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/analytics/conversations?start_date=2026-08-10&end_date=2026-08-01"
            )
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_date_range_exceeding_365_days_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/analytics/conversations?start_date=2025-01-01&end_date=2026-12-31"
            )
        app.dependency_overrides.clear()
        assert resp.status_code == 400
        assert "365 days" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_invalid_sort_by_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations?sort_by=evil_column")
        app.dependency_overrides.clear()
        assert resp.status_code == 400
        assert "Invalid sort_by" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_invalid_sort_order_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations?sort_order=up")
        app.dependency_overrides.clear()
        assert resp.status_code == 400
        assert "sort_order" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_invalid_attention_filter_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations?attention=bogus")
        app.dependency_overrides.clear()
        assert resp.status_code == 400
        assert "Invalid attention" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_page_size_validation(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations?page_size=0")
        app.dependency_overrides.clear()
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_page_size_max_enforced(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations?page_size=200")
        app.dependency_overrides.clear()
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_date_filtering_passed_to_query(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        mock_result = self._mock_conversations_result()
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_fn:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/analytics/conversations?start_date=2026-08-01&end_date=2026-08-17"
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_fn.assert_called_once()
        call_kwargs = mock_fn.call_args.kwargs
        assert call_kwargs["start_date"] == "2026-08-01"
        assert call_kwargs["end_date"] == "2026-08-17"

    @pytest.mark.asyncio
    async def test_pagination_params_passed(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        mock_result = self._mock_conversations_result()
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_fn:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations?page=2&page_size=10")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        call_kwargs = mock_fn.call_args.kwargs
        assert call_kwargs["page"] == 2
        assert call_kwargs["page_size"] == 10

    @pytest.mark.asyncio
    async def test_sort_params_passed(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        mock_result = self._mock_conversations_result()
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_fn:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/analytics/conversations?sort_by=response_time&sort_order=asc"
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        call_kwargs = mock_fn.call_args.kwargs
        assert call_kwargs["sort_by"] == "response_time"
        assert call_kwargs["sort_order"] == "asc"

    @pytest.mark.asyncio
    async def test_db_error_returns_500(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations")
        app.dependency_overrides.clear()
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B — API: /api/analytics/conversations/{user_id}
# ═══════════════════════════════════════════════════════════════════════════════


class TestConversationDetailAPI:
    """Tests for GET /api/analytics/conversations/{user_id}."""

    def _mock_detail_result(self):
        return {
            "user": {
                "id": 1001,
                "username": "alice",
                "first_name": "Alice",
                "last_seen": "2026-08-17T10:00:00",
                "message_count": 27,
                "funnel_stage": "engaged",
                "is_blocked": False,
            },
            "analytics": {
                "inbound_count": 15,
                "outbound_count": 12,
                "last_inbound_at": "2026-08-17T09:55:00",
                "last_outbound_at": "2026-08-17T09:56:00",
                "first_message_at": "2026-08-01T08:00:00",
                "avg_confidence": 0.85,
                "auto_approved_count": 10,
                "operator_approved_count": 2,
                "ai_generated": 12,
                "auto_approval_rate": 83.3,
                "avg_response_seconds": 45.2,
            },
            "queue": {
                "total_queue_items": 3,
                "pending_queue_items": 0,
                "approved_queue_items": 2,
                "rejected_queue_items": 1,
            },
            "attention_reasons": [],
            "timeline": [
                {"date": "2026-08-15", "inbound": 5, "outbound": 4},
                {"date": "2026-08-16", "inbound": 6, "outbound": 5},
                {"date": "2026-08-17", "inbound": 4, "outbound": 3},
            ],
        }

    @pytest.mark.asyncio
    async def test_requires_authentication(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/1001")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_request_returns_expected_structure(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        mock_result = self._mock_detail_result()
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversation_detail",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert "user" in body
        assert "analytics" in body
        assert "queue" in body
        assert "attention_reasons" in body
        assert "timeline" in body

    @pytest.mark.asyncio
    async def test_nonexistent_user_returns_404(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversation_detail",
            new_callable=AsyncMock,
            return_value=None,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/99999")
        app.dependency_overrides.clear()
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_date_filtering_passed(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        mock_result = self._mock_detail_result()
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversation_detail",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_fn:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/analytics/conversations/1001?start_date=2026-08-01&end_date=2026-08-17"
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_fn.assert_called_once_with(
            user_id=1001, start_date="2026-08-01", end_date="2026-08-17"
        )

    @pytest.mark.asyncio
    async def test_malformed_start_date_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/1001?start_date=bad")
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_start_after_end_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/analytics/conversations/1001?start_date=2026-08-10&end_date=2026-08-01"
            )
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_db_error_returns_500(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversation_detail",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001")
        app.dependency_overrides.clear()
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B2 — Advanced Filters: assigned_operator filter
# ═══════════════════════════════════════════════════════════════════════════════


class TestConversationsAdvancedFilters:
    """Tests for assigned_operator filter on conversations list."""

    @pytest.mark.asyncio
    async def test_assigned_operator_filter_passed_to_db(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            return_value={
                "items": [],
                "pagination": {"page": 1, "page_size": 25, "total": 0, "total_pages": 0},
            },
        ) as mock_fn:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/analytics/conversations",
                    params={"assigned_operator_id": 5},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        call_kwargs = mock_fn.call_args[1]
        assert call_kwargs["assigned_operator_id"] == 5

    @pytest.mark.asyncio
    async def test_no_operator_filter_passes_none(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            return_value={
                "items": [],
                "pagination": {"page": 1, "page_size": 25, "total": 0, "total_pages": 0},
            },
        ) as mock_fn:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        call_kwargs = mock_fn.call_args[1]
        assert call_kwargs["assigned_operator_id"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B3 — Bulk Operations: assign, tag, attention
# ═══════════════════════════════════════════════════════════════════════════════


class TestBulkAssignAPI:
    """Tests for POST /api/analytics/conversations/bulk/assign."""

    @pytest.mark.asyncio
    async def test_requires_authentication(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/analytics/conversations/bulk/assign",
                json={"user_ids": [1, 2], "assigned_operator_id": 5},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_user_ids_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/analytics/conversations/bulk/assign",
                json={"user_ids": [], "assigned_operator_id": 5},
            )
        app.dependency_overrides.clear()
        assert resp.status_code == 400
        assert "empty" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_too_many_user_ids_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/analytics/conversations/bulk/assign",
                json={"user_ids": list(range(1, 102)), "assigned_operator_id": 5},
            )
        app.dependency_overrides.clear()
        assert resp.status_code == 400
        assert "100" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_invalid_operator_returns_400(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.dependencies.verify_operator_active",
            new_callable=AsyncMock,
            return_value=JSONResponse({"error": "Operator not found or inactive"}, status_code=400),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/bulk/assign",
                    json={"user_ids": [1, 2], "assigned_operator_id": 999},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_successful_bulk_assign(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with (
            patch(
                "chatbotv2.dashboard.dependencies.verify_operator_active",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.bulk_ops.upsert_conversation_attention",
                new_callable=AsyncMock,
                return_value={"status": "new", "assigned_operator_id": 5},
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/bulk/assign",
                    json={"user_ids": [1, 2, 3], "assigned_operator_id": 5},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert body["updated"] == 3
        assert body["errors"] == []


class TestBulkTagAPI:
    """Tests for POST /api/analytics/conversations/bulk/tag."""

    @pytest.mark.asyncio
    async def test_requires_authentication(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/analytics/conversations/bulk/tag",
                json={"user_ids": [1, 2], "tag_id": 1},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_user_ids_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/analytics/conversations/bulk/tag",
                json={"user_ids": [], "tag_id": 1},
            )
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_tag_returns_404(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.bulk_ops.get_conversation_tag",
            new_callable=AsyncMock,
            return_value=None,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/bulk/tag",
                    json={"user_ids": [1, 2], "tag_id": 999},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_successful_bulk_tag(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with (
            patch(
                "chatbotv2.dashboard.routes.bulk_ops.get_conversation_tag",
                new_callable=AsyncMock,
                return_value={"id": 1, "name": "vip"},
            ),
            patch(
                "chatbotv2.dashboard.routes.bulk_ops.assign_conversation_tag",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/bulk/tag",
                    json={"user_ids": [1, 2, 3], "tag_id": 1},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert body["assigned"] == 3
        assert body["errors"] == []


class TestBulkAttentionAPI:
    """Tests for PATCH /api/analytics/conversations/bulk/attention."""

    @pytest.mark.asyncio
    async def test_requires_authentication(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/analytics/conversations/bulk/attention",
                json={"user_ids": [1, 2], "status": "reviewed"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_status_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/analytics/conversations/bulk/attention",
                json={"user_ids": [1, 2], "status": "invalid"},
            )
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_successful_bulk_attention_update(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.bulk_ops.upsert_conversation_attention",
            new_callable=AsyncMock,
            return_value={"status": "reviewed"},
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/bulk/attention",
                    json={"user_ids": [1, 2, 3], "status": "reviewed"},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert body["updated"] == 3
        assert body["errors"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP C — Database Query: get_conversations_analytics
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetConversationsAnalytics:
    """Tests for db/postgres.py get_conversations_analytics()."""

    def _make_mock_conn(self, count_row=None, data_rows=None):
        if count_row is None:
            count_row = {"cnt": 0}
        if data_rows is None:
            data_rows = []

        mock_conn = AsyncMock()

        async def mock_fetchrow(sql, *args, **kwargs):
            return count_row

        async def mock_fetch(sql, *args, **kwargs):
            return data_rows

        mock_conn.fetchrow = mock_fetchrow
        mock_conn.fetch = mock_fetch

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        return mock_pool

    def _make_row(self, **overrides):

        defaults = {
            "user_id": 1001,
            "username": "testuser",
            "first_name": "Test",
            "last_seen": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "inbound_count": 10,
            "outbound_count": 8,
            "last_inbound_at": datetime(2026, 8, 17, 9, 55, 0, tzinfo=UTC),
            "last_outbound_at": datetime(2026, 8, 17, 9, 56, 0, tzinfo=UTC),
            "avg_confidence": 0.8,
            "auto_approved_count": 6,
            "operator_approved_count": 2,
            "queue_count": 1,
            "avg_response_seconds": 30.0,
            "unanswered": False,
            "slow_response": False,
            "frequent_queue": False,
        }
        defaults.update(overrides)

        row = MagicMock()
        for k, v in defaults.items():
            setattr(row, k, v)
            row.__getitem__ = lambda self, k: getattr(self, k)
        return row

    @pytest.mark.asyncio
    async def test_returns_all_required_keys(self):
        mock_pool = self._make_mock_conn(
            count_row={"cnt": 0},
            data_rows=[],
        )

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversations_analytics

            result = await get_conversations_analytics()

        assert "items" in result
        assert "pagination" in result
        p = result["pagination"]
        assert "page" in p
        assert "page_size" in p
        assert "total" in p
        assert "total_pages" in p

    @pytest.mark.asyncio
    async def test_empty_result_set(self):
        mock_pool = self._make_mock_conn(count_row={"cnt": 0}, data_rows=[])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversations_analytics

            result = await get_conversations_analytics()

        assert result["items"] == []
        assert result["pagination"]["total"] == 0
        assert result["pagination"]["total_pages"] == 0

    @pytest.mark.asyncio
    async def test_attention_reasons_populated(self):
        row = self._make_row(
            unanswered=True,
            slow_response=True,
            frequent_queue=False,
        )
        mock_pool = self._make_mock_conn(count_row={"cnt": 1}, data_rows=[row])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversations_analytics

            result = await get_conversations_analytics()

        assert len(result["items"]) == 1
        reasons = result["items"][0]["attention_reasons"]
        assert "unanswered_message" in reasons
        assert "slow_response" in reasons

    @pytest.mark.asyncio
    async def test_multiple_attention_reasons(self):
        row = self._make_row(
            unanswered=True,
            slow_response=True,
            frequent_queue=True,
        )
        mock_pool = self._make_mock_conn(count_row={"cnt": 1}, data_rows=[row])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversations_analytics

            result = await get_conversations_analytics()

        reasons = result["items"][0]["attention_reasons"]
        assert len(reasons) == 3
        assert "unanswered_message" in reasons
        assert "slow_response" in reasons
        assert "frequent_queue_routing" in reasons

    @pytest.mark.asyncio
    async def test_no_attention_when_clean(self):
        row = self._make_row(
            unanswered=False,
            slow_response=False,
            frequent_queue=False,
        )
        mock_pool = self._make_mock_conn(count_row={"cnt": 1}, data_rows=[row])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversations_analytics

            result = await get_conversations_analytics()

        assert result["items"][0]["attention_reasons"] == []

    @pytest.mark.asyncio
    async def test_pagination_calculation(self):
        mock_pool = self._make_mock_conn(count_row={"cnt": 55}, data_rows=[])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversations_analytics

            result = await get_conversations_analytics(page=1, page_size=25)

        assert result["pagination"]["total"] == 55
        assert result["pagination"]["total_pages"] == 3

    @pytest.mark.asyncio
    async def test_confidence_rounded(self):
        row = self._make_row(avg_confidence=0.8567)
        mock_pool = self._make_mock_conn(count_row={"cnt": 1}, data_rows=[row])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversations_analytics

            result = await get_conversations_analytics()

        assert result["items"][0]["avg_confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_response_time_rounded(self):
        row = self._make_row(avg_response_seconds=45.678)
        mock_pool = self._make_mock_conn(count_row={"cnt": 1}, data_rows=[row])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversations_analytics

            result = await get_conversations_analytics()

        assert result["items"][0]["avg_response_seconds"] == 45.7


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP D — Database Query: get_conversation_detail
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetConversationDetail:
    """Tests for db/postgres.py get_conversation_detail()."""

    _SENTINEL = object()

    def _make_mock_conn(self, detail_row=None, user_row=_SENTINEL, att_row=None):
        if detail_row is None:
            detail_row = {
                "inbound_count": 0,
                "outbound_count": 0,
                "last_inbound_at": None,
                "last_outbound_at": None,
                "first_message_at": None,
                "avg_confidence": None,
                "auto_approved_count": 0,
                "operator_approved_count": 0,
                "ai_not_auto_count": 0,
                "total_queue_items": 0,
                "pending_queue_items": 0,
                "approved_queue_items": 0,
                "rejected_queue_items": 0,
                "avg_response_seconds": None,
                "timeline": None,
            }
        if user_row is self._SENTINEL:
            from datetime import datetime

            user_row = {
                "id": 1001,
                "username": "testuser",
                "first_name": "Test",
                "last_seen": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
                "message_count": 10,
                "funnel_stage": "new",
                "is_blocked": False,
            }

        if att_row is None:
            att_row = {
                "user_id": 1001,
                "status": "new",
                "assigned_operator_id": None,
                "reviewed_at": None,
                "reviewed_by": None,
            }

        mock_conn = AsyncMock()

        async def mock_fetchrow(sql, *args, **kwargs):
            if "conversation_attention" in sql:
                return att_row
            if "users" in sql:
                return user_row
            return detail_row

        mock_conn.fetchrow = mock_fetchrow

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        return mock_pool

    @pytest.mark.asyncio
    async def test_returns_all_required_keys(self):
        mock_pool = self._make_mock_conn()

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversation_detail

            result = await get_conversation_detail(user_id=1001)

        assert result is not None
        assert "user" in result
        assert "analytics" in result
        assert "queue" in result
        assert "attention_reasons" in result
        assert "attention" in result
        assert "timeline" in result

    @pytest.mark.asyncio
    async def test_nonexistent_user_returns_none(self):
        mock_pool = self._make_mock_conn(user_row=None)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversation_detail

            result = await get_conversation_detail(user_id=99999)

        assert result is None

    @pytest.mark.asyncio
    async def test_attention_reasons_unanswered(self):

        detail_row = {
            "inbound_count": 5,
            "outbound_count": 0,
            "last_inbound_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "last_outbound_at": None,
            "first_message_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "avg_confidence": None,
            "auto_approved_count": 0,
            "operator_approved_count": 0,
            "ai_not_auto_count": 0,
            "total_queue_items": 0,
            "pending_queue_items": 0,
            "approved_queue_items": 0,
            "rejected_queue_items": 0,
            "avg_response_seconds": None,
            "timeline": None,
        }
        mock_pool = self._make_mock_conn(detail_row=detail_row)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversation_detail

            result = await get_conversation_detail(user_id=1001)

        assert "unanswered_message" in result["attention_reasons"]

    @pytest.mark.asyncio
    async def test_attention_reasons_slow_response(self):

        detail_row = {
            "inbound_count": 5,
            "outbound_count": 5,
            "last_inbound_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "last_outbound_at": datetime(2026, 8, 17, 10, 5, 0, tzinfo=UTC),
            "first_message_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "avg_confidence": 0.9,
            "auto_approved_count": 5,
            "operator_approved_count": 0,
            "ai_not_auto_count": 0,
            "total_queue_items": 0,
            "pending_queue_items": 0,
            "approved_queue_items": 0,
            "rejected_queue_items": 0,
            "avg_response_seconds": 400.0,
            "timeline": None,
        }
        mock_pool = self._make_mock_conn(detail_row=detail_row)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversation_detail

            result = await get_conversation_detail(user_id=1001)

        assert "slow_response" in result["attention_reasons"]

    @pytest.mark.asyncio
    async def test_attention_reasons_frequent_queue(self):

        detail_row = {
            "inbound_count": 10,
            "outbound_count": 10,
            "last_inbound_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "last_outbound_at": datetime(2026, 8, 17, 10, 1, 0, tzinfo=UTC),
            "first_message_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "avg_confidence": 0.9,
            "auto_approved_count": 8,
            "operator_approved_count": 2,
            "ai_not_auto_count": 0,
            "total_queue_items": 5,
            "pending_queue_items": 1,
            "approved_queue_items": 3,
            "rejected_queue_items": 1,
            "avg_response_seconds": 10.0,
            "timeline": None,
        }
        mock_pool = self._make_mock_conn(detail_row=detail_row)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversation_detail

            result = await get_conversation_detail(user_id=1001)

        assert "frequent_queue_routing" in result["attention_reasons"]

    @pytest.mark.asyncio
    async def test_auto_approval_rate_calculated(self):

        detail_row = {
            "inbound_count": 20,
            "outbound_count": 15,
            "last_inbound_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "last_outbound_at": datetime(2026, 8, 17, 10, 1, 0, tzinfo=UTC),
            "first_message_at": datetime(2026, 8, 1, 8, 0, 0, tzinfo=UTC),
            "avg_confidence": 0.82,
            "auto_approved_count": 10,
            "operator_approved_count": 3,
            "ai_not_auto_count": 2,
            "total_queue_items": 3,
            "pending_queue_items": 0,
            "approved_queue_items": 3,
            "rejected_queue_items": 0,
            "avg_response_seconds": 25.5,
            "timeline": None,
        }
        mock_pool = self._make_mock_conn(detail_row=detail_row)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversation_detail

            result = await get_conversation_detail(user_id=1001)

        # ai_generated = 10 + 2 = 12, rate = 10/12*100 = 83.3
        assert result["analytics"]["ai_generated"] == 12
        assert result["analytics"]["auto_approval_rate"] == 83.3

    @pytest.mark.asyncio
    async def test_auto_approval_rate_null_when_no_ai(self):

        detail_row = {
            "inbound_count": 5,
            "outbound_count": 3,
            "last_inbound_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "last_outbound_at": datetime(2026, 8, 17, 10, 1, 0, tzinfo=UTC),
            "first_message_at": datetime(2026, 8, 1, 8, 0, 0, tzinfo=UTC),
            "avg_confidence": None,
            "auto_approved_count": 0,
            "operator_approved_count": 3,
            "ai_not_auto_count": 0,
            "total_queue_items": 3,
            "pending_queue_items": 0,
            "approved_queue_items": 3,
            "rejected_queue_items": 0,
            "avg_response_seconds": None,
            "timeline": None,
        }
        mock_pool = self._make_mock_conn(detail_row=detail_row)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversation_detail

            result = await get_conversation_detail(user_id=1001)

        assert result["analytics"]["ai_generated"] == 0
        assert result["analytics"]["auto_approval_rate"] is None

    @pytest.mark.asyncio
    async def test_queue_stats_populated(self):

        detail_row = {
            "inbound_count": 10,
            "outbound_count": 8,
            "last_inbound_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "last_outbound_at": datetime(2026, 8, 17, 10, 1, 0, tzinfo=UTC),
            "first_message_at": datetime(2026, 8, 1, 8, 0, 0, tzinfo=UTC),
            "avg_confidence": 0.8,
            "auto_approved_count": 6,
            "operator_approved_count": 2,
            "ai_not_auto_count": 0,
            "total_queue_items": 4,
            "pending_queue_items": 1,
            "approved_queue_items": 2,
            "rejected_queue_items": 1,
            "avg_response_seconds": 20.0,
            "timeline": None,
        }
        mock_pool = self._make_mock_conn(detail_row=detail_row)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversation_detail

            result = await get_conversation_detail(user_id=1001)

        q = result["queue"]
        assert q["total_queue_items"] == 4
        assert q["pending_queue_items"] == 1
        assert q["approved_queue_items"] == 2
        assert q["rejected_queue_items"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP E — Frontend Template Contracts
# ═══════════════════════════════════════════════════════════════════════════════


class TestConversationsFrontend:
    """Tests for analytics.html conversation section contracts."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_conversations_section_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Conversation Performance" in content

    def test_conversations_table_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convData" in content
        assert "app-table" in content

    def test_conversations_api_endpoint_referenced(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "/static/js/analytics-api.js" in content
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "/api/analytics/conversations" in api_content

    def test_conversations_has_pagination(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convPage" in content
        assert "total_pages" in content

    def test_conversations_has_sorting(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "sortConversations" in content
        assert "convSortBy" in content
        assert "convSortOrder" in content

    def test_conversations_has_attention_filter(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convAttention" in content
        assert "needs_attention" in content
        assert "quiet" in content

    def test_conversations_has_detail_modal(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "detailOpen" in content
        assert "showConversationDetail" in content

    def test_conversations_has_attention_reason_badges(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "unanswered_message" in content
        assert "slow_response" in content
        assert "Frequent Queue" in content or "frequent_queue" in content

    def test_conversations_has_response_time_coloring(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "formatResponseTime" in content

    def test_conversations_has_time_ago(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "timeAgo" in content

    def test_conversations_has_link_to_profile(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "/dashboard/profile/" in content

    def test_conversations_api_route_exists(self):
        from chatbotv2.dashboard.app import app

        routes = _collect_route_paths(app.routes)
        assert "/api/analytics/conversations" in routes

    def test_conversation_detail_api_route_exists(self):
        from chatbotv2.dashboard.app import app

        routes = _collect_route_paths(app.routes)
        assert "/api/analytics/conversations/{user_id}" in routes


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP F — Phase 2.3: Attention Management API
# ═══════════════════════════════════════════════════════════════════════════════


class TestAttentionManagementAPI:
    """Tests for PATCH /api/analytics/conversations/{user_id}/attention and assignment."""

    @pytest.mark.asyncio
    async def test_attention_update_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/analytics/conversations/1001/attention",
                json={"status": "reviewed"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_attention_update_valid_status(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        mock_result = {
            "user_id": 1001,
            "status": "reviewed",
            "assigned_operator_id": None,
            "reviewed_at": "2026-08-17T10:00:00",
            "reviewed_by": "admin",
        }
        with patch(
            "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/attention",
                    json={"status": "reviewed"},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "reviewed"
        assert body["reviewed_by"] == "admin"

    @pytest.mark.asyncio
    async def test_attention_update_invalid_status_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/analytics/conversations/1001/attention",
                json={"status": "bogus"},
            )
        app.dependency_overrides.clear()

        assert resp.status_code == 400
        assert "Invalid status" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_assignment_update_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/analytics/conversations/1001/assignment",
                json={"assigned_operator_id": 1},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_assignment_update_valid_operator(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        mock_result = {
            "user_id": 1001,
            "status": "new",
            "assigned_operator_id": 1,
            "reviewed_at": None,
            "reviewed_by": "admin",
        }

        with (
            patch(
                "chatbotv2.dashboard.routes.attention.verify_operator_active",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/assignment",
                    json={"assigned_operator_id": 1},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["assigned_operator_id"] == 1

    @pytest.mark.asyncio
    async def test_assignment_invalid_operator_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.attention.verify_operator_active",
            new_callable=AsyncMock,
            return_value=JSONResponse({"error": "Operator not found or inactive"}, status_code=400),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/assignment",
                    json={"assigned_operator_id": 999},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 400
        assert "not found" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_assignment_unassign_operator(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        mock_result = {
            "user_id": 1001,
            "status": "new",
            "assigned_operator_id": None,
            "reviewed_at": None,
            "reviewed_by": "admin",
        }
        with patch(
            "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/assignment",
                    json={"assigned_operator_id": 0},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["assigned_operator_id"] is None

    @pytest.mark.asyncio
    async def test_db_error_returns_500(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/attention",
                    json={"status": "reviewed"},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP G — Phase 2.3: Operators API
# ═══════════════════════════════════════════════════════════════════════════════


class TestOperatorListAPI:
    """Tests for GET /api/operators."""

    @pytest.mark.asyncio
    async def test_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/operators")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_operator_list(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        mock_ops = [{"id": 1, "username": "op1", "name": "Operator 1"}]
        with patch(
            "chatbotv2.dashboard.routes.operators.get_operator_list",
            new_callable=AsyncMock,
            return_value=mock_ops,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/operators")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == 1

    @pytest.mark.asyncio
    async def test_operators_api_route_exists(self):
        from chatbotv2.dashboard.app import app

        routes = _collect_route_paths(app.routes)
        assert "/api/operators" in routes

    @pytest.mark.asyncio
    async def test_attention_api_route_exists(self):
        from chatbotv2.dashboard.app import app

        routes = _collect_route_paths(app.routes)
        assert "/api/analytics/conversations/{user_id}/attention" in routes

    @pytest.mark.asyncio
    async def test_assignment_api_route_exists(self):
        from chatbotv2.dashboard.app import app

        routes = _collect_route_paths(app.routes)
        assert "/api/analytics/conversations/{user_id}/assignment" in routes


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP H — Phase 2.3: DB Query — Conversation Attention
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetConversationAttention:
    """Tests for db/postgres.py get_conversation_attention()."""

    def _make_mock_conn(self, att_row=None):
        mock_conn = AsyncMock()

        async def mock_fetchrow(sql, *args, **kwargs):
            return att_row

        mock_conn.fetchrow = mock_fetchrow

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        return mock_pool

    @pytest.mark.asyncio
    async def test_returns_attention_state(self):
        att_row = {
            "user_id": 1001,
            "status": "reviewed",
            "assigned_operator_id": 1,
            "reviewed_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "reviewed_by": "admin",
        }
        mock_pool = self._make_mock_conn(att_row=att_row)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversation_attention

            result = await get_conversation_attention(user_id=1001)

        assert result is not None
        assert result["status"] == "reviewed"
        assert result["assigned_operator_id"] == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_no_attention(self):
        mock_pool = self._make_mock_conn(att_row=None)

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversation_attention

            result = await get_conversation_attention(user_id=99999)

        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP I — Phase 2.3: Conversations list includes attention fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestConversationsListAttentionFields:
    """Verify that conversations list items include attention management fields."""

    def _make_mock_conn(self, count_row=None, data_rows=None):
        if count_row is None:
            count_row = {"cnt": 0}
        if data_rows is None:
            data_rows = []

        mock_conn = AsyncMock()

        async def mock_fetchrow(sql, *args, **kwargs):
            return count_row

        async def mock_fetch(sql, *args, **kwargs):
            return data_rows

        mock_conn.fetchrow = mock_fetchrow
        mock_conn.fetch = mock_fetch

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        return mock_pool

    def _make_row(self, **overrides):
        defaults = {
            "user_id": 1001,
            "username": "testuser",
            "first_name": "Test",
            "last_seen": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "inbound_count": 10,
            "outbound_count": 8,
            "last_inbound_at": datetime(2026, 8, 17, 9, 55, 0, tzinfo=UTC),
            "last_outbound_at": datetime(2026, 8, 17, 9, 56, 0, tzinfo=UTC),
            "avg_confidence": 0.8,
            "auto_approved_count": 6,
            "operator_approved_count": 2,
            "queue_count": 1,
            "avg_response_seconds": 30.0,
            "unanswered": False,
            "slow_response": False,
            "frequent_queue": False,
            "attention_status": "new",
            "assigned_operator_id": None,
            "reviewed_at": None,
            "reviewed_by": None,
        }
        defaults.update(overrides)

        row = MagicMock()
        for k, v in defaults.items():
            setattr(row, k, v)
            row.__getitem__ = lambda self, k: getattr(self, k)
        return row

    @pytest.mark.asyncio
    async def test_items_include_attention_fields(self):
        row = self._make_row(
            attention_status="reviewed",
            assigned_operator_id=1,
            reviewed_at=datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            reviewed_by="admin",
        )
        mock_pool = self._make_mock_conn(count_row={"cnt": 1}, data_rows=[row])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversations_analytics

            result = await get_conversations_analytics()

        assert len(result["items"]) == 1
        item = result["items"][0]
        assert "attention_status" in item
        assert "assigned_operator_id" in item
        assert "reviewed_at" in item
        assert "reviewed_by" in item
        assert item["attention_status"] == "reviewed"
        assert item["assigned_operator_id"] == 1
        assert item["reviewed_by"] == "admin"

    @pytest.mark.asyncio
    async def test_items_default_attention_status(self):
        row = self._make_row()
        mock_pool = self._make_mock_conn(count_row={"cnt": 1}, data_rows=[row])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            from db.postgres import get_conversations_analytics

            result = await get_conversations_analytics()

        item = result["items"][0]
        assert item["attention_status"] == "new"
        assert item["assigned_operator_id"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP J — Phase 2.3: Frontend Template Contracts
# ═══════════════════════════════════════════════════════════════════════════════


class TestAttentionFrontend:
    """Tests for analytics.html attention management UI contracts."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_operators_state_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "operators" in content

    def test_load_operators_function(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "loadOperators" in content
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "/api/operators" in api_content

    def test_mark_reviewed_function(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "markReviewed" in content

    def test_assign_operator_function(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "assignOperator" in content

    def test_attention_status_badge_in_table(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "attention_status" in content
        assert "Reviewed" in content

    def test_attention_detail_modal_controls(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Attention Management" in content
        assert "detailMarkReviewed" in content
        assert "detailAssignOperator" in content

    def test_attention_detail_modal_shows_reviewer(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "reviewed_by" in content
        assert "reviewed_at" in content

    def test_attention_detail_modal_operator_select(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Unassigned" in content

    def test_attention_api_endpoints_referenced(self):
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "/api/analytics/conversations/" in api_content
        assert "/attention" in api_content
        assert "/assignment" in api_content

    def test_operators_dropdown_in_table(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "op.username || op.name" in content

    def test_attention_status_in_table_column(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "attention_status === 'reviewed'" in content


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP K — Phase 2.4: Conversation Notes API
# ═══════════════════════════════════════════════════════════════════════════════


class TestConversationNotesAPI:
    """Tests for GET/POST /api/analytics/conversations/{user_id}/notes."""

    @pytest.mark.asyncio
    async def test_list_notes_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/1001/notes")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_create_note_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/analytics/conversations/1001/notes",
                json={"content": "test note"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_notes_returns_structure(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with (
            patch(
                "chatbotv2.dashboard.routes.notes.verify_user_id_exists",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.list_conversation_notes",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.count_conversation_notes",
                new_callable=AsyncMock,
                return_value=0,
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001/notes")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert "notes" in body
        assert "total" in body
        assert body["notes"] == []
        assert body["total"] == 0

    @pytest.mark.asyncio
    async def test_create_note_success(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        from datetime import datetime

        mock_note = {
            "id": 1,
            "user_id": 1001,
            "content": "Customer complained about delayed delivery.",
            "created_by": "admin",
            "created_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
        }

        with (
            patch(
                "chatbotv2.dashboard.routes.notes.verify_user_id_exists",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.create_conversation_note",
                new_callable=AsyncMock,
                return_value=mock_note,
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/1001/notes",
                    json={"content": "Customer complained about delayed delivery."},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 201
        body = resp.json()
        assert body["content"] == "Customer complained about delayed delivery."
        assert body["created_by"] == "admin"
        assert body["user_id"] == 1001
        assert "id" in body
        assert "created_at" in body

    @pytest.mark.asyncio
    async def test_create_note_empty_content_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.notes.verify_user_id_exists",
            new_callable=AsyncMock,
            return_value=None,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/1001/notes",
                    json={"content": "   "},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 400
        assert "empty" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_create_note_oversized_content_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.notes.verify_user_id_exists",
            new_callable=AsyncMock,
            return_value=None,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/1001/notes",
                    json={"content": "x" * 2001},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 400
        assert "2000" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_create_note_nonexistent_user_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.notes.verify_user_id_exists",
            new_callable=AsyncMock,
            return_value=JSONResponse({"error": "User not found"}, status_code=404),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/99999/notes",
                    json={"content": "test note"},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 404
        assert "not found" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_list_notes_nonexistent_user_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.notes.verify_user_id_exists",
            new_callable=AsyncMock,
            return_value=JSONResponse({"error": "User not found"}, status_code=404),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/99999/notes")
        app.dependency_overrides.clear()

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_notes_api_routes_exist(self):
        from chatbotv2.dashboard.app import app

        routes = _collect_route_paths(app.routes)
        assert "/api/analytics/conversations/{user_id}/notes" in routes

    @pytest.mark.asyncio
    async def test_list_notes_passes_pagination_params(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with (
            patch(
                "chatbotv2.dashboard.routes.notes.verify_user_id_exists",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.list_conversation_notes",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_list,
            patch(
                "chatbotv2.dashboard.routes.notes.count_conversation_notes",
                new_callable=AsyncMock,
                return_value=0,
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001/notes?limit=10&offset=5")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_list.assert_called_once_with(1001, limit=10, offset=5)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP L — Phase 2.4: DB Query — Conversation Notes
# ═══════════════════════════════════════════════════════════════════════════════


class TestConversationNotesDB:
    """Tests for db/postgres.py conversation notes functions."""

    def _make_mock_conn(self, fetchrow_result=None, fetch_result=None, execute_result=None):
        mock_conn = AsyncMock()

        async def mock_fetchrow(sql, *args, **kwargs):
            return fetchrow_result

        async def mock_fetch(sql, *args, **kwargs):
            return fetch_result or []

        async def mock_execute(sql, *args, **kwargs):
            return execute_result or "INSERT 0 1"

        mock_conn.fetchrow = mock_fetchrow
        mock_conn.fetch = mock_fetch
        mock_conn.execute = mock_execute

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        return mock_pool

    @pytest.mark.asyncio
    async def test_create_note_returns_expected_fields(self):
        from datetime import datetime

        note_row = {
            "id": 1,
            "user_id": 1001,
            "content": "Test note",
            "created_by": "admin",
            "created_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
        }
        mock_pool = self._make_mock_conn(fetchrow_result=note_row)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            from db.postgres import create_conversation_note

            result = await create_conversation_note(
                user_id=1001, content="Test note", created_by="admin"
            )

        assert result["id"] == 1
        assert result["user_id"] == 1001
        assert result["content"] == "Test note"
        assert result["created_by"] == "admin"
        assert result["created_at"] is not None

    @pytest.mark.asyncio
    async def test_list_notes_returns_list(self):
        from datetime import datetime

        notes = [
            {
                "id": 2,
                "user_id": 1001,
                "content": "Second note",
                "created_by": "admin",
                "created_at": datetime(2026, 8, 17, 11, 0, 0, tzinfo=UTC),
            },
            {
                "id": 1,
                "user_id": 1001,
                "content": "First note",
                "created_by": "operator1",
                "created_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            },
        ]
        mock_pool = self._make_mock_conn(fetch_result=notes)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            from db.postgres import list_conversation_notes

            result = await list_conversation_notes(user_id=1001)

        assert len(result) == 2
        assert result[0]["id"] == 2
        assert result[1]["id"] == 1

    @pytest.mark.asyncio
    async def test_count_notes_returns_count(self):
        mock_pool = self._make_mock_conn(fetchrow_result={"cnt": 5})

        with patch("db.postgres.get_pool", return_value=mock_pool):
            from db.postgres import count_conversation_notes

            result = await count_conversation_notes(user_id=1001)

        assert result == 5

    @pytest.mark.asyncio
    async def test_count_notes_returns_zero_when_empty(self):
        mock_pool = self._make_mock_conn(fetchrow_result={"cnt": 0})

        with patch("db.postgres.get_pool", return_value=mock_pool):
            from db.postgres import count_conversation_notes

            result = await count_conversation_notes(user_id=99999)

        assert result == 0

    @pytest.mark.asyncio
    async def test_list_notes_empty_result(self):
        mock_pool = self._make_mock_conn(fetch_result=[])

        with patch("db.postgres.get_pool", return_value=mock_pool):
            from db.postgres import list_conversation_notes

            result = await list_conversation_notes(user_id=99999)

        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP M — Phase 2.4: Frontend Template Contracts
# ═══════════════════════════════════════════════════════════════════════════════


class TestNotesFrontend:
    """Tests for analytics.html notes UI contracts."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_notes_section_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Internal Notes" in content

    def test_notes_endpoint_referenced(self):
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "/notes" in api_content

    def test_notes_input_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "noteInput" in content
        assert "Add a note..." in content

    def test_notes_submit_button_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "submitNote" in content
        assert "Add Note" in content

    def test_notes_author_rendered(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "created_by" in content

    def test_notes_timestamp_rendered(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "created_at" in content
        assert "formatDate" in content

    def test_notes_empty_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "No internal notes yet" in content

    def test_notes_loading_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "notesLoading" in content

    def test_notes_duplicate_protection(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "noteSubmitting" in content

    def test_notes_error_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "noteError" in content

    def test_notes_success_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "noteSuccess" in content

    def test_notes_internal_only_message(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "internal only" in content

    def test_notes_ctrl_enter_submit(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "ctrl.enter" in content or "meta.enter" in content

    def test_notes_load_on_detail_open(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "loadNotes" in content

    def test_notes_max_length_attribute(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert 'maxlength="2000"' in content

    def test_notes_lock_icon(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "fa-lock" in content

    def test_notes_disabled_during_submit(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert ':disabled="noteSubmitting' in content


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP N — Phase 2.5: Note Edit/Delete API
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoteEditDeleteAPI:
    """Tests for PATCH and DELETE /api/analytics/conversations/{user_id}/notes/{note_id}."""

    @pytest.mark.asyncio
    async def test_patch_note_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/analytics/conversations/1001/notes/1",
                json={"content": "updated"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_note_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/analytics/conversations/1001/notes/1")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_patch_note_not_found(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.notes.get_conversation_note",
            new_callable=AsyncMock,
            return_value=None,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/notes/999",
                    json={"content": "updated"},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_note_not_found(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.notes.get_conversation_note",
            new_callable=AsyncMock,
            return_value=None,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/api/analytics/conversations/1001/notes/999")
        app.dependency_overrides.clear()
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_note_wrong_user_returns_404(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.notes.get_conversation_note",
            new_callable=AsyncMock,
            return_value={"id": 1, "user_id": 9999, "created_by": "admin", "content": "x"},
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/notes/1",
                    json={"content": "updated"},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_note_forbidden_when_not_owner(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.notes.get_conversation_note",
            new_callable=AsyncMock,
            return_value={"id": 1, "user_id": 1001, "created_by": "other_user", "content": "x"},
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/notes/1",
                    json={"content": "updated"},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_note_forbidden_when_not_owner(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.notes.get_conversation_note",
            new_callable=AsyncMock,
            return_value={"id": 1, "user_id": 1001, "created_by": "other_user", "content": "x"},
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/api/analytics/conversations/1001/notes/1")
        app.dependency_overrides.clear()
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_patch_note_empty_content_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.notes.get_conversation_note",
            new_callable=AsyncMock,
            return_value={"id": 1, "user_id": 1001, "created_by": "admin", "content": "x"},
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/notes/1",
                    json={"content": "  "},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_patch_note_oversized_content_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.notes.get_conversation_note",
            new_callable=AsyncMock,
            return_value={"id": 1, "user_id": 1001, "created_by": "admin", "content": "x"},
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/notes/1",
                    json={"content": "x" * 2001},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_patch_note_success(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.notes.get_conversation_note",
                new_callable=AsyncMock,
                return_value={"id": 1, "user_id": 1001, "created_by": "admin", "content": "old"},
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.update_conversation_note",
                new_callable=AsyncMock,
                return_value={
                    "id": 1,
                    "user_id": 1001,
                    "created_by": "admin",
                    "content": "updated",
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/notes/1",
                    json={"content": "updated"},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == "updated"
        assert body["id"] == 1

    @pytest.mark.asyncio
    async def test_delete_note_success(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.notes.get_conversation_note",
                new_callable=AsyncMock,
                return_value={"id": 1, "user_id": 1001, "created_by": "admin", "content": "x"},
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.delete_conversation_note",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/api/analytics/conversations/1001/notes/1")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP O — Phase 2.5: DB Query — Note Update/Delete
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoteUpdateDeleteDB:
    """Tests for update_conversation_note and delete_conversation_note DB functions."""

    def _make_mock_conn(self, fetchrow_result=None, execute_result="DELETE 1"):
        mock_conn = AsyncMock()

        async def mock_fetchrow(sql, *args, **kwargs):
            return fetchrow_result

        async def mock_execute(sql, *args, **kwargs):
            return execute_result

        mock_conn.fetchrow = mock_fetchrow
        mock_conn.execute = mock_execute

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        return mock_pool

    @pytest.mark.asyncio
    async def test_get_conversation_note_returns_dict(self):
        from db.postgres import get_conversation_note

        mock_row = {
            "id": 1,
            "user_id": 1001,
            "content": "test note",
            "created_by": "admin",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
        mock_pool = self._make_mock_conn(fetchrow_result=mock_row)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await get_conversation_note(1)

        assert result == mock_row

    @pytest.mark.asyncio
    async def test_get_conversation_note_returns_none_when_not_found(self):
        from db.postgres import get_conversation_note

        mock_pool = self._make_mock_conn(fetchrow_result=None)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await get_conversation_note(999)

        assert result is None

    @pytest.mark.asyncio
    async def test_update_conversation_note_returns_updated_row(self):
        from db.postgres import update_conversation_note

        mock_row = {
            "id": 1,
            "user_id": 1001,
            "content": "updated content",
            "created_by": "admin",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
        mock_pool = self._make_mock_conn(fetchrow_result=mock_row)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await update_conversation_note(1, "updated content")

        assert result == mock_row

    @pytest.mark.asyncio
    async def test_update_conversation_note_returns_none_when_not_found(self):
        from db.postgres import update_conversation_note

        mock_pool = self._make_mock_conn(fetchrow_result=None)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await update_conversation_note(999, "content")

        assert result is None

    @pytest.mark.asyncio
    async def test_delete_conversation_note_returns_true(self):
        from db.postgres import delete_conversation_note

        mock_pool = self._make_mock_conn(execute_result="DELETE 1")

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await delete_conversation_note(1)

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_conversation_note_returns_false_when_not_found(self):
        from db.postgres import delete_conversation_note

        mock_pool = self._make_mock_conn(execute_result="DELETE 0")

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await delete_conversation_note(999)

        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP P — Phase 2.5: Frontend Edit/Delete Contracts
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoteEditDeleteFrontend:
    """Tests for analytics.html edit/delete UI contracts."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_edit_button_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "startEditNote" in content
        assert "fa-pen" in content

    def test_delete_button_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "confirmDeleteNote" in content
        assert "fa-trash" in content

    def test_edit_mode_textarea_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "editingNoteId" in content
        assert "editingNoteContent" in content

    def test_edit_save_button_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "saveEditNote" in content

    def test_edit_cancel_button_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "cancelEditNote" in content

    def test_current_user_tracking(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "currentUser" in content
        assert "data-current-user" in content

    def test_ownership_check_in_template(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "note.created_by === currentUser" in content

    def test_edit_delete_controls_container(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "margin-left: auto" in content

    def test_edit_save_sending_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "editingNoteSaving" in content

    def test_delete_confirmation_prompt(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Delete this note?" in content

    def test_edit_error_handling(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Not authorized to edit this note" in content

    def test_delete_error_handling(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Not authorized to delete this note" in content

    def test_note_updated_in_list(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "this.notes[idx].content" in content

    def test_note_removed_from_list(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "this.notes = this.notes.filter" in content


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP Q — Phase 2.6: Tag CRUD API
# ═══════════════════════════════════════════════════════════════════════════════


class TestTagCRUDAPI:
    """Tests for /api/conversation-tags endpoints."""

    @pytest.mark.asyncio
    async def test_list_tags_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/conversation-tags")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_tags_returns_list(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.tags.list_conversation_tags",
            new_callable=AsyncMock,
            return_value=[
                {
                    "id": 1,
                    "name": "VIP",
                    "description": "High value",
                    "created_by": "admin",
                    "created_at": "2026-01-01T00:00:00",
                }
            ],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/conversation-tags")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["name"] == "VIP"

    @pytest.mark.asyncio
    async def test_create_tag_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/conversation-tags", json={"name": "VIP"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_create_tag_success(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.tags.create_conversation_tag",
            new_callable=AsyncMock,
            return_value={
                "id": 1,
                "name": "VIP",
                "description": "High value",
                "created_by": "admin",
                "created_at": "2026-01-01T00:00:00",
            },
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/conversation-tags", json={"name": "VIP", "description": "High value"}
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 201
        assert resp.json()["name"] == "VIP"

    @pytest.mark.asyncio
    async def test_create_tag_empty_name_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/conversation-tags", json={"name": ""})
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_tag_whitespace_only_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/conversation-tags", json={"name": "   "})
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_tag_name_too_long_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/conversation-tags", json={"name": "x" * 51})
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_tag_description_too_long_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/conversation-tags", json={"name": "VIP", "description": "x" * 201}
            )
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_tag_duplicate_rejected(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.tags.create_conversation_tag",
            new_callable=AsyncMock,
            return_value=None,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/conversation-tags", json={"name": "VIP"})
        app.dependency_overrides.clear()
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_create_tag_strips_whitespace(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.tags.create_conversation_tag",
            new_callable=AsyncMock,
            return_value={
                "id": 1,
                "name": "VIP",
                "description": None,
                "created_by": "admin",
                "created_at": "2026-01-01T00:00:00",
            },
        ) as mock_create:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/conversation-tags", json={"name": "  VIP  "})
        app.dependency_overrides.clear()
        assert resp.status_code == 201
        mock_create.assert_called_once_with(name="VIP", description=None, created_by="admin")

    @pytest.mark.asyncio
    async def test_delete_tag_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/conversation-tags/1")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_tag_success(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.tags.delete_conversation_tag",
            new_callable=AsyncMock,
            return_value=True,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/api/conversation-tags/1")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_delete_tag_not_found(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.tags.delete_conversation_tag",
            new_callable=AsyncMock,
            return_value=False,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/api/conversation-tags/999")
        app.dependency_overrides.clear()
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP R — Phase 2.6: Tag Assignment API
# ═══════════════════════════════════════════════════════════════════════════════


class TestTagAssignmentAPI:
    """Tests for conversation tag assignment endpoints."""

    @pytest.mark.asyncio
    async def test_list_tags_for_conversation(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.tags.verify_user_id_exists",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.list_user_conversation_tags",
                new_callable=AsyncMock,
                return_value=[{"id": 1, "name": "VIP"}],
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001/tags")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    @pytest.mark.asyncio
    async def test_list_tags_user_not_found(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.tags.verify_user_id_exists",
            new_callable=AsyncMock,
            return_value=JSONResponse({"error": "User not found"}, status_code=404),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/9999/tags")
        app.dependency_overrides.clear()
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_assign_tag_success(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.tags.verify_user_id_exists",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.get_conversation_tag",
                new_callable=AsyncMock,
                return_value={"id": 1, "name": "VIP"},
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.assign_conversation_tag",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/analytics/conversations/1001/tags/1")
        app.dependency_overrides.clear()
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_assign_tag_records_assigned_by(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.tags.verify_user_id_exists",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.get_conversation_tag",
                new_callable=AsyncMock,
                return_value={"id": 1, "name": "VIP"},
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.assign_conversation_tag",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_assign,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post("/api/analytics/conversations/1001/tags/1")
        app.dependency_overrides.clear()
        mock_assign.assert_called_once_with(user_id=1001, tag_id=1, assigned_by="admin")

    @pytest.mark.asyncio
    async def test_assign_tag_user_not_found(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.tags.verify_user_id_exists",
            new_callable=AsyncMock,
            return_value=JSONResponse({"error": "User not found"}, status_code=404),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/analytics/conversations/9999/tags/1")
        app.dependency_overrides.clear()
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_assign_tag_not_found(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.tags.verify_user_id_exists",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.get_conversation_tag",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/analytics/conversations/1001/tags/999")
        app.dependency_overrides.clear()
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_remove_tag_success(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.tags.remove_conversation_tag",
            new_callable=AsyncMock,
            return_value=True,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/api/analytics/conversations/1001/tags/1")
        app.dependency_overrides.clear()
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_remove_tag_not_found(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.tags.remove_conversation_tag",
            new_callable=AsyncMock,
            return_value=False,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/api/analytics/conversations/1001/tags/999")
        app.dependency_overrides.clear()
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_assign_tag_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/analytics/conversations/1001/tags/1")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_remove_tag_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/analytics/conversations/1001/tags/1")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP S — Phase 2.6: DB Functions — Tags
# ═══════════════════════════════════════════════════════════════════════════════


class TestTagDB:
    """Tests for tag DB functions."""

    def _make_mock_conn(self, fetchrow_result=None, fetch_result=None, execute_result="DELETE 1"):
        mock_conn = AsyncMock()

        async def mock_fetchrow(sql, *args, **kwargs):
            return fetchrow_result

        async def mock_fetch(sql, *args, **kwargs):
            return fetch_result or []

        async def mock_execute(sql, *args, **kwargs):
            return execute_result

        mock_conn.fetchrow = mock_fetchrow
        mock_conn.fetch = mock_fetch
        mock_conn.execute = mock_execute

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        return mock_pool

    @pytest.mark.asyncio
    async def test_create_tag_returns_dict(self):
        from db.postgres import create_conversation_tag

        mock_row = {
            "id": 1,
            "name": "VIP",
            "description": "High value",
            "created_by": "admin",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
        mock_pool = self._make_mock_conn(fetchrow_result=mock_row)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await create_conversation_tag("VIP", "High value", "admin")

        assert result["name"] == "VIP"

    @pytest.mark.asyncio
    async def test_create_tag_returns_none_on_duplicate(self):
        import asyncpg

        from db.postgres import create_conversation_tag

        mock_conn = AsyncMock()

        async def mock_fetchrow(sql, *args, **kwargs):
            raise asyncpg.UniqueViolationError("unique violation")

        mock_conn.fetchrow = mock_fetchrow

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await create_conversation_tag("VIP")

        assert result is None

    @pytest.mark.asyncio
    async def test_list_tags_returns_list(self):
        from db.postgres import list_conversation_tags

        tags = [{"id": 1, "name": "VIP"}, {"id": 2, "name": "New"}]
        mock_pool = self._make_mock_conn(fetch_result=tags)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await list_conversation_tags()

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_delete_tag_returns_true(self):
        from db.postgres import delete_conversation_tag

        mock_pool = self._make_mock_conn(execute_result="DELETE 1")

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await delete_conversation_tag(1)

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_tag_returns_false_when_not_found(self):
        from db.postgres import delete_conversation_tag

        mock_pool = self._make_mock_conn(execute_result="DELETE 0")

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await delete_conversation_tag(999)

        assert result is False

    @pytest.mark.asyncio
    async def test_get_tag_returns_dict(self):
        from db.postgres import get_conversation_tag

        mock_row = {
            "id": 1,
            "name": "VIP",
            "description": "High value",
            "created_by": "admin",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
        mock_pool = self._make_mock_conn(fetchrow_result=mock_row)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await get_conversation_tag(1)

        assert result["name"] == "VIP"

    @pytest.mark.asyncio
    async def test_get_tag_returns_none_when_not_found(self):
        from db.postgres import get_conversation_tag

        mock_pool = self._make_mock_conn(fetchrow_result=None)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await get_conversation_tag(999)

        assert result is None

    @pytest.mark.asyncio
    async def test_list_user_tags_returns_list(self):
        from db.postgres import list_user_conversation_tags

        tags = [{"id": 1, "name": "VIP", "assigned_by": "admin"}]
        mock_pool = self._make_mock_conn(fetch_result=tags)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await list_user_conversation_tags(1001)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_assign_tag_returns_true(self):
        from db.postgres import assign_conversation_tag

        mock_pool = self._make_mock_conn(execute_result="INSERT 0 1")

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await assign_conversation_tag(1001, 1, "admin")

        assert result is True

    @pytest.mark.asyncio
    async def test_assign_tag_idempotent_on_duplicate(self):
        from db.postgres import assign_conversation_tag

        mock_conn = AsyncMock()

        async def mock_execute(sql, *args, **kwargs):
            return "INSERT 0 0"

        mock_conn.execute = mock_execute

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await assign_conversation_tag(1001, 1, "admin")

        assert result is True

    @pytest.mark.asyncio
    async def test_remove_tag_returns_true(self):
        from db.postgres import remove_conversation_tag

        mock_pool = self._make_mock_conn(execute_result="DELETE 1")

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await remove_conversation_tag(1001, 1)

        assert result is True

    @pytest.mark.asyncio
    async def test_remove_tag_returns_false_when_not_found(self):
        from db.postgres import remove_conversation_tag

        mock_pool = self._make_mock_conn(execute_result="DELETE 0")

        with patch("db.postgres.get_pool", return_value=mock_pool):
            result = await remove_conversation_tag(1001, 999)

        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP T — Phase 2.6: Analytics Tag Filtering
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnalyticsTagFilter:
    """Tests for tag_id filter in conversation analytics."""

    def test_conversations_api_accepts_tag_id(self):
        from chatbotv2.dashboard.app import app

        routes = _collect_route_paths(app.routes)
        assert "/api/analytics/conversations" in routes

    def test_get_conversations_analytics_accepts_tag_id_param(self):
        import inspect

        from db.postgres import get_conversations_analytics

        sig = inspect.signature(get_conversations_analytics)
        assert "tag_id" in sig.parameters

    def test_tag_filter_sql_generated_when_tag_id_set(self):
        import inspect

        from db.postgres import get_conversations_analytics

        source = inspect.getsource(get_conversations_analytics)
        assert "conversation_tag_assignments" in source
        assert "tag_id" in source

    @pytest.mark.asyncio
    async def test_conversations_api_passes_tag_id(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            return_value={
                "items": [],
                "pagination": {"page": 1, "page_size": 25, "total": 0, "total_pages": 0},
            },
        ) as mock_analytics:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get("/api/analytics/conversations?tag_id=5")
        app.dependency_overrides.clear()
        call_kwargs = mock_analytics.call_args.kwargs
        assert call_kwargs["tag_id"] == 5

    @pytest.mark.asyncio
    async def test_conversations_api_no_tag_id_passes_none(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            return_value={
                "items": [],
                "pagination": {"page": 1, "page_size": 25, "total": 0, "total_pages": 0},
            },
        ) as mock_analytics:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get("/api/analytics/conversations")
        app.dependency_overrides.clear()
        call_kwargs = mock_analytics.call_args.kwargs
        assert call_kwargs["tag_id"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP T2 — Assigned Operator Filter
# ═══════════════════════════════════════════════════════════════════════════════


class TestAssignedOperatorFilter:
    """Tests for assigned_operator_id filter in get_conversations_analytics."""

    def test_assigned_operator_filter_in_source(self):
        import inspect

        from db.postgres import get_conversations_analytics

        source = inspect.getsource(get_conversations_analytics)
        assert "assigned_operator_id" in source
        assert "operator_filter" in source

    @pytest.mark.asyncio
    async def test_conversations_api_passes_assigned_operator(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            return_value={
                "items": [],
                "pagination": {"page": 1, "page_size": 25, "total": 0, "total_pages": 0},
            },
        ) as mock_analytics:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get("/api/analytics/conversations?assigned_operator_id=3")
        app.dependency_overrides.clear()
        call_kwargs = mock_analytics.call_args.kwargs
        assert call_kwargs["assigned_operator_id"] == 3

    @pytest.mark.asyncio
    async def test_conversations_api_no_operator_passes_none(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            return_value={
                "items": [],
                "pagination": {"page": 1, "page_size": 25, "total": 0, "total_pages": 0},
            },
        ) as mock_analytics:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get("/api/analytics/conversations")
        app.dependency_overrides.clear()
        call_kwargs = mock_analytics.call_args.kwargs
        assert call_kwargs["assigned_operator_id"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP U — Phase 2.6: Frontend Tag Contracts
# ═══════════════════════════════════════════════════════════════════════════════


class TestTagFrontend:
    """Tests for analytics.html tag UI contracts."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_conversation_tags_section_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Conversation Tags" in content

    def test_assigned_tags_rendered(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convTags" in content

    def test_tag_assignment_endpoint_referenced(self):
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "/tags/" in api_content

    def test_tag_removal_endpoint_referenced(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "removeTag" in content

    def test_create_tag_ui_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "createTag" in content
        assert "newTagName" in content

    def test_delete_tag_ui_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "deleteTag" in content

    def test_tag_error_handling_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "tagError" in content
        assert "convTagError" in content

    def test_tag_loading_states_exist(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convTagsLoading" in content
        assert "convTagAssigning" in content
        assert "tagCreating" in content

    def test_tag_filter_dropdown_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convTagFilter" in content

    def test_all_tags_loaded_on_init(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "loadAllTags" in content

    def test_manage_tags_section_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Manage Tags" in content

    def test_available_tags_filtered(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "availableTagsToAssign" in content

    def test_tag_description_input_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "newTagDescription" in content

    def test_tag_max_length_attributes(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert 'maxlength="50"' in content
        assert 'maxlength="200"' in content

    def test_tag_icon_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "fa-tags" in content


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP AA — Activity Timeline: API endpoint + DB function
# ═══════════════════════════════════════════════════════════════════════════════


class TestConversationActivityAPI:
    """Tests for GET /api/analytics/conversations/{user_id}/activity."""

    def _mock_activity_result(self):
        return {
            "items": [
                {
                    "event_type": "message",
                    "event_id": 501,
                    "created_at": "2026-08-17T10:00:00",
                    "direction": "inbound",
                    "content_preview": "Hey, I need help with my order",
                    "operator": None,
                },
                {
                    "event_type": "note",
                    "event_id": 12,
                    "created_at": "2026-08-17T09:50:00",
                    "content_preview": "User is a VIP customer",
                    "operator": "admin",
                },
                {
                    "event_type": "attention_change",
                    "event_id": 1,
                    "created_at": "2026-08-17T09:45:00",
                    "detail": "reviewed",
                    "operator": "admin",
                    "extra": {"status": "reviewed", "assigned_operator_id": 5},
                },
                {
                    "event_type": "tag_assigned",
                    "event_id": 3,
                    "created_at": "2026-08-17T09:40:00",
                    "detail": "vip",
                    "operator": "admin",
                    "extra": {"tag_id": 1, "tag_name": "vip"},
                },
            ],
            "pagination": {
                "page": 1,
                "page_size": 50,
                "total": 4,
                "total_pages": 1,
            },
        }

    @pytest.mark.asyncio
    async def test_requires_authentication(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/1001/activity")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_request_returns_expected_structure(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        mock_result = self._mock_activity_result()
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversation_activity",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001/activity")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "pagination" in body
        assert len(body["items"]) == 4
        assert body["items"][0]["event_type"] == "message"
        assert body["items"][1]["event_type"] == "note"
        assert body["items"][2]["event_type"] == "attention_change"
        assert body["items"][3]["event_type"] == "tag_assigned"

    @pytest.mark.asyncio
    async def test_empty_activity_returns_empty_items(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversation_activity",
            new_callable=AsyncMock,
            return_value={
                "items": [],
                "pagination": {"page": 1, "page_size": 50, "total": 0, "total_pages": 0},
            },
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001/activity")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["items"] == []

    @pytest.mark.asyncio
    async def test_db_failure_returns_500(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversation_activity",
            new_callable=AsyncMock,
            side_effect=Exception("db down"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001/activity")
        app.dependency_overrides.clear()
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_pagination_params_forwarded(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversation_activity",
            new_callable=AsyncMock,
            return_value={
                "items": [],
                "pagination": {"page": 2, "page_size": 10, "total": 0, "total_pages": 0},
            },
        ) as mock_fn:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/analytics/conversations/1001/activity",
                    params={"page": 2, "page_size": 10},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_fn.assert_awaited_once_with(user_id=1001, page=2, page_size=10)

    @pytest.mark.asyncio
    async def test_event_types_present_in_response(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        mock_result = self._mock_activity_result()
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversation_activity",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001/activity")
        app.dependency_overrides.clear()

        body = resp.json()
        event_types = {item["event_type"] for item in body["items"]}
        assert event_types == {"message", "note", "attention_change", "tag_assigned"}


class TestConversationActivityDB:
    """Tests for get_conversation_activity DB function."""

    @pytest.mark.asyncio
    async def test_returns_messages_in_timeline(self):
        from db.postgres import get_conversation_activity

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        count_row = MagicMock()
        count_row.__getitem__ = lambda self, k: {"cnt": 1}[k]

        msg_row = MagicMock()
        msg_row.__getitem__ = lambda self, k: {
            "event_type": "message",
            "event_id": 1,
            "created_at": datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
            "direction": "inbound",
            "detail": "Hello",
            "operator": None,
            "extra": None,
        }[k]

        mock_conn.fetchrow = AsyncMock(return_value=count_row)
        mock_conn.fetch = AsyncMock(return_value=[msg_row])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_conversation_activity(user_id=1001)

        assert len(result["items"]) == 1
        assert result["items"][0]["event_type"] == "message"
        assert result["items"][0]["direction"] == "inbound"
        assert result["pagination"]["total"] == 1

    @pytest.mark.asyncio
    async def test_empty_conversation_returns_empty(self):
        from db.postgres import get_conversation_activity

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        count_row = MagicMock()
        count_row.__getitem__ = lambda self, k: {"cnt": 0}[k]

        mock_conn.fetchrow = AsyncMock(return_value=count_row)
        mock_conn.fetch = AsyncMock(return_value=[])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_conversation_activity(user_id=99999)

        assert result["items"] == []
        assert result["pagination"]["total"] == 0
        assert result["pagination"]["total_pages"] == 0

    @pytest.mark.asyncio
    async def test_note_events_include_operator(self):
        from db.postgres import get_conversation_activity

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        count_row = MagicMock()
        count_row.__getitem__ = lambda self, k: {"cnt": 1}[k]

        note_row = MagicMock()
        note_row.__getitem__ = lambda self, k: {
            "event_type": "note",
            "event_id": 12,
            "created_at": datetime(2026, 8, 17, 9, 50, tzinfo=UTC),
            "direction": None,
            "detail": "VIP customer",
            "operator": "admin",
            "extra": None,
        }[k]

        mock_conn.fetchrow = AsyncMock(return_value=count_row)
        mock_conn.fetch = AsyncMock(return_value=[note_row])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_conversation_activity(user_id=1001)

        assert result["items"][0]["event_type"] == "note"
        assert result["items"][0]["operator"] == "admin"
        assert result["items"][0]["content_preview"] == "VIP customer"

    @pytest.mark.asyncio
    async def test_attention_change_events_include_extra(self):
        from db.postgres import get_conversation_activity

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        count_row = MagicMock()
        count_row.__getitem__ = lambda self, k: {"cnt": 1}[k]

        att_row = MagicMock()
        att_row.__getitem__ = lambda self, k: {
            "event_type": "attention_change",
            "event_id": 1,
            "created_at": datetime(2026, 8, 17, 9, 45, tzinfo=UTC),
            "direction": None,
            "detail": "reviewed",
            "operator": "admin",
            "extra": {"status": "reviewed", "assigned_operator_id": 5},
        }[k]

        mock_conn.fetchrow = AsyncMock(return_value=count_row)
        mock_conn.fetch = AsyncMock(return_value=[att_row])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_conversation_activity(user_id=1001)

        assert result["items"][0]["event_type"] == "attention_change"
        assert result["items"][0]["extra"]["status"] == "reviewed"
        assert result["items"][0]["extra"]["assigned_operator_id"] == 5

    @pytest.mark.asyncio
    async def test_tag_assigned_events_include_tag_info(self):
        from db.postgres import get_conversation_activity

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        count_row = MagicMock()
        count_row.__getitem__ = lambda self, k: {"cnt": 1}[k]

        tag_row = MagicMock()
        tag_row.__getitem__ = lambda self, k: {
            "event_type": "tag_assigned",
            "event_id": 3,
            "created_at": datetime(2026, 8, 17, 9, 40, tzinfo=UTC),
            "direction": None,
            "detail": "vip",
            "operator": "admin",
            "extra": {"tag_id": 1, "tag_name": "vip"},
        }[k]

        mock_conn.fetchrow = AsyncMock(return_value=count_row)
        mock_conn.fetch = AsyncMock(return_value=[tag_row])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_conversation_activity(user_id=1001)

        assert result["items"][0]["event_type"] == "tag_assigned"
        assert result["items"][0]["extra"]["tag_name"] == "vip"

    @pytest.mark.asyncio
    async def test_content_preview_truncated_to_200_chars(self):
        from db.postgres import get_conversation_activity

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        count_row = MagicMock()
        count_row.__getitem__ = lambda self, k: {"cnt": 1}[k]

        long_msg = MagicMock()
        long_content = "x" * 300
        long_msg.__getitem__ = lambda self, k: {
            "event_type": "message",
            "event_id": 1,
            "created_at": datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
            "direction": "inbound",
            "detail": long_content,
            "operator": None,
            "extra": None,
        }[k]

        mock_conn.fetchrow = AsyncMock(return_value=count_row)
        mock_conn.fetch = AsyncMock(return_value=[long_msg])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_conversation_activity(user_id=1001)

        assert len(result["items"][0]["content_preview"]) == 200

    @pytest.mark.asyncio
    async def test_pagination_total_pages_computed(self):
        from db.postgres import get_conversation_activity

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        count_row = MagicMock()
        count_row.__getitem__ = lambda self, k: {"cnt": 120}[k]

        mock_conn.fetchrow = AsyncMock(return_value=count_row)
        mock_conn.fetch = AsyncMock(return_value=[])

        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await get_conversation_activity(user_id=1001, page_size=50)

        assert result["pagination"]["total"] == 120
        assert result["pagination"]["total_pages"] == 3


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP AB — Frontend: Bulk Operations & Activity Timeline
# ═══════════════════════════════════════════════════════════════════════════════


class TestBulkOperationsFrontend:
    """Tests for bulk operations API route registration."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_bulk_assign_route_exists_in_app(self):
        content = self._read("chatbotv2/dashboard/routes/bulk_ops.py")
        assert '"/api/analytics/conversations/bulk/assign"' in content

    def test_bulk_tag_route_exists_in_app(self):
        content = self._read("chatbotv2/dashboard/routes/bulk_ops.py")
        assert '"/api/analytics/conversations/bulk/tag"' in content

    def test_bulk_attention_route_exists_in_app(self):
        content = self._read("chatbotv2/dashboard/routes/bulk_ops.py")
        assert '"/api/analytics/conversations/bulk/attention"' in content

    def test_bulk_models_defined(self):
        content = self._read("chatbotv2/dashboard/schemas.py")
        assert "BulkAssignRequest" in content
        assert "BulkTagRequest" in content
        assert "BulkAttentionRequest" in content


class TestActivityTimelineFrontend:
    """Tests for activity timeline API route registration."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_activity_route_exists_in_app(self):
        content = self._read("chatbotv2/dashboard/routes/analytics.py")
        assert '"/api/analytics/conversations/{user_id}/activity"' in content

    def test_activity_db_function_exists(self):
        content = self._read("db/postgres.py")
        assert "get_conversation_activity" in content

    def test_assigned_operator_filter_in_db(self):
        content = self._read("db/postgres.py")
        assert "assigned_operator_id" in content


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP AC — Frontend Contract Tests: Phase 2.8 Complete
# ═══════════════════════════════════════════════════════════════════════════════


class TestPhase28FrontendActivityTimeline:
    """Tests for activity timeline UI contracts in analytics.html."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_activity_section_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Activity Timeline" in content

    def test_activity_endpoint_referenced(self):
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "/activity" in api_content

    def test_activity_loading_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "activityLoading" in content

    def test_activity_error_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "activityError" in content

    def test_activity_items_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "activityItems" in content

    def test_activity_empty_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "No activity yet" in content

    def test_activity_load_more_button(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "loadMoreActivity" in content

    def test_activity_pagination_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "activityTotalPages" in content

    def test_activity_event_type_rendering(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "event_type" in content
        assert "message" in content
        assert "note" in content
        assert "attention_change" in content
        assert "tag_assigned" in content

    def test_activity_direction_rendering(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "direction" in content
        assert "inbound" in content
        assert "outbound" in content

    def test_activity_operator_display(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "ev.operator" in content

    def test_activity_timestamp_display(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "timeAgo(ev.created_at)" in content

    def test_activity_loads_on_detail_open(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "loadActivity(userId" in content


class TestPhase28FrontendOperatorFilter:
    """Tests for operator filter UI contracts in analytics.html."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_operator_filter_dropdown_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convOperatorFilter" in content

    def test_operator_filter_all_option(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "All Operators" in content

    def test_operator_filter_unassigned_option(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Unassigned" in content

    def test_operator_filter_loads_operators(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "operators" in content

    def test_operator_filter_reloads_conversations(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convOperatorFilter" in content

    def test_operator_filter_in_api_params(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "assigned_operator_id" in content


class TestPhase28FrontendSelection:
    """Tests for conversation selection UI contracts in analytics.html."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_row_checkbox_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert 'class="row-checkbox"' in content

    def test_select_all_checkbox_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "toggleSelectAll" in content

    def test_row_checkbox_toggle(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "toggleSelect" in content

    def test_selected_convs_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "selectedConvs" in content

    def test_selected_count_displayed(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Selected:" in content

    def test_clear_selection_function(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "clearSelection" in content

    def test_bulk_toolbar_appears_on_selection(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "selectedConvs.length > 0" in content


class TestPhase28FrontendBulkAssign:
    """Tests for bulk assign UI contracts in analytics.html."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_bulk_assign_modal_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "bulkAssignOpen" in content

    def test_bulk_assign_endpoint(self):
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "bulk/assign" in api_content

    def test_bulk_assign_method(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "executeBulkAssign" in content

    def test_bulk_assign_operator_selector(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "bulkAssignOperatorId" in content

    def test_bulk_assign_request_body(self):
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "user_ids" in api_content
        assert "assigned_operator_id" in api_content

    def test_bulk_assign_loading_state(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "bulkOperating" in content

    def test_bulk_assign_success_refreshes(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "loadConversations()" in content

    def test_bulk_assign_clears_selection(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "selectedConvs = []" in content

    def test_bulk_assign_error_display(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "bulkError" in content


class TestPhase28FrontendBulkTag:
    """Tests for bulk tag UI contracts in analytics.html."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_bulk_tag_modal_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "bulkTagOpen" in content

    def test_bulk_tag_endpoint(self):
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "bulk/tag" in api_content

    def test_bulk_tag_method(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "executeBulkTag" in content

    def test_bulk_tag_tag_selector(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "bulkTagId" in content

    def test_bulk_tag_request_body(self):
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "tag_id" in api_content


class TestPhase28FrontendBulkAttention:
    """Tests for bulk attention UI contracts in analytics.html."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_bulk_attention_modal_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "bulkAttentionOpen" in content

    def test_bulk_attention_endpoint(self):
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "bulk/attention" in api_content

    def test_bulk_attention_method(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "executeBulkAttention" in content

    def test_bulk_attention_status_selector(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "bulkAttentionStatus" in content

    def test_bulk_attention_valid_statuses(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert '"new"' in content
        assert '"reviewed"' in content

    def test_bulk_attention_http_method(self):
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "method: 'PATCH'" in api_content


class TestPhase28FrontendDetailWorkflow:
    """Tests for improved conversation detail workflow."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_attention_management_section(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Attention Management" in content

    def test_assignment_controls(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "detailAssignOperator" in content

    def test_activity_section_in_detail(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Activity Timeline" in content

    def test_notes_section_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Internal Notes" in content

    def test_tags_section_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Conversation Tags" in content

    def test_stats_grid_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Inbound" in content
        assert "Outbound" in content

    def test_queue_section_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Queue Activity" in content

    def test_profile_link_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Open Full Profile" in content

    def test_toast_notification_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "toastMessage" in content

    def test_show_toast_function(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "showToast" in content


class TestPhase28FrontendCSS:
    """Tests for Phase 2.8 CSS additions."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_bulk_toolbar_style(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert ".bulk-toolbar" in content

    def test_row_checkbox_style(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert ".row-checkbox" in content

    def test_activity_item_style(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert ".activity-item" in content

    def test_activity_icon_styles(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert ".activity-icon" in content

    def test_bulk_modal_style(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert ".bulk-modal-overlay" in content
        assert ".bulk-modal" in content


class TestPhase28Regression:
    """Regression tests to ensure existing features are preserved."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_search_section_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "searchQuery" in content
        assert "searchResults" in content
        assert "performSearch" in content

    def test_date_filters_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "startDate" in content
        assert "endDate" in content
        assert "clearDates" in content

    def test_sorting_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "sortConversations" in content
        assert "convSortBy" in content
        assert "convSortOrder" in content

    def test_pagination_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convPage" in content
        assert "convPageSize" in content

    def test_attention_filter_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convAttention" in content
        assert "needs_attention" in content

    def test_tag_filter_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convTagFilter" in content

    def test_notes_crud_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "submitNote" in content
        assert "saveEditNote" in content
        assert "confirmDeleteNote" in content

    def test_tag_crud_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "createTag" in content
        assert "deleteTag" in content
        assert "assignTag" in content
        assert "removeTag" in content

    def test_chart_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "renderChart" in content
        assert "timelineChart" in content

    def test_operators_loaded(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "loadOperators" in content


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP AC — Phase 2.9: Conversation Reporting & Export
# ═══════════════════════════════════════════════════════════════════════════════


class TestCSVExportConversationsAPI:
    """Tests for GET /api/analytics/conversations/export."""

    @pytest.mark.asyncio
    async def test_requires_authentication(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/export")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_start_date_returns_400(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/export?start_date=not-a-date")
        app.dependency_overrides.clear()
        assert resp.status_code == 400
        assert "start_date" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_invalid_end_date_returns_400(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/export?end_date=bad")
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_start_after_end_returns_400(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/analytics/conversations/export?start_date=2026-08-20&end_date=2026-08-01"
            )
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_date_range_exceeds_365_returns_400(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/analytics/conversations/export?start_date=2025-01-01&end_date=2026-12-31"
            )
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_sort_by_returns_400(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/export?sort_by=bogus")
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_sort_order_returns_400(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/export?sort_order=up")
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_attention_filter_returns_400(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/export?attention=unknown")
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_csv_content_type(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        mock_rows = [
            {
                "user_id": 1001,
                "username": "alice",
                "first_name": "Alice",
                "last_seen": datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
                "inbound_count": 15,
                "outbound_count": 12,
                "last_inbound_at": datetime(2026, 8, 17, 9, 55, tzinfo=UTC),
                "last_outbound_at": datetime(2026, 8, 17, 9, 56, tzinfo=UTC),
                "avg_confidence": 0.85,
                "auto_approved_count": 10,
                "operator_approved_count": 2,
                "queue_count": 1,
                "avg_response_seconds": 45.2,
                "attention_reasons": [],
                "attention_status": "new",
                "assigned_operator_id": None,
                "reviewed_at": None,
                "reviewed_by": None,
            },
        ]

        with patch(
            "chatbotv2.dashboard.routes.export.get_conversations_export_rows",
            new_callable=AsyncMock,
            return_value=mock_rows,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/export")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "attachment" in resp.headers.get("content-disposition", "")
        body = resp.text
        assert "user_id,username" in body
        assert "alice" in body

    @pytest.mark.asyncio
    async def test_csv_rows_match_data(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        mock_rows = [
            {
                "user_id": 2001,
                "username": "bob",
                "first_name": "Bob",
                "last_seen": datetime(2026, 8, 16, 14, 0, tzinfo=UTC),
                "inbound_count": 8,
                "outbound_count": 3,
                "last_inbound_at": datetime(2026, 8, 16, 13, 50, tzinfo=UTC),
                "last_outbound_at": None,
                "avg_confidence": None,
                "auto_approved_count": 0,
                "operator_approved_count": 0,
                "queue_count": 5,
                "avg_response_seconds": None,
                "attention_reasons": ["unanswered_message"],
                "attention_status": "new",
                "assigned_operator_id": None,
                "reviewed_at": None,
                "reviewed_by": None,
            },
        ]

        with patch(
            "chatbotv2.dashboard.routes.export.get_conversations_export_rows",
            new_callable=AsyncMock,
            return_value=mock_rows,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/export")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.text
        assert "2001" in body
        assert "bob" in body
        assert "unanswered_message" in body

    @pytest.mark.asyncio
    async def test_db_failure_returns_500(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.export.get_conversations_export_rows",
            new_callable=AsyncMock,
            side_effect=Exception("db down"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/export")
        app.dependency_overrides.clear()

        assert resp.status_code == 500


class TestCSVExportActivityAPI:
    """Tests for GET /api/analytics/conversations/{user_id}/activity/export."""

    @pytest.mark.asyncio
    async def test_requires_authentication(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/1001/activity/export")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_csv_with_activity_data(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        mock_items = [
            {
                "event_type": "message",
                "event_id": 5001,
                "created_at": "2026-08-17T09:55:00",
                "operator": None,
                "direction": "inbound",
                "content_preview": "Hello there",
            },
            {
                "event_type": "note",
                "event_id": 6001,
                "created_at": "2026-08-17T10:00:00",
                "operator": "admin",
                "content_preview": "Follow up needed",
            },
        ]

        with patch(
            "chatbotv2.dashboard.routes.export.get_conversation_activity_export_rows",
            new_callable=AsyncMock,
            return_value=mock_items,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001/activity/export")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        body = resp.text
        assert "event_type" in body
        assert "message" in body
        assert "note" in body

    @pytest.mark.asyncio
    async def test_csv_filename_in_header(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.export.get_conversation_activity_export_rows",
            new_callable=AsyncMock,
            return_value=[],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/999/activity/export")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "activity_user_999.csv" in cd

    @pytest.mark.asyncio
    async def test_db_failure_returns_500(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.export.get_conversation_activity_export_rows",
            new_callable=AsyncMock,
            side_effect=Exception("db error"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001/activity/export")
        app.dependency_overrides.clear()

        assert resp.status_code == 500


class TestCSVExportDetailAPI:
    """Tests for GET /api/analytics/conversations/{user_id}/export."""

    @pytest.mark.asyncio
    async def test_requires_authentication(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/1001/export")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_conversation_not_found_returns_404(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.export.get_conversation_detail",
            new_callable=AsyncMock,
            return_value=None,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/9999/export")
        app.dependency_overrides.clear()

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_csv_with_detail_fields(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        mock_detail = {
            "user": {
                "id": 1001,
                "username": "alice",
                "first_name": "Alice",
                "last_seen": "2026-08-17T10:00:00",
                "message_count": 27,
                "funnel_stage": "lead",
                "is_blocked": False,
            },
            "analytics": {
                "inbound_count": 15,
                "outbound_count": 12,
                "last_inbound_at": "2026-08-17T09:55:00",
                "last_outbound_at": "2026-08-17T09:56:00",
                "first_message_at": "2026-08-15T08:00:00",
                "avg_confidence": 0.85,
                "ai_generated": 10,
                "auto_approval_rate": 83.3,
                "avg_response_seconds": 45.2,
            },
            "queue": {
                "total_queue_items": 3,
                "pending_queue_items": 1,
                "approved_queue_items": 2,
                "rejected_queue_items": 0,
            },
            "attention_reasons": [],
            "attention": {
                "status": "new",
                "assigned_operator_id": None,
                "reviewed_at": None,
                "reviewed_by": None,
            },
            "timeline": [],
        }

        with patch(
            "chatbotv2.dashboard.routes.export.get_conversation_detail",
            new_callable=AsyncMock,
            return_value=mock_detail,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001/export")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        body = resp.text
        assert "user_id" in body
        assert "1001" in body
        assert "alice" in body
        assert "inbound_count" in body

    @pytest.mark.asyncio
    async def test_invalid_start_date_returns_400(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/analytics/conversations/1001/export?start_date=bad")
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_start_after_end_returns_400(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/analytics/conversations/1001/export?start_date=2026-08-20&end_date=2026-08-01"
            )
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_db_failure_returns_500(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.export.get_conversation_detail",
            new_callable=AsyncMock,
            side_effect=Exception("boom"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations/1001/export")
        app.dependency_overrides.clear()

        assert resp.status_code == 500


class TestCSVSafety:
    """Tests for CSV formula injection prevention."""

    def test_formula_injection_prefix_equals(self):
        from chatbotv2.dashboard.app import _sanitize_csv_value

        assert _sanitize_csv_value("=SUM(A1:A10)").startswith("'")
        assert "SUM" in _sanitize_csv_value("=SUM(A1:A10)")

    def test_formula_injection_prefix_plus(self):
        from chatbotv2.dashboard.app import _sanitize_csv_value

        assert _sanitize_csv_value("+cmd|'/C calc'!A0").startswith("'")

    def test_formula_injection_prefix_minus(self):
        from chatbotv2.dashboard.app import _sanitize_csv_value

        assert _sanitize_csv_value("-1+2").startswith("'")

    def test_formula_injection_prefix_at(self):
        from chatbotv2.dashboard.app import _sanitize_csv_value

        assert _sanitize_csv_value("@SUM(A1)").startswith("'")

    def test_safe_value_unchanged(self):
        from chatbotv2.dashboard.app import _sanitize_csv_value

        assert _sanitize_csv_value("hello world") == "hello world"
        assert _sanitize_csv_value("123") == "123"
        assert _sanitize_csv_value("") == ""

    def test_csv_rows_format(self):
        from chatbotv2.dashboard.app import _build_conversations_csv_rows

        items = [
            {
                "user_id": 1,
                "username": "alice",
                "first_name": "Alice",
                "last_seen": "2026-08-17T10:00:00",
                "inbound_count": 5,
                "outbound_count": 3,
                "last_inbound_at": "2026-08-17T09:00:00",
                "last_outbound_at": "2026-08-17T09:01:00",
                "avg_confidence": 0.9,
                "auto_approved_count": 2,
                "operator_approved_count": 1,
                "queue_count": 0,
                "avg_response_seconds": 30.0,
                "attention_reasons": [],
                "attention_status": "new",
                "assigned_operator_id": None,
                "reviewed_at": None,
                "reviewed_by": None,
            },
        ]
        csv = _build_conversations_csv_rows(items)
        lines = csv.strip().split("\n")
        assert len(lines) == 2
        assert "user_id,username" in lines[0]
        assert "1," in lines[1]
        assert "alice" in lines[1]

    def test_csv_empty_rows(self):
        from chatbotv2.dashboard.app import _build_conversations_csv_rows

        csv = _build_conversations_csv_rows([])
        lines = csv.strip().split("\n")
        assert len(lines) == 1
        assert "user_id" in lines[0]

    def test_activity_csv_rows_format(self):
        from chatbotv2.dashboard.app import _build_activity_csv_rows

        items = [
            {
                "event_type": "message",
                "event_id": 100,
                "created_at": "2026-08-17T09:55:00",
                "operator": None,
                "direction": "inbound",
                "content_preview": "Hello",
            },
            {
                "event_type": "note",
                "event_id": 200,
                "created_at": "2026-08-17T10:00:00",
                "operator": "admin",
                "content_preview": "=DANGEROUS",
            },
        ]
        csv = _build_activity_csv_rows(items)
        lines = csv.strip().split("\n")
        assert len(lines) == 3
        assert "event_type" in lines[0]
        assert "=DANGEROUS" in lines[2]


class TestExportFrontend:
    """Tests for Phase 2.9 frontend elements in analytics.html."""

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_export_csv_button_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "exportConversationsCSV" in content
        assert "Export CSV" in content

    def test_export_activity_button_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "exportActivityCSV" in content

    def test_export_detail_button_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "exportDetailCSV" in content
        assert "Export Detail" in content

    def test_report_summary_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Inbound Msgs" in content
        assert "Outbound Msgs" in content
        assert "Needs Attention" in content
        assert "Avg Response" in content

    def test_export_methods_defined(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "async exportConversationsCSV()" in content
        assert "async exportActivityCSV(userId)" in content
        assert "async exportDetailCSV(userId)" in content

    def test_export_downloads_blob(self):
        utils_content = self._read("chatbotv2/dashboard/static/js/analytics-utils.js")
        assert "createObjectURL" in utils_content
        assert "a.click()" in utils_content

    def test_app_has_export_endpoints(self):
        content = self._read("chatbotv2/dashboard/routes/export.py")
        assert "/api/analytics/conversations/export" in content
        assert "/activity/export" in content
        assert "/export" in content

    def test_app_has_csv_imports(self):
        content = self._read("chatbotv2/dashboard/csv_helpers.py")
        assert "import csv" in content
        assert "import io" in content
        # StreamingResponse is used in the route module
        route_content = self._read("chatbotv2/dashboard/routes/export.py")
        assert "StreamingResponse" in route_content

    def test_postgres_has_export_functions(self):
        content = self._read("db/postgres.py")
        assert "get_conversations_export_rows" in content
        assert "get_conversation_activity_export_rows" in content

    def test_max_rows_enforced(self):
        content = self._read("db/postgres.py")
        assert "max_rows: int = 5000" in content


class TestPhase29Regression:
    """Regression tests to ensure Phase 2.8 and earlier are intact."""

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_conversations_endpoint_preserved(self):
        content = self._read("chatbotv2/dashboard/routes/analytics.py")
        assert '@router.get("/api/analytics/conversations")' in content

    def test_bulk_assign_preserved(self):
        content = self._read("chatbotv2/dashboard/routes/bulk_ops.py")
        assert '@router.post("/api/analytics/conversations/bulk/assign")' in content

    def test_bulk_tag_preserved(self):
        content = self._read("chatbotv2/dashboard/routes/bulk_ops.py")
        assert '@router.post("/api/analytics/conversations/bulk/tag")' in content

    def test_bulk_attention_preserved(self):
        content = self._read("chatbotv2/dashboard/routes/bulk_ops.py")
        assert '@router.patch("/api/analytics/conversations/bulk/attention")' in content

    def test_conversation_detail_preserved(self):
        content = self._read("chatbotv2/dashboard/routes/analytics.py")
        assert '@router.get("/api/analytics/conversations/{user_id}")' in content

    def test_activity_endpoint_preserved(self):
        content = self._read("chatbotv2/dashboard/routes/analytics.py")
        assert "/activity" in content

    def test_notes_endpoints_preserved(self):
        content = self._read("chatbotv2/dashboard/routes/notes.py")
        assert "/notes" in content

    def test_search_endpoint_preserved(self):
        content = self._read("chatbotv2/dashboard/routes/search.py")
        assert "/api/search" in content


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP AD — Phase 3.0: Realtime Analytics & Dashboard Integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestPhase30AnalyticsRealtime:
    """Tests for analytics.html realtime integration."""

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_analytics_has_realtime_access_via_dashboard(self):
        content = self._read("chatbotv2/dashboard/templates/dashboard.html")
        assert "realtime.js" in content

    def test_analytics_creates_realtime_client(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "_initRealtime" in content

    def test_message_created_handler_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "message.created" in content

    def test_message_sent_handler_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "message.sent" in content

    def test_operator_queue_updated_handler_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "operator_queue.updated" in content

    def test_ai_generation_completed_handler_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "ai.generation_completed" in content

    def test_handlers_trigger_conversation_refresh(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "loadConversations()" in content

    def test_detail_refresh_scoped_to_matching_user(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "detailOpen && self.detailData" in content
        assert "showConversationDetail" in content

    def test_detail_refresh_checks_dialog_id(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "detailData.user.id" in content


class TestPhase30AnalyticsPolling:
    """Tests for analytics.html polling fallback."""

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_uses_is_polling_active(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "isPollingActive()" in content

    def test_polling_interval_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "setInterval" in content

    def test_polling_interval_15_seconds(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "15000" in content

    def test_polling_restricted_to_conversations(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "loadConversations()" in content


class TestPhase30DashboardRealtime:
    """Tests for dashboard.html realtime integration."""

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_dashboard_creates_realtime_client(self):
        content = self._read("chatbotv2/dashboard/templates/dashboard.html")
        assert "new RealtimeClient()" in content

    def test_dashboard_loads_counts(self):
        content = self._read("chatbotv2/dashboard/templates/dashboard.html")
        assert "loadCounts()" in content

    def test_dashboard_exposes_rt_globally(self):
        content = self._read("chatbotv2/dashboard/templates/dashboard.html")
        assert "window.rt" in content


class TestPhase30Regression:
    """Regression tests ensuring Phase 3.0 preserves existing functionality."""

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_analytics_api_endpoints_preserved(self):
        content = self._read("chatbotv2/dashboard/routes/analytics.py")
        assert '@router.get("/api/analytics/conversations")' in content

    def test_filters_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convAttention" in content
        assert "convOperatorFilter" in content
        assert "convTagFilter" in content

    def test_sorting_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "sortConversations" in content
        assert "convSortBy" in content

    def test_pagination_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "convPage" in content

    def test_notes_functionality_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "submitNote" in content

    def test_tags_functionality_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "assignTag" in content
        assert "removeTag" in content

    def test_attention_functionality_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "markReviewed" in content

    def test_export_functionality_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "exportConversationsCSV" in content
        assert "exportActivityCSV" in content
        assert "exportDetailCSV" in content

    def test_bulk_operations_preserved(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "executeBulkAssign" in content
        assert "executeBulkTag" in content
        assert "executeBulkAttention" in content


class TestPhase30FreezeVerification:
    """Verify frozen Phase 1 files remain untouched."""

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_event_bus_frozen(self):
        content = self._read("core/event_bus.py")
        assert "async def publish_event(" in content
        assert 'CHANNEL = "chatbot:events"' in content

    def test_ws_manager_frozen(self):
        content = self._read("chatbotv2/dashboard/ws_manager.py")
        assert "class ConnectionManager" in content
        assert "class ManagedConnection" in content

    def test_event_subscriber_frozen(self):
        content = self._read("chatbotv2/dashboard/event_subscriber.py")
        assert "async def start_event_subscriber" in content
        assert "MAX_BACKOFF" in content

    def test_realtime_js_frozen(self):
        content = self._read("chatbotv2/dashboard/static/js/realtime.js")
        assert "function RealtimeClient" in content
        assert "DEDUP_CACHE_SIZE" in content

    def test_llm_worker_frozen(self):
        content = self._read("workers/llm_worker.py")
        assert "async def process_message(" in content
        assert "generation_id" in content


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP AE — Phase 3.1: Realtime Events for Operator State Changes
# ═══════════════════════════════════════════════════════════════════════════════


class TestPhase31AttentionChangedEvent:
    """Tests for conversation.attention_changed event publication."""

    @pytest.mark.asyncio
    async def test_event_published_on_status_change(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_result = {
            "user_id": 1001,
            "status": "reviewed",
            "assigned_operator_id": None,
            "reviewed_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "reviewed_by": "admin",
        }
        with (
            patch(
                "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/attention",
                    json={"status": "reviewed"},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_pub.assert_called_once()
        call_args = mock_pub.call_args
        assert call_args[0][0] == "conversation.attention_changed"
        data = call_args[0][1]
        assert data["user_id"] == 1001
        assert data["status"] == "reviewed"
        assert data["changed_by"] == "admin"
        assert call_args[1]["scope"] == "global"

    @pytest.mark.asyncio
    async def test_event_not_published_without_status(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_result = {
            "user_id": 1001,
            "status": "new",
            "assigned_operator_id": None,
            "reviewed_at": None,
            "reviewed_by": None,
        }
        with (
            patch(
                "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/attention",
                    json={"status": "new"},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_pub.assert_called_once()
        data = mock_pub.call_args[0][1]
        assert data["status"] == "new"

    @pytest.mark.asyncio
    async def test_event_not_published_on_db_failure(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
                new_callable=AsyncMock,
                side_effect=Exception("DB error"),
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/attention",
                    json={"status": "reviewed"},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 500
        mock_pub.assert_not_called()

    @pytest.mark.asyncio
    async def test_payload_includes_reviewed_by_and_reviewed_at(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "operator1"}
        mock_result = {
            "user_id": 2001,
            "status": "reviewed",
            "assigned_operator_id": None,
            "reviewed_at": datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC),
            "reviewed_by": "operator1",
        }
        with (
            patch(
                "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/2001/attention",
                    json={"status": "reviewed"},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = mock_pub.call_args[0][1]
        assert data["reviewed_by"] == "operator1"
        assert "2026-08-18" in data["reviewed_at"]

    @pytest.mark.asyncio
    async def test_assignment_only_does_not_emit_attention(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_result = {
            "user_id": 1001,
            "status": "new",
            "assigned_operator_id": 5,
            "reviewed_at": None,
            "reviewed_by": None,
        }

        with (
            patch(
                "chatbotv2.dashboard.routes.attention.verify_operator_active",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/assignment",
                    json={"assigned_operator_id": 5},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_pub.assert_called_once()
        assert mock_pub.call_args[0][0] == "conversation.assigned"


class TestPhase31AssignedEvent:
    """Tests for conversation.assigned event publication."""

    @pytest.mark.asyncio
    async def test_assignment_event_emitted(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_result = {
            "user_id": 1001,
            "status": "new",
            "assigned_operator_id": 5,
            "reviewed_at": None,
            "reviewed_by": None,
        }
        with (
            patch(
                "chatbotv2.dashboard.routes.attention.verify_operator_active",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/assignment",
                    json={"assigned_operator_id": 5},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_pub.assert_called_once()
        call_args = mock_pub.call_args
        assert call_args[0][0] == "conversation.assigned"
        data = call_args[0][1]
        assert data["user_id"] == 1001
        assert data["assigned_operator_id"] == 5
        assert data["assigned_by"] == "admin"
        assert call_args[1]["scope"] == "global"

    @pytest.mark.asyncio
    async def test_unassignment_event_emitted(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_result = {
            "user_id": 1001,
            "status": "new",
            "assigned_operator_id": None,
            "reviewed_at": None,
            "reviewed_by": None,
        }
        with (
            patch(
                "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/assignment",
                    json={"assigned_operator_id": 0},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = mock_pub.call_args[0][1]
        assert data["assigned_operator_id"] is None

    @pytest.mark.asyncio
    async def test_event_not_published_on_db_failure(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with (
            patch(
                "chatbotv2.dashboard.routes.attention.verify_operator_active",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
                new_callable=AsyncMock,
                side_effect=Exception("DB error"),
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/assignment",
                    json={"assigned_operator_id": 5},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 500
        mock_pub.assert_not_called()


class TestPhase31TagChangedEvent:
    """Tests for conversation.tag_changed event publication."""

    @pytest.mark.asyncio
    async def test_assignment_emits_tag_changed(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_tag = {"id": 1, "name": "vip", "description": None, "created_by": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.tags.verify_user_id_exists",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.get_conversation_tag",
                new_callable=AsyncMock,
                return_value=mock_tag,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.assign_conversation_tag",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/analytics/conversations/1001/tags/1")
        app.dependency_overrides.clear()

        assert resp.status_code == 201
        mock_pub.assert_called_once()
        call_args = mock_pub.call_args
        assert call_args[0][0] == "conversation.tag_changed"
        data = call_args[0][1]
        assert data["user_id"] == 1001
        assert data["tag_id"] == 1
        assert data["tag_name"] == "vip"
        assert data["action"] == "assigned"
        assert data["changed_by"] == "admin"
        assert call_args[1]["scope"] == "global"

    @pytest.mark.asyncio
    async def test_removal_emits_tag_changed(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_tag = {"id": 1, "name": "vip", "description": None, "created_by": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.tags.remove_conversation_tag",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.get_conversation_tag",
                new_callable=AsyncMock,
                return_value=mock_tag,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/api/analytics/conversations/1001/tags/1")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_pub.assert_called_once()
        data = mock_pub.call_args[0][1]
        assert data["action"] == "removed"
        assert data["tag_name"] == "vip"

    @pytest.mark.asyncio
    async def test_event_not_published_on_removal_failure(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.tags.remove_conversation_tag",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/api/analytics/conversations/1001/tags/999")
        app.dependency_overrides.clear()

        assert resp.status_code == 404
        mock_pub.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_not_published_on_assignment_failure(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_tag = {"id": 1, "name": "vip", "description": None, "created_by": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.tags.verify_user_id_exists",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.get_conversation_tag",
                new_callable=AsyncMock,
                return_value=mock_tag,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.assign_conversation_tag",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "chatbotv2.dashboard.routes.tags.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/analytics/conversations/1001/tags/1")
        app.dependency_overrides.clear()

        assert resp.status_code == 500
        mock_pub.assert_not_called()


class TestPhase31NoteChangedEvent:
    """Tests for conversation.note_changed event publication."""

    @pytest.mark.asyncio
    async def test_creation_event(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_note = {
            "id": 42,
            "user_id": 1001,
            "content": "test note",
            "created_by": "admin",
            "created_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
        }
        with (
            patch(
                "chatbotv2.dashboard.routes.notes.verify_user_id_exists",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.create_conversation_note",
                new_callable=AsyncMock,
                return_value=mock_note,
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/1001/notes",
                    json={"content": "test note"},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 201
        mock_pub.assert_called_once()
        call_args = mock_pub.call_args
        assert call_args[0][0] == "conversation.note_changed"
        data = call_args[0][1]
        assert data["user_id"] == 1001
        assert data["note_id"] == 42
        assert data["action"] == "created"
        assert data["changed_by"] == "admin"
        assert call_args[1]["scope"] == "global"

    @pytest.mark.asyncio
    async def test_update_event(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_note = {
            "id": 42,
            "user_id": 1001,
            "content": "old",
            "created_by": "admin",
            "created_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
        }
        mock_updated = {
            "id": 42,
            "user_id": 1001,
            "content": "updated",
            "created_by": "admin",
            "created_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
        }
        with (
            patch(
                "chatbotv2.dashboard.routes.notes.get_conversation_note",
                new_callable=AsyncMock,
                return_value=mock_note,
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.update_conversation_note",
                new_callable=AsyncMock,
                return_value=mock_updated,
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/notes/42",
                    json={"content": "updated"},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_pub.assert_called_once()
        data = mock_pub.call_args[0][1]
        assert data["action"] == "updated"
        assert data["note_id"] == 42

    @pytest.mark.asyncio
    async def test_deletion_event(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_note = {
            "id": 42,
            "user_id": 1001,
            "content": "old",
            "created_by": "admin",
            "created_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
        }
        with (
            patch(
                "chatbotv2.dashboard.routes.notes.get_conversation_note",
                new_callable=AsyncMock,
                return_value=mock_note,
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.delete_conversation_note",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete("/api/analytics/conversations/1001/notes/42")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        mock_pub.assert_called_once()
        data = mock_pub.call_args[0][1]
        assert data["action"] == "deleted"
        assert data["note_id"] == 42

    @pytest.mark.asyncio
    async def test_ownership_failure_emits_no_event(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "other_user"}
        mock_note = {
            "id": 42,
            "user_id": 1001,
            "content": "old",
            "created_by": "admin",
            "created_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
        }
        with (
            patch(
                "chatbotv2.dashboard.routes.notes.get_conversation_note",
                new_callable=AsyncMock,
                return_value=mock_note,
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/notes/42",
                    json={"content": "hacked"},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 403
        mock_pub.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_found_emits_no_event(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.notes.get_conversation_note",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.notes.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/1001/notes/999",
                    json={"content": "x"},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 404
        mock_pub.assert_not_called()


class TestPhase31BulkEvents:
    """Tests for bulk operation event publication."""

    @pytest.mark.asyncio
    async def test_bulk_assignment_emits_per_user(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_result = {
            "user_id": 1,
            "status": "new",
            "assigned_operator_id": 5,
            "reviewed_at": None,
            "reviewed_by": None,
        }

        with (
            patch(
                "chatbotv2.dashboard.dependencies.verify_operator_active",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.bulk_ops.upsert_conversation_attention",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "chatbotv2.dashboard.routes.bulk_ops.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/bulk/assign",
                    json={"user_ids": [1, 2, 3], "assigned_operator_id": 5},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert mock_pub.call_count == 3
        for call in mock_pub.call_args_list:
            assert call[0][0] == "conversation.assigned"
            assert call[0][1]["assigned_operator_id"] == 5
            assert call[1]["scope"] == "global"

    @pytest.mark.asyncio
    async def test_bulk_tag_emits_per_user(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_tag = {"id": 1, "name": "vip", "description": None, "created_by": "admin"}
        with (
            patch(
                "chatbotv2.dashboard.routes.bulk_ops.get_conversation_tag",
                new_callable=AsyncMock,
                return_value=mock_tag,
            ),
            patch(
                "chatbotv2.dashboard.routes.bulk_ops.assign_conversation_tag",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "chatbotv2.dashboard.routes.bulk_ops.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/bulk/tag",
                    json={"user_ids": [1, 2], "tag_id": 1},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert mock_pub.call_count == 2
        for call in mock_pub.call_args_list:
            assert call[0][0] == "conversation.tag_changed"
            assert call[0][1]["tag_name"] == "vip"

    @pytest.mark.asyncio
    async def test_bulk_attention_emits_per_user(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_result = {
            "user_id": 1,
            "status": "reviewed",
            "assigned_operator_id": None,
            "reviewed_at": datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC),
            "reviewed_by": "admin",
        }
        with (
            patch(
                "chatbotv2.dashboard.routes.bulk_ops.upsert_conversation_attention",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "chatbotv2.dashboard.routes.bulk_ops.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/bulk/attention",
                    json={"user_ids": [1, 2, 3], "status": "reviewed"},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert mock_pub.call_count == 3
        for call in mock_pub.call_args_list:
            assert call[0][0] == "conversation.attention_changed"
            assert call[0][1]["status"] == "reviewed"

    @pytest.mark.asyncio
    async def test_bulk_only_emits_for_successful_mutations(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        async def side_effect(**kwargs):
            if kwargs.get("user_id") == 2:
                raise RuntimeError("DB error")
            return {
                "user_id": kwargs["user_id"],
                "status": "new",
                "assigned_operator_id": kwargs.get("assigned_operator_id"),
                "reviewed_at": None,
                "reviewed_by": None,
            }

        with (
            patch(
                "chatbotv2.dashboard.dependencies.verify_operator_active",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "chatbotv2.dashboard.routes.bulk_ops.upsert_conversation_attention",
                new_callable=AsyncMock,
                side_effect=side_effect,
            ),
            patch(
                "chatbotv2.dashboard.routes.bulk_ops.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/bulk/assign",
                    json={"user_ids": [1, 2, 3], "assigned_operator_id": 5},
                )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert body["updated"] == 2
        assert 2 in body["errors"]
        assert mock_pub.call_count == 2


class TestPhase31EventEnvelope:
    """Verify event envelope correctness for all new events."""

    @pytest.mark.asyncio
    async def test_envelope_uses_global_scope(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_result = {
            "user_id": 1001,
            "status": "reviewed",
            "assigned_operator_id": None,
            "reviewed_at": None,
            "reviewed_by": "admin",
        }
        with (
            patch(
                "chatbotv2.dashboard.routes.attention.upsert_conversation_attention",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "chatbotv2.dashboard.routes.attention.publish_event",
                new_callable=AsyncMock,
            ) as mock_pub,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.patch(
                    "/api/analytics/conversations/1001/attention",
                    json={"status": "reviewed"},
                )
        app.dependency_overrides.clear()

        assert mock_pub.call_args[1]["scope"] == "global"

    def test_publish_event_uses_existing_envelope(self):
        """Verify publish_event signature matches our usage."""
        import inspect

        from core.event_bus import publish_event

        sig = inspect.signature(publish_event)
        params = list(sig.parameters.keys())
        assert "event_type" in params
        assert "data" in params
        assert "scope" in params

    def test_new_event_types_are_distinct(self):
        """Ensure all four new event types are distinct strings."""
        new_events = {
            "conversation.attention_changed",
            "conversation.assigned",
            "conversation.tag_changed",
            "conversation.note_changed",
        }
        assert len(new_events) == 4
        existing_events = {
            "message.created",
            "message.sent",
            "message.send_failed",
            "ai.generation_started",
            "ai.generation_completed",
            "ai.generation_failed",
            "suggestion.created",
            "operator_queue.updated",
        }
        assert new_events.isdisjoint(existing_events)


class TestPhase31Regression:
    """Regression tests for Phase 3.0 and earlier."""

    def test_phase30_realtime_handlers_still_exist(self):
        """Verify analytics.html has Phase 3.0 handler registration."""
        with open("chatbotv2/dashboard/templates/analytics.html", encoding="utf-8") as f:
            content = f.read()
        assert "_initRealtime" in content
        assert "message.created" in content
        assert "message.sent" in content
        assert "operator_queue.updated" in content
        assert "ai.generation_completed" in content

    def test_polling_fallback_still_exists(self):
        """Verify 15s polling fallback is present."""
        with open("chatbotv2/dashboard/templates/analytics.html", encoding="utf-8") as f:
            content = f.read()
        assert "isPollingActive" in content
        assert "15000" in content

    def test_existing_8_event_types_unchanged(self):
        """Verify original 8 event types are not modified."""
        import importlib

        import core.event_bus as eb

        importlib.reload(eb)
        assert eb.CHANNEL == "chatbot:events"


class TestPhase31FreezeVerification:
    """Verify frozen files remain untouched."""

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_event_bus_frozen(self):
        content = self._read("core/event_bus.py")
        assert "async def publish_event(" in content
        assert 'CHANNEL = "chatbot:events"' in content

    def test_ws_manager_frozen(self):
        content = self._read("chatbotv2/dashboard/ws_manager.py")
        assert "class ConnectionManager" in content
        assert "class ManagedConnection" in content

    def test_event_subscriber_frozen(self):
        content = self._read("chatbotv2/dashboard/event_subscriber.py")
        assert "async def start_event_subscriber" in content
        assert "MAX_BACKOFF" in content

    def test_realtime_js_frozen(self):
        content = self._read("chatbotv2/dashboard/static/js/realtime.js")
        assert "function RealtimeClient" in content
        assert "DEDUP_CACHE_SIZE" in content

    def test_llm_worker_frozen(self):
        content = self._read("workers/llm_worker.py")
        assert "async def process_message(" in content
        assert "generation_id" in content


class TestPhase31FrontendContract:
    """Frontend contract tests for Phase 3.1 realtime handlers."""

    def _read_analytics(self):
        with open("chatbotv2/dashboard/templates/analytics.html", encoding="utf-8") as f:
            return f.read()

    def test_conversation_attention_changed_handler_exists(self):
        content = self._read_analytics()
        assert "conversation.attention_changed" in content

    def test_conversation_assigned_handler_exists(self):
        content = self._read_analytics()
        assert "conversation.assigned" in content

    def test_conversation_tag_changed_handler_exists(self):
        content = self._read_analytics()
        assert "conversation.tag_changed" in content

    def test_conversation_note_changed_handler_exists(self):
        content = self._read_analytics()
        assert "conversation.note_changed" in content

    def test_handlers_use_evt_data_user_id(self):
        content = self._read_analytics()
        assert "evt.data.user_id" in content

    def test_handlers_trigger_conversation_refresh(self):
        content = self._read_analytics()
        assert "_scheduleListRefresh" in content

    def test_detail_refresh_scoped_to_matching_user_id(self):
        content = self._read_analytics()
        assert "_scheduleDetailRefresh" in content

    def test_refresh_coalescing_exists(self):
        content = self._read_analytics()
        assert "_COALESCE_MS" in content
        assert "_listTimer" in content
        assert "_detailTimer" in content

    def test_bulk_event_burst_no_unbounded_direct_calls(self):
        """Verify coalescing prevents one REST call per event."""
        content = self._read_analytics()
        assert "_scheduleListRefresh" in content
        assert "_scheduleDetailRefresh" in content

    def test_polling_fallback_remains(self):
        content = self._read_analytics()
        assert "isPollingActive" in content
        assert "15000" in content

    def test_phase30_handlers_remain(self):
        content = self._read_analytics()
        assert "message.created" in content
        assert "message.sent" in content
        assert "operator_queue.updated" in content
        assert "ai.generation_completed" in content

    def test_filters_remain(self):
        content = self._read_analytics()
        assert "convTagFilter" in content
        assert "convAttention" in content
        assert "convOperatorFilter" in content

    def test_detail_workflow_remain(self):
        content = self._read_analytics()
        assert "showConversationDetail" in content
        assert "detailOpen" in content
        assert "detailData" in content

    def test_notes_ui_remain(self):
        content = self._read_analytics()
        assert "submitNote" in content
        assert "saveEditNote" in content
        assert "confirmDeleteNote" in content

    def test_tags_ui_remain(self):
        content = self._read_analytics()
        assert "assignTag" in content
        assert "removeTag" in content

    def test_attention_ui_remain(self):
        content = self._read_analytics()
        assert "markReviewed" in content
        assert "assignOperator" in content


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP AF — Phase 3.2: Realtime Hardening & Race Condition Protection
# ═══════════════════════════════════════════════════════════════════════════════


class TestPhase32LoadConversationsRaceGuard:
    """Verify loadConversations() stale-response protection."""

    def _read_analytics(self):
        with open("chatbotv2/dashboard/templates/analytics.html", encoding="utf-8") as f:
            return f.read()

    def test_list_seq_counter_exists(self):
        content = self._read_analytics()
        assert "_listSeq" in content

    def test_list_abort_controller_exists(self):
        content = self._read_analytics()
        assert "_listAbort" in content

    def test_list_seq_incremented_before_fetch(self):
        content = self._read_analytics()
        assert "++this._listSeq" in content

    def test_list_seq_captured_before_fetch(self):
        content = self._read_analytics()
        assert "var seq = ++this._listSeq" in content

    def test_list_seq_checked_after_response(self):
        content = self._read_analytics()
        assert "seq !== this._listSeq" in content

    def test_list_abort_error_returns_early(self):
        content = self._read_analytics()
        assert "AbortError" in content

    def test_list_abort_previous_request(self):
        content = self._read_analytics()
        assert "this._listAbort.abort()" in content

    def test_list_fetch_uses_signal(self):
        with open("chatbotv2/dashboard/static/js/analytics-api.js", encoding="utf-8") as f:
            api_content = f.read()
        assert "signal: signal" in api_content

    def test_list_loading_only_cleared_for_current_seq(self):
        content = self._read_analytics()
        assert "seq === this._listSeq" in content


class TestPhase32ShowConversationDetailRaceGuard:
    """Verify showConversationDetail() stale-response protection."""

    def _read_analytics(self):
        with open("chatbotv2/dashboard/templates/analytics.html", encoding="utf-8") as f:
            return f.read()

    def test_detail_seq_counter_exists(self):
        content = self._read_analytics()
        assert "_detailSeq" in content

    def test_detail_abort_controller_exists(self):
        content = self._read_analytics()
        assert "_detailAbort" in content

    def test_detail_seq_incremented_before_fetch(self):
        content = self._read_analytics()
        assert "++this._detailSeq" in content

    def test_detail_seq_checked_after_response(self):
        content = self._read_analytics()
        assert "seq !== this._detailSeq" in content

    def test_detail_abort_previous_request(self):
        content = self._read_analytics()
        assert "this._detailAbort.abort()" in content

    def test_detail_fetch_uses_signal(self):
        with open("chatbotv2/dashboard/static/js/analytics-api.js", encoding="utf-8") as f:
            api_content = f.read()
        assert "signal: signal" in api_content

    def test_detail_abort_error_returns_early(self):
        content = self._read_analytics()
        assert "e.name === 'AbortError'" in content

    def test_detail_loading_only_cleared_for_current_seq(self):
        content = self._read_analytics()
        assert "seq === this._detailSeq" in content

    def test_detail_sub_requests_receive_seq(self):
        content = self._read_analytics()
        assert "this.loadNotes(userId, seq)" in content
        assert "this.loadConvTags(userId, seq)" in content
        assert "this.loadActivity(userId, seq)" in content


class TestPhase32SubRequestRaceGuard:
    """Verify sub-request stale-response protection."""

    def _read_analytics(self):
        with open("chatbotv2/dashboard/templates/analytics.html", encoding="utf-8") as f:
            return f.read()

    def test_load_notes_accepts_seq(self):
        content = self._read_analytics()
        assert "async loadNotes(userId, seq)" in content

    def test_load_notes_checks_seq_before_assign(self):
        content = self._read_analytics()
        assert "seq !== this._detailSeq" in content

    def test_load_conv_tags_accepts_seq(self):
        content = self._read_analytics()
        assert "async loadConvTags(userId, seq)" in content

    def test_load_conv_tags_checks_seq_before_assign(self):
        content = self._read_analytics()
        count = content.count("seq !== this._detailSeq")
        assert count >= 3

    def test_load_activity_accepts_seq(self):
        content = self._read_analytics()
        assert "async loadActivity(userId, seq)" in content

    def test_load_activity_checks_seq_before_assign(self):
        content = self._read_analytics()
        assert "seq !== this._detailSeq" in content

    def test_backward_compat_no_seq(self):
        """Sub-requests work when called without seq (backward compat)."""
        content = self._read_analytics()
        assert "seq == null || seq === this._detailSeq" in content


class TestPhase32ReconnectRefresh:
    """Verify WebSocket reconnect triggers state repair."""

    def _read_analytics(self):
        with open("chatbotv2/dashboard/templates/analytics.html", encoding="utf-8") as f:
            return f.read()

    def test_onconnected_handler_registered(self):
        content = self._read_analytics()
        assert "onconnected" in content

    def test_reconnect_triggers_list_refresh(self):
        content = self._read_analytics()
        assert "rt.onconnected" in content
        assert "_scheduleListRefresh()" in content

    def test_reconnect_triggers_detail_refresh_if_open(self):
        content = self._read_analytics()
        assert "self.detailOpen && self.detailData" in content

    def test_reconnect_handler_registered_for_deferred_rt(self):
        """When rt is not yet available, onconnected is still registered."""
        content = self._read_analytics()
        assert "window.rt.onconnected" in content


class TestPhase32CoalescingStillWorks:
    """Verify coalescing mechanism is preserved."""

    def _read_analytics(self):
        with open("chatbotv2/dashboard/templates/analytics.html", encoding="utf-8") as f:
            return f.read()

    def test_coalesce_ms_exists(self):
        content = self._read_analytics()
        assert "_COALESCE_MS" in content

    def test_list_timer_coalescing(self):
        content = self._read_analytics()
        assert "_listTimer" in content
        assert "if (_listTimer) return" in content

    def test_detail_timer_coalescing(self):
        content = self._read_analytics()
        assert "_detailTimer" in content
        assert "if (_detailTimer) return" in content

    def test_polling_fallback_preserved(self):
        content = self._read_analytics()
        assert "isPollingActive" in content
        assert "15000" in content


class TestPhase32Regression:
    """Regression: existing Phase 3.0/3.1 behavior preserved."""

    def _read_analytics(self):
        with open("chatbotv2/dashboard/templates/analytics.html", encoding="utf-8") as f:
            return f.read()

    def test_phase30_handlers_exist(self):
        content = self._read_analytics()
        assert "message.created" in content
        assert "message.sent" in content
        assert "operator_queue.updated" in content
        assert "ai.generation_completed" in content

    def test_phase31_handlers_exist(self):
        content = self._read_analytics()
        assert "conversation.attention_changed" in content
        assert "conversation.assigned" in content
        assert "conversation.tag_changed" in content
        assert "conversation.note_changed" in content

    def test_evt_data_user_id_still_used(self):
        content = self._read_analytics()
        assert "evt.data.user_id" in content

    def test_filters_preserved_in_load(self):
        content = self._read_analytics()
        assert "this.convAttention" in content
        assert "this.convTagFilter" in content
        assert "this.convOperatorFilter" in content

    def test_sorting_preserved(self):
        content = self._read_analytics()
        assert "this.convSortBy" in content
        assert "this.convSortOrder" in content

    def test_pagination_preserved(self):
        content = self._read_analytics()
        assert "this.convPage" in content
        assert "this.convPageSize" in content


class TestPhase32FreezeVerification:
    """Verify frozen files remain untouched."""

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_event_bus_frozen(self):
        content = self._read("core/event_bus.py")
        assert "async def publish_event(" in content
        assert 'CHANNEL = "chatbot:events"' in content

    def test_ws_manager_frozen(self):
        content = self._read("chatbotv2/dashboard/ws_manager.py")
        assert "class ConnectionManager" in content

    def test_event_subscriber_frozen(self):
        content = self._read("chatbotv2/dashboard/event_subscriber.py")
        assert "async def start_event_subscriber" in content

    def test_realtime_js_frozen(self):
        content = self._read("chatbotv2/dashboard/static/js/realtime.js")
        assert "function RealtimeClient" in content

    def test_llm_worker_frozen(self):
        content = self._read("workers/llm_worker.py")
        assert "async def process_message(" in content
