# Handoff: Placeholder-Echo — Model Copies Schema Example, Cold Greetings Never Reply

Date: 2026-09-25. Author: Luna reliability program. Status: root cause proven live, fix pending.

## 1. Symptom (user-visible)

Fan sends `hi`/`hello` → no reply, ever. Two consecutive retries produced two operator-queue drafts and zero sends:
- `<<REPLY>>` (conf 0.5, `too_generic`) — rejected, never sent.
- `You are the beautiful Sunny, and I'm just Sunny...` (conf 0.5, `speaker_inversion` + `character_as_player_inversion`) — pending/editable.

Pipeline, workers, and guardrails all healthy. The system fail-closes correctly and then goes silent with nobody watching the queue.

## 2. Proven evidence (all executed, not theorized)

- Live generation, new prompt, cold Luna context (`user_message="hello"`, empty profile/history):
  `is_valid=True, reply='<<REPLY>>', quality 0.5 [too_generic], confidence 0.0, needs_handoff=True → routing QUEUE below_threshold.`
  Script: `C:\Users\User\AppData\Local\Temp\opencode\luna_live_test.py`.
- `detect_prompt_echo('<<REPLY>>')` → `False` (`core/one_call.py:592-610` matches one phrase only). The new placeholder bypasses echo detection entirely.
- Payload audit (builders executed): fixed `system_instruction` 1,037 tokens + snapshot `user_content` ~1,195 tokens = **~2,232 tokens/turn for a 1-token `hello`** (97% of the 2,300-token wire cap, `core/context_compact.py:42`).
- Model: `Llama-3.2-1B-Instruct-Uncensored-Q4_K_M` (`.env:33-35`, `core/config.py:79`). Prior stack used `qwen2.5:3b`/`qwen3:4b` (3–4× params).

## 3. Root cause (known industry failure class)

Constrained decoding guarantees **shape, not content**. Our llama.cpp call uses `json_schema` `strict:True`
(`core/llm_provider_llamacpp.py:69-76`), which forces valid JSON with correct keys — but any string is a
valid `reply`, so the sampler is free to emit the most salient string in context: the schema's own example
value. References: FriendliAI "Structured Output Requires More Than Guided Generation" (constrained
decoding "does not determine which token the model should generate next... valid but unintended
continuations"); GMI Cloud "enum violation... instruction-following as a conversational assistant rather
than as a data generator"; JSONSchemaBench (arXiv 2501.10868) on coverage gaps across frameworks incl. llamacpp.

Contributors, in leverage order:
1. **Example value in prompt** (`core/one_call.py:1068` `"reply": "<<REPLY>>"`): the copy source. First it was a sendable sentence (sent twice to a real user); now a placeholder (queued, never sent). Any literal is copyable.
2. **Model capacity**: 1B Q4 generic instruct. Copying the example is the lowest-effort valid continuation; larger/instruction-tuned models resist it.
3. **No counter-examples**: zero few-shot greeting pairs anywhere in the payload (`example|few_shot` grep in `core/` = one hit: the rule telling it *not* to copy examples).
4. **Cold context**: fresh users present persona essay (792 tokens) + 17 chars of state; Rules 10–11 demand specifics that don't exist, so the model fills the void with schema text or invented intimacy.

What was already tried and did NOT fix it: identity binding (`You are Sunny...`, `core/one_call.py:1065`), rule 12 anti-intimacy, rule 1 first-person rewrite — live test above still returns `<<REPLY>>`. Wording is exhausted.

## 4. Recommended fixes (pick in order; each independently shippable)

**A. Remove the example value (smallest, do first).** Delete the example JSON block from the prompt text
(`core/one_call.py:1067-1090` → prose field list, no `"reply": <anything>` literal); keep the formal
`json_schema` (shape still enforced by llama.cpp) + Rule 8 `Output ONLY`. Verify with the live-test script:
`reply != '<<REPLY>>'` across 10 cold greetings. Risk: negligible. Note: keep `_PROMPT_ECHO_PHRASES` as-is
(detector must catch historical echoes).

**B. Add `<<REPLY>>` to echo detection (observability).** Append `"<>reply>>"`-normalized form to
`_PROMPT_ECHO_PHRASES` (`core/one_call.py:613-615`) + output-rails echo check so placeholder copies get
`prompt_echo`, 0.29 cap, and correct boards — instead of hiding inside `too_generic`. Does not restore
replies by itself. Tests: extend `test_crooked_reply_fixes.py` + `test_output_rails.py`.

**C. Greeting fast-path without JSON (structural).** `hi/hello` + `funnel=new` + empty history → reply-only
prompt (no schema, no commerce signals), validated by existing rails + routing. Removes the copy source
entirely for the exact failing case. Cost: one new code path + tests; keep strict JSON for substantive turns.

**D. Bigger / roleplay-tuned model (highest leverage, highest cost).** Return to `qwen2.5:3b`/`qwen3:4b`-class
or roleplay-tuned 3B+. Check VRAM + p50/p95 latency before/after (`total_e2e_latency_ms`). This is the only
fix that addresses role inversion (`You are the beautiful Sunny`) rather than just echo.

**E. Few-shot greeting pairs (cheap complement).** 2 correct `Fan: hi → Sunny reply` examples in the prompt.
Documented need (`FORENSIC_AUDIT.md:782`: "Qwen 3B needs examples"). Pairs with A–D, not standalone.

Explicitly NOT recommended: more rules text (proven ineffective), lowering temperature alone (reduces
variety without removing the salient copy target), deleting history (makes cold-start worse).

## 5. Acceptance criteria

- 10/10 cold greetings (`hi/hello/hey`, fresh user) produce sendable replies: no `<<REPLY>>`, no old
  echo sentence, no speaker inversion, no `fan` vocative — asserted via rails flags, not eyeballing.
- At least 8/10 auto-send (score ≥ 0.80, no hard flags); remainder queue with correct flags.
- Live Luna retry ends in `message.sent`, not `operator_queue.pending`.
- Guardrail suites green: `test_output_rails`, `test_phase01_echo_preserve`, `test_phase12_wiring`,
  `test_phase13_choke`, `test_crooked_reply_fixes`, `test_luna_replay`, `test_redteam_replay`.
- Phase 1 event contract intact (no event rename; `generation_id`/`event_id` preserved).

## 6. Pointers

- Prompt: `core/one_call.py:1065-1104`. Schema call: `core/one_call_pipeline.py:264-275`.
  Provider: `core/llm_provider_llamacpp.py:56-79,174-211`. Detector: `core/one_call.py:592-615`.
  Rails: `core/output_rails.py`. Routing gate: `workers/llm_worker.py:5575`.
- Program reference: `docs/LUNA_RELIABILITY_PROGRAM.md`. Baselines: `docs/LUNA_BASELINES.md`.
- Live-test script: `C:\Users\User\AppData\Local\Temp\opencode\luna_live_test.py` (adapt freely).
