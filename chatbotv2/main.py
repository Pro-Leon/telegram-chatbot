import asyncio
import logging
from pathlib import Path

from telethon.errors import (
    FloodError,
    FloodWaitError,
    PeerFloodError,
    RPCError,
    UserIsBlockedError,
)

from chatbotv2.client import get_client, send_file
from chatbotv2.handlers import setup_handlers
from core.config import get_settings
from core.event_bus import publish_event
from core.logging_config import setup_logging
from core.shutdown import is_shutting_down, set_shutting_down
from core.worker_heartbeat import write_heartbeat
from db.postgres import close_pool, init_pool, save_outbound_after_send, verify_schema
from db.redis import (
    SEND_DLQ_REASON_POST_SEND,
    SEND_DLQ_REASON_SEND_ERROR,
    SEND_DLQ_REASON_UNKNOWN,
    ack_send,
    check_send_rate_limit,
    clear_send_repair_needed,
    clear_unknown_send_attempt,
    close_redis,
    confirm_send_dedup,
    enqueue_send,
    ensure_consumer_group,
    get_or_create_send_random_id,
    get_send_dedup_value,
    get_send_rate_limit_wait,
    is_send_duplicate,
    list_unknown_vault_reservation_ids,
    mark_send_dedup,
    move_send_to_dlq,
    read_send_messages,
    record_send_repair_needed,
    record_unknown_send_attempt,
    release_send_dedup,
    requeue_stalled_send_messages,
    try_reserve_send_dedup,
)
from db.vault import release_stale_reservations

logger = logging.getLogger("chatbotv2.main")
_settings = get_settings()
_cleanup_done = False

# Allowed media types for outbound sends.
_ALLOWED_MEDIA_TYPES = frozenset({"photo", "video", "document"})

# Maximum media file size (100 MB) to prevent accidental large transfers.
_MAX_MEDIA_SIZE_BYTES = 100 * 1024 * 1024


def _validate_media_path(media_path: str) -> str | None:
    """Validate a media file path or URL for safe outbound sending.

    Accepts:
    - Local file paths (resolved, checked for existence and size)
    - HTTPS URLs (passed through for Telethon to download)

    Returns the resolved path/URL if valid, None otherwise.
    """
    if not media_path or not media_path.strip():
        return None
    media_path = media_path.strip()
    if media_path.startswith("https://"):
        return media_path
    if media_path.startswith("http://"):
        logger.warning("HTTP URLs not allowed for media (must be HTTPS): %s", media_path[:80])
        return None
    try:
        resolved = Path(media_path).resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    try:
        if resolved.stat().st_size > _MAX_MEDIA_SIZE_BYTES:
            logger.warning("Media file too large: %s", resolved)
            return None
    except OSError:
        return None
    return str(resolved)


# H4 Batch 1: stable DLQ reason distinguishing post-send persistence /
# observability failure (Telegram already accepted; repair/reconcile only)
# from pre-send "send_error" (safe to resend). Canonical values live in
# db.redis (SEND_DLQ_REASON_*). H4 Batch 3 adds SEND_DLQ_REASON_UNKNOWN for
# ambiguous transport outcomes: never certify UNKNOWN as failed, never
# auto-resend it.


async def _handle_unknown_send_result(
    msg_id: str,
    data: dict,
    *,
    dedup_id: str | None,
    generation_id: str | None,
    creator_id: int | None,
    delivery_reservation_id,
    dropfans_row_id,
    error: BaseException,
) -> None:
    """Best-effort UNKNOWN persistence for an ambiguous Telegram attempt.

    H4 Batch 3: Telegram may or may not have accepted — the process cannot
    prove either. Records the attempt state (never raises), DLQs with the
    unknown reason (original ACKed by the move), and returns. The caller
    keeps the dedup lease and vault reservation untouched; a retry after
    UNKNOWN is at-least-once on the wire by library constraint.
    """
    try:
        vault_ids = [v for v in (delivery_reservation_id, dropfans_row_id) if v is not None]
        await record_unknown_send_attempt(
            dedup_id,
            creator_id=creator_id,
            generation_id=generation_id,
            error_type=type(error).__name__,
            error_detail=str(error),
            vault_reservation_ids=vault_ids,
        )
    except Exception:
        logger.debug("unknown attempt record failed for %s", msg_id, exc_info=True)
    try:
        payload = dict(data) if isinstance(data, dict) else None
        if isinstance(payload, dict):
            payload["unknown_error"] = f"{type(error).__name__}: {str(error)[:200]}"
        await move_send_to_dlq(
            msg_id,
            SEND_DLQ_REASON_UNKNOWN,
            payload=payload,
            worker_id="bot_main",
        )
    except Exception:
        logger.warning(
            "unknown-result DLQ move failed for %s (entry remains pending)",
            msg_id,
            exc_info=True,
        )


# H4 Batch 3: truthful transport outcome classification against installed
# Telethon 1.44.0. Only "refused" errors prove Telegram did not accept:
# UserIsBlockedError (server refused: blocked) and ValueError/TypeError
# (client-side, pre-wire). EVERYTHING else from the transport await —
# timeouts, resets, flood-family, other RPC errors, unexpected exceptions —
# is UNKNOWN: the request may have reached the server.
_REFUSED_TRANSPORT_ERRORS = (UserIsBlockedError, ValueError, TypeError)


def _classify_transport_error(exc: BaseException) -> str:
    """Return "refused" (proven pre-send) or "unknown" (ambiguous)."""
    if isinstance(exc, _REFUSED_TRANSPORT_ERRORS):
        return "refused"
    return "unknown"


async def _handle_post_send_failure(
    msg_id: str,
    data: dict,
    *,
    dedup_id: str | None,
    generation_id: str | None,
    creator_id: int | None,
    dedup_reserved,
    delivery_reservation_id,
    dropfans_pending_info,
    telegram_message_id=None,
    entity=None,
) -> None:
    """Repair path for failures after proven Telegram acceptance (H4 Batch 1).

    Invariants (central H4 truthfulness rule):
    * never emits message.send_failed,
    * never releases a vault/dropfans reservation as pre-send cleanup,
    * never classifies the delivery as failed.

    Fully swallowing by contract: must never raise, so a fault here cannot
    fall through to a pre-send failure classification.
    """
    # 1. Ensure the delivered marker (confirm, never release, post-send).
    try:
        if dedup_id:
            if dedup_reserved:
                tok = dedup_reserved if isinstance(dedup_reserved, str) else None
                await confirm_send_dedup(dedup_id, creator_id=creator_id, token=tok)
            else:
                await mark_send_dedup(dedup_id, creator_id=creator_id)
    except Exception:
        logger.debug("post-send dedup confirm failed for %s", msg_id, exc_info=True)
    # 2. Best-effort finalize of the proven delivery (conditional,
    # idempotent UPDATE ... WHERE pending). A failure preserves the pending
    # reservation for later reconciliation — never converted into a
    # pre-send release.
    try:
        if delivery_reservation_id is not None:
            from db.vault import finalize_delivery as _finalize

            await _finalize(delivery_reservation_id, telegram_message_id=telegram_message_id, creator_id=creator_id)
    except Exception:
        logger.warning(
            "post-send vault finalization failed for reservation %s",
            delivery_reservation_id,
            exc_info=True,
        )
    try:
        if dropfans_pending_info is not None:
            from db.vault import finalize_dropfans_delivery as _finalize_drop

            _c, _vid = dropfans_pending_info
            _uid = int(entity) if entity and str(entity).isdigit() else None
            if _uid is not None:
                await _finalize_drop(_c, _uid, _vid, telegram_message_id=telegram_message_id)
    except Exception:
        logger.warning(
            "post-send DropFans finalization failed for %s",
            dropfans_pending_info,
            exc_info=True,
        )
    # 3. DLQ for repair/reconciliation with the stable post-send reason.
    # move_send_to_dlq ACKs the original entry; creator/generation survive
    # inside the stored payload. H4 Batch 3: enrich the payload with the
    # evidence the repair path needs (Telegram id, vault reservation ids) —
    # additive keys only; the original send-stream fields are untouched.
    try:
        _repair_payload = dict(data) if isinstance(data, dict) else None
        if isinstance(_repair_payload, dict):
            _repair_payload["post_send_telegram_message_id"] = telegram_message_id
            if delivery_reservation_id is not None:
                _repair_payload["post_send_delivery_reservation_id"] = delivery_reservation_id
            if dropfans_pending_info is not None:
                _c, _vid = dropfans_pending_info
                _uid = int(entity) if entity and str(entity).isdigit() else None
                if _uid is not None:
                    _repair_payload["post_send_dropfans"] = {
                        "creator": _c,
                        "user_id": _uid,
                        "vault_item_id": _vid,
                    }
        await move_send_to_dlq(
            msg_id,
            SEND_DLQ_REASON_POST_SEND,
            payload=_repair_payload,
            worker_id="bot_main",
        )
    except Exception:
        logger.warning(
            "post-send DLQ write failed for %s (entry remains pending, delivered marker set)",
            msg_id,
            exc_info=True,
        )


async def _handle_send_entry(client, msg_id: str, data: dict) -> None:
    """Handle a single send stream entry via the existing send path.

    Preserves dedup, rate limit, blacklist, reservation, send, ACK/DLQ.
    Used for both normal XREADGROUP and XAUTOCLAIM reclaimed entries.
    """
    # dedup + creator isolation, rate limit, blacklist, vault, send, ack/dlq — exact same as normal loop
    # Inline implementation shared via helper to avoid duplication; see _process_send_stream body.
    # This helper is a thin dispatcher — actual logic lives in _process_send_entry_inner.
    await _process_send_entry_inner(client, msg_id, data)


async def _process_send_entry_inner(client, msg_id: str, data: dict) -> None:
    """Inner send processing — extracted from _process_send_stream for reclaim reuse."""
    # P1.4: creator_id is REQUIRED for send stream – authoritative, fail closed if missing/invalid
    _cid_raw = data.get("creator_id")
    if not _cid_raw or not str(_cid_raw).strip().isdigit():
        logger.warning("send stream %s missing/invalid creator_id, failing closed DLQ (P1.4)", msg_id)
        await move_send_to_dlq(msg_id, "creator_context_unavailable", payload=dict(data), worker_id="bot_main")
        return
    dedup_reserved = False  # tracks whether we hold lease for this dedup
    # H4 Batch 1: partitions pre-send vs post-send failure semantics. Set to
    # True exactly once, immediately after the transport send returns a
    # message object (proven Telegram acceptance). Any later throw is a
    # post-send persistence/observability failure and must never emit
    # message.send_failed, release vault reservations, or DLQ as send_error.
    telegram_accepted = False
    repair_needed_recorded = False  # H4 Batch 3 (D6): confirm failed post-acceptance; success path must not clear it
    try:
        dedup_id = data.get("dedup_id")
        generation_id = data.get("generation_id") or None
        creator_id = int(str(_cid_raw).strip())
        # P1.6 R-03 / P1.8: check dedup state before processing – distinguish reserved (with token) vs delivered
        if dedup_id:
            try:
                _dedup_val = await get_send_dedup_value(dedup_id, creator_id=creator_id)
                if _dedup_val is not None:
                    if _dedup_val.startswith("reserved:"):
                        logger.info("Send dedup reserved/in-flight dedup=%s creator=%s deferring without ACK (P1.8 ownership lease)", dedup_id, creator_id)
                        return  # leave pending for retry after lease expiry (prevents duplicate in crash window)
                    elif _dedup_val == "reserved":
                        # Legacy generic reserved (pre-P1.8) — treat same as reserved
                        logger.info("Send dedup reserved/in-flight (legacy) dedup=%s creator=%s deferring", dedup_id, creator_id)
                        return
                    else:
                        logger.info("Skipping duplicate send (dedup_id=%s creator=%s)", dedup_id, creator_id)
                        try:
                            from commerce.production_control import record_metric as _rc_dup
                            _rc_dup(name="duplicate_send_suppressed", creator_id=creator_id, user_id=int(data.get("entity")) if str(data.get("entity")).isdigit() else None, value=1.0)
                        except Exception:
                            pass
                        # F7-L3: an ACK failure here must never fall through
                        # to a send — the dedup decision is final.
                        try:
                            await ack_send(msg_id)
                        except Exception:
                            logger.debug("ack after duplicate-skip failed for %s", msg_id, exc_info=True)
                        return
            except Exception:
                logger.debug("dedup value check failed", exc_info=True)

        entity = data.get("entity")
        content = data["content"]
        if entity and entity.isdigit():
            entity_int = int(entity)
        else:
            entity_int = entity

        peer_key = str(entity_int) if entity_int else "unknown"
        wait_sec = await get_send_rate_limit_wait(peer_key)
        if wait_sec > 0:
            await asyncio.sleep(wait_sec)
        allowed = await check_send_rate_limit(peer_key)
        if not allowed:
            logger.warning("Rate limit exceeded for peer %s, re-queuing", peer_key)
            # H4 Batch 1: re-enqueue BEFORE ACK — the crash window where the
            # original is ACKed but the replacement was never created is a
            # silent loss. If XADD fails, leave the entry pending for reclaim.
            try:
                await enqueue_send(dict(data), dedup_id=dedup_id, generation_id=generation_id, creator_id=creator_id)
            except Exception:
                logger.warning("Rate-limit re-enqueue failed for %s — leaving pending for reclaim", msg_id, exc_info=True)
                return
            await ack_send(msg_id)
            return

        if entity and str(entity).isdigit():
            try:
                from core.entity_blacklist import is_blacklisted

                if await is_blacklisted(entity):
                    logger.warning(
                        "Skipping blacklisted entity %s (known unresolvable), moving to DLQ",
                        entity,
                    )
                    if dedup_id:
                        try:
                            await mark_send_dedup(dedup_id, ttl=86400, creator_id=creator_id)
                        except Exception:
                            logger.debug("Failed to mark dedup on DLQ blacklisted", exc_info=True)
                    await move_send_to_dlq(
                        msg_id,
                        "entity_blacklisted",
                        payload=dict(data),
                        worker_id="bot_main",
                    )
                    return
            except Exception:
                logger.debug("Blacklist check failed for entity=%s", entity, exc_info=True)

            try:
                entity_int_val = int(entity)
                input_entity = await client.get_input_entity(entity_int_val)
            except (ValueError, TypeError):
                logger.warning(
                    "Cannot resolve entity %s (permanent), moving to DLQ",
                    entity,
                )
                try:
                    from core.entity_blacklist import blacklist_entity

                    await blacklist_entity(entity, reason="entity_not_found")
                except Exception:
                    logger.debug("Failed to blacklist entity=%s", entity, exc_info=True)
                if dedup_id:
                    try:
                        await mark_send_dedup(dedup_id, ttl=86400, creator_id=creator_id)
                    except Exception:
                        logger.debug("Failed to mark dedup on DLQ entity_not_found", exc_info=True)
                await move_send_to_dlq(
                    msg_id,
                    "entity_not_found",
                    payload=dict(data),
                    worker_id="bot_main",
                )
                return
            except FloodWaitError as e:
                logger.warning(
                    "Telegram flood wait %ss for entity %s, sleeping then re-queuing",
                    e.seconds,
                    entity,
                )
                # H4 Batch 3: never block the single consumer unboundedly.
                # Bound one cycle's sleep to the reclaim cadence (derived from
                # the existing redis_pending_idle_ms setting, not a new
                # constant); any remainder is deferred through requeue cycles.
                _flood_cap = max(1, int(_settings.redis_pending_idle_ms // 1000))
                _flood_wait = e.seconds or 0
                try:
                    _flood_wait = float(_flood_wait)
                except Exception:
                    _flood_wait = 0.0
                if _flood_wait > _flood_cap:
                    logger.warning(
                        "FloodWait %ss exceeds per-cycle cap %ss for %s — deferring remainder via requeue",
                        e.seconds,
                        _flood_cap,
                        msg_id,
                    )
                    _flood_wait = float(_flood_cap)
                if _flood_wait > 0:
                    await asyncio.sleep(_flood_wait)
                # H4 Batch 1: re-enqueue BEFORE ACK (see rate-limit path).
                try:
                    await enqueue_send(dict(data), dedup_id=dedup_id, generation_id=generation_id, creator_id=creator_id)
                except Exception:
                    logger.warning("FloodWait re-enqueue failed for %s — leaving pending for reclaim", msg_id, exc_info=True)
                    return
                await ack_send(msg_id)
                return
            except (FloodError, PeerFloodError) as e:
                # H4 Batch 3 (D3): transient throttle (slow-mode / premium /
                # peer flood) at resolution — defer via requeue. Never
                # blacklist a valid peer, never mark the identity delivered.
                logger.warning(
                    "Transient Telegram throttle %s for entity %s, deferring via requeue",
                    type(e).__name__,
                    entity,
                )
                _throttle_cap = max(1, int(_settings.redis_pending_idle_ms // 1000))
                _throttle_wait = getattr(e, "seconds", 0) or 0
                try:
                    _throttle_wait = float(_throttle_wait)
                except Exception:
                    _throttle_wait = 0.0
                if _throttle_wait > _throttle_cap:
                    _throttle_wait = float(_throttle_cap)
                if _throttle_wait > 0:
                    await asyncio.sleep(_throttle_wait)
                try:
                    await enqueue_send(dict(data), dedup_id=dedup_id, generation_id=generation_id, creator_id=creator_id)
                except Exception:
                    logger.warning("Throttle re-enqueue failed for %s — leaving pending for reclaim", msg_id, exc_info=True)
                    return
                await ack_send(msg_id)
                return
            except RPCError as e:
                logger.warning(
                    "RPC error resolving entity %s: %s, moving to DLQ",
                    entity,
                    type(e).__name__,
                )
                try:
                    from core.entity_blacklist import blacklist_entity

                    await blacklist_entity(entity, reason="entity_rpc_error")
                except Exception:
                    logger.debug("Failed to blacklist entity=%s", entity, exc_info=True)
                if dedup_id:
                    try:
                        await mark_send_dedup(dedup_id, ttl=86400, creator_id=creator_id)
                    except Exception:
                        logger.debug("Failed to mark dedup on DLQ entity_rpc_error", exc_info=True)
                await move_send_to_dlq(
                    msg_id,
                    "entity_rpc_error",
                    payload=dict(data),
                    worker_id="bot_main",
                )
                return
        else:
            input_entity = entity

        media_type = data.get("media_type", "").strip().lower()
        media_path_raw = data.get("media_path", "").strip()
        is_media_send = media_type in _ALLOWED_MEDIA_TYPES and media_path_raw

        fangate_media_id_check = data.get("fangate_media_id")
        dropfans_vault_item_id_check = data.get("dropfans_vault_item_id")
        delivery_reservation_id = None
        dropfans_pending_info: tuple[int, str] | None = None  # (creator,user) for DropFans finalize
        dropfans_row_id: int | None = None  # H4 Batch 3: shields the pending row from the stale reaper on UNKNOWN
        # DropFans text: dropfans_vault_item_id TEXT pending row already inserted by post_purchase
        # For DropFans media, verify still pending (creator-authorized) and block duplicate sends
        if is_media_send and dropfans_vault_item_id_check and str(dropfans_vault_item_id_check).strip():
            try:
                creator_id_check = data.get("creator_id")
                if creator_id_check and entity and str(entity).isdigit():
                    from db.postgres import get_pool as _pool_drop_main
                    _pool = await _pool_drop_main()
                    async with _pool.acquire() as conn:
                        row = await conn.fetchrow(
                            "SELECT id, status FROM vault_media_deliveries WHERE creator_id=$1 AND user_id=$2 AND dropfans_vault_item_id=$3 LIMIT 1",
                            int(creator_id_check),
                            int(entity),
                            str(dropfans_vault_item_id_check),
                        )
                        if row is None:
                            # No reservation exists — treat as invalid (should have been reserved by post_purchase)
                            logger.warning("DropFans vault media %s has no pending reservation for user %s, DLQing", dropfans_vault_item_id_check, entity)
                            await move_send_to_dlq(msg_id, "dropfans_missing_reservation", payload=dict(data), worker_id="bot_main")
                            return
                        if row["status"] != "pending":
                            logger.info("DropFans vault media %s already %s for user %s, skipping", dropfans_vault_item_id_check, row["status"], entity)
                            await ack_send(msg_id)
                            return
                        dropfans_pending_info = (int(creator_id_check), str(dropfans_vault_item_id_check))
                        try:
                            dropfans_row_id = int(row["id"])
                        except Exception:
                            dropfans_row_id = None
            except Exception:
                logger.warning("DB DropFans reservation check failed for media %s", dropfans_vault_item_id_check, exc_info=True)
                await move_send_to_dlq(msg_id, "delivery_reservation_failed", payload=dict(data), worker_id="bot_main")
                return
        elif fangate_media_id_check and is_media_send:
            try:
                from db.vault import reserve_delivery as _reserve

                creator_id_check = data.get("creator_id")
                if creator_id_check and entity and str(entity).isdigit():
                    delivery_reservation_id = await _reserve(
                        int(creator_id_check),
                        int(entity),
                        int(fangate_media_id_check),
                        product_id=int(data["product_id"])
                        if data.get("product_id")
                        else None,
                    )
                    if delivery_reservation_id is None:
                        logger.info(
                            "Vault media %s already reserved/delivered to user %s, skipping",
                            fangate_media_id_check,
                            entity,
                        )
                        await ack_send(msg_id)
                        return
            except Exception:
                logger.warning(
                    "DB delivery reservation failed for media %s, DLQing to prevent untracked send",
                    fangate_media_id_check,
                    exc_info=True,
                )
                await move_send_to_dlq(
                    msg_id,
                    "delivery_reservation_failed",
                    payload=dict(data),
                    worker_id="bot_main",
                )
                return

        # API-conformance guard: sales_url / buyUrl must never be treated as media (RED-1)
        # DropFans authoritative asset URLs are filePath (image/audio) or downloadUrl (video/audio) per docs.
        # Checkout URLs (https://www.dropfans.io/buy/… or telegram buyTemplate) are not media.
        if is_media_send:
            # Block checkout URLs deterministically before validation
            _buy_url_marker = "/buy/"
            if _buy_url_marker in media_path_raw or media_path_raw == data.get("sales_url", "") or media_path_raw == data.get("buyUrl", ""):
                # Defensive: if media_path equals sales_url/buyUrl, treat as invalid media
                if media_path_raw.count("/buy/") > 0 or "dropfans.io" in media_path_raw.lower():
                    # If it's the checkout URL, do not treat as media — DLQ with dedicated reason and release reservation
                    logger.warning("RED-1 guard: checkout URL passed as media_path for send %s: %s", msg_id, media_path_raw[:120])
                    if delivery_reservation_id is not None:
                        try:
                            from db.vault import release_delivery as _release_invalid
                            await _release_invalid(delivery_reservation_id, creator_id=locals().get("creator_id"))
                        except Exception:
                            pass
                    await move_send_to_dlq(
                        msg_id,
                        "checkout_url_as_media_blocked",
                        payload=dict(data),
                        worker_id="bot_main",
                    )
                    return
            validated_path = _validate_media_path(media_path_raw)
            if validated_path is None:
                logger.warning(
                    "Invalid media path for send %s: %s",
                    msg_id,
                    media_path_raw,
                )
                # YELLOW-1: release vault reservation before DLQ (do not wait for stale reaper)
                if delivery_reservation_id is not None:
                    try:
                        from db.vault import release_delivery as _release_invalid2
                        await _release_invalid2(delivery_reservation_id, creator_id=locals().get("creator_id"))
                    except Exception:
                        logger.warning("Failed to release delivery reservation %s on invalid_media_path", delivery_reservation_id, exc_info=True)
                await move_send_to_dlq(
                    msg_id,
                    "invalid_media_path",
                    payload=dict(data),
                    worker_id="bot_main",
                )
                return
            # P1.8: reserve lease before external send with ownership token
            if dedup_id and not dedup_reserved:
                try:
                    _can_reserve = await try_reserve_send_dedup(dedup_id, creator_id=creator_id)
                    if not _can_reserve:
                        _v2 = await get_send_dedup_value(dedup_id, creator_id=creator_id)
                        if _v2 and (_v2.startswith("reserved:") or _v2 == "reserved"):
                            logger.info("Concurrent dedup reservation race dedup=%s creator=%s deferring", dedup_id, creator_id)
                            return
                        else:
                            logger.info("Skipping duplicate after race dedup=%s creator=%s", dedup_id, creator_id)
                            # F7-L3: same as above — never fall through to a send.
                            try:
                                await ack_send(msg_id)
                            except Exception:
                                logger.debug("ack after race-skip failed for %s", msg_id, exc_info=True)
                            return
                    dedup_reserved = _can_reserve
                except Exception:
                    logger.debug("dedup reserve failed pre-media send", exc_info=True)
            # Attempt external send with lease held (release on proven refusal only)
            # H4 Batch 2: stable transport idempotency per logical send —
            # the same (creator_id, dedup_id) always reuses the same
            # random_id, so a retry never invents a fresh transport identity.
            # H4 Batch 3 (D2): ambiguous outcomes are UNKNOWN, never FAILED.
            # The lease and vault reservation are kept on UNKNOWN (never
            # released, never marked delivered); a retry after UNKNOWN is
            # at-least-once on the wire by Telethon library constraint.
            try:
                _media_random_id = (
                    await get_or_create_send_random_id(dedup_id, creator_id)
                    if dedup_id
                    else None
                )
                result = await send_file(
                    input_entity,
                    validated_path,
                    caption=content,
                    force_document=(media_type == "document"),
                    random_id=_media_random_id,
                )
            except BaseException as _media_exc:
                if _classify_transport_error(_media_exc) == "refused":
                    if dedup_reserved:
                        try:
                            await release_send_dedup(dedup_id, creator_id=creator_id, token=dedup_reserved if isinstance(dedup_reserved, str) else None)
                        except Exception:
                            pass
                    raise
                if not isinstance(_media_exc, Exception):
                    # Cancellation-style: record best-effort, keep the entry
                    # pending, and re-raise (never swallow cancellation).
                    await _handle_unknown_send_result(
                        msg_id, data,
                        dedup_id=dedup_id,
                        generation_id=generation_id,
                        creator_id=creator_id,
                        delivery_reservation_id=delivery_reservation_id,
                        dropfans_row_id=dropfans_row_id,
                        error=_media_exc,
                    )
                    raise
                await _handle_unknown_send_result(
                    msg_id, data,
                    dedup_id=dedup_id,
                    generation_id=generation_id,
                    creator_id=creator_id,
                    delivery_reservation_id=delivery_reservation_id,
                    dropfans_row_id=dropfans_row_id,
                    error=_media_exc,
                )
                return
        else:
            # P1.8: reserve lease before external send (text path) with ownership token
            if dedup_id and not dedup_reserved:
                try:
                    _can_reserve2 = await try_reserve_send_dedup(dedup_id, creator_id=creator_id)
                    if not _can_reserve2:
                        _v2b = await get_send_dedup_value(dedup_id, creator_id=creator_id)
                        if _v2b and (_v2b.startswith("reserved:") or _v2b == "reserved"):
                            logger.info("Concurrent dedup reservation race dedup=%s creator=%s deferring", dedup_id, creator_id)
                            return
                        else:
                            logger.info("Skipping duplicate after race dedup=%s creator=%s", dedup_id, creator_id)
                            # F7-L3: same as above — never fall through to a send.
                            try:
                                await ack_send(msg_id)
                            except Exception:
                                logger.debug("ack after race-skip failed for %s", msg_id, exc_info=True)
                            return
                    dedup_reserved = _can_reserve2
                except Exception:
                    logger.debug("dedup reserve failed pre-text send", exc_info=True)
            # H4 Batch 2: obtain the stable transport value for this logical
            # send (durable, creator-scoped; Batch 3 reconciliation reads the
            # same key). It is deliberately NOT passed to the raw Telethon
            # client here: Telethon 1.44 send_message() has no random_id
            # parameter (TypeError) and send_file() swallows it via **kwargs
            # without effect — see implementation report. The store still
            # guarantees same-dedup → same-value for later phases.
            # H4 Batch 3 (D2): same refused/UNKNOWN partition as the media
            # branch above. Cancellation re-raises after best-effort record.
            try:
                _text_random_id = (
                    await get_or_create_send_random_id(dedup_id, creator_id)
                    if dedup_id
                    else None
                )
                logger.debug(
                    "stable send random_id %s dedup=%s creator=%s",
                    "ready" if _text_random_id is not None else "absent",
                    dedup_id,
                    creator_id,
                )
                result = await client.send_message(input_entity, content)
            except BaseException as _text_exc:
                if _classify_transport_error(_text_exc) == "refused":
                    if dedup_reserved:
                        try:
                            await release_send_dedup(dedup_id, creator_id=creator_id, token=dedup_reserved if isinstance(dedup_reserved, str) else None)
                        except Exception:
                            pass
                    raise
                if not isinstance(_text_exc, Exception):
                    await _handle_unknown_send_result(
                        msg_id, data,
                        dedup_id=dedup_id,
                        generation_id=generation_id,
                        creator_id=creator_id,
                        delivery_reservation_id=delivery_reservation_id,
                        dropfans_row_id=dropfans_row_id,
                        error=_text_exc,
                    )
                    raise
                await _handle_unknown_send_result(
                    msg_id, data,
                    dedup_id=dedup_id,
                    generation_id=generation_id,
                    creator_id=creator_id,
                    delivery_reservation_id=delivery_reservation_id,
                    dropfans_row_id=dropfans_row_id,
                    error=_text_exc,
                )
                return

        # ── H4 Batch 1 boundary: PRE_SEND / TELEGRAM_SEND / POST_SEND ──
        # Both transport branches above return a message object on success and
        # raise before any Telegram acceptance otherwise. Reaching here proves
        # Telegram accepted the message. Everything below (confirm, ACK, DB
        # persistence, vault finalization, events) is post-send
        # persistence/observability: failures there route to
        # _handle_post_send_failure via the telegram_accepted guards below and
        # must never emit message.send_failed, release vault reservations as
        # pre-send cleanup, or DLQ as send_error.
        telegram_accepted = True

        if dedup_id:
            try:
                if dedup_reserved:
                    # Owner-safe confirm
                    tok = dedup_reserved if isinstance(dedup_reserved, str) else None
                    await confirm_send_dedup(dedup_id, creator_id=creator_id, token=tok)
                else:
                    await mark_send_dedup(dedup_id, creator_id=creator_id)
            except Exception as _confirm_exc:
                logger.debug("dedup confirm failed for %s", dedup_id, exc_info=True)
                # H4 Batch 3 (D6): acceptance is proven — durably record the
                # repair need (marker/DB/vault reconcile, never a resend).
                # message.sent semantics stay truthful; no DLQ here.
                repair_needed_recorded = await record_send_repair_needed(
                    dedup_id,
                    creator_id=creator_id,
                    generation_id=generation_id,
                    reason="confirm_failed",
                    telegram_message_id=getattr(result, "id", None),
                    detail=f"{type(_confirm_exc).__name__}: {str(_confirm_exc)[:120]}",
                )

        fangate_media_id = data.get("fangate_media_id")

        if (
            data.get("save_to_db", "").lower() in ("true", "1", "yes")
            and entity_int
            and isinstance(entity_int, int)
        ):
            # H4 Batch 1 delivery truth: ACK before persistence. If the ACK
            # itself throws, persistence never runs (ordering) and the outer
            # telegram_accepted guard routes to _handle_post_send_failure.
            await ack_send(msg_id)
            try:
                await save_outbound_after_send(
                    user_id=entity_int,
                    content=content,
                    draft_content=data.get("draft_content") or content,
                    was_edited=data.get("was_edited", "").lower()
                    in ("true", "1", "yes"),
                    was_auto_approved=data.get("was_auto_approved", "").lower()
                    in ("true", "1", "yes"),
                    confidence_score=float(data.get("confidence_score") or 0),
                    operator_id=int(data["operator_id"])
                    if data.get("operator_id", "").strip()
                    else None,
                    telegram_message_id=getattr(result, "id", None),
                    media_type=media_type if is_media_send else None,
                    media_path=media_path_raw if is_media_send else None,
                    fangate_media_id=int(fangate_media_id)
                    if fangate_media_id
                    else None,
                    creator_id=creator_id,
                    generation_id=generation_id,
                    dedup_id=dedup_id,
                )
            except Exception as _save_exc:
                logger.warning(
                    "save_outbound_after_send failed dedup=%s creator=%s — post-send repair",
                    dedup_id,
                    creator_id,
                    exc_info=True,
                )
                # H4 Batch 1 central invariant: acceptance was proven, so a
                # persistence failure is post-send (confirm/finalize/DLQ with
                # the stable reason — never send_failed, never a resend).
                await _handle_post_send_failure(
                    msg_id, data,
                    dedup_id=dedup_id,
                    generation_id=generation_id,
                    creator_id=creator_id,
                    dedup_reserved=dedup_reserved,
                    delivery_reservation_id=delivery_reservation_id,
                    dropfans_pending_info=dropfans_pending_info,
                    telegram_message_id=getattr(result, "id", None),
                    entity=entity,
                )
                return

            if delivery_reservation_id is not None:
                try:
                    from db.vault import finalize_delivery as _finalize

                    await _finalize(
                        delivery_reservation_id,
                        telegram_message_id=getattr(result, "id", None),
                        creator_id=creator_id,
                    )
                except Exception:
                    logger.warning(
                        "Vault delivery finalization failed for reservation %s",
                        delivery_reservation_id,
                        exc_info=True,
                    )
            # DropFans pending row finalized by CUID
            if dropfans_pending_info is not None:
                try:
                    from db.vault import finalize_dropfans_delivery as _finalize_drop
                    _c, _vid = dropfans_pending_info
                    _uid_int = int(entity) if str(entity).isdigit() else None
                    if _uid_int is not None:
                        await _finalize_drop(_c, _uid_int, _vid, telegram_message_id=getattr(result, "id", None))
                except Exception:
                    logger.warning("DropFans delivery finalization failed for %s", dropfans_pending_info, exc_info=True)
        else:
            # No persistence requested: ACK as before (original behavior).
            await ack_send(msg_id)

        event_payload = {
            "content": content,
            "telegram_message_id": getattr(result, "id", None),
            "was_auto_approved": data.get("was_auto_approved", "").lower()
            in ("true", "1", "yes"),
            "confidence_score": float(data.get("confidence_score") or 0),
        }
        if is_media_send:
            event_payload["media_type"] = media_type
        fangate_media_id_for_event = data.get("fangate_media_id")
        if fangate_media_id_for_event:
            event_payload["fangate_media_id"] = fangate_media_id_for_event

        await publish_event(
            "message.sent",
            event_payload,
            user_id=entity_int if isinstance(entity_int, int) else None,
            dialog_id=entity_int if isinstance(entity_int, int) else None,
            generation_id=generation_id,
            creator_id=creator_id,
            scope="user",
        )
        # Relationship V2 send hook (Stage F4): confirmed send -> ResponseSent.
        # Flag-gated, fail-open; never affects sending, persistence, or realtime.
        try:
            if isinstance(entity_int, int):
                from relationship_v2.integration.send_hook import (
                    note_send_confirmed as _note_v2_sent,
                )

                _v2_queue_id = data.get("queue_id")
                try:
                    _v2_queue_id = (
                        int(_v2_queue_id) if _v2_queue_id not in (None, "") else None
                    )
                except (ValueError, TypeError):
                    _v2_queue_id = None
                await _note_v2_sent(
                    creator_id=creator_id,
                    user_id=entity_int,
                    telegram_message_id=getattr(result, "id", None),
                    generation_id=generation_id,
                    was_edited=data.get("was_edited", "").lower() in ("true", "1", "yes"),
                    queue_id=_v2_queue_id,
                    dedup_id=dedup_id,
                )
        except Exception:
            logger.debug("v2 send hook skipped", exc_info=True)
        # M7 (B1/B2/B5): durable success record linking the external Telegram
        # message id. The actor was embedded at the trusted enqueue site
        # (route/worker, server-side); the stream itself is server-written, so
        # this is propagation — not client trust. Business logic above never
        # reads actor keys. Invalid values degrade to explicit unknown.
        try:
            from core.audit import record_audit_event as _main_audit
            from core.audit import validate_actor as _validate_actor

            _claimed = _validate_actor(
                data.get("actor_type"), data.get("actor_id")
            )
            if "actor_type" not in data and "actor_id" not in data:
                _claimed = {"actor_type": "unknown", "actor_id": "no-actor-on-stream"}
            await _main_audit(
                event_type="success",
                actor=_claimed,
                creator_id=creator_id,
                user_id=entity_int if isinstance(entity_int, int) else None,
                action="send_stream.consumer",
                content=content,
                generation_id=generation_id,
                dedup_id=dedup_id,
                state_before="enqueued",
                state_after="sent",
                result="sent",
                telegram_message_id=getattr(result, "id", None),
            )
        except Exception:
            pass

        if fangate_media_id_for_event and is_media_send:
            await publish_event(
                "vault.media_sent",
                {
                    "fangate_media_id": fangate_media_id_for_event,
                    "product_id": data.get("product_id", ""),
                    "user_id": entity_int if isinstance(entity_int, int) else None,
                },
                user_id=entity_int if isinstance(entity_int, int) else None,
                dialog_id=entity_int if isinstance(entity_int, int) else None,
                generation_id=generation_id,
                creator_id=creator_id,
                scope="user",
            )

        # H4 Batch 3: outcome now proven accepted — clear earlier UNKNOWN
        # evidence for this identity. A repair record written above in this
        # same invocation is preserved (marker still unconfirmed); a stale
        # one is cleared only because bookkeeping just completed.
        if dedup_id:
            await clear_unknown_send_attempt(dedup_id, creator_id=creator_id)
            if not repair_needed_recorded:
                await clear_send_repair_needed(dedup_id, creator_id=creator_id)

    except UserIsBlockedError:
        if telegram_accepted:
            # Defensive (unreachable in practice: no post-send await raises
            # UserIsBlockedError): acceptance already proven, so this is a
            # post-send failure by invariant — never classify as failed send.
            logger.error("Post-send failure misrouted to blocked handler, msg=%s — handling as post-send", msg_id)
            await _handle_post_send_failure(
                msg_id, data,
                dedup_id=dedup_id,
                generation_id=generation_id,
                creator_id=creator_id,
                dedup_reserved=dedup_reserved,
                delivery_reservation_id=delivery_reservation_id,
                dropfans_pending_info=dropfans_pending_info,
                telegram_message_id=getattr(result, "id", None),
                entity=entity,
            )
            return
        logger.warning("User blocked, removing from send stream: %s", msg_id)
        # P1.8: if we held lease, mark delivered owner-safe to prevent retry storm on blocked peer
        try:
            _dr = locals().get("dedup_reserved")
            _didd = locals().get("dedup_id")
            _cidb = locals().get("creator_id")
            if _dr and _didd and _cidb:
                try:
                    _tok = _dr if isinstance(_dr, str) and _dr.startswith("reserved:") else None
                    await confirm_send_dedup(_didd, creator_id=_cidb, token=_tok)
                except Exception:
                    pass
        except Exception:
            pass
        await ack_send(msg_id)
        # Release delivery reservation on failure — capture from outer if available via closure
        # For reclaimed path, delivery_reservation_id may not be in scope; best-effort
        try:
            _rid = locals().get("delivery_reservation_id")
            if _rid is not None:
                from db.vault import release_delivery as _release

                await _release(_rid, creator_id=locals().get("creator_id"))
            # DropFans pending release
            _dinfo = locals().get("dropfans_pending_info")
            if _dinfo is not None:
                try:
                    _c, _vid = _dinfo  # type: ignore
                    _ent = data.get("entity")
                    _uid = int(_ent) if _ent and str(_ent).isdigit() else None
                    if _uid is not None:
                        from db.vault import release_dropfans_delivery as _release_drop
                        await _release_drop(_c, _uid, _vid)
                except Exception:
                    pass
        except Exception:
            pass
        entity_int_val = None
        try:
            _ent = data.get("entity")
            entity_int_val = int(_ent) if _ent and str(_ent).isdigit() else None
        except:
            pass
        _gid = data.get("generation_id")
        _cid_raw = data.get("creator_id")
        _cid = None
        try:
            _cid = int(_cid_raw) if _cid_raw and str(_cid_raw).isdigit() else None
        except:
            pass
        await publish_event(
            "message.send_failed",
            {"error": "UserIsBlockedError"},
            user_id=entity_int_val,
            dialog_id=entity_int_val,
            generation_id=_gid,
            creator_id=_cid,
            scope="user",
        )
    except Exception:
        if telegram_accepted:
            # H4 Batch 1 central invariant: Telegram acceptance was proven
            # above (confirm/ACK/DB-save/finalize/event failure). Post-send
            # persistence/observability failure — never message.send_failed,
            # never pre-send vault release, never send_error classification.
            logger.exception("Post-send persistence/observability failed after Telegram acceptance, msg=%s", msg_id)
            await _handle_post_send_failure(
                msg_id, data,
                dedup_id=dedup_id,
                generation_id=generation_id,
                creator_id=creator_id,
                dedup_reserved=dedup_reserved,
                delivery_reservation_id=delivery_reservation_id,
                dropfans_pending_info=dropfans_pending_info,
                telegram_message_id=getattr(result, "id", None),
                entity=entity,
            )
            return
        logger.exception("Failed to send message from stream: %s", msg_id)
        # P1.8: release lease owner-safe on transient send failure to allow DLQ replay retry
        try:
            _dr2 = locals().get("dedup_reserved")
            _didd2 = locals().get("dedup_id")
            _cid2 = locals().get("creator_id")
            if _dr2 and _didd2 and _cid2:
                try:
                    _tok2 = _dr2 if isinstance(_dr2, str) and _dr2.startswith("reserved:") else None
                    await release_send_dedup(_didd2, creator_id=_cid2, token=_tok2)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            _rid = locals().get("delivery_reservation_id")
            if _rid is not None:
                from db.vault import release_delivery as _release

                await _release(_rid, creator_id=locals().get("creator_id"))
            _dinfo = locals().get("dropfans_pending_info")
            if _dinfo is not None:
                try:
                    _c, _vid = _dinfo  # type: ignore
                    _ent = data.get("entity")
                    _uid = int(_ent) if _ent and str(_ent).isdigit() else None
                    if _uid is not None:
                        from db.vault import release_dropfans_delivery as _release_drop2
                        await _release_drop2(_c, _uid, _vid)
                except Exception:
                    pass
        except Exception:
            pass
        # H4 Batch 3 (D4): move_send_to_dlq reports whether record+ACK both
        # succeeded. On failure the entry stays pending for reclaim — do not
        # certify the failure yet; the reclaim attempt will DLQ and emit.
        try:
            _dlq_ok = await move_send_to_dlq(
                msg_id,
                SEND_DLQ_REASON_SEND_ERROR,
                payload=dict(data) if isinstance(data, dict) else None,
                worker_id="bot_main",
            )
        except Exception:
            logger.warning("send_error DLQ move failed for %s", msg_id, exc_info=True)
            _dlq_ok = False
        entity_int_val = None
        try:
            _ent = data.get("entity")
            entity_int_val = int(_ent) if _ent and str(_ent).isdigit() else None
        except:
            pass
        _gid = data.get("generation_id")
        _cid_raw = data.get("creator_id")
        _cid = None
        try:
            _cid = int(_cid_raw) if _cid_raw and str(_cid_raw).isdigit() else None
        except:
            pass
        if _dlq_ok:
            await publish_event(
                "message.send_failed",
                {"error": "SendError"},
                user_id=entity_int_val,
                dialog_id=entity_int_val,
                generation_id=_gid,
                creator_id=_cid,
                scope="user",
            )
            # M7 (B1/B2): durable failure record (hashes only, never raw content).
            try:
                from core.audit import record_audit_event as _fail_audit
                from core.audit import validate_actor as _fail_validate

                await _fail_audit(
                    event_type="failure",
                    actor=_fail_validate(data.get("actor_type"), data.get("actor_id"))
                    if ("actor_type" in data or "actor_id" in data)
                    else {"actor_type": "unknown", "actor_id": "no-actor-on-stream"},
                    creator_id=_cid if _cid is not None else 0,
                    user_id=entity_int_val,
                    action="send_stream.consumer",
                    generation_id=_gid,
                    dedup_id=data.get("dedup_id"),
                    state_before="enqueued",
                    state_after="failed",
                    result="failure",
                    error="SendError",
                )
            except Exception:
                pass
        else:
            logger.warning(
                "send_error DLQ unavailable for %s — entry remains pending for reclaim; send_failed deferred",
                msg_id,
            )


async def _process_send_stream(client) -> None:
    # P1.6 periodic debounce recovery throttle
    _last_debounce_recover = 0.0
    while True:
        if is_shutting_down():
            break
        try:
            # P1.6 R-02: periodic orphaned debounce recovery (bounded, fail-open, throttle 30s, timeout 2s)
            try:
                import time as _t
                _now = _t.monotonic()
                if _now - _last_debounce_recover > 30:
                    from db.redis import requeue_stalled_debounce
                    try:
                        _deb = await asyncio.wait_for(requeue_stalled_debounce(window_seconds=_settings.debounce_window_seconds, max_keys=50), timeout=2.0)
                        if _deb:
                            logger.info("P1.6 periodic requeue_stalled_debounce recovered %d entries", _deb)
                    except asyncio.TimeoutError:
                        logger.debug("periodic debounce recovery timed out")
                    _last_debounce_recover = _now
            except Exception:
                logger.debug("periodic debounce recovery failed", exc_info=True)
            stale_count, claimed = await requeue_stalled_send_messages(
                "bot_main", idle_ms=_settings.redis_pending_idle_ms
            )
            if stale_count > 0:
                stale_ids = [mid for mid, _ in claimed]
                logger.info("Reclaimed %d stalled send messages: %s", stale_count, stale_ids)
                for _cid, _fields in claimed:
                    await _handle_send_entry(client, _cid, _fields)

            released = await release_stale_reservations(
                max_age_minutes=_settings.vault_stale_reservation_minutes,
                # H4 Batch 3: reservations tied to a recorded UNKNOWN attempt
                # are evidence, not orphans — never reap them here.
                skip_ids=await list_unknown_vault_reservation_ids(limit=200),
            )
            if released:
                logger.info("Recovered %d stale vault reservations", len(released))

            messages = await read_send_messages("bot_main", count=10, block_ms=2000)
            for stream, msgs in messages:
                for msg_id, data in msgs:
                    await _handle_send_entry(client, msg_id, data)
                    continue
                    try:
                        dedup_id = data.get("dedup_id")
                        generation_id = data.get("generation_id") or None
                        # Creator-scoped for isolation
                        creator_id_raw = data.get("creator_id")
                        creator_id = None
                        if creator_id_raw and str(creator_id_raw).isdigit():
                            try:
                                creator_id = int(creator_id_raw)
                            except:
                                creator_id = None
                        if dedup_id and await is_send_duplicate(dedup_id, creator_id=creator_id):
                            logger.info("Skipping duplicate send (dedup_id=%s creator=%s)", dedup_id, creator_id)
                            await ack_send(msg_id)
                            continue

                        entity = data.get("entity")
                        content = data["content"]
                        if entity and entity.isdigit():
                            entity_int = int(entity)
                        else:
                            entity_int = entity

                        peer_key = str(entity_int) if entity_int else "unknown"
                        wait_sec = await get_send_rate_limit_wait(peer_key)
                        if wait_sec > 0:
                            await asyncio.sleep(wait_sec)
                        allowed = await check_send_rate_limit(peer_key)
                        if not allowed:
                            logger.warning("Rate limit exceeded for peer %s, re-queuing", peer_key)
                            # H4 Batch 1 (dead-code parity): re-enqueue BEFORE ACK.
                            # If XADD fails, leave pending for reclaim; only ACK after success.
                            try:
                                await enqueue_send(dict(data), dedup_id=dedup_id, generation_id=generation_id, creator_id=creator_id)
                            except Exception:
                                logger.warning("Rate-limit re-enqueue failed for %s — leaving pending for reclaim", msg_id, exc_info=True)
                                continue
                            await ack_send(msg_id)
                            continue

                        if entity and str(entity).isdigit():
                            # Pre-check: skip known-blacklisted entities entirely
                            try:
                                from core.entity_blacklist import is_blacklisted

                                if await is_blacklisted(entity):
                                    logger.warning(
                                        "Skipping blacklisted entity %s (known unresolvable), moving to DLQ",
                                        entity,
                                    )
                                    if dedup_id:
                                        try:
                                            await mark_send_dedup(dedup_id, ttl=86400, creator_id=creator_id)
                                        except Exception:
                                            logger.debug("Failed to mark dedup on DLQ blacklisted", exc_info=True)
                                    await move_send_to_dlq(
                                        msg_id,
                                        "entity_blacklisted",
                                        payload=dict(data),
                                        worker_id="bot_main",
                                    )
                                    continue
                            except Exception:
                                logger.debug("Blacklist check failed for entity=%s", entity, exc_info=True)

                            try:
                                entity_int_val = int(entity)
                                input_entity = await client.get_input_entity(entity_int_val)
                            except (ValueError, TypeError):
                                logger.warning(
                                    "Cannot resolve entity %s (permanent), moving to DLQ",
                                    entity,
                                )
                                # Blacklist so send_worker stops re-enqueueing this entity
                                try:
                                    from core.entity_blacklist import blacklist_entity

                                    await blacklist_entity(entity, reason="entity_not_found")
                                except Exception:
                                    logger.debug("Failed to blacklist entity=%s", entity, exc_info=True)
                                # Mark dedup even on DLQ: prevents enqueue_purchase_confirmation
                                # from re-enqueueing the same post_purchase message on every reconciliation/webhook cycle
                                if dedup_id:
                                    try:
                                        await mark_send_dedup(dedup_id, ttl=86400, creator_id=creator_id)
                                    except Exception:
                                        logger.debug("Failed to mark dedup on DLQ entity_not_found", exc_info=True)
                                await move_send_to_dlq(
                                    msg_id,
                                    "entity_not_found",
                                    payload=dict(data),
                                    worker_id="bot_main",
                                )
                                continue
                            except FloodWaitError as e:
                                logger.warning(
                                    "Telegram flood wait %ss for entity %s, sleeping then re-queuing",
                                    e.seconds,
                                    entity,
                                )
                                await asyncio.sleep(e.seconds)
                                # H4 Batch 1 (dead-code parity): re-enqueue BEFORE ACK.
                                try:
                                    await enqueue_send(dict(data), dedup_id=dedup_id, generation_id=generation_id, creator_id=creator_id)
                                except Exception:
                                    logger.warning("FloodWait re-enqueue failed for %s — leaving pending for reclaim", msg_id, exc_info=True)
                                    continue
                                await ack_send(msg_id)
                                continue
                            except RPCError as e:
                                logger.warning(
                                    "RPC error resolving entity %s: %s, moving to DLQ",
                                    entity,
                                    type(e).__name__,
                                )
                                # RPC errors on entity resolution are also permanent (PeerIdInvalidError etc.)
                                try:
                                    from core.entity_blacklist import blacklist_entity

                                    await blacklist_entity(entity, reason="entity_rpc_error")
                                except Exception:
                                    logger.debug("Failed to blacklist entity=%s", entity, exc_info=True)
                                if dedup_id:
                                    try:
                                        await mark_send_dedup(dedup_id, ttl=86400, creator_id=creator_id)
                                    except Exception:
                                        logger.debug("Failed to mark dedup on DLQ entity_rpc_error", exc_info=True)
                                await move_send_to_dlq(
                                    msg_id,
                                    "entity_rpc_error",
                                    payload=dict(data),
                                    worker_id="bot_main",
                                )
                                continue
                        else:
                            input_entity = entity

                        # Determine send mode: media or text.
                        media_type = data.get("media_type", "").strip().lower()
                        media_path_raw = data.get("media_path", "").strip()
                        is_media_send = media_type in _ALLOWED_MEDIA_TYPES and media_path_raw

                        # Atomic delivery reservation: reserve BEFORE Telegram send.
                        # This eliminates the TOCTOU race between check and record.
                        fangate_media_id_check = data.get("fangate_media_id")
                        delivery_reservation_id = None
                        if fangate_media_id_check and is_media_send:
                            try:
                                from db.vault import reserve_delivery as _reserve

                                creator_id_check = data.get("creator_id")
                                if creator_id_check and entity and str(entity).isdigit():
                                    delivery_reservation_id = await _reserve(
                                        int(creator_id_check),
                                        int(entity),
                                        int(fangate_media_id_check),
                                        product_id=int(data["product_id"])
                                        if data.get("product_id")
                                        else None,
                                    )
                                    if delivery_reservation_id is None:
                                        logger.info(
                                            "Vault media %s already reserved/delivered to user %s, skipping",
                                            fangate_media_id_check,
                                            entity,
                                        )
                                        await ack_send(msg_id)
                                        continue
                            except Exception:
                                logger.warning(
                                    "DB delivery reservation failed for media %s, DLQing to prevent untracked send",
                                    fangate_media_id_check,
                                    exc_info=True,
                                )
                                await move_send_to_dlq(
                                    msg_id,
                                    "delivery_reservation_failed",
                                    payload=dict(data),
                                    worker_id="bot_main",
                                )
                                continue

                        if is_media_send:
                            validated_path = _validate_media_path(media_path_raw)
                            if validated_path is None:
                                logger.warning(
                                    "Invalid media path for send %s: %s",
                                    msg_id,
                                    media_path_raw,
                                )
                                await move_send_to_dlq(
                                    msg_id,
                                    "invalid_media_path",
                                    payload=dict(data),
                                    worker_id="bot_main",
                                )
                                continue
                            # H4 Batch 2 (dead-code parity): stable random_id per
                            # logical send; media passes it to the wrapper.
                            _dead_media_random_id = (
                                await get_or_create_send_random_id(dedup_id, creator_id)
                                if dedup_id
                                else None
                            )
                            result = await send_file(
                                input_entity,
                                validated_path,
                                caption=content,
                                force_document=(media_type == "document"),
                                random_id=_dead_media_random_id,
                            )
                        else:
                            # H4 Batch 2 (dead-code parity): value obtained for
                            # durability only; raw Telethon send_message has no
                            # random_id parameter (see live path comment).
                            _dead_text_random_id = (
                                await get_or_create_send_random_id(dedup_id, creator_id)
                                if dedup_id
                                else None
                            )
                            logger.debug(
                                "stable send random_id %s dedup=%s creator=%s (dead path)",
                                "ready" if _dead_text_random_id is not None else "absent",
                                dedup_id,
                                creator_id,
                            )
                            result = await client.send_message(input_entity, content)

                        if dedup_id:
                            await mark_send_dedup(dedup_id, creator_id=creator_id)
                        await ack_send(msg_id)

                        fangate_media_id = data.get("fangate_media_id")

                        if (
                            data.get("save_to_db", "").lower() in ("true", "1", "yes")
                            and entity_int
                            and isinstance(entity_int, int)
                        ):
                            await save_outbound_after_send(
                                user_id=entity_int,
                                content=content,
                                draft_content=data.get("draft_content") or content,
                                was_edited=data.get("was_edited", "").lower()
                                in ("true", "1", "yes"),
                                was_auto_approved=data.get("was_auto_approved", "").lower()
                                in ("true", "1", "yes"),
                                confidence_score=float(data.get("confidence_score") or 0),
                                operator_id=int(data["operator_id"])
                                if data.get("operator_id", "").strip()
                                else None,
                                telegram_message_id=getattr(result, "id", None),
                                media_type=media_type if is_media_send else None,
                                media_path=media_path_raw if is_media_send else None,
                                fangate_media_id=int(fangate_media_id)
                                if fangate_media_id
                                else None,
                                creator_id=creator_id,
                                generation_id=data.get("generation_id") or None,
                                dedup_id=dedup_id,
                            )

                            # Finalize vault delivery reservation if one was made.
                            if delivery_reservation_id is not None:
                                try:
                                    from db.vault import finalize_delivery as _finalize

                                    await _finalize(
                                        delivery_reservation_id,
                                        telegram_message_id=getattr(result, "id", None),
                                        creator_id=locals().get("creator_id"),
                                    )
                                except Exception:
                                    logger.warning(
                                        "Vault delivery finalization failed for reservation %s",
                                        delivery_reservation_id,
                                        exc_info=True,
                                    )

                        event_payload = {
                            "content": content,
                            "telegram_message_id": getattr(result, "id", None),
                            "was_auto_approved": data.get("was_auto_approved", "").lower()
                            in ("true", "1", "yes"),
                            "confidence_score": float(data.get("confidence_score") or 0),
                        }
                        if is_media_send:
                            event_payload["media_type"] = media_type
                        fangate_media_id_for_event = data.get("fangate_media_id")
                        if fangate_media_id_for_event:
                            event_payload["fangate_media_id"] = fangate_media_id_for_event

                        await publish_event(
                            "message.sent",
                            event_payload,
                            user_id=entity_int if isinstance(entity_int, int) else None,
                            dialog_id=entity_int if isinstance(entity_int, int) else None,
                            generation_id=generation_id,
                            creator_id=creator_id,
                            scope="user",
                        )
                        # Relationship V2 send hook (Stage F4): confirmed send
                        # -> ResponseSent. Flag-gated, fail-open; no behavior change.
                        try:
                            if isinstance(entity_int, int):
                                from relationship_v2.integration.send_hook import (
                                    note_send_confirmed as _note_v2_sent,
                                )

                                _v2_queue_id = data.get("queue_id")
                                try:
                                    _v2_queue_id = (
                                        int(_v2_queue_id)
                                        if _v2_queue_id not in (None, "")
                                        else None
                                    )
                                except (ValueError, TypeError):
                                    _v2_queue_id = None
                                await _note_v2_sent(
                                    creator_id=creator_id,
                                    user_id=entity_int,
                                    telegram_message_id=getattr(result, "id", None),
                                    generation_id=generation_id,
                                    was_edited=data.get("was_edited", "").lower()
                                    in ("true", "1", "yes"),
                                    queue_id=_v2_queue_id,
                                    dedup_id=dedup_id,
                                )
                        except Exception:
                            logger.debug("v2 send hook skipped", exc_info=True)

                        if fangate_media_id_for_event and is_media_send:
                            await publish_event(
                                "vault.media_sent",
                                {
                                    "fangate_media_id": fangate_media_id_for_event,
                                    "product_id": data.get("product_id", ""),
                                    "user_id": entity_int if isinstance(entity_int, int) else None,
                                },
                                user_id=entity_int if isinstance(entity_int, int) else None,
                                dialog_id=entity_int if isinstance(entity_int, int) else None,
                                generation_id=generation_id,
                                creator_id=creator_id,
                                scope="user",
                            )

                    except UserIsBlockedError:
                        logger.warning("User blocked, removing from send stream: %s", msg_id)
                        await ack_send(msg_id)

                        # Release delivery reservation on failure.
                        if delivery_reservation_id is not None:
                            try:
                                from db.vault import release_delivery as _release

                                await _release(delivery_reservation_id, creator_id=locals().get("creator_id"))
                            except Exception:
                                logger.warning(
                                    "Failed to release delivery reservation %s",
                                    delivery_reservation_id,
                                    exc_info=True,
                                )

                        entity_int_val = int(entity) if entity and str(entity).isdigit() else None
                        await publish_event(
                            "message.send_failed",
                            {"error": "UserIsBlockedError"},
                            user_id=entity_int_val,
                            dialog_id=entity_int_val,
                            generation_id=generation_id,
                            creator_id=creator_id,
                            scope="user",
                        )
                    except Exception:
                        logger.exception("Failed to send message from stream: %s", msg_id)

                        # Release delivery reservation on failure.
                        if delivery_reservation_id is not None:
                            try:
                                from db.vault import release_delivery as _release

                                await _release(delivery_reservation_id, creator_id=locals().get("creator_id"))
                            except Exception:
                                logger.warning(
                                    "Failed to release delivery reservation %s",
                                    delivery_reservation_id,
                                    exc_info=True,
                                )

                        await move_send_to_dlq(
                            msg_id,
                            "send_error",
                            payload=dict(data),
                            worker_id="bot_main",
                        )

                        entity_int_val = int(entity) if entity and str(entity).isdigit() else None
                        await publish_event(
                            "message.send_failed",
                            {"error": "SendError"},
                            user_id=entity_int_val,
                            dialog_id=entity_int_val,
                            generation_id=generation_id,
                            creator_id=creator_id,
                            scope="user",
                        )
        except Exception:
            logger.exception("Send stream loop error")
        await asyncio.sleep(0.5)


async def _do_cleanup(client=None) -> None:
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    from chatbotv2.client import close_client

    await close_client()
    await close_pool()
    await close_redis()


async def run() -> None:
    setup_logging(structured=_settings.structured_logging)

    await init_pool()
    await verify_schema()
    await ensure_consumer_group()
    # P1.6 R-01/R-02: startup reconciliation for crash gaps (bounded, fail-open, timeout 2s)
    try:
        from db.redis import reconcile_inbound_gaps, requeue_stalled_debounce
        try:
            _rec = await asyncio.wait_for(reconcile_inbound_gaps(lookback_seconds=300, limit=100), timeout=2.0)
            if _rec:
                logger.info("P1.6 startup reconcile_inbound_gaps recovered %d entries", _rec)
            _deb = await asyncio.wait_for(requeue_stalled_debounce(window_seconds=_settings.debounce_window_seconds, max_keys=100), timeout=2.0)
            if _deb:
                logger.info("P1.6 startup requeue_stalled_debounce recovered %d entries", _deb)
        except asyncio.TimeoutError:
            logger.debug("P1.6 startup reconcile timed out")
    except Exception:
        logger.debug("P1.6 startup reconciliation failed", exc_info=True)

    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        write_heartbeat(
            "bot_main",
            worker_type="bot_main",
            interval_seconds=_settings.worker_heartbeat_interval,
            ttl_seconds=_settings.worker_heartbeat_ttl,
            stop_event=heartbeat_stop,
        )
    )

    for attempt in range(3):
        try:
            client = await get_client()
            setup_handlers(client)
            logger.info("MTProto chatbotv2 started")

            send_task = asyncio.create_task(_process_send_stream(client))

            try:
                await client.run_until_disconnected()
            finally:
                set_shutting_down(True)
                heartbeat_stop.set()
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass
                await _do_cleanup()
                logger.info("MTProto chatbotv2 stopped")
            return
        except Exception as e:
            logger.warning(f"Bot startup attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(3)
            else:
                raise


if __name__ == "__main__":
    asyncio.run(run())
