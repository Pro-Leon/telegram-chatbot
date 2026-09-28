# Phase C.1-A Memory Audit

## 1. Runtime Path Trace

```
Telegram inbound
  → llm_worker.process_message()
    → build_context(user_id, msg, persona, creator_id)      # memory/context.py
      → get_user(user_id)
      → get_user_profile(user_id)                            # JSONB profile
      → build_system_prompt(persona, user, profile)          # includes format_profile
      → get_latest_summary(user_id)                          # single rolling summary
      → build_llm_context(creator_id, user_id)               # commerce context assembler
        → get_user, get_recent_messages, get_summary, purchases, offers, product, segments
        → derive_relationship_state, derive_commercial_pressure
      → retrieve_relevant_history(user_id, query)             # trigger-word gated vector search
      → get_recent_messages(user_id, limit=30)
      → trim_to_token_budget(recent, 1500)
    → generate_draft / generate_draft_with_tools / commerce draft
    → score_draft
    → route (auto-approve / operator queue / reject)
    → asyncio.create_task(post_process(user_id))             # fire-and-forget
      → get_recent_messages(user_id, limit=20)               # DUPLICATE fetch
      → extract_and_update_profile(user_id, recent)          # LLM extraction + merge
      → maybe_summarize(user_id, len(recent))                # modulo gate + LLM summary
```

## 2. Issues Found

### 2.1 Summary Chain — Bounded But Has Bugs

**Current state:** `save_summary` does UPDATE-or-INSERT. At most 1 summary per user. NOT append-only.

**Bugs:**
- `post_process` passes `len(recent)` (capped at 20) as `message_count` to `maybe_summarize`. The modulo check `message_count % 20 == 0` always passes when `len(recent) == 20`. Summarization runs on EVERY post_process call.
- Summary prompt is generic — doesn't emphasize person, preferences, relationship, open loops.
- No staleness check — a months-old summary is still injected.
- `MAX_SUMMARY_TOKENS = 250` is hardcoded, not configurable.

### 2.2 Profile Extraction — Incomplete Schema Mapping

**Bugs:**
- `PROFILE_EXTRACTION_SYSTEM` prompt only asks for: name, age, location, occupation, relationship_status, interests, communication_style, emotional_state_recent, important_dates, topics_to_avoid, purchase_signals.
- **Missing from prompt:** `mentioned_topics`, `preferences` — these fields exist in `PROFILE_SCHEMA` but are never extracted by the LLM.
- No `confidence` field — all extracted facts treated as equal certainty.
- No distinction between explicit vs inferred vs temporary facts.
- Only last 10 messages used for extraction.

### 2.3 Profile Merge — Unbounded Lists, No Contradiction Handling

**Bugs:**
- `merge_profiles` unions lists via `list(set(...))`. Lists (`interests`, `purchase_signals`, `topics_to_avoid`, `mentioned_topics`, `preferences`) grow without bound.
- No max list length. A fan's profile can accumulate hundreds of interests.
- No contradiction handling — if fan says "I love coffee" then "I quit coffee", both remain in interests.
- Scalars simply overwrite — no history of previous values.

### 2.4 Profile Rendering — Python Repr in LLM Context

**Bug:** `format_profile` (context.py:73) renders lists as `['hiking', 'music']` and dicts as `{'birthday': 'March 5'}`. This is Python repr, not natural language.

### 2.5 Token Budget — Partially Enforced

**Current state:** `TOKEN_BUDGET` dict defines allocations (system=600, profile=250, summary=400, commerce=300, retrieved=500, recent=1500).

**Bugs:**
- Only `recent` messages are token-trimmed via `trim_to_token_budget`.
- Commerce context (`render_context` output) is appended without token check.
- Retrieved messages are appended without token check.
- Summary is appended without token check.
- System prompt (persona + profile) is not token-trimmed.
- No total context size limit.

### 2.6 Recent Messages — Fetched Twice, Inconsistent Limits

**Bug:** `build_context` fetches 30 recent messages. `post_process` fetches 20. Two DB round-trips for overlapping data.

### 2.7 Summarization Trigger — Broken Modulo

**Bug:** `maybe_summarize(user_id, message_count)` checks `message_count % summarize_every_n != 0`. But `message_count = len(recent) = 20` and `summarize_every_n = 20`, so `20 % 20 == 0` → always triggers. Summarization happens on every inbound message.

### 2.8 Rate Limit — No-Op

**Bug:** Both `summarizer.py` and `profile.py` have `if not check_rate_limit(credential): pass`. When rate limited, the request proceeds anyway.

### 2.9 Profile Embedding — Name-Gated

**Bug:** `extract_and_update_profile` only creates/updates profile embedding when `extracted.get("name")` is truthy. Users who never reveal their name get no profile embedding.

### 2.10 Memory Prioritization — None

**Bug:** No prioritization when context exceeds budget. Recent messages are trimmed from the end, but there's no logic to prefer high-value messages over low-value ones.

### 2.11 Summary Staleness — No Check

**Bug:** A summary from months ago is injected without checking `created_at`. Stale summaries can contain outdated information.

## 3. Creator Isolation Audit

| Table | Has creator_id? | Isolation |
|-------|----------------|-----------|
| conversation_summaries | NO | Safe in single-creator system |
| user_profiles | NO | Safe in single-creator system |
| message_embeddings | NO | Safe in single-creator system |
| messages | NO | Safe in single-creator system |
| commerce_offers | YES | Fully isolated |
| commerce_purchases | YES | Fully isolated |

**Verdict:** In the current DropFans single-creator architecture, memory tables are safe. A single creator's API key maps to one creator. All memory is for that creator's fans. No cross-creator leakage possible with current architecture.

## 4. Changes Planned

### 4.1 Summary Chain Fix
- Pass actual user `message_count` from DB (not `len(recent)`) to `maybe_summarize`
- Enhance summary prompt to focus on person, preferences, open loops
- Add summary staleness awareness to context assembly
- Make `MAX_SUMMARY_TOKENS` configurable

### 4.2 Profile Extraction Fix
- Add `mentioned_topics` and `preferences` to extraction prompt
- Add `confidence` field to extraction schema (explicit/inferred/temporary)
- Cap list lengths in `merge_profiles` (max 15 per list)
- Handle contradictions: latest explicit statement supersedes older ones

### 4.3 Profile Rendering Fix
- Replace Python repr with natural language in `format_profile`
- Lists → comma-separated prose
- Dicts → natural format

### 4.4 Token Budget Enforcement
- Add token checks for commerce context and retrieved messages
- Enforce total context limit
- When over budget, drop lowest-priority memory first

### 4.5 Rate Limit Fix
- Actually respect rate limit check results (skip LLM call when limited)

### 4.6 Profile Embedding Fix
- Update embedding on any profile change, not just when name is extracted
