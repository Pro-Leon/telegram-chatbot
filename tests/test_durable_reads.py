"""Phase 2.2 durable reads tests (mocked pool, no DB)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest


class _FakeConn:
    def __init__(self, fetch=None, fetchrow=None):
        self._fetch = fetch or []
        self._fetchrow = fetchrow

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch(self, *a, **k):
        if isinstance(self._fetch, Exception):
            raise self._fetch
        return self._fetch

    async def fetchrow(self, *a, **k):
        if isinstance(self._fetchrow, Exception):
            raise self._fetchrow
        return self._fetchrow


def _pool_with(conn_or_exc):
    pool = AsyncMock()

    class _Acquire:
        async def __aenter__(self):
            if isinstance(conn_or_exc, Exception):
                raise conn_or_exc
            return conn_or_exc

        async def __aexit__(self, *a):
            return False

    pool.acquire = lambda: _Acquire()
    return pool


def _patch_pool(conn_or_exc):
    import db.postgres as _pg

    return patch.object(_pg, "get_pool", new=AsyncMock(return_value=_pool_with(conn_or_exc)))


def _row(id, direction, content, creator_id=42, minute=0):
    return {
        "id": id,
        "direction": direction,
        "content": content,
        "created_at": datetime(2026, 9, 24, 6, minute, tzinfo=UTC),
        "creator_id": creator_id,
    }


@pytest.mark.asyncio
async def test_recent_miss_not_degraded():
    from db.postgres import get_recent_messages_durable

    with _patch_pool(_FakeConn(fetch=[])):
        rows, degraded, source = await get_recent_messages_durable(7, creator_id=42)
    assert rows == [] and degraded is False and source == "miss"


@pytest.mark.asyncio
async def test_recent_error_degraded():
    from db.postgres import get_recent_messages_durable

    with _patch_pool(RuntimeError("db down")):
        rows, degraded, source = await get_recent_messages_durable(7, creator_id=42)
    assert rows == [] and degraded is True and source == "error"


@pytest.mark.asyncio
async def test_recent_legacy_merged_ordered():
    from db.postgres import get_recent_messages_durable

    rows_in = [
        _row(3, "outbound", "new reply", creator_id=42, minute=3),
        _row(2, "inbound", "legacy hi", creator_id=None, minute=2),
        _row(1, "inbound", "old hi", creator_id=42, minute=1),
    ]
    with _patch_pool(_FakeConn(fetch=rows_in)):
        rows, degraded, source = await get_recent_messages_durable(7, creator_id=42)
    assert degraded is False and source == "hit+legacy"
    assert [r["id"] for r in rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_recent_tiebreak_same_second_deterministic():
    from db.postgres import get_recent_messages_durable

    rows_in = [
        _row(9, "outbound", "b", minute=5),
        _row(7, "inbound", "a", minute=5),
    ]
    with _patch_pool(_FakeConn(fetch=rows_in)):
        rows, degraded, _ = await get_recent_messages_durable(7, creator_id=42)
    assert degraded is False
    assert [r["id"] for r in rows] == [7, 9]


@pytest.mark.asyncio
async def test_recent_still_requires_creator():
    from db.postgres import get_recent_messages, get_recent_messages_durable

    with pytest.raises(ValueError):
        await get_recent_messages(7, creator_id=None)
    rows, degraded, source = await get_recent_messages_durable(7, creator_id=None)
    assert rows == [] and degraded is True and source == "error"


@pytest.mark.asyncio
async def test_summary_miss_vs_error():
    from db.postgres import get_latest_summary_durable, get_latest_summary_with_age_durable

    with _patch_pool(_FakeConn(fetchrow=None)):
        s, d, src = await get_latest_summary_durable(7, creator_id=42)
        assert (s, d, src) == (None, False, "miss")
        (s2, a2), d2, src2 = await get_latest_summary_with_age_durable(7, creator_id=42)
        assert (s2, a2, d2, src2) == (None, None, False, "miss")
    with _patch_pool(RuntimeError("db down")):
        s, d, src = await get_latest_summary_durable(7, creator_id=42)
        assert (s, d, src) == (None, True, "error")


@pytest.mark.asyncio
async def test_user_miss_vs_error():
    from db.postgres import get_user_durable

    with _patch_pool(_FakeConn(fetchrow={"id": 7, "first_name": "Al"})):
        row, d, src = await get_user_durable(7)
        assert (d, src) == (False, "hit") and row["first_name"] == "Al"
    with _patch_pool(_FakeConn(fetchrow=None)):
        row, d, src = await get_user_durable(7)
        assert (row, d, src) == (None, False, "miss")
    with _patch_pool(RuntimeError("db down")):
        row, d, src = await get_user_durable(7)
        assert (row, d, src) == (None, True, "error")


@pytest.mark.asyncio
async def test_gatherer_messages_safe_propagates_empty_on_error():
    from context_engine.gatherer import ConversationHistorySource, GathererConfig

    src = ConversationHistorySource()
    cfg = GathererConfig(user_id=7, creator_id=42, current_message="hi")
    with _patch_pool(RuntimeError("db down")):
        assert await src._get_messages_safe(cfg) == []
        assert await src._get_summary_safe(cfg) is None


@pytest.mark.asyncio
async def test_assembler_marks_degraded_on_error():
    from memory.context_assembler import build_llm_context

    with _patch_pool(RuntimeError("db down")):
        ctx = await build_llm_context(creator_id=42, user_id=7)
    assert ctx.history_degraded is True
    assert ctx.recent_messages == []


@pytest.mark.asyncio
async def test_assembler_clean_miss_not_degraded():
    from memory.context_assembler import build_llm_context

    with _patch_pool(_FakeConn(fetch=[], fetchrow=None)):
        ctx = await build_llm_context(creator_id=42, user_id=7)
    assert ctx.history_degraded is False
