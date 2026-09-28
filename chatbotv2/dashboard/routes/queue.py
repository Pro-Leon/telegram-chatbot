"""Operator queue routes."""

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from core.event_bus import publish_event
from db.postgres import get_pending_queue_items, get_queue_item, resolve_queue_item
from db.redis import enqueue_send

router = APIRouter()

# M6: closed set of operator queue resolution states. resolve_queue_item
# persists status verbatim, so an unconstrained value would strand the item
# (invisible to both the pending list and the flush worker, undeliverable).
# "pending" is included: persisting an edit without resolving reuses the
# pending transition (state unchanged, content/edited flags updated).
QUEUE_RESOLVE_STATUSES = frozenset({"pending", "approved", "rejected", "failed"})


@router.get("/api/queue/pending")
async def api_queue_pending(auth: dict = Depends(require_auth)):
    # Creator-scoped queue isolation (Phase 43F) – P1.3a strict, no global fallback
    _creator_id = None
    try:
        from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
        _ctx = await resolve_single_application_creator()
        if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
            _creator_id = _ctx.creator_id
    except Exception:
        _creator_id = None
    if _creator_id is None:
        # P1.3a: fail closed – no global queue read when creator unavailable
        return JSONResponse(jsonable_encoder([]))
    items = await get_pending_queue_items(limit=50, creator_id=_creator_id)
    return JSONResponse(jsonable_encoder(items))


@router.get("/api/dialogs/{dialog_id}/suggestions")
async def api_dialog_suggestions(dialog_id: int, auth: dict = Depends(require_auth)):
    from db.postgres import get_pool

    # Creator-scoped suggestions isolation (Phase 43F)
    _creator_id = None
    try:
        from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
        _ctx = await resolve_single_application_creator()
        if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
            _creator_id = _ctx.creator_id
    except Exception:
        _creator_id = None
    pool = await get_pool()
    async with pool.acquire() as conn:
        if _creator_id is None:
            # P1.3a: fail closed – no global fallback
            return JSONResponse(jsonable_encoder([]))
        rows = await conn.fetch(
            "SELECT id, draft_content, confidence_score, flags, created_at "
            "FROM operator_queue WHERE user_id = $1 AND creator_id = $2 AND status = 'pending' ORDER BY created_at DESC LIMIT 10",
            dialog_id,
            _creator_id,
        )
    return JSONResponse(jsonable_encoder([dict(r) for r in rows]))


@router.post("/api/queue/{item_id}/resolve")
async def api_resolve_queue(
    item_id: int,
    status: str = Form(...),
    content: str | None = Form(None),
    auth: dict = Depends(require_auth),
):
    # Creator-scoped + state-scoped resolution (Phases 1-4): creator identity
    # comes from the application context (single-creator resolution), never
    # from the authenticated username, and is checked against the queue row.
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
    # M6: closed resolution states only (see QUEUE_RESOLVE_STATUSES).
    if status not in QUEUE_RESOLVE_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid queue status")
    item = await get_queue_item(item_id, creator_id=_creator_id)
    if not item:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    user_id = item["user_id"]
    _resolved_by = auth.get("username") if isinstance(auth, dict) else None
    # M7 (B5): server-derived human actor; retained alongside legacy resolved_by.
    from core.audit import actor_from_auth, record_audit_event

    _actor = actor_from_auth(auth)
    mutated = await resolve_queue_item(
        item_id, status, final_content=content,
        creator_id=_creator_id, resolved_by=_resolved_by,
    )
    if not mutated:
        # Already resolved / raced: do not mutate or send again.
        raise HTTPException(status_code=409, detail="Suggestion already resolved")

    from core.generation import is_valid_generation_id as _is_valid_gid

    _row_gid = item.get("generation_id")
    # M7 (B1/B3): durable lifecycle record for the resolution. resolve with
    # status="pending" plus content is an edit-persist (state unchanged);
    # approved/rejected/failed are terminal-ish transitions.
    _is_edit_only = status == "pending" and isinstance(content, str) and content is not None
    await record_audit_event(
        event_type="edit" if _is_edit_only else (
            "approve" if status == "approved" else (
                "reject" if status == "rejected" else (
                    "failure" if status == "failed" else "intent"))),
        actor=_actor,
        creator_id=_creator_id,
        user_id=user_id,
        action="POST /api/queue/{id}/resolve",
        content=content if isinstance(content, str) else None,
        content_original=item.get("draft_content"),
        generation_id=_row_gid if _is_valid_gid(_row_gid) else None,
        queue_id=item_id,
        state_before="pending",
        state_after=status,
        result=status,
    )
    await publish_event(
        "operator_queue.updated",
        {
            "queue_id": item_id,
            "action": status,
            "user_id": user_id,
        },
        user_id=user_id,
        dialog_id=user_id,
        creator_id=_creator_id,
        generation_id=_row_gid if _is_valid_gid(_row_gid) else None,
        scope="user",
    )
    return JSONResponse({"ok": True})


@router.post("/api/suggestions/{queue_id}/send")
async def api_send_suggestion(
    queue_id: int,
    content: str | None = Form(None),
    auth: dict = Depends(require_auth),
):
    # Creator-scoped send (Phases 1-4): resolve creator first, then read the
    # queue row with the creator predicate. Never infer ownership from auth.
    _creator_id2 = None
    try:
        from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
        _ctx2 = await resolve_single_application_creator()
        if _ctx2.status == SingleCreatorStatus.READY and _ctx2.creator_id is not None:
            _creator_id2 = _ctx2.creator_id
    except Exception:
        _creator_id2 = None
    if _creator_id2 is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    item = await get_queue_item(queue_id, creator_id=_creator_id2)
    if not item or item["status"] != "pending":
        raise HTTPException(status_code=404, detail="Suggestion not found")

    # M6: operator intent must match the outbound bytes. The queue UI posts
    # edited content to this endpoint; previously the field was silently
    # ignored and the original draft was sent. An accepted edit is persisted
    # (pending-state, content/edited flags) BEFORE enqueue so the persisted
    # row, the enqueued payload, and the transmitted content are identical.
    _resolved_by = auth.get("username") if isinstance(auth, dict) else None
    _draft = item["draft_content"] or ""
    # FastAPI resolves Form(None) at the HTTP boundary; direct in-process
    # callers see the Form marker itself — treat any non-string as absent.
    _incoming: str | None = content if isinstance(content, str) else None
    _edited_text: str | None = _incoming if (_incoming is not None and _incoming != _draft) else None
    if _edited_text is not None and not _edited_text.strip():
        raise HTTPException(status_code=422, detail="Edited content is empty")
    if _edited_text is not None:
        _persisted = await resolve_queue_item(
            queue_id, "pending", final_content=_edited_text,
            creator_id=_creator_id2, resolved_by=_resolved_by,
        )
        if not _persisted:
            # Lost the pending-state race before enqueue: do not send.
            raise HTTPException(status_code=409, detail="Suggestion already resolved")
    _send_text = _edited_text if _edited_text is not None else _draft
    _was_edited = _edited_text is not None or bool(item.get("edited"))

    # Preserve the queue row's correlation identity. queue_item:{id} remains
    # the dedup key only; it is never a replacement for generation_id.
    # M6: edited content gets a content-bound dedup suffix so it can neither
    # be suppressed as a duplicate of the unedited draft nor suppress one;
    # retries of the same edit still share one identity. was_edited is set
    # explicitly so downstream never mistakes edited bytes for evaluated ones.
    import hashlib as _hashlib

    from core.generation import is_valid_generation_id

    _row_gid = item.get("generation_id")
    _send_gid = _row_gid if is_valid_generation_id(_row_gid) else None
    if _edited_text is not None:
        _content_hash = _hashlib.sha256(_edited_text.encode("utf-8")).hexdigest()[:16]
        _dedup_id = f"queue_item:{queue_id}:{_content_hash}"
    else:
        _dedup_id = f"queue_item:{queue_id}"
    # M7 (B5): server-derived human actor threaded into the stream payload
    # (additive keys; downstream consumers ignore unknown keys).
    from core.audit import actor_from_auth as _actor_from_auth
    from core.audit import record_audit_event as _record_audit

    _send_actor = _actor_from_auth({"username": _resolved_by} if _resolved_by else {})
    if _edited_text is not None:
        await _record_audit(
            event_type="edit",
            actor=_send_actor,
            creator_id=_creator_id2,
            user_id=item["user_id"],
            action="POST /api/suggestions/{id}/send",
            content=_edited_text,
            content_original=_draft,
            generation_id=_send_gid,
            dedup_id=_dedup_id,
            queue_id=queue_id,
            state_before="pending",
            state_after="pending",
            result="edit_persisted",
        )
    try:
        await enqueue_send(
            {
                "entity": str(item["user_id"]),
                "content": _send_text,
                "draft_content": _send_text,
                "was_edited": _was_edited,
                "was_auto_approved": False,
                "confidence_score": item.get("confidence_score") or 0,
                "operator_id": item.get("assigned_to"),
                "save_to_db": True,
                "creator_id": str(_creator_id2),
                "actor_type": _send_actor["actor_type"],
                "actor_id": _send_actor["actor_id"],
                **({"generation_id": _send_gid} if _send_gid else {}),
            },
            dedup_id=_dedup_id,
            generation_id=_send_gid,
            creator_id=_creator_id2,
        )
    except Exception as _send_enq_exc:
        await _record_audit(
            event_type="failure",
            actor=_send_actor,
            creator_id=_creator_id2,
            user_id=item["user_id"],
            action="POST /api/suggestions/{id}/send",
            content=_send_text,
            content_original=_draft if _edited_text is not None else None,
            generation_id=_send_gid,
            dedup_id=_dedup_id,
            queue_id=queue_id,
            state_before="pending",
            state_after="enqueue_failed",
            result="failure",
            error=str(_send_enq_exc)[:200],
        )
        # Phase 1.3: rails refusal surfaces as 422 with flags so the operator
        # can edit and retry (human override loop); item stays pending.
        try:
            from core.output_rails import RailsRefusal as _RailsRefusal

            if isinstance(_send_enq_exc, _RailsRefusal):
                raise HTTPException(status_code=422, detail=str(_send_enq_exc))
        except HTTPException:
            raise
        except Exception:
            pass
        raise
    await _record_audit(
        event_type="enqueue",
        actor=_send_actor,
        creator_id=_creator_id2,
        user_id=item["user_id"],
        action="POST /api/suggestions/{id}/send",
        content=_send_text,
        content_original=_draft if _edited_text is not None else None,
        generation_id=_send_gid,
        dedup_id=_dedup_id,
        queue_id=queue_id,
        state_before="pending",
        state_after="enqueued",
        result="enqueued",
    )
    mutated = await resolve_queue_item(
        queue_id, "approved", creator_id=_creator_id2, resolved_by=_resolved_by,
    )
    if not mutated:
        # Lost the pending-state race after enqueue: the row was already
        # resolved elsewhere. The send payload is dedup-keyed
        # (queue_item:{id}[:content-hash]) so a duplicate send is suppressed
        # downstream.
        await _record_audit(
            event_type="cancel_raced",
            actor=_send_actor,
            creator_id=_creator_id2,
            user_id=item["user_id"],
            action="POST /api/suggestions/{id}/send",
            content=_send_text,
            generation_id=_send_gid,
            dedup_id=_dedup_id,
            queue_id=queue_id,
            state_before="enqueued",
            state_after="race_lost",
            result="conflict",
            error="Suggestion already resolved",
        )
        raise HTTPException(status_code=409, detail="Suggestion already resolved")
    # M7 (B1/B3): durable approval record (hashes only, never raw content).
    await _record_audit(
        event_type="approve",
        actor=_send_actor,
        creator_id=_creator_id2,
        user_id=item["user_id"],
        action="POST /api/suggestions/{id}/send",
        content=_send_text,
        content_original=_draft if _edited_text is not None else None,
        generation_id=_send_gid,
        dedup_id=_dedup_id,
        queue_id=queue_id,
        state_before="pending",
        state_after="approved",
        result="approved",
    )
    # M6: operator send previously emitted no event. Publish the approval with
    # explicit edit provenance (additive fields only; content stays in the DB
    # row, never in the event).
    await publish_event(
        "operator_queue.updated",
        {
            "queue_id": queue_id,
            "action": "approved",
            "user_id": item["user_id"],
            "edited": _was_edited,
            "resolved_by": _resolved_by,
        },
        user_id=item["user_id"],
        dialog_id=item["user_id"],
        creator_id=_creator_id2,
        generation_id=_send_gid,
        scope="user",
    )
    return JSONResponse({"ok": True, "edited": _was_edited})
