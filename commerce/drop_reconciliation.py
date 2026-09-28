"""P3.3.6 — provider-verified existing-Drop reconciliation/seeding (operator-gated).

Architecture::

    Known local Drop CUIDs (mirror / intents / offer snapshots)
        ↓  collect_drop_candidates (creator-scoped union, deduped)
    Live provider GET (owning creator's credentials, read-only)
        ↓  verify_live_drop (canonical Vault set, price, currency, download, status)
    Deterministic classification (fail closed on ambiguity)
        ↓
    Operator-approved seed inputs (stable_key + offer_type, explicit only)
        ↓
    OfferDefinition v1 (draft → active) + OfferDefinitionDropMapping

Inert to the legacy commerce path: this module never touches product
selection, ranking, the LLM worker, autonomous execution, taxonomy, or
ContentFamily creation. Provider interaction is GET-only; no helper reachable
from here can create, modify, or delete a Dropfans Drop. Historical
``commerce_offers`` rows are candidate-discovery sources only and are never
mutated. Dry-run is the default; catalog writes require explicit
``dry_run=False`` plus per-Drop operator input.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable

logger = logging.getLogger("commerce.drop_reconciliation")

# Price bounds mirror the Drop creation invariant (service.create_drop):
# USD 0 (free-form) or 5–750 dollars, stored as integer minor units.
_PRICE_MIN_MINOR = 500
_PRICE_MAX_MINOR = 75000


class ReconciliationClassification(str, enum.Enum):
    """Closed classification for one reconciled Drop candidate."""

    SAFE_TO_SEED = "safe_to_seed"
    SAFE_TO_MAP = "safe_to_map"
    NEEDS_PROVIDER_REFRESH = "needs_provider_refresh"
    AMBIGUOUS = "ambiguous"
    UNRECONCILABLE = "unreconcilable"


class LiveDropInvalid(ValueError):
    """Live provider payload failed verification; ``reason`` is machine-readable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CreatorCredentialFailure(Exception):
    """Provider auth failure for a creator: stop reconciliation fail-closed."""


@dataclass(frozen=True)
class DropCandidate:
    """One locally known Drop CUID with optional (non-authoritative) mirror hints."""

    creator_id: int
    dropfans_product_id: str
    mirror_vault_ids: tuple[str, ...] | None = None
    mirror_price_minor: int | None = None
    mirror_allow_download: bool | None = None


@dataclass(frozen=True)
class VerifiedLive:
    """Authoritative live provider state for one Drop (post-verification)."""

    dropfans_product_id: str
    vault_item_ids: list[str]
    price_minor: int
    currency: str
    allow_download: bool
    status: str
    buy_url: str | None = None
    media_count: int | None = None


@dataclass(frozen=True)
class OperatorSeedInput:
    """Explicit operator decision for one Drop: stable key + offer type.

    Nothing is inferred: not from titles, descriptions, folders, taxonomy,
    media count, price, or naming. A one-item Drop *may* be offered as SINGLE,
    but the type is still explicitly selected here, never invented.
    """

    stable_key: str
    offer_type: str

    def validated(self) -> "OperatorSeedInput":
        from db.offer_definitions import OFFER_TYPES

        key = self.stable_key.strip() if isinstance(self.stable_key, str) else ""
        if not key:
            raise ValueError("stable_key must not be empty")
        offer_type = (
            self.offer_type.strip().upper() if isinstance(self.offer_type, str) else ""
        )
        if offer_type not in OFFER_TYPES:
            raise ValueError(f"offer_type must be one of {sorted(OFFER_TYPES)}")
        return OperatorSeedInput(stable_key=key, offer_type=offer_type)


@dataclass
class ReconciliationResult:
    """Structured per-Drop reconciliation outcome (no credentials/secrets)."""

    creator_id: int
    dropfans_product_id: str
    classification: str
    live_vault_item_ids: list[str] | None = None
    live_price_minor: int | None = None
    live_currency: str | None = None
    live_allow_download: bool | None = None
    live_status: str | None = None
    existing_definition_id: int | None = None
    existing_definition_version: int | None = None
    stable_key: str | None = None
    offer_type: str | None = None
    reason: str = ""
    wrote_definition: bool = False
    wrote_mapping: bool = False


@dataclass
class CreatorReconciliation:
    """Full per-creator reconciliation pass (sequential, bounded)."""

    creator_id: int
    dry_run: bool
    results: list[ReconciliationResult] = field(default_factory=list)
    credential_failed: bool = False


def _require_creator_id(creator_id: int) -> int:
    if not isinstance(creator_id, int) or isinstance(creator_id, bool) or creator_id <= 0:
        raise ValueError("creator_id is required")
    return creator_id


def _require_cuid(cuid: Any) -> str | None:
    """Return the stripped CUID, or None when absent/blank (excluded, never guessed)."""
    if not isinstance(cuid, str) or not cuid.strip():
        return None
    return cuid.strip()


def collect_drop_candidates(
    creator_id: int,
    mirror_rows: list[dict[str, Any]] | None = None,
    intent_cuids: list[str] | None = None,
    offer_cuids: list[str] | None = None,
) -> list[DropCandidate]:
    """Union locally known Drop CUIDs, deduplicated by ``creator + CUID``.

    Pure (no I/O): callers supply mirror rows (with optional
    ``vault_item_ids``/``price_minor``/``is_downloadable`` hints) plus plain
    CUID lists from intents and historical offers. Blank CUIDs are excluded.
    Synthetic BIGINT product IDs are never used as identity. Nothing is
    inferred from titles, descriptions, taxonomy, or folders.
    """
    _require_creator_id(creator_id)
    by_cuid: dict[str, DropCandidate] = {}
    for row in mirror_rows or []:
        cuid = _require_cuid((row or {}).get("dropfans_product_id"))
        if cuid is None or cuid in by_cuid:
            continue
        vids: tuple[str, ...] | None = None
        try:
            raw_ids = (row or {}).get("vault_item_ids") or (row or {}).get("vaultItemIds")
            if raw_ids:
                vids = tuple(str(v).strip() for v in list(raw_ids) if str(v).strip())
                if not vids:
                    vids = None
        except Exception:
            vids = None
        price: int | None = None
        try:
            raw_price = (row or {}).get("price_minor")
            price = int(raw_price) if raw_price is not None else None
        except (TypeError, ValueError):
            price = None
        downloadable = (row or {}).get("is_downloadable")
        by_cuid[cuid] = DropCandidate(
            creator_id=creator_id,
            dropfans_product_id=cuid,
            mirror_vault_ids=vids,
            mirror_price_minor=price,
            mirror_allow_download=bool(downloadable) if downloadable is not None else None,
        )
    for cuid in list(intent_cuids or []) + list(offer_cuids or []):
        clean = _require_cuid(cuid)
        if clean is None or clean in by_cuid:
            continue
        by_cuid[clean] = DropCandidate(creator_id=creator_id, dropfans_product_id=clean)
    return [by_cuid[k] for k in sorted(by_cuid)]


async def collect_candidates_for_creator(creator_id: int) -> list[DropCandidate]:
    """Creator-scoped candidate discovery from local state (read-only).

    Sources: Dropfans mirror rows with a CUID, intent CUIDs, historical offer
    CUIDs. No provider calls, no writes, no enumeration of arbitrary provider
    IDs (no list endpoint exists).
    """
    _require_creator_id(creator_id)
    from db import dropfans as _ddb
    from db.postgres import get_pool

    try:
        mirror_rows = await _ddb.list_active_dropfans_products(creator_id)
    except Exception:
        logger.warning("drop reconciliation: mirror read failed creator=%s", creator_id)
        mirror_rows = []
    pool = await get_pool()
    intent_cuids: list[str] = []
    offer_cuids: list[str] = []
    try:
        async with pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT dropfans_product_id
                    FROM dropfans_drop_intents
                    WHERE creator_id = $1 AND dropfans_product_id IS NOT NULL
                      AND btrim(dropfans_product_id) <> ''
                    """,
                    creator_id,
                )
                intent_cuids = [str(r["dropfans_product_id"]) for r in rows]
            except Exception:
                logger.debug("drop reconciliation: intent read failed", exc_info=True)
            try:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT dropfans_product_id
                    FROM commerce_offers
                    WHERE creator_id = $1 AND dropfans_product_id IS NOT NULL
                      AND btrim(dropfans_product_id) <> ''
                    """,
                    creator_id,
                )
                offer_cuids = [str(r["dropfans_product_id"]) for r in rows]
            except Exception:
                logger.debug("drop reconciliation: offer read failed", exc_info=True)
    except Exception:
        logger.warning("drop reconciliation: pool failed creator=%s", creator_id)
    return collect_drop_candidates(
        creator_id,
        mirror_rows=mirror_rows,
        intent_cuids=intent_cuids,
        offer_cuids=offer_cuids,
    )


def dollars_to_price_minor(price: Any) -> int:
    """Convert provider USD dollars to integer minor units (Decimal, no float drift).

    Mirrors the Drop creation invariant: exactly 0, or 500–75000 minor units.
    """
    try:
        minor = int((Decimal(str(price)) * Decimal(100)).to_integral_value())
    except (InvalidOperation, ValueError, TypeError, ArithmeticError) as exc:
        raise LiveDropInvalid("invalid_price") from exc
    if minor != 0 and (minor < _PRICE_MIN_MINOR or minor > _PRICE_MAX_MINOR):
        raise LiveDropInvalid("price_out_of_range")
    if minor < 0:
        raise LiveDropInvalid("price_out_of_range")
    return minor


def verify_live_drop(live: dict[str, Any] | None) -> VerifiedLive:
    """Validate a live ``service.get_drop`` payload into authoritative state.

    Raises :class:`LiveDropInvalid` with a machine-readable reason for every
    rejection: empty/invalid Vault set, over-limit set, bad price, non-USD
    currency, missing allowDownload, or malformed payload. Never guesses.
    """
    from commerce.vault_sets import canonical_identity_ids

    if not isinstance(live, dict):
        raise LiveDropInvalid("malformed_response")
    cuid = _require_cuid(live.get("id") or live.get("productId"))
    if cuid is None:
        raise LiveDropInvalid("missing_product_id")
    media = live.get("media")
    if not isinstance(media, list):
        raise LiveDropInvalid("missing_media")
    try:
        vault_ids = canonical_identity_ids(
            [str(m.get("vault_item_id", "")) for m in media if isinstance(m, dict)]
        )
    except ValueError as exc:
        raise LiveDropInvalid("invalid_vault_ids") from exc
    if not vault_ids:
        raise LiveDropInvalid("empty_vault_set")
    if len(vault_ids) > 10:
        raise LiveDropInvalid("vault_set_over_limit")
    if live.get("price") is None:
        raise LiveDropInvalid("missing_price")
    price_minor = dollars_to_price_minor(live.get("price"))
    currency = live.get("currency") or "USD"
    currency = str(currency).strip().upper()
    if currency != "USD":
        raise LiveDropInvalid("non_usd_currency")
    if "allow_download" in live:
        raw_allow = live.get("allow_download")
    elif "allowDownload" in live:
        raw_allow = live.get("allowDownload")
    else:
        raise LiveDropInvalid("missing_allow_download")
    if raw_allow is None:
        raise LiveDropInvalid("missing_allow_download")
    allow_download = bool(raw_allow)
    status = str(live.get("status") or "").strip().upper()
    if not status:
        raise LiveDropInvalid("missing_status")
    media_count = live.get("media_count", live.get("mediaCount"))
    try:
        media_count = int(media_count) if media_count is not None else None
    except (TypeError, ValueError):
        media_count = None
    return VerifiedLive(
        dropfans_product_id=cuid,
        vault_item_ids=vault_ids,
        price_minor=price_minor,
        currency=currency,
        allow_download=allow_download,
        status=status,
        buy_url=live.get("buy_url", live.get("buyUrl")),
        media_count=media_count,
    )


def definitions_exact_match(definition: dict[str, Any], live: VerifiedLive) -> bool:
    """True when a stored definition exactly equals verified live commercial state."""
    try:
        stored_ids = [str(v) for v in list(definition.get("canonical_vault_item_ids") or [])]
    except Exception:
        return False
    return (
        stored_ids == list(live.vault_item_ids)
        and definition.get("price_minor") == live.price_minor
        and str(definition.get("currency") or "").strip().upper() == live.currency
        and bool(definition.get("allow_download")) is live.allow_download
    )


def classify_reconciliation(
    candidate: DropCandidate,
    live: VerifiedLive | None,
    *,
    live_error: str | None = None,
    existing_definition: dict[str, Any] | None = None,
    already_mapped: bool = False,
) -> tuple[ReconciliationClassification, str]:
    """Deterministic classification; ambiguity always fails closed (never seeds)."""
    if live is None:
        if live_error in ("not_found", "deleted"):
            return ReconciliationClassification.UNRECONCILABLE, "drop_not_found"
        if live_error in ("invalid",):
            return ReconciliationClassification.UNRECONCILABLE, "invalid_live_state"
        return ReconciliationClassification.NEEDS_PROVIDER_REFRESH, live_error or "no_live_state"
    if live.status != "APPROVED":
        return (
            ReconciliationClassification.AMBIGUOUS,
            f"provider_status_not_approved:{live.status}",
        )
    if existing_definition is not None:
        if definitions_exact_match(existing_definition, live):
            if already_mapped:
                return (
                    ReconciliationClassification.SAFE_TO_MAP,
                    "mapping_verified_current",
                )
            return ReconciliationClassification.SAFE_TO_MAP, "exact_definition_exists"
        return (
            ReconciliationClassification.AMBIGUOUS,
            "definition_commercial_mismatch",
        )
    mismatches: list[str] = []
    if candidate.mirror_vault_ids is not None:
        try:
            from commerce.vault_sets import canonical_identity_ids

            mirror_canonical = canonical_identity_ids(list(candidate.mirror_vault_ids))
        except ValueError:
            mismatches.append("vault_set")
        else:
            # Empty mirror membership is no hint (live is authoritative);
            # a non-empty hint must canonically equal the live set.
            if mirror_canonical and mirror_canonical != list(live.vault_item_ids):
                mismatches.append("vault_set")
    if candidate.mirror_price_minor is not None:
        if candidate.mirror_price_minor != live.price_minor:
            mismatches.append("price")
    if candidate.mirror_allow_download is not None:
        if candidate.mirror_allow_download is not live.allow_download:
            mismatches.append("allow_download")
    if mismatches:
        return (
            ReconciliationClassification.AMBIGUOUS,
            f"mirror_live_mismatch:{','.join(mismatches)}",
        )
    return ReconciliationClassification.SAFE_TO_SEED, "live_verified_approved"


def _classify_provider_error(exc: BaseException) -> str:
    """Map provider failures to reconciliation outcomes (never a seed)."""
    from integrations.dropfans.errors import (
        DropfansAuthenticationError,
        DropfansAuthorizationError,
        DropfansNotFoundError,
        DropfansRateLimitError,
        DropfansServerError,
        DropfansTimeoutError,
        DropfansTransportError,
    )

    if isinstance(exc, (DropfansAuthenticationError, DropfansAuthorizationError)):
        return "credential_failure"
    if isinstance(exc, DropfansNotFoundError):
        return "not_found"
    if getattr(exc, "status_code", None) == 404:
        return "not_found"
    if isinstance(exc, DropfansRateLimitError):
        return "rate_limited"
    if isinstance(exc, (DropfansServerError, DropfansTimeoutError, DropfansTransportError)):
        return "transient_failure"
    return "transient_failure"


async def reconcile_creator(
    creator_id: int,
    *,
    dry_run: bool = True,
    operator_inputs: dict[str, OperatorSeedInput] | None = None,
    candidates: list[DropCandidate] | None = None,
    get_drop: Callable[[int, str], Awaitable[dict[str, Any]]] | None = None,
    existing_definitions: list[dict[str, Any]] | None = None,
    existing_mappings: list[dict[str, Any]] | None = None,
) -> CreatorReconciliation:
    """Reconcile one creator's known Drops (sequential, GET-only, fail-closed).

    Dry-run (default) performs zero catalog writes while still verifying live
    state, classifying, and reporting required operator inputs. Live seeding
    requires ``dry_run=False`` plus an :class:`OperatorSeedInput` per Drop.
    Catalog writes go exclusively through ``db.offer_definitions`` DAO
    functions. Raises :class:`CreatorCredentialFailure` state via the
    ``credential_failed`` flag (loop stops; remaining candidates report
    ``NEEDS_PROVIDER_REFRESH``) instead of raising, so operators get a full
    report object.
    """
    _require_creator_id(creator_id)
    operator_inputs = operator_inputs or {}
    report = CreatorReconciliation(creator_id=creator_id, dry_run=dry_run)
    if candidates is None:
        candidates = await collect_candidates_for_creator(creator_id)
    if get_drop is None:
        from integrations.dropfans import service as _service

        get_drop = _service.get_drop
    if existing_definitions is None or existing_mappings is None:
        from db import offer_definitions as _odb

        try:
            if existing_definitions is None:
                existing_definitions = await _odb.list_offer_definitions(creator_id)
            if existing_mappings is None:
                existing_mappings = await _odb.list_offer_definition_drops(creator_id)
        except Exception:
            logger.warning(
                "drop reconciliation: catalog read failed creator=%s", creator_id
            )
            existing_definitions = existing_definitions or []
            existing_mappings = existing_mappings or []
    mapped_cuids: dict[str, dict[str, Any]] = {}
    for mapping in existing_mappings or []:
        try:
            mapped_cuids[str(mapping.get("dropfans_product_id"))] = mapping
        except Exception:
            continue
    definitions_by_id: dict[Any, dict[str, Any]] = {}
    for definition in existing_definitions or []:
        try:
            definitions_by_id[definition.get("id")] = definition
        except Exception:
            continue

    def _find_exact(live: VerifiedLive) -> dict[str, Any] | None:
        for definition in existing_definitions or []:
            try:
                if definitions_exact_match(definition, live):
                    return definition
            except Exception:
                continue
        return None

    credential_failed = False
    for candidate in candidates:
        cuid = candidate.dropfans_product_id
        if credential_failed:
            report.results.append(
                ReconciliationResult(
                    creator_id=creator_id,
                    dropfans_product_id=cuid,
                    classification=ReconciliationClassification.NEEDS_PROVIDER_REFRESH.value,
                    reason="credential_failure",
                )
            )
            continue
        live: VerifiedLive | None = None
        live_error: str | None = None
        live_status: str | None = None
        try:
            payload = await get_drop(creator_id, cuid)
            live = verify_live_drop(payload)
            live_status = live.status
        except LiveDropInvalid as exc:
            # Deterministic content verdicts are unreconcilable (§7: empty /
            # over-limit / invalid Vault set, non-USD currency, out-of-range
            # price). Incomplete envelopes (missing id/media/price/status/
            # allow_download, unparseable price) may complete on retry.
            live_error = exc.reason
            if exc.reason in (
                "empty_vault_set",
                "vault_set_over_limit",
                "invalid_vault_ids",
                "non_usd_currency",
                "price_out_of_range",
            ):
                live_error = "invalid"
        except Exception as exc:  # provider failure: never a seed
            kind = _classify_provider_error(exc)
            if kind == "credential_failure":
                credential_failed = True
                report.credential_failed = True
                report.results.append(
                    ReconciliationResult(
                        creator_id=creator_id,
                        dropfans_product_id=cuid,
                        classification=ReconciliationClassification.NEEDS_PROVIDER_REFRESH.value,
                        reason="credential_failure",
                    )
                )
                continue
            live_error = kind
        existing = definitions_by_id.get((mapped_cuids.get(cuid) or {}).get("definition_id"))
        if existing is None and live is not None:
            existing = _find_exact(live)
        already_mapped = cuid in mapped_cuids
        classification, reason = classify_reconciliation(
            candidate,
            live,
            live_error=live_error,
            existing_definition=existing,
            already_mapped=already_mapped,
        )
        result = ReconciliationResult(
            creator_id=creator_id,
            dropfans_product_id=cuid,
            classification=classification.value,
            live_vault_item_ids=list(live.vault_item_ids) if live else None,
            live_price_minor=live.price_minor if live else None,
            live_currency=live.currency if live else None,
            live_allow_download=live.allow_download if live else None,
            live_status=live_status or (live.status if live else None),
            existing_definition_id=(existing or {}).get("id"),
            existing_definition_version=(existing or {}).get("version"),
            reason=reason,
        )
        if classification is ReconciliationClassification.SAFE_TO_MAP and not dry_run:
            if already_mapped and existing is not None:
                # Idempotent rerun: mapping verified current, no writes.
                report.results.append(result)
                continue
            if existing is not None:
                from db import offer_definitions as _odb

                try:
                    await _odb.map_offer_definition_drop(
                        creator_id,
                        int(existing["id"]),
                        int(existing["version"]),
                        cuid,
                    )
                    result.wrote_mapping = True
                except Exception as exc:
                    if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                        reloaded = await _odb.list_offer_definition_drops(
                            creator_id, int(existing["id"])
                        )
                        if any(
                            str(m.get("dropfans_product_id")) == cuid for m in reloaded
                        ):
                            result.reason = "mapping_verified_current"
                        else:
                            result.classification = (
                                ReconciliationClassification.AMBIGUOUS.value
                            )
                            result.reason = "mapping_conflict"
                    else:
                        result.classification = (
                            ReconciliationClassification.AMBIGUOUS.value
                        )
                        result.reason = f"mapping_failed:{exc.__class__.__name__}"
            report.results.append(result)
            continue
        if classification is ReconciliationClassification.SAFE_TO_SEED and not dry_run:
            seed_input = operator_inputs.get(cuid)
            if seed_input is None:
                result.reason = "awaiting_operator_input"
                report.results.append(result)
                continue
            try:
                validated = seed_input.validated()
            except ValueError as exc:
                result.classification = ReconciliationClassification.AMBIGUOUS.value
                result.reason = f"invalid_operator_input:{exc}"
                report.results.append(result)
                continue
            result.stable_key = validated.stable_key
            result.offer_type = validated.offer_type
            from db import offer_definitions as _odb

            try:
                created = await _odb.create_offer_definition(
                    creator_id,
                    validated.stable_key,
                    validated.offer_type,
                    list(live.vault_item_ids),
                    live.price_minor,
                    live.currency,
                    live.allow_download,
                    version=1,
                    status="draft",
                    family_id=None,
                    config=None,
                )
                result.wrote_definition = True
                result.existing_definition_id = created.get("id")
                result.existing_definition_version = created.get("version")
                activated = await _odb.activate_offer_definition(
                    creator_id, int(created["id"])
                )
                result.existing_definition_version = activated.get("version")
                await _odb.map_offer_definition_drop(
                    creator_id,
                    int(created["id"]),
                    int(activated.get("version", 1)),
                    cuid,
                )
                result.wrote_mapping = True
                result.reason = "seeded_v1_active_mapped"
            except Exception as exc:
                text = str(exc).lower()
                if "unique" in text or "duplicate" in text:
                    try:
                        reloaded = await _odb.get_offer_definition_by_key(
                            creator_id, validated.stable_key, 1
                        )
                    except Exception:
                        reloaded = None
                    if reloaded is not None and definitions_exact_match(reloaded, live):
                        result.existing_definition_id = reloaded.get("id")
                        result.existing_definition_version = reloaded.get("version")
                        try:
                            await _odb.map_offer_definition_drop(
                                creator_id,
                                int(reloaded["id"]),
                                int(reloaded["version"]),
                                cuid,
                            )
                            result.wrote_mapping = True
                            result.reason = "idempotent_conflict_exact_match"
                        except Exception:
                            result.classification = (
                                ReconciliationClassification.AMBIGUOUS.value
                            )
                            result.reason = "mapping_conflict"
                    else:
                        result.classification = ReconciliationClassification.AMBIGUOUS.value
                        result.reason = "stable_key_collision"
                else:
                    result.classification = ReconciliationClassification.AMBIGUOUS.value
                    result.reason = f"seed_failed:{exc.__class__.__name__}"
            report.results.append(result)
            continue
        report.results.append(result)
    return report
