# Audit PHASE_07 — Documentation cleanup

- Dependency check: no Python module imports from `docs/*.md` (markdown only);
  dashboard/docs references are informational. No runtime, worker, scheduler,
  CI, migration, or env dependency on report contents found.
- Decision: `docs/*.md` classified ARCHIVE (historical forensic reference).
  No files moved or deleted — a bulk move of ~200 reports would endanger
  unrelated work and exceed the minimal-change mandate. `sunny_v2/` is
  declared the single authoritative current location; this record + README
  resolve any "current architecture" conflict.
- Root dev scripts and misc dirs marked UNKNOWN (require review), not
  removed, per the no-blind-delete rule.
- Pre-existing `sunny_v2/` docs kept; precedence documented in README and
  `00_DOCUMENT_INDEX.md` order.
