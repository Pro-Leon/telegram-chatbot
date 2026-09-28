"""Conversation tags routes."""

import logging

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from chatbotv2.dashboard.dependencies import validate_tag_name, verify_user_id_exists
from chatbotv2.dashboard.schemas import TagCreateRequest
from core.event_bus import publish_event
from db.postgres import (
    assign_conversation_tag,
    create_conversation_tag,
    delete_conversation_tag,
    get_conversation_tag,
    list_conversation_tags,
    list_user_conversation_tags,
    remove_conversation_tag,
)

logger = logging.getLogger("chatbotv2.dashboard")

router = APIRouter()


@router.get("/api/conversation-tags")
async def api_list_conversation_tags(auth: dict = Depends(require_auth)):
    tags = await list_conversation_tags()
    return JSONResponse(jsonable_encoder(tags))


@router.post("/api/conversation-tags")
async def api_create_conversation_tag(
    req: TagCreateRequest,
    auth: dict = Depends(require_auth),
):
    name, description, err = validate_tag_name(req.name, req.description)
    if err:
        return err

    tag = await create_conversation_tag(
        name=name,
        description=description,
        created_by=auth.get("username"),
    )
    if tag is None:
        return JSONResponse(
            {"error": "A tag with this name already exists"},
            status_code=409,
        )
    return JSONResponse(jsonable_encoder(tag), status_code=201)


@router.delete("/api/conversation-tags/{tag_id}")
async def api_delete_conversation_tag(
    tag_id: int,
    auth: dict = Depends(require_auth),
):
    deleted = await delete_conversation_tag(tag_id)
    if not deleted:
        return JSONResponse({"error": "Tag not found"}, status_code=404)
    return JSONResponse({"ok": True})


@router.get("/api/analytics/conversations/{user_id}/tags")
async def api_list_conversation_tags_for_user(
    user_id: int,
    auth: dict = Depends(require_auth),
):
    err = await verify_user_id_exists(user_id)
    if err:
        return err

    tags = await list_user_conversation_tags(user_id)
    return JSONResponse(jsonable_encoder(tags))


@router.post("/api/analytics/conversations/{user_id}/tags/{tag_id}")
async def api_assign_conversation_tag(
    user_id: int,
    tag_id: int,
    auth: dict = Depends(require_auth),
):
    err = await verify_user_id_exists(user_id)
    if err:
        return err

    tag = await get_conversation_tag(tag_id)
    if not tag:
        return JSONResponse({"error": "Tag not found"}, status_code=404)

    assigned = await assign_conversation_tag(
        user_id=user_id,
        tag_id=tag_id,
        assigned_by=auth.get("username"),
    )
    if not assigned:
        return JSONResponse({"error": "Failed to assign tag"}, status_code=500)

    _creator_id = None
    try:
        from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
        _ctx = await resolve_single_application_creator()
        if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
            _creator_id = _ctx.creator_id
    except Exception:
        _creator_id = None
    await publish_event(
        "conversation.tag_changed",
        {
            "user_id": user_id,
            "tag_id": tag_id,
            "tag_name": tag.get("name") if isinstance(tag, dict) else str(tag),
            "action": "assigned",
            "changed_by": auth.get("username"),
        },
        scope="user",
        dialog_id=user_id,
        creator_id=_creator_id,
    )

    return JSONResponse({"ok": True})


@router.delete("/api/analytics/conversations/{user_id}/tags/{tag_id}")
async def api_remove_conversation_tag(
    user_id: int,
    tag_id: int,
    auth: dict = Depends(require_auth),
):
    err = await verify_user_id_exists(user_id)
    if err:
        return err

    removed = await remove_conversation_tag(user_id, tag_id)
    if not removed:
        return JSONResponse({"error": "Tag assignment not found"}, status_code=404)

    _creator_id = None
    try:
        from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
        _ctx = await resolve_single_application_creator()
        if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
            _creator_id = _ctx.creator_id
    except Exception:
        _creator_id = None
    await publish_event(
        "conversation.tag_changed",
        {
            "user_id": user_id,
            "tag_id": tag_id,
            "action": "removed",
            "changed_by": auth.get("username"),
        },
        scope="user",
        dialog_id=user_id,
        creator_id=_creator_id,
    )

    return JSONResponse({"ok": True})
