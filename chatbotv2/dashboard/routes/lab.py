"""Conversation Lab — Phase 90.

Reuses canonical AuthoritativeState → Context Engine → OneCall → llama.cpp
but disables delivery/PPV/payment. Synthetic history by default.
"""
from fastapi import APIRouter, Depends, Body
from fastapi.responses import JSONResponse
import uuid
import asyncio

from chatbotv2.dashboard.auth import require_auth

router = APIRouter()

# Phase 90: bounded concurrency for Lab — max 2 concurrent evaluations per process
lab_semaphore = asyncio.Semaphore(2)

async def _single_lab_turn(
    *,
    creator_id: int,
    persona_id,
    fan_user_id: int,
    fan_first_name: str,
    fan_message: str,
    norm_history: list[dict],
    structured: dict | None,
    persona_text: str,
):
    """Execute a single Lab turn — deterministic, no delivery, no PPV, one generation with timeout."""
    from context_engine.models import AuthoritativeState
    from core.conversation_state import derive_conversation_state
    from core.conversation_contract import derive_participants, derive_contract, render_participants_block, render_contract_block

    # Lab turn: synthetic evaluation origin, never Telegram-derived.
    # manual:<uuid> cannot collide with MD5(user:content:tgId) turns.
    from core.generation import manual_generation_id

    generation_id = manual_generation_id(f"lab:{fan_user_id}:{uuid.uuid4().hex}")

    synthetic_user = {"first_name": fan_first_name, "funnel_stage": "new", "message_count": len(norm_history)}
    synthetic_profile: dict = {}
    hist_for_state = list(norm_history) + [{"direction": "inbound", "content": fan_message}]
    conv_state = derive_conversation_state(hist_for_state, user=synthetic_user)

    # persona name
    persona_name_val = None
    if structured and isinstance(structured, dict):
        ident = structured.get("identity") or {}
        if isinstance(ident, dict) and ident.get("name"):
            persona_name_val = ident["name"]
    if not persona_name_val:
        try:
            from db.postgres import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                if persona_id:
                    # M7 (B7): creator-scoped read — a foreign creator's
                    # persona is indistinguishable from missing (None), never
                    # leaked. Global-default (creator_id IS NULL) personas
                    # remain usable as the documented exception.
                    row = await conn.fetchrow("SELECT instructions FROM personas WHERE id=$1 AND (creator_id=$2 OR creator_id IS NULL)", int(persona_id), creator_id)
                else:
                    row = await conn.fetchrow("SELECT instructions FROM personas WHERE creator_id=$1 ORDER BY is_default DESC LIMIT 1", creator_id)
                if row and row["instructions"]:
                    _pt = row["instructions"]
                    import re
                    m = re.search(r"You are\s+([A-Za-z ]+?)(?:\s*[—. ]|$)", _pt)
                    if m:
                        persona_name_val = m.group(1).strip()
                    if not persona_text:
                        persona_text = _pt
        except Exception:
            pass
    if not persona_name_val:
        persona_name_val = "Sunny Skye"
    if not persona_text:
        try:
            from db.postgres import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                if persona_id:
                    # M7 (B7): same creator-scoped predicate as above.
                    row = await conn.fetchrow("SELECT instructions FROM personas WHERE id=$1 AND (creator_id=$2 OR creator_id IS NULL)", int(persona_id), creator_id)
                else:
                    row = await conn.fetchrow("SELECT instructions FROM personas WHERE creator_id=$1 ORDER BY is_default DESC LIMIT 1", creator_id)
                if row:
                    persona_text = row["instructions"] or ""
        except Exception:
            persona_text = ""

    author_state = AuthoritativeState(
        creator_id=creator_id,
        user_id=fan_user_id,
        generation_id=generation_id,
        current_message=fan_message,
        user=synthetic_user,
        profile=synthetic_profile,
        recent_messages=tuple(norm_history),
        persona=persona_text or "",
        structured_persona=structured,
        persona_name=persona_name_val,
        persona_id=int(persona_id) if persona_id is not None else None,
        persona_version=int(structured.get("_persona_version") or structured.get("persona_version") or 1) if isinstance(structured, dict) and structured else None,
        conversation_state=conv_state,
        conversation_state_dict=conv_state.__dict__ if hasattr(conv_state, "__dict__") else None,
    )
    participants = derive_participants(author_state)
    contract = derive_contract(author_state, participants)
    try:
        object.__setattr__(author_state, "participants", participants)
        object.__setattr__(author_state, "conversation_contract", contract)
    except Exception:
        pass

    pipeline_result = None
    # OneCall generation — exactly 1 generative call, no PPV, no delivery, with 30s timeout
    from core.one_call_pipeline import one_call_generation
    try:
        # Use wait_for for bounded Lab turn timeout (does not alter production timeout)
        result = await asyncio.wait_for(
            one_call_generation(
                user_id=fan_user_id,
                creator_id=creator_id,
                user_message=fan_message,
                persona=persona_text or f"You are {persona_name_val}",
                profile=synthetic_profile,
                user=synthetic_user,
                persona_name=persona_name_val,
                conversation_state=conv_state,
                recent_messages=[{"direction": m["direction"], "content": m["content"]} for m in norm_history],
                retrieved_context="",
                authoritative_state=author_state,
                generation_id=generation_id,
            ),
            timeout=30,
        )
    except asyncio.TimeoutError:
        # Fail cleanly, never trigger delivery/PPV
        return {
            "error": "Lab turn timeout",
            "generation_id": generation_id,
            "llm_call_count": 1,
            "model": "default",
            "provider": "llamacpp",
        }, None, None, None, None, None, None

    # Build blocks
    try:
        participants_block = render_participants_block(participants) if participants else ""
        contract_block = render_contract_block(contract) if contract else ""
    except Exception:
        participants_block = ""
        contract_block = ""

    quality_flags = list(getattr(result, "quality_flags", []) or [])
    speaker_correct = "speaker_inversion" not in quality_flags and "character_as_player_inversion" not in quality_flags
    character_correct = speaker_correct
    player_agency_preserved = "unauthorized_player_speech" not in quality_flags and "player_agency_violation" not in quality_flags
    out_of_character = "out_of_character" in quality_flags
    question_answered = None
    topic_continuous = None
    if contract and getattr(contract, "answer_required", False):
        question_answered = "unanswered_question" not in quality_flags
    if contract and getattr(contract, "maintain_topic", False):
        topic_continuous = "topic_pivot" not in quality_flags

    turn_result = {
        "reply": result.reply,
        "persona": {"name": persona_name_val, "id": persona_id, "version": getattr(author_state, "persona_version", None)},
        "roleplay": {
            "character": getattr(participants, "character_name", None) if participants else persona_name_val,
            "player": getattr(participants, "player_name", None) if participants else fan_first_name,
            "current_turn_owner": getattr(contract, "current_turn_owner", "player") if contract else "player",
            "response_owner": getattr(contract, "response_owner", "character") if contract else "character",
        },
        "participants": {
            "speaker_name": getattr(participants, "speaker_name", None) if participants else None,
            "listener_name": getattr(participants, "listener_name", None) if participants else None,
            "speaker_role": getattr(participants, "speaker_role", None) if participants else None,
            "listener_role": getattr(participants, "listener_role", None) if participants else None,
            "character_name": getattr(participants, "character_name", None) if participants else persona_name_val,
            "player_name": getattr(participants, "player_name", None) if participants else fan_first_name,
            "character_role": getattr(participants, "character_role", None) if participants else "creator_persona",
            "player_role": getattr(participants, "player_role", None) if participants else "fan",
            "rendered": participants_block,
        },
        "contract": {
            "character_name": getattr(contract, "character_name", None) if contract else persona_name_val,
            "player_name": getattr(contract, "player_name", None) if contract else fan_first_name,
            "character_role": getattr(contract, "character_role", None) if contract else "creator_persona",
            "player_role": getattr(contract, "player_role", None) if contract else "fan",
            "current_turn_owner": getattr(contract, "current_turn_owner", "player") if contract else "player",
            "response_owner": getattr(contract, "response_owner", "character") if contract else "character",
            "current_intent": getattr(contract, "current_intent", None) if contract else None,
            "question_target": getattr(contract, "question_target", None) if contract else None,
            "answer_required": getattr(contract, "answer_required", None) if contract else None,
            "current_topic": getattr(contract, "current_topic", None) if contract else None,
            "maintain_topic": getattr(contract, "maintain_topic", None) if contract else None,
            "last_question": getattr(contract, "last_question", None) if contract else None,
            "last_question_answered": getattr(contract, "last_question_answered", None) if contract else None,
            "rendered": contract_block,
        },
        "validation": {
            "is_valid": bool(getattr(result, "is_valid", False)),
            "validation_error": getattr(result, "validation_error", None),
            "quality_score": getattr(result, "quality_score", None),
            "quality_flags": quality_flags,
            "quality_signals": quality_flags,
            "safety_flags": list(getattr(result, "safety_flags", []) or []),
            "speaker_correct": speaker_correct,
            "character_correct": character_correct,
            "player_agency_preserved": player_agency_preserved,
            "question_answered": question_answered,
            "topic_continuous": topic_continuous,
            "out_of_character": out_of_character,
            "roleplay_validation_flags": quality_flags,
        },
        "quality_signals": quality_flags,
        "generation_id": generation_id,
        "model": getattr(result, "model_name", "default"),
        "provider": getattr(result, "provider_name", "llamacpp"),
        "llm_call_count": 1,
        "total_llm_calls": 1,
        "ppv": False,
    }
    return turn_result, author_state, participants, contract, persona_name_val, fan_first_name, result

@router.post("/api/lab/execute")
async def api_lab_execute(
    payload: dict = Body(...),
    auth: dict = Depends(require_auth),
):
    """
    Single-turn and multi-turn Lab — bounded concurrency 2, sequential turns, no gather, no delivery.
    """
    async with lab_semaphore:
        creator_id = payload.get("creator_id")
        fan_message = payload.get("fan_message") or payload.get("current_message") or ""
        history = payload.get("history") or payload.get("recent_messages") or []
        persona_id = payload.get("persona_id")
        fan_first_name = payload.get("fan_first_name") or "Fan"
        fan_user_id = payload.get("fan_user_id") or 999999001
        turns = payload.get("turns") or payload.get("fan_messages") or None

        is_multi = isinstance(turns, list) and len(turns) > 0

        if creator_id is None:
            return JSONResponse({"error": "creator_id required"}, status_code=400)
        if is_multi:
            norm_turns: list[str] = []
            for t in turns:
                if isinstance(t, dict):
                    txt = t.get("content") or t.get("text") or t.get("fan_message") or ""
                    norm_turns.append(str(txt))
                elif isinstance(t, str):
                    norm_turns.append(t)
                else:
                    norm_turns.append(str(t))
            norm_turns = [s for s in norm_turns if s.strip()]
            if not norm_turns:
                return JSONResponse({"error": "turns required"}, status_code=400)
            # M7 (B7): bound evaluation cost (additive truncation flag).
            _lab_truncated = False
            if len(norm_turns) > 10:
                norm_turns = norm_turns[:10]
                _lab_truncated = True
        else:
            _lab_truncated = False
            if not fan_message or not fan_message.strip():
                return JSONResponse({"error": "fan_message required"}, status_code=400)

        try:
            creator_id = int(creator_id)
            fan_user_id = int(fan_user_id)
        except Exception:
            return JSONResponse({"error": "invalid creator_id/fan_user_id"}, status_code=400)

        # M7 (B7): pin creator selection to the same authority used by send
        # paths. When the single-creator resolver is READY, the requested
        # creator must match it; otherwise the requested creator must at
        # least have an integration record. There is no multi-creator admin
        # model — anything else is rejected, never evaluated.
        try:
            from commerce.single_creator import (
                resolve_single_application_creator,
                SingleCreatorStatus,
            )

            _sctx = await resolve_single_application_creator()
            if _sctx.status == SingleCreatorStatus.READY and _sctx.creator_id is not None:
                if int(_sctx.creator_id) != int(creator_id):
                    return JSONResponse({"error": "creator not authorized"}, status_code=403)
            else:
                from db import fangate as _lab_fdb

                _creator_row = await _lab_fdb.get_creator(creator_id)
                if not _creator_row:
                    return JSONResponse({"error": "creator not found"}, status_code=404)
        except Exception as _creator_check_exc:
            # Fail closed on resolver/lookup errors (but preserve explicit
            # JSON error responses above, which return directly).
            return JSONResponse({"error": "creator validation failed"}, status_code=403)

        # M7 (B7): reject foreign personas up front (404, indistinguishable
        # from missing — same convention as catalog reads). Global-default
        # personas (creator_id IS NULL) remain usable.
        if persona_id is not None:
            try:
                from db.postgres import get_pool as _lab_pool_fn

                _lab_pool = await _lab_pool_fn()
                async with _lab_pool.acquire() as _lab_conn:
                    _prow = await _lab_conn.fetchrow(
                        "SELECT id FROM personas WHERE id=$1 AND (creator_id=$2 OR creator_id IS NULL)",
                        int(persona_id),
                        creator_id,
                    )
                if _prow is None:
                    return JSONResponse({"error": "persona not found"}, status_code=404)
            except Exception:
                return JSONResponse({"error": "persona validation failed"}, status_code=403)

        if not isinstance(history, list):
            history = []
        norm_history: list[dict] = []
        for item in history[:20]:
            if isinstance(item, dict):
                direction = item.get("direction") or item.get("role") or "inbound"
                if direction == "user":
                    direction = "inbound"
                elif direction == "assistant":
                    direction = "outbound"
                content = item.get("content") or item.get("text") or ""
                norm_history.append({"direction": direction, "content": str(content)})
            elif isinstance(item, str):
                norm_history.append({"direction": "inbound", "content": item})

        # Fetch structured persona once (same for all turns)
        structured = None
        persona_text = ""
        try:
            from memory.creator_persona import get_structured_persona_async
            if persona_id is not None:
                structured = await get_structured_persona_async(creator_id=creator_id, persona_id=int(persona_id))
            else:
                structured = await get_structured_persona_async(creator_id=creator_id)
            if not isinstance(structured, dict) or not structured:
                structured = None
        except Exception:
            structured = None
        # Persona text fallback (fetch once)
        try:
            from db.postgres import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                if persona_id:
                    # M7 (B7): creator-scoped (see _single_lab_turn).
                    row = await conn.fetchrow("SELECT instructions FROM personas WHERE id=$1 AND (creator_id=$2 OR creator_id IS NULL)", int(persona_id), creator_id)
                else:
                    row = await conn.fetchrow("SELECT instructions FROM personas WHERE creator_id=$1 ORDER BY is_default DESC LIMIT 1", creator_id)
                if row and row["instructions"]:
                    persona_text = row["instructions"]
        except Exception:
            pass

        if is_multi:
            # Multi-turn sequential — each turn exactly one generation, no gather, no PPV/delivery
            # History accumulates: turn1 fan->character, turn2 fan->character, etc.
            results: list[dict] = []
            cur_history = list(norm_history)
            for turn_msg in norm_turns:
                turn_res, author_state, participants, contract, persona_name_val, fan_first_name_tmp, result = await _single_lab_turn(
                    creator_id=creator_id,
                    persona_id=persona_id,
                    fan_user_id=fan_user_id,
                    fan_first_name=fan_first_name,
                    fan_message=turn_msg,
                    norm_history=cur_history,
                    structured=structured,
                    persona_text=persona_text,
                )
                # Handle timeout case where turn_res contains error
                if isinstance(turn_res, dict) and "error" in turn_res and "reply" not in turn_res:
                    results.append({**turn_res, "fan_message": turn_msg, "history_length": len(cur_history)})
                    # On timeout, do not append to history (failed turn)
                    continue
                results.append({**turn_res, "fan_message": turn_msg})
                # Append to history for next turn: inbound fan + outbound character
                cur_history.append({"direction": "inbound", "content": turn_msg})
                # result.reply may be empty on error, handle
                reply_text = turn_res.get("reply", "") if isinstance(turn_res, dict) else ""
                cur_history.append({"direction": "outbound", "content": reply_text})
                cur_history = cur_history[-20:]  # keep bounded

            # Multi-turn response contract
            last = results[-1] if results else {}
            # M7 (B7): durable lab-selection record (actor + creator + persona
            # + bounded turn count). Best-effort; evaluation never depends on it.
            try:
                from core.audit import actor_from_auth, record_audit_event

                await record_audit_event(
                    event_type="attempt",
                    actor=actor_from_auth(auth),
                    creator_id=creator_id,
                    user_id=fan_user_id,
                    action="POST /api/lab/execute",
                    state_before=None,
                    state_after="evaluated",
                    result="lab_evaluated",
                    attempt=len(results),
                    error="truncated" if _lab_truncated else None,
                )
            except Exception:
                pass
            return JSONResponse({
                "turns": results,
                "turn_count": len(results),
                "truncated": _lab_truncated,
                "history": cur_history,
                "persona": last.get("persona") if last else {"name": "Sunny Skye"},
                "roleplay": last.get("roleplay") if last else None,
                "participants": last.get("participants") if last else None,
                "contract": last.get("contract") if last else None,
                "validation": last.get("validation") if last else None,
                "quality_signals": last.get("quality_signals") if last else [],
                "reply": last.get("reply") if last else "",
                "generation_id": last.get("generation_id") if last else None,
                "model": last.get("model", "default") if last else "default",
                "provider": last.get("provider", "llamacpp") if last else "llamacpp",
                "llm_call_count": 1,
                "total_llm_calls": len(results),
                "total_generations": len(results),
                "ppv": False,
                "sequential": True,
                "bounded_concurrency": True,
            })

        # Single-turn path (existing, now also under semaphore and with timeout via helper)
        # Reuse _single_lab_turn for consistency (also timeout)
        turn_res, author_state, participants, contract, persona_name_val, fan_first_name_tmp, result = await _single_lab_turn(
            creator_id=creator_id,
            persona_id=persona_id,
            fan_user_id=fan_user_id,
            fan_first_name=fan_first_name,
            fan_message=fan_message,
            norm_history=norm_history,
            structured=structured,
            persona_text=persona_text,
        )
        # Handle timeout sentinel
        if isinstance(turn_res, dict) and "error" in turn_res and "reply" not in turn_res:
            return JSONResponse({"error": turn_res["error"], "generation_id": turn_res.get("generation_id")}, status_code=504)
        # M7 (B7): durable lab-selection record for the single-turn path.
        try:
            from core.audit import actor_from_auth, record_audit_event

            await record_audit_event(
                event_type="attempt",
                actor=actor_from_auth(auth),
                creator_id=creator_id,
                user_id=fan_user_id,
                action="POST /api/lab/execute",
                state_before=None,
                state_after="evaluated",
                result="lab_evaluated",
                attempt=1,
            )
        except Exception:
            pass
        # _single_lab_turn already returns full single-turn JSON, just return it
        return JSONResponse(turn_res)

@router.get("/api/lab/health")
async def api_lab_health(auth: dict = Depends(require_auth)):
    return JSONResponse({"ok": True, "lab": "ready", "model": "default"})
