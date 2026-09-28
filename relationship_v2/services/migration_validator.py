"""Migration validator (Stage F2). Legacy rows in, gated decisions out.

Caller: FUTURE backfill job (Stage F). Every legacy candidate — flattened
`user_profiles.facts` entries, `FanKnowledgeItem`s, long-term memories —
passes through here before touching V2 tables. Pure function, no I/O.

Rules (Phased_Plan Phase 27 + sunny_upgrade_v2 §65):
- Never blind-migrate: capped/topic-gated/overwritten legacy state is
  untrusted until validated.
- Accepted facts land HISTORICAL with `legacy_imported` confidence (0.5);
  CURRENT requires newer live evidence through the normal pipeline.
- Creator identity, commerce truth, and verbatim intimate content are
  always rejected (commerce re-reads live; intimacy re-abstracts).
- Oversized blobs need human review, not silent truncation.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger("sunny.v2.migration_validator")

LEGACY_IMPORTED_CONFIDENCE = 0.5
MAX_VALUE_CHARS = 512

_CREATOR_KEYS = frozenset(
    {
        "creator_name",
        "creator_identity",
        "creator_biography",
        "creator_account",
        "creator_content",
    }
)
_COMMERCE_KEYS = frozenset(
    {
        "price",
        "product",
        "purchase",
        "purchase_status",
        "ownership",
        "offer",
        "offer_eligibility",
        "transaction",
    }
)
_INTIMATE_MARKERS = (
    "horny",
    "sexy",
    "naked",
    "nude",
    "kiss",
    "flirt",
    "tease",
)


class MigrationDisposition(str, Enum):
    MIGRATE_HISTORICAL = "migrate_historical"
    REJECT = "reject"


class LegacyFact(BaseModel):
    source_table: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1)
    category: str | None = Field(max_length=64, default=None)
    observed_at: datetime | None = None
    legacy_confidence: float | None = Field(ge=0.0, le=1.0, default=None)

    model_config = {"frozen": True}


class MigrationDecision(BaseModel):
    disposition: MigrationDisposition
    reason: str = Field(min_length=1)
    mapped_category: str | None = None
    mapped_confidence: float | None = Field(ge=0.0, le=1.0, default=None)

    model_config = {"frozen": True}


class MigrationReport(BaseModel):
    decisions: list[MigrationDecision] = Field(default_factory=list)
    accepted: int = Field(ge=0, default=0)
    rejected: int = Field(ge=0, default=0)

    model_config = {"frozen": True}


def validate_legacy_fact(fact: LegacyFact) -> MigrationDecision:
    """Gate one legacy candidate. Pure; never raises on content."""
    try:
        key = fact.key.strip().lower()
        value = fact.value.strip()
        category = (fact.category or "").strip().lower()
        if not key or not value:
            return MigrationDecision(
                disposition=MigrationDisposition.REJECT, reason="malformed_empty"
            )
        if len(value) > MAX_VALUE_CHARS:
            return MigrationDecision(
                disposition=MigrationDisposition.REJECT,
                reason="oversized_needs_review",
            )
        if key in _CREATOR_KEYS or key.startswith("creator") or category.startswith("creator"):
            return MigrationDecision(
                disposition=MigrationDisposition.REJECT,
                reason="creator_identity_never_migrated",
            )
        if (
            key in _COMMERCE_KEYS
            or category.startswith("commerce")
            or any(k in key for k in ("price", "purchase", "ownership", "offer"))
        ):
            return MigrationDecision(
                disposition=MigrationDisposition.REJECT,
                reason="commerce_reread_live",
            )
        lowered = value.lower()
        if any(m in lowered for m in _INTIMATE_MARKERS):
            return MigrationDecision(
                disposition=MigrationDisposition.REJECT,
                reason="intimate_verbatim_never_migrated",
            )
        return MigrationDecision(
            disposition=MigrationDisposition.MIGRATE_HISTORICAL,
            reason="legacy_imported_as_history",
            mapped_category=category or "legacy_import",
            mapped_confidence=LEGACY_IMPORTED_CONFIDENCE,
        )
    except Exception:
        logger.exception("migration validation failed (fail-closed reject)")
        return MigrationDecision(
            disposition=MigrationDisposition.REJECT, reason="validation_error"
        )


def validate_batch(facts: list[LegacyFact]) -> MigrationReport:
    """Gate a batch. Order-preserving; never raises."""
    decisions = [validate_legacy_fact(f) for f in facts]
    accepted = sum(1 for d in decisions if d.disposition == MigrationDisposition.MIGRATE_HISTORICAL)
    return MigrationReport(
        decisions=decisions, accepted=accepted, rejected=len(decisions) - accepted
    )
