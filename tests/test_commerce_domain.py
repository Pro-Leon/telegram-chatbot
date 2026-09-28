"""Phase 5.1A - Commerce domain (PPV offers + fan attribution) tests.

Covers:
- Migration content and safety (new commerce migration + frozen Phase 5.0)
- Domain models (proposal security boundary, offer mapping, states)
- DAO creator-scoping, transitions, and concurrency guards
"""

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

MIGRATIONS_DIR = Path(__file__).parent.parent / "db" / "migrations"
COMMERCE_MIGRATION = MIGRATIONS_DIR / "20260819010000_fangate_ppv_commerce.sql"
FROZEN_FANGATE_MIGRATION = MIGRATIONS_DIR / "20260819000000_fangate_commerce.sql"
FROZEN_FANGATE_SHA256 = "D038C458F4941EB9AE7210A30E0996BC7D79EAB2915E1A3F90C80FDFAAA46817"

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


def _commerce_sql() -> str:
    return COMMERCE_MIGRATION.read_text(encoding="utf-8")


class TestCommerceMigrationDiscovery:
    """Verify the new migration participates in the engine correctly."""

    def test_migration_file_exists(self):
        assert COMMERCE_MIGRATION.exists()
        assert COMMERCE_MIGRATION.suffix == ".sql"

    def test_migration_version_sorts_after_fangate(self):
        from db.migrate import discover_migrations

        migrations = discover_migrations()
        versions = [m["version"] for m in migrations]
        assert "20260819000000" in versions
        assert "20260819010000" in versions
        assert versions.index("20260819000000") < versions.index("20260819010000")


class TestCommerceMigrationContent:
    """Verify schema, constraints, indexes, and idempotency of the migration."""

    def test_has_commerce_offers_table(self):
        sql = _commerce_sql()
        assert "CREATE TABLE IF NOT EXISTS commerce_offers" in sql
        assert "BIGSERIAL PRIMARY KEY" in sql

    def test_has_fan_attribution_column(self):
        sql = _commerce_sql()
        assert "ALTER TABLE fangate_transactions" in sql
        assert "ADD COLUMN IF NOT EXISTS user_id" in sql
        assert "REFERENCES users(id)" in sql

    def test_has_foreign_keys(self):
        sql = _commerce_sql()
        assert "REFERENCES creators(id) ON DELETE CASCADE" in sql
        assert "REFERENCES users(id) ON DELETE CASCADE" in sql

    def test_has_state_check_constraint(self):
        sql = _commerce_sql()
        expected = (
            "CHECK (state IN ('pending', 'clicked', 'purchased', 'declined', 'expired', 'revoked'))"
        )
        assert expected in sql

    def test_has_price_constraint(self):
        sql = _commerce_sql()
        assert "CHECK (price_minor IS NULL OR price_minor >= 0)" in sql

    def test_has_purchased_invariants(self):
        sql = _commerce_sql()
        assert "CHECK (purchased_at IS NULL OR state = 'purchased')" in sql
        assert "CHECK (transaction_id IS NULL OR state = 'purchased')" in sql

    def test_has_indexes(self):
        sql = _commerce_sql()
        for idx in (
            "idx_commerce_offers_user_state",
            "idx_commerce_offers_creator_product_state",
            "idx_commerce_offers_creator_user_created",
            "idx_commerce_offers_transaction_id",
            "idx_fangate_transactions_user",
        ):
            assert idx in sql, f"Missing index: {idx}"

    def test_transaction_id_index_is_partial_and_unique(self):
        sql = _commerce_sql()
        assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_commerce_offers_transaction_id" in sql
        assert "WHERE transaction_id IS NOT NULL" in sql

    def test_uses_if_not_exists_everywhere(self):
        sql = _commerce_sql()
        lines = sql.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "CREATE TABLE" in stripped or "CREATE INDEX" in stripped:
                assert "IF NOT EXISTS" in stripped, f"Not idempotent: {stripped}"
            if "ALTER TABLE" in stripped:
                following = " ".join(lines[i + 1 : i + 2])
                assert "IF NOT EXISTS" in following, f"Not idempotent: {stripped}"

    def test_no_destructive_statements(self):
        sql = _commerce_sql()
        assert "DROP TABLE" not in sql.upper()
        assert "DROP COLUMN" not in sql.upper()
        assert "TRUNCATE" not in sql.upper()

    def test_unknown_creator_or_user_is_rejected_at_db_level(self):
        """FK rejection is enforced by the migration's REFERENCES + CASCADE."""
        sql = _commerce_sql()
        assert "user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE" in sql
        assert "creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE" in sql

    def test_frozen_phase5_migration_unmodified(self):
        """The Phase 5.0 migration is frozen - any change must fail loudly.

        Re-baselined 2026-09-28: the original file was lost with the other
        pre-rebuild migrations; recreated from live staging DDL
        (table-for-table consistent with 001_commerce_core.sql) and
        re-frozen here. Any future change must fail loudly.
        """
        content = FROZEN_FANGATE_MIGRATION.read_bytes()
        digest = hashlib.sha256(content).hexdigest().upper()
        assert digest == FROZEN_FANGATE_SHA256


class TestCommerceModels:
    def test_offer_states_match_database_constraint(self):
        from commerce import OFFER_STATES
        from commerce.models import OfferState

        assert {state.value for state in OfferState} == OFFER_STATES
        assert OFFER_STATES == {"pending", "clicked", "purchased", "declined", "expired", "revoked"}

    def test_proposal_from_dict_valid(self):
        from commerce.models import CommerceAction, CommerceProposal

        proposal = CommerceProposal.from_dict(
            {"action": "offer_ppv", "ppv_id": 42, "reason": "engaged fan"}
        )
        assert proposal.action is CommerceAction.OFFER_PPV
        assert proposal.ppv_id == 42
        assert proposal.reason == "engaged fan"

    def test_proposal_from_dict_dont_offer(self):
        from commerce.models import CommerceAction, CommerceProposal

        proposal = CommerceProposal.from_dict({"action": "dont_offer"})
        assert proposal.action is CommerceAction.DONT_OFFER
        assert proposal.ppv_id is None

    def test_proposal_from_dict_rejects_unknown_action(self):
        from commerce.models import CommerceProposal

        with pytest.raises(ValueError):
            CommerceProposal.from_dict({"action": "give_everything_away"})

    def test_proposal_carries_no_credentials_or_urls(self):
        """Structural security boundary: the proposal model is a pure
        action + reference. No price, URL, customer-id, or secret fields."""
        import dataclasses

        from commerce.models import CommerceProposal

        fields = {f.name for f in dataclasses.fields(CommerceProposal)}
        assert fields == {"action", "ppv_id", "reason"}
        assert not any(name in fields for name in ("price", "url", "link", "secret", "api_key"))

    def test_ppv_offer_from_row(self):
        from commerce.models import PpvOffer

        row = {
            "id": 1,
            "creator_id": 7,
            "user_id": 9001,
            "product_id": 11,
            "link": "https://fangate.test/p/11",
            "price_minor": 1500,
            "currency": "USD",
            "state": "pending",
            "reason": "engaged",
            "created_by": "operator",
            "created_at": "2026-08-19T10:00:00Z",
            "expires_at": None,
            "clicked_at": None,
            "purchased_at": None,
            "transaction_id": None,
        }
        offer = PpvOffer.from_row(row)
        assert offer.creator_id == 7
        assert offer.user_id == 9001
        assert offer.product_id == 11
        assert offer.link == "https://fangate.test/p/11"
        assert offer.state == "pending"

    def test_policy_decision_denial(self):
        from commerce.models import PolicyDecision

        denied = PolicyDecision(allowed=False, denial_reason="hmac_mismatch")
        assert denied.allowed is False
        assert denied.denial_reason == "hmac_mismatch"


class TestCommerceDao:
    """Verify DAO SQL shape, creator-scoping, and concurrency guards."""

    @pytest.mark.asyncio
    async def test_create_offer_inserts_and_returns_row(self):
        mock_conn = AsyncMock()
        row = {
            "id": 1,
            "creator_id": 7,
            "user_id": 9,
            "product_id": 11,
            "link": "https://fangate.test/p/11",
            "state": "pending",
        }
        mock_conn.fetchrow = AsyncMock(return_value=row)
        _patch_pool(mock_conn)

        from commerce.dao import create_offer

        result = await create_offer(
            creator_id=7,
            user_id=9,
            product_id=11,
            link="https://fangate.test/p/11",
            price_minor=1500,
            currency="USD",
            reason=None,
            created_by="operator",
            expires_at=None,
            dropfans_product_id="df_test_11",
            vault_item_ids=["v1", "v2"],
        )
        assert result["creator_id"] == 7
        sql, *params = mock_conn.fetchrow.call_args[0]
        assert "INSERT INTO commerce_offers" in sql
        assert "RETURNING *" in sql
        # P3.2C F1: offer-time snapshot columns are written atomically with
        # the row (media_count derived, content hash derived).
        assert params[:10] == [
            7,
            9,
            11,
            "https://fangate.test/p/11",
            1500,
            "USD",
            None,
            "operator",
            None,
            "df_test_11",
        ]
        assert params[10] == ["v1", "v2"]
        assert params[11] == 2
        assert isinstance(params[12], str) and len(params[12]) == 64

    @pytest.mark.asyncio
    async def test_create_offer_without_snapshot_fails_closed(self):
        """P3.2C F1: missing snapshot → ValueError with zero DB writes."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"id": 1})
        _patch_pool(mock_conn)

        from commerce.dao import create_offer

        with pytest.raises(ValueError, match="commerce_snapshot_required"):
            await create_offer(
                creator_id=7,
                user_id=9,
                product_id=11,
                link="https://fangate.test/p/11",
                created_by="operator",
            )
        mock_conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_offer_is_creator_scoped(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        _patch_pool(mock_conn)

        from commerce.dao import get_offer

        result = await get_offer(creator_id=7, offer_id=3)
        assert result is None
        sql = mock_conn.fetchrow.call_args[0][0]
        assert "creator_id = $1" in sql
        assert "id = $2" in sql

    @pytest.mark.asyncio
    async def test_list_offers_for_user_filters_by_creator_and_state(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        _patch_pool(mock_conn)

        from commerce.dao import list_offers_for_user

        result = await list_offers_for_user(user_id=9, creator_id=7, state="pending")
        assert result == []
        sql, *params = mock_conn.fetch.call_args[0]
        assert "user_id = $1" in sql
        assert "creator_id = $2" in sql
        assert "state = $3" in sql
        assert params == [9, 7, "pending", 100, 0]

    @pytest.mark.asyncio
    async def test_list_offers_for_user_defaults_no_extra_filters(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        _patch_pool(mock_conn)

        from commerce.dao import list_offers_for_user

        await list_offers_for_user(user_id=9)
        sql, *params = mock_conn.fetch.call_args[0]
        assert "creator_id = $2" not in sql
        assert params == [9, 100, 0]

    @pytest.mark.asyncio
    async def test_find_pending_offer_for_product_finds_redeemable(self):
        mock_conn = AsyncMock()
        row = {"id": 2, "creator_id": 7, "user_id": 9, "product_id": 11, "state": "clicked"}
        mock_conn.fetchrow = AsyncMock(return_value=row)
        _patch_pool(mock_conn)

        from commerce.dao import find_pending_offer_for_product

        result = await find_pending_offer_for_product(creator_id=7, user_id=9, product_id=11)
        assert result is not None
        sql, *params = mock_conn.fetchrow.call_args[0]
        assert "creator_id = $1" in sql
        assert "state IN ('pending', 'clicked')" in sql
        assert params == [7, 9, 11]

    @pytest.mark.asyncio
    async def test_mark_offer_clicked_only_from_pending(self):
        mock_conn = AsyncMock()
        row = {"id": 2, "creator_id": 7, "user_id": 9, "state": "clicked"}
        mock_conn.fetchrow = AsyncMock(return_value=row)
        _patch_pool(mock_conn)

        from commerce.dao import mark_offer_clicked

        result = await mark_offer_clicked(creator_id=7, offer_id=2)
        assert result["state"] == "clicked"
        sql, *params = mock_conn.fetchrow.call_args[0]
        assert "state = 'clicked'" in sql
        assert "state = 'pending'" in sql
        assert params == [7, 2]

    @pytest.mark.asyncio
    async def test_mark_offer_purchased_only_from_active_states(self):
        mock_conn = AsyncMock()
        row = {"id": 2, "creator_id": 7, "state": "purchased", "transaction_id": "TX1"}
        mock_conn.fetchrow = AsyncMock(return_value=row)
        _patch_pool(mock_conn)

        from commerce.dao import mark_offer_purchased

        result = await mark_offer_purchased(creator_id=7, offer_id=2, transaction_id="TX1")
        assert result["state"] == "purchased"
        sql, *params = mock_conn.fetchrow.call_args[0]
        assert "state IN ('pending', 'clicked')" in sql
        assert params == [7, 2, "TX1"]

    @pytest.mark.asyncio
    async def test_mark_offer_purchased_is_idempotent_for_same_transaction(self):
        mock_conn = AsyncMock()
        row = {"id": 2, "creator_id": 7, "state": "purchased", "transaction_id": "TX1"}
        mock_conn.fetchrow = AsyncMock(return_value=row)
        _patch_pool(mock_conn)

        from commerce.dao import mark_offer_purchased

        await mark_offer_purchased(creator_id=7, offer_id=2, transaction_id="TX1")
        sql = mock_conn.fetchrow.call_args[0][0]
        assert "transaction_id IS NULL OR transaction_id = $3" in sql

    @pytest.mark.asyncio
    async def test_mark_offer_purchased_after_decline_is_noop(self):
        """A declined/revoked/expired offer must never flip to purchased."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)  # UPDATE affects 0 rows
        _patch_pool(mock_conn)

        from commerce.dao import mark_offer_purchased

        result = await mark_offer_purchased(creator_id=7, offer_id=2, transaction_id="TX9")
        assert result is None

    @pytest.mark.asyncio
    async def test_mark_offer_expired_only_from_active_states(self):
        mock_conn = AsyncMock()
        row = {"id": 2, "creator_id": 7, "state": "expired"}
        mock_conn.fetchrow = AsyncMock(return_value=row)
        _patch_pool(mock_conn)

        from commerce.dao import mark_offer_expired

        result = await mark_offer_expired(creator_id=7, offer_id=2)
        assert result["state"] == "expired"
        sql = mock_conn.fetchrow.call_args[0][0]
        assert "state IN ('pending', 'clicked')" in sql

    @pytest.mark.asyncio
    async def test_attach_transaction_user_sets_only_when_null(self):
        mock_conn = AsyncMock()
        row = {
            "id": 1,
            "creator_id": 7,
            "transaction_id": "TX1",
            "event_type": "payment.successful",
            "user_id": 9,
        }
        mock_conn.fetchrow = AsyncMock(return_value=row)
        _patch_pool(mock_conn)

        from commerce.dao import attach_transaction_user

        result = await attach_transaction_user(creator_id=7, transaction_id="TX1", user_id=9)
        assert result["user_id"] == 9
        sql, *params = mock_conn.fetchrow.call_args[0]
        assert "user_id IS NULL" in sql
        assert params == [7, "TX1", 9]

    @pytest.mark.asyncio
    async def test_attach_transaction_user_concurrent_single_winner(self):
        """Two concurrent deliveries: only the first UPDATE sees user_id IS NULL."""
        mock_conn = AsyncMock()
        row = {"id": 1, "creator_id": 7, "transaction_id": "TX1", "user_id": 9}
        mock_conn.fetchrow = AsyncMock(side_effect=[row, None])
        _patch_pool(mock_conn)

        from commerce.dao import attach_transaction_user

        first = await attach_transaction_user(creator_id=7, transaction_id="TX1", user_id=9)
        second = await attach_transaction_user(creator_id=7, transaction_id="TX1", user_id=9)
        assert first is not None
        assert second is None

    @pytest.mark.asyncio
    async def test_attach_transaction_user_never_overwrites(self):
        """An existing attribution is never silently replaced."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)  # user_id already set
        _patch_pool(mock_conn)

        from commerce.dao import attach_transaction_user

        result = await attach_transaction_user(creator_id=7, transaction_id="TX1", user_id=9)
        assert result is None

    def test_dao_stays_out_of_realtime_and_http_layers(self):
        """Structural boundary: the DAO never touches the WebSocket layer,
        FastAPI, or the Fangate HTTP client. ``core.event_bus`` is allowed
        for the best-effort ``commerce.sale_recorded`` publish only (workers
        communicate with realtime exclusively through the event bus)."""
        import commerce.dao

        src = Path(commerce.dao.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "ws_manager",
            "event_subscriber",
            "httpx",
            "fastapi",
            "infra",
        ):
            assert forbidden not in src, f"commerce.dao must not import {forbidden}"
