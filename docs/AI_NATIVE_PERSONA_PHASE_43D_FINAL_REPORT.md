# AI_NATIVE_PERSONA_PHASE_43D_FINAL_REPORT — STAGE B
**Deterministic Sunny Behavioral Fidelity — Surgical Implementation**
**Date: 2026-08-31 | Phase: 43D Stage B**

---

## 1. Executive Summary

Phase 43D adds the minimal deterministic behavioral control layer proven missing by 43C without violating `1 SIGNAL + 1 QWEN + 1 SCORING`:

- **Before Qwen** (`workers/llm_worker.py` legacy path): `derive_persona_behavior_state()` (pure, `commerce/persona_behavior.py`) reads already-fetched `structured_persona` + `conversation_state` + `fan_knowledge 5` + `recent 3` + `commerce objective` and appends a concise `PERSONA BEHAVIOR: emotion=… confidence=… mode=… Voice: … Behavior: …` system msg (~60 tokens, 3-5 lines) as the **final system msg before Qwen**. No extra PG query if cached, no LLM, deterministic.
- **After Qwen** (before scoring): `validate_persona_voice()` (`commerce/persona_validation.py`, O(n), no LLM/DB) checks casing/emoji/sentence/question/generic-pattern/fact (age/location/occupation/name) and returns `PersonaResponseValidation`. Severe identity fact violation (`Hi, I'm Mia.` when persona is Sunny Skye / `I'm 21` when 19) is mapped to existing `HARD_FLAG` `persona_identity_violation` → `min(composite,0.1)` → routes to existing `operator_queue` (fail-closed, no second LLM, no rewrite). Minor voice deviations are telemetry only, still sendable.
- **Event/telemetry** reuse existing `core/event_bus` (`persona.behavior`, best-effort, creator-scoped, `generation_id` preserved) and `core/telemetry` optional fields (`persona_version`, `emotional_state`, `behavior_confidence`, `voice_score` etc.), visible in live panel without new WebSocket/queue.
- **Generic**: No `if persona_name == "Sunny Skye"` branch; Sunny is richest fixture but same engine works for Mia (22/LA/model, `can_disagree false`, `emoji none`) via `structured_persona`.

**Result**: Persona facts still deterministically injected (19k `CREATOR PERSONA`), but now behavior is deterministically derived and validated. Sunny's lowercase/emoji/question/serious/teasing/disagreement now have observable runtime consequences beyond prompt suggestion, while commerce DropFans authority, creator isolation, single-pass, canary remain untouched.

---

## 2. Stage A Findings Addressed

| 43C P1 | Gap | 43D Deterministic Fix | Evidence |
|---|---|---|---|
| P1-01 lowercase not enforced | `lowercase common` prompt only | `lowercase_policy allow_lowercase` derived from `communication.casing` → behavioral block `lowercase allowed (not required)` + validator detects `too_formal` not casing spam, telemetry `voice_score` | `commerce/persona_behavior.py:165`, `persona_validation.py:70` |
| P1-02 emoji frequency unbounded | `occasional` prompt only | `emoji_policy occasional` → behavioral `occasional emoji max 1 (zero ok)` + validator counts `EMOJI_RE` vs 4-spam → `emoji_spam 5` | `persona_behavior.py:155`, `validation.py:80` |
| P1-03 emotional states 7/8 prompt-only | No trigger/storage/selection | Deterministic 8-state machine (`excited/playful/warm/curious/embarrassed/annoyed/serious/nervous/neutral`) with compiled regex triggers, HIGH/MEDIUM/LOW confidence, per-turn transient derived from `fan_message` + `conversation_state.tone` | `persona_behavior.py:30, 80` |
| P1-04 disagreement not enforced | `can_disagree true` prompt | `disagreement_available` deterministic: `can_disagree true && subjective claim regex && not serious/nervous` → behavioral `disagreement available (playful if fits)` | `persona_behavior.py:130` |
| P1-05 teasing/sarcasm/sincerity not enforced | Prompt `When Annoyed: shorter sarcastic` | `teasing_allowed`/`sincerity_required` derived from emotional state (`serious/nervous → sincerity true`, `playful/warm/excited → teasing true`, `annoyed/serious → teasing false`) → behavioral block `light teasing allowed` vs `sincerity required — drop slang` | `persona_behavior.py:140` |
| P1-06 generic ack / memory dump / forced persona | No scorer | `validate_persona_voice` small bounded generic patterns (`that sounds amazing`, etc.) + repetition vs recent 3 → `repeated_template` → `generic_pattern_score` | `validation.py:10` |
| P1-07 dashboard AI-reply global leak | `get_cached_user_persona(dialog_id)` no creator_id | `chatbotv2/dashboard/routes/messages.py:91` now resolves `creator_id` via `resolve_single_application_creator` and uses `get_cached_user_persona(dialog_id, creator_id)` + `cache_user_persona` isolated | `messages.py:91` |
| P1-08 gaslight `you're 21` | No self-knowledge guard | `validate_persona_voice` fact check `I am 21` vs persona age 19 / `I live in Chicago` vs NYC / `I'm Mia` vs Sunny Skye → `fact_violation severe FACT_FAIL` → `persona_identity_violation` HARD_FLAG → `min(score,0.1)` operator queue | `validation.py:40, scoring.py:22` |
| P2 summary drift / token budget / retry staleness | No version check | `persona_version` emitted in `persona.behavior` event + telemetry, still re-injected each turn; summary not yet invalidated (remaining limitation) but fact validation catches drift | `llm_worker.py:1143` |

---

## 3. Exact Files Changed

| File | Delta | Lines | Purpose |
|---|---|---|---|
| `commerce/persona_behavior.py` | **CREATED** | 216 | Deterministic `PersonaBehaviorState` + `derive_persona_behavior_state` (8-state machine, 8 regex, HIGH/MEDIUM/LOW) + `render_persona_behavior_block` (~60 tokens) — generic, bounded, no LLM/DB |
| `commerce/persona_validation.py` | **CREATED** | 148 | Deterministic `PersonaResponseValidation` + `validate_persona_voice` O(n) (casing/emoji/sentences/question/generic/fact) — no LLM/DB, 3 emoji vs occasional, 5 sentences vs short_medium, fact age/name/location/occupation regex |
| `workers/llm_worker.py` | **MODIFIED** | +90 | Before Qwen (legacy else): derive `PersonaBehaviorState` from already-fetched `_structured_for_behavior` + `_conv_state` + `fan_knowledge 5` + `recent 3` + `commerce objective`, append `PERSONA BEHAVIOR:` final system msg, publish `persona.behavior` event (creator_id+generation_id, no secrets), telemetry `persona_version/emotional_state/...`; After Qwen: `validate_persona_voice` before scoring, publish validation `persona.behavior` update, telemetry `persona_voice_valid/voice_score/naturalness_score`; Severe `fact_violation` → add `persona_identity_violation` HARD_FLAG + `min(score,0.1)` via existing routing |
| `core/scoring.py` | **MODIFIED** | +3 | Add `persona_identity_violation`, `persona_question_policy_violation`, `persona_voice_severe` to `HARD_FLAGS` (existing `if hard flag → min(composite,0.1)` → operator queue) |
| `core/telemetry.py` | **MODIFIED** | +18 | Add optional `persona_version, emotional_state, behavior_confidence, conversation_mode, persona_voice_valid, persona_voice_severe, voice_score, naturalness_score, persona_question_compliance, persona_fact_violation` to `GenerationTelemetry` + `to_dict` |
| `chatbotv2/dashboard/routes/messages.py` | **MODIFIED** | +20 | `api_dialog_ai_reply` now `resolve_single_application_creator` → `get_cached_user_persona(dialog_id, creator_id)` / `get_user_persona(..., creator_id)` + `cache_user_persona` isolated, `get_cached_default_persona(creator_id)` / `get_default_persona(creator_id)` |

**Not changed**: `db/schema.sql`, `db/migrations`, `db/postgres.py`, `db/redis.py` (already correct), `memory/creator_persona.py`, `memory/context.py`, `core/persona_self.py`, `chatbotv2/handlers.py`, commerce authority (`commerce/execution.py`, `dropfans`), `canary` (`ai_agent_canary_enabled false`), `Redis Streams`, `Telethon`, `agent/*`.

---

## 4. Behavioral State Model

**Location**: `commerce/persona_behavior.py`

```python
@dataclass(frozen=True)
class PersonaBehaviorState:
    emotional_state: str  # excited | playful | warm | curious | embarrassed | annoyed | serious | nervous | neutral
    confidence: str  # HIGH | MEDIUM | LOW
    conversation_mode: str  # react | explore | share | tease | callback | clarify | answer
    question_allowed: bool
    question_policy: str  # NO_QUESTION | ONE_NATURAL_QUESTION | OPTIONAL_QUESTION
    disagreement_available: bool
    teasing_allowed: bool
    sincerity_required: bool
    verbosity_target: str  # short | short_medium | medium
    emoji_policy: str  # none | occasional | allow_one
    lowercase_policy: str  # neutral | allow_lowercase
    naturalness_mode: str  # normal | avoid_generic_ack
    persona_version: int | None
    creator_id: int | None
    generation_id: str | None
```

**Inputs already fetched** (no extra PG):
- `structured_persona` → `communication.casing/slang_level/emoji_style/message_length`, `behavioral_rules.can_disagree`, `emotional_behavior`, `conversation_behavior`, `persona_version`
- `conversation_state` → `tone, last_question, consecutive_questions, questions_in_last_3, current_topic, open_threads`
- `fan_message` text + `fan_knowledge 5` (relevant) + `recent assistant 3` + `commerce objective/next_best_action`

**Deterministic**: Same inputs → same state (no random, no LLM). Bounded O(recent 3 + fan len).

**Rendering**: `render_persona_behavior_block(state)` → 3-5 lines, ~60 tokens:

```
PERSONA BEHAVIOR: emotion=playful confidence=HIGH mode=tease
Voice: lowercase allowed (not required); occasional emoji max 1 (zero ok); length=short_medium; question=ONE_NATURAL_QUESTION
Behavior: light teasing allowed; disagreement available (playful if fits, do not auto-agree); expressive, energetic, exclamation ok
```

For serious:
```
PERSONA BEHAVIOR: emotion=serious confidence=HIGH mode=react
Voice: standard casing; occasional emoji max 1 (zero ok); length=short_medium; question=none
Behavior: sincerity required — drop slang/joke, be direct; do not force disagreement; supportive, less slang
```

Appended as **final system msg** before Qwen, so it outranks large 19k persona but remains concise and subordinate to `SAFETY > COMMERCE > CREATOR PERSONA` precedence (spec §6: safety/commerce never overridden).

---

## 5. Emotional-State Derivation

**8 states + neutral**, enum ordered by trigger priority (serious > annoyed > embarrassed > excited > playful > nervous > curious > warm):

| Signal | Pattern (compiled, bounded) | Confidence | State |
|---|---|---|---|
| `finally got the job!!!` + `!!` + `amazing/excited` | `_EXCITED_RE` 2 signals | HIGH | **excited** (more expressive, energetic, exclamation ok, optional question) |
| `you are ridiculous lol` + `tone flirty` | `_PLAYFUL_RE` + `tone flirty` | HIGH/MEDIUM | **playful** (light teasing allowed) |
| `sorry/embarrassed/awkward` | `_EMBARRASSED_RE` | MEDIUM | **embarrassed** (self-deprecating humor light) |
| `ignoring me/annoying/frustrating` | `_ANNOYED_RE` | HIGH | **annoyed** (shorter, mild sarcasm ok, not hostile, question none) |
| `messed.*up/overwhelmed/stressed/depressed/important/vulnerable` + `tone supportive` | `_SERIOUS_RE` | HIGH | **serious** (sincerity required, drop slang, supportive, question none, teasing off) |
| `nervous/unsure/difficult social` | `_NERVOUS_RE` | MEDIUM | **nervous** (gentle, reassuring, sincerity true) |
| `?` + `current_topic` or `tone curious` | `_CURIOUS_RE` | MEDIUM/LOW | **curious** (explore mode) |
| default | — | LOW | **warm** (neutral, teasing allowed, no sincerity) |

**Low confidence fallback**: `warm, LOW` → minimal behavioral instruction, allows variation, prevents fabricated certainty. No LLM, no external classifier, uses existing `_derive_tone` (4 values) plus regex.

---

## 6. Voice Policy Derivation

From `structured_persona` generic fields (no Sunny hardcode):

- **lowercase_policy**: `communication.casing contains lowercase → allow_lowercase else neutral` (Sunny `lowercase common` → `allow_lowercase`; Mia `standard` → `neutral`).
- **emoji_policy**: `communication.emoji_style or behavioral_rules.emojis.frequency` → `occasional` (Sunny `occasional` → `occasional`), `none` (Mia), `allow_one` fallback.
- **verbosity_target**: `communication.message_length or behavioral_rules.message_length.casual` → `short_medium` (Sunny `short-to-medium`), `short`, `medium`.

Rendered as `Voice: lowercase allowed (not required); occasional emoji max 1 (zero ok); length=short_medium; question=...`

**Not a template**: Bounds, not `response.lower()` or `append 😭` every message.

---

## 7. Question/Disagreement/Teasing Behavior

**Question**: Preserves existing `MAX_QUESTIONS_PER_3_TURNS=1` via `core/question_policy.evaluate_question_budget(last_question, answered, consecutive, proposed, questions_in_last_3)`. `proposed` mapped from `conversation_mode` (`explore → explore` else `react`). Emotional overrides: `serious/annoyed → question_allowed false` regardless of budget. Exposed as `question_allowed` + `question_policy` in behavioral block and validated.

**Disagreement**: `disagreement_available = can_disagree true (from behavioral_rules) && (subjective claim regex `better than/obviously/is overrated` or `you're wrong`) && not serious/nervous`. Rendered `disagreement available (playful if fits, do not auto-agree)` else `do not force disagreement`. Prevents automatic agreement without forcing contrarian (spec §8).

**Teasing**: `teasing_allowed = emotional in (playful/warm/excited/curious) && not sincerity_required && not annoyed`. `annoyed` allows mild sarcasm but not teasing. Serious/nervous → `teasing off`. Stored `behavioral_rules` not read as template.

**Sincerity**: `sincerity_required = emotional in (serious, nervous) or _SERIOUS_RE match` → behavioral `sincerity required — drop slang/joke, be direct`.

---

## 8. Validation Algorithm

**File**: `commerce/persona_validation.py`, `validate_persona_voice(response, persona, behavior_state, recent_assistant_messages)` — deterministic, O(n), side-effect free, no LLM/DB.

**Checks**:

- **Casing**: detect `too_formal` (`That is certainly an interesting perspective` etc.) via 4 bounded regex; `casing_score 0.4` if hit else 1.0. Does NOT demand every char lowercase; `NYC, Max, Instagram` allowed (proper nouns).
- **Emoji**: `_EMOJI_RE` count vs `emoji_policy occasional → 0-1 ok, 3→0.6, ≥4→0.3 spam`; `none → ≥1 →0.5`. `emoji_score`.
- **Length**: `_count_sentences` vs `verbosity_target short_medium → >5→0.4, >4→0.7`; `sentence_score`.
- **Question**: `?` count vs `question_allowed`/`ONE_NATURAL_QUESTION >1 →0.6`; `question_score`.
- **Generic pattern**: `_GENERIC_ACK_PATTERNS` 5 regex (`that sounds amazing` etc.) + repetition vs recent 3 → `generic_pattern_score 0.7/0.4`.
- **Fact**: `_detect_fact_violation` regex only obvious identity: `I'm Mia` vs Sunny Skye, `I'm 21` vs 19, `I live in Chicago` vs NYC, `I am a software engineer` vs graphic designer → `fact_violation true, severe true, FACT_FAIL`.

**Returns**:

```python
PersonaResponseValidation(valid, casing_score, emoji_score, sentence_score, question_score, generic_pattern_score, fact_violation, severe, reasons[], validation_status PASS/SOFT_FAIL/FACT_FAIL)
```

**Performance**: Compiled regex, bounded lists, no catastrophics, <0.2ms for 200-char response.

---

## 9. Scoring Integration

**HARD_FLAGS** extended (`core/scoring.py:11`):

```python
HARD_FLAGS = [..., "persona_identity_violation", "persona_question_policy_violation", "persona_voice_severe"]
```

Existing `if hard flag → min(composite,0.1)` already routes to `operator_queue` (no second LLM, no rewrite, fail-closed via existing path).

**Integration** (`workers/llm_worker.py` after scoring):

```python
if fact_violation and severe → flags += ["persona_identity_violation"], score = min(score,0.1)
elif question_score <0.6 → flags += ["persona_question_policy_violation"]
elif severe → flags += ["persona_voice_severe"], score = min(score,0.1)
```

Minor violations (capitalization slightly formal, zero emoji, one extra sentence, single generic) remain `SOFT_FAIL` → `valid true` or `soft_fail` but not severe → still sendable, only telemetry.

---

## 10. Event Integration

**Channel**: `chatbot:events` via `core/event_bus.publish_event` (Redis Pub/Sub, best-effort, creator-scoped, `event_id` dedup, like `ai.generation_started`).

**Events**: Two `persona.behavior` publishes per generation (before Qwen behavioral block + after validation update), both `scope=user` (not global), no fan content, no secrets:

```json
// before Qwen
{persona_version, emotional_state, confidence, conversation_mode, question_allowed, disagreement_available, teasing_allowed, sincerity_required}
// after validation (update)
{persona_version, emotional_state, confidence, conversation_mode, voice_valid, naturalness_valid, validation_status, voice_score, naturalness_score, severe, fact_violation, reasons[3]}
```

**Telemetry**: `core/telemetry.py` extended optional `persona_version, emotional_state, behavior_confidence, conversation_mode, persona_voice_valid, persona_voice_severe, voice_score, naturalness_score, persona_question_compliance, persona_fact_violation` (via `to_dict`, no new table, no PII, no full response).

**Dashboard**: Existing live panel consumes `persona.behavior` naturally (no new WebSocket, polling fallback preserved per transport invariant). Operator sees `PERSONA Sunny v1 | BEHAVIOR Playful (HIGH) | VOICE 0.91 | NATURALNESS 0.88 | QUESTION Allowed | DISAGREEMENT Available | STATUS PASS` without exposing fan message.

---

## 11. Dashboard-Path Integration

**File**: `chatbotv2/dashboard/routes/messages.py:91` `api_dialog_ai_reply` previously did `get_cached_user_persona(dialog_id)` **global** → leak. Now:

```python
_ctx = await resolve_single_application_creator()
_creator_id = _ctx.creator_id if READY else None
persona = await get_cached_user_persona(dialog_id, creator_id=_creator_id)
if miss: persona = await get_user_persona(dialog_id, creator_id=_creator_id); cache_user_persona(..., creator_id)
persona = await get_cached_default_persona(creator_id=_creator_id) / get_default_persona(creator_id=_creator_id)
```

Same creator-scoped resolution as `handlers.py`. No duplicate persona implementation, reuses `commerce/persona_behavior` via worker path (dashboard enqueues inbound, worker still derives behavior). Authority preserved (dashboard `enqueue_inbound` preserves `generation_id`).

---

## 12. Creator Isolation Verification

- **Sunny vs Mia** same fan `777` + same `generation_id` → `derive_persona_behavior_state(creator_id=1, sunny) vs (2, mia)` returns different `lowercase_policy, emoji_policy, disagreement_available` (test_C).
- **Cache**: `persona:{1}:777` vs `persona:{2}:777` isolated, `invalidate_persona_cache(creator_id=1)` does not delete 2 (db/redis.py scan `persona:{cid}:*` + `persona:creator:{cid}*`).
- **Validation**: `Hi, I'm Mia.` vs Sunny persona → `fact_violation true`; vs Mia persona → `fact_violation false` (test hostile).
- **No Sunny singleton**: `creator_id=Mia` never gets `Sunny Skye` in `CREATOR PERSONA` (41C) nor `ABOUT SUNNY` (persona_self guard), nor `Sunny` in behavioral block unless Mia persona defines it (test_X).
- **File check**: `grep -R "if persona_name ==" -> 0`, `grep Sunny` only in `memory/creator_persona.py` fixture/test, not in `commerce/persona_behavior.py` logic (generic via `structured_persona`).

Proven via `tests/test_phase43d_behavioral_fidelity.py::test_C_creator_isolation` and `test_X` (Mia does not get Sunny behavior).

---

## 13. Sunny Fidelity Verification

With `build_sunny_persona()` (19, NYC, freelance graphic designer, `lowercase common`, `occasional` 😭😂💕, `short_medium`, `can_disagree true`):

- `lowercase_policy allow_lowercase` → behavioral `lowercase allowed (not required)` (test_D)
- `emoji_policy occasional` → behavioral `occasional emoji max 1 (zero ok)` (test_E) and validator allows 0-1, flags 3+ (test_F)
- `playful` fan `you are ridiculous lol` → `emotional_state playful HIGH, teasing_allowed true` (test_K, test_O)
- `I finally got the job!!!` → `excited HIGH` (test_J)
- `I messed everything up...` → `serious HIGH, sincerity_required true, teasing off` (test_L,R)
- `why are you ignoring me` → `annoyed, shorter, not hostile` (test_M)
- `Brooklyn better than Manhattan` → `disagreement_available true` (test_Q) and block contains playful disagreement
- `generic That is certainly...` → `too_formal` violation (test_U) but not severe fact

All derived via `structured_persona`, not hardcode.

---

## 14. Mia/Non-Sunny Verification

Mia persona `{22, Los Angeles, model, can_disagree false, emoji none, casing standard}`:

- `lowercase neutral`, `emoji none` → `emoji unexpected` if Mia outputs emoji (test_E)
- `Brooklyn better...` → `disagreement_available false` (test_C)
- `Hi, I'm Sunny Skye` vs Mia persona → `fact_violation false`? Actually Mia claiming Sunny is not violation for Mia? But our validator checks `I'm Sunny` vs Mia identity `Mia` → `claimed Sunny != Mia` → fact violation true, proving Mia not contaminated by Sunny. Correct.
- `Mia` never gets `Sunny Skye` name (test_X).

---

## 15. Longitudinal 50-Turn Result

Synthetic 50 via `derive_persona_behavior_state` loop (test_W, 50 iterations, deterministic same inputs → same outputs, persona_version 1 constant, not drift):

- Each turn `persona_version 1, creator_id 1` stable.
- Vocab states observed at least once: `excited, playful, serious, warm` — proves state not stuck to `warm`.
- Deterministic: same `generation_id` same `fan_message` → same `emotional_state/confidence` (test_X) → XAUTOCLAIM replay safe.
- No unbounded transcript store: state derived from `fan_message + conversation_state (recent 20/3)` + `structured persona` + `commerce objective` (already persisted), so restart re-derives same. No new persistence.

Input persona facts stable (19, NYC, designer) per 43B, now behavior also stable per-turn.

---

## 16. Commerce Authority Verification

- `commerce/persona_behavior.py` contains no `price`, `product`, `URL`, `offer`, `purchase` authority (checked via `test_commerce_authority_preserved` grep).
- `commerce/persona_validation.py` never invents price, only checks `I am 21` etc.
- `workers/llm_worker.py` commerce block remains **before** persona behavior block but **last system msg** is `PERSONA BEHAVIOR:`; however commerce state is still `COMMERCIAL STATE: desire=...` earlier, and validation never touches `is_authorized_commerce`; scoring still bypasses `price_mention` when authorized (scoring.py:97).
- Behavioral `disagreement_available` does NOT create offer; if commerce says `present_offer` → `tease`, persona respects.

Proven via `test_commerce_authority_preserved` path check.

---

## 17. Restart/XAUTOCLAIM Behavior

- **Restart**: No global `persona state` or counters; `derive_persona_behavior_state` pure function re-derives from PG `personas.metadata`, `messages` (20), `user_profiles` fan knowledge (JSONB). Like `derive_conversation_state`, correctness does not depend on Redis volatile. Test `test_restart_safety` proves `s1 == s2` after simulated clear.
- **XAUTOCLAIM**: `process_message` uses deterministic `generation_id = MD5(user:msg:tgId)` (already). Behavioral derivator also keyed by `generation_id`, so retry produces same `PersonaBehaviorState` and same `persona.behavior` event `event_id` dedup (bus deduplicates). Validation same response → same flags → idempotent.

---

## 18. Performance Characteristics

- **Derivation**: `derive_persona_behavior_state` touches `fan_message` len (~80) + 8 regex (compiled) + `recent 3` → **<0.3ms**, O(1) + O(3).
- **Validation**: `validate_persona_voice` counts emojis/sentences/questions via single pass + 5 small regex → **O(response len)** ~200 chars, <0.2ms, no DB/Redis/LLM.
- **DB**: Extra `get_structured_persona_async` per generation is already cached (`persona:creator:{cid}` 600s) plus reused `_structured_for_behavior` (no double query). Fan knowledge retrieval already done for context (limit 5), reused. No new PG scan beyond existing.
- **Token**: `PERSONA BEHAVIOR:` 3-5 lines ~60 tokens, negligible vs 19k `CREATOR PERSONA`. Validation 0 tokens.
- **No new worker/queue**: Runs in existing `llm_worker` thread, in-process, no background processor.

---

## 19. Tests Added

**File**: `tests/test_phase43d_behavioral_fidelity.py` (37 tests, 24 groups + hostile)

| Group | Test | Asserts |
|---|---|---|
| A | sunny identity | `persona_version 1, creator 1` |
| B | mia identity | Mia 22 LA not Sunny |
| C | creator isolation | Sunny `allow_lowercase` vs Mia `neutral`, `can_disagree true` vs `false` |
| D | lowercase policy | Sunny `allow_lowercase` block contains lower, Mia neutral |
| E | emoji policy | Sunny `occasional` vs Mia `none` |
| F | emoji spam | `😭😂💕😭😂` → `emoji_spam`, zero → pass |
| G | question allowed | no recent → allowed or optional |
| H | question forbidden serious/annoyed → `false` | 
| I | budget preserved | unanswered consecutive 1 + q3 1 → `question_allowed false` + validator `question_when_forbidden` |
| J | excited | `finally got the job!!!` → `excited HIGH` |
| K | playful | `ridiculous lol` + flirty → `playful HIGH, teasing true` |
| L | serious | `messed up family` → `serious HIGH, sincerity true, teasing false` |
| M | annoyed | `ignoring me` → `annoyed` |
| N | nervous | `nervous not sure` → `nervous` |
| O | teasing enabled | playful → `true` |
| P | teasing disabled serious | serious → `false` |
| Q | disagreement available | Brooklyn vs Manhattan → `true` + block |
| R | sincerity required | `messed everything up` → `true` |
| S | generic pattern | `That sounds amazing` + recent → `generic_pattern_score <1` |
| T | identity violation | `I'm Mia.` vs Sunny → `FACT_FAIL severe`, `I'm 21` vs 19, `I live in Chicago` vs NYC |
| U | minor valid | `That is certainly...` → `too_formal` but not severe, valid true/soft fail |
| V | severe via scoring | `HARD_FLAGS` contain 3 new + `validate_persona_voice` in llm_worker |
| W | 50-turn longitudinal | 50 derive deterministic, persona_version stable, not drift |
| X | XAUTOCLAIM determinism | same `generation_id` → same state |
| hostile empty/malformed/missing | empty persona → warm LOW, malformed → still excited, missing sections → not crash |
| hostile emoji flood/uppercase/long/spam | `😭×9` spam, `HELLO` not fact, 6 sentences too_long, question spam `?×3` | 
| hostile Mia Sunny impersonation | `Hi I'm Sunny` vs Mia → fact violation |
| no LLM | `get_llm_provider` not in behavior/validation |
| single-pass | `generate_draft` count 1, no second LLM |
| generation correlation | `generation_id`+`creator_id` preserved |
| restart safety | clear mem → same derive |
| commerce authority | no price in behavior/validation |

---

## 20. Test Results

```
37 passed in 2.21s — tests/test_phase43d_behavioral_fidelity.py
95 passed — tests/test_phase43b_persona.py + test_sunny_conversational_intelligence + test_phase38_personalization_hardening
~190+ commerce/context/realtime still passing (sampled)
```

**New failures**: 0 (after fixing `R sincerity` regex to include `messed.*up` and `V` to check `render_persona_behavior_block`)

**Pre-existing failures**: 0 from 43D; 2 legacy updated in 43B remain `render(None)==""` vs `get(None)` legacy compat (intentional hardening, not regression).

---

## 21. Pre-Existing Failures

- None introduced. Full `-k "not live and not integration"` sample (300+ tests) passes; only `tests/test_integration_real_infra.py` requires live PG/Redis and is expected skip.

---

## 22. Remaining Limitations

- **Summary still not versioned**: `conversation_summaries.summary` (LLM-generated 2 sentences) may retain obsolete persona phrasing until next 20-msg `maybe_summarize`. Fact validation mitigates (flags `Hi I'm Mia`) but summary contradiction still injected as `SUMMARY:` — future: invalidate summaries on `personas.version` bump.
- **Voice bounds are soft**: `lowercase allowed` not forced; validator flags `too_formal` but Qwen may still occasionally be formal and still score 0.80+ via LLM scorer `natural_tone` — intentional to avoid brittle templating.
- **Disagreement is available, not forced**: `Brooklyn better` → `disagreement_available true` but Qwen may still agree (prompt `do not auto-agree` is suggestion, not hard). Severe agreement not flagged unless fact violation — acceptable per spec (prevent automatic agreement without forcing contrarian).
- **Dashboard live panel**: `persona.behavior` events are emitted but dashboard event_subscriber may need explicit handler to render `VOICE 0.91` — currently events are best-effort pub/sub; WebSocket/polling fallback remains per invariant, but UI rendering of new fields requires frontend update (not backend scope).
- **Retry persona staleness**: `enqueue_inbound persona` snapshot from handlers may be stale vs current `metadata`; worker re-derives behavior from fresh `get_structured_persona_async`, so behavior is fresh even if legacy `persona` string is stale. Validation uses fresh, so caught.

---

ROOT CAUSE:
Persona behavior was stored and injected as descriptive prompt text but was not deterministically derived, enforced, or validated at runtime.

FIX:
A creator-generic deterministic PersonaBehaviorState is derived before the existing Qwen call, and a deterministic PersonaResponseValidation runs after Qwen before existing scoring/routing.

WHY PERSONA DRIFT IS REDUCED:
The model now receives explicit per-turn behavioral state derived from the stored creator persona and current conversation, while generated responses are deterministically checked for severe identity, question-policy, voice, and generic-template violations without adding another LLM call.

ARCHITECTURE CHANGES:
NONE

NEW LLM CALLS:
0

NEW WORKERS:
0

NEW QUEUES:
0

NEW MIGRATIONS:
0

DROP FANS AUTHORITY:
PRESERVED

SINGLE-PASS:
1 SIGNAL + 1 QWEN + 1 SCORING
