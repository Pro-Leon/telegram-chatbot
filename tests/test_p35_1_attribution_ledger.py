"""P3.5.1 — Outcome Attribution Foundation (unit, no DB/provider/Redis writes).

Proves the durable opportunity ledger connects evaluation → eligible set →
selected → seal → offer → outcome without changing commercial policy:

- decision persistence (idempotent, creator-isolated, nullable generation)
- frozen snapshot integrity (immutable after outcome)
- no-opportunity / no-seal / seal-failure records
- seal linkage, send outcomes, purchase attribution, terminal outcomes
- re-engagement linkage without new offers or double-counted revenue
- failure isolation (ledger failures never break commerce)
- provenance preservation on decline/expiry
- honest historical classification (no fabricated ranking data)
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from datetime import UTC

import pytest

pytestmark = [pytest.mark.unit]

LEDGER_PATH = Path(__file__).parent.parent / "commerce" / "opportunity_ledger.py"
MIGRATION_PATH = (
    Path(__file__).parent.parent / "db" / "migrations" / "20260918000000_p35_1_opportunity_ledger.sql"
)
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeConn:
    """Scripted asyncpg stand-in. Pops fetchrow/fetch/execute returns in order."""

    def __init__(self, fetchrows=None, fetches=None, executes=None):
        self._fetchrows = list(fetchrows or [])
        self._fetches = list(fetches or [])
        self._executes = list(executes or [])
        self.calls: list[tuple] = []

    async def fetchrow(self, sql, *params):
        self.calls.append(("fetchrow", sql, params))
        if not self._fetchrows:
            return None
        return self._fetchrows.pop(0)

    async def fetch(self, sql, *params):
        self.calls.append(("fetch", sql, params))
        if not self._fetches:
            return []
        return self._fetches.pop(0)

    async def execute(self, sql, *params):
        self.calls.append(("execute", sql, params))
        if not self._executes:
            return "UPDATE 1"
        return self._executes.pop(0)

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def sqls(self, method="fetchrow"):
        return [c[1] for c in self.calls if c[0] == method]


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


def patch_dao_pool(monkeypatch, conn):
    monkeypatch.setattr("commerce.dao.get_pool", AsyncMock(return_value=make_pool(conn)))
    return conn


def cand(definition_id=11, version=2, vault=("V1", "V2"), drops=("drop_x",), **over):
    base = {
        "creator_id": 1,
        "user_id": 10,
        "definition_id": definition_id,
        "stable_key": "alpha",
        "version": version,
        "offer_type": "SMALL_BUNDLE",
        "canonical_vault_item_ids": list(vault),
        "price_minor": 1999,
        "currency": "USD",
        "mapped_drop_ids": list(drops),
    }
    base.update(over)
    return base


def verdict(*reasons):
    return {"eligible": not reasons, "denial_reasons": list(reasons)}


def engine_result(**over):
    c = cand()
    base = {
        "creator_id": 1,
        "user_id": 10,
        "evaluated_at": NOW,
        "fan_commercial_state": {"purchase_count": 0},
        "offer_history": {"total_offer_count": 1},
        "owned_vault_ids": frozenset(),
        "candidates": (c,),
        "eligible_candidates": (c,),
        "ineligible": (),
        "ranking_inputs": (),
        "ranking_result": {
            "ranked": [{"definition_id": 11, "stable_key": "alpha", "version": 2, "factors": ["NOVEL_CANONICAL_SET"]}],
            "selected": {"definition_id": 11},
            "policy_version": "v1",
        },
        "selected_candidate": c,
        "has_opportunity": True,
        "status": "RANKED",
        "ranking_conversation": {"lifecycle": "established", "current_topic": "movie", "recent_topics": [], "open_threads": []},
    }
    base.update(over)
    return SimpleNamespace(**base)


def sealed_envelope(definition_id=11, version=2):
    return json.dumps(
        {
            "v": 1,
            "definition_id": definition_id,
            "definition_version": version,
            "stable_key": "alpha",
            "sealed_at": "2026-09-01T00:00:00+00:00",
            "verified_at": "2026-09-01T00:00:00+00:00",
            "verified_hash": "h",
            "allow_download": True,
            "verifier_version": 1,
        },
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# Decision persistence
# ---------------------------------------------------------------------------


class TestDecisionPersistence:
    @pytest.mark.asyncio
    async def test_one_decision_creates_one_row(self, monkeypatch):
        conn = patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[{"opportunity_id": 1, "decision_status": "DECIDED"}]))
        from commerce.opportunity_ledger import record_opportunity_decision

        row = await record_opportunity_decision(
            creator_id=1, user_id=10, generation_id="gen-1",
            evaluated_at=NOW, opportunity_result=engine_result(),
        )
        assert row["opportunity_id"] == 1
        sql = conn.sqls()[0]
        assert "INSERT INTO commerce_opportunity_decisions" in sql
        assert "ON CONFLICT (creator_id, generation_id)" in sql
        params = conn.calls[0][2]
        snapshot = json.loads(params[4])
        assert snapshot["eligible"][0]["definition_id"] == 11
        assert snapshot["selected"]["stable_key"] == "alpha"
        assert snapshot["ranking"]["policy_version"] == "v1"
        assert snapshot["ranking"]["ranked_order"] == [11]
        assert snapshot["ranking"]["factors"] == {"11": ["NOVEL_CANONICAL_SET"]}

    @pytest.mark.asyncio
    async def test_duplicate_generation_returns_existing_without_rewrite(self, monkeypatch):
        existing = {"opportunity_id": 7, "decision_status": "DECIDED"}
        conn = patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[None, existing]))
        from commerce.opportunity_ledger import record_opportunity_decision

        row = await record_opportunity_decision(
            creator_id=1, user_id=10, generation_id="gen-dup",
            evaluated_at=NOW, opportunity_result=engine_result(),
        )
        assert row == existing
        # One INSERT..DO NOTHING (no row) + one SELECT of the existing row.
        assert len(conn.sqls()) == 2
        assert "ON CONFLICT (creator_id, generation_id)" in conn.sqls()[0]
        assert conn.sqls()[1].strip().upper().startswith("SELECT")

    @pytest.mark.asyncio
    async def test_invalid_scope_raises(self, monkeypatch):
        patch_ledger_pool(monkeypatch, FakeConn())
        from commerce.opportunity_ledger import record_opportunity_decision

        with pytest.raises(ValueError):
            await record_opportunity_decision(
                creator_id=0, user_id=10, generation_id="g",
                evaluated_at=NOW, opportunity_result=engine_result(),
            )
        with pytest.raises(ValueError):
            await record_opportunity_decision(
                creator_id=1, user_id=10, generation_id="g",
                evaluated_at=datetime(2026, 9, 1), opportunity_result=engine_result(),
            )
        with pytest.raises(TypeError):
            await record_opportunity_decision(
                creator_id=1, user_id=10, generation_id="g",
                evaluated_at=NOW, opportunity_result=None,
            )

    @pytest.mark.asyncio
    async def test_same_generation_different_creator_no_collision(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch,
            FakeConn(fetchrows=[{"opportunity_id": 1}, {"opportunity_id": 2}]),
        )
        from commerce.opportunity_ledger import record_opportunity_decision

        r1 = await record_opportunity_decision(
            creator_id=1, user_id=10, generation_id="shared-gen",
            evaluated_at=NOW, opportunity_result=engine_result(),
        )
        r2 = await record_opportunity_decision(
            creator_id=2, user_id=10, generation_id="shared-gen",
            evaluated_at=NOW, opportunity_result=engine_result(),
        )
        assert (r1["opportunity_id"], r2["opportunity_id"]) == (1, 2)
        assert conn.calls[0][2][0] == 1 and conn.calls[1][2][0] == 2

    @pytest.mark.asyncio
    async def test_no_opportunity_statuses(self, monkeypatch):
        from commerce.opportunity_ledger import record_opportunity_decision

        for status, decision in [
            ("NO_CANDIDATES", "NO_OPPORTUNITY"),
            ("NO_ELIGIBLE_CANDIDATES", "NO_OPPORTUNITY"),
            ("NO_SELECTION", "NO_SELECTION"),
        ]:
            conn = patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[{"opportunity_id": 1}]))
            res = engine_result(
                has_opportunity=False, selected_candidate=None, status=status,
                eligible_candidates=(), ranking_result=None,
            )
            await record_opportunity_decision(
                creator_id=1, user_id=10, generation_id=f"g-{status}",
                evaluated_at=NOW, opportunity_result=res,
            )
            params = conn.calls[0][2]
            assert params[9] == decision  # decision_status
            assert params[10] == decision  # outcome_state mirrors
            assert params[8] == status  # no_selection_reason


# ---------------------------------------------------------------------------
# Snapshot integrity
# ---------------------------------------------------------------------------


class TestSnapshotIntegrity:
    @pytest.mark.asyncio
    async def test_ineligible_reasons_preserved(self, monkeypatch):
        conn = patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[{"opportunity_id": 1}]))
        from commerce.opportunity_ledger import record_opportunity_decision

        bad = cand(definition_id=12, version=1)
        res = engine_result(ineligible=[(bad, verdict("OWNERSHIP_FULL_OVERLAP"))])
        await record_opportunity_decision(
            creator_id=1, user_id=10, generation_id="g-inelig",
            evaluated_at=NOW, opportunity_result=res,
        )
        snapshot = json.loads(conn.calls[0][2][4])
        assert snapshot["ineligible"][0]["denial_reasons"] == ["OWNERSHIP_FULL_OVERLAP"]
        assert snapshot["ineligible"][0]["definition_id"] == 12

    @pytest.mark.asyncio
    async def test_outcome_update_never_touches_snapshot(self, monkeypatch):
        row = {
            "opportunity_id": 3, "outcome_state": "SENT",
            "decision_snapshot": json.dumps({"eligible": [{"definition_id": 11}]}),
        }
        conn = patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[dict(row)]))
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-1",
            price_minor=1999, currency="USD",
        )
        assert out["decision_snapshot"] == row["decision_snapshot"]
        update_sql = conn.sqls()[0]
        assert "decision_snapshot" not in update_sql
        assert "selected_definition_id" not in update_sql


# ---------------------------------------------------------------------------
# Sealing
# ---------------------------------------------------------------------------


class TestSealLinkage:
    def _linked_row(self, snapshot_selected_drops=("drop_x",)):
        snap = {"selected": {"mapped_drop_ids": list(snapshot_selected_drops)}}
        return {"opportunity_id": 5, "sealed_offer_id": None, "decision_snapshot": json.dumps(snap)}

    @pytest.mark.asyncio
    async def test_sealed_links_offer_and_cuid(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch,
            FakeConn(fetchrows=[
                self._linked_row(),
                {"opportunity_id": 5, "decision_status": "SEALED", "sealed_offer_id": 42},
            ]),
        )
        from commerce.opportunity_ledger import link_opportunity_seal

        out = await link_opportunity_seal(
            5,
            seal_result=SimpleNamespace(status="SEALED", offer={"id": 42, "dropfans_product_id": "drop_x"}),
            drop_cuid="drop_x",
        )
        assert out["sealed_offer_id"] == 42
        update_sql = conn.sqls()[1]
        assert "sealed_offer_id" in update_sql and "'SEALED'" in update_sql

    @pytest.mark.asyncio
    async def test_relink_is_idempotent(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch,
            FakeConn(fetchrows=[{"opportunity_id": 5, "sealed_offer_id": 42}]),
        )
        from commerce.opportunity_ledger import link_opportunity_seal

        out = await link_opportunity_seal(
            5, seal_result=SimpleNamespace(status="SEALED", offer={"id": 99}), drop_cuid="drop_z"
        )
        assert out["sealed_offer_id"] == 42  # original link kept
        assert len(conn.sqls()) == 1  # read only, no UPDATE

    @pytest.mark.asyncio
    async def test_no_mapped_drop_recorded(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch,
            FakeConn(fetchrows=[self._linked_row(snapshot_selected_drops=()), {"opportunity_id": 5}]),
        )
        from commerce.opportunity_ledger import link_opportunity_seal

        await link_opportunity_seal(5, seal_result=None, drop_cuid=None)
        assert conn.calls[1][2][1] == "NO_MAPPED_DROP"

    @pytest.mark.asyncio
    async def test_multiple_drops_recorded(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch,
            FakeConn(fetchrows=[self._linked_row(snapshot_selected_drops=("a", "b")), {"opportunity_id": 5}]),
        )
        from commerce.opportunity_ledger import link_opportunity_seal

        await link_opportunity_seal(5, seal_result=None, drop_cuid=None)
        assert conn.calls[1][2][1] == "MULTIPLE_DROPS"

    @pytest.mark.asyncio
    async def test_seal_failure_preserves_vocabulary(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch,
            FakeConn(fetchrows=[self._linked_row(), {"opportunity_id": 5}]),
        )
        from commerce.opportunity_ledger import link_opportunity_seal

        await link_opportunity_seal(
            5, seal_result=SimpleNamespace(status="PROVIDER_DRIFT", subreason="PRICE_MISMATCH"), drop_cuid="drop_x"
        )
        sql, params = conn.sqls()[1], conn.calls[1][2]
        assert "'SEAL_FAILED'" in sql and params[1] == "PRICE_MISMATCH"

    @pytest.mark.asyncio
    async def test_unknown_opportunity_returns_none(self, monkeypatch):
        patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[None]))
        from commerce.opportunity_ledger import link_opportunity_seal

        assert await link_opportunity_seal(4242, seal_result=None) is None


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


class TestOutcomes:
    @pytest.mark.asyncio
    async def test_purchase_updates_original_row_first(self, monkeypatch):
        updated = {"opportunity_id": 5, "outcome_state": "PURCHASED", "transaction_id": "txn-9"}
        conn = patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[updated]))
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-9",
            price_minor=1999, currency="USD", purchased_at=NOW,
        )
        assert out["outcome_state"] == "PURCHASED"
        assert len(conn.sqls()) == 1  # fallback SELECT never runs
        sql, params = conn.sqls()[0], conn.calls[0][2]
        assert "reengagement_of IS NULL" in sql
        assert params[3] == "txn-9" and params[4] == 1999 and params[5] == "USD"

    @pytest.mark.asyncio
    async def test_duplicate_webhook_returns_existing_unchanged(self, monkeypatch):
        existing = {"opportunity_id": 5, "outcome_state": "PURCHASED", "transaction_id": "txn-9"}
        conn = patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[None, None, dict(existing)]))
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-9", price_minor=1999, currency="USD"
        )
        assert out == existing
        assert not any("SET outcome_state" in s and "PURCHASED" in s and "UPDATE" in s for s in conn.sqls()[2:])
        assert "transaction_id = $3" in conn.sqls()[2]  # idempotent re-read

    @pytest.mark.asyncio
    async def test_unlinked_offer_claims_nothing(self, monkeypatch):
        patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[None, None, None]))
        from commerce.opportunity_ledger import record_purchase_by_offer

        assert await record_purchase_by_offer(
            creator_id=1, offer_id=777, transaction_id="txn-x", price_minor=1, currency="USD"
        ) is None

    @pytest.mark.asyncio
    async def test_purchase_after_send_failure_resolves(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch, FakeConn(fetchrows=[{"opportunity_id": 5, "outcome_state": "PURCHASED"}])
        )
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-late", price_minor=5, currency="USD"
        )
        assert out["outcome_state"] == "PURCHASED"

    @pytest.mark.asyncio
    async def test_naive_timestamp_coerced_and_skew_accepted(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch, FakeConn(fetchrows=[{"opportunity_id": 5, "outcome_state": "PURCHASED"}])
        )
        from commerce.opportunity_ledger import record_purchase_by_offer

        early = datetime(2026, 8, 1, 12, 0, 0)  # naive + older than decision
        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-skew",
            price_minor=5, currency="USD", purchased_at=early,
        )
        assert out["outcome_state"] == "PURCHASED"
        stored_at = conn.calls[0][2][2]
        assert stored_at.tzinfo is not None  # coerced to aware, never rejected

    @pytest.mark.asyncio
    async def test_send_outcomes_distinct(self, monkeypatch):
        from commerce.opportunity_ledger import record_opportunity_send

        base = {"opportunity_id": 5, "outcome_state": "PENDING"}
        conn = patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[dict(base), {"opportunity_id": 5, "outcome_state": "SENT"}]))
        out = await record_opportunity_send(5, sealed_execution=SimpleNamespace(status="EXECUTED"))
        assert out["outcome_state"] == "SENT"

        conn2 = patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[dict(base), {"opportunity_id": 5, "outcome_state": "SEND_FAILED"}]))
        out2 = await record_opportunity_send(5, sealed_execution=None)
        assert out2["outcome_state"] == "SEND_FAILED"

    @pytest.mark.asyncio
    async def test_inflight_duplicate_left_untouched(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch, FakeConn(fetchrows=[{"opportunity_id": 5, "outcome_state": "PENDING"}])
        )
        from commerce.opportunity_ledger import record_opportunity_send

        inflight = SimpleNamespace(
            status="ALREADY_ENQUEUED", already_delivered=False,
            subreason="ALREADY_RESERVED", dedup_value="0",
        )
        out = await record_opportunity_send(5, sealed_execution=inflight)
        assert out["outcome_state"] == "PENDING"
        assert len(conn.sqls()) == 1  # read only

    @pytest.mark.asyncio
    async def test_terminal_outcome_validation(self, monkeypatch):
        patch_ledger_pool(monkeypatch, FakeConn())
        from commerce.opportunity_ledger import record_offer_terminal_outcome

        with pytest.raises(ValueError):
            await record_offer_terminal_outcome(creator_id=1, offer_id=1, outcome_state="PURCHASED")
        with pytest.raises(ValueError):
            await record_offer_terminal_outcome(creator_id=1, offer_id=1, outcome_state="SENT")


# ---------------------------------------------------------------------------
# Decline / expiry provenance (dao seam)
# ---------------------------------------------------------------------------


class TestProvenancePreservation:
    @pytest.mark.asyncio
    async def test_decline_preserves_sealed_envelope(self, monkeypatch):
        prev = {"id": 9, "reason": sealed_envelope()}
        updated = {"id": 9, "state": "declined", "reason": "placeholder"}
        conn = patch_dao_pool(monkeypatch, FakeConn(fetchrows=[prev, updated]))
        linked = []
        monkeypatch.setattr(
            "commerce.opportunity_ledger.record_offer_terminal_outcome",
            AsyncMock(side_effect=lambda **kw: linked.append(kw) or []),
        )
        from commerce.dao import mark_offer_declined

        row = await mark_offer_declined(creator_id=1, user_id=10, reason="rejection_hard")
        assert row is not None
        new_reason = json.loads(conn.calls[1][2][2])
        assert new_reason["definition_id"] == 11
        assert new_reason["definition_version"] == 2
        assert new_reason["stable_key"] == "alpha"
        assert new_reason["outcome_state"] == "DECLINED"
        assert new_reason["outcome_detail"] == "rejection_hard"
        assert linked and linked[0]["outcome_state"] == "DECLINED" and linked[0]["offer_id"] == 9

    @pytest.mark.asyncio
    async def test_decline_preserves_legacy_text(self, monkeypatch):
        prev = {"id": 9, "reason": "some legacy note"}
        conn = patch_dao_pool(monkeypatch, FakeConn(fetchrows=[prev, {"id": 9, "state": "declined"}]))
        monkeypatch.setattr(
            "commerce.opportunity_ledger.record_offer_terminal_outcome", AsyncMock(return_value=[])
        )
        from commerce.dao import mark_offer_declined

        await mark_offer_declined(creator_id=1, user_id=10, reason="fan_rejected")
        stored = conn.calls[1][2][2]
        assert "some legacy note" in stored and "fan_rejected" in stored

    @pytest.mark.asyncio
    async def test_decline_first_write_wins(self):
        from commerce.opportunity_ledger import merge_outcome_into_reason

        once = merge_outcome_into_reason(sealed_envelope(), "DECLINED", detail="a")
        twice = merge_outcome_into_reason(once, "EXPIRED", detail="b")
        parsed = json.loads(twice)
        assert parsed["outcome_state"] == "DECLINED" and parsed["outcome_detail"] == "a"
        assert parsed["definition_id"] == 11

    @pytest.mark.asyncio
    async def test_expiry_hooks_ledger_without_touching_reason(self, monkeypatch):
        conn = patch_dao_pool(monkeypatch, FakeConn(fetchrows=[{"id": 4, "state": "expired"}]))
        linked = []
        monkeypatch.setattr(
            "commerce.opportunity_ledger.record_offer_terminal_outcome",
            AsyncMock(side_effect=lambda **kw: linked.append(kw) or []),
        )
        from commerce.dao import mark_offer_expired

        row = await mark_offer_expired(creator_id=1, offer_id=4)
        assert row is not None
        assert "reason" not in conn.sqls()[0]  # reason column untouched
        assert linked and linked[0] == {"creator_id": 1, "offer_id": 4, "outcome_state": "EXPIRED"}

    @pytest.mark.asyncio
    async def test_decline_no_active_offer_returns_none(self, monkeypatch):
        patch_dao_pool(monkeypatch, FakeConn(fetchrows=[None]))
        from commerce.dao import mark_offer_declined

        assert await mark_offer_declined(creator_id=1, user_id=10) is None


# ---------------------------------------------------------------------------
# Re-engagement linkage
# ---------------------------------------------------------------------------


def stale_offer(**over):
    base = {
        "id": 42, "creator_id": 1, "user_id": 10, "product_id": 77,
        "dropfans_product_id": "drop_x", "state": "pending",
        "vault_item_ids": ["V1", "V2"],
    }
    base.update(over)
    return base


class TestReengagementLinkage:
    @pytest.mark.asyncio
    async def test_touch_links_parent_creates_no_offer(self, monkeypatch):
        parent = {"opportunity_id": 5, "selected_definition_id": 11, "selected_version": 2, "selected_stable_key": "alpha"}
        conn = patch_ledger_pool(
            monkeypatch, FakeConn(fetchrows=[parent, {"opportunity_id": 6, "reengagement_of": 5}])
        )
        from commerce.opportunity_ledger import record_reengagement_touch

        out = await record_reengagement_touch(creator_id=1, user_id=10, offer=stale_offer())
        assert out["reengagement_of"] == 5
        assert all("commerce_offers" not in s or "commerce_opportunity_decisions" in s for s in conn.sqls())
        insert_sql = conn.sqls()[1]
        assert "INSERT INTO commerce_opportunity_decisions" in insert_sql
        assert "'REENGAGED'" in insert_sql
        params = conn.calls[1][2]
        assert params[6] == 42  # sealed_offer_id, the same existing offer

    @pytest.mark.asyncio
    async def test_touch_idempotent_across_passes(self, monkeypatch):
        existing = {"opportunity_id": 6, "reengagement_of": 5}
        conn = patch_ledger_pool(
            monkeypatch, FakeConn(fetchrows=[{"opportunity_id": 5}, None, existing])
        )
        from commerce.opportunity_ledger import record_reengagement_touch

        out = await record_reengagement_touch(creator_id=1, user_id=10, offer=stale_offer())
        assert out == existing
        assert "ON CONFLICT (creator_id, user_id, sealed_offer_id)" in conn.sqls()[1]

    @pytest.mark.asyncio
    async def test_touch_without_parent_recorded_honestly(self, monkeypatch):
        conn = patch_ledger_pool(
            monkeypatch, FakeConn(fetchrows=[None, {"opportunity_id": 8, "reengagement_of": None}])
        )
        from commerce.opportunity_ledger import record_reengagement_touch

        out = await record_reengagement_touch(creator_id=1, user_id=10, offer=stale_offer())
        assert out["reengagement_of"] is None
        params = conn.calls[1][2]
        assert params[7] == "unattributed"  # confidence, never fabricated

    @pytest.mark.asyncio
    async def test_purchase_after_reengagement_hits_parent_once(self, monkeypatch):
        updated = {"opportunity_id": 5, "outcome_state": "PURCHASED"}
        conn = patch_ledger_pool(monkeypatch, FakeConn(fetchrows=[updated]))
        from commerce.opportunity_ledger import record_purchase_by_offer

        out = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-re", price_minor=1999, currency="USD"
        )
        assert out["opportunity_id"] == 5
        assert len(conn.sqls()) == 1  # child fallback never runs: no double revenue

    @pytest.mark.asyncio
    async def test_touch_scope_mismatch_raises(self, monkeypatch):
        patch_ledger_pool(monkeypatch, FakeConn())
        from commerce.opportunity_ledger import record_reengagement_touch

        with pytest.raises(ValueError):
            await record_reengagement_touch(creator_id=2, user_id=10, offer=stale_offer())


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    @pytest.mark.asyncio
    async def test_ledger_raises_fail_loud_for_direct_callers(self, monkeypatch):
        async def boom():
            raise RuntimeError("db down")

        monkeypatch.setattr("commerce.opportunity_ledger.get_pool", boom)
        from commerce.opportunity_ledger import record_opportunity_decision

        with pytest.raises(RuntimeError):
            await record_opportunity_decision(
                creator_id=1, user_id=10, generation_id="g",
                evaluated_at=NOW, opportunity_result=engine_result(),
            )

    @pytest.mark.asyncio
    async def test_purchase_succeeds_when_ledger_down(self, monkeypatch):
        candidate = {"id": 7, "user_id": 10, "product_id": 77}
        updated = {"id": 7, "price_minor": 1999, "currency": "USD", "purchased_at": NOW}
        conn = FakeConn(fetchrows=[updated, None])
        conn._fetches = [[candidate]]
        patch_dao_pool(monkeypatch, conn)
        monkeypatch.setattr("core.event_bus.publish_event", AsyncMock(return_value="evt-1"))

        async def ledger_boom(**kw):
            raise RuntimeError("ledger down")

        monkeypatch.setattr(
            "commerce.opportunity_ledger.record_purchase_by_offer", ledger_boom
        )
        from commerce.dao import attribute_purchase_from_webhook

        record = await attribute_purchase_from_webhook(
            creator_id=1, product_id=77, transaction_id="txn-iso"
        )
        assert record is not None and record.offer_id == 7

    def test_call_sites_isolate_ledger(self):
        root = Path(__file__).parent.parent
        for rel, tokens in [
            ("workers/llm_worker.py", ["record_opportunity_decision", "link_opportunity_seal", "record_opportunity_send"]),
            ("commerce/dao.py", ["record_purchase_by_offer", "record_offer_terminal_outcome"]),
            ("commerce/reconciliation.py", ["record_purchase_by_offer"]),
            ("workers/scheduler_worker.py", ["record_reengagement_touch"]),
        ]:
            src = (root / rel).read_text(encoding="utf-8")
            for token in tokens:
                idx = src.find(token)
                assert idx != -1, f"{token} missing in {rel}"
                window = src[max(0, idx - 1200): idx + 600]
                assert "try:" in window and "except Exception" in window, (
                    f"{token} in {rel} is not failure-isolated"
                )

    def test_policy_files_untouched_by_ledger(self):
        root = Path(__file__).parent.parent
        for rel in [
            "commerce/opportunity_eligibility.py",
            "commerce/opportunity_ranking.py",
            "commerce/opportunity_sealing.py",
            "commerce/ownership.py",
        ]:
            src = (root / rel).read_text(encoding="utf-8")
            assert "opportunity_ledger" not in src, f"policy file {rel} references ledger"


# ---------------------------------------------------------------------------
# Historical classification (no fabrication)
# ---------------------------------------------------------------------------


class TestHistoricalClassification:
    def test_sealed_purchased_is_full(self):
        from commerce.opportunity_ledger import classify_historical_offer

        assert classify_historical_offer({
            "reason": sealed_envelope(), "state": "purchased", "transaction_id": "t1",
        }) == "full"

    def test_sealed_pending_is_partial(self):
        from commerce.opportunity_ledger import classify_historical_offer

        assert classify_historical_offer({
            "reason": sealed_envelope(), "state": "pending", "transaction_id": None,
        }) == "partial"

    def test_legacy_and_malformed_are_unattributed(self):
        from commerce.opportunity_ledger import classify_historical_offer

        assert classify_historical_offer({"reason": "legacy free text", "state": "purchased", "transaction_id": "t"}) == "unattributed"
        assert classify_historical_offer({"reason": None, "state": "pending"}) == "unattributed"
        # Pre-P3.5.1 decline-overwritten rows lost their envelope: unattributed.
        assert classify_historical_offer({"reason": "rejection_hard", "state": "declined", "transaction_id": None}) == "unattributed"
        # Intact envelope + non-purchase terminal (new decline behavior): partial.
        assert classify_historical_offer({"reason": sealed_envelope(), "state": "declined", "transaction_id": None}) == "partial"
        assert classify_historical_offer(None) == "unattributed"

    def test_backfill_snapshot_never_fabricates_ranking(self):
        from commerce.opportunity_ledger import build_backfill_snapshot

        snap = build_backfill_snapshot({
            "id": 1, "creator_id": 1, "user_id": 10, "state": "purchased",
            "price_minor": 1999, "currency": "USD", "dropfans_product_id": "d",
            "vault_item_ids": ["V1"], "transaction_id": "t",
            "created_at": NOW, "purchased_at": NOW, "reason": sealed_envelope(),
        })
        assert snap["attribution_confidence"] == "full"
        assert snap["provenance"]["definition_id"] == 11
        for forbidden in ("eligible", "ranking", "selected", "factors", "policy_version"):
            assert forbidden not in snap, f"fabricated key {forbidden}"


# ---------------------------------------------------------------------------
# Click semantics + migration + envelope
# ---------------------------------------------------------------------------


class TestClickMigrationEnvelope:
    def test_click_outcome_unavailable_no_production_writer(self):
        root = Path(__file__).parent.parent
        writers = []
        for path in list((root / "commerce").glob("*.py")) + list((root / "workers").glob("*.py")) + list((root / "chatbotv2").rglob("*.py")):
            try:
                src = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if "mark_offer_clicked(" in src and "async def mark_offer_clicked" not in src and "test" not in path.parts:
                writers.append(str(path.relative_to(root)))
        assert writers == [], f"unexpected click writers: {writers}"

    @pytest.mark.asyncio
    async def test_terminal_outcome_accepts_click_state_for_future_use(self, monkeypatch):
        conn = patch_ledger_pool(monkeypatch, FakeConn(fetches=[[]]))
        from commerce.opportunity_ledger import record_offer_terminal_outcome

        # No production writer exists (see test above); the state is accepted
        # so future instrumentation needs no schema change.
        out = await record_offer_terminal_outcome(creator_id=1, offer_id=1, outcome_state="CLICKED_NO_PURCHASE")
        assert out == []
        fetch_calls = [c for c in conn.calls if c[0] == "fetch"]
        assert fetch_calls and fetch_calls[0][2][2] == "CLICKED_NO_PURCHASE"

    def test_migration_shape(self):
        src = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS commerce_opportunity_decisions" in src
        for token in [
            "idx_opportunity_decisions_creator_generation",
            "idx_opportunity_decisions_reengage_dedup",
            "idx_opportunity_decisions_creator_definition",
            "idx_opportunity_decisions_creator_outcome",
            "idx_opportunity_decisions_sealed_offer",
            "idx_opportunity_decisions_reengagement_of",
        ]:
            assert token in src, f"missing index {token}"
        assert "ALTER TABLE commerce_offers" not in src
        assert "DROP" not in src
        upper = src.upper()
        assert "INSERT INTO" not in upper and "UPDATE " not in upper  # schema only, no backfill

    def test_envelope_keys_preserved(self):
        src = (Path(__file__).parent.parent / "commerce" / "dao.py").read_text(encoding="utf-8")
        assert "merge_outcome_into_reason" in src
