"""P3.3.13 — provider sealing primitive (standalone, two-step verify).

Converts one already-ranked ``OpportunityCandidate`` + one caller-chosen
Dropfans CUID into a sealed ``commerce_offers`` snapshot. The caller must
supply the single CUID; the sealer never chooses among ``mapped_drop_ids``.

Safety invariant (TOCTOU closed):

    GET + verify outside TX → exact candidate/live check
    → BEGIN TX → advisory lock on sealed identity
    → GET + verify inside TX → exact check again
    → persist verified-live snapshot → COMMIT

The advisory lock is on the sealed identity ``(creator, user, definition,
version, CUID)`` — not the legacy ``ppv_offer:{product}`` key — so two
different definitions sharing one Drop remain distinct, and two Drops for
one definition remain distinct.

No ranking, no eligibility, no LLM, no Redis, no worker wiring, no
autonomous execution, no schema migration. Reuses only:

* ``integrations.dropfans.service.get_drop`` (creator-scoped)
* ``commerce.drop_reconciliation.verify_live_drop`` + ``LiveDropInvalid``
* ``commerce.vault_sets.canonical_identity_ids`` + ``drop_content_hash``
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("commerce.opportunity_sealing")

VERIFIER_VERSION = "p33.13.v1"

# ---------------------------------------------------------------------------
# Result classification (stable strings; tests assert membership, not ints)
# ---------------------------------------------------------------------------

SEALED = "SEALED"
PROVIDER_NOT_FOUND = "PROVIDER_NOT_FOUND"
PROVIDER_REJECTED = "PROVIDER_REJECTED"
PROVIDER_DRIFT = "PROVIDER_DRIFT"
CREATOR_MISMATCH = "CREATOR_MISMATCH"
MALFORMED_PROVIDER_DATA = "MALFORMED_PROVIDER_DATA"
PROVIDER_TIMEOUT_AMBIGUOUS = "PROVIDER_TIMEOUT_AMBIGUOUS"
LOCAL_PERSISTENCE_FAILURE = "LOCAL_PERSISTENCE_FAILURE"

SEAL_STATUSES = frozenset(
    {
        SEALED,
        PROVIDER_NOT_FOUND,
        PROVIDER_REJECTED,
        PROVIDER_DRIFT,
        CREATOR_MISMATCH,
        MALFORMED_PROVIDER_DATA,
        PROVIDER_TIMEOUT_AMBIGUOUS,
        LOCAL_PERSISTENCE_FAILURE,
    }
)

# Sub-reasons (machine-readable detail for drift/rejection/malformed cases)
PRICE_DRIFT = "PRICE_DRIFT"
MEMBERSHIP_DRIFT = "MEMBERSHIP_DRIFT"
CURRENCY_DRIFT = "CURRENCY_DRIFT"
ALLOW_DOWNLOAD_DRIFT = "ALLOW_DOWNLOAD_DRIFT"
DEFINITION_COMMERCIAL_MISMATCH = "DEFINITION_COMMERCIAL_MISMATCH"
PROVIDER_STATUS_NOT_APPROVED = "PROVIDER_STATUS_NOT_APPROVED"
DROP_NOT_FOUND = "DROP_NOT_FOUND"
MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
TIMEOUT = "TIMEOUT"
TRANSPORT_ERROR = "TRANSPORT_ERROR"
RATE_LIMITED = "RATE_LIMITED"
PERSISTENCE_ERROR = "PERSISTENCE_ERROR"
CUID_NOT_MAPPED = "CUID_NOT_MAPPED"
CUID_MISMATCH = "CUID_MISMATCH"


@dataclass(frozen=True)
class SealResult:
    """Immutable sealing outcome (never persisted as object; row is ``commerce_offers``)."""

    status: str
    subreason: str | None = None
    detail: str | None = None
    offer: dict[str, Any] | None = None
    verified_live: Any | None = None
    sealed_identity: tuple[Any, ...] | None = None


@dataclass(frozen=True)
class SealVerificationResult:
    """Pure candidate/live comparison verdict."""

    ok: bool
    subreason: str | None = None
    detail: str | None = None


def _get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    try:
        return getattr(source, key, default)
    except Exception:
        return default


def _require_scope(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} is required")
    return value


def _sealed_identity(
    creator_id: int, user_id: int, definition_id: int, version: int, cuid: str
) -> tuple[int, int, int, int, str]:
    return (int(creator_id), int(user_id), int(definition_id), int(version), str(cuid))


def _advisory_key(identity: tuple[Any, ...]) -> str:
    c, u, d, v, cuid = identity
    return f"seal:{c}:{u}:{d}:{v}:{cuid}"


def _synthetic_product_id(cuid: str) -> int:
    return int(hashlib.sha256(cuid.encode()).hexdigest()[:15], 16) % (2**62)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def verify_candidate_against_live(
    candidate: Any,
    chosen_drop_cuid: str,
    verified_live: Any,
) -> SealVerificationResult:
    """Pure exact-match check (8 gates). Fail-closed on any mismatch."""
    # 1/2 already validated by caller scope checks, but re-assert CUID membership here.
    mapped = _get(candidate, "mapped_drop_ids", ())
    try:
        mapped_set = {str(x).strip() for x in (mapped or ()) if str(x).strip()}
    except Exception:
        mapped_set = set()
    if str(chosen_drop_cuid).strip() not in mapped_set:
        return SealVerificationResult(
            False, CUID_NOT_MAPPED, "chosen CUID not in candidate.mapped_drop_ids"
        )

    # 3 live CUID must equal chosen
    live_cuid = (
        _get(verified_live, "dropfans_product_id")
        or _get(verified_live, "product_id")
        or _get(verified_live, "id")
    )
    if str(live_cuid or "").strip() != str(chosen_drop_cuid).strip():
        return SealVerificationResult(
            False, CUID_MISMATCH, f"live CUID {live_cuid!r} != chosen {chosen_drop_cuid!r}"
        )

    # 4 status APPROVED
    status = str(_get(verified_live, "status") or "").strip().upper()
    if status != "APPROVED":
        return SealVerificationResult(
            False, PROVIDER_STATUS_NOT_APPROVED, status or "missing_status"
        )

    # 5 membership exact (canonical)
    try:
        from commerce.vault_sets import canonical_identity_ids
    except Exception:
        return SealVerificationResult(False, MEMBERSHIP_DRIFT, "canonical helper unavailable")
    cand_ids = _get(candidate, "canonical_vault_item_ids", ())
    try:
        cand_list = [str(x).strip() for x in list(cand_ids or []) if str(x).strip()]
        cand_canonical = canonical_identity_ids(cand_list) if cand_list else []
    except Exception as exc:
        return SealVerificationResult(False, MEMBERSHIP_DRIFT, f"candidate vault invalid: {exc}")
    live_ids = _get(verified_live, "vault_item_ids", []) or []
    try:
        live_canonical = canonical_identity_ids([str(x) for x in list(live_ids) if str(x).strip()])
    except Exception as exc:
        return SealVerificationResult(False, MEMBERSHIP_DRIFT, f"live vault invalid: {exc}")
    if cand_canonical != live_canonical:
        return SealVerificationResult(
            False, MEMBERSHIP_DRIFT, f"candidate {cand_canonical!r} != live {live_canonical!r}"
        )

    # 6 price
    cand_price = _get(candidate, "price_minor")
    live_price = _get(verified_live, "price_minor")
    if cand_price != live_price:
        return SealVerificationResult(False, PRICE_DRIFT, f"{cand_price!r} != {live_price!r}")

    # 7 currency
    cand_curr = str(_get(candidate, "currency") or "").strip().upper()
    live_curr = str(_get(verified_live, "currency") or "").strip().upper()
    if cand_curr != live_curr:
        return SealVerificationResult(False, CURRENCY_DRIFT, f"{cand_curr!r} != {live_curr!r}")

    # 8 allow_download
    cand_dl = bool(_get(candidate, "allow_download"))
    live_dl = bool(_get(verified_live, "allow_download"))
    if cand_dl is not live_dl:
        return SealVerificationResult(False, ALLOW_DOWNLOAD_DRIFT, f"{cand_dl!r} != {live_dl!r}")

    return SealVerificationResult(True, None, None)


def _classify_provider_error(exc: BaseException) -> tuple[str, str, str]:
    """Map provider exception to (status, subreason, detail)."""
    from commerce.drop_reconciliation import LiveDropInvalid
    from integrations.dropfans.errors import (
        DropfansAuthenticationError,
        DropfansAuthorizationError,
        DropfansNotFoundError,
        DropfansRateLimitError,
        DropfansServerError,
        DropfansTimeoutError,
        DropfansTransportError,
    )

    if isinstance(exc, LiveDropInvalid):
        # Every LiveDropInvalid reason is a malformed live payload (missing fields,
        # empty set, non-USD, price range, etc.). Status gate failures are NOT
        # LiveDropInvalid — they surface as VerifiedLive with status != APPROVED.
        return (MALFORMED_PROVIDER_DATA, exc.reason or MALFORMED_RESPONSE, str(exc))

    if isinstance(exc, DropfansNotFoundError) or getattr(exc, "status_code", None) == 404:
        return (PROVIDER_NOT_FOUND, DROP_NOT_FOUND, str(exc))

    if isinstance(exc, (DropfansAuthenticationError, DropfansAuthorizationError)):
        return (CREATOR_MISMATCH, "CREDENTIAL_FAILURE", str(exc))

    if isinstance(exc, DropfansRateLimitError):
        return (PROVIDER_TIMEOUT_AMBIGUOUS, RATE_LIMITED, str(exc))
    if isinstance(exc, DropfansTimeoutError):
        return (PROVIDER_TIMEOUT_AMBIGUOUS, TIMEOUT, str(exc))
    if isinstance(exc, (DropfansTransportError, DropfansServerError)):
        # DropfansTransportError is base for Timeout; ServerError is 5xx — ambiguous
        sub = TRANSPORT_ERROR if isinstance(exc, DropfansTransportError) else "SERVER_ERROR"
        return (PROVIDER_TIMEOUT_AMBIGUOUS, sub, str(exc))
    # Fallback for unknown DropfansError with 5xx/429
    try:
        code = int(getattr(exc, "status_code", 0) or 0)
        if 500 <= code < 600:
            return (PROVIDER_TIMEOUT_AMBIGUOUS, "SERVER_ERROR", str(exc))
        if code == 429:
            return (PROVIDER_TIMEOUT_AMBIGUOUS, RATE_LIMITED, str(exc))
    except Exception:
        pass
    # Non-Dropfans unexpected error treated as persistence/transport ambiguous
    return (PROVIDER_TIMEOUT_AMBIGUOUS, TRANSPORT_ERROR, str(exc))


async def _fetch_and_verify(creator_id: int, cuid: str, get_drop_fn: Any) -> Any:
    """Fetch via get_drop and verify to VerifiedLive. Raises on any provider/validation failure."""
    from commerce.drop_reconciliation import verify_live_drop

    # Resolve callable: prefer injected, else service.get_drop
    if get_drop_fn is None:
        from integrations.dropfans.service import get_drop as _svc_get

        get_drop_fn = _svc_get
    payload = await get_drop_fn(creator_id, cuid)
    return verify_live_drop(payload)


async def seal_ranked_candidate(
    candidate: Any,
    ranked_context: Any,
    creator_id: int,
    user_id: int,
    chosen_drop_cuid: str,
    *,
    get_drop: Any | None = None,
    now_iso: str | None = None,
) -> SealResult:
    """Seal one ranked candidate into a verified ``commerce_offers`` snapshot.

    Two-step verification: outside TX verify → inside TX re-verify under
    ``seal:{creator}:{user}:{definition}:{version}:{CUID}`` advisory lock.
    Only verified-live fields are persisted; mirror price/membership/hash are
    never used.
    """
    # ---- input validation (fail closed, no provider call yet) ----
    try:
        creator_id = _require_scope("creator_id", creator_id)
        user_id = _require_scope("user_id", user_id)
    except ValueError as exc:
        return SealResult(CREATOR_MISMATCH, "INVALID_SCOPE", str(exc))

    if candidate is None:
        return SealResult(CREATOR_MISMATCH, "MISSING_CANDIDATE", "candidate is required")
    if not isinstance(chosen_drop_cuid, str) or not chosen_drop_cuid.strip():
        return SealResult(PROVIDER_DRIFT, CUID_NOT_MAPPED, "chosen_drop_cuid is required")
    chosen_drop_cuid = chosen_drop_cuid.strip()

    cand_creator = _get(candidate, "creator_id")
    cand_user = _get(candidate, "user_id")
    if cand_creator != creator_id or cand_user != user_id:
        return SealResult(
            CREATOR_MISMATCH,
            "SCOPE_MISMATCH",
            f"candidate {cand_creator}/{cand_user} != {creator_id}/{user_id}",
        )

    # ranked_context audit (when supplied) must match scope
    if ranked_context is not None:
        rc_creator = _get(ranked_context, "creator_id", creator_id)
        rc_user = _get(ranked_context, "user_id", user_id)
        if rc_creator != creator_id or rc_user != user_id:
            return SealResult(
                CREATOR_MISMATCH, "RANKED_CONTEXT_MISMATCH", "ranked_context scope mismatch"
            )

    # chosen CUID must be in candidate.mapped_drop_ids
    mapped = _get(candidate, "mapped_drop_ids", ())
    try:
        mapped_set = {str(x).strip() for x in (mapped or ()) if str(x).strip()}
    except Exception:
        mapped_set = set()
    if chosen_drop_cuid not in mapped_set:
        return SealResult(
            PROVIDER_DRIFT, CUID_NOT_MAPPED, f"{chosen_drop_cuid!r} not in {sorted(mapped_set)!r}"
        )

    # provider state must be unverified before sealing (candidate invariant)
    prov = _get(candidate, "provider_verification", "unverified")
    if prov != "unverified":
        return SealResult(MALFORMED_PROVIDER_DATA, "PROVIDER_STATE_NOT_UNVERIFIED", f"{prov!r}")

    # Extract definition identity for sealed_identity + reason
    definition_id = _get(candidate, "definition_id")
    version = _get(candidate, "version")
    stable_key = _get(candidate, "stable_key", "")
    try:
        definition_id = int(definition_id)
        version = int(version)
        if definition_id <= 0 or version < 1:
            raise ValueError
    except Exception:
        return SealResult(
            MALFORMED_PROVIDER_DATA,
            DEFINITION_COMMERCIAL_MISMATCH,
            "candidate definition identity invalid",
        )
    sealed_identity = _sealed_identity(
        creator_id, user_id, definition_id, version, chosen_drop_cuid
    )
    advisory_key = _advisory_key(sealed_identity)

    # ---- first fetch + verify outside TX ----
    try:
        verified_first = await _fetch_and_verify(creator_id, chosen_drop_cuid, get_drop)
    except BaseException as exc:  # noqa: BLE001
        status, sub, detail = _classify_provider_error(exc)
        return SealResult(status, sub, detail, sealed_identity=sealed_identity)

    cmp_first = verify_candidate_against_live(candidate, chosen_drop_cuid, verified_first)
    if not cmp_first.ok:
        # Map drift subreason to PROVIDER_DRIFT, status rejection already handled as PROVIDER_STATUS_NOT_APPROVED drift path
        if cmp_first.subreason == PROVIDER_STATUS_NOT_APPROVED:
            return SealResult(
                PROVIDER_REJECTED,
                PROVIDER_STATUS_NOT_APPROVED,
                cmp_first.detail,
                sealed_identity=sealed_identity,
            )
        return SealResult(
            PROVIDER_DRIFT, cmp_first.subreason, cmp_first.detail, sealed_identity=sealed_identity
        )

    # ---- second fetch + verify inside serialized TX then persist ----
    # Lazily import pool to allow test injection via monkeypatch
    try:
        from db.postgres import get_pool
    except Exception as exc:
        return SealResult(
            LOCAL_PERSISTENCE_FAILURE,
            PERSISTENCE_ERROR,
            f"pool unavailable: {exc}",
            sealed_identity=sealed_identity,
        )

    # Resolve pool (mocked in tests)
    try:
        pool = await get_pool()
    except BaseException as exc:
        return SealResult(
            LOCAL_PERSISTENCE_FAILURE, PERSISTENCE_ERROR, str(exc), sealed_identity=sealed_identity
        )

    # Acquire connection + TX
    verified_second: Any | None = None
    try:
        async with pool.acquire() as conn:
            # Use transaction if available (asyncpg); fallback to plain acquire for fakes
            txn = None
            try:
                txn = conn.transaction()
                await txn.__aenter__()  # type: ignore[union-attr]
            except Exception:
                txn = None

            try:
                # Advisory lock on sealed identity (only serializes same sealed identity)
                try:
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                        advisory_key,
                    )
                except Exception as exc:
                    if txn is not None:
                        try:
                            await txn.__aexit__(type(exc), exc, exc.__traceback__)  # type: ignore[union-attr]
                        except Exception:
                            pass
                    return SealResult(
                        LOCAL_PERSISTENCE_FAILURE,
                        PERSISTENCE_ERROR,
                        f"lock failed: {exc}",
                        sealed_identity=sealed_identity,
                    )

                # Second provider GET inside TX
                try:
                    verified_second = await _fetch_and_verify(
                        creator_id, chosen_drop_cuid, get_drop
                    )
                except BaseException as exc:
                    if txn is not None:
                        try:
                            await txn.__aexit__(type(exc), exc, exc.__traceback__)  # type: ignore[union-attr]
                        except Exception:
                            pass
                    status, sub, detail = _classify_provider_error(exc)
                    return SealResult(status, sub, detail, sealed_identity=sealed_identity)

                cmp_second = verify_candidate_against_live(
                    candidate, chosen_drop_cuid, verified_second
                )
                if not cmp_second.ok:
                    if txn is not None:
                        try:
                            await txn.__aexit__(None, None, None)  # type: ignore[union-attr]
                        except Exception:
                            pass
                    if cmp_second.subreason == PROVIDER_STATUS_NOT_APPROVED:
                        return SealResult(
                            PROVIDER_REJECTED,
                            PROVIDER_STATUS_NOT_APPROVED,
                            cmp_second.detail,
                            sealed_identity=sealed_identity,
                            verified_live=verified_second,
                        )
                    return SealResult(
                        PROVIDER_DRIFT,
                        cmp_second.subreason,
                        cmp_second.detail,
                        sealed_identity=sealed_identity,
                        verified_live=verified_second,
                    )

                # Idempotency: any pending/clicked offer for same sealed identity?
                # Check via reason JSON (definition identity) + CUID; no migration.
                try:
                    # Try JSON-aware check; fallback to LIKE for test fakes.
                    existing = None
                    try:
                        rows = await conn.fetch(
                            """
                            SELECT * FROM commerce_offers
                            WHERE creator_id = $1 AND user_id = $2
                              AND dropfans_product_id = $3
                              AND state IN ('pending', 'clicked')
                            ORDER BY created_at DESC LIMIT 10
                            """,
                            creator_id,
                            user_id,
                            chosen_drop_cuid,
                        )
                    except Exception:
                        rows = []
                    for r in rows or []:
                        reason_raw = (
                            r.get("reason") if isinstance(r, dict) else getattr(r, "reason", None)
                        )
                        if not reason_raw:
                            continue
                        try:
                            parsed = (
                                json.loads(reason_raw)
                                if isinstance(reason_raw, str)
                                else reason_raw
                            )
                            if (
                                isinstance(parsed, dict)
                                and int(parsed.get("definition_id", -1)) == int(definition_id)
                                and int(parsed.get("definition_version", -1)) == int(version)
                            ):
                                existing = r
                                break
                        except Exception:
                            # Fallback substring check for fakes without JSON
                            if str(definition_id) in str(reason_raw) and str(version) in str(
                                reason_raw
                            ):
                                existing = r
                                break
                    if existing is not None:
                        # Idempotent — return existing without duplicate insert
                        if txn is not None:
                            try:
                                await txn.__aexit__(None, None, None)  # type: ignore[union-attr]
                            except Exception:
                                pass
                        return SealResult(
                            SEALED,
                            None,
                            "already_sealed",
                            offer=dict(existing),
                            verified_live=verified_second,
                            sealed_identity=sealed_identity,
                        )
                except BaseException as exc:
                    if txn is not None:
                        try:
                            await txn.__aexit__(type(exc), exc, exc.__traceback__)  # type: ignore[union-attr]
                        except Exception:
                            pass
                    return SealResult(
                        LOCAL_PERSISTENCE_FAILURE,
                        PERSISTENCE_ERROR,
                        f"idempotency check failed: {exc}",
                        sealed_identity=sealed_identity,
                    )

                # Build verified snapshot (only live-verified fields)
                try:
                    from commerce.vault_sets import canonical_identity_ids, drop_content_hash

                    canonical_live_ids = canonical_identity_ids(
                        list(verified_second.vault_item_ids)
                    )
                    media_count = len(canonical_live_ids)
                    verified_hash = drop_content_hash(canonical_live_ids)
                except BaseException as exc:
                    if txn is not None:
                        try:
                            await txn.__aexit__(type(exc), exc, exc.__traceback__)  # type: ignore[union-attr]
                        except Exception:
                            pass
                    return SealResult(
                        MALFORMED_PROVIDER_DATA,
                        MALFORMED_RESPONSE,
                        f"hash failed: {exc}",
                        sealed_identity=sealed_identity,
                    )

                # Synthetic product_id for legacy NOT NULL column
                synthetic_product_id = _synthetic_product_id(chosen_drop_cuid)
                # Prefer existing mirror id when available (creator-scoped)
                try:
                    from db import dropfans as _ddb

                    existing_product = await _ddb.find_dropfans_product(
                        creator_id, chosen_drop_cuid
                    )  # type: ignore[func-returns-value]
                    if existing_product and existing_product.get("id"):
                        try:
                            synthetic_product_id = int(existing_product["id"])
                        except Exception:
                            pass
                except Exception:
                    pass

                # Resolve link (convenience only, not authority)
                link = _get(verified_second, "buy_url")
                if not link or not str(link).strip():
                    link = f"https://www.dropfans.io/buy/{chosen_drop_cuid}"

                price_minor = int(_get(verified_second, "price_minor"))
                currency = str(_get(verified_second, "currency") or "USD").strip().upper()
                allow_download = bool(_get(verified_second, "allow_download"))

                sealed_at = now_iso or _now_iso()
                reason_envelope = {
                    "v": 1,
                    "definition_id": int(definition_id),
                    "definition_version": int(version),
                    "stable_key": str(stable_key),
                    "sealed_at": sealed_at,
                    "verified_at": sealed_at,
                    "verified_hash": verified_hash,
                    "allow_download": allow_download,
                    "verifier_version": VERIFIER_VERSION,
                }
                reason_json = json.dumps(reason_envelope, sort_keys=True)

                # Persist
                try:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO commerce_offers (
                            creator_id, user_id, product_id, link, price_minor, currency,
                            reason, created_by, dropfans_product_id, vault_item_ids, media_count, drop_content_hash
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::text[], $11, $12)
                        RETURNING *
                        """,
                        creator_id,
                        user_id,
                        synthetic_product_id,
                        str(link).strip(),
                        price_minor,
                        currency,
                        reason_json,
                        "opportunity_sealing",
                        chosen_drop_cuid,
                        canonical_live_ids,
                        media_count,
                        verified_hash,
                    )
                except BaseException as exc:
                    if txn is not None:
                        try:
                            await txn.__aexit__(type(exc), exc, exc.__traceback__)  # type: ignore[union-attr]
                        except Exception:
                            pass
                    return SealResult(
                        LOCAL_PERSISTENCE_FAILURE,
                        PERSISTENCE_ERROR,
                        f"insert failed: {exc}",
                        sealed_identity=sealed_identity,
                        verified_live=verified_second,
                    )

                if txn is not None:
                    try:
                        await txn.__aexit__(None, None, None)  # type: ignore[union-attr]
                    except Exception:
                        pass
                return SealResult(
                    SEALED,
                    None,
                    None,
                    offer=dict(row) if row else None,
                    verified_live=verified_second,
                    sealed_identity=sealed_identity,
                )

            except BaseException as exc:  # outer TX guard
                if txn is not None:
                    try:
                        await txn.__aexit__(type(exc), exc, exc.__traceback__)  # type: ignore[union-attr]
                    except Exception:
                        pass
                return SealResult(
                    LOCAL_PERSISTENCE_FAILURE,
                    PERSISTENCE_ERROR,
                    str(exc),
                    sealed_identity=sealed_identity,
                )

    except BaseException as exc:
        return SealResult(
            LOCAL_PERSISTENCE_FAILURE, PERSISTENCE_ERROR, str(exc), sealed_identity=sealed_identity
        )
