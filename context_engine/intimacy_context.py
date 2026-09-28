"""Intimacy-aware context selection -- Phase 6.

Single deterministic selection step between the descriptive intimacy
trajectory and context assembly::

    descriptive intimacy trajectory bands (Phase 6, read-only)
            +
    current-turn intimacy evidence (Phase 6, read-only)
            v
    ONE bounded selection (this module)
            v
    ONE bounded data-only representation (``render_intimacy_context``)
            v
    Context Engine snapshot / legacy context list (data, advisory)
            +
    Phase 4 strategy evidence (prior intimate-context reference only)

This module is architecturally parallel to, and independent from,
``context_engine/relationship_context.py`` (Phase 5). The two
selectors never share state, bands, or evidence; relationship context
and intimacy context render as separate blocks.

Design rules enforced here (mirrors Phase 1-5 contracts):

* Pure and deterministic: no DB, no Redis, no network, no LLM, no
  randomness, no global mutable state, no clock reads. The async
  ``assemble_intimacy_context`` helper only reads the already-fetched
  ``profile`` mapping (converted to plain dicts at the boundary) and
  performs no writes; selection itself is pure.
* Single owner: this module is the sole place where a Phase 6
  intimacy-context selection is made. It does not call, wrap, or
  extend the legacy response-mode planner, and it never selects a
  conversational move (Phase 4 remains the sole move selector).
* Descriptive input only: trajectory bands are read, never written,
  never promoted, never reinterpreted. Counters, provenance floats,
  transition mechanics, and internal IDs are never rendered.
* Current context wins: a continuity reference is reported only when
  current-turn evidence genuinely supports it. Band-only evidence
  never produces a reference (bands alone never inform strategy).
* Data, not directives: the rendered block contains band labels only --
  never instructions such as "you should", "escalate", "flirt",
  "sell", and never permission/commerce vocabulary ("allowed",
  "consent", "ready", "offer", "PPV").
* Commerce separation: this module imports no commerce funnel state
  (desire/temperature/readiness/relationship). Commerce-ladder and
  permission tokens are excluded by deny list as defense-in-depth.
* Fail-open: any unusable input yields an empty selection; rendering
  an empty selection yields ``""`` so callers leave the prompt
  byte-identical.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("context_engine.intimacy_context")

# ---------------------------------------------------------------------------
# Bounds (part of the Phase 6 contract; deterministic, testable)
# ---------------------------------------------------------------------------

#: Soft token ceiling for the rendered block. Carved from the existing
#: advisory/context budget: the relationship 200-token budget is
#: unchanged and the global context budget is not increased.
MAX_INTIMACY_TOKENS = 120

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Rendered output must never carry permission, commerce, safety-policy,
#: or escalation vocabulary (exact-token match, lowercase).
#: Defense-in-depth: the selector only emits band labels, but the deny
#: list pins the renderer contract in tests.
_DENY_TOKENS = frozenset(
    {
        # Permission / consent / policy.
        "allowed",
        "allow",
        "permission",
        "consent",
        "consented",
        "authorized",
        "authorization",
        "grant",
        "granted",
        "approve",
        "approved",
        "permit",
        "permitted",
        "forbidden",
        "blocked",
        "refuse",
        "refusal",
        "boundaries",
        "boundary",
        "escalate",
        "escalation",
        "escalating",
        "deescalate",
        "de-escalate",
        "deescalation",
        # Readiness / ??????????-equivalent claims.
        "ready",
        "readiness",
        "adult",
        "adults",
        "verified",
        "verification",
        "age",
        # Commerce.
        "purchase",
        "purchased",
        "offer",
        "offers",
        "price",
        "prices",
        "pricing",
        "product",
        "products",
        "payment",
        "ppv",
        "paid",
        "refund",
        "discount",
        "upsell",
        "sell",
        "selling",
        "buy",
        "buying",
        # Directives.
        "should",
        "must",
        "always",
        "never",
        "flirt",
        "tease",
    }
)

#: Band dimensions rendered (fixed order for deterministic output).
_BAND_ORDER = (
    "romantic",
    "playful",
    "emotional",
    "sexual_conversation",
    "intimate_continuity",
)


# ---------------------------------------------------------------------------
# Typed contracts (immutable, advisory, turn-scoped)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntimacyContext:
    """Selected intimacy context for exactly one turn.

    Turn-scoped, advisory, never persisted.
    ``has_intimate_reference`` is True only when current-turn evidence
    genuinely references prior intimate context -- bands alone never
    set it (mirrors Phase 5 ``has_prior_context`` discipline).
    """

    band_labels: tuple[str, ...] = ()
    has_intimate_reference: bool = False


# ---------------------------------------------------------------------------
# Tolerant readers (duck-typed: dataclass OR mapping OR None; never raises)
# ---------------------------------------------------------------------------


def to_plain_dict(value: Any) -> Any:
    """Recursively convert mappings (incl. MappingProxy) to plain containers.

    The authoritative snapshot freezes nested dicts into
    MappingProxyType, which Phase 1-6 readers reject with strict
    ``isinstance(x, dict)`` checks. Phase 6 converts once at the
    boundary instead of changing those domain contracts. (Same utility
    shape as Phase 5; defined locally so this module imports no
    relationship-context state.)
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
    """Lowercase alphanumeric tokens (same tokenizer family as Phase 5)."""
    try:
        if not isinstance(text, str) or not text:
            return set()
        return set(_TOKEN_RE.findall(text.lower()))
    except Exception:
        return set()


def _band_token(value: Any) -> str | None:
    """Normalize one band value; unknown/missing -> None (omitted)."""
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


def _is_denied(text: str) -> bool:
    """True when rendered text hits the deny list (defense-in-depth)."""
    try:
        return bool(_tokens(text) & _DENY_TOKENS)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Core selector (pure; never raises -- unusable input yields empty context)
# ---------------------------------------------------------------------------


def select_intimacy_context(
    *,
    bands: Any | None = None,
    snapshot: Any | None = None,
    evidence: Any | None = None,
) -> IntimacyContext:
    """Select bounded intimacy context for one turn (pure).

    * ``bands`` / ``snapshot`` supply the descriptive trajectory view
      (mapping or intimacy-snapshot object; unknown values omitted).
    * ``evidence`` supplies current-turn intimacy evidence (mapping or
      ``IntimacyTurnEvidence`` object). A reference flag requires
      genuine current-turn linkage: either a linkage-proven prior
      reference (substantive token overlap between the current
      message and prior intimate texts, decided in the extractor)
      or an intimate-continuity signal on a currently intimate
      topic/thread. Bands alone, and bare co-occurrence of unrelated
      intimacy categories, never produce it.

    Deterministic: same inputs -> identical output. Never raises.
    """
    try:
        return _select(bands=bands, snapshot=snapshot, evidence=evidence)
    except Exception:
        logger.debug("intimacy context selection failed (fail-open empty)", exc_info=True)
        return IntimacyContext()


def _select(
    *,
    bands: Any | None,
    snapshot: Any | None,
    evidence: Any | None,
) -> IntimacyContext:
    # -- Band labels (descriptive metadata only; unknown omitted) --
    source = snapshot if snapshot is not None else bands
    band_labels: list[str] = []
    for name in _BAND_ORDER:
        token = _band_token(_field(source, name, None))
        if token is not None:
            band_labels.append(f"{name}={token}")

    # -- Reference flag: current-turn linkage only --
    # ``prior_intimate_context_reference`` already encodes substantive
    # token overlap (decided in the extractor, which holds both texts).
    # The continuity+topic path covers topic-mediated linkage. Bare
    # co-occurrence (continuity plus known bands, without linkage) is
    # explicitly NOT a reference: historical intimacy alone, current
    # intimacy alone, and unrelated category co-occurrence all fail.
    reference = False
    try:
        if evidence is not None:
            direct = _field(evidence, "prior_intimate_context_reference", False)
            continued = _field(evidence, "intimate_continuity_signal", False)
            current_topic = _field(evidence, "current_intimate_topic", False)
            reference = bool((direct is True) or (continued is True and current_topic is True))
            # Bands alone never suffice: require at least one boolean
            # evidence field to be genuinely True (not merely present).
            if reference:
                any_evidence = any(
                    _field(evidence, name, False) is True
                    for name in (
                        "romantic_signal",
                        "playful_signal",
                        "emotional_signal",
                        "sexual_conversation_signal",
                        "intimate_continuity_signal",
                        "prior_intimate_context_reference",
                    )
                )
                reference = bool(any_evidence)
    except Exception:
        reference = False

    return IntimacyContext(
        band_labels=tuple(band_labels),
        has_intimate_reference=bool(reference),
    )


def has_intimate_context_reference(context: IntimacyContext | None) -> bool:
    """True only when the selection carries a genuine intimate reference."""
    try:
        return bool(isinstance(context, IntimacyContext) and context.has_intimate_reference)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Rendering (pure, bounded, data-only; never raises)
# ---------------------------------------------------------------------------


def render_intimacy_context(
    context: IntimacyContext | None,
    *,
    max_tokens: int = MAX_INTIMACY_TOKENS,
) -> str:
    """Render the advisory intimacy block (pure, fail-open).

    Returns ``""`` when there is nothing safe to render so callers
    leave the prompt byte-identical. Band labels render as data lines
    only, never as instructions, permissions, or commerce hints.
    """
    try:
        if not isinstance(context, IntimacyContext):
            return ""
        bands = [b for b in context.band_labels if isinstance(b, str) and b]
        if not bands:
            return ""
        lines = [
            "INTIMACY CONTEXT [DERIVED]:",
            "descriptive only — authorizes nothing, implies no action.",
        ]
        for label in bands:
            name, _, value = label.partition("=")
            name = name.strip().lower()
            value = value.strip().lower()
            if name not in _BAND_ORDER or not value:
                continue
            lines.append(f"{name}: {value}")
        if len(lines) <= 2:
            return ""
        text = "\n".join(lines)
        # Defense-in-depth deny check (selector only emits band labels,
        # but the contract is pinned here).
        if _is_denied(text):
            logger.debug("intimacy context render denied (fail-open empty)")
            return ""
        # Enforce the soft token ceiling deterministically by dropping
        # the lowest-priority trailing bands first (fixed order).
        try:
            from context_engine.budget import estimate_tokens as _estimate
        except Exception:
            _estimate = None  # type: ignore[assignment]
        if _estimate is not None:
            try:
                _line2 = "descriptive only — authorizes nothing, implies no action."
                _band_lines = list(lines[2:])
                while (
                    _band_lines
                    and _estimate(
                        "INTIMACY CONTEXT [DERIVED]:\n" + _line2 + "\n" + "\n".join(_band_lines)
                    )
                    > max_tokens
                ):
                    _band_lines = _band_lines[:-1]
                if not _band_lines:
                    return ""
                text = "INTIMACY CONTEXT [DERIVED]:\n" + _line2 + "\n" + "\n".join(_band_lines)
            except Exception:
                pass
        return text
    except Exception:
        logger.debug("intimacy context render failed (fail-open empty)", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Async assembly over already-fetched state (no new store, no writes)
# ---------------------------------------------------------------------------


async def assemble_intimacy_context(
    *,
    creator_id: int | None,
    user_id: int,
    current_message: str = "",
    conversation_state: Any | None = None,
    profile: Any | None = None,
    evidence: Any | None = None,
) -> IntimacyContext:
    """Assemble one turn of intimacy context (fail-open, read-only).

    Reads the already-fetched ``profile`` mapping (converted to plain
    dicts at the boundary so the frozen snapshot's MappingProxy values
    remain usable without changing Phase 1-6 domain contracts),
    derives the read-only intimacy snapshot, and selects the bounded
    advisory context. Performs zero DB/Redis writes, zero LLM calls.
    Never raises: any failure yields an empty context.
    """
    try:
        if creator_id is None:
            return IntimacyContext()
        try:
            cid = int(creator_id)
            _ = int(user_id)
        except Exception:
            return IntimacyContext()

        plain_profile = to_plain_dict(profile) if profile is not None else None
        if plain_profile is not None and not isinstance(plain_profile, dict):
            plain_profile = None

        # -- Descriptive trajectory snapshot (pure read, never written) --
        snapshot: Any | None = None
        try:
            from commerce.intimacy_trajectory import (
                derive_intimacy_snapshot,
                get_intimacy_anchors,
            )

            anchors = get_intimacy_anchors(plain_profile, cid)
            snapshot = derive_intimacy_snapshot(anchors, None)
        except Exception:
            logger.debug("intimacy snapshot derivation failed (fail-open)", exc_info=True)
            snapshot = None

        # -- Current-turn evidence: caller-supplied or minimally derived --
        turn_evidence = evidence
        if turn_evidence is None:
            try:
                from commerce.intimacy_evidence import extract_intimacy_evidence

                turn_evidence = extract_intimacy_evidence(
                    user_message=current_message if isinstance(current_message, str) else "",
                    history=None,
                    conversation_state=conversation_state,
                )
            except Exception:
                turn_evidence = None

        return select_intimacy_context(
            snapshot=snapshot,
            evidence=turn_evidence,
        )
    except Exception:
        logger.debug("assemble_intimacy_context failed (fail-open empty)", exc_info=True)
        return IntimacyContext()


__all__ = [
    "MAX_INTIMACY_TOKENS",
    "IntimacyContext",
    "assemble_intimacy_context",
    "has_intimate_context_reference",
    "render_intimacy_context",
    "select_intimacy_context",
    "to_plain_dict",
]
