# M6 Stage B — Implementation Report: Operator/Content Integrity

## 1. Objective

Implement the confirmed M6 Stage A findings, beginning with edited-content
integrity and the directly connected operator/dashboard boundaries:
queue UI → dashboard route → persistence → enqueue → send worker → Telethon.
Smallest compatible corrections only; no redesign of M2/M3/M4/M5, commerce
authority, routing, OneCall, generation semantics, or streams.

## 2. Confirmed findings implemented

- **M6-01 (HIGH): edit-form send silently discarded the edit.** `queue.html`
  posts `FormData{status, content}` to `/api/suggestions/{id}/send`, whose
  handler declared no `content` param and enqueued `draft_content` verbatim.
- **M6-02 (MEDIUM): edited content under stale identity, no revalidation.**
  Resolve-persisted edits sent under the original `generation_id` and
  `queue_item:{id}` dedup; `was_edited` was forwarded on one path and
  hardcoded `False` on the other; row confidence (evaluated against the
  original draft) could derive `was_auto_approved=True` for edited bytes.
- **M6-03 (MEDIUM): vault media attribution/identity.** Creator resolved via
  any-status lowest-ID lookup; caption-blind dedup suppressed edited-caption
  resends; no generation identity on media sends.
- **M6-04 (LOW): free-form resolve statuses** could strand queue items.
- **M6-05 (LOW): AI-reply `or 0` identity fork** for NULL telegram IDs.
- **WS fail-closed (secondary boundary):** empty creator filter matched all
  creators' events on resolver failure.
- **M6-07 (auditability, included minimally):** direct-send path emitted no
  event; no edit provenance anywhere.
- Commerce quarantine, recipient/creator-mutation impossibility: verified by
  contract tests, no code change (already correct).

## 3. Root cause

The queue UI, the send endpoint, and the persistence layer disagreed about
where edited content lives: the form sent it to an endpoint that ignores it,
while the endpoint that persists it never enqueues. Identity (generation +
dedup) was bound once at queue-insert time and never revisited on edit, and
the flush/send-worker paths disagreed about `was_edited`. Vault and WS paths
used weakest-available resolution/filtering.

## 4. Exact changes

1. `api_send_suggestion` accepts `content: Form(None)`; a differing edit is
   persisted first via `resolve_queue_item(id, "pending", final_content)`
   (state unchanged, 409 on race), then the edited bytes are enqueued with
   `was_edited=True`, `was_auto_approved=False`, dedup
   `queue_item:{id}:{sha16(content)}`; unedited sends keep the legacy
   `queue_item:{id}` identity. Empty edits → 422. Non-string Form markers
   (direct callers) treated as absent.
2. `api_resolve_queue` rejects statuses outside
   `{pending, approved, rejected, failed}` with 422.
3. `process_approved_message(..., was_edited=False)`; edited forces
   `was_auto_approved=False`; flush forwards `bool(item.edited)`.
4. `send_suggestion` publishes `operator_queue.updated` (action approved +
   additive `edited`, `resolved_by`; content stays in the DB row).
5. Vault `_require_creator_id`: prefer `list_active_creator_ids()[0]`
   (same Fangate authority), legacy any-status lookup only as fallback.
6. Vault send-media: dedup `md5(user:media:caption)`; explicit
   `manual_generation_id("vault:...")` correlation.
7. AI-reply: NULL telegram ID → `manual_generation_id("ai-reply:...")`
   instead of MD5-with-0; repeat-click suppression now reported via additive
   `deduped` response field.
8. `ws_manager.broadcast`: empty creator filter matches no creator-scoped
   events (global-only); documented; polling fallback untouched.

## 5. Files changed

- `chatbotv2/dashboard/routes/queue.py` (edit lifecycle, status allowlist, send event)
- `workers/send_worker.py` (`was_edited` param + forwarding)
- `chatbotv2/dashboard/routes/vault.py` (creator preference, dedup, generation)
- `chatbotv2/dashboard/routes/messages.py` (AI-reply identity + deduped flag)
- `chatbotv2/dashboard/ws_manager.py` (fail-closed broadcast filter)
- `tests/test_vault.py` (2 tests updated to corrected contracts + 1 added)

## 6. Files created

- `tests/test_m6_operator_integrity.py` (21 tests)
- `docs/AI_NATIVE_ARCHITECTURE_PHASE_M6_IMPLEMENTATION_REPORT.md` (this file)

## 7. Contracts affected

- `POST /api/suggestions/{id}/send`: now accepts optional `content`; returns
  additive `edited` boolean; emits `operator_queue.updated` on success.
- `POST /api/queue/{id}/resolve`: 422 on unknown status (previously persisted verbatim).
- `POST /api/dialogs/{id}/ai-reply`: additive `deduped` boolean in response.
- Vault send-media dedup now caption-bound (old `md5(user:media)` identities
  do not collide with new ones; no cross-talk).
- `process_approved_message` gains trailing `was_edited=False` (compatible).
- Unchanged: generation preservation on queue/scheduler paths, `queue_item:{id}`
  base dedup, pending-state guards, 409 race semantics, commerce 501s.

## 8. Invariants verified

1. Operator intent == outbound bytes (edit persisted before enqueue; tests
   assert payload, row, and event agree).
2. Creator scope never weakened (all send/mutation paths still resolver- or
   row-derived; foreign-creator 404s tested; vault stays within Fangate
   authority, preferring active).
3. Dedup effective (stable IDs for identical retries; distinct IDs for edited
   content/captions; no double-send introduced — 409 path still relies on
   dedup backstop).
4. Pending-state + send-side gates intact (blacklist/rate/dedup/creator at
   send; blacklist test passes).
5. No operator path reaches commerce execution (contract tests).
6. Commerce 501 quarantine intact (contract tests).
7. M2/M3/M4/M5 untouched (milestone suites green, §10).

## 9. Tests added

`tests/test_m6_operator_integrity.py` (21): edit reaches payload/persistence/
event; unedited legacy identity; empty-edit 422; lost-race 409 without event;
status allowlist (incl. foreign-creator 404); signature negatives (no
recipient/creator params); flush `was_edited` parity + auto-approval honesty;
vault active-preference/fallback/fail-closed + caption dedup/generation;
AI-reply synthetic-vs-canonical identity + repeat signaling; WS empty-filter
and mismatch filtering; commerce quarantine (no execution edge; 501s);
manual-send fail-closed/permitted.
`tests/test_vault.py`: prefer-active test added; 2 tests updated to corrected
contracts (dedup includes caption; resolver mocks cover both lookups).

## 10. Exact tests executed

- New: `test_m6_operator_integrity.py` — 21 passed.
- Updated: `test_vault.py` — 54 passed.
- Affected: `test_m4_creator_isolation.py` + `test_m5_profile_freshness.py` —
  45 passed; `test_realtime.py` — all pass; `test_first_message_identity_correlation.py`
  — 50 passed; `TestOperatorQueueFlow` (incl. HTTP suggestion-send) — pass
  except pre-existing env flake below; `test_forensic_remediation` /
  `test_p18` / `test_p14` — pass except 3 pre-existing (below);
  `test_dropfans_dashboard.py` + `test_followups_dashboard.py` — pass except
  1 pre-existing; phase-1 WS broadcast classes — 18 passed.

## 11. Results

All new and updated tests pass. No new failures introduced (see §12 for
attribution of every observed failure).

## 12. Pre-existing failures (demonstrated unrelated)

- `phase46::TestOperatorQueueFlow::test_send_worker_flush_*` (subset runs) +
  `TestMessagePipelineE2E` failures: live dev-PG pool shared across per-test
  event loops (`InterfaceError: another operation is in progress` in
  untouched creator enumeration) or worker-evolution drift; pass solo or in
  other groupings; my diff is outside their observable graph (mocks cover) or
  untouched files.
- `TestPostProcessIsolation::test_post_process_calls_profile_and_summarize`:
  fails identically before/after (fail-closed early return vs stale
  expectation).
- `forensic_remediation` blacklist/shape + migration-state tests: stale
  source-shape assertion (M4-era signature), live-Redis dependence, dev-DB
  migration state.
- `followups_dashboard::test_create_scheduled_message_without_creator_id`:
  expects creator-optional create; code correctly fails closed.
- `phase17::TestSinglePass`, phase2/canonical order flakes, `commerce_state`
  in-file order dependence, qwen3 provider drift, OneCall model-string drift:
  as documented in M4/M5 reports, unchanged by M6 ( Commerce-state file
  result identical to M4 baseline: 36F/29P).
- Vault dedup/resolver tests that pinned remediated contracts were updated
  (§9), not left red.

## 13. Performance measurements

No hot-path algorithmic change: one extra conditional UPDATE only when an
edit is submitted; one extra small SELECT (active integrations) on vault
calls; no new queries on send/flush/AI-reply/WS paths. No dedicated
benchmarks applicable; full new+updated suites run in seconds (21 M6 tests
~4s; 54 vault ~10s).

## 14. Architecture confirmation

Send pipeline shape preserved (enqueue → dedup/rate/blacklist → Telegram);
queue pending-state machine preserved; generation-as-correlation (never
authorization) preserved; commerce authority untouched with zero new edges;
single-creator resolver semantics untouched (vault fix stays within Fangate
authority); WS remains acceleration-only with polling fallback; M2/M3/M4/M5
guarantees intact per §10.

## 15. Remaining risks

1. ms-scale race: concurrent flush snapshot vs edit-persist on the same item
   can deliver both versions (PG row vs Redis stream are separate systems;
   at-least-once direction preserved; dedup cannot backstop divergent
   content by design).
2. `resolve_queue_item`/`get_queue_item` legacy unscoped branches remain for
   non-dashboard callers (no dashboard caller omits creator).
3. Operator identity stays self-asserted under a shared admin password (no
   roles); audit events now carry it but cannot authenticate it.
4. Blacklist fail-open and the `or 0`-equivalent legacy corners elsewhere
   are pre-existing design, unchanged.
5. AI-reply repeat clicks return success-shaped responses (now with explicit
   `deduped` flag).

## 16. Next phase boundary

Suggested M7 (if commissioned): operator audit-trail durability (persist
content diffs server-side), role-aware dashboard auth, flush-path row
re-read before enqueue to narrow the residual double-delivery window noted
in Section 15 item 1, and scheduled-content freshness policy. None started
here.
