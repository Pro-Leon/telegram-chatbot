"""Conversation notes routes."""

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from chatbotv2.dashboard.dependencies import validate_note_content, verify_user_id_exists
from chatbotv2.dashboard.schemas import NoteCreateRequest, NoteUpdateRequest
from core.event_bus import publish_event
from db.postgres import (
    count_conversation_notes,
    create_conversation_note,
    delete_conversation_note,
    get_conversation_note,
    list_conversation_notes,
    update_conversation_note,
)

logger = logging.getLogger("chatbotv2.dashboard")

router = APIRouter()


@router.get("/api/analytics/conversations/{user_id}/notes")
async def api_list_conversation_notes(
    user_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_auth),
):
    err = await verify_user_id_exists(user_id)
    if err:
        return err

    try:
        notes = await list_conversation_notes(user_id, limit=limit, offset=offset)
        total = await count_conversation_notes(user_id)
    except Exception:
        logger.exception("Failed to list notes for user %s", user_id)
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Failed to list notes")

    return JSONResponse(
        jsonable_encoder(
            {
                "notes": [
                    {
                        "id": n["id"],
                        "user_id": n["user_id"],
                        "content": n["content"],
                        "created_by": n["created_by"],
                        "created_at": n["created_at"].isoformat() if n["created_at"] else None,
                    }
                    for n in notes
                ],
                "total": total,
            }
        )
    )


@router.post("/api/analytics/conversations/{user_id}/notes")
async def api_create_conversation_note(
    user_id: int,
    req: NoteCreateRequest,
    auth: dict = Depends(require_auth),
):
    content, err = validate_note_content(req.content)
    if err:
        return err

    err = await verify_user_id_exists(user_id)
    if err:
        return err

    try:
        note = await create_conversation_note(
            user_id=user_id,
            content=content,
            created_by=auth.get("username"),
        )
    except Exception:
        logger.exception("Failed to create note for user %s", user_id)
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Failed to create note")

    # Creator-scoped for isolation
    _creator_id = None
    try:
        from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
        _ctx = await resolve_single_application_creator()
        if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
            _creator_id = _ctx.creator_id
    except Exception:
        _creator_id = None
    await publish_event(
        "conversation.note_changed",
        {
            "user_id": user_id,
            "note_id": note["id"],
            "action": "created",
            "changed_by": auth.get("username"),
        },
        scope="user",
        dialog_id=user_id,
        creator_id=_creator_id,
    )

    return JSONResponse(
        jsonable_encoder(
            {
                "id": note["id"],
                "user_id": note["user_id"],
                "content": note["content"],
                "created_by": note["created_by"],
                "created_at": note["created_at"].isoformat() if note["created_at"] else None,
            }
        ),
        status_code=201,
    )


@router.patch("/api/analytics/conversations/{user_id}/notes/{note_id}")
async def api_update_conversation_note(
    user_id: int,
    note_id: int,
    req: NoteUpdateRequest,
    auth: dict = Depends(require_auth),
):
    note = await get_conversation_note(note_id)
    if not note or note["user_id"] != user_id:
        return JSONResponse({"error": "Note not found"}, status_code=404)
    if note["created_by"] != auth.get("username"):
        return JSONResponse({"error": "Not authorized to edit this note"}, status_code=403)

    content, err = validate_note_content(req.content)
    if err:
        return err

    updated = await update_conversation_note(note_id, content)
    if not updated:
        return JSONResponse({"error": "Failed to update note"}, status_code=500)

    _creator_id = None
    try:
        from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
        _ctx = await resolve_single_application_creator()
        if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
            _creator_id = _ctx.creator_id
    except Exception:
        _creator_id = None
    await publish_event(
        "conversation.note_changed",
        {
            "user_id": user_id,
            "note_id": note_id,
            "action": "updated",
            "changed_by": auth.get("username"),
        },
        scope="user",
        dialog_id=user_id,
        creator_id=_creator_id,
    )

    return JSONResponse(
        jsonable_encoder(
            {
                "id": updated["id"],
                "user_id": updated["user_id"],
                "content": updated["content"],
                "created_by": updated["created_by"],
                "created_at": updated["created_at"].isoformat() if updated["created_at"] else None,
            }
        ),
    )


@router.delete("/api/analytics/conversations/{user_id}/notes/{note_id}")
async def api_delete_conversation_note(
    user_id: int,
    note_id: int,
    auth: dict = Depends(require_auth),
):
    note = await get_conversation_note(note_id)
    if not note or note["user_id"] != user_id:
        return JSONResponse({"error": "Note not found"}, status_code=404)
    if note["created_by"] != auth.get("username"):
        return JSONResponse({"error": "Not authorized to delete this note"}, status_code=403)

    deleted = await delete_conversation_note(note_id)
    if not deleted:
        return JSONResponse({"error": "Failed to delete note"}, status_code=500)

    _creator_id = None
    try:
        from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
        _ctx = await resolve_single_application_creator()
        if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
            _creator_id = _ctx.creator_id
    except Exception:
        _creator_id = None
    await publish_event(
        "conversation.note_changed",
        {
            "user_id": user_id,
            "note_id": note_id,
            "action": "deleted",
            "changed_by": auth.get("username"),
        },
        scope="user",
        dialog_id=user_id,
        creator_id=_creator_id,
    )

    return JSONResponse({"ok": True})
