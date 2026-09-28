# Sunny V2 — Definition of Done

A production-ready V2 implementation must satisfy all categories below.

## Architecture

- [ ] V2 is a new functional architecture
- [ ] legacy conversation architecture is not authoritative
- [ ] commerce remains authoritative
- [ ] explicit adapters exist at boundaries
- [ ] dependencies are documented

## Relationship

- [ ] persistent creator/fan relationship exists
- [ ] familiarity is durable
- [ ] engagement is durable
- [ ] reciprocity is durable
- [ ] continuity is durable
- [ ] intimacy trajectory is durable
- [ ] absence is tracked
- [ ] buyer status is integrated
- [ ] active threads persist

## Memory

- [ ] semantic memory exists
- [ ] episodic memory exists
- [ ] relationship summary exists
- [ ] current/history semantics exist
- [ ] contradictions are handled
- [ ] important memories survive long gaps
- [ ] proactive recall exists
- [ ] negative engagement is learned
- [ ] memory provenance exists
- [ ] memory retrieval is tested

## Conversation

- [ ] new fan conversation works
- [ ] returning fan conversation works
- [ ] multi-day continuity works
- [ ] intimate thread continuity works
- [ ] natural topic transitions work
- [ ] unnecessary questions are controlled
- [ ] repetitive behavior is controlled
- [ ] response length is controlled
- [ ] invented fan facts are blocked
- [ ] creator identity is protected

## Strategy

- [ ] RELATIONSHIP exists
- [ ] EXPLORE exists
- [ ] BUILD_DESIRE exists
- [ ] QUALIFY exists
- [ ] RECOMMEND exists
- [ ] PRESENT_OFFER exists
- [ ] AFTERCARE exists
- [ ] stages are strategic rather than rigid scripts

## Commerce

- [ ] deterministic commerce remains authoritative
- [ ] product state is authoritative
- [ ] price is authoritative
- [ ] ownership is authoritative
- [ ] purchase state is authoritative
- [ ] offer sealing remains authoritative
- [ ] transaction attribution remains authoritative
- [ ] delivery remains authoritative
- [ ] buyer context reaches V2

## Reliability

- [ ] generation IDs are idempotent
- [ ] relationship writes are idempotent
- [ ] duplicate sends are prevented
- [ ] concurrent turns are serialized appropriately
- [ ] failed turns cannot corrupt relationship state
- [ ] queue behavior is deterministic
- [ ] retry behavior is tested

## Observability

- [ ] every turn has a trace
- [ ] snapshot is reproducible
- [ ] memory selection is explainable
- [ ] strategy selection is explainable
- [ ] commerce interaction is traceable
- [ ] failures are diagnosable

## Security

- [ ] creator/fan isolation
- [ ] prompt injection resistance
- [ ] memory poisoning resistance
- [ ] commerce authority protection
- [ ] sensitive logging controls
- [ ] fail-closed authority boundaries

## Release gate

V2 is not production-ready until every required checkbox is satisfied or an explicit documented exception has been approved.
