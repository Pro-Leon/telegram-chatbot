"""Canonical first-message identity helpers (Phases 1-4 remediation).

Two separate concepts - never conflated:

* ``creator_id``  = tenant / business scope.
* ``generation_id`` = logical-turn correlation identifier.
* Telegram ``(user_id, telegram_message_id)`` + entity IDs = transport identity.
* ``dedup_id`` / dedup keys = delivery / idempotency mechanism.

Telegram-derived generation IDs use the exact historical production
algorithm::

    md5(f"{user_id}:{content}:{telegram_message_id}")

``creator_id`` is deliberately NOT part of the hash: creator isolation is
provided by the composite ``(creator_id, generation_id)``, which preserves
compatibility with existing Redis reconciliation and deterministic retry
behavior. Changing the hash would orphan all historical joins.

Synthetic IDs (scheduler / operator / manual origins) use explicit
namespaces (``scheduled:``, ``queue_item:``, ``manual:``) and must never
pretend to be Telegram-derived MD5 IDs.

Correlation rule: an existing valid ``generation_id`` received from
transport/payload is authoritative for correlation; deterministic
recomputation is only a recovery mechanism when the original value is
genuinely absent. Generic database helpers never recompute - the caller
owns generation identity.
"""

from __future__ import annotations

import hashlib
import uuid

_MD5_HEX_LENGTH = 32

# Namespaces that are reserved for synthetic (non-Telegram) origins.
# Telegram-derived IDs are always 32-char lowercase hex MD5 digests, so a
# value containing ":" can never collide with one.
SYNTHETIC_NAMESPACES = ("scheduled:", "queue_item:", "manual:", "synthetic:")


def telegram_generation_id(user_id: int, content: str, telegram_message_id: int) -> str:
    """Authoritative Telegram-derived generation ID.

    Preserves the exact historical algorithm. ``content`` must be the raw
    inbound text; callers must not normalize it here (normalization would
    fork correlation from Redis reconciliation, which hashes raw values).
    """
    return hashlib.md5(f"{user_id}:{content}:{telegram_message_id}".encode()).hexdigest()


def is_telegram_generation_id(value: object) -> bool:
    """Return True for 32-char hex digests (Telegram-derived shape)."""
    if not isinstance(value, str) or len(value) != _MD5_HEX_LENGTH:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower() and all(c in "0123456789abcdef" for c in value)


def is_valid_generation_id(value: object) -> bool:
    """Return True for any usable correlation ID (Telegram or synthetic)."""
    if not isinstance(value, str) or not value.strip():
        return False
    if is_telegram_generation_id(value):
        return True
    return value.startswith(SYNTHETIC_NAMESPACES)


def ensure_generation_id(
    value: object,
    *,
    user_id: int | None = None,
    content: str | None = None,
    telegram_message_id: int | None = None,
) -> str | None:
    """Return ``value`` when valid; otherwise recompute from the Telegram tuple.

    Recomputation happens only when the original is genuinely absent/invalid
    and the full Telegram tuple is available. Returns None when neither a
    valid value nor a complete tuple is available (caller decides fallback).
    """
    if isinstance(value, str) and value.strip() and is_valid_generation_id(value.strip()):
        return value.strip()
    # Legacy tolerance: pre-synthetic callers sometimes pass "" – treat as absent.
    if user_id is None or content is None or telegram_message_id is None:
        return None
    try:
        return telegram_generation_id(int(user_id), content, int(telegram_message_id))
    except (TypeError, ValueError):
        return None


def synthetic_generation_id(namespace: str, unique_value: str) -> str:
    """Mint an explicitly-namespaced synthetic generation ID.

    ``namespace`` must end with ":". Raises ValueError otherwise so synthetic
    IDs can never silently take the Telegram MD5 shape.
    """
    if not namespace.endswith(":"):
        raise ValueError("synthetic namespace must end with ':'")
    if not unique_value or not str(unique_value).strip():
        raise ValueError("synthetic unique_value must be non-empty")
    candidate = f"{namespace}{unique_value}"
    if is_telegram_generation_id(candidate):
        raise ValueError("synthetic ID must not collide with Telegram MD5 shape")
    return candidate


def scheduled_generation_id(schedule_key: str, message_id: int | str) -> str:
    """Stable synthetic ID for scheduler-origin sends.

    Same scheduled row always yields the same ID (retry-safe), distinct rows
    never collide.
    """
    return synthetic_generation_id("scheduled:", f"{schedule_key}:{message_id}")


def queue_item_generation_id(queue_id: int | str, generation_id: str | None = None) -> str:
    """Correlation ID for operator-approved sends.

    Prefers the queue row's own generation_id when present (normal Phases
    1-4 path); otherwise mints a namespaced synthetic fallback so the send
    is still correlatable without pretending to be Telegram-derived.
    """
    if isinstance(generation_id, str) and is_valid_generation_id(generation_id.strip()):
        return generation_id.strip()
    return synthetic_generation_id("queue_item:", f"{queue_id}:{uuid.uuid4().hex[:12]}")


def manual_generation_id(unique_value: str | None = None) -> str:
    """Synthetic ID for manually initiated dashboard sends."""
    return synthetic_generation_id("manual:", unique_value or uuid.uuid4().hex)
