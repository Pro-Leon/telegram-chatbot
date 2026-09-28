import argparse
import asyncio
import hashlib
import logging
import time as _time
import uuid
from datetime import datetime, timezone
from typing import Any

from commerce.pipeline import PIPELINE_MAX_MESSAGES
from commerce.selection import (
    CommerceSelectionResult,
    CommerceSelectionStatus,
    select_commerce_response,
)
from commerce.single_creator import (
    SingleCreatorStatus,
    resolve_single_application_creator,
)
from commerce.state import CommerceStateRequest
from core.config import get_settings
from core.llm_provider import get_llm_provider
from core.logging_config import setup_logging
from core.scoring import score_draft
from core.shutdown import is_shutting_down, setup_signal_handlers
from core.worker_heartbeat import write_heartbeat
from db.postgres import (
    add_to_operator_queue,
    get_recent_messages,
    init_pool,
    is_user_auto_reply_excluded,
    upsert_user,
)
from db.redis import (
    ack_inbound,
    acquire_user_lock,
    enqueue_send,
    ensure_consumer_group,
    is_auto_reply_enabled,
    move_to_dlq,
    read_inbound,
    release_user_lock,
    requeue_stalled_messages,
)
from memory.context import build_qwen3_context, current_message_in_history
from memory.profile import extract_and_update_profile
from memory.summarizer import maybe_summarize
from context_engine.authoritative_assembly import assemble_authoritative_context

logger = logging.getLogger("llm_worker")
_settings = get_settings()


class UserLockContentionError(RuntimeError):
    """Raised when a user lock cannot be acquired due to contention.

    This is a transient, retryable condition and must NOT be treated as
    successful processing. The queue consumer must not ACK the message
    when this is raised; the message should remain pending for XAUTOCLAIM
    retry.
    """


async def generate_draft(
    context_messages: list[dict],
    user_message: str,
    model: str | None = None,
) -> str:
    system_parts: list[str] = []
    messages: list[dict[str, str]] = []
    for m in context_messages:
        role = m.get("role")
        text = m.get("content", "")
        if role == "system":
            system_parts.append(text)
        elif role in ("user", "model", "assistant"):
            messages.append({"role": role, "content": text})
    # M3 exactly-once: history already contains the persisted current inbound
    # in the canonical flow — append only when it is genuinely absent (empty
    # history, trimmed window, or redelivered turn outside the window).
    if not current_message_in_history(context_messages, user_message):
        messages.append({"role": "user", "content": user_message})

    merged_system = "\n\n".join(p for p in system_parts if p)

    # Sole provider (llama.cpp): single attempt, no iteration/fallback.
    # Prompt, output contract, top_p, and failure semantics preserved:
    # any failure logs and returns "" so the worker follows the existing
    # DLQ/requeue/operator recovery path.
    provider = get_llm_provider()
    try:
        return await provider.generate_with_history(
            system_instruction=merged_system,
            messages=messages,
            model=model,
            max_output_tokens=_settings.max_tokens,
            temperature=_settings.temperature,
            top_p=0.95,
        )
    except Exception:
        logger.exception("generate_draft failed (llamacpp)")
        return ""


# NOTE: Gemini-SDK-native generate_draft_with_tools removed atomically with
# its Gemini imports (types/gtypes, client, tool declarations, quota
# preamble). supports_tool_calling() == False for llama.cpp and the tools
# path was legacy/inert; no llama.cpp tool replacement is introduced.


async def _get_canonical_commerce_evaluation(
    user_id: int,
    context: list[dict],
    persona: str,
    signals: Any | None = None,
    conversation_state: Any | None = None,
    user_message: str | None = None,
    opportunity_result: Any | None = None,
) -> tuple[Any | None, Any | None]:
    """Single authoritative commerce decision for one inbound message (P1.2).

    Evaluates product selection, state resolution, and the deterministic
    decision exactly once. Returns (decision, pipeline_request) where
    decision is the canonical CommerceDecision and pipeline_request is the
    resolved CommercePipelineRequest (or None if not ready). This is the
    ONLY place where an authoritative decision is computed per message;
    downstream consumers must reuse the returned decision rather than
    recomputing.

    P0 preservation: user_message is threaded for deterministic gating.
    P1.1 preservation: no lock handling here.

    Returns (None, None) when not ready or on any failure (free may proceed).
    """
    # P3.3.14.2 quarantine: legacy selector removed. Opportunity Engine is sole
    # commercial-opportunity authority. This function is now advisory-only and
    # must not authorize legacy PPV.
    if not _settings.autonomy_enabled:
        return None, None
    if signals is None:
        return None, None
    try:
        # P3.3.14.3: single evaluation per message — reuse if supplied
        if opportunity_result is not None:
            if getattr(opportunity_result, "has_opportunity", False):
                sel = getattr(opportunity_result, "selected_candidate", None)
                logger.info(
                    "Opportunity engine has opportunity creator=%s user=%s def=%s (quarantined, no legacy PPV) [reused]",
                    getattr(opportunity_result, "creator_id", None),
                    user_id,
                    getattr(sel, "definition_id", None) if sel else None,
                )
            return None, None
        from commerce.opportunity_engine import evaluate_opportunity

        creator = await resolve_single_application_creator()
        if creator.status is not SingleCreatorStatus.READY or creator.creator_id is None:
            return None, None
        # Establish deterministic ConversationState
        cs = conversation_state
        if cs is None:
            try:
                from core.conversation_state import derive_conversation_state as _derive_cs

                cs = _derive_cs(context)
            except Exception:
                cs = None
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        try:
            result = await evaluate_opportunity(creator.creator_id, user_id, cs, now)
        except Exception:
            logger.debug(
                "Opportunity engine evaluation failed for user=%s (fail-closed)",
                user_id,
                exc_info=True,
            )
            return None, None
        if result.has_opportunity:
            logger.info(
                "Opportunity engine has opportunity creator=%s user=%s def=%s (quarantined, no legacy PPV)",
                creator.creator_id,
                user_id,
                result.selected_candidate.definition_id if result.selected_candidate else None,
            )
        # LEGACY QUARANTINE: never return a legacy CommerceDecision that could
        # authorize OFFER_PPV. Caller will treat (None,None) as no PPV.
        return None, None
    except Exception:
        logger.debug("Canonical commerce evaluation failed for user=%s", user_id, exc_info=True)
        return None, None


async def _is_commerce_ppv_authorized_for_precedence(
    user_id: int,
    context: list[dict],
    persona: str,
    signals: Any | None = None,
    conversation_state: Any | None = None,
    user_message: str | None = None,
    canonical_decision: Any | None = None,
    opportunity_result: Any | None = None,
) -> bool:
    """Decision-only PPV authorization check for free-photo precedence.

    P1.2 convergence: when *canonical_decision* is supplied it is the
    single per-message authoritative decision reused without recomputation.
    Downstream consumers must pass the canonical decision; otherwise (legacy
    / tests) a fresh decision is evaluated.

    Pure decision evaluation — does NOT create offers, call DropFans, verify
    live price, generate commerce response, or mutate state. Reuses the same
    deterministic product selection and state resolution as the full commerce
    path, but stops after ``decide_commerce_action``.

    Returns True only when ``action == OFFER_PPV and allowed == True``.
    Any failure / non-ready state returns False (free-photo may proceed).
    """
    # P1.2: reuse canonical decision when supplied — quarantined: legacy OFFER_PPV
    # no longer authorizes precedence. Return False to prevent legacy PPV win.
    if canonical_decision is not None:
        return False
    if not _settings.autonomy_enabled:
        return False
    if signals is None:
        return False
    try:
        # P3.3.14.3: single evaluation — reuse if supplied
        if opportunity_result is not None:
            if getattr(opportunity_result, "has_opportunity", False):
                logger.info(
                    "Opportunity engine has opportunity creator=%s user=%s (quarantined, precedence suppressed) [reused]",
                    getattr(opportunity_result, "creator_id", None),
                    user_id,
                )
            return False
        from commerce.opportunity_engine import evaluate_opportunity

        creator = await resolve_single_application_creator()
        if creator.status is not SingleCreatorStatus.READY or creator.creator_id is None:
            return False
        cs = conversation_state
        if cs is None:
            try:
                from core.conversation_state import derive_conversation_state as _derive_cs

                cs = _derive_cs(context)
            except Exception:
                cs = None
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        try:
            result = await evaluate_opportunity(creator.creator_id, user_id, cs, now)
        except Exception:
            logger.debug(
                "Opportunity engine evaluation failed for user=%s (fail-closed)",
                user_id,
                exc_info=True,
            )
            return False
        if result.has_opportunity:
            logger.info(
                "Opportunity engine has opportunity creator=%s user=%s (quarantined, precedence suppressed)",
                creator.creator_id,
                user_id,
            )
        return False
    except Exception:
        logger.debug(
            "PPV precedence check failed for user=%s (free-photo may proceed)",
            user_id,
            exc_info=True,
        )
        return False


async def _try_commerce_draft(
    user_id: int,
    context: list[dict],
    persona: str,
    signals: Any | None = None,
    conversation_state: Any | None = None,
    user_message: str | None = None,
    canonical_decision: Any | None = None,
    canonical_request: Any | None = None,
    opportunity_result: Any | None = None,
) -> CommerceSelectionResult | None:
    """Attempt the commerce draft-generation path at most once per message.

    Worker-local, thin adapter. It builds a ``CommerceStateRequest`` from data
    the worker actually holds (user_id, the existing ``build_context`` output,
    the resolved persona), runs the sealed 6D boundary
    (``resolve_and_run_commerce``) and asks the 6E boundary
    (``select_commerce_response``) which draft to use. No commerce rules live
    here; the commerce package remains the authority.

    Product selection is deterministic: when the single creator resolves,
    products are resolved considering purchase history. With one valid product
    it is selected directly. With multiple valid products, already-purchased
    ones are excluded — if exactly one unpurchased product remains it is
    selected; otherwise the selection is ambiguous and product_id stays None.

    Guarantees:

    - at most ONE commerce attempt per inbound message
    - never raises: any failure degrades to ``None`` so the caller keeps the
      existing ``generate_draft()`` fallback
    - never executes, enqueues, sends, or publishes anything itself
    - never invents product/currency/age/cooldown state: the request carries
      only ``user_id``, the existing context (bounded to the sealed request
      contract, latest kept), the existing persona, and — when the single
      active creator resolves — that creator's ``creator_id`` from the
      authoritative creator_integrations source; conversation text never
      influences creator or product resolution
    - logs only bounded metadata (user_id, selection status/reason,
      single-creator resolution status)
    """
    if not _settings.autonomy_enabled:
        logger.info(
            "autonomy_disabled: commerce attempt skipped for user=%s (autonomy_enabled=false)",
            user_id,
        )
        return None

    # P3.3.14.2 quarantine: canonical legacy path removed. Opportunity Engine
    # is now sole opportunity authority; no legacy pipeline execution here.
    # Even when canonical decision/request are supplied, do not run pipeline.
    if canonical_decision is not None and canonical_request is not None:
        logger.info("Commerce attempt skipped for user=%s (quarantined canonical path)", user_id)
        return None

    # P3.3.14.3: single evaluation — reuse if supplied
    if opportunity_result is not None:
        if getattr(opportunity_result, "has_opportunity", False):
            _sel = getattr(opportunity_result, "selected_candidate", None)
            logger.info(
                "Opportunity engine has opportunity creator=%s user=%s def=%s (quarantined, no PPV execution yet) [reused]",
                getattr(opportunity_result, "creator_id", None),
                user_id,
                getattr(_sel, "definition_id", None) if _sel else None,
            )
        else:
            logger.info(
                "Opportunity engine no opportunity for user=%s (normal conversation) [reused]",
                user_id,
            )
        return None
    # P3.3.14.2 quarantine: legacy autonomous selector removed.
    # Establish deterministic ConversationState and invoke Opportunity Engine.
    # Do NOT run legacy pipeline, do NOT create offer, do NOT call sealing yet.
    try:
        from commerce.opportunity_engine import evaluate_opportunity

        creator = await resolve_single_application_creator()
        if creator.status is not SingleCreatorStatus.READY or creator.creator_id is None:
            logger.info(
                "Single-creator resolution %s for user %s (engine path, no product selection)",
                creator.status.value if hasattr(creator.status, "value") else str(creator.status),
                user_id,
            )
            return None
        cs = conversation_state
        if cs is None:
            try:
                from core.conversation_state import derive_conversation_state as _derive_cs

                cs = _derive_cs(context)
            except Exception:
                cs = None
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        try:
            result = await evaluate_opportunity(creator.creator_id, user_id, cs, now)
        except Exception:
            logger.debug(
                "Opportunity engine evaluation failed for user=%s (fail-closed)",
                user_id,
                exc_info=True,
            )
            return None
        if result.has_opportunity:
            logger.info(
                "Opportunity engine has opportunity creator=%s user=%s def=%s (quarantined, no PPV execution yet)",
                creator.creator_id,
                user_id,
                result.selected_candidate.definition_id if result.selected_candidate else None,
            )
            # Preserve for next phase via telemetry/context if needed, but do NOT execute PPV.
            return None
        logger.info("Opportunity engine no opportunity for user=%s (normal conversation)", user_id)
        return None
    except Exception:  # noqa: BLE001 — commerce must never break normal messaging
        logger.warning("Commerce attempt skipped for user %s (safe fallback quarantined)", user_id)
        return None


async def notify_operators(
    queue_id: int,
    user_id: int,
    draft: str,
    score: float,
    flags: list[str],
) -> None:
    logger.info(
        "Operator queue item %s created for user %s (score=%.2f, flags=%s)",
        queue_id,
        user_id,
        score,
        flags,
    )


async def post_process(user_id: int, creator_id: int | None = None) -> None:
    # P1.3a: creator-scoped post-processing – fail closed if creator_id missing
    if creator_id is None:
        logger.warning(
            "post_process missing creator_id for user=%s – skipping (strict isolation)", user_id
        )
        return
    try:
        recent = await get_recent_messages(user_id, limit=20, creator_id=creator_id)
        # Use the actual user message count for summarization gating,
        # not len(recent) which is capped at 20.
        from db.postgres import get_user as _get_user_for_count

        user = await _get_user_for_count(user_id)
        message_count = user.get("message_count", 0) if user else len(recent)

        # M5: capture the source order (max messages.id over the extraction
        # window) at read time. Retries/replays re-read, so a replayed old
        # message honestly reflects whatever window it actually observed;
        # the atomic gate in extract_and_update_profile rejects only sources
        # strictly older than the already-applied state. No lock is held
        # around LLM work; freshness is enforced at the mutation boundary.
        _source_order: int | None = None
        try:
            _window = recent[-10:] if isinstance(recent, list) else []
            _ids = [
                m.get("id")
                for m in _window
                if isinstance(m, dict)
                and isinstance(m.get("id"), int)
                and not isinstance(m.get("id"), bool)
            ]
            if _ids:
                _source_order = max(_ids)
        except Exception:
            _source_order = None

        await extract_and_update_profile(
            user_id, recent, source_order=_source_order, creator_id=creator_id
        )
        # Phase 2.4: deterministic funnel progression (idempotent, background).
        # Never raises; terminal/converted stages untouched.
        try:
            from db.postgres import maybe_advance_funnel_stage as _advance_funnel

            await _advance_funnel(
                user_id,
                message_count,
                current_stage=(user or {}).get("funnel_stage") if isinstance(user, dict) else None,
            )
        except Exception:
            logger.debug("post_process funnel advance failed for user %s", user_id, exc_info=True)
        await maybe_summarize(
            user_id, message_count, creator_id=creator_id, source_count=message_count
        )
    except Exception:
        logger.exception("post_process failed for user %s", user_id)


async def _record_relationship_trajectory(
    *,
    user_id: int,
    creator_id: int | None,
    generation_id: str | None,
    user_message: str,
    recent_messages: Any | None = None,
    conversation_state: Any | None = None,
    conversation_contract: Any | None = None,
    lifecycle: str | None = None,
    profile: Any | None = None,
    current_topic: str | None = None,
    open_threads: Any | None = None,
    assistant_text: str | None = None,
    assistant_valid: bool = False,
    conversation_mode: str | None = None,
    llm_signals: Any | None = None,
) -> None:
    """Phase 2 relationship evidence path (fail-open, never raises).

    Accumulates exactly one ``RelationshipTurnEvidence`` per processed
    inbound turn (not per raw Telegram message: callers invoke this once
    per claimed inbound turn, after debounce collapsed rapid messages).

    * Single UTC ``now`` threads through extraction → accumulation →
      snapshot → persistence metadata.
    * Deterministic user-side evidence is recordable even when generation
      failed (``llm_signals`` absent/None, ``assistant_valid`` False).
    * Uses the real ``commerce.long_term_memory`` implementation directly;
      never the dead open-loop module path; never the contract's
      topic-maintenance flag.
    * No commerce/prompt/objective integration: the trajectory state is
      never passed into decision/ranking/sealing/execution/pricing, prompts,
      or objectives. Dependency is one-way: runtime evidence → Phase 1.
    """
    try:
        if creator_id is None:
            return
        try:
            _cid = int(creator_id)
            _uid = int(user_id)
        except Exception:
            return
        _gen = generation_id if isinstance(generation_id, str) and generation_id.strip() else ""
        if not _gen:
            return
        from datetime import datetime, timezone

        _now = datetime.now(timezone.utc)
        try:
            from commerce.relationship_evidence import (
                accumulate_relationship_turn_idempotent,
                extract_turn_evidence,
                has_explicit_fan_knowledge_hit,
                has_explicit_ltm_hit,
            )
        except Exception:
            logger.debug("relationship evidence imports unavailable (fail-open)", exc_info=True)
            return
        # Narrow disclosure inputs (deterministic explicit extractors only;
        # never post-hoc profile LLM deltas, never raw text persistence).
        try:
            _ltm_hit = has_explicit_ltm_hit(user_message, _cid, _uid)
        except Exception:
            _ltm_hit = False
        _existing_knowledge: list[dict[str, Any]] = []
        try:
            from commerce.fan_knowledge import get_fan_knowledge as _get_fk_existing

            _prof_dict = profile if isinstance(profile, dict) else None
            _existing_knowledge = await _get_fk_existing(_cid, _uid, profile=_prof_dict) or []
        except Exception:
            _existing_knowledge = []
        try:
            _fk_hit = has_explicit_fan_knowledge_hit(
                user_message, _cid, _uid, existing_knowledge=_existing_knowledge
            )
        except Exception:
            _fk_hit = False
        # Topic/thread inputs from state (fallback to explicit params).
        _topic = current_topic
        _threads: tuple[str, ...] = ()
        try:
            if _topic is None and conversation_state is not None:
                if isinstance(conversation_state, dict):
                    _topic = conversation_state.get("current_topic")
                else:
                    _topic = getattr(conversation_state, "current_topic", None)
            _th_raw = open_threads
            if _th_raw is None and conversation_state is not None:
                if isinstance(conversation_state, dict):
                    _th_raw = conversation_state.get("open_threads", ())
                else:
                    _th_raw = getattr(conversation_state, "open_threads", ())
            if isinstance(_th_raw, (list, tuple)):
                _threads = tuple(str(t) for t in _th_raw if isinstance(t, str))[:3]
        except Exception:
            _topic = current_topic
            _threads = ()
        # Existing retrieval evidence (fail-open; absent → False downstream).
        _retrieved_mems: list[dict[str, Any]] = []
        _retrieved_know: list[dict[str, Any]] = []
        _loop_continued = False
        try:
            from commerce.long_term_memory import retrieve_relevant_memories as _rrm

            _prof2 = profile if isinstance(profile, dict) else None
            _retrieved_mems = (
                await _rrm(
                    _cid,
                    _uid,
                    current_topic=_topic,
                    open_threads=_threads,
                    limit=5,
                    profile=_prof2,
                )
                or []
            )
        except Exception:
            _retrieved_mems = []
        try:
            from commerce.fan_knowledge import retrieve_relevant_knowledge as _rrk

            _prof3 = profile if isinstance(profile, dict) else None
            _retrieved_know = (
                await _rrk(
                    _cid,
                    _uid,
                    current_topic=_topic,
                    open_threads=_threads,
                    limit=5,
                    profile=_prof3,
                )
                or []
            )
        except Exception:
            _retrieved_know = []
        try:
            from commerce.long_term_memory import OPEN_LOOP as _OPEN_LOOP_TYPE

            for _m in _retrieved_mems:
                try:
                    if (
                        isinstance(_m, dict)
                        and _m.get("memory_type") == _OPEN_LOOP_TYPE
                        and _m.get("status", "OPEN") == "OPEN"
                    ):
                        _loop_continued = True
                        break
                except Exception:
                    continue
        except Exception:
            _loop_continued = False
        # Resolution via the REAL long_term_memory implementation (the
        # legacy ``commerce.open_loop`` module does not exist; the old
        # worker import is swallowed by its exception handler and is never
        # used here).
        _loop_resolved = False
        try:
            from commerce.long_term_memory import resolve_open_loop as _real_resolve

            _loop_resolved = bool(await _real_resolve(_cid, _uid, user_message or ""))
        except Exception:
            _loop_resolved = False
        # Lifecycle: explicit param wins, else state's field. ONLY the
        # existing ConversationLifecycle.RETURNING definition counts
        # (interpreted inside the extractor; no new timeout here).
        _lifecycle = lifecycle
        try:
            if _lifecycle is None and conversation_state is not None:
                if isinstance(conversation_state, dict):
                    _cand = conversation_state.get("lifecycle")
                else:
                    _cand = getattr(conversation_state, "lifecycle", None)
                if isinstance(_cand, str):
                    _lifecycle = _cand
        except Exception:
            pass
        # History for the prior-presence guard (DB recent without current
        # is the common shape; the extractor also tolerates trailing-current).
        _history: list[dict[str, Any]] = []
        try:
            if recent_messages is not None:
                _history = [m for m in list(recent_messages) if isinstance(m, dict)]
        except Exception:
            _history = []
        try:
            _evidence = extract_turn_evidence(
                user_message=user_message,
                history=_history,
                conversation_state=conversation_state,
                contract=conversation_contract,
                lifecycle=_lifecycle,
                ltm_explicit_hit=_ltm_hit,
                fan_knowledge_explicit_hit=_fk_hit,
                open_loop_continued=_loop_continued,
                open_loop_resolved=_loop_resolved,
                retrieved_memories=_retrieved_mems,
                retrieved_knowledge=_retrieved_know,
                assistant_text=assistant_text,
                assistant_valid=bool(assistant_valid),
                conversation_mode=conversation_mode,
                llm_signals=llm_signals,
                now=_now,
            )
        except Exception:
            logger.debug("relationship evidence extraction failed (fail-open)", exc_info=True)
            return
        try:
            await accumulate_relationship_turn_idempotent(
                user_id=_uid,
                creator_id=_cid,
                generation_id=_gen,
                evidence=_evidence,
                now=_now,
                source="relationship_evidence",
            )
        except Exception:
            logger.debug("relationship accumulation failed (fail-open)", exc_info=True)
            return
    except Exception:
        logger.debug("relationship trajectory path failed (fail-open)", exc_info=True)
        return


async def _record_intimacy_trajectory(
    *,
    user_id: int,
    creator_id: int | None,
    generation_id: str | None,
    user_message: str,
    recent_messages: Any | None = None,
    conversation_state: Any | None = None,
    assistant_text: str | None = None,
    assistant_valid: bool = False,
    llm_content_interest: Any | None = None,
    llm_explicit_content: Any | None = None,
) -> None:
    """Phase 6 intimacy evidence path (fail-open, never raises).

    Extracts exactly one ``IntimacyTurnEvidence`` for one processed
    inbound turn and accumulates it idempotently (once per
    ``generation_id``) into the creator-scoped intimacy namespace.
    Descriptive state only: no permission, consent, safety-policy, or
    commerce semantics are produced or consumed here. A single UTC
    ``now`` threads through extraction → accumulation.
    """
    try:
        if creator_id is None:
            return
        try:
            _cid = int(creator_id)
            _uid = int(user_id)
        except Exception:
            return
        _gen = generation_id if isinstance(generation_id, str) and generation_id.strip() else ""
        if not _gen:
            return
        from datetime import datetime, timezone

        _now = datetime.now(timezone.utc)
        try:
            from commerce.intimacy_evidence import (
                accumulate_intimacy_turn_idempotent,
                extract_intimacy_evidence,
            )
        except Exception:
            logger.debug("intimacy evidence imports unavailable (fail-open)", exc_info=True)
            return
        _history: list[dict[str, Any]] = []
        try:
            if recent_messages is not None:
                _history = [m for m in list(recent_messages) if isinstance(m, dict)]
        except Exception:
            _history = []
        try:
            _evidence = extract_intimacy_evidence(
                user_message=user_message,
                history=_history,
                conversation_state=conversation_state,
                assistant_text=assistant_text if assistant_valid else None,
                llm_content_interest=llm_content_interest,
                llm_explicit_content=llm_explicit_content,
                now=_now,
            )
        except Exception:
            logger.debug("intimacy evidence extraction failed (fail-open)", exc_info=True)
            return
        try:
            _g5_anchors, _g5_did = await accumulate_intimacy_turn_idempotent(
                user_id=_uid,
                creator_id=_cid,
                generation_id=_gen,
                evidence=_evidence,
                now=_now,
                source="intimacy_evidence",
            )
        except Exception:
            logger.debug("intimacy accumulation failed (fail-open)", exc_info=True)
            return
        # G5 evidence-ledger emission only (log; fail-open; no behavior change).
        # Per-dimension bands + active signals; no counters, timestamps, or text.
        try:
            from commerce.intimacy_evidence import (
                snapshot_for_accumulated as _g5_snapshot_for_accumulated,
            )

            _g5_snap = _g5_snapshot_for_accumulated(_g5_anchors, _evidence, _now)
            logger.info(
                "G5 intimacy_bands generation_id=%s romantic=%s playful=%s emotional=%s "
                "sexual_conversation=%s intimate_continuity=%s active_signals=%s accumulated=%s",
                _gen,
                getattr(
                    getattr(_g5_snap, "romantic", None),
                    "value",
                    getattr(_g5_snap, "romantic", None),
                ),
                getattr(
                    getattr(_g5_snap, "playful", None), "value", getattr(_g5_snap, "playful", None)
                ),
                getattr(
                    getattr(_g5_snap, "emotional", None),
                    "value",
                    getattr(_g5_snap, "emotional", None),
                ),
                getattr(
                    getattr(_g5_snap, "sexual_conversation", None),
                    "value",
                    getattr(_g5_snap, "sexual_conversation", None),
                ),
                getattr(
                    getattr(_g5_snap, "intimate_continuity", None),
                    "value",
                    getattr(_g5_snap, "intimate_continuity", None),
                ),
                getattr(_g5_snap, "active_signals_this_turn", None),
                bool(_g5_did),
            )
        except Exception:
            pass
    except Exception:
        logger.debug("intimacy trajectory path failed (fail-open)", exc_info=True)
        return


async def process_message(
    user_id: int,
    user_message: str,
    telegram_message_id: int,
    username: str,
    first_name: str,
    persona: str,
    generation_id: str | None = None,
    is_redelivery: bool = False,
    creator_id: int | None = None,
) -> None:
    # P1.4: creator_id from payload is authoritative – do not re-resolve if present
    from core.event_bus import publish_event, publish_events_batch
    from core.generation import ensure_generation_id, telegram_generation_id

    # Sunny V1 cutover (Phase 3): legacy conversational generation is DISABLED.
    # No draft, no score, no routing, no send enqueue, no operator queue insert,
    # no relationship/conversation state mutation from this path. Commerce
    # reconciliation/post-purchase paths do not enter through here.
    try:
        from core.architecture_router import is_v1_conversational_enabled, log_v1_suppressed

        if not is_v1_conversational_enabled():
            log_v1_suppressed("llm_worker.process_message", user_id=user_id)
            try:
                await publish_event(
                    "ai.generation_failed",
                    {"error": "v1_disabled"},
                    user_id=user_id,
                    dialog_id=user_id,
                    generation_id=generation_id,
                    creator_id=creator_id,
                    scope="user",
                )
            except Exception:
                pass
            return
    except Exception:
        logger.warning("V1 gate unreadable in process_message — failing closed")
        return

    # ── Generation ID (canonical helper) ─────────────────────────────────────
    # Existing valid generation_id from transport/payload is authoritative for
    # correlation; deterministic recomputation is only recovery when absent.
    generation_id = ensure_generation_id(
        generation_id,
        user_id=user_id,
        content=user_message,
        telegram_message_id=telegram_message_id,
    )
    if generation_id is None:
        # No usable correlation and no complete Telegram tuple (should not
        # happen for Telegram-derived turns); fall back to content hash so the
        # turn still has a stable local identifier without crashing.
        # Same MD5(user:content:tgId) algorithm via the canonical helper.
        generation_id = telegram_generation_id(
            int(user_id), str(user_message), int(telegram_message_id)
        )
    # Phase 87: inbound redelivery tracking (fail-open)
    try:
        if is_redelivery:
            _telemetry_data.inbound_redelivery_count = 1  # type: ignore
            _telemetry_data.delivery_status = "redelivery"  # type: ignore
    except Exception:
        pass

    # ── Telemetry ──────────────────────────────────────────────────────────
    from core.telemetry import get_telemetry_collector

    _telemetry = get_telemetry_collector()
    # Phase 87: runtime_mode reflects actual llm_path (new vs legacy); shadow/agent separate labels
    _runtime_mode_label = getattr(_settings, "llm_path", "legacy")
    if _runtime_mode_label not in ("new", "legacy"):
        _runtime_mode_label = "legacy"
    _telemetry_data = _telemetry.start_generation(
        user_id=user_id,
        creator_id=None,
        runtime_mode=_runtime_mode_label,
        generation_id=generation_id,
    )

    # ── Creator resolution (with lock propagation) ─────────────────────────
    # P1.4: payload creator_id is authoritative; only re-resolve if None (legacy compat)
    _creator_id: int | None = creator_id
    _creator_sales_enabled: bool = False
    if _creator_id is not None:
        # Validate authoritative creator's sales enabled, do not re-resolve to different creator
        try:
            from db import dropfans as _db_dropfans_check

            _integ = await _db_dropfans_check.get_dropfans_integration(_creator_id)
            _creator_sales_enabled = _integ is not None and _integ.get("status") == "active"
        except Exception:
            logger.debug("Creator sales check failed for creator=%s", _creator_id)
            _creator_sales_enabled = False
    else:
        # No authoritative creator (legacy caller) – try single-creator resolve
        try:
            from commerce.single_creator import (
                SingleCreatorStatus,
                resolve_single_application_creator,
            )

            _creator_ctx = await resolve_single_application_creator()
            if (
                _creator_ctx.status is SingleCreatorStatus.READY
                and _creator_ctx.creator_id is not None
            ):
                _creator_id = _creator_ctx.creator_id
                _creator_sales_enabled = True
        except Exception:
            logger.debug("Creator resolution failed for context enrichment (user=%s)", user_id)

    # Phase 87: sync creator_id into telemetry for correlation (creator+generation join)
    try:
        _telemetry_data.creator_id = _creator_id
        try:
            _telemetry._telemetry_cache.pop((_telemetry_data.creator_id, generation_id), None)
        except Exception:
            pass
        try:
            _telemetry._telemetry_cache[_telemetry._cache_key(generation_id, _creator_id)] = (
                _telemetry_data
            )
        except Exception:
            pass
    except Exception:
        pass
    # P1.4: fail closed if creator_id still missing – do not acquire global lock
    if _creator_id is None:
        logger.warning(
            "creator_id missing for user=%s generation=%s – failing closed, not acquiring global lock (P1.4)",
            user_id,
            generation_id,
        )
        try:
            await publish_event(
                "ai.generation_failed",
                {"error": "creator_context_unavailable"},
                user_id=user_id,
                dialog_id=user_id,
                generation_id=generation_id,
                creator_id=None,
                scope="user",
            )
        except Exception:
            pass
        return

    # Phase 1 creator-isolation fix: was lock:user:{user_id} global, now creator-scoped lock:creator:{creator_id}:user:{user_id}
    locked = await acquire_user_lock(user_id, ttl=_settings.user_lock_ttl, creator_id=_creator_id)
    if not locked:
        logger.info("User %s already locked, deferring (creator=%s)", user_id, _creator_id)
        raise UserLockContentionError(f"User {user_id} already locked, creator={_creator_id}")

    try:
        # ── User upsert ────────────────────────────────────────────────────
        _upsert_coro = upsert_user(user_id, username, first_name)
        _auto_reply_coro = is_user_auto_reply_excluded(user_id)
        _upsert_result, _auto_reply_excluded = await asyncio.gather(
            _upsert_coro, _auto_reply_coro, return_exceptions=True
        )
        if _auto_reply_excluded is True:
            # H6: exclusion still prevents any send, but must produce an
            # explicit deterministic suppressed outcome (started -> completed
            # with a stable suppression reason) instead of a silent return.
            # No operator queue insert, no Telegram send.
            logger.info("User %s is excluded from auto-reply, suppressing", user_id)
            _telemetry_data.routing_decision = "suppressed_excluded"
            _telemetry_data.handoff_reason = "do_not_auto_reply"  # type: ignore
            _telemetry_data.delivery_status = "suppressed"  # type: ignore
            try:
                await publish_event(
                    "ai.generation_started",
                    {
                        "message_length": len(user_message),
                        "has_content": bool(user_message and user_message.strip()),
                    },
                    user_id=user_id,
                    dialog_id=user_id,
                    generation_id=generation_id,
                    creator_id=_creator_id,
                    scope="user",
                )
            except Exception:
                pass
            try:
                await publish_event(
                    "ai.generation_completed",
                    {
                        "draft": "",
                        "score": 0.0,
                        "flags": [],
                        "was_auto_approved": False,
                        "suppressed": True,
                        "suppression_reason": "do_not_auto_reply",
                    },
                    user_id=user_id,
                    dialog_id=user_id,
                    generation_id=generation_id,
                    creator_id=_creator_id,
                    scope="user",
                )
            except Exception:
                pass
            try:
                _telemetry_data.complete(success=True)
                await _telemetry.record(_telemetry_data)
            except Exception:
                logger.debug(
                    "telemetry record failed for suppressed user=%s (fail-open)",
                    user_id,
                    exc_info=True,
                )
            # Phase 2 relationship evidence (fail-open; deterministic only,
            # no LLM signals on the suppressed path).
            try:
                await _record_relationship_trajectory(
                    user_id=user_id,
                    creator_id=_creator_id,
                    generation_id=generation_id,
                    user_message=user_message,
                )
            except Exception:
                logger.debug(
                    "relationship path failed on suppressed turn (fail-open)", exc_info=True
                )
            # Phase 6 intimacy evidence (fail-open; deterministic only,
            # no LLM signals on the suppressed path).
            try:
                await _record_intimacy_trajectory(
                    user_id=user_id,
                    creator_id=_creator_id,
                    generation_id=generation_id,
                    user_message=user_message,
                )
            except Exception:
                logger.debug("intimacy path failed on suppressed turn (fail-open)", exc_info=True)
            # Phase 7 boundary evidence (fail-open; deterministic only).
            # A boundary asserted while excluded must still persist so it
            # constrains future turns once exclusion lifts.
            try:
                from commerce.boundary_evidence import (
                    extract_boundary_evidence as _extract_boundary_suppressed,
                )
                from commerce.boundary_state import (
                    accumulate_boundary_turn_idempotent as _accumulate_boundary_suppressed,
                )

                _suppressed_evidence = _extract_boundary_suppressed(user_message)
                if _suppressed_evidence is not None and not _suppressed_evidence.is_empty():
                    await _accumulate_boundary_suppressed(
                        user_id=user_id,
                        creator_id=_creator_id,
                        generation_id=generation_id,
                        evidence=_suppressed_evidence,
                    )
            except Exception:
                logger.debug("boundary path failed on suppressed turn (fail-open)", exc_info=True)
            return

        # ── Creator fail-closed ────────────────────────────────────────────
        _fail_closed_creator_unavailable = False
        if _creator_id is None and not _settings.autonomy_enabled:
            _fail_closed_creator_unavailable = True
            logger.info("No creator resolved and autonomy disabled for user=%s", user_id)

        # ── Persona snapshot ───────────────────────────────────────────────
        _persona_snapshot: dict[str, Any] | None = None
        _persona_snapshot_version: int | None = None
        try:
            from memory.creator_persona import get_structured_persona_async

            _persona_snapshot = await get_structured_persona_async(creator_id=_creator_id)
            if _persona_snapshot:
                _persona_snapshot_version = _persona_snapshot.get("persona_version")
        except Exception:
            logger.debug("Persona snapshot fetch failed for user=%s", user_id)

        # ── Authoritative Context Assembly (Phase 2: ONE TURN = ONE SNAPSHOT) ──
        _context_start = _time.monotonic()
        _authoritative_state = None
        _authoritative_snapshot_available = False
        # Preserve legacy context for llm_path legacy or fallback
        _llm_path_at_acquisition = getattr(_settings, "llm_path", "legacy")
        if _llm_path_at_acquisition == "new":
            try:
                _authoritative_state = await assemble_authoritative_context(
                    creator_id=_creator_id,
                    user_id=user_id,
                    current_message=user_message,
                    generation_id=generation_id,
                    persona_override=persona,
                    structured_persona_snapshot=_persona_snapshot,
                )
                _authoritative_snapshot_available = True
                # Build legacy-shaped context from snapshot for consumers that still expect it
                # (commerce state derive, persona behavior, commerce draft caller). This is a thin
                # conversion, not a second DB fetch.
                try:
                    # Reuse snapshot recent_messages for conversation portion; prepend minimal system
                    _snap_recent = (
                        list(_authoritative_state.recent_messages) if _authoritative_state else []
                    )
                    # Minimal system from snapshot persona
                    _snap_persona = (
                        getattr(_authoritative_state, "persona", persona)
                        if _authoritative_state
                        else persona
                    )
                    _snap_user = (
                        getattr(_authoritative_state, "user", {}) if _authoritative_state else {}
                    )
                    _snap_profile = (
                        getattr(_authoritative_state, "profile", {}) if _authoritative_state else {}
                    )
                    # Keep context variable compatible: list with system + recent (conversation only)
                    # Consumers like _try_commerce_draft only need recent conversation sliced, not full system.
                    # We'll provide recent as context for those callers; system is elsewhere via snapshot.
                    context = [
                        {
                            "role": "user" if m.get("direction") == "inbound" else "assistant",
                            "content": m.get("content", ""),
                        }
                        for m in _snap_recent
                    ]
                    # Ensure at least one system entry for legacy callers that check system
                    if _snap_persona:
                        context = [{"role": "system", "content": _snap_persona}] + context
                except Exception:
                    context = await build_qwen3_context(
                        user_id, user_message, persona, creator_id=_creator_id
                    )
                    _authoritative_snapshot_available = False
            except Exception:
                logger.warning(
                    "Authoritative assembly failed, falling back to legacy context", exc_info=True
                )
                context = await build_qwen3_context(
                    user_id, user_message, persona, creator_id=_creator_id
                )
                _authoritative_state = None
                _authoritative_snapshot_available = False
        else:
            context = await build_qwen3_context(
                user_id, user_message, persona, creator_id=_creator_id
            )
        _context_end = _time.monotonic()
        _context_build_ms = int((_context_end - _context_start) * 1000)
        _telemetry_data.context_build_ms = _context_build_ms
        # Store snapshot availability for later phases
        try:
            _telemetry_data.authoritative_snapshot = _authoritative_snapshot_available  # type: ignore[attr-defined]
        except Exception:
            pass
        # Phase 87: detailed context budget constants (for dashboard verification)
        try:
            from context_engine.models import TOTAL_CONTEXT_BUDGET
            from context_engine.budget import HEADER_RESERVE_TOKENS, EFFECTIVE_TOTAL_BUDGET

            _telemetry_data.context_budget_limit = TOTAL_CONTEXT_BUDGET  # type: ignore
            _telemetry_data.context_header_reserve = HEADER_RESERVE_TOKENS  # type: ignore
            _telemetry_data.context_effective_budget = EFFECTIVE_TOTAL_BUDGET  # type: ignore
            _telemetry_data.lexical_threshold = 80  # type: ignore
            _telemetry_data.semantic_threshold = 0.30  # type: ignore
        except Exception:
            pass
        # Phase 89: persona identity + grounding flags (fail-open)
        try:
            if _authoritative_state is not None:
                if getattr(_authoritative_state, "persona_id", None) is not None:
                    _telemetry_data.persona_id = _authoritative_state.persona_id  # type: ignore
                if getattr(_authoritative_state, "persona_version", None) is not None:
                    _telemetry_data.persona_version = _authoritative_state.persona_version  # type: ignore
                _telemetry_data.participant_grounding_enabled = bool(
                    getattr(_authoritative_state, "participants", None) is not None
                )  # type: ignore
                _telemetry_data.conversation_contract_present = bool(
                    getattr(_authoritative_state, "conversation_contract", None) is not None
                )  # type: ignore
        except Exception:
            pass
        # Phase 87: STATE_READY event (fail-open)
        try:
            from core.phase87_events import emit_state_ready

            _state_success = bool(
                _authoritative_snapshot_available and _authoritative_state is not None
            )
            _state_acq_ms = 0
            try:
                _state_acq_ms = (
                    int(_authoritative_state.metadata.get("acquisition_ms", _context_build_ms))
                    if _authoritative_state and hasattr(_authoritative_state, "metadata")
                    else _context_build_ms
                )
            except Exception:
                _state_acq_ms = _context_build_ms
            await emit_state_ready(
                user_id=user_id,
                creator_id=_creator_id,
                generation_id=generation_id,
                runtime_mode=_runtime_mode_label,
                success=_state_success,
                acquisition_ms=_state_acq_ms,
                degraded=not _state_success,
            )
        except Exception:
            pass

        # ── Context timing and persona block ───────────────────────────────
        _gen_context_chars = sum(len(m.get("content", "")) for m in context if isinstance(m, dict))
        _persona_block = ""
        try:
            _last_sys = next(
                (m for m in reversed(context) if isinstance(m, dict) and m.get("role") == "system"),
                None,
            )
            if _last_sys:
                _persona_block = _last_sys.get("content", "")[:200]
        except Exception:
            pass
        _telemetry_data.context_chars = _gen_context_chars

        # ── Phase 102: authoritative product/menu context (DB → context) ──
        # Reuses existing authoritative context, no second builder, no LLM.
        # Deterministic, creator-scoped, fail-closed.
        if _creator_id is not None:
            try:
                from commerce.product_catalog import get_menu_context

                _menu_ctx = await get_menu_context(_creator_id, max_items=5)
                if _menu_ctx:
                    context = list(context) + [{"role": "system", "content": _menu_ctx}]
                    _telemetry_data.context_chars = sum(
                        len(m.get("content", "")) for m in context if isinstance(m, dict)
                    )
                    try:
                        _telemetry_data.menu_items = sum(
                            1 for _line in _menu_ctx.splitlines() if _line.strip().startswith("- ")
                        )  # type: ignore
                    except Exception:
                        pass
            except Exception:
                pass

        # ── Publish ai.generation_started ──────────────────────────────────
        # Phase 103 privacy: no raw user message, only non-content-bearing diagnostic
        await publish_event(
            "ai.generation_started",
            {
                "message_length": len(user_message),
                "has_content": bool(user_message and user_message.strip()),
            },
            user_id=user_id,
            dialog_id=user_id,
            generation_id=generation_id,
            creator_id=_creator_id,
            scope="user",
        )

        # ── Context Engine observation (deferred — see after conversation_state) ──
        # Stage 78D: moved after conversation_state so state relevance is real, not neutral.
        _context_engine_observation = None
        _retrieved_context = ""
        _telemetry_data.context_engine_observed = False

        # ── Fail-closed routing ────────────────────────────────────────────
        # Thanks for your message! Our team will follow up shortly.
        if _fail_closed_creator_unavailable:
            if _creator_id is not None:
                _fail_qid = await add_to_operator_queue(
                    user_id=user_id,
                    draft_content="",
                    confidence_score=0.0,
                    flags=["creator_context_unavailable"],
                    creator_id=_creator_id,
                    generation_id=generation_id,
                )
            else:
                # P1.3a FIX: no creator – fail closed without INSERT, no NULL legacy row
                logger.warning(
                    "creator_context_unavailable – no creator, failing closed without operator_queue insert (P1.3a) user=%s",
                    user_id,
                )
                _fail_qid = None
            _telemetry_data.routing_decision = "fail_closed_operator"
            await publish_events_batch(
                [
                    {
                        "event": "ai.generation_completed",
                        "data": {
                            "draft": "",
                            "score": 0.0,
                            "flags": ["creator_context_unavailable"],
                            "was_auto_approved": False,
                        },
                        "user_id": user_id,
                        "dialog_id": user_id,
                        "generation_id": generation_id,
                        "creator_id": _creator_id,
                        "scope": "user",
                    },
                    {
                        "event": "suggestion.created",
                        "data": {
                            "queue_id": _fail_qid,
                            "draft": "",
                            "score": 0.0,
                            "flags": ["creator_context_unavailable"],
                        },
                        "user_id": user_id,
                        "dialog_id": user_id,
                        "generation_id": generation_id,
                        "creator_id": _creator_id,
                        "scope": "user",
                    },
                ]
            )
            return

        # ── Long-term memory extraction + persistence ──────────────────────
        _ltm_memories: list[dict[str, Any]] = []
        try:
            from commerce.long_term_memory import extract_explicit_memories, add_memory_item

            if _creator_id is not None:
                explicit_mems = extract_explicit_memories(user_message, _creator_id, user_id)
                _ltm_memories = list(explicit_mems) if explicit_mems else []
                for mem in _ltm_memories:
                    try:
                        # Phase 5: actual signature is
                        # add_memory_item(creator_id, user_id, item) and it
                        # must be awaited (async). The previous call passed
                        # (mem, user_id=, creator_id=) without await, so the
                        # coroutine was never executed and nothing persisted.
                        await add_memory_item(_creator_id, user_id, mem)
                    except Exception:
                        logger.debug("Memory persistence failed for user=%s item=%s", user_id, mem)
                if _ltm_memories:
                    _telemetry_data.ltm_extracted = len(_ltm_memories)
        except Exception:
            logger.debug("LTM extraction failed for user=%s", user_id)

        # ── Fan knowledge extraction + persistence + retrieval ─────────────
        _fan_knowledge: list[dict[str, Any]] = []
        try:
            from commerce.fan_knowledge import (
                extract_fan_knowledge,
                add_knowledge_item,
                get_fan_knowledge,
            )

            if _creator_id is not None:
                # Phase 5: get_knowledge_memory does not exist (canonical is
                # get_fan_knowledge(creator_id, user_id)); the previous
                # import raised ImportError on every turn, disabling this
                # whole block (fail-open).
                existing = await get_fan_knowledge(_creator_id, user_id)
                fan_items = extract_fan_knowledge(
                    user_message, _creator_id, user_id, generation_id=generation_id
                )
                for it in fan_items or []:
                    _item_dict = it.__dict__ if hasattr(it, "__dict__") else it
                    _fan_knowledge.append(_item_dict)
                    try:
                        # Phase 5: actual signature is
                        # add_knowledge_item(creator_id, user_id, item:
                        # FanKnowledgeItem). The previous call passed the
                        # dict form with swapped kwargs, so nothing persisted.
                        await add_knowledge_item(_creator_id, user_id, it)
                    except Exception:
                        logger.debug("Fan knowledge persistence failed user=%s", user_id)
                _telemetry_data.fan_knowledge_extracted = len(_fan_knowledge)
                _telemetry_data.fan_knowledge_existing = len(existing if existing else 0)
        except Exception:
            logger.debug("Fan knowledge extraction failed for user=%s", user_id)

        # ── Behavioral signals with hour ───────────────────────────────────
        try:
            from commerce.behavioral_intelligence import observe_behavioral_signal

            if _creator_id is not None:
                hour_utc = datetime.now(timezone.utc).hour
                observe_behavioral_signal(
                    _creator_id,
                    user_id,
                    "message_received",
                    user_message[:100],
                    generation_id=generation_id,
                    hour_utc=hour_utc,
                )
        except Exception:
            logger.debug("Behavioral signal recording failed for user=%s", user_id)

        # ── Q1 Shadow launch removed ──────────────────────────────────────
        # Disabled shadow path (core/qwen3_shadow.py) deleted with the
        # Ollama stack. No shadow execution; authoritative path unchanged.
        _shadow_start = _time.monotonic()
        _shadow_task = None

        # Commerce signals: extracted lazily per path
        _commerce_signals = None

        # ── Open loop resolution ───────────────────────────────────────────
        _resolved = None
        try:
            from commerce.open_loop import resolve_open_loop

            _resolved = await resolve_open_loop(user_id, user_message, _creator_id, _ltm_memories)
            if _resolved:
                _telemetry_data.open_loop_resolved = True
        except Exception:
            logger.debug("Open loop resolution failed for user=%s", user_id)

        # ── Commerce state derivation ──────────────────────────────────────
        _conv_state = None
        _cstate = None
        desire = None
        temp = None
        readiness = None
        window = None
        objective = None
        next_action = None
        _response_mode = None
        _question_policy = None
        _action_str = None
        _nba_str = None
        _get_cached_user = None
        _user_for_state = None
        _cached_profile_for_commerce = None
        _get_profile_cache = None
        # Phase 2: reuse authoritative state's single derivation when available
        if (
            _authoritative_state is not None
            and getattr(_authoritative_state, "conversation_state", None) is not None
        ):
            _conv_state = getattr(_authoritative_state, "conversation_state")
            _user_for_state = getattr(_authoritative_state, "user", None)
            _cached_profile_for_commerce = getattr(_authoritative_state, "profile", None)
        else:
            try:
                from core.conversation_state import derive_conversation_state

                _conv_state = derive_conversation_state(context)
            except Exception:
                pass
            try:
                from memory.context import get_last_user as _get_cached_user_fn

                _user_for_state = _get_cached_user_fn()
            except Exception:
                _user_for_state = None
            if _user_for_state is None:
                try:
                    from db.postgres import get_user as _get_user_fn

                    _user_for_state = await _get_user_fn(user_id)
                except Exception:
                    pass
            try:
                from memory.context import get_last_profile as _get_cached_profile

                _cached_profile_for_commerce = _get_cached_profile()
            except Exception:
                _cached_profile_for_commerce = None
            if _cached_profile_for_commerce is None:
                try:
                    from db.postgres import get_user_profile as _get_profile_fn

                    _cached_profile_for_commerce = await _get_profile_fn(user_id)
                except Exception:
                    pass
        # ── Phase 7 boundary evidence + durable state (deterministic) ──
        # Separate domain (never inside relationship/intimacy/commerce
        # modules): current-turn evidence → creator-scoped durable
        # constraints → effective snapshot (durable + current evidence, so
        # current explicit evidence beats historical intimacy/relationship
        # below). The LLM never creates boundary state. Current-turn
        # evidence is enforced from memory even if the durable write
        # fails. No new worker/queue/table/Redis/dependency.
        # Debounce note: ingress forwards the latest buffered message
        # only (chatbotv2/handlers.py); a boundary asserted in a
        # superseded message of the same 3s window is not accumulated
        # until re-asserted. All inbound messages remain in the DB audit
        # log. Documented limitation, covered by tests.
        _boundary_evidence = None
        _boundary_snapshot = None
        _boundary_applied = False
        _boundary_commerce_veto = False
        _boundary_commerce_reason = None
        _routing_boundary_violation = False
        try:
            from commerce.boundary_evidence import (
                BoundaryTurnEvidence as _BoundaryEvidence,
            )
            from commerce.boundary_evidence import (
                extract_boundary_evidence as _extract_boundary,
            )
            from commerce.boundary_state import (
                BoundarySnapshot as _BoundarySnapshot,
            )
            from commerce.boundary_state import (
                accumulate_boundary_turn_idempotent as _accumulate_boundary,
            )
            from commerce.boundary_state import (
                boundary_blocks_commerce as _boundary_veto_fn,
            )

            try:
                _boundary_evidence = _extract_boundary(user_message)
            except Exception:
                logger.debug("Boundary evidence extraction failed for user=%s (fail-open)", user_id)
                _boundary_evidence = None
            if _boundary_evidence is None:
                _boundary_evidence = _BoundaryEvidence()
            try:
                _boundary_snapshot, _boundary_applied = await _accumulate_boundary(
                    user_id=user_id,
                    creator_id=_creator_id,
                    generation_id=generation_id,
                    evidence=_boundary_evidence,
                    profile=_cached_profile_for_commerce,
                )
            except Exception:
                logger.debug(
                    "Boundary accumulation failed for user=%s (fail-safe: current-turn evidence only)",
                    user_id,
                )
                _boundary_snapshot = None
            if _boundary_snapshot is None:
                # Fail-closed: unknown durable state must not read as "no
                # boundary". Enforce current-turn evidence from memory; the
                # degraded flag forces operator review at routing.
                try:
                    from commerce.boundary_state import (
                        snapshot_with_current_evidence as _boundary_effective,
                    )

                    _boundary_snapshot = _boundary_effective({}, _boundary_evidence)
                    _boundary_snapshot = _BoundarySnapshot(
                        active=_boundary_snapshot.active,
                        recovering=_boundary_snapshot.recovering,
                        expired=_boundary_snapshot.expired,
                        degraded=True,
                    )
                except Exception:
                    _boundary_snapshot = None
            try:
                _boundary_commerce_veto, _boundary_commerce_reason = _boundary_veto_fn(
                    _boundary_snapshot
                )
            except Exception:
                _boundary_commerce_veto, _boundary_commerce_reason = False, None
            if _boundary_commerce_veto:
                logger.info(
                    "Boundary commerce veto user=%s reason=%s",
                    user_id,
                    _boundary_commerce_reason,
                )
        except Exception:
            logger.debug("Boundary state path failed for user=%s (fail-safe)", user_id)

        # ── Phase 7 DO_NOT_CONTACT suppression (deterministic, no LLM) ──
        # An active contact boundary suppresses autonomous outbound for
        # this turn entirely: no generation, no operator queue, no send
        # (mirrors the excluded path below). The inbound message stays in
        # the DB audit log and the suppression is emitted as an event.
        # Explicit relaxation ("you can message me again") clears the
        # boundary via normal evidence before this check.
        _boundary_suppressed_contact = False
        try:
            _boundary_suppressed_contact = bool(
                _boundary_snapshot is not None and _boundary_snapshot.blocks_contact()
            )
        except Exception:
            _boundary_suppressed_contact = False
        if _boundary_suppressed_contact:
            logger.info("User %s has active DO_NOT_CONTACT boundary, suppressing", user_id)
            _telemetry_data.routing_decision = "suppressed_boundary_contact"
            _telemetry_data.handoff_reason = "boundary_do_not_contact"  # type: ignore
            _telemetry_data.delivery_status = "suppressed"  # type: ignore
            try:
                await publish_event(
                    "ai.generation_completed",
                    {
                        "draft": "",
                        "score": 0.0,
                        "flags": ["boundary_violation:do_not_contact"],
                        "was_auto_approved": False,
                        "suppressed": True,
                        "suppression_reason": "boundary_do_not_contact",
                    },
                    user_id=user_id,
                    dialog_id=user_id,
                    generation_id=generation_id,
                    creator_id=_creator_id,
                    scope="user",
                )
            except Exception:
                pass
            try:
                _telemetry_data.complete(success=True)
                await _telemetry.record(_telemetry_data)
            except Exception:
                logger.debug(
                    "telemetry record failed for boundary-suppressed user=%s (fail-open)",
                    user_id,
                    exc_info=True,
                )
            try:
                await _record_relationship_trajectory(
                    user_id=user_id,
                    creator_id=_creator_id,
                    generation_id=generation_id,
                    user_message=user_message,
                )
            except Exception:
                logger.debug(
                    "relationship path failed on boundary-suppressed turn (fail-open)",
                    exc_info=True,
                )
            try:
                await _record_intimacy_trajectory(
                    user_id=user_id,
                    creator_id=_creator_id,
                    generation_id=generation_id,
                    user_message=user_message,
                )
            except Exception:
                logger.debug(
                    "intimacy path failed on boundary-suppressed turn (fail-open)", exc_info=True
                )
            return
        try:
            from commerce.conversational import build_conversational_commerce_state

            _cstate = await build_conversational_commerce_state(
                user_id=user_id,
                creator_id=_creator_id,
                context=context,
                signals=_commerce_signals,
                current_topic=_conv_state.get("current_topic") if _conv_state else None,
                profile=_cached_profile_for_commerce,
                generation_id=generation_id,  # G6 ledger correlation only
            )
            if _cstate:
                desire = _cstate.get("desire")
                temp = _cstate.get("temperature")
                readiness = _cstate.get("readiness")
                window = _cstate.get("sales_window")
                objective = _cstate.get("commercial_objective")
                next_action = _cstate.get("next_best_action")
                _response_mode = _cstate.get("response_mode")
                _question_policy = _cstate.get("question_policy")
                _action_str = str(next_action) if next_action else None
                _nba_str = str(next_action) if next_action else None
                # Phase 101: warming / readiness (deterministic, creator-scoped, LLM advisory only)
                try:
                    _warming = _cstate.get("warming")
                    _phase101_readiness = _cstate.get("phase101_readiness")
                    if _warming is not None:
                        _telemetry_data.warming_level = getattr(_warming, "level", None)  # type: ignore
                        _telemetry_data.warming_score = getattr(_warming, "score", None)  # type: ignore
                        _telemetry_data.warming_available = getattr(_warming, "available", None)  # type: ignore
                        _telemetry_data.warming_ceiling = getattr(_warming, "ceiling", None)  # type: ignore
                    if _phase101_readiness is not None:
                        _telemetry_data.readiness_level = getattr(
                            _phase101_readiness, "level", None
                        )  # type: ignore
                        _telemetry_data.readiness_available = getattr(
                            _phase101_readiness, "available", None
                        )  # type: ignore
                except Exception:
                    pass
                # G4/G7/G8 evidence-ledger emission only (fail-open; no behavior change).
                # _cstate keys use short spellings ("temp"/"window"/"objective");
                # read tolerantly WITHOUT rewiring the legacy locals above.
                # Telemetry twins populate existing schema fields (most lack a
                # persist column in insert_generation_telemetry, so each has a
                # logger.info twin that is the durable observable).
                try:
                    _g_rel_state = _cstate.get("relationship_state")
                    # G4: no telemetry schema field for relationship_state -> log-only.
                    logger.info(
                        "G4 relationship_state generation_id=%s relationship_state=%s",
                        generation_id,
                        _g_rel_state,
                    )
                    _g_desire = _cstate.get("desire")
                    try:
                        _g_desire_stage = getattr(_g_desire, "stage", None)
                        _g_desire_stage_s = getattr(_g_desire_stage, "value", _g_desire_stage)
                        _g_desire_conf = getattr(_g_desire, "confidence", None)
                        _g_desire_ev = tuple(getattr(_g_desire, "evidence", None) or ())
                    except Exception:
                        _g_desire_stage_s, _g_desire_conf, _g_desire_ev = None, None, ()
                    try:
                        _telemetry_data.desire_stage = (
                            str(_g_desire_stage_s) if _g_desire_stage_s is not None else None
                        )
                    except Exception:
                        pass
                    logger.info(
                        "G7 desire generation_id=%s stage=%s confidence=%s evidence=%s",
                        generation_id,
                        _g_desire_stage_s,
                        _g_desire_conf,
                        list(_g_desire_ev)[:5],
                    )
                    _g_temp = _cstate.get("temp", _cstate.get("temperature"))
                    _g_window = _cstate.get("window", _cstate.get("sales_window"))
                    _g_readiness = _cstate.get("readiness")
                    _g_nba = _cstate.get("next_best_action")
                    _g_obj = _cstate.get("objective", _cstate.get("commercial_objective"))
                    try:
                        _g_temp_level = getattr(_g_temp, "level", _g_temp)
                        _g_temp_s = getattr(_g_temp_level, "value", _g_temp_level)
                        _telemetry_data.temperature = (
                            str(_g_temp_s) if _g_temp_s is not None else None
                        )
                    except Exception:
                        _g_temp_s = None
                    try:
                        _g_window_s = getattr(_g_window, "value", _g_window)
                        _telemetry_data.sales_window = (
                            str(_g_window_s) if _g_window_s is not None else None
                        )
                    except Exception:
                        _g_window_s = None
                    try:
                        _g_readiness_s = getattr(_g_readiness, "value", _g_readiness)
                        _telemetry_data.offer_readiness = (
                            str(_g_readiness_s) if _g_readiness_s is not None else None
                        )
                    except Exception:
                        _g_readiness_s = None
                    try:
                        _g_nba_s = getattr(_g_nba, "value", _g_nba)
                        _telemetry_data.next_best_action = (
                            str(_g_nba_s) if _g_nba_s is not None else None
                        )
                    except Exception:
                        _g_nba_s = None
                    try:
                        _telemetry_data.conversation_objective = (
                            str(_g_obj) if _g_obj is not None else None
                        )
                    except Exception:
                        pass
                    logger.info(
                        "G8 derived_commerce generation_id=%s temperature=%s sales_window=%s "
                        "offer_readiness=%s next_best_action=%s conversation_objective=%s",
                        generation_id,
                        _g_temp_s,
                        _g_window_s,
                        _g_readiness_s,
                        _g_nba_s,
                        _g_obj,
                    )
                except Exception:
                    pass
        except Exception:
            logger.debug("Commerce state derivation failed for user=%s", user_id)

        # ── P3.3.14.3: single Opportunity Engine evaluation per message ───────
        # Deterministic, read-only, provider-free. One tz-aware UTC timestamp
        # for the entire message; result is preserved for downstream helpers.
        _opportunity_result = None
        _opportunity_now = None
        _sealing_candidate = None
        _chosen_drop_cuid = None
        try:
            from commerce.opportunity_engine import evaluate_opportunity
            from datetime import datetime, timezone

            _opportunity_now = datetime.now(timezone.utc)
            if _creator_id is not None and _conv_state is not None:
                _opportunity_result = await evaluate_opportunity(
                    _creator_id, user_id, _conv_state, _opportunity_now
                )
                # Preserve complete result; derive sealing locals per v1 contract
                if (
                    _opportunity_result
                    and _opportunity_result.has_opportunity
                    and _opportunity_result.selected_candidate is not None
                ):
                    _cand = _opportunity_result.selected_candidate
                    _mapped = getattr(_cand, "mapped_drop_ids", ()) or ()
                    if len(_mapped) == 0:
                        logger.info(
                            "opportunity NO_MAPPED_DROP creator=%s user=%s def=%s",
                            _creator_id,
                            getattr(_cand, "definition_id", None),
                        )
                        _sealing_candidate = None
                        _chosen_drop_cuid = None
                    elif len(_mapped) > 1:
                        logger.info(
                            "opportunity MULTIPLE_DROPS creator=%s user=%s def=%s drops=%s",
                            _creator_id,
                            getattr(_cand, "definition_id", None),
                            _mapped,
                        )
                        _sealing_candidate = None
                        _chosen_drop_cuid = None
                    else:
                        _sealing_candidate = _cand
                        _chosen_drop_cuid = _mapped[0]
        except Exception:
            logger.debug("Single opportunity evaluation failed (fail-closed)", exc_info=True)
            _opportunity_result = None
            _sealing_candidate = None
            _chosen_drop_cuid = None

        # ── P3.5.1: opportunity attribution ledger (observability only) ──
        # Best-effort and failure-isolated: a ledger failure must never break
        # generation, sealing, or send. Skipped when there is no evaluation
        # result to attribute (fail-closed turns decide nothing).
        _opportunity_id = None
        if _opportunity_result is not None and _creator_id is not None:
            try:
                from commerce.opportunity_ledger import record_opportunity_decision

                _opportunity_row = await record_opportunity_decision(
                    creator_id=_creator_id,
                    user_id=user_id,
                    generation_id=generation_id,
                    evaluated_at=_opportunity_now,
                    opportunity_result=_opportunity_result,
                    conversation_state=_conv_state,
                )
                _opportunity_id = (
                    _opportunity_row.get("opportunity_id") if _opportunity_row else None
                )
            except Exception:
                logger.debug("Opportunity ledger decision record failed (isolated)", exc_info=True)
                _opportunity_id = None

        # ── P3.3.14.3: sealing — exact Drop handoff, only when single mapping ──
        _seal_result = None
        _sealed_execution = None
        # P3.3.14.4 single-outbound gate: True only when the sealed PPV was
        # proven handed to the send stream (EXECUTED or ALREADY_DELIVERED).
        # Local only, per inbound message. Reserved/in-flight and all
        # failures leave it False so normal handling proceeds.
        _sealed_ppv_handled = False
        # Phase 7: an active conversational boundary vetoes sealed offer
        # transitions before anything is handed to the send stream.
        # Manner constraints (NO_FLIRTING/NO_PET_NAME/NO_PERSONAL_QUESTION)
        # do not veto here; realization is validated post-generation.
        try:
            if _boundary_commerce_veto and _sealing_candidate is not None:
                logger.info(
                    "Boundary commerce veto user=%s reason=%s — clearing sealing candidate",
                    user_id,
                    _boundary_commerce_reason,
                )
                _sealing_candidate = None
        except Exception:
            pass
        # Phase 3.3: single-outbound per (creator,user,tg_id). Local per-turn
        # state: _turn_send_claimed=True means THIS invocation owns the turn
        # (claimed at the first outbound site); _turn_already_sent=True means
        # another invocation already sent this turn — suppress all outbound
        # below, never a second wire. Redis error fails open (helper returns
        # True) so the gate never causes a lost-send.
        _turn_send_claimed = False
        _turn_already_sent = False
        if _sealing_candidate is not None and _chosen_drop_cuid is not None:
            try:
                from commerce.opportunity_sealing import seal_ranked_candidate

                _seal_result = await seal_ranked_candidate(
                    _sealing_candidate,
                    _opportunity_result.ranking_result if _opportunity_result else None,
                    _creator_id,
                    user_id,
                    _chosen_drop_cuid,
                )
                if _seal_result.status == "SEALED":
                    logger.info(
                        "Sealed opportunity creator=%s user=%s offer_id=%s",
                        _creator_id,
                        user_id,
                        _seal_result.offer.get("id") if _seal_result.offer else None,
                    )
                    # P3.3.14.4: thin sealed execution — single attempt using the
                    # exact sealed result plus the existing message scope. No
                    # second evaluation, no new timestamp, no fallback.
                    # Phase 3.3: claim the turn before the sealed wire so a
                    # redelivered/second draft for the same tg_id is suppressed.
                    _sealed_execution = None
                    _sealed_ppv_handled = False
                    try:
                        from commerce.opportunity_execution import (
                            execute_sealed_offer,
                            is_sealed_ppv_handled,
                        )
                        from db.redis import try_claim_turn_send as _claim_turn_seal

                        _seal_claimed = await _claim_turn_seal(
                            _creator_id, user_id, telegram_message_id
                        )
                        if not _seal_claimed:
                            logger.info(
                                "Turn already sent creator=%s user=%s tg_id=%s — suppressing sealed execution",
                                _creator_id,
                                user_id,
                                telegram_message_id,
                            )
                            _turn_already_sent = True
                        else:
                            _turn_send_claimed = True
                            _sealed_execution = await execute_sealed_offer(
                                _seal_result,
                                creator_id=_creator_id,
                                user_id=user_id,
                                generation_id=generation_id,
                                conversation=context,
                                user_message=user_message,
                                persona=persona,
                                telegram_message_id=telegram_message_id,
                                turn_preclaimed=True,
                            )
                        try:
                            _sealed_ppv_handled = bool(is_sealed_ppv_handled(_sealed_execution))
                        except Exception:
                            _sealed_ppv_handled = (
                                _sealed_execution is not None
                                and getattr(_sealed_execution, "status", None) == "EXECUTED"
                            )
                        logger.info(
                            "Sealed execution creator=%s user=%s status=%s offer_id=%s handled=%s",
                            _creator_id,
                            user_id,
                            _sealed_execution.status if _sealed_execution else None,
                            _sealed_execution.offer_id if _sealed_execution else None,
                            _sealed_ppv_handled,
                        )
                    except Exception:
                        logger.exception(
                            "Sealed execution failed (fail-closed) creator=%s user=%s",
                            _creator_id,
                            user_id,
                        )
                        _sealed_execution = None
                        _sealed_ppv_handled = False
                    # ── P3.5.1: record send outcome (isolated) ──
                    try:
                        if _opportunity_id is not None:
                            from commerce.opportunity_ledger import record_opportunity_send

                            await record_opportunity_send(
                                _opportunity_id,
                                sealed_execution=_sealed_execution,
                            )
                    except Exception:
                        logger.debug(
                            "Opportunity ledger send record failed (isolated)", exc_info=True
                        )
                else:
                    logger.info(
                        "Sealing failed creator=%s user=%s status=%s sub=%s",
                        _creator_id,
                        user_id,
                        _seal_result.status,
                        _seal_result.subreason,
                    )
            except Exception:
                logger.exception(
                    "Sealing failed (fail-closed) creator=%s user=%s", _creator_id, user_id
                )
                _seal_result = None

        # ── P3.5.1: link sealing outcome to the ledger (isolated) ──
        try:
            if _opportunity_id is not None:
                from commerce.opportunity_ledger import link_opportunity_seal

                await link_opportunity_seal(
                    _opportunity_id,
                    seal_result=_seal_result,
                    drop_cuid=_chosen_drop_cuid,
                )
        except Exception:
            logger.debug("Opportunity ledger seal link failed (isolated)", exc_info=True)

        # ── Experiment tracking ────────────────────────────────────────────
        _experiment_registry = None
        _strat_family = None
        _prod_family = None
        _recent_exps = None
        _fat = None
        _exposure = None
        _exp_id = None
        _exp = None
        _var = None
        try:
            from commerce.adaptive_optimization import (
                make_exposure,
                persist_exposure,
                compute_fatigue,
                get_exposures_memory,
                deterministic_assignment,
                assign_variant,
            )

            # Phase 10 fix: get_exposures_memory(creator_id, user_id) is
            # synchronous and creator-first. Handle both sync/async defensively
            # and preserve creator isolation (never swap the order).
            _recent_exps = []
            if _creator_id is not None:
                try:
                    _maybe = get_exposures_memory(_creator_id, user_id)
                    import inspect as _insp

                    _recent_exps = await _maybe if _insp.isawaitable(_maybe) else _maybe
                    if _recent_exps is None:
                        _recent_exps = []
                except Exception:
                    _recent_exps = []
            _strat_family = (
                _cstate.get("strategy_family") if _cstate else None
            ) or "RELATIONSHIP_BUILD"
            _prod_family = _cstate.get("product_family") if _cstate else None
            # Phase 10 fix: compute_fatigue(recent_exposures, strategy, window).
            try:
                _fat = compute_fatigue(_recent_exps or [], _strat_family) if _recent_exps else 0.0
            except Exception:
                _fat = 0.0
            # Phase 10: stamp config/strategy version (fail-safe legacy default).
            try:
                from commerce.phase10_learning import get_active_config_version as _p10_cfg

                _p10_cfg_v = (
                    _p10_cfg(creator_scope=_creator_id)
                    if _creator_id is not None
                    else "unversioned-legacy"
                )
            except Exception:
                _p10_cfg_v = "unversioned-legacy"
            _exposure = make_exposure(
                creator_id=_creator_id if _creator_id is not None else 0,
                user_id=user_id,
                generation_id=generation_id,
                strategy_family=_strat_family,
                product_family=_prod_family,
                config_version=_p10_cfg_v,
            )
            if _exposure:
                await persist_exposure(_exposure)
                _exp_id = (
                    _exposure.get("experiment_id")
                    if isinstance(_exposure, dict)
                    else getattr(_exposure, "experiment_id", None)
                )
                _var = (
                    _exposure.get("variant")
                    if isinstance(_exposure, dict)
                    else getattr(_exposure, "variant", None)
                )
                if _exp_id is None:
                    _exp_id = getattr(_exposure, "experiment_id", None)
                if _var is None:
                    _var = getattr(_exposure, "experiment_variant", None)
                _exp = _exposure
                try:
                    _telemetry_data.experiment_id = _exp_id
                    _telemetry_data.experiment_variant = _var
                except Exception:
                    pass
        except Exception:
            logger.debug("Experiment tracking failed for user=%s", user_id)

        # ── Pressure / risk / operation decision ───────────────────────────
        _cp = None
        _dr = None
        _bod = None
        _dl = None
        _LCS = None
        _recent_offer_cnt = 0
        _consec_rej = 0
        _has_purch = False
        _after_s = False
        _is_cooldown = False
        _pressure = None
        _risk = None
        _lc = "new"
        _op_dec = None
        _bridge_exc = None
        try:
            from commerce.conversation_operations import (
                compute_pressure,
                derive_risk,
                build_operation_decision,
                derive_lifecycle,
                LifecycleState,
            )

            try:
                # Phase 10 fix: derive_lifecycle(*, desire_stage, ...) is
                # keyword-only. Derive from deterministic _cstate fields.
                if _cstate:
                    _lc_obj = derive_lifecycle(
                        desire_stage=_cstate.get("desire"),
                        relationship_state=_cstate.get("relationship_state"),
                        aftercare_status=_cstate.get("aftercare_status"),
                        is_on_cooldown=bool(_cstate.get("is_on_cooldown", False)),
                        consecutive_rejections=int(_cstate.get("consecutive_rejections", 0) or 0),
                        has_purchased=bool(
                            _cstate.get("has_purchased", False)
                            or _cstate.get("recent_purchase_count", 0)
                        ),
                        is_handoff=bool(_cstate.get("is_handoff", False)),
                    )
                else:
                    _lc_obj = LifecycleState.NEW
                _lc = _lc_obj.value if hasattr(_lc_obj, "value") else str(_lc_obj)
            except Exception:
                _lc = "new"
            try:
                from commerce.dao import get_timing_context, get_behavioral_feedback_context

                if _creator_id:
                    _timing_coro = get_timing_context(_creator_id, user_id)
                    _behavioral_coro = get_behavioral_feedback_context(_creator_id, user_id)
                    _timing_ctx, _behavioral_ctx = await asyncio.gather(
                        _timing_coro, _behavioral_coro, return_exceptions=True
                    )
                    if isinstance(_timing_ctx, Exception):
                        _timing_ctx = {}
                    if isinstance(_behavioral_ctx, Exception):
                        _behavioral_ctx = {}
                else:
                    _timing_ctx = {}
                    _behavioral_ctx = {}
                _recent_offer_cnt = _timing_ctx.get("recent_offer_count", 0)
                _consec_rej = _behavioral_ctx.get("consecutive_rejections", 0)
                _after_s = _behavioral_ctx.get("aftercare_status", "none") != "none"
                _is_cooldown = _timing_ctx.get("is_on_cooldown", False)
            except Exception:
                pass
            try:
                _pressure = compute_pressure(
                    recent_offer_count=_recent_offer_cnt,
                    recent_rejection_count=_consec_rej,
                    aftercare=_after_s,
                    cooldown=_is_cooldown,
                    lifecycle=_lc,
                )
                _risk = derive_risk(_pressure)
                _telemetry_data.pressure_bucket = _pressure.bucket
                _telemetry_data.risk_state = _risk.value
            except Exception:
                pass
            try:
                _bod = build_operation_decision(
                    objective=objective or _commercial_objective or "engagement",
                    objective_reason="commerce_state_derivation",
                    strategy=_var,
                    pressure=_pressure,
                    risk_state=_risk,
                    lifecycle=_lc,
                    generation_id=generation_id,
                    creator_id=_creator_id,
                    user_id=user_id,
                )
                _op_dec = _bod
                _telemetry_data.operation_decision_allowed = _bod.allowed if _bod else None
            except Exception:
                pass
        except Exception:
            logger.debug("Pressure/risk/operation decision failed for user=%s", user_id)

        # ── Commercial objective with pause checks ─────────────────────────
        _skip_qwen_due_to_pause = False
        _paused_reason = None
        _pc_allowed_pre = True
        _rr_pre = None
        _iraf_pre = None
        _icp_pre = None
        _irp_pre = None
        _strategy_for_gate = None
        _exp_for_gate = None
        _allowed_pre = True
        _reason_pre = None
        _obj_for_gate = None
        _ghm_pre = None
        _h = None
        _rollout_blocked = False
        _rid = None
        _r = None
        _commercial_objective = None
        try:
            from commerce.objective import derive_commercial_objective

            _commercial_objective = derive_commercial_objective(
                _commerce_signals,
                _cstate if _cstate else desire,
            )
            _telemetry_data.commercial_objective = _commercial_objective
        except Exception:
            pass
        try:
            from commerce.production_control import (
                autonomous_allowed,
                is_rollout_active_for,
                is_commerce_paused,
                is_reengagement_paused,
                record_metric,
                record_audit,
                OperationalAuditRecord,
            )

            _pc_allowed_pre = autonomous_allowed(
                creator_id=_creator_id,
                user_id=user_id,
                objective=_commercial_objective,
                strategy=_var,
            )
            _rollout_blocked = (
                is_rollout_active_for(creator_id=_creator_id) if _creator_id else False
            )
            _paused_reason = None
            if is_commerce_paused(_creator_id) if _creator_id else False:
                _skip_qwen_due_to_pause = True
                _paused_reason = "commerce_paused"
            elif is_reengagement_paused(_creator_id) if _creator_id else False:
                _skip_qwen_due_to_pause = True
                _paused_reason = "reengagement_paused"
            if _skip_qwen_due_to_pause:
                logger.info("Production control pause user=%s reason=%s", user_id, _paused_reason)
            try:
                if _creator_id:
                    from commerce.conversation_operations import get_handoff_memory

                    _ghm_pre = get_handoff_memory(_creator_id, user_id)
                    if _ghm_pre and _ghm_pre.active:
                        _allowed_pre = False
                        _reason_pre = f"handoff:{_ghm_pre.reason}"
            except Exception:
                pass
            try:
                _h = record_metric(
                    name="commercial_objective_selected",
                    creator_id=_creator_id,
                    user_id=user_id,
                    strategy=_var,
                    topic=None,
                    product_family=_prod_family,
                    lifecycle=_lc,
                    objective=_commercial_objective,
                )
            except Exception:
                pass
            try:
                _PcAudit = OperationalAuditRecord
                _audit = _PcAudit(
                    creator_id=_creator_id,
                    user_id=user_id,
                    objective=_commercial_objective,
                    strategy=_var,
                    risk_state=_risk,
                    pressure=_pressure,
                    allowed=_pc_allowed_pre,
                    reason=_reason_pre,
                )
                record_audit(_audit)
            except Exception:
                pass
            _obj_for_gate = _commercial_objective
        except Exception:
            logger.debug(
                "Commercial objective / production control gate failed for user=%s", user_id
            )

        # ── Operational intelligence ───────────────────────────────────────
        _op_health = None
        _op_loops = None
        _op_decision = None
        try:
            from commerce.operational_intelligence import operational_decision
            from commerce.operational_execution import (
                execute_operational_recommendation,
                evaluate_production_health,
                MetricWindow,
            )

            _op_decision = operational_decision(
                creator_id=_creator_id,
                user_id=user_id,
                signals=_commerce_signals,
                pressure=_pressure,
                risk_state=_risk,
            )
            if _op_decision:
                _telemetry_data.operational_intel_action = str(_op_decision.get("action", "none"))
            try:
                _op_health = evaluate_production_health(_creator_id, _creator_id, user_id)
                _op_loops = _op_health.get("open_loops", []) if _op_health else []
            except Exception:
                pass
            try:
                _tmp_topic = None
                try:
                    _tmp_topic = _conv_state.get("current_topic") if _conv_state else None
                except Exception:
                    pass
                retrieve_relevant_memories_fn = None
                try:
                    from commerce.long_term_memory import retrieve_relevant_memories

                    retrieve_relevant_memories_fn = retrieve_relevant_memories
                except Exception:
                    pass
                if _op_decision and _op_decision.get("recommendation"):
                    await execute_operational_recommendation(
                        _op_decision,
                        user_id=user_id,
                        creator_id=_creator_id,
                    )
            except Exception:
                pass
        except Exception:
            logger.debug("Operational intelligence failed for user=%s", user_id)

        # ── Persona behavior with knowledge (deterministic, always runs) ───
        _persona_behavior_state = None
        _behavior_block = None
        _structured_for_behavior = _persona_snapshot
        _recent_for_behavior = None
        _fan_know_for_behavior = _fan_knowledge or None
        _cur_for_beh = None
        _open_for_beh = None
        _cobj_for_beh = None
        _nba_for_beh = None
        _legacy_provider = None
        _get_user_for_auth_cached = None
        try:
            # Phase 5: _conv_state may be a ConversationState object (from the
            # authoritative snapshot) or a dict (legacy path); read tolerantly
            # so persona derivation is not skipped on the canonical path.
            if isinstance(_conv_state, dict):
                _cur_for_beh = _conv_state.get("current_topic") if _conv_state else None
                _open_for_beh = _conv_state.get("open_threads") if _conv_state else None
            else:
                _cur_for_beh = getattr(_conv_state, "current_topic", None) if _conv_state else None
                _open_for_beh = getattr(_conv_state, "open_threads", None) if _conv_state else None
            _cobj_for_beh = _commercial_objective
            _nba_for_beh = _action_str
            _recent_for_behavior = _op_loops if _op_loops else None
            retrieve_relevant_knowledge_fn = None
            try:
                from commerce.fan_knowledge import retrieve_relevant_knowledge

                retrieve_relevant_knowledge_fn = retrieve_relevant_knowledge
                if retrieve_relevant_knowledge_fn and _creator_id:
                    # Phase 5: actual signature is
                    # (creator_id, user_id, current_topic, open_threads,
                    # limit, profile) — the previous ``query=`` argument
                    # never existed (TypeError → fail-open every turn).
                    if isinstance(_conv_state, dict):
                        _beh_topic = _conv_state.get("current_topic") if _conv_state else None
                        _beh_threads = (
                            _conv_state.get("open_threads", ()) if _conv_state else ()
                        ) or ()
                    else:
                        _beh_topic = (
                            getattr(_conv_state, "current_topic", None) if _conv_state else None
                        )
                        _beh_threads = (
                            getattr(_conv_state, "open_threads", ()) if _conv_state else ()
                        )
                    if not isinstance(_beh_threads, (list, tuple)):
                        _beh_threads = ()
                    _fan_know_for_behavior = await retrieve_relevant_knowledge(
                        _creator_id,
                        user_id,
                        current_topic=_beh_topic if isinstance(_beh_topic, str) else None,
                        open_threads=tuple(t for t in _beh_threads if isinstance(t, str)),
                        limit=5,
                    )
            except Exception:
                pass
            from commerce.persona_behavior import (
                derive_persona_behavior_state,
                render_persona_behavior_block,
            )

            _persona_behavior_state = derive_persona_behavior_state(
                structured_persona=_structured_for_behavior,
                conversation_state=_conv_state,
                fan_message=user_message,
                fan_knowledge=_fan_know_for_behavior,
                recent_assistant_messages=_recent_for_behavior,
                commerce_objective=_cobj_for_beh,
                next_best_action=_nba_for_beh,
                creator_id=_creator_id,
                generation_id=generation_id,
            )
            _behavior_block = render_persona_behavior_block(
                _persona_behavior_state,
            )
            if _behavior_block:
                context.append({"role": "system", "content": _behavior_block})
            # Phase 5 remediation (Defect 2): carry the same persona-behavior
            # realization-guidance block on the frozen AuthoritativeState so
            # the canonical OneCall path — which rebuilds from the snapshot
            # via context_compact.py and ignores the legacy ``context`` list
            # above — receives it. No second renderer, no semantic change;
            # empty renders nothing downstream. Fail-open.
            try:
                if _authoritative_state is not None:
                    object.__setattr__(
                        _authoritative_state,
                        "behavior_block_text",
                        _behavior_block or "",
                    )
            except Exception:
                pass
            _telemetry_data.persona_behavior_derived = True
            # G13/G14 evidence-ledger emission only (fail-open; no behavior change).
            # emotional_state/conversation_mode + response_mode/question_policy reuse
            # existing schema fields (no persist columns -> logger.info twins are
            # the durable observable). teasing_allowed is log-only by rule (no
            # schema field; schema+to_dict+persist left untouched per constraints).
            # Wiring finding (logged, NOT rewired): _cstate never carries
            # "response_mode"/"question_policy" keys, so _response_mode /
            # _question_policy read at :1436-1437 are always None.
            try:
                try:
                    _telemetry_data.emotional_state = getattr(
                        _persona_behavior_state, "emotional_state", None
                    )
                except Exception:
                    pass
                try:
                    _telemetry_data.conversation_mode = getattr(
                        _persona_behavior_state, "conversation_mode", None
                    )
                except Exception:
                    pass
                logger.info(
                    "G13 persona_mode generation_id=%s emotional_state=%s conversation_mode=%s "
                    "teasing_allowed=%s tease_reason=%s",
                    generation_id,
                    getattr(_persona_behavior_state, "emotional_state", None),
                    getattr(_persona_behavior_state, "conversation_mode", None),
                    bool(getattr(_persona_behavior_state, "teasing_allowed", False)),
                    getattr(_persona_behavior_state, "tease_reason", None),
                )
            except Exception:
                pass
            try:
                try:
                    _telemetry_data.response_mode = (
                        _response_mode if isinstance(_response_mode, str) else None
                    )
                except Exception:
                    pass
                try:
                    _telemetry_data.question_policy = (
                        _question_policy if isinstance(_question_policy, str) else None
                    )
                except Exception:
                    pass
                try:
                    _g14_has_key = bool(isinstance(_cstate, dict) and "response_mode" in _cstate)
                except Exception:
                    _g14_has_key = False
                logger.info(
                    "G14 response_mode generation_id=%s response_mode=%s question_policy=%s "
                    "cstate_has_response_mode_key=%s",
                    generation_id,
                    _response_mode,
                    _question_policy,
                    _g14_has_key,
                )
            except Exception:
                pass
        except Exception:
            logger.debug("Persona behavior with knowledge failed for user=%s", user_id)

        # ── Phase 5 relationship-aware context (deterministic, fail-open) ──
        # ONE bounded selection per turn over already-derived state plus
        # existing retrieval output. The rendered block is advisory data
        # (never directives); the same selection also informs Phase 4
        # previous-context evidence below. Empty selection renders nothing
        # and leaves the prompt byte-identical. Never blocks generation,
        # commerce, routing, sending, or accumulation.
        _relationship_selection = None
        _relationship_block = ""
        try:
            from context_engine.relationship_context import (
                assemble_relationship_context as _assemble_rel_ctx,
                render_relationship_context as _render_rel_ctx,
            )

            _rel_summary = None
            try:
                if _authoritative_state is not None:
                    _rel_summary = getattr(_authoritative_state, "summary", None)
            except Exception:
                _rel_summary = None
            _relationship_selection = await _assemble_rel_ctx(
                creator_id=_creator_id,
                user_id=user_id,
                current_message=user_message,
                conversation_state=_conv_state,
                profile=_cached_profile_for_commerce,
                summary=_rel_summary if isinstance(_rel_summary, str) else None,
            )
            _relationship_block = _render_rel_ctx(_relationship_selection)
            if _relationship_block:
                context.append({"role": "system", "content": _relationship_block})
            try:
                if _authoritative_state is not None:
                    # Frozen dataclass: same object.__setattr__ pattern used
                    # for participants/contract in authoritative_assembly.
                    object.__setattr__(
                        _authoritative_state, "relationship_context_text", _relationship_block
                    )
            except Exception:
                pass
        except Exception:
            logger.debug("Relationship context assembly failed for user=%s (fail-open)", user_id)
            _relationship_selection = None
            _relationship_block = ""

        # ── Phase 6 descriptive intimacy context (deterministic, fail-open) ──
        # ONE bounded selection per turn over the descriptive intimacy
        # trajectory plus current-turn intimacy evidence. The rendered
        # block is advisory data (never permission, consent, safety
        # policy, or commerce). Empty selection renders nothing and
        # leaves the prompt byte-identical. Never blocks generation,
        # commerce, routing, sending, or accumulation.
        _intimacy_evidence = None
        _intimacy_selection = None
        _intimacy_block = ""
        _int_prior_ref = False
        try:
            from commerce.intimacy_evidence import extract_intimacy_evidence
            from context_engine.intimacy_context import (
                assemble_intimacy_context as _assemble_int_ctx,
                has_intimate_context_reference as _has_int_ref,
                render_intimacy_context as _render_int_ctx,
            )

            _int_history = None
            try:
                if _authoritative_state is not None:
                    _int_history = list(
                        getattr(_authoritative_state, "recent_messages", None) or []
                    )
            except Exception:
                _int_history = None
            try:
                _intimacy_evidence = extract_intimacy_evidence(
                    user_message=user_message,
                    history=_int_history,
                    conversation_state=_conv_state,
                    llm_signals=_commerce_signals,
                    now=datetime.now(timezone.utc),
                )
            except Exception:
                logger.debug("Intimacy evidence extraction failed for user=%s (fail-open)", user_id)
                _intimacy_evidence = None
            _intimacy_selection = await _assemble_int_ctx(
                creator_id=_creator_id,
                user_id=user_id,
                current_message=user_message,
                conversation_state=_conv_state,
                profile=_cached_profile_for_commerce,
                evidence=_intimacy_evidence,
            )
            _intimacy_block = _render_int_ctx(_intimacy_selection)
            if _intimacy_block:
                context.append({"role": "system", "content": _intimacy_block})
            try:
                if _authoritative_state is not None:
                    # Frozen dataclass: same object.__setattr__ pattern
                    # used for the Phase 5 snapshot carriers.
                    object.__setattr__(
                        _authoritative_state, "intimacy_context_text", _intimacy_block
                    )
            except Exception:
                pass
            try:
                _int_prior_ref = bool(_has_int_ref(_intimacy_selection))
            except Exception:
                _int_prior_ref = False
        except Exception:
            logger.debug("Intimacy context assembly failed for user=%s (fail-open)", user_id)
            _intimacy_evidence = None
            _intimacy_selection = None
            _intimacy_block = ""
            _int_prior_ref = False

        # ── Phase 7 boundary context (deterministic, fail-open guidance) ──
        # ONE bounded selection per turn over durable constraints plus
        # current-turn evidence (current evidence already merged into the
        # effective snapshot above, so it outranks historical
        # intimacy/relationship here). The rendered block is advisory
        # constraint guidance with explicit precedence over persona style;
        # it is NOT the enforcement mechanism (enforcement is output
        # validation plus routing/commerce veto below). Empty selection
        # renders nothing and leaves the prompt byte-identical. Never
        # blocks generation, commerce, routing, sending, or accumulation.
        # Canonical ordering: RELATIONSHIP → INTIMACY → BOUNDARY →
        # STRATEGY → PERSONA BEHAVIOR (see phase5_snapshot_blocks).
        _boundary_selection = None
        _boundary_block = ""
        try:
            from context_engine.boundary_context import (
                assemble_boundary_context as _assemble_bdry_ctx,
                render_boundary_context as _render_bdry_ctx,
            )

            _boundary_selection = await _assemble_bdry_ctx(
                creator_id=_creator_id,
                user_id=user_id,
                profile=_cached_profile_for_commerce,
                evidence=_boundary_evidence,
                snapshot=_boundary_snapshot,
            )
            _boundary_block = _render_bdry_ctx(_boundary_selection)
            if _boundary_block:
                context.append({"role": "system", "content": _boundary_block})
            try:
                if _authoritative_state is not None:
                    # Frozen dataclass: same object.__setattr__ pattern
                    # used for the Phase 5/6 snapshot carriers.
                    object.__setattr__(
                        _authoritative_state, "boundary_context_text", _boundary_block or ""
                    )
            except Exception:
                pass
        except Exception:
            logger.debug("Boundary context assembly failed for user=%s (fail-open)", user_id)
            _boundary_selection = None
            _boundary_block = ""

        # ── Phase 8 content-transition guidance (deterministic, fail-open) ──
        # ONE bounded selection per turn over current-turn deterministic
        # content-interest evidence plus the already-computed effective
        # boundary snapshot (Phase 7 veto). The rendered block is advisory
        # categorical guidance (never commerce authority, never a strategy
        # move, never an offer command). Empty selection renders nothing
        # and leaves the prompt byte-identical. Never blocks generation,
        # commerce, routing, sending, or accumulation. Phase 8 sits
        # upstream of existing commerce authority: downstream commerce
        # evaluation, free-photo routing, opportunity eligibility/ranking/
        # sealing/execution, and the Phase 7 common validator all run
        # unchanged. Canonical ordering: RELATIONSHIP → INTIMACY →
        # BOUNDARY → CONTENT TRANSITION → STRATEGY → PERSONA BEHAVIOR
        # (see phase5_snapshot_blocks).
        # Anti-funnel by construction: relationship warmth, intimacy,
        # desire, temperature, history, and product inventory are not
        # inputs here and can never activate a transition; LLM signals
        # are never consulted (structural neutrality).
        _transition_evidence = None
        _transition_decision = None
        _transition_block = ""
        try:
            from commerce.content_transition_evidence import (
                extract_content_transition_evidence as _extract_transition_evidence,
            )
            from context_engine.content_transition_context import (
                render_content_transition_context as _render_transition_ctx,
            )
            from context_engine.content_transition_context import (
                select_content_transition_context as _select_transition_ctx,
            )

            # Trustworthy thread-continuation anchors only: current_topic /
            # open_threads arrive via _conv_state; LTM open-loop subjects
            # come from the already-extracted explicit memories of this
            # turn (no new retrieval, no commerce.open_loop, never
            # ConversationContract.maintain_topic).
            _transition_loop_subjects: list[str] = []
            try:
                for _mem in _ltm_memories or []:
                    if isinstance(_mem, dict):
                        _mtype = str(_mem.get("memory_type", "") or "").lower()
                        if _mtype in ("open_loop", "commitment", "promise", "plan"):
                            _subj = _mem.get("subject") or _mem.get("value")
                            if isinstance(_subj, str) and _subj.strip():
                                _transition_loop_subjects.append(_subj.strip()[:120])
            except Exception:
                _transition_loop_subjects = []
            # Current-to-prior linkage corroboration reuses the existing
            # Phase 5 relationship selection (no second retrieval system;
            # bands/threads alone never set it).
            _transition_prior_ref = False
            try:
                from context_engine.relationship_context import (
                    has_prior_context_evidence as _has_prior_ctx_for_transition,
                )

                _transition_prior_ref = bool(_has_prior_ctx_for_transition(_relationship_selection))
            except Exception:
                _transition_prior_ref = False
            _transition_evidence = _extract_transition_evidence(
                user_message,
                conversation_state=_conv_state,
                open_loop_subjects=tuple(_transition_loop_subjects),
                has_prior_context_reference=_transition_prior_ref,
            )
            _transition_decision = _select_transition_ctx(
                evidence=_transition_evidence,
                boundary_snapshot=_boundary_snapshot,
            )
            # G2 evidence-ledger emission only (log; fail-open; no behavior change).
            # Bounded categorical vocab only; never raw text.
            try:
                _g2_ev = _transition_evidence
                _g2_dec = _transition_decision
                _g2_transition = (
                    getattr(
                        getattr(_g2_dec, "transition", None),
                        "value",
                        getattr(_g2_dec, "transition", None),
                    )
                    if _g2_dec is not None
                    else None
                )
                _g2_interest = (
                    getattr(
                        getattr(_g2_dec, "user_interest", None),
                        "value",
                        getattr(_g2_dec, "user_interest", None),
                    )
                    if _g2_dec is not None
                    else None
                )
                _g2_realization = (
                    getattr(
                        getattr(_g2_dec, "realization", None),
                        "value",
                        getattr(_g2_dec, "realization", None),
                    )
                    if _g2_dec is not None
                    else None
                )
                try:
                    _g2_reason = tuple(getattr(_g2_dec, "reason", None) or ())
                    _g2_reason0 = str(_g2_reason[0]) if _g2_reason else "NEUTRAL"
                except Exception:
                    _g2_reason0 = "NEUTRAL"
                logger.info(
                    "G2 content_transition generation_id=%s transition=%s user_interest=%s "
                    "realization=%s reason=%s explicit_request=%s curiosity=%s access_question=%s "
                    "thread_continuation=%s purchase_intent=%s current_disinterest=%s",
                    generation_id,
                    _g2_transition,
                    _g2_interest,
                    _g2_realization,
                    _g2_reason0,
                    bool(getattr(_g2_ev, "explicit_request", False))
                    if _g2_ev is not None
                    else False,
                    bool(getattr(_g2_ev, "curiosity", False)) if _g2_ev is not None else False,
                    bool(getattr(_g2_ev, "access_question", False))
                    if _g2_ev is not None
                    else False,
                    bool(getattr(_g2_ev, "thread_continuation", False))
                    if _g2_ev is not None
                    else False,
                    bool(getattr(_g2_ev, "purchase_intent", False))
                    if _g2_ev is not None
                    else False,
                    bool(getattr(_g2_ev, "current_disinterest", False))
                    if _g2_ev is not None
                    else False,
                )
            except Exception:
                pass
            _transition_block = _render_transition_ctx(_transition_decision)
            if _transition_block:
                context.append({"role": "system", "content": _transition_block})
            try:
                if _authoritative_state is not None:
                    # Frozen dataclass: same object.__setattr__ pattern
                    # used for the Phase 5/6/7 snapshot carriers.
                    object.__setattr__(
                        _authoritative_state,
                        "content_transition_context_text",
                        _transition_block or "",
                    )
            except Exception:
                pass
        except Exception:
            logger.debug("Content-transition assembly failed for user=%s (fail-open)", user_id)
            _transition_evidence = None
            _transition_decision = None
            _transition_block = ""

        # ── Phase 9 commerce-context adapter (deterministic, turn-scoped) ──
        # ONE computation per turn over Phase 8 transition + effective
        # boundary (read-only veto) + existing deterministic buy/price/photo
        # evidence. Advisory/suppressive/contextual only; zero commerce
        # authority (no product/price/selection/sealing/execution). The
        # single result below is reused by the canonical OneCall path, the
        # legacy fallback, commerce/agent drafts, and free-photo gating
        # (no duplicate legacy engine). Precedence:
        #   boundary > Phase 9 contextual guidance > commerce evaluation.
        # Phase 7 remains authoritative and is never duplicated here.
        # Relationship/intimacy trajectories are never inputs (descriptive
        # conversational context stays in the existing context/strategy
        # layer). LLM signals remain advisory and never create
        # deterministic authorization through this adapter.
        _commerce_context = None
        _phase9_det_buy = False
        _phase9_det_price = False
        _phase9_det_photo = False
        try:
            from commerce.commerce_context_adapter import (
                build_commerce_context as _build_phase9_ctx,
            )

            try:
                from commerce.purchase_intent import (
                    is_explicit_purchase_request as _is_det_buy_fn,
                )

                _phase9_det_buy = bool(_is_det_buy_fn(user_message))
            except Exception:
                _phase9_det_buy = False
            try:
                from commerce.purchase_intent import (
                    is_price_inquiry as _is_det_price_fn,
                )

                _phase9_det_price = bool(_is_det_price_fn(user_message))
            except Exception:
                _phase9_det_price = False
            try:
                from commerce.free_photo_routing import (
                    is_photo_request as _is_det_photo_fn,
                )

                # Deterministic text check only (signals=None keeps the LLM
                # advisory out of the authorization path).
                _phase9_det_photo = bool(_is_det_photo_fn(user_message, None))
            except Exception:
                _phase9_det_photo = False
            _commerce_context = _build_phase9_ctx(
                transition_decision=_transition_decision,
                transition_evidence=_transition_evidence,
                boundary_snapshot=_boundary_snapshot,
                deterministic_buy=_phase9_det_buy,
                deterministic_price=_phase9_det_price,
                deterministic_photo_request=_phase9_det_photo,
            )
        except Exception:
            logger.debug(
                "Phase 9 commerce-context failed for user=%s (fail-closed)", user_id, exc_info=True
            )
            try:
                from commerce.commerce_context_adapter import (
                    CommerceContext as _P9Ctx,
                )

                _commerce_context = _P9Ctx()  # fail-closed neutral
            except Exception:
                _commerce_context = None
        # Phase 9 telemetry: context vs authority distinction (bounded,
        # categorical, no raw text; telemetry only, never an authority input).
        try:
            if _commerce_context is not None:
                try:
                    _p9_reason = getattr(_commerce_context, "reason", ())
                    _p9_reason_str = str(_p9_reason[0]) if _p9_reason else "NEUTRAL"
                except Exception:
                    _p9_reason_str = "NEUTRAL"
                _telemetry_data.commerce_context = _p9_reason_str[:64]  # type: ignore
                _telemetry_data.commerce_authorization_basis = str(
                    getattr(_commerce_context, "authorization_basis", "NONE")
                )[:32]  # type: ignore
                _telemetry_data.commerce_user_initiated = bool(
                    getattr(_commerce_context, "user_initiated_commercial", False)
                )  # type: ignore
                _telemetry_data.commerce_warmth_without_evidence = bool(
                    getattr(_commerce_context, "warmth_without_commercial_evidence", True)
                )  # type: ignore
                # G1/G3 evidence-ledger emission only (log; fail-open; no behavior change).
                # G1: deterministic verifier bools (already computed above, reused).
                # G3: disinterest/current-interest/continuation have NO telemetry
                # schema column (insert_generation_telemetry persists only
                # commerce_context + commerce_authorization_basis), so log-only
                # by rule (never new ad-hoc telemetry attrs).
                try:
                    logger.info(
                        "G1 determ_verifiers generation_id=%s buy=%s price=%s photo=%s",
                        generation_id,
                        bool(_phase9_det_buy),
                        bool(_phase9_det_price),
                        bool(_phase9_det_photo),
                    )
                except Exception:
                    pass
                try:
                    logger.info(
                        "G3 phase9_interest generation_id=%s disinterest_present=%s "
                        "current_content_interest=%s continuation_context=%s",
                        generation_id,
                        bool(getattr(_commerce_context, "disinterest_present", False)),
                        bool(getattr(_commerce_context, "current_content_interest", False)),
                        bool(getattr(_commerce_context, "continuation_context", False)),
                    )
                except Exception:
                    pass
        except Exception:
            pass

        # ── Phase 4 conversational strategy (deterministic, fail-open) ────
        # Pure selector over trajectory snapshot + turn context + hard
        # constraints. Appends one advisory block or nothing. Never blocks
        # generation, commerce, routing, sending, or accumulation.
        _conversation_strategy = None
        try:
            from commerce.conversation_strategy import (
                TurnEvidenceSummary as _TurnSummary,
                detect_farewell as _detect_farewell,
                render_conversation_strategy as _render_strategy,
                select_for_turn as _select_for_turn,
            )
            from commerce.relationship_evidence import (
                extract_turn_evidence as _extract_evidence,
            )

            _strategy_turn = None
            try:
                _strategy_history = None
                try:
                    if _authoritative_state is not None:
                        _strategy_history = list(
                            getattr(_authoritative_state, "recent_messages", None) or []
                        )
                except Exception:
                    _strategy_history = None
                _strategy_lifecycle = None
                try:
                    if isinstance(_conv_state, dict):
                        _strategy_lifecycle = _conv_state.get("lifecycle") if _conv_state else None
                    else:
                        _strategy_lifecycle = getattr(_conv_state, "lifecycle", None)
                    if not isinstance(_strategy_lifecycle, str):
                        _strategy_lifecycle = None
                except Exception:
                    _strategy_lifecycle = None
                _strategy_evidence = _extract_evidence(
                    user_message=user_message,
                    history=_strategy_history,
                    conversation_state=_conv_state,
                    lifecycle=_strategy_lifecycle,
                    assistant_valid=False,
                    now=datetime.now(timezone.utc),
                )
                # Phase 5: the same selected relationship context that the
                # LLM receives also informs previous-context evidence, so
                # CALLBACK becomes reachable when actual relevant historical
                # context was selected — without a second retrieval system.
                # Bands/threads alone never set this (the selector requires
                # overlapping supporting facts for has_prior_context).
                _rel_prior = False
                try:
                    from context_engine.relationship_context import (
                        has_prior_context_evidence as _has_prior_ctx,
                    )

                    _rel_prior = bool(_has_prior_ctx(_relationship_selection))
                except Exception:
                    _rel_prior = False
                # Phase 6: a genuine current-turn reference to prior
                # intimate context (selected intimacy context with real
                # current-turn support — never bands alone) is also a
                # reference to previous context in the existing Phase 4
                # evidence shape. No new strategy input; no Phase 4 change.
                # (_int_prior_ref is always bound by the Phase 6 block
                # above; fail-open False.)
                _strategy_turn = _TurnSummary(
                    user_asked_question=bool(_strategy_evidence.user_asked_question),
                    user_answered_question=bool(_strategy_evidence.user_answered_question),
                    user_continued_topic=bool(_strategy_evidence.user_continued_topic),
                    user_referenced_previous_context=bool(
                        _strategy_evidence.user_referenced_previous_context
                    )
                    or _rel_prior
                    or _int_prior_ref,
                    assistant_asked_question=False,
                    assistant_shared_information=False,
                    farewell=_detect_farewell(user_message),
                )
            except Exception:
                try:
                    _strategy_turn = _TurnSummary(farewell=_detect_farewell(user_message))
                except Exception:
                    _strategy_turn = None
            _strategy_contract = None
            try:
                if _authoritative_state is not None:
                    _strategy_contract = getattr(
                        _authoritative_state, "conversation_contract", None
                    )
            except Exception:
                _strategy_contract = None
            # Phase 5 remediation (Defect 1): the canonical production path
            # supplies the frozen AuthoritativeState.profile
            # (MappingProxyType), which select_for_turn() rejects via its
            # strict isinstance(profile, dict) guard. Convert once at this
            # caller boundary using the repository's established
            # to_plain_dict() utility. Strategy semantics unchanged.
            _profile_for_strategy = _cached_profile_for_commerce
            try:
                from context_engine.relationship_context import (
                    to_plain_dict as _to_plain_dict_for_strategy,
                )

                if _profile_for_strategy is not None:
                    _profile_for_strategy = _to_plain_dict_for_strategy(_profile_for_strategy)
            except Exception:
                _profile_for_strategy = _cached_profile_for_commerce
            _conversation_strategy = _select_for_turn(
                profile=_profile_for_strategy,
                creator_id=_creator_id,
                conversation_state=_conv_state,
                contract=_strategy_contract,
                persona=_persona_behavior_state,
                turn=_strategy_turn,
            )
            # Phase 7: constrain the selected move by active boundaries.
            # The Phase 4 selector above is untouched; this post-selection
            # uses only the existing move/hint/question vocabulary
            # (STOP → safe-default ACKNOWLEDGE; question barred →
            # no-question variants). Never a new move, never TEASE.
            try:
                from context_engine.boundary_context import (
                    constrain_strategy_for_boundary as _constrain_strategy,
                )

                _constrained_strategy = _constrain_strategy(
                    _conversation_strategy, _boundary_snapshot
                )
                if _constrained_strategy is not None:
                    _conversation_strategy = _constrained_strategy
            except Exception:
                pass
            if _conversation_strategy is not None:
                _strategy_block = _render_strategy(_conversation_strategy)
                if _strategy_block:
                    context.append({"role": "system", "content": _strategy_block})
            # Phase 5: carry the strategy block on the authoritative snapshot
            # so the production OneCall path — which rebuilds messages from
            # the snapshot and ignores the legacy ``context`` list above —
            # receives the same strategy block as the legacy path. Empty
            # strategy renders nothing (abstention stays byte-identical).
            try:
                if _authoritative_state is not None:
                    _snap_strategy_text = ""
                    try:
                        if _conversation_strategy is not None:
                            _snap_strategy_text = _render_strategy(_conversation_strategy) or ""
                    except Exception:
                        _snap_strategy_text = ""
                    object.__setattr__(
                        _authoritative_state, "strategy_block_text", _snap_strategy_text
                    )
            except Exception:
                pass
        except Exception:
            logger.debug("Conversational strategy failed for user=%s (fail-open)", user_id)

        # ── Draft generation + scoring ─────────────────────────────────────
        draft = ""
        score = 0.0
        flags: list[str] = []
        # Phase 5/H3 routing signals. needs_handoff is the merged signal
        # (advisory + deterministic); advisory_handoff is the raw LLM flag;
        # safety_hard_block tracks deterministic safety/persona hard blocks.
        # Legacy path leaves handoff signals False (no advisory source there).
        _routing_needs_handoff = False
        _routing_advisory_handoff = False
        _routing_safety_block = False
        _generation_valid = False
        _generation_end = None
        _scoring_start = None
        _scoring_end = None
        _use_agent = False
        _agent_state = None
        _agent_result = None
        _agent_provider = None

        # ── Context Engine retrieval (78D: lexical RapidFuzz + MiniLM semantic hybrid) ──
        # Moved after conversation_state so state relevance is real, not neutral.
        # Deterministic sampling for canary: hash(user_id) %100 < pct*100
        try:
            from context_engine.worker_integration import observe_context_engine as _observe_ce

            _ce_enabled_raw = bool(
                getattr(_settings, "context_engine_observational", False)
                or getattr(_settings, "context_engine_enabled", False)
            )
            _ce_sample_rate = float(getattr(_settings, "context_engine_sample_rate", 0.0) or 0.0)
            # Also consider legacy canary sample_rate if observational true and new rate is 0
            if _ce_enabled_raw and _ce_sample_rate == 0.0:
                try:
                    _ce_sample_rate = float(
                        getattr(_settings, "context_engine_canary_sample_rate", 0.0) or 0.0
                    )
                except Exception:
                    pass
            if _ce_enabled_raw and 0.0 < _ce_sample_rate < 1.0:
                import hashlib as _ce_hash

                # Creator isolation: same fan with different creator must be independent cohort
                _sample_key = (
                    f"{_creator_id}:{user_id}" if _creator_id is not None else str(user_id)
                )
                _h = int(_ce_hash.sha256(_sample_key.encode()).hexdigest()[:8], 16) % 100
                _ce_should_run = _h < int(_ce_sample_rate * 100)
            else:
                _ce_should_run = _ce_enabled_raw

            # Pass authoritative conversation_state for state relevance scoring (single derivation)
            _ce_conv_state = (
                _conv_state
                if isinstance(_conv_state, dict)
                else (getattr(_conv_state, "__dict__", None) if _conv_state else None)
            )
            # Phase 2: also pass authoritative_state snapshot for reuse (no refetch)
            _obs_enabled = bool(_ce_should_run)
            # Telemetry: retrieval_enabled
            try:
                _telemetry_data.retrieval_enabled = _obs_enabled  # type: ignore[attr-defined]
            except Exception:
                pass
            _t_ce_start = _time.monotonic()
            _context_engine_observation = await _observe_ce(
                user_id=user_id,
                creator_id=_creator_id,
                user_message=user_message,
                generation_id=generation_id,
                persona_snapshot=_persona_snapshot,
                enabled=_obs_enabled,
                conversation_state=_ce_conv_state,  # type: ignore[arg-type]
                authoritative_state=_authoritative_state,
            )
            if _context_engine_observation and _context_engine_observation.enabled:
                _telemetry_data.context_engine_enabled = True
                _telemetry_data.context_engine_ms = _context_engine_observation.total_ms
                _telemetry_data.context_engine_gather_ms = _context_engine_observation.gather_ms
                _telemetry_data.context_engine_candidates = (
                    _context_engine_observation.candidate_count
                )
                _telemetry_data.context_engine_selected = _context_engine_observation.selected_count
                _telemetry_data.context_engine_dropped = _context_engine_observation.dropped_count
                _telemetry_data.context_engine_tokens = _context_engine_observation.token_count
                _telemetry_data.context_engine_chars = _context_engine_observation.char_count
                # Phase 87: truthful retrieval instrumentation (no placeholders)
                try:
                    _rm = getattr(_context_engine_observation, "retrieval_metrics", None)
                    if _rm:
                        _telemetry_data.lexical_candidate_count = int(
                            _rm.get("lexical_candidate_count", 0)
                        )  # type: ignore
                        _telemetry_data.semantic_candidate_count = int(
                            _rm.get("semantic_candidate_count", 0)
                        )  # type: ignore
                        _telemetry_data.merged_candidate_count = int(
                            _rm.get(
                                "merged_candidate_count",
                                _context_engine_observation.candidate_count,
                            )
                        )  # type: ignore
                        _telemetry_data.lexical_latency_ms = int(_rm.get("lexical_latency_ms", 0))  # type: ignore
                        _telemetry_data.embedding_latency_ms = int(
                            _rm.get("embedding_latency_ms", 0)
                        )  # type: ignore
                        _telemetry_data.retrieval_latency_ms = int(
                            _rm.get("total_retrieval_ms", _context_engine_observation.gather_ms)
                        )  # type: ignore
                        _telemetry_data.retrieval_degraded = bool(_rm.get("degraded", False))  # type: ignore
                        _telemetry_data.lexical_threshold = int(_rm.get("lexical_threshold", 80))  # type: ignore
                        _telemetry_data.semantic_threshold = float(
                            _rm.get("semantic_threshold", 0.30)
                        )  # type: ignore
                    else:
                        _telemetry_data.lexical_candidate_count = 0  # type: ignore
                        _telemetry_data.semantic_candidate_count = 0  # type: ignore
                        _telemetry_data.merged_candidate_count = (
                            _context_engine_observation.candidate_count
                        )  # type: ignore
                        _telemetry_data.retrieval_latency_ms = int(
                            _context_engine_observation.gather_ms
                        )  # type: ignore
                        _telemetry_data.embedding_latency_ms = None  # type: ignore
                        _telemetry_data.lexical_latency_ms = None  # type: ignore
                    try:
                        _telemetry_data.ranking_latency_ms = int(
                            getattr(_context_engine_observation, "score_ms", 0) or 0
                        )  # type: ignore
                        _telemetry_data.conflict_dropped_count = int(
                            getattr(_context_engine_observation, "conflict_dropped", 0) or 0
                        )  # type: ignore
                        _telemetry_data.lexical_dedup_removed_count = int(
                            getattr(_context_engine_observation, "lexical_dedup_removed", 0) or 0
                        )  # type: ignore
                        _telemetry_data.total_deduplication_count = int(
                            getattr(_context_engine_observation, "dropped_count", 0) or 0
                        )  # type: ignore
                        _telemetry_data.context_tokens = int(
                            getattr(_context_engine_observation, "token_count", 0) or 0
                        )  # type: ignore
                        _telemetry_data.context_category_tokens = getattr(
                            _context_engine_observation, "category_tokens", None
                        )  # type: ignore
                        _telemetry_data.context_degradation_level = int(
                            getattr(_context_engine_observation, "degradation_level", 0) or 0
                        )  # type: ignore
                        _telemetry_data.context_truncation_count = int(
                            getattr(_context_engine_observation, "truncation_count", 0) or 0
                        )  # type: ignore
                        _telemetry_data.context_budget_violation_count = int(
                            getattr(_context_engine_observation, "budget_violations", 0) or 0
                        )  # type: ignore
                        _telemetry_data.context_engine_ms = float(
                            _context_engine_observation.total_ms
                        )  # type: ignore
                    except Exception:
                        pass
                except Exception:
                    pass
                if (
                    not _context_engine_observation.failed
                    and _context_engine_observation.rendered_text
                ):
                    _retrieved_context = _context_engine_observation.rendered_text
            # Track observed vs enabled
            _telemetry_data.context_engine_observed = bool(
                _context_engine_observation and _context_engine_observation.enabled
            )
            # Relationship V2 shadow observation (Stage D3): metadata only.
            # Flag-gated, fail-open; never controls replies, sends, or state.
            try:
                from relationship_v2.worker_hook import (
                    observe_turn_context as _observe_v2_turn,
                )

                _v2_turn_observation = await _observe_v2_turn(
                    creator_id=_creator_id,
                    user_id=user_id,
                    generation_id=generation_id,
                )
                if _v2_turn_observation is not None:
                    logger.debug(
                        "v2 shadow context gen=%s chars=%s sections=%s recall=%s commerce_ok=%s ms=%.1f",
                        generation_id,
                        _v2_turn_observation.total_chars,
                        _v2_turn_observation.section_count,
                        _v2_turn_observation.recall_count,
                        _v2_turn_observation.commerce_ok,
                        _v2_turn_observation.elapsed_ms,
                    )
            except Exception:
                logger.debug("v2 shadow observation skipped", exc_info=True)
            # Retrieval latency telemetry
            try:
                _telemetry_data.retrieval_enabled = _obs_enabled  # type: ignore
            except Exception:
                pass
            # Phase 87: emit RETRIEVAL_READY and CONTEXT_READY (fail-open, creator-safe)
            try:
                from core.phase87_events import emit_retrieval_ready, emit_context_ready

                if (
                    _context_engine_observation
                    and _context_engine_observation.enabled
                    and not _context_engine_observation.failed
                ):
                    _rm2 = getattr(_context_engine_observation, "retrieval_metrics", None) or {}
                    _cand = int(_context_engine_observation.candidate_count or 0)
                    _lex = int(_rm2.get("lexical_candidate_count", 0) if _rm2 else 0)
                    _sem = int(_rm2.get("semantic_candidate_count", 0) if _rm2 else 0)
                    _merged = int(_rm2.get("merged_candidate_count", _cand) if _rm2 else _cand)
                    _ret_ms = float(
                        _rm2.get("total_retrieval_ms", _context_engine_observation.gather_ms)
                        if _rm2
                        else _context_engine_observation.gather_ms
                    )
                    _emb_ms = float(_rm2.get("embedding_latency_ms", 0) if _rm2 else 0)
                    _deg = bool(_rm2.get("degraded", False) if _rm2 else False)
                    await emit_retrieval_ready(
                        user_id=user_id,
                        creator_id=_creator_id,
                        generation_id=generation_id,
                        runtime_mode=_runtime_mode_label,
                        candidate_count=_cand,
                        lexical_candidate_count=_lex,
                        semantic_candidate_count=_sem,
                        merged_candidate_count=_merged,
                        retrieval_latency_ms=_ret_ms,
                        embedding_latency_ms=_emb_ms,
                        lexical_threshold=80,
                        semantic_threshold=0.30,
                        degraded=_deg,
                    )
                    _selected = int(_context_engine_observation.selected_count or 0)
                    _conflict = int(
                        getattr(_context_engine_observation, "conflict_dropped", 0) or 0
                    )
                    _lex_dedup = int(
                        getattr(_context_engine_observation, "lexical_dedup_removed", 0) or 0
                    )
                    _total_dedup = int(
                        getattr(_context_engine_observation, "dropped_count", 0) or 0
                    )
                    _tokens = int(getattr(_context_engine_observation, "token_count", 0) or 0)
                    _cat_tokens = (
                        getattr(_context_engine_observation, "category_tokens", None) or {}
                    )
                    _degrad = int(getattr(_context_engine_observation, "degradation_level", 0) or 0)
                    _trunc = int(getattr(_context_engine_observation, "truncation_count", 0) or 0)
                    _viol = int(getattr(_context_engine_observation, "budget_violations", 0) or 0)
                    _gather_ms = float(_context_engine_observation.gather_ms or 0)
                    _score_ms = float(getattr(_context_engine_observation, "score_ms", 0) or 0)
                    _dedup_ms = float(getattr(_context_engine_observation, "dedup_ms", 0) or 0)
                    _budget_ms = float(getattr(_context_engine_observation, "budget_ms", 0) or 0)
                    _render_ms = float(getattr(_context_engine_observation, "render_ms", 0) or 0)
                    _total_ctx_ms = float(_context_engine_observation.total_ms or 0)
                    await emit_context_ready(
                        user_id=user_id,
                        creator_id=_creator_id,
                        generation_id=generation_id,
                        runtime_mode=_runtime_mode_label,
                        candidate_count=_cand,
                        selected_count=_selected,
                        conflict_dropped_count=_conflict,
                        lexical_dedup_count=_lex_dedup,
                        total_deduplication_count=_total_dedup,
                        total_tokens=_tokens,
                        category_tokens=_cat_tokens,
                        degradation_level=_degrad,
                        truncation_count=_trunc,
                        budget_violations=_viol,
                        gather_ms=_gather_ms,
                        ranking_ms=_score_ms,
                        dedup_ms=_dedup_ms,
                        budget_ms=_budget_ms,
                        render_ms=_render_ms,
                        total_context_ms=_total_ctx_ms,
                    )
                else:
                    await emit_retrieval_ready(
                        user_id=user_id,
                        creator_id=_creator_id,
                        generation_id=generation_id,
                        runtime_mode=_runtime_mode_label,
                        candidate_count=0,
                        lexical_candidate_count=0,
                        semantic_candidate_count=0,
                        merged_candidate_count=0,
                        retrieval_latency_ms=None,
                        embedding_latency_ms=None,
                        lexical_threshold=80,
                        semantic_threshold=0.30,
                        degraded=True,
                    )
                    await emit_context_ready(
                        user_id=user_id,
                        creator_id=_creator_id,
                        generation_id=generation_id,
                        runtime_mode=_runtime_mode_label,
                        candidate_count=0,
                        selected_count=0,
                        conflict_dropped_count=0,
                        lexical_dedup_count=0,
                        total_deduplication_count=0,
                        total_tokens=0,
                        category_tokens={},
                        degradation_level=0,
                        truncation_count=0,
                        budget_violations=0,
                        gather_ms=0,
                        ranking_ms=0,
                        dedup_ms=0,
                        budget_ms=0,
                        render_ms=0,
                        total_context_ms=0,
                    )
            except Exception:
                pass
        except Exception:
            _telemetry_data.context_engine_observed = False

        _llm_path = getattr(_settings, "llm_path", "legacy")

        if _llm_path == "new":
            # ── ONE-CALL PATH: single Qwen2.5 generation ──────────────────
            # Phase 87: per-turn generation counters (creator-safe, fail-open)
            _one_call_count = 0
            _ppv_second_generation_count = 0
            _legacy_generation_count = 0
            _total_llm_calls = 0
            _one_call_provider = ""
            _one_call_model = ""
            _one_call_input_tokens = None
            _one_call_output_tokens = None
            _one_call_latency_ms = None
            _validation_outcome = None
            _authority_action = None
            _authority_status = None
            _authority_product_id = None
            _authority_price_minor = None
            _authority_currency = None
            _commerce_price_authority = None
            _handoff_reason_for_telemetry = None
            _one_call_start = _time.monotonic()
            _phase87_one_call_started = False
            # Emit ONE_CALL_START (fail-open, creator-safe)
            try:
                from core.phase87_events import (
                    emit_one_call_start,
                    emit_one_call_success,
                    emit_one_call_failed,
                )

                _tmp_provider = getattr(_settings, "llm_provider", "llamacpp")
                _tmp_model = getattr(_settings, "llama_model", "default")
                await emit_one_call_start(
                    user_id=user_id,
                    creator_id=_creator_id,
                    generation_id=generation_id,
                    runtime_mode=_runtime_mode_label,
                    provider_name=_tmp_provider,
                    model_name=_tmp_model,
                    call_index=1,
                    generation_kind="one_call",
                )
                _phase87_one_call_started = True
            except Exception:
                pass
            try:
                from core.one_call_pipeline import one_call_pipeline_with_fallback
                from commerce.signals import CommerceSignals as _CS

                # Derive inputs for one-call pipeline - Phase 2: prefer authoritative snapshot (single source)
                if _authoritative_state is not None:
                    _profile_for_onecall = (
                        getattr(_authoritative_state, "profile", {})
                        or _cached_profile_for_commerce
                        or {}
                    )
                    _user_for_onecall = (
                        getattr(_authoritative_state, "user", {}) or _user_for_state or {}
                    )
                else:
                    _profile_for_onecall = _cached_profile_for_commerce or {}
                    _user_for_onecall = _user_for_state or {}
                _persona_name = None
                _identity_established = None
                _relationship_state = None
                _recent_purchase_count = 0
                _has_active_offer = False
                _has_relevant_product = True

                if _authoritative_state is not None and getattr(
                    _authoritative_state, "persona_name", None
                ):
                    _persona_name = getattr(_authoritative_state, "persona_name")
                elif _persona_snapshot:
                    _persona_name = _persona_snapshot.get("persona_name") or _persona_snapshot.get(
                        "name"
                    )
                if _conv_state:
                    _identity_established = getattr(
                        _conv_state, "identity_already_established", None
                    )
                    if _identity_established is None and isinstance(_conv_state, dict):
                        _identity_established = _conv_state.get("identity_already_established")
                if _cstate:
                    _relationship_state = _cstate.get("relationship_state")
                    _has_active_offer = bool(_cstate.get("has_active_offer"))
                    _recent_purchase_count = int(_cstate.get("recent_purchase_count", 0) or 0)
                    _has_relevant_product = bool(_cstate.get("has_relevant_product", True))
                # Pass 7L: thread deterministic commerce enrichment into the
                # OneCall hints gate (tolerates both key spellings; the gate
                # coerces DesireState/SalesWindow/str via _enrichment_text).
                _commerce_desire = None
                _commerce_window = None
                _commerce_objective = None
                try:
                    if _cstate:
                        _commerce_desire = _cstate.get("desire", _cstate.get("desire_stage"))
                        _commerce_window = _cstate.get("window", _cstate.get("sales_window"))
                        _commerce_objective = _cstate.get(
                            "objective", _cstate.get("commercial_objective")
                        )
                except Exception:
                    _commerce_desire = None
                    _commerce_window = None
                    _commerce_objective = None

                # Build commerce text from signals (or from snapshot commerce_context_text if signals not yet available)
                _commerce_text = ""
                if _commerce_signals:
                    _commerce_text = (
                        f"primary_intent: {getattr(_commerce_signals, 'primary_intent', 'unknown')}"
                    )
                    _pi = getattr(_commerce_signals, "purchase_intent", 0.0)
                    if _pi and _pi > 0.3:
                        _commerce_text += f"; purchase_intent: {_pi:.2f}"
                elif _authoritative_state is not None and getattr(
                    _authoritative_state, "commerce_context_text", ""
                ):
                    # Use snapshot commerce text as fallback for OneCall hints
                    _commerce_text = getattr(_authoritative_state, "commerce_context_text", "")[
                        :200
                    ]

                # Phase 2: provide snapshot + pipeline result to enable authoritative path
                _pipeline_result_for_onecall = None
                try:
                    if (
                        _context_engine_observation is not None
                        and getattr(_context_engine_observation, "pipeline_result", None)
                        is not None
                    ):
                        _pipeline_result_for_onecall = _context_engine_observation.pipeline_result
                except Exception:
                    _pipeline_result_for_onecall = None

                _one_call_kwargs: dict[str, Any] = dict(
                    user_id=user_id,
                    creator_id=_creator_id or 0,
                    user_message=user_message,
                    persona=persona or "",
                    profile=_profile_for_onecall,
                    user=_user_for_onecall,
                    commerce_text=_commerce_text,
                    conversation_state=_conv_state,
                    recent_messages=context,
                    persona_name=_persona_name,
                    identity_established=_identity_established,
                    relationship_state=_relationship_state,
                    recent_offer_count=_recent_offer_cnt,
                    recent_purchase_count=_recent_purchase_count,
                    has_active_offer=_has_active_offer,
                    has_relevant_product=_has_relevant_product,
                    retrieved_context=_retrieved_context,
                    generation_id=generation_id,
                    commerce_desire=_commerce_desire,
                    commerce_window=_commerce_window,
                    commerce_objective=_commerce_objective,
                )
                if _authoritative_state is not None:
                    _one_call_kwargs["authoritative_state"] = _authoritative_state
                if _pipeline_result_for_onecall is not None:
                    _one_call_kwargs["pipeline_result"] = _pipeline_result_for_onecall

                _one_call_result = await one_call_pipeline_with_fallback(**_one_call_kwargs)
                # Phase 87: count the canonical OneCall (even if validation failed, the provider call happened)
                _one_call_count = 1
                _total_llm_calls = 1
                try:
                    _one_call_provider = getattr(
                        _one_call_result, "provider_name", None
                    ) or getattr(_settings, "llm_provider", "llamacpp")
                    _one_call_model = getattr(_one_call_result, "model_name", None) or getattr(
                        _settings, "llama_model", "default"
                    )
                    _one_call_input_tokens = getattr(_one_call_result, "input_tokens", None)
                    _one_call_output_tokens = getattr(_one_call_result, "output_tokens", None)
                    _one_call_latency_ms = getattr(_one_call_result, "latency_ms", None)
                    _telemetry_data.provider_name = _one_call_provider  # type: ignore
                    _telemetry_data.model_name = _one_call_model  # type: ignore
                    _telemetry_data.input_token_count = _one_call_input_tokens  # type: ignore
                    _telemetry_data.output_token_count = _one_call_output_tokens  # type: ignore
                    if _one_call_latency_ms is not None:
                        _telemetry_data.provider_latency_ms = int(_one_call_latency_ms)  # type: ignore
                except Exception:
                    pass
                # Phase 89R: conversational grounding + roleplay telemetry (fail-open)
                try:
                    qf = list(getattr(_one_call_result, "quality_flags", []) or [])
                    _telemetry_data.conversational_validation_flags = qf  # type: ignore
                    _telemetry_data.roleplay_validation_flags = qf  # type: ignore
                    # speaker_correct / character_correct
                    if getattr(_authoritative_state, "participants", None) is not None:
                        _telemetry_data.speaker_correct = (
                            "speaker_inversion" not in qf
                            and "character_as_player_inversion" not in qf
                        )  # type: ignore
                        _telemetry_data.character_correct = _telemetry_data.speaker_correct  # type: ignore
                        _telemetry_data.participant_grounding_enabled = True  # type: ignore
                        _telemetry_data.roleplay_enabled = True  # type: ignore
                        _telemetry_data.roleplay_contract_present = bool(
                            getattr(_authoritative_state, "conversation_contract", None) is not None
                        )  # type: ignore
                        _telemetry_data.conversation_contract_present = (
                            _telemetry_data.roleplay_contract_present
                        )  # type: ignore
                        try:
                            part = getattr(_authoritative_state, "participants", None)
                            if part is not None:
                                _telemetry_data.character_name = getattr(
                                    part, "character_name", None
                                ) or getattr(part, "speaker_name", None)  # type: ignore
                                # player_name privacy: store hash, not raw
                                _telemetry_data.player_name = None  # type: ignore
                                try:
                                    _p_name = getattr(part, "player_name", None) or getattr(
                                        part, "listener_name", None
                                    )
                                    if (
                                        _p_name
                                        and isinstance(_p_name, str)
                                        and _p_name.strip()
                                        and _p_name.strip().lower() not in ("there", "fan", "user")
                                    ):
                                        _telemetry_data.player_name_hash = hashlib.sha256(
                                            _p_name.strip().lower().encode()
                                        ).hexdigest()[:16]  # type: ignore
                                    else:
                                        _telemetry_data.player_name_hash = None  # type: ignore
                                except Exception:
                                    _telemetry_data.player_name_hash = None  # type: ignore
                        except Exception:
                            pass
                        # player agency preserved
                        _telemetry_data.player_agency_preserved = (
                            "unauthorized_player_speech" not in qf
                            and "player_agency_violation" not in qf
                        )  # type: ignore
                        _telemetry_data.out_of_character = "out_of_character" in qf  # type: ignore
                    else:
                        _telemetry_data.roleplay_enabled = False  # type: ignore
                        _telemetry_data.roleplay_contract_present = False  # type: ignore
                        _telemetry_data.conversation_contract_present = False  # type: ignore
                    # question_answered: check contract
                    try:
                        contract = (
                            getattr(_authoritative_state, "conversation_contract", None)
                            if _authoritative_state
                            else None
                        )
                        if contract is not None and bool(
                            getattr(contract, "answer_required", False)
                        ):
                            _telemetry_data.question_answered = "unanswered_question" not in qf  # type: ignore
                        else:
                            _telemetry_data.question_answered = None  # type: ignore
                    except Exception:
                        pass
                    try:
                        contract = (
                            getattr(_authoritative_state, "conversation_contract", None)
                            if _authoritative_state
                            else None
                        )
                        if contract is not None and bool(
                            getattr(contract, "maintain_topic", False)
                        ):
                            _telemetry_data.topic_continuous = "topic_pivot" not in qf  # type: ignore
                        else:
                            _telemetry_data.topic_continuous = None  # type: ignore
                    except Exception:
                        pass
                except Exception:
                    pass
                # Phase 89R: insert quality feedback row best-effort (separate table) — extended with roleplay flags
                try:
                    import asyncio as _asyncio_feedback

                    async def _insert_feedback():
                        try:
                            from db.postgres import get_pool as _get_pool_fb

                            pool_fb = await _get_pool_fb()
                            async with pool_fb.acquire() as conn_fb:
                                qf2 = list(getattr(_one_call_result, "quality_flags", []) or [])
                                # compute roleplay signals for persona_feedback
                                _char_correct = (
                                    "speaker_inversion" not in qf2
                                    and "character_as_player_inversion" not in qf2
                                    if getattr(_authoritative_state, "participants", None)
                                    is not None
                                    else None
                                )
                                _player_preserved = (
                                    "unauthorized_player_speech" not in qf2
                                    and "player_agency_violation" not in qf2
                                    if getattr(_authoritative_state, "participants", None)
                                    is not None
                                    else None
                                )
                                _ooc = (
                                    "out_of_character" in qf2
                                    if getattr(_authoritative_state, "participants", None)
                                    is not None
                                    else None
                                )
                                # Try extended schema first (with roleplay columns), fallback to legacy if columns missing
                                try:
                                    await conn_fb.execute(
                                        "INSERT INTO persona_feedback (generation_id, creator_id, persona_id, persona_version, speaker_correct, character_correct, player_agency_preserved, question_answered, topic_continuous, out_of_character, generic_deflection, validation_status, quality_flags, roleplay_validation_flags) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)",
                                        generation_id,
                                        _creator_id,
                                        getattr(_authoritative_state, "persona_id", None)
                                        if _authoritative_state
                                        else None,
                                        getattr(_authoritative_state, "persona_version", None)
                                        if _authoritative_state
                                        else None,
                                        "speaker_inversion" not in qf2
                                        if getattr(_authoritative_state, "participants", None)
                                        is not None
                                        else None,
                                        _char_correct,
                                        _player_preserved,
                                        ("unanswered_question" not in qf2)
                                        if (
                                            _authoritative_state
                                            and getattr(
                                                getattr(
                                                    _authoritative_state,
                                                    "conversation_contract",
                                                    None,
                                                ),
                                                "answer_required",
                                                False,
                                            )
                                        )
                                        else None,
                                        ("topic_pivot" not in qf2)
                                        if (
                                            _authoritative_state
                                            and getattr(
                                                getattr(
                                                    _authoritative_state,
                                                    "conversation_contract",
                                                    None,
                                                ),
                                                "maintain_topic",
                                                False,
                                            )
                                        )
                                        else None,
                                        _ooc,
                                        "generic_deflection" in qf2 or "too_generic" in qf2,
                                        "success"
                                        if getattr(_one_call_result, "is_valid", False)
                                        else "failure",
                                        qf2,
                                        qf2,
                                    )
                                except Exception as _e_ext:
                                    # fallback to legacy schema if extended columns missing
                                    err_s = str(_e_ext).lower()
                                    if "column" in err_s and "does not exist" in err_s:
                                        await conn_fb.execute(
                                            "INSERT INTO persona_feedback (generation_id, creator_id, persona_id, persona_version, speaker_correct, question_answered, topic_continuous, generic_deflection, validation_status, quality_flags) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                                            generation_id,
                                            _creator_id,
                                            getattr(_authoritative_state, "persona_id", None)
                                            if _authoritative_state
                                            else None,
                                            getattr(_authoritative_state, "persona_version", None)
                                            if _authoritative_state
                                            else None,
                                            "speaker_inversion" not in qf2
                                            if getattr(_authoritative_state, "participants", None)
                                            is not None
                                            else None,
                                            ("unanswered_question" not in qf2)
                                            if (
                                                _authoritative_state
                                                and getattr(
                                                    getattr(
                                                        _authoritative_state,
                                                        "conversation_contract",
                                                        None,
                                                    ),
                                                    "answer_required",
                                                    False,
                                                )
                                            )
                                            else None,
                                            ("topic_pivot" not in qf2)
                                            if (
                                                _authoritative_state
                                                and getattr(
                                                    getattr(
                                                        _authoritative_state,
                                                        "conversation_contract",
                                                        None,
                                                    ),
                                                    "maintain_topic",
                                                    False,
                                                )
                                            )
                                            else None,
                                            "generic_deflection" in qf2 or "too_generic" in qf2,
                                            "success"
                                            if getattr(_one_call_result, "is_valid", False)
                                            else "failure",
                                            qf2,
                                        )
                                    else:
                                        raise
                        except Exception:
                            pass

                    # fire-and-forget, do not block
                    try:
                        _asyncio_feedback.create_task(_insert_feedback())
                    except Exception:
                        pass
                except Exception:
                    pass

                if _one_call_result.is_valid and _one_call_result.reply:
                    draft = _one_call_result.reply
                    score = _one_call_result.quality_score
                    flags = list(_one_call_result.safety_flags) + list(
                        _one_call_result.quality_flags
                    )
                    # G15 evidence-ledger emission only (fail-open; no behavior change).
                    # Mirror OneCall quality+safety into the persisted scoring
                    # fields (legacy score_draft path at :3605 untouched).
                    try:
                        _telemetry_data.scoring_score = float(score)
                    except Exception:
                        pass
                    try:
                        _telemetry_data.scoring_flags = list(flags)
                    except Exception:
                        pass
                    # Phase 5/H3: capture routing signals verbatim. needs_handoff
                    # is no longer silently ignored; advisory_handoff preserves
                    # the raw LLM flag for the routing boundary.
                    _routing_needs_handoff = bool(getattr(_one_call_result, "needs_handoff", False))
                    _routing_advisory_handoff = bool(
                        getattr(_one_call_result, "advisory_handoff", False)
                    )
                    _routing_safety_block = bool(getattr(_one_call_result, "safety_flags", []))
                    _generation_valid = True
                    # Use one-call's embedded commerce signals (avoids separate LLM call)
                    if _one_call_result.signals:
                        _commerce_signals = _one_call_result.signals
                    _telemetry_data.routing_decision = "one_call"
                    # Phase 87 validation taxonomy
                    _validation_outcome = "success"
                    if _one_call_result.safety_flags:
                        _validation_outcome = "safety_validation_failure"
                    elif _one_call_result.quality_score < 0.3:
                        _validation_outcome = "quality_validation_failure"
                    logger.info(
                        "One-call pipeline succeeded for user=%s score=%.2f flags=%s",
                        user_id,
                        score,
                        flags,
                    )
                    # Phase 87: emit ONE_CALL_SUCCESS for canonical (fail-open)
                    try:
                        await emit_one_call_success(
                            user_id=user_id,
                            creator_id=_creator_id,
                            generation_id=generation_id,
                            runtime_mode=_runtime_mode_label,
                            provider_name=_one_call_provider,
                            model_name=_one_call_model,
                            call_index=1,
                            generation_kind="one_call",
                            input_tokens=_one_call_input_tokens,
                            output_tokens=_one_call_output_tokens,
                            latency_ms=_one_call_latency_ms
                            if _one_call_latency_ms is not None
                            else int((_time.monotonic() - _one_call_start) * 1000),
                            validation_status=_validation_outcome or "success",
                        )
                    except Exception:
                        pass

                    # ── Commerce PPV precedence — P1.2 canonical decision convergence ──
                    # Single authoritative decision per message. Evaluate once via
                    # _get_canonical_commerce_evaluation, reuse for PPV precedence,
                    # free-photo gating, and commerce execution without second
                    # recomputation. Raw user_message is threaded for P0 gating.
                    _commerce_ppv_authorized = False
                    _canonical_decision = None
                    _canonical_request = None
                    # P2 Fix 1 reuse: authoritative conversation_state for canonical evaluation
                    _cs_for_canonical = (
                        _conv_state
                        if _conv_state is not None
                        else (
                            getattr(_authoritative_state, "conversation_state", None)
                            if _authoritative_state
                            else None
                        )
                    )
                    try:
                        (
                            _canonical_decision,
                            _canonical_request,
                        ) = await _get_canonical_commerce_evaluation(
                            user_id,
                            context,
                            persona,
                            signals=_commerce_signals,
                            conversation_state=_cs_for_canonical,
                            user_message=user_message,
                            opportunity_result=_opportunity_result,
                        )
                        if _canonical_decision is not None:
                            # Phase 10: stamp the authoritative decision with
                            # its deterministic config/strategy version where
                            # creator scope is known (additive, fail-open).
                            try:
                                from commerce.phase10_learning import (
                                    stamp_decision_with_version as _p10_stamp,
                                    get_active_config_version as _p10_ver,
                                )

                                _p10_stamp(
                                    _canonical_decision,
                                    config_version=_p10_ver(creator_scope=_creator_id),
                                    creator_scope=_creator_id,
                                )
                            except Exception:
                                pass
                            from commerce.models import CommerceAction as _CA_PPV

                            _commerce_ppv_authorized = bool(
                                _canonical_decision.action is _CA_PPV.OFFER_PPV
                                and bool(getattr(_canonical_decision, "allowed", False))
                            )
                            # For backward compat, still call precedence helper with canonical decision
                            # so any spy on decide_from_signals sees only one invocation (inside canonical).
                            try:
                                _ppv_via_helper = await _is_commerce_ppv_authorized_for_precedence(
                                    user_id,
                                    context,
                                    persona,
                                    signals=_commerce_signals,
                                    conversation_state=_cs_for_canonical,
                                    user_message=user_message,
                                    canonical_decision=_canonical_decision,
                                    opportunity_result=_opportunity_result,
                                )
                                # Helper must agree with canonical; if diverges, trust canonical
                                if _ppv_via_helper != _commerce_ppv_authorized:
                                    logger.debug(
                                        "PPV helper diverged from canonical for user=%s canonical=%s helper=%s",
                                        user_id,
                                        _commerce_ppv_authorized,
                                        _ppv_via_helper,
                                    )
                            except Exception:
                                pass
                            if _commerce_ppv_authorized:
                                logger.info(
                                    "Commerce PPV authorized for user=%s — suppressing free-photo (OFFER_PPV)",
                                    user_id,
                                )
                                try:
                                    _telemetry_data.commerce_precedence = "ppv_authorized"  # type: ignore
                                except Exception:
                                    pass
                    except Exception:
                        logger.debug(
                            "Canonical commerce evaluation failed for user=%s (free-photo may proceed)",
                            user_id,
                            exc_info=True,
                        )
                        _commerce_ppv_authorized = False
                        _canonical_decision = None
                        _canonical_request = None
                    # G11 evidence-ledger emission only (log string, not new state;
                    # fail-open; no behavior change). Live path is quarantined so
                    # the label is always "none"; "selection" would only apply at
                    # the :3190 consumption point which is armed separately (G10).
                    try:
                        if _canonical_decision is None or not _commerce_ppv_authorized:
                            _g11_gate = "none"
                        elif bool(_phase9_det_buy) or bool(_phase9_det_price):
                            _g11_gate = "verifier"
                        else:
                            _g11_gate = "float"
                        logger.info(
                            "G11 commerce_gate generation_id=%s gate=%s authorized=%s",
                            generation_id,
                            _g11_gate,
                            bool(_commerce_ppv_authorized),
                        )
                    except Exception:
                        pass

                    # ── Phase 7 commerce veto (deterministic, downstream) ──
                    # An active conversational boundary vetoes the PPV
                    # authorization independently of LLM commerce signals.
                    # Commerce authority itself is unchanged: a vetoed turn
                    # simply proceeds as a neutral conversational turn.
                    try:
                        if _boundary_commerce_veto and _commerce_ppv_authorized:
                            logger.info(
                                "Boundary commerce veto user=%s reason=%s — overriding PPV authorization",
                                user_id,
                                _boundary_commerce_reason,
                            )
                            _commerce_ppv_authorized = False
                            try:
                                _telemetry_data.commerce_precedence = "boundary_veto"  # type: ignore
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # ── Phase 99: deterministic free-photo routing (only if not PPV-authorized) ──
                    # Reuses existing authoritative state, retrieval, and OneCall signals.
                    # No second context builder, no second LLM call, no PPV duplication.
                    # Calls the certified Phase 98 `authorize_free_photo` as sole authority for
                    # quota/purchase/duplicate/approved-media. Result is authoritative;
                    # LLM output is advisory only. Never converts quota_exhausted etc. to PPV.
                    # If commerce PPV is authorized, free-photo is not attempted at all.
                    _free_photo_result = None
                    _free_photo_eligible = False
                    if _commerce_ppv_authorized:
                        logger.info(
                            "Skipping free-photo routing for user=%s — commerce owns turn (OFFER_PPV)",
                            user_id,
                        )
                        try:
                            _telemetry_data.free_photo_outcome = "suppressed_by_ppv"  # type: ignore
                        except Exception:
                            pass
                    elif _boundary_commerce_veto:
                        # Phase 7: media/content transitions stay suppressed
                        # under an active topic/close/contact boundary.
                        logger.info(
                            "Skipping free-photo routing for user=%s — boundary veto reason=%s",
                            user_id,
                            _boundary_commerce_reason,
                        )
                        try:
                            _telemetry_data.free_photo_outcome = "suppressed_by_boundary"  # type: ignore
                        except Exception:
                            pass
                    elif _commerce_context is not None and bool(
                        getattr(_commerce_context, "disinterest_present", False)
                    ):
                        # Phase 9: current content disinterest suppresses
                        # content-related promotion this turn (turn-scoped,
                        # no durable flag). Purchase/boundary/eligibility
                        # authority is unchanged; only the advisory content
                        # promotion is withheld.
                        logger.info(
                            "Skipping free-photo routing for user=%s — Phase 9 current disinterest",
                            user_id,
                        )
                        try:
                            _telemetry_data.free_photo_outcome = "suppressed_by_disinterest"  # type: ignore
                        except Exception:
                            pass
                    else:
                        try:
                            if _creator_id is not None:
                                import importlib as _il

                                _free_mod = _il.import_module("commerce.free_photo_routing")
                                _route_fn = getattr(_free_mod, "route_free_photo")

                                _free_photo_result = await _route_fn(
                                    creator_id=_creator_id,
                                    user_id=user_id,
                                    user_message=user_message,
                                    signals=_commerce_signals,
                                )
                                if _free_photo_result.attempted:
                                    try:
                                        _telemetry_data.free_photo_outcome = (
                                            _free_photo_result.outcome
                                        )  # type: ignore
                                        _telemetry_data.free_photo_vault_item = (
                                            _free_photo_result.selected_vault_item_id
                                        )  # type: ignore
                                        _telemetry_data.free_photo_llm_flag = (
                                            _free_photo_result.llm_explicit_content_request
                                        )  # type: ignore
                                    except Exception:
                                        pass
                                    if _free_photo_result.outcome == "eligible":
                                        _free_photo_eligible = True
                                        # Free-photo warming takes precedence for this turn; do not also authorize PPV.
                                        # Phase 100 will handle actual Telethon delivery; Phase 99 only reserves.
                                        logger.info(
                                            "Free-photo routing eligible for user=%s creator=%s vault=%s seq=%s",
                                            user_id,
                                            _creator_id,
                                            _free_photo_result.selected_vault_item_id,
                                            _free_photo_result.authorization.seq
                                            if _free_photo_result.authorization
                                            else None,
                                        )
                                    elif _free_photo_result.outcome in (
                                        "quota_exhausted",
                                        "already_pending",
                                        "already_sent_same_media",
                                        "invalid_media",
                                        "no_approved_media",
                                        "invalid_creator_or_user",
                                    ):
                                        logger.info(
                                            "Free-photo routing %s for user=%s creator=%s",
                                            _free_photo_result.outcome,
                                            user_id,
                                            _creator_id,
                                        )
                                    # Never auto-convert denied free-photo into PPV — decision.py fix ensures
                                    # explicit_content_request alone does not become OFFER_PPV.
                        except Exception:
                            logger.debug(
                                "Free-photo routing failed for user=%s", user_id, exc_info=True
                            )

                    # ── Phase 100: actual free-photo delivery (pending → sent/failed) ──
                    # Reuses the pending reservation from Phase 99/98; never re-authorizes.
                    # Uses the existing Telethon send_file abstraction; ledger transition
                    # occurs only after real Telegram result, not enqueue.
                    # Phase 3.3: same turn-send claim — sealed (earlier) wins;
                    # a repeat turn never gets a second wire here.
                    if (
                        _free_photo_eligible
                        and _free_photo_result
                        and _free_photo_result.authorization
                        and _free_photo_result.authorization.reservation_id
                    ):
                        if _turn_already_sent:
                            logger.info(
                                "Turn already sent creator=%s user=%s tg_id=%s — suppressing free-photo delivery",
                                _creator_id,
                                user_id,
                                telegram_message_id,
                            )
                        else:
                            _fp_claimed = True
                            if not _turn_send_claimed:
                                try:
                                    from db.redis import (
                                        try_claim_turn_send as _claim_turn_fp,
                                    )

                                    _fp_claimed = await _claim_turn_fp(
                                        _creator_id, user_id, telegram_message_id
                                    )
                                except Exception:
                                    _fp_claimed = True
                            if not _fp_claimed:
                                logger.info(
                                    "Turn already sent creator=%s user=%s tg_id=%s — suppressing free-photo delivery",
                                    _creator_id,
                                    user_id,
                                    telegram_message_id,
                                )
                                _turn_already_sent = True
                            else:
                                _turn_send_claimed = True
                                try:
                                    import importlib as _il2

                                    _deliv_mod = _il2.import_module(
                                        "commerce.free_photo_delivery"
                                    )
                                    _deliv_fn = getattr(_deliv_mod, "deliver_free_photo")
                                    _deliv_res = await _deliv_fn(
                                        _creator_id,
                                        user_id,
                                        _free_photo_result.authorization.reservation_id,
                                    )
                                    try:
                                        _telemetry_data.free_photo_delivery = _deliv_res.reason  # type: ignore
                                        _telemetry_data.free_photo_telegram_id = (
                                            _deliv_res.telegram_message_id
                                        )  # type: ignore
                                    except Exception:
                                        pass
                                    if _deliv_res.delivered:
                                        logger.info(
                                            "Free-photo delivery succeeded res=%s tg_id=%s",
                                            _deliv_res.reservation_id,
                                            _deliv_res.telegram_message_id,
                                        )
                                    else:
                                        logger.info(
                                            "Free-photo delivery not sent res=%s reason=%s",
                                            _deliv_res.reservation_id,
                                            _deliv_res.reason,
                                        )
                                except Exception:
                                    logger.debug(
                                        "Free-photo delivery execution failed for user=%s",
                                        user_id,
                                        exc_info=True,
                                    )

                    # ── Commerce execution (one-call path) ────────────────
                    # Execute PPV / offer if commerce signals warrant it.
                    # Pass one-call signals to skip duplicate extract_commerce_signals().
                    # Precedence: OFFER_PPV authorized → commerce owns turn (free already suppressed).
                    # Otherwise, if free-photo eligible → suppress PPV to keep warming vs paid distinct.
                    # For SOFT_OFFER/FOLLOW_UP/NO_OFFER/TIP/HANDOFF → free may proceed (allowed).
                    _ppv_gate_hit = False
                    try:
                        if _commerce_ppv_authorized:
                            # P1.2: commerce owns turn, reuse canonical decision without second evaluation
                            logger.info(
                                "Commerce owns turn for user=%s — free-photo was suppressed (OFFER_PPV)",
                                user_id,
                            )
                            _cs_for_commerce = (
                                _conv_state
                                if _conv_state is not None
                                else (
                                    getattr(_authoritative_state, "conversation_state", None)
                                    if _authoritative_state
                                    else None
                                )
                            )
                            try:
                                _oc_selection = await _try_commerce_draft(
                                    user_id,
                                    context,
                                    persona,
                                    signals=_commerce_signals,
                                    conversation_state=_cs_for_commerce,
                                    user_message=user_message,
                                    canonical_decision=_canonical_decision,
                                    canonical_request=_canonical_request,
                                    opportunity_result=_opportunity_result,
                                )
                            except TypeError:
                                _oc_selection = await _try_commerce_draft(
                                    user_id,
                                    context,
                                    persona,
                                    signals=_commerce_signals,
                                    user_message=user_message,
                                )
                        elif _free_photo_eligible:
                            # Free-photo reservation already holds the turn's
                            # relationship-building slot; do not also execute commerce.
                            logger.info(
                                "Skipping commerce PPV for user=%s — free-photo eligible takes precedence (no PPV authorized)",
                                user_id,
                            )
                            _oc_selection = None
                        elif _boundary_commerce_veto:
                            # Phase 7: no commerce draft under an active
                            # topic/close/contact boundary; the neutral
                            # conversational draft owns the turn.
                            logger.info(
                                "Skipping commerce execution for user=%s — boundary veto reason=%s",
                                user_id,
                                _boundary_commerce_reason,
                            )
                            _oc_selection = None
                        else:
                            # P1.2 + P2 Fix 1: reuse authoritative state and canonical decision (no second derive/decide)
                            _cs_for_commerce = (
                                _conv_state
                                if _conv_state is not None
                                else (
                                    getattr(_authoritative_state, "conversation_state", None)
                                    if _authoritative_state
                                    else None
                                )
                            )
                            try:
                                _oc_selection = await _try_commerce_draft(
                                    user_id,
                                    context,
                                    persona,
                                    signals=_commerce_signals,
                                    conversation_state=_cs_for_commerce,
                                    user_message=user_message,
                                    canonical_decision=_canonical_decision,
                                    canonical_request=_canonical_request,
                                    opportunity_result=_opportunity_result,
                                )
                            except TypeError:
                                # Fallback for tests mocking old signature
                                _oc_selection = await _try_commerce_draft(
                                    user_id,
                                    context,
                                    persona,
                                    signals=_commerce_signals,
                                    user_message=user_message,
                                )
                        # G10 evidence-ledger emission only (log; fail-open; no behavior
                        # change; does NOT re-enable the commerce draft path).
                        try:
                            if _oc_selection is not None:
                                _g10_status = getattr(
                                    getattr(_oc_selection, "status", None),
                                    "value",
                                    getattr(_oc_selection, "status", None),
                                )
                                _g10_reason = getattr(
                                    getattr(_oc_selection, "reason", None),
                                    "value",
                                    getattr(_oc_selection, "reason", None),
                                )
                                logger.info(
                                    "G10 selection generation_id=%s status=%s reason=%s",
                                    generation_id,
                                    _g10_status,
                                    _g10_reason,
                                )
                        except Exception:
                            pass
                        if (
                            _oc_selection is not None
                            and _oc_selection.status
                            is CommerceSelectionStatus.USE_COMMERCE_RESPONSE
                        ):
                            draft = _oc_selection.commerce_response_text
                            # Phase 1.2: output-rails re-check on the commerce-overwrite
                            # draft (fail-open). Commerce text bypasses pipeline
                            # validation, so run rails on the new draft; union flags,
                            # min-cap score, re-assert handoff. No new I/O: last-3
                            # from in-scope history. Routing at :5256 decides.
                            try:
                                from core.output_rails import check as _rails_check_commerce

                                _rails_prior_c: list[str] = []
                                try:
                                    _auth_recent = (
                                        getattr(_authoritative_state, "recent_messages", None) or []
                                    )
                                    for _m in list(_auth_recent):
                                        if (
                                            isinstance(_m, dict)
                                            and (_m.get("direction") or "") != "inbound"
                                            and _m.get("content")
                                        ):
                                            _rails_prior_c.append(_m.get("content"))
                                    if not _rails_prior_c:
                                        for _m in list(context or []):
                                            if (
                                                isinstance(_m, dict)
                                                and _m.get("role") == "assistant"
                                                and _m.get("content")
                                            ):
                                                _rails_prior_c.append(_m.get("content"))
                                    _rails_prior_c = _rails_prior_c[-3:]
                                except Exception:
                                    _rails_prior_c = []
                                _rails_verdict_c, _rails_flags_c, _rails_cap_c = (
                                    _rails_check_commerce(
                                        draft, recent_outbound=_rails_prior_c, embed=None
                                    )
                                )
                                for _rf in _rails_flags_c:
                                    if _rf not in flags:
                                        flags.append(_rf)
                                if _rails_cap_c is not None:
                                    score = min(score, _rails_cap_c)
                                if _rails_verdict_c == "review":
                                    _routing_needs_handoff = True
                                try:
                                    _telemetry_data.scoring_flags = list(flags)
                                except Exception:
                                    pass
                            except Exception:
                                pass
                            _telemetry_data.routing_decision = "one_call_commerce"
                            # Phase 87: PPV second generation is INTENTIONAL and counted at exact gate (action == OFFER_PPV + status EXECUTED/ALREADY)
                            _ppv_gate_hit = True
                            _ppv_second_generation_count = 1
                            _total_llm_calls = 2
                            # Distinguish already_executed vs executed for idempotency metric
                            try:
                                _exec_status = getattr(_oc_selection, "execution_status", None)
                                if (
                                    _exec_status is not None
                                    and str(getattr(_exec_status, "value", _exec_status))
                                    == "already_executed"
                                ):
                                    _telemetry_data.already_executed_count = 1  # type: ignore
                                    _telemetry_data.commerce_status = "ALREADY_EXECUTED"  # type: ignore
                                else:
                                    _telemetry_data.already_executed_count = 0  # type: ignore
                                    _telemetry_data.commerce_status = "EXECUTED"  # type: ignore
                            except Exception:
                                pass
                            # Try to capture PPV provider/tokens if commerce_response generated via LLM (inside pipeline)
                            try:
                                # If commerce Response used Ollama, provider last tokens are in same process but we attribute to PPV
                                from commerce.deepseek_response import (
                                    CommerceResponseStatus as _CRS2,
                                )

                                # _oc_selection may have underlying commerce_response with status GENERATED?
                                # We approximate PPV provider as same as OneCall (llamacpp default)
                                _telemetry_data.ppv_provider_name = _one_call_provider  # type: ignore
                                _telemetry_data.ppv_model_name = _one_call_model  # type: ignore
                            except Exception:
                                pass
                            # Phase 87: emit PPV second-generation lifecycle (distinct from canonical)
                            try:
                                await emit_one_call_start(
                                    user_id=user_id,
                                    creator_id=_creator_id,
                                    generation_id=generation_id,
                                    runtime_mode=_runtime_mode_label,
                                    provider_name=_one_call_provider,
                                    model_name=_one_call_model,
                                    call_index=2,
                                    generation_kind="ppv_second_generation",
                                )
                                await emit_one_call_success(
                                    user_id=user_id,
                                    creator_id=_creator_id,
                                    generation_id=generation_id,
                                    runtime_mode=_runtime_mode_label,
                                    provider_name=_one_call_provider,
                                    model_name=_one_call_model,
                                    call_index=2,
                                    generation_kind="ppv_second_generation",
                                    input_tokens=None,
                                    output_tokens=None,
                                    latency_ms=None,
                                    validation_status="ppv_success",
                                )
                            except Exception:
                                pass
                            logger.info("Commerce draft selected (one-call) for user %s", user_id)
                        else:
                            _ppv_second_generation_count = 0
                    except Exception:
                        logger.debug("Commerce execution skipped (one-call) for user=%s", user_id)
                    # Phase 87: persist generation counters even on success
                    try:
                        _telemetry_data.one_call_count = _one_call_count  # type: ignore
                        _telemetry_data.ppv_second_generation_count = _ppv_second_generation_count  # type: ignore
                        _telemetry_data.total_llm_calls = _total_llm_calls  # type: ignore
                        _telemetry_data.legacy_generation_count = 0  # type: ignore
                        _telemetry_data.validation_outcome = _validation_outcome  # type: ignore
                    except Exception:
                        pass
                else:
                    # Validation failure taxonomy
                    _ve = (_one_call_result.validation_error or "").lower()
                    if "json parse" in _ve or "malformed" in _ve:
                        _validation_outcome = "malformed_json"
                    elif "schema validation" in _ve or "pydantic" in _ve:
                        _validation_outcome = "pydantic_validation_failure"
                    elif "generation failed" in _ve and "timeout" in _ve:
                        _validation_outcome = "provider_timeout"
                    elif "generation failed" in _ve:
                        _validation_outcome = "provider_exception"
                    elif _one_call_result.safety_flags:
                        _validation_outcome = "safety_validation_failure"
                    elif "quality" in _ve:
                        _validation_outcome = "quality_validation_failure"
                    else:
                        _validation_outcome = "unknown_validation_failure"
                    try:
                        _telemetry_data.one_call_count = 1  # type: ignore
                        _telemetry_data.total_llm_calls = 1  # type: ignore
                        _telemetry_data.ppv_second_generation_count = 0  # type: ignore
                        _telemetry_data.legacy_generation_count = 0  # type: ignore
                        _telemetry_data.validation_outcome = _validation_outcome  # type: ignore
                    except Exception:
                        pass
                    try:
                        await emit_one_call_failed(
                            user_id=user_id,
                            creator_id=_creator_id,
                            generation_id=generation_id,
                            runtime_mode=_runtime_mode_label,
                            provider_name=_one_call_provider
                            or getattr(_settings, "llm_provider", "llamacpp"),
                            model_name=_one_call_model
                            or getattr(_settings, "llama_model", "default"),
                            call_index=1,
                            generation_kind="one_call",
                            latency_ms=_one_call_latency_ms,
                            failure_reason=_validation_outcome or "unknown",
                            error_class=_one_call_result.validation_error,
                        )
                    except Exception:
                        pass
                    logger.warning(
                        "One-call pipeline returned invalid result for user=%s: %s",
                        user_id,
                        _one_call_result.validation_error,
                    )
                    # Hardened: no legacy cascade. Route to operator queue.
                    _telemetry_data.routing_decision = "one_call_failed_operator_queue"
                    _operator_queue_entry = None
                    try:
                        _operator_queue_entry = await add_to_operator_queue(
                            user_id=user_id,
                            draft_content="",
                            confidence_score=0.0,
                            flags=["one_call_invalid_result"],
                            creator_id=_creator_id,
                            generation_id=generation_id,
                        )
                        if _operator_queue_entry:
                            _notify_operator = True
                    except Exception:
                        logger.debug("Operator queue insert failed for user=%s", user_id)
                    # Phase 87 handoff event
                    try:
                        from core.phase87_events import emit_handoff

                        await emit_handoff(
                            user_id=user_id,
                            creator_id=_creator_id,
                            generation_id=generation_id,
                            runtime_mode=_runtime_mode_label,
                            reason=_validation_outcome or "one_call_invalid_result",
                            queue_id=_operator_queue_entry,
                        )
                    except Exception:
                        pass
                    # Publish lifecycle events (best-effort, matches test mock)
                    try:
                        await publish_event(
                            "ai.generation_completed",
                            {
                                "draft": "",
                                "score": 0.0,
                                "flags": ["one_call_invalid_result"],
                                "was_auto_approved": False,
                            },
                            user_id=user_id,
                            dialog_id=user_id,
                            generation_id=generation_id,
                            creator_id=_creator_id,
                            scope="user",
                        )
                    except Exception:
                        pass
                    try:
                        await publish_event(
                            "suggestion.created",
                            {
                                "queue_id": _operator_queue_entry,
                                "draft": "",
                                "score": 0.0,
                                "flags": ["one_call_invalid_result"],
                            },
                            user_id=user_id,
                            dialog_id=user_id,
                            generation_id=generation_id,
                            creator_id=_creator_id,
                            scope="user",
                        )
                    except Exception:
                        pass
                    # Phase 87: persist telemetry even on validation failure path (fail-open)
                    try:
                        _telemetry_data.complete(
                            success=False,
                            failure_type=_validation_outcome or "unknown_validation_failure",
                        )
                        await _telemetry.record(_telemetry_data)
                    except Exception:
                        pass
                    # Phase 2 relationship evidence on generation failure
                    # (fail-open; deterministic user-side evidence still
                    # processes, llm_signals absent).
                    try:
                        _rel_recent = None
                        _rel_contract = None
                        _rel_mode = None
                        try:
                            if _authoritative_state is not None:
                                _rel_recent = getattr(_authoritative_state, "recent_messages", None)
                                _rel_contract = getattr(
                                    _authoritative_state, "conversation_contract", None
                                )
                        except Exception:
                            pass
                        try:
                            if _persona_behavior_state is not None:
                                _rel_mode = getattr(
                                    _persona_behavior_state, "conversation_mode", None
                                )
                        except Exception:
                            pass
                        if _rel_mode is None:
                            try:
                                _rel_mode = _response_mode
                            except Exception:
                                _rel_mode = None
                        await _record_relationship_trajectory(
                            user_id=user_id,
                            creator_id=_creator_id,
                            generation_id=generation_id,
                            user_message=user_message,
                            recent_messages=_rel_recent,
                            conversation_state=_conv_state,
                            conversation_contract=_rel_contract,
                            profile=_cached_profile_for_commerce,
                            current_topic=_cur_for_beh,
                            open_threads=_open_for_beh,
                            assistant_text="",
                            assistant_valid=False,
                            conversation_mode=_rel_mode if isinstance(_rel_mode, str) else None,
                            llm_signals=None,
                        )
                    except Exception:
                        logger.debug(
                            "relationship path failed on invalid-result turn (fail-open)",
                            exc_info=True,
                        )
                    # Phase 6 intimacy evidence on generation failure
                    # (fail-open; deterministic user-side evidence still
                    # processes, llm advisories absent).
                    try:
                        await _record_intimacy_trajectory(
                            user_id=user_id,
                            creator_id=_creator_id,
                            generation_id=generation_id,
                            user_message=user_message,
                            recent_messages=_rel_recent,
                            conversation_state=_conv_state,
                        )
                    except Exception:
                        logger.debug(
                            "intimacy path failed on invalid-result turn (fail-open)", exc_info=True
                        )
                    return

            except Exception as e:
                # OneCall provider exception also counts as one attempt
                try:
                    _telemetry_data.one_call_count = 1  # type: ignore
                    _telemetry_data.total_llm_calls = 1  # type: ignore
                    _telemetry_data.ppv_second_generation_count = 0  # type: ignore
                    _telemetry_data.legacy_generation_count = 0  # type: ignore
                    _telemetry_data.validation_outcome = "provider_exception"  # type: ignore
                    await emit_one_call_failed(
                        user_id=user_id,
                        creator_id=_creator_id,
                        generation_id=generation_id,
                        runtime_mode=_runtime_mode_label,
                        provider_name=getattr(_settings, "llm_provider", "llamacpp"),
                        model_name=getattr(_settings, "llama_model", "default"),
                        call_index=1,
                        generation_kind="one_call",
                        latency_ms=None,
                        failure_reason="provider_exception",
                        error_class=str(e)[:200],
                    )
                except Exception:
                    pass
                logger.warning("One-call pipeline failed for user=%s: %s", user_id, e)
                # Hardened: no legacy cascade. Route to operator queue.
                _telemetry_data.routing_decision = "one_call_exception_operator_queue"
                _operator_queue_entry = None
                try:
                    _operator_queue_entry = await add_to_operator_queue(
                        user_id=user_id,
                        draft_content="",
                        confidence_score=0.0,
                        flags=["one_call_exception"],
                        creator_id=_creator_id,
                        generation_id=generation_id,
                    )
                    if _operator_queue_entry:
                        _notify_operator = True
                except Exception:
                    logger.debug("Operator queue insert failed for user=%s", user_id)
                # handoff
                try:
                    from core.phase87_events import emit_handoff

                    await emit_handoff(
                        user_id=user_id,
                        creator_id=_creator_id,
                        generation_id=generation_id,
                        runtime_mode=_runtime_mode_label,
                        reason="provider_exception",
                        queue_id=_operator_queue_entry,
                    )
                except Exception:
                    pass
                try:
                    await publish_event(
                        "ai.generation_completed",
                        {
                            "draft": "",
                            "score": 0.0,
                            "flags": ["one_call_exception"],
                            "was_auto_approved": False,
                        },
                        user_id=user_id,
                        dialog_id=user_id,
                        generation_id=generation_id,
                        creator_id=_creator_id,
                        scope="user",
                    )
                except Exception:
                    pass
                try:
                    await publish_event(
                        "suggestion.created",
                        {
                            "queue_id": _operator_queue_entry,
                            "draft": "",
                            "score": 0.0,
                            "flags": ["one_call_exception"],
                        },
                        user_id=user_id,
                        dialog_id=user_id,
                        generation_id=generation_id,
                        creator_id=_creator_id,
                        scope="user",
                    )
                except Exception:
                    pass
                # Phase 87: persist telemetry even on provider exception path
                try:
                    _telemetry_data.complete(success=False, failure_type="provider_exception")
                    await _telemetry.record(_telemetry_data)
                except Exception:
                    pass
                # Phase 2 relationship evidence on provider exception
                # (fail-open; deterministic user-side evidence still processes).
                try:
                    _rel_recent2 = None
                    _rel_contract2 = None
                    _rel_mode2 = None
                    try:
                        if _authoritative_state is not None:
                            _rel_recent2 = getattr(_authoritative_state, "recent_messages", None)
                            _rel_contract2 = getattr(
                                _authoritative_state, "conversation_contract", None
                            )
                    except Exception:
                        pass
                    try:
                        if _persona_behavior_state is not None:
                            _rel_mode2 = getattr(_persona_behavior_state, "conversation_mode", None)
                    except Exception:
                        pass
                    if _rel_mode2 is None:
                        try:
                            _rel_mode2 = _response_mode
                        except Exception:
                            _rel_mode2 = None
                    await _record_relationship_trajectory(
                        user_id=user_id,
                        creator_id=_creator_id,
                        generation_id=generation_id,
                        user_message=user_message,
                        recent_messages=_rel_recent2,
                        conversation_state=_conv_state,
                        conversation_contract=_rel_contract2,
                        profile=_cached_profile_for_commerce,
                        current_topic=_cur_for_beh,
                        open_threads=_open_for_beh,
                        assistant_text="",
                        assistant_valid=False,
                        conversation_mode=_rel_mode2 if isinstance(_rel_mode2, str) else None,
                        llm_signals=None,
                    )
                except Exception:
                    logger.debug(
                        "relationship path failed on exception turn (fail-open)", exc_info=True
                    )
                # Phase 6 intimacy evidence on provider exception
                # (fail-open; deterministic user-side evidence still processes).
                try:
                    await _record_intimacy_trajectory(
                        user_id=user_id,
                        creator_id=_creator_id,
                        generation_id=generation_id,
                        user_message=user_message,
                        recent_messages=_rel_recent2,
                        conversation_state=_conv_state,
                    )
                except Exception:
                    logger.debug(
                        "intimacy path failed on exception turn (fail-open)", exc_info=True
                    )
                return

            _one_call_end = _time.monotonic()
            _telemetry_data.generation_latency_ms = int((_one_call_end - _one_call_start) * 1000)
            # Ensure counters persisted even if we fell through without setting (e.g., mock missing)
            try:
                if getattr(_telemetry_data, "one_call_count", 0) == 0:
                    _telemetry_data.one_call_count = 1  # type: ignore
                    _telemetry_data.total_llm_calls = 1  # type: ignore
                    _telemetry_data.ppv_second_generation_count = 0  # type: ignore
                    _telemetry_data.legacy_generation_count = 0  # type: ignore
                    if not getattr(_telemetry_data, "provider_name", None):
                        _telemetry_data.provider_name = getattr(
                            _settings, "llm_provider", "llamacpp"
                        )  # type: ignore
                    if not getattr(_telemetry_data, "model_name", None):
                        _telemetry_data.model_name = getattr(_settings, "llama_model", "default")  # type: ignore
            except Exception:
                pass

        if _llm_path == "legacy":
            # Phase 87: mark legacy path counters (creator-safe)
            try:
                _telemetry_data.legacy_generation_count = 1  # type: ignore
                _telemetry_data.one_call_count = 0  # type: ignore
                _telemetry_data.ppv_second_generation_count = 0  # type: ignore
                _telemetry_data.total_llm_calls = 1  # type: ignore
                _telemetry_data.provider_name = getattr(_settings, "llm_provider", "llamacpp")  # type: ignore
                _telemetry_data.model_name = getattr(_settings, "llama_model", "default")  # type: ignore
            except Exception:
                pass
            # ── LEGACY PATH: 3-LLM pipeline ───────────────────────────────
            # Commerce signal extraction (legacy-only)
            try:
                from commerce.deepseek import extract_commerce_signals

                _commerce_signals = await extract_commerce_signals(context)
                if _commerce_signals:
                    _telemetry_data.commerce_signals_extracted = True
            except Exception:
                logger.debug("Commerce signal extraction failed for user=%s", user_id)
            # H2 custom-acknowledgment guidance (advisory suffix, fail-open):
            # renders ONLY when this turn's advisory intent_tags contain
            # custom_request. Same ``context`` list sink the legacy draft
            # consumes at generate_draft below; move/budget/authority
            # untouched. NOTE: the production OneCall prompt freezes from
            # the snapshot before same-turn signals exist, so this suffix
            # cannot reach the OneCall draft without restructuring (not
            # done); it serves legacy/fallback drafts.
            try:
                from commerce.conversation_strategy import (
                    render_custom_acknowledgment as _render_custom_ack,
                )

                _custom_ack_block = _render_custom_ack(
                    getattr(_commerce_signals, "intent_tags", None)
                )
                if _custom_ack_block and isinstance(context, list):
                    context.append({"role": "system", "content": _custom_ack_block})
            except Exception:
                logger.debug("Custom-ack guidance failed for user=%s (fail-open)", user_id)
            # H3 bot-accusation deflection guidance (advisory suffix, fail-open):
            # renders ONLY when this turn's advisory intent_tags contain
            # operator_request. Same ``context`` list sink the legacy draft
            # consumes at generate_draft below; move/budget/authority
            # untouched. NOTE: same OneCall limitation as H2 (prompt freezes
            # from the snapshot before same-turn signals exist); serves
            # legacy/fallback drafts. Bright line: playful register, zero
            # verifiable claims (fact checks + OOC lists untouched).
            try:
                from commerce.conversation_strategy import (
                    render_botask_deflection as _render_botask,
                )

                _botask_block = _render_botask(
                    getattr(_commerce_signals, "intent_tags", None)
                )
                if _botask_block and isinstance(context, list):
                    context.append({"role": "system", "content": _botask_block})
            except Exception:
                logger.debug("Botask guidance failed for user=%s (fail-open)", user_id)
            # Commerce draft selection — P2 Fix 1 reuse (with fallback for old mocks)
            try:
                selection = await _try_commerce_draft(
                    user_id,
                    context,
                    persona,
                    signals=_commerce_signals,
                    conversation_state=_conv_state,
                    user_message=user_message,
                )
            except TypeError:
                selection = await _try_commerce_draft(
                    user_id, context, persona, signals=_commerce_signals, user_message=user_message
                )
            # G10 evidence-ledger emission only (log; fail-open; no behavior
            # change; does NOT re-enable the commerce draft path).
            try:
                if selection is not None:
                    _g10l_status = getattr(
                        getattr(selection, "status", None),
                        "value",
                        getattr(selection, "status", None),
                    )
                    _g10l_reason = getattr(
                        getattr(selection, "reason", None),
                        "value",
                        getattr(selection, "reason", None),
                    )
                    logger.info(
                        "G10 selection generation_id=%s status=%s reason=%s",
                        generation_id,
                        _g10l_status,
                        _g10l_reason,
                    )
            except Exception:
                pass
            if (
                selection is not None
                and selection.status is CommerceSelectionStatus.USE_COMMERCE_RESPONSE
                and not _boundary_commerce_veto
            ):
                draft = selection.commerce_response_text
                _telemetry_data.routing_decision = "commerce_response"
                logger.info("Commerce draft selected for user %s", user_id)
            elif (
                selection is not None
                and selection.status is CommerceSelectionStatus.USE_COMMERCE_RESPONSE
                and _boundary_commerce_veto
            ):
                # Phase 7: vetoed commerce draft is dropped; neutral
                # generation below owns the turn.
                logger.info(
                    "Boundary commerce veto user=%s reason=%s — dropping commerce draft",
                    user_id,
                    _boundary_commerce_reason,
                )

            # Agent canary check
            try:
                from agent.canary import should_use_agent, CanaryConfig

                _canary_config = CanaryConfig()
                _use_agent = should_use_agent(user_id)
                if _use_agent:
                    _telemetry_data.agent_canary_hit = True
            except Exception:
                logger.debug("Agent canary check failed for user=%s", user_id)

            # Agent runtime
            _provider_start = None
            if _use_agent and not draft:
                try:
                    from agent.runtime import build_agent_state, run_agent_runtime
                    from agent.memory import AgentMemory

                    _agent_state = build_agent_state(
                        user_id=user_id,
                        creator_id=_creator_id,
                        context={"messages": context},
                        user_message=user_message,
                        persona=persona,
                        conversation_history=[
                            {"role": m["role"], "content": m["content"]}
                            for m in context
                            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                        ],
                    )
                    _agent_provider = get_llm_provider()
                    _provider_start = _time.monotonic()
                    _agent_result = await run_agent_runtime(_agent_state, _agent_provider)
                    if _agent_result and _agent_result.success:
                        draft = _agent_result.response_text
                        _telemetry_data.agent_runtime_used = True
                        _telemetry_data.agent_provider = (
                            _agent_provider.provider_name if _agent_provider else "unknown"
                        )
                except Exception as e:
                    logger.debug("Agent runtime failed for user=%s: %s", user_id, e)
                    draft = ""

            # Legacy rollback generation (sole provider, no tools path)
            if not draft:
                _agent_used_inner = False
                if _use_agent and _agent_state:
                    try:
                        from agent.runtime import run_agent_runtime

                        if not _agent_result:
                            _agent_result = await run_agent_runtime(_agent_state, _agent_provider)
                        if _agent_result and _agent_result.success:
                            draft = _agent_result.response_text
                            _agent_used_inner = True
                            _telemetry_data.agent_runtime_used = True
                    except Exception:
                        pass
                if not _agent_used_inner and not draft:
                    draft = await generate_draft(context, user_message)

            # Generation end / scoring start
            _generation_end = _time.monotonic()

            # LLM scoring
            _scoring_start = _time.monotonic()
            score, flags = await score_draft(draft, user_message, context)
            _scoring_end = _time.monotonic()
            _scoring_ms = int((_scoring_end - _scoring_start) * 1000)
            _telemetry_data.scoring_latency_ms = _scoring_ms
            _telemetry_data.scoring_score = score
            _telemetry_data.scoring_flags = flags
            # Phase 5 invalid-output bar (legacy path): an empty/missing
            # draft is not a valid generation and must never auto-send.
            _generation_valid = bool(draft and draft.strip())

        # ── Persona validation with full state ─────────────────────────────
        _persona_validation = None
        _structured_for_val = _persona_snapshot
        _recent_for_val = None
        _behavior_for_val = _persona_behavior_state
        _is_authorized_commerce = False
        _auth_price = None
        _auth_url = None
        _exec_offer_id = None
        _pc_autonomous = _pc_allowed_pre
        _pc_record = None
        _pc_audit = None
        _PcAudit = None
        _allowed = True
        _reason = None
        _audit = None
        try:
            from commerce.persona_validation import validate_persona_voice

            _recent_for_val = _op_loops if _op_loops else None
            _persona_validation = validate_persona_voice(
                response=draft,
                persona=_structured_for_val,
                behavior_state=_behavior_for_val,
                recent_assistant_messages=_recent_for_val,
            )
            if not _persona_validation.valid:
                _telemetry_data.persona_validation_status = _persona_validation.validation_status
                if _persona_validation.severe:
                    flags = flags + ["persona_validation_severe"]
                    # Phase 5: deterministic persona hard block feeds the
                    # routing safety veto (in addition to blocking via flags).
                    _routing_safety_block = True
        except Exception:
            logger.debug("Persona validation failed for user=%s", user_id)

        # ── Authority-aware scoring adjustment ─────────────────────────────
        try:
            if "persona_validation_severe" in flags:
                score = max(0.0, score - 0.10)
                _telemetry_data.authority_score_adjusted = True
        except Exception:
            pass

        # ── Phase 7 boundary enforcement (deterministic common choke) ────
        # Covers canonical OneCall, commerce-draft, agent, and legacy
        # rollback paths: all converge on draft/score/flags here. Prompt
        # guidance alone never enforces; the reply TEXT is checked against
        # the effective snapshot (durable constraints + current-turn
        # evidence, so current explicit evidence beats historical
        # intimacy/relationship). A violation swaps in a fixed safe
        # completion and forces QUEUE via the routing boundary_violation
        # veto (never AUTO_SEND). STOP_CONVERSATION forces QUEUE even when
        # the draft is otherwise clean (brief close goes to the operator).
        # Unknown boundary state (degraded) fails closed to QUEUE.
        # DO_NOT_CONTACT never reaches generation (suppressed above); a
        # violation naming it here is defensive and also queues.
        try:
            from commerce.boundary_validation import (
                safe_completion_for as _safe_completion,
            )
            from commerce.boundary_validation import (
                validate_reply_against_boundaries as _validate_boundaries,
            )

            _bval = None
            try:
                _bval = _validate_boundaries(draft, _boundary_snapshot)
            except Exception:
                _bval = None
            _boundary_degraded = False
            try:
                _boundary_degraded = bool(
                    _boundary_snapshot is None or getattr(_boundary_snapshot, "degraded", False)
                )
            except Exception:
                _boundary_degraded = True
            if _bval is not None and _bval.violated:
                _safe_text = None
                try:
                    _safe_text = _safe_completion(_bval.violations)
                except Exception:
                    _safe_text = None
                if _safe_text is None:
                    # Defensive DO_NOT_CONTACT at generation time: keep the
                    # draft for operator review, never auto-send.
                    flags = list(flags) + [
                        f"boundary_violation:{v.lower()}" for v in _bval.violations
                    ]
                else:
                    draft = _safe_text
                    flags = list(flags) + [
                        f"boundary_violation:{v.lower()}" for v in _bval.violations
                    ]
                _routing_boundary_violation = True
                try:
                    _validation_outcome = "boundary_validation_failure"
                except Exception:
                    pass
                logger.info(
                    "Boundary violation user=%s violations=%s action=%s",
                    user_id,
                    list(_bval.violations),
                    _bval.action,
                )
            elif _boundary_degraded:
                # Fail-closed: unknown boundary state must not auto-send.
                _routing_boundary_violation = True
                try:
                    _validation_outcome = "boundary_state_unknown"
                except Exception:
                    pass
                logger.info("Boundary state unknown user=%s — forcing operator review", user_id)
            else:
                try:
                    _stop_active = bool(
                        _boundary_snapshot is not None and _boundary_snapshot.wants_close()
                    )
                except Exception:
                    _stop_active = False
                if _stop_active:
                    _routing_boundary_violation = True
            # Phase 4: unauthorized soft-sell CTA check (deterministic,
            # all-paths — canonical OneCall, commerce-draft, agent, and
            # legacy rollback paths all converge on draft/score/flags
            # here). A conversational draft carrying a commercial CTA
            # without authority gets flags=["unauthorized_commercial_cta"]
            # and QUEUEs via the existing has_blocking_flags path; the
            # score>=0.80-and-no-flags auto-send is automatically blocked.
            # Authorized commerce (USE_COMMERCE_RESPONSE pre-validated
            # text, sealed PPV, free-photo delivery) never reaches this
            # check as a conversational draft; the exemption below is
            # defense-in-depth. Fail-open: never raises, never rewrites
            # the draft, never touches thresholds/routing/planner.
            try:
                from commerce.commercial_cta import (
                    CTA_FLAG as _cta_flag,
                )
                from commerce.commercial_cta import (
                    detect_unauthorized_commercial_cta as _detect_cta,
                )

                _cta_authorized = False
                try:
                    _cta_selection = locals().get("selection")
                    if _cta_selection is not None and getattr(
                        _cta_selection, "status", None
                    ) is CommerceSelectionStatus.USE_COMMERCE_RESPONSE:
                        _cta_authorized = True
                except Exception:
                    pass
                try:
                    if bool(locals().get("_sealed_ppv_handled")):
                        _cta_authorized = True
                except Exception:
                    pass
                try:
                    _cta_free_result = locals().get("_free_photo_result")
                    if (
                        bool(locals().get("_free_photo_eligible"))
                        and _cta_free_result is not None
                        and getattr(_cta_free_result, "authorization", None) is not None
                        and getattr(
                            getattr(_cta_free_result, "authorization", None),
                            "reservation_id",
                            None,
                        )
                    ):
                        _cta_authorized = True
                except Exception:
                    pass
                _cta_hit, _cta_pattern = _detect_cta(draft, is_authorized=_cta_authorized)
                if _cta_hit:
                    flags = list(flags) + [_cta_flag]
                    logger.info(
                        "Unauthorized commercial CTA user=%s pattern=%s",
                        user_id,
                        _cta_pattern,
                    )
            except Exception:
                logger.debug(
                    "Commercial CTA check failed for user=%s (fail-open)", user_id, exc_info=True
                )
        except Exception:
            logger.debug(
                "Boundary enforcement failed for user=%s (fail-safe queue)", user_id, exc_info=True
            )
            try:
                _routing_boundary_violation = True
            except Exception:
                pass
        # G16 evidence-ledger emission only (log; fail-open; no behavior change).
        # No telemetry schema columns exist for boundary snapshot/violations
        # (insert_generation_telemetry has no boundary fields), so log-only by
        # rule (never new ad-hoc telemetry attrs). Reuses _boundary_snapshot/_bval.
        try:
            try:
                _g16_active = tuple(getattr(_boundary_snapshot, "active", None) or ())
            except Exception:
                _g16_active = ()
            try:
                _g16_degraded = bool(getattr(_boundary_snapshot, "degraded", True))
            except Exception:
                _g16_degraded = True
            try:
                _g16_violations = (
                    tuple(getattr(_bval, "violations", None) or ()) if _bval is not None else ()
                )
            except Exception:
                _g16_violations = ()
            try:
                _g16_action = getattr(_bval, "action", None) if _bval is not None else None
            except Exception:
                _g16_action = None
            logger.info(
                "G16 boundary generation_id=%s active=%s degraded=%s violations=%s action=%s routing_violation=%s",
                generation_id,
                list(_g16_active)[:7],
                _g16_degraded,
                list(_g16_violations)[:7],
                _g16_action,
                bool(_routing_boundary_violation),
            )
        except Exception:
            pass

        # ── Production control metrics ─────────────────────────────────────
        try:
            from commerce.production_control import record_metric

            record_metric(
                name="llm_generation",
                creator_id=_creator_id,
                user_id=user_id,
                strategy=_var,
                topic=_cur_for_beh,
                product_family=_prod_family,
                lifecycle=_lc,
                objective="response_generation",
            )
        except Exception:
            logger.debug("Production control metric recording failed for user=%s", user_id)

        # Q1 shadow evaluation removed with the Ollama shadow stack.
        _shadow_result = None
        _eval = None
        _authoritative_latency_ms = 0
        try:
            _telemetry_data.shadow_latency_ms = 0
        except Exception:
            logger.debug("Shadow evaluation skipped for user=%s", user_id)

        # ── Phase 87: AUTHORITY_DECISION observable (fail-open, creator-safe) ──────────
        try:
            from core.phase87_events import emit_authority_decision

            _auth_action = None
            _auth_status = None
            _auth_product_id = None
            _auth_price_minor = None
            _auth_currency = None
            _auth_eligibility = None
            _auth_execution = None
            # Prefer PPV execution status from new path selection if available
            try:
                if "_oc_selection" in locals() and _oc_selection is not None:
                    _auth_action = getattr(_oc_selection, "status", None)
                    _auth_status = (
                        str(_auth_action.value)
                        if hasattr(_auth_action, "value")
                        else str(_auth_action)
                        if _auth_action
                        else None
                    )
                    # Try product_id from selection or authoritative state
                    _auth_product_id = (
                        getattr(_oc_selection, "product_id", None)
                        or getattr(_oc_selection, "commerce_response_text", None)
                        and None
                    )
                # Fallback to legacy selection
                if _auth_action is None and "selection" in locals() and selection is not None:
                    _auth_action = getattr(selection, "status", None)
                    _auth_status = (
                        str(_auth_action.value)
                        if hasattr(_auth_action, "value")
                        else str(_auth_action)
                        if _auth_action
                        else None
                    )
                # Map to stable taxonomy
                # Use routing_decision as fallback taxonomy mapping
                if not _auth_status:
                    _map = {
                        "one_call": "NO_COMMERCE",
                        "one_call_commerce": "OFFER_PPV",
                        "commerce_response": "OFFER_PPV",
                        "one_call_failed_operator_queue": "EXECUTION_FAILED",
                        "one_call_exception_operator_queue": "EXECUTION_FAILED",
                    }
                    _auth_status = _map.get(
                        getattr(_telemetry_data, "routing_decision", ""), "NO_COMMERCE"
                    )
                    _auth_action = _auth_status
                # Price authority: explicitly fangate_products.price_minor when commerce with price
                _commerce_price_authority = None
                if _auth_status in ("EXECUTED", "ALREADY_EXECUTED", "OFFER_PPV"):
                    _commerce_price_authority = "fangate_products.price_minor"
                    # Try to fetch from authoritative state or selection
                    try:
                        if _authoritative_state and getattr(
                            _authoritative_state, "llm_context", None
                        ):
                            _lc = getattr(_authoritative_state, "llm_context", None)
                            if isinstance(_lc, dict):
                                _auth_price_minor = _lc.get("product_price_minor")
                    except Exception:
                        pass
                    try:
                        _auth_currency = "USD"
                    except Exception:
                        pass
                _telemetry_data.authority_decision = str(_auth_status or "NO_COMMERCE")  # type: ignore
                _telemetry_data.commerce_status = str(_auth_status or "NO_COMMERCE")  # type: ignore
            except Exception:
                pass
            # G12 evidence-ledger emission only (log; fail-open; no behavior change).
            # Authority is quarantined live: product/price stay explicitly None;
            # the price-source string names fangate_products.price_minor only when
            # a commerce status would carry price authority, else "none".
            try:
                _g12_price_source = locals().get("_commerce_price_authority")
                logger.info(
                    "G12 authority generation_id=%s action=%s status=%s product_id=%s "
                    "price_minor=%s currency=%s price_source=%s eligibility_result=%s",
                    generation_id,
                    locals().get("_auth_action"),
                    locals().get("_auth_status"),
                    locals().get("_auth_product_id"),
                    locals().get("_auth_price_minor"),
                    locals().get("_auth_currency"),
                    _g12_price_source
                    if locals().get("_auth_status") in ("EXECUTED", "ALREADY_EXECUTED", "OFFER_PPV")
                    else "none",
                    locals().get("_auth_eligibility"),
                )
            except Exception:
                pass
            await emit_authority_decision(
                user_id=user_id,
                creator_id=_creator_id,
                generation_id=generation_id,
                runtime_mode=_runtime_mode_label,
                action=str(_auth_action) if _auth_action else None,
                status=str(_auth_status) if _auth_status else None,
                product_id=_auth_product_id,
                price_authority=_commerce_price_authority,
                price_minor=_auth_price_minor,
                currency=_auth_currency,
                eligibility_result=None,
                execution_result=str(_auth_status) if _auth_status else None,
            )
        except Exception:
            pass
        # ── Send/handoff decision ──────────────────────────────────────────
        auto_reply_on = await is_auto_reply_enabled()

        dedup_id = hashlib.md5(
            f"{user_id}:{user_message}:{telegram_message_id}".encode()
        ).hexdigest()

        _telemetry_data.auto_reply_enabled = auto_reply_on

        # ── Phase 5: explicit routing decision boundary ────────────────────
        # One identifiable choke point (core/routing.py) mapping validated
        # output + deterministic state to AUTO_SEND/QUEUE. Commercial
        # authority stays deterministic: all inputs below are deterministic
        # facts; the LLM advisory flag is recorded, never authoritative.
        from core.routing import RoutingAction, decide_routing

        _commerce_handoff_required = False
        try:
            from commerce.models import CommerceAction as _CA_ROUTE

            _cd_route = _canonical_decision
            if (
                _cd_route is not None
                and getattr(_cd_route, "action", None) is _CA_ROUTE.OPERATOR_HANDOFF
            ):
                _commerce_handoff_required = True
        except Exception:
            pass
        try:
            # Active deterministic handoff memory (computed above via
            # get_handoff_memory) requires handoff even though it was
            # previously never enforced at routing.
            if _allowed_pre is False:
                _commerce_handoff_required = True
        except Exception:
            pass
        _routing_decision = decide_routing(
            is_valid=bool(_generation_valid),
            boundary_violation=bool(_routing_boundary_violation),
            commerce_deny=False,  # No explicit deterministic deny signal
            # exists in current call-site scope; seal refusals fall back to
            # normal handling per existing architecture (fail-closed there).
            commerce_handoff_required=bool(_commerce_handoff_required),
            sealed_suppressed=bool(_sealed_execution is not None and not _sealed_ppv_handled),
            auto_reply_on=bool(auto_reply_on),
            safety_block=bool(_routing_safety_block),
            score=score,
            auto_approve_threshold=_settings.auto_approve_threshold,
            has_blocking_flags=bool(flags),
            needs_handoff=bool(_routing_needs_handoff),
            advisory_handoff=bool(_routing_advisory_handoff),
            flags=list(flags),
        )
        _routing_forces_queue = _routing_decision.action is RoutingAction.QUEUE
        if _routing_forces_queue:
            # Phase 0.3 baseline: veto log carries full routing context so
            # pre-fix violation/queue rates are greppable without schema change.
            logger.info(
                "Routing veto user=%s generation=%s score=%.3f flags=%s reason=%s advisory_handoff=%s corroborated=%s",
                user_id,
                generation_id,
                score,
                flags,
                _routing_decision.reason,
                _routing_decision.advisory_handoff,
                _routing_decision.corroborated_handoff,
            )
        try:
            _telemetry_data.routing_veto = _routing_decision.reason  # type: ignore
            _telemetry_data.advisory_handoff = _routing_decision.advisory_handoff  # type: ignore
            _telemetry_data.corroborated_handoff = _routing_decision.corroborated_handoff  # type: ignore
        except Exception:
            pass

        # Phase 1.4: HARD-tier subset for the send gate (classification lives
        # in core.routing). Info-tier flags are score-penalty-only. Unknown
        # flags default hard, mirroring decide_routing.
        from core.routing import (
            HARD_QUALITY_FLAGS as _HARD_FLAGS_GATE,
            INFO_QUALITY_FLAGS as _INFO_FLAGS_GATE,
        )

        _has_hard_flags = any(
            f in _HARD_FLAGS_GATE or f not in _INFO_FLAGS_GATE for f in flags
        )

        # Publish operator queue events using batch
        _operator_events: list[dict] = []

        if _sealed_ppv_handled:
            # P3.3.14.4 single-outbound gate: the sealed PPV was already
            # handed to the send stream for this turn, so the redundant
            # normal draft send and operator handoff are suppressed. All
            # downstream processing (events batch, learning, telemetry,
            # post_process) continues unchanged below.
            _sealed_content = getattr(_sealed_execution, "content", None) or ""
            _sealed_offer_id = getattr(_sealed_execution, "offer_id", None)
            _sealed_dedup = getattr(_sealed_execution, "dedup_id", None)
            logger.info(
                "Sealed PPV handled creator=%s user=%s offer_id=%s — suppressing normal outbound",
                _creator_id,
                user_id,
                _sealed_offer_id,
            )
            _telemetry_data.routing_decision = "sealed_ppv"
            _telemetry_data.delivery_status = "sent"  # type: ignore
            _telemetry_data.handoff_reason = None  # type: ignore
            _operator_events.append(
                {
                    "event": "ai.generation_completed",
                    "data": {
                        "draft": _sealed_content,
                        "score": 1.0,
                        "flags": [],
                        "was_auto_approved": True,
                        "sealed_offer_id": _sealed_offer_id,
                        "sealed_dedup_id": _sealed_dedup,
                    },
                    "user_id": user_id,
                    "dialog_id": user_id,
                    "generation_id": generation_id,
                    "creator_id": _creator_id,
                    "scope": "user",
                }
            )
        elif not auto_reply_on:
            # Phase 3.3: single-outbound per (creator,user,tg_id) — one queue
            # row max per turn. A repeat turn is suppressed entirely (never a
            # second send, never a second queue row); downstream telemetry and
            # event-batch processing continue below.
            _queue_turn_ok = True
            if _turn_already_sent:
                _queue_turn_ok = False
            elif not _turn_send_claimed:
                try:
                    from db.redis import try_claim_turn_send as _claim_turn_q

                    _queue_turn_ok = await _claim_turn_q(
                        _creator_id, user_id, telegram_message_id
                    )
                except Exception:
                    _queue_turn_ok = True
            if not _queue_turn_ok:
                logger.info(
                    "Turn already sent creator=%s user=%s tg_id=%s — suppressing second queue row",
                    _creator_id,
                    user_id,
                    telegram_message_id,
                )
                _turn_already_sent = True
                _telemetry_data.routing_decision = "turn_suppressed"
                _telemetry_data.delivery_status = "suppressed"  # type: ignore
                _operator_events.append(
                    {
                        "event": "ai.generation_completed",
                        "data": {
                            "draft": draft,
                            "score": score,
                            "flags": flags,
                            "was_auto_approved": False,
                            "routing_reason": "turn_already_sent",
                        },
                        "user_id": user_id,
                        "dialog_id": user_id,
                        "generation_id": generation_id,
                        "creator_id": _creator_id,
                        "scope": "user",
                    }
                )
            else:
                _turn_send_claimed = True
                queue_id = await add_to_operator_queue(
                    user_id=user_id,
                    draft_content=draft,
                    confidence_score=score,
                    flags=flags,
                    creator_id=_creator_id,
                    generation_id=generation_id,
                )
                logger.info(
                    "Auto-reply off, suggestion %s created for user %s",
                    queue_id,
                    user_id,
                )
                _telemetry_data.routing_decision = "operator_queued"
                _telemetry_data.handoff_reason = "auto_reply_disabled"  # type: ignore
                _telemetry_data.delivery_status = "handoff"  # type: ignore
                try:
                    from core.phase87_events import emit_handoff

                    await emit_handoff(
                        user_id=user_id,
                        creator_id=_creator_id,
                        generation_id=generation_id,
                        runtime_mode=_runtime_mode_label,
                        reason="auto_reply_disabled",
                        queue_id=queue_id,
                    )
                except Exception:
                    pass
                _operator_events.append(
                    {
                        "event": "ai.generation_completed",
                        "data": {
                            "draft": draft,
                            "score": score,
                            "flags": flags,
                            "was_auto_approved": False,
                            "routing_reason": _routing_decision.reason,
                            "advisory_handoff": bool(_routing_advisory_handoff),
                        },
                        "user_id": user_id,
                        "dialog_id": user_id,
                        "generation_id": generation_id,
                        "creator_id": _creator_id,
                        "scope": "user",
                    }
                )
                _operator_events.append(
                    {
                        "event": "suggestion.created",
                        "data": {
                            "queue_id": queue_id,
                            "draft": draft,
                            "score": score,
                            "flags": flags,
                        },
                        "user_id": user_id,
                        "dialog_id": user_id,
                        "generation_id": generation_id,
                        "creator_id": _creator_id,
                        "scope": "user",
                    }
                )
        elif score >= _settings.auto_approve_threshold and not _has_hard_flags and not _routing_forces_queue:
            # Phase 3.3: same turn-send claim — sealed/free-photo (earlier) win;
            # a repeat turn never gets a second wire here.
            _auto_turn_ok = True
            if _turn_already_sent:
                _auto_turn_ok = False
            elif not _turn_send_claimed:
                try:
                    from db.redis import try_claim_turn_send as _claim_turn_auto

                    _auto_turn_ok = await _claim_turn_auto(
                        _creator_id, user_id, telegram_message_id
                    )
                except Exception:
                    _auto_turn_ok = True
            if not _auto_turn_ok:
                logger.info(
                    "Turn already sent creator=%s user=%s tg_id=%s — suppressing second auto-send",
                    _creator_id,
                    user_id,
                    telegram_message_id,
                )
                _turn_already_sent = True
                _telemetry_data.routing_decision = "turn_suppressed"
                _telemetry_data.delivery_status = "suppressed"  # type: ignore
                _operator_events.append(
                    {
                        "event": "ai.generation_completed",
                        "data": {
                            "draft": draft,
                            "score": score,
                            "flags": flags,
                            "was_auto_approved": False,
                            "routing_reason": "turn_already_sent",
                        },
                        "user_id": user_id,
                        "dialog_id": user_id,
                        "generation_id": generation_id,
                        "creator_id": _creator_id,
                        "scope": "user",
                    }
                )
            else:
                _turn_send_claimed = True
                # Pass generation correlation through send stream (creator+generation join)
                await enqueue_send(
                    {
                        "entity": str(user_id),
                        "content": draft,
                        "draft_content": draft,
                        "was_edited": False,
                        "was_auto_approved": True,
                        "confidence_score": score,
                        "operator_id": None,
                        "save_to_db": True,
                        "generation_id": generation_id,
                        "creator_id": str(_creator_id) if _creator_id is not None else "",
                    },
                    dedup_id=dedup_id,
                    generation_id=generation_id,
                    creator_id=_creator_id,
                )
                _telemetry_data.routing_decision = "auto_approved"
                _telemetry_data.delivery_status = "sent"  # type: ignore
                _telemetry_data.handoff_reason = None  # type: ignore
                _operator_events.append(
                    {
                        "event": "ai.generation_completed",
                        "data": {
                            "draft": draft,
                            "score": score,
                            "flags": flags,
                            "was_auto_approved": True,
                            "routing_reason": _routing_decision.reason,
                            "advisory_handoff": bool(_routing_advisory_handoff),
                        },
                        "user_id": user_id,
                        "dialog_id": user_id,
                        "generation_id": generation_id,
                        "creator_id": _creator_id,
                        "scope": "user",
                    }
                )
        else:
            # H1 triage label (metadata only, post-routing): when this
            # queue entry is a commerce handoff with complaint-triage
            # evidence, append complaint:payment_technical |
            # complaint:payment_claim | complaint:experience to a copy of
            # the flags for the queue row / operator notify / queue events.
            # Routing already decided above; the decision inputs stay
            # untouched. Fan words correlate via generation_id. No schema
            # or signature change.
            _queue_flags = flags
            try:
                if bool(_commerce_handoff_required):
                    from commerce.complaint_triage_evidence import (
                        extract_complaint_triage_evidence as _extract_triage_for_queue,
                    )

                    _triage_ev = (
                        _extract_triage_for_queue(user_message)
                        if isinstance(user_message, str) and user_message
                        else None
                    )
                    _triage_label = (
                        _triage_ev.triage_label() if _triage_ev is not None else None
                    )
                    if _triage_label:
                        _queue_flags = list(flags) + [f"complaint:{_triage_label}"]
            except Exception:
                _queue_flags = flags
            # H2 custom summary token (metadata only, post-routing): when
            # this queue entry is a custom handoff, append
            # "custom:<truncated-need>" (~120 chars, card-data redacted,
            # fail-open None) to the same flags copy. Custom attribution
            # prefers the canonical decision's handoff_reason, falling back
            # to this turn's advisory intent_tags. No signature/schema/API
            # change; fan words correlate via generation_id.
            try:
                if bool(_commerce_handoff_required):
                    from commerce.conversation_strategy import (
                        custom_need_token as _custom_need_token,
                    )

                    _custom_hr = None
                    try:
                        _cd_meta = getattr(_canonical_decision, "metadata", None)
                        if isinstance(_cd_meta, dict):
                            _custom_hr = _cd_meta.get("handoff_reason")
                    except Exception:
                        _custom_hr = None
                    _custom_tags: list[str] = []
                    try:
                        _sig_tags = getattr(_commerce_signals, "intent_tags", None)
                        if isinstance(_sig_tags, (list, tuple, set, frozenset)):
                            _custom_tags = [str(t) for t in _sig_tags]
                    except Exception:
                        _custom_tags = []
                    if _custom_hr == "custom_request" or "custom_request" in _custom_tags:
                        _custom_token = _custom_need_token(user_message, _custom_tags)
                        if _custom_token:
                            _queue_flags = list(_queue_flags) + [_custom_token]
            except Exception:
                pass
            # H3 bot-ask token (metadata only, post-routing): when this
            # queue entry carries a bot-accusation turn, append
            # "botask:repeat" (second ask) or "botask:complaint-combo"
            # (accusation + already-firing complaint) to the same flags
            # copy. Repeat is re-derived turn-locally over the prompt
            # history (no writes, no new state). No signature/schema/API
            # change; fan words correlate via generation_id.
            try:
                if bool(_commerce_handoff_required):
                    from commerce.conversation_strategy import (
                        botask_queue_token as _botask_token_fn,
                    )
                    from commerce.conversation_strategy import (
                        had_prior_bot_accusation as _had_prior_botask_q,
                    )

                    _b_tags: list[str] = []
                    try:
                        _sig_tags = getattr(_commerce_signals, "intent_tags", None)
                        if isinstance(_sig_tags, (list, tuple, set, frozenset)):
                            _b_tags = [str(t) for t in _sig_tags]
                    except Exception:
                        _b_tags = []
                    _b_neg: list[str] = []
                    _b_sent: Any = None
                    try:
                        _sig_neg = getattr(_commerce_signals, "negative_intent_tags", None)
                        if isinstance(_sig_neg, (list, tuple, set, frozenset)):
                            _b_neg = [str(t) for t in _sig_neg]
                        _b_sent = getattr(_commerce_signals, "negative_sentiment", None)
                    except Exception:
                        _b_neg = []
                        _b_sent = None
                    _b_rep = False
                    try:
                        _b_rep = bool(_had_prior_botask_q(context))
                    except Exception:
                        _b_rep = False
                    _b_tok = _botask_token_fn(
                        intent_tags=_b_tags,
                        negative_intent_tags=_b_neg,
                        negative_sentiment=_b_sent,
                        repeated=_b_rep,
                    )
                    if _b_tok:
                        _queue_flags = list(_queue_flags) + [_b_tok]
            except Exception:
                pass
            # Phase 3.3: same turn-send claim — one queue row max per turn.
            _handoff_turn_ok = True
            if _turn_already_sent:
                _handoff_turn_ok = False
            elif not _turn_send_claimed:
                try:
                    from db.redis import try_claim_turn_send as _claim_turn_h

                    _handoff_turn_ok = await _claim_turn_h(
                        _creator_id, user_id, telegram_message_id
                    )
                except Exception:
                    _handoff_turn_ok = True
            if not _handoff_turn_ok:
                logger.info(
                    "Turn already sent creator=%s user=%s tg_id=%s — suppressing second queue row",
                    _creator_id,
                    user_id,
                    telegram_message_id,
                )
                _turn_already_sent = True
                _telemetry_data.routing_decision = "turn_suppressed"
                _telemetry_data.delivery_status = "suppressed"  # type: ignore
                _operator_events.append(
                    {
                        "event": "ai.generation_completed",
                        "data": {
                            "draft": draft,
                            "score": score,
                            "flags": _queue_flags,
                            "was_auto_approved": False,
                            "routing_reason": "turn_already_sent",
                        },
                        "user_id": user_id,
                        "dialog_id": user_id,
                        "generation_id": generation_id,
                        "creator_id": _creator_id,
                        "scope": "user",
                    }
                )
            else:
                _turn_send_claimed = True
                queue_id = await add_to_operator_queue(
                    user_id=user_id,
                    draft_content=draft,
                    confidence_score=score,
                    flags=_queue_flags,
                    creator_id=_creator_id,
                    generation_id=generation_id,
                )
                await notify_operators(queue_id, user_id, draft, score, _queue_flags)
                _telemetry_data.routing_decision = "operator_queued"
                # Phase 5: when the routing boundary forced the queue, the veto
                # reason is the handoff reason (not the flags join); otherwise
                # preserve the existing flags-joined reason contract.
                _queue_handoff_reason = (
                    _routing_decision.reason
                    if _routing_forces_queue
                    else (",".join(flags) if flags else "operator_queued")
                )
                _telemetry_data.handoff_reason = _queue_handoff_reason  # type: ignore
                _telemetry_data.delivery_status = "handoff"  # type: ignore
                try:
                    from core.phase87_events import emit_handoff

                    await emit_handoff(
                        user_id=user_id,
                        creator_id=_creator_id,
                        generation_id=generation_id,
                        runtime_mode=_runtime_mode_label,
                        reason=_queue_handoff_reason,
                        queue_id=queue_id,
                    )
                except Exception:
                    pass
                _operator_events.append(
                    {
                        "event": "ai.generation_completed",
                        "data": {
                            "draft": draft,
                            "score": score,
                            "flags": _queue_flags,
                            "was_auto_approved": False,
                            "routing_reason": _routing_decision.reason,
                            "advisory_handoff": bool(_routing_advisory_handoff),
                        },
                        "user_id": user_id,
                        "dialog_id": user_id,
                        "generation_id": generation_id,
                        "creator_id": _creator_id,
                        "scope": "user",
                    }
                )
                _operator_events.append(
                    {
                        "event": "suggestion.created",
                        "data": {
                            "queue_id": queue_id,
                            "draft": draft,
                            "score": score,
                            "flags": _queue_flags,
                        },
                        "user_id": user_id,
                        "dialog_id": user_id,
                        "generation_id": generation_id,
                        "creator_id": _creator_id,
                        "scope": "user",
                    }
                )

        # ── Publish events batch ───────────────────────────────────────────
        try:
            await publish_events_batch(_operator_events)
        except Exception:
            try:
                for _ev in _operator_events:
                    await publish_event(
                        _ev["event"],
                        _ev["data"],
                        user_id=_ev["user_id"],
                        dialog_id=_ev["dialog_id"],
                        generation_id=_ev["generation_id"],
                        creator_id=_ev.get("creator_id", _creator_id),
                        scope=_ev["scope"],
                    )
            except Exception:
                logger.warning("Failed to publish operator events for user %s", user_id)

        # ── Phase 2 relationship trajectory (fail-open, never blocks) ──
        # Canonical grain: ONE successfully claimed/processed inbound turn
        # (debounce already collapsed rapid messages; nothing here
        # reconstructs collapsed messages). Runs after the send/queue
        # decision so persistence failure cannot block response generation,
        # sending, commerce processing, or operator handoff. Deterministic
        # user-side evidence processes even when generation failed
        # (llm_signals absent, assistant invalid); LLM signals flow only
        # from validated OneCallResult.signals on the new path.
        try:
            _rel_recent3 = None
            _rel_contract3 = None
            _rel_mode3 = None
            _rel_signals3 = None
            _rel_asst_text: str | None = None
            _rel_asst_valid = False
            try:
                if _authoritative_state is not None:
                    _rel_recent3 = getattr(_authoritative_state, "recent_messages", None)
                    _rel_contract3 = getattr(_authoritative_state, "conversation_contract", None)
            except Exception:
                pass
            if _rel_recent3 is None:
                try:
                    _rel_recent3 = context
                except Exception:
                    _rel_recent3 = None
            try:
                if _persona_behavior_state is not None:
                    _rel_mode3 = getattr(_persona_behavior_state, "conversation_mode", None)
            except Exception:
                pass
            if _rel_mode3 is None:
                try:
                    _rel_mode3 = _response_mode
                except Exception:
                    _rel_mode3 = None
            try:
                if _sealed_ppv_handled:
                    _rel_asst_text = _sealed_content if isinstance(_sealed_content, str) else ""
                    _rel_asst_valid = bool(_rel_asst_text and _rel_asst_text.strip())
                elif _llm_path == "new":
                    _rel_asst_text = draft if isinstance(draft, str) else ""
                    try:
                        _rel_asst_valid = bool(
                            _one_call_result.is_valid and _rel_asst_text and _rel_asst_text.strip()
                        )
                    except Exception:
                        _rel_asst_valid = bool(_rel_asst_text and _rel_asst_text.strip())
                    try:
                        if _rel_asst_valid and _commerce_signals is not None:
                            _rel_signals3 = _commerce_signals
                    except Exception:
                        _rel_signals3 = None
                else:
                    _rel_asst_text = draft if isinstance(draft, str) else ""
                    try:
                        _rel_asst_valid = bool(
                            _generation_valid and _rel_asst_text and _rel_asst_text.strip()
                        )
                    except Exception:
                        _rel_asst_valid = bool(_rel_asst_text and _rel_asst_text.strip())
                    _rel_signals3 = None
            except Exception:
                pass
            await _record_relationship_trajectory(
                user_id=user_id,
                creator_id=_creator_id,
                generation_id=generation_id,
                user_message=user_message,
                recent_messages=_rel_recent3,
                conversation_state=_conv_state,
                conversation_contract=_rel_contract3,
                profile=_cached_profile_for_commerce,
                current_topic=_cur_for_beh,
                open_threads=_open_for_beh,
                assistant_text=_rel_asst_text,
                assistant_valid=_rel_asst_valid,
                conversation_mode=_rel_mode3 if isinstance(_rel_mode3, str) else None,
                llm_signals=_rel_signals3,
            )
        except Exception:
            logger.debug("relationship path failed on main turn (fail-open)", exc_info=True)

        # ── Phase 6 intimacy trajectory (fail-open, never blocks) ──
        # Same grain as Phase 2: one processed turn, idempotent per
        # generation_id, descriptive state only (no permission, consent,
        # safety-policy, or commerce semantics).
        try:
            _int_ci3 = None
            _int_ec3: Any = None
            try:
                if _rel_signals3 is not None:
                    _int_ci3 = getattr(_rel_signals3, "content_interest", None)
                    _int_ec3 = getattr(_rel_signals3, "explicit_content_request", None)
            except Exception:
                _int_ci3 = None
                _int_ec3 = None
            await _record_intimacy_trajectory(
                user_id=user_id,
                creator_id=_creator_id,
                generation_id=generation_id,
                user_message=user_message,
                recent_messages=_rel_recent3,
                conversation_state=_conv_state,
                assistant_text=_rel_asst_text,
                assistant_valid=_rel_asst_valid,
                llm_content_interest=_int_ci3,
                llm_explicit_content=_int_ec3,
            )
        except Exception:
            logger.debug("intimacy path failed on main turn (fail-open)", exc_info=True)

        # ── Strategy learning (full pipeline) ──────────────────────────────
        _facts_prev = None
        _get_profile_strat = None
        _get_profile_strat_fallback = None
        _by_creator_prev = None
        _prev_strategy = None
        _clf_legacy = None
        _clf_canon = None
        _out_strength = None
        _desire_after_val = None
        _outcome_obj = None
        _canon = None
        _topic_for_ev = None
        _pf_for_ev = None
        _lc_for_ev = None
        _stage_for = None
        _strategy_name = None
        _facts_new = None
        _by_creator_new = None
        _exps = None
        _last = None
        _exp_time = None
        _get_exp_mem = None
        try:
            # Phase 10 fix: import each primitive from its authoritative
            # module. A single missing name must not swallow the rest.
            try:
                from commerce.conversation_outcomes import classify_outcome as _p10_classify_legacy
            except Exception:
                _p10_classify_legacy = None
            try:
                from commerce.adaptive_optimization import (
                    classify_canonical_outcome as _p10_classify_canon,
                    outcome_strength as _p10_strength,
                )
            except Exception:
                _p10_classify_canon = None
                _p10_strength = None
            try:
                from commerce.conversation_outcomes import classify_outcome as _p10_co_legacy

                if _p10_classify_legacy is None:
                    _p10_classify_legacy = _p10_co_legacy
            except Exception:
                pass
            try:
                from commerce.strategy_learning import (
                    update_strategy_evidence,
                    update_strategy_evidence_extended,
                )
            except Exception:
                update_strategy_evidence = None  # type: ignore
                update_strategy_evidence_extended = None  # type: ignore
            try:
                from commerce.adaptive_optimization import stage_for_objective as _p10_stage_for
            except Exception:
                _p10_stage_for = None  # type: ignore
            try:
                from commerce.adaptive_optimization import get_exposures_memory as _p10_get_exp
            except Exception:
                _p10_get_exp = None  # type: ignore
            from db.postgres import update_user_profile, get_user_profile

            _facts_prev = _cached_profile_for_commerce if _cached_profile_for_commerce else None
            if _facts_prev is None:
                try:
                    _facts_prev = await get_user_profile(user_id) if get_user_profile else None
                except Exception:
                    _facts_prev = None
            _get_profile_strat = get_user_profile
            _prev_strategy = _facts_prev.get("strategy", "default") if _facts_prev else "default"
            _by_creator_prev = _facts_prev.get("by_creator", {}) if _facts_prev else {}

            try:
                _clf_legacy = (
                    _p10_classify_legacy(
                        previous_strategy=_prev_strategy,
                        fan_message=user_message,
                        desire_before=desire or "unknown",
                        desire_after=desire or "unknown",
                    )
                    if _p10_classify_legacy
                    else None
                )
            except Exception:
                _clf_legacy = None
            try:
                # Phase 10 fix: canonical classifier has no previous_strategy
                # and takes a single outcome for strength (no _pressure/_risk).
                _canon = (
                    _p10_classify_canon(
                        fan_message=user_message,
                        desire_before=desire or "unknown",
                        desire_after=desire or "unknown",
                    )
                    if _p10_classify_canon
                    else None
                )
            except Exception:
                _canon = None
            try:
                _canon_val = (
                    _canon.value
                    if hasattr(_canon, "value")
                    else (str(_canon).lower() if _canon else None)
                )
                _legacy_val = (
                    _clf_legacy.value
                    if hasattr(_clf_legacy, "value")
                    else (str(_clf_legacy).lower() if _clf_legacy else None)
                )
                _out_strength = (
                    _p10_strength(_canon_val or _legacy_val)
                    if (_p10_strength and (_canon_val or _legacy_val))
                    else None
                )
            except Exception:
                _out_strength = None

            try:
                _desire_after_val = desire or "unknown"
                _outcome_obj = {
                    "strategy": _prev_strategy,
                    "outcome": _canon.value
                    if hasattr(_canon, "value")
                    else str(_canon)
                    if _canon
                    else "unknown",
                    "strength": _out_strength if _out_strength else 0.0,
                    "desire_after": _desire_after_val,
                }
            except Exception:
                pass

            if _creator_id is not None:
                try:
                    if update_strategy_evidence is not None:
                        await update_strategy_evidence(
                            _creator_id,
                            user_id,
                            _prev_strategy or "default",
                            (_canon.value if hasattr(_canon, "value") else str(_canon))
                            if _canon
                            else "neutral_engagement",
                        )
                        _telemetry_data.strategy_learning_updated = True
                except Exception:
                    pass
                try:
                    _topic_for_ev = _cur_for_beh if _cur_for_beh else None
                    _pf_for_ev = _prod_family
                    _lc_for_ev = _lc
                    _stage_for = (
                        _p10_stage_for(_commercial_objective)
                        if (_p10_stage_for and _commercial_objective)
                        else "unknown"
                    )
                    _strategy_name = _prev_strategy
                    if update_strategy_evidence_extended is not None:
                        await update_strategy_evidence_extended(
                            creator_id=_creator_id,
                            user_id=user_id,
                            strategy=_strategy_name or "default",
                            outcome=(_canon.value if hasattr(_canon, "value") else str(_canon))
                            if _canon
                            else "neutral",
                            topic=_topic_for_ev,
                            product_family=_pf_for_ev,
                            lifecycle_stage=_lc_for_ev,
                            generation_id=generation_id,
                        )
                        _telemetry_data.strategy_learning_extended = True
                except Exception:
                    pass
                try:
                    _facts_new = await get_user_profile(user_id) if get_user_profile else None
                    _by_creator_new = _facts_new.get("by_creator", {}) if _facts_new else {}
                    if _by_creator_new != _by_creator_prev:
                        await update_user_profile(user_id, {"by_creator": _by_creator_new})
                except Exception:
                    pass
                try:
                    # Phase 10 fix: conservative purchase attribution. The old
                    # call passed (strategy, objective) which matches neither
                    # commerce.attribution.attribute_purchase (needs
                    # product_id + transaction_id) nor adaptive window
                    # attribution (needs exposure/purchase times + transaction
                    # evidence). Without DropFans transaction evidence we must
                    # NOT guess — record unattributed and leave the ledger to
                    # the webhook path (single-winner P3.5.2 authority).
                    from commerce.phase10_learning import (
                        attribute_generation_outcome as _p10_attr,
                        find_exposure_for_generation as _p10_find,
                        canonical_to_relationship as _p10_rel,
                        canonical_to_commerce as _p10_com,
                    )

                    _p10_exp_list = []
                    try:
                        if _p10_get_exp is not None:
                            import inspect as _insp2

                            _maybe2 = _p10_get_exp(_creator_id, user_id)
                            _p10_exp_list = (
                                await _maybe2 if _insp2.isawaitable(_maybe2) else _maybe2
                            )
                    except Exception:
                        _p10_exp_list = []
                    _p10_exp = _p10_find(
                        _p10_exp_list or [],
                        creator_id=_creator_id,
                        user_id=user_id,
                        generation_id=generation_id,
                    )
                    _canon_key = (
                        _canon.value
                        if hasattr(_canon, "value")
                        else (str(_canon).lower() if _canon else None)
                    )
                    _last_purchase = (
                        (_cstate.get("last_purchase") if _cstate else None)
                        if isinstance(_cstate, dict)
                        else None
                    )
                    _txn = None
                    if isinstance(_last_purchase, dict):
                        _txn = _last_purchase.get("transaction_id")
                    _has_txn = bool(_txn and str(_txn).strip())
                    _rel_out = _p10_rel(_canon_key) if _canon_key else None
                    _com_out = _p10_com(_canon_key) if _canon_key else None
                    if _last_purchase and not _has_txn:
                        # Purchase claim without transaction evidence stays
                        # unavailable (never a supervised positive).
                        _com_out = None
                    _p10_attr_res = _p10_attr(
                        creator_id=_creator_id,
                        user_id=user_id,
                        generation_id=generation_id,
                        exposure=_p10_exp,
                        relationship_outcome=_rel_out.value if _rel_out else None,
                        commerce_outcome=(_com_out.value if _com_out else None),
                        maturity_state="MATURE",
                        has_transaction_evidence=_has_txn,
                    )
                    try:
                        _telemetry_data.attribution_status = _p10_attr_res.status
                        _telemetry_data.relationship_outcome = _p10_attr_res.relationship_outcome
                        _telemetry_data.commerce_outcome = _p10_attr_res.commerce_outcome
                    except Exception:
                        pass
                    try:
                        # Phase 10 §15: smallest post-response outcome capture
                        # from subsequent observable behavior (not LLM prose).
                        # Observational only: reason codes feed telemetry,
                        # never authority. No durable intimacy facts here.
                        from commerce.phase10_learning import (
                            capture_post_response_outcome as _p10_capture,
                        )

                        _p10_msg = user_message or ""
                        _p10_low = _p10_msg.lower()
                        _p10_declined = (
                            any(w in _p10_low for w in ("no", "not interested", "stop", "nah"))
                            and len(_p10_msg.strip()) < 24
                        )
                        _p10_boundary = any(
                            w in _p10_low for w in ("stop", "don't", "dont", "leave me", "boundary")
                        )
                        _p10_observed = _p10_capture(
                            user_continued_topic=bool(_cur_for_beh and len(_p10_msg.strip()) > 4),
                            user_returned=False,
                            positive_signal=(_canon_key == "positive_engagement"),
                            negative_signal=(
                                _canon_key in ("objection", "rejection", "desire_decrease")
                            ),
                            withdrew=(_canon_key in ("rejection", "cooldown")),
                            boundary=_p10_boundary,
                            content_interest_continued=False,
                            purchase_confirmed_by_webhook=_has_txn,
                            transaction_id=_txn,
                            offer_presented=bool(_canon_key == "offer_request"),
                            declined=_p10_declined,
                        )
                        try:
                            _telemetry_data.maturity_state = "MATURE"
                            _telemetry_data.maturity_policy_version = "p353b.v1"
                        except Exception:
                            pass
                    except Exception:
                        pass
                except Exception:
                    pass
                try:
                    _get_exp_mem = _p10_get_exp
                    if _get_exp_mem and _creator_id:
                        import inspect as _insp3

                        _maybe3 = _get_exp_mem(_creator_id, user_id)
                        _exps = await _maybe3 if _insp3.isawaitable(_maybe3) else _maybe3
                    else:
                        _exps = []
                    if _exps:
                        _last = _exps[-1] if _exps else None
                        _exp_time = (
                            _last.get("timestamp") if _last and isinstance(_last, dict) else None
                        )
                except Exception:
                    pass
        except Exception:
            logger.debug("Strategy learning failed for user=%s", user_id)

        # ── Conversation outcomes ──────────────────────────────────────────
        try:
            from commerce.conversation_outcomes import classify_outcome

            _outcome = classify_outcome(
                previous_strategy=_prev_strategy or "default",
                fan_message=user_message,
                desire_before=desire or "unknown",
                desire_after=desire or "unknown",
            )
            _telemetry_data.conversation_outcome = (
                _outcome.value if hasattr(_outcome, "value") else str(_outcome)
            )
        except Exception:
            logger.debug("Conversation outcome classification failed for user=%s", user_id)

        # ── Telemetry enrichment with funnel data ─────────────────────────
        try:
            # Phase 10 fix: enrich_telemetry_with_funnel(telemetry, *,
            # funnel_*) takes funnel kwargs only (no user_id/creator_id).
            # Call with defaults (all None) fail-open, then stamp Phase 10
            # version/namespace attribution (bounded, PII-free).
            from commerce.revenue_intelligence import enrich_telemetry_with_funnel

            enrich_telemetry_with_funnel(_telemetry_data)
        except Exception:
            logger.debug("Telemetry enrichment failed for user=%s", user_id)
        try:
            from commerce.phase10_learning import apply_phase10_telemetry as _p10_tel

            _p10_tel(
                _telemetry_data,
                creator_scope=_creator_id,
                relationship_outcome=getattr(_telemetry_data, "relationship_outcome", None),
                commerce_outcome=getattr(_telemetry_data, "commerce_outcome", None),
                attribution_status=getattr(_telemetry_data, "attribution_status", None),
                maturity_state=getattr(_telemetry_data, "maturity_state", None),
                experiment_id=getattr(_telemetry_data, "experiment_id", None),
                experiment_variant=getattr(_telemetry_data, "experiment_variant", None),
            )
        except Exception:
            logger.debug("Phase 10 telemetry stamping failed for user=%s", user_id)

        # Phase 87: finalize generation counters defaults if not set (fail-open)
        try:
            if (
                not hasattr(_telemetry_data, "one_call_count")
                or _telemetry_data.one_call_count is None
            ):
                _telemetry_data.one_call_count = 0  # type: ignore
            if (
                getattr(_telemetry_data, "total_llm_calls", 0) == 0
                and getattr(_telemetry_data, "one_call_count", 0) == 0
            ):
                # Default for paths that didn't set (e.g., early fail-closed)
                if _telemetry_data.routing_decision in (
                    "fail_closed_operator",
                    "operator_queued",
                    "auto_approved",
                    "one_call",
                    "one_call_commerce",
                ):
                    _telemetry_data.one_call_count = (
                        1
                        if "one_call" in str(_telemetry_data.routing_decision)
                        or _telemetry_data.routing_decision in ("auto_approved", "operator_queued")
                        else 0
                    )  # type: ignore
                    _telemetry_data.total_llm_calls = int(_telemetry_data.one_call_count or 0)  # type: ignore
            # Ensure provider/model present
            if not getattr(_telemetry_data, "provider_name", None):
                _telemetry_data.provider_name = getattr(_settings, "llm_provider", "llamacpp")  # type: ignore
            if not getattr(_telemetry_data, "model_name", None):
                _telemetry_data.model_name = getattr(_settings, "llama_model", "default")  # type: ignore
            # Duplicate/redis metrics defaults
            if not hasattr(_telemetry_data, "duplicate_send_suppressed_count"):
                _telemetry_data.duplicate_send_suppressed_count = 0  # type: ignore
        except Exception:
            pass
        # Phase 87: persist telemetry best-effort (fail-open, never blocks delivery)
        try:
            _telemetry_data.complete(success=True)
            # Use background task to avoid blocking send
            try:
                import asyncio as _asyncio_record

                loop = _asyncio_record.get_event_loop()
                if loop.is_running():
                    loop.create_task(_telemetry.record(_telemetry_data))
                else:
                    await _telemetry.record(_telemetry_data)
            except Exception:
                await _telemetry.record(_telemetry_data)
        except Exception:
            logger.debug("telemetry record failed (fail-open)", exc_info=True)
        # ── Post-processing (async) ────────────────────────────────────────
        asyncio.create_task(post_process(user_id, creator_id=_creator_id))

    except Exception:
        logger.exception("Error processing message for user %s", user_id)
        try:
            await publish_event(
                "ai.generation_failed",
                {"error": "Generation failed"},
                user_id=user_id,
                dialog_id=user_id,
                generation_id=generation_id,
                creator_id=_creator_id,
                scope="user",
            )
        except Exception:  # noqa: BLE001
            logger.warning("Failed to publish ai.generation_failed event for user %s", user_id)
        raise
    finally:
        pass  # lock release handled by run_worker after ACK


async def run_worker(worker_id: str) -> None:
    await init_pool()
    await ensure_consumer_group(consumer_name=worker_id)

    # P3.2C F1: commerce schema readiness gate. Messaging continues regardless,
    # but autonomous commerce execution is refused until the P3.2 safety schema
    # verifies PRESENT. ABSENT and UNKNOWN both disable commerce (unknown is
    # never treated as healthy). The gate re-checks periodically (TTL), so
    # applying the migration later enables commerce without a restart.
    try:
        from commerce.execution import check_commerce_schema_ready

        _commerce_ready, _commerce_reason = await check_commerce_schema_ready(force=True)
    except Exception as exc:  # noqa: BLE001 — readiness check itself failed
        _commerce_ready, _commerce_reason = False, "commerce_schema_unknown"
        logger.warning(
            "commerce readiness check failed (%s) — autonomous commerce DISABLED",
            exc.__class__.__name__,
        )
    if _commerce_ready:
        logger.info("commerce readiness: P3.2 schema PRESENT — autonomous commerce ENABLED")
    else:
        logger.warning(
            "commerce readiness: %s — autonomous commerce DISABLED until P3.2 schema verifies PRESENT",
            _commerce_reason,
        )

    # Load persisted state (production control)
    try:
        from commerce.production_control import load_persisted_state

        _loaded = await load_persisted_state()
        logger.info("production_control: loaded persisted state %s", _loaded)
    except Exception:
        logger.debug("production_control: load persisted state failed", exc_info=True)

    # Warm embedding model
    try:
        from commerce.embedding_model import get_model

        _ensure_reference_cache = get_model
        _t_warm = _time.monotonic()
        _model_warm = _ensure_reference_cache()
        _t0 = _time.monotonic() - _t_warm
        logger.info("Local intelligence warmup: model loaded in %.2fs", _t0)
    except Exception:
        logger.debug("Local intelligence warmup failed", exc_info=True)

    # P1.6 R-01: startup reconciliation for DB->Redis crash gap (bounded, fail-open, timeout 2s)
    try:
        from db.redis import reconcile_inbound_gaps

        try:
            _r = await asyncio.wait_for(
                reconcile_inbound_gaps(lookback_seconds=300, limit=100), timeout=2.0
            )
            if _r:
                logger.info("P1.6 startup reconcile_inbound_gaps recovered %d entries", _r)
        except asyncio.TimeoutError:
            logger.debug("P1.6 startup reconcile timed out")
    except Exception:
        logger.debug("P1.6 startup reconciliation failed", exc_info=True)

    loop = asyncio.get_running_loop()
    heartbeat_stop = asyncio.Event()
    setup_signal_handlers([_worker_cleanup, lambda: heartbeat_stop.set()], loop=loop)

    logger.info("Worker %s started", worker_id)

    _heartbeat_task = asyncio.create_task(
        write_heartbeat(
            worker_id,
            worker_type="llm",
            interval_seconds=_settings.worker_heartbeat_interval,
            ttl_seconds=_settings.worker_heartbeat_ttl,
            stop_event=heartbeat_stop,
        )
    )

    _last_reconcile = 0.0
    while not is_shutting_down():
        try:
            # P1.6 R-01 periodic inbound gap reconciliation (bounded, throttle 60s, fail-open, timeout 2s)
            try:
                import time as _t2

                _now2 = _t2.monotonic()
                if _now2 - _last_reconcile > 60:
                    from db.redis import reconcile_inbound_gaps

                    try:
                        _rr = await asyncio.wait_for(
                            reconcile_inbound_gaps(lookback_seconds=300, limit=50), timeout=2.0
                        )
                        if _rr:
                            logger.info(
                                "P1.6 periodic reconcile_inbound_gaps recovered %d entries", _rr
                            )
                    except asyncio.TimeoutError:
                        logger.debug("periodic reconcile timed out")
                    _last_reconcile = _now2
            except Exception:
                logger.debug("periodic inbound reconcile failed", exc_info=True)
            stale_count, claimed = await requeue_stalled_messages(
                worker_id, idle_ms=_settings.redis_pending_idle_ms
            )
            if stale_count > 0:
                # claimed is list[(msg_id, fields)] with full payload preserved (Stage B fix)
                stale_ids = [mid for mid, _ in claimed]
                logger.info(
                    "Reclaimed %d stalled inbound messages: %s",
                    stale_count,
                    stale_ids,
                )
                for msg_id, data in claimed:
                    # Process reclaimed entry via existing path (preserves ACK/DLQ/dedup)
                    # P1.4: creator_id from payload is authoritative – validate before processing
                    try:
                        _msg_user_id = int(data["user_id"])
                    except (KeyError, ValueError, TypeError):
                        logger.warning("Reclaimed inbound %s has invalid user_id, DLQing", msg_id)
                        try:
                            await move_to_dlq(
                                msg_id,
                                "invalid_payload",
                                payload=dict(data) if isinstance(data, dict) else None,
                                worker_id=worker_id,
                            )
                        except Exception:
                            logger.exception("Failed to DLQ invalid reclaimed %s", msg_id)
                        continue
                    _rcid_raw = data.get("creator_id") if isinstance(data, dict) else None
                    if not _rcid_raw or not str(_rcid_raw).strip().isdigit():
                        logger.warning(
                            "Reclaimed inbound %s missing/invalid creator_id, failing closed DLQ",
                            msg_id,
                        )
                        try:
                            await move_to_dlq(
                                msg_id,
                                "creator_context_unavailable",
                                payload=dict(data) if isinstance(data, dict) else None,
                                worker_id=worker_id,
                            )
                        except Exception:
                            logger.exception(
                                "Failed to DLQ reclaimed %s for missing creator", msg_id
                            )
                        continue
                    _rcid_int_claim = int(str(_rcid_raw).strip())
                    _is_contention = False
                    try:
                        msg_data = {
                            "user_id": _msg_user_id,
                            "user_message": data.get("content", ""),
                            "telegram_message_id": int(data.get("telegram_message_id", "0") or "0"),
                            "username": data.get("username", ""),
                            "first_name": data.get("first_name", ""),
                            "persona": data.get("persona", ""),
                            "creator_id": _rcid_int_claim,
                        }
                        # Preserve generation_id if present in original stream entry
                        _gid = data.get("generation_id")
                        if _gid:
                            msg_data["generation_id"] = _gid  # type: ignore
                        msg_data["is_redelivery"] = True  # type: ignore

                        await process_message(**msg_data)  # type: ignore[arg-type]

                        await ack_inbound(msg_id)

                    except UserLockContentionError:
                        _is_contention = True
                        logger.info(
                            "Lock contention for reclaimed user %s, deferring message %s for retry (no ACK)",
                            _msg_user_id,
                            msg_id,
                        )
                    except Exception:
                        logger.exception("Failed to process reclaimed message %s", msg_id)
                        await move_to_dlq(
                            msg_id,
                            "processing_error",
                            payload=dict(data) if isinstance(data, dict) else None,
                            worker_id=worker_id,
                        )
                    finally:
                        if not _is_contention:
                            try:
                                _rcid = data.get("creator_id") if isinstance(data, dict) else None
                                _rcid_int = int(_rcid) if _rcid and str(_rcid).isdigit() else None
                                await release_user_lock(_msg_user_id, creator_id=_rcid_int)
                            except Exception:
                                pass

            messages = await read_inbound(worker_id, count=5, block_ms=2000)

            if not messages:
                pass
            else:
                for stream, stream_messages in messages:
                    for msg_id, data in stream_messages:
                        try:
                            _msg_user_id = int(data["user_id"])
                        except (KeyError, ValueError, TypeError):
                            logger.warning("Inbound %s has invalid user_id, DLQing", msg_id)
                            try:
                                await move_to_dlq(
                                    msg_id,
                                    "invalid_payload",
                                    payload=dict(data),
                                    worker_id=worker_id,
                                )
                            except Exception:
                                logger.exception("Failed to DLQ inbound %s", msg_id)
                            continue
                        _rcid_raw2 = data.get("creator_id") if isinstance(data, dict) else None
                        if not _rcid_raw2 or not str(_rcid_raw2).strip().isdigit():
                            logger.warning(
                                "Inbound %s missing/invalid creator_id, failing closed DLQ", msg_id
                            )
                            try:
                                await move_to_dlq(
                                    msg_id,
                                    "creator_context_unavailable",
                                    payload=dict(data),
                                    worker_id=worker_id,
                                )
                            except Exception:
                                logger.exception(
                                    "Failed to DLQ inbound %s for missing creator", msg_id
                                )
                            continue
                        _rcid_int2 = int(str(_rcid_raw2).strip())
                        _is_contention2 = False
                        try:
                            # Phases 1-4: prefer the stream's generation_id verbatim
                            # (authoritative correlation); recomputation inside
                            # process_message is recovery-only when absent.
                            msg_data = {
                                "user_id": _msg_user_id,
                                "user_message": data["content"],
                                "telegram_message_id": int(data["telegram_message_id"]),
                                "username": data.get("username", ""),
                                "first_name": data.get("first_name", ""),
                                "persona": data.get("persona", ""),
                                "creator_id": _rcid_int2,
                                "generation_id": data.get("generation_id") or None,
                            }

                            await process_message(**msg_data)

                            await ack_inbound(msg_id)

                        except UserLockContentionError:
                            _is_contention2 = True
                            logger.info(
                                "Lock contention for user %s, deferring message %s for retry (no ACK)",
                                _msg_user_id,
                                msg_id,
                            )
                        except Exception:
                            logger.exception("Failed to process message %s", msg_id)
                            await move_to_dlq(
                                msg_id,
                                "processing_error",
                                payload=dict(data),
                                worker_id=worker_id,
                            )
                        finally:
                            if not _is_contention2:
                                try:
                                    _rcid2 = (
                                        data.get("creator_id") if isinstance(data, dict) else None
                                    )
                                    _rcid2_int = (
                                        int(_rcid2) if _rcid2 and str(_rcid2).isdigit() else None
                                    )
                                    await release_user_lock(_msg_user_id, creator_id=_rcid2_int)
                                except Exception:
                                    pass

        except Exception:
            logger.exception("Worker loop error")
            await asyncio.sleep(1)

        if is_shutting_down():
            break

        await asyncio.sleep(0.5)


async def _worker_cleanup() -> None:
    from db.postgres import close_pool
    from db.redis import close_redis

    logger.info("LLM worker shutting down...")
    await close_pool()
    await close_redis()


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM Worker")
    parser.add_argument("--worker-id", required=True, help="Unique worker ID")
    args = parser.parse_args()

    _settings_local = get_settings()
    setup_logging(structured=_settings_local.structured_logging)

    asyncio.run(run_worker(args.worker_id))


if __name__ == "__main__":
    main()
