"""Static segment presets — predefined audience definitions.

Presets are STATIC PYTHON DEFINITIONS. They are NOT database records.
When activated, they create normal editable fan_segments records.

Every preset MUST pass the existing rule validation/compiler.
"""

from __future__ import annotations

from typing import Any

from segments.evaluator import validate_rules
from segments.models import FieldRule, RuleGroup, SegmentRule


# ── Preset metadata ────────────────────────────────────────────────────


class SegmentPreset:
    """A static segment preset definition."""

    def __init__(
        self,
        id: str,
        name: str,
        description: str,
        category: str,
        rules: SegmentRule,
    ) -> None:
        self.id = id
        self.name = name
        self.description = description
        self.category = category
        self.rules = rules

    def validate(self) -> tuple[bool, str | None]:
        """Validate preset rules against the field registry."""
        return validate_rules(self.rules)

    def to_dict(self) -> dict[str, Any]:
        """Serialize preset for API consumption."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "rules": self.rules.model_dump(),
        }


# ── Preset definitions ─────────────────────────────────────────────────


PRESETS: list[SegmentPreset] = []


def _register(preset: SegmentPreset) -> None:
    PRESETS.append(preset)


# ── Engagement presets ─────────────────────────────────────────────────


_register(SegmentPreset(
    id="recently_active",
    name="Recently Active",
    description="Fans who sent an inbound message in the last 3 days",
    category="engagement",
    rules=RuleGroup(operator="AND", children=[
        FieldRule(field="last_inbound_days_ago", operator="<=", value=3),
    ]),
))


_register(SegmentPreset(
    id="inactive_fans",
    name="Inactive Fans",
    description="Fans who haven't been seen in 30+ days",
    category="engagement",
    rules=RuleGroup(operator="AND", children=[
        FieldRule(field="last_seen_days_ago", operator=">=", value=30),
    ]),
))


_register(SegmentPreset(
    id="high_message_count",
    name="High Message Count",
    description="Fans with 50+ total messages",
    category="engagement",
    rules=RuleGroup(operator="AND", children=[
        FieldRule(field="message_count", operator=">=", value=50),
    ]),
))


# ── Purchase presets ───────────────────────────────────────────────────


_register(SegmentPreset(
    id="vip",
    name="VIP Fans",
    description="Fans at the VIP funnel stage",
    category="purchase",
    rules=RuleGroup(operator="AND", children=[
        FieldRule(field="funnel_stage", operator="=", value="vip"),
    ]),
))


_register(SegmentPreset(
    id="high_spenders",
    name="High Spenders",
    description="Fans who spent $500+ (50000 minor units)",
    category="purchase",
    rules=RuleGroup(operator="AND", children=[
        FieldRule(field="total_spend_minor", operator=">=", value=50000),
    ]),
))


_register(SegmentPreset(
    id="repeat_buyers",
    name="Repeat Buyers",
    description="Fans with 2+ purchases",
    category="purchase",
    rules=RuleGroup(operator="AND", children=[
        FieldRule(field="purchase_count", operator=">=", value=2),
    ]),
))


_register(SegmentPreset(
    id="purchased_fans",
    name="Purchased Fans",
    description="Fans who have made at least one purchase",
    category="purchase",
    rules=RuleGroup(operator="AND", children=[
        FieldRule(field="has_purchased", operator="=", value=True),
    ]),
))


_register(SegmentPreset(
    id="never_purchased",
    name="Never Purchased",
    description="Fans who have never made a purchase",
    category="purchase",
    rules=RuleGroup(operator="AND", children=[
        FieldRule(field="has_purchased", operator="=", value=False),
    ]),
))


# ── Lifecycle presets ──────────────────────────────────────────────────


_register(SegmentPreset(
    id="new_fans",
    name="New Fans",
    description="Fans discovered in the last 7 days",
    category="lifecycle",
    rules=RuleGroup(operator="AND", children=[
        FieldRule(field="first_seen_days_ago", operator="<=", value=7),
    ]),
))


_register(SegmentPreset(
    id="at_risk_buyers",
    name="At-Risk Buyers",
    description="Fans who purchased but haven't been seen in 14+ days",
    category="lifecycle",
    rules=RuleGroup(operator="AND", children=[
        FieldRule(field="last_seen_days_ago", operator=">=", value=14),
        FieldRule(field="has_purchased", operator="=", value=True),
    ]),
))


# ── Operations presets ─────────────────────────────────────────────────


_register(SegmentPreset(
    id="pending_attention",
    name="Pending Operator Attention",
    description="Fans with pending items in the operator queue",
    category="operations",
    rules=RuleGroup(operator="AND", children=[
        FieldRule(field="has_pending_queue", operator="=", value=True),
    ]),
))


_register(SegmentPreset(
    id="vault_engaged",
    name="Vault Engaged",
    description="Fans who have received vault media deliveries",
    category="operations",
    rules=RuleGroup(operator="AND", children=[
        FieldRule(field="has_delivery", operator="=", value=True),
    ]),
))


# ── Public API ─────────────────────────────────────────────────────────


def list_presets() -> list[dict[str, Any]]:
    """Return all presets as dicts for API consumption."""
    return [p.to_dict() for p in PRESETS]


def get_preset(preset_id: str) -> SegmentPreset | None:
    """Return a preset by ID, or None if not found."""
    for p in PRESETS:
        if p.id == preset_id:
            return p
    return None
