"""Phase 5.2 - PPV Intelligence tests.

Covers:
- Migration content and safety for the Phase 5.2 intelligence tables
- Migration ordering after the Phase 5.1A commerce migration
- Analytics DAO (decision audit log, counter rollups, funnel aggregation)
- Purchase attribution coordination
"""

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

MIGRATIONS_DIR = Path(__file__).parent.parent / "db" / "migrations"
INTELLIGENCE_MIGRATION = MIGRATIONS_DIR / "20260819100000_ppv_intelligence.sql"

_ACTIVE_PATCHERS: list = []


def _patch_pool(mock_conn: AsyncMock) -> None:
    """Expose a mock pool/connection the way db tests do, patching get_pool."""
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_ctx)
    patcher = patch("commerce.dao.get_pool", new_callable=AsyncMock, return_value=mock_pool)
    patcher.start()
    _ACTIVE_PATCHERS.append(patcher)


@pytest.fixture(autouse=True)
def _stop_patches():
    yield
    while _ACTIVE_PATCHERS:
        _ACTIVE_PATCHERS.pop().stop()


def _sql() -> str:
    return INTELLIGENCE_MIGRATION.read_text(encoding="utf-8")


class TestIntelligenceMigrationDiscovery:
    def test_migration_file_exists(self):
        assert INTELLIGENCE_MIGRATION.exists()
        assert INTELLIGENCE_MIGRATION.suffix == ".sql"

    def test_migration_version_sorts_after_commerce(self):
        from db.migrate import discover_migrations

        migrations = discover_migrations()
        versions = [m["version"] for m in migrations]
        assert "20260819010000" in versions
        assert "20260819100000" in versions
        assert versions.index("20260819010000") < versions.index("20260819100000")

    def test_migrations_stay_ordered(self):
        from db.migrate import discover_migrations

        versions = [m["version"] for m in discover_migrations()]
        assert versions == sorted(versions)


class TestIntelligenceMigrationContent:
    def test_has_eligibility_decision_table(self):
        sql = _sql()
        assert "CREATE TABLE IF NOT EXISTS ppv_eligibility_decisions" in sql
        assert "BIGSERIAL PRIMARY KEY" in sql

    def test_has_analytics_table(self):
        sql = _sql()
        assert "CREATE TABLE IF NOT EXISTS ppv_analytics_daily" in sql
        assert "PRIMARY KEY (creator_id, product_id, day)" in sql

    def test_has_foreign_keys_with_cascade(self):
        sql = _sql()
        assert "REFERENCES creators(id) ON DELETE CASCADE" in sql
        assert "REFERENCES users(id) ON DELETE CASCADE" in sql

    def test_has_decision_snapshot_jsonb(self):
        sql = _sql()
        assert "inputs JSONB NOT NULL DEFAULT '{}'" in sql

    def test_has_indexes(self):
        sql = _sql()
        for idx in (
            "idx_ppv_eligibility_user_evaluated",
            "idx_ppv_eligibility_creator_product_decision",
            "idx_ppv_eligibility_creator_evaluated",
            "idx_ppv_analytics_day",
        ):
            assert idx in sql, f"Missing index: {idx}"

    def test_uses_if_not_exists_everywhere(self):
        sql = _sql()
        lines = sql.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "CREATE TABLE" in stripped or "CREATE INDEX" in stripped:
                assert "IF NOT EXISTS" in stripped, f"Not idempotent: {stripped}"

    def test_no_destructive_statements(self):
        sql = _sql()
        assert "DROP TABLE" not in sql.upper()
        assert "DROP COLUMN" not in sql.upper()
        assert "TRUNCATE" not in sql.upper()

    def test_analytics_counters_are_non_negative_by_default(self):
        sql = _sql()
        assert "DEFAULT 0" in sql
        assert "INTEGER NOT NULL DEFAULT 0" in sql
        assert "BIGINT NOT NULL DEFAULT 0" in sql


class TestEligibilityDecisionDao:
    @pytest.mark.asyncio
    async def test_record_eligibility_decision_inserts_with_json_inputs(self):
        mock_conn = AsyncMock()
        row = {
            "id": 1,
            "creator_id": 7,
            "product_id": 11,
            "decision": False,
            "denial_reason": "user_blocked",
            "evaluated_at": None,
        }
        mock_conn.fetchrow = AsyncMock(return_value=row)
        _patch_pool(mock_conn)

        from commerce.dao import record_eligibility_decision

        result = await record_eligibility_decision(
            creator_id=7,
            user_id=9,
            product_id=11,
            decision=False,
            denial_reason="user_blocked",
            inputs={"is_blocked": True},
        )
        assert result["decision"] is False
        sql, *params = mock_conn.fetchrow.call_args[0]
        assert "INSERT INTO ppv_eligibility_decisions" in sql
        assert "::jsonb" in sql
        assert params == [7, 9, 11, False, "user_blocked", '{"is_blocked": true}']

    @pytest.mark.asyncio
    async def test_list_eligibility_decisions_scopes_creator(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        _patch_pool(mock_conn)

        from commerce.dao import list_eligibility_decisions

        await list_eligibility_decisions(creator_id=7, user_id=9)
        sql, *params = mock_conn.fetch.call_args[0]
        assert "creator_id = $1" in sql
        assert "user_id = $2" in sql
        assert params == [7, 9, 100, 0]


class TestAnalyticsDao:
    @pytest.mark.asyncio
    async def test_increment_analytics_counter_rejects_unknown_column(self):
        from commerce.dao import increment_analytics_counter

        with pytest.raises(ValueError):
            await increment_analytics_counter(
                creator_id=7,
                product_id=11,
                day="2026-08-19",
                column="total_money",  # not in whitelist
            )

    @pytest.mark.asyncio
    async def test_increment_analytics_counter_builds_upsert(self):
        mock_conn = AsyncMock()
        _patch_pool(mock_conn)

        from commerce.dao import increment_analytics_counter

        await increment_analytics_counter(
            creator_id=7, product_id=11, day="2026-08-19", column="offers_created"
        )
        sql, *params = mock_conn.execute.call_args[0]
        assert "INSERT INTO ppv_analytics_daily" in sql
        assert "ON CONFLICT (creator_id, product_id, day) DO UPDATE SET" in sql
        assert (
            "offers_created = ppv_analytics_daily.offers_created + EXCLUDED.offers_created" in sql
        )
        assert params == [7, 11, "2026-08-19", 1]

    @pytest.mark.asyncio
    async def test_record_offer_transition_maps_state_to_column(self):
        mock_conn = AsyncMock()
        _patch_pool(mock_conn)

        from commerce.dao import record_offer_transition

        column = await record_offer_transition(
            creator_id=7,
            product_id=11,
            state="purchased",
            day="2026-08-19",
            revenue_minor=1500,
        )
        assert column == "offers_purchased"
        sql, *params = mock_conn.execute.call_args_list[0][0]
        assert "offers_purchased = ppv_analytics_daily.offers_purchased" in sql
        assert params == [7, 11, "2026-08-19", 1]
        # A second call bumps revenue
        revenue_sql, *revenue_params = mock_conn.execute.call_args_list[1][0]
        assert "revenue_minor" in revenue_sql
        assert revenue_params == [7, 11, "2026-08-19", 1500]

    @pytest.mark.asyncio
    async def test_record_offer_transition_rejects_unknown_state(self):
        from commerce.dao import record_offer_transition

        with pytest.raises(ValueError):
            await record_offer_transition(
                creator_id=7, product_id=11, state="refunded", day="2026-08-19"
            )

    @pytest.mark.asyncio
    async def test_get_ppv_funnel_aggregates_per_product(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {
                    "product_id": 11,
                    "offers_created": 3,
                    "offers_clicked": 2,
                    "offers_purchased": 1,
                    "offers_declined": 0,
                    "offers_expired": 0,
                    "offers_revoked": 0,
                    "revenue_minor": 1500,
                },
            ]
        )
        _patch_pool(mock_conn)

        from commerce.dao import get_ppv_funnel

        result = await get_ppv_funnel(creator_id=7, start_date="2026-08-01", end_date="2026-08-31")
        assert result[0]["offers_purchased"] == 1
        assert result[0]["revenue_minor"] == 1500
        sql, *params = mock_conn.fetch.call_args[0]
        assert "SUM(offers_created)" in sql
        assert "day >= $2" in sql
        assert "day <= $3" in sql
        assert params == [7, "2026-08-01", "2026-08-31"]


class TestPurchaseAttribution:
    @pytest.mark.asyncio
    async def test_attribute_purchase_happy_path(self):
        offer = {
            "id": 2,
            "creator_id": 7,
            "user_id": 9,
            "product_id": 11,
            "state": "clicked",
            "transaction_id": None,
        }
        updated = {**offer, "state": "purchased", "transaction_id": "TX1"}

        with (
            patch(
                "commerce.attribution.find_pending_offer_for_product",
                new=AsyncMock(return_value=offer),
            ),
            patch("commerce.attribution.mark_offer_purchased", new=AsyncMock(return_value=updated)),
            patch(
                "commerce.attribution.attach_transaction_user",
                new=AsyncMock(return_value={"user_id": 9}),
            ),
            patch(
                "commerce.attribution.record_offer_transition",
                new=AsyncMock(return_value="offers_purchased"),
            ),
        ):
            from commerce.attribution import attribute_purchase

            record = await attribute_purchase(
                creator_id=7,
                user_id=9,
                product_id=11,
                transaction_id="TX1",
                revenue_minor=1500,
                occurred_at=datetime.fromisoformat("2026-08-19T12:00:00+00:00"),
            )
        assert record is not None
        assert record.transaction_id == "TX1"
        assert record.offer_id == 2
        assert record.user_id == 9

    @pytest.mark.asyncio
    async def test_attribute_purchase_no_active_offer(self):
        with (
            patch(
                "commerce.attribution.find_pending_offer_for_product",
                new=AsyncMock(return_value=None),
            ),
        ):
            from commerce.attribution import attribute_purchase

            record = await attribute_purchase(
                creator_id=7,
                user_id=9,
                product_id=11,
                transaction_id="TX1",
            )
        assert record is None

    @pytest.mark.asyncio
    async def test_attribute_purchase_transaction_already_claimed(self):
        offer = {"id": 2, "creator_id": 7, "user_id": 9, "product_id": 11, "state": "pending"}
        with (
            patch(
                "commerce.attribution.find_pending_offer_for_product",
                new=AsyncMock(return_value=offer),
            ),
            patch(
                "commerce.attribution.mark_offer_purchased", new=AsyncMock(return_value=None)
            ),  # conditional UPDATE matched 0 rows
        ):
            from commerce.attribution import attribute_purchase

            record = await attribute_purchase(
                creator_id=7,
                user_id=9,
                product_id=11,
                transaction_id="TX1",
            )
        assert record is None

    def test_attribution_stays_out_of_realtime_and_http_layers(self):
        """Structural boundary: attribution imports only DAO + domain models."""
        import inspect

        import commerce.attribution as mod

        src = inspect.getsource(mod)
        for forbidden in ("event_bus", "ws_manager", "event_subscriber", "fastapi", "httpx"):
            assert forbidden not in src, f"commerce.attribution must not import {forbidden}"
