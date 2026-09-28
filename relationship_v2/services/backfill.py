"""Backfill job (Stage F8). Legacy stores in, validated V2 rows out.

Caller: FUTURE offline job, gated by explicit approval (never automatic,
never part of the live turn path). Flattens legacy `user_profiles.facts`
dicts and `FanKnowledgeItem` dicts into `LegacyFact`s, gates each through
`validate_legacy_fact`, and persists accepted rows as CANDIDATE memory
facts. Dry-run by default (validate + report, persist nothing).

Lifecycle justification: `candidate -> superseded` is an INVALID transition
(STATE_MACHINES.md), so legacy rows cannot enter as history/super-
seded rows. They enter as candidates with `legacy_imported` confidence
(0.5, below the promotion gate): preserved, retrievable in lower tiers,
never presented as verified current. Live explicit evidence promotes
through the normal validation lifecycle. In-batch dedupe on normalized
(key, value); reruns are one-shot operations with reconciled counts
(dry-run first, compare report before apply).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from relationship_v2.services.migration_validator import (
    LegacyFact,
    MigrationDecision,
    MigrationDisposition,
    validate_batch,
)

logger = logging.getLogger("sunny.v2.backfill")

PROVENANCE = "relationship_v2.services.backfill"


class BackfillReport(BaseModel):
    scanned: int = Field(ge=0)
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    persisted: int = Field(ge=0)
    dry_run: bool = True
    decisions: list[MigrationDecision] = Field(default_factory=list)

    model_config = {"frozen": True}


def _norm(value: str) -> str:
    return " ".join(value.strip().lower().split())


def flatten_profile_facts(
    facts: dict[str, Any], source_table: str = "user_profiles"
) -> list[LegacyFact]:
    """Flatten a legacy facts dict (one level of namespaces). Never raises."""
    out: list[LegacyFact] = []
    try:
        for key, value in (facts or {}).items():
            if not isinstance(key, str) or key.startswith("_"):
                continue
            if isinstance(value, str) and value.strip():
                out.append(
                    LegacyFact(source_table=source_table, key=key, value=value.strip())
                )
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                out.append(
                    LegacyFact(source_table=source_table, key=key, value=str(value))
                )
            elif isinstance(value, dict):
                for inner, sub in value.items():
                    if not isinstance(inner, str) or inner.startswith("_"):
                        continue
                    if isinstance(sub, str) and sub.strip():
                        out.append(
                            LegacyFact(
                                source_table=source_table,
                                key=f"{key}.{inner}",
                                value=sub.strip(),
                            )
                        )
    except Exception:
        logger.exception("profile flatten failed (fail-open partial)")
    return out


def flatten_knowledge_items(
    items: list[dict[str, Any]], source_table: str = "fan_knowledge"
) -> list[LegacyFact]:
    """Flatten legacy knowledge-item dicts. Never raises."""
    out: list[LegacyFact] = []
    try:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            subject = str(item.get("subject", "") or "")
            value = str(item.get("value", "") or "")
            if not subject.strip() or not value.strip():
                continue
            category = item.get("category")
            out.append(
                LegacyFact(
                    source_table=source_table,
                    key=subject.strip(),
                    value=value.strip(),
                    category=str(category).strip() or None
                    if category is not None
                    else None,
                )
            )
    except Exception:
        logger.exception("knowledge flatten failed (fail-open partial)")
    return out


async def run_backfill(
    entries: list[LegacyFact],
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    *,
    provenance: str = PROVENANCE,
    dry_run: bool = True,
    create_fact: Callable[..., Awaitable[dict]] | None = None,
) -> BackfillReport:
    """Validate (always) and persist (apply mode only) legacy entries."""
    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    if not provenance:
        raise ValueError("provenance required")
    report = validate_batch(entries or [])
    accepted = [
        (fact, decision)
        for fact, decision in zip(entries, report.decisions, strict=True)
        if decision.disposition == MigrationDisposition.MIGRATE_HISTORICAL
    ]
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[LegacyFact, MigrationDecision]] = []
    for fact, decision in accepted:
        marker = (fact.key.strip().lower(), _norm(fact.value))
        if marker in seen:
            continue
        seen.add(marker)
        unique.append((fact, decision))
    persisted = 0
    if not dry_run:
        persist = create_fact or _default_create_fact
        for fact, decision in unique:
            await persist(
                creator_id,
                user_id,
                relationship_id,
                decision.mapped_category or "legacy_import",
                fact.key.strip(),
                fact.value.strip(),
                decision.mapped_confidence or 0.5,
                importance="normal",
                provenance=f"{provenance}:{fact.source_table}",
                source_event_id=None,
                generation_id=None,
            )
            persisted += 1
    return BackfillReport(
        scanned=len(entries),
        accepted=len(unique),
        rejected=report.rejected,
        persisted=persisted,
        dry_run=dry_run,
        decisions=report.decisions,
    )


async def _default_create_fact(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    category: str,
    memory_key: str,
    value: str,
    confidence: float,
    importance: str = "normal",
    provenance: str = "",
    source_event_id: str | None = None,
    generation_id: str | None = None,
) -> dict:
    from relationship_v2.persistence.repository import create_memory_fact

    return await create_memory_fact(
        creator_id, user_id, relationship_id, category, memory_key, value,
        confidence, importance=importance, provenance=provenance,
        source_event_id=source_event_id, generation_id=generation_id,
    )
