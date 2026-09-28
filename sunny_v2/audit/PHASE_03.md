# Audit PHASE_03 — V1 disable

- Mechanism: `core/architecture_router.py` (new) + `sunny_v1_enabled=false`
  in `core/config.py` / `.env.example`.
- Gates (fail-closed, server-side flag only):
  - `chatbotv2/handlers.py handle_incoming_message` — returns before
    persistence/debounce/enqueue; logs suppression.
  - `workers/llm_worker.py process_message` — returns before draft/score/
    routing/enqueue/queue-insert/state mutation; emits `ai.generation_failed
    {error: v1_disabled}` for observability.
  - `workers/send_worker.py flush_queue` — returns 0; dashboard remains
    readable, nothing auto-flushes.
- Not gated (PRESERVED): `chatbotv2/main.py` send delivery,
  `workers/scheduler_worker.py` (commerce reconciliation/follow-ups),
  dashboard manual sends (human override), commerce services/providers.
- No V1 domain logic refactored. V2 activation later uses `SUNNY_V2_ENABLED`
  without resurrecting V1.
