"""P3.5.1 — Outcome Attribution Foundation ledger (observability only).

Durable, creator-scoped record of commercial opportunity evaluations: what
the Opportunity Engine saw at decision time (eligible/ineligible/selected
candidates, ranking policy/factors), how sealing resolved, and what the
eventual commercial outcome was.

Never a decision authority. Nothing in eligibility, ranking, pricing,
sealing, execution, sending, or provider behavior reads this table. All
production writers are best-effort and failure-isolated at their call
sites; the functions below raise on database errors so callers can
isolate them deterministically (a ledger failure must never break
generation, sealing, or send).

Conventions:
- Every query takes ``creator_id`` first and scopes SQL by it.
- Decision snapshot columns are written once at insert; outcome writers
  only touch outcome-side columns (see ``_OUTCOME_COLUMNS``).
- Timestamps are timezone-aware UTC. Provider/local clock skew never
  rejects an otherwise valid purchase.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from db.postgres import get_pool

logger = logging.getLogger("commerce.opportunity_ledger")

DECISION_STATUSES = frozenset(
    {"NO_OPPORTUNITY", "NO_SELECTION", "DECIDED", "SEALED", "SEAL_FAILED", "REENGAGED"}
)

OUTCOME_STATES = frozenset(
    {
        "PENDING",
        "SENT",
        "SEND_FAILED",
        "PURCHASED",
        "DECLINED",
        "EXPIRED",
        "CLICKED_NO_PURCHASE",
        "REVOKED",
        "REENGAGED",
        "SEAL_FAILED",
        "NO_OPPORTUNITY",
        "NO_SELECTION",
    }
)

#: Outcome states that still permit a later terminal update. Everything
#: else is terminal and never overwritten by outcome writers.
NON_TERMINAL_OUTCOMES = frozenset({"PENDING", "SENT", "SEND_FAILED"})

ATTRIBUTION_STATUSES = frozenset({"unattributed", "attributed", "ambiguous"})

ATTRIBUTION_CONFIDENCE = frozenset({"full", "partial", "unattributed"})

#: Columns an outcome writer may touch. The decision snapshot and
#: selection identity are immutable after insert.
#: P3.5.3B: send-level exposure evidence (exposure_state/exposure_at/
#: exposure_source) is outcome-side and monotonic: only the send writer sets
#: it; terminal/purchase writers preserve it.
_OUTCOME_COLUMNS = (
    "outcome_state",
    "outcome_at",
    "transaction_id",
    "purchased_price_minor",
    "purchased_currency",
    "attribution_status",
    "updated_at",
    "exposure_state",
    "exposure_at",
    "exposure_source",
)


def _require_scope(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return int(value)


def _require_evaluated_at(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("evaluated_at must be a timezone-aware datetime")
    return value


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


def _get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    try:
        return getattr(source, key, default)
    except Exception:
        return default


def _col(row: Any, key: str, default: Any = None) -> Any:
    """Read one column from a dict or asyncpg Record without assuming .get()."""
    try:
        if isinstance(row, dict):
            return row.get(key, default)
        return row[key]
    except Exception:
        return default


def _jsonable(value: Any) -> Any:
    """Convert decision-time facts to JSON-safe structures (pure, no I/O)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(v) for v in value), key=str)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    try:
        if is_dataclass(value) and not isinstance(value, type):
            return _jsonable(asdict(value))
    except Exception:
        pass
    return str(value)


def candidate_identity_snapshot(candidate: Any) -> dict[str, Any]:
    """Project one candidate to its immutable commercial identity (pure).

    Only explicit catalog/snapshot fields. No taxonomy, folder, title,
    family inference, or provider state.
    """
    raw_ids = _get(candidate, "canonical_vault_item_ids") or []
    vault_ids = [
        i
        for i in (list(raw_ids) if isinstance(raw_ids, (list, tuple)) else [])
        if isinstance(i, str)
    ]
    raw_drops = _get(candidate, "mapped_drop_ids") or []
    drops = sorted(
        {
            d
            for d in (
                list(raw_drops) if isinstance(raw_drops, (list, tuple, set, frozenset)) else []
            )
            if isinstance(d, str)
        }
    )
    price = _get(candidate, "price_minor")
    if isinstance(price, bool) or not isinstance(price, int):
        price = None
    definition_id = _get(candidate, "definition_id")
    if isinstance(definition_id, bool) or not isinstance(definition_id, int):
        definition_id = None
    version = _get(candidate, "version")
    if isinstance(version, bool) or not isinstance(version, int):
        version = None
    return {
        "definition_id": definition_id,
        "version": version,
        "stable_key": _get(candidate, "stable_key"),
        "offer_type": _get(candidate, "offer_type"),
        "vault_ids": vault_ids,
        "price_minor": price,
        "currency": _get(candidate, "currency"),
        "mapped_drop_ids": drops,
    }


def build_decision_snapshot(opportunity_result: Any, conversation: Any = None) -> dict[str, Any]:
    """Freeze what the engine actually saw at decision time (pure, no I/O).

    Raises TypeError when there is no result to freeze. Never re-reads
    FanCommercialState or OfferHistory: the caller-supplied result already
    carries the frozen facts by reference.
    """
    if opportunity_result is None:
        raise TypeError("opportunity_result is required")
    eligible = [
        candidate_identity_snapshot(c)
        for c in (_get(opportunity_result, "eligible_candidates") or ())
    ]
    ineligible: list[dict[str, Any]] = []
    for pair in _get(opportunity_result, "ineligible") or ():
        try:
            cand, verdict = pair
        except Exception:
            continue
        entry = candidate_identity_snapshot(cand)
        reasons = _get(verdict, "denial_reasons", ()) or ()
        entry["denial_reasons"] = [str(r) for r in list(reasons)]
        ineligible.append(entry)
    ranking_result = _get(opportunity_result, "ranking_result")
    ranked_order: list[Any] = []
    factors: dict[str, Any] = {}
    policy_version = _get(ranking_result, "policy_version")
    if ranking_result is not None:
        for rc in _get(ranking_result, "ranked") or ():
            did = _get(rc, "definition_id")
            ranked_order.append(did)
            factors[str(did)] = [str(f) for f in (_get(rc, "factors") or ())]
    selected = _get(opportunity_result, "selected_candidate")
    conversation_ctx = (
        conversation
        if conversation is not None
        else _get(opportunity_result, "ranking_conversation")
    )
    # Phase 10: stamp which deterministic configuration/strategy version
    # produced this snapshot (additive; historical rows remain unversioned).
    # Creator-scoped when the result carries creator identity; otherwise the
    # global default (legacy) applies. Never invents a version.
    try:
        from commerce.phase10_learning import runtime_snapshot as _p10_snap

        _p10_creator = _get(opportunity_result, "creator_id")
        try:
            _p10_creator = int(_p10_creator) if _p10_creator is not None else None
        except Exception:
            _p10_creator = None
        _p10 = _p10_snap(creator_scope=_p10_creator)
        _cfg_v = str(_p10.get("config_version") or "unversioned-legacy")
        _strat_v = str(_p10.get("strategy_version") or "strategy.v1")
    except Exception:
        _cfg_v, _strat_v = "unversioned-legacy", "strategy.v1"
    return {
        "eligible": eligible,
        "ineligible": ineligible,
        "selected": candidate_identity_snapshot(selected) if selected is not None else None,
        "ranking": {
            "policy_version": policy_version,
            "ranked_order": ranked_order,
            "factors": factors,
        },
        "conversation": {
            "lifecycle": _get(conversation_ctx, "lifecycle"),
            "current_topic": _get(conversation_ctx, "current_topic"),
            "recent_topics": list(_get(conversation_ctx, "recent_topics") or ()),
            "open_threads": list(_get(conversation_ctx, "open_threads") or ()),
        },
        "fan": _jsonable(_get(opportunity_result, "fan_commercial_state")),
        "history": _jsonable(_get(opportunity_result, "offer_history")),
        "config_version": _cfg_v,
        "strategy_version": _strat_v,
    }


def _decision_status_for(opportunity_result: Any) -> tuple[str, str, str | None]:
    """Map an engine result to (decision_status, outcome_state, reason) (pure)."""
    status = _get(opportunity_result, "status")
    if status in ("NO_CANDIDATES", "NO_ELIGIBLE_CANDIDATES"):
        return "NO_OPPORTUNITY", "NO_OPPORTUNITY", str(status)
    if status == "NO_SELECTION":
        return "NO_SELECTION", "NO_SELECTION", str(status)
    if (
        _get(opportunity_result, "has_opportunity") is True
        and _get(opportunity_result, "selected_candidate") is not None
    ):
        return "DECIDED", "PENDING", None
    if _get(opportunity_result, "selected_candidate") is None:
        return "NO_SELECTION", "NO_SELECTION", str(status) if status else "NO_SELECTION"
    return "DECIDED", "PENDING", None


def merge_outcome_into_reason(
    existing_reason: Any,
    outcome_state: str,
    detail: str | None = None,
    now: datetime | None = None,
) -> str:
    """Fold terminal-outcome metadata into a commerce_offers reason (pure).

    Sealed JSON envelopes keep every existing key and gain ``outcome_*``
    keys (first write wins — outcomes are terminal). Legacy free-text
    reasons keep their text with the outcome appended. Never destroys
    sealed provenance; never invents definition identity.
    """
    if outcome_state not in OUTCOME_STATES:
        raise ValueError(f"unknown outcome_state: {outcome_state!r}")
    stamp = _coerce_aware(now) or datetime.now(UTC)
    parsed: Any = None
    if isinstance(existing_reason, str) and existing_reason.strip():
        try:
            parsed = json.loads(existing_reason)
        except Exception:
            parsed = None
    elif isinstance(existing_reason, dict):
        parsed = dict(existing_reason)
    if isinstance(parsed, dict):
        merged = dict(parsed)
        merged.setdefault("outcome_state", outcome_state)
        merged.setdefault("outcome_at", stamp.isoformat())
        if detail is not None:
            merged.setdefault("outcome_detail", str(detail))
        return json.dumps(merged, sort_keys=True)
    base = (
        existing_reason.strip()
        if isinstance(existing_reason, str) and existing_reason.strip()
        else ""
    )
    suffix = f"{outcome_state}:{detail}" if detail else outcome_state
    return f"{base} | {suffix}" if base else suffix


def classify_historical_offer(offer: Any) -> str:
    """Classify one historical commerce_offers row (pure, no I/O).

    - ``full``: sealed envelope intact + purchased + transaction linked
      (selection and outcome provable; decision-time ranking data absent
      by construction and never fabricated).
    - ``partial``: sealed envelope intact but outcome non-terminal,
      non-purchase terminal, or purchase without a transaction link.
    - ``unattributed``: no sealed provenance (legacy rows, malformed
      envelopes, decline-overwritten rows).
    """
    if not isinstance(offer, dict):
        return "unattributed"
    provenance: Any = None
    reason = offer.get("reason")
    if isinstance(reason, str) and reason.strip():
        try:
            parsed = json.loads(reason)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            try:
                did = (
                    int(parsed.get("definition_id"))
                    if parsed.get("definition_id") is not None
                    else None
                )
                ver = (
                    int(parsed.get("definition_version"))
                    if parsed.get("definition_version") is not None
                    else None
                )
            except Exception:
                did, ver = None, None
            if did is not None and did > 0 and ver is not None and ver >= 1:
                provenance = parsed
    if provenance is None:
        return "unattributed"
    txn = offer.get("transaction_id")
    has_txn = isinstance(txn, str) and bool(txn.strip())
    if offer.get("state") == "purchased" and has_txn:
        return "full"
    return "partial"


def build_backfill_snapshot(offer: Any) -> dict[str, Any]:
    """Build a provable-fields-only snapshot for one historical row (pure).

    Contains identity and outcome facts only. Never fabricates eligible
    candidate sets, ranking factors, ranking policy, or selection
    history — those keys are absent by design, not empty.
    """
    if not isinstance(offer, dict):
        raise TypeError("offer is required")
    confidence = classify_historical_offer(offer)
    provenance: Any = None
    reason = offer.get("reason")
    if isinstance(reason, str) and reason.strip():
        try:
            parsed = json.loads(reason)
            provenance = parsed if isinstance(parsed, dict) else None
        except Exception:
            provenance = None
    created = _coerce_aware(offer.get("created_at"))
    purchased = _coerce_aware(offer.get("purchased_at"))
    return {
        "offer": {
            "id": offer.get("id"),
            "creator_id": offer.get("creator_id"),
            "user_id": offer.get("user_id"),
            "state": offer.get("state"),
            "price_minor": offer.get("price_minor"),
            "currency": offer.get("currency"),
            "drop_cuid": offer.get("dropfans_product_id"),
            "vault_item_ids": list(offer.get("vault_item_ids") or []),
            "transaction_id": offer.get("transaction_id"),
            "created_at": created.isoformat() if created else None,
            "purchased_at": purchased.isoformat() if purchased else None,
        },
        "provenance": provenance,
        "attribution_confidence": confidence,
    }


async def record_opportunity_decision(
    *,
    creator_id: int,
    user_id: int,
    generation_id: str | None,
    evaluated_at: datetime,
    opportunity_result: Any,
    conversation_state: Any = None,
) -> dict[str, Any] | None:
    """Persist one frozen opportunity decision (append-oriented, idempotent).

    Idempotent on ``(creator_id, generation_id)`` when generation_id is
    present: a retry returns the existing row without touching the frozen
    snapshot. Raises on invalid scope/timestamps and on database errors
    (callers isolate failures; the ledger must never break commerce).
    """
    creator_id = _require_scope("creator_id", creator_id)
    user_id = _require_scope("user_id", user_id)
    evaluated_at = _require_evaluated_at(evaluated_at)
    if opportunity_result is None:
        raise TypeError("opportunity_result is required")
    if generation_id is not None and (
        not isinstance(generation_id, str) or not generation_id.strip()
    ):
        raise ValueError("generation_id must be a non-empty string or None")
    decision_status, outcome_state, no_selection_reason = _decision_status_for(opportunity_result)
    snapshot = build_decision_snapshot(opportunity_result, conversation_state)
    selected = snapshot.get("selected") or {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        if generation_id is not None:
            row = await conn.fetchrow(
                """
                INSERT INTO commerce_opportunity_decisions
                    (creator_id, user_id, generation_id, evaluated_at,
                     decision_snapshot, selected_definition_id, selected_version,
                     selected_stable_key, no_selection_reason,
                     decision_status, outcome_state,
                     attribution_status, attribution_confidence)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11, 'unattributed', 'full')
                ON CONFLICT (creator_id, generation_id) WHERE generation_id IS NOT NULL
                DO NOTHING
                RETURNING *
                """,
                creator_id,
                user_id,
                generation_id,
                evaluated_at,
                json.dumps(snapshot, sort_keys=True),
                selected.get("definition_id"),
                selected.get("version"),
                selected.get("stable_key"),
                no_selection_reason,
                decision_status,
                outcome_state,
            )
            if row is None:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM commerce_opportunity_decisions
                    WHERE creator_id = $1 AND generation_id = $2
                    """,
                    creator_id,
                    generation_id,
                )
            return dict(row) if row else None
        row = await conn.fetchrow(
            """
            INSERT INTO commerce_opportunity_decisions
                (creator_id, user_id, generation_id, evaluated_at,
                 decision_snapshot, selected_definition_id, selected_version,
                 selected_stable_key, no_selection_reason,
                 decision_status, outcome_state,
                 attribution_status, attribution_confidence)
            VALUES ($1, $2, NULL, $3, $4::jsonb, $5, $6, $7, $8, $9, $10, 'unattributed', 'full')
            RETURNING *
            """,
            creator_id,
            user_id,
            evaluated_at,
            json.dumps(snapshot, sort_keys=True),
            selected.get("definition_id"),
            selected.get("version"),
            selected.get("stable_key"),
            no_selection_reason,
            decision_status,
            outcome_state,
        )
        return dict(row) if row else None


async def link_opportunity_seal(
    opportunity_id: int,
    *,
    seal_result: Any = None,
    drop_cuid: str | None = None,
) -> dict[str, Any] | None:
    """Link a sealing outcome to its decision row (idempotent, isolated).

    Sealed rows gain ``sealed_offer_id``/``drop_cuid``; failed seals record
    the existing seal vocabulary. When no seal was attempted, the reason
    is derived from the frozen selected snapshot (0/2+ mapped Drops) so
    the ``selected vs sealed`` funnel stays measurable. Never touches the
    decision snapshot. Returns None for unknown opportunity ids.
    """
    if (
        not isinstance(opportunity_id, int)
        or isinstance(opportunity_id, bool)
        or opportunity_id <= 0
    ):
        raise ValueError("opportunity_id is required")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM commerce_opportunity_decisions WHERE opportunity_id = $1",
            opportunity_id,
        )
        if row is None:
            return None
        current = dict(row)
        if current.get("sealed_offer_id") is not None:
            return current
        status = _get(seal_result, "status") if seal_result is not None else None
        if status == "SEALED":
            offer = _get(seal_result, "offer") or {}
            offer_id = offer.get("id") if isinstance(offer, dict) else _get(offer, "id")
            try:
                offer_id = int(offer_id) if offer_id is not None else None
            except Exception:
                offer_id = None
            cuid = drop_cuid
            if not isinstance(cuid, str) or not cuid.strip():
                raw = (
                    offer.get("dropfans_product_id")
                    if isinstance(offer, dict)
                    else _get(offer, "dropfans_product_id")
                )
                cuid = raw if isinstance(raw, str) and raw.strip() else None
            updated = await conn.fetchrow(
                """
                UPDATE commerce_opportunity_decisions
                SET decision_status = 'SEALED',
                    sealed_offer_id = $2,
                    drop_cuid = $3,
                    updated_at = NOW()
                WHERE opportunity_id = $1 AND sealed_offer_id IS NULL
                RETURNING *
                """,
                opportunity_id,
                offer_id,
                cuid,
            )
            return dict(updated) if updated else current
        if seal_result is not None:
            subreason = _get(seal_result, "subreason") or (
                str(status) if status else "SEAL_ATTEMPT_FAILED"
            )
            updated = await conn.fetchrow(
                """
                UPDATE commerce_opportunity_decisions
                SET decision_status = 'SEAL_FAILED',
                    seal_subreason = $2,
                    outcome_state = 'SEAL_FAILED',
                    outcome_at = NOW(),
                    updated_at = NOW()
                WHERE opportunity_id = $1 AND sealed_offer_id IS NULL
                RETURNING *
                """,
                opportunity_id,
                str(subreason),
            )
            return dict(updated) if updated else current
        # No seal attempted: derive the no-seal reason from frozen snapshot.
        mapped: list[Any] = []
        try:
            snapshot = current.get("decision_snapshot")
            parsed = json.loads(snapshot) if isinstance(snapshot, str) else (snapshot or {})
            mapped = ((parsed.get("selected") or {}).get("mapped_drop_ids")) or []
        except Exception:
            mapped = []
        try:
            count = len(list(mapped))
        except Exception:
            count = -1
        if count == 0:
            no_seal = "NO_MAPPED_DROP"
        elif count > 1:
            no_seal = "MULTIPLE_DROPS"
        else:
            no_seal = "SEAL_ATTEMPT_FAILED"
        updated = await conn.fetchrow(
            """
            UPDATE commerce_opportunity_decisions
            SET seal_subreason = $2,
                updated_at = NOW()
            WHERE opportunity_id = $1 AND sealed_offer_id IS NULL
            RETURNING *
            """,
            opportunity_id,
            no_seal,
        )
        return dict(updated) if updated else current


async def record_opportunity_send(
    opportunity_id: int,
    *,
    sealed_execution: Any = None,
) -> dict[str, Any] | None:
    """Record the send outcome for a sealed opportunity (isolated).

    EXECUTED or proven-delivered duplicates → SENT. In-flight duplicates
    (owned by another turn's send) are left untouched. Anything else →
    SEND_FAILED. Only transitions from PENDING, so a raced-in purchase is
    never overwritten. Returns None for unknown ids.

    P3.5.3B: durably records send-level exposure alongside the outcome
    (SENT → exposure SENT; SEND_FAILED → exposure SEND_ATTEMPTED; source
    ``sealed_execution``). Exposure columns are monotonic and never touched
    by terminal/purchase writers, so later outcomes cannot erase the fact
    that a send was recorded. SENT means recorded send/execution, not
    confirmed delivery or click.
    """
    if (
        not isinstance(opportunity_id, int)
        or isinstance(opportunity_id, bool)
        or opportunity_id <= 0
    ):
        raise ValueError("opportunity_id is required")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM commerce_opportunity_decisions WHERE opportunity_id = $1",
            opportunity_id,
        )
        if row is None:
            return None
        current = dict(row)
        if current.get("outcome_state") != "PENDING":
            return current
        exec_status = _get(sealed_execution, "status") if sealed_execution is not None else None
        handled = False
        if sealed_execution is not None:
            try:
                from commerce.opportunity_execution import is_sealed_ppv_handled as _handled

                handled = bool(_handled(sealed_execution))
            except Exception:
                handled = exec_status == "EXECUTED"
        if handled:
            new_state = "SENT"
            new_exposure = "SENT"
        elif exec_status == "ALREADY_ENQUEUED":
            return current
        else:
            new_state = "SEND_FAILED"
            new_exposure = "SEND_ATTEMPTED"
        updated = await conn.fetchrow(
            """
            UPDATE commerce_opportunity_decisions
            SET outcome_state = $2,
                outcome_at = NOW(),
                exposure_state = $3,
                exposure_at = NOW(),
                exposure_source = 'sealed_execution',
                updated_at = NOW()
            WHERE opportunity_id = $1 AND outcome_state = 'PENDING'
            RETURNING *
            """,
            opportunity_id,
            new_state,
            new_exposure,
        )
        return dict(updated) if updated else current


async def record_purchase_by_offer(
    *,
    creator_id: int,
    offer_id: int,
    transaction_id: str,
    price_minor: int | None = None,
    currency: str | None = None,
    purchased_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Resolve a purchase to its opportunity row via the sealed offer link.

    Updates exactly one original opportunity row first — the lowest
    eligible ``opportunity_id`` for the creator+offer (deterministic
    single-winner; revenue is attributed once, to the original decision).
    Falls back to the earliest re-engagement touch only when no original
    row exists (legacy offers). Duplicate webhooks return the existing
    row unchanged. Ambiguous or unlinked offers return None without
    claiming anything. Provider/local clock skew never rejects a valid
    purchase.
    """
    creator_id = _require_scope("creator_id", creator_id)
    if not isinstance(offer_id, int) or isinstance(offer_id, bool) or offer_id <= 0:
        raise ValueError("offer_id is required")
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        raise ValueError("transaction_id is required")
    if isinstance(price_minor, bool) or (
        price_minor is not None and not isinstance(price_minor, int)
    ):
        raise ValueError("price_minor must be an integer or None")
    outcome_at = _coerce_aware(purchased_at) or datetime.now(UTC)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # P3.5.2.0 single-winner: one sealed offer may produce at most one
        # PURCHASED ledger row. The subselect deterministically picks the
        # lowest eligible original opportunity_id; the outer UPDATE then
        # touches at most that one row (opportunity_id is the PK).
        updated = await conn.fetchrow(
            """
            UPDATE commerce_opportunity_decisions
            SET outcome_state = 'PURCHASED',
                outcome_at = $3,
                transaction_id = $4,
                purchased_price_minor = $5,
                purchased_currency = $6,
                attribution_status = 'attributed',
                updated_at = NOW()
            WHERE opportunity_id = (
                SELECT opportunity_id FROM commerce_opportunity_decisions
                WHERE creator_id = $1
                  AND sealed_offer_id = $2
                  AND outcome_state IN ('PENDING', 'SENT', 'SEND_FAILED')
                  AND reengagement_of IS NULL
                ORDER BY opportunity_id ASC
                LIMIT 1
            )
            RETURNING *
            """,
            creator_id,
            offer_id,
            outcome_at,
            transaction_id.strip(),
            price_minor,
            currency,
        )
        if updated is not None:
            return dict(updated)
        # Fallback: earliest re-engagement touch when no original row exists.
        updated = await conn.fetchrow(
            """
            UPDATE commerce_opportunity_decisions
            SET outcome_state = 'PURCHASED',
                outcome_at = $3,
                transaction_id = $4,
                purchased_price_minor = $5,
                purchased_currency = $6,
                attribution_status = 'attributed',
                updated_at = NOW()
            WHERE opportunity_id = (
                SELECT opportunity_id FROM commerce_opportunity_decisions
                WHERE creator_id = $1
                  AND sealed_offer_id = $2
                  AND outcome_state IN ('PENDING', 'SENT', 'SEND_FAILED')
                ORDER BY opportunity_id ASC
                LIMIT 1
            )
            RETURNING *
            """,
            creator_id,
            offer_id,
            outcome_at,
            transaction_id.strip(),
            price_minor,
            currency,
        )
        if updated is not None:
            return dict(updated)
        existing = await conn.fetchrow(
            """
            SELECT * FROM commerce_opportunity_decisions
            WHERE creator_id = $1 AND sealed_offer_id = $2 AND transaction_id = $3
            ORDER BY opportunity_id ASC
            LIMIT 1
            """,
            creator_id,
            offer_id,
            transaction_id.strip(),
        )
        return dict(existing) if existing else None


async def record_offer_terminal_outcome(
    *,
    creator_id: int,
    offer_id: int,
    outcome_state: str,
) -> list[dict[str, Any]]:
    """Mark non-purchase terminal outcomes for an offer's rows (isolated).

    Only transitions non-terminal rows (PENDING/SENT/SEND_FAILED); terminal
    rows are never overwritten. Purchases must use
    :func:`record_purchase_by_offer`. Returns the updated rows.
    """
    creator_id = _require_scope("creator_id", creator_id)
    if not isinstance(offer_id, int) or isinstance(offer_id, bool) or offer_id <= 0:
        raise ValueError("offer_id is required")
    if outcome_state not in ("DECLINED", "EXPIRED", "REVOKED", "CLICKED_NO_PURCHASE"):
        raise ValueError(f"outcome_state not terminal-non-purchase: {outcome_state!r}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE commerce_opportunity_decisions
            SET outcome_state = $3,
                outcome_at = NOW(),
                updated_at = NOW()
            WHERE creator_id = $1
              AND sealed_offer_id = $2
              AND outcome_state IN ('PENDING', 'SENT', 'SEND_FAILED')
            RETURNING *
            """,
            creator_id,
            offer_id,
            outcome_state,
        )
        return [dict(r) for r in (rows or [])]


async def record_reengagement_touch(
    *,
    creator_id: int,
    user_id: int,
    offer: Any,
    dedup_key: str | None = None,
) -> dict[str, Any] | None:
    """Record a scheduler re-engagement touch as a linked child row.

    Never creates a commerce offer and never re-runs the engine: the
    stale offer row is the identity. Links to the original opportunity
    row when one exists (NULL parent for pre-ledger offers — recorded
    honestly, never fabricated). Idempotent per (creator, fan, offer):
    repeated scheduler passes return the existing touch. No revenue is
    ever recorded here; purchases resolve to the original row.
    """
    creator_id = _require_scope("creator_id", creator_id)
    user_id = _require_scope("user_id", user_id)
    if not isinstance(offer, dict):
        raise TypeError("offer is required")
    try:
        offer_creator = offer.get("creator_id")
        offer_user = offer.get("user_id")
        offer_id = int(offer.get("id"))
    except Exception:
        raise ValueError("offer id is required")
    if offer_creator != creator_id or offer_user != user_id or offer_id <= 0:
        raise ValueError("offer scope mismatch")
    if dedup_key is not None and (not isinstance(dedup_key, str) or not dedup_key.strip()):
        raise ValueError("dedup_key must be a non-empty string or None")
    product_id = offer.get("product_id")
    derived_dedup = dedup_key or (
        f"reengage:{creator_id}:{user_id}:{product_id}" if isinstance(product_id, int) else None
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        parent = await conn.fetchrow(
            """
            SELECT opportunity_id, selected_definition_id, selected_version, selected_stable_key
            FROM commerce_opportunity_decisions
            WHERE creator_id = $1 AND sealed_offer_id = $2 AND reengagement_of IS NULL
            ORDER BY opportunity_id ASC
            LIMIT 1
            """,
            creator_id,
            offer_id,
        )
        parent_id = _col(parent, "opportunity_id") if parent else None
        snapshot = {
            "reengaged_offer_id": offer_id,
            "dedup_key": derived_dedup,
            "drop_cuid": offer.get("dropfans_product_id"),
            "vault_item_ids": list(offer.get("vault_item_ids") or []),
            "parent_selected": (
                {
                    "definition_id": _col(parent, "selected_definition_id"),
                    "version": _col(parent, "selected_version"),
                    "stable_key": _col(parent, "selected_stable_key"),
                }
                if parent
                else None
            ),
        }
        row = await conn.fetchrow(
            """
            INSERT INTO commerce_opportunity_decisions
                (creator_id, user_id, generation_id, evaluated_at,
                 decision_snapshot, selected_definition_id, selected_version,
                 selected_stable_key, decision_status, sealed_offer_id,
                 outcome_state, outcome_at, attribution_status,
                 attribution_confidence, reengagement_of)
            VALUES ($1, $2, NULL, NOW(), $3::jsonb, $4, $5, $6,
                    'REENGAGED', $7, 'REENGAGED', NOW(),
                    'unattributed', $8, $9)
            ON CONFLICT (creator_id, user_id, sealed_offer_id)
                WHERE generation_id IS NULL
            DO NOTHING
            RETURNING *
            """,
            creator_id,
            user_id,
            json.dumps(snapshot, sort_keys=True),
            _col(parent, "selected_definition_id") if parent else None,
            _col(parent, "selected_version") if parent else None,
            _col(parent, "selected_stable_key") if parent else None,
            offer_id,
            "partial" if parent else "unattributed",
            parent_id,
        )
        if row is None:
            row = await conn.fetchrow(
                """
                SELECT * FROM commerce_opportunity_decisions
                WHERE creator_id = $1 AND user_id = $2 AND sealed_offer_id = $3
                  AND generation_id IS NULL
                """,
                creator_id,
                user_id,
                offer_id,
            )
        return dict(row) if row else None
