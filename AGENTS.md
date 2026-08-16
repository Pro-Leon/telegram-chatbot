# AGENTS.md - Project Instructions

## Run Commands

### Local Development
```bash
# Install dependencies
pip install -e ".[dev]"

# Create .env file from .env.example
cp .env.example .env

# Start all services with Docker Compose
docker-compose up

# Or run services individually:
# Ingestion server
uvicorn ingestion.main:app --reload --port 8000

# LLM Workers (run multiple instances)
python -m workers.llm_worker --worker-id worker_1
python -m workers.llm_worker --worker-id worker_2

# Send worker
python -m workers.send_worker --worker-id sender_1

# Operator dashboard (Telegram polling bot)
python -m operator_dashboard.dashboard_worker
```

### Testing
```bash
# Run tests
pytest

# Type checking (if added)
# Add mypy to dev dependencies
```

### Linting
```bash
# Format code
ruff format .

# Lint code
ruff check .
```

## Architecture Notes

- **Debounce Flow**: When rapid messages arrive, the first message owns a debounce window. After the window expires, all messages in the buffer are collected and only the latest is enqueued for processing. Earlier messages are still saved to the DB for audit.

- **Auto-Approval**: Confidence score ≥ 0.80 with no hard flags = auto-sent. Otherwise routed to operator queue.

- **Operator Flow**: Operators receive inline keyboard buttons (Approve/Edit/Reject/History) on their Telegram bot. Edit requires replying to the notification message with new content.

- **Consumer Groups**: All LLM workers share the `llm_workers` consumer group on Redis Streams. Stalled messages (idle > 30s) are auto-claimed via `XAUTOCLAIM`.

## Database Schema
- PostgreSQL with pgvector for embeddings
- Schema is initialized from `db/schema.sql` on container startup
- Redis Streams for queueing, Redis for locks and rate limiting
