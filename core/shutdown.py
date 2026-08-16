import asyncio
import logging
import signal

logger = logging.getLogger("shutdown")

_shutting_down = False


def setup_signal_handlers(cleanup_funcs: list) -> None:
    async def _shutdown() -> None:
        global _shutting_down
        if _shutting_down:
            return
        _shutting_down = True

        logger.info("Shutting down...")
        for func in cleanup_funcs:
            try:
                if asyncio.iscoroutinefunction(func):
                    await func()
                else:
                    func()
            except Exception:
                logger.exception("Error during cleanup: %s", func.__name__)
        logger.info("Shutdown complete")

    def _signal_handler(*_args) -> None:
        asyncio.create_task(_shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _signal_handler)


def is_shutting_down() -> bool:
    return _shutting_down
