import asyncio
import logging
import sys

import uvicorn
from aiogram import Dispatcher
from fastapi import FastAPI

from operator_dashboard.api_routes import router as api_router
from operator_dashboard.dashboard_bot import close_dashboard_bot, get_router, init_dashboard_bot
from operator_dashboard.web_routes import router as web_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger("operator_dashboard")

dp = Dispatcher()
dp.include_router(get_router())

app = FastAPI(title="Operator Dashboard")
app.include_router(web_router)
app.include_router(api_router)


async def run_bot() -> None:
    bot = await init_dashboard_bot()
    logger.info("Operator dashboard (Telegram) started")

    try:
        await dp.start_polling(bot)
    finally:
        await close_dashboard_bot()
        logger.info("Operator dashboard stopped")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Operator Dashboard")
    parser.add_argument("--mode", choices=["bot", "web"], default="bot")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if args.mode == "web":
        uvicorn.run("operator_dashboard.dashboard_worker:app", host="0.0.0.0", port=args.port)
    else:
        asyncio.run(run_bot())
