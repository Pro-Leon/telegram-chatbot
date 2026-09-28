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
    embedding_api_key: str | None = None
    postgres_dsn: str
    redis_url: str
    webhook_url: str | None = None
    webhook_secret: str | None = None
    auto_approve_threshold: float = 0.80
    embedding_model: str = "text-embedding-3-small"
    use_groq: bool = True
    max_tokens: int = 200
    temperature: float = 0.85
    presence_penalty: float = 0.5
    frequency_penalty: float = 0.3
    summarize_every_n: int = 20
    debounce_window_seconds: int = 3
    rate_limit_per_minute: int = 20
    # P1.6 R-05: increased from 60 to 300 to cover worst-case LLM latency (llama.cpp 120s + context engine + scoring + commerce)
    # 300s comfortably exceeds realistic max ~210s while remaining bounded (not arbitrarily huge)
    user_lock_ttl: int = 300
    dashboard_admin_password: str = "admin123"
    enable_websocket: bool = True
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

    # LLM Provider selection — llama.cpp is the sole supported provider.
    # Retained for deployment/testing clarity; only 'llamacpp' is accepted.
    llm_provider: str = "llamacpp"

    # llama.cpp provider configuration (local OpenAI-compatible server)
    # Local endpoint: http://localhost:8081 (GET /v1/models, POST /v1/chat/completions)
    llama_base_url: str = "http://localhost:8081"
    llama_model: str = "Pola010101/Llama-3.2-1B-Instruct-Uncensored-Q4_K_M-GGUF:Q4_K_M"
    llama_timeout: float = 120.0
    llama_api_key: str = Field(default="", repr=False)

    # Autonomy kill switch. When False, autonomous commerce/PPV actions are
    # disabled. The system falls back to standard non-autonomous LLM behavior.
    # Independent from the Redis auto_reply toggle. Server-side only — cannot
    # be overridden by browser/API parameters.
    autonomy_enabled: bool = True

    # Sunny architecture cutover (Phase 3 — V1 freeze).
    # V1 legacy conversational processing defaults to DISABLED. Only explicit
    # server-side configuration (SUNNY_V1_ENABLED=true) can re-enable it;
    # request bodies, query params, and stream payloads must never drive it.
    sunny_v1_enabled: bool = False
    # V2 has no runtime yet (specification only). Exists so future activation
    # does not require resurrecting V1.
    sunny_v2_enabled: bool = False

    # Relationship V2 remediation flags (Stage A1).
    # Purpose: staged cutover without a single global kill switch.
    # Defaults: all False (V2 observe-only, never authoritative).
    # Activation: set server-side env only; never from request/stream payloads.
    # Rollback: set READ/WRITE false, legacy flags true; V2 data preserved.
    relationship_v2_enabled: bool = False
    relationship_v2_read_enabled: bool = False
    relationship_v2_write_enabled: bool = False
    relationship_v2_shadow_mode: bool = False

    # AI-Native Runtime Mode (Phase 2)
    # Controls which runtime processes inbound messages.
    # "legacy" = existing production behavior (default, safe)
    # "agent" = agent runtime as primary conversational path
    # "shadow" = legacy authoritative, agent runs async for comparison
    ai_runtime_mode: str = "legacy"

    # Agent runtime configuration
    agent_max_tool_calls: int = 5
    agent_max_response_tokens: int = 120
    agent_max_runtime_seconds: float = 30.0

    # AI-Native Canary Configuration (Phase 2)
    # Controls gradual rollout of agent runtime.
    # When canary is enabled, a percentage of conversations use agent runtime.
    # Default: disabled (legacy only)
    ai_agent_canary_enabled: bool = False
    ai_agent_canary_sample_rate: float = 0.0  # 0.01 = 1%, 0.1 = 10%, 1.0 = 100%
    ai_agent_canary_creator_ids: str = ""  # Comma-separated creator IDs, empty = all creators

    # Phase 73/77B: Context Engine observational mode
    # When true, Context Engine runs and its rendered context is passed to OneCall.
    # Fail-open: any Context Engine failure does not affect production processing.
    context_engine_observational: bool = False

    # Phase 1: Context Engine canonical production runtime (100%)
    # Retrieval uses lexical RapidFuzz + MiniLM semantic hybrid without hnswlib.
    # Canonical 1.0 =100% deterministic; canary 0.10 preserved via sample_rate for rollback/testing.
    context_engine_enabled: bool = True
    context_engine_sample_rate: float = 1.0  # 1.0 =100% canonical, 0.1 =10% canary, 0.0 disabled

    # Phase 79/77B: Runtime path selector
    # "new" = Context Engine + one Qwen generation (default, active)
    # "legacy" = existing 3-LLM pipeline (fallback/rollback)
    # When one-call fails, routes to operator queue (no legacy cascade).
    llm_path: str = "new"  # "new" | "legacy"

    # Phase 77: Context Engine A/B Canary
    # Modes: "disabled" (default), "observe"
    # When "observe": Context Engine + Qwen runs in parallel, output compared to authoritative path.
    # The canary path NEVER sends, NEVER creates offers, NEVER mutates state.
    # Fail-open: any canary failure does not affect production processing.
    context_engine_canary_mode: str = "disabled"  # "disabled" | "observe"
    context_engine_canary_sample_rate: float = 0.0  # 0.01 = 1%, 1.0 = 100%
    context_engine_canary_timeout: float = 30.0  # seconds for Qwen generation
    context_engine_canary_max_tokens: int = 500
    context_engine_canary_model: str = ""  # empty = use llama_model from settings

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

settings: Settings = get_settings()
