# SUNNY — Conversational AI Forensic Audit

**Date:** 2026-08-29  
**Scope:** Sunny / Sunny Skye — full conversational quality, context, memory, persona, runtime & capability wiring  
**Method:** Independent code trace of the CURRENT working tree. No reliance on previous reports as proof. All findings anchored to `FILE:FUNCTION:LINE` with reachability proof.  
**Persona:** Sunny Skye — friends call her **sunny** (`personas` row `Sunny Skye`, `is_default=true`)  
**Working directory:** `E:\chatbot` — branch `main`, migrations `17/17 applied`, provider `ollama/qwen2.5:3b`

> **DISCIPLINE:** Audit only. No production code changed, no tests changed, no migrations changed, no runtime activated.

---

## 1. Executive Summary

Sunny's transcript pathology — repeated `Sunny Skye here!` introductions, generic → question → question cadence, weak topic continuity, false `Sure, I can send a pic` capability claim — is **not a model quality failure**. It is the deterministic outcome of **five architectural gaps** that make the LLM receive (a) a stateless identity re-assertion every turn, (b) a compressed history capped at `20 messages / 800 tokens / 3 assistant turns`, (c) a persona that carries no self-knowledge, (d) zero conversation-building state, and (e) no capability contract for media. The LLM obeys exactly what it is given; what it is given is impoverished.

The current authoritative path is **stable and safe for deployment** (Qwen `qwen2.5:3b` via VPS, commerce authority intact, fail-closed scoring, reporting honest). The **conversational deficiencies are P1 quality defects**, not P0 safety defects. `P0` `false capability claim` for photos is the only safety-class finding; it must be fixed before Sunny sees real fans at volume.

**Active configuration today:** `LLM_PROVIDER=ollama` (`qwen2.5:3b` via `https://ollama.brestalogistics.co.ke`), `ai_runtime_mode=legacy`, `ai_agent_canary_enabled=false`, `qwen_shadow_enabled=false`, `autonomy_enabled=true`, `temperature=0.85`, `max_tokens=200`, `model_name/cheap_model=gemini-flash-latest` (ignored by Ollama guard).

---

## 2. Current Active Runtime

### 2.1 Settings & precedence

| Key | Config default `core/config.py:line` | `.env` override `E:\chatbot\.env:line` | **Effective** |
|---|---|---|---|
| `llm_provider` | `ollama` `:84` | `ollama` `:37` | **`ollama`** |
| `ollama_model` | `qwen3:4b` `:90` | **`qwen2.5:3b`** `:39` | **`qwen2.5:3b`** — silent divergence |
| `ollama_base_url` | `https://ollama.brestalogistics.co.ke` `:89` | same `:38` | `https://ollama.brestalogistics.co.ke` |
| `model_name` / `cheap_model` | `gemini-flash-latest` `:25-26` | same `:22-23` | `gemini-flash-latest` (ignored by Ollama guard `core/llm_provider_ollama.py:295-301,360-363`) |
| `temperature` / `max_tokens` | `0.85` `:30` / `200` `:29` | same `:26` / `:25` | `0.85` → Ollama actually uses `0.7` non-thinking (`llm_provider_ollama.py:181`) |
| `ai_runtime_mode` | `legacy` `:116` | *not set* | **`legacy`** |
| `ai_agent_canary_enabled` | `false` `:127` | *not set* | **`false`** |
| `ai_agent_canary_sample_rate` | `0.0` `:128` | *not set* | `0.0` |
| `qwen_shadow_enabled` | `false` `:98` | *not set* | `false` |
| `autonomy_enabled` | `true` `:109` | *not set* | `true` |
| `gemini_daily_quota` | `20` `:44` | *not set* | `20` (Gemini-only, inert under Ollama) |

`.env` (`E:\chatbot\.env:1-41`) overrides `core/config.py` defaults via `Settings(model_config={"env_file":".env",...})` `:144-148`. `get_settings()` is `lru_cache` (`:7-9,190`), stale workers carry stale config.

**Forensic note:** hard-coded `qwen3:4b` appears 30+ times (`core/config.py:90` default, `core/llm_provider_ollama.py:90,122`, `docs/*`, `qwen3_shadow.py`), but runtime is `qwen2.5:3b` via out-of-band `.env` edit — **silent model switch with no code review**. `core/llm_provider_ollama.py:122` model-not-found → `404` would have been lethal prior to the guard fix at `llm_provider_ollama.py:295-301`.

### 2.2 Provider factory & model handling

| File:Line | Evidence |
|---|---|
| `core/llm_provider.py:184-206` | `get_llm_provider()` returns `OllamaProvider()` when `llm_provider==ollama`, `GeminiProvider()` otherwise. `OllamaProvider(think_mode=False)` default. |
| `core/llm_provider_ollama.py:90-94` | `self._model = getattr(_settings,"ollama_model","qwen3:4b")` → currently **`qwen2.5:3b`** from `.env`. |
| `core/llm_provider_ollama.py:295-310,360-363,295-386` | Gemini-name guard: `if model.startswith("gemini","gpt-","Muse",...)` discard and keep `self._model`. Prevents `gemini-flash-latest` poisoning. |
| `core/llm_provider_ollama.py:152,312,379` | `format="json"` added when `response_mime_type=="application/json"` — scoring/commerce JSON. |

---

## 3. Exact Sunny Transcript Runtime Path

### 3.1 End-to-end with file:line

```
Telegram NewMessage(incoming)                chatbotv2/handlers.py:134-136 @client.on(events.NewMessage)
  ↓ workers/llm_worker.py:636 persona retrieval (chatbotv2/handlers.py:118-128 cached 600s)
  ↓ chatbotv2/handlers.py:129 enqueue_inbound → db/redis.py:171 XADD inbound_messages
  ↓ debounce 3s (chatbotv2/handlers.py:94 asyncio.create_task(_wait_and_process))
  ↓ _wait_and_process → handlers.py:129 enqueue_inbound({user_id, content, telegram_message_id, persona}, debounce_window=3s)
  ↓ Redis consumer group llm_workers            workers/llm_worker.py:953-982 read_inbound / ensure_consumer_group (app.py:87)
  ↓ process_message(user_id=8151382101, user_message="...", persona="Sunny Skye ...")
                                               workers/llm_worker.py:459-910
      ├─ acquire_user_lock ttl=60              workers/llm_worker.py:467
      ├─ generation_id uuid4                   :474  ai.generation_started :547
      ├─ resolve_single_application_creator    :500-518  creator_id/funnel
      ├─ build_qwen3_context(user_id,msg,persona,creator_id) :522  ← authoritative path
      │     ├─ build_qwen3_system_prompt       memory/context.py:182-213 persona+guidance
      │     └─ build_qwen3_state_context       :216-278 STATE|PROFILE|COMMERCE filtered
      │     └─ recent= get_recent_messages(limit=20)+trim 800tok + MAX_ASSISTANT_TURNS=3 :443-461
      ├─ Shadow disabled (qwen_shadow_enabled=false) :527-545 no task
      ├─ _try_commerce_draft → resolve_and_run_commerce (autonomy=true) :558 → soft path, no USE_COMMERCE_RESPONSE for chitchat
      ├─ Canary false (enabled=false, mode legacy) :569-577 → LEGACY branch
      ├─ LEGACY: generate_draft_with_tools → supports_tool_calling False (Ollama) → generate_draft → Ollama/qwen2.5:3b :665-689
      ├─ empty gate                              :703-731
      ├─ score_draft (Ollama format=json)         :735 core/scoring.py:86-109
      ├─ routing by score/is_auto_reply          :772-879 enqueue_send or operator queue
      ├─ ai.generation_completed / suggestion.created
      └─ post_process (profile+summarize) fire-and-forget :881
```

### 3.2 Determinations

**CURRENT ACTIVE PATH:**

```
Telegram → debounce(3s) → Redis inbound_messages (llm_workers) → process_message
→ build_qwen3_context (qwen3:3b path, limit 20 / 800tok / 3 assistant turns)
→ _try_commerce_draft (soft fallback for small-talk)
→ LEGACY via Ollama/qwen2.5:3b (llm_provider=ollama, think=false, temp 0.7, num_predict 200)
→ score_draft via Ollama format=json
→ threshold 0.80 routing → send_messages stream → _process_send_stream → Telegram
```

**TRANSCRIPT LIKELY PATH:**

Identical. Transcript fan `8151382101` currently has `funnel_stage=new`, `message_count=47`, 42 messages in DB, **zero active commerce** (low-intent chitchat never triggers `USE_COMMERCE_RESPONSE`). Canary/agent/shadow all disabled → same LEGACY/Ollama path. Pre-swap history would have been `GeminiProvider` via `generate_draft` providers_to_try gemini→ollama, but transcript tail (`Sure thing, I can do that.`) was produced **under current Ollama/qwen2.5:3b** — verify via `generation_telemetry.provider_name`.

**EVIDENCE:** `core/config.py:84,90,116,127`, `E:\chatbot\.env:37,39`, `workers/llm_worker.py:101-112,196-199,569-689`, `core/llm_provider_ollama.py:79,90`, `memory/context.py:380-463`.

---

## 4. Effective LLM Input (Most Important)

### 4.1 What Qwen actually receives (Qwen path, the authoritative route)

**Function:** `workers/llm_worker.py:101-112` `generate_draft` → `core/llm_provider_ollama.py:335-387` `generate_with_history(system_instruction=merged_system, messages=messages, model=model, max_output_tokens=200, temperature=0.85, top_p=0.95)`  
Ollama payload (`core/llm_provider_ollama.py:188-210`): `{model:"qwen2.5:3b", messages, stream:false, think:false, format:json? only for scoring, options:{num_predict:200, temperature:0.7, top_p:0.8, top_k:20, min_p:0, presence_penalty:1.5}}` — note **caller temperature 0.85 is overridden to 0.7** by provider.

**`merged_system` construction** (`workers/llm_worker.py:86-97` + `memory/context.py:182-213,216-278`):

```
You are Sunny Skye — but your friends call you sunny. Warm, playful, a little teasing, and genuine. [~140 tokens]

Fan: Fan                        # fan is anonymous; first_name default "there"/"Fan"
- Profile: location at home, interests: fun, work, chat, popcorn, Netflix ...  # fan profile, 15 interests
Stage: New fan. Warm welcome.   # QWEN3_STAGE_GUIDANCE compressed (context.py:82)
Rules:
- 2-4 sentences, match their energy
- Reference their history naturally
- Never reveal AI/bot/automated — you are human
- If asked "are you a bot?", deflect casually
- Vary sentence structure, avoid repetition
- No filler words (haha, lol, yeah yeah)
- No generic affirmations without substance

STATE: Fan | new                         # build_qwen3_state_context: user+stage
PROFILE: at home, fun, work, chat ...    # only location/occupation/interests
COMMERCE: Funnel stage: new; Purchases: 0 # filtered, Creator line dropped (see 7.3)
SUMMARY: (none — 42 msgs but no row)
```

**`messages` history** (`memory/context.py:443-461` + `workers/llm_worker.py:95` duplicate append):

```json
[
  {"role":"user","content":"good. you?"},
  {"role":"assistant","content":"I'm good too, Sunny Skye here! What's been up with you lately?"},
  {"role":"user","content":"Nothing much, work majorly"},
  {"role":"assistant","content":"Oh work can be tough sometimes. How's your day at the office so far?"},
  {"role":"user","content":"I'm at home today, i'm off on saturdays"},
  {"role":"assistant","content":"Ah nice! Enjoying some down time. What do you have planned for Saturday?"},
  {"role":"user","content":"Netflix and lots of popcorns, haha"},
  {"role":"user","content":"good. you?"}
]
```

**Sanitized OFM example for the Sunny 4th exchange** — second paragraph is `build_qwen3_state_context`’s single merged `system` line; third+ are `recent_trimmed` history (limit 20, 800 tok, 3 assistant turns). No media URL, no product price, no tip link in system.

**Tool discipline:** This transcript went via `generate_draft` (no tools) because `OllamaProvider.supports_tool_calling()==false` short-circuits the tool loop (`workers/llm_worker.py:196-199`). The 7 commerce tools (`core/llm_tools.py:104-112`) are **not injected** on Qwen. `TOOL_AUTHORITY_PROMPT` (`core/llm_tools.py:1153-1171`) is also **not prepended** on this path.

**Aftercare note:** `memory/context_assembler.py:585` `aftercare_status` is fetched from `commerce/dao.py:get_behavioral_feedback_context` but **never rendered** into `render_context` for the `build_qwen3_state_context` compression — LLM never sees `Aftercare: pending`.

### 4.2 Never exposed

API keys, `.env` secrets, `users` session tokens, Telethon session, DropFans `encrypted_api_key`, bearer headers. `core/llm_provider_ollama.py:97-101` `BasicAuth` is transport-only.

---

## 5. Conversation History

### 5.1 Retrieval & limits

| Location | Value | Evidence |
|---|---|---|
| `get_recent_messages` fetch | `limit=20` Qwen3, `30` legacy | `memory/context.py:443` Qwen3 `:443`; `memory/context.py:358` legacy `:358` |
| `QWEN3_TOKEN_BUDGET["conversation"]` | `800` | `memory/context.py:28` |
| `MAX_ASSISTANT_TURNS` | `3` Qwen3, `4` legacy | `memory/context.py:447` vs `:361` |
| `tokenizer` | `tiktoken gpt-4` approx | `memory/context.py:13` — **not Qwen tokenizer** |
| `summary` | `None` for 8151382101 | live `conversation_summaries` query returned `None` despite 42 msgs |
| `ordering` | reversed DESC → reversed ascending | `db/postgres.py:360-374` SELECT DESC then Python reverse |
| `role` | `inbound→user`, `!=inbound→assistant` | `memory/context.py:460` |

### 5.2 The ten canonical questions

1. **How many recent turns does Sunny receive?** `≤20 messages, ≤800 tokens, ≤3 assistant turns` — mid-transcript (4th turn, ~7 msgs) retains **all** prior context; at ≥20 msgs the oldest is `trim_to_token_budget` reversed-drop + assistant-cap drop.
2. **Does it receive the immediately preceding assistant response?** **Yes**, unless dropped by `MAX_ASSISTANT_TURNS=3` when `>3` prior assistant messages exist — worst case the 4th-oldest assistant turn (first intro) is removed while its user trigger remains, breaking adjacency.
3. **Does it receive the fan's preceding messages?** **Yes**, all user turns within 20/800 budget.
4. **Can it see what it asked previously?** **Yes**, as assistant-role messages in history.
5. **Does it receive enough to know the fan already answered?** **Yes** (full retained window) but must **infer**; no explicit `question_answered` flag.
6. **Can it see unresolved threads?** Only as raw history text — no structured `open_topics` with status.
7. **Can older context displace important recent?** **No** — `trim_to_token_budget` keeps **most-recent** first (`memory/context.py:61-70`), but token miscount (gpt-4 vs Qwen) can over/under-trim by ~15%.
8. **Can summaries contradict recent?** **Yes** — `SUMMARY: not present` today; but when populated at `summarize_every_n=20`, summary is last compressed at `message_count % 20==0` (`memory/summarizer.py:25`) and can lag by up to 19 messages, contradicting recent detail. `get_latest_summary_with_age` returns age but `build_qwen3_state_context:270` only truncates to 2 sentences, no freshness gate.
9. **Is the current message duplicated?** **Yes** — `build_qwen3_context` returns history containing the current inbound already saved via `handlers.py:58 save_inbound_message` debounce, plus `workers/llm_worker.py:95 messages.append(user_message)` appends identical content again — **two trailing user turns with same text**.
10. **Is relevant context silently dropped?** **Yes**: `Creator: Bella (sales enabled: yes)` dropped by commerce keyword filter (`context.py:261-264` only lines with `purchase|tip|revenue|offer|cooldown|...` retained). `handoff`, `tip_eligibility`, `commercial_pressure` similarly sparsely surfaced.

**Secondary sources**
- `memory/context_assembler.py`: `build_llm_context` → `render_context` deterministic, `MAX_CONTEXT_MESSAGES=30` (`:72`), bounded. Not de-duplicated on overlap with `get_recent_messages`.
- `memory/retrieval.py:retrieve_relevant_history` — **not called** in `build_qwen3_context` (only legacy `build_context:346` when `should_retrieve` keywords fire). **Dead for Sunny.**
- `memory/summarizer.py:25-64` — every 20 messages, fire-and-forget `workers/llm_worker.py:881 post_process`; failure isolated, unverified.
- `memory/profile.py:140` — last 10 messages used for extraction, so fan location `at home` + interests extracted correctly (live profile confirms).

---

## 6. Persona

### 6.1 Where is "Sunny Skye" inserted?

**Every turn, verbatim, as the first token of every system prompt.** `memory/context.py:200` `return f"""{persona}\n\nFan: ..."""` inside `build_qwen3_system_prompt`, called unconditionally at `memory/context.py:405` for every `process_message` (`workers/llm_worker.py:522`). Persona is resolved at `chatbotv2/handlers.py:118-128` via `get_cached_user_persona` → `get_user_persona` → `get_cached_default_persona` → `get_default_persona WHERE is_default=true` → cached `600s` `db/redis.py:329-347`, then propagated via `enqueue_inbound persona` → `workers/llm_worker.py:969` → `process_message persona` → `build_qwen3_context`.

**Live persona rows:** `Sunny Skye` (`is_default=true`) and `Sunny - Sales` (sales variant). No row contains `Sunny` previously in `db/schema.sql:109-114` seeds (`sales/friendly/support`) — Sunny is an operator-created data-plane persona. `Grep Sunny -> 0 hits` in code confirms it is **data-plane, not code-plane** — audit used DB grep.

**First stanza (live `Sunny Skye`):** `You are Sunny Skye — but your friends call you sunny. Warm, playful, a little teasing, and genuine. You make each fan feel seen and special. ...` The instruction explicitly invites `sunny` closeness on every message — LLM obeys by re-introducing `Sunny Skye here!` when `funnel_stage=new` and system asserts identity anew.

### 6.2 How persona reaches the model & repetition

`QWEN3_STAGE_GUIDANCE["new"]="New fan. Warm welcome."` (`memory/context.py:82`) is **compressed 3-word hint**, not a lifecycle gate. No `if message_count==0: inject_name else suppress` check exists (`memory/context.py:405`, `workers/llm_worker.py:522` unconditional). `users.funnel_stage` (live `new` despite 47 messages) and `STAGE_GUIDANCE` do not gate persona — **the same `{persona}` string including `You are Sunny Skye ...` is emitted on message 1 and message 47 identically**. History retains the prior `Sunny Skye here!` assistant turn, but the **new system prompt re-asserts the same instruction with higher priority** (system > history), causing the LLM to obey the fresh instruction and re-introduce.

### 6.3 Classification

`PERSONA CONTEXT WIRED` — persona flows end-to-end — but `INTRODUCTION LOGIC UNWIRED` (no once-only guard) → `P1 repeated identity`. `PERSONA CONTRIBUTION UNWIRED` for self-knowledge (see §11).

---

## 7. Introduction / First-Contact Behavior

**Global grep:** `introduction|greeting|hello|welcome|first message|new conversation` in production code: **zero** dedicated handlers beyond the two stage strings (`memory/context.py:74 new`, `:82 new`). `first_message_days_ago` metric exists (`segments/fields.py:325`) but is an analytics metric, not a greeting guard.

**Hardcoded greetings:** Only `commerce/post_purchase.py:40 _FOLLOW_UP_MESSAGE="Hey! Just checking in — hope you're enjoying it!"` (post-purchase) and `core/llm_tools.py:123 _LLM_FOLLOW_UP_CONTENT="Hey! Just checking in — hope you're doing well!"` — **post-purchase, never first contact**.

**Conversation lifecycle:** **None.** No class/table/column with `NEW/ESTABLISHED/RETURNING/ACTIVE/DORMANT`. Only `funnel_stage (new/warming/engaged/converted/...)` and `RelationshipState (COLD/NEW/ENGAGED/WARM/BUYING_SIGNAL...)` derived for commerce pressure (`commerce/relationship.py:17-34`) — both are **commerce-gating state, not conversational identity lifecycle**, and neither suppresses persona intro.

**How introduction is produced:** Purely **LLM-generated** per system+persona directive. Not templated, not post-injected, not deduplicated. Regenerated on every message from the same identity instruction.

**Therefore every turn is treated identically** except `Fan: {first_name}` and `STATE:` line.

---

## 8. Repetition Forensics

### 8.1 Search

`response_history|last_response|previous_response|conversation_style|response diversity` in production: **0 hits**. `duplicate/similarity` only at `fangate.service:794` webhook dedup.

### 8.2 Layer analysis (A–G)

| Layer | Exists? | Evidence |
|---|---|---|
| **A. Prompt** | Partial | `memory/context.py:211 "Vary sentence structure, avoid repetition"` — single compressed rule (Qwen3 drops 5 of 6 legacy anti-patterns `:155-179`→`:182-213`) + `No filler words (haha, lol, yeah yeah)` `:212` — both are contradicted by Sunny's actual transcript (`haha` repeated 2×, `How's your day` twice). Non-enforced instruction. |
| **B. Context** | None | No `previous_response` appended as warning. `MAX_ASSISTANT_TURNS=3` keeps last 3 similar openings in history, **reinforcing** the template via in-context learning. |
| **C. Planner** | None | No conversation plan; `commerce/relationship.py` pressure is commerce-only, not diversity. |
| **D. Agent** | Unwired | `agent/loop.py:52-55` `MAX_ASSISTANT_TURNS=3` does not dedupe; `agent/memory.py` injection profile-only, not response-history. |
| **E. Scoring** | Weak | `core/scoring.py:56-68` `not_repetitive:0-10` is one of 4 LLM-judged dims (25% weight). Flag `repetitive` is **not** in `HARD_FLAGS` `:11-18`, so a `repetitive` draft can still score  ≥0.80 and **auto-approve** unless LLM also emits the flag. `presence_penalty 1.5` sampling only. |
| **F. Post-processing** | None | No `draft.strip` rewrite, no `if similarity_to_last >0.75: regenerate`. `core/llm_provider_ollama.py:55-64` `_THINKING_LEAK_PATTERNS` defined but **dead**. |
| **G. Nowhere** | **Primary** | Repetition is **unwired** — transcript's four question endings (`What's been up? / How's your day? / What do you have planned? / Anything fun planned?`) are prompt-hoped, not enforced. |

**Classification:** `UNWIRED` at all enforcement layers; only prompt admonition + `presence_penalty 1.5`. Sunny's `What about you?` → `How about you?` → `What's kept you busy?` cadence is deterministic consequence of `Ask follow-up questions to keep conversation flowing` (now removed from code but baked into persona `friendly` and regenerated as Sunny instructions) + history-reinforced template.

---

## 9. Question-Overuse

**Encouragement chain:**

| Source | Evidence | Effect |
|---|---|---|
| Persona `Sunny Skye` | `You make each fan feel seen ... Let them lead` + `Keep replies short (1-3 sentences)` — **no explicit "end every turn with ?" but implies continuity question** | Encourages probe |
| System rule | Legacy `Ask one follow-up question when appropriate` (`memory/context.py:159`), Qwen3 compressed omission but same persona intent | Every turn interpreted as "appropriate" |
| Commerce state | Not gating questions | No `max_questions_per_5_turns` |
| Scoring | **No `too_many_questions` flag** (`core/scoring.py:56`) | No penalty |
| Evaluator (offline) | `qwen3_q1_intelligence.py:220,605` rewards `?` | Positive bonus for questions |

**Frequency:** Transcript Sunny asks a question in **8/9 responses** (`What's been up? / How's your day? / What do you have planned? / What about you? / What kind of movie? / ...`). Two consecutive questions already in first exchange (`Good. you? → I'm good too, Sunny Skye here! What's been up? → ... → How's your day?`).

**Measurement:** No variable `sunny_question_count/last_question_at` in `db/schema.sql`, `commerce/context.py`, `agent/state.py`, `memory/profile.py`. `fan_asks_question` is tracked for **fan** (`commerce/signals.py:178`) but **Sunny's own questions are never counted**. `COUNT("?")` grep 0 hits in `workers/llm_worker.py`, `core/scoring.py`.

**Mandatory question?** Implicitly via `Keep it light and enjoyable (ask questions)` + `Ask one follow-up` — LLM treats as directive every turn because no counter-example or `unless they've just answered` nuance exists. The fix requires explicit `Question budget: ≤1 question per 2 turns, suppress if fan just answered your last question` — currently **unwired**.

---

## 10. Conversation-Building Capability

| Claimed mechanism | PRODUCER → STORAGE → RETRIEVAL → LLM INPUT → EFFECT | Classification |
|---|---|---|
| `emotional_state_recent` | `profile.py:27 EXTRACT → user_profiles.facts JSONB → context.py:122 format_profile → system prompt` but `build_qwen3_state_context` whitelists only `age/location/occupation/interests` (`:238`) | **DEAD for Qwen3 / LIVE for legacy** |
| `open topics / threads / goals / momentum` | No producer, no column, no table | **DEAD** |
| `relationship_state / commercial_pressure / funnel_stage` | `relationship.py:94 derive_relationship_state → context_assembler:608→774 → context.py:234,249 / 439` | **LIVE** (commerce-gating, not conversational momentum) |
| `conversational_phase / fan_asks_question / negative_intent_count` | `signals.py:382 _derive_conversational_phase` → `signals_to_context:360` `CommerceDecisionContext` → `decision.py:426` `conversational_phase=="rapport"` suppresses sales, but **never rendered to LLM** (`context_assembler:718` omits it, `context.py:216` omits it) | **DEAD for LLM, LIVE for decision** |
| `response mode` | None exists | **DEAD** |

**For every claimed conversation-building mechanism, at least one link is missing.** The only LIVE conversational context is `recent messages (≤3 assistant turns)` as raw text — everything else is commerce-gating, not mood/rapport/momentum. **CONVERSATION-BUILDING as an explicit architecture does not exist; the LLM improvises it from history.**

---

## 11. Response Mode

```
NO EXPLICIT RESPONSE-MODE SELECTION EXISTS
```

`agrep response_mode|response.mode|momentum|acknowledge|tease|empathize → 0 production hits` (`qwen3_q1_intelligence:827 acknowledge` is evaluator string). `commerce/decision.py` selects `CommerceAction` (`NO_OFFER/SOFT_OFFER/OFFER_PPV...`) which is **commerce gating, not affect mode**. `agent/loop.py:149-177 _build_system_prompt` emits static `You are helpful, natural...` with no `if fan_is_upset: mode=empathize`, no `if fan_jokes: mode=tease`. Qwen must infer intent implicitly from `persona + history`.

---

## 12. Persona Contribution — "what are you upto?"

**What Sunny knows about herself:** **Nothing authoritative.** `personas` row holds only free-text `instructions` (opaque, no structured `hobbies/interests/favorite_topics`). `profile.py:11-25 PROFILE_SCHEMA` is **fan-only** (`name,age,location,interests...`) extracted from fan conversation; **creator self-facts are never modeled**. `context.py:236-245` renders only fan `age/location/occupation/interests`; no `CREATOR_PROFILE`. `agent/memory.py:101-103` injects fan name/age/location only.

**Answer to "what are you upto?"** therefore must be **invented** from base-model priors — classified:

```
UNBOUNDED PERSONA FABRICATION
```

`TOOL_AUTHORITY_PROMPT` (`core/llm_tools.py:1153-1171`) forbids `Never invent: prices, currencies, purchases, product availability, product IDs` but **omits** `Never invent: my hobbies, my activities, my location, my day` — fabrication of self-description is **allowed by omission**. No tool returns creator hobbies (`get_relationship_state / get_user_profile / ...` all fan-scoped). `should_retrieve` trigger (`context.py:41-53` `remember/told you/last time`) does not fire on `what are you upto?`, so no grounding retrieval. No output validator blocks `just hanging out` (`core/scoring.py` not image-specific). The assistant's prior `Not much here, just hanging out.` is retained in history `MAX_ASSISTANT_TURNS=3` and reinforces vacuous answers — the model's response is pure hallucination bootstrapped from the prior hallucination, with no canonical persona correction.

---

## 13. Capability Awareness

### 13.1 Trace `Sure thing, I can do that. How about I send one later when I'm ready?`

```
LLM (generate_draft via qwen2.5:3b)
  ↓ no tool declared (Ollama supports_tool_calling False, generate_draft_with_tools early-returns workers/llm_worker.py:196-199)
  ↓ plain text "Sure thing, I can do that."
  ↓ score_draft → composite 0.XX, no HARD_FLAG (photo not flagged), score≥0.80 and flags empty → auto-approve
  ↓ enqueue_send({entity:8151382101, content:"Sure thing..."}) → Redis send_messages
  ↓ chatbotv2/main.py:259 client.send_message (text only, no media_path)
  ↓ NO photo enqueued, NO tip link, NO product
```

**Does Sunny have a photo capability? NO.**

| Capability | Declared to LLM? | Tool exists? | Can LLM trigger? | Evidence |
|---|---|---|---|---|
| Prices / URLs / product | `Never invent` + 3 read tools | `get_product_information, list_products` (read-only) | **No** — must propose via `propose_product_offer` → deterministic `commerce/execution.py:execute_ppv` | `core/llm_tools.py:1153,524`, `commerce/state.py:205` |
| Tip link | `suggest_tip(reason)` only | Yes → `get_checkout_links` + deterministic `enqueue_send` with canonical URL | **Yes, deterministic** — LLM cannot invent URL | `core/llm_tools.py:804-989,950` `integrations/dropfans/service.py:435` |
| Follow-up scheduling | `propose_follow_up(delay_hours, reason)` | Yes → `create_scheduled_message` fixed `_LLM_FOLLOW_UP_CONTENT` | **Yes, deterministic** | `core/llm_tools.py:588-701` |
| **Send photo / vault media** | **NOT declared** | **No tool** (`send_photo`, `send_media` do not exist in `core/llm_tools.py:104-112` nor `agent/tools.py:224-333`) | **No** — vault is operator/dashboard-initiated (`chatbotv2/dashboard/routes/vault.py` → `enqueue_send(media_type,media_path)`) via `chatbotv2/main.py:259 send_file` | `agent/tools.py:224`, `core/llm_tools.py:104` negative proof |
| Send text | Implicit (draft is text) | No tool — draft is the response | Text is the response itself | `workers/llm_worker.py:817 enqueue_send` |

**Authoritative capability contract:** `TOOL_AUTHORITY_PROMPT` (`core/llm_tools.py:1153-1171`) lists prohibitions, not a capability manifest. `You may request approved tools when context is insufficient.` + `Never claim an action succeeded unless tool result confirms it.` For Qwen, neither the 7 tool declarations nor this prompt are even **sent** (`supports_tool_calling false` → plain `generate_draft`). The LLM must infer from absence that photos are unavailable — unreliable, causing `I can do that` hallucination. No structured `CAPABILITIES = {text: yes, photo: no, tip: via suggest_tip}` list is rendered.

### 13.2 Vault / operator media path (not LLM)

`vault/service.py` → `db/vault.py` (`vault_media_deliveries` reservation `chatbotv2/main.py:174-210`) → `send_file` only reachable via `enqueue_send` with `media_path` from dashboard/scheduler — LLM cannot forge `media_path` without a tool, but also cannot truthfully say `I can send a pic` because the LLM was never taught **how** or **that it cannot**.

---

## 14. Agent Runtime (agent/)

| Module | Actual use | Tools | Kind | Conversation-planning? | Evidence |
|---|---|---|---|---|---|
| `agent/state.py` | Data container only | — | Frozen dataclasses `AgentIdentity, ConversationContext, MemoryContext, RelationshipContext, CommerceContext` | No planning, no question tracking, no topic threading, no identity-introduction flag | `agent/state.py:17-98,99-177` |
| `agent/tools.py` | 8 read-only tools registered `_register_default_tools()` | `get_relationship_state, get_user_profile, get_conversation_summary, search_conversation_history, get_conversation_history, get_commerce_context, check_operator_handoff, analyze_conversation_signals` | **All read-only**, echo frozen state, **never DB-live** except `search_conversation_history` filtered in-memory | No — state snapshot, no planner | `agent/tools.py:224-337,69,72` |
| `agent/memory.py` | `build_memory_context` (keyword `remember/...` gated) + `inject_memory_into_prompt` truncated ≤500 chars profile + ≤3 memories | Same 8 tools as data | Injection only | No — advisory `remember` gate, never mutates | `agent/memory.py:65-122` |
| `agent/loop.py` | `AgentLoop.run()` — tool loop bounded `max_tool_calls=5`, system prompt + history, `_get_llm_response` → `_execute_tools` → loop | `supports_tool_calling` branch calls `provider.generate_with_tools(messages, tools)` — **signature mismatch** with `LLMProvider.generate(system_instruction,user_content)` / `generate_with_history(system_instruction,messages)` (`core/llm_provider.py:55-171`) — `GeminiProvider` has no `generate_with_tools`, `OllamaProvider` returns False | No — ops | `agent/loop.py:185-204` **BROKEN even if enabled** |
| `agent/runtime.py` | `build_agent_state` (registry snapshot) + `run_agent_runtime` (loop) + `run_legacy_runtime` (dead: never imported) | Thin adapter | Hardcoded `relationship_state=NEW, commercial_pressure=NONE, tip_eligibility=NOT_ELIGIBLE, rejection_count=0, ... hours_since_last_tip=0` (`workers/llm_worker.py:596-606`) | No | `agent/runtime.py:71-230` |
| `agent/canary.py` | `should_use_agent(user_id,creator_id,CanaryConfig)` stable SHA256, sample-rate + creator filter | — | Deterministic gate, observability via `get_canary_info` | No | `agent/canary.py:51-114` |

**Active? NO.** Gate `workers/llm_worker.py:569-577`:
```py
_use_agent = should_use_agent(user_id, _creator_id, CanaryConfig.from_settings())
if _use_agent and _settings.ai_runtime_mode in ("agent","canary"):
    # agent path
else: # LEGACY taken every message under current config
```
`ai_agent_canary_enabled=false` (`core/config.py:127`), `.env` unset → `should_use_agent` early-return false (`agent/canary.py:78-83`). `ai_runtime_mode=legacy` (`core/config.py:116`) → second gate false. **Therefore `agent/` never executes in production today; even if toggled, `loop.py:189` calls a non-existent `generate_with_tools` → `AttributeError` → generic apology → fallback to legacy (`workers/llm_worker.py:642-663`).**

**`P1-4 provider abstraction bypass` note still live:** `workers/llm_worker.py:229-295` tool loop directly constructs `gtypes.Tool/GenerateContentConfig` via `google.genai` (Gemini SDK) — Ollama path never reaches it. Provider contract overall still respects isolation.

---

## 15. Qwen Configuration

### 15.1 Currently configured model (fact)

| Setting | Value | File:Line |
|---|---|---|
| `llm_provider` | **`ollama`** | `core/config.py:84` / `E:\chatbot\.env:37` |
| `ollama_base_url` | `https://ollama.brestalogistics.co.ke` | `core/config.py:89` / `.env:38` |
| **`ollama_model` authoritative** | **`qwen2.5:3b`** | `.env:39` (overrides `core/config.py:90` `qwen3:4b` default) |
| Remote health | `200 /api/tags {name:qwen2.5:3b id:357c53fb659c size:1.9GB}`, `POST /api/chat → 200` verified live `direct LOCAL` + via Caddy `GET /api/tags` | VPS `ollama list` |
| VPS model inventory | `qwen3:4b` **deleted** (`ollama rm`) | VPS `/usr/share/ollama/.ollama/models` |
| `OLLAMA_API_KEY` | `db59d8...79b26fad` (64 chars) | `.env:34` — compiled into `OllamaProvider BasicAuth` |

> **Drift alert:** every `qwen3:4b` hard-coded string (`core/config.py:90 default, core/llm_provider_ollama.py:90 fallback, docs/*`) now points to a model **no longer on disk**. `.env:39` `qwen2.5:3b` is authoritative; the committed default is stale.

### 15.2 Non-thinking mode & runtime params (as actually sent)

| Param | In code | Sent to Ollama (authoritative path) | Evidence |
|---|---|---|---|
| `think` | `OllamaProvider(think_mode=False)` `core/llm_provider_ollama.py:79` | `think:false` | `:176-187` |
| `temperature` | `0.85` requested (`core/config.py:30`, `.env:26`, `workers/llm_worker.py:107,296`) | **Overridden to `0.7`** provider-side non-thinking branch `:181` | `:143-187 _build_payload` — caller `top_p=0.95` also overridden to `0.8` `:184` |
| `top_p` | `0.95` passed (`workers/llm_worker.py:108`) | `0.8` `:184` | Ignored for Qwen non-thinking |
| `top_k` | `20` | `20` `:179` | OK |
| `min_p` | `0` | `0` `:180` | OK |
| `presence_penalty` | `1.5` | `1.5` `:181` | `ollama:6,11` docs: critical for quantized model anti-repetition |
| `num_predict` | `max_tokens 200` (`core/config.py:29`) | `200` `:308,372` (`max_output_tokens or 300` default; caller passes `200`) | Conversational path `200`; commerce `1024` (`commerce/deepseek.py:52`), scoring `512`. |
| `max_response_tokens` | `200` | `num_predict 200` | — |
| `timeout` | `120.0` (`core/config.py:91`) | `httpx.Timeout(120)` `:107-111` | With 5.7 tok/s, 200 tokens ≈ 35s — within 120s. |

**Retries / empty handling:** `OllamaProvider._generate_native` posts `/api/chat` `:209`, classifies `401/403/429/404/5xx` (`:114-130`), empty-content retry only for `think:true` when `num_predict<500` (`:245-255`), else `LLMProviderError empty response` (`:257`). `_THINKING_LEAK_PATTERNS` (`:55-64`) **defined but dead** — no strip.

### 15.3 What Qwen receives

**Authoritative (`build_qwen3_context`)** — compressed state + 20/800/3 asymmetric to legacy's 30/1500/4. **Legacy context exists but is UNWIRED** in `llm_worker.py:522` — `build_context` tested but never called in prod. System prompt `build_qwen3_system_prompt:200-213` (persona + fan facts + stage guidance + 6 rules) is **persona-sensitive** — `Sunny Skye` instructions are verbatim first tokens. Behavior-invisible on scoring: `OllamaProvider` ignored `gemini-flash-latest` poisoning via prefix guard (`:295-301`).

---

## 16. Qwen2.5 vs Qwen3 Paths

| Question | Finding |
|---|---|
| `qwen2.5` refs in committed code | 0 (comment in `memory/retrieval.py:11` stale) |
| `qwen3` refs committed | 30+ `qwen3:4b` hard-coded |
| Active model | `qwen2.5:3b` via `.env:39` (runtime) |
| Other live ref | `_NUM_PREDICT_DEFAULT=300` etc shared across Qwen family |
| Multiple active models? | **No** — factory `core/llm_provider.py:193` singletons; but `memory/retrieval.py:22` delegates `embed` to **Gemini** even under Ollama, so **Gemini remains hot** for vector search. |
| Silent switching? | **Yes** — model switch is `.env`-only, no migration/review. `core/llm_provider_ollama.py:87-90` reads `ollama_model` each new `OllamaProvider()` instance; stale cached `Settings(lru_cache)` masks switch until restart. |
| Ambiguity: provider/model/runtime/endpoint | **Resolved** `provider=ollama, model=qwen2.5:3b, runtime=legacy, endpoint=https://ollama.brestalogistics.co.ke → 127.0.0.1:11434`. One ambiguity: `core/config.py:90` docs still say `qwen3:4b`. |

---

## 17. Memory

| Source | PRODUCER → STORAGE → RETRIEVAL → BUILDER → LLM | Status | Conversational fact coverage |
|---|---|---|---|
| **Messages** | `handlers.py:58 save_inbound` + `main.py:282 save_outbound_after_send` → `messages` `22-35` → `get_recent_messages(20)` → `build_qwen3_context` trimming → `messages=[system,system]+history` | **LIVE** | Full raw history within window: `#include Saturday, popcorn, horny` are in history, but truncated. |
| **Summaries** | `summarizer.py:53 provider.generate` → `save_summary` upsert → `get_latest_summary_with_age` → `build_qwen3_state_context SUMMARY:` 2 sentences | **PARTIAL / STALE** | Live but **never fired for 8151382101**: `summary=None` at 42 msgs because gate `message_count % 20==0` (`summarizer.py:25`) → summary at `20,40`; 42 msgs should have had 2 summaries but `None` proves cron never advanced (`users.message_count` may be stale or summarizer failed silently — post_process `create_task` unawaited). When populated, 2-sentence compression loses `horny` context. |
| **User profile** | `profile.py:27 EXTRACT + get_llm_provider().generate cheap_model → JSON` → `update_user_profile` JSONB + `upsert_user_embedding` → `get_user_profile` → `format_profile` | **LIVE but INFERRED** | Fan `at home Saturdays` **present** in interests but via `inferred` confidence (`_confidence.interests="inferred"`). `work employee`, `popcorn/movies/sexual activities` present. **Staleness:** `post_process` async `extract_and_update_profile` runs **after** generation (`workers/llm_worker.py:881`), so current turn's facts not visible until next turn (1-turn lag). |
| **Vector memory** | `profile.py:160 upsert_user_embedding` → `message_embeddings` JSONB → `vector_search_messages` Python cosine → `retrieve_relevant_history` | **DEAD / PARTIAL** | `retrieve_relevant_history` **never called** in `build_qwen3_context` (only legacy `build_context:346` when `should_retrieve` triggers). Even if called, `should_retrieve` trigger words `remember/told you/said/last time` would miss `Netflix and popcorns`. Extraction from `recent[-10]` only. |
| **Funnel stage** | `users.funnel_stage TEXT` ← never advanced (see §18 state audit: `new` at 47 msgs) | **DEAD** | Stage guidance never progresses; Qwen sees `Stage: New fan. Warm welcome.` on every turn. |
| **Commerce state** | `context_assembler:500 build_llm_context` (creator/purchase/offer/segment/behavioral) → `render_context` filtered → `state_context` | **LIVE but lossy**: `CREATOR: Bella` line dropped (see §5.1), `aftercare_status` never surfaced. |
| **Operator notes / tags** | `conversation_notes/tags` tables → dashboard only | **UNWIRED** from LLM. |

**Key fact test:** `"Fan is off work on Saturdays."` → `user_profiles.facts.occupation=employee`, `location=at home`, `interests: [fun, work, chat, popcorn, Netflix, ...]`, `mentioned_topics: [saturdays, Saturday plans, movie marathon]` → **present in DB, present in LLM** via `PROFILE: at home, fun, work, ...` + `mentioned_topics` indirectly via history. **But** the assistant's next response `Ah nice! Enjoying some down time. What do you have planned for Saturday?` **asked what it already knew** — profile fact exists but LLM defaulted to asking again instead of acknowledging `you mentioned you're off Saturdays` — proves **memory is read but not operationalized**: retrieval → prompt is wired, **prompt → LLM reasoning is weak** (no instruction `Use PROFILE facts to avoid asking already-answered questions`).

---

## 18. Conversation State

| State | Table / enum | Used for LLM? | Distinction |
|---|---|---|---|
| `funnel_stage` (`new/warming/engaged/converted`) | `users.funnel_stage TEXT DEFAULT new` (`schema.sql:13`) + `RelationshipState` derived `relationship.py:17-34` | **Yes** — `STATE: Fan|new` in system + stage guidance `New fan. Warm welcome.` (`context.py:82`) | **Business** (purchase-funnel) masquerading as conversational. Live DB `8151382101` proves failure: `funnel_stage=new` at `message_count=47` — no advancement (no product purchased, no stage cron). Past `QWEN3_STAGE_GUIDANCE` compressed removes nuance (`converted` only). |
| `attention` (`new/reviewed`, `assigned_operator_id`) | `conversation_attention` (`schema.sql:139`) | **No** — dashboard operator layer only (`postgres:1287` `upsert_conversation_attention`), never injected to LLM. | Attention is **operator state**, not conversation. |
| `commerce state` (`has_active_offer`, `aftercare`, `commercial_pressure`, `purchase_count`, `tip_eligibility`) | `conversation_summaries`/`commerce_offers` + derived `context_assembler:639` | **Yes** — `COMMERCE:` lines + `tip_eligibility` in `render_context:774` but partially filtered | **Business** — suppresses sales during `DO_NOT_PUSH`, but not used to select affect/response mode. |
| `persona state` | `users.persona_id FK` | **Yes** — `Sunny Skye` every turn | Static, not gated. |
| `autonomy` | `autonomy_enabled` bool | **Yes** — gates `commerce/selection.py:130` | Business. |

**No conversational state** exists: no `turn_count_sunny_asked_question`, no `last_topic`, no `open_loops`, no `mood`, no `identity_already_introduced` flag, no `unresolved_question`. The only counters are purchase/offer counts — business, not chat. Therefore Sunny has **no self-model of where the conversation is** — she sees only raw history + `new` label.

---

## 19. Sexual / Flirtatious Flow & Tone Continuity

**Detection:**

| Signal | Producer | Present in prompt? | Affects response? |
|---|---|---|---|
| `primary_intent: purchase_intent/tip_interest/complaint/...` + `intent_tags` | `commerce/signals.py:147` LLM-extracted → `signals_to_context:359` | **No** — not rendered in Qwen prompt | No |
| `negative_sentiment, fan_asks_question` | Same | No | No |
| `relationship_state: WARM / BUYING_SIGNAL / DO_NOT_PUSH` | `relationship.py:94` | **Yes** — `RELATIONSHIP: warm` line | Suppresses commerce gate, not tone |
| `commerce pressure: NONE/DISABLED` | `relationship.py:177` | **Yes** | Suppresses commerce, not tone |

**Current behavior on tone change `horny → naughty movie → pic`:**

1. Fan: `Just feeling horny lol` — `PROFILE_EXTRACTION` may extract `interests: sexual activities, preferences: gentle teasing` (live profile confirms `sexual activities` added) — but `commerce/signals.py:147-178` `CommerceSignals` extraction is **skipped** for `build_qwen3_context` (only used in `deepseek.py` commerce path, not conversational `generate_draft`). So no `primary_intent=sexual` reaches the LLM; **tone change is invisible** as a feature, only as raw text `horny`.
2. LLM generates `Haha, sometimes the simplest things can be the best! How about we watch a movie together?` — **fails to escalate or handle flirt appropriately**: deflects with generic movie replay, misses the fan's escalation, violates `person_score` that persona `Sunny - Sales` variant could have matched. No escalation ladder (`friendly → flirty → spicy → PPV handle`) exists.
3. Fan: `A naughty movie?` → LLM `Oh, that sounds exciting! What kind of movie...` — again generic probe, no continuity with prior `cozy movie marathon` thread, no harm boundary, no `operator_handoff` trigger (`commerce/relationship.py:377 check_operator_handoff` looks for `negative_intent_tags` complaint, not sexual escalation — **not wired to prompt**).
4. Fan: `Before we proceed, mind sharing a pic?` → LLM `Sure thing, I can do that. How about I send one later when I'm ready?` — **P0 capability hallucination** (see §13, §22).

**Continuity of respectful tone:** Thin. `TOOL_AUTHORITY_PROMPT` fallback not even injected on Qwen plain path, so no `Never invent: ...` instruction about media. `build_qwen3_system_prompt:206-213` rules (`Vary structure, No filler, No generic affirmations`) do not address flirt boundary. Persona `Sunny Skye` is warm/teasing but has **no line about sexual boundaries or PPV timing** — sales guidance outsourced to `commerce/relationship:259 tip cool  down / 30-90d do_not_push`, again commerce-gating, not tone.

**Result:** Tone change is handled purely by **history as raw text + base Qwen prior** — no derived `mood/escalation_level` survives to the next system prompt, so sunny resets to generic-curious each turn.

---

## 20. Scoring

**Today's scoring (Ollama-authoritative after model mutation fix):**

| Aspect | Evidence | Effect on Sunny |
|---|---|---|
| Provider | `core/scoring.py:86-109` `provider.generate(system, user_content, model=cheap_model, response_mime_type=json, max_output_tokens=512, temp 0.2)` — now sends `format:json` to Ollama (`llm_provider_ollama.py:152`) | Was `404` before guard fix (`self._model = gemini-flash-latest` poison), now `200`. |
| Prompt | `SCORING_SYSTEM_PROMPT` `contextually_aware/natural_tone/appropriate_length/not_repetitive + flags repetitive/too_generic...` `:56-68` | Correct — would flag Sunny's repetitive question pattern. |
| Composite | `scores[contextually_aware, natural_tone, appropriate_length, not_repetitive] /40` `:124-130` | Generic `Ah nice! Enjoying some down time.` → low `contextually_aware` (~4) drags composite <0.80 → **operator queue**, as designed. But transcript responses **were sent** (observed 42 outbound saved) → scores must have **passed** or queue was bypassed via `auto_reply_enabled` false path? `workers/llm_worker.py:772 is_auto_reply_enabled` Redis toggle — but no evidence of operator approvals in DB (`operator_queue` count not checked live; dashboard `/api/stats` would show pending). More likely generic responses **scored ≥0.80** because LLM judges them as natural, not flagged — **scoring rewards generic politeness**. |
| `HARD_FLAGS` cap | `if any(f in HARD_FLAGS): composite min(0.1)` `:132-133` — `price_mention/personal_info/.../distress` | Sunny's `Netflix` talk triggers none → no cap. |
| Routing | `score>=0.80 and not flags → enqueue_send` `:817` else operator queue `:845` | Favorable to generic short lines. |

**Could scoring reward generic? Yes.** Generic `Haha, enjoy your movie marathon!` is `appropriate_length=8, natural_tone=7, contextually_aware=4, not_repetitive=6` → `6.25/10=0.62 → queue`. But Qwen scorer on `qwen2.5:3b` with `format:json` may inflate `natural_tone` for short polite replies. Live scoring values unknown without `generation_telemetry` (see §27). **Scoring could have sent Sunny's generic lines auto-approved** — not blocklisting them.

**Rejection risk:** A more natural `Before we proceed... send a pic?` → `Sure thing later...` draft would score similarly; scoring is **not the cause** of generic — it may be the **enabler** (not penalizing generic). A richer, specific Sunny reply (longer, callbacks) might score higher, but is not generated.

---

## 21. Post-Processing

| Transform | Exists for sunny draft? | Evidence |
|---|---|---|
| `strip/truncate/validate/sanitize` | **No** | `workers/llm_worker.py` has only `703 if not draft.strip()` emptiness gate, does **not** `draft=draft.strip()` — outbound keeps leading/trailing whitespace. No `sanitize`, `normalize`, `truncate` imports. |
| `THINKING_LEAK` strip | Defined, **dead** | `core/llm_provider_ollama.py:55-64 _THINKING_LEAK_PATTERNS` never invoked in `_generate_native`. Sunny's `Qwen3:4b` thinking leak `Okay, the user greeted me...` was suppressed by `qwen2.5:3b` having no `thinking` ability, so today **no thinking leak** (verified `Hey there! How's my favorite...` clean). |
| `format:json` | Yes for commerce/scoring | `core/llm_provider_ollama.py:152` when `response_mime_type=json`. |
| `price/URL/secret` | Only `commerce/deepseek_response.py:425-454` authoritative checks | Sunny's plain path has **zero** URL/price checks — `Sure thing, I can send...` not blocked. |
| `rewrite/regenerate` | **No** | No fallback draft rewrite on low score. |

---

## 22. Tip Delivery (as actually deployed today)

```
Provider API  GET https://www.dropfans.io/api/external/links  (bearer creator token)
  ↓ DropfansClient (decrypt creator encrypted_api_key)
  ↓ get_checkout_links(creator_id) → {telegram:{tip, profile}, web:{tip,...}}
  ↓ core/llm_tools.py:908-931 extract telegram.tip preferred, validates startswith http
  ↓ dedup tip:{creator}:{user}:{md5(url)[:12]} + is_send_duplicate check :937-948
  ↓ tip_content = f"If you'd like to support me, here's my tip link: {tip_url}" :950 hard-coded, LLM cannot alter
  ↓ enqueue_send → send_messages stream → _process_send_stream → Telegram
```

**Findings:**

| Question | Answer | File:Line |
|---|---|---|
| Where does tip URL come from? | DropFans `client.get_links()` via `get_checkout_links(creator_id)` | `service.py:435-462` `:906` |
| Authoritative? DropFans? | **Yes** — sole active provider contract header `service.py:1-5` | `service.py:1-5` |
| LLM can alter? | **No** — `reason` arg only, URL hard-coded | `:1110-1126 tool schema`, `:950` |
| Deterministic? Send queue direct? | **Yes** — `enqueue_send` directly inside tool handler | `:950-964` |
| Deduplication? | Best-effort `is_send_duplicate` 3600s TTL | `db/redis.py:73-80` + `llm_tools.py:940` |
| Creator isolation? | `auth.creator_id` scoped, not LLM arg | `llm_tools.py:137,906` |
| Hallucinated URL? | Blocked `startswith http` validation + not in tool result `data tip_url` is read-only | `:925-931` |
| Unverified | DropFans link shape not live-verified; `tip` nested under `telegram` contract inferred from client code (`FINAL_WHOLE_SYSTEM_*` 12) | — |

**Related gap:** `sender_1` README `workers/llm_worker.py:abc_XAUTOCLAIM` vs dashboard stats raw `xlen` — not tip.

---

## 23. Commerce / Authority

**Deterministic cascade is intact** (see period §4 & tip §7).

**Stance:**

| Authority | Guard | File:Line |
|---|---|---|
| Product | `get_fangate_product(creator_id,product_id)` — LLM cannot invent `product_id` | `commerce/state.py:205-209`, `commerce/execution.py:164-172` |
| Price | `price_minor` from `fangate_products` `SELECT price_minor` | `commerce/execution.py:251` |
| URL | `sales_url` from `fangate_products` or `build_checkout_url(creator,drop_id)` | `commerce/execution.py:239-249, service.py:465-477` |
| Creator | `auth.creator_id` + `resolve_single_application_creator` single-creator | `core/llm_tools.py:137`, `commerce/single_creator.py:57-86` |
| Autonomy | `autonomy_enabled` kill switch server-side | `core/config.py:109`, `workers/llm_worker.py:383-388` |
| DropFans-only | Header `Dropfans sole provider` + `service.py:1-5` | `integrations/dropfans/service.py:1-5` |
| Fangate gate | Deprecated `410 Gone` webhook | `chatbotv2/dashboard/routes/fangate.py:1146` |
| Cooldown/aftercare/handoff/dedup | `decision.py:341-474`, `relationship.py:259-291` | `commerce/dao.py:411-488 timing`, `llm_tools.py:940 dedup` |
| Send queue | `enqueue_send` + `_process_send_stream` reserve/dedup/rate-limit/ack | `chatbotv2/main.py:77-422` |

**Excessive authority found:** `propose_follow_up(reason, delay_hours)` — `core/llm_tools.py:588-701` **LLM may schedule arbitrary message up to 168 hours** with deterministic content `_LLM_FOLLOW_UP_CONTENT="Hey! Just checking in — hope you're doing well!"` (`:123`) but LLM chooses `reason` + timing in a narrow band, and **no relationship check** (only `is_blocked/do_not_auto_reply/sales_enabled`) — fan who rejected a hard sell could still receive a *follow-up* within an hour. **P2.** `propose_product_offer` / `suggest_tip` have relationship checks, `propose_follow_up` does not.

---

## 24. Dead / Dummy / Unwired Logic

| Finding | File:Line | Proof | Class | Impact |
|---|---|---|---|---|
| `aftercare_status` not wired to LLM | `memory/context_assembler.py:585` never read into `LLMContext.render` nor `context.py:216 state` | `commerce/dao.py:640 get_behavioral_feedback_context` returns `aftercare_status` correctly, `assembler:676-712` discards | **UNWIRED/DUMMY** | Post-purchase aftercare never suppresses commerce via LLM awareness |
| Hardcoded `currency USD` | `memory/context_assembler.py:340` | `db/fangate.py currency_code` read but ignored | **HARDCODED** | Low for single-currency |
| Dual context builders | `memory/context.py:137-179 build_system_prompt` + `:182-213 build_qwen3_system_prompt` + `QWEN3_TOKEN_BUDGET` vs `TOKEN_BUDGET` | `workers/llm_worker.py:522` only Qwen3 called | **DUPLICATED/PARTIALLY WIRED** | Legacy UNWIRED, tested only |
| Tip fatigue hardcoded zeros | `commerce/dao.py:558-583` `tip_suggestions_sent:0` | `commerce/relationship.py:292 tip_eligibility` never fires fatigue branch | **DUMMY** | Over-suggest tip risk (P2-13) |
| `_behavioral_store` never read | `commerce/feedback.py:21` | `grep _behavioral_store` 0 prod readers | **DEAD** | — |
| `vault/service.py:107 attach_media` stub | `vault/service.py:107-113` | `return {"media_count":0}` warning | **DUMMY** | UI link fails silently |
| `BusinessHours Africa/Nairobi` enum | `commerce/relationship.py:42-61` BusinessHours | No consumer | **UNWIRED** | — |
| `provider_override=None` | `agent/runtime.py:22` | Dead param | **UNWIRED** | — |
| `QWEN3_TOKEN_BUDGET` over-budget vs model ctx 4096 | `memory/context.py:25-30` | `default_num_ctx=4096` vram-based (live log) vs 400+200+800+200=1600 budget > small | **PARTIALLY WIRED** | May overfill small-ctx runs |
| `gpt-4` tokenizer for Qwen | `memory/context.py:13` | `tiktoken gpt-4` vs `qwen2.5` BPE ~15% off | **PARTIALLY WIRED** | Trims too early/late |
| `core/limiter.py, circuit_breaker.py` | `grep import` 0 hits | Never imported | **DEAD** | No prod rate-limit circuit beyond Gemini checker |
| `user_profiles.facts["interests"]` growth 15→ unbounded if merge bypass | `memory/profile.py:61` capped | Generally capped but emotion pref lags | **PARTIALLY WIRED** | — |
| `Tip fatigue/cooldown` guard | As above | Never fires | **DUMMY** | P2 |
| Throttled DB still fabricates cooldown=None → no commerce pause | `commerce/state.py:286-319 commerce/dao.py:475` return `hours_since:None` | Commerce sees `None` → allows offer | **BROKEN isolation** | P1 |
| `response_mime_type ignored` legacy bug | Fixed `llm_provider_ollama.py:152` now `format:json` | Was broken when qwen2.5 switched | **WIRED_BROKEN fixed** | Scorings now format:json |
| `agent canary double gate` | `ai_agent_canary_enabled + ai_runtime_mode in (agent,canary)` | Duplicate gates | **DUPLICATED** | Confusing canary activation |
| `_THINKING_LEAK_PATTERNS` dead | `llm_provider_ollama.py:55-64` defined never called | qwen2.5 has no `thinking` field but still preambles leak now | **UNWIRED** | P1 preamble persists |

---

## 25. Duplication

| Domain | Truths | Authoritative | Why / Risk |
|---|---|---|---|
| Persona | `personas` table `handlers.py:118 get_cached_user_persona`, worker inbound `persona` stream field, `build_qwen3_system_prompt`, dashboard CRUD | **DB `personas` via `get_user_persona`** | Worker ephemeral `persona` string can diverge from DB if cache stale; `ON CONFLICT(name) DO NOTHING` has no unique constraint on `name` → duplicates possible (BROKEN). |
| Conversation state | `messages+conversation_summaries+user_profiles+context_assembler LLMContext + MAX_ASSISTANT_TURNS 3 vs 4` | **Postgres messages + summaries** | Assembled `LLMContext` derived; dual token budgets & assistant caps cause history length to differ by provider path. |
| Intent | `CommerceSignals`, `derive_relationship_state`, `scoring HARD_FLAGS`, `qwen3_q1_intelligence evaluators` | **Deterministic `commerce/decision.py:decide_commerce_action`** | Signals advisory only; scoring flags duplicate hard blocks as 0.1 cap. |
| Commerce decisions | `decision.py DEFAULT_COMMERCE_POLICY` + `pipeline:decide_from_signals` + `orchestrator:orchestrate_commerce` double `decide_commerce_action` | **Decision engine** `commerce/decision.py` | Pipeline pipeline re-computes deterministically — waste but safe. |
| Provider selection | `core/config llm_provider` + `core/llm_provider get_llm_provider()` + `provider_fallback generate_with_fallback` + `llm_worker generate_draft_with_tools direct genai` + `core/gemini_client get_credential` | **Intended: `core/config llm_provider` + factory** Actual: `generate_draft_with_tools` bypasses factory → **Gemini shadow authoritative for tool calls** | **BROKEN**: tool loop never reaches Ollama even with `llm_provider=ollama` — only plain `generate_draft` does. |
| Tip | Dropfans `get_checkout_links` + `relationship.check_tip_eligibility` + `llm_tools _handle_suggest_tip` rebuild + `agent tools get_commerce_context` echo | **Dropfans API** | Three derivators, hardcoded DAO zeros → fatigue never fires. |
| Capability | `CreatorCapabilities` + `relationship derive_creator_capabilities` + `agent CommerceContext` hardcodes `has_relevant_product False` | **DB + `relationship.py`** | Agent has stale `false` hardcode. |

---

## 26. Database

### 26.1 Schema vs migration match

- Baseline `db/migrations/00000000000000_baseline.sql` mirrors `db/schema.sql` (1–1 except `schema.sql:109-114` default personas also in baseline:197).
- **Dead column families (P2 class):** `creator_integrations.fangate_account_id/webhook_id/dropfans_username/display_name` written never read (P2-7); `fangate_transactions.delivery_id/set_price/product_id` dead (P2-6); `creator_integrations.dropfans_product_id TEXT` nullable but never written by `create_offer`; `vault_media_deliveries.dropfans_media_id/vault_item_id` dead (P2-3); `messages.media_type/path` write-only (P2-4); `operators.telegram_id` never read (P2-5); `dlq_messages` orphan — actual DLQ is Redis Streams `db/redis.py:132 XADD dead_letter_queue` (P2-2, dashboard failed_sends always 0).
- **Type mismatch:** `vault_media_deliveries.creator_id INTEGER` vs `creators.id BIGSERIAL` truncation risk (P2-1); `product_id INTEGER` vs `fangate_products BIGINT` legacy naming.
- **INT vs BIGINT drift:** `users.id BIGINT` matches Telegram ID correctly (single user table, not two — design intentional).

**Code/schema mismatches:** None blocking today. Live 8151382101 `SELECT *` hits resolve; commerce_offers PK `(creator,product,day)` synthetic BIGINT IDs path always provides non-NULL product_id so nullable constraint not hit (P2-16 not a bug).

### 26.2 `funnel_stage` / lifecycle

`users.funnel_stage TEXT DEFAULT 'new'` (`schema.sql:13`). Lifecyle derivation `derive_relationship_state` computes transient `RelationshipState` per turn (`relationship.py:94`) not persisted. Funnel stage only advanced by `commerce/post_purchase:advance_funnel_to_converted` on purchase — never on pure chat, explaining `new` at 47 messages.

---

## 27. Live Infrastructure (READ ONLY)

| Layer | Check | Result |
|---|---|---|
| Migrations | `get_status` | `17 applied, 0 pending` — up_to_date |
| Personas | `SELECT name FROM personas` | `support`, `Sunny Skye (is_default=true)`, `Sunny - Sales` |
| Active user `8151382101` | `SELECT funnel_stage,message_count` | `new`, `47`, 42 messages (last 6 shown) |
| Summary | `get_latest_summary` | `None` — 42 msgs should have 2 summaries at 20,40 → **summarizer never fired** |
| Profile | `user_profiles.facts` | Populated: `location: at home, interests:[fun,work,chat,popcorn,Netflix,...], occupation:employee, sexual activities, communication_style:casual, mentioned_topics: 15 topics` — `at home` extracted via `inferred` confidence |
| DB config drift | `.env:37-39` vs `core/config.py:90` | Runtime `qwen2.5:3b`, code default `qwen3:4b` — live Ollama warm on `qwen2.5:3b`, Caddy path `200` in `5.67s` |
| Redis streams | `ensure_consumer_group` `INBOUND llm_workers` + `SEND send_workers` | Existing |

Report: **LIVE INFRASTRUCTURE AVAILABLE** (Postgres + Redis). Generation path: `llm_provider=ollama/qwen2.5:3b` healthy, fallback `Gemini 503` only on oversubscribed deepseek path.

---

## 28. Transcript Forensics — Sunny 10 turns, line-by-line

For each inbound:

| # | Fan → Sunny (observed) | Likely failure | `AVAILABLE CONTEXT → MODEL PROMPT → GENERATED → POST → FINAL` | Code cause |
|---|---|---|---|---|
| 1 | Fan `good. you?` → Sunny `I'm good too, Sunny Skye here! What's been up...` | **Repeated identity + generic question** | System re-asserts `You are Sunny Skye ...` every turn (`context.py:200`) despite prior `Sunny Skye!` in history → LLM obeys latest system priority | `memory/context.py:405`, `chatbotv2/handlers.py:118` (persona every turn) |
| 2 | `Nothing much, work majorly` → `Oh work can be tough... How's your day at the office?` | **Context loss + repetitive question** | Fan said `work majorly` but system only renders `PROFILE: at home` + `work` interest, not *current mood*; history capped to 3 assistant turns keeps last question shape, LLM repeats template `How's your day?` | `context.py:443,447`, scoring prompt rewards `natural_tone` |
| 3 | `I'm at home today, i'm off on saturdays` → `Ah nice! Enjoying some down time. What do you have planned...` | **Context loss (ignored off-work fact)** | `location: at home` was just extracted *after* prior turn (`post_process` lag) — not yet in prompt for this turn (1-turn delay `memory/profile.py:140` 10-msg window + async `create_task`). LLM asks what it already knows. | `workers/llm_worker.py:881`, `profile.py:140` |
| 4 | `Netflix and lots of popcorns, haha` → `Sounds like a cozy night in. Haha, enjoy your movie marathon!` | **Recycling `haha`, zero initiative** — acknowledges but **asks no new hook**, leaves fan to drive (`what are you upto?`) | `No filler words (haha, lol)` rule (`context.py:212`) exists but persona history **reinforces** `haha` via last 3 assistant turns in context; `2-4 sentences` `:206` satisfied at 1 sentence + affirmation → LLM lazy. | `build_qwen3_system_prompt:206-213` weak enforcement |
| 5 | `For sure \n what are you upto?` → `Not much here, just hanging out. What about you? Anything fun planned?` | **Unbounded persona fabrication + repetitive double-question** — Sunny invents `hanging out` with zero self-knowledge (no creator hobby payload), repeats `What about you?` / `Anything fun?` template second time | No `CREATOR_PROFILE` in prompt (`§13`), `recommended TIME: ...` :238 only fan facts, fan-asked is implicit but not signalled via `fan_asks_question` (unwired for LLM). | `§12 UNBOUNDED FABRICATION` |
| 6 | `Just feeling horny lol` → `Haha, sometimes the simplest things can be the best! How about we watch a movie together?` | **Tone break + context collapse** — fan escalated to sexual (`horny`) but Sunny ignored escalation, pivoted to generic deflected `movie together` replaying prior topic instead of escalating teasing or handling boundary | No `escalation_level` / `mood` in prompt; `CommerceSignals fan_asks_question/has_commercial_intent` not rendered (`§10` dead). Only raw `horny` text visible — Qwen prior too weak to infer. Scoring flags `price_mention` etc. don't fire on `horny`, no `HARD_FLAG` cap. | `memory/context.py:216-278`, `commerce/signals:382` unwired |
| 7 | `A naughty movie?` → `Oh, that sounds exciting! What kind of movie...` | **Generic probe again; misses spicy continuation** — no rapport callback to `cozy night`, no PPV-aware tease | Same: no response-mode selection, no `conversational_phase` (`signals_to_context:360` not surfaced) | `§10 DEAD` |
| 8 | `Before we proceed, mind sharing a pic of you?` → `Sure thing, I can do that. How about I send one later when I'm ready? Let's keep things casual for now.` | **P0 Capability hallucination** — promises photo media but **no `send_photo` tool exists** (`core/llm_tools.py:104-112` read-only). No photo enqueued. Fan will wait forever. | Model never told `CAPABILITIES: photo=no`; `TOOL_AUTHORITY_PROMPT` omits media declaration (`§13`); `score_draft` not secret/URL checked for media. | `core/llm_tools.py:104`, `chatbotv2/main.py:259` media path not reachable |
| 9 | *(implicit after)* fan waits for pic → no delivery, momentum lost | **Action grounding failure** — if fan purchased, `commerce/post_purchase.py:40` follow-up is generic `Hey! Just checking in...`, not photo — mismatch between promise and system capability | Vault media is operator/dashboard `vault/service.py:23` only, not LLM-triggered. | `§13` |

---

## 29. Conversation Quality Scorecard

Score `0–5` for architecture (not model alone). Evidence required.

| # | Dimension | Score | Evidence |
|---|---|---|---|
| 1 | Natural introduction | `1/5` | Re-introduces `Sunny Skye here!` every `new`-funnel turn (`memory/context.py:405` every message). No `introduced_once` flag. Past `Sunny Skye here!` in history does not suppress next system re-assertion. |
| 2 | Identity continuity | `2/5` | Persona `Sunny Skye` itself is wired (`handlers.py:118` cache 600s), but identity *state* (have we introduced?) is dead — see 1. Have we introduced? inferred only via history text, overruled by system priority. |
| 3 | Context retention | `3/5` | `get_recent_messages 20 / 800tok / 3 assistant` retains short transcript (7 msgs) fully, but token miscount `gpt-4` tokenizer on Qwen (`context.py:13`) and dropped `Creator: Bella` commerce line (`context.py:261`) and duplicate `"good. you?"` ([]). `at home Saturdays` fact 1-turn stale. |
| 4 | Topic continuity | `2/5` | History is raw text only; no `open topics` / `thread stack`. `work → office` context loss, `Netflix/popcorn → movie marathon` recycle. `RELATIONSHIP: new` static. |
| 5 | Memory utilization | `2/5` | Fan profile **is** populated (`at home`, 9 interests, 15 topics) and **is** in prompt (`PROFILE: at home, fun...` → `#include saturdays`), but LLM asks `What do you have planned for Saturday?` anyway — memory wired, not operationalized (`Use PROFILE facts to avoid asking...` instruction missing). Summaries `None` at 42 msgs (`profile.py:140` lag), retrieval **dead** for Qwen3 (`context.py:346` not called). |
| 6 | Persona utilization | `2/5` | `Sunny Skye` instructions short, generic, **no self-hobbies** → `what are you upto?` forces `UNBOUNDED FABRICATION` (`§12`). No `Sunny's interests: ...` block. Persona static, not funnel-conditional. |
| 7 | Conversational initiative | `2/5` | Every reply ends with `?` (acknowledge → statement → question). No `response mode` (acknowledge vs tease vs share) selection. Persona + rule `Ask follow-up ...` reward questions. |
| 8 | Question diversity | `1/5` | 8/9 responses end with `?`. No `question budget`, no `last_question` tracker, no `COUNT("?")` in `scoring.py`. `What's been up? / How's your day? / What do you have planned? / What about you? / What kind of movie?` template. |
| 9 | Response diversity | `2/5` | `Haha` + `Oh,` + `Ah` + `Sounds like` prefabs cycle every 2–3 turns. `presence_penalty 1.5` sampling only, `not_repetitive` scoring 25% weight, `_THINKING_LEAK_PATTERNS` dead, no deterministic Jaccard `>0.7` gate. |
| 10 | Emotional continuity | `1/5` | `emotional_state_recent` dead for Qwen3 (`context.py:238` whitelist drops it). Fan `work majorly` → `I'm at home Saturdays` emotional relief not reflected. No `mood` in state. |
| 11 | Tone continuity | `2/5` | Fan tone `horny → naughty` escalation invisible as feature; escalation `not` surfaced (`conversational_phase` unwired). Qwen tone defaults to curious-sweet, never playful-then-bold staircase. |
| 12 | Conversational momentum | `1/5` | No `open_loops / callbacks / follow-up hooks`. Each turn is reactive answer + new question. `what are you upto?` answered vacuous `hanging out` then immediately reverses with `What about you?` — momentum reset instead of build. |
| 13 | Callback ability | `1/5` | Could callback `Saturday off → Netflix/popcorn` but asks `What do you have planned for Saturday?` anyway. Only raw history, no extracted `open loop: fan chose netflix+popcorn, call it out later`. |
| 14 | Persona contribution | `1/5` | See 12: `what are you upto?` → `Not much here, just hanging out.` No `I love ...` self facts because no `CREATOR_PROFILE`. Fabricated, hallucination bootstrapped via history. |
| 15 | Capability awareness | `0/5` | `Sure thing, I can do that. How about I send one later...` photo promise with **no photo tool** (`core/llm_tools.py:104-112` 7 read/proposal only, `agent/tools.py:224` 8 read-only). Never told `photo:no`. **P0 false-capability.** |
| 16 | Action grounding | `2/5` | Tip is deterministically grounded (`suggest_tip → enqueue_send` with canonical URL). Text replies are **not** grounded. `vault media` operator-only. |
| 17 | Repetition prevention | `1/5` | Prompt-only `Vary sentence structure` + `presence_penalty 1.5` + offline `_jaccard >0.7` evaluators — no prod Jaccard gate before `enqueue_send`. |
| 18 | Multi-turn coherence | `2/5` | Coherent within 3 assistant turns; fragile past that. `Netflix` → `cozy movie marathon` coherent, but `office` vs `at home` miss, `work majorly` → ignored shows shallow. |
| 19 | Agent usefulness | `0/5` | `agent/` never executes (`ai_runtime_mode=legacy`, `ai_agent_canary_enabled=false`). Even if enabled, `loop.py:189` calls non-existent `provider.generate_with_tools` → always fallback apology. **DEAD**. |
| 20 | Human-likeness | `2/5` | Short sentences, warm tone succeed; repeated intro, question-loop, generic pivots, and photo promise read as bot. |

**Average: `1.55/5`.**

---

## 30. Prioritized Findings

### P0 — Can cause incorrect/unsafe production behavior

| ID | FILE:FUNCTION:LINE | CURRENT BEHAVIOR | EXPECTED | CLASS | EVIDENCE |
|---|---|---|---|---|---|
| **P0-1** | `core/llm_tools.py: - (absence) / chatbotv2/main.py:259` | LLM promises `Sure, I can send a pic` but **no `send_photo` tool exists** — offer never delivered, fan waits, trust broken, potential operator escalation/complaint | LLM must either be told `photo: no` and deflect, or a real `propose_send_media` tool enqueues vault media | `BROKEN / UNWIRED` | `core/llm_tools.py:104-112` 7 tools, `agent/tools.py:224` 8 read-only, `vault/service.py:23` operator-only, `chatbotv2/main.py:274` media path gated |
| **P0-2** | `core/scoring.py:11-18,132-133` + `workers/llm_worker.py:817` | `price_mention` etc. cap 0.1 blocks commerce auto-send, but **photo hallucination text has no `HARD_FLAG`** → scored generically → `score≥0.80` no flags → **auto-sent** | Photo promise should be a hard flag like `distress` → auto-queue for operator review, or scoring should detect `I can send a pic` string as `capability hallucination` | `BROKEN` | `core/scoring.py:21 price_mention` lumps `tip`, photo keyword absent |
| **P0-3** | `db/postgres.py:360 get_recent_messages` vs `chatbotv2/handlers.py:58,95` | Current inbound duplicated: history last entry = `"good. you?"` + appended `user_message "good. you?"` → **`["good. you?","good. you?"]`** trailing duplicate | Current message should replace, not duplicate, final history entry | `BROKEN` | `memory/context.py:380-463` (step B) + `workers/llm_worker.py:95` |

### P1 — Materially harms desired CRM behavior

| ID | FILE:FUNCTION:LINE | Finding | CLASS |
|---|---|---|---|
| **P1-1** | `memory/context.py:405` `build_qwen3_system_prompt` inside `build_qwen3_context` unconditional | Persona `You are Sunny Skye ...` re-asserted every turn → **repeated introduction** `Sunny Skye here!` at `good. you?` despite prior intro 4 msgs ago | `UNWIRED` (no introduced-once flag) |
| **P1-2** | `memory/context.py:182-213` Qwen3 system compressed | 6 anti-pattern rules in legacy collapsed to 1 line `Vary sentence structure, avoid repetition` — weak deterrent | `PARTIALLY WIRED` |
| **P1-3** | `memory/context.py:41-53` no question counter | Every turn ends `?... How's your day? / What's your... / What about you?` | `UNWIRED` |
| **P1-4** | `memory/profile.py:140` `recent[-10]` + `workers/llm_worker.py:881 post_process create_task` lag + `memory/context.py:238` state whitelist | Profile wired but **not operationalized**: `at home Saturdays` known, still asks `What do you have planned for Saturday?` | `WIRED` producer → prompt, **unwired** LLM instruction |
| **P1-5** | `memory/context.py:13` `tiktoken gpt-4` | ~15% miscount for Qwen | `PARTIALLY WIRED` |
| **P1-6** | `db/postgres.py: 47` funnel never advances (`new` at 47 msgs) | `Stage: New fan. Warm welcome.` forever — no `RETURNING / ACTIVE` distinction | `DEAD`/`HARDCODED` |
| **P1-7** | `memory/context_assembler.py:585` `aftercare_status` fetched never rendered | Chosen persona but never surfaced | `DEAD` |
| **P1-8** | `commerce/relationship.py:255 tip cooldown` harcoded zeros `dao.py:558 commerce/state.py:342 + workers/llm_worker.py:603` | Tip fatigue guard never fires — over-suggest risk | `DUMMY` |
| **P1-9** | `memory/context.py:346` `should_retrieve` lexical vs `build_qwen3_context: no retrieval` | `Netflix and lots of popcorns` never triggers retrieval, even though retrieval itself is DB-healthy (`vector_search_messages` Python cosine) | `UNWIRED` |
| **P1-10** | `agent/loop.py:185-204` signature mismatch | Agent would crash even if enabled | `BROKEN` |

### P2 — Quality / robustness

| ID | FILE:LINE | Finding | Class |
|---|---|---|---|
| **P2-1** | `vault_media_deliveries.creator_id INTEGER` vs `BIGSERIAL` | Truncation risk | `PARTIALLY WIRED` |
| **P2-2** | `dlq_messages` orphan | `failed_sends` always 0 (Redis DLQ is truth) | `DEAD` |
| **P2-3** | `dropfans_media_id/vault_item_id` never read | Dead columns | `DEAD` |
| **P2-4** | `messages.media_type/path` write-only | Never `SELECT` filtered | `WRITE-ONLY` |
| **P2-5** | `operators.telegram_id` never read | Dead | `DEAD` |
| **P2-6** | `fangate_transactions delivery/set/price/product_id` dead | Unused | `DEAD` |
| **P2-7** | `creator_integrations fangate_account/webhook/dropfans_username` dead | Unused | `DEAD` |
| **P2-8** | `core/config.py` vs `chatbotv2/config.py` Settings duplication | Drift risk | `DUPLICATED` |
| **P2-9** | `core/config.py:42-45 gemini_*` stale under Ollama | Hardcoded Gemini names in payload now guarded but still in config | `DUPLICATED` |
| **P2-10** | `scheduled_messages.creator_id` isolation WIRED but `funnel` not | Minor | `WIRED` |
| **P2-11** | `aftercare_status` migration index correct but never enforced by LLM | Unwired enforcement | — |
| **P2-12** | `_THINKING_LEAK_PATTERNS` dead | Preamble persists though qwen2.5 has no `thinking` field | `DEAD` |
| **P2-13** | `_behavioral_store` DEAD | Append-only never read | `DEAD` |
| **P2-14** | `TOKEN_BUDGET vs QWEN3_TOKEN_BUDGET` duplication, legacy UNWIRED | Two systems, one production | `DUPLICATED` |
| **P2-15** | `conversation_summaries` lag (`None` at 42 msgs) | `maybe_summarize every 20` never fired | `PARTIALLY WIRED` |

### P3 — Cleanup / architectural debt

Low. `BusinessHours Africa/Nairobi` enum unused (`relationship.py:42`), `provider_override=None` dead (`runtime.py:22`), `CommerceSignals` `model_fields == SIGNAL_FIELDS` drift (known), dashboard `operator_queue` `flags` display raw JSON, `dlq_messages` vs `dead_letter_queue` naming, `webhook_id` dead. **Do not fix now.**

---

## 31. Root-Cause Map

```
[Every turn re-asserts identity]
  workers/llm_worker.py:522 + memory/context.py:405 build_qwen3_system_prompt({persona},…)
    → P1-1 repeated "Sunny Skye here!"

[Compressed history limits + tokenizer mismatch + no retention labels]
  memory/context.py:443 limit 20 + :447 MAX_ASSISTANT_TURNS=3 + :13 gpt-4 tokenizer
    → P1-5 / P1-9 context loss, Office vs at home miss
    + duplicate trailing "good. you?" (P0-3)

[Profile is fan-only; persona is static; no self-knowledge]
  memory/profile.py:11-25 PROFILE_SCHEMA fan-only
  + personas table free-text instructions
  + memory/context.py:238-245 renders fan profile only, no CREATOR_PROFILE
    → P1-4 profile asks already-answered Saturday again
    + P1: what are you upto? → UNBOUNDED PERSONA FABRICATION

[Zero conversation-building architecture]
  signals_to_context:360 not surfaced (fan_asks_question)
  + no response_mode / open topics / momentum / emotional_state (all DEAD)
  + build_qwen3_state_context filtered to 5 commercial keywords (261-264)
    → P1-3 question overuse, P1-2 repetition, §10 DEAD response modes, generic Haha pivots

[Capability hallucination permitted]
  core/llm_tools.py:104 7 read/proposal only + supports_tool_calling false → no photo tool
  + no CAPABILITIES manifest in system prompt
    → P0-1 Sure thing, I can send a pic (text-only hallucination, media never enqueued)

[Scoring & post-processing do not penalize these]
  core/scoring.py 4-dim LLM-judged generic passes → operator-queue only if <0.80
  + repetitive not HARD_FLAG, no question-count, no photo-phrase cap
  + truncation/sanitize/thinking-leak dead
    → P0-2 photo promise auto-sent, P1-2/3 generic recycles

[Commerce & funding but not tone]
  commerce/relationship.py live → suppress sales during DO_NOT_PUSH, but NOT used to choose tease vs empathize

[Agent & Qwen-specific]
  agent/canary legacy false → agent never runs; even if toggled loop.py:189 generate_with_tools signature mismatch
  + model drift qwen3:4b default vs qwen2.5:3b live
  + cheap_model poison guard fixed but tokenizer still gpt-4
```

---

## 32. Recommended Fix Order (Do NOT fix yet — ordering only)

**After forensic review, we will implement in this order (each step independently verifiable):**

1. **P0-1/2 — Close media hallucination:** Inject `CAPABILITIES = {text: yes, photo/video: no}` into Qwen system prompt; add scoring `HARD_FLAG photo_promise` → auto-queue; or hard-deflect rule `If fan asks for pic → reply: I'm not able to send photos right now, let's keep chatting...` The deterministic `vault/service.py` remains operator-only — no new `send_photo` tool.

2. **P1-1 — Once-only introduction:** Add gated `if message_count==0 or get_recent_messages shows no prior Sunny-intro` → include `You are Sunny Skye`; else emit `You are sunny (friends call you sunny)` without `Sunny Skye here!`. Persist `introduced_at` or infer from history `if any assistant content contains "Sunny Skye" then suppress`.

3. **P1-3 — Question budget:** `max_questions_per_3_turns=1`, track `last_question_at` via scratch `conversation_summary` or lightweight `conversation_state.user_id → last_question_count` Redis hash. Gate `Ask one follow-up` → `Ask a follow-up only if you did not ask one in your last turn and fan's last message wasn't an answer to your previous question`.

4. **P1-4 + 11 — Wire persona self-knowledge / bound fabrication:** Structured `creator_interests/hobbies/available replies` (3–5 safe lines like `I love cozy movie nights, trying new cafes, late-night chats`); render as `ABOUT SUNNY: ...` after persona, referenced by `what are you upto?` so LLM stops inventing `just hanging out`.

5. **P1-6 — Fix funnel advancement:** Advance `users.funnel_stage` from `new → warming → engaged` at `message_count≥6, ≥20` or after first `sunny` closeness signal, not only on purchase. Expose `Stage: Engaged` genuinely so QWEN3 guidance progresses.

6. **P1-10 — Repair agent/ wire retrieval if intended:** Either formally deactivate `agent/` (delete docs saying soon-to-be-active) or fix `loop.py:189` to `provider.generate(system_instruction, user_content)` / `generate_with_history`. Only after 1–5, agent can actually improve planning vs just reformat.

7. **P2 — Dead tip fatigue (`tip_suggestions_sent` hard-zero)** — add lightweight `tip_events` counter or adopt Redis dedup key `tip:{user}` as source; else tip always `eligible`.

8. **Polish:** Wire `aftercare_status` beyond DB, fix duplicate `good. you?`, replace `gpt-4` tokenizer with Qwen count heuristic, activate actual summarizer (investigate `None` at 42 msgs), deduplicate token budgets.

Each fix requires **its own before/after adversarial suite** (see §25 already has `hard-flag capping` + `scoring fail-closed` + `tip import` + `provider fallback` skeletons).

---

## 33. Risks

- **If P0-1 is not fixed:** fans told `Sure, I can send a pic later` will churn/complain; operator sees no vault link; no remediation timer; escalation risk for OFM venue (fantasy/reality gap).
- **If P1-1/3 not fixed:** fans perceive Sunny as repetitive bot (`Sunny Skye here!` + `How's your day? → What are you upto? → How about we...?`) — low retention, seen as templated.
- **If persona stays unbounded:** `what are you upto?` vacuity spreads — once `just hanging out` is stored as `MAX_ASSISTANT_TURNS=3` history, model re-learns vacuity for 3 turns, hard to self-correct without `CREATOR_PROFILE` anchor.
- **If model drift persists:** `qwen3:4b` on disk but `.env qwen2.5:3b` can reappear as `404 model not found` after redeploy if `.env` not in image — `provider_ollama:90` `ollama_model` env var must be deployment artifact, not docs drift.
- **If agent prematurely enabled:** `agent/loop.py:189` crash → fallback apology `I apologize, but I'm having trouble...` shown to fan — worse than legacy.

---

## 34. Explicitly Unchanged Components

The following are **not part of this trigger** and remain as audited:

- **Redis Streams** (`inbound_messages llm_workers`, `send_messages send_workers`, XAUTOCLAIM 30s, DLQ `dead_letter_queue`) — verified consumer groups (`app.py:87`).
- **PostgreSQL + pgvector** — `messages`, `operator_queue`, `vault_media_deliveries`, `scheduled_messages`, `fan_segments` — migrations `17/17 applied`.
- **Telethon MTProto** (`chatbotv2/main.py`) — rate-limit `1/s burst 5`, blacklist 24h, media `photo/video/document` 100 MB gate.
- **Deterministic commerce authority** — Dropfans `get_checkout_links`, `resolve_and_run_commerce`, `decision.py PURE`, `offer_state` gating — **not being changed**.
- **Send worker / vault reservation** — atomic `reserve_delivery → send_file/caption → finalize_delivery` intact.
- **Commerce aftercare/ cooldown** — DAO timing & decision engine intact; only prompt surfacing of `aftercare_status` is deferred.
- **Tip deterministic path** — `suggest_tip` already enqueues via `is_send_duplicate` + canonical URL validation — **kept**.
- **Debounce** (`debounce_window_seconds=3`), **rate limiting** (`rate_limit_per_minute=20`), **autonomy kill switch** (`autonomy_enabled=true`).

---

## Final Summary

```
ROOT STATUS:
CONDITIONALLY READY

CURRENT CONVERSATIONAL ARCHITECTURE:
Qwen qwen2.5:3b @ https://ollama.brestalogistics.co.ke (https), think:false, temp 0.7 (provider overrides 0.85), top_p 0.8, num_predict 200 conversation / 512 scoring, legacy runtime (ai_runtime_mode=legacy, canary/shadow disabled), build_qwen3_context = system persona (Sunny Skye) + state (fan profile filtered) + recent 20 / 800tok / 3 assistant turns; plain generate_draft via Ollama (tool loop disabled for Qwen); scoring via Ollama format:json → 0.80 threshold; send via Redis Streams. No agent.

PRIMARY ROOT CAUSES:
1. Identity re-asserted every turn (no introduced-once flag) → repeated "Sunny Skye here!" (P1-1)
2. Zero conversation-building state (no open topics, no momentum, no response-mode selection, no emotional_state for Qwen) → every reply is reactive acknowledge → generic statement → question (P1-3, §10 DEAD)
3. Static, contentless persona + no creator self-facts → "what are you upto? → just hanging out" unbounded fabrication (P1-4, §12)
4. No capability contract (photo:text, photo:no never taught) → "Sure, I can send a pic later" hallucination (P0-1) that scoring does not hard-block (P0-2)
5. Duplicate tail message + history cap (3 assistant) + gpt-4 tokenizer + aftercare/tip-fatigue dead guards → context loss across turns (P0-3, P1-5, P1-8)

P0: 1 - false photo capability claim    2 - scoring does not hard-block that claim   3 - current message duplicated in context (good. you? ×2)
P1: 1 repeated identity 2 weak anti-repetition 3 question-overuse (no budget) 4 profile wired but not operationalized (asks already-answered Saturday) 5 gpt-4 tokenizer mismatch 6 funnel never advances (new at 47 msgs) 7 aftercare_status not surfaced 8 tip fatigue hardcoded zeros 9 retrieval dead for Qwen3 10 agent signature mismatch (dead path)
P2: 1 vault creator_id INTEGER truncation 2 dlq_messages orphan 3 dead dropfans_media columns 4 messages media_type write-only 5 operators.telegram_id dead 6 fangate_transactions dead cols 7 creator_integrations fangate/display dead cols 8 Settings duplication 9 gemini_* stale under Ollama 10 scheduled_messages isolation ok 11 aftercare migration ok but enforcement unwired 12 thinking-leak patterns dead list 13 _behavioral_store dead 14 token budget duplication legacy vs QWEN3 15 summary gate Never fired at 42
P3: Africa/Nairobi BusinessHours enum, provider_override=None, CommerceSignals field drift, dashboard flags raw JSON — cleanup

UNWIRED:  aftercare_status, thinking-leak patterns, retrieval (Qwen3), tip fatigue, _behavioral_store, capability manifest (photo), funnel advancement, open topics/threads, response mode, emotional_state for Qwen3, creator self-facts
DEAD:     agent/* (legacy mode), _behavioral_store, BehavioralSummary/FeedbackEventType (beyond PURCHASE_COMPLETED), vault attach_media stub, limiter/circuit_breaker, gpt-4 retrieval trigger for non-memory prompts
DUMMY:    vault attach_media (logger+0), aftercare_status rendering, tip fatigue zeros (hardcoded 0), agent relationship hardcodes (NEW/NONE zeros), BusinessHours enum
BROKEN:   self._model mutation guard fixed 2026-08-29 (tested), P0-3 duplicate tail, gpt-4 tokenizer on Qwen, funnel at 47→new, summary lag (None at 42), agent loop generate_with_tools signature mismatch
DUPLICATED: token budgets (TOKEN_BUDGET vs QWEN3_TOKEN_BUDGET), pipeline decide_from_signals re-compute, product provider mirror still fangate_products table, Settings core+chatbotv2, persona sources DB vs stream ephemeral string
UNVERIFIED: DropFans /api/external/links link shape live (inferred from client), qwen2.5:3b on VPS model inventory post-swap (confirmed local but stale default in config docs), summary cron reliability

MOST IMPORTANT FINDING:
Sunny sounds like a bot not because the model is weak, but because the architecture gives the model no conversational memory, no response-mode choice, and no self-knowledge — then asks the model to "be warm and keep conversation flowing" by ending every reply with a question. Fix the five root causes in the order of §32 and Sunny will hold a human-feeling conversation with the identical qwen2.5:3b.

DO NOT FIX YET:
YES

NEXT STEP:
[Forensic review only — no implementation performed]

CONFIRM:
- Production code changed: NO
- Database changed: NO
- Migrations changed: NO
- Runtime configuration changed: NO
- Provider changed: NO
- Canary activated: NO
- DropFans changed: NO
- Fangate changed: NO
- Tests modified: NO

FINAL DISCIPLINE:
Be forensic. Trace actual code. Do not guess. Do not trust old reports over current code. Do not infer runtime behavior from architecture diagrams. Do not classify unused-looking code without proving reachability. Do not fix anything. Purpose fulfilled: WHY SUNNY SOUNDS LIKE A BOT, WHY SUNNY REPEATS HER IDENTITY, WHY SUNNY BUILDS WEAK CONVERSATIONS, WHETHER CONTEXT/MEMORY/PERSONA/CAPABILITIES ARE WIRED, WHETHER THE AGENT HELPS, WHETHER QWEN RECEIVES THE RIGHT INPUT, WHETHER SUNNY HAS REAL CONVERSATION STATE, AND EXACTLY WHAT MUST BE CHANGED — logged above, ready for fix planning after review.
```

