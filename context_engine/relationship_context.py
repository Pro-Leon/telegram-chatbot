"""Relationship-aware context selection — Phase 5.

Single deterministic selection step between existing retrieval/memory
sources and context assembly::

    current-turn state (topic / open threads / message)
            +
    existing retrieved LTM (``commerce.long_term_memory``)
            +
    existing retrieved fan knowledge (``commerce.fan_knowledge``)
            +
    existing summary (already-fetched, never refetched here)
            +
    descriptive relationship trajectory bands (Phase 1, read-only)
            ↓
    ONE bounded selection (this module)
            ↓
    ONE bounded data-only representation (``render_relationship_context``)
            ↓
    Context Engine snapshot / legacy context list (data, advisory)
            +
    Phase 4 strategy evidence (``user_referenced_previous_context``)

Design rules enforced here (mirrors Phase 1–4 contracts):

* Pure and deterministic: no DB, no Redis, no network, no LLM, no
  randomness, no global mutable state, no clock reads. The async
  ``assemble_relationship_context`` helper only awaits the *existing*
  retrieval functions with their *existing* signatures and performs no
  writes; all ranking/selection itself is pure.
* Single owner: this module is the sole place where a Phase 5
  relationship-context selection is made. It does not call, wrap, or
  extend the legacy response-mode planner, ``commerce.open_loop`` (dead),
  or the vector-history path (dead), and it never selects a
  conversational move (Phase 4 remains the sole move selector).
* Descriptive input only: trajectory bands are read, never written,
  never promoted, never reinterpreted. Counters, provenance floats,
  transition mechanics, streaks, decay state, and internal IDs are never
  rendered.
* Current context wins: a supporting fact is selected only when it
  overlaps the current topic / open threads / current message. Band-only
  evidence never produces facts; threads alone never set prior-context
  evidence (facts are required for that).
* Data, not directives: the rendered block contains semantic facts and
  labels only — never instructions such as "you should", "ask", "sell",
  "flirt", or "escalate".
* Commerce separation: commerce-ladder subjects (purchase/offer/price/
  product/…) and sexual/intimacy subjects are excluded by token deny
  lists even though the underlying stores should never contain them.
* Fail-open: any unusable input yields an empty selection; rendering an
  empty selection yields ``""`` so callers leave the prompt byte-identical.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("context_engine.relationship_context")

# ---------------------------------------------------------------------------
# Bounds (part of the Phase 5 contract; deterministic, testable)
# ---------------------------------------------------------------------------

#: Maximum supporting historical facts rendered in one turn.
MAX_RELATIONSHIP_FACTS = 3

#: Maximum ongoing-thread labels rendered in one turn.
MAX_RELATIONSHIP_THREADS = 3

#: Maximum characters for the single optional summary line.
MAX_SUMMARY_CHARS = 200

#: Soft token ceiling for the rendered block (enforced by dropping the
#: summary first, then lowest-ranked facts; see ``render_relationship_context``).
MAX_RELATIONSHIP_TOKENS = 200

#: Minimum relevance score for a supporting fact. Mirrors the existing
#: ``> 0.2`` retrieval threshold used by ``retrieve_relevant_memories`` and
#: ``retrieve_relevant_knowledge`` so Phase 5 introduces no second,
#: incompatible relevance language.
RELEVANCE_THRESHOLD = 0.2

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Token deny lists (exact-token match, lowercase). Defense-in-depth:
#: the underlying stores should never hold these subjects, but the
#: relationship block must never become commerce or sexual/intimacy
#: context even if an upstream extractor ever emits one.
_COMMERCE_DENY_TOKENS = frozenset(
    {
        "purchase",
        "purchased",
        "purchases",
        "offer",
        "offers",
        "price",
        "prices",
        "pricing",
        "product",
        "products",
        "payment",
        "payments",
        "checkout",
        "ppv",
        "paid",
        "refund",
        "refunds",
        "discount",
        "upsell",
        "order",
        "orders",
    }
)

_SEXUAL_DENY_TOKENS = frozenset(
    {
        "sexual",
        "sexy",
        "intimate",
        "intimacy",
        "erotic",
        "erotica",
        "aroused",
        "arousal",
        "orgasm",
        "nude",
        "naked",
        "porn",
        "pornography",
        "fetish",
        "kink",
        "bdsm",
        "escort",
    }
)

_DENY_TOKENS = _COMMERCE_DENY_TOKENS | _SEXUAL_DENY_TOKENS

#: LTM memory types treated as open-loop/commitment-like for relevance
#: boosting. Mirrors the boost in ``retrieve_relevant_memories``.
_OPEN_LOOP_TYPES = frozenset({"open_loop", "commitment"})

#: Fan-knowledge temporal types treated as commitment-like for relevance
#: boosting (upcoming/remembered items, e.g. trips, events).
_COMMITMENT_TEMPORAL_TYPES = frozenset({"FUTURE", "EVENT"})

#: Band dimensions rendered (fixed order for deterministic output).
_BAND_ORDER = ("familiarity", "engagement", "reciprocity", "continuity", "trend")


# ---------------------------------------------------------------------------
# Typed contracts (immutable, advisory, turn-scoped)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelationshipFact:
    """One selected supporting historical fact (bounded, semantic only)."""

    subject: str
    value: str
    status: str = ""
    confidence: float = 0.5
    memory_type: str = ""


@dataclass(frozen=True)
class RelationshipContext:
    """Selected relationship context for exactly one turn.

    Turn-scoped, advisory, never persisted. ``has_prior_context`` is True
    only when at least one supporting fact with genuine current-turn
    overlap was selected — threads or bands alone never set it.
    """

    band_labels: tuple[str, ...] = ()
    threads: tuple[str, ...] = ()
    facts: tuple[RelationshipFact, ...] = ()
    summary_line: str | None = None
    has_prior_context: bool = False


# ---------------------------------------------------------------------------
# Tolerant readers (duck-typed: dataclass OR mapping OR None; never raises)
# ---------------------------------------------------------------------------


def to_plain_dict(value: Any) -> Any:
    """Recursively convert mappings (incl. MappingProxy) to plain containers.

    The authoritative snapshot freezes nested dicts into MappingProxyType,
    which Phase 1–4 readers reject with strict ``isinstance(x, dict)``
    checks. Phase 5 converts once at the boundary instead of changing
    those domain contracts.
    """
    try:
        if isinstance(value, Mapping):
            return {k: to_plain_dict(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [to_plain_dict(v) for v in value]
        return value
    except Exception:
        return value


def _field(source: Any, name: str, default: Any = None) -> Any:
    if source is None:
        return default
    try:
        if isinstance(source, Mapping):
            return source.get(name, default)
        return getattr(source, name, default)
    except Exception:
        return default


def _tokens(text: Any) -> set[str]:
    """Lowercase alphanumeric tokens (same tokenizer family as retrieval)."""
    try:
        if not isinstance(text, str) or not text:
            return set()
        return set(_TOKEN_RE.findall(text.lower()))
    except Exception:
        return set()


def _clean_label(text: Any, max_chars: int = 80) -> str | None:
    try:
        if not isinstance(text, str):
            return None
        cleaned = " ".join(text.strip().split())
        if not cleaned:
            return None
        # Never leak raw multi-line content or unbounded strings.
        cleaned = cleaned.split("\n")[0].strip()
        if len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars].rstrip()
        return cleaned or None
    except Exception:
        return None


def _band_token(value: Any) -> str | None:
    """Normalize one band value; unknown/missing → None (omitted)."""
    try:
        if value is None:
            return None
        token = getattr(value, "value", value)
        text = str(token).strip().lower()
        if not text or text == "unknown":
            return None
        return text
    except Exception:
        return None


def _as_confidence(value: Any) -> float:
    try:
        if isinstance(value, bool):
            return 0.5
        number = float(value)
        if number != number or number == float("inf") or number == float("-inf"):
            return 0.5
        return min(1.0, max(0.0, number))
    except Exception:
        return 0.5


def _is_denied(subject: str, value: str) -> bool:
    """True when subject/value tokens hit commerce/sexual deny lists."""
    try:
        tokens = _tokens(subject) | _tokens(value)
        return bool(tokens & _DENY_TOKENS)
    except Exception:
        return False


def _normalize_fact(item: Any) -> RelationshipFact | None:
    """Normalize one LTM / fan-knowledge row to a bounded fact (or None)."""
    try:
        subject = _field(item, "subject", "")
        value = _field(item, "value", "")
        if not isinstance(subject, str) or not isinstance(value, str):
            return None
        subject = _clean_label(subject, 48)
        value = _clean_label(value, 80)
        if not subject or not value:
            return None
        if _is_denied(subject, value):
            return None
        status = _field(item, "status", "") or ""
        status = str(status).strip().upper()[:16]
        if status == "EXPIRED":
            return None
        memory_type = _field(item, "memory_type", "") or ""
        memory_type = str(memory_type).strip().lower()[:24]
        temporal_type = _field(item, "temporal_type", "") or ""
        temporal_type = str(temporal_type).strip().upper()[:16]
        if not memory_type and temporal_type in _COMMITMENT_TEMPORAL_TYPES:
            memory_type = "commitment"
        confidence = _as_confidence(_field(item, "confidence", 0.5))
        return RelationshipFact(
            subject=subject,
            value=value,
            status=status,
            confidence=confidence,
            memory_type=memory_type,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core selector (pure; never raises — unusable input yields empty context)
# ---------------------------------------------------------------------------


def select_relationship_context(
    *,
    bands: Any | None = None,
    current_topic: str | None = None,
    open_threads: Any | None = None,
    current_message: str | None = None,
    ltm_items: Any | None = None,
    fk_items: Any | None = None,
    summary: str | None = None,
) -> RelationshipContext:
    """Select bounded relationship context for one turn (pure).

    Facts require genuine current-turn overlap (topic / open threads /
    current message tokens); band-only evidence never produces facts.
    Deterministic: same inputs → identical output. Never raises.
    """
    try:
        return _select(
            bands=bands,
            current_topic=current_topic,
            open_threads=open_threads,
            current_message=current_message,
            ltm_items=ltm_items,
            fk_items=fk_items,
            summary=summary,
        )
    except Exception:
        logger.debug("relationship context selection failed (fail-open empty)", exc_info=True)
        return RelationshipContext()


def _select(
    *,
    bands: Any | None,
    current_topic: str | None,
    open_threads: Any | None,
    current_message: str | None,
    ltm_items: Any | None,
    fk_items: Any | None,
    summary: str | None,
) -> RelationshipContext:
    # -- Band labels (descriptive metadata only; unknown omitted) --
    band_labels: list[str] = []
    for name in _BAND_ORDER:
        token = _band_token(_field(bands, name, None))
        if token is not None:
            band_labels.append(f"{name}={token}")

    # -- Current-turn relevance vocabulary (topic + threads + message) --
    topic_label = _clean_label(current_topic, 48)
    threads: list[str] = []
    try:
        raw_threads = open_threads if isinstance(open_threads, (list, tuple)) else []
        for entry in raw_threads:
            label = _clean_label(entry, 48)
            if label and label not in threads:
                threads.append(label)
            if len(threads) >= MAX_RELATIONSHIP_THREADS:
                break
    except Exception:
        threads = []
    query_tokens: set[str] = set()
    if topic_label:
        query_tokens |= _tokens(topic_label)
    for thread in threads:
        query_tokens |= _tokens(thread)
    query_tokens |= _tokens(current_message)

    # -- Candidate facts from existing retrieval output (no new fetch) --
    candidates: list[tuple[RelationshipFact, int, float]] = []
    seen_keys: set[str] = set()
    for source_items in (ltm_items, fk_items):
        try:
            items = list(source_items) if isinstance(source_items, (list, tuple)) else []
        except Exception:
            continue
        for raw in items:
            fact = _normalize_fact(raw)
            if fact is None:
                continue
            key = f"{fact.subject.lower()}={fact.value.lower()}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            overlap = len((_tokens(fact.subject) | _tokens(fact.value)) & query_tokens)
            if overlap <= 0:
                # No genuine current-turn support: never selected.
                continue
            boosted = fact.memory_type in _OPEN_LOOP_TYPES
            score = overlap * 0.5 + fact.confidence * 0.3 + (0.3 if boosted else 0.0)
            if score <= RELEVANCE_THRESHOLD:
                continue
            candidates.append((fact, overlap, score))

    # Deterministic ordering: open-loop/commitment relevance first, then
    # overlap, then confidence, then lexical tie-breaks.
    def _rank(item: tuple[RelationshipFact, int, float]) -> tuple[Any, ...]:
        fact, overlap, score = item
        boosted = 0 if fact.memory_type in _OPEN_LOOP_TYPES else 1
        return (boosted, -overlap, -fact.confidence, fact.subject.lower(), fact.value.lower())

    candidates.sort(key=_rank)
    facts = tuple(fact for fact, _, _ in candidates[:MAX_RELATIONSHIP_FACTS])

    # -- Optional single bounded summary line (first sentence only) --
    summary_line: str | None = None
    try:
        if isinstance(summary, str) and summary.strip():
            first = summary.strip().split(". ")[0].strip()
            first = " ".join(first.split())
            if first:
                if not first.endswith("."):
                    first += "."
                if len(first) > MAX_SUMMARY_CHARS:
                    first = first[:MAX_SUMMARY_CHARS].rstrip() + "."
                summary_line = first
    except Exception:
        summary_line = None

    return RelationshipContext(
        band_labels=tuple(band_labels),
        threads=tuple(threads),
        facts=facts,
        summary_line=summary_line,
        has_prior_context=bool(facts),
    )


def has_prior_context_evidence(context: RelationshipContext | None) -> bool:
    """True only when selected facts genuinely support prior context."""
    try:
        return bool(
            isinstance(context, RelationshipContext)
            and context.has_prior_context
            and len(context.facts) > 0
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Rendering (pure, bounded, data-only; never raises)
# ---------------------------------------------------------------------------


def render_relationship_context(
    context: RelationshipContext | None,
    *,
    max_tokens: int = MAX_RELATIONSHIP_TOKENS,
) -> str:
    """Render the advisory relationship block (pure, fail-open).

    Returns ``""`` when there is nothing safe to render so callers leave
    the prompt byte-identical. Historical facts render as data lines only,
    never as instructions.
    """
    try:
        if not isinstance(context, RelationshipContext):
            return ""
        facts = list(context.facts[:MAX_RELATIONSHIP_FACTS])
        threads = list(context.threads[:MAX_RELATIONSHIP_THREADS])
        bands = list(context.band_labels)
        summary_line = context.summary_line

        def _build(
            use_bands: list[str],
            use_threads: list[str],
            use_facts: list[RelationshipFact],
            use_summary: str | None,
        ) -> str:
            lines = ["RELATIONSHIP CONTEXT [DERIVED]:"]
            if use_bands:
                lines.append("relationship: " + ", ".join(use_bands))
            for thread in use_threads:
                lines.append(f"ongoing_thread: {thread}")
            for fact in use_facts:
                tail = f" ({fact.status.lower()})" if fact.status else ""
                lines.append(f"relevant_fact: {fact.subject}={fact.value}{tail}")
            if use_summary:
                lines.append(f"summary: {use_summary}")
            if len(lines) <= 1:
                return ""
            return "\n".join(lines)

        text = _build(bands, threads, facts, summary_line)
        if not text:
            return ""
        # Enforce the soft token ceiling deterministically: drop the
        # summary first, then lowest-ranked facts, preserving band labels
        # and thread labels (current-state, cheapest, most relevant).
        try:
            from context_engine.budget import estimate_tokens as _estimate
        except Exception:
            _estimate = None  # type: ignore[assignment]
        if _estimate is not None:
            try:
                while _estimate(text) > max_tokens and (summary_line is not None or facts):
                    if summary_line is not None:
                        summary_line = None
                    elif facts:
                        facts = facts[:-1]
                    else:
                        break
                    text = _build(bands, threads, facts, summary_line)
                    if not text:
                        return ""
            except Exception:
                pass
        return text
    except Exception:
        logger.debug("relationship context render failed (fail-open empty)", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Async assembly over existing retrieval (no new store, no writes)
# ---------------------------------------------------------------------------


async def assemble_relationship_context(
    *,
    creator_id: int | None,
    user_id: int,
    current_message: str = "",
    conversation_state: Any | None = None,
    profile: Any | None = None,
    summary: str | None = None,
    ltm_limit: int = 5,
    fk_limit: int = 5,
) -> RelationshipContext:
    """Assemble one turn of relationship context (fail-open, read-only).

    Reuses the existing retrieval functions with their existing
    signatures (``current_topic`` / ``open_threads`` / ``limit`` /
    ``profile`` — never a ``query=`` argument) plus the already-fetched
    ``profile`` mapping (converted to plain dicts at the boundary so the
    frozen snapshot's MappingProxy values remain usable without changing
    Phase 1–4 domain contracts). Performs zero DB/Redis writes, zero LLM
    calls. Never raises: any failure yields an empty context.
    """
    try:
        if creator_id is None:
            return RelationshipContext()
        try:
            cid = int(creator_id)
            uid = int(user_id)
        except Exception:
            return RelationshipContext()

        plain_profile = to_plain_dict(profile) if profile is not None else None
        if plain_profile is not None and not isinstance(plain_profile, dict):
            plain_profile = None

        # -- Current-turn state (tolerant: dict OR object OR None) --
        current_topic: str | None = None
        open_threads: tuple[str, ...] = ()
        try:
            raw_topic = _field(conversation_state, "current_topic", None)
            if isinstance(raw_topic, str) and raw_topic.strip():
                current_topic = raw_topic.strip()[:80]
            raw_threads = _field(conversation_state, "open_threads", ()) or ()
            if isinstance(raw_threads, (list, tuple)):
                open_threads = tuple(
                    str(t).strip()[:80] for t in raw_threads if isinstance(t, str) and t.strip()
                )[:MAX_RELATIONSHIP_THREADS]
        except Exception:
            current_topic = None
            open_threads = ()

        # -- Descriptive trajectory snapshot (pure read, never written) --
        snapshot: Any | None = None
        try:
            from commerce.relationship_trajectory import (
                derive_relationship_snapshot,
                get_relationship_anchors,
            )

            anchors = get_relationship_anchors(plain_profile, cid)
            snapshot = derive_relationship_snapshot(anchors, None)
        except Exception:
            logger.debug("relationship snapshot derivation failed (fail-open)", exc_info=True)
            snapshot = None

        # -- Existing retrieval reuse (correct signatures; fail-open each) --
        ltm_items: list[dict[str, Any]] = []
        try:
            from commerce.long_term_memory import retrieve_relevant_memories

            ltm_items = await retrieve_relevant_memories(
                cid,
                uid,
                current_topic=current_topic,
                open_threads=open_threads,
                limit=ltm_limit,
                profile=plain_profile,
            ) or []
        except Exception:
            logger.debug("relationship LTM retrieval failed (fail-open)", exc_info=True)
            ltm_items = []
        fk_items: list[dict[str, Any]] = []
        try:
            from commerce.fan_knowledge import retrieve_relevant_knowledge

            fk_items = await retrieve_relevant_knowledge(
                cid,
                uid,
                current_topic=current_topic,
                open_threads=open_threads,
                limit=fk_limit,
                profile=plain_profile,
            ) or []
        except Exception:
            logger.debug("relationship FK retrieval failed (fail-open)", exc_info=True)
            fk_items = []

        bands: dict[str, Any] = {}
        try:
            if snapshot is not None:
                for name in _BAND_ORDER:
                    bands[name] = _field(snapshot, name, None)
        except Exception:
            bands = {}

        return select_relationship_context(
            bands=bands,
            current_topic=current_topic,
            open_threads=open_threads,
            current_message=current_message if isinstance(current_message, str) else "",
            ltm_items=ltm_items,
            fk_items=fk_items,
            summary=summary,
        )
    except Exception:
        logger.debug("assemble_relationship_context failed (fail-open empty)", exc_info=True)
        return RelationshipContext()


__all__ = [
    "MAX_RELATIONSHIP_FACTS",
    "MAX_RELATIONSHIP_THREADS",
    "MAX_RELATIONSHIP_TOKENS",
    "MAX_SUMMARY_CHARS",
    "RELEVANCE_THRESHOLD",
    "RelationshipContext",
    "RelationshipFact",
    "assemble_relationship_context",
    "has_prior_context_evidence",
    "render_relationship_context",
    "select_relationship_context",
    "to_plain_dict",
]
