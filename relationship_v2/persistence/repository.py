"""Phase 1 repositories: creator-scoped, fail-closed, idempotent writes.

Shared infra reuse (allowed, PRESERVED):
  db.postgres.get_pool — PostgreSQL connection pool.
No V1 imports. No commerce writes. No provider calls.
Each function is the documented owner for its table (see owners.py).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger("sunny.v2.repository")


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _pool():
    from db.postgres import get_pool

    return await get_pool()


def _require_scope(creator_id: int, user_id: int) -> None:
    if not isinstance(creator_id, int) or creator_id <= 0:
        raise ValueError("creator_id must be a positive int (fail-closed)")
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("user_id must be a positive int (fail-closed)")


def _to_vector_literal(values: list[float]) -> str:
    """Render a pgvector literal. asyncpg cannot bind lists to ::vector."""
    if not values:
        raise ValueError("embedding required")
    return "[" + ",".join(str(float(v)) for v in values) + "]"


async def get_or_create_relationship(
    creator_id: int, user_id: int, provenance: str
) -> dict[str, Any]:
    """Owner of v2_relationships. Idempotent on (creator_id, user_id)."""
    _require_scope(creator_id, user_id)
    if not provenance:
        raise ValueError("provenance required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO v2_relationships
                (creator_id, user_id, provenance, first_interaction_at, last_interaction_at)
            VALUES ($1, $2, $3, NOW(), NOW())
            ON CONFLICT (creator_id, user_id) DO UPDATE SET
                last_interaction_at = NOW(),
                updated_at = NOW()
            RETURNING *
            """,
            creator_id,
            user_id,
            provenance,
        )
        return dict(row)


async def create_memory_fact(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    category: str,
    memory_key: str,
    value: str,
    confidence: float,
    effective_from: datetime | None = None,
    importance: str = "normal",
    provenance: str = "",
    source_event_id: str | None = None,
    generation_id: str | None = None,
) -> dict[str, Any]:
    """Owner of v2_memory_facts (candidate insert; promotion via supersede)."""
    _require_scope(creator_id, user_id)
    if not provenance:
        raise ValueError("provenance required")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("confidence must be in [0, 1]")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO v2_memory_facts
                (creator_id, user_id, relationship_id, category, memory_key,
                 value, status, importance, confidence, effective_from,
                 provenance, source_event_id, generation_id)
            VALUES ($1,$2,$3,$4,$5,$6,'candidate',$7,$8,COALESCE($9, NOW()),$10,$11,$12)
            RETURNING *
            """,
            creator_id,
            user_id,
            relationship_id,
            category,
            memory_key,
            value,
            importance,
            confidence,
            effective_from,
            provenance,
            source_event_id,
            generation_id,
        )
        return dict(row)


async def supersede_memory_fact(
    creator_id: int,
    user_id: int,
    old_fact_id: UUID,
    new_value: str,
    confidence: float,
    provenance: str,
) -> dict[str, Any]:
    """Supersede current fact: old row -> superseded, new row -> current.

    Single transaction; history preserved (non-destructive).
    """
    _require_scope(creator_id, user_id)
    if not provenance:
        raise ValueError("provenance required")
    pool = await _pool()
    async with pool.acquire() as conn, conn.transaction():
        old = await conn.fetchrow(
            """
                SELECT * FROM v2_memory_facts
                WHERE id = $1 AND creator_id = $2 AND user_id = $3
                """,
            old_fact_id,
            creator_id,
            user_id,
        )
        if old is None:
            raise ValueError("old fact not found in scope")
        old = dict(old)
        await conn.execute(
            """
                UPDATE v2_memory_facts
                SET status = 'superseded', effective_to = NOW(), updated_at = NOW()
                WHERE id = $1
                """,
            old_fact_id,
        )
        row = await conn.fetchrow(
            """
                INSERT INTO v2_memory_facts
                    (creator_id, user_id, relationship_id, category, memory_key,
                     value, previous_value, status, importance, confidence,
                     effective_from, supersedes_id, provenance)
                VALUES ($1,$2,$3,$4,$5,$6,$7,'current',$8,$9,NOW(),$10,$11)
                RETURNING *
                """,
            creator_id,
            user_id,
            old["relationship_id"],
            old["category"],
            old["memory_key"],
            new_value,
            old["value"],
            old["importance"],
            confidence,
            provenance,
            old_fact_id,
        )
        return dict(row)


async def create_memory_episode(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    episode_type: str,
    summary: str,
    provenance: str,
    salience: float = 0.5,
    generation_id: str | None = None,
    conversation_id: UUID | None = None,
) -> dict[str, Any]:
    """Owner of v2_memory_episodes (append-only)."""
    _require_scope(creator_id, user_id)
    if not provenance or not summary or not episode_type:
        raise ValueError("episode_type/summary/provenance required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO v2_memory_episodes
                (creator_id, user_id, relationship_id, episode_type,
                 summary, salience, generation_id, conversation_id, provenance)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING *
            """,
            creator_id,
            user_id,
            relationship_id,
            episode_type,
            summary,
            salience,
            generation_id,
            conversation_id,
            provenance,
        )
        return dict(row)


async def create_conversation(
    creator_id: int, user_id: int, relationship_id: UUID, provenance: str
) -> dict[str, Any]:
    """Owner of v2_conversations."""
    _require_scope(creator_id, user_id)
    if not provenance:
        raise ValueError("provenance required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO v2_conversations
                (creator_id, user_id, relationship_id, provenance)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            creator_id,
            user_id,
            relationship_id,
            provenance,
        )
        return dict(row)


async def create_conversation_turn(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    conversation_id: UUID,
    generation_id: str,
    inbound_event_id: str,
    plan_hash: str,
    validation_verdict: str,
    routing_decision: str,
) -> dict[str, Any]:
    """Owner of v2_conversation_turns. Idempotent on (conversation, generation)."""
    _require_scope(creator_id, user_id)
    for name, val in (
        ("generation_id", generation_id),
        ("inbound_event_id", inbound_event_id),
        ("plan_hash", plan_hash),
        ("validation_verdict", validation_verdict),
        ("routing_decision", routing_decision),
    ):
        if not val:
            raise ValueError(f"{name} required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO v2_conversation_turns
                (creator_id, user_id, relationship_id, conversation_id,
                 generation_id, inbound_event_id, plan_hash,
                 validation_verdict, routing_decision)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (conversation_id, generation_id) DO NOTHING
            RETURNING *
            """,
            creator_id,
            user_id,
            relationship_id,
            conversation_id,
            generation_id,
            inbound_event_id,
            plan_hash,
            validation_verdict,
            routing_decision,
        )
        if row is None:
            existing = await conn.fetchrow(
                """
                SELECT * FROM v2_conversation_turns
                WHERE conversation_id = $1 AND generation_id = $2
                """,
                conversation_id,
                generation_id,
            )
            return dict(existing)
        return dict(row)


async def create_relationship_event(
    event_id: str,
    event_type: str,
    creator_id: int,
    user_id: int,
    idempotency_key: str,
    producer: str,
    payload: dict[str, Any] | None = None,
    relationship_id: UUID | None = None,
    conversation_id: UUID | None = None,
    generation_id: str | None = None,
) -> dict[str, Any]:
    """Owner of v2_events. Idempotent on (creator, user, idempotency_key)."""
    _require_scope(creator_id, user_id)
    for name, val in (
        ("event_id", event_id),
        ("event_type", event_type),
        ("idempotency_key", idempotency_key),
        ("producer", producer),
    ):
        if not val:
            raise ValueError(f"{name} required")
    import json

    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO v2_events
                (event_id, event_type, creator_id, user_id, relationship_id,
                 conversation_id, generation_id, idempotency_key, producer, payload)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
            ON CONFLICT (creator_id, user_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            event_id,
            event_type,
            creator_id,
            user_id,
            relationship_id,
            conversation_id,
            generation_id,
            idempotency_key,
            producer,
            json.dumps(payload or {}),
        )
        if row is None:
            existing = await conn.fetchrow(
                """
                SELECT * FROM v2_events
                WHERE creator_id = $1 AND user_id = $2 AND idempotency_key = $3
                """,
                creator_id,
                user_id,
                idempotency_key,
            )
            return dict(existing)
        return dict(row)


async def create_commerce_ref(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    commerce_request_id: str,
    payload_hash: str,
    confirmed_at: datetime,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """Owner of v2_commerce_refs. Cached confirmations only, never truth."""
    _require_scope(creator_id, user_id)
    if not commerce_request_id or not payload_hash:
        raise ValueError("commerce_request_id/payload_hash required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO v2_commerce_refs
                (creator_id, user_id, relationship_id, commerce_request_id,
                 payload_hash, confirmed_at, expires_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (creator_id, user_id, commerce_request_id) DO UPDATE SET
                payload_hash = EXCLUDED.payload_hash,
                confirmed_at = EXCLUDED.confirmed_at,
                expires_at = EXCLUDED.expires_at
            RETURNING *
            """,
            creator_id,
            user_id,
            relationship_id,
            commerce_request_id,
            payload_hash,
            confirmed_at,
            expires_at,
        )
        return dict(row)


async def record_engagement_signal(    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    topic: str,
    behavior: str,
    polarity: str,
    confidence: float,
    provenance: str,
) -> dict[str, Any]:
    """Owner of v2_engagement_signals. Accumulates evidence (never one-event rule)."""
    _require_scope(creator_id, user_id)
    if not topic or not behavior or not provenance:
        raise ValueError("topic/behavior/provenance required")
    if polarity not in ("positive", "negative", "neutral"):
        raise ValueError("polarity must be positive|negative|neutral")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("confidence must be in [0, 1]")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO v2_engagement_signals
                (creator_id, user_id, relationship_id, topic, behavior,
                 polarity, confidence, provenance)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (creator_id, user_id, topic, behavior) DO UPDATE SET
                evidence_count = v2_engagement_signals.evidence_count + 1,
                polarity = EXCLUDED.polarity,
                confidence = EXCLUDED.confidence,
                last_observed_at = NOW(),
                updated_at = NOW()
            RETURNING *
            """,
            creator_id,
            user_id,
            relationship_id,
            topic,
            behavior,
            polarity,
            confidence,
            provenance,
        )
        return dict(row)


async def mark_event_processed(
    event_id: str,
    processor: str,
    creator_id: int,
    user_id: int,
    relationship_id: UUID | None = None,
) -> dict[str, Any]:
    """Owner of v2_processed_events. Idempotent on (event_id, processor).

    Returns (already_processed: bool via existing row id match is left to the
    caller — redelivery returns the existing row, never a duplicate).
    """
    _require_scope(creator_id, user_id)
    for name, val in (("event_id", event_id), ("processor", processor)):
        if not val:
            raise ValueError(f"{name} required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO v2_processed_events
                (event_id, processor, creator_id, user_id, relationship_id)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (event_id, processor) DO NOTHING
            RETURNING *, (xmax = 0) AS inserted
            """,
            event_id,
            processor,
            creator_id,
            user_id,
            relationship_id,
        )
        if row is None:
            existing = await conn.fetchrow(
                """
                SELECT *, FALSE AS inserted FROM v2_processed_events
                WHERE event_id = $1 AND processor = $2
                """,
                event_id,
                processor,
            )
            return dict(existing)
        return dict(row)


async def is_event_processed(event_id: str, processor: str) -> bool:
    """Non-mutating idempotency check for (event_id, processor)."""
    if not event_id or not processor:
        raise ValueError("event_id/processor required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 FROM v2_processed_events
            WHERE event_id = $1 AND processor = $2
            """,
            event_id,
            processor,
        )
        return row is not None


async def create_open_loop(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    description: str,
    provenance: str,
    priority: str = "normal",
    expected_at: datetime | None = None,
    source_memory_id: UUID | None = None,
    source_event_id: str | None = None,
) -> dict[str, Any]:
    """Owner of v2_open_loops. Opens a durable conversational thread."""
    _require_scope(creator_id, user_id)
    if not description or not description.strip() or len(description) > 512:
        raise ValueError("description must be 1..512 chars")
    if priority not in ("low", "normal", "high"):
        raise ValueError("priority must be low|normal|high")
    if not provenance:
        raise ValueError("provenance required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO v2_open_loops
                (creator_id, user_id, relationship_id, source_memory_id,
                 source_event_id, description, expected_at, priority, provenance)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING *
            """,
            creator_id,
            user_id,
            relationship_id,
            source_memory_id,
            source_event_id,
            description.strip(),
            expected_at,
            priority,
            provenance,
        )
        return dict(row)


async def mark_loop_referenced(
    loop_id: UUID, creator_id: int, user_id: int
) -> dict[str, Any]:
    """Record a follow-up reference: attempts+1, REFERENCED, timestamp."""
    _require_scope(creator_id, user_id)
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE v2_open_loops SET
                status = 'referenced',
                follow_up_attempts = follow_up_attempts + 1,
                last_referenced_at = NOW(),
                updated_at = NOW()
            WHERE id = $1 AND creator_id = $2 AND user_id = $3
              AND status IN ('open','due','referenced')
            RETURNING *
            """,
            loop_id,
            creator_id,
            user_id,
        )
        if row is None:
            raise ValueError("loop not found or not referenceable in scope")
        return dict(row)


async def resolve_open_loop(
    loop_id: UUID, creator_id: int, user_id: int, outcome: str
) -> dict[str, Any]:
    """Close a loop as RESOLVED with its outcome (what happened)."""
    _require_scope(creator_id, user_id)
    if not outcome or not outcome.strip():
        raise ValueError("outcome required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE v2_open_loops SET
                status = 'resolved',
                outcome = $4,
                resolved_at = NOW(),
                updated_at = NOW()
            WHERE id = $1 AND creator_id = $2 AND user_id = $3
              AND status IN ('open','due','referenced')
            RETURNING *
            """,
            loop_id,
            creator_id,
            user_id,
            outcome.strip(),
        )
        if row is None:
            raise ValueError("loop not found or not resolvable in scope")
        return dict(row)


async def close_open_loop(
    loop_id: UUID, creator_id: int, user_id: int, status: str
) -> dict[str, Any]:
    """Close a loop as EXPIRED or DISMISSED (no longer follow-up worthy)."""
    _require_scope(creator_id, user_id)
    if status not in ("expired", "dismissed"):
        raise ValueError("status must be expired|dismissed")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE v2_open_loops SET
                status = $4,
                resolved_at = NOW(),
                updated_at = NOW()
            WHERE id = $1 AND creator_id = $2 AND user_id = $3
              AND status IN ('open','due','referenced')
            RETURNING *
            """,
            loop_id,
            creator_id,
            user_id,
            status,
        )
        if row is None:
            raise ValueError("loop not found or not closeable in scope")
        return dict(row)


async def list_active_loops(
    creator_id: int, user_id: int, relationship_id: UUID | None = None
) -> list[dict[str, Any]]:
    """Non-mutating read of actionable loops (open/due/referenced)."""
    _require_scope(creator_id, user_id)
    pool = await _pool()
    async with pool.acquire() as conn:
        if relationship_id is not None:
            rows = await conn.fetch(
                """
                SELECT * FROM v2_open_loops
                WHERE creator_id = $1 AND user_id = $2 AND relationship_id = $3
                  AND status IN ('open','due','referenced')
                ORDER BY expected_at NULLS LAST, created_at
                """,
                creator_id,
                user_id,
                relationship_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM v2_open_loops
                WHERE creator_id = $1 AND user_id = $2
                  AND status IN ('open','due','referenced')
                ORDER BY expected_at NULLS LAST, created_at
                """,
                creator_id,
                user_id,
            )
        return [dict(r) for r in rows]


async def list_current_facts(
    creator_id: int,
    user_id: int,
    relationship_id: UUID | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Non-mutating read of current facts (never historical). Bounded."""
    _require_scope(creator_id, user_id)
    if limit < 1 or limit > 200:
        raise ValueError("limit must be 1..200")
    pool = await _pool()
    async with pool.acquire() as conn:
        if relationship_id is not None:
            rows = await conn.fetch(
                """
                SELECT * FROM v2_memory_facts
                WHERE creator_id = $1 AND user_id = $2 AND relationship_id = $3
                  AND status = 'current'
                ORDER BY updated_at DESC
                LIMIT $4
                """,
                creator_id,
                user_id,
                relationship_id,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM v2_memory_facts
                WHERE creator_id = $1 AND user_id = $2
                  AND status = 'current'
                ORDER BY updated_at DESC
                LIMIT $3
                """,
                creator_id,
                user_id,
                limit,
            )
        return [dict(r) for r in rows]


async def list_recent_episodes(
    creator_id: int,
    user_id: int,
    relationship_id: UUID | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Non-mutating read of recent episodes, newest first. Bounded."""
    _require_scope(creator_id, user_id)
    if limit < 1 or limit > 100:
        raise ValueError("limit must be 1..100")
    pool = await _pool()
    async with pool.acquire() as conn:
        if relationship_id is not None:
            rows = await conn.fetch(
                """
                SELECT * FROM v2_memory_episodes
                WHERE creator_id = $1 AND user_id = $2 AND relationship_id = $3
                ORDER BY created_at DESC
                LIMIT $4
                """,
                creator_id,
                user_id,
                relationship_id,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM v2_memory_episodes
                WHERE creator_id = $1 AND user_id = $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                creator_id,
                user_id,
                limit,
            )
        return [dict(r) for r in rows]


async def list_signals(
    creator_id: int,
    user_id: int,
    relationship_id: UUID | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Non-mutating read of engagement signals. Bounded."""
    _require_scope(creator_id, user_id)
    if limit < 1 or limit > 200:
        raise ValueError("limit must be 1..200")
    pool = await _pool()
    async with pool.acquire() as conn:
        if relationship_id is not None:
            rows = await conn.fetch(
                """
                SELECT * FROM v2_engagement_signals
                WHERE creator_id = $1 AND user_id = $2 AND relationship_id = $3
                ORDER BY last_observed_at DESC
                LIMIT $4
                """,
                creator_id,
                user_id,
                relationship_id,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM v2_engagement_signals
                WHERE creator_id = $1 AND user_id = $2
                ORDER BY last_observed_at DESC
                LIMIT $3
                """,
                creator_id,
                user_id,
                limit,
            )
        return [dict(r) for r in rows]


async def count_events(
    creator_id: int, user_id: int, event_types: list[str]
) -> int:
    """Non-mutating count of durable events by type (evidence derivation)."""
    _require_scope(creator_id, user_id)
    if not event_types:
        raise ValueError("event_types required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS n FROM v2_events
            WHERE creator_id = $1 AND user_id = $2 AND event_type = ANY($3)
            """,
            creator_id,
            user_id,
            event_types,
        )
        return int(row["n"])


async def count_current_facts(creator_id: int, user_id: int) -> int:
    """Non-mutating count of current facts (evidence derivation)."""
    _require_scope(creator_id, user_id)
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS n FROM v2_memory_facts
            WHERE creator_id = $1 AND user_id = $2 AND status = 'current'
            """,
            creator_id,
            user_id,
        )
        return int(row["n"])


async def count_episodes(
    creator_id: int, user_id: int, episode_types: list[str] | None = None
) -> int:
    """Non-mutating count of episodes, optionally filtered by type."""
    _require_scope(creator_id, user_id)
    pool = await _pool()
    async with pool.acquire() as conn:
        if episode_types:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS n FROM v2_memory_episodes
                WHERE creator_id = $1 AND user_id = $2 AND episode_type = ANY($3)
                """,
                creator_id,
                user_id,
                episode_types,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS n FROM v2_memory_episodes
                WHERE creator_id = $1 AND user_id = $2
                """,
                creator_id,
                user_id,
            )
        return int(row["n"])


async def set_fact_embedding(
    fact_id: UUID, creator_id: int, user_id: int, embedding: list[float]
) -> dict[str, Any]:
    """Store a fact embedding for hybrid retrieval. scoped update, no text."""
    _require_scope(creator_id, user_id)
    vector_literal = _to_vector_literal(embedding)
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE v2_memory_facts SET
                embedding = $4::vector,
                updated_at = NOW()
            WHERE id = $1 AND creator_id = $2 AND user_id = $3
            RETURNING id
            """,
            fact_id,
            creator_id,
            user_id,
            vector_literal,
        )
        if row is None:
            raise ValueError("fact not found in scope")
        return dict(row)


async def search_facts_semantic(
    creator_id: int,
    user_id: int,
    query_embedding: list[float],
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Cosine-nearest current facts. Null-embedding rows never match."""
    _require_scope(creator_id, user_id)
    vector_literal = _to_vector_literal(query_embedding)
    if limit < 1 or limit > 50:
        raise ValueError("limit must be 1..50")
    pool = await _pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, memory_key, value, category, confidence, importance,
                   1 - (embedding <=> $3::vector) AS similarity
            FROM v2_memory_facts
            WHERE creator_id = $1 AND user_id = $2
              AND status = 'current' AND embedding IS NOT NULL
            ORDER BY embedding <=> $3::vector
            LIMIT $4
            """,
            creator_id,
            user_id,
            vector_literal,
            limit,
        )
        return [dict(r) for r in rows]


async def record_intimate_signal(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    kind: str,
    signal: str,
    confidence: float,
    provenance: str,
    importance: str = "normal",
    source_event_id: str | None = None,
) -> dict[str, Any]:
    """Owner of v2_intimate_history. Abstract signals only, never verbatim.

    Idempotent on (relationship, kind, signal) while current: redelivery
    refreshes confidence instead of duplicating.
    """
    _require_scope(creator_id, user_id)
    if kind not in ("comfort", "preference", "boundary", "turnoff", "milestone"):
        raise ValueError("kind must be comfort|preference|boundary|turnoff|milestone")
    if not signal or not signal.strip() or len(signal) > 280:
        raise ValueError("signal must be a 1..280 char abstraction")
    if importance not in ("critical", "high", "normal", "low"):
        raise ValueError("importance must be critical|high|normal|low")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be in [0, 1]")
    if not provenance:
        raise ValueError("provenance required")
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO v2_intimate_history
                (creator_id, user_id, relationship_id, kind, signal,
                 confidence, importance, provenance, source_event_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (relationship_id, kind, signal) WHERE status = 'current'
            DO UPDATE SET
                confidence = EXCLUDED.confidence,
                importance = EXCLUDED.importance,
                updated_at = NOW()
            RETURNING *
            """,
            creator_id,
            user_id,
            relationship_id,
            kind,
            signal.strip(),
            confidence,
            importance,
            provenance,
            source_event_id,
        )
        return dict(row)


async def list_intimate_history(
    creator_id: int, user_id: int, relationship_id: UUID | None = None
) -> list[dict[str, Any]]:
    """Non-mutating read of current intimate continuity."""
    _require_scope(creator_id, user_id)
    pool = await _pool()
    async with pool.acquire() as conn:
        if relationship_id is not None:
            rows = await conn.fetch(
                """
                SELECT * FROM v2_intimate_history
                WHERE creator_id = $1 AND user_id = $2 AND relationship_id = $3
                  AND status = 'current'
                ORDER BY created_at
                """,
                creator_id,
                user_id,
                relationship_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM v2_intimate_history
                WHERE creator_id = $1 AND user_id = $2
                  AND status = 'current'
                ORDER BY created_at
                """,
                creator_id,
                user_id,
            )
        return [dict(r) for r in rows]
