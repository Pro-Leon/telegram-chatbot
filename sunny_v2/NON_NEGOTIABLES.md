# Sunny V2 — Non-Negotiables

The strongest architectural contract. Violations block implementation phases.

```text
1.  Sunny V2 is a new architecture. (FUTURE)
2.  V1 is frozen and disabled. (DISABLED — core/architecture_router.py)
3.  V1 must not silently become a dependency of V2.
4.  Commerce remains deterministic and authoritative. (PRESERVED)
5.  V2 cannot invent commerce truth: no products, prices, availability,
    purchases, or entitlements originate in V2.
6.  Relationship state is persistent, structured, and creator-scoped.
7.  Memory preserves history and current truth (temporal, non-destructive).
8.  Conversation context persists across sessions; re-entry resumes.
9.  Generated language cannot mutate authoritative state by itself.
10. All state mutation must pass through explicit application logic.
11. Every module must have a defined caller.
12. Every persistent field must have an owner.
13. Every event must have a producer and consumer.
14. Every queue must have a producer and consumer.
15. Every API must have an owner and contract.
16. Every implementation phase requires audit → implement → verify → audit.
17. No speculative infrastructure.
18. No dead code.
19. No silent fallbacks to V1.
20. No bypassing commerce.
```

## Operational meaning (CURRENT)

- Rules 2/19 are enforced today by `core/architecture_router.py` +
  `core/config.py sunny_v1_enabled=false` + gates in `chatbotv2/handlers.py`,
  `workers/llm_worker.py`, `workers/send_worker.py`.
- Rules 4/5/20 are enforced today by keeping `commerce/` untouched and
  documenting the contract in `COMMERCE_CONTRACT.md`.
- Rules 6–15 are design constraints on FUTURE V2 code; each implementation
  phase must name caller, owner, producer, and consumer before writing code.
- Rule 16 is enforced by `OPENCODE_BUILD_INSTRUCTIONS.md`.
