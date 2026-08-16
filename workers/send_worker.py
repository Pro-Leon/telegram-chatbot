import asyncio
import logging
import sys

from core.config import get_settings
from db.postgres import (
    get_pending_queue_items,
    resolve_queue_item,
    save_outbound_message,
)
from ingestion.bot import send_message

logger = logging.getLogger("send_worker")
_settings = get_settings()


async def process_approved_message(
    user_id: int,
    content: str,
) -> dict | None:
    try:
        msg = await send_message(user_id, content)
        return {"message_id": msg.message_id, "ok": True}
    except Exception:
        logger.exception("Failed to send message to user %s", user_id)
        return None


async def flush_queue(max_items: int = 50) -> int:
    items = await get_pending_queue_items(limit=max_items)
    sent = 0

    for item in items:
        if item.get("status") != "pending":
            continue

        user_id = item["user_id"]
        content = item["draft_content"]
        if not content:
            await resolve_queue_item(item["id"], "rejected")
            continue

        result = await process_approved_message(user_id, content)
        if result and result["ok"]:
            await save_outbound_message(
                user_id=user_id,
                content=content,
                draft_content=content,
                was_edited=False,
                was_auto_approved=item.get("confidence_score", 0)
                >= _settings.auto_approve_threshold,
                confidence_score=item.get("confidence_score"),
                operator_id=item.get("assigned_to"),
                telegram_message_id=result["message_id"],
            )
            await resolve_queue_item(
                item["id"],
                "approved",
                operator_id=item.get("assigned_to"),
            )
            sent += 1
        else:
            logger.warning("Failed to send queue item %s", item["id"])

    return sent


async def run_send_worker(worker_id: str) -> None:
    logger.info("Send worker %s started", worker_id)

    while True:
        try:
            await flush_queue(max_items=50)
        except Exception:
            logger.exception("Send worker loop error")

        await asyncio.sleep(5)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Send Worker")
    parser.add_argument("--worker-id", required=True)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(run_send_worker(args.worker_id))


if __name__ == "__main__":
    main()
