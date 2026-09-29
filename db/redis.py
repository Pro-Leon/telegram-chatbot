"""Redis transport: streams, locks, debounce, dedup, DLQ (F7 wiring).

Inbound (`inbound_messages` / `llm_workers`) and send (`send_messages` /
`send_workers`) Streams with XAUTOCLAIM recovery; creator-scoped user
locks; atomic debounce windows with fence tokens; send-dedup lease
protocol; unknown-attempt + repair stores; DLQ with inspection + replay.

Contract: tests/test_redis_recovery.py (recovery), test_delivery_idempotency.py
(inbound dedup + send redelivery), test_turn_send_gate.py (turn claims),
test_p18_dedup_atomicity.py (leases + debounce + reconcile),
test_m2_debounce_atomicity.py (fence protocol), test_h4_batch2/3 (random
ids, unknown/repair stores), test_dlq_recovery.py (DLQ shape + replay),
test_p14/p16/p39 (creator scoping), test_m4/phase43b (persona cache).
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from typing import Any

import redis.asyncio as redis

from core.config import get_settings

logger = logging.getLogger("db.redis")

_settings = get_settings()

_client: redis.Redis | None = None

INBOUND_STREAM = "inbound_messages"
DRAFT_STREAM = "draft_messages"
SEND_STREAM = "send_messages"
DLQ_STREAM = "dead_letter_queue"

CONSUMER_GROUP = "llm_workers"
SEND_CONSUMER_GROUP = "send_workers"

SEND_DLQ_REASON_POST_SEND = "post_send_persistence_failed"
SEND_DLQ_REASON_SEND_ERROR = "send_error"
SEND_DLQ_REASON_UNKNOWN = "unknown_send_result"

MAX_SEND_DELIVERIES = 5
SEND_DEDUP_TTL = 3600
SEND_RANDOM_ID_TTL = 86400
UNKNOWN_STORE_TTL = 3600
REPLAY_LOCK_TTL = 30

_UNSET: Any = object()


async def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(
            _settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def ensure_consumer_group(consumer_name: str | None = None) -> None:
    """Create inbound + send consumer groups (idempotent, BUSYGROUP-safe)."""
    _ = consumer_name
    r = await get_redis()
    for stream, group in (
        (INBOUND_STREAM, CONSUMER_GROUP),
        (SEND_STREAM, SEND_CONSUMER_GROUP),
    ):
        try:
            await r.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise


# ── Inbound stream ────────────────────────────────────────────────────


INBOUND_RESERVE_TTL = 120
INBOUND_CONFIRM_TTL = 3600
INBOUND_CONTENT_WINDOW_TTL = 300


def _inbound_tg_key(user_id: Any, telegram_message_id: Any, creator_id: Any = None) -> str:
    return f"inbound_dedup:{creator_id}:{user_id}:{telegram_message_id}"


def _inbound_content_key(user_id: Any, content: Any) -> str:
    digest = hashlib.md5(f"{user_id}:{content}".encode()).hexdigest()
    return f"inbound:dedup:content:{digest}"


async def try_reserve_inbound(
    user_id: Any, telegram_message_id: Any, creator_id: Any = None
) -> str | None:
    """SET NX reservation on the tg dedup key. Winner gets a token."""
    r = await get_redis()
    token = secrets.token_hex(8)
    try:
        won = await r.set(
            _inbound_tg_key(user_id, telegram_message_id, creator_id),
            token,
            nx=True,
            ex=INBOUND_RESERVE_TTL,
        )
    except Exception:  # noqa: BLE001
        return None
    return token if won else None


async def get_inbound_dedup_value(
    user_id: Any, telegram_message_id: Any, creator_id: Any = None
) -> str | None:
    r = await get_redis()
    try:
        return await r.get(_inbound_tg_key(user_id, telegram_message_id, creator_id))
    except Exception:  # noqa: BLE001
        return None


async def is_inbound_duplicate(message_data: dict) -> bool:
    """True when tg reservation or content window already seen (fail-open False)."""
    try:
        r = await get_redis()
        user_id = message_data.get("user_id")
        tg = message_data.get("telegram_message_id")
        creator_id = message_data.get("creator_id")
        if tg not in (None, ""):
            if await r.exists(_inbound_tg_key(user_id, tg, creator_id)) == 1:
                return True
        content = message_data.get("content", "")
        if content:
            if await r.exists(_inbound_content_key(user_id, content)) == 1:
                return True
        return False
    except Exception:  # noqa: BLE001 — dedup never blocks ingestion
        return False


async def mark_inbound_dedup(message_data: dict) -> None:
    r = await get_redis()
    user_id = message_data.get("user_id")
    tg = message_data.get("telegram_message_id")
    creator_id = message_data.get("creator_id")
    if tg not in (None, ""):
        await r.set(_inbound_tg_key(user_id, tg, creator_id), "1", ex=INBOUND_CONFIRM_TTL)
    content = message_data.get("content", "")
    if content:
        await r.set(_inbound_content_key(user_id, content), "1", ex=INBOUND_CONTENT_WINDOW_TTL)


async def enqueue_inbound(message_data: dict) -> str:
    """Reserve → XADD → confirm. Returns stream id or 'duplicate:*'.

    creator_id is required (P1.4 fail-closed, no global fallback).
    tg reservation (SET NX token) wins exactly once per identity; the
    content window collapses same-text redeliveries under a new tg id.
    XADD failure releases the reservation and raises (never permanently
    suppressed). Confirm degrades to plain SETEX when Lua is unavailable.
    """
    creator_id = message_data.get("creator_id")
    if creator_id is None or (isinstance(creator_id, str) and not creator_id.strip()):
        raise ValueError("creator_id is required")
    r = await get_redis()
    user_id = message_data.get("user_id")
    tg = message_data.get("telegram_message_id")
    creator_id = message_data.get("creator_id")
    content = message_data.get("content", "")
    token = secrets.token_hex(8)
    tg_key = _inbound_tg_key(user_id, tg, creator_id)
    content_key = _inbound_content_key(user_id, content) if content else None
    if tg not in (None, ""):
        try:
            won = await r.set(tg_key, token, nx=True, ex=INBOUND_RESERVE_TTL)
        except Exception:  # noqa: BLE001
            won = False
        if not won:
            return f"duplicate:{tg}"
    won_content = False
    if content_key is not None:
        try:
            won_content = bool(
                await r.set(content_key, "1", nx=True, ex=INBOUND_CONTENT_WINDOW_TTL)
            )
        except Exception:  # noqa: BLE001
            won_content = True
        if not won_content:
            if tg not in (None, ""):
                try:
                    await r.delete(tg_key)
                except Exception:  # noqa: BLE001
                    pass
            return "duplicate:content"
    data = {str(k): ("" if v is None else str(v)) for k, v in message_data.items()}
    try:
        stream_id = await r.xadd(INBOUND_STREAM, data)
    except Exception:
        if tg not in (None, ""):
            try:
                await r.delete(tg_key)
            except Exception:  # noqa: BLE001
                pass
        if won_content and content_key is not None:
            try:
                await r.delete(content_key)
            except Exception:  # noqa: BLE001
                pass
        raise
    if tg not in (None, ""):
        try:
            result = await r.eval(_CONFIRM_DEDUP_LUA, 1, tg_key, token, INBOUND_CONFIRM_TTL)
            if result != 1:
                await r.setex(tg_key, INBOUND_CONFIRM_TTL, "1")
        except Exception:  # noqa: BLE001 — confirm unavailable; fallback persists
            try:
                await r.setex(tg_key, INBOUND_CONFIRM_TTL, "1")
            except Exception:  # noqa: BLE001
                pass
    return stream_id


async def read_inbound(
    consumer_name: str,
    count: int = 10,
    block_ms: int = 2000,
) -> list[tuple[str, list[tuple[str, dict]]]]:
    r = await get_redis()
    try:
        messages = await r.xreadgroup(
            CONSUMER_GROUP,
            consumer_name,
            {INBOUND_STREAM: ">"},
            count=count,
            block=block_ms,
        )
        return messages or []
    except redis.ResponseError:
        return []


async def ack_inbound(message_id: str) -> None:
    r = await get_redis()
    await r.xack(INBOUND_STREAM, CONSUMER_GROUP, message_id)


async def requeue_stalled_messages(
    consumer_name: str, idle_ms: int = 30000
) -> tuple[int, list[tuple[str, dict]]]:
    """XAUTOCLAIM idle inbound entries. Returns (count, [(id, fields)])."""
    r = await get_redis()
    try:
        result = await r.xautoclaim(
            name=INBOUND_STREAM,
            groupname=CONSUMER_GROUP,
            consumername=consumer_name,
            min_idle_time=idle_ms,
            start_id="0",
            count=10,
        )
    except (redis.ResponseError, IndexError):
        return 0, []
    entries = list(result[1]) if result and result[1] else []
    claimed = [(mid, dict(fields)) for mid, fields in entries]
    return len(claimed), claimed


# ── Send stream ───────────────────────────────────────────────────────


async def enqueue_send(
    payload: dict,
    dedup_id: str | None = None,
    generation_id: str | None = None,
    creator_id: int | None = None,
) -> str:
    """XADD send stream. creator_id required (kwarg or payload), fail-closed."""
    cid = creator_id
    if cid is None:
        cid = payload.get("creator_id")
    if cid is None or (isinstance(cid, str) and not cid.strip()):
        raise ValueError("creator_id is required")
    data = {str(k): ("" if v is None else str(v)) for k, v in payload.items()}
    data["creator_id"] = str(cid)
    if dedup_id is not None:
        data["dedup_id"] = str(dedup_id)
    if generation_id:
        data["generation_id"] = str(generation_id)
    r = await get_redis()
    return await r.xadd(SEND_STREAM, data)


async def read_send_messages(
    consumer_name: str,
    count: int = 10,
    block_ms: int = 2000,
) -> list[tuple[str, list[tuple[str, dict]]]]:
    r = await get_redis()
    try:
        messages = await r.xreadgroup(
            SEND_CONSUMER_GROUP,
            consumer_name,
            {SEND_STREAM: ">"},
            count=count,
            block=block_ms,
        )
        return messages or []
    except redis.ResponseError:
        return []


async def ack_send(message_id: str) -> None:
    r = await get_redis()
    await r.xack(SEND_STREAM, SEND_CONSUMER_GROUP, message_id)


async def requeue_stalled_send_messages(
    consumer_name: str, idle_ms: int = 30000
) -> tuple[int, list[tuple[str, dict]]]:
    """XAUTOCLAIM idle send entries; poison (>MAX) → DLQ + ACK. No auto-ACK."""
    r = await get_redis()
    try:
        result = await r.xautoclaim(
            name=SEND_STREAM,
            groupname=SEND_CONSUMER_GROUP,
            consumername=consumer_name,
            min_idle_time=idle_ms,
            start_id="0",
            count=10,
        )
    except (redis.ResponseError, IndexError):
        return 0, []
    claimed = list(result[1]) if result and result[1] else []
    if not claimed:
        return 0, []
    try:
        pending = await r.xpending_range(SEND_STREAM, SEND_CONSUMER_GROUP, "-", "+", len(claimed))
    except (redis.ResponseError, AttributeError):
        pending = []
    deliveries = {p.get("message_id"): int(p.get("times_delivered") or 0) for p in pending or []}
    live: list[tuple[str, dict]] = []
    for mid, fields in claimed:
        if deliveries.get(mid, 1) >= MAX_SEND_DELIVERIES:
            await move_send_to_dlq(mid, "max_redeliveries_exceeded", payload=dict(fields))
            await r.xack(SEND_STREAM, SEND_CONSUMER_GROUP, mid)
        else:
            live.append((mid, dict(fields)))
    return len(live), live


# ── DLQ ───────────────────────────────────────────────────────────────


def _dlq_record(
    message_id: str,
    stream: str,
    reason: str,
    payload: dict | None = None,
    worker_id: str | None = None,
) -> dict:
    record = {
        "message_id": message_id,
        "stream": stream,
        "reason": reason,
        "replay_count": "0",
        "failure_timestamp": int(time.time()),
    }
    if payload is not None:
        record["payload"] = json.dumps(payload)
    if worker_id is not None:
        record["worker_id"] = worker_id
    return record


async def move_to_dlq(
    message_id: str, reason: str, payload: dict | None = None, worker_id: str | None = None
) -> None:
    try:
        r = await get_redis()
    except Exception:  # noqa: BLE001 — Redis down; caller decides
        return
    try:
        await r.xadd(DLQ_STREAM, _dlq_record(message_id, "inbound", reason, payload, worker_id))
    except Exception:  # noqa: BLE001
        return
    try:
        await r.xack(INBOUND_STREAM, CONSUMER_GROUP, message_id)
    except Exception:  # noqa: BLE001
        pass


async def move_send_to_dlq(
    message_id: str, reason: str, payload: dict | None = None, worker_id: str | None = None
) -> bool:
    """DLQ a send entry then ACK it. False (never raises) when DLQ fails."""
    try:
        r = await get_redis()
    except Exception:  # noqa: BLE001
        return False
    try:
        await r.xadd(DLQ_STREAM, _dlq_record(message_id, "send", reason, payload, worker_id))
    except Exception:  # noqa: BLE001 — DLQ write failed; keep entry pending
        return False
    try:
        await r.xack(SEND_STREAM, SEND_CONSUMER_GROUP, message_id)
    except Exception:  # noqa: BLE001 — ACK best-effort after DLQ record
        return False
    return True


async def list_dlq_entries(stream_filter: str | None = None, count: int = 100) -> list[dict]:
    r = await get_redis()
    rows = await r.xrevrange(DLQ_STREAM, count=count) or []
    out = []
    for entry_id, fields in rows:
        record = {"entry_id": entry_id, **dict(fields)}
        if stream_filter is None or record.get("stream") == stream_filter:
            out.append(record)
    return out


async def get_dlq_entry(entry_id: str) -> dict | None:
    r = await get_redis()
    rows = await r.xrange(DLQ_STREAM, entry_id, entry_id) or []
    if not rows:
        return None
    mid, fields = rows[0]
    return {"entry_id": mid, **dict(fields)}


async def count_dlq_entries() -> int:
    r = await get_redis()
    info = await r.xinfo_stream(DLQ_STREAM) or {}
    try:
        return int(info.get("length", 0))
    except (TypeError, ValueError):
        return 0


async def delete_dlq_entry(entry_id: str) -> bool:
    r = await get_redis()
    try:
        removed = await r.xdel(DLQ_STREAM, entry_id)
    except Exception:  # noqa: BLE001
        return False
    return int(removed or 0) > 0


async def cleanup_expired_dlq_entries(
    retention_seconds: int = 86400, limit: int = 500
) -> int:
    """Delete DLQ entries older than retention (missing timestamps count as
    old). Bounded to `limit` entries per call. Returns deleted count."""
    r = await get_redis()
    try:
        rows = await r.xrange(DLQ_STREAM, count=limit) or []
    except Exception:  # noqa: BLE001
        return 0
    now = time.time()
    old = []
    for mid, fields in list(rows)[:limit]:
        try:
            age = now - int(fields.get("failure_timestamp"))
        except (TypeError, ValueError):
            age = None
        if age is None or age >= retention_seconds:
            old.append(mid)
    if not old:
        return 0
    try:
        await r.xdel(DLQ_STREAM, *old)
    except Exception:  # noqa: BLE001
        return 0
    return len(old)


async def _rotate_dlq_entry(r: Any, entry_id: str, fields: dict, replay_count: int) -> None:
    """Rotate a DLQ record: delete + re-add with replay_count+1 (streams are
    append-only; rotation keeps the bound enforceable without mutating)."""
    record = {str(k): str(v) for k, v in fields.items()}
    record["replay_count"] = str(replay_count + 1)
    try:
        await r.xdel(DLQ_STREAM, entry_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        await r.xadd(DLQ_STREAM, record)
    except Exception:  # noqa: BLE001
        pass


async def repair_post_send_delivery(
    dedup_id: str,
    creator_id: int,
    user_id: int,
    content: str,
    telegram_message_id: int | None,
    delivery_reservation_id: int | None = None,
    draft_content: str | None = None,
    generation_id: str | None = None,
) -> dict:
    """Repair-only post-send reconciliation (never resends on the wire).

    Persists the idempotent outbound row, confirms the delivered mark
    unless a foreign lease is in flight (never clobbers), and finalizes
    the vault reservation when one is attached. Returns step evidence.
    """
    from db.postgres import save_outbound_after_send

    steps: dict[str, Any] = {}
    try:
        value = await get_send_dedup_value(dedup_id, creator_id=creator_id)
    except Exception:  # noqa: BLE001
        value = None
    if isinstance(value, str) and value.startswith("reserved:"):
        steps["marker"] = "deferred_inflight"
    else:
        try:
            await mark_send_dedup(dedup_id, creator_id=creator_id)
            steps["marker"] = "marked"
        except Exception:  # noqa: BLE001
            steps["marker"] = "mark_failed"
    try:
        await save_outbound_after_send(
            user_id=int(user_id),
            content=content,
            draft_content=draft_content if draft_content is not None else content,
            telegram_message_id=telegram_message_id,
            creator_id=int(creator_id),
            generation_id=generation_id,
            dedup_id=dedup_id,
        )
        steps["saved"] = True
    except Exception:  # noqa: BLE001
        steps["saved"] = False
    if delivery_reservation_id is not None:
        try:
            from db.vault import finalize_delivery

            await finalize_delivery(delivery_reservation_id, telegram_message_id)
        except Exception:  # noqa: BLE001
            pass
    return {"steps": steps}


async def replay_dlq_entry(
    entry_id: str, max_replay_attempts: int = 3, force_resend: bool = False
) -> dict:
    """Re-enqueue one DLQ entry (lock-serialized, bounded attempts).

    send_error → resend preserving identity (rotation bounds retries).
    post_send persistence failure → repair-only (no second row, no resend).
    unknown results → refused unless force_resend (explicit operator act).
    """
    r = await get_redis()
    lock_key = f"dlq_replay_lock:{entry_id}"
    try:
        locked = await r.set(lock_key, "1", nx=True, ex=REPLAY_LOCK_TTL)
    except Exception:  # noqa: BLE001
        locked = False
    if not locked:
        return {"success": False, "error": "replay_in_progress", "replay_count": 0}
    try:
        entry = await get_dlq_entry(entry_id)
        if entry is None:
            return {
                "success": False,
                "error": "entry_not_found",
                "replay_count": 0,
                "new_message_id": None,
            }
        try:
            replay_count = int(entry.get("replay_count") or 0)
        except (TypeError, ValueError):
            replay_count = 0
        if replay_count >= max_replay_attempts:
            return {
                "success": False,
                "error": "max_replay_attempts_reached",
                "replay_count": replay_count,
                "new_message_id": None,
            }
        reason = entry.get("reason")
        stream = entry.get("stream")
        if "payload" not in entry:
            return {
                "success": False,
                "error": "missing_original_payload",
                "replay_count": replay_count,
                "new_message_id": None,
            }
        try:
            payload = json.loads(entry.get("payload") or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        fields = {k: v for k, v in entry.items() if k != "entry_id"}
        if reason is None:
            # Legacy record (pre-reason): replay permissively, rotate bound.
            try:
                if stream == "send":
                    new_id = await enqueue_send(
                        payload,
                        dedup_id=payload.get("dedup_id"),
                        generation_id=payload.get("generation_id"),
                        creator_id=payload.get("creator_id"),
                    )
                elif stream == "inbound":
                    new_id = await enqueue_inbound(payload)
                else:
                    return {
                        "success": False,
                        "error": f"unsupported_stream:{stream}",
                        "replay_count": replay_count,
                        "new_message_id": None,
                    }
            except Exception as exc:  # noqa: BLE001
                return {
                    "success": False,
                    "error": f"replay_failed:{exc.__class__.__name__}",
                    "replay_count": replay_count,
                    "new_message_id": None,
                }
            await _rotate_dlq_entry(r, entry_id, fields, replay_count)
            return {
                "success": True,
                "error": None,
                "replay_count": replay_count + 1,
                "new_message_id": new_id,
            }
        if reason == SEND_DLQ_REASON_UNKNOWN and not force_resend:
            return {
                "success": False,
                "error": "unknown_result_requires_operator_decision",
                "replay_count": replay_count,
                "new_message_id": None,
            }
        if reason == SEND_DLQ_REASON_POST_SEND:
            repaired = await repair_post_send_delivery(
                payload.get("dedup_id"),
                int(payload.get("creator_id")),
                int(payload.get("user_id") or payload.get("entity") or 0),
                payload.get("content", ""),
                payload.get("post_send_telegram_message_id"),
                delivery_reservation_id=payload.get("post_send_delivery_reservation_id"),
                draft_content=payload.get("draft_content"),
                generation_id=payload.get("generation_id"),
            )
            await _rotate_dlq_entry(r, entry_id, fields, replay_count)
            return {
                "success": True,
                "error": None,
                "replay_count": replay_count + 1,
                "new_message_id": None,
                "repaired": repaired,
            }
        if stream == "send":
            creator = payload.get("creator_id")
            if creator is None or (isinstance(creator, str) and not creator.strip()):
                return {
                    "success": False,
                    "error": "creator_id is required for send replay",
                    "replay_count": replay_count,
                    "new_message_id": None,
                }
            try:
                new_id = await enqueue_send(
                    payload,
                    dedup_id=payload.get("dedup_id"),
                    generation_id=payload.get("generation_id"),
                    creator_id=creator,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "success": False,
                    "error": f"replay_failed:{exc.__class__.__name__}",
                    "replay_count": replay_count,
                    "new_message_id": None,
                }
            await _rotate_dlq_entry(r, entry_id, fields, replay_count)
            return {
                "success": True,
                "error": None,
                "replay_count": replay_count + 1,
                "new_message_id": new_id,
            }
        if stream == "inbound":
            if not payload.get("creator_id") and reason == "creator_context_unavailable":
                try:
                    from commerce.single_creator import (
                        SingleCreatorStatus,
                        resolve_single_application_creator,
                    )

                    ctx = await resolve_single_application_creator()
                    if ctx.status is SingleCreatorStatus.READY and ctx.creator_id is not None:
                        payload = {**payload, "creator_id": str(ctx.creator_id)}
                    else:
                        return {
                            "success": False,
                            "error": "creator_context_unavailable",
                            "replay_count": replay_count,
                            "new_message_id": None,
                        }
                except Exception:  # noqa: BLE001
                    return {
                        "success": False,
                        "error": "creator_context_unavailable",
                        "replay_count": replay_count,
                        "new_message_id": None,
                    }
            try:
                new_id = await enqueue_inbound(payload)
            except Exception as exc:  # noqa: BLE001
                return {
                    "success": False,
                    "error": f"replay_failed:{exc.__class__.__name__}",
                    "replay_count": replay_count,
                    "new_message_id": None,
                }
            await _rotate_dlq_entry(r, entry_id, fields, replay_count)
            return {
                "success": True,
                "error": None,
                "replay_count": replay_count + 1,
                "new_message_id": new_id,
            }
        return {
            "success": False,
            "error": f"unsupported_stream:{stream}",
            "replay_count": replay_count,
            "new_message_id": None,
        }
    finally:
        try:
            await r.delete(lock_key)
        except Exception:  # noqa: BLE001
            pass


# ── Creator-scoped user locks ─────────────────────────────────────────


def _user_lock_key(user_id: int, creator_id: Any = _UNSET) -> str:
    if creator_id is _UNSET:
        return f"lock:user:{user_id}"
    if creator_id is None:
        raise ValueError("creator_id is required")
    return f"lock:creator:{creator_id}:user:{user_id}"


async def acquire_user_lock(user_id: int, ttl: int = 30, creator_id: Any = _UNSET) -> bool:
    """SET NX lock. Omitted creator → legacy global key; explicit None → ValueError."""
    r = await get_redis()
    result = await r.set(_user_lock_key(user_id, creator_id), "1", nx=True, ex=ttl)
    return result is True


async def release_user_lock(user_id: int, creator_id: Any = _UNSET) -> None:
    r = await get_redis()
    await r.delete(_user_lock_key(user_id, creator_id))


async def clear_all_user_locks() -> int:
    """Delete every user-lock key (global + creator-scoped). Startup hygiene."""
    r = await get_redis()
    keys: list[str] = []
    for pattern in ("lock:user:*", "lock:creator:*:user:*"):
        try:
            async for key in r.scan_iter(pattern):
                if key not in keys:
                    keys.append(key)
        except Exception:  # noqa: BLE001
            continue
    if not keys:
        return 0
    try:
        removed = await r.delete(*keys)
        return int(removed or 0)
    except Exception:  # noqa: BLE001
        return 0


# ── Debounce windows ──────────────────────────────────────────────────


def _debounce_key(user_id: int, creator_id: Any = _UNSET) -> str:
    if creator_id is _UNSET:
        return f"debounce:{user_id}"
    if creator_id is None:
        raise ValueError("creator_id is required")
    return f"debounce:creator:{creator_id}:user:{user_id}"


class DebounceConsumeError(RuntimeError):
    """Raised when the debounce consume Lua fails."""


_DEBOUNCE_CONSUME_LUA = (
    "if redis.call('GET', KEYS[1]) ~= ARGV[1] then return {'STALE', ''} end "
    "local msgs = redis.call('LRANGE', KEYS[2], 0, -1) "
    "redis.call('DEL', KEYS[2], KEYS[1], KEYS[3]) "
    "if #msgs == 0 then return {'EMPTY', ''} end "
    "return {'OK', cjson.encode(msgs)}"
)

_DEBOUNCE_RECOVER_LUA = (
    "if redis.call('EXISTS', KEYS[1]) == 1 then return {'BUSY', ''} end "
    "local msgs = redis.call('LRANGE', KEYS[3], 0, -1) "
    "if #msgs == 0 then return {'EMPTY', ''} end "
    "redis.call('DEL', KEYS[3], KEYS[2]) "
    "return {'OK', cjson.encode(msgs)}"
)


async def debounce_enqueue(
    user_id: int,
    content: str,
    message_data: dict,
    window_seconds: int = 3,
    creator_id: Any = _UNSET,
) -> str:
    """Buffer into the debounce window. Returns fence token (owner) or "".

    Same-telegram redelivery inside the window collapses to "". RPUSH
    failure propagates after releasing the elected lock.
    """
    base = _debounce_key(user_id, creator_id)
    lock_key, owner_key, list_key = f"{base}:lock", f"{base}:owner", f"{base}:messages"
    # Already ingested downstream: collapse (no new window, no buffer).
    try:
        if await is_inbound_duplicate(
            {"user_id": user_id, "telegram_message_id": message_data.get("telegram_message_id"), "content": content, "creator_id": creator_id if creator_id is not _UNSET else None}
        ):
            return ""
    except Exception:  # noqa: BLE001 — dedup check fail-open
        pass
    fence = secrets.token_hex(8)
    r = await get_redis()
    won = await r.set(lock_key, fence, nx=True, ex=window_seconds)
    if won:
        await r.set(owner_key, fence, ex=window_seconds)
    try:
        existing = await r.lrange(list_key, 0, -1)
    except Exception:  # noqa: BLE001
        existing = []
    tg = str(message_data.get("telegram_message_id", ""))
    if tg:
        for raw in existing or []:
            try:
                if json.loads(raw).get("telegram_message_id") == tg:
                    return ""
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
    try:
        await r.rpush(list_key, json.dumps(message_data))
    except Exception:
        try:
            if won:
                await r.delete(lock_key)
        except Exception:  # noqa: BLE001
            pass
        raise
    await r.expire(list_key, window_seconds + 300)
    return fence if won else ""


async def debounce_consume(user_id: int, creator_id: int, fence: str) -> tuple[str, list[dict]]:
    """Timer consume: ("ok", msgs) | ("empty", []) | ("stale", [])."""
    base = _debounce_key(user_id, creator_id)
    r = await get_redis()
    try:
        result = await r.eval(
            _DEBOUNCE_CONSUME_LUA, 3, f"{base}:owner", f"{base}:messages", f"{base}:lock", fence
        )
    except Exception as exc:
        raise DebounceConsumeError(str(exc)) from exc
    status, payload = result[0], result[1] if len(result) > 1 else ""
    if status == "OK":
        try:
            raw = json.loads(payload or "[]")
        except json.JSONDecodeError:
            raw = []
        msgs = []
        for item in raw:
            try:
                msgs.append(json.loads(item) if isinstance(item, str) else item)
            except json.JSONDecodeError:
                continue
        return "ok", msgs
    if status == "EMPTY":
        return "empty", []
    return "stale", []


async def get_debounced_messages(user_id: int, creator_id: Any = _UNSET) -> list[dict]:
    """Drain the debounce buffer (legacy direct drain)."""
    base = _debounce_key(user_id, creator_id)
    r = await get_redis()
    raw = await r.lrange(f"{base}:messages", 0, -1)
    await r.delete(f"{base}:messages")
    out = []
    for item in raw or []:
        try:
            out.append(json.loads(item))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


async def requeue_stalled_debounce(window_seconds: int = 3, max_keys: int = 10) -> int:
    """Claim orphaned debounce buffers (lock absent) into inbound. Returns count."""
    _ = window_seconds
    r = await get_redis()
    recovered = 0
    seen = 0
    try:
        iterator = r.scan_iter("debounce:*:messages")
    except Exception:  # noqa: BLE001
        return 0
    async for list_key in iterator:
        if seen >= max_keys:
            break
        seen += 1
        base = list_key[: -len(":messages")]
        try:
            result = await r.eval(
                _DEBOUNCE_RECOVER_LUA, 3, f"{base}:lock", f"{base}:owner", list_key
            )
        except Exception:  # noqa: BLE001
            continue
        status = result[0] if result else "BUSY"
        if status != "OK":
            continue
        # The Lua already popped the buffer server-side; delete explicitly
        # too (idempotent no-op live, observable for callers/tests).
        try:
            await r.delete(list_key)
        except Exception:  # noqa: BLE001
            pass
        try:
            raw = json.loads(result[1] if len(result) > 1 else "[]")
        except json.JSONDecodeError:
            raw = []
        msgs = []
        for item in raw or []:
            try:
                msgs.append(json.loads(item) if isinstance(item, str) else item)
            except json.JSONDecodeError:
                continue
        if not msgs:
            continue
        latest = msgs[-1]
        try:
            persona = await get_cached_user_persona(
                latest.get("user_id"), creator_id=latest.get("creator_id")
            )
            if persona is None:
                persona = await get_cached_default_persona(creator_id=latest.get("creator_id"))
            if persona and not latest.get("persona"):
                latest = {**latest, "persona": persona}
            await enqueue_inbound(latest)
            try:
                await mark_inbound_dedup(latest)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            continue
        recovered += 1
    return recovered


# ── Inbound gap reconciliation ────────────────────────────────────────


async def reconcile_inbound_gaps(lookback_seconds: int = 300, limit: int = 100) -> int:
    """Re-enqueue recent inbound DB rows missing from the stream (bounded)."""
    from db.postgres import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id, creator_id, content, telegram_message_id, created_at
            FROM messages
            WHERE direction = 'inbound'
              AND created_at > NOW() - ($1 || ' seconds')::INTERVAL
            ORDER BY created_at DESC
            LIMIT $2
            """,
            str(lookback_seconds),
            int(limit),
        )
    latest: dict[tuple, dict] = {}
    for row in rows or []:
        item = dict(row)
        key = (str(item.get("user_id")), str(item.get("creator_id")))
        if key not in latest:
            latest[key] = item
    requeued = 0
    for item in latest.values():
        user_id, creator_id = str(item.get("user_id")), item.get("creator_id")
        content, tg = item.get("content", ""), item.get("telegram_message_id")
        generation_id = hashlib.md5(f"{user_id}:{content}:{tg}".encode()).hexdigest()
        persona = None
        try:
            from db.postgres import get_default_persona, get_user_persona

            persona = await get_cached_user_persona(user_id, creator_id=creator_id)
            if persona is None:
                persona = await get_user_persona(int(user_id), creator_id=creator_id)
            if persona is None:
                persona = await get_cached_default_persona(creator_id=creator_id)
            if persona is None:
                persona = await get_default_persona(creator_id=creator_id)
        except Exception:  # noqa: BLE001 — persona best-effort
            persona = None
        payload = {
            "user_id": user_id,
            "content": content,
            "telegram_message_id": str(tg),
            "username": "",
            "first_name": "",
            "persona": persona or "",
            "generation_id": generation_id,
            "creator_id": str(creator_id),
        }
        try:
            result = await enqueue_inbound(payload)
        except Exception:  # noqa: BLE001
            continue
        if not str(result).startswith("duplicate:"):
            requeued += 1
    return requeued


# ── Send dedup lease protocol ─────────────────────────────────────────


def _send_dedup_key(dedup_id: str, creator_id: Any = None) -> str:
    if creator_id is None:
        return f"send_dedup:{dedup_id}"
    return f"send_dedup:{creator_id}:{dedup_id}"


async def is_send_duplicate(dedup_id: str, creator_id: Any = None) -> bool:
    r = await get_redis()
    return bool(await r.exists(_send_dedup_key(dedup_id, creator_id)))


async def get_send_dedup_value(dedup_id: str, creator_id: Any = None) -> str | None:
    r = await get_redis()
    try:
        value = await r.get(_send_dedup_key(dedup_id, creator_id))
    except Exception:  # noqa: BLE001
        return None
    return value


async def mark_send_dedup(dedup_id: str, creator_id: Any = None, ttl: int = SEND_DEDUP_TTL) -> None:
    r = await get_redis()
    await r.setex(_send_dedup_key(dedup_id, creator_id), ttl, "1")


async def try_reserve_send_dedup(
    dedup_id: str, creator_id: Any = None, ttl: int = SEND_DEDUP_TTL
) -> str | None:
    """SET NX lease. Winner gets 'reserved:{token}'; losers get None."""
    r = await get_redis()
    token = secrets.token_hex(8)
    try:
        won = await r.set(
            _send_dedup_key(dedup_id, creator_id), f"reserved:{token}", nx=True, ex=ttl
        )
    except Exception:  # noqa: BLE001
        return None
    return f"reserved:{token}" if won else None


_CONFIRM_DEDUP_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] "
    "then return redis.call('SETEX', KEYS[1], ARGV[2], '1') "
    "else return 0 end"
)

_RELEASE_DEDUP_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] "
    "then return redis.call('DEL', KEYS[1]) "
    "else return 0 end"
)


async def confirm_send_dedup(
    dedup_id: str, creator_id: Any = None, token: str | None = None
) -> bool:
    """Lease token → confirmed '1' (86400s delivered mark). Wrong token →
    False (value untouched). No token → force-confirm via SETEX."""
    r = await get_redis()
    if not token:
        try:
            await r.setex(_send_dedup_key(dedup_id, creator_id), 86400, "1")
            return True
        except Exception:  # noqa: BLE001
            return False
    try:
        result = await r.eval(
            _CONFIRM_DEDUP_LUA, 1, _send_dedup_key(dedup_id, creator_id), token, 86400
        )
    except Exception:  # noqa: BLE001
        return False
    return result == 1


async def release_send_dedup(
    dedup_id: str, creator_id: Any = None, token: str | None = None
) -> bool:
    """Release a held lease token. Wrong token → False."""
    if not token:
        return False
    r = await get_redis()
    try:
        result = await r.eval(_RELEASE_DEDUP_LUA, 1, _send_dedup_key(dedup_id, creator_id), token)
    except Exception:  # noqa: BLE001
        return False
    return result == 1


async def get_or_create_send_random_id(
    dedup_id: str | None, creator_id: int | None
) -> bytes | None:
    """Stable 16-byte random_id per (creator, dedup), hex-persisted."""
    if not dedup_id or not str(dedup_id).strip() or creator_id is None:
        return None
    key = f"send_random_id:{creator_id}:{dedup_id}"
    r = await get_redis()
    try:
        existing = await r.get(key)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(existing, str) and existing:
        try:
            return bytes.fromhex(existing)
        except ValueError:
            pass
    value = secrets.token_bytes(16)
    try:
        await r.set(key, value.hex(), nx=True, ex=SEND_RANDOM_ID_TTL)
        stored = await r.get(key)
        if isinstance(stored, str) and stored:
            return bytes.fromhex(stored)
        return value
    except Exception:  # noqa: BLE001
        return value


# ── Send rate limiting ────────────────────────────────────────────────


async def check_send_rate_limit(peer: str, max_per_minute: int = 20) -> bool:
    r = await get_redis()
    key = f"send_rate_limit:{peer}"
    try:
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 60)
        return int(count) <= max_per_minute
    except Exception:  # noqa: BLE001 — fail-open on Redis errors
        return True


async def get_send_rate_limit_wait(peer: str, max_per_minute: int = 20) -> int:
    """Seconds until the peer window resets, or 0 when under the limit."""
    r = await get_redis()
    try:
        count = await r.get(f"send_rate_limit:{peer}")
        if count is None or int(count) <= max_per_minute:
            return 0
        ttl = await r.ttl(f"send_rate_limit:{peer}")
    except Exception:  # noqa: BLE001
        return 0
    return max(0, int(ttl or 0))


# ── Unknown-attempt + repair stores ───────────────────────────────────


async def record_unknown_send_attempt(
    dedup_id: str | None,
    creator_id: int | None = None,
    generation_id: str | None = None,
    generation: str | None = None,
    error_type: str | None = None,
    error_detail: str | None = None,
    vault_reservation_ids: list | None = None,
) -> bool:
    if not dedup_id or creator_id is None:
        return False
    record = {
        "dedup_id": dedup_id,
        "creator_id": int(creator_id),
        "generation_id": generation_id or generation,
        "classification": "unknown",
        "error_type": error_type,
        "error_detail": error_detail,
        "vault_reservation_ids": list(vault_reservation_ids or []),
        "attempt_timestamp": int(time.time()),
    }
    r = await get_redis()
    await r.setex(f"send_unknown:{creator_id}:{dedup_id}", UNKNOWN_STORE_TTL, json.dumps(record))
    return True


async def get_unknown_send_attempt(
    dedup_id: str | None, creator_id: int | None = None
) -> dict | None:
    if not dedup_id or creator_id is None:
        return None
    r = await get_redis()
    try:
        raw = await r.get(f"send_unknown:{creator_id}:{dedup_id}")
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def clear_unknown_send_attempt(dedup_id: str | None, creator_id: int | None = None) -> bool:
    if not dedup_id or creator_id is None:
        return False
    r = await get_redis()
    return bool(await r.delete(f"send_unknown:{creator_id}:{dedup_id}"))


async def list_unknown_vault_reservation_ids(limit: int = 200) -> list[int]:
    r = await get_redis()
    ids: list[int] = []
    try:
        iterator = r.scan_iter("send_unknown:*")
    except Exception:  # noqa: BLE001
        return []
    async for key in iterator:
        if len(ids) >= limit:
            break
        try:
            raw = await r.get(key)
            record = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
        for vid in record.get("vault_reservation_ids") or []:
            try:
                candidate = int(vid)
            except (TypeError, ValueError):
                continue
            if candidate not in ids:
                ids.append(candidate)
            if len(ids) >= limit:
                break
    return ids


async def record_send_repair_needed(
    dedup_id: str | None,
    creator_id: int | None = None,
    generation_id: str | None = None,
    reason: str | None = None,
    telegram_message_id: int | None = None,
    detail: str | None = None,
) -> bool:
    if not dedup_id or creator_id is None:
        return False
    record = {
        "dedup_id": dedup_id,
        "creator_id": int(creator_id),
        "generation_id": generation_id,
        "reason": reason,
        "telegram_message_id": telegram_message_id,
        "attempt_timestamp": int(time.time()),
    }
    if detail is not None:
        record["detail"] = detail
    r = await get_redis()
    await r.setex(f"send_repair:{creator_id}:{dedup_id}", UNKNOWN_STORE_TTL, json.dumps(record))
    return True


async def get_send_repair_needed(
    dedup_id: str | None, creator_id: int | None = None
) -> dict | None:
    if not dedup_id or creator_id is None:
        return None
    r = await get_redis()
    try:
        raw = await r.get(f"send_repair:{creator_id}:{dedup_id}")
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def clear_send_repair_needed(dedup_id: str | None, creator_id: int | None = None) -> bool:
    if not dedup_id or creator_id is None:
        return False
    r = await get_redis()
    return bool(await r.delete(f"send_repair:{creator_id}:{dedup_id}"))


# ── Turn-send gate ────────────────────────────────────────────────────


def _turn_send_key(creator_id: Any, user_id: Any, telegram_message_id: Any) -> str:
    return f"turn_send:{creator_id}:{user_id}:{telegram_message_id}"


async def try_claim_turn_send(creator_id: Any, user_id: Any, telegram_message_id: Any) -> bool:
    """First claim per (creator, user, tg) wins; missing identity / Redis
    outage proceeds (fail-open — never lost-send)."""
    if creator_id is None or user_id is None or telegram_message_id in (None, 0, "0", ""):
        return True
    r = await get_redis()
    try:
        won = await r.set(
            _turn_send_key(creator_id, user_id, telegram_message_id), "1", nx=True, ex=86400
        )
    except Exception:  # noqa: BLE001
        return True
    return bool(won)


# ── Persona cache ─────────────────────────────────────────────────────


def _persona_user_key(user_id: Any, creator_id: Any = None) -> str:
    if creator_id is None:
        return f"persona:{user_id}"
    return f"persona:{creator_id}:{user_id}"


async def get_cached_user_persona(user_id: Any, creator_id: Any = None) -> str | None:
    r = await get_redis()
    try:
        return await r.get(_persona_user_key(user_id, creator_id))
    except Exception:  # noqa: BLE001
        return None


async def cache_user_persona(user_id: Any, persona_text: str, creator_id: Any = None) -> None:
    r = await get_redis()
    try:
        await r.setex(_persona_user_key(user_id, creator_id), 600, persona_text)
    except Exception:  # noqa: BLE001
        pass


def _default_persona_key(creator_id: Any = None) -> str:
    if creator_id is None:
        return "persona:default"
    return f"persona:creator:{creator_id}:default"


async def get_cached_default_persona(creator_id: Any = None) -> str | None:
    r = await get_redis()
    try:
        return await r.get(_default_persona_key(creator_id))
    except Exception:  # noqa: BLE001
        return None


async def cache_default_persona(persona_text: str, creator_id: Any = None) -> None:
    r = await get_redis()
    try:
        await r.setex(_default_persona_key(creator_id), 600, persona_text)
    except Exception:  # noqa: BLE001
        pass


async def cache_creator_persona(creator_id: int, persona: dict) -> None:
    r = await get_redis()
    try:
        await r.setex(f"persona:creator:{creator_id}", 600, json.dumps(persona))
    except Exception:  # noqa: BLE001
        pass


async def get_cached_creator_persona(creator_id: int) -> dict | None:
    r = await get_redis()
    try:
        raw = await r.get(f"persona:creator:{creator_id}")
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def invalidate_persona_cache(user_id: Any = None, creator_id: Any = None) -> None:
    """Creator-scoped invalidation; legacy user-only call keeps scoped keys."""
    r = await get_redis()
    try:
        if creator_id is not None and user_id is not None:
            await r.delete(f"persona:{creator_id}:{user_id}")
        elif creator_id is not None:
            keys = [f"persona:creator:{creator_id}", f"persona:creator:{creator_id}:default"]
            try:
                async for key in r.scan_iter(f"persona:{creator_id}:*"):
                    keys.append(key)
            except Exception:  # noqa: BLE001
                pass
            if keys:
                await r.delete(*keys)
        elif user_id is not None:
            await r.delete(f"persona:{user_id}")
    except Exception:  # noqa: BLE001
        pass


# ── Auto-reply switch ─────────────────────────────────────────────────


async def is_auto_reply_enabled() -> bool:
    r = await get_redis()
    try:
        value = await r.get("setting:auto_reply")
    except Exception:  # noqa: BLE001
        return True
    if value is None:
        return True
    return str(value).lower() not in ("0", "false", "no", "off")


async def set_auto_reply_enabled(enabled: bool) -> None:
    r = await get_redis()
    await r.set("setting:auto_reply", "1" if enabled else "0")


# ── Legacy context helpers (unchanged behavior) ───────────────────────


async def cache_user_context(user_id: int, context: dict, ttl: int = 300) -> None:
    r = await get_redis()
    await r.setex(f"user_context:{user_id}", ttl, json.dumps(context))


async def get_cached_context(user_id: int) -> dict | None:
    r = await get_redis()
    data = await r.get(f"user_context:{user_id}")
    if data:
        return json.loads(data)
    return None


async def invalidate_context_cache(user_id: int) -> None:
    r = await get_redis()
    await r.delete(f"user_context:{user_id}")


async def check_rate_limit(user_id: int, max_per_minute: int = 20) -> bool:
    key = f"rate_limit:{user_id}"
    r = await get_redis()
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, 60)
    return count <= max_per_minute
