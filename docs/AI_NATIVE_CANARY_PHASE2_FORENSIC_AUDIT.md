# AI-Native Canary Phase 2 Forensic Audit

**Date:** 2026-08-28  
**Status:** Phase 1 Complete — Forensic Audit

---

## Executive Summary

The existing observability infrastructure includes an event bus (Redis Pub/Sub), Phase 1 events (8 types), tool events (3 types), and a tool audit log table. The primary gaps are: no per-generation telemetry table, no LLM provider timing, no scoring latency tracking, and no token usage tracking. All gaps can be filled additively without breaking authority boundaries.

---

## Runtime Call Graph (Current)

```
Telegram Message
    ↓
handlers.py:handle_incoming_message()
    → publish_event("message.created")           [EXISTS]
    ↓
debounce → Redis Stream
    ↓
llm_worker.py:process_message()
    → acquire_user_lock()                         [EXISTS]
    → upsert_user()                               [EXISTS]
    → build_qwen3_context()                       [EXISTS - NO TIMING]
    → _try_commerce_draft()                       [EXISTS - NO TIMING]
    → publish_event("ai.generation_started")      [EXISTS]
    → [CANARY ROUTING]                            [EXISTS]
    → generate_draft() / generate_draft_with_tools() / run_agent_runtime()
                                                  [EXISTS - NO PROVIDER TIMING]
    → score_draft()                               [EXISTS - NO SCORING TIMING]
    → publish_event("ai.generation_completed")    [EXISTS]
    → post_process()                              [EXISTS]
    → release_user_lock()                         [EXISTS]
    ↓
send_worker.py
    → publish_event("message.sent")               [EXISTS]
```

---

## Existing Observability Infrastructure

| Component | Status | Location |
|-----------|--------|----------|
| Event Bus (Redis Pub/Sub) | EXISTS | `core/event_bus.py` |
| Phase 1 Events (8 types) | EXISTS | `llm_worker.py`, `main.py`, `handlers.py` |
| Tool Events (3 types) | EXISTS | `core/llm_tools.py` |
| Structured Logging (JSON) | EXISTS | `core/logging_config.py` |
| Worker Heartbeat | EXISTS | `core/worker_heartbeat.py` |
| Health/Readiness Endpoints | EXISTS | `core/health.py` |
| Circuit Breakers (in-memory) | EXISTS | `core/circuit_breaker.py` |
| Rate Limiter (in-memory) | EXISTS | `core/rate_limiter.py` |
| Tool Audit Log (DB) | EXISTS | `tool_audit_log` table |
| Messages Table (audit) | EXISTS | `messages` table |

---

## Missing Telemetry

| Gap | Impact | Priority |
|-----|--------|----------|
| No per-generation telemetry table | Cannot track individual generation lifecycle | P1 |
| No LLM provider timing | Cannot measure provider latency | P1 |
| No scoring latency tracking | Cannot measure scoring overhead | P1 |
| No token usage tracking | Cannot measure token consumption | P2 |
| No queue depth monitoring | Cannot measure consumer lag | P2 |
| No error rate counters | Cannot aggregate error rates | P2 |
| No end-to-end latency (receipt to send) | Cannot measure full cycle | P2 |

---

## Recommended Implementation Points

### 1. New Database Table: `generation_telemetry`

```sql
CREATE TABLE generation_telemetry (
    id BIGSERIAL PRIMARY KEY,
    generation_id UUID NOT NULL UNIQUE,
    user_id BIGINT NOT NULL,
    creator_id BIGINT,
    runtime_mode TEXT NOT NULL,           -- 'legacy' or 'agent'
    provider_name TEXT NOT NULL,
    model_name TEXT NOT NULL,
    context_build_ms INTEGER,
    generation_latency_ms INTEGER,
    scoring_latency_ms INTEGER,
    scoring_score FLOAT,
    scoring_flags JSONB,
    tool_calls_count INTEGER DEFAULT 0,
    tool_names JSONB DEFAULT '[]',
    total_e2e_latency_ms INTEGER,
    routing_decision TEXT NOT NULL,       -- 'auto_approved', 'operator_queued', 'commerce_response'
    success BOOLEAN NOT NULL DEFAULT TRUE,
    failure_type TEXT,
    worker_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 2. In `llm_worker.py` — Add Timing Around Key Calls

- `build_qwen3_context()` timing
- `generate_draft()` / `generate_draft_with_tools()` timing
- `score_draft()` timing
- Insert into `generation_telemetry` at end

### 3. In `core/llm_provider_gemini.py` — Add Provider Timing

- Wrap `client.aio.models.generate_content()` with timing
- Log latency at INFO level

### 4. In `core/scoring.py` — Add Scoring Timing

- Wrap `provider.generate()` call with timing
- Log scoring latency

---

## P0/P1/P2 Classification

### P0 — Authority Violations

None. All existing authority boundaries remain intact.

### P1 — Must-Implement for Production Measurement

| ID | Issue | Impact |
|----|-------|--------|
| P1-1 | No per-generation telemetry table | Cannot track individual generation lifecycle |
| P1-2 | No LLM provider timing | Cannot measure provider latency |
| P1-3 | No scoring latency tracking | Cannot measure scoring overhead |

### P2 — Nice-to-Have

| ID | Issue | Impact |
|----|-------|--------|
| P2-1 | No token usage tracking | Cannot measure token consumption |
| P2-2 | No queue depth monitoring | Cannot measure consumer lag |
| P2-3 | No error rate counters | Cannot aggregate error rates |

---

## Conclusion

All P0 invariants preserved. No authority violations. Implementation can proceed safely with additive telemetry.

---

*Audit completed: 2026-08-28*
