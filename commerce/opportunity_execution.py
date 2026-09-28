"""P3.3.14.4 — thin sealed-offer execution adapter.

Smallest safe bridge from an authoritative sealed offer to a fan-facing
send-stream handoff. The sealed offer is authoritative; this module never
selects, ranks, re-seals, re-queries live state, mirrors, or derives
commercial facts. It copies sealed facts verbatim, renders deterministic
wording, reserves a deterministic send identity, and enqueues once.

Ordering:

    validate sealed offer
      -> build sealed facts
      -> render final message (sealed price + sealed link)
      -> reserve deterministic send identity
      -> enqueue once
      -> release reservation (worker confirms only after proven Telegram
         acceptance; H4 Batch 3 D1 — never confirm here) /
         release on enqueue failure

Duplicate reserves are refined read-only: confirmed ("1") reports
ALREADY_DELIVERED, anything else reports ALREADY_RESERVED. Only a
proven handoff (EXECUTED or ALREADY_DELIVERED) may suppress a redundant
normal response; reserved/in-flight must let normal handling proceed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from db import redis as _redis_mod

logger = logging.getLogger("commerce.opportunity_execution")

EXECUTED = "EXECUTED"
ALREADY_ENQUEUED = "ALREADY_ENQUEUED"
NOT_SEALED = "NOT_SEALED"
SCOPE_MISMATCH = "SCOPE_MISMATCH"
ENQUEUE_FAILED = "ENQUEUE_FAILED"

# Caller-visible refinement of ALREADY_ENQUEUED (status unchanged).
# Only "1" proves a confirmed handoff; anything else is in-flight/absent.
ALREADY_DELIVERED = "ALREADY_DELIVERED"
ALREADY_RESERVED = "ALREADY_RESERVED"
DELIVERED_VALUE = "1"

_EXECUTE_STATUSES = frozenset(
    {EXECUTED, ALREADY_ENQUEUED, NOT_SEALED, SCOPE_MISMATCH, ENQUEUE_FAILED}
)


@dataclass(frozen=True)
class SealedExecutionFacts:
    """Immutable facts copied verbatim from the sealed offer."""

    offer_id: int
    creator_id: int
    user_id: int
    link: str
    price_minor: int
    currency: str
    vault_item_ids: tuple[str, ...]
    media_count: int
    dropfans_product_id: str
    definition_id: int | None = None
    definition_version: int | None = None
    stable_key: str | None = None
    verified_hash: str | None = None
    allow_download: bool | None = None
    sealed_at: str | None = None


@dataclass(frozen=True)
class ExecuteResult:
    """Deterministic outcome of one sealed-execution attempt."""

    status: str
    subreason: str | None = None
    detail: str | None = None
    offer_id: int | None = None
    dedup_id: str | None = None
    generation_id: str | None = None
    facts: SealedExecutionFacts | None = None
    content: str | None = None
    # Refinement for ALREADY_ENQUEUED only. True means the dedup key holds
    # the confirmed "1" value; False means reserved/in-flight, absent, or
    # unreadable (caller must NOT suppress normal output). None for all
    # other statuses.
    already_delivered: bool | None = None
    dedup_value: str | None = None


def is_sealed_ppv_handled(result: Any) -> bool:
    """Return True only when the sealed PPV outbound was handed off.

    True for EXECUTED, and for ALREADY_ENQUEUED proven delivered
    (already_delivered is True, subreason ALREADY_DELIVERED, or raw
    dedup value "1"). Every other status — including reserved/in-flight
    duplicates, enqueue/reservation failures, and validation rejections —
    returns False so normal conversation handling proceeds.
    """
    if result is None:
        return False
    if (
        _get(result, "status", None) != EXECUTED
        and _get(result, "status", None) != ALREADY_ENQUEUED
    ):
        return False
    if _get(result, "status", None) == EXECUTED:
        return True
    delivered_flag = _get(result, "already_delivered", None)
    if delivered_flag is True:
        return True
    if delivered_flag is False:
        return False
    if _get(result, "subreason", None) == ALREADY_DELIVERED:
        return True
    if _get(result, "dedup_value", None) == DELIVERED_VALUE:
        return True
    return False


def _get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    try:
        return getattr(source, key, default)
    except Exception:
        return default


def _require_scope(name: str, value: Any) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return int(value)


def _format_price(price_minor: int, currency: str) -> str:
    curr = (currency or "USD").strip().upper() or "USD"
    dollars = int(price_minor) // 100
    cents = int(price_minor) % 100
    if curr == "USD":
        return f"${dollars}.{cents:02d}"
    return f"{dollars}.{cents:02d} {curr}"


def _build_facts_block(price_str: str, link: str, media_count: int) -> str:
    if int(media_count) == 1:
        media_phrase = "1 exclusive item"
    else:
        media_phrase = f"{int(media_count)} exclusive items"
    return (
        f"{media_phrase} for {price_str} \u2728\n\n"
        f"Grab it here: {link}"
    )


def _build_message(price_str: str, link: str, media_count: int) -> str:
    facts_block = _build_facts_block(price_str, link, media_count)
    return f"I've got something special for you — {facts_block}"


async def _try_dynamic_sealed_content(
    *,
    price_str: str,
    link: str,
    media_count: int,
    conversation: Any | None = None,
    user_message: Any | None = None,
    persona: Any | None = None,
) -> str | None:
    """Best-effort dynamic lead-in + deterministic facts. Returns None on any failure."""
    try:
        has_conversation = False
        if conversation is not None:
            try:
                has_conversation = len(list(conversation)) > 0
            except Exception:
                has_conversation = bool(conversation)
        has_user_message = isinstance(user_message, str) and bool(user_message.strip())
        if not has_conversation and not has_user_message:
            return None
        from commerce.dynamic_copy import (
            build_sealed_message_with_lead_in,
            generate_sealed_lead_in,
        )

        conv_list = list(conversation) if has_conversation else None
        user_text = user_message if has_user_message else None
        persona_text = persona if isinstance(persona, str) and persona.strip() else None
        result = await generate_sealed_lead_in(
            conversation=conv_list,
            user_message=user_text,
            persona=persona_text,
            media_count=media_count,
        )
        if result is None or getattr(result, "status", None) != "SUCCESS":
            return None
        lead_in = getattr(result, "text", None)
        if not isinstance(lead_in, str) or not lead_in.strip():
            return None
        return build_sealed_message_with_lead_in(
            lead_in,
            price_str=price_str,
            link=link,
            media_count=media_count,
        )
    except Exception:
        logger.debug("dynamic sealed copy failed — fallback", exc_info=True)
        return None


def _parse_provenance(reason: Any) -> dict[str, Any]:
    try:
        if reason is None:
            return {}
        parsed = json.loads(reason) if isinstance(reason, str) else reason
        if not isinstance(parsed, dict):
            return {}
        out: dict[str, Any] = {}
        try:
            out["definition_id"] = (
                int(parsed.get("definition_id"))
                if parsed.get("definition_id") is not None
                else None
            )
        except Exception:
            out["definition_id"] = None
        try:
            out["definition_version"] = (
                int(parsed.get("definition_version"))
                if parsed.get("definition_version") is not None
                else None
            )
        except Exception:
            out["definition_version"] = None
        stable = parsed.get("stable_key")
        out["stable_key"] = str(stable) if stable is not None else None
        vhash = parsed.get("verified_hash")
        out["verified_hash"] = str(vhash) if vhash is not None else None
        allow = parsed.get("allow_download")
        out["allow_download"] = bool(allow) if allow is not None else None
        sealed_at = parsed.get("sealed_at")
        out["sealed_at"] = str(sealed_at) if sealed_at is not None else None
        return out
    except Exception:
        return {}


async def execute_sealed_offer(
    seal_result: Any,
    *,
    creator_id: int,
    user_id: int,
    generation_id: str | None = None,
    conversation: Any | None = None,
    user_message: str | None = None,
    persona: str | None = None,
    telegram_message_id: int | str | None = None,
    turn_preclaimed: bool = False,
) -> ExecuteResult:
    """Enqueue one fan-facing message for an already-sealed offer.

    The sealed offer is authoritative. This function never chooses an
    offer, never re-verifies live state, never reads mirrors, and never
    derives price, membership, or links. It copies sealed values verbatim
    into deterministic wording and enqueues exactly once under
    ``sealed:{offer.id}``.
    """
    scope_creator = _require_scope("creator_id", creator_id)
    scope_user = _require_scope("user_id", user_id)
    if scope_creator is None or scope_user is None:
        return ExecuteResult(
            NOT_SEALED,
            subreason="INVALID_SCOPE",
            detail="creator_id and user_id are required",
            generation_id=generation_id,
        )

    status = _get(seal_result, "status", None)
    offer = _get(seal_result, "offer", None)
    if status != "SEALED" or offer is None:
        return ExecuteResult(
            NOT_SEALED,
            subreason="NOT_SEALED",
            detail=f"status={status!r} offer_present={offer is not None}",
            generation_id=generation_id,
        )

    try:
        raw_offer_id = _get(offer, "id", None)
        offer_id = int(raw_offer_id)
        if offer_id <= 0:
            raise ValueError("bad offer id")
    except Exception:
        return ExecuteResult(
            NOT_SEALED,
            subreason="INVALID_OFFER",
            detail="offer.id invalid",
            generation_id=generation_id,
        )

    try:
        offer_creator = _get(offer, "creator_id", None)
        offer_user = _get(offer, "user_id", None)
        offer_creator_int = int(offer_creator) if offer_creator is not None else None
        offer_user_int = int(offer_user) if offer_user is not None else None
    except Exception:
        offer_creator_int = None
        offer_user_int = None
    if offer_creator_int != int(scope_creator) or offer_user_int != int(scope_user):
        return ExecuteResult(
            SCOPE_MISMATCH,
            subreason="SCOPE_MISMATCH",
            detail=f"offer {offer_creator_int}/{offer_user_int} != {scope_creator}/{scope_user}",
            offer_id=offer_id,
            generation_id=generation_id,
        )

    try:
        link_raw = _get(offer, "link", None)
        link = str(link_raw).strip() if link_raw is not None else ""
        if not link:
            raise ValueError("missing link")
    except Exception:
        return ExecuteResult(
            NOT_SEALED,
            subreason="INVALID_OFFER",
            detail="offer.link invalid",
            offer_id=offer_id,
            generation_id=generation_id,
        )

    try:
        price_raw = _get(offer, "price_minor", None)
        if isinstance(price_raw, bool):
            raise ValueError("bad price")
        price_minor = int(price_raw)
        if price_minor < 0:
            raise ValueError("bad price")
    except Exception:
        return ExecuteResult(
            NOT_SEALED,
            subreason="INVALID_OFFER",
            detail="offer.price_minor invalid",
            offer_id=offer_id,
            generation_id=generation_id,
        )

    try:
        curr_raw = _get(offer, "currency", None)
        currency = str(curr_raw).strip().upper() if curr_raw is not None else ""
        if not currency:
            raise ValueError("missing currency")
    except Exception:
        return ExecuteResult(
            NOT_SEALED,
            subreason="INVALID_OFFER",
            detail="offer.currency invalid",
            offer_id=offer_id,
            generation_id=generation_id,
        )

    try:
        vault_raw = _get(offer, "vault_item_ids", None)
        if not isinstance(vault_raw, (list, tuple)) or len(list(vault_raw)) == 0:
            raise ValueError("missing vault ids")
        vault_ids = tuple(str(x).strip() for x in list(vault_raw) if str(x).strip())
        if not vault_ids:
            raise ValueError("missing vault ids")
    except Exception:
        return ExecuteResult(
            NOT_SEALED,
            subreason="INVALID_OFFER",
            detail="offer.vault_item_ids invalid",
            offer_id=offer_id,
            generation_id=generation_id,
        )

    try:
        media_raw = _get(offer, "media_count", None)
        media_count = int(media_raw)
        if media_count <= 0:
            raise ValueError("bad media count")
    except Exception:
        return ExecuteResult(
            NOT_SEALED,
            subreason="INVALID_OFFER",
            detail="offer.media_count invalid",
            offer_id=offer_id,
            generation_id=generation_id,
        )

    try:
        cuid_raw = _get(offer, "dropfans_product_id", None)
        cuid = str(cuid_raw).strip() if cuid_raw is not None else ""
        if not cuid:
            raise ValueError("missing cuid")
    except Exception:
        return ExecuteResult(
            NOT_SEALED,
            subreason="INVALID_OFFER",
            detail="offer.dropfans_product_id invalid",
            offer_id=offer_id,
            generation_id=generation_id,
        )

    provenance = _parse_provenance(_get(offer, "reason", None))

    facts = SealedExecutionFacts(
        offer_id=offer_id,
        creator_id=int(scope_creator),
        user_id=int(scope_user),
        link=link,
        price_minor=price_minor,
        currency=currency,
        vault_item_ids=vault_ids,
        media_count=media_count,
        dropfans_product_id=cuid,
        definition_id=provenance.get("definition_id"),
        definition_version=provenance.get("definition_version"),
        stable_key=provenance.get("stable_key"),
        verified_hash=provenance.get("verified_hash"),
        allow_download=provenance.get("allow_download"),
        sealed_at=provenance.get("sealed_at"),
    )

    price_str = _format_price(facts.price_minor, facts.currency)
    # Dynamic copywriter: lead-in is conversational, facts remain deterministic.
    # Any generation/validation failure falls back to the deterministic template.
    content = _build_message(price_str, facts.link, facts.media_count)
    try:
        dynamic_content = await _try_dynamic_sealed_content(
            price_str=price_str,
            link=facts.link,
            media_count=facts.media_count,
            conversation=conversation,
            user_message=user_message,
            persona=persona,
        )
        if isinstance(dynamic_content, str) and dynamic_content.strip():
            content = dynamic_content
    except Exception:
        logger.debug("dynamic sealed content failed — fallback", exc_info=True)

    dedup_id = f"sealed:{facts.offer_id}"

    # Phase 3.3: single-outbound per (creator,user,tg_id). When the inbound
    # turn id is known and this call does not ride on an outer turn claim,
    # the first claimant for the turn wins; a repeat (redelivery, second
    # draft, sealed+normal) is suppressed here so no second wire starts.
    # Fail-open (helper returns True on error/missing id) so the gate can
    # never cause a lost-send. turn_preclaimed=True skips the re-claim for
    # the llm_worker chain that already claimed before calling us.
    if not turn_preclaimed:
        try:
            _tg = str(telegram_message_id).strip() if telegram_message_id is not None else ""
            if _tg and _tg != "0":
                _claimed = await _redis_mod.try_claim_turn_send(
                    int(scope_creator), int(scope_user), _tg
                )
                if not _claimed:
                    logger.info(
                        "sealed execution suppressed turn already sent offer=%s creator=%s user=%s tg_id=%s",
                        offer_id,
                        scope_creator,
                        scope_user,
                        _tg,
                    )
                    return ExecuteResult(
                        ALREADY_ENQUEUED,
                        subreason="TURN_ALREADY_SENT",
                        detail="turn already sent; suppressing second outbound",
                        offer_id=offer_id,
                        dedup_id=dedup_id,
                        generation_id=generation_id,
                        facts=facts,
                        content=content,
                        already_delivered=False,
                    )
        except Exception:
            logger.debug("sealed turn-gate failed open offer=%s", offer_id, exc_info=True)

    try:
        reservation = await _redis_mod.try_reserve_send_dedup(
            dedup_id, creator_id=int(scope_creator)
        )
    except Exception as exc:
        logger.warning("sealed execution reserve failed offer=%s: %s", offer_id, exc)
        return ExecuteResult(
            ENQUEUE_FAILED,
            subreason="DEDUP_RESERVE_FAILED",
            detail=str(exc)[:200],
            offer_id=offer_id,
            dedup_id=dedup_id,
            generation_id=generation_id,
            facts=facts,
            content=content,
        )
    if reservation is None or reservation is False:
        # Distinguish confirmed handoff ("1") from in-flight reservation
        # ("reserved:<token>") via a read-only lookup. Reservation
        # semantics and TTLs are unchanged; this read never reserves.
        dedup_value: str | None = None
        try:
            dedup_value = await _redis_mod.get_send_dedup_value(
                dedup_id, creator_id=int(scope_creator)
            )
        except Exception:
            dedup_value = None
        if dedup_value == DELIVERED_VALUE:
            return ExecuteResult(
                ALREADY_ENQUEUED,
                subreason=ALREADY_DELIVERED,
                detail="send already confirmed",
                offer_id=offer_id,
                dedup_id=dedup_id,
                generation_id=generation_id,
                facts=facts,
                content=content,
                already_delivered=True,
                dedup_value=dedup_value,
            )
        return ExecuteResult(
            ALREADY_ENQUEUED,
            subreason=ALREADY_RESERVED,
            detail="send reserved in-flight or unreadable; not proven sent",
            offer_id=offer_id,
            dedup_id=dedup_id,
            generation_id=generation_id,
            facts=facts,
            content=content,
            already_delivered=False,
            dedup_value=dedup_value,
        )

    token: str | None = reservation if isinstance(reservation, str) else None

    payload: dict[str, Any] = {
        "entity": str(int(scope_user)),
        "content": content,
        "draft_content": content,
        "was_edited": False,
        "was_auto_approved": True,
        "confidence_score": 1.0,
        "operator_id": None,
        "save_to_db": True,
        "creator_id": str(int(scope_creator)),
    }

    try:
        await _redis_mod.enqueue_send(
            payload,
            dedup_id=dedup_id,
            generation_id=generation_id,
            creator_id=int(scope_creator),
        )
    except Exception as exc:
        try:
            await _redis_mod.release_send_dedup(
                dedup_id, creator_id=int(scope_creator), token=token
            )
        except Exception:
            logger.debug("sealed execution release failed offer=%s", offer_id, exc_info=True)
        return ExecuteResult(
            ENQUEUE_FAILED,
            subreason="ENQUEUE_FAILED",
            detail=str(exc)[:200],
            offer_id=offer_id,
            dedup_id=dedup_id,
            generation_id=generation_id,
            facts=facts,
            content=content,
        )

    # H4 Batch 3 (D1): do NOT confirm the marker here. Confirmation means
    # "Telegram accepted", which only the send worker can prove. Confirming
    # now would make the worker skip the actual send as a "duplicate".
    # Instead release our enqueue-time reservation: the worker then reserves,
    # sends, and confirms normally. A crash before the send stays recoverable
    # (entry pending; reservation lapses back to unreserved).
    try:
        await _redis_mod.release_send_dedup(
            dedup_id, creator_id=int(scope_creator), token=token
        )
    except Exception:
        logger.debug("sealed execution release failed offer=%s", offer_id, exc_info=True)

    return ExecuteResult(
        EXECUTED,
        subreason=None,
        detail=None,
        offer_id=offer_id,
        dedup_id=dedup_id,
        generation_id=generation_id,
        facts=facts,
        content=content,
    )
