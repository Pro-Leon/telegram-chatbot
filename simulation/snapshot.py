"""DecisionSnapshotBuilder — minimal builder producing valid decision_snapshot for build_optimization_input.

Covers required fields only, deterministic.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from simulation.world import (
    SyntheticContent,
    SyntheticConversationContext,
    SyntheticCreator,
    SyntheticFan,
)


def build_decision_snapshot(
    *,
    creator: SyntheticCreator,
    fan: SyntheticFan,
    content: SyntheticContent,
    conversation: SyntheticConversationContext | None = None,
    evaluated_at: datetime | None = None,
    definition_id: int | None = None,
    version: int | None = None,
    stable_key: str | None = None,
    policy_version: str = "v1",
    history_state: Any | None = None,
    conversation_state: Any | None = None,
) -> dict[str, Any]:
    """Build a deterministic decision_snapshot dict compatible with build_optimization_input:748.

    Required: eligible[0] as candidate dict with definition_id, version, stable_key, price_minor etc.
    No DB/TG/Dropfans calls.
    """
    if not isinstance(creator, SyntheticCreator):
        raise ValueError("creator must be SyntheticCreator")
    if not isinstance(fan, SyntheticFan):
        raise ValueError("fan must be SyntheticFan")
    if not isinstance(content, SyntheticContent):
        raise ValueError("content must be SyntheticContent")
    # Derive deterministic definition identity from content + fan if not overridden
    # Use hash of content_id for stable definition_id that fits creator scope
    import hashlib

    if definition_id is None:
        h = (
            int(hashlib.sha256(f"{content.content_id}:{fan.fan_id}".encode()).hexdigest()[:8], 16)
            % 90000
            + 1
        )
        definition_id = int(h)
    if version is None:
        version = 1
    if stable_key is None:
        stable_key = f"sim_{creator.creator_id}_{definition_id}"

    eligible_entry = {
        "definition_id": int(definition_id),
        "version": int(version),
        "stable_key": str(stable_key),
        "offer_type": str(content.offer_type),
        "price_minor": int(content.price_minor),
        "currency": str(content.currency),
        "vault_ids": list(content.vault_ids),
        "canonical_vault_item_ids": list(content.vault_ids),
        "mapped_drop_ids": list(content.mapped_drop_ids),
    }
    conv = conversation or SyntheticConversationContext()
    # Derive conversation lifecycle from Phase 7 state if provided (HOT/WARM/COLD → hot/warm/cold)
    if conversation_state is not None:
        try:
            derived = getattr(conversation_state, "derived_label", None)
            if isinstance(derived, str) and derived.strip():
                conv = SyntheticConversationContext(
                    lifecycle=derived.strip().lower(),
                    current_topic=conv.current_topic,
                    recent_topics=conv.recent_topics,
                    open_threads=conv.open_threads,
                )
            else:
                # fallback: try dict access
                d = conversation_state.get("derived_label") if isinstance(conversation_state, dict) else None
                if isinstance(d, str) and d.strip():
                    conv = SyntheticConversationContext(
                        lifecycle=d.strip().lower(),
                        current_topic=conv.current_topic,
                        recent_topics=conv.recent_topics,
                        open_threads=conv.open_threads,
                    )
        except Exception:
            pass

    # Derive observable fan/history counts from HistoryState if provided
    fan_recent_offer_count = 0
    fan_recent_rejected = 0
    fan_last_offer_at = None
    fan_last_purchase_at = None
    fan_purchase_count = 0
    fan_recent_purchase_count = 0
    hist_total = 0
    hist_recent = 0
    hist_declined_total = 0
    hist_recent_declined = 0
    hist_last_offer_at = None
    hist_state_counts: dict[str, int] = {}
    if history_state is not None:
        try:
            # HistoryState dataclass attributes
            hist_total = int(getattr(history_state, "total_offer_count", 0) or 0)
            hist_recent = int(getattr(history_state, "recent_offer_count", 0) or 0)
            hist_recent_declined = int(getattr(history_state, "recent_declined_count", 0) or 0)
            # last offer/purchase ISO
            lo = getattr(history_state, "last_offer_at", None)
            lp = getattr(history_state, "last_purchase_at", None)
            # events list for totals
            events = getattr(history_state, "events", None)
            if events is None and isinstance(history_state, dict):
                events = history_state.get("events")
                hist_total = int(history_state.get("total_offer_count", hist_total) or 0)
                hist_recent = int(history_state.get("recent_offer_count", hist_recent) or 0)
                hist_recent_declined = int(history_state.get("recent_declined_count", hist_recent_declined) or 0)
                lo = history_state.get("last_offer_at", lo)
                lp = history_state.get("last_purchase_at", lp)
            # handle ISO strings vs datetime
            def _iso_or_none(v: Any) -> str | None:
                if v is None:
                    return None
                if isinstance(v, str) and v.strip():
                    return v.strip()
                if isinstance(v, datetime):
                    return v.isoformat()
                return None

            fan_last_offer_at = _iso_or_none(lo)
            fan_last_purchase_at = _iso_or_none(lp)
            hist_last_offer_at = fan_last_offer_at
            # compute counts from events if available
            if isinstance(events, (list, tuple)):
                purchased_total = 0
                declined_total = 0
                for e in events:
                    if not isinstance(e, dict):
                        continue
                    outcome = e.get("outcome")
                    if outcome == "PURCHASED":
                        purchased_total += 1
                    elif outcome == "DECLINED":
                        declined_total += 1
                # total declined for history
                hist_declined_total = declined_total
                fan_purchase_count = purchased_total
                # recent purchased = recent_offer - recent_declined
                fan_recent_purchase_count = max(0, hist_recent - hist_recent_declined)
                if purchased_total > 0 or declined_total > 0:
                    if purchased_total > 0:
                        hist_state_counts["PURCHASED"] = purchased_total
                    if declined_total > 0:
                        hist_state_counts["DECLINED"] = declined_total
                else:
                    # fallback if events not detailed
                    fan_purchase_count = max(0, hist_total - hist_recent_declined) if hist_total else 0
                    fan_recent_purchase_count = max(0, hist_recent - hist_recent_declined)
            else:
                fan_purchase_count = max(0, hist_total - hist_recent_declined) if hist_total else 0
                fan_recent_purchase_count = max(0, hist_recent - hist_recent_declined)
                hist_declined_total = hist_recent_declined if hist_total else 0
            fan_recent_offer_count = int(hist_recent)
            fan_recent_rejected = int(hist_recent_declined)
        except Exception:
            pass

    snapshot: dict[str, Any] = {
        "eligible": [eligible_entry],
        "ineligible": [],
        "selected": dict(eligible_entry),
        "ranking": {
            "policy_version": str(policy_version),
            "ranked_order": [int(definition_id)],
            "factors": {str(definition_id): []},
        },
        "conversation": {
            "lifecycle": conv.lifecycle,
            "current_topic": conv.current_topic,
            "recent_topics": list(conv.recent_topics),
            "open_threads": list(conv.open_threads),
        },
        "fan": {
            "creator_id": int(creator.creator_id),
            "user_id": int(fan.fan_id),
            "purchase_count": int(fan_purchase_count),
            "total_spend_minor": 0,
            "purchased_vault_ids": [],
            "delivered_vault_ids": [],
            "recent_offer_count": int(fan_recent_offer_count),
            "recent_rejected_offer_count": int(fan_recent_rejected),
            "recent_offered_vault_ids": [],
            "recent_purchase_count": int(fan_recent_purchase_count),
            "recent_spend_minor": 0,
            "average_order_value_minor": None,
            "highest_purchase_minor": None,
            "last_purchase_at": fan_last_purchase_at,
            "last_offer_at": fan_last_offer_at,
            "currency": "USD",
        },
        "history": {
            "creator_id": int(creator.creator_id),
            "user_id": int(fan.fan_id),
            "total_offer_count": int(hist_total),
            "recent_offer_count": int(hist_recent),
            "declined_offer_count": int(hist_declined_total),
            "recent_declined_offer_count": int(hist_recent_declined),
            "state_counts": dict(hist_state_counts),
            "has_active_offer": False,
            "active_offer_count": 0,
            "offered_vault_sets": [],
            "active_vault_sets": [],
            "null_snapshot_count": 0,
            "definition_identity_available": False,
        },
    }
    return snapshot


def ledger_row_from_opportunity(
    opportunity: Any,
    *,
    sealed_offer_id: int | None = None,
    outcome_state: str = "PENDING",
    exposure_state: str = "NONE",
) -> dict[str, Any]:
    """Convert SyntheticOpportunity to ledger-row-shaped dict for build_optimization_input.

    Produces minimal required ledger columns: creator_id, opportunity_id, user_id, generation_id, evaluated_at, decision_snapshot JSON, selected_*, etc.
    Does not write DB. For SENT/PURCHASED cases caller will set exposure/outcome fields.
    """
    from simulation.world import SyntheticOpportunity

    if not isinstance(opportunity, SyntheticOpportunity):
        raise ValueError("opportunity must be SyntheticOpportunity")
    selected = opportunity.decision_snapshot.get("selected") or {}
    return {
        "creator_id": int(opportunity.creator_id),
        "opportunity_id": int(opportunity.opportunity_id),
        "user_id": int(opportunity.fan_id),
        "generation_id": str(opportunity.generation_id),
        "evaluated_at": opportunity.evaluated_at,
        "decision_snapshot": json.dumps(opportunity.decision_snapshot, sort_keys=True),
        "selected_definition_id": int(selected.get("definition_id"))
        if isinstance(selected.get("definition_id"), int)
        else None,
        "selected_version": int(selected.get("version"))
        if isinstance(selected.get("version"), int)
        else None,
        "selected_stable_key": str(selected.get("stable_key"))
        if selected.get("stable_key")
        else None,
        "decision_status": "SEALED" if sealed_offer_id is not None else "DECIDED",
        "sealed_offer_id": sealed_offer_id,
        "outcome_state": outcome_state,
        "exposure_state": exposure_state,
        "synthetic": "SYNTHETIC_P356_FIXTURE",
    }


__all__ = ["build_decision_snapshot", "ledger_row_from_opportunity"]
