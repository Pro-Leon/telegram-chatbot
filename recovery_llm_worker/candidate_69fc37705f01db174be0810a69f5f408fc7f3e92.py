# MTProto Configuration (chatbotv2 - user account)
API_ID=37179768
API_HASH=d978f8c85cdac619988c8faae5e9a357
PHONE_NUMBER=+254769983540
TELETHON_SESSION=chatbotv2

# LLM Configuration (Groq API key from https://console.groq.com/keys)
OPENAI_API_KEY=gsk_your_groq_key_here
EMBEDDING_API_KEY=
USE_GROQ=true

# Gemini API Keys (comma-separated, 1-4 keys from separate Google Cloud projects)
# Each key provides independent quota (separate projects required).
# If GEMINI_API_KEYS is empty, falls back to GOOGLE_API_KEY.
# GEMINI_API_KEYS=key1,key2,key3,key4
GOOGLE_API_KEY=your_gemini_api_key_here

# Gemini Rate Limiting (per-process, local)
GEMINI_RPM_LIMIT=10
GEMINI_RPM_SAFETY_MARGIN=0.8

# Database Configuration
POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/postgres
REDIS_URL=redis://127.0.0.1:6379

# Dashboard
DASHBOARD_ADMIN_PASSWORD=admin123

# Tuning Parameters
AUTO_APPROVE_THRESHOLD=0.80
MODEL_NAME=meta-llama/llama-4-scout-17b-16e-instruct
CHEAP_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
EMBEDDING_MODEL=text-embedding-3-small
MAX_TOKENS=200
TEMPERATURE=0.85
PRESENCE_PENALTY=0.5
FREQUENCY_PENALTY=0.3
SUMMARIZE_EVERY_N=20
DEBOUNCE_WINDOW_SECONDS=3
RATE_LIMIT_PER_MINUTE=20
USER_LOCK_TTL=60

# Redis Stream Recovery (milliseconds)
# Idle threshold for reclaiming pending messages after worker crash.
# Default 60000ms (60s) — long enough for slow Gemini calls, short enough for crash recovery.
REDIS_PENDING_IDLE_MS=60000

# DLQ Recovery
# Maximum times a failed message can be replayed from the DLQ.
DLQ_MAX_REPLAY_ATTEMPTS=3
# How long to retain DLQ records (seconds). Default 7 days.
DLQ_RETENTION_SECONDS=604800

# Structured Logging (false = human-readable, true = JSON)
STRUCTURED_LOGGING=false

# Worker Heartbeat
# How often workers refresh their Redis heartbeat key (seconds).
WORKER_HEARTBEAT_INTERVAL=10
# TTL on heartbeat keys — auto-expires if worker dies (seconds).
WORKER_HEARTBEAT_TTL=30

# Fangate Commerce Integration
# Production API base: https://fangate.info/api  (Development: https://fangate.co/api)
FANGATE_API_BASE_URL=https://fangate.info/api
# HTTP timeout for Fangate API calls (seconds).
FANGATE_API_TIMEOUT=15
# Master key used to encrypt Fangate API keys and webhook secrets at rest (Fernet).
# Use a strong random value, e.g. generated with:
#   python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(48)).decode())"
# Never commit or log it. Kept in the environment only — rotation requires
# decrypting and re-encrypting every stored credential.
FANGATE_ENC_KEY=cf2c0ae7c3978d3c95ae5c9f50141c6a2ea2a2b9060e74655a76de13f65c8dde

# Vault Delivery Recovery
# Minutes before a pending reservation is considered stale and recoverable.
# Must exceed Redis pending idle threshold (REDIS_PENDING_IDLE_MS) to avoid
# reclaiming active reservations. Default 5 minutes.
VAULT_STALE_RESERVATION_MINUTES=5

# LLM Provider Selection (Ollama Phase A)
# "gemini" = active production provider (default)
# "ollama" = Ollama provider (remote endpoint with Basic Auth)
# LLM_PROVIDER=gemini

# Ollama Provider Configuration (Ollama Phase D1 + Qwen3)
# Remote endpoint: https://ollama.brestalogistics.co.ke (Caddy + Basic Auth)
# Or SSH tunnel: ssh -N -L 11435:127.0.0.1:11434 <user>@<vps>
# OLLAMA_BASE_URL=https://ollama.brestalogistics.co.ke
# OLLAMA_MODEL=qwen3:4b
# OLLAMA_TIMEOUT=60.0
# OLLAMA_USERNAME=ollama
# OLLAMA_API_KEY=your_ollama_api_key_here

# Autonomy Kill Switch
# When false, autonomous commerce/PPV actions are disabled. The system falls
# back to standard non-autonomous LLM behavior. Independent from the Redis
# auto_reply toggle. Server-side only — cannot be overridden by browser/API.
# AUTONOMY_ENABLED=true
