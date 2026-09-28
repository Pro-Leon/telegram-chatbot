"""Phase 2 Authoritative Context Assembly (single coordinated acquisition).

ONE TURN = ONE AUTHORITATIVE STATE SNAPSHOT.
All downstream consumers (Context Engine ranking, conflict resolution,
dedup, budget, rendering, OneCall) reuse the SAME instance.

This module is the ONLY place where per-turn authoritative DB state is
fetched. It preserves parallel I/O exactly as proven in memory/context.py
(Phase 44C) and does not refetch the same state elsewhere in the
canonical new path.

Invariants:
- No LLM calls.
- No writes.
- Deterministic given same DB snapshot + same inputs.
- Creator-scoped.
- Failure-isolated per source (same as memory/context.py).
- Does NOT become commerce execution authority (price remains fangate_products).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

from context_engine.models import AuthoritativeState

logger = logging.getLogger("context_engine.authoritative_assembly")


# Counter to prove single derivation per turn in tests
_derive_call_count: int = 0


def get_derive_call_count() -> int:
    return _derive_call_count


def reset_derive_call_count() -> None:
    global _derive_call_count
    _derive_call_count = 0


def _increment_derive_count() -> None:
    global _derive_call_count
    _derive_call_count += 1


async def assemble_authoritative_context(
    *,
    creator_id: int | None,
    user_id: int,
    current_message: str,
    generation_id: str | None = None,
    persona_override: str | None = None,
    structured_persona_snapshot: dict[str, Any] | None = None,
) -> AuthoritativeState:
    """Coordinated acquisition of authoritative state for one turn.

    Preserves parallel I/O: get_user, get_user_profile, get_recent_messages,
    get_latest_summary, get_structured_persona all overlap where safe.
    Fan knowledge interest isolation is applied after profile fetch.
    Commerce context (LLMContext) is built with reuse of already-fetched
    user/recent/summary to avoid redundant PG reads (B2 pattern).

    Conversation state is derived EXACTLY ONCE and stored on the snapshot.
    """
    from db.postgres import (
        get_latest_summary_with_age_durable,
        get_recent_messages_durable,
        get_user_durable,
        get_user_profile,
    )

    start = time.monotonic()

    # Prepare parallel coroutines (same set as memory/context.py:544).
    # Phase 2.2: durable variants never raise; they report (payload, degraded, source).
    snapshot_struct = structured_persona_snapshot
    coro_user = get_user_durable(user_id)
    coro_profile = get_user_profile(user_id)
    coro_recent = get_recent_messages_durable(user_id, limit=20, creator_id=creator_id)
    coro_summary = get_latest_summary_with_age_durable(user_id, creator_id=creator_id)

    if snapshot_struct is None and creator_id is not None:
        try:
            from memory.creator_persona import get_structured_persona_async as _get_struct_async
            coro_struct = _get_struct_async(creator_id)
            gather_results = await asyncio.gather(
                coro_user, coro_profile, coro_recent, coro_summary, coro_struct, return_exceptions=True
            )
            user_res, profile_res, recent_res, summary_res, struct_res = gather_results
        except Exception:
            # Fallback without struct
            gather_results = await asyncio.gather(coro_user, coro_profile, coro_recent, coro_summary, return_exceptions=True)
            user_res, profile_res, recent_res, summary_res = gather_results  # type: ignore[misc]
            struct_res = snapshot_struct
    else:
        gather_results = await asyncio.gather(coro_user, coro_profile, coro_recent, coro_summary, return_exceptions=True)
        user_res, profile_res, recent_res, summary_res = gather_results  # type: ignore[misc]
        struct_res = snapshot_struct

    # Phase 2.2: durable unpack — MISS vs ERROR distinguished; ERROR tags degraded.
    # Value fallbacks unchanged (placeholder user, [], (None, None)).
    if isinstance(user_res, Exception) or not isinstance(user_res, tuple):
        _u_val, _u_deg, _u_src = None, True, "error"
    else:
        _u_val, _u_deg, _u_src = user_res
    user = _u_val if _u_val is not None else {"first_name": "there", "funnel_stage": "new"}
    profile = profile_res if not isinstance(profile_res, Exception) and isinstance(profile_res, dict) else {}
    if isinstance(recent_res, Exception) or not isinstance(recent_res, tuple):
        _r_val, _r_deg, _r_src = [], True, "error"
    else:
        _r_val, _r_deg, _r_src = recent_res
        if not isinstance(_r_val, list):
            _r_val, _r_deg, _r_src = [], True, "error"
    recent = _r_val
    if isinstance(summary_res, Exception) or not isinstance(summary_res, tuple):
        _s_val, _s_deg, _s_src = (None, None), True, "error"
    else:
        try:
            _s_raw, _s_deg, _s_src = summary_res
            _s_val = _s_raw if isinstance(_s_raw, tuple) else (None, None)
        except Exception:
            _s_val, _s_deg, _s_src = (None, None), True, "error"
    summary, summary_age_days = _s_val
    history_degraded = bool(_u_deg or _r_deg or _s_deg)
    history_sources = {"user": _u_src, "recent": _r_src, "summary": _s_src}
    if history_degraded:
        logger.warning(
            "authoritative assembly history degraded user=%s creator=%s generation=%s sources=%s",
            user_id,
            creator_id,
            generation_id,
            history_sources,
        )
    structured_for_name = struct_res if not isinstance(struct_res, Exception) and isinstance(struct_res, dict) and struct_res else None
    if structured_persona_snapshot is not None and isinstance(structured_persona_snapshot, dict) and structured_persona_snapshot:
        structured_for_name = structured_persona_snapshot

    # Store for generation-local reuse (legacy cache compatibility)
    try:
        from memory.context import _profile_cache
        _profile_cache["profile"] = profile if isinstance(profile, dict) else {}
        _profile_cache["user"] = user if isinstance(user, dict) else {}
    except Exception:
        pass

    # Fan knowledge interest isolation (reuse of build_qwen3_context logic without refetch)
    # B1 pattern: pass profile to avoid redundant PG
    if creator_id is not None:
        try:
            from commerce.fan_knowledge import get_fan_knowledge as _get_fk_isolation
            _fk_for_profile = await _get_fk_isolation(creator_id, user_id, profile=profile)
            _interest_vals = [k["value"] for k in _fk_for_profile if k.get("subject") == "interest" and k.get("status") == "CURRENT"]
            if _interest_vals:
                profile = {**profile, "interests": _interest_vals}
            else:
                profile = {k: v for k, v in profile.items() if k not in ("interests", "preferences")}
        except Exception:
            profile = {k: v for k, v in profile.items() if k not in ("interests", "preferences")}

    # Persona text resolution (creator-scoped, with cache reuse)
    persona_text = persona_override if isinstance(persona_override, str) and persona_override else ""
    if not persona_text:
        if creator_id is not None:
            try:
                from db.postgres import get_user_persona as _gup
                # Try maybe persona per creator
                from db.redis import get_cached_user_persona, cache_user_persona  # type: ignore

                cached = await get_cached_user_persona(user_id, creator_id=creator_id)
                if cached:
                    persona_text = cached
                else:
                    persona_text = await _gup(user_id, creator_id=creator_id) or ""
                    if persona_text:
                        try:
                            await cache_user_persona(user_id, persona_text, creator_id=creator_id)
                        except Exception:
                            pass
                    else:
                        from db.redis import get_cached_default_persona, cache_default_persona
                        from db.postgres import get_default_persona as _gdp
                        cached_def = await get_cached_default_persona(creator_id=creator_id)
                        if cached_def:
                            persona_text = cached_def
                        else:
                            persona_text = await _gdp(creator_id=creator_id) or ""
                            if persona_text:
                                try:
                                    await cache_default_persona(persona_text, creator_id=creator_id)
                                except Exception:
                                    pass
            except Exception:
                try:
                    from db.postgres import get_user_persona as _gup2
                    persona_text = await _gup2(user_id, creator_id=creator_id) or ""  # type: ignore
                except Exception:
                    persona_text = ""
        else:
            try:
                from db.postgres import get_user_persona as _gup3
                persona_text = await _gup3(user_id, creator_id=None) or ""
            except Exception:
                persona_text = ""

    # Derive persona_name once (same logic as memory/context.py:622)
    persona_name: str | None = None
    try:
        if persona_text:
            first_line = persona_text.split("\n")[0]
            if "sunny" in first_line.lower():
                persona_name = "Sunny Skye"
            import re as _re
            m = _re.search(r"You are\s+([A-Za-z ]+?)(?:\s*[—. ]|$)", persona_text)
            if m:
                candidate = m.group(1).strip()
                if candidate and len(candidate.split()) <= 3:
                    persona_name = candidate
    except Exception:
        pass
    if structured_for_name:
        try:
            ident = structured_for_name.get("identity") or {}
            if isinstance(ident, dict) and ident.get("name"):
                persona_name = ident["name"]
            elif structured_for_name.get("display_name"):
                persona_name = structured_for_name["display_name"]
        except Exception:
            pass

    # Trim recent for state derive (same as build_qwen3_context 609)
    recent_trimmed: list[dict[str, Any]]
    try:
        from memory.context import trim_to_token_budget, QWEN3_TOKEN_BUDGET
        recent_trimmed = trim_to_token_budget(recent, QWEN3_TOKEN_BUDGET["conversation"])
        MAX_ASSISTANT_TURNS = 3
        assistant_indices: list[int] = []
        for i, msg in enumerate(recent_trimmed):
            if msg.get("direction") != "inbound":
                assistant_indices.append(i)
        drop_indices: set[int] = set()
        if len(assistant_indices) > MAX_ASSISTANT_TURNS:
            drop_indices = set(assistant_indices[:-MAX_ASSISTANT_TURNS])
        recent_trimmed = [m for i, m in enumerate(recent_trimmed) if i not in drop_indices]
    except Exception:
        recent_trimmed = list(recent)

    # Derive conversation state ONCE (canonical)
    conversation_state = None
    conversation_state_dict: dict[str, Any] | None = None
    try:
        from core.conversation_state import derive_conversation_state
        _increment_derive_count()
        history_for_state = list(recent_trimmed) + [{"direction": "inbound", "content": current_message}]
        conversation_state = derive_conversation_state(history_for_state, user=user)
        # Build dict form for scorer (stable)
        if conversation_state is not None:
            if isinstance(conversation_state, dict):
                conversation_state_dict = dict(conversation_state)
            else:
                try:
                    conversation_state_dict = dict(conversation_state.__dict__)  # type: ignore[attr-defined]
                except Exception:
                    try:
                        conversation_state_dict = vars(conversation_state)  # type: ignore[arg-type]
                    except Exception:
                        conversation_state_dict = None
    except Exception:
        logger.warning("authoritative_assembly: conversation_state derivation failed for user %s", user_id, exc_info=True)

    # Phase 89: persona identity/version for telemetry
    persona_id: int | None = None
    persona_version: int | None = None
    try:
        if isinstance(structured_for_name, dict):
            # _persona_id/_persona_version injected by get_structured_persona_async
            if structured_for_name.get("_persona_id") is not None:
                persona_id = int(structured_for_name["_persona_id"])  # type: ignore[arg-type]
            if structured_for_name.get("_persona_version") is not None:
                persona_version = int(structured_for_name["_persona_version"])
            elif structured_for_name.get("_db_version") is not None:
                persona_version = int(structured_for_name["_db_version"])
            elif structured_for_name.get("persona_version") is not None:
                persona_version = int(structured_for_name["persona_version"])
    except Exception:
        pass

    # Phase 89: derive deterministic participants + contract (no DB, no LLM)
    participants = None
    conversation_contract = None
    try:
        from core.conversation_contract import derive_participants, derive_contract
        # Build a minimal holder for derive (needs user, structured_persona, persona_name, current_message, conversation_state)
        # We will derive after AuthoritativeState is built, but we can also derive here using locals
        # to avoid second derivation pass; do it after state object creation below.
        pass
    except Exception:
        pass

    # Commerce LLMContext (B2 reuse of user/recent/summary)
    commerce_context_text = ""
    llm_ctx_obj = None
    if creator_id is not None:
        try:
            from memory.context_assembler import build_llm_context, render_context
            llm_ctx_obj = await build_llm_context(creator_id, user_id, user_data=user, recent_messages=recent, summary=summary)
            commerce_context_text = render_context(llm_ctx_obj)
        except Exception:
            logger.warning("authoritative_assembly: commerce context failed for user %s", user_id, exc_info=True)

    # Fan knowledge / LTM snapshots (bounded, for rendering/ranking reuse without refetch)
    fan_know_snapshot: tuple[dict[str, Any], ...] = ()
    ltm_snapshot: tuple[dict[str, Any], ...] = ()
    # We do NOT fetch fan_knowledge here by default; gatherers will retrieve relevance-ranked.
    # But we populate empty snapshot to satisfy type; retrieval happens inside ContextEngine gatherers
    # which are now snapshot-aware and will reuse if provided. Keeping empty avoids double fetch.
    # If needed we could eagerly fetch limit5 for snapshot, but defer to gatherer to preserve hybrid logic.

    # Phase 5 single authority: derive relationship_state from real purchase history (AUTHORITATIVE → DERIVED)
    # Phase 2.4: via the single lifecycle owner; purchase unknowns are explicit
    # (None → degraded tag), never zeros-as-truth.
    relationship_state: str | None = None
    has_active_offer: bool = False
    purchase_context: dict[str, Any] = {}
    _rel_degraded = False
    try:
        if creator_id is not None:
            from commerce.dao import get_behavioral_feedback_context, get_timing_context
            from core.conversation_state import derive_lifecycle_state as _lifecycle_owner

            # Real purchase history (fail-open 0)
            _purchase_known = True
            try:
                beh = await get_behavioral_feedback_context(creator_id, user_id)
                purchase_count = int(beh.get("total_purchases", 0))
            except Exception:
                purchase_count = 0
                _purchase_known = False
                beh = {}
            # Real has_active_offer (fail-open False)
            try:
                from db.postgres import get_pool as _get_pool_rel

                _pool = await get_pool()
                async with _pool.acquire() as _conn:
                    _row = await _conn.fetchrow(
                        "SELECT 1 FROM commerce_offers WHERE creator_id=$1 AND user_id=$2 AND state IN ('pending','clicked') LIMIT 1",
                        creator_id,
                        user_id,
                    )
                    has_active_offer = _row is not None
            except Exception:
                has_active_offer = False
                _purchase_known = False
            # last_purchase_days from timing
            last_purchase_days = None
            try:
                _timing = await get_timing_context(creator_id, user_id)
                hlp = _timing.get("hours_since_last_purchase")
                if hlp is not None:
                    last_purchase_days = hlp / 24.0
            except Exception:
                last_purchase_days = None
            # last_message_days from recent_trimmed
            last_message_days = None
            if recent_trimmed:
                try:
                    last_msg = recent_trimmed[-1]
                    last_at = last_msg.get("created_at") or last_msg.get("timestamp")
                    if last_at:
                        if isinstance(last_at, str):
                            last_at = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
                        if hasattr(last_at, "tzinfo") and last_at.tzinfo is None:
                            last_at = last_at.replace(tzinfo=UTC)
                        last_message_days = (datetime.now(UTC) - last_at).total_seconds() / 86400
                except Exception:
                    last_message_days = None
            try:
                _life_auth = _lifecycle_owner(
                    funnel_stage=user.get("funnel_stage", "new"),
                    purchase_count=purchase_count if _purchase_known else None,
                    last_purchase_days_ago=last_purchase_days,
                    last_message_days_ago=last_message_days,
                    message_count=int(user.get("message_count", 0)),
                    has_active_offer=has_active_offer if _purchase_known else None,
                )
                relationship_state = _life_auth.relationship_state
                if _life_auth.degraded or not _purchase_known:
                    _rel_degraded = True
                purchase_context = {
                    "total_purchases": purchase_count,
                    "has_active_offer": has_active_offer,
                    "last_purchase_days_ago": last_purchase_days,
                    "last_message_days_ago": last_message_days,
                }
            except Exception:
                logger.debug("authoritative relationship derivation failed", exc_info=True)
    except Exception:
        pass

    author_state = AuthoritativeState(
        creator_id=creator_id,
        user_id=user_id,
        generation_id=generation_id,
        current_message=current_message,
        timestamp=time.time(),
        user=user if isinstance(user, dict) else {},
        profile=profile if isinstance(profile, dict) else {},
        recent_messages=tuple(recent_trimmed),
        summary=summary,
        summary_age_days=summary_age_days,
        persona=persona_text,
        structured_persona=structured_for_name,
        persona_name=persona_name,
        persona_id=persona_id,
        persona_version=persona_version,
        conversation_state=conversation_state,
        conversation_state_dict=conversation_state_dict,
        commerce_context_text=commerce_context_text,
        llm_context=llm_ctx_obj,
        fan_knowledge_snapshot=fan_know_snapshot,
        long_term_memories_snapshot=ltm_snapshot,
        relationship_state=relationship_state,
        has_active_offer=has_active_offer,
        purchase_context=purchase_context,
        metadata={
            "acquisition_ms": int((time.monotonic() - start) * 1000),
            "recent_raw_count": len(recent),
            "recent_trimmed_count": len(recent_trimmed),
            # Phase 2.2: history degraded tag (ERROR-sourced empties, not silent)
            # Phase 2.4: relationship purchase-unknown also degrades the turn.
            "history_degraded": bool(history_degraded or _rel_degraded),
            "history_sources": dict(history_sources),
        },
    )
    # Phase 89: derive participants + contract from the frozen snapshot (no extra DB)
    try:
        from core.conversation_contract import derive_participants, derive_contract

        participants = derive_participants(author_state)
        contract = derive_contract(author_state, participants)
        # frozen dataclass → use object.__setattr__
        object.__setattr__(author_state, "participants", participants)
        object.__setattr__(author_state, "conversation_contract", contract)
    except Exception:
        # fail-open: leave as None, do not break message processing
        pass
    return author_state
