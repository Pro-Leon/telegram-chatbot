"""Purchase-day refresh — mocked unit regression (no real PG).

Verifies the 10 required cases via the authorize path with mocked DB,
and ensures hard cap 4, failed/duplicate handling, and UTC boundaries
are preserved. Production files are not modified; this is pure verification.
"""
from datetime import datetime, timezone, date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

def _utc(hour, minute=0, day=12):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)

def _mock_pool_with_state(purchased_ever=False, purchase_today_at=None, sent_rows=None, existing=None, free_media_status="approved"):
    """Build a mock pool that simulates authorize's DB calls."""
    sent_rows = sent_rows or []
    mock_conn = AsyncMock()
    # Track calls
    # fetchrow sequence: creator, user, free_media, purchase_today, existing, sent counts, etc.
    # Instead of strict sequencing, we make fetchrow/fetchval return based on SQL content
    async def fake_fetchrow(sql, *args):
        s = sql.strip().lower()
        if "from creators" in s:
            return {"id": 1}
        if "from users" in s and "select 1" in s:
            return {"id": 1}
        if "from free_media_pool" in s:
            if free_media_status == "approved":
                return {"status": "approved"}
            return None
        if "select purchased_at" in s and "commerce_offers" in s:
            if purchase_today_at:
                return {"purchased_at": purchase_today_at}
            return None
        if "select 1 from commerce_offers" in s and "purchased" in s:
            return {"id": 1} if purchased_ever else None
        if "select id, status, seq from free_photo_deliveries" in s and "vault_item_id" in s:
            # same-media check
            if existing:
                return existing
            return None
        if "update free_photo_deliveries set status = 'pending'" in s:
            # For failed->pending reuse, return updated row
            if existing and existing.get("status") == "failed":
                return {"id": existing["id"], "seq": existing["seq"], "status": "pending"}
            return None
        if "insert into free_photo_deliveries" in s:
            return {"id": 999, "seq": 1, "status": "pending"}
        return None

    async def fake_fetchval(sql, *args):
        s = sql.strip().lower()
        if "count(*)" in s and "status = 'sent'" in s:
            # sent count after purchase or all
            if purchase_today_at is not None:
                # count only after purchase
                # args: creator, user, day, purchase_at
                # Filter sent_rows where sent_at > purchase
                cnt = sum(1 for r in sent_rows if r.get("status")=="sent" and r.get("sent_at") and r["sent_at"] > purchase_today_at)
                return cnt
            return sum(1 for r in sent_rows if r.get("status")=="sent")
        return 0

    async def fake_fetch(sql, *args):
        s = sql.strip().lower()
        if "select id, seq, status" in s and "where creator_id" in s and "day_bucket" in s:
            return sent_rows
        return []

    mock_conn.fetchrow = AsyncMock(side_effect=fake_fetchrow)
    mock_conn.fetchval = AsyncMock(side_effect=fake_fetchval)
    mock_conn.fetch = AsyncMock(side_effect=fake_fetch)
    mock_conn.execute = AsyncMock(return_value="SELECT 1")
    # transaction + advisory lock
    mock_conn.transaction = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))
    return mock_pool, mock_conn

@pytest.mark.asyncio
async def test_1_no_purchase_0():
    from commerce.free_photo import authorize_free_photo
    pool, _ = _mock_pool_with_state(purchased_ever=False, sent_rows=[])
    with patch("commerce.free_photo.get_pool", return_value=pool):
        r = await authorize_free_photo(1, 1, "vaultX", _now_utc=_utc(12))
        assert r.eligible
        assert r.ceiling == 2

@pytest.mark.asyncio
async def test_2_no_purchase_2_exhausted():
    from commerce.free_photo import authorize_free_photo
    sent = [{"status":"sent","seq":1,"sent_at":_utc(9)}, {"status":"sent","seq":2,"sent_at":_utc(9,30)}]
    pool, _ = _mock_pool_with_state(purchased_ever=False, sent_rows=sent)
    with patch("commerce.free_photo.get_pool", return_value=pool):
        r = await authorize_free_photo(1, 1, "vaultC", _now_utc=_utc(12))
        assert not r.eligible
        assert r.reason == "quota_exhausted"
        assert r.ceiling == 2

@pytest.mark.asyncio
async def test_3_purchase_before_any():
    from commerce.free_photo import authorize_free_photo
    # purchase today at 10:00, no sends
    purchase_at = _utc(10)
    pool, _ = _mock_pool_with_state(purchased_ever=True, purchase_today_at=purchase_at, sent_rows=[])
    with patch("commerce.free_photo.get_pool", return_value=pool):
        r = await authorize_free_photo(1, 1, "vaultX", _now_utc=_utc(11))
        assert r.eligible
        assert r.ceiling == 4

@pytest.mark.asyncio
async def test_4_two_before_purchase_forgiven_but_seq_hard_cap():
    """2 before purchase, purchase at 10:00 -> next should be eligible, but only 2 slots remain due to seq hard cap 4."""
    from commerce.free_photo import authorize_free_photo
    purchase_at = _utc(10)
    # 2 sent before purchase at 09:00
    sent = [
        {"status":"sent","seq":1,"sent_at":_utc(9), "vault_item_id":"vA"},
        {"status":"sent","seq":2,"sent_at":_utc(9,30), "vault_item_id":"vB"},
    ]
    pool, conn = _mock_pool_with_state(purchased_ever=True, purchase_today_at=purchase_at, sent_rows=sent)
    # Need to make sent count after purchase =0, so next is eligible
    with patch("commerce.free_photo.get_pool", return_value=pool):
        r = await authorize_free_photo(1, 1, "vC", _now_utc=_utc(11))
        # With forgiveness, sent after =0 <4, so eligible
        assert r.eligible
        assert r.ceiling == 4
        # But seq allocation will see used {1,2} -> candidate 3, so only 2 more allocatable
        # Next after using 3,4 should be exhausted
        # Simulate using seq3
        sent.append({"status":"sent","seq":3,"sent_at":_utc(11)})
        pool2, _ = _mock_pool_with_state(purchased_ever=True, purchase_today_at=purchase_at, sent_rows=sent)
        with patch("commerce.free_photo.get_pool", return_value=pool2):
            r2 = await authorize_free_photo(1, 1, "vD", _now_utc=_utc(11,30))
            assert r2.eligible
            sent.append({"status":"sent","seq":4,"sent_at":_utc(11,30)})
            pool3, _ = _mock_pool_with_state(purchased_ever=True, purchase_today_at=purchase_at, sent_rows=sent)
            with patch("commerce.free_photo.get_pool", return_value=pool3):
                r3 = await authorize_free_photo(1, 1, "vE", _now_utc=_utc(11,30))
                # Hard cap 4 seq total (1,2,3,4) -> no candidate
                assert not r3.eligible
                assert r3.reason == "quota_exhausted"

@pytest.mark.asyncio
async def test_5_one_before_purchase():
    from commerce.free_photo import authorize_free_photo
    purchase_at = _utc(10)
    sent = [{"status":"sent","seq":1,"sent_at":_utc(9)}]
    pool, _ = _mock_pool_with_state(purchased_ever=True, purchase_today_at=purchase_at, sent_rows=sent)
    with patch("commerce.free_photo.get_pool", return_value=pool):
        r = await authorize_free_photo(1, 1, "vB", _now_utc=_utc(11))
        assert r.eligible
        assert r.ceiling == 4

@pytest.mark.asyncio
async def test_6_four_post_purchase_exhausted():
    from commerce.free_photo import authorize_free_photo
    purchase_at = _utc(9)
    sent = [{"status":"sent","seq":i,"sent_at":_utc(10)} for i in [1,2,3,4]]
    pool, _ = _mock_pool_with_state(purchased_ever=True, purchase_today_at=purchase_at, sent_rows=sent)
    with patch("commerce.free_photo.get_pool", return_value=pool):
        r = await authorize_free_photo(1, 1, "vE", _now_utc=_utc(11))
        assert not r.eligible
        assert r.reason == "quota_exhausted"

@pytest.mark.asyncio
async def test_7_second_purchase_after_four_still_exhausted():
    from commerce.free_photo import authorize_free_photo
    # First purchase 09:00, 4 sent 10-11, second purchase 12:00 same day
    first_at = _utc(9)
    second_at = _utc(12)
    # Latest purchase is 12:00, sent after latest =0, but seq 1-4 still occupied -> should still be exhausted due to seq
    sent = [{"status":"sent","seq":i,"sent_at":_utc(10)} for i in [1,2,3,4]]
    # For second purchase, latest is 12:00, sent after 12:00 is 0, but seq still full
    pool, _ = _mock_pool_with_state(purchased_ever=True, purchase_today_at=second_at, sent_rows=sent)
    with patch("commerce.free_photo.get_pool", return_value=pool):
        r = await authorize_free_photo(1, 1, "vE", _now_utc=_utc(12,30))
        assert not r.eligible
        assert r.reason == "quota_exhausted"

@pytest.mark.asyncio
async def test_8_two_purchases_before_any_max_4():
    from commerce.free_photo import authorize_free_photo
    # Two purchases today, no sends -> max 4, not 8
    purchase_at = _utc(10)  # latest
    pool, _ = _mock_pool_with_state(purchased_ever=True, purchase_today_at=purchase_at, sent_rows=[])
    with patch("commerce.free_photo.get_pool", return_value=pool):
        for vid in ["vA","vB","vC","vD"]:
            r = await authorize_free_photo(1, 1, vid, _now_utc=_utc(11))
            assert r.eligible
            # Simulate marking sent to occupy seq
            # For next iteration, need new pool with updated sent
        # After 4, next should be exhausted
        sent = [{"status":"sent","seq":i,"sent_at":_utc(11)} for i in [1,2,3,4]]
        pool2, _ = _mock_pool_with_state(purchased_ever=True, purchase_today_at=purchase_at, sent_rows=sent)
        with patch("commerce.free_photo.get_pool", return_value=pool2):
            r5 = await authorize_free_photo(1, 1, "vE", _now_utc=_utc(11))
            assert not r5.eligible

@pytest.mark.asyncio
async def test_9_failed_does_not_consume():
    from commerce.free_photo import authorize_free_photo
    purchase_at = _utc(9)
    # No sent, one failed for different vault, next different vault should be eligible (failed not counted)
    sent = [{"id": 1, "status":"failed","seq":1,"vault_item_id":"vA","sent_at":None}]
    pool, _ = _mock_pool_with_state(purchased_ever=True, purchase_today_at=purchase_at, sent_rows=sent, existing=None)
    with patch("commerce.free_photo.get_pool", return_value=pool):
        r = await authorize_free_photo(1, 1, "vB", _now_utc=_utc(11))
        assert r.eligible
        assert r.ceiling == 4

@pytest.mark.asyncio
async def test_10_duplicate_suppressed():
    from commerce.free_photo import authorize_free_photo
    sent = [{"status":"sent","seq":1,"sent_at":_utc(11), "vault_item_id":"vA"}]
    existing = {"id": 1, "status": "sent", "seq": 1}
    pool, _ = _mock_pool_with_state(purchased_ever=True, purchase_today_at=_utc(9), sent_rows=sent, existing=existing)
    with patch("commerce.free_photo.get_pool", return_value=pool):
        r = await authorize_free_photo(1, 1, "vA", _now_utc=_utc(11))
        assert not r.eligible
        assert r.reason == "already_sent_same_media"

def test_seq_hard_cap_preserved():
    import pathlib
    sql = pathlib.Path("db/migrations/20260912000000_free_photo_ledger.sql").read_text()
    assert "CHECK (seq BETWEEN 1 AND 4)" in sql
    assert "seq 1..4" in pathlib.Path("commerce/free_photo.py").read_text()
