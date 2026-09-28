"""P3-B — Deterministic Vault candidate ranking (pure, no I/O).

Ranks approved Vault candidates against operator allowlists. No LLM calls,
no external API calls, no DB access. Identical inputs yield identical outputs.

Ranking order:
  1. tag overlap descending
  2. folder priority ascending (index in folder_priority list; unlisted last)
  3. createdAt descending (newer first; missing/invalid treated as oldest)
  4. stable ID ascending (final deterministic tie-break)

Constraints (AND semantics when both configured):
  - allowed_folders non-empty: candidate must belong to an allowed folder
  - allowed_tags non-empty: candidate must share >=1 normalized tag
  - moderation_status must be explicitly APPROVED (P3.1 F-07b:
    unknown/missing moderation is excluded, never assumed approved)
  - vault_item_id in purchased_ids is excluded

The LLM must never select Vault IDs; callers pass only deterministic inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


@dataclass(frozen=True)
class VaultCandidate:
    """Deterministic Vault candidate (all fields plain values)."""

    vault_item_id: str
    tags: tuple[str, ...] = ()
    folder_id: str | None = None
    moderation_status: str | None = None
    file_type: str | None = None
    created_at: str | None = None


def _normalize_tag(tag: Any) -> str | None:
    if not isinstance(tag, str):
        return None
    cleaned = tag.strip().lower()
    return cleaned or None


def _normalize_tags(tags: Iterable[Any] | None) -> frozenset[str]:
    if not tags:
        return frozenset()
    out: set[str] = set()
    for tag in tags:
        normalized = _normalize_tag(tag)
        if normalized:
            out.add(normalized)
    return frozenset(out)


def _parse_created_at(value: Any) -> float:
    """Return epoch seconds; missing/invalid sorts as oldest (0.0)."""
    if not isinstance(value, str) or not value.strip():
        return 0.0
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return 0.0


def rank_vault_candidates(
    candidates: Iterable[VaultCandidate | dict[str, Any]],
    *,
    allowed_folders: Iterable[str] | None = None,
    allowed_tags: Iterable[str] | None = None,
    folder_priority: Iterable[str] | None = None,
    purchased_ids: Iterable[str] | None = None,
) -> list[tuple[VaultCandidate, int]]:
    """Filter and rank Vault candidates deterministically.

    Returns ``[(candidate, tag_overlap)]`` sorted by the documented order.
    Empty allowlists mean unrestricted (all approved, unpurchased candidates).
    """
    folder_allow = [str(f) for f in (allowed_folders or []) if str(f).strip()]
    folder_allow_set = set(folder_allow)
    tag_allow = _normalize_tags(allowed_tags)
    priority_list = [str(f) for f in (folder_priority or []) if str(f).strip()]
    priority_index = {folder: index for index, folder in enumerate(priority_list)}
    purchased_set = {str(p) for p in (purchased_ids or []) if str(p).strip()}

    ranked: list[tuple[tuple[int, int, float, str], VaultCandidate, int]] = []
    for raw in candidates:
        if isinstance(raw, VaultCandidate):
            candidate = raw
        elif isinstance(raw, dict):
            vid = raw.get("vault_item_id", raw.get("id", ""))
            if not isinstance(vid, str) or not vid.strip():
                continue
            tags_raw = raw.get("tags", raw.get("content_tags", raw.get("contentTags", ())))
            if isinstance(tags_raw, str):
                tags_raw = [tags_raw]
            candidate = VaultCandidate(
                vault_item_id=str(vid).strip(),
                tags=tuple(t for t in (_normalize_tag(t) for t in (tags_raw or ())) if t),
                folder_id=str(raw.get("folder_id")).strip() if raw.get("folder_id") else None,
                moderation_status=str(raw.get("moderation_status")).strip().upper() if raw.get("moderation_status") else None,
                file_type=str(raw.get("file_type")).strip().lower() if raw.get("file_type") else None,
                created_at=raw.get("created_at") if isinstance(raw.get("created_at"), str) else None,
            )
        else:
            continue

        if not candidate.vault_item_id:
            continue
        if candidate.vault_item_id in purchased_set:
            continue
        # P3.1 F-07b: fail-closed on unknown moderation. Only an explicit
        # APPROVED value is selectable; missing/unknown never implies approved.
        if (candidate.moderation_status or "").strip().upper() != "APPROVED":
            continue
        if folder_allow_set and (candidate.folder_id not in folder_allow_set):
            continue
        candidate_tags = _normalize_tags(candidate.tags)
        overlap = len(candidate_tags & tag_allow) if tag_allow else 0
        if tag_allow and overlap < 1:
            continue
        folder_rank = priority_index.get(candidate.folder_id or "", len(priority_list))
        created_epoch = _parse_created_at(candidate.created_at)
        # createdAt descending → negate epoch for ascending sort
        key = (-overlap, folder_rank, -created_epoch, candidate.vault_item_id)
        ranked.append((key, candidate, overlap))

    ranked.sort(key=lambda item: item[0])
    return [(candidate, overlap) for _, candidate, overlap in ranked]
