# OneCall Stabilization & Latency Reduction Plan
## Forensic-Based Fix Plan for E:\chatbot

> Derived from live `llm_path=new` / `provider=llamacpp` forensic trace (generation `forensic-live-002`, Pola 1.2B Q4_K_M). No architecture rewrite. Ship passes sequentially; stop and re-benchmark after each.

---

## 0. Context: What the Audit Proved

**Pipeline is correct at the seams:**

- `workers/llm_worker.py:2646` `llm_path==new` single canonical path, no legacy fallback.
- `context_engine/authoritative_assembly.py:52` ONE `AuthoritativeState` frozen (`context_engine/models.py:273` `MappingProxyType`), `derive_call_count ==1`.
- `memory/context.py:90` + `core/context_compact.py:391` current message exactly-once (`occ==1`).
- `core/llm_provider_llamacpp.py:174` `_build_payload(..., onecall_json_schema=True)` sends `response_format: {type:"json_schema", json_schema:{name:"OneCallReply", schema:build_onecall_json_schema(), strict:true}}` (`core/one_call.py:114`) and is actually transmitted (HTTP payload 9509 chars verified live).
- `core/one_call.py:361` `validate_one_call_response` succeeds (`is_valid true`, `quality 0.875`, no flags) — one generation, `total_llm_calls=1`.
- Wire estimate `1533` EST (`system 831` + `serialized 702`) vs model-reported `prompt 1548` (`core/llm_provider_llamacpp.py:244` `usage.prompt_tokens`) delta 1.0% — estimates are trustworthy. `gen 145` / `total 1693` model-reported.

**Discrepancies (prioritized):**

| Pri | Finding | Impact |
|---|---|---|
| 🔴 High | `requested_price=0` emitted despite `exclusiveMinimum:0` → `one_call_invalid_result` (queue ids 67,66) | Wasted generations |
| 🟠 Med | CE `21.762s` (semantic `21.402s`) inside `MemorySource._get_knowledge_safe` `context_engine/gatherer.py:842` for 0 hits | Dominates latency; total `~49.6s` |
| 🟠 Med | Inbound history rendered as `Sunny Skye: [inbound] ...` (`gatherer.py:514` → `renderer.py:229` → `context_compact.py:325` `_label_conv`) | Mislabels speaker, breaks `CHARACTER/PLAYER/CURRENT TURN/RESPONSE OWNER` contract `core/conversation_contract.py:265,335,430` |
| 🟠 Med | Relationship `cold` (CE `gatherer.py:297` `purchase_count=0`) vs `engaged,27 msgs` carrier (`workers/llm_worker.py:2019`) vs `warming` (funnel) | Contradictory signals to LLM |
| 🟡 Low | Commerce hints dead: `core/one_call_pipeline.py:94` `has_commerce` suppresses `core/commerce_prompt.py:19` even when needed | Hides offer context |
| 🟡 Info | Serialization `529→702` `+173 +32.7%` (`core/one_call_pipeline.py:179` `json.dumps`) not in category budgets | Wire > app budget |
| 🟡 Info | `ONE_CALL_TOKEN_BUDGET system350 state150 conv600 signals50` (`core/context_compact.py:27`) not enforced vs final `system 448` | Misleading accounting |

Target invariant set (keep): `ONE snapshot`, `ZERO duplicate authoritative reads`, `ONE LLM generation`, `ONE current-message occurrence`, `ZERO legacy fallback`, plus added `semantic only when required`, `bounded top_k`, `hard timeout`, `speaker direction preserved`, `authoritative not overridden`, `final prompt within budget`, `invalid commerce never executes`.

---

## 1. Target End State

```
Telegram → Debounce (3s) → Redis inbound (llm_workers)
                ↓
  assemble_authoritative_context (60ms parallel: get_user, get_user_profile, get_recent_messages×20, get_latest_summary_with_age, get_structured_persona)
                ↓
  FAST deterministic enrichment (relationship/strategy/behavior/commerce)
                ↓
  FAST Context Engine (gated retrieve → resolve → dedup → budget → render)
                ↓
  build_one_call_from_snapshot (participants+contract+carriers+history+current)
                ↓
  ONE llama.cpp POST /v1/chat/completions (system 3120 + user json 2646, schema 3119, max_tokens 400 temp 0.7)
                ↓
  validate_one_call_response + validate_draft_quality → decide_routing (0.80) → enqueue_send | operator_queue
```

**Performance target:** `CE: 21.7s → <1s` (then optimize model separately). Correctness target: `invalid_result 0` for no-price turns, `speaker inversion 0`.

---

## 2. Pass 0 — Baseline + Instrumentation (1–2d, no behavior change)

Instrument exact `llm_path=new` path without changing it. Extend `context_engine/worker_integration.py:77` `observe_context_engine` + `core/telemetry.py` / `context_engine/integration.py:192`:

```
generation_id creator_id user_id
authoritative_assembly_ms  workers/llm_worker.py:969  context_engine/authoritative_assembly.py:52 metadata.acquisition_ms
CE: candidate_count selected_count dropped_count conflict_dropped lexical_dedup truncation total_ms gather_ms score_ms dedup_ms budget_ms render_ms category_tokens[9] degradation_level retrieval_metrics{lexical_count semantic_count merged_count lexical_ms semantic_ms embedding_ms degraded total_retrieval_ms}  context_engine/worker_integration.py:255
OneCall: compaction_ms  core/context_compact.py:302  serialization_ms  core/one_call_pipeline.py:179  provider_ms + timings.prompt_ms/predicted_ms  core/llm_provider_llamacpp.py:302  prompt_tokens/completion_tokens/total_tokens  core/llm_provider_llamacpp.py:244  validation_ms  core/one_call.py:361  routing_ms  core/routing.py
total_ms
flags: semantic_invoked semantic_required semantic_hits CE_tokens
```

Scenarios A–F: `A hey` (no retrieval), `B knowledge Q` (needs retrieval), `C price/product`, `D relationship-heavy`, `E long history (20)`, `F no-price → requested_price null`.

*Accept:* forensic `CE 21.762 gather 21761 semantic 21402 wire 1533/1548 gen 145/27.83s` reproduced locally via existing `httpx` probe.

---

## 3. Pass 1 — Diagnose Semantic Retrieval (0.5d, read-only)

Why `21.4s` for 0 hits?

Audit `context_engine/gatherer.py:842` `MemorySource._get_knowledge_safe`:

1. Entrant: `ContextGatherer.gather_all` `context_engine/gatherer.py:1223` — sequential per source, no `wait_for`.
2. Query: `current_message` → `commerce/embedding_model.py:encode_message` — is `get_model()` (`workers/llm_worker.py:4555`) warm once or reloaded per request? Lock? CPU? disk I/O? `config: llama_model Pola1.2B, embedding_model text-embedding-3-small` (`core/config.py:58`).
3. Corpus: `get_fan_knowledge(creator,user)` size for real fan `8151382101` (seen 95 msgs) → `encode_messages_sync(texts)` `gatherer.py:971` brute-force `dot>=0.30` limit 5, sorted deterministically.
4. Caching: any `doc→vec` or `query→vec` cache? Currently none (`LTM fan_knowledge_by_creator` per-creator but embeddings not cached).
5. `RapidFuzz WRatio 80` `lexical 73ms` vs `MiniLM` `semantic 21402ms` — ratio already measured.

Deliverable: call graph + per-stage timings + corpus size + lock/cache status. Confirm hypothesis: reload + uncached brute-force is pathological.

---

## 4. Pass 2 — Retrieval Gating + Timeouts (2d) — **First Fix, Highest ROI**

**2.1 Gating** (`context_engine/gatherer.py:842` or `context_engine/worker_integration.py:140` before `MemorySource`):

```python
def should_retrieve_knowledge(msg: str, state: dict) -> bool:  # deterministic, no LLM
```

Signals: `len<8`, `?` / `W?` (`core/conversation_contract.py:32` `_is_question`), product/topic lexicon `segments/`, commerce intent `commerce/purchase_intent.py`, `fan_asks_question`, `current_topic` continuity `core/conversation_state.py`.

- `hey / good morning / ok / miss you` → `False` → CE `<100ms` (skip `semantic`, keep `RapidFuzz 73ms`).
- `remember/told you/what was` (`memory/context.py:58` triggers) or knowledge-dependent Q → `True` → bounded rare path.

**2.2 Timeout** `ContextGatherer.gather_all` each `await gather` wrap `asyncio.wait_for(2.0)`; on timeout return `[]` (already fail-open `gatherer.py:89` `return []`).

*Accept:* A `CE <200ms semantic_invoked false`; B `semantic true` but still bounded; hits unchanged.

---

## 5. Pass 3 — Cheap Retrieval When Needed (2–3d)

Ordered inside `commerce/embedding_model.py`:

1. **Reuse model** — `workers/llm_worker.py:4555` `get_model()` singleton per worker (already warmed at startup); never reload in request. Guard init with `asyncio.Lock` once.
2. **Cache `doc→vec`** LRU key `(creator_id, knowledge_id/version)`; invalidate on `add_knowledge_item` `commerce/fan_knowledge.py`.
3. **Cache `query→vec`** TTL `60s` key `hash(normalized query)`.
4. **Bound search** — filter `creator_id/user_id` already done; add `top_k 5 threshold 0.30 max_candidates 20` before `gatherer.py:981` loop; early exit `0` docs → skip encode.
5. **Indexed path** when corpus >500 — use `pgvector` (`db/schema.sql` already `pgvector`) or `hnswlib`/`annoy`; `memory/retrieval.py` currently `return []` — replace with indexed retrieval instead of optimizing brute-force forever. Do not add new vector DB until `P1` shows need.

*Accept:* `semantic_ms <300ms` for 20 docs (was `21s`); `CE total <1s` on gated path.

---

## 6. Pass 4 — Speaker Attribution Fix (1d)

**Bug:** `gatherer.py:514` `f"[{role}] {content}"` + `renderer.py:229` `metadata.role` → `context_compact.py:325` `_label_conv(role=="user")` prefixes `Sunny Skye:` even for `[inbound]` fan turns.

**Fix:**

- `context_engine/gatherer.py:514` + `context_engine/renderer.py:222` preserve canonical `direction` (`inbound`/`outbound` from `db/postgres.py:722`) not derived `role`.
- `core/context_compact.py:324` `_label_conv`: `inbound → listener (Fan/Mason)` `outbound → speaker (Sunny Skye)` via `participants` (`core/conversation_contract.py:265`); strip `[inbound]/[outbound]` tag before sending.
- Keep `CHARACTER=SPEAKER=creator persona`, `PLAYER=LISTENER=fan` invariant `core/conversation_contract.py:430`.

Tests `tests/test_m3_current_message_once.py` sibling `test_speaker_attribution.py`: `inbound→user/fan`, `outbound→assistant`, mixed ordering, summary inert, exactly-once still `1`.

---

## 7. Pass 5 — Relationship State Single Authority (1d)

Conflict `CE cold` (`gatherer.py:297` `_derive_rel(...,purchase_count 0)`) vs carrier `engaged` (`workers/llm_worker.py:2019`) vs `funnel_stage warming`.

Authority: `AUTHORITATIVE` funnel `+` purchase history + boundaries (DB) → `DERIVED` strategy → `ADVISORY` LLM/ranking (never LLM→DB).

Fix: compute `relationship_state` once in `assemble_authoritative_context` `context_engine/authoritative_assembly.py:296` via `commerce/relationship.derive_relationship_state` with real `purchase_count`; `FanStateSource` `gatherer.py:266` reuses `authoritative_state.relationship_state` when present (like `PersonaSource` `gatherer.py:165`) instead of `0`.

---

## 8. Pass 6 — `requested_price` Semantics (0.5d)

Schema `exclusiveMinimum 0` + `anyOf number/null` `core/one_call.py:242` correct yet `0` emitted → `Pydantic Value error commerce_signals.requested_price must be finite positive` → `flags one_call_invalid_result` `operator_queue id 67/66`.

Interpretation: `0 = invalid`, `null = not provided`, `>0 = provided`.

Deterministic normalization before `OneCallReply.model_validate` (`commerce/signals.py:195`):

```python
if isinstance(requested_price, (int,float)) and requested_price == 0:
    requested_price = None  # only if 0 has no business meaning (confirm commerce contract)
```

Keep `strict:true` (`core/llm_provider_llamacpp.py:68`) and `validate_one_call_response` (`core/one_call.py:361`) — do not weaken validation. Audit other `0/false/""` vs `null`.

*Accept:* `invalid_result` rate for no-price turns → `0`; re-audit shows `requested_price null`.

---

## 9. Pass 7 — Commerce Context Gating (0.5d)

`core/one_call_pipeline.py:94` `has_commerce = any("commerce" ...)` suppresses `build_commerce_signal_hints` `core/commerce_prompt.py:19` for pipeline path even when needed.

Define `commerce_context_required(snapshot,enrichment)->bool` deterministic on `desire/window/objective` (`commerce/conversational.py` `workers/llm_worker.py:1421` already computed). Include bounded `COMMERCE FACTS` only when `True` (price/product/purchase/post-purchase). Normal chat → omit to save `30` chars.

---

## 10. Pass 8 — OneCall Budget Truth (0.5d)

`ONE_CALL_TOKEN_BUDGET system350 state150 conv600 signals50` `core/context_compact.py:27` vs actual `system 448` (`persona 31`+`participants 104`+`contract 187`+carriers `21`) passes only because `validate_one_call_context` checks `8192` (`core/context_compact.py:605`), not sub-budgets.

**Prefer Option A:** define `ONE_CALL_MAX_PROMPT_TOKENS 1800` against **final wire** `system 831 + serialized 702 =1533 EST / 1548 reported` (authoritative, matches `llama.cpp n_ctx 8192` `core/config.py:79` probe `n_ctx_train 131072`). Keep component budgets as soft diagnostics.

---

## 11. Pass 9 — Serialization Overhead (defer)

`529 →702 +173 +32.7%` via `json.dumps(messages)` inside `user_content` (`core/one_call_pipeline.py:179`). Overhead `173` << `21s`; address only if `wire >1800`. Investigate textual compact (`SYSTEM:\n... STATE:\n... CONVERSATION:\n...`) after P2–P6 rather than JSON object per message.

---

## 12. Pass 10 — llama.cpp Runtime (parallel track)

Once `CE <1s`, generation `prompt1548 gen145 27.8s @7.19 tok/s` becomes bottleneck (`timings {prompt_ms 1430 predicted_ms 28075}`). Profile model/quant/CPU vs GPU layers/threads/batch/context/`KV cache`/`cached_tokens 851`. Do not change until prompt is correct and cheap.

---

## 13. Pass 11 — Regression Suite + Re-audit (1d)

New `tests/test_one_call_invariants.py`: 10 invariants + `retrieval_timeout`, `bounded top_k`, `speaker direction preserved`, `authoritative not overridden`, `final prompt ≤1800/8192`, `invalid commerce never executes`.

Re-run Pass 0 benchmark; require:

```
BEFORE (this audit)        AFTER target
CE 21.762s                <1.0s
 semantic 21.402s          <0.3s
 wire 1548                 1100–1300 (gated)
 gen 145                   145 (same model)
 llama 27.8s               27.8s (unchanged until P10)
 total 49.6s               ~29s
 invalid_rate              <1%
 speaker_inversion         0
 relationship conflicts    0
```

---

## 14. Execution Order

| Phase | Work | Priority |
|---|---|---|
| 0 | Baseline + instrumentation | 🔴 |
| 1 | Diagnose semantic retrieval | 🔴 |
| 2 | Retrieval gating + timeouts | 🔴 |
| 3 | Retrieval performance/cache/index | 🔴 |
| 4 | Speaker attribution | 🔴 |
| 5 | Relationship authority | 🔴 |
| 6 | `requested_price` semantics | 🔴 |
| 7 | Commerce gating | 🟠 |
| 8 | Budget accounting | 🟠 |
| 9 | Serialization optimization | 🟡 |
| 10 | llama.cpp runtime | 🟡 (parallel) |
| 11 | Regression suite + full forensic re-audit | 🔴 |

**Do NOT change:** `AuthoritativeState` frozen (`context_engine/models.py:273`), `observe_context_engine` fail-open (`context_engine/worker_integration.py:230`), `one snapshot` / `one generation` / `exactly-once`, hard validation `core/one_call.py:361`, operator fallback `workers/llm_worker.py:3899`.

**Deliver per pass:** file diff + benchmark delta + before/after `retrieval_ms` `wire_prompt` `invalid_rate`; no subjective "feels faster".

---

## 15. Raw Evidence Index

`workers/llm_worker.py:54,850,969,2646,2678,2921,3829` · `context_engine/authoritative_assembly.py:52,81,144,229,283,325` · `context_engine/models.py:54,58,181` · `context_engine/budget.py:40,58` · `context_engine/integration.py:161,174,192` · `context_engine/worker_integration.py:77,140,230,255` · `context_engine/gatherer.py:89,165,266,297,514,842,971,981,1223` · `context_engine/renderer.py:222,229` · `core/context_compact.py:27,35,38,302,324,385,446,605` · `core/one_call.py:41,114,148,242,361,984` · `core/commerce_prompt.py:19,112` · `core/llm_provider_llamacpp.py:68,174,213,244,302,332` · `core/conversation_contract.py:32,76,178,265,335,430,457` · `core/config.py:33,58,72,78,117,124` · `memory/context.py:13,50,80` · `db/postgres.py:201,241,722,766,938` · `qwen3_raw_output.json:1` legacy ref.
