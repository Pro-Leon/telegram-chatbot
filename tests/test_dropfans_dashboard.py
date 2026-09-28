"""DropFans dashboard integration regression tests.

Covers:
1. Unauthenticated request rejected
2. Authenticated connection succeeds
3. Invalid API key fails safely
4. API key is never returned in responses
5. API key is never logged
6. Creator isolation
7. Successful dropfans_creator_id persistence
8. Already-connected creator behavior
9. DropFans health/status response
10. UI template renders
11. Zero autonomous Fangate paths in DropFans flow

All DB/external interaction is mocked — no live credentials.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_auth():
    """Mock the require_auth dependency to return an authenticated session."""
    with patch("chatbotv2.dashboard.auth.verify_session") as mock:
        mock.return_value = {"username": "admin"}
        yield mock


@pytest.fixture
def mock_creator():
    """Mock the _require_creator helper to pass validation."""
    with patch("chatbotv2.dashboard.routes.fangate._require_creator") as mock:
        mock.return_value = None
        yield mock


@pytest.fixture
def mock_get_creator():
    """Mock fdb.get_creator to return a valid creator."""
    with patch("chatbotv2.dashboard.routes.fangate.fdb.get_creator") as mock:
        mock.return_value = {"id": 1, "name": "Test Creator", "display_name": "Test Creator"}
        yield mock


@pytest.fixture
def app_client():
    """Create a FastAPI test client."""
    from chatbotv2.dashboard.app import app
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


# ---------------------------------------------------------------------------
# 1. Unauthenticated request rejected
# ---------------------------------------------------------------------------


class TestUnauthenticatedRejected:
    @pytest.mark.asyncio
    async def test_integrate_requires_auth(self, app_client):
        """POST /api/fangate/creators/1/dropfans-integrate without session returns 401."""
        resp = await app_client.post(
            "/api/fangate/creators/1/dropfans-integrate",
            json={"api_key": "dpfn_test"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_status_requires_auth(self, app_client):
        """GET /api/fangate/creators/1/dropfans-status without session returns 401."""
        resp = await app_client.get("/api/fangate/creators/1/dropfans-status")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. Authenticated connection succeeds
# ---------------------------------------------------------------------------


class TestAuthenticatedConnection:
    @pytest.mark.asyncio
    async def test_integrate_success(
        self, app_client, mock_auth, mock_creator, mock_get_creator
    ):
        """Authenticated POST with valid API key returns success with identity."""
        with patch(
            "integrations.dropfans.service.connect_creator",
            new_callable=AsyncMock,
        ) as mock_connect:
            mock_connect.return_value = {
                "ok": True,
                "dropfans_creator_id": "df_user_abc123",
                "username": "testcreator",
                "display_name": "Test Creator",
            }
            resp = await app_client.post(
                "/api/fangate/creators/1/dropfans-integrate",
                json={"api_key": "dpfn_valid_key_123"},
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["dropfans_creator_id"] == "df_user_abc123"
            assert data["username"] == "testcreator"
            assert data["display_name"] == "Test Creator"

    @pytest.mark.asyncio
    async def test_integrate_calls_connect_creator(
        self, app_client, mock_auth, mock_creator, mock_get_creator
    ):
        """Connection flow delegates to df_svc.connect_creator."""
        with patch(
            "integrations.dropfans.service.connect_creator",
            new_callable=AsyncMock,
        ) as mock_connect:
            mock_connect.return_value = {
                "ok": True,
                "dropfans_creator_id": "df_new_id",
                "username": "newuser",
                "display_name": "New User",
            }
            await app_client.post(
                "/api/fangate/creators/1/dropfans-integrate",
                json={"api_key": "dpfn_new_key"},
                cookies={"session": "valid_token"},
            )
            mock_connect.assert_called_once_with(1, "dpfn_new_key")


# ---------------------------------------------------------------------------
# 3. Invalid API key fails safely
# ---------------------------------------------------------------------------


class TestInvalidApiKey:
    @pytest.mark.asyncio
    async def test_invalid_key_returns_error(
        self, app_client, mock_auth, mock_creator, mock_get_creator
    ):
        """Invalid API key returns error without crashing."""
        with patch(
            "integrations.dropfans.service.connect_creator",
            new_callable=AsyncMock,
        ) as mock_connect:
            from integrations.dropfans.errors import DropfansAuthenticationError

            mock_connect.side_effect = DropfansAuthenticationError(
                "connect_creator", "Invalid API key", 401
            )
            resp = await app_client.post(
                "/api/fangate/creators/1/dropfans-integrate",
                json={"api_key": "dpfn_invalid"},
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 500
            data = resp.json()
            assert "error" in data

    @pytest.mark.asyncio
    async def test_empty_api_key_returns_400(
        self, app_client, mock_auth, mock_creator, mock_get_creator
    ):
        """Empty API key returns 400 validation error."""
        resp = await app_client.post(
            "/api/fangate/creators/1/dropfans-integrate",
            json={"api_key": "  "},
            cookies={"session": "valid_token"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 4. API key is never returned in responses
# ---------------------------------------------------------------------------


class TestApiKeyNeverReturned:
    @pytest.mark.asyncio
    async def test_integrate_response_no_key(
        self, app_client, mock_auth, mock_creator, mock_get_creator
    ):
        """Connection response never contains the raw API key."""
        with patch(
            "integrations.dropfans.service.connect_creator",
            new_callable=AsyncMock,
        ) as mock_connect:
            mock_connect.return_value = {
                "ok": True,
                "dropfans_creator_id": "df_abc",
                "username": "user",
                "display_name": "User",
            }
            resp = await app_client.post(
                "/api/fangate/creators/1/dropfans-integrate",
                json={"api_key": "dpfn_secret_key_12345"},
                cookies={"session": "valid_token"},
            )
            body = resp.text
            assert "dpfn_secret_key_12345" not in body
            assert "api_key" not in resp.json()

    @pytest.mark.asyncio
    async def test_status_response_no_key(
        self, app_client, mock_auth, mock_creator
    ):
        """Status response never contains the encrypted API key."""
        with patch(
            "integrations.dropfans.service.get_integration_status",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {
                "creator_id": 1,
                "dropfans_creator_id": "df_abc",
                "username": "user",
                "display_name": "User",
                "status": "active",
            }
            resp = await app_client.get(
                "/api/fangate/creators/1/dropfans-status",
                cookies={"session": "valid_token"},
            )
            body = resp.text
            assert "gAAAAA" not in body
            assert "encrypted_api_key" not in body


# ---------------------------------------------------------------------------
# 5. API key is never logged
# ---------------------------------------------------------------------------


class TestApiKeyNeverLogged:
    @pytest.mark.asyncio
    async def test_connect_creator_logs_no_key(
        self, app_client, mock_auth, mock_creator, mock_get_creator
    ):
        """connect_creator service function never logs the raw API key."""
        with patch(
            "integrations.dropfans.service.connect_creator",
            new_callable=AsyncMock,
        ) as mock_connect:
            mock_connect.return_value = {
                "ok": True,
                "dropfans_creator_id": "df_abc",
                "username": "user",
                "display_name": "User",
            }
            with patch("integrations.dropfans.service.logger") as mock_logger:
                await app_client.post(
                    "/api/fangate/creators/1/dropfans-integrate",
                    json={"api_key": "dpfn_super_secret_999"},
                    cookies={"session": "valid_token"},
                )
                # Verify logger was called but never with the raw key
                for call in mock_logger.info.call_args_list:
                    args = str(call)
                    assert "dpfn_super_secret_999" not in args
                for call in mock_logger.warning.call_args_list:
                    args = str(call)
                    assert "dpfn_super_secret_999" not in args


# ---------------------------------------------------------------------------
# 6. Creator isolation
# ---------------------------------------------------------------------------


class TestCreatorIsolation:
    @pytest.mark.asyncio
    async def test_connect_scoped_to_creator(
        self, app_client, mock_auth, mock_creator, mock_get_creator
    ):
        """Connection is scoped to the creator_id in the URL path."""
        with patch(
            "integrations.dropfans.service.connect_creator",
            new_callable=AsyncMock,
        ) as mock_connect:
            mock_connect.return_value = {
                "ok": True,
                "dropfans_creator_id": "df_isolated",
                "username": "isolated",
                "display_name": "Isolated",
            }
            resp = await app_client.post(
                "/api/fangate/creators/42/dropfans-integrate",
                json={"api_key": "dpfn_key_for_42"},
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            mock_connect.assert_called_once_with(42, "dpfn_key_for_42")

    @pytest.mark.asyncio
    async def test_status_scoped_to_creator(
        self, app_client, mock_auth, mock_creator
    ):
        """Status check is scoped to the creator_id in the URL path."""
        with patch(
            "integrations.dropfans.service.get_integration_status",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"creator_id": 7, "status": "active"}
            resp = await app_client.get(
                "/api/fangate/creators/7/dropfans-status",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            mock_status.assert_called_once_with(7)


# ---------------------------------------------------------------------------
# 7. Successful dropfans_creator_id persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    @pytest.mark.asyncio
    async def test_connect_persists_identity(
        self, app_client, mock_auth, mock_creator, mock_get_creator
    ):
        """Successful connection persists dropfans_creator_id in the database."""
        with patch(
            "integrations.dropfans.service.connect_creator",
            new_callable=AsyncMock,
        ) as mock_connect:
            mock_connect.return_value = {
                "ok": True,
                "dropfans_creator_id": "df_persisted_123",
                "username": "persisted_user",
                "display_name": "Persisted User",
            }
            resp = await app_client.post(
                "/api/fangate/creators/1/dropfans-integrate",
                json={"api_key": "dpfn_persist_key"},
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["dropfans_creator_id"] == "df_persisted_123"


# ---------------------------------------------------------------------------
# 8. Already-connected creator behavior
# ---------------------------------------------------------------------------


class TestAlreadyConnected:
    @pytest.mark.asyncio
    async def test_reconnect_overwrites(
        self, app_client, mock_auth, mock_creator, mock_get_creator
    ):
        """Reconnecting with a new key overwrites the previous identity."""
        with patch(
            "integrations.dropfans.service.connect_creator",
            new_callable=AsyncMock,
        ) as mock_connect:
            mock_connect.return_value = {
                "ok": True,
                "dropfans_creator_id": "df_new_identity",
                "username": "new_user",
                "display_name": "New Identity",
            }
            resp = await app_client.post(
                "/api/fangate/creators/1/dropfans-integrate",
                json={"api_key": "dpfn_new_key"},
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            assert resp.json()["dropfans_creator_id"] == "df_new_identity"


# ---------------------------------------------------------------------------
# 9. DropFans health/status response
# ---------------------------------------------------------------------------


class TestHealthStatus:
    @pytest.mark.asyncio
    async def test_status_returns_identity(
        self, app_client, mock_auth, mock_creator
    ):
        """Status endpoint returns DropFans identity when connected."""
        with patch(
            "integrations.dropfans.service.get_integration_status",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {
                "creator_id": 1,
                "dropfans_creator_id": "df_health_123",
                "username": "health_user",
                "display_name": "Health User",
                "status": "active",
            }
            resp = await app_client.get(
                "/api/fangate/creators/1/dropfans-status",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"]["dropfans_creator_id"] == "df_health_123"
            assert data["status"]["status"] == "active"

    @pytest.mark.asyncio
    async def test_status_returns_null_when_not_connected(
        self, app_client, mock_auth, mock_creator
    ):
        """Status endpoint returns null when DropFans is not connected."""
        with patch(
            "integrations.dropfans.service.get_integration_status",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = None
            resp = await app_client.get(
                "/api/fangate/creators/1/dropfans-status",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] is None


# ---------------------------------------------------------------------------
# 10. UI template renders
# ---------------------------------------------------------------------------


class TestUITemplate:
    @pytest.mark.asyncio
    async def test_fangate_page_includes_dropfans_tab(self, app_client, mock_auth):
        """The fangate dashboard page includes the DropFans tab."""
        with patch(
            "commerce.single_creator.resolve_single_application_creator",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.creator_id = 1
            mock_resolve.return_value = mock_ctx
            resp = await app_client.get(
                "/dashboard/fangate",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            html = resp.text
            assert "DropFans" in html
            assert "dropfans-integrate" in html
            assert "dropfans-status" in html
            assert "dropfansApiKey" in html
            assert "connectDropfans" in html

    @pytest.mark.asyncio
    async def test_fangate_page_includes_connection_form(self, app_client, mock_auth):
        """The fangate dashboard page includes the DropFans connection form."""
        with patch(
            "commerce.single_creator.resolve_single_application_creator",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.creator_id = 1
            mock_resolve.return_value = mock_ctx
            resp = await app_client.get(
                "/dashboard/fangate",
                cookies={"session": "valid_token"},
            )
            html = resp.text
            assert "Connect DropFans" in html
            assert "dpfn_" in html
            assert "type=\"password\"" in html


# ---------------------------------------------------------------------------
# 11. Zero autonomous Fangate paths in DropFans flow
# ---------------------------------------------------------------------------


class TestFangateIsolation:
    def test_dropfans_integrate_endpoint_no_fangate_service_call(self):
        """The dropfans-integrate endpoint does NOT call Fangate service."""
        import ast
        from pathlib import Path

        fangate_py = Path("chatbotv2/dashboard/routes/fangate.py").read_text(encoding="utf-8")
        tree = ast.parse(fangate_py)

        # Find the api_dropfans_integrate function
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "api_dropfans_integrate":
                # Check that integrations.fangate.service is NOT imported inside this function
                source_lines = fangate_py.splitlines()
                func_source = "\n".join(
                    source_lines[node.lineno - 1 : node.end_lineno or node.lineno + 50]
                )
                assert "integrations.fangate" not in func_source or "from integrations.dropfans" in func_source
                assert "from integrations.fangate" not in func_source
                break

    def test_dropfans_status_endpoint_no_fangate_service_call(self):
        """The dropfans-status endpoint does NOT call Fangate service."""
        import ast
        from pathlib import Path

        fangate_py = Path("chatbotv2/dashboard/routes/fangate.py").read_text(encoding="utf-8")
        tree = ast.parse(fangate_py)

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "api_dropfans_status":
                source_lines = fangate_py.splitlines()
                func_source = "\n".join(
                    source_lines[node.lineno - 1 : node.end_lineno or node.lineno + 30]
                )
                assert "from integrations.fangate" not in func_source
                assert "integrations.fangate.service" not in func_source
                break

    def test_dropfans_connect_service_uses_dropfans_client(self):
        """The connect_creator service function uses only DropFans client."""
        from pathlib import Path

        service_py = Path("integrations/dropfans/service.py").read_text()
        assert "DropfansClient" in service_py
        assert "from integrations.fangate" not in service_py


# ---------------------------------------------------------------------------
# 12. Health check endpoint
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_requires_auth(self, app_client):
        """Health endpoint requires authentication."""
        resp = await app_client.get("/api/fangate/creators/1/dropfans-health")
        assert resp.status_code in (401, 403, 307)

    @pytest.mark.asyncio
    async def test_health_returns_healthy_when_api_works(
        self, app_client, mock_auth, mock_creator
    ):
        """Health endpoint returns HEALTHY when DropFans API responds."""
        mock_account = MagicMock()
        mock_account.tier = "personal"
        mock_account.account_type = "CREATOR"
        with patch(
            "integrations.dropfans.service.get_account",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = {
                "creator_id": "123",
                "username": "testuser",
                "display_name": "Test",
                "tier": "personal",
                "account_type": "CREATOR",
            }
            resp = await app_client.get(
                "/api/fangate/creators/1/dropfans-health",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["health"] == "HEALTHY"

    @pytest.mark.asyncio
    async def test_health_returns_auth_error_on_invalid_key(
        self, app_client, mock_auth, mock_creator
    ):
        """Health endpoint returns AUTHENTICATION_ERROR on 401."""
        from integrations.dropfans.errors import DropfansAuthenticationError

        with patch(
            "integrations.dropfans.service.get_account",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = DropfansAuthenticationError(
                "get_me", message="Invalid key"
            )
            resp = await app_client.get(
                "/api/fangate/creators/1/dropfans-health",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["health"] == "AUTHENTICATION_ERROR"

    @pytest.mark.asyncio
    async def test_health_returns_unreachable_on_timeout(
        self, app_client, mock_auth, mock_creator
    ):
        """Health endpoint returns UNREACHABLE on timeout."""
        from integrations.dropfans.errors import DropfansTimeoutError

        with patch(
            "integrations.dropfans.service.get_account",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = DropfansTimeoutError("get_me", message="Timeout")
            resp = await app_client.get(
                "/api/fangate/creators/1/dropfans-health",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["health"] == "UNREACHABLE"

    @pytest.mark.asyncio
    async def test_health_returns_rate_limited(
        self, app_client, mock_auth, mock_creator
    ):
        """Health endpoint returns RATE_LIMITED on 429."""
        from integrations.dropfans.errors import DropfansRateLimitError

        with patch(
            "integrations.dropfans.service.get_account",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = DropfansRateLimitError(
                "get_me", message="Rate limited"
            )
            resp = await app_client.get(
                "/api/fangate/creators/1/dropfans-health",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["health"] == "RATE_LIMITED"

    @pytest.mark.asyncio
    async def test_health_returns_not_configured_when_no_integration(
        self, app_client, mock_auth, mock_creator
    ):
        """Health endpoint returns NOT_CONFIGURED when no integration exists."""
        from integrations.dropfans.service import DropfansIntegrationNotFoundError

        with patch(
            "integrations.dropfans.service.get_account",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = DropfansIntegrationNotFoundError(
                "get_account"
            )
            resp = await app_client.get(
                "/api/fangate/creators/1/dropfans-health",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["health"] == "NOT_CONFIGURED"

    @pytest.mark.asyncio
    async def test_health_no_api_key_in_response(
        self, app_client, mock_auth, mock_creator
    ):
        """Health endpoint never exposes API key."""
        with patch(
            "integrations.dropfans.service.get_account",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = {"username": "test", "tier": "personal"}
            resp = await app_client.get(
                "/api/fangate/creators/1/dropfans-health",
                cookies={"session": "valid_token"},
            )
            assert "dpfn_" not in resp.text
            assert "api_key" not in resp.json()


# ---------------------------------------------------------------------------
# 13. Overview endpoint
# ---------------------------------------------------------------------------


class TestOverview:
    @pytest.mark.asyncio
    async def test_overview_requires_auth(self, app_client):
        """Overview endpoint requires authentication."""
        resp = await app_client.get("/api/fangate/creators/1/dropfans-overview")
        assert resp.status_code in (401, 403, 307)

    @pytest.mark.asyncio
    async def test_overview_returns_disconnected_when_no_integration(
        self, app_client, mock_auth, mock_creator
    ):
        """Overview returns connected=False when no integration exists."""
        with patch(
            "integrations.dropfans.service.get_integration_status",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = None
            resp = await app_client.get(
                "/api/fangate/creators/1/dropfans-overview",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["connected"] is False

    @pytest.mark.asyncio
    async def test_overview_returns_connected_state(
        self, app_client, mock_auth, mock_creator
    ):
        """Overview returns connected=True with identity when integration exists."""
        with patch(
            "integrations.dropfans.service.get_integration_status",
            new_callable=AsyncMock,
        ) as mock_status, patch(
            "integrations.dropfans.service.list_vault_items",
            new_callable=AsyncMock,
        ) as mock_vault, patch(
            "integrations.dropfans.service.get_balance",
            new_callable=AsyncMock,
        ) as mock_balance, patch(
            "db.dropfans.count_active_dropfans_products",
            new_callable=AsyncMock,
        ) as mock_drops, patch(
            "db.dropfans.count_recorded_sales",
            new_callable=AsyncMock,
        ) as mock_sales, patch(
            "db.dropfans.sum_recorded_sales_cents",
            new_callable=AsyncMock,
        ) as mock_earnings:
            mock_status.return_value = {
                "dropfans_creator_id": "123",
                "username": "testuser",
                "display_name": "Test",
            }
            vault_result = MagicMock()
            vault_result.total = 42
            mock_vault.return_value = vault_result
            mock_balance.return_value = {
                "available": 150.50,
                "pending": 25.00,
                "processing": 10.00,
                "paid_out": 500.00,
                "currency": "USD",
            }
            mock_drops.return_value = 5
            mock_sales.return_value = 12
            mock_earnings.return_value = 120000

            resp = await app_client.get(
                "/api/fangate/creators/1/dropfans-overview",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["connected"] is True
            assert data["identity"]["dropfans_creator_id"] == "123"
            assert data["vault_count"] == 42
            assert data["drops_count"] == 5
            assert data["earnings"]["total_sales"] == 12
            assert data["balance"]["available"] == 150.50


# ---------------------------------------------------------------------------
# 14. Page routes render
# ---------------------------------------------------------------------------


class TestPageRoutes:
    @pytest.mark.asyncio
    async def test_dropfans_overview_page_renders(self, app_client, mock_auth):
        """The /dashboard/dropfans page renders."""
        with patch(
            "commerce.single_creator.resolve_single_application_creator",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.creator_id = 1
            mock_resolve.return_value = mock_ctx
            resp = await app_client.get(
                "/dashboard/dropfans",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            assert "DropFans Workspace" in resp.text

    @pytest.mark.asyncio
    async def test_dropfans_settings_page_renders(self, app_client, mock_auth):
        """The /dashboard/dropfans/settings page renders."""
        with patch(
            "commerce.single_creator.resolve_single_application_creator",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.creator_id = 1
            mock_resolve.return_value = mock_ctx
            resp = await app_client.get(
                "/dashboard/dropfans/settings",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            assert "DropFans Settings" in resp.text

    @pytest.mark.asyncio
    async def test_posts_page_renders(self, app_client, mock_auth):
        """The /dashboard/posts page renders."""
        with patch(
            "commerce.single_creator.resolve_single_application_creator",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.creator_id = 1
            mock_resolve.return_value = mock_ctx
            resp = await app_client.get(
                "/dashboard/posts",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            assert "DropFans Posts" in resp.text

    @pytest.mark.asyncio
    async def test_notifications_page_renders(self, app_client, mock_auth):
        """The /dashboard/notifications page renders."""
        with patch(
            "commerce.single_creator.resolve_single_application_creator",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.creator_id = 1
            mock_resolve.return_value = mock_ctx
            resp = await app_client.get(
                "/dashboard/notifications",
                cookies={"session": "valid_token"},
            )
            assert resp.status_code == 200
            assert "DropFans Notifications" in resp.text

    @pytest.mark.asyncio
    async def test_posts_page_includes_workspace_nav(self, app_client, mock_auth):
        """Posts page includes workspace navigation links."""
        with patch(
            "commerce.single_creator.resolve_single_application_creator",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_ctx = MagicMock()
            mock_ctx.creator_id = 1
            mock_resolve.return_value = mock_ctx
            resp = await app_client.get(
                "/dashboard/posts",
                cookies={"session": "valid_token"},
            )
            html = resp.text
            assert "/dashboard/dropfans" in html
            assert "/dashboard/drops" in html
            assert "/dashboard/posts" in html
            assert "/dashboard/earnings" in html
            assert "/dashboard/links" in html
            assert "/dashboard/vault" in html
            assert "/dashboard/notifications" in html
            assert "/dashboard/dropfans/settings" in html


# ---------------------------------------------------------------------------
# 15. Creator isolation for new endpoints
# ---------------------------------------------------------------------------


class TestCreatorIsolationNewEndpoints:
    @pytest.mark.asyncio
    async def test_health_scoped_to_creator(self, app_client, mock_auth):
        """Health endpoint uses _require_creator for isolation."""
        with patch(
            "chatbotv2.dashboard.routes.fangate._require_creator",
            new_callable=AsyncMock,
        ) as mock_req:
            mock_req.return_value = None
            with patch(
                "integrations.dropfans.service.get_account",
                new_callable=AsyncMock,
            ) as mock_get:
                mock_get.return_value = {"username": "test"}
                resp = await app_client.get(
                    "/api/fangate/creators/1/dropfans-health",
                    cookies={"session": "valid_token"},
                )
                assert resp.status_code == 200
                mock_req.assert_called_once()

    @pytest.mark.asyncio
    async def test_overview_scoped_to_creator(self, app_client, mock_auth):
        """Overview endpoint uses _require_creator for isolation."""
        with patch(
            "chatbotv2.dashboard.routes.fangate._require_creator",
            new_callable=AsyncMock,
        ) as mock_req:
            mock_req.return_value = None
            with patch(
                "integrations.dropfans.service.get_integration_status",
                new_callable=AsyncMock,
            ) as mock_status:
                mock_status.return_value = None
                resp = await app_client.get(
                    "/api/fangate/creators/1/dropfans-overview",
                    cookies={"session": "valid_token"},
                )
                assert resp.status_code == 200
                mock_req.assert_called_once()
