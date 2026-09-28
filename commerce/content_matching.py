"""Vault content matching — deterministic title-token relevance (Phase 2.2).

No embeddings, no vision. Title is the semantic. Preserves the cheapest-
unselected fallback. Creator-scoped, deterministic.

Hierarchy per spec:
  1. conversational relevance
  2. fan demonstrated interest
  3. unpurchased
  4. offer suitability (is_accessible + sales_url)
  5. price tie-breaker (cheapest)
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")

def _tokens(title: str) -> set[str]:
    return set(_TOKEN_RE.findall(title.lower()))

def _topic_tokens(current_topic: str | None, open_threads: tuple[str,...], fan_preferences: list[str] | None = None) -> set[str]:
    toks: set[str] = set()
    if current_topic:
        toks.update(_tokens(current_topic))
    for t in open_threads[:3]:
        toks.update(_tokens(t))
    if fan_preferences:
        for pref in fan_preferences[:5]:
            toks.update(_tokens(str(pref)))
    return toks

def relevance_score(title: str, topics: set[str]) -> float:
    if not topics or not title:
        return 0.0
    t = _tokens(title)
    if not t:
        return 0.0
    overlap = t & topics
    return len(overlap) / max(1, len(t))

def _pid(p: dict[str, Any]) -> int:
    return int(p.get("id") if p.get("id") is not None else p.get("product_id"))

def _media_count(p: dict[str, Any]) -> int:
    # P3.3.4 QUARANTINE: descriptive helper only (title/raw media count).
    # No longer consulted by ranking — media count must not imply bundle
    # composition. Retained for compatibility, not for selection.
    # best-effort: derive from title taxonomy or raw
    try:
        from commerce.vault_taxonomy import parse_taxonomy
        title = p.get("title") or ""
        tax = parse_taxonomy(title)
        if tax.media_count is not None:
            return tax.media_count
    except Exception:
        pass
    # fallback: raw vaultItemIds length if available
    try:
        raw = p.get("raw")
        if isinstance(raw, str):
            import json as _json
            raw = _json.loads(raw)
        if isinstance(raw, dict):
            vids = raw.get("vaultItemIds") or raw.get("vault_item_ids") or []
            if isinstance(vids, list):
                return len(vids)
    except Exception:
        pass
    return 0

def _is_family_suppressed(creator_id: int | None, family: str) -> bool:
    """P3.3.4 QUARANTINE: retained for operator-metric compatibility only.

    Title-derived family suppression no longer participates in commercial
    selection — similarity of titles is descriptive, never proof of a shared
    commercial relationship. Do not call this from ranking/selection paths.
    """
    if not family or creator_id is None:
        return False
    try:
        from commerce.production_control import query_metrics, MetricWindow
        events = query_metrics(name="family_suppressed", creator_id=creator_id, window=MetricWindow.D7)
        for ev in events:
            if ev.get("product_family") == family:
                return True
    except Exception:
        pass
    return False

def rank_products_by_relevance(
    products: list[dict[str, Any]],
    current_topic: str | None,
    open_threads: tuple[str, ...] = (),
    fan_preferences: list[str] | None = None,
    purchased_ids: set[int] | None = None,
    recent_offered_ids: set[int] | None = None,
    recent_offered_groups: set[str] | None = None,
    creator_id: int | None = None,
) -> list[tuple[dict[str, Any], float]]:
    """Return list of (product, relevance) sorted: relevance desc, price asc, id asc.

    Filters already-purchased. P3.3.4: taxonomy describes similarity only —
    there is no bundle preference (no larger-bundle boost) and no
    title-derived family fatigue or suppression. Only actual per-product
    recent-offer recency penalizes relevance. ``recent_offered_groups`` and
    ``creator_id`` are retained for signature compatibility and ignored.
    Penalizes recently offered (24h) by -0.20 per-product to avoid fatigue.
    """
    purchased_ids = purchased_ids or set()
    recent_offered_ids = recent_offered_ids or set()
    recent_offered_groups = recent_offered_groups or set()
    topics = _topic_tokens(current_topic, open_threads, fan_preferences)
    scored: list[tuple[dict[str, Any], float]] = []
    for p in products:
        try:
            pid = _pid(p)
        except Exception:
            continue
        if pid in purchased_ids:
            continue
        title = p.get("title") or ""
        rel = relevance_score(title, topics)
        # Content fatigue: recently offered same product within 24h -> penalize.
        # P3.3.4: no per-family penalty — a shared title-derived bundle_group
        # is descriptive similarity, not a commercial relationship.
        if pid in recent_offered_ids:
            rel = max(0.0, rel - 0.20)
        scored.append((p, rel))
    def sort_key(item: tuple[dict[str, Any], float]):
        p, rel = item
        price = p.get("price_minor")
        price_key = price if price is not None else 10**12
        # P3.3.4: no bundle preference — taxonomy must not select SINGLE vs
        # BUNDLE composition. Relevance, then price, then id.
        return (-rel, price_key, _pid(p))
    scored.sort(key=sort_key)
    return scored

def best_match_or_none(
    products: list[dict[str, Any]],
    current_topic: str | None,
    open_threads: tuple[str, ...] = (),
    fan_preferences: list[str] | None = None,
    purchased_ids: set[int] | None = None,
    recent_offered_ids: set[int] | None = None,
    recent_offered_groups: set[str] | None = None,
    min_relevance: float = 0.15,
    creator_id: int | None = None,
) -> dict[str, Any] | None:
    """Return best-matching product or None if NO_CONFIDENT_MATCH."""
    ranked = rank_products_by_relevance(products, current_topic, open_threads, fan_preferences, purchased_ids, recent_offered_ids, recent_offered_groups, creator_id=creator_id)
    if not ranked:
        return None
    best, score = ranked[0]
    if score < min_relevance:
        return None
    return best
