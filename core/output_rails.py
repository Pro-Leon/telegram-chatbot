"""Phase 1.1: output-rails verdicts (pure, no I/O).

Single deterministic choke-point helper condensing the five stop-the-bleed
verdicts: prompt echo, fan-word leak, speaker-prefix leak, cross-turn repeat,
and markup echo. Returns ``(verdict, flags, cap)`` where ``verdict`` is
``"clean"`` or ``"review"``, ``flags`` is a subset of the closed vocabulary,
and ``cap`` is a score ceiling (``None`` when clean).

Contract:
- Pure function: no PG/Redis/LLM/pipeline/worker imports. Callers supply the
  last-3 DB outbound rows and an optional embedding hook; this module only
  computes. Never raises: non-string/empty input yields clean, unexpected
  errors yield clean (fail-open; wiring in 1.2/1.3 decides fail-closed).
- Reuses existing sources instead of forking them: ``detect_prompt_echo``
  (:func:`core.one_call.detect_prompt_echo`), the speaker pattern + generic
  names from ``strip_leading_speaker_prefix``, and
  :func:`core.text_sanitize.contains_markup`.
- Wiring lives in Phase 1.2/1.3. This module must not be called anywhere yet.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from core.one_call import _PROMPT_ECHO_PHRASES, detect_prompt_echo
from core.text_sanitize import contains_markup

VERDICT_CLEAN = "clean"
VERDICT_REVIEW = "review"

FLAGS = ("prompt_echo", "fan_word", "speaker_prefix", "repeat", "markup_echo")

CAP_ECHO = 0.29
CAP_OTHER = 0.5


class RailsRefusal(ValueError):
    """Refusal raised by send-path chokes when rails verdict is review.

    Subclasses ValueError so existing ``except Exception`` send paths keep
    working; callers that need hold/override semantics catch this type
    explicitly. Carries the rails ``flags`` for operator-visible errors.
    """

    def __init__(self, flags: list[str]):
        self.flags = list(flags)
        super().__init__(f"output-rails review: {','.join(self.flags)}")


_FUZZY_WRATIO_CUTOFF = 90
_FUZZY_RATIO_CUTOFF = 80
_REPEAT_COSINE_CUTOFF = 0.85
_MAX_OUTBOUND = 3

# Generic speaker labels mirrored from strip_leading_speaker_prefix.
_GENERIC_SPEAKERS = ("CHARACTER", "PLAYER", "SPEAKER", "LISTENER")

# Fan-word leak = direct address of the player as "fan" (internal vocabulary
# per the reliability program). Third-party narrative ("A fan once asked…")
# is not a leak, so bare ``\\bfan\\b`` is insufficient: these patterns target
# vocative / you-directed uses only. fan_name never exempts.
_FAN_LEAK_RES = (
    re.compile(
        r"^\s*(?:hey|hi|hello|hola|hey there|hi there)\b[^\n.!?]{0,30}\bfan\b", re.IGNORECASE
    ),
    re.compile(r"\b(?:thanks|thank you|my)\b[^\n.!?]{0,25}\bfan\b", re.IGNORECASE),
    re.compile(r"\byou\b[^\n.!?]{0,30}\bfan\b", re.IGNORECASE),
    re.compile(r"\bfan\b\s*[,!]", re.IGNORECASE),
)

_INTERNAL_ID_RE = re.compile(r"\bid:\d+")


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())


def _names(character_name: str | None, player_name: str | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def _add(name: str | None) -> None:
        if not name or not str(name).strip():
            return
        cand = str(name).strip()
        if cand.lower() not in seen:
            out.append(cand)
            seen.add(cand.lower())
        first = cand.split()[0].strip().rstrip(":,.-")
        if first and first.lower() not in seen:
            out.append(first)
            seen.add(first.lower())

    _add(character_name)
    _add(player_name)
    for gen in _GENERIC_SPEAKERS:
        if gen.lower() not in seen:
            out.append(gen)
            seen.add(gen.lower())
    out.sort(key=len, reverse=True)
    return out


def _has_fuzzy_echo(norm: str) -> bool:
    """Exact (reused) OR substring containment OR RapidFuzz fuzzy match.

    WRatio inflates on short strings (measured 85.5 on clean fixtures), so
    it runs at cutoff 90; plain ratio runs at 80. Either plus containment
    catches verbatim, punctuated, embedded, and near-paraphrase echo.
    """
    try:
        if detect_prompt_echo(norm):
            return True
    except Exception:
        pass
    try:
        for phrase in _PROMPT_ECHO_PHRASES:
            if phrase in norm or norm in phrase:
                return True
    except Exception:
        pass
    try:
        from rapidfuzz import fuzz as _fuzz

        for phrase in _PROMPT_ECHO_PHRASES:
            if _fuzz.WRatio(norm, phrase) >= _FUZZY_WRATIO_CUTOFF:
                return True
            if _fuzz.ratio(norm, phrase) >= _FUZZY_RATIO_CUTOFF:
                return True
    except Exception:
        pass
    return False


def _has_fan_word(reply: str) -> bool:
    try:
        return any(pat.search(reply) for pat in _FAN_LEAK_RES)
    except Exception:
        return False


def _has_speaker_prefix(reply: str, names: list[str]) -> bool:
    """Speaker label leading-anchored OR after a sentence boundary."""
    try:
        for name in names:
            esc = re.escape(name)
            md = r"(?:\*\*|\*|__)?\s*"
            if re.search(rf"^\s*{md}{esc}\s*[:\-\–—]\s*", reply, re.IGNORECASE):
                return True
            if re.search(rf"[.!?]\s+{md}{esc}\s*[:\-\–—]\s*", reply, re.IGNORECASE):
                return True
        return False
    except Exception:
        return False


def _cosine_sim(a: list[float], b: list[float]) -> float | None:
    """Cosine similarity mirroring db.postgres._cosine_distance (1 - distance)."""
    try:
        if not a or not b or len(a) != len(b):
            return None
        dot = sum(x * y for x, y in zip(a, b))
        import math

        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return None
        return dot / (na * nb)
    except Exception:
        return None


def _has_repeat(
    reply: str,
    recent_outbound: Any,
    embed: Callable[[list[str]], list[list[float]] | None] | None,
) -> bool:
    try:
        prior = [p for p in list(recent_outbound or []) if isinstance(p, str) and p.strip()]
        prior = prior[-_MAX_OUTBOUND:]
        if not prior:
            return False
        norm = _normalize(reply)
        if not norm:
            return False
        for prev in prior:
            if norm == _normalize(prev):
                return True
        if embed is None:
            return False
        try:
            vecs = embed([reply] + prior)
        except Exception:
            return False
        if not vecs or len(vecs) != len(prior) + 1:
            return False
        for vec in vecs[1:]:
            sim = _cosine_sim(vecs[0], vec)
            if sim is not None and sim > _REPEAT_COSINE_CUTOFF:
                return True
        return False
    except Exception:
        return False


def _has_markup(reply: str) -> bool:
    try:
        if contains_markup(reply):
            return True
        return bool(_INTERNAL_ID_RE.search(reply))
    except Exception:
        return False


def check(
    reply: Any,
    *,
    fan_name: str | None = None,
    character_name: str | None = None,
    player_name: str | None = None,
    recent_outbound: Any = (),
    embed: Callable[[list[str]], list[list[float]] | None] | None = None,
) -> tuple[str, list[str], float | None]:
    """Run output-rails verdicts over a draft reply.

    Args:
        reply: Draft text under review.
        fan_name: Accepted for call-site symmetry; never exempts (unused).
        character_name / player_name: Speaker identities for prefix detection.
        recent_outbound: Last-3 DB outbound rows supplied by the caller.
        embed: Optional ``(texts) -> vectors`` hook for cosine repeat;
            ``None`` (or any failure) means lexical-only (fail-open).

    Returns:
        ``(verdict, flags, cap)`` with ``verdict`` ``"clean"``/``"review"``,
        ``flags`` a subset of ``prompt_echo, fan_word, speaker_prefix,
        repeat, markup_echo``, and ``cap`` ``0.29`` (echo) / ``0.5``
        (others) / ``None`` (clean). Never raises.
    """
    try:
        if not isinstance(reply, str) or not reply.strip():
            return VERDICT_CLEAN, [], None
        flags: list[str] = []
        cap: float | None = None

        def _hit(flag: str, ceiling: float) -> None:
            nonlocal cap
            if flag not in flags:
                flags.append(flag)
            cap = ceiling if cap is None else min(cap, ceiling)

        if _has_fuzzy_echo(" ".join(reply.strip().lower().split())):
            _hit("prompt_echo", CAP_ECHO)
        if _has_fan_word(reply):
            _hit("fan_word", CAP_OTHER)
        if _has_speaker_prefix(reply, _names(character_name, player_name)):
            _hit("speaker_prefix", CAP_OTHER)
        if _has_repeat(reply, recent_outbound, embed):
            _hit("repeat", CAP_OTHER)
        if _has_markup(reply):
            _hit("markup_echo", CAP_OTHER)
        if not flags:
            return VERDICT_CLEAN, [], None
        return VERDICT_REVIEW, flags, cap
    except Exception:
        return VERDICT_CLEAN, [], None
