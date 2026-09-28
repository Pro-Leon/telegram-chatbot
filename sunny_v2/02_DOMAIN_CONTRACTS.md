# Sunny V2 — Domain Contracts

## Purpose

This document defines the stable interfaces between V2 subsystems.

Contracts must be implemented explicitly rather than through implicit dictionaries shared between modules.

## Core entities

### FanIdentity

Represents the Telegram-side identity of a fan.

Required fields:

- `creator_id`
- `fan_id`
- Telegram identifiers required for messaging
- creation timestamp
- last-seen timestamp

Identity is never inferred from memory.

### RelationshipContext

The authoritative conversational snapshot for one fan.

It should contain:

- relationship stage
- familiarity
- engagement
- reciprocity
- continuity
- intimacy trajectory
- active conversation thread
- important semantic memories
- recent episodic memories
- open loops
- known preferences
- negative engagement signals
- purchase summary
- commerce state
- absence duration
- conversation strategy
- current objective
- constraints

This object is immutable once assembled for a generation.

### Episode

Represents a meaningful event in the relationship.

Minimum conceptual fields:

- episode ID
- creator ID
- fan ID
- timestamp
- episode type
- summary
- participants
- topic
- emotional/interaction signals
- source message IDs
- confidence
- importance
- current/history status where applicable

### SemanticMemory

Represents durable knowledge about the fan.

Examples:

- occupation
- hobbies
- family
- location
- preferences
- dislikes
- schedule
- recurring interests
- personality cues
- content preferences
- conversational preferences
- relationship-relevant details

### MemoryEvidence

Every extracted memory should retain provenance.

Minimum:

- source message/episode
- extraction method
- confidence
- timestamp
- generation ID
- whether explicitly stated or inferred

Inferred memories must never be rendered as explicit facts unless policy permits it and confidence is sufficient.

## ConversationPlan

The planner produces a strategy, not final prose.

It may specify:

- objective
- active thread
- response mode
- desired emotional direction
- whether to recall memory
- whether to ask a question
- whether to tease
- whether to continue intimacy
- whether a commerce opportunity may be evaluated
- prohibited transitions
- response length target

It must not fabricate commerce authority.

## CommerceDecision

The commerce adapter returns deterministic facts.

Conceptually:

- eligible opportunity
- product identity
- current price
- availability
- ownership
- active offer
- purchase status
- sealing state
- aftercare state

The relationship engine consumes these facts.

It does not mutate commerce state directly.

## ResponseEnvelope

Generation must return a structured object rather than an untyped string.

Minimum:

- `text`
- `generation_id`
- `strategy`
- `memory_references`
- `commerce_intent`
- `thread_continuity`
- `validation_flags`
- `confidence`
- `model_metadata`

## Persistence invariants

1. Creator and fan identity must be present for all relationship state.
2. Generation IDs must be idempotent.
3. Memory writes must be transactional where practical.
4. A failed generation must not partially advance relationship state.
5. A queued draft must not be treated as sent.
6. An edited operator response must be distinguished from the generated response.
7. Purchase state must never be inferred from generated text.
8. Commerce state must never be overwritten by conversational state.
