"""Phase 98 — Free-photo eligibility + atomic quota authorization.

Deterministic authorization/reservation primitive for autonomous
character→fan free photos.

Frozen policy (see Phase 96D, updated purchase-day refresh):
 - never-purchased fan → ceiling 2 / UTC day
 - purchased fan → ceiling 4 / UTC day, monotonic ever-purchased
 - purchase-day forgiveness: if qualifying purchase occurred today, only
   successful sends with sent_at > latest_purchase_today_at count;
   pre-purchase sends are forgiven. Hard cap remains 4/day (seq 1..4).
   Second purchase same day does NOT grant extra beyond 4.
 - only successful (status='sent') consumes allowance
 - manual/operator sends outside quota
 - free_media_pool is the only free-media authority
 - free_photo_deliveries is the quota authority
 - UTC calendar day is the boundary

This module is authorization + reservation ONLY:
 - does NOT download media, call Telegram, send photo
 - does NOT finalize media delivery, create PPV, invoke LLM
 - does NOT alter desire/temperature/readiness/sales-window
 - does NOT touch vault_media_deliveries

Concurrency: serialized per (creator, user, day) via
  pg_advisory_xact_lock(hashtextextended('free_photo:{creator}:{user}:{day}',0))
inside a single DB transaction. No SELECT-then-INSERT without serialization.

Ledger semantics:
 - seq 1..4 (DB CHECK), but authorization enforces ceiling 2 or 4
 - only status='sent' counts toward quota
 - status='pending' reserves a seq tentatively (prevents over-reservation)
 - status='failed' consumes zero — eligible for reuse

Failed retry semantics (compatible with Phase 97 unique constraints):
 - UNIQUE (creator,user,day,seq) and partial UNIQUE
   (creator,user,day,vault_item_id) WHERE vault_item_id IS NOT NULL
 - same-media pending → do not create another, converge on one
 - same-media sent → suppress, already_sent_same_media
 - same-media failed → reuse same row: UPDATE failed→pending,
   do not allocate new seq, do not increase sent count
 - different-media with existing failed slot occupying the next seq:
   reuse that failed row for the new media (UPDATE vault_item_id + status)
   so failed never permanently consumes a seq. This is the safest
   equivalent that preserves "failed = zero allowance" while respecting
   the UNIQUE(seq) invariant.

Purchase authority:
 - chatbot commerce only, via commerce_offers state='purchased' AND
   transaction_id IS NOT NULL, creator-scoped. Never uses
   fangate_transactions organic activity, LLM signals, or conversation text.
 - monotonic ever-purchased for ceiling 2→4, plus purchase-day forgiveness:
   latest qualifying purchased_at today determines refresh; sent_at > purchase
   strict comparison. Equal timestamp is forgiven (pre-refresh).

Day bucket:
 - authoritative UTC date: datetime.now(timezone.utc).date() or caller's
   now_utc converted to UTC. Never caller-provided arbitrary day string,
   never server-local date, never rolling 24h.

Approved free media:
 - EXISTS free_media_pool WHERE creator_id=$1 AND vault_item_id=$2
   AND status='approved'. Absence or revoked → denied.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("commerce.free_photo")


@dataclass(frozen=True)
class FreePhotoAuthorization:
    """Result of one atomic free-photo quota authorization.

    eligible == True  → reservation succeeded (or reused) and caller may
                        proceed to delivery phase.
    eligible == False → denied; reservation_id is None.

    day_bucket is always the authoritative UTC date used for the decision.
    ceiling is 2 or 4 per purchase tier.
    reservation_id / seq are present only when eligible.
    """

    eligible: bool
    reason: str
    day_bucket: date
    ceiling: int
    reservation_id: int | None = None
    seq: int | None = None
    vault_item_id: str | None = None
    status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "day_bucket": self.day_bucket.isoformat() if self.day_bucket else None,
            "ceiling": self.ceiling,
            "reservation_id": self.reservation_id,
            "seq": self.seq,
            "vault_item_id": self.vault_item_id,
            "status": self.status,
        }


def _utc_day_bucket(now_utc: datetime | None) -> date:
    """Derive authoritative UTC calendar date."""
    if now_utc is None:
        return datetime.now(timezone.utc).date()
    # Normalize to UTC
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)
    return now_utc.date()


async def _has_purchased_for_quota(conn: Any, creator_id: int, user_id: int) -> bool:
    """Creator-scoped chatbot-commerce purchase check.

    Monotonic: any state='purchased' with transaction_id qualifies for 4/day.
    Never uses fangate_transactions organic activity.
    """
    row = await conn.fetchrow(
        """
        SELECT 1 FROM commerce_offers
        WHERE creator_id = $1 AND user_id = $2
          AND state = 'purchased' AND transaction_id IS NOT NULL
        LIMIT 1
        """,
        creator_id,
        user_id,
    )
    return row is not None


async def _authorize_free_photo_inner(
    creator_id: int,
    user_id: int,
    vault_item_id: str,
    now_utc: datetime | None = None,
) -> FreePhotoAuthorization:
    """Internal deterministic implementation with clock injection.

    `now_utc` is a TEST-ONLY seam. Production callers must use
    `authorize_free_photo()` which derives the current UTC day internally.
    This separation ensures production cannot manipulate quota day via
    request-derived timestamps.
    """
    # ---- Input validation (before DB, no side effects) ----
    # Derive day_bucket early for return value even on early denials.
    day_bucket = _utc_day_bucket(now_utc)

    # Validate vault_item_id — invalid_media is deterministic
    if not isinstance(vault_item_id, str) or not vault_item_id.strip():
        return FreePhotoAuthorization(
            eligible=False,
            reason="invalid_media",
            day_bucket=day_bucket,
            ceiling=2,
            vault_item_id=vault_item_id,
            status=None,
        )

    vault_item_id = vault_item_id.strip()

    # Validate creator/user — we still need day_bucket/ceiling for audit,
    # but we can determine ceiling only after DB lookup. For invalid ids
    # we return a safe default ceiling 2.
    if not isinstance(creator_id, int) or creator_id <= 0 or not isinstance(user_id, int) or user_id <= 0:
        return FreePhotoAuthorization(
            eligible=False,
            reason="invalid_creator_or_user",
            day_bucket=day_bucket,
            ceiling=2,
            vault_item_id=vault_item_id,
        )

    pool = await get_pool()
    lock_key = f"free_photo:{creator_id}:{user_id}:{day_bucket.isoformat()}"

    async with pool.acquire() as conn, conn.transaction():
        # Serialize concurrent workers for same (creator, user, day)
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            lock_key,
        )

        # ---- Creator / user existence ----
        creator_row = await conn.fetchrow("SELECT 1 FROM creators WHERE id = $1", creator_id)
        if creator_row is None:
            return FreePhotoAuthorization(
                eligible=False,
                reason="invalid_creator_or_user",
                day_bucket=day_bucket,
                ceiling=2,
                vault_item_id=vault_item_id,
            )
        user_row = await conn.fetchrow("SELECT 1 FROM users WHERE id = $1", user_id)
        if user_row is None:
            return FreePhotoAuthorization(
                eligible=False,
                reason="invalid_creator_or_user",
                day_bucket=day_bucket,
                ceiling=2,
                vault_item_id=vault_item_id,
            )

        # ---- Approved free media (creator-scoped) ----
        pool_row = await conn.fetchrow(
            """
            SELECT status FROM free_media_pool
            WHERE creator_id = $1 AND vault_item_id = $2
            """,
            creator_id,
            vault_item_id,
        )
        if pool_row is None:
            # Absence means NOT free
            # need purchase tier for ceiling in response
            purchased = await _has_purchased_for_quota(conn, creator_id, user_id)
            ceiling = 4 if purchased else 2
            return FreePhotoAuthorization(
                eligible=False,
                reason="media_not_approved",
                day_bucket=day_bucket,
                ceiling=ceiling,
                vault_item_id=vault_item_id,
            )
        if pool_row["status"] != "approved":
            purchased = await _has_purchased_for_quota(conn, creator_id, user_id)
            ceiling = 4 if purchased else 2
            return FreePhotoAuthorization(
                eligible=False,
                reason="media_not_approved",
                day_bucket=day_bucket,
                ceiling=ceiling,
                vault_item_id=vault_item_id,
            )

        # ---- Purchase tier → ceiling + purchase-day forgiveness ----
        purchased = await _has_purchased_for_quota(conn, creator_id, user_id)
        ceiling = 4 if purchased else 2
        purchase_today_at: datetime | None = None
        if purchased:
            try:
                day_start = datetime.combine(day_bucket, datetime.min.time(), tzinfo=timezone.utc)
                day_end = day_start + timedelta(days=1)
                row_pt = await conn.fetchrow(
                    """
                    SELECT purchased_at FROM commerce_offers
                    WHERE creator_id=$1 AND user_id=$2 AND state='purchased' AND transaction_id IS NOT NULL
                      AND purchased_at >= $3 AND purchased_at < $4
                    ORDER BY purchased_at DESC LIMIT 1
                    """,
                    creator_id,
                    user_id,
                    day_start,
                    day_end,
                )
                if row_pt and row_pt["purchased_at"]:
                    ts = row_pt["purchased_at"]
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    else:
                        ts = ts.astimezone(timezone.utc)
                    purchase_today_at = ts
            except Exception:
                purchase_today_at = None

        # ---- Same-media state (idempotency) ----
        existing = await conn.fetchrow(
            """
            SELECT id, status, seq FROM free_photo_deliveries
            WHERE creator_id = $1 AND user_id = $2
              AND day_bucket = $3 AND vault_item_id = $4
            """,
            creator_id,
            user_id,
            day_bucket,
            vault_item_id,
        )
        if existing is not None:
            st = existing["status"]
            if st == "sent":
                return FreePhotoAuthorization(
                    eligible=False,
                    reason="already_sent_same_media",
                    day_bucket=day_bucket,
                    ceiling=ceiling,
                    vault_item_id=vault_item_id,
                    status=st,
                    reservation_id=existing["id"],
                    seq=existing["seq"],
                )
            if st == "pending":
                # Converge on existing pending — do not create another
                return FreePhotoAuthorization(
                    eligible=False,
                    reason="already_pending",
                    day_bucket=day_bucket,
                    ceiling=ceiling,
                    reservation_id=existing["id"],
                    seq=existing["seq"],
                    vault_item_id=vault_item_id,
                    status=st,
                )
            if st == "failed":
                # Reuse failed reservation for retry (same media)
                # Quota check still applies — failed does not count, but
                # we must not exceed ceiling of successful sends.
                # Purchase-day forgiveness: only sent after latest purchase today counts.
                if purchase_today_at is not None:
                    sent_cnt = await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM free_photo_deliveries
                        WHERE creator_id = $1 AND user_id = $2
                          AND day_bucket = $3 AND status = 'sent' AND sent_at > $4
                        """,
                        creator_id,
                        user_id,
                        day_bucket,
                        purchase_today_at,
                    )
                else:
                    sent_cnt = await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM free_photo_deliveries
                        WHERE creator_id = $1 AND user_id = $2
                          AND day_bucket = $3 AND status = 'sent'
                        """,
                        creator_id,
                        user_id,
                        day_bucket,
                    )
                if sent_cnt is not None and int(sent_cnt) >= ceiling:
                    return FreePhotoAuthorization(
                        eligible=False,
                        reason="quota_exhausted",
                        day_bucket=day_bucket,
                        ceiling=ceiling,
                        vault_item_id=vault_item_id,
                        status="failed",
                    )
                # Transition failed → pending, keep same seq
                updated = await conn.fetchrow(
                    """
                    UPDATE free_photo_deliveries
                    SET status = 'pending',
                        updated_at = NOW(),
                        sent_at = NULL
                    WHERE id = $1 AND status = 'failed'
                    RETURNING id, seq, status
                    """,
                    existing["id"],
                )
                if updated is None:
                    # Race: someone else mutated it; treat as pending duplicate
                    return FreePhotoAuthorization(
                        eligible=False,
                        reason="already_pending",
                        day_bucket=day_bucket,
                        ceiling=ceiling,
                        vault_item_id=vault_item_id,
                    )
                return FreePhotoAuthorization(
                    eligible=True,
                    reason="reused_failed",
                    day_bucket=day_bucket,
                    ceiling=ceiling,
                    reservation_id=updated["id"],
                    seq=updated["seq"],
                    vault_item_id=vault_item_id,
                    status=updated["status"],
                )
            # Unknown status — defensive deny
            return FreePhotoAuthorization(
                eligible=False,
                reason="invalid_media",
                day_bucket=day_bucket,
                ceiling=ceiling,
                vault_item_id=vault_item_id,
                status=st,
            )

        # ---- Quota check (only sent counts, purchase-day forgiveness) ----
        if purchase_today_at is not None:
            sent_cnt = await conn.fetchval(
                """
                SELECT COUNT(*) FROM free_photo_deliveries
                WHERE creator_id = $1 AND user_id = $2
                  AND day_bucket = $3 AND status = 'sent' AND sent_at > $4
                """,
                creator_id,
                user_id,
                day_bucket,
                purchase_today_at,
            )
        else:
            sent_cnt = await conn.fetchval(
                """
                SELECT COUNT(*) FROM free_photo_deliveries
                WHERE creator_id = $1 AND user_id = $2
                  AND day_bucket = $3 AND status = 'sent'
                """,
                creator_id,
                user_id,
                day_bucket,
            )
        sent_cnt = int(sent_cnt or 0)
        if sent_cnt >= ceiling:
            return FreePhotoAuthorization(
                eligible=False,
                reason="quota_exhausted",
                day_bucket=day_bucket,
                ceiling=ceiling,
                vault_item_id=vault_item_id,
            )

        # ---- Find next available seq (pending+sent occupy, failed is reclaimable) ----
        rows = await conn.fetch(
            """
            SELECT id, seq, status, vault_item_id FROM free_photo_deliveries
            WHERE creator_id = $1 AND user_id = $2 AND day_bucket = $3
            """,
            creator_id,
            user_id,
            day_bucket,
        )
        used_seqs: set[int] = set()
        failed_by_seq: dict[int, int] = {}
        for r in rows:
            s = int(r["seq"])
            st = r["status"]
            if st in ("pending", "sent"):
                used_seqs.add(s)
            elif st == "failed":
                # Track failed seq for potential reuse
                # If multiple failed share same seq (should not happen due to UNIQUE),
                # keep first.
                if s not in failed_by_seq:
                    failed_by_seq[s] = int(r["id"])

        candidate_seq: int | None = None
        candidate_failed_id: int | None = None
        for seq in range(1, ceiling + 1):
            if seq not in used_seqs:
                candidate_seq = seq
                candidate_failed_id = failed_by_seq.get(seq)
                break

        if candidate_seq is None:
            # No free slot in 1..ceiling among pending+sent; quota appears exhausted
            # Even though sent < ceiling, pending already reserves remaining slots.
            return FreePhotoAuthorization(
                eligible=False,
                reason="quota_exhausted",
                day_bucket=day_bucket,
                ceiling=ceiling,
                vault_item_id=vault_item_id,
            )

        # If candidate seq is currently held by a failed row, reuse that row
        # for the new media (different vault_item_id). This reclaims the slot
        # so failed truly consumes zero.
        if candidate_failed_id is not None:
            reused = await conn.fetchrow(
                """
                UPDATE free_photo_deliveries
                SET vault_item_id = $2,
                    status = 'pending',
                    updated_at = NOW(),
                    sent_at = NULL
                WHERE id = $1 AND status = 'failed'
                RETURNING id, seq, status
                """,
                candidate_failed_id,
                vault_item_id,
            )
            if reused is not None:
                return FreePhotoAuthorization(
                    eligible=True,
                    reason="reused_failed_slot",
                    day_bucket=day_bucket,
                    ceiling=ceiling,
                    reservation_id=reused["id"],
                    seq=reused["seq"],
                    vault_item_id=vault_item_id,
                    status=reused["status"],
                )
            # If update failed due to race (e.g., same failed was reused by concurrent txn),
            # fall through to INSERT attempt; the advisory lock should prevent this,
            # but handle defensively.

        # ---- Reserve new seq ----
        # INSERT may still collide on (creator,user,day,seq) or (creator,user,day,vault_item_id)
        # due to races outside lock domain — but lock domain covers this key, so safe.
        # Use RETURNING to get id.
        try:
            new_row = await conn.fetchrow(
                """
                INSERT INTO free_photo_deliveries
                    (creator_id, user_id, day_bucket, seq, vault_item_id, status)
                VALUES ($1, $2, $3, $4, $5, 'pending')
                RETURNING id, seq, status
                """,
                creator_id,
                user_id,
                day_bucket,
                candidate_seq,
                vault_item_id,
            )
        except Exception as exc:
            # Unique violations indicate concurrent duplicate or seq collision.
            # For same-media duplicate, we already handled; for seq collision,
            # treat as quota_exhausted or duplicate.
            msg = str(exc).lower()
            if "idx_free_photo_deliveries_media_day" in msg or "vault_item_id" in msg:
                return FreePhotoAuthorization(
                    eligible=False,
                    reason="already_pending",
                    day_bucket=day_bucket,
                    ceiling=ceiling,
                    vault_item_id=vault_item_id,
                )
            if "free_photo_deliveries" in msg and "seq" in msg:
                return FreePhotoAuthorization(
                    eligible=False,
                    reason="quota_exhausted",
                    day_bucket=day_bucket,
                    ceiling=ceiling,
                    vault_item_id=vault_item_id,
                )
            raise

        if new_row is None:
            return FreePhotoAuthorization(
                eligible=False,
                reason="quota_exhausted",
                day_bucket=day_bucket,
                ceiling=ceiling,
                vault_item_id=vault_item_id,
            )

        return FreePhotoAuthorization(
            eligible=True,
            reason="eligible",
            day_bucket=day_bucket,
            ceiling=ceiling,
            reservation_id=new_row["id"],
            seq=new_row["seq"],
            vault_item_id=vault_item_id,
            status=new_row["status"],
        )


async def authorize_free_photo(
    creator_id: int,
    user_id: int,
    vault_item_id: str,
    *,
    _now_utc: datetime | None = None,
    **kwargs: Any,
) -> FreePhotoAuthorization:
    """Production authorization seam — derives current UTC day internally.

    This is the ONLY production entry point. It does NOT accept a caller-
    supplied day or arbitrary timestamp; the quota day is always the
    current UTC calendar day (`datetime.now(timezone.utc).date()`).

    The ``_now_utc`` parameter and the deprecated ``now_utc`` kwarg are
    TEST-ONLY seams for deterministic UTC-boundary tests. They are
    private (leading underscore) and must never be supplied by runtime
    request handling (message timestamp, user timestamp, etc.).
    """
    # Backward-compat: tests historically used `now_utc=`; accept it but
    # treat as test-only. Production callers must not pass it.
    now_utc = _now_utc
    if "now_utc" in kwargs:
        # Deprecated alias — only for existing unit tests
        if now_utc is None:
            now_utc = kwargs.pop("now_utc")
        else:
            kwargs.pop("now_utc", None)
    if kwargs:
        raise TypeError(f"unexpected kwargs: {list(kwargs)}")
    return await _authorize_free_photo_inner(creator_id, user_id, vault_item_id, now_utc=now_utc)


# Backward-compatible alias for existing tests that import `authorize_free_photo(now_utc=...)`.
# New tests should use `_now_utc` or the private inner directly.
async def authorize_free_photo_with_clock(
    creator_id: int, user_id: int, vault_item_id: str, now_utc: datetime | None = None
) -> FreePhotoAuthorization:
    """Explicit test-only helper — deterministic clock injection."""
    return await _authorize_free_photo_inner(creator_id, user_id, vault_item_id, now_utc=now_utc)


# ---------------------------------------------------------------------------
# Helpers for testing / future delivery phase (not used in Phase 98 routing)
# ---------------------------------------------------------------------------

async def mark_free_photo_sent(reservation_id: int) -> bool:
    """Transition a pending reservation to sent (delivery phase).

    Not used in Phase 98 itself; provided for completeness and tests.
    Returns True if row transitioned.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE free_photo_deliveries
            SET status = 'sent',
                sent_at = NOW(),
                updated_at = NOW()
            WHERE id = $1 AND status = 'pending'
            """,
            reservation_id,
        )
        return result.endswith("UPDATE 1")


async def mark_free_photo_failed(reservation_id: int) -> bool:
    """Transition a pending reservation to failed.

    Failed consumes zero allowance; seq becomes reclaimable via
    authorize_free_photo reuse logic.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE free_photo_deliveries
            SET status = 'failed',
                updated_at = NOW()
            WHERE id = $1 AND status = 'pending'
            """,
            reservation_id,
        )
        return result.endswith("UPDATE 1")


async def count_sent_today(creator_id: int, user_id: int, day_bucket: date) -> int:
    """Return count of successful sends for creator/user/day (for tests)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        cnt = await conn.fetchval(
            """
            SELECT COUNT(*) FROM free_photo_deliveries
            WHERE creator_id = $1 AND user_id = $2
              AND day_bucket = $3 AND status = 'sent'
            """,
            creator_id,
            user_id,
            day_bucket,
        )
        return int(cnt or 0)

