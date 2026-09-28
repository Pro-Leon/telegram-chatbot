"""Segment rule → parameterized SQL compiler.

Security-critical component.  Every rule passes through the field registry;
no raw SQL is ever accepted or generated from client input.
"""

from __future__ import annotations

from typing import Any

from db.postgres import get_pool
from segments.fields import FIELDS, validate_rule
from segments.models import (
    FieldEvaluation,
    FieldRule,
    RuleEvaluation,
    RuleGroup,
    SegmentRule,
    flatten_rules,
)


class CompilationError(Exception):
    """Raised when a rule tree cannot be compiled to valid SQL."""


class SQLCompiler:
    """Compile a SegmentRule tree into a parameterized SQL WHERE clause.

    Parameter numbering is sequential across the entire tree: $1, $2, $3, ...
    The ``creator_id`` is injected as $1 for creator-scoped fields and
    consumed by their generators via ``$PH`` markers.
    """

    def __init__(self, creator_id: int) -> None:
        self.creator_id = creator_id
        self.params: list[Any] = []
        # creator_id is always $1 — pre-inject it
        self.params.append(creator_id)
        self._next_idx = 2  # next available $N

    def _claim_param(self, value: Any) -> str:
        """Append a parameter and return its ``$N`` placeholder."""
        idx = self._next_idx
        self._next_idx += 1
        self.params.append(value)
        return f"${idx}"

    def _replace_ph(self, sql: str, param_values: list[Any]) -> str:
        """Replace ``$PH`` placeholders with sequential ``$N`` numbers.

        Each ``$PH`` in *sql* is replaced in left-to-right order with the
        corresponding value from *param_values*.
        """
        for val in param_values:
            sql = sql.replace("$PH", self._claim_param(val), 1)
        return sql

    def compile(self, node: SegmentRule | FieldRule) -> str:
        """Compile a rule node into a SQL WHERE-clause fragment."""
        if isinstance(node, FieldRule):
            return self._compile_field(node)
        if isinstance(node, RuleGroup):
            return self._compile_group(node)
        raise CompilationError(f"Unknown node type: {type(node).__name__}")

    def _compile_group(self, group: RuleGroup) -> str:
        if not group.children:
            raise CompilationError("Empty rule group")
        if group.operator not in ("AND", "OR"):
            raise CompilationError(f"Invalid group operator: '{group.operator}'")

        parts = [self.compile(child) for child in group.children]
        joiner = f" {group.operator} "
        inner = joiner.join(parts)
        # Wrap in parens for correctness when nested
        if len(parts) > 1:
            inner = f"({inner})"
        # Apply NOT if negated
        if group.negated:
            inner = f"NOT ({inner})"
        return inner

    def _compile_field(self, rule: FieldRule) -> str:
        field_spec = FIELDS.get(rule.field)
        if field_spec is None:
            raise CompilationError(f"Unknown field: '{rule.field}'")

        if rule.operator not in field_spec.operators:
            raise CompilationError(
                f"Invalid operator '{rule.operator}' for field '{rule.field}'"
            )

        if field_spec.sql_generator is None:
            raise CompilationError(
                f"Field '{rule.field}' has no SQL generator"
            )

        # Call the generator — it returns (sql_with_PH, param_values)
        sql, param_count = field_spec.sql_generator(
            "u", self.params, rule.operator, rule.value
        )

        # Replace $PH placeholders with actual $N numbers
        # The generator already appended values to self.params;
        # now we need to map those values to $N.
        # But wait — the generator appended values to self.params directly.
        # We need to extract the newly added values.
        # The generator appended `param_count` values at the end.
        new_values = self.params[-param_count:] if param_count > 0 else []
        self.params = self.params[:-param_count] if param_count > 0 else self.params

        # Now replace $PH with proper $N
        for val in new_values:
            sql = sql.replace("$PH", self._claim_param(val), 1)

        # Apply NOT if negated
        if rule.negated:
            sql = f"NOT ({sql})"

        return sql


def compile_rule(creator_id: int, rule: SegmentRule) -> tuple[str, list[Any]]:
    """Compile a SegmentRule into (where_clause, params).

    The where_clause uses ``$N``-style parameter placeholders.
    The returned params list is ordered to match.
    """
    compiler = SQLCompiler(creator_id)
    where = compiler.compile(rule)
    return where, compiler.params


def validate_rules(rule: SegmentRule) -> tuple[bool, str | None]:
    """Validate all FieldRules in a rule tree against the registry.

    Returns (is_valid, error_message_or_none).
    Fails closed on any invalid input.
    """
    if isinstance(rule, FieldRule):
        return validate_rule(rule.model_dump())
    if isinstance(rule, RuleGroup):
        if not rule.children:
            return False, "Rule group must have at least one child"
        for child in rule.children:
            ok, err = validate_rules(child)
            if not ok:
                return ok, err
        return True, None
    return False, f"Unknown rule type: {type(rule).__name__}"


# ── Membership evaluation ──────────────────────────────────────────────


async def get_segment_members(
    creator_id: int,
    rules: SegmentRule,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Return matching fans for a segment."""
    where_clause, params = compile_rule(creator_id, rules)
    limit_idx = len(params) + 1
    offset_idx = len(params) + 2
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT u.id, u.username, u.first_name, u.last_seen, u.message_count
            FROM users u
            WHERE {where_clause}
            ORDER BY u.last_seen DESC NULLS LAST
            LIMIT ${limit_idx} OFFSET ${offset_idx}
            """,
            *params, limit, offset,
        )
    return [dict(r) for r in rows]


async def get_segment_count(creator_id: int, rules: SegmentRule) -> int:
    """Return count of matching fans."""
    where_clause, params = compile_rule(creator_id, rules)
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            f"SELECT COUNT(*) FROM users u WHERE {where_clause}",
            *params,
        )
    return count or 0


async def check_user_in_segment(
    creator_id: int,
    rules: SegmentRule,
    user_id: int,
) -> bool:
    """Check if a specific user belongs to a segment."""
    where_clause, params = compile_rule(creator_id, rules)
    uid_idx = len(params) + 1
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM users u WHERE u.id = ${uid_idx} AND {where_clause})",
            *params, user_id,
        )
    return bool(exists)


# ── Explanation engine ────────────────────────────────────────────────


async def _fetch_user_field(user_id: int, field: str) -> Any:
    """Fetch a single field value for a user from the database.

    Returns the raw value, or None if the user doesn't exist.
    For subquery-based fields, this runs the appropriate SQL.
    """
    simple_fields = {
        "funnel_stage", "is_blocked", "do_not_auto_reply", "message_count",
        "first_name", "last_name", "username", "has_username",
    }
    if field in simple_fields:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {field} FROM users WHERE id = $1", user_id,
            )
        return row[field] if row else None
    return None


async def _evaluate_field(
    creator_id: int,
    rule: FieldRule,
    user_id: int,
) -> FieldEvaluation:
    """Evaluate a single FieldRule against a specific user.

    Compiles the rule to SQL, wraps it in EXISTS(...), and checks if the
    user satisfies the condition.
    """
    field_spec = FIELDS.get(rule.field)
    field_label = field_spec.label if field_spec else rule.field
    actual_value: Any = None

    try:
        where_clause, params = compile_rule(
            creator_id,
            RuleGroup(operator="AND", children=[rule]),
        )
        uid_idx = len(params) + 1
        pool = await get_pool()
        async with pool.acquire() as conn:
            satisfied = await conn.fetchval(
                f"SELECT EXISTS(SELECT 1 FROM users u WHERE u.id = ${uid_idx} AND {where_clause})",
                *params, user_id,
            )
            satisfied = bool(satisfied)

            if not satisfied and field_spec:
                actual_value = await _fetch_user_field(user_id, rule.field)
    except Exception:
        satisfied = False

    # Apply negation if needed
    if rule.negated:
        satisfied = not satisfied

    return FieldEvaluation(
        field=rule.field,
        field_label=field_label,
        operator=rule.operator,
        value=rule.value,
        satisfied=satisfied,
        actual_value=actual_value,
        negated=rule.negated,
    )


async def explain_rule(
    creator_id: int,
    rules: SegmentRule,
    user_id: int,
) -> RuleEvaluation:
    """Walk the rule tree and evaluate each condition against a user.

    Returns a structured evaluation tree showing which conditions
    the user satisfies and which they don't.
    """
    if isinstance(rules, FieldRule):
        ev = await _evaluate_field(creator_id, rules, user_id)
        return RuleEvaluation(
            operator="AND",
            children=[ev],
            satisfied=ev.satisfied,
            negated=rules.negated,
        )

    children: list[FieldEvaluation | RuleEvaluation] = []
    for child in rules.children:
        child_eval = await explain_rule(creator_id, child, user_id)
        children.append(child_eval)

    if rules.operator == "AND":
        satisfied = all(c.satisfied for c in children)
    else:
        satisfied = any(c.satisfied for c in children)

    # Apply negation if needed
    if rules.negated:
        satisfied = not satisfied

    return RuleEvaluation(
        operator=rules.operator,
        children=children,
        satisfied=satisfied,
        negated=rules.negated,
    )


def _count_evaluation_leaves(eval_node: RuleEvaluation | FieldEvaluation) -> tuple[int, int]:
    """Count satisfied and total leaf conditions in an evaluation tree."""
    if isinstance(eval_node, FieldEvaluation):
        return (1, 1) if eval_node.satisfied else (0, 1)
    satisfied = 0
    total = 0
    for child in eval_node.children:
        s, t = _count_evaluation_leaves(child)
        satisfied += s
        total += t
    return satisfied, total


async def explain_user_segments(
    creator_id: int,
    user_id: int,
) -> list[dict]:
    """Explain all enabled segments for a user.

    Returns a list of dicts with segment info and evaluation results.
    """
    from db import segments as sdb

    segments = await sdb.list_segments(creator_id, enabled_only=True)
    results = []
    for seg in segments:
        rules_data = seg.get("rules")
        if not rules_data:
            continue
        try:
            rule = RuleGroup.model_validate(rules_data)
            eval_tree = await explain_rule(creator_id, rule, user_id)
            satisfied, total = _count_evaluation_leaves(eval_tree)
            results.append({
                "segment_id": seg["id"],
                "segment_name": seg["name"],
                "is_member": eval_tree.satisfied,
                "satisfied_count": satisfied,
                "total_count": total,
                "rule_evaluation": eval_tree.model_dump(),
            })
        except Exception:
            continue
    return results


async def get_segment_stats(
    creator_id: int,
    segment_id: int,
) -> dict | None:
    """Get field-level statistics for a segment.

    For each leaf rule, computes how many users satisfy that condition.
    """
    from db import segments as sdb

    segment = await sdb.get_segment(creator_id, segment_id)
    if not segment:
        return None

    rules_data = segment.get("rules")
    if not rules_data:
        return {
            "segment_id": segment_id,
            "member_count": segment.get("member_count", 0),
            "last_evaluated_at": segment.get("last_evaluated_at"),
            "field_stats": [],
        }

    try:
        rule = RuleGroup.model_validate(rules_data)
    except Exception:
        return {
            "segment_id": segment_id,
            "member_count": segment.get("member_count", 0),
            "last_evaluated_at": segment.get("last_evaluated_at"),
            "field_stats": [],
        }

    field_stats = []
    for leaf in flatten_rules(rule):
        field_spec = FIELDS.get(leaf.field)
        field_label = field_spec.label if field_spec else leaf.field
        try:
            where_clause, params = compile_rule(
                creator_id,
                RuleGroup(operator="AND", children=[leaf]),
            )
            pool = await get_pool()
            async with pool.acquire() as conn:
                count = await conn.fetchval(
                    f"SELECT COUNT(*) FROM users u WHERE {where_clause}",
                    *params,
                )
            field_stats.append({
                "field": leaf.field,
                "field_label": field_label,
                "operator": leaf.operator,
                "value": leaf.value,
                "matching_count": count or 0,
            })
        except Exception:
            field_stats.append({
                "field": leaf.field,
                "field_label": field_label,
                "operator": leaf.operator,
                "value": leaf.value,
                "matching_count": 0,
            })

    return {
        "segment_id": segment_id,
        "member_count": segment.get("member_count", 0),
        "last_evaluated_at": segment.get("last_evaluated_at"),
        "field_stats": field_stats,
    }
