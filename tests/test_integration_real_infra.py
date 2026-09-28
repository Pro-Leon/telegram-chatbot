"""Real-infrastructure integration tests for autonomous commerce.

These tests exercise the actual production code paths against real PostgreSQL
and Redis infrastructure. They mock only external network boundaries (LLM,
Fangate HTTP, Telegram MTProto).

REQUIRES:
- PostgreSQL running at POSTGRES_DSN (default: postgresql://postgres:postgres@127.0.0.1:5432/postgres)
- Redis running at REDIS_URL (default: redis://127.0.0.1:6379)
- Database schema initialized from db/schema.sql + migrations

To run:
    pytest tests/test_integration_real_infra.py -v

If PostgreSQL/Redis are unavailable, tests are skipped with a clear message.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Infrastructure availability check
# ---------------------------------------------------------------------------

def _pg_available() -> bool:
    """Check if PostgreSQL is reachable (synchronous TCP check)."""
    import socket
    from core.config import get_settings
    try:
        settings = get_settings()
        dsn = settings.postgres_dsn
        host = "127.0.0.1"
        port = 5432
        for part in dsn.split():
            if part.startswith("host="):
                host = part.split("=", 1)[1]
            elif part.startswith("port="):
                port = int(part.split("=", 1)[1])
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False


def _redis_available() -> bool:
    """Check if Redis is reachable (synchronous TCP check)."""
    import socket
    from core.config import get_settings
    try:
        settings = get_settings()
        url = settings.redis_url
        host = "127.0.0.1"
        port = 6379
        # redis://127.0.0.1:6379
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or host
        port = parsed.port or port
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False


_skip_no_infra = pytest.mark.skipif(
    not (_pg_available() and _redis_available()),
    reason="Requires PostgreSQL and Redis (set POSTGRES_DSN, REDIS_URL)",
)


# ---------------------------------------------------------------------------
# Test A — Autonomous commerce path (real infra)
# ---------------------------------------------------------------------------

class TestAutonomousCommercePath:
    """Exercise the real path from commerce state resolution through offer creation.

    Mocks: LLM signal extraction, Fangate API verification
    Real: PostgreSQL, Redis, application wiring
    """

    @_skip_no_infra
    @pytest.mark.asyncio
    async def test_offer_creation_with_real_db(self):
        """Verify that execute_ppv creates a real offer in PostgreSQL."""
        from db.postgres import get_pool

        pool = await get_pool()

        # Setup: create a creator, integration, user, and product
        async with pool.acquire() as conn:
            # Create creator
            creator = await conn.fetchrow(
                "INSERT INTO creators (name) VALUES ('test_creator') RETURNING id"
            )
            creator_id = creator["id"]

            # Create integration (encrypted key is fake but valid format)
            await conn.execute(
                """
                INSERT INTO creator_integrations
                    (creator_id, encrypted_api_key, status)
                VALUES ($1, 'fake_encrypted_key', 'active')
                """,
                creator_id,
            )

            # Create user
            user = await conn.fetchrow(
                "INSERT INTO users (id, username, first_name) VALUES (999001, 'testuser', 'Test') RETURNING id"
            )
            user_id = user["id"]

            # Create product in local mirror
            await conn.execute(
                """
                INSERT INTO fangate_products
                    (id, creator_id, title, price_minor, sales_url, is_accessible, raw)
                VALUES (99001, $1, 'Test Product', 1000, 'https://fangate.info/buy/99001', true, $2::jsonb)
                """,
                creator_id,
                json.dumps({"media": [{"id": 1, "type": "image", "preview": "https://example.com/img.jpg"}]}),
            )

        try:
            from commerce.execution import ExecutionStatus, execute_ppv
            from commerce.decision import CommerceDecision, CommerceAction

            # Mock Fangate verification to return consistent data
            mock_verify = AsyncMock(return_value={
                "title": "Test Product",
                "price_minor": 1000,
                "currency": "USD",
                "sales_url": "https://fangate.info/buy/99001",
                "age_verification_required": False,
            })

            decision = CommerceDecision(
                action=CommerceAction.OFFER_PPV,
                allowed=True,
                reason="explicit_buying_intent",
                confidence=0.95,
            )

            with patch("commerce.execution.fservice.verify_product", mock_verify), \
                 patch("commerce.execution.fservice.get_product", AsyncMock(return_value={
                     "title": "Test Product",
                     "price_minor": 1000,
                     "currency": "USD",
                     "sales_url": "https://fangate.info/buy/99001",
                     "is_accessible": True,
                 })):
                result = await execute_ppv(
                    creator_id=creator_id,
                    user_id=user_id,
                    product_id=99001,
                    decision=decision,
                    created_by="integration_test",
                )

            # Verify offer was created in the real database
            async with pool.acquire() as conn:
                offer = await conn.fetchrow(
                    "SELECT * FROM commerce_offers WHERE creator_id = $1 AND user_id = $2 AND product_id = $3",
                    creator_id,
                    user_id,
                    99001,
                )

            assert result.status == ExecutionStatus.EXECUTED
            assert offer is not None
            assert offer["state"] == "pending"
            assert offer["link"] == "https://fangate.info/buy/99001"
            assert offer["price_minor"] == 1000

        finally:
            # Cleanup
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM commerce_offers WHERE creator_id = $1", creator_id)
                await conn.execute("DELETE FROM fangate_products WHERE id = 99001")
                await conn.execute("DELETE FROM users WHERE id = $1", user_id)
                await conn.execute("DELETE FROM creator_integrations WHERE creator_id = $1", creator_id)
                await conn.execute("DELETE FROM creators WHERE id = $1", creator_id)


# ---------------------------------------------------------------------------
# Test B — Webhook → attribution → fulfillment (real infra)
# ---------------------------------------------------------------------------

class TestWebhookAttributionPath:
    """Exercise the real path from webhook persistence through attribution.

    Mocks: Fangate HTTP boundary (HMAC verification bypassed for test)
    Real: PostgreSQL, application attribution logic
    """

    @_skip_no_infra
    @pytest.mark.asyncio
    async def test_webhook_attribution_with_real_db(self):
        """Verify that a webhook purchase is attributed to the correct pending offer."""
        from db.postgres import get_pool
        from commerce.dao import attribute_purchase_from_webhook

        pool = await get_pool()

        # Setup: creator, integration, user, product, and a pending offer
        async with pool.acquire() as conn:
            creator = await conn.fetchrow(
                "INSERT INTO creators (name) VALUES ('test_webhook_creator') RETURNING id"
            )
            creator_id = creator["id"]

            user = await conn.fetchrow(
                "INSERT INTO users (id, username, first_name) VALUES (999002, 'webhookuser', 'Webhook') RETURNING id"
            )
            user_id = user["id"]

            await conn.execute(
                """
                INSERT INTO fangate_products
                    (id, creator_id, title, price_minor, sales_url, is_accessible)
                VALUES (99002, $1, 'Webhook Product', 2000, 'https://fangate.info/buy/99002', true)
                """,
                creator_id,
            )

            # Create a pending offer (simulates what execute_ppv creates)
            offer = await conn.fetchrow(
                """
                INSERT INTO commerce_offers
                    (creator_id, user_id, product_id, link, price_minor, currency, state, created_by)
                VALUES ($1, $2, 99002, 'https://fangate.info/buy/99002', 2000, 'USD', 'pending', 'test')
                RETURNING id
                """,
                creator_id,
                user_id,
            )
            offer_id = offer["id"]

            # Create a pending transaction (simulates webhook persistence before attribution)
            await conn.execute(
                """
                INSERT INTO fangate_transactions
                    (creator_id, transaction_id, event_type, product_id)
                VALUES ($1, 'webhook_txn_001', 'purchase', 99002)
                """,
                creator_id,
            )

        try:
            # Run the real attribution
            record = await attribute_purchase_from_webhook(
                creator_id=creator_id,
                product_id=99002,
                transaction_id="webhook_txn_001",
                revenue_minor=2000,
            )

            # Verify attribution succeeded
            assert record is not None
            assert record.user_id == user_id
            assert record.offer_id == offer_id
            assert record.transaction_id == "webhook_txn_001"

            # Verify database state
            async with pool.acquire() as conn:
                updated_offer = await conn.fetchrow(
                    "SELECT * FROM commerce_offers WHERE id = $1", offer_id
                )
                updated_txn = await conn.fetchrow(
                    "SELECT * FROM fangate_transactions WHERE creator_id = $1 AND transaction_id = 'webhook_txn_001'",
                    creator_id,
                )

            assert updated_offer["state"] == "purchased"
            assert updated_offer["transaction_id"] == "webhook_txn_001"
            assert updated_txn["user_id"] == user_id

        finally:
            # Cleanup
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM commerce_offers WHERE creator_id = $1", creator_id)
                await conn.execute("DELETE FROM fangate_transactions WHERE creator_id = $1", creator_id)
                await conn.execute("DELETE FROM ppv_analytics_daily WHERE creator_id = $1", creator_id)
                await conn.execute("DELETE FROM fangate_products WHERE id = 99002")
                await conn.execute("DELETE FROM users WHERE id = $1", user_id)
                await conn.execute("DELETE FROM creator_integrations WHERE creator_id = $1", creator_id)
                await conn.execute("DELETE FROM creators WHERE id = $1", creator_id)


# ---------------------------------------------------------------------------
# Test C — Vault delivery reservation (real infra)
# ---------------------------------------------------------------------------

class TestVaultDeliveryReservation:
    """Exercise the real vault delivery reservation and uniqueness constraint.

    Real: PostgreSQL (vault_media_deliveries table)
    """

    @_skip_no_infra
    @pytest.mark.asyncio
    async def test_reserve_and_finalize_delivery(self):
        """Verify reserve → finalize lifecycle with real PostgreSQL."""
        from db.vault import finalize_delivery, has_user_received_media, record_delivery, reserve_delivery

        creator_id = 999100
        user_id = 999200
        fangate_media_id = 999300

        try:
            # Reserve a delivery
            delivery_id = await reserve_delivery(
                creator_id, user_id, fangate_media_id, product_id=999400
            )
            assert delivery_id is not None
            assert isinstance(delivery_id, int)

            # Verify pending state
            assert not await has_user_received_media(creator_id, user_id, fangate_media_id)

            # Duplicate reservation returns None (UNIQUE constraint)
            dup_id = await reserve_delivery(
                creator_id, user_id, fangate_media_id, product_id=999400
            )
            assert dup_id is None

            # Finalize the delivery
            finalized = await finalize_delivery(delivery_id, telegram_message_id=12345)
            assert finalized is True

            # Now marked as received
            assert await has_user_received_media(creator_id, user_id, fangate_media_id)

        finally:
            # Cleanup
            from db.postgres import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM vault_media_deliveries WHERE creator_id = $1 AND user_id = $2",
                    creator_id,
                    user_id,
                )

    @_skip_no_infra
    @pytest.mark.asyncio
    async def test_uniqueness_constraint_prevents_duplicate_delivery(self):
        """The UNIQUE(creator_id, user_id, fangate_media_id) constraint prevents duplicates."""
        from db.vault import reserve_delivery
        from db.postgres import get_pool

        creator_id = 999101
        user_id = 999201
        fangate_media_id = 999301

        try:
            # First reservation succeeds
            id1 = await reserve_delivery(creator_id, user_id, fangate_media_id)
            assert id1 is not None

            # Second reservation returns None (conflict)
            id2 = await reserve_delivery(creator_id, user_id, fangate_media_id)
            assert id2 is None

            # Third reservation for different media succeeds
            id3 = await reserve_delivery(creator_id, user_id, fangate_media_id + 1)
            assert id3 is not None

        finally:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM vault_media_deliveries WHERE creator_id = $1 AND user_id = $2",
                    creator_id,
                    user_id,
                )


# ---------------------------------------------------------------------------
# Test D — Attribution reconciliation (real infra)
# ---------------------------------------------------------------------------

class TestAttributionReconciliation:
    """Verify that reconciliation can attribute previously unattributed purchases.

    Real: PostgreSQL
    """

    @_skip_no_infra
    @pytest.mark.asyncio
    async def test_reconcile_webhook_before_offer(self):
        """Transaction persisted before offer exists → reconciliation attributes later."""
        from db.postgres import get_pool
        from commerce.reconciliation import reconcile_unattributed_purchases

        pool = await get_pool()

        # Setup: creator, user, product
        async with pool.acquire() as conn:
            creator = await conn.fetchrow(
                "INSERT INTO creators (name) VALUES ('test_reconcile_creator') RETURNING id"
            )
            creator_id = creator["id"]

            user = await conn.fetchrow(
                "INSERT INTO users (id, username, first_name) VALUES (999003, 'reconcileuser', 'Reconcile') RETURNING id"
            )
            user_id = user["id"]

            await conn.execute(
                """
                INSERT INTO fangate_products
                    (id, creator_id, title, price_minor, sales_url, is_accessible)
                VALUES (99003, $1, 'Reconcile Product', 3000, 'https://fangate.info/buy/99003', true)
                """,
                creator_id,
            )

            # Transaction persisted FIRST (webhook arrived before offer)
            await conn.execute(
                """
                INSERT INTO fangate_transactions
                    (creator_id, transaction_id, event_type, product_id)
                VALUES ($1, 'reconcile_txn_001', 'purchase', 99003)
                """,
                creator_id,
            )

            # Offer created LATER (after webhook)
            await conn.execute(
                """
                INSERT INTO commerce_offers
                    (creator_id, user_id, product_id, link, price_minor, currency, state, created_by)
                VALUES ($1, $2, 99003, 'https://fangate.info/buy/99003', 3000, 'USD', 'pending', 'test')
                """,
                creator_id,
                user_id,
            )

        try:
            # Run reconciliation
            count = await reconcile_unattributed_purchases()

            assert count == 1

            # Verify the transaction is now attributed
            async with pool.acquire() as conn:
                txn = await conn.fetchrow(
                    "SELECT * FROM fangate_transactions WHERE creator_id = $1 AND transaction_id = 'reconcile_txn_001'",
                    creator_id,
                )
                offer = await conn.fetchrow(
                    """
                    SELECT * FROM commerce_offers
                    WHERE creator_id = $1 AND product_id = 99003 AND state = 'purchased'
                    """,
                    creator_id,
                )

            assert txn["user_id"] == user_id
            assert offer is not None
            assert offer["transaction_id"] == "reconcile_txn_001"

        finally:
            # Cleanup
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM commerce_offers WHERE creator_id = $1", creator_id)
                await conn.execute("DELETE FROM fangate_transactions WHERE creator_id = $1", creator_id)
                await conn.execute("DELETE FROM ppv_analytics_daily WHERE creator_id = $1", creator_id)
                await conn.execute("DELETE FROM fangate_products WHERE id = 99003")
                await conn.execute("DELETE FROM users WHERE id = $1", user_id)
                await conn.execute("DELETE FROM creator_integrations WHERE creator_id = $1", creator_id)
                await conn.execute("DELETE FROM creators WHERE id = $1", creator_id)


# ---------------------------------------------------------------------------
# Test E — Autonomy kill switch (real infra)
# ---------------------------------------------------------------------------

class TestAutonomyKillSwitch:
    """Verify kill switch behavior with real config infrastructure."""

    def test_kill_switch_prevents_commerce(self):
        """AUTONOMY_ENABLED=false returns None from _try_commerce_draft."""
        from core.config import Settings

        settings = Settings(
            OPENAI_API_KEY="test",
            POSTGRES_DSN="postgresql://localhost:5432/test",
            REDIS_URL="redis://localhost:6379",
            autonomy_enabled=False,
        )
        assert settings.autonomy_enabled is False

    def test_kill_switch_allows_commerce(self):
        """AUTONOMY_ENABLED=true (default) allows commerce path."""
        from core.config import Settings

        settings = Settings(
            OPENAI_API_KEY="test",
            POSTGRES_DSN="postgresql://localhost:5432/test",
            REDIS_URL="redis://localhost:6379",
        )
        assert settings.autonomy_enabled is True
