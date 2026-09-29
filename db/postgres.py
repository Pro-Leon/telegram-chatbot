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


async def get_recent_messages(
    user_id: int, limit: int = 20, creator_id: int | None = None
) -> list[dict[str, Any]]:
    if creator_id is None:
        raise ValueError("creator_id is required (strict isolation)")
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, direction, content, created_at, creator_id
            FROM messages
            WHERE user_id = $1 AND (creator_id = $2 OR creator_id IS NULL)
            ORDER BY created_at DESC, id DESC
            LIMIT $3
            """,
            user_id,
            creator_id,
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


async def mutate_user_profile_atomically(user_id: int, mutate: Any) -> bool:
    """Row-locked whole-facts mutation. SELECT ... FOR UPDATE so other
    creators' namespaces on the same row cannot be clobbered. Returns
    True when persisted (mutator returning False aborts the write)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT facts FROM user_profiles WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if row and row["facts"] is not None:
                raw = row["facts"]
                facts = json.loads(raw) if isinstance(raw, str) else dict(raw)
            else:
                facts = {}
            if mutate(facts) is False:
                return False
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
            return True


async def update_commercial_preferences(
    creator_id: int, user_id: int, preferences: dict[str, Any]
) -> None:
    """Replace this creator's commercial-preference namespace (others kept)."""

    def _mutate(facts: dict[str, Any]) -> bool:
        by_creator = facts.setdefault("commercial_preferences_by_creator", {})
        by_creator[str(creator_id)] = dict(preferences)
        return True

    await mutate_user_profile_atomically(user_id, _mutate)


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


async def get_latest_summary_with_age(
    user_id: int, creator_id: int | None = None
) -> tuple[str | None, float | None]:
    """Latest summary plus age in days (both None when absent)."""
    from datetime import datetime, timezone

    pool = await get_pool()
    async with pool.acquire() as conn:
        if creator_id is None:
            row = await conn.fetchrow(
                """
                SELECT summary, created_at FROM conversation_summaries
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                user_id,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT summary, created_at FROM conversation_summaries
                WHERE user_id = $1 AND creator_id = $2
                ORDER BY created_at DESC
                LIMIT 1
                """,
                user_id,
                creator_id,
            )
        if not row:
            return None, None
        age: float | None = None
        created = row["created_at"]
        if created is not None:
            age = (datetime.now(timezone.utc) - created).total_seconds() / 86400
        summary = row["summary"]
        return (str(summary) if summary is not None else None), age


async def get_latest_summary_with_age_durable(
    user_id: int, creator_id: int | None = None
) -> tuple[tuple[str | None, float | None], bool, str]:
    """Durable ((summary, age_days), degraded, source). Never raises."""
    try:
        value = await get_latest_summary_with_age(user_id, creator_id=creator_id)
    except Exception:  # noqa: BLE001
        return (None, None), True, "error"
    if value[0] is None:
        return value, False, "miss"
    return value, False, "hit"


async def get_latest_summary_durable(
    user_id: int, creator_id: int | None = None
) -> tuple[str | None, bool, str]:
    """Durable (summary, degraded, source). Never raises."""
    try:
        summary, _age = await get_latest_summary_with_age(user_id, creator_id=creator_id)
    except Exception:  # noqa: BLE001
        return None, True, "error"
    if summary is None:
        return None, False, "miss"
    return summary, False, "hit"


async def get_user_durable(user_id: int) -> tuple[dict[str, Any] | None, bool, str]:
    """Durable (row, degraded, hit|miss|error). Never raises."""
    try:
        row = await get_user(user_id)
    except Exception:  # noqa: BLE001
        return None, True, "error"
    if row is None:
        return None, False, "miss"
    return row, False, "hit"


async def get_recent_messages_durable(
    user_id: int, limit: int = 20, creator_id: int | None = None
) -> tuple[list[dict[str, Any]], bool, str]:
    """Durable (rows ASC, degraded, hit|hit+legacy|miss|error). Never raises."""
    if creator_id is None:
        return [], True, "error"
    try:
        rows = await get_recent_messages(user_id, limit=limit, creator_id=creator_id)
    except Exception:  # noqa: BLE001
        return [], True, "error"
    if not rows:
        return [], False, "miss"
    kept = [r for r in rows if r.get("creator_id") in (None, creator_id)]
    kept.sort(key=lambda r: (r.get("created_at"), r.get("id")))
    source = "hit+legacy" if any(r.get("creator_id") is None for r in kept) else "hit"
    return kept, False, source


async def save_summary(
    user_id: int, summary: str, message_count: int, creator_id: int | None = None,
    source_count: int | None = None,
) -> None:
    """Persist a conversation summary with last-writer-wins freshness.

    One transaction: SELECT ... FOR UPDATE, then INSERT when absent or
    UPDATE when the incoming source_count >= stored count (ties and
    legacy sourceless writes apply; stale writes perform no write at
    all). Creator-scoped rows are independent.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, message_count_at_summary FROM conversation_summaries
                WHERE user_id = $1 AND (creator_id = $2 OR ($2 IS NULL AND creator_id IS NULL))
                ORDER BY id DESC LIMIT 1 FOR UPDATE
                """,
                user_id,
                creator_id,
            )
            effective = source_count if source_count is not None else message_count
            if row is None:
                await conn.execute(
                    """
                    INSERT INTO conversation_summaries
                        (user_id, creator_id, summary, message_count_at_summary)
                    VALUES ($1, $2, $3, $4)
                    """,
                    user_id,
                    creator_id,
                    summary,
                    effective,
                )
                return
            stored = row["message_count_at_summary"]
            if source_count is not None and stored is not None and source_count < stored:
                return
            await conn.execute(
                """
                UPDATE conversation_summaries
                SET summary = $2, message_count_at_summary = $3, updated_at = NOW()
                WHERE id = $1
                """,
                row["id"],
                summary,
                effective,
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


async def get_queue_item(
    queue_id: int, creator_id: int | None = None
) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if creator_id is None:
            row = await conn.fetchrow(
                """
                SELECT q.*, u.username, u.first_name, u.funnel_stage
                FROM operator_queue q
                JOIN users u ON u.id = q.user_id
                WHERE q.id = $1
                """,
                queue_id,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT q.*, u.username, u.first_name, u.funnel_stage
                FROM operator_queue q
                JOIN users u ON u.id = q.user_id
                WHERE q.id = $1 AND q.creator_id = $2
                """,
                queue_id,
                creator_id,
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
    creator_id: int | None = None,
    resolved_by: str | None = None,
    operator_id: int | None = None,
) -> bool:
    """Resolve a queue item (creator-scoped, pending-only). False when the
    row is missing, foreign, or already resolved (caller maps to 409)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if creator_id is None:
            sql = """
                UPDATE operator_queue SET
                    status = $1,
                    draft_content = COALESCE($2, draft_content),
                    final_content = COALESCE($2, final_content),
                    edited = CASE WHEN $2 IS NOT NULL AND $2 IS DISTINCT FROM draft_content
                                  THEN TRUE ELSE edited END,
                    assigned_to = COALESCE($3, assigned_to),
                    resolved_by = COALESCE($4, resolved_by),
                    resolved_at = NOW()
                WHERE id = $5 AND status = 'pending'
                """
            params: tuple = (status, final_content, operator_id, resolved_by, queue_id)
        else:
            sql = """
                UPDATE operator_queue SET
                    status = $1,
                    draft_content = COALESCE($2, draft_content),
                    final_content = COALESCE($2, final_content),
                    edited = CASE WHEN $2 IS NOT NULL AND $2 IS DISTINCT FROM draft_content
                                  THEN TRUE ELSE edited END,
                    assigned_to = COALESCE($3, assigned_to),
                    resolved_by = COALESCE($4, resolved_by),
                    resolved_at = NOW()
                WHERE id = $5 AND creator_id = $6 AND status = 'pending'
                """
            params = (status, final_content, operator_id, resolved_by, queue_id, creator_id)
        status_tag = await conn.execute(sql, *params)
        try:
            return int(str(status_tag).split()[-1]) > 0
        except (ValueError, IndexError):
            return False


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


async def insert_generation_telemetry(data: dict[str, Any]) -> bool:
    """Best-effort telemetry sink. generation_id passes through verbatim
    (md5 or legacy UUID — never converted). Returns True when stored."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO generation_telemetry
                (generation_id, user_id, creator_id, routing_decision, created_at)
            VALUES ($1, $2, $3, $4, NOW())
            """,
            data.get("generation_id"),
            data.get("user_id"),
            data.get("creator_id"),
            data.get("routing_decision"),
        )
        return True


# ── Persona resolution (creator isolation) ─────────────────────────────


async def get_user_persona(user_id: int, creator_id: int | None = None) -> str | None:
    """Fan's assigned persona instructions: legacy per-user link first,
    then creator-scoped default. Never another creator's default."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT p.instructions AS instructions FROM users u
            JOIN personas p ON p.id = u.persona_id
            WHERE u.id = $1
            """,
            user_id,
        )
        if row and row["instructions"]:
            return str(row["instructions"])
        if creator_id is None:
            return None
        row = await conn.fetchrow(
            """
            SELECT instructions FROM personas
            WHERE creator_id = $1
            ORDER BY is_default DESC, updated_at DESC
            LIMIT 1
            """,
            creator_id,
        )
        return str(row["instructions"]) if row and row["instructions"] else None


async def get_default_persona(creator_id: int | None = None) -> str | None:
    """Creator default, else legitimate global default. Never foreign."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if creator_id is not None:
            row = await conn.fetchrow(
                """
                SELECT instructions FROM personas
                WHERE creator_id = $1
                ORDER BY is_default DESC, updated_at DESC
                LIMIT 1
                """,
                creator_id,
            )
            if row and row["instructions"]:
                return str(row["instructions"])
        row = await conn.fetchrow(
            """
            SELECT instructions FROM personas
            WHERE creator_id IS NULL AND is_default = TRUE
            LIMIT 1
            """
        )
        return str(row["instructions"]) if row and row["instructions"] else None


async def get_all_personas(creator_id: int | None = None) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if creator_id is None:
            rows = await conn.fetch(
                "SELECT * FROM personas ORDER BY is_default DESC, id ASC"
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM personas WHERE creator_id = $1 OR creator_id IS NULL "
                "ORDER BY is_default DESC, id ASC",
                creator_id,
            )
        return [dict(r) for r in rows]


async def create_persona(
    name: str,
    instructions: str,
    is_default: bool = False,
    creator_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if is_default:
            await conn.execute(
                "UPDATE personas SET is_default = FALSE WHERE "
                "((creator_id = $1) OR ($1 IS NULL AND creator_id IS NULL))",
                creator_id,
            )
        row = await conn.fetchrow(
            """
            INSERT INTO personas
                (name, instructions, is_default, creator_id, metadata, version, updated_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, 1, NOW())
            RETURNING *
            """,
            name,
            instructions,
            bool(is_default),
            creator_id,
            json.dumps(metadata or {}),
        )
        try:
            from db.redis import invalidate_persona_cache

            await invalidate_persona_cache(creator_id=creator_id)
        except Exception:  # noqa: BLE001
            pass
        return dict(row)


async def update_persona(
    persona_id: int,
    name: str,
    instructions: str,
    is_default: bool,
    creator_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update a persona; version = version + 1. Metadata None preserves."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        current = await conn.fetchrow(
            "SELECT version, creator_id FROM personas WHERE id = $1", persona_id
        )
        if current is None:
            return None
        target_creator = creator_id if creator_id is not None else current["creator_id"]
        if is_default:
            await conn.execute(
                "UPDATE personas SET is_default = FALSE WHERE "
                "((creator_id = $1) OR ($1 IS NULL AND creator_id IS NULL)) AND id <> $2",
                target_creator,
                persona_id,
            )
        if metadata is None:
            row = await conn.fetchrow(
                """
                UPDATE personas SET name = $2, instructions = $3, is_default = $4,
                    creator_id = COALESCE($5, creator_id),
                    version = version + 1, updated_at = NOW()
                WHERE id = $1 RETURNING *
                """,
                persona_id, name, instructions, bool(is_default), creator_id,
            )
        else:
            row = await conn.fetchrow(
                """
                UPDATE personas SET name = $2, instructions = $3, is_default = $4,
                    creator_id = COALESCE($5, creator_id), metadata = $6::jsonb,
                    version = version + 1, updated_at = NOW()
                WHERE id = $1 RETURNING *
                """,
                persona_id, name, instructions, bool(is_default), creator_id,
                json.dumps(metadata),
            )
        try:
            from db.redis import invalidate_persona_cache

            await invalidate_persona_cache(creator_id=target_creator)
        except Exception:  # noqa: BLE001
            pass
        return dict(row) if row else None


async def delete_persona(persona_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT creator_id FROM personas WHERE id = $1", persona_id
        )
        target_creator = row["creator_id"] if row else None
        status = await conn.execute("DELETE FROM personas WHERE id = $1", persona_id)
        try:
            from db.redis import invalidate_persona_cache

            await invalidate_persona_cache(creator_id=target_creator)
        except Exception:  # noqa: BLE001
            pass
        try:
            return int(str(status).split()[-1]) > 0
        except (ValueError, IndexError):
            return False


async def set_user_persona(user_id: int, persona_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET persona_id = $1 WHERE id = $2", persona_id, user_id
        )


# ── Dashboard sessions ──────────────────────────────────────────────────


async def create_session(token: str, username: str, expires_at: float) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (id, data, expires_at)
            VALUES ($1, $2::jsonb, to_timestamp($3))
            ON CONFLICT (id) DO UPDATE SET
                data = $2::jsonb, expires_at = to_timestamp($3)
            """,
            token,
            json.dumps({"username": username}),
            float(expires_at),
        )


async def get_session(token: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT data FROM sessions WHERE id = $1", token)
        if not row:
            return None
        data = row["data"]
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return None
        if not isinstance(data, dict) or not data.get("username"):
            return None
        return {"username": data["username"]}


async def delete_session(token: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM sessions WHERE id = $1", token)


async def clean_expired_sessions() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "DELETE FROM sessions WHERE expires_at < NOW() RETURNING id"
        )
        return len(rows or [])


# ── Scheduled messages (durable outbound jobs) ──────────────────────────


async def create_scheduled_message(
    user_id: int,
    execute_at: Any,
    content: str,
    dedup_key: str,
    reason: str | None = None,
    creator_id: int | None = None,
    media_type: str | None = None,
    media_path: str | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
) -> int | None:
    """Insert a scheduled job, or return the existing active id for the key.

    Active (pending/processing) rows dedupe; completed/cancelled/failed
    rows never block a fresh schedule. (Actor identity is recorded by
    callers' audit trail; the table carries no actor columns.)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM scheduled_messages WHERE dedup_key = $1 "
            "AND status IN ('pending', 'processing') LIMIT 1",
            dedup_key,
        )
        if existing is not None:
            return int(existing)
        row = await conn.fetchrow(
            """
            INSERT INTO scheduled_messages
                (user_id, execute_at, content, dedup_key, reason, creator_id,
                 media_type, media_path, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')
            RETURNING id
            """,
            user_id,
            execute_at,
            content,
            dedup_key,
            reason or "",
            creator_id,
            media_type or "",
            media_path or "",
        )
        return int(row["id"]) if row else None


async def claim_due_messages(
    batch_size: int = 10, worker_id: str = "scheduler"
) -> list[dict[str, Any]]:
    """Claim due pending jobs (FOR UPDATE SKIP LOCKED). Returns claimed rows."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM scheduled_messages
            WHERE status = 'pending' AND execute_at <= NOW()
            ORDER BY execute_at, id
            LIMIT $1
            FOR UPDATE SKIP LOCKED
            """,
            batch_size,
        )
        claimed = []
        for row in rows or []:
            await conn.execute(
                """
                UPDATE scheduled_messages
                SET status = 'processing', claimed_by = $2, claimed_at = NOW(),
                    attempts = attempts + 1, updated_at = NOW()
                WHERE id = $1
                """,
                row["id"],
                worker_id,
            )
            claimed.append(dict(row))
        return claimed


async def recover_stale_messages(stale_seconds: int = 300) -> list[dict[str, Any]]:
    """Return stuck processing jobs to pending (attempts preserved)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM scheduled_messages
            WHERE status = 'processing'
              AND updated_at < NOW() - ($1 || ' seconds')::INTERVAL
            FOR UPDATE SKIP LOCKED
            """,
            stale_seconds,
        )
        recovered = []
        for row in rows or []:
            await conn.execute(
                "UPDATE scheduled_messages SET status = 'pending', updated_at = NOW() "
                "WHERE id = $1",
                row["id"],
            )
            recovered.append(dict(row))
        return recovered


async def get_scheduled_message(message_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM scheduled_messages WHERE id = $1", message_id
        )
        return dict(row) if row else None


async def get_scheduled_message_with_user(
    message_id: int, creator_id: int | None = None
) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if creator_id is None:
            row = await conn.fetchrow(
                """
                SELECT sm.*, u.username, u.first_name FROM scheduled_messages sm
                LEFT JOIN users u ON u.id = sm.user_id
                WHERE sm.id = $1
                """,
                message_id,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT sm.*, u.username, u.first_name FROM scheduled_messages sm
                LEFT JOIN users u ON u.id = sm.user_id
                WHERE sm.id = $1 AND sm.creator_id = $2
                """,
                message_id,
                creator_id,
            )
        return dict(row) if row else None


async def list_scheduled_messages_with_users(
    creator_id: int,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status is None:
            rows = await conn.fetch(
                """
                SELECT sm.*, u.username, u.first_name FROM scheduled_messages sm
                LEFT JOIN users u ON u.id = sm.user_id
                WHERE sm.creator_id = $1
                ORDER BY sm.execute_at ASC, sm.id ASC LIMIT $2 OFFSET $3
                """,
                creator_id,
                limit,
                offset,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT sm.*, u.username, u.first_name FROM scheduled_messages sm
                LEFT JOIN users u ON u.id = sm.user_id
                WHERE sm.creator_id = $1 AND sm.status = $2
                ORDER BY sm.execute_at ASC, sm.id ASC LIMIT $3 OFFSET $4
                """,
                creator_id,
                status,
                limit,
                offset,
            )
        return [dict(r) for r in rows or []]


async def count_scheduled_messages(
    creator_id: int, status: str | None = None
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status is None:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM scheduled_messages WHERE creator_id = $1",
                creator_id,
            )
        else:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM scheduled_messages "
                "WHERE creator_id = $1 AND status = $2",
                creator_id,
                status,
            )
        return int(row["cnt"]) if row else 0


async def get_scheduled_stats(creator_id: int) -> dict[str, int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, COUNT(*) AS cnt FROM scheduled_messages "
            "WHERE creator_id = $1 GROUP BY status",
            creator_id,
        )
        return {str(r["status"]): int(r["cnt"]) for r in rows or []}


async def cancel_scheduled_message(message_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            "UPDATE scheduled_messages SET status = 'cancelled', updated_at = NOW() "
            "WHERE id = $1 AND status = 'pending'",
            message_id,
        )
        try:
            return int(str(status).split()[-1]) > 0
        except (ValueError, IndexError):
            return False


async def cancel_scheduled_message_rich(
    message_id: int, creator_id: int | None = None
) -> dict[str, Any]:
    """Creator-scoped cancel with prior-state evidence for audit."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if creator_id is None:
            row = await conn.fetchrow(
                "SELECT id, status FROM scheduled_messages WHERE id = $1", message_id
            )
        else:
            row = await conn.fetchrow(
                "SELECT id, status FROM scheduled_messages WHERE id = $1 AND creator_id = $2",
                message_id,
                creator_id,
            )
        if row is None:
            return {"cancelled": False, "status": None, "reason": "not_found"}
        prior = str(row["status"])
        if prior not in ("pending", "processing"):
            return {"cancelled": False, "status": prior, "reason": f"not_cancellable_{prior}"}
        if creator_id is None:
            status = await conn.execute(
                "UPDATE scheduled_messages SET status = 'cancelled', updated_at = NOW() "
                "WHERE id = $1 AND status IN ('pending', 'processing')",
                message_id,
            )
        else:
            status = await conn.execute(
                "UPDATE scheduled_messages SET status = 'cancelled', updated_at = NOW() "
                "WHERE id = $1 AND creator_id = $2 AND status IN ('pending', 'processing')",
                message_id,
                creator_id,
            )
        try:
            changed = int(str(status).split()[-1]) > 0
        except (ValueError, IndexError):
            changed = False
        if not changed:
            return {"cancelled": False, "status": prior, "reason": "race_lost"}
        return {"cancelled": True, "status": prior, "reason": None}


async def mark_scheduled_enqueued(message_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE scheduled_messages SET status = 'completed', completed_at = NOW(), "
            "updated_at = NOW() WHERE id = $1",
            message_id,
        )


async def mark_scheduled_failed(message_id: int, error: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE scheduled_messages SET status = 'failed', last_error = $2, "
            "failed_at = NOW(), attempts = attempts + 1, updated_at = NOW() "
            "WHERE id = $1",
            message_id,
            error,
        )


async def mark_scheduled_suppressed(message_id: int, reason: str) -> None:
    """Terminal suppression (completed + reason) for ineligible recipients."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE scheduled_messages SET status = 'completed', last_error = $2, "
            "completed_at = NOW(), updated_at = NOW() WHERE id = $1",
            message_id,
            reason,
        )


async def get_user_safety_info(user_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, is_blocked, do_not_auto_reply FROM users WHERE id = $1",
            user_id,
        )
        return dict(row) if row else None


async def get_scheduled_status(message_id: int) -> str | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM scheduled_messages WHERE id = $1", message_id
        )
        return str(row["status"]) if row else None


async def get_queue_item_for_send(
    queue_id: int, creator_id: int | None = None
) -> dict[str, Any] | None:
    """Fresh pending-only queue row for send (cancel-race guard)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if creator_id is None:
            row = await conn.fetchrow(
                "SELECT * FROM operator_queue WHERE id = $1 AND status = 'pending'",
                queue_id,
            )
        else:
            row = await conn.fetchrow(
                "SELECT * FROM operator_queue WHERE id = $1 AND creator_id = $2 "
                "AND status = 'pending'",
                queue_id,
                creator_id,
            )
        return dict(row) if row else None


async def is_user_auto_reply_excluded(user_id: int) -> bool:
    """True when the fan opted out of auto-reply (users.do_not_auto_reply)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT do_not_auto_reply FROM users WHERE id = $1", user_id)
        return bool(row["do_not_auto_reply"]) if row else False


async def save_outbound_after_send(
    user_id: int,
    content: str,
    draft_content: str | None = None,
    was_edited: bool = False,
    was_auto_approved: bool = False,
    confidence_score: float = 0,
    operator_id: int | None = None,
    telegram_message_id: int | None = None,
    media_type: str | None = None,
    media_path: str | None = None,
    fangate_media_id: int | None = None,
    creator_id: int | None = None,
    generation_id: str | None = None,
    dedup_id: str | None = None,
) -> None:
    """Persist an outbound send. Upsert on (creator_id, dedup_id): retry wire
    updates telegram_message_id/sent_at; content is first-wins. Blank
    dedup uses the legacy plain insert (never conflicts)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if dedup_id is None or (isinstance(dedup_id, str) and not dedup_id.strip()):
            await conn.execute(
                """
                INSERT INTO messages
                    (user_id, creator_id, generation_id, content,
                     draft_content, was_edited, was_auto_approved,
                     confidence_score, operator_id, telegram_message_id,
                     media_type, media_path, fangate_media_id, direction, sent_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        $13, 'outbound', NOW())
                """,
                user_id,
                creator_id,
                generation_id,
                content,
                draft_content if draft_content is not None else content,
                bool(was_edited),
                bool(was_auto_approved),
                float(confidence_score or 0),
                operator_id,
                telegram_message_id,
                media_type,
                media_path,
                fangate_media_id,
            )
            return
        await conn.execute(
            """
            INSERT INTO messages
                (user_id, creator_id, generation_id, dedup_id, content,
                 draft_content, was_edited, was_auto_approved,
                 confidence_score, operator_id, telegram_message_id,
                 media_type, media_path, fangate_media_id, direction, sent_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, 'outbound', NOW())
            ON CONFLICT (creator_id, dedup_id) WHERE dedup_id IS NOT NULL
            DO UPDATE SET telegram_message_id = EXCLUDED.telegram_message_id,
                          sent_at = NOW()
            """,
            user_id,
            creator_id,
            generation_id,
            dedup_id,
            content,
            draft_content if draft_content is not None else content,
            bool(was_edited),
            bool(was_auto_approved),
            float(confidence_score or 0),
            operator_id,
            telegram_message_id,
            media_type,
            media_path,
            fangate_media_id,
        )


# ── Schema verification (migration health) ─────────────────────────────


async def _missing_tables(conn: Any, tables: list[str]) -> list[str]:
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = ANY($1)",
        list(tables),
    )
    present = {str(r["table_name"]) for r in rows}
    return [t for t in tables if t not in present]


async def verify_schema() -> dict[str, Any]:
    """Check core + commerce tables exist (incl. scheduled_messages,
    dropfans_drop_intents). Fail-open: never raises; reports missing."""
    import logging

    expected = [
        "users",
        "messages",
        "conversation_summaries",
        "user_profiles",
        "operator_queue",
        "scheduled_messages",
        "creators",
        "creator_integrations",
        "fangate_products",
        "fangate_transactions",
        "commerce_offers",
        "vault_media_deliveries",
        "fan_segments",
        "automation_operations",
        "dropfans_drop_intents",
        "commerce_content_families",
        "commerce_offer_definitions",
    ]
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            missing = await _missing_tables(conn, expected)
    except Exception as exc:  # noqa: BLE001 — verification is best-effort
        logging.getLogger("db.postgres").warning(
            "verify_schema failed (%s)", exc.__class__.__name__
        )
        return {"ok": False, "missing": list(expected), "unknown": True}
    return {"ok": not missing, "missing": missing, "unknown": False}


async def verify_commerce_safety_schema() -> dict[str, Any]:
    """Check the P3.2 commerce safety schema is present.

    Returns {"present": bool, "missing": [...], "unknown": bool}.
    Connectivity failures yield unknown (never healthy).
    """
    import logging

    expected = ["commerce_offers", "dropfans_drop_intents"]
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            missing = await _missing_tables(conn, expected)
    except Exception as exc:  # noqa: BLE001 — verification is best-effort
        logging.getLogger("db.postgres").warning(
            "verify_commerce_safety_schema failed (%s)", exc.__class__.__name__
        )
        return {"present": False, "missing": list(expected), "unknown": True}
    return {"present": not missing, "missing": missing, "unknown": False}


async def check_migrations_pending() -> dict[str, Any]:
    """Report migration status via the migrate engine (fail-closed unknown)."""
    import logging

    try:
        from db.migrate import get_status

        return await get_status()
    except Exception as exc:  # noqa: BLE001 — never break callers
        logging.getLogger("db.postgres").warning(
            "check_migrations_pending failed (%s)", exc.__class__.__name__
        )
        return {
            "current_version": None,
            "applied": [],
            "applied_count": 0,
            "pending": [],
            "pending_count": 0,
            "up_to_date": False,
            "unknown": True,
        }
