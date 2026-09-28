"""Migration engine (commerce rebuild Phase 3f). Versioned SQL runner.

Discovers ``db/migrations/*.sql`` lexicographically by version prefix,
tracks applications in ``schema_migrations(version TEXT PK, name,
applied_at, execution_ms)``, and applies pending files in order with a
comment- and dollar-quote-aware statement splitter.

CLI: ``python -m db.migrate status`` | ``upgrade`` | ``baseline``.
``run_all.py`` auto-applies pending migrations on startup via
``get_status``/``upgrade``.

Contract: docs/HANDOFF.md §7.3; tests/test_database_migrations.py
(discovery, tracking, pending, execution, splitter, status, baseline,
safety, failure handling, upgrade).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("db.migrate")

VERSION_TABLE = "schema_migrations"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_VERSION_RE = re.compile(r"^([0-9]+)")


def _parse_migration(path: Path) -> dict[str, Any] | None:
    match = _VERSION_RE.match(path.name)
    if not match:
        return None
    version = match.group(1)
    name = path.name[len(version) :].lstrip("_")[:-4] if path.name.endswith(".sql") else path.name
    return {"version": version, "name": name or path.stem, "path": path}


def discover_migrations(directory: Path | None = None) -> list[dict[str, Any]]:
    """All ``*.sql`` migrations sorted by version (lexicographic)."""
    directory = directory or MIGRATIONS_DIR
    found: list[dict[str, Any]] = []
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.sql")):
        parsed = _parse_migration(path)
        if parsed is not None:
            found.append(parsed)
    found.sort(key=lambda m: m["version"])
    return found


def get_pending_migrations(applied: list[str]) -> list[dict[str, Any]]:
    """Discovered migrations whose version is not in ``applied`` (sync)."""
    applied_set = set(applied or [])
    return [m for m in discover_migrations() if m["version"] not in applied_set]


async def _ensure_version_table(conn: Any) -> None:
    await conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {VERSION_TABLE} (
            version TEXT PRIMARY KEY,
            name TEXT,
            applied_at TIMESTAMPTZ DEFAULT NOW(),
            execution_ms INTEGER
        )"""
    )


async def get_current_version(conn: Any) -> str | None:
    await _ensure_version_table(conn)
    row = await conn.fetchrow(f"SELECT version FROM {VERSION_TABLE} ORDER BY version DESC LIMIT 1")
    return str(row["version"]) if row else None


async def get_applied_versions(conn: Any) -> list[str]:
    await _ensure_version_table(conn)
    rows = await conn.fetch(f"SELECT version FROM {VERSION_TABLE} ORDER BY version")
    return [str(r["version"]) for r in rows]


def _split_sql_statements(sql: str) -> list[str]:
    """Split SQL on real statement semicolons only.

    Ignores ``--`` line comments (whole-line and trailing), ``$$`` /
    ``$tag$`` dollar-quoted blocks (DO bodies), and single-quoted string
    literals (with ``''`` escapes). Statements are stripped of trailing
    semicolons; empty/comment-only fragments are dropped.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    dollar_tag: str | None = None

    while i < n:
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
            else:
                buf.append(sql[i])
                i += 1
            continue
        ch = sql[i]
        two = sql[i : i + 2]
        if two == "--":
            end = sql.find("\n", i)
            i = n if end == -1 else end
            continue
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if sql[j + 1 : j + 2] == "'":
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i : j + 1])
            i = j + 1
            continue
        if ch == "$":
            m = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[i:])
            if m:
                dollar_tag = m.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmt = stmt.rstrip(";").strip()
                if stmt:
                    statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        tail = tail.rstrip(";").strip()
        if tail:
            statements.append(tail)
    return statements


async def apply_migration(conn: Any, migration: dict[str, Any], dry_run: bool = False) -> bool:
    """Apply one migration file. Records version on success; False on error.

    Empty (comment/whitespace-only) files are skipped as success without
    recording. dry_run executes then ROLLBACKs without recording.
    """
    try:
        sql = Path(migration["path"]).read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("db.migrate: unreadable %s (%s)", migration.get("version"), exc)
        return False
    statements = _split_sql_statements(sql)
    if not statements:
        return True
    started = time.monotonic()
    try:
        if dry_run:
            await conn.execute("BEGIN")
        for stmt in statements:
            await conn.execute(stmt)
        if dry_run:
            await conn.execute("ROLLBACK")
            return True
    except Exception as exc:  # noqa: BLE001 — caller decides; failure is data
        logger.warning(
            "db.migrate: %s failed (%s)", migration.get("version"), exc.__class__.__name__
        )
        return False
    elapsed_ms = int((time.monotonic() - started) * 1000)
    try:
        await conn.execute(
            f"INSERT INTO {VERSION_TABLE} (version, name, execution_ms) "
            "VALUES ($1, $2, $3) ON CONFLICT (version) DO NOTHING",
            migration["version"],
            migration.get("name", ""),
            elapsed_ms,
        )
    except Exception as exc:  # noqa: BLE001 — record failure, not silent
        logger.warning(
            "db.migrate: version record failed for %s (%s)",
            migration.get("version"),
            exc.__class__.__name__,
        )
        return False
    return True


async def get_status(conn: Any | None = None) -> dict[str, Any]:
    """{current_version, applied, applied_count, pending, pending_count, up_to_date}."""
    if conn is None:
        pool = await get_pool()
        async with pool.acquire() as owned:
            return await get_status(owned)
    await _ensure_version_table(conn)
    applied = await get_applied_versions(conn)
    current = await get_current_version(conn)
    pending = get_pending_migrations(applied)
    pending_versions = [m["version"] for m in pending]
    return {
        "current_version": current,
        "applied": applied,
        "applied_count": len(applied),
        "pending": pending_versions,
        "pending_count": len(pending_versions),
        "up_to_date": len(pending_versions) == 0,
    }


async def upgrade(conn: Any | None = None) -> list[str]:
    """Apply pending migrations in order; stops at first failure."""
    if conn is None:
        pool = await get_pool()
        async with pool.acquire() as owned:
            return await upgrade(owned)
    await _ensure_version_table(conn)
    applied = await get_applied_versions(conn)
    applied_versions: list[str] = []
    for migration in get_pending_migrations(applied):
        if not await apply_migration(conn, migration):
            logger.warning("db.migrate: stopping at %s", migration["version"])
            break
        applied_versions.append(migration["version"])
        applied.append(migration["version"])
    return applied_versions


async def baseline_existing_schema(conn: Any, version: str = "00000000000000") -> bool:
    """Adopt a non-empty database without replaying history.

    Records ``version`` when the DB has tables but the version is not yet
    tracked. Empty databases are rejected (False) — run upgrade instead.
    Idempotent: already-recorded versions succeed without re-inserting.
    """
    await _ensure_version_table(conn)
    applied = await get_applied_versions(conn)
    if version in applied:
        return True
    table_count = await conn.fetchval(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    )
    if not table_count:
        return False
    await conn.execute(
        f"INSERT INTO {VERSION_TABLE} (version, name, execution_ms) "
        "VALUES ($1, $2, $3) ON CONFLICT (version) DO NOTHING",
        version,
        "baseline",
        0,
    )
    return True


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="db.migrate", description="Versioned SQL runner")
    parser.add_argument("command", choices=["status", "upgrade", "baseline"])
    parser.add_argument("--version", default="00000000000000")
    args = parser.parse_args(argv)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if args.command == "status":
            status = await get_status(conn)
            print(f"current_version: {status['current_version']}")
            print(f"applied: {status['applied_count']} pending: {status['pending_count']}")
            for version in status["pending"]:
                print(f"  pending {version}")
            print("up_to_date" if status["up_to_date"] else "BEHIND")
            return 0 if status["up_to_date"] else 1
        if args.command == "upgrade":
            applied = await upgrade(conn)
            print(f"applied {len(applied)} migration(s): {', '.join(applied)}")
            return 0
        ok = await baseline_existing_schema(conn, args.version)
        print(f"baseline {args.version}: {'recorded' if ok else 'rejected (empty DB?)'}")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


__all__ = [
    "MIGRATIONS_DIR",
    "VERSION_TABLE",
    "apply_migration",
    "baseline_existing_schema",
    "discover_migrations",
    "get_applied_versions",
    "get_current_version",
    "get_pending_migrations",
    "get_pool",
    "get_status",
    "main",
    "upgrade",
]
