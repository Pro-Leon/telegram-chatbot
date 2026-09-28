# AI-Native Canary Forensic Audit

**Date:** 2026-08-28  
**Status:** Phase 1 Complete — Forensic Current-State Audit

---

## Executive Summary

**CRITICAL FINDING:** The agent runtime is fully built but completely disconnected from production code. The `llm_worker.py` file has zero references to the agent module. The `AI_RUNTIME_MODE` setting exists in config but no production code reads it. No canary, no shadow, no conditional routing exists.

---

## Verification Results

### 1. Does llm_worker.py reach agent/runtime.py when AI_RUNTIME_MODE=agent?

**NO.** Zero references to `agent` package in `llm_worker.py`. The setting exists in `core/config.py:111` but is never read by the worker.

### 2. Does legacy mode reach existing legacy generation path?

**YES.** The only path that exists is legacy. Lines 443-469 contain the generation step:
- Commerce response (line 448)
- `generate_draft_with_tools()` (line 465)
- `generate_draft()` (line 469)

### 3. Does agent mode create a second outbound Telegram path?

**NO.** Agent mode doesn't exist in production code. No second outbound path.

### 4. Do agent results enter existing send/routing machinery?

**NOT IMPLEMENTED.** Agent runtime exists but is not called.

### 5. Does agent tool execution use only registered governed tools?

**YES.** `agent/tools.py` registers 8 tools, all READ_ONLY, all with authority validation.

### 6. Do commerce decisions still terminate in commerce/decision.py?

**YES.** Commerce pipeline unchanged. Agent cannot bypass it.

### 7. Does offer creation still use existing idempotent machinery?

**YES.** Agent cannot create offers. Only `execute_ppv()` can.

### 8. Does AUTONOMY_ENABLED remain enforced?

**YES.** Checked at `llm_worker.py:289` and `commerce/execution.py`.

### 9. Does DropFans remain the only autonomous commerce provider?

**YES.** Only provider in `commerce/execution.py`.

### 10. Is Fangate unreachable from autonomous agent path?

**YES.** Agent tools don't access Fangate.

### 11. Do memory writes remain governed?

**YES.** Agent reads memory via tools. Writes go through existing infrastructure.

### 12. Does operator handoff remain governed?

**YES.** `check_operator_handoff()` tool validates deterministically.

### 13. Can agent timeouts produce partial sends?

**NO.** Agent runtime catches all exceptions, returns structured result.

### 14. Is there implicit Gemini/Qwen/Ollama fallback?

**NO.** Provider selection explicit via `LLM_PROVIDER` setting.

### 15. Is runtime mode selection explicit?

**YES.** `AI_RUNTIME_MODE` setting in `core/config.py`.

---

## Current State: What EXISTS

| Component | Status | Location |
|-----------|--------|----------|
| `agent/` package | IMPLEMENTED | `agent/` directory, ~1,165 lines |
| `ai_runtime_mode` config | EXISTS | `core/config.py:111` |
| `agent_max_*` config | EXISTS | `core/config.py:114-116` |
| Unit tests | EXISTS | `tests/test_agent_core.py`, `tests/test_ai_native_runtime.py` |
| Documentation | EXISTS | 4 doc files in `docs/` |

## Current State: What NEEDS IMPLEMENTATION

| Component | Status | Location |
|-----------|--------|----------|
| Import of `agent` in `llm_worker.py` | MISSING | Needs `from agent.runtime import ...` |
| `ai_runtime_mode` check in `llm_worker.py` | MISSING | Needs conditional branch at lines 443-469 |
| Agent runtime call in `llm_worker.py` | MISSING | Needs `build_agent_state()` + `run_agent_runtime()` |
| Canary mode routing | MISSING | No config, no routing logic |
| Shadow comparison mode | MISSING | Documented as "not implemented" |
| Agent observability events | MISSING | Documented as "Phase 6" |
| Agent-specific metrics | MISSING | Not collected |

---

## Conclusion

The agent runtime is a **self-contained island**. The `llm_worker.py` file — the only production consumer — has not been modified. The `AI_RUNTIME_MODE` setting is a dead setting that no production code reads.

**P0 Authority Violations:** NONE

**P0 Blockers for Canary:** The agent runtime must be wired into `llm_worker.py` before any canary testing is possible.

---

*Audit completed: 2026-08-28*
