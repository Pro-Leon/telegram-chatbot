# Handoff — Relationship V2 + Commerce Rebuild (continue in a new chat)

Date: 2026-09-28. Branch pushed: `origin/checkpoint/relationship-v2-staging`
(commit `dcf4759` + local follow-ups below — COMMIT BEFORE CONTINUING;
working tree currently has uncommitted work listed in §7).

## 1. Where we are

- **V2 unit suite: 338 green** (`tests/test_relationship_v2_*`, `test_db_*_contract`).
  Run: `python -m pytest tests/test_relationship_v2_minilm.py ... ` (full list
  in session history) or simply `python -m pytest tests/ -q -k "relationship_v2 or db_fangate or db_dropfans or db_offer"`.
- **Stages done**: A–F10 (flags → orchestrator halves → dossier), commerce
  Phase 1 (`db/fangate.py`, 26 fns), Phase 2 (`db/dropfans.py`, 20 fns),
  Phase 3a (offer_definitions 9 fns + drop_intents 6 fns + 3 dated migrations).
- **Live staging proofs passing** on local PG18.6 + Redis 7.2.5 + pgvector
  0.8.6 + MiniLM: F7 battery 8/8, DAO round-trips, all 3 commerce ports,
  semantic recall. Records: `sunny_v2/LIVE_PROOF_MATRIX.md` execution table.

## 2. Staging environment (local machine)

- `.env`: `POSTGRES_DSN=postgresql://postgres:***@127.0.0.1:5432/postgres`,
  `REDIS_URL=redis://127.0.0.1:6379`. Never commit `.env` (gitignored).
- Seeds: fans 9001 (5 inbound msgs), 9002 blocked, 9003 opted-out; F7 fans
  9010+. V2 writes use `RELATIONSHIP_V2_*` flags (default off).
- pgvector installed via elevated script (binaries remain in
  `C:\Users\User\AppData\Local\Temp\opencode\pgvector\`).
- Helper scripts (temp, NOT in repo): `apply_staging.py`,
  `verify_staging.py`, `live_proof_{l2,vec,minilm,f7,commerce}.py`,
  `dao_smoke.py`, `df_smoke.py`, `offer_smoke.py`, `fix_{products,wallet,offer}.py`,
  `check_{pg,redis,minilm,cons}.py`, `clean_df.py`, `enable_vector.py`, `pg_home.py`.
  Re-run any with `$env:PYTHONPATH='E:\chatbot'; python <script>`.
- MiniLM weights cached in HF cache; llama-server NOT running (port 8081
  empty last check). User chose sentence-transformers over llama.cpp/ollama.

## 3. Key design decisions (do not reverse casually)

- Legacy rows enter V2 as CANDIDATEs (candidate→superseded is invalid).
- Only `ResponseSent` mutates; drafts never do (`integration/operator_events`).
- `v2_engagement_signals` IS the pattern store (no duplicate table).
- Commerce truth never duplicated; V2 reads via `integration/commerce_ports`.
- Live products table uses `PRIMARY KEY (id)` (older fuller schema won over
  spec draft); CUID arbiter is `UNIQUE(creator_id, dropfans_product_id)`.
- `mark_event_processed` returns `(xmax = 0) AS inserted` — the concurrency
  linearization point (fixed a real double-APPLIED race found by F7).
- `db/*` DAO modules expose top-level `get_pool` (test patching compat).
- Outbound replay excluded (no send-confirmation evidence); extraction-over-
  replay deferred to stable-live pipeline.

## 4. Remaining work (in order)

1. **Commit** (§7 files) + push branch.
2. Remaining DAO: `db/vault.py` (~17 fns), `db/segments.py` (~9),
   `db/automation.py` (~12), `db/families.py` (7) + tables
   (vault_media_deliveries, fan_segments, automation_operations — families
   DDL already applied). Extraction reports are in session history.
3. `db/migrate.py` runner (manual `psql -f` until then).
4. F7 rest: L7 streams wiring, duplicate-send transport, failure injection,
   adversarial suites on staging.
5. F8/F10 execution (dry-run reports first), F11 shadow window, F12 cutover,
   F13 hardening, Part 3 re-audit (`DEFINITION_OF_DONE_GAP_CLOSURE_PLAN.md`
   Part 4 tracks status; `LIVE_PROOF_MATRIX.md` + `DEPENDENCY_REGISTER.md`
   in `sunny_v2/`).

## 5. Tooling gotchas (learned the hard way)

- PowerShell: NO `tail/grep/echo/&&`/`2>/dev/null`. Use `Select-Object`,
  `Select-String`, `;`. `curl.exe` needs no pipes to unix tools.
- No pytest-asyncio: async unit tests use a sync `_run()` helper with
  `asyncio.run` (see phase9/concurrency tests).
- The `edit` tool collapses lines when old/new strings share trailing
  context — ALWAYS `grep`/`read` to verify after editing `__init__.py`
  `__all__` blocks; prefer ruff (`RUF022/I001`) as the arbiter and apply
  its exact diffs.
- `ruff check` on `.sql` files = noise (parses as Python); exclude them.
- Temp scripts need `$env:PYTHONPATH='E:\chatbot'`.
- asyncpg cannot bind Python lists to `::vector` — stringify via
  `_to_vector_literal` (pattern in `relationship_v2/persistence/repository.py`).
- Live staging has residue from partial runs (old creators/products);
  clean with `clean_df.py`-style deletes before re-proving.

## 6. Docs map

- `sunny_v2/RELATIONSHIP_V2_REMEDIATION_PLAN.md` — Stages A–F roadmap.
- `sunny_v2/DEFINITION_OF_DONE_GAP_CLOSURE_PLAN.md` — gap register + F4–F13
  closure phases + Part 4 reconciliation (current status).
- `sunny_v2/LIVE_PROOF_MATRIX.md` — F7 scope + prerequisites + run records.
- `sunny_v2/DEPENDENCY_REGISTER.md` — reuse contracts (closes A4).
- `docs/COMMERCE_DB_REBUILD_SPEC.md` — commerce DAO rebuild spec.

## 7. Uncommitted work to commit first

`db/fangate.py` (get_pool re-export), `db/dropfans.py` (CUID fallback,
tx-id semantics, placeholder-key comment), `db/offer_definitions.py`,
`db/drop_intents.py`, `db/migrations/001_commerce_core.sql` (products PK
fix), `db/migrations/002_commerce_dropfans.sql`,
`db/migrations/20260915000000_p3_vault_index.sql`,
`db/migrations/20260916000000_p32_safety_foundation.sql` (+error col),
`db/migrations/20260917000000_p33_content_families.sql`,
`db/migrations/20260917010000_p33_offer_definitions.sql`,
`relationship_v2/persistence/{repository,schema}.py`,
`relationship_v2/services/{event_processor,turn_orchestrator,__init__}.py`,
`tests/test_db_offer_definitions_contract.py`,
`tests/test_db_dropfans_contract.py` (already committed? verify with
`git status --short`), `tests/test_relationship_v2_{concurrency,
turn_context,semantic,minilm}.py` (race-flag + determinism fixes),
`sunny_v2/LIVE_PROOF_MATRIX.md` (new run rows).
Verify: `git status --short`, review diff, `git add` the above,
commit, push branch.
