"""Tests for P2.2 - Dashboard Follow-Up Management routes."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_msg(**overrides):
    base = {
        "id": 1,
        "user_id": 100,
        "execute_at": datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
        "content": "Hey! Just checking in",
        "reason": "post_purchase_followup",
        "status": "pending",
        "attempts": 0,
        "created_at": datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        "media_type": "",
        "media_path": "",
        "username": "testuser",
        "first_name": "Test",
    }
    base.update(overrides)
    return base


def _make_detail_msg(**overrides):
    base = _make_msg(**overrides)
    base.update(
        {
            "claimed_at": None,
            "claimed_by": None,
            "completed_at": None,
            "failed_at": None,
            "last_error": None,
            "dedup_key": "post_purchase_followup:txn123",
        }
    )
    return base


@pytest.mark.asyncio
@patch(
    "chatbotv2.dashboard.routes.followups._get_creator_id", new_callable=AsyncMock, return_value=1
)
@patch("chatbotv2.dashboard.routes.followups._require_creator", new_callable=AsyncMock)
@patch(
    "chatbotv2.dashboard.routes.followups.list_scheduled_messages_with_users",
    new_callable=AsyncMock,
)
@patch("chatbotv2.dashboard.routes.followups.count_scheduled_messages", new_callable=AsyncMock)
@patch("chatbotv2.dashboard.routes.followups.get_scheduled_stats", new_callable=AsyncMock)
async def test_followups_list_ok(
    mock_stats, mock_count, mock_list, mock_req, mock_cid, test_client
):
    msg = _make_msg()
    mock_list.return_value = [msg]
    mock_count.return_value = 1
    mock_stats.return_value = {
        "total": 1,
        "pending": 1,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }
    client = test_client
    resp = await client.get("/api/followups")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 1
    assert data["pagination"]["total"] == 1
    assert data["stats"]["total"] == 1
    assert isinstance(data["messages"][0]["execute_at"], str)


@pytest.mark.asyncio
@patch(
    "chatbotv2.dashboard.routes.followups._get_creator_id", new_callable=AsyncMock, return_value=1
)
@patch("chatbotv2.dashboard.routes.followups._require_creator", new_callable=AsyncMock)
@patch(
    "chatbotv2.dashboard.routes.followups.list_scheduled_messages_with_users",
    new_callable=AsyncMock,
)
@patch("chatbotv2.dashboard.routes.followups.count_scheduled_messages", new_callable=AsyncMock)
@patch("chatbotv2.dashboard.routes.followups.get_scheduled_stats", new_callable=AsyncMock)
async def test_followups_list_filter_status(
    mock_stats, mock_count, mock_list, mock_req, mock_cid, test_client
):
    mock_list.return_value = []
    mock_count.return_value = 0
    mock_stats.return_value = {
        "total": 0,
        "pending": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }
    client = test_client
    resp = await client.get("/api/followups?status=failed")
    assert resp.status_code == 200
    mock_list.assert_called_once_with(creator_id=1, status="failed", limit=20, offset=0)
    mock_count.assert_called_once_with(creator_id=1, status="failed")


@pytest.mark.asyncio
@patch(
    "chatbotv2.dashboard.routes.followups._get_creator_id", new_callable=AsyncMock, return_value=1
)
@patch("chatbotv2.dashboard.routes.followups._require_creator", new_callable=AsyncMock)
@patch(
    "chatbotv2.dashboard.routes.followups.list_scheduled_messages_with_users",
    new_callable=AsyncMock,
)
@patch("chatbotv2.dashboard.routes.followups.count_scheduled_messages", new_callable=AsyncMock)
@patch("chatbotv2.dashboard.routes.followups.get_scheduled_stats", new_callable=AsyncMock)
async def test_followups_list_pagination(
    mock_stats, mock_count, mock_list, mock_req, mock_cid, test_client
):
    mock_list.return_value = []
    mock_count.return_value = 50
    mock_stats.return_value = {
        "total": 50,
        "pending": 10,
        "processing": 0,
        "completed": 40,
        "failed": 0,
        "cancelled": 0,
    }
    client = test_client
    resp = await client.get("/api/followups?page=2&page_size=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pagination"]["page"] == 2
    assert data["pagination"]["total_pages"] == 5
    mock_list.assert_called_once_with(creator_id=1, status=None, limit=10, offset=10)


@pytest.mark.asyncio
@patch("chatbotv2.dashboard.routes.followups._compute_lag", new_callable=AsyncMock)
@patch(
    "chatbotv2.dashboard.routes.followups._get_creator_id", new_callable=AsyncMock, return_value=1
)
@patch("chatbotv2.dashboard.routes.followups._require_creator", new_callable=AsyncMock)
@patch("chatbotv2.dashboard.routes.followups.get_scheduled_stats", new_callable=AsyncMock)
async def test_followups_health_healthy(mock_stats, mock_req, mock_cid, mock_lag, test_client):
    mock_stats.return_value = {
        "total": 10,
        "pending": 5,
        "processing": 0,
        "completed": 5,
        "failed": 0,
        "cancelled": 0,
    }
    mock_lag.return_value = {"oldest_pending": None, "lag_seconds": 0, "pending_count": 0}
    client = test_client
    resp = await client.get("/api/followups/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
@patch("chatbotv2.dashboard.routes.followups._compute_lag", new_callable=AsyncMock)
@patch(
    "chatbotv2.dashboard.routes.followups._get_creator_id", new_callable=AsyncMock, return_value=1
)
@patch("chatbotv2.dashboard.routes.followups._require_creator", new_callable=AsyncMock)
@patch("chatbotv2.dashboard.routes.followups.get_scheduled_stats", new_callable=AsyncMock)
async def test_followups_health_warning(mock_stats, mock_req, mock_cid, mock_lag, test_client):
    mock_stats.return_value = {
        "total": 10,
        "pending": 5,
        "processing": 0,
        "completed": 4,
        "failed": 1,
        "cancelled": 0,
    }
    mock_lag.return_value = {"oldest_pending": None, "lag_seconds": 0, "pending_count": 0}
    client = test_client
    resp = await client.get("/api/followups/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "warning"


@pytest.mark.asyncio
@patch("chatbotv2.dashboard.routes.followups._compute_lag", new_callable=AsyncMock)
@patch(
    "chatbotv2.dashboard.routes.followups._get_creator_id", new_callable=AsyncMock, return_value=1
)
@patch("chatbotv2.dashboard.routes.followups._require_creator", new_callable=AsyncMock)
@patch("chatbotv2.dashboard.routes.followups.get_scheduled_stats", new_callable=AsyncMock)
async def test_followups_health_degraded(mock_stats, mock_req, mock_cid, mock_lag, test_client):
    mock_stats.return_value = {
        "total": 10,
        "pending": 1,
        "processing": 0,
        "completed": 5,
        "failed": 3,
        "cancelled": 1,
    }
    mock_lag.return_value = {"oldest_pending": None, "lag_seconds": 0, "pending_count": 0}
    client = test_client
    resp = await client.get("/api/followups/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


@pytest.mark.asyncio
@patch(
    "chatbotv2.dashboard.routes.followups._get_creator_id", new_callable=AsyncMock, return_value=1
)
@patch("chatbotv2.dashboard.routes.followups._require_creator", new_callable=AsyncMock)
@patch(
    "chatbotv2.dashboard.routes.followups.get_scheduled_message_with_user", new_callable=AsyncMock
)
async def test_followups_detail_ok(mock_get, mock_req, mock_cid, test_client):
    msg = _make_detail_msg()
    mock_get.return_value = msg
    client = test_client
    resp = await client.get("/api/followups/1")
    assert resp.status_code == 200
    data = resp.json()["message"]
    assert data["id"] == 1
    assert data["username"] == "testuser"
    assert isinstance(data["execute_at"], str)


@pytest.mark.asyncio
@patch(
    "chatbotv2.dashboard.routes.followups._get_creator_id", new_callable=AsyncMock, return_value=1
)
@patch("chatbotv2.dashboard.routes.followups._require_creator", new_callable=AsyncMock)
@patch(
    "chatbotv2.dashboard.routes.followups.get_scheduled_message_with_user", new_callable=AsyncMock
)
async def test_followups_detail_not_found(mock_get, mock_req, mock_cid, test_client):
    mock_get.return_value = None
    client = test_client
    resp = await client.get("/api/followups/999")
    assert resp.status_code == 404
    assert "NotFound" in resp.json()["error"]


@pytest.mark.asyncio
@patch(
    "chatbotv2.dashboard.routes.followups._get_creator_id", new_callable=AsyncMock, return_value=1
)
@patch("chatbotv2.dashboard.routes.followups._require_creator", new_callable=AsyncMock)
@patch("chatbotv2.dashboard.routes.followups.cancel_scheduled_message_rich", new_callable=AsyncMock)
async def test_followups_cancel_ok(mock_cancel, mock_req, mock_cid, test_client):
    mock_cancel.return_value = {
        "cancelled": True,
        "status": "cancelled",
        "reason": "Cancelled successfully",
    }
    client = test_client
    resp = await client.post("/api/followups/1/cancel")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True


@pytest.mark.asyncio
@patch(
    "chatbotv2.dashboard.routes.followups._get_creator_id", new_callable=AsyncMock, return_value=1
)
@patch("chatbotv2.dashboard.routes.followups._require_creator", new_callable=AsyncMock)
@patch("chatbotv2.dashboard.routes.followups.cancel_scheduled_message_rich", new_callable=AsyncMock)
async def test_followups_cancel_already_completed(mock_cancel, mock_req, mock_cid, test_client):
    mock_cancel.return_value = {
        "cancelled": False,
        "status": "completed",
        "reason": "Cannot cancel",
    }
    client = test_client
    resp = await client.post("/api/followups/1/cancel")
    assert resp.status_code == 409
    assert resp.json()["cancelled"] is False


@pytest.mark.asyncio
@patch(
    "chatbotv2.dashboard.routes.followups._get_creator_id", new_callable=AsyncMock, return_value=1
)
@patch("chatbotv2.dashboard.routes.followups._require_creator", new_callable=AsyncMock)
@patch("chatbotv2.dashboard.routes.followups.cancel_scheduled_message_rich", new_callable=AsyncMock)
async def test_followups_cancel_processing_rejected(mock_cancel, mock_req, mock_cid, test_client):
    mock_cancel.return_value = {
        "cancelled": False,
        "status": "processing",
        "reason": "Scheduler is delivering",
    }
    client = test_client
    resp = await client.post("/api/followups/1/cancel")
    assert resp.status_code == 409
    assert resp.json()["cancelled"] is False


@pytest.mark.asyncio
@patch(
    "chatbotv2.dashboard.routes.followups._get_creator_id", new_callable=AsyncMock, return_value=1
)
@patch("chatbotv2.dashboard.routes.followups._require_creator", new_callable=AsyncMock)
@patch("chatbotv2.dashboard.routes.followups.cancel_scheduled_message_rich", new_callable=AsyncMock)
async def test_followups_cancel_not_found(mock_cancel, mock_req, mock_cid, test_client):
    mock_cancel.return_value = {
        "cancelled": False,
        "status": "not_found",
        "reason": "Message not found",
    }
    client = test_client
    resp = await client.post("/api/followups/999/cancel")
    assert resp.status_code == 409
    assert resp.json()["status"] == "not_found"


@pytest.mark.asyncio
@patch("db.postgres.get_pool")
async def test_cancel_rich_pending_to_cancelled(mock_pool):
    from db.postgres import cancel_scheduled_message_rich

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"status": "pending"})
    conn.execute = AsyncMock(return_value="UPDATE 1")
    pool = AsyncMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock())
    )
    mock_pool.return_value = pool
    result = await cancel_scheduled_message_rich(1)
    assert result["cancelled"] is True
    assert result["status"] == "cancelled"
    conn.execute.assert_called_once()


@pytest.mark.asyncio
@patch("db.postgres.get_pool")
async def test_cancel_rich_already_cancelled(mock_pool):
    from db.postgres import cancel_scheduled_message_rich

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"status": "cancelled"})
    pool = AsyncMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock())
    )
    mock_pool.return_value = pool
    result = await cancel_scheduled_message_rich(1)
    assert result["cancelled"] is False
    assert result["status"] == "cancelled"
    conn.execute.assert_not_called()


@pytest.mark.asyncio
@patch("db.postgres.get_pool")
async def test_cancel_rich_completed_rejected(mock_pool):
    from db.postgres import cancel_scheduled_message_rich

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"status": "completed"})
    pool = AsyncMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock())
    )
    mock_pool.return_value = pool
    result = await cancel_scheduled_message_rich(1)
    assert result["cancelled"] is False
    assert result["status"] == "completed"


@pytest.mark.asyncio
@patch("db.postgres.get_pool")
async def test_cancel_rich_processing_rejected(mock_pool):
    from db.postgres import cancel_scheduled_message_rich

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"status": "processing"})
    pool = AsyncMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock())
    )
    mock_pool.return_value = pool
    result = await cancel_scheduled_message_rich(1)
    assert result["cancelled"] is False
    assert result["status"] == "processing"
    assert "delivering" in result["reason"].lower()


@pytest.mark.asyncio
@patch("db.postgres.get_pool")
async def test_cancel_rich_not_found(mock_pool):
    from db.postgres import cancel_scheduled_message_rich

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock())
    )
    mock_pool.return_value = pool
    result = await cancel_scheduled_message_rich(999)
    assert result["cancelled"] is False
    assert result["status"] == "not_found"


@pytest.mark.asyncio
async def test_followups_page_renders(test_client):
    client = test_client
    resp = await client.get("/dashboard/followups")
    assert resp.status_code == 200
    assert "Scheduled Follow-Ups" in resp.text


@pytest.mark.asyncio
@patch("db.postgres.get_pool")
async def test_create_scheduled_message_with_creator_id(mock_pool):
    from db.postgres import create_scheduled_message

    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value={"id": 42})
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock())
    )
    pool = AsyncMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock())
    )
    mock_pool.return_value = pool
    execute_at = datetime.now(UTC) + timedelta(hours=24)
    result = await create_scheduled_message(
        user_id=100,
        execute_at=execute_at,
        content="test",
        dedup_key="test:dedup:1",
        creator_id=7,
    )
    assert result == 42
    call_args = conn.fetchrow.call_args
    sql = call_args[0][0]
    assert "creator_id" in sql
    assert call_args[0][8] == 7


@pytest.mark.asyncio
@patch("db.postgres.get_pool")
async def test_create_scheduled_message_without_creator_id(mock_pool):
    from db.postgres import create_scheduled_message

    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value={"id": 43})
    conn.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock())
    )
    pool = AsyncMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock())
    )
    mock_pool.return_value = pool
    execute_at = datetime.now(UTC) + timedelta(hours=24)
    result = await create_scheduled_message(
        user_id=100,
        execute_at=execute_at,
        content="test",
        dedup_key="test:dedup:2",
    )
    assert result == 43
    call_args = conn.fetchrow.call_args
    assert call_args[0][8] is None
