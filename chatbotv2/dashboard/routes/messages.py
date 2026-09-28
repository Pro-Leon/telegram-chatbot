"""Message send and dialog interaction routes."""

import hashlib
import logging

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from chatbotv2.dashboard.schemas import SendMessageRequest
from db.postgres import get_pool, get_user_persona
from db.redis import (
    cache_default_persona,
    enqueue_inbound,
    enqueue_send,
    get_cached_default_persona,
    get_cached_user_persona,
)

logger = logging.getLogger("chatbotv2.dashboard")

router = APIRouter()


@router.post("/api/send-message")
async def api_send_message(
    req: SendMessageRequest,
    auth: dict = Depends(require_auth),
):
    try:
        from core.generation import manual_generation_id

        _creator_id = None
        try:
            from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
            _ctx = await resolve_single_application_creator()
            if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
                _creator_id = _ctx.creator_id
        except Exception:
            _creator_id = None
        if _creator_id is None:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        # M7 (B5): human actor derived server-side at the trust boundary.
        # The request body is never authoritative for actor identity.
        from core.audit import actor_from_auth, record_audit_event

        _actor = actor_from_auth(auth)
        # Manual dashboard send: explicit synthetic correlation, never
        # Telegram-derived. manual:<...> cannot collide with MD5 turns.
        _send_gid = manual_generation_id(f"dashboard:{req.user_id}:{req.content[:32]}")
        dedup_id = hashlib.md5(f"{req.user_id}:{req.content}".encode()).hexdigest()
        await record_audit_event(
            event_type="intent",
            actor=_actor,
            creator_id=_creator_id,
            user_id=req.user_id,
            action="POST /api/send-message",
            content=req.content,
            generation_id=_send_gid,
            dedup_id=dedup_id,
            state_before=None,
            state_after="intended",
        )
        try:
            await enqueue_send(
                {
                    "entity": str(req.user_id),
                    "content": req.content,
                    "draft_content": req.content,
                    "was_edited": False,
                    "was_auto_approved": False,
                    "confidence_score": 1.0,
                    "operator_id": None,
                    "save_to_db": True,
                    "creator_id": str(_creator_id),
                    "generation_id": _send_gid,
                    "actor_type": _actor["actor_type"],
                    "actor_id": _actor["actor_id"],
                },
                dedup_id=dedup_id,
                generation_id=_send_gid,
                creator_id=_creator_id,
            )
        except Exception as _enq_exc:
            await record_audit_event(
                event_type="failure",
                actor=_actor,
                creator_id=_creator_id,
                user_id=req.user_id,
                action="POST /api/send-message",
                content=req.content,
                generation_id=_send_gid,
                dedup_id=dedup_id,
                state_before="intended",
                state_after="enqueue_failed",
                result="failure",
                error=str(_enq_exc)[:200],
            )
            raise
        await record_audit_event(
            event_type="enqueue",
            actor=_actor,
            creator_id=_creator_id,
            user_id=req.user_id,
            action="POST /api/send-message",
            content=req.content,
            generation_id=_send_gid,
            dedup_id=dedup_id,
            state_before="intended",
            state_after="enqueued",
            result="enqueued",
        )
        return JSONResponse({"ok": True})
    except HTTPException:
        raise
    except Exception as e:
        # Phase 1.3 gap-fix: rails refusal surfaces as 422 with flags so the
        # operator can edit and retry (human override loop), same as queue.py.
        try:
            from core.output_rails import RailsRefusal as _RailsRefusal

            if isinstance(e, _RailsRefusal):
                raise HTTPException(status_code=422, detail=str(e))
        except HTTPException:
            raise
        except Exception:
            pass
        logger.exception("Failed to enqueue message to user %s", req.user_id)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/dialogs/{dialog_id}/send")
async def api_dialog_send(
    dialog_id: int,
    content: str = Form(...),
    auth: dict = Depends(require_auth),
):
    try:
        from core.generation import manual_generation_id

        _creator_id = None
        try:
            from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
            _ctx = await resolve_single_application_creator()
            if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
                _creator_id = _ctx.creator_id
        except Exception:
            _creator_id = None
        if _creator_id is None:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        # M7 (B5): human actor derived server-side; request body never authoritative.
        from core.audit import actor_from_auth, record_audit_event

        _actor2 = actor_from_auth(auth)
        # Manual dashboard send: explicit synthetic correlation, never
        # Telegram-derived. manual:<...> cannot collide with MD5 turns.
        _send_gid = manual_generation_id(f"dashboard:{dialog_id}:{content[:32]}")
        dedup_id = hashlib.md5(f"{dialog_id}:{content}".encode()).hexdigest()
        await record_audit_event(
            event_type="intent",
            actor=_actor2,
            creator_id=_creator_id,
            user_id=dialog_id,
            action="POST /api/dialogs/{id}/send",
            content=content,
            generation_id=_send_gid,
            dedup_id=dedup_id,
            state_before=None,
            state_after="intended",
        )
        try:
            await enqueue_send(
                {
                    "entity": str(dialog_id),
                    "content": content,
                    "draft_content": content,
                    "was_edited": False,
                    "was_auto_approved": False,
                    "confidence_score": 1.0,
                    "operator_id": None,
                    "save_to_db": True,
                    "creator_id": str(_creator_id),
                    "generation_id": _send_gid,
                    "actor_type": _actor2["actor_type"],
                    "actor_id": _actor2["actor_id"],
                },
                dedup_id=dedup_id,
                generation_id=_send_gid,
                creator_id=_creator_id,
            )
        except Exception as _enq_exc2:
            await record_audit_event(
                event_type="failure",
                actor=_actor2,
                creator_id=_creator_id,
                user_id=dialog_id,
                action="POST /api/dialogs/{id}/send",
                content=content,
                generation_id=_send_gid,
                dedup_id=dedup_id,
                state_before="intended",
                state_after="enqueue_failed",
                result="failure",
                error=str(_enq_exc2)[:200],
            )
            raise
        await record_audit_event(
            event_type="enqueue",
            actor=_actor2,
            creator_id=_creator_id,
            user_id=dialog_id,
            action="POST /api/dialogs/{id}/send",
            content=content,
            generation_id=_send_gid,
            dedup_id=dedup_id,
            state_before="intended",
            state_after="enqueued",
            result="enqueued",
        )
        return JSONResponse({"ok": True})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/dialogs/{dialog_id}/ai-reply")
async def api_dialog_ai_reply(
    dialog_id: int,
    auth: dict = Depends(require_auth),
):
    pool = await get_pool()
    # M4 D3: creator resolution happens BEFORE the row lookup so the lookup
    # itself is creator-scoped; missing creator fails closed.
    _creator_id = None
    try:
        from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
        _ctx = await resolve_single_application_creator()
        if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
            _creator_id = _ctx.creator_id
    except Exception:
        _creator_id = None
    if _creator_id is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT m.user_id, m.content, m.telegram_message_id, u.username, u.first_name FROM messages m JOIN users u ON m.user_id = u.id WHERE m.user_id = $1 AND m.creator_id = $2 AND m.direction = 'inbound' ORDER BY m.created_at DESC LIMIT 1",
            dialog_id,
            _creator_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="No inbound messages found")

    # Phase 43D: creator-scoped persona for dashboard AI-reply (isolated)
    persona = await get_cached_user_persona(dialog_id, creator_id=_creator_id)
    if persona is None:
        persona = await get_user_persona(dialog_id, creator_id=_creator_id)
        if persona and _creator_id is not None:
            try:
                from db.redis import cache_user_persona
                await cache_user_persona(dialog_id, persona, creator_id=_creator_id)
            except Exception:
                pass
    if not persona:
        persona = await get_cached_default_persona(creator_id=_creator_id)
        if persona is None:
            from db.postgres import get_default_persona

            persona = await get_default_persona(creator_id=_creator_id) or ""
            if persona:
                await cache_default_persona(persona, creator_id=_creator_id)

    # P0-3: preserve canonical generation_id for re-enqueued AI reply
    # Canonical helper (core/generation.py); same md5(user:content:tgId)
    # algorithm used at ingress so the retried turn correlates.
    # M6: a NULL telegram_message_id (legacy/synthetic rows) must not be
    # coerced to 0 and minted as Telegram-derived identity. Such rows get an
    # explicit synthetic correlation instead (never MD5-shaped).
    from core.generation import manual_generation_id, telegram_generation_id

    _tg_raw = row["telegram_message_id"]
    if _tg_raw is None:
        gid = manual_generation_id(
            f"ai-reply:{row['user_id']}:{(row['content'] or '')[:32]}"
        )
    else:
        gid = telegram_generation_id(
            int(row["user_id"]), row["content"] or "", int(_tg_raw)
        )
    _enq_result = await enqueue_inbound(
        {
            "user_id": str(row["user_id"]),
            "content": row["content"],
            "telegram_message_id": str(row["telegram_message_id"]),
            "username": row["username"] or "",
            "first_name": row["first_name"] or "",
            "persona": persona,
            "generation_id": gid,
            "creator_id": str(_creator_id),
        }
    )
    # M6: a repeat click for the same inbound row is suppressed by inbound
    # dedup ("duplicate:*") — report it instead of claiming a fresh enqueue.
    _deduped = isinstance(_enq_result, str) and _enq_result.startswith("duplicate:")
    return JSONResponse(
        {
            "ok": True,
            "message": "AI reply request already queued"
            if _deduped
            else "AI reply request enqueued",
            "deduped": _deduped,
        }
    )


@router.post("/api/dialogs/{dialog_id}/members")
async def api_dialog_members(
    dialog_id: int,
    auth: dict = Depends(require_auth),
):
    raise HTTPException(
        status_code=501,
        detail="Group member listing is only available via the main bot process",
    )


@router.post("/api/dialogs/{dialog_id}/kick")
async def api_dialog_kick(
    dialog_id: int,
    user_id: int = Form(...),
    auth: dict = Depends(require_auth),
):
    raise HTTPException(
        status_code=501,
        detail="Kick operation is only available via the main bot process",
    )


@router.get("/api/messages/recent")
async def api_recent_messages(auth: dict = Depends(require_auth)):
    # M4 D3: the recent feed is creator-dependent — scope to the session
    # creator and fail closed (empty) when creator identity is unavailable.
    _creator_id = None
    try:
        from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
        _ctx = await resolve_single_application_creator()
        if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
            _creator_id = _ctx.creator_id
    except Exception:
        _creator_id = None
    if _creator_id is None:
        return JSONResponse({"messages": []})
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.id, m.user_id, m.direction, m.content, m.created_at,
                   u.username, u.first_name
            FROM messages m
            LEFT JOIN users u ON m.user_id = u.id
            WHERE m.creator_id = $1
            ORDER BY m.created_at DESC
            LIMIT 20
            """,
            _creator_id,
        )
    messages = []
    for r in rows:
        name = r["first_name"] or r["username"] or f"User {r['user_id']}"
        created = r["created_at"]
        time_str = ""
        if created:
            time_str = created.strftime("%H:%M")
        messages.append(
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "direction": r["direction"],
                "content": r["content"],
                "created_at": created.isoformat() if created else None,
                "sender": name,
                "text": r["content"],
                "time": time_str,
            }
        )
    return JSONResponse({"messages": messages})


@router.post("/api/chats/create")
async def api_create_group(
    title: str = Form(...),
    user_ids: str = Form(...),
    auth: dict = Depends(require_auth),
):
    raise HTTPException(
        status_code=501,
        detail="Group creation is only available via the main bot process",
    )
