# Sunny V2 — Specification Root

## Purpose

Sunny V2 is a **new relationship-conversation architecture**, not a refactor of
the legacy (V1) conversational system. It owns relationship context, memory,
conversation continuity, escalation strategy, response planning, and response
generation. Deterministic commerce remains an external authority integrated
through a strict contract.

## Status

```text
V2 STATUS: SPECIFICATION / NOT IMPLEMENTED
V1 STATUS: DISABLED
COMMERCE STATUS: PRESERVED
```

- **V1 (legacy conversational):** DISABLED via `core/architecture_router.py`.
  Ingress (`chatbotv2/handlers.py`), generation (`workers/llm_worker.py`), and
  operator-queue flush (`workers/send_worker.py`) fail closed when
  `SUNNY_V1_ENABLED` is not `true`. No V1 domain logic was refactored.
- **V2:** specification only. `sunny_v2/` contains design documents. No V2
  runtime modules, tables, queues, or APIs were created in the cutover task.
- **Commerce:** PRESERVED. Execution, attribution, reconciliation,
  post-purchase, provider integration, and shared delivery (`chatbotv2/main.py`
  send loop) are untouched.

## What V2 owns (FUTURE)

Relationship context, relationship memory, conversation continuity and state,
response strategy and planning, escalation state, semantic interpretation,
memory extraction and consolidation, context assembly, response generation.

## What commerce owns (PRESERVED)

Products, prices, availability, eligibility, ownership, offer state, purchase
state, provider execution, transaction state, commerce attribution. See
`COMMERCE_CONTRACT.md`.

## Authoritative documentation location

```text
sunny_v2/
```

All other `docs/*.md` forensic reports are ARCHIVED historical material, not
current architecture. `sunny_v2/` takes precedence on any conflict.

## Status vocabulary

Every technical statement in these docs is tagged:

- `CURRENT` — true of the repository right now.
- `FUTURE` — a V2 design decision, not yet implemented.
- `PRESERVED` — existing commerce/shared component, kept as-is.
- `DEPRECATED` — legacy, do not extend.
- `DISABLED` — legacy path, actively gated off.
- `UNKNOWN` — purpose not established; requires review before action.

## Implementation status

No V2 implementation phase has started. The next safe action is Phase 1 of
`PHASED_IMPLEMENTATION_PLAN.md` (core domain and persistence), executed under
`OPENCODE_BUILD_INSTRUCTIONS.md`. Do not implement V2 services before that.
