"""Live operation panel -- creator-scoped bridge queries."""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
from db.postgres import get_pool

router = APIRouter()

async def _resolve_creator_or_403():
    ctx = await resolve_single_application_creator()
    if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
        return None
    return ctx.creator_id

@router.get("/api/live/fans")
async def api_live_fans(
    window_minutes: int = Query(default=15, ge=1, le=1440),
    auth: dict = Depends(require_auth),
):
    """Creator-scoped active fans: fans with inbound message within window."""
    creator_id = await _resolve_creator_or_403()
    if creator_id is None:
        return JSONResponse({"error": "Creator not available"}, status_code=503)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT COUNT(DISTINCT user_id) as cnt
            FROM generation_telemetry
            WHERE creator_id = $1 AND created_at > NOW() - ($2 || ' minutes')::interval
            """,
            creator_id,
            str(window_minutes),
        )
        active_via_telemetry = rows[0]["cnt"] if rows else 0
        rows2 = await conn.fetch(
            """
            SELECT COUNT(DISTINCT m.user_id) as cnt
            FROM messages m
            JOIN commerce_offers co ON co.user_id = m.user_id AND co.creator_id = $1
            WHERE m.direction = 'inbound' AND m.created_at > NOW() - ($2 || ' minutes')::interval
            """,
            creator_id,
            str(window_minutes),
        )
        active_via_messages = rows2[0]["cnt"] if rows2 else 0
        active = max(active_via_telemetry, active_via_messages)
        return JSONResponse({"creator_id": creator_id, "window_minutes": window_minutes, "active_count": active})

@router.get("/api/live/overview")
async def api_live_overview(auth: dict = Depends(require_auth)):
    """Creator-scoped live operation panel."""
    creator_id = await _resolve_creator_or_403()
    if creator_id is None:
        return JSONResponse({"error": "Creator not available"}, status_code=503)
    pool = await get_pool()
    async with pool.acquire() as conn:
        active_rows = await conn.fetch(
            "SELECT COUNT(DISTINCT user_id) as cnt FROM generation_telemetry WHERE creator_id=$1 AND created_at > NOW() - interval '15 minutes'",
            creator_id,
        )
        active = active_rows[0]["cnt"] if active_rows else 0
        gen_rows = await conn.fetch(
            "SELECT COUNT(*) as cnt FROM generation_telemetry WHERE creator_id=$1 AND created_at > NOW() - interval '1 minute'",
            creator_id,
        )
        generating = gen_rows[0]["cnt"] if gen_rows else 0
        offer_rows = await conn.fetch(
            "SELECT COUNT(*) as cnt FROM commerce_offers WHERE creator_id=$1 AND state IN ('pending','clicked')",
            creator_id,
        )
        offers_pending = offer_rows[0]["cnt"] if offer_rows else 0
        sales_rows = await conn.fetch(
            "SELECT COUNT(*) as cnt FROM commerce_offers WHERE creator_id=$1 AND state='purchased'",
            creator_id,
        )
        sales = sales_rows[0]["cnt"] if sales_rows else 0
        rev_rows = await conn.fetch(
            "SELECT COALESCE(SUM(seller_earning*100),0) as total FROM fangate_transactions WHERE creator_id=$1 AND event_type='dropfans_sale'",
            creator_id,
        )
        revenue_cents = int(rev_rows[0]["total"]) if rev_rows else 0
        after_rows = await conn.fetch(
            "SELECT COUNT(*) as cnt FROM commerce_offers WHERE creator_id=$1 AND aftercare_status='pending'",
            creator_id,
        )
        aftercare = after_rows[0]["cnt"] if after_rows else 0
        fail_rows = await conn.fetch(
            "SELECT COUNT(*) as cnt FROM generation_telemetry WHERE creator_id=$1 AND success=false AND created_at > NOW() - interval '1 hour'",
            creator_id,
        )
        failures = fail_rows[0]["cnt"] if fail_rows else 0
        return JSONResponse({
            "creator_id": creator_id,
            "active": active,
            "generating": generating,
            "offers_pending": offers_pending,
            "sales": sales,
            "revenue_cents": revenue_cents,
            "revenue_display": f"${revenue_cents/100:.2f}",
            "aftercare": aftercare,
            "failures": failures,
        })

@router.get("/api/generation/{generation_id}/stage")
async def api_generation_stage(generation_id: str, auth: dict = Depends(require_auth)):
    """Derive execution stage for a generation."""
    from core.execution_stage import derive_stage
    creator_id = await _resolve_creator_or_403()
    if creator_id is None:
        return JSONResponse({"error": "Creator not available"}, status_code=503)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tel = await conn.fetchrow("SELECT * FROM generation_telemetry WHERE generation_id=$1 AND creator_id=$2", generation_id, creator_id)
        if not tel:
            return JSONResponse({"error": "Generation not found"}, status_code=404)
        has_sent = tel["routing_decision"] == "auto_approved"
        has_failed = not tel["success"]
        stage = derive_stage(has_failed=has_failed, has_sent=has_sent, has_scoring=tel["scoring_score"] is not None, has_qwen=True, has_generation_started=True)
        return JSONResponse({
            "generation_id": generation_id,
            "creator_id": creator_id,
            "stage": stage.value,
            "telemetry": dict(tel),
        })


def _degraded_quality(creator_id: int, window_hours: int) -> dict:
    """Zero-filled quality payload (DB/gauge outage must never 500)."""
    return {
        "creator_id": creator_id,
        "window_hours": window_hours,
        "turns": 0,
        "prompt_echo_turns": 0,
        "safety_failures": 0,
        "quality_failures": 0,
        "auto_approved": 0,
        "operator_queued": 0,
        "advisory_handoffs": 0,
        "corroborated_handoffs": 0,
        "unrecorded_verdict": 0,
        "veto_split": [],
        "repeat_pairs": 0,
        "outbound_turns": 0,
        "dedup_hits": 0,
        "already_executed": 0,
        "inbound_redeliveries": 0,
        "degraded": True,
    }


@router.get("/api/live/quality")
async def api_live_quality(
    window_hours: int = Query(default=24, ge=1, le=168),
    auth: dict = Depends(require_auth),
):
    """Phase 4.2 read-only quality board: violation/repeat/dedup + 4.1 verdict split.

    Read-only SELECTs over generation_telemetry/messages (existing indexes)
    plus persisted per-turn counters. NULL verdict rows bucket as
    ``unrecorded`` so counts always sum to turns. Degraded zeros on outage.
    """
    creator_id = await _resolve_creator_or_403()
    if creator_id is None:
        return JSONResponse({"error": "Creator not available"}, status_code=503)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Q1 (+4.1): violation counts + verdict flags, one scan.
            vrow = await conn.fetchrow(
                """
                SELECT COUNT(*) AS turns,
                    COUNT(*) FILTER (
                        WHERE scoring_flags::jsonb ? 'prompt_echo'
                           OR conversational_validation_flags::jsonb ? 'prompt_echo'
                           OR roleplay_validation_flags::jsonb ? 'prompt_echo'
                    ) AS prompt_echo_turns,
                    COUNT(*) FILTER (WHERE validation_outcome LIKE '%safety%') AS safety_failures,
                    COUNT(*) FILTER (WHERE validation_outcome LIKE '%quality%') AS quality_failures,
                    COUNT(*) FILTER (WHERE routing_decision = 'auto_approved') AS auto_approved,
                    COUNT(*) FILTER (WHERE routing_decision = 'operator_queued') AS operator_queued,
                    COUNT(*) FILTER (WHERE advisory_handoff IS TRUE) AS advisory_handoffs,
                    COUNT(*) FILTER (WHERE corroborated_handoff IS TRUE) AS corroborated_handoffs,
                    COUNT(*) FILTER (WHERE routing_veto IS NULL) AS unrecorded_verdict
                FROM generation_telemetry
                WHERE creator_id = $1 AND created_at > NOW() - ($2 || ' hours')::interval
                """,
                creator_id,
                str(window_hours),
            )
            # 4.1 veto distribution (NULL -> unrecorded bucket).
            veto_rows = await conn.fetch(
                """
                SELECT COALESCE(routing_veto, 'unrecorded') AS veto, COUNT(*) AS turns
                FROM generation_telemetry
                WHERE creator_id = $1 AND created_at > NOW() - ($2 || ' hours')::interval
                GROUP BY 1 ORDER BY turns DESC
                """,
                creator_id,
                str(window_hours),
            )
            # Q2: repeat back-to-back normalized-equal outbound pairs.
            rrow = await conn.fetchrow(
                """
                WITH norm AS (
                    SELECT lower(regexp_replace(content, '\\s+', ' ', 'g')) AS ntext,
                        lag(lower(regexp_replace(content, '\\s+', ' ', 'g')))
                            OVER (PARTITION BY creator_id, user_id ORDER BY created_at) AS prev_ntext
                    FROM messages
                    WHERE direction = 'outbound' AND creator_id = $1
                      AND created_at > NOW() - ($2 || ' hours')::interval
                )
                SELECT COUNT(*) AS outbound_turns,
                    COUNT(*) FILTER (WHERE ntext = prev_ntext) AS repeat_pairs
                FROM norm
                """,
                creator_id,
                str(window_hours),
            )
            # Q3: dedup-hit persisted counters.
            drow = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(duplicate_send_suppressed_count), 0) AS dedup_hits,
                    COALESCE(SUM(already_executed_count), 0) AS already_executed,
                    COALESCE(SUM(inbound_redelivery_count), 0) AS inbound_redeliveries,
                    COUNT(*) AS turns
                FROM generation_telemetry
                WHERE creator_id = $1 AND created_at > NOW() - ($2 || ' hours')::interval
                """,
                creator_id,
                str(window_hours),
            )
    except Exception:
        return JSONResponse(_degraded_quality(creator_id, window_hours))
    payload = _degraded_quality(creator_id, window_hours)
    payload["degraded"] = False
    if vrow:
        for key in (
            "turns",
            "prompt_echo_turns",
            "safety_failures",
            "quality_failures",
            "auto_approved",
            "operator_queued",
            "advisory_handoffs",
            "corroborated_handoffs",
            "unrecorded_verdict",
        ):
            try:
                payload[key] = int(vrow[key] or 0)
            except (TypeError, ValueError):
                pass
    payload["veto_split"] = [
        {"veto": r["veto"], "turns": int(r["turns"] or 0)} for r in (veto_rows or [])
    ]
    if rrow:
        for key in ("outbound_turns", "repeat_pairs"):
            try:
                payload[key] = int(rrow[key] or 0)
            except (TypeError, ValueError):
                pass
    if drow:
        for key in ("dedup_hits", "already_executed", "inbound_redeliveries"):
            try:
                payload[key] = int(drow[key] or 0)
            except (TypeError, ValueError):
                pass
    return JSONResponse(payload)


@router.get("/api/live/latency")
async def api_live_latency(
    window_hours: int = Query(default=24, ge=1, le=168),
    auth: dict = Depends(require_auth),
):
    """Phase 4.2 read-only latency board: p50/p95 of total_e2e_latency_ms.

    Canary precedent (PERCENTILE_CONT); creator-scoped + windowed. Empty
    window returns nulls with turns=0. Degraded zeros on outage.
    """
    creator_id = await _resolve_creator_or_403()
    if creator_id is None:
        return JSONResponse({"error": "Creator not available"}, status_code=503)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS turns,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_e2e_latency_ms) AS p50,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY total_e2e_latency_ms) AS p95
                FROM generation_telemetry
                WHERE creator_id = $1 AND created_at > NOW() - ($2 || ' hours')::interval
                  AND total_e2e_latency_ms IS NOT NULL
                """,
                creator_id,
                str(window_hours),
            )
    except Exception:
        return JSONResponse(
            {"creator_id": creator_id, "window_hours": window_hours, "turns": 0, "p50_ms": None, "p95_ms": None, "degraded": True}
        )
    turns = 0
    try:
        turns = int((row["turns"] if row else 0) or 0)
    except (TypeError, ValueError):
        turns = 0
    def _ms(value):
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    return JSONResponse(
        {
            "creator_id": creator_id,
            "window_hours": window_hours,
            "turns": turns,
            "p50_ms": _ms(row["p50"]) if row else None,
            "p95_ms": _ms(row["p95"]) if row else None,
            "degraded": False,
        }
    )


@router.get("/api/live/streams")
async def api_live_streams(auth: dict = Depends(require_auth)):
    """Phase 4.2 read-only stream board: XPENDING gauges + scheduler lag.

    Uses existing fail-open gauges (db/redis.py) and the followups lag
    pattern. Gauge/DB error degrades to zeros, never 500.
    """
    from datetime import datetime, timezone

    creator_id = await _resolve_creator_or_403()
    if creator_id is None:
        return JSONResponse({"error": "Creator not available"}, status_code=503)
    try:
        from db import redis as _redis_mod

        send_pending = await _redis_mod.get_send_pending_count()
        inbound_pending = await _redis_mod.get_inbound_pending_count()
        send_length = await _redis_mod.get_send_stream_length()
        inbound_length = await _redis_mod.get_inbound_stream_length()
        try:
            dlq_age = await _redis_mod.get_dlq_age_seconds()
        except Exception:
            dlq_age = None
    except Exception:
        send_pending = inbound_pending = send_length = inbound_length = 0
        dlq_age = None
    lag_seconds: float = 0
    scheduler_pending = 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            lag_row = await conn.fetchrow(
                "SELECT MIN(execute_at) AS oldest_pending, COUNT(*) AS pending_count "
                "FROM scheduled_messages "
                "WHERE status = 'pending' AND creator_id = $1",
                creator_id,
            )
        if lag_row and lag_row["oldest_pending"] is not None:
            oldest = lag_row["oldest_pending"]
            now = datetime.now(timezone.utc)
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)
            lag_seconds = round(max(0, (now - oldest).total_seconds()), 1)
            try:
                scheduler_pending = int(lag_row["pending_count"] or 0)
            except (TypeError, ValueError):
                scheduler_pending = 0
    except Exception:
        lag_seconds = 0
        scheduler_pending = 0
    return JSONResponse(
        {
            "creator_id": creator_id,
            "send_pending": int(send_pending or 0),
            "inbound_pending": int(inbound_pending or 0),
            "send_stream_length": int(send_length or 0),
            "inbound_stream_length": int(inbound_length or 0),
            "dlq_age_seconds": dlq_age,
            "scheduler_pending": scheduler_pending,
            "scheduler_lag_seconds": lag_seconds,
            "degraded": False,
        }
    )
