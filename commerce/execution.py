"""Phase 5.3C — deterministic PPV commerce execution (Dropfans provider).

Execution is the gated activation of a creator-scoped ``commerce_offers`` row
carrying the AUTHORITATIVE Dropfans drop link + price. The fan pays through
the official Dropfans buy URL and purchase confirmation arrives via the
existing polling + reconciliation path.

Dropfans is the sole active commerce provider.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from commerce.dao import (
    create_offer_serialized,
    find_pending_offer_for_product,
    has_purchased_product,
    record_offer_transition,
)
from commerce.decision import CommerceDecision
from commerce.eligibility import (
    OfferContext,
    ProductEligibilityState,
    UserEligibilityState,
    evaluate_ppv_eligibility,
)
from commerce.models import CommerceAction
from db import dropfans as ddb
from core.event_bus import publish_event
from db.postgres import get_user
from integrations.dropfans.errors import DropfansError
from integrations.dropfans.security import decrypt_secret
from integrations.dropfans import service as dservice

logger = logging.getLogger("commerce.execution")


# ── P3.2C F1: commerce schema readiness gate ─────────────────────────────
# No autonomous commerce execution may occur unless the P3.2 commerce safety
# schema is verified PRESENT. ABSENT and UNKNOWN (e.g. DB unreachable) both
# refuse execution — unknown is never treated as healthy. Results are cached
# briefly so per-message commerce attempts do not re-probe the schema.

_COMMERCE_SCHEMA_GATE_TTL_S = 60.0
_commerce_schema_gate: dict[str, Any] = {
    "ready": None,
    "reason": "commerce_schema_unverified",
    "checked_at": 0.0,
}


async def check_commerce_schema_ready(*, force: bool = False) -> tuple[bool, str]:
    """Verify the P3.2 commerce safety schema. Never raises.

    Returns ``(ready, reason)`` where reason is one of
    ``commerce_schema_present`` / ``commerce_schema_absent`` /
    ``commerce_schema_unknown``. Connectivity failures yield ``unknown``
    (not ready), never healthy.
    """
    now = datetime.now(UTC).timestamp()
    _cached_ready = _commerce_schema_gate["ready"]
    _cache_fresh = now - float(_commerce_schema_gate["checked_at"]) < _COMMERCE_SCHEMA_GATE_TTL_S
    if not force and _cached_ready is not None and _cache_fresh:
        return bool(_cached_ready), str(_commerce_schema_gate["reason"])
    try:
        from db.postgres import verify_commerce_safety_schema

        result = await verify_commerce_safety_schema()
    except Exception as exc:  # noqa: BLE001 — verification itself failed
        ready, reason = False, "commerce_schema_unknown"
        logger.warning(
            "commerce schema gate: verification failed (%s) — commerce refused",
            exc.__class__.__name__,
        )
    else:
        if result.get("present") is True:
            ready, reason = True, "commerce_schema_present"
        elif result.get("unknown"):
            ready, reason = False, "commerce_schema_unknown"
            logger.warning("commerce schema gate: schema state unknown — commerce refused")
        else:
            ready, reason = False, "commerce_schema_absent"
            logger.warning(
                "commerce schema gate: P3.2 schema absent (missing=%s) — commerce refused",
                result.get("missing"),
            )
    _commerce_schema_gate.update({"ready": ready, "reason": reason, "checked_at": now})
    return ready, reason


class ExecutionStatus(str, Enum):
    """Stable Phase 5.3C result states."""

    EXECUTED = "executed"
    ALREADY_EXECUTED = "already_executed"
    DENIED = "denied"
    PRODUCT_UNAVAILABLE = "product_unavailable"
    ELIGIBILITY_DENIED = "eligibility_denied"
    CREATOR_NOT_READY = "creator_not_ready"
    PROVIDER_ERROR = "provider_error"
    PERSISTENCE_FAILED = "persistence_failed"
    EXECUTION_CONFLICT = "execution_conflict"
    REQUIRES_MANUAL_REVIEW = "requires_manual_review"


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of one PPV execution attempt."""

    status: ExecutionStatus
    offer_id: int | None = None
    offer_state: str | None = None
    denial_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def created(self) -> bool:
        return self.status is ExecutionStatus.EXECUTED


def _result(
    status: ExecutionStatus,
    *,
    offer_id: int | None = None,
    offer_state: str | None = None,
    denial_reason: str | None = None,
    **metadata: Any,
) -> ExecutionResult:
    return ExecutionResult(
        status=status,
        offer_id=offer_id,
        offer_state=offer_state,
        denial_reason=denial_reason,
        metadata=dict(metadata),
    )


async def execute_ppv(
    *,
    creator_id: int,
    user_id: int,
    product_id: int,
    decision: CommerceDecision,
    created_by: str,
    age_verified: bool = False,
) -> ExecutionResult:
    """Gate and execute one deterministic PPV offer activation.

    All authority conditions are re-evaluated immediately before any side effect.
    Dropfans is the sole active commerce provider.

    P3.2C F1: the P3.2 commerce safety schema must verify PRESENT before any
    commerce side effect. ABSENT or UNKNOWN refuses with DENIED.
    """
    # 0. P3.2 commerce schema readiness (fail closed on absent AND unknown).
    _schema_ready, _schema_reason = await check_commerce_schema_ready()
    if not _schema_ready:
        return _result(
            ExecutionStatus.DENIED,
            denial_reason=_schema_reason,
        )

    # 1. Decision authority
    if decision.action is not CommerceAction.OFFER_PPV or not decision.allowed:
        return _result(
            ExecutionStatus.DENIED,
            denial_reason="decision_not_authorized",
            action=decision.action.value,
        )

    # 2. Creator and integration authority (Dropfans only)
    try:
        integration = await ddb.get_dropfans_integration(creator_id)
    except Exception:
        logger.warning(
            "commerce.execution dropfans integration lookup failed",
            extra={"creator_id": creator_id, "product_id": product_id},
            exc_info=True,
        )
        integration = None
    if integration is None or integration.get("status") != "active":
        return _result(
            ExecutionStatus.CREATOR_NOT_READY,
            denial_reason="integration_not_ready",
            integration_status=integration.get("status") if integration else None,
        )

    # 3. Vault credential authority (Fernet). Decryptable now, or refuse.
    try:
        decrypt_secret(integration["encrypted_api_key"])
    except Exception:  # noqa: BLE001
        logger.warning(
            "commerce.execution credential unavailable",
            extra={"creator_id": creator_id, "product_id": product_id},
        )
        return _result(
            ExecutionStatus.CREATOR_NOT_READY,
            denial_reason="credential_unavailable",
        )

    # 4. Fan authority (blocked / opted out / unknown).
    fan = await get_user(user_id)
    if fan is None:
        return _result(
            ExecutionStatus.ELIGIBILITY_DENIED,
            denial_reason="user_unknown",
        )
    user_state = UserEligibilityState(
        is_blocked=bool(fan.get("is_blocked", False)),
        do_not_auto_reply=bool(fan.get("do_not_auto_reply", False)),
    )

    # Fast-path: blocked/opted-out users cannot receive offers at all.
    if user_state.is_blocked:
        return _result(
            ExecutionStatus.ELIGIBILITY_DENIED,
            denial_reason="user_blocked",
        )
    if user_state.do_not_auto_reply:
        return _result(
            ExecutionStatus.ELIGIBILITY_DENIED,
            denial_reason="user_opted_out",
        )

    # 5. Local product authority (provider-neutral product mirror).
    from db.postgres import get_pool
    pool = await get_pool()
    try:
        local_product_row = await pool.fetchrow(
            "SELECT * FROM fangate_products WHERE creator_id = $1 AND id = $2",
            creator_id,
            product_id,
        )
    except Exception:
        logger.warning(
            "commerce.execution product lookup failed",
            extra={"creator_id": creator_id, "product_id": product_id},
        )
        return _result(
            ExecutionStatus.PRODUCT_UNAVAILABLE,
            denial_reason="product_lookup_failed",
        )
    if local_product_row is None:
        return _result(
            ExecutionStatus.PRODUCT_UNAVAILABLE,
            denial_reason="product_missing",
        )
    local_product = dict(local_product_row)

    # Extract Dropfans product ID from canonical column first, then raw JSONB.
    # The canonical column is creator-scoped (P3.2); raw is the legacy source.
    import json
    raw = local_product.get("raw", {})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = {}
    dropfans_product_id = local_product.get("dropfans_product_id") or raw.get("dropfans_product_id")
    if not dropfans_product_id:
        return _result(
            ExecutionStatus.PRODUCT_UNAVAILABLE,
            denial_reason="no_dropfans_product_id",
        )
    dropfans_product_id = str(dropfans_product_id)

    # P3.2 offer-time content snapshot: canonical Vault set from the mirror.
    # Fail closed when membership cannot be established reliably — never
    # create an apparently valid offer with an empty/missing snapshot while
    # the Drop is supposed to have known Vault membership.
    from commerce.vault_sets import drop_content_hash, presentation_ids
    _snapshot_ids: list[str] | None = None
    try:
        _raw_ids = raw.get("vaultItemIds") or raw.get("vault_item_ids") or []
        _snapshot_ids = presentation_ids(_raw_ids)
    except Exception:
        _snapshot_ids = None
    if not _snapshot_ids:
        return _result(
            ExecutionStatus.PRODUCT_UNAVAILABLE,
            denial_reason="no_vault_snapshot",
        )
    try:
        _snapshot_hash = drop_content_hash(_snapshot_ids)
    except Exception:
        return _result(
            ExecutionStatus.PRODUCT_UNAVAILABLE,
            denial_reason="no_vault_snapshot",
        )
    _snapshot_count = len(_snapshot_ids)

    # 5b. LIVE PRICE VERIFICATION (Phase 93D) — Drop live price is monetary authority
    # fangate_products.price_minor is cache; commerce_offers.price_minor is immutable snapshot after verification.
    # Verification MUST happen before fan-visible offer. Failure → no stale PPV (fail closed).
    # Uses existing DropFans client + retry, creator-isolated Bearer key, bounded retries, no key logging.
    _local_price_minor = local_product.get("price_minor")
    _live_price_minor: int | None = None
    _live_currency: str | None = None
    _verification_failure: str | None = None
    try:
        # Prefer helper that also validates response shape; re-use service.get_drop (wraps client.get_drop with audited retries)
        _drop_live = await dservice.get_drop(creator_id, str(dropfans_product_id))
        _live_price = _drop_live.get("price")
        _live_currency = _drop_live.get("currency") or "USD"
        # Currency must be USD per DropFans docs
        if _live_currency != "USD":
            _verification_failure = f"unsupported_currency:{_live_currency}"
        elif _live_price is None:
            _verification_failure = "missing_live_price"
        else:
            # Deterministic conversion USD dollars → cents via Decimal (avoid float)
            try:
                from decimal import Decimal, InvalidOperation
                _dec_price = Decimal(str(_live_price))
                # DropFans allows 0 or 5-750
                if _dec_price < 0 or (_dec_price != 0 and _dec_price < Decimal("5")) or _dec_price > Decimal("750"):
                    _verification_failure = f"price_out_of_range:{_live_price}"
                else:
                    _live_price_minor = int((_dec_price * Decimal(100)).to_integral_value())
            except (InvalidOperation, ValueError, TypeError) as _e:
                _verification_failure = f"invalid_price:{_live_price}"
        if _verification_failure is None and _live_price_minor is not None:
            # Cache refresh: update fangate_products.price_minor to live verified value if stale
            # Preserve existing schema; synced_at updated to NOW()
            if _local_price_minor != _live_price_minor:
                try:
                    # Use same pool as above (still open) — update mirror
                    await pool.execute(
                        "UPDATE fangate_products SET price_minor=$1, synced_at=NOW(), updated_at=NOW() WHERE id=$2 AND creator_id=$3",
                        _live_price_minor,
                        product_id,
                        creator_id,
                    )
                    # Update local_product dict for downstream eligibility/link logic to use verified price
                    local_product["price_minor"] = _live_price_minor
                    logger.info(
                        "price verification: refreshed stale cache creator=%s product=%s local=%s live=%s",
                        creator_id, product_id, _local_price_minor, _live_price_minor,
                    )
                except Exception:
                    logger.warning("price cache refresh failed creator=%s product=%s", creator_id, product_id, exc_info=True)
                    # Non-blocking: continue with live price for this offer even if mirror update failed
            # Telemetry (best-effort, never logs keys/URLs)
            try:
                from core.telemetry import get_telemetry_collector as _tel
                _t = _tel()
                # Use existing generation telemetry if available via context var? Best-effort via event
                await publish_event(
                    "commerce.price_verified",
                    {
                        "product_id": product_id,
                        "local_price_minor": _local_price_minor,
                        "live_price_minor": _live_price_minor,
                        "price_delta_minor": (_live_price_minor - _local_price_minor) if isinstance(_local_price_minor, int) and isinstance(_live_price_minor, int) else None,
                        "currency": _live_currency,
                        "verification_result": "ok",
                    },
                    user_id=user_id,
                    creator_id=creator_id,
                    scope="user",
                )
            except Exception:
                pass
    except Exception as _exc:
        # Network/timeout/429 after retries/500/404/malformed
        _verification_failure = _exc.__class__.__name__ + ":" + str(_exc)[:120]
        logger.warning("live price verification failed creator=%s product=%s drop=%s: %s", creator_id, product_id, dropfans_product_id, _exc)

    if _verification_failure is not None or _live_price_minor is None:
        # Fail closed: no stale offer using local cache
        try:
            await publish_event(
                "commerce.price_verification_failed",
                {
                    "product_id": product_id,
                    "local_price_minor": _local_price_minor,
                    "failure_reason": _verification_failure,
                },
                user_id=user_id,
                creator_id=creator_id,
                scope="user",
            )
        except Exception:
            pass
        return _result(
            ExecutionStatus.PROVIDER_ERROR,
            denial_reason="price_verification_failed",
            verification_failure=_verification_failure,
            local_price_minor=_local_price_minor,
        )

    # 6. Existing-offer + purchase authority (pre-reservation re-check)
    # If existing pending has stale price differing from live, expire it before creating fresh live-price offer
    existing = await find_pending_offer_for_product(creator_id, user_id, product_id)
    if existing is not None:
        # Stale pending price handling: do not mutate historical offer, but if pending stale vs live, expire and allow fresh
        _existing_price = existing.get("price_minor")
        if _existing_price is not None and _existing_price != _live_price_minor and existing.get("state") == "pending":
            try:
                from commerce.dao import expire_pending_offer_if_still_pending
                await expire_pending_offer_if_still_pending(creator_id, existing["id"])
                logger.info("expired stale pending offer %s price %s vs live %s", existing["id"], _existing_price, _live_price_minor)
                # Re-check after expiry - fall through to create new
                existing = None
            except Exception:
                logger.warning("failed to expire stale pending offer %s", existing.get("id"), exc_info=True)
                return _result(
                    ExecutionStatus.ALREADY_EXECUTED,
                    offer_id=existing["id"],
                    offer_state=existing["state"],
                )
        if existing is not None:
            return _result(
                ExecutionStatus.ALREADY_EXECUTED,
                offer_id=existing["id"],
                offer_state=existing["state"],
            )
    if await has_purchased_product(creator_id, user_id, product_id):
        return _result(
            ExecutionStatus.ELIGIBILITY_DENIED,
            denial_reason="already_purchased",
        )

    # 7. Deterministic eligibility re-evaluation (first denial wins).
    # Use verified live price for eligibility (is_accessible check unchanged, price used for allow)
    verdict = evaluate_ppv_eligibility(
        user_state,
        ProductEligibilityState(
            is_accessible=bool(local_product.get("is_accessible", True)),
            sales_url=local_product.get("sales_url"),
            price_minor=_live_price_minor,
        ),
        OfferContext(
            creator_ready=True,
            has_active_offer=False,
            already_purchased=False,
            enforce_age_verification=False,
            age_verified=bool(age_verified),
        ),
    )
    if not verdict.allowed:
        return _result(
            ExecutionStatus.ELIGIBILITY_DENIED,
            denial_reason=verdict.denial_reason,
        )

    # 8. Checkout link resolution (Dropfans canonical template).
    remote_link = local_product.get("sales_url")
    if not remote_link:
        try:
            remote_link = await dservice.build_checkout_url(creator_id, dropfans_product_id)
        except DropfansError:
            logger.warning("Failed to build checkout URL", extra={"creator_id": creator_id})
            return _result(
                ExecutionStatus.PROVIDER_ERROR,
                denial_reason="checkout_link_unavailable",
            )

    # P3.2 price discipline: persist the live-verified execution-time price,
    # never the (possibly stale) mirror cache. If the mirror refresh above
    # failed, local_product may still hold the stale value — ignore it here.
    remote_price = _live_price_minor
    offer_currency = _live_currency or "USD"

    # 9. Consistency check: refuse rather than guess.
    mismatches: list[str] = []
    if not remote_link:
        mismatches.append("link_missing")
    if mismatches:
        return _result(
            ExecutionStatus.PRODUCT_UNAVAILABLE,
            denial_reason="product_inconsistent",
            mismatches=mismatches,
        )

    # 10. Serialized idempotency reservation + INSERT.
    # Snapshot (CUID + Vault IDs + count + hash) is written atomically with
    # the row; price is the live-verified execution-time value.
    try:
        offer_row, created = await create_offer_serialized(
            creator_id=creator_id,
            user_id=user_id,
            product_id=product_id,
            link=remote_link,
            price_minor=remote_price,
            currency=offer_currency,
            reason="ppv_execution",
            created_by=created_by,
            dropfans_product_id=dropfans_product_id,
            vault_item_ids=_snapshot_ids,
            media_count=_snapshot_count,
            drop_content_hash=_snapshot_hash,
        )
    except Exception as exc:  # noqa: BLE001
        recovered = await find_pending_offer_for_product(creator_id, user_id, product_id)
        if recovered is not None:
            return _result(
                ExecutionStatus.ALREADY_EXECUTED,
                offer_id=recovered["id"],
                offer_state=recovered["state"],
                recovered_after="persistence_ambiguity",
            )
        return _result(
            ExecutionStatus.PERSISTENCE_FAILED,
            denial_reason=exc.__class__.__name__,
        )
    if not created:
        return _result(
            ExecutionStatus.ALREADY_EXECUTED,
            offer_id=offer_row["id"],
            offer_state=offer_row["state"],
        )
    # 11. Best-effort funnel rollup
    try:
        await record_offer_transition(
            creator_id=creator_id,
            product_id=product_id,
            state="pending",
            day=datetime.now(UTC).date(),
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "commerce.execution rollup failed (offer stands)",
            extra={"creator_id": creator_id, "product_id": product_id},
        )

    # 12. Observability: commerce.offer_created (best-effort, never blocks)
    try:
        await publish_event(
            "commerce.offer_created",
            {
                "offer_id": offer_row["id"],
                "product_id": product_id,
                "price_minor": remote_price,
                "currency": offer_currency,
                "dropfans_product_id": dropfans_product_id,
                "media_count": _snapshot_count,
                "drop_content_hash": _snapshot_hash,
            },
            user_id=user_id,
            dialog_id=user_id,
            creator_id=creator_id,
            scope="user",
        )
    except Exception:
        logger.debug("commerce.offer_created publish failed", exc_info=True)

    return _result(
        ExecutionStatus.EXECUTED,
        offer_id=offer_row["id"],
        offer_state=offer_row["state"],
        verified_price_minor=remote_price,
        verified_currency=offer_currency,
        verified_sales_url=remote_link,
        dropfans_product_id=dropfans_product_id,
        media_count=_snapshot_count,
        drop_content_hash=_snapshot_hash,
    )
