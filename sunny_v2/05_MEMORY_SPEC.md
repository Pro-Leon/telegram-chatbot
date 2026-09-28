# Sunny V2 — Relationship Episodic and Semantic Memory Specification

## Goal

Sunny should behave as though it has known the fan for a long time.

The memory system therefore has three layers:

1. semantic memory
2. episodic memory
3. derived relationship memory

## 1. Semantic memory

Store important stable or semi-stable information.

Categories:

- identity
- occupation
- location
- schedule
- hobbies
- interests
- preferences
- dislikes
- family
- pets
- relationships
- goals
- routines
- personality cues
- conversational preferences
- content preferences
- important dates
- recurring topics
- known jokes
- relationship-relevant details

## 2. Episodic memory

Store events with context.

Example:

> Fan returned after work and said he was exhausted. Conversation shifted into playful flirting. He responded positively to teasing about his long day.

This is more useful than storing only:

`occupation=office worker`

Episodes preserve why information matters.

## 3. Relationship memory

Derived summary should answer:

- Who is this person?
- How long have we known him?
- What does he like?
- What do we joke about?
- What topics engage him?
- What topics fail?
- How intimate is the relationship?
- What threads are open?
- What happened recently?
- What purchases occurred?
- What did he respond positively to?
- What should be avoided?
- How long has he been away?

The summary must be generated from structured evidence, not invented independently.

## Memory extraction

Every inbound turn should pass through:

1. explicit fact extraction
2. episodic event detection
3. preference detection
4. engagement signal detection
5. contradiction detection
6. importance classification
7. confidence assignment

## Current vs historical

When a fact changes:

Example:

Old:

`job = bartender`

New:

`job = electrician`

Persist:

- bartender → HISTORICAL
- electrician → CURRENT

Never render the old value as current.

## Intimate/sexual history

Meaningful intimate history becomes relationship memory.

Store abstract relational signals rather than unnecessary verbatim explicit content.

Examples:

- comfortable with sexual teasing
- responsive to playful teasing
- prefers slow escalation
- previously continued intimate thread
- content type previously purchased
- intimate topic that became a recurring joke

Memory must not automatically convert history into permission.

## Positive engagement

Record:

- topic
- behavior
- evidence
- recurrence
- confidence

Example:

`teasing_about_work = positive, confidence .82, observations 4`

## Negative engagement

Record what does not work.

Examples:

- ignores repeated questions about topic X
- abandons topic Y
- declines content type Z
- sales transition repeatedly produces disengagement

Negative signals must decay or be revised when later evidence contradicts them.

## Retrieval

Use a hybrid retrieval strategy:

### Tier 1 — always relevant

- relationship summary
- current relationship state
- active thread
- recent meaningful episodes
- purchase status
- current identity facts

### Tier 2 — context relevant

Retrieve memories related to:

- current topic
- active threads
- current emotional direction
- recent conversation

### Tier 3 — callback candidates

Select older high-importance memories that can naturally create continuity.

This tier is intentionally different from ordinary semantic similarity.

Example:

Current:

> "hey"

Possible callback candidate:

> Fan mentioned a job interview two weeks ago.

If the event is important and unresolved, the planner may choose to recall it.

## Proactive recall

Proactive recall should not require exact topic overlap.

A callback score can combine:

- importance
- recency
- unresolved status
- relationship relevance
- previous successful callback
- current conversational compatibility
- surprise/novelty
- negative callback history

The planner decides whether recall improves the conversation.

## Memory budget

Do not dump all memory into the prompt.

Build a compact `RelationshipContext` first.

The LLM receives selected evidence, not the entire database.

## Memory quality tests

Tests must prove:

1. important facts persist
2. changed facts become current
3. history remains available
4. contradictions are resolved
5. intimate continuity persists
6. negative engagement is learned
7. old memories can be proactively recalled
8. irrelevant memories are excluded
9. memory does not invent facts
10. creator identity cannot be overwritten by fan memory
