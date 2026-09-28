"""Inbound Telegram message idempotency tests.

Verifies the PostgreSQL-level uniqueness guarantee for inbound messages:
- Handler saves exactly once (no dual-save with worker)
- save_inbound_message() is idempotent (ON CONFLICT)
- Migration creates the correct unique index
- Outbound messages are unaffected
- NULL telegram_message_id handled correctly
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Test A: Worker does NOT save inbound — handler does
# ---------------------------------------------------------------------------

class TestNoDualSave:
    """The LLM worker must not import or call save_inbound_message."""

    @pytest.mark.asyncio
    async def test_worker_does_not_import_save_inbound(self):
        """save_inbound_message is not imported in llm_worker module."""
        import workers.llm_worker as wm
        assert not hasattr(wm, "save_inbound_message"), (
            "save_inbound_message should not be imported in llm_worker"
        )

    @pytest.mark.asyncio
    async def test_handler_still_has_save_inbound(self):
        """save_inbound_message is still imported in handlers module."""
        import chatbotv2.handlers as h
        assert hasattr(h, "save_inbound_message"), (
            "save_inbound_message must remain in handlers"
        )


# ---------------------------------------------------------------------------
# Test B: save_inbound_message is idempotent
# ---------------------------------------------------------------------------

class TestIdempotentInsert:
    """save_inbound_message uses ON CONFLICT for idempotency."""

    @pytest.mark.asyncio
    async def test_function_uses_on_conflict(self):
        """The INSERT uses ON CONFLICT to handle duplicates atomically."""
        from db.postgres import save_inbound_message
        import inspect
        source = inspect.getsource(save_inbound_message)
        assert "ON CONFLICT" in source
        assert "DO UPDATE" in source

    @pytest.mark.asyncio
    async def test_on_conflict_targets_correct_columns(self):
        """ON CONFLICT targets (user_id, telegram_message_id)."""
        from db.postgres import save_inbound_message
        import inspect
        source = inspect.getsource(save_inbound_message)
        assert "user_id, telegram_message_id" in source

    @pytest.mark.asyncio
    async def test_on_conflict_where_clause_matches_index(self):
        """ON CONFLICT WHERE clause matches the partial index definition."""
        from db.postgres import save_inbound_message
        import inspect
        source = inspect.getsource(save_inbound_message)
        assert "direction = 'inbound'" in source
        assert "telegram_message_id IS NOT NULL" in source

    @pytest.mark.asyncio
    async def test_do_update_is_noop(self):
        """DO UPDATE SET id = messages.id is a no-op that returns existing ID."""
        from db.postgres import save_inbound_message
        import inspect
        source = inspect.getsource(save_inbound_message)
        assert "DO UPDATE SET id = messages.id" in source


# ---------------------------------------------------------------------------
# Test C: Outbound unaffected
# ---------------------------------------------------------------------------

class TestOutboundUnaffected:
    """Outbound messages use direction='outbound', outside the partial index."""

    @pytest.mark.asyncio
    async def test_outbound_save_uses_different_direction(self):
        """save_outbound_message uses direction='outbound'."""
        from db.postgres import save_outbound_message
        import inspect
        source = inspect.getsource(save_outbound_message)
        assert "'outbound'" in source

    @pytest.mark.asyncio
    async def test_outbound_after_send_uses_different_direction(self):
        """save_outbound_after_send uses direction='outbound'."""
        from db.postgres import save_outbound_after_send
        import inspect
        source = inspect.getsource(save_outbound_after_send)
        assert "'outbound'" in source


# ---------------------------------------------------------------------------
# Test D: NULL telegram_message_id behavior
# ---------------------------------------------------------------------------

class TestNullTelegramMessageId:
    """NULL telegram_message_id values are excluded from the unique index."""

    def test_partial_index_excludes_nulls(self):
        with open("db/migrations/20260824010000_inbound_message_idempotency.sql") as f:
            sql = f.read()
        assert "IS NOT NULL" in sql
        assert "CREATE UNIQUE INDEX" in sql
        assert "direction = 'inbound'" in sql


# ---------------------------------------------------------------------------
# Test E: Migration exists and is correct
# ---------------------------------------------------------------------------

class TestMigrationExists:
    """The uniqueness migration has the correct structure."""

    def test_migration_file_exists(self):
        import os
        path = "db/migrations/20260824010000_inbound_message_idempotency.sql"
        assert os.path.exists(path)

    def test_migration_creates_unique_index(self):
        with open("db/migrations/20260824010000_inbound_message_idempotency.sql") as f:
            sql = f.read()
        assert "CREATE UNIQUE INDEX" in sql
        assert "idx_messages_inbound_unique" in sql

    def test_migration_scoped_to_inbound(self):
        with open("db/migrations/20260824010000_inbound_message_idempotency.sql") as f:
            sql = f.read()
        assert "direction = 'inbound'" in sql

    def test_migration_idempotent(self):
        with open("db/migrations/20260824010000_inbound_message_idempotency.sql") as f:
            sql = f.read()
        assert "IF NOT EXISTS" in sql


# ---------------------------------------------------------------------------
# Test F: Debounce behavior preserved
# ---------------------------------------------------------------------------

class TestDebouncePreserved:
    """The debounce mechanism is independent of the persistence fix."""

    def test_debounce_window_configurable(self):
        """debounce_window_seconds is still in the config."""
        from core.config import get_settings
        settings = get_settings()
        assert hasattr(settings, "debounce_window_seconds")
        assert settings.debounce_window_seconds >= 0

    def test_debounce_enqueue_exists(self):
        """debounce_enqueue function still exists in redis module."""
        from db.redis import debounce_enqueue
        assert callable(debounce_enqueue)

    def test_get_debounced_messages_exists(self):
        """get_debounced_messages function still exists."""
        from db.redis import get_debounced_messages
        assert callable(get_debounced_messages)
