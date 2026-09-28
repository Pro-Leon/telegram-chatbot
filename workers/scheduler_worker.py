"""P2.1 — Scheduler Worker.

Claims due scheduled messages from PostgreSQL and enqueues them into the
existing Redis send stream. Never sends Telegram messages directly.

Lifecycle: separate process via run_all.py, same pattern as send_worker.
"""

import argparse
import asyncio
import logging
from typing import Any

from core.config import get_settings
from core.logging_config import setup_logging
from core.shutdown import is_shutting_down, setup_signal_handlers
from core.worker_heartbeat import write_heartbeat
from db.postgres import (
    claim_due_messages,
    close_pool,
    get_scheduled_status,
    get_user_safety_info,
    init_pool,
    mark_scheduled_enqueued,
    mark_scheduled_failed,
    mark_scheduled_suppressed,
    recover_stale_messages,
)
from db.redis import close_redis, enqueue_send, ensure_consumer_group

logger = logging.getLogger("scheduler_worker")
_settings = get_settings()

SCHEDULER_POLL_INTERVAL: int = _settings.scheduler_poll_interval
SCHEDULER_BATCH_SIZE: int = _settings.scheduler_batch_size
SCHEDULER_RECOVERY_TIMEOUT: int = _settings.scheduler_recovery_timeout
SCHEDULER_MAX_RECOVERY_ATTEMPTS: int = _settings.scheduler_max_recovery_attempts
# P3-A: authoritative Dropfans sweep interval (single source of truth).
# General scheduler work stays on SCHEDULER_POLL_INTERVAL (10s); the full
# Dropfans reconciliation (check-status + earnings) runs only when due.
DROPFANS_SWEEP_INTERVAL: int = _settings.dropfans_reconciliation_interval_seconds
_last_dropfans_sweep: float = 0.0


def _dropfans_sweep_due(now: float | None = None) -> bool:
    """Return True when a periodic Dropfans sweep is due (P3-A)."""
    import time as _time

    current = now if now is not None else _time.monotonic()
    return (current - _last_dropfans_sweep) >= DROPFANS_SWEEP_INTERVAL


def _mark_dropfans_sweep(now: float | None = None) -> None:
    """Record a completed Dropfans sweep attempt (P3-A)."""
    import time as _time

    global _last_dropfans_sweep
    _last_dropfans_sweep = now if now is not None else _time.monotonic()


def _build_send_payload(msg: dict) -> dict:
    """Convert a scheduled_messages row into the existing send-stream payload."""
    # P2.1: creator_id is authoritative for P1.4 isolation
    cid = msg.get("creator_id")
    if cid is None:
        # Fail-closed: required by P1.4 — caller must validate
        raise ValueError("creator_id is required for scheduled send payload (P1.4)")
    # Phases 1-4: explicit synthetic correlation, never Telegram-derived.
    # Same scheduled row always yields the same ID (retry-safe).
    from core.generation import scheduled_generation_id

    _gid = msg.get("generation_id")
    if not isinstance(_gid, str) or not _gid.startswith("scheduled:"):
        _gid = scheduled_generation_id(str(msg.get("dedup_key", "job")), msg.get("id", "0"))
    return {
        "entity": str(msg["user_id"]),
        "content": msg["content"],
        "draft_content": msg["content"],
        "was_edited": False,
        "was_auto_approved": False,
        "confidence_score": 1.0,
        "operator_id": None,
        "save_to_db": True,
        "media_type": msg.get("media_type", ""),
        "media_path": msg.get("media_path", ""),
        "creator_id": str(cid),
        "generation_id": _gid,
    }


def _make_dedup_id(msg: dict) -> str:
    """Deterministic dedup identity for a scheduled message.

    Same scheduled job retry produces the same dedup_id, allowing the
    existing send-stream dedup to prevent duplicate Telegram messages.
    """
    return f"scheduled:{msg['dedup_key']}:{msg['id']}"


async def _check_boundary_contact_block(user_id: int, creator_id: Any | None) -> bool:
    """True when a durable boundary blocks autonomous outbound (Phase 7).

    Reads the creator-scoped boundary state (no new table/queue). Blocks
    on DO_NOT_CONTACT (no autonomous contact) and STOP_CONVERSATION (no
    offer/continuation on a closing turn). Fail-closed: an unreadable
    state suppresses the scheduled send rather than risking a violating
    message. No boundary state at all (missing/empty profile) is not a
    block. Never raises.
    """
    try:
        if creator_id is None:
            return False
        try:
            cid = int(creator_id)
        except Exception:
            return False
        from commerce.boundary_state import (
            derive_boundary_snapshot,
            get_boundary_constraints,
        )
        from db.postgres import get_user_profile
        try:
            profile = await get_user_profile(int(user_id))
        except Exception:
            logger.warning(
                "scheduler: boundary state unreadable user=%s creator=%s, suppressing",
                user_id, creator_id,
            )
            return True
        try:
            if not isinstance(profile, dict):
                return False
            constraints = get_boundary_constraints(profile, cid)
            snapshot = derive_boundary_snapshot(constraints)
            return bool(snapshot.blocks_contact() or snapshot.wants_close())
        except Exception:
            logger.warning(
                "scheduler: boundary evaluation failed user=%s creator=%s, suppressing",
                user_id, creator_id,
            )
            return True
    except Exception:
        return True


async def _check_user_eligible(user_id: int, creator_id: Any | None = None) -> bool:
    """Check if user is eligible for automated outbound messages."""
    info = await get_user_safety_info(user_id)
    if info is None:
        logger.warning("scheduler: user=%s not found, skipping", user_id)
        return False
    if info["is_blocked"]:
        logger.info("scheduler: user=%s is blocked, skipping", user_id)
        return False
    if info["do_not_auto_reply"]:
        logger.info("scheduler: user=%s has do_not_auto_reply, skipping", user_id)
        return False
    try:
        if await _check_boundary_contact_block(user_id, creator_id):
            logger.info(
                "scheduler: user=%s creator=%s has active contact/close boundary, skipping",
                user_id, creator_id,
            )
            return False
    except Exception:
        logger.warning(
            "scheduler: boundary check failed user=%s creator=%s, skipping",
            user_id, creator_id,
        )
        return False
    return True


async def process_due_messages(worker_id: str) -> int:
    """Claim due messages, check eligibility, enqueue into send stream.

    Returns count of messages successfully enqueued.

    M7 (B1/B4/B5): every lifecycle step emits a best-effort durable audit
    record with the server-derived scheduler actor. Ineligible recipients are
    terminally *suppressed* (``completed`` + ``last_error='suppressed:...'`` —
    no new status, so dashboard consumers are unaffected). The terminal mark
    result is checked: if the row left ``processing`` between enqueue and
    marking (cancel/recovery race), the race is audited as ``cancel_raced``
    while the actual send outcome is preserved.
    """
    from core.audit import record_audit_event as _record_audit

    _actor = {"actor_type": "scheduler", "actor_id": str(worker_id)}
    claimed = await claim_due_messages(
        batch_size=SCHEDULER_BATCH_SIZE,
        worker_id=worker_id,
    )
    enqueued = 0

    for msg in claimed:
        msg_id = msg["id"]
        try:
            full = await _fetch_message(msg_id)
            if full is None:
                logger.warning("scheduler: message %s not found after claim", msg_id)
                continue

            await _record_audit(
                event_type="claim",
                actor=_actor,
                creator_id=full.get("creator_id")
                if full.get("creator_id") is not None
                and str(full.get("creator_id")).strip().isdigit()
                else 0,
                user_id=full.get("user_id"),
                action="scheduler_worker.process_due_messages",
                content=full.get("content"),
                generation_id=full.get("generation_id")
                if isinstance(full.get("generation_id"), str)
                else None,
                schedule_id=msg_id,
                state_before="pending",
                state_after="processing",
                result="claimed",
                attempt=int(full.get("attempts") or 0),
                lease_token=str(full.get("claimed_by") or worker_id),
            )
            if full["status"] == "cancelled":
                logger.debug("scheduler: message %s was cancelled", msg_id)
                await _record_audit(
                    event_type="cancel",
                    actor=_actor,
                    creator_id=full.get("creator_id")
                    if full.get("creator_id") is not None
                    and str(full.get("creator_id")).strip().isdigit()
                    else 0,
                    user_id=full.get("user_id"),
                    action="scheduler_worker.process_due_messages",
                    schedule_id=msg_id,
                    state_before="processing",
                    state_after="cancelled",
                    result="cancelled_before_send",
                )
                continue

            if not await _check_user_eligible(full["user_id"], full.get("creator_id")):
                await mark_scheduled_suppressed(msg_id, "recipient_ineligible")
                await _record_audit(
                    event_type="suppress",
                    actor=_actor,
                    creator_id=full.get("creator_id")
                    if full.get("creator_id") is not None
                    and str(full.get("creator_id")).strip().isdigit()
                    else 0,
                    user_id=full.get("user_id"),
                    action="scheduler_worker.process_due_messages",
                    content=full.get("content"),
                    generation_id=full.get("generation_id")
                    if isinstance(full.get("generation_id"), str)
                    else None,
                    schedule_id=msg_id,
                    state_before="processing",
                    state_after="completed",
                    result="suppressed",
                    error="suppressed:recipient_ineligible",
                )
                continue

            # P2.1: creator_id required — fail-closed if missing/invalid per P1.4
            creator_id_val = full.get("creator_id")
            if creator_id_val is None or not str(creator_id_val).strip().isdigit():
                logger.warning("scheduler: message %s missing/invalid creator_id, marking failed (P1.4)", msg_id)
                await mark_scheduled_failed(msg_id, "creator_context_unavailable")
                await _record_audit(
                    event_type="failure",
                    actor=_actor,
                    creator_id=0,
                    user_id=full.get("user_id"),
                    action="scheduler_worker.process_due_messages",
                    schedule_id=msg_id,
                    state_before="processing",
                    state_after="failed",
                    result="failure",
                    error="creator_context_unavailable",
                )
                continue
            try:
                payload = _build_send_payload(full)
            except ValueError as ve:
                logger.warning("scheduler: message %s payload build failed: %s", msg_id, ve)
                await mark_scheduled_failed(msg_id, "creator_context_unavailable")
                await _record_audit(
                    event_type="failure",
                    actor=_actor,
                    creator_id=int(str(creator_id_val).strip()),
                    user_id=full.get("user_id"),
                    action="scheduler_worker.process_due_messages",
                    schedule_id=msg_id,
                    state_before="processing",
                    state_after="failed",
                    result="failure",
                    error=str(ve)[:200],
                )
                continue
            dedup_id = _make_dedup_id(full)
            # M7 (B5): scheduler actor threaded into the stream payload
            # (additive keys; the send consumer ignores unknown keys).
            payload["actor_type"] = _actor["actor_type"]
            payload["actor_id"] = _actor["actor_id"]
            try:
                await enqueue_send(
                    payload,
                    dedup_id=dedup_id,
                    creator_id=int(creator_id_val),
                    generation_id=payload.get("generation_id"),
                )
            except Exception as _enq_exc:
                # Phase 1.3: rails-review refusal holds (pending + last_error),
                # never terminally fails: the operator-authored job stays
                # retryable/editable. Other enqueue errors keep failed semantics.
                try:
                    from core.output_rails import RailsRefusal as _RailsRefusal

                    _is_rails_hold = isinstance(_enq_exc, _RailsRefusal)
                except Exception:
                    _is_rails_hold = isinstance(_enq_exc, ValueError) and str(_enq_exc).startswith(
                        "output-rails review:"
                    )
                if _is_rails_hold:
                    logger.warning(
                        "scheduler: message %s held for rails review: %s", msg_id, _enq_exc
                    )
                    try:
                        from db.postgres import get_pool as _hold_pool

                        _pool = await _hold_pool()
                        async with _pool.acquire() as _conn:
                            await _conn.execute(
                                "UPDATE scheduled_messages "
                                "SET status = 'pending', last_error = $1, updated_at = NOW() "
                                "WHERE id = $2 AND status = 'processing'",
                                f"held:{_enq_exc}"[:200],
                                msg_id,
                            )
                    except Exception:
                        logger.warning(
                            "scheduler: hold reset failed for message %s", msg_id, exc_info=True
                        )
                    await _record_audit(
                        event_type="failure",
                        actor=_actor,
                        creator_id=int(str(creator_id_val).strip()),
                        user_id=full.get("user_id"),
                        action="scheduler_worker.process_due_messages",
                        content=full.get("content"),
                        generation_id=payload.get("generation_id"),
                        dedup_id=dedup_id,
                        schedule_id=msg_id,
                        state_before="processing",
                        state_after="pending",
                        result="held_for_review",
                        error=str(_enq_exc)[:200],
                    )
                    continue
                logger.exception("scheduler: enqueue failed for message %s", msg_id)
                await mark_scheduled_failed(msg_id, "enqueue_error")
                await _record_audit(
                    event_type="failure",
                    actor=_actor,
                    creator_id=int(str(creator_id_val).strip()),
                    user_id=full.get("user_id"),
                    action="scheduler_worker.process_due_messages",
                    content=full.get("content"),
                    generation_id=payload.get("generation_id"),
                    dedup_id=dedup_id,
                    schedule_id=msg_id,
                    state_before="processing",
                    state_after="failed",
                    result="failure",
                    error=str(_enq_exc)[:200],
                )
                continue
            await _record_audit(
                event_type="enqueue",
                actor=_actor,
                creator_id=int(str(creator_id_val).strip()),
                user_id=full.get("user_id"),
                action="scheduler_worker.process_due_messages",
                content=full.get("content"),
                generation_id=payload.get("generation_id"),
                dedup_id=dedup_id,
                schedule_id=msg_id,
                state_before="processing",
                state_after="enqueued",
                result="enqueued",
            )
            # M7 (B4) cancel-race check: verify the row is still ours before
            # marking terminal. Cancellation requires 'pending' today, so a
            # non-processing state here means recovery or an external
            # transition raced us — never silently discard that fact.
            try:
                _status_now = await get_scheduled_status(msg_id)
            except Exception:
                _status_now = "processing"
            marked = await mark_scheduled_enqueued(msg_id)
            if _status_now != "processing" or not marked:
                logger.warning(
                    "scheduler: message %s left processing during enqueue "
                    "(status=%s marked=%s) — send outcome preserved, race audited",
                    msg_id,
                    _status_now,
                    marked,
                )
                await _record_audit(
                    event_type="cancel_raced",
                    actor=_actor,
                    creator_id=int(str(creator_id_val).strip()),
                    user_id=full.get("user_id"),
                    action="scheduler_worker.process_due_messages",
                    content=full.get("content"),
                    generation_id=payload.get("generation_id"),
                    dedup_id=dedup_id,
                    schedule_id=msg_id,
                    state_before="processing",
                    state_after=str(_status_now),
                    result="send_preserved_race",
                    error=f"terminal_mark_applied={marked}",
                )
                # The send already happened; count it, do not re-enqueue.
                enqueued += 1
                continue

            # Phase 10 Fix #3: Aftercare completion via scheduler after followup is enqueued
            # When a post-purchase followup is delivered, mark aftercare as completed
            # (best-effort, no new table, uses existing commerce/dao)
            try:
                if full.get("reason") == "post_purchase_followup" and full.get("creator_id"):
                    from commerce.dao import mark_aftercare_completed
                    await mark_aftercare_completed(full["creator_id"], full["user_id"])
            except Exception:
                logger.debug("scheduler: aftercare completion failed for message %s", msg_id, exc_info=True)

            logger.info(
                "scheduler: enqueued message=%s user=%s dedup=%s",
                msg_id,
                full["user_id"],
                dedup_id,
            )
            await _record_audit(
                event_type="success",
                actor=_actor,
                creator_id=int(str(creator_id_val).strip()),
                user_id=full.get("user_id"),
                action="scheduler_worker.process_due_messages",
                content=full.get("content"),
                generation_id=payload.get("generation_id"),
                dedup_id=dedup_id,
                schedule_id=msg_id,
                state_before="processing",
                state_after="completed",
                result="enqueued_completed",
                attempt=int(full.get("attempts") or 0),
            )
            enqueued += 1

        except Exception as _proc_exc:
            logger.exception("scheduler: failed to process message %s", msg_id)
            try:
                await mark_scheduled_failed(msg_id, "enqueue_error")
            except Exception:
                logger.exception("scheduler: failed to mark message %s as failed", msg_id)
            try:
                await _record_audit(
                    event_type="failure",
                    actor=_actor,
                    creator_id=0,
                    action="scheduler_worker.process_due_messages",
                    schedule_id=msg_id,
                    state_before="processing",
                    state_after="failed",
                    result="failure",
                    error=str(_proc_exc)[:200],
                )
            except Exception:
                pass

    return enqueued


async def _fetch_message(msg_id: int) -> dict | None:
    """Fetch a single scheduled message by ID."""
    from db.postgres import get_scheduled_message

    return await get_scheduled_message(msg_id)


async def recover_stale(worker_id: str) -> int:
    """Recover stale processing jobs. Returns count recovered."""
    recovered = await recover_stale_messages(
        stale_seconds=SCHEDULER_RECOVERY_TIMEOUT,
        batch_size=SCHEDULER_BATCH_SIZE,
        max_attempts=SCHEDULER_MAX_RECOVERY_ATTEMPTS,
    )
    if recovered:
        logger.info(
            "scheduler: recovered %d stale jobs worker=%s",
            len(recovered),
            worker_id,
        )
        # M7 (B1/B4): durable recovery records (best-effort).
        try:
            from core.audit import record_audit_event as _recover_audit

            for _rec in recovered:
                try:
                    await _recover_audit(
                        event_type="recover",
                        actor_type="scheduler",
                        actor_id=str(worker_id),
                        creator_id=0,
                        action="scheduler_worker.recover_stale",
                        schedule_id=int(_rec.get("id"))
                        if isinstance(_rec, dict) and _rec.get("id") is not None
                        else None,
                        state_before="processing",
                        state_after="pending",
                        result="recovered",
                    )
                except Exception:
                    continue
        except Exception:
            pass
    return len(recovered)


async def reconcile_purchases() -> int:
    """Reconcile Dropfans sales + unattributed purchases. Returns count attributed."""
    try:
        from commerce.reconciliation import reconcile_all

        return await reconcile_all()
    except Exception:
        logger.exception("scheduler: reconciliation failed")
        return 0


async def _scheduler_loop(worker_id: str) -> None:
    """Main scheduler loop: poll, claim, enqueue, recover, reconcile."""
    logger.info("Scheduler loop started worker=%s", worker_id)

    while not is_shutting_down():
        try:
            # Phase 24: Global + commerce pause gate before any scheduled work (fail-closed, best-effort)
            _should_skip_due = False
            try:
                from commerce.production_control import is_global_paused, is_commerce_paused
                if is_global_paused():
                    _should_skip_due = True
                    logger.info("scheduler: global pause active — skipping due message processing")
                # Commerce pause does not block scheduler claim itself, but blocks re-engagement downstream
            except Exception:
                pass
            if not _should_skip_due:
                await recover_stale(worker_id)
                await process_due_messages(worker_id)
                # P3-A: Dropfans full reconciliation only when its interval is due.
                # Due-message processing, recovery, orchestration and re-engagement
                # keep running every SCHEDULER_POLL_INTERVAL.
                if _dropfans_sweep_due():
                    await reconcile_purchases()
                    _mark_dropfans_sweep()
                else:
                    logger.debug("scheduler: dropfans sweep not due, skipping")
            else:
                # Still run orchestration to detect recovery
                await recover_stale(worker_id)
            # Phase 22/23: Production control orchestration (deterministic, no new worker/queue, best-effort)
            try:
                from commerce.production_control import orchestrate_production_controls
                # Run orchestration synchronously (pure, bounded, no DB) — not async
                orchestrate_production_controls()
            except Exception:
                logger.debug("scheduler: orchestration failed", exc_info=True)
            # Phase 27: Operational intelligence → recommendation → authorized execution (per creator, best-effort, pure, idempotent, bounded)
            try:
                from commerce.operational_intelligence import operational_decision as _op_dec27
                from commerce.operational_execution import execute_operational_recommendation as _op_exec27
                from commerce.production_control import evaluate_production_health as _eval27, MetricWindow as _MW27
                from db import fangate as _fdb27
                from datetime import datetime as _dt27, timezone as _tz27
                _creator_ids_27 = await _fdb27.list_active_creator_ids() or []
                for _cid27 in _creator_ids_27[:5]:  # bounded to 5 per cycle
                    try:
                        _health27 = _eval27(creator_id=_cid27, window=_MW27.H24)
                        _op_dec27_obj = _op_dec27(
                            creator_id=_cid27,
                            rejection_rate=_health27.rejection_rate,
                            handoff_rate=_health27.handoff_rate,
                            spam_rate=_health27.spam_rate,
                            pressure_suppressed_rate=_health27.pressure_suppressed_rate,
                            baseline_rate=0.30,
                            current_rate=_health27.purchase_rate,
                            sample_size=_health27.sample_size,
                            window="24h",
                        )
                        for _rec27 in _op_dec27_obj.recommendations:
                            if _rec27.allowed and _rec27.recommendation not in ("NO_ACTION", "OBSERVE"):
                                # Use scheduler generation_id for idempotency, creator-scoped
                                _rec27.generation_id = f"scheduler-{_cid27}-{int(_dt27.now(_tz27.utc).timestamp())}-{_rec27.recommendation}"
                                _op_exec27(_rec27, revalidate=True)
                    except Exception:
                        continue
            except Exception:
                logger.debug("operational intelligence scheduler execution failed", exc_info=True)
            # Phase 13: Autonomous re-engagement via existing scheduler (no new worker)
            try:
                from commerce.re_engagement import schedule_reengagement_if_eligible
                from db import fangate as _fdb
                creator_ids = await _fdb.list_active_creator_ids()
                for cid in creator_ids or []:
                    # Find users with abandoned offers (has_active_offer && age>=48h) via DAO
                    # Best-effort: iterate recent offers with active state
                    try:
                        from commerce.dao import list_offers_for_creator
                        # Get up to 50 active offers for this creator
                        active_offers = await list_offers_for_creator(cid, state="pending", limit=50)
                        for offer in active_offers:
                            # Check age >=48h
                            from datetime import datetime, timezone
                            created = offer.get("created_at")
                            if isinstance(created, str):
                                try:
                                    created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                                except Exception:
                                    continue
                            if created and created.tzinfo is None:
                                created = created.replace(tzinfo=timezone.utc)
                            if not created:
                                continue
                            age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600
                            if age_h >= 48:
                                # Phase 24: Re-engagement + commerce + global pause gate (fail-closed)
                                try:
                                    from commerce.production_control import is_reengagement_paused, is_commerce_paused, is_global_paused as _igp2
                                    if _igp2() or is_reengagement_paused(creator_id=cid) or is_commerce_paused(creator_id=cid):
                                        continue
                                except Exception:
                                    pass
                                # Phase 21: governed re-engagement (pressure/fatigue/max-frequency) — best-effort
                                try:
                                    from commerce.conversation_operations import compute_pressure as _cp_gov, derive_risk as _dr_gov, is_reengagement_governed_allowed as _is_gov
                                    from commerce.dao import get_timing_context as _gtc, get_behavioral_feedback_context as _gbf
                                    from commerce.adaptive_optimization import get_exposures_memory as _gem, compute_fatigue as _cf
                                    from commerce.production_control import query_metrics as _qm_gov, MetricWindow as _MW_gov
                                    _timing_gov = await _gtc(cid, offer["user_id"])
                                    _beh_gov = await _gbf(cid, offer["user_id"])
                                    _recent_exps_gov = _gem(cid, offer["user_id"], limit=5)
                                    _fat_gov = _cf(_recent_exps_gov, "re_engagement") if _recent_exps_gov else 0.0
                                    _pressure_gov = _cp_gov(recent_offer_count=_timing_gov.get("recent_offer_count",0), recent_rejection_count=_beh_gov.get("consecutive_rejections",0), aftercare=(_beh_gov.get("aftercare_status") in ("pending","sent")), cooldown=(_beh_gov.get("consecutive_rejections",0) >=3), fatigue=_fat_gov, objective="re_engage")
                                    # P2-B: real 7d count, not hardcoded 0, via query_metrics filtered by user_id
                                    try:
                                        _recent_events_gov = _qm_gov(name="reengagement_sent", creator_id=cid, window=_MW_gov.D7)
                                        _recent_cnt_gov = sum(1 for e in _recent_events_gov if e.get("user_id") == offer["user_id"])
                                    except Exception:
                                        _recent_cnt_gov = 0
                                    _allowed_gov, _reason_gov = _is_gov(has_active_offer=True, offer_age_hours=age_h, aftercare_active=(_beh_gov.get("aftercare_status") in ("pending","sent")), is_on_cooldown=(_beh_gov.get("consecutive_rejections",0) >=3), consecutive_rejections=_beh_gov.get("consecutive_rejections",0), has_relevant_unpurchased=True, relationship_state="warm", pressure=_pressure_gov, fatigue=_fat_gov, recent_reengagements_7d=_recent_cnt_gov)
                                    if not _allowed_gov:
                                        continue
                                except Exception:
                                    pass
                                # P3.4: pass the stale pending offer as the sole
                                # candidate identity. product_id is preserved
                                # verbatim for dedup-key continuity only.
                                _scheduled = await schedule_reengagement_if_eligible(creator_id=cid, user_id=offer["user_id"], product_id=offer["product_id"], offer=offer)
                                if _scheduled:
                                    try:
                                        from commerce.production_control import record_metric as _rm_gov
                                        _rm_gov(name="reengagement_sent", creator_id=cid, user_id=offer["user_id"], value=1.0)
                                    except Exception:
                                        pass
                                    # P3.5.1: re-engagement linkage (isolated, no new
                                    # offer, no gating change). Links the touch to
                                    # the original opportunity row when one exists.
                                    try:
                                        from commerce.opportunity_ledger import record_reengagement_touch
                                        await record_reengagement_touch(
                                            creator_id=cid, user_id=offer["user_id"], offer=offer
                                        )
                                    except Exception:
                                        pass
                    except Exception:
                        continue
            except Exception:
                logger.debug("scheduler: re-engagement check failed", exc_info=True)
        except Exception:
            logger.exception("scheduler: loop error worker=%s", worker_id)

        if is_shutting_down():
            break
        await asyncio.sleep(SCHEDULER_POLL_INTERVAL)


async def _scheduler_cleanup() -> None:
    logger.info("Scheduler worker shutting down...")
    await close_pool()
    await close_redis()


async def run_scheduler(worker_id: str) -> None:
    """Entry point for the scheduler worker process."""
    await init_pool()
    await ensure_consumer_group()
    try:
        from commerce.production_control import load_persisted_state
        _loaded = await load_persisted_state()
        import logging
        logging.getLogger("scheduler_worker").info("production_control: loaded persisted state %s", _loaded)
    except Exception:
        pass
    loop = asyncio.get_running_loop()
    heartbeat_stop = asyncio.Event()
    setup_signal_handlers(
        [_scheduler_cleanup, lambda: heartbeat_stop.set()],
        loop=loop,
    )

    logger.info("Scheduler worker %s started", worker_id)

    _heartbeat_task = asyncio.create_task(
        write_heartbeat(
            worker_id,
            worker_type="scheduler",
            interval_seconds=_settings.worker_heartbeat_interval,
            ttl_seconds=_settings.worker_heartbeat_ttl,
            stop_event=heartbeat_stop,
        )
    )

    await _scheduler_loop(worker_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scheduler Worker")
    parser.add_argument("--worker-id", required=True)
    args = parser.parse_args()

    _settings_local = get_settings()
    setup_logging(structured=_settings_local.structured_logging)
    asyncio.run(run_scheduler(args.worker_id))


if __name__ == "__main__":
    main()
