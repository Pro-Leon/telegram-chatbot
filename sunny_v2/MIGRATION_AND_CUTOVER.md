# Sunny V2 — Migration and Cutover

```text
V1 frozen                                          (DONE — no V1 logic edits)
↓
V1 disabled                                        (DONE — architecture router gates)
↓
commerce verified                                  (DONE — untouched + checks below)
↓
V2 specification complete                          (DONE — sunny_v2/ docs)
↓
V2 implementation                                  (FUTURE — phased plan)
↓
V2 isolated testing                                (FUTURE)
↓
shadow validation                                  (FUTURE — V2 observes, never sends/mutates)
↓
controlled activation                              (FUTURE — explicit flag, bounded scope)
↓
V1 remains quarantined                             (permanent until removal phase)
↓
V1 removal only after verified stability           (FUTURE — separate approval)
```

## Rules

- V1 removal is never a prerequisite for starting V2.
- Shadow validation must never send, create offers, or mutate state.
- Activation is explicit, scoped, reversible, and audited.
- Commerce behavior is verified at every step; any commerce regression
  blocks the migration.

## Commerce verification performed at cutover (CURRENT)

- `commerce/` tree present and unmodified (only V2 docs + router added).
- `integrations/dropfans/` + `integrations/fangate/` present.
- Delivery loop `chatbotv2/main.py` untouched; scheduler reconciliation
  entry `commerce/reconciliation.py reconcile_all` untouched.
- Config: `SUNNY_V1_ENABLED`/`SUNNY_V2_ENABLED` default false; no commerce
  env vars changed.
