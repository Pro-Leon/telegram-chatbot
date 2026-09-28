"""Vault API routes.

Provides aggregated media view, product management (delegates to Fangate),
and delivery history tracking.

Auth: The auth session contains {username}. Creator is resolved from the
Fangate integration record (single-creator app).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from db.redis import enqueue_send
from integrations.fangate.errors import FangateError
from vault import service as vault_svc

logger = logging.getLogger("chatbotv2.dashboard.vault")

router = APIRouter()
AuthSession = Annotated[dict, Depends(require_auth)]


async def _require_creator_id(auth: dict) -> int:
    """Resolve creator_id from the Fangate integration record.

    The auth session only contains {username}. In a single-creator app,
    we resolve creator_id from the integration table.

    M6: prefer an *active* integration (deterministic ASC) so media reads
    and sends attribute to the operational creator when several integration
    rows exist. The legacy any-status lookup remains only as a fallback
    preserving previous availability (same authority — Fangate, not
    Dropfans — so the Dropfans single-creator resolver is deliberately
    not used here).
    """
    from db import fangate as fdb

    try:
        active_ids = await fdb.list_active_creator_ids()
    except Exception:
        active_ids = []
    if active_ids:
        return int(active_ids[0])
    creator_id = await fdb.get_any_creator_id_with_integration()
    if not creator_id:
        raise ValueError("No Fangate integration configured")
    return creator_id


# ── Media ────────────────────────────────────────────────────────────────────


@router.get("/api/vault/media")
async def list_media(
    request: Request,
    auth: AuthSession,
    user_id: int | None = Query(None, description="Fan user ID for delivery status"),
    product_id: int | None = Query(None),
    folder_id: str | None = Query(None),
    collection_only: bool = Query(False),
    media_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List aggregated media items from the Fangate product mirror.

    Pagination is product-level. Media items shown are the union of all
    media across products in the current page.
    """
    try:
        creator_id = await _require_creator_id(auth)
        result = await vault_svc.list_media(
            creator_id,
            user_id=user_id,
            product_id=product_id,
            folder_id=folder_id,
            collection_only=collection_only,
            media_type=media_type,
            limit=limit,
            offset=offset,
        )
        return result
    except FangateError as exc:
        return JSONResponse(status_code=exc.status_code or 500, content={"error": exc.message})
    except Exception:
        logger.exception("vault.media.list failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


@router.get("/api/vault/media/{media_id}")
async def get_media(
    request: Request,
    auth: AuthSession,
    media_id: int,
):
    """Get a single media item by ID with product context."""
    try:
        creator_id = await _require_creator_id(auth)
        item = await vault_svc.get_media(creator_id, media_id)
        if not item:
            return JSONResponse(status_code=404, content={"error": "Media not found"})
        return item.to_dict()
    except FangateError as exc:
        return JSONResponse(status_code=exc.status_code or 500, content={"error": exc.message})
    except Exception:
        logger.exception("vault.media.get failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


# ── Products ─────────────────────────────────────────────────────────────────


@router.get("/api/vault/products")
async def list_products(
    request: Request,
    auth: AuthSession,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List products with Vault metadata (media count, folder, collection)."""
    try:
        creator_id = await _require_creator_id(auth)
        return await vault_svc.list_products(creator_id, limit=limit, offset=offset)
    except FangateError as exc:
        return JSONResponse(status_code=exc.status_code or 500, content={"error": exc.message})
    except Exception:
        logger.exception("vault.products.list failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


@router.get("/api/vault/products/{product_id}")
async def get_product(
    request: Request,
    auth: AuthSession,
    product_id: int,
):
    """Get a single product with its media list."""
    try:
        creator_id = await _require_creator_id(auth)
        product = await vault_svc.get_product(creator_id, product_id)
        if not product:
            return JSONResponse(status_code=404, content={"error": "Product not found"})
        return product
    except FangateError as exc:
        return JSONResponse(status_code=exc.status_code or 500, content={"error": exc.message})
    except Exception:
        logger.exception("vault.products.get failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


# ── Folders ──────────────────────────────────────────────────────────────────


@router.get("/api/vault/folders")
async def list_folders(
    request: Request,
    auth: AuthSession,
):
    """List content folders from Fangate."""
    try:
        creator_id = await _require_creator_id(auth)
        return await vault_svc.list_folders(creator_id)
    except FangateError as exc:
        return JSONResponse(status_code=exc.status_code or 500, content={"error": exc.message})
    except Exception:
        logger.exception("vault.folders.list failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


# ── Media mutations (delegates to Fangate) ───────────────────────────────────


@router.post("/api/vault/products/{product_id}/media")
async def attach_media(
    request: Request,
    auth: AuthSession,
    product_id: int,
):
    """Attach existing library media to a product."""
    try:
        creator_id = await _require_creator_id(auth)
        body = await request.json()
        media_ids = body.get("media_ids", [])
        if not media_ids:
            return JSONResponse(status_code=400, content={"error": "media_ids required"})
        result = await vault_svc.attach_media(creator_id, product_id, media_ids)
        return result
    except FangateError as exc:
        return JSONResponse(status_code=exc.status_code or 500, content={"error": exc.message})
    except Exception:
        logger.exception("vault.media.attach failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


@router.delete("/api/vault/media/{media_id}")
async def delete_media(
    request: Request,
    auth: AuthSession,
    media_id: int,
):
    """Delete a media item from Fangate."""
    try:
        creator_id = await _require_creator_id(auth)
        # M7 (B8): report the actual outcome — the previous unconditional
        # {"deleted": True} claimed success even when the provider call failed.
        ok = await vault_svc.delete_media(creator_id, media_id)
        if not ok:
            return JSONResponse(
                status_code=502, content={"deleted": False, "media_id": media_id}
            )
        return {"deleted": True, "media_id": media_id}
    except FangateError as exc:
        return JSONResponse(status_code=exc.status_code or 500, content={"error": exc.message})
    except Exception:
        logger.exception("vault.media.delete failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


# ── Delivery history ─────────────────────────────────────────────────────────


@router.post("/api/vault/deliveries")
async def record_delivery(
    request: Request,
    auth: AuthSession,
):
    """Record a media delivery (called after successful Telegram send)."""
    try:
        creator_id = await _require_creator_id(auth)
        body = await request.json()
        user_id = body.get("user_id")
        fangate_media_id = body.get("fangate_media_id")
        if not user_id or not fangate_media_id:
            return JSONResponse(
                status_code=400,
                content={"error": "user_id and fangate_media_id required"},
            )
        record = await vault_svc.record_delivery(
            creator_id,
            int(user_id),
            int(fangate_media_id),
            product_id=body.get("product_id"),
            telegram_message_id=body.get("telegram_message_id"),
        )
        # M7 (B1/B8): durable record of the manual delivery-recording action.
        try:
            from core.audit import actor_from_auth, record_audit_event

            await record_audit_event(
                event_type="success",
                actor=actor_from_auth(auth),
                creator_id=creator_id,
                user_id=int(user_id),
                action="POST /api/vault/deliveries",
                asset_kind="vault_media",
                asset_id=int(fangate_media_id),
                state_before=None,
                state_after="recorded",
                result="recorded",
                telegram_message_id=body.get("telegram_message_id"),
            )
        except Exception:
            pass
        return record.to_dict()
    except FangateError as exc:
        return JSONResponse(status_code=exc.status_code or 500, content={"error": exc.message})
    except Exception:
        logger.exception("vault.deliveries.record failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


@router.get("/api/vault/deliveries/check")
async def check_delivery(
    request: Request,
    auth: AuthSession,
    user_id: int = Query(...),
    media_id: int = Query(..., alias="fangate_media_id"),
):
    """Check if a fan has already received a media item."""
    try:
        creator_id = await _require_creator_id(auth)
        received = await vault_svc.has_fan_received_media(creator_id, user_id, media_id)
        return {"received": received, "user_id": user_id, "fangate_media_id": media_id}
    except Exception:
        logger.exception("vault.deliveries.check failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


@router.get("/api/vault/deliveries")
async def list_deliveries(
    request: Request,
    auth: AuthSession,
    user_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List delivery records."""
    try:
        creator_id = await _require_creator_id(auth)
        return await vault_svc.list_deliveries(
            creator_id, user_id=user_id, limit=limit, offset=offset
        )
    except Exception:
        logger.exception("vault.deliveries.list failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


@router.get("/api/vault/unseen")
async def get_unseen_media(
    request: Request,
    auth: AuthSession,
    user_id: int = Query(...),
    media_ids: str = Query(..., description="Comma-separated media IDs"),
):
    """Return media_ids that the fan hasn't received yet."""
    try:
        creator_id = await _require_creator_id(auth)
        ids = [int(x.strip()) for x in media_ids.split(",") if x.strip().isdigit()]
        unseen = await vault_svc.get_fan_unseen_media(creator_id, user_id, ids)
        return {"unseen_ids": unseen, "user_id": user_id}
    except Exception:
        logger.exception("vault.unseen failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


# ── Analytics ──────────────────────────────────────────────────────────────


@router.get("/api/vault/analytics/overview")
async def get_analytics_overview(
    request: Request,
    auth: AuthSession,
):
    """Aggregated overview combining Fangate product metrics + CRM delivery data."""
    try:
        creator_id = await _require_creator_id(auth)
        return await vault_svc.get_vault_analytics(creator_id)
    except FangateError as exc:
        return JSONResponse(status_code=exc.status_code or 500, content={"error": exc.message})
    except Exception:
        logger.exception("vault.analytics.overview failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


@router.get("/api/vault/analytics/products")
async def get_analytics_products(
    request: Request,
    auth: AuthSession,
):
    """Per-product performance with Fangate metrics + CRM delivery counts."""
    try:
        creator_id = await _require_creator_id(auth)
        return await vault_svc.get_product_performance(creator_id)
    except FangateError as exc:
        return JSONResponse(status_code=exc.status_code or 500, content={"error": exc.message})
    except Exception:
        logger.exception("vault.analytics.products failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


@router.get("/api/vault/analytics/fans/{user_id}")
async def get_analytics_fan(
    request: Request,
    auth: AuthSession,
    user_id: int,
):
    """Delivery history and analytics for a specific fan."""
    try:
        creator_id = await _require_creator_id(auth)
        return await vault_svc.get_fan_analytics(creator_id, user_id)
    except FangateError as exc:
        return JSONResponse(status_code=exc.status_code or 500, content={"error": exc.message})
    except Exception:
        logger.exception("vault.analytics.fan failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})


# ── Media send (enqueues to send pipeline) ───────────────────────────────────


@router.post("/api/vault/send-media")
async def send_vault_media(
    request: Request,
    auth: AuthSession,
):
    """Enqueue a vault media item for sending through the existing send pipeline.

    Expects JSON body:
        user_id: int         — target fan Telegram ID
        media_type: str      — "photo", "video", or "document"
        media_path: str      — HTTPS URL of the Fangate media
        fangate_media_id: int — canonical Fangate media ID
        product_id: int      — associated product ID
        caption: str         — optional caption text
    """
    try:
        creator_id = await _require_creator_id(auth)
        body = await request.json()
        user_id = body.get("user_id")
        media_type = body.get("media_type", "").strip().lower()
        media_path = body.get("media_path", "").strip()
        fangate_media_id = body.get("fangate_media_id")

        if not user_id or not media_type or not media_path or not fangate_media_id:
            return JSONResponse(
                status_code=400,
                content={"error": "user_id, media_type, media_path, and fangate_media_id required"},
            )
        if media_type not in ("photo", "video", "document"):
            return JSONResponse(
                status_code=400,
                content={"error": "media_type must be photo, video, or document"},
            )
        if not media_path.startswith("https://"):
            return JSONResponse(
                status_code=400,
                content={"error": "media_path must be an HTTPS URL"},
            )
        try:
            user_id = int(user_id)
            fangate_media_id = int(fangate_media_id)
        except Exception:
            return JSONResponse(
                status_code=400,
                content={"error": "user_id and fangate_media_id must be integers"},
            )

        import hashlib

        from core.audit import actor_from_auth, content_hash, record_audit_event
        from core.generation import manual_generation_id

        _actor = actor_from_auth(auth)
        # M7 (B8): canonical ownership check against the Fangate mirror.
        # - Mirror hit + product/type mismatch, or media provably owned by a
        #   different creator → fail closed with 404 (indistinguishable from
        #   missing, never a cross-creator leak).
        # - Mirror miss (DropFans-side or unsynced asset) → fail OPEN with an
        #   explicit asset_unverified audit, preserving M6 send availability.
        #   The resolution authority itself is unchanged (Fangate active[0]).
        _ownership = "unverified"
        _canonical_ref: str | None = None
        try:
            _item = await vault_svc.get_media(creator_id, fangate_media_id)
            if _item is not None:
                _ownership = "verified"
                _canonical_ref = (
                    f"{creator_id}:{getattr(_item, 'media_id', fangate_media_id)}:"
                    f"{getattr(_item, 'product_id', body.get('product_id', ''))}:"
                    f"{getattr(_item, 'media_type', media_type)}"
                )
                _req_product = body.get("product_id")
                try:
                    _req_product_int = (
                        int(_req_product)
                        if _req_product is not None and str(_req_product).strip() != ""
                        else None
                    )
                except Exception:
                    _req_product_int = None
                _item_product = getattr(_item, "product_id", None)
                if _req_product_int is not None and _item_product is not None:
                    try:
                        if int(_item_product) != _req_product_int:
                            await record_audit_event(
                                event_type="failure",
                                actor=_actor,
                                creator_id=creator_id,
                                user_id=user_id,
                                action="POST /api/vault/send-media",
                                asset_kind="vault_media",
                                asset_id=fangate_media_id,
                                generation_id=None,
                                state_before=None,
                                state_after="rejected",
                                result="failure",
                                error="product_media_mismatch",
                            )
                            return JSONResponse(
                                status_code=404, content={"error": "Media not found"}
                            )
                    except Exception:
                        pass
                _item_type = getattr(_item, "media_type", None)
                if _item_type and str(_item_type).lower() != media_type:
                    await record_audit_event(
                        event_type="failure",
                        actor=_actor,
                        creator_id=creator_id,
                        user_id=user_id,
                        action="POST /api/vault/send-media",
                        asset_kind="vault_media",
                        asset_id=fangate_media_id,
                        state_before=None,
                        state_after="rejected",
                        result="failure",
                        error="media_type_mismatch",
                    )
                    return JSONResponse(
                        status_code=400,
                        content={"error": "media_type does not match catalogued asset"},
                    )
            else:
                _ownership = "mirror-miss"
        except Exception:
            logger.debug("vault.send-media ownership lookup failed", exc_info=True)
            _ownership = "lookup-failed"

        caption = body.get("caption", "") or ""
        # M6: the previous dedup (user + media only) silently suppressed a
        # resend whose caption was edited. Bind the caption so edited captions
        # deliver under their own identity while identical retries still
        # dedup. Explicit synthetic correlation (never Telegram-derived).
        # M7 dedup shape is UNCHANGED (legacy alias preserved); the canonical
        # snapshot hash below is additive and ignored by existing matchers.
        dedup_id = hashlib.md5(
            f"{user_id}:{fangate_media_id}:{caption}".encode()
        ).hexdigest()
        _vault_gid = manual_generation_id(
            f"vault:{user_id}:{fangate_media_id}:{caption[:32]}"
        )
        # M7 (B8): immutable snapshot identity bound from canonical mirror
        # values when verified, else from the request values (marked by
        # _ownership). Recorded in audit + carried additively in the payload.
        _snapshot_src = _canonical_ref or f"{creator_id}:{fangate_media_id}:{media_type}"
        _asset_hash = content_hash(f"{_snapshot_src}:{caption}")
        await record_audit_event(
            event_type="intent",
            actor=_actor,
            creator_id=creator_id,
            user_id=user_id,
            action="POST /api/vault/send-media",
            content=caption,
            asset_kind="vault_media",
            asset_id=fangate_media_id,
            asset_ref=f"{_snapshot_src}:{caption}",
            generation_id=_vault_gid,
            dedup_id=dedup_id,
            state_before=None,
            state_after="intended",
            result=f"asset_{_ownership}",
        )
        try:
            await enqueue_send(
                {
                    "entity": str(user_id),
                    "content": caption,
                    "draft_content": caption,
                    "was_edited": False,
                    "was_auto_approved": False,
                    "confidence_score": 1.0,
                    "operator_id": None,
                    "save_to_db": True,
                    "media_type": media_type,
                    "media_path": media_path,
                    "fangate_media_id": str(fangate_media_id),
                    "product_id": str(body.get("product_id", "")),
                    "creator_id": str(creator_id),
                    "generation_id": _vault_gid,
                    "actor_type": _actor["actor_type"],
                    "actor_id": _actor["actor_id"],
                    "asset_hash": _asset_hash,
                    "asset_verified": _ownership,
                },
                dedup_id=dedup_id,
                generation_id=_vault_gid,
                creator_id=creator_id,
            )
        except Exception as _vault_enq_exc:
            await record_audit_event(
                event_type="failure",
                actor=_actor,
                creator_id=creator_id,
                user_id=user_id,
                action="POST /api/vault/send-media",
                content=caption,
                asset_kind="vault_media",
                asset_id=fangate_media_id,
                asset_ref=f"{_snapshot_src}:{caption}",
                generation_id=_vault_gid,
                dedup_id=dedup_id,
                state_before="intended",
                state_after="enqueue_failed",
                result="failure",
                error=str(_vault_enq_exc)[:200],
            )
            raise
        await record_audit_event(
            event_type="enqueue",
            actor=_actor,
            creator_id=creator_id,
            user_id=user_id,
            action="POST /api/vault/send-media",
            content=caption,
            asset_kind="vault_media",
            asset_id=fangate_media_id,
            asset_ref=f"{_snapshot_src}:{caption}",
            generation_id=_vault_gid,
            dedup_id=dedup_id,
            state_before="intended",
            state_after="enqueued",
            result="enqueued",
        )
        return {"ok": True, "queued": True}
    except FangateError as exc:
        return JSONResponse(status_code=exc.status_code or 500, content={"error": exc.message})
    except Exception as exc:
        # Phase 1.3 gap-fix: rails refusal surfaces as 422 with flags so the
        # operator can edit and retry (human override loop), same as queue.py.
        # Previously this fell through to generic 500 "Internal error" (flags lost).
        try:
            from core.output_rails import RailsRefusal as _RailsRefusal

            if isinstance(exc, _RailsRefusal):
                return JSONResponse(status_code=422, content={"error": str(exc)})
        except Exception:
            pass
        logger.exception("vault.send-media failed")
        return JSONResponse(status_code=500, content={"error": "Internal error"})
