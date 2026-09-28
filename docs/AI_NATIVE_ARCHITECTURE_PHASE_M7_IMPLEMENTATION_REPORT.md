# AI Native Architecture — Phase M7 Implementation Report

## Reliability, Auditability & Integrity Remediation (Stage B)

Stage A forensic audit is the source of truth for findings M7-01…M7-15.
Stage B implements the confirmed surgical work only. No architecture redesign,
no role model, no commerce-authority change, no exactly-once conversion,
no destructive migration was executed.

---

## 1. Executive summary

**Implemented (surgical, additive, independently deployable):**

- **B1 — Audit event contract** (`core/audit.py` new; `operator_audit_events`
  table via new migration + `db/schema.sql` append, NOT applied to any
  database in this pass): closed `event_type`/`actor_type` vocabularies,
  hash-only content handling (never raw text), correlation fields, lifecycle
  transitions, best-effort durable writer that never raises.
- **B5 — Actor identity propagation**: server-derived actors
  (`human` from auth session, `worker`/`scheduler` from worker id, `ai`,
  `system`, `migration`, explicit `unknown`) threaded as additive
  `actor_type`/`actor_id` stream keys route → stream → worker → audit;
  `strip_spoofed_actor` helper for consumer boundaries; legacy `resolved_by`
  retained.
- **B3 — Queue lifecycle + flush/edit race**: durable records for draft
  creation, edit, enqueue, approve, reject, stale-suppression, cancel-race;
  flush performs an authoritative pending-row re-read before enqueue and
  drops divergent snapshots (`stale_suppressed`); flush dedup is now
  content-bound for edited bytes (`queue_item:{id}:{sha16}`, legacy
  `queue_item:{id}` preserved for unedited); `was_auto_approved` is derived
  from the authoritative row.
- **B4(a–c) — Scheduler lifecycle**: durable records for schedule creation
  (with caller actors), claim, enqueue, success, failure, suppress, cancel,
  cancel-race, recovery; ineligible recipients are terminally **suppressed**
  (`completed` + `last_error='suppressed:…'` — no new status, no migration);
  terminal-mark results are checked and races audited as `cancel_raced`
  with the send outcome preserved.
- **B2 — Direct-send audit**: intent + enqueue + failure records on
  `POST /api/send-message`, `POST /api/dialogs/{id}/send`,
  `POST /api/vault/send-media`; success/failure records in the send-stream
  consumer linking `telegram_message_id`. Validation/rate-limit untouched,
  responses unchanged.
- **B8 — Vault integrity**: pre-enqueue mirror ownership check (fail-closed
  on provable mismatch, fail-open with explicit `asset_unverified` audit on
  mirror-miss to preserve M6 availability); additive canonical snapshot hash
  (`asset_hash`, `asset_verified`) in payload; M6 dedup string UNCHANGED;
  `finalize/release_delivery` accept optional `creator_id` with scoped SQL
  (all internal callers updated); `delete_media` reports the real outcome.
- **B7 — Lab boundary**: creator pinned to single-creator authority (403 on
  mismatch, 404 when creator unknown); all three raw persona-ID lookups
  scoped (`AND (creator_id=$2 OR creator_id IS NULL)`); foreign persona
  rejected 404 pre-evaluation; actor/creator/persona/turn-count audit;
  multi-turn batches truncated at 10 (additive `truncated` flag). LLM
  pipeline untouched.
- **B6 — Residual scope hardening**: creator predicates added to
  `pages.py` (queue/users/chats/chat/embed), `ai_intel.py` (4 queries),
  `dialogs.py`/`users.py` unsegmented lists (membership EXISTS),
  `search_messages` (+ route threading), `get_user_analytics`/`timeline`
  (optional creator; rank stays platform-relative by design); segment
  message/queue/purchase/vault/commerce generators bind creator explicitly
  (also fixes a latent multi-rule misbinding in previously-"scoped"
  generators); deprecation warnings on legacy id-only `get_queue_item`,
  `resolve_queue_item`, `finalize/release_delivery` branches.

**Deferred / not executed (per scope rules):**

- **B4(d) scheduled dedup migration** — analysis + plan only (section 12).
  No DDL executed, no data rewritten.
- Role-based auth, shared-password removal, multi-creator admin model,
  scheduled rescheduling/versioning policy, exactly-once semantics — all
  out of scope, untouched.
- Structural globals without a creator column (`users`, `user_profiles`
  outer row, notes/tags/attention, `settings` stats, analytics ranking)
  are documented exceptions (section 10), not redesigned.

**No commits were made. No production writes were performed.** On explicit
approval ("apply all migrations", 2026-09-16), all 7 pending migrations were
applied to the **local dev database only** (`POSTGRES_DSN`
`postgresql://postgres:postgres@127.0.0.1:5432/postgres`):
`20260917000000`, `20260917010000`, `20260918000000`, `20260919000000`,
`20260920000000`, `20260921000000`, `20260922000000` (M7 audit table).
Pre-apply review found no destructive statements in any pending file;
dependency tables (`creators`, `users`, `commerce_offers`) verified present;
row counts before/after identical (messages 90, operator_queue 29,
scheduled_messages 0, users 7, commerce_offers 0, vault_media_deliveries 0).
`migrate status` → 35 applied, 0 pending, up to date. The audit writer was
verified end-to-end against dev (insert → hash-only read-back → delete, plus
cleanup of one test-generated row; table left empty). The B4(d) dedup index
was verified UNCHANGED post-apply (`idx_scheduled_messages_dedup` still
global) — B4(d) remains plan-only. Staging/production migrations are still
pending approval.

---

## 2. M7 findings disposition

| ID | Finding | Disposition |
|----|---------|-------------|
| M7-01 | Flush-vs-edit double/stale delivery | **Fixed with guard**: authoritative re-read + `stale_suppressed`; divergent dedup makes collisions observable. Residual post-re-read window (re-read→XADD RTT) documented — narrowed from poll-cycle scale, not eliminated (separate PG/Redis systems). |
| M7-02 | Stale `was_auto_approved` derivation | **Fixed**: approval flags derived from the authoritative re-read row; edited bytes force manual attribution; flush dedup content-bound. |
| M7-03 | Self-asserted/dropped operator identity | **Fixed with guard**: server-derived envelope on all paths; `unknown` explicit. Shared-password weakness itself retained by design (no roles per scope). |
| M7-04 | No durable audit record | **Fixed with guard**: B1 contract + per-path coverage. Durability requires the new migration to be applied (pending); until then log-degraded, never blocking. |
| M7-05 | Overwrite-only edit history | **Fixed with guard**: edit/approve events carry original+final hashes, editor, transition. No version table (hashes + row refs deemed sufficient for Stage B). |
| M7-06 | Scheduler ambiguity + cancel loss + no events | **Fixed**: suppress terminal with reason; cancel/cancel-raced audits; terminal-mark verification; claim/enqueue/success/failure/recover events. |
| M7-07 | Global scheduled `dedup_key` uniqueness | **Migration pending**: B4(d) plan only (section 12). Code gate unchanged. |
| M7-08 | Worker-global reads by design | **Intentionally unchanged**: documented exception; dashboard paths use scoped variants (verified). |
| M7-09 | Lab cross-creator persona read | **Fixed**: authority pin + scoped reads + rejection + audit + turn bound. |
| M7-10 | Vault unvalidated mutable references + dual dedup | **Fixed with guard**: ownership check, additive snapshot hash, M6 dedup preserved. Edited-caption-after-terminal stays suppressed (existing idempotency); recorded as approved semantics (section 11, item 7). |
| M7-11 | Vault finalize/release IDOR | **Fixed**: optional creator scoping; all internal callers pass creator; legacy id-only retained with warning for compat. |
| M7-12 | Reachable unscoped reads | **Fixed with guard**: scoped where a creator column exists; structural globals documented (section 10). |
| M7-13 | Optional-creator helper branches | **Fixed with guard**: deprecation warnings (queue + vault); personas/commerce helpers documented caller-dependent. No deletions. |
| M7-14 | Split failure/DLQ semantics | **Fixed with guard**: durable success/failure records in consumer; existing events/DLQ behavior untouched. |
| M7-15 | Scheduled surface unknowns | **Confirmed, folded in**: UTC-aware callers, no edit API, idempotent aftercare, DLQ replay bounds verified. Freshness policy deferred per scope. |

---

## 3. Files changed

### New

- `core/audit.py` — B1 contract + B5 actors + best-effort writer. (M7-03/04/05/14)
- `db/migrations/20260922000000_m7_operator_audit.sql` — `operator_audit_events`
  table + indexes, idempotent. NOT applied. (M7-04)
- `tests/test_m7_audit_integrity.py` — 40 hermetic tests. (All units)

### Modified (production)

- `db/schema.sql` — appended `operator_audit_events` DDL mirror. (M7-04)
- `chatbotv2/dashboard/routes/messages.py` — intent/enqueue/failure audit +
  actor keys on P1/P2 payloads. Responses unchanged. (M7-03/04, B2/B5)
- `chatbotv2/dashboard/routes/queue.py` — edit/approve/reject/failure/
  enqueue/cancel-raced audits + actor on P4/P5. 409/422/404 semantics
  unchanged. (M7-03/04/05, B3/B5)
- `chatbotv2/dashboard/routes/vault.py` — ownership check, snapshot hash,
  actor, intent/enqueue/failure audits; truthful `delete_media`; delivery
  record audit. M6 dedup string unchanged. (M7-03/04/10, B2/B8)
- `chatbotv2/dashboard/routes/lab.py` — authority pin, scoped persona reads,
  foreign-persona 404, turn bound + flag, audit. Pipeline untouched.
  (M7-09, B7)
- `chatbotv2/dashboard/routes/pages.py` — creator scoping for queue/users/
  chats/chat/embed reads. (M7-12, B6)
- `chatbotv2/dashboard/routes/ai_intel.py` — creator scoping (4 queries),
  fail-closed empty card. (M7-12, B6)
- `chatbotv2/dashboard/routes/dialogs.py`, `users.py` — unsegmented lists
  scoped via activity EXISTS, fail-closed 503. (M7-12, B6)
- `chatbotv2/dashboard/routes/search.py` — creator-threaded message search,
  fail-closed; fixed latent `msg_result` NameError on skip paths. (M7-12, B6)
- `chatbotv2/dashboard/routes/followups.py` — cancel/cancel-raced audit with
  human actor. (M7-06, B4)
- `workers/send_worker.py` — B3 re-read/suppress/content-dedup/actor/audits.
  (M7-01/02/03/04/05)
- `workers/scheduler_worker.py` — B4 lifecycle/suppress/cancel-race/actor/
  audits; no new status; stable identities kept. (M7-03/04/06)
- `chatbotv2/main.py` — B2 success/failure audits (additive); B8 creator on
  all finalize/release calls. Send/DLQ/event logic untouched. (M7-04/11/14)
- `db/postgres.py` — `get_queue_item_for_send`, `mark_scheduled_suppressed`,
  `get_scheduled_status`; draft/creation audits; `actor_*` params on
  `create_scheduled_message`; optional creator on analytics/timeline/search;
  deprecation warnings on legacy unscoped queue branches. (B1/B3/B4/B6)
- `db/vault.py` — optional creator scoping on finalize/release (+ warnings).
  (M7-11)
- `db/redis.py` — repair-path finalize passes creator (1 line). (M7-11)
- `vault/service.py` — creator threading on finalize/release. (M7-11)
- `segments/fields.py` — explicit creator binding for message/queue/
  purchase/vault/commerce generators. (M7-12, B6)
- `commerce/re_engagement.py`, `commerce/post_purchase.py`, `core/llm_tools.py`
  — scheduling-origin actors; post_purchase scoped release calls. No commerce
  semantics changed. (M7-06/11, B4/B8)

### Modified (tests, intended contract updates only)

- `tests/test_m6_operator_integrity.py` — flush dedup shape for edited bytes;
  re-read mock for flush test. M6 intent preserved, extended for B3.
- `tests/test_first_message_identity_correlation.py` — return-shape + audit
  statement selection (2 tests).
- `tests/test_scheduled_messages.py` — ineligible → suppressed (1 test).
- `tests/test_vault.py`, `tests/test_vault_concurrency.py`,
  `tests/test_post_purchase_delivery.py` — creator-scoped finalize/release
  assertions (1 each).
- `tests/test_phase46_integration.py` — return-shape assertion (1 test).
- `tests/test_segment_integration.py` — resolver mocks for unsegmented lists
  (2 tests).
- `tests/test_search.py` — resolver mocks + creator assertion (5 tests).
- `tests/test_forensic_remediation.py` — scoped-form blacklist assertion
  (1 test).

---

## 4. B1–B8 implementation status

### B1 — Audit event contract: DONE (migration pending apply)

- `core/audit.py`: `EVENT_TYPES` (intent/enqueue/claim/dequeue/attempt/
  success/failure/retry/cancel/reject/edit/approve/stale_suppressed/
  cancel_raced/suppress/recover/actor_spoof_attempt), `ACTOR_TYPES`
  (human/scheduler/ai/worker/system/migration/unknown),
  `build_audit_event` (validates, hashes, never stores raw text),
  `record_audit`/`record_audit_event` (best-effort, never raises).
- Compatibility: additive only; no existing call modified except to emit.
- Transactions: writer runs post-commit, outside business transactions
  (one exception: `create_scheduled_message` emits inside its insert txn on a
  separate pooled connection so a rollback also drops the audit — intended).
- Redis: none created for the writer.
- Migration: `20260922000000_m7_operator_audit.sql` + schema mirror.
  **Applied to local dev on 2026-09-16** (explicit approval; §12 record
  below). Writer verified end-to-end against dev (insert → hash-only
  read-back → delete). On databases where it is not yet applied the writer
  degrades to structured-log + `False` (previously verified live).
- Rollback: delete call sites or leave writer failing-closed-safe.

### B5 — Actor identity: DONE

- Trust boundaries: dashboard (`actor_from_auth`), workers
  (`actor_worker`/`actor_scheduler`), scheduling origins (explicit per
  caller), AI (`actor_ai`), system, migration, `unknown` with reason.
- Stream keys `actor_type`/`actor_id` are additive; business logic never
  reads them; `strip_spoofed_actor` available at consumer boundaries.
- `resolved_by` retained everywhere. No roles, no auth changes.
- Rollback: strip keys + calls; payloads remain valid downstream.

### B3 — Queue lifecycle + race: DONE

- Re-read: `get_queue_item_for_send(id, creator)` (pending-only) immediately
  before XADD; mismatch → `stale_suppressed` + skip; missing → skip as
  resolved-elsewhere. Fail-closed on re-read error.
- Dedup: edited → `queue_item:{id}:{sha16(content)}` (dashboard parity);
  unedited → legacy `queue_item:{id}`. `process_approved_message` returns
  `{"ok", "dedup_id"}` (extended shape; updated assertions only).
- At-least-once preserved: suppression applies solely to proven-stale
  snapshots; identical retries share identity.
- Rollback: revert flush to snapshot path (race reopens; audits stop).

### B4(a–c) — Scheduler: DONE (no migration)

- No new status: suppression = `completed` + `last_error='suppressed:…'`
  (`mark_scheduled_suppressed`, guarded `WHERE status='processing'`).
- Cancel-race: `get_scheduled_status` check + terminal-mark result check →
  `cancel_raced` audit, send preserved, counted once.
- Stable `scheduled:{key}:{id}` identities untouched; recovery path audited.
- Rollback: revert to silent `mark_scheduled_enqueued` for ineligible.

### B2 — Direct sends: DONE

- Intent before attempt, enqueue after success, failure on exception;
  consumer links `telegram_message_id` on success, durable failure on
  `send_failed` paths. No validation/rate-limit/response changes.

### B8 — Vault: DONE (policy item recorded)

- Ownership: `vault_svc.get_media(creator, media)` pre-check; mismatch →
  404/400 fail-closed; mirror-miss → enqueue + `asset_unverified` audit
  (availability preserved, authority unchanged).
- Snapshot: additive `asset_hash`/`asset_verified` payload keys; M6
  `md5(user:media:caption)` dedup byte-identical.
- Guards: `(id, creator_id)` finalize/release; callers updated; legacy
  id-only warns.
- Rollback: remove pre-check (open) or keep check and drop audit.

### B7 — Lab: DONE

- Authority pin (single-creator match else integration-exists), scoped
  persona reads (3 sites), up-front foreign-persona 404, audit, turn cap 10.
- Rollback: remove pin (restores client-selected creator).

### B6 — Scope hardening: DONE (batches, exceptions documented)

- Scoped: pages, ai_intel, dialogs/users unsegmented, search messages,
  analytics stats/timeline (optional param; rank global by design),
  segment generators (message/queue + purchase/vault/commerce binding fix).
- Quarantined (warn, not remove): id-only queue/vault branches.
- Retained globals (section 10): settings stats, users-table rows,
  notes/tags/attention/segments-user-fields, ranking.

### B4(d) — Dedup migration: ANALYSIS ONLY (section 12)

---

## 5. Actor model

| actor_type | Derived from | Carried through |
|---|---|---|
| human | `actor_from_auth` (session username) at dashboard trust boundary | route → stream keys → consumer audit; `resolved_by` retained |
| worker | worker id (`send_worker`, `bot_main` paths) | `process_approved_message` args → payload → audit |
| scheduler | scheduler worker id | payload → audit; creation origins (`reengagement`) |
| ai | `llm_worker` draft creation; `llm-tool` scheduling | `add_to_operator_queue` audit; `create_scheduled_message` kwarg |
| system | post-purchase/commerce flows | creation kwarg; consumer fallback |
| migration | reserved (backfills) | contract-level |
| unknown | missing/invalid/legacy with reason (`missing-auth`, `flush-unattributed`, `no-actor-on-stream`, `unspecified`) | never fabricated; explicit |

Client-supplied `actor_type`/`actor_id` keys are never read for attribution;
`strip_spoofed_actor` removes them at consumer boundaries (worker-tested).

---

## 6. Audit event contract

Table `operator_audit_events` (migration pending apply): `audit_id` (uuid),
`event_type`, `actor_type`, `actor_id`, `creator_id`, `user_id`, `chat_id`,
`action` (source route/subsystem), `content_hash`, `content_hash_original`,
`content_hash_final` (SHA-256, never raw text), `asset_kind`, `asset_id`,
`asset_hash`, `generation_id`, `dedup_id`, `queue_id`, `schedule_id`,
`state_before/after`, `result`, `error`, `attempt`, `lease_token`,
`telegram_message_id`, `created_at`. Indexes: (creator,created),
(creator,queue), (creator,schedule), (creator,generation).

Lifecycle coverage: draft `intent` → `edit` → `enqueue` → `claim`/`dequeue` →
`attempt` → `success` (`telegram_message_id` set) / `failure` (`error`) /
`retry` (stable identity) / `cancel` / `reject` / `approve` /
`stale_suppressed` / `cancel_raced` / `suppress` / `recover`.

Privacy: hashes only in durable rows; Pub/Sub payloads unchanged (no raw
content added). Failure isolation: writer catches everything, returns bool;
business paths never branch on it (one deliberate exception: flush *skips*
on re-read infrastructure error — fail-closed for integrity, documented).

---

## 7. Queue race model

Before: `SELECT pending list → (5s window) → XADD snapshot → UPDATE approved`.
A concurrent dashboard edit persisted + enqueued under a *different* dedup
(`:{sha16}`) → both delivered; or flush won and the edit 409'd.

After: `SELECT pending list → … → SELECT … WHERE id AND creator AND
status='pending' (re-read) → compare bytes+edited → on divergence:
stale_suppressed + skip → XADD authoritative bytes → UPDATE approved →
approve audit`. The residual window is re-read→XADD (RTT scale, separate
systems) — narrowed, observable via divergent dedups, and no longer
*knowingly* enqueues stale bytes. Exactly-once is NOT claimed; at-least-once
for fresh rows is preserved (identical retries share identity at both layers).

---

## 8. Scheduler state machine

`pending →(SKIP LOCKED claim)→ processing → enqueue → completed`
(success) / `failed` (`creator_context_unavailable`, `enqueue_error`,
recovery-exhausted) / `completed` + `last_error='suppressed:…'` (ineligible —
previously silent) / `cancelled` (pending-only cancel; processing cancel
rejected 409 and, if observed post-claim, audited `cancel`). Crash between
enqueue and mark → recovery requeues under the stable
`scheduled:{key}:{id}` identity; terminal-mark mismatch → `cancel_raced`
audit with send preserved. No new CHECK state; dashboard consumers unaffected
(verified: stats/count filter the five states only).

---

## 9. Vault integrity

Creator binding: resolution authority unchanged (Fangate active[0]); sends
add a mirror ownership pre-check. Canonical binding: `asset_hash` over
canonical ids + caption, carried additively; sent bytes unchanged from M6
(client caption/URL) to avoid behavior drift. Dedup: M6 stream string
unchanged; vault `UNIQUE(creator,user,media)` unchanged. Finalize/release:
`(id, creator_id)` scoped with all internal callers passing creator.
`delete_media` now returns 502 on provider failure instead of false success.

---

## 10. Creator isolation

Hardened (creator predicate added / threaded): pages queue/users/chats/chat/
embed; ai_intel (4); dialogs/users unsegmented; search messages; analytics
stats/timeline (optional); segment message/queue/purchase/vault/commerce
generators; queue + vault mutations (scoped branches + warnings).

Explicitly retained globals (structural, no creator column): `users` rows
(name/display only — message/queue content always scoped around them);
`user_profiles` outer row (inner-JSON per-creator + FOR UPDATE — M4);
notes/tags/attention reads+writes; `settings` stats; analytics rank;
segment user/tag/attention fields; worker-global claim/recover/reaper
(worker-only, never request-reachable); NULL-creator legacy rows (fail-closed
out of scoped reads).

---

## 11. Verification-required items

1. Operator Telegram-bot handlers — **confirmed N/A**: no such module in the
   production tree (only `recovery_llm_worker` references); queue routes are
   the authority. Affects: B3 (no extra caller to thread). Evidence: repo
   grep. No further action.
2. `execute_sealed_offer` — **confirmed**: `sealed:{offer.id}` dedup,
   sealed-facts-verbatim content, scope checks, reserve/release without
   premature confirm. Untouched. No action.
3. `original_draft` population — **confirmed**: defaults to `draft_content`;
   history partially recoverable + B1 hashes. No action.
4. Scheduled creation — **confirmed**: 3 internal UTC-aware callers; no
   edit/reschedule API exists. Freshness policy deferred (out of scope).
5. `mark_aftercare_completed` — **confirmed idempotent** (conditional
   transition). Safe under retry. No action.
6. DLQ replay — **confirmed**: `replay_count` bound, per-entry lock,
   reason-aware routing. No action.
7. Messages partial-unique — **confirmed** from live DDL intent
   (`UNIQUE(creator_id,dedup_id) WHERE dedup_id IS NOT NULL`; NULLs never
   conflict). No action.
8. Vault creator divergence — **confirmed, authority unchanged**; ownership
   check added instead of re-resolution. No further action.
9. Privacy — **confirmed**: existing contract stores message content in DB;
   audit stores hashes only (stricter). No new Pub/Sub content. No action.
10. Dashboard consumers — **confirmed**: status-only filtering → no new state
    introduced; settings stats intentionally global. No action.
11. Browser polling — **N/A**: no event names/scopes/timing changed. No action.
12. Scheduled helper callers — **confirmed**: followups passes creator to
    all (list/count/detail/cancel). No action.
13. **Edited-caption-after-terminal-delivery policy** — recorded, needs
    product approval: current (preserved) behavior suppresses via vault
    idempotency (`UNIQUE(creator,user,media)`); stream identity would allow
    redelivery. M7 did not choose; recommend explicit decision before any
    change. Affects: B8 follow-up.
14. Production `scheduled_messages` collision population for B4(d) —
    **unknown** (dev table empty; no prod access). Required pre-migration
    check is part of the plan (section 12).

---

## 12. Migration plan

### B1 audit table — APPLIED to local dev 2026-09-16 (was: migration written, not applied)

Apply record (`python -m db.migrate upgrade` against local dev
`127.0.0.1:5432/postgres`): all 7 pending applied in order, each logged
OK — `20260917000000` (233ms), `20260917010000` (57ms), `20260918000000`
(45ms), `20260919000000` (379ms), `20260920000000` (23ms),
`20260921000000` (7ms), `20260922000000_m7_operator_audit` (33ms).
Post-apply `migrate status`: 35 applied, 0 pending, up-to-date.
Pre-apply safety: all 7 files reviewed (additive `IF NOT EXISTS` only, no
DROP/DELETE/TRUNCATE/backfill); FK targets (`creators`, `users`,
`commerce_offers`) verified present read-only; row-count snapshot
before == after (messages 90, operator_queue 29, scheduled_messages 0,
users 7, commerce_offers 0, vault_media_deliveries 0) — zero data change.
New objects confirmed present (audit table + 4 indexes, 3 commerce tables,
8 added columns). Writer verified end-to-end (marked insert → hash-only
read-back → delete; table left at 0 rows after removing 1 test-generated
row). Staging/production application still requires its own approval.

- File: `db/migrations/20260922000000_m7_operator_audit.sql` (+ schema mirror).
- Risk: none (new table, IF NOT EXISTS, no backfill, no code dependency —
  writer degrades gracefully, verified live against dev DB).
- Apply: standard migration run on dev/staging/prod whenever approved; no
  ordering constraints; rollback = DROP TABLE (audit-only loss).

### B4(d) scheduled dedup scoping (PLAN ONLY — do not execute yet)

- Current (verified live): `UNIQUE (dedup_key) WHERE status IN
  ('pending','processing')` (`idx_scheduled_messages_dedup`).
- Desired: `UNIQUE (creator_id, dedup_key) WHERE status IN
  ('pending','processing') AND creator_id IS NOT NULL`.
- Collision analysis: dev table EMPTY (0 rows, verified read-only
  2026-09-16); cross-creator active-key collisions: none (vacuous);
  NULL-creator rows: none in dev. **Production population unknown** —
  mandatory pre-flight:
  `SELECT dedup_key, COUNT(DISTINCT creator_id) … WHERE status IN
  ('pending','processing') GROUP BY dedup_key HAVING COUNT(*) > 1;`
  and `SELECT COUNT(*) … WHERE creator_id IS NULL AND status IN (…)`.
- Backfill: none if pre-flight clean; if dirty, resolve duplicates manually
  (cancel/rename superseded keys) before index swap. NULL-active rows (if
  any): backfill creator from recipient activity or cancel them; the
  proposed partial predicate leaves NULL rows unscoped (documented legacy
  behavior) — alternatively add `NOT NULL` (bigger change, not recommended
  in this pass).
- Index strategy (online, no locking writes):
  1. `CREATE UNIQUE INDEX CONCURRENTLY idx_scheduled_messages_creator_dedup
     ON scheduled_messages (creator_id, dedup_key) WHERE status IN
     ('pending','processing') AND creator_id IS NOT NULL;`
  2. Deploy code with scoped gate (`WHERE dedup_key=$1 AND creator_id=$2
     AND status …`) — keep old index for rollback.
  3. Validate (duplicate-key errors absent, suppression behavior unchanged).
  4. `DROP INDEX CONCURRENTLY idx_scheduled_messages_dedup;`
  (`CONCURRENTLY` cannot run inside a migration transaction — execute as
  separate ops, not as one migration file.)
- App compat window: between (1) and (2), writes use the global gate (safe,
  stricter); between (2) and (4), both indexes enforced (a cross-creator
  duplicate fails on the old index — fail-closed, acceptable briefly).
- Rollback: drop new index, keep/re-create old; no data migration involved.
- Forward recovery: re-CREATE CONCURRENTLY on failure; idempotent.

---

## 13. Tests

New: `tests/test_m7_audit_integrity.py` — 40 hermetic tests (contract 8,
actors 5, flush race 7, scheduler 5, direct-send 2, vault 4, lab 2, scope 7).

Modified (intended contract updates, each documented in §3): M6 (2),
first_message_identity (2), scheduled_messages eligibility (1), vault (1),
vault_concurrency (1), post_purchase_delivery (1), phase46 (1),
segment_integration (2), search (5 + helper), forensic_remediation (1).

Focused commands + results (exact):

- `pytest tests/test_m7_audit_integrity.py` → **40 passed**.
- `pytest tests/test_m6_operator_integrity.py` → **21 passed**.
- `pytest tests/test_m7… test_m6… test_scheduler_p23 test_segments
  test_search test_segment_integration test_vault test_vault_analytics
  test_post_purchase_delivery test_phase10_lifecycle
  test_p34_reengagement_convergence` → **354 passed**.
- `pytest tests/test_scheduled_messages tests/test_followups_dashboard
  tests/test_vault_concurrency tests/test_vault_reservation_recovery
  test_forensic_remediation::…blacklisted tests/test_realtime
  tests/test_first_message_identity_correlation` → **217 passed, 11 failed**
  (all 11 = pre-existing stale drift: creator-less scheduled inserts,
  P1.3a-era).
- `pytest tests/test_vault tests/test_scheduled_messages tests/test_realtime`
  → **141 passed, 10 failed** (identical failure set to the Stage A
  baseline — no M7 delta).
- `pytest tests/test_m4_creator_isolation tests/test_p13a_creator_isolation`
  (+ forensic subset) → **89 passed**, 3 failed (1 Redis-env, 1 since-fixed
  source-shape, 1 pending-migrations env).
- `pytest tests/test_p1_lock_contention.py` → **8 passed** (271s, slow
  pre-existing).
- `pytest tests/test_vault_concurrency.py` alone → **29 passed** after fix
  (1 file-combined flake noted below).

---

## 14. Regression ledger

| Suite | Result | Classification |
|---|---|---|
| M7 focused (new) | 40 passed | M7 |
| M6 baseline | 21 passed | M7-compatible (2 assertions extended for B3) |
| First-message identity | passed (in 206-batch) | M7-compatible (2 assertions extended) |
| Scheduler p23 / segments / search / segment-integration / vault / vault-analytics / post-purchase-delivery / phase10 / p34-reengagement | 354 passed | M7-compatible (resolver mocks + creator assertions added) |
| Vault concurrency / reservation / realtime / followups / forensic-shape | passed except ledger items | M7-compatible (scoped-call assertions added) |
| `test_scheduled_messages` (10) + `test_followups…without_creator` (1) | failed | **Pre-existing stale drift** (P1.3a-era creator-less calls; identical Stage A set) |
| `test_worker_commerce_integration` (bulk) | failed | **Pre-existing drift** (missing `llm_worker.resolve_and_run_commerce`; stale commerce-import guards; files untouched by M7) |
| `test_llm_tools` product-offer (3), `test_e2e_p34` (9) | failed | **Pre-existing drift** (Opportunity-Engine gating; unmigrated dev DB — missing `generation_id` column, FK violations; unmocked resolver) |
| `test_conversations_analytics` (15) | failed | **Pre-existing** (untouched notes/tags/attention routes: `scope` expectations + closed-loop DB in env) |
| `test_phase46_integration` (9 of 10 in subset) | failed | **Pre-existing env/drift** (closed-loop creator enumeration; unmocked llm resolver; stale xautoclaim shape) |
| `test_send_loop_peer42_fix` (11) | failed | **Pre-existing drift** (payloads lack `creator_id` vs P1.4 fail-closed gate; gate untouched by M7) |
| `test_forensic_remediation` blacklist roundtrip / migrations-applied | roundtrip: **environment** (no Redis); migrations: **now passing on dev** (was environment — 6 pre-existing + 1 new pending, all applied 2026-09-16) |
| `test_redis_dedup_catches_before_reservation` (1 combo run) | failed once, passes alone + file-only | **Flaky/order-dependent** (under investigation; not reproduced in isolation) |
| p1 lock contention | 8 passed / very slow | Environment (slow pre-existing) |

No pre-existing failure was relabeled as M7-caused; every M7-touched
assertion is listed in §3 with its intended contract change.

---

## 15. M2–M6 regression status

- M2 (creator fencing/debounce/locks): untouched; p1 locks green.
- M3 (model-visible dedup boundary): untouched; identity tests green.
- M4 (isolation/atomic profiles/scoped persona): untouched code; m4/p13a
  isolation suites green (89 passed modulo env items).
- M5 (freshness/high-water): untouched; no contrary evidence.
- M6 (edited-bytes-sent, manual attribution, content-bound dedup,
  truthful queue state, vault/caption dedup compat, AI-reply identity,
  WS fail-closed filtering, commerce authority): **behavior extended, not
  weakened** — dashboard edited path byte-identical; flush dedup extended
  per B3 spec (legacy alias kept for unedited); sealed-offer path untouched
  (verified); commerce authority diff = zero (only actor kwargs + scoped
  release calls); WS events unchanged (names/semantics/timing/scope).
- At-least-once delivery: preserved everywhere (suppression applies only to
  proven-stale snapshots; recovery paths keep stable identities).

---

## 16. Remaining risks

1. Post-re-read flush race (re-read→XADD RTT): narrowed, not eliminated —
   separate PG/Redis systems admit no atomic cross-system commit without a
   protocol change (out of scope).
2. Audit durability was migration-gated: **resolved on local dev**
   (migration applied + writer verified end-to-end 2026-09-16);
   staging/production application still pending approval. No backfill of
   pre-migration lifecycle history is planned.
3. Shared-password operator identity remains self-asserted; audit now
   records it honestly but cannot authenticate it (roles deferred).
4. Scheduled dedup stays global until B4(d) executes (plan ready, §12).
5. Edited-caption-after-terminal-delivery stays suppressed (idempotency
   wins); needs product approval to change ( §11 item 13).
6. Legacy id-only branches (queue/vault) still callable internally (warn);
   removal deferred.
7. Dev DB was unmigrated at Stage B start (now resolved — 35/35 applied);
   staging/prod still pending. No Redis in this environment — several suites
   only pass with fakes or live infra; e2e coverage remains limited
   accordingly. The B4(d) dedup index was re-verified UNCHANGED after the
   migration apply (still global `idx_scheduled_messages_dedup`).
8. One combo-run flake (`test_redis_dedup_catches_before_reservation`)
   did not reproduce in isolation; monitor.

---

## 17. Final verdict

`M7 STAGE B PARTIAL`

Blocking items for COMPLETE: (a) apply the migrations on staging/production
(dev done and verified 2026-09-16); (b) product approval on the
edited-caption-after-terminal policy (§11.13); (c) execute (or formally
defer with sign-off) the B4(d) migration per §12; (d) confirm the
pre-existing/environmental suite gaps in §14 are accepted as non-M7.
All non-migration Stage B code, tests, and acceptance criteria in §17 of the
task brief are otherwise met: actor identity on every in-scope path, spoof
rejection, direct/queue/scheduler/vault/lab lifecycle audit records,
authoritative edited bytes, stale-snapshot suppression with hermetic
coverage, truthful approval attribution, explicit cancel races, stable
retry identity, unambiguous terminal semantics without new states,
creator-bound vault payloads, creator-safe lab, hardened residual reads,
green M4–M6 invariants, no destructive migration, isolated B4(d).
