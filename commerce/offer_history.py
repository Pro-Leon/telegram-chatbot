"""P3.3.10 — offer-event history reader (deterministic, read-only).

Read-only primitive over existing ``commerce_offers`` rows for one
``creator_id + user_id`` pair. Supplies the duplicate/reoffer facts the
future Opportunity Engine needs before ranking. No ranking, no scoring, no
pricing, no provider calls, no persistence, no cache, no Redis.

Source discipline
-----------------
Facts come exclusively from existing ``commerce_offers`` rows:

- frozen ``vault_item_ids`` snapshots when present (never reconstructed
  from live Drops, taxonomy, titles, folders, segments, analytics,
  transactions, delivery, conversation state, or LLM output);
- ``created_at`` for offer timing (24-hour UTC window, matching the
  established ``FanCommercialState`` / timing-reader convention);
- ``state`` with existing semantics preserved (``pending`` = offered,
  ``clicked`` = engaged not accepted, ``purchased`` = completed,
  ``declined`` = rejected, ``expired`` = expired, ``revoked`` =
  stored-only/history). No ``accepted`` state is introduced: a purchased
  row counts as a historical offer and a completed sale, without inferring
  a nonexistent acceptance field.

Known limitation (documented, not worked around)
------------------------------------------------
Historical ``commerce_offers`` rows carry NO OfferDefinition identity:
there is no ``definition_id`` / ``stable_key`` / ``version`` column on
``commerce_offers`` (verified against the P3.3.5 migration, which creates
``commerce_offer_definitions`` without touching ``commerce_offers``).
Therefore "was this exact definition identity/version previously offered"
CANNOT be established from existing rows. ``OfferHistory`` exposes
``definition_identity_available = False`` and
:meth:`OfferHistory.was_definition_offered` returns ``None`` (unknown).
Duplicate detection in this phase operates on frozen canonical Vault sets
and on deterministically observable active offers only.

Failure semantics: any database failure raises and is never converted to
zero offers / empty history / no active offer. An empty successful query
result is valid empty facts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from db.postgres import get_pool

logger = logging.getLogger("commerce.offer_history")

RECENT_WINDOW_HOURS = 24

ACTIVE_OFFER_STATES = frozenset({"pending", "clicked"})

KNOWN_OFFER_STATES = frozenset(
    {"pending", "clicked", "purchased", "declined", "expired", "revoked"}
)

#: Historical rows carry no OfferDefinition identity columns, so
#: definition-level reoffer checks are not supportable in this phase.
DEFINITION_IDENTITY_AVAILABLE = False

#: Bound on snapshot rows read per fan (creator+user scoped, newest first).
_SNAPSHOT_ROW_LIMIT = 1000

#: Bound on active-offer rows read per fan (active sets are tiny in practice).
_ACTIVE_ROW_LIMIT = 100


def _require_ids(creator_id: int, user_id: int) -> None:
    for name, value in (("creator_id", creator_id), ("user_id", user_id)):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} is required")


def _canonical_set(raw_ids: Any) -> tuple[str, ...] | None:
    """Canonicalize one frozen snapshot row; None when unusable/empty."""
    from commerce.vault_sets import canonical_identity_ids

    if raw_ids is None:
        return None
    try:
        canonical = canonical_identity_ids(list(raw_ids))
    except Exception:
        return None
    if not canonical:
        return None
    return tuple(canonical)


@dataclass(frozen=True)
class OfferHistory:
    """Deterministic offer-event facts for one creator + one fan (immutable)."""

    creator_id: int
    user_id: int

    total_offer_count: int
    recent_offer_count: int
    last_offer_at: datetime | None

    declined_offer_count: int
    recent_declined_offer_count: int

    state_counts: tuple[tuple[str, int], ...]

    has_active_offer: bool
    active_offer_count: int

    #: Distinct historical canonical Vault sets from frozen snapshots
    #: (sorted for determinism). Rows with NULL/empty snapshots contribute
    #: nothing here; see ``null_snapshot_count``.
    offered_vault_sets: tuple[tuple[str, ...], ...]

    #: Distinct canonical Vault sets on currently active (pending/clicked)
    #: offers, sorted for determinism.
    active_vault_sets: tuple[tuple[str, ...], ...]

    #: Rows whose snapshot is NULL/empty and therefore cannot participate
    #: in set-identity duplicate detection.
    null_snapshot_count: int

    #: Always False in this phase: historical rows carry no definition id.
    definition_identity_available: bool = False

    def was_canonical_set_offered(
        self, canonical_vault_ids: Iterable[str] | None
    ) -> bool:
        """Return True when the exact canonical set was previously offered.

        Pure helper over frozen snapshots. Unknown/unusable input yields
        False (no deterministically established prior offer), never True.
        """
        wanted = _canonical_set(canonical_vault_ids)
        if wanted is None:
            return False
        return wanted in set(self.offered_vault_sets)

    def has_active_canonical_set(
        self, canonical_vault_ids: Iterable[str] | None
    ) -> bool:
        """Return True when an active offer carries the exact canonical set."""
        wanted = _canonical_set(canonical_vault_ids)
        if wanted is None:
            return False
        return wanted in set(self.active_vault_sets)

    def was_definition_offered(
        self, definition_id: Any = None, version: Any = None
    ) -> None:
        """Always None (unknown): history rows carry no definition identity.

        Kept as an explicit method so callers cannot mistake set-identity
        duplicate detection for definition-identity detection.
        """
        return None


def _dedup_sorted(sets: Iterable[tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    return tuple(sorted(set(sets)))


async def get_offer_history(creator_id: int, user_id: int) -> OfferHistory:
    """Read one fan's offer-event history (creator-scoped, read-only).

    Three bounded, creator+user-scoped reads: a state aggregate, frozen
    snapshot sets, and active-offer snapshots. No N+1 queries, no writes,
    no cache, no Redis, no provider calls. Raises on any database failure;
    empty successful results are valid empty facts.
    """
    _require_ids(creator_id, user_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        aggregate = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (
                       WHERE created_at >= NOW() - INTERVAL '24 hours') AS recent,
                   MAX(created_at) AS last_at,
                   COUNT(*) FILTER (WHERE state = 'declined') AS declined_total,
                   COUNT(*) FILTER (
                       WHERE state = 'declined'
                         AND created_at >= NOW() - INTERVAL '24 hours'
                   ) AS declined_recent,
                   COUNT(*) FILTER (
                       WHERE state IN ('pending', 'clicked')) AS active_total,
                   COUNT(*) FILTER (WHERE state = 'pending') AS pending_total,
                   COUNT(*) FILTER (WHERE state = 'clicked') AS clicked_total,
                   COUNT(*) FILTER (WHERE state = 'purchased') AS purchased_total,
                   COUNT(*) FILTER (WHERE state = 'declined') AS declined_check,
                   COUNT(*) FILTER (WHERE state = 'expired') AS expired_total,
                   COUNT(*) FILTER (WHERE state = 'revoked') AS revoked_total,
                   COUNT(*) FILTER (WHERE vault_item_ids IS NULL) AS null_snapshots
            FROM commerce_offers
            WHERE creator_id = $1 AND user_id = $2
            """,
            creator_id,
            user_id,
        )
        snapshot_rows = await conn.fetch(
            """
            SELECT vault_item_ids
            FROM commerce_offers
            WHERE creator_id = $1 AND user_id = $2
              AND vault_item_ids IS NOT NULL
            ORDER BY created_at DESC
            LIMIT %d
            """
            % _SNAPSHOT_ROW_LIMIT,
            creator_id,
            user_id,
        )
        active_rows = await conn.fetch(
            """
            SELECT vault_item_ids
            FROM commerce_offers
            WHERE creator_id = $1 AND user_id = $2
              AND state IN ('pending', 'clicked')
            ORDER BY created_at DESC
            LIMIT %d
            """
            % _ACTIVE_ROW_LIMIT,
            creator_id,
            user_id,
        )
    if aggregate is None:
        raise RuntimeError("offer history unavailable: empty aggregate result")

    offered: list[tuple[str, ...]] = []
    for row in snapshot_rows or []:
        try:
            raw = row["vault_item_ids"]
        except Exception:
            try:
                raw = row.get("vault_item_ids")  # type: ignore[union-attr]
            except Exception:
                raw = None
        canonical = _canonical_set(raw)
        if canonical is not None:
            offered.append(canonical)

    active: list[tuple[str, ...]] = []
    for row in active_rows or []:
        try:
            raw = row["vault_item_ids"]
        except Exception:
            try:
                raw = row.get("vault_item_ids")  # type: ignore[union-attr]
            except Exception:
                raw = None
        canonical = _canonical_set(raw)
        if canonical is not None:
            active.append(canonical)

    active_total = int(aggregate["active_total"] or 0)
    state_counts = (
        ("pending", int(aggregate["pending_total"] or 0)),
        ("clicked", int(aggregate["clicked_total"] or 0)),
        ("purchased", int(aggregate["purchased_total"] or 0)),
        ("declined", int(aggregate["declined_check"] or 0)),
        ("expired", int(aggregate["expired_total"] or 0)),
        ("revoked", int(aggregate["revoked_total"] or 0)),
    )
    return OfferHistory(
        creator_id=creator_id,
        user_id=user_id,
        total_offer_count=int(aggregate["total"] or 0),
        recent_offer_count=int(aggregate["recent"] or 0),
        last_offer_at=aggregate["last_at"],
        declined_offer_count=int(aggregate["declined_total"] or 0),
        recent_declined_offer_count=int(aggregate["declined_recent"] or 0),
        state_counts=state_counts,
        has_active_offer=active_total > 0,
        active_offer_count=active_total,
        offered_vault_sets=_dedup_sorted(offered),
        active_vault_sets=_dedup_sorted(active),
        null_snapshot_count=int(aggregate["null_snapshots"] or 0),
        definition_identity_available=DEFINITION_IDENTITY_AVAILABLE,
    )
