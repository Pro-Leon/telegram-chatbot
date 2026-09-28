"""P3.5.3B — Evidence Quality / Outcome Maturity (unit, no DB/provider/Redis writes).

Proves the durable evidence layer answers, at a label-as-of time, whether an
opportunity was sufficiently exposed and mature to serve as future learning
evidence — without fabricating delivery/clicks, negatives, or weights:

- exposure ladder honoring recorded send evidence (sent_exposure != delivered)
- deterministic versioned maturity; open states stay censored, never negative
- label-as-of cutoff with no temporal leakage
- evidence-quality cohorts without numerical weights
- re-engagement single-revenue semantics; creator isolation; recovery honesty
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit]

UTC = timezone.utc
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
WINDOW = timedelta(hours=7 * 24)


def row(**over):
    base = {
        "opportunity_id": 5,
        "creator_id": 1,
        "user_id": 10,
        "generation_id": "gen-1",
        "evaluated_at": NOW - timedelta(days=10),
        "decision_snapshot": json.dumps({"eligible": []}),
        "selected_definition_id": 11,
        "selected_version": 2,
        "selected_stable_key": "alpha",
        "decision_status": "SEALED",
        "sealed_offer_id": 42,
        "drop_cuid": "drop_x",
        "outcome_state": "PENDING",
        "outcome_at": None,
        "transaction_id": None,
        "purchased_price_minor": None,
        "purchased_currency": None,
        "attribution_status": "unattributed",
        "attribution_confidence": "full",
        "reengagement_of": None,
        "exposure_state": "NONE",
        "exposure_at": None,
        "exposure_source": None,
    }
    base.update(over)
    return base


def sent_row(**over):
    base = row(
        outcome_state="SENT",
        outcome_at=NOW - timedelta(days=9),
        exposure_state="SENT",
        exposure_at=NOW - timedelta(days=9),
        exposure_source="sealed_execution",
    )
    base.update(over)
    return base


def classify(r, as_of=None):
    from commerce.opportunity_evidence import classify_opportunity_evidence

    return classify_opportunity_evidence(r, as_of=as_of if as_of is not None else NOW)


class FakeConn:
    def __init__(self, fetchrows=None, fetches=None):
        self._fetchrows = list(fetchrows or [])
        self._fetches = list(fetches or [])
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
        return "UPDATE 1"

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def make_pool(conn):
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    return pool


def patch_evidence_pool(monkeypatch, conn):
    monkeypatch.setattr(
        "commerce.opportunity_evidence.get_pool", AsyncMock(return_value=make_pool(conn))
    )
    return conn


# ---------------------------------------------------------------------------
# Exposure (req 1-5)
# ---------------------------------------------------------------------------


class TestExposure:
    def test_decision_with_no_send_has_no_exposure(self):
        ev = classify(row(sealed_offer_id=None, decision_status="DECIDED"))
        assert ev["exposure_state"] == "DECISION"
        assert ev["exposure_at"] == ev["evaluated_at"]
        assert ev["label"] == "CENSORED"

    def test_sealed_pending_exposes_sealed_not_sent(self):
        ev = classify(row())
        assert ev["exposure_state"] == "SEALED"
        assert ev["exposure_at"] is None
        assert ev["label"] == "CENSORED"

    def test_successful_send_is_sent_exposure(self):
        ev = classify(sent_row())
        assert ev["exposure_state"] == "SENT"
        assert ev["exposure_at"] == NOW - timedelta(days=9)
        assert ev["exposure_source"] == "sealed_execution"
        # Mature (9d > 7d window) but still censored — never a negative.
        assert ev["maturity_state"] == "MATURE"
        assert ev["label"] == "CENSORED"

    def test_send_failure_is_attempt_not_positive_exposure(self):
        ev = classify(
            row(
                outcome_state="SEND_FAILED",
                outcome_at=NOW - timedelta(days=9),
                exposure_state="SEND_ATTEMPTED",
                exposure_at=NOW - timedelta(days=9),
                exposure_source="sealed_execution",
            )
        )
        assert ev["exposure_state"] == "SEND_ATTEMPTED"
        assert ev["label"] == "PROCESS_NEGATIVE"

    def test_exposure_does_not_alter_decision_time_fields(self):
        r = sent_row()
        before = {k: r[k] for k in (
            "decision_snapshot", "selected_definition_id", "selected_version",
            "selected_stable_key", "generation_id", "evaluated_at",
        )}
        classify(r)
        assert {k: r[k] for k in before} == before

    def test_sent_means_recorded_send_not_delivery(self):
        import pathlib

        src = pathlib.Path(__file__).parent.parent.joinpath(
            "commerce", "opportunity_evidence.py"
        ).read_text(encoding="utf-8")
        assert "NOT confirmed recipient delivery or click" in src
        assert "purchases / sent_exposures" in src


# ---------------------------------------------------------------------------
# Maturity + label-as-of (req 6-12)
# ---------------------------------------------------------------------------


class TestMaturity:
    def test_recent_pending_is_immature(self):
        ev = classify(row(evaluated_at=NOW - timedelta(hours=1), outcome_at=None))
        assert ev["maturity_state"] == "IMMATURE"
        assert ev["label"] == "CENSORED"
        assert ev["maturity_policy_version"] == "p353b.v1"
        assert ev["maturity_at"] == NOW - timedelta(hours=1) + WINDOW

    def test_recent_sent_is_immature(self):
        at = NOW - timedelta(hours=2)
        ev = classify(sent_row(outcome_at=at, exposure_at=at))
        assert ev["maturity_state"] == "IMMATURE"
        assert ev["label"] == "CENSORED"

    def test_mature_unresolved_opportunity_stays_censored(self):
        ev = classify(sent_row())  # 9 days old, no purchase
        assert ev["maturity_state"] == "MATURE"
        assert ev["label"] == "CENSORED"

    def test_purchase_before_cutoff_is_positive_mature(self):
        ev = classify(
            row(
                outcome_state="PURCHASED",
                outcome_at=NOW - timedelta(days=8),
                transaction_id="txn-1",
                purchased_price_minor=1999,
                purchased_currency="USD",
                attribution_status="attributed",
                exposure_state="SENT",
                exposure_at=NOW - timedelta(days=9),
                exposure_source="sealed_execution",
            )
        )
        assert ev["label"] == "POSITIVE"
        assert ev["maturity_state"] == "MATURE"
        assert ev["evidence_quality"] == "FULL"

    def test_purchase_after_cutoff_is_invisible(self):
        bought = row(
            outcome_state="PURCHASED",
            outcome_at=NOW - timedelta(days=1),
            transaction_id="txn-1",
            attribution_status="attributed",
        )
        ev = classify(bought, as_of=NOW - timedelta(days=5))
        assert ev["label"] == "CENSORED"
        assert ev["outcome_state"] is None  # future outcome not leaked
        assert ev["observed_outcome_state"] == "PURCHASED"  # transparency preserved

    def test_maturity_is_deterministic(self):
        r = sent_row()
        assert classify(r) == classify(r)
        assert classify(r, as_of=NOW)["maturity_at"] == NOW - timedelta(days=9) + WINDOW

    def test_policy_version_explicit_and_window_pinned(self):
        from commerce import reconciliation as rec
        from commerce.opportunity_evidence import MATURITY_POLICY_VERSION, MATURITY_WINDOW_HOURS

        assert MATURITY_POLICY_VERSION == "p353b.v1"
        assert MATURITY_WINDOW_HOURS == rec.RECONCILIATION_WINDOW_HOURS == 168

    def test_naive_as_of_raises(self):
        from commerce.opportunity_evidence import classify_opportunity_evidence

        with pytest.raises(ValueError):
            classify_opportunity_evidence(row(), as_of=datetime(2026, 9, 1))


# ---------------------------------------------------------------------------
# Negative evidence (req 13-19)
# ---------------------------------------------------------------------------


class TestNegativeEvidence:
    def test_declined_negative_only_when_mature(self):
        recent = classify(
            row(outcome_state="DECLINED", outcome_at=NOW - timedelta(hours=1))
        )
        assert recent["label"] == "COMMERCIAL_NEGATIVE"  # terminal: mature once recorded
        assert recent["maturity_state"] == "MATURE"
        future_cutoff = classify(
            row(outcome_state="DECLINED", outcome_at=NOW - timedelta(hours=1)),
            as_of=NOW - timedelta(days=1),
        )
        assert future_cutoff["label"] == "CENSORED"  # decline not knowable yet

    def test_expired_negative_only_when_mature(self):
        ev = classify(row(outcome_state="EXPIRED", outcome_at=NOW - timedelta(days=8)))
        assert ev["label"] == "COMMERCIAL_NEGATIVE"
        assert ev["maturity_state"] == "MATURE"

    def test_send_failure_remains_process_negative(self):
        ev = classify(
            row(
                outcome_state="SEND_FAILED",
                outcome_at=NOW - timedelta(days=8),
                exposure_state="SEND_ATTEMPTED",
                exposure_at=NOW - timedelta(days=8),
            )
        )
        assert ev["label"] == "PROCESS_NEGATIVE"
        assert ev["label"] != "COMMERCIAL_NEGATIVE"

    def test_seal_failure_remains_process_negative(self):
        ev = classify(
            row(
                decision_status="SEAL_FAILED",
                sealed_offer_id=None,
                outcome_state="SEAL_FAILED",
                outcome_at=NOW - timedelta(days=8),
            )
        )
        assert ev["label"] == "PROCESS_NEGATIVE"

    def test_no_row_is_unavailable_never_negative(self):
        from commerce.opportunity_evidence import classify_opportunity_evidence

        ev = classify_opportunity_evidence(None, as_of=NOW)
        assert ev["label"] == "UNAVAILABLE"
        assert ev["evidence_quality"] == "UNAVAILABLE"
        assert ev["maturity_state"] == "IMMATURE"

    def test_no_opportunity_distinct(self):
        ev = classify(
            row(
                decision_status="NO_OPPORTUNITY",
                sealed_offer_id=None,
                outcome_state="NO_OPPORTUNITY",
                outcome_at=NOW - timedelta(days=8),
                selected_definition_id=None,
                selected_version=None,
                selected_stable_key=None,
            )
        )
        assert ev["label"] == "NO_OPPORTUNITY"

    def test_no_selection_distinct(self):
        ev = classify(
            row(
                decision_status="NO_SELECTION",
                sealed_offer_id=None,
                outcome_state="NO_SELECTION",
                outcome_at=NOW - timedelta(days=8),
                selected_definition_id=None,
            )
        )
        assert ev["label"] == "NO_SELECTION"


# ---------------------------------------------------------------------------
# Evidence quality (req 20-24)
# ---------------------------------------------------------------------------


class TestEvidenceQuality:
    def test_normal_full_row_is_full(self):
        ev = classify(sent_row())
        assert ev["evidence_quality"] == "FULL"
        assert ev["recovered"] is False

    def test_recovered_row_is_partial(self):
        snap = json.dumps({"recovery": {"recovered": True}})
        ev = classify(
            row(
                generation_id="recovered:1:42",
                decision_snapshot=snap,
                attribution_confidence="partial",
                outcome_state="PURCHASED",
                outcome_at=NOW - timedelta(days=8),
                transaction_id="txn-1",
                attribution_status="attributed",
            )
        )
        assert ev["evidence_quality"] == "PARTIAL"
        assert ev["recovered"] is True
        assert ev["label"] == "POSITIVE"  # purchase still labels; quality carries honesty

    def test_unattributed_purchase_quality(self):
        ev = classify(
            row(
                outcome_state="PURCHASED",
                outcome_at=NOW - timedelta(days=8),
                transaction_id=None,
                attribution_status="unattributed",
            )
        )
        assert ev["evidence_quality"] == "UNATTRIBUTED"
        assert ev["label"] == "POSITIVE"

    def test_unavailable_quality(self):
        from commerce.opportunity_evidence import classify_opportunity_evidence

        assert classify_opportunity_evidence(None, as_of=NOW)["evidence_quality"] == "UNAVAILABLE"

    def test_no_numerical_weights(self):
        import pathlib
        import re

        src = pathlib.Path(__file__).parent.parent.joinpath(
            "commerce", "opportunity_evidence.py"
        ).read_text(encoding="utf-8")
        floats = set(re.findall(r"(?<![\w.])\d+\.\d+", src))
        floats -= {"353.0"}  # no such literal expected; guard set only
        assert floats == set(), f"numeric weight-like literals present: {floats}"
        assert "QUALITY" in src  # cohorts exist as classifications


# ---------------------------------------------------------------------------
# Re-engagement (req 25-28)
# ---------------------------------------------------------------------------


class TestReengagementEvidence:
    @pytest.mark.asyncio
    async def test_original_plus_child_do_not_double_count_purchase(self, monkeypatch):
        from commerce.opportunity_evidence import list_offer_exposures

        rows = [
            sent_row(opportunity_id=5, outcome_state="PURCHASED",
                     outcome_at=NOW - timedelta(days=8), transaction_id="txn-1",
                     attribution_status="attributed"),
            sent_row(opportunity_id=6, reengagement_of=5, outcome_state="SENT",
                     outcome_at=NOW - timedelta(days=7),
                     exposure_at=NOW - timedelta(days=7)),
        ]
        patch_evidence_pool(monkeypatch, FakeConn(fetches=[rows]))
        out = await list_offer_exposures(1, 42, as_of=NOW)
        assert out["revenue_events"] == 1
        assert out["purchase_winner_opportunity_id"] == 5
        by_id = {e["opportunity_id"]: e for e in out["exposures"]}
        assert by_id[5]["label"] == "POSITIVE"
        assert by_id[6]["label"] == "CENSORED"  # touch stays exposure-only

    def test_child_touch_individually_distinguishable(self):
        ev = classify(sent_row(opportunity_id=6, reengagement_of=5))
        assert ev["reengagement_of"] == 5
        assert ev["exposure_state"] == "SENT"

    def test_single_winner_attribution_intact(self):
        import pathlib

        src = pathlib.Path(__file__).parent.parent.joinpath(
            "commerce", "opportunity_ledger.py"
        ).read_text(encoding="utf-8")
        assert "ORDER BY opportunity_id ASC" in src and "LIMIT 1" in src

    def test_child_maturity_deterministic(self):
        at = NOW - timedelta(hours=3)
        ev = classify(sent_row(opportunity_id=6, reengagement_of=5, outcome_at=at, exposure_at=at))
        assert ev["maturity_state"] == "IMMATURE"
        assert ev["maturity_at"] == at + WINDOW


# ---------------------------------------------------------------------------
# Creator isolation (req 29-31)
# ---------------------------------------------------------------------------


class TestCreatorIsolation:
    @pytest.mark.asyncio
    async def test_same_offer_across_creators_isolated(self, monkeypatch):
        conn = patch_evidence_pool(monkeypatch, FakeConn(fetchrows=[sent_row()]))
        from commerce.opportunity_evidence import get_evidence

        out = await get_evidence(1, 5, as_of=NOW)
        assert out["creator_id"] == 1
        sql, params = conn.calls[0][1], conn.calls[0][2]
        assert "creator_id = $1" in sql and "opportunity_id = $2" in sql
        assert params == (1, 5)

    @pytest.mark.asyncio
    async def test_offer_listing_is_creator_scoped(self, monkeypatch):
        conn = patch_evidence_pool(monkeypatch, FakeConn(fetches=[[sent_row()]]))
        from commerce.opportunity_evidence import list_offer_exposures

        out = await list_offer_exposures(2, 42, as_of=NOW)
        assert out["creator_id"] == 2
        sql, params = conn.calls[0][1], conn.calls[0][2]
        assert "creator_id = $1" in sql and "sealed_offer_id = $2" in sql
        assert params[0] == 2 and params[1] == 42

    def test_generation_prefix_cannot_cross_creators(self):
        from commerce.opportunity_recovery import synthetic_generation_id

        assert synthetic_generation_id(1, 42) != synthetic_generation_id(2, 42)


# ---------------------------------------------------------------------------
# Temporal leakage (req 32-35)
# ---------------------------------------------------------------------------


class TestTemporalLeakage:
    def test_future_purchase_absent_from_historical_label(self):
        r = row(
            outcome_state="PURCHASED",
            outcome_at=NOW,
            transaction_id="txn-9",
            attribution_status="attributed",
            evaluated_at=NOW - timedelta(days=10),
        )
        ev = classify(r, as_of=NOW - timedelta(days=9))
        assert ev["label"] == "CENSORED"
        assert ev["outcome_state"] is None
        assert ev["transaction_id"] is None
        assert ev["purchased_price_minor"] is None
        assert ev["purchased_currency"] is None

    def test_future_outcome_state_does_not_affect_earlier_cutoff(self):
        r = row(outcome_state="DECLINED", outcome_at=NOW)
        ev = classify(r, as_of=NOW - timedelta(days=1))
        assert ev["label"] == "CENSORED"
        assert ev["label"] != "COMMERCIAL_NEGATIVE"

    def test_current_definition_state_never_consulted(self):
        import pathlib

        src = pathlib.Path(__file__).parent.parent.joinpath(
            "commerce", "opportunity_evidence.py"
        ).read_text(encoding="utf-8")
        assert "offer_definitions" not in src
        assert "get_offer_definition" not in src

    def test_current_dropfans_state_never_consulted(self):
        import pathlib

        src = pathlib.Path(__file__).parent.parent.joinpath(
            "commerce", "opportunity_evidence.py"
        ).read_text(encoding="utf-8")
        # Scan imports/calls only: the contract docstring legitimately names
        # Dropfans in the "never exposure evidence" prohibition.
        code_lines = [
            ln for ln in src.splitlines()
            if ln.strip().startswith(("import ", "from ")) or "(" in ln
        ]
        joined = "\n".join(code_lines)
        for token in ("from integrations", "get_drop", "check_drop", "dropfans.service"):
            assert token not in joined


# ---------------------------------------------------------------------------
# Recovery (req 36-39) + send-writer exposure persistence
# ---------------------------------------------------------------------------


class TestRecoveryAndSendExposure:
    def test_recovered_purchase_remains_partial(self):
        snap = json.dumps({"recovery": {"recovered": True}})
        ev = classify(
            row(
                generation_id="recovered:1:42",
                decision_snapshot=snap,
                attribution_confidence="partial",
                outcome_state="PURCHASED",
                outcome_at=NOW - timedelta(days=8),
                transaction_id="txn-1",
                attribution_status="attributed",
                exposure_state="SENT",
                exposure_at=NOW - timedelta(days=8),
                exposure_source="purchase_implied",
            )
        )
        assert ev["evidence_quality"] == "PARTIAL"
        assert ev["exposure_state"] == "SENT"
        assert ev["exposure_source"] == "purchase_implied"

    def test_recovered_row_never_masquerades_as_full(self):
        snap = json.dumps({"recovery": {"recovered": True}})
        ev = classify(row(generation_id="recovered:1:42", decision_snapshot=snap))
        assert ev["evidence_quality"] != "FULL"

    @pytest.mark.asyncio
    async def test_orphan_recovery_still_idempotent(self, monkeypatch):
        from commerce.opportunity_recovery import recover_orphan_offer

        offer = {
            "id": 42, "creator_id": 1, "user_id": 10, "state": "pending",
            "reason": json.dumps({
                "v": 1, "definition_id": 11, "definition_version": 2,
                "stable_key": "alpha",
            }),
            "created_at": NOW - timedelta(days=10),
            "price_minor": 1999, "currency": "USD",
            "dropfans_product_id": "drop_x", "vault_item_ids": ["V1"],
            "transaction_id": None, "purchased_at": None,
        }
        definition = {"id": 11, "creator_id": 1, "stable_key": "alpha", "version": 2}
        conn = FakeConn(
            fetchrows=[dict(offer), {"opportunity_id": 100, "generation_id": "recovered:1:42"}],
            fetches=[[]],
        )
        monkeypatch.setattr(
            "commerce.opportunity_recovery.get_pool", AsyncMock(return_value=make_pool(conn))
        )

        async def _get_definition(creator_id, definition_id, version=None):
            return dict(definition)

        monkeypatch.setattr("db.offer_definitions.get_offer_definition", _get_definition)
        out = await recover_orphan_offer(creator_id=1, offer_id=42, dry_run=False)
        assert out["classification"] == "RECOVERED"
        insert_sql = [c[1] for c in conn.calls if c[0] == "fetchrow" and "INSERT INTO" in c[1]][0]
        assert "exposure_state" in insert_sql

    def test_single_winner_protection_intact(self):
        import pathlib

        src = pathlib.Path(__file__).parent.parent.joinpath(
            "commerce", "opportunity_ledger.py"
        ).read_text(encoding="utf-8")
        purchase_fn = src.split("async def record_purchase_by_offer")[1].split("async def ")[0]
        assert "ORDER BY opportunity_id ASC" in purchase_fn
        assert "LIMIT 1" in purchase_fn

    @pytest.mark.asyncio
    async def test_send_writer_persists_exposure(self, monkeypatch):
        from types import SimpleNamespace

        from commerce.opportunity_ledger import record_opportunity_send

        base = {"opportunity_id": 5, "outcome_state": "PENDING"}
        conn = FakeConn(fetchrows=[dict(base), {"opportunity_id": 5, "outcome_state": "SENT"}])
        monkeypatch.setattr(
            "commerce.opportunity_ledger.get_pool", AsyncMock(return_value=make_pool(conn))
        )
        out = await record_opportunity_send(5, sealed_execution=SimpleNamespace(status="EXECUTED"))
        assert out["outcome_state"] == "SENT"
        update_sql = conn.calls[1][1]
        assert "exposure_state" in update_sql and "exposure_at" in update_sql
        assert conn.calls[1][2][2] == "SENT"

    @pytest.mark.asyncio
    async def test_send_failure_persists_attempt(self, monkeypatch):
        from commerce.opportunity_ledger import record_opportunity_send

        base = {"opportunity_id": 5, "outcome_state": "PENDING"}
        conn = FakeConn(fetchrows=[dict(base), {"opportunity_id": 5, "outcome_state": "SEND_FAILED"}])
        monkeypatch.setattr(
            "commerce.opportunity_ledger.get_pool", AsyncMock(return_value=make_pool(conn))
        )
        out = await record_opportunity_send(5, sealed_execution=None)
        assert out["outcome_state"] == "SEND_FAILED"
        assert conn.calls[1][2][2] == "SEND_ATTEMPTED"

    @pytest.mark.asyncio
    async def test_read_api_fail_closed(self, monkeypatch):
        conn = FakeConn()
        patch_evidence_pool(monkeypatch, conn)

        async def _boom(sql, *params):
            raise RuntimeError("db down")

        monkeypatch.setattr(conn, "fetchrow", _boom)
        from commerce.opportunity_evidence import get_evidence

        out = await get_evidence(1, 5, as_of=NOW)
        assert out["label"] == "UNAVAILABLE"
        assert out["evidence_quality"] == "UNAVAILABLE"

    def test_migration_shape(self):
        src = Path(__file__).parent.parent.joinpath(
            "db", "migrations", "20260919000000_p35_3b_evidence_exposure.sql"
        ).read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS exposure_state" in src
        assert "ADD COLUMN IF NOT EXISTS exposure_at" in src
        assert "ADD COLUMN IF NOT EXISTS exposure_source" in src
        assert "idx_opportunity_decisions_creator_exposure" in src
        assert "CREATE TABLE" not in src and "DROP" not in src
