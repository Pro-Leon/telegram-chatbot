import asyncio
import logging
import signal

logger = logging.getLogger("shutdown")

_shutting_down = False
_loop: asyncio.AbstractEventLoop | None = None


def set_shutting_down(value: bool = True) -> None:
    global _shutting_down
    _shutting_down = value


def _set_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    global _loop
    _loop = loop


def setup_signal_handlers(
    cleanup_funcs: list, loop: asyncio.AbstractEventLoop | None = None
) -> None:
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

    _set_loop(loop)

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
                logger.exception("Error during cleanup: %s", getattr(func, "__name__", str(func)))
        logger.info("Shutdown complete")

    def _signal_handler():
        asyncio.create_task(_shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: _signal_handler())


def is_shutting_down() -> bool:
    return _shutting_down
