"""P3.3.13 — provider sealing tests (mocked provider + mocked DB, no live I/O).

Covers ``commerce.opportunity_sealing``: exact candidate/live match,
creator isolation, CUID mapping, canonical membership (reordered still
matches), vault drift, price/currency/allow_download drift, status
gates, malformed/timeout/5xx/429 mapping, two-step TOCTOU (second
fetch drift/status → no insert), verified snapshot fields (hash from
live, media_count, price), reason JSON provenance, idempotency per
sealed identity, isolation across definitions/Drops/creators,
persistence failure behavior, retry semantics, synthetic-id boundary,
mirror non-authority, checkout-URL non-authority, and static/mechanical
boundary (no LLM/worker/Redis/legacy ranking imports, lock key shape).
"""

import dataclasses
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from commerce.drop_reconciliation import VerifiedLive
from commerce.opportunity import candidate_from_definition
from commerce.opportunity_sealing import (
    ALLOW_DOWNLOAD_DRIFT,
    CREATOR_MISMATCH,
    CUID_NOT_MAPPED,
    CURRENCY_DRIFT,
    DROP_NOT_FOUND,
    LOCAL_PERSISTENCE_FAILURE,
    MALFORMED_PROVIDER_DATA,
    MEMBERSHIP_DRIFT,
    PRICE_DRIFT,
    PROVIDER_DRIFT,
    PROVIDER_NOT_FOUND,
    PROVIDER_REJECTED,
    PROVIDER_STATUS_NOT_APPROVED,
    PROVIDER_TIMEOUT_AMBIGUOUS,
    RATE_LIMITED,
    SEALED,
    SealResult,
    SealVerificationResult,
    verify_candidate_against_live,
)

pytestmark = [pytest.mark.unit]

MODULE_PATH = Path(__file__).parent.parent / "commerce" / "opportunity_sealing.py"


# ---------------------------------------------------------------------------
# Helpers: candidate / live payloads / fakes
# ---------------------------------------------------------------------------


def _definition(**over):
    row = {
        "id": 11,
        "creator_id": 1,
        "stable_key": "black-lingerie",
        "version": 1,
        "offer_type": "SINGLE",
        "canonical_vault_item_ids": ["V1", "V2"],
        "family_id": None,
        "price_minor": 1999,
        "currency": "USD",
        "allow_download": True,
        "status": "active",
    }
    row.update(over)
    return row


def _candidate(**over):
    mapped = over.pop("mapped_drop_ids", ["drop_abc"])
    return candidate_from_definition(1, 10, _definition(**over), mapped_drop_ids=mapped)


def _live_payload(**over):
    base = {
        "id": "drop_abc",
        "price": 19.99,
        "currency": "USD",
        "status": "APPROVED",
        "allowDownload": True,
        "media": [{"vault_item_id": "V1"}, {"vault_item_id": "V2"}],
        "buyUrl": "https://www.dropfans.io/buy/drop_abc",
        "mediaCount": 2,
    }
    base.update(over)
    return base


def _verified(**over):
    from commerce.drop_reconciliation import verify_live_drop

    return verify_live_drop(_live_payload(**over))


class FakeConn:
    def __init__(self, existing_rows=None, fail_fetchrow=False, fail_execute=False):
        self.existing_rows = existing_rows or []
        self.fail_fetchrow = fail_fetchrow
        self.fail_execute = fail_execute
        self.calls = []
        self.inserts = []

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        if self.fail_execute and "pg_advisory" in sql:
            raise RuntimeError("lock failed")
        return "SELECT 1"

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "FROM commerce_offers" in sql and "dropfans_product_id" in sql:
            # Filter by the requested CUID (args[2] when present) to respect isolation tests
            if len(args) >= 3 and isinstance(args[2], str):
                wanted = args[2].strip()
                return [
                    r
                    for r in self.existing_rows
                    if str(r.get("dropfans_product_id", "")).strip() == wanted
                ]
            return list(self.existing_rows)
        return []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if self.fail_fetchrow:
            raise RuntimeError("insert failed")
        if "INSERT INTO commerce_offers" in sql:
            # args: creator,user,product,link,price,currency,reason,created_by,CUID,vault_ids,media_count,hash
            row = {
                "id": 999,
                "creator_id": args[0],
                "user_id": args[1],
                "product_id": args[2],
                "link": args[3],
                "price_minor": args[4],
                "currency": args[5],
                "reason": args[6],
                "created_by": args[7],
                "dropfans_product_id": args[8],
                "vault_item_ids": args[9],
                "media_count": args[10],
                "drop_content_hash": args[11],
                "state": "pending",
            }
            self.inserts.append(row)
            return row
        return None

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self):
                return outer

            async def __aexit__(self, *a):
                return False

        return _Tx()


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Acq:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _Acq()


def _install(monkeypatch, conn, get_drop_fn=None):
    import commerce.opportunity_sealing as mod

    monkeypatch.setattr(mod, "_now_iso", lambda: "2026-06-01T12:00:00+00:00")
    # Mock get_pool to return FakePool
    import db.postgres as pg

    monkeypatch.setattr(pg, "get_pool", AsyncMock(return_value=FakePool(conn)))
    # Also patch the direct import inside sealing module (it imports get_pool inside function, so pg mock suffices)
    # Patch dropfans find for synthetic resolution to avoid extra DB call complexity
    import db.dropfans as ddb

    monkeypatch.setattr(ddb, "find_dropfans_product", AsyncMock(return_value=None))
    return get_drop_fn


# ---------------------------------------------------------------------------
# 1-6: exact match and canonical handling
# ---------------------------------------------------------------------------


class TestExactMatch:
    def test_exact_match_ok(self):
        cand = _candidate()
        live = _verified()
        assert verify_candidate_against_live(cand, "drop_abc", live).ok is True

    def test_reordered_membership_still_matches(self):
        cand = _candidate(canonical_vault_item_ids=["V1", "V2"])
        live = _verified(media=[{"vault_item_id": "V2"}, {"vault_item_id": "V1"}])
        assert verify_candidate_against_live(cand, "drop_abc", live).ok is True

    def test_provider_drop_id_mismatch(self):
        cand = _candidate()
        live = _verified(id="drop_other")
        res = verify_candidate_against_live(cand, "drop_abc", live)
        assert not res.ok and res.subreason == "CUID_MISMATCH"

    @pytest.mark.parametrize("price", [1998, 2000, 0, 75000])
    def test_price_mismatch(self, price):
        cand = _candidate(price_minor=1999)
        live = _verified(price=price / 100 if price != 1999 else 20.00)
        # 1999 vs live 1998 etc -> drift; exact 1999 case would pass but we test drift
        if price == 1999:
            assert verify_candidate_against_live(cand, "drop_abc", live).ok is True
        else:
            res = verify_candidate_against_live(cand, "drop_abc", live)
            assert not res.ok and res.subreason == PRICE_DRIFT

    def test_currency_mismatch(self):
        cand = _candidate(currency="USD")
        # verify_live_drop rejects non-USD before drift check, so craft VerifiedLive directly
        live2 = VerifiedLive("drop_abc", ["V1", "V2"], 1999, "EUR", True, "APPROVED")
        res2 = verify_candidate_against_live(cand, "drop_abc", live2)
        assert not res2.ok and res2.subreason == CURRENCY_DRIFT

    def test_allow_download_mismatch(self):
        cand = _candidate(allow_download=True)
        live = VerifiedLive("drop_abc", ["V1", "V2"], 1999, "USD", False, "APPROVED")
        res = verify_candidate_against_live(cand, "drop_abc", live)
        assert not res.ok and res.subreason == ALLOW_DOWNLOAD_DRIFT


class TestCreatorAndMapping:
    @pytest.mark.asyncio
    async def test_creator_mismatch(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()

        async def get_drop(c, cuid):
            return _live_payload()

        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        res = await seal_ranked_candidate(cand, None, 2, 10, "drop_abc", get_drop=get_drop)
        assert res.status == CREATOR_MISMATCH

    @pytest.mark.asyncio
    async def test_chosen_cuid_not_mapped(self, monkeypatch):
        cand = _candidate(mapped_drop_ids=["drop_abc"])
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            return _live_payload()

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_other", get_drop=get_drop)
        assert res.status == PROVIDER_DRIFT and res.subreason == CUID_NOT_MAPPED
        assert conn.inserts == []

    def test_pure_helper_cuid_not_mapped(self):
        cand = _candidate(mapped_drop_ids=["drop_abc"])
        live = _verified()
        res = verify_candidate_against_live(cand, "drop_other", live)
        assert not res.ok and res.subreason == CUID_NOT_MAPPED


class TestMembershipDrift:
    @pytest.mark.parametrize(
        "media",
        [
            [{"vault_item_id": "V1"}, {"vault_item_id": "V2"}, {"vault_item_id": "V3"}],  # extra
            [{"vault_item_id": "V1"}],  # missing
            [{"vault_item_id": "V1"}, {"vault_item_id": "V9"}],  # replacement
        ],
    )
    def test_membership_drift(self, media):
        cand = _candidate()
        live = _verified(media=media)
        res = verify_candidate_against_live(cand, "drop_abc", live)
        assert not res.ok and res.subreason == MEMBERSHIP_DRIFT


# ---------------------------------------------------------------------------
# 13-19: status and provider failures
# ---------------------------------------------------------------------------


class TestStatusAndProviderFailures:
    def test_pending_status_rejected(self):
        cand = _candidate()
        live = VerifiedLive(
            "drop_abc", ["V1", "V2"], 1999, "USD", True, "PENDING", buy_url="https://x"
        )
        res = verify_candidate_against_live(cand, "drop_abc", live)
        assert not res.ok and res.subreason == PROVIDER_STATUS_NOT_APPROVED

    def test_rejected_status_rejected(self):
        cand = _candidate()
        live = VerifiedLive("drop_abc", ["V1", "V2"], 1999, "USD", True, "REJECTED")
        res = verify_candidate_against_live(cand, "drop_abc", live)
        assert not res.ok and res.subreason == PROVIDER_STATUS_NOT_APPROVED

    @pytest.mark.asyncio
    async def test_malformed_response(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            return {"id": "drop_abc"}  # missing media, price, status

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == MALFORMED_PROVIDER_DATA
        assert conn.inserts == []

    @pytest.mark.asyncio
    async def test_provider_404(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate
        from integrations.dropfans.errors import DropfansNotFoundError

        async def get_drop(c, cuid):
            raise DropfansNotFoundError("get_drop", "not found", 404)

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == PROVIDER_NOT_FOUND and res.subreason == DROP_NOT_FOUND

    @pytest.mark.asyncio
    async def test_provider_timeout(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate
        from integrations.dropfans.errors import DropfansTimeoutError

        async def get_drop(c, cuid):
            raise DropfansTimeoutError("get_drop", "timeout")

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == PROVIDER_TIMEOUT_AMBIGUOUS
        assert conn.inserts == []

    @pytest.mark.asyncio
    async def test_provider_5xx(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate
        from integrations.dropfans.errors import DropfansServerError

        async def get_drop(c, cuid):
            raise DropfansServerError("get_drop", "boom", 500)

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == PROVIDER_TIMEOUT_AMBIGUOUS

    @pytest.mark.asyncio
    async def test_provider_429(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate
        from integrations.dropfans.errors import DropfansRateLimitError

        async def get_drop(c, cuid):
            raise DropfansRateLimitError("get_drop", "limit", 429)

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == PROVIDER_TIMEOUT_AMBIGUOUS and res.subreason == RATE_LIMITED

    @pytest.mark.asyncio
    async def test_creator_mismatch_via_auth_error(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate
        from integrations.dropfans.errors import DropfansAuthenticationError

        async def get_drop(c, cuid):
            raise DropfansAuthenticationError("get_drop", "bad key", 401)

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == CREATOR_MISMATCH


# ---------------------------------------------------------------------------
# 20-21: TOCTOU second-fetch drift/status
# ---------------------------------------------------------------------------


class TestTOCTOU:
    @pytest.mark.asyncio
    async def test_second_fetch_price_drift_no_insert(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        calls = []

        async def get_drop(c, cuid):
            calls.append(len(calls))
            if len(calls) == 1:
                return _live_payload(price=19.99)
            return _live_payload(price=20.99)  # drift to 2099

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == PROVIDER_DRIFT and res.subreason == PRICE_DRIFT
        assert conn.inserts == []
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_second_fetch_membership_drift_no_insert(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        calls = []

        async def get_drop(c, cuid):
            calls.append(1)
            if len(calls) == 1:
                return _live_payload(media=[{"vault_item_id": "V1"}, {"vault_item_id": "V2"}])
            return _live_payload(media=[{"vault_item_id": "V1"}, {"vault_item_id": "V9"}])

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == PROVIDER_DRIFT and res.subreason == MEMBERSHIP_DRIFT
        assert conn.inserts == []

    @pytest.mark.asyncio
    async def test_second_fetch_status_rejection_no_insert(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        calls = []

        async def get_drop(c, cuid):
            calls.append(1)
            if len(calls) == 1:
                return _live_payload(status="APPROVED")
            return _live_payload(status="PENDING")

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == PROVIDER_REJECTED
        assert conn.inserts == []


# ---------------------------------------------------------------------------
# 22-28: snapshot fields
# ---------------------------------------------------------------------------


class TestSnapshotIntegrity:
    @pytest.mark.asyncio
    async def test_verified_snapshot_stores_exact_live_set(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            return _live_payload(media=[{"vault_item_id": "V2"}, {"vault_item_id": "V1"}])

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == SEALED
        assert res.offer is not None
        assert res.offer["vault_item_ids"] == ["V1", "V2"]  # canonical, not presentation
        assert res.offer["dropfans_product_id"] == "drop_abc"

    @pytest.mark.asyncio
    async def test_hash_based_on_verified_live_not_mirror(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate
        from commerce.vault_sets import drop_content_hash

        async def get_drop(c, cuid):
            return _live_payload()

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        expected = drop_content_hash(["V1", "V2"])
        assert res.offer["drop_content_hash"] == expected

    @pytest.mark.asyncio
    async def test_media_count_and_price_persisted_exactly(self, monkeypatch):
        cand = _candidate(price_minor=1999)
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            return _live_payload(price=19.99, currency="USD")

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.offer["media_count"] == 2
        assert res.offer["price_minor"] == 1999
        assert res.offer["currency"] == "USD"

    @pytest.mark.asyncio
    async def test_reason_round_trip(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            return _live_payload()

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        reason = json.loads(res.offer["reason"])
        assert reason["definition_id"] == 11
        assert reason["definition_version"] == 1
        assert reason["stable_key"] == "black-lingerie"
        assert reason["verified_hash"] == res.offer["drop_content_hash"]
        assert reason["allow_download"] is True
        assert reason["verifier_version"] == "p33.13.v1"
        assert "sealed_at" in reason and "verified_at" in reason


# ---------------------------------------------------------------------------
# 29-32: idempotency / isolation
# ---------------------------------------------------------------------------


class TestIdempotencyIsolation:
    @pytest.mark.asyncio
    async def test_same_sealed_identity_idempotent_no_duplicate(self, monkeypatch):
        cand = _candidate()
        existing_reason = json.dumps(
            {
                "v": 1,
                "definition_id": 11,
                "definition_version": 1,
                "stable_key": "black-lingerie",
                "sealed_at": "2026-06-01T12:00:00+00:00",
                "verified_at": "2026-06-01T12:00:00+00:00",
                "verified_hash": "abc",
                "allow_download": True,
                "verifier_version": "p33.13.v1",
            }
        )
        existing_row = {
            "id": 1,
            "reason": existing_reason,
            "dropfans_product_id": "drop_abc",
            "state": "pending",
        }
        conn = FakeConn(existing_rows=[existing_row])
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            return _live_payload()

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == SEALED
        assert res.offer["id"] == 1
        assert conn.inserts == []  # no duplicate insert

    @pytest.mark.asyncio
    async def test_same_drop_different_definitions_independent(self, monkeypatch):
        # Definition A (id 11) already sealed; sealing definition B (id 12) same CUID must create new row
        cand_b = candidate_from_definition(
            1, 10, _definition(id=12, stable_key="b"), mapped_drop_ids=["drop_abc"]
        )
        existing_reason = json.dumps(
            {
                "v": 1,
                "definition_id": 11,
                "definition_version": 1,
                "stable_key": "a",
                "sealed_at": "2026-06-01T12:00:00+00:00",
                "verified_at": "2026-06-01T12:00:00+00:00",
                "verified_hash": "abc",
                "allow_download": True,
                "verifier_version": "p33.13.v1",
            }
        )
        conn = FakeConn(
            existing_rows=[
                {
                    "id": 1,
                    "reason": existing_reason,
                    "dropfans_product_id": "drop_abc",
                    "state": "pending",
                }
            ]
        )
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            # Live matches both (same vault set/price) but we seal B
            return _live_payload()

        res = await seal_ranked_candidate(cand_b, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == SEALED
        assert res.offer["id"] == 999
        assert len(conn.inserts) == 1

    @pytest.mark.asyncio
    async def test_same_definition_different_drops_independent(self, monkeypatch):
        cand = _candidate(mapped_drop_ids=["drop_abc", "drop_xyz"])
        existing_reason = json.dumps(
            {
                "v": 1,
                "definition_id": 11,
                "definition_version": 1,
                "stable_key": "black-lingerie",
                "sealed_at": "2026-06-01T12:00:00+00:00",
                "verified_at": "2026-06-01T12:00:00+00:00",
                "verified_hash": "abc",
                "allow_download": True,
                "verifier_version": "p33.13.v1",
            }
        )
        conn = FakeConn(
            existing_rows=[
                {
                    "id": 1,
                    "reason": existing_reason,
                    "dropfans_product_id": "drop_abc",
                    "state": "pending",
                }
            ]
        )
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            # Need to handle both CUIDs; sealing drop_xyz so live id must match
            return _live_payload(id=cuid)

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_xyz", get_drop=get_drop)
        assert res.status == SEALED
        assert res.offer["dropfans_product_id"] == "drop_xyz"

    @pytest.mark.asyncio
    async def test_same_cuid_different_creators_isolated(self, monkeypatch):
        # Existing row for creator 1 should not block creator 2
        cand = candidate_from_definition(
            2, 10, _definition(creator_id=2, id=11), mapped_drop_ids=["drop_abc"]
        )
        existing_reason = json.dumps(
            {
                "v": 1,
                "definition_id": 11,
                "definition_version": 1,
                "stable_key": "black-lingerie",
                "sealed_at": "2026-06-01T12:00:00+00:00",
                "verified_at": "2026-06-01T12:00:00+00:00",
                "verified_hash": "abc",
                "allow_download": True,
                "verifier_version": "p33.13.v1",
            }
        )
        conn = FakeConn(
            existing_rows=[
                {
                    "id": 1,
                    "reason": existing_reason,
                    "dropfans_product_id": "drop_abc",
                    "state": "pending",
                }
            ]
        )
        # FakePool will be queried with creator 2, user 10, CUID drop_abc — existing row is for creator 1 so fetch returns same row in fake; need to simulate creator isolation via fake that filters by creator
        # Our FakeConn fetch returns existing_rows regardless of args, so we simulate isolation by using empty for creator 2: override fetch
        original_fetch = conn.fetch

        async def filtered_fetch(sql, *args):
            if args and args[0] == 2:
                return []
            return await original_fetch(sql, *args)

        conn.fetch = filtered_fetch
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            assert c == 2
            return _live_payload(id="drop_abc")

        res = await seal_ranked_candidate(cand, None, 2, 10, "drop_abc", get_drop=get_drop)
        assert res.status == SEALED


# ---------------------------------------------------------------------------
# 33-35: persistence failure and retry
# ---------------------------------------------------------------------------


class TestPersistenceAndRetry:
    @pytest.mark.asyncio
    async def test_local_persistence_failure_leaves_no_partial_row(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn(fail_fetchrow=True)
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            return _live_payload()

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == LOCAL_PERSISTENCE_FAILURE
        assert conn.inserts == []

    @pytest.mark.asyncio
    async def test_retry_after_persistence_failure_reruns_both_verifications(self, monkeypatch):
        cand = _candidate()
        # First attempt fails on insert
        conn1 = FakeConn(fail_fetchrow=True)
        _install(monkeypatch, conn1)
        from commerce.opportunity_sealing import seal_ranked_candidate

        calls = []

        async def get_drop(c, cuid):
            calls.append(cuid)
            return _live_payload()

        res1 = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res1.status == LOCAL_PERSISTENCE_FAILURE
        assert len(calls) == 2  # both verifications ran

        # Second attempt succeeds, both verifications run again
        calls.clear()
        conn2 = FakeConn()
        _install(monkeypatch, conn2)

        res2 = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res2.status == SEALED
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_retry_after_ambiguous_does_not_persist_stale(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate
        from integrations.dropfans.errors import DropfansTimeoutError

        async def get_drop_timeout(c, cuid):
            raise DropfansTimeoutError("get_drop", "timeout")

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop_timeout)
        assert res.status == PROVIDER_TIMEOUT_AMBIGUOUS
        assert conn.inserts == []

        async def get_drop_ok(c, cuid):
            return _live_payload()

        res2 = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop_ok)
        assert res2.status == SEALED


# ---------------------------------------------------------------------------
# 36-39: synthetic / mirror / URL / ranking non-authority
# ---------------------------------------------------------------------------


class TestBoundaries:
    def test_no_synthetic_product_id_in_sealed_identity(self):
        from commerce.opportunity_sealing import _sealed_identity, _synthetic_product_id

        ident = _sealed_identity(1, 10, 11, 1, "drop_abc")
        assert ident == (1, 10, 11, 1, "drop_abc")
        assert str(_synthetic_product_id("drop_abc")) not in str(ident)

    @pytest.mark.asyncio
    async def test_mirror_membership_not_used(self, monkeypatch):
        # Candidate vault set is [V1,V2]; mirror would be different but sealer must ignore mirror
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            return _live_payload(media=[{"vault_item_id": "V1"}, {"vault_item_id": "V2"}])

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        assert res.status == SEALED
        assert res.offer["vault_item_ids"] == ["V1", "V2"]

    @pytest.mark.asyncio
    async def test_provider_url_not_authority(self, monkeypatch):
        cand = _candidate()
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            return _live_payload(
                buyUrl="https://evil.example/steal", buy_url="https://evil.example/steal"
            )

        res = await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        # Price still verified against candidate, URL is just convenience
        assert res.status == SEALED
        # Link is persisted but commercial terms come from verified_live, not URL
        assert res.offer["price_minor"] == 1999

    def test_ranking_module_still_pure(self):
        src = (Path(__file__).parent.parent / "commerce" / "opportunity_ranking.py").read_text(
            encoding="utf-8"
        )
        for tok in ("get_drop", "verify_live_drop", "commerce_offers", "pg_advisory"):
            assert tok not in src

    def test_opportunity_module_still_pure(self):
        src = (Path(__file__).parent.parent / "commerce" / "opportunity.py").read_text(
            encoding="utf-8"
        )
        # Prose may mention commerce_offers as limitation doc; assert no mechanics.
        for tok in (
            "get_drop",
            "verify_live_drop",
            "INSERT INTO commerce_offers",
            "SELECT * FROM commerce_offers",
            "pg_advisory",
        ):
            assert tok not in src


class TestAdvisoryLockKey:
    @pytest.mark.asyncio
    async def test_lock_key_includes_all_sealed_identity_parts(self, monkeypatch):
        cand = _candidate(id=11, version=2)
        conn = FakeConn()
        _install(monkeypatch, conn)
        from commerce.opportunity_sealing import seal_ranked_candidate

        async def get_drop(c, cuid):
            return _live_payload()

        await seal_ranked_candidate(cand, None, 1, 10, "drop_abc", get_drop=get_drop)
        lock_calls = [c for c in conn.calls if c[0] == "execute" and "pg_advisory" in c[1]]
        assert lock_calls, "no advisory lock executed"
        key = lock_calls[0][2][0]
        assert "seal:1:10:11:2:drop_abc" in key
        assert "ppv_offer" not in key

    def test_lock_key_not_using_synthetic_product(self):
        from commerce.opportunity_sealing import _advisory_key

        key = _advisory_key((1, 10, 11, 1, "drop_abc"))
        # Synthetic hash of drop_abc would be numeric, not the CUID string
        import hashlib

        synth = str(int(hashlib.sha256(b"drop_abc").hexdigest()[:15], 16) % (2**62))
        assert synth not in key
        assert "drop_abc" in key


class TestResultContracts:
    def test_seal_result_frozen(self):
        assert SealResult.__dataclass_params__.frozen is True

    def test_verification_result_frozen_or_dataclass(self):
        # SealVerificationResult may be frozen or not, but should be dataclass
        assert dataclasses.is_dataclass(SealVerificationResult)

    def test_status_vocabulary(self):
        for name in (
            SEALED,
            PROVIDER_NOT_FOUND,
            PROVIDER_REJECTED,
            PROVIDER_DRIFT,
            CREATOR_MISMATCH,
            MALFORMED_PROVIDER_DATA,
            PROVIDER_TIMEOUT_AMBIGUOUS,
            LOCAL_PERSISTENCE_FAILURE,
        ):
            assert name in MODULE_PATH.read_text(encoding="utf-8")

    def test_no_llm_worker_redis_imports(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        for tok in (
            "llm_worker",
            "product_selection",
            "vault_taxonomy",
            "segments.",
            "seller_earning",
            "analytics",
            "import redis",
            "from redis",
            "workers.",
        ):
            assert tok not in src

    def test_reuses_primitives(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        assert "verify_live_drop" in src
        assert "canonical_identity_ids" in src
        assert "drop_content_hash" in src
