from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


@lru_cache
def get_settings() -> "Settings":
    return Settings()


class Settings(BaseSettings):
    telegram_token: str | None = None
    operator_bot_token: str | None = None
    operator_telegram_ids: tuple[int, ...] = Field(default_factory=tuple)
    openai_api_key: str
    google_api_key: str | None = Field(default=None, repr=False)
    gemini_api_keys: str | None = Field(default=None, repr=False)
    embedding_api_key: str | None = None
    postgres_dsn: str
    redis_url: str
    webhook_url: str | None = None
    webhook_secret: str | None = None
    auto_approve_threshold: float = 0.80
    model_name: str = "gemini-flash-latest"
    cheap_model: str = "gemini-flash-latest"
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
    dashboard_admin_password: str = "admin123"
    enable_websocket: bool = True
    gemini_rpm_limit: int = 10
    gemini_rpm_safety_margin: float = 0.8
    redis_pending_idle_ms: int = 60000
    dlq_max_replay_attempts: int = 3
    dlq_retention_seconds: int = 604800
    structured_logging: bool = False
    worker_heartbeat_interval: int = 10
    worker_heartbeat_ttl: int = 30
    fangate_api_base_url: str = "https://fangate.info/api"
    fangate_api_timeout: float = 15.0
    fangate_enc_key: str | None = Field(default=None, repr=False)

    # Dropfans — sole active commerce provider
    dropfans_api_base_url: str = "https://www.dropfans.io"
    dropfans_api_timeout: float = 30.0
    dropfans_enc_key: str | None = Field(default=None, repr=False)
    dropfans_reconciliation_interval_seconds: int = 120

    # Vault delivery recovery
    vault_stale_reservation_minutes: int = 5

    # Scheduler worker configuration
    scheduler_poll_interval: int = 10
    scheduler_batch_size: int = 20
    scheduler_recovery_timeout: int = 300
    scheduler_max_recovery_attempts: int = 5

    # P3.1 -- LLM context enrichment limits
    max_context_messages: int = 30
    max_purchase_history: int = 5
    max_active_offers: int = 3

    # P3.2 -- Controlled LLM tool calling
    llm_tools_enabled: bool = True
    llm_max_tool_calls: int = 3
    llm_tool_timeout_seconds: float = 5.0

    # LLM Provider selection (Ollama Phase A)
    # "gemini" = active production provider (default)
    # "ollama" = experimental Ollama provider (requires SSH tunnel)
    llm_provider: str = "gemini"

    # Ollama provider configuration (Ollama Phase A+D1)
    # Remote endpoint: https://ollama.brestalogistics.co.ke (Caddy + Basic Auth)
    # Or SSH tunnel: ssh -N -L 11435:127.0.0.1:11434 <user>@<vps>
    ollama_base_url: str = "https://ollama.brestalogistics.co.ke"
    ollama_model: str = "qwen3:4b"
    ollama_timeout: float = 120.0
    ollama_username: str = "ollama"
    ollama_api_key: str = Field(default="", repr=False)

    # Autonomy kill switch. When False, autonomous commerce/PPV actions are
    # disabled. The system falls back to standard non-autonomous LLM behavior.
    # Independent from the Redis auto_reply toggle. Server-side only — cannot
    # be overridden by browser/API parameters.
    autonomy_enabled: bool = True

    # Phase C: Relationship derivation thresholds
    relationship_engaged_min_purchases: int = 1
    relationship_warm_min_purchases: int = 2
    relationship_warm_min_messages: int = 20
    relationship_buying_signal_min_purchases: int = 3
    relationship_repeat_min_purchases: int = 3
    relationship_vip_min_purchases: int = 5
    relationship_cooling_days: int = 30
    relationship_do_not_push_days: int = 90
    relationship_recent_purchase_hours: int = 48
    relationship_tip_eligible_min_purchases: int = 2
    relationship_tip_eligible_cooling_days: int = 7

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

    @field_validator("gemini_api_keys", mode="before")
    @classmethod
    def parse_gemini_keys(cls, v: str | list | None) -> str | None:
        if v is None:
            return None
        if isinstance(v, list):
            parts = [str(k).strip() for k in v if str(k).strip()]
            return ",".join(parts) if parts else None
        if isinstance(v, str):
            parts = [k.strip() for k in v.split(",") if k.strip()]
            return ",".join(parts) if parts else None
        return None

    def get_gemini_api_keys(self) -> list[str]:
        """Return resolved list of Gemini API keys.

        If GEMINI_API_KEYS is set, returns those (comma-separated, stripped).
        Otherwise falls back to [GOOGLE_API_KEY] if set.
        Returns empty list if neither is configured.
        """
        if self.gemini_api_keys:
            return [k for k in self.gemini_api_keys.split(",") if k]
        if self.google_api_key:
            return [self.google_api_key]
        return []


settings: Settings = get_settings()
