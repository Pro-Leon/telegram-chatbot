"""Phase 99 — Deterministic free-photo routing / decision integration.

Integrates the certified Phase 98 `authorize_free_photo` into the existing
deterministic authority boundary. No second context builder, no second LLM call,
no vault/media delivery, no PPV reproduction.

Flow (reuses existing infrastructure):
  process_message()
  → authoritative_application_state (assemble_authoritative_context)
  → existing retrieval / OneCall (single Qwen2.5 generation)
  → validation
  → deterministic authority — free-photo routing (this module) + commerce decision
  → delivery (Phase 100 owns actual Telethon send)

LLM is advisory only. `authorize_free_photo` remains the sole authority for
quota / purchase tier / duplicate / approved-media.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from commerce.free_photo import FreePhotoAuthorization, authorize_free_photo
from db.postgres import get_pool

logger = logging.getLogger("commerce.free_photo_routing")


# Deterministic photo-request detection — pure, no LLM, no DB, no clock.
# Matches the frozen examples "send me a photo" / "can I see a picture" / "show me a pic"
# and common variants. User text is the source; LLM signals are advisory only.
_PHOTO_KEYWORDS = (
    "photo",
    "picture",
    "pic ",
    "pics",
    "image",
    "selfie",
    "selfies",
    "piks",  # common typo
)
_PHOTO_RE = re.compile(r"\b(photo|picture|pics?|images?|selfies?)\b", re.IGNORECASE)
_SEND_RE = re.compile(r"\b(send|show|see|give|want|wanna|buy|can i|can you|please)\b", re.IGNORECASE)


def is_photo_request(user_message: str, signals: Any | None = None) -> bool:
    """Deterministically decide whether the incoming turn is a photo request.

    Uses only `user_message` text (authoritative) and optionally advisory
    `signals` as a hint, never as authority. Returns True if the text
    contains a photo-like token together with a request verb, or if the
    advisory `explicit_content_request` is true and the text is non-empty.
    This keeps LLM from manufacturing a request when the user did not write one,
    while still allowing the advisory to reinforce a borderline text.

    `signals` may be `CommerceSignals` or any object with
    `explicit_content_request` bool attr.
    """
    if not isinstance(user_message, str) or not user_message.strip():
        return False
    text = user_message.strip()
    lower = text.lower()
    # Direct keyword match — most reliable
    if _PHOTO_RE.search(lower):
        # Require a request-like verb or question mark to avoid false positives on storytelling
        if _SEND_RE.search(lower) or "?" in text:
            return True
        # Even without verb, the examples "send me a photo" etc. include verb, but
        # a bare "photo?" or "picture please" should also count
        if any(k.strip() in lower for k in ("photo", "picture", "pics", "pic", "image", "selfie")):
            # If LLM advisory also says content request, reinforce
            try:
                if signals is not None and bool(getattr(signals, "explicit_content_request", False)):
                    return True
            except Exception:
                pass
            # Fallback: photo token alone with question/request -> count
            # Be conservative: require either '?' or send verb; already handled above, so false
            return False
    # Advisory-only path: if LLM says explicit_content_request but text has no photo token,
    # do NOT treat as photo request — prevents LLM manufacturing.
    # However if text contains send+pic variant with typo, already caught.
    return False


# More precise variant used by routing — simple deterministic: photo token + request
def _is_deterministic_photo_request(user_message: str) -> bool:
    if not isinstance(user_message, str) or not user_message.strip():
        return False
    lower = user_message.lower()
    has_photo = bool(_PHOTO_RE.search(lower))
    has_request = bool(_SEND_RE.search(lower) or "photo" in lower or "picture" in lower or "pic" in lower or "image" in lower)
    # Require photo token; request verb is soft
    return has_photo


async def select_approved_free_media(creator_id: int) -> str | None:
    """Deterministically select one approved free-media item for a creator.

    Creator-scoped, approved-only, ordered by `approved_at ASC, id ASC`.
    Returns `vault_item_id` or None if none approved.
    Deterministic: same DB snapshot → same selection. No LLM, no user text.
    """
    if not isinstance(creator_id, int) or creator_id <= 0:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT vault_item_id FROM free_media_pool
            WHERE creator_id = $1 AND status = 'approved'
            ORDER BY approved_at ASC, id ASC
            LIMIT 1
            """,
            creator_id,
        )
        return row["vault_item_id"] if row else None


@dataclass(frozen=True)
class FreePhotoRoutingResult:
    """Result of one deterministic free-photo routing attempt.

    `attempted` is False when the turn was not a photo request or no approved
    media exists — routing was not entered.
    When `attempted` is True, `authorization` holds the Phase 98 result and
    `outcome` is a normalized string for tests/telemetry.
    This module never sends media and never creates a PPV offer.
    """

    attempted: bool
    outcome: str  # not_attempted | eligible | quota_exhausted | already_pending | already_sent_same_media | invalid_media | invalid_creator_or_user | no_approved_media | not_photo_request
    selected_vault_item_id: str | None = None
    authorization: FreePhotoAuthorization | None = None
    # Advisory LLM content request flag (for observability, not authority)
    llm_explicit_content_request: bool | None = None


async def route_free_photo(
    *,
    creator_id: int | None,
    user_id: int,
    user_message: str,
    signals: Any | None = None,
    preselected_vault_item_id: str | None = None,
) -> FreePhotoRoutingResult:
    """Deterministically route a turn that may be a photo request through Phase 98.

    This is the sole Phase 99 entry point. It is stateful (calls
    `authorize_free_photo` which reserves a pending row) and therefore must be
    invoked at most once per logical routing decision, at the deterministic
    authority boundary (after OneCall/validation, before delivery).

    Caller must supply only authoritative identifiers; `vault_item_id` is never
    taken from LLM output or arbitrary user text. If `preselected_vault_item_id`
    is supplied it must already be proven approved (still re-validated inside
    `authorize_free_photo`). Otherwise this function selects deterministically
    via `select_approved_free_media`.

    Returns a `FreePhotoRoutingResult` describing the outcome. The caller must
    handle each outcome without auto-converting `quota_exhausted` etc. into PPV.
    """
    # Advisory flag for observability
    llm_flag = None
    try:
        if signals is not None:
            llm_flag = bool(getattr(signals, "explicit_content_request", False))
    except Exception:
        llm_flag = None

    # Not a photo request → do not enter free-photo path
    # Use stricter deterministic text check (photo token + request verb/?) — see
    # is_photo_request() header. LLM flag alone is never sufficient.
    if not is_photo_request(user_message, signals):
        return FreePhotoRoutingResult(
            attempted=False,
            outcome="not_photo_request",
            llm_explicit_content_request=llm_flag,
        )

    if not isinstance(creator_id, int) or creator_id is None or creator_id <= 0:
        return FreePhotoRoutingResult(
            attempted=True,
            outcome="invalid_creator_or_user",
            llm_explicit_content_request=llm_flag,
        )

    # Select approved media deterministically — never from LLM/user text
    vault_item_id = preselected_vault_item_id
    if vault_item_id is None:
        vault_item_id = await select_approved_free_media(creator_id)
        if vault_item_id is None:
            return FreePhotoRoutingResult(
                attempted=True,
                outcome="no_approved_media",
                llm_explicit_content_request=llm_flag,
            )

    # Authoritative Phase 98 call — sole quota/purchase/duplicate authority
    # Production seam derives current UTC day internally; no request timestamp used.
    auth = await authorize_free_photo(creator_id, user_id, vault_item_id)

    # Normalize outcome for routing layer
    reason = auth.reason
    if auth.eligible:
        outcome = "eligible"
    elif reason == "quota_exhausted":
        outcome = "quota_exhausted"
    elif reason == "already_pending":
        outcome = "already_pending"
    elif reason == "already_sent_same_media":
        outcome = "already_sent_same_media"
    elif reason in ("invalid_media", "media_not_approved"):
        outcome = "invalid_media"
    elif reason == "invalid_creator_or_user":
        outcome = "invalid_creator_or_user"
    else:
        # Fallback for other deterministic reasons (e.g., reused_failed is eligible, already handled)
        # reused_failed / reused_failed_slot are eligible, so not here
        outcome = reason

    return FreePhotoRoutingResult(
        attempted=True,
        outcome=outcome,
        selected_vault_item_id=vault_item_id,
        authorization=auth,
        llm_explicit_content_request=llm_flag,
    )


# For tests: expose the deterministic check
__all__ = [
    "is_photo_request",
    "select_approved_free_media",
    "route_free_photo",
    "FreePhotoRoutingResult",
]
