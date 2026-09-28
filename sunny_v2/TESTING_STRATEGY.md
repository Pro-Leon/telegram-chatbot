# Sunny V2 — Testing Strategy (FUTURE)

No test suites were added in cutover. Each implementation phase must add its
layer before proceeding.

```text
unit               pure logic: routing, eligibility mapping, assembly budgets
integration        V2 + Postgres/Redis/delivery with real infra
contract           V2↔commerce request/confirmation semantics; event schemas
state-machine      valid/invalid transitions, guards, terminal/recovery states
event              producer/consumer wiring, dedupe, redelivery
memory             extraction/validation/consolidation/contradiction/temporal queries
retrieval          relevance quality vs exact-topic baselines
concurrency        lock contention, stale writes, race suppression
idempotency        duplicate inbound/generation/send/commerce/attribution
LLM-output validation   rails rejection classes, adversarial cases
commerce-boundary  forbidden-mutation probes (direct writes/calls rejected)
end-to-end         inbound → validated outbound with commerce confirmation
failure injection  DB/LLM/commerce/queue/provider outages per FAILURE_MODES.md
regression         V1-stays-disabled + commerce-stays-authoritative guards
```

Exit criteria per phase live in `PHASED_IMPLEMENTATION_PLAN.md`. A phase is
done only when its tests pass and the final audit confirms no orphan/dead
code, no V1 dependency, and no commerce leakage.
