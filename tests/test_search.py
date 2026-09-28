"""Tests for Phase 2.7 Conversation Search / Full-Text Search."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _ready_creator_ctx(creator_id=100):
    """READY single-creator context (M7 B6: message search is creator-scoped)."""
    from unittest.mock import MagicMock

    from commerce.single_creator import SingleCreatorStatus

    ctx = MagicMock()
    ctx.status = SingleCreatorStatus.READY
    ctx.creator_id = creator_id
    return ctx


_R_SEARCH = "chatbotv2.dashboard.routes.search.resolve_single_application_creator"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP V — API: /api/search
# ═══════════════════════════════════════════════════════════════════════════════


class TestSearchAPI:
    """Tests for GET /api/search."""

    def _mock_search_result(self):
        return {
            "items": [
                {
                    "id": 1,
                    "user_id": 1001,
                    "username": "alice",
                    "first_name": "Alice",
                    "content": "Hello, I need help with my order",
                    "direction": "inbound",
                    "created_at": "2026-08-17T10:00:00",
                    "similarity": 0.85,
                }
            ],
            "pagination": {
                "page": 1,
                "page_size": 25,
                "total": 1,
                "total_pages": 1,
            },
        }

    @pytest.mark.asyncio
    async def test_search_requires_auth(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides.pop(require_auth, None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/search?q=test")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_search_min_length_validation(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/search?q=a")
        app.dependency_overrides.clear()
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_search_invalid_scope(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/search?q=test&scope=invalid")
        app.dependency_overrides.clear()
        assert resp.status_code == 400
        assert "Invalid scope" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_search_invalid_direction(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/search?q=test&direction=sideways")
        app.dependency_overrides.clear()
        assert resp.status_code == 400
        assert "direction must be" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_search_invalid_date_format(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/search?q=test&date_from=not-a-date")
        app.dependency_overrides.clear()
        assert resp.status_code == 400
        assert "Invalid date_from" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_search_messages_scope(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(_R_SEARCH, new_callable=AsyncMock, return_value=_ready_creator_ctx()),
            patch(
                "chatbotv2.dashboard.routes.search.search_messages",
                new_callable=AsyncMock,
                return_value=self._mock_search_result(),
            ) as mock_search,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/search?q=order&scope=messages")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "messages"
        assert len(data["items"]) == 1
        assert data["items"][0]["type"] == "message"
        mock_search.assert_called_once()
        # M7 (B6): creator scoping is threaded into message search.
        assert mock_search.call_args.kwargs.get("creator_id") == 100

    @pytest.mark.asyncio
    async def test_search_notes_scope(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        note_result = {
            "items": [
                {
                    "id": 1,
                    "user_id": 1001,
                    "content": "Follow up on pricing",
                    "created_by": "admin",
                    "created_at": "2026-08-17T09:00:00",
                    "similarity": 0.72,
                }
            ],
            "pagination": {"page": 1, "page_size": 25, "total": 1, "total_pages": 1},
        }
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.search.search_conversation_notes",
            new_callable=AsyncMock,
            return_value=note_result,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/search?q=pricing&scope=notes")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "notes"
        assert data["items"][0]["type"] == "note"

    @pytest.mark.asyncio
    async def test_search_users_scope(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        user_result = {
            "items": [
                {
                    "id": 1001,
                    "username": "alice",
                    "first_name": "Alice",
                    "last_seen": "2026-08-17T10:00:00",
                    "message_count": 15,
                    "similarity": 0.9,
                }
            ],
            "pagination": {"page": 1, "page_size": 25, "total": 1, "total_pages": 1},
        }
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with patch(
            "chatbotv2.dashboard.routes.search.search_users",
            new_callable=AsyncMock,
            return_value=user_result,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/search?q=alice&scope=users")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "users"
        assert data["items"][0]["type"] == "user"

    @pytest.mark.asyncio
    async def test_search_all_scope_combines_results(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        msg_result = {
            "items": [
                {
                    "id": 1,
                    "user_id": 1001,
                    "username": "alice",
                    "first_name": "Alice",
                    "content": "Hello",
                    "direction": "inbound",
                    "created_at": "2026-08-17T10:00:00",
                    "similarity": 0.8,
                }
            ],
            "pagination": {"page": 1, "page_size": 10, "total": 1, "total_pages": 1},
        }
        note_result = {
            "items": [
                {
                    "id": 1,
                    "user_id": 1001,
                    "content": "Note about alice",
                    "created_by": "admin",
                    "created_at": "2026-08-17T09:00:00",
                    "similarity": 0.7,
                }
            ],
            "pagination": {"page": 1, "page_size": 10, "total": 1, "total_pages": 1},
        }
        user_result = {
            "items": [
                {
                    "id": 1001,
                    "username": "alice",
                    "first_name": "Alice",
                    "last_seen": "2026-08-17T10:00:00",
                    "message_count": 15,
                    "similarity": 0.9,
                }
            ],
            "pagination": {"page": 1, "page_size": 10, "total": 1, "total_pages": 1},
        }
        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(_R_SEARCH, new_callable=AsyncMock, return_value=_ready_creator_ctx()),
            patch(
                "chatbotv2.dashboard.routes.search.search_messages",
                new_callable=AsyncMock,
                return_value=msg_result,
            ),
            patch(
                "chatbotv2.dashboard.routes.search.search_conversation_notes",
                new_callable=AsyncMock,
                return_value=note_result,
            ),
            patch(
                "chatbotv2.dashboard.routes.search.search_users",
                new_callable=AsyncMock,
                return_value=user_result,
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/search?q=alice&scope=all")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3
        types = {item["type"] for item in data["items"]}
        assert types == {"message", "note", "user"}

    @pytest.mark.asyncio
    async def test_search_passes_user_id_filter(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(_R_SEARCH, new_callable=AsyncMock, return_value=_ready_creator_ctx()),
            patch(
                "chatbotv2.dashboard.routes.search.search_messages",
                new_callable=AsyncMock,
                return_value={
                    "items": [],
                    "pagination": {"page": 1, "page_size": 25, "total": 0, "total_pages": 0},
                },
            ) as mock_search,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get("/api/search?q=test&user_id=42")
        app.dependency_overrides.clear()
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["user_id"] == 42

    @pytest.mark.asyncio
    async def test_search_passes_direction_filter(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(_R_SEARCH, new_callable=AsyncMock, return_value=_ready_creator_ctx()),
            patch(
                "chatbotv2.dashboard.routes.search.search_messages",
                new_callable=AsyncMock,
                return_value={
                    "items": [],
                    "pagination": {"page": 1, "page_size": 25, "total": 0, "total_pages": 0},
                },
            ) as mock_search,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.get("/api/search?q=test&direction=inbound")
        app.dependency_overrides.clear()
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["direction"] == "inbound"

    @pytest.mark.asyncio
    async def test_search_handles_db_error(self):
        from httpx import ASGITransport, AsyncClient

        from chatbotv2.dashboard.app import app
        from chatbotv2.dashboard.auth import require_auth

        app.dependency_overrides[require_auth] = lambda: {"username": "admin"}
        with (
            patch(_R_SEARCH, new_callable=AsyncMock, return_value=_ready_creator_ctx()),
            patch(
                "chatbotv2.dashboard.routes.search.search_messages",
                new_callable=AsyncMock,
                side_effect=Exception("DB connection failed"),
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/search?q=test")
        app.dependency_overrides.clear()
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Search computation failed"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP W — DB Functions: search_messages
# ═══════════════════════════════════════════════════════════════════════════════


class TestSearchMessages:
    """Tests for search_messages DB function."""

    def _mock_pool(self):
        mock_conn = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_ctx)
        return mock_pool, mock_conn

    @pytest.mark.asyncio
    async def test_search_messages_basic(self):
        from db.postgres import search_messages

        mock_pool, mock_conn = self._mock_pool()
        mock_conn.fetchrow.return_value = {"cnt": 1}
        mock_conn.fetch.return_value = [
            {
                "id": 1,
                "user_id": 1001,
                "username": "alice",
                "first_name": "Alice",
                "content": "Hello world",
                "direction": "inbound",
                "created_at": datetime(2026, 8, 17, tzinfo=UTC),
                "sim": 0.85,
            }
        ]
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await search_messages("hello")
        assert result["pagination"]["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["content"] == "Hello world"
        assert result["items"][0]["similarity"] == 0.85

    @pytest.mark.asyncio
    async def test_search_messages_with_user_id_filter(self):
        from db.postgres import search_messages

        mock_pool, mock_conn = self._mock_pool()
        mock_conn.fetchrow.return_value = {"cnt": 0}
        mock_conn.fetch.return_value = []
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await search_messages("hello", user_id=42)
        assert result["pagination"]["total"] == 0
        sql = mock_conn.fetchrow.call_args[0][0]
        assert "m.user_id = $2" in sql

    @pytest.mark.asyncio
    async def test_search_messages_with_direction_filter(self):
        from db.postgres import search_messages

        mock_pool, mock_conn = self._mock_pool()
        mock_conn.fetchrow.return_value = {"cnt": 0}
        mock_conn.fetch.return_value = []
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await search_messages("hello", direction="outbound")
        sql = mock_conn.fetchrow.call_args[0][0]
        assert "m.direction = $" in sql

    @pytest.mark.asyncio
    async def test_search_messages_with_date_filters(self):
        from db.postgres import search_messages

        mock_pool, mock_conn = self._mock_pool()
        mock_conn.fetchrow.return_value = {"cnt": 0}
        mock_conn.fetch.return_value = []
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await search_messages("hello", date_from="2026-08-01", date_to="2026-08-17")
        sql = mock_conn.fetchrow.call_args[0][0]
        assert "m.created_at >=" in sql
        assert "m.created_at <" in sql

    @pytest.mark.asyncio
    async def test_search_messages_pagination(self):
        from db.postgres import search_messages

        mock_pool, mock_conn = self._mock_pool()
        mock_conn.fetchrow.return_value = {"cnt": 50}
        mock_conn.fetch.return_value = []
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await search_messages("hello", page=2, page_size=10)
        assert result["pagination"]["page"] == 2
        assert result["pagination"]["page_size"] == 10
        assert result["pagination"]["total"] == 50
        assert result["pagination"]["total_pages"] == 5

    @pytest.mark.asyncio
    async def test_search_messages_empty_result(self):
        from db.postgres import search_messages

        mock_pool, mock_conn = self._mock_pool()
        mock_conn.fetchrow.return_value = {"cnt": 0}
        mock_conn.fetch.return_value = []
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await search_messages("nonexistent")
        assert result["items"] == []
        assert result["pagination"]["total"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP X — DB Functions: search_conversation_notes
# ═══════════════════════════════════════════════════════════════════════════════


class TestSearchConversationNotes:
    """Tests for search_conversation_notes DB function."""

    def _mock_pool(self):
        mock_conn = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_ctx)
        return mock_pool, mock_conn

    @pytest.mark.asyncio
    async def test_search_notes_basic(self):
        from db.postgres import search_conversation_notes

        mock_pool, mock_conn = self._mock_pool()
        mock_conn.fetchrow.return_value = {"cnt": 1}
        mock_conn.fetch.return_value = [
            {
                "id": 1,
                "user_id": 1001,
                "content": "Follow up on pricing",
                "created_by": "admin",
                "created_at": datetime(2026, 8, 17, tzinfo=UTC),
                "sim": 0.72,
            }
        ]
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await search_conversation_notes("pricing")
        assert result["pagination"]["total"] == 1
        assert result["items"][0]["content"] == "Follow up on pricing"
        assert result["items"][0]["created_by"] == "admin"

    @pytest.mark.asyncio
    async def test_search_notes_with_user_id_filter(self):
        from db.postgres import search_conversation_notes

        mock_pool, mock_conn = self._mock_pool()
        mock_conn.fetchrow.return_value = {"cnt": 0}
        mock_conn.fetch.return_value = []
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            await search_conversation_notes("pricing", user_id=42)
        sql = mock_conn.fetchrow.call_args[0][0]
        assert "n.user_id = $2" in sql

    @pytest.mark.asyncio
    async def test_search_notes_pagination(self):
        from db.postgres import search_conversation_notes

        mock_pool, mock_conn = self._mock_pool()
        mock_conn.fetchrow.return_value = {"cnt": 10}
        mock_conn.fetch.return_value = []
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await search_conversation_notes("test", page=1, page_size=5)
        assert result["pagination"]["total"] == 10
        assert result["pagination"]["total_pages"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP Y — DB Functions: search_users
# ═══════════════════════════════════════════════════════════════════════════════


class TestSearchUsers:
    """Tests for search_users DB function."""

    def _mock_pool(self):
        mock_conn = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_ctx)
        return mock_pool, mock_conn

    @pytest.mark.asyncio
    async def test_search_users_basic(self):
        from db.postgres import search_users

        mock_pool, mock_conn = self._mock_pool()
        mock_conn.fetchrow.return_value = {"cnt": 1}
        mock_conn.fetch.return_value = [
            {
                "id": 1001,
                "username": "alice",
                "first_name": "Alice",
                "last_seen": datetime(2026, 8, 17, tzinfo=UTC),
                "message_count": 15,
                "sim": 0.9,
            }
        ]
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await search_users("alice")
        assert result["pagination"]["total"] == 1
        assert result["items"][0]["username"] == "alice"
        assert result["items"][0]["similarity"] == 0.9

    @pytest.mark.asyncio
    async def test_search_users_empty_result(self):
        from db.postgres import search_users

        mock_pool, mock_conn = self._mock_pool()
        mock_conn.fetchrow.return_value = {"cnt": 0}
        mock_conn.fetch.return_value = []
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await search_users("nonexistent")
        assert result["items"] == []
        assert result["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_search_users_pagination(self):
        from db.postgres import search_users

        mock_pool, mock_conn = self._mock_pool()
        mock_conn.fetchrow.return_value = {"cnt": 20}
        mock_conn.fetch.return_value = []
        with patch("db.postgres.get_pool", new_callable=AsyncMock, return_value=mock_pool):
            result = await search_users("a", page=2, page_size=10)
        assert result["pagination"]["page"] == 2
        assert result["pagination"]["total"] == 20
        assert result["pagination"]["total_pages"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP Z — Frontend Contracts
# ═══════════════════════════════════════════════════════════════════════════════


class TestSearchFrontend:
    """Tests for analytics.html search UI contracts."""

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_search_section_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "search-section" in content

    def test_search_input_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "searchQuery" in content
        assert "search-input" in content

    def test_search_scope_dropdown_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "searchScope" in content

    def test_search_results_container_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "searchResults" in content
        assert "search-result-item" in content

    def test_search_result_type_badges_exist(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "search-result-type" in content
        assert "message" in content
        assert "note" in content
        assert "user" in content

    def test_search_loading_state_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "searchLoading" in content

    def test_search_error_state_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "searchError" in content

    def test_perform_search_function_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "performSearch" in content

    def test_clear_search_function_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "clearSearch" in content

    def test_search_debounce_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "@input.debounce" in content

    def test_search_result_click_handler_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "handleSearchResultClick" in content

    def test_search_api_endpoint_referenced(self):
        api_content = self._read("chatbotv2/dashboard/static/js/analytics-api.js")
        assert "/api/search" in api_content

    def test_search_similarity_display_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "similarity" in content
        assert "match" in content

    def test_search_empty_state_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "No results found" in content

    def test_search_clear_button_exists(self):
        content = self._read("chatbotv2/dashboard/templates/analytics.html")
        assert "Clear" in content
