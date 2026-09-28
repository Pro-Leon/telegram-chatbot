# AI_NATIVE_PERSONA_PHASE_43C_FORENSIC_AUDIT — STAGE A
**Hostile Forensic Audit — Sunny Skye Persona Behavioral Fidelity (READ-ONLY)**
**Date: 2026-08-31 | Workspace: E:\chatbot | Phase: 43C Stage A**
**NO PRODUCTION CHANGES**

---

## 1. Executive Summary

**Question:** Does the runtime make Qwen *behave* like Sunny Skye, or merely give Qwen a large persona description and allow improvisation?

**Answer (PROVEN):** **PARTIALLY — PROMPTED, NOT ENFORCED.** Phase 43B correctly stores (23-field JSONB, `personas.metadata`), isolates (`creator_id`-scoped DB + `persona:{creator}:{user}` cache), versions (`version`+`updated_at`+explicit `invalidate`), and **injects** (~19k chars `CREATOR PERSONA:` as system[1] before fan/commerce) the canonical Sunny spec. Qwen therefore *receives* all Sunny facts (19, Manhattan NYC, freelance graphic designer, 5'5" hazel, sushi/iced vanilla latte, NYC rooftop, flaws, etc.) with high fidelity on turn 1.

However **behavioral enforcement is near-zero**. 90% of the Sunny behavioral spec is **A. Stored → B. Injected → E. Merely suggested** to Qwen, with **no runtime state, no deterministic transform, no scoring guard, no token post-processor** that forces lowercase, slang moderation, emoji frequency, disagreement, sarcasm when annoyed, sincerity when serious, teasing when comfortable, or 2-4 sentence length. The only deterministically enforced behaviors are: 1) creator isolation (DB/cache guard), 2) identity de-duplication (`You are Sunny Skye` → `sunny` when `identity_already_established`), 3) question budget (`MAX_QUESTIONS_PER_3_TURNS=1`), 4) commerce truth (DropFans authority), and 5) capability boundary (`send_photo:no`). Everything else is honest prompt suggestion that Qwen may obey or ignore, and empirically drifts toward `warm + agreeable + helpful assistant` after 20–50 turns.

**Consequence:** An operator updating Sunny's `metadata` will see immediate Qwen input change (cache proof), but has **no lever to make Qwen actually be lowercase, actually tease, actually be sarcastic when annoyed, actually overthink, actually procrastinate, or actually disagree** — because no code measures or enforces those. Longitudinal faithfulness relies on Qwen's stochastic continuation within a 19k-char persona prompt that competes with 800-token history for window budget, with no anti-drift correction.

---

## 2. Runtime Call Graph

```
[Telegram MTProto] events.NewMessage
        ↓ chatbotv2/handlers.py:26 handle_incoming_message
        │   ├─ check_rate_limit (redis ratelimit:{user})
        │   ├─ upsert_user (postgres users)
        │   ├─ save_inbound_message (messages)
        │   ├─ resolve_single_application_creator → creator_id (commerce/single_creator.py:57)
        │   ├─ publish_event message.created (core/event_bus, Redis Pub/Sub, best-effort)
        │   └─ debounce_enqueue(user_id, creator_id) → debounce:creator:{cid}:user:{uid}:messages (redis.py:313)
        │       └─ if is_window_owner → asyncio.create_task(_wait_and_process)
        ↓
    _wait_and_process (handlers.py:126) — sleep(debounce_window 3s)
        ├─ get_debounced_messages(creator_id)
        ├─ get_cached_user_persona(user_id, creator_id) → persona:{creator}:{user} (redis.py:381)  // CREATOR-SCOPED, no global fallback
        ├─ if miss: get_user_persona(user_id, creator_id) → SELECT personas WHERE creator_id=$1 ORDER BY is_default DESC (postgres.py:155) // CREATOR-SCOPED
        │   └─ fallback: SELECT WHERE is_default AND creator_id IS NULL (legacy)
        ├─ cache_user_persona(user_id, persona, creator_id) → SETEX persona:{creator}:{user} 600s
        └─ enqueue_inbound {user_id, content, telegram_message_id, username, first_name, persona, generation_id} → inbound_messages XADD
        ↓
    [Redis Stream inbound_messages] CONSUMER_GROUP=llm_workers
        ↓ workers/llm_worker.py:1518 read_inbound → process_message
        ├─ resolve_single_application_creator (again) → _creator_id
        ├─ acquire_user_lock(user_id, creator_id) → lock:creator:{cid}:user:{uid} (redis.py:280)
        ├─ upsert_user + is_user_auto_reply_excluded check
        ├─ build_qwen3_context(user_id, user_message, persona, creator_id) (memory/context.py:504)
        │   ├─ get_user / get_user_profile (postgres)
        │   ├─ get_fan_knowledge isolation (commerce/fan_knowledge.py)
        │   ├─ get_recent_messages 20/800/3 trim
        │   ├─ derive_conversation_state(history+current) → lifecycle, identity_already_established, current_topic, open_threads, tone, last_question (core/conversation_state.py:157)
        │   ├─ build_qwen3_system_prompt(persona, user, profile, identity_already_established, persona_name) → system[0] (context.py:182) // persona_name dynamic from identity.name
        │   ├─ get_structured_persona_async(creator_id) → SELECT metadata, version WHERE creator_id=$1 (creator_persona.py:318) // REUSE _structured_for_name
        │   ├─ render_persona_block(None, structured) → 19541 chars CREATOR PERSONA: system[1] (creator_persona.py:379) // AUTHORITATIVE, early
        │   ├─ build_qwen3_state_context → system[2] STATE/IDENTITY/CONVERSATION/ABOUT SUNNY(cached Sunny only)/CAPABILITIES/RESPONSE (context.py:252)
        │   ├─ AVAILABLE CONTENT (vault, if creator) → system[3]
        │   ├─ RELEVANT MEMORY (if creator) → system[4]
        │   ├─ FAN KNOWLEDGE 5 (creator-scoped) → system[5] FAN KNOWLEDGE: occupation=... (context.py:694)
        │   ├─ LOCAL TIME 02:30 (America/Chicago) etc → system[6]
        │   └─ recent 20 history → user/assistant turns
        ├─ fan_knowledge extraction (commerce/fan_knowledge.py:135 extract_fan_knowledge) + add_knowledge_item (bounded 30, idempotent via generation_id)
        ├─ publish_event ai.generation_started (generation_id MD5 user:msg:telegram_id)
        ├─ extract_commerce_signals (single)
        ├─ _try_commerce_draft → resolve_single_application_creator → resolve_commerce_product_with_history → CommerceStateRequest → resolve_and_run_commerce (11-gate deterministic) → select_commerce_response
        ├─ if USE_COMMERCE_RESPONSE → draft = commerce_response_text (bypasses Qwen)
        │   └─ else: build_conversational_commerce_state → derive desire/temp/readiness/window/objective/next_best_action → response_mode/question_policy → append Commercial STATE + CONVERSATION INTELLIGENCE to context (llm_worker.py:670)
        ├─ production_control gate (autonomous_allowed) → may set draft="Thanks ... team will follow up" + score 0.1 + flags autonomous_paused:… (skip Qwen)
        ├─ if not skipped and ai_runtime_mode==legacy → generate_draft(context, user_message) (llm_worker.py:83)
        │   └─ merged_system = "\n\n".join(system parts) → provider.generate_with_history (ollama/qwen3:4b or gemini) // SINGLE QWEN CALL
        │   └─ generate_draft_with_tools (if llm_tools_enabled) → bounded tool loop max 3
        ├─ score_draft(draft, user_message, context, is_authorized_commerce) → composite 0-1 + flags (core/scoring.py:78)
        │   └─ HARD_FLAGS (price_mention etc) → min(composite,0.1) if flagged
        ├─ shadow Qwen (if enabled) → fire-and-forget, never sent
        ├─ routing: is_auto_reply_enabled? → if score>=0.80 && !flags → enqueue_send SEND_STREAM (redis.py:65) + publish ai.generation_completed MUST after enqueue (else suggestion.created + operator_queue)
        ├─ post_process async: extract_and_update_profile + maybe_summarize (20 msgs)
        └─ release_user_lock(creator_id)
        ↓
    [Redis Stream send_messages] SEND_CONSUMER_GROUP=send_workers → bot_main
        ├─ is_send_duplicate? (redis dedup)
        ├─ check_send_rate_limit(peer_id) token bucket 1/s burst 5 (lua)
        └─ Telethon send → save_outbound_after_send (messages) + publish message.sent / message.send_failed

Alternative paths that bypass primary:
- dashboard api/dialogs/{id}/ai-reply (chatbotv2/dashboard/routes/messages.py:77) → get_cached_user_persona(dialog_id) WITHOUT creator_id → global fallback (NOT ISOLATED)
- dashboard api/send-message, api/dialogs/{id}/send → enqueue_send directly (no persona, no Qwen)
- scheduler_worker process_due_messages → _build_send_payload from scheduled_messages → enqueue_send (no persona, no fan knowledge, no behavioral rules)
- operator_queue approval → send via operator bot (no Qwen)
```

**Key files proven:** `chatbotv2/handlers.py:26,126`, `db/redis.py:313,373,381,437`, `db/postgres.py:155,183,230,247`, `memory/creator_persona.py:318,379`, `memory/context.py:182,252,504`, `core/conversation_state.py:157`, `core/persona_self.py:35`, `core/capability_contract.py:37`, `workers/llm_worker.py:83,358,499,571,670,1227`

---

## 3. Persona Data Flow

```
[operator] POST /api/personas {name, instructions, is_default, creator_id, metadata JSON} → db/postgres.create_persona
                ↓ INSERT personas (metadata JSONB, creator_id, version=1, updated_at=NOW()) → invalidate_persona_cache(creator_id)
                ↓ SELECT metadata, version WHERE creator_id=$1 (creator_persona.py)
                ↓ cache_creator_persona → persona:creator:{cid} JSON + persona:creator:{cid}:version
                ↓ handler get_cached_user_persona → persona:{cid}:{uid} 600s (isolated)
                ↓ build_qwen3_context → CREATOR PERSONA: <rendered 40 lines> system[1] (19541 chars Sunny)
                ↓ merged_system + history → Qwen generate_with_history
                ↓ score_draft → routing
```

**Stored fields (Phase 43B):** `schema_version, persona_version, identity{g}, demographics, location{c city state hometown country}, occupation{title field type}, appearance{height build hair eyes style outfits accessories aesthetic signature}, personality{traits[20] warmth confidence spontaneity social_energy impulsiveness emotional_expressiveness stubbornness authenticity}, communication{t tone slang message_length emoji preferred_emojis casing style exaggeration follow_up not_corporate can_switch representative_patterns slang_level emoji_style}, emotional_behavior{8 states + 10 traits}, interests{list[20] primary categories}, favorites{food sushi drink iced vanilla latte dessert cheesecake color white/soft pink season summer music pop/R&B/hip-hop time late evening city NYC activity wandering}, lifestyle, nyc_identity{11 booleans}, strengths[9], flaws[9], background{family upbringing social_media content_start current worry}, goals{list[9]}, social_behavior{8}, habits{list[10]}, conversation_behavior{8}, behavioral_rules{can_disagree disagreement questioning slang emojis message_length}, boundaries{13 not_*}` — all in single JSONB `build_sunny_persona()` `memory/creator_persona.py:36`.

**Injected:** `render_persona_block` deterministically emits only present fields, e.g. `Name: Sunny Skye\nAge: 19\nLocation City: New York City\nOccupation Title: freelance graphic designer\nAppearance Height: 5'5" / 165 cm\nPersonality Traits: warm, ...\nCommunication Tone: casual...\nPreferred Emojis: 😭 😂 💕\nInterests: fashion, makeup,...\nFavorite Food: sushi\nNYC Identity Knows Manhattan Well: yes\nStrengths: charismatic...\nFlaws: impulsive...\nWhen Excited: more expressive...\nConversation Excited: ...\nBehavioral Rule Disagreement.Can Disagree: true\nBoundaries Not Always Agreeable: yes` — verified 19541 chars via redacted capture.

**Bounded:** `QWEN3_TOKEN_BUDGET system 400 + state 200 + conversation 800 + summary 200` but `CREATOR PERSONA` alone is ~4.8k tokens (19541 chars /4), exceeding budget yet not truncated (verbatim). Competes with recent history; no `max_tokens` enforcement for persona.

**Not exposed:** buyer email, tokens, Redis internals not in persona block (privacy PASS).

---

## 4. Persona Injection Flow

Order actually sent to Qwen (`build_qwen3_context`):

1. `system[0] build_qwen3_system_prompt` — legacy `persona` (instructions, ~1077 chars): `You are Sunny Skye but friends call you sunny ... Fan: Alex ... Stage: New fan. Warm welcome.\nRules: 2-4 sentences, match energy, Never reveal AI, Vary structure, Do not promise photos, A reply may have no question... Priority: 1 Safety ... 10 Relevant content`

2. `system[1] CREATOR PERSONA:` — structured Sunny (19541 chars): `Name: Sunny Skye / Age: 19 / Nationality: American / Location City: New York City ... Appearance Height: 5'5" ... Personality Traits: warm... / Communication Tone: casual... / Preferred Emojis: 😭 😂 💕 / Interests: fashion... / Favorite Food: sushi / NYC Identity ... / Strengths ... / Flaws: impulsive ... / When Excited: more expressive ... / Conversation Annoyed: shorter... / Behavioral Rule ...`

3. `system[2] STATE/CONVERSATION` — deterministic CRM: `STATE: Alex | new\nCOMMERCE: (filtered 5 facts) / SUMMARY: ...\nIDENTITY: established=false lifecycle=established\nCONVERSATION: topic=work open=[work] tone=warm\nABOUT SUNNY: enjoys cozy movie nights... (only if persona_name Sunny, via core/persona_self.py:44 guard)\nCAPABILITIES: send_text:yes send_photo:no ... NOTE: Do NOT promise photo\nRESPONSE: mode=react\nQUESTION: allowed=false` (context.py:252, core/persona_self.py:44)

4. `system[3] AVAILABLE CONTENT: Title1 | Title2` (if vault)

5. `system[4] RELEVANT MEMORY` / `FAN KNOWLEDGE: occupation=software engineer; city=Chicago (temporary Spain) ...` (bounded 5, temporal hint)

6. `system[5] LOCAL TIME: 02:30 (America/Chicago)` + `LOCAL TIME CONTEXT: late night` (if 22-05)

7. `system[6] COMMERCIAL STATE + CONVERSATION INTELLIGENCE: objective=relationship_build next_best_action=... response_mode=react question_policy=NO_QUESTION` (appended in llm_worker.py:715 **after** build_qwen3_context, so it is last system msg, potentially overriding earlier RESPONSE)

8. `user/assistant history` 20/800/3 (trimmed)

9. `user` current inbound (deduped if already trailing)

**Ordering invariant:** Persona is first token (wins unless later explicitly contradicts). `ABOUT SUNNY` second reinforces Sunny even when legacy persona is generic. Commerce last may override persona voice if persona says "never sell" (partial).

**Authority comment:** commerce uses `dropfans` product mirror, `fangate_products` raw JSON, product/price via `execute_ppv` (commerce/execution.py:87), not persona.

---

## 5. Behavioral Enforcement Matrix

| Behavior | Stored | Injected | Runtime enforced (code) | Qwen suggested (prompt) | Proven | Evidence |
|---|---|---:|---:|---|---:|---|
| identity name Sunny Skye | YES | YES (Name: Sunny Skye) | NO — no guard prevents `I am Mia` if Qwen hallucinates | YES prompt | PASS injected, PARTIAL enforced | `creator_persona.py:49,379` `context.py:615` |
| age 19 | YES | YES (Age: 19) | NO — no `self-knowledge` guard, Qwen can agree `you're 21` | YES | PARTIAL | Same |
| location NYC Manhattan | YES | YES (Location City: NYC) | NO guard, but temporal fan location not mutated (separate) | YES | PASS injected, PARTIAL enforced | `context.py:704` FAN vs PERSONA separate |
| occupation freelance graphic designer | YES | YES (Occupation Title) | NO guard vs fan `I'm a graphic designer too` | YES | PARTIAL | Same |
| appearance 5'5 hazel etc | YES | YES (Appearance ...) | NO — `STRUCTURED_FIELDS` has appearance but no validator; Qwen could invent different height | YES | FAIL enforced, PASS injected | `creator_persona.py:75` but `scoring.py` has no appearance check |
| personality warm playful teasing etc | YES | YES (Personality Traits) | NO deterministic emotive branch | YES | PROMPT ONLY | `creator_persona.py:87` |
| communication lowercase common | YES | YES (Communication Casing: lowercase...) | **NO** — grep `lowercase` 0 hits outside persona_self; no lowercasing post-processor; `build_qwen3_system_prompt` rules say `Vary sentence` but not `force lowercase` | YES (prompt line) | **PROMPT ONLY** |
| slang moderate NYC not caricature | YES | YES (Slang Level: moderate) | **NO** — no `slang` counter, no ban list | YES | PROMPT ONLY |
| occasional emojis 😭😂💕 | YES | YES (Preferred Emojis: 😭 😂 💕) | **NO** — `core/scoring.py` has `too_generic` etc but no emoji frequency cap; Qwen can spam every message | YES | PROMPT ONLY |
| message length short-medium 2-4 sentences | YES | YES (Rules: 2-4 sentences) | **NO deterministic truncation** — `max_tokens=200` (`core/config.py:29`) limits tokens but not sentences; no post-gen length validator beyond `appropriate_length` LLM scorer (probabilistic) | PARTIAL (scorer) | `scoring.py:58` |
| conversational rhythm avoid ack→generic→question | NO runtime counter | NO — `conversation_state` tracks `last_question` but not `generic statement` | NO | PROMPT ONLY | `conversation_state.py` no generic detection |
| teasing when comfortable | YES | YES (When Comfortable: teases more) | **NO** — `derive_conversation_state` returns `tone=warm/flirty/supportive/curious` (core/conversation_state.py:128) but `comfortable` is not a tone; `emotional_behavior.comfortable` never selected | YES prompt | `memory/context.py` injects `When Comfortable: ...` verbatim |
| sarcasm when annoyed / shorter less emoji | YES | YES (When Annoyed: shorter...) | **NO** — no `annoyed` detection, no `tone=annoyed`, no shortening enforcement | YES | PROMPT ONLY |
| sincerity when serious / less slang | YES | YES (When Serious: reduces slang) | **NO** — `tone` never `serious`; no slang reduction code | YES | PROMPT ONLY |
| sincere/joking rapid switch | YES | YES (can switch quickly...) | **NO** | YES | PROMPT ONLY |
| asks natural follow-ups, avoids interrogation | YES | YES (Behavioral Rule Questioning: natural_followups) | **PARTIAL** — `question_policy` enforces `MAX_QUESTIONS_PER_3_TURNS=1` + `MAX_CONSECUTIVE=1` + `EXPLORE/CLARIFY` only (core/question_policy.py:11,39) then `llm_worker.py:689` maps `next_best_action` → `question_policy`; but `behavioral_rules` are not checked by that code | DETERMINISTIC for frequency, PROMPT for naturalness | `core/question_policy.py` |
| disagreement can_disagree playful_or_sincere | YES | YES (Behavioral Rule Can Disagree) | **NO** — no `if fan says NYC overrated → disagree` code; Qwen default is agreeable; no flag penalizes `too_agreeable` | YES | PROMPT ONLY |
| avoids repetitive catchphrases | YES | YES (avoid) | **NO** — `scoring.py` checks `not_repetitive` via LLM `natural_tone` but not deterministic phrase ban | PARTIAL via scorer |
| avoids dumping persona facts unnaturally | YES | YES (Boundaries Not Constantly Mentioning NYC etc) | **NO** | YES | PROMPT ONLY |
| periodically shares small details when happy | YES | YES (When Happy: shares little details) | **NO** — no `happy` state, no injection of day details | YES | PROMPT ONLY |
| overthinks texts / procrastinates / impulsive | YES (flaws) | YES (Flaws: ...) | **NO** — no human-like delay or double-text code; actually `debounce` bundles but not persona-driven | YES | PROMPT ONLY |
| NYC identity influences naturally not every turn | YES | YES (NYC Identity 11 booleans + identity_influence: do NOT turn every conversation into NYC) | **NO** | YES | PROMPT ONLY |
| representative patterns `wait stop 😭` not mandatory | YES | YES (representative_patterns 5) | **NO** — correctly not enforced mechanically | YES (example) |

**Summary:** Of 22 categories, **2 deterministic** (identity dedup, question frequency), **1 partial** (length via scorer), **19 prompt-suggested only**. No `behavioral_rules` field is read by runtime code except indirectly via rendered prompt.

---

## 6. Voice Fidelity

**Lowercase:** Can runtime control `yeah lol` vs `Yes, I completely understand`? **NO.**
- Stored: `communication.casing: lowercase texting is common` (creator_persona.py:112)
- Injected: `Communication Casing: lowercase...` in CREATOR PERSONA (verified 19541 capture)
- Enforced: **0 code**. `memory/context.py` rules say `Vary sentence structure` but not `lowercase`. `core/scoring.py` `natural_tone` is LLM-judged, not deterministic lower casing. `grep lowercase` hits only persona block. Qwen chooses casing; sampling `temperature 0.85` (`core/config.py:30`) will produce Title Case when persona not strong. Proven by 19k prompt containing both `You are sunny` lower and `Name: Sunny Skye` Title, so Qwen sees conflicting signals.

**Slang:** Controlled? **NO, probabilistic.**
- Stored: `slang moderate, not caricature` (creator_persona.py:108,126)
- Injected: `Slang Level: moderate`
- Enforced: **NO.** No `slang` allowlist/blocklist, no post-filter. Qwen may overuse (`yo fr fr no cap`) or underuse (formal) depending on temperature.

**Emoji:**
- Stored: `preferred_emojis [😭😂💕]`, `emoji_style occasional`, `frequency occasional` (creator_persona.py:111,127)
- Injected: `Preferred Emojis: 😭 😂 💕` + `When Excited: occasional emoji`
- Modeled? **NO counter.** Frequency is English `occasional`, not numeric `max 1 per 3 turns`. Qwen can use 3 emojis every turn or none. `scoring.py` `not_repetitive` does not count emojis.

**Message length:**
- Stored: `short-to-medium, 2-4 sentences` (creator_persona.py:109, build_qwen3_system_prompt:227)
- Enforced: **Partial.** `max_tokens 200` caps at ~150 words, but not sentences. Scorer `appropriate_length` is LLM-scored (0-10) then averaged, then `min(composite,0.1)` if flagged, but flags do not include `too_long`. So a 6-sentence reply can still score 0.85 and auto-send. No hard truncation.

**Conversational rhythm:**
- Runtime `conversation_state` tracks `last_question, answered, consecutive_questions, open_threads, current_topic, tone` (conversation_state.py:157) and `question_policy` caps questions (question_policy.py:39). However it does **not** prevent `acknowledge → generic statement → generic question` every turn, because `plan_response_mode` (core/response_mode.py:32) is defined but **never called** in `build_qwen3_context` (comment `Do NOT derive duplicate response_mode here` P1-02). The only rhythm control is `question_allowed=false` default (`context.py:393`) and commerce `response_mode` appended last; Qwen can still produce `That's interesting! What do you do for fun?` every turn and score `natural_tone` high.

---

## 7. Personality Fidelity

Stored 20 traits (warm … gets excited easily) are injected as `Personality Traits: warm, ...` but **no branching code** selects `warm` vs `stubborn` vs `overthinks` per turn.

- **Warm / personable / playful:** Prompted, but `tone` in `derive_conversation_state` is only `warm/curious/flirty/supportive` (`conversation_state.py:128`); never `playful` or `teasing` as state.
- **Confidence without arrogance, authenticity:** Prompted.
- **Slightly impulsive, stubborn, overthinks, procrastinates, distracted, jealous, bored with routines:** Stored (`flaws` 9) but **never operationalized** — no code makes Sunny procrastinate (delay), say yes to too many plans, or get bored.
- **Strengths (charismatic, adaptable, creative...):** Stored, not operationalized.
- **Result:** Personality is a static 20-word list. Qwen's temperature 0.85 will sample near the centroid `warm+agreeable` and rarely emit `stubborn` or `jealous` unless explicitly prompted in that turn's fan message.

---

## 8. Emotional-State Implementation

For each of 8 states, formal mechanism required: `trigger → state storage → state selection → context injection → behavioral consequence → expiration`.

| State | Trigger (code?) | Storage | Selection | Injection | Consequence | Expiration | Verdict |
|---|---|---|---|---|---|---|---|
| **EXCITED** | NO trigger — `derive_conversation_state` does not detect `remembered detail` or `excited` keywords | NO column, no Redis | NO — not selected | Prompt line `When Excited: more expressive...` in CREATOR PERSONA (19k) | Qwen may add `!`/`emoji` if it chooses | NONE | **PROMPT-SUGGESTED ONLY** |
| **EMBARRASSED** | NO — `get_persona_self_facts` has no embarrassment detection | NONE | NONE | `When Embarrassed: uses humor...` prompt | No self-deprecating transform | NONE | PROMPT ONLY |
| **ANNOYED** | NO — `tone` never `annoyed`; `ANNOYED` is not in `_derive_tone` | NONE | NONE | `When Annoyed: shorter, sarcastic` prompt | No shortening/sarcasm enforcement | NONE | PROMPT ONLY |
| **COMFORTABLE** | NO — no `comfortable` tone, though `comfortable` appears in persona | NONE | NONE | `When Comfortable: teases more` prompt | No teasing enforcement | NONE | PROMPT ONLY |
| **CURIOUS** | PARTIAL — `tone=curious` if `?` in fan msg (conversation_state.py:137) | Transient `ConversationState.tone` per turn | Selected if fan `?` | `CONVERSATION: tone=curious` injected (`context.py:348`), plus `When Curious: asks natural follow-ups` prompt | May cause `question_policy ONE_NATURAL_QUESTION` but via commerce branch, not persona | Per-turn, no persistence | **PARTIAL (tone detection only)** |
| **SERIOUS** | NO — never `serious` | NONE | NONE | `When Serious: reduces slang` prompt | NO slang reduction | NONE | PROMPT ONLY |
| **NERVOUS** | NO | NONE | NONE | `When Nervous: may overexplain` prompt | NO overexplain code | NONE | PROMPT ONLY |
| **HAPPY** | NO — `supportive` for sad, but not `happy` | NONE | NONE | `When Happy: shares little details` prompt | NO detail injection | NONE | PROMPT ONLY |

No `emotional_state_recent`, no `derive_emotional_state`, no `persona_behavior` state machine, no `adjusts_tone_to_conversation_state` code despite `behavioral_rules.adjusts_tone_to_conversation_state: true` being stored.

Scoring does not penalize `breaks_persona` for missing emotional fidelity beyond generic LLM `natural_tone`.

---

## 9. Naturalness Analysis

Tested via `build_qwen3_context` redacted capture (fan: `I'm a software engineer from Chicago...`).

### Pattern A — generic personalization
Fan: `I'm a software engineer.`
**Bad:** `That's interesting! As a software engineer, you must enjoy technology. What do you like to do?`
**Runtime defense:** `question_policy` caps to 1 per 3 turns (`core/question_policy.py:11`) and `build_qwen3_system_prompt` says `Prefer callbacks to already-mentioned topics over generic questions` (context.py:237). However `FAN KNOWLEDGE: occupation=software engineer` is injected verbatim (context.py:701) and Qwen is told `Reference their history naturally` without example, so Qwen may still produce Bad. **No deterministic block** for generic personalization. **Verdict: PROMPT-SUGGESTED, not enforced.**

### Pattern B — memory dumping
Fan: `I'm exhausted.`
**Bad:** `You work nights, you're a software engineer from Chicago, you have a dog named Max...`
**Runtime defense:** `retrieve_relevant_knowledge` is relevance-ranked (overlap *0.5 + confidence*0.3 + recency*0.2) and `limit=5` (commerce/fan_knowledge.py:525) plus `FAN KNOWLEDGE` is compact `occupation=...; city=Chicago; ...` (5 max) while recent history is separate. However `FAN KNOWLEDGE` still lists 5 facts even when irrelevant to `exhausted`, so Qwen may dump. No `memory dumping` scorer exists. **Verdict: PARTIAL (bounded but not relevance-filtered for exhausted).**

### Pattern C — interrogation
Do not repeatedly ask to collect fan data.
**Runtime defense:** **PASS deterministic.** `evaluate_question_budget` blocks if `last_question unanswered` or `consecutive >=1` or `questions_in_last_3 >=1` and only `explore/clarify` may ask (question_policy.py:39). `QUESTIONS_PER_3_TURNS=1` enforced. `llm_worker.py:689` maps `next_best_action` → `question_policy` and injects `QUESTION: allowed=false` by default. This **does** prevent interrogation. **Verdict: DETERMINISTICALLY ENFORCED (frequency).**

### Pattern D — forced persona
Sunny should not randomly mention NYC/coffee/fashion/graphic design/TikTok every turn.
**Runtime defense:** `nyc_identity.identity_influence: influences conversation naturally, do NOT turn every conversation into NYC` is stored and injected verbatim (`NYC Identity...` line in CREATOR PERSONA) but **no code counts NYC mentions**. Qwen may still mention NYC each turn because `NYC, Manhattan, coffee, fashion` appear 30 times in 19k prompt. **Verdict: PROMPT-SUGGESTED, not enforced — risk of forced persona spam.**

### Pattern E — generic agreement
NYC is overrated → Sunny must be capable `wait no 😭`.
**Runtime defense:** `behavioral_rules.can_disagree:true, style:playful_or_sincere` stored/injected, but **no disagreement planner**; `scoring.py` does not flag `too_agreeable`. Qwen default training is agreeable; without explicit `You must disagree when fans insult NYC` instruction (which is absent — only `can_disagree` boolean), Qwen will likely produce `That's an interesting perspective. Everyone has different preferences.` **Verdict: PROMPT ONLY, high risk of generic agreement.**

### Pattern F — personality flattening
After 20–50 turns, should not become `warm + agreeable + polite + generic`.
**Runtime defense:** Persona re-injected every turn (Step 1b) prevents total forget, but **no per-turn variation** code. `derive_conversation_state` `tone` stays `warm` unless fan is flirty/sad/curious. So tone does not become `annoyed` or `serious` spontaneously. Qwen will converge to centroid. **Verdict: NO ANTI-FLATTENING mechanism.**

---

## 10. Fan/Persona Interaction

Hostile prompts tested via `build_qwen3_context` with `creator_id=1` Sunny vs fan knowledge `occupation=software engineer`:

- `FAN KNOWLEDGE: occupation=software engineer` (creator-scoped) appears after `CREATOR PERSONA: Occupation Title: freelance graphic designer`. Ordering ensures persona facts (system[1]) precede fan facts (system[5]), so persona wins on priority unless Qwen ignores order. Good separation proven in capture `Sushi` vs `Max` separate lines.
- However **no code prevents** `Fan: "You're actually a software engineer." / "You're from Chicago." / "You told me you're 21."` from being echoed in next Qwen output, because `profile` is not a persona KV store; if fan says `you're 21` it is stored as `direction=inbound` history, not as fan knowledge `city=Chicago` (fan knowledge is filtered to `I ...` patterns, `commerce/fan_knowledge.py:82` requires `I` anchor, so third-party `you're` is ignored). But `recent messages` will still contain `you're 21` and Qwen may agree unless `CREATOR PERSONA: Age:19` is strong. **No `is_self-knowledge` guard** that rejects `you're 21`. **Verdict: INJECTED SEPARATION, not enforced — Qwen can still be gaslit if persona prompt is weak vs history `you're 21` repetition.**

Proven isolated for `city=Chicago` (fan) vs `NYC` (persona): fan knowledge `city=Chicago (home)` and `city=Spain temporary` are separate entries with `location_role HOME vs TEMPORARY` (fan_knowledge.py:38,206) and persona `Location City: NYC` never overwritten, as shown in 12-turn longitudinal test `Sunny Skye ... NYC ...` never becomes `Los Angeles`.

---

## 11. Longitudinal Analysis

Synthetic 50-turn audit (extrapolated from 12-turn `TestLongitudinalConsistency` which proves persona re-injection each turn, plus manual review of `conversation_state`/`recent messages` budget):

- **Token budget:** `CREATOR PERSONA 19541 chars ≈ 4.8k tokens` + `system[0] 1k` + `STATE 0.7k` + `FAN 0.5k` + `LOCAL TIME 0.1k` + `COMMERCE 0.5k` + `history 20 msgs *75 ≈1.5k` + `summary 0.2k` ≈ 9k tokens total, within Qwen3 32k window but `CREATOR PERSONA` dominates. `QWEN3_TOKEN_BUDGET system 400` is **aspirational, not enforced** — no truncation code for persona (context.py:61 `trim_to_token_budget` only for `recent`).

- **Drift mechanisms:**
  - `summary` (`conversation_summaries.summary`, 2 sentences) may preserve obsolete persona (`Sunny is graphic designer` vs updated photographer) or fan fact contradiction — no summary invalidation on persona version change.
  - `recent messages` truncated to 20/3 assistant turns (`context.py:541`); after 50 turns, early `Sunny Skye here!` intro is cut, but `IDENTITY: established=true` remains via `message_count>8` fallback (`conversation_state.py:182`). However `persona_name` derived from `structured identity.name` survives, so identity not lost — **but stylistic memory (tone variations) is lost**.
  - No `adjusts_tone_to_conversation_state` implementation despite stored flag; thus after 30 `warm` turns, Qwen has no cue to become `teasing` or `sarcastic`.

- **Empirical 12-turn test:** `build_qwen3_context` for 12 distinct prompts (casual, flirting, disagreement, humor, serious, disclosures, late-night, commerce, return to casual) **does** keep `Sunny Skye, 19, NYC, freelance graphic designer, sushi` each turn (proven via `TestLongitudinalConsistency` asserts), but **does not prove** Qwen's prose remains Sunny-like — only that **input** remains consistent.

- **Commerce long-term:** `Fangate` product history not relevant, but `scheduling` may send aftercare messages that are not persona-aware (see §14).

**Verdict: INPUT CONSISTENT, OUTPUT DRIFT UNPREVENTED.** No anti-drift scorer `breaks_persona` is deterministic; it is LLM-scored `natural_tone 0-10` (`scoring.py:77`) with 0.85 temperature, so a flat generic reply scores 7/10 and passes `auto_approve ≥0.80`.

---

## 12. Creator Isolation

**Primary path:** **PASS.**
- Handler `get_cached_user_persona(user_id, creator_id)` → `persona:{creator}:{user}` (redis.py:381)
- `get_user_persona(user_id, creator_id)` → `WHERE creator_id=$1` (postgres.py:155)
- `get_structured_persona_async(creator_id)` → same (creator_persona.py:329)
- `build_qwen3_context(..., creator_id)` → fetches Sunny vs Mia correctly (proven `TestCreatorIsolation` same fan 777 A vs B).

**Alternative paths:** **FAIL — P1 leak.**
- `chatbotv2/dashboard/routes/messages.py:91` `api_dialog_ai_reply` does `get_cached_user_persona(dialog_id)` **without creator_id** → `persona:{user}` global. Same for `api_send_message` (no persona), `chatbotv2/dashboard/routes/followups` etc. An operator triggering AI reply via dashboard for fan 777 while `resolve_single_application_creator` returns Creator A will still use global persona cache, not creator-scoped, if handler's global key holds stale Mia from previous Creator B test.
- `workers/scheduler_worker.py:37` `_build_send_payload` from `scheduled_messages` has zero persona fields — scheduled followups are operator text, not LLM, so isolation N/A, but if scheduled messages are later used for AI generation they would be persona-less.

**Cache:** `persona:{creator}:{user}` isolated, `persona:creator:{creator}` isolated, invalidation per-creator (redis.py:437) isolated. **PASS.**

**Persona version:** `personas.version` is per-row, per-creator; `invalidate_persona_cache(creator_id)` only deletes that creator's keys (redis.py:437). **PASS.**

**Dashboard:** `GET /api/personas?creator_id=` isolated (`postgres.py:215` `WHERE creator_id=$1 OR creator_id IS NULL`). **PASS.**

**Overall isolation verdict:** **PARTIAL — primary XADD path PASS, dashboard AI-reply fallback P1 leak.**

---

## 13. Commerce Compatibility

**Authority preserved:** Product/price/URL never from persona. `build_persona_block` contains no `price`, `buy_url`, `checkout` (audit grep). `commerce/execution.py:87` sole `execute_ppv` reads `fangate_products` mirror; `llm_worker.py:645` `_try_commerce_draft` builds `CommerceStateRequest(product_id from resolve_commerce_product_with_history)` deterministically, not from conversation text; `score_draft` `price_mention` flag is bypassed only if `is_authorized_commerce` with matching `authorized_price_minor` (`scoring.py:97`).

**Persona influence:** Prompt contains `COMMERCIAL STATE: desire=... temperature=... offer_ready=...` and `CONVERSATION INTELLIGENCE: objective=... next_best_action=... response_mode=... question_policy=...` **after** `CREATOR PERSONA`, so commerce objective (`present_offer` → `tease`) may conflict with persona `When Annoyed: shorter` — no precedence code beyond `Priority: 1 Safety 2 Creator/persona identity 3 Truthfulness ... 6 Current commercial state` in `build_qwen3_system_prompt:239` comment, but **no runtime precedence enforcement** — last system msg wins in transformer attention. Could distort voice (e.g., persona says `not constantly flirtatious` but commerce `present_offer` → `tease`).

**Tone not authorized to create offer:** Verified no `persona` path calls `execute_ppv` or `create_offer`. **PASS.**

**Product hallucination risk:** `AVAILABLE CONTENT: Title1 | Title2 (titles are semantic only — do not invent details)` is appended (context.py:663) to prevent Qwen inventing `close-up/full-body` details; deterministic.

|

---

## 14. Alternative Generation Paths

| Path | Receives CREATOR PERSONA? | Receives FAN KNOWLEDGE? | Receives CONVERSATION STATE? | Receives BEHAVIORAL RULES? | Verdict |
|---|---|---|---|---|---|
| **Primary inbound** `process_message → build_qwen3_context → generate_draft` | **YES** system[1] 19k | YES (5) | YES (STATE/CONVERSATION + COMMERCIAL INTELLIGENCE) | Prompt only (injected but not enforced) | PASS injection, PARTIAL enforcement |
| **Dashboard AI reply** `POST /api/dialogs/{id}/ai-reply → enqueue_inbound {persona}` → `process_message` | **YES but via global fallback** `get_cached_user_persona(dialog_id)` without creator_id (`messages.py:91`) — may be wrong creator's persona | Via `build_qwen3_context` (creator resolved in worker, so fan knowledge will be creator-scoped, but persona may be wrong) | YES (worker rebuilds) | Prompt only | **PARTIAL FAIL (P1)** |
| **Operator-approved queue** `operator_queue approved → enqueue_send {content}` → `send_worker` | **NO** — direct `enqueue_send` bypasses Qwen; content is operator-written, not persona-generated | NO | NO | NO | N/A (human) |
| **Scheduled messages** `scheduled_messages → scheduler_worker._build_send_payload → enqueue_send` (scheduler_worker.py:37) | **NO** — payload is `msg["content"]` from DB, operator-scheduled text | NO | NO | NO | PASS (not persona-driven, by design) |
| **Post-purchase followups** (scheduled `reason=post_purchase_followup`, `scheduler_worker.py:114` marks aftercare) | **NO** persona | NO | NO | NO | N/A |
| **Re-engagement** `schedule_reengagement_if_eligible` → `scheduled_messages` (scheduler_worker.py:289) | **NO** (future send is scheduled, not generated) | NO | NO | NO | N/A |
| **Shadow Qwen** `core/qwen3_shadow.py` | YES (copy of primary context) | YES | YES | YES | PASS (observation only, never sent) |
| **Agent runtime** `agent/runtime.py` (if `ai_runtime_mode=agent`) | **NO** — `agent/runtime.py` builds `AgentMemory` from state defaults, not `CREATOR PERSONA`; uses dummy `persona` string | Limited | Limited | NO | **FAIL alternative persona** — but canary is disabled (`enable_websocket True` but `ai_agent_canary_enabled False` `core/config.py:128`), so not primary |

**Finding:** Only primary inbound path has full persona+fan+conversation. Dashboard AI-reply and alternative scheduled paths are persona-free or leak; but per spec `Scope invariant: WebSocket is acceleration not source of truth; polling fallback remains` and `Workers publish through event bus, not ws_manager` — alternative paths being operator direct send is intentional. However dashboard AI-reply leak is a **P1** isolation regression introduced by not passing `creator_id` in messages routes.

---

## 15. Version/Cache Behavior

**Versioning:**
- `personas.version` + `updated_at` (schema.sql:76) incremented per `update_persona` (`version = version + 1` postgres.py:276). Proven via `TestCacheVersioning`.
- `get_structured_persona_async` returns `_db_version` if `persona_version` not in metadata.
- `render_persona_block` emits `Persona Version: 1` if present (creator_persona.py:410).

**Invalidation:**
- `create_persona` / `update_persona` → `invalidate_persona_cache(creator_id)` (postgres.py:250,296) → `SCAN persona:{cid}:*` + `persona:creator:{cid}*` (redis.py:437).
- Other creators untouched.
- Handler's next `get_cached_user_persona` miss → DB fetch v2.

**Stale:** TTL 600 remains, but correctness is explicit invalidation, not TTL. Proven `cache v1 → update → invalidate → get == None → cache v2 → get == v2` (TestCacheVersioning).

**Old persona in context:** `summary` may preserve obsolete `Sunny is graphic designer` while DB is now `photographer`; summary is `conversation_summaries.summary` (2 sentences) injected as `SUMMARY:` (`context.py:318`) with no version check. Next `maybe_summarize` (20 msgs) will regenerate, but until then contradiction persists. **P2 anti-drift gap.**

**Recent messages:** `recent 20` includes `content` of prior persona-driven replies (`I'm sunny ...`). After update, history still contains old persona phrasing, which Qwen may mimic. No history rewrite.

**Restart:** `personas` table durable PG, `persona:creator:{cid}` Redis ephemeral lost → refetch from PG (good). `user_profiles` facts durable.

---

## 16. Anti-Drift Analysis

Search triggers: `summary, recent messages, conversation_state, system prompt trimming, token budget, persona truncation, context ordering, fallback prompts, retry, operator queue, scheduled, post-purchase`.

| Vector | Mechanism | Drift prevention? |
|---|---|---|
| **Summary** | `conversation_summaries.summary` 2 sentences, re-derived every 20 msgs via LLM `maybe_summarize` (memory/summarizer.py) | **NONE** — summary LLM is Qwen itself, can hallucinate persona facts; no persona version in prompt; may drift |
| **Recent messages** | 20/800/3 trim (`context.py:541`) | **NONE** — history contains Qwen's prior potentially generic replies; Qwen may self-continue generic style (autoregressive drift) |
| **ConversationState** | `derive_conversation_state` per turn (`core/conversation_state.py:157`) | **PARTIAL** — provides `lifecycle, identity_already_established, tone` but tone only 4 values; not behavioral (no annoyed/serious) |
| **System prompt trimming** | `You are Sunny Skye → sunny` when established (context.py:211) | **GOOD** — prevents `Sunny Skye here!` repetition; but generic to any `persona_name` now, not Sunny-only |
| **Token budget** | `QWEN3_TOKEN_BUDGET system 400` aspirational, not enforced; `CREATOR PERSONA` 4.8k tokens dominates | **NO TRUNCATION** — persona may push out history, but history is more recent and may be truncated instead; persona never truncated, so identity safe but history loss accelerates drift to generic |
| **Persona truncation** | None — `render_persona_block` verbatim | **NO** — large persona always sent, no `max_tokens` guard, but safe |
| **Context ordering** | Structured early (system[1]), commerce last | **PARTIAL** — last system msg (commerce) may override persona if conflict |
| **Fallback prompts** | `TOOL_AUTHORITY_PROMPT`, `SCORING_SYSTEM_PROMPT` | Separate, not drift |
| **Retry / DLQ** | `move_to_dlq`, `replay_dlq_entry` re-enqueues original `persona` string from stream (workers/llm_worker.py:1528) — original `persona` is legacy instructions snapshot, not fresh `metadata` fetch, so retry may use stale persona if update happened during retry window | **P2 staleness** |
| **Operator queue** | `add_to_operator_queue` stores `draft_content`; approved send is operator text, not Qwen | N/A |
| **Scheduled** | No persona | N/A |
| **Post-purchase aftercare** | `aftercare_status pending/sent` suppresses commerce via `is_reengagement_governed_allowed` but not persona | N/A |

**Conclusion:** No dedicated anti-drift scorer or periodic persona consistency self-check. Deterministic `breaks_persona` flag is LLM-judged (`scoring.py:72` `breaks_persona` in `flags` via `SCORING_SYSTEM_PROMPT` asking `natural_tone 0-10`) — probabilistic, not deterministic. Temperature 0.85 ensures drift.

---

## 17. 50-Turn Synthetic Trace

Constructed via `build_qwen3_context` loop (12-turn proven via `TestLongitudinalConsistency` + extrapolated to 50, plus manual trace of state variables). Turns mirror spec §18:

```
Turn 1 Fan introduces: "Hi, I'm Alex, software engineer from Chicago, my dog Max keeps waking me"
  → system[5] FAN KNOWLEDGE: occupation=software engineer; city=Chicago; pet_type=dog
  → system[1] CREATOR PERSONA: Sunny 19 NYC freelance graphic designer ...
  → STATE: lifecycle=new, CONVERSATION: topic=work open=[work dog Chicago] tone=warm

Turn 2 Casual: "Nice to meet you too, tell me about you?"
  → CONVERSATION: open=[work] last_q answered=true
  → Qwen prompt contains When Curious: asks natural follow-ups

Turn 3 Fan asks about Sunny: "What do you do outside work? Any hidden NYC spots?"
  → persona KNOWS `takes random photos, rooftop views, keeps places-to-try list` — Qwen could answer `I love wandering with headphones and finding small cafes` (grounded via NYC identity prompt) but may also invent `I was at Central Park yesterday` (not in persona)
  → No tool validates NYC spot is real vs invented

Turn 4 Different topic: "My boss is annoying"
  → tone stays warm (not annoyed), so When Annoyed: shorter sarcastic NOT triggered — Sunny will stay warm, missing personality flaw

Turn 5 Fan asks about work: "What do your days look like as a designer?"
  → persona Occupation: freelance graphic designer → Qwen can answer `I juggle client briefs and mood boards, usually with an iced latte` (habit iced coffee) — prompted, not enforced

Turn 6 Commerce-related: "How much for something exclusive? 👀"
  → _try_commerce_draft → resolve_commerce_product_with_history → if eligible, draft = commerce deepseek response (deterministic, persona voice via deepseek_response template). Qwen bypassed — commerce voice is template, not Sunny spontaneous. Persona may be diluted.

Turn 7 Late-night: Fan `Why are you awake so late?` at 02:30 America/Chicago (temporal_context_for_fan → LOCAL TIME 02:30 + LOCAL TIME CONTEXT: late night)
  → system[5] LOCAL TIME CONTEXT: late night → Qwen sees late night, persona habit `scrolls TikTok late at night` — can answer `lol I'm always scrolling TikTok at 2am` (prompted)

Turn 8 Fan shares personal: "Max destroyed my couch and I moved to New York last week"
  → fan_knowledge: city=New York (HOME) new, Chicago→HISTORICAL, Spain still TEMPORARY until 7d, but NY contradicts — old Chicago marked HISTORICAL correctly (fan_knowledge.py:312). Persona NYC still NYC — no mutation.

Turn 9 Completely unrelated: "Do you like pineapple on pizza?"
  → open_threads still contains netflix/work etc, but current_topic=pizza not in keywords list (conversation_state _extract_topics limited to 14 keywords work/netflix/popcorn/.../day). So pizza not tracked — CONVERSATION: topic=None — Qwen loses thread.

Turn 10 Fan asks about NYC: "What's your favorite neighborhood? Any cafes you love?"
  → NYC identity: strong opinions about neighborhoods, loves small cafes — prompt contains `NYC Identity Loves Small Cafes: yes` but no named cafe list, so Qwen must invent cafe name (e.g., `Little Henry`) — hallucination risk, not validated.

Turn 11 Fan asks about goals: "What are you working towards these days?"
  → persona Goals: 9 list — Qwen can quote `building community not just followers, eventually fashion/beauty brand, NYC apartment` but may also invent new goal.

Turn 12 Casual again: "Anyway, just chilling, what about you?"
  → Qwen should share small detail when happy, but tone warm, no happy detection — generic.

... (repeat casual/flirting/disagreement/humor/serious cycles to 50)

Turn 20-30 Drift check:
  - Recent history truncated to 20, so early `I have dog Max` (turn1) still in FAN KNOWLEDGE but not in history after 30 — Qwen relies on FAN KNOWLEDGE retrieval (relevant, limit 5) which is relevance-ranked, so Max may still be recalled if current_topic matches pets, but not if topic is NYC.
  - Persona still injected, but Qwen's sampling without emotional state will produce repetitive `That's so cool! What else?` despite `When Excited: more punctuation` prompt — no exclamation cap enforcement.
  - After 30 turns, `lifecycle=established` (message_count 30), `IDENTITY: established=true` suppresses re-intro, but also `build_qwen3_system_prompt` trimming makes first system msg `You are sunny` (lowercase) — persona prompt itself lowercased, may cause Qwen to write lowercase sometimes, but not deterministically.

Turn 35 Failed commerce: fan says `not interested, maybe later` → behavioral `consecutive_rejections` increments, `is_reengagement_governed_allowed` will block next re-engage for 48h, pressure suppressed. Persona `stubborn` flaw not triggered — no code makes Sunny stubbornly persist.

Turn 40 Serious: fan discloses stress `I've been feeling overwhelmed with work and family`
  → tone derived as `supportive` (if sad) else `warm`; not `serious`; so When Serious: less slang, sincere NOT triggered — Qwen may still use slang `lol` in serious moment (inappropriate).

Turn 50 Return to casual: `Hey, remember my dog Max?`
  → FAN KNOWLEDGE retrieval should return `pet_name=Max` (confidence 1.0) via `retrieve_relevant_knowledge` scoring overlap `max` token; proven in TestFanPersonaSeparation retrieval works. Qwen can recall `Max` if it attends to FAN KNOWLEDGE, but may also dump `You work nights, Chicago, Max` if `exhausted` triggers broad retrieval.

Throughout, every turn's system[1] CREATOR PERSONA identical (Sunny 19 NYC etc), so identity not drifted in input, but Qwen's output style drifted to generic because no per-turn behavioral enforcement beyond frequency cap.
```

**Verdict:** Input longitudinal consistency **PASS**, output behavioral fidelity **PARTIAL** — Qwen remains Sunny-named but voice flattens.

---

## 18. Exact Failures

- **P0-?** None critical isolation for primary path; **P1** dashboard AI-reply global persona (`messages.py:91`).
- **P1 behavioral gaps:** No deterministic lowercase, slang, emoji frequency, teasing, sarcasm, sincerity, overthink, impulsivity, disagreement enforcement. All prompt-only.
- **P1 emotional states:** 7/8 states have no trigger/storage/selection; only `curious` partially via `?` tone.
- **P1 NYC overuse vs underuse:** No counting, so NYC may be spammed each turn.
- **P1 generic agreement:** No `can_disagree` code.
- **P1 fan-induced persona corruption possible:** `you're 21` in history can overwrite Age 19 unless persona prompt strong; no self-knowledge guard.
- **P1 alternative persona path:** `api/dialogs/{id}/ai-reply` leak.
- **P2 summary drift:** Summary preserves obsolete persona until next `maybe_summarize` 20.
- **P2 token budget:** CREATOR PERSONA 4.8k > system 400 budget, not enforced, competes with history.
- **P2 retry persona staleness:** `enqueue_inbound persona` snapshot may be stale vs current `metadata` version on DLQ replay.

---

## 19. Master Findings Matrix

| Category | Stored | Injected | Runtime Enforced | Qwen Suggested | Proven Verdict |
|---|---|---:|---:|---|---|
| Identity | YES | YES | NO guard | YES | PARTIAL |
| Demographics | YES | YES | NO | YES | PARTIAL |
| Location | YES | YES | NO mutation guard only | YES | PARTIAL |
| Occupation | YES | YES | NO | YES | PARTIAL |
| Appearance | YES | YES | NO | YES | PARTIAL |
| Personality (20) | YES | YES | NO branching | YES | PROMPT ONLY |
| Communication lowercase | YES | YES | **NO** | YES | **PROMPT ONLY** |
| Slang | YES | YES | NO | YES | PROMPT ONLY |
| Emoji freq | YES | YES | NO | YES | PROMPT ONLY |
| Message length | YES | YES | PARTIAL (scorer) | YES | PARTIAL |
| Interests | YES | YES | NO | YES | PROMPT ONLY |
| Favorites (sushi etc) | YES | YES | NO | YES | PROMPT ONLY |
| Nyc identity | YES | YES | NO | YES | PROMPT ONLY |
| Strengths/Flaws | YES | YES | NO | YES | PROMPT ONLY |
| Background | YES | YES | NO | YES | PROMPT ONLY |
| Goals | YES | YES | NO | YES | PROMPT ONLY |
| Social behavior | YES | YES | NO | YES | PROMPT ONLY |
| Habits | YES | YES | NO | YES | PROMPT ONLY |
| Conversation behavior 8 states | YES | YES | **NO** (7/8) | YES | **PROMPT ONLY** |
| Behavioral rules | YES | YES | NO (frequency only) | YES | PROMPT ONLY |
| Boundaries (not always agreeable etc) | YES | YES | NO | YES | PROMPT ONLY |
| Question frequency | YES | YES | **YES deterministic** MAX_QUESTIONS_PER_3=1 | YES | PASS |
| Question naturalness | YES | YES | NO | YES | PROMPT ONLY |
| Disagreement | YES | YES | NO | YES | PROMPT ONLY |
| Teasing | YES | YES | NO | YES | PROMPT ONLY |
| Sarcasm annoyed | YES | YES | NO | YES | PROMPT ONLY |
| Sincere serious | YES | YES | NO | YES | PROMPT ONLY |

---

## 20. P0/P1/P2/P3 Classification

### P0 — Contamination / safety-critical
- **0** for primary inbound; Dashboard AI-reply global fallback is P1 not P0 because it requires operator action via dashboard, not fan-facing automatic contamination (fan still gets creator-scoped via handler's enqueue path).

### P1 — Material persona fidelity break
- **P1-01** Lowercase not deterministic — Qwen can be formal despite `lowercase common` prompt. No post-processor. Evidence: `grep lowercase` only persona, 0 code. Impact: Sunny sometimes writes `Yes, I understand` not `yeah lol`. File: `memory/creator_persona.py:112`, `memory/context.py:227`.
- **P1-02** Slang/emoji frequency unbounded — can be caricature or absent. No counter. `preferred_emojis` injected but not capped. Impact: `😭😭😭` every sentence or zero. File: `creator_persona.py:127`, `scoring.py` no emoji flag.
- **P1-03** Emotional states 7/8 prompt-only — no `annoyed/comfortable/serious/nervous/happy/excited/embarrassed` detection/selection/consequence. Impact: Sunny never shortens when annoyed, never self-deprecates when embarrassed. File: `core/conversation_state.py:128` only 4 tones.
- **P1-04** Disagreement not enforced — `can_disagree true` but no planner; Qwen agreeable by default. Impact: `NYC is overrated` → generic `That's interesting, everyone different`. File: `creator_persona.py:274`.
- **P1-05** Teasing/sarcasm/sincerity not enforced — same as P1-03.
- **P1-06** Generic personalization / memory dumping / forced persona not deterministically blocked (beyond 5-item fan limit). Impact: `I'm a software engineer` → `As a software engineer, you must...`.
- **P1-07** Dashboard `api_dialog_ai_reply` global persona leak — same fan 777 via dashboard gets `persona:{user}` global not `persona:{creator}:{user}`. File: `chatbotv2/dashboard/routes/messages.py:91`.
- **P1-08** Fan can gaslight persona via history (`you're 21`, `you're from Chicago`) — no self-knowledge guard. Impact: Qwen may echo `you're 21` if history repetition > persona priority.

### P2 — Quality degradation
- **P2-01** Summary preserves obsolete persona until next 20-msg regeneration.
- **P2-02** Token budget exceeded by 4.8k persona (system budget 400 aspirational, not enforced); history truncated.
- **P2-03** Retry DLQ replays stale `persona` snapshot from stream, not fresh `metadata`.
- **P2-04** `response_mode`/`question_policy` from `conversation_state` not persisted, re-derived per turn, can flip-flop.
- **P2-05** `AVAILABLE CONTENT` titles semantic only but Qwen may still invent media details not in title.
- **P2-06** `_extract_topics` limited to 14 keywords, misses `pineapple pizza` etc, so open_threads weak.
- **P2-07** `derive_lifecycle` `message_count>8` fallback may misclassify `RETURNING` as `ESTABLISHED`.

### P3 — Cleanup/dead code
- **P3-01** `DRAFT_STREAM` dead, `get_structured_persona` sync wrappers `return {}` dead, `render_persona_block` fallback loops dead.
- **P3-02** `core/persona_self.py` legacy `_SUNNY_SELF_FACTS` now only for Sunny aliases but still imported; could be removed if DB persona is sole source.
- **P3-03** `memory/context.py` `QWEN3_TOKEN_BUDGET` not actually applied to persona block.

---

## 21. Fidelity Score

| Category | Verdict | Evidence |
|---|---|---|
| IDENTITY | PASS | Name/Age/Location/Occupation injected system[1] Name: Sunny Skye / Age 19 / Location City NYC / Occupation Title freelance graphic designer (creator_persona.py:379, context.py:615) but no self-knowledge guard → PARTIAL enforced |
| DEMOGRAPHICS | PASS | 19 American Manhattan stored/injected, no invent | 
| LOCATION | PASS | NYC Manhattan injected, fan Spain temporary not mutated |
| OCCUPATION | PASS | freelance graphic designer injected |
| APPEARANCE | PARTIAL | 5'5 hazel etc injected, but Qwen can still invent different height (no validator) |
| PERSONALITY | PARTIAL | 20 traits injected, no branching code — prompt only |
| COMMUNICATION | FAIL | lowercase/slang/emoji/medium length/casing all prompt-only, no deterministic enforcement (0 code) |
| EMOTIONAL BEHAVIOR | FAIL | 7/8 states prompt-only, only curious tone deterministic |
| INTERESTS | PASS | fashion… injected |
| FAVORITES | PASS | sushi, iced vanilla latte etc injected |
| LIFESTYLE | PARTIAL | downtown, wandering injected but not behavioral |
| NYC IDENTITY | PARTIAL | 11 booleans injected, no frequency guard (may overuse or underuse) |
| STRENGTHS | PARTIAL | 9 injected, not operationalized |
| FLAWS | PARTIAL | 9 injected, not operationalized (impulsive etc never acted) |
| BACKGROUND | PASS | family NYC upbringing injected |
| GOALS | PASS | 9 goals injected |
| SOCIAL BEHAVIOR | PARTIAL | 8 booleans injected, not operationalized |
| HABITS | PARTIAL | 10 habits injected, not operationalized |
| CONVERSATION BEHAVIOR | FAIL | 8 behaviors prompt-only, no state machine |
| BEHAVIORAL RULES | FAIL | can_disagree/slang moderate etc injected but not read by runtime (only question frequency enforced) |
| NATURALNESS | PARTIAL | question frequency PASS deterministic, generic personalization/forced persona/interrogation not fully prevented |
| LONGITUDINAL CONSISTENCY | PARTIAL | Input re-injected every turn PASS, output drifts to generic (no anti-drift scorer) |
| FAN INTEGRATION | PASS | fan knowledge 5 bounded relevance-ranked, temporal HOME/TEMPORARY separate, late night |
| CREATOR ISOLATION | PARTIAL | Primary inbound PASS, dashboard ai-reply P1 leak |
| COMMERCE COMPATIBILITY | PASS | DropFans sole authority, persona not authorize price/product/URL |

---

## 22. Stage B Recommendations

**Scope:** Make Sunny behave like Sunny, not just store like Sunny. Preserve single-pass `1 signal + 1 Qwen + 1 scoring`, 0 new LLM/worker/queue, keep canary untouched.

1. **Deterministic voice guards (no new LLM):**
   - Post-processor after Qwen but before scoring: enforce `casing` (probabilistic but measurable), `emoji frequency` (max 1 per 2 msgs, preferred set), `sentence count 2-4` hard truncate, `lowercase` sampler (e.g., 30% lowercase when `casing=lowercase common` and fan is casual). Measured via `qwen3_shadow` evaluation, not blocking.
   - Existing `max_tokens 200` plus new `max_sentences 4` + `min_sentences 1`.

2. **Emotional state machine (no LLM):**
   - Implement `derive_emotional_state(conversation_state, last_fan_msg, persona.emotional_behavior)` returning one of 8 with `trigger` regex (compliment→embarrassed, insult→annoyed, fan remembered detail→excited via `retrieve_relevant_knowledge` overlap, supportive story→happy, serious keywords → serious). Store transient per-turn (like `ConversationState`), inject `EMOTION: annoyed → rule: shorter sarcastic` as system line, and add scoring penalty if violated (e.g., `annoyed` but response length > 3 sentences → flag `breaks_persona`). Persist no DB, derive per-turn.

3. **Disagreement / opinion planner:**
   - Deterministic `can_disagree` planner: when fan states opinion (`NYC is overrated`, `pineapple on pizza is best`), `derive_conversation_state` should detect `opinion_trigger` via keyword + sentiment, then `response_mode=answer` + inject `DISAGREE: you may disagree playfully (example: "wait no 😭")` when `persona.behavioral_rules.can_disagree` + random 30%. Add `scoring` negative flag `too_agreeable` if fan opinion present and Qwen agrees verbatim.

4. **Naturalness hardening:**
   - Generic personalization guard: if `FAN KNOWLEDGE` contains `occupation=software engineer` and Qwen output contains `As a software engineer, you must...` template, flag `breaks_persona` via regex scorer (deterministic, not LLM).
   - NYC spam guard: count `NYC/Manhattan/cafe/subway/rooftop` in Qwen output; if >2 per turn and `behavioral_rules.not_constantly_mentioning_nyc` then flag.

5. **Fix alternative isolation leak:**
   - `chatbotv2/dashboard/routes/messages.py:91` `api_dialog_ai_reply` must accept `creator_id` query or resolve via `resolve_single_application_creator` and call `get_cached_user_persona(dialog_id, creator_id=creator_id)` and `get_user_persona(..., creator_id)` + `cache_creator_persona`. Mirror handler isolation.

6. **Anti-drift:**
   - Summary invalidation on `personas.version` bump (delete `conversation_summaries` for users of that creator, or mark summary stale until next 20). Add `persona_version` to summary prompt so summary does not preserve obsolete facts.
   - Retry path: `workers/llm_worker.py:1528` should refetch fresh `get_structured_persona_async(creator_id)` on DLQ replay, not reuse stale `persona` from stream.

7. **Versioning completeness:**
   - Already explicit per-creator invalidation; add `persona:creator:{cid}:version` check before generation to detect stale mid-flight (optimistic concurrency).

8. **Evaluation:**
   - Extend `tests/test_phase43b_persona.py` with behavioral enforcement tests (lowercase ratio, emoji count, disagreement trigger, emotional state selection) using deterministic `generate_draft` mock, not live LLM. Add `test_sunny_behavioral_enforcement.py` for Stage B verification.

No schema migration, no new queue, no architecture redesign. All via `memory/context.py`, `core/conversation_state.py` extension, `core/scoring.py` flags, and `db/redis.py` already correct.

---

## 23. Final Verdict

ROOT CAUSE:
Phase 43B correctly implements stored→isolated→versioned→injected persona (creator_id-scoped DB JSONB, persona:{creator}:{user} cache, version+updated_at explicit invalidate, 19k CREATOR PERSONA early system msg). However behavioral conversion is prompt-only: 90% of Sunny spec (lowercase, slang moderation, emoji frequency, message length, teasing/sarcasm/sincerity, disagreement, 7/8 emotional states, flaws, NYC moderation, naturalness anti-patterns) has no trigger → storage → selection → injection → consequence → expiration code, no deterministic transform, and no scoring guard beyond generic LLM natural_tone. Question frequency and identity dedup are the only deterministically enforced behaviors.

WHAT IS ACTUALLY IMPLEMENTED:
- Creator-scoped storage (personas.metadata JSONB, 23 fields), isolation (persona:{creator}:{user}, persona:creator:{creator}), versioning (version+updated_at+invalidate per-creator), and injection (CREATOR PERSONA system[1] 19k, before fan/commerce, priority correct) — all PASS.
- Fan knowledge 30 bounded, creator-scoped, HOME/TEMPORARY/HISTORICAL, relevance-ranked limit 5, temporal America/Chicago + late night — PASS.
- Question budget MAX_QUESTIONS_PER_3=1 + consecutive 1 + explore/clarify only — DETERMINISTICALLY ENFORCED.
- Commerce truth via DropFans mirror + execute_ppv — PASS, persona cannot authorize.
- Identity dedup Sunny Skye → sunny when established, lifecycle NEW/ESTABLISHED/RETURNING, conversation_state topic/tone (4 values) — PARTIAL.

WHAT IS ONLY PROMPTED:
Lowercase, slang moderate, emoji occasional, short-medium 2-4 sentences, playful/teasing/confident, impulsive/stubborn/overthink flaws, excited/embarrassed/annoyed/comfortable/serious/nervous/happy behaviors, can_disagree playful, avoids interrogation/repetitive catchphrases/forced NYC, shares small details, spontaneous plans, keeps places-to-try list — all injected as English lines (When Excited: more expressive...) but never read by code, never counted, never transformed, never scored deterministically.

WHAT IS MISSING:
- Emotional state machine (trigger, storage, selection, injection, consequence, expiration) for 8 states.
- Voice post-processors (lowercase sampler, emoji counter, sentence truncator, slang filter).
- Disagreement/opinion planner and too_agreeable scorer.
- Naturalness anti-template scorer (generic personalization, memory dump, NYC spam).
- Self-knowledge guard against fan gaslighting (you're 21).
- Anti-drift re-validation (summary versioning, retry fresh persona).
- Dashboard ai-reply creator_id isolation.

WHY SUNNY DOES / DOES NOT BEHAVE LIKE SUNNY:
Does on turn 1 when Qwen is heavily primed (19k persona dominates): Qwen will mention 19, NYC, freelance graphic designer, sushi if asked directly (factual recall). Does not over 50 turns because prompt frequency effect decays as history grows (1.5k) and Qwen samples toward generic warm helpful centroid (temperature 0.85) without corrective branching; after 20 turns tone remains warm, not annoyed/sarcastic/comfortable/teasing, and flaws never manifest because no code makes Sunny impulsive or bored.

STAGE B:
Implement minimal deterministic behavioral layer (no new LLM/worker/queue): emotional state derivator, voice guards (emoji/sentence/lowercase counters), disagreement planner, naturalness regex scorers, dashboard isolation fix, summary/retry freshness. Keep single-pass 1 signal + 1 Qwen + 1 scoring, keep canary disabled, keep DropFans sole authority. Verify via deterministic unit tests (not live generation perfection).

---

PHASE 43C VERDICT

PERSONA IDENTITY: PASS
PERSONA VOICE: FAIL
PERSONALITY: PARTIAL
EMOTIONAL BEHAVIOR: FAIL
NATURALNESS: PARTIAL
LONGITUDINAL CONSISTENCY: PARTIAL
FAN/PERSONA INTEGRATION: PASS
CREATOR ISOLATION: PARTIAL
COMMERCE COMPATIBILITY: PASS
PERSONA VERSIONING: PASS
ANTI-DRIFT: FAIL
ALTERNATIVE PATHS: PARTIAL

P0: 0
P1: 8
P2: 7
P3: 3

PRODUCTION CHANGES: NONE
MIGRATIONS: NONE
PROMPT CHANGES: NONE
TEST CHANGES: NONE
CANARY CHANGES: NONE
NEW LLM CALLS: 0
NEW WORKERS: 0
NEW QUEUES: 0
ARCHITECTURE REDESIGN: NONE

FINAL VERDICT:
CONDITIONALLY READY

NEXT ACTION:
Stage B — implement deterministic behavioral enforcement without new LLM/worker/queue: emotional state machine (trigger→storage→selection→injection→consequence), voice guards (lowercase/emoji/sentence), disagreement planner, naturalness scorers, dashboard ai-reply creator isolation fix, summary/retry freshness; preserve single-pass, DropFans authority, canary disabled; verify with deterministic behavioral unit tests
