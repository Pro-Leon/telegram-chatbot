"""P3.2 canonical Vault-set representation + Drop content key.

Pure helpers, no I/O, no LLM, no clock. Two representations:

- identity: ``sorted(unique(ids))`` for equality/idempotency.
- presentation: input order with duplicates removed first-wins for delivery/display.

Identity must never use presentation order; presentation must never be sorted.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any, Mapping

MAX_VAULT_ITEMS = 10


def _clean_ids(vault_item_ids: Any) -> list[str]:
    """Strip whitespace, drop non-strings/empties, preserve input order."""
    if not isinstance(vault_item_ids, (list, tuple)):
        raise ValueError("vault_item_ids must be a list of strings")
    cleaned: list[str] = []
    for item in vault_item_ids:
        if not isinstance(item, str):
            raise ValueError(f"invalid vault item id: {item!r}")
        stripped = item.strip()
        if not stripped:
            raise ValueError("invalid vault item id: empty value")
        cleaned.append(stripped)
    return cleaned


def canonical_identity_ids(vault_item_ids: list[str] | tuple[str, ...]) -> list[str]:
    """Return sorted unique Vault IDs for equality/idempotency.

    ``["b", "a"] -> ["a", "b"]``; ``["a", "b", "a"] -> ["a", "b"]``.
    """
    return sorted(set(_clean_ids(vault_item_ids)))


def presentation_ids(vault_item_ids: list[str] | tuple[str, ...]) -> list[str]:
    """Return input order with duplicates removed first-wins.

    ``["a", "b", "a"] -> ["a", "b"]``; ``["b", "a"] -> ["b", "a"]``.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for item in _clean_ids(vault_item_ids):
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def validate_vault_set(
    vault_item_ids: Any,
    *,
    allow_empty: bool = False,
    max_items: int = MAX_VAULT_ITEMS,
) -> list[str]:
    """Validate a Vault set and return cleaned input-order IDs.

    Rejects empty sets (unless ``allow_empty``), over-limit sets, and
    invalid entries. Duplicate entries are rejected here: callers that
    intentionally accept duplicates must use ``presentation_ids`` first
    and validate the deduped result.
    """
    cleaned = _clean_ids(vault_item_ids)
    if not cleaned and not allow_empty:
        raise ValueError("vault_item_ids must contain at least one item")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("vault_item_ids contains duplicate entries")
    if len(cleaned) > max_items:
        raise ValueError(f"vault_item_ids exceeds Dropfans limit of {max_items}")
    return cleaned


def drop_content_hash(vault_item_ids: list[str] | tuple[str, ...]) -> str:
    """Deterministic hash of the canonical Vault set (identity only)."""
    canonical = canonical_identity_ids(vault_item_ids)
    joined = "\n".join(canonical)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _normalize_price_minor(price_minor: Any) -> int:
    if isinstance(price_minor, bool):
        raise ValueError("price_minor must be an integer")
    try:
        value = int(price_minor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("price_minor must be an integer") from None
    if value < 0:
        raise ValueError("price_minor must be >= 0")
    return value


def _normalize_currency(currency: Any) -> str:
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError("currency is required")
    return currency.strip().upper()


def drop_content_key(
    *,
    creator_id: int,
    vault_item_ids: list[str] | tuple[str, ...],
    price_minor: int,
    currency: str = "USD",
    allow_download: bool = True,
    extra_config: Mapping[str, Any] | None = None,
) -> str:
    """Deterministic content identity for a Drop creation intent.

    Based on ``creator_id + canonical Vault set + price + currency +
    allow_download + provider-visible extra config``. Excludes names,
    descriptions, timestamps, UUIDs, and presentation ordering.
    Suitable for a database unique constraint.
    """
    if not isinstance(creator_id, int) or isinstance(creator_id, bool) or creator_id <= 0:
        raise ValueError("creator_id is required")
    canonical = canonical_identity_ids(vault_item_ids)
    if not canonical:
        raise ValueError("vault_item_ids must contain at least one item")
    if len(canonical) > MAX_VAULT_ITEMS:
        raise ValueError(f"vault_item_ids exceeds Dropfans limit of {MAX_VAULT_ITEMS}")
    price = _normalize_price_minor(price_minor)
    curr = _normalize_currency(currency)
    allow = bool(allow_download)
    config_part = ""
    if extra_config:
        if not isinstance(extra_config, Mapping):
            raise ValueError("extra_config must be a mapping")
        items = sorted((str(k), _config_scalar(v)) for k, v in extra_config.items())
        config_part = "|".join(f"{k}={v}" for k, v in items)
    payload = "\n".join(
        [
            f"creator:{creator_id}",
            "vault:" + ",".join(canonical),
            f"price_minor:{price}",
            f"currency:{curr}",
            f"allow_download:{int(allow)}",
            f"config:{config_part}",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _config_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return f"bool:{int(value)}"
    if isinstance(value, (int, float, Decimal)):
        return f"num:{Decimal(str(value))}"
    if isinstance(value, str):
        return f"str:{value.strip()}"
    if value is None:
        return "none:"
    raise ValueError(f"unsupported extra_config value: {value!r}")
