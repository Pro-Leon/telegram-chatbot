# Qwen3 Native Optimization Research

## 1. Qwen3-4B Context Architecture

| Property | Value |
|----------|-------|
| Parameters | 4.0B (3.6B non-embedding) |
| Layers | 36 |
| Attention Heads | 32 Q / 8 KV (GQA) |
| Native Context | 32,768 tokens |
| Extended Context | 131,072 tokens (YaRN) |
| Quantization | Q4_K_M (installed) |
| Vocabulary | 151,669 (BBPE) |
| Architecture | Dense transformer |

**Key finding:** Qwen3-4B has 32K native context. Our current CRM prompt (~700+ tokens) is well within this limit. The latency issue is NOT context size — it's prompt complexity and VPS inference speed.

## 2. Thinking / Non-Thinking Mode

Qwen3 uniquely supports **hybrid thinking** within a single model:

- **Thinking mode** (`/think` or `enable_thinking=True`): Step-by-step reasoning, internal chain-of-thought. Higher latency, better for complex reasoning.
- **Non-thinking mode** (`/no_think` or `enable_thinking=False`): Direct response generation. Lower latency, optimized for conversational tasks.

**Critical insight for CRM:** The model follows the MOST RECENT instruction in multi-turn conversations. `/no_think` in the system message disables thinking for the entire conversation unless overridden.

## 3. Official Recommended Parameters

### Non-Thinking Mode (our primary mode)
```
Temperature=0.7
TopP=0.8
TopK=20
MinP=0
PresencePenalty=1.5
```

### Thinking Mode (for complex reasoning)
```
Temperature=0.6
TopP=0.95
TopK=20
MinP=0
PresencePenalty=1.5
```

**Critical:** DO NOT use greedy decoding — it causes performance degradation and endless repetitions.

**Critical:** `presence_penalty=1.5` is recommended for quantized models to suppress repetitive outputs.

## 4. Qwen3 Strengths Relevant to CRM

| Strength | CRM Relevance |
|----------|---------------|
| Non-thinking mode | Fast conversational generation |
| Instruction following | Follows persona/role constraints |
| Format following | Structured output when needed |
| Role-playing | Natural persona maintenance |
| Multi-turn dialogue | Conversation continuity |
| 119 languages | Multilingual fan support |
| Agent/tool capabilities | Reserved for complex reasoning |
| Human preference alignment | Natural, engaging responses |

## 5. Qwen-Specific Prompt Recommendations

From official documentation:

1. **Use `/no_think` in system message** for consistent non-thinking behavior
2. **Keep instructions short and explicit** — Qwen3 excels at following concise instructions
3. **Do not over-explain rules** — the model follows instructions naturally
4. **Use structured formats** when you need specific output structure
5. **`presence_penalty=1.5`** for quantized models to reduce repetition

## 6. Performance Considerations

| Factor | Impact |
|--------|--------|
| Prompt length | Linear — longer prompts = more processing time |
| Thinking mode | 3-10x slower than non-thinking |
| Quantization (Q4_K_M) | ~40% faster than Q8_0, slight quality loss |
| Context length | Within 32K native = no YaRN overhead |
| Temperature | Higher = more variance, not slower |

## 7. Architecture Decision

**DO NOT let Qwen3 reason about CRM policy.**

The CRM is deterministic. Qwen3 is the conversational realization layer.

```
Application Decision (deterministic)
    ↓
Compact CRM State (what is true)
    ↓
Qwen3 Non-Thinking (realize naturally)
    ↓
Response
```

NOT:

```
Fan Message
    ↓
Qwen3 Thinking (reason about rules)
    ↓
Qwen3 decides action
    ↓
Response
```

## 8. Ollama Usage

From official docs:
```bash
ollama run qwen3:4b
```

The `/no_think` and `/think` prefixes work in user prompts and system messages.

## 9. Conclusion

Qwen3-4B is architecturally well-suited for the CRM conversational layer when:
1. Using non-thinking mode (default)
2. Following official parameter recommendations
3. Keeping prompts concise and explicit
4. Letting the deterministic CRM own all authority

The optimization path is:
1. Compact context format
2. Non-thinking mode as default
3. Official recommended parameters
4. Prompt structure aligned with Qwen3's strengths
