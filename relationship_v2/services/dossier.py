"""Operator debug dossier (F13 code slice). Turn context + rows in, view out.

Answers "why does Sunny believe this?" by rendering one fan's V2 state as
named, bounded sections: relationship, person, memories, loops, episodes,
patterns, intimate continuity, commerce, timeline. Pure composer over a
`TurnContext` plus raw store rows; no I/O, no new queries. Live serving
(dashboard route) and prod log audit stay later F13 items.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from relationship_v2.services.turn_context import TurnContext

logger = logging.getLogger("sunny.v2.dossier")

LINE_LIMIT = 280
SECTION_LIMIT = 2000


class DossierSection(BaseModel):
    name: str = Field(min_length=1)
    lines: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


class Dossier(BaseModel):
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: str = Field(min_length=1)
    sections: list[DossierSection] = Field(default_factory=list)
    generated_at: datetime
    provenance: str = Field(min_length=1)

    model_config = {"frozen": True}


def _line(text: object, limit: int = LINE_LIMIT) -> str:
    return " ".join(str(text).split())[:limit]


def _take(lines: list[str], budget: int = SECTION_LIMIT) -> list[str]:
    kept: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 <= budget:
            kept.append(line)
            used += len(line) + 1
    return kept


def build_dossier(
    ctx: TurnContext,
    facts: list[dict] | None = None,
    episodes: list[dict] | None = None,
    loops: list[dict] | None = None,
    signals: list[dict] | None = None,
    intimate: list[dict] | None = None,
    provenance: str = "relationship_v2.services.dossier",
    now: datetime | None = None,
) -> Dossier:
    """Render the debug view. Pure; never raises on row content."""
    if not provenance:
        raise ValueError("provenance required")
    snap = ctx.snapshot
    sections = [
        DossierSection(
            name="relationship",
            lines=_take([
                f"lifecycle={snap.lifecycle.value}",
                f"familiarity={snap.familiarity}",
                f"comfort={snap.comfort}",
                f"buyer={snap.buyer_class}",
                f"absence_days={snap.absence_days}",
                f"boundaries={'yes' if snap.has_boundaries else 'no'}",
            ]),
        ),
        DossierSection(
            name="person",
            lines=_take([
                _line(f"{f.get('memory_key')}={f.get('value')}") for f in facts or []
            ]),
        ),
        DossierSection(
            name="open_loops",
            lines=_take([
                _line(f"{loop.get('status')}: {loop.get('description')}")
                for loop in loops or []
            ]),
        ),
        DossierSection(
            name="episodes",
            lines=_take([
                _line(f"{e.get('episode_type')}: {e.get('summary')}") for e in episodes or []
            ]),
        ),
        DossierSection(
            name="patterns",
            lines=_take([
                _line(
                    f"{s.get('topic')}/{s.get('behavior')} "
                    f"{s.get('polarity')} x{s.get('evidence_count')}"
                )
                for s in signals or []
            ]),
        ),
        DossierSection(
            name="intimate",
            lines=_take([
                _line(f"{r.get('kind')}: {r.get('signal')}") for r in intimate or []
            ]),
        ),
        DossierSection(
            name="commerce",
            lines=_take(
                [
                    _line(
                        f"status={ctx.commerce.purchase_status} "
                        f"count={ctx.commerce.purchase_count} "
                        f"active_offer={ctx.commerce.active_offer}"
                    )
                ]
                if ctx.commerce is not None
                else ["commerce unavailable (stale)"]
            ),
        ),
        DossierSection(
            name="recall",
            lines=_take([_line(f"recall: {ref}") for ref in ctx.recall_refs]),
        ),
    ]
    return Dossier(
        creator_id=ctx.creator_id,
        user_id=ctx.user_id,
        relationship_id=str(ctx.relationship_id),
        sections=sections,
        generated_at=now or datetime.now(UTC),
        provenance=provenance,
    )
