import time

from core.config import get_settings

_settings = get_settings()

_rate_limit_store: dict[str, tuple[float, int]] = {}


async def check_global_rate_limit(key: str, max_per_minute: int = 100) -> bool:
    now = time.time()
    stored_time, count = _rate_limit_store.get(key, (now, 0))

    if now - stored_time > 60:
        _rate_limit_store[key] = (now, 1)
        return True

    if count >= max_per_minute:
        return False

    _rate_limit_store[key] = (stored_time, count + 1)
    return True
