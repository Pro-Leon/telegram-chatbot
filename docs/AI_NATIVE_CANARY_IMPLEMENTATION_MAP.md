# AI-Native Canary Implementation Map

**Date:** 2026-08-28  
**Status:** Canary Implementation Complete

---

## Architecture

### Canary Routing Flow

```
Telegram Message
    ↓
handlers.py:handle_incoming_message()    [UNCHANGED]
    ↓ (debounce, rate limit, Redis Stream)
llm_worker.py:process_message()
    ↓
build_qwen3_context()                    [UNCHANGED]
    ↓
_try_commerce_draft()                    [UNCHANGED]
    ↓
[CANARY ROUTING]                         [NEW]
    ↓
should_use_agent(user_id, creator_id)    [NEW - deterministic hash]
    ↓
┌─────────────────────────────────────────────────────────┐
│ IF canary_enabled AND sample_rate > 0:                  │
│   IF hash(user_id) < sample_rate:                       │
│     → Agent Runtime (build_agent_state + run_agent)     │
│   ELSE:                                                 │
│     → Legacy Runtime                                    │
│ ELSE:                                                   │
│   → Legacy Runtime                                      │
└─────────────────────────────────────────────────────────┘
    ↓
score_draft()                            [UNCHANGED]
    ↓
enqueue_send() / operator_queue          [UNCHANGED]
```

### Canary States

| Mode | Behavior | Use Case |
|------|----------|----------|
| `legacy` | Existing behavior only | Default, production |
| `canary` | Percentage-based routing | Gradual rollout |
| `agent` | Agent for all conversations | Full activation |

---

## Files Changed

### New Files

| File | Purpose |
|------|---------|
| `agent/canary.py` | Canary routing logic |
| `tests/test_ai_native_canary.py` | Canary tests |
| `docs/AI_NATIVE_CANARY_FORENSIC_AUDIT.md` | Forensic audit |
| `docs/AI_NATIVE_CANARY_IMPLEMENTATION_MAP.md` | This document |

### Modified Files

| File | Change |
|------|--------|
| `core/config.py` | Added canary configuration settings |
| `workers/llm_worker.py` | Added canary routing and agent runtime integration |
| `agent/__init__.py` | Added canary exports |

---

## Configuration

### Default (Safe)

```bash
AI_RUNTIME_MODE=legacy
AI_AGENT_CANARY_ENABLED=false
AI_AGENT_CANARY_SAMPLE_RATE=0.0
AI_AGENT_CANARY_CREATOR_IDS=
```

### Canary (Gradual Rollout)

```bash
AI_RUNTIME_MODE=canary
AI_AGENT_CANARY_ENABLED=true
AI_AGENT_CANARY_SAMPLE_RATE=0.01  # 1% of conversations
AI_AGENT_CANARY_CREATOR_IDS=      # All creators
```

### Full Agent

```bash
AI_RUNTIME_MODE=agent
AI_AGENT_CANARY_ENABLED=true
AI_AGENT_CANARY_SAMPLE_RATE=1.0  # 100% of conversations
AI_AGENT_CANARY_CREATOR_IDS=
```

---

## Canary Behavior

### Deterministic Routing

- Same user_id always gets same decision
- Hash is stable across messages
- No random behavior between consecutive messages

### Creator Filtering

- Optional creator ID filter
- Empty = all creators
- Comma-separated list of creator IDs

### Fallback

- Agent runtime failure → legacy fallback
- Canary check failure → legacy fallback
- Any exception → legacy fallback

---

## Safety Model

### Guarantees

1. Legacy mode always available
2. Canary is opt-in (disabled by default)
3. Deterministic routing per user
4. Agent failure falls back to legacy
5. No duplicate outbound messages
6. Commerce authority unchanged
7. All existing invariants preserved

### Rollback

1. Set `AI_RUNTIME_MODE=legacy`
2. Restart worker
3. Immediate rollback, no data loss

---

*Implementation map created: 2026-08-28*
