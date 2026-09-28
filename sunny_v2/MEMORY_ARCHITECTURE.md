# Sunny V2 — Memory Architecture (FUTURE)

Persistent relationship continuity, not a searchable transcript.

## Required coverage

```text
episodic memory
semantic memory
current facts
historical facts
preferences
likes/dislikes
conversation patterns
important life events
relationship milestones
interaction history
topics that engage the fan
topics that fail to engage the fan
long inactivity
previous conversations
important promises/commitments
```

## Retrieval

Hybrid lexical + semantic with importance, recency, and relevance scoring.
`CURRENT` context engine (`context_engine/`, lexical RapidFuzz + MiniLM
semantic) is `DEPRECATED` for V2 design reference only — V2 defines its own
retrieval behind the memory API. Retrieval must not depend exclusively on
exact topic matching.

## Lifecycle

- **Extraction** — deterministic + model-assisted candidates, always with
  provenance and confidence.
- **Validation** — contradiction checks against current facts and commerce
  context; conflicts create explicit records.
- **Persistence** — append-only episodes; facts supersede with temporal
  validity (`effective_from`, `previous_value`).
- **Consolidation** — periodic distillation into semantic memory with source
  links; never destructive.
- **Contradiction resolution** — newest verified source wins for "now";
  history preserved for "used to be".
- **Temporal validity** — every fact answers "true now?" and "true at T?".
- **Importance / relevance scoring** — salience, recency decay, engagement
  evidence, promise/commitment boosts.
- **Decay rules** — explicit, reviewable, non-deleting (salience fades, rows
  remain).
- **Correction** — fan corrections supersede with provenance; the error and
  fix both persist.
- **Provenance** — turn, extractor version, source spans on every row.
