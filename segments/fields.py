"""Segment field registry — trusted server-side definitions of valid fields.

Every segment rule must pass through this registry.  The client can NEVER
provide raw SQL, column names, table names, or arbitrary operators.
All SQL identifiers and operators come exclusively from this file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# ── Operator allowlist ─────────────────────────────────────────────────

Comparisons = frozenset({">=", "<=", ">", "<", "=", "!="})
SetOps = frozenset({"=", "!=", "in", "not_in"})
NullOps = frozenset({"=", "!=", "is_null"})

# ── Field specification ────────────────────────────────────────────────

# sql_generator signature: (alias: str, params: list[Any]) -> (sql_fragment: str, params_added: int)
#   alias: the users table alias (always "u")
#   params: mutable list — append parameter values here
#   Returns (sql, count_of_new_params)
#   The sql uses $PH as a placeholder for parameter numbers.
#   The evaluator replaces $PH with actual $N numbers after each field.
FieldGenerator = Callable[[str, list[Any]], tuple[str, int]]


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    category: str
    operators: frozenset[str]
    value_type: str  # "int", "str", "bool", "list_int", "list_str", "none"
    value_options: tuple[str, ...] = ()
    sql_generator: FieldGenerator | None = None
    description: str = ""


# ── SQL generation helpers ─────────────────────────────────────────────

def _cmp_col(alias: str, col: str, params: list, op: str, value: Any) -> tuple[str, int]:
    """Simple column comparison."""
    params.append(value)
    return (f"{alias}.{col} {op} $PH", 1)


def _bool_col(alias: str, col: str, params: list, op: str, value: Any) -> tuple[str, int]:
    """Boolean column comparison."""
    if op not in ("=", "!="):
        raise ValueError(f"Operator '{op}' not supported for boolean field")
    params.append(bool(value))
    return (f"{alias}.{col} {op} $PH", 1)


def _days_ago(alias: str, col: str, params: list, op: str, value: Any) -> tuple[str, int]:
    """Days since a timestamp column."""
    if not isinstance(value, int):
        raise ValueError(f"Value must be an integer (days), got {type(value).__name__}")
    params.append(value)
    sql = f"EXTRACT(DAY FROM NOW() - {alias}.{col})::int {op} $PH"
    return (sql, 1)


def _subquery_count(
    alias: str, table: str, where_extra: str, params: list, op: str, value: Any
) -> tuple[str, int]:
    """Scalar subquery COUNT(*) with optional extra WHERE clause."""
    extra = f" AND {where_extra}" if where_extra else ""
    params.append(value)
    sql = (
        f"(SELECT COUNT(*) FROM {table} WHERE {table}.user_id = {alias}.id{extra}) {op} $PH"
    )
    return (sql, 1)


def _subquery_exists(
    alias: str, table: str, where_extra: str, params: list, op: str, value: Any
) -> tuple[str, int]:
    """EXISTS / NOT EXISTS subquery."""
    if op not in ("=", "!="):
        raise ValueError(f"Operator '{op}' not supported for boolean existence field")
    extra = f" AND {where_extra}" if where_extra else ""
    exists_sql = f"EXISTS(SELECT 1 FROM {table} WHERE {table}.user_id = {alias}.id{extra})"
    if op == "!=":
        return (f"NOT {exists_sql}", 0)
    return (exists_sql, 0)


def _tag_exists(
    alias: str, params: list, op: str, value: Any
) -> tuple[str, int]:
    """Check if fan has a specific tag (case-insensitive)."""
    if op not in ("=", "!="):
        raise ValueError(f"Operator '{op}' not supported for has_tag")
    params.append(str(value).lower())
    sql = (
        f"EXISTS(SELECT 1 FROM conversation_tag_assignments a "
        f"JOIN conversation_tags t ON t.id = a.tag_id "
        f"WHERE a.user_id = {alias}.id AND LOWER(t.name) = $PH)"
    )
    if op == "!=":
        return (f"NOT {sql}", 1)
    return (sql, 1)


def _tag_count(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    """Count of tags on a fan."""
    params.append(value)
    sql = (
        f"(SELECT COUNT(*) FROM conversation_tag_assignments "
        f"WHERE user_id = {alias}.id) {op} $PH"
    )
    return (sql, 1)


def _in_list(alias: str, col: str, params: list, op: str, value: Any) -> tuple[str, int]:
    """IN / NOT IN list comparison."""
    if op not in ("in", "not_in"):
        raise ValueError(f"Operator '{op}' not supported for list field")
    if not isinstance(value, list) or not value:
        raise ValueError("Value must be a non-empty list")
    params.append(value)
    pg_op = "IN" if op == "in" else "NOT IN"
    sql = f"{alias}.{col} {pg_op} $PH"
    return (sql, 1)


def _in_list_subquery(
    alias: str, table: str, col: str, params: list, op: str, value: Any
) -> tuple[str, int]:
    """IN / NOT IN list via subquery for creator-scoped fields."""
    if op not in ("in", "not_in"):
        raise ValueError(f"Operator '{op}' not supported for list field")
    if not isinstance(value, list) or not value:
        raise ValueError("Value must be a non-empty list")
    params.append(value)
    pg_op = "IN" if op == "in" else "NOT IN"
    sql = (
        f"(SELECT {col} FROM {table} WHERE {table}.user_id = {alias}.id) {pg_op} $PH"
    )
    return (sql, 1)


def _creator_scoped_count(
    alias: str, table: str, creator_col: str, params: list, extra_where: str = ""
) -> tuple[str, int]:
    """COUNT subquery scoped to creator_id. creator_id must be in params before calling."""
    extra = f" AND {extra_where}" if extra_where else ""
    sql = (
        f"(SELECT COUNT(*) FROM {table} "
        f"WHERE {table}.user_id = {alias}.id "
        f"AND {table}.{creator_col} = $PH{extra})"
    )
    return (sql, 1)


# ── Field registry ─────────────────────────────────────────────────────

FIELDS: dict[str, FieldSpec] = {}


def _register(spec: FieldSpec) -> None:
    FIELDS[spec.name] = spec


# ── User direct fields ─────────────────────────────────────────────────

_register(FieldSpec(
    name="funnel_stage", label="Funnel Stage", category="User",
    operators=SetOps, value_type="str",
    value_options=("new", "engaged", "paying", "vip", "churned"),
    sql_generator=lambda a, p, op="=", v="": _cmp_col(a, "funnel_stage", p, op, v),
    description="Current funnel stage of the fan",
))

_register(FieldSpec(
    name="is_blocked", label="Is Blocked", category="User",
    operators=frozenset({"=", "!="}), value_type="bool",
    sql_generator=lambda a, p, op="=", v=True: _bool_col(a, "is_blocked", p, op, v),
    description="Whether the fan is blocked",
))

_register(FieldSpec(
    name="do_not_auto_reply", label="Do Not Auto-Reply", category="User",
    operators=frozenset({"=", "!="}), value_type="bool",
    sql_generator=lambda a, p, op="=", v=True: _bool_col(a, "do_not_auto_reply", p, op, v),
    description="Whether auto-reply is disabled for this fan",
))

_register(FieldSpec(
    name="message_count", label="Total Messages", category="User",
    operators=Comparisons, value_type="int",
    sql_generator=lambda a, p, op=">=", v=0: _cmp_col(a, "message_count", p, op, v),
    description="Total message count (users.message_count)",
))

_register(FieldSpec(
    name="first_seen_days_ago", label="First Seen (Days Ago)", category="User",
    operators=Comparisons, value_type="int",
    sql_generator=lambda a, p, op=">=", v=0: _days_ago(a, "first_seen", p, op, v),
    description="Days since the fan was first seen",
))

_register(FieldSpec(
    name="last_seen_days_ago", label="Last Seen (Days Ago)", category="User",
    operators=Comparisons, value_type="int",
    sql_generator=lambda a, p, op=">=", v=0: _days_ago(a, "last_seen", p, op, v),
    description="Days since the fan was last seen",
))

_register(FieldSpec(
    name="has_username", label="Has Username", category="User",
    operators=frozenset({"=", "!="}), value_type="bool",
    sql_generator=lambda a, p, op="=", v=True: (
        (f"({alias_check(a)} IS NOT NULL AND {alias_check(a)} != '')", 0)
        if (op == "=" and v) or (op == "!=" and not v)
        else (f"({alias_check(a)} IS NULL OR {alias_check(a)} = '')", 0)
    ),
    description="Whether the fan has a Telegram username set",
))


def alias_check(a: str) -> str:
    return f"{a}.username"


# Patch the lambda to use a proper function
FIELDS["has_username"] = FieldSpec(
    name="has_username", label="Has Username", category="User",
    operators=frozenset({"=", "!="}), value_type="bool",
    description="Whether the fan has a Telegram username set",
)


def _gen_has_username(alias: str, params: list, op: str = "=", value: Any = True) -> tuple[str, int]:
    sql_not_null = f"({alias}.username IS NOT NULL AND {alias}.username != '')"
    if (op == "=" and value) or (op == "!=" and not value):
        return (sql_not_null, 0)
    return (f"NOT {sql_not_null}", 0)


FIELDS["has_username"] = FieldSpec(
    name="has_username", label="Has Username", category="User",
    operators=frozenset({"=", "!="}), value_type="bool",
    sql_generator=_gen_has_username,
    description="Whether the fan has a Telegram username set",
)


# ── Message activity fields ────────────────────────────────────────────

def _msg_count(alias: str, params: list, op: str, value: Any,
               direction: str | None = None) -> tuple[str, int]:
    where = f"AND direction = '{direction}'" if direction else ""
    # M7 (B6): creator-scoped. params[0] is the compiler-injected creator_id;
    # append it explicitly so multi-rule compilations stay correct.
    params.append(params[0])
    params.append(value)
    sql = (
        f"(SELECT COUNT(*) FROM messages WHERE messages.user_id = {alias}.id "
        f"AND messages.creator_id = $PH {where}) "
        f"{op} $PH"
    )
    return (sql, 2)


_register(FieldSpec(
    name="inbound_count", label="Inbound Messages", category="Messages",
    operators=Comparisons, value_type="int",
    sql_generator=lambda a, p, op=">=", v=0: _msg_count(a, p, op, v, "inbound"),
    description="Number of inbound messages from this fan",
))

_register(FieldSpec(
    name="outbound_count", label="Outbound Messages", category="Messages",
    operators=Comparisons, value_type="int",
    sql_generator=lambda a, p, op=">=", v=0: _msg_count(a, p, op, v, "outbound"),
    description="Number of outbound messages to this fan",
))

_register(FieldSpec(
    name="total_messages", label="Total Messages (Activity)", category="Messages",
    operators=Comparisons, value_type="int",
    sql_generator=lambda a, p, op=">=", v=0: _msg_count(a, p, op, v, None),
    description="Total messages (inbound + outbound) for this fan",
))


def _msg_days_ago(alias: str, params: list, op: str, value: Any,
                  direction: str) -> tuple[str, int]:
    # M7 (B6): creator-scoped (see _msg_count).
    params.append(params[0])
    params.append(value)
    sql = (
        f"(SELECT EXTRACT(DAY FROM NOW() - MAX(created_at))::int "
        f"FROM messages WHERE messages.user_id = {alias}.id "
        f"AND messages.creator_id = $PH "
        f"AND direction = '{direction}') {op} $PH"
    )
    return (sql, 2)


_register(FieldSpec(
    name="last_inbound_days_ago", label="Last Inbound (Days Ago)", category="Messages",
    operators=Comparisons, value_type="int",
    sql_generator=lambda a, p, op=">=", v=0: _msg_days_ago(a, p, op, v, "inbound"),
    description="Days since last inbound message from this fan",
))

_register(FieldSpec(
    name="last_outbound_days_ago", label="Last Outbound (Days Ago)", category="Messages",
    operators=Comparisons, value_type="int",
    sql_generator=lambda a, p, op=">=", v=0: _msg_days_ago(a, p, op, v, "outbound"),
    description="Days since last outbound message to this fan",
))


def _msg_days_ago_first(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    # M7 (B6): creator-scoped (see _msg_count).
    params.append(params[0])
    params.append(value)
    sql = (
        f"(SELECT EXTRACT(DAY FROM NOW() - MIN(created_at))::int "
        f"FROM messages WHERE messages.user_id = {alias}.id "
        f"AND messages.creator_id = $PH) {op} $PH"
    )
    return (sql, 2)


_register(FieldSpec(
    name="first_message_days_ago", label="First Message (Days Ago)", category="Messages",
    operators=Comparisons, value_type="int",
    sql_generator=_msg_days_ago_first,
    description="Days since the first message from this fan",
))


def _msg_has(alias: str, params: list, op: str, value: Any,
             direction: str) -> tuple[str, int]:
    if op not in ("=", "!="):
        raise ValueError(f"Operator '{op}' not supported for boolean message field")
    # M7 (B6): creator-scoped (see _msg_count).
    params.append(params[0])
    sql = (
        f"(SELECT COUNT(*) FROM messages WHERE messages.user_id = {alias}.id "
        f"AND messages.creator_id = $PH "
        f"AND direction = '{direction}') > 0"
    )
    if op == "!=":
        return (f"NOT {sql}", 1)
    return (sql, 1)


_register(FieldSpec(
    name="has_inbound", label="Has Inbound Messages", category="Messages",
    operators=frozenset({"=", "!="}), value_type="bool",
    sql_generator=lambda a, p, op="=", v=True: _msg_has(a, p, op, v, "inbound"),
    description="Whether the fan has sent any inbound messages",
))

_register(FieldSpec(
    name="has_outbound", label="Has Outbound Messages", category="Messages",
    operators=frozenset({"=", "!="}), value_type="bool",
    sql_generator=lambda a, p, op="=", v=True: _msg_has(a, p, op, v, "outbound"),
    description="Whether the fan has received any outbound messages",
))


def _msg_count_days(alias: str, params: list, op: str, value: Any,
                    direction: str | None = None) -> tuple[str, int]:
    """Message count within the last N days."""
    # value here is a dict {"days": N, "count": M} or we use two-param approach.
    # For simplicity, we use a dedicated field for this.
    # M7 (B6): creator-scoped (see _msg_count). Appends follow left-to-right
    # $PH order in the SQL below: creator first, then the days value.
    params.append(params[0])
    params.append(value)
    dir_filter = f"AND direction = '{direction}'" if direction else ""
    sql = (
        f"(SELECT COUNT(*) FROM messages WHERE messages.user_id = {alias}.id "
        f"AND messages.creator_id = $PH "
        f"AND created_at >= NOW() - ($PH || ' days')::INTERVAL {dir_filter}) "
    )
    return (sql, 2)


# ── Tag fields ─────────────────────────────────────────────────────────

_register(FieldSpec(
    name="has_tag", label="Has Tag", category="Tags",
    operators=frozenset({"=", "!="}), value_type="str",
    sql_generator=_tag_exists,
    description="Whether the fan has a specific tag (case-insensitive)",
))

_register(FieldSpec(
    name="tag_count", label="Tag Count", category="Tags",
    operators=Comparisons, value_type="int",
    sql_generator=_tag_count,
    description="Number of tags assigned to this fan",
))


# ── Attention fields ───────────────────────────────────────────────────

def _att_status(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    if op in ("in", "not_in"):
        if not isinstance(value, list) or not value:
            raise ValueError("Value must be a non-empty list for 'in' operator")
        params.append(value)
        pg_op = "IN" if op == "in" else "NOT IN"
        sql = (
            f"(SELECT status FROM conversation_attention "
            f"WHERE user_id = {alias}.id) {pg_op} $PH"
        )
        return (sql, 1)
    params.append(value)
    sql = (
        f"(SELECT status FROM conversation_attention "
        f"WHERE user_id = {alias}.id) {op} $PH"
    )
    return (sql, 1)


_register(FieldSpec(
    name="attention_status", label="Attention Status", category="Attention",
    operators=SetOps, value_type="str",
    value_options=("new", "reviewed"),
    sql_generator=_att_status,
    description="Current attention status of the conversation",
))


def _has_attention(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    if op not in ("=", "!="):
        raise ValueError(f"Operator '{op}' not supported for boolean attention field")
    sql = (
        f"(SELECT COUNT(*) FROM conversation_attention "
        f"WHERE user_id = {alias}.id) > 0"
    )
    if op == "!=":
        return (f"NOT {sql}", 0)
    return (sql, 0)


_register(FieldSpec(
    name="has_attention", label="Has Attention Record", category="Attention",
    operators=frozenset({"=", "!="}), value_type="bool",
    sql_generator=_has_attention,
    description="Whether the fan has a conversation_attention record",
))


def _assigned_operator(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    if op == "is_null":
        sql = (
            f"(SELECT assigned_operator_id FROM conversation_attention "
            f"WHERE user_id = {alias}.id) IS NULL"
        )
        return (sql, 0)
    params.append(value)
    sql = (
        f"(SELECT assigned_operator_id FROM conversation_attention "
        f"WHERE user_id = {alias}.id) {op} $PH"
    )
    return (sql, 1)


_register(FieldSpec(
    name="assigned_operator_id", label="Assigned Operator", category="Attention",
    operators=frozenset({"=", "!=", "is_null"}), value_type="int",
    sql_generator=_assigned_operator,
    description="Operator ID assigned to this conversation (from conversation_attention)",
))


# ── Queue fields ───────────────────────────────────────────────────────

def _has_pending_queue(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    if op not in ("=", "!="):
        raise ValueError(f"Operator '{op}' not supported for boolean queue field")
    # M7 (B6): creator-scoped (params[0] is the compiler-injected creator_id).
    params.append(params[0])
    sql = (
        f"(SELECT COUNT(*) FROM operator_queue "
        f"WHERE user_id = {alias}.id AND creator_id = $PH AND status = 'pending') > 0"
    )
    if op == "!=":
        return (f"NOT {sql}", 1)
    return (sql, 1)


_register(FieldSpec(
    name="has_pending_queue", label="Has Pending Queue Items", category="Queue",
    operators=frozenset({"=", "!="}), value_type="bool",
    sql_generator=_has_pending_queue,
    description="Whether the fan has pending items in the operator queue",
))


def _queue_count(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    # M7 (B6): creator-scoped (see _msg_count).
    params.append(params[0])
    params.append(value)
    sql = (
        f"(SELECT COUNT(*) FROM operator_queue "
        f"WHERE user_id = {alias}.id AND creator_id = $PH) {op} $PH"
    )
    return (sql, 2)


_register(FieldSpec(
    name="queue_count", label="Queue Item Count", category="Queue",
    operators=Comparisons, value_type="int",
    sql_generator=_queue_count,
    description="Total number of operator queue items for this fan",
))


# ── Purchase / revenue fields (creator-scoped) ────────────────────────
# NOTE: These generators receive creator_id as the FIRST param value.
# The evaluator injects creator_id before calling these generators.
# The $PH placeholder for creator_id is consumed first, then the rule value.


def _purchase_spend(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    """Total spend in minor units (creator-scoped via fangate_transactions)."""
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    params.append(value)
    sql = (
        f"(SELECT COALESCE(SUM(seller_earning)::int, 0) "
        f"FROM fangate_transactions "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH) {op} $PH"
    )
    return (sql, 2)


def _purchase_count(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    """Number of purchases (creator-scoped)."""
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    params.append(value)
    sql = (
        f"(SELECT COUNT(*) FROM fangate_transactions "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH "
        f"AND event_type = 'purchase') {op} $PH"
    )
    return (sql, 2)


def _has_purchased(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    """Whether the fan has any purchase (creator-scoped)."""
    if op not in ("=", "!="):
        raise ValueError(f"Operator '{op}' not supported for boolean purchase field")
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    sql = (
        f"(SELECT COUNT(*) FROM fangate_transactions "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH "
        f"AND event_type = 'purchase') > 0"
    )
    if op == "!=":
        return (f"NOT {sql}", 1)
    return (sql, 1)


def _purchased_product_id(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    """Whether fan purchased a specific product (creator-scoped)."""
    if op not in ("=", "in"):
        raise ValueError(f"Operator '{op}' not supported for purchased_product_id")
    if op == "in":
        if not isinstance(value, list) or not value:
            raise ValueError("Value must be a non-empty list for 'in' operator")
        # M7 (B6): explicit creator binding (see _msg_count).
        params.append(params[0])
        params.append(value)
        sql = (
            f"EXISTS(SELECT 1 FROM fangate_transactions "
            f"WHERE user_id = {alias}.id "
            f"AND creator_id = $PH "
            f"AND product_id IN $PH "
            f"AND event_type = 'purchase')"
        )
        return (sql, 2)
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    params.append(value)
    sql = (
        f"EXISTS(SELECT 1 FROM fangate_transactions "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH "
        f"AND product_id = $PH "
        f"AND event_type = 'purchase')"
    )
    return (sql, 2)


def _purchased_product_count(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    """Number of distinct products purchased (creator-scoped)."""
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    params.append(value)
    sql = (
        f"(SELECT COUNT(DISTINCT product_id) FROM fangate_transactions "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH "
        f"AND event_type = 'purchase') {op} $PH"
    )
    return (sql, 2)


_register(FieldSpec(
    name="total_spend_minor", label="Total Spend (Minor)", category="Purchases",
    operators=Comparisons, value_type="int",
    sql_generator=_purchase_spend,
    description="Total seller earning in minor currency units (e.g., cents)",
))

_register(FieldSpec(
    name="purchase_count", label="Purchase Count", category="Purchases",
    operators=Comparisons, value_type="int",
    sql_generator=_purchase_count,
    description="Number of purchase transactions",
))

_register(FieldSpec(
    name="has_purchased", label="Has Purchased", category="Purchases",
    operators=frozenset({"=", "!="}), value_type="bool",
    sql_generator=_has_purchased,
    description="Whether the fan has at least one purchase",
))

_register(FieldSpec(
    name="purchased_product_id", label="Purchased Product", category="Purchases",
    operators=frozenset({"=", "in"}), value_type="int",
    sql_generator=_purchased_product_id,
    description="Check if fan purchased a specific product ID",
))

_register(FieldSpec(
    name="purchased_product_count", label="Distinct Products Purchased", category="Purchases",
    operators=Comparisons, value_type="int",
    sql_generator=_purchased_product_count,
    description="Number of distinct products purchased",
))


# ── Vault delivery fields (creator-scoped) ─────────────────────────────

def _delivery_count(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    params.append(value)
    sql = (
        f"(SELECT COUNT(*) FROM vault_media_deliveries "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH "
        f"AND status = 'sent') {op} $PH"
    )
    return (sql, 2)


def _has_delivery(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    if op not in ("=", "!="):
        raise ValueError(f"Operator '{op}' not supported for boolean delivery field")
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    sql = (
        f"(SELECT COUNT(*) FROM vault_media_deliveries "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH "
        f"AND status = 'sent') > 0"
    )
    if op == "!=":
        return (f"NOT {sql}", 1)
    return (sql, 1)


def _delivered_product_id(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    if op not in ("=", "in"):
        raise ValueError(f"Operator '{op}' not supported for delivered_product_id")
    if op == "in":
        if not isinstance(value, list) or not value:
            raise ValueError("Value must be a non-empty list")
        # M7 (B6): explicit creator binding (see _msg_count).
        params.append(params[0])
        params.append(value)
        sql = (
            f"EXISTS(SELECT 1 FROM vault_media_deliveries "
            f"WHERE user_id = {alias}.id "
            f"AND creator_id = $PH "
            f"AND product_id IN $PH "
            f"AND status = 'sent')"
        )
        return (sql, 2)
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    params.append(value)
    sql = (
        f"EXISTS(SELECT 1 FROM vault_media_deliveries "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH "
        f"AND product_id = $PH "
        f"AND status = 'sent')"
    )
    return (sql, 2)


def _last_delivery_days_ago(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    params.append(value)
    sql = (
        f"(SELECT EXTRACT(DAY FROM NOW() - MAX(sent_at))::int "
        f"FROM vault_media_deliveries "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH "
        f"AND status = 'sent') {op} $PH"
    )
    return (sql, 2)


def _delivered_product_count(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    params.append(value)
    sql = (
        f"(SELECT COUNT(DISTINCT product_id) FROM vault_media_deliveries "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH "
        f"AND status = 'sent') {op} $PH"
    )
    return (sql, 2)


_register(FieldSpec(
    name="delivery_count", label="Vault Delivery Count", category="Vault",
    operators=Comparisons, value_type="int",
    sql_generator=_delivery_count,
    description="Number of vault media deliveries sent to this fan",
))

_register(FieldSpec(
    name="has_delivery", label="Has Vault Delivery", category="Vault",
    operators=frozenset({"=", "!="}), value_type="bool",
    sql_generator=_has_delivery,
    description="Whether the fan has received any vault media delivery",
))

_register(FieldSpec(
    name="delivered_product_id", label="Delivered Product", category="Vault",
    operators=frozenset({"=", "in"}), value_type="int",
    sql_generator=_delivered_product_id,
    description="Check if fan received delivery from a specific product",
))

_register(FieldSpec(
    name="last_delivery_days_ago", label="Last Delivery (Days Ago)", category="Vault",
    operators=Comparisons, value_type="int",
    sql_generator=_last_delivery_days_ago,
    description="Days since last vault delivery to this fan",
))

_register(FieldSpec(
    name="delivered_product_count", label="Distinct Products Delivered", category="Vault",
    operators=Comparisons, value_type="int",
    sql_generator=_delivered_product_count,
    description="Number of distinct products delivered to this fan",
))


# ── Commerce / offer fields (creator-scoped) ───────────────────────────

def _has_active_offer(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    if op not in ("=", "!="):
        raise ValueError(f"Operator '{op}' not supported for boolean offer field")
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    sql = (
        f"(SELECT COUNT(*) FROM commerce_offers "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH "
        f"AND state IN ('pending','clicked')) > 0"
    )
    if op == "!=":
        return (f"NOT {sql}", 1)
    return (sql, 1)


def _has_purchased_offer(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    if op not in ("=", "!="):
        raise ValueError(f"Operator '{op}' not supported for boolean offer field")
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    sql = (
        f"(SELECT COUNT(*) FROM commerce_offers "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH "
        f"AND state = 'purchased') > 0"
    )
    if op == "!=":
        return (f"NOT {sql}", 1)
    return (sql, 1)


def _offer_state(alias: str, params: list, op: str, value: Any) -> tuple[str, int]:
    if op in ("in", "not_in"):
        if not isinstance(value, list) or not value:
            raise ValueError("Value must be a non-empty list")
        # M7 (B6): explicit creator binding (see _msg_count).
        params.append(params[0])
        params.append(value)
        pg_op = "IN" if op == "in" else "NOT IN"
        sql = (
            f"(SELECT state FROM commerce_offers "
            f"WHERE user_id = {alias}.id "
            f"AND creator_id = $PH "
            f"ORDER BY created_at DESC LIMIT 1) {pg_op} $PH"
        )
        return (sql, 2)
    # M7 (B6): explicit creator binding (see _msg_count).
    params.append(params[0])
    params.append(value)
    sql = (
        f"(SELECT state FROM commerce_offers "
        f"WHERE user_id = {alias}.id "
        f"AND creator_id = $PH "
        f"ORDER BY created_at DESC LIMIT 1) {op} $PH"
    )
    return (sql, 2)


_register(FieldSpec(
    name="has_active_offer", label="Has Active Offer", category="Commerce",
    operators=frozenset({"=", "!="}), value_type="bool",
    sql_generator=_has_active_offer,
    description="Whether the fan has a pending or clicked offer",
))

_register(FieldSpec(
    name="has_purchased_offer", label="Has Purchased Offer", category="Commerce",
    operators=frozenset({"=", "!="}), value_type="bool",
    sql_generator=_has_purchased_offer,
    description="Whether the fan has a purchased offer",
))

_register(FieldSpec(
    name="offer_state", label="Latest Offer State", category="Commerce",
    operators=SetOps, value_type="str",
    value_options=("pending", "clicked", "purchased", "declined", "expired", "revoked"),
    sql_generator=_offer_state,
    description="State of the most recent offer for this fan",
))


# ── Public API ─────────────────────────────────────────────────────────


def get_field(name: str) -> FieldSpec | None:
    """Return field spec by name, or None if not found."""
    return FIELDS.get(name)


def list_fields() -> list[dict[str, Any]]:
    """Return all fields as dicts for API consumption."""
    return [
        {
            "name": f.name,
            "label": f.label,
            "category": f.category,
            "operators": sorted(f.operators),
            "value_type": f.value_type,
            "value_options": f.value_options,
            "description": f.description,
        }
        for f in FIELDS.values()
    ]


def validate_rule(rule: dict) -> tuple[bool, str | None]:
    """Validate a single FieldRule dict against the registry.

    Returns (is_valid, error_message_or_none).
    Fails closed on any invalid input.
    """
    if not isinstance(rule, dict):
        return False, "Rule must be a JSON object"
    if rule.get("type") != "rule":
        return False, "Rule type must be 'rule'"

    negated = rule.get("negated", False)
    if not isinstance(negated, bool):
        return False, "negated must be a boolean"

    field_name = rule.get("field", "")
    field_spec = FIELDS.get(field_name)
    if not field_spec:
        return False, f"Unknown field: '{field_name}'"

    operator = rule.get("operator", "")
    if operator not in field_spec.operators:
        return False, f"Invalid operator '{operator}' for field '{field_name}'"

    value = rule.get("value")
    if not _validate_value_type(field_spec, value):
        return False, f"Invalid value type for field '{field_name}': expected {field_spec.value_type}"

    return True, None


def _validate_value_type(spec: FieldSpec, value: Any) -> bool:
    """Validate that a value matches the expected type for a field."""
    if spec.value_type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    elif spec.value_type == "str":
        return isinstance(value, str) and len(value) > 0
    elif spec.value_type == "bool":
        return isinstance(value, bool)
    elif spec.value_type == "list_int":
        return isinstance(value, list) and all(isinstance(v, int) for v in value)
    elif spec.value_type == "list_str":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    elif spec.value_type == "none":
        return value is None
    return False
