# Sunny V2 — Documentation Index

## Purpose

This directory contains the normative engineering documentation for Sunny V2.

Sunny V2 is a **new functional relationship-conversation architecture**, not a refactor of the legacy conversation architecture. The existing deterministic commerce architecture remains an external authority and must be integrated through a clean boundary.

## Document precedence

When documents appear to conflict, use this order:

1. `sunny_upgrade_v2.md` — product and architectural intent
2. `Sunny_Relationship_V2_Phased_Plan.md` — implementation sequence
3. `00_DOCUMENT_INDEX.md` — document map and precedence rules
4. `01_ENGINEERING_PRINCIPLES.md` — non-negotiable engineering rules
5. `02_DOMAIN_CONTRACTS.md` — contracts and invariants
6. `03_DATA_MODEL.md` — persistence model
7. `04_STATE_MACHINE.md` — relationship/conversation state model
8. `05_MEMORY_SPEC.md` — episodic/semantic memory requirements
9. `06_COMMERCE_BOUNDARY.md` — commerce integration contract
10. `07_TEST_AND_VERIFICATION.md` — verification gates
11. `08_OBSERVABILITY.md` — telemetry and debugging
12. `09_SECURITY_AND_PRIVACY.md` — security requirements
13. `10_LEGACY_ISOLATION.md` — legacy architecture isolation
14. `11_OPENCODE_EXECUTION_RULES.md` — mandatory implementation workflow
15. `Sunny_V2_Build_Instructions.md` - Build instrucions

If a later document proposes implementation detail that violates an earlier normative requirement, the implementation detail is invalid.

## Required reading before implementation

OpenCode MUST read:

- the architectural writeup
- the phased implementation plan
- this index
- engineering principles
- domain contracts
- the phase-specific requirements
- all applicable tests/specifications

before changing code.

## Core rule

**Audit → design/check → implement → test → verify → document → proceed.**

No phase is considered complete because code was written. A phase is complete only when its acceptance criteria are demonstrably satisfied.
