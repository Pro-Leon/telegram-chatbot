import argparse
import asyncio
import logging
import sys

from groq import AsyncGroq

from core.config import get_settings
from core.scoring import score_draft
from db.postgres import (
    add_to_operator_queue,
    get_recent_messages,
    save_inbound_message,
    upsert_user,
)
from db.redis import (
    ack_inbound,
    acquire_user_lock,
    ensure_consumer_group,
    move_to_dlq,
    read_inbound,
    release_user_lock,
)
from ingestion.bot import send_message
from memory.context import build_context
from memory.profile import extract_and_update_profile
from memory.summarizer import maybe_summarize

logger = logging.getLogger("llm_worker")
_settings = get_settings()
_client = AsyncGroq(api_key=_settings.openai_api_key)


async def generate_draft(
    context_messages: list[dict],
    user_message: str,
    model: str = _settings.model_name,
) -> str:
    messages = [*context_messages, {"role": "user", "content": user_message}]

    response = await _client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=_settings.temperature,
        max_tokens=_settings.max_tokens,
        presence_penalty=_settings.presence_penalty,
        frequency_penalty=_settings.frequency_penalty,
    )

    return response.choices[0].message.content


async def notify_operators(
    queue_id: int,
    user_id: int,
    draft: str,
    score: float,
    flags: list[str],
) -> None:
    from operator_dashboard.dashboard_bot import notify_operator_of_draft

    await notify_operator_of_draft(queue_id, user_id, draft, score, flags)


async def post_process(user_id: int) -> None:
    recent = await get_recent_messages(user_id, limit=20)
    message_count = len(recent)

    await extract_and_update_profile(user_id, recent)
    await maybe_summarize(user_id, message_count)


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

    try:
        await upsert_user(user_id, username, first_name)
        await save_inbound_message(user_id, user_message, telegram_message_id)

        context = await build_context(user_id, user_message, persona)

        draft = await generate_draft(context, user_message)

        score, flags = await score_draft(draft, user_message, context)

        if score >= _settings.auto_approve_threshold and not flags:
            sent_msg = await send_message(user_id, draft)
            from db.postgres import save_outbound_message

            await save_outbound_message(
                user_id=user_id,
                content=draft,
                draft_content=draft,
                was_edited=False,
                was_auto_approved=True,
                confidence_score=score,
                operator_id=None,
                telegram_message_id=sent_msg.message_id,
            )
        else:
            queue_id = await add_to_operator_queue(
                user_id=user_id,
                draft_content=draft,
                confidence_score=score,
                flags=flags,
            )
            await notify_operators(queue_id, user_id, draft, score, flags)

        asyncio.create_task(post_process(user_id))

    except Exception:
        logger.exception("Error processing message for user %s", user_id)
        raise
    finally:
        await release_user_lock(user_id)


async def run_worker(worker_id: str) -> None:
    await ensure_consumer_group()

    logger.info("Worker %s started", worker_id)

    while True:
        try:
            messages = await read_inbound(worker_id, count=5, block_ms=2000)

            if not messages:
                await asyncio.sleep(0.5)
                continue

            for stream, stream_messages in messages:
                for msg_id, data in stream_messages:
                    try:
                        msg_data = {
                            "user_id": int(data["user_id"]),
                            "content": data["content"],
                            "telegram_message_id": int(data["telegram_message_id"]),
                            "username": data.get("username", ""),
                            "first_name": data.get("first_name", ""),
                            "persona": data.get("persona", ""),
                        }

                        await process_message(**msg_data)

                        await ack_inbound(msg_id)

                    except Exception:
                        logger.exception("Failed to process message %s", msg_id)
                        await move_to_dlq(msg_id, "processing_error")

        except Exception:
            logger.exception("Worker loop error")
            await asyncio.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM Worker")
    parser.add_argument("--worker-id", required=True, help="Unique worker ID")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )

    asyncio.run(run_worker(args.worker_id))


if __name__ == "__main__":
    main()
