import argparse
import asyncio
import logging

from core.config import get_settings
from core.logging_config import setup_logging
from core.shutdown import is_shutting_down, setup_signal_handlers
from core.worker_heartbeat import write_heartbeat
from db.postgres import (
    get_pending_queue_items,
    init_pool,
    resolve_queue_item,
)
from db.redis import close_redis, enqueue_send, ensure_consumer_group

logger = logging.getLogger("send_worker")
_settings = get_settings()


def is_queue_item_human_approved(item) -> bool:
    """True when a pending queue row carries human-approval evidence.

    Flush delivery requires a human touch: operator-edited content
    (``edited``) or a recorded resolver (``resolved_by``). Untouched
    pending rows (fresh drafts awaiting review) return False and wait
    for dashboard review instead of being auto-approved by flush.

    Pure over plain data, never raises (unusable input -> False).
    """
    try:
        if not isinstance(item, dict):
            return False
        if bool(item.get("edited")):
            return True
        resolver = item.get("resolved_by")
        if isinstance(resolver, bool):
            return resolver
        return resolver is not None and bool(str(resolver).strip())
    except Exception:
        return False


async def process_approved_message(
    user_id: int,
    content: str,
    confidence_score: float | None = None,
    operator_id: int | None = None,
    queue_id: int | None = None,
    creator_id: int | None = None,
    generation_id: str | None = None,
    was_edited: bool = False,
    actor_type: str | None = None,
    actor_id: str | None = None,
) -> dict | None:
    # P1.4: creator_id is REQUIRED for send stream
    if creator_id is None:
        logger.warning("process_approved_message missing creator_id for user=%s – failing closed", user_id)
        return None
    # Phases 1-4: preserve the queue row's generation correlation verbatim;
    # never regenerate it from content. queue_item:{id} stays the dedup key only.
    # Historical rows without generation_id keep generation_id absent (NULL valid).
    # M6: edited bytes must never inherit the evaluated draft's auto-approval.
    # was_edited forces was_auto_approved=False even when the row's stored
    # confidence (evaluated against the original draft) exceeds the threshold.
    # M7 (B3): edited content uses the content-bound dedup suffix
    # queue_item:{id}:{sha16} consistent with the dashboard send path, so a
    # stale unedited snapshot and a fresh edit never share one identity.
    # Unedited sends keep the legacy queue_item:{id} identity (identical
    # retries still suppress).
    from core.generation import is_valid_generation_id

    _send_gid: str | None = str(generation_id) if is_valid_generation_id(generation_id) else None
    try:
        import hashlib as _hashlib
        # P1.8: stable digest (not Python's randomized hash) for manual dedup across restarts
        _content_hash = _hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        if queue_id and was_edited:
            dedup_id = f"queue_item:{queue_id}:{_content_hash}"
        else:
            dedup_id = f"queue_item:{queue_id}" if queue_id else f"manual:{user_id}:{_content_hash}"
        _payload: dict = {
            "entity": str(user_id),
            "content": content,
            "draft_content": content,
            "was_edited": bool(was_edited),
            "was_auto_approved": (not was_edited)
            and confidence_score is not None
            and confidence_score >= _settings.auto_approve_threshold,
            "confidence_score": confidence_score or 0,
            "operator_id": operator_id,
            "save_to_db": True,
            "creator_id": str(creator_id),
        }
        if _send_gid:
            _payload["generation_id"] = _send_gid
        # M7 (B5): server-derived actor only. Callers pass the trusted actor;
        # anything arriving here from a stream payload must already have been
        # stripped via core.audit.strip_spoofed_actor by the consumer.
        if actor_type is not None:
            _payload["actor_type"] = str(actor_type)
        if actor_id is not None:
            _payload["actor_id"] = str(actor_id)
        await enqueue_send(
            _payload,
            dedup_id=dedup_id,
            generation_id=_send_gid,
            creator_id=creator_id,
        )
        return {"ok": True, "dedup_id": dedup_id}
    except Exception:
        logger.exception("Failed to enqueue message to user %s", user_id)
        return None


async def flush_queue(max_items: int = 50) -> int:
    # Sunny V1 cutover (Phase 3): operator-queue flush is a V1 conversational
    # path and is DISABLED. Dashboard review UI stays readable; no rows are
    # auto-flushed to the send stream from this worker. Commerce delivery
    # (post-purchase confirmations, vault media, scheduled commerce messages)
    # enters via enqueue_send directly and is delivered by chatbotv2/main.py,
    # which is PRESERVED and not gated here.
    try:
        from core.architecture_router import is_v1_conversational_enabled, log_v1_suppressed

        if not is_v1_conversational_enabled():
            log_v1_suppressed("send_worker.flush_queue")
            return 0
    except Exception:
        logger.warning("V1 gate unreadable in flush_queue — failing closed")
        return 0
    # P1.4: strict isolation – enumerate active creators, no single-creator LIMIT 1
    active_ids: list[int] = []
    try:
        from db.postgres import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT creator_id FROM creator_integrations WHERE status = 'active' ORDER BY creator_id")
            active_ids = [int(r["creator_id"]) for r in rows if r["creator_id"] is not None]
    except Exception as e:
        logger.warning("flush_queue: failed to enumerate active creators: %s", e, exc_info=True)
        return 0
    if not active_ids:
        logger.warning("flush_queue: no active creators – skipping (strict isolation)")
        return 0
    sent = 0
    for _cid in active_ids:
        try:
            items = await get_pending_queue_items(limit=max_items, creator_id=_cid)
        except Exception as e:
            logger.warning("flush_queue: get_pending_queue_items failed for creator %s: %s", _cid, e, exc_info=True)
            continue

        for item in items:
            if item.get("status") != "pending":
                continue

            user_id = item["user_id"]

            # --- Entity blacklist check: skip re-enqueuing permanently unresolvable peers ---
            try:
                from core.entity_blacklist import is_blacklisted

                if await is_blacklisted(user_id):
                    # Entity is known-unresolvable. Mark queue item as failed so it
                    # doesn't keep cycling through flush_queue every 5 seconds.
                    logger.warning(
                        "flush_queue: skipping blacklisted entity user=%s queue_id=%s",
                        user_id,
                        item["id"],
                    )
                    try:
                        await resolve_queue_item(item["id"], "failed", creator_id=_cid)
                    except Exception:
                        logger.warning("Failed to mark blacklisted queue item %s as failed", item["id"])
                    try:
                        from core.audit import record_audit_event as _bl_audit

                        await _bl_audit(
                            event_type="reject",
                            actor_type="worker",
                            actor_id="send_worker",
                            creator_id=_cid,
                            user_id=user_id,
                            action="send_worker.flush_queue",
                            queue_id=item["id"],
                            state_before="pending",
                            state_after="failed",
                            result="rejected",
                            error="blacklisted entity",
                        )
                    except Exception:
                        pass
                    continue
            except Exception:
                # Blacklist check must never break flush_queue
                logger.debug("flush_queue: blacklist check failed for user=%s", user_id, exc_info=True)

            content = item["draft_content"]
            if not content:
                await resolve_queue_item(item["id"], "rejected", creator_id=_cid)
                continue

            # Creator attribution: the row was fetched scoped to _cid, but use
            # the row's own creator_id when present so attribution never drifts
            # to the loop variable. Fail closed on mismatch.
            _item_cid = item.get("creator_id")
            try:
                _item_cid_int = int(_item_cid) if _item_cid is not None and str(_item_cid).strip().isdigit() else _cid
            except (TypeError, ValueError):
                _item_cid_int = _cid
            if _item_cid_int != _cid:
                logger.warning(
                    "flush_queue: queue item %s creator mismatch (row=%s loop=%s) – skipping",
                    item["id"], _item_cid, _cid,
                )
                continue
            from core.generation import is_valid_generation_id as _is_valid_gid

            _item_gid = item.get("generation_id")
            # M7 (B3/B5): authoritative re-read immediately before enqueue.
            # The pending-list snapshot above may predate a dashboard edit;
            # the re-read row (still pending under this creator) is the only
            # content the worker is allowed to send. Any divergence in bytes
            # or edited flag means the snapshot is stale: drop it, audit
            # stale_suppressed, and never enqueue the stale bytes. The actor
            # is the row's resolving operator when known, else explicit
            # unknown (never fabricated).
            # NOTE: a dashboard edit landing after this re-read but before the
            # XADD below still races (separate PG/Redis systems); the window
            # is narrowed from the full poll cycle to the re-read→enqueue RTT
            # and divergent bytes now carry divergent dedup identities so the
            # collision is observable instead of silent. At-least-once is
            # preserved: suppression only skips known-stale snapshots.
            from core.audit import record_audit_event as _record_audit

            _resolved_by = item.get("resolved_by")
            if _resolved_by:
                _flush_actor = {"actor_type": "human", "actor_id": str(_resolved_by)}
            else:
                _flush_actor = {"actor_type": "unknown", "actor_id": "flush-unattributed"}
            try:
                from db.postgres import get_queue_item_for_send

                _fresh = await get_queue_item_for_send(item["id"], _item_cid_int)
            except Exception:
                logger.warning(
                    "flush_queue: authoritative re-read failed for item %s – skipping",
                    item["id"],
                    exc_info=True,
                )
                continue
            if _fresh is None:
                # Already resolved/approved/rejected elsewhere after the
                # snapshot was taken: nothing to send.
                logger.info(
                    "flush_queue: queue item %s no longer pending – skipping stale snapshot",
                    item["id"],
                )
                await _record_audit(
                    event_type="stale_suppressed",
                    actor=_flush_actor,
                    creator_id=_item_cid_int,
                    user_id=user_id,
                    action="send_worker.flush_queue",
                    content=item.get("draft_content"),
                    generation_id=_item_gid if _is_valid_gid(_item_gid) else None,
                    queue_id=item["id"],
                    state_before="pending?",
                    state_after="resolved-elsewhere",
                    result="suppressed",
                )
                continue
            _fresh_content = _fresh.get("draft_content") or ""
            _fresh_edited = bool(_fresh.get("edited"))
            if _fresh_content != (item.get("draft_content") or "") or _fresh_edited != bool(item.get("edited")):
                logger.warning(
                    "flush_queue: queue item %s changed after snapshot – suppressing stale bytes",
                    item["id"],
                )
                await _record_audit(
                    event_type="stale_suppressed",
                    actor=_flush_actor,
                    creator_id=_item_cid_int,
                    user_id=user_id,
                    action="send_worker.flush_queue",
                    content=item.get("draft_content"),
                    content_final=_fresh_content,
                    generation_id=_item_gid if _is_valid_gid(_item_gid) else None,
                    queue_id=item["id"],
                    state_before="pending",
                    state_after="pending",
                    result="suppressed",
                )
                continue
            # Human-touch gate: flush delivers only rows a human has touched
            # (edited content or recorded resolver). Untouched pending rows
            # are fresh drafts awaiting dashboard review — previously flush
            # auto-approved everything it polled. They stay pending here.
            if not is_queue_item_human_approved(_fresh):
                logger.info(
                    "flush_queue: queue item %s awaiting human review – skipping",
                    item["id"],
                )
                await _record_audit(
                    event_type="awaiting_review",
                    actor=_flush_actor,
                    creator_id=_item_cid_int,
                    user_id=user_id,
                    action="send_worker.flush_queue",
                    generation_id=_item_gid if _is_valid_gid(_item_gid) else None,
                    queue_id=item["id"],
                    state_before="pending",
                    state_after="pending",
                    result="skipped",
                )
                continue
            # Authoritative bytes from here on: the re-read row.
            content = _fresh_content
            if not content:
                await resolve_queue_item(item["id"], "rejected", creator_id=_cid)
                await _record_audit(
                    event_type="reject",
                    actor=_flush_actor,
                    creator_id=_item_cid_int,
                    user_id=user_id,
                    action="send_worker.flush_queue",
                    generation_id=_item_gid if _is_valid_gid(_item_gid) else None,
                    queue_id=item["id"],
                    state_before="pending",
                    state_after="rejected",
                    result="rejected",
                    error="empty content on re-read",
                )
                continue
            _fresh_resolved_by = _fresh.get("resolved_by") or _resolved_by
            if _fresh_resolved_by:
                _flush_actor = {"actor_type": "human", "actor_id": str(_fresh_resolved_by)}
            result = await process_approved_message(
                user_id=user_id,
                content=content,
                confidence_score=_fresh.get("confidence_score"),
                operator_id=_fresh.get("assigned_to"),
                queue_id=item["id"],
                creator_id=_item_cid_int,
                generation_id=_item_gid if _is_valid_gid(_item_gid) else None,
                # M6: content may carry a persisted operator edit
                # (draft_content overwritten by resolve); report it truthfully
                # from the authoritative row, never the stale snapshot.
                was_edited=_fresh_edited,
                actor_type=_flush_actor["actor_type"],
                actor_id=_flush_actor["actor_id"],
            )
            if result and result["ok"]:
                # M7 (B1/B3): durable enqueue record (hashes only) before the
                # approval transition below.
                await _record_audit(
                    event_type="enqueue",
                    actor=_flush_actor,
                    creator_id=_item_cid_int,
                    user_id=user_id,
                    action="send_worker.flush_queue",
                    content=content,
                    generation_id=_item_gid if _is_valid_gid(_item_gid) else None,
                    dedup_id=(result.get("dedup_id") if isinstance(result, dict) else None),
                    queue_id=item["id"],
                    state_before="pending",
                    state_after="enqueued",
                    result="enqueued",
                )
                try:
                    mutated = await resolve_queue_item(
                        item["id"],
                        "approved",
                        operator_id=_fresh.get("assigned_to"),
                        creator_id=_item_cid_int,
                    )
                    if not mutated:
                        logger.warning("flush_queue: queue item %s already resolved – skipping event", item["id"])
                        await _record_audit(
                            event_type="cancel_raced",
                            actor=_flush_actor,
                            creator_id=_item_cid_int,
                            user_id=user_id,
                            action="send_worker.flush_queue",
                            content=content,
                            generation_id=_item_gid if _is_valid_gid(_item_gid) else None,
                            dedup_id=(result.get("dedup_id") if isinstance(result, dict) else None),
                            queue_id=item["id"],
                            state_before="enqueued",
                            state_after="race_lost",
                            result="conflict",
                            error="already resolved after enqueue",
                        )
                        continue
                    # M7 (B1/B3): durable approval record.
                    await _record_audit(
                        event_type="approve",
                        actor=_flush_actor,
                        creator_id=_item_cid_int,
                        user_id=user_id,
                        action="send_worker.flush_queue",
                        content=content,
                        generation_id=_item_gid if _is_valid_gid(_item_gid) else None,
                        dedup_id=(result.get("dedup_id") if isinstance(result, dict) else None),
                        queue_id=item["id"],
                        state_before="pending",
                        state_after="approved",
                        result="approved",
                    )
                    from core.event_bus import publish_event

                    await publish_event(
                        "operator_queue.updated",
                        {
                            "queue_id": item["id"],
                            "action": "approved",
                            "user_id": user_id,
                        },
                        user_id=user_id,
                        dialog_id=user_id,
                        creator_id=_item_cid_int,
                        generation_id=_item_gid if _is_valid_gid(_item_gid) else None,
                        scope="user",
                    )
                    sent += 1
                except Exception:
                    logger.exception("Failed to resolve queue item %s after send", item["id"])
            else:
                logger.warning("Failed to enqueue queue item %s", item["id"])
                await _record_audit(
                    event_type="failure",
                    actor=_flush_actor,
                    creator_id=_item_cid_int,
                    user_id=user_id,
                    action="send_worker.flush_queue",
                    content=content,
                    generation_id=_item_gid if _is_valid_gid(_item_gid) else None,
                    queue_id=item["id"],
                    state_before="pending",
                    state_after="enqueue_failed",
                    result="failure",
                    error="enqueue_send failed",
                )

    return sent


async def _send_worker_cleanup() -> None:
    from db.postgres import close_pool

    logger.info("Send worker shutting down...")
    await close_pool()
    await close_redis()


async def run_send_worker(worker_id: str) -> None:
    await init_pool()
    await ensure_consumer_group()
    loop = asyncio.get_running_loop()
    heartbeat_stop = asyncio.Event()
    setup_signal_handlers([_send_worker_cleanup, lambda: heartbeat_stop.set()], loop=loop)

    logger.info("Send worker %s started", worker_id)

    _heartbeat_task = asyncio.create_task(
        write_heartbeat(
            worker_id,
            worker_type="send",
            interval_seconds=_settings.worker_heartbeat_interval,
            ttl_seconds=_settings.worker_heartbeat_ttl,
            stop_event=heartbeat_stop,
        )
    )

    while not is_shutting_down():
        try:
            await flush_queue(max_items=50)
        except Exception:
            logger.exception("Send worker loop error")

        if is_shutting_down():
            break
        await asyncio.sleep(5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send Worker")
    parser.add_argument("--worker-id", required=True)
    args = parser.parse_args()

    _settings_local = get_settings()
    setup_logging(structured=_settings_local.structured_logging)
    asyncio.run(run_send_worker(args.worker_id))


if __name__ == "__main__":
    main()
