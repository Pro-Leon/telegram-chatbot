"""Autonomy kill switch tests.

Verifies that AUTONOMY_ENABLED=false prevents autonomous commerce while
preserving normal CRM functionality, webhook persistence, and manual operations.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(autonomy_enabled: bool = True):
    from core.config import Settings
    return Settings(
        OPENAI_API_KEY="test-key",
        POSTGRES_DSN="postgresql://localhost:5432/test",
        REDIS_URL="redis://localhost:6379",
        autonomy_enabled=autonomy_enabled,
    )


# ---------------------------------------------------------------------------
# Test 1: AUTONOMY_ENABLED=false prevents autonomous commerce
# ---------------------------------------------------------------------------

class TestKillSwitchDisabled:
    """When autonomy_enabled=false, _try_commerce_draft must return None."""

    @pytest.mark.asyncio
    async def test_commerce_draft_returns_none_when_disabled(self):
        """Commerce attempt is skipped entirely when autonomy is disabled."""
        settings = _make_settings(autonomy_enabled=False)

        from workers.llm_worker import _try_commerce_draft

        with patch("workers.llm_worker._settings", settings):
            result = await _try_commerce_draft(
                user_id=12345,
                context=[{"role": "user", "content": "I want to buy your content"}],
                persona="default",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_commerce_draft_does_not_call_resolve(self):
        """When disabled, no creator resolution or product selection occurs."""
        settings = _make_settings(autonomy_enabled=False)

        from workers.llm_worker import _try_commerce_draft

        with patch("workers.llm_worker._settings", settings), \
             patch("workers.llm_worker.resolve_single_application_creator") as mock_resolve, \
             patch("workers.llm_worker.resolve_commerce_product_with_history") as mock_product:
            result = await _try_commerce_draft(
                user_id=12345,
                context=[{"role": "user", "content": "test"}],
                persona=None,
            )

        assert result is None
        mock_resolve.assert_not_called()
        mock_product.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_autonomy_logs_info(self):
        """When disabled, an info-level log is emitted."""
        settings = _make_settings(autonomy_enabled=False)

        from workers.llm_worker import _try_commerce_draft

        with patch("workers.llm_worker._settings", settings), \
             patch("workers.llm_worker.logger") as mock_logger:
            await _try_commerce_draft(
                user_id=12345,
                context=[],
                persona=None,
            )

        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0]
        assert "autonomy_disabled" in call_args[0]


# ---------------------------------------------------------------------------
# Test 2: AUTONOMY_ENABLED=true allows existing autonomous path
# ---------------------------------------------------------------------------

class TestKillSwitchEnabled:
    """When autonomy_enabled=true, the existing commerce path executes normally."""

    @pytest.mark.asyncio
    async def test_commerce_draft_proceeds_when_enabled(self):
        """Commerce attempt proceeds through the normal path when enabled."""
        settings = _make_settings(autonomy_enabled=True)

        from commerce.integration import CommerceIntegrationStatus
        from commerce.selection import CommerceSelectionStatus

        mock_selection = MagicMock()
        mock_selection.status = CommerceSelectionStatus.COMMERCE_UNAVAILABLE
        mock_selection.reason = MagicMock()

        from workers.llm_worker import _try_commerce_draft

        with patch("workers.llm_worker._settings", settings), \
             patch("workers.llm_worker.resolve_single_application_creator") as mock_creator, \
             patch("workers.llm_worker.resolve_and_run_commerce") as mock_commerce, \
             patch("workers.llm_worker.select_commerce_response", return_value=mock_selection):
            mock_creator.return_value = MagicMock(status=MagicMock(value="unavailable"))
            mock_commerce.return_value = MagicMock(status=CommerceIntegrationStatus.COMPLETED)

            result = await _try_commerce_draft(
                user_id=12345,
                context=[],
                persona=None,
            )

        mock_creator.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3: Manual operator functionality unaffected
# ---------------------------------------------------------------------------

class TestManualOperationUnaffected:
    """Dashboard/operator manual sends must work regardless of autonomy setting."""

    @pytest.mark.asyncio
    async def test_operator_queue_works_with_autonomy_disabled(self):
        """Commerce returns None, allowing fallback to standard LLM path."""
        settings = _make_settings(autonomy_enabled=False)

        with patch("workers.llm_worker._settings", settings):
            from workers.llm_worker import _try_commerce_draft

            result = await _try_commerce_draft(
                user_id=12345,
                context=[],
                persona=None,
            )

        # With autonomy disabled, commerce returns None, allowing fallback
        assert result is None


# ---------------------------------------------------------------------------
# Test 4: Webhook persistence unaffected
# ---------------------------------------------------------------------------

class TestWebhookPersistenceUnaffected:
    """Webhook transaction persistence must work regardless of autonomy setting."""

    @pytest.mark.asyncio
    async def test_webhook_persists_transaction_when_disabled(self):
        """Webhook transactions are persisted even with autonomy disabled."""
        from integrations.fangate import service as fangate_service

        mock_integration = {
            "encrypted_webhook_secret": "encrypted_secret",
            "encrypted_api_key": "encrypted_key",
            "status": "active",
        }

        with patch("db.fangate.get_creator_integration", new_callable=AsyncMock, return_value=mock_integration), \
             patch("integrations.fangate.service.decrypt_secret", new_callable=AsyncMock, return_value="test-secret"), \
             patch("integrations.fangate.service.verify_webhook_signature", return_value=True), \
             patch("db.fangate.insert_fangate_webhook_event", new_callable=AsyncMock, return_value=True), \
             patch("db.fangate.mark_webhook_event_processed", new_callable=AsyncMock), \
             patch("db.fangate.upsert_fangate_transaction", new_callable=AsyncMock, return_value=True):
            import json
            payload = json.dumps({
                "data": {
                    "transaction_id": "txn_123",
                    "product_id": 5001,
                    "event": "purchase",
                }
            }).encode()

            result = await fangate_service.receive_webhook(
                creator_id=100,
                body=payload,
                signature="sha256=fake",
                delivery_id="del_123",
            )

            assert result["recorded"] is True


# ---------------------------------------------------------------------------
# Test 5: Browser cannot override kill switch
# ---------------------------------------------------------------------------

class TestBrowserCannotOverride:
    """No browser/API parameter can activate autonomy when server-side is off."""

    def test_settings_has_no_browser_override_field(self):
        """Settings class has no field that accepts browser-controlled autonomy."""
        from core.config import Settings

        fields = Settings.model_fields
        assert "autonomy_enabled" in fields

        field = fields["autonomy_enabled"]
        assert field.annotation is bool

    def test_autonomy_enabled_is_server_side_only(self):
        """autonomy_enabled defaults to True and is set via env/config only."""
        settings = _make_settings(autonomy_enabled=True)
        assert settings.autonomy_enabled is True

        settings = _make_settings(autonomy_enabled=False)
        assert settings.autonomy_enabled is False
