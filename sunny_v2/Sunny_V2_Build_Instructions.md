# SUNNY V2 — OPENCODE MASTER BUILD INSTRUCTIONS

## Document Status

**Purpose:** Master execution instructions for OpenCode while building Sunny V2.

**Applies to:** Every phase, sub-phase, file, database migration, integration, test, and cutover step in the Sunny V2 implementation.

**Primary source documents inside this folder:**

```text
sunny_v2/
├── [architectural writeup]
├── [detailed phased implementation plan]
└── OPENCODE_BUILD_INSTRUCTIONS.md
```

OpenCode MUST treat the architectural writeup and detailed phased implementation plan as authoritative design documents.

This file defines **how OpenCode must execute those documents**.

---

# 1. NON-NEGOTIABLE EXECUTION RULE

OpenCode MUST NOT treat the phased plan as a checklist where it simply writes code phase after phase.

Every phase and every meaningful sub-phase follows this lifecycle:

```text
READ
  ↓
AUDIT
  ↓
PLAN
  ↓
IMPLEMENT
  ↓
VERIFY
  ↓
AUDIT AGAIN
  ↓
ACCEPT
  ↓
REPORT
  ↓
PROCEED
```

Never:

```text
Implement → assume it works → continue
```

Always:

```text
Audit → implement → verify → audit implementation → accept → continue
```

If verification fails, STOP.

Do not proceed to the next phase until the current phase is demonstrably complete.

---

# 2. SOURCE-OF-TRUTH HIERARCHY

Before doing any work, OpenCode MUST read:

1. This file.
2. The Sunny V2 architectural writeup.
3. The Sunny V2 detailed phased implementation plan.
4. The current codebase relevant to the phase.
5. Existing tests and runtime configuration relevant to the phase.

The architectural writeup defines:

```text
WHAT the architecture is.
```

The phased plan defines:

```text
WHAT must be built and in what order.
```

This file defines:

```text
HOW OpenCode must build it.
```

The existing codebase defines:

```text
WHAT actually exists today.
```

The existing codebase must never silently override the V2 architecture merely because legacy code is easier to reuse.

If the implementation documents and existing code disagree:

```text
STOP
AUDIT
DOCUMENT THE CONFLICT
DO NOT GUESS
```

Then determine whether the difference is:

- expected legacy behavior
- an architectural constraint
- an undocumented dependency
- a missing implementation detail
- a contradiction in the design documents

Do not silently resolve architectural contradictions.

---

# 3. GREENFIELD RULE

Sunny V2 is a new functional architecture.

It is NOT:

```text
Legacy system + more fields
Legacy memory + better retrieval
Legacy profile + more tables
Legacy trajectory + additional scores
Legacy ConversationState made persistent
Legacy LTM renamed
```

Do not turn existing systems into V2 merely because they contain useful code.

Existing code may be reused only when the reuse is explicitly compatible with the V2 ownership model.

For every reused legacy component, OpenCode MUST document:

```text
Component:
Why it is reused:
What V2 depends on:
What V2 does NOT inherit:
Why reuse does not compromise the V2 architecture:
```

If that cannot be demonstrated, build a V2-native component instead.

---

# 4. NO LOOSE CODE POLICY

"Loose code" is prohibited.

The following are not acceptable as completed implementation:

```python
pass
```

```python
TODO
```

```python
FIXME
```

```python
raise NotImplementedError
```

placeholder return values

fake repositories

fake persistence

mock implementations accidentally used in production

silent exception swallowing

empty service methods

unused speculative abstractions

dead interfaces created "for later"

unwired classes

unreachable production code

functions that always return a hardcoded value

comments describing functionality that does not exist

temporary compatibility code with no removal condition

"we can finish this later"

"implementation omitted"

"stub for future phase"
```

If a phase requires functionality, implement the functionality.

If a dependency is genuinely required before the functionality can work:

```text
STOP
identify dependency
build dependency
verify dependency
resume
```

Do not hide incomplete work behind an abstraction.

---

# 5. NO SPECULATIVE ENGINEERING

Do not build features simply because they might be useful later.

Do not introduce:

- unnecessary frameworks
- unnecessary queues
- unnecessary databases
- unnecessary vector databases
- unnecessary services
- unnecessary microservices
- unnecessary event buses
- unnecessary abstractions
- unnecessary configuration layers

Every new dependency must have a demonstrated requirement.

For every significant new dependency:

```text
Why is it required?
Why cannot existing infrastructure satisfy the requirement?
What operational cost does it introduce?
What failure modes does it introduce?
```

If the answer is weak, do not introduce it.

---

# 6. SINGLE-CREATOR SCOPE

Sunny V2 is currently a single-creator system.

Do not introduce multi-tenant or multi-creator architecture unless explicitly required by the architectural documents.

However, preserve existing creator scoping where the current runtime requires it.

Do not accidentally remove existing isolation guarantees.

---

# 7. EXISTING SYSTEM PROTECTION

While V2 is being built, protect the existing working system.

The following areas are considered protected unless a phase explicitly says otherwise:

```text
Telegram / Telethon transport
Redis Streams
LLM provider / llama.cpp
OneCall contract
LLM validation
commerce engine
commerce transaction sealing
purchase execution
vault/content delivery
operator dashboard
operator queue
Telegram sending
existing retry infrastructure
existing idempotency infrastructure
existing scheduler infrastructure
```

Do not refactor these systems merely for style.

Do not rewrite large sections of `workers/llm_worker.py` simply because V2 is being integrated.

Create clean integration boundaries.

---

# 8. COMMERCE IS A HARD BOUNDARY

Commerce remains authoritative.

V2 may understand commercial relationship context.

V2 may NOT become authoritative for:

```text
product
price
availability
ownership
purchase
purchase completion
offer eligibility
transaction execution
PPV sealing
refund truth
```

The LLM may propose commercial intent.

It cannot authorize commerce.

Relationship V2 may say:

```text
"this is a long-term buyer who has previously responded well to offers"
```

Commerce decides:

```text
what product exists
what price applies
whether the fan owns it
whether the fan is eligible
whether an offer can be presented
whether a purchase actually occurred
```

Any implementation violating this boundary MUST be rejected.

---

# 9. RELATIONSHIP V2 OWNERSHIP

V2 owns:

```text
person understanding
relationship understanding
semantic memory
historical memory
episodic memory
open loops
interaction patterns
negative interaction evidence
intimate history
relationship continuity
absence/return understanding
episode state
proactive recall
relationship context
```

The LLM does not directly own any of these.

The LLM can produce observations.

V2 validates and persists them.

---

# 10. LLM AUTHORITY RULE

The LLM is an interpreter/generator.

It is not the database.

It cannot directly decide:

```text
"this is definitely true"
```

and write that fact directly into durable state.

Instead:

```text
LLM observation
      ↓
validation
      ↓
confidence evaluation
      ↓
contradiction evaluation
      ↓
persistence policy
      ↓
V2 state
```

Likewise:

```text
LLM commerce signal
      ↓
commerce validation
      ↓
deterministic commerce system
```

---

# 11. REALITY EVENT RULE

The most important event rule:

> A generated response is not a conversational event.

This distinction must exist throughout the implementation.

These are NOT equivalent:

```text
ResponseGenerated
ResponseSent
```

Only the actual sent response can mutate permanent conversational relationship state.

Example:

```text
Draft A
    ↓
operator rejects
```

must result in:

```text
NO relationship event
NO memory mutation
NO relationship progression
NO intimacy history mutation
NO interaction learning
```

Then:

```text
Draft B
    ↓
operator edits
    ↓
B sent
```

the edited/sent message becomes the actual conversational event.

---

# 12. EVERY PHASE STARTS WITH AN AUDIT

Before implementing a phase, OpenCode MUST perform a phase audit.

The audit must answer:

```text
1. What does the architecture require?
2. What does the phased plan require?
3. What currently exists?
4. What files are involved?
5. What modules depend on those files?
6. What database schema exists?
7. What runtime paths are affected?
8. What legacy behavior is nearby?
9. What existing tests cover the area?
10. What hidden coupling exists?
11. What can safely be changed?
12. What must remain untouched?
13. What new files should be created?
14. What existing files genuinely require integration?
15. What acceptance criteria define completion?
```

The audit MUST happen before implementation.

---

# 13. PHASE AUDIT OUTPUT

At the beginning of every phase, produce:

```text
=== PHASE AUDIT ===

Phase:
Sub-phase:

Architecture requirements:

Phased-plan requirements:

Current implementation:

Relevant files:

Relevant functions/classes:

Relevant database objects:

Relevant runtime paths:

Legacy dependencies:

Protected components:

New V2 components required:

Integration points:

Risks:

Potential regressions:

Tests currently available:

Missing tests:

Implementation boundary:

Acceptance criteria:
```

Do not begin coding until this audit is understood.

---

# 14. PLAN BEFORE CODE

After the audit, produce a concise implementation plan.

It must specify:

```text
Files to create
Files to modify
Files explicitly not to modify
Database changes
Runtime changes
Tests to create/update
Integration points
Rollback considerations
Verification commands
```

Do not make changes outside this scope without re-auditing.

---

# 15. SCOPE LOCK

Once implementation begins, OpenCode must maintain a scope lock.

If an unrelated issue is discovered:

```text
DO NOT silently fix it.
```

Instead classify it:

```text
BLOCKER
REQUIRED FOR CURRENT PHASE
IMPORTANT BUT OUT OF SCOPE
COSMETIC
TECHNICAL DEBT
```

Only blockers and current-phase requirements may be fixed.

Other issues go into:

```text
OPEN CONSIDERATIONS
```

This prevents V2 implementation from turning into an uncontrolled repository rewrite.

---

# 16. IMPLEMENTATION RULE

Implement in small vertical slices.

Prefer:

```text
domain model
→ repository
→ service
→ persistence
→ test
→ integration
→ integration test
```

over:

```text
create 50 files
→ wire everything later
```

Every new component should become functional as soon as its dependencies exist.

---

# 17. DOMAIN-FIRST RULE

For each V2 feature:

1. Define domain semantics.
2. Define invariants.
3. Define persistence model.
4. Define service behavior.
5. Implement.
6. Test.
7. Integrate.

Do not begin with prompts.

Do not begin with UI.

Do not begin with retrieval hacks.

The domain must exist independently of the LLM.

---

# 18. DATABASE RULES

Database changes must be:

- explicit
- versioned
- reversible where practical
- tested
- idempotent
- compatible with deployment order

Never modify production schema manually without a migration.

Every migration must have:

```text
up migration
down/rollback strategy where supported
schema verification
migration test
```

Do not create duplicate representations of the same V2 concept without an explicit reason.

---

# 19. DATA INTEGRITY RULE

Persistent relationship data must have:

```text
provenance
timestamps
relationship ownership
source event/message where applicable
confidence where applicable
status where applicable
versioning/supersession where applicable
```

Do not create durable facts with no way to determine where they came from.

---

# 20. MEMORY INTEGRITY

Every durable memory should be explainable.

The system should eventually answer:

```text
Why does Sunny believe this?
```

with a chain such as:

```text
Current memory
    ↓
source event
    ↓
source message
    ↓
timestamp
```

If a proposed memory cannot be safely traced, its persistence policy must be reconsidered.

---

# 21. CONTRADICTION RULE

Never destroy historical truth merely because a newer fact exists.

When:

```text
old fact
```

is contradicted by:

```text
new fact
```

V2 should preserve both with status:

```text
old = SUPERSEDED/HISTORICAL
new = ACTIVE/CURRENT
```

unless the evidence indicates the old fact was simply erroneous.

Do not implement "latest value wins" as the sole model.

---

# 22. EVENT IMMUTABILITY

Once a relationship event has been accepted as real:

```text
do not edit it
```

If correction is necessary:

```text
create corrective event
```

This preserves history.

---

# 23. IDEMPOTENCY RULE

Every event-processing operation must be safe to execute more than once.

Test:

```text
event processed once
event processed twice
event processed after worker retry
event processed after crash
event processed concurrently
```

Expected result:

```text
one logical state transition
```

---

# 24. CONCURRENCY RULE

Relationship state is shared mutable state.

Every operation affecting the same fan must consider:

```text
concurrent inbound messages
concurrent response generation
operator approval
operator edits
send
relationship event processing
commerce events
retries
```

Do not assume Redis serialization alone makes database updates safe.

Use appropriate:

```text
locks
transactions
optimistic versioning
unique constraints
idempotency keys
```

where required.

---

# 25. TEST-FIRST ACCEPTANCE

Every phase must add or update tests.

No phase is complete because:

```text
the code imports
```

or:

```text
the server starts
```

A phase is complete when its behavioral requirements are tested.

---

# 26. TEST LAYERS

Use multiple levels.

## Unit tests

Test:

```text
domain rules
memory rules
contradictions
ranking
state transitions
validation
```

## Repository tests

Test:

```text
database persistence
transactions
constraints
queries
supersession
event storage
```

## Service tests

Test:

```text
event processing
memory extraction
relationship reasoning
episode management
open loops
pattern learning
```

## Integration tests

Test:

```text
Telegram/event adapter
Redis
database
commerce adapter
LLM context
operator workflow
```

## End-to-end tests

Test real workflows:

```text
fan message
→ V2
→ LLM
→ operator/auto route
→ send
→ ResponseSent
→ V2 mutation
```

---

# 27. VERIFICATION COMMANDS

Every phase must define exact verification commands.

Examples:

```bash
pytest
```

or targeted:

```bash
pytest tests/relationship_v2/
```

plus:

```bash
python -m compileall relationship_v2
```

plus project-specific:

```bash
ruff check ...
mypy ...
```

where those tools exist in the repository.

Do not assume a tool exists.

Audit the repository first.

---

# 28. STATIC VERIFICATION

After implementation, inspect:

```text
imports
unused code
dead code
circular dependencies
typing errors
lint errors
migration consistency
exception paths
transaction boundaries
```

Do not rely exclusively on tests.

---

# 29. RUNTIME VERIFICATION

Where applicable, verify:

```text
service startup
worker startup
database connection
migration execution
Redis connection
event consumption
event persistence
LLM context construction
operator path
send path
```

Use realistic test data.

---

# 30. POST-IMPLEMENTATION AUDIT

After verification, perform a second audit.

This is mandatory.

Ask:

```text
Did implementation actually satisfy the architecture?

Did it accidentally reuse legacy authority?

Did it create a second source of truth?

Did it introduce hidden coupling?

Can rejected drafts mutate state?

Can the LLM bypass validation?

Can commerce facts be invented?

Can duplicate events mutate state twice?

Can concurrent events corrupt state?

Are all database writes transactional where required?

Are tests testing behavior rather than implementation details?

Are there TODOs/stubs/placeholders?

Are there dead files?

Are there unused abstractions?

Is every new file actually wired into production behavior?
```

Only after this audit passes can the phase be accepted.

---

# 31. PHASE ACCEPTANCE GATE

A phase is accepted only when:

```text
[ ] Architecture requirements satisfied
[ ] Phased-plan requirements satisfied
[ ] Implementation scope respected
[ ] Domain invariants implemented
[ ] Database changes verified
[ ] Unit tests pass
[ ] Integration tests pass where applicable
[ ] Runtime verification passes where applicable
[ ] Idempotency verified where applicable
[ ] Concurrency verified where applicable
[ ] No loose code
[ ] No unexplained TODO/FIXME
[ ] No production stubs
[ ] No accidental legacy authority
[ ] No duplicate source of truth
[ ] Commerce boundary preserved
[ ] Operator semantics preserved
[ ] Observability adequate
[ ] Post-implementation audit passed
```

If any critical item fails:

```text
PHASE = NOT COMPLETE
```

Do not proceed.

---

# 32. MANDATORY STOP CONDITIONS

OpenCode MUST stop and ask for clarification or resolve the dependency before proceeding if:

- architectural documents contradict each other
- required database schema is unknown
- required runtime behavior cannot be established
- an existing dependency has undocumented authority over V2
- a phase would require changing protected commerce behavior
- a migration could cause data loss
- a proposed change affects production sending semantics unexpectedly
- a test exposes an unresolved correctness issue
- a concurrency issue cannot be proven safe
- a feature cannot be implemented without violating V2 ownership
- the repository contains a missing dependency that prevents reliable verification
- the implementation would require guessing

Never silently guess.

---

# 33. DO NOT MASK FAILURES

Never do this:

```python
try:
    ...
except Exception:
    return None
```

unless the failure policy explicitly requires that behavior.

Never turn a correctness failure into:

```text
best effort
```

without architectural approval.

Errors should be:

```text
classified
logged
observable
handled intentionally
```

---

# 34. NO SILENT FALLBACKS

Do not silently fall back from:

```text
V2
→ legacy
```

because V2 failed.

A fallback can create invisible architectural corruption.

If fallback is required during shadow/cutover:

```text
explicit feature flag
explicit metric
explicit log
explicit reason
```

The fallback must be visible.

---

# 35. LEGACY SYSTEM RULE

Legacy systems may coexist during development.

They may NOT silently remain authoritative after V2 cutover.

For every legacy system, explicitly classify:

```text
KEEP
REUSE
ISOLATE
DISABLE
REPLACE
REMOVE
```

The classification must be documented.

---

# 36. LEGACY READ/WRITE AUDIT

Before disabling a legacy system:

Search the entire repository for:

```text
imports
function calls
database reads
database writes
Redis keys
event handlers
scheduled jobs
API endpoints
tests
configuration flags
```

Do not assume a component is unused because it is not referenced from the main path.

---

# 37. COMMERCE REGRESSION GATE

Before any relationship cutover, verify commerce independently.

At minimum:

```text
product lookup
price lookup
ownership
eligibility
offer creation
offer sealing
purchase execution
purchase completion
post-purchase handling
refund handling
```

must continue working.

If commerce regresses:

```text
V2 cutover = BLOCKED
```

---

# 38. OPERATOR REGRESSION GATE

Verify:

```text
draft generation
queue insertion
rejection
regeneration
editing
approval
send
retry
failed send
```

Then verify:

```text
rejected draft does not mutate V2
edited sent draft does mutate V2
```

This is mandatory.

---

# 39. PHASE EXECUTION TEMPLATE

For every phase, OpenCode should use this internal process.

## Step A — Read

Read:

```text
OPENCODE_BUILD_INSTRUCTIONS.md
architecture document
phased plan
relevant code
relevant tests
```

## Step B — Audit

Produce:

```text
=== PHASE AUDIT ===
```

## Step C — Plan

Produce:

```text
=== IMPLEMENTATION PLAN ===
```

## Step D — Implement

Implement only the scoped work.

## Step E — Verify

Run:

```text
unit tests
integration tests
static checks
runtime checks
migration checks
```

as applicable.

## Step F — Audit Again

Produce:

```text
=== POST-IMPLEMENTATION AUDIT ===
```

## Step G — Acceptance

Produce:

```text
=== PHASE ACCEPTANCE ===
```

with:

```text
PASS
```

or:

```text
BLOCKED
```

## Step H — Report

Produce the standard sync-back report.

## Step I — Continue

Only if:

```text
PHASE ACCEPTANCE = PASS
```

---

# 40. STANDARD PHASE REPORT

Every completed phase must end with:

```text
=== SYNC-BACK REPORT ===

Phase:
Status:

Files Created:
- ...

Files Modified:
- ...

Database Changes:
- ...

Core Changes:
- ...

Tests Added:
- ...

Tests Run:
- ...

Verification:
- ...

Architecture Compliance:
- ...

Legacy Systems Affected:
- ...

Commerce Impact:
- None / Description

Operator Flow Impact:
- None / Description

Known Risks:
- ...

Open Considerations:
- ...

Next Phase:
- ...
```

Do not claim a test passed unless it was actually executed.

Do not claim a file was modified unless it was actually modified.

---

# 41. CONTINUATION PROTOCOL

Whenever OpenCode is asked to continue work, it MUST begin by reading:

```text
1. OPENCODE_BUILD_INSTRUCTIONS.md
2. architecture writeup
3. detailed phased implementation plan
4. latest SYNC-BACK REPORT
5. current repository state
```

Then explicitly determine:

```text
What phase am I in?

What sub-phase am I in?

What has already been completed?

What acceptance criteria already passed?

What remains?

What changed since the last report?

Are there regressions?

```

Do not restart completed work.

Do not assume previous work was correct.

Audit it.

---

# 42. MANDATORY CONTINUATION INSTRUCTION

At the beginning of every continuation session, OpenCode should internally follow this rule:

> "Before making any changes, read the Sunny V2 architectural writeup, read the detailed phased implementation plan, read the Sunny V2 OpenCode build instructions, inspect the current implementation and latest sync-back report, audit the current phase against all three documents, identify exactly what is complete and what remains, then implement only the next verified scope. Do not proceed to another phase until the current phase passes its audit, implementation verification, post-implementation audit, and acceptance gate."

---

# 43. PHASE DEPENDENCY RULE

Do not skip foundational phases because a later feature appears easy.

For example:

Do not implement:

```text
proactive recall
```

before:

```text
semantic memory
events
importance
provenance
relationship context
```

Do not implement:

```text
LLM relationship prompting
```

before:

```text
relationship context
```

Do not implement:

```text
legacy shutdown
```

before:

```text
V2 replacement path
shadow validation
cutover verification
```

Dependencies must be respected.

---

# 44. VERTICAL FUNCTIONALITY RULE

A phase should result in real working behavior whenever possible.

Avoid building a giant disconnected framework.

For example, when implementing semantic memory:

Bad:

```text
20 classes
0 database writes
0 integration
0 tests
```

Good:

```text
memory domain
+
repository
+
database migration
+
validation
+
persistence
+
retrieval
+
tests
+
observable behavior
```

The goal is a functional system, not a collection of files.

---

# 45. DATA-FIRST VALIDATION

Before trusting an abstraction, test actual data.

Use fixtures representing:

```text
new fan
long-term fan
contradictory facts
important life event
open loop
negative preference
intimate history
long absence
repeat buyer
operator edited response
```

The architecture should be validated against realistic scenarios.

---

# 46. GOLDEN FAN FIXTURES

Create deterministic test fixtures for representative fan histories.

At minimum:

```text
Fixture A — New Fan

Fixture B — Long-Term Relationship

Fixture C — Contradictory Biography

Fixture D — Important Life Event

Fixture E — Open Loop

Fixture F — Learned Positive Preference

Fixture G — Learned Negative Preference

Fixture H — Intimate Continuity

Fixture I — Long Absence

Fixture J — Repeat Buyer

Fixture K — Operator Rejection + Regeneration

Fixture L — Operator Edit

Fixture M — Concurrent Messages
```

These fixtures should be reused across phases.

This prevents each phase from inventing unrelated test data.

---

# 47. PROPERTY-STYLE INVARIANTS

Where practical, test invariants such as:

```text
A rejected response cannot become a sent-response event.

Processing the same event twice cannot create two logical relationship transitions.

Superseding a memory cannot erase historical provenance.

A commerce purchase cannot be created by relationship memory.

An LLM cannot directly authorize a transaction.

A fan relationship cannot disappear because an episode ends.

Ending an episode cannot delete relationship memories.

A long absence cannot create a new person identity.

A new current fact cannot erase historical facts.

A failed send cannot be treated as a successful conversational event.
```

These are architectural invariants.

---

# 48. OBSERVABILITY REQUIREMENT

Every major V2 operation should be diagnosable.

At minimum capture:

```text
relationship_id
fan_id
event_id
episode_id
generation_id
source_message_id
operation
result
duration
error
```

For memory:

```text
memory_id
memory_type
source_event_id
confidence
status
```

For patterns:

```text
pattern_id
pattern_type
evidence_event_id
confidence
```

Do not log sensitive message contents unnecessarily.

---

# 49. SECURITY AND PRIVACY

Relationship data contains sensitive personal and intimate information.

Do not expose it unnecessarily.

Follow existing application security controls.

Do not:

- print entire relationship records to normal logs
- log full intimate histories
- expose raw personal information in generic exceptions
- include unnecessary message content in telemetry
- copy production personal data into source-controlled fixtures

Use sanitized fixtures.

---

# 50. PERFORMANCE RULE

Do not optimize prematurely.

First establish:

```text
correctness
consistency
traceability
functional behavior
```

Then measure.

Important future measurements:

```text
relationship context generation latency
memory retrieval latency
database query count
LLM context size
event processing latency
Redis latency
per-fan lock contention
```

Do not introduce caching until profiling demonstrates a need.

---

# 51. FAILURE RECOVERY

Every phase that introduces persistence must consider:

```text
worker crash
database transaction rollback
Redis retry
duplicate event
partial failure
network failure
LLM failure
operator action during processing
send failure
```

The expected state after recovery must be defined.

---

# 52. TRANSACTION RULE

When multiple durable changes represent one logical event, determine whether they must occur in the same transaction.

Example:

```text
ResponseSent
+
episode update
+
relationship timestamp
+
memory update
+
processed-event marker
```

must not accidentally produce an impossible partial state.

Use database transactions where required.

---

# 53. EVENT ORDERING

Do not assume event arrival order is always perfect.

Events may be:

```text
delayed
retried
duplicated
processed concurrently
```

Where ordering matters, use:

```text
timestamps
sequence numbers
generation IDs
versions
event ordering rules
```

as appropriate.

Do not simply trust worker execution order.

---

# 54. CONFIGURATION RULE

Every V2 feature flag must have:

```text
purpose
default
allowed values
activation behavior
rollback behavior
```

Do not create flags with ambiguous names.

Prefer explicit names:

```text
RELATIONSHIP_V2_ENABLED
RELATIONSHIP_V2_READ_ENABLED
RELATIONSHIP_V2_WRITE_ENABLED
RELATIONSHIP_V2_SHADOW_MODE
```

Avoid:

```text
NEW_MODE
V2
USE_NEW
TEMP
DEBUG2
```

---

# 55. MIGRATION RULE

Never perform destructive migration as part of normal implementation.

First:

```text
create V2
populate V2
verify V2
shadow V2
cut over V2
disable legacy writes
verify
disable legacy reads
```

Only much later consider legacy cleanup.

---

# 56. NO PREMATURE LEGACY DELETION

Do not delete legacy files merely because V2 exists.

First establish:

```text
no runtime reads
no runtime writes
no scheduled jobs
no event consumers
no dashboard dependencies
no commerce dependencies
no tests requiring them
```

Then document the removal.

---

# 57. DATABASE PACKAGE WARNING

The prior audit identified a repository/database evidence gap around the `db/` package.

If this remains true during implementation:

```text
DO NOT INVENT THE DATABASE CONTRACT.
```

Inspect:

```text
imports
call sites
actual database schema
migration files
runtime configuration
database metadata
```

before implementing migrations.

If the actual schema cannot be verified:

```text
BLOCK the affected database phase.
```

Do not guess table names, columns, constraints, or transaction semantics.

---

# 58. EXISTING AUDIT MUST REMAIN THE BASELINE

The previously completed forensic audit identified major existing-system properties including:

```text
three parallel relationship systems
fragmented memory
weak proactive recall
latest-value contradiction handling
no semantic intimate history
no first-class episode model
partial commerce-scoped learning
absence handling based partly on transient/lifecycle logic
generated-but-unsent drafts mutating relationship state
cross-generation send-order risk
strong deterministic commerce authority
```

These findings must be treated as baseline constraints.

Do not accidentally rebuild these defects inside V2.

---

# 59. PHASE-SPECIFIC RULES

For every phase in the detailed implementation plan:

## Before Phase

```text
Read architecture.
Read phased plan.
Read this instruction.
Audit repository.
Audit dependencies.
Define acceptance criteria.
```

## During Phase

```text
Implement only scoped functionality.
Write tests.
Run tests continuously.
Do not leave stubs.
Do not silently expand scope.
```

## After Phase

```text
Run complete verification.
Audit implementation against architecture.
Audit legacy coupling.
Audit source-of-truth ownership.
Audit failure modes.
Run acceptance tests.
```

## Only Then

```text
Mark PASS.
Write SYNC-BACK REPORT.
Proceed to next phase.
```

---

# 60. IF A PHASE IS TOO LARGE

If a phase is too large to safely implement in one operation, split it into explicit sub-phases.

Example:

```text
Phase 5 — Semantic Memory

5.1 Domain model
5.2 Database
5.3 Repository
5.4 Validation
5.5 Persistence
5.6 Retrieval
5.7 Integration
5.8 Tests
5.9 Verification
```

Each sub-phase must follow:

```text
AUDIT
→ IMPLEMENT
→ VERIFY
→ POST-AUDIT
→ ACCEPT
```

Do not treat sub-phases as informal tasks.

---

# 61. SUB-PHASE COMPLETION

A parent phase cannot be marked complete while any child sub-phase is:

```text
PARTIAL
IN PROGRESS
BLOCKED
UNVERIFIED
```

All must be:

```text
PASS
```

---

# 62. QUALITY BAR

The objective is not:

```text
"the feature works once"
```

The objective is:

```text
correct
durable
testable
observable
idempotent
concurrency-safe
architecturally isolated
production-usable
```

A feature that works in a happy-path demo but fails on retry, duplicate events, operator edits, contradictions, or process restart is NOT complete.

---

# 63. FINAL CUTOVER REQUIREMENTS

Before V2 becomes the live relationship authority, OpenCode must produce a final audit covering:

```text
Architecture compliance

Domain ownership

Database integrity

Event integrity

Memory integrity

Contradiction handling

Episode continuity

Open loops

Interaction learning

Negative learning

Intimate continuity

Absence handling

Proactive recall

Commerce isolation

LLM authority boundaries

Operator semantics

Concurrency

Idempotency

Retry behavior

Failure recovery

Performance

Observability

Security/privacy

Legacy dependencies

Feature flags

Rollback
```

No final cutover without this audit.

---

# 64. FINAL CUTOVER CHECKLIST

```text
[ ] V2 relationship model is authoritative
[ ] V2 memory is authoritative
[ ] V2 episode model is authoritative
[ ] V2 proactive recall is live
[ ] V2 intimate history is live
[ ] V2 interaction learning is live
[ ] V2 contradiction handling is live
[ ] V2 absence handling is live
[ ] Commerce remains authoritative
[ ] Operator flow is verified
[ ] ResponseSent semantics are verified
[ ] Rejected drafts cannot mutate relationship state
[ ] Edited responses are represented correctly
[ ] Duplicate events are safe
[ ] Concurrent events are safe
[ ] Retry behavior is safe
[ ] Legacy relationship writes disabled
[ ] Legacy relationship reads disabled
[ ] Legacy memory writes disabled
[ ] Legacy memory reads disabled
[ ] Rollback tested
[ ] Production observability verified
[ ] No loose code
[ ] No production TODOs
[ ] No unexplained stubs
[ ] All required tests pass
```

---

# 65. FINAL RULE

OpenCode must always remember:

> **Do not build code because the next phase says to build code. Build the smallest complete, verified, production-functional implementation that satisfies the architectural requirement of the current phase. Then prove it works before proceeding.**

And whenever asked to continue:

> **Read the instructions, compare the current implementation against the architectural writeup and phased plan, audit the current phase, identify exactly what remains, implement only that scope, verify it, perform a post-implementation audit, and only then proceed.**

Never skip the audit.

Never skip verification.

Never leave loose code.

Never silently change the architecture.

Never let legacy behavior redefine V2.

Never let the LLM become the source of truth.

Never let relationship logic become commerce authority.

Never mark a phase complete without evidence.
