# Luna Reliability Program — Industry Standards + Phased Fix Plan

> Canonical reference for all audit / implement / verify prompts.
> Phase 1 realtime event contract (`AGENTS.md`) is IMMUTABLE: no rename/remove/reorder of
> `message.created|sent|send_failed`, `ai.generation_started|completed|failed`,
> `suggestion.created`, `operator_queue.updated`. Preserve `generation_id` lineage,
> unique `event_id`, best-effort publish, workers→event-bus only, polling fallback.
> All phases below are classified SAFE (bug fixes preserving contract).

## 0. Luna incident (evidence)

- 10 msgs `2026-09-24 06:13–06:19 UTC`, user `8151382101`, `funnel=new`.
- `Hey Luna, how's it going?` ×3 (`0.825` auto) ignoring `good, just abit busy / How are you?`.
- `Your conversational response to the fan` ×2 (`0.875` auto, `send_stream.consumer=sent`) → user `what do you mean fan? are you a bot?`.
- Back-to-back outbound pair (`134/135`) with no inbound between.
- Profile strategy-only, 0 summaries, queue 0.

## 1. Industry standards applied

### 1.1 Guardrails (NeMo / Guardrails AI / LLM Guard / Bedrock)
- Defense-in-depth: input rails → model → output rails as a separate layer with positional
  independence (cannot be talked out of its job). Cheap deterministic checks first, LLM-judge behind.
- Verdict-as-event: every check emits verdict + metrics (violation rate, FP rate, latency).
- Single choke point: ALL send paths pass output rails. No caller bypass.
- References: NeMo Colang state machine for allowable dialog paths; Guardrails AI RAIL schema validators;
  OWASP LLM Top-10 2025 (LLM01 prompt injection, LLM07 system-prompt leakage, LLM09 misinformation).

### 1.2 Prompt / schema hardening (OWASP LLM07)
- Never use a sendable sentence as a schema example. Placeholder must be non-sendable (`<<REPLY>>`).
- Output filter for known system-prompt fragments (fuzzy/substring, not exact-only).
- Strict JSON-schema decoding: schema failure → DLQ/queue, never fallback-valid.

### 1.3 Conversation quality
- Repetition: `distinct-n` + embedding similarity vs recent DB outbound, not intra-draft word counts.
- Grounding: every reply references user words/prior fact; `unanswered_question` is blocking, not advisory.
- Persona: forbidden internal vocabulary (`fan`, speaker labels) stripped/replaced with `first_name`.
- Lifecycle: single state-machine authority with persisted checkpoints, not per-turn regex re-derivation.

### 1.4 Memory / RAG
- Durable reads distinguish MISS vs ERROR; generation tagged `degraded:true` instead of silent empty history.
- Summarization checkpoint on token/turn budget + background catch-up (not `count % N`).
- RAG with scores + citations + degraded flag; salient-span retention with omission markers.

### 1.5 Delivery (Redis Streams standard)
- At-least-once + idempotent consumer = effective exactly-once (production standard).
- Idempotency key on `(creator,user,telegram_message_id)` + content-hash window; `SET NX` with TTL.
- Transactional outbox: DB row + stream relay atomically; `XACK` immediately after side effects; small
  process→ack window; redelivery counter + DLQ after N; `XPENDING` + dedup-hit monitoring.
- Redis 8.6 `XCFGSET IDMP-DURATION/MAXSIZE` for producer dedup where available.

## 2. Root causes (code-anchored)

- R1 Echo source: `core/one_call.py:1068` + `:1093` schema example copied by model.
- R2 Detector erased: `core/one_call.py:514-517,568-570` sets `prompt_echo/0.29/handoff`, but
  `core/one_call_pipeline.py:356` allowlist omits it; `:364-381` `validate_draft_quality` overwrites flags/score.
- R3 Bypass paths: legacy `workers/llm_worker.py:4662-4846` + `core/scoring.py:79`, dashboard
  `chatbotv2/dashboard/routes/messages.py:65`, `routes/queue.py:233`, `routes/vault.py:550`,
  scheduler `workers/scheduler_worker.py:307`, `send_worker.py:103,323` never validate.
- R4 Send gate: `workers/llm_worker.py:5391` `score>=0.80 (core/config.py:22)` + `not flags` + `not forces_queue`;
  `_routing_needs_handoff` only set on one-call-success (`:3774`), legacy stays `False (:3038)`.
- R5 History collapse: `db/postgres.py:722-745` strict creator excludes legacy NULL; fallbacks to
  `recent=[]/user={new}` (`memory/context.py:645`, `context_assembler.py:154`, `authoritative_assembly.py:109`);
  debounce latest-only (`chatbotv2/handlers.py:246`); trim `MAX_ASSISTANT_TURNS=3` (`memory/context.py:691`,
  `core/context_compact.py:183`); profile one-turn late (`llm_worker.py:416`).
- R6 Stuck funnel: `db/schema.sql:14` default `new`; only writer `commerce/post_purchase.py:47` → `converted`;
  guidance `memory/context.py:153` always `Warm welcome`; lifecycle regex `core/conversation_state.py:53-67,189`.
- R7 Duplicates: per-draft dedup `md5(user:msg:tgId)` (`llm_worker.py:5223`) + `md5(user:content)` manual
  (`routes/messages.py:50`); non-atomic PG→Redis (`send_worker.py:115-356`, `queue.py:233→285`,
  `main.py:755-780` ACK-before-DB); `send_random_id` fetched never passed (`main.py:687-708`); reclaim no counter (`db/redis.py:862`).
- R8 Observability: `routing_reason/veto` dropped (`core/telemetry.py:18-290,300`, `audit.py:35 EVENT_TYPES`);
  no violation/repeat/dedup boards (`dashboard/routes/live.py:53`); tests cover validator only
  (`tests/test_crooked_reply_fixes.py:167`), not pipeline/send-mTURN.

## 3. Phased plan with sub-phases

### Phase 0 — stop-the-bleed (1–2 days)
- 0.1 Echo preserve: add `prompt_echo,markup_echo,no_grounding` to `_roleplay_flags_set` (`pipeline.py:356`);
  re-run `detect_prompt_echo` after `validate_draft_quality`; re-cap `0.29` + `needs_handoff`.
  Accept: pipeline echo fixture → `QUEUE`.
- 0.2 Placeholder: `one_call.py:1068` → `"<<REPLY>>"`; update `RULES:1093` + contract doc.
  Accept: prompt snapshot has no sendable sentence.
- 0.3 Baseline: log `quality_flags,routing_reason,veto`; ad-hoc SQL for violation/repeat/dedup/`XPENDING`.
  Accept: pre-fix rates recorded.

### Phase 1 — output-rails choke point
- 1.1 New `core/output_rails.py:check()` — echo fuzzy/substring, `fan`-word, speaker-prefix anywhere,
  repeat vs last-3 DB outbound (`cosine>0.85`/normalized-equal), markup. Returns `(verdict,flags,cap)`.
- 1.2 Call after `validate_draft_quality` (`pipeline.py:379`) + after commerce overwrite (`llm_worker.py:4225`).
- 1.3 Call in `db/redis.py:66 enqueue_send`, `send_worker.py:103,323`, `routes/messages.py:65`,
  `routes/queue.py:233`, `routes/vault.py:550`, `scheduler_worker.py:307`.
- 1.4 Tiers in `core/routing.py:93`: hard `{prompt_echo,safety,boundary}` veto vs info penalty-only;
  propagate `needs_handoff` on all branches.
- Accept: echo/manual/legacy replay → `QUEUE`, never `was_auto_approved=True`.

### Phase 2 — input + memory durability
- 2.1 Input rails (`handlers.py:28`): length cap, empty drop, content-hash dedup; close `redis.py:944` fail-open.
- 2.2 Durable reads: MISS vs ERR; `degraded:true` tag; `(created_at,id)` ordering; legacy NULL fallback.
- 2.3 Debounce merge: full `debounced[]` into context, not `latest-only :246`.
- 2.4 State authority: single lifecycle owner; `warming/engaged` writers; summarizer catch-up (not `%20 :summarizer.py:37`).
- Accept: empty-history rate → ~0; busy-fact retained next turn.

### Phase 3 — delivery guarantees
- 3.1 Idempotency `(creator,user,tg_id)` + content window; `XACK` after persist; counter + DLQ after N.
- 3.2 Outbox: PG txn + relay; `save_outbound_after_send (:postgres.py:602)` before ACK; wire-id recorded on retry.
- 3.3 Single-outbound per `(creator,user,tg_id)`.
- Accept: duplicate-wire rate → 0; `XPENDING` bounded; DLQ policy documented.

### Phase 4 — observability + gates
- 4.1 Persist `routing_reason,veto,quality_flags,score` (`telemetry.py`), `audit EVENT_TYPES` +=
  `auto_approved,prompt_echo,repeat`; join `operator_queue↔telemetry` on `generation_id`.
- 4.2 Boards: violation/repeat/dedup-hit/pending-lag + p50/p95.
- 4.3 CI corpus: Luna 5-turn replay, pipeline-overwrite test, send-gate test, `AGENTS.md` lifecycle test
  (`completed` only after `enqueue_send` ok else `failed`).
- Accept: every fix has metric + regression test; contract unchanged (verify with existing event tests).

## 4. How to work (always cite this file)

- Audit → Implement → Verify per phase/sub-phase. Each prompt MUST name this file and the target
  `Phase X.Y`. Auditor returns evidence + file:line gaps. Implementer changes minimal code + tests.
  Verifier replays Luna fixture + checks metrics + confirms event contract intact.
