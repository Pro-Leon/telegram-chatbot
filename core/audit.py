"""M7 (B1+B5) — normalized durable audit event contract + server-derived actors.

Stage A findings addressed: M7-03 (weak operator identity), M7-04 (no durable
audit record), M7-05 (overwrite-only edit history), M7-14 (split failure
semantics).

Design notes (invariants preserved):

- Audit writing is NEVER request-critical or send-critical. ``record_audit``
  catches every exception and returns ``False`` instead of raising.
- No Redis idempotency state is created for the audit writer.
- No raw message content is stored in the audit record — only SHA-256 hashes
  and row/stream identifiers. Pub/Sub behavior is unchanged (callers keep
  publishing the existing Phase 1 events separately).
- Client-supplied ``actor_type``/``actor_id`` payload keys are never trusted.
  Use :func:`strip_spoofed_actor` at worker boundaries.
- Durable store: ``operator_audit_events`` (migration
  ``20260922000000_m7_operator_audit``). On pre-migration databases the writer
  falls back to a structured log line and returns ``False`` — business logic
  is unaffected.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger("core.audit")

# ── Closed vocabularies ──────────────────────────────────────────────────

EVENT_TYPES = frozenset({
    "intent",
    "enqueue",
    "claim",
    "dequeue",
    "attempt",
    "success",
    "failure",
    "retry",
    "cancel",
    "reject",
    "edit",
    "approve",
    "stale_suppressed",
    "cancel_raced",
    "suppress",
    "recover",
    "actor_spoof_attempt",
    # Phase 4.1: verdict/quality signal events (hash-only payloads, same shape)
    "auto_approved",
    "prompt_echo",
    "repeat",
    # Flush-cycle skip marker for untouched pending rows (send_worker).
    "awaiting_review",
})

ACTOR_TYPES = frozenset({
    "human",
    "scheduler",
    "ai",
    "worker",
    "system",
    "migration",
    "unknown",
})

# Keys that must never be trusted from inbound client/worker payloads.
SPOOFABLE_ACTOR_KEYS = ("actor_type", "actor_id")


# ── Hashing (privacy: hashes only, never raw content) ───────────────────

def content_hash(text: str | None) -> str | None:
    """SHA-256 hex digest of outbound text, or ``None`` for missing input."""
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(text: str | None) -> str | None:
    """16-char prefix matching the M6 ``queue_item:{id}:{sha16}`` convention."""
    full = content_hash(text)
    return full[:16] if full else None


# ── B5: server-derived actor constructors ────────────────────────────────

def actor_from_auth(auth: Any) -> dict[str, str | None]:
    """Derive a human actor from the dashboard auth session (trust boundary).

    The session carries a self-asserted username under a shared password
    (M7-03), so the actor is ``human`` with that username — carried for
    provenance, never treated as authenticated role identity.
    """
    username = None
    try:
        if isinstance(auth, dict):
            username = auth.get("username")
    except Exception:
        username = None
    if username:
        return {"actor_type": "human", "actor_id": str(username)}
    return {"actor_type": "unknown", "actor_id": "missing-auth"}


def actor_worker(worker_id: str) -> dict[str, str | None]:
    return {"actor_type": "worker", "actor_id": str(worker_id)}


def actor_scheduler(worker_id: str) -> dict[str, str | None]:
    return {"actor_type": "scheduler", "actor_id": str(worker_id)}


def actor_ai(detail: str = "llm") -> dict[str, str | None]:
    return {"actor_type": "ai", "actor_id": detail}


def actor_system(name: str = "system") -> dict[str, str | None]:
    return {"actor_type": "system", "actor_id": str(name)}


def actor_migration(reason: str = "backfill") -> dict[str, str | None]:
    return {"actor_type": "migration", "actor_id": str(reason)}


def actor_unknown(reason: str = "legacy") -> dict[str, str | None]:
    return {"actor_type": "unknown", "actor_id": str(reason)}


def validate_actor(actor_type: str | None, actor_id: Any) -> dict[str, str | None]:
    """Normalize an actor pair; unknown/invalid input becomes explicit unknown."""
    if actor_type not in ACTOR_TYPES:
        return {"actor_type": "unknown", "actor_id": "invalid-actor-type"}
    if actor_id is None or (isinstance(actor_id, str) and not actor_id.strip()):
        return {"actor_type": actor_type, "actor_id": "unspecified"}
    return {"actor_type": actor_type, "actor_id": str(actor_id)}


def strip_spoofed_actor(payload: dict) -> tuple[dict, bool]:
    """Remove client-supplied actor keys from an inbound stream payload.

    Returns ``(cleaned_payload, spoof_detected)``. Workers must call this on
    every inbound send-stream entry and overwrite identity with the
    server-derived actor (B5). The spoof itself is auditable via the
    ``actor_spoof_attempt`` event type.
    """
    if not isinstance(payload, dict):
        return {}, False
    spoofed = any(k in payload for k in SPOOFABLE_ACTOR_KEYS)
    if not spoofed:
        return payload, False
    cleaned = {k: v for k, v in payload.items() if k not in SPOOFABLE_ACTOR_KEYS}
    return cleaned, True


# ── B1: normalized event constructor (pure, no I/O) ─────────────────────

_REQUIRED_FOR_RECORD = ("event_type", "actor_type", "creator_id", "action")


def build_audit_event(
    *,
    event_type: str,
    actor_type: str,
    actor_id: Any = None,
    creator_id: int | None = None,
    user_id: int | None = None,
    chat_id: int | None = None,
    action: str | None = None,
    content: str | None = None,
    content_original: str | None = None,
    content_final: str | None = None,
    asset_kind: str | None = None,
    asset_id: Any = None,
    asset_ref: str | None = None,
    generation_id: str | None = None,
    dedup_id: str | None = None,
    queue_id: int | None = None,
    schedule_id: int | None = None,
    state_before: str | None = None,
    state_after: str | None = None,
    result: str | None = None,
    error: str | None = None,
    attempt: int = 0,
    lease_token: str | None = None,
    telegram_message_id: int | None = None,
) -> dict[str, Any]:
    """Build a normalized audit event dict.

    Raw ``content*`` inputs are hashed immediately and never retained.
    Raises :class:`ValueError` on unknown ``event_type``/``actor_type`` or a
    missing/invalid ``creator_id``/``action``.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown audit event_type: {event_type!r}")
    actor = validate_actor(actor_type, actor_id)
    # validate_actor never returns an invalid type, but a caller-supplied
    # actor_type outside the vocabulary must be rejected, not relabeled.
    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"unknown audit actor_type: {actor_type!r}")
    if creator_id is None or not str(creator_id).strip().isdigit():
        raise ValueError("creator_id is required for audit events")
    if not action or not str(action).strip():
        raise ValueError("action (source route/subsystem) is required")

    now_ms = int(time.time() * 1000)
    event: dict[str, Any] = {
        "audit_id": str(uuid.uuid4()),
        "event_type": event_type,
        "actor_type": actor["actor_type"],
        "actor_id": actor["actor_id"],
        "creator_id": int(str(creator_id).strip()),
        "user_id": int(user_id) if user_id is not None else None,
        "chat_id": int(chat_id) if chat_id is not None else None,
        "action": str(action),
        "content_hash": content_hash(content) if content is not None else None,
        "content_hash_original": content_hash(content_original)
        if content_original is not None
        else None,
        "content_hash_final": content_hash(content_final)
        if content_final is not None
        else None,
        "asset_kind": asset_kind,
        "asset_id": str(asset_id) if asset_id is not None else None,
        "asset_hash": content_hash(asset_ref) if asset_ref is not None else None,
        "generation_id": generation_id,
        "dedup_id": dedup_id,
        "queue_id": int(queue_id) if queue_id is not None else None,
        "schedule_id": int(schedule_id) if schedule_id is not None else None,
        "state_before": state_before,
        "state_after": state_after,
        "result": result,
        "error": error,
        "attempt": int(attempt or 0),
        "lease_token": lease_token,
        "telegram_message_id": int(telegram_message_id)
        if telegram_message_id is not None
        else None,
        "created_at_ms": now_ms,
    }
    return event


# ── Durable writer (best-effort, never raises) ───────────────────────────

async def record_audit(event: dict[str, Any]) -> bool:
    """Persist a pre-built audit event. Returns ``True`` on success.

    Never raises: persistence failure is logged and returns ``False`` so
    business logic (requests, enqueue, sends) can never fail because the
    audit store is unavailable (failure-isolation invariant).
    """
    try:
        for field in _REQUIRED_FOR_RECORD:
            if event.get(field) is None or (
                isinstance(event.get(field), str) and not str(event[field]).strip()
            ):
                logger.warning("audit: dropping event with missing %s", field)
                return False
        from db.postgres import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO operator_audit_events "
                "(audit_id, event_type, actor_type, actor_id, creator_id, "
                " user_id, chat_id, action, content_hash, content_hash_original, "
                " content_hash_final, asset_kind, asset_id, asset_hash, "
                " generation_id, dedup_id, queue_id, schedule_id, "
                " state_before, state_after, result, error, attempt, "
                " lease_token, telegram_message_id) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,"
                "$16,$17,$18,$19,$20,$21,$22,$23,$24,$25)",
                event.get("audit_id"),
                event.get("event_type"),
                event.get("actor_type"),
                event.get("actor_id"),
                event.get("creator_id"),
                event.get("user_id"),
                event.get("chat_id"),
                event.get("action"),
                event.get("content_hash"),
                event.get("content_hash_original"),
                event.get("content_hash_final"),
                event.get("asset_kind"),
                event.get("asset_id"),
                event.get("asset_hash"),
                event.get("generation_id"),
                event.get("dedup_id"),
                event.get("queue_id"),
                event.get("schedule_id"),
                event.get("state_before"),
                event.get("state_after"),
                event.get("result"),
                event.get("error"),
                event.get("attempt"),
                event.get("lease_token"),
                event.get("telegram_message_id"),
            )
            return True
    except Exception:
        # Includes UndefinedTableError on pre-migration databases.
        logger.warning(
            "audit: persistence failed event=%s action=%s",
            event.get("event_type"),
            event.get("action"),
            exc_info=True,
        )
        return False


async def record_audit_event(
    *,
    event_type: str,
    actor: dict[str, Any] | None = None,
    actor_type: str | None = None,
    actor_id: Any = None,
    **kwargs: Any,
) -> bool:
    """Build-and-persist helper. Never raises; returns success bool."""
    try:
        if actor is not None:
            actor_type = actor.get("actor_type")
            actor_id = actor.get("actor_id")
        event = build_audit_event(
            event_type=event_type,
            actor_type=actor_type or "unknown",
            actor_id=actor_id,
            **kwargs,
        )
    except Exception:
        logger.warning("audit: event construction failed", exc_info=True)
        return False
    return await record_audit(event)
