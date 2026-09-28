"""Segment rule AST models.

The rule representation is a structured/typed JSON tree.  Clients supply
FieldRule | RuleGroup nodes; the server compiles them to parameterized SQL.
Raw SQL is never accepted.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class FieldRule(BaseModel):
    """A single field condition: field operator value."""

    type: Literal["rule"] = "rule"
    field: str
    operator: str
    value: Any
    negated: bool = False


class RuleGroup(BaseModel):
    """Boolean composition of rules (AND / OR)."""

    type: Literal["group"] = "group"
    operator: Literal["AND", "OR"]
    children: list[FieldRule | RuleGroup] = Field(default_factory=list)
    negated: bool = False

    @model_validator(mode="after")
    def _non_empty_children(self) -> RuleGroup:
        if not self.children:
            raise ValueError("Rule group must have at least one child")
        return self


# The root rule type is always a RuleGroup (single rules are wrapped).
SegmentRule = RuleGroup


# ── API request / response models ──────────────────────────────────────


class SegmentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=500)
    rules: SegmentRule


class SegmentUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    rules: SegmentRule | None = None


class SegmentPreviewRequest(BaseModel):
    rules: SegmentRule


# ── Explanation models ────────────────────────────────────────────────


class FieldEvaluation(BaseModel):
    """Result of evaluating a single FieldRule against a user."""

    field: str
    field_label: str
    operator: str
    value: Any
    satisfied: bool
    actual_value: Any | None = None
    negated: bool = False


class RuleEvaluation(BaseModel):
    """Result of evaluating a RuleGroup (recursive)."""

    type: Literal["group"] = "group"
    operator: Literal["AND", "OR"]
    children: list[FieldEvaluation | RuleEvaluation] = Field(default_factory=list)
    satisfied: bool = False
    negated: bool = False


class SegmentExplanation(BaseModel):
    """Full explanation of why a user matches (or doesn't match) a segment."""

    user_id: int
    segment_id: int
    segment_name: str
    is_member: bool
    rule_evaluation: RuleEvaluation
    satisfied_count: int
    total_count: int


# ── Helpers ────────────────────────────────────────────────────────────


def flatten_rules(node: SegmentRule | FieldRule) -> list[FieldRule]:
    """Yield all FieldRule leaves in depth-first order."""
    if isinstance(node, FieldRule):
        yield node
    elif isinstance(node, RuleGroup):
        for child in node.children:
            yield from flatten_rules(child)
