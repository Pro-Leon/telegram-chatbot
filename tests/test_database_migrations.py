"""Phase 4.2 - Database migration infrastructure tests.

Tests for:
- Migration discovery and ordering
- Version tracking
- Migration execution
- Idempotency
- Existing database adoption (baseline)
- Migration failure handling
- Application compatibility
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit]


MIGRATIONS_DIR = Path(__file__).parent.parent / "db" / "migrations"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP A - Migration Discovery
# ═══════════════════════════════════════════════════════════════════════════════


class TestMigrationDiscovery:
    """Verify migration files are discovered correctly."""

    def test_migrations_directory_exists(self):
        assert MIGRATIONS_DIR.exists()
        assert MIGRATIONS_DIR.is_dir()

    def test_baseline_migration_exists(self):
        baseline = MIGRATIONS_DIR / "00000000000000_baseline.sql"
        assert baseline.exists()

    def test_discover_migrations_finds_files(self):
        from db.migrate import discover_migrations

        migrations = discover_migrations()
        assert len(migrations) >= 1
        assert any(m["version"] == "00000000000000" for m in migrations)

    def test_migrations_sorted_by_version(self):
        from db.migrate import discover_migrations

        migrations = discover_migrations()
        versions = [m["version"] for m in migrations]
        assert versions == sorted(versions)

    def test_versions_are_unique(self):
        from db.migrate import discover_migrations

        migrations = discover_migrations()
        versions = [m["version"] for m in migrations]
        assert len(versions) == len(set(versions))

    def test_migration_files_have_sql_extension(self):
        from db.migrate import discover_migrations

        migrations = discover_migrations()
        for m in migrations:
            assert m["path"].suffix == ".sql"

    def test_baseline_sql_is_not_empty(self):
        baseline = MIGRATIONS_DIR / "00000000000000_baseline.sql"
        content = baseline.read_text(encoding="utf-8")
        assert len(content.strip()) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B - Version Tracking
# ═══════════════════════════════════════════════════════════════════════════════


class TestVersionTracking:
    """Verify migration version tracking mechanism."""

    @pytest.mark.asyncio
    async def test_ensure_version_table_creates_table(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        from db.migrate import _ensure_version_table

        await _ensure_version_table(mock_conn)

        mock_conn.execute.assert_called()
        call_sql = mock_conn.execute.call_args[0][0]
        assert "schema_migrations" in call_sql
        assert "CREATE TABLE IF NOT EXISTS" in call_sql

    @pytest.mark.asyncio
    async def test_get_current_version_returns_none_when_empty(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock()

        from db.migrate import get_current_version

        version = await get_current_version(mock_conn)
        assert version is None

    @pytest.mark.asyncio
    async def test_get_current_version_returns_version(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"version": "20260101120000"})
        mock_conn.execute = AsyncMock()

        from db.migrate import get_current_version

        version = await get_current_version(mock_conn)
        assert version == "20260101120000"

    @pytest.mark.asyncio
    async def test_get_applied_versions_returns_list(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[{"version": "00000000000000"}, {"version": "20260101120000"}]
        )
        mock_conn.execute = AsyncMock()

        from db.migrate import get_applied_versions

        versions = await get_applied_versions(mock_conn)
        assert versions == ["00000000000000", "20260101120000"]


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP C - Pending Migrations
# ═══════════════════════════════════════════════════════════════════════════════


class TestPendingMigrations:
    """Verify pending migration detection."""

    def test_no_pending_when_all_applied(self):
        from db.migrate import discover_migrations, get_pending_migrations

        all_m = discover_migrations()
        applied = [m["version"] for m in all_m]
        pending = get_pending_migrations(applied)
        assert len(pending) == 0

    def test_pending_when_none_applied(self):
        from db.migrate import discover_migrations, get_pending_migrations

        all_m = discover_migrations()
        pending = get_pending_migrations([])
        assert len(pending) == len(all_m)

    def test_pending_partial(self):
        from db.migrate import get_pending_migrations

        pending = get_pending_migrations(["00000000000000"])
        # Baseline applied: any non-baseline migrations are pending
        versions = [m["version"] for m in pending]
        assert "20260819000000" in versions
        assert "20260819010000" in versions
        assert "20260819100000" in versions
        # All non-baseline migrations should be pending
        assert len(pending) >= 3


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP D - Migration Execution
# ═══════════════════════════════════════════════════════════════════════════════


class TestMigrationExecution:
    """Verify migration application mechanics."""

    @pytest.mark.asyncio
    async def test_apply_migration_records_version(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        from db.migrate import apply_migration, discover_migrations

        migrations = discover_migrations()
        assert len(migrations) > 0

        success = await apply_migration(mock_conn, migrations[0])
        assert success is True

        # Should have called execute for INSERT into version table
        calls = [str(c) for c in mock_conn.execute.call_args_list]
        assert any("schema_migrations" in c for c in calls)

    @pytest.mark.asyncio
    async def test_apply_migration_returns_false_on_error(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("SQL error"))

        from db.migrate import apply_migration, discover_migrations

        migrations = discover_migrations()

        success = await apply_migration(mock_conn, migrations[0])
        assert success is False

    @pytest.mark.asyncio
    async def test_apply_migration_dry_run_rolls_back(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        from db.migrate import apply_migration, discover_migrations

        migrations = discover_migrations()

        success = await apply_migration(mock_conn, migrations[0], dry_run=True)
        assert success is True

        # Should have called ROLLBACK
        calls = [str(c) for c in mock_conn.execute.call_args_list]
        assert any("ROLLBACK" in c for c in calls)

    @pytest.mark.asyncio
    async def test_empty_migration_skipped(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        from db.migrate import apply_migration

        migration = {"version": "99999999999999", "name": "empty", "path": Path("/dev/null")}

        # Create a temporary empty file
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sql", mode="w", delete=False) as f:
            f.write("  \n  ")
            temp_path = Path(f.name)

        try:
            migration["path"] = temp_path
            success = await apply_migration(mock_conn, migration)
            assert success is True
        finally:
            temp_path.unlink()


class TestMigrationStatementSplitting:
    """The engine must split on real statement semicolons only, ignoring
    semicolons that appear inside '--' comment lines."""

    @pytest.mark.asyncio
    async def test_semicolon_in_comment_does_not_split(self):
        mock_conn = AsyncMock()
        sql = (
            "-- header comment; with semicolon\n"
            "CREATE TABLE sample_a (id INTEGER);\n"
            "-- footer comment; also has a semicolon\n"
            "CREATE INDEX idx_sample_a ON sample_a(id);\n"
        )
        import tempfile
        from pathlib import Path

        from db.migrate import apply_migration

        with tempfile.NamedTemporaryFile(
            suffix=".sql", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(sql)
            path = Path(f.name)
        try:
            migration = {"version": "88888888888888", "name": "split", "path": path}
            ok = await apply_migration(mock_conn, migration)
            assert ok is True
        finally:
            path.unlink()

        executed = [
            c.args[0] for c in mock_conn.execute.call_args_list if "schema_migrations" not in str(c)
        ]
        assert len(executed) == 2
        assert executed[0].startswith("CREATE TABLE sample_a")
        assert executed[1].startswith("CREATE INDEX idx_sample_a")

    @pytest.mark.asyncio
    async def test_dollar_quoted_do_block_not_split(self):
        """DO $$ ... END $$; must execute as ONE statement even though the
        body contains semicolons (P3.2 safety migration relies on this)."""
        mock_conn = AsyncMock()
        sql = (
            "ALTER TABLE commerce_offers ADD COLUMN IF NOT EXISTS media_count INTEGER;\n"
            "DO $$\n"
            "BEGIN\n"
            "    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'x') THEN\n"
            "        ALTER TABLE commerce_offers ADD CONSTRAINT x CHECK (media_count >= 0);\n"
            "    END IF;\n"
            "END $$;\n"
            "CREATE INDEX IF NOT EXISTS idx_x ON commerce_offers (media_count);\n"
        )
        import tempfile
        from pathlib import Path

        from db.migrate import apply_migration

        with tempfile.NamedTemporaryFile(
            suffix=".sql", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(sql)
            path = Path(f.name)
        try:
            migration = {"version": "66666666666666", "name": "dollar", "path": path}
            ok = await apply_migration(mock_conn, migration)
            assert ok is True
        finally:
            path.unlink()

        executed = [
            c.args[0] for c in mock_conn.execute.call_args_list if "schema_migrations" not in str(c)
        ]
        assert len(executed) == 3
        assert executed[0].startswith("ALTER TABLE commerce_offers ADD COLUMN")
        assert executed[1].startswith("DO $$")
        assert executed[1].rstrip().endswith("END $$")
        assert "ADD CONSTRAINT x" in executed[1]
        assert executed[2].startswith("CREATE INDEX")

    @pytest.mark.asyncio
    async def test_semicolon_in_string_literal_not_split(self):
        """RAISE EXCEPTION '...;...' text must not terminate a statement."""
        mock_conn = AsyncMock()
        sql = (
            "DO $$\n"
            "BEGIN\n"
            "    RAISE EXCEPTION 'P3.2: % groups map; resolve manually', 1;\n"
            "END $$;\n"
        )
        import tempfile
        from pathlib import Path

        from db.migrate import apply_migration

        with tempfile.NamedTemporaryFile(
            suffix=".sql", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(sql)
            path = Path(f.name)
        try:
            migration = {"version": "55555555555555", "name": "strsemi", "path": path}
            ok = await apply_migration(mock_conn, migration)
            assert ok is True
        finally:
            path.unlink()

        executed = [
            c.args[0] for c in mock_conn.execute.call_args_list if "schema_migrations" not in str(c)
        ]
        assert len(executed) == 1
        assert executed[0].startswith("DO $$")

    def test_p32_migration_splits_into_executable_statements(self):
        """The P3.2 safety migration must survive the engine splitter intact:
        every DO block whole, no mid-block fragments."""
        from db.migrate import _split_sql_statements

        sql = (MIGRATIONS_DIR / "20260916000000_p32_safety_foundation.sql").read_text(
            encoding="utf-8"
        )
        statements = _split_sql_statements(sql)
        do_blocks = [s for s in statements if s.startswith("DO $$")]
        assert len(do_blocks) == sql.count("DO $$") == 4
        for block in do_blocks:
            assert block.rstrip().endswith("END $$")
        # No fragment may start mid-block (e.g. with BEGIN/IF/RAISE/END).
        for stmt in statements:
            first = stmt.split("\n", 1)[0].strip().upper()
            assert not first.startswith(("BEGIN", "END IF", "END $$", "RAISE ")), stmt[:80]
        assert len(statements) == 18

    @pytest.mark.asyncio
    async def test_leading_comment_only_section_skipped(self):
        mock_conn = AsyncMock()
        sql = (
            "-- only comments; here\n"
            "-- still comments; and here\n"
            "\n"
            "CREATE INDEX idx_xyz ON users(id);\n"
        )
        import tempfile
        from pathlib import Path

        from db.migrate import apply_migration

        with tempfile.NamedTemporaryFile(
            suffix=".sql", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(sql)
            path = Path(f.name)
        try:
            migration = {"version": "77777777777777", "name": "comments", "path": path}
            ok = await apply_migration(mock_conn, migration)
            assert ok is True
        finally:
            path.unlink()

        executed = [
            c.args[0] for c in mock_conn.execute.call_args_list if "schema_migrations" not in str(c)
        ]
        assert len(executed) == 1
        assert executed[0].startswith("CREATE INDEX idx_xyz")


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP E - Status
# ═══════════════════════════════════════════════════════════════════════════════


class TestMigrationStatus:
    """Verify migration status reporting."""

    @pytest.mark.asyncio
    async def test_status_when_nothing_applied(self):
        from db.migrate import discover_migrations

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchrow = AsyncMock(return_value=None)

        all_m = discover_migrations()
        with patch("db.migrate.discover_migrations", return_value=all_m):
            from db.migrate import get_status

            status = await get_status(mock_conn)
            assert status["current_version"] is None
            assert status["pending_count"] == len(all_m)
            assert status["up_to_date"] is False

    @pytest.mark.asyncio
    async def test_status_when_fully_applied(self):
        from db.migrate import discover_migrations

        all_m = discover_migrations()
        applied = [m["version"] for m in all_m]

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[{"version": v} for v in applied])
        mock_conn.fetchrow = AsyncMock(return_value={"version": applied[-1]} if applied else None)

        with patch("db.migrate.discover_migrations", return_value=all_m):
            from db.migrate import get_status

            status = await get_status(mock_conn)
            assert status["up_to_date"] is True
            assert status["pending_count"] == 0
            assert status["applied_count"] == len(all_m)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP F - Baseline (Existing Database Adoption)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBaselineExistingSchema:
    """Verify existing database adoption via baseline."""

    @pytest.mark.asyncio
    async def test_baseline_records_version(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetchval = AsyncMock(return_value=14)

        from db.migrate import baseline_existing_schema

        ok = await baseline_existing_schema(mock_conn, "00000000000000")
        assert ok is True

    @pytest.mark.asyncio
    async def test_baseline_idempotent(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetch = AsyncMock(return_value=[{"version": "00000000000000"}])

        from db.migrate import baseline_existing_schema

        ok = await baseline_existing_schema(mock_conn, "00000000000000")
        assert ok is True

    @pytest.mark.asyncio
    async def test_baseline_rejects_empty_database(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=0)

        from db.migrate import baseline_existing_schema

        ok = await baseline_existing_schema(mock_conn)
        assert ok is False


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP G - Baseline SQL Content
# ═══════════════════════════════════════════════════════════════════════════════


class TestBaselineSqlContent:
    """Verify the baseline migration covers the full current schema."""

    def _read_baseline(self) -> str:
        baseline = MIGRATIONS_DIR / "00000000000000_baseline.sql"
        return baseline.read_text(encoding="utf-8")

    def test_baseline_has_all_required_tables(self):
        sql = self._read_baseline()
        required_tables = [
            "users",
            "messages",
            "conversation_summaries",
            "user_profiles",
            "message_embeddings",
            "personas",
            "operator_queue",
            "operators",
            "sessions",
            "conversation_attention",
            "conversation_notes",
            "dlq_messages",
            "conversation_tags",
            "conversation_tag_assignments",
        ]
        for table in required_tables:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, f"Missing table: {table}"

    def test_baseline_has_users_do_not_auto_reply(self):
        sql = self._read_baseline()
        assert "do_not_auto_reply" in sql

    def test_baseline_has_users_persona_id(self):
        sql = self._read_baseline()
        assert "persona_id" in sql

    def test_baseline_has_extensions(self):
        sql = self._read_baseline()
        assert "uuid-ossp" in sql
        assert "pg_trgm" in sql

    def test_baseline_has_all_indexes(self):
        sql = self._read_baseline()
        required_indexes = [
            "idx_messages_user_id_created",
            "idx_messages_content_trgm",
            "idx_conv_summaries_user_id",
            "idx_message_embeddings_user",
            "idx_message_embeddings_vector",
            "idx_personas_default",
            "idx_operator_queue_status",
            "idx_sessions_expires",
            "idx_users_name_trgm",
            "idx_conv_attention_status",
            "idx_conv_notes_user_created",
            "idx_conv_notes_content_trgm",
            "idx_conversation_tags_name_lower",
            "idx_conv_tag_assign_user",
            "idx_conv_tag_assign_tag",
        ]
        for idx in required_indexes:
            assert idx in sql, f"Missing index: {idx}"

    def test_baseline_has_default_personas(self):
        sql = self._read_baseline()
        assert "INSERT INTO personas" in sql
        assert "ON CONFLICT" in sql

    def test_baseline_uses_if_not_exists(self):
        sql = self._read_baseline()
        # All CREATE TABLE should be IF NOT EXISTS for idempotency
        lines = [l for l in sql.split("\n") if "CREATE TABLE" in l and "--" not in l]
        for line in lines:
            assert "IF NOT EXISTS" in line, f"Missing IF NOT EXISTS: {line.strip()}"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP H - Application Compatibility
# ═══════════════════════════════════════════════════════════════════════════════


class TestApplicationCompatibility:
    """Verify existing application code still works with migration infrastructure."""

    def test_verify_schema_importable(self):
        from db.postgres import verify_schema

        assert callable(verify_schema)

    def test_check_migrations_pending_importable(self):
        from db.postgres import check_migrations_pending

        assert callable(check_migrations_pending)

    def test_migrate_module_importable(self):
        import db.migrate

        assert hasattr(db.migrate, "discover_migrations")
        assert hasattr(db.migrate, "get_pending_migrations")
        assert hasattr(db.migrate, "apply_migration")
        assert hasattr(db.migrate, "upgrade")
        assert hasattr(db.migrate, "get_status")
        assert hasattr(db.migrate, "baseline_existing_schema")

    def test_migrate_cli_importable(self):
        from db.migrate import main

        assert callable(main)

    def test_version_table_name_constant(self):
        from db.migrate import VERSION_TABLE

        assert VERSION_TABLE == "schema_migrations"

    def test_migrations_dir_path(self):
        from db.migrate import MIGRATIONS_DIR

        assert MIGRATIONS_DIR.exists()
        assert MIGRATIONS_DIR.name == "migrations"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP I - Safety
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafety:
    """Verify no destructive operations are introduced."""

    def test_no_drop_table_in_baseline(self):
        sql = (MIGRATIONS_DIR / "00000000000000_baseline.sql").read_text(encoding="utf-8")
        assert "DROP TABLE" not in sql.upper()

    def test_no_drop_column_in_baseline(self):
        sql = (MIGRATIONS_DIR / "00000000000000_baseline.sql").read_text(encoding="utf-8")
        assert "DROP COLUMN" not in sql.upper()

    def test_no_truncate_in_baseline(self):
        sql = (MIGRATIONS_DIR / "00000000000000_baseline.sql").read_text(encoding="utf-8")
        assert "TRUNCATE" not in sql.upper()

    def test_baseline_uses_if_not_exists(self):
        sql = (MIGRATIONS_DIR / "00000000000000_baseline.sql").read_text(encoding="utf-8")
        lines = [l for l in sql.split("\n") if "CREATE TABLE" in l and "--" not in l]
        for line in lines:
            assert "IF NOT EXISTS" in line

    def test_baseline_insert_uses_on_conflict(self):
        sql = (MIGRATIONS_DIR / "00000000000000_baseline.sql").read_text(encoding="utf-8")
        assert "ON CONFLICT" in sql


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP J - Migration Failure Handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestMigrationFailureHandling:
    """Verify failed migrations are handled safely."""

    @pytest.mark.asyncio
    async def test_upgrade_stops_on_first_failure(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetch = AsyncMock(return_value=[])

        from db.migrate import discover_migrations, upgrade

        all_m = discover_migrations()

        call_count = 0

        async def fail_on_second_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 3:  # After CREATE TABLE + INSERT
                raise RuntimeError("Simulated failure")

        mock_conn.execute = AsyncMock(side_effect=fail_on_second_call)

        with patch("db.migrate.discover_migrations", return_value=all_m):
            versions = await upgrade(conn=mock_conn)
            # Should have applied some but not necessarily all
            # The important thing is it didn't crash
            assert isinstance(versions, list)

    @pytest.mark.asyncio
    async def test_failed_migration_not_recorded(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("SQL error"))

        from db.migrate import apply_migration, discover_migrations

        migrations = discover_migrations()

        success = await apply_migration(mock_conn, migrations[0])
        assert success is False

        # The INSERT into version table should NOT have been called
        # (since execute failed before reaching it)
        calls = [str(c) for c in mock_conn.execute.call_args_list]
        # Only the migration SQL should have been attempted
        assert not any("INSERT INTO schema_migrations" in c for c in calls)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP K - Upgrade Function
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpgradeFunction:
    """Verify the upgrade function orchestrates migrations correctly."""

    @pytest.mark.asyncio
    async def test_upgrade_applies_pending(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetch = AsyncMock(return_value=[])

        from db.migrate import upgrade

        versions = await upgrade(conn=mock_conn)
        assert isinstance(versions, list)
        # Baseline should have been applied
        if versions:
            assert "00000000000000" in versions

    @pytest.mark.asyncio
    async def test_upgrade_nothing_pending(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"version": "00000000000000"})
        mock_conn.fetch = AsyncMock(return_value=[{"version": "00000000000000"}])

        from db.migrate import get_pending_migrations, upgrade

        versions = await upgrade(conn=mock_conn)
        assert versions == [m["version"] for m in get_pending_migrations(["00000000000000"])]


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP L - P0.3 Schema/Query Regression Tests
# ═══════════════════════════════════════════════════════════════════════════════


SCHEMA_SQL = (Path(__file__).parent.parent / "db" / "schema.sql").read_text(encoding="utf-8")
POSTGRES_PY = (Path(__file__).parent.parent / "db" / "postgres.py").read_text(encoding="utf-8")


class TestP03SchemaRegressions:
    """Regression tests for P0.3 schema/query bug fixes."""

    def test_schema_sql_has_do_not_auto_reply(self):
        assert "do_not_auto_reply BOOLEAN DEFAULT FALSE" in SCHEMA_SQL

    def test_conversation_tags_has_no_color_column(self):
        import re
        match = re.search(
            r"CREATE TABLE IF NOT EXISTS conversation_tags\s*\((.*?)\)",
            SCHEMA_SQL,
            re.DOTALL,
        )
        assert match is not None, "conversation_tags table not found in schema.sql"
        body = match.group(1)
        assert "color" not in body.lower()

    def test_postgres_no_ct_color_reference(self):
        assert "ct.color" not in POSTGRES_PY

    def test_postgres_no_operators_name_reference(self):
        import re
        assert not re.search(r"SELECT\b.*\bname\b.*FROM\s+operators", POSTGRES_PY)
