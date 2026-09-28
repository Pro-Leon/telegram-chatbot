"""One-call LLM response contract (Phase 75B).

Defines the structured response schema for the single qwen2.5 generation
call that replaces the 3-LLM pipeline. The one-call contract produces:

1. Reply text (main conversational output)
2. Commerce signals (18 advisory fields)
3. Confidence score (self-assessment)
4. Needs handoff flag (operator routing)

The schema is designed for qwen2.5's structured JSON output mode.
All fields are advisory — no field directly authorizes price, payment, or access.

Security boundary:
- PPV authority remains in deterministic engine (commerce/execution.py)
- Persona enforcement remains in deterministic validation (commerce/persona_validation.py)
- Commerce decisions remain in deterministic engine (commerce/decision.py)
"""

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from commerce.signals import (
    _NEGATIVE_INTENTS,
    INTENT_CATEGORIES,
    MAX_EVIDENCE_ITEM_LENGTH,
    MAX_EVIDENCE_ITEMS,
    CommerceSignals,
    _contains_payment_data,
)

logger = logging.getLogger("one_call")


class OneCallReply(BaseModel):
    """Structured response from single qwen2.5 generation.

    Combines reply text, commerce signals, confidence, and handoff flag
    into a single JSON output. All fields are advisory.

    Schema is intentionally minimal to maximize qwen2.5 compliance.
    """
    model_config = ConfigDict(extra="forbid")

    # Main reply text
    reply: str = Field(..., min_length=1, max_length=2000)

    # Commerce signals (18 fields)
    commerce_signals: CommerceSignals = Field(default_factory=CommerceSignals.low_information)

    # Confidence self-assessment (0.0 to 1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # Operator handoff flag
    needs_handoff: bool = Field(default=False)

    @field_validator("reply")
    @classmethod
    def _validate_reply(cls, v: str) -> str:
        """Strip whitespace, reject empty after strip."""
        v = v.strip()
        if not v:
            raise ValueError("reply must not be empty")
        return v


@dataclass
class OneCallResult:
    """Processed one-call result after deterministic validation.

    Contains validated reply, signals, quality metrics, and flags.
    Ready for routing decision.
    """
    reply: str
    signals: CommerceSignals
    confidence: float
    needs_handoff: bool
    # Raw advisory LLM handoff flag (Phase 5/H3). needs_handoff above is the
    # merged signal (advisory + deterministic triggers such as validation
    # failure, safety flags, poor quality); advisory_handoff preserves the
    # model's own flag verbatim so routing can distinguish a bare advisory
    # signal (must not veto by itself) from deterministically corroborated
    # handoff. Never business authority.
    advisory_handoff: bool = False
    quality_score: float = 0.0
    quality_flags: list[str] = field(default_factory=list)
    safety_flags: list[str] = field(default_factory=list)
    # Phase 7: deterministic boundary outcome. boundary_violations lists
    # the active BoundaryType values the reply violates (empty when the
    # reply respects every active constraint or no constraint is active);
    # boundary_action is the deterministic BoundaryAction for the turn
    # (None when unviolated). Never LLM-derived.
    boundary_violations: list[str] = field(default_factory=list)
    boundary_action: str | None = None
    is_valid: bool = True
    validation_error: str | None = None
    # Phase 87: provider identity & token counts (fail-open, never affects validation)
    provider_name: str | None = None
    model_name: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    generation_kind: str = "one_call"  # one_call | ppv_second_generation
    call_index: int = 1


def build_onecall_json_schema() -> dict[str, Any]:
    """Build a llama.cpp-ready JSON Schema for ``OneCallReply`` constrained decoding.

    Derived from the executable contract (``OneCallReply.model_json_schema()``),
    so the constraint stays synchronized with the Pydantic model. Deterministic:
    no I/O, no randomness, no clock.

    Adaptations applied (documented, syntactic only):
    - Local ``$ref`` entries (``#/$defs/...``) are inlined; ``$defs`` is dropped.
    - Non-standard numeric metadata (``ge``/``le``/``gt``/``lt``) becomes
      standard ``minimum``/``maximum``/``exclusiveMinimum``/``exclusiveMaximum``.
    - ``primary_intent`` enum is set from ``INTENT_CATEGORIES`` and
      ``negative_intent_tags`` items from ``_NEGATIVE_INTENTS`` (both enforced
      by Pydantic field validators but absent from the derived schema).
    - ``requested_price`` keeps the positive-finite application rule as
      ``exclusiveMinimum: 0`` on the number branch (JSON numbers are finite
      by construction under constrained decoding).
    - ``description`` annotations are stripped (size only; titles/defaults kept).
    - Top-level ``additionalProperties``/``required`` are preserved exactly as
      derived (``extra="forbid"``; only truly required fields required).
      Nested ``CommerceSignals`` keeps no ``additionalProperties: false``,
      faithful to its ``extra="ignore"`` application behavior.

    NOT expressible here and remaining application-validation responsibilities:
    evidence payment-data ban, whitespace-only reply rejection, unknown
    ``intent_tags`` dropping, NaN/Inf handling, and all deterministic
    post-schema commerce/safety/grounding/quality/handoff checks.
    Constrained decoding is an output-generation constraint, NOT a
    replacement for :func:`validate_one_call_response`.

    Raises:
        ValueError: If the derived model shape no longer matches the
            assumptions above (fail-loud so callers can fall back).
    """
    schema = copy.deepcopy(OneCallReply.model_json_schema())
    defs = schema.pop("$defs", {}) or {}
    if not isinstance(defs, dict):
        raise ValueError("OneCallReply schema $defs is not an object")  # noqa: TRY004
    _inline_local_refs(schema, defs)
    _normalize_numeric_constraints(schema)
    _apply_onecall_enums(schema)
    _enforce_positive_price(schema)
    _strip_schema_descriptions(schema)
    return schema


def _inline_local_refs(node: Any, defs: dict[str, Any], _depth: int = 0) -> Any:
    """Inline local ``#/$defs/...`` references in place (recursive)."""
    if _depth > 10:
        raise ValueError("OneCallReply schema $ref nesting exceeds depth 10")
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            if not ref.startswith("#/$defs/"):
                raise ValueError(f"Unsupported non-local $ref in OneCallReply schema: {ref!r}")
            name = ref[len("#/$defs/"):]
            if name not in defs:
                raise ValueError(f"Unresolvable $ref in OneCallReply schema: {ref!r}")
            merged = copy.deepcopy(defs[name])
            if not isinstance(merged, dict):
                raise ValueError(f"$defs entry is not an object: {name!r}")  # noqa: TRY004
            for key, value in node.items():
                if key != "$ref":
                    merged[key] = value
            node.clear()
            node.update(merged)
        for value in node.values():
            _inline_local_refs(value, defs, _depth + 1)
    elif isinstance(node, list):
        for item in node:
            _inline_local_refs(item, defs, _depth + 1)
    return node


_NUMERIC_TRANSLATIONS = (
    ("ge", "minimum"),
    ("le", "maximum"),
    ("gt", "exclusiveMinimum"),
    ("lt", "exclusiveMaximum"),
)


def _normalize_numeric_constraints(node: Any) -> None:
    """Translate Pydantic internal bound keys to standard JSON Schema keys."""
    if isinstance(node, dict):
        for internal, standard in _NUMERIC_TRANSLATIONS:
            if internal in node:
                if standard not in node:
                    node[standard] = node[internal]
                del node[internal]
        for value in node.values():
            _normalize_numeric_constraints(value)
    elif isinstance(node, list):
        for item in node:
            _normalize_numeric_constraints(item)


def _signals_properties(schema: dict[str, Any]) -> dict[str, Any]:
    """Locate the inlined ``commerce_signals`` properties mapping."""
    try:
        signals = schema["properties"]["commerce_signals"]
        properties = signals["properties"]
    except (KeyError, TypeError) as exc:
        raise ValueError("OneCallReply schema missing commerce_signals.properties") from exc
    if not isinstance(properties, dict):
        raise ValueError("OneCallReply schema commerce_signals.properties is not an object")  # noqa: TRY004
    return properties


def _apply_onecall_enums(schema: dict[str, Any]) -> None:
    """Attach the executable intent enums missing from the derived schema."""
    properties = _signals_properties(schema)
    try:
        primary = properties["primary_intent"]
        negative = properties["negative_intent_tags"]
    except KeyError as exc:
        raise ValueError("OneCallReply schema missing intent signal fields") from exc
    if not isinstance(primary, dict) or not isinstance(negative, dict):
        raise ValueError("OneCallReply schema intent signal fields are not objects")  # noqa: TRY004
    primary["enum"] = sorted(INTENT_CATEGORIES)
    items = negative.get("items")
    if not isinstance(items, dict):
        raise ValueError("OneCallReply schema negative_intent_tags.items is not an object")  # noqa: TRY004
    items["type"] = "string"
    items["enum"] = sorted(_NEGATIVE_INTENTS)


def _enforce_positive_price(schema: dict[str, Any]) -> None:
    """Express the positive-finite ``requested_price`` rule as JSON Schema."""
    properties = _signals_properties(schema)
    price = properties.get("requested_price")
    if not isinstance(price, dict):
        raise ValueError("OneCallReply schema missing requested_price")  # noqa: TRY004
    branches = price.get("anyOf")
    if not isinstance(branches, list):
        raise ValueError("OneCallReply schema requested_price is not nullable-number shaped")  # noqa: TRY004
    number_branch = next(
        (b for b in branches if isinstance(b, dict) and b.get("type") == "number"),
        None,
    )
    if number_branch is None:
        raise ValueError("OneCallReply schema requested_price has no number branch")
    number_branch["exclusiveMinimum"] = 0


def _strip_schema_descriptions(node: Any) -> None:
    """Remove ``description`` annotations (payload size only)."""
    if isinstance(node, dict):
        node.pop("description", None)
        for value in node.values():
            _strip_schema_descriptions(value)
    elif isinstance(node, list):
        for item in node:
            _strip_schema_descriptions(item)


def strip_leading_speaker_prefix(
    reply: str,
    *,
    character_name: str | None = None,
    player_name: str | None = None,
    max_iterations: int = 3,
) -> tuple[str, bool]:
    """Deterministic leading speaker-label strip (Phase 91).

    Removes a duplicated transport/dialogue label that Qwen may copy from
    history formatting such as ``Sunny Skye: ...``.

    Contract:
    - anchored to beginning of response (after leading whitespace)
    - only when definitive ``NAME:`` / ``NAME-`` / ``NAME—`` pattern
    - character-specific (configured names) + canonical generic
      ``CHARACTER:`` / ``PLAYER:`` / ``SPEAKER:`` / ``LISTENER:``
    - bounded (max_iterations, default 3) for ``Sunny: Sunny: ...``
    - idempotent: second pass returns same string
    - does NOT touch ``Sunny, that sounds`` (comma) or mid-sentence ``I love Sunny``

    Returns (normalized_reply, did_strip).
    """
    if not reply or not reply.strip():
        return reply, False
    # Build ordered prefix list: full persona, first token, player full/first, generics
    prefixes: list[str] = []
    seen_lower: set[str] = set()

    def _add(name: str | None) -> None:
        if not name:
            return
        cand = str(name).strip()
        if not cand:
            return
        # full name
        low = cand.lower()
        if low not in seen_lower:
            prefixes.append(cand)
            seen_lower.add(low)
        # first token
        first = cand.split()[0].strip().rstrip(":,.-")
        if first and first.lower() not in seen_lower and first.lower() != low:
            prefixes.append(first)
            seen_lower.add(first.lower())

    _add(character_name)
    _add(player_name)
    # generic canonical (always)
    for gen in ("CHARACTER", "PLAYER", "SPEAKER", "LISTENER"):
        low = gen.lower()
        if low not in seen_lower:
            prefixes.append(gen)
            seen_lower.add(low)

    # Longest prefix first so ``Sunny Skye`` wins over ``Sunny``
    prefixes.sort(key=len, reverse=True)

    original = reply
    did_strip = False
    cur = reply
    for _ in range(max_iterations):
        stripped_this = False
        # Trim leading whitespace for anchored check but preserve remainder trimming
        cur_lstrip = cur.lstrip()
        if not cur_lstrip:
            break
        for pref in prefixes:
            # Allow optional markdown wrapping **, *, __ before name and trailing after colon
            pat = re.compile(rf"^(?:\*\*|\*|__)?\s*{re.escape(pref)}\s*[:\-\–—]\s*(?:\*\*|\*|__)?\s*", re.IGNORECASE)
            m = pat.match(cur_lstrip)
            if m:
                # Remove matched prefix, then strip any leftover leading whitespace for next iteration
                cur_lstrip = cur_lstrip[m.end():].lstrip()
                cur = cur_lstrip
                did_strip = True
                stripped_this = True
                break
        if not stripped_this:
            # put back original leading whitespace handling? reply should be stripped overall
            # If first iteration didn't strip, cur is still original stripped version; return trimmed original?
            # For idempotence, if no strip, return original (preserve original whitespace? but we trim)
            break
    # If we stripped, cur is already lstrip'd remainder; if not stripped, return original (but trimmed leading?)
    if did_strip:
        # Ensure we return stripped remainder (which is already stripped). If remainder empty, keep original semantics?
        # If stripping left empty, fallback to cur (which may be empty)
        return cur, True
    return original, False


def validate_one_call_response(
    raw_json: str | None,
    *,
    is_authorized_commerce: bool = False,
    authorized_price_minor: int | None = None,
    participants: Any | None = None,
    contract: Any | None = None,
    boundary_active: Any | None = None,
) -> OneCallResult:
    """Validate and parse one-call JSON response.

    Validates:
    1. JSON parse
    2. Pydantic schema (OneCallReply)
    3. CommerceSignals inner validation
    4. Deterministic safety flags
    5. Quality heuristics
    6. Deterministic Phase 7 boundary check (when ``boundary_active``
       carries active BoundaryType values or a BoundarySnapshot)

    Args:
        raw_json: Raw JSON string from qwen2.5
        is_authorized_commerce: If True, price mentions are allowed
        authorized_price_minor: Authorized price in cents (if applicable)
        boundary_active: Active boundary constraints for this turn
            (BoundarySnapshot or sequence of BoundaryType strings).
            None/empty means no constraint is active.

    Returns:
        OneCallResult with validated data and flags
    """
    # Layer 1: JSON parse
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("one_call: JSON parse failed: %s", exc)
        return OneCallResult(
            reply="",
            signals=CommerceSignals.low_information(),
            confidence=0.0,
            needs_handoff=True,
            advisory_handoff=False,
            is_valid=False,
            validation_error=f"JSON parse failed: {exc}",
        )

    # Defense-in-depth: strict:true does not prevent 0 under all samplers/q4
    # Business: missing vs zero vs positive are distinct; 0 -> None (missing)
    # Parse then mutate — never raw string replace.
    try:
        if isinstance(data, dict):
            _cs = data.get("commerce_signals")
            if isinstance(_cs, dict):
                _rp = _cs.get("requested_price")
                if _rp == 0 or (isinstance(_rp, float) and _rp == 0.0):
                    _cs["requested_price"] = None
                elif isinstance(_rp, str) and _rp.strip() in ("0", "0.0", "0.00"):
                    _cs["requested_price"] = None
    except Exception:
        pass

    # Layer 2: Pydantic schema validation
    try:
        parsed = OneCallReply.model_validate(data)
    except Exception as exc:
        logger.warning("one_call: Schema validation failed: %s", exc)
        return OneCallResult(
            reply="",
            signals=CommerceSignals.low_information(),
            confidence=0.0,
            needs_handoff=True,
            advisory_handoff=False,
            is_valid=False,
            validation_error=f"Schema validation failed: {exc}",
        )

    # Phase 91: Deterministic leading speaker-label normalization (no LLM, bounded, idempotent)
    # Must happen BEFORE safety/quality/grounding so leaked label does not pollute flags
    _char_for_strip = None
    _play_for_strip = None
    try:
        if participants is not None:
            _char_for_strip = getattr(participants, "character_name", None) or getattr(participants, "speaker_name", None)
            _play_for_strip = getattr(participants, "player_name", None) or getattr(participants, "listener_name", None)
        if not _char_for_strip and contract is not None:
            _char_for_strip = getattr(contract, "character_name", None) or getattr(contract, "speaker_name", None)
        if not _play_for_strip and contract is not None:
            _play_for_strip = getattr(contract, "player_name", None) or getattr(contract, "listener_name", None)
        # Fallback to defaults if still empty: Sunny Skye / Fan generic will still catch
        if not _char_for_strip:
            _char_for_strip = "Sunny Skye"
    except Exception:
        pass
    _normalized_reply = parsed.reply
    _did_strip = False
    try:
        _normalized_reply, _did_strip = strip_leading_speaker_prefix(
            parsed.reply, character_name=_char_for_strip, player_name=_play_for_strip
        )
    except Exception:
        _normalized_reply = parsed.reply
        _did_strip = False
    # Use normalized reply for all downstream validation
    _reply_for_validation = _normalized_reply if _did_strip and _normalized_reply is not None else parsed.reply
    # If stripping left empty string (e.g., reply was only "Sunny:"), keep empty for validation to flag
    if _did_strip and not _reply_for_validation.strip():
        # keep empty – will be caught as quality low / invalid downstream
        pass

    # Layer 3: Deterministic safety flags
    safety_flags = _compute_safety_flags(
        _reply_for_validation,
        is_authorized_commerce=is_authorized_commerce,
        authorized_price_minor=authorized_price_minor,
    )

    # Layer 3b: Deterministic Phase 7 boundary check (existing validation
    # + boundary validation, not a parallel pipeline). Examines the reply
    # TEXT against active constraints; the LLM never self-certifies.
    _boundary_violations: list[str] = []
    _boundary_action: str | None = None
    try:
        _has_boundary_input = False
        try:
            if boundary_active is not None:
                if hasattr(boundary_active, "active"):
                    _has_boundary_input = bool(getattr(boundary_active, "active", ()))
                else:
                    _has_boundary_input = bool(list(boundary_active))  # type: ignore[arg-type]
        except Exception:
            _has_boundary_input = False
        if _has_boundary_input:
            from commerce.boundary_validation import validate_reply_against_boundaries

            _bval = validate_reply_against_boundaries(
                _reply_for_validation, boundary_active
            )
            if _bval.violated:
                _boundary_violations = list(_bval.violations)
                _boundary_action = _bval.action
    except Exception:
        _boundary_violations = []
        _boundary_action = None

    # Layer 4: Quality heuristics (replaces LLM #3 scoring)
    quality_score, quality_flags = _compute_quality_heuristics(_reply_for_validation)

    # Layer 4b: Prompt-echo detection (deterministic, no LLM).
    # The model sometimes outputs schema/prompt text (e.g. the reply-field
    # descriptor from ONE_CALL_SYSTEM_PROMPT) as its reply. Quality
    # heuristics cannot see it (short, clean words), so an explicit check
    # forces review instead of letting it auto-send.
    try:
        if detect_prompt_echo(_reply_for_validation):
            if "prompt_echo" not in quality_flags:
                quality_flags.append("prompt_echo")
            quality_score = min(quality_score, 0.29)
    except Exception:
        pass

    # Phase 89R: Conversational grounding checks (deterministic, no LLM) — hardened
    # Also capture Phase 91 speaker-prefix leak as distinct flag for observability
    if _did_strip:
        if "speaker_prefix_leak" not in quality_flags:
            quality_flags.append("speaker_prefix_leak")
    try:
        conv_flags = _compute_conversational_flags(_reply_for_validation, participants=participants, contract=contract)
        if conv_flags:
            for cf in conv_flags:
                if cf not in quality_flags:
                    quality_flags.append(cf)
            # Penalties (advisory, not handoff by itself) — deterministic quality signal only
            if any(f in conv_flags for f in ("speaker_inversion", "character_as_player_inversion", "player_as_character_inversion")):
                quality_score = min(quality_score, 0.5)
            if "unanswered_question" in conv_flags:
                quality_score = min(quality_score, 0.6)
            if "topic_pivot" in conv_flags:
                quality_score = min(quality_score, 0.6)
            if "out_of_character" in conv_flags:
                quality_score = min(quality_score, 0.4)
            if any(f in conv_flags for f in ("unauthorized_player_speech", "player_agency_violation")):
                quality_score = min(quality_score, 0.5)
            if "speaker_prefix_leak" in conv_flags or "speaker_prefix_leak" in quality_flags:
                quality_score = min(quality_score, 0.5)
    except Exception:
        pass
    # If leak was stripped early, ensure penalty already applied
    if "speaker_prefix_leak" in quality_flags:
        quality_score = min(quality_score, 0.5)

    # Layer 5: Determine confidence and handoff
    confidence = parsed.confidence
    needs_handoff = parsed.needs_handoff

    # Override handoff if safety flags present
    if safety_flags:
        needs_handoff = True
        confidence = min(confidence, 0.3)

    # Phase 7: a boundary violation corroborates handoff deterministically
    # (the routing boundary_violation veto then prevents AUTO_SEND).
    if _boundary_violations:
        needs_handoff = True
        confidence = min(confidence, 0.3)

    # Prompt echo corroborates handoff deterministically (schema text
    # must never auto-send, even at high model confidence).
    if "prompt_echo" in quality_flags:
        needs_handoff = True
        confidence = min(confidence, 0.3)

    # Override handoff if quality is very poor
    if quality_score < 0.3:
        needs_handoff = True

    return OneCallResult(
        reply=_reply_for_validation,
        signals=parsed.commerce_signals,
        confidence=confidence,
        needs_handoff=needs_handoff,
        advisory_handoff=bool(parsed.needs_handoff),
        quality_score=quality_score,
        quality_flags=quality_flags,
        safety_flags=safety_flags,
        boundary_violations=_boundary_violations,
        boundary_action=_boundary_action,
        is_valid=True,
        validation_error=None,
    )


def detect_prompt_echo(reply: str) -> bool:
    """True when the reply echoes prompt/schema text instead of answering.

    The model occasionally outputs field descriptors from
    ``ONE_CALL_SYSTEM_PROMPT`` (observed: "Your conversational response
    to the fan" sent verbatim). Quality heuristics score such strings
    as fine, so this explicit check exists. Pure, bounded, never
    raises; non-string/empty input yields False.
    """
    try:
        if not isinstance(reply, str) or not reply.strip():
            return False
        norm = " ".join(reply.strip().lower().split())
        for phrase in _PROMPT_ECHO_PHRASES:
            if norm == phrase or norm.rstrip(".!,;:") == phrase:
                return True
        return False
    except Exception:
        return False


_PROMPT_ECHO_PHRASES = (
    "your conversational response to the fan",
    "<<reply>>",
)


def _compute_safety_flags(
    text: str,
    *,
    is_authorized_commerce: bool = False,
    authorized_price_minor: int | None = None,
) -> list[str]:
    """Compute deterministic safety flags from reply text.

    Replicates the safety-critical keyword detection from core/scoring.py
    without LLM involvement. These are the HARD_FLAGS that require operator
    review.
    """
    from core.scoring import FLAG_KEYWORDS, HARD_FLAGS

    flags: list[str] = []
    text_lower = text.lower()

    for flag, keywords in FLAG_KEYWORDS.items():
        if flag not in HARD_FLAGS:
            continue

        # Skip price mentions for authorized commerce
        if flag == "price_mention" and is_authorized_commerce:
            if authorized_price_minor is not None:
                import re
                _price_re = re.compile(r"\$?\s*(\d+(?:\.\d{1,2})?)")
                _authorized_dollars = authorized_price_minor / 100.0
                _found_prices: list[float] = []
                for m in _price_re.finditer(text):
                    try:
                        _found_prices.append(float(m.group(1)))
                    except Exception:
                        continue
                if _found_prices:
                    _matches = any(abs(p - _authorized_dollars) <= 0.005 for p in _found_prices)
                    if _matches:
                        continue
                else:
                    if f"${_authorized_dollars:.2f}" in text or f"${int(_authorized_dollars)}" in text:
                        continue
                    if "$" not in text and "price" not in text_lower:
                        continue
                    continue
            else:
                continue

        if any(k in text_lower for k in keywords):
            flags.append(flag)

    return flags


def _compute_quality_heuristics(reply: str) -> tuple[float, list[str]]:
    """Compute deterministic quality score and flags.

    Replaces LLM #3 (score_draft) with deterministic heuristics:
    - Length check (appropriate_length)
    - Formality check (too_formal)
    - Generic patterns (too_generic)
    - Repetition check (repetitive)
    - Awkward phrasing detection (awkward_phrasing)
    """
    flags: list[str] = []
    scores: dict[str, float] = {}

    # 1. Length check (0-10) — human-shaped: brief-but-clean replies are
    # legitimate (affiliative continuers, reactions). Only degenerate
    # (0-1 words) is flagged generic; 2-word replies get an informational
    # short_reply flag for queue triage; 3-4 word clean replies may send.
    # (Mirrors core/scoring_deterministic.py — keep the two in sync.)
    word_count = len(reply.split())
    if 10 <= word_count <= 100:
        scores["appropriate_length"] = 10.0
    elif 5 <= word_count < 10 or 100 < word_count <= 150:
        scores["appropriate_length"] = 7.0
    elif 3 <= word_count < 5:
        scores["appropriate_length"] = 8.0
    elif 150 < word_count <= 200:
        scores["appropriate_length"] = 5.0
    elif word_count == 2:
        scores["appropriate_length"] = 5.0
        flags.append("short_reply")
    else:
        scores["appropriate_length"] = 1.0
        flags.append("too_generic")

    # 2. Formality check (0-10)
    formal_indicators = [
        "dear", "sincerely", "regards", "respectfully",
        "therefore", "furthermore", "consequently", "moreover",
        "in conclusion", "as per", "pursuant to",
    ]
    formal_count = sum(1 for f in formal_indicators if f in reply.lower())
    if formal_count == 0:
        scores["natural_tone"] = 9.0
    elif formal_count <= 2:
        scores["natural_tone"] = 6.0
        flags.append("too_formal")
    else:
        scores["natural_tone"] = 3.0
        flags.append("too_formal")

    # 3. Template openers (0-10) — opener-position only, single-sourced
    # with core/scoring_deterministic.py (validator and prompt never drift).
    try:
        from core.scoring_deterministic import TEMPLATE_OPENER_PHRASES as _TEMPLATE_OPS
    except Exception:
        _TEMPLATE_OPS = ("i understand", "thank you for", "how can i help")
    _reply_open = reply.lower().strip()
    generic_count = sum(1 for g in _TEMPLATE_OPS if _reply_open.startswith(g))
    if generic_count == 0:
        scores["contextually_aware"] = 9.0
    elif generic_count <= 1:
        scores["contextually_aware"] = 6.0
        flags.append("too_generic")
    else:
        scores["contextually_aware"] = 3.0
        flags.append("too_generic")

    # 4. Repetition check (0-10)
    words = reply.lower().split()
    if len(words) > 5:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio >= 0.7:
            scores["not_repetitive"] = 10.0
        elif unique_ratio >= 0.5:
            scores["not_repetitive"] = 6.0
            flags.append("repetitive")
        else:
            scores["not_repetitive"] = 3.0
            flags.append("repetitive")
    else:
        scores["not_repetitive"] = 8.0

    # Composite score (0.0 to 1.0)
    composite = sum(scores.values()) / (len(scores) * 10) if scores else 0.5

    # Override: degenerate replies (0-1 words) capped at 0.5
    if word_count <= 1:
        composite = min(composite, 0.5)
        if "too_generic" not in flags:
            flags.append("too_generic")

    # Markup echo (internal prompt scaffolding in the draft) — mirror of
    # core/scoring_deterministic.py, which is the final authority on the
    # canonical path. Flag + soft cap so the turn queues with a reason.
    try:
        from core.text_sanitize import contains_markup as _has_markup_q

        _has_internal_id_q = bool(re.search(r"\bid:\d+", reply))
        if _has_markup_q(reply) or _has_internal_id_q:
            if "markup_echo" not in flags:
                flags.append("markup_echo")
            composite = min(composite, 0.5)
    except Exception:
        pass

    return composite, flags


def _compute_conversational_flags(
    reply: str,
    participants: Any | None = None,
    contract: Any | None = None,
) -> list[str]:
    """Deterministic roleplay validation — Phase 89R.

    Covers:
      - character-as-player inversion (Hey Sunny! when Sunny is character)
      - player-as-character inversion (e.g., reply claims to be the player)
      - unauthorized player speech (e.g., invented player dialogue / You walked over...)
      - character identity preservation (I am Sunny is OK)
      - question answering (fan question -> character must answer)
      - topic continuity (maintain_topic)
      - out-of-character / assistant meta drift
      - player agency preservation

    Heuristic, no LLM, deterministic, fail-open. Adds quality_flags, slight quality penalty,
    never auto-regenerates, never second LLM call.
    """
    flags: list[str] = []
    if not reply or not reply.strip():
        return flags
    low = reply.lower().strip()
    # Extract speaker/character and listener/player names (support both naming)
    speaker = ""
    listener = ""
    character_name = ""
    player_name = ""
    try:
        if participants is not None:
            speaker = str(getattr(participants, "speaker_name", "") or getattr(participants, "character_name", "") or "").strip()
            listener = str(getattr(participants, "listener_name", "") or getattr(participants, "player_name", "") or "").strip()
            character_name = str(getattr(participants, "character_name", "") or speaker or "").strip()
            player_name = str(getattr(participants, "player_name", "") or listener or "").strip()
            # also fallback to speaker/listener if character/player empty
            if not character_name:
                character_name = speaker
            if not player_name:
                player_name = listener
    except Exception:
        pass
    speaker_first = speaker.split()[0].lower() if speaker else ""
    listener_first = listener.split()[0].lower() if listener else ""
    char_first = character_name.split()[0].lower() if character_name else speaker_first
    play_first = player_name.split()[0].lower() if player_name else listener_first
    # Use canonical char/play for logic
    import re as _re

    # 0. Speaker prefix leak — leading CHARACTER:/PLAYER: label at beginning (Phase 91)
    # Detect even before normalization for direct _compute_conversational_flags calls
    try:
        _norm, _did = strip_leading_speaker_prefix(
            reply, character_name=character_name or speaker, player_name=player_name or listener
        )
        if _did:
            if "speaker_prefix_leak" not in flags:
                flags.append("speaker_prefix_leak")
            # Use normalized for remaining checks to avoid double-penalizing speaker_inversion on same prefix
            # but keep original reply variable for inversion checks that require full text? Use normalized low
            # For simplicity, update low to stripped version for downstream heuristics
            low = _norm.lower().strip()
    except Exception:
        pass

    # 1. Speaker/Character-as-player inversion — character name used as if listener
    # Example: Sunny is character, reply "Hey there sunny!" is inversion (should address player)
    if char_first:
        pat = _re.compile(rf"^\s*(hey|hi|hello|hola|hey there|hi there)\s+{_re.escape(char_first)}\b", _re.I)
        if pat.search(reply):
            flags.append("speaker_inversion")
            if "character_as_player_inversion" not in flags:
                flags.append("character_as_player_inversion")
        else:
            if play_first and char_first != play_first:
                if char_first in low and ("hey" in low or "hi " in low or "hello" in low):
                    # exclude legitimate self-identification "I'm Sunny"
                    if "i'm " + char_first not in low and "i am " + char_first not in low and "just your favorite" not in low:
                        if _re.search(rf"\b(hey|hi|hello)\b[^.]*\b{_re.escape(char_first)}\b", low):
                            if "speaker_inversion" not in flags:
                                flags.append("speaker_inversion")
                            if "character_as_player_inversion" not in flags:
                                flags.append("character_as_player_inversion")

    # 2. Player-as-character inversion — reply claims to be player
    # Example: Fan is player, reply "I am <player>, the creator..." is inversion
    if play_first and char_first:
        # detect "I am <player>" or "I'm <player>" when player is Fan
        # Should NOT flag "I am Sunny" when Sunny is character (character first-person is valid)
        # Only flag when reply self-identifies as player name, not character
        patterns_player_claim = [
            rf"\bi\s+am\s+{_re.escape(play_first)}\b",
            rf"\bi['’]m\s+{_re.escape(play_first)}\b",
            rf"\bmy\s+name\s+is\s+{_re.escape(play_first)}\b",
        ]
        for pat_str in patterns_player_claim:
            try:
                if _re.search(pat_str, low):
                    # But ensure not also claiming character simultaneously? If both names same substring, avoid
                    # If player_first != char_first, this is clear inversion
                    if play_first != char_first:
                        flags.append("player_as_character_inversion")
                        if "speaker_inversion" not in flags:
                            # also consider it speaker confusion, but keep distinct
                            pass
                        break
            except Exception:
                pass

    # 3. Unauthorized player speech — model invents player dialogue/actions
    # Flag obvious cases: "<player> smiled and said, ..." or "You walked over and hugged Sunny."
    # Do NOT flag normal second-person dialogue like "Do you want to tell me more?"
    try:
        # Player name + speech/action verb
        if play_first:
            # pattern: <player> said / smiled / laughed / walked / hugged etc
            player_speech_verbs = ["said", "smiled", "laughed", "walked", "hugged", "kissed", "ran", "came", "went", "looked", "whispered", "shouted", "asked", "replied", "answered"]
            for verb in player_speech_verbs:
                pat = _re.compile(rf"\b{_re.escape(play_first)}\s+{verb}\b", _re.I)
                if pat.search(reply):
                    if "unauthorized_player_speech" not in flags:
                        flags.append("unauthorized_player_speech")
                    if "player_agency_violation" not in flags:
                        flags.append("player_agency_violation")
                    break
            # possessive narration like "<player>'s smile" maybe?
            if "unauthorized_player_speech" not in flags:
                pat2 = _re.compile(rf"\b{_re.escape(play_first)}\s+(smiled|laughed|said)", _re.I)
                if pat2.search(reply):
                    flags.append("unauthorized_player_speech")
        # Second-person narration: "You walked over", "You hugged Sunny", "You said ..."
        # Distinguish from normal questions like "Do you want..." -> contains auxiliary before you
        # Narration pattern: sentence starts with "You <past_tense_verb>"
        # Phase 90 tiny guard: "You said you..." is legitimate echo, not invented narration
        narration_verbs = ["walked", "hugged", "smiled", "laughed", "said", "ran", "came", "went", "kissed", "hugged", "jumped", "sat", "stood", "looked"]
        # Check for "You walked/hugged..." at sentence boundary
        for verb in narration_verbs:
            pat_you = _re.compile(rf"(^|[.!?]\s+)you\s+{verb}\b", _re.I)
            if pat_you.search(reply):
                # Tiny guard: "You said you like..." is echo, not narration — don't flag
                if verb == "said" and "you said you" in low:
                    continue
                # Also guard "You said, ..." quoting? Still echo if next word is you
                if "unauthorized_player_speech" not in flags:
                    flags.append("unauthorized_player_speech")
                if "player_agency_violation" not in flags:
                    flags.append("player_agency_violation")
                break
        # Also direct "You walked over and hugged" full phrase — keep, but echo guard already
        if "you walked over" in low or "you hugged" in low or "you smiled and said" in low:
            if "unauthorized_player_speech" not in flags:
                flags.append("unauthorized_player_speech")
            if "player_agency_violation" not in flags:
                flags.append("player_agency_violation")
    except Exception:
        pass

    # 4. Character identity preservation — Sunny can say "I am Sunny" naturally, do NOT flag
    # Ensure we do NOT penalize legitimate first-person character speech
    # Our speaker_inversion already excludes "i'm sunny", so this is satisfied.
    # Add explicit check: if reply contains "i am sunny" or "i'm sunny", ensure not flagged as inversion beyond the exclusion
    # Already handled.

    # 5. Question answered — if answer_required true and question_target character
    try:
        if contract is not None:
            answer_required = bool(getattr(contract, "answer_required", False))
            q_target = getattr(contract, "question_target", "none")
            # Handle dual equality: contract may store "character" canonical that equals "speaker"
            # Check both strings
            is_char_target = False
            try:
                # _QuestionTargetStr equals both, but plain string check needs both
                qt_str = str(q_target).lower() if q_target is not None else "none"
                if qt_str in ("speaker", "character"):
                    is_char_target = True
                elif q_target == "speaker" or q_target == "character":
                    is_char_target = True
            except Exception:
                is_char_target = (q_target == "speaker" or q_target == "character")
            if answer_required and is_char_target:
                generic_defs = [
                    "sounds like a fun way to stay fit",
                    "how about we chat about your workouts",
                    "maybe i can share some tips",
                    "just hear about what you're up to",
                    "sounds like a fun way",
                    "sounds like a fun way to stay",
                ]
                for gd in generic_defs:
                    if gd in low:
                        if "unanswered_question" not in flags:
                            flags.append("unanswered_question")
                        break
                # Additional heuristic: very short generic reply (<12 words) that doesn't address question is likely unanswered
                # But avoid false positive for concise valid answers like "My favorite workout is dancing"
                # So only flag if reply is short AND contains generic acknowledgement
                wc = len(reply.split())
                if wc < 8 and "unanswered_question" not in flags:
                    # If reply is short and contains no question-relevant content (like workout/dance etc when topic workout)
                    # Check contract current_topic
                    try:
                        cur_topic = getattr(contract, "current_topic", None)
                        if cur_topic:
                            topic_low = str(cur_topic).lower()
                            if topic_low not in low:
                                # short and off-topic -> likely not answered
                                # But don't flag greeting like "hi"
                                if wc > 3:
                                    flags.append("unanswered_question")
                    except Exception:
                        pass
    except Exception:
        pass

    # 6. Topic continuity — if maintain_topic true, detect obvious unnecessary pivot
    try:
        if contract is not None:
            maintain = bool(getattr(contract, "maintain_topic", False))
            cur_topic = getattr(contract, "current_topic", None)
            if maintain and cur_topic:
                topic_low = str(cur_topic).lower()
                # Build relevant term set per topic; for workout, expect workout terms
                # Generic: if topic is not location, pivot to location is suspicious
                pivot_terms = ["where are you from", "where you from", "what's your favorite", "whats your favorite", "do you have any cool stories", "where do you live", "what do you do for work"]
                # Check if reply contains workout-related term when topic workout
                if topic_low == "workout" or "work" in topic_low:
                    workout_terms = ["workout", "exercise", "fitness", "dance", "salsa", "gym", "fit", "training", "work out", "dancing"]
                    has_topic = any(t in low for t in workout_terms)
                    if not has_topic:
                        if any(p in low for p in pivot_terms):
                            if "topic_pivot" not in flags:
                                flags.append("topic_pivot")
                else:
                    # Generic check: if maintain and topic exists but reply pivots to location while topic not location
                    if topic_low not in low:
                        # Only flag if pivot term present and topic not in reply at all
                        # But avoid over-penalizing natural transitions: check if reply is very short pivot
                        if any(p in low for p in pivot_terms):
                            # Ensure pivot is not already part of topic (e.g., topic is location)
                            if "where" not in topic_low:
                                if "topic_pivot" not in flags:
                                    flags.append("topic_pivot")
                    # Even if no pivot phrase but topic completely absent and reply asks unrelated question, could be pivot
                    # Keep lenient: only flag when we see obvious non sequitur
    except Exception:
        pass

    # 7. Out-of-character / assistant meta drift — tiny Phase 90 hardening
    try:
        ooc_patterns = [
            "as an ai",
            "i am an ai assistant",
            "i am an ai",
            "i can't roleplay",
            "i cannot roleplay",
            "as your assistant",
            "how can i assist you today",
            "i am a language model",
            "as a language model",
            "i don't have personal experiences",
        ]
        for pat in ooc_patterns:
            if pat in low:
                # Tiny guard: if AI claim is explicitly negated (I am not an AI) and character asserts identity, treat as valid meta-answer, not pure OOC
                # Keeps deterministic, no LLM, avoids obvious false positive when player asks "Are you an AI?" 
                # Spec §11 says if cannot safely distinguish without brittle complexity, leave unchanged — this is minimal.
                if pat in ("i am an ai", "i am an ai assistant", "i am a language model") and "not an ai" in low:
                    continue
                if pat == "as an ai" and "not an ai" in low:
                    continue
                if pat == "as your assistant" and "not" in low and char_first and char_first in low:
                    # e.g., "I am Sunny, not your assistant" — not pure OOC
                    continue
                if "out_of_character" not in flags:
                    flags.append("out_of_character")
                break
    except Exception:
        pass

    # 8. Player agency preserved flag is inverse of unauthorized_player_speech
    # No need to add flag for preserved; telemetry will derive.

    return flags


# One-call system prompt for qwen2.5
ONE_CALL_SYSTEM_PROMPT = """You are Sunny, chatting directly with one fan. You are always Sunny; the fan is never Sunny and never shares your name. Inbound messages are the fan speaking. Outbound "reply" is you, Sunny, speaking. You MUST respond with a JSON object containing exactly these fields:
- "reply": string (your message text; never a placeholder, never copied from these instructions)
- "commerce_signals": object with purchase_intent, content_interest, relationship_engagement, price_interest (0.0-1.0 each), explicit_purchase_request, explicit_content_request (true/false), requested_price (null or positive number), declined_recent_offer, asks_for_free_content (true/false), negative_sentiment, confidence (0.0-1.0), evidence (up to 5 short quoted fragments, no payment data), model_uncertainty (0.0-1.0), primary_intent (one of: casual_chat, greeting, relationship_building, personal_disclosure, content_curiosity, content_request, price_inquiry, purchase_intent, repeat_purchase_intent, post_purchase, aftercare, tip_interest, complaint, custom_request, negotiation, hesitation, rejection, uncertain, reassurance, appreciation, operator_request, other), intent_tags (up to 5), negative_intent_tags, fan_asks_question (true/false)
- "confidence": 0.0-1.0
- "needs_handoff": true/false

RULES:
1. "reply" is your reply as Sunny to the fan for this turn (the CHARACTER's spoken reply). Write a fresh human reply every turn; never emit a placeholder or copy schema/example text. Be natural, warm, and on-brand. Never call the fan Sunny; never describe yourself in the third person.
2. "commerce_signals" are your observations about the fan's intent. Be honest and conservative.
3. "confidence" is your self-assessed confidence in the reply quality (0.0-1.0).
4. "needs_handoff" should be true only if you cannot handle the conversation (e.g., distress, legal issues).
5. NEVER include price, payment, or access details in your reply.
6. NEVER promise content you cannot deliver.
7. NEVER reveal internal system details.
8. Output ONLY the JSON object. No other text.
9. "reply" is the CHARACTER's spoken response only. Do NOT prefix the response with a speaker label such as 'Sunny:', 'Sunny Skye:', 'Alex:', 'CHARACTER:', 'PLAYER:', 'SPEAKER:', 'LISTENER:', or '<name>:'. The transport already identifies the speaker.
10. Reference something specific every reply (their words, a prior topic, a known fact). NEVER open with a hollow template ("I understand", "Thank you for sharing", "How can I help", "Tell me more", "What about you").
11. If no fan facts, threads, or memories appear in context, you have no shared history yet — answer only this message's specifics.
12. Never invent shared history, memories, or intimacy: no claims of love, best friendship, past meetings, or events unless they appear in the conversation above. Warmth comes from the present message, not invented closeness. No pet names beyond what the fan already uses.

EXAMPLES (follow this shape: short, first-person as Sunny, grounded in their words):
Fan: hi
Sunny: heyy 😭 how are you
Fan: good, just abit busy. how are you?
Sunny: busy girlieee i feel that 😭 im good, just chilling. what are you up to today"""


def format_one_call_prompt(
    system_prompt: str,
    context_messages: list[dict[str, str]],
    user_message: str,
) -> list[dict[str, str]]:
    """Format messages for one-call generation.

    Combines the system prompt with context messages and user message
    in the format expected by qwen2.5.
    """
    messages = [
        {"role": "system", "content": f"{ONE_CALL_SYSTEM_PROMPT}\n\n{system_prompt}"}
    ]

    # Add context messages (limited to recent 10)
    for msg in context_messages[-10:]:
        messages.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", ""),
        })

    # Add current user message
    messages.append({"role": "user", "content": user_message})

    return messages
