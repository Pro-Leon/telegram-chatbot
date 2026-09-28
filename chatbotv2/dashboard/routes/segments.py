"""Fan segment API routes.

All routes require dashboard authentication.  Creator resolution follows
the existing single-creator pattern (server-side, never from the browser).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from commerce.single_creator import SingleCreatorStatus, resolve_single_application_creator
from db import segments as sdb
from segments.evaluator import (
    CompilationError,
    check_user_in_segment,
    explain_rule,
    get_segment_count,
    get_segment_members,
    get_segment_stats,
    validate_rules,
)
from segments.fields import list_fields as get_field_list
from segments.models import SegmentCreate, SegmentRule, SegmentUpdate
from segments.presets import get_preset, list_presets

logger = logging.getLogger("chatbotv2.dashboard.segments")

router = APIRouter(prefix="/api/segments", tags=["segments"])


async def _require_creator() -> int:
    """Resolve the single application creator or raise 503."""
    ctx = await resolve_single_application_creator()
    if ctx.status != SingleCreatorStatus.READY or ctx.creator_id is None:
        raise HTTPException(
            status_code=503,
            detail="Creator integration not available",
        )
    return ctx.creator_id


def _segment_json(segment: dict) -> dict:
    """Serialize a segment dict for JSON response."""
    return jsonable_encoder(segment)


# ── Field registry ─────────────────────────────────────────────────────


@router.get("/fields")
async def api_list_fields(auth: dict = Depends(require_auth)):
    """List all available segment fields and operators."""
    return JSONResponse(jsonable_encoder(get_field_list()))


# ── Presets ────────────────────────────────────────────────────────────


@router.get("/presets")
async def api_list_presets(auth: dict = Depends(require_auth)):
    """List all available segment presets."""
    return JSONResponse(jsonable_encoder(list_presets()))


@router.post("/presets/{preset_id}/activate", status_code=201)
async def api_activate_preset(preset_id: str, auth: dict = Depends(require_auth)):
    """Activate a preset, creating a normal fan_segments record."""
    creator_id = await _require_creator()

    preset = get_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail=f"Preset '{preset_id}' not found")

    ok, err = preset.validate()
    if not ok:
        raise HTTPException(status_code=400, detail=f"Invalid preset rules: {err}")

    segment = await sdb.create_segment(
        creator_id, preset.name, preset.description, preset.rules.model_dump()
    )

    # Evaluate member count eagerly
    try:
        count = await get_segment_count(creator_id, preset.rules)
        await sdb.update_member_count(creator_id, segment["id"], count)
        await sdb.update_last_evaluated(creator_id, segment["id"])
        segment["member_count"] = count
    except Exception:
        logger.warning("Failed to evaluate preset segment count", exc_info=True)

    return JSONResponse(_segment_json(segment), status_code=201)


# ── CRUD ───────────────────────────────────────────────────────────────


@router.get("")
async def api_list_segments(auth: dict = Depends(require_auth)):
    """List all segments for the current creator."""
    creator_id = await _require_creator()
    segments = await sdb.list_segments(creator_id)
    return JSONResponse(jsonable_encoder(segments))


@router.post("", status_code=201)
async def api_create_segment(body: SegmentCreate, auth: dict = Depends(require_auth)):
    """Create a new segment."""
    creator_id = await _require_creator()

    ok, err = validate_rules(body.rules)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Invalid rule: {err}")

    segment = await sdb.create_segment(
        creator_id, body.name, body.description, body.rules.model_dump()
    )

    # Evaluate member count eagerly
    try:
        count = await get_segment_count(creator_id, body.rules)
        await sdb.update_member_count(creator_id, segment["id"], count)
        await sdb.update_last_evaluated(creator_id, segment["id"])
        segment["member_count"] = count
    except Exception:
        logger.warning("Failed to evaluate segment count on create", exc_info=True)

    return JSONResponse(_segment_json(segment), status_code=201)


@router.get("/{segment_id}")
async def api_get_segment(segment_id: int, auth: dict = Depends(require_auth)):
    """Get a single segment."""
    creator_id = await _require_creator()
    segment = await sdb.get_segment(creator_id, segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")
    return JSONResponse(_segment_json(segment))


@router.patch("/{segment_id}")
async def api_update_segment(
    segment_id: int, body: SegmentUpdate, auth: dict = Depends(require_auth)
):
    """Update a segment."""
    creator_id = await _require_creator()

    updates: dict = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.description is not None:
        updates["description"] = body.description
    if body.rules is not None:
        ok, err = validate_rules(body.rules)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Invalid rule: {err}")
        updates["rules"] = body.rules.model_dump()

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    segment = await sdb.update_segment(creator_id, segment_id, **updates)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")

    # Re-evaluate if rules changed
    if "rules" in updates:
        try:
            rules = SegmentRule(**segment["rules"])
            count = await get_segment_count(creator_id, rules)
            await sdb.update_member_count(creator_id, segment_id, count)
            await sdb.update_last_evaluated(creator_id, segment_id)
            segment["member_count"] = count
        except Exception:
            logger.warning("Failed to re-evaluate segment after update", exc_info=True)

    return JSONResponse(_segment_json(segment))


@router.delete("/{segment_id}")
async def api_delete_segment(segment_id: int, auth: dict = Depends(require_auth)):
    """Delete a segment."""
    creator_id = await _require_creator()
    deleted = await sdb.delete_segment(creator_id, segment_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Segment not found")
    return JSONResponse({"ok": True})


# ── Toggle / Duplicate ─────────────────────────────────────────────────


@router.post("/{segment_id}/toggle")
async def api_toggle_segment(segment_id: int, auth: dict = Depends(require_auth)):
    """Toggle segment enabled/disabled."""
    creator_id = await _require_creator()
    segment = await sdb.get_segment(creator_id, segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")
    new_enabled = not segment["enabled"]
    segment = await sdb.toggle_segment(creator_id, segment_id, new_enabled)
    return JSONResponse(_segment_json(segment))


@router.post("/{segment_id}/duplicate")
async def api_duplicate_segment(segment_id: int, auth: dict = Depends(require_auth)):
    """Duplicate a segment."""
    creator_id = await _require_creator()
    source = await sdb.get_segment(creator_id, segment_id)
    if not source:
        raise HTTPException(status_code=404, detail="Segment not found")
    new_name = f"{source['name']} (Copy)"
    segment = await sdb.duplicate_segment(creator_id, segment_id, new_name)
    if not segment:
        raise HTTPException(status_code=500, detail="Failed to duplicate segment")
    return JSONResponse(_segment_json(segment), status_code=201)


# ── Evaluation ─────────────────────────────────────────────────────────


@router.post("/preview")
async def api_preview_segment(body: dict, auth: dict = Depends(require_auth)):
    """Preview segment members without saving."""
    creator_id = await _require_creator()

    # Parse rules from request body
    rules_data = body.get("rules")
    if not rules_data:
        raise HTTPException(status_code=400, detail="Missing 'rules' in request body")

    try:
        rules = SegmentRule(**rules_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid rule structure: {e}")

    ok, err = validate_rules(rules)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Invalid rule: {err}")

    try:
        count = await get_segment_count(creator_id, rules)
        members = await get_segment_members(creator_id, rules, limit=20)
    except CompilationError as e:
        raise HTTPException(status_code=400, detail=f"Rule compilation error: {e}")

    return JSONResponse(jsonable_encoder({"count": count, "members": members}))


@router.get("/{segment_id}/members")
async def api_get_members(
    segment_id: int,
    limit: int = 100,
    offset: int = 0,
    auth: dict = Depends(require_auth),
):
    """Get members of a segment."""
    creator_id = await _require_creator()
    segment = await sdb.get_segment(creator_id, segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")

    try:
        rules = SegmentRule(**segment["rules"])
        members = await get_segment_members(
            creator_id, rules, min(limit, 500), offset
        )
    except CompilationError as e:
        raise HTTPException(status_code=400, detail=f"Rule compilation error: {e}")

    return JSONResponse(
        jsonable_encoder({"members": members, "total": segment["member_count"]})
    )


@router.get("/{segment_id}/count")
async def api_get_count(segment_id: int, auth: dict = Depends(require_auth)):
    """Get member count for a segment."""
    creator_id = await _require_creator()
    segment = await sdb.get_segment(creator_id, segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")

    try:
        rules = SegmentRule(**segment["rules"])
        count = await get_segment_count(creator_id, rules)
        await sdb.update_member_count(creator_id, segment_id, count)
        await sdb.update_last_evaluated(creator_id, segment_id)
    except CompilationError as e:
        raise HTTPException(status_code=400, detail=f"Rule compilation error: {e}")

    return JSONResponse(jsonable_encoder({"count": count}))


@router.get("/{segment_id}/check/{user_id}")
async def api_check_member(
    segment_id: int, user_id: int, auth: dict = Depends(require_auth)
):
    """Check if a user belongs to a segment."""
    creator_id = await _require_creator()
    segment = await sdb.get_segment(creator_id, segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")

    try:
        rules = SegmentRule(**segment["rules"])
        is_member = await check_user_in_segment(creator_id, rules, user_id)
    except CompilationError as e:
        raise HTTPException(status_code=400, detail=f"Rule compilation error: {e}")

    return JSONResponse(jsonable_encoder({"user_id": user_id, "is_member": is_member}))


# ── Explanation & Statistics ──────────────────────────────────────────


@router.get("/{segment_id}/explain/{user_id}")
async def api_explain_segment_user(
    segment_id: int, user_id: int, auth: dict = Depends(require_auth)
):
    """Explain why a specific user matches (or doesn't match) a segment."""
    creator_id = await _require_creator()
    segment = await sdb.get_segment(creator_id, segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")

    try:
        rules = SegmentRule(**segment["rules"])
        eval_tree = await explain_rule(creator_id, rules, user_id)
    except CompilationError as e:
        raise HTTPException(status_code=400, detail=f"Rule compilation error: {e}")

    from segments.evaluator import _count_evaluation_leaves

    satisfied, total = _count_evaluation_leaves(eval_tree)
    return JSONResponse(jsonable_encoder({
        "user_id": user_id,
        "segment_id": segment_id,
        "segment_name": segment["name"],
        "is_member": eval_tree.satisfied,
        "rule_evaluation": eval_tree,
        "satisfied_count": satisfied,
        "total_count": total,
    }))


@router.get("/{segment_id}/stats")
async def api_segment_stats(segment_id: int, auth: dict = Depends(require_auth)):
    """Get field-level statistics for a segment."""
    creator_id = await _require_creator()
    stats = await get_segment_stats(creator_id, segment_id)
    if stats is None:
        raise HTTPException(status_code=404, detail="Segment not found")
    return JSONResponse(jsonable_encoder(stats))
