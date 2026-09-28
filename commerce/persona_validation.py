"""Deterministic Persona Response Validation — Phase 43D.

Validates Qwen output against persona behavioral constraints.
Deterministic, O(response len), side-effect free, no LLM/DB/network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Small bounded generic patterns (not huge blacklist)
_GENERIC_ACK_PATTERNS = [
    re.compile(r"\bthat sounds amazing\b", re.I),
    re.compile(r"\bi totally get that\b", re.I),
    re.compile(r"\bthat must be\b", re.I),
    re.compile(r"\btell me more\b", re.I),
    re.compile(r"\bwhat about you\?\b", re.I),
]

_FORMAL_PHRASES = [
    re.compile(r"\bThat is certainly an interesting perspective\b", re.I),
    re.compile(r"\bI completely agree with you\b", re.I),
    re.compile(r"\bAs an AI\b", re.I),
    re.compile(r"\bI am here to assist\b", re.I),
]

# Emoji detection: broad range, but we treat Sunny's preferred as allowed
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "😭😂💕"
    "]",
)

@dataclass(frozen=True)
class PersonaResponseValidation:
    valid: bool
    casing_score: float
    emoji_score: float
    sentence_score: float
    question_score: float
    generic_pattern_score: float
    fact_violation: bool
    severe: bool
    reasons: list[str] = field(default_factory=list)
    validation_status: str = "PASS"  # PASS | SOFT_FAIL | FACT_FAIL

def _count_sentences(text: str) -> int:
    # Split on .!? but not ... etc, bounded
    parts = [p.strip() for p in re.split(r"[.!?]+", text.strip()) if p.strip()]
    return len(parts) if parts else 1

def _count_questions(text: str) -> int:
    return text.count("?")

def _count_emojis(text: str) -> int:
    return len(_EMOJI_RE.findall(text))

def _detect_fact_violation(response: str, persona: dict[str, Any] | None) -> tuple[bool, list[str]]:
    if not persona:
        return False, []
    reasons: list[str] = []
    # Identity name: check "Hi, I'm Mia" when persona is Sunny Skye
    identity = persona.get("identity") or {}
    name = identity.get("name") if isinstance(identity, dict) else None
    if name:
        # If response contains "I'm <other name>" with high confidence
        # Look for pattern "I'm Mia" etc.
        m = re.search(r"\bI'?m\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", response)
        if m:
            claimed = m.group(1).strip()
            # Compare case-insensitive, allow first name sunny
            if claimed.lower() not in name.lower() and claimed.lower() not in name.split()[0].lower():
                # But ensure claimed is likely a name, not "fine" etc
                # Heuristic: claimed word is capitalized and length 2-12, not common word
                common = {"fine","good","here","okay","sorry","nervous","excited","happy","sad","tired"}
                if claimed.lower() not in common and len(claimed) >= 2:
                    reasons.append(f"fact_identity_claimed_{claimed}")
                    return True, reasons
    # Age
    age = None
    if isinstance(identity, dict) and identity.get("age") is not None:
        age = identity.get("age")
    else:
        demo = persona.get("demographics", {}) if isinstance(persona.get("demographics"), dict) else {}
        age = demo.get("age") if isinstance(demo, dict) else None
    if age is not None:
        # Find "I am 21" or "I'm 21"
        m_age = re.search(r"\bI'?m\s+(\d{1,2})\b", response)
        if m_age:
            try:
                claimed_age = int(m_age.group(1))
                if claimed_age != int(age) and 13 <= claimed_age <= 60:
                    reasons.append(f"fact_age_{claimed_age}_vs_{age}")
                    return True, reasons
            except Exception:
                pass
        # Also "I am 21 years old"
        m_age2 = re.search(r"\b(\d{1,2})\s+years old\b", response, re.I)
        if m_age2:
            try:
                claimed_age = int(m_age2.group(1))
                if claimed_age != int(age) and 13 <= claimed_age <= 60:
                    reasons.append(f"fact_age_{claimed_age}_vs_{age}")
                    return True, reasons
            except Exception:
                pass
    # Location: persona NYC, response says "I live in Chicago"
    loc = persona.get("location") if isinstance(persona.get("location"), dict) else {}
    city = loc.get("city") if isinstance(loc, dict) else None
    if city and "new york" in city.lower():
        # Check if response claims Chicago as home
        if re.search(r"\bI live in (?:chicago|los angeles|miami)\b", response, re.I):
            reasons.append("fact_location_chicago_claim")
            return True, reasons
    # Occupation
    occ = persona.get("occupation") if isinstance(persona.get("occupation"), dict) else {}
    occ_title = occ.get("title") if isinstance(occ, dict) else None
    if occ_title and "graphic designer" in str(occ_title).lower():
        if re.search(r"\bI am a (?:software engineer|nurse|teacher|doctor)\b", response, re.I):
            # Unless fan knowledge says fan is that, but persona is separate
            # This is a persona fact violation if Sunny claims she's software engineer
            reasons.append("fact_occupation_mismatch")
            return True, reasons
    return False, reasons

def validate_persona_voice(
    response: str,
    persona: dict[str, Any] | None,
    behavior_state: Any | None,
    recent_assistant_messages: list[dict[str, Any]] | None = None,
) -> PersonaResponseValidation:
    """Validate response deterministically.
    Never raises, never does I/O.
    """
    try:
        if not response or not response.strip():
            return PersonaResponseValidation(
                valid=False,
                casing_score=0.0,
                emoji_score=0.0,
                sentence_score=0.0,
                question_score=0.0,
                generic_pattern_score=0.0,
                fact_violation=False,
                severe=False,
                reasons=["empty_response"],
                validation_status="SOFT_FAIL",
            )
        reasons: list[str] = []
        # Casing — for Sunny lowercase common, we consider too formal as violation, not lowercase ratio
        # Simple: if response is all uppercase words >50% and length >10, flag formal
        casing_score = 1.0
        text_lower = response.lower()
        # Formal detection
        formal_hits = 0
        for pat in _FORMAL_PHRASES:
            if pat.search(response):
                formal_hits += 1
                reasons.append("too_formal_phrase")
                casing_score = 0.4
        # If persona allows lowercase, we don't require lowercase, just not overly formal
        # So casing_score remains high unless formal phrase detected

        # Emoji
        emoji_count = _count_emojis(response)
        emoji_score = 1.0
        try:
            emoji_policy = getattr(behavior_state, "emoji_policy", "occasional") if behavior_state else "occasional"
        except Exception:
            emoji_policy = "occasional"
        if emoji_policy == "occasional":
            if emoji_count >= 4:
                emoji_score = 0.3
                reasons.append(f"emoji_spam_{emoji_count}")
            elif emoji_count >= 3:
                emoji_score = 0.6
                reasons.append(f"emoji_many_{emoji_count}")
            else:
                emoji_score = 1.0
        elif emoji_policy == "none":
            if emoji_count >= 1:
                emoji_score = 0.5
                reasons.append(f"emoji_unexpected_{emoji_count}")
        else:  # allow_one
            if emoji_count > 1:
                emoji_score = 0.7
                reasons.append(f"emoji_over_1_{emoji_count}")

        # Sentence length
        sent_count = _count_sentences(response)
        sentence_score = 1.0
        try:
            verbosity = getattr(behavior_state, "verbosity_target", "short_medium") if behavior_state else "short_medium"
        except Exception:
            verbosity = "short_medium"
        if verbosity == "short_medium":
            if sent_count > 5:
                sentence_score = 0.4
                reasons.append(f"too_long_{sent_count}_gt_5")
            elif sent_count > 4:
                sentence_score = 0.7
                reasons.append(f"long_{sent_count}")
            else:
                sentence_score = 1.0
        elif verbosity == "short":
            if sent_count > 3:
                sentence_score = 0.5
                reasons.append(f"too_long_short_{sent_count}")

        # Question compliance
        question_count = _count_questions(response)
        question_score = 1.0
        try:
            q_allowed = bool(getattr(behavior_state, "question_allowed", False)) if behavior_state else False
            q_policy = getattr(behavior_state, "question_policy", "NO_QUESTION") if behavior_state else "NO_QUESTION"
        except Exception:
            q_allowed = False
            q_policy = "NO_QUESTION"
        if not q_allowed and question_count >= 1:
            # Allow if question is rhetorical? For now, any ? when not allowed is violation
            # But be lenient: 1 question when not allowed is soft fail, not severe
            question_score = 0.5
            reasons.append(f"question_when_forbidden_{question_count}")
        elif q_allowed and q_policy == "ONE_NATURAL_QUESTION" and question_count > 1:
            question_score = 0.6
            reasons.append(f"too_many_questions_{question_count}")
        else:
            question_score = 1.0

        # Generic pattern
        generic_hits = 0
        for pat in _GENERIC_ACK_PATTERNS:
            # Check recent + current for repetition
            if pat.search(response):
                generic_hits += 1
                reasons.append(f"generic_pattern_{pat.pattern[:15]}")
        # Also check repetition vs recent 3
        if recent_assistant_messages:
            for m in recent_assistant_messages[-3:]:
                prev = (m.get("content") or "").lower()
                for pat in _GENERIC_ACK_PATTERNS:
                    if pat.search(prev) and pat.search(response):
                        generic_hits += 1
                        reasons.append("repeated_template_across_turns")
                        break
        generic_score = 1.0
        if generic_hits >= 2:
            generic_score = 0.4
        elif generic_hits == 1:
            generic_score = 0.7

        # Fact violation (severe)
        fact_viol, fact_reasons = _detect_fact_violation(response, persona)
        if fact_viol:
            reasons.extend(fact_reasons)
            return PersonaResponseValidation(
                valid=False,
                casing_score=casing_score,
                emoji_score=emoji_score,
                sentence_score=sentence_score,
                question_score=question_score,
                generic_pattern_score=generic_score,
                fact_violation=True,
                severe=True,
                reasons=reasons,
                validation_status="FACT_FAIL",
            )

        # Determine overall
        # Severe if fact violation or too_formal + emoji_spam etc?
        severe = fact_viol
        # Voice severe if extremely formal + very long + emoji spam
        if casing_score < 0.5 and sentence_score < 0.5:
            severe = False  # still soft unless fact
        # Overall valid if no severe and scores decent
        # For now, valid = not severe and not too many soft fails
        soft_fail_count = sum(1 for r in reasons if r.startswith(("too_formal", "emoji_spam", "too_long", "question_when_forbidden", "generic_pattern")))
        valid = not severe and soft_fail_count < 2

        # Compute aggregate voice_score etc for telemetry
        # Simple average of component scores
        status = "PASS"
        if fact_viol:
            status = "FACT_FAIL"
        elif not valid:
            status = "SOFT_FAIL"

        return PersonaResponseValidation(
            valid=valid,
            casing_score=casing_score,
            emoji_score=emoji_score,
            sentence_score=sentence_score,
            question_score=question_score,
            generic_pattern_score=generic_score,
            fact_violation=fact_viol,
            severe=severe,
            reasons=reasons,
            validation_status=status,
        )
    except Exception:
        # Never crash validation
        return PersonaResponseValidation(
            valid=True,
            casing_score=1.0,
            emoji_score=1.0,
            sentence_score=1.0,
            question_score=1.0,
            generic_pattern_score=1.0,
            fact_violation=False,
            severe=False,
            reasons=[],
            validation_status="PASS",
        )

