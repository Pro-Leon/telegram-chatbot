# Luna pre-fix baselines — Phase 0.3

Canonical ref: `docs/LUNA_RELIABILITY_PROGRAM.md` Phase 0.3.
Collected: 2026-09-24 (local dev DB, all-time — only 77 telemetry rows exist, no 7-day volume).
Queries: `scripts/baseline_queries.sql` (read-only; the run below used all-time variants since `created_at` window is empty on dev).

## Pre-fix numbers

| Metric | Value (n=77 turns) |
|---|---|
| `prompt_echo` turns (`scoring_flags`/`conversational`/`roleplay` `? 'prompt_echo'`) | 0 |
| safety failures (`validation_outcome LIKE '%safety%'`) | 0 |
| quality failures (`validation_outcome LIKE '%quality%'`) | 0 |
| `routing_decision='auto_approved'` | 58 |
| `routing_decision='operator_queued'` | 2 |
| `routing_decision='one_call_failed_operator_queue'` | 16 |
| `routing_decision='suppressed_excluded'` | 1 |
| Repeat: normalized-equal back-to-back outbound pairs / outbound turns (`messages`) | 1 / 6 |
| Dedup-hit sums (`duplicate_send_suppressed` / `already_executed` / `inbound_redelivery`) | 0 / 0 / 0 |
| `XPENDING send_messages/send_workers` | 0 pending |
| `XPENDING inbound_messages/llm_workers` | 0 pending |
| Stream lengths `send_messages` / `inbound_messages` / `dead_letter_queue` | 9 / 5 / 20 |

## Notes

* Veto reason (`blocking_flags` vs `below_threshold` vs `corroborated_handoff`) is NOT in the DB —
  only in the `Routing veto` log line (`workers/llm_worker.py:5275`, extended in 0.3) and the
  `ai.generation_completed` event payload. Grep logs for per-reason splits until Phase 4.1 persists them.
* `prompt_echo_turns=0` is consistent with Phase 0.2 (placeholder no longer in prompt) + 0.1 detector;
  re-run Q1 after Phase 1 replay to confirm it stays 0 while `operator_queued` absorbs echo fixtures.
* DLQ length 20 is pre-existing backlog, not drained here (Phase 3 scope).
