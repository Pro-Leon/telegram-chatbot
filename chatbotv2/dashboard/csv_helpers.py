"""CSV generation helpers for dashboard exports."""

import csv
import io
import json
from typing import Any

_EXPORT_MAX_ROWS = 5000
_DANGEROUS_PREFIXES = ("=", "+", "-", "@")


def sanitize_csv_value(value: str) -> str:
    """Prevent CSV formula injection by prefixing dangerous values."""
    if value and value[0] in _DANGEROUS_PREFIXES:
        return "'" + value
    return value


def build_conversations_csv_rows(items: list[dict[str, Any]]) -> str:
    """Convert conversation analytics items to CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "user_id",
            "username",
            "first_name",
            "last_seen",
            "inbound_count",
            "outbound_count",
            "last_inbound_at",
            "last_outbound_at",
            "avg_confidence",
            "auto_approved_count",
            "operator_approved_count",
            "queue_count",
            "avg_response_seconds",
            "attention_reasons",
            "attention_status",
            "assigned_operator_id",
            "reviewed_at",
            "reviewed_by",
        ]
    )
    for item in items:
        writer.writerow(
            [
                item["user_id"],
                sanitize_csv_value(str(item.get("username") or "")),
                sanitize_csv_value(str(item.get("first_name") or "")),
                item.get("last_seen") or "",
                item.get("inbound_count", 0),
                item.get("outbound_count", 0),
                item.get("last_inbound_at") or "",
                item.get("last_outbound_at") or "",
                item.get("avg_confidence") if item.get("avg_confidence") is not None else "",
                item.get("auto_approved_count", 0),
                item.get("operator_approved_count", 0),
                item.get("queue_count", 0),
                item.get("avg_response_seconds")
                if item.get("avg_response_seconds") is not None
                else "",
                sanitize_csv_value(";".join(item.get("attention_reasons") or [])),
                item.get("attention_status") or "new",
                item.get("assigned_operator_id") or "",
                item.get("reviewed_at") or "",
                sanitize_csv_value(str(item.get("reviewed_by") or "")),
            ]
        )
    return buf.getvalue()


def build_activity_csv_rows(items: list[dict[str, Any]]) -> str:
    """Convert activity timeline items to CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "event_type",
            "event_id",
            "created_at",
            "operator",
            "direction",
            "content_preview",
            "detail",
            "extra",
        ]
    )
    for item in items:
        extra_str = ""
        if item.get("extra"):
            extra_str = json.dumps(dict(item["extra"]))
        writer.writerow(
            [
                item.get("event_type") or "",
                item.get("event_id") or "",
                item.get("created_at") or "",
                sanitize_csv_value(str(item.get("operator") or "")),
                item.get("direction") or "",
                sanitize_csv_value(str(item.get("content_preview") or "")),
                sanitize_csv_value(str(item.get("detail") or "")),
                sanitize_csv_value(extra_str),
            ]
        )
    return buf.getvalue()


def build_detail_csv(detail: dict[str, Any], user_id: int) -> str:
    """Build a field/value two-column CSV for a single conversation detail."""
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["field", "value"])

    user = detail.get("user", {})
    analytics = detail.get("analytics", {})
    queue = detail.get("queue", {})
    attention = detail.get("attention", {})

    rows_data = [
        ("user_id", user.get("id", "")),
        ("username", sanitize_csv_value(str(user.get("username") or ""))),
        ("first_name", sanitize_csv_value(str(user.get("first_name") or ""))),
        ("last_seen", user.get("last_seen") or ""),
        ("message_count", user.get("message_count", 0)),
        ("funnel_stage", user.get("funnel_stage") or ""),
        ("inbound_count", analytics.get("inbound_count", 0)),
        ("outbound_count", analytics.get("outbound_count", 0)),
        ("last_inbound_at", analytics.get("last_inbound_at") or ""),
        ("last_outbound_at", analytics.get("last_outbound_at") or ""),
        ("first_message_at", analytics.get("first_message_at") or ""),
        (
            "avg_confidence",
            analytics.get("avg_confidence") if analytics.get("avg_confidence") is not None else "",
        ),
        ("ai_generated", analytics.get("ai_generated", 0)),
        (
            "auto_approval_rate",
            analytics.get("auto_approval_rate")
            if analytics.get("auto_approval_rate") is not None
            else "",
        ),
        (
            "avg_response_seconds",
            analytics.get("avg_response_seconds")
            if analytics.get("avg_response_seconds") is not None
            else "",
        ),
        ("total_queue_items", queue.get("total_queue_items", 0)),
        ("pending_queue_items", queue.get("pending_queue_items", 0)),
        ("attention_status", attention.get("status") or "new"),
        ("assigned_operator_id", attention.get("assigned_operator_id") or ""),
        ("reviewed_at", attention.get("reviewed_at") or ""),
        ("reviewed_by", sanitize_csv_value(str(attention.get("reviewed_by") or ""))),
    ]
    for field, value in rows_data:
        writer.writerow([field, value])

    return buf.getvalue()
