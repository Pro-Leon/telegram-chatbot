# Sunny V2 — OpenCode Build Instructions

## Mission

Build Sunny V2 as a robust, production-grade new functional architecture.

Do not optimize for speed of implementation.

Optimize for:

- correctness
- explicit contracts
- deterministic behavior
- testability
- observability
- maintainability
- clean boundaries
- zero loose code

## Mandatory instruction hierarchy

Before every implementation task, OpenCode MUST read:

1. architectural writeup
2. phased implementation plan
3. this document
4. relevant domain specification
5. existing audit findings
6. relevant source files
7. relevant tests

Do not rely on memory of previous phases.

## Mandatory workflow

For EVERY phase and subphase:

### Step 1 — AUDIT

Before editing:

- inspect current repository state
- inspect relevant modules
- inspect database schema
- inspect tests
- identify existing behavior
- identify dependencies
- identify conflicting legacy paths
- identify assumptions

Produce a short audit report.

### Step 2 — COMPARE

Compare the audit against:

- architectural writeup
- phased plan
- domain contracts
- current phase acceptance criteria

Explicitly identify:

- already satisfied requirements
- missing requirements
- contradictions
- risks
- required files

Do not implement until the comparison is complete.

### Step 3 — DESIGN

Define:

- module boundaries
- interfaces
- data structures
- persistence changes
- transaction boundaries
- failure behavior
- idempotency behavior
- tests

Prefer small cohesive modules.

### Step 4 — IMPLEMENT

Implement only the current phase/subphase.

Do not opportunistically refactor unrelated legacy code.

Do not create speculative abstractions.

Do not leave TODO implementations.

Do not silently weaken requirements to make tests pass.

### Step 5 — TEST

Run:

- targeted unit tests
- targeted integration tests
- type/static checks available in the repository
- lint/format checks
- relevant full-suite tests

Fix failures before continuing.

### Step 6 — VERIFY

Perform a second code audit after implementation.

Verify:

- actual execution path
- imports
- database queries
- transaction boundaries
- error handling
- concurrency
- idempotency
- logging
- tests
- legacy isolation
- commerce boundary

Do not infer correctness from test names alone.

### Step 7 — DOCUMENT

Update implementation notes and record:

- files added
- files modified
- files intentionally untouched
- tests
- verification results
- unresolved risks

### Step 8 — PROCEED

Only proceed when:

- acceptance criteria pass
- tests pass
- verification passes
- no critical unresolved issue remains

If blocked, stop and report the blocker.

## Anti-loose-code rules

Never:

- leave placeholder functions
- use `pass` for unfinished logic
- swallow exceptions without reason
- add broad `except Exception` without structured handling
- duplicate business logic
- copy-paste large blocks from legacy modules
- introduce untyped dictionaries where a domain object is required
- create dead compatibility wrappers
- add unused configuration
- add unused database columns
- add untested behavior
- suppress failing tests without documented justification

## Database rules

Any schema change must include:

- migration
- constraints
- indexes where justified
- rollback consideration
- repository/DAO implementation
- tests
- idempotency consideration

Never modify schema manually outside migrations.

## API/interface rules

Every cross-module boundary must have an explicit contract.

Prefer:

- typed models
- dataclasses
- Pydantic models where appropriate
- enums for finite state
- explicit result types

Avoid magic strings.

## Concurrency rules

For relationship state:

- define lock scope
- define transaction scope
- define versioning
- make writes idempotent

Test concurrent processing.

## LLM rules

The LLM is responsible for language generation and advisory interpretation.

It is NOT authoritative for:

- purchase status
- ownership
- price
- product availability
- creator identity
- transaction state

Prompt context must be generated from an authoritative V2 snapshot.

## Commerce rules

Never reimplement deterministic commerce logic inside V2.

Use an adapter.

If the commerce API does not provide a required fact, stop and identify the missing contract rather than guessing.

## Memory rules

Every memory feature must demonstrate:

```text
extract
→ validate
→ normalize
→ persist
→ retrieve
→ render
→ use
```

A write-only memory feature is incomplete.

## Relationship rules

Relationship state must be derived from evidence.

Do not make the LLM directly mutate relationship stage.

The LLM can propose observations.

The relationship engine validates and commits them.

## Testing requirements

Every new module requires tests.

Every new state transition requires tests.

Every new persistence rule requires tests.

Every new commerce boundary requires tests.

Every new memory retrieval behavior requires tests.

## Final phase gate

OpenCode must not begin the next phase until it can state:

```text
I audited the current repository.
I read the architecture.
I read the phased plan.
I read the relevant contracts.
I implemented only the current phase.
I ran the required tests.
I performed a post-implementation audit.
I verified the execution path.
I verified legacy isolation.
I verified commerce authority.
I documented remaining risks.
The phase acceptance criteria pass.
```

If any statement is false, do not proceed.
