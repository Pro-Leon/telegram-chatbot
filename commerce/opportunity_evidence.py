"""P3.5.3B — Evidence Quality / Outcome Maturity read model (advisory only).

Durable, creator-safe, deterministic classification answering, at a label-as-of
time T: was this opportunity sufficiently exposed and sufficiently mature that
its observed outcome may safely be treated as future learning evidence?

Developer contract (read all ten points before consuming this module):

1. WHAT CONSTITUTES EXPOSURE. Durable exposure rungs, weakest first:
   DECISION (a decision row exists) → SEALED (``sealed_offer_id`` set) →
   SEND_ATTEMPTED (send tried, unconfirmed) → SENT (application-recorded
   send/execution, or purchase-entailed). Only the send rungs are stored
   (``exposure_state/exposure_at/exposure_source``); higher rungs derive from
   ``sealed_offer_id`` and outcome facts.
2. WHAT DOES NOT CONSTITUTE EXPOSURE. SENT means recorded send/execution —
   NOT confirmed recipient delivery or click. No delivery/click confirmation
   exists in this system; nothing here fabricates it. Future conversion math
   over this layer is ``purchases / sent_exposures`` — never "delivered
   conversion" or "click-through conversion". Message text, telemetry, and
   Dropfans URL generation are never exposure evidence.
3. MATURITY SEMANTICS. Terminal outcomes are mature once recorded
   (``as_of >= outcome_at``). Open states (PENDING/SENT/SEND_FAILED) mature
   only after ``MATURITY_WINDOW_HOURS`` past the latest knowable event; a
   mature-but-unresolved row stays CENSORED — maturity never manufactures a
   negative. Window (168h) derives from the existing reconciliation purchase-
   attribution window, not from business intuition; it is versioned
   (``MATURITY_POLICY_VERSION``) and applied in exactly one place.
4. LABEL-AS-OF SEMANTICS. Every classification takes ``as_of`` (default now).
   Only facts with timestamps ``<= as_of`` participate; later outcomes are
   invisible, so a historical label never leaks the future. ``as_of`` must be
   timezone-aware (naive values raise — ambiguous cutoffs are a leakage
   vector, not a default).
5. CENSORED/OPEN STATES. PENDING/SENT/SEND_FAILED are CENSORED until the
   maturity policy says otherwise — and even then they remain CENSORED, never
   negative. No row means UNAVAILABLE, never negative.
6. RECOVERED EVIDENCE. Rows carrying the ``recovered:`` generation namespace
   or snapshot recovery marker classify as PARTIAL, never FULL. Recovery
   semantics are read, never rewritten, here.
7. RE-ENGAGEMENT. Child touches (``reengagement_of`` set) classify as
   CENSORED exposure touches. Purchase attribution stays single-winner;
   revenue is counted once on the winning row (see
   :func:`list_offer_exposures`). Never aggregate two rows into two purchases.
8. CREATOR ISOLATION. Every read scopes ``creator_id`` first; grouping key is
   always ``creator_id`` (+ opportunity/offer). Cross-creator joins are
   forbidden.
9. TEMPORAL CUTOFF. Features must be cut at ``evaluated_at``; labels at
   ``as_of``. Current OfferDefinition/Drop/Vault/provider state is never
   consulted — history comes from frozen snapshots, offer rows, provenance,
   and ledger outcomes only.
10. WHAT FUTURE LEARNERS MAY CONSUME. Mature POSITIVE / COMMERCIAL_NEGATIVE /
    PROCESS_NEGATIVE labels with FULL (or explicitly PARTIAL) quality;
    NO_OPPORTUNITY/NO_SELECTION as separate system-state labels; CENSORED and
    UNAVAILABLE rows excluded from supervised labels (usable only for
    exposure counts). No numerical weights exist — quality is a cohort, and
    asserting numeric score equivalents for quality classes is explicitly
    rejected.

This module never writes, never calls providers/Redis/LLM, never alters
commercial authority. All classification is pure except the two read helpers,
which are creator-scoped SELECTs that fail closed to UNAVAILABLE.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("commerce.opportunity_evidence")

#: Maturity policy identity. Bumped only with an explicit, tested policy
#: change — never silently.
MATURITY_POLICY_VERSION = "p353b.v1"

#: Open opportunities mature this long after their latest knowable event.
#: Derived from the existing purchase-attribution horizon
#: (``RECONCILIATION_WINDOW_HOURS = 7 * 24`` in commerce.reconciliation):
#: attribution can legitimately arrive that late, so an earlier absence of
#: purchase proves nothing. A pinned regression test asserts equality.
MATURITY_WINDOW_HOURS = 7 * 24

# ---------------------------------------------------------------------------
# Vocabularies (closed; no numerical weights attached to any class).
# ---------------------------------------------------------------------------

#: Exposure rungs, weakest first. SENT = sent_exposure (recorded send or
#: purchase-entailed), never delivery/click confirmation.
EXPOSURE_UNAVAILABLE = "UNAVAILABLE"
EXPOSURE_DECISION = "DECISION"
EXPOSURE_SEALED = "SEALED"
EXPOSURE_SEND_ATTEMPTED = "SEND_ATTEMPTED"
EXPOSURE_SENT = "SENT"

EXPOSURE_STATES = frozenset(
    {
        EXPOSURE_UNAVAILABLE,
        EXPOSURE_DECISION,
        EXPOSURE_SEALED,
        EXPOSURE_SEND_ATTEMPTED,
        EXPOSURE_SENT,
    }
)

MATURITY_IMMATURE = "IMMATURE"
MATURITY_MATURE = "MATURE"

#: Supervised-label taxonomy. CENSORED and UNAVAILABLE are never negatives;
#: NO_OPPORTUNITY/NO_SELECTION are system states, not negatives.
LABEL_POSITIVE = "POSITIVE"
LABEL_COMMERCIAL_NEGATIVE = "COMMERCIAL_NEGATIVE"
LABEL_PROCESS_NEGATIVE = "PROCESS_NEGATIVE"
LABEL_CENSORED = "CENSORED"
LABEL_NO_OPPORTUNITY = "NO_OPPORTUNITY"
LABEL_NO_SELECTION = "NO_SELECTION"
LABEL_UNAVAILABLE = "UNAVAILABLE"

#: Evidence-quality cohorts. No weights — classification only.
QUALITY_FULL = "FULL"
QUALITY_PARTIAL = "PARTIAL"
QUALITY_UNATTRIBUTED = "UNATTRIBUTED"
QUALITY_UNAVAILABLE = "UNAVAILABLE"

#: Open (non-terminal) ledger outcomes: censored until mature, never negative.
OPEN_OUTCOMES = frozenset({"PENDING", "SENT", "SEND_FAILED"})

#: Decision-final outcomes: mature once the decision exists.
DECISION_FINAL_OUTCOMES = frozenset({"NO_OPPORTUNITY", "NO_SELECTION"})

#: Fan-commercial negatives (authoritative terminal fan behavior).
COMMERCIAL_NEGATIVE_OUTCOMES = frozenset({"DECLINED", "EXPIRED"})

#: Process negatives (system/process failure, distinct population).
PROCESS_NEGATIVE_OUTCOMES = frozenset({"SEAL_FAILED", "REVOKED", "CLICKED_NO_PURCHASE"})

_RECOVERY_GENERATION_PREFIX = "recovered:"


def _require_scope(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return int(value)


def _require_as_of(value: Any) -> datetime:
    """Require a timezone-aware label-as-of instant (fail closed, no coercion).

    Naive datetimes raise: an ambiguous cutoff is a temporal-leakage vector,
    and silently assuming a zone would move label boundaries.
    """
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("as_of must be a timezone-aware datetime")
    return value


def _coerce_aware(value: Any) -> datetime | None:
    """Coerce durable timestamps to tz-aware UTC (provider-skew rule)."""
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


def _col(row: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(row, dict):
            return row.get(key, default)
        return row[key]
    except Exception:
        return default


def _is_recovered(row: Any) -> bool:
    """Detect recovery markers without rewriting them (pure)."""
    generation = _col(row, "generation_id")
    if isinstance(generation, str) and generation.startswith(_RECOVERY_GENERATION_PREFIX):
        return True
    snapshot = _col(row, "decision_snapshot")
    try:
        parsed = json.loads(snapshot) if isinstance(snapshot, str) else (snapshot or {})
    except Exception:
        return False
    if not isinstance(parsed, dict):
        return False
    recovery = parsed.get("recovery")
    return isinstance(recovery, dict) and recovery.get("recovered") is True


def maturity_policy() -> dict[str, Any]:
    """Expose the pinned maturity policy for transparency (pure)."""
    return {
        "policy_version": MATURITY_POLICY_VERSION,
        "window_hours": MATURITY_WINDOW_HOURS,
        "window_source": "commerce.reconciliation RECONCILIATION_WINDOW_HOURS",
    }


def classify_opportunity_evidence(row: Any, *, as_of: datetime | None = None) -> dict[str, Any]:
    """Classify one ledger row as learning evidence at label-as-of time (pure).

    Uses only facts knowable by ``as_of``: the decision itself
    (``evaluated_at``), recorded send evidence (``exposure_*``), and the
    outcome (``outcome_state/outcome_at``). Later facts are invisible, so a
    historical label can never leak the future. Never raises on bad data —
    unparseable rows classify UNAVAILABLE (fail closed). Only a naive
    ``as_of`` raises (see :func:`_require_as_of`).
    """
    now = _require_as_of(as_of) if as_of is not None else datetime.now(UTC)
    if row is None:
        return _unavailable(reason="no_row")
    evaluated_at = _coerce_aware(_col(row, "evaluated_at"))
    if evaluated_at is None:
        return _unavailable(reason="missing_evaluated_at")
    if evaluated_at > now:
        # The decision postdates the cutoff: at ``as_of`` nothing existed.
        return _unavailable(reason="decision_after_cutoff")
    outcome_state = _col(row, "outcome_state")
    outcome_at = _coerce_aware(_col(row, "outcome_at"))
    outcome_known = (
        isinstance(outcome_state, str)
        and outcome_state
        and outcome_at is not None
        and outcome_at <= now
    )
    effective_outcome = outcome_state if outcome_known else None
    effective_outcome_at = outcome_at if outcome_known else None

    exposure_state, exposure_at, exposure_source, entailed = _effective_exposure(
        row, effective_outcome=effective_outcome, effective_outcome_at=effective_outcome_at, now=now
    )
    maturity_state, maturity_at = _maturity(
        effective_outcome=effective_outcome,
        effective_outcome_at=effective_outcome_at,
        evaluated_at=evaluated_at,
        exposure_at=exposure_at if exposure_at is not None and exposure_at <= now else None,
        now=now,
    )
    label, label_reason = _label(
        effective_outcome=effective_outcome,
        outcome_known=outcome_known,
        maturity_state=maturity_state,
        reengagement_of=_col(row, "reengagement_of"),
    )
    recovered = _is_recovered(row)
    quality = _quality(row, effective_outcome=effective_outcome, recovered=recovered)
    # Cutoff-gate outcome-attached values: when the outcome itself is not
    # knowable by as_of, its transaction/price/currency must not leak through
    # passthrough fields either. Identity/reference fields above are unaffected.
    if effective_outcome is None:
        gated_transaction = None
        gated_price = None
        gated_currency = None
    else:
        gated_transaction = _col(row, "transaction_id")
        gated_price = _col(row, "purchased_price_minor")
        gated_currency = _col(row, "purchased_currency")
    return {
        "creator_id": _col(row, "creator_id"),
        "opportunity_id": _col(row, "opportunity_id"),
        "evaluated_at": evaluated_at,
        "exposure_state": exposure_state,
        "exposure_at": exposure_at,
        "exposure_source": exposure_source,
        "exposure_entailed_by_purchase": entailed,
        "outcome_state": effective_outcome,
        "outcome_at": effective_outcome_at,
        "observed_outcome_state": outcome_state,
        "maturity_state": maturity_state,
        "maturity_at": maturity_at,
        "maturity_policy_version": MATURITY_POLICY_VERSION,
        "label": label,
        "label_reason": label_reason,
        "label_as_of": now,
        "evidence_quality": quality,
        "attribution_status": _col(row, "attribution_status"),
        "recovered": recovered,
        "reengagement_of": _col(row, "reengagement_of"),
        "sealed_offer_id": _col(row, "sealed_offer_id"),
        "definition_id": _col(row, "selected_definition_id"),
        "definition_version": _col(row, "selected_version"),
        "stable_key": _col(row, "selected_stable_key"),
        "purchased_price_minor": gated_price,
        "purchased_currency": gated_currency,
        "transaction_id": gated_transaction,
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "creator_id": None,
        "opportunity_id": None,
        "evaluated_at": None,
        "exposure_state": EXPOSURE_UNAVAILABLE,
        "exposure_at": None,
        "exposure_source": None,
        "exposure_entailed_by_purchase": False,
        "outcome_state": None,
        "outcome_at": None,
        "observed_outcome_state": None,
        "maturity_state": MATURITY_IMMATURE,
        "maturity_at": None,
        "maturity_policy_version": MATURITY_POLICY_VERSION,
        "label": LABEL_UNAVAILABLE,
        "label_reason": reason,
        "label_as_of": None,
        "evidence_quality": QUALITY_UNAVAILABLE,
        "attribution_status": None,
        "recovered": False,
        "reengagement_of": None,
        "sealed_offer_id": None,
        "definition_id": None,
        "definition_version": None,
        "stable_key": None,
        "purchased_price_minor": None,
        "purchased_currency": None,
        "transaction_id": None,
    }


def _effective_exposure(
    row: Any,
    *,
    effective_outcome: str | None,
    effective_outcome_at: datetime | None,
    now: datetime,
) -> tuple[str, datetime | None, str | None, bool]:
    """Derive the strongest durably evidenced exposure rung (pure).

    Stored send evidence counts only when its timestamp is knowable by the
    cutoff. A knowable PURCHASED outcome entails SENT exposure (a purchase
    through the offer link proves prior exposure); the entailment is flagged
    rather than silent. Pre-migration rows (outcome SENT/SEND_FAILED with
    exposure NONE) derive the rung from the knowable outcome timestamp.
    """
    stored = _col(row, "exposure_state") or "NONE"
    recorded_at = _coerce_aware(_col(row, "exposure_at"))
    record_usable = recorded_at is not None and recorded_at <= now
    if stored == "SENT" and record_usable:
        return EXPOSURE_SENT, recorded_at, _col(row, "exposure_source"), False
    if effective_outcome == "PURCHASED":
        return EXPOSURE_SENT, effective_outcome_at, _col(row, "exposure_source"), True
    if effective_outcome == "SENT":
        return EXPOSURE_SENT, effective_outcome_at, _col(row, "exposure_source"), False
    if stored == "SEND_ATTEMPTED" and record_usable:
        return EXPOSURE_SEND_ATTEMPTED, recorded_at, _col(row, "exposure_source"), False
    if effective_outcome == "SEND_FAILED":
        return EXPOSURE_SEND_ATTEMPTED, effective_outcome_at, _col(row, "exposure_source"), False
    if _col(row, "sealed_offer_id") is not None:
        return EXPOSURE_SEALED, None, None, False
    return EXPOSURE_DECISION, _coerce_aware(_col(row, "evaluated_at")), None, False


def _maturity(
    *,
    effective_outcome: str | None,
    effective_outcome_at: datetime | None,
    evaluated_at: datetime,
    exposure_at: datetime | None,
    now: datetime,
) -> tuple[str, datetime | None]:
    """Determine maturity at the cutoff (pure).

    Terminal outcomes are mature once recorded. Decision-final outcomes are
    mature once the decision exists. Open/unknown outcomes mature only after
    the policy window past the latest knowable event — and stay CENSORED.
    """
    if effective_outcome is None:
        anchor = evaluated_at
        if exposure_at is not None and exposure_at > anchor:
            anchor = exposure_at
        mature_at = anchor + timedelta(hours=MATURITY_WINDOW_HOURS)
        if now >= mature_at:
            return MATURITY_MATURE, mature_at
        return MATURITY_IMMATURE, mature_at
    if effective_outcome in DECISION_FINAL_OUTCOMES:
        return MATURITY_MATURE, evaluated_at
    if effective_outcome in OPEN_OUTCOMES:
        anchor = effective_outcome_at or evaluated_at
        if exposure_at is not None and exposure_at > anchor:
            anchor = exposure_at
        mature_at = anchor + timedelta(hours=MATURITY_WINDOW_HOURS)
        if now >= mature_at:
            return MATURITY_MATURE, mature_at
        return MATURITY_IMMATURE, mature_at
    return MATURITY_MATURE, effective_outcome_at or evaluated_at


def _label(
    *,
    effective_outcome: str | None,
    outcome_known: bool,
    maturity_state: str,
    reengagement_of: Any,
) -> tuple[str, str]:
    """Map effective outcome to a supervised-label class (pure)."""
    if effective_outcome is None:
        return LABEL_CENSORED, "outcome_not_knowable_at_cutoff"
    if reengagement_of is not None:
        # Scheduler re-exposure touch: counted as exposure, never as its own
        # purchase/negative label. Revenue stays on the winning row.
        return LABEL_CENSORED, "reengagement_touch"
    if effective_outcome == "PURCHASED":
        return LABEL_POSITIVE, "purchase_attributed"
    if effective_outcome in COMMERCIAL_NEGATIVE_OUTCOMES:
        return LABEL_COMMERCIAL_NEGATIVE, "terminal_fan_outcome"
    if effective_outcome in PROCESS_NEGATIVE_OUTCOMES or (
        effective_outcome == "SEND_FAILED" and maturity_state == MATURITY_MATURE
    ):
        return LABEL_PROCESS_NEGATIVE, "terminal_process_outcome"
    if effective_outcome in OPEN_OUTCOMES:
        return LABEL_CENSORED, "open_outcome"
    if effective_outcome == "NO_OPPORTUNITY":
        return LABEL_NO_OPPORTUNITY, "decision_system_state"
    if effective_outcome == "NO_SELECTION":
        return LABEL_NO_SELECTION, "decision_system_state"
    if effective_outcome == "REENGAGED":
        return LABEL_CENSORED, "reengagement_touch"
    return LABEL_CENSORED, "unrecognized_outcome_conservative"


def _quality(row: Any, *, effective_outcome: str | None, recovered: bool) -> str:
    """Evidence-quality cohort (pure). No weights — classification only."""
    if recovered:
        return QUALITY_PARTIAL
    if effective_outcome == "PURCHASED" and _col(row, "attribution_status") != "attributed":
        return QUALITY_UNATTRIBUTED
    if _col(row, "attribution_status") == "ambiguous":
        return QUALITY_UNATTRIBUTED
    return QUALITY_FULL


async def get_evidence(
    creator_id: int,
    opportunity_id: int,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Read one opportunity's evidence, creator-scoped, at label-as-of (read-only).

    Fail-closed: unknown ids and read errors classify UNAVAILABLE (never
    negative, never mature). Raises only on invalid scope/identity or a
    naive ``as_of``.
    """
    creator_id = _require_scope("creator_id", creator_id)
    if not isinstance(opportunity_id, int) or isinstance(opportunity_id, bool) or opportunity_id <= 0:
        raise ValueError("opportunity_id is required")
    now = _require_as_of(as_of) if as_of is not None else datetime.now(UTC)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM commerce_opportunity_decisions
                WHERE creator_id = $1 AND opportunity_id = $2
                """,
                creator_id,
                opportunity_id,
            )
    except Exception:
        logger.debug(
            "evidence read failed creator=%s opportunity=%s", creator_id, opportunity_id, exc_info=True
        )
        out = _unavailable(reason="read_error")
        out["label_as_of"] = now
        return out
    if row is None:
        out = _unavailable(reason="not_found")
        out["label_as_of"] = now
        return out
    return classify_opportunity_evidence(dict(row), as_of=now)


async def list_offer_exposures(
    creator_id: int,
    offer_id: int,
    *,
    as_of: datetime | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List per-touch evidence for one sealed offer, creator-scoped (read-only).

    Each linked row classifies individually so original and re-engagement
    touches stay distinguishable. Revenue is counted exactly once:
    ``revenue_events`` is 1 iff any touch carries an effective POSITIVE
    label, and ``purchase_winner_opportunity_id`` names the lowest such row
    (mirroring single-winner attribution). Fail-closed to empty on error.
    """
    creator_id = _require_scope("creator_id", creator_id)
    if not isinstance(offer_id, int) or isinstance(offer_id, bool) or offer_id <= 0:
        raise ValueError("offer_id is required")
    now = _require_as_of(as_of) if as_of is not None else datetime.now(UTC)
    try:
        limit = int(limit)
    except Exception:
        limit = 50
    limit = max(1, min(limit, 200))
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM commerce_opportunity_decisions
                WHERE creator_id = $1 AND sealed_offer_id = $2
                ORDER BY opportunity_id ASC
                LIMIT $3
                """,
                creator_id,
                offer_id,
                limit,
            )
    except Exception:
        logger.debug(
            "offer exposure list failed creator=%s offer=%s", creator_id, offer_id, exc_info=True
        )
        return {
            "creator_id": creator_id,
            "offer_id": offer_id,
            "label_as_of": now,
            "exposures": [],
            "purchase_winner_opportunity_id": None,
            "revenue_events": 0,
            "error": "read_error",
        }
    exposures = [classify_opportunity_evidence(dict(r), as_of=now) for r in (rows or [])]
    winners = [e["opportunity_id"] for e in exposures if e["label"] == LABEL_POSITIVE]
    return {
        "creator_id": creator_id,
        "offer_id": offer_id,
        "label_as_of": now,
        "exposures": exposures,
        "purchase_winner_opportunity_id": min(winners) if winners else None,
        "revenue_events": 1 if winners else 0,
        "error": None,
    }
