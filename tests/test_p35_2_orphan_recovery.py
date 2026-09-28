"""P3.5.2 — Opportunity Decision Orphan Recovery (unit, no DB/provider/Redis writes).

Proves bounded recovery of missing commerce_opportunity_decisions rows for
already-sealed commerce_offers without fabricating history or widening
commercial authority:

- pure recovery classification (fail-closed)
- sealed orphan -> recoverable; malformed/missing/ambiguous -> skipped
- creator-scoped exact definition validation (retired allowed, no substitution)
- deterministic synthetic generation ids + recovery marker + partial confidence
- no fabricated ranking/candidate/fan/history facts
- idempotent recovery, advisory locking, dry-run safety, per-row isolation
- P3.5.2.0 residual regression: second same-txn invocation with a spare
  eligible row must NOT mark that spare row PURCHASED via the safe path
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit]

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def sealed_envelope(definition_id=11, version=2, **over):
    env = {
        "v": 1,
        "definition_id": definition_id,
        "definition_version": version,
        "stable_key": "alpha",
        "sealed_at": "2026-09-01T00:00:00+00:00",
        "verified_at": "2026-09-01T00:00:00+00:00",
        "verified_hash": "h",
        "allow_download": True,
        "verifier_version": "p33.13.v1",
    }
    env.update(over)
    return json.dumps(env, sort_keys=True)


def stale_offer(**over):
    base = {
        "id": 42,
        "creator_id": 1,
        "user_id": 10,
        "product_id": 77,
        "link": "https://www.dropfans.io/buy/drop_x",
        "price_minor": 1999,
        "currency": "USD",
        "state": "pending",
        "reason": sealed_envelope(),
        "created_by": "opportunity_sealing",
        "created_at": NOW,
        "dropfans_product_id": "drop_x",
        "vault_item_ids": ["V1", "V2"],
        "transaction_id": None,
        "purchased_at": None,
    }
    base.update(over)
    return base


def definition_row(**over):
    base = {
        "id": 11,
        "creator_id": 1,
        "stable_key": "alpha",
        "version": 2,
        "status": "active",
    }
    base.update(over)
    return base


class FakeConn:
    """Scripted asyncpg stand-in with transaction support. Records all SQL."""

    def __init__(self, fetchrows=None, fetches=None, executes=None):
        self._fetchrows = list(fetchrows or [])
        self._fetches = list(fetches or [])
        self._executes = list(executes or [])
        self.calls: list[tuple] = []

    async def fetchrow(self, sql, *params):
        self.calls.append(("fetchrow", sql, params))
        if not self._fetchrows:
            return None
        result = self._fetchrows.pop(0)
        return result() if callable(result) else result

    async def fetch(self, sql, *params):
        self.calls.append(("fetch", sql, params))
        if not self._fetches:
            return []
        result = self._fetches.pop(0)
        return result() if callable(result) else result

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

    def sqls(self, method=None):
        if method is None:
            return [c[1] for c in self.calls]
        return [c[1] for c in self.calls if c[0] == method]


def make_pool(conn):
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    return pool


def patch_recovery_pool(monkeypatch, conn):
    monkeypatch.setattr(
        "commerce.opportunity_recovery.get_pool", AsyncMock(return_value=make_pool(conn))
    )
    return conn


def patch_definition(monkeypatch, value):
    async def _get(creator_id, definition_id, version=None):
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("db.offer_definitions.get_offer_definition", _get)


# ---------------------------------------------------------------------------
# A. Pure recovery classification
# ---------------------------------------------------------------------------


class TestPureClassification:
    def test_valid_sealed_orphan_is_recoverable(self):
        from commerce.opportunity_recovery import plan_recovery

        plan = plan_recovery(
            offer=stale_offer(), definition=definition_row(), linked_rows=[],
            expected_creator_id=1,
        )
        assert plan["classification"] == "RECOVERABLE"
        assert plan["outcome_state"] == "PENDING"
        assert plan["generation_id"] == "recovered:1:42"

    def test_purchased_maps_to_attributed_purchase(self):
        from commerce.opportunity_recovery import plan_recovery

        offer = stale_offer(state="purchased", transaction_id="txn-1", purchased_at=NOW)
        plan = plan_recovery(offer=offer, definition=definition_row(), linked_rows=[])
        assert plan["classification"] == "RECOVERABLE"
        assert plan["outcome_state"] == "PURCHASED"
        assert plan["attribution_status"] == "attributed"

    def test_terminal_states_map_honestly(self):
        from commerce.opportunity_recovery import plan_recovery

        for state, outcome in [("declined", "DECLINED"), ("expired", "EXPIRED"), ("revoked", "REVOKED")]:
            plan = plan_recovery(
                offer=stale_offer(state=state), definition=definition_row(), linked_rows=[],
            )
            assert plan["outcome_state"] == outcome, state
            assert plan["attribution_status"] == "unattributed"

    def test_clicked_never_fabricates_terminal_click(self):
        from commerce.opportunity_recovery import plan_recovery

        plan = plan_recovery(
            offer=stale_offer(state="clicked"), definition=definition_row(), linked_rows=[],
        )
        assert plan["outcome_state"] == "PENDING"

    def test_missing_envelope_is_legacy(self):
        from commerce.opportunity_recovery import plan_recovery

        plan = plan_recovery(
            offer=stale_offer(reason=None), definition=definition_row(), linked_rows=[],
        )
        assert plan["classification"] == "LEGACY_UNATTRIBUTABLE"

    def test_malformed_envelope_skipped(self):
        from commerce.opportunity_recovery import plan_recovery

        for bad in ["not json", json.dumps({"v": 1}), json.dumps({"v": 2, "definition_id": 11, "definition_version": 2, "stable_key": "alpha"}), "   "]:
            plan = plan_recovery(
                offer=stale_offer(reason=bad), definition=definition_row(), linked_rows=[],
            )
            assert plan["classification"] in ("MALFORMED_ENVELOPE", "LEGACY_UNATTRIBUTABLE"), bad

    def test_missing_definition_skipped(self):
        from commerce.opportunity_recovery import plan_recovery

        plan = plan_recovery(offer=stale_offer(), definition=None, linked_rows=[])
        assert plan["classification"] == "DEFINITION_MISSING"

    def test_definition_identity_mismatch_skipped(self):
        from commerce.opportunity_recovery import plan_recovery

        assert plan_recovery(
            offer=stale_offer(), definition=definition_row(version=3), linked_rows=[],
        )["classification"] == "DEFINITION_INVALID"
        assert plan_recovery(
            offer=stale_offer(), definition=definition_row(stable_key="beta"), linked_rows=[],
        )["classification"] == "DEFINITION_INVALID"
        assert plan_recovery(
            offer=stale_offer(), definition=definition_row(creator_id=2), linked_rows=[],
        )["classification"] == "DEFINITION_INVALID"

    def test_retired_definition_still_validates(self):
        from commerce.opportunity_recovery import plan_recovery

        plan = plan_recovery(
            offer=stale_offer(), definition=definition_row(status="retired"), linked_rows=[],
        )
        assert plan["classification"] == "RECOVERABLE"

    def test_creator_mismatch(self):
        from commerce.opportunity_recovery import plan_recovery

        plan = plan_recovery(
            offer=stale_offer(), definition=definition_row(), linked_rows=[],
            expected_creator_id=2,
        )
        assert plan["classification"] == "CREATOR_MISMATCH"

    def test_already_linked_and_purchased(self):
        from commerce.opportunity_recovery import plan_recovery

        linked = [{"opportunity_id": 5, "outcome_state": "SENT"}]
        assert plan_recovery(
            offer=stale_offer(), definition=definition_row(), linked_rows=linked,
        )["classification"] == "ALREADY_LINKED"
        purchased = [{"opportunity_id": 5, "outcome_state": "PURCHASED"}]
        assert plan_recovery(
            offer=stale_offer(), definition=definition_row(), linked_rows=purchased,
        )["classification"] == "ALREADY_PURCHASED"

    def test_unknown_offer_is_unrecoverable(self):
        from commerce.opportunity_recovery import plan_recovery

        assert plan_recovery(offer=None, definition=None, linked_rows=[])["classification"] == "UNRECOVERABLE"
        assert plan_recovery(offer={"id": 1}, definition=None, linked_rows=[])["classification"] == "UNRECOVERABLE"


# ---------------------------------------------------------------------------
# B–I. Recovery operation behavior (mocked)
# ---------------------------------------------------------------------------


class TestRecoveryOperation:
    @pytest.mark.asyncio
    async def test_valid_sealed_orphan_recovers(self, monkeypatch):
        offer = stale_offer()
        inserted = {"opportunity_id": 100, "decision_status": "SEALED", "sealed_offer_id": 42}
        conn = patch_recovery_pool(monkeypatch, FakeConn(fetchrows=[dict(offer), dict(inserted)], fetches=[[]]))
        patch_definition(monkeypatch, definition_row())
        from commerce.opportunity_recovery import recover_orphan_offer

        out = await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=False)
        assert out["classification"] == "RECOVERED"
        insert_sql = [s for s in conn.sqls("fetchrow") if "INSERT INTO commerce_opportunity_decisions" in s]
        assert len(insert_sql) == 1
        assert "ON CONFLICT (creator_id, generation_id)" in insert_sql[0]
        assert "'SEALED'" in insert_sql[0]

    @pytest.mark.asyncio
    async def test_recovered_pending_has_no_fabricated_purchase(self, monkeypatch):
        offer = stale_offer(state="clicked")
        conn = patch_recovery_pool(
            monkeypatch, FakeConn(fetchrows=[dict(offer), {"opportunity_id": 100}], fetches=[[]])
        )
        patch_definition(monkeypatch, definition_row())
        from commerce.opportunity_recovery import recover_orphan_offer

        out = await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=False)
        assert out["classification"] == "RECOVERED"
        params = [c[2] for c in conn.calls if c[0] == "fetchrow" and "INSERT INTO" in c[1]][0]
        assert params[10] == "PENDING"  # outcome_state
        assert params[12] is None  # transaction_id

    @pytest.mark.asyncio
    async def test_recovered_purchased_carries_authoritative_outcome(self, monkeypatch):
        offer = stale_offer(state="purchased", transaction_id="txn-9", purchased_at=NOW)
        conn = patch_recovery_pool(
            monkeypatch, FakeConn(fetchrows=[dict(offer), {"opportunity_id": 101}], fetches=[[]])
        )
        patch_definition(monkeypatch, definition_row())
        from commerce.opportunity_recovery import recover_orphan_offer

        out = await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=False)
        assert out["classification"] == "RECOVERED"
        params = [c[2] for c in conn.calls if c[0] == "fetchrow" and "INSERT INTO" in c[1]][0]
        assert params[10] == "PURCHASED"
        assert params[12] == "txn-9"
        assert params[13] == 1999 and params[14] == "USD"
        assert params[15] == "attributed"
        # record_purchase_by_offer must never be invoked by recovery itself.
        assert not any("opportunity_id = (" in c[1] and "PURCHASED" in c[1].upper() for c in conn.calls if c[0] == "fetchrow" and "UPDATE" in c[1].upper())

    @pytest.mark.asyncio
    async def test_already_linked_no_duplicate(self, monkeypatch):
        offer = stale_offer()
        linked = [{"opportunity_id": 5, "outcome_state": "SENT", "transaction_id": None}]
        conn = patch_recovery_pool(monkeypatch, FakeConn(fetchrows=[dict(offer)], fetches=[linked]))
        patch_definition(monkeypatch, definition_row())
        from commerce.opportunity_recovery import recover_orphan_offer

        out = await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=False)
        assert out["classification"] == "ALREADY_LINKED"
        assert not any("INSERT INTO" in c[1] for c in conn.calls)

    @pytest.mark.asyncio
    async def test_already_purchased_never_invokes_purchase_attribution(self, monkeypatch):
        offer = stale_offer(state="purchased", transaction_id="txn-9", purchased_at=NOW)
        linked = [{"opportunity_id": 5, "outcome_state": "PURCHASED", "transaction_id": "txn-9"}]
        conn = patch_recovery_pool(monkeypatch, FakeConn(fetchrows=[dict(offer)], fetches=[linked]))
        patch_definition(monkeypatch, definition_row())
        calls = []
        monkeypatch.setattr(
            "commerce.opportunity_ledger.record_purchase_by_offer",
            AsyncMock(side_effect=lambda **kw: calls.append(kw) or {"opportunity_id": 5}),
        )
        from commerce.opportunity_recovery import recover_orphan_offer

        out = await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=False)
        assert out["classification"] == "ALREADY_PURCHASED"
        assert calls == []
        assert not any("INSERT INTO" in c[1] for c in conn.calls)

    @pytest.mark.asyncio
    async def test_definition_lookup_failure_is_explicit_error(self, monkeypatch):
        offer = stale_offer()
        conn = patch_recovery_pool(monkeypatch, FakeConn(fetchrows=[dict(offer)], fetches=[[]]))
        patch_definition(monkeypatch, RuntimeError("db down"))
        from commerce.opportunity_recovery import recover_orphan_offer

        out = await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=False)
        assert out["classification"] == "RECOVERY_ERROR"
        assert not any("INSERT INTO" in c[1] for c in conn.calls)

    @pytest.mark.asyncio
    async def test_recovery_takes_creator_offer_advisory_lock(self, monkeypatch):
        offer = stale_offer()
        conn = patch_recovery_pool(monkeypatch, FakeConn(fetchrows=[dict(offer)], fetches=[[]]))
        patch_definition(monkeypatch, definition_row())
        from commerce.opportunity_recovery import recover_orphan_offer

        await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=True)
        lock_calls = [c for c in conn.calls if c[0] == "execute" and "pg_advisory_xact_lock" in c[1]]
        assert lock_calls and lock_calls[0][2][0] == "recovery:1:42"

    @pytest.mark.asyncio
    async def test_concurrent_recovery_conflict_returns_winner(self, monkeypatch):
        offer = stale_offer()
        existing = {"opportunity_id": 77, "generation_id": "recovered:1:42"}
        conn = patch_recovery_pool(
            monkeypatch, FakeConn(fetchrows=[dict(offer), None, dict(existing)], fetches=[[]])
        )
        patch_definition(monkeypatch, definition_row())
        from commerce.opportunity_recovery import recover_orphan_offer

        out = await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=False)
        assert out["classification"] == "ALREADY_LINKED"
        assert out["reason"] == "concurrent_recovery_won"


# ---------------------------------------------------------------------------
# J–M. Generation scheme, marker, confidence, no fabrication
# ---------------------------------------------------------------------------


class TestRecoveryHonesty:
    def test_synthetic_generation_determinism(self):
        from commerce.opportunity_recovery import synthetic_generation_id

        assert synthetic_generation_id(1, 42) == "recovered:1:42"
        assert synthetic_generation_id(1, 42) == synthetic_generation_id(1, 42)
        assert synthetic_generation_id(1, 43) != synthetic_generation_id(1, 42)
        assert synthetic_generation_id(2, 42) != synthetic_generation_id(1, 42)
        with pytest.raises(ValueError):
            synthetic_generation_id(0, 42)

    @pytest.mark.asyncio
    async def test_snapshot_marker_confidence_and_no_fabrication(self, monkeypatch):
        offer = stale_offer()
        conn = patch_recovery_pool(
            monkeypatch, FakeConn(fetchrows=[dict(offer), {"opportunity_id": 100}], fetches=[[]])
        )
        patch_definition(monkeypatch, definition_row())
        from commerce.opportunity_recovery import recover_orphan_offer

        out = await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=False)
        assert out["classification"] == "RECOVERED"
        params = [c[2] for c in conn.calls if c[0] == "fetchrow" and "INSERT INTO" in c[1]][0]
        snapshot = json.loads(params[4])
        assert snapshot["recovery"]["recovered"] is True
        assert snapshot["recovery"]["source"] == "commerce_offers"
        assert snapshot["recovery"]["generation_id"] == "recovered:1:42"
        assert snapshot["attribution_confidence"] == "partial"
        assert snapshot["eligible"] == [] and snapshot["ineligible"] == []
        assert snapshot["ranking"] == {
            "available": False, "policy_version": None, "ranked_order": [], "factors": {},
        }
        assert snapshot["conversation"] is None and snapshot["fan"] is None and snapshot["history"] is None
        assert "eligible_candidates" in snapshot["unavailable_facts"]
        assert snapshot["selected"] == {"definition_id": 11, "version": 2, "stable_key": "alpha"}
        assert snapshot["provenance"]["definition_id"] == 11
        assert "purchase" not in json.dumps(snapshot["ranking"]).lower()

    def test_snapshot_never_contains_llm_or_traits(self):
        import pathlib

        src = pathlib.Path(__file__).parent.parent.joinpath(
            "commerce", "opportunity_recovery.py"
        ).read_text(encoding="utf-8")
        # Scan imports/calls only: docstrings and the explicit
        # _UNAVAILABLE_FACTS honesty markers legitimately name these families.
        code_lines = [
            ln for ln in src.splitlines()
            if ln.strip().startswith(("import ", "from ")) or "(" in ln
        ]
        joined = "\n".join(code_lines)
        for token in ("llm_client", "suggest_", "infer_", "segment", "salesCount", "seller_earning", "set_price"):
            assert token not in joined, f"recovery must not reference {token}"


# ---------------------------------------------------------------------------
# N. Creator isolation
# ---------------------------------------------------------------------------


class TestCreatorIsolation:
    @pytest.mark.asyncio
    async def test_recovery_queries_are_creator_scoped(self, monkeypatch):
        offer = stale_offer()
        conn = patch_recovery_pool(monkeypatch, FakeConn(fetchrows=[dict(offer)], fetches=[[]]))
        patch_definition(monkeypatch, definition_row())
        from commerce.opportunity_recovery import recover_orphan_offer

        await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=True)
        for method, sql, params in conn.calls:
            if "commerce_offers" in sql and "SELECT" in sql.upper():
                assert params[0] == 1 and params[1] == 42
            if "commerce_opportunity_decisions" in sql and "sealed_offer_id" in sql:
                assert params[0] == 1 and params[1] == 42

    def test_same_offer_id_other_creator_is_distinct(self):
        from commerce.opportunity_recovery import synthetic_generation_id

        assert synthetic_generation_id(1, 42) != synthetic_generation_id(2, 42)


# ---------------------------------------------------------------------------
# O–S. Sweep: idempotency, bounds, dry-run, isolation
# ---------------------------------------------------------------------------


class TestRecoverySweep:
    @pytest.mark.asyncio
    async def test_idempotent_repeated_recovery(self, monkeypatch):
        from commerce.opportunity_recovery import recover_orphan_offer

        offer = stale_offer()
        state = {"rows": []}

        class _Conn(FakeConn):
            async def fetchrow(self, sql, *params):
                self.calls.append(("fetchrow", sql, params))
                if "FROM commerce_offers" in sql:
                    return dict(offer)
                if "INSERT INTO" in sql:
                    if any(r["generation_id"] == params[2] and r["creator_id"] == params[0] for r in state["rows"]):
                        return None
                    row = {"opportunity_id": 100, "generation_id": params[2], "creator_id": params[0]}
                    state["rows"].append(row)
                    return dict(row)
                if "generation_id = $2" in sql:
                    for r in state["rows"]:
                        if r["creator_id"] == params[0] and r["generation_id"] == params[1]:
                            return dict(r)
                    return None
                return None

            async def fetch(self, sql, *params):
                self.calls.append(("fetch", sql, params))
                return [r for r in state["rows"] if r.get("sealed_offer_id") == 42] if "sealed_offer_id" in sql else []

        conn = _Conn()
        patch_recovery_pool(monkeypatch, conn)
        patch_definition(monkeypatch, definition_row())
        first = await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=False)
        second = await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=False)
        # First recovers; second observes the linked row. Either way exactly one row exists.
        assert len(state["rows"]) == 1
        assert first["classification"] == "RECOVERED"
        assert second["classification"] in ("ALREADY_LINKED", "RECOVERED")

    @pytest.mark.asyncio
    async def test_sweep_is_bounded_and_deterministic(self, monkeypatch):
        conn = patch_recovery_pool(monkeypatch, FakeConn(fetches=[[]]))
        from commerce.opportunity_recovery import recover_orphan_decisions

        out = await recover_orphan_decisions(limit=500, dry_run=True)
        assert out["checked"] == 0 and out["dry_run"] is True
        sql = conn.sqls("fetch")[0]
        assert "LIMIT $1" in sql
        assert "ORDER BY co.creator_id ASC, co.id ASC" in sql
        assert "LEFT JOIN commerce_opportunity_decisions" in sql
        assert conn.calls[0][2][0] == 100  # capped at RECOVERY_SWEEP_MAX

    @pytest.mark.asyncio
    async def test_dry_run_produces_no_writes(self, monkeypatch):
        offer = stale_offer()
        conn = patch_recovery_pool(monkeypatch, FakeConn(fetchrows=[dict(offer)], fetches=[[]]))
        patch_definition(monkeypatch, definition_row())
        from commerce.opportunity_recovery import recover_orphan_offer

        out = await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=True)
        assert out["classification"] == "RECOVERABLE"
        assert out["dry_run"] is True
        assert not any("INSERT INTO" in c[1] for c in conn.calls)
        assert not any("UPDATE" in c[1].upper() and "PURCHASED" in c[1].upper() for c in conn.calls)

    @pytest.mark.asyncio
    async def test_sweep_per_row_failure_isolation(self, monkeypatch):
        from commerce.opportunity_recovery import recover_orphan_decisions

        cands = [stale_offer(id=41), stale_offer(id=42)]
        conn = patch_recovery_pool(monkeypatch, FakeConn(fetches=[cands]))
        patch_definition(monkeypatch, definition_row())
        import commerce.opportunity_recovery as rec

        async def _recover(*, creator_id, offer_id, dry_run=True):
            if offer_id == 41:
                raise RuntimeError("boom")
            return {"classification": "RECOVERABLE", "dry_run": True}

        monkeypatch.setattr(rec, "recover_orphan_offer", _recover)
        out = await recover_orphan_decisions(limit=10, dry_run=True)
        assert out["failed"] == 1 and out["checked"] == 1

    @pytest.mark.asyncio
    async def test_sweep_query_failure_is_explicit(self, monkeypatch):
        conn = FakeConn()
        patch_recovery_pool(monkeypatch, conn)

        async def _boom(sql, *params):
            raise RuntimeError("db down")

        monkeypatch.setattr(conn, "fetch", _boom)
        from commerce.opportunity_recovery import recover_orphan_decisions

        out = await recover_orphan_decisions(limit=10, dry_run=True)
        assert out["error"] == "sweep_query_failed"
        assert out["checked"] == 0


# ---------------------------------------------------------------------------
# T. Ambiguous purchase path + authority boundary
# ---------------------------------------------------------------------------


class TestAmbiguousAndAuthority:
    def test_ambiguous_resolver_authority_unchanged(self):
        src = Path(__file__).parent.parent.joinpath("commerce", "dao.py").read_text(encoding="utf-8")
        fn_src = src.split("async def resolve_ambiguous_recovery")[1].split("\nasync def ")[0]
        assert "record_purchase_by_offer" not in fn_src
        assert "opportunity_recovery" not in fn_src
        assert "mark_offer_purchased" in fn_src  # authoritative path intact

    def test_purchased_without_transaction_is_unattributed_not_invented(self):
        from commerce.opportunity_recovery import plan_recovery

        offer = stale_offer(state="purchased", transaction_id=None, purchased_at=NOW)
        plan = plan_recovery(offer=offer, definition=definition_row(), linked_rows=[])
        assert plan["classification"] == "RECOVERABLE"
        assert plan["outcome_state"] == "PURCHASED"
        assert plan["attribution_status"] == "unattributed"

    def test_recovery_never_touches_commercial_authority(self):
        root = Path(__file__).parent.parent
        src = (root / "commerce" / "opportunity_recovery.py").read_text(encoding="utf-8")
        for token in (
            "from integrations",
            "get_drop",
            "get_earnings",
            "check_drop",
            "publish_event",
            "get_redis",
            "enqueue_send",
            "CREATE TABLE",
            "ALTER TABLE",
        ):
            assert token not in src, f"recovery must not contain {token}"
        import_lines = [
            ln.strip() for ln in src.splitlines()
            if ln.strip().startswith(("import ", "from "))
        ]
        joined_imports = "\n".join(import_lines)
        for mod in ("opportunity_eligibility", "opportunity_ranking", "opportunity_engine",
                    "ownership", "fan_commercial_state", "offer_history"):
            assert mod not in joined_imports, f"recovery must not depend on {mod}"

    def test_reconciliation_hook_is_isolated_and_count_preserving(self):
        root = Path(__file__).parent.parent
        src = (root / "commerce" / "reconciliation.py").read_text(encoding="utf-8")
        assert "recover_orphan_decisions" in src
        idx = src.find("recover_orphan_decisions")
        window = src[max(0, idx - 600): idx + 600]
        assert "try:" in window and "except Exception" in window
        assert "return dropfans_new + attributed + recovered" in src

    @pytest.mark.asyncio
    async def test_reconcile_all_survives_recovery_failure(self, monkeypatch):
        from unittest.mock import patch as _patch

        from commerce import reconciliation as rec

        with _patch.object(rec, "reconcile_dropfans_sales", AsyncMock(return_value=1)), \
             _patch.object(rec, "reconcile_unattributed_purchases", AsyncMock(return_value=1)), \
             _patch.object(rec, "recover_incomplete_post_purchases", AsyncMock(return_value=0)), \
             _patch.object(rec, "emit_attribution_divergence_snapshot", AsyncMock(return_value=0)), \
             _patch("commerce.opportunity_recovery.recover_orphan_decisions", AsyncMock(side_effect=RuntimeError("x"))):
            total = await rec.reconcile_all()
        assert total == 2


# ---------------------------------------------------------------------------
# U. Mandatory P3.5.2.0 residual regression through the safe path
# ---------------------------------------------------------------------------


class TestSafePurchaseRegression:
    @pytest.mark.asyncio
    async def test_second_same_txn_invocation_does_not_mark_spare_row(self, monkeypatch):
        """Two eligible rows, same creator/offer/txn.

        First attribution (authoritative single-winner) marks the lowest
        opportunity_id. The second invocation through the recovery-safe path
        must observe the existing winner and leave the spare row PENDING —
        it must NOT claim the spare row.
        """
        rows = [
            {"opportunity_id": 5, "creator_id": 1, "sealed_offer_id": 42,
             "outcome_state": "PENDING", "reengagement_of": None, "transaction_id": None},
            {"opportunity_id": 6, "creator_id": 1, "sealed_offer_id": 42,
             "outcome_state": "PENDING", "reengagement_of": None, "transaction_id": None},
        ]

        class _SharedConn(FakeConn):
            async def fetchrow(self, sql, *params):
                self.calls.append(("fetchrow", sql, params))
                upper = sql.upper()
                if "UPDATE" in upper and "PURCHASED" in upper:
                    creator, offer = params[0], params[1]
                    eligible = [
                        r for r in rows
                        if r["creator_id"] == creator and r["sealed_offer_id"] == offer
                        and r["outcome_state"] in ("PENDING", "SENT", "SEND_FAILED")
                        and r["reengagement_of"] is None
                    ]
                    if not eligible:
                        return None
                    eligible.sort(key=lambda r: r["opportunity_id"])
                    winner = eligible[0]
                    winner.update({
                        "outcome_state": "PURCHASED",
                        "transaction_id": params[3],
                        "attribution_status": "attributed",
                    })
                    return dict(winner)
                if "TRANSACTION_ID = $3" in upper:
                    cands = [r for r in rows if r["creator_id"] == params[0]
                             and r["sealed_offer_id"] == params[1]
                             and r.get("transaction_id") == params[2]]
                    cands.sort(key=lambda r: r["opportunity_id"])
                    return dict(cands[0]) if cands else None
                if "OUTCOME_STATE = 'PURCHASED'" in upper:
                    cands = [r for r in rows if r["creator_id"] == params[0]
                             and r["sealed_offer_id"] == params[1]
                             and r["outcome_state"] == "PURCHASED"]
                    cands.sort(key=lambda r: r["opportunity_id"])
                    return dict(cands[0]) if cands else None
                return None

            async def fetch(self, sql, *params):
                self.calls.append(("fetch", sql, params))
                return []

        conn = _SharedConn()
        monkeypatch.setattr(
            "commerce.opportunity_ledger.get_pool", AsyncMock(return_value=make_pool(conn))
        )
        monkeypatch.setattr(
            "commerce.opportunity_recovery.get_pool", AsyncMock(return_value=make_pool(conn))
        )
        from commerce.opportunity_ledger import record_purchase_by_offer
        from commerce.opportunity_recovery import attribute_purchase_safe

        first = await record_purchase_by_offer(
            creator_id=1, offer_id=42, transaction_id="txn-9",
            price_minor=1999, currency="USD",
        )
        assert first["opportunity_id"] == 5
        second = await attribute_purchase_safe(
            creator_id=1, offer_id=42, transaction_id="txn-9",
            price_minor=1999, currency="USD",
        )
        assert second["opportunity_id"] == 5
        assert second["transaction_id"] == "txn-9"
        by_id = {r["opportunity_id"]: r for r in rows}
        assert by_id[5]["outcome_state"] == "PURCHASED"
        assert by_id[6]["outcome_state"] == "PENDING"
        assert sum(1 for r in rows if r["outcome_state"] == "PURCHASED") == 1

    @pytest.mark.asyncio
    async def test_safe_path_delegates_once_when_nothing_linked(self, monkeypatch):
        conn = patch_recovery_pool(monkeypatch, FakeConn(fetchrows=[None, None]))
        delegated = []
        monkeypatch.setattr(
            "commerce.opportunity_ledger.record_purchase_by_offer",
            AsyncMock(side_effect=lambda **kw: delegated.append(kw) or {"opportunity_id": 5}),
        )
        from commerce.opportunity_recovery import attribute_purchase_safe

        out = await attribute_purchase_safe(
            creator_id=1, offer_id=42, transaction_id="txn-new", price_minor=1, currency="USD"
        )
        assert out == {"opportunity_id": 5}
        assert len(delegated) == 1
        assert delegated[0]["transaction_id"] == "txn-new"
