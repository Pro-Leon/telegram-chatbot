"""Tests for Phase 5.3 — Segment integration into CRM workflows.

Covers: fan list filtering, dialog filtering, user-segment membership,
bulk operation segment targeting, conversation analytics filtering.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ── Helpers ──────────────────────────────────────────────────────────────


class _FakePoolConnCM:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


def _make_pool(mock_conn):
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))
    return pool


FAKE_SEGMENT = {
    "id": 1,
    "creator_id": 100,
    "name": "Active Buyers",
    "description": None,
    "rules": {
        "operator": "AND",
        "children": [
            {"field": "is_blocked", "operator": "=", "value": False},
            {"field": "has_purchased", "operator": "=", "value": True},
        ],
    },
    "is_enabled": True,
    "member_count": 5,
    "last_evaluated_at": None,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

DISABLED_SEGMENT = {**FAKE_SEGMENT, "is_enabled": False}


def _make_creator_ctx(ready=True):
    from commerce.single_creator import SingleCreatorStatus
    ctx = MagicMock()
    ctx.status = SingleCreatorStatus.READY if ready else SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE
    ctx.creator_id = 100 if ready else None
    return ctx


# Patch helper: patch at usage site since routes import the name directly
_R_USERS = "chatbotv2.dashboard.routes.users.resolve_single_application_creator"
_R_DIALOGS = "chatbotv2.dashboard.routes.dialogs.resolve_single_application_creator"
_R_BULK = "chatbotv2.dashboard.routes.bulk_ops.resolve_single_application_creator"
_R_ANALYTICS = "chatbotv2.dashboard.routes.analytics.resolve_single_application_creator"
_GSM_BULK = "chatbotv2.dashboard.routes.bulk_ops.get_segment_members"
_GSM_ANALYTICS = "chatbotv2.dashboard.routes.analytics.get_segment_members"


# ── Fan-list segment filter ─────────────────────────────────────────────


class TestFanListSegmentFilter:
    @pytest.mark.asyncio
    async def test_users_without_segment_returns_all(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[{"id": 1, "username": "alice", "first_name": "Alice", "message_count": 5, "last_seen": None}])

        # M7 (B6): the unsegmented list is creator-scoped (fail-closed).
        with (
            patch(_R_USERS, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("chatbotv2.dashboard.routes.users.get_pool", new_callable=AsyncMock, return_value=_make_pool(mock_conn)),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/users")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    @pytest.mark.asyncio
    async def test_users_with_valid_segment_filters(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[{"id": 2, "username": "bob", "first_name": "Bob", "message_count": 10, "last_seen": None}])

        with (
            patch(_R_USERS, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=FAKE_SEGMENT),
            patch("chatbotv2.dashboard.routes.users.get_pool", new_callable=AsyncMock, return_value=_make_pool(mock_conn)),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/users?segment_id=1")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == 2

    @pytest.mark.asyncio
    async def test_users_with_nonexistent_segment_returns_404(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with (
            patch(_R_USERS, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=None),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/users?segment_id=999")
        app.dependency_overrides.clear()
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_users_with_disabled_segment_returns_400(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with (
            patch(_R_USERS, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=DISABLED_SEGMENT),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/users?segment_id=1")
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_users_creator_not_ready_returns_503(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(_R_USERS, new_callable=AsyncMock, return_value=_make_creator_ctx(ready=False)):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/users?segment_id=1")
        app.dependency_overrides.clear()
        assert resp.status_code == 503


# ── Dialog-list segment filter ──────────────────────────────────────────


class TestDialogListSegmentFilter:
    @pytest.mark.asyncio
    async def test_dialogs_without_segment_returns_all(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[{"id": 1, "username": "alice", "first_name": "Alice", "message_count": 5, "last_seen": None}])

        # M7 (B6): the unsegmented list is creator-scoped (fail-closed).
        with (
            patch(_R_DIALOGS, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("chatbotv2.dashboard.routes.dialogs.get_pool", new_callable=AsyncMock, return_value=_make_pool(mock_conn)),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/dialogs")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    @pytest.mark.asyncio
    async def test_dialogs_with_valid_segment_filters(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[{"id": 3, "username": "carol", "first_name": "Carol", "message_count": 8, "last_seen": None}])

        with (
            patch(_R_DIALOGS, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=FAKE_SEGMENT),
            patch("chatbotv2.dashboard.routes.dialogs.get_pool", new_callable=AsyncMock, return_value=_make_pool(mock_conn)),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/dialogs?segment_id=1")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == 3

    @pytest.mark.asyncio
    async def test_dialogs_disabled_segment_returns_400(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with (
            patch(_R_DIALOGS, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=DISABLED_SEGMENT),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/dialogs?segment_id=1")
        app.dependency_overrides.clear()
        assert resp.status_code == 400


# ── User-segments membership ────────────────────────────────────────────


class TestUserSegmentsMembership:
    @pytest.mark.asyncio
    async def test_user_segments_returns_matching(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=True)

        with (
            patch(_R_USERS, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.list_segments", new_callable=AsyncMock, return_value=[FAKE_SEGMENT]),
            patch("chatbotv2.dashboard.routes.users.get_pool", new_callable=AsyncMock, return_value=_make_pool(mock_conn)),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/user/42/segments")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == 1
        assert data[0]["name"] == "Active Buyers"
        assert data[0]["is_member"] is True
        assert "member_count" in data[0]

    @pytest.mark.asyncio
    async def test_user_segments_returns_empty_when_no_match(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=False)

        with (
            patch(_R_USERS, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.list_segments", new_callable=AsyncMock, return_value=[FAKE_SEGMENT]),
            patch("chatbotv2.dashboard.routes.users.get_pool", new_callable=AsyncMock, return_value=_make_pool(mock_conn)),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/user/42/segments")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        # Now returns all segments with is_member status
        assert len(data) == 1
        assert data[0]["is_member"] is False
        assert data[0]["name"] == "Active Buyers"

    @pytest.mark.asyncio
    async def test_user_segments_creator_not_ready(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(_R_USERS, new_callable=AsyncMock, return_value=_make_creator_ctx(ready=False)):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/user/42/segments")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json() == []


# ── Bulk-ops segment targeting ──────────────────────────────────────────


class TestBulkOpsSegmentTargeting:
    @pytest.mark.asyncio
    async def test_bulk_assign_with_segment_id(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        members = [{"id": 10}, {"id": 20}, {"id": 30}]

        with (
            patch(_R_BULK, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=FAKE_SEGMENT),
            patch(_GSM_BULK, new_callable=AsyncMock, return_value=members),
            patch("chatbotv2.dashboard.dependencies.verify_operator_active", new_callable=AsyncMock, return_value=None),
            patch("chatbotv2.dashboard.routes.bulk_ops.upsert_conversation_attention", new_callable=AsyncMock, return_value={"status": "new", "assigned_operator_id": 5}),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/bulk/assign",
                    json={"segment_id": 1, "assigned_operator_id": 5},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json()["updated"] == 3

    @pytest.mark.asyncio
    async def test_bulk_tag_with_segment_id(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        members = [{"id": 10}, {"id": 20}]

        with (
            patch(_R_BULK, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=FAKE_SEGMENT),
            patch(_GSM_BULK, new_callable=AsyncMock, return_value=members),
            patch("chatbotv2.dashboard.routes.bulk_ops.get_conversation_tag", new_callable=AsyncMock, return_value={"id": 1, "name": "VIP"}),
            patch("chatbotv2.dashboard.routes.bulk_ops.assign_conversation_tag", new_callable=AsyncMock, return_value=True),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/bulk/tag",
                    json={"segment_id": 1, "tag_id": 1},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json()["assigned"] == 2

    @pytest.mark.asyncio
    async def test_bulk_attention_with_segment_id(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        members = [{"id": 5}]

        with (
            patch(_R_BULK, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=FAKE_SEGMENT),
            patch(_GSM_BULK, new_callable=AsyncMock, return_value=members),
            patch("chatbotv2.dashboard.routes.bulk_ops.upsert_conversation_attention", new_callable=AsyncMock, return_value={"status": "reviewed"}),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.patch(
                    "/api/analytics/conversations/bulk/attention",
                    json={"segment_id": 1, "status": "reviewed"},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json()["updated"] == 1

    @pytest.mark.asyncio
    async def test_bulk_empty_both_returns_400(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with (
            patch(_R_BULK, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=FAKE_SEGMENT),
            patch(_GSM_BULK, new_callable=AsyncMock, return_value=[]),
            patch("chatbotv2.dashboard.dependencies.verify_operator_active", new_callable=AsyncMock, return_value=None),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/bulk/assign",
                    json={"segment_id": 1, "assigned_operator_id": 5},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_bulk_segment_not_found_returns_404(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with (
            patch(_R_BULK, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=None),
            patch("chatbotv2.dashboard.dependencies.verify_operator_active", new_callable=AsyncMock, return_value=None),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/bulk/assign",
                    json={"segment_id": 999, "assigned_operator_id": 5},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_bulk_merges_explicit_and_segment_user_ids(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        members = [{"id": 10}, {"id": 20}]

        with (
            patch(_R_BULK, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=FAKE_SEGMENT),
            patch(_GSM_BULK, new_callable=AsyncMock, return_value=members),
            patch("chatbotv2.dashboard.dependencies.verify_operator_active", new_callable=AsyncMock, return_value=None),
            patch("chatbotv2.dashboard.routes.bulk_ops.upsert_conversation_attention", new_callable=AsyncMock, return_value={"status": "new"}),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/analytics/conversations/bulk/assign",
                    json={"user_ids": [10, 30], "segment_id": 1, "assigned_operator_id": 5},
                )
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json()["updated"] == 3


# ── Conversations analytics segment filter ───────────────────────────────


class TestConversationsAnalyticsSegmentFilter:
    @pytest.mark.asyncio
    async def test_analytics_without_segment_passes_no_user_ids(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with patch(
            "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
            new_callable=AsyncMock,
            return_value={"items": [], "pagination": {"page": 1, "page_size": 25, "total": 0, "total_pages": 0}},
        ) as mock_analytics:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        call_kwargs = mock_analytics.call_args[1]
        assert call_kwargs.get("user_ids") is None

    @pytest.mark.asyncio
    async def test_analytics_with_segment_passes_resolved_user_ids(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        members = [{"id": 10}, {"id": 20}]

        with (
            patch(_R_ANALYTICS, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=FAKE_SEGMENT),
            patch(_GSM_ANALYTICS, new_callable=AsyncMock, return_value=members),
            patch(
                "chatbotv2.dashboard.routes.analytics.get_conversations_analytics",
                new_callable=AsyncMock,
                return_value={"items": [], "pagination": {"page": 1, "page_size": 25, "total": 0, "total_pages": 0}},
            ) as mock_analytics,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations?segment_id=1")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        call_kwargs = mock_analytics.call_args[1]
        assert call_kwargs["user_ids"] == [10, 20]

    @pytest.mark.asyncio
    async def test_analytics_with_empty_segment_returns_empty(self):
        from httpx import ASGITransport, AsyncClient
        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}

        with (
            patch(_R_ANALYTICS, new_callable=AsyncMock, return_value=_make_creator_ctx()),
            patch("db.segments.get_segment", new_callable=AsyncMock, return_value=FAKE_SEGMENT),
            patch(_GSM_ANALYTICS, new_callable=AsyncMock, return_value=[]),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/analytics/conversations?segment_id=1")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["pagination"]["total"] == 0


# ── Schema contract tests ───────────────────────────────────────────────


class TestSchemaContracts:
    def test_bulk_assign_accepts_segment_id(self):
        from chatbotv2.dashboard.schemas import BulkAssignRequest
        req = BulkAssignRequest(segment_id=5, assigned_operator_id=3)
        assert req.segment_id == 5
        assert req.user_ids == []

    def test_bulk_tag_accepts_segment_id(self):
        from chatbotv2.dashboard.schemas import BulkTagRequest
        req = BulkTagRequest(segment_id=5, tag_id=1)
        assert req.segment_id == 5
        assert req.user_ids == []

    def test_bulk_attention_accepts_segment_id(self):
        from chatbotv2.dashboard.schemas import BulkAttentionRequest
        req = BulkAttentionRequest(segment_id=5, status="new")
        assert req.segment_id == 5
        assert req.user_ids == []

    def test_bulk_assign_both_user_ids_and_segment(self):
        from chatbotv2.dashboard.schemas import BulkAssignRequest
        req = BulkAssignRequest(user_ids=[1, 2], segment_id=5, assigned_operator_id=3)
        assert req.user_ids == [1, 2]
        assert req.segment_id == 5
