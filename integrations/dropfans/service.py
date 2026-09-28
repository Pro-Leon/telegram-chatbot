"""Dropfans service layer.

Dropfans is the sole active external commerce / vault / payment provider.
All active CRM operations route through this service.
Fangate is legacy-only and must never be called from active runtime paths.
"""

from __future__ import annotations

import logging
from typing import Any

from db import dropfans as ddb
from integrations.dropfans.client import DropfansClient
from integrations.dropfans.errors import (
    DropfansError,
    DropfansRateLimitError,
)
from integrations.dropfans.models import (
    DropfansAccount,
    DropfansSaleStatus,
    DropfansVaultFolder,
    DropfansVaultListResult,
    DropfansVaultUploadResult,
    DropfansVideoStatus,
    DropfansVideoUploadComplete,
    DropfansVideoUploadStart,
)
from integrations.dropfans.security import decrypt_secret

logger = logging.getLogger("dropfans.service")

_RATE_LIMIT_RETRIES = 2
_RATE_LIMIT_BASE_DELAY = 2.0


class DropfansIntegrationNotFoundError(DropfansError):
    """No Dropfans integration exists for this creator."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_client(creator_id: int) -> DropfansClient:
    """Load and decrypt Dropfans API key, return an authenticated client."""
    integration = await ddb.get_dropfans_integration(creator_id)
    if not integration or not integration.get("dropfans_creator_id"):
        raise DropfansIntegrationNotFoundError(
            "get_client", f"No Dropfans integration for creator {creator_id}"
        )
    encrypted_key = integration.get("encrypted_api_key", "")
    if not encrypted_key:
        raise DropfansIntegrationNotFoundError(
            "get_client", f"No encrypted API key for creator {creator_id}"
        )
    api_key = decrypt_secret(encrypted_key)
    from core.config import get_settings
    settings = get_settings()
    return DropfansClient(
        api_key,
        base_url=settings.dropfans_api_base_url,
        timeout=settings.dropfans_api_timeout,
    )


def _audit(creator_id: int, operation: str, **extra: Any) -> None:
    """Structured audit log. Never logs credentials."""
    from core.logging_config import sanitize_log_value
    log_data = {"creator_id": creator_id, "operation": operation, **extra}
    logger.info("dropfans.operation", extra=sanitize_log_value(log_data))


def _is_app_suspended(exc: Exception) -> bool:
    """Return True when a Dropfans error carries code app_suspended (P3-D)."""
    try:
        text = f"{getattr(exc, 'message', '')} {exc}".lower()
        return "app_suspended" in text
    except Exception:
        return False


def _auth_failure_reason(exc: Exception) -> str | None:
    """Classify credential failures without exposing secrets (P3-D).

    Returns an operator-facing reason slug, or None when not an auth failure.
    Preserves documented 403 codes (app_suspended, first_party_only) instead
    of collapsing them into a generic message.
    """
    from integrations.dropfans.errors import (
        DropfansAuthenticationError,
        DropfansAuthorizationError,
    )

    try:
        text = f"{getattr(exc, 'message', '')} {exc}".lower()
    except Exception:
        text = ""
    if isinstance(exc, DropfansAuthenticationError):
        return "credential_revoked"
    if isinstance(exc, DropfansAuthorizationError):
        if "app_suspended" in text:
            return "app_suspended"
        if "first_party_only" in text:
            return "first_party_only"
        return "authorization_denied"
    return None


async def _persist_auth_failure(
    creator_id: int, exc: Exception, operation: str
) -> None:
    """Persist operator-visible integration error state (P3-D, best-effort).

    Reuses the existing creator_integrations error mechanism. Never logs keys,
    never retries, never fabricates credentials, never changes commerce authority.
    """
    reason = _auth_failure_reason(exc)
    if reason is None:
        return
    try:
        from db import fangate as _fdb

        if reason == "credential_revoked":
            await _fdb.record_integration_error(
                creator_id, f"dropfans_unauthorized ({operation}): reconnect required"
            )
        elif reason == "app_suspended":
            await _fdb.record_integration_error(
                creator_id, f"dropfans_app_suspended ({operation}): contact Dropfans support"
            )
        else:
            # first_party_only / generic authorization: preserve provider code
            await _fdb.record_integration_error(
                creator_id, f"dropfans_{reason} ({operation})"
            )
    except Exception:
        logger.debug("persist auth failure failed", exc_info=True)


async def _run_scoped(creator_id: int, operation: str, coro: Any) -> Any:
    """Execute an operation within a scoped client lifecycle."""
    from integrations.dropfans.errors import DropfansAuthenticationError
    from integrations.dropfans.errors import DropfansAuthorizationError

    client = await _get_client(creator_id)
    try:
        result = await coro(client)
        _audit(creator_id, operation, success=True)
        return result
    except DropfansRateLimitError:
        raise
    except (DropfansAuthenticationError, DropfansAuthorizationError) as auth_exc:
        # P3-D: persist operator-visible state, still stop retries (re-raise).
        _audit(creator_id, operation, success=False)
        try:
            await _persist_auth_failure(creator_id, auth_exc, operation)
        except Exception:
            pass
        raise
    except DropfansError:
        _audit(creator_id, operation, success=False)
        raise
    finally:
        await client.close()


async def _with_rate_limit_retries(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Retry on rate-limit errors up to _RATE_LIMIT_RETRIES times."""
    last_exc: DropfansRateLimitError | None = None
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            return await fn(*args, **kwargs)
        except DropfansRateLimitError as exc:
            last_exc = exc
            if attempt < _RATE_LIMIT_RETRIES:
                delay = exc.retry_after if exc.retry_after and exc.retry_after <= 30 else _RATE_LIMIT_BASE_DELAY * (attempt + 1)
                logger.warning("dropfans.rate_limited", extra={"retry_after": delay, "attempt": attempt})
                import asyncio
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Account / validation
# ---------------------------------------------------------------------------

async def validate_api_key(api_key: str) -> DropfansAccount:
    """Validate a Dropfans API key and return the creator identity."""
    from core.config import get_settings
    settings = get_settings()
    async with DropfansClient(api_key, base_url=settings.dropfans_api_base_url, timeout=settings.dropfans_api_timeout) as client:
        return await _with_rate_limit_retries(client.get_me)


async def connect_creator(
    creator_id: int,
    api_key: str,
) -> dict[str, Any]:
    """Validate key, store encrypted, return integration status."""
    from integrations.dropfans.security import encrypt_secret

    account = await validate_api_key(api_key)
    encrypted = encrypt_secret(api_key)

    await ddb.upsert_dropfans_integration(
        creator_id,
        dropfans_creator_id=account.creator_id,
        dropfans_username=account.username,
        dropfans_display_name=account.display_name,
    )

    # Store encrypted API key on the existing creator_integrations row
    from db.postgres import get_pool
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE creator_integrations
        SET encrypted_api_key = $2, status = 'active', updated_at = NOW()
        WHERE creator_id = $1
        """,
        creator_id,
        encrypted,
    )

    _audit(creator_id, "connect_creator", dropfans_creator_id=account.creator_id)
    return {
        "ok": True,
        "dropfans_creator_id": account.creator_id,
        "username": account.username,
        "display_name": account.display_name,
    }


async def get_integration_status(creator_id: int) -> dict[str, Any] | None:
    """Return integration status without exposing credentials.

    P3-D: distinguishes Active / credential revoked (reconnect required) /
    app suspended, reusing persisted last_error. Never includes ciphertext.
    """
    integration = await ddb.get_dropfans_integration(creator_id)
    if not integration:
        return None
    status = integration.get("status")
    last_error = integration.get("last_error")
    last_error_at = integration.get("last_error_at")
    try:
        last_error_at = last_error_at.isoformat() if hasattr(last_error_at, "isoformat") else last_error_at
    except Exception:
        pass
    action = "active"
    if status != "active":
        lowered = str(last_error or "").lower()
        if "app_suspended" in lowered:
            action = "suspended"
        elif "unauthorized" in lowered or "revoked" in lowered:
            action = "reconnect_required"
        else:
            action = "error"
    return {
        "creator_id": creator_id,
        "dropfans_creator_id": integration.get("dropfans_creator_id"),
        "username": integration.get("dropfans_username"),
        "display_name": integration.get("dropfans_display_name"),
        "status": status,
        "last_error": last_error,
        "last_error_at": last_error_at,
        "action": action,
        "reconnect_required": action == "reconnect_required",
    }


async def get_account(creator_id: int) -> dict[str, Any]:
    """Get full account info from Dropfans."""
    async def _fetch(client: DropfansClient) -> dict[str, Any]:
        account = await _with_rate_limit_retries(client.get_me)
        return {
            "creator_id": account.creator_id,
            "username": account.username,
            "display_name": account.display_name,
            "image": account.image,
            "account_type": account.account_type,
            "key_name": account.key_name,
            "tier": account.tier,
        }
    return await _run_scoped(creator_id, "get_account", _fetch)


async def get_timezone(creator_id: int) -> str | None:
    """Get creator timezone from Dropfans."""
    async def _fetch(client: DropfansClient) -> str | None:
        return await _with_rate_limit_retries(client.get_timezone)
    return await _run_scoped(creator_id, "get_timezone", _fetch)


async def set_timezone(creator_id: int, timezone: str) -> str | None:
    """Set creator timezone on Dropfans."""
    async def _update(client: DropfansClient) -> str | None:
        return await _with_rate_limit_retries(client.set_timezone, timezone)
    return await _run_scoped(creator_id, "set_timezone", _update)


# ---------------------------------------------------------------------------
# Vault operations
# ---------------------------------------------------------------------------

async def list_vault_items(
    creator_id: int,
    *,
    page: int = 1,
    limit: int = 50,
    folder_id: str | None = None,
    include_pending: bool = False,
) -> DropfansVaultListResult:
    """List vault items from Dropfans with pagination."""
    async def _fetch(client: DropfansClient) -> DropfansVaultListResult:
        return await _with_rate_limit_retries(
            client.list_vault,
            page=page,
            limit=limit,
            folder_id=folder_id,
            include_pending=include_pending,
        )
    return await _run_scoped(creator_id, "list_vault", _fetch)


async def upload_vault_item(
    creator_id: int,
    *,
    file_type: str,
    original_name: str,
    display_file: Any = None,
    thumbnail_file: Any = None,
    file: Any = None,
    folder_id: str | None = None,
    duration_seconds: int | None = None,
) -> DropfansVaultUploadResult:
    """Upload a vault item (image/audio/small video)."""
    async def _upload(client: DropfansClient) -> DropfansVaultUploadResult:
        return await _with_rate_limit_retries(
            client.upload_vault_item,
            file_type=file_type,
            original_name=original_name,
            display_file=display_file,
            thumbnail_file=thumbnail_file,
            file=file,
            folder_id=folder_id,
            duration_seconds=duration_seconds,
        )
    return await _run_scoped(creator_id, "upload_vault_item", _upload)


async def delete_vault_item(creator_id: int, item_id: str) -> bool:
    """Delete a vault item on Dropfans."""
    async def _delete(client: DropfansClient) -> bool:
        return await _with_rate_limit_retries(client.delete_vault_item, item_id)
    return await _run_scoped(creator_id, "delete_vault_item", _delete)


async def move_vault_item(creator_id: int, item_id: str, folder_id: str | None) -> bool:
    """Move a vault item to a folder (or unfile if folder_id is None)."""
    async def _move(client: DropfansClient) -> bool:
        return await _with_rate_limit_retries(client.move_vault_item, item_id, folder_id)
    return await _run_scoped(creator_id, "move_vault_item", _move)


async def update_vault_tags(creator_id: int, item_id: str, tags: list[str]) -> bool:
    """Replace content tags on a vault item."""
    async def _update(client: DropfansClient) -> bool:
        return await _with_rate_limit_retries(client.update_vault_tags, item_id, tags)
    return await _run_scoped(creator_id, "update_vault_tags", _update)


async def list_vault_folders(creator_id: int) -> list[DropfansVaultFolder]:
    """List vault folders from Dropfans."""
    async def _fetch(client: DropfansClient) -> list[DropfansVaultFolder]:
        return await _with_rate_limit_retries(client.list_vault_folders)
    return await _run_scoped(creator_id, "list_vault_folders", _fetch)


async def create_vault_folder(creator_id: int, name: str) -> DropfansVaultFolder:
    """Create a vault folder on Dropfans."""
    async def _create(client: DropfansClient) -> DropfansVaultFolder:
        return await _with_rate_limit_retries(client.create_vault_folder, name)
    return await _run_scoped(creator_id, "create_vault_folder", _create)


async def delete_vault_folder(creator_id: int, folder_id: str) -> bool:
    """Delete a vault folder on Dropfans."""
    async def _delete(client: DropfansClient) -> bool:
        return await _with_rate_limit_retries(client.delete_vault_folder, folder_id)
    return await _run_scoped(creator_id, "delete_vault_folder", _delete)


# ---------------------------------------------------------------------------
# Video upload
# ---------------------------------------------------------------------------

async def start_video_upload(
    creator_id: int,
    *,
    original_name: str,
    file_size: int | None = None,
) -> DropfansVideoUploadStart:
    """Start a video upload (step 1 of 3)."""
    async def _start(client: DropfansClient) -> DropfansVideoUploadStart:
        return await _with_rate_limit_retries(
            client.start_video_upload,
            original_name=original_name,
            file_size=file_size,
        )
    return await _run_scoped(creator_id, "start_video_upload", _start)


async def complete_video_upload(
    creator_id: int,
    *,
    video_id: str,
    original_name: str,
    completion_token: str,
    folder_id: str | None = None,
) -> DropfansVideoUploadComplete:
    """Complete a video upload (step 3 of 3)."""
    async def _complete(client: DropfansClient) -> DropfansVideoUploadComplete:
        return await _with_rate_limit_retries(
            client.complete_video_upload,
            video_id=video_id,
            original_name=original_name,
            completion_token=completion_token,
            folder_id=folder_id,
        )
    return await _run_scoped(creator_id, "complete_video_upload", _complete)


async def get_video_status(
    creator_id: int,
    video_ids: list[str],
) -> list[DropfansVideoStatus]:
    """Check video transcoding status (batch)."""
    async def _check(client: DropfansClient) -> list[DropfansVideoStatus]:
        return await _with_rate_limit_retries(client.get_video_status, video_ids)
    return await _run_scoped(creator_id, "get_video_status", _check)


# ---------------------------------------------------------------------------
# Drop / product creation
# ---------------------------------------------------------------------------

def _is_definitive_rejection(exc: Exception) -> bool:
    """Classify a create_drop failure as DEFINITIVE_REJECTION vs ambiguous.

    P3.2C F2.1/F2.2: only a provider response that proves the Drop was NOT
    created may transition an intent pending → failed (safe to POST again
    after explicit rearm). ``client.create_drop`` performs no local
    validation — every error below corresponds to an actual HTTP exchange:

    - DEFINITIVE (4xx: validation/auth/not-found/rate-limit): the provider
      rejected the request before creating anything. Safe to retry later.
    - AMBIGUOUS (timeout/transport/5xx/unparseable-2xx/unexpected): provider
      state unknown — the Drop may exist. The intent stays pending; no
      automatic rearm, no fresh POST (diagnostic: drop_provider_result_ambiguous).
    """
    from integrations.dropfans.errors import DropfansError

    if isinstance(exc, DropfansError) and exc.status_code is not None:
        return 400 <= exc.status_code < 500
    return False


async def create_drop(
    creator_id: int,
    *,
    name: str | None = None,
    price: float,
    vault_item_ids: list[str],
    allow_download: bool = True,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a drop on Dropfans. Price in USD dollars.

    P3.2: Vault set is canonicalized (presentation order preserved for the
    provider call, sorted-unique identity for the intent key); monetary
    values are normalized via Decimal; a durable intent
    ``UNIQUE(creator_id, content_key)`` converges retries. An existing
    active intent is reused; a pending intent never triggers a blind
    re-POST (caller must reconcile). Provider-side idempotency is NOT
    assumed — see ``db/drop_intents.py``.
    """
    from commerce.vault_sets import (
        canonical_identity_ids,
        drop_content_key,
        presentation_ids,
    )

    # Canonicalize + validate before any side effect. 1-10 enforced here
    # (route also enforces; defense in depth for non-dashboard callers).
    try:
        ordered_ids = presentation_ids(vault_item_ids)
        identity_ids = canonical_identity_ids(vault_item_ids)
    except Exception as exc:
        from integrations.dropfans.errors import DropfansValidationError
        raise DropfansValidationError("create_drop", f"invalid vault_item_ids: {exc}") from None
    if not ordered_ids:
        from integrations.dropfans.errors import DropfansValidationError
        raise DropfansValidationError("create_drop", "vault_item_ids must contain at least one item")
    if len(identity_ids) > 10:
        from integrations.dropfans.errors import DropfansValidationError
        raise DropfansValidationError("create_drop", "vault_item_ids exceeds Dropfans limit of 10")
    try:
        from decimal import Decimal, InvalidOperation
        _dec = Decimal(str(price))
        _price_minor = int((_dec * Decimal(100)).to_integral_value())
    except Exception:
        from integrations.dropfans.errors import DropfansValidationError
        raise DropfansValidationError("create_drop", f"invalid price: {price!r}") from None
    if _price_minor != 0 and (_price_minor < 500 or _price_minor > 75000):
        from integrations.dropfans.errors import DropfansValidationError
        raise DropfansValidationError("create_drop", f"price out of range: {price!r}")
    try:
        _content_key = drop_content_key(
            creator_id=creator_id,
            vault_item_ids=identity_ids,
            price_minor=_price_minor,
            currency="USD",
            allow_download=bool(allow_download),
        )
    except Exception as exc:
        from integrations.dropfans.errors import DropfansValidationError
        raise DropfansValidationError("create_drop", f"invalid drop identity: {exc}") from None

    # Durable intent first (fail-closed on DB errors other than missing table).
    _intent: dict[str, Any] | None = None
    _intent_created = False
    _intents_available = True
    try:
        from db import drop_intents as _intents

        _intent, _intent_created = await _intents.get_or_create_intent(
            creator_id,
            content_key=_content_key,
            canonical_vault_item_ids=identity_ids,
            price_minor=_price_minor,
            currency="USD",
            allow_download=bool(allow_download),
        )
    except Exception as exc:
        if "dropfans_drop_intents" in str(exc) or "UndefinedTable" in str(exc):
            # Pre-migration database: legacy direct-POST path (documented gap).
            _intents_available = False
            logger.warning("create_drop: intents table unavailable, using legacy path creator=%s", creator_id)
        else:
            raise
    if _intents_available and _intent is not None and not _intent_created:
        if _intent.get("status") == "active" and _intent.get("dropfans_product_id"):
            # Reuse converged Drop: ensure mirror row exists, then return it.
            _cuid = str(_intent["dropfans_product_id"])
            try:
                existing = await ddb.find_dropfans_product(creator_id, _cuid)
            except Exception:
                existing = None
            if existing:
                return {
                    "product_id": _cuid,
                    "buy_url": existing.get("sales_url"),
                    "media_count": len(identity_ids),
                }
            try:
                _live = await get_drop(creator_id, _cuid)
                await ddb.upsert_dropfans_product(
                    creator_id,
                    dropfans_product_id=_cuid,
                    name=name or "",
                    price_cents=_price_minor,
                    buy_url=_live.get("buy_url"),
                    status="active",
                    allow_download=bool(allow_download),
                    media_count=int(_live.get("media_count") or len(identity_ids)),
                    vault_item_ids=ordered_ids,
                )
                return {
                    "product_id": _cuid,
                    "buy_url": _live.get("buy_url"),
                    "media_count": int(_live.get("media_count") or len(identity_ids)),
                }
            except Exception:
                logger.warning("create_drop: active intent rehydration failed creator=%s", creator_id, exc_info=True)
                return {"product_id": _cuid, "buy_url": None, "media_count": len(identity_ids)}
        if _intent.get("status") == "failed":
            # Explicit retry after a recorded provider failure: re-arm to
            # pending, then fall through to a single fresh POST below.
            # Pending/active rows are never rearmed here.
            try:
                from db import drop_intents as _intents_retry

                _rearmed = await _intents_retry.rearm_failed_intent(
                    creator_id,
                    _content_key,
                    canonical_vault_item_ids=identity_ids,
                    price_minor=_price_minor,
                    currency="USD",
                    allow_download=bool(allow_download),
                )
            except Exception:
                _rearmed = None
            if not _rearmed:
                from integrations.dropfans.errors import DropfansError
                raise DropfansError(
                    "create_drop",
                    "drop creation failed previously and could not be rearmed; refusing duplicate POST",
                )
        else:
            # Pending without a persisted CUID: never blindly POST again. A
            # pending intent means a previous POST may have succeeded
            # provider-side without persisting. Surface for explicit
            # reconciliation via adopt_drop (diagnostic below names it).
            from integrations.dropfans.errors import DropfansError
            raise DropfansError(
                "create_drop",
                f"drop_provider_result_ambiguous: drop creation already in progress "
                f"for this content (status={_intent.get('status')}); intent remains "
                f"pending for manual reconciliation/adoption (drop_intent_pending_reconciliation); "
                f"refusing duplicate POST",
            )

    _post_attempted = False

    async def _create(client: DropfansClient) -> dict[str, Any]:
        nonlocal _post_attempted
        try:
            _post_attempted = True
            result = await _with_rate_limit_retries(
                client.create_drop,
                name=name,
                price=price,
                vault_item_ids=ordered_ids,
                allow_download=allow_download,
                description=description,
            )
        except Exception as exc:
            # P3.2C F2.2/F2.3: definitive rejections (4xx — nothing was
            # created) become failed and may be explicitly rearmed later.
            # Ambiguous results (timeout/transport/5xx/unparseable/unexpected)
            # leave the intent pending: NO rearm, NO fresh POST.
            if _intents_available:
                if _is_definitive_rejection(exc):
                    try:
                        from db import drop_intents as _intents2

                        await _intents2.mark_intent_failed(
                            creator_id, _content_key,
                            f"drop_provider_rejection:{exc.__class__.__name__}"[:500],
                        )
                    except Exception:
                        pass
                    logger.warning(
                        "create_drop: drop_provider_rejection creator=%s: %s",
                        creator_id, exc.__class__.__name__,
                    )
                else:
                    logger.warning(
                        "create_drop: drop_provider_result_ambiguous creator=%s: %s — "
                        "intent remains pending (drop_intent_pending_reconciliation), no re-POST",
                        creator_id, exc.__class__.__name__,
                    )
            raise
        # P3.2C F2.4: provider POST succeeded. Any local persistence failure
        # below must NOT mark the intent failed (the provider object exists)
        # and must NOT trigger a second POST. The intent stays pending with
        # the known CUID preserved for adoption.
        try:
            await ddb.upsert_dropfans_product(
                creator_id,
                dropfans_product_id=result.product_id,
                name=name or "",
                price_cents=_price_minor,
                buy_url=result.buy_url,
                status="active",
                allow_download=allow_download,
                media_count=result.media_count,
                vault_item_ids=ordered_ids,
            )
        except Exception:
            if _intents_available:
                try:
                    from db import drop_intents as _intents_persist

                    await _intents_persist.note_intent_provider_cuid(
                        creator_id, _content_key, result.product_id)
                except Exception:
                    pass
            logger.warning(
                "create_drop: provider accepted but mirror persistence failed creator=%s drop=%s — "
                "intent remains pending, no second POST (drop_intent_pending_reconciliation)",
                creator_id, result.product_id, exc_info=True,
            )
            raise
        if _intents_available:
            try:
                from db import drop_intents as _intents3

                await _intents3.mark_intent_active(creator_id, _content_key, result.product_id)
            except Exception:
                try:
                    from db import drop_intents as _intents_persist2

                    await _intents_persist2.note_intent_provider_cuid(
                        creator_id, _content_key, result.product_id)
                except Exception:
                    pass
                logger.warning(
                    "create_drop: provider succeeded but intent activation failed creator=%s drop=%s — retry must reconcile, not re-POST",
                    creator_id, result.product_id, exc_info=True,
                )
        _audit(creator_id, "create_drop", dropfans_product_id=result.product_id)
        return {
            "product_id": result.product_id,
            "buy_url": result.buy_url,
            "media_count": result.media_count,
        }
    try:
        return await _run_scoped(creator_id, "create_drop", _create)
    except Exception:
        # P3.2C F2: pre-POST failure (client construction/auth — no HTTP
        # attempt was possible) on a JUST-created intent is safe to mark
        # failed: nothing could have been created provider-side. Any other
        # failure already left the intent pending (ambiguous) or failed
        # (definitive) via _create above — never touch it here.
        if _intents_available and _intent_created and not _post_attempted:
            try:
                from db import drop_intents as _intents_pre

                await _intents_pre.mark_intent_failed(
                    creator_id, _content_key, "drop_provider_rejection:pre_post_failure")
            except Exception:
                pass
            logger.warning(
                "create_drop: pre-POST failure creator=%s — intent marked failed (nothing was sent)",
                creator_id,
            )
        raise


async def adopt_drop(
    creator_id: int,
    *,
    content_key: str,
    dropfans_product_id: str,
) -> dict[str, Any]:
    """Adopt a known provider Drop into a pending intent (operator recovery).

    P3.2C F2.7/F2.8: reconciles ``POST accepted but result never persisted``
    without creating another provider Drop. Issues only provider GETs —
    NEVER a POST — plus creator-scoped local writes. No LLM involvement;
    callers must authenticate/authorize the operator (dashboard route).

    Steps: load pending intent (creator-scoped) → validate CUID shape →
    verify live provider Drop under this creator's credentials (ownership) →
    recompute the content key from intent fields (integrity) → compare live
    price/currency/Vault-set/allow_download against the intent (no blind
    adoption of mismatched Drops) → sync mirror → mark active.

    Raises DropfansError/ValueError with explicit diagnostics; never POSTs.
    """
    from commerce.vault_sets import canonical_identity_ids, drop_content_key
    from integrations.dropfans.errors import DropfansError, DropfansValidationError

    if not isinstance(creator_id, int) or isinstance(creator_id, bool) or creator_id <= 0:
        raise DropfansValidationError("adopt_drop", "creator_id is required")
    if not isinstance(content_key, str) or not content_key.strip():
        raise DropfansValidationError("adopt_drop", "content_key is required")
    content_key = content_key.strip()
    if not isinstance(dropfans_product_id, str) or not dropfans_product_id.strip():
        raise DropfansValidationError("adopt_drop", "dropfans_product_id is required")
    cuid = dropfans_product_id.strip()
    if len(cuid) > 128:
        raise DropfansValidationError("adopt_drop", "dropfans_product_id malformed")

    from db import drop_intents as _intents

    try:
        intent = await _intents.get_intent(creator_id, content_key)
    except Exception as exc:
        if "dropfans_drop_intents" in str(exc) or "UndefinedTable" in str(exc):
            raise DropfansError("adopt_drop", "drop_intent_adopt_failed: intents table unavailable") from None
        raise
    if intent is None:
        raise DropfansError(
            "adopt_drop",
            "drop_intent_adopt_failed: no such intent for this creator (creator isolation)",
        )
    if intent.get("status") == "active":
        if str(intent.get("dropfans_product_id") or "").strip() == cuid:
            return {
                "product_id": cuid,
                "buy_url": None,
                "media_count": len(list(intent.get("canonical_vault_item_ids") or [])),
                "adopted": False,
                "already_active": True,
            }
        raise DropfansError(
            "adopt_drop",
            "drop_intent_adopt_failed: intent already active with a different CUID",
        )
    if intent.get("status") != "pending":
        raise DropfansError(
            "adopt_drop",
            f"drop_intent_adopt_failed: only pending intents are adoptable (status={intent.get('status')})",
        )

    # Reject an obvious synthetic local id passed where an external CUID is required.
    if cuid.isdigit():
        try:
            from db.dropfans import find_synthetic_product as _find_synth

            if await _find_synth(creator_id, int(cuid)) is not None:
                raise DropfansValidationError(
                    "adopt_drop",
                    "drop_intent_adopt_failed: value is a synthetic local product id, not an external Dropfans CUID",
                )
        except DropfansValidationError:
            raise
        except Exception:
            pass

    # Verify the live provider Drop under THIS creator's credentials: proves
    # creator-scoped ownership (another creator's Drop is not visible here).
    try:
        live = await get_drop(creator_id, cuid)
    except Exception as exc:
        raise DropfansError(
            "adopt_drop",
            f"drop_intent_adopt_failed: provider Drop not verifiable under this creator ({exc.__class__.__name__})",
        ) from None

    # Integrity: recompute the content key from the stored intent fields.
    try:
        expected_key = drop_content_key(
            creator_id=creator_id,
            vault_item_ids=list(intent.get("canonical_vault_item_ids") or []),
            price_minor=int(intent.get("price_minor")),
            currency=str(intent.get("currency") or "USD"),
            allow_download=bool(intent.get("allow_download", True)),
        )
    except Exception as exc:
        raise DropfansError(
            "adopt_drop", f"drop_intent_adopt_failed: intent fields invalid ({exc})") from None
    if expected_key != content_key:
        raise DropfansError(
            "adopt_drop", "drop_intent_adopt_failed: intent content-key integrity mismatch")

    # Compatibility: live Drop must match the intent (no blind adoption).
    try:
        from decimal import Decimal

        _live_minor = int((Decimal(str(live.get("price"))) * Decimal(100)).to_integral_value())
    except Exception:
        raise DropfansError(
            "adopt_drop", "drop_intent_adopt_failed: live Drop price unreadable") from None
    _live_currency = str(live.get("currency") or "USD").strip().upper()
    _live_vault = sorted(
        str(m.get("vault_item_id", "")).strip()
        for m in (live.get("media") or []) if str(m.get("vault_item_id", "")).strip()
    )
    _intent_vault = sorted(
        str(v).strip() for v in (intent.get("canonical_vault_item_ids") or []) if str(v).strip()
    )
    _mismatches: list[str] = []
    if _live_minor != int(intent.get("price_minor")):
        _mismatches.append("price")
    if _live_currency != str(intent.get("currency") or "USD").strip().upper():
        _mismatches.append("currency")
    if _live_vault != _intent_vault:
        _mismatches.append("vault_set")
    if bool(live.get("allow_download", False)) != bool(intent.get("allow_download", True)):
        _mismatches.append("allow_download")
    if _mismatches:
        raise DropfansError(
            "adopt_drop",
            f"drop_intent_adopt_failed: live Drop differs from intent ({','.join(_mismatches)}); refusing blind adoption",
        )

    # Sync the mirror, then mark active. No provider POST anywhere in this path.
    await ddb.upsert_dropfans_product(
        creator_id,
        dropfans_product_id=cuid,
        name=str(live.get("name") or ""),
        price_cents=int(intent.get("price_minor")),
        buy_url=live.get("buy_url"),
        status="active",
        allow_download=bool(intent.get("allow_download", True)),
        media_count=int(live.get("media_count") or len(_intent_vault)),
        vault_item_ids=list(intent.get("canonical_vault_item_ids") or []),
    )
    await _intents.mark_intent_active(creator_id, content_key, cuid)
    _audit(creator_id, "adopt_drop", dropfans_product_id=cuid)
    logger.info(
        "adopt_drop: drop_intent_adopted creator=%s drop=%s",
        creator_id, cuid,
    )
    return {
        "product_id": cuid,
        "buy_url": live.get("buy_url"),
        "media_count": int(live.get("media_count") or len(_intent_vault)),
        "adopted": True,
        "already_active": False,
    }


async def get_drop(creator_id: int, drop_id: str) -> dict[str, Any]:
    """Get drop details from Dropfans."""
    async def _fetch(client: DropfansClient) -> dict[str, Any]:
        drop = await _with_rate_limit_retries(client.get_drop, drop_id)
        return {
            "id": drop.product_id,
            "name": drop.name,
            "price": drop.price,
            "currency": drop.currency,
            "status": drop.status,
            "moderation_reason": drop.moderation_reason,
            "buy_url": drop.buy_url,
            "allow_download": drop.allow_download,
            "media_count": drop.media_count,
            "media": [
                {
                    "vault_item_id": m.vault_item_id,
                    "order": m.order,
                    "file_type": m.file_type,
                    "moderation_status": m.moderation_status,
                    "has_preview": m.has_preview,
                }
                for m in drop.media
            ],
            "sales_count": drop.sales_count,
            "last_sale_at": drop.last_sale_at,
            "created_at": drop.created_at,
        }
    return await _run_scoped(creator_id, "get_drop", _fetch)


async def attach_drop_previews(
    creator_id: int,
    drop_id: str,
    previews: dict[str, Any],
) -> int:
    """Attach baked blur previews to a drop."""
    async def _attach(client: DropfansClient) -> int:
        return await _with_rate_limit_retries(
            client.attach_drop_previews, drop_id, previews,
        )
    return await _run_scoped(creator_id, "attach_drop_previews", _attach)


# ---------------------------------------------------------------------------
# Checkout links
# ---------------------------------------------------------------------------

async def get_checkout_links(creator_id: int) -> dict[str, Any]:
    """Get canonical purchase links from Dropfans."""
    async def _fetch(client: DropfansClient) -> dict[str, Any]:
        links = await _with_rate_limit_retries(client.get_links)
        result: dict[str, Any] = {
            "username": links.username,
            "web": {
                "profile": links.web.profile,
                "tip": links.web.tip,
                "tip_template": links.web.tip_template,
                "subscribe": links.web.subscribe,
                "buy_template": links.web.buy_template,
            },
        }
        if links.telegram:
            result["telegram"] = {
                "bot": links.telegram.bot,
                "profile": links.telegram.profile,
                "tip": links.telegram.tip,
                "tip_template": links.telegram.tip_template,
                "subscribe": links.telegram.subscribe,
                "spin": links.telegram.spin,
                "buy_template": links.telegram.buy_template,
            }
        else:
            result["telegram"] = None
        return result
    return await _run_scoped(creator_id, "get_checkout_links", _fetch)


async def build_checkout_url(creator_id: int, drop_id: str) -> str | None:
    """Build a checkout URL for a specific drop using canonical templates."""
    try:
        links = await get_checkout_links(creator_id)
        telegram = links.get("telegram")
        if telegram and telegram.get("buy_template"):
            return telegram["buy_template"].replace("{productId}", drop_id)
        web = links.get("web", {})
        if web.get("buy_template"):
            return web["buy_template"].replace("{productId}", drop_id)
    except DropfansError:
        logger.warning("Failed to get checkout links, using fallback")
    return f"https://www.dropfans.io/buy/{drop_id}"


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------

async def create_post(
    creator_id: int,
    *,
    caption: str | None = None,
    kind: str = "TEXT",
    product_id: str | None = None,
    media: list[dict[str, Any]] | None = None,
    scheduled_at: str | None = None,
) -> dict[str, Any]:
    """Publish a post to the For You feed."""
    async def _create(client: DropfansClient) -> dict[str, Any]:
        result = await _with_rate_limit_retries(
            client.create_post,
            caption=caption,
            kind=kind,
            product_id=product_id,
            media=media,
            scheduled_at=scheduled_at,
        )
        _audit(creator_id, "create_post", post_id=result.id, status=result.status)
        return {
            "id": result.id,
            "status": result.status,
            "pending": result.pending,
            "scheduled_at": result.scheduled_at,
            "url": result.url,
        }
    return await _run_scoped(creator_id, "create_post", _create)


async def list_posts(
    creator_id: int,
    *,
    page: int = 1,
    limit: int = 20,
    status: str | None = None,
) -> dict[str, Any]:
    """List creator posts with pagination."""
    async def _fetch(client: DropfansClient) -> dict[str, Any]:
        result = await _with_rate_limit_retries(
            client.list_posts, page=page, limit=limit, status=status,
        )
        return {
            "posts": [
                {
                    "id": p.id,
                    "kind": p.kind,
                    "caption": p.caption,
                    "status": p.status,
                    "live": p.live,
                    "scheduled_at": p.scheduled_at,
                    "published_at": p.published_at,
                    "created_at": p.created_at,
                    "product_id": p.product_id,
                    "likes": p.likes,
                    "comments": p.comments,
                    "media": [
                        {
                            "id": m.id,
                            "vault_item_id": m.vault_item_id,
                            "is_paid": m.is_paid,
                            "order": m.order,
                            "type": m.type,
                        }
                        for m in p.media
                    ],
                }
                for p in result.posts
            ],
            "pagination": {
                "page": result.page,
                "limit": result.limit,
                "total": result.total,
                "has_more": result.has_more,
            },
            "limits": {
                "posts_per_day": result.limits.posts_per_day,
                "max_caption_chars": result.limits.max_caption_chars,
                "max_media_per_post": result.limits.max_media_per_post,
                "max_schedule_days": result.limits.max_schedule_days,
                "min_paid_price": result.limits.min_paid_price,
            },
        }
    return await _run_scoped(creator_id, "list_posts", _fetch)


async def get_post(creator_id: int, post_id: str) -> dict[str, Any]:
    """Get a single post by ID."""
    async def _fetch(client: DropfansClient) -> dict[str, Any]:
        post = await _with_rate_limit_retries(client.get_post, post_id)
        return {
            "id": post.id,
            "kind": post.kind,
            "caption": post.caption,
            "status": post.status,
            "live": post.live,
            "scheduled_at": post.scheduled_at,
            "published_at": post.published_at,
            "created_at": post.created_at,
            "product_id": post.product_id,
            "likes": post.likes,
            "comments": post.comments,
            "media": [
                {
                    "id": m.id,
                    "vault_item_id": m.vault_item_id,
                    "is_paid": m.is_paid,
                    "order": m.order,
                    "type": m.type,
                }
                for m in post.media
            ],
        }
    return await _run_scoped(creator_id, "get_post", _fetch)


async def delete_post(creator_id: int, post_id: str) -> bool:
    """Delete a post or cancel a scheduled one."""
    async def _delete(client: DropfansClient) -> bool:
        return await _with_rate_limit_retries(client.delete_post, post_id)
    return await _run_scoped(creator_id, "delete_post", _delete)


# ---------------------------------------------------------------------------
# Earnings / analytics
# ---------------------------------------------------------------------------

async def get_earnings(
    creator_id: int,
    *,
    start_date: str,
    end_date: str,
    tz: str = "UTC",
) -> dict[str, Any]:
    """Get earnings stats, chart and recent transactions."""
    async def _fetch(client: DropfansClient) -> dict[str, Any]:
        earnings = await _with_rate_limit_retries(
            client.get_earnings,
            start_date=start_date,
            end_date=end_date,
            tz=tz,
        )
        return {
            "stats": {
                "total_earnings_cents": earnings.stats.total_earnings_cents,
                "gross_earnings_cents": earnings.stats.gross_earnings_cents,
                "previous_period_earnings_cents": earnings.stats.previous_period_earnings_cents,
                "previous_period_gross_earnings_cents": earnings.stats.previous_period_gross_earnings_cents,
                "transaction_count": earnings.stats.transaction_count,
                "avg_transaction_cents": earnings.stats.avg_transaction_cents,
                "unique_customers": earnings.stats.unique_customers,
                "type_totals": {
                    k: {
                        "gross_cents": v.gross_cents,
                        "net_cents": v.net_cents,
                        "count": v.count,
                    }
                    for k, v in earnings.stats.type_totals.items()
                },
            },
            "chart": {
                "labels": earnings.chart.labels,
                "values": earnings.chart.values,
                "dates": earnings.chart.dates,
                "group_by": earnings.chart.group_by,
                "typed_values": earnings.chart.typed_values,
            },
            "transactions": [
                {
                    "id": t.id,
                    "product_id": t.product_id,
                    "product_name": t.product_name,
                    "amount_cents": t.amount_cents,
                    "gross_amount_cents": t.gross_amount_cents,
                    "buyer_email": t.buyer_email,
                    "buyer_name": t.buyer_name,
                    "paid_at": t.paid_at,
                    "type": t.type,
                }
                for t in earnings.transactions
            ],
        }
    return await _run_scoped(creator_id, "get_earnings", _fetch)


async def get_balance(creator_id: int) -> dict[str, Any]:
    """Get wallet balance from Dropfans (in USD dollars)."""
    async def _fetch(client: DropfansClient) -> dict[str, Any]:
        balance = await _with_rate_limit_retries(client.get_balance)
        return {
            "currency": balance.currency,
            "pending": balance.pending,
            "available": balance.available,
            "processing": balance.processing,
            "paid_out": balance.paid_out,
        }
    return await _run_scoped(creator_id, "get_balance", _fetch)


# ---------------------------------------------------------------------------
# Telegram notifications
# ---------------------------------------------------------------------------

async def get_notifications(creator_id: int) -> dict[str, Any]:
    """Read Telegram notification status."""
    async def _fetch(client: DropfansClient) -> dict[str, Any]:
        notifs = await _with_rate_limit_retries(client.get_notifications)
        return {
            "telegram_handle": notifs.telegram_handle,
            "personal_connected": notifs.personal_connected,
            "group_connected": notifs.group_connected,
            "group_chat_id": notifs.group_chat_id,
            "group_name": notifs.group_name,
        }
    return await _run_scoped(creator_id, "get_notifications", _fetch)


async def update_notifications(
    creator_id: int,
    *,
    action: str,
    telegram_handle: str | None = None,
    type: str | None = None,
    group_chat_id: str | None = None,
) -> dict[str, Any]:
    """Update Telegram notification settings."""
    async def _update(client: DropfansClient) -> dict[str, Any]:
        return await _with_rate_limit_retries(
            client.update_notifications,
            action=action,
            telegram_handle=telegram_handle,
            type=type,
            group_chat_id=group_chat_id,
        )
    return await _run_scoped(creator_id, "update_notifications", _update)


async def register_telegram_chat(creator_id: int, telegram_chat_id: str) -> bool:
    """Register the creator's personal notification chat."""
    async def _register(client: DropfansClient) -> bool:
        return await _with_rate_limit_retries(
            client.register_telegram_chat, telegram_chat_id,
        )
    return await _run_scoped(creator_id, "register_telegram_chat", _register)


# ---------------------------------------------------------------------------
# Sale polling / reconciliation
# ---------------------------------------------------------------------------

async def poll_sales(creator_id: int) -> list[dict[str, Any]]:
    """Poll Dropfans for new sales across all active drops."""
    products = await ddb.list_active_dropfans_products(creator_id)
    if not products:
        return []

    product_ids = [p["dropfans_product_id"] for p in products]
    if not product_ids:
        return []

    async def _poll(client: DropfansClient) -> dict[str, DropfansSaleStatus]:
        return await _with_rate_limit_retries(client.check_drop_status, product_ids)

    sales_map = await _run_scoped(creator_id, "poll_sales", _poll)
    return [
        {
            "product_id": s.product_id,
            "paid": s.paid,
            "sale_amount_cents": s.sale_amount_cents,
            "buyer_email": s.buyer_email,
        }
        for s in sales_map.values()
        if s.paid
    ]


async def check_sale_now(
    creator_id: int,
    product_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Targeted on-demand sale check for a small set of products (P3-A).

    Thin wrapper around the existing ``check_drop_status`` path. Reuses the
    existing client/service auth, timeout, 429 handling and 200-product
    chunking. Read-only: performs no DB writes, calls no earnings endpoint,
    mutates no commerce authority. Returns ``{product_id: sale}`` for paid
    products only; unknown/unsold IDs are omitted by the provider.
    """
    if not isinstance(creator_id, int) or creator_id <= 0:
        raise ValueError("creator_id is required for check_sale_now")
    if not product_ids:
        return {}
    clean_ids = [str(p).strip() for p in product_ids if str(p).strip()]
    if not clean_ids:
        return {}
    # Enforce the provider 200-product chunking limit at the service level
    # (the client also chunks per request). Never silently drop IDs beyond
    # the first chunk — iterate and merge instead of truncating.
    merged: dict[str, DropfansSaleStatus] = {}
    for start in range(0, len(clean_ids), 200):
        chunk = clean_ids[start : start + 200]

        async def _poll(
            client: DropfansClient, _chunk: list[str] = chunk
        ) -> dict[str, DropfansSaleStatus]:
            return await _with_rate_limit_retries(client.check_drop_status, _chunk)

        sales_map = await _run_scoped(creator_id, "check_sale_now", _poll)
        merged.update(sales_map)
    return {
        pid: {
            "product_id": s.product_id,
            "paid": s.paid,
            "sale_amount_cents": s.sale_amount_cents,
            "buyer_email": s.buyer_email,
        }
        for pid, s in merged.items()
        if s.paid
    }


async def reconcile_sales(creator_id: int) -> dict[str, Any]:
    """Poll for sales and record new ones idempotently.

    Per-sale transaction identity (Phase 12): use DropFans sale_id when available
    via get_earnings, else derive from drop_id + buyer_email + amount for
    per-buyer uniqueness. Same provider sale delivered twice -> one transaction;
    two distinct purchases of same product -> two transactions (different buyer_email or amount).
    """
    sales = await poll_sales(creator_id)
    newly_recorded = 0
    already_recorded = 0

    for sale in sales:
        pid = sale["product_id"]
        sale_id = sale.get("sale_id")
        buyer_email = sale.get("buyer_email")
        amount = sale.get("sale_amount_cents", 0)
        paid_at = sale.get("paid_at")
        if not sale_id and not buyer_email:
            if await ddb.has_dropfans_sale_been_recorded(creator_id, pid):
                already_recorded += 1
                continue

        recorded = await ddb.record_dropfans_sale(
            creator_id,
            dropfans_product_id=pid,
            buyer_email=buyer_email,
            sale_amount_cents=amount,
            sale_id=sale_id,
            paid_at=paid_at,
        )
        # P3.1 F-04: count/audit only first observations. Repeats are silent
        # no-ops (no event, no false "new" count).
        if recorded:
            newly_recorded += 1
            _audit(creator_id, "sale_detected", dropfans_product_id=pid)
        else:
            already_recorded += 1

    # Per-sale via get_earnings for more accurate id + paid_at (when check_drop_status lacks per-sale id)
    try:
        from datetime import datetime, timedelta, timezone
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        earnings = await get_earnings(creator_id, start_date=start.strftime("%Y-%m-%d"), end_date=end.strftime("%Y-%m-%d"), tz="UTC")
        for txn in earnings.get("transactions", []):
            pid = txn.get("product_id")
            if not pid:
                continue
            sale_id = txn.get("id")
            buyer_email = txn.get("buyer_email")
            amount = txn.get("amount_cents", 0)
            paid_at = txn.get("paid_at")
            if sale_id:
                await ddb.record_dropfans_sale(
                    creator_id,
                    dropfans_product_id=pid,
                    buyer_email=buyer_email,
                    sale_amount_cents=amount,
                    sale_id=sale_id,
                    paid_at=paid_at,
                )
    except Exception:
        pass

    return {
        "total_polled": len(sales),
        "newly_recorded": newly_recorded,
        "already_recorded": already_recorded,
    }
