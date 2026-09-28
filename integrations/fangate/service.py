"""Fangate business service layer.

Orchestrates the FangateClient and the DB mirror while keeping the client
free of business logic. Every operation is creator-scoped and recorded in
the integration status. Credentials are decrypted only for the duration of a
call and never logged or returned by any API.
"""

import asyncio
import datetime as dt
import hmac
import json
import logging
import time
from collections import Counter
from typing import Any

from core.logging_config import sanitize_log_value

from .client import SUPPORTED_WEBHOOK_EVENTS, FangateClient
from .errors import (
    FangateError,
    FangateNotFoundError,
    FangateRateLimitError,
    FangateValidationError,
)
from .models import (
    FangateContentFolder,
    FangateFolderDeleteResult,
    FangateProduct,
    FangateWallet,
    FangateWebhookEventPayload,
    ReconcileEventOutcome,
    ReconcileEventResult,
    ReconcileOutcome,
    ReconcileResult,
    normalize_transaction_status,
)
from .security import decrypt_secret, encrypt_secret

logger = logging.getLogger("integrations.fangate.service")

_RATE_LIMIT_RETRIES = 2
_RATE_LIMIT_BASE_DELAY = 2.0
_PAGE_SIZE = 50


class IntegrationNotFoundError(FangateError):
    """No Fangate integration exists for the requested creator."""


class WebhookSignatureInvalidError(FangateError):
    """X-Fangate-Signature did not verify."""


async def _get_client(creator_id: int) -> FangateClient:
    from db import fangate as fdb

    integration = await fdb.get_creator_integration(creator_id)
    if not integration:
        raise IntegrationNotFoundError(
            "create_client", message=f"Creator {creator_id} has no Fangate integration"
        )
    try:
        api_key = decrypt_secret(integration["encrypted_api_key"])
    except Exception as exc:
        raise FangateError(
            "create_client", message="Fangate credential unavailable", status_code=500
        ) from exc
    return FangateClient(api_key)


def _audit(
    creator_id: int,
    operation: str,
    resource_type: str,
    resource_id: Any,
    success: bool,
    **extra: Any,
) -> None:
    """Record safe operational context. Never passes credentials."""
    entry = {
        "creator": creator_id,
        "operation": operation,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "success": success,
        **extra,
    }
    if success:
        logger.info("fangate.op=%s", operation, extra=sanitize_log_value(entry))
    else:
        logger.warning("fangate.op=%s failed", operation, extra=sanitize_log_value(entry))


async def _run_scoped(creator_id: int, operation: str, resource_type: str, coro):
    """Execute an operation with the creator's client; record status."""
    from db import fangate as fdb

    client = await _get_client(creator_id)
    started = time.monotonic()
    try:
        result = await coro(client)
        await fdb.record_integration_success(creator_id)
        _audit(
            creator_id,
            operation,
            resource_type,
            None,
            True,
            ms=int((time.monotonic() - started) * 1000),
        )
        return result
    except FangateError as exc:
        # Don't record credential/config failures as integration errors —
        # they are operator-fixable (re-integrate) and would immediately
        # re-poison the status on every page load.
        if exc.status_code != 500:
            await fdb.record_integration_error(creator_id, f"{exc.__class__.__name__}: {exc.message}")
        _audit(creator_id, operation, resource_type, None, False, error=exc.__class__.__name__)
        raise
    finally:
        await client.close()


async def _paginate(client: FangateClient, fetch_page, page_size: int = _PAGE_SIZE):
    """Page through a Fangate paginated endpoint with rate-limit retries."""
    page = 1
    while True:
        data = await _with_rate_limit_retries(fetch_page, page, page_size)
        yield data
        if page >= (data.pages_total if hasattr(data, "pages_total") else 1):
            break
        page += 1


async def _with_rate_limit_retries(fn, *args, **kwargs):
    last_exc: FangateRateLimitError | None = None
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            return await fn(*args, **kwargs)
        except FangateRateLimitError as exc:
            last_exc = exc
            delay = (
                exc.retry_after
                if exc.retry_after and exc.retry_after <= 30
                else _RATE_LIMIT_BASE_DELAY
            )
            await asyncio.sleep(delay * (attempt + 1))
    assert last_exc is not None
    raise last_exc


# ── Products ────────────────────────────────────────────────────────────────


async def sync_products(creator_id: int) -> dict[str, Any]:
    """Pull all pages of Fangate products into the local mirror.

    - creator isolated (single creator's token drives all requests)
    - pagination via pages_total
    - rate-limit retries with backoff
    - upsert only — NEVER deletes local rows on a partial/failed response
    """
    from db import fangate as fdb

    counts = {"pages": 0, "products": 0, "upserted": 0}
    existing_before = await fdb.count_fangate_products(creator_id)

    async def _run(client: FangateClient):
        async for page in _paginate(client, client.list_products):
            counts["pages"] += 1
            for product in page.products:
                await fdb.upsert_fangate_product(creator_id, product)
                counts["products"] += 1
                counts["upserted"] += 1
        return dict(counts)

    try:
        result = await _run_scoped(creator_id, "sync_products", "product", _run)
    finally:
        # Stale handling: nothing is deleted; report totals for observability.
        pass
    result["local_total"] = await fdb.count_fangate_products(creator_id)
    result["retained"] = result["local_total"] >= existing_before
    return result


async def list_products(creator_id: int, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    from db import fangate as fdb

    items = await fdb.list_fangate_products(creator_id, limit=limit, offset=offset)
    total = await fdb.count_fangate_products(creator_id)
    return {"items": items, "total": total}


async def list_product_collection(
    creator_id: int, page: int = 1, limit: int = 50
) -> FangateProductPage:
    """List products in the collection via GET /products/collection (API-backed)."""

    async def _run(client: FangateClient):
        return await client.list_product_collection(page=page, limit=limit)

    return await _run_scoped(creator_id, "list_product_collection", "product", _run)


async def get_product(creator_id: int, product_id: int | str) -> dict[str, Any]:
    """Look up a single product from the creator-scoped local mirror.

    Creator scoping is enforced in SQL (WHERE creator_id = $1 AND id = $2):
    requesting another creator's product is indistinguishable from a missing
    product and raises FangateNotFoundError (404) — never a data leak.

    Raises FangateNotFoundError (404) when the product is absent and
    FangateError (500) when the database lookup itself fails.
    """
    from db.fangate import get_fangate_product

    try:
        product = await get_fangate_product(creator_id, product_id)
    except Exception as exc:
        raise FangateError(
            "get_product", message="Fangate product lookup failed", status_code=500
        ) from exc
    if not product:
        raise FangateNotFoundError(
            "get_product",
            message=f"Fangate product {product_id} not found for creator {creator_id}",
            status_code=404,
        )
    return product


async def verify_product(creator_id: int, product_id: int) -> FangateProduct:
    """Live, read-only Fangate lookup of one product (pre-execution check).

    Unlike ``get_product`` (local mirror), this reads the authoritative
    catalog with the creator's own token. It never mutates anything, so a
    timeout/error is always safe to retry later; it only tells the caller
    whether the product still exists remotely and what its authoritative
    link/price are.

    Errors propagate as the mapped Fangate exception hierarchy.
    """

    async def _run(client: FangateClient):
        return await _with_rate_limit_retries(client.get_product, int(product_id))

    return await _run_scoped(creator_id, "verify_product", "product", _run)


async def update_product(
    creator_id: int,
    product_id: int,
    *,
    title: str | None = None,
    private_description: str | None = None,
    public_description: str | None = None,
    is_adult_content: bool | None = None,
    is_verif_age: bool | None = None,
) -> FangateProduct:
    """Update product metadata on Fangate and refresh the local mirror.

    The Fangate API is the authority. The local mirror is updated only
    after a successful remote confirmation.
    """
    from db import fangate as fdb

    async def _run(client: FangateClient):
        product = await client.update_product(
            product_id,
            title=title,
            private_description=private_description,
            public_description=public_description,
            is_adult_content=is_adult_content,
            is_verif_age=is_verif_age,
        )
        await fdb.upsert_fangate_product(creator_id, product)
        return product

    return await _run_scoped(creator_id, "update_product", "product", _run)


async def delete_product(creator_id: int, product_id: int) -> None:
    """Delete a product from Fangate and remove the local mirror.

    The Fangate API is the authority. The local mirror is deleted only
    after a successful remote confirmation. No FK constraints reference
    fangate_products, so hard delete is safe — historical commerce
    records (offers, transactions, analytics) retain their product_id
    as a plain integer and are never orphaned.
    """
    from db import fangate as fdb

    async def _run(client: FangateClient):
        await client.delete_product(product_id)
        await fdb.delete_fangate_product(creator_id, product_id)

    await _run_scoped(creator_id, "delete_product", "product", _run)


async def update_product_price(
    creator_id: int, product_id: int, price_minor: int
) -> FangateProduct:
    """Update product price on Fangate and refresh the local mirror.

    The Fangate API is the authority. The local mirror is updated only
    after a successful remote confirmation. Existing offers retain their
    historical price snapshots.
    """
    from db import fangate as fdb

    async def _run(client: FangateClient):
        product = await client.update_product_price(product_id, price_minor)
        await fdb.upsert_fangate_product(creator_id, product)
        return product

    return await _run_scoped(creator_id, "update_product_price", "product", _run)


async def toggle_product_collection(creator_id: int, product_id: int) -> FangateProduct:
    """Toggle product collection membership on Fangate and refresh the local mirror.

    The Fangate API is the authority. The local mirror is updated only
    after a successful remote confirmation. Toggling creates or removes
    a collection/short link as needed by Fangate.
    """
    from db import fangate as fdb

    async def _run(client: FangateClient):
        product = await client.toggle_product_collection(product_id)
        await fdb.upsert_fangate_product(creator_id, product)
        return product

    return await _run_scoped(creator_id, "toggle_product_collection", "product", _run)


async def update_product_folder(
    creator_id: int, product_id: int, folder_id: int | None
) -> FangateProduct:
    """Assign or unassign a product to a folder on Fangate and refresh the local mirror.

    The Fangate API is the authority. The local mirror is updated only
    after a successful remote confirmation. Pass folder_id=None to unassign.
    """
    from db import fangate as fdb

    async def _run(client: FangateClient):
        product = await client.update_product_folder(product_id, folder_id)
        await fdb.upsert_fangate_product(creator_id, product)
        return product

    return await _run_scoped(creator_id, "update_product_folder", "product", _run)


async def create_price_link(
    creator_id: int,
    product_id: int,
    price: int | None = None,
    title: str | None = None,
    private_description: str | None = None,
    public_description: str | None = None,
) -> FangateProduct:
    """Create a price link for a product on Fangate and refresh the local mirror.

    Price links clone the product with a new price and optional metadata overrides.
    The Fangate API is the authority. The local mirror is updated only after a
    successful remote confirmation.
    """
    from db import fangate as fdb

    async def _run(client: FangateClient):
        product = await client.create_price_link(
            product_id,
            price=price,
            title=title,
            private_description=private_description,
            public_description=public_description,
        )
        await fdb.upsert_fangate_product(creator_id, product)
        return product

    return await _run_scoped(creator_id, "create_price_link", "product", _run)


# ── Product creation ────────────────────────────────────────────────────────


async def create_product(
    creator_id: int,
    *,
    title: str | None = None,
    price: int | None = None,
    media_bytes: bytes | None = None,
    media_filename: str | None = None,
    upload_session_id: str | None = None,
    extension: str | None = None,
    is_adult_content: bool | None = None,
    is_verif_age: bool | None = None,
    is_should_consent: bool | None = None,
    private_description: str | None = None,
    public_description: str | None = None,
) -> FangateProduct:
    """Create a new product on Fangate and persist the local mirror.

    The Fangate API is the authority. The local mirror is created only
    after a successful remote confirmation.
    """
    from db import fangate as fdb

    async def _run(client: FangateClient):
        product = await client.create_product(
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
        await fdb.upsert_fangate_product(creator_id, product)
        return product

    return await _run_scoped(creator_id, "create_product", "product", _run)


async def upload_product_media(
    creator_id: int,
    product_id: int,
    *,
    media_bytes: bytes,
    media_filename: str = "upload",
    upload_session_id: str | None = None,
) -> FangateProduct:
    """Upload media to an existing product and refresh the local mirror.

    .. deprecated::
        This uses the legacy multipart upload path. Prefer
        :func:`attach_product_media` which uses the documented JSON
        ``{"media_ids": [...]}`` approach for n:m library linking.
    """
    from db import fangate as fdb

    async def _run(client: FangateClient):
        product = await client.upload_product_media(
            product_id,
            media_bytes=media_bytes,
            media_filename=media_filename,
            upload_session_id=upload_session_id,
        )
        await fdb.upsert_fangate_product(creator_id, product)
        return product

    return await _run_scoped(creator_id, "upload_product_media", "product", _run)


async def attach_product_media(
    creator_id: int,
    product_id: int,
    media_ids: list[int],
) -> FangateProduct:
    """Attach existing library media to a product and refresh the local mirror.

    Uses the documented JSON ``{"media_ids": [...]}`` approach for n:m
    product-media linking.  The media must already exist in the creator's
    media library.
    """
    from db import fangate as fdb

    async def _run(client: FangateClient):
        product = await client.attach_product_media(product_id, media_ids)
        await fdb.upsert_fangate_product(creator_id, product)
        return product

    return await _run_scoped(creator_id, "attach_product_media", "product", _run)


async def delete_product_media(creator_id: int, media_id: int) -> Any:
    """Delete a media item from Fangate via DELETE /products/media/{media_id}."""

    async def _run(client: FangateClient):
        return await client.delete_product_media(media_id)

    return await _run_scoped(creator_id, "delete_product_media", "media", _run)


# ── Wallet (vault) ──────────────────────────────────────────────────────────


async def get_wallet_vault(
    creator_id: int, page: int = 1, limit: int = 50
) -> FangateWallet:
    """Get wallet balance and transactions from Fangate (API-backed)."""

    async def _run(client: FangateClient):
        return await client.get_wallet_vault(page=page, limit=limit)

    return await _run_scoped(creator_id, "get_wallet_vault", "wallet", _run)


# ── Content folders ─────────────────────────────────────────────────────────


async def list_content_folders(creator_id: int) -> list[FangateContentFolder]:
    """List all content folders from Fangate (API-backed, no local mirror)."""

    async def _run(client: FangateClient):
        return await client.list_content_folders()

    return await _run_scoped(creator_id, "list_content_folders", "content_folder", _run)


async def create_content_folder(creator_id: int, name: str) -> FangateContentFolder:
    """Create a content folder on Fangate (API-backed, no local mirror)."""

    async def _run(client: FangateClient):
        return await client.create_content_folder(name)

    return await _run_scoped(creator_id, "create_content_folder", "content_folder", _run)


async def update_content_folder(creator_id: int, folder_id: str, name: str) -> FangateContentFolder:
    """Update a content folder name on Fangate (API-backed, no local mirror)."""

    async def _run(client: FangateClient):
        return await client.update_content_folder(folder_id, name)

    return await _run_scoped(creator_id, "update_content_folder", "content_folder", _run)


async def delete_content_folder(creator_id: int, folder_id: str) -> FangateFolderDeleteResult:
    """Delete a content folder on Fangate (API-backed, no local mirror)."""

    async def _run(client: FangateClient):
        return await client.delete_content_folder(folder_id)

    return await _run_scoped(creator_id, "delete_content_folder", "content_folder", _run)


# ── Dashboard summary ──────────────────────────────────────────────────────


async def get_dashboard_summary(creator_id: int) -> dict[str, Any]:
    """Fetch the dashboard summary from Fangate (API-backed, no local mirror).

    Returns the pre-aggregated KPI data from GET /api/dashboard/summary.
    """

    async def _run(client: FangateClient):
        return await client.get_dashboard_summary()

    return await _run_scoped(creator_id, "get_dashboard_summary", "dashboard_summary", _run)


# ── Wallet / transactions ───────────────────────────────────────────────────


async def sync_wallet(creator_id: int) -> dict[str, Any]:
    """Mirror the creator's wallet ledger entries (GET /api/wallet)."""
    from db import fangate as fdb

    counts = {"pages": 0, "entries": 0, "upserted": 0}

    async def _run(client: FangateClient):
        pages = 1
        while True:
            wallet = await _with_rate_limit_retries(client.get_wallet, pages, _PAGE_SIZE)
            counts["pages"] += 1
            for entry in wallet.transactions:
                await fdb.upsert_fangate_wallet_entry(creator_id, entry)
                counts["upserted"] += 1
                counts["entries"] += 1
            # Documented: wallet returns a paginated `transactions` collection
            # with no explicit page count; stop when a short page arrives.
            if len(wallet.transactions) < _PAGE_SIZE:
                break
            pages += 1
        return dict(counts)

    result = await _run_scoped(creator_id, "sync_wallet", "wallet_entry", _run)
    result["local_total"] = await fdb.count_fangate_wallet_entries(creator_id)
    return result


async def list_transactions(creator_id: int, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    from db import fangate as fdb

    items = await fdb.list_fangate_transactions(creator_id, limit=limit, offset=offset)
    for item in items:
        # Raw event_type stays untouched (backward-compatible storage schema);
        # `status` is the normalized {successful, failed, pending} view.
        item["status"] = normalize_transaction_status(item.get("event_type"))
    total = await fdb.count_fangate_transactions(creator_id)
    return {"items": items, "total": total}


async def list_wallet_entries(creator_id: int, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    from db import fangate as fdb

    items = await fdb.list_fangate_wallet_entries(creator_id, limit=limit, offset=offset)
    total = await fdb.count_fangate_wallet_entries(creator_id)
    return {"items": items, "total": total}


# ── Webhooks ────────────────────────────────────────────────────────────────


async def register_webhook(
    creator_id: int,
    url: str,
    events: list[str],
    include_set_price: bool = False,
) -> dict[str, Any]:
    """Register an outbound webhook with Fangate and persist the secret
    (encrypted, shown only once by Fangate)."""
    from db import fangate as fdb

    async def _run(client: FangateClient):
        webhook = await client.create_webhook(url, events, include_set_price)
        encrypted_secret = encrypt_secret(webhook.secret) if webhook.secret else None
        await fdb.update_integration_webhook(creator_id, webhook.id, encrypted_secret)
        return {
            "id": webhook.id,
            "url": webhook.url,
            "events": webhook.events,
            "include_set_price": webhook.include_set_price,
        }

    return await _run_scoped(creator_id, "register_webhook", "webhook", _run)


async def list_webhooks(creator_id: int) -> list[dict[str, Any]]:
    async def _run(client: FangateClient):
        hooks = await client.list_webhooks()
        return [
            {
                "id": h.id,
                "url": h.url,
                "events": h.events,
                "include_set_price": h.include_set_price,
                "is_active": h.is_active,
            }
            for h in hooks
        ]

    return await _run_scoped(creator_id, "list_webhooks", "webhook", _run)


async def delete_webhook(creator_id: int, webhook_id: int) -> None:
    from db import fangate as fdb

    async def _run(client: FangateClient):
        await client.delete_webhook(webhook_id)
        await fdb.update_integration_webhook(creator_id, None, None)

    await _run_scoped(creator_id, "delete_webhook", "webhook", _run)


async def update_webhook(
    creator_id: int,
    webhook_id: int,
    *,
    url: str | None = None,
    events: list[str] | None = None,
    include_set_price: bool | None = None,
    is_active: bool | None = None,
) -> dict[str, Any]:
    async def _run(client: FangateClient):
        hook = await client.update_webhook(
            webhook_id,
            url=url,
            events=events,
            include_set_price=include_set_price,
            is_active=is_active,
        )
        return {"id": hook.id, "url": hook.url, "events": hook.events, "is_active": hook.is_active}

    return await _run_scoped(creator_id, "update_webhook", "webhook", _run)


# ── Webhook reception ───────────────────────────────────────────────────────


def verify_webhook_signature(secret: str, body: bytes, signature: str | None) -> bool:
    """Verify X-Fangate-Signature (`sha256={hex}` = HMAC-SHA256 of raw body)."""
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + _hmac_hex(secret, body)
    return hmac.compare_digest(expected, signature)


def _hmac_hex(secret: str, body: bytes) -> str:
    import hashlib

    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def receive_webhook(
    creator_id: int,
    body: bytes,
    signature: str | None,
    delivery_id: str | None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Validate, persist, and process an outbound webhook delivery.

    Idempotency layers (in order):
    1. UNIQUE (creator_id, delivery_id) in fangate_webhook_events
    2. UNIQUE (creator_id, transaction_id, event_type) in fangate_transactions

    Raises WebhookSignatureInvalidError on bad signatures, IntegrationNotFoundError
    when the creator has no integration/secret.
    """
    from db import fangate as fdb

    integration = await fdb.get_creator_integration(creator_id)
    if not integration or not integration.get("encrypted_webhook_secret"):
        logger.warning("fangate.webhook rejected: no secret for creator=%s", creator_id)
        raise IntegrationNotFoundError(
            "receive_webhook", message=f"Creator {creator_id} has no registered webhook secret"
        )

    try:
        secret = decrypt_secret(integration["encrypted_webhook_secret"])
    except Exception as exc:
        logger.error("fangate.webhook rejected: secret decryption failed creator=%s", creator_id)
        raise FangateError(
            "receive_webhook", message="Webhook secret unavailable", status_code=500
        ) from exc

    valid = verify_webhook_signature(secret, body, signature)
    if not valid:
        _audit(
            creator_id,
            "receive_webhook",
            "webhook_event",
            delivery_id,
            False,
            reason="invalid_signature",
        )
        raise WebhookSignatureInvalidError(
            "receive_webhook", message="Invalid Fangate webhook signature"
        )

    if not delivery_id:
        _audit(
            creator_id,
            "receive_webhook",
            "webhook_event",
            None,
            False,
            reason="missing_delivery_id",
        )
        raise FangateError(
            "receive_webhook", message="Missing X-Fangate-Delivery-Id", status_code=400
        )

    import json as _json

    try:
        payload = _json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise FangateError(
            "receive_webhook", message="Malformed JSON payload", status_code=400
        ) from exc
    if not isinstance(payload, dict):
        raise FangateError("receive_webhook", message="Malformed payload", status_code=400)

    try:
        inserted = await fdb.insert_fangate_webhook_event(
            creator_id, delivery_id, payload.get("event", ""), payload, signature_valid=True
        )
    except Exception as exc:
        raise FangateError(
            "receive_webhook", message="Webhook persistence failed", status_code=500
        ) from exc
    if not inserted:
        # Idempotency: delivery already recorded. If the previous attempt
        # completed, skip. If it FAILED mid-processing (processed=False),
        # resume deterministically — downstream upserts are idempotent.
        try:
            existing = await fdb.get_fangate_webhook_event(creator_id, delivery_id)
        except Exception as exc:
            raise FangateError(
                "receive_webhook", message="Webhook persistence failed", status_code=500
            ) from exc
        if existing and existing.get("processed"):
            logger.info(
                "fangate.webhook duplicate delivery ignored creator=%s delivery=%s",
                creator_id,
                delivery_id,
            )
            return {"duplicate": True, "processed": False}
        if not existing:
            # Record vanished between insert and here — treat as a fresh delivery.
            logger.warning(
                "fangate.webhook previously recorded delivery missing creator=%s delivery=%s",
                creator_id,
                delivery_id,
            )
            return {"duplicate": True, "processed": False}

    parsed = FangateWebhookEventPayload.from_api(payload)
    event_type = payload.get("event")
    if not parsed.transaction_id:
        # Persisted raw event; nothing to mirror downstream.
        logger.info(
            "fangate.webhook %s without transaction_id ignored creator=%s", event_type, creator_id
        )
        await fdb.mark_webhook_event_processed(creator_id, delivery_id)
        return {"duplicate": False, "processed": False, "recorded": False}

    try:
        occurred_at = dt.datetime.fromisoformat(parsed.timestamp)
    except (ValueError, AttributeError, TypeError):
        occurred_at = None

    try:
        created = await fdb.upsert_fangate_transaction(
            creator_id,
            parsed.transaction_id,
            event_type,
            buyer_email=parsed.buyer_email,
            seller_earning=parsed.seller_earning,
            currency=parsed.currency,
            product_id=parsed.product_id,
            set_price=parsed.set_price,
            occurred_at=occurred_at,
            delivery_id=delivery_id,
        )
        await fdb.mark_webhook_event_processed(creator_id, delivery_id)
    except Exception as exc:
        raise FangateError(
            "receive_webhook", message="Webhook persistence failed", status_code=500
        ) from exc

    # ── Purchase attribution (best-effort) ──────────────────────────────────
    # After the transaction is persisted, attempt to link it to a pending
    # commerce offer. Attribution is deterministic: it only succeeds when
    # exactly one redeemable offer exists for the product. Multiple candidates
    # are a fail-closed ambiguity (no attribution). Attribution failure never
    # blocks webhook processing — the transaction itself is valid regardless.
    attribution_record = None
    if parsed.product_id and parsed.transaction_id:
        try:
            from commerce.dao import attribute_purchase_from_webhook

            # Convert seller_earning to integer minor units for analytics.
            revenue_minor = 0
            if parsed.seller_earning is not None:
                try:
                    revenue_minor = int(round(float(parsed.seller_earning) * 100))
                except (ValueError, TypeError):
                    revenue_minor = 0

            attribution_record = await attribute_purchase_from_webhook(
                creator_id=creator_id,
                product_id=int(parsed.product_id),
                transaction_id=parsed.transaction_id,
                revenue_minor=revenue_minor,
                occurred_at=occurred_at,
            )
        except Exception:  # noqa: BLE001 — attribution must never break webhook processing
            logger.warning(
                "fangate.webhook attribution failed creator=%s txn=%s",
                creator_id,
                parsed.transaction_id,
                exc_info=True,
            )

    # ── Post-purchase side effects (best-effort) ───────────────────────────
    # After successful attribution, advance funnel and enqueue confirmation.
    # Best-effort: failures are logged but never affect the webhook response.
    if attribution_record is not None:
        try:
            from commerce.post_purchase import handle_post_purchase

            await handle_post_purchase(attribution_record)
        except Exception:  # noqa: BLE001 — post-purchase must never break webhook processing
            logger.warning(
                "fangate.webhook post-purchase failed creator=%s txn=%s",
                creator_id,
                parsed.transaction_id,
                exc_info=True,
            )

    _audit(
        creator_id,
        "receive_webhook",
        "fangate_transaction",
        parsed.transaction_id,
        True,
        event=event_type,
        recorded=created,
        attributed=attribution_record is not None,
    )
    return {
        "duplicate": False,
        "processed": True,
        "recorded": created,
        "attributed": attribution_record is not None,
    }


# ── Webhook-event reconciliation (per-record audit) ─────────────────────────


async def reconcile_webhook_events(
    creator_id: int,
    events_or_transactions: list[dict[str, Any]],
) -> list[ReconcileEventResult]:
    """Audit a batch of upstream webhook payloads against the local mirror
    and repair discrepancies deterministically (9-state machine).

    Batch precondition (Step 1 — credential/vault validation):
        - No integration row            -> IntegrationNotFoundError
        - status != 'active' or no key  -> FangateError (403)
        - secret decrypt failure        -> FangateError (500)

    Per-record state machine (Step 3) — exactly one outcome per record:

        api_error / auth_error annotations (from an upstream audit call)
            -> API_ERROR / AUTH_ERROR                      (no mutation)
        missing transaction_id                             -> VALIDATION_ERROR
        signature provided but missing/unverifiable fields -> VALIDATION_ERROR
        upstream_found=False:
            local exists   -> MISSING_UPSTREAM             (no mutation)
            local absent   -> MATCHED                      (converged)
        delivery event exists AND processed AND txn matches
                                                           -> ALREADY_PROCESSED
        local txn absent            -> backfill + repair   -> MISSING_LOCALLY
        local txn event differs     -> status update       -> STATUS_UPDATED
        local txn event matches     -> repair delivery     -> MISSING_LOCALLY /
                                                               STATUS_UPDATED
        unexpected per-item error                          -> UNHANDLED

    Mutations (Step 4) — raw asyncpg bound parameters only; every mutation is
    idempotent (ON CONFLICT ... DO NOTHING or guarded UPDATE). Step 5: each
    item is isolated so one failure never aborts the batch.

    Input item shapes (all optional):
        transaction_id, event, delivery_id, timestamp, buyer_email,
        seller_earning, currency, product_id, set_price, signature,
        raw_body, upstream_found, api_error, auth_error
    """
    import asyncpg

    from db import fangate as fdb
    from db.postgres import get_pool

    integration = await fdb.get_creator_integration(creator_id)
    if not integration:
        raise IntegrationNotFoundError(
            "reconcile_webhooks",
            message=f"Creator {creator_id} has no Fangate integration",
        )
    if integration.get("status") != "active" or not integration.get("encrypted_api_key"):
        raise FangateError(
            "reconcile_webhooks",
            message=f"Creator {creator_id} integration is inactive or has no credentials",
            status_code=403,
        )

    secret: str | None = None
    if integration.get("encrypted_webhook_secret"):
        try:
            secret = decrypt_secret(integration["encrypted_webhook_secret"])
        except Exception as exc:
            raise FangateError(
                "reconcile_webhooks",
                message="Fangate credential unavailable",
                status_code=500,
            ) from exc

    delivery_ids = [str(i["delivery_id"]) for i in events_or_transactions if i.get("delivery_id")]
    transaction_ids = [
        str(i["transaction_id"]) for i in events_or_transactions if i.get("transaction_id")
    ]

    pool = await get_pool()
    async with pool.acquire() as conn:
        event_rows = (
            await conn.fetch(
                """
                SELECT delivery_id, event, processed
                FROM fangate_webhook_events
                WHERE creator_id = $1 AND delivery_id = ANY($2::text[])
                """,
                creator_id,
                delivery_ids,
            )
            if delivery_ids
            else []
        )
        txn_rows = (
            await conn.fetch(
                """
                SELECT transaction_id, event_type
                FROM fangate_transactions
                WHERE creator_id = $1 AND transaction_id = ANY($2::text[])
                """,
                creator_id,
                transaction_ids,
            )
            if transaction_ids
            else []
        )

    local_events = {
        r["delivery_id"]: {"event": r["event"], "processed": r["processed"]} for r in event_rows
    }
    local_txns = {r["transaction_id"]: r["event_type"] for r in txn_rows}

    results: list[ReconcileEventResult] = []

    def _result(
        state: ReconcileEventOutcome, txn_id: str | None, details: str
    ) -> ReconcileEventResult:
        return ReconcileEventResult(creator_id, txn_id, state, details)

    for item in events_or_transactions:
        txn_id = (
            str(item["transaction_id"]) if item.get("transaction_id") not in (None, "") else None
        )
        try:
            if item.get("api_error"):
                results.append(
                    _result(ReconcileEventOutcome.API_ERROR, txn_id, str(item["api_error"]))
                )
                continue
            if item.get("auth_error"):
                results.append(
                    _result(ReconcileEventOutcome.AUTH_ERROR, txn_id, str(item["auth_error"]))
                )
                continue
            if not txn_id:
                results.append(
                    _result(ReconcileEventOutcome.VALIDATION_ERROR, None, "missing transaction_id")
                )
                continue

            signature = item.get("signature")
            raw_body = item.get("raw_body")
            if signature is not None or raw_body is not None:
                if signature is None or raw_body is None or secret is None:
                    results.append(
                        _result(
                            ReconcileEventOutcome.VALIDATION_ERROR,
                            txn_id,
                            "signature cannot be verified (missing fields or no secret)",
                        )
                    )
                    continue
                if not verify_webhook_signature(
                    secret, str(raw_body).encode("utf-8"), str(signature)
                ):
                    results.append(
                        _result(ReconcileEventOutcome.VALIDATION_ERROR, txn_id, "invalid signature")
                    )
                    continue

            if item.get("upstream_found") is False:
                if txn_id in local_txns:
                    results.append(
                        _result(
                            ReconcileEventOutcome.MISSING_UPSTREAM,
                            txn_id,
                            "local record retained; not found upstream (may be orphaned)",
                        )
                    )
                else:
                    results.append(
                        _result(
                            ReconcileEventOutcome.MATCHED, txn_id, "absent upstream and locally"
                        )
                    )
                continue

            parsed = FangateWebhookEventPayload.from_api(item)
            top_event = item.get("event")
            event_type = top_event if isinstance(top_event, str) and top_event else parsed.event

            local_txn_event = local_txns.get(txn_id)

            async with pool.acquire() as conn:
                delivery_id = str(item["delivery_id"]) if item.get("delivery_id") else None
                local_event = local_events.get(delivery_id) if delivery_id else None

                if local_txn_event is None:
                    await conn.execute(
                        """
                        INSERT INTO fangate_transactions
                            (creator_id, transaction_id, event_type, buyer_email,
                             seller_earning, currency, product_id, set_price,
                             occurred_at, delivery_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        ON CONFLICT (creator_id, transaction_id, event_type) DO NOTHING
                        """,
                        creator_id,
                        txn_id,
                        event_type,
                        parsed.buyer_email
                        if parsed.buyer_email is not None
                        else item.get("buyer_email"),
                        parsed.seller_earning
                        if parsed.seller_earning is not None
                        else item.get("seller_earning"),
                        parsed.currency if parsed.currency is not None else item.get("currency"),
                        parsed.product_id
                        if parsed.product_id is not None
                        else item.get("product_id"),
                        parsed.set_price if parsed.set_price is not None else item.get("set_price"),
                        _parse_occurred_at(item.get("timestamp")),
                        delivery_id,
                    )
                    local_txns[txn_id] = event_type
                    outcome = ReconcileEventOutcome.MISSING_LOCALLY
                elif local_txn_event != event_type:
                    try:
                        await conn.execute(
                            """
                            UPDATE fangate_transactions
                            SET event_type = $3
                            WHERE creator_id = $1 AND transaction_id = $2
                              AND event_type IS DISTINCT FROM $3
                            """,
                            creator_id,
                            txn_id,
                            event_type,
                        )
                    except asyncpg.UniqueViolationError:
                        outcome = ReconcileEventOutcome.MATCHED
                    else:
                        local_txns[txn_id] = event_type
                        outcome = ReconcileEventOutcome.STATUS_UPDATED
                else:
                    outcome = ReconcileEventOutcome.MATCHED

                if (
                    local_event is not None
                    and local_event["processed"]
                    and local_event["event"] == event_type
                    and outcome == ReconcileEventOutcome.MATCHED
                ):
                    results.append(
                        _result(
                            ReconcileEventOutcome.ALREADY_PROCESSED,
                            txn_id,
                            "delivery already processed",
                        )
                    )
                    continue

                if delivery_id:
                    await conn.execute(
                        """
                        INSERT INTO fangate_webhook_events
                            (creator_id, delivery_id, event, payload, signature_valid)
                        VALUES ($1, $2, $3, $4::jsonb, $5)
                        ON CONFLICT (creator_id, delivery_id) DO NOTHING
                        """,
                        creator_id,
                        delivery_id,
                        event_type,
                        json.dumps(item),
                        item.get("signature") is not None,
                    )
                    if local_event is None:
                        local_events[delivery_id] = {"event": event_type, "processed": False}
                        outcome = ReconcileEventOutcome.MISSING_LOCALLY
                    elif not local_event["processed"]:
                        outcome = ReconcileEventOutcome.STATUS_UPDATED

                    await conn.execute(
                        """
                        UPDATE fangate_webhook_events SET processed = TRUE
                        WHERE creator_id = $1 AND delivery_id = $2 AND NOT processed
                        """,
                        creator_id,
                        delivery_id,
                    )
                    if local_event is not None:
                        local_events[delivery_id] = {"event": event_type, "processed": True}

            details = {
                ReconcileEventOutcome.MISSING_LOCALLY: "backfilled from upstream",
                ReconcileEventOutcome.STATUS_UPDATED: "local state updated to match upstream",
                ReconcileEventOutcome.MATCHED: "already in sync",
            }.get(outcome, "")
            results.append(_result(outcome, txn_id, details))
        except Exception as exc:  # noqa: BLE001 — per-item isolation (Step 5)
            logger.warning(
                "fangate.reconcile item failed creator=%s txn=%s error=%s",
                creator_id,
                txn_id,
                exc.__class__.__name__,
            )
            results.append(
                _result(ReconcileEventOutcome.UNHANDLED, txn_id, f"{exc.__class__.__name__}: {exc}")
            )

    summary = ", ".join(
        f"{state.value}={n}" for state, n in Counter(r.state for r in results).items()
    )
    logger.info("fangate.reconcile creator=%s items=%d (%s)", creator_id, len(results), summary)
    return results


# ── Webhook registration reconciliation ─────────────────────────────────────


def _reconcile_result(creator_id: int, state: ReconcileOutcome, details: str) -> ReconcileResult:
    return ReconcileResult(creator_id, state, details)


def _webhook_config_matches(
    webhook: Any,
    url: str,
    events: list[str],
    include_set_price: bool,
) -> bool:
    return (
        webhook.url == url
        and sorted(webhook.events or []) == sorted(events or [])
        and bool(webhook.include_set_price) == bool(include_set_price)
    )


async def _recreate_registered_webhook(
    fdb,
    client: FangateClient,
    creator_id: int,
    url: str,
    events: list[str],
    include_set_price: bool,
    *,
    outcome: ReconcileOutcome,
) -> ReconcileResult:
    try:
        webhook = await client.create_webhook(url, events, include_set_price)
    except FangateError as exc:
        return _reconcile_result(
            creator_id,
            ReconcileOutcome.RECONCILIATION_FAILED,
            f"webhook recreation failed: {exc.__class__.__name__}: {exc.message}",
        )
    if not webhook.secret:
        return _reconcile_result(
            creator_id,
            ReconcileOutcome.PERSISTENCE_FAILED,
            "replacement webhook created without a secret; nothing persisted",
        )
    encrypted = encrypt_secret(webhook.secret)
    try:
        await fdb.update_integration_webhook(creator_id, webhook.id, encrypted)
    except Exception as exc:  # noqa: BLE001 — persistence failure isolation (Rule 8)
        logger.warning(
            "fangate.reconcile persistence failed creator=%s error=%s",
            creator_id,
            exc.__class__.__name__,
        )
        return _reconcile_result(
            creator_id,
            ReconcileOutcome.PERSISTENCE_FAILED,
            "replacement webhook created but local persistence failed",
        )
    return _reconcile_result(creator_id, outcome, "replacement webhook created and persisted")


async def _replace_orphan_webhook(
    fdb,
    client: FangateClient,
    creator_id: int,
    remote_id: int,
    url: str,
    events: list[str],
    include_set_price: bool,
) -> ReconcileResult:
    try:
        await client.delete_webhook(remote_id)
    except FangateNotFoundError:
        # Rule 9/CP5-18: a 404 during deletion means the orphan is already
        # gone — recreate safely without claiming deletion is required.
        pass
    except FangateError as exc:
        return _reconcile_result(
            creator_id,
            ReconcileOutcome.RECONCILIATION_FAILED,
            f"orphan deletion failed: {exc.__class__.__name__}: {exc.message}",
        )
    return await _recreate_registered_webhook(
        fdb,
        client,
        creator_id,
        url,
        events,
        include_set_price,
        outcome=ReconcileOutcome.ORPHAN_REPLACED,
    )


async def _reconcile_webhook_registration(
    fdb,
    client: FangateClient,
    creator_id: int,
    integration: dict[str, Any],
    url: str,
    events: list[str],
    include_set_price: bool,
) -> ReconcileResult:
    try:
        remote_hooks = await client.list_webhooks()
    except FangateError as exc:  # catalog failure is an outcome
        return _reconcile_result(
            creator_id,
            ReconcileOutcome.RECONCILIATION_FAILED,
            f"webhook catalog unavailable: {exc.__class__.__name__}: {exc.message}",
        )

    remote_by_id = {h.id: h for h in remote_hooks}
    url_matches = [h for h in remote_hooks if h.url == url]
    local_id = integration.get("webhook_id")

    secret: str | None = None
    if integration.get("encrypted_webhook_secret"):
        try:
            secret = decrypt_secret(integration["encrypted_webhook_secret"])
        except Exception:  # noqa: BLE001 — see RULE 3: secret unusable = orphan
            secret = None

    if local_id is not None:
        remote = remote_by_id.get(local_id)
        if remote is None:
            # RULE 6 — known webhook vanished remotely; configuration is known.
            return await _recreate_registered_webhook(
                fdb,
                client,
                creator_id,
                url,
                events,
                include_set_price,
                outcome=ReconcileOutcome.REMOTE_WEBHOOK_NOT_FOUND,
            )
        if _webhook_config_matches(remote, url, events, include_set_price):
            if secret is not None:
                if remote.is_active is False:
                    # RULE 2 — reactivate the known webhook (no identity doubt).
                    try:
                        await client.update_webhook(local_id, is_active=True)
                    except FangateError as exc:
                        return _reconcile_result(
                            creator_id,
                            ReconcileOutcome.RECONCILIATION_FAILED,
                            f"webhook reactivation failed: {exc.__class__.__name__}: {exc.message}",
                        )
                    return _reconcile_result(
                        creator_id,
                        ReconcileOutcome.NO_ACTION_REQUIRED,
                        "known webhook reactivated",
                    )
                return _reconcile_result(
                    creator_id,
                    ReconcileOutcome.NO_ACTION_REQUIRED,
                    "webhook is in sync",
                )
            # RULE 3 — secret unusable but identity is strong (id + url): replace.
            return await _replace_orphan_webhook(
                fdb,
                client,
                creator_id,
                local_id,
                url,
                events,
                include_set_price,
            )
        # Configuration differs.
        if secret is not None:
            # RULE 2 — update, do not recreate unnecessarily.
            try:
                await client.update_webhook(
                    local_id,
                    url=url,
                    events=events,
                    include_set_price=include_set_price,
                )
            except FangateError as exc:
                return _reconcile_result(
                    creator_id,
                    ReconcileOutcome.RECONCILIATION_FAILED,
                    f"webhook update failed: {exc.__class__.__name__}: {exc.message}",
                )
            return _reconcile_result(
                creator_id,
                ReconcileOutcome.NO_ACTION_REQUIRED,
                "known webhook updated to expected configuration",
            )
        if remote.url == url:
            # RULE 3 — secret unusable + identity strong: replace.
            return await _replace_orphan_webhook(
                fdb,
                client,
                creator_id,
                local_id,
                url,
                events,
                include_set_price,
            )
        # RULE 3 — weak identity (known id, unrelated url): never guess.
        return _reconcile_result(
            creator_id,
            ReconcileOutcome.ORPHAN_FOUND,
            "orphan detected: known webhook id has an unrelated URL and no usable secret; "
            "manual review required",
        )

    # No known local webhook id.
    if not url_matches:
        # RULE 7 — never manufacture a webhook unexpectedly.
        return _reconcile_result(
            creator_id,
            ReconcileOutcome.NO_ACTION_REQUIRED,
            "no remote webhook for this URL",
        )
    if len(url_matches) > 1:
        # RULE 5 — ambiguous; zero destructive mutations.
        return _reconcile_result(
            creator_id,
            ReconcileOutcome.AMBIGUOUS,
            f"{len(url_matches)} remote webhooks match this URL; no changes made",
        )
    # RULE 4 — exactly one orphan with the expected URL: delete + recreate.
    return await _replace_orphan_webhook(
        fdb,
        client,
        creator_id,
        url_matches[0].id,
        url,
        events,
        include_set_price,
    )


async def reconcile_webhooks(
    creator_id: int,
    *,
    url: str,
    events: list[str] | None = None,
    include_set_price: bool = False,
) -> ReconcileResult:
    """Reconcile the creator's webhook registration against Fangate's catalog.

    Deterministic 7-outcome state machine (Phase 5.1 rules 1-9). Uses the
    existing scoped client (Bearer auth), Fernet credential handling, typed
    client models, and sanitized auditing. Credentials never leave the client
    and are never logged or returned.

    Raises IntegrationNotFoundError when the creator has no integration and
    FangateValidationError for unsupported event names.
    """
    from db import fangate as fdb

    integration = await fdb.get_creator_integration(creator_id)
    if not integration:
        raise IntegrationNotFoundError(
            "reconcile_webhooks",
            message=f"Creator {creator_id} has no Fangate integration",
        )

    expected_events = list(events) if events is not None else list(SUPPORTED_WEBHOOK_EVENTS)
    unknown = [e for e in expected_events if e not in SUPPORTED_WEBHOOK_EVENTS]
    if not expected_events or unknown:
        raise FangateValidationError(
            "reconcile_webhooks",
            message=f"Unsupported webhook events: {', '.join(unknown)}",
        )

    client = await _get_client(creator_id)
    try:
        result = await _reconcile_webhook_registration(
            fdb,
            client,
            creator_id,
            integration,
            url,
            expected_events,
            include_set_price,
        )
        converged = result.state in {
            ReconcileOutcome.NO_ACTION_REQUIRED,
            ReconcileOutcome.ORPHAN_REPLACED,
            ReconcileOutcome.REMOTE_WEBHOOK_NOT_FOUND,
        }
        if converged:
            await fdb.record_integration_success(creator_id)
        else:
            await fdb.record_integration_error(
                creator_id, f"{result.state.value}: {result.details}"
            )
        _audit(
            creator_id,
            "reconcile_webhooks",
            "webhook",
            integration.get("webhook_id"),
            converged,
            outcome=result.state.value,
        )
        return result
    finally:
        await client.close()


def _parse_occurred_at(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    try:
        return dt.datetime.fromisoformat(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


# ── Status ──────────────────────────────────────────────────────────────────


async def get_integration_status(creator_id: int) -> dict[str, Any] | None:
    """Exposure-safe integration state. Never includes credentials."""
    from db import fangate as fdb

    integration = await fdb.get_creator_integration(creator_id)
    if not integration:
        return None

    def _iso(value: Any) -> str | None:
        return value.isoformat() if isinstance(value, (dt.datetime, dt.date)) else value

    return {
        "creator_id": creator_id,
        "status": integration["status"],
        "fangate_account_id": integration["fangate_account_id"],
        "api_key_name": integration["api_key_name"],
        "webhook_id": integration["webhook_id"],
        "webhook_registered": integration["webhook_id"] is not None,
        "last_success_at": _iso(integration["last_success_at"]),
        "last_error_at": _iso(integration["last_error_at"]),
        "last_error": integration["last_error"],
    }
