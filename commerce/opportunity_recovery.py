"""P3.5.2 — Opportunity Decision Orphan Recovery (observability/history repair only).

Recovers missing ``commerce_opportunity_decisions`` rows for already-sealed
``commerce_offers`` where the ledger link was never created (pre-ledger
seals, crash-after-seal, ambiguous-resolution purchases that bypassed the
ledger).

Authority boundary (read the whole docstring before touching this file):

- The authoritative source is the existing ``commerce_offers`` row plus its
  sealed reason-envelope provenance. Nothing is recovered from
  ``fangate_products``, Dropfans mirrors, message text, salesCount,
  segments, ``seller_earning``/``set_price``, ``users.first_*``, or
  ``ppv_analytics_daily``.
- Definition identity is validated exactly (creator + id + version +
  stable_key) through the creator-scoped OfferDefinition DAO, which remains
  addressable for retired definitions. Never substitutes the active version,
  another definition, Drop, or Vault set.
- The recovery snapshot contains ONLY recoverable facts. Decision-time
  candidate sets, ranking order/factors, conversation intent,
  FanCommercialState, OfferHistory, and LLM reasoning are marked unavailable
  — never fabricated. ``attribution_confidence`` is always ``'partial'``.
- Single-winner safety (P3.5.2.0 condition): recovery NEVER invokes
  ``record_purchase_by_offer`` for an offer that already has a PURCHASED
  ledger row. Purchased outcomes are written directly onto the recovered row
  by primary key. ``attribute_purchase_safe`` is the only recovery-safe
  purchase-linking primitive: existing-link and already-purchased checks run
  before any delegation.
- No provider calls, no new offers, no sealing, no sending, no ranking, no
  commercial-authority change. All writers are bounded and failure-isolated
  at their call sites.

Conventions (match ``commerce.opportunity_ledger``):

- Every query takes ``creator_id`` first and scopes SQL by it.
- Timestamps are timezone-aware UTC.
- ``plan_recovery`` is pure (no I/O): deterministic, fail-closed.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("commerce.opportunity_recovery")

RECOVERY_VERSION = "p35.2.v1"

#: Deterministic synthetic generation namespace for recovered rows. Normal
#: decision rows use inbound-turn generation ids; the ``recovered:`` prefix
#: is visibly recovery and cannot collide with them.
RECOVERY_GENERATION_TEMPLATE = "recovered:{creator_id}:{offer_id}"

#: Default bound for one orphan-recovery sweep cycle.
RECOVERY_SWEEP_LIMIT = 25

#: Hard cap for one sweep cycle (bounded even when callers ask for more).
RECOVERY_SWEEP_MAX = 100

# ---------------------------------------------------------------------------
# Recovery classifications (smallest vocabulary that fits the implementation).
# ``plan_recovery`` is pure and returns one of these; the async operation adds
# only ``RECOVERED`` for a successful insert.
# ---------------------------------------------------------------------------

RECOVERABLE = "RECOVERABLE"
RECOVERED = "RECOVERED"
ALREADY_LINKED = "ALREADY_LINKED"
ALREADY_PURCHASED = "ALREADY_PURCHASED"
LEGACY_UNATTRIBUTABLE = "LEGACY_UNATTRIBUTABLE"
MALFORMED_ENVELOPE = "MALFORMED_ENVELOPE"
DEFINITION_MISSING = "DEFINITION_MISSING"
DEFINITION_INVALID = "DEFINITION_INVALID"
OFFER_NOT_SEALED = "OFFER_NOT_SEALED"
CREATOR_MISMATCH = "CREATOR_MISMATCH"
UNRECOVERABLE = "UNRECOVERABLE"
RECOVERY_ERROR = "RECOVERY_ERROR"

#: Authoritative offer states mapped to honest ledger outcomes. Pending and
#: clicked (both non-terminal in ``commerce_offers``) map to PENDING — never
#: to the terminal ``CLICKED_NO_PURCHASE``, which would fabricate a purchase
#: verdict for an offer that may still convert.
_OFFER_TERMINAL_MAP = {
    "declined": "DECLINED",
    "expired": "EXPIRED",
    "revoked": "REVOKED",
}

_SEALED_LIFECYCLE_STATES = frozenset(
    {"pending", "clicked", "purchased", "declined", "expired", "revoked"}
)

#: Decision-time fact families that recovery can never reconstruct. Listed in
#: the snapshot so consumers can distinguish "unknown" from "empty".
_UNAVAILABLE_FACTS = (
    "eligible_candidates",
    "ineligible_candidates",
    "ranking_order",
    "ranking_factors",
    "ranking_policy",
    "conversation_state",
    "fan_commercial_state",
    "offer_history",
    "generation_timing",
    "llm_reasoning",
)


def _require_scope(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return int(value)


def _col(row: Any, key: str, default: Any = None) -> Any:
    """Read one column from a dict or asyncpg Record without assuming .get()."""
    try:
        if isinstance(row, dict):
            return row.get(key, default)
        return row[key]
    except Exception:
        return default


def _coerce_aware(value: Any) -> datetime | None:
    """Coerce to tz-aware UTC; naive values are assumed UTC (provider skew rule)."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def synthetic_generation_id(creator_id: int, offer_id: int) -> str:
    """Deterministic recovery generation id (pure).

    Creator-scoped, unique per recovered offer, visibly marked as recovery.
    Never presented as the original generation id.
    """
    creator_id = _require_scope("creator_id", creator_id)
    if not isinstance(offer_id, int) or isinstance(offer_id, bool) or offer_id <= 0:
        raise ValueError("offer_id is required")
    return RECOVERY_GENERATION_TEMPLATE.format(creator_id=creator_id, offer_id=offer_id)


def parse_sealed_provenance(offer: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and validate the sealed reason envelope (pure, no I/O).

    Returns ``(provenance, None)`` when the envelope establishes definition
    identity (definition_id > 0, definition_version >= 1, non-empty
    stable_key, envelope ``v == 1``), else ``(None, reason)`` where reason is
    ``LEGACY_UNATTRIBUTABLE`` (no envelope at all) or ``MALFORMED_ENVELOPE``.
    Optional seal metadata (timestamps, hash, allow_download, verifier
    version) is passed through only when well-typed — never required.
    """
    if not isinstance(offer, dict):
        return None, MALFORMED_ENVELOPE
    raw = offer.get("reason")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, LEGACY_UNATTRIBUTABLE
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return None, MALFORMED_ENVELOPE
    if not isinstance(parsed, dict):
        return None, MALFORMED_ENVELOPE
    try:
        definition_id = int(parsed.get("definition_id")) if parsed.get("definition_id") is not None else None
        definition_version = (
            int(parsed.get("definition_version")) if parsed.get("definition_version") is not None else None
        )
    except Exception:
        return None, MALFORMED_ENVELOPE
    stable_key = parsed.get("stable_key")
    try:
        version = int(parsed.get("v")) if parsed.get("v") is not None else None
    except Exception:
        version = None
    if (
        version != 1
        or definition_id is None
        or definition_id <= 0
        or definition_version is None
        or definition_version < 1
        or not isinstance(stable_key, str)
        or not stable_key.strip()
    ):
        return None, MALFORMED_ENVELOPE
    provenance: dict[str, Any] = {
        "definition_id": definition_id,
        "definition_version": definition_version,
        "stable_key": stable_key.strip(),
    }
    for key in ("sealed_at", "verified_at"):
        coerced = _coerce_aware(parsed.get(key))
        if coerced is not None:
            provenance[key] = coerced.isoformat()
    for key in ("verified_hash", "verifier_version"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            provenance[key] = value.strip()
    allow_download = parsed.get("allow_download")
    if isinstance(allow_download, bool):
        provenance["allow_download"] = allow_download
    return provenance, None


def plan_recovery(
    *,
    offer: Any,
    definition: Any,
    linked_rows: Any,
    expected_creator_id: int | None = None,
) -> dict[str, Any]:
    """Classify one candidate offer for orphan recovery (pure, no I/O).

    Inputs are already-fetched facts: the authoritative ``commerce_offers``
    row, the validated (or missing) OfferDefinition row, and existing ledger
    rows for ``(creator_id, sealed_offer_id)``. Never touches the database,
    providers, or commercial policy. Fail-closed: anything unexpected is
    ``UNRECOVERABLE`` or a precise skip classification — never recoverable
    by default.
    """
    if not isinstance(offer, dict):
        return {"classification": UNRECOVERABLE, "reason": "offer_missing"}
    try:
        offer_creator = int(offer.get("creator_id")) if offer.get("creator_id") is not None else None
        offer_id = int(offer.get("id")) if offer.get("id") is not None else None
        user_id = int(offer.get("user_id")) if offer.get("user_id") is not None else None
    except Exception:
        return {"classification": UNRECOVERABLE, "reason": "offer_identity_invalid"}
    if (
        offer_creator is None
        or offer_creator <= 0
        or offer_id is None
        or offer_id <= 0
        or user_id is None
        or user_id <= 0
    ):
        return {"classification": UNRECOVERABLE, "reason": "offer_identity_invalid"}
    if expected_creator_id is not None and int(expected_creator_id) != int(offer_creator):
        return {"classification": CREATOR_MISMATCH, "reason": "offer_creator_mismatch"}
    linked = list(linked_rows or [])
    for row in linked:
        if _col(row, "outcome_state") == "PURCHASED":
            return {
                "classification": ALREADY_PURCHASED,
                "reason": "purchased_row_exists",
                "offer_id": offer_id,
                "creator_id": offer_creator,
            }
    if linked:
        return {
            "classification": ALREADY_LINKED,
            "reason": "linked_row_exists",
            "offer_id": offer_id,
            "creator_id": offer_creator,
        }
    state = offer.get("state")
    if not isinstance(state, str) or state.strip().lower() not in _SEALED_LIFECYCLE_STATES:
        return {"classification": OFFER_NOT_SEALED, "reason": "offer_state_not_sealed"}
    state = state.strip().lower()
    provenance, envelope_error = parse_sealed_provenance(offer)
    if provenance is None:
        return {
            "classification": envelope_error or MALFORMED_ENVELOPE,
            "reason": "provenance_unavailable",
            "offer_id": offer_id,
            "creator_id": offer_creator,
        }
    if definition is None:
        return {
            "classification": DEFINITION_MISSING,
            "reason": "definition_not_found",
            "offer_id": offer_id,
            "creator_id": offer_creator,
        }
    try:
        def_id = int(_col(definition, "id")) if _col(definition, "id") is not None else None
        def_version = int(_col(definition, "version")) if _col(definition, "version") is not None else None
        def_creator = int(_col(definition, "creator_id")) if _col(definition, "creator_id") is not None else None
        def_stable = _col(definition, "stable_key")
    except Exception:
        return {"classification": DEFINITION_INVALID, "reason": "definition_unreadable"}
    if (
        def_id != provenance["definition_id"]
        or def_version != provenance["definition_version"]
        or def_creator != offer_creator
        or not isinstance(def_stable, str)
        or def_stable.strip() != provenance["stable_key"]
    ):
        return {
            "classification": DEFINITION_INVALID,
            "reason": "definition_identity_mismatch",
            "offer_id": offer_id,
            "creator_id": offer_creator,
        }
    if state == "purchased":
        outcome_state = "PURCHASED"
        # Attributed only when the authoritative transaction link exists;
        # a purchase without a transaction stays honest (unattributed) rather
        # than inventing attribution. Confidence remains partial either way.
        txn = offer.get("transaction_id")
        attribution_status = (
            "attributed" if isinstance(txn, str) and txn.strip() else "unattributed"
        )
    elif state in _OFFER_TERMINAL_MAP:
        outcome_state = _OFFER_TERMINAL_MAP[state]
        attribution_status = "unattributed"
    else:
        outcome_state = "PENDING"
        attribution_status = "unattributed"
    return {
        "classification": RECOVERABLE,
        "reason": "sealed_orphan",
        "offer_id": offer_id,
        "creator_id": offer_creator,
        "user_id": user_id,
        "outcome_state": outcome_state,
        "attribution_status": attribution_status,
        "generation_id": synthetic_generation_id(offer_creator, offer_id),
        "provenance": provenance,
    }


def build_recovery_snapshot(
    *,
    offer: dict[str, Any],
    provenance: dict[str, Any],
    definition: Any,
    outcome_state: str,
) -> dict[str, Any]:
    """Build a conservative recovery snapshot (pure, no I/O).

    Contains only facts recoverable from the authoritative offer row, the
    validated envelope, and the validated definition. Decision-time ranking
    and candidate facts are explicitly marked unavailable — empty collections
    mean "unknown, never reconstructed", per ``unavailable_facts``.
    ``attribution_confidence`` is always ``'partial'``: historical
    decision-time ranking data is absent by construction and never
    fabricated.
    """
    vault_ids = offer.get("vault_item_ids") or []
    try:
        vault_ids = [str(v) for v in list(vault_ids)]
    except Exception:
        vault_ids = []
    created = _coerce_aware(offer.get("created_at"))
    purchased = _coerce_aware(offer.get("purchased_at"))
    sealed_at = _coerce_aware(provenance.get("sealed_at")) or created
    return {
        "recovered_offer": {
            "id": offer.get("id"),
            "creator_id": offer.get("creator_id"),
            "user_id": offer.get("user_id"),
            "state": offer.get("state"),
            "price_minor": offer.get("price_minor"),
            "currency": offer.get("currency"),
            "drop_cuid": offer.get("dropfans_product_id"),
            "vault_item_ids": vault_ids,
            "transaction_id": offer.get("transaction_id"),
            "created_at": created.isoformat() if created else None,
            "purchased_at": purchased.isoformat() if purchased else None,
        },
        "provenance": dict(provenance),
        "selected": {
            "definition_id": provenance["definition_id"],
            "version": provenance["definition_version"],
            "stable_key": provenance["stable_key"],
        },
        # Honest absence markers: these keys exist so consumers never mistake
        # "missing" for "empty". Nothing here is reconstructed.
        "eligible": [],
        "ineligible": [],
        "ranking": {"available": False, "policy_version": None, "ranked_order": [], "factors": {}},
        "conversation": None,
        "fan": None,
        "history": None,
        "unavailable_facts": list(_UNAVAILABLE_FACTS),
        "outcome_state": outcome_state,
        "attribution_confidence": "partial",
        "recovery": {
            "recovered": True,
            "recovery_version": RECOVERY_VERSION,
            "recovery_reason": "sealed_orphan",
            "source": "commerce_offers",
            "generation_id": synthetic_generation_id(offer["creator_id"], offer["id"]),
            "sealed_at": sealed_at.isoformat() if sealed_at else None,
        },
    }


async def recover_orphan_offer(
    *,
    creator_id: int,
    offer_id: int,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Recover one missing decision row for an already-sealed offer.

    Bounded, creator-scoped, idempotent. Holds the repository-standard
    transaction advisory lock (``recovery:{creator}:{offer}``) so concurrent
    workers converge; the insert is additionally guarded by
    ``ON CONFLICT (creator_id, generation_id) DO NOTHING`` on the
    deterministic synthetic generation id, with a re-read fallback.

    Purchased/terminal outcomes are written directly onto the recovered row
    by primary key in the same INSERT — ``record_purchase_by_offer`` is never
    invoked here, so no spare eligible row can be claimed (P3.5.2.0
    condition). ``dry_run=True`` performs reads and classification only and
    issues no writes. Database errors yield ``RECOVERY_ERROR`` (explicit
    failure, never "nothing to recover"). Raises only on invalid scope.
    """
    creator_id = _require_scope("creator_id", creator_id)
    if not isinstance(offer_id, int) or isinstance(offer_id, bool) or offer_id <= 0:
        raise ValueError("offer_id is required")
    pool = await get_pool()
    try:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"recovery:{creator_id}:{offer_id}",
            )
            offer = await conn.fetchrow(
                "SELECT * FROM commerce_offers WHERE creator_id = $1 AND id = $2",
                creator_id,
                offer_id,
            )
            if offer is None:
                return {"classification": UNRECOVERABLE, "reason": "offer_not_found"}
            offer = dict(offer)
            linked = await conn.fetch(
                """
                SELECT opportunity_id, outcome_state, transaction_id
                FROM commerce_opportunity_decisions
                WHERE creator_id = $1 AND sealed_offer_id = $2
                ORDER BY opportunity_id ASC
                """,
                creator_id,
                offer_id,
            )
            provenance, _ = parse_sealed_provenance(offer)
            definition = None
            if provenance is not None:
                try:
                    from db.offer_definitions import get_offer_definition as _get_definition

                    definition = await _get_definition(
                        creator_id,
                        provenance["definition_id"],
                        provenance["definition_version"],
                    )
                except Exception:
                    logger.debug(
                        "orphan recovery: definition lookup failed creator=%s offer=%s",
                        creator_id,
                        offer_id,
                        exc_info=True,
                    )
                    return {"classification": RECOVERY_ERROR, "reason": "definition_lookup_failed"}
            plan = plan_recovery(
                offer=offer,
                definition=definition,
                linked_rows=linked,
                expected_creator_id=creator_id,
            )
            if plan.get("classification") != RECOVERABLE:
                return plan
            if dry_run:
                return {**plan, "dry_run": True}
            outcome_state = plan["outcome_state"]
            snapshot = build_recovery_snapshot(
                offer=offer,
                provenance=provenance or {},
                definition=definition,
                outcome_state=outcome_state,
            )
            txn_id = offer.get("transaction_id")
            txn_id = txn_id.strip() if isinstance(txn_id, str) and txn_id.strip() else None
            if outcome_state != "PURCHASED":
                txn_id = None
            evaluated_at = _coerce_aware(offer.get("created_at")) or datetime.now(UTC)
            outcome_at = (
                _coerce_aware(offer.get("purchased_at")) or datetime.now(UTC)
                if outcome_state == "PURCHASED"
                else datetime.now(UTC)
            )
            # P3.5.3B: durable send-level exposure for the recovered row.
            # A recovered purchase entails prior exposure (the fan bought
            # through the offer link); source 'purchase_implied' keeps that
            # inference explicit and distinct from recorded live sends.
            # Non-purchased recoveries record no send evidence (exposure
            # NONE); SEALED-level exposure derives from sealed_offer_id.
            if outcome_state == "PURCHASED":
                recovery_exposure = "SENT"
                recovery_exposure_source = "purchase_implied"
                recovery_exposure_at = outcome_at
            else:
                recovery_exposure = "NONE"
                recovery_exposure_source = None
                recovery_exposure_at = None
            row = await conn.fetchrow(
                """
                INSERT INTO commerce_opportunity_decisions
                    (creator_id, user_id, generation_id, evaluated_at,
                     decision_snapshot, selected_definition_id, selected_version,
                     selected_stable_key, decision_status, sealed_offer_id,
                     drop_cuid, outcome_state, outcome_at, transaction_id,
                     purchased_price_minor, purchased_currency,
                     attribution_status, attribution_confidence,
                     exposure_state, exposure_at, exposure_source)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8,
                        'SEALED', $9, $10, $11, $12, $13, $14, $15, $16, 'partial',
                        $17, $18, $19)
                ON CONFLICT (creator_id, generation_id) WHERE generation_id IS NOT NULL
                DO NOTHING
                RETURNING *
                """,
                creator_id,
                plan["user_id"],
                plan["generation_id"],
                evaluated_at,
                json.dumps(snapshot, sort_keys=True),
                provenance["definition_id"] if provenance else None,
                provenance["definition_version"] if provenance else None,
                provenance["stable_key"] if provenance else None,
                offer_id,
                offer.get("dropfans_product_id"),
                outcome_state,
                outcome_at,
                txn_id,
                offer.get("price_minor"),
                offer.get("currency"),
                plan["attribution_status"],
                recovery_exposure,
                recovery_exposure_at,
                recovery_exposure_source,
            )
            if row is None:
                # Lost a concurrent race: the winner already inserted our
                # deterministic generation id. Return it (idempotent).
                row = await conn.fetchrow(
                    """
                    SELECT * FROM commerce_opportunity_decisions
                    WHERE creator_id = $1 AND generation_id = $2
                    """,
                    creator_id,
                    plan["generation_id"],
                )
                if row is None:
                    return {"classification": RECOVERY_ERROR, "reason": "recovery_conflict_unreadable"}
                return {"classification": ALREADY_LINKED, "reason": "concurrent_recovery_won", "row": dict(row)}
            return {"classification": RECOVERED, "reason": "sealed_orphan", "row": dict(row)}
    except Exception:
        logger.warning(
            "orphan recovery failed creator=%s offer=%s", creator_id, offer_id, exc_info=True
        )
        return {"classification": RECOVERY_ERROR, "reason": "database_error"}


async def attribute_purchase_safe(
    *,
    creator_id: int,
    offer_id: int,
    transaction_id: str,
    price_minor: int | None = None,
    currency: str | None = None,
    purchased_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Recovery-safe purchase linkage (P3.5.2.0 residual-condition guard).

    Before delegating to the single-winner ``record_purchase_by_offer``:

    A. returns the existing row for ``(creator, offer, transaction)`` when
       the transaction is already linked (duplicate/retry convergence);
    B. returns the existing PURCHASED row for ``(creator, offer)`` when one
       exists and NEVER invokes ``record_purchase_by_offer`` — so a second
       same-transaction invocation with a spare eligible row cannot mark
       that spare row PURCHASED.

    Only when no linked and no purchased row exists does it delegate once to
    ``record_purchase_by_offer``. Unlinked offers return None without
    claiming anything. Raises only on invalid scope/identity.
    """
    creator_id = _require_scope("creator_id", creator_id)
    if not isinstance(offer_id, int) or isinstance(offer_id, bool) or offer_id <= 0:
        raise ValueError("offer_id is required")
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        raise ValueError("transaction_id is required")
    transaction_id = transaction_id.strip()
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT * FROM commerce_opportunity_decisions
            WHERE creator_id = $1 AND sealed_offer_id = $2 AND transaction_id = $3
            ORDER BY opportunity_id ASC
            LIMIT 1
            """,
            creator_id,
            offer_id,
            transaction_id,
        )
        if existing is not None:
            return dict(existing)
        purchased = await conn.fetchrow(
            """
            SELECT * FROM commerce_opportunity_decisions
            WHERE creator_id = $1 AND sealed_offer_id = $2 AND outcome_state = 'PURCHASED'
            ORDER BY opportunity_id ASC
            LIMIT 1
            """,
            creator_id,
            offer_id,
        )
        if purchased is not None:
            return dict(purchased)
    from commerce.opportunity_ledger import record_purchase_by_offer as _record_purchase

    return await _record_purchase(
        creator_id=creator_id,
        offer_id=offer_id,
        transaction_id=transaction_id,
        price_minor=price_minor,
        currency=currency,
        purchased_at=purchased_at,
    )


async def recover_orphan_decisions(
    *,
    limit: int = RECOVERY_SWEEP_LIMIT,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Bounded sweep for orphaned sealed offers missing a ledger row.

    Deterministic anti-join (missing link only), ordered by
    ``(creator_id, offer_id)``, capped at ``RECOVERY_SWEEP_MAX``. Each offer
    is processed through :func:`recover_orphan_offer` with per-offer failure
    isolation — one bad offer never aborts the sweep. Read failures yield an
    explicit ``error`` summary (never "nothing to recover"). Never raises,
    never calls providers, never creates offers/seals/sends. ``dry_run=True``
    issues no writes.
    """
    try:
        limit = int(limit)
    except Exception:
        limit = RECOVERY_SWEEP_LIMIT
    limit = max(1, min(limit, RECOVERY_SWEEP_MAX))
    summary: dict[str, Any] = {
        "checked": 0,
        "recovered": 0,
        "already_linked": 0,
        "already_purchased": 0,
        "skipped": {},
        "failed": 0,
        "dry_run": bool(dry_run),
        "error": None,
    }
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            candidates = await conn.fetch(
                """
                SELECT co.* FROM commerce_offers co
                LEFT JOIN commerce_opportunity_decisions od
                  ON od.creator_id = co.creator_id AND od.sealed_offer_id = co.id
                WHERE od.opportunity_id IS NULL
                  AND co.reason IS NOT NULL
                ORDER BY co.creator_id ASC, co.id ASC
                LIMIT $1
                """,
                limit,
            )
    except Exception:
        logger.warning("orphan recovery sweep query failed", exc_info=True)
        summary["error"] = "sweep_query_failed"
        return summary
    for candidate in candidates or []:
        try:
            offer = dict(candidate)
            creator = offer.get("creator_id")
            oid = offer.get("id")
            if not isinstance(creator, int) or isinstance(creator, bool) or creator <= 0:
                raise ValueError("candidate creator scope invalid")
            if not isinstance(oid, int) or isinstance(oid, bool) or oid <= 0:
                raise ValueError("candidate offer identity invalid")
            result = await recover_orphan_offer(creator_id=creator, offer_id=oid, dry_run=dry_run)
        except Exception:
            logger.warning("orphan recovery per-offer failed (isolated)", exc_info=True)
            summary["failed"] += 1
            continue
        summary["checked"] += 1
        cls = (result or {}).get("classification")
        if cls == RECOVERED:
            summary["recovered"] += 1
        elif cls == ALREADY_LINKED:
            summary["already_linked"] += 1
        elif cls == ALREADY_PURCHASED:
            summary["already_purchased"] += 1
        elif cls == RECOVERABLE and dry_run:
            summary["recovered"] += 1
        elif cls in (RECOVERY_ERROR,):
            summary["failed"] += 1
        else:
            skipped = summary["skipped"]
            skipped[cls or "UNKNOWN"] = skipped.get(cls or "UNKNOWN", 0) + 1
    return summary
