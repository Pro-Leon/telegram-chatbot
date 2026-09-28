# Phase 1: Real-Time WebSocket Layer

> **Status**: PLANNING
> **Created**: 2026-08-17
> **Scope**: Additive WebSocket layer for real-time browser updates. No modifications to existing application logic, DB schema, or Redis structures.

---

## Table of Contents

1. [Design Decisions Summary](#1-design-decisions-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Redis Pub/Sub Bridge](#3-redis-pubsub-bridge)
4. [WebSocket Connection Manager](#4-websocket-connection-manager)
5. [Event Routing & Filtering](#5-event-routing--filtering)
6. [Event Schema](#6-event-schema)
7. [Auth for WebSocket Upgrade](#7-auth-for-websocket-upgrade)
8. [Reconnection Strategy](#8-reconnection-strategy)
9. [Polling Fallback](#9-polling-fallback)
10. [AI Reply Flow (Real-Time)](#10-ai-reply-flow-real-time)
11. [Ordering & Deduplication](#11-ordering--deduplication)
12. [Failure Modes](#12-failure-modes)
13. [File Modification Specs](#13-file-modification-specs)
14. [Frontend JavaScript Specs](#14-frontend-javascript-specs)
15. [Testing Strategy](#15-testing-strategy)
16. [Rollback Plan](#16-rollback-plan)
17. [Performance Considerations](#17-performance-considerations)
18. [Security Considerations](#18-security-considerations)
19. [Monitoring & Observability](#19-monitoring--observability)
20. [Dependency Check](#20-dependency-check)
21. [Implementation Order](#21-implementation-order)
22. [Open Questions](#22-open-questions)
23. [Out of Scope](#23-out-of-scope)
24. [Success Criteria](#24-success-criteria)

---

## 1. Design Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Transport | WebSocket (primary) + SSE fallback | Full-duplex; SSE as static fallback for restricted envs |
| Pub/Sub mechanism | Redis Pub/Sub (new, parallel to Streams) | Workers are stateless publishers; no consumer group needed for broadcast |
| Auth | Cookie-based session (reuse existing `sessions` table) | Same cookie works for HTTP and WS upgrade; no token duplication |
| Connection scope | Per-user broadcast (all events) | Localhost tool; operator sees everything; filtering is UI-side |
| Reconnection | Exponential backoff 1s→30s + jitter | Standard; prevents thundering herd |
| Message ordering | Event timestamp (ms) + sequence counter | Redis Pub/Sub is fire-and-forget; ordering is best-effort |
| Deduplication | Client-side `last_event_id` tracking | Simple; events are idempotent by nature |
| Rollback | Feature flag `ENABLE_WEBSOCKET` in `.env` | Kill switch; polling always works |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Worker Processes                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │ handlers │  │llm_worker│  │send_worker│                  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                  │
│       │              │              │                        │
│       ▼              ▼              ▼                        │
│  ┌──────────────────────────────────────────┐               │
│  │        event_bus.py (new)                │               │
│  │  publish(event_type, payload, user_id)   │               │
│  └────────────────────┬─────────────────────┘               │
│                       │                                     │
│              Redis PUBLISH channel                           │
│              "chatbot:events"                                │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌───────────────────────┴─────────────────────────────────────┐
│                    FastAPI Process                            │
│  ┌──────────────────────────────────────────┐               │
│  │       event_subscriber.py (new)          │               │
│  │  - Redis Pub/Sub listener task           │               │
│  │  - Broadcasts to ConnectionManager       │               │
│  └────────────────────┬─────────────────────┘               │
│                       │                                     │
│  ┌────────────────────▼─────────────────────┐               │
│  │       ws_manager.py (new)                │               │
│  │  - ConnectionManager class               │               │
│  │  - WebSocket endpoint (/ws)              │               │
│  │  - Connection lifecycle                   │               │
│  └────────────────────┬─────────────────────┘               │
│                       │                                     │
│              Browser WebSocket                               │
│              ws://localhost:8080/ws                           │
└─────────────────────────────────────────────────────────────┘
```

**Data flow**: Worker emits event → `event_bus.publish()` → Redis PUBLISH → FastAPI `event_subscriber` receives → `ConnectionManager.broadcast()` → Browser WebSocket → JavaScript handler.

**Key invariant**: Workers never import or reference `ws_manager.py`, `event_subscriber.py`, or any WebSocket code. The `event_bus.py` module is a thin wrapper around Redis `PUBLISH` that workers already have access to via `db.redis`.

---

## 3. Redis Pub/Sub Bridge

### 3.1 Why Redis Pub/Sub (Not Streams)

- **Broadcast**: Pub/Sub delivers to all subscribers instantly. Streams require consumer groups with ack.
- **Workers are stateless**: They fire events and forget. No need for durable event log.
- **Low latency**: Pub/Sub is sub-millisecond on localhost.
- **Separation of concerns**: Streams are for task queues (inbound_messages, send_messages). Pub/Sub is for notifications.

### 3.2 Channel Design

Single channel: `chatbot:events`

All event types published to one channel. The `event_type` field in the payload allows filtering at the subscriber level. This avoids managing multiple channels and simplifies the connection manager.

### 3.3 Implementation in `db/redis.py`

Add two functions to the existing Redis module:

```python
async def publish_event(event_type: str, payload: dict, user_id: int | None = None) -> int:
    """Publish a real-time event to the chatbot:events channel.

    Returns the number of subscribers that received the message.
    """
    import time

    event = {
        "event_type": event_type,
        "user_id": user_id,
        "timestamp_ms": int(time.time() * 1000),
        "data": payload,
    }
    return await _redis_client.publish("chatbot:events", json.dumps(event))


async def subscribe_events():
    """Return a Pub/Sub object for listening to chatbot:events.

    Caller is responsible for managing the subscription lifecycle.
    """
    pubsub = _redis_client.pubsub()
    await pubsub.subscribe("chatbot:events")
    return pubsub
```

**Why in `db/redis.py`**: Workers already import from this module. Adding `publish_event` here means workers can call it without any new imports or dependency changes. The `_redis_client` singleton is already initialized in worker processes.

### 3.4 Connection Pool Notes

- `_redis_client` is created with `aioredis.from_url()` in `db/redis.py` (line ~30).
- Pub/Sub works on the same connection pool. No separate connection needed for publishing.
- Subscribing requires a dedicated connection (Pub/Sub blocks). The `event_subscriber` in FastAPI will create its own connection for subscribing.

---

## 4. WebSocket Connection Manager

### 4.1 Class: `ConnectionManager`

**File**: `chatbotv2/dashboard/ws_manager.py` (new)

```python
class ConnectionManager:
    """Manages WebSocket connections and broadcasts events."""
    
    def __init__(self):
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self._connections.append(websocket)
    
    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        async with self._lock:
            if websocket in self._connections:
                self._connections.remove(websocket)
    
    async def broadcast(self, event: dict) -> None:
        """Send an event to all connected WebSockets.
        
        Failed connections are silently removed.
        """
        message = json.dumps(event)
        stale = []
        async with self._lock:
            for ws in self._connections:
                try:
                    await ws.send_text(message)
                except Exception:
                    stale.append(ws)
            for ws in stale:
                self._connections.remove(ws)
    
    @property
    def connection_count(self) -> int:
        return len(self._connections)
```

### 4.2 Singleton Pattern

```python
_manager: ConnectionManager | None = None


def get_manager() -> ConnectionManager:
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager
```

**Why a singleton**: FastAPI runs in a single process. The `event_subscriber` background task and the WebSocket endpoint share the same `ConnectionManager` instance via `get_manager()`.

### 4.3 WebSocket Endpoint

**File**: `chatbotv2/dashboard/app.py` — add at the end of route definitions (before middleware):

```python
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time events.
    
    Auth: Checks session cookie from the HTTP upgrade request.
    Falls back to query param ?token= if cookies are blocked.
    """
    from chatbotv2.dashboard.ws_manager import get_manager
    from chatbotv2.dashboard.auth import verify_session
    
    # Extract session token from cookie or query param
    session_token = websocket.cookies.get("session_token")
    if not session_token:
        session_token = websocket.query_params.get("token")
    
    if not session_token or not await verify_session(session_token):
        await websocket.close(code=4001, reason="Unauthorized")
        return
    
    manager = get_manager()
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive; receive pings or client messages
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"event_type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)
```

**Why query param fallback**: Some proxy/iframe setups strip cookies. The `?token=` param is a fallback for `chat_embed.html` in iframes.

**Why no background send task**: The WebSocket is full-duplex. The `broadcast()` method sends directly. No need for a separate send queue.

---

## 5. Event Routing & Filtering

### 5.1 Event Types

All events published by workers are broadcast to all connected browsers. Filtering happens client-side.

| Event Type | Source | Payload | Browser Target |
|-----------|--------|---------|----------------|
| `new_message` | `handlers.py` | user_id, content, direction, timestamp | chat.html, overview.html |
| `ai_generation_started` | `llm_worker.py` | user_id, message_preview | chat.html (AI button spinner) |
| `ai_generation_completed` | `llm_worker.py` | user_id, draft, score, flags | chat.html (suggestions), queue.html |
| `ai_generation_failed` | `llm_worker.py` | user_id, error | chat.html (AI button error) |
| `suggestion_created` | `llm_worker.py` | queue_id, user_id, draft, score | chat.html (suggestion bar), queue.html |
| `message_sent` | `main.py` | user_id, content, telegram_message_id | chat.html (check mark), overview.html |
| `message_send_failed` | `main.py` | user_id, error, dedup_id | chat.html, overview.html |
| `operator_queue_update` | `llm_worker.py` | queue_id, action (created/resolved) | queue.html, overview.html |
| `stats_update` | (aggregated) | total_users, total_messages, pending_queue | overview.html |
| `pong` | server | (empty) | connection keepalive |

### 5.2 Client-Side Filtering

Each page subscribes to `ws.onmessage` and filters by `event_type`:

- **chat.html**: `new_message`, `ai_generation_*`, `suggestion_created`, `message_sent`
- **queue.html**: `operator_queue_update`, `suggestion_created`
- **overview.html**: `stats_update`, `new_message`, `message_sent`
- **chat_embed.html**: `new_message`, `ai_generation_*`, `suggestion_created`, `message_sent`

---

## 6. Event Schema

### 6.1 Standard Envelope

Every event published to Redis and sent over WebSocket follows this schema:

```json
{
  "event_type": "string",
  "user_id": 12345,
  "timestamp_ms": 1692300000000,
  "data": {
    "field1": "value1",
    "field2": "value2"
  }
}
```

### 6.2 Event Payloads

**`new_message`**
```json
{
  "event_type": "new_message",
  "user_id": 12345,
  "timestamp_ms": 1692300000000,
  "data": {
    "content": "Hello!",
    "direction": "inbound",
    "telegram_message_id": "98765",
    "username": "john_doe",
    "first_name": "John"
  }
}
```

**`ai_generation_started`**
```json
{
  "event_type": "ai_generation_started",
  "user_id": 12345,
  "timestamp_ms": 1692300000000,
  "data": {
    "message_preview": "Hello!..."
  }
}
```

**`ai_generation_completed`**
```json
{
  "event_type": "ai_generation_completed",
  "user_id": 12345,
  "timestamp_ms": 1692300000000,
  "data": {
    "draft": "Hi there! How can I help?",
    "score": 0.92,
    "flags": [],
    "was_auto_approved": true
  }
}
```

**`ai_generation_failed`**
```json
{
  "event_type": "ai_generation_failed",
  "user_id": 12345,
  "timestamp_ms": 1692300000000,
  "data": {
    "error": "LLM timeout after 3 retries"
  }
}
```

**`suggestion_created`**
```json
{
  "event_type": "suggestion_created",
  "user_id": 12345,
  "timestamp_ms": 1692300000000,
  "data": {
    "queue_id": 42,
    "draft": "Hi there! How can I help?",
    "score": 0.92,
    "flags": ["price_mention"]
  }
}
```

**`message_sent`**
```json
{
  "event_type": "message_sent",
  "user_id": 12345,
  "timestamp_ms": 1692300000000,
  "data": {
    "content": "Hi there! How can I help?",
    "telegram_message_id": 123456,
    "was_auto_approved": true,
    "confidence_score": 0.92
  }
}
```

**`message_send_failed`**
```json
{
  "event_type": "message_send_failed",
  "user_id": 12345,
  "timestamp_ms": 1692300000000,
  "data": {
    "error": "UserIsBlockedError"
  }
}
```

**`operator_queue_update`**
```json
{
  "event_type": "operator_queue_update",
  "user_id": 12345,
  "timestamp_ms": 1692300000000,
  "data": {
    "queue_id": 42,
    "action": "created",
    "draft": "Hi there!",
    "score": 0.45
  }
}
```

**`stats_update`**
```json
{
  "event_type": "stats_update",
  "user_id": null,
  "timestamp_ms": 1692300000000,
  "data": {
    "total_users": 150,
    "total_messages": 3200,
    "pending_queue": 5,
    "blocked_users": 3,
    "pending_inbound": 0,
    "dlq_count": 2
  }
}
```

---

## 7. Auth for WebSocket Upgrade

### 7.1 Cookie-Based Auth

The WebSocket endpoint reads the `session_token` cookie from the HTTP upgrade request:

```python
session_token = websocket.cookies.get("session_token")
```

This works because:
- The browser sends cookies with WebSocket upgrade requests to the same origin.
- The `session_token` cookie is httponly, same-site=lax (set by `auth.py`).
- `verify_session()` in `auth.py` already validates the token against PostgreSQL `sessions` table.

### 7.2 Query Param Fallback

For `chat_embed.html` running in an iframe (cross-origin), cookies may be blocked:

```python
if not session_token:
    session_token = websocket.query_params.get("token")
```

The `token` is the same `session_token` value, passed explicitly. The frontend reads it from a Jinja2 variable embedded in the page.

### 7.3 No Auth Changes

No modifications to `auth.py`. The existing `verify_session()` function accepts a token string and returns a user dict or None. WebSocket auth simply calls this same function.

---

## 8. Reconnection Strategy

### 8.1 Frontend Reconnection

```javascript
class RealtimeClient {
    constructor(url, options = {}) {
        this.url = url;
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 30000;
        this.reconnectAttempts = 0;
        this.handlers = {};
        this._connect();
    }
    
    _connect() {
        this.ws = new WebSocket(this.url);
        
        this.ws.onopen = () => {
            this.reconnectDelay = 1000;
            this.reconnectAttempts = 0;
            this._startPing();
        };
        
        this.ws.onmessage = (event) => {
            const parsed = JSON.parse(event.data);
            if (parsed.event_type === 'pong') return;
            const handler = this.handlers[parsed.event_type];
            if (handler) handler(parsed);
        };
        
        this.ws.onclose = (event) => {
            this._stopPing();
            if (event.code === 4001) return; // Auth failure, don't reconnect
            this._scheduleReconnect();
        };
        
        this.ws.onerror = () => {
            this.ws.close();
        };
    }
    
    _scheduleReconnect() {
        const jitter = Math.random() * 1000;
        const delay = Math.min(this.reconnectDelay + jitter, this.maxReconnectDelay);
        this.reconnectAttempts++;
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
        setTimeout(() => this._connect(), delay);
    }
    
    _startPing() {
        this._pingInterval = setInterval(() => {
            if (this.ws.readyState === WebSocket.OPEN) {
                this.ws.send('ping');
            }
        }, 30000);
    }
    
    _stopPing() {
        if (this._pingInterval) clearInterval(this._pingInterval);
    }
    
    on(eventType, handler) {
        this.handlers[eventType] = handler;
    }
}
```

### 8.2 Server-Side Keepalive

The WebSocket endpoint sends `pong` in response to `ping` messages. Additionally, the `broadcast()` method silently removes dead connections (line: `stale.append(ws)`).

### 8.3 Backoff Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Initial delay | 1s | First reconnect after 1s |
| Multiplier | 2x | Doubles each attempt |
| Max delay | 30s | Caps at 30s |
| Jitter | 0-1s random | Prevents thundering herd |
| Max attempts | ∞ | Retries until auth failure or page unload |

---

## 9. Polling Fallback

### 9.1 Strategy

WebSocket is the primary transport. Polling continues as fallback when:
- WebSocket connection fails and hasn't reconnected
- Browser doesn't support WebSocket (unlikely in modern browsers)
- User is behind a proxy that blocks WebSocket

### 9.2 Implementation

Each page's polling functions are wrapped in a WebSocket-aware check:

```javascript
let usePolling = true; // Default to polling until WS connected

const rt = new RealtimeClient('ws://localhost:8080/ws');
rt.on('new_message', handleNewMessage);
rt.on('suggestion_created', handleSuggestion);

rt.ws.onopen = () => { usePolling = false; };
rt.ws.onclose = () => { 
    usePolling = true;
    // Polling intervals already running as fallback
};

// Existing polling continues, but skips if WS is active
setInterval(() => {
    if (!usePolling) return;
    loadSuggestions();
}, 10000);

setInterval(() => {
    if (!usePolling) return;
    loadStats();
}, 5000);
```

### 9.3 Polling Intervals (Unchanged)

| Page | Function | Interval | Notes |
|------|----------|----------|-------|
| overview.html | `loadStats()` | 5s | Kept as-is; skipped when WS connected |
| overview.html | `loadRecentMessages()` | (not polled) | Only on page load |
| chat.html | `loadSuggestions()` | 10s | Skipped when WS connected |
| chat.html | `loadGlobalAutoReply()` | (not polled) | Only on page load |
| chat_embed.html | `loadSug()` | 10s | Skipped when WS connected |

**Key rule**: Polling intervals are NOT removed. They serve as fallback and initial data source.

---

## 10. AI Reply Flow (Real-Time)

### 10.1 Current Flow

1. User clicks "AI" button → `POST /api/dialogs/{id}/ai-reply`
2. Route calls `enqueue_inbound()` → returns `{"ok": true}`
3. LLM worker picks up → generates draft → scores → auto-approves or queues
4. **Problem**: No notification to browser that generation is complete

### 10.2 New Flow

1. User clicks "AI" button → `POST /api/dialogs/{id}/ai-reply`
2. Route calls `enqueue_inbound()` → returns `{"ok": true}`
3. **LLM worker emits `ai_generation_started`** → browser shows spinner on AI button
4. **LLM worker emits `ai_generation_completed`** → browser updates suggestions bar, removes spinner
5. If queued: **LLM worker emits `suggestion_created`** → browser shows suggestion chip
6. If auto-approved: **LLM worker emits `message_sent`** → browser shows check mark on sent message

### 10.3 Event Emission Points

| Location | Event | File:Line |
|----------|-------|-----------|
| `llm_worker.py` after `generate_draft()` returns | `ai_generation_started` | `workers/llm_worker.py:144` |
| `llm_worker.py` after `score_draft()` and routing | `ai_generation_completed` | `workers/llm_worker.py:166-187` |
| `llm_worker.py` on exception in `process_message` | `ai_generation_failed` | `workers/llm_worker.py:191` |
| `llm_worker.py` after `add_to_operator_queue()` | `suggestion_created` | `workers/llm_worker.py:155-160, 181-187` |
| `handlers.py` after `enqueue_inbound()` | `new_message` | `chatbotv2/handlers.py:104` |
| `main.py` after successful send | `message_sent` | `chatbotv2/main.py:75-98` |
| `main.py` on `UserIsBlockedError` | `message_send_failed` | `chatbotv2/main.py:100-101` |
| `send_worker.py` after `resolve_queue_item("approved")` | `operator_queue_update` | `workers/send_worker.py:72-76` |

### 10.4 Auto-Reply Check (Gap Fix)

Currently, after `llm_worker` generates a draft, if auto-reply is OFF, it queues to operator but doesn't check if the user was auto-reply excluded mid-processing. The `ai_generation_completed` event includes the `was_auto_approved` flag so the browser can react appropriately.

---

## 11. Ordering & Deduplication

### 11.1 Ordering

Redis Pub/Sub does not guarantee ordering across multiple publishers. However:
- Events from the same worker for the same user are naturally ordered (sequential `await publish_event()` calls).
- Cross-worker ordering is best-effort.
- The `timestamp_ms` field allows clients to sort if needed.

**Recommendation**: Don't rely on ordering. Each event is self-contained. The browser applies events independently (e.g., a `message_sent` event is always valid regardless of when it arrives).

### 11.2 Deduplication

Redis Pub/Sub can deliver duplicates if:
- Network hiccup causes the same PUBLISH to be received twice
- Client reconnects and receives cached messages (unlikely with Pub/Sub)

**Strategy**: Each event has a unique `timestamp_ms` + `event_type` + `user_id` combination. Clients can track `last_event_id` (using `timestamp_ms`) and skip events with timestamps <= last seen.

**Implementation in frontend**:
```javascript
let lastEventTimestamp = 0;

rt.on('*', (event) => {
    if (event.timestamp_ms <= lastEventTimestamp) return;
    lastEventTimestamp = event.timestamp_ms;
    // ... handle event
});
```

---

## 12. Failure Modes

### 12.1 Redis Pub/Sub Failure

**Scenario**: Redis restarts or becomes unreachable.

**Impact**: Events stop flowing to browsers. Workers continue processing normally (they use Redis Streams, not Pub/Sub, for task queues).

**Recovery**: When Redis recovers, `publish_event()` will succeed again. The `event_subscriber` in FastAPI will reconnect automatically (aioredis handles reconnection).

**Mitigation**: Polling continues as fallback. Users see stale data but don't lose functionality.

### 12.2 WebSocket Connection Drop

**Scenario**: Browser loses WebSocket connection.

**Impact**: Real-time updates stop. Polling takes over.

**Recovery**: Frontend reconnects with exponential backoff.

### 12.3 FastAPI Process Restart

**Scenario**: Dashboard process restarts.

**Impact**: All WebSocket connections dropped. `event_subscriber` task restarts. Redis Pub/Sub subscriptions are re-established.

**Recovery**: Clients reconnect. No event loss (events that happened during restart are missed, but polling catches up).

### 12.4 Worker Process Failure

**Scenario**: LLM worker crashes mid-generation.

**Impact**: No `ai_generation_completed` event emitted. Browser shows spinner indefinitely.

**Mitigation**: Frontend timeout (10s) removes spinner and shows error. Polling fallback refreshes suggestions.

### 12.5 Event Backpressure

**Scenario**: Many events published rapidly (e.g., batch message processing).

**Impact**: WebSocket sends may queue up, causing memory pressure.

**Mitigation**: `ConnectionManager.broadcast()` is async and sends sequentially. If a connection is slow, it's removed on error. Max 10 connections (localhost tool).

---

## 13. File Modification Specs

### 13.1 New Files

| File | Purpose | Dependencies |
|------|---------|-------------|
| `chatbotv2/dashboard/ws_manager.py` | WebSocket ConnectionManager class | `fastapi`, `websockets` |
| `chatbotv2/dashboard/event_subscriber.py` | Redis Pub/Sub listener background task | `db.redis`, `ws_manager` |

### 13.2 Modified Files

#### `db/redis.py` — Add 2 functions

**Current state**: 340 lines. Contains `_redis_client` singleton, all Redis operations for Streams and caching.

**Changes**:
- Add `import json` at top (already imported)
- Add `publish_event(event_type, payload, user_id)` function
- Add `subscribe_events()` function

**Lines affected**: Add ~20 lines at end of file (after `close_redis()`).

**No existing functions modified.**

#### `chatbotv2/dashboard/app.py` — Add WebSocket endpoint + startup task

**Current state**: 661 lines. 20 routes, `@app.on_event("startup")` at line 560.

**Changes**:
- Add `from chatbotv2.dashboard.ws_manager import get_manager` at top imports
- Add `from chatbotv2.dashboard.event_subscriber import start_event_subscriber` at top imports
- Add WebSocket route `/ws` (new function, ~20 lines)
- Modify `startup_event()` to also start `event_subscriber` background task

**Lines affected**: 
- Imports: add 2 lines near line 1-15
- `startup_event()`: add 1 line at line 562 (start subscriber task)
- New route: add ~25 lines after existing routes

**No existing routes modified.**

#### `chatbotv2/dashboard/auth.py` — No changes

`verify_session()` already accepts a token string. WebSocket endpoint calls it directly.

#### `workers/llm_worker.py` — Add event emissions

**Current state**: 268 lines.

**Changes**:
- Add `from db.redis import publish_event` at top (line ~30, alongside existing imports)
- Add `publish_event("ai_generation_started", ...)` before `generate_draft()` call (line ~143)
- Add `publish_event("ai_generation_completed", ...)` after routing decision (line ~187)
- Add `publish_event("ai_generation_failed", ...)` in exception handler (line ~191)
- Add `publish_event("suggestion_created", ...)` after `add_to_operator_queue()` calls (lines ~155, ~181)
- Add `publish_event("operator_queue_update", ...)` after `add_to_operator_queue()` calls

**Lines affected**: ~30 lines added (6 `publish_event` calls + 1 import).

**No existing logic modified.** Events are fire-and-forget; `publish_event` returns are ignored.

#### `chatbotv2/handlers.py` — Add event emission

**Current state**: 119 lines.

**Changes**:
- Add `from db.redis import publish_event` at top (line ~10)
- Add `publish_event("new_message", ..., user_id=user_id)` after `enqueue_inbound()` (line ~104)

**Lines affected**: ~5 lines added (1 import + 1 publish_event call).

**No existing logic modified.**

#### `chatbotv2/main.py` — Add event emissions

**Current state**: 164 lines.

**Changes**:
- Add `from db.redis import publish_event` at top (line ~11)
- Add `publish_event("message_sent", ..., user_id=entity_int)` after successful send (line ~98)
- Add `publish_event("message_send_failed", ..., user_id=entity_int)` on exception (line ~104)

**Lines affected**: ~8 lines added.

**No existing logic modified.**

#### `workers/send_worker.py` — Add event emission

**Current state**: 126 lines.

**Changes**:
- Add `from db.redis import publish_event` at top (line ~13)
- Add `publish_event("operator_queue_update", ..., user_id=user_id)` after `resolve_queue_item("approved")` (line ~76)

**Lines affected**: ~4 lines added.

**No existing logic modified.**

### 13.3 Frontend Template Modifications

#### `chatbotv2/dashboard/templates/chat.html` — Add WebSocket client

**Current state**: 566 lines. Script block at lines 359-565.

**Changes**:
- Add `RealtimeClient` class (~60 lines) at start of `<script>` block
- Add event handlers for `new_message`, `ai_generation_*`, `suggestion_created`, `message_sent`
- Wrap existing `loadSuggestions()` in polling fallback check
- Remove hardcoded `setInterval(loadSuggestions, 10000)` or make conditional

**Lines affected**: ~80 lines added in `<script>` block.

**No existing functions modified** — they continue to work as polling fallback.

#### `chatbotv2/dashboard/templates/chat_embed.html` — Add WebSocket client

**Current state**: 249 lines. Script block at lines 163-247.

**Changes**:
- Add `RealtimeClient` class (same as chat.html, ~60 lines)
- Add event handlers for same events
- Make `loadSug()` polling conditional

**Lines affected**: ~70 lines added.

#### `chatbotv2/dashboard/templates/overview.html` — Add WebSocket client

**Current state**: 293 lines. Script block at lines 245-292.

**Changes**:
- Add `RealtimeClient` class (~60 lines)
- Add event handlers for `stats_update`, `new_message`, `message_sent`
- Make `loadStats()` polling conditional

**Lines affected**: ~60 lines added.

#### `chatbotv2/dashboard/templates/queue.html` — Add WebSocket client

**Current state**: 140 lines. Script block at lines 102-139.

**Changes**:
- Add `RealtimeClient` class (~60 lines)
- Add event handler for `operator_queue_update`, `suggestion_created`
- Auto-reload on new queue item or resolution

**Lines affected**: ~50 lines added.

---

## 14. Frontend JavaScript Specs

### 14.1 Shared `RealtimeClient` Class

Defined once in each template's `<script>` block. Could be extracted to a shared JS file, but for localhost tool simplicity, each page includes its own copy.

### 14.2 Event Handler Mappings

#### chat.html

| Event | Handler | DOM Update |
|-------|---------|------------|
| `new_message` (inbound) | `handleNewMessage` | Append incoming message bubble to `#chat-messages` |
| `new_message` (outgoing) | `handleNewMessage` | Append outgoing message bubble to `#chat-messages` |
| `ai_generation_started` | `handleAiStarted` | Show spinner on `#ai-reply-btn` |
| `ai_generation_completed` | `handleAiCompleted` | Remove spinner, update suggestions if auto-approved |
| `ai_generation_failed` | `handleAiFailed` | Remove spinner, show error toast |
| `suggestion_created` | `handleSuggestion` | Add chip to `#ai-suggestions` container |
| `message_sent` | `handleMessageSent` | Update check icon on sent message |

#### overview.html

| Event | Handler | DOM Update |
|-------|---------|------------|
| `stats_update` | `handleStats` | Update stat counters and queue status |
| `new_message` | `handleNewMessage` | Prepend to `#recent-messages` list |
| `message_sent` | `handleMessageSent` | Prepend to `#recent-messages` list |

#### queue.html

| Event | Handler | DOM Update |
|-------|---------|------------|
| `operator_queue_update` (created) | `handleQueueUpdate` | Reload page or prepend new item |
| `operator_queue_update` (resolved) | `handleQueueUpdate` | Remove resolved item from list |
| `suggestion_created` | `handleSuggestion` | Reload page if on queue view |

#### chat_embed.html

| Event | Handler | DOM Update |
|-------|---------|------------|
| `new_message` (inbound) | `handleNewMessage` | Append incoming bubble to `#msgs` |
| `ai_generation_started` | `handleAiStarted` | Show spinner on `#ai-btn` |
| `ai_generation_completed` | `handleAiCompleted` | Remove spinner, call `loadSug()` |
| `suggestion_created` | `handleSuggestion` | Add chip to `#sug-bar` |
| `message_sent` | `handleMessageSent` | Update check icon |

### 14.3 DOM Element IDs (Existing)

| Page | Element | ID |
|------|---------|-----|
| chat.html | Chat messages container | `chat-messages` |
| chat.html | Message input | `message-input` |
| chat.html | Send button | `send-btn` |
| chat.html | AI reply button | `ai-reply-btn` |
| chat.html | Suggestions container | `ai-suggestions` |
| chat.html | Send form | `send-message-form` |
| overview.html | Stats counters | `stat-users`, `stat-messages`, `stat-queue`, `stat-blocked` |
| overview.html | Queue status | `queue-status` |
| overview.html | Recent messages | `recent-messages` |
| chat_embed.html | Messages container | `msgs` |
| chat_embed.html | AI button | `ai-btn` |
| chat_embed.html | Suggestions bar | `sug-bar` |
| chat_embed.html | Message input | `msg-in` |
| queue.html | Edit modal | `edit-modal` |

---

## 15. Testing Strategy

### 15.1 Unit Tests

- `test_ws_manager.py`: Test `ConnectionManager.connect()`, `disconnect()`, `broadcast()` with mock WebSocket
- `test_event_bus.py`: Test `publish_event()` publishes correct JSON to Redis
- `test_event_subscriber.py`: Test subscriber receives and forwards events

### 15.2 Integration Tests

- Test WebSocket upgrade with valid/invalid session cookie
- Test event flow: publish → subscribe → receive
- Test reconnection: close connection, verify reconnect

### 15.3 Manual Testing

1. Start all services: `python run_all.py`
2. Open `http://localhost:8080` in browser
3. Open browser DevTools → Network → WS tab
4. Verify WebSocket connection established
5. Send a message from Telegram → verify `new_message` event arrives
6. Click AI button → verify `ai_generation_started` → `ai_generation_completed` flow
7. Kill Redis → verify polling fallback works
8. Restart Redis → verify WebSocket reconnects

### 15.4 Linting

```bash
ruff format .
ruff check .
```

---

## 16. Rollback Plan

### 16.1 Feature Flag

Add to `.env`:
```
ENABLE_WEBSOCKET=true
```

In `event_bus.py`:
```python
def publish_event(...):
    if not os.getenv("ENABLE_WEBSOCKET", "true").lower() == "true":
        return 0
    # ... publish
```

### 16.2 Full Rollback

1. Remove `ENABLE_WEBSOCKET=true` from `.env` (or set to `false`)
2. Workers stop publishing events (no functional change)
3. Frontend falls back to polling (existing behavior)
4. Optionally remove new files: `ws_manager.py`, `event_subscriber.py`

### 16.3 What Stays

- All `publish_event()` calls in workers remain (harmless no-ops when flag is off)
- All polling intervals remain
- No existing behavior changes

---

## 17. Performance Considerations

### 17.1 Redis Pub/Sub Overhead

- Each `publish_event()` call serializes a small JSON object (~200 bytes) and publishes to one channel.
- On localhost, this is sub-millisecond.
- Workers publish at most ~5 events per inbound message.
- **Impact**: Negligible.

### 17.2 WebSocket Memory

- Each connection holds ~10KB of Python object overhead.
- Max 10 connections (localhost tool).
- **Impact**: ~100KB total.

### 17.3 Broadcast Performance

- `broadcast()` sends to all connections sequentially.
- With 10 connections and ~200 byte messages, this is <1ms.
- **Impact**: Negligible.

### 17.4 JSON Serialization

- Each event is serialized once (by `publish_event`) and once (by `broadcast`).
- `json.dumps()` for ~200 byte objects is <0.01ms.
- **Impact**: Negligible.

---

## 18. Security Considerations

### 18.1 Authentication

- WebSocket upgrade requires valid session cookie (same as HTTP routes).
- No additional auth mechanism needed.
- Query param fallback (`?token=`) is only for same-origin or explicitly configured embeds.

### 18.2 Cross-Site WebSocket Hijacking

- Cookie `same-site=lax` prevents cross-site WebSocket upgrade.
- No `Access-Control-Allow-Origin` header needed (WebSocket doesn't use CORS).
- **Risk**: Low for localhost tool.

### 18.3 Event Data Exposure

- Events contain message content, scores, and flags.
- Only authenticated users (with valid session) can connect.
- **Risk**: Same as existing HTTP routes.

### 18.4 No Secrets in Events

- No API keys, tokens, or passwords in event payloads.
- Only user-facing data (messages, scores, flags).

---

## 19. Monitoring & Observability

### 19.1 Logs

- `ws_manager.py`: Log connect/disconnect events at INFO level
- `event_subscriber.py`: Log subscription start/stop at INFO level
- `event_bus.py`: Log publish failures at WARNING level

### 19.2 Metrics (Future)

- `ws_connections`: Gauge of active WebSocket connections
- `events_published`: Counter of events published by type
- `events_received`: Counter of events received by browsers

### 19.3 Debug Endpoint

Add to `app.py`:
```python
@app.get("/api/ws/stats")
async def ws_stats():
    from chatbotv2.dashboard.ws_manager import get_manager

    manager = get_manager()
    return {"connections": manager.connection_count}
```

---

## 20. Dependency Check

### 20.1 Existing Dependencies

| Package | Required | Available | Notes |
|---------|----------|-----------|-------|
| `fastapi` | WebSocket support | ✅ >=0.100 | WebSocket built-in |
| `redis` | Pub/Sub | ✅ >=4.5 | aioredis supports Pub/Sub |
| `json` | Serialization | ✅ stdlib | Already imported |
| `asyncio` | Background tasks | ✅ stdlib | Already used |

### 20.2 No New Dependencies Required

FastAPI includes WebSocket support. Redis Pub/Sub is built into the `redis` package. No new pip packages needed.

### 20.3 Optional

- `websockets` package: Not required. FastAPI uses its own WebSocket implementation.

---

## 21. Implementation Order

### Step 1: `db/redis.py` — Add `publish_event()` and `subscribe_events()`

**Why first**: Foundation for everything else. Workers need this to publish events.

**Estimated effort**: 20 lines of code.

### Step 2: `chatbotv2/dashboard/ws_manager.py` — ConnectionManager class

**Why second**: Needed by both the WebSocket endpoint and the event subscriber.

**Estimated effort**: 60 lines of code.

### Step 3: `chatbotv2/dashboard/event_subscriber.py` — Redis listener task

**Why third**: Bridges Redis Pub/Sub to the ConnectionManager.

**Estimated effort**: 40 lines of code.

### Step 4: `chatbotv2/dashboard/app.py` — WebSocket endpoint + startup task

**Why fourth**: Exposes WebSocket to browsers and starts the subscriber.

**Estimated effort**: 30 lines of code.

### Step 5: Worker event emissions (llm_worker, handlers, main, send_worker)

**Why fifth**: Adds events to the pipeline. Can be tested with Redis CLI.

**Estimated effort**: 50 lines total across 4 files.

### Step 6: Frontend JavaScript (chat.html, chat_embed.html, overview.html, queue.html)

**Why sixth**: Consumes events. Depends on all backend pieces.

**Estimated effort**: 260 lines total across 4 templates.

### Step 7: Feature flag + cleanup

**Why last**: Safety net and documentation.

**Estimated effort**: 10 lines.

**Total estimated effort**: ~470 lines of new code, 0 lines of modified existing logic.

---

## 22. Open Questions

| # | Question | Resolution |
|---|----------|------------|
| 1 | Should the `RealtimeClient` be extracted to a shared JS file? | No — each template includes its own copy for simplicity. Can be deduplicated in Phase 2. |
| 2 | Should we add SSE fallback for environments that block WebSocket? | No — localhost tool; WebSocket works everywhere. Can add in Phase 2 if needed. |
| 3 | Should events include a monotonically increasing sequence number? | No — `timestamp_ms` is sufficient for dedup on localhost. |
| 4 | Should the `event_subscriber` buffer events during brief disconnects? | No — Pub/Sub is fire-and-forget. Polling catches up missed events. |

---

## 23. Out of Scope

- ❌ Modifying existing application logic
- ❌ Changing DB schema
- ❌ Changing Redis Stream structures
- ❌ Adding new pip dependencies
- ❌ Removing polling
- ❌ Refactoring unrelated code
- ❌ Multi-server/distributed deployment
- ❌ Event persistence/durability
- ❌ SSE transport
- ❌ Authentication changes to `auth.py`

---

## 24. Success Criteria

1. ✅ WebSocket connection established on page load
2. ✅ Events flow: Worker → Redis Pub/Sub → FastAPI → Browser
3. ✅ Real-time message updates (no 10s delay)
4. ✅ Real-time AI suggestion updates (no 10s delay)
5. ✅ AI generation status visible (spinner → complete/failed)
6. ✅ Operator queue updates in real-time
7. ✅ Overview stats update in real-time
8. ✅ Reconnection works after connection drop
9. ✅ Polling fallback works when WebSocket is down
10. ✅ No modifications to existing application code
11. ✅ All existing tests pass
12. ✅ Linting passes (`ruff format .` and `ruff check .`)
