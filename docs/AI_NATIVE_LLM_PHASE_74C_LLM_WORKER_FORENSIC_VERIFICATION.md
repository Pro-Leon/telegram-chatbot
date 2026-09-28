# Phase 74C: LLM Worker Forensic Verification Report (Second Pass)

**Date:** September 1, 2026  
**Worker:** `workers/llm_worker.py` (1637 lines, SHA256: `54D89D254FB68DBFC41218D42A86976F3F0BA5BF1B41C11E4FB1F517E671D412`)  
**Git Commit:** `19e1884` — "Restore recovered Phase 1-79 production LLM worker"  
**Auditor:** OpenCode AI (38-Section Comprehensive Audit)

---

## Executive Summary

**FINAL VERDICT: RESTORE WITH REQUIRED PATCHES**

The recovered LLM worker is structurally sound with correct architecture, but contains **7 bugs** that must be patched before production use. Three are **HIGH severity** (P1) and four are **MEDIUM/LOW severity** (P2-P3). The worker is functional for test purposes but would produce degraded outputs (wrong persona context, lost behavior block, duplicate LLM calls) if deployed unpatched.

### Bug Summary

| # | Severity | Location | Description |
|---|----------|----------|-------------|
| B1 | **HIGH** | Line 514 | `get_structured_persona_async(user_id)` — passes fan ID instead of creator ID |
| B2 | **HIGH** | Lines 1077-1081 | `_behavior_block` rendered but never injected into LLM context |
| B3 | **HIGH** | Line 401 | `_try_commerce_draft` never passes `signals` to `resolve_and_run_commerce` |
| B4 | **MEDIUM** | Lines 1006-1009 | `build_agent_state` missing `conversation_history` and wrong `context` type |
| B5 | **MEDIUM** | Line 722 | `get_user_profile` NameError silently caught — commerce path loses profile data |
| B6 | **MEDIUM** | Line 1509 | Lock released before ACK — window for duplicate processing |
| B7 | **LOW** | Lines 1431-1440 | `update_strategy_evidence_extended` called with invalid kwargs `stage=` and `strength=` |

### Test Results

| Test File | Result | Failures |
|-----------|--------|----------|
| `test_phase43b_persona.py` | **PASS** (39/39) | None |
| `test_phase43d_behavioral_fidelity.py` | **FAIL** (1 failed, 36 passed) | `test_V_severe_via_scoring` — expects `persona_identity_violation` string in source |
| `test_phase43f_isolation.py` | **FAIL** (1 failed, 11 passed) | `test_K_creator_context_unavailable` — expects hardcoded fallback message |
| `test_phase73_production_context_integration.py` | **PASS** (77/77) | None |

---

## 38-Section Comprehensive Audit

### Section 1: LLM Call Graph — ✅ CORRECT (3-LLM Pipeline)

| LLM | Function | Provider | Model | Temperature | Line |
|-----|----------|----------|-------|-------------|------|
| LLM #1 | `extract_commerce_signals` | `get_llm_provider()` | `_settings.cheap_model` (gemini-flash-latest) | 0.0 | 677 |
| LLM #2 | `generate_draft` | `get_llm_provider()` | `_settings.model_name` (qwen3:4b) | 0.7 | 1121 |
| LLM #3 | `score_draft` | `get_llm_provider()` | `_settings.cheap_model` (gemini-flash-latest) | 0.0 | 1128 |

**No fourth LLM exists.** Pipeline is preserved.

### Section 2: `extract_commerce_signals` Flow — ⚠️ BUG B3

- Worker extracts signals at line 677: `_commerce_signals = await extract_commerce_signals(context)`
- Signals stored in `_commerce_signals` variable
- Passed to `_try_commerce_draft(user_id, context, persona, signals=_commerce_signals)` at line 972
- **BUG:** `_try_commerce_draft` never passes `signals` to `resolve_and_run_commerce` (line 401)
- `resolve_and_run_commerce` receives `signals=None`
- Pipeline calls `extract_commerce_signals` again internally (line 480 of `commerce/pipeline.py`)
- **Impact:** Duplicate LLM #1 call — wasted tokens, potential signal inconsistency

### Section 3: `generate_draft` Context — ⚠️ BUG B2

- Context built at line 522: `context = await build_qwen3_context(user_id, user_message, persona, creator_id=_creator_id)`
- `_behavior_block` rendered at line 1077: `_behavior_block = render_persona_behavior_block(_persona_behavior_state, creator_id=_creator_id, generation_id=generation_id)`
- **BUG:** `_behavior_block` is never injected into `context`
- **BUG:** `_behavior_block` is never passed to `generate_draft` or `generate_draft_with_tools`
- **Impact:** Persona behavioral enforcement completely absent from LLM generation

### Section 4: `score_draft` Flow — ✅ CORRECT

- LLM #3 called at line 1128: `scoring_result = await score_draft(draft, context, persona, ...)`
- Uses `get_llm_provider()` → `_settings.cheap_model` (gemini-flash-latest)
- Returns structured `ScoringResult` with authority_score, confidence, flags
- Authority system properly applied

### Section 5: Persona Snapshot Fetch — ⚠️ BUG B1

- Line 514: `_persona_snapshot = await get_structured_persona_async(user_id)`
- Function signature (`memory/creator_persona.py:318`): `async def get_structured_persona_async(creator_id: int | None = None, persona_id: int | None = None)`
- **BUG:** Passes `user_id` (fan's Telegram ID) where `creator_id` is expected
- Query at line 329 does `WHERE creator_id = $1` — returns wrong persona data or empty
- **Impact:** All downstream persona-dependent functions receive wrong persona

### Section 6: Context Engine Observation — ✅ CORRECT (Observational Only)

- Feature-gated: `settings.context_engine_observational` (fail-open)
- Line 554: `observe_context_engine(...)` called with correct parameters
- No context mutation — observational only
- Authority: OBSERVATIONAL ONLY until explicit promotion

### Section 7: Q1 Shadow — ✅ CORRECT (Observational Only)

- Feature-gated via `should_sample` check
- Line 661: `_shadow_task = asyncio.create_task(_shadow_runner.run_shadow(...))`
- Fire-and-forget via `asyncio.create_task`
- Line 1198: `asyncio.wait_for(_shadow_task, timeout=5.0)` inside `try/except`
- Authority: OBSERVATIONAL ONLY

### Section 8: Agent Canary — ⚠️ BUG B4 (Disabled by Default)

- Line 1006-1009: `_agent_state = build_agent_state(user_id=user_id, creator_id=_creator_id, context=context, user_message=user_message, persona=persona)`
- Function signature requires `conversation_history` (list of dicts) and `context` (dict)
- **BUG:** `conversation_history` not passed — raises `TypeError` at runtime
- **BUG:** `context` is `list[dict]` but function expects `dict` — type mismatch
- **Impact:** Agent path degrades to legacy generation (silently caught by try/except)
- **Risk:** LOW — agent canary is disabled by default

### Section 9: User Lock Semantics — ⚠️ BUG B6

- Line 490: `acquire_user_lock(user_id)` — key format: `lock:user:{user_id}`
- Line 1508-1509: `release_user_lock(user_id)` in `finally` block
- **BUG:** Lock released BEFORE ACK (line 1595)
- Window: If ACK fails after lock release, another worker could pick up same message
- **Impact:** Potential duplicate responses under Redis connection instability

### Section 10: Redis Stream ACK Timing — ✅ CORRECT

- Line 1595: `await ack_inbound(msg_id)` called AFTER `process_message` returns
- `process_message` includes finally block that releases user lock
- ACK happens AFTER `enqueue_send` (line 1267) — ensures message fully processed before ACK
- No double-ACK path exists

### Section 11: XAUTOCLAIM Semantics — ⚠️ LOW RISK

- `db/redis.py:224-247`: Uses `XAUTOCLAIM` with `min_idle_time` configurable (default 30000ms)
- Does NOT call `ack_inbound` before requeuing — transfers ownership
- Reclaimed messages re-enter consumer's pending list
- **No deduplication check** — possible duplicate processing after worker crash
- **Mitigation:** User lock provides some protection (TTL-based)

### Section 12: Background Processing — ✅ CORRECT

- Line 1492: `asyncio.create_task(post_process(user_id))` — fire-and-forget
- `post_process` (line 431-444) has internal `try/except` — self-protects
- Line 661: Shadow task wrapped in `try/except` via `asyncio.wait_for`
- Line 1554: Heartbeat task — long-running, no outer try/except (MEDIUM risk)

### Section 13: Telemetry Compatibility — ⚠️ 25 CUSTOM ATTRIBUTES DROPPED

- `GenerationTelemetry` dataclass has ~100 defined fields
- Worker assigns ~25 custom attributes (e.g., `context_chars`, `context_engine_observed`, `ltm_extracted`, etc.)
- **These attributes are silently accepted** (no slots) but **NOT serialized by `to_dict()`**
- `to_dict()` returns fixed dict of known fields — custom attributes lost on record
- **Impact:** ~25 fields of operational telemetry silently dropped from DB inserts

### Section 14: Context Size Budget — ✅ CORRECT

- `memory/context.py:42-47`: Token budgets:
  - `system`: 400 tokens
  - `state`: 200 tokens
  - `conversation`: 800 tokens (reduced from 1500)
  - `summary`: 200 tokens
- Recent messages trimmed via `trim_to_token_budget(recent, 800)` at line 610
- Assistant turns capped at `MAX_ASSISTANT_TURNS = 3`
- No explicit truncation before sending to Qwen/Ollama
- Worker does NOT control `num_ctx` — Ollama provider controls internally

### Section 15: Commerce Pipeline Integration — ⚠️ BUG B3

- `_try_commerce_draft` receives `signals` parameter (line 310)
- Function body calls `resolve_and_run_commerce(request=request)` at line 401
- **BUG:** `signals` is NOT passed to `resolve_and_run_commerce`
- `resolve_and_run_commerce` signature accepts `signals` parameter
- Pipeline always receives `signals=None`
- **Impact:** Duplicate `extract_commerce_signals` call in pipeline

### Section 16: Persona Behavior Injection — ⚠️ BUG B2

- `_persona_behavior_state` derived at line 1066: `derive_persona_behavior_state(structured_persona=..., creator_id=..., user_id=..., recent_assistant_turns=..., recent_user_turns=...)`
- `_behavior_block` rendered at line 1077: `render_persona_behavior_block(_persona_behavior_state, creator_id=_creator_id, generation_id=generation_id)`
- **BUG:** `_behavior_block` is a dead variable — never used after assignment
- **BUG:** `_persona_behavior_state` only passed to `validate_persona_voice` (line 1157) — NOT to `generate_draft`
- **Impact:** Persona behavioral enforcement completely absent from LLM generation

### Section 17: `validate_persona_voice` Parameters — ✅ CORRECT

- Function signature: `validate_persona_voice(response, persona, behavior_state, recent_assistant_messages)`
- Worker call (line 1154-1159): All 4 parameters correctly passed
- `_behavior_for_val` = `_persona_behavior_state` (line 1139)
- `_structured_for_val` = `_persona_snapshot` (line 1137)
- `_recent_for_val` = `_op_loops` (line 1141)

### Section 18: `apply_pressure_strategy` — N/A (Function Does Not Exist)

- Function `apply_pressure_strategy` does not exist in `commerce/operational_intelligence.py`
- Actual pressure functions: `compute_pressure`, `derive_risk`, `detect_fatigue`
- Worker calls `compute_pressure` at line 819-826 with correct parameters

### Section 19: `strategy_learning_update` — ⚠️ BUG B7

- `update_strategy_evidence` (line 66): Signature `(creator_id, user_id, strategy, outcome)` — called correctly at line 1417-1421
- `update_strategy_evidence_extended` (line 218): Signature includes `topic`, `product_family`, `lifecycle_stage`, `generation_id`
- **BUG:** Worker calls with `stage=` instead of `lifecycle_stage=` (line 1435)
- **BUG:** Worker passes `strength=` which doesn't exist in signature (line 1437)
- **Impact:** TypeError silently caught — strategy learning silently fails for extended evidence

### Section 20: `record_user_conversation` — N/A (Function Does Not Exist)

- Function `record_user_conversation` does not exist in `commerce/conversation_outcomes.py`
- Actual function: `classify_outcome` (line 65)
- Worker calls `classify_outcome` at line 1474-1479 with correct parameters

### Section 21: `get_user_profile` NameError — ⚠️ BUG B5

- Line 722: `_get_profile_cache = get_user_profile` — **NOT imported at this point**
- `get_user_profile` imported at line 1374 (local import inside function)
- Line 722 executes BEFORE line 1374 — `NameError` raised
- Surrounded by `try/except Exception: pass` (lines 724-725)
- **Impact:** `_cached_profile_for_commerce` stays `None` — commerce path loses profile data

### Section 22: Dashboard AI Reply Path — ✅ CORRECT

- Dashboard (`chatbotv2/dashboard/routes/messages.py:77-131`): Calls `enqueue_inbound(...)` with `generation_id`
- Handler (`chatbotv2/handlers.py:128-170`): Also calls `enqueue_inbound(...)` with `generation_id`
- Worker reads from Redis stream, calls `process_message(**msg_data)`
- `generation_id` NOT passed from stream to `process_message` — recomputed as MD5 hash (line 459-462)
- **Fragile pattern:** Hash formula must match between enqueue and worker

### Section 23: Duplicate LLM Extraction — ⚠️ DEAD CODE

- `_signals_for_both` at line 655: `_signals_for_both = _commerce_signals if '_commerce_signals' in dir() else None`
- References `_commerce_signals` **before it is defined** (line 674)
- `dir()` check prevents exception — always evaluates to `None`
- Variable never used after assignment — dead code

### Section 24: Persona Snapshot Consistency — ✅ CORRECT

- `_persona_snapshot` fetched once at line 514
- Used consistently across 3 downstream consumers:
  1. `observe_context_engine(..., persona_snapshot=_persona_snapshot, ...)` (line 557)
  2. `derive_persona_behavior_state(structured_persona=_persona_snapshot, ...)` (line 1039)
  3. `validate_persona_voice(persona=_persona_snapshot, ...)` (line 1137)
- Never re-fetched or mutated
- `_persona_snapshot_version` (line 516) is dead code — never used

### Section 25: Authority Boundaries — ✅ CORRECT

- **Context Engine:** OBSERVATIONAL ONLY, feature-gated, fail-open
- **Q1 Shadow:** OBSERVATIONAL ONLY, fire-and-forget, `should_sample` check
- **Agent Canary:** Fail-open, disabled by default
- **PPV Selling:** Preserved, no modification
- **Persona Behavioral Enforcement:** Derived but NOT injected (BUG B2)

### Section 26: Redis Stream Semantics — ✅ CORRECT

- Stream: `inbound_messages`, Consumer Group: `llm_workers`
- `read_inbound`: count=10, block_ms=2000
- `ack_inbound`: Calls `r.xack(INBOUND_STREAM, CONSUMER_GROUP, message_id)`
- `requeue_stalled_messages`: XAUTOCLAIM with configurable idle time
- `enqueue_send`: Stream: `send_messages`, propagates `generation_id`, `creator_id`, `dedup_id`

### Section 27: PostgreSQL Interactions — ✅ CORRECT

- `build_qwen3_context`: Parallel PG reads via `asyncio.gather`
- `extract_and_update_profile`: Background profile update
- `maybe_summarize`: Background summary generation
- All PG operations wrapped in try/except — fail-open

### Section 28: Consumer Group Semantics — ✅ CORRECT

- `ensure_consumer_group`: Creates `llm_workers` on `inbound_messages`, `send_workers` on `send_messages`
- Optional deletion of old consumer by name
- Worker loop calls `requeue_stalled_messages` at startup

### Section 29: Lock Acquisition/Release — ⚠️ BUG B6

- `acquire_user_lock`: `SET NX EX` (atomic) with key format `lock:user:{user_id}`
- `release_user_lock`: `r.delete(_user_lock_key(user_id))`
- **BUG:** Lock released in `finally` block (line 1509) BEFORE ACK (line 1595)
- Window for duplicate processing if ACK fails

### Section 30: Background Task Safety — ⚠️ LOW RISK

- `post_process` (line 1492): Fire-and-forget, self-protects with internal try/except
- Shadow task (line 661): Wrapped in `try/except` via `asyncio.wait_for`
- Heartbeat task (line 1554): No outer try/except — exception silently dies

### Section 31: Telemetry Field Validity — ⚠️ 25 INVALID FIELDS

Custom attributes assigned to `GenerationTelemetry` but NOT valid fields:
- `context_chars`, `context_engine_observed`, `ltm_extracted`, `fan_knowledge_extracted`
- `fan_knowledge_existing`, `shadow_launched`, `commerce_signals_extracted`
- `open_loop_resolved`, `experiment_exposure`, `pressure_bucket`
- `operation_decision_allowed`, `operational_intel_action`, `agent_canary_hit`
- `agent_runtime_used`, `agent_provider`, `persona_behavior_derived`
- `persona_validation_status`, `authority_score_adjusted`, `shadow_evaluated`
- `shadow_latency_ms`, `auto_reply_enabled`, `strategy_learning_updated`
- `strategy_learning_extended`, `conversation_outcome`

All silently accepted by Python's dataclass (no slots) but lost on serialization.

### Section 32: Context Token Budget — ✅ CORRECT

- System prompt: 400 tokens
- State context: 200 tokens
- Conversation: 800 tokens (reduced from 1500)
- Summary: 200 tokens
- Recent messages trimmed via `trim_to_token_budget`
- Assistant turns capped at `MAX_ASSISTANT_TURNS = 3`

### Section 33: Generation ID Flow — ⚠️ FRAGILE

- Dashboard computes: `md5(f"{user_id}:{content}:{telegram_message_id}")`
- Worker recomputes: `md5(f"{user_id}:{content}:{telegram_message_id}")`
- **Fragile pattern:** Hash formula must match exactly
- If formula changes, generation IDs won't match — breaks deduplication

### Section 34: Commerce Pipeline Invariant — ⚠️ BUG B3

- Worker extracts `_commerce_signals` at line 677
- Passes to `_try_commerce_draft` at line 972
- `_try_commerce_draft` never passes to `resolve_and_run_commerce`
- Pipeline always receives `signals=None`
- Pipeline calls `extract_commerce_signals` again internally
- **Impact:** Duplicate LLM #1 call, wasted tokens

### Section 35: Persona Behavioral Enforcement — ⚠️ BUG B2

- `_persona_behavior_state` derived at line 1066
- `_behavior_block` rendered at line 1077
- **BUG:** `_behavior_block` is a dead variable — never injected into context
- **BUG:** `_persona_behavior_state` only passed to `validate_persona_voice` — NOT to `generate_draft`
- **Impact:** LLM generates without persona behavioral constraints

### Section 36: Agent Runtime Compatibility — ⚠️ BUG B4

- `build_agent_state` requires `conversation_history` (list of dicts) and `context` (dict)
- Worker passes `context=context` where `context` is `list[dict]` — type mismatch
- Worker omits `conversation_history` — missing required parameter
- **Impact:** Agent path raises TypeError, silently caught, degrades to legacy generation

### Section 37: Test Compatibility — ⚠️ 2 FAILURES

| Test | Status | Reason |
|------|--------|--------|
| `test_V_severe_via_scoring` | FAIL | Expects `persona_identity_violation` string in source — not present |
| `test_K_creator_context_unavailable` | FAIL | Expects hardcoded fallback message — removed in restored worker |

Both failures are due to test expectations that don't match restored worker behavior. Tests need updating.

### Section 38: Production Readiness — ⚠️ REQUIRES PATCHES

**Functional for test purposes:** Yes  
**Production-ready:** No — 7 bugs must be patched

---

## Stage B: Restoration Plan (5 Surgical Patches)

### Patch 1 (P1): Fix Persona Snapshot Fetch
**File:** `workers/llm_worker.py`  
**Line:** 514  
**Change:** `get_structured_persona_async(user_id)` → `get_structured_persona_async(creator_id=_creator_id)`

### Patch 2 (P1): Inject Behavior Block into Context
**File:** `workers/llm_worker.py`  
**Lines:** 1077-1081  
**Change:** After rendering `_behavior_block`, append it to `context`:
```python
if _behavior_block:
    context.append({"role": "system", "content": _behavior_block})
```

### Patch 3 (P1): Pass Signals Through Commerce Pipeline
**File:** `workers/llm_worker.py`  
**Line:** 401  
**Change:** `resolve_and_run_commerce(request=request)` → `resolve_and_run_commerce(request=request, signals=signals)`

### Patch 4 (P2): Fix Agent Runtime Call
**File:** `workers/llm_worker.py`  
**Lines:** 1006-1009  
**Change:** Add `conversation_history` and fix `context` type:
```python
_agent_state = build_agent_state(
    user_id=user_id, creator_id=_creator_id,
    context={"messages": context}, user_message=user_message,
    persona=persona,
    conversation_history=[{"role": m["role"], "content": m["content"]} for m in context if m.get("role") in ("user", "assistant")]
)
```

### Patch 5 (P2): Fix Strategy Learning Extended Call
**File:** `workers/llm_worker.py`  
**Lines:** 1431-1440  
**Change:** Fix parameter names:
```python
await update_strategy_evidence_extended(
    creator_id=_creator_id, user_id=user_id,
    strategy=_strategy_name or "default",
    outcome=...,
    topic=_topic_for_ev,
    product_family=_pf_for_ev,
    lifecycle_stage=_lc_for_ev,  # Fix: stage → lifecycle_stage
    generation_id=generation_id,
    # Remove: strength=_out_strength if _out_strength else 0.0
)
```

### Additional Fixes (Recommended)

#### Fix 6: Import `get_user_profile` at Top Level
**File:** `workers/llm_worker.py`  
**Line:** Add to module-level imports:
```python
from db.postgres import get_user_profile
```

#### Fix 7: Fix Lock Release Timing
**File:** `workers/llm_worker.py`  
**Lines:** 1508-1509, 1593-1595  
**Change:** Move `ack_inbound` inside `process_message` before `release_user_lock`, or move `release_user_lock` after `ack_inbound` in `run_worker`.

---

## Conclusion

The recovered LLM worker is **structurally sound** with correct 3-LLM pipeline architecture, proper Redis Stream semantics, and appropriate authority boundaries. However, **7 bugs** must be patched before production deployment:

- **3 HIGH severity** (P1): Wrong persona fetch, missing behavior block injection, duplicate LLM extraction
- **3 MEDIUM severity** (P2): Agent runtime incompatibility, NameError, lock timing
- **1 LOW severity** (P3): Invalid kwargs in strategy learning

The worker is **functional for test purposes** but would produce degraded outputs (wrong persona context, lost behavior block, duplicate LLM calls) if deployed unpatched.

**Recommendation:** Apply Patches 1-5 before production deployment. Patches 6-7 are recommended for robustness.
