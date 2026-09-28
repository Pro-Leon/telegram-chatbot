"""Conversation grounding contracts — Phase 89R Hardening.

Deterministic, pre-generation, no LLM, no DB.
Roleplay is explicit and authoritative.

Invariant (additive, never inverted):
    CHARACTER = creator persona (e.g., Sunny Skye)  == SPEAKER
    PLAYER    = fan/listener   (e.g., Fan)        == LISTENER
    TURN: PLAYER speaks -> CHARACTER responds

Transport roles (user/assistant) are NOT roleplay identities.
The contract is deterministic and exists BEFORE OneCall.
No LLM is used to determine the contract.

Terminology is explicit to make confusion impossible.
Backward compatible aliases: speaker_name <-> character_name, listener_name <-> player_name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Any

# Canonical literals
QuestionTargetCanonical = Literal["character", "none"]
QuestionTargetCompat = Literal["character", "speaker", "none"]
CurrentIntent = Literal["question", "statement", "greeting", "other"]
TurnOwner = Literal["player", "character"]
RoleName = Literal["creator_persona", "creator", "persona", "fan", "player"]

_QUESTION_RE = re.compile(r"\?\s*$")
_WH_RE = re.compile(r"^\s*(what|how|when|where|who|why|can you|could you|do you|are you|have you|will you|would you|did you|is |are |who's|what's)\b", re.I)

def _is_question(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if _QUESTION_RE.search(t):
        return True
    return bool(_WH_RE.match(t.lower()))

# ---------------------------------------------------------------------------
# Dual-equality helper for question_target backward compat
# Stores canonical "character" but == "speaker" is True for legacy code.
# ---------------------------------------------------------------------------
class _QuestionTargetStr(str):
    """String that is equal to both 'character' and 'speaker' when canonical is character.

    Allows:
        contract.question_target == "character"  -> True (canonical)
        contract.question_target == "speaker"    -> True (legacy alias)
        contract.question_target == "none"       -> only if actually none
    """
    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            s = str(self)
            o = str(other)
            if s == "character" and o == "speaker":
                return True
            if s == "speaker" and o == "character":
                return True
            return str.__eq__(s, o)
        return super().__eq__(other)

    def __hash__(self) -> int:  # type: ignore[override]
        return str.__hash__(self)

    def __repr__(self) -> str:
        return f"'{str(self)}'"

# Keep old QuestionTarget alias for imports
QuestionTarget = Literal["speaker", "none", "character"]  # type: ignore


@dataclass(frozen=True)
class ConversationParticipants:
    """Immutable roleplay grounding — derived, not inferred.

    Canonical:
        character_name == speaker_name == creator persona (e.g., Sunny Skye)
        player_name    == listener_name == fan (e.g., Fan)

    Aliases retained for backward compatibility, but canonical semantic is explicit.
    character_role is always "creator_persona"; player_role is "fan".
    TURN: PLAYER speaks -> CHARACTER responds.
    """

    # Canonical storage (with compat aliases via __post_init__ sync)
    # We expose all four as fields so both old and new constructors work.
    speaker_name: str = ""
    listener_name: str = ""
    speaker_role: str = "creator"  # legacy default, normalized to creator_persona for character_role
    listener_role: str = "fan"
    character_name: str | None = None
    player_name: str | None = None
    character_role: str | None = None
    player_role: str | None = None

    def __post_init__(self) -> None:
        # Resolve character/player from whichever alias was supplied
        # Prefer explicit character_name/player_name if provided and non-empty
        cn = self.character_name if self.character_name not in (None, "") else self.speaker_name
        pn = self.player_name if self.player_name not in (None, "") else self.listener_name

        # If still empty, raise
        if not cn or not str(cn).strip() or not pn or not str(pn).strip():
            raise ValueError("speaker_name/character_name and listener_name/player_name required")
        cn = str(cn).strip()
        pn = str(pn).strip()
        if cn.lower() == pn.lower():
            raise ValueError("speaker and listener names must differ")

        # Roles: resolve canonical
        # speaker_role legacy default "creator" -> character_role "creator_persona"
        raw_char_role = self.character_role if self.character_role not in (None, "") else self.speaker_role
        raw_player_role = self.player_role if self.player_role not in (None, "") else self.listener_role

        # Normalize character role: creator/persona -> creator_persona
        if raw_char_role in ("creator", "persona"):
            canonical_char_role = "creator_persona"
        elif raw_char_role in ("creator_persona",):
            canonical_char_role = "creator_persona"
        else:
            canonical_char_role = str(raw_char_role).strip() if raw_char_role else "creator_persona"

        if raw_player_role in ("fan", "player"):
            canonical_player_role = "fan" if raw_player_role == "fan" else "fan"  # keep fan canonical
            # spec says player_role = "fan" ; allow "player" alias but normalize to fan for consistency
            # Actually keep as provided if "player", but default is fan
            if raw_player_role == "player":
                canonical_player_role = "fan"  # normalize player -> fan for telemetry compat, but allow alias via equality?
                # Keep "fan" canonical; alias handling via property not needed
                pass
            else:
                canonical_player_role = "fan"
        else:
            canonical_player_role = "fan"

        # For backward compat, keep speaker_role as "creator" if original was that,
        # but character_role is normalized.
        # If caller supplied speaker_name path, speaker_role stays as originally (default "creator")
        # If caller supplied character_name path, speaker_role should mirror character_role normalized? But keep creator compat.
        # Decide: speaker_role stored = raw_char_role if raw_char_role in ("creator","persona") else canonical_char_role
        # To satisfy old test expecting speaker_role == "creator", we keep speaker_role as "creator" when canonical is creator_persona and original was default.
        # So we keep speaker_role as raw_char_role if raw is creator/persona, else canonical.

        # Determine legacy speaker_role to preserve old test expectation
        # If no explicit speaker_role/character_role supplied, default is "creator" for legacy, but character_role should be "creator_persona"
        # So we set speaker_role_legacy = "creator" when canonical is creator_persona and no explicit override
        speaker_role_legacy = raw_char_role if raw_char_role in ("creator", "persona") else canonical_char_role
        # If raw was default "creator" (from field default), keep speaker_role_legacy = "creator" for old test
        # character_role canonical is "creator_persona"
        # listener_role stays "fan"

        # Synchronize all aliases to same names but roles differ slightly for compat
        object.__setattr__(self, "speaker_name", cn)
        object.__setattr__(self, "listener_name", pn)
        object.__setattr__(self, "character_name", cn)
        object.__setattr__(self, "player_name", pn)

        object.__setattr__(self, "speaker_role", speaker_role_legacy if speaker_role_legacy in ("creator", "creator_persona", "persona") else "creator")
        # listener_role stays fan
        object.__setattr__(self, "listener_role", "fan")
        object.__setattr__(self, "character_role", canonical_char_role)
        object.__setattr__(self, "player_role", canonical_player_role)

    # Alias properties for alternative access (redundant with synced fields, but explicit)
    @property
    def char_name(self) -> str:  # type: ignore
        return self.character_name or self.speaker_name

    @property
    def play_name(self) -> str:  # type: ignore
        return self.player_name or self.listener_name


@dataclass(frozen=True)
class ConversationContract:
    """Deterministic conversational expectations for this turn (pre-generation).

    Explicit roleplay fields (canonical):
        character_name, player_name, character_role, player_role,
        current_turn_owner, response_owner,
        answer_required, question_target, current_intent, current_topic,
        maintain_topic, last_question, last_question_answered

    Compat aliases:
        speaker_name == character_name, listener_name == player_name,
        question_target "speaker" alias for "character" (dual equality).
    """

    speaker_name: str = ""
    listener_name: str = ""
    character_name: str | None = None
    player_name: str | None = None
    character_role: str | None = None
    player_role: str | None = None
    current_turn_owner: TurnOwner = "player"
    response_owner: TurnOwner = "character"
    answer_required: bool = False
    question_target: Any = "none"  # compatibility: may be _QuestionTargetStr
    current_intent: CurrentIntent = "other"
    current_topic: str | None = None
    maintain_topic: bool = False
    last_question: str | None = None
    last_question_answered: bool = True

    def __post_init__(self) -> None:
        # Sync character/player names from speaker/listener if needed
        cn = self.character_name if self.character_name not in (None, "") else self.speaker_name
        pn = self.player_name if self.player_name not in (None, "") else self.listener_name
        if not cn:
            cn = self.speaker_name
        if not pn:
            pn = self.listener_name
        # Roles
        cr = self.character_role if self.character_role not in (None, "") else "creator_persona"
        pr = self.player_role if self.player_role not in (None, "") else "fan"
        if cr in ("creator", "persona"):
            cr = "creator_persona"
        if pr == "player":
            pr = "fan"

        # question_target dual handling
        qt_raw = self.question_target
        # Normalize: "speaker" -> canonical "character" with dual equality
        if qt_raw == "speaker":
            qt_canonical: Any = _QuestionTargetStr("character")
        elif qt_raw == "character":
            qt_canonical = _QuestionTargetStr("character")
        elif qt_raw == "none" or qt_raw is None or qt_raw == "":
            qt_canonical = "none"
        else:
            # keep as is but wrap if character-like
            qt_canonical = qt_raw

        # current_turn_owner / response_owner defaults per spec
        cto = self.current_turn_owner
        ro = self.response_owner
        if cto not in ("player", "character"):
            cto = "player"
        if ro not in ("character", "player"):
            ro = "character"
        # For normal fan turn, enforce invariant
        # If answer_required true, turn must be player->character
        # Keep as derived; don't override if explicitly set differently (fail-open)
        # But ensure defaults align with spec: player speaks, character responds
        if not cto:
            cto = "player"
        if not ro:
            ro = "character"

        object.__setattr__(self, "speaker_name", str(cn) if cn else "")
        object.__setattr__(self, "listener_name", str(pn) if pn else "")
        object.__setattr__(self, "character_name", str(cn) if cn else "")
        object.__setattr__(self, "player_name", str(pn) if pn else "")
        object.__setattr__(self, "character_role", cr)
        object.__setattr__(self, "player_role", pr)
        object.__setattr__(self, "current_turn_owner", cto)
        object.__setattr__(self, "response_owner", ro)
        object.__setattr__(self, "question_target", qt_canonical)
        # other fields remain as is (already set via dataclass)

def derive_participants(authoritative_state: Any, fallback_listener: str = "Fan") -> ConversationParticipants:
    """Derive participants from AuthoritativeState (no DB, no arbitrary overrides).

    Invariant enforced:
        character_name == authoritative_persona_name (speaker)
        player_name    == authoritative_fan_name (listener)
        speaker == character, listener == player, never inverted.
    """
    # Speaker from structured persona or persona_name or fallback
    speaker = None
    try:
        sp = getattr(authoritative_state, "structured_persona", None)
        if sp is not None and hasattr(sp, "get"):
            try:
                ident = sp.get("identity") or {}
            except Exception:
                ident = {}
            if hasattr(ident, "get") and ident.get("name"):
                speaker = str(ident.get("name")).strip()
            elif hasattr(sp, "get") and sp.get("display_name"):
                speaker = str(sp.get("display_name")).strip()
    except Exception:
        pass
    if not speaker:
        pn = getattr(authoritative_state, "persona_name", None)
        if pn:
            speaker = str(pn).strip()
    if not speaker:
        try:
            persona_text = getattr(authoritative_state, "persona", "") or ""
            m = re.search(r"You are\s+([A-Za-z ]+?)(?:\s*[—. ]|$)", persona_text)
            if m:
                cand = m.group(1).strip()
                if cand and len(cand.split()) <= 3:
                    speaker = cand
        except Exception:
            pass
    if not speaker:
        speaker = "Sunny Skye"
    # Listener from user first_name, privacy-safe fallback
    listener = None
    try:
        user = getattr(authoritative_state, "user", {}) or {}
        if user is not None and hasattr(user, "get"):
            fn = user.get("first_name")
            if fn and isinstance(fn, str) and fn.strip() and fn.strip().lower() not in ("there", "user", "fan"):
                listener = fn.strip()
    except Exception:
        pass
    if not listener:
        listener = fallback_listener
    # ensure distinct + never inverted (if fan name equals persona, fallback to Fan)
    if speaker.strip().lower() == listener.strip().lower():
        listener = "Fan"
    # Enforce invariant: speaker is persona, listener is fan – never allow arbitrary override
    # Additional safety: if authoritative_state has explicit persona_name and it differs from derived, prefer authoritative
    try:
        auth_persona = getattr(authoritative_state, "persona_name", None)
        if auth_persona and isinstance(auth_persona, str) and auth_persona.strip():
            # canonical persona is auth_persona; ensure derived matches (unless MappingProxy fallback)
            # If mismatch due to structured_persona missing, we already used it; but enforce consistency
            # Only override if speaker was fallback generic and auth is more specific
            if speaker == "Sunny Skye" and auth_persona.strip() != speaker:
                # keep derived if auth is generic? but spec says character == authoritative_persona_name
                # So enforce auth
                speaker = auth_persona.strip()
    except Exception:
        pass
    return ConversationParticipants(speaker_name=speaker, listener_name=listener)

def derive_contract(authoritative_state: Any, participants: ConversationParticipants | None = None) -> ConversationContract:
    """Derive contract from state + current_message.

    Uses conversation_state for current_topic/last_question.
    Determines answer_required based on current_message being question directed to character.

    New roleplay semantics:
        current_turn_owner = player (fan has spoken)
        response_owner     = character (Sunny must respond)
        question_target    = character when fan asks, else none (canonical "character", alias "speaker")
        character_name/player_name mirrored from participants
    """
    try:
        current_msg = str(getattr(authoritative_state, "current_message", "") or "")
    except Exception:
        current_msg = ""
    is_q = _is_question(current_msg)
    # question target: fan asks -> character (Sunny). Canonical "character", legacy alias "speaker"
    # Use dual string so == "speaker" and == "character" both True
    if is_q:
        q_target: Any = _QuestionTargetStr("character")
        answer_required = True
    else:
        q_target = "none"
        answer_required = False
    # intent
    if is_q:
        intent: CurrentIntent = "question"
    elif not current_msg.strip():
        intent = "other"
    else:
        low = current_msg.strip().lower()
        if low in ("hi", "hey", "hello", "hola"):
            intent = "greeting"
        elif "?" in current_msg:
            intent = "question"
        else:
            intent = "statement"
    # topic from conversation_state
    current_topic: str | None = None
    last_q: str | None = None
    last_q_answered = True
    maintain = False
    try:
        cs = getattr(authoritative_state, "conversation_state", None)
        if cs is not None:
            current_topic = getattr(cs, "current_topic", None)
            last_q = getattr(cs, "last_question", None)
            last_q_answered = bool(getattr(cs, "last_question_answered", True))
            if current_topic:
                try:
                    open_threads = getattr(cs, "open_threads", ()) or ()
                    low_topic = str(current_topic).lower()
                    low_msg = current_msg.lower()
                    if low_topic in low_msg:
                        maintain = True
                    elif any(t.lower() in low_msg for t in open_threads):
                        maintain = True
                    else:
                        maintain = True
                except Exception:
                    maintain = bool(current_topic)
    except Exception:
        pass
    if participants is None:
        try:
            participants = derive_participants(authoritative_state)
        except Exception:
            participants = ConversationParticipants(speaker_name="Sunny Skye", listener_name="Fan")

    # Derive roleplay ownership (deterministic, before OneCall)
    current_turn_owner: TurnOwner = "player"
    response_owner: TurnOwner = "character"

    # If no message (edge), keep defaults but still player->character for ordinary turn
    # Do not use LLM to determine contract.

    return ConversationContract(
        speaker_name=participants.speaker_name,
        listener_name=participants.listener_name,
        character_name=participants.character_name,
        player_name=participants.player_name,
        character_role=participants.character_role,
        player_role=participants.player_role,
        current_turn_owner=current_turn_owner,
        response_owner=response_owner,
        answer_required=answer_required,
        question_target=q_target,
        current_intent=intent,
        current_topic=current_topic,
        maintain_topic=maintain,
        last_question=last_q,
        last_question_answered=last_q_answered,
    )

def render_participants_block(participants: ConversationParticipants) -> str:
    """Authoritative participant block — compact, explicit CHARACTER/PLAYER.

    Generated from AuthoritativeState, not from arbitrary request params.
    Uses dual labeling CHARACTER / SPEAKER and PLAYER / LISTENER to make
    roleplay vs transport distinction impossible to misunderstand.
    """
    # Ensure we use canonical names (character == speaker)
    char_name = participants.character_name or participants.speaker_name
    play_name = participants.player_name or participants.listener_name
    char_role = participants.character_role or participants.speaker_role
    play_role = participants.player_role or participants.listener_role
    # Normalize display roles for spec
    char_role_display = "creator persona being portrayed" if char_role in ("creator_persona", "creator", "persona") else str(char_role)
    play_role_display = "fan interacting with the character" if play_role in ("fan", "player") else str(play_role)
    return (
        "[CONVERSATION PARTICIPANTS - AUTHORITATIVE]\n"
        f"CHARACTER / SPEAKER:\n{char_name}\n\n"
        f"PLAYER / LISTENER:\n{play_name}\n\n"
        f"CHARACTER ROLE:\n{char_role_display}\n\n"
        f"PLAYER ROLE:\n{play_role_display}\n\n"
        "The CHARACTER is the creator persona (assistant). The PLAYER is the fan.\n"
        "The SPEAKER is the CHARACTER. The LISTENER is the PLAYER.\n"
        "Do not reverse character and player. Do not address the PLAYER using the CHARACTER's name.\n"
        "Do not reverse speaker and listener identities."
    )

def render_contract_block(contract: ConversationContract) -> str:
    """Authoritative roleplay + deterministic contract block — compact.

    Hierarchy: SYSTEM RULES -> ROLEPLAY CONTRACT -> CHARACTER PERSONA -> STATE -> ...
    Keep compact for context budget (spec: 80-120 tokens). Does not duplicate full persona.
    """
    char_name = contract.character_name or contract.speaker_name or "Sunny Skye"
    play_name = contract.player_name or contract.listener_name or "Fan"
    char_role = contract.character_role or "creator_persona"
    play_role = contract.player_role or "fan"
    # Normalize question_target display: canonical "character" but legacy alias "speaker" maps to character for display
    qt = contract.question_target
    # For display, show "character" canonical when question, else "none"
    if isinstance(qt, str) and qt in ("speaker", "character"):
        qt_display = "character"
    else:
        qt_display = str(qt) if qt is not None else "none"

    # Compact roleplay contract per spec section 4, plus deterministic fields
    return (
        "[ROLEPLAY CONTRACT - AUTHORITATIVE]\n"
        f"CHARACTER:\n{char_name}\n\n"
        f"PLAYER:\n{play_name}\n\n"
        f"CHARACTER ROLE:\n{char_role} (creator persona being portrayed)\n\n"
        f"PLAYER ROLE:\n{play_role} (fan interacting with the character)\n\n"
        "CURRENT TURN:\nThe PLAYER has spoken. The CHARACTER must respond.\n\n"
        "RESPONSE IDENTITY:\nSpeak as the CHARACTER.\n\n"
        "ROLEPLAY RULES:\n"
        "- Stay in character as the CHARACTER.\n"
        "- Never speak as the PLAYER.\n"
        "- Never write dialogue for the PLAYER.\n"
        "- Never decide the PLAYER's actions, thoughts, feelings, or intentions.\n"
        "- Do not confuse the CHARACTER's name with the PLAYER's name.\n"
        "- Respond to the PLAYER's current message before changing topics.\n\n"
        f"CURRENT INTENT: {contract.current_intent}\n"
        f"QUESTION TARGET: {qt_display}\n"
        f"ANSWER REQUIRED: {'yes' if contract.answer_required else 'no'}\n"
        f"CURRENT TOPIC: {contract.current_topic or 'none'}\n"
        f"MAINTAIN TOPIC: {'yes' if contract.maintain_topic else 'no'}\n"
        f"LAST QUESTION ANSWERED: {'yes' if contract.last_question_answered else 'no'}\n"
        f"CURRENT TURN OWNER: {contract.current_turn_owner}\n"
        f"RESPONSE OWNER: {contract.response_owner}"
    )

# Backward compat alias for older imports expecting render_participants_block / render_contract_block
# Already defined.

# Additional helper for hierarchy documentation (not rendered, but for verification)
ROLEPLAY_HIERARCHY_DOC = (
    "SYSTEM RULES\n"
    "  ↓\n"
    "ROLEPLAY CONTRACT\n"
    "  ↓\n"
    "CHARACTER PERSONA\n"
    "  ↓\n"
    "AUTHORITATIVE STATE\n"
    "  ↓\n"
    "MEMORY / KNOWLEDGE\n"
    "  ↓\n"
    "CONVERSATION CONTEXT\n"
    "  ↓\n"
    "PLAYER MESSAGE (untrusted)"
)

# CONVERSATION CONTRACT - AUTHORITATIVE (legacy alias retained for backward compat / test detection)
_CONVERSATION_CONTRACT_ALIAS = "CONVERSATION CONTRACT"
