"""Complaint-triage evidence (H1, deterministic, turn-local).

Connects the current inbound turn to the H1 complaint-triage routing:
``technical_payment_signal`` vs ``payment_claim`` vs
``chat_experience_complaint`` (see
``docs/HUMAN_HANDOFF_RESEARCH_AND_PROPOSALS.md`` Part 2 item 1).

CORE PRINCIPLE (enforced here, mirrors Phase 7/8):

* This module extracts evidence from the current turn text only.
* The pipeline/relationship layers decide handoff; the strategy layer
  decides the conversational move. This module decides nothing.
* The LLM never creates triage evidence (no LLM inputs at all).
* Historical relationship / commerce / desire state never creates
  evidence here. Only the current message text can set a flag.
* Raw message text is never persisted here. Only bounded booleans flow
  out (one object per turn).

Lexical discipline (mirrors the Phase 6/7 philosophy):

* Every pattern uses word boundaries (``\\b``); substring matches are
  impossible by construction.
* A positive flag requires an explicit construction (framed payment
  failure, demand/accusation wording, or a named experience complaint),
  never a bare keyword such as "free", "charge", "trust", or "legit".
* Explicit false-positive guards: third-party attribution
  (``he said "..."``), ``don't forget ...`` / ``don't mean ...``
  clauses, "someone in charge" (no payment framing), authorized
  free-tier phrasing (demand framing required), reassurance
  ("is this legit", "can I trust you" — never a scam accusation), and
  bare quality remarks ("blurry" without payment framing).

Imports are ``re`` / ``dataclasses`` / ``typing`` only: no DB, no
Redis, no network, no LLM, no clock, no persistence, no global mutable
state. Importing this module has no side effects.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Evidence object (turn-scoped, bounded booleans only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComplaintTriageEvidence:
    """One turn of complaint-triage evidence (immutable, advisory only)."""

    technical_payment_signal: bool = False
    payment_claim: bool = False
    chat_experience_complaint: bool = False

    def has_payment_signal(self) -> bool:
        """True for payment-technical or payment-claim wording."""
        try:
            return bool(self.technical_payment_signal or self.payment_claim)
        except Exception:
            return False

    def is_experience_only(self) -> bool:
        """True only for positively-identified experience complaints.

        Requires an explicit experience-complaint construction AND no
        payment signal of either kind. Unknown text (all False) is NOT
        experience-only: callers must stay fail-closed (legacy handoff).
        """
        try:
            return bool(
                self.chat_experience_complaint
                and not self.technical_payment_signal
                and not self.payment_claim
            )
        except Exception:
            return False

    def triage_label(self) -> str | None:
        """Queue-payload label, precedence technical > claim > experience."""
        try:
            if self.technical_payment_signal:
                return "payment_technical"
            if self.payment_claim:
                return "payment_claim"
            if self.chat_experience_complaint:
                return "experience"
            return None
        except Exception:
            return None

    def is_empty(self) -> bool:
        try:
            return not (
                self.technical_payment_signal
                or self.payment_claim
                or self.chat_experience_complaint
            )
        except Exception:
            return True


_NEUTRAL_EVIDENCE = ComplaintTriageEvidence()


# ---------------------------------------------------------------------------
# Lexical rule families (explicit constructions only; \\b everywhere)
# ---------------------------------------------------------------------------
# FP = known false-positive shape handled by the rule or a guard.

#: Clause splitter (conservative, mirrors boundary_evidence).
_CLAUSE_SPLIT_RE = re.compile(r"[.!?;\n]+")

#: Third-party attribution ("he said ...", "my friend told me ...").
_ATTRIBUTION_RE = re.compile(
    r"\b(he|she|they|my (?:friend|mom|dad|sister|brother|ex|boss|mother|father))\b"
    r"[^.!?;\n]{0,40}\b(said|told|says|telling)\b",
    re.IGNORECASE,
)

#: Double-quoted spans (removed when attribution is present).
_QUOTED_RE = re.compile(r'"[^"]{1,200}"|\'[^\']{1,200}\'')

#: "don't forget ..." guard: such clauses never contribute evidence.
#: FP: "don't forget to pay your bill".
_FORGET_RE = re.compile(r"\bdon'?t forget\b|\bdo not forget\b", re.IGNORECASE)

#: "don't mean ..." guard: denying constructions never contribute.
_MEAN_RE = re.compile(r"\bdon'?t\s+mean\b|\bdidn'?t\s+mean\b|\bdo\s+not\s+mean\b", re.IGNORECASE)

# -- Technical payment signals ---------------------------------------------
# Grounded in aftercare rows (broken link, won't load, paid-but-nothing)
# and the proposal's "charged twice / link doesn't work / payment failed".
# "someone in charge" can never match: every alternative requires a
# payment-failure or access-failure frame, never bare "charge".
_TECHNICAL_RES: tuple[re.Pattern[str], ...] = (
    # "charged twice", "double charge/charged", "charged me twice"
    re.compile(r"\bcharged\s+twice\b", re.IGNORECASE),
    re.compile(r"\bdouble\s+charg\w*\b", re.IGNORECASE),
    re.compile(r"\bcharg\w*\s+me\s+twice\b", re.IGNORECASE),
    # "payment failed / declined / didn't go through / error"
    re.compile(
        r"\bpayment\s+(failed|declined|was\s+declined|didn'?t\s+go\s+through"
        r"|not\s+going\s+through|error)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bcharg\w+\s+(failed|declined)\b", re.IGNORECASE),
    # "link is broken / doesn't work / expired"
    re.compile(
        r"\blink\b.{0,30}\b(broken|doesn'?t\s+work|does\s+not\s+work"
        r"|not\s+work\w*|expired|invalid)\b",
        re.IGNORECASE,
    ),
    # "video won't load / can't access / not downloading"
    re.compile(
        r"\b(video|videos|content|download|downloads|access|link)\b.{0,30}"
        r"\b(won'?t\s+load|can'?t\s+(access|open|load|view)"
        r"|not\s+(loading|downloading|showing|working|opening))\b",
        re.IGNORECASE,
    ),
    # "paid / payment ... haven't received / never arrived / nothing / no access"
    # FP: "not what I paid for" (no non-delivery frame -> no match);
    # "paid $50 and got low quality" (quality, not non-delivery -> no match).
    re.compile(
        r"\b(paid|payment)\b.{0,40}\b(haven'?t\s+(received|got|arrived)"
        r"|never\s+(received|got|arrived|came)|nothing\s+(arrived|came|showed)"
        r"|no\s+(access|content|delivery))\b",
        re.IGNORECASE,
    ),
)

# -- Payment claims / freebie patterns --------------------------------------
# Grounded in complaint rows (scam accusation, ripped off, dispute,
# refund demand) plus free demands. Deliberately narrower than
# objection TRUST (trust/scam/fake): bare "trust", "fake", "legit"
# never fire (reassurance "is this legit / can I trust you" FP).
_CLAIM_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bscam\w*\b", re.IGNORECASE),
    re.compile(r"\bripped\s+off\b", re.IGNORECASE),
    re.compile(r"\bchargeback\w*\b", re.IGNORECASE),
    re.compile(r"\bdispute\w*\b", re.IGNORECASE),
    # Refund demand. Bare "refund" fires fail-closed (a refund word in a
    # complaint turn is payment-related); routing only *suppresses*
    # handoff for positively-identified experience-only turns, so this
    # FP direction keeps humans in the loop.
    re.compile(r"\brefund\w*\b", re.IGNORECASE),
    # Free demand: demand verb + "free" within a short window.
    # FP: "free tier", "free and paid stuff" (no demand verb -> no match).
    re.compile(
        r"\b(give|send|want|need|demand\w*|get|offer)\b.{0,30}\bfree\b",
        re.IGNORECASE,
    ),
    # "you never send me anything"
    re.compile(r"\byou\s+never\s+send\b", re.IGNORECASE),
)

# -- Chat-experience complaints ----------------------------------------------
# Grounded in complaint rows (service terrible, not happy, disappointed)
# plus the annoyed-affect overlap (ignoring me / annoying / frustrating
# also drive RECOVER via persona_behavior — intended, not duplicated
# authority: strategy still decides the move).
_EXPERIENCE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brude\b", re.IGNORECASE),
    re.compile(r"\bboring\b", re.IGNORECASE),
    re.compile(r"\bignoring\s+me\b", re.IGNORECASE),
    re.compile(r"\bannoying\b", re.IGNORECASE),
    re.compile(r"\bfrustrating\b", re.IGNORECASE),
    re.compile(r"\bterrible\s+service\b", re.IGNORECASE),
    re.compile(r"\bcustomer\s+service\b.{0,25}\bterrible\b", re.IGNORECASE),
    re.compile(r"\bservice\b.{0,20}\b(terrible|awful|horrible|useless)\b", re.IGNORECASE),
    re.compile(r"\bnot\s+happy\b", re.IGNORECASE),
    re.compile(r"\bdisappoint\w*\b", re.IGNORECASE),
    # Responsiveness (vs "you never send" -> claim).
    re.compile(r"\byou\s+never\s+respond\b", re.IGNORECASE),
    re.compile(r"\blet\s+down\b", re.IGNORECASE),
    # Long waits ("waiting for 5 days"). FP: access-load waits also
    # match technical above -> both fire -> NOT experience-only ->
    # fail-closed handoff. Bare "waiting" without a time unit: no fire.
    re.compile(r"\bwaiting\b.{0,20}\b(days?|weeks?|hours?)\b", re.IGNORECASE),
    re.compile(r"\bwaited\b.{0,20}\b(days?|weeks?|hours?)\b", re.IGNORECASE),
)


# ---------------------------------------------------------------------------
# Normalization + guards (pure)
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    try:
        cleaned = str(text)
    except Exception:
        return ""
    cleaned = (
        cleaned.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("`", "'")
    )
    try:
        cleaned = re.sub(r"[.!?;](?:\s*[.!?;])+", " ", cleaned)
    except Exception:
        pass
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _strip_attributed_quotes(normalized: str) -> str:
    """Remove quoted spans under third-party attribution (pure)."""
    try:
        if _ATTRIBUTION_RE.search(normalized):
            return _QUOTED_RE.sub(" ", normalized)
    except Exception:
        pass
    return normalized


def _clauses(normalized: str) -> list[str]:
    """Split into clauses, dropping forget/mean-denial clauses."""
    try:
        parts = _CLAUSE_SPLIT_RE.split(normalized)
    except Exception:
        return [normalized]
    out: list[str] = []
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        try:
            if _FORGET_RE.search(piece):
                continue
            if _MEAN_RE.search(piece):
                continue
        except Exception:
            pass
        out.append(piece)
    return out


def _any_match(patterns: Sequence[re.Pattern[str]], clauses: Sequence[str]) -> bool:
    for clause in clauses:
        for pattern in patterns:
            try:
                if pattern.search(clause):
                    return True
            except Exception:
                continue
    return False


# ---------------------------------------------------------------------------
# Public extractor (pure; never raises — unusable input yields neutral)
# ---------------------------------------------------------------------------


def extract_complaint_triage_evidence(user_message: Any) -> ComplaintTriageEvidence:
    """Extract one turn of complaint-triage evidence (pure, fail-open).

    Text only: no LLM, no history, no DB, no clock, no persistence.
    Unusable input yields neutral evidence (all False), which callers
    must treat as unknown (fail-closed: legacy handoff preserved).
    """
    try:
        if not isinstance(user_message, str) or not user_message.strip():
            return _NEUTRAL_EVIDENCE
        normalized = _normalize(user_message)
        if not normalized:
            return _NEUTRAL_EVIDENCE
        if len(normalized) > 2000:
            normalized = normalized[:2000]
        dequoted = _strip_attributed_quotes(normalized)
        clauses = _clauses(dequoted)
        if not clauses:
            return _NEUTRAL_EVIDENCE
        return ComplaintTriageEvidence(
            technical_payment_signal=_any_match(_TECHNICAL_RES, clauses),
            payment_claim=_any_match(_CLAIM_RES, clauses),
            chat_experience_complaint=_any_match(_EXPERIENCE_RES, clauses),
        )
    except Exception:
        return _NEUTRAL_EVIDENCE


__all__ = [
    "ComplaintTriageEvidence",
    "extract_complaint_triage_evidence",
]
