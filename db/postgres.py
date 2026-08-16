import json
from typing import Any

import asyncpg

from core.config import get_settings

_settings = get_settings()

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=_settings.postgres_dsn,
        min_size=5,
        max_size=20,
        command_timeout=30,
    )


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        await init_pool()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def upsert_user(user_id: int, username: str, first_name: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (id, username, first_name, last_seen)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (id) DO UPDATE SET
                username = $2,
                first_name = $3,
                last_seen = NOW(),
                message_count = users.message_count + 1
            """,
            user_id,
            username,
            first_name,
        )


async def get_user(user_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        return dict(row) if row else None


async def update_funnel_stage(user_id: int, stage: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET funnel_stage = $1 WHERE id = $2", stage, user_id)


async def set_user_notes(user_id: int, notes: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET notes = $1 WHERE id = $2", notes, user_id)


async def block_user(user_id: int, blocked: bool = True) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET is_blocked = $1 WHERE id = $2", blocked, user_id)


async def save_inbound_message(user_id: int, content: str, telegram_message_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO messages
                (user_id, direction, content, telegram_message_id, sent_at)
            VALUES ($1, 'inbound', $2, $3, NOW())
            RETURNING id
            """,
            user_id,
            content,
            telegram_message_id,
        )
        return row["id"]


async def save_outbound_message(
    user_id: int,
    content: str,
    draft_content: str,
    was_edited: bool,
    was_auto_approved: bool,
    confidence_score: float,
    operator_id: int | None,
    telegram_message_id: int | None,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO messages (
                user_id, direction, content, draft_content,
                was_edited, was_auto_approved, confidence_score,
                operator_id, telegram_message_id, sent_at
            )
            VALUES ($1, 'outbound', $2, $3, $4, $5, $6, $7, $8, NOW())
            RETURNING id
            """,
            user_id,
            content,
            draft_content,
            was_edited,
            was_auto_approved,
            confidence_score,
            operator_id,
            telegram_message_id,
        )
        return row["id"]


async def get_recent_messages(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT direction, content, created_at
            FROM messages
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
        return [dict(r) for r in reversed(rows)]


async def get_user_profile(user_id: int) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT facts FROM user_profiles WHERE user_id = $1", user_id)
        if row and row["facts"]:
            return json.loads(row["facts"]) if isinstance(row["facts"], str) else dict(row["facts"])
        return {}


async def update_user_profile(user_id: int, facts: dict[str, Any]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_profiles (user_id, facts, updated_at)
            VALUES ($1, $2::jsonb, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                facts = $2::jsonb,
                updated_at = NOW()
            """,
            user_id,
            json.dumps(facts),
        )


async def get_latest_summary(user_id: int) -> str | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT summary FROM conversation_summaries
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            user_id,
        )
        return row["summary"] if row else None


async def save_summary(user_id: int, summary: str, message_count: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO conversation_summaries
                (user_id, summary, message_count_at_summary)
            VALUES ($1, $2, $3)
            """,
            user_id,
            summary,
            message_count,
        )


async def add_to_operator_queue(
    user_id: int,
    draft_content: str,
    confidence_score: float,
    flags: list[str],
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO operator_queue
                (user_id, draft_content, confidence_score, flags)
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING id
            """,
            user_id,
            draft_content,
            confidence_score,
            json.dumps(flags),
        )
        return row["id"]


async def get_queue_item(queue_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT q.*, u.username, u.first_name, u.funnel_stage
            FROM operator_queue q
            JOIN users u ON u.id = q.user_id
            WHERE q.id = $1
            """,
            queue_id,
        )
        return dict(row) if row else None


async def get_pending_queue_items(limit: int = 20) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT q.*, u.username, u.first_name, u.funnel_stage
            FROM operator_queue q
            JOIN users u ON u.id = q.user_id
            WHERE q.status = 'pending'
            ORDER BY q.created_at ASC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]


async def resolve_queue_item(
    queue_id: int,
    status: str,
    final_content: str | None = None,
    operator_id: int | None = None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE operator_queue SET
                status = $1,
                draft_content = COALESCE($2, draft_content),
                assigned_to = $3,
                resolved_at = NOW()
            WHERE id = $4
            """,
            status,
            final_content,
            operator_id,
            queue_id,
        )


async def insert_message_embedding(message_id: int, user_id: int, embedding: list[float]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO message_embeddings (message_id, user_id, embedding)
            VALUES ($1, $2, $3::vector)
            """,
            message_id,
            user_id,
            embedding,
        )


async def vector_search_messages(
    user_id: int,
    query_embedding: list[float],
    k: int = 5,
) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.content, m.direction, m.created_at,
                   1 - (e.embedding <=> $1::vector) AS similarity
            FROM message_embeddings e
            JOIN messages m ON m.id = e.message_id
            WHERE e.user_id = $2
            ORDER BY e.embedding <=> $1::vector
            LIMIT $3
            """,
            query_embedding,
            user_id,
            k,
        )
        return [dict(r) for r in rows]


async def upsert_user_embedding(user_id: int, embedding: list[float]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_profiles (user_id, embedding, updated_at)
            VALUES ($1, $2::vector, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                embedding = $2::vector,
                updated_at = NOW()
            """,
            user_id,
            embedding,
        )


async def get_user_profile_with_embedding(user_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT facts, embedding FROM user_profiles WHERE user_id = $1", user_id
        )
        if row:
            result = dict(row)
            if result.get("facts"):
                result["facts"] = (
                    json.loads(result["facts"])
                    if isinstance(result["facts"], str)
                    else dict(result["facts"])
                )
            return result
        return None
