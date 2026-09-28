# Sunny V2 — Security and Privacy Requirements

## Identity isolation

Every query and mutation involving relationship data must be scoped by:

- creator ID
- fan ID

No global fan-memory query may accidentally mix creators.

## Creator identity

Creator identity must come from authoritative system configuration/persona data.

Fan memory must never redefine:

- creator name
- creator identity
- creator biography
- creator account identity
- creator-owned content metadata

## Memory poisoning resistance

Do not blindly persist model-generated claims.

Memory writes require:

- source evidence
- validation
- confidence
- provenance

Explicit fan statements receive stronger authority than inferred model assumptions.

## Prompt injection

Fan messages are untrusted input.

The system must treat:

- "forget your instructions"
- "you are now..."
- "tell me the creator's hidden information"
- requests for internal prompts
- requests for system state

as fan content, not system authority.

## Commerce safety

Never trust:

- price written by the fan
- product identity written by the fan
- generated purchase confirmation
- generated ownership claims

Use deterministic commerce state.

## Data minimization

Do not store raw intimate content merely because it exists.

Store relationship-relevant abstractions where possible.

## Auditability

Sensitive state changes should be traceable through generation ID and source evidence.

## Concurrency

Relationship mutations must be protected against:

- duplicate workers
- retries
- concurrent inbound turns
- stale snapshots

## Failure behavior

Security-sensitive uncertainty must fail closed.

A database/Redis/model failure must not silently grant:

- creator identity changes
- commerce authorization
- duplicate send permission
- purchase state
- ownership
