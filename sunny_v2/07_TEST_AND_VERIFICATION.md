# Sunny V2 — Test and Verification Standard

## Core rule

No phase advances without evidence.

Required loop:

**AUDIT → IMPLEMENT → TEST → VERIFY → AUDIT AGAIN → PROCEED**

## Test layers

### Unit tests

Every domain module must have unit tests.

Required areas:

- memory extraction
- memory contradiction
- memory retrieval
- episode classification
- relationship scoring
- thread management
- strategy selection
- commerce adapter
- snapshot construction
- idempotency
- validation

### Integration tests

Required flows:

1. new fan
2. returning fan
3. multi-day relationship
4. topic callback
5. changed personal fact
6. intimate thread continuation
7. negative engagement learning
8. purchase
9. post-purchase
10. 30-day return
11. operator queue
12. retry
13. duplicate inbound
14. duplicate generation
15. commerce failure

### End-to-end tests

Test actual message flow:

```text
Telegram input
→ persistence
→ relationship snapshot
→ memory
→ strategy
→ LLM
→ validation
→ commerce boundary
→ routing
→ outbound
→ persistence
```

## Acceptance scenarios

### Scenario A — new fan

Fan:

> "Hey, just got off work."

Expected:

- relationship starts
- work is captured if sufficiently explicit
- response feels conversational
- no arbitrary sale

### Scenario B — recurring interest

Fan previously discussed football.

Days later:

> "hey"

Expected:

- system may naturally recall football if appropriate
- no forced callback every time

### Scenario C — changed fact

Old:

> "I work at a hotel."

Later:

> "Actually I left the hotel. I'm doing construction now."

Expected:

- hotel remains history
- construction becomes current
- current context uses construction

### Scenario D — intimate continuity

Prior conversation became intimate.

Fan returns:

> "miss me?"

Expected:

- relationship context recognizes prior intimacy
- system does not reset to generic introduction

### Scenario E — negative engagement

System asks about a topic three times and fan repeatedly changes subject.

Expected:

- negative signal increases
- future strategy reduces reliance on that topic
- it is not permanently banned without sufficient evidence

### Scenario F — buyer

Fan purchases content.

Expected:

- purchase event becomes an episode
- buyer context is available
- relationship continues
- future eligible opportunities remain available

### Scenario G — 30-day return

Fan returns after long absence.

Expected:

- identity retained
- important memories retained
- absence recognized
- response may naturally acknowledge return
- system does not behave like a new fan

## Invariants

Automated tests must enforce:

- one generation ID → one state application
- one inbound turn → one authoritative snapshot
- one turn cannot create duplicate outbound sends
- commerce authority cannot be bypassed
- stale memories cannot override current memories
- operator-edited messages are not treated as automatically generated
- creator identity is immutable
- fan memories cannot overwrite creator metadata
- failed generation cannot partially commit relationship state

## Verification report

Every phase must end with:

```text
PHASE:
SUBPHASE:

FILES CHANGED:
FILES ADDED:
FILES REMOVED:

TESTS RUN:
PASS:
FAIL:

INVARIANTS VERIFIED:
- ...

KNOWN LIMITATIONS:
- ...

AUDIT FINDINGS:
- ...

STATUS:
PASS / BLOCKED
```

A BLOCKED phase cannot be marked complete.
