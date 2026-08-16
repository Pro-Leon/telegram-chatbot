import asyncio
import logging

from aiogram import Dispatcher
from fastapi import FastAPI

from core.config import get_settings
from db.postgres import close_pool, init_pool
from db.redis import close_redis, ensure_consumer_group
from ingestion.bot import close_bot, get_bot
from ingestion.handlers import router as tg_router
from ingestion.health import router as health_router

logger = logging.getLogger("ingestion")
_settings = get_settings()

dp = Dispatcher()
dp.include_router(tg_router)

app = FastAPI(title="Telegram Chatbot Ingestion")
app.include_router(health_router)


@app.on_event("startup")
async def startup() -> None:
    await init_pool()
    await ensure_consumer_group()
    logger.info("Ingestion started")


@app.on_event("shutdown")
async def shutdown() -> None:
    await close_bot()
    await close_pool()
    await close_redis()
    logger.info("Ingestion stopped")


async def run_polling() -> None:
    await init_pool()
    await ensure_consumer_group()
    bot = await get_bot()
    try:
        logger.info("Starting polling mode...")
        await dp.start_polling(bot, allowed_updates=["message"])
    finally:
        await close_bot()
        await close_pool()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(run_polling())
