import asyncio
import logging
import time

logger = logging.getLogger("circuit_breaker")

HALF_OPEN_DELAY = 30
FAILURE_THRESHOLD = 5
SUCCESS_THRESHOLD = 3


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = FAILURE_THRESHOLD,
        half_open_delay: float = HALF_OPEN_DELAY,
        success_threshold: int = SUCCESS_THRESHOLD,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.half_open_delay = half_open_delay
        self.success_threshold = success_threshold
        self._failure_count = 0
        self._success_count = 0
        self._state: str = "closed"
        self._next_try_time: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state

    async def can_execute(self) -> bool:
        if self._state == "closed":
            return True

        if self._state == "open":
            if time.time() >= self._next_try_time:
                async with self._lock:
                    if time.time() >= self._next_try_time:
                        self._state = "half_open"
                        self._success_count = 0
                        logger.info("Circuit breaker '%s' half-open", self.name)
                        return True
            return False

        return True

    async def record_success(self) -> None:
        if self._state == "half_open":
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                async with self._lock:
                    if self._state == "half_open":
                        self._state = "closed"
                        self._failure_count = 0
                        logger.info("Circuit breaker '%s' closed", self.name)

    async def record_failure(self) -> None:
        self._failure_count += 1

        if self._state == "half_open":
            self._state = "open"
            self._next_try_time = time.time() + self.half_open_delay
            logger.warning("Circuit breaker '%s' opened (half-open failure)", self.name)
            return

        if self._failure_count >= self.failure_threshold:
            self._state = "open"
            self._next_try_time = time.time() + self.half_open_delay
            logger.warning(
                "Circuit breaker '%s' opened (%d failures)", self.name, self._failure_count
            )


class CircuitBreakerError(Exception):
    pass


class CircuitBreakerOpen(CircuitBreakerError):
    pass


openai_breaker: CircuitBreaker | None = None
telegram_breaker: CircuitBreaker | None = None
postgres_breaker: CircuitBreaker | None = None


def get_circuit_breakers() -> dict[str, CircuitBreaker]:
    global openai_breaker, telegram_breaker, postgres_breaker

    if openai_breaker is None:
        openai_breaker = CircuitBreaker("openai")
    if telegram_breaker is None:
        telegram_breaker = CircuitBreaker("telegram")
    if postgres_breaker is None:
        postgres_breaker = CircuitBreaker("postgres")

    return {
        "openai": openai_breaker,
        "telegram": telegram_breaker,
        "postgres": postgres_breaker,
    }
