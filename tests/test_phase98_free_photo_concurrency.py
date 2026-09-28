"""Phase 98 Gate 2 — Real PostgreSQL concurrency proof.

Mocks/sequential simulations do NOT prove advisory-lock serialization.
This module uses two independent asyncpg connections via a per-test pool,
actual transactions, actual advisory locks, and actual
free_photo_deliveries constraints.

Requires real PostgreSQL + migrations applied.
All tests are skipped if infra unavailable (not faked).

Scenarios:
  A — ceiling 2, 0 sent, 2 concurrent distinct media → both succeed, third fails
  B — ceiling 2, 1 sent, 2 concurrent distinct media → exactly one succeeds
  C — purchased ceiling 4, 0 sent, 5 concurrent → exactly 4 succeed
  D — concurrent duplicate same media → one reservation, second duplicate

Pending is considered occupancy (seq reservation) but quota is COUNT sent.
The invariant is: effective reserved capacity never exceeds ceiling and
no duplicate/racing reservations escape.
"""

from __future__ import annotations

import asyncio
import random
from datetime import date
from unittest.mock import patch

import asyncpg
import pytest

pytestmark = [pytest.mark.integration]


def _pg_available() -> bool:
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


_skip_no_pg = pytest.mark.skipif(not _pg_available(), reason="Requires PostgreSQL")


async def _create_test_pool():
    from core.config import get_settings
    s = get_settings()
    # min_size 2 to allow two concurrent workers each with its own connection
    return await asyncpg.create_pool(dsn=s.postgres_dsn, min_size=2, max_size=5, command_timeout=30)


async def _setup_creator_user(pool, *, creator_name: str, user_id: int):
    async with pool.acquire() as conn:
        crow = await conn.fetchrow("INSERT INTO creators (name) VALUES ($1) RETURNING id", creator_name)
        cid = crow["id"]
        await conn.execute(
            "INSERT INTO users (id, username, first_name) VALUES ($1, $2, $3) ON CONFLICT (id) DO NOTHING",
            user_id,
            f"u{user_id}",
            f"User{user_id}",
        )
        return cid


async def _teardown(pool, creator_id: int, user_id: int, vault_ids: list[str]):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM free_photo_deliveries WHERE creator_id=$1", creator_id)
        if vault_ids:
            await conn.execute("DELETE FROM free_media_pool WHERE creator_id=$1 AND vault_item_id = ANY($2::text[])", creator_id, vault_ids)
        await conn.execute("DELETE FROM commerce_offers WHERE creator_id=$1 AND user_id=$2", creator_id, user_id)
        await conn.execute("DELETE FROM users WHERE id=$1", user_id)
        await conn.execute("DELETE FROM creators WHERE id=$1", creator_id)


async def _add_approved_media(pool, creator_id: int, vault_ids: list[str]):
    async with pool.acquire() as conn:
        for vid in vault_ids:
            await conn.execute(
                "INSERT INTO free_media_pool (creator_id, vault_item_id, status) VALUES ($1,$2,'approved') ON CONFLICT (creator_id, vault_item_id) DO UPDATE SET status='approved'",
                creator_id, vid,
            )


async def _add_purchased_offer(pool, creator_id: int, user_id: int):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO commerce_offers (creator_id, user_id, product_id, link, state, transaction_id, created_by)
            VALUES ($1,$2, 999999, 'https://example.com/buy', 'purchased', $3, 'test')
            ON CONFLICT DO NOTHING
            """,
            creator_id, user_id, f"txn_{creator_id}_{user_id}_{random.randint(100000,999999)}",
        )


async def _count_deliveries(pool, creator_id: int, user_id: int, day: date, status: str | None = None):
    async with pool.acquire() as conn:
        if status:
            cnt = await conn.fetchval(
                "SELECT COUNT(*) FROM free_photo_deliveries WHERE creator_id=$1 AND user_id=$2 AND day_bucket=$3 AND status=$4",
                creator_id, user_id, day, status,
            )
        else:
            cnt = await conn.fetchval(
                "SELECT COUNT(*) FROM free_photo_deliveries WHERE creator_id=$1 AND user_id=$2 AND day_bucket=$3",
                creator_id, user_id, day,
            )
        rows = await conn.fetch(
            "SELECT seq, vault_item_id, status FROM free_photo_deliveries WHERE creator_id=$1 AND user_id=$2 AND day_bucket=$3 ORDER BY seq",
            creator_id, user_id, day,
        )
        return int(cnt or 0), rows


# ── Scenario A — ceiling 2, 0 sent, 2 concurrent → both succeed, 3rd fails ──


@_skip_no_pg
@pytest.mark.asyncio
async def test_concurrency_ceiling2_zero_sent_two_concurrent_both_succeed():
    from commerce.free_photo import authorize_free_photo
    test_pool = await _create_test_pool()
    # Patch both import locations so authorize uses our test_pool
    with patch("commerce.free_photo.get_pool", return_value=test_pool), patch("db.postgres.get_pool", return_value=test_pool):
        user_id = random.randint(8000000, 8999999)
        creator_name = f"test98_A_{user_id}"
        vault_ids = [f"vaultA_{user_id}_1", f"vaultA_{user_id}_2", f"vaultA_{user_id}_3"]
        creator_id = await _setup_creator_user(test_pool, creator_name=creator_name, user_id=user_id)
        await _add_approved_media(test_pool, creator_id, vault_ids)
        try:
            r1, r2 = await asyncio.gather(
                authorize_free_photo(creator_id, user_id, vault_ids[0]),
                authorize_free_photo(creator_id, user_id, vault_ids[1]),
            )
            assert r1.eligible and r2.eligible, f"both should succeed r1={r1} r2={r2}"
            assert r1.seq != r2.seq, f"seq must be distinct r1.seq={r1.seq} r2.seq={r2.seq}"
            assert {r1.seq, r2.seq} == {1, 2}

            day = r1.day_bucket
            cnt, rows = await _count_deliveries(test_pool, creator_id, user_id, day)
            assert cnt == 2, f"expected 2 rows, got {rows}"

            r3 = await authorize_free_photo(creator_id, user_id, vault_ids[2])
            assert not r3.eligible
            assert r3.reason == "quota_exhausted"
            assert r3.ceiling == 2

            cnt2, rows2 = await _count_deliveries(test_pool, creator_id, user_id, day)
            assert cnt2 == 2, "third must not create extra row"
        finally:
            await _teardown(test_pool, creator_id, user_id, vault_ids)
            await test_pool.close()


# ── Scenario B — ceiling 2, 1 sent, 2 concurrent → exactly one succeeds ──


@_skip_no_pg
@pytest.mark.asyncio
async def test_concurrency_ceiling2_one_remaining_two_concurrent_one_succeeds():
    from commerce.free_photo import authorize_free_photo, mark_free_photo_sent
    test_pool = await _create_test_pool()
    with patch("commerce.free_photo.get_pool", return_value=test_pool), patch("db.postgres.get_pool", return_value=test_pool):
        user_id = random.randint(9000000, 9099999)
        creator_name = f"test98_B_{user_id}"
        vault_ids = [f"vaultB_{user_id}_1", f"vaultB_{user_id}_2", f"vaultB_{user_id}_3"]
        creator_id = await _setup_creator_user(test_pool, creator_name=creator_name, user_id=user_id)
        await _add_approved_media(test_pool, creator_id, vault_ids)
        try:
            r0 = await authorize_free_photo(creator_id, user_id, vault_ids[0])
            assert r0.eligible
            # mark sent via direct SQL to avoid using global pool (patch not needed for mark helper, it uses get_pool which we patched)
            # Use test_pool to update status to sent directly
            async with test_pool.acquire() as conn:
                await conn.execute("UPDATE free_photo_deliveries SET status='sent', sent_at=NOW(), updated_at=NOW() WHERE id=$1", r0.reservation_id)

            day = r0.day_bucket
            cnt_sent, _ = await _count_deliveries(test_pool, creator_id, user_id, day, status="sent")
            assert cnt_sent == 1

            start = asyncio.Event()

            async def _worker(vid):
                await start.wait()
                return await authorize_free_photo(creator_id, user_id, vid)

            t1 = asyncio.create_task(_worker(vault_ids[1]))
            t2 = asyncio.create_task(_worker(vault_ids[2]))
            await asyncio.sleep(0.05)
            start.set()
            r1, r2 = await asyncio.gather(t1, t2)

            results = [r1, r2]
            succ = [r for r in results if r.eligible]
            fail = [r for r in results if not r.eligible]
            assert len(succ) == 1, f"expected 1 success, got {results}"
            assert len(fail) == 1
            assert fail[0].reason == "quota_exhausted"
            assert succ[0].seq in (1, 2)
            cnt_all, rows = await _count_deliveries(test_pool, creator_id, user_id, day)
            assert cnt_all == 2, f"rows={rows}"
            seqs = [r["seq"] for r in rows]
            assert len(seqs) == len(set(seqs)), f"duplicate seq {rows}"
        finally:
            await _teardown(test_pool, creator_id, user_id, vault_ids)
            await test_pool.close()


# ── Scenario C — purchased ceiling 4, 5 concurrent → 4 succeed ──


@_skip_no_pg
@pytest.mark.asyncio
async def test_concurrency_purchased_ceiling4_five_concurrent_four_succeed():
    from commerce.free_photo import authorize_free_photo
    test_pool = await _create_test_pool()
    with patch("commerce.free_photo.get_pool", return_value=test_pool), patch("db.postgres.get_pool", return_value=test_pool):
        user_id = random.randint(9100000, 9199999)
        creator_name = f"test98_C_{user_id}"
        vault_ids = [f"vaultC_{user_id}_{i}" for i in range(5)]
        creator_id = await _setup_creator_user(test_pool, creator_name=creator_name, user_id=user_id)
        await _add_approved_media(test_pool, creator_id, vault_ids)
        await _add_purchased_offer(test_pool, creator_id, user_id)
        try:
            start = asyncio.Event()

            async def _w(vid):
                await start.wait()
                return await authorize_free_photo(creator_id, user_id, vid)

            tasks = [asyncio.create_task(_w(vid)) for vid in vault_ids]
            await asyncio.sleep(0.05)
            start.set()
            results = await asyncio.gather(*tasks)

            succ = [r for r in results if r.eligible]
            fail = [r for r in results if not r.eligible]
            assert len(succ) == 4, f"expected 4 successes, got {results}"
            assert len(fail) == 1
            assert fail[0].reason == "quota_exhausted"
            assert fail[0].ceiling == 4
            seqs = {r.seq for r in succ}
            assert seqs == {1, 2, 3, 4}

            day = succ[0].day_bucket
            cnt, rows = await _count_deliveries(test_pool, creator_id, user_id, day)
            assert cnt == 4
            db_seqs = [r["seq"] for r in rows]
            assert set(db_seqs) == {1, 2, 3, 4}

            extra = f"vaultC_{user_id}_extra"
            async with test_pool.acquire() as conn:
                await conn.execute("INSERT INTO free_media_pool (creator_id, vault_item_id, status) VALUES ($1,$2,'approved') ON CONFLICT DO NOTHING", creator_id, extra)
            r6 = await authorize_free_photo(creator_id, user_id, extra)
            assert not r6.eligible
            assert r6.reason == "quota_exhausted"
            vault_ids.append(extra)
        finally:
            await _teardown(test_pool, creator_id, user_id, vault_ids)
            await test_pool.close()


# ── Scenario D — concurrent duplicate same media → one reservation ──


@_skip_no_pg
@pytest.mark.asyncio
async def test_concurrency_duplicate_same_media_one_reservation():
    from commerce.free_photo import authorize_free_photo
    test_pool = await _create_test_pool()
    with patch("commerce.free_photo.get_pool", return_value=test_pool), patch("db.postgres.get_pool", return_value=test_pool):
        user_id = random.randint(9200000, 9299999)
        creator_name = f"test98_D_{user_id}"
        vault_id = f"vaultD_{user_id}_same"
        creator_id = await _setup_creator_user(test_pool, creator_name=creator_name, user_id=user_id)
        await _add_approved_media(test_pool, creator_id, [vault_id])
        try:
            start = asyncio.Event()

            async def _w():
                await start.wait()
                return await authorize_free_photo(creator_id, user_id, vault_id)

            t1 = asyncio.create_task(_w())
            t2 = asyncio.create_task(_w())
            await asyncio.sleep(0.05)
            start.set()
            r1, r2 = await asyncio.gather(t1, t2)

            results = [r1, r2]
            succ = [r for r in results if r.eligible]
            dup = [r for r in results if not r.eligible]
            assert len(succ) == 1, f"expected 1 success, got {results}"
            assert len(dup) == 1
            assert dup[0].reason in ("already_pending", "already_sent_same_media")
            day = succ[0].day_bucket
            cnt, rows = await _count_deliveries(test_pool, creator_id, user_id, day)
            assert cnt == 1, f"rows={rows}"
            assert rows[0]["vault_item_id"] == vault_id
            assert rows[0]["status"] == "pending"
        finally:
            await _teardown(test_pool, creator_id, user_id, [vault_id])
            await test_pool.close()


# ── Regression: failed handling still correct with real DB ──


@_skip_no_pg
@pytest.mark.asyncio
async def test_failed_reuse_real_db():
    from commerce.free_photo import authorize_free_photo
    test_pool = await _create_test_pool()
    with patch("commerce.free_photo.get_pool", return_value=test_pool), patch("db.postgres.get_pool", return_value=test_pool):
        user_id = random.randint(9300000, 9399999)
        creator_name = f"test98_F_{user_id}"
        vault_ids = [f"vaultF_{user_id}_1", f"vaultF_{user_id}_2", f"vaultF_{user_id}_3"]
        creator_id = await _setup_creator_user(test_pool, creator_name=creator_name, user_id=user_id)
        await _add_approved_media(test_pool, creator_id, vault_ids)
        try:
            r1 = await authorize_free_photo(creator_id, user_id, vault_ids[0])
            assert r1.eligible
            async with test_pool.acquire() as conn:
                await conn.execute("UPDATE free_photo_deliveries SET status='failed', updated_at=NOW() WHERE id=$1", r1.reservation_id)
            r_retry = await authorize_free_photo(creator_id, user_id, vault_ids[0])
            assert r_retry.eligible
            assert r_retry.reservation_id == r1.reservation_id
            assert r_retry.reason == "reused_failed"
            async with test_pool.acquire() as conn:
                await conn.execute("UPDATE free_photo_deliveries SET status='sent', sent_at=NOW(), updated_at=NOW() WHERE id=$1", r_retry.reservation_id)
            r2 = await authorize_free_photo(creator_id, user_id, vault_ids[1])
            assert r2.eligible
            async with test_pool.acquire() as conn:
                await conn.execute("UPDATE free_photo_deliveries SET status='failed', updated_at=NOW() WHERE id=$1", r2.reservation_id)
            r3 = await authorize_free_photo(creator_id, user_id, vault_ids[2])
            assert r3.eligible
            assert r3.reason in ("reused_failed_slot", "eligible")
            day = r3.day_bucket
            cnt_sent, _ = await _count_deliveries(test_pool, creator_id, user_id, day, status="sent")
            assert cnt_sent == 1
            cnt_all, _ = await _count_deliveries(test_pool, creator_id, user_id, day)
            assert cnt_all == 2
        finally:
            await _teardown(test_pool, creator_id, user_id, vault_ids)
            await test_pool.close()
