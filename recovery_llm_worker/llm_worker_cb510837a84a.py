import argparse
import asyncio
import hashlib
import logging
import uuid

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
from memory.context import build_context
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

    try:
        response_text = await provider.generate_with_history(
            system_instruction=merged_system,
            messages=messages,
            model=model,
            max_output_tokens=_settings.max_tokens,
            temperature=_settings.temperature,
            top_p=0.95,
        )
        return response_text
    except Exception:
        logger.exception("generate_draft failed")
        return ""


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
) -> None:
    locked = await acquire_user_lock(user_id, ttl=_settings.user_lock_ttl)
    if not locked:
        logger.info("User %s already locked, skipping", user_id)
        return

    from core.event_bus import publish_event

    generation_id = str(uuid.uuid4())

    try:
        await upsert_user(user_id, username, first_name)

        if await is_user_auto_reply_excluded(user_id):
            logger.info("User %s is excluded from auto-reply, skipping", user_id)
            return

        # P3.1 -- Resolve creator for context enrichment (failure-safe).
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
                # READY means an active integration exists -- sales is enabled
                _creator_sales_enabled = True
        except Exception:
            logger.debug("Creator resolution failed for context enrichment (user=%s)", user_id)

        context = await build_context(user_id, user_message, persona, creator_id=_creator_id)

        await publish_event(
            "ai.generation_started",
            {
                "message_preview": user_message[:100],
            },
            user_id=user_id,
            dialog_id=user_id,
            generation_id=generation_id,
            scope="user",
        )

        selection = await _try_commerce_draft(user_id, context, persona)
        if (
            selection is not None
            and selection.status is CommerceSelectionStatus.USE_COMMERCE_RESPONSE
        ):
            draft = selection.commerce_response_text
            logger.info("Commerce draft selected for user %s", user_id)
        elif _settings.llm_tools_enabled and _creator_id is not None:
            # P3.2 -- Tool-aware generation with bounded tool loop
            from db.postgres import get_user as _get_user_for_auth

            _auth_user = await _get_user_for_auth(user_id) or {}
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
            draft = await generate_draft(context, user_message)

        score, flags = await score_draft(draft, user_message, context)

        auto_reply_on = await is_auto_reply_enabled()

        dedup_id = hashlib.md5(
            f"{user_id}:{user_message}:{telegram_message_id}".encode()
        ).hexdigest()

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
            await publish_event(
                "ai.generation_completed",
                {
                    "draft": draft,
                    "score": score,
                    "flags": flags,
                    "was_auto_approved": False,
                },
                user_id=user_id,
                dialog_id=user_id,
                generation_id=generation_id,
                scope="user",
            )
            await publish_event(
                "suggestion.created",
                {
                    "queue_id": queue_id,
                    "draft": draft,
                    "score": score,
                    "flags": flags,
                },
                user_id=user_id,
                dialog_id=user_id,
                generation_id=generation_id,
                scope="user",
            )
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
            await publish_event(
                "ai.generation_completed",
                {
                    "draft": draft,
                    "score": score,
                    "flags": flags,
                    "was_auto_approved": True,
                },
                user_id=user_id,
                dialog_id=user_id,
                generation_id=generation_id,
                scope="user",
            )
        else:
            queue_id = await add_to_operator_queue(
                user_id=user_id,
                draft_content=draft,
                confidence_score=score,
                flags=flags,
            )
            await notify_operators(queue_id, user_id, draft, score, flags)
            await publish_event(
                "ai.generation_completed",
                {
                    "draft": draft,
                    "score": score,
                    "flags": flags,
                    "was_auto_approved": False,
                },
                user_id=user_id,
                dialog_id=user_id,
                generation_id=generation_id,
                scope="user",
            )
            await publish_event(
                "suggestion.created",
                {
                    "queue_id": queue_id,
                    "draft": draft,
                    "score": score,
                    "flags": flags,
                },
                user_id=user_id,
                dialog_id=user_id,
                generation_id=generation_id,
                scope="user",
            )

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
