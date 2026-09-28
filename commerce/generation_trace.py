"""Phase 11 — GenerationTrace read-only renderer (observability, NOT authority).

Assembles a deterministic, bounded, creator-scoped view of one generation
from already-persisted / already-produced descriptive primitives. The trace
describes decisions; it never makes them.

Conceptual flow::

    creator_id + generation_id
        -> existing persisted/transient descriptive primitives
        -> deterministic trace assembly (this module)
        -> bounded GenerationTrace
        -> read-only operator/diagnostic surface

The trace is NOT a new event bus, event store, attribution system, outcome
system, state store, experiment system, config registry, decision engine,
or runtime policy layer. It performs no writes, no LLM calls, no network
calls except the caller's own read queries, and it never feeds its output
back into prompts or decision gates.

Identity: the canonical Phase 10 join ``(creator_id, generation_id)`` is
reused. No second trace ID is minted. ``user_id`` travels only as a scoped
associated identity.

Honesty rules: missing data renders as ``unknown`` / ``unavailable`` /
``not_persisted`` / ``pre_trace`` / ``degraded`` / ``lineage_unavailable``.
Values are never fabricated. Existing confidence/provenance semantics
(relationship/intimacy ``EXPLICIT / SYSTEM_EVENT / STRONG / WEAK`` weights,
fixed rule-strength confidences) are rendered verbatim; no universal
confidence or reward scalar is introduced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Reuse canonical identity helpers (never a second identity mechanism).
from core.generation import is_valid_generation_id

# Section status vocabulary (closed).
OK = "ok"
UNKNOWN = "unknown"
UNAVAILABLE = "unavailable"
NOT_PERSISTED = "not_persisted"
PRE_TRACE = "pre_trace"
DEGRADED = "degraded"
LINEAGE_UNAVAILABLE = "lineage_unavailable"
DERIVABLE_NOT_PERSISTED = "derivable_not_persisted"

STATUSES = frozenset(
    {
        OK,
        UNKNOWN,
        UNAVAILABLE,
        NOT_PERSISTED,
        PRE_TRACE,
        DEGRADED,
        LINEAGE_UNAVAILABLE,
        DERIVABLE_NOT_PERSISTED,
    }
)

# Layer vocabulary for reason-code namespacing and rejected alternatives.
LAYERS = frozenset(
    {
        "relationship",
        "intimacy",
        "boundary",
        "content_transition",
        "strategy",
        "operation",
        "commerce",
        "learning",
    }
)

#: Hard bound on retained rejected alternatives per trace.
MAX_REJECTED = 8

#: Hard bound on retained reason/evidence code lists per section.
MAX_CODES = 12

#: Hard bound on rendered string lengths (IDs exempted: generation IDs are
#: fixed-shape correlation identifiers, not prose).
_MAX_STR = 96


def ns(layer: str, code: Any) -> str:
    """Namespace an existing reason code by layer without renaming it."""
    layer_name = str(layer or "unknown")[:32]
    if layer_name not in LAYERS:
        layer_name = "unknown"
    text = code.value if hasattr(code, "value") else str(code or "unknown")
    return f"{layer_name}:{text.strip()[:_MAX_STR] or 'unknown'}"


def _str(value: Any, limit: int = _MAX_STR) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        try:
            value = value.value
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _codes(values: Any) -> tuple[str, ...]:
    """Bound a raw code collection to clean short strings (no prose)."""
    out: list[str] = []
    try:
        items = list(values) if isinstance(values, (list, tuple, set, frozenset)) else [values]
    except Exception:
        return ()
    for item in items:
        text = _str(item, 64)
        if text:
            out.append(text)
        if len(out) >= MAX_CODES:
            break
    return tuple(out)


def _scope_int(value: Any) -> int | None:
    try:
        number = int(value)
    except Exception:
        return None
    if isinstance(value, bool) or number <= 0:
        return None
    return number


# ── Rejected alternatives (Slice B) ───────────────────────────────────────
#
# Bounded representation of alternatives a decision layer actually
# considered and rejected. Only retained data may be rendered: layers that
# do not persist candidate/rejection information render absent.


@dataclass(frozen=True)
class RejectedAlternative:
    """One actually-considered, rejected candidate (bounded, no prose)."""

    layer: str
    candidate_id: str
    reason: str


def collect_rejected(
    layer: str, items: Any, *, limit: int = MAX_REJECTED
) -> tuple[RejectedAlternative, ...]:
    """Build a deterministically-ordered rejected list from retained items.

    Each item may be a mapping with ``candidate_id``/``id`` and
    ``reason`` keys. Items without both are skipped (never fabricated).
    Unknown layers render under ``unknown`` rather than raising.
    """
    layer_name = str(layer or "unknown")[:32]
    if layer_name not in LAYERS:
        layer_name = "unknown"
    try:
        raw = list(items) if isinstance(items, (list, tuple)) else []
    except Exception:
        return ()
    cleaned: list[RejectedAlternative] = []
    for entry in raw:
        try:
            if not isinstance(entry, dict):
                continue
            candidate = entry.get("candidate_id", entry.get("id"))
            reason = entry.get("reason")
            candidate_text = _str(candidate, 96)
            reason_text = _str(reason, 96)
            if not candidate_text or not reason_text:
                continue
            cleaned.append(
                RejectedAlternative(
                    layer=layer_name, candidate_id=candidate_text, reason=reason_text
                )
            )
        except Exception:
            continue
    cleaned.sort(key=lambda item: (item.layer, item.candidate_id, item.reason))
    return tuple(cleaned[: max(0, min(int(limit), MAX_REJECTED))])


def rejected_from_ledger_snapshot(snapshot: Any) -> tuple[RejectedAlternative, ...]:
    """Derive commerce rejected alternatives from a retained ledger snapshot.

    Renders only what the snapshot kept: ineligible candidates (with their
    retained denial reasons) and eligible-but-unselected candidates (ranked
    lower than the winner, per the retained ranked order). Nothing is
    recomputed; hypothetical losers are never invented.
    """
    try:
        snap = snapshot if isinstance(snapshot, dict) else {}
        ranking = snap.get("ranking") if isinstance(snap.get("ranking"), dict) else {}
        selected = snap.get("selected") if isinstance(snap.get("selected"), dict) else {}
        selected_id = selected.get("definition_id")
        ranked_order = ranking.get("ranked_order") or []
        try:
            winner_rank = (
                list(ranked_order).index(selected_id) if selected_id in list(ranked_order) else -1
            )
        except Exception:
            winner_rank = -1
        items: list[dict[str, str]] = []
        eligible = snap.get("eligible") or []
        if isinstance(eligible, list):
            for candidate in eligible:
                try:
                    if not isinstance(candidate, dict):
                        continue
                    definition_id = candidate.get("definition_id")
                    if definition_id is None or definition_id == selected_id:
                        continue
                    label = _str(definition_id, 32) or "unknown"
                    version = candidate.get("version")
                    candidate_id = f"def:{label}:v:{version}"[:96]
                    try:
                        rank = list(ranked_order).index(definition_id)
                        reason = (
                            "ranked_lower"
                            if winner_rank < 0 or rank > winner_rank
                            else "not_selected"
                        )
                    except Exception:
                        reason = "not_selected"
                    items.append({"candidate_id": candidate_id, "reason": reason})
                except Exception:
                    continue
        ineligible = snap.get("ineligible") or []
        if isinstance(ineligible, list):
            for candidate in ineligible:
                try:
                    if not isinstance(candidate, dict):
                        continue
                    definition_id = candidate.get("definition_id")
                    label = _str(definition_id, 32) or "unknown"
                    reasons = candidate.get("denial_reasons") or []
                    reason_text = None
                    if isinstance(reasons, list):
                        for entry in reasons:
                            reason_text = _str(entry, 96)
                            if reason_text:
                                break
                    items.append(
                        {
                            "candidate_id": f"def:{label}"[:96],
                            "reason": reason_text or "ineligible",
                        }
                    )
                except Exception:
                    continue
        return collect_rejected("commerce", items)
    except Exception:
        return ()


# ── Trace sections (all frozen; every section carries an honest status) ───


@dataclass(frozen=True)
class TraceIdentity:
    creator_id: int
    generation_id: str
    user_id: int | None = None
    turn_timestamp: str | None = None
    status: str = OK


@dataclass(frozen=True)
class RelationshipTrace:
    status: str = UNKNOWN
    bands: tuple[tuple[str, str], ...] = ()
    schema_version: int | None = None
    anchor_timestamp: str | None = None
    provenance: str | None = None
    degraded: bool = False
    current_turn_evidence: tuple[str, ...] = ()
    delta_status: str = DERIVABLE_NOT_PERSISTED
    last_generation_id: str | None = None
    generation_provenance: str = UNAVAILABLE


@dataclass(frozen=True)
class IntimacyTrace:
    status: str = UNKNOWN
    bands: tuple[tuple[str, str], ...] = ()
    direction: str | None = None
    provenance: str | None = None
    schema_version: int | None = None
    anchor_timestamp: str | None = None
    degraded: bool = False
    leader: str | None = None
    last_generation_id: str | None = None
    generation_provenance: str = UNAVAILABLE


@dataclass(frozen=True)
class BoundaryTrace:
    status: str = UNKNOWN
    constraints: tuple[tuple[str, str, str], ...] = ()
    veto_reason: str | None = None
    degraded: bool = False
    provenance: str | None = None
    recovering: tuple[str, ...] = ()
    last_generation_id: str | None = None
    generation_provenance: str = UNAVAILABLE


@dataclass(frozen=True)
class ContentTransitionTrace:
    status: str = UNKNOWN
    state: str | None = None
    reason: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    suppression: str | None = None
    degraded: bool = False


@dataclass(frozen=True)
class StrategyTrace:
    status: str = UNKNOWN
    move: str | None = None
    reason: tuple[str, ...] = ()
    abstained: bool = False
    source: str | None = None
    mode: str | None = None
    objective: str | None = None
    rejected: tuple[RejectedAlternative, ...] = ()


@dataclass(frozen=True)
class OperationTrace:
    status: str = UNKNOWN
    objective: str | None = None
    objective_reason: str | None = None
    allowed: bool | None = None
    blocking_reason: str | None = None
    handoff: bool | None = None
    compact_trace: str | None = None
    experiment_id: str | None = None
    variant: str | None = None


@dataclass(frozen=True)
class CommerceTrace:
    status: str = UNKNOWN
    action: str | None = None
    reason_code: str | None = None
    allowed: bool | None = None
    authorization_basis: str | None = None
    confidence: float | None = None
    config_version: str | None = None
    strategy_version: str | None = None
    ranking_policy_version: str | None = None
    ledger_reference: str | None = None
    ledger_outcome: str | None = None
    rejected: tuple[RejectedAlternative, ...] = ()


@dataclass(frozen=True)
class LearningTrace:
    status: str = UNKNOWN
    exposure_reference: str | None = None
    strategy_family: str | None = None
    attribution_status: str | None = None
    attribution_reason: str | None = None
    maturity_state: str | None = None
    maturity_policy_version: str | None = None
    evidence_quality: str | None = None
    inclusion_reason: str | None = None
    recommendation_reference: str | None = None
    approval_reference: str | None = None
    activation_reference: str | None = None
    rollback_reference: str | None = None
    config_lineage: str = LINEAGE_UNAVAILABLE
    legacy_note: str = "legacy_learning:observable:limited"


@dataclass(frozen=True)
class VersionsTrace:
    config_version: str | None = None
    config_lineage: str = LINEAGE_UNAVAILABLE
    strategy_version: str | None = None
    ranking_policy_version: str | None = None
    experiment_id: str | None = None
    variant: str | None = None
    maturity_policy_version: str | None = None
    schema_versions: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class GenerationTrace:
    """Immutable bounded view of one generation (descriptive only)."""

    identity: TraceIdentity = field(
        default_factory=lambda: TraceIdentity(creator_id=0, generation_id="unknown")
    )
    relationship: RelationshipTrace = field(default_factory=RelationshipTrace)
    intimacy: IntimacyTrace = field(default_factory=IntimacyTrace)
    boundary: BoundaryTrace = field(default_factory=BoundaryTrace)
    content_transition: ContentTransitionTrace = field(default_factory=ContentTransitionTrace)
    strategy: StrategyTrace = field(default_factory=StrategyTrace)
    operation: OperationTrace = field(default_factory=OperationTrace)
    commerce: CommerceTrace = field(default_factory=CommerceTrace)
    learning: LearningTrace = field(default_factory=LearningTrace)
    versions: VersionsTrace = field(default_factory=VersionsTrace)
    complete: bool = False
    missing_sections: tuple[str, ...] = ()
    rendered_as: str = "historical"

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe rendering (bounded codes/IDs/versions/counts only)."""

        def _section(section: Any) -> dict[str, Any]:
            try:
                out: dict[str, Any] = {}
                for key in getattr(section, "__dataclass_fields__", {}):
                    try:
                        value = getattr(section, key)
                    except Exception:
                        continue
                    if isinstance(value, tuple):
                        rendered: list[Any] = []
                        for entry in value:
                            try:
                                if hasattr(entry, "__dataclass_fields__"):
                                    rendered.append(
                                        {
                                            sub: getattr(entry, sub)
                                            for sub in entry.__dataclass_fields__
                                        }
                                    )
                                elif isinstance(entry, (list, tuple)):
                                    rendered.append([str(part)[:_MAX_STR] for part in entry])
                                else:
                                    rendered.append(entry)
                            except Exception:
                                continue
                        out[key] = rendered
                    else:
                        out[key] = value
                return out
            except Exception:
                return {"status": UNKNOWN}

        try:
            identity = self.identity
            return {
                "identity": {
                    "creator_id": identity.creator_id,
                    "user_id": identity.user_id,
                    "generation_id": identity.generation_id,
                    "turn_timestamp": identity.turn_timestamp,
                    "status": identity.status,
                },
                "relationship": _section(self.relationship),
                "intimacy": _section(self.intimacy),
                "boundary": _section(self.boundary),
                "content_transition": _section(self.content_transition),
                "strategy": _section(self.strategy),
                "operation": _section(self.operation),
                "commerce": _section(self.commerce),
                "learning": _section(self.learning),
                "versions": _section(self.versions),
                "complete": self.complete,
                "missing_sections": list(self.missing_sections),
                "rendered_as": self.rendered_as,
            }
        except Exception:
            return {
                "identity": {"status": UNKNOWN},
                "complete": False,
                "missing_sections": ["all"],
                "rendered_as": "historical",
            }


# ── Section builders (pure, total: never raise on untrusted input) ────────


def build_identity(
    *,
    creator_id: Any,
    generation_id: Any,
    user_id: Any = None,
    turn_timestamp: Any = None,
) -> TraceIdentity:
    """Validate trace identity (fail-closed: bad identity raises)."""
    creator = _scope_int(creator_id)
    if creator is None:
        raise ValueError("creator_id is required")
    generation = str(generation_id).strip() if isinstance(generation_id, str) else ""
    if not generation or not is_valid_generation_id(generation):
        raise ValueError("generation_id is required")
    user = _scope_int(user_id)
    stamp = _str(turn_timestamp, 64)
    return TraceIdentity(
        creator_id=creator,
        generation_id=generation,
        user_id=user,
        turn_timestamp=stamp,
        status=OK,
    )


def build_relationship(anchors: Any, *, current_turn_evidence: Any = None) -> RelationshipTrace:
    """Render relationship anchors descriptively (existing semantics kept)."""
    try:
        if not isinstance(anchors, dict) or not anchors:
            return RelationshipTrace(status=UNAVAILABLE)
        bands: list[tuple[str, str]] = []
        raw_bands = anchors.get("bands")
        if isinstance(raw_bands, dict):
            for name in sorted(raw_bands):
                value = _str(raw_bands.get(name), 32)
                if value:
                    bands.append((str(name)[:32], value))
        degraded = bool(anchors.get("degraded", False))
        schema_version = anchors.get("schema_version")
        try:
            schema_version = int(schema_version) if schema_version is not None else None
        except Exception:
            schema_version = None
        provenance_value = anchors.get("last_provenance")
        provenance = _str(provenance_value, 32)
        last_generation = anchors.get("last_generation_id")
        last_generation_id = _str(last_generation, 128)
        generation_provenance = OK if last_generation_id else UNAVAILABLE
        # Never reinterpret bands as intimacy/consent/commerce (descriptive only).
        return RelationshipTrace(
            status=DEGRADED if degraded else OK,
            bands=tuple(bands),
            schema_version=schema_version,
            anchor_timestamp=_str(anchors.get("last_seen_at"), 64),
            provenance=provenance,
            degraded=degraded,
            current_turn_evidence=_codes(current_turn_evidence),
            delta_status=DERIVABLE_NOT_PERSISTED,
            last_generation_id=last_generation_id,
            generation_provenance=generation_provenance,
        )
    except Exception:
        return RelationshipTrace(status=UNKNOWN)


def build_intimacy(anchors: Any, *, leader: Any = None) -> IntimacyTrace:
    """Render intimacy anchors descriptively (never authorization/consent)."""
    try:
        if not isinstance(anchors, dict) or not anchors:
            return IntimacyTrace(status=UNAVAILABLE)
        bands: list[tuple[str, str]] = []
        raw_bands = anchors.get("bands")
        if isinstance(raw_bands, dict):
            for name in sorted(raw_bands):
                value = _str(raw_bands.get(name), 32)
                if value:
                    bands.append((str(name)[:32], value))
        degraded = bool(anchors.get("degraded", False))
        schema_version = anchors.get("schema_version")
        try:
            schema_version = int(schema_version) if schema_version is not None else None
        except Exception:
            schema_version = None
        last_generation = anchors.get("last_generation_id")
        last_generation_id = _str(last_generation, 128)
        leader_text = _str(leader, 32)
        if leader_text not in ("user-led", "assistant-led", "ambiguous", None):
            leader_text = None
        return IntimacyTrace(
            status=DEGRADED if degraded else OK,
            bands=tuple(bands),
            direction=_str(anchors.get("direction"), 32),
            provenance=_str(anchors.get("last_provenance"), 32),
            schema_version=schema_version,
            anchor_timestamp=_str(anchors.get("last_seen_at"), 64),
            degraded=degraded,
            leader=leader_text,
            last_generation_id=last_generation_id,
            generation_provenance=OK if last_generation_id else UNAVAILABLE,
        )
    except Exception:
        return IntimacyTrace(status=UNKNOWN)


def build_boundary(snapshot: Any, *, provenance: Any = None) -> BoundaryTrace:
    """Render a boundary snapshot (bounded type/scope/reason only)."""
    try:
        if snapshot is None:
            return BoundaryTrace(status=UNAVAILABLE)
        data = snapshot if isinstance(snapshot, dict) else {}
        degraded = bool(data.get("degraded", False))
        constraints: list[tuple[str, str, str]] = []
        for source_key in ("active", "constraints"):
            raw = data.get(source_key)
            items = []
            if isinstance(raw, dict):
                items = list(raw.values())
            elif isinstance(raw, (list, tuple)):
                items = list(raw)
            for entry in items:
                try:
                    if isinstance(entry, dict):
                        entry_type = _str(entry.get("boundary_type", entry.get("type")), 32)
                        scope = _str(entry.get("scope"), 32)
                        state = _str(entry.get("status", entry.get("state")), 32)
                    else:
                        entry_type, scope, state = _str(entry, 32), None, None
                    if entry_type:
                        constraints.append((entry_type, scope or "unknown", state or "unknown"))
                    if len(constraints) >= MAX_CODES:
                        break
                except Exception:
                    continue
            if constraints:
                break
        # Deduplicate deterministically, keep bounded count.
        constraints = sorted(set(constraints))[:MAX_CODES]
        recovering = _codes(data.get("recovering"))
        last_generation = data.get("last_generation_id")
        last_generation_id = _str(last_generation, 128)
        return BoundaryTrace(
            status=DEGRADED if degraded else (OK if (constraints or data) else UNAVAILABLE),
            constraints=tuple(constraints),
            veto_reason=_str(data.get("veto_reason"), 96),
            degraded=degraded,
            provenance=_str(provenance if provenance is not None else data.get("provenance"), 64),
            recovering=recovering,
            last_generation_id=last_generation_id,
            generation_provenance=OK if last_generation_id else UNAVAILABLE,
        )
    except Exception:
        return BoundaryTrace(status=UNKNOWN)


def build_content_transition(decision: Any) -> ContentTransitionTrace:
    """Render a Phase 8 content-transition decision (turn-scoped)."""
    try:
        if decision is None:
            return ContentTransitionTrace(status=NOT_PERSISTED)
        data = decision if isinstance(decision, dict) else {}
        state = _str(data.get("state", data.get("transition")), 32)
        valid = {"NONE", "ACKNOWLEDGE_ONLY", "BRIDGE", "DEFER_TO_COMMERCE"}
        if state not in valid:
            return ContentTransitionTrace(status=UNKNOWN)
        degraded = bool(data.get("degraded", False))
        return ContentTransitionTrace(
            status=DEGRADED if degraded else OK,
            state=state,
            reason=_codes(data.get("reason")),
            evidence=tuple(flag for flag in _codes(data.get("evidence")) if flag),
            suppression=_str(data.get("suppression"), 96),
            degraded=degraded,
        )
    except Exception:
        return ContentTransitionTrace(status=UNKNOWN)


def build_strategy(
    decision: Any, *, rejected: tuple[RejectedAlternative, ...] = ()
) -> StrategyTrace:
    """Render an already-produced strategy decision (never recompute)."""
    try:
        if decision is None:
            return StrategyTrace(status=NOT_PERSISTED)
        data = decision if isinstance(decision, dict) else {}
        move = _str(data.get("move", data.get("strategy_family", data.get("strategy"))), 64)
        abstained = bool(data.get("abstained", data.get("abstain", False)))
        if move is None and not abstained:
            return StrategyTrace(status=UNKNOWN)
        return StrategyTrace(
            status=OK,
            move=move,
            reason=_codes(data.get("reason", data.get("reason_codes"))),
            abstained=abstained,
            source=_str(data.get("source", data.get("strategy_source")), 64),
            mode=_str(data.get("mode", data.get("strategy_mode")), 32),
            objective=_str(data.get("objective"), 64),
            rejected=tuple(rejected)[:MAX_REJECTED],
        )
    except Exception:
        return StrategyTrace(status=UNKNOWN)


def build_operation(decision: Any) -> OperationTrace:
    """Render an already-produced operation decision (compact trace kept)."""
    try:
        if decision is None:
            return OperationTrace(status=NOT_PERSISTED)
        data = decision if isinstance(decision, dict) else {}
        allowed = data.get("allowed")
        if allowed is not None:
            allowed = bool(allowed)
        handoff = data.get("handoff", data.get("handoff_required"))
        if handoff is not None:
            handoff = bool(handoff)
        return OperationTrace(
            status=OK,
            objective=_str(data.get("objective"), 64),
            objective_reason=_str(data.get("objective_reason"), 96),
            allowed=allowed,
            blocking_reason=_str(data.get("blocking_reason"), 96),
            handoff=handoff,
            compact_trace=_str(data.get("compact_trace", data.get("decision_trace")), 512),
            experiment_id=_str(data.get("experiment_id"), 96),
            variant=_str(data.get("variant"), 32),
        )
    except Exception:
        return OperationTrace(status=UNKNOWN)


def build_commerce(
    decision: Any,
    *,
    ledger: Any = None,
    rejected: tuple[RejectedAlternative, ...] = (),
) -> CommerceTrace:
    """Render an already-produced commerce decision plus ledger reference."""
    try:
        ledger_data = ledger if isinstance(ledger, dict) else {}
        ledger_reference = _str(
            ledger_data.get("opportunity_id", ledger_data.get("sealed_offer_id")), 64
        )
        ledger_outcome = _str(ledger_data.get("outcome_state"), 32)
        if decision is None:
            if not ledger_data:
                return CommerceTrace(status=NOT_PERSISTED)
            # Ledger-only rendering stays honest about the missing decision.
            return CommerceTrace(
                status=PRE_TRACE,
                ledger_reference=ledger_reference,
                ledger_outcome=ledger_outcome,
                rejected=tuple(rejected)[:MAX_REJECTED],
            )
        data = decision if isinstance(decision, dict) else {}
        action = _str(data.get("action"), 64)
        reason_code = _str(data.get("reason_code", data.get("reason")), 96)
        allowed = data.get("allowed")
        if allowed is not None:
            allowed = bool(allowed)
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        confidence = data.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except Exception:
            confidence = None
        return CommerceTrace(
            status=OK,
            action=action,
            reason_code=reason_code,
            allowed=allowed,
            authorization_basis=_str(
                data.get("authorization_basis", metadata.get("authorization_basis")), 64
            ),
            confidence=confidence,
            config_version=_str(data.get("config_version", metadata.get("config_version")), 64),
            strategy_version=_str(
                data.get("strategy_version", metadata.get("strategy_version")), 64
            ),
            ranking_policy_version=_str(
                data.get("ranking_policy_version", metadata.get("ranking_policy_version")), 32
            ),
            ledger_reference=ledger_reference,
            ledger_outcome=ledger_outcome,
            rejected=tuple(rejected)[:MAX_REJECTED],
        )
    except Exception:
        return CommerceTrace(status=UNKNOWN)


def build_learning(
    *,
    exposure: Any = None,
    attribution_status: Any = None,
    attribution_reason: Any = None,
    maturity_state: Any = None,
    maturity_policy_version: Any = None,
    evidence_quality: Any = None,
    recommendation_reference: Any = None,
    approval_reference: Any = None,
    activation_reference: Any = None,
    rollback_reference: Any = None,
    legacy_counts: Any = None,
) -> LearningTrace:
    """Render Phase 10 learning references (references only, never rerun)."""
    try:
        exposure_data = exposure if isinstance(exposure, dict) else {}
        exposure_reference = _str(
            exposure_data.get("generation_id", exposure_data.get("exposure_id")), 128
        )
        strategy_family = _str(exposure_data.get("strategy_family"), 64)
        status_text = _str(attribution_status, 32)
        maturity_text = _str(maturity_state, 32)
        if exposure_reference is None and status_text is None and maturity_text is None:
            return LearningTrace(status=UNAVAILABLE)
        inclusion = None
        mature = maturity_text == "MATURE" if maturity_text else None
        if mature is True and status_text == "attributed":
            inclusion = "included_mature_attributed"
        elif mature is False:
            inclusion = "excluded_immature"
        elif status_text in ("censored", "unavailable", "unattributed", "missing"):
            inclusion = f"excluded_{status_text}"
        legacy_note = "legacy_learning:observable:limited"
        if isinstance(legacy_counts, dict) and legacy_counts:
            parts = []
            for key in ("attempt_count", "positive_count", "purchase_count"):
                try:
                    parts.append(f"{key}={int(legacy_counts.get(key, 0))}")
                except Exception:
                    continue
            if parts:
                legacy_note = f"legacy_learning:observable:limited:{':'.join(parts)}"[:192]
        lineage_known = any(
            _str(value, 128)
            for value in (
                recommendation_reference,
                approval_reference,
                activation_reference,
                rollback_reference,
            )
        )
        return LearningTrace(
            status=OK,
            exposure_reference=exposure_reference,
            strategy_family=strategy_family,
            attribution_status=status_text,
            attribution_reason=_str(attribution_reason, 128),
            maturity_state=maturity_text,
            maturity_policy_version=_str(maturity_policy_version, 32),
            evidence_quality=_str(evidence_quality, 32),
            inclusion_reason=inclusion,
            recommendation_reference=_str(recommendation_reference, 128),
            approval_reference=_str(approval_reference, 128),
            activation_reference=_str(activation_reference, 128),
            rollback_reference=_str(rollback_reference, 128),
            config_lineage=OK if lineage_known else LINEAGE_UNAVAILABLE,
            legacy_note=legacy_note,
        )
    except Exception:
        return LearningTrace(status=UNKNOWN)


def build_versions(
    *,
    config_version: Any = None,
    strategy_version: Any = None,
    ranking_policy_version: Any = None,
    experiment_id: Any = None,
    variant: Any = None,
    maturity_policy_version: Any = None,
    schema_versions: Any = None,
) -> VersionsTrace:
    """Render version provenance honestly (legacy stays legacy)."""
    try:
        config_text = _str(config_version, 64)
        lineage = OK
        if not config_text:
            lineage = UNKNOWN
        elif config_text in ("unversioned-legacy", "unknown"):
            lineage = PRE_TRACE if config_text == "unversioned-legacy" else UNKNOWN
        schemas: list[tuple[str, int]] = []
        try:
            items = list(schema_versions) if isinstance(schema_versions, (list, tuple)) else []
        except Exception:
            items = []
        for entry in items:
            try:
                if isinstance(entry, (list, tuple)) and len(entry) == 2:
                    schemas.append((str(entry[0])[:48], int(entry[1])))
                if len(schemas) >= MAX_CODES:
                    break
            except Exception:
                continue
        return VersionsTrace(
            config_version=config_text,
            config_lineage=lineage,
            strategy_version=_str(strategy_version, 64),
            ranking_policy_version=_str(ranking_policy_version, 32),
            experiment_id=_str(experiment_id, 96),
            variant=_str(variant, 32),
            maturity_policy_version=_str(maturity_policy_version, 32),
            schema_versions=tuple(sorted(schemas)),
        )
    except Exception:
        return VersionsTrace()


# ── Assembly (pure; identity validated, sections total) ───────────────────


def assemble_trace(
    *,
    creator_id: Any,
    generation_id: Any,
    user_id: Any = None,
    turn_timestamp: Any = None,
    telemetry: Any = None,
    ledger: Any = None,
    ledger_snapshot: Any = None,
    relationship_anchors: Any = None,
    relationship_evidence: Any = None,
    intimacy_anchors: Any = None,
    intimacy_leader: Any = None,
    boundary_snapshot: Any = None,
    boundary_provenance: Any = None,
    content_decision: Any = None,
    strategy_decision: Any = None,
    operation_decision: Any = None,
    commerce_decision: Any = None,
    exposure: Any = None,
    attribution_status: Any = None,
    attribution_reason: Any = None,
    maturity_state: Any = None,
    maturity_policy_version: Any = None,
    evidence_quality: Any = None,
    learning_refs: Any = None,
    legacy_counts: Any = None,
    rendered_as: str = "historical",
) -> GenerationTrace:
    """Assemble one bounded GenerationTrace from descriptive inputs.

    Identity failures raise (fail-closed). Every section builder is total:
    partial input yields per-section unknown/unavailable markers, never a
    top-level failure. No writes, no recomputation, no authority.
    """
    identity = build_identity(
        creator_id=creator_id,
        generation_id=generation_id,
        user_id=user_id,
        turn_timestamp=turn_timestamp,
    )
    telemetry_data = telemetry if isinstance(telemetry, dict) else {}
    ledger_data = ledger if isinstance(ledger, dict) else {}

    # Prefer explicit section inputs; fall back to telemetry fragments only
    # for fields telemetry already persists (never undeclared attrs).
    strategy_input = strategy_decision
    if strategy_input is None and telemetry_data:
        move = telemetry_data.get("strategy_selected")
        if move is not None or telemetry_data.get("strategy_source") is not None:
            strategy_input = {
                "strategy": move,
                "source": telemetry_data.get("strategy_source"),
                "mode": telemetry_data.get("strategy_mode"),
                "objective": telemetry_data.get("conversation_objective"),
                "reason": telemetry_data.get("objective_reason"),
            }
    operation_input = operation_decision
    if operation_input is None and telemetry_data:
        if (
            telemetry_data.get("operation_allowed") is not None
            or telemetry_data.get("decision_trace") is not None
            or telemetry_data.get("pressure_score") is not None
        ):
            operation_input = {
                "objective": telemetry_data.get("conversation_objective"),
                "objective_reason": telemetry_data.get("objective_reason"),
                "allowed": telemetry_data.get("operation_allowed"),
                "blocking_reason": telemetry_data.get("operation_block_reason"),
                "handoff": telemetry_data.get("handoff_required"),
                "decision_trace": telemetry_data.get("decision_trace"),
                "experiment_id": telemetry_data.get("experiment_id"),
                "variant": telemetry_data.get("experiment_variant"),
            }
    content_input = content_decision
    commerce_input = commerce_decision
    snapshot = ledger_snapshot
    if snapshot is None and ledger_data:
        snapshot = ledger_data.get("decision_snapshot") or ledger_data.get("snapshot")
    try:
        import json as _json

        if isinstance(snapshot, str):
            snapshot = _json.loads(snapshot)
    except Exception:
        snapshot = None

    commerce_rejected = rejected_from_ledger_snapshot(snapshot)

    refs = learning_refs if isinstance(learning_refs, dict) else {}
    relationship = build_relationship(
        relationship_anchors, current_turn_evidence=relationship_evidence
    )
    intimacy = build_intimacy(intimacy_anchors, leader=intimacy_leader)
    boundary = build_boundary(boundary_snapshot, provenance=boundary_provenance)
    content = build_content_transition(content_input)
    strategy = build_strategy(strategy_input)
    operation = build_operation(operation_input)
    commerce = build_commerce(commerce_input, ledger=ledger_data, rejected=commerce_rejected)
    learning = build_learning(
        exposure=exposure,
        attribution_status=(
            attribution_status
            if attribution_status is not None
            else telemetry_data.get("attribution_status")
        ),
        attribution_reason=attribution_reason,
        maturity_state=(
            maturity_state if maturity_state is not None else telemetry_data.get("maturity_state")
        ),
        maturity_policy_version=(
            maturity_policy_version
            if maturity_policy_version is not None
            else telemetry_data.get("maturity_policy_version")
        ),
        evidence_quality=evidence_quality,
        recommendation_reference=refs.get("recommendation_version", refs.get("recommendation")),
        approval_reference=refs.get("approval"),
        activation_reference=refs.get("activation"),
        rollback_reference=refs.get("rollback"),
        legacy_counts=legacy_counts,
    )
    exposure_data = exposure if isinstance(exposure, dict) else {}
    # Experiment assignment precedence: persisted telemetry first, then the
    # same generation's exposure record (both bounded, both already stored).
    # Never invented; unknown stays unknown.
    experiment_value = telemetry_data.get("experiment_id")
    if experiment_value is None:
        experiment_value = exposure_data.get("experiment_id")
    variant_value = telemetry_data.get("experiment_variant")
    if variant_value is None:
        variant_value = exposure_data.get("experiment_variant", exposure_data.get("variant"))
    versions = build_versions(
        config_version=telemetry_data.get("config_version"),
        strategy_version=telemetry_data.get("strategy_version"),
        ranking_policy_version=telemetry_data.get("ranking_policy_version"),
        experiment_id=experiment_value,
        variant=variant_value,
        maturity_policy_version=telemetry_data.get("maturity_policy_version"),
        schema_versions=[
            ("relationship", (relationship_anchors or {}).get("schema_version"))
            if isinstance(relationship_anchors, dict)
            else None,
            ("intimacy", (intimacy_anchors or {}).get("schema_version"))
            if isinstance(intimacy_anchors, dict)
            else None,
        ],
    )

    sections = {
        "relationship": relationship,
        "intimacy": intimacy,
        "boundary": boundary,
        "content_transition": content,
        "strategy": strategy,
        "operation": operation,
        "commerce": commerce,
        "learning": learning,
    }
    missing = tuple(
        sorted(
            name
            for name, section in sections.items()
            if getattr(section, "status", UNKNOWN) in (UNKNOWN, UNAVAILABLE, NOT_PERSISTED)
        )
    )
    complete = not missing
    rendered = rendered_as if rendered_as in ("historical", "live") else "historical"
    return GenerationTrace(
        identity=identity,
        relationship=relationship,
        intimacy=intimacy,
        boundary=boundary,
        content_transition=content,
        strategy=strategy,
        operation=operation,
        commerce=commerce,
        learning=learning,
        versions=versions,
        complete=complete,
        missing_sections=missing,
        rendered_as=rendered,
    )


# ── Read-path assembly (I/O isolated here; still read-only) ───────────────


async def build_generation_trace(
    creator_id: Any,
    generation_id: Any,
    *,
    user_id: Any = None,
) -> GenerationTrace:
    """Fetch persisted descriptive rows and assemble the trace (read-only).

    Point lookups only: one telemetry row, one ledger row, one profile
    facts read. Every fetch is best-effort; missing rows render as
    unavailable sections. Never writes. Raises only on invalid identity
    or when the telemetry row itself is absent (fail-closed 404 path).
    """
    identity = build_identity(creator_id=creator_id, generation_id=generation_id, user_id=user_id)
    telemetry_row: dict[str, Any] | None = None
    ledger_row: dict[str, Any] | None = None
    facts: dict[str, Any] | None = None
    try:
        from db.postgres import get_pool
    except Exception:
        get_pool = None  # type: ignore[assignment]
    if get_pool is not None:
        try:
            pool = await get_pool()
        except Exception:
            pool = None
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    try:
                        row = await conn.fetchrow(
                            "SELECT * FROM generation_telemetry WHERE generation_id=$1 AND creator_id=$2",
                            identity.generation_id,
                            identity.creator_id,
                        )
                    except Exception:
                        row = None
                    if row is None:
                        raise LookupError("generation not found")
                    try:
                        telemetry_row = dict(row)
                    except Exception:
                        telemetry_row = None
                    try:
                        ledger_candidate = await conn.fetchrow(
                            "SELECT * FROM commerce_opportunity_decisions WHERE generation_id=$1 AND creator_id=$2",
                            identity.generation_id,
                            identity.creator_id,
                        )
                    except Exception:
                        ledger_candidate = None
                    try:
                        ledger_row = (
                            dict(ledger_candidate) if ledger_candidate is not None else None
                        )
                    except Exception:
                        ledger_row = None
                    try:
                        profile_row = await conn.fetchrow(
                            "SELECT facts FROM user_profiles WHERE user_id=$1",
                            telemetry_row.get("user_id") if telemetry_row else (user_id or 0),
                        )
                    except Exception:
                        profile_row = None
                    try:
                        raw_facts = profile_row["facts"] if profile_row is not None else None
                    except Exception:
                        raw_facts = None
                    try:
                        import json as _json

                        if isinstance(raw_facts, str):
                            facts = _json.loads(raw_facts)
                        elif isinstance(raw_facts, dict):
                            facts = raw_facts
                    except Exception:
                        facts = None
            except LookupError:
                raise
            except Exception:
                pass
    if telemetry_row is None:
        raise LookupError("generation not found")

    relationship_anchors = None
    intimacy_anchors = None
    boundary_snapshot = None
    exposure = None
    legacy_counts = None
    try:
        scoped = (
            facts.get("relationship_trajectory_by_creator", {}) if isinstance(facts, dict) else {}
        )
        block = (
            (scoped.get(str(identity.creator_id), {}) or scoped.get(identity.creator_id, {}))
            if isinstance(scoped, dict)
            else {}
        )
        relationship_anchors = dict(block) if isinstance(block, dict) and block else None
    except Exception:
        relationship_anchors = None
    try:
        scoped = facts.get("intimacy_trajectory_by_creator", {}) if isinstance(facts, dict) else {}
        block = (
            (scoped.get(str(identity.creator_id), {}) or scoped.get(identity.creator_id, {}))
            if isinstance(scoped, dict)
            else {}
        )
        intimacy_anchors = dict(block) if isinstance(block, dict) and block else None
    except Exception:
        intimacy_anchors = None
    try:
        scoped = facts.get("boundary_state_by_creator", {}) if isinstance(facts, dict) else {}
        block = (
            (scoped.get(str(identity.creator_id), {}) or scoped.get(identity.creator_id, {}))
            if isinstance(scoped, dict)
            else {}
        )
        if isinstance(block, dict) and block:
            constraints = block.get("constraints")
            boundary_snapshot = {
                "constraints": constraints,
                "degraded": False,
                "provenance": "durable",
                "last_generation_id": block.get("last_generation_id"),
            }
    except Exception:
        boundary_snapshot = None
    try:
        scoped = facts.get("strategy_exposures_by_creator", {}) if isinstance(facts, dict) else {}
        exposures = (
            (scoped.get(str(identity.creator_id), []) or scoped.get(identity.creator_id, []))
            if isinstance(scoped, dict)
            else []
        )
        if isinstance(exposures, list):
            for entry in exposures:
                try:
                    if (
                        isinstance(entry, dict)
                        and entry.get("generation_id") == identity.generation_id
                    ):
                        exposure = dict(entry)
                        break
                except Exception:
                    continue
    except Exception:
        exposure = None
    try:
        scoped = facts.get("strategy_evidence_by_creator", {}) if isinstance(facts, dict) else {}
        evidence_map = (
            (scoped.get(str(identity.creator_id), {}) or scoped.get(identity.creator_id, {}))
            if isinstance(scoped, dict)
            else {}
        )
        family = (exposure or {}).get("strategy_family") if isinstance(exposure, dict) else None
        if isinstance(evidence_map, dict) and family and isinstance(evidence_map.get(family), dict):
            record = evidence_map[family]
            legacy_counts = {
                key: record.get(key, 0)
                for key in ("attempt_count", "positive_count", "purchase_count")
            }
    except Exception:
        legacy_counts = None

    turn_timestamp = None
    try:
        created = telemetry_row.get("created_at")
        turn_timestamp = (
            created.isoformat()
            if hasattr(created, "isoformat")
            else (str(created) if created is not None else None)
        )
    except Exception:
        turn_timestamp = None

    return assemble_trace(
        creator_id=identity.creator_id,
        generation_id=identity.generation_id,
        user_id=telemetry_row.get("user_id", user_id),
        turn_timestamp=turn_timestamp,
        telemetry=telemetry_row,
        ledger=ledger_row,
        relationship_anchors=relationship_anchors,
        intimacy_anchors=intimacy_anchors,
        boundary_snapshot=boundary_snapshot,
        exposure=exposure,
        legacy_counts=legacy_counts,
        rendered_as="historical",
    )


__all__ = [
    "OK",
    "UNKNOWN",
    "UNAVAILABLE",
    "NOT_PERSISTED",
    "PRE_TRACE",
    "DEGRADED",
    "LINEAGE_UNAVAILABLE",
    "DERIVABLE_NOT_PERSISTED",
    "LAYERS",
    "MAX_REJECTED",
    "MAX_CODES",
    "RejectedAlternative",
    "TraceIdentity",
    "RelationshipTrace",
    "IntimacyTrace",
    "BoundaryTrace",
    "ContentTransitionTrace",
    "StrategyTrace",
    "OperationTrace",
    "CommerceTrace",
    "LearningTrace",
    "VersionsTrace",
    "GenerationTrace",
    "ns",
    "collect_rejected",
    "rejected_from_ledger_snapshot",
    "build_identity",
    "build_relationship",
    "build_intimacy",
    "build_boundary",
    "build_content_transition",
    "build_strategy",
    "build_operation",
    "build_commerce",
    "build_learning",
    "build_versions",
    "assemble_trace",
    "build_generation_trace",
]
