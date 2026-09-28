#!/usr/bin/env python
"""Terminate stale PostgreSQL connections before starting services.

Use this when you get 'too many clients already' errors from
killed processes that didn't shut down gracefully.
"""

import asyncio

import asyncpg


async def main():
    conn = await asyncpg.connect(dsn="postgresql://postgres:postgres@localhost:5432/postgres")
    my_pid = await conn.fetchval("SELECT pg_backend_pid()")
    rows = await conn.fetch("SELECT pid FROM pg_stat_activity WHERE pid <> $1", my_pid)
    terminated = 0
    for r in rows:
        await conn.execute("SELECT pg_terminate_backend($1)", r["pid"])
        terminated += 1
    await conn.close()
    print(f"Terminated {terminated} stale connection(s)")


if __name__ == "__main__":
    asyncio.run(main())
