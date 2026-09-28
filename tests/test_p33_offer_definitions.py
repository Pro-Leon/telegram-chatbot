"""P3.3.5 — OfferDefinition schema foundation tests (mocked DB, no live data).

Covers ``db.offer_definitions`` creator isolation, version immutability,
canonical Vault composition, lifecycle, drop-mapping integrity, plus
migration structural checks and the no-seeding / no-selector /
no-ContentFamily-inference / no-taxonomy-inference contracts.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit]

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "db"
    / "migrations"
    / "20260917010000_p33_offer_definitions.sql"
)

DAO_PATH = Path(__file__).parent.parent / "db" / "offer_definitions.py"


def _pool(fetchrow=None, fetch=None, execute=None):
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow)
    mock_conn.fetch = AsyncMock(return_value=fetch or [])
    if execute is not None:
        mock_conn.execute = AsyncMock(return_value=execute)
    else:
        mock_conn.execute = AsyncMock(return_value="DELETE 0")
    mock_pool = MagicMock()

    class _Acquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            return False

    mock_pool.acquire = MagicMock(return_value=_Acquire())
    return mock_pool, mock_conn


def _pool_sequence(fetchrows):
    """Mocked pool whose fetchrow returns each side_effect in order."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(side_effect=list(fetchrows))
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_pool = MagicMock()

    class _Acquire:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *a):
            return False

    mock_pool.acquire = MagicMock(return_value=_Acquire())
    return mock_pool, mock_conn


def _def_row(**over):
    row = {
        "id": 11, "creator_id": 1, "stable_key": "black-lingerie",
        "version": 1, "offer_type": "SINGLE",
        "canonical_vault_item_ids": ["V1"],
        "family_id": None, "price_minor": 1999, "currency": "USD",
        "allow_download": True, "status": "draft", "config": None,
        "created_at": None, "updated_at": None,
    }
    row.update(over)
    return row


def _create_kwargs(**over):
    kw = {
        "creator_id": 1, "stable_key": "black-lingerie",
        "offer_type": "SINGLE", "vault_item_ids": ["V1"],
        "price_minor": 1999, "currency": "USD", "allow_download": True,
    }
    kw.update(over)
    return kw


class TestCreate:
    """Spec Tests 1–2: explicit SINGLE and bundle definitions."""

    @pytest.mark.asyncio
    async def test_create_single(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool(fetchrow=_def_row())
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        row = await odb.create_offer_definition(**_create_kwargs())
        assert row["offer_type"] == "SINGLE"
        assert row["canonical_vault_item_ids"] == ["V1"]
        sql = conn.fetchrow.call_args[0][0]
        assert "INSERT INTO commerce_offer_definitions" in sql

    @pytest.mark.asyncio
    async def test_create_bundle(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool(
            fetchrow=_def_row(offer_type="CORE_BUNDLE",
                              canonical_vault_item_ids=["V1", "V2", "V3"])
        )
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        row = await odb.create_offer_definition(**_create_kwargs(
            offer_type="CORE_BUNDLE", vault_item_ids=["V1", "V2", "V3"]))
        assert row["offer_type"] == "CORE_BUNDLE"
        assert row["canonical_vault_item_ids"] == ["V1", "V2", "V3"]


class TestCanonicalization:
    """Spec Tests 3–6: deterministic Vault identity, never truncated."""

    @pytest.mark.asyncio
    async def test_canonical_ordering(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool(fetchrow=_def_row(
            canonical_vault_item_ids=["V1", "V2", "V3"]))
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        await odb.create_offer_definition(**_create_kwargs(
            vault_item_ids=["V3", "V1", "V2"]))
        args = conn.fetchrow.call_args[0][1:]
        assert args[4] == ["V1", "V2", "V3"]  # canonical_ids position

    @pytest.mark.asyncio
    async def test_duplicate_collapse(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool(fetchrow=_def_row(
            canonical_vault_item_ids=["V1", "V2"]))
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        await odb.create_offer_definition(**_create_kwargs(
            vault_item_ids=["V1", "V2", "V2"]))
        args = conn.fetchrow.call_args[0][1:]
        assert args[4] == ["V1", "V2"]

    @pytest.mark.asyncio
    async def test_empty_composition_rejected(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool(fetchrow=_def_row())
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        with pytest.raises(ValueError):
            await odb.create_offer_definition(**_create_kwargs(vault_item_ids=[]))
        conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_over_limit_rejected_never_truncated(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool(fetchrow=_def_row())
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        with pytest.raises(ValueError):
            await odb.create_offer_definition(**_create_kwargs(
                vault_item_ids=[f"V{i}" for i in range(11)]))
        conn.fetchrow.assert_not_called()

    def test_migration_enforces_cardinality(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "cardinality(canonical_vault_item_ids) BETWEEN 1 AND 10" in sql


class TestIdentityAndLifecycle:
    """Spec Tests 7–11: per-creator identity, single active, retirement."""

    @pytest.mark.asyncio
    async def test_duplicate_key_version_rejected_within_creator(self, monkeypatch):
        import asyncpg

        from db import offer_definitions as odb

        pool, _ = _pool_sequence(
            [asyncpg.exceptions.UniqueViolationError("duplicate key value")]
        )
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await odb.create_offer_definition(**_create_kwargs())

    @pytest.mark.asyncio
    async def test_same_key_version_allowed_across_creators(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool(fetchrow=_def_row(creator_id=2))
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        row = await odb.create_offer_definition(**_create_kwargs(creator_id=2))
        assert row["creator_id"] == 2
        args = conn.fetchrow.call_args[0][1:]
        assert args[0] == 2  # creator first

    def test_migration_identity_is_per_creator(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "UNIQUE (creator_id, stable_key, version)" in sql
        assert "UNIQUE (stable_key" not in sql.replace(
            "UNIQUE (creator_id, stable_key, version)", "")

    @pytest.mark.asyncio
    async def test_second_active_version_rejected(self, monkeypatch):
        """X v1 active + X v2 active attempt surfaces a DB uniqueness error."""
        import asyncpg

        from db import offer_definitions as odb

        pool, _ = _pool_sequence(
            [asyncpg.exceptions.UniqueViolationError("duplicate key value")]
        )
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await odb.create_offer_definition(**_create_kwargs(
                version=2, status="active"))

    def test_migration_single_active_per_key(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "WHERE status = 'active'" in sql
        assert "idx_offer_definitions_creator_key_active" in sql

    def test_migration_drafts_may_coexist(self):
        """Only the active partial index is restricted — no draft uniqueness."""
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert sql.count("WHERE status = 'draft'") == 0
        assert "UNIQUE (creator_id, stable_key)" not in sql.replace(
            "UNIQUE (creator_id, stable_key, version)", "")

    @pytest.mark.asyncio
    async def test_retired_version_remains_addressable(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool(fetchrow=_def_row(status="retired"))
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        retired = await odb.retire_offer_definition(1, 11)
        assert retired["status"] == "retired"
        assert retired["id"] == 11
        sql = conn.fetchrow.call_args[0][0]
        assert "SET status = 'retired'" in sql
        assert "creator_id = $1" in sql


class TestCommercialAttributes:
    """Spec Tests 12–16: price, currency, download flag, nullable family."""

    @pytest.mark.asyncio
    async def test_price_stored_exactly(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool(fetchrow=_def_row(price_minor=1999))
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        row = await odb.create_offer_definition(**_create_kwargs(price_minor=1999))
        assert row["price_minor"] == 1999
        assert isinstance(row["price_minor"], int)
        args = conn.fetchrow.call_args[0][1:]
        assert args[6] == 1999

    @pytest.mark.asyncio
    async def test_currency_stored_exactly(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool(fetchrow=_def_row(currency="USD"))
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        row = await odb.create_offer_definition(**_create_kwargs(currency="USD"))
        assert row["currency"] == "USD"

    @pytest.mark.asyncio
    async def test_allow_download_round_trip(self, monkeypatch):
        from db import offer_definitions as odb

        for flag in (True, False):
            pool, conn = _pool(fetchrow=_def_row(allow_download=flag))
            monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
            row = await odb.create_offer_definition(**_create_kwargs(
                allow_download=flag))
            assert row["allow_download"] is flag
            args = conn.fetchrow.call_args[0][1:]
            assert args[8] is flag

    @pytest.mark.asyncio
    async def test_family_id_nullable(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool(fetchrow=_def_row(family_id=None))
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        row = await odb.create_offer_definition(**_create_kwargs())
        assert row["family_id"] is None
        args = conn.fetchrow.call_args[0][1:]
        assert args[5] is None

    @pytest.mark.asyncio
    async def test_family_id_creator_isolation(self, monkeypatch):
        """Creator 2 cannot attach creator 1's family: deterministic rejection."""
        from db import offer_definitions as odb

        pool, conn = _pool_sequence([None])  # family ownership check misses
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        with pytest.raises(ValueError, match="not found for creator"):
            await odb.create_offer_definition(**_create_kwargs(
                creator_id=2, family_id=7))


class TestDropMapping:
    """Spec Tests 17–20: versioned mapping, creator-safe, single mapping."""

    @pytest.mark.asyncio
    async def test_map_definition_drop(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool_sequence([
            {"id": 11},  # definition ownership+version check
            {"definition_id": 11, "definition_version": 1, "creator_id": 1,
             "dropfans_product_id": "drop_abc", "created_at": None},
        ])
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        row = await odb.map_offer_definition_drop(1, 11, 1, "drop_abc")
        assert row["dropfans_product_id"] == "drop_abc"
        assert row["definition_version"] == 1
        insert_sql = conn.fetchrow.call_args_list[1][0][0]
        assert "INSERT INTO commerce_offer_definition_drops" in insert_sql

    @pytest.mark.asyncio
    async def test_mapping_creator_isolation(self, monkeypatch):
        """Creator A cannot map creator B's definition (id+version+creator)."""
        from db import offer_definitions as odb

        pool, _ = _pool_sequence([None])  # no definition for creator 1
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        with pytest.raises(ValueError, match="not found for creator"):
            await odb.map_offer_definition_drop(1, 99, 1, "drop_xyz")

    @pytest.mark.asyncio
    async def test_same_drop_cannot_map_twice(self, monkeypatch):
        import asyncpg

        from db import offer_definitions as odb

        pool, _ = _pool_sequence([
            {"id": 11},
            asyncpg.exceptions.UniqueViolationError("duplicate key value"),
        ])
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await odb.map_offer_definition_drop(1, 11, 1, "drop_abc")

    @pytest.mark.asyncio
    async def test_mapping_pins_explicit_version(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool_sequence([
            {"id": 11},
            {"definition_id": 11, "definition_version": 1, "creator_id": 1,
             "dropfans_product_id": "drop_abc", "created_at": None},
        ])
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        row = await odb.map_offer_definition_drop(1, 11, 1, "drop_abc")
        assert row["definition_version"] == 1
        check_sql = conn.fetchrow.call_args_list[0][0][0]
        assert "version = $3" in check_sql  # id+creator+version pinned
        insert_args = conn.fetchrow.call_args_list[1][0][1:]
        assert insert_args[1] == 1  # definition_version persisted

    def test_migration_drop_pk_and_creator_safety(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "PRIMARY KEY (creator_id, dropfans_product_id)" in sql
        assert "FOREIGN KEY (definition_id, creator_id)" in sql
        assert "REFERENCES commerce_offer_definitions (id, creator_id)" in sql


class TestFoundationContracts:
    """Spec Tests 21–24 + structural contracts."""

    def _ddl(self) -> str:
        return "\n".join(
            line for line in MIGRATION_PATH.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("--")
        )

    def _code_lines(self, path: Path) -> str:
        """Non-comment source lines (docstrings/comments excluded from
        mechanics checks so prose like 'no automatic seeding' cannot
        false-positive)."""
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("--", '"""', "'''", "#")):
                continue
            lines.append(line)
        return "\n".join(lines)

    def test_no_automatic_seeding_in_dao(self):
        code = self._code_lines(DAO_PATH)
        for token in ("fangate_products", "FROM commerce_offers",
                      "commerce_offers", "SELECT vaultItemIds",
                      "backfill", "INSERT INTO commerce_offer_definitions\nSELECT"):
            assert token not in code
        # 'seed' may appear in prose; forbid only seeding mechanics
        assert "auto_seed" not in code.lower()
        assert "seed_from" not in code.lower()

    def test_no_automatic_seeding_in_migration(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        ddl = "\n".join(
            line for line in sql.splitlines()
            if not line.strip().startswith("--")
        )
        for stmt in ("INSERT INTO", "UPDATE ", "DELETE FROM", "backfill", "BACKFILL"):
            assert stmt not in ddl

    def test_no_selector_integration(self):
        import inspect

        import commerce.content_matching
        import commerce.product_selection
        import commerce.vault_ranking

        for mod in (commerce.product_selection,
                    commerce.content_matching,
                    commerce.vault_ranking):
            src = inspect.getsource(mod)
            assert "offer_definition" not in src.lower()
            assert "commerce_offer_definition" not in src

    def test_selector_behavior_unchanged(self):
        """Taxonomy-quarantined ranking still decides by relevance→price→id."""
        from commerce.content_matching import rank_products_by_relevance

        products = [
            {"id": 1, "title": "Red Lace - Bedroom - 3 Photo Set",
             "price_minor": 100, "is_accessible": True,
             "sales_url": "https://example.com/1"},
            {"id": 2, "title": "Red Lace - Bedroom - 6 Photo Bundle",
             "price_minor": 200, "is_accessible": True,
             "sales_url": "https://example.com/2"},
        ]
        ranked = rank_products_by_relevance(
            products, "red", ("red",), ["red lace"], creator_id=1)
        assert [p["id"] for p, _ in ranked] == [1, 2]

    def test_no_contentfamily_inference_in_dao(self):
        src = DAO_PATH.read_text(encoding="utf-8")
        assert "get_families_for_vault_item" not in src
        assert "list_family_members" not in src
        assert "family members" not in src.lower() or "never" in src.lower()
        # family_id accepted only as an explicit caller-supplied reference
        assert "automatically" not in src.lower()

    def test_no_taxonomy_inference_in_dao(self):
        code = self._code_lines(DAO_PATH)
        for token in ("parse_taxonomy", "bundle_group", "bundle_related",
                      "vault_taxonomy", "VaultTaxonomy"):
            assert token not in code

    def test_dao_immutability_no_broad_update(self):
        src = DAO_PATH.read_text(encoding="utf-8")
        updates = [line for line in src.splitlines()
                   if "UPDATE commerce_offer_definitions" in line]
        assert updates, "lifecycle transitions must exist"
        for line in src.splitlines():
            if "UPDATE commerce_offer_definitions" in line:
                continue
            assert "SET price_minor" not in line
            assert "SET canonical_vault_item_ids" not in line
            assert "SET offer_type" not in line

    @pytest.mark.asyncio
    async def test_dao_creator_isolation_reads(self, monkeypatch):
        from db import offer_definitions as odb

        pool, conn = _pool(fetchrow=None, fetch=[_def_row()])
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool))
        assert await odb.get_offer_definition(1, 11) is None
        sql = conn.fetchrow.call_args[0][0]
        assert "creator_id = $1" in sql
        assert conn.fetchrow.call_args[0][1] == 1

        rows = await odb.list_offer_definitions(1)
        assert len(rows) == 1
        list_sql = conn.fetch.call_args[0][0]
        assert "WHERE creator_id = $1" in list_sql

        pool_d, conn_d = _pool(fetchrow=None, fetch=[])
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=pool_d))
        drops = await odb.list_offer_definition_drops(1)
        assert drops == []
        drop_sql = conn_d.fetch.call_args[0][0]
        assert "commerce_offer_definition_drops" in drop_sql
        assert "creator_id = $1" in drop_sql

    @pytest.mark.asyncio
    async def test_db_failure_propagates_never_false_empty(self, monkeypatch):
        from db import offer_definitions as odb

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(side_effect=RuntimeError("DB down"))
        mock_conn.fetchrow = AsyncMock(side_effect=RuntimeError("DB down"))
        mock_pool = MagicMock()

        class _Acquire:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        mock_pool.acquire = MagicMock(return_value=_Acquire())
        monkeypatch.setattr(odb, "get_pool", AsyncMock(return_value=mock_pool))
        with pytest.raises(RuntimeError):
            await odb.list_offer_definitions(1)
        with pytest.raises(RuntimeError):
            await odb.get_offer_definition(1, 11)

    def test_offer_types_constrained(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "'SINGLE', 'SMALL_BUNDLE', 'CORE_BUNDLE', 'PREMIUM'" in sql
        for extra in ("FREE", "TEASE", "DISCOUNT", "FLASH", "SUBSCRIPTION"):
            assert f"'{extra}'" not in sql

    def test_statuses_constrained(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "'draft', 'active', 'retired'" in sql

    def test_migration_conventions(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert sql.count("CREATE TABLE IF NOT EXISTS") == 2
        assert "CREATE UNIQUE INDEX IF NOT EXISTS" in sql
        assert "CREATE INDEX IF NOT EXISTS" in sql
        assert "price_minor INTEGER NOT NULL CHECK (price_minor >= 0)" in sql
        assert "allow_download BOOLEAN NOT NULL" in sql
        ddl = self._ddl()
        assert "opportunity_id" not in ddl.lower().replace(
            "-- no commerce_offers.opportunity_id here", "")

    def test_migration_additive_only(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        ddl = "\n".join(
            line for line in sql.splitlines()
            if not line.strip().startswith("--")
        ).upper()
        assert "ALTER TABLE" not in ddl
        assert "DROP TABLE" not in ddl
        assert "DROP COLUMN" not in ddl
        assert "TRUNCATE" not in ddl
        assert "FANGATE_PRODUCTS" not in ddl
        assert "COMMERCE_OFFERS" not in ddl.replace(
            "COMMERCE_OFFER_DEFINITIONS", "").replace(
            "COMMERCE_OFFER_DEFINITION_DROPS", "")

    def test_family_reference_is_creator_safe(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "FOREIGN KEY (family_id, creator_id)" in sql
        assert "REFERENCES commerce_content_families (id, creator_id)" in sql

    def test_models_represent_definition(self):
        from commerce.models import (
            OFFER_DEFINITION_STATUSES,
            OFFER_DEFINITION_TYPES,
            OfferDefinition,
            OfferDefinitionDropMapping,
        )

        assert OFFER_DEFINITION_TYPES == frozenset(
            {"SINGLE", "SMALL_BUNDLE", "CORE_BUNDLE", "PREMIUM"})
        assert OFFER_DEFINITION_STATUSES == frozenset(
            {"draft", "active", "retired"})
        row = _def_row()
        model = OfferDefinition.from_row(row)
        assert model.creator_id == 1
        assert model.stable_key == "black-lingerie"
        assert model.version == 1
        assert model.canonical_vault_item_ids == ["V1"]
        assert model.price_minor == 1999
        assert model.currency == "USD"
        assert model.allow_download is True
        mapping = OfferDefinitionDropMapping.from_row(
            {"definition_id": 11, "definition_version": 1, "creator_id": 1,
             "dropfans_product_id": "drop_abc", "created_at": None})
        assert mapping.definition_version == 1

    def test_migration_does_not_touch_families_migration(self):
        families_path = (
            Path(__file__).parent.parent / "db" / "migrations"
            / "20260917000000_p33_content_families.sql"
        )
        assert families_path.exists()
        assert "commerce_offer_definition" not in families_path.read_text(
            encoding="utf-8")
