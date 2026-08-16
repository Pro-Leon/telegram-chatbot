from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


@lru_cache
def get_settings() -> "Settings":
    return Settings()


class Settings(BaseSettings):
    telegram_token: str
    operator_bot_token: str
    operator_telegram_ids: tuple[int, ...] = Field(default_factory=tuple)
    openai_api_key: str
    embedding_api_key: str | None = None
    postgres_dsn: str
    redis_url: str
    webhook_url: str
    webhook_secret: str
    auto_approve_threshold: float = 0.80
    model_name: str = "llama-3.1-8b-instant"
    cheap_model: str = "llama-3.1-8b-instant"
    embedding_model: str = "text-embedding-3-small"
    use_groq: bool = True
    max_tokens: int = 200
    temperature: float = 0.85
    presence_penalty: float = 0.5
    frequency_penalty: float = 0.3
    summarize_every_n: int = 20
    debounce_window_seconds: int = 3
    rate_limit_per_minute: int = 20
    user_lock_ttl: int = 60

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @field_validator("operator_telegram_ids", mode="before")
    @classmethod
    def parse_int_list(cls, v: str | list | tuple | int | None) -> tuple[int, ...]:
        if v is None or v == "":
            return ()
        if isinstance(v, int):
            return (v,)
        if isinstance(v, str):
            return tuple(int(x.strip()) for x in v.split(",") if x.strip())
        if isinstance(v, (list, tuple)):
            return tuple(int(x) for x in v)
        return v


settings: Settings = get_settings()
