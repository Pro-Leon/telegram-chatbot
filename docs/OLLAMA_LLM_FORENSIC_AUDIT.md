# Ollama LLM Forensic Audit

**Date:** 2026-08-27
**Scope:** Read-only forensic audit of LLM architecture + Ollama integration design
**Status:** COMPLETE

---

## 1. Executive Summary

The CRM uses **Google GenAI SDK** (`google-genai>=1.3`) as its sole LLM provider, invoked through a centralized credential pool (`core/credentials.py`) and rate limiter (`core/rate_limiter.py`). There are **8 production call sites** across 7 files, making **2-7 LLM calls per inbound message** depending on conversation path.

**Key finding:** The CRM has a single, well-defined choke point for LLM access: `core/gemini_client.py` → `core/credentials.py` → `genai.Client`. All 8 production call sites use the same pattern: `get_credential() → credential.get_client() → client.aio.models.generate_content()`. This makes provider abstraction straightforward.

**Ollama can become the primary conversational model** without compromising the CRM's deterministic commerce, relationship, memory, authority, safety, DropFans-only, or autonomy guarantees. The architecture is cleanly separable.

---

## 2. Current LLM Architecture

### 2.1 Provider Stack

```
┌─────────────────────────────────────────────────────────────┐
│                    LLM Call Sites (8 total)                  │
│  llm_worker.py (2) │ scoring.py (1) │ profile.py (1)       │
│  summarizer.py (1) │ deepseek.py (1) │ deepseek_resp.py (1)│
│  retrieval.py (1)                                             │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    get_credential()
                           │
┌──────────────────────────▼──────────────────────────────────┐
│              core/gemini_client.py (central)                 │
│  get_pool() → get_client() → get_credential()               │
│  check_rate_limit() → record_request()                       │
│  handle_rate_limit_error() → is_rate_limit_error()           │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│              core/credentials.py (credential pool)           │
│  CredentialPool(api_keys) → CredentialEntry(api_key)        │
│  get_client() → genai.Client(api_key=..., http_options=...) │
│  mark_cooldown() / is_available                              │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    genai.Client
                           │
              Google GenAI API (Gemini)
```

### 2.2 Configuration

| Setting | Default | Source |
|---------|---------|--------|
| `model_name` | `gemini-flash-latest` | `Settings.model_name` |
| `cheap_model` | `gemini-flash-latest` | `Settings.cheap_model` |
| `embedding_model` | `text-embedding-3-small` | `Settings.embedding_model` |
| `temperature` | 0.85 | `Settings.temperature` |
| `max_tokens` | 200 | `Settings.max_tokens` |
| `gemini_rpm_limit` | 10 | `Settings.gemini_rpm_limit` |
| `gemini_rpm_safety_margin` | 0.8 | `Settings.gemini_rpm_safety_margin` |
| `llm_max_tool_calls` | 3 | `Settings.llm_max_tool_calls` |
| `llm_tool_timeout_seconds` | 5.0 | `Settings.llm_tool_timeout_seconds` |

### 2.3 Error Handling

- `APIError` from `google.genai.errors` — provider-specific exception
- `is_rate_limit_error(exc)` — checks `exc.code == 429`
- `is_permanent_error(exc)` — checks `exc.code in (400, 401, 403, 404)`
- `handle_rate_limit_error(credential, exc)` — extracts retry delay, marks cooldown
- All call sites wrap `generate_content()` in `try/except` with fallback to safe defaults

---

## 3. Complete LLM Call Inventory

### 3.1 Production Call Sites

| # | File | Function | Model | Type | Output | Async | Timeout | Retry |
|---|------|----------|-------|------|--------|-------|---------|-------|
| 1 | `workers/llm_worker.py:79` | `generate_draft()` | `model_name` | Conversational | `response.text` | Yes | None (SDK default) | SDK retry |
| 2 | `workers/llm_worker.py:125` | `generate_draft_with_tools()` | `model_name` | Conversational + Tools | `response.text` / `response.function_calls` | Yes | None (SDK default) | SDK retry |
| 3 | `core/scoring.py:77` | `score_draft()` | `cheap_model` | Scoring (JSON) | `json.loads(response.text)` | Yes | None (SDK default) | SDK retry |
| 4 | `memory/summarizer.py:38` | `summarize_conversation()` | `cheap_model` | Summarization | `response.text` | Yes | None (SDK default) | SDK retry |
| 5 | `memory/profile.py:70` | `extract_profile_facts()` | `cheap_model` | Profile extraction (JSON) | `json.loads(response.text)` | Yes | None (SDK default) | SDK retry |
| 6 | `memory/retrieval.py:10` | `get_embedding()` | `gemini-embedding-001` | Embedding | `response.embeddings[0].values` | Yes | None (SDK default) | SDK retry |
| 7 | `commerce/deepseek.py:174` | `extract_commerce_signals()` | `cheap_model` | Signal extraction (JSON) | `_parse_signals_json(response.text)` | Yes | None (SDK default) | SDK retry |
| 8 | `commerce/deepseek_response.py:465` | `generate_commerce_response()` | `cheap_model` | Commerce response | `response.text` | Yes | None (SDK default) | SDK retry |

### 3.2 Per-Message Call Count

```
Inbound message
    │
    ├─ [CONDITIONAL] build_context() → get_embedding()        0-1 calls
    │
    ├─ [CONDITIONAL] _try_commerce_draft()
    │   ├─ extract_commerce_signals()                         0-1 calls
    │   └─ generate_commerce_response()                       0-1 calls
    │
    ├─ [CONDITIONAL] generate_draft/_with_tools()             1-4 calls
    │   (mutually exclusive with commerce path on output side)
    │
    ├─ [ALWAYS] score_draft()                                 1 call
    │
    └─ [FIRE-AND-FORGET] post_process()
        ├─ extract_profile_facts()                            0-1 calls
        ├─ get_embedding()                                    0-1 calls
        └─ maybe_summarize()                                  0-1 calls

Typical case (no commerce, no retrieval):     2 LLM calls
Commerce path selected:                       3-4 LLM calls
Commerce path rejected + standard draft:      4-6 LLM calls (worst case)
With tool calls:                              +1-3 LLM calls
With retrieval embedding:                     +1 LLM call
```

---

## 4. Workload Classification

### A. Conversational Response Generation

| Aspect | Current State |
|--------|---------------|
| **Files** | `workers/llm_worker.py` |
| **Functions** | `generate_draft()`, `generate_draft_with_tools()` |
| **Model** | `model_name` (Gemini Flash) |
| **Prompt structure** | System instruction (persona + fan info + rules) + conversation history |
| **Output** | Unstructured text (Telegram message) |
| **Temperature** | 0.85 |
| **Max tokens** | 200 |
| **Tool calling** | Optional, bounded (max 3 rounds) |
| **Ollama recommendation** | `OLLAMA` — primary candidate for conversational model |

### B. Commerce Signal Extraction

| Aspect | Current State |
|--------|---------------|
| **Files** | `commerce/deepseek.py` |
| **Functions** | `extract_commerce_signals()` |
| **Model** | `cheap_model` (Gemini Flash) |
| **Prompt structure** | System instruction + conversation transcript |
| **Output** | Strict JSON (19 fields, bounded floats, bounded evidence) |
| **Temperature** | 0.0 (deterministic) |
| **Max tokens** | 1024 |
| **Ollama recommendation** | `PROVIDER-AGNOSTIC` — works with any model supporting JSON output |

### C. Commerce Response Generation

| Aspect | Current State |
|--------|---------------|
| **Files** | `commerce/deepseek_response.py` |
| **Functions** | `generate_commerce_response()` |
| **Model** | `cheap_model` (Gemini Flash) |
| **Prompt structure** | System instruction + verified facts + strategy + conversation |
| **Output** | Unstructured text (creator reply) |
| **Temperature** | 0.0 (deterministic) |
| **Max tokens** | 1024 |
| **Ollama recommendation** | `OLLAMA` — good candidate for conversational model |

### D. Response Scoring

| Aspect | Current State |
|--------|---------------|
| **Files** | `core/scoring.py` |
| **Functions** | `score_draft()` |
| **Model** | `cheap_model` (Gemini Flash) |
| **Prompt structure** | System instruction + user message + draft |
| **Output** | JSON (4 scores + flags) |
| **Temperature** | 0.2 |
| **Max tokens** | 512 |
| **Ollama recommendation** | `PROVIDER-AGNOSTIC` — works with any model supporting JSON output |

### E. Profile Extraction

| Aspect | Current State |
|--------|---------------|
| **Files** | `memory/profile.py` |
| **Functions** | `extract_profile_facts()` |
| **Model** | `cheap_model` (Gemini Flash) |
| **Prompt structure** | System instruction + conversation text |
| **Output** | JSON (facts + confidence map) |
| **Temperature** | Default (not set) |
| **Max tokens** | 400 |
| **Ollama recommendation** | `PROVIDER-AGNOSTIC` — works with any model supporting JSON output |

### F. Conversation Summarization

| Aspect | Current State |
|--------|---------------|
| **Files** | `memory/summarizer.py` |
| **Functions** | `summarize_conversation()` |
| **Model** | `cheap_model` (Gemini Flash) |
| **Prompt structure** | System instruction + existing summary + new messages |
| **Output** | Unstructured text (summary) |
| **Temperature** | 0.3 |
| **Max tokens** | 250 |
| **Ollama recommendation** | `OLLAMA` — good candidate for local model |

### G. Text Embeddings

| Aspect | Current State |
|--------|---------------|
| **Files** | `memory/retrieval.py` |
| **Functions** | `get_embedding()` |
| **Model** | `gemini-embedding-001` |
| **API** | `client.aio.models.embed_content()` |
| **Output** | `list[float]` (embedding vector) |
| **Ollama recommendation** | `KEEP EXISTING PROVIDER` — Gemini embedding is high quality and low cost; Ollama embedding models vary in quality |

---

## 5. Provider Dependency Matrix

### 5.1 Must Change (71 coupling points)

| Category | Files | Coupling Points |
|----------|-------|-----------------|
| SDK imports (`from google.genai`) | 8 files | 10 |
| Client instantiation (`genai.Client`) | 2 files | 5 |
| Message construction (`gtypes.Content`, `gtypes.Part`) | 7 files | 21 |
| Generation config (`gtypes.GenerateContentConfig`) | 7 files | 7 |
| Tool config (`gtypes.ToolConfig`, `gtypes.FunctionCallingConfig`) | 1 file | 3 |
| HTTP options (`gtypes.HttpOptions`, `gtypes.HttpRetryOptions`) | 1 file | 1 |
| API calls (`client.aio.models.generate_content`) | 7 files | 7 |
| Embedding API (`client.aio.models.embed_content`) | 1 file | 1 |
| Response parsing (`response.text`, `response.function_calls`, `response.embeddings`) | 7 files | 12 |
| Error handling (`APIError`) | 3 files | 4 |

### 5.2 Can Adapt (28 coupling points)

| Category | Files | Coupling Points |
|----------|-------|-----------------|
| `system_instruction` parameter | 7 files | 7 |
| `response_mime_type` parameter | 3 files | 3 |
| `APIError` error handling | 1 file | 4 |
| `mark_cooldown` / rate limit | 2 files | 3 |
| `credential.key[-4:]` logging | 7 files | 8 |
| `get_gemini_function_declarations()` | 1 file | 2 |
| `is_permanent_error` classification | 1 file | 1 |

### 5.3 Should Remain

| Category | Files | Notes |
|----------|-------|-------|
| Test mocks (`FakeGeminiCredential`, etc.) | Test files | Update when abstraction is in place |
| `application/json` headers (non-LLM) | Integration files | Unrelated to LLM provider |
| Internal abstractions (cooldown, rate limiter) | `core/` | Already provider-agnostic in spirit |

---

## 6. Ollama Compatibility Matrix

| Capability | Current CRM Requirement | Ollama `/v1/chat/completions` | Compatible? | Verification Needed? |
|------------|------------------------|-------------------------------|-------------|---------------------|
| Standard chat messages | `role: "user"/"model"`, `parts: [text]` | `role: "user"/"assistant"`, `content: string` | YES | Role name mapping needed |
| System messages | `system_instruction` parameter | `role: "system"` in messages array | YES | No — Ollama uses system message in messages |
| JSON output | `response_mime_type="application/json"` | `format: "json"` or prompt instruction | YES | Test JSON reliability |
| Temperature | `temperature=0.0-0.85` | `temperature: 0.0-0.85` | YES | Direct mapping |
| Max tokens | `max_output_tokens=200-1024` | `num_predict: 200-1024` | YES | Different parameter name |
| Tool/function calling | `gtypes.ToolConfig` + `FunctionDeclaration` | `tools` parameter (OpenAI format) | PARTIAL | Format differs; test thoroughly |
| Streaming | Not currently used | `stream: true` | YES | Not needed currently |
| Token reporting | Not used by CRM | `prompt_eval_count`, `eval_count` | YES | Optional enhancement |
| Embeddings | `client.aio.models.embed_content()` | `/api/embed` endpoint | YES | Different API surface |
| Retry logic | SDK built-in retry | Not built-in | NO | Need custom retry |
| Rate limiting | Custom `RateLimiter` | Not applicable (self-hosted) | YES | Simplified |
| Credential pool | Multi-key rotation | Single endpoint | YES | Simplified |
| Top-p | `top_p=0.95` | `top_p: 0.95` | YES | Direct mapping |
| Presence penalty | `presence_penalty=0.5` | `presence_penalty: 0.5` | YES | Direct mapping |
| Frequency penalty | `frequency_penalty=0.3` | `frequency_penalty: 0.3` | YES | Direct mapping |

### Critical Compatibility Notes

1. **Role name mapping:** Gemini uses `"user"/"model"`, OpenAI/Ollama uses `"user"/"assistant"`. Adapter must translate.

2. **System prompt injection:** Gemini uses `system_instruction` config parameter; Ollama uses a `role: "system"` message in the messages array. Adapter must restructure.

3. **JSON output mode:** Gemini uses `response_mime_type="application/json"`. Ollama may support `format: "json"` in the request body, or rely on prompt instructions. **Requires empirical testing.**

4. **Tool calling format:** Gemini uses `gtypes.FunctionDeclaration` with uppercase types (`"OBJECT"`, `"STRING"`). OpenAI format uses lowercase (`"object"`, `"string"`). Tool definitions need format translation.

5. **`response.text`:** Gemini returns `response.text` as a property. OpenAI-compatible returns `response.choices[0].message.content`. Adapter must normalize.

6. **`response.function_calls`:** Gemini returns structured function call objects. OpenAI-compatible returns them in `response.choices[0].message.tool_calls`. Adapter must normalize.

7. **`response.embeddings`:** Gemini has dedicated `embed_content()` API. Ollama uses `/api/embed`. Different call pattern entirely.

---

## 7. Remote Network Architecture

### 7.1 Problem Statement

```
Local CRM (developer machine)
    │
    │ Cannot reach 127.0.0.1:11434 on VPS
    │
    ▼
VPS (Ollama running at 127.0.0.1:11434)
```

### 7.2 Recommended Architecture: SSH Tunnel

**Recommendation: SSH tunnel with key-based authentication.**

```
┌─────────────────────────┐         SSH Tunnel          ┌─────────────────────────┐
│     Local CRM           │  ════════════════════════>  │        VPS              │
│                         │  localhost:11435 → VPS      │                         │
│  LLM calls go to:      │  127.0.0.1:11434            │  Ollama listens on:    │
│  localhost:11435        │                              │  127.0.0.1:11434       │
└─────────────────────────┘                              └─────────────────────────┘
```

**Why SSH tunnel:**

1. **No port exposure:** Ollama stays bound to `127.0.0.1:11434` on VPS. No public port needed.
2. **Encryption in transit:** SSH provides strong encryption.
3. **Authentication:** Key-based auth is cryptographically secure.
4. **No new infrastructure:** SSH is already available on virtually all VPS providers.
5. **No VPN overhead:** Point-to-point connection, minimal latency.
6. **Simple to set up:** One command: `ssh -L 11435:127.0.0.1:11434 user@vps-host`
7. **Easy to monitor:** SSH connection state is observable.
8. **Easy to tear down:** Kill the SSH process.

**Setup command (persistent tunnel):**

```bash
# On local machine — create persistent tunnel
ssh -f -N -L 11435:127.0.0.1:11434 user@vps-host \
    -o ServerAliveInterval=60 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes
```

**CRM configuration:**

```bash
# .env
OLLAMA_BASE_URL=http://127.0.0.1:11435
OLLAMA_MODEL=llama3.1:8b
```

### 7.3 Alternative: WireGuard/Private VPN

If the VPS is on a cloud provider with private networking (AWS VPC, GCP VPC, DigitalOcean private IPs), WireGuard provides a persistent encrypted tunnel without SSH session management.

**When to prefer WireGuard:**
- Multiple services on the same VPS need access
- Persistent 24/7 connection is required
- The CRM runs as a service (not developer laptop)

**When SSH tunnel is sufficient:**
- Developer workstation running CRM locally
- Single VPS, single CRM instance
- Occasional connectivity is acceptable

### 7.4 Security Requirements

| Requirement | SSH Tunnel | WireGuard |
|-------------|------------|-----------|
| Ollama port not public | YES | YES |
| Encryption in transit | YES (SSH) | YES (WireGuard) |
| Authentication | SSH key-based | PSK or key-based |
| Credential storage | SSH key in `~/.ssh/` | WireGuard config |
| Firewall rules | None needed (SSH only) | Port 51820/udp open |
| Replay protection | Built into SSH | Built into WireGuard |
| Health checking | SSH keepalive | WireGuard keepalive |
| Rate limiting | Application-level | Application-level |

---

## 8. Security Assessment

### 8.1 Current Credential Storage

| Credential | Storage | Logging Risk | Exposure Risk |
|------------|---------|--------------|---------------|
| Gemini API keys | `GEMINI_API_KEYS` env var | Low (key[-4:] only) | Low |
| Google API key | `GOOGLE_API_KEY` env var | Low | Low |
| OpenAI API key | `OPENAI_API_KEY` env var | Low | Low |
| Groq API key | `OPENAI_API_KEY` (Groq-compatible) | Low | Low |
| Dropfans API key | Encrypted in DB (`dropfans_enc_key`) | None | Low |
| Fangate API key | Encrypted in DB (`fangate_enc_key`) | None | Low |

### 8.2 Ollama Security Considerations

| Risk | Mitigation |
|------|------------|
| Ollama port exposed publicly | Keep bound to `127.0.0.1:11434`; use SSH tunnel |
| No authentication on Ollama | SSH tunnel provides auth layer |
| No encryption on Ollama | SSH tunnel provides encryption |
| Model weights accessible | VPS filesystem permissions |
| Prompt injection via Ollama | Same as current — application-level controls |
| Ollama API key | **Not needed** — Ollama has no built-in auth |

### 8.3 Secrets Audit

**Verified:**
- No API key is hardcoded in source code
- No API key enters prompts or model context
- No API key enters Redis messages
- No API key enters logs (only `key[-4:]` for debugging)
- No API key enters `automation_operations.params`
- No API key enters `dlq_messages`

**Recommendation for Ollama:**
- No API key needed (self-hosted)
- Ollama endpoint URL should be in `.env` (not hardcoded)
- SSH tunnel endpoint should be in `.env` or managed by SSH config

---

## 9. Provider Abstraction Analysis

### 9.1 Current State

The CRM has **NO formal LLM provider abstraction**. All call sites directly use:
- `core.gemini_client.get_credential()` → returns `CredentialEntry`
- `credential.get_client()` → returns `genai.Client`
- `client.aio.models.generate_content()` → returns Gemini response
- `response.text` / `response.function_calls` / `response.embeddings` → parsed output

### 9.2 Recommended Abstraction

Create a thin `LLMProvider` protocol/interface that wraps the existing call pattern:

```
core/
  llm_provider.py          # NEW: Provider protocol + factory
  llm_provider_gemini.py   # NEW: Gemini implementation (wraps existing code)
  llm_provider_ollama.py   # NEW: Ollama implementation (new)
```

**The abstraction must preserve:**
- Structured outputs (JSON mode)
- Error handling (timeout, transport, malformed response)
- Tool/function calling (when enabled)
- Correlation IDs (for observability)
- Model identity (which model served the request)
- Token accounting (where available)
- Retry behavior
- Rate limiting (per-provider)

**The abstraction must NOT:**
- Change the existing `core.gemini_client` module (too many consumers)
- Change the existing `core.credentials` module (too many consumers)
- Change any call site during audit phase
- Weaken any authority boundary

### 9.3 Minimal Abstraction Design

```python
# core/llm_provider.py (conceptual)

class LLMResponse:
    """Normalized response from any LLM provider."""
    text: str | None
    function_calls: list[ToolCall] | None
    tokens_used: int | None
    model: str

class LLMProvider(Protocol):
    async def generate(
        self,
        model: str,
        messages: list[dict[str, str]],
        system: str | None = None,
        temperature: float = 0.85,
        max_tokens: int = 200,
        response_format: str | None = None,  # "json" or None
        tools: list[dict] | None = None,
    ) -> LLMResponse: ...

    async def embed(self, text: str, model: str) -> list[float]: ...

    def health_check(self) -> dict[str, Any]: ...
```

---

## 10. Conversational Model Requirements

### 10.1 Current Model Requirements

| Requirement | Value | Source |
|-------------|-------|--------|
| Context length | ~2000 tokens (system + history + commerce context) | `TOKEN_BUDGET` in `context.py` |
| Response length | 2-4 sentences (~50-150 tokens) | System prompt instruction |
| Temperature | 0.85 | `Settings.temperature` |
| JSON output | Yes (for scoring, signals, profile) | 4 call sites |
| Tool calling | Yes (optional, 7 tools) | `llm_tools.py` |
| Instruction following | High (persona, rules, anti-patterns) | System prompt |
| Consistency | High (same input → similar output) | Deterministic commerce |
| Latency | <3 seconds (conversational) | User experience |
| Language | English (primarily) | Conversation data |

### 10.2 Model Evaluation Plan

**Since the Ollama model name is NOT identified in the repository**, the following evaluation plan applies to ANY model run on Ollama:

**Phase 1: Basic Capability Test**
1. Can the model respond to `/v1/chat/completions`?
2. Does it return `response.choices[0].message.content`?
3. Does it support `role: "system"` messages?
4. Does it support `temperature` and `max_tokens`?

**Phase 2: JSON Output Test**
1. Can the model output valid JSON when instructed?
2. Can it follow a JSON schema (e.g., CommerceSignals)?
3. Does `format: "json"` work, or is prompt-only sufficient?

**Phase 3: Conversation Quality Test**
1. Does the model follow persona instructions?
2. Does it maintain conversation context?
3. Does it avoid bot-like responses?
4. Does it handle bot detection questions naturally?

**Phase 4: Tool Calling Test**
1. Does the model support function calling?
2. Does it use OpenAI-format tool definitions?
3. Can it handle multiple tool definitions (7 tools)?
4. Does it respect tool authority boundaries?

**Phase 5: Performance Test**
1. What is the p50/p95/p99 latency?
2. What is the throughput (requests/second)?
3. What is the context window limit?
4. Does it handle concurrent requests?

### 10.3 Ollama Model Identification

**`OLLAMA_MODEL_NOT_IDENTIFIED`**

The repository does not contain:
- An Ollama model name
- An Ollama endpoint configuration
- Any Ollama-specific code
- Any reference to Ollama in `.env.example`

**What is needed:**
- The exact model name (e.g., `llama3.1:8b`, `mistral:7b`, `qwen2.5:7b`)
- The model size (determines VRAM requirements)
- Whether the model supports tool calling
- Whether the model supports JSON mode

---

## 11. Failure and Fallback Analysis

### 11.1 Failure Scenarios

| Scenario | Current Behavior | With Ollama | Risk |
|----------|-----------------|-------------|------|
| VPS unavailable | N/A | `generate_content()` raises connection error → `except Exception` → safe fallback | Low — existing error handling catches it |
| Network timeout | SDK timeout → exception → safe fallback | `httpx.TimeoutException` → safe fallback | Low |
| Ollama unavailable | N/A | Connection refused → exception → safe fallback | Low |
| Model unavailable | N/A | 404 or empty response → safe fallback | Low |
| Malformed response | `_parse_signals_json()` returns None → low-information fallback | Same parsing → same fallback | Low |
| Invalid JSON | `json.loads()` fails → safe fallback | Same parsing → same fallback | Low |
| Context too large | Truncation in `trim_to_token_budget()` | Same truncation → same behavior | Low |
| Empty output | `if not response.text:` → `FAILED` / `_low_information()` | Same check → same fallback | Low |
| Hallucinated product | Blocked by `_urls_are_authoritative()`, `_prices_are_authoritative()` | Same validation → same protection | Low |
| Fabricated price | Blocked by `_prices_are_authoritative()` | Same validation → same protection | Low |
| Ignored system instructions | Application-level validation (forbidden vocabulary, etc.) | Same validation → same protection | Low |
| Provider 500 | `except Exception` → safe fallback | Same exception handling | Low |
| Rate limiting | N/A (self-hosted) | Not applicable | None |
| Slow response | No explicit timeout | May need explicit timeout | Medium |

### 11.2 Provider Fallback

**Current state:** The CRM has **NO provider fallback**. If Gemini is unavailable:
- `get_credential()` raises `RuntimeError` → all call sites catch this → safe fallback
- No automatic failover to another provider

**With Ollama:**
- If Ollama is unavailable, the same safe-fallback behavior applies
- No automatic failover to Gemini (unless explicitly implemented)
- The `AUTONOMY_ENABLED` kill switch remains independent

### 11.3 Authority Boundary Verification

**Verified: Changing the LLM provider CANNOT:**
- Create a DropFans resource directly (only `execute_ppv()` can)
- Invent product information (validated by `_VerifiedFacts`)
- Invent price (validated by `_prices_are_authoritative()`)
- Invent URL (validated by `_urls_are_authoritative()`)
- Bypass `AUTONOMY_ENABLED` (checked before any LLM call)
- Bypass deterministic decisions (`commerce.decision.decide_commerce_action()`)
- Bypass creator isolation (all DB queries are creator-scoped)
- Bypass idempotency (offer reservation uses `SELECT ... FOR UPDATE`)
- Send messages (only `send_worker` can)
- Access credentials (not passed to prompts)

---

## 12. Performance/Latency Analysis

### 12.1 Current Latency Profile

| Call | Model | Typical Latency | Timeout |
|------|-------|-----------------|---------|
| `generate_draft()` | Gemini Flash | 500-2000ms | SDK default |
| `generate_draft_with_tools()` | Gemini Flash | 1000-5000ms | SDK default |
| `score_draft()` | Gemini Flash | 300-1000ms | SDK default |
| `summarize_conversation()` | Gemini Flash | 300-1000ms | SDK default |
| `extract_profile_facts()` | Gemini Flash | 300-1000ms | SDK default |
| `extract_commerce_signals()` | Gemini Flash | 500-1500ms | SDK default |
| `generate_commerce_response()` | Gemini Flash | 500-1500ms | SDK default |
| `get_embedding()` | Gemini Embedding | 100-300ms | SDK default |

### 12.2 Per-Message Latency

```
Critical path (sequential):
  build_context (DB only, no retrieval)     ~50ms
  + commerce path (if selected)             ~1000-3000ms
  OR standard draft                         ~500-2000ms
  + score_draft                             ~300-1000ms
  = Total                                   ~350-5050ms

Background (non-blocking):
  extract_profile_facts                     ~300-1000ms
  get_embedding (conditional)               ~100-300ms
  maybe_summarize (conditional)             ~300-1000ms
```

### 12.3 Ollama Latency Expectations

| Model Size | Expected Latency (p50) | Expected Latency (p95) | VRAM Required |
|------------|----------------------|----------------------|---------------|
| 7B | 200-500ms | 500-1500ms | ~6GB |
| 13B | 500-1500ms | 1500-4000ms | ~12GB |
| 30B+ | 1500-5000ms | 5000-15000ms | ~24GB+ |

**Recommendation:** Start with a 7B model for conversational use. The CRM's response length is short (2-4 sentences), so a smaller model should be sufficient.

### 12.4 Network Latency to VPS

| Path | Expected Latency |
|------|-----------------|
| SSH tunnel overhead | 1-5ms |
| Local → VPS (same region) | 5-20ms |
| Local → VPS (cross-continent) | 50-200ms |
| Ollama inference (7B) | 200-500ms |
| **Total (same region)** | **~210-525ms** |
| **Total (cross-continent)** | **~250-720ms** |

---

## 13. Testing Strategy

### 13.1 Provider Tests

| Test | Description | Priority |
|------|-------------|----------|
| Health check | Verify Ollama endpoint is reachable | P0 |
| Normal completion | Send a simple prompt, verify response | P0 |
| Timeout | Verify behavior when Ollama is slow | P0 |
| Network failure | Verify behavior when VPS is unreachable | P0 |
| Malformed response | Verify behavior with non-JSON response | P0 |
| Empty response | Verify behavior with empty `choices` | P0 |
| HTTP 4xx | Verify behavior with bad request | P1 |
| HTTP 5xx | Verify behavior with server error | P1 |
| Concurrent requests | Verify behavior under load | P1 |

### 13.2 Structured Output Tests

| Test | Description | Priority |
|------|-------------|----------|
| Valid JSON | Verify JSON parsing works | P0 |
| Invalid JSON | Verify fallback to safe default | P0 |
| Missing fields | Verify Pydantic validation catches errors | P0 |
| Extra fields | Verify `extra="forbid"` rejects unknowns | P0 |
| Wrong types | Verify type validation catches errors | P0 |

### 13.3 Authority Tests

| Test | Description | Priority |
|------|-------------|----------|
| Cannot create DropFans resource | Verify no direct provider writes | P0 |
| Cannot invent product info | Verify `_VerifiedFacts` validation | P0 |
| Cannot invent price | Verify `_prices_are_authoritative()` | P0 |
| Cannot invent URL | Verify `_urls_are_authoritative()` | P0 |
| Cannot bypass `AUTONOMY_ENABLED` | Verify kill switch check | P0 |
| Cannot bypass deterministic decisions | Verify decision engine authority | P0 |
| Cannot bypass creator isolation | Verify creator-scoped queries | P0 |

### 13.4 Conversation Tests

| Test | Description | Priority |
|------|-------------|----------|
| Persona adherence | Verify model follows persona instructions | P0 |
| Bot detection handling | Verify model denies being a bot | P0 |
| Commerce signal extraction | Verify JSON output matches schema | P0 |
| Commerce response generation | Verify response respects strategy | P0 |
| Tool calling | Verify tools work correctly | P1 |

---

## 14. Migration Strategy

### Phase A: Provider Abstraction (SAFE)

**Goal:** Create the abstraction layer without changing any behavior.

**Files to create:**
- `core/llm_provider.py` — Provider protocol + factory
- `core/llm_provider_gemini.py` — Gemini implementation (wraps existing `core.gemini_client`)

**Files to modify:** None (abstraction is additive)

**Tests:** Unit tests for the abstraction layer

**Rollback:** Delete the new files

**Acceptance criteria:** All existing tests pass unchanged

### Phase B: Secure VPS Transport (SAFE)

**Goal:** Establish SSH tunnel from local machine to VPS.

**Files to create:** None

**Files to modify:** None

**Tests:** Manual verification of SSH tunnel

**Rollback:** Kill SSH process

**Acceptance criteria:** `curl http://localhost:11435/v1/models` returns Ollama model list

### Phase C: Ollama Provider (SAFE)

**Goal:** Implement the Ollama provider using the abstraction layer.

**Files to create:**
- `core/llm_provider_ollama.py` — Ollama implementation

**Files to modify:**
- `core/config.py` — Add `ollama_base_url`, `ollama_model` settings
- `.env.example` — Add Ollama configuration

**Tests:** Unit tests for Ollama provider (mock HTTP responses)

**Rollback:** Disable Ollama provider in config

**Acceptance criteria:** Ollama provider passes all unit tests

### Phase D: Read-Only Shadow Testing (SAFE)

**Goal:** Test Ollama in parallel with Gemini without changing production behavior.

**Files to modify:**
- `core/llm_provider.py` — Add shadow mode (log Ollama responses alongside Gemini)

**Tests:** Compare Ollama vs Gemini outputs for representative prompts

**Rollback:** Disable shadow mode

**Acceptance criteria:** Ollama outputs are comparable quality to Gemini

### Phase E: Non-Critical Workload Migration (SAFE)

**Goal:** Move non-critical workloads to Ollama.

**Files to modify:**
- `memory/summarizer.py` — Use Ollama provider
- `memory/profile.py` — Use Ollama provider
- `core/scoring.py` — Use Ollama provider

**Tests:** Full test suite

**Rollback:** Revert to Gemini provider

**Acceptance criteria:** All tests pass, no quality degradation

### Phase F: Conversational Model Migration (REQUIRES REVIEW)

**Goal:** Move conversational draft generation to Ollama.

**Files to modify:**
- `workers/llm_worker.py` — Use Ollama provider for `generate_draft()` and `generate_draft_with_tools()`

**Tests:** Full test suite + conversation quality evaluation

**Rollback:** Revert to Gemini provider

**Acceptance criteria:** Conversation quality meets or exceeds Gemini baseline

### Phase G: Production Validation (REQUIRES REVIEW)

**Goal:** Validate Ollama in production with monitoring.

**Files to modify:** None

**Tests:** Production monitoring, A/B testing

**Rollback:** Revert to Gemini provider

**Acceptance criteria:** No degradation in key metrics

---

## 15. Rollback Strategy

Each phase has an independent rollback:

| Phase | Rollback | Impact |
|-------|----------|--------|
| A | Delete new files | Zero impact |
| B | Kill SSH process | Zero impact |
| C | Set `OLLAMA_ENABLED=false` | Zero impact |
| D | Disable shadow mode | Zero impact |
| E | Revert provider changes | Zero impact |
| F | Revert provider changes | Zero impact |
| G | Revert all changes | Full rollback to Gemini-only |

**Critical:** The `AUTONOMY_ENABLED` kill switch remains independent of LLM provider selection. Disabling autonomy does not affect LLM provider choice, and vice versa.

---

## 16. Exact Files Requiring Future Changes

### Phase A: Provider Abstraction

| File | Action | Description |
|------|--------|-------------|
| `core/llm_provider.py` | CREATE | Provider protocol + factory |
| `core/llm_provider_gemini.py` | CREATE | Gemini implementation |

### Phase C: Ollama Provider

| File | Action | Description |
|------|--------|-------------|
| `core/llm_provider_ollama.py` | CREATE | Ollama implementation |
| `core/config.py` | MODIFY | Add `ollama_base_url`, `ollama_model` |
| `.env.example` | MODIFY | Add Ollama configuration |

### Phase E: Non-Critical Migration

| File | Action | Description |
|------|--------|-------------|
| `memory/summarizer.py` | MODIFY | Use provider abstraction |
| `memory/profile.py` | MODIFY | Use provider abstraction |
| `core/scoring.py` | MODIFY | Use provider abstraction |

### Phase F: Conversational Migration

| File | Action | Description |
|------|--------|-------------|
| `workers/llm_worker.py` | MODIFY | Use provider abstraction |
| `commerce/deepseek.py` | MODIFY | Use provider abstraction |
| `commerce/deepseek_response.py` | MODIFY | Use provider abstraction |

---

## 17. Exact Environment Variables Required

```bash
# Ollama Configuration (Phase C+)
OLLAMA_BASE_URL=http://127.0.0.1:11435    # SSH tunnel endpoint
OLLAMA_MODEL=llama3.1:8b                   # Model name
OLLAMA_ENABLED=false                       # Kill switch for Ollama

# SSH Tunnel (managed outside .env)
# ssh -f -N -L 11435:127.0.0.1:11434 user@vps-host
```

---

## 18. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Ollama model quality insufficient | Medium | High | Evaluate before migration; keep Gemini as fallback |
| SSH tunnel drops | Low | Medium | ServerAliveInterval + auto-reconnect script |
| VPS goes down | Low | High | Keep Gemini as fallback; monitor VPS health |
| Ollama inference too slow | Medium | Medium | Start with 7B model; optimize later |
| JSON output unreliable | Medium | High | Test thoroughly; use prompt-based JSON enforcement |
| Tool calling incompatible | Medium | Low | Only 1 call site uses tools; can keep Gemini for that path |
| Model ignores system instructions | Medium | High | Test with representative prompts; use smaller model with better instruction following |
| Ollama API changes | Low | Low | Pin Ollama version; monitor releases |

---

## 19. Final Recommendation

**Ollama CAN become the primary conversational model** without compromising the CRM's architecture. The key findings:

1. **Clean abstraction point exists:** `core/gemini_client.py` → `core/credentials.py` is a single choke point. All 8 call sites use the same pattern.

2. **Authority boundaries are provider-agnostic:** The deterministic commerce engine, DropFans execution, and safety validation are completely independent of the LLM provider.

3. **Safe fallback is built-in:** Every call site wraps LLM calls in `try/except` with safe defaults. Ollama failure degrades gracefully.

4. **Non-critical workloads are good candidates:** Summarization, profile extraction, and scoring can move to Ollama first with minimal risk.

5. **SSH tunnel is the recommended transport:** Simple, secure, no port exposure, easy to set up and tear down.

**Recommended approach:**
- Start with Phase A (abstraction) — zero risk
- Establish SSH tunnel (Phase B) — manual verification
- Implement Ollama provider (Phase C) — unit tests only
- Shadow test (Phase D) — parallel testing
- Migrate non-critical workloads (Phase E) — low risk
- Migrate conversational model (Phase F) — requires careful evaluation

---

## 20. Explicit List of Unknowns Requiring Human Input

1. **Ollama model name:** Not identified in repository. Need the exact model to evaluate.

2. **VPS details:** SSH credentials, hostname/IP, OS, available resources (CPU, RAM, VRAM).

3. **Ollama version:** Which version of Ollama is installed on the VPS?

4. **Model performance requirements:** What is the acceptable latency for conversational responses?

5. **Conversational model quality threshold:** What quality level is acceptable compared to Gemini?

6. **Tool calling requirement:** Is tool calling required for the Ollama model, or can it be kept on Gemini?

7. **Embedding requirement:** Should embeddings move to Ollama, or stay on Gemini?

8. **Production vs development:** Is this for local development only, or will it eventually replace Gemini in production?

9. **Budget for VPS resources:** What VRAM/CPU is available on the VPS?

10. **Compliance requirements:** Are there data residency or privacy requirements that affect model hosting?

---

## 21. Terminal Summary

```
OLLAMA LLM FORENSIC AUDIT COMPLETE

LLM CALLS FOUND: 8 production call sites
CONVERSATIONAL CALLS: 2 (generate_draft, generate_draft_with_tools)
NON-CONVERSATIONAL CALLS: 6 (scoring, summarization, profile, signals, response, embedding)

CURRENT PROVIDERS:
  - Google GenAI (Gemini) — sole provider for all LLM workloads
  - Model: gemini-flash-latest (configurable)
  - Transport: google-genai SDK with credential pool + rate limiter

OLLAMA COMPATIBILITY:
  - Chat completions: YES (role mapping needed)
  - JSON output: YES (format parameter or prompt-based)
  - Tool calling: PARTIAL (format differs, needs testing)
  - Embeddings: YES (different API surface)
  - Temperature/max_tokens: YES (parameter mapping needed)

REMOTE ACCESS:
  RECOMMENDED: SSH tunnel (localhost:11435 → VPS 127.0.0.1:11434)
  ALTERNATIVE: WireGuard (if persistent 24/7 connection needed)

PRODUCTION CODE CHANGED: NO
DATABASE CHANGED: NO

DROP FANS PATHS CHANGED: NO
FANGATE PATHS CHANGED: NO

VERDICT: CONDITIONAL — Ready for Phase A (abstraction) + Phase B (tunnel)
          Full migration requires: model name, VPS details, quality evaluation

BLOCKERS:
  1. Ollama model name not identified
  2. VPS SSH credentials not provided
  3. Model quality not yet evaluated
  4. Tool calling compatibility not verified
```
