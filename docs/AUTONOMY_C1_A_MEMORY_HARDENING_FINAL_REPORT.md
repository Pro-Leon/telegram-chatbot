# Phase C.1-A Memory Hardening — Final Report

## Before/After Architecture

### Before
```
format_profile: Python repr for lists/dicts
merge_profiles: Unbounded list growth, no confidence, no contradiction handling
PROFILE_EXTRACTION_SYSTEM: Missing preferences, mentioned_topics fields; no confidence
SUMMARY_SYSTEM_PROMPT: Generic (topics, tone, commitments)
post_process: message_count = len(recent) [always 20 → summarization on every call]
Rate limit: No-op (pass when limited)
Token budget: Only recent messages trimmed; commerce/retrieved unchecked
Profile embedding: Only created when name extracted
Summary staleness: No check
```

### After
```
format_profile: Natural language with human-readable labels
merge_profiles: List cap (15), deduplication, confidence metadata stored
PROFILE_EXTRACTION_SYSTEM: Full schema (all 13 fields), confidence tracking (explicit/inferred/temporary)
SUMMARY_SYSTEM_PROMPT: Person, preferences, relationship, commercial context, open loops
post_process: message_count from DB user record (correct modulo gating)
Rate limit: Respected (early return when limited)
Token budget: Commerce and retrieved context checked against budget
Profile embedding: Updated on any meaningful profile change
Summary staleness: Age check (>7 days → staleness note in context)
```

## Changes Made

### Files Modified

| File | Change |
|------|--------|
| `memory/context.py` | `format_profile` → natural language; `_render_value` helper; `_PROFILE_LABELS` dict; token budget enforcement for commerce/retrieved; summary staleness note; import `get_latest_summary_with_age` |
| `memory/profile.py` | `PROFILE_EXTRACTION_SYSTEM` expanded; `_PROFILE_LIST_CAP = 15`; `extract_profile_facts` returns `(facts, confidence)` tuple; `merge_profiles` gains `confidence_map` param, list caps, dedup; `extract_and_update_profile` updated for new return type and embedding on any change; rate limit respected |
| `memory/summarizer.py` | `SUMMARY_SYSTEM_PROMPT` rewritten; rate limit respected (early return) |
| `workers/llm_worker.py` | `post_process` uses `user.message_count` from DB instead of `len(recent)` |
| `db/postgres.py` | Added `get_latest_summary_with_age()` function |
| `tests/test_phase46_integration.py` | Added `db.postgres.get_user` mock for `post_process` tests |
| `tests/test_ai_resilience.py` | Updated `extract_profile_facts` assertion for tuple return type |
| `tests/test_phase_c1a_memory.py` | **Created** — 48 new tests |
| `docs/AUTONOMY_C1_A_MEMORY_AUDIT.md` | **Created** — Full memory audit |

## Summary Chain Behavior

- **Before:** Single rolling summary per user (UPDATE-or-INSERT). Bounded to 1 row. No append-only growth.
- **After:** Same bounded behavior. Summary prompt improved to focus on person, preferences, relationship, commercial context, open loops. Staleness check adds note when summary > 7 days old.

## Profile Extraction Changes

- **Before:** 11 fields in extraction prompt (missing `preferences`, `mentioned_topics`). No confidence.
- **After:** All 13 fields in extraction prompt. Confidence tracking (explicit/inferred/temporary) stored under `_confidence` key.

## Preference Handling

- **Before:** Lists grow without bound via `set()` union. No caps. No contradiction handling.
- **After:** Lists capped at 15 items. Deduplication preserves order. Latest explicit statement supersedes older ones (via scalar overwrite for explicit facts). Confidence metadata tracks certainty level.

## Contradiction Handling

- **Before:** No contradiction handling. Both "I love coffee" and "I quit coffee" remain in interests.
- **After:** Scalar fields (name, location, occupation) use latest-wins semantics. Lists accumulate but are capped. Confidence metadata distinguishes explicit from inferred.

## Confidence Handling

- **Before:** No confidence tracking. All extracted facts treated equally.
- **After:** Each field gets a confidence label (explicit/inferred/temporary) stored in `_confidence` key.

## Rendering Changes

- **Before:** `interests: ['hiking', 'music']` (Python repr)
- **After:** `Interests: hiking, music` (natural language with human-readable labels)

## Token Budgets

- **Before:** `TOKEN_BUDGET` dict defined but only `recent` enforced. Commerce/retrieved unchecked.
- **After:** All sections checked: commerce context and retrieved messages are token-counted and dropped if exceeding their budget allocations.

## Prioritization

- **Before:** No prioritization. Trimming was last-in-first-out.
- **After:** Existing trim logic preserved (keeps most recent). Token budget enforcement prevents oversized sections from being included at all.

## Creator Isolation

- **Before:** Memory tables lack `creator_id` but safe in single-creator DropFans system.
- **After:** No change needed. Verified: all DB queries are user-scoped. DropFans single-creator architecture ensures no cross-creator leakage.

## Cache Isolation

- **Before:** No explicit memory cache.
- **After:** No change needed. Profile embeddings stored in DB with user_id scoping.

## Idempotency

- **Before:** `merge_profiles` with `set()` union was idempotent for lists.
- **After:** `merge_profiles` with dedup+cap remains idempotent. Running merge twice with same data produces same result.

## Failure Handling

- **Before:** Rate limit check was no-op. Summarization ran on every call.
- **After:** Rate limit respected (early return). Summarization gated by actual user message count. All memory failures remain non-blocking to the main pipeline.

## Performance

- **Before:** No N+1 queries. Profile and summary use single DB queries.
- **After:** One additional `get_user` call in `post_process` for correct message_count. Negligible overhead.

## Tests Added

**48 new tests** in `tests/test_phase_c1a_memory.py`:

| Category | Count |
|----------|-------|
| Format profile rendering | 9 |
| merge_profiles | 10 |
| extract_profile_facts | 4 |
| Summary prompt quality | 8 |
| Token budget enforcement | 4 |
| Profile extraction order | 2 |
| Creator isolation | 1 |
| Memory idempotency | 2 |
| Adversarial scenarios | 8 |

**2 existing tests updated:**
- `test_extract_profile_uses_credential_pool` — tuple return type
- `test_post_process_calls_profile_and_summarize` — added get_user mock
- `test_profile_extraction_failure_logged_not_raised` — added get_user mock

## Test Results

```
3489 passed, 14 failed (all pre-existing), 1 skipped
```

Pre-existing failures (14):
- 8 DropFans model renames (title→file_name, price_cents, net_cents, pending_cents, web_buy_url)
- 1 DB connection (fangate dashboard)
- 5 DB connection (integration_real_infra)

## Remaining Limitations

1. **Vector search is O(N)** — full table scan for embeddings. No pgvector. Will degrade with message volume.
2. **No total context cap** — individual sections are budgeted but total system prompt size is unbounded.
3. **Summary is single-row** — no historical summary archive for long-term analysis.
4. **Profile embedding** — only created when meaningful data exists (name, interests, etc.). Users who never reveal info get no embedding.
5. **Confidence not used in rendering** — confidence metadata is stored but not yet consumed by the LLM context. Future phase could use it to prioritize explicit over inferred facts.
