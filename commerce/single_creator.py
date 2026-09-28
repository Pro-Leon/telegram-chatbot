"""Phase 5.4 Chk 6H — single-creator application resolution.

The current deployment is an in-house tool for exactly ONE operational
creator: one application, one configured/known creator, that creator's
Dropfans products, Telegram fans. The data model keeps ``creator_id``
scoping for future multi-creator use; this module only makes today's
resolution deterministic and explicit.

Authoritative source: the existing ``creator_integrations`` table — a creator
is operational only when its Dropfans integration status is ``active``
(``error``/``disconnected`` are explicitly non-operational). No other input is
consulted; the resolver never reads conversation text and never guesses.

Outcomes (closed set):

- READY                      exactly one active Dropfans integration
- CREATOR_CONTEXT_UNAVAILABLE  zero active integrations (or DB failure)
- AMBIGUOUS_CREATOR_CONTEXT    more than one active integration

AMBIGUOUS is never resolved by picking a creator: the caller keeps the
standard (non-commerce) path, exactly as for UNAVAILABLE.

Invariants:

- DETERMINISTIC: identical DB snapshots yield identical resolutions
  (ORDER BY creator_id ASC).
- FAILURE ISOLATION: DB failures degrade to CREATOR_CONTEXT_UNAVAILABLE;
  this function never raises, so a commerce attempt can never break the
  normal chatbot because of creator resolution.
"""

import logging
from enum import Enum

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger("commerce.single_creator")


class SingleCreatorStatus(str, Enum):
    """Closed outcome set of single-creator application resolution."""

    READY = "ready"
    CREATOR_CONTEXT_UNAVAILABLE = "creator_context_unavailable"
    AMBIGUOUS_CREATOR_CONTEXT = "ambiguous_creator_context"


class SingleCreatorContext(BaseModel):
    """Resolution outcome. ``creator_id`` is set only for READY."""

    model_config = ConfigDict(extra="forbid")

    status: SingleCreatorStatus
    creator_id: int | None = None


async def resolve_single_application_creator() -> SingleCreatorContext:
    """Return the single operational creator of this deployment.

    Exactly one ACTIVE Dropfans integration resolves to READY with its
    creator_id. Zero active integrations yield CREATOR_CONTEXT_UNAVAILABLE.
    More than one active integration yields AMBIGUOUS_CREATOR_CONTEXT — no
    arbitrary pick (in particular never the lowest id by accident).
    """
    try:
        # Active Dropfans integrations (sole active provider); error and
        # disconnected integrations are explicitly non-operational.
        from db import dropfans as db_dropfans
        try:
            active_ids = await db_dropfans.list_active_dropfans_creator_ids()
        except Exception:
            logger.warning(
                "single-creator dropfans lookup failed",
                exc_info=True,
            )
            return SingleCreatorContext(status=SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE)
        if not active_ids:
            return SingleCreatorContext(status=SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE)
        if len(active_ids) > 1:
            logger.warning(
                "single-creator ambiguous: %d active integrations, refusing to pick",
                len(active_ids),
            )
            return SingleCreatorContext(status=SingleCreatorStatus.AMBIGUOUS_CREATOR_CONTEXT)
        return SingleCreatorContext(
            status=SingleCreatorStatus.READY,
            creator_id=active_ids[0],
        )
    except Exception:
        logger.warning(
            "single-creator resolution failed (DB error) — commerce unavailable",
            exc_info=True,
        )
        return SingleCreatorContext(status=SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE)
