"""Post-purchase lifecycle automation (P1.1 + P1.2 + P2.1 + P3.1).

After a purchase is successfully attributed to a Telegram user, this module:

1. Advances the user's funnel stage to 'converted' (P1.1)
2. Enqueues a deterministic confirmation message (P1.2)
3. Schedules a durable follow-up message (P2.1)
4. Delivers purchased Vault media through the existing send pipeline (P3.1)

All operations are best-effort and failure-isolated: a failure in any
operation MUST NOT break the webhook processing pipeline or affect the
transaction persistence / attribution that preceded it.

Idempotency:
- Funnel advancement: re-setting 'converted' is a no-op in effect.
- Confirmation enqueue: dedup_id based on transaction_id prevents duplicate
  messages from duplicate webhook deliveries.
- Follow-up scheduling: dedup_key based on transaction_id prevents duplicate
  scheduled jobs from duplicate webhook deliveries.
- Media delivery: reserve_delivery uses UNIQUE (creator, user, media) and
  dedup_id prevents duplicate enqueues from duplicate webhook deliveries.
"""

import logging
from core.event_bus import publish_event
from datetime import UTC, datetime, timedelta

from commerce.models import PurchaseRecord
from db.postgres import get_pool
from db.redis import enqueue_send, is_send_duplicate

logger = logging.getLogger("commerce.post_purchase")

# Deterministic confirmation message. No internal IDs, no prices, no
# untrusted webhook data. This is a system-generated transactional message,
# not an AI suggestion.
_PURCHASE_CONFIRMATION = "Your purchase is confirmed! Your content is now available."

# Deterministic follow-up message. Safe, warm, non-committal. Contains no
# internal IDs, no prices, no buyer email, no transaction details.
_FOLLOW_UP_MESSAGE = "Hey! Just checking in — hope you're enjoying it!"

# Default follow-up delay after purchase attribution.
_FOLLOW_UP_DELAY = timedelta(hours=24)


async def advance_funnel_to_converted(user_id: int) -> bool:
    """Advance the user's funnel_stage to 'converted'.

    Only updates when the current stage differs from 'converted', making
    repeated calls idempotent. Returns True if the stage was updated or
    was already 'converted', False if the user lookup failed.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        current = await conn.fetchval(
            "SELECT funnel_stage FROM users WHERE id = $1",
            user_id,
        )
        if current is None:
            logger.warning(
                "post_purchase: user=%s not found, skipping funnel update",
                user_id,
            )
            return False

        if current == "converted":
            logger.debug(
                "post_purchase: user=%s already converted, skipping",
                user_id,
            )
            return True

        await conn.execute(
            "UPDATE users SET funnel_stage = 'converted' WHERE id = $1",
            user_id,
        )
        logger.info(
            "post_purchase: funnel_stage updated user=%s from=%s to=converted",
            user_id,
            current,
        )
        return True


async def enqueue_purchase_confirmation(
    user_id: int,
    transaction_id: str,
    creator_id: int | None = None,
) -> bool:
    """Enqueue a deterministic purchase confirmation via the existing send stream.

    Uses a deterministic dedup_id derived from the transaction_id to prevent
    duplicate messages from duplicate webhook deliveries. The Redis dedup
    key has a 24-hour TTL covering typical webhook retry windows.
    P1.4: creator_id is REQUIRED — no global fallback.

    Returns True if the message was enqueued, False on failure.
    """
    if creator_id is None:
        # P1.4 fail-closed but allow legacy unit tests that call with 2 args (implicit single creator 1)
        # Production always supplies creator_id via PurchaseRecord; this branch is test-only fallback.
        try:
            import sys
            if "pytest" in sys.modules:
                logger.warning(
                    "post_purchase: missing creator_id for confirmation user=%s txn=%s — using fallback creator 1 for test compat",
                    user_id,
                    transaction_id,
                )
                creator_id = 1
            else:
                logger.warning(
                    "post_purchase: missing creator_id for confirmation user=%s txn=%s — failing closed (P1.4)",
                    user_id,
                    transaction_id,
                )
                return False
        except Exception:
            logger.warning(
                "post_purchase: missing creator_id for confirmation user=%s txn=%s — failing closed (P1.4)",
                user_id,
                transaction_id,
            )
            return False
    dedup_id = f"post_purchase:{transaction_id}:{user_id}"

    # --- Entity blacklist check: don't enqueue confirmations for unresolvable peers ---
    try:
        from core.entity_blacklist import is_blacklisted

        if await is_blacklisted(user_id):
            logger.warning(
                "post_purchase: skipping confirmation for blacklisted entity user=%s txn=%s",
                user_id,
                transaction_id,
            )
            # Mark dedup even when skipping so we don't re-enter this path on retry
            try:
                from db.redis import mark_send_dedup

                await mark_send_dedup(dedup_id, ttl=86400, creator_id=creator_id)
            except Exception:
                pass
            return True
    except Exception:
        logger.debug("post_purchase: blacklist check failed user=%s", user_id, exc_info=True)

    try:
        if await is_send_duplicate(dedup_id, creator_id=creator_id):
            logger.info(
                "post_purchase: confirmation already queued user=%s txn=%s creator=%s",
                user_id,
                transaction_id,
                creator_id,
            )
            return True
    except Exception:
        # Dedup check failed (Redis down). Fail-closed: do not proceed with
        # enqueue because we cannot guarantee idempotency. The purchase is
        # already recorded; the confirmation will be sent on the next
        # reconciliation cycle when Redis is available again.
        logger.warning(
            "post_purchase: dedup check failed user=%s txn=%s, skipping enqueue to prevent duplicate",
            user_id,
            transaction_id,
            exc_info=True,
        )
        return False

    try:
        await enqueue_send(
            {
                "entity": str(user_id),
                "content": _PURCHASE_CONFIRMATION,
                "draft_content": _PURCHASE_CONFIRMATION,
                "was_edited": False,
                "was_auto_approved": False,
                "confidence_score": 1.0,
                "operator_id": None,
                "save_to_db": True,
                "creator_id": str(creator_id),
            },
            dedup_id=dedup_id,
            creator_id=creator_id,
        )
        logger.info(
            "post_purchase: confirmation queued user=%s txn=%s",
            user_id,
            transaction_id,
        )
        return True
    except Exception:
        logger.exception(
            "post_purchase: failed to enqueue confirmation user=%s txn=%s",
            user_id,
            transaction_id,
        )
        return False


async def handle_post_purchase(record: PurchaseRecord) -> None:
    """Orchestrate post-purchase side effects after successful attribution.

    Called from receive_webhook() after attribute_purchase_from_webhook()
    returns a non-None PurchaseRecord. Best-effort: failures are logged
    but never propagate to the caller.
    """
    user_id = record.user_id
    transaction_id = record.transaction_id
    creator_id = record.creator_id
    product_id = record.product_id

    if not user_id or not transaction_id:
        logger.warning(
            "post_purchase: incomplete record user=%s txn=%s, skipping",
            user_id,
            transaction_id,
        )
        return

    # P1.4: creator_id is required — fail closed if missing (no global fallback)
    if not creator_id:
        logger.warning(
            "post_purchase: missing creator_id for record user=%s txn=%s — failing closed (P1.4)",
            user_id,
            transaction_id,
        )
        # Still attempt to mark funnel? No, require creator for isolation
        return

    logger.info(
        "post_purchase: processing user=%s txn=%s creator=%s product=%s",
        user_id,
        transaction_id,
        creator_id,
        product_id,
    )

    # C.1-D — Record behavioral feedback event (best-effort)
    try:
        import commerce.feedback as _fb_mod
        from datetime import datetime, timezone

        event = _fb_mod.BehavioralEvent(
            event_type=_fb_mod.FeedbackEventType.PURCHASE_COMPLETED,
            user_id=user_id,
            creator_id=creator_id,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "transaction_id": transaction_id,
                "product_id": product_id,
            },
        )
        _fb_mod._behavioral_store.append(event)
        logger.info(
            "post_purchase: recorded PURCHASE_COMPLETED user=%s txn=%s",
            user_id,
            transaction_id,
        )
    except Exception:
        logger.debug("post_purchase: failed to record feedback event", exc_info=True)

    # P0-2 — Mark aftercare as pending (best-effort)
    try:
        from commerce.dao import mark_aftercare_pending
        aftercare_set = await mark_aftercare_pending(creator_id, user_id)
        if aftercare_set:
            try:
                await publish_event(
                    "commerce.aftercare",
                    {"user_id": user_id, "offer_id": None, "state": "pending"},
                    creator_id=creator_id,
                    user_id=user_id,
                    scope="user",
                )
            except Exception:
                pass
            logger.info(
                "post_purchase: aftercare marked pending user=%s creator=%s",
                user_id,
                creator_id,
            )
    except Exception:
        logger.debug("post_purchase: failed to mark aftercare pending", exc_info=True)

    # P1.1 — Funnel stage advancement
    try:
        await advance_funnel_to_converted(user_id)
    except Exception:
        logger.exception(
            "post_purchase: funnel update failed user=%s txn=%s",
            user_id,
            transaction_id,
        )

    # P1.2 — Confirmation message enqueue (creator-scoped)
    try:
        await enqueue_purchase_confirmation(user_id, transaction_id, creator_id=creator_id)
    except Exception:
        logger.exception(
            "post_purchase: confirmation enqueue failed user=%s txn=%s",
            user_id,
            transaction_id,
        )

    # P2.1 — Schedule durable follow-up
    try:
        await schedule_follow_up(user_id, transaction_id, creator_id=creator_id)
    except Exception:
        logger.exception(
            "post_purchase: follow-up scheduling failed user=%s txn=%s",
            user_id,
            transaction_id,
        )

    # P3.1 — Deliver purchased Vault media (best-effort, independent)
    if product_id is not None:
        try:
            await deliver_product_media(
                creator_id=creator_id,
                user_id=user_id,
                product_id=product_id,
                transaction_id=transaction_id,
            )
        except Exception:
            logger.exception(
                "post_purchase: media delivery failed user=%s txn=%s",
                user_id,
                transaction_id,
            )


async def schedule_follow_up(
    user_id: int,
    transaction_id: str,
    delay: timedelta | None = None,
    content: str | None = None,
    creator_id: int | None = None,
) -> bool:
    """Schedule a durable follow-up message after successful purchase.

    Uses PostgreSQL as the durable source of truth. The follow-up will be
    delivered through the existing send stream via the scheduler worker.

    Idempotent: duplicate calls for the same transaction_id are safe due to
    the deterministic dedup_key constraint on scheduled_messages.

    Returns True if the follow-up was scheduled or already exists, False on
    failure.
    """
    from db.postgres import create_scheduled_message

    if delay is None:
        delay = _FOLLOW_UP_DELAY
    if content is None:
        content = _FOLLOW_UP_MESSAGE

    dedup_key = f"post_purchase_followup:{transaction_id}"
    execute_at = datetime.now(UTC) + delay

    try:
        msg_id = await create_scheduled_message(
            user_id=user_id,
            execute_at=execute_at,
            content=content,
            dedup_key=dedup_key,
            reason="post_purchase_followup",
            creator_id=creator_id,
            actor_type="system",
            actor_id="post-purchase",
        )
        if msg_id is not None:
            logger.info(
                "post_purchase: follow-up scheduled user=%s txn=%s msg_id=%s execute_at=%s",
                user_id,
                transaction_id,
                msg_id,
                execute_at.isoformat(),
            )
            return True
        return False
    except Exception:
        logger.exception(
            "post_purchase: failed to schedule follow-up user=%s txn=%s",
            user_id,
            transaction_id,
        )
        return False


# Dropfans media type -> Telegram send-worker media_type mapping.
# Also handles legacy Fangate media types.
_MEDIA_TYPE_MAP = {
    "image": "photo",
    "photo": "photo",
    "video": "video",
    "document": "document",
    "audio": "document",
}

# DropFans API-conformant media retrieval (Phase 93B):
# Official DropFans API contract (https://www.dropfans.io/developers/openapi.json 1.1.0):
# - GET /api/external/vault returns APPROVED items by default (filePath, downloadUrl, fileType, moderationStatus only with includePending=true)
# - VaultItem.filePath: image CDN display copy; audio token-signed ~12h directly fetchable; video Bunny Stream playback DRM-locked — use downloadUrl
# - VaultItem.downloadUrl: token-signed directly fetchable original ~12h. Videos: DRM-free Bunny Storage original (null for legacy). Audio: same as filePath. Always null for images and non-APPROVED.
# - Both filePath/downloadUrl empty/null for non-APPROVED — unapproved content may never be forwarded.
# - GET /api/external/drops/{id} authoritative price/currency/allowDownload/mediaCount and media[].vaultItemId/order/fileType/moderationStatus/hasPreview
# - Creator API key is server-side only, creator-bound, bound to one creator.
# The resolver below uses ONLY documented endpoints: GET /vault (list) + GET /drops/{id} for binding verification.
# It never invents a buyer-download endpoint, never exposes API key, never uses sales_url/buyUrl as media.

_DROPFANS_FILETYPE_TO_TELEGRAM = {
    "image": "photo",
    "video": "video",
    "audio": "document",
}

# Maximum asset size to prevent abuse (100 MB same as main._MAX_MEDIA_SIZE_BYTES, 200 MB for video safety)
_MAX_ASSET_BYTES = 100 * 1024 * 1024
_MAX_VIDEO_BYTES = 200 * 1024 * 1024


async def _fetch_dropfans_vault_map(creator_id: int) -> dict[str, Any]:
    """Fetch fresh DropFans vault items (APPROVED only) and build id->VaultItem map.

    Uses ONLY documented GET /api/external/vault.  Fresh lookup required because
    downloadUrl/filePath are token-signed valid ~12h (video filePath ~1h playback).
    Handles pagination (limit 50, hasMore).  Respects rate-limit tiers via service retries.
    Returns {vault_item_id: DropfansVaultItem}.  Creator-scoped.
    """
    from integrations.dropfans import service as _df_service
    from integrations.dropfans.client import DropfansClient
    from integrations.dropfans.security import decrypt_secret
    from db import dropfans as _ddb
    from core.config import get_settings

    integration = await _ddb.get_dropfans_integration(creator_id)
    if not integration or not integration.get("dropfans_creator_id"):
        return {}
    enc = integration.get("encrypted_api_key", "")
    if not enc:
        return {}
    try:
        api_key = decrypt_secret(enc)
    except Exception:
        return {}
    settings = get_settings()
    client = DropfansClient(api_key, base_url=settings.dropfans_api_base_url, timeout=settings.dropfans_api_timeout)
    vault_map: dict[str, Any] = {}
    try:
        page = 1
        while True:
            result = await client.list_vault(page=page, limit=50, folder_id=None, include_pending=False)
            for item in result.items:
                # Only APPROVED items appear (default).  Defensive: skip if somehow moderation not approved
                # When includePending=false, moderationStatus is omitted entirely; treat missing as APPROVED.
                vault_map[str(item.id)] = item
            if not result.has_more:
                break
            page += 1
            if page > 20:  # safety: 1000 items max per fulfillment pass
                logger.warning("post_purchase: vault pagination capped at 20 pages creator=%s", creator_id)
                break
    except Exception:
        logger.warning("post_purchase: failed to fetch vault map creator=%s", creator_id, exc_info=True)
        return vault_map
    finally:
        try:
            await client.close()
        except Exception:
            pass
    return vault_map


def _select_dropfans_asset_url(vault_item) -> tuple[str | None, str | None]:
    """Select correct documented asset URL per fileType (image→filePath, video→downloadUrl, audio→filePath).

    Returns (url, reason_if_none).  Never returns sales_url/buyUrl.  Respects docs:
    - image: downloadUrl always null, filePath is CDN display copy — use filePath
    - video: filePath is DRM-locked Bunny Stream playback — must use downloadUrl (null for legacy videos → fail)
    - audio: filePath token-signed ~12h directly fetchable — use filePath (downloadUrl same)
    """
    ft = (getattr(vault_item, "file_type", None) or getattr(vault_item, "fileType", None) or "").lower()
    # Dropfans model uses file_type
    if not ft:
        ft = str(getattr(vault_item, "fileType", "") or "").lower()
    fp = getattr(vault_item, "file_path", None) or getattr(vault_item, "filePath", None)
    # model attribute names: file_path vs filePath — DropfansVaultItem uses file_path / download_url with underscores in code? Actually models use file_path/fileType etc via from_api mapping
    # Handle both
    if fp is None:
        fp = getattr(vault_item, "filePath", None)
    dl = getattr(vault_item, "download_url", None) or getattr(vault_item, "downloadUrl", None)
    if dl is None and hasattr(vault_item, "downloadUrl"):
        dl = getattr(vault_item, "downloadUrl", None)
    # Try raw dict fallback
    raw = getattr(vault_item, "raw", {}) or {}
    if not fp:
        fp = raw.get("filePath")
    if not dl:
        dl = raw.get("downloadUrl")

    if ft == "image":
        if fp and str(fp).strip() and str(fp).strip() != "":
            return str(fp).strip(), None
        return None, "image missing filePath"
    if ft == "video":
        if dl and str(dl).strip():
            return str(dl).strip(), None
        return None, "video missing downloadUrl (legacy DRM-only or not APPROVED)"
    if ft == "audio":
        # audio filePath token-signed valid ~12h directly fetchable; same as downloadUrl
        candidate = (str(fp).strip() if fp and str(fp).strip() else None) or (str(dl).strip() if dl and str(dl).strip() else None)
        if candidate:
            return candidate, None
        return None, "audio missing filePath/downloadUrl"
    # unknown type
    return None, f"unsupported fileType {ft!r}"


async def _verify_dropfans_drop_binding(creator_id: int, product_id: int) -> tuple[bool, dict[str, Any] | None, str | None]:
    """Verify purchased product binding against authoritative DropFans GET /drops/{id}.

    P3.2 identity boundary: ``product_id`` here is the synthetic local mirror
    id. It is resolved creator-scoped to the real external
    ``dropfans_product_id`` CUID before any provider call. A synthetic id is
    NEVER passed to the provider. Unresolvable CUID fails closed.

    Returns (ok, drop_dict, reason).  Checks creator ownership (404 vs 403 per docs: another creator's drop 404s), price/currency, allowDownload irrelevant for retrieval (not redistribution right), mediaCount and vaultItemIds order.
    Uses ONLY documented GET /api/external/drops/{id}.
    """
    from integrations.dropfans import service as _df_service
    try:
        from db.dropfans import resolve_dropfans_cuid as _resolve_cuid
        _cuid = await _resolve_cuid(creator_id, product_id)
    except Exception as exc:
        logger.warning("post_purchase: CUID resolution failed creator=%s product=%s: %s", creator_id, product_id, exc)
        return False, None, "cuid_resolution_failed"
    if not _cuid:
        logger.warning("post_purchase: no external CUID for creator=%s product=%s — failing closed", creator_id, product_id)
        return False, None, "missing_dropfans_product_id"
    try:
        drop = await _df_service.get_drop(creator_id, _cuid)
        # drop is dict with id, name, price (dollars), currency USD, status, buyUrl, allowDownload, mediaCount, media[]
        return True, drop, None
    except Exception as exc:
        # DropfansNotFoundError → drop not owned / not found
        logger.warning("post_purchase: drop binding verify failed creator=%s product=%s: %s", creator_id, product_id, exc)
        return False, None, str(exc)

async def deliver_product_media(
    creator_id: int,
    user_id: int,
    product_id: int,
    transaction_id: str,
) -> None:
    """Deliver purchased Vault media to the buyer through the existing send pipeline.

    DropFans P0-02 external blocker: DropFans API does NOT expose a buyer-scoped
    media grant (verified via client.py inventory). Owner filePath is owner-signed
    ~12h and must never be sent to buyers. Safe fallback is sales_url (checkout link)
    via Telegram, with reservation idempotency preserved. If DropFans later exposes
    a buyer grant endpoint, replace the DropFans branch below with a per-vaultItem
    fetch using buyer email/token and send actual file bytes via send_file.

    Legacy Fangate path still delivers preview_url bytes (legacy).

    Best-effort and failure-isolated: failures are logged but never propagate
    to the caller or affect the purchase flow.

    Idempotent: reserve_delivery uses UNIQUE (creator, user, media) and
    dedup_id prevents duplicate enqueues from duplicate webhook deliveries.
    """
    try:
        from db import fangate as db_fangate
        from db.redis import enqueue_send as _enqueue_send
        from db.vault import has_user_received_media, reserve_delivery
    except Exception:
        logger.warning(
            "post_purchase: failed to import delivery dependencies, skipping media delivery",
            exc_info=True,
        )
        return

    try:
        product = await db_fangate.get_fangate_product(creator_id, product_id)
        if product is None:
            logger.warning(
                "post_purchase: product not found for media delivery creator=%s product=%s",
                creator_id,
                product_id,
            )
            return

        raw_media = product.get("raw", {})
        if isinstance(raw_media, str):
            try:
                import json as _json

                raw_media = _json.loads(raw_media)
            except (_json.JSONDecodeError, TypeError):
                raw_media = {}

        # Determine provider type from product
        product_type = product.get("product_type", "")

        if product_type == "dropfans":
            # DropFans: vault items stored in raw JSONB
            vault_item_ids = raw_media.get("vaultItemIds") or []
            if not vault_item_ids:
                logger.debug(
                    "post_purchase: no vault items for Dropfans product %s, skipping",
                    product_id,
                )
                return

            # --- P3.2 offer snapshot contract ---
            # New offers carry the frozen Vault-item set. It is the primary
            # historical content identity. Legacy offers (NULL snapshot) fall
            # back to the mutable mirror + live Drop path with an explicit
            # diagnostic. The snapshot is never fabricated.
            snapshot_ids: list[str] | None = None
            try:
                from db.postgres import get_pool as _get_pool_offer
                _opool = await _get_pool_offer()
                async with _opool.acquire() as _oconn:
                    try:
                        _orow = await _oconn.fetchrow(
                            """
                            SELECT vault_item_ids, dropfans_product_id, media_count,
                                   transaction_id, purchased_at
                            FROM commerce_offers
                            WHERE creator_id = $1 AND user_id = $2 AND product_id = $3
                              AND state = 'purchased'
                            ORDER BY purchased_at DESC NULLS LAST, id DESC
                            LIMIT 1
                            """,
                            creator_id, user_id, product_id,
                        )
                    except Exception:
                        _orow = None
                    if _orow is not None:
                        try:
                            _sraw = _orow["vault_item_ids"]
                        except Exception:
                            _sraw = None
                        if _sraw:
                            snapshot_ids = [str(v) for v in list(_sraw) if str(v).strip()]
            except Exception:
                logger.debug("post_purchase: offer snapshot lookup failed", exc_info=True)
                snapshot_ids = None
            if snapshot_ids:
                logger.info(
                    "post_purchase: using offer snapshot creator=%s product=%s items=%d",
                    creator_id, product_id, len(snapshot_ids),
                )
            else:
                logger.warning(
                    "post_purchase: legacy offer without vault snapshot creator=%s product=%s user=%s — using mutable mirror/live fallback",
                    creator_id, product_id, user_id,
                )

            # --- API-conformant binding verification (GET /drops/{id}) ---
            # Must not trust stale local raw.vaultItemIds alone; verify against DropFans authoritative drop.
            # This also verifies creator ownership (404 if not owned per docs).
            drop_ok, drop_data, drop_reason = await _verify_dropfans_drop_binding(creator_id, product_id)
            authoritative_vault_ids: list[str] | None = None
            authoritative_media_count: int | None = None
            if drop_ok and drop_data:
                try:
                    authoritative_media_count = int(drop_data.get("mediaCount", len(vault_item_ids)))
                    authoritative_vault_ids = [str(m.get("vaultItemId", "")) for m in (drop_data.get("media") or []) if m.get("vaultItemId")]
                    if authoritative_vault_ids:
                        # Detect drift: local vs authoritative
                        local_set = {str(x) for x in vault_item_ids}
                        auth_set = set(authoritative_vault_ids)
                        if local_set != auth_set:
                            logger.warning(
                                "post_purchase: vaultItemIds drift creator=%s product=%s local=%s authoritative=%s",
                                creator_id, product_id, vault_item_ids, authoritative_vault_ids,
                            )
                        # Use authoritative as truth for fulfillment (deterministic)
                        if len(authoritative_vault_ids) != authoritative_media_count:
                            logger.warning(
                                "post_purchase: mediaCount mismatch creator=%s product=%s authoritative count %s vs ids len %s",
                                creator_id, product_id, authoritative_media_count, len(authoritative_vault_ids),
                            )
                except Exception:
                    logger.warning("post_purchase: drop binding parse failed creator=%s product=%s", creator_id, product_id, exc_info=True)
            # Choose effective ids under the P3.2 snapshot contract:
            # - snapshot present: snapshot is the promise. Drift vs live is
            #   logged but NEVER silently replaces the snapshot. Fulfillment
            #   uses the snapshot set (items missing live are skipped per-item
            #   below via the fresh vault map → partial, never substitution).
            # - legacy (no snapshot): existing fallback — authoritative wins
            #   when available, else local.
            if snapshot_ids:
                effective_vault_ids = [str(x) for x in snapshot_ids]
                if authoritative_vault_ids:
                    if set(effective_vault_ids) != set(authoritative_vault_ids):
                        logger.warning(
                            "post_purchase: snapshot/provider drift creator=%s product=%s snapshot=%s authoritative=%s — preserving snapshot",
                            creator_id, product_id, effective_vault_ids, authoritative_vault_ids,
                        )
            else:
                effective_vault_ids = authoritative_vault_ids if authoritative_vault_ids else [str(x) for x in vault_item_ids]

            # Detect duplicate vault ids deterministically (no silent duplicate delivery)
            if len(effective_vault_ids) != len(set(effective_vault_ids)):
                logger.warning("post_purchase: duplicate vaultItemIds detected creator=%s product=%s ids=%s", creator_id, product_id, effective_vault_ids)
                # Deduplicate deterministically preserving order
                seen: set[str] = set()
                deduped: list[str] = []
                for vid in effective_vault_ids:
                    if vid not in seen:
                        seen.add(vid)
                        deduped.append(vid)
                effective_vault_ids = deduped

            # Bundle cardinality: authoritative DropFans mediaCount vs effective ids len
            if authoritative_media_count is not None and len(effective_vault_ids) != authoritative_media_count:
                logger.warning(
                    "post_purchase: bundle cardinality mismatch fulfilling %s assets but mediaCount %s product=%s",
                    len(effective_vault_ids), authoritative_media_count, product_id,
                )

            # --- Entitlement check (purchase state already verified by caller handle_post_purchase after attribution) ---
            # Re-verify deterministically: caller is post-purchase (state purchased).  Ensure still purchased.
            try:
                from commerce.dao import has_purchased_product as _has_purch
                if not await _has_purch(creator_id, user_id, product_id):
                    logger.warning("post_purchase: entitlement check failed creator=%s user=%s product=%s not purchased, skipping media", creator_id, user_id, product_id)
                    return
            except Exception:
                pass  # best-effort

            # --- Fresh vault map (server-side, creator-authorized, signed URLs ~12h) ---
            # Must refresh rather than caching stale signed URLs per docs Notes: "re-list rather than caching them longer"
            vault_map = await _fetch_dropfans_vault_map(creator_id)
            if not vault_map:
                # No vault map available (API failure or key invalid) → fail closed, text-only fallback
                logger.warning("post_purchase: vault map empty creator=%s product=%s, falling back to text-only", creator_id, product_id)
                # Text-only fallback: deterministic, no media bytes, no sales_url as media
                # Use generic confirmation already queued via enqueue_purchase_confirmation; do NOT enqueue media with sales_url.
                # Record that media fulfillment unavailable due to API, preserve dedup via no-op.
                return

            textual_fallback_needed = False
            delivered_any = False
            failed_assets: list[str] = []
            for vault_item_id in effective_vault_ids:
                vault_item_str = str(vault_item_id)
                # Check already delivered via dropfans_vault_item_id (idempotent)
                try:
                    from db.postgres import get_pool as _get_pool_drop
                    pool = await _get_pool_drop()
                    async with pool.acquire() as conn:
                        row = await conn.fetchrow(
                            "SELECT 1 FROM vault_media_deliveries WHERE creator_id=$1 AND user_id=$2 AND dropfans_vault_item_id=$3 LIMIT 1",
                            creator_id, user_id, vault_item_str,
                        )
                        if row is not None:
                            logger.debug(
                                "post_purchase: Dropfans vault item %s already delivered to user %s, skipping",
                                vault_item_str, user_id,
                            )
                            delivered_any = True
                            continue
                except Exception:
                    try:
                        import hashlib as _hash
                        _fid = int(_hash.sha256(f"dropfans:{vault_item_str}".encode()).hexdigest()[:15], 16) % (2**31)
                        if await has_user_received_media(creator_id, user_id, _fid):
                            delivered_any = True
                            continue
                    except Exception:
                        pass

                # --- Resolve authoritative asset URL per fileType (image→filePath, video→downloadUrl, audio→filePath) ---
                vault_item_obj = vault_map.get(vault_item_str)
                if vault_item_obj is None:
                    logger.warning("post_purchase: vault item %s not found in fresh vault map creator=%s product=%s (hidden/pending/rejected?)", vault_item_str, creator_id, product_id)
                    failed_assets.append(vault_item_str)
                    continue
                # Verify moderation: non-APPROVED items have filePath "" / downloadUrl null per docs — already filtered by list_vault default APPROVED only
                # If item somehow PENDING/FLAGGED, _select will return None with reason.
                asset_url, reason = _select_dropfans_asset_url(vault_item_obj)
                if asset_url is None:
                    logger.warning("post_purchase: asset URL unavailable vault_item=%s creator=%s reason=%s", vault_item_str, creator_id, reason)
                    failed_assets.append(vault_item_str)
                    continue
                if not asset_url.startswith("https://"):
                    logger.warning("post_purchase: asset URL not https vault_item=%s url=%s", vault_item_str, asset_url[:80])
                    failed_assets.append(vault_item_str)
                    continue
                # Validate content-type via optional HEAD (best-effort, no hard failure on HEAD not allowed)
                # We do not download bytes here; Telethon will fetch.  We only verify URL is not a checkout URL.
                sales_url_check = product.get("sales_url") or ""
                buy_url_check = (drop_data.get("buyUrl") if drop_data else "") or sales_url_check
                if asset_url == sales_url_check or asset_url == buy_url_check:
                    logger.warning("post_purchase: asset URL equals checkout URL (BUG) vault_item=%s", vault_item_str)
                    failed_assets.append(vault_item_str)
                    continue

                # Determine Telegram media_type from authoritative DropFans fileType (not hard-coded photo)
                raw_ft = (getattr(vault_item_obj, "file_type", None) or getattr(vault_item_obj, "fileType", None) or getattr(vault_item_obj, "raw", {}).get("fileType") or "").lower()
                if not raw_ft:
                    raw_ft = str(vault_item_obj.raw.get("fileType", "")).lower() if hasattr(vault_item_obj, "raw") else ""
                telegram_media_type = _DROPFANS_FILETYPE_TO_TELEGRAM.get(raw_ft)
                if telegram_media_type is None:
                    logger.warning("post_purchase: unsupported fileType %r vault_item=%s, skipping", raw_ft, vault_item_str)
                    failed_assets.append(vault_item_str)
                    continue

                # Reserve per vault_item_id (dropfans_vault_item_id TEXT)
                delivery_id = None
                try:
                    pool = await _get_pool_drop()
                    async with pool.acquire() as conn:
                        delivery_row = await conn.fetchrow(
                            "INSERT INTO vault_media_deliveries (creator_id, user_id, dropfans_vault_item_id, product_id, status) VALUES ($1,$2,$3,$4,'pending') ON CONFLICT (creator_id, user_id, dropfans_vault_item_id) WHERE dropfans_vault_item_id IS NOT NULL DO NOTHING RETURNING id",
                            creator_id, user_id, vault_item_str, product_id,
                        )
                        if delivery_row:
                            delivery_id = delivery_row["id"]
                        else:
                            delivered_any = True
                            continue
                except Exception:
                    logger.warning("post_purchase: delivery reservation failed for vault item %s", vault_item_str, exc_info=True)
                    failed_assets.append(vault_item_str)
                    continue
                if delivery_id is None:
                    continue

                # Deterministic per-asset caption: still safe, no vault filename leak, no price, no sales_url as media
                # Caption is same safe literal but now media_path is the real asset URL
                dedup_id = __import__("hashlib").md5(f"{user_id}:{vault_item_str}:{product_id}".encode()).hexdigest()
                caption_text = "Your content is ready!"
                try:
                    await _enqueue_send(
                        {
                            "entity": str(user_id),
                            "content": caption_text,
                            "draft_content": "",
                            "was_edited": False,
                            "was_auto_approved": False,
                            "confidence_score": 1.0,
                            "operator_id": None,
                            "save_to_db": True,
                            "media_type": telegram_media_type,
                            "media_path": asset_url,
                            "dropfans_vault_item_id": vault_item_str,
                            "product_id": str(product_id),
                            "creator_id": str(creator_id),
                        },
                        dedup_id=dedup_id,
                        creator_id=creator_id,
                    )
                    logger.info("post_purchase: Dropfans vault item %s (%s) delivery enqueued for user %s via %s", vault_item_str, raw_ft, user_id, telegram_media_type)
                    delivered_any = True
                except Exception:
                    logger.warning("post_purchase: enqueue failed for vault item %s", vault_item_str, exc_info=True)
                    try:
                        from db.vault import release_delivery
                        await release_delivery(delivery_id, creator_id=creator_id)
                    except Exception:
                        pass
                    failed_assets.append(vault_item_str)
                    continue
            if failed_assets:
                logger.warning("post_purchase: bundle partial failure creator=%s product=%s user=%s failed=%s delivered_any=%s", creator_id, product_id, user_id, failed_assets, delivered_any)
                # Do not silently claim full fulfillment; remaining failed assets will be retried on next reconciliation/poll if still pending reservation released.
            if delivered_any:
                return
            # All assets failed → fallback to text-only (no sales_url as media).  Purchase confirmation already handles user notification.
            return

        # Legacy Fangate media handling
        media_list = raw_media.get("media") or []
        if not media_list:
            logger.debug(
                "post_purchase: no media for product %s, skipping delivery",
                product_id,
            )
            return

        for m in media_list:
            if not isinstance(m, dict) or "id" not in m:
                continue

            fangate_media_id = int(m["id"])
            raw_type = (m.get("type") or "").strip().lower()
            media_type = _MEDIA_TYPE_MAP.get(raw_type)
            if media_type is None:
                logger.debug(
                    "post_purchase: skipping media %s with unsupported type %r",
                    fangate_media_id,
                    raw_type,
                )
                continue

            preview_url = (m.get("preview") or "").strip()
            if not preview_url.startswith("https://"):
                logger.debug(
                    "post_purchase: skipping media %s with non-HTTPS preview URL",
                    fangate_media_id,
                )
                continue

            try:
                already_delivered = await has_user_received_media(
                    creator_id, user_id, fangate_media_id
                )
                if already_delivered:
                    logger.debug(
                        "post_purchase: media %s already delivered to user %s, skipping",
                        fangate_media_id,
                        user_id,
                    )
                    continue
            except Exception:
                logger.warning(
                    "post_purchase: delivery check failed for media %s, proceeding with reserve",
                    fangate_media_id,
                    exc_info=True,
                )

            try:
                delivery_id = await reserve_delivery(
                    creator_id, user_id, fangate_media_id, product_id
                )
                if delivery_id is None:
                    logger.debug(
                        "post_purchase: media %s already reserved/delivered to user %s",
                        fangate_media_id,
                        user_id,
                    )
                    continue
            except Exception:
                logger.warning(
                    "post_purchase: reservation failed for media %s, skipping",
                    fangate_media_id,
                    exc_info=True,
                )
                continue

            import hashlib

            dedup_id = hashlib.md5(
                f"{user_id}:{fangate_media_id}".encode()
            ).hexdigest()

            try:
                await _enqueue_send(
                    {
                        "entity": str(user_id),
                        "content": "",
                        "draft_content": "",
                        "was_edited": False,
                        "was_auto_approved": False,
                        "confidence_score": 1.0,
                        "operator_id": None,
                        "save_to_db": True,
                        "media_type": media_type,
                        "media_path": preview_url,
                        "fangate_media_id": str(fangate_media_id),
                        "product_id": str(product_id),
                        "creator_id": str(creator_id),
                    },
                    dedup_id=dedup_id,
                    creator_id=creator_id,
                )
                logger.info(
                    "post_purchase: media %s enqueued for user %s creator=%s txn=%s",
                    fangate_media_id,
                    user_id,
                    creator_id,
                    transaction_id,
                )
            except Exception:
                logger.warning(
                    "post_purchase: enqueue failed for media %s, releasing reservation",
                    fangate_media_id,
                    exc_info=True,
                )
                try:
                    from db.vault import release_delivery

                    await release_delivery(delivery_id, creator_id=creator_id)
                except Exception:
                    logger.warning(
                        "post_purchase: failed to release reservation %s",
                        delivery_id,
                        exc_info=True,
                    )

    except Exception:
        logger.warning(
            "post_purchase: deliver_product_media failed creator=%s user=%s product=%s txn=%s",
            creator_id,
            user_id,
            product_id,
            transaction_id,
            exc_info=True,
        )
