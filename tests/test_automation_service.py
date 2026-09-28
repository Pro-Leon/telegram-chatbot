"""Phase C: AutomationService tests.

Tests for automation/service.py — the central write authority.
Uses mocked DB and provider functions to test the orchestration logic.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from automation.service import (
    AutomationService,
    classify_provider_error,
    is_retryable_error,
)


# ── Error classification ───────────────────────────────────────────────────


class TestClassifyProviderError:
    """Test provider error classification."""

    def test_auth_error_401(self):
        error_class, message = classify_provider_error(Exception("401 Unauthorized"))
        assert error_class == "auth"

    def test_auth_error_403(self):
        error_class, message = classify_provider_error(Exception("403 Forbidden"))
        assert error_class == "auth"

    def test_rate_limit_error(self):
        error_class, message = classify_provider_error(Exception("429 Too Many Requests"))
        assert error_class == "rate_limit"

    def test_timeout_error(self):
        error_class, message = classify_provider_error(Exception("Connection timeout"))
        assert error_class == "timeout"

    def test_network_error(self):
        error_class, message = classify_provider_error(Exception("Network unreachable"))
        assert error_class == "network"

    def test_validation_error_400(self):
        error_class, message = classify_provider_error(Exception("400 Bad Request"))
        assert error_class == "validation"

    def test_validation_error_422(self):
        error_class, message = classify_provider_error(Exception("422 Unprocessable"))
        assert error_class == "validation"

    def test_not_found_error(self):
        error_class, message = classify_provider_error(Exception("404 Not Found"))
        assert error_class == "not_found"

    def test_server_error(self):
        error_class, message = classify_provider_error(Exception("500 Internal Server Error"))
        assert error_class == "provider"

    def test_unknown_error(self):
        error_class, message = classify_provider_error(Exception("Something weird"))
        assert error_class == "unknown"
        assert "Something weird" in message


class TestIsRetryableError:
    """Test retryable error classification."""

    def test_retryable(self):
        assert is_retryable_error("rate_limit") is True
        assert is_retryable_error("timeout") is True
        assert is_retryable_error("network") is True
        assert is_retryable_error("provider") is True

    def test_not_retryable(self):
        assert is_retryable_error("auth") is False
        assert is_retryable_error("validation") is False
        assert is_retryable_error("unknown") is False


# ── AutomationService.execute ─────────────────────────────────────────────


class TestAutomationServiceExecute:
    """Test AutomationService.execute method."""

    @pytest.mark.asyncio
    async def test_execute_creates_operation(self):
        """Execute creates an operation when kill switch is enabled."""
        with patch("automation.service._settings") as mock_settings:
            mock_settings.autonomy_enabled = True
            with patch("automation.service.adb") as mock_adb:
                # Return an operation without "status" key to indicate new creation
                mock_adb.create_operation = AsyncMock(return_value={"id": 1})

                service = AutomationService()
                result = await service.execute(
                    creator_id=123,
                    action="checkout_links",
                    target="user:456",
                    params={"amount": 500},
                    idempotency_key="test-key-123",
                )

                assert result["status"] == "created"
                assert result["operation_id"] == 1
                mock_adb.create_operation.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_blocks_kill_switch(self):
        """Execute blocks when kill switch is disabled."""
        with patch("automation.service._settings") as mock_settings:
            mock_settings.autonomy_enabled = False

            service = AutomationService()
            result = await service.execute(
                creator_id=123,
                action="checkout_links",
                target="user:456",
            )

            assert result["status"] == "cancelled"
            assert result["reason"] == "autonomy_disabled"

    @pytest.mark.asyncio
    async def test_execute_returns_existing_operation(self):
        """Execute returns existing operation when idempotency key matches."""
        with patch("automation.service._settings") as mock_settings:
            mock_settings.autonomy_enabled = True
            with patch("automation.service.adb") as mock_adb:
                mock_adb.create_operation = AsyncMock(
                    return_value={"id": 42, "status": "pending"}
                )

                service = AutomationService()
                result = await service.execute(
                    creator_id=123,
                    action="checkout_links",
                    target="user:456",
                    idempotency_key="existing-key",
                )

                assert result["status"] == "exists"
                assert result["existing_status"] == "pending"


# ── AutomationService.execute_provider_write ──────────────────────────────


class TestAutomationServiceExecuteProviderWrite:
    """Test AutomationService.execute_provider_write method."""

    @pytest.mark.asyncio
    async def test_execute_provider_write_success(self):
        """Provider write succeeds."""
        with patch("automation.service._settings") as mock_settings:
            mock_settings.autonomy_enabled = True
            with patch("automation.service.adb") as mock_adb:
                mock_adb.mark_succeeded = AsyncMock(return_value=True)

                service = AutomationService()
                provider_fn = AsyncMock(return_value={"checkout_url": "https://example.com"})

                result = await service.execute_provider_write(
                    operation_id=1,
                    creator_id=123,
                    provider_fn=provider_fn,
                )

                assert result["status"] == "succeeded"
                provider_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_provider_write_blocks_kill_switch(self):
        """Provider write blocks when kill switch is disabled."""
        with patch("automation.service._settings") as mock_settings:
            mock_settings.autonomy_enabled = False
            with patch("automation.service.adb") as mock_adb:
                mock_adb.mark_cancelled = AsyncMock(return_value=True)

                service = AutomationService()
                provider_fn = AsyncMock()

                result = await service.execute_provider_write(
                    operation_id=1,
                    creator_id=123,
                    provider_fn=provider_fn,
                )

                assert result["status"] == "cancelled"
                provider_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_provider_write_auth_error(self):
        """Provider write fails with auth error (not retryable)."""
        with patch("automation.service._settings") as mock_settings:
            mock_settings.autonomy_enabled = True
            with patch("automation.service.adb") as mock_adb:
                mock_adb.mark_failed = AsyncMock(return_value=True)
                mock_adb.get_operation = AsyncMock(
                    return_value={"attempt_count": 1, "max_attempts": 3}
                )

                service = AutomationService()
                provider_fn = AsyncMock(side_effect=Exception("401 Unauthorized"))

                result = await service.execute_provider_write(
                    operation_id=1,
                    creator_id=123,
                    provider_fn=provider_fn,
                )

                assert result["status"] == "failed"
                assert result["error_class"] == "auth"

    @pytest.mark.asyncio
    async def test_execute_provider_write_rate_limit_retries(self):
        """Provider write retries on rate limit error."""
        with patch("automation.service._settings") as mock_settings:
            mock_settings.autonomy_enabled = True
            with patch("automation.service.adb") as mock_adb:
                mock_adb.mark_retrying = AsyncMock(return_value=True)
                mock_adb.get_operation = AsyncMock(
                    return_value={"attempt_count": 1, "max_attempts": 3}
                )

                service = AutomationService()
                provider_fn = AsyncMock(side_effect=Exception("429 Too Many Requests"))

                result = await service.execute_provider_write(
                    operation_id=1,
                    creator_id=123,
                    provider_fn=provider_fn,
                )

                assert result["status"] == "retrying"
                assert result["error_class"] == "rate_limit"

    @pytest.mark.asyncio
    async def test_execute_provider_write_max_attempts(self):
        """Provider write fails after max attempts."""
        with patch("automation.service._settings") as mock_settings:
            mock_settings.autonomy_enabled = True
            with patch("automation.service.adb") as mock_adb:
                mock_adb.mark_failed = AsyncMock(return_value=True)
                mock_adb.get_operation = AsyncMock(
                    return_value={"attempt_count": 3, "max_attempts": 3}
                )

                service = AutomationService()
                provider_fn = AsyncMock(side_effect=Exception("429 Too Many Requests"))

                result = await service.execute_provider_write(
                    operation_id=1,
                    creator_id=123,
                    provider_fn=provider_fn,
                )

                assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_execute_provider_write_timeout_goes_unknown(self):
        """Provider write timeout results in UNKNOWN status."""
        with patch("automation.service._settings") as mock_settings:
            mock_settings.autonomy_enabled = True
            with patch("automation.service.adb") as mock_adb:
                mock_adb.mark_unknown = AsyncMock(return_value=True)
                mock_adb.get_operation = AsyncMock(
                    return_value={"attempt_count": 1, "max_attempts": 3}
                )

                service = AutomationService()
                provider_fn = AsyncMock(side_effect=Exception("Connection timeout"))

                result = await service.execute_provider_write(
                    operation_id=1,
                    creator_id=123,
                    provider_fn=provider_fn,
                )

                assert result["status"] == "unknown"
                assert result["error_class"] == "timeout"


# ── AutomationService.cancel ──────────────────────────────────────────────


class TestAutomationServiceCancel:
    """Test AutomationService.cancel method."""

    @pytest.mark.asyncio
    async def test_cancel_success(self):
        with patch("automation.service.adb") as mock_adb:
            mock_adb.cancel_operation = AsyncMock(return_value=True)

            service = AutomationService()
            result = await service.cancel(operation_id=1, creator_id=123)

            assert result is True

    @pytest.mark.asyncio
    async def test_cancel_failure(self):
        with patch("automation.service.adb") as mock_adb:
            mock_adb.cancel_operation = AsyncMock(return_value=False)

            service = AutomationService()
            result = await service.cancel(operation_id=1, creator_id=123)

            assert result is False
