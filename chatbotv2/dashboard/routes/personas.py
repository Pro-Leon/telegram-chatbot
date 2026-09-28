"""Persona management routes — Phase 43B: creator-scoped + structured metadata.
Phase 89 Studio adds validate/preview/promote and commerce safety.
"""

from fastapi import APIRouter, Depends, Form, Query, Body
from fastapi.responses import JSONResponse
import json
import re
from typing import Any

from chatbotv2.dashboard.auth import require_auth
from db.postgres import (
    create_persona,
    delete_persona,
    get_all_personas,
    set_user_persona,
    update_persona,
)
from db.redis import invalidate_persona_cache

router = APIRouter()

# Phase 89R: forbidden commerce + roleplay authority fields in persona metadata
_FORBIDDEN_PERSONA_KEYS = {
    "product_id",
    "price",
    "price_minor",
    "currency",
    "ppv",
    "payment",
    "offer",
    "checkout",
    "payment_url",
    "sales_url",
    "eligibility",
    "transaction_id",
}

# Phase 89R: persona must NOT define player identity/dialogue/actions (roleplay separation)
_FORBIDDEN_ROLEPLAY_KEYS = {
    "player_name",
    "player_identity",
    "player_dialogue",
    "player_action",
    "player_actions",
    "listener_name",
    "fan_name",
    "fan_identity",
    "player_id",
    "fan_id",
}

def _contains_forbidden_commerce(obj: Any, seen: set | None = None) -> str | None:
    """Return forbidden key if found, else None. Recursively checks dict keys."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in _FORBIDDEN_PERSONA_KEYS:
                return str(k)
            if kl in _FORBIDDEN_ROLEPLAY_KEYS:
                return str(k)
            # check nested
            found = _contains_forbidden_commerce(v, seen)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _contains_forbidden_commerce(item, seen)
            if found:
                return found
    return None

def _contains_forbidden_roleplay(obj: Any) -> str | None:
    """Check for roleplay authority violations (player identity etc.)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in _FORBIDDEN_ROLEPLAY_KEYS:
                return str(k)
            found = _contains_forbidden_roleplay(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _contains_forbidden_roleplay(item)
            if found:
                return found
    return None

async def _verify_persona_ownership(persona_id: int, requested_creator_id: int | None, session: dict) -> None:
    """Creator isolation: ensure requested persona belongs to requested creator."""
    # If no requested creator, allow (legacy) but log; real ownership is DB row's creator_id
    # For V1, enforce that if persona exists, its creator_id must match requested_creator_id when both present
    from db.postgres import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT creator_id FROM personas WHERE id = $1", persona_id)
        if not row:
            raise ValueError("Persona not found")
        actual = row["creator_id"]
        # If requested is None, treat as allowed for legacy admin; but if actual is not null and request is null, still allow read (existing behavior)
        # For write isolation, require match when request provided
        if requested_creator_id is not None and actual is not None and int(actual) != int(requested_creator_id):
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Creator isolation: persona belongs to different creator")
        # If no requested but actual exists, still allow (dashboard admin model) — no hard fail


@router.get("/api/personas")
async def api_get_personas(
    creator_id: int | None = Query(default=None),
    auth: dict = Depends(require_auth),
):
    personas = await get_all_personas(creator_id=creator_id)
    return JSONResponse(personas)


@router.post("/api/personas")
async def api_create_persona(
    name: str = Form(...),
    instructions: str = Form(...),
    is_default: bool = Form(False),
    creator_id: int | None = Form(default=None),
    metadata: str | None = Form(default=None),
    auth: dict = Depends(require_auth),
):
    meta_dict = None
    if metadata:
        try:
            meta_dict = json.loads(metadata) if isinstance(metadata, str) else metadata
        except Exception:
            return JSONResponse({"error": "invalid metadata JSON"}, status_code=400)
        # Phase 89: reject commerce authority fields
        forbidden = _contains_forbidden_commerce(meta_dict)
        if forbidden:
            return JSONResponse({"error": f"metadata contains forbidden commerce field: {forbidden}"}, status_code=400)
    persona_id = await create_persona(name, instructions, is_default, creator_id=creator_id, metadata=meta_dict)
    await invalidate_persona_cache(creator_id=creator_id)
    return JSONResponse({"id": persona_id, "ok": True})


@router.put("/api/personas/{persona_id}")
async def api_update_persona(
    persona_id: int,
    name: str = Form(...),
    instructions: str = Form(...),
    is_default: bool = Form(False),
    creator_id: int | None = Form(default=None),
    metadata: str | None = Form(default=None),
    auth: dict = Depends(require_auth),
):
    meta_dict = None
    # metadata is optional; if omitted, preserve existing; if provided, replace
    # Empty string means no metadata change
    if metadata is not None and metadata != "":
        try:
            meta_dict = json.loads(metadata) if isinstance(metadata, str) else metadata
        except Exception:
            return JSONResponse({"error": "invalid metadata JSON"}, status_code=400)
        forbidden = _contains_forbidden_commerce(meta_dict)
        if forbidden:
            return JSONResponse({"error": f"metadata contains forbidden commerce field: {forbidden}"}, status_code=400)
        # Phase 89 creator isolation: verify ownership when creator_id supplied
        try:
            await _verify_persona_ownership(persona_id, creator_id, auth)
        except Exception as e:
            from fastapi import HTTPException

            if isinstance(e, HTTPException):
                raise
            return JSONResponse({"error": str(e)}, status_code=404)
    # When metadata is "" or None, we pass None to preserve (handled in update_persona)
    if metadata == "":
        meta_dict = None
    await update_persona(persona_id, name, instructions, is_default, creator_id=creator_id, metadata=meta_dict)
    await invalidate_persona_cache(creator_id=creator_id)
    return JSONResponse({"ok": True})


@router.delete("/api/personas/{persona_id}")
async def api_delete_persona(
    persona_id: int,
    creator_id: int | None = Query(default=None),
    auth: dict = Depends(require_auth),
):
    # P0: creator isolation — use existing mechanism
    try:
        await _verify_persona_ownership(persona_id, creator_id, auth)
    except Exception as e:
        from fastapi import HTTPException

        if isinstance(e, HTTPException):
            raise
        # _verify raises 403 for cross-creator, 404 for not found — propagate
        raise
    # Additional guard: if persona has creator_id but caller omitted creator_id, require it
    # (prevents bypass by omitting query param)
    if creator_id is None:
        try:
            from db.postgres import get_pool

            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT creator_id FROM personas WHERE id = $1", persona_id)
                if row and row["creator_id"] is not None:
                    from fastapi import HTTPException

                    raise HTTPException(status_code=403, detail="Creator isolation: creator_id required for delete")
        except Exception as e:
            from fastapi import HTTPException

            if isinstance(e, HTTPException):
                raise
            pass
    await delete_persona(persona_id)
    await invalidate_persona_cache(creator_id=creator_id)
    return JSONResponse({"ok": True})


@router.post("/api/users/{user_id}/persona")
async def api_set_user_persona(
    user_id: int,
    persona_id: int = Form(...),
    auth: dict = Depends(require_auth),
):
    # M4 D4: persona assignment enforces the same ownership semantics as
    # sibling persona routes. The session creator comes from the application
    # resolver (never from request parameters); a foreign persona is rejected
    # and a missing creator fails closed.
    from fastapi import HTTPException

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
    try:
        await _verify_persona_ownership(persona_id, _creator_id, auth)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        return JSONResponse({"error": str(e)}, status_code=404)
    await set_user_persona(user_id, persona_id)
    # M4 D4: creator-scoped invalidation only — must not wipe other
    # creators' persona caches for this user.
    await invalidate_persona_cache(user_id=user_id, creator_id=_creator_id)
    return JSONResponse({"ok": True})


@router.get("/api/personas/{persona_id}")
async def api_get_persona(
    persona_id: int,
    creator_id: int | None = Query(default=None),
    auth: dict = Depends(require_auth),
):
    from db.postgres import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, instructions, is_default, creator_id, version, updated_at, metadata FROM personas WHERE id = $1",
            persona_id,
        )
        if not row:
            return JSONResponse({"error": "Persona not found"}, status_code=404)
        # creator isolation: if creator_id supplied, must match or allow if row creator null
        actual = row["creator_id"]
        if creator_id is not None and actual is not None and int(actual) != int(creator_id):
            return JSONResponse({"error": "Creator isolation: persona belongs to different creator"}, status_code=403)
        # if creator_id is None and actual not null, still allow for admin read (existing behavior)
        return JSONResponse(dict(row))


@router.post("/api/personas/{persona_id}/promote")
async def api_promote_persona(
    persona_id: int,
    creator_id: int | None = Form(default=None),
    auth: dict = Depends(require_auth),
):
    # Promote == set is_default true, version bump already via update
    from db.postgres import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT creator_id, is_default FROM personas WHERE id = $1", persona_id)
        if not row:
            return JSONResponse({"error": "Persona not found"}, status_code=404)
        actual = row["creator_id"]
        target_creator = creator_id if creator_id is not None else actual
        if target_creator is not None and actual is not None and int(actual) != int(target_creator):
            return JSONResponse({"error": "Creator isolation: persona belongs to different creator"}, status_code=403)
        # set is_default true for this persona, false for others of same creator
        if target_creator is not None:
            await conn.execute("UPDATE personas SET is_default = FALSE WHERE creator_id = $1 AND id != $2", target_creator, persona_id)
        else:
            await conn.execute("UPDATE personas SET is_default = FALSE WHERE creator_id IS NULL AND id != $1", persona_id)
        await conn.execute("UPDATE personas SET is_default = TRUE, updated_at = NOW() WHERE id = $1", persona_id)
        await invalidate_persona_cache(creator_id=target_creator)
        # fetch updated row
        updated = await conn.fetchrow("SELECT id, name, instructions, is_default, creator_id, version, updated_at, metadata FROM personas WHERE id = $1", persona_id)
        return JSONResponse({"ok": True, "persona": dict(updated) if updated else {}})


@router.post("/api/personas/validate")
async def api_validate_persona(
    payload: dict = Body(...),
    auth: dict = Depends(require_auth),
):
    # payload may contain metadata dict or string
    metadata = payload.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            return JSONResponse({"valid": False, "error": "invalid metadata JSON"}, status_code=400)
    if metadata is None:
        metadata = payload
    forbidden = _contains_forbidden_commerce(metadata)
    if forbidden:
        return JSONResponse({"valid": False, "error": f"forbidden commerce field: {forbidden}"}, status_code=400)
    # also validate structured fields via existing persona schema (light)
    # Use memory/creator_persona STRUCTURED_FIELDS allowlist check not strict here
    return JSONResponse({"valid": True, "ok": True})


@router.post("/api/personas/{persona_id}/preview")
async def api_preview_persona(
    persona_id: int,
    creator_id: int | None = Query(default=None),
    auth: dict = Depends(require_auth),
):
    from db.postgres import get_pool
    from memory.creator_persona import render_persona_block, render_compact_persona_block

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, name, instructions, metadata, version FROM personas WHERE id = $1", persona_id)
        if not row:
            return JSONResponse({"error": "Persona not found"}, status_code=404)
        actual_creator = None
        try:
            # fetch creator for isolation check
            crow = await conn.fetchrow("SELECT creator_id FROM personas WHERE id = $1", persona_id)
            actual_creator = crow["creator_id"] if crow else None
        except Exception:
            pass
        if creator_id is not None and actual_creator is not None and int(actual_creator) != int(creator_id):
            return JSONResponse({"error": "Creator isolation"}, status_code=403)
        metadata = row["metadata"] or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        instructions = row["instructions"] or ""
        # Use canonical renderer
        try:
            full = render_persona_block(instructions, metadata)
            compact = render_compact_persona_block(metadata)
        except Exception as e:
            full = f"render error: {e}"
            compact = ""
        return JSONResponse(
            {
                "id": row["id"],
                "name": row["name"],
                "version": row["version"],
                "rendered_full": full,
                "rendered_compact": compact,
                "metadata": metadata,
            }
        )
