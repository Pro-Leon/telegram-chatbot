# AGENTS.md - Project Instructions

## Run Commands

### Local Development
```bash
# Install dependencies
pip install -e ".[dev]"

# Create .env file from .env.example
cp .env.example .env

# Start all services with Docker Compose (requires Docker)
docker-compose up

# Or run everything with a single script (requires PostgreSQL + Redis running locally):
python run_all.py

# Or run services individually:
# MTProto bot (main process, handles Telethon client + send stream)
python -m chatbotv2.main

# LLM Workers (run multiple instances)
python -m workers.llm_worker --worker-id worker_1
python -m workers.llm_worker --worker-id worker_2

# Send worker
python -m workers.send_worker --worker-id sender_1

# Operator dashboard web UI
python -m uvicorn chatbotv2.dashboard.app:app --host 0.0.0.0 --port 1010
```

### Database Setup (for local dev without Docker)
```bash
# Initialize the database schema (after starting PostgreSQL service)
psql -U postgres -d postgres -f db/schema.sql

# If schema was modified, ensure missing columns are added
psql -U postgres -d postgres -c "ALTER TABLE users ADD COLUMN IF NOT EXISTS persona_id INTEGER REFERENCES personas(id);"
psql -U postgres -d postgres -c "ALTER TABLE users ADD COLUMN IF NOT EXISTS do_not_auto_reply BOOLEAN DEFAULT FALSE;"
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

# Realtime Architecture Invariants

## Phase 1 Event Contract — IMMUTABLE

The Phase 1 realtime event contract is a stable public interface between
workers, Redis Pub/Sub, the FastAPI WebSocket layer, and browser clients.

DO NOT modify, rename, remove, reorder, reinterpret, or replace existing
Phase 1 events unless the current task explicitly demonstrates that the
contract must change.

Existing events:

- message.created
- message.sent
- message.send_failed
- ai.generation_started
- ai.generation_completed
- ai.generation_failed
- suggestion.created
- operator_queue.updated

### Event lifecycle invariant

For an AI generation:

ai.generation_started
    ↓
generation + scoring
    ↓
routing decision
    ↓
if auto-approved:
    enqueue_send() must succeed
    ↓
ai.generation_completed

If generation, scoring, routing, or enqueue_send fails:

ai.generation_failed

### Critical semantic rule

`ai.generation_completed` MUST NOT be emitted before
`enqueue_send()` successfully completes for an auto-approved generation.

A consumer must be able to trust:

`ai.generation_completed` = generation completed AND required
send-queue handoff succeeded.

### generation_id invariant

Every AI generation lifecycle event must carry the same generation_id:

- ai.generation_started
- ai.generation_completed
- ai.generation_failed
- suggestion.created

Never generate a new generation_id between lifecycle events.

### event_id invariant

Every published event must receive a unique event_id.

Frontend deduplication relies on event_id.

### Failure isolation invariant

Realtime event publication is best-effort.

Failure to publish a realtime event MUST NOT break:

- AI generation
- AI scoring
- queue routing
- Telegram sending
- Redis Stream processing
- database operations

Conversely, business-operation failures MUST NOT be hidden by realtime
event publication failures.

### Transport invariant

Workers publish events through the event bus.

Workers MUST NOT import or depend directly on:

- ws_manager
- event_subscriber
- FastAPI WebSocket implementation
- browser/frontend code

Workers communicate with the realtime layer only through the event bus.

### Frontend invariant

WebSocket is an acceleration/notification layer, not the source of truth.

Polling remains available as fallback.

When WebSocket is connected:
- realtime events may update the UI immediately
- polling may pause according to existing implementation

When WebSocket is disconnected:
- polling must resume

Never remove polling solely because WebSocket functionality exists.

### Scope invariant

User/dialog-scoped events must only be delivered to the appropriate
connected clients.

Global events may be delivered to global subscribers.

Do not broaden event scope without explicit architectural approval.

### Backward compatibility

Phase 2+ implementations MUST preserve the Phase 1 event schema and
semantics unless the task explicitly requires a versioned contract change.

If an event contract genuinely needs to change:

1. STOP implementation.
2. Explain why the existing contract is insufficient.
3. Identify all producers.
4. Identify all consumers.
5. Propose a backward-compatible migration.
6. Do not silently change the event.

# Change Boundary Rules

Before modifying an existing realtime component, determine whether the
change affects the Phase 1 event contract.

Classify changes as:

SAFE:
- bug fixes that preserve event names/schema/semantics
- performance improvements preserving behavior
- frontend rendering improvements
- additional event consumers
- additional tests
- additional logging/observability
- reconnect improvements
- polling improvements

REQUIRES REVIEW:
- changing event payload fields
- changing event timing
- changing event scope
- changing generation_id behavior
- changing event ordering
- changing Redis Pub/Sub behavior
- changing WebSocket authentication
- removing polling
- changing producer/consumer responsibilities

FORBIDDEN WITHOUT EXPLICIT APPROVAL:
- renaming existing events
- deleting existing events
- changing the meaning of an existing event
- making business logic depend on WebSocket delivery
- making workers depend on WebSocket implementation
- replacing Redis Streams with Pub/Sub for task processing
- treating WebSocket as durable state
- silently changing an existing event schema

If a task falls into REQUIRES REVIEW or FORBIDDEN:
DO NOT IMPLEMENT THE CHANGE AUTONOMOUSLY.
Stop and report the conflict.