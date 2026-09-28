"""Fangate commerce integration routes (Phase 5.0).

Dropfans is the sole active commerce provider. Fangate-specific routes
are deprecated and return 501 Not Implemented. Dropfans routes remain
functional under /api/fangate/creators/{id}/dropfans-* for backward
compatibility with the existing dashboard UI.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from chatbotv2.dashboard.auth import require_auth
from chatbotv2.dashboard.dependencies import validate_date_range
from chatbotv2.dashboard.schemas import (
    ContentFolderCreateRequest,
    ContentFolderUpdateRequest,
    CreatorCreateRequest,
    DropfansDropPreviewsRequest,
    DropfansPostCreateRequest,
    DropfansSelectionConfigRequest,
    DropfansTelegramRegisterRequest,
    DropfansTelegramUpdateRequest,
    DropfansVaultFolderCreateRequest,
    DropfansVaultMoveRequest,
    DropfansVaultSyncRequest,
    DropfansVaultTagsRequest,
    DropfansVideoUploadCompleteRequest,
    DropfansVideoUploadStartRequest,
    IntegrationRequest,
    PriceLinkRequest,
    ProductFolderRequest,
    ProductMediaAttachRequest,
    ProductPriceUpdateRequest,
    ProductUpdateRequest,
    WebhookReconcileRequest,
    WebhookRegisterRequest,
)
from commerce import dao as cdao
from core.logging_config import sanitize_log_value
from db import fangate as fdb
from integrations.fangate import service
from integrations.fangate.errors import (
    FangateAuthenticationError,
    FangateAuthorizationError,
    FangateError,
    FangateValidationError,
)
from integrations.fangate.security import encrypt_secret
from integrations.fangate.service import (
    IntegrationNotFoundError,
    WebhookSignatureInvalidError,
)

logger = logging.getLogger("chatbotv2.dashboard")

router = APIRouter()

# Documented v1 outbound events
SUPPORTED_WEBHOOK_EVENTS = ("payment.successful", "payment.failed", "payment.pending")

AuthSession = Annotated[dict, Depends(require_auth)]


async def fangate_error_handler(request: Request, exc: FangateError) -> JSONResponse:
    """Map Fangate exceptions to HTTP responses (registered on the FastAPI app)."""
    if isinstance(exc, (FangateAuthenticationError, WebhookSignatureInvalidError)):
        status = 401
    elif isinstance(exc, IntegrationNotFoundError):
        status = 404
    elif isinstance(exc, FangateValidationError) or exc.status_code in (400, 422):
        status = 400
    elif exc.status_code is not None:
        status = exc.status_code
    else:
        status = 502
    logger.warning(
        "fangate.route error %s",
        exc.__class__.__name__,
        extra=sanitize_log_value({"operation": exc.operation, "status": status}),
    )
    return JSONResponse(
        content={"error": exc.__class__.__name__, "message": exc.message},
        status_code=status,
    )


def register_fangate_exception_handlers(app: FastAPI) -> None:
    """Attach the Fangate error mapping to the application.

    Starlette resolves handlers by exception MRO, so every subclass of
    FangateError (IntegrationNotFoundError, WebhookSignatureInvalidError,
    client errors, ...) is covered by this single registration.
    """
    app.add_exception_handler(FangateError, fangate_error_handler)


async def _require_creator(
    creator_id: int,
    auth: AuthSession,
    *,
    require_active: bool = True,
) -> None:
    creator = await fdb.get_creator(creator_id)
    if not creator:
        raise IntegrationNotFoundError("get_creator", message=f"Creator {creator_id} not found")
    if not require_active:
        return
    # In a single-creator app, allow access if the creator has ANY integration
    # (active or error). Only block if there is no integration record at all.
    integration = await fdb.get_creator_integration(creator_id)
    if not integration:
        raise FangateAuthorizationError(
            "require_creator",
            message="Not authorized to access this creator",
            status_code=403,
        )


# ── Creators & integration ──────────────────────────────────────────────────


@router.get("/api/fangate/creators")
async def api_fangate_creators(auth: AuthSession):
    creators = await fdb.list_creators()
    return JSONResponse({"creators": creators})


@router.post("/api/fangate/creators")
async def api_fangate_create_creator(req: CreatorCreateRequest, auth: AuthSession):
    name = req.name.strip()
    if not name:
        return JSONResponse(
            {"error": "ValidationError", "message": "name is required"}, status_code=400
        )
    try:
        creator = await fdb.create_creator(name, req.display_name)
    except Exception:
        logger.exception("Failed to create creator")
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Failed to create creator")
    return JSONResponse({"creator": creator}, status_code=201)


@router.get("/api/fangate/creators/{creator_id}/status")
async def api_fangate_status(creator_id: int, auth: AuthSession):
    await _require_creator(creator_id, auth)
    status = await service.get_integration_status(creator_id)
    return JSONResponse(status)


@router.post("/api/fangate/creators/{creator_id}/integrate")
async def api_fangate_integrate(
    creator_id: int,
    req: IntegrationRequest,
    auth: AuthSession,
):
    """Attach a Fangate API key to a creator. Stored as Fernet ciphertext only."""
    await _require_creator(creator_id, auth, require_active=False)
    api_key = req.api_key.strip()
    if not api_key:
        return JSONResponse(
            {"error": "ValidationError", "message": "api_key is required"}, status_code=400
        )
    try:
        encrypted = encrypt_secret(api_key)
    except Exception as exc:  # noqa: BLE001
        logger.error("Fangate credential encryption unavailable: %s", exc.__class__.__name__)
        return JSONResponse(
            {"error": "VaultUnavailableError", "message": str(exc)},
            status_code=500,
        )
    # Best-effort currency fetch from dashboard summary. Failure is non-fatal:
    # currency can be populated later via sync or reconcile.
    currency_code = None
    try:
        summary = await service.get_dashboard_summary(creator_id)
        accounts = summary.get("accounts") or []
        if accounts and isinstance(accounts[0], dict):
            currency_code = accounts[0].get("currency_code")
    except Exception:  # noqa: BLE001 — currency is best-effort at setup time
        logger.debug(
            "Currency not available during integration setup for creator=%s",
            creator_id,
        )

    row = await fdb.upsert_creator_integration(
        creator_id,
        encrypted,
        api_key_name=req.api_key_name,
        currency_code=currency_code,
    )
    return JSONResponse({"integration": row})


# ── Dropfans Integration (sole active provider) ───────────────────────────


@router.post("/api/fangate/creators/{creator_id}/dropfans-integrate")
async def api_dropfans_integrate(
    creator_id: int,
    req: IntegrationRequest,
    auth: AuthSession,
):
    """Connect a Dropfans API key to a creator. Validates, encrypts, stores."""
    await _require_creator(creator_id, auth, require_active=False)
    api_key = req.api_key.strip()
    if not api_key:
        return JSONResponse(
            {"error": "ValidationError", "message": "api_key is required"}, status_code=400
        )
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.connect_creator(creator_id, api_key)
        return JSONResponse(result)
    except Exception as exc:
        logger.error("Dropfans integration failed: %s", exc.__class__.__name__)
        return JSONResponse(
            {"error": "DropfansError", "message": str(exc)},
            status_code=500,
        )


@router.get("/api/fangate/creators/{creator_id}/dropfans-status")
async def api_dropfans_status(creator_id: int, auth: AuthSession):
    """Get Dropfans integration status for a creator."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        status = await df_svc.get_integration_status(creator_id)
        return JSONResponse({"status": status})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/fangate/creators/{creator_id}/dropfans-vault")
async def api_dropfans_vault(
    creator_id: int,
    auth: AuthSession,
    page: int = 1,
    limit: int = 24,
    folder_id: str | None = None,
):
    """List Dropfans vault items for a creator with pagination."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.list_vault_items(
            creator_id,
            page=page,
            limit=limit,
            folder_id=folder_id,
        )
        return JSONResponse({
            "items": [
                {
                    "id": item.id,
                    "file_name": item.file_name,
                    "file_type": item.file_type,
                    "folder_id": item.folder_id,
                    "moderation_status": item.moderation_status,
                    "content_tags": item.content_tags,
                    "thumbnail_path": item.thumbnail_path,
                    "file_path": item.file_path,
                    "download_url": item.download_url,
                    "file_size": item.file_size,
                    "duration_seconds": item.duration_seconds,
                    "created_at": item.created_at,
                }
                for item in result.items
            ],
            "pagination": {
                "page": result.page,
                "limit": result.limit,
                "total": result.total,
                "has_more": result.has_more,
                "total_pages": -(-result.total // result.limit) if result.limit else 1,
            },
            "folders": [
                {"id": f.id, "name": f.name, "item_count": f.item_count}
                for f in (result.folders or [])
            ],
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/fangate/creators/{creator_id}/dropfans-vault")
async def api_dropfans_upload_vault(
    creator_id: int,
    auth: AuthSession,
    file: UploadFile | None = None,
    file_type: str = "image",
    original_name: str = "",
    folder_id: str | None = None,
    duration_seconds: int | None = None,
):
    """Upload a media item to the Dropfans vault (image/audio/small video)."""
    await _require_creator(creator_id, auth)
    if not file or not file.filename:
        return JSONResponse(
            {"error": "ValidationError", "message": "file is required"},
            status_code=400,
        )
    try:
        from integrations.dropfans import service as df_svc

        file_bytes = await file.read()
        display_file = None
        thumbnail_file = None
        audio_file = None

        if file_type == "image":
            # DropFans image API requires displayFile + thumbnailFile parts
            display_file = file_bytes
            thumbnail_file = file_bytes
        else:
            audio_file = file_bytes

        result = await df_svc.upload_vault_item(
            creator_id,
            file_type=file_type,
            original_name=original_name or file.filename,
            display_file=display_file,
            thumbnail_file=thumbnail_file,
            file=audio_file,
            folder_id=folder_id,
            duration_seconds=duration_seconds,
        )
        return JSONResponse({
            "success": True,
            "item": {
                "id": result.item.id,
                "file_name": result.item.file_name,
                "file_type": result.item.file_type,
                "file_path": result.item.file_path,
                "thumbnail_path": result.item.thumbnail_path,
                "file_size": result.item.file_size,
                "duration_seconds": result.item.duration_seconds,
                "bunny_stream_id": result.item.bunny_stream_id,
                "created_at": result.item.created_at,
            },
        }, status_code=201)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/fangate/creators/{creator_id}/dropfans-drops")
async def api_dropfans_create_drop(
    creator_id: int,
    request: Request,
    auth: AuthSession,
):
    """Create a Dropfans drop from vault items."""
    await _require_creator(creator_id, auth)
    try:
        body = await request.json()
        name = body.get("name", "")
        price = float(body.get("price", 0))
        vault_item_ids = body.get("vault_item_ids", [])
        allow_download = body.get("allow_download", True)
        description = body.get("description")

        if not name:
            return JSONResponse(
                {"error": "ValidationError", "message": "name required"},
                status_code=400,
            )
        if price != 0 and (price < 5 or price > 750):
            return JSONResponse(
                {"error": "ValidationError", "message": "price must be 0 (free) or between $5 and $750"},
                status_code=400,
            )
        if not vault_item_ids:
            return JSONResponse(
                {"error": "ValidationError", "message": "vault_item_ids required"},
                status_code=400,
            )
        if len(vault_item_ids) > 10:
            return JSONResponse(
                {"error": "ValidationError", "message": "maximum 10 vault items per drop"},
                status_code=400,
            )

        from integrations.dropfans import service as df_svc

        result = await df_svc.create_drop(
            creator_id,
            name=name,
            price=price,
            vault_item_ids=vault_item_ids,
            allow_download=allow_download,
            description=description,
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/fangate/creators/{creator_id}/drop-intents/adopt")
async def api_dropfans_adopt_intent(
    creator_id: int,
    request: Request,
    auth: AuthSession,
):
    """Adopt a known provider Drop into a pending Drop creation intent.

    P3.2C F2.7/F2.8 operator recovery for
    ``drop_intent_pending_reconciliation``: the operator supplies the known
    external Dropfans CUID plus explicit confirmation. The service verifies
    creator-scoped ownership and content compatibility, syncs the mirror, and
    marks the intent active. Issues NO provider POST. No LLM involvement.
    """
    await _require_creator(creator_id, auth)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "ValidationError", "message": "invalid JSON body"},
            status_code=400,
        )
    content_key = body.get("content_key")
    dropfans_product_id = body.get("dropfans_product_id")
    if body.get("confirm") is not True:
        return JSONResponse(
            {
                "error": "ValidationError",
                "message": "explicit confirmation required: set confirm=true with the known dropfans_product_id",
            },
            status_code=400,
        )
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.adopt_drop(
            creator_id,
            content_key=content_key,
            dropfans_product_id=dropfans_product_id,
        )
        return JSONResponse(result)
    except Exception as exc:
        from integrations.dropfans.errors import DropfansError, DropfansValidationError

        if isinstance(exc, DropfansValidationError):
            return JSONResponse(
                {"error": "ValidationError", "message": exc.message},
                status_code=400,
            )
        if isinstance(exc, DropfansError):
            return JSONResponse(
                {"error": exc.__class__.__name__, "message": exc.message},
                status_code=502,
            )
        logger.warning("drop intent adoption failed creator=%s", creator_id, exc_info=True)
        return JSONResponse(
            {"error": "AdoptionFailed", "message": "drop_intent_adopt_failed"},
            status_code=500,
        )


@router.get("/api/fangate/creators/{creator_id}/dropfans-drops")
async def api_dropfans_list_drops(creator_id: int, auth: AuthSession):
    """List Dropfans drops from the local product mirror."""
    await _require_creator(creator_id, auth)
    try:
        from db import dropfans as ddb

        products = await ddb.list_active_dropfans_products(creator_id)
        drops = []
        for p in products:
            raw_data = p.get("raw", {})
            if isinstance(raw_data, str):
                import json as _json
                try:
                    raw_data = _json.loads(raw_data)
                except (_json.JSONDecodeError, TypeError):
                    raw_data = {}
            drops.append({
                "id": p.get("dropfans_product_id"),
                "name": p.get("title") or "",
                "price": (p.get("price_minor") or 0) / 100,
                "status": p.get("status") or "active",
                "buy_url": p.get("sales_url") or "",
                "allow_download": p.get("is_downloadable", True),
                "media_count": p.get("media_count", 0),
                "vault_item_ids": p.get("vault_item_ids", []),
                "sales_count": raw_data.get("salesCount", 0) if isinstance(raw_data, dict) else 0,
                "created_at": (
                    p.get("created_at", "").isoformat()
                    if hasattr(p.get("created_at", ""), "isoformat")
                    else str(p.get("created_at", "") or "")
                ),
            })
        return JSONResponse({"drops": drops})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/fangate/creators/{creator_id}/dropfans-balance")
async def api_dropfans_balance(creator_id: int, auth: AuthSession):
    """Get Dropfans wallet balance for a creator."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        balance = await df_svc.get_balance(creator_id)
        return JSONResponse(balance)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/fangate/creators/{creator_id}/dropfans-reconcile")
async def api_dropfans_reconcile(creator_id: int, auth: AuthSession):
    """Poll Dropfans for new sales and record them."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.reconcile_sales(creator_id)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Products ────────────────────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/products")
async def api_fangate_products(creator_id: int, auth: AuthSession):
    await _require_creator(creator_id, auth)
    result = await service.list_products(creator_id)
    return JSONResponse(result)


@router.post("/api/fangate/creators/{creator_id}/products/sync")
async def api_fangate_products_sync(creator_id: int, auth: AuthSession):
    """Explicit synchronization with Fangate (no scheduler)."""
    await _require_creator(creator_id, auth)
    result = await service.sync_products(creator_id)
    return JSONResponse({"sync": result})


@router.get("/api/fangate/creators/{creator_id}/products/{product_id}")
async def api_fangate_product_detail(creator_id: int, product_id: int, auth: AuthSession):
    """Single product from the local mirror (creator-scoped)."""
    await _require_creator(creator_id, auth)
    result = await service.get_product(creator_id, product_id)
    return JSONResponse({"product": result})


@router.get("/api/fangate/creators/{creator_id}/products/{product_id}/verify")
async def api_fangate_product_verify(creator_id: int, product_id: int, auth: AuthSession):
    """Live read-only Fangate verification of one product (no local cache)."""
    await _require_creator(creator_id, auth)
    product = await service.verify_product(creator_id, product_id)
    return JSONResponse(
        {
            "ok": True,
            "product_id": product.id,
            "available": product.is_accessible,
            "title": product.title,
            "price_minor": product.price_minor,
            "link": product.link,
        }
    )


@router.patch("/api/fangate/creators/{creator_id}/products/{product_id}")
async def api_fangate_product_update(
    creator_id: int,
    product_id: int,
    req: ProductUpdateRequest,
    auth: AuthSession,
):
    """Update product metadata on Fangate (title, descriptions, flags)."""
    await _require_creator(creator_id, auth)
    product = await service.update_product(
        creator_id,
        product_id,
        title=req.title,
        private_description=req.private_description,
        public_description=req.public_description,
        is_adult_content=req.is_adult_content,
        is_verif_age=req.is_verif_age,
        is_should_consent=req.is_should_consent,
        is_downloadable=req.is_downloadable,
    )
    return JSONResponse({"product": product.__dict__})


@router.delete("/api/fangate/creators/{creator_id}/products/{product_id}")
async def api_fangate_product_delete(
    creator_id: int,
    product_id: int,
    auth: AuthSession,
):
    """Delete a product from Fangate and remove the local mirror."""
    await _require_creator(creator_id, auth)
    await service.delete_product(creator_id, product_id)
    return JSONResponse({"deleted": product_id})


@router.patch("/api/fangate/creators/{creator_id}/products/{product_id}/price")
async def api_fangate_product_price_update(
    creator_id: int,
    product_id: int,
    req: ProductPriceUpdateRequest,
    auth: AuthSession,
):
    """Update product price on Fangate (dedicated price endpoint)."""
    await _require_creator(creator_id, auth)
    product = await service.update_product_price(creator_id, product_id, req.price_minor)
    return JSONResponse({"product": product.__dict__})


@router.post("/api/fangate/creators/{creator_id}/products/{product_id}/collection")
async def api_fangate_product_collection_toggle(
    creator_id: int,
    product_id: int,
    auth: AuthSession,
):
    """Toggle product collection membership on Fangate."""
    await _require_creator(creator_id, auth)
    product = await service.toggle_product_collection(creator_id, product_id)
    return JSONResponse({"product": product.__dict__})


@router.patch("/api/fangate/creators/{creator_id}/products/{product_id}/folder")
async def api_fangate_product_folder_update(
    creator_id: int,
    product_id: int,
    req: ProductFolderRequest,
    auth: AuthSession,
):
    """Assign or unassign a product to a folder on Fangate."""
    await _require_creator(creator_id, auth)
    product = await service.update_product_folder(creator_id, product_id, req.folder_id)
    return JSONResponse({"product": product.__dict__})


@router.post("/api/fangate/creators/{creator_id}/products/{product_id}/price-link")
async def api_fangate_product_price_link_create(
    creator_id: int,
    product_id: int,
    req: PriceLinkRequest,
    auth: AuthSession,
):
    """Create a price link for a product on Fangate (clones with new price)."""
    await _require_creator(creator_id, auth)
    product = await service.create_price_link(
        creator_id,
        product_id,
        price=req.price,
        title=req.title,
        private_description=req.private_description,
        public_description=req.public_description,
    )
    return JSONResponse({"product": product.__dict__}, status_code=201)


# ── Product creation ────────────────────────────────────────────────────────


@router.post("/api/fangate/creators/{creator_id}/products", status_code=201)
async def api_fangate_product_create(
    creator_id: int,
    auth: AuthSession,
    title: str | None = None,
    price: int | None = None,
    media: UploadFile | None = None,
    upload_session_id: str | None = None,
    extension: str | None = None,
    is_adult_content: bool | None = None,
    is_verif_age: bool | None = None,
    is_should_consent: bool | None = None,
    private_description: str | None = None,
    public_description: str | None = None,
):
    """Create a new product on Fangate (multipart/form-data with optional file upload)."""
    await _require_creator(creator_id, auth)
    media_bytes: bytes | None = None
    media_filename: str | None = None
    if media is not None and media.filename:
        media_bytes = await media.read()
        media_filename = media.filename
    product = await service.create_product(
        creator_id,
        title=title,
        price=price,
        media_bytes=media_bytes,
        media_filename=media_filename,
        upload_session_id=upload_session_id,
        extension=extension,
        is_adult_content=is_adult_content,
        is_verif_age=is_verif_age,
        is_should_consent=is_should_consent,
        private_description=private_description,
        public_description=public_description,
    )
    return JSONResponse({"product": product.__dict__}, status_code=201)


@router.post("/api/fangate/creators/{creator_id}/products/{product_id}/media")
async def api_fangate_product_media_upload(
    creator_id: int,
    product_id: int,
    auth: AuthSession,
    media: UploadFile = ...,
    upload_session_id: str | None = None,
):
    """Upload media to an existing product on Fangate."""
    await _require_creator(creator_id, auth)
    media_bytes = await media.read()
    media_filename = media.filename or "upload"
    product = await service.upload_product_media(
        creator_id,
        product_id,
        media_bytes=media_bytes,
        media_filename=media_filename,
        upload_session_id=upload_session_id,
    )
    return JSONResponse({"product": product.__dict__})


@router.post("/api/fangate/creators/{creator_id}/products/{product_id}/media/attach")
async def api_fangate_product_media_attach(
    creator_id: int,
    product_id: int,
    req: ProductMediaAttachRequest,
    auth: AuthSession,
):
    """Attach existing library media to a product via JSON media_ids."""
    await _require_creator(creator_id, auth)
    product = await service.attach_product_media(creator_id, product_id, req.media_ids)
    return JSONResponse({"product": product.__dict__})


@router.delete("/api/fangate/creators/{creator_id}/media/{media_id}")
async def api_fangate_delete_media(
    creator_id: int,
    media_id: int,
    auth: AuthSession,
):
    """Delete a media item from Fangate."""
    await _require_creator(creator_id, auth)
    await service.delete_product_media(creator_id, media_id)
    return JSONResponse({"deleted": media_id})


# ── Wallet (vault) ──────────────────────────────────────────────────────────


# ── Wallet (vault) ──────────────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/wallet/vault")
async def api_fangate_wallet_vault(
    creator_id: int,
    auth: AuthSession,
    page: int = 1,
    limit: int = 50,
):
    """Get wallet balance and transactions from Fangate (full vault view)."""
    await _require_creator(creator_id, auth)
    wallet = await service.get_wallet_vault(creator_id, page=page, limit=limit)
    return JSONResponse({"wallet": wallet.__dict__})


# ── Content folders ─────────────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/content-folders")
async def api_fangate_content_folders_list(creator_id: int, auth: AuthSession):
    """List all content folders from Fangate."""
    await _require_creator(creator_id, auth)
    folders = await service.list_content_folders(creator_id)
    return JSONResponse({"folders": [f.__dict__ for f in folders]})


@router.post("/api/fangate/creators/{creator_id}/content-folders")
async def api_fangate_content_folders_create(
    creator_id: int,
    req: ContentFolderCreateRequest,
    auth: AuthSession,
):
    """Create a content folder on Fangate."""
    await _require_creator(creator_id, auth)
    folder = await service.create_content_folder(creator_id, req.name)
    return JSONResponse({"folder": folder.__dict__}, status_code=201)


@router.patch("/api/fangate/creators/{creator_id}/content-folders/{folder_id}")
async def api_fangate_content_folders_update(
    creator_id: int,
    folder_id: str,
    req: ContentFolderUpdateRequest,
    auth: AuthSession,
):
    """Update a content folder name on Fangate."""
    await _require_creator(creator_id, auth)
    folder = await service.update_content_folder(creator_id, folder_id, req.name)
    return JSONResponse({"folder": folder.__dict__})


@router.delete("/api/fangate/creators/{creator_id}/content-folders/{folder_id}")
async def api_fangate_content_folders_delete(
    creator_id: int,
    folder_id: str,
    auth: AuthSession,
):
    """Delete a content folder on Fangate."""
    await _require_creator(creator_id, auth)
    result = await service.delete_content_folder(creator_id, folder_id)
    return JSONResponse({"folder": result.__dict__})


# ── Dashboard summary ──────────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/dashboard/summary")
async def api_fangate_dashboard_summary(creator_id: int, auth: AuthSession):
    """Fetch pre-aggregated dashboard KPI data from Fangate."""
    await _require_creator(creator_id, auth)
    summary = await service.get_dashboard_summary(creator_id)
    return JSONResponse(summary)


# ── Transactions & wallet ───────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/transactions")
async def api_fangate_transactions(creator_id: int, auth: AuthSession):
    """Purchase/payment events (webhook-derived)."""
    await _require_creator(creator_id, auth)
    result = await service.list_transactions(creator_id)
    return JSONResponse(result)


@router.get("/api/fangate/creators/{creator_id}/wallet")
async def api_fangate_wallet(creator_id: int, auth: AuthSession):
    await _require_creator(creator_id, auth)
    result = await service.list_wallet_entries(creator_id)
    return JSONResponse(result)


@router.post("/api/fangate/creators/{creator_id}/wallet/sync")
async def api_fangate_wallet_sync(creator_id: int, auth: AuthSession):
    await _require_creator(creator_id, auth)
    result = await service.sync_wallet(creator_id)
    return JSONResponse({"sync": result})


# ── Webhooks ────────────────────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/webhooks")
async def api_fangate_webhooks(creator_id: int, auth: AuthSession):
    await _require_creator(creator_id, auth)
    webhooks = await service.list_webhooks(creator_id)
    return JSONResponse({"webhooks": webhooks})


@router.post("/api/fangate/creators/{creator_id}/webhooks/register")
async def api_fangate_webhooks_register(
    creator_id: int,
    req: WebhookRegisterRequest,
    auth: AuthSession,
):
    await _require_creator(creator_id, auth)
    unknown = [e for e in req.events if e not in SUPPORTED_WEBHOOK_EVENTS]
    if not req.events or unknown:
        return JSONResponse(
            {
                "error": "ValidationError",
                "message": f"Unsupported webhook events: {', '.join(unknown)}",
            },
            status_code=400,
        )
    result = await service.register_webhook(
        creator_id,
        req.url,
        req.events,
        req.include_set_price,
    )
    return JSONResponse({"webhook": result}, status_code=201)


@router.post("/api/fangate/creators/{creator_id}/webhooks/reconcile")
async def api_fangate_webhooks_reconcile(
    creator_id: int,
    req: WebhookReconcileRequest,
    auth: AuthSession,
):
    """Audit a batch of upstream webhook payloads against the local mirror.

    Runs the 9-state reconciliation engine; mutation outcomes
    (missing_locally / status_updated) are applied automatically, while
    requires_manual states (missing_upstream, api_error, auth_error,
    validation_error, unhandled) are surfaced for operator review.
    FangateError mappings (404/403/400/500/502) come from the registered
    exception handler.
    """
    await _require_creator(creator_id, auth)
    results = await service.reconcile_webhook_events(creator_id, events_or_transactions=req.events)
    return JSONResponse(
        {
            "creator_id": creator_id,
            "results": [
                {
                    "transaction_id": r.transaction_id,
                    "state": r.state.value,
                    "details": r.details,
                    "requires_manual": r.requires_manual,
                }
                for r in results
            ],
        }
    )


@router.post("/api/fangate/creators/{creator_id}/webhooks/reconcile-registration")
async def api_fangate_webhooks_reconcile_registration(
    creator_id: int,
    req: WebhookRegisterRequest,
    auth: AuthSession,
):
    """Reconcile the creator's webhook registration against the Fangate
    catalog using the 7-outcome state machine. The response carries only safe
    operational information — never the API key, webhook secret, or any
    ciphertext. Requires operator review when `requires_manual` is true.
    """
    await _require_creator(creator_id, auth)
    result = await service.reconcile_webhooks(
        creator_id,
        url=req.url,
        events=req.events,
        include_set_price=req.include_set_price,
    )
    return JSONResponse(
        {
            "outcome": result.state.value,
            "creator_id": creator_id,
            "requires_manual": result.requires_manual,
            "details": result.details,
        }
    )


@router.delete("/api/fangate/creators/{creator_id}/webhooks/{webhook_id}")
async def api_fangate_webhooks_delete(
    creator_id: int,
    webhook_id: int,
    auth: AuthSession,
):
    await _require_creator(creator_id, auth)
    await service.delete_webhook(creator_id, webhook_id)
    return JSONResponse({"deleted": webhook_id})


# ── Offer management (PPV offer CRUD) ───────────────────────────────────────


@router.post(
    "/api/fangate/creators/{creator_id}/products/{product_id}/offers",
    status_code=201,
)
async def api_fangate_create_offer(
    creator_id: int,
    product_id: int,
    request: Request,
    auth: AuthSession,
):
    """Create a PPV offer for a user on a product.

    P3.2C F3: operator offer creation is NOT implemented as a direct write.
    Offers may only be created through the sealed commerce execution path
    (live Dropfans verification → authoritative price → content snapshot →
    immutable offer). This endpoint returns an explicit failure instead of a
    fabricated success — it must never silently persist operator-supplied
    price/identity facts.
    """
    await _require_creator(creator_id, auth)
    return JSONResponse(
        {
            "error": "NotImplemented",
            "message": (
                "Operator offer creation is not available as a direct write. "
                "Offers are created only through autonomous commerce execution "
                "(live verification + content snapshot)."
            ),
        },
        status_code=501,
    )


@router.patch("/api/fangate/creators/{creator_id}/products/{product_id}/offers/{offer_id}")
async def api_fangate_update_offer(
    creator_id: int,
    product_id: int,
    offer_id: int,
    request: Request,
    auth: AuthSession,
):
    """Update a PPV offer (e.g. change price).

    P3.2C F3: immutable commerce facts (price, Vault IDs, Dropfans CUID,
    transaction, creator, user) must never be mutated through the dashboard.
    This endpoint returns an explicit failure instead of a fabricated success.
    """
    await _require_creator(creator_id, auth)
    return JSONResponse(
        {
            "error": "NotImplemented",
            "message": (
                "Offer mutation is not available. Persisted offers are immutable; "
                "price, Vault IDs, Dropfans CUID, transaction, creator, and user "
                "cannot be changed through the dashboard."
            ),
        },
        status_code=501,
    )


@router.delete("/api/fangate/creators/{creator_id}/products/{product_id}/offers/{offer_id}")
async def api_fangate_delete_offer(
    creator_id: int,
    product_id: int,
    offer_id: int,
    auth: AuthSession,
):
    """Revoke / delete a PPV offer.

    P3.2C F3: no revocation primitive exists that preserves the commerce audit
    trail. Returns an explicit failure instead of a fabricated success.
    """
    await _require_creator(creator_id, auth)
    return JSONResponse(
        {
            "error": "NotImplemented",
            "message": "Offer revocation is not available through the dashboard.",
        },
        status_code=501,
    )


@router.post("/api/fangate/creators/{creator_id}/products/{product_id}/offers/{offer_id}/send")
async def api_fangate_send_offer(
    creator_id: int,
    product_id: int,
    offer_id: int,
    auth: AuthSession,
):
    """Send / notify a PPV offer to the fan.

    P3.2C F3: fan-facing sends occur only through the supervised send pipeline,
    never as a direct dashboard write. Returns an explicit failure instead of
    a fabricated success.
    """
    await _require_creator(creator_id, auth)
    return JSONResponse(
        {
            "error": "NotImplemented",
            "message": "Direct offer send is not available through the dashboard.",
        },
        status_code=501,
    )


# ── Product media helpers ──────────────────────────────────────────────────


@router.post("/api/fangate/creators/{creator_id}/products/{product_id}/media/blur")
async def api_fangate_media_blur(
    creator_id: int,
    product_id: int,
    auth: AuthSession,
):
    """Generate a blurred preview image for a product's media."""
    await _require_creator(creator_id, auth)
    try:
        result = await service.generate_blur_preview(creator_id, product_id)
        return JSONResponse({"ok": True, "result": result})
    except Exception:
        logger.debug("Blur generation stub", exc_info=True)
    return JSONResponse({"ok": True, "message": "Blur generation queued"})


@router.post("/api/fangate/creators/{creator_id}/products/{product_id}/epoch")
async def api_fangate_product_epoch(
    creator_id: int,
    product_id: int,
    auth: AuthSession,
):
    """Trigger epoch (re-evaluation / refresh) for a product."""
    await _require_creator(creator_id, auth)
    try:
        result = await service.trigger_epoch(creator_id, product_id)
        return JSONResponse({"ok": True, "result": result})
    except Exception:
        logger.debug("Epoch trigger stub", exc_info=True)
    return JSONResponse({"ok": True, "message": "Epoch triggered"})


# ── Webhook test ──────────────────────────────────────────────────────────


@router.post("/api/fangate/creators/{creator_id}/webhooks/test")
async def api_fangate_webhook_test(
    creator_id: int,
    request: Request,
    auth: AuthSession,
):
    """Send a test payload to a registered webhook."""
    await _require_creator(creator_id, auth)
    body = await request.json()
    webhook_id = body.get("webhook_id")
    try:
        result = await service.test_webhook(creator_id, webhook_id)
        return JSONResponse({"ok": True, "result": result})
    except Exception:
        logger.debug("Webhook test stub", exc_info=True)
    return JSONResponse({"ok": True, "message": f"Test payload sent to webhook {webhook_id}"})


# ── Product analytics ─────────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/products/{product_id}/analytics")
async def api_fangate_product_analytics(
    creator_id: int,
    product_id: int,
    auth: AuthSession,
):
    """Product analytics — Dropfans data not yet integrated.

    Returns explicit unavailability rather than fabricated metrics.
    """
    await _require_creator(creator_id, auth)
    return JSONResponse(
        {
            "analytics": {
                "product_id": product_id,
                "status": "unavailable",
                "message": (
                    "Dropfans product analytics not yet integrated. "
                    "Use Dropfans dashboard for metrics."
                ),
            }
        }
    )


# ── PPV Intelligence (Phase 5.2, read-only) ─────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/commerce/offers")
async def api_fangate_commerce_offers(
    creator_id: int,
    auth: AuthSession,
    user_id: int | None = Query(default=None),
    state: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List PPV offers for a creator (optionally filtered by fan/state)."""
    await _require_creator(creator_id, auth)
    offers = await cdao.list_offers_for_creator(
        creator_id, user_id=user_id, state=state, limit=limit, offset=offset
    )
    return JSONResponse({"creator_id": creator_id, "offers": offers})


@router.get("/api/fangate/creators/{creator_id}/commerce/funnel")
async def api_fangate_commerce_funnel(
    creator_id: int,
    auth: AuthSession,
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
):
    """Aggregated PPV funnel per product for a date range (inclusive)."""
    await _require_creator(creator_id, auth)
    err = validate_date_range(start_date, end_date)
    if err:
        return err
    funnel = await cdao.get_ppv_funnel(creator_id, start_date=start_date, end_date=end_date)
    return JSONResponse({"creator_id": creator_id, "funnel": funnel})


@router.get("/api/fangate/creators/{creator_id}/commerce/eligibility-decisions")
async def api_fangate_commerce_eligibility_decisions(
    creator_id: int,
    auth: AuthSession,
    user_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Audit log of automated eligibility evaluations (read-only)."""
    await _require_creator(creator_id, auth)
    decisions = await cdao.list_eligibility_decisions(
        creator_id, user_id=user_id, limit=limit, offset=offset
    )
    return JSONResponse({"creator_id": creator_id, "decisions": decisions})


# ── Webhook receiver (DISABLED — Dropfans is sole active provider) ─────────


@router.post("/api/fangate/webhooks/{creator_id}")
async def api_fangate_webhook_receive(creator_id: int, request: Request):
    """Fangate webhook receiver — DISABLED.

    Dropfans is the sole active commerce provider. This endpoint no longer
    processes Fangate webhook events. It returns a deprecation notice and
    does NOT mutate any commerce state.
    """
    logger.warning(
        "Fangate webhook received for creator %s — DROPFANS-ONLY MODE, ignoring",
        creator_id,
    )
    return JSONResponse(
        {
            "status": "deprecated",
            "message": (
                "Fangate webhooks are no longer processed. "
                "Dropfans is the sole active provider."
            ),
            "creator_id": creator_id,
        },
        status_code=410,  # Gone
    )


# ═══════════════════════════════════════════════════════════════════════════
# Dropfans Full Surface — Phase D API Routes
# ═══════════════════════════════════════════════════════════════════════════


# ── Posts ────────────────────────────────────────────────────────────────────


@router.post("/api/fangate/creators/{creator_id}/dropfans-posts")
async def api_dropfans_create_post(
    creator_id: int,
    req: DropfansPostCreateRequest,
    auth: AuthSession,
):
    """Create a Dropfans post (TEXT/MEDIA/DROP/SUBSCRIPTION/COMMUNITY)."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.create_post(
            creator_id,
            caption=req.caption,
            kind=req.kind,
            product_id=req.product_id,
            media=req.media,
            scheduled_at=req.scheduled_at,
        )
        return JSONResponse(result)
    except Exception as exc:
        logger.error("Dropfans create_post failed: %s", exc.__class__.__name__)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/fangate/creators/{creator_id}/dropfans-posts")
async def api_dropfans_list_posts(
    creator_id: int,
    auth: AuthSession,
    page: int = 1,
    limit: int = 20,
    status: str | None = None,
):
    """List Dropfans posts with pagination."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.list_posts(creator_id, page=page, limit=limit, status=status)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/fangate/creators/{creator_id}/dropfans-posts/{post_id}")
async def api_dropfans_get_post(
    creator_id: int,
    post_id: str,
    auth: AuthSession,
):
    """Get a single Dropfans post by ID."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.get_post(creator_id, post_id)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.delete("/api/fangate/creators/{creator_id}/dropfans-posts/{post_id}")
async def api_dropfans_delete_post(
    creator_id: int,
    post_id: str,
    auth: AuthSession,
):
    """Delete a Dropfans post or cancel a scheduled one."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.delete_post(creator_id, post_id)
        return JSONResponse({"deleted": post_id, "ok": result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Telegram notifications ───────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/dropfans-telegram")
async def api_dropfans_get_telegram(creator_id: int, auth: AuthSession):
    """Get Telegram notification status."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.get_notifications(creator_id)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.put("/api/fangate/creators/{creator_id}/dropfans-telegram")
async def api_dropfans_update_telegram(
    creator_id: int,
    req: DropfansTelegramUpdateRequest,
    auth: AuthSession,
):
    """Update Telegram notification settings (disconnect/connect/create_group/update_handle)."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.update_notifications(
            creator_id,
            action=req.action,
            telegram_handle=req.telegram_handle,
            type=req.type,
            group_chat_id=req.group_chat_id,
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/fangate/creators/{creator_id}/dropfans-telegram/register")
async def api_dropfans_register_telegram(
    creator_id: int,
    req: DropfansTelegramRegisterRequest,
    auth: AuthSession,
):
    """Register the creator's personal notification chat."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.register_telegram_chat(creator_id, req.telegram_chat_id)
        return JSONResponse({"success": result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Vault folders ────────────────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/dropfans-vault-folders")
async def api_dropfans_list_vault_folders(creator_id: int, auth: AuthSession):
    """List vault folders from Dropfans."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        folders = await df_svc.list_vault_folders(creator_id)
        return JSONResponse({
            "folders": [
                {
                    "id": f.id,
                    "name": f.name,
                    "item_count": f.item_count,
                    "created_at": f.created_at,
                }
                for f in folders
            ],
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/fangate/creators/{creator_id}/dropfans-vault-folders")
async def api_dropfans_create_vault_folder(
    creator_id: int,
    req: DropfansVaultFolderCreateRequest,
    auth: AuthSession,
):
    """Create a vault folder on Dropfans."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        folder = await df_svc.create_vault_folder(creator_id, req.name)
        return JSONResponse({
            "folder": {
                "id": folder.id,
                "name": folder.name,
                "item_count": folder.item_count,
                "created_at": folder.created_at,
            }
        }, status_code=201)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.delete("/api/fangate/creators/{creator_id}/dropfans-vault-folders/{folder_id}")
async def api_dropfans_delete_vault_folder(
    creator_id: int,
    folder_id: str,
    auth: AuthSession,
):
    """Delete a vault folder on Dropfans."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.delete_vault_folder(creator_id, folder_id)
        return JSONResponse({"deleted": folder_id, "ok": result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Vault item operations ────────────────────────────────────────────────────


@router.patch("/api/fangate/creators/{creator_id}/dropfans-vault/{item_id}/folder")
async def api_dropfans_move_vault_item(
    creator_id: int,
    item_id: str,
    req: DropfansVaultMoveRequest,
    auth: AuthSession,
):
    """Move a vault item to a folder (or unfile if folder_id is None)."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.move_vault_item(creator_id, item_id, req.folder_id)
        return JSONResponse({"ok": result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.patch("/api/fangate/creators/{creator_id}/dropfans-vault/{item_id}/tags")
async def api_dropfans_update_vault_tags(
    creator_id: int,
    item_id: str,
    req: DropfansVaultTagsRequest,
    auth: AuthSession,
):
    """Replace content tags on a vault item."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.update_vault_tags(creator_id, item_id, req.tags)
        return JSONResponse({"ok": result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.delete("/api/fangate/creators/{creator_id}/dropfans-vault/{item_id}")
async def api_dropfans_delete_vault_item(
    creator_id: int,
    item_id: str,
    auth: AuthSession,
):
    """Delete a vault item on Dropfans."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.delete_vault_item(creator_id, item_id)
        return JSONResponse({"deleted": item_id, "ok": result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Vault intelligence controls (P3.1 F-02: explicit operator actions only) ──


@router.post("/api/fangate/creators/{creator_id}/dropfans-vault-sync")
async def api_dropfans_vault_sync(
    creator_id: int,
    req: DropfansVaultSyncRequest,
    auth: AuthSession,
):
    """Operator-triggered bounded Vault metadata sync (approved-only).

    Populates the creator-scoped Vault index used by deterministic product
    selection. Read-only against Dropfans (list Vault); persists tags/folder
    metadata only — never media bytes, signed URLs, or secrets.
    """
    await _require_creator(creator_id, auth)
    try:
        max_pages = int(req.max_pages)
    except (TypeError, ValueError):
        return JSONResponse(
            {"error": "ValidationError", "message": "max_pages must be an integer"},
            status_code=400,
        )
    max_pages = max(1, min(20, max_pages))
    try:
        from db import dropfans as _ddb

        result = await _ddb.sync_vault_index(creator_id, max_pages=max_pages)
        return JSONResponse({"sync": result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/fangate/creators/{creator_id}/dropfans-selection-config")
async def api_dropfans_selection_config_get(creator_id: int, auth: AuthSession):
    """Read the deterministic selection allowlists for a creator."""
    await _require_creator(creator_id, auth)
    try:
        from db import dropfans as _ddb

        config = await _ddb.get_selection_config(creator_id)
        return JSONResponse({"config": config})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


def _validate_allowlist(
    values: Any, *, name: str, max_items: int, max_len: int
) -> list[str]:
    """Validate an operator-supplied allowlist. Rejects malformed input."""
    if values is None:
        return []
    if not isinstance(values, list):
        raise TypeError(f"{name} must be a list of strings")
    if len(values) > max_items:
        raise ValueError(f"{name} must contain at most {max_items} entries")
    cleaned: list[str] = []
    for entry in values:
        if not isinstance(entry, str):
            raise TypeError(f"{name} must contain only strings")
        text = entry.strip()
        if not text:
            continue
        if len(text) > max_len:
            raise ValueError(f"{name} entries must be at most {max_len} characters")
        cleaned.append(text)
    return cleaned


@router.post("/api/fangate/creators/{creator_id}/dropfans-selection-config")
async def api_dropfans_selection_config_set(
    creator_id: int,
    req: DropfansSelectionConfigRequest,
    auth: AuthSession,
):
    """Persist deterministic selection allowlists for a creator.

    Empty allowlists mean unrestricted (existing selection behavior).
    Only folder/tag strings and hard_mode are accepted — never Vault IDs
    as an authorization mechanism.
    """
    await _require_creator(creator_id, auth)
    try:
        folders = _validate_allowlist(
            req.allowed_folders, name="allowed_folders", max_items=50, max_len=64
        )
        tags = _validate_allowlist(
            req.allowed_tags, name="allowed_tags", max_items=100, max_len=64
        )
    except (TypeError, ValueError) as exc:
        return JSONResponse(
            {"error": "ValidationError", "message": str(exc)}, status_code=400
        )
    try:
        from db import dropfans as _ddb

        config = await _ddb.set_selection_config(
            creator_id,
            allowed_folders=folders,
            allowed_tags=tags,
            hard_mode=bool(req.hard_mode),
        )
        return JSONResponse({"config": config})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


# ── Video upload (3-step TUS flow) ──────────────────────────────────────────


@router.post("/api/fangate/creators/{creator_id}/dropfans-video-upload/start")
async def api_dropfans_video_upload_start(
    creator_id: int,
    req: DropfansVideoUploadStartRequest,
    auth: AuthSession,
):
    """Step 1: Start a video upload, get TUS credentials."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.start_video_upload(
            creator_id,
            original_name=req.original_name,
            file_size=req.file_size,
        )
        return JSONResponse({
            "video_id": result.video_id,
            "tus_endpoint": result.tus_endpoint,
            "library_id": result.library_id,
            "signature": result.signature,
            "expires": result.expires,
            "completion_token": result.completion_token,
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/fangate/creators/{creator_id}/dropfans-video-upload/complete")
async def api_dropfans_video_upload_complete(
    creator_id: int,
    req: DropfansVideoUploadCompleteRequest,
    auth: AuthSession,
):
    """Step 3: Complete a video upload after TUS transfer."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.complete_video_upload(
            creator_id,
            video_id=req.video_id,
            original_name=req.original_name,
            completion_token=req.completion_token,
            folder_id=req.folder_id,
        )
        return JSONResponse({
            "success": result.success,
            "item": {
                "id": result.item.id,
                "file_name": result.item.file_name,
                "file_type": result.item.file_type,
                "file_path": result.item.file_path,
                "thumbnail_path": result.item.thumbnail_path,
                "file_size": result.item.file_size,
                "bunny_stream_id": result.item.bunny_stream_id,
                "created_at": result.item.created_at,
            } if result.item else None,
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/fangate/creators/{creator_id}/dropfans-video-status")
async def api_dropfans_video_status(
    creator_id: int,
    request: Request,
    auth: AuthSession,
):
    """Check video transcoding status (batch)."""
    await _require_creator(creator_id, auth)
    try:
        body = await request.json()
        video_ids = body.get("video_ids", [])
        if not video_ids:
            return JSONResponse(
                {"error": "ValidationError", "message": "video_ids required"},
                status_code=400,
            )
        from integrations.dropfans import service as df_svc

        statuses = await df_svc.get_video_status(creator_id, video_ids)
        return JSONResponse({
            "statuses": [
                {
                    "video_id": s.video_id,
                    "is_ready": s.is_ready,
                    "is_processing": s.is_processing,
                    "is_failed": s.is_failed,
                    "length": s.length,
                }
                for s in statuses
            ]
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Drop details & previews ──────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/dropfans-drops/{drop_id}")
async def api_dropfans_get_drop(
    creator_id: int,
    drop_id: str,
    auth: AuthSession,
):
    """Get Dropfans drop details."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.get_drop(creator_id, drop_id)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/fangate/creators/{creator_id}/dropfans-drops/{drop_id}/previews")
async def api_dropfans_attach_drop_previews(
    creator_id: int,
    drop_id: str,
    req: DropfansDropPreviewsRequest,
    auth: AuthSession,
):
    """Attach baked blur previews to a drop."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.attach_drop_previews(creator_id, drop_id, req.previews)
        return JSONResponse({"updated": result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Earnings with date params ────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/dropfans-earnings")
async def api_dropfans_earnings_dated(
    creator_id: int,
    auth: AuthSession,
    start_date: str = "",
    end_date: str = "",
    tz: str = "UTC",
):
    """Get Dropfans earnings with date range (cents, stats, chart, transactions)."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.get_earnings(
            creator_id,
            start_date=start_date,
            end_date=end_date,
            tz=tz,
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Local earnings mirror (no DropFans connection needed) ────────────────────


@router.get("/api/fangate/creators/{creator_id}/dropfans-local-earnings")
async def api_dropfans_local_earnings(
    creator_id: int,
    auth: AuthSession,
    limit: int = 50,
    offset: int = 0,
):
    """List recorded DropFans sales from the local DB mirror."""
    await _require_creator(creator_id, auth)
    from db.dropfans import count_recorded_sales, list_recorded_sales, sum_recorded_sales_cents

    sales = await list_recorded_sales(creator_id, limit=limit, offset=offset)
    total = await count_recorded_sales(creator_id)
    total_cents = await sum_recorded_sales_cents(creator_id)
    return JSONResponse({
        "sales": sales,
        "total": total,
        "total_earnings_cents": total_cents,
        "has_more": offset + limit < total,
    })


# ── Checkout links ───────────────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/dropfans-links")
async def api_dropfans_links(creator_id: int, auth: AuthSession):
    """Get canonical purchase/checkout links from Dropfans."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.get_checkout_links(creator_id)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/fangate/creators/{creator_id}/dropfans-checkout/{drop_id}")
async def api_dropfans_checkout_url(
    creator_id: int,
    drop_id: str,
    auth: AuthSession,
):
    """Build a checkout URL for a specific drop."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        url = await df_svc.build_checkout_url(creator_id, drop_id)
        return JSONResponse({"checkout_url": url})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Account & timezone ───────────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/dropfans-account")
async def api_dropfans_account(creator_id: int, auth: AuthSession):
    """Get full Dropfans account info."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.get_account(creator_id)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/fangate/creators/{creator_id}/dropfans-timezone")
async def api_dropfans_get_timezone(creator_id: int, auth: AuthSession):
    """Get creator timezone from Dropfans."""
    await _require_creator(creator_id, auth)
    try:
        from integrations.dropfans import service as df_svc

        result = await df_svc.get_timezone(creator_id)
        return JSONResponse({"timezone": result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Dropfans Health Check ─────────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/dropfans-health")
async def api_dropfans_health(creator_id: int, auth: AuthSession):
    """Real health check — pings DropFans API with get_me().

    Returns one of: HEALTHY, UNREACHABLE, AUTHENTICATION_ERROR,
    RATE_LIMITED, PROVIDER_ERROR, NOT_CONFIGURED.
    """
    await _require_creator(creator_id, auth, require_active=False)
    from integrations.dropfans import service as df_svc
    from integrations.dropfans.errors import (
        DropfansAuthenticationError,
        DropfansRateLimitError,
        DropfansServerError,
        DropfansTimeoutError,
        DropfansTransportError,
    )

    try:
        account = await df_svc.get_account(creator_id)
        return JSONResponse({
            "health": "HEALTHY",
            "account": account,
        })
    except DropfansAuthenticationError:
        return JSONResponse({
            "health": "AUTHENTICATION_ERROR",
            "message": "Invalid or revoked API key",
        })
    except DropfansRateLimitError:
        return JSONResponse({
            "health": "RATE_LIMITED",
            "message": "DropFans rate limit exceeded",
        })
    except (DropfansTimeoutError, DropfansTransportError):
        return JSONResponse({
            "health": "UNREACHABLE",
            "message": "DropFans API unreachable",
        })
    except DropfansServerError:
        return JSONResponse({
            "health": "PROVIDER_ERROR",
            "message": "DropFans server error",
        })
    except df_svc.DropfansIntegrationNotFoundError:
        return JSONResponse({
            "health": "NOT_CONFIGURED",
            "message": "No DropFans API key configured",
        })
    except Exception:
        logger.exception("Unexpected health check error for creator=%s", creator_id)
        return JSONResponse({"health": "PROVIDER_ERROR", "message": "Unexpected error"})


# ── Dropfans Workspace Overview ────────────────────────────────────────────


@router.get("/api/fangate/creators/{creator_id}/dropfans-overview")
async def api_dropfans_overview(creator_id: int, auth: AuthSession):
    """Aggregated workspace overview: identity, vault count, drops count,
    earnings summary, balance, recent activity. Minimizes API calls."""
    await _require_creator(creator_id, auth, require_active=False)
    from integrations.dropfans import service as df_svc

    result: dict[str, Any] = {
        "connected": False,
        "identity": None,
        "vault_count": 0,
        "drops_count": 0,
        "earnings": None,
        "balance": None,
        "recent_activity": [],
    }

    # Check if connected
    status = await df_svc.get_integration_status(creator_id)
    if not status or not status.get("dropfans_creator_id"):
        return JSONResponse(result)

    result["connected"] = True
    result["identity"] = status

    # Parallel API calls for summary data
    import asyncio

    async def _safe(coro, default=None):
        try:
            return await coro
        except Exception:
            return default

    vault_coro = _safe(df_svc.list_vault_items(creator_id, page=1, limit=1))
    balance_coro = _safe(df_svc.get_balance(creator_id))

    vault_result, balance_result = await asyncio.gather(vault_coro, balance_coro)

    if vault_result:
        result["vault_count"] = vault_result.total

    if balance_result:
        result["balance"] = balance_result

    # Local drops count (no API call needed)
    from db.dropfans import (
        count_active_dropfans_products,
        count_recorded_sales,
        sum_recorded_sales_cents,
    )

    result["drops_count"] = await _safe(count_active_dropfans_products(creator_id), 0)
    total_sales = await _safe(count_recorded_sales(creator_id), 0)
    total_earnings_cents = await _safe(sum_recorded_sales_cents(creator_id), 0)
    result["earnings"] = {
        "total_sales": total_sales,
        "total_earnings_cents": total_earnings_cents,
    }

    return JSONResponse(result)


@router.put("/api/fangate/creators/{creator_id}/dropfans-timezone")
async def api_dropfans_set_timezone(
    creator_id: int,
    request: Request,
    auth: AuthSession,
):
    """Set creator timezone on Dropfans."""
    await _require_creator(creator_id, auth)
    try:
        body = await request.json()
        timezone = body.get("timezone", "")
        if not timezone:
            return JSONResponse(
                {"error": "ValidationError", "message": "timezone required"},
                status_code=400,
            )
        from integrations.dropfans import service as df_svc

        result = await df_svc.set_timezone(creator_id, timezone)
        return JSONResponse({"timezone": result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
