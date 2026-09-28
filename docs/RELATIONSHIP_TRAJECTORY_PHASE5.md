# Relationship Trajectory — Phase 5: Relationship-Aware Context Assembly

> Phase 5 adds a bounded deterministic relationship-context selection step
> between existing retrieval/memory sources and context assembly. It creates
> no new memory system, no new relationship system, and no new strategy
> system. Phases 1–4 contracts are unchanged.

## 1. Purpose

Allow the LLM to retain useful relationship-relevant historical context
even after the relevant interaction has fallen outside the recent-message
window — without dumping history, without a second retrieval engine, and
without giving the LLM authority over state.

A second, concrete goal: fix the Phase 4 production wiring gap. Strategy
was computed in `workers/llm_worker.py` but appended to the legacy
`context` list that the production OneCall path rebuilds from the
authoritative snapshot (ignoring that list), so strategy never reached
production prompts.

After Phase 5:

* relationship-aware context reaches the production OneCall path,
* strategy reaches the production OneCall path,
* the same selected relationship context supports strategy's
  `user_referenced_previous_context` evidence,
* legacy and OneCall use the same underlying selected context.

## 2. Context architecture

```text
CURRENT TURN (user message, once, dominant)
        ↓
Conversation State / Contract (once-derived, authoritative transient)
        ↓
Existing retrieval (LTM + fan knowledge, existing signatures/thresholds)
+ existing summary (already-fetched, never refetched here)
+ descriptive trajectory bands (Phase 1, read-only)
        ↓
ONE bounded deterministic selection
(context_engine/relationship_context.py)
        ↓
ONE bounded data-only representation (RELATIONSHIP CONTEXT [DERIVED])
        ↓
┌─ legacy ``context`` list (append; parity) ──────────────┐
└─ authoritative snapshot fields ─→ OneCall compact path ─┘
        ↓
Phase 4 strategy (same selection feeds referenced evidence)
        ↓
OneCall / legacy LLM (realization only)
```

## 3. Relationship state vs relationship context

`RELATIONSHIP STATE ≠ CONTEXT`. Bands (`familiarity`, `engagement`,
`reciprocity`, `continuity`, `trend`) are descriptive metadata: they say
*that* a relationship has depth/continuity, never *what* was said,
*which* thread is open, or *what* was promised. Phase 5 renders band
labels (known values only; `unknown` omitted) **plus** a small number of
supporting historical facts selected by current-turn overlap. Bands
alone never produce facts, and facts require genuine overlap — so a
"familiarity = established" label can never invent a callback.

## 4. Selection rules (`select_relationship_context`)

Pure, synchronous, deterministic, creator-scoped, fail-open. Inputs are
already-derived/current-turn data only:

* band labels (snapshot object or mapping; unknown omitted),
* current topic + open threads (tolerant dict/object reads),
* current message (relevance tokens only; never rendered),
* existing retrieved LTM / fan-knowledge rows (no new fetch inside the
  pure selector),
* existing summary string (first sentence only, ≤200 chars).

Per-candidate rules:

1. Normalize rows tolerantly (dict or object); drop rows without
   `subject`/`value`.
2. Drop `EXPIRED` rows and commerce/sexual token hits (exact-token deny
   lists; defense-in-depth — stores should never contain them).
3. Require token overlap (`[a-z0-9]+`) with
   topic ∪ threads ∪ message; overlap ≤ 0 is never selected.
4. Score `overlap * 0.5 + confidence * 0.3 (+ 0.3 open-loop/commitment
   boost when overlapping)`, mirroring the existing retrieval formula;
   keep `score > 0.2` (existing threshold language, no new scale).
5. Deterministic order: open-loop/commitment first, then overlap, then
   confidence, then lexical tie-breaks.
6. Top-k bound: `MAX_RELATIONSHIP_FACTS = 3` facts,
   `MAX_RELATIONSHIP_THREADS = 3` thread labels, 1 summary line.
7. Deduplicate on `subject.lower()=value.lower()` across LTM + fan
   knowledge (+ profile-derived rows where callers supply them).

`has_prior_context` is True only when ≥1 supporting fact was selected.
Threads or bands alone never set it.

## 5. Precedence

```text
CURRENT USER TURN
  > RECENT CONVERSATION
    > CURRENT CONVERSATION STATE / CONTRACT
      > RELEVANT HISTORICAL CONTEXT (this layer, advisory)
        > OLDER PROFILE / SUMMARY INFORMATION
```

Trajectory bands are metadata, not commands. Historical facts render as
data lines (`relevant_fact: subject=value (status)`), never as
instructions. No "you should / always / sell / ask / flirt / escalate"
language exists anywhere in the renderer (pinned by tests).

## 6. Token budget

No global budget constant changes. `TOTAL_CONTEXT_BUDGET` remains 2600;
per-category budgets are unchanged; the OneCall outer 8192 check is
unchanged. The rendered block enforces `MAX_RELATIONSHIP_TOKENS = 200`
via the existing `context_engine.budget.estimate_tokens` (no second
counter): on overflow it drops the summary first, then lowest-ranked
facts. Typical populated size is ~60–150 tokens. Recent conversation,
state, and system/persona remain protected by the existing CE ordering
and legacy tail-keep trims.

## 7. OneCall / legacy integration

* Legacy: worker appends `RELATIONSHIP CONTEXT` then `CONVERSATION
  STRATEGY` as trailing system blocks (after persona/state/content/
  memory/knowledge), before history — the documented Phase 5 ordering.
* OneCall: the worker attaches both texts to the frozen
  `AuthoritativeState` (`relationship_context_text`,
  `strategy_block_text`; empty = render nothing) via the same
  `object.__setattr__` pattern used for participants/contract.
  `core/context_compact.py::phase5_snapshot_blocks` appends them in all
  three `build_one_call_from_snapshot` branches after CE system blocks
  and grounding blocks, before labeled conversation. Abstention in
  either block keeps prompts byte-identical.
* The legacy `context` list argument to OneCall (`recent_messages=`)
  remains ignored on the authoritative branch by design; snapshot fields
  are the carrier — this is the actual Phase 4 wiring fix.

## 8. Strategy integration

Phase 4 is untouched (same hierarchy, same vocabulary, same priority).
The worker ORs the selector result into turn evidence:

```text
referenced = retrieval_backed_evidence OR selection.has_prior_context
```

`has_prior_context` requires overlapping supporting facts, so CALLBACK
stays conservative: familiarity alone, continuity alone, or a bare
thread never enables it. No new strategy branch, no strategy-owned
retrieval, no context selection depending on strategy branches
(selector stays upstream).

## 9. Creator isolation

All retrieval calls pass `(creator_id, user_id)`; the trajectory read
uses the creator-namespaced `relationship_trajectory_by_creator` block
only. `to_plain_dict` converts frozen snapshot mappings at the boundary
without weakening isolation. Cross-creator tests pin that creator A's
facts never appear in creator B's selection.

Known pre-existing limitation (flagged, not changed): flat profile
scalars (`name/age/location/…`) are keyed by `user_id` only, so one
creator's scalars can overwrite another's. Phase 5 does not read flat
scalars for selection and does not depend on them.

## 10. Fail-open behavior

Every Phase 5 operation fails open: missing creator → empty; retrieval
exception → `[]` per source; snapshot failure → bands omitted; selector
exception → empty context; render of empty/non-conforming input →
`""`; snapshot attach failure → logged, generation continues. Failures
never block generation, routing, handoff, send, or commerce authority.
Existing `logger.debug` conventions are used.

## 11. Non-goals

Phase 5 does not implement sexual/intimacy state, escalation,
permission, consent, refusal, boundary state, content-interest
transitions, commerce integration, purchase/product/pricing/offer
logic, readiness, desire, temperature, ranking, sealing, execution,
optimizer learning, new relationship dimensions, new tables, new Redis
structures, new vector stores, new LLM decision engines, new strategy
engines, LLM-controlled state/policy, telemetry overhaul, or historical
backfill.

## 12. Known limitations

* Supporting facts require lexical overlap with the current turn. A
  relevant fact phrased with entirely disjoint vocabulary will not be
  selected (same limitation as the underlying retrieval scorer).
* The summary line is first-sentence-only and LLM-derived; it is
  advisory and clearly labeled.
* The persona behavior block fix (single-arg render call) restores an
  intended ~60-token advisory block; its content rules are unchanged.
* Memory persistence call-site fixes restore intended writes; scoring
  and storage models are unchanged.
