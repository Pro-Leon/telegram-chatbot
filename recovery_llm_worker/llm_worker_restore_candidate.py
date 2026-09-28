import argparse
import asyncio
import hashlib
import logging
import time as _time
import uuid
from datetime import datetime, timezone
from typing import Any

from google.genai import types as gtypes

from commerce.integration import resolve_and_run_commerce
from commerce.pipeline import PIPELINE_MAX_MESSAGES
from commerce.product_selection import resolve_commerce_product_with_history
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
from core.gemini_client import (
    check_rate_limit,
    get_credential,
    get_pool,
    record_request,
)
from core.llm_provider import get_llm_provider
from core.llm_tools import (
    TOOL_AUTHORITY_PROMPT,
    ToolAuthContext,
    dispatch_tool,
    get_gemini_function_declarations,
)
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
from memory.context import build_qwen3_context
from memory.profile import extract_and_update_profile
from memory.summarizer import maybe_summarize

logger = logging.getLogger("llm_worker")
_settings = get_settings()
_worker_preferred_index: int = 0


def _parse_worker_preferred_index(worker_id: str) -> int:
    """Extract a numeric suffix from worker_id for credential affinity.

    'worker_1' -> 0, 'worker_2' -> 1, 'sender_1' -> 0, 'worker' -> 0
    """
    try:
        num = int(worker_id.rsplit("_", 1)[-1])
        pool = get_pool()
        return (num - 1) % pool.size
    except (ValueError, IndexError, RuntimeError):
        return 0


async def generate_draft(
    context_messages: list[dict],
    user_message: str,
    model: str = _settings.model_name,
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
    messages.append({"role": "user", "content": user_message})

    merged_system = "\n\n".join(p for p in system_parts if p)

    provider = get_llm_provider()

    last_error: Exception | None = None
    providers_to_try: list[str] = []
    if _settings.llm_provider and _settings.llm_provider.strip():
        providers_to_try = [p.strip() for p in _settings.llm_provider.split(",") if p.strip()]
    if not providers_to_try:
        providers_to_try = ["ollama"]

    for provider_name in providers_to_try:
        try:
            if provider_name == "ollama":
                from core.llm_provider_ollama import OllamaProvider
                response_text = await provider.generate_with_history(
                    system_instruction=merged_system,
                    messages=messages,
                    model=model,
                    max_output_tokens=_settings.max_tokens,
                    temperature=_settings.temperature,
                    top_p=0.95,
                )
                return response_text
            else:
                response_text = await provider.generate_with_history(
                    system_instruction=merged_system,
                    messages=messages,
                    model=model,
                    max_output_tokens=_settings.max_tokens,
                    temperature=_settings.temperature,
                    top_p=0.95,
                )
                return response_text
        except Exception as e:
            last_error = e
            logger.exception("generate_draft failed for provider %s", provider_name)
            if _is_quota_error(e):
                from core.daily_quota import mark_quota_exhausted
                mark_quota_exhausted(provider_name)
                logger.info("Quota exhausted for provider %s", provider_name)
                continue
            if _settings.gemini_fallback_enabled and provider_name != "ollama":
                logger.warning("Falling back to ollama after %s failure", provider_name)
                continue
            break

    logger.exception("generate_draft failed for all providers")
    return ""


def _is_quota_error(e: Exception) -> bool:
    """Check if an exception indicates quota exhaustion."""
    err_str = str(e).lower()
    return "quota" in err_str or "429" in err_str or "resource_exhausted" in err_str


async def generate_draft_with_tools(
    context_messages: list[dict],
    user_message: str,
    auth: ToolAuthContext,
    model: str = _settings.model_name,
    max_tool_calls: int | None = None,
) -> str:
    """Generate a draft with bounded tool calling.

    When the model returns a function call, the tool is validated,
    authorized, and executed.  The result is appended to the conversation
    and the model is called again.  This repeats up to max_tool_calls
    times.  If the model returns text, that text is returned.

    When tools are disabled or no tools are available, falls back to
    plain text generation.
    """
    if max_tool_calls is None:
        max_tool_calls = _settings.llm_max_tool_calls

    tool_declarations = get_gemini_function_declarations()
    if not tool_declarations:
        return await generate_draft(context_messages, user_message, model)

    # Check if provider supports tool calling
    provider = get_llm_provider()
    if not provider.supports_tool_calling():
        # Fall back to plain text generation if provider doesn't support tools
        logger.info("Provider %s does not support tool calling, falling back to plain text", provider.provider_name)
        return await generate_draft(context_messages, user_message, model)

    # Build system instruction with tool authority
    system_parts: list[str] = []
    conversation_parts: list[gtypes.Content] = []
    for m in context_messages:
        role = m.get("role")
        text = m.get("content", "")
        if role == "system":
            system_parts.append(text)
        elif role in ("user", "model", "assistant"):
            conversation_parts.append(
                gtypes.Content(
                    role="user" if role == "user" else "model",
                    parts=[gtypes.Part(text=text)],
                )
            )
    conversation_parts.append(
        gtypes.Content(role="user", parts=[gtypes.Part(text=user_message)])
    )

    merged_system = "\n\n".join(p for p in system_parts if p)
    merged_system += "\n\n" + TOOL_AUTHORITY_PROMPT

    tool_config = gtypes.ToolConfig(
        function_calling_config=gtypes.FunctionCallingConfig(mode="AUTO")
    )

    tools = [gtypes.Tool(function_declarations=tool_declarations)]

    # Check daily quota for Gemini tools
    _gemini_tool_error = None
    if _settings.gemini_fallback_enabled:
        try:
            from core.daily_quota import check_daily_quota
            daily_limit = check_daily_quota("gemini")
            if daily_limit:
                logger.warning("Gemini daily quota reached, falling back to plain text")
                return await generate_draft(context_messages, user_message, model)
        except Exception:
            pass

    credential = get_credential(preferred_index=_worker_preferred_index)
    client = credential.get_client()

    # Bounded tool loop
    for _call_idx in range(max_tool_calls + 1):
        config = gtypes.GenerateContentConfig(
            temperature=_settings.temperature,
            max_output_tokens=_settings.max_tokens,
            top_p=0.95,
            tools=tools,
            tool_config=tool_config,
            automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )
        if merged_system:
            config.system_instruction = merged_system

        if not check_rate_limit(credential):
            logger.debug(
                "Tool draft rate-limited for credential ...%s", credential.key[-4:]
            )

        response = await client.aio.models.generate_content(
            model=model,
            contents=conversation_parts,
            config=config,
        )
        record_request(credential)

        # Check for function calls
        if not response.function_calls:
            return response.text or ""

        # Process each function call
        function_response_parts: list[gtypes.Part] = []
        for fc in response.function_calls:
            tool_name = fc.name or ""
            tool_args = fc.args or {}
            logger.info("tool_requested tool=%s args_keys=%s", tool_name, list(tool_args.keys()))

            try:
                result = await dispatch_tool(tool_name, tool_args, auth)
            except Exception:
                logger.exception("tool_dispatch_unexpected tool=%s", tool_name)
                from core.llm_tools import ToolResult as _TR, ToolErrorCode as _TE
                result = _TR(
                    success=False,
                    error_code=_TE.TOOL_EXCEPTION,
                    safe_message="Unexpected tool error.",
                )

            function_response_parts.append(
                gtypes.Part.from_function_response(
                    name=tool_name,
                    response={
                        "success": result.success,
                        "data": result.data,
                        "error_code": result.error_code,
                        "message": result.safe_message,
                    },
                )
            )

        # Append model's function call and our response to conversation
        if response.candidates and response.candidates[0].content:
            conversation_parts.append(response.candidates[0].content)
        conversation_parts.append(
            gtypes.Content(role="user", parts=function_response_parts)
        )

    # Exceeded max tool calls -- return last text if available
    logger.warning(
        "max_tool_calls reached (limit=%d) -- returning last response",
        max_tool_calls,
    )
    return response.text if response and response.text else ""


async def _try_commerce_draft(
    user_id: int,
    context: list[dict],
    persona: str,
    signals: Any | None = None,
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

    try:
        creator = await resolve_single_application_creator()
        if creator.status is not SingleCreatorStatus.READY:
            logger.info(
                "Single-creator resolution %s for user %s (standard draft path)",
                creator.status.value,
                user_id,
            )
        product_id = None
        if creator.status is SingleCreatorStatus.READY and creator.creator_id is not None:
            product_id = await resolve_commerce_product_with_history(
                creator.creator_id, user_id
            )

        # Derive conversation state for product selection
        _cur_topic: str | None = None
        _open_threads: tuple[str, ...] = ()
        _prefs: dict[str, Any] = {}
        try:
            from core.conversation_state import derive_conversation_state
            _conv_state_for_product = await derive_conversation_state(user_id, creator.creator_id)
            _cur_topic = _conv_state_for_product.get("current_topic") if _conv_state_for_product else None
            _open_threads = tuple(_conv_state_for_product.get("open_threads", ())) if _conv_state_for_product else ()
        except Exception:
            pass

        # Get user and profile for product selection
        _user_for_product = None
        _profile_for_product = None
        try:
            from db.postgres import get_user as _get_user_for_product
            _user_for_product = await _get_user_for_product(user_id)
        except Exception:
            pass
        try:
            from memory.context import get_last_profile
            _profile_for_product = await get_last_profile(user_id)
            if isinstance(_profile_for_product, str):
                _profile_for_product = None
        except Exception:
            pass

        messages = (
            context[-PIPELINE_MAX_MESSAGES:] if len(context) > PIPELINE_MAX_MESSAGES else context
        )
        request = CommerceStateRequest(
            user_id=user_id,
            creator_id=creator.creator_id,
            product_id=product_id,
            messages=messages,
            persona=persona or None,
        )
        outcome = await resolve_and_run_commerce(request=request)
        selection = select_commerce_response(outcome)
    except Exception:  # noqa: BLE001 — commerce must never break normal messaging
        logger.warning("Commerce attempt skipped for user %s (safe fallback)", user_id)
        return None
    logger.info(
        "Commerce attempt user=%s status=%s reason=%s",
        user_id,
        selection.status.value,
        selection.reason.value,
    )
    return selection


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


async def post_process(user_id: int) -> None:
    try:
        recent = await get_recent_messages(user_id, limit=20)
        # Use the actual user message count for summarization gating,
        # not len(recent) which is capped at 20.
        from db.postgres import get_user as _get_user_for_count

        user = await _get_user_for_count(user_id)
        message_count = user.get("message_count", 0) if user else len(recent)

        await extract_and_update_profile(user_id, recent)
        await maybe_summarize(user_id, message_count)
    except Exception:
        logger.exception("post_process failed for user %s", user_id)


async def process_message(
    user_id: int,
    user_message: str,
    telegram_message_id: int,
    username: str,
    first_name: str,
    persona: str,
    generation_id: str | None = None,
) -> None:
    from core.event_bus import publish_event, publish_events_batch

    # ── Generation ID (deterministic hash) ─────────────────────────────────
    if generation_id is None:
        generation_id = hashlib.md5(
            f"{user_id}:{user_message}:{telegram_message_id}".encode()
        ).hexdigest()

    # ── Telemetry ──────────────────────────────────────────────────────────
    from core.telemetry import get_telemetry_collector
    _telemetry = get_telemetry_collector()
    _telemetry_data = _telemetry.start_generation(
        user_id=user_id, creator_id=None, runtime_mode="legacy", generation_id=generation_id
    )

    # ── Creator resolution (with lock propagation) ─────────────────────────
    _creator_id: int | None = None
    _creator_sales_enabled: bool = False
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

    locked = await acquire_user_lock(user_id, ttl=_settings.user_lock_ttl)
    if not locked:
        logger.info("User %s already locked, skipping", user_id)
        return

    try:
        # ── User upsert ────────────────────────────────────────────────────
        await upsert_user(user_id, username, first_name)

        if await is_user_auto_reply_excluded(user_id):
            logger.info("User %s is excluded from auto-reply, skipping", user_id)
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
            _persona_snapshot = await get_structured_persona_async(user_id)
            if _persona_snapshot:
                _persona_snapshot_version = _persona_snapshot.get("persona_version")
        except Exception:
            logger.debug("Persona snapshot fetch failed for user=%s", user_id)

        # ── Context build ──────────────────────────────────────────────────
        _context_start = _time.monotonic()
        context = await build_qwen3_context(user_id, user_message, persona, creator_id=_creator_id)
        _context_end = _time.monotonic()
        _context_build_ms = int((_context_end - _context_start) * 1000)
        _telemetry_data.context_build_ms = _context_build_ms

        # ── Context timing and persona block ───────────────────────────────
        _gen_context_chars = sum(len(m.get("content", "")) for m in context if isinstance(m, dict))
        _persona_block = ""
        try:
            _last_sys = next((m for m in reversed(context) if isinstance(m, dict) and m.get("role") == "system"), None)
            if _last_sys:
                _persona_block = _last_sys.get("content", "")[:200]
        except Exception:
            pass
        _telemetry_data.context_chars = _gen_context_chars

        # ── Publish ai.generation_started ──────────────────────────────────
        await publish_event(
            "ai.generation_started",
            {"message_preview": user_message[:100]},
            user_id=user_id,
            dialog_id=user_id,
            generation_id=generation_id,
            scope="user",
        )

        # ── Context Engine observation ─────────────────────────────────────
        _context_engine_observation = None
        try:
            from context_engine.worker_integration import observe_context_engine
            _context_engine_observation = await observe_context_engine(
                user_id=user_id,
                creator_id=_creator_id,
                user_message=user_message,
                generation_id=generation_id,
                persona_snapshot=_persona_snapshot,
                enabled=_settings.context_engine_observational,
            )
            if _context_engine_observation and _context_engine_observation.enabled:
                _telemetry_data.context_engine_enabled = True
                _telemetry_data.context_engine_ms = _context_engine_observation.total_ms
                _telemetry_data.context_engine_gather_ms = _context_engine_observation.gather_ms
                _telemetry_data.context_engine_candidates = _context_engine_observation.candidate_count
                _telemetry_data.context_engine_selected = _context_engine_observation.selected_count
                _telemetry_data.context_engine_dropped = _context_engine_observation.dropped_count
                _telemetry_data.context_engine_tokens = _context_engine_observation.token_count
                _telemetry_data.context_engine_chars = _context_engine_observation.char_count
            _telemetry_data.context_engine_observed = True
        except Exception:
            _telemetry_data.context_engine_observed = False

        # ── Fail-closed routing ────────────────────────────────────────────
        if _fail_closed_creator_unavailable:
            _fail_qid = await add_to_operator_queue(
                user_id=user_id,
                draft_content="",
                confidence_score=0.0,
                flags=["creator_context_unavailable"],
            )
            _telemetry_data.routing_decision = "fail_closed_operator"
            await publish_events_batch([
                {
                    "event": "ai.generation_completed",
                    "data": {"draft": "", "score": 0.0, "flags": ["creator_context_unavailable"], "was_auto_approved": False},
                    "user_id": user_id, "dialog_id": user_id,
                    "generation_id": generation_id, "scope": "user",
                },
                {
                    "event": "suggestion.created",
                    "data": {"queue_id": _fail_qid, "draft": "", "score": 0.0, "flags": ["creator_context_unavailable"]},
                    "user_id": user_id, "dialog_id": user_id,
                    "generation_id": generation_id, "scope": "user",
                },
            ])
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
                        add_memory_item(mem, user_id=user_id, creator_id=_creator_id)
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
                extract_fan_knowledge, add_knowledge_item, get_knowledge_memory,
            )
            if _creator_id is not None:
                existing = await get_knowledge_memory(user_id, _creator_id)
                fan_items = extract_fan_knowledge(
                    user_message, _creator_id, user_id, generation_id=generation_id
                )
                for it in (fan_items or []):
                    _item_dict = it.__dict__ if hasattr(it, '__dict__') else it
                    _fan_knowledge.append(_item_dict)
                    try:
                        await add_knowledge_item(_item_dict, user_id=user_id, creator_id=_creator_id)
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
                    _creator_id, user_id, "message_received", user_message[:100],
                    generation_id=generation_id, hour_utc=hour_utc,
                )
        except Exception:
            logger.debug("Behavioral signal recording failed for user=%s", user_id)

        # ── Q1 Shadow launch ──────────────────────────────────────────────
        _shadow_start = _time.monotonic()
        _shadow_task = None
        _shadow_cfg = None
        _shadow_runner = None
        _signals_for_both = _commerce_signals if '_commerce_signals' in dir() else None
        try:
            from core.qwen3_shadow import ShadowRunner, ShadowConfig
            _shadow_cfg = ShadowConfig.from_settings()
            if _shadow_cfg.enabled and _shadow_cfg.should_sample(user_id) and _creator_id is not None:
                _shadow_runner = ShadowRunner(_shadow_cfg)
                _shadow_task = asyncio.create_task(
                    _shadow_runner.run_shadow(
                        context_messages=context,
                        user_message=user_message,
                        creator_id=_creator_id,
                        user_id=user_id,
                    )
                )
                _telemetry_data.shadow_launched = True
        except Exception:
            logger.debug("Shadow launch failed for user=%s", user_id)

        # ── Commerce signal extraction ─────────────────────────────────────
        _commerce_signals = None
        try:
            from commerce.deepseek import extract_commerce_signals
            _commerce_signals = await extract_commerce_signals(context)
            if _commerce_signals:
                _telemetry_data.commerce_signals_extracted = True
        except Exception:
            logger.debug("Commerce signal extraction failed for user=%s", user_id)

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
        try:
            from core.conversation_state import derive_conversation_state
            _conv_state = await derive_conversation_state(user_id, _creator_id)
        except Exception:
            pass
        try:
            from db.postgres import get_user as _get_user_fn
            _get_cached_user = _get_user_fn
            _user_for_state = await _get_cached_user(user_id) if _get_cached_user else None
        except Exception:
            pass
        try:
            _get_profile_cache = get_user_profile
            _cached_profile_for_commerce = await _get_profile_cache(user_id) if _get_profile_cache else None
        except Exception:
            pass
        try:
            from commerce.conversational import build_conversational_commerce_state
            _cstate = await build_conversational_commerce_state(
                user_id=user_id,
                creator_id=_creator_id,
                context=context,
                signals=_commerce_signals,
                current_topic=_conv_state.get("current_topic") if _conv_state else None,
                profile=_cached_profile_for_commerce,
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
        except Exception:
            logger.debug("Commerce state derivation failed for user=%s", user_id)

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
                make_exposure, persist_exposure, compute_fatigue,
                get_exposures_memory, deterministic_assignment, assign_variant,
            )
            _recent_exps = await get_exposures_memory(user_id, _creator_id) if _creator_id else []
            _fat = compute_fatigue(_recent_exps) if _recent_exps else 0.0
            _strat_family = _cstate.get("strategy_family") if _cstate else None
            _prod_family = _cstate.get("product_family") if _cstate else None
            _exposure = make_exposure(
                user_id=user_id, creator_id=_creator_id,
                strategy_family=_strat_family, product_family=_prod_family,
                fatigue=_fat, generation_id=generation_id,
            )
            if _exposure:
                await persist_exposure(_exposure)
                _exp_id = _exposure.get("experiment_id") if isinstance(_exposure, dict) else getattr(_exposure, "experiment_id", None)
                _var = _exposure.get("variant") if isinstance(_exposure, dict) else getattr(_exposure, "variant", None)
                _exp = _exposure
                _telemetry_data.experiment_exposure = _exp_id
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
                compute_pressure, derive_risk, build_operation_decision, derive_lifecycle, LifecycleState,
            )
            try:
                _lc_obj = derive_lifecycle(user_id, _creator_id, _cstate) if _cstate else LifecycleState.NEW
                _lc = _lc_obj.value if hasattr(_lc_obj, "value") else str(_lc_obj)
            except Exception:
                _lc = "new"
            try:
                from commerce.dao import get_timing_context, get_behavioral_feedback_context
                _timing_ctx = await get_timing_context(_creator_id, user_id) if _creator_id else {}
                _behavioral_ctx = await get_behavioral_feedback_context(_creator_id, user_id) if _creator_id else {}
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
                _commerce_signals, _cstate if _cstate else desire,
            )
            _telemetry_data.commercial_objective = _commercial_objective
        except Exception:
            pass
        try:
            from commerce.production_control import (
                autonomous_allowed, is_rollout_active_for, is_commerce_paused, is_reengagement_paused,
                record_metric, record_audit, OperationalAuditRecord,
            )
            _pc_allowed_pre = autonomous_allowed(
                creator_id=_creator_id, user_id=user_id,
                objective=_commercial_objective, strategy=_var,
            )
            _rollout_blocked = is_rollout_active_for(creator_id=_creator_id) if _creator_id else False
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
                    creator_id=_creator_id, user_id=user_id,
                    strategy=_var, topic=None,
                    product_family=_prod_family, lifecycle=_lc,
                    objective=_commercial_objective,
                )
            except Exception:
                pass
            try:
                _PcAudit = OperationalAuditRecord
                _audit = _PcAudit(
                    creator_id=_creator_id, user_id=user_id,
                    objective=_commercial_objective, strategy=_var,
                    risk_state=_risk, pressure=_pressure,
                    allowed=_pc_allowed_pre, reason=_reason_pre,
                )
                record_audit(_audit)
            except Exception:
                pass
            _obj_for_gate = _commercial_objective
        except Exception:
            logger.debug("Commercial objective / production control gate failed for user=%s", user_id)

        # ── Operational intelligence ───────────────────────────────────────
        _op_health = None
        _op_loops = None
        _op_decision = None
        try:
            from commerce.operational_intelligence import operational_decision
            from commerce.operational_execution import (
                execute_operational_recommendation, evaluate_production_health, MetricWindow,
            )
            _op_decision = operational_decision(
                creator_id=_creator_id, user_id=user_id,
                signals=_commerce_signals, pressure=_pressure, risk_state=_risk,
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
                        _op_decision, user_id=user_id, creator_id=_creator_id,
                    )
            except Exception:
                pass
        except Exception:
            logger.debug("Operational intelligence failed for user=%s", user_id)

        # ── Commerce draft selection ───────────────────────────────────────
        selection = await _try_commerce_draft(user_id, context, persona, signals=_commerce_signals)
        draft = ""
        if (
            selection is not None
            and selection.status is CommerceSelectionStatus.USE_COMMERCE_RESPONSE
        ):
            draft = selection.commerce_response_text
            _telemetry_data.routing_decision = "commerce_response"
            logger.info("Commerce draft selected for user %s", user_id)

        # ── Agent canary check ─────────────────────────────────────────────
        _use_agent = False
        _canary_config = None
        _generation_end = None
        _scoring_start = None
        _scoring_end = None
        try:
            from agent.canary import should_use_agent, CanaryConfig
            _canary_config = CanaryConfig()
            _use_agent = should_use_agent(user_id)
            if _use_agent:
                _telemetry_data.agent_canary_hit = True
        except Exception:
            logger.debug("Agent canary check failed for user=%s", user_id)

        # ── Agent runtime ──────────────────────────────────────────────────
        _agent_state = None
        _agent_result = None
        _agent_provider = None
        _provider_start = None
        if _use_agent and not draft:
            try:
                from agent.runtime import build_agent_state, run_agent_runtime
                from agent.memory import AgentMemory
                _agent_state = build_agent_state(
                    user_id=user_id, creator_id=_creator_id,
                    context=context, user_message=user_message, persona=persona,
                )
                _agent_provider = get_llm_provider()
                _provider_start = _time.monotonic()
                _agent_result = await run_agent_runtime(_agent_state)
                if _agent_result and _agent_result.success:
                    draft = _agent_result.response_text
                    _telemetry_data.agent_runtime_used = True
                    _telemetry_data.agent_provider = _agent_provider.provider_name if _agent_provider else "unknown"
            except Exception as e:
                logger.debug("Agent runtime failed for user=%s: %s", user_id, e)
                try:
                    _auth_user_fb = None
                    from db.postgres import get_user as _get_user_for_auth_cached_fb
                    _auth_user_fb = await _get_user_for_auth_cached_fb(user_id) if _get_user_for_auth_cached_fb else {}
                    _auth_user = _auth_user_fb or {}
                    auth_ctx = ToolAuthContext(
                        creator_id=_creator_id, user_id=user_id,
                        creator_sales_enabled=_creator_sales_enabled,
                        user_is_blocked=bool(_auth_user.get("is_blocked", False)),
                        user_do_not_auto_reply=bool(_auth_user.get("do_not_auto_reply", False)),
                        funnel_stage=_auth_user.get("funnel_stage", "new"),
                    )
                except Exception:
                    pass
                draft = ""
                _agent_used = False

        # ── Persona behavior with knowledge ────────────────────────────────
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
            _cur_for_beh = _conv_state.get("current_topic") if _conv_state else None
            _open_for_beh = _conv_state.get("open_threads") if _conv_state else None
            _cobj_for_beh = _commercial_objective
            _nba_for_beh = _action_str
            _recent_for_behavior = _op_loops if _op_loops else None
            retrieve_relevant_knowledge_fn = None
            try:
                from commerce.fan_knowledge import retrieve_relevant_knowledge
                retrieve_relevant_knowledge_fn = retrieve_relevant_knowledge
                if retrieve_relevant_knowledge_fn and _creator_id:
                    _fan_know_for_behavior = await retrieve_relevant_knowledge(
                        user_id=user_id, creator_id=_creator_id,
                        query=user_message, limit=5,
                    )
            except Exception:
                pass
            from commerce.persona_behavior import derive_persona_behavior_state, render_persona_behavior_block
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
                creator_id=_creator_id,
                generation_id=generation_id,
            )
            _telemetry_data.persona_behavior_derived = True
        except Exception:
            logger.debug("Persona behavior with knowledge failed for user=%s", user_id)

        # ── Tool-aware generation path (when no draft from commerce/agent) ──
        if not draft:
            if _settings.llm_tools_enabled and _creator_id is not None:
                from db.postgres import get_user as _get_user_for_auth

                _auth_user = await _get_user_for_auth(user_id) or {}
                _get_user_for_auth_cached = _get_user_for_auth
                auth_ctx = ToolAuthContext(
                    creator_id=_creator_id,
                    user_id=user_id,
                    creator_sales_enabled=_creator_sales_enabled,
                    user_is_blocked=bool(_auth_user.get("is_blocked", False)),
                    user_do_not_auto_reply=bool(
                        _auth_user.get("do_not_auto_reply", False)
                    ),
                    funnel_stage=_auth_user.get("funnel_stage", "new"),
                )
                draft = await generate_draft_with_tools(
                    context, user_message, auth_ctx
                )
            else:
                _agent_used_inner = False
                if _use_agent and _agent_state:
                    try:
                        from agent.runtime import run_agent_runtime
                        if not _agent_result:
                            _agent_result = await run_agent_runtime(_agent_state)
                        if _agent_result and _agent_result.success:
                            draft = _agent_result.response_text
                            _agent_used_inner = True
                            _telemetry_data.agent_runtime_used = True
                    except Exception:
                        pass
                if not _agent_used_inner and not draft:
                    _legacy_provider = get_llm_provider()
                    draft = await generate_draft(context, user_message)

        # ── Generation end / scoring start ─────────────────────────────────
        _generation_end = _time.monotonic()

        # ── Scoring ────────────────────────────────────────────────────────
        _scoring_start = _time.monotonic()
        score, flags = await score_draft(draft, user_message, context)
        _scoring_end = _time.monotonic()
        _scoring_ms = int((_scoring_end - _scoring_start) * 1000)
        _telemetry_data.scoring_latency_ms = _scoring_ms
        _telemetry_data.scoring_score = score
        _telemetry_data.scoring_flags = flags

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
        except Exception:
            logger.debug("Persona validation failed for user=%s", user_id)

        # ── Authority-aware scoring adjustment ─────────────────────────────
        try:
            if "persona_validation_severe" in flags:
                score = max(0.0, score - 0.10)
                _telemetry_data.authority_score_adjusted = True
        except Exception:
            pass

        # ── Production control metrics ─────────────────────────────────────
        try:
            from commerce.production_control import record_metric
            record_metric(
                name="llm_generation",
                creator_id=_creator_id, user_id=user_id,
                strategy=_var, topic=_cur_for_beh,
                product_family=_prod_family, lifecycle=_lc,
                objective="response_generation",
            )
        except Exception:
            logger.debug("Production control metric recording failed for user=%s", user_id)

        # ── Q1 Shadow evaluation ──────────────────────────────────────────
        _shadow_end = None
        _authoritative_latency_ms = None
        _shadow_result = None
        _eval = None
        try:
            if _shadow_task and not _shadow_task.done():
                _shadow_end = _time.monotonic()
                _authoritative_latency_ms = int((_shadow_end - _shadow_start) * 1000)
                try:
                    _shadow_result = await asyncio.wait_for(_shadow_task, timeout=5.0)
                except asyncio.TimeoutError:
                    _shadow_result = None
                except Exception:
                    _shadow_result = None
                if _shadow_result:
                    try:
                        from core.qwen3_shadow import evaluate_shadow_response, log_shadow_summary
                        _eval = evaluate_shadow_response(draft, _shadow_result)
                        log_shadow_summary(_eval, user_id=user_id, generation_id=generation_id)
                        _telemetry_data.shadow_evaluated = True
                    except Exception:
                        pass
            _telemetry_data.shadow_latency_ms = _authoritative_latency_ms or 0
        except Exception:
            logger.debug("Shadow evaluation failed for user=%s", user_id)

        # ── Send/handoff decision ──────────────────────────────────────────
        auto_reply_on = await is_auto_reply_enabled()

        dedup_id = hashlib.md5(
            f"{user_id}:{user_message}:{telegram_message_id}".encode()
        ).hexdigest()

        _telemetry_data.auto_reply_enabled = auto_reply_on

        # Publish operator queue events using batch
        _operator_events: list[dict] = []

        if not auto_reply_on:
            queue_id = await add_to_operator_queue(
                user_id=user_id,
                draft_content=draft,
                confidence_score=score,
                flags=flags,
            )
            logger.info(
                "Auto-reply off, suggestion %s created for user %s",
                queue_id,
                user_id,
            )
            _telemetry_data.routing_decision = "operator_queued"
            _operator_events.append({
                "event": "ai.generation_completed",
                "data": {
                    "draft": draft,
                    "score": score,
                    "flags": flags,
                    "was_auto_approved": False,
                },
                "user_id": user_id,
                "dialog_id": user_id,
                "generation_id": generation_id,
                "scope": "user",
            })
            _operator_events.append({
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
                "scope": "user",
            })
        elif score >= _settings.auto_approve_threshold and not flags:
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
                },
                dedup_id=dedup_id,
            )
            _telemetry_data.routing_decision = "auto_approved"
            _operator_events.append({
                "event": "ai.generation_completed",
                "data": {
                    "draft": draft,
                    "score": score,
                    "flags": flags,
                    "was_auto_approved": True,
                },
                "user_id": user_id,
                "dialog_id": user_id,
                "generation_id": generation_id,
                "scope": "user",
            })
        else:
            queue_id = await add_to_operator_queue(
                user_id=user_id,
                draft_content=draft,
                confidence_score=score,
                flags=flags,
            )
            await notify_operators(queue_id, user_id, draft, score, flags)
            _telemetry_data.routing_decision = "operator_queued"
            _operator_events.append({
                "event": "ai.generation_completed",
                "data": {
                    "draft": draft,
                    "score": score,
                    "flags": flags,
                    "was_auto_approved": False,
                },
                "user_id": user_id,
                "dialog_id": user_id,
                "generation_id": generation_id,
                "scope": "user",
            })
            _operator_events.append({
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
                "scope": "user",
            })

        # ── Publish events batch ───────────────────────────────────────────
        try:
            await publish_events_batch(_operator_events)
        except Exception:
            try:
                for _ev in _operator_events:
                    await publish_event(
                        _ev["event"], _ev["data"],
                        user_id=_ev["user_id"], dialog_id=_ev["dialog_id"],
                        generation_id=_ev["generation_id"], scope=_ev["scope"],
                    )
            except Exception:
                logger.warning("Failed to publish operator events for user %s", user_id)

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
            from commerce.conversation_outcomes import (
                classify_outcome, classify_canonical_outcome, outcome_strength, attribute_purchase,
            )
            from commerce.strategy_learning import (
                update_strategy_evidence, update_strategy_evidence_extended, stage_for_objective,
            )
            from db.postgres import update_user_profile, get_user_profile

            _facts_prev = await get_user_profile(user_id) if get_user_profile else None
            _get_profile_strat = get_user_profile
            _prev_strategy = _facts_prev.get("strategy", "default") if _facts_prev else "default"
            _by_creator_prev = _facts_prev.get("by_creator", {}) if _facts_prev else {}

            try:
                _clf_legacy = classify_outcome(
                    previous_strategy=_prev_strategy,
                    fan_message=user_message,
                    desire_before=desire or "unknown",
                    desire_after=desire or "unknown",
                )
            except Exception:
                _clf_legacy = None
            try:
                _canon = classify_canonical_outcome(
                    previous_strategy=_prev_strategy,
                    fan_message=user_message,
                    desire_before=desire or "unknown",
                    desire_after=desire or "unknown",
                )
            except Exception:
                _canon = None
            try:
                _out_strength = outcome_strength(_canon or _clf_legacy, _pressure, _risk)
            except Exception:
                _out_strength = None

            try:
                _desire_after_val = desire or "unknown"
                _outcome_obj = {
                    "strategy": _prev_strategy,
                    "outcome": _canon.value if hasattr(_canon, "value") else str(_canon) if _canon else "unknown",
                    "strength": _out_strength if _out_strength else 0.0,
                    "desire_after": _desire_after_val,
                }
            except Exception:
                pass

            if _creator_id is not None:
                try:
                    await update_strategy_evidence(
                        _creator_id, user_id,
                        _prev_strategy or "default",
                        (_canon.value if hasattr(_canon, "value") else str(_canon)) if _canon else "neutral_engagement",
                    )
                    _telemetry_data.strategy_learning_updated = True
                except Exception:
                    pass
                try:
                    _topic_for_ev = _cur_for_beh if _cur_for_beh else None
                    _pf_for_ev = _prod_family
                    _lc_for_ev = _lc
                    _stage_for = stage_for_objective(_commercial_objective) if _commercial_objective else "unknown"
                    _strategy_name = _prev_strategy
                    await update_strategy_evidence_extended(
                        creator_id=_creator_id, user_id=user_id,
                        strategy=_strategy_name or "default",
                        outcome=(_canon.value if hasattr(_canon, "value") else str(_canon)) if _canon else "neutral",
                        topic=_topic_for_ev,
                        product_family=_pf_for_ev,
                        lifecycle=_lc_for_ev,
                        stage=_stage_for,
                        strength=_out_strength if _out_strength else 0.0,
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
                    if _cstate and _cstate.get("last_purchase"):
                        await attribute_purchase(
                            user_id=user_id, creator_id=_creator_id,
                            strategy=_strategy_name or "default",
                            objective=_commercial_objective,
                        )
                except Exception:
                    pass
                try:
                    _get_exp_mem = get_exposures_memory
                    _exps = await _get_exp_mem(user_id, _creator_id) if _get_exp_mem and _creator_id else []
                    if _exps:
                        _last = _exps[-1] if _exps else None
                        _exp_time = _last.get("timestamp") if _last and isinstance(_last, dict) else None
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
            _telemetry_data.conversation_outcome = _outcome.value if hasattr(_outcome, 'value') else str(_outcome)
        except Exception:
            logger.debug("Conversation outcome classification failed for user=%s", user_id)

        # ── Telemetry enrichment with funnel data ─────────────────────────
        try:
            from commerce.revenue_intelligence import enrich_telemetry_with_funnel
            enrich_telemetry_with_funnel(_telemetry_data, user_id=user_id, creator_id=_creator_id)
        except Exception:
            logger.debug("Telemetry enrichment failed for user=%s", user_id)

        # ── Post-processing (async) ────────────────────────────────────────
        asyncio.create_task(post_process(user_id))

    except Exception:
        logger.exception("Error processing message for user %s", user_id)
        try:
            await publish_event(
                "ai.generation_failed",
                {"error": "Generation failed"},
                user_id=user_id,
                dialog_id=user_id,
                generation_id=generation_id,
                scope="user",
            )
        except Exception:  # noqa: BLE001
            logger.warning("Failed to publish ai.generation_failed event for user %s", user_id)
        raise
    finally:
        await release_user_lock(user_id)


async def run_worker(worker_id: str) -> None:
    global _worker_preferred_index
    _worker_preferred_index = _parse_worker_preferred_index(worker_id)

    await init_pool()
    await ensure_consumer_group(consumer_name=worker_id)

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

    loop = asyncio.get_running_loop()
    heartbeat_stop = asyncio.Event()
    setup_signal_handlers([_worker_cleanup, lambda: heartbeat_stop.set()], loop=loop)

    # Sync daily quota from Redis if Gemini fallback enabled
    if _settings.gemini_fallback_enabled:
        try:
            from core.daily_quota import sync_from_redis, get_daily_count
            await sync_from_redis()
            redis_count = await get_daily_count()
            logger.info("Daily quota synced from Redis: %d", redis_count)
        except Exception:
            logger.debug("Daily quota sync failed", exc_info=True)

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

    while not is_shutting_down():
        try:
            stale_count, stale_ids = await requeue_stalled_messages(
                worker_id, idle_ms=_settings.redis_pending_idle_ms
            )
            if stale_count > 0:
                logger.info(
                    "Reclaimed %d stalled inbound messages: %s",
                    stale_count,
                    stale_ids,
                )

            messages = await read_inbound(worker_id, count=5, block_ms=2000)

            if not messages:
                pass
            else:
                for stream, stream_messages in messages:
                    for msg_id, data in stream_messages:
                        try:
                            msg_data = {
                                "user_id": int(data["user_id"]),
                                "user_message": data["content"],
                                "telegram_message_id": int(data["telegram_message_id"]),
                                "username": data.get("username", ""),
                                "first_name": data.get("first_name", ""),
                                "persona": data.get("persona", ""),
                            }

                            await process_message(**msg_data)

                            await ack_inbound(msg_id)

                        except Exception:
                            logger.exception("Failed to process message %s", msg_id)
                            await move_to_dlq(
                                msg_id,
                                "processing_error",
                                payload=dict(data),
                                worker_id=worker_id,
                            )

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
