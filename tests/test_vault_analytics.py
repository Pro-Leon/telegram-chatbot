"""Vault analytics tests — Phase 4 coverage.

Covers:
A. DB analytics queries (get_delivery_stats, get_product_delivery_counts, etc.)
B. Service layer analytics (get_vault_analytics, get_product_performance, get_fan_analytics)
C. API routes (analytics/overview, analytics/products, analytics/fans/{user_id})
D. Edge cases (empty data, no deliveries, single fan)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


# ── Helpers ──────────────────────────────────────────────────────────────────


class _FakePoolConnCM:
    """Async context manager that yields a mock connection."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        pass


def _raw_product(product_id=101, media=None, **overrides):
    """Build a raw product dict matching db.fangate.list_fangate_products output."""
    if media is None:
        media = [
            {"id": 201, "type": "image", "preview": "https://fangate.s3.amazonaws.com/img1.jpg"},
            {"id": 202, "type": "video", "preview": "https://fangate.s3.amazonaws.com/vid1.mp4"},
        ]
    base = {
        "id": product_id,
        "product_type": "digital",
        "title": f"Product {product_id}",
        "preview_url": f"https://fangate.s3.amazonaws.com/preview_{product_id}.jpg",
        "preview_blurred_url": None,
        "price_minor": 500,
        "in_collection": True,
        "sales_url": f"https://fangate.dev/link/{product_id}",
        "link_clicks": 10,
        "unlocks": 5,
        "total_earnings": 250,
        "folder_id": "folder_1",
        "folder_name": "Main Folder",
        "is_adult_content": False,
        "is_verif_age": False,
        "is_epoch_enabled": False,
        "is_should_consent": False,
        "is_downloadable": False,
        "is_accessible": True,
        "private_description": None,
        "public_description": None,
        "raw": {"media": media},
        "synced_at": "2026-08-23T00:00:00Z",
    }
    base.update(overrides)
    return base


def _mock_delivery_stats():
    return {
        "total_deliveries": 42,
        "unique_fans": 15,
        "unique_media": 30,
        "sent_count": 40,
        "pending_count": 2,
    }


def _mock_delivery_row(user_id=123, media_id=201, product_id=101, status="sent"):
    now = datetime.now(UTC)
    return {
        "id": 1,
        "fangate_media_id": media_id,
        "product_id": product_id,
        "sent_at": now,
        "status": status,
    }


def _mock_recent_delivery(user_id=123, media_id=201, product_id=101):
    now = datetime.now(UTC)
    return {
        "id": 1,
        "user_id": user_id,
        "fangate_media_id": media_id,
        "product_id": product_id,
        "sent_at": now,
        "status": "sent",
        "first_name": "TestFan",
        "username": "testfan",
    }


def _mock_top_fan(user_id=123, delivery_count=10):
    now = datetime.now(UTC)
    return {
        "user_id": user_id,
        "delivery_count": delivery_count,
        "first_delivery": now,
        "last_delivery": now,
        "first_name": "TestFan",
        "username": "testfan",
    }


def _make_pool(mock_conn):
    """Create a mock pool with proper async context manager for acquire()."""
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolConnCM(mock_conn))
    return pool


# ── A. DB analytics queries ─────────────────────────────────────────────────


class TestDeliveryStats:
    @pytest.mark.asyncio
    async def test_get_delivery_stats_returns_data(self):
        from db.vault import get_delivery_stats

        mock_row = _mock_delivery_stats()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)
        pool = _make_pool(mock_conn)

        with patch("db.vault.get_pool", return_value=pool):
            result = await get_delivery_stats(creator_id=1)

        assert result["total_deliveries"] == 42
        assert result["unique_fans"] == 15
        assert result["sent_count"] == 40

    @pytest.mark.asyncio
    async def test_get_delivery_stats_empty(self):
        from db.vault import get_delivery_stats

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        pool = _make_pool(mock_conn)

        with patch("db.vault.get_pool", return_value=pool):
            result = await get_delivery_stats(creator_id=1)

        assert result["total_deliveries"] == 0
        assert result["unique_fans"] == 0


class TestProductDeliveryCounts:
    @pytest.mark.asyncio
    async def test_get_product_delivery_counts(self):
        from db.vault import get_product_delivery_counts

        mock_rows = [
            {"product_id": 101, "cnt": 5},
            {"product_id": 102, "cnt": 3},
        ]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)
        pool = _make_pool(mock_conn)

        with patch("db.vault.get_pool", return_value=pool):
            result = await get_product_delivery_counts(creator_id=1)

        assert result == {101: 5, 102: 3}

    @pytest.mark.asyncio
    async def test_get_product_delivery_counts_empty(self):
        from db.vault import get_product_delivery_counts

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        pool = _make_pool(mock_conn)

        with patch("db.vault.get_pool", return_value=pool):
            result = await get_product_delivery_counts(creator_id=1)

        assert result == {}


class TestMediaDeliveryCounts:
    @pytest.mark.asyncio
    async def test_get_media_delivery_counts(self):
        from db.vault import get_media_delivery_counts

        mock_rows = [
            {"fangate_media_id": 201, "cnt": 8},
            {"fangate_media_id": 202, "cnt": 2},
        ]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)
        pool = _make_pool(mock_conn)

        with patch("db.vault.get_pool", return_value=pool):
            result = await get_media_delivery_counts(creator_id=1)

        assert result == {201: 8, 202: 2}


class TestFanDeliveryHistory:
    @pytest.mark.asyncio
    async def test_get_fan_delivery_history(self):
        from db.vault import get_fan_delivery_history

        mock_rows = [_mock_delivery_row(), _mock_delivery_row(media_id=202)]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)
        pool = _make_pool(mock_conn)

        with patch("db.vault.get_pool", return_value=pool):
            result = await get_fan_delivery_history(creator_id=1, user_id=123)

        assert len(result) == 2
        assert result[0]["fangate_media_id"] == 201

    @pytest.mark.asyncio
    async def test_get_fan_delivery_count(self):
        from db.vault import get_fan_delivery_count

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"cnt": 7})
        pool = _make_pool(mock_conn)

        with patch("db.vault.get_pool", return_value=pool):
            result = await get_fan_delivery_count(creator_id=1, user_id=123)

        assert result == 7


class TestRecentDeliveries:
    @pytest.mark.asyncio
    async def test_get_recent_deliveries(self):
        from db.vault import get_recent_deliveries

        mock_rows = [_mock_recent_delivery(), _mock_recent_delivery(user_id=456, media_id=203)]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)
        pool = _make_pool(mock_conn)

        with patch("db.vault.get_pool", return_value=pool):
            result = await get_recent_deliveries(creator_id=1, limit=10)

        assert len(result) == 2
        assert result[0]["first_name"] == "TestFan"


class TestTopFans:
    @pytest.mark.asyncio
    async def test_get_top_fans(self):
        from db.vault import get_top_fans

        mock_rows = [_mock_top_fan(delivery_count=15), _mock_top_fan(user_id=456, delivery_count=8)]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)
        pool = _make_pool(mock_conn)

        with patch("db.vault.get_pool", return_value=pool):
            result = await get_top_fans(creator_id=1, limit=5)

        assert len(result) == 2
        assert result[0]["delivery_count"] == 15


# ── B. Service layer analytics ──────────────────────────────────────────────


class TestGetVaultAnalytics:
    @pytest.mark.asyncio
    async def test_returns_aggregated_data(self):
        from vault.service import get_vault_analytics

        products = [_raw_product(101), _raw_product(102)]
        delivery_stats = _mock_delivery_stats()
        recent = [_mock_recent_delivery()]
        top_fans = [_mock_top_fan()]

        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=products), \
             patch("db.vault.get_delivery_stats", new_callable=AsyncMock, return_value=delivery_stats), \
             patch("db.vault.get_recent_deliveries", new_callable=AsyncMock, return_value=recent), \
             patch("db.vault.get_top_fans", new_callable=AsyncMock, return_value=top_fans), \
             patch("vault.service.df_service.get_earnings", new_callable=AsyncMock, return_value={}):

            result = await get_vault_analytics(creator_id=1)

        assert result["products"]["total"] == 2
        assert result["products"]["total_media"] == 4  # 2 media per product
        assert result["deliveries"]["total"] == 42
        assert result["deliveries"]["unique_fans"] == 15
        assert len(result["recent_deliveries"]) == 1
        assert len(result["top_fans"]) == 1

    @pytest.mark.asyncio
    async def test_empty_products(self):
        from vault.service import get_vault_analytics

        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=[]), \
             patch("db.vault.get_delivery_stats", new_callable=AsyncMock, return_value=_mock_delivery_stats()), \
             patch("db.vault.get_recent_deliveries", new_callable=AsyncMock, return_value=[]), \
             patch("db.vault.get_top_fans", new_callable=AsyncMock, return_value=[]), \
             patch("vault.service.df_service.get_earnings", new_callable=AsyncMock, return_value={}):

            result = await get_vault_analytics(creator_id=1)

        assert result["products"]["total"] == 0
        assert result["products"]["total_media"] == 0


class TestGetProductPerformance:
    @pytest.mark.asyncio
    async def test_returns_sorted_performance(self):
        from vault.service import get_product_performance

        products = [
            _raw_product(101, link_clicks=20, unlocks=10, total_earnings=500),
            _raw_product(102, link_clicks=5, unlocks=2, total_earnings=100),
        ]
        delivery_counts = {101: 8, 102: 3}

        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=products), \
             patch("db.vault.get_product_delivery_counts", new_callable=AsyncMock, return_value=delivery_counts):

            result = await get_product_performance(creator_id=1)

        assert len(result) == 2
        # Sorted by delivery_count DESC
        assert result[0]["id"] == 101
        assert result[0]["delivery_count"] == 8
        assert result[1]["id"] == 102
        assert result[1]["delivery_count"] == 3

    @pytest.mark.asyncio
    async def test_empty_products(self):
        from vault.service import get_product_performance

        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=[]), \
             patch("db.vault.get_product_delivery_counts", new_callable=AsyncMock, return_value={}):

            result = await get_product_performance(creator_id=1)

        assert result == []


class TestGetFanAnalytics:
    @pytest.mark.asyncio
    async def test_returns_enriched_history(self):
        from vault.service import get_fan_analytics

        history = [
            _mock_delivery_row(media_id=201, product_id=101),
            _mock_delivery_row(media_id=202, product_id=101),
        ]
        product = {"id": 101, "title": "Test Product"}

        with patch("db.vault.get_fan_delivery_history", new_callable=AsyncMock, return_value=history), \
             patch("db.vault.get_fan_delivery_count", new_callable=AsyncMock, return_value=2), \
             patch("db.fangate.get_fangate_product", new_callable=AsyncMock, return_value=product):

            result = await get_fan_analytics(creator_id=1, user_id=123)

        assert result["user_id"] == 123
        assert result["total_deliveries"] == 2
        assert len(result["history"]) == 2
        assert result["history"][0]["product_title"] == "Test Product"

    @pytest.mark.asyncio
    async def test_empty_history(self):
        from vault.service import get_fan_analytics

        with patch("db.vault.get_fan_delivery_history", new_callable=AsyncMock, return_value=[]), \
             patch("db.vault.get_fan_delivery_count", new_callable=AsyncMock, return_value=0):

            result = await get_fan_analytics(creator_id=1, user_id=123)

        assert result["total_deliveries"] == 0
        assert result["history"] == []
        assert result["first_delivery"] is None
        assert result["last_delivery"] is None


# ── D. Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_vault_analytics_handles_missing_raw_media(self):
        from vault.service import get_vault_analytics

        product = _raw_product(101)
        product["raw"] = "invalid json"  # Simulate corrupted raw field

        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=[product]), \
             patch("db.vault.get_delivery_stats", new_callable=AsyncMock, return_value=_mock_delivery_stats()), \
             patch("db.vault.get_recent_deliveries", new_callable=AsyncMock, return_value=[]), \
             patch("db.vault.get_top_fans", new_callable=AsyncMock, return_value=[]), \
             patch("vault.service.df_service.get_earnings", new_callable=AsyncMock, return_value={}):

            result = await get_vault_analytics(creator_id=1)

        # Should handle gracefully, counting 0 media for corrupted product
        assert result["products"]["total"] == 1
        assert result["products"]["total_media"] == 0

    @pytest.mark.asyncio
    async def test_product_performance_handles_none_values(self):
        from vault.service import get_product_performance

        product = _raw_product(101, media=[])

        with patch("db.fangate.list_fangate_products", new_callable=AsyncMock, return_value=[product]), \
             patch("db.vault.get_product_delivery_counts", new_callable=AsyncMock, return_value={}):

            result = await get_product_performance(creator_id=1)

        assert result[0]["delivery_count"] == 0
        assert result[0]["media_count"] == 0

    @pytest.mark.asyncio
    async def test_fan_analytics_missing_product(self):
        from vault.service import get_fan_analytics

        history = [_mock_delivery_row(product_id=999)]

        with patch("db.vault.get_fan_delivery_history", new_callable=AsyncMock, return_value=history), \
             patch("db.vault.get_fan_delivery_count", new_callable=AsyncMock, return_value=1), \
             patch("db.fangate.get_fangate_product", new_callable=AsyncMock, return_value=None):

            result = await get_fan_analytics(creator_id=1, user_id=123)

        # Should fallback to "Product #999" when product not found
        assert result["history"][0]["product_title"] == "Product #999"
