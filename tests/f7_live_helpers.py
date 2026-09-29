"""Shared helpers for F7 live-proof files (staging only, never faked)."""

from __future__ import annotations

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def f7_pools():
    """Reset module-global PG/Redis clients so each test binds the current loop."""
    import db.postgres as pg
    import db.redis as rdb

    for mod in (pg, rdb):
        try:
            await mod.close_pool() if hasattr(mod, "close_pool") else await mod.close_redis()
        except Exception:  # noqa: BLE001 — stale-loop clients are expected
            pass
        try:
            if hasattr(mod, "_pool"):
                mod._pool = None
            if hasattr(mod, "_client"):
                mod._client = None
        except Exception:  # noqa: BLE001
            pass
    yield
    for mod in (pg, rdb):
        try:
            await mod.close_pool() if hasattr(mod, "close_pool") else await mod.close_redis()
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(mod, "_pool"):
                mod._pool = None
            if hasattr(mod, "_client"):
                mod._client = None
        except Exception:  # noqa: BLE001
            pass


async def require_staging():
    """Return (pool, redis) on live staging, else skip."""
    import db.postgres as pg
    import db.redis as rdb

    try:
        pool = await pg.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        r = await rdb.get_redis()
        assert await r.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"staging unavailable: {exc!r}")
    return pool, r
