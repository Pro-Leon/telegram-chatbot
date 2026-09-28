"""Phase 100 — Real PostgreSQL delivery concurrency.

Tests pending→sent/failed transitions under actual PG concurrency.
"""

import asyncio
import random
from datetime import date
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

pytestmark = [pytest.mark.integration]


def _pg_available():
    import socket
    from core.config import get_settings
    try:
        s = get_settings()
        dsn = s.postgres_dsn
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


_skip = pytest.mark.skipif(not _pg_available(), reason="Requires PostgreSQL")


async def _pool():
    from core.config import get_settings
    s = get_settings()
    return await asyncpg.create_pool(dsn=s.postgres_dsn, min_size=2, max_size=5, command_timeout=30)


async def _setup(pool, creator_name, user_id, vault_id):
    async with pool.acquire() as conn:
        cr = await conn.fetchrow("INSERT INTO creators (name) VALUES ($1) RETURNING id", creator_name)
        cid = cr["id"]
        await conn.execute("INSERT INTO users (id, username, first_name) VALUES ($1,$2,$3) ON CONFLICT (id) DO NOTHING", user_id, f"u{user_id}", f"U{user_id}")
        await conn.execute("INSERT INTO free_media_pool (creator_id, vault_item_id, status) VALUES ($1,$2,'approved') ON CONFLICT DO NOTHING", cid, vault_id)
        auth = await conn.fetchrow(
            "INSERT INTO free_photo_deliveries (creator_id, user_id, day_bucket, seq, vault_item_id, status) VALUES ($1,$2, (NOW() AT TIME ZONE 'UTC')::date, 1, $3, 'pending') RETURNING id",
            cid, user_id, vault_id,
        )
        return cid, auth["id"]


async def _cleanup(pool, creator_id, user_id, vault_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM free_photo_deliveries WHERE creator_id=$1", creator_id)
        await conn.execute("DELETE FROM free_media_pool WHERE creator_id=$1", creator_id)
        await conn.execute("DELETE FROM users WHERE id=$1", user_id)
        await conn.execute("DELETE FROM creators WHERE id=$1", creator_id)


@_skip
@pytest.mark.asyncio
async def test_same_pending_two_concurrent_one_wins():
    from commerce.free_photo_delivery import deliver_free_photo
    pool = await _pool()
    with patch("commerce.free_photo_delivery.get_pool", return_value=pool):
        user_id = random.randint(10000000, 10999999)
        vault_id = f"vault100_{user_id}"
        cid, rid = await _setup(pool, f"c100_{user_id}", user_id, vault_id)
        try:
            mock_send = AsyncMock(return_value=type("M", (), {"id": 999})())
            with patch("chatbotv2.client.send_file", mock_send):
                r1, r2 = await asyncio.gather(
                    deliver_free_photo(cid, user_id, rid),
                    deliver_free_photo(cid, user_id, rid),
                )
            # At most one delivered, other already_sent/failed
            delivered = [r for r in (r1, r2) if r.delivered]
            assert len(delivered) == 1, f"{r1} {r2}"
            assert mock_send.await_count == 1  # only one actual Telegram send due to advisory lock serialization
            # DB final state sent
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT status FROM free_photo_deliveries WHERE id=$1", rid)
                assert row["status"] == "sent"
        finally:
            await _cleanup(pool, cid, user_id, vault_id)
            await pool.close()


@_skip
@pytest.mark.asyncio
async def test_pending_success_final_sent():
    from commerce.free_photo_delivery import deliver_free_photo
    pool = await _pool()
    with patch("commerce.free_photo_delivery.get_pool", return_value=pool):
        user_id = random.randint(11000000, 11999999)
        vault_id = f"vault101_{user_id}"
        cid, rid = await _setup(pool, f"c101_{user_id}", user_id, vault_id)
        try:
            with patch("chatbotv2.client.send_file", AsyncMock(return_value=type("M", (), {"id": 111})())):
                r = await deliver_free_photo(cid, user_id, rid)
            assert r.delivered is True
            assert r.reason == "sent"
            async with pool.acquire() as conn:
                cnt = await conn.fetchval("SELECT COUNT(*) FROM free_photo_deliveries WHERE creator_id=$1 AND user_id=$2 AND status='sent'", cid, user_id)
                assert int(cnt) == 1
        finally:
            await _cleanup(pool, cid, user_id, vault_id)
            await pool.close()


@_skip
@pytest.mark.asyncio
async def test_pending_failed_final_failed_zero_quota():
    from commerce.free_photo_delivery import deliver_free_photo
    pool = await _pool()
    with patch("commerce.free_photo_delivery.get_pool", return_value=pool):
        user_id = random.randint(12000000, 12999999)
        vault_id = f"vault102_{user_id}"
        cid, rid = await _setup(pool, f"c102_{user_id}", user_id, vault_id)
        try:
            with patch("chatbotv2.client.send_file", AsyncMock(side_effect=Exception("Telegram down"))):
                r = await deliver_free_photo(cid, user_id, rid)
            assert r.delivered is False
            assert r.reason == "failed"
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT status FROM free_photo_deliveries WHERE id=$1", rid)
                assert row["status"] == "failed"
                cnt = await conn.fetchval("SELECT COUNT(*) FROM free_photo_deliveries WHERE creator_id=$1 AND user_id=$2 AND status='sent'", cid, user_id)
                assert int(cnt) == 0
        finally:
            await _cleanup(pool, cid, user_id, vault_id)
            await pool.close()


@_skip
@pytest.mark.asyncio
async def test_already_sent_no_second_delivery():
    from commerce.free_photo_delivery import deliver_free_photo
    pool = await _pool()
    with patch("commerce.free_photo_delivery.get_pool", return_value=pool):
        user_id = random.randint(13000000, 13999999)
        vault_id = f"vault103_{user_id}"
        cid, rid = await _setup(pool, f"c103_{user_id}", user_id, vault_id)
        try:
            with patch("chatbotv2.client.send_file", AsyncMock(return_value=type("M", (), {"id": 1})())):
                r1 = await deliver_free_photo(cid, user_id, rid)
                assert r1.delivered is True
            with patch("chatbotv2.client.send_file", AsyncMock()) as ms:
                r2 = await deliver_free_photo(cid, user_id, rid)
                assert r2.reason == "already_sent"
                ms.assert_not_called()
        finally:
            await _cleanup(pool, cid, user_id, vault_id)
            await pool.close()


@_skip
@pytest.mark.asyncio
async def test_failed_retry_via_phase98():
    from commerce.free_photo import authorize_free_photo
    from commerce.free_photo_delivery import deliver_free_photo
    pool = await _pool()
    # Need to patch both get_pools to same test pool
    with patch("commerce.free_photo.get_pool", return_value=pool), patch("commerce.free_photo_delivery.get_pool", return_value=pool), patch("db.postgres.get_pool", return_value=pool):
        user_id = random.randint(14000000, 14999999)
        vault_id = f"vault104_{user_id}"
        # Use same helper but need creator
        async with pool.acquire() as conn:
            cr = await conn.fetchrow("INSERT INTO creators (name) VALUES ($1) RETURNING id", f"c104_{user_id}")
            cid = cr["id"]
            await conn.execute("INSERT INTO users (id, username, first_name) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", user_id, f"u{user_id}", f"U{user_id}")
            await conn.execute("INSERT INTO free_media_pool (creator_id, vault_item_id, status) VALUES ($1,$2,'approved') ON CONFLICT DO NOTHING", cid, vault_id)
        try:
            # First reservation via Phase99/98
            auth1 = await authorize_free_photo(cid, user_id, vault_id)
            assert auth1.eligible
            rid = auth1.reservation_id
            # Deliver and fail
            with patch("chatbotv2.client.send_file", AsyncMock(side_effect=Exception("fail"))):
                d1 = await deliver_free_photo(cid, user_id, rid)
                assert d1.reason == "failed"
            # Retry via Phase98 should reuse same reservation
            auth2 = await authorize_free_photo(cid, user_id, vault_id)
            assert auth2.eligible
            assert auth2.reservation_id == rid
            assert auth2.reason == "reused_failed"
            # Now deliver succeeds
            with patch("chatbotv2.client.send_file", AsyncMock(return_value=type("M", (), {"id": 5})())):
                d2 = await deliver_free_photo(cid, user_id, rid)
                assert d2.delivered is True
                assert d2.reason == "sent"
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM free_photo_deliveries WHERE creator_id=$1", cid)
                await conn.execute("DELETE FROM free_media_pool WHERE creator_id=$1", cid)
                await conn.execute("DELETE FROM users WHERE id=$1", user_id)
                await conn.execute("DELETE FROM creators WHERE id=$1", cid)
            await pool.close()
