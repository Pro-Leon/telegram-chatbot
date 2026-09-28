import pathlib
p=pathlib.Path('E:/chatbot/core/one_call.py')
t=p.read_text(encoding='utf-8')
old='"confidence": 0.0-1.0,\n    "primary_intent"'
new='"confidence": 0.0-1.0,\n    "evidence": ["up to 5 short quoted fragments supporting your assessment, no payment data"],\n    "model_uncertainty": 0.0-1.0,\n    "primary_intent"'
if old in t:
    t=t.replace(old, new)
    print("patched evidence/model_uncertainty")
else:
    print("NOT FOUND old2")
    print(t.count('"confidence": 0.0-1.0,'))
    # Find
    import re
    print(repr(t[3000:4000]))

# Also update header to include canonical note
if "# One-call system prompt for Qwen2.5" in t and "canonical qwen2.5:3b" not in t:
    t=t.replace("# One-call system prompt for Qwen2.5", "# One-call system prompt for Qwen2.5 — canonical qwen2.5:3b (8192 ctx, 400 max) — Phase 87 certified")
    print("patched header")

p.write_text(t, encoding='utf-8')
print("done2")
