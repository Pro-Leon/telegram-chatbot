# Sunny V2 — Engineering Principles

## 1. Build a new system

Sunny V2 must be implemented as a new functional architecture.

Do not incrementally reshape the legacy relationship/conversation architecture into V2.

Allowed:

- new modules
- new tables
- new services
- new interfaces
- new workers
- explicit adapters around existing commerce

Not allowed:

- silently changing legacy behavior to make V2 work
- copying legacy state machines and renaming them
- routing V2 through legacy conversation planners
- sharing mutable legacy relationship state as an implicit dependency

## 2. Commerce remains deterministic

The commerce system is authoritative for:

- products
- prices
- availability
- ownership
- offers
- purchase state
- PPV eligibility
- sealing
- transaction attribution
- post-purchase state
- delivery

The V2 relationship engine may identify conversational opportunities, but it does not authorize commerce.

The LLM must never become the source of truth for commerce.

## 3. Relationship context is persistent

A fan is represented as a persistent relationship context, not merely a conversation history.

The system should be capable of reconstructing a useful relationship snapshot such as:

> I've known this guy for a while. He's comfortable with me. We joke around. He likes X. He's responsive when I tease him. We haven't talked in three days.

This snapshot is derived from durable structured state and episodic/semantic memory.

## 4. Memory must be useful, not merely stored

A fact is not considered implemented simply because it is persisted.

Every important memory class must have:

- extraction
- normalization
- confidence
- provenance
- current/history semantics
- contradiction handling
- retrieval
- prompt/context rendering
- tests proving retrieval

## 5. Current facts replace current interpretation, not history

When a fan changes a fact:

- preserve the historical record
- make the newest valid fact current
- record when and why it changed
- prevent stale facts from being presented as current

Example:

> "I work nights now."

The system must preserve the earlier occupation/schedule as historical while making the new schedule current.

## 6. Episodic relationship memory is first-class

Sunny should remember meaningful events, not only isolated facts.

Examples:

- first conversation
- meaningful personal disclosure
- recurring joke
- shared topic
- flirt escalation
- successful teasing
- failed/ignored conversational approach
- promise or open loop
- important date
- purchase
- post-purchase interaction
- content preference
- boundaries
- periods of absence
- re-engagement after absence

## 7. Learn what does not work

The relationship model must capture negative conversational evidence.

Examples:

- question repeatedly ignored
- topic repeatedly abandoned
- teasing style receives weak response
- CTA ignored
- content type declined
- fan changes topic after a particular approach

Negative evidence must influence future strategy without being treated as absolute truth.

## 8. Preserve intimate continuity

When an intimate thread is active and the fan returns, V2 should normally preserve that thread unless:

- the fan changes topic
- a boundary requires transition
- safety requires transition
- the relationship context indicates the thread is no longer appropriate
- commerce/post-purchase state requires a different deterministic flow

The system must not arbitrarily reset to generic small talk.

## 9. Natural conversation before mechanical funneling

The intended progression is:

RELATIONSHIP
→ EXPLORE
→ BUILD_DESIRE
→ QUALIFY
→ RECOMMEND
→ PRESENT_OFFER
→ AFTERCARE

These are strategic modes, not mandatory scripted steps.

Conversation quality always matters. The engine should not force a stage transition merely because a timer or counter says it is due.

## 10. Buyer relationships are different

A purchaser is not reset to a generic fan.

Purchase history should strengthen relationship context.

A buyer may have:

- higher familiarity
- known preferences
- known content history
- prior successful offers
- post-purchase continuity
- stronger relationship context

Commerce opportunities remain deterministic, but V2 should not artificially suppress legitimate future opportunities merely because the fan has purchased before.

## 11. One inbound turn has one authoritative context snapshot

For every processed inbound message:

1. acquire the conversation/relationship lock
2. construct the authoritative snapshot
3. freeze the snapshot
4. generate using that snapshot
5. validate
6. persist resulting state
7. release/complete

Do not re-fetch mutable relationship state halfway through generation.

## 12. No loose code

Every new module must have:

- clear ownership
- typed interfaces
- deterministic behavior where appropriate
- error handling
- tests
- logging
- documented invariants
- explicit dependencies

No dead compatibility code should be introduced.

## 13. Fail closed for authority boundaries

Failures involving:

- commerce authorization
- purchase state
- creator identity
- safety boundaries
- message ownership
- duplicate-send prevention

must not silently authorize an action.

## 14. Fail open only where safe

Examples of potentially fail-open subsystems:

- analytics
- non-critical telemetry
- optional personalization ranking

But a fail-open decision must never turn an advisory subsystem into an authority.

## 15. Every phase must be auditable

OpenCode must produce evidence:

- commands run
- tests run
- important files changed
- invariants verified
- known limitations
- unresolved findings

before proceeding.
