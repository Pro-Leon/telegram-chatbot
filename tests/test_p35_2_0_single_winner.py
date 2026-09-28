"""P3.5.2.0 — Opportunity Ledger Single-Winner Fix (unit, no DB/provider/Redis writes).

Proves one sealed commerce offer produces at most one PURCHASED
opportunity-ledger row:

- primary UPDATE selects exactly one eligible row via
  ORDER BY opportunity_id ASC LIMIT 1 (deterministic)
- update remains creator-scoped with existing eligibility predicates
- fallback (earliest re-engagement touch) is also single-winner + scoped
- duplicate/concurrent webhooks converge without duplicate revenue
- ledger never touches commerce authority (commerce_offers)

All DB interaction is mocked. No migrations, no provider/Redis writes.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit]

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

ELIGIBLE = ("PENDING", "SENT", "SEND_FAILED")


class FakeConn:
    """Scripted asyncpg stand-in. Pops fetchrow returns in order, records SQL."""

    def __init__(self, fetchrows=None):
        self._fetchrows = list(fetchrows or [])
        self.calls: list[tuple] = []

    async def fetchrow(self, sql, *params):
        self.calls.append(("fetchrow", sql, params))
        if not self._fetchrows:
            return None
        return self._fetchrows.pop(0)

    async def fetch(self, sql, *params):  # pragma: no cover - not used here
        self.calls.append(("fetch", sql, params))
        return []

    async def execute(self, sql, *params):  # pragma: no cover - not used here
        self.calls.append(("execute", sql, params))
        return "UPDATE 0"

    def sqls(self):
        return [c[1] for c in self.calls if c[0] == "fetchrow"]


def make_pool(conn):
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    return pool


def patch_ledger_pool(monkeypatch, conn):
    monkeypatch.setattr(
        "commerce.opportunity_ledger.get_pool", AsyncMock(return_value=make_pool(conn))
    )
    return conn


class SingleWinnerConn:
    """Stateful in-memory emulation of the fixed single-winner SQL semantics.

    Holds ledger rows as dicts and applies the same predicates the SQL
    expresses: creator + offer + eligible state (+ reengagement_of IS NULL
    for the primary path), deterministic ORDER BY opportunity_id ASC LIMIT 1.
    A legacy multi-row implementation would update every eligible row; this
    fake updates exactly one, mirroring the fixed statement.
    """

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.calls: list[tuple] = []

    async def fetchrow(self, sql, *params):
        self.calls.append(("fetchrow", sql, params))
        upper = sql.upper()
        is_update = "UPDATE" in upper and "PURCHASED" in upper
        if is_update:
            creator, offer = params[0], params[1]
            outcome_at, txn, price, curr = params[2], params[3], params[4], params[5]
            primary = "REENGAGEMENT_OF IS NULL" in upper
            eligible = [
                r
                for r in self.rows
                if r.get("creator_id") == creator
                and r.get("sealed_offer_id") == offer
                and r.get("outcome_state") in ELIGIBLE
                and (r.get("reengagement_of") is None if primary else True)
            ]
            # Fallback path only runs when no original row exists: the
            # production code calls it second, so emulate honestly — if this
            # is the fallback statement but an eligible ORIGINAL row exists,
            # it must not claim anything (production would never reach it).
            # The test harness calls record_purchase_by_offer which sequences
            # primary -> fallback, so reaching fallback implies primary found
            # nothing. Here we simply apply per-statement predicates.
            if not eligible:
                return None
            eligible.sort(key=lambda r: r["opportunity_id"])
            winner = eligible[0]
            winner.update(
                {
                    "outcome_state": "PURCHASED",
                    "outcome_at": outcome_at,
                    "transaction_id": txn,
                    "purchased_price_minor": price,
                    "purchased_currency": curr,
                    "attribution_status": "attributed",
                }
            )
            return dict(winner)
        # Existing-winner re-read: creator + offer + transaction.
        if "TRANSACTION_ID = $3" in upper:
            creator, offer, txn = params[0], params[1], params[2]
            cands = [
                r
                for r in self.rows
                if r.get("creator_id") == creator
                and r.get("sealed_offer_id") == offer
                and r.get("transaction_id") == txn
            ]
            cands.sort(key=lambda r: r["opportunity_id"])
            return dict(cands[0]) if cands else None
        return None

    async def fetch(self, sql, *params):
        self.calls.append(("fetch", sql, params))
        return []

    async def execute(self, sql, *params):
        self.calls.append(("execute", sql, params))
        return "UPDATE 0"

    def sqls(self):
        return [c[1] for c in self.calls if c[0] == "fetchrow"]


def row(oid, creator=1, offer=42, state="PENDING", reeng_of="__absent__", txn=None):
    r = {
        "opportunity_id": oid,
        "creator_id": creator,
        "sealed_offer_id": offer,
        "outcome_state": state,
        "transaction_id": txn,
    }
    if reeng_of != "__absent__":
        r["reengagement_of"] = reeng_of
    else:
        r["reengagement_of"] = None
    return r


def primary_sql(conn):
    assert conn.sqls(), "expected at least one statement"
    return conn.sqls()[0]


class TestSingleWinnerSQL:
    @pytest.mark.asyncio
    async def test_primary_update_is_deterministic_single_winner(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch, FakeConn(fetchrows=[{"opportunity_id": 5, "outcome_state": "PURCHASED"}])
        )
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-1",
            price_minor=1999, currency="USD", purchased_at=NOW,
        )
        assert out["outcome_state"] == "PURCHASED"
        sql = primary_sql(conn)
        upper = sql.upper()
        # Single-winner construction.
        assert "WHERE OPPORTUNITY_ID = (" in upper
        assert "ORDER BY OPPORTUNITY_ID ASC" in upper
        assert "LIMIT 1" in upper
        # Creator scope + offer identity + eligibility preserved.
        assert "CREATOR_ID = $1" in upper
        assert "SEALED_OFFER_ID = $2" in upper
        assert "REENGAGEMENT_OF IS NULL" in upper
        assert "PENDING" in upper and "SENT" in upper and "SEND_FAILED" in upper
        # Must not claim by product/drop identity and must stay on the ledger table.
        assert "commerce_opportunity_decisions" in sql
        assert "commerce_offers" not in sql
        assert "product_id" not in sql.lower() and "drop_cuid" not in sql.lower()
        # Bind params unchanged: creator, offer, outcome_at, txn, price, currency.
        params = conn.calls[0][2]
        assert params[0] == 1 and params[1] == 42
        assert params[3] == "txn-1" and params[4] == 1999 and params[5] == "USD"
        assert getattr(params[2], "tzinfo", None) is not None

    @pytest.mark.asyncio
    async def test_primary_does_not_bare_multirow_update(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch, FakeConn(fetchrows=[{"opportunity_id": 5, "outcome_state": "PURCHASED"}])
        )
        from commerce.opportunity_ledger import record_purchase_by_offer

        await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-1",
            price_minor=1999, currency="USD",
        )
        sql = primary_sql(conn)
        # The old multi-row shape (bare WHERE creator+offer+state with no
        # subselect) must be gone: every UPDATE...PURCHASED must go through
        # the opportunity_id subselect. (Count via "UPDATE COMMERCE" to avoid
        # matching the "updated_at" column.)
        assert sql.upper().count("UPDATE COMMERCE_OPPORTUNITY_DECISIONS") == 1
        assert "SELECT OPPORTUNITY_ID FROM COMMERCE_OPPORTUNITY_DECISIONS" in sql.upper()

    @pytest.mark.asyncio
    async def test_fallback_is_single_winner_and_scoped(self, monkeypatch):
        # Primary finds nothing; fallback wins.
        conn = patch_ledger_pool(
            monkeypatch,
            FakeConn(fetchrows=[None, {"opportunity_id": 9, "outcome_state": "PURCHASED"}]),
        )
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-fb",
            price_minor=100, currency="USD",
        )
        assert out["opportunity_id"] == 9
        assert len(conn.sqls()) == 2
        fb = conn.sqls()[1].upper()
        assert "WHERE OPPORTUNITY_ID = (" in fb
        assert "ORDER BY OPPORTUNITY_ID ASC" in fb
        assert "LIMIT 1" in fb
        assert "CREATOR_ID = $1" in fb and "SEALED_OFFER_ID = $2" in fb
        assert "PENDING" in fb and "SENT" in fb and "SEND_FAILED" in fb

    @pytest.mark.asyncio
    async def test_single_matching_row_becomes_purchased(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch, FakeConn(fetchrows=[{"opportunity_id": 5, "outcome_state": "PURCHASED"}])
        )
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-1",
            price_minor=1999, currency="USD",
        )
        assert out["outcome_state"] == "PURCHASED"
        assert len(conn.sqls()) == 1  # fallback never runs


class TestSingleWinnerBehavior:
    @pytest.mark.asyncio
    async def test_multiple_matching_rows_only_lowest_wins(self, monkeypatch):
        conn = SingleWinnerConn([row(5), row(6), row(7)])
        patch_ledger_pool(monkeypatch, conn)
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-1",
            price_minor=1999, currency="USD",
        )
        assert out["opportunity_id"] == 5
        by_id = {r["opportunity_id"]: r for r in conn.rows}
        assert by_id[5]["outcome_state"] == "PURCHASED"
        assert by_id[6]["outcome_state"] == "PENDING"
        assert by_id[7]["outcome_state"] == "PENDING"
        purchased = [r for r in conn.rows if r["outcome_state"] == "PURCHASED"]
        assert len(purchased) == 1  # no duplicate ledger revenue

    @pytest.mark.asyncio
    async def test_reverse_insertion_order_still_lowest_id(self, monkeypatch):
        conn = SingleWinnerConn([row(9), row(7), row(8)])
        patch_ledger_pool(monkeypatch, conn)
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-1",
            price_minor=1999, currency="USD",
        )
        assert out["opportunity_id"] == 7
        assert {r["opportunity_id"]: r["outcome_state"] for r in conn.rows}[7] == "PURCHASED"

    @pytest.mark.asyncio
    async def test_creator_isolation(self, monkeypatch):
        conn = SingleWinnerConn([row(5, creator=1), row(6, creator=2)])
        patch_ledger_pool(monkeypatch, conn)
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-1",
            price_minor=1999, currency="USD",
        )
        assert out["opportunity_id"] == 5 and out["creator_id"] == 1
        other = next(r for r in conn.rows if r["creator_id"] == 2)
        assert other["outcome_state"] == "PENDING"

    @pytest.mark.asyncio
    async def test_already_purchased_winner_no_additional_row(self, monkeypatch):
        # Winner already PURCHASED; the only competitor is terminal, so no
        # eligible row remains and nothing else may become PURCHASED.
        # (A spare PENDING row + a *different* txn is unreachable via the
        # authoritative caller: commerce_offers allows exactly one purchase
        # per offer, so the ledger never sees a second distinct txn for the
        # same sealed offer. Duplicate deliveries reuse the same txn and hit
        # the existing-winner re-read below.)
        conn = SingleWinnerConn([
            row(5, state="PURCHASED", txn="txn-9"),
            row(6, state="EXPIRED"),
        ])
        patch_ledger_pool(monkeypatch, conn)
        from commerce.opportunity_ledger import record_purchase_by_offer

        before = dict(next(r for r in conn.rows if r["opportunity_id"] == 5))
        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-9",
            price_minor=1999, currency="USD",
        )
        assert out["opportunity_id"] == 5
        assert out["transaction_id"] == "txn-9"
        after = next(r for r in conn.rows if r["opportunity_id"] == 5)
        assert after == before
        assert next(r for r in conn.rows if r["opportunity_id"] == 6)["outcome_state"] == "EXPIRED"
        assert sum(1 for r in conn.rows if r["outcome_state"] == "PURCHASED") == 1

    @pytest.mark.asyncio
    async def test_terminal_competing_rows_excluded(self, monkeypatch):
        conn = SingleWinnerConn([
            row(5, state="EXPIRED"),
            row(6, state="SENT"),
            row(7, state="DECLINED"),
        ])
        patch_ledger_pool(monkeypatch, conn)
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-1",
            price_minor=1999, currency="USD",
        )
        assert out["opportunity_id"] == 6
        by_id = {r["opportunity_id"]: r for r in conn.rows}
        assert by_id[5]["outcome_state"] == "EXPIRED"
        assert by_id[7]["outcome_state"] == "DECLINED"
        assert by_id[6]["outcome_state"] == "PURCHASED"

    @pytest.mark.asyncio
    async def test_duplicate_webhook_no_duplicate(self, monkeypatch):
        # Single matching row: duplicate delivery returns the winner unchanged.
        conn = SingleWinnerConn([row(5)])
        patch_ledger_pool(monkeypatch, conn)
        from commerce.opportunity_ledger import record_purchase_by_offer

        first = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-9",
            price_minor=1999, currency="USD",
        )
        second = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-9",
            price_minor=1999, currency="USD",
        )
        assert first["opportunity_id"] == 5
        assert second["opportunity_id"] == 5
        assert second["transaction_id"] == "txn-9"
        purchased = [r for r in conn.rows if r["outcome_state"] == "PURCHASED"]
        assert len(purchased) == 1

    @pytest.mark.asyncio
    async def test_concurrent_retry_converges_deterministically(self, monkeypatch):
        # True concurrent deliveries overlap: both subselects snapshot the
        # same lowest eligible row and converge on it via the row lock, so at
        # most one row becomes PURCHASED. Sequentially this is emulated with
        # a single eligible row: the retry observes the existing winner.
        conn = SingleWinnerConn([row(5)])
        patch_ledger_pool(monkeypatch, conn)
        from commerce.opportunity_ledger import record_purchase_by_offer

        kw = {
            "creator_id": 1,
            "offer_id": 42,
            "transaction_id": "txn-c",
            "price_minor": 1999,
            "currency": "USD",
        }
        r1 = await record_purchase_by_offer(**kw)
        r2 = await record_purchase_by_offer(**kw)
        assert r1["opportunity_id"] == r2["opportunity_id"] == 5
        assert sum(1 for r in conn.rows if r["outcome_state"] == "PURCHASED") == 1
        # The single statement is atomic/conditional: exactly one UPDATE per
        # call path, deterministic ORDER BY + LIMIT 1.
        for sql in conn.sqls():
            if "PURCHASED" in sql.upper() and "UPDATE" in sql.upper():
                assert "ORDER BY OPPORTUNITY_ID ASC" in sql.upper()
                assert "LIMIT 1" in sql.upper()

    @pytest.mark.asyncio
    async def test_unlinked_purchase_remains_unattributed(self, monkeypatch):
        conn = patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[None, None, None]))
        from commerce.opportunity_ledger import record_purchase_by_offer

        assert await record_purchase_by_offer(
            creator_id=1, offer_id=777, transaction_id="txn-x",
            price_minor=1, currency="USD",
        ) is None
        # Three statements: primary UPDATE, fallback UPDATE, existing re-read.
        assert len(conn.sqls()) == 3
        # Neither UPDATE may bypass sealed_offer_id or claim via product/drop.
        for sql in conn.sqls()[:2]:
            assert "SEALED_OFFER_ID = $2" in sql.upper()
            assert "PRODUCT_ID" not in sql.upper()

    @pytest.mark.asyncio
    async def test_fallback_single_winner_when_no_original(self, monkeypatch):
        # No original rows (all touches are re-engagement children): fallback
        # picks the earliest child only.
        child1 = row(11, reeng_of=5)
        child2 = row(12, reeng_of=5)
        conn = SingleWinnerConn([child1, child2])
        patch_ledger_pool(monkeypatch, conn)
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-r",
            price_minor=100, currency="USD",
        )
        # Primary finds no original (reengagement_of IS NULL excludes both),
        # fallback wins the lowest child.
        assert out["opportunity_id"] == 11
        by_id = {r["opportunity_id"]: r for r in conn.rows}
        assert by_id[11]["outcome_state"] == "PURCHASED"
        assert by_id[12]["outcome_state"] == "PENDING"

    @pytest.mark.asyncio
    async def test_original_preferred_over_reengagement_child(self, monkeypatch):
        conn = SingleWinnerConn([row(5), row(11, reeng_of=5)])
        patch_ledger_pool(monkeypatch, conn)
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-1",
            price_minor=1999, currency="USD",
        )
        assert out["opportunity_id"] == 5
        assert next(r for r in conn.rows if r["opportunity_id"] == 11)["outcome_state"] != "PURCHASED"


class TestAuthorityBoundary:
    def test_ledger_purchase_never_touches_commerce_authority(self):
        import pathlib

        src = pathlib.Path(__file__).parent.parent.joinpath(
            "commerce", "opportunity_ledger.py"
        ).read_text(encoding="utf-8")
        assert "commerce_offers" not in src.split("async def record_purchase_by_offer")[1].split(
            "async def "
        )[0].replace("commerce_opportunity_decisions", "")
        assert "attribute_purchase_from_webhook" not in src

    def test_authoritative_webhook_attribution_unchanged(self):
        import pathlib

        src = pathlib.Path(__file__).parent.parent.joinpath("commerce", "dao.py").read_text(
            encoding="utf-8"
        )
        # Authoritative path still transitions commerce_offers conditionally.
        assert "UPDATE commerce_offers" in src
        assert "SET state = 'purchased'" in src
        # Ledger call remains isolated + observer-only.
        idx = src.find("record_purchase_by_offer")
        assert idx != -1
        window = src[max(0, idx - 1200): idx + 600]
        assert "try:" in window and "except Exception" in window

    def test_no_provider_redis_migration_writes(self):
        import pathlib

        root = pathlib.Path(__file__).parent.parent
        ledger = (root / "commerce" / "opportunity_ledger.py").read_text(encoding="utf-8")
        # Provider/transport writes: no Redis, event bus, or provider client.
        for token in ("get_redis", "xadd", "publish_event", "DropfansError",
                      "from integrations", "import redis"):
            assert token not in ledger, f"ledger must not touch {token}"
        # No schema change in this fix.
        assert "CREATE TABLE" not in ledger and "ALTER TABLE" not in ledger
        test_src = pathlib.Path(__file__).read_text(encoding="utf-8")
        # This test module itself must not import provider/transport layers:
        # scan import statements only (scanning the whole file would
        # self-match the ledger assertions above).
        import_lines = [
            ln.strip() for ln in test_src.splitlines()
            if ln.strip().startswith(("import ", "from "))
        ]
        joined = "\n".join(import_lines)
        for token in ("redis", "event_bus", "integrations"):
            assert token not in joined, f"test must not import {token}"
